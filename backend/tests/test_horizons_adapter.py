import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.official_data import (
    build_horizons_url,
    fetch_lunar_site_sky_series,
    fetch_official_ephemeris,
    parse_horizons_az_el_result,
)
from app.main import app


SITE_METERS = (-30.0, -85.0, 1500.0)
REQUEST = {
    "start_time": "2026-Nov-10 00:00:00",
    "stop_time": "2026-Nov-10 02:00:00",
    "step": "1h",
    "site_coordinates": SITE_METERS,
}
TABLE_HEADER = "Date__(UT)__HR:MN    Azi_(a-app)_    Elev_(a-app)_"
LIVE_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "reference"
    / "horizons_lunar_site_q4_live_2026-11-14.json"
)
HOR02_LIVE_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "reference"
    / "horizons_lunar_site_q4_live_2026-11-15.json"
)


def q4_result(rows: str, header: str = TABLE_HEADER) -> str:
    return f"{header}\n$$SOE\n{rows}\n$$EOE"


def parsed_point(timestamp: str, azimuth: float | None = 10.0, elevation: float | None = 20.0) -> dict:
    return {
        "timestamp_iso": timestamp,
        "azimuth_apparent_deg": azimuth,
        "elevation_apparent_deg": elevation,
    }


def observer_metadata(frame: str = "MEAN_ME") -> dict:
    return {
        "body_id": "301",
        "longitude_east_deg": 330.0,
        "latitude_deg": -85.0,
        "altitude_km": 1.5,
        "coordinate_frame": frame,
    }


def fake_target_response(
    points: list[dict], frame: str = "MEAN_ME"
) -> dict:
    return {
        "status": "ok",
        "source_name": "NASA/JPL Horizons",
        "target": "10",
        "parsed_points": points,
        "observer_site": observer_metadata(frame),
    }


@pytest.mark.parametrize("target", ["10", "399"])
def test_lunar_request_parameters_and_site_units(target: str) -> None:
    url = build_horizons_url(
        target=target,
        start_time="2026-11-10+00:00:00",
        stop_time="2026-11-10+01:00:00",
        step="10 min",
        observer="301",
        site_coordinates=SITE_METERS,
    )

    assert "COMMAND='%s'" % target in url
    assert "OBJ_DATA='NO'" in url
    assert "MAKE_EPHEM='YES'" in url
    assert "EPHEM_TYPE='OBSERVER'" in url
    assert "CENTER='coord@301'" in url
    assert "COORD_TYPE='GEODETIC'" in url
    assert "SITE_COORD='330,-85,1.5'" in url
    assert "TIME_TYPE='UT'" in url
    assert "TIME_DIGITS='SECONDS'" in url
    assert "START_TIME='2026-11-10%2000:00:00'" in url
    assert "STOP_TIME='2026-11-10%2001:00:00'" in url
    assert "STEP_SIZE='10%20min'" in url
    assert "QUANTITIES='4'" in url
    assert "APPARENT='AIRLESS'" in url


def test_q4_parser_uses_reordered_header_and_preserves_nulls() -> None:
    raw = q4_result(
        "2026-Nov-10 00:00    -25.5    extra    --",
        "Date__(UT)__HR:MN    Elev_(a-app)_    Other_column    Azi_(a-app)_",
    )

    assert parse_horizons_az_el_result(raw) == [
        parsed_point("2026-11-10T00:00:00", None, -25.5)
    ]


def test_q4_parser_accepts_csv_header_and_rows() -> None:
    raw = q4_result(
        '"2026-Nov-10 00:00",,15.25',
        "Date__(UT)__HR:MN,Azi_(a-app)_,Elev_(a-app)_",
    )

    assert parse_horizons_az_el_result(raw) == [
        parsed_point("2026-11-10T00:00:00", None, 15.25)
    ]


@pytest.mark.parametrize("marker", ["i", "*", "*i", "i*"])
def test_q4_parser_ignores_horizons_presence_marker(marker: str) -> None:
    raw = q4_result(
        f"2026-Nov-14 13:00:00 {marker} 179.984042 3.442056",
        "Date__(UT)__HR:MN:SS     Azi____(a-app)___Elev",
    )

    assert parse_horizons_az_el_result(raw) == [
        parsed_point("2026-11-14T13:00:00", 179.984042, 3.442056)
    ]


def test_q4_parser_accepts_saved_live_horizons_responses() -> None:
    fixture = json.loads(LIVE_FIXTURE_PATH.read_text(encoding="utf-8"))
    expected_by_target = {
        "10": [
            parsed_point("2026-11-10T00:00:00", 108.609341, -85.153524),
            parsed_point("2026-11-10T01:00:00", 106.872231, -84.669136),
        ],
        "399": [
            parsed_point("2026-11-10T00:00:00", 27.562830, 82.556826),
            parsed_point("2026-11-10T01:00:00", 27.315336, 82.573506),
        ],
    }

    for record in fixture["live_response_records"]:
        response_body = json.loads(record["response_body_text"])
        points = parse_horizons_az_el_result(response_body["result"])
        assert record["http_status"] == 200
        assert points == expected_by_target[record["target_id"]]


def test_hor02_live_fixture_has_parseable_aligned_sun_and_earth_samples() -> None:
    fixture = json.loads(HOR02_LIVE_FIXTURE_PATH.read_text(encoding="utf-8"))
    points_by_target = {}

    for record in fixture["live_response_records"]:
        response_body = json.loads(record["response_body_text"])
        points = parse_horizons_az_el_result(response_body["result"])
        assert record["http_status"] == 200
        assert record["request_url"].startswith("https://ssd.jpl.nasa.gov/api/horizons.api?")
        assert len(points) == 3
        points_by_target[record["target_id"]] = points

    assert set(points_by_target) == {"10", "399"}
    assert [point["timestamp_iso"] for point in points_by_target["10"]] == [
        point["timestamp_iso"] for point in points_by_target["399"]
    ]


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("No table header", "missing the SOE marker"),
        ("Date__(UT)__HR:MN    Azi_(a-app)_    Elev_(a-app)_\n$$SOE\n", "missing the EOE marker"),
        (
            "Date__(UT)__HR:MN    Azi_(a-app)_    Elev_(a-app)_\n$$EOE\n$$SOE",
            "EOE marker must follow SOE",
        ),
        (
            "$$SOE\n2026-Nov-10 00:00    10    20\n$$EOE",
            "missing a recognized Q4 table header",
        ),
        (
            "Date__(UT)__HR:MN    Azi_(a-app)_\n$$SOE\n2026-Nov-10 00:00    10\n$$EOE",
            "missing a recognized Q4 table header",
        ),
        (
            "Date__(UT)__HR:MN    Azi_(a-app)_    Elev_(a-app)_\n$$SOE\n$$EOE",
            "no data rows",
        ),
        (
            "Date__(UT)__HR:MN    Azi_(a-app)_    Elev_(a-app)_\n$$SOE\n"
            "2026-Nov-10 00:00    10\n$$EOE",
            "expected 3 columns",
        ),
        (
            "Date__(UT)__HR:MN    Azi_(a-app)_    Elev_(a-app)_\n$$SOE\n"
            "bad-date 00:00    10    20\n$$EOE",
            "Invalid timestamp",
        ),
        (
            "Date__(UT)__HR:MN    Azi_(a-app)_    Elev_(a-app)_\n$$SOE\n"
            "2026-Nov-10 00:00    invalid    20\n$$EOE",
            "Invalid azimuth",
        ),
        (
            "Date__(UT)__HR:MN    Azi_(a-app)_    Elev_(a-app)_\n$$SOE\n"
            "2026-Nov-10 00:00    NaN    20\n$$EOE",
            "Invalid azimuth",
        ),
        (
            "Date__(UT)__HR:MN    Azi_(a-app)_    Elev_(a-app)_\n$$SOE\n"
            "2026-Nov-10 00:00    10    91\n$$EOE",
            "Invalid elevation",
        ),
    ],
)
def test_q4_parser_rejects_invalid_tables(raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_horizons_az_el_result(raw)


@pytest.mark.parametrize("status_code", [400, 503])
def test_horizons_transport_rejects_http_errors(monkeypatch, status_code: int) -> None:
    def fake_get(url: str, **kwargs):
        return httpx.Response(
            status_code,
            text="upstream error",
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("app.official_data.httpx.get", fake_get)
    result = fetch_official_ephemeris(
        target="10", observer="301", site_coordinates=(0.0, 0.0, 0.0)
    )

    assert result["status"] == "unavailable"
    assert result["http_status"] == status_code
    assert f"HTTP {status_code}" in result["error"]


@pytest.mark.parametrize(
    ("exception_type", "message"),
    [
        (httpx.ReadTimeout, "timed out"),
        (httpx.ConnectError, "connection failed"),
    ],
)
def test_horizons_transport_reports_network_failures(
    monkeypatch, exception_type: type[Exception], message: str
) -> None:
    def fail(url: str, **kwargs):
        raise exception_type(message, request=httpx.Request("GET", url))

    monkeypatch.setattr("app.official_data.httpx.get", fail)
    result = fetch_official_ephemeris(
        target="10", observer="301", site_coordinates=(0.0, 0.0, 0.0)
    )

    assert result["status"] == "unavailable"
    assert message in result["error"]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("not JSON", "Invalid JSON"),
        ('{"error":"invalid query"}', "Horizons API error"),
        ('{"result":42}', "missing a text result"),
        ('{"result":"API ERROR: invalid query"}', "missing the SOE marker"),
    ],
)
def test_horizons_transport_rejects_invalid_json_payloads(
    monkeypatch, body: str, message: str
) -> None:
    def fake_get(url: str, **kwargs):
        return httpx.Response(
            200,
            content=body.encode(),
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("app.official_data.httpx.get", fake_get)
    result = fetch_official_ephemeris(
        target="10", observer="301", site_coordinates=(0.0, 0.0, 0.0)
    )

    assert result["status"] == "unavailable"
    assert message in result["error"]


def test_horizons_transport_parses_headered_live_format_without_network(monkeypatch) -> None:
    body = {
        "result": q4_result(
            "2026-Nov-10 00:00  i  10.5  -20.25",
            "Date__(UT)__HR:MN    Azi_(a-app)_    Elev_(a-app)_",
        )
    }

    def fake_get(url: str, **kwargs):
        assert kwargs["timeout"] == (10.0, 60.0)
        return httpx.Response(
            200,
            json=body,
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("app.official_data.httpx.get", fake_get)
    result = fetch_official_ephemeris(
        target="399", observer="301", site_coordinates=SITE_METERS
    )

    assert result["status"] == "ok"
    assert result["http_status"] == 200
    assert result["target"] == "399"
    assert result["observer_site"]["altitude_km"] == 1.5
    assert result["request_parameters"]["time_type"] == "UT"
    assert result["parsed_points"] == [
        parsed_point("2026-11-10T00:00:00", 10.5, -20.25)
    ]


def test_alignment_outer_joins_missing_target_samples_and_null_angles(monkeypatch) -> None:
    def fake_fetch(target: str, **kwargs) -> dict:
        if target == "10":
            points = [
                parsed_point("2026-11-10T00:00:00", 1.0, 2.0),
                parsed_point("2026-11-10T01:00:00", None, 3.0),
                parsed_point("2026-11-10T02:00:00", 5.0, None),
            ]
        else:
            points = [
                parsed_point("2026-11-10T00:00:00", 10.0, 20.0),
                parsed_point("2026-11-10T02:00:00", 30.0, 40.0),
            ]
        return fake_target_response(points)

    monkeypatch.setattr("app.official_data.fetch_official_ephemeris", fake_fetch)
    result = fetch_lunar_site_sky_series(**REQUEST)

    assert result["status"] == "ok"
    assert [sample["timestamp_ut"] for sample in result["samples"]] == [
        "2026-11-10T00:00:00",
        "2026-11-10T01:00:00",
        "2026-11-10T02:00:00",
    ]
    assert result["samples"][1]["earth"] == {"azimuth_deg": None, "elevation_deg": None}
    assert result["samples"][1]["sun"] == {"azimuth_deg": None, "elevation_deg": 3.0}
    assert result["samples"][2]["sun"] == {"azimuth_deg": 5.0, "elevation_deg": None}
    assert result["target_coverage"]["earth"]["missing_sample_count"] == 1
    assert result["target_coverage"]["earth"]["cadence_consistent"] is False
    assert result["request_summary"]["time_type"] == "UT"


@pytest.mark.parametrize(
    ("sun_points", "earth_points", "message"),
    [
        (
            [
                parsed_point("2026-11-10T00:00:00"),
                parsed_point("2026-11-10T00:00:00"),
            ],
            [parsed_point("2026-11-10T00:00:00")],
            "duplicate timestamp",
        ),
        (
            [
                parsed_point("2026-11-10T00:00:00"),
                parsed_point("2026-11-10T02:00:00"),
            ],
            [
                parsed_point("2026-11-10T00:00:00"),
                parsed_point("2026-11-10T02:00:00"),
            ],
            "requested cadence",
        ),
        (
            [parsed_point("2026-11-10T00:00:00+00:00")],
            [parsed_point("2026-11-10T00:00:00+00:00")],
            "remain in UT",
        ),
        (
            [{"azimuth_apparent_deg": 10.0, "elevation_apparent_deg": 20.0}],
            [parsed_point("2026-11-10T00:00:00")],
            "without a timestamp",
        ),
    ],
)
def test_alignment_rejects_duplicates_cadence_gaps_and_timezone_relabeling(
    monkeypatch, sun_points: list[dict], earth_points: list[dict], message: str
) -> None:
    def fake_fetch(target: str, **kwargs) -> dict:
        return fake_target_response(sun_points if target == "10" else earth_points)

    monkeypatch.setattr("app.official_data.fetch_official_ephemeris", fake_fetch)
    result = fetch_lunar_site_sky_series(**REQUEST)

    assert result["status"] == "unavailable"
    assert message in result["errors"]["alignment"]


def test_horizons_route_preserves_ut_and_missing_target_samples(monkeypatch) -> None:
    from app import main

    monkeypatch.setattr(
        main,
        "fetch_lunar_site_sky_series",
        lambda **kwargs: {
            "status": "ok",
            "source_type": "official",
            "source_name": "NASA/JPL Horizons",
            "observer_body_id": "301",
            "longitude_east_deg": 0.0,
            "latitude_deg": 0.0,
            "altitude_km": 0.0,
            "coordinate_frame": "MEAN_ME",
            "time_system": "UT",
            "position_kind": "airless_apparent_target_center",
            "azimuth_convention": "clockwise_from_north",
            "elevation_reference": "plane_perpendicular_to_local_zenith",
            "samples": [
                {
                    "timestamp_ut": "2026-11-10T00:00:00",
                    "sun": {"azimuth_deg": 10.0, "elevation_deg": 5.0},
                    "earth": {"azimuth_deg": 20.0, "elevation_deg": 10.0},
                },
                {
                    "timestamp_ut": "2026-11-10T01:00:00",
                    "sun": {"azimuth_deg": 11.0, "elevation_deg": 6.0},
                    "earth": {"azimuth_deg": None, "elevation_deg": None},
                },
            ],
        },
    )

    response = TestClient(app).get(
        "/api/analysis/horizons?start_time=2026-11-10T00%3A00%3A00Z"
        "&stop_time=2026-11-10T01%3A00%3A00Z&site_longitude_deg=0"
        "&site_latitude_deg=0&site_altitude_km=0"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["time_system"] == "UT"
    assert payload["samples"][1]["timestamp_ut"] == "2026-11-10T01:00:00"
    assert payload["samples"][1]["earth"] == {"azimuth_deg": None, "elevation_deg": None}
    assert payload["visibility_analysis"]["earth_data_complete"] is False
    assert any("Earth-disc visibility" in item for item in payload["limitations"])


def test_horizons_route_returns_upstream_failure_without_synthetic_fallback(monkeypatch) -> None:
    from app import main

    monkeypatch.setattr(
        main,
        "fetch_lunar_site_sky_series",
        lambda **kwargs: {
            "status": "unavailable",
            "source_name": "NASA/JPL Horizons",
            "errors": {"sun": "Horizons request timed out"},
        },
    )

    response = TestClient(app).get(
        "/api/analysis/horizons?start_time=2026-11-10T00%3A00%3A00Z"
        "&stop_time=2026-11-10T01%3A00%3A00Z&site_longitude_deg=0"
        "&site_latitude_deg=0&site_altitude_km=0"
    )

    assert response.status_code == 502
    assert "Horizons request timed out" in response.json()["detail"]["sun"]


def test_horizons_route_rejects_empty_live_data(monkeypatch) -> None:
    from app import main

    monkeypatch.setattr(
        main,
        "fetch_lunar_site_sky_series",
        lambda **kwargs: {
            "status": "ok",
            "source_name": "NASA/JPL Horizons",
            "longitude_east_deg": 0.0,
            "latitude_deg": 0.0,
            "altitude_km": 0.0,
            "coordinate_frame": "MEAN_ME",
            "samples": [],
        },
    )

    response = TestClient(app).get(
        "/api/analysis/horizons?start_time=2026-11-10T00%3A00%3A00Z"
        "&stop_time=2026-11-10T01%3A00%3A00Z&site_longitude_deg=0"
        "&site_latitude_deg=0&site_altitude_km=0"
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Horizons returned no samples for the requested interval"
