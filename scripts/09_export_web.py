"""Step 9: export processed layers to lightweight files for the web map.

ToR §5 step 10 (export layers as lightweight GeoJSON) and §7 D3.
Implementation Plan §9 (web map specification, performance budget).

Each layer is exported only if its processed source exists, so the web map
can be tested while later steps (susceptibility, exposure, rainfall) are
still being built. The map shows missing layers as "pending".

Outputs: docs/data/*.geojson and docs/data/meta.json
"""
import json
from datetime import datetime, timezone

import geopandas as gpd

from flood.config import DATA_PROCESSED, WEB_DATA, ensure_dirs, load_config

PRECISION = 5  # decimal degrees, about 1 m (Plan §9 performance budget)

# (output name, source file, layer inside source or None, columns to keep)
LAYERS = [
    ("study_area", "study_area.gpkg", "study_area", ["name", "area_km2"]),
    ("units", "study_area.gpkg", "units", ["shapeName", "group", "area_km2"]),
    # Added on later days (Plan §11):
    ("susceptibility", "susceptibility_classes.gpkg", None, ["class"]),        # Day 5
    ("water", "water_mask.gpkg", None, ["type", "area_km2"]),                  # Day 4
    ("facilities", "facilities_exposed.gpkg", None, None),                     # Day 6
    ("reported_floods", "reported_floods.gpkg", None, None),                   # Day 6
]


def main() -> None:
    cfg = load_config()
    ensure_dirs()
    exported = {}
    for out_name, src, layer, cols in LAYERS:
        path = DATA_PROCESSED / src
        if not path.exists():
            print(f"[pending] {out_name}: {src} not built yet")
            continue
        gdf = gpd.read_file(path, layer=layer)
        if cols:
            gdf = gdf[cols + ["geometry"]]
        for c in gdf.select_dtypes("float").columns:
            gdf[c] = gdf[c].round(2)
        out = WEB_DATA / f"{out_name}.geojson"
        gdf.to_crs(cfg["crs"]["web"]).to_file(
            out, driver="GeoJSON",
            layer_options={"COORDINATE_PRECISION": PRECISION, "RFC7946": "YES"},
        )
        exported[out_name] = out.name
        print(f"[ok] {out_name}: {len(gdf)} features, {out.stat().st_size / 1024:.0f} KB")

    meta = {
        "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "disclaimer": cfg["project"]["disclaimer"],
        "layers": exported,
    }
    (WEB_DATA / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
