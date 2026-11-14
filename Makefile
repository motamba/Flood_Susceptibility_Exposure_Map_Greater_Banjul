# Reproducible pipeline entry point (Implementation Plan §3, §10; ToR §9).
# Usage (inside the flood-banjul environment):
#   make all     run every implemented step in order
#   make test    run sanity checks
# Steps are added to STEPS as they are implemented (Plan §11).

PY := python
export PYTHONPATH := src

STEPS := 00_study_area 01_download_data 02_prepare_dem 03_factors 04_overlay_classify 06_exposure 05_sensitivity 08_plausibility 09_export_web

.PHONY: all test serve clean-interim $(STEPS)

all: $(STEPS)

$(STEPS):
	$(PY) scripts/$@.py

test:
	$(PY) -m pytest -q

# Preview the web map locally at http://localhost:8000
serve:
	$(PY) -m http.server 8000 --directory docs

clean-interim:
	rm -rf data/interim/*
