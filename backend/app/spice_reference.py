from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
from typing import Iterator, Literal

KERNEL_FILENAMES = (
    "naif0012.tls",
    "pck00011.tpc",
    "moon_pa_de440_200625.bpc",
    "moon_de440_250416.tf",
    "de440s.bsp",
)


@dataclass(frozen=True)
class ApparentAngles:
    azimuth_deg: float
    elevation_deg: float


@contextmanager
def loaded_reference_kernels(kernel_directory: Path) -> Iterator[None]:
    """Load the pinned external kernel set for one isolated reference run."""
    import spiceypy

    missing = [name for name in KERNEL_FILENAMES if not (kernel_directory / name).is_file()]
    if missing:
        raise FileNotFoundError(
            f"Missing NAIF reference kernels in {kernel_directory}: {', '.join(missing)}"
        )

    spiceypy.kclear()
    try:
        for filename in KERNEL_FILENAMES:
            spiceypy.furnsh(str(kernel_directory / filename))
        yield
    finally:
        spiceypy.kclear()


def compute_spice_lunar_site_apparent_angles(
    target_id: Literal["10", "399"],
    timestamp_utc: datetime,
    site_coordinates: tuple[float, float, float],
) -> ApparentAngles:
    """Compute apparent target-center angles in the site's local Moon ME tangent frame."""
    if target_id not in {"10", "399"}:
        raise ValueError("target_id must be '10' (Sun) or '399' (Earth)")
    if timestamp_utc.tzinfo is None or timestamp_utc.utcoffset() != timedelta(0):
        raise ValueError("timestamp_utc must include a zero UTC offset")
    if not all(math.isfinite(value) for value in site_coordinates):
        raise ValueError("Site coordinates must be finite")

    longitude_deg, latitude_deg, altitude_km = site_coordinates
    if not -90.0 <= latitude_deg <= 90.0:
        raise ValueError("Site latitude must be between -90 and 90 degrees")

    import spiceypy

    longitude_deg %= 360.0
    longitude_rad = math.radians(longitude_deg)
    latitude_rad = math.radians(latitude_deg)
    utc_text = timestamp_utc.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f UTC")
    et = spiceypy.str2et(utc_text)

    _, radii = spiceypy.bodvrd("MOON", "RADII", 3)
    flattening = (radii[0] - radii[2]) / radii[0]
    observer_position = spiceypy.georec(
        longitude_rad,
        latitude_rad,
        altitude_km,
        radii[0],
        flattening,
    )
    state, _ = spiceypy.spkcpo(
        target_id,
        et,
        "MOON_ME",
        "OBSERVER",
        "LT+S",
        observer_position,
        "MOON",
        "MOON_ME",
    )

    east = (-math.sin(longitude_rad), math.cos(longitude_rad), 0.0)
    north = (
        -math.sin(latitude_rad) * math.cos(longitude_rad),
        -math.sin(latitude_rad) * math.sin(longitude_rad),
        math.cos(latitude_rad),
    )
    up = (
        math.cos(latitude_rad) * math.cos(longitude_rad),
        math.cos(latitude_rad) * math.sin(longitude_rad),
        math.sin(latitude_rad),
    )
    east_component = sum(state[index] * east[index] for index in range(3))
    north_component = sum(state[index] * north[index] for index in range(3))
    up_component = sum(state[index] * up[index] for index in range(3))

    azimuth_deg = math.degrees(math.atan2(east_component, north_component)) % 360.0
    elevation_deg = math.degrees(
        math.atan2(up_component, math.hypot(east_component, north_component))
    )
    return ApparentAngles(azimuth_deg, elevation_deg)
