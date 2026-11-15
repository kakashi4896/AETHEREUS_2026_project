from __future__ import annotations

import math
from datetime import datetime, timedelta


def _coerce_datetime(timestamp: str | datetime) -> datetime:
    if isinstance(timestamp, datetime):
        return timestamp
    return datetime.fromisoformat(timestamp)


def _wrap_azimuth(value_deg: float) -> float:
    return value_deg % 360.0


def compute_local_horizon_state(
    latitude_deg: float,
    longitude_deg: float,
    timestamp: str | datetime,
) -> dict[str, float | bool]:
    """Return local solar and Earth azimuth/elevation using a simple deterministic model.

    The convention follows the project plan:
    - azimuth is measured clockwise from north in the local horizontal frame
    - elevation is measured above the local geometric horizon
    - the horizon is treated as a flat plane for Phase 4; terrain masking is not included yet
    """
    dt = _coerce_datetime(timestamp)
    hour_fraction = (dt.hour + dt.minute / 60.0 + dt.second / 3600.0) / 24.0
    phase = (hour_fraction * 2.0 * math.pi) + (latitude_deg / 180.0) * math.pi

    sun_elevation_deg = 15.0 + (22.0 * math.sin(phase)) + (latitude_deg / 12.0)
    earth_elevation_deg = -5.0 + (12.0 * math.cos(phase + math.radians(longitude_deg)))
    sun_azimuth_deg = _wrap_azimuth(90.0 + (dt.hour * 15.0) + (longitude_deg * 0.6))
    earth_azimuth_deg = _wrap_azimuth(sun_azimuth_deg + 180.0)

    sun_above_horizon = sun_elevation_deg > 0.0
    earth_above_horizon = earth_elevation_deg > 0.0

    return {
        "sun_azimuth_deg": float(sun_azimuth_deg),
        "sun_elevation_deg": float(sun_elevation_deg),
        "earth_azimuth_deg": float(earth_azimuth_deg),
        "earth_elevation_deg": float(earth_elevation_deg),
        "sun_above_horizon": bool(sun_above_horizon),
        "earth_above_horizon": bool(earth_above_horizon),
    }


def summarize_visibility(series: list[bool], step_minutes: int) -> dict[str, float | int]:
    """Summarize a binary visibility time series and the longest continuous intervals."""
    if not series:
        return {
            "total_points": 0,
            "sunlit_fraction": 0.0,
            "longest_no_sunlight_interval_minutes": 0,
            "longest_sunlight_interval_minutes": 0,
        }

    total_points = len(series)
    sunlit_count = sum(1 for item in series if item)
    max_false = 0
    max_true = 0
    current_false = 0
    current_true = 0

    for item in series:
        if item:
            current_false = 0
            current_true += 1
            max_true = max(max_true, current_true)
        else:
            current_true = 0
            current_false += 1
            max_false = max(max_false, current_false)

    return {
        "total_points": total_points,
        "sunlit_fraction": round(sunlit_count / total_points, 3),
        "longest_no_sunlight_interval_minutes": max_false * step_minutes,
        "longest_sunlight_interval_minutes": max_true * step_minutes,
    }


def build_visibility_intervals(
    series: list[bool | None],
    timestamps: list[datetime],
    step_minutes: int,
    kind: str,
    window_end: datetime | None = None,
) -> list[dict[str, object]]:
    """Return consecutive visible/hidden runs; unknown samples break a run."""
    if not series:
        return []
    if len(series) != len(timestamps):
        raise ValueError("series and timestamps must have the same length")
    if step_minutes <= 0:
        raise ValueError("step_minutes must be positive")

    intervals: list[dict[str, object]] = []
    start_index: int | None = None
    current_state: bool | None = None

    for index in range(len(series) + 1):
        state = series[index] if index < len(series) else None
        if current_state is not None and state != current_state:
            assert start_index is not None
            start_dt = timestamps[start_index]
            end_dt = timestamps[index - 1] + timedelta(minutes=step_minutes)
            if window_end is not None:
                end_dt = min(end_dt, window_end)
            duration_minutes = int((end_dt - start_dt).total_seconds() // 60)
            if duration_minutes > 0:
                intervals.append(
                    {
                        "kind": kind,
                        "state": "visible" if current_state else "hidden",
                        "start_iso": start_dt.isoformat(),
                        "end_iso": end_dt.isoformat(),
                        "duration_minutes": duration_minutes,
                    }
                )
            start_index = None
            current_state = None

        if state is not None and current_state is None:
            start_index = index
            current_state = state

    return intervals


def build_horizon_visibility_intervals(
    elevations_deg: list[float | None],
    timestamps: list[datetime],
    step_minutes: int,
    kind: str,
    window_end: datetime,
) -> list[dict[str, object]]:
    """Classify sampled apparent elevations against the ideal 0-degree horizon."""
    visible_samples = [
        None if elevation is None else elevation > 0.0
        for elevation in elevations_deg
    ]
    return build_visibility_intervals(
        visible_samples,
        timestamps,
        step_minutes,
        kind,
        window_end=window_end,
    )


def _compute_lunar_sidereal_time(timestamp: datetime, longitude_deg: float) -> float:
    """Compute Local Sidereal Time (LST) at the lunar surface site.

    The Moon's rotation period is ~27.32 days (synchronous with orbital period).
    The Moon's prime meridian passes through the mean sub-Earth point.

    Args:
        timestamp: Datetime in UTC
        longitude_deg: Lunar longitude in degrees (positive = east from mean sub-Earth)

    Returns:
        LST in degrees (0-360)
    """
    # Julian Date
    dt = timestamp
    a = (14 - dt.month) // 12
    y = dt.year + 4800 - a
    m = dt.month + 12 * a - 3
    jdn = dt.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045

    # Julian Century from J2000.0
    T = (jdn - 2451545.0) / 36525.0

    # Mean lunar longitude of the ascending node (W)
    # W = 125.04452 - 0.0529921 * T (in degrees)
    W = 125.04452 - 0.0529921 * T

    # Mean elongation of the Moon (D)
    # D = 297.850192 + 26.0649998 * T (in degrees)
    D = 297.850192 + 26.0649998 * T

    # Mean anomaly of the Moon (M)
    # M = 357.52911 + 35999.05024 * T (in degrees)
    M = 357.52911 + 35999.05024 * T

    # Mean anomaly of the Sun (M')
    # M' = 134.96339 + 29.0706180 * T (in degrees)
    M_prime = 134.96339 + 29.0706180 * T

    # Julian Date fraction
    jd = jdn + dt.hour / 24.0 + dt.minute / 1440.0 + dt.second / 86400.0

    # Julian Date of J2000.0
    j2000 = 2451545.0

    # Julian Century
    t_j2000 = (jd - j2000) / 36525.0

    # Greenwich Mean Sidereal Time (GMST) in seconds
    # GMST = 67310.54841 + 876600.0 * t_j2000 + 8640184.812866 * t_j2000^2
    #         + 0.093104 * t_j2000^3 - 6.2e-6 * t_j2000^4
    gmst_sec = (67310.54841 + 876600.0 * t_j2000 + 8640184.812866 * t_j2000 ** 2
                + 0.093104 * t_j2000 ** 3 - 6.2e-6 * t_j2000 ** 4)

    # Convert to degrees and normalize to [0, 360)
    gmst_deg = (gmst_sec / 240.0) % 360.0

    # On the Moon, the "GMST" is approximately the same as Earth's GMST
    # because the Moon is tidally locked and rotates with the same period
    # The local sidereal time at a lunar site is:
    # LST_moon = GMST_moon - longitude_lunar
    # where GMST_moon ≈ GMST_earth (since the Moon's rotation is synchronous)
    # But we need to account for the libration

    # For Phase 4, we'll use:
    # LST_moon ≈ GMST_earth - longitude_lunar
    # (This is approximate; a full treatment would include libration)

    # Simplified: LST_moon = GMST_earth - longitude_lunar
    lst_moon = (gmst_deg - longitude_deg) % 360.0

    return lst_moon


def celestial_to_horizontal(
    ra_deg: float,
    dec_deg: float,
    lst_deg: float,
    latitude_deg: float,
) -> dict[str, float]:
    """Convert celestial coordinates (RA, Dec) to horizontal coordinates (Az, Alt).

    Args:
        ra_deg: Right Ascension in degrees (0-360)
        dec_deg: Declination in degrees (-90 to 90)
        lst_deg: Local Sidereal Time in degrees (0-360)
        latitude_deg: Observer's latitude in degrees (-90 to 90)

    Returns:
        Dict with 'azimuth_deg' (0-360) and 'altitude_deg' (-90 to 90)
    """
    # Hour Angle (HA) in degrees
    ha_deg = (lst_deg - ra_deg) % 360.0
    if ha_deg > 180.0:
        ha_deg -= 360.0

    # Convert to radians
    ha = math.radians(ha_deg)
    dec = math.radians(dec_deg)
    lat = math.radians(latitude_deg)

    # Altitude (elevation) calculation
    sin_alt = (math.sin(lat) * math.sin(dec) +
               math.cos(lat) * math.cos(dec) * math.cos(ha))
    # Clamp to [-1, 1] to avoid numerical errors
    sin_alt = max(-1.0, min(1.0, sin_alt))
    alt_deg = math.degrees(math.asin(sin_alt))

    # Azimuth calculation
    # tan(Az) = -sin(HA) / (cos(HA)*sin(lat) - tan(dec)*cos(lat))
    cos_alt = math.cos(math.asin(sin_alt))  # cos(alt)
    if abs(cos_alt) < 1e-10:
        # At zenith or nadir, azimuth is undefined
        az_deg = 0.0
    else:
        tan_az = -math.sin(ha) / (math.cos(ha) * math.sin(lat) -
                                   math.tan(dec) * math.cos(lat))
        az_deg = math.degrees(math.atan2(-math.sin(ha),
                                          math.cos(ha) * math.sin(lat) -
                                          math.tan(dec) * math.cos(lat)))
        # Normalize to [0, 360)
        az_deg = az_deg % 360.0

    return {
        "azimuth_deg": float(_wrap_azimuth(az_deg)),
        "altitude_deg": float(alt_deg),
        "hour_angle_deg": float(_wrap_azimuth(ha_deg)),
    }


def compute_lunar_site_visibility(
    ra_sun_deg: float,
    dec_sun_deg: float,
    ra_earth_deg: float,
    dec_earth_deg: float,
    lst_deg: float,
    latitude_deg: float,
    longitude_deg: float,
) -> dict[str, float | bool]:
    """Compute local horizon state at a lunar site using RA/Dec of Sun and Earth.

    Args:
        ra_sun_deg: Sun's right ascension in degrees
        dec_sun_deg: Sun's declination in degrees
        ra_earth_deg: Earth's right ascension in degrees
        dec_earth_deg: Earth's declination in degrees
        lst_deg: Local Sidereal Time in degrees
        latitude_deg: Site latitude in degrees
        longitude_deg: Site longitude in degrees

    Returns:
        Dict with sun and earth azimuth/elevation and visibility flags
    """
    sun_horiz = celestial_to_horizontal(ra_sun_deg, dec_sun_deg, lst_deg, latitude_deg)
    earth_horiz = celestial_to_horizontal(ra_earth_deg, dec_earth_deg, lst_deg, latitude_deg)

    sun_above = sun_horiz["altitude_deg"] > 0.0
    earth_above = earth_horiz["altitude_deg"] > 0.0

    return {
        "sun_azimuth_deg": sun_horiz["azimuth_deg"],
        "sun_elevation_deg": sun_horiz["altitude_deg"],
        "earth_azimuth_deg": earth_horiz["azimuth_deg"],
        "earth_elevation_deg": earth_horiz["altitude_deg"],
        "sun_above_horizon": sun_above,
        "earth_above_horizon": earth_above,
    }
