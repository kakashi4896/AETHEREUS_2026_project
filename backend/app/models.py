from __future__ import annotations

from datetime import datetime, timedelta
from math import isfinite
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Site(BaseModel):
    site_id: str
    name: str
    latitude_deg: float = Field(..., ge=-90.0, le=90.0)
    longitude_deg: float = Field(..., ge=-180.0, le=180.0)
    elevation_m: float = Field(..., ge=0.0)
    source_type: str = "synthetic"
    model_status: str = "synthetic_demo_data_only"
    site_type: Literal[
        "actual_landing_site",
        "official_candidate_site",
        "user_defined_site",
        "synthetic_demo_site",
    ] = "synthetic_demo_site"
    mission_id: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    coordinate_frame: str | None = None
    data_status: Literal["unverified", "verified", "rejected"] = "unverified"


class TimeWindow(BaseModel):
    start_iso: str
    end_iso: str
    step_minutes: int = Field(..., gt=0, le=1440)
    timezone: str = "UTC"


class EphemerisPoint(BaseModel):
    timestamp: datetime
    sun_azimuth_deg: float
    sun_elevation_deg: float
    earth_azimuth_deg: float
    earth_elevation_deg: float


class NormalizedEphemerisPoint(BaseModel):
    target: str = Field(..., min_length=1)
    observer: str = Field(..., min_length=1)
    timestamp_utc: datetime
    source_time_system: Literal["UTC", "UT", "TDB", "ET", "unknown"] = "unknown"
    coordinate_frame: str = Field(..., min_length=1)
    target_position_relative_to_observer_km: tuple[float, float, float]
    source_type: Literal["official", "computed", "synthetic"]
    source_name: str = Field(..., min_length=1)
    source_url: str | None = None
    source_version: str | None = None

    @field_validator("timestamp_utc")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("timestamp_utc must include a zero UTC offset")
        return value

    @field_validator("target_position_relative_to_observer_km")
    @classmethod
    def position_must_be_finite(
        cls, value: tuple[float, float, float]
    ) -> tuple[float, float, float]:
        if not all(isfinite(component) for component in value):
            raise ValueError("position vector components must be finite")
        return value


class VisibilityState(BaseModel):
    timestamp: datetime
    sun_above_horizon: bool
    earth_above_horizon: bool
    sunlight_available: bool
    direct_to_earth_geometric_visibility: bool
    note: str = "synthetic_demo_data_only"


class VisibilityInterval(BaseModel):
    kind: Literal["sunlight", "earth_visibility"]
    state: Literal["visible", "hidden"]
    start_iso: str
    end_iso: str
    duration_minutes: int = Field(..., gt=0)


class AnalysisResult(BaseModel):
    site_id: str
    site_name: str
    source_type: str = "synthetic"
    model_status: str = "synthetic_demo_data_only"
    window: TimeWindow
    ephemeris: list[EphemerisPoint]
    visibility_series: list[VisibilityState]
    sunlight_intervals: list[VisibilityInterval] = Field(default_factory=list)
    earth_visibility_intervals: list[VisibilityInterval] = Field(default_factory=list)
    summary: dict[str, bool | float | int | str]
    official_data: dict[str, object] | None = None


class ComparisonRequest(BaseModel):
    site_ids: list[str] = Field(..., min_length=2)
    start_date: date
    end_date: date | None = None
    step_minutes: int = Field(default=60, gt=0, le=1440)

    @field_validator("site_ids")
    @classmethod
    def site_ids_must_have_at_least_two_entries(cls, value: list[str]) -> list[str]:
        cleaned = [site_id.strip() for site_id in value if site_id and site_id.strip()]
        if len(cleaned) < 2:
            raise ValueError("site_ids must include at least two site IDs")
        return cleaned


class HorizonsApparentAngle(BaseModel):
    azimuth_deg: float | None
    elevation_deg: float | None


class HorizonsLunarSkySample(BaseModel):
    timestamp_ut: datetime
    sun: HorizonsApparentAngle
    earth: HorizonsApparentAngle


class ComparisonRow(BaseModel):
    site_id: str
    site_name: str
    latitude_deg: float
    longitude_deg: float
    altitude_km: float | None = None
    sunlit_fraction: float | None
    longest_no_sunlight_interval_minutes: int | None
    longest_sunlight_interval_minutes: int | None
    earth_visible_fraction: float | None
    longest_no_earth_visibility_interval_minutes: int | None
    longest_earth_visibility_interval_minutes: int | None
    sunlight_intervals: list[VisibilityInterval] = Field(default_factory=list)
    earth_visibility_intervals: list[VisibilityInterval] = Field(default_factory=list)
    samples: list[HorizonsLunarSkySample] = Field(default_factory=list)
    sample_count: int | None = None
    sun_valid_sample_count: int | None = None
    earth_valid_sample_count: int | None = None
    coordinate_frame: str | None = None
    source_type: str = "synthetic"
    model_status: str = "synthetic_demo_data_only"


class ComparisonResult(BaseModel):
    source_type: str = "synthetic"
    model_status: str = "synthetic_demo_data_only"
    source_name: str | None = None
    time_system: Literal["UT", "UTC"] = "UTC"
    site_count: int
    window: TimeWindow
    rows: list[ComparisonRow]


class HorizonsVisibilityAnalysis(BaseModel):
    horizon_elevation_deg: float = 0.0
    sampling_method: Literal["sampled_step_function_no_interpolation"] = (
        "sampled_step_function_no_interpolation"
    )
    sample_step_minutes: int = Field(..., gt=0)
    sample_count: int = Field(..., ge=0)
    valid_sun_sample_count: int = Field(..., ge=0)
    valid_earth_sample_count: int = Field(..., ge=0)
    sun_data_complete: bool
    earth_data_complete: bool
    sunlight_intervals: list[VisibilityInterval] = Field(default_factory=list)
    earth_visibility_intervals: list[VisibilityInterval] = Field(default_factory=list)
    total_sunlight_minutes: int | None
    longest_continuous_darkness_minutes: int | None
    total_earth_visible_minutes: int | None
    longest_continuous_earth_hidden_minutes: int | None


class HorizonsTargetCoverage(BaseModel):
    first_timestamp_ut: datetime
    last_timestamp_ut: datetime
    sample_count: int = Field(..., ge=1)
    expected_cadence_seconds: int = Field(..., gt=0)
    cadence_consistent: bool
    missing_sample_count: int = Field(..., ge=0)


class HorizonsLunarSiteSkySeries(BaseModel):
    source_type: Literal["official"] = "official"
    source_name: str = "NASA/JPL Horizons"
    observer_body_id: Literal["301"] = "301"
    longitude_east_deg: float
    latitude_deg: float
    altitude_km: float
    coordinate_frame: str
    time_system: Literal["UT"] = "UT"
    position_kind: Literal["airless_apparent_target_center"] = "airless_apparent_target_center"
    azimuth_convention: Literal["clockwise_from_north"] = "clockwise_from_north"
    elevation_reference: Literal["plane_perpendicular_to_local_zenith"] = (
        "plane_perpendicular_to_local_zenith"
    )
    samples: list[HorizonsLunarSkySample]
    request_summary: dict[str, str] = Field(default_factory=dict)
    target_coverage: dict[str, HorizonsTargetCoverage] = Field(default_factory=dict)
    visibility_analysis: HorizonsVisibilityAnalysis | None = None
    limitations: list[str] = Field(
        default_factory=lambda: [
            "Apparent angles are not Cartesian geometric vectors.",
            "Visibility intervals classify Sun/Earth target-center elevations against the ideal 0-degree horizon; they do not model Earth-disc visibility.",
            "Visibility analysis requires a positive whole-minute sampling step.",
            "APPARENT=AIRLESS is specified; Horizons documents refraction applicability for Earth-topocentric observers only.",
            "No terrain masking, Earth-disc visibility, communication-link availability, or power model is included.",
            "Observer coordinates are user-supplied and are not verified landing-site data.",
        ]
    )
