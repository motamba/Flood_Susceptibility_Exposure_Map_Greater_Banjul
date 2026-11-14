"""Step 6: exposure of buildings, roads, schools and health facilities.

ToR §5 step 7 (intersect OSM buildings, roads and facilities with the zones;
counts and a short table), §2 objective 2, §7 D2, §10 (OSM is incomplete).
Implementation Plan §6 (tags, methods, outputs, reporting rule), §11 Day 6.

Method
------
buildings   building=* polygons -> representative point -> class of its 30 m cell
roads       highway=* lines clipped by the class polygons, length in km.
            Second count "vehicle roads" leaves out footway, path, steps,
            track, pedestrian, cycleway and bridleway.
schools     amenity = school / college / university / kindergarten
health      amenity = hospital / clinic / doctors, or any healthcare=* except
            pharmacy (pharmacies are shops, not care facilities)
facilities  points and polygons (-> representative point); the same name and
            type within 50 m counts once (Plan §6)

Assets outside the study area are ignored. Assets on open-water cells have no
class and are reported as "Unclassified". All counts are minimum values: OSM
coverage is incomplete (ToR §10).

Outputs
-------
outputs/tables/exposure.csv               assets per class (count or km, % of total)
outputs/tables/exposure_by_council.csv    the same per council, High class only
data/processed/facilities_exposed.gpkg    facilities with class (web map popups)
data/interim/exposure/building_cells.npy  buildings per 30 m cell (for step 5)
outputs/figures/exposure.png
"""
import re
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import rowcol

from flood.config import DATA_INTERIM, DATA_PROCESSED, DATA_RAW, FIGURES, TABLES, ensure_dirs, load_config

OSM = DATA_INTERIM / "osm" / "greater_banjul.osm.pbf"
OSMIUM = str(Path(sys.executable).with_name("osmium"))
OUT_CELLS = DATA_INTERIM / "exposure" / "building_cells.npy"

COLOURS = {"Low": "#fed976", "Medium": "#fd8d3c", "High": "#bd0026",
           "Unclassified": "#bdbdbd"}   # susceptibility palette of the web map (Plan §9)
SCHOOL = {"school", "college", "university", "kindergarten"}
HEALTH = {"hospital", "clinic", "doctors"}
NON_VEHICLE = {"footway", "path", "steps", "track", "pedestrian", "cycleway", "bridleway"}
DEDUP_M = 50


def tag(other_tags: pd.Series, key: str) -> pd.Series:
    """Extract one key from GDAL's OSM 'other_tags' hstore text."""
    return other_tags.fillna("").str.extract(rf'"{re.escape(key)}"=>"([^"]*)"')[0]


def osm_timestamp() -> str:
    pbf = sorted((DATA_RAW / "osm").glob("senegal-and-gambia-2*.osm.pbf"))[-1]
    return subprocess.run(
        [OSMIUM, "fileinfo", "-g", "header.option.osmosis_replication_timestamp", str(pbf)],
        capture_output=True, text=True, check=True).stdout.strip()


def class_at(points: gpd.GeoSeries, classes: np.ndarray, transform) -> np.ndarray:
    """Class code (0 = none) of the raster cell under each point."""
    rows, cols = rowcol(transform, points.x.values, points.y.values)
    rows, cols = np.asarray(rows), np.asarray(cols)
    ok = (rows >= 0) & (rows < classes.shape[0]) & (cols >= 0) & (cols < classes.shape[1])
    out = np.zeros(len(points), dtype=np.uint8)
    out[ok] = classes[rows[ok], cols[ok]]
    return out


def load_facilities(crs, study) -> gpd.GeoDataFrame:
    parts = []
    for layer, where in [
        ("points", "other_tags LIKE '%\"amenity\"=>%' OR other_tags LIKE '%\"healthcare\"=>%'"),
        ("multipolygons", "amenity IS NOT NULL OR other_tags LIKE '%\"healthcare\"=>%'"),
    ]:
        g = gpd.read_file(OSM, layer=layer, where=where)
        if layer == "points":
            osm_id = "node/" + g["osm_id"].astype(str)
        else:   # GDAL puts relations in osm_id and closed ways in osm_way_id
            osm_id = ("relation/" + g["osm_id"].astype(str)).where(
                g["osm_id"].notna(), "way/" + g["osm_way_id"].astype(str))
        amenity = g["amenity"] if "amenity" in g else tag(g["other_tags"], "amenity")
        g = gpd.GeoDataFrame({
            "osm_id": osm_id,
            "name": g["name"],
            "amenity": amenity,
            "healthcare": tag(g["other_tags"], "healthcare"),
        }, geometry=g.geometry.to_crs(crs).representative_point(), crs=crs)
        parts.append(g)
    f = pd.concat(parts, ignore_index=True)
    is_school = f.amenity.isin(SCHOOL)
    is_health = f.amenity.isin(HEALTH) | (f.healthcare.notna() & (f.healthcare != "pharmacy")
                                          & (f.amenity != "pharmacy"))
    f["type"] = np.where(is_school, "school", np.where(is_health, "health", None))
    f = f[f["type"].notna()]
    f["kind"] = f.amenity.fillna(f.healthcare)
    f = f[f.within(study)].reset_index(drop=True)

    # Deduplicate: same type and same name within 50 m (polygon + point of one site).
    key = f["name"].fillna("").str.lower().str.replace(r"[^a-z0-9]", "", regex=True)
    drop = set()
    for i in range(len(f)):
        if i in drop or not key[i]:
            continue
        near = f.index[(key == key[i]) & (f["type"] == f["type"][i])
                       & (f.distance(f.geometry[i]) <= DEDUP_M) & (f.index > i)]
        drop.update(near)
    print(f"Facilities: {len(f)} in study area, {len(drop)} duplicates removed")
    return f.drop(index=list(drop)).reset_index(drop=True)


def share_table(values: dict, labels: list[str], unit: str) -> pd.DataFrame:
    rows = []
    for asset, per_class in values.items():
        total = sum(per_class.values())
        row = {"asset": asset, "unit": unit if asset not in UNITS else UNITS[asset]}
        for lab in labels + ["Unclassified"]:
            v = per_class.get(lab, 0)
            row[lab] = round(v, 1)
            row[f"{lab}_pct"] = round(100 * v / total, 1) if total else np.nan
        row["Total"] = round(total, 1)
        rows.append(row)
    return pd.DataFrame(rows)


UNITS = {"Land area": "km2", "Buildings": "count", "Roads (all)": "km",
         "Roads (vehicle)": "km", "Schools": "count", "Health facilities": "count"}


def main() -> None:
    cfg = load_config()
    ensure_dirs()
    labels = cfg["classification"]["labels"]
    study_gdf = gpd.read_file(DATA_PROCESSED / "study_area.gpkg", layer="study_area")
    crs = study_gdf.crs
    study = study_gdf.union_all()
    units = gpd.read_file(DATA_PROCESSED / "study_area.gpkg", layer="units")
    with rasterio.open(DATA_PROCESSED / "susceptibility_classes.tif") as src:
        classes, transform = src.read(1), src.transform
    cell_km2 = transform.a ** 2 / 1e6
    name = {i + 1: lab for i, lab in enumerate(labels)} | {0: "Unclassified"}
    ts = osm_timestamp()
    print(f"OSM data as of {ts}")

    # Buildings: one representative point per footprint.
    b = gpd.read_file(OSM, layer="multipolygons", where="building IS NOT NULL",
                      columns=["building"])
    b = gpd.GeoDataFrame(geometry=b.geometry.to_crs(crs).representative_point(), crs=crs)
    b = b[b.within(study)].reset_index(drop=True)
    b["code"] = class_at(b.geometry, classes, transform)
    rows, cols = rowcol(transform, b.geometry.x.values, b.geometry.y.values)
    cells = np.zeros(classes.shape, dtype=np.int32)
    np.add.at(cells, (np.asarray(rows), np.asarray(cols)), 1)
    OUT_CELLS.parent.mkdir(parents=True, exist_ok=True)
    np.save(OUT_CELLS, cells)

    # Roads: clip lines with the class polygons (same polygons as the web map).
    r = gpd.read_file(OSM, layer="lines", where="highway IS NOT NULL", columns=["highway"])
    r = r.to_crs(crs)
    r = r[r.intersects(study)]
    r["geometry"] = r.intersection(study)
    zones = gpd.read_file(DATA_PROCESSED / "susceptibility_classes.gpkg")
    road_km = {"Roads (all)": {}, "Roads (vehicle)": {}}
    vehicle = ~r.highway.isin(NON_VEHICLE)
    for _, z in zones.iterrows():
        km = r.intersection(z.geometry).length / 1000
        road_km["Roads (all)"][z["class"]] = km.sum()
        road_km["Roads (vehicle)"][z["class"]] = km[vehicle].sum()
    for k, sub in [("Roads (all)", r), ("Roads (vehicle)", r[vehicle])]:
        road_km[k]["Unclassified"] = sub.length.sum() / 1000 - sum(road_km[k].values())

    # Facilities.
    f = load_facilities(crs, study)
    f["code"] = class_at(f.geometry, classes, transform)
    f["class"] = f["code"].map(name)
    f = f.sjoin(units[["shapeName", "group", "geometry"]], how="left", predicate="within") \
         .drop(columns="index_right").rename(columns={"shapeName": "district"})

    # Exposure table (Plan §6).
    def per_class(codes):
        s = pd.Series(codes).map(name).value_counts()
        return {lab: float(s.get(lab, 0)) for lab in labels + ["Unclassified"]}

    land = {name[i]: float((classes == i).sum() * cell_km2) for i in range(1, len(labels) + 1)}
    values = {"Land area": land,
              "Buildings": per_class(b["code"]),
              **road_km,
              "Schools": per_class(f.loc[f["type"] == "school", "code"]),
              "Health facilities": per_class(f.loc[f["type"] == "health", "code"])}
    table = share_table(values, labels, "count")
    table["osm_data_as_of"] = ts
    table.to_csv(TABLES / "exposure.csv", index=False)
    show = ["asset", "unit"] + [c for lab in labels for c in (lab, f"{lab}_pct")] + ["Unclassified", "Total"]
    print(table[show].to_string(index=False))

    # By council: what lies in the High class (useful for the web panel).
    b = b.sjoin(units[["group", "geometry"]], how="left", predicate="within")
    high = len(labels)
    rows = []
    for g, ug in units.groupby("group"):
        mask_b = b["group"] == g
        mask_f = f["group"] == g
        rows.append({
            "council": g,
            "buildings": int(mask_b.sum()),
            "buildings_high": int((mask_b & (b["code"] == high)).sum()),
            "buildings_high_pct": round(100 * (mask_b & (b["code"] == high)).sum() / max(mask_b.sum(), 1), 1),
            "schools_high": int((mask_f & (f["type"] == "school") & (f["code"] == high)).sum()),
            "health_high": int((mask_f & (f["type"] == "health") & (f["code"] == high)).sum()),
        })
    by_council = pd.DataFrame(rows)
    by_council.to_csv(TABLES / "exposure_by_council.csv", index=False)
    print(f"\n{by_council.to_string(index=False)}")
    print(f"(buildings outside any council polygon, e.g. in the added Tanbi area: "
          f"{b['group'].isna().sum()})")

    # Facilities layer for the web map popups.
    out = DATA_PROCESSED / "facilities_exposed.gpkg"
    out.unlink(missing_ok=True)
    f[["osm_id", "name", "type", "kind", "class", "district", "geometry"]].to_file(out, driver="GPKG")

    # Figure: share of each asset per class, against the share of land (Plan §6).
    fig, ax = plt.subplots(figsize=(9, 4.2), layout="constrained")
    order = ["Land area", "Buildings", "Roads (vehicle)", "Schools", "Health facilities"]
    t = table.set_index("asset").loc[order]
    left = np.zeros(len(order))
    for lab in labels + ["Unclassified"]:
        w = t[f"{lab}_pct"].fillna(0).values
        ax.barh(order, w, left=left, color=COLOURS[lab], edgecolor="white", linewidth=2,
                label=lab if w.max() >= 0.5 else None, height=0.6)
        if lab == "High":
            for y, (l0, v) in enumerate(zip(left, w)):
                ax.text(l0 + v / 2, y, f"{v:.0f}%", ha="center", va="center",
                        color="white", fontsize=9, fontweight="bold")
        left += w
    totals = [f"{t.loc[a, 'Total']:,.0f} {'km²' if a == 'Land area' else 'km' if 'Roads' in a else ''}"
              for a in order]
    ax.set_yticks(range(len(order)), [f"{a}\n({tot.strip()})" for a, tot in zip(order, totals)])
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("share of total (%)")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.legend(ncols=4, loc="upper center", bbox_to_anchor=(0.5, -0.18), frameon=False)
    ax.set_title(f"Exposure by susceptibility class (minimum counts, OSM data {ts[:10]})")
    fig.savefig(FIGURES / "exposure.png", dpi=130)


if __name__ == "__main__":
    main()
