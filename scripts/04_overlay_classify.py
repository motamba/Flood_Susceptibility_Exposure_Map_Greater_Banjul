"""Step 4: weighted overlay and classification into low / medium / high.

ToR §5 steps 4 and 6 (weighted overlay; classify into low, medium, high),
§7 D2 (susceptibility layer as GeoTIFF / GeoJSON).
Implementation Plan §5.2 (weights), §5.4 (Jenks baseline, quantiles compared),
§11 Day 5.

Outputs
-------
data/processed/susceptibility_index.tif     continuous index 0-1 (D2)
data/processed/susceptibility_classes.tif   1 Low, 2 Medium, 3 High (D2)
data/processed/susceptibility_classes.gpkg  class polygons for the web map
outputs/tables/classification.csv           Jenks vs quantile breaks and areas
outputs/figures/susceptibility.png
"""
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from matplotlib.colors import ListedColormap
from rasterio.features import shapes
from shapely.geometry import shape

from flood.config import DATA_PROCESSED, FIGURES, TABLES, active_weights, ensure_dirs, load_config
from flood.model import class_breaks, classify, load_factors, weighted_index

COLOURS = ["#fed976", "#fd8d3c", "#bd0026"]   # same palette as the web map (Plan §9)


def save(path, array, profile, dtype, nodata):
    prof = profile.copy()
    prof.update(dtype=dtype, nodata=nodata, count=1, compress="deflate", tiled=True,
                blockxsize=256, blockysize=256)
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(array.astype(dtype), 1)


def area_table(cls, labels, cell_km2):
    counts = [(cls == i + 1).sum() for i in range(len(labels))]
    total = sum(counts)
    return {lab: (round(c * cell_km2, 1), round(100 * c / total, 1))
            for lab, c in zip(labels, counts)}


def main() -> None:
    cfg = load_config()
    ensure_dirs()
    c = cfg["classification"]
    labels = c["labels"]
    weights = active_weights(cfg)
    factors, valid, profile = load_factors(list(weights))
    cell_km2 = profile["transform"].a ** 2 / 1e6

    # Weighted overlay (ToR §5 step 4).
    index = weighted_index(factors, weights, valid)
    print(f"Weights: {weights}")
    print(f"Index on {valid.sum():,} land cells ({valid.sum() * cell_km2:.1f} km2): "
          f"min {np.nanmin(index):.3f}, median {np.nanmedian(index):.3f}, max {np.nanmax(index):.3f}")

    # Classification: Jenks (baseline) and quantiles (comparison) (Plan §5.4).
    rows, classes = [], {}
    for method in ("jenks", "quantiles"):
        b = class_breaks(index, valid, method, c["n_classes"], c["jenks_precision"])
        classes[method] = classify(index, valid, b)
        areas = area_table(classes[method], labels, cell_km2)
        row = {"method": method, "breaks": ", ".join(f"{x:.3f}" for x in b)}
        for lab, (km2, pct) in areas.items():
            row[f"{lab}_km2"], row[f"{lab}_pct"] = km2, pct
        rows.append(row)
    table = pd.DataFrame(rows)
    table.to_csv(TABLES / "classification.csv", index=False)
    print(f"\n{table.to_string(index=False)}")
    chosen = classes[c["method"]]
    agree = (classes["jenks"] == classes["quantiles"])[valid].mean()
    print(f"\nJenks and quantile classes agree on {agree:.1%} of cells; using {c['method']}")

    # Save rasters (D2).
    save(DATA_PROCESSED / "susceptibility_index.tif", np.nan_to_num(index, nan=-9999),
         profile, "float32", -9999)
    save(DATA_PROCESSED / "susceptibility_classes.tif", chosen, profile, "uint8", 0)

    # Polygons for the web map: dissolved by class, simplified (Plan §9).
    geoms = [(shape(g), int(v)) for g, v in
             shapes(chosen, mask=chosen > 0, transform=profile["transform"])]
    gdf = gpd.GeoDataFrame({"code": [v for _, v in geoms]},
                           geometry=[g for g, _ in geoms], crs=profile["crs"])
    gdf = gdf.dissolve(by="code", as_index=False)
    gdf["class"] = gdf["code"].map(lambda v: labels[v - 1])
    gdf["area_km2"] = (gdf.area / 1e6).round(1)
    gdf["geometry"] = gdf.simplify(10)
    out = DATA_PROCESSED / "susceptibility_classes.gpkg"
    out.unlink(missing_ok=True)
    gdf[["code", "class", "area_km2", "geometry"]].to_file(out, driver="GPKG")

    # Figure.
    study = gpd.read_file(DATA_PROCESSED / "study_area.gpkg", layer="study_area")
    t = profile["transform"]
    ext = (t.c, t.c + profile["width"] * t.a, t.f + profile["height"] * t.e, t.f)
    fig, axes = plt.subplots(1, 2, figsize=(14, 6.5), layout="constrained")
    im = axes[0].imshow(index, extent=ext, cmap="YlOrRd", vmin=0, vmax=1)
    fig.colorbar(im, ax=axes[0], shrink=0.7, label="susceptibility index (0-1)")
    axes[0].set_title("Weighted susceptibility index")
    show = np.where(chosen > 0, chosen, np.nan)
    axes[1].imshow(show, extent=ext, cmap=ListedColormap(COLOURS), vmin=0.5, vmax=3.5,
                   interpolation="nearest")
    axes[1].set_title(f"Classes ({c['method']} natural breaks)")
    for lab, col in zip(labels, COLOURS):
        axes[1].plot([], [], "s", color=col, markersize=10, label=lab)
    axes[1].legend(loc="lower left")
    for a in axes:
        study.boundary.plot(ax=a, color="black", linewidth=0.6)
        a.set_xticks([])
        a.set_yticks([])
    fig.suptitle("Greater Banjul flood susceptibility (index, not a flood forecast)")
    fig.savefig(FIGURES / "susceptibility.png", dpi=130)


if __name__ == "__main__":
    main()
