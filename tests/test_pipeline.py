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


HYDRO = ROOT / "data" / "interim" / "hydro"


@pytest.mark.skipif(not (HYDRO / "drainage.tif").exists(),
                    reason="run scripts/02_prepare_dem.py first")
def test_hydrology_outputs_consistent():
    """Plan §5.3: all hydrology rasters share one UTM grid; depths are >= 0."""
    import rasterio
    names = ["dem_hydro", "dem_breached", "flow_accum", "depression_depth", "drainage"]
    grids = set()
    for n in names:
        with rasterio.open(HYDRO / f"{n}.tif") as src:
            assert src.crs.to_string() == CFG["crs"]["processing"]
            assert src.res == (CFG["resolution_m"], CFG["resolution_m"])
            grids.add((src.transform, src.shape))
    assert len(grids) == 1
    with rasterio.open(HYDRO / "depression_depth.tif") as src:
        d = src.read(1, masked=True)
    assert d.min() >= 0


FACTORS = ROOT / "data" / "interim" / "factors"


@pytest.mark.skipif(not (FACTORS / "norm_hand.tif").exists(),
                    reason="run scripts/03_factors.py first")
def test_normalised_factors_in_range():
    """ToR §5 step 3: every factor is on a common 0-1 scale."""
    import rasterio
    for name in CFG["factors"]:
        with rasterio.open(FACTORS / f"norm_{name}.tif") as src:
            a = src.read(1, masked=True)
        assert a.count() > 0, name
        assert 0 <= a.min() and a.max() <= 1, name
