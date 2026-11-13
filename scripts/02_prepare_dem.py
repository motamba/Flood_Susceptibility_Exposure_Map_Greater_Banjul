"""Step 2: prepare the DEM and derive the hydrological base layers.

ToR §5 step 2 (clip DEM, fill sinks, slope, flow direction, flow accumulation).
Implementation Plan §5.3 (breach, don't fill, for routing; depression depth),
§4 (UTM 28N, 30 m, 2 km buffer, stream threshold), §11 Day 3
("done when streams match OSM waterways reasonably").

Method
------
1. Reproject FABDEM (and Copernicus, for later comparison) to a 30 m UTM 28N
   grid covering the hydrology buffer.
2. Water mask from ESA WorldCover (FABDEM stores the sea as 0 m, so the DEM
   cannot be used). Water and cells outside the buffer become nodata, so flow
   drains into the sea, the river and the wetlands channels.
3. Breach depressions (least cost) for flow routing; flow that cannot be
   breached within the maximum distance is filled.
4. Fill depressions separately to measure depression depth (filled minus
   original). In flat urban Banjul, depressions are where rain water ponds,
   so they are kept as a susceptibility factor rather than removed.
5. D8 flow pointer and flow accumulation on the breached DEM.
6. Streams for several thresholds, compared with OSM waterways to choose one.

Outputs: data/interim/hydro/*.tif, outputs/tables/stream_threshold_tuning.csv,
         outputs/figures/dem_hydrology.png
"""
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
import whitebox
from matplotlib.colors import ListedColormap
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
from scipy.ndimage import distance_transform_edt

from flood.config import DATA_INTERIM, DATA_PROCESSED, DATA_RAW, FIGURES, TABLES, ensure_dirs, load_config

HYDRO = DATA_INTERIM / "hydro"
NODATA = -9999.0


def target_grid(buffer: gpd.GeoDataFrame, res: float):
    """30 m grid snapped to whole metres around the hydrology buffer."""
    x0, y0, x1, y1 = buffer.total_bounds
    x0, y0 = np.floor(x0 / res) * res, np.floor(y0 / res) * res
    x1, y1 = np.ceil(x1 / res) * res, np.ceil(y1 / res) * res
    width, height = int((x1 - x0) / res), int((y1 - y0) / res)
    return from_origin(x0, y1, res, res), width, height


def warp(src_path, transform, width, height, crs, resampling, src_values=None):
    """Reproject a raster (or an array derived from it) onto the target grid."""
    with rasterio.open(src_path) as src:
        data = src.read(1) if src_values is None else src_values(src.read(1))
        # A derived array (e.g. 0/1 water) must not inherit the source nodata:
        # WorldCover declares 0 as nodata, which would drop every "not water" cell.
        src_nodata = src.nodata if src_values is None else None
        out = np.full((height, width), NODATA, dtype="float32")
        reproject(data.astype("float32"), out, src_transform=src.transform, src_crs=src.crs,
                  src_nodata=src_nodata, dst_transform=transform, dst_crs=crs,
                  dst_nodata=NODATA, resampling=resampling)
    return out


def save(path, array, transform, crs, dtype="float32", nodata=NODATA):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=array.shape[0], width=array.shape[1],
                       count=1, dtype=dtype, crs=crs, transform=transform, nodata=nodata,
                       compress="deflate", tiled=True) as dst:
        dst.write(array.astype(dtype), 1)


def read(path):
    with rasterio.open(path) as src:
        a = src.read(1).astype("float32")
        a[a == src.nodata] = np.nan
    return a


def osm_waterways(cfg, study_utm) -> gpd.GeoDataFrame:
    """OSM waterway lines (Plan §6), clipped to the study area."""
    kinds = cfg["hydrology"]["osm_waterways"]
    where = "waterway IN (" + ",".join(f"'{k}'" for k in kinds) + ")"
    ww = gpd.read_file(DATA_INTERIM / "osm" / "greater_banjul.osm.pbf", layer="lines",
                       where=where, columns=["osm_id", "name", "waterway"])
    return gpd.clip(ww.to_crs(study_utm.crs), study_utm)


def agreement(streams, osm_mask, inside, res, tol):
    """Cell-based agreement between derived streams and OSM waterways.

    recall    = share of OSM waterway cells within tol of a derived stream
    precision = share of derived stream cells within tol of an OSM waterway
    """
    s = (streams > 0) & inside
    d_to_stream = distance_transform_edt(~s) * res
    d_to_osm = distance_transform_edt(~osm_mask) * res
    o = osm_mask & inside
    recall = float((d_to_stream[o] <= tol).mean()) if o.any() else np.nan
    precision = float((d_to_osm[s] <= tol).mean()) if s.any() else np.nan
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    length_km = s.sum() * res / 1000  # rough: one cell length per stream cell
    return recall, precision, f1, length_km


def main() -> None:
    cfg = load_config()
    ensure_dirs()
    h = cfg["hydrology"]
    res = cfg["resolution_m"]
    crs = cfg["crs"]["processing"]
    gpkg = DATA_PROCESSED / "study_area.gpkg"
    buffer = gpd.read_file(gpkg, layer="hydrology_buffer")
    study = gpd.read_file(gpkg, layer="study_area")
    transform, width, height = target_grid(buffer, res)
    print(f"Grid: {width} x {height} cells at {res} m ({crs})")

    # 1. DEMs on the analysis grid (bilinear for continuous heights).
    dem = warp(DATA_RAW / "dem" / "fabdem_clip.tif", transform, width, height, crs,
               Resampling.bilinear)
    cop = warp(DATA_RAW / "dem" / "copernicus_glo30_clip.tif", transform, width, height, crs,
               Resampling.bilinear)
    save(HYDRO / "dem_fabdem_utm.tif", dem, transform, crs)
    save(HYDRO / "dem_copernicus_utm.tif", cop, transform, crs)

    # 2. Water fraction: share of 10 m WorldCover cells that are water.
    frac = warp(DATA_RAW / "landcover" / "worldcover_2021_clip.tif", transform, width, height,
                crs, Resampling.average,
                src_values=lambda a: (a == h["water_class"]).astype("float32"))
    water = frac >= h["water_min_fraction"]
    inside_buffer = rasterize([(g, 1) for g in buffer.geometry], out_shape=(height, width),
                              transform=transform, fill=0).astype(bool)
    inside_study = rasterize([(g, 1) for g in study.geometry], out_shape=(height, width),
                             transform=transform, fill=0).astype(bool)
    save(HYDRO / "water_mask.tif", water.astype("uint8"), transform, crs, "uint8", 255)
    save(HYDRO / "inside_study.tif", inside_study.astype("uint8"), transform, crs, "uint8", 255)

    dem_h = dem.copy()
    dem_h[water | ~inside_buffer | (dem == NODATA)] = NODATA
    save(HYDRO / "dem_hydro.tif", dem_h, transform, crs)
    land = dem_h != NODATA
    print(f"Water cells: {water[inside_study].mean():.1%} of study area")

    # 3-5. WhiteboxTools hydrology (Plan §5.3).
    wbt = whitebox.WhiteboxTools()
    wbt.set_verbose_mode(False)
    p = {k: str(HYDRO / f"{k}.tif") for k in
         ("dem_hydro", "dem_breached", "dem_filled", "d8_pointer", "flow_accum")}
    wbt.breach_depressions_least_cost(p["dem_hydro"], p["dem_breached"],
                                      dist=h["breach_max_dist_cells"], fill=True)
    wbt.fill_depressions(p["dem_hydro"], p["dem_filled"], fix_flats=False)
    wbt.d8_pointer(p["dem_breached"], p["d8_pointer"])
    wbt.d8_flow_accumulation(p["dem_breached"], p["flow_accum"], out_type="cells")

    depth = read(p["dem_filled"]) - read(p["dem_hydro"])
    depth = np.where(land, np.clip(depth, 0, None), np.nan)
    save(HYDRO / "depression_depth.tif", np.nan_to_num(depth, nan=NODATA), transform, crs)
    print(f"Depression depth: {np.nanmean(depth > 0.1):.1%} of land cells > 0.1 m deep, "
          f"max {np.nanmax(depth):.1f} m")

    # 6. Stream threshold tuning against OSM waterways (Plan §11 Day 3).
    ww = osm_waterways(cfg, study)
    osm_mask = rasterize([(g, 1) for g in ww.geometry], out_shape=(height, width),
                         transform=transform, fill=0, all_touched=True).astype(bool)
    print(f"OSM waterways in study area: {len(ww)} lines, {ww.length.sum() / 1000:.1f} km "
          f"({ww.waterway.value_counts().to_dict()})")
    rows = []
    for t in h["threshold_candidates"]:
        out = HYDRO / f"streams_{t}.tif"
        wbt.extract_streams(p["flow_accum"], str(out), threshold=t)
        r, pr, f1, km = agreement(np.nan_to_num(read(out)), osm_mask, inside_study & land,
                                  res, h["agreement_tolerance_m"])
        rows.append({"threshold_cells": t, "catchment_km2": t * res * res / 1e6,
                     "stream_km": round(km, 1), "recall_osm": round(r, 3),
                     "precision_osm": round(pr, 3), "f1": round(f1, 3)})
    tuning = pd.DataFrame(rows)
    tuning.to_csv(TABLES / "stream_threshold_tuning.csv", index=False)
    print("\nStream threshold tuning (tolerance "
          f"{h['agreement_tolerance_m']} m):\n{tuning.to_string(index=False)}")
    chosen = h["stream_threshold_cells"]
    streams = np.nan_to_num(read(HYDRO / f"streams_{chosen}.tif")) > 0
    save(HYDRO / "streams.tif", streams.astype("uint8"), transform, crs, "uint8", 255)
    print(f"Using threshold {chosen} cells (config)")

    # Drainage network for HAND (Plan §5.2): derived streams + open water +
    # OSM waterways. OSM adds tidal creeks and man-made drains, which a 30 m
    # DEM cannot resolve but which do carry water away.
    drainage = streams | water | osm_mask
    save(HYDRO / "drainage.tif", drainage.astype("uint8"), transform, crs, "uint8", 255)
    print(f"Drainage network: {drainage[inside_study].mean():.1%} of study-area cells "
          f"(streams {streams[inside_study].mean():.1%}, water {water[inside_study].mean():.1%}, "
          f"OSM {osm_mask[inside_study].mean():.1%})")

    # Figure for visual checking (Plan §10 "hydrology looks right").
    ext = (transform.c, transform.c + width * res, transform.f - height * res, transform.f)
    fig, axes = plt.subplots(2, 2, figsize=(12, 10.5))
    show = np.where(land, dem, np.nan)
    ax = axes[0, 0]
    im = ax.imshow(show, extent=ext, cmap="terrain", vmin=0, vmax=35)
    fig.colorbar(im, ax=ax, label="m", shrink=0.7)
    ax.set_title("FABDEM elevation (water masked)")
    ax = axes[0, 1]
    im = ax.imshow(np.where(depth > 0.05, depth, np.nan), extent=ext, cmap="Blues", vmin=0, vmax=3)
    fig.colorbar(im, ax=ax, label="m", shrink=0.7)
    ax.set_title("Depression depth (filled minus original)")
    ax = axes[1, 0]
    im = ax.imshow(np.where(land, np.log10(read(p["flow_accum"])), np.nan), extent=ext,
                   cmap="viridis")
    fig.colorbar(im, ax=ax, label="log10(cells)", shrink=0.7)
    ax.set_title("D8 flow accumulation (breached DEM)")
    ax = axes[1, 1]
    ax.imshow(np.where(water & inside_buffer, 1, np.nan), extent=ext,
              cmap=ListedColormap(["#c6dbef"]))
    ax.imshow(np.where(streams, 1, np.nan), extent=ext, cmap=ListedColormap(["#08519c"]),
              interpolation="nearest")
    ww.plot(ax=ax, color="#e31a1c", linewidth=0.8)
    ax.set_title(f"Derived streams ({chosen} cells, blue) vs OSM waterways (red)")
    for a in axes.flat:
        study.boundary.plot(ax=a, color="black", linewidth=0.7)
        a.set_xticks([])
        a.set_yticks([])
    fig.suptitle("Greater Banjul: DEM preparation and hydrology (FABDEM, 30 m, UTM 28N)")
    fig.tight_layout()
    fig.savefig(FIGURES / "dem_hydrology.png", dpi=130)


if __name__ == "__main__":
    main()
