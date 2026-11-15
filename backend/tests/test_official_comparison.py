from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.models import HorizonsApparentAngle, HorizonsLunarSkySample, VisibilityInterval


client = TestClient(app)


def horizons_comparison_site(
    longitude_deg: float,
    latitude_deg: float,
    altitude_km: float,
) -> SimpleNamespace:
    if longitude_deg == 120.0:
        sun_elevations = [10.0, -2.0, None, 4.0]
        earth_elevations = [-1.0, 3.0, 5.0, -4.0]
        sun_valid_count = 3
    else:
        sun_elevations = [8.0, 2.0, -3.0, -5.0]
        earth_elevations = [2.0, None, -1.0, -3.0]
        sun_valid_count = 4

    samples = [
        HorizonsLunarSkySample(
            timestamp_ut=datetime(2026, 1, 1) + timedelta(hours=index),
            sun=HorizonsApparentAngle(
                azimuth_deg=100.0 + index,
                elevation_deg=sun_elevations[index],
            ),
            earth=HorizonsApparentAngle(
                azimuth_deg=200.0 + index,
                elevation_deg=earth_elevations[index],
            ),
        )
        for index in range(4)
    ]
    return SimpleNamespace(
        source_type="official",
        source_name="NASA/JPL Horizons",
        coordinate_frame="MEAN_ME",
        samples=samples,
        visibility_analysis=SimpleNamespace(
            sampling_method="sampled_step_function_no_interpolation",
            sample_count=4,
            valid_sun_sample_count=sun_valid_count,
            valid_earth_sample_count=sum(value is not None for value in earth_elevations),
            longest_continuous_darkness_minutes=120,
            longest_continuous_earth_hidden_minutes=60,
            sunlight_intervals=[
                VisibilityInterval(
                    kind="sunlight",
                    state="visible",
                    start_iso="2026-01-01T00:00:00",
                    end_iso="2026-01-01T01:00:00",
                    duration_minutes=60,
                ),
                VisibilityInterval(
                    kind="sunlight",
                    state="hidden",
                    start_iso="2026-01-01T01:00:00",
                    end_iso="2026-01-01T02:00:00",
                    duration_minutes=60,
                ),
            ],
            earth_visibility_intervals=[
                VisibilityInterval(
                    kind="earth_visibility",
                    state="visible",
                    start_iso="2026-01-01T01:00:00",
                    end_iso="2026-01-01T02:00:00",
                    duration_minutes=60,
                ),
                VisibilityInterval(
                    kind="earth_visibility",
                    state="hidden",
                    start_iso="2026-01-01T02:00:00",
                    end_iso="2026-01-01T03:00:00",
                    duration_minutes=60,
                ),
            ],
        ),
    )


def test_official_comparison_uses_horizons_per_site_and_preserves_coverage(monkeypatch) -> None:
    calls = []

    def fake_horizons_analysis(**kwargs):
        calls.append(kwargs)
        assert kwargs["start_time"] == datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert kwargs["stop_time"] == datetime(2026, 1, 3, tzinfo=timezone.utc)
        assert kwargs["step"] == "60min"
        return horizons_comparison_site(
            kwargs["site_longitude_deg"],
            kwargs["site_latitude_deg"],
            kwargs["site_altitude_km"],
        )

    monkeypatch.setattr("app.main.get_horizons_lunar_site_analysis", fake_horizons_analysis)
    monkeypatch.setattr(
        "app.main.build_analysis_result",
        lambda *args, **kwargs: pytest.fail("official comparison must not use synthetic analysis"),
    )

    response = client.get(
        "/api/comparison",
        params={
            "site_ids": "demo-north-polar,demo-south-polar",
            "start_date": "2026-01-01",
            "end_date": "2026-01-02",
            "step_minutes": 60,
            "source": "official",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_type"] == "official"
    assert payload["source_name"] == "NASA/JPL Horizons"
    assert payload["time_system"] == "UT"
    assert payload["window"]["timezone"] == "UT"
    assert payload["site_count"] == 2
    assert [(call["site_longitude_deg"], call["site_latitude_deg"], call["site_altitude_km"]) for call in calls] == [
        (120.0, 85.0, 1.0),
        (-30.0, -85.0, 1.5),
    ]

    north, south = payload["rows"]
    assert north["sunlit_fraction"] == pytest.approx(2 / 3)
    assert north["earth_visible_fraction"] == pytest.approx(0.5)
    assert north["sample_count"] == 4
    assert north["sun_valid_sample_count"] == 3
    assert north["earth_valid_sample_count"] == 4
    assert north["coordinate_frame"] == "MEAN_ME"
    assert north["altitude_km"] == 1.0
    assert len(north["samples"]) == 4
    assert north["samples"][0]["timestamp_ut"] == "2026-01-01T00:00:00"
    assert north["samples"][0]["sun"] == {"azimuth_deg": 100.0, "elevation_deg": 10.0}
    assert north["samples"][2]["sun"]["elevation_deg"] is None
    assert north["source_type"] == "official"
    assert north["model_status"] == "sampled_step_function_no_interpolation"
    assert south["sunlit_fraction"] == pytest.approx(0.5)
    assert south["earth_visible_fraction"] == pytest.approx(1 / 3)


def test_official_comparison_fails_without_synthetic_fallback(monkeypatch) -> None:
    def fail_south_site(**kwargs):
        if kwargs["site_longitude_deg"] == -30.0:
            raise HTTPException(status_code=502, detail={"earth": "upstream unavailable"})
        return horizons_comparison_site(
            kwargs["site_longitude_deg"],
            kwargs["site_latitude_deg"],
            kwargs["site_altitude_km"],
        )

    monkeypatch.setattr("app.main.get_horizons_lunar_site_analysis", fail_south_site)
    monkeypatch.setattr(
        "app.main.build_analysis_result",
        lambda *args, **kwargs: pytest.fail("official comparison must not use synthetic analysis"),
    )

    response = client.get(
        "/api/comparison",
        params={
            "site_ids": "demo-north-polar,demo-south-polar",
            "start_date": "2026-01-01",
            "end_date": "2026-01-02",
            "source": "official",
        },
    )

    assert response.status_code == 502
    assert response.json()["detail"]["site_id"] == "demo-south-polar"
    assert response.json()["detail"]["error"] == {"earth": "upstream unavailable"}
