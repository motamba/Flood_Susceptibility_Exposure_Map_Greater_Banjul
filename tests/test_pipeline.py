"""Sanity checks (Implementation Plan §10). Tests are added as each step lands."""
from pathlib import Path

import pytest

from flood.config import DATA_PROCESSED, MANIFEST, ROOT, active_weights, load_config

CFG = load_config()


def test_weights_sum_to_one():
    assert sum(active_weights(CFG).values()) == pytest.approx(1.0)


def test_weights_sum_to_one_without_landcover():
    cfg = {**CFG, "use_landcover": False}
    w = active_weights(cfg)
    assert "landcover" not in w
    assert sum(w.values()) == pytest.approx(1.0)


def test_factor_directions_valid():
    assert all(f["direction"] in {"low", "high"} for f in CFG["factors"].values())


@pytest.mark.skipif(not MANIFEST.exists(), reason="run scripts/01_download_data.py first")
def test_manifest_complete():
    """ToR §9: every raw file has a source, licence, date and checksum."""
    import csv
    with open(MANIFEST, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows
    for r in rows:
        for field in ("url", "source", "licence", "downloaded_utc", "sha256"):
            assert r[field], f"{r['file']} is missing {field}"
        assert (ROOT / r["file"]).exists(), f"{r['file']} listed but not on disk"


@pytest.mark.skipif(not (DATA_PROCESSED / "study_area.gpkg").exists(),
                    reason="run scripts/00_study_area.py first")
def test_study_area_crs_and_validity():
    import geopandas as gpd
    sa = gpd.read_file(DATA_PROCESSED / "study_area.gpkg", layer="study_area")
    assert sa.crs.to_string() == CFG["crs"]["processing"]
    assert len(sa) == 1 and sa.is_valid.all()
    assert 50 < sa.area.iloc[0] / 1e6 < 400  # plausible size in km2
