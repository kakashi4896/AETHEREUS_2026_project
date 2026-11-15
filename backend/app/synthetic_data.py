from __future__ import annotations

from datetime import date, datetime, timedelta

from app.geometry import build_visibility_intervals, compute_local_horizon_state, summarize_visibility
from app.models import AnalysisResult, ComparisonRow, EphemerisPoint, Site, TimeWindow, VisibilityInterval, VisibilityState


SYNTHETIC_SITES: list[Site] = [
    Site(
        site_id="demo-north-polar",
        name="Demo North Polar Site",
        latitude_deg=85.0,
        longitude_deg=120.0,
        elevation_m=1000.0,
        site_type="synthetic_demo_site",
    ),
    Site(
        site_id="demo-south-polar",
        name="Demo South Polar Site",
        latitude_deg=-85.0,
        longitude_deg=-30.0,
        elevation_m=1500.0,
        site_type="synthetic_demo_site",
    ),
    Site(
        site_id="demo-equatorial",
        name="Demo Equatorial Site",
        latitude_deg=0.0,
        longitude_deg=45.0,
        elevation_m=500.0,
        site_type="synthetic_demo_site",
    ),
]


def get_site_by_id(site_id: str) -> Site:
    for site in SYNTHETIC_SITES:
        if site.site_id == site_id:
            return site
    raise KeyError(site_id)


def build_analysis_result(site_id: str, start_date: date, end_date: date, step_minutes: int) -> AnalysisResult:
    site = get_site_by_id(site_id)
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.min.time())
    current = start_dt
    ephemeris: list[EphemerisPoint] = []
    visibility: list[VisibilityState] = []
    earth_visible_count = 0

    horizon_flags: list[bool] = []
    earth_flags: list[bool] = []
    while current <= end_dt:
        state = compute_local_horizon_state(site.latitude_deg, site.longitude_deg, current)

        sun_elevation = float(state["sun_elevation_deg"])
        earth_elevation = float(state["earth_elevation_deg"])
        sun_azimuth = float(state["sun_azimuth_deg"])
        earth_azimuth = float(state["earth_azimuth_deg"])

        sun_above_horizon = bool(state["sun_above_horizon"])
        earth_above_horizon = bool(state["earth_above_horizon"])
        sunlight_available = sun_above_horizon
        direct_to_earth = earth_above_horizon

        if direct_to_earth:
            earth_visible_count += 1
        horizon_flags.append(sunlight_available)
        earth_flags.append(direct_to_earth)

        ephemeris.append(
            EphemerisPoint(
                timestamp=current,
                sun_azimuth_deg=sun_azimuth,
                sun_elevation_deg=sun_elevation,
                earth_azimuth_deg=earth_azimuth,
                earth_elevation_deg=earth_elevation,
            )
        )
        visibility.append(
            VisibilityState(
                timestamp=current,
                sun_above_horizon=sun_above_horizon,
                earth_above_horizon=earth_above_horizon,
                sunlight_available=sunlight_available,
                direct_to_earth_geometric_visibility=direct_to_earth,
            )
        )

        current += timedelta(minutes=step_minutes)

    total_points = len(ephemeris)
    sunlight_summary = summarize_visibility(horizon_flags, step_minutes)
    earth_summary = summarize_visibility(earth_flags, step_minutes)
    sunlight_intervals = [
        VisibilityInterval.model_validate(item)
        for item in build_visibility_intervals(horizon_flags, [point.timestamp for point in ephemeris], step_minutes, "sunlight")
    ]
    earth_visibility_intervals = [
        VisibilityInterval.model_validate(item)
        for item in build_visibility_intervals(
            earth_flags,
            [point.timestamp for point in ephemeris],
            step_minutes,
            "earth_visibility",
        )
    ]
    summary = sunlight_summary.copy()
    summary.update(
        {
            "total_points": total_points,
            "time_step_minutes": step_minutes,
            "earth_visible_fraction": round((earth_visible_count / total_points) if total_points else 0.0, 3),
            "longest_no_earth_visibility_interval_minutes": earth_summary["longest_no_sunlight_interval_minutes"],
            "longest_earth_visibility_interval_minutes": earth_summary["longest_sunlight_interval_minutes"],
            "source_type": "synthetic",
            "model_status": "synthetic_demo_data_only",
        }
    )

    return AnalysisResult(
        site_id=site.site_id,
        site_name=site.name,
        source_type="synthetic",
        model_status="synthetic_demo_data_only",
        window=TimeWindow(
            start_iso=start_date.isoformat(),
            end_iso=end_date.isoformat(),
            step_minutes=step_minutes,
        ),
        ephemeris=ephemeris,
        visibility_series=visibility,
        sunlight_intervals=sunlight_intervals,
        earth_visibility_intervals=earth_visibility_intervals,
        summary=summary,
    )


def build_comparison_rows(site_ids: list[str], start_date: date, end_date: date, step_minutes: int) -> list[ComparisonRow]:
    rows: list[ComparisonRow] = []
    for site_id in site_ids:
        site = get_site_by_id(site_id)
        result = build_analysis_result(site_id, start_date, end_date, step_minutes)
        sunlight_summary = summarize_visibility(
            [item.sunlight_available for item in result.visibility_series],
            step_minutes,
        )
        earth_summary = summarize_visibility(
            [item.direct_to_earth_geometric_visibility for item in result.visibility_series],
            step_minutes,
        )
        rows.append(
            ComparisonRow(
                site_id=result.site_id,
                site_name=result.site_name,
                latitude_deg=site.latitude_deg,
                longitude_deg=site.longitude_deg,
                sunlit_fraction=sunlight_summary["sunlit_fraction"],
                longest_no_sunlight_interval_minutes=sunlight_summary["longest_no_sunlight_interval_minutes"],
                longest_sunlight_interval_minutes=sunlight_summary["longest_sunlight_interval_minutes"],
                earth_visible_fraction=result.summary["earth_visible_fraction"],
                longest_no_earth_visibility_interval_minutes=earth_summary["longest_no_sunlight_interval_minutes"],
                longest_earth_visibility_interval_minutes=earth_summary["longest_sunlight_interval_minutes"],
                sunlight_intervals=result.sunlight_intervals,
                earth_visibility_intervals=result.earth_visibility_intervals,
                source_type=result.source_type,
                model_status=result.model_status,
            )
        )
    return rows
