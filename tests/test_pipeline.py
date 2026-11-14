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


def test_weighted_jenks_matches_mapclassify():
    """Plan §5.4: the fast weighted Jenks gives the same breaks as the reference."""
    import mapclassify
    import numpy as np
    from flood.model import jenks_weighted
    rng = np.random.default_rng(1)
    x = np.round(np.concatenate([rng.normal(0.3, .05, 300), rng.normal(0.6, .05, 200),
                                 rng.normal(0.85, .03, 100)]), 2)
    u, c = np.unique(x, return_counts=True)
    ours = [u[e] for e in jenks_weighted(u, c.astype(float), 3)]
    ref = list(mapclassify.FisherJenks(x, k=3).bins[:-1])
    assert np.allclose(ours, ref)


@pytest.mark.skipif(not (DATA_PROCESSED / "susceptibility_classes.tif").exists(),
                    reason="run scripts/04_overlay_classify.py first")
def test_susceptibility_outputs():
    """ToR §5 steps 4 and 6: index in 0-1; three classes; web file in budget."""
    import rasterio
    with rasterio.open(DATA_PROCESSED / "susceptibility_index.tif") as src:
        idx = src.read(1, masked=True)
    assert 0 <= idx.min() and idx.max() <= 1
    with rasterio.open(DATA_PROCESSED / "susceptibility_classes.tif") as src:
        cls = src.read(1, masked=True)
    assert set(cls.compressed().tolist()) == {1, 2, 3}
    web = ROOT / "docs" / "data"
    total = sum(p.stat().st_size for p in web.glob("*.geojson"))
    assert total < 5 * 1024 * 1024, "web data over the 5 MB budget (Plan §9)"


TABLES = ROOT / "outputs" / "tables"
DATA_INTERIM_EXPOSURE = ROOT / "data" / "interim" / "exposure"


@pytest.mark.skipif(not (TABLES / "exposure.csv").exists(), reason="run scripts/06_exposure.py first")
def test_exposure_table_consistent():
    """ToR §5 step 7: per-class counts add up to the total; shares to 100%."""
    import numpy as np
    import pandas as pd
    t = pd.read_csv(TABLES / "exposure.csv")
    assert {"Buildings", "Roads (all)", "Schools", "Health facilities"} <= set(t.asset)
    classes = CFG["classification"]["labels"] + ["Unclassified"]
    assert np.allclose(t[classes].sum(axis=1), t["Total"], atol=0.5)
    assert np.allclose(t[[f"{c}_pct" for c in classes]].sum(axis=1), 100, atol=0.5)
    roads = t.set_index("asset")["Total"]
    assert roads["Roads (vehicle)"] <= roads["Roads (all)"]
    cells = DATA_INTERIM_EXPOSURE / "building_cells.npy"
    if cells.exists():
        assert int(np.load(cells).sum()) == int(roads["Buildings"])


@pytest.mark.skipif(not (DATA_PROCESSED / "reported_floods.csv").exists(),
                    reason="run scripts/08_plausibility.py first")
def test_reported_floods_sourced_and_inside():
    """Plan §8: every point has a source URL and a class, and lies in the study area."""
    import geopandas as gpd
    pts = gpd.read_file(DATA_PROCESSED / "reported_floods.gpkg")
    study = gpd.read_file(DATA_PROCESSED / "study_area.gpkg", layer="study_area").union_all()
    assert pts.source_url.str.startswith("https://").all()
    assert pts["class"].isin(CFG["classification"]["labels"]).all()
    assert pts.within(study).all()
