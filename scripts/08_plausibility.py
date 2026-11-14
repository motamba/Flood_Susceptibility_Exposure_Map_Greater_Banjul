"""Step 8: plausibility check against reported flood locations.

ToR §5 step 8 (compare the high zones with publicly known flood-affected areas;
record where the map agrees and where it does not), §8 (no ground truth).
Implementation Plan §8 (reported-flood points, metric), §11 Day 6.

Input
-----
config/reported_floods.csv   places named as flooded in news reports, each with
                             its source URL, a short quote and an OSM reference
                             used to locate it (hand-collected, 14 Nov 2026)

Geocoding uses the local OSM extract (no web geocoder): node -> its point; way
-> the middle of the line (bridges) or a point inside the polygon (wards).

Metric (Plan §8)
----------------
1. Class of the 30 m cell under each point.
2. Because a place name covers an area, not a cell, also the share of land
   cells in each class within a radius around the point:
   feature (bridge) 100 m, ward or settlement 300 m.
3. Compare the share of points in High (and High + Medium) with the share of
   land in those classes. More points in High than the land share suggests the
   map has some skill; with only a handful of points this is indicative only.

Outputs
-------
data/processed/reported_floods.csv / .gpkg   geocoded points with class
outputs/tables/plausibility.csv              per-point results and the summary
outputs/figures/plausibility.png
"""
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from matplotlib.colors import ListedColormap
from rasterio.transform import rowcol
from shapely.geometry import Point

from flood.config import DATA_INTERIM, DATA_PROCESSED, FIGURES, ROOT, TABLES, ensure_dirs, load_config

OSM = DATA_INTERIM / "osm" / "greater_banjul.osm.pbf"
SOURCES = ROOT / "config" / "reported_floods.csv"
RADIUS_M = {"feature": 100, "ward": 300, "settlement": 300}
COLOURS = ["#fed976", "#fd8d3c", "#bd0026"]
LABEL_LEFT = {"R7"}   # figure only: avoid overlapping labels


def geocode(refs: pd.Series, crs) -> gpd.GeoSeries:
    """Locate 'node/<id>' or 'way/<id>' references in the local OSM extract."""
    kinds = refs.str.split("/").str[0]
    ids = refs.str.split("/").str[1]
    nodes = gpd.read_file(OSM, layer="points", where=f"osm_id IN ({','.join(repr(i) for i in ids[kinds == 'node'])})")
    way_ids = ",".join(repr(i) for i in ids[kinds == "way"])
    lines = gpd.read_file(OSM, layer="lines", where=f"osm_id IN ({way_ids})")
    polys = gpd.read_file(OSM, layer="multipolygons", where=f"osm_way_id IN ({way_ids})")
    lookup = {}
    for _, r in nodes.to_crs(crs).iterrows():
        lookup[f"node/{r.osm_id}"] = r.geometry
    for _, r in lines.to_crs(crs).iterrows():
        lookup[f"way/{r.osm_id}"] = r.geometry.interpolate(0.5, normalized=True)
    for _, r in polys.to_crs(crs).iterrows():
        lookup[f"way/{r.osm_way_id}"] = r.geometry.representative_point()
    missing = sorted(set(refs) - set(lookup))
    if missing:
        raise SystemExit(f"OSM references not found in the extract: {missing}")
    return gpd.GeoSeries([lookup[r] for r in refs], crs=crs, index=refs.index)


def neighbourhood_shares(pt: Point, radius: float, classes, transform, k: int) -> list[float]:
    """Share of classified land cells per class within radius of the point."""
    res = transform.a
    r0, c0 = rowcol(transform, pt.x, pt.y)
    n = int(np.ceil(radius / res))
    rr, cc = np.mgrid[r0 - n:r0 + n + 1, c0 - n:c0 + n + 1]
    inside = ((rr - r0) ** 2 + (cc - c0) ** 2) * res ** 2 <= radius ** 2
    inside &= (rr >= 0) & (rr < classes.shape[0]) & (cc >= 0) & (cc < classes.shape[1])
    vals = classes[rr[inside], cc[inside]]
    vals = vals[vals > 0]
    if not len(vals):
        return [np.nan] * k
    return [float((vals == i).mean()) for i in range(1, k + 1)]


def main() -> None:
    cfg = load_config()
    ensure_dirs()
    labels = cfg["classification"]["labels"]
    k = len(labels)
    with rasterio.open(DATA_PROCESSED / "susceptibility_classes.tif") as src:
        classes, transform, crs = src.read(1), src.transform, src.crs
    land = np.array([(classes == i).sum() for i in range(1, k + 1)], dtype=float)
    land_share = land / land.sum()

    pts = pd.read_csv(SOURCES, dtype=str)
    geom = geocode(pts["osm_ref"], crs)
    gdf = gpd.GeoDataFrame(pts, geometry=geom, crs=crs)
    study = gpd.read_file(DATA_PROCESSED / "study_area.gpkg", layer="study_area").union_all()
    outside = ~gdf.within(study)
    if outside.any():
        print(f"Dropping points outside the study area: {gdf.loc[outside, 'place'].tolist()}")
        gdf = gdf[~outside].reset_index(drop=True)

    rows, cols = rowcol(transform, gdf.geometry.x.values, gdf.geometry.y.values)
    codes = classes[np.asarray(rows), np.asarray(cols)]
    # A point on a bridge or creek can fall on an open-water cell (no class);
    # then take the most common class within the radius instead.
    gdf["radius_m"] = gdf["precision"].map(RADIUS_M)
    shares = np.array([neighbourhood_shares(p, r, classes, transform, k)
                       for p, r in zip(gdf.geometry, gdf["radius_m"])])
    for i, lab in enumerate(labels):
        gdf[f"{lab.lower()}_share_radius"] = shares[:, i].round(2)
    gdf["class_at_point"] = [labels[c - 1] if c else "water" for c in codes]
    gdf["code"] = np.where(codes > 0, codes, shares.argmax(axis=1) + 1)
    gdf["class"] = gdf["code"].map(lambda c: labels[c - 1])

    # Summary (Plan §8 metric).
    n = len(gdf)
    high = (gdf["code"] == k).mean()
    high_med = (gdf["code"] >= k - 1).mean()
    summary = pd.DataFrame([
        {"measure": "points", "value": n},
        {"measure": "share of points in High", "value": round(high, 2)},
        {"measure": "share of land in High", "value": round(land_share[-1], 2)},
        {"measure": "share of points in High or Medium", "value": round(high_med, 2)},
        {"measure": "share of land in High or Medium", "value": round(land_share[-2:].sum(), 2)},
        {"measure": "mean High share within radius", "value": round(np.nanmean(shares[:, -1]), 2)},
        {"measure": "ratio High points / High land", "value": round(high / land_share[-1], 2)},
    ])
    print(gdf[["id", "place", "precision", "class_at_point", "class",
               f"{labels[-1].lower()}_share_radius"]].to_string(index=False))
    print(f"\n{summary.to_string(index=False)}")

    per_point = gdf.drop(columns=["geometry", "evidence"]).copy()
    with open(TABLES / "plausibility.csv", "w", encoding="utf-8") as fh:
        per_point.to_csv(fh, index=False)
        fh.write("\n")
        summary.to_csv(fh, index=False)

    wgs = gdf.to_crs(cfg["crs"]["web"])
    out = gdf.drop(columns="code").copy()
    out["lon"], out["lat"] = wgs.geometry.x.round(5), wgs.geometry.y.round(5)
    out.drop(columns="geometry").to_csv(DATA_PROCESSED / "reported_floods.csv", index=False)
    gpkg = DATA_PROCESSED / "reported_floods.gpkg"
    gpkg.unlink(missing_ok=True)
    out[["id", "place", "event_date", "evidence", "source_title", "source_url", "precision",
         "class", "high_share_radius", "geometry"]].to_file(gpkg, driver="GPKG")

    # Figure: classes with the points and their ids.
    t = transform
    ext = (t.c, t.c + classes.shape[1] * t.a, t.f + classes.shape[0] * t.e, t.f)
    fig, ax = plt.subplots(figsize=(9, 7.5), layout="constrained")
    ax.imshow(np.where(classes > 0, classes, np.nan), extent=ext,
              cmap=ListedColormap(COLOURS), vmin=0.5, vmax=3.5, interpolation="nearest")
    gpd.GeoSeries([study], crs=crs).boundary.plot(ax=ax, color="black", linewidth=0.6)
    for _, r in gdf.iterrows():
        ax.add_patch(plt.Circle((r.geometry.x, r.geometry.y), r.radius_m, fill=False,
                                edgecolor="#6a3d9a", linewidth=1.2))
        ax.plot(r.geometry.x, r.geometry.y, "o", color="#6a3d9a", markersize=7,
                markeredgecolor="white")
        left = r.id in LABEL_LEFT
        ax.annotate(f"{r.id} {r.place.split(' (')[0]}", (r.geometry.x, r.geometry.y),
                    xytext=(-6 if left else 6, 4), textcoords="offset points", fontsize=8,
                    ha="right" if left else "left",
                    bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.8))
    for lab, col in zip(labels, COLOURS):
        ax.plot([], [], "s", color=col, markersize=10, label=lab)
    ax.plot([], [], "o", color="#6a3d9a", label="Reported flood location")
    ax.legend(loc="lower left")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_title(f"Reported flood locations vs susceptibility: {high:.0%} of points in High "
                 f"(High = {land_share[-1]:.0%} of land)")
    fig.savefig(FIGURES / "plausibility.png", dpi=130)


if __name__ == "__main__":
    main()
