import json
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
import httpx
import pytest

from app import official_data, synthetic_data
from app.geometry import (
    build_horizon_visibility_intervals,
    celestial_to_horizontal,
    compute_local_horizon_state,
    compute_lunar_site_visibility,
    summarize_visibility,
)
from app.main import app
from app.models import NormalizedEphemerisPoint, Site
from app.official_data import (
    build_horizons_url,
    fetch_official_ephemeris,
    fetch_lunar_site_sky_series,
    parse_horizons_az_el_result,
    parse_horizons_result,
)
from app.reference_data import load_reference_snapshot

client = TestClient(app)

HORIZONS_RESULT_FIXTURE = """$$SOE
2026-Jan-01 00:00  00 00 00.00  +00 00 00.00  00 00 00.00  00 00 00.00  00 00 00.00  180.00  -12.0  1.00000  0.0000  1.00  100000.0  1.0  0.5
2026-Jan-01 01:00  00 15 00.00  +00 00 00.00  00 00 00.00  00 00 00.00  00 00 00.00  179.50  -11.5  1.00100  0.0000  1.01  100100.0  1.0  0.5
$$EOE"""
HORIZONS_LUNAR_SITE_FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "data"
        / "reference"
        / "horizons_lunar_site_az_el_2026-11-10.json"
    ).read_text()
)


def _headered_q4_result(raw_result: str) -> str:
    header = "Date__(UT)__HR:MN    Azi_(a-app)_    Elev_(a-app)_"
    return raw_result.replace("$$SOE", f"{header}\n$$SOE", 1)


def test_api_health_endpoint() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "clps-lunar-mission-browser"


def test_frontend_index_is_served() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "CLPS Lunar Mission Browser" in response.text


def test_sites_endpoint_returns_synthetic_contract() -> None:
    response = client.get("/api/sites")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source_type"] == "synthetic"
    assert payload["model_status"] == "synthetic_demo_data_only"
    assert len(payload["sites"]) >= 3
    assert {"site_id", "name", "latitude_deg", "longitude_deg", "elevation_m"}.issubset(payload["sites"][0].keys())
    assert all(site["site_type"] == "synthetic_demo_site" for site in payload["sites"])
    assert all(site["coordinate_frame"] is None for site in payload["sites"])
    assert all(site["data_status"] == "unverified" for site in payload["sites"])


@pytest.mark.parametrize(
    "site_type",
    [
        "actual_landing_site",
        "official_candidate_site",
        "user_defined_site",
        "synthetic_demo_site",
    ],
)
def test_site_schema_accepts_defined_site_types(site_type: str) -> None:
    site = Site(
        site_id="site-example",
        name="Example Site",
        latitude_deg=0.0,
        longitude_deg=0.0,
        elevation_m=0.0,
        site_type=site_type,
    )

    assert site.site_type == site_type
    assert site.coordinate_frame is None
    assert site.data_status == "unverified"


def test_site_schema_rejects_undefined_site_type() -> None:
    with pytest.raises(ValueError):
        Site(
            site_id="site-example",
            name="Example Site",
            latitude_deg=0.0,
            longitude_deg=0.0,
            elevation_m=0.0,
            site_type="future_candidate",
        )


def test_analysis_endpoint_returns_demo_time_series() -> None:
    response = client.get(
        "/api/analysis?site_id=demo-north-polar&start_date=2026-01-01&end_date=2026-01-02&step_minutes=60"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["source_type"] == "synthetic"
    assert payload["model_status"] == "synthetic_demo_data_only"
    assert payload["site_id"] == "demo-north-polar"
    assert len(payload["ephemeris"]) >= 2
    assert len(payload["visibility_series"]) == len(payload["ephemeris"])
    assert "sunlit_fraction" in payload["summary"]


def test_comparison_endpoint_supports_multiple_sites() -> None:
    response = client.get(
        "/api/comparison?site_ids=demo-north-polar,demo-south-polar&start_date=2026-01-01&end_date=2026-01-02&step_minutes=60"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["site_count"] == 2
    assert payload["source_type"] == "synthetic"
    assert payload["window"]["step_minutes"] == 60
    assert {row["site_id"] for row in payload["rows"]} == {"demo-north-polar", "demo-south-polar"}
    assert all("sunlit_fraction" in row for row in payload["rows"])
    assert all(row["sample_count"] > 0 for row in payload["rows"])
    assert all(row["sun_valid_sample_count"] == row["sample_count"] for row in payload["rows"])
    assert all(row["earth_valid_sample_count"] == row["sample_count"] for row in payload["rows"])
    assert all(row["sunlight_intervals"] for row in payload["rows"])
    assert all("earth_visible_fraction" in row for row in payload["rows"])


def test_analysis_result_exposes_visibility_intervals() -> None:
    response = client.get(
        "/api/analysis?site_id=demo-equatorial&start_date=2026-01-01&end_date=2026-01-01&step_minutes=60"
    )
    assert response.status_code == 200
    payload = response.json()
    assert "sunlight_intervals" in payload
    assert "earth_visibility_intervals" in payload
    assert payload["sunlight_intervals"]
    assert all("start_iso" in interval and "end_iso" in interval for interval in payload["sunlight_intervals"])
    assert all("duration_minutes" in interval for interval in payload["sunlight_intervals"])


def test_synthetic_earth_visibility_is_independent_of_sunlight(monkeypatch) -> None:
    states = iter(
        [
            {
                "sun_azimuth_deg": 0.0,
                "sun_elevation_deg": -1.0,
                "earth_azimuth_deg": 180.0,
                "earth_elevation_deg": 1.0,
                "sun_above_horizon": False,
                "earth_above_horizon": True,
            },
            {
                "sun_azimuth_deg": 0.0,
                "sun_elevation_deg": 1.0,
                "earth_azimuth_deg": 180.0,
                "earth_elevation_deg": -1.0,
                "sun_above_horizon": True,
                "earth_above_horizon": False,
            },
        ]
    )
    monkeypatch.setattr(synthetic_data, "compute_local_horizon_state", lambda *args: next(states))

    result = synthetic_data.build_analysis_result(
        "demo-equatorial",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
        step_minutes=1440,
    )

    assert [item.sunlight_available for item in result.visibility_series] == [False, True]
    assert [item.direct_to_earth_geometric_visibility for item in result.visibility_series] == [
        True,
        False,
    ]
    assert result.summary["earth_visible_fraction"] == 0.5


def test_invalid_step_minutes_is_rejected() -> None:
    response = client.get(
        "/api/analysis?site_id=demo-equatorial&start_date=2026-01-01&step_minutes=0"
    )
    assert response.status_code == 422


def test_reference_snapshot_loader_returns_replayable_data() -> None:
    payload = load_reference_snapshot("demo-north-polar", "2026-01-01")
    assert payload["source_type"] == "reference"
    assert payload["model_status"] == "reference_snapshot"
    assert payload["reference_id"] == "demo-north-polar-2026-01-01"
    assert len(payload["points"]) >= 3
    assert {"timestamp", "sun_azimuth_deg", "sun_elevation_deg", "earth_azimuth_deg", "earth_elevation_deg"}.issubset(
        payload["points"][0].keys()
    )


def test_reference_endpoint_returns_snapshot_metadata() -> None:
    response = client.get("/api/reference?site_id=demo-north-polar&date=2026-01-01")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source_type"] == "reference"
    assert payload["model_status"] == "reference_snapshot"
    assert payload["site_id"] == "demo-north-polar"
    assert len(payload["points"]) >= 3


def test_geometry_state_defines_azimuth_and_elevation() -> None:
    state = compute_local_horizon_state(latitude_deg=45.0, longitude_deg=30.0, timestamp="2026-01-01T12:00:00")
    assert 0.0 <= state["sun_azimuth_deg"] <= 360.0
    assert 0.0 <= state["earth_azimuth_deg"] <= 360.0
    assert isinstance(state["sun_elevation_deg"], float)
    assert isinstance(state["earth_elevation_deg"], float)
    assert state["sun_above_horizon"] == (state["sun_elevation_deg"] > 0.0)
    assert state["earth_above_horizon"] == (state["earth_elevation_deg"] > 0.0)


def test_visibility_summary_tracks_fractions_and_longest_gap() -> None:
    samples = [
        True,
        True,
        False,
        False,
        False,
        True,
        True,
        True,
    ]
    summary = summarize_visibility(samples, step_minutes=60)
    assert summary["sunlit_fraction"] == 5 / 8
    assert summary["longest_no_sunlight_interval_minutes"] == 180
    assert summary["longest_sunlight_interval_minutes"] == 180


def test_horizon_visibility_intervals_use_sampled_elevations_and_ignore_missing_values() -> None:
    from datetime import datetime

    timestamps = [
        datetime.fromisoformat(f"2026-11-10T0{hour}:00:00")
        for hour in range(4)
    ]

    intervals = build_horizon_visibility_intervals(
        [2.0, 0.0, -3.0, None],
        timestamps,
        step_minutes=60,
        kind="sunlight",
        window_end=datetime.fromisoformat("2026-11-10T03:30:00"),
    )

    assert intervals == [
        {
            "kind": "sunlight",
            "state": "visible",
            "start_iso": "2026-11-10T00:00:00",
            "end_iso": "2026-11-10T01:00:00",
            "duration_minutes": 60,
        },
        {
            "kind": "sunlight",
            "state": "hidden",
            "start_iso": "2026-11-10T01:00:00",
            "end_iso": "2026-11-10T03:00:00",
            "duration_minutes": 120,
        },
    ]


def test_horizon_visibility_intervals_are_capped_at_requested_end() -> None:
    from datetime import datetime

    intervals = build_horizon_visibility_intervals(
        [2.0, -3.0],
        [
            datetime.fromisoformat("2026-11-10T00:00:00"),
            datetime.fromisoformat("2026-11-10T01:00:00"),
        ],
        step_minutes=60,
        kind="earth_visibility",
        window_end=datetime.fromisoformat("2026-11-10T01:30:00"),
    )

    assert intervals[-1]["end_iso"] == "2026-11-10T01:30:00"
    assert intervals[-1]["duration_minutes"] == 30


def test_horizons_url_builder_uses_v2_params() -> None:
    url = build_horizons_url(
        target="10",
        start_time="2026-01-01",
        stop_time="2026-01-02",
        step="1h",
        observer="399",
    )
    assert "https://ssd.jpl.nasa.gov/api/horizons.api" in url
    assert "COMMAND='10'" in url
    assert "MAKE_EPHEM='YES'" in url
    assert "EPHEM_TYPE='OBSERVER'" in url
    assert "TIME_TYPE='UT'" in url
    assert "START_TIME='2026-01-01'" in url
    assert "STOP_TIME='2026-01-02'" in url
    assert "STEP_SIZE='1h'" in url
    assert "CENTER='500@399'" in url
    assert "OBJ_DATA='NO'" in url
    assert "TIME_DIGITS='SECONDS'" in url


def test_horizons_url_builder_supports_lunar_surface_observer() -> None:
    url = build_horizons_url(
        target="10",
        start_time="2026-11-10 00:00",
        stop_time="2026-11-10 01:00",
        step="1h",
        observer="301",
        site_coordinates=(-30.0, -85.0, 1500.0),
    )

    assert "CENTER='coord@301'" in url
    assert "COORD_TYPE='GEODETIC'" in url
    assert "SITE_COORD='330,-85,1.5'" in url
    assert "QUANTITIES='4'" in url
    assert "APPARENT='AIRLESS'" in url


def test_horizons_url_builder_preserves_date_time_separator() -> None:
    url = build_horizons_url(
        target="10",
        start_time="2026-11-10+00:00:00",
        stop_time="2026-11-10+01:00:00",
        observer="301",
        site_coordinates=(0.0, 0.0, 0.0),
    )

    assert "START_TIME='2026-11-10%2000:00:00'" in url
    assert "STOP_TIME='2026-11-10%2001:00:00'" in url


@pytest.mark.parametrize("case", HORIZONS_LUNAR_SITE_FIXTURE["cases"])
def test_horizons_lunar_site_az_el_parser_matches_live_reference(case: dict) -> None:
    assert parse_horizons_az_el_result(
        _headered_q4_result(case["raw_result_excerpt"])
    ) == case["expected_points"]


def test_horizons_lunar_site_fetch_returns_angle_provenance(monkeypatch) -> None:
    case = HORIZONS_LUNAR_SITE_FIXTURE["cases"][0]
    monkeypatch.setattr(
        official_data.httpx,
        "get",
        lambda *args, **kwargs: httpx.Response(
            200,
            json={"result": _headered_q4_result(case["raw_result_excerpt"])},
            request=httpx.Request("GET", args[0]),
        ),
    )

    payload = fetch_official_ephemeris(
        target=case["target_id"],
        start_time="2026-11-10 00:00",
        stop_time="2026-11-10 01:00",
        observer="301",
        site_coordinates=(0.0, 0.0, 0.0),
    )

    assert payload["status"] == "ok"
    assert payload["point_count"] == 2
    assert payload["time_system"] == "UT"
    assert payload["position_kind"] == "airless_apparent_target_center"
    assert payload["observer_site"] == {
        "body_id": "301",
        "longitude_east_deg": 0.0,
        "latitude_deg": 0.0,
        "altitude_km": 0.0,
        "coordinate_frame": "MEAN_ME (high precision)",
    }
    assert payload["request_parameters"]["observer"] == "coord@301"
    assert payload["request_parameters"]["time_type"] == "UT"


def test_horizons_lunar_site_series_aligns_sun_and_earth_samples(monkeypatch) -> None:
    cases = {case["target_id"]: case for case in HORIZONS_LUNAR_SITE_FIXTURE["cases"]}

    def fake_fetch(target: str, **kwargs) -> dict:
        case = cases[target]
        points = parse_horizons_az_el_result(_headered_q4_result(case["raw_result_excerpt"]))
        return {
            "status": "ok",
            "source_name": "NASA/JPL Horizons",
            "parsed_points": points,
            "observer_site": {
                "body_id": "301",
                "longitude_east_deg": 0.0,
                "latitude_deg": 0.0,
                "altitude_km": 0.0,
                "coordinate_frame": case["coordinate_frame"],
            },
        }

    monkeypatch.setattr(official_data, "fetch_official_ephemeris", fake_fetch)

    result = fetch_lunar_site_sky_series(
        start_time="2026-11-10 00:00",
        stop_time="2026-11-10 01:00",
        step="1h",
        site_coordinates=(0.0, 0.0, 0.0),
    )

    assert result["status"] == "ok"
    assert len(result["samples"]) == 2
    assert result["samples"][0] == {
        "timestamp_ut": "2026-11-10T00:00:00",
        "sun": {"azimuth_deg": 108.609341, "elevation_deg": -85.153524},
        "earth": {"azimuth_deg": 27.56283, "elevation_deg": 82.556826},
    }
    assert result["request_summary"]["time_type"] == "UT"
    assert result["target_coverage"]["sun"]["cadence_consistent"] is True


def test_horizons_lunar_site_series_preserves_sample_missing_from_one_target(monkeypatch) -> None:
    cases = {case["target_id"]: case for case in HORIZONS_LUNAR_SITE_FIXTURE["cases"]}

    def fake_fetch(target: str, **kwargs) -> dict:
        case = cases[target]
        points = parse_horizons_az_el_result(_headered_q4_result(case["raw_result_excerpt"]))
        if target == "399":
            points.pop()
        return {
            "status": "ok",
            "parsed_points": points,
            "observer_site": {
                "body_id": "301",
                "longitude_east_deg": 0.0,
                "latitude_deg": 0.0,
                "altitude_km": 0.0,
                "coordinate_frame": case["coordinate_frame"],
            },
        }

    monkeypatch.setattr(official_data, "fetch_official_ephemeris", fake_fetch)
    result = fetch_lunar_site_sky_series(
        start_time="2026-11-10 00:00",
        stop_time="2026-11-10 01:00",
        step="1h",
        site_coordinates=(0.0, 0.0, 0.0),
    )

    assert result["status"] == "ok"
    assert len(result["samples"]) == 2
    assert result["samples"][1]["earth"] == {"azimuth_deg": None, "elevation_deg": None}
    assert result["target_coverage"]["earth"]["missing_sample_count"] == 1
    assert result["target_coverage"]["earth"]["cadence_consistent"] is True


def test_horizons_lunar_site_series_rejects_mismatched_frames(monkeypatch) -> None:
    cases = {case["target_id"]: case for case in HORIZONS_LUNAR_SITE_FIXTURE["cases"]}

    def fake_fetch(target: str, **kwargs) -> dict:
        case = cases[target]
        points = parse_horizons_az_el_result(_headered_q4_result(case["raw_result_excerpt"]))
        frame = case["coordinate_frame"]
        if target == "399":
            frame = "different lunar frame"
        return {
            "status": "ok",
            "parsed_points": points,
            "observer_site": {
                "body_id": "301",
                "longitude_east_deg": 0.0,
                "latitude_deg": 0.0,
                "altitude_km": 0.0,
                "coordinate_frame": frame,
            },
        }

    monkeypatch.setattr(official_data, "fetch_official_ephemeris", fake_fetch)
    result = fetch_lunar_site_sky_series(
        start_time="2026-11-10 00:00",
        stop_time="2026-11-10 01:00",
        step="1h",
        site_coordinates=(0.0, 0.0, 0.0),
    )

    assert result["status"] == "unavailable"
    assert result["errors"] == {
        "alignment": "Sun and Earth observer metadata does not match"
    }


def test_horizons_lunar_site_analysis_endpoint_returns_labeled_series(monkeypatch) -> None:
    import app.main as main_module

    forwarded: dict[str, object] = {}

    def fake_fetch(**kwargs) -> dict:
        forwarded.update(kwargs)
        return {
            "status": "ok",
            "source_type": "official",
            "source_name": "NASA/JPL Horizons",
            "observer_body_id": "301",
            "longitude_east_deg": 0.0,
            "latitude_deg": 0.0,
            "altitude_km": 1.5,
            "coordinate_frame": "MEAN_ME (high precision)",
            "time_system": "UT",
            "position_kind": "airless_apparent_target_center",
            "azimuth_convention": "clockwise_from_north",
            "elevation_reference": "plane_perpendicular_to_local_zenith",
            "samples": [
                {
                    "timestamp_ut": "2026-11-10T00:00:00",
                    "sun": {"azimuth_deg": 108.609341, "elevation_deg": -85.153524},
                    "earth": {"azimuth_deg": 27.562830, "elevation_deg": 82.556826},
                }
            ],
        }

    monkeypatch.setattr(
        main_module,
        "fetch_lunar_site_sky_series",
        fake_fetch,
    )

    response = client.get(
        "/api/analysis/horizons?start_time=2026-11-10T00%3A00%3A00Z"
        "&stop_time=2026-11-10T01%3A00%3A00Z&site_longitude_deg=0"
        "&site_latitude_deg=0&site_altitude_km=1.5"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_type"] == "official"
    assert payload["position_kind"] == "airless_apparent_target_center"
    assert payload["time_system"] == "UT"
    assert payload["samples"][0]["earth"]["elevation_deg"] == 82.556826
    assert "not Cartesian geometric vectors" in payload["limitations"][0]
    assert any("do not model Earth-disc visibility" in item for item in payload["limitations"])
    assert payload["request_summary"] == {}
    assert payload["target_coverage"] == {}
    assert payload["visibility_analysis"]["sample_step_minutes"] == 60
    assert payload["visibility_analysis"]["sun_data_complete"] is True
    assert payload["visibility_analysis"]["earth_data_complete"] is True
    assert payload["visibility_analysis"]["total_sunlight_minutes"] == 0
    assert payload["visibility_analysis"]["total_earth_visible_minutes"] == 60
    assert payload["visibility_analysis"]["earth_visibility_intervals"] == [
        {
            "kind": "earth_visibility",
            "state": "visible",
            "start_iso": "2026-11-10T00:00:00",
            "end_iso": "2026-11-10T01:00:00",
            "duration_minutes": 60,
        }
    ]
    assert forwarded == {
        "start_time": "2026-11-10 00:00:00",
        "stop_time": "2026-11-10 01:00:00",
        "step": "1h",
        "site_coordinates": (0.0, 0.0, 1500.0),
    }


def test_horizons_analysis_rejects_steps_without_whole_minute_resolution(
    monkeypatch,
) -> None:
    import app.main as main_module

    monkeypatch.setattr(
        main_module,
        "fetch_lunar_site_sky_series",
        lambda **kwargs: pytest.fail("invalid step must be rejected before fetching"),
    )

    response = client.get(
        "/api/analysis/horizons?start_time=2026-11-10T00%3A00%3A00Z"
        "&stop_time=2026-11-10T01%3A00%3A00Z&site_longitude_deg=0"
        "&site_latitude_deg=0&site_altitude_km=0&step=30s"
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "step must be a positive whole-minute Horizons interval"


@pytest.mark.parametrize(
    ("samples", "stop_time", "expected_detail"),
    [
        ([], "2026-11-10T01%3A00%3A00Z", "Horizons returned no samples"),
        (
            [
                {
                    "timestamp_ut": "2026-11-10T00:00:00",
                    "sun": {"azimuth_deg": 0.0, "elevation_deg": 1.0},
                    "earth": {"azimuth_deg": 0.0, "elevation_deg": -1.0},
                },
                {
                    "timestamp_ut": "2026-11-10T00:30:00",
                    "sun": {"azimuth_deg": 0.0, "elevation_deg": 1.0},
                    "earth": {"azimuth_deg": 0.0, "elevation_deg": -1.0},
                },
            ],
            "2026-11-10T01%3A00%3A00Z",
            "Horizons samples do not match the requested step",
        ),
    ],
)
def test_horizons_visibility_analysis_rejects_empty_or_irregular_samples(
    monkeypatch,
    samples,
    stop_time,
    expected_detail,
) -> None:
    import app.main as main_module

    monkeypatch.setattr(
        main_module,
        "fetch_lunar_site_sky_series",
        lambda **kwargs: {
            "status": "ok",
            "source_type": "official",
            "source_name": "NASA/JPL Horizons",
            "observer_body_id": "301",
            "longitude_east_deg": 0.0,
            "latitude_deg": 0.0,
            "altitude_km": 0.0,
            "coordinate_frame": "MEAN_ME (high precision)",
            "time_system": "UT",
            "position_kind": "airless_apparent_target_center",
            "azimuth_convention": "clockwise_from_north",
            "elevation_reference": "plane_perpendicular_to_local_zenith",
            "samples": samples,
        },
    )

    response = client.get(
        "/api/analysis/horizons?start_time=2026-11-10T00%3A00%3A00Z"
        f"&stop_time={stop_time}&site_longitude_deg=0"
        "&site_latitude_deg=0&site_altitude_km=0"
    )

    assert response.status_code == 502
    assert expected_detail in response.json()["detail"]


def test_horizons_lunar_site_analysis_rejects_non_utc_interval() -> None:
    response = client.get(
        "/api/analysis/horizons?start_time=2026-11-10T00%3A00%3A00"
        "&stop_time=2026-11-10T01%3A00%3A00Z&site_longitude_deg=0"
        "&site_latitude_deg=0&site_altitude_km=0"
    )

    assert response.status_code == 422
    assert "start_time must include" in response.json()["detail"]


def test_horizons_lunar_site_analysis_surfaces_upstream_failure(monkeypatch) -> None:
    import app.main as main_module

    monkeypatch.setattr(
        main_module,
        "fetch_lunar_site_sky_series",
        lambda **kwargs: {
            "status": "unavailable",
            "errors": {"earth": "Horizons API unavailable"},
        },
    )

    response = client.get(
        "/api/analysis/horizons?start_time=2026-11-10T00%3A00%3A00Z"
        "&stop_time=2026-11-10T01%3A00%3A00Z&site_longitude_deg=0"
        "&site_latitude_deg=0&site_altitude_km=0"
    )

    assert response.status_code == 502
    assert response.json()["detail"] == {"earth": "Horizons API unavailable"}


@pytest.mark.parametrize(
    ("site_coordinates", "expected_error"),
    [
        ((0.0, 91.0, 0.0), "latitude"),
        ((0.0, 0.0, float("inf")), "finite"),
    ],
)
def test_horizons_site_observer_rejects_invalid_coordinates(
    site_coordinates: tuple[float, float, float], expected_error: str
) -> None:
    with pytest.raises(ValueError, match=expected_error):
        build_horizons_url(
            target="10",
            start_time="2026-11-10 00:00",
            stop_time="2026-11-10 01:00",
            observer="301",
            site_coordinates=site_coordinates,
        )


def test_official_adapter_with_valid_soee_result() -> None:
    """Verify parsing of a handcrafted Horizons-shaped fixture, not reference data."""
    parsed = parse_horizons_result(HORIZONS_RESULT_FIXTURE)
    assert len(parsed) == 2
    assert parsed[0]["timestamp_iso"] == "2026-01-01T00:00:00"
    assert parsed[0]["ra_deg"] == 0.0
    assert parsed[1]["ra_deg"] == 0.25 * 15.0  # 15 min = 0.25h * 15
    assert parsed[0]["elongation_deg"] == 180.0
    assert parsed[0]["mag"] == -12.0
    assert parsed[0]["dist_au"] == 1.0


def test_horizons_parser_rejects_missing_markers() -> None:
    with pytest.raises(ValueError, match="missing the SOE marker"):
        parse_horizons_result("no markers here")
    with pytest.raises(ValueError, match="missing the EOE marker"):
        parse_horizons_result("$$SOE\n")


def test_horizons_parser_rejects_short_data_rows() -> None:
    with pytest.raises(ValueError, match="expected at least 25 columns"):
        parse_horizons_result("$$SOE\n2026-Jan-01 00:00 00 00\n$$EOE")


def test_horizons_fetch_parses_json_result_offline(monkeypatch) -> None:
    def fake_get(url, **kwargs):
        return httpx.Response(
            200,
            json={"result": HORIZONS_RESULT_FIXTURE},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(official_data.httpx, "get", fake_get)

    payload = fetch_official_ephemeris()

    assert payload["status"] == "ok"
    assert payload["point_count"] == 2
    assert payload["parsed_points"][0]["timestamp_iso"] == "2026-01-01T00:00:00"


@pytest.mark.parametrize("source_type", ["official", "computed", "synthetic"])
def test_normalized_ephemeris_contract_requires_explicit_frame_and_provenance(
    source_type: str,
) -> None:
    point = NormalizedEphemerisPoint(
        target="10",
        observer="399",
        timestamp_utc="2026-01-01T00:00:00Z",
        source_time_system="UT",
        coordinate_frame="ICRF",
        target_position_relative_to_observer_km=(1.0, 2.0, 3.0),
        source_type=source_type,
        source_name="NASA/JPL Horizons",
        source_url="https://ssd.jpl.nasa.gov/api/horizons.api",
    )

    assert point.timestamp_utc.isoformat() == "2026-01-01T00:00:00+00:00"
    assert point.target_position_relative_to_observer_km == (1.0, 2.0, 3.0)
    assert point.source_time_system == "UT"
    assert point.model_dump()["source_type"] == source_type


@pytest.mark.parametrize(
    "timestamp",
    ["2026-01-01T00:00:00", "2026-01-01T00:00:00+01:00"],
)
def test_normalized_ephemeris_contract_rejects_non_utc_timestamps(timestamp: str) -> None:
    with pytest.raises(ValueError, match="zero UTC offset"):
        NormalizedEphemerisPoint(
            target="10",
            observer="399",
            timestamp_utc=timestamp,
            source_time_system="UT",
            coordinate_frame="ICRF",
            target_position_relative_to_observer_km=(1.0, 2.0, 3.0),
            source_type="official",
            source_name="NASA/JPL Horizons",
        )


@pytest.mark.parametrize("position", [(1.0, 2.0), (1.0, 2.0, float("inf"))])
def test_normalized_ephemeris_contract_rejects_invalid_vectors(position) -> None:
    with pytest.raises(ValueError):
        NormalizedEphemerisPoint(
            target="10",
            observer="399",
            timestamp_utc="2026-01-01T00:00:00Z",
            source_time_system="UT",
            coordinate_frame="ICRF",
            target_position_relative_to_observer_km=position,
            source_type="official",
            source_name="NASA/JPL Horizons",
        )


def test_normalized_ephemeris_contract_rejects_unknown_source_type() -> None:
    with pytest.raises(ValueError):
        NormalizedEphemerisPoint(
            target="10",
            observer="399",
            timestamp_utc="2026-01-01T00:00:00Z",
            source_time_system="UT",
            coordinate_frame="ICRF",
            target_position_relative_to_observer_km=(1.0, 2.0, 3.0),
            source_type="reference",
            source_name="Example",
        )


@pytest.mark.parametrize(
    ("stdout", "expected_error"),
    [
        ("", "Empty response"),
        ("not JSON", "Invalid JSON"),
        ("[]", "must be a JSON object"),
        ('{"error":"invalid query"}', "Horizons API error"),
        ('{"message":"no result"}', "missing a text result"),
        ('{"result":"no delimiters"}', "missing the SOE marker"),
        ('{"result":"$$SOE\\n2026-Jan-01 00:00 00 00\\n$$EOE"}', "expected at least 25 columns"),
        (
            json.dumps(
                {"result": HORIZONS_RESULT_FIXTURE.replace("2026-Jan-01 00:00", "bad-date 00:00", 1)}
            ),
            "Unsupported Horizons timestamp",
        ),
    ],
)
def test_horizons_fetch_reports_invalid_responses_offline(
    monkeypatch, stdout: str, expected_error: str
) -> None:
    def fake_get(url, **kwargs):
        return httpx.Response(
            200,
            content=stdout.encode(),
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(official_data.httpx, "get", fake_get)

    payload = fetch_official_ephemeris()

    assert payload["status"] == "unavailable"
    assert payload["parsed_points"] == []
    assert expected_error in payload["error"]


def test_horizons_fetch_reports_connection_failure_offline(monkeypatch) -> None:
    def fail_connect(url, **kwargs):
        raise httpx.ConnectError("connection failed", request=httpx.Request("GET", url))

    monkeypatch.setattr(official_data.httpx, "get", fail_connect)

    payload = fetch_official_ephemeris()

    assert payload["status"] == "unavailable"
    assert "connection failed" in payload["error"]


def test_horizons_fetch_reports_timeout_offline(monkeypatch) -> None:
    def timeout(url, **kwargs):
        raise httpx.ReadTimeout("request timed out", request=httpx.Request("GET", url))

    monkeypatch.setattr(official_data.httpx, "get", timeout)

    payload = fetch_official_ephemeris()

    assert payload["status"] == "unavailable"
    assert "timed out" in payload["error"]


def test_celestial_to_horizontal_basic() -> None:
    """Test the RA/Dec to Az/Alt conversion."""
    # At RA=Dec=0, LST=0, lat=0, object is on the meridian at zenith
    result = celestial_to_horizontal(0.0, 0.0, 0.0, 0.0)
    assert -90.0 <= result["azimuth_deg"] <= 360.0
    assert -90.0 <= result["altitude_deg"] <= 90.0
    assert result["altitude_deg"] > 0.0  # Should be above horizon


def test_celestial_to_horizontal_below_horizon() -> None:
    """Test object below horizon."""
    # RA=180, Dec=0, LST=0 means object is on the opposite side of the sky
    result = celestial_to_horizontal(180.0, 0.0, 0.0, 45.0)
    assert result["altitude_deg"] < 0.0  # Should be below horizon


def test_lunar_site_visibility_basic() -> None:
    """Test the full lunar site visibility computation."""
    # Sun at RA=0, Dec=0, Earth at RA=12h=180deg, Dec=0
    # LST=0, lat=45, lon=0
    result = compute_lunar_site_visibility(
        ra_sun_deg=0.0,
        dec_sun_deg=0.0,
        ra_earth_deg=180.0,
        dec_earth_deg=0.0,
        lst_deg=0.0,
        latitude_deg=45.0,
        longitude_deg=0.0,
    )
    assert "sun_azimuth_deg" in result
    assert "sun_elevation_deg" in result
    assert "earth_azimuth_deg" in result
    assert "earth_elevation_deg" in result
    assert "sun_above_horizon" in result
    assert "earth_above_horizon" in result


def test_official_api_endpoint_signature(monkeypatch) -> None:
    """Verify /api/official forwards parameters without making a network request."""
    import app.main as main_module

    received = {}

    def fake_official(**kwargs):
        received.update(kwargs)
        return {
            "status": "unavailable",
            "source_name": "NASA/JPL Horizons",
            "api_url": "https://example.test/horizons",
            "parsed_points": [],
            "error": "mocked upstream failure",
        }

    monkeypatch.setattr(main_module, "fetch_official_ephemeris", fake_official)

    response = client.get(
        "/api/official?target=10&start_time=2026-01-01+00:00:00&stop_time=2026-01-02+00:00:00&step=1h&observer=399"
    )
    assert response.status_code == 200
    payload = response.json()
    assert "status" in payload
    assert "source_name" in payload
    assert "parsed_points" in payload
    assert "api_url" in payload
    assert received == {
        "target": "10",
        "start_time": "2026-01-01 00:00:00",
        "stop_time": "2026-01-02 00:00:00",
        "step": "1h",
        "observer": "399",
        "site_coordinates": None,
    }


def test_official_api_endpoint_requires_complete_site_coordinates(monkeypatch) -> None:
    import app.main as main_module

    monkeypatch.setattr(
        main_module,
        "fetch_official_ephemeris",
        lambda **kwargs: {"status": "ok", "parsed_points": []},
    )

    response = client.get("/api/official?observer=301&site_longitude_deg=0")

    assert response.status_code == 400
    assert "must be provided together" in response.json()["detail"]


def test_official_api_endpoint_requires_observer_body_for_site_coordinates() -> None:
    response = client.get(
        "/api/official?site_longitude_deg=0&site_latitude_deg=0&site_altitude_km=0"
    )

    assert response.status_code == 400
    assert "observer body ID must be provided" in response.json()["detail"]


def test_official_api_endpoint_forwards_lunar_site_coordinates(monkeypatch) -> None:
    import app.main as main_module

    received = {}
    monkeypatch.setattr(
        main_module,
        "fetch_official_ephemeris",
        lambda **kwargs: received.update(kwargs) or {"status": "ok", "parsed_points": []},
    )

    response = client.get(
        "/api/official?target=10&observer=301&site_longitude_deg=0"
        "&site_latitude_deg=-85&site_altitude_km=1.5"
    )

    assert response.status_code == 200
    assert received["observer"] == "301"
    assert received["site_coordinates"] == (0.0, -85.0, 1500.0)


def test_analysis_endpoint_prefers_official_source_when_available(monkeypatch) -> None:
    import app.main as main_module

    def fake_official(*args, **kwargs):
        return {
            "status": "ok",
            "source_name": "NASA/JPL Horizons",
            "api_url": "https://example.test/horizons",
            "parsed_points": [],
            "point_count": 0,
            "target": "10",
        }

    monkeypatch.setattr(main_module, "fetch_official_ephemeris", fake_official)

    response = client.get(
        "/api/analysis?site_id=demo-north-polar&start_date=2026-01-01&end_date=2026-01-02&step_minutes=60&source=official"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["source_type"] == "synthetic"
    assert payload["model_status"] == "synthetic_demo_data_only"
    assert payload["summary"]["data_source"] == "synthetic"
    assert payload["summary"]["ephemeris_reference_source"] == "NASA/JPL Horizons"
    assert payload["summary"]["api_status"] == "ok"
    assert "not used to calculate this analysis" in payload["summary"]["source_note"]
    assert payload["official_data"]["source_name"] == "NASA/JPL Horizons"
    assert payload["summary"]["official_ephemeris_available"] is True
    assert payload["summary"]["official_data_used_for_site_geometry"] is False


def test_analysis_endpoint_uses_reference_source_when_selected() -> None:
    response = client.get(
        "/api/analysis?site_id=demo-north-polar&start_date=2026-01-01&end_date=2026-01-02&step_minutes=60&source=reference"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["source_type"] == "reference"
    assert payload["model_status"] == "reference_snapshot"
    assert payload["summary"]["source_note"] == "Reference snapshot mode selected."


def test_analysis_endpoint_returns_point_count_when_official_available(monkeypatch) -> None:
    import app.main as main_module

    def fake_official(*args, **kwargs):
        return {
            "status": "ok",
            "source_name": "NASA/JPL Horizons",
            "api_url": "https://example.test/horizons",
            "parsed_points": [{"timestamp_iso": "2026-01-01T00:00:00"}] * 24,
            "point_count": 24,
            "target": "10",
        }

    monkeypatch.setattr(main_module, "fetch_official_ephemeris", fake_official)

    response = client.get(
        "/api/analysis?site_id=demo-north-polar&start_date=2026-01-01&end_date=2026-01-02&step_minutes=60&source=official"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["point_count"] == 24
    assert payload["summary"]["api_status"] == "ok"
