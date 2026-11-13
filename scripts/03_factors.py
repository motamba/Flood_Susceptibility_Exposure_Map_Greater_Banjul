"""Step 3: build and normalise the five susceptibility factor layers.

ToR §5 step 3 (factor layers, normalised to a common 0-1 scale).
Implementation Plan §5.2 (factors, directions), §5.3 (processing rules),
§11 Day 4 ("done when: five normalised factor rasters plus a panel figure").

Factors (all on the 30 m UTM grid from step 2)
-------
hand              Height Above Nearest Drainage: metres above the drainage cell
                  each cell flows to (drainage = streams + water + OSM waterways).
elevation         FABDEM elevation above sea level.
depression_depth  Depth of closed hollows (filled minus original DEM).
twi               Topographic Wetness Index ln(a / tan b), a = specific
                  contributing area (D-infinity), b = slope.
landcover         Runoff / wetness score per WorldCover class (config).

Normalisation: 1 = more susceptible. Water cells and cells outside the study
area are excluded (nodata).

Outputs: data/interim/factors/{raw,norm}_<factor>.tif
         data/processed/water_mask.gpkg  (water and wetland polygons for the map)
         outputs/tables/factor_stats.csv, outputs/tables/factor_correlation.csv
         outputs/figures/factors.png
"""
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
import whitebox
from rasterio.features import shapes
from rasterio.warp import Resampling, reproject
from shapely.geometry import shape

from flood.config import DATA_INTERIM, DATA_PROCESSED, DATA_RAW, FIGURES, TABLES, ensure_dirs, load_config

HYDRO = DATA_INTERIM / "hydro"
FACTORS = DATA_INTERIM / "factors"
NODATA = -9999.0


def read(path):
    with rasterio.open(path) as src:
        a = src.read(1).astype("float32")
        if src.nodata is not None:
            a[a == src.nodata] = np.nan
        return a, src.profile


def save(path, array, profile, dtype="float32", nodata=NODATA):
    path.parent.mkdir(parents=True, exist_ok=True)
    prof = profile.copy()
    prof.update(dtype=dtype, nodata=nodata, count=1, compress="deflate", tiled=True,
                blockxsize=256, blockysize=256)
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(np.where(np.isnan(array), nodata, array).astype(dtype) if dtype.startswith("float")
                  else array.astype(dtype), 1)


def normalise(raw, valid, spec, pct):
    """Scale to 0-1 (1 = more susceptible) following the factor's config."""
    if spec["scaling"] == "percentile":
        lo, hi = np.nanpercentile(raw[valid], pct)
    elif spec["scaling"] == "fixed":
        lo, hi = spec["range"]
    else:  # already 0-1
        lo, hi = 0.0, 1.0
    norm = np.clip((raw - lo) / (hi - lo), 0, 1)
    if spec["direction"] == "low":
        norm = 1 - norm
    return np.where(valid, norm, np.nan).astype("float32"), float(lo), float(hi)


def landcover_30m(cfg, profile):
    """WorldCover classes on the 30 m grid (majority) and the mean 0-1 score."""
    scores = {int(k): v for k, v in cfg["landcover_scores"].items()}
    with rasterio.open(DATA_RAW / "landcover" / "worldcover_2021_clip.tif") as src:
        lc = src.read(1)
        lut = np.full(256, np.nan, dtype="float32")
        for k, v in scores.items():
            lut[k] = v
        score10 = lut[lc]
        kw = dict(src_transform=src.transform, src_crs=src.crs,
                  dst_transform=profile["transform"], dst_crs=profile["crs"])
        shape_ = (profile["height"], profile["width"])
        cls = np.zeros(shape_, dtype="uint8")
        reproject(lc, cls, resampling=Resampling.mode, src_nodata=None, dst_nodata=0, **kw)
        score = np.full(shape_, np.nan, dtype="float32")
        reproject(score10, score, resampling=Resampling.average, src_nodata=np.nan,
                  dst_nodata=np.nan, **kw)
    return cls, score


def water_polygons(water, wetland, inside, profile, crs) -> gpd.GeoDataFrame:
    """Polygonise water (1) and wetland (2) inside the study area for the web map."""
    cat = np.zeros(water.shape, dtype="uint8")
    cat[wetland] = 2
    cat[water] = 1
    cat[~inside] = 0
    geoms = [(shape(g), int(v)) for g, v in shapes(cat, mask=cat > 0,
                                                    transform=profile["transform"])]
    gdf = gpd.GeoDataFrame({"type": [{1: "water", 2: "wetland"}[v] for _, v in geoms]},
                           geometry=[g for g, _ in geoms], crs=crs)
    gdf = gdf.dissolve(by="type", as_index=False)
    gdf["geometry"] = gdf.simplify(15)   # metres; keeps the web file small (Plan §9)
    gdf["area_km2"] = (gdf.area / 1e6).round(2)
    return gdf


def main() -> None:
    cfg = load_config()
    ensure_dirs()
    crs = cfg["crs"]["processing"]
    pct = cfg["normalisation_percentiles"]
    wbt = whitebox.WhiteboxTools()
    wbt.set_verbose_mode(False)

    dem, profile = read(HYDRO / "dem_fabdem_utm.tif")
    dem_hydro, _ = read(HYDRO / "dem_hydro.tif")
    water = read(HYDRO / "water_mask.tif")[0] == 1
    inside = read(HYDRO / "inside_study.tif")[0] == 1
    valid = inside & ~water & ~np.isnan(dem_hydro)       # land cells in the study area

    raw = {}

    # HAND. The breached DEM from step 2 already gives every land cell a
    # downhill path to water. Water cells are set to a constant below all land
    # so they act as drainage (HAND = 0) without breaching the large flat sea,
    # which is extremely slow.
    breached, _ = read(HYDRO / "dem_breached.tif")
    floor = float(np.nanmin(breached)) - 1.0
    dem_hand = np.where(water & ~np.isnan(dem), floor, breached)
    save(FACTORS / "dem_for_hand.tif", dem_hand, profile)
    wbt.elevation_above_stream(str(FACTORS / "dem_for_hand.tif"),
                               str(HYDRO / "drainage.tif"), str(FACTORS / "hand_wbt.tif"))
    hand, _ = read(FACTORS / "hand_wbt.tif")
    raw["hand"] = np.clip(hand, 0, None)
    missing = np.isnan(raw["hand"]) & valid
    print(f"HAND: median {np.nanmedian(raw['hand'][valid]):.1f} m; "
          f"{missing.mean() / valid.mean():.2%} of land cells without a value")

    # Elevation.
    raw["elevation"] = dem

    # Depression depth (from step 2).
    raw["depression_depth"], _ = read(HYDRO / "depression_depth.tif")

    # TWI with D-infinity specific contributing area (better than D8 on flat,
    # dispersive terrain) and a minimum slope to avoid dividing by zero.
    wbt.d_inf_flow_accumulation(str(HYDRO / "dem_breached.tif"), str(FACTORS / "sca.tif"),
                                out_type="Specific Contributing Area")
    wbt.slope(str(HYDRO / "dem_hydro.tif"), str(FACTORS / "slope_deg.tif"), units="degrees")
    sca, _ = read(FACTORS / "sca.tif")
    slope, _ = read(FACTORS / "slope_deg.tif")
    tan_b = np.tan(np.radians(np.maximum(slope, cfg["min_slope_deg"])))
    raw["twi"] = np.log(sca / tan_b)
    print(f"Slope: median {np.nanmedian(slope[valid]):.2f} deg, "
          f"{np.nanmean(slope[valid] < 1):.0%} of land cells flatter than 1 deg")

    # Land cover.
    lc_class, raw["landcover"] = landcover_30m(cfg, profile)
    wetland = np.isin(lc_class, cfg["wetland_classes"]) & ~water

    # Normalise and save.
    stats, norm = [], {}
    for name, spec in cfg["factors"].items():
        r = raw[name]
        v = valid & ~np.isnan(r)
        norm[name], lo, hi = normalise(r, v, spec, pct)
        save(FACTORS / f"raw_{name}.tif", np.where(v, r, np.nan), profile)
        save(FACTORS / f"norm_{name}.tif", norm[name], profile)
        stats.append({"factor": name, "direction": spec["direction"], "scaling": spec["scaling"],
                      "scale_low": round(lo, 3), "scale_high": round(hi, 3),
                      "raw_min": round(float(np.nanmin(r[v])), 3),
                      "raw_median": round(float(np.nanmedian(r[v])), 3),
                      "raw_max": round(float(np.nanmax(r[v])), 3),
                      "norm_mean": round(float(np.nanmean(norm[name][v])), 3),
                      "coverage": round(float(v.sum() / valid.sum()), 4)})
    stats = pd.DataFrame(stats)
    stats.to_csv(TABLES / "factor_stats.csv", index=False)
    print(f"\n{stats.to_string(index=False)}")

    # Correlation between normalised factors: shows overlap between factors
    # (e.g. HAND and elevation in flat coastal land).
    both = valid & np.all([~np.isnan(n) for n in norm.values()], axis=0)
    corr = pd.DataFrame({k: v[both] for k, v in norm.items()}).corr(method="spearman").round(2)
    corr.to_csv(TABLES / "factor_correlation.csv")
    print(f"\nSpearman correlation of normalised factors:\n{corr.to_string()}")

    # Water and wetland polygons for the web map (Plan §9 water layer).
    wp = water_polygons(water, wetland, inside, profile, crs)
    out = DATA_PROCESSED / "water_mask.gpkg"
    out.unlink(missing_ok=True)
    wp.to_file(out, driver="GPKG")
    print(f"\nWater/wetland polygons: {wp[['type', 'area_km2']].to_dict('records')}")

    # Panel figure (Plan §11 Day 4).
    study = gpd.read_file(DATA_PROCESSED / "study_area.gpkg", layer="study_area")
    t = profile["transform"]
    ext = (t.c, t.c + profile["width"] * t.a, t.f + profile["height"] * t.e, t.f)
    titles = {"hand": "HAND (low = susceptible)", "elevation": "Elevation (low = susceptible)",
              "depression_depth": "Depression depth (0-1 m)", "twi": "Wetness index (TWI)",
              "landcover": "Land-cover score"}
    fig, axes = plt.subplots(2, 3, figsize=(15, 10), layout="constrained")
    for ax, (name, title) in zip(axes.flat, titles.items()):
        im = ax.imshow(norm[name], extent=ext, cmap="YlOrRd", vmin=0, vmax=1)
        w = cfg["factors"][name]["weight"]
        ax.set_title(f"{title}\nweight {w:.2f}", fontsize=10)
    ax = axes.flat[5]
    lc_show = np.where(inside, lc_class, 0).astype(float)
    lc_show[lc_show == 0] = np.nan
    from matplotlib.colors import BoundaryNorm, ListedColormap
    classes = [10, 20, 30, 40, 50, 60, 80, 90, 95]
    colours = ["#006400", "#ffbb22", "#ffff4c", "#f096ff", "#fa0000", "#b4b4b4",
               "#0064c8", "#0096a0", "#00cf75"]   # official WorldCover palette
    ax.imshow(lc_show, extent=ext, interpolation="nearest",
              cmap=ListedColormap(colours), norm=BoundaryNorm(classes + [100], len(classes)))
    ax.set_title("WorldCover 2021 (majority per 30 m cell)", fontsize=10)
    for a in axes.flat:
        study.boundary.plot(ax=a, color="black", linewidth=0.6)
        a.set_xticks([])
        a.set_yticks([])
    fig.colorbar(im, ax=axes, location="bottom", shrink=0.4, aspect=40,
                 label="normalised factor (1 = more susceptible); last panel: land-cover classes")
    fig.suptitle("Susceptibility factors, normalised to 0-1 (water excluded)")
    fig.savefig(FIGURES / "factors.png", dpi=120, bbox_inches="tight")


if __name__ == "__main__":
    main()
