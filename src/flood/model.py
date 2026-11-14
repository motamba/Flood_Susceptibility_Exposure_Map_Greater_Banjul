"""Weighted overlay and classification, shared by steps 4 and 5.

ToR §5 steps 4-6 (weighted overlay, sensitivity check, classify).
Implementation Plan §5.2 (weights), §5.4 (Jenks), §5.5 (scenarios).
"""
import numpy as np
import rasterio

from flood.config import DATA_INTERIM

FACTORS = DATA_INTERIM / "factors"
HYDRO = DATA_INTERIM / "hydro"


def load_factors(names):
    """Normalised factor arrays, the land mask and the raster profile.

    Valid = land cells inside the study area (water excluded). Depression
    depth has no value on about 1% of land cells (edges of the filled DEM);
    these are treated as 0 = no depression.
    """
    with rasterio.open(HYDRO / "inside_study.tif") as src:
        inside = src.read(1) == 1
        profile = src.profile
    with rasterio.open(HYDRO / "water_mask.tif") as src:
        water = src.read(1) == 1
    factors = {}
    for n in names:
        with rasterio.open(FACTORS / f"norm_{n}.tif") as src:
            a = src.read(1, masked=True).filled(np.nan).astype("float32")
        factors[n] = a
    valid = inside & ~water & ~np.isnan(factors[names[0]])
    if "depression_depth" in factors:
        d = factors["depression_depth"]
        factors["depression_depth"] = np.where(valid & np.isnan(d), 0.0, d)
    for n, a in factors.items():
        valid &= ~np.isnan(a)
    return factors, valid, profile


def weighted_index(factors, weights, valid):
    """S = sum(w_i * f_i) on valid cells; weights must sum to 1."""
    total = sum(weights.values())
    assert abs(total - 1) < 1e-6, f"weights sum to {total}"
    s = np.zeros(valid.shape, dtype="float32")
    for n, w in weights.items():
        s += w * np.nan_to_num(factors[n])
    return np.where(valid, s, np.nan)


def jenks_weighted(values, weights, k):
    """Exact Fisher-Jenks optimal breaks for weighted, sorted values.

    Dynamic programming that minimises the total within-class sum of squared
    deviations. Returns the index of the last value in each of the first k-1
    classes.
    """
    m = len(values)
    w = np.concatenate([[0.0], np.cumsum(weights)])
    s1 = np.concatenate([[0.0], np.cumsum(weights * values)])
    s2 = np.concatenate([[0.0], np.cumsum(weights * values ** 2)])

    def ssd(i, j):  # values i..j-1 (vectorised over i)
        ww = w[j] - w[i]
        return np.where(ww > 0, (s2[j] - s2[i]) - (s1[j] - s1[i]) ** 2 / np.maximum(ww, 1e-12), 0)

    cost = np.full((k + 1, m + 1), np.inf)
    back = np.zeros((k + 1, m + 1), dtype=int)
    cost[0, 0] = 0.0
    for c in range(1, k + 1):
        for j in range(c, m + 1):
            i = np.arange(c - 1, j)
            total = cost[c - 1, i] + ssd(i, j)
            best = int(np.argmin(total))
            cost[c, j], back[c, j] = total[best], i[best]
    ends, j = [], m
    for c in range(k, 1, -1):
        j = back[c, j]
        ends.append(j - 1)
    return sorted(ends)


def class_breaks(index, valid, method, k, precision=0.001):
    """Upper class bounds (excluding the maximum) for k classes.

    jenks:     exact Fisher-Jenks on all valid cells. The index is rounded to
               `precision` first, so the algorithm runs on about 1000 distinct
               values weighted by their cell counts instead of on every cell.
    quantiles: equal numbers of cells per class (comparison only).
    """
    values = index[valid]
    if method == "quantiles":
        return list(np.quantile(values, np.linspace(0, 1, k + 1)[1:-1]))
    rounded = np.round(values / precision).astype(np.int64)
    uniq, counts = np.unique(rounded, return_counts=True)
    v = uniq * precision
    ends = jenks_weighted(v, counts.astype(float), k)
    # break halfway between the last value of a class and the next value
    return [float((v[e] + v[e + 1]) / 2) for e in ends]


def classify(index, valid, breaks):
    """Class codes 1..k (0 = not classified)."""
    cls = np.digitize(index, breaks) + 1
    return np.where(valid, cls, 0).astype("uint8")
