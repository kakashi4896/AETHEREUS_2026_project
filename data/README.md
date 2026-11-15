# Data directory

This directory is reserved for project data and demonstration datasets.

## Contents

- `sample/`: synthetic or fixture data used during local development and demos.
- `reference/`: local copies of verified reference outputs or saved test fixtures.
- `cache/`: optional downloaded files and caches; keep large artifacts out of Git unless explicitly approved.

`reference/horizons_lunar_site_az_el_2026-11-10.json` contains extracted NASA/JPL Horizons Sun/Earth observer-table rows for a test-only lunar geodetic coordinate `(0°, 0°, 0 km)`. It is a provider/parser reference, not a CLPS landing site or a full independent scientific validation.

`reference/spice_horizons_lunar_site_comparison_2026-11-10.json` records an independent NAIF SPICE calculation against that same Horizons fixture, including frame/aberration settings, kernel URLs and checksums, outputs, tolerance, and limitations. It is a computed reference for the test coordinate only; kernel binaries are not stored in this repository.

`reference/spice_horizons_lunar_site_matrix_2026-11-10.json` adds live Horizons excerpts at four software-test coordinates, including a high southern latitude, nonzero site elevations, and three dates. The optional SPICE test compares 24 target/time samples with that reference matrix; all coordinates remain test-only and are not CLPS locations.

## Rule

Anything placed here must be labeled as either `synthetic`, `reference`, or `official`, with a clear source and verification status. Do not mix unverified data with official results.
