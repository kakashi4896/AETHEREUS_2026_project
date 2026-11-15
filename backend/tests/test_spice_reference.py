import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.spice_reference import (
    compute_spice_lunar_site_apparent_angles,
    loaded_reference_kernels,
)
from app.official_data import parse_horizons_az_el_result

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HORIZONS_FIXTURE_PATH = (
    PROJECT_ROOT / "data" / "reference" / "horizons_lunar_site_az_el_2026-11-10.json"
)
MATRIX_FIXTURE_PATH = (
    PROJECT_ROOT / "data" / "reference" / "spice_horizons_lunar_site_matrix_2026-11-10.json"
)
ANGLE_TOLERANCE_DEG = 1e-4


def test_spice_reference_requires_utc_timestamp() -> None:
    with pytest.raises(ValueError, match="zero UTC offset"):
        compute_spice_lunar_site_apparent_angles(
            "10",
            datetime(2026, 11, 10),
            (0.0, 0.0, 0.0),
        )


@pytest.mark.parametrize(
    "coordinates",
    [
        (0.0, 91.0, 0.0),
        (0.0, 0.0, float("inf")),
    ],
)
def test_spice_reference_rejects_invalid_coordinates(
    coordinates: tuple[float, float, float],
) -> None:
    with pytest.raises(ValueError):
        compute_spice_lunar_site_apparent_angles(
            "10",
            datetime(2026, 11, 10, tzinfo=timezone.utc),
            coordinates,
        )


def test_spice_reference_rejects_unsupported_target() -> None:
    with pytest.raises(ValueError, match="target_id"):
        compute_spice_lunar_site_apparent_angles(
            "301",
            datetime(2026, 11, 10, tzinfo=timezone.utc),
            (0.0, 0.0, 0.0),
        )


def test_spice_reference_reports_missing_kernels(tmp_path: Path) -> None:
    pytest.importorskip("spiceypy", reason="Install backend[science-validation] to run SPICE checks")

    with pytest.raises(FileNotFoundError, match="Missing NAIF reference kernels"):
        with loaded_reference_kernels(tmp_path):
            pass


def test_spice_moon_site_angles_match_horizons_reference() -> None:
    kernel_directory = os.environ.get("CLPS_SPICE_KERNEL_DIR")
    if not kernel_directory:
        pytest.skip("Set CLPS_SPICE_KERNEL_DIR to run the optional NAIF kernel comparison")
    pytest.importorskip("spiceypy", reason="Install backend[science-validation] to run SPICE checks")

    fixture = json.loads(HORIZONS_FIXTURE_PATH.read_text())
    with loaded_reference_kernels(Path(kernel_directory)):
        for case in fixture["cases"]:
            for point in case["expected_points"]:
                timestamp = datetime.fromisoformat(point["timestamp_iso"]).replace(
                    tzinfo=timezone.utc
                )
                angles = compute_spice_lunar_site_apparent_angles(
                    case["target_id"],
                    timestamp,
                    (
                        fixture["request"]["longitude_east_deg"],
                        fixture["request"]["latitude_deg"],
                        fixture["request"]["altitude_km"],
                    ),
                )

                assert angles.azimuth_deg == pytest.approx(
                    point["azimuth_apparent_deg"], abs=ANGLE_TOLERANCE_DEG
                )
                assert angles.elevation_deg == pytest.approx(
                    point["elevation_apparent_deg"], abs=ANGLE_TOLERANCE_DEG
                )


def test_spice_lunar_site_matrix_matches_horizons_reference() -> None:
    kernel_directory = os.environ.get("CLPS_SPICE_KERNEL_DIR")
    if not kernel_directory:
        pytest.skip("Set CLPS_SPICE_KERNEL_DIR to run the optional NAIF kernel comparison")
    pytest.importorskip("spiceypy", reason="Install backend[science-validation] to run SPICE checks")

    fixture = json.loads(MATRIX_FIXTURE_PATH.read_text())
    sample_count = 0
    max_azimuth_difference_deg = 0.0
    max_elevation_difference_deg = 0.0
    with loaded_reference_kernels(Path(kernel_directory)):
        for case in fixture["cases"]:
            site = case["site"]
            site_coordinates = (
                site["longitude_east_deg"],
                site["latitude_deg"],
                site["altitude_km"],
            )
            for target in case["targets"]:
                raw_result = target["raw_result_excerpt"].replace(
                    "$$SOE",
                    "Date__(UT)__HR:MN    Azi_(a-app)_    Elev_(a-app)_\n$$SOE",
                    1,
                )
                parsed_points = parse_horizons_az_el_result(raw_result)
                assert parsed_points == target["expected_points"]
                for point in parsed_points:
                    timestamp = datetime.fromisoformat(point["timestamp_iso"]).replace(
                        tzinfo=timezone.utc
                    )
                    angles = compute_spice_lunar_site_apparent_angles(
                        target["target_id"],
                        timestamp,
                        site_coordinates,
                    )
                    sample_count += 1
                    max_azimuth_difference_deg = max(
                        max_azimuth_difference_deg,
                        abs(angles.azimuth_deg - point["azimuth_apparent_deg"]),
                    )
                    max_elevation_difference_deg = max(
                        max_elevation_difference_deg,
                        abs(angles.elevation_deg - point["elevation_apparent_deg"]),
                    )

                    assert angles.azimuth_deg == pytest.approx(
                        point["azimuth_apparent_deg"], abs=ANGLE_TOLERANCE_DEG
                    ), f"{case['case_id']} {target['target_name']} at {point['timestamp_iso']}"
                    assert angles.elevation_deg == pytest.approx(
                        point["elevation_apparent_deg"], abs=ANGLE_TOLERANCE_DEG
                    ), f"{case['case_id']} {target['target_name']} at {point['timestamp_iso']}"

    comparison = fixture["comparison"]
    assert sample_count == comparison["sample_count"]
    assert max_azimuth_difference_deg == pytest.approx(
        comparison["max_azimuth_difference_deg"], abs=1e-14
    )
    assert max_elevation_difference_deg == pytest.approx(
        comparison["max_elevation_difference_deg"], abs=1e-14
    )
    assert comparison["tolerance_deg"] == ANGLE_TOLERANCE_DEG
