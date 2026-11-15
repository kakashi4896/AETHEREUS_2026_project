from datetime import date, datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
import re

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.geometry import build_horizon_visibility_intervals
from app.models import (
    AnalysisResult,
    ComparisonRequest,
    ComparisonRow,
    ComparisonResult,
    HorizonsLunarSiteSkySeries,
    HorizonsVisibilityAnalysis,
    Site,
)
from app.official_data import fetch_lunar_site_sky_series, fetch_official_ephemeris
from app.reference_data import load_reference_snapshot
from app.synthetic_data import SYNTHETIC_SITES, build_analysis_result, build_comparison_rows

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent
FRONTEND_ROOT = PROJECT_ROOT / "frontend"

_HORIZONS_STEP_PATTERN = re.compile(
    r"^\s*(?P<amount>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d)\s*$",
    re.IGNORECASE,
)


app = FastAPI(
    title="CLPS Lunar Mission Browser",
    version="0.1.0",
    description="Minimal API and demo UI for early-phase lunar mission visibility analysis.",
)

app.mount("/static", StaticFiles(directory=str(FRONTEND_ROOT / "static")), name="static")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "clps-lunar-mission-browser",
        "version": "0.1.0",
    }


def _step_to_minutes(step: str) -> int:
    match = _HORIZONS_STEP_PATTERN.fullmatch(step)
    if match is None:
        raise HTTPException(
            status_code=422,
            detail="step must be a positive whole-minute Horizons interval",
        )

    amount = float(match.group("amount"))
    unit = match.group("unit").lower()
    minutes_per_unit = {
        "s": 1 / 60,
        "sec": 1 / 60,
        "secs": 1 / 60,
        "second": 1 / 60,
        "seconds": 1 / 60,
        "m": 1,
        "min": 1,
        "mins": 1,
        "minute": 1,
        "minutes": 1,
        "h": 60,
        "hr": 60,
        "hrs": 60,
        "hour": 60,
        "hours": 60,
        "d": 1440,
        "day": 1440,
        "days": 1440,
    }[unit]
    duration_minutes = amount * minutes_per_unit
    if duration_minutes < 1 or not duration_minutes.is_integer():
        raise HTTPException(
            status_code=422,
            detail="step must be a positive whole-minute Horizons interval",
        )
    return int(duration_minutes)


@app.get("/api/sites")
def list_sites() -> dict[str, list[Site] | str]:
    return {
        "sites": SYNTHETIC_SITES,
        "source_type": "synthetic",
        "model_status": "synthetic_demo_data_only",
    }


@app.get("/api/analysis")
def get_analysis(
    site_id: str,
    start_date: date,
    end_date: date | None = None,
    step_minutes: int = Query(60, gt=0, le=1440),
    source: str = Query("synthetic", pattern="^(official|synthetic|reference)$"),
) -> AnalysisResult:
    if end_date is None:
        end_date = start_date

    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date must be on or after start_date")

    try:
        result = build_analysis_result(site_id, start_date, end_date, step_minutes)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown site_id: {site_id}") from exc

    official_payload = None
    if source in {"official", "reference"}:
        start_iso = start_date.isoformat()
        stop_iso = end_date.isoformat()
        official_payload = fetch_official_ephemeris(
            target="10",
            start_time=start_iso,
            stop_time=stop_iso,
            step="1h",
            observer="399",
        )

    if source == "reference":
        result.source_type = "reference"
        result.model_status = "reference_snapshot"
        result.summary["data_source"] = "reference"
        result.summary["api_status"] = "reference_snapshot"
        result.summary["source_note"] = "Reference snapshot mode selected."
        return result

    if official_payload and official_payload.get("status") == "ok":
        result.official_data = official_payload
        result.summary["data_source"] = "synthetic"
        result.summary["ephemeris_reference_source"] = official_payload.get(
            "source_name", "NASA/JPL Horizons"
        )
        result.summary["api_status"] = "ok"
        result.summary["point_count"] = official_payload.get("point_count", 0)
        result.summary["official_ephemeris_available"] = True
        result.summary["official_data_used_for_site_geometry"] = False
        result.summary["source_note"] = (
            "Horizons data was retrieved separately and is not used to calculate this analysis; site-local geometry remains synthetic until a verified lunar landing list and observer geometry are adopted."
        )
        return result

    if official_payload:
        result.summary["data_source"] = "synthetic"
        result.summary["api_status"] = official_payload.get("status", "unavailable")
        result.summary["source_note"] = "Official API unavailable; synthetic demo data retained as a fallback."

    if source == "official":
        result.source_type = "synthetic"
        result.model_status = "synthetic_fallback_after_official_unavailable"

    return result


@app.get("/api/analysis/horizons", response_model=HorizonsLunarSiteSkySeries)
def get_horizons_lunar_site_analysis(
    start_time: datetime,
    stop_time: datetime,
    site_longitude_deg: float = Query(..., ge=-360.0, le=360.0),
    site_latitude_deg: float = Query(..., ge=-90.0, le=90.0),
    site_altitude_km: float = Query(...),
    step: str = "1h",
) -> HorizonsLunarSiteSkySeries:
    if start_time.tzinfo is None or start_time.utcoffset() != timedelta(0):
        raise HTTPException(status_code=422, detail="start_time must include a zero UTC offset")
    if stop_time.tzinfo is None or stop_time.utcoffset() != timedelta(0):
        raise HTTPException(status_code=422, detail="stop_time must include a zero UTC offset")
    if stop_time <= start_time:
        raise HTTPException(status_code=400, detail="stop_time must be after start_time")
    if not isfinite(site_altitude_km):
        raise HTTPException(status_code=422, detail="site_altitude_km must be finite")
    step_minutes = _step_to_minutes(step)

    coordinates = (site_longitude_deg, site_latitude_deg, site_altitude_km * 1000.0)
    payload = fetch_lunar_site_sky_series(
        start_time=start_time.strftime("%Y-%m-%d %H:%M:%S"),
        stop_time=stop_time.strftime("%Y-%m-%d %H:%M:%S"),
        step=step,
        site_coordinates=coordinates,
    )
    if payload.get("status") != "ok":
        raise HTTPException(status_code=502, detail=payload.get("errors", payload))

    result = HorizonsLunarSiteSkySeries.model_validate(payload)
    timestamps = [sample.timestamp_ut for sample in result.samples]
    if not timestamps:
        raise HTTPException(
            status_code=502,
            detail="Horizons returned no samples for the requested interval",
        )
    window_start_ut = start_time.replace(tzinfo=None)
    window_end_ut = stop_time.replace(tzinfo=None)
    if timestamps[0] != window_start_ut or timestamps[-1] > window_end_ut:
        raise HTTPException(
            status_code=502,
            detail="Horizons samples fall outside the requested interval",
        )
    if any(
        (current - previous).total_seconds() != step_minutes * 60
        for previous, current in zip(timestamps, timestamps[1:])
    ):
        raise HTTPException(
            status_code=502,
            detail="Horizons samples do not match the requested step",
        )
    if window_end_ut - timestamps[-1] > timedelta(minutes=step_minutes):
        raise HTTPException(
            status_code=502,
            detail="Horizons samples do not cover the end of the requested interval",
        )

    sun_elevations = [sample.sun.elevation_deg for sample in result.samples]
    earth_elevations = [sample.earth.elevation_deg for sample in result.samples]
    sunlight_intervals = build_horizon_visibility_intervals(
        sun_elevations,
        timestamps,
        step_minutes,
        "sunlight",
        window_end_ut,
    )
    earth_intervals = build_horizon_visibility_intervals(
        earth_elevations,
        timestamps,
        step_minutes,
        "earth_visibility",
        window_end_ut,
    )
    valid_sun_sample_count = sum(elevation is not None for elevation in sun_elevations)
    valid_earth_sample_count = sum(elevation is not None for elevation in earth_elevations)

    def interval_minutes(intervals: list[dict[str, object]], state: str) -> list[int]:
        return [
            int(interval["duration_minutes"])
            for interval in intervals
            if interval["state"] == state
        ]

    visible_sun_minutes = interval_minutes(sunlight_intervals, "visible")
    hidden_sun_minutes = interval_minutes(sunlight_intervals, "hidden")
    visible_earth_minutes = interval_minutes(earth_intervals, "visible")
    hidden_earth_minutes = interval_minutes(earth_intervals, "hidden")
    result.visibility_analysis = HorizonsVisibilityAnalysis(
        sample_step_minutes=step_minutes,
        sample_count=len(result.samples),
        valid_sun_sample_count=valid_sun_sample_count,
        valid_earth_sample_count=valid_earth_sample_count,
        sun_data_complete=valid_sun_sample_count == len(result.samples) and bool(result.samples),
        earth_data_complete=valid_earth_sample_count == len(result.samples) and bool(result.samples),
        sunlight_intervals=sunlight_intervals,
        earth_visibility_intervals=earth_intervals,
        total_sunlight_minutes=(
            sum(visible_sun_minutes)
            if visible_sun_minutes
            else (0 if valid_sun_sample_count else None)
        ),
        longest_continuous_darkness_minutes=(
            max(hidden_sun_minutes, default=0) if valid_sun_sample_count else None
        ),
        total_earth_visible_minutes=(
            sum(visible_earth_minutes)
            if visible_earth_minutes
            else (0 if valid_earth_sample_count else None)
        ),
        longest_continuous_earth_hidden_minutes=(
            max(hidden_earth_minutes, default=0) if valid_earth_sample_count else None
        ),
    )
    return result


@app.get("/api/reference")
def get_reference(site_id: str, date: date) -> dict[str, object]:
    try:
        return load_reference_snapshot(site_id, date.isoformat())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown site_id: {site_id}") from exc


@app.get("/api/comparison")
def get_comparison(
    site_ids: str = Query(..., description="Comma-separated site IDs to compare"),
    start_date: date = Query(...),
    end_date: date | None = None,
    step_minutes: int = Query(60, gt=0, le=1440),
    source: str = Query("synthetic", pattern="^(official|synthetic)$"),
) -> ComparisonResult:
    if end_date is None:
        end_date = start_date
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date must be on or after start_date")

    parsed_site_ids = [site_id.strip() for site_id in site_ids.split(",") if site_id and site_id.strip()]
    if len(parsed_site_ids) < 2:
        raise HTTPException(status_code=400, detail="Provide at least two site_ids for comparison")

    if source == "official":
        return _build_horizons_comparison_result(
            parsed_site_ids,
            start_date,
            end_date,
            step_minutes,
        )

    rows = []
    for site_id in parsed_site_ids:
        try:
            rows.append(build_analysis_result(site_id, start_date, end_date, step_minutes))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Unknown site_id: {site_id}") from exc

    comparison_rows = []
    for result in rows:
        sunlight_summary = result.summary
        earth_summary = {
            "earth_visible_fraction": sunlight_summary.get("earth_visible_fraction", 0.0),
            "longest_no_earth_visibility_interval_minutes": sunlight_summary.get(
                "longest_no_earth_visibility_interval_minutes", 0
            ),
            "longest_earth_visibility_interval_minutes": sunlight_summary.get(
                "longest_earth_visibility_interval_minutes", 0
            ),
        }
        comparison_rows.append(
            {
                "site_id": result.site_id,
                "site_name": result.site_name,
                "latitude_deg": next(
                    site.latitude_deg
                    for site in SYNTHETIC_SITES
                    if site.site_id == result.site_id
                ),
                "longitude_deg": next(
                    site.longitude_deg
                    for site in SYNTHETIC_SITES
                    if site.site_id == result.site_id
                ),
                "sunlit_fraction": sunlight_summary["sunlit_fraction"],
                "longest_no_sunlight_interval_minutes": sunlight_summary["longest_no_sunlight_interval_minutes"],
                "longest_sunlight_interval_minutes": sunlight_summary["longest_sunlight_interval_minutes"],
                "earth_visible_fraction": earth_summary["earth_visible_fraction"],
                "longest_no_earth_visibility_interval_minutes": earth_summary["longest_no_earth_visibility_interval_minutes"],
                "longest_earth_visibility_interval_minutes": earth_summary["longest_earth_visibility_interval_minutes"],
                "sunlight_intervals": result.sunlight_intervals,
                "earth_visibility_intervals": result.earth_visibility_intervals,
                "sample_count": len(result.ephemeris),
                "sun_valid_sample_count": len(result.ephemeris),
                "earth_valid_sample_count": len(result.ephemeris),
                "source_type": result.source_type,
                "model_status": result.model_status,
            }
        )

    return ComparisonResult(
        source_type="synthetic",
        model_status="synthetic_demo_data_only",
        site_count=len(comparison_rows),
        window=result.window if rows else {
            "start_iso": start_date.isoformat(),
            "end_iso": end_date.isoformat(),
            "step_minutes": step_minutes,
            "timezone": "UTC",
        },
        rows=comparison_rows,
    )


def _build_horizons_comparison_result(
    site_ids: list[str],
    start_date: date,
    end_date: date,
    step_minutes: int,
) -> ComparisonResult:
    sites_by_id = {site.site_id: site for site in SYNTHETIC_SITES}
    unknown_site_ids = [site_id for site_id in site_ids if site_id not in sites_by_id]
    if unknown_site_ids:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown site_id: {', '.join(unknown_site_ids)}",
        )

    start_time = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
    stop_time = datetime.combine(
        end_date + timedelta(days=1),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    step = f"{step_minutes}min"
    comparison_rows = []

    for site_id in site_ids:
        site = sites_by_id[site_id]
        try:
            series = get_horizons_lunar_site_analysis(
                start_time=start_time,
                stop_time=stop_time,
                site_longitude_deg=site.longitude_deg,
                site_latitude_deg=site.latitude_deg,
                site_altitude_km=site.elevation_m / 1000.0,
                step=step,
            )
        except HTTPException as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "site_id": site_id,
                    "source_name": "NASA/JPL Horizons",
                    "error": exc.detail,
                },
            ) from exc

        analysis = series.visibility_analysis
        if analysis is None:
            raise HTTPException(
                status_code=502,
                detail={
                    "site_id": site_id,
                    "source_name": series.source_name,
                    "error": "Horizons response has no visibility analysis",
                },
            )

        sun_elevations = [sample.sun.elevation_deg for sample in series.samples]
        earth_elevations = [sample.earth.elevation_deg for sample in series.samples]
        valid_sun_elevations = [value for value in sun_elevations if value is not None]
        valid_earth_elevations = [value for value in earth_elevations if value is not None]
        visible_sun_minutes = [
            interval.duration_minutes
            for interval in analysis.sunlight_intervals
            if interval.state == "visible"
        ]
        visible_earth_minutes = [
            interval.duration_minutes
            for interval in analysis.earth_visibility_intervals
            if interval.state == "visible"
        ]

        comparison_rows.append(
            ComparisonRow(
                site_id=site.site_id,
                site_name=site.name,
                latitude_deg=site.latitude_deg,
                longitude_deg=site.longitude_deg,
                altitude_km=site.elevation_m / 1000.0,
                sunlit_fraction=(
                    sum(value > 0.0 for value in valid_sun_elevations)
                    / len(valid_sun_elevations)
                    if valid_sun_elevations
                    else None
                ),
                longest_no_sunlight_interval_minutes=analysis.longest_continuous_darkness_minutes,
                longest_sunlight_interval_minutes=(
                    max(visible_sun_minutes, default=0)
                    if valid_sun_elevations
                    else None
                ),
                earth_visible_fraction=(
                    sum(value > 0.0 for value in valid_earth_elevations)
                    / len(valid_earth_elevations)
                    if valid_earth_elevations
                    else None
                ),
                longest_no_earth_visibility_interval_minutes=analysis.longest_continuous_earth_hidden_minutes,
                longest_earth_visibility_interval_minutes=(
                    max(visible_earth_minutes, default=0)
                    if valid_earth_elevations
                    else None
                ),
                sunlight_intervals=analysis.sunlight_intervals,
                earth_visibility_intervals=analysis.earth_visibility_intervals,
                samples=series.samples,
                sample_count=analysis.sample_count,
                sun_valid_sample_count=analysis.valid_sun_sample_count,
                earth_valid_sample_count=analysis.valid_earth_sample_count,
                coordinate_frame=series.coordinate_frame,
                source_type=series.source_type,
                model_status=analysis.sampling_method,
            )
        )

    return ComparisonResult(
        source_type="official",
        model_status="horizons_apparent_angle_sampled_no_interpolation",
        source_name="NASA/JPL Horizons",
        time_system="UT",
        site_count=len(comparison_rows),
        window={
            "start_iso": start_time.isoformat(),
            "end_iso": stop_time.isoformat(),
            "step_minutes": step_minutes,
            "timezone": "UT",
        },
        rows=comparison_rows,
    )


@app.post("/api/comparison")
def post_comparison(request: ComparisonRequest) -> ComparisonResult:
    if request.end_date is None:
        request.end_date = request.start_date
    if request.end_date < request.start_date:
        raise HTTPException(status_code=400, detail="end_date must be on or after start_date")

    rows = build_comparison_rows(
        request.site_ids,
        request.start_date,
        request.end_date,
        request.step_minutes,
    )
    return ComparisonResult(
        source_type="synthetic",
        model_status="synthetic_demo_data_only",
        site_count=len(rows),
        window={
            "start_iso": request.start_date.isoformat(),
            "end_iso": request.end_date.isoformat(),
            "step_minutes": request.step_minutes,
            "timezone": "UTC",
        },
        rows=rows,
    )


@app.get("/api/official")
def get_official_ephemeris(
    target: str = "10",
    start_time: str = "2026-01-01+00:00:00",
    stop_time: str = "2026-01-02+00:00:00",
    step: str = "1h",
    observer: str | None = None,
    site_longitude_deg: float | None = Query(None, ge=-360.0, le=360.0),
    site_latitude_deg: float | None = Query(None, ge=-90.0, le=90.0),
    site_altitude_km: float | None = None,
) -> dict[str, object]:
    site_coordinates_provided = (
        site_longitude_deg is not None,
        site_latitude_deg is not None,
        site_altitude_km is not None,
    )
    if any(site_coordinates_provided) and not all(site_coordinates_provided):
        raise HTTPException(
            status_code=400,
            detail="site_longitude_deg, site_latitude_deg, and site_altitude_km must be provided together",
        )
    if any(site_coordinates_provided) and not observer:
        raise HTTPException(
            status_code=400,
            detail="observer body ID must be provided when site coordinates are used",
        )

    site_coordinates = None
    if (
        site_longitude_deg is not None
        and site_latitude_deg is not None
        and site_altitude_km is not None
    ):
        site_coordinates = (
            site_longitude_deg,
            site_latitude_deg,
            site_altitude_km * 1000.0,
        )

    return fetch_official_ephemeris(
        target=target,
        start_time=start_time,
        stop_time=stop_time,
        step=step,
        observer=observer or "399",
        site_coordinates=site_coordinates,
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_ROOT / "index.html")
