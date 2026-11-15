# Lunar Landing Site Navigator

A browser-based prototype for exploring Sun/Earth geometry at selected lunar demo locations.

The application retrieves Sun and Earth apparent azimuth and elevation samples from NASA/JPL Horizons for predefined lunar Demo Sites. It aligns the observations by timestamp and visualizes the sampled results relative to an ideal `0°` horizon. It also provides a local Synthetic Demo mode and multi-site comparison.

> **Scope Disclaimer**
>
> This project is a visualization prototype, not an official NASA mission-planning tool or a landing-site selection system. The built-in North, South, and Equatorial Demo Sites use synthetic test coordinates. They are not officially designated NASA or CLPS landing-site candidates.
>
> The displayed angles represent the apparent directions of the centers of the Sun and Earth. The ideal horizon does not account for lunar terrain obstruction.

## Features

- **Live Horizons Queries:** Retrieve Q4 apparent azimuth and elevation for the Sun (`10`) and Earth (`399`) using a lunar geodetic observer.
- **Time-Series Analysis:** Align Sun and Earth samples by timestamp, preserve missing values, and display sample coverage, angles, and sample-based intervals.
- **Two Data Modes:** Use locally generated Synthetic Demo data or query the live NASA/JPL Horizons API when an internet connection is available.
- **Multi-site Comparison:** Compare selected Demo Sites using summary bars, tables, hourly grids, and sample timelines. Official multi-site comparison is implemented but has not received full acceptance.

## Requirements

- Python 3.11 or later
- Internet connection for Live Horizons queries
- No internet connection required for Synthetic Demo mode

## Quick Start (Windows)

Open PowerShell in the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .\backend
Set-Location .\backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open the application:

- App: http://127.0.0.1:8000/
- Health Check: http://127.0.0.1:8000/api/health

If port `8000` is already in use, change `--port 8000` to `--port 8001` and open http://127.0.0.1:8001/.

## Suggested Demo Workflow

1. Open the application and introduce it as a lunar geometry visualization prototype. Explain that the Demo Site coordinates are test coordinates, not official landing sites.
2. Select **Demo North Polar Site** in the single-site timeline.
3. Choose a date and a sampling interval of 60 minutes.
4. Select **Official API / NASA/JPL Horizons** and load the timeline.
5. Show the data source, observer coordinates, UT timestamps, Sun/Earth center angles, sample coverage, and ideal horizon classification.
6. Explain that `center elevation > 0°` only means the target center is above the ideal horizontal horizon. It does not establish full-disc visibility, terrain clearance, communication availability, or power generation.
7. Open Multi-site Comparison and select North/South to demonstrate the comparison views. Both locations still use synthetic observer coordinates, and official comparison has not received full acceptance.
8. Switch to **Synthetic** mode to demonstrate the locally generated model and distinguish it from live Horizons data.
9. If demonstrating Error or Empty states, identify them as simulated test states where applicable. Do not describe mocked responses as actual NASA Live API failures.

## NASA Data Sources and API

### NASA/JPL Horizons System

This project uses the **NASA/JPL Horizons System**, through its API v2 interface:

https://ssd.jpl.nasa.gov/horizons/

The single-site lunar observer query uses the following configuration:

| Parameter | Configuration |
|---|---|
| Sun target | NAIF ID `10` |
| Earth target | NAIF ID `399` |
| Moon observer body | NAIF ID `301` |
| Observer center | `coord@301` |
| Coordinate type | `GEODETIC` |
| Quantities | Q4 (`QUANTITIES=4`) |
| Data | Apparent azimuth and elevation |
| Time type | `UT` |
| Apparent setting | `AIRLESS` |

The application sends separate requests for the Sun and Earth, parses the returned Q4 data, and joins the samples by timestamp.

**Observer coordinate limitation:** Horizons is queried using the predefined coordinates of the project's synthetic Demo Sites. The use of NASA/JPL Horizons data does not make these locations official NASA or CLPS landing-site candidates.

### Data Modes and Endpoints

| Mode / Endpoint | Description |
|---|---|
| `GET /api/analysis/horizons` | Main single-site lunar observer workflow using live Horizons data. Queries the Sun and Earth separately using `coord@301` / `GEODETIC`. Upstream failures are reported rather than silently replaced with Synthetic data. |
| `GET /api/comparison?source=official` | Queries Horizons for each selected Demo Site. Implemented, but only partially accepted. |
| `GET /api/analysis?source=synthetic` | Locally generated simplified Synthetic model. It is not NASA ephemeris data. |
| `GET /api/analysis?source=reference` | Locally generated reference-labelled analysis. It is not an official NASA dataset. |
| `GET /api/reference` | Locally generated reference payload based on simplified formulas. |
| `GET /api/analysis?source=official` | Legacy compatibility route. Its primary analysis remains synthetic site geometry and may include an additional Earth-centered ephemeris sidecar. It is not equivalent to `/api/analysis/horizons`. |

**Data provenance**

- **Live Horizons data:** Retrieved directly from NASA/JPL at runtime.
- **Synthetic Demo data:** Generated locally by simplified formulas in the application.
- **Local Generated Reference:** Generated locally; it is not an independent NASA reference dataset.
- **Fixtures:** Saved response samples used for offline parser and regression tests.
- **Mocks:** Used in automated tests or simulated UI states. They do not represent live NASA responses.

All time values retain the Horizons `UT` time-scale semantics and should not be relabelled as UTC without an explicit time conversion.

## Technology Stack

- **Backend:** Python, FastAPI, Pydantic, httpx
- **Frontend:** HTML, CSS, JavaScript
- **External Data Source:** NASA/JPL Horizons API v2
- **Testing:** pytest and JavaScript syntax checks

## Project Structure

```text
.
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── models.py
│   │   ├── official_data.py
│   │   ├── synthetic_data.py
│   │   ├── geometry.py
│   │   └── reference_data.py
│   └── tests/
├── frontend/
│   ├── index.html
│   └── static/
│       ├── app.js
│       └── styles.css
└── data/
    ├── reference/
    └── manifests/
        └── datasets.json
```

## Validation

The following results were recorded during development on November 15, 2026. They are historical development records and were not re-executed during final documentation preparation.

| Validation | Recorded Result |
|---|---|
| Backend test suite | 112 passed, 3 skipped, 1 warning |
| Horizons adapter tests | 38 passed, 1 warning |
| JavaScript syntax check | Passed |
| Manual Live Browser verification | 25 aligned samples; 25 valid Sun and 25 valid Earth elevation samples |

The three skipped backend tests require optional external SPICE kernels. The recorded browser verification was performed manually and was not an automated end-to-end test.

## Scientific Interpretation and Limitations

- Horizons Q4 provides apparent azimuth and elevation for the centers of the Sun and Earth.
- An elevation greater than `0°` indicates that the target center is above the ideal horizontal horizon.
- Sample-based intervals are derived from discrete samples and the selected sampling step. Crossing-time interpolation is not implemented; these intervals are not precise event times.
- Sample counts and fractions do not represent guaranteed continuous visibility, available solar energy, or successful communications.
- The ideal `0°` horizon does not account for lunar terrain, local obstructions, or equipment constraints.
- Full-disc visibility of the Sun or Earth is not calculated.
- The project does not currently include DEM processing, terrain-horizon analysis, power-generation models, or communication-link analysis.
- The Demo Sites are synthetic test locations and must not be interpreted as validated mission sites.

## AI / Super Intelligence Usage Disclosure

AI-assisted tools were used during development as supporting tools, not as substitutes for the project's technical design decisions or validation.

**Code and Development**
- AI assistance was used to support code drafting, code review, debugging, and implementation guidance.
- Generated or suggested code was reviewed and integrated into the project development workflow.
- Application behavior, API integration, and test results were checked against the implementation and recorded development evidence.

**Documentation**
- AI assistance was used to help organize, draft, and refine project documentation and technical explanations.
- The final documentation was reviewed against the implemented features and available development records.

**Data and Scientific Results**
- Live Sun/Earth observations are retrieved directly from NASA/JPL Horizons at runtime; they are not AI-generated.
- Synthetic Demo data is produced by deterministic local formulas in the application, not presented as NASA observations.
- Saved Horizons fixtures preserve previously retrieved responses for offline testing; they are not newly generated AI data.

**Images, Video, and Audio**
- The application functionality does not depend on AI-generated image, video, or audio content.
- Any AI-generated media included in separately submitted presentation materials must be visibly identified as AI-generated.

AI tools were used to assist implementation and communication. The project’s data-source selection, application integration, interpretation of results, and validation scope remain explicitly documented.

## License and Attribution

NASA/JPL Horizons is acknowledged as the external source of live ephemeris data used by this prototype.

Refer to the NASA/JPL Horizons website for information about its data services and applicable usage guidance:

https://ssd.jpl.nasa.gov/horizons/