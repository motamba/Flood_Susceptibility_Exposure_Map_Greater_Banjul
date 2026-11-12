# Flood Susceptibility and Exposure Web Map for Greater Banjul

> **Work in progress** (personal project, November 2026). The full README follows on Day 10 of the work plan.

> **Disclaimer:** this project produces a *susceptibility index*, not a validated flood forecast. It is for illustration and learning, and must not be used for planning or safety decisions.

## Purpose

This repository identifies areas in Greater Banjul (The Gambia) that are most likely to flood, based on terrain and land cover. It then counts the buildings, roads, schools and health facilities in those areas. The results are published as an interactive web map. Everything is built from open data with a reproducible Python pipeline.

## Study area

Greater Banjul is defined as Banjul City Council, Kanifing Municipal Council and Kombo North district. The polygon is dissolved from geoBoundaries ADM2 units (CC BY 4.0). See `scripts/00_study_area.py`.

The ADM2 units leave out the Tanbi Wetland between Banjul and Kanifing. That wetland floods and carries the only road link to Banjul, so it is added back with a 3 km morphological closing (buffer out, then back in). Land belonging to other districts is subtracted afterwards. This adds 26.7 km², giving a study area of 273.4 km². Open water picked up this way is removed later by the water mask.

![Study area](outputs/figures/study_area.png)

## Reproduce

```bash
# 1. Install micromamba (https://mamba.readthedocs.io), then:
micromamba env create -f environment.yml
micromamba activate flood-banjul

# 2. Run the pipeline and the tests
make all
make test
```

Raw data is downloaded by the scripts and is not stored in Git. Every downloaded file is listed with its URL, date, licence and checksum in [`data/raw/MANIFEST.csv`](data/raw/MANIFEST.csv).

## Status

| Step | Script | Status |
|---|---|---|
| 0 Study area | `00_study_area.py` | done |
| 1 Data download | `01_download_data.py` | planned (Day 2) |
| 2 DEM preparation | `02_prepare_dem.py` | planned |
| 3 Factor layers | `03_factors.py` | planned |
| 4 Overlay and classes | `04_overlay_classify.py` | planned |
| 5 Sensitivity | `05_sensitivity.py` | planned |
| 6 Exposure | `06_exposure.py` | planned |
| 7 Rainfall | `07_rainfall.py` | planned |
| 8 Plausibility | `08_plausibility.py` | planned |
| 9 Web export and map | `09_export_web.py`, `docs/` | map shell working; layers added as steps finish |

Preview the map locally with `make serve`, then open http://localhost:8000.

## Licence

Code: MIT (see [LICENSE](LICENSE)). Data: each source keeps its own licence (see `data/raw/MANIFEST.csv`). Map data © OpenStreetMap contributors (ODbL).
