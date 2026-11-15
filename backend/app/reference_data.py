from __future__ import annotations

from datetime import datetime

from app.synthetic_data import get_site_by_id


def _build_points_for_site(site_id: str, date_str: str) -> list[dict[str, object]]:
    site = get_site_by_id(site_id)
    hours = [0, 6, 12, 18]
    points: list[dict[str, object]] = []
    for hour in hours:
        timestamp = datetime.fromisoformat(f"{date_str}T{hour:02d}:00:00")
        latitude = site.latitude_deg
        longitude = site.longitude_deg

        sun_elevation = -10.0 + (15.0 * (hour / 6.0)) + (latitude / 8.0)
        earth_elevation = -12.0 + (10.0 * ((hour - 12.0) / 12.0)) + (longitude / 30.0)
        sun_azimuth = (hour * 30.0 + abs(longitude) * 0.7) % 360.0
        earth_azimuth = (sun_azimuth + 180.0) % 360.0

        points.append(
            {
                "timestamp": timestamp.isoformat(),
                "sun_azimuth_deg": round(sun_azimuth, 3),
                "sun_elevation_deg": round(sun_elevation, 3),
                "earth_azimuth_deg": round(earth_azimuth, 3),
                "earth_elevation_deg": round(earth_elevation, 3),
            }
        )

    return points


def load_reference_snapshot(site_id: str, date_str: str) -> dict[str, object]:
    site = get_site_by_id(site_id)
    points = _build_points_for_site(site_id, date_str)
    reference_id = f"{site_id}-{date_str}"

    return {
        "site_id": site.site_id,
        "site_name": site.name,
        "date": date_str,
        "source_type": "reference",
        "model_status": "reference_snapshot",
        "reference_id": reference_id,
        "points": points,
        "metadata": {
            "source": "local_reference_snapshot",
            "coordinate_system": "local-horizontal",
            "time_system": "UTC",
            "note": "reference snapshot for Phase 3 verification; not official NASA ephemeris data",
            "site_latitude_deg": site.latitude_deg,
            "site_longitude_deg": site.longitude_deg,
        },
    }
