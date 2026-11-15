"""Step 7: rainfall context from CHIRPS v3.

ToR §5 step 9 (rainfall context: when and how much it rains), §2 objective 3
(link to early warning), §7 D3 (charts in the web map).
Implementation Plan §7 (method, outputs, early-warning text), §11 Day 7.

Method
------
CHIRPS cells are 0.05° (about 5.5 km), so only a few cells cover Greater
Banjul. The study-area series is the mean of those cells, each weighted by the
area of study-area land (open water removed) inside it.

1. Monthly climatology 1991-2020: mean rainfall per calendar month (the WMO
   standard 30-year normal), with the 2022 example year beside it.
2. Daily rainfall June-October 2022 with a heavy-rain line and labelled peaks.
   CHIRPS v3 daily values are 5-day totals spread over days with satellite
   (IMERG) timing, so single-day peaks are smoothed and understated.
3. Heavy-rain threshold: 50 mm/day (config). The script reports how often it
   was exceeded so the choice can be judged; it is illustrative, not an
   official warning level.

Outputs
-------
outputs/tables/rainfall_monthly.csv          study-area mean per month 1991-2025
outputs/tables/rainfall_climatology.csv      1991-2020 normal and 2022 per calendar month
outputs/tables/rainfall_daily_<year>.csv     daily series for the example season
data/processed/rainfall.json                 the chart data for the web map
outputs/figures/rainfall.png
"""
import json

import matplotlib.dates as mdates

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from shapely.geometry import box

from flood.config import DATA_PROCESSED, DATA_RAW, FIGURES, TABLES, ensure_dirs, load_config

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
BAR = "#4a90c8"      # water blue of the web map
ACCENT = "#bd0026"   # High-class red: the heavy-rain line
EXAMPLE = "#1f2328"


def land_polygon(crs_utm):
    study = gpd.read_file(DATA_PROCESSED / "study_area.gpkg", layer="study_area").union_all()
    water = gpd.read_file(DATA_PROCESSED / "water_mask.gpkg")
    water = water[water["type"] == "water"].to_crs(crs_utm).union_all()
    return study.difference(water)


def cell_weights(src, land, crs_utm) -> np.ndarray:
    """Area (km²) of study-area land inside each CHIRPS cell."""
    t = src.transform
    boxes, idx = [], []
    for r in range(src.height):
        for c in range(src.width):
            x0, y0 = t * (c, r)
            x1, y1 = t * (c + 1, r + 1)
            boxes.append(box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
            idx.append((r, c))
    cells = gpd.GeoSeries(boxes, crs=src.crs).to_crs(crs_utm)
    w = np.zeros((src.height, src.width))
    for (r, c), g in zip(idx, cells):
        w[r, c] = g.intersection(land).area / 1e6
    return w


def area_mean(path, land, crs_utm, nodata):
    """Weighted study-area mean for every band, with band descriptions as labels."""
    with rasterio.open(path) as src:
        data = src.read().astype(float)
        labels = list(src.descriptions)
        w = cell_weights(src, land, crs_utm)
    data[data == nodata] = np.nan
    used = w > 0
    vals = data[:, used]
    weights = np.broadcast_to(w[used], vals.shape)
    ok = ~np.isnan(vals)
    mean = (np.where(ok, vals, 0) * weights).sum(axis=1) / (weights * ok).sum(axis=1)
    return pd.Series(mean, index=labels), int(used.sum()), float(w.sum())


def main() -> None:
    cfg = load_config()
    ensure_dirs()
    c, r = cfg["data"]["chirps"], cfg["rainfall"]
    y0, y1 = c["monthly_years"]
    year = r["example_season_year"]
    m0, m1 = r["example_season_months"]
    c0, c1 = r["climatology_years"]
    thr = r["heavy_rain_mm_per_day"]
    crs_utm = cfg["crs"]["processing"]
    land = land_polygon(crs_utm)

    # 1. Monthly series and climatology.
    monthly, n_cells, land_km2 = area_mean(
        DATA_RAW / "chirps" / f"chirps_v3_monthly_{y0}-{y1}_clip.tif", land, crs_utm, c["nodata"])
    print(f"Study-area mean from {n_cells} CHIRPS cells covering {land_km2:.0f} km2 of land")
    m = pd.DataFrame({"month": pd.PeriodIndex(monthly.index, freq="M"), "mm": monthly.values.round(1)})
    m["year"], m["m"] = m.month.dt.year, m.month.dt.month
    m.assign(month=m.month.astype(str))[["month", "mm"]].to_csv(TABLES / "rainfall_monthly.csv", index=False)
    norm = m[(m.year >= c0) & (m.year <= c1)].groupby("m")["mm"].mean()
    ex = m[m.year == year].set_index("m")["mm"]
    annual = m.groupby("year")["mm"].sum()
    normal_annual = annual.loc[c0:c1]
    clim = pd.DataFrame({"month": MONTHS, f"normal_{c0}_{c1}_mm": norm.round(1).values,
                         f"{year}_mm": ex.reindex(range(1, 13)).round(1).values})
    clim.to_csv(TABLES / "rainfall_climatology.csv", index=False)
    wet_share = norm.loc[m0:m1].sum() / norm.sum()
    print(f"\n{clim.to_string(index=False)}")
    print(f"Normal annual total {norm.sum():.0f} mm; June-October = {wet_share:.0%} of it. "
          f"{year}: {annual[year]:.0f} mm (rank {int((annual.loc[y0:y1] >= annual[year]).sum())} "
          f"of {y1 - y0 + 1} years {y0}-{y1})")

    # 2. Daily example season.
    v = c["daily_variant"]
    daily_path = DATA_RAW / "chirps" / f"chirps_v3_{v}_daily_{year}_clip.tif"
    d = None
    if daily_path.exists():
        daily, _, _ = area_mean(daily_path, land, crs_utm, c["nodata"])
        d = pd.DataFrame({"date": pd.to_datetime(daily.index), "mm": daily.values.round(1)})
        d.to_csv(TABLES / f"rainfall_daily_{year}.csv", index=False)
        wet = d[d.mm >= 1]
        # Label the days above the threshold (at least the largest one).
        peaks = d[d.mm >= thr].nlargest(5, "mm") if (d.mm >= thr).any() else d.nlargest(1, "mm")
        print(f"\nDaily {year} (June-October): {len(wet)} wet days (>= 1 mm), "
              f"95th percentile of wet days {wet.mm.quantile(0.95):.0f} mm, "
              f"{(d.mm >= thr).sum()} days >= {thr} mm")
        print(f"30-31 Jul {year}: {d.set_index('date').loc[f'{year}-07-30':f'{year}-07-31', 'mm'].sum():.0f} mm")
        print("Days above the threshold:", ", ".join(f"{p.date:%d %b} {p.mm:.0f} mm" for p in peaks.itertuples()))
    else:
        print(f"\n[pending] {daily_path.name} not downloaded yet; daily chart skipped")

    # Web chart data (Plan §7 outputs 1 and 2).
    out = {
        "source": "CHIRPS v3.0 (Funk et al. 2026), study-area mean of 0.05° cells",
        "n_cells": n_cells,
        "climatology": {"period": f"{c0}-{c1}", "months": MONTHS,
                        "normal_mm": norm.round(1).tolist(),
                        "example_year": year, "example_mm": ex.reindex(range(1, 13)).round(1).tolist(),
                        "normal_annual_mm": round(float(norm.sum())),
                        "example_annual_mm": round(float(annual[year])),
                        "wet_season_share": round(float(wet_share), 2),
                        "annual_range_mm": [round(float(normal_annual.min())), round(float(normal_annual.max()))]},
        "threshold_mm": thr,
    }
    if d is not None:
        out["daily"] = {"year": year, "dates": d.date.dt.strftime("%Y-%m-%d").tolist(),
                        "mm": d.mm.tolist(),
                        "peaks": [{"date": f"{p.date:%Y-%m-%d}", "mm": p.mm} for p in peaks.itertuples()],
                        "days_over_threshold": int((d.mm >= thr).sum()),
                        "note": "5-day totals spread over days with satellite timing; "
                                "single-day peaks are smoothed"}
    (DATA_PROCESSED / "rainfall.json").write_text(json.dumps(out, indent=1), encoding="utf-8")

    # Figure.
    n_ax = 2 if d is not None else 1
    fig, axes = plt.subplots(n_ax, 1, figsize=(9, 3.6 * n_ax), layout="constrained", squeeze=False)
    ax = axes[0, 0]
    x = np.arange(12)
    ax.bar(x, norm.values, width=0.6, color=BAR, label=f"Normal {c0}-{c1}")
    ax.plot(x, ex.reindex(range(1, 13)).values, "o", color=EXAMPLE, markersize=6,
            markeredgecolor="white", label=f"{year}")
    ax.set_xticks(x, MONTHS)
    ax.set_ylabel("mm per month")
    ax.set_title(f"Monthly rainfall, Greater Banjul: {wet_share:.0%} of the {norm.sum():.0f} mm "
                 f"annual normal falls June-October")
    ax.legend(frameon=False, loc="upper left")
    if d is not None:
        ax = axes[1, 0]
        ax.bar(d.date, d.mm, width=0.8, color=BAR)
        ax.axhline(thr, color=ACCENT, linestyle="--", linewidth=1.5)
        ax.text(d.date.iloc[0], thr + 2, f"heavy rain {thr} mm/day (illustrative)",
                color=ACCENT, fontsize=8, va="bottom")
        for p in peaks.itertuples():
            ax.annotate(f"{p.date:%d %b}\n{p.mm:.0f} mm", (p.date, p.mm), xytext=(0, 4),
                        textcoords="offset points", ha="center", fontsize=8)
        ax.set_ylim(0, max(d.mm.max(), thr) * 1.25)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        ax.set_ylabel("mm per day")
        ax.set_title(f"Daily rainfall, June-October {year} (CHIRPS daily = smoothed 5-day totals)")
    for a in axes.flat:
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
        a.grid(axis="y", color="#e5e5e5", linewidth=0.6)
        a.set_axisbelow(True)
    fig.savefig(FIGURES / "rainfall.png", dpi=130)


if __name__ == "__main__":
    main()
