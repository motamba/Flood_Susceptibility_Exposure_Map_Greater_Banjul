"""Step 0: define the Greater Banjul study area.

ToR §3 (scope): study area fixed as a polygon in Week 1.
ToR §5 step 1 (set up: define the study area polygon).
Implementation Plan §4 (study area, CRS, hydrology buffer) and §11 Day 1.

Greater Banjul = Banjul City Council + Kanifing Municipal Council + Kombo North,
built by dissolving geoBoundaries ADM2 units (CC BY 4.0).

Outputs
-------
data/raw/boundaries/geoBoundaries-GMB-ADM2.geojson   raw download (in MANIFEST.csv)
data/processed/study_area.gpkg      layers: units, study_area, hydrology_buffer (EPSG:32628)
data/processed/study_area.geojson   dissolved outline, EPSG:4326 (for the web map)
outputs/figures/study_area.png      quick-look map for visual checking
"""
import geopandas as gpd
import matplotlib.pyplot as plt

from flood.config import DATA_PROCESSED, DATA_RAW, FIGURES, ensure_dirs, load_config
from flood.io import download


def main() -> None:
    cfg = load_config()
    ensure_dirs()
    sa = cfg["study_area"]
    utm = cfg["crs"]["processing"]

    # 1. Download (or reuse) the national ADM2 boundaries.
    raw = download(
        sa["boundaries_url"],
        DATA_RAW / "boundaries" / "geoBoundaries-GMB-ADM2.geojson",
        source=sa["boundaries_source"],
        licence=sa["boundaries_licence"],
    )
    adm2 = gpd.read_file(raw).to_crs(utm)
    name = sa["name_field"]

    # 2. Select the units that make up Greater Banjul; fail loudly on typos.
    wanted = {unit: group for group, units in sa["units"].items() for unit in units}
    missing = set(wanted) - set(adm2[name])
    if missing:
        raise SystemExit(f"Units not found in boundaries: {sorted(missing)}")
    units = adm2[adm2[name].isin(wanted)].copy()
    units["group"] = units[name].map(wanted)
    units["area_km2"] = units.area / 1e6
    units = units[[name, "group", "area_km2", "geometry"]]

    # 3. Dissolve into one study-area polygon. The ADM2 units leave out the
    #    Tanbi Wetland between Banjul and Kanifing, which Plan §5.3 keeps in the
    #    analysis. A morphological closing (buffer out, then back in) fills gaps
    #    narrower than 2 x closing_m. Land of other districts is then removed,
    #    so only unassigned area (wetland, coastal water) can be added. Sea
    #    picked up along the coast is removed later by the water mask
    #    (Plan §5.3). Then buffer for hydrology (Plan §4).
    closing = sa["gap_closing_m"]
    dissolved = units.union_all()
    others = adm2[~adm2[name].isin(wanted)].union_all()
    closed = (dissolved.buffer(closing).buffer(-closing)
              .difference(others).union(dissolved))
    # keep only the main polygon (drop slivers left by the difference)
    if closed.geom_type == "MultiPolygon":
        closed = max(closed.geoms, key=lambda g: g.area)
    study = gpd.GeoDataFrame(
        {"name": ["Greater Banjul"], "geometry": [closed]}, crs=utm
    )
    study["added_by_closing_km2"] = (closed.area - dissolved.area) / 1e6
    study["area_km2"] = study.area / 1e6
    buffer = study.copy()
    buffer["geometry"] = study.buffer(sa["hydrology_buffer_m"])
    buffer["name"] = f"Greater Banjul + {sa['hydrology_buffer_m']} m buffer"

    # 4. Write outputs.
    gpkg = DATA_PROCESSED / "study_area.gpkg"
    gpkg.unlink(missing_ok=True)
    units.to_file(gpkg, layer="units", driver="GPKG")
    study.to_file(gpkg, layer="study_area", driver="GPKG")
    buffer.to_file(gpkg, layer="hydrology_buffer", driver="GPKG")
    study.to_crs(cfg["crs"]["web"]).to_file(
        DATA_PROCESSED / "study_area.geojson", driver="GeoJSON",
        layer_options={"COORDINATE_PRECISION": 5},
    )

    # 5. Quick-look figure for visual checking (Plan §11 Day 1 "done when").
    fig, ax = plt.subplots(figsize=(8, 7))
    xmin, ymin, xmax, ymax = buffer.total_bounds
    pad = 3000
    adm2.plot(ax=ax, color="#eeeeee", edgecolor="#999999", linewidth=0.5)
    colours = {"banjul": "#1b9e77", "kanifing": "#d95f02", "kombo_north": "#7570b3"}
    for group, gdf in units.groupby("group"):
        gdf.plot(ax=ax, color=colours[group], alpha=0.6, edgecolor="white",
                 linewidth=0.8, label=group.replace("_", " ").title())
    study.boundary.plot(ax=ax, color="black", linewidth=1.2)
    buffer.boundary.plot(ax=ax, color="black", linestyle="--", linewidth=0.8)
    for _, r in units.iterrows():
        p = r.geometry.representative_point()
        ax.annotate(r[name], (p.x, p.y), fontsize=6, ha="center")
    ax.set_xlim(xmin - pad, xmax + pad)
    ax.set_ylim(ymin - pad, ymax + pad)
    ax.ticklabel_format(style="plain")
    ax.set_title("Greater Banjul study area\n"
                 "solid: study area incl. Tanbi gap fill; dashed: 2 km hydrology buffer",
                 fontsize=10)
    ax.set_xlabel("Easting (m, UTM 28N)")
    ax.set_ylabel("Northing (m, UTM 28N)")
    ax.legend(loc="lower left", fontsize=8)
    ax.text(0.99, 0.01, "Boundaries: geoBoundaries (CC BY 4.0)",
            transform=ax.transAxes, ha="right", fontsize=6)
    fig.tight_layout()
    fig.savefig(FIGURES / "study_area.png", dpi=150)

    # 6. Summary for the log / README.
    print(units[[name, "group", "area_km2"]].round(2).to_string(index=False))
    lon_min, lat_min, lon_max, lat_max = study.to_crs(cfg["crs"]["web"]).total_bounds
    print(f"\nStudy area: {study.area_km2.iloc[0]:.1f} km2 "
          f"(of which {study.added_by_closing_km2.iloc[0]:.1f} km2 added by gap closing)")
    print(f"Bounds (lon/lat): {lon_min:.4f}, {lat_min:.4f}, {lon_max:.4f}, {lat_max:.4f}")


if __name__ == "__main__":
    main()
