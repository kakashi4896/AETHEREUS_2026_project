# Backend directory

This directory contains the minimal FastAPI backend for the CLPS Lunar Mission Browser project.

Current status: Phase 1 runtime skeleton is in place.

## Local startup

From the repository root:

```bash
python -m pip install -e backend
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

If port 8000 is occupied, use a different port such as 8001.

Then open or test:

- http://127.0.0.1:8000/
- http://127.0.0.1:8000/api/health
- http://127.0.0.1:8000/api/sites
- http://127.0.0.1:8000/api/analysis?site_id=demo-north-polar&start_date=2026-01-01&end_date=2026-01-02&step_minutes=60
- Direct upstream NASA/JPL Horizons call:
  `curl -s "https://ssd.jpl.nasa.gov/api/horizons.api?format=json&COMMAND='10'&MAKE_EPHEM='YES'&EPHEM_TYPE='OBSERVER'&CENTER='500@399'&TIME_TYPE='UT'&START_TIME='2026-01-01'&STOP_TIME='2026-01-02'&STEP_SIZE='1h'"`
- Pretty-printed upstream Horizons response:
  `curl -s "https://ssd.jpl.nasa.gov/api/horizons.api?format=json&COMMAND='10'&MAKE_EPHEM='YES'&EPHEM_TYPE='OBSERVER'&CENTER='500@399'&TIME_TYPE='UT'&START_TIME='2026-01-01'&STOP_TIME='2026-01-02'&STEP_SIZE='1h'" | python -m json.tool`

## Tests

```bash
cd backend
python -m pytest
```

This backend is intentionally minimal and uses synthetic/demo content only; it does not yet calculate scientific lighting or communications visibility from official sources.
