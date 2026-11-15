from __future__ import annotations

import json
import math
import re
import csv
from datetime import datetime
from io import StringIO
from urllib.parse import quote, urlencode

import httpx

HORIZONS_API_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"


def _normalize_horizons_time(value: str) -> str:
    """Convert the legacy date+time separator to a space for Horizons."""
    return value.strip().replace("+", " ", 1)


def _parse_time_to_iso(time_str: str) -> str:
    """Convert Horizons time format ('2026-Jan-01 00:00:00' or '2026-01-01 00:00') to ISO format."""
    for time_format in (
        "%Y-%b-%d %H:%M:%S.%f",
        "%Y-%b-%d %H:%M:%S",
        "%Y-%b-%d %H:%M",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(time_str.strip(), time_format).isoformat()
        except ValueError:
            continue
    raise ValueError(f"Unsupported Horizons timestamp: {time_str!r}")


def _parse_ra_to_deg(hms_str: str) -> float:
    """Convert RA 'HH MM SS.ss' to degrees (1h = 15deg)."""
    parts = hms_str.strip().split()
    if len(parts) == 3:
        hours = float(parts[0])
        minutes = float(parts[1])
        seconds = float(parts[2])
        total = hours + minutes / 60.0 + seconds / 3600.0
        return total * 15.0
    return 0.0


def _parse_dec_to_deg(hms_str: str) -> float:
    """Convert Dec 'DD MM SS.s' to degrees (with sign)."""
    parts = hms_str.strip().split()
    if len(parts) == 3:
        deg = float(parts[0])
        minutes = float(parts[1])
        seconds = float(parts[2])
        sign = -1.0 if deg < 0 else 1.0
        abs_deg = abs(deg) + minutes / 60.0 + seconds / 3600.0
        return sign * abs_deg
    return 0.0


def _safe_float(val_str: str) -> float | None:
    """Safely parse a float, returning None for 'n.a.' or empty."""
    val_str = val_str.strip()
    if not val_str or val_str == "n.a.":
        return None
    try:
        return float(val_str)
    except ValueError:
        return None


def _validate_site_coordinates(
    site_coordinates: tuple[float, float, float],
) -> tuple[float, float, float]:
    longitude_deg, latitude_deg, elevation_m = site_coordinates
    if not all(math.isfinite(value) for value in site_coordinates):
        raise ValueError("Site coordinates must be finite")
    if not -90.0 <= latitude_deg <= 90.0:
        raise ValueError("Site latitude must be between -90 and 90 degrees")
    return longitude_deg % 360.0, latitude_deg, elevation_m / 1000.0


def parse_horizons_az_el_result(raw_result: str) -> list[dict[str, object]]:
    """Parse a header-labeled Horizons observer quantity 4 table."""
    lines = raw_result.splitlines()
    soe_positions = [i for i, line in enumerate(lines) if line.strip() in ("SOE", "$$SOE")]
    eoe_positions = [i for i, line in enumerate(lines) if line.strip() in ("EOE", "$$EOE")]
    if not soe_positions:
        raise ValueError("Horizons result is missing the SOE marker")
    if not eoe_positions:
        raise ValueError("Horizons result is missing the EOE marker")
    if len(soe_positions) != 1 or len(eoe_positions) != 1:
        raise ValueError("Horizons result contains duplicate ephemeris markers")
    soe_idx, eoe_idx = soe_positions[0], eoe_positions[0]
    if eoe_idx <= soe_idx:
        raise ValueError("Horizons EOE marker must follow SOE")

    header = _find_horizons_az_el_header(lines[:soe_idx])
    if header is None:
        raise ValueError("Horizons result is missing a recognized Q4 table header")
    timestamp_index, azimuth_index, elevation_index, header_count = header

    result: list[dict[str, object]] = []
    for line_number, line in enumerate(lines[soe_idx + 1:eoe_idx], start=soe_idx + 2):
        if not line.strip():
            continue
        columns = _parse_horizons_row(line, line_number, header_count)
        try:
            timestamp = _parse_time_to_iso(columns[timestamp_index])
        except ValueError as exc:
            raise ValueError(f"Invalid timestamp in Horizons row {line_number}: {exc}") from exc
        azimuth_deg = _parse_horizons_angle(
            columns[azimuth_index], "azimuth", line_number, 0.0, 360.0
        )
        elevation_deg = _parse_horizons_angle(
            columns[elevation_index], "elevation", line_number, -90.0, 90.0
        )

        result.append({
            "timestamp_iso": timestamp,
            "azimuth_apparent_deg": azimuth_deg,
            "elevation_apparent_deg": elevation_deg,
        })

    if not result:
        raise ValueError("Horizons result has no data rows between SOE and EOE")
    return result


def _find_horizons_az_el_header(
    lines: list[str],
) -> tuple[int, int, int, int] | None:
    for line in lines:
        if not line.strip():
            continue
        if "," in line:
            try:
                columns = next(csv.reader([line], skipinitialspace=True))
            except csv.Error:
                continue
        else:
            columns = re.split(r"\s{2,}", line.strip())
        normalized = [re.sub(r"[^a-z0-9]", "", column.lower()) for column in columns]
        timestamp_index = next(
            (
                index
                for index, value in enumerate(normalized)
                if "date" in value and "ut" in value
            ),
            None,
        )
        azimuth_index = next(
            (
                index
                for index, value in enumerate(normalized)
                if "azi" in value or "azimuth" in value
            ),
            None,
        )
        elevation_index = next(
            (index for index, value in enumerate(normalized) if "elev" in value),
            None,
        )
        if (
            timestamp_index is not None
            and azimuth_index is not None
            and elevation_index == azimuth_index
            and timestamp_index != azimuth_index
        ):
            combined_angle_header = normalized[azimuth_index]
            azimuth_position = combined_angle_header.find("azi")
            elevation_position = combined_angle_header.find("elev")
            if (
                azimuth_position >= 0
                and elevation_position > azimuth_position
                and azimuth_index == timestamp_index + 1
            ):
                return timestamp_index, azimuth_index, azimuth_index + 1, 3
        if (
            timestamp_index is not None
            and azimuth_index is not None
            and elevation_index is not None
            and len({timestamp_index, azimuth_index, elevation_index}) == 3
        ):
            return timestamp_index, azimuth_index, elevation_index, len(columns)
    return None


def _parse_horizons_row(line: str, line_number: int, header_count: int) -> list[str]:
    if "," in line:
        try:
            columns = next(csv.reader([line], skipinitialspace=True))
        except csv.Error as exc:
            raise ValueError(f"Malformed Horizons row {line_number}: invalid CSV") from exc
    else:
        columns = re.split(r"\s{2,}", line.strip())
        if len(columns) < header_count or len(line.split()) > header_count + 1:
            tokens = line.split()
            if len(tokens) < 2:
                raise ValueError(f"Malformed Horizons row {line_number}: missing timestamp")
            columns = [" ".join(tokens[:2]), *tokens[2:]]

    if len(columns) > header_count and len(columns) > 1 and _is_time_token(columns[1]):
        columns = [f"{columns[0]} {columns[1]}", *columns[2:]]
    if (
        len(columns) == header_count + 1
        and len(columns) > 1
        and _is_horizons_presence_marker(columns[1])
    ):
        columns = [columns[0], *columns[2:]]
    if len(columns) != header_count:
        raise ValueError(
            f"Malformed Horizons row {line_number}: expected {header_count} columns, got {len(columns)}"
        )
    return [column.strip() for column in columns]


def _is_horizons_presence_marker(value: str) -> bool:
    marker = re.sub(r"\s+", "", value).lower()
    return marker in {"*", "i", "r", "*i", "i*", "*r", "r*"}


def _is_time_token(value: str) -> bool:
    return bool(re.fullmatch(r"\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?", value.strip()))


def _parse_horizons_angle(
    value: str, name: str, line_number: int, minimum: float, maximum: float
) -> float | None:
    token = value.strip().lower()
    if token in {"", "n.a.", "na", "null", "-", "--"}:
        return None
    try:
        angle = float(token)
    except ValueError as exc:
        raise ValueError(f"Invalid {name} in Horizons row {line_number}: {value!r}") from exc
    if not math.isfinite(angle) or not minimum <= angle <= maximum:
        raise ValueError(f"Invalid {name} in Horizons row {line_number}: {value!r}")
    return angle


def parse_horizons_result(raw_result: str) -> list[dict]:
    """Parse Horizons SOE/EOE delimited result into structured data.

    Returns list of dicts with keys:
        timestamp_iso, ra_deg, dec_deg, elongation_deg, mag,
        dist_au, earth_distance_km, lunar_distance_ratio,
        lunar_phase_angle, lunar_phase_frac
    """
    lines = raw_result.splitlines()
    soe_idx = next(
        (i for i, line in enumerate(lines) if line.strip() in ("SOE", "$$SOE")),
        None,
    )
    if soe_idx is None:
        raise ValueError("Horizons result is missing the SOE marker")

    eoe_idx = next(
        (i for i in range(soe_idx + 1, len(lines)) if lines[i].strip() in ("EOE", "$$EOE")),
        None,
    )
    if eoe_idx is None:
        raise ValueError("Horizons result is missing the EOE marker")

    data_lines = lines[soe_idx + 1:eoe_idx]
    result = []

    for line_number, line in enumerate(data_lines, start=soe_idx + 2):
        if not line.strip():
            continue
        cols = line.split()
        if len(cols) < 25:
            raise ValueError(
                f"Malformed Horizons data row {line_number}: expected at least 25 columns, got {len(cols)}"
            )

        # Horizons time column is split into two tokens: date + time
        # We must rejoin them before passing to the timestamp parser.
        timestamp_raw = f"{cols[0]} {cols[1]}"
        ts_iso = _parse_time_to_iso(timestamp_raw)

        # Column mapping (Horizons default observer format):
        # [0] date  [1] time
        # RA/Dec are 3-token values each: "HH MM SS.ss" / "+DD MM SS.s"
        # [2..4] RA (h)  [5..7] Dec  [8..10] geom RA  [11..13] geom Dec
        # [14..16] ?  [17] elongation [18] mag  [19] dist_au  [20] earth_dist_km
        # [21] lunar_dist_ratio [22] lunar_phase_angle [23] lunar_phase_frac
        ra_str = f"{cols[2]} {cols[3]} {cols[4]}"
        dec_str = f"{cols[5]} {cols[6]} {cols[7]}"
        ra_deg = _parse_ra_to_deg(ra_str)
        dec_deg = _parse_dec_to_deg(dec_str)
        elongation = _safe_float(cols[17])
        mag = _safe_float(cols[18])
        dist_au = _safe_float(cols[19])
        earth_dist_km = _safe_float(cols[20])
        lunar_ratio = _safe_float(cols[21])
        lunar_phase_angle = _safe_float(cols[23])
        lunar_phase_frac = _safe_float(cols[24])

        result.append({
            "timestamp_iso": ts_iso,
            "ra_deg": ra_deg,
            "dec_deg": dec_deg,
            "elongation_deg": elongation,
            "mag": mag,
            "dist_au": dist_au,
            "earth_distance_km": earth_dist_km,
            "lunar_distance_ratio": lunar_ratio,
            "lunar_phase_angle": lunar_phase_angle,
            "lunar_phase_frac": lunar_phase_frac,
        })

    return result


def build_horizons_url(
    target: str,
    start_time: str,
    stop_time: str,
    step: str = "1h",
    observer: str = "399",
    site_coordinates: tuple[float, float, float] | None = None,
) -> str:
    """Build the official NASA/JPL Horizons query URL using the documented parameter names."""
    parameters = {
        "format": "json",
        "COMMAND": f"'{target}'",
        "OBJ_DATA": "'NO'",
        "MAKE_EPHEM": "'YES'",
        "EPHEM_TYPE": "'OBSERVER'",
    }
    if site_coordinates is None:
        center = f"500@{observer}"
    else:
        longitude_deg, latitude_deg, altitude_km = _validate_site_coordinates(site_coordinates)
        center = f"coord@{observer}"
        parameters.update(
            {
                "COORD_TYPE": "'GEODETIC'",
                "SITE_COORD": f"'{longitude_deg:g},{latitude_deg:g},{altitude_km:g}'",
                "QUANTITIES": "'4'",
                "APPARENT": "'AIRLESS'",
            }
        )
    parameters.update(
        {
            "CENTER": f"'{center}'",
            "TIME_TYPE": "'UT'",
            "TIME_DIGITS": "'SECONDS'",
            "START_TIME": f"'{_normalize_horizons_time(start_time)}'",
            "STOP_TIME": f"'{_normalize_horizons_time(stop_time)}'",
            "STEP_SIZE": f"'{step}'",
        }
    )
    query = urlencode(parameters, safe="'@,:", quote_via=quote)
    return f"{HORIZONS_API_URL}?{query}"


def _unavailable_response(url: str, error: str) -> dict[str, object]:
    return {
        "status": "unavailable",
        "source_name": "NASA/JPL Horizons",
        "api_url": url,
        "error": error,
        "parsed_points": [],
    }


def fetch_official_ephemeris(
    target: str = "10",
    start_time: str = "2026-01-01+00:00:00",
    stop_time: str = "2026-01-02+00:00:00",
    step: str = "1h",
    observer: str = "399",
    site_coordinates: tuple[float, float, float] | None = None,
) -> dict[str, object]:
    """Fetch official ephemeris from NASA/JPL Horizons API v2.

    Args:
        target: Solar system body ID (e.g. '10' = Sun, 'Moon', '301').
        start_time: Start time in Horizons format ('YYYY-Mon-DD+HH:MM:SS').
        stop_time: Stop time in Horizons format.
        step: Time step (e.g. '1h', '30min').
        observer: Observer body ID ('399' = Earth, '301' = Moon).
        site_coordinates: Optional (east longitude degrees, latitude degrees,
            elevation meters) for a geodetic site on the observer body.

    Returns:
        Dict with 'status', 'source_name', 'api_url', 'parsed_points', 'error'.
    """
    try:
        url = build_horizons_url(target, start_time, stop_time, step, observer, site_coordinates)
    except ValueError as exc:
        return _unavailable_response("", str(exc))
    try:
        response = httpx.get(url, timeout=(10.0, 60.0))
    except httpx.TimeoutException as exc:
        return _unavailable_response(url, f"Horizons request timed out: {exc}")
    except httpx.RequestError as exc:
        return _unavailable_response(url, str(exc))

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        unavailable = _unavailable_response(
            url, f"Horizons returned HTTP {exc.response.status_code}"
        )
        unavailable["http_status"] = exc.response.status_code
        return unavailable

    if not response.content:
        return _unavailable_response(url, "Empty response from Horizons API")

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        return _unavailable_response(url, f"Invalid JSON from Horizons API: {exc}")

    if not isinstance(payload, dict):
        return _unavailable_response(url, "Horizons API response must be a JSON object")

    if payload.get("error"):
        return _unavailable_response(url, f"Horizons API error: {payload['error']}")

    raw_result = payload.get("result")
    if not isinstance(raw_result, str):
        return _unavailable_response(url, "Horizons API response is missing a text result")

    try:
        if site_coordinates is None:
            parsed = parse_horizons_result(raw_result)
        else:
            parsed = parse_horizons_az_el_result(raw_result)
    except ValueError as exc:
        return _unavailable_response(url, f"Invalid Horizons result: {exc}")

    response: dict[str, object] = {
        "status": "ok",
        "source_name": "NASA/JPL Horizons",
        "api_url": url,
        "parsed_points": parsed,
        "point_count": len(parsed),
        "target": target,
        "http_status": response.status_code,
    }
    if site_coordinates is not None:
        longitude_deg, latitude_deg, altitude_km = _validate_site_coordinates(site_coordinates)
        frame_match = re.search(
            r"^Center pole/equ\s*:\s*(.+?)\s*(?:\{|$)",
            raw_result,
            re.MULTILINE,
        )
        response.update(
            {
                "observer_site": {
                    "body_id": observer,
                    "longitude_east_deg": longitude_deg,
                    "latitude_deg": latitude_deg,
                    "altitude_km": altitude_km,
                    "coordinate_frame": frame_match.group(1).strip() if frame_match else None,
                },
                "time_system": "UT",
                "position_kind": "airless_apparent_target_center",
                "azimuth_convention": "clockwise_from_north",
                "elevation_reference": "plane_perpendicular_to_local_zenith",
                "request_parameters": {
                    "target": target,
                    "observer": f"coord@{observer}",
                    "coordinate_type": "GEODETIC",
                    "longitude_east_deg": longitude_deg,
                    "latitude_deg": latitude_deg,
                    "altitude_km": altitude_km,
                    "time_type": "UT",
                    "time_digits": "SECONDS",
                    "step": step,
                    "quantity": "4",
                    "apparent": "AIRLESS",
                },
            }
        )
    return response


def fetch_lunar_site_sky_series(
    start_time: str,
    stop_time: str,
    step: str,
    site_coordinates: tuple[float, float, float],
) -> dict[str, object]:
    """Fetch aligned apparent Sun/Earth angles for one user-specified lunar site."""
    sun = fetch_official_ephemeris(
        target="10",
        start_time=start_time,
        stop_time=stop_time,
        step=step,
        observer="301",
        site_coordinates=site_coordinates,
    )
    earth = fetch_official_ephemeris(
        target="399",
        start_time=start_time,
        stop_time=stop_time,
        step=step,
        observer="301",
        site_coordinates=site_coordinates,
    )

    errors = {
        target: str(response.get("error", "Unknown Horizons error"))
        for target, response in (("sun", sun), ("earth", earth))
        if response.get("status") != "ok"
    }
    if errors:
        return {
            "status": "unavailable",
            "source_name": "NASA/JPL Horizons",
            "errors": errors,
        }

    sun_points = sun["parsed_points"]
    earth_points = earth["parsed_points"]
    if not isinstance(sun_points, list) or not isinstance(earth_points, list):
        return {
            "status": "unavailable",
            "source_name": "NASA/JPL Horizons",
            "errors": {"alignment": "Horizons did not return valid point lists"},
        }

    def index_points(points: list[dict[str, object]], target_name: str) -> dict[datetime, dict[str, object]]:
        indexed: dict[datetime, dict[str, object]] = {}
        for point in points:
            if not isinstance(point, dict) or not isinstance(point.get("timestamp_iso"), str):
                raise ValueError(f"{target_name} contains a sample without a timestamp")
            try:
                timestamp = datetime.fromisoformat(point["timestamp_iso"])
            except ValueError as exc:
                raise ValueError(f"{target_name} contains an invalid timestamp") from exc
            if timestamp.tzinfo is not None:
                raise ValueError(f"{target_name} timestamps must remain in UT without a UTC offset")
            if timestamp in indexed:
                raise ValueError(f"{target_name} contains a duplicate timestamp")
            indexed[timestamp] = point
        if not indexed:
            raise ValueError(f"{target_name} contains no samples")
        return indexed

    try:
        sun_by_time = index_points(sun_points, "Sun")
        earth_by_time = index_points(earth_points, "Earth")
    except ValueError as exc:
        return {
            "status": "unavailable",
            "source_name": "NASA/JPL Horizons",
            "errors": {"alignment": str(exc)},
        }

    timestamps = sorted(sun_by_time.keys() | earth_by_time.keys())
    try:
        step_seconds = _horizons_step_seconds(step)
    except ValueError as exc:
        return {
            "status": "unavailable",
            "source_name": "NASA/JPL Horizons",
            "errors": {"alignment": str(exc)},
        }
    if any((current - previous).total_seconds() != step_seconds for previous, current in zip(timestamps, timestamps[1:])):
        return {
            "status": "unavailable",
            "source_name": "NASA/JPL Horizons",
            "errors": {
                "alignment": "Joined Sun/Earth samples do not match the requested cadence"
            },
        }

    observer_site = sun.get("observer_site")
    earth_observer_site = earth.get("observer_site")
    if not isinstance(observer_site, dict) or not isinstance(earth_observer_site, dict):
        return {
            "status": "unavailable",
            "source_name": "NASA/JPL Horizons",
            "errors": {"alignment": "Horizons observer metadata is missing"},
        }
    if observer_site != earth_observer_site:
        return {
            "status": "unavailable",
            "source_name": "NASA/JPL Horizons",
            "errors": {"alignment": "Sun and Earth observer metadata does not match"},
        }
    coordinate_frame = observer_site.get("coordinate_frame")
    if not isinstance(coordinate_frame, str) or not coordinate_frame.strip():
        return {
            "status": "unavailable",
            "source_name": "NASA/JPL Horizons",
            "errors": {"alignment": "Horizons did not identify the observer coordinate frame"},
        }

    longitude_deg, latitude_deg, altitude_km = _validate_site_coordinates(site_coordinates)
    samples = []
    for timestamp in timestamps:
        sun_point = sun_by_time.get(timestamp)
        earth_point = earth_by_time.get(timestamp)
        samples.append(
            {
                "timestamp_ut": timestamp.isoformat(),
                "sun": {
                    "azimuth_deg": sun_point.get("azimuth_apparent_deg") if sun_point else None,
                    "elevation_deg": sun_point.get("elevation_apparent_deg") if sun_point else None,
                },
                "earth": {
                    "azimuth_deg": earth_point.get("azimuth_apparent_deg") if earth_point else None,
                    "elevation_deg": earth_point.get("elevation_apparent_deg") if earth_point else None,
                },
            }
        )

    def target_coverage(
        indexed: dict[datetime, dict[str, object]],
    ) -> dict[str, object]:
        target_timestamps = sorted(indexed)
        return {
            "first_timestamp_ut": target_timestamps[0].isoformat(),
            "last_timestamp_ut": target_timestamps[-1].isoformat(),
            "sample_count": len(target_timestamps),
            "expected_cadence_seconds": step_seconds,
            "cadence_consistent": all(
                (current - previous).total_seconds() == step_seconds
                for previous, current in zip(target_timestamps, target_timestamps[1:])
            ),
            "missing_sample_count": len(timestamps) - len(target_timestamps),
        }

    return {
        "status": "ok",
        "source_type": "official",
        "source_name": "NASA/JPL Horizons",
        "observer_body_id": "301",
        "longitude_east_deg": longitude_deg,
        "latitude_deg": latitude_deg,
        "altitude_km": altitude_km,
        "coordinate_frame": coordinate_frame,
        "time_system": "UT",
        "position_kind": "airless_apparent_target_center",
        "azimuth_convention": "clockwise_from_north",
        "elevation_reference": "plane_perpendicular_to_local_zenith",
        "samples": samples,
        "request_summary": {
            "start_time_ut": _normalize_horizons_time(start_time),
            "stop_time_ut": _normalize_horizons_time(stop_time),
            "step": step,
            "targets": "10,399",
            "observer": "coord@301",
            "coordinate_type": "GEODETIC",
            "time_type": "UT",
            "quantity": "4",
            "apparent": "AIRLESS",
        },
        "target_coverage": {
            "sun": target_coverage(sun_by_time),
            "earth": target_coverage(earth_by_time),
        },
    }


def _horizons_step_seconds(step: str) -> float:
    match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d)\s*",
        step,
        re.IGNORECASE,
    )
    if match is None:
        raise ValueError(f"Unsupported Horizons step: {step!r}")
    amount = float(match.group(1))
    unit = match.group(2).lower()
    seconds_per_unit = {
        "s": 1,
        "sec": 1,
        "secs": 1,
        "second": 1,
        "seconds": 1,
        "m": 60,
        "min": 60,
        "mins": 60,
        "minute": 60,
        "minutes": 60,
        "h": 3600,
        "hr": 3600,
        "hrs": 3600,
        "hour": 3600,
        "hours": 3600,
        "d": 86400,
        "day": 86400,
        "days": 86400,
    }[unit]
    seconds = amount * seconds_per_unit
    if not math.isfinite(seconds) or seconds <= 0 or not seconds.is_integer():
        raise ValueError(f"Horizons step must be a positive whole number of seconds: {step!r}")
    return seconds
