"""Step 5: sensitivity of the high-susceptibility zone to the weights.

ToR §5 step 5 (re-run with alternative weights and note how much the high zone
changes), ToR §9 ("weights are justified, and sensitivity is reported").
Implementation Plan §5.5 (scenarios, metrics, interpretation rule).

Scenarios
---------
S0         baseline weights
S1         equal weights
S2-S6      one factor +0.10, the others reduced proportionally
S7-S11     one factor dropped, the others rescaled to sum to 1

Each scenario is a complete re-run of the model: weighted index, then Jenks
classes. Metrics compare its High class with the baseline High class:
  high_km2       area of the High class
  jaccard        |A and B| / |A or B| for the High cells of S0 and the scenario
  buildings_high share of OSM buildings in High (added once step 6 has run)

Interpretation (Plan §5.5): Jaccard >= 0.8 robust; < 0.6 strongly dependent
on that change.

Outputs: outputs/tables/sensitivity.csv, outputs/figures/sensitivity.png
"""
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio

from flood.config import DATA_INTERIM, FIGURES, TABLES, active_weights, ensure_dirs, load_config
from flood.model import class_breaks, classify, load_factors, weighted_index

BUILDINGS = DATA_INTERIM / "exposure" / "building_cells.npy"   # written by step 6


def scenarios(base: dict) -> list[tuple[str, str, dict]]:
    names = list(base)
    out = [("S0", "baseline", dict(base)),
           ("S1", "equal weights", {n: 1 / len(names) for n in names})]
    for i, n in enumerate(names):
        w = {k: v * (1 - (base[n] + 0.10)) / (1 - base[n]) for k, v in base.items()}
        w[n] = base[n] + 0.10
        out.append((f"S{2 + i}", f"{n} +0.10", w))
    for i, n in enumerate(names):
        rest = {k: v for k, v in base.items() if k != n}
        total = sum(rest.values())
        out.append((f"S{2 + len(names) + i}", f"drop {n}",
                    {k: v / total for k, v in rest.items()}))
    return out


def verdict(j: float) -> str:
    return "robust" if j >= 0.8 else ("moderate" if j >= 0.6 else "sensitive")


def main() -> None:
    cfg = load_config()
    ensure_dirs()
    c = cfg["classification"]
    base = active_weights(cfg)
    factors, valid, profile = load_factors(list(base))
    cell_km2 = profile["transform"].a ** 2 / 1e6
    buildings = np.load(BUILDINGS) if BUILDINGS.exists() else None

    rows, high0 = [], None
    for sid, label, w in scenarios(base):
        idx = weighted_index(factors, w, valid)
        b = class_breaks(idx, valid, c["method"], c["n_classes"], c["jenks_precision"])
        high = classify(idx, valid, b) == c["n_classes"]
        if high0 is None:
            high0 = high
        jac = (high & high0).sum() / (high | high0).sum()
        row = {"scenario": sid, "change": label,
               **{f"w_{k}": round(w.get(k, 0.0), 3) for k in base},
               "high_km2": round(high.sum() * cell_km2, 1),
               "high_pct_land": round(100 * high.sum() / valid.sum(), 1),
               "jaccard_vs_S0": round(float(jac), 3), "verdict": verdict(jac)}
        if buildings is not None:
            row["buildings_high_pct"] = round(100 * buildings[high].sum() / buildings.sum(), 1)
        rows.append(row)
    table = pd.DataFrame(rows)
    table.to_csv(TABLES / "sensitivity.csv", index=False)
    show = ["scenario", "change", "high_km2", "high_pct_land", "jaccard_vs_S0", "verdict"]
    if buildings is not None:
        show.append("buildings_high_pct")
    print(table[show].to_string(index=False))
    others = table[table.scenario != "S0"]
    print(f"\nJaccard vs baseline: min {others.jaccard_vs_S0.min():.2f} "
          f"({others.loc[others.jaccard_vs_S0.idxmin(), 'change']}), "
          f"median {others.jaccard_vs_S0.median():.2f}")

    # Figure: Jaccard per scenario with the interpretation bands (Plan §5.5).
    fig, ax = plt.subplots(figsize=(9, 5), layout="constrained")
    t = table.iloc[1:]
    colours = t.verdict.map({"robust": "#2c7fb8", "moderate": "#fdae61", "sensitive": "#d7191c"})
    ax.barh(t.scenario + "  " + t.change, t.jaccard_vs_S0, color=colours)
    ax.axvline(0.8, color="grey", linestyle="--", linewidth=0.8)
    ax.axvline(0.6, color="grey", linestyle=":", linewidth=0.8)
    ax.set_xlim(0, 1)
    ax.invert_yaxis()
    ax.set_xlabel("Jaccard overlap of the High class with the baseline (1 = identical)")
    ax.set_title("Sensitivity of the high-susceptibility zone to the weights\n"
                 "dashed: robust (0.8); dotted: sensitive (0.6)")
    for y, (j, km2) in enumerate(zip(t.jaccard_vs_S0, t.high_km2)):
        ax.text(j + 0.01, y, f"{j:.3f}  ({km2:.0f} km²)", va="center", fontsize=8)
    fig.savefig(FIGURES / "sensitivity.png", dpi=130)


if __name__ == "__main__":
    main()
