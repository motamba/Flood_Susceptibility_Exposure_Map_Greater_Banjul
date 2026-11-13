"""Step 1: download all raw data and record source, date and licence.

ToR §4 (data sources: access, resolution and licence checked and recorded),
ToR §9 (every layer's source, date and licence is stated).
Implementation Plan §5.1 (DEM choice), §6 (OSM), §7 (CHIRPS), §11 Day 2.

Cloud-optimised rasters are read remotely and clipped to the hydrology buffer
plus a margin, so only the study-area window is transferred. Every output is
listed in data/raw/MANIFEST.csv with URL, date, licence and SHA-256.

Usage:  python scripts/01_download_data.py [--only dem,worldcover,osm,chirps]

Outputs (all git-ignored except MANIFEST.csv)
-------
data/raw/dem/fabdem_clip.tif, data/raw/dem/copernicus_glo30_clip.tif
data/raw/landcover/worldcover_2021_clip.tif
data/raw/osm/senegal-and-gambia-<date>.osm.pbf  (+ data/interim/osm/greater_banjul.osm.pbf)
data/raw/chirps/chirps_v3_monthly_<y0>-<y1>_clip.tif
data/raw/chirps/chirps_v3_<variant>_daily_<year>_clip.tif
"""
import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

# GDAL network settings must be set before rasterio loads GDAL.
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")  # no directory listings
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "1")                    # one quick retry; long
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "30")                 # back-off is in Python
# Only request the raster itself, not sidecar files (.aux.xml, .ovr, .msk),
# which would waste requests on rate-limited servers.
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif,.cog,.zip")

import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
import requests  # noqa: E402
from rasterio.windows import from_bounds  # noqa: E402

from flood.config import DATA_INTERIM, DATA_PROCESSED, DATA_RAW, ensure_dirs, load_config  # noqa: E402
from flood.io import download, record  # noqa: E402

GTIFF = {"driver": "GTiff", "compress": "deflate", "tiled": True,
         "blockxsize": 256, "blockysize": 256}

# Find CLI tools in the active environment even when it is not "activated".
import shutil  # noqa: E402
import sys  # noqa: E402
OSMIUM = shutil.which("osmium", path=f"{Path(sys.executable).parent}{os.pathsep}"
                      f"{os.environ.get('PATH', '')}") or "osmium"


def clip_bounds(cfg: dict) -> tuple[float, float, float, float]:
    """Hydrology buffer bounds in lon/lat, plus a margin (Plan §4)."""
    buf = gpd.read_file(DATA_PROCESSED / "study_area.gpkg", layer="hydrology_buffer")
    lon0, lat0, lon1, lat1 = buf.to_crs("EPSG:4326").total_bounds
    m = cfg["data"]["clip_margin_deg"]
    return tuple(round(float(v), 4) for v in (lon0 - m, lat0 - m, lon1 + m, lat1 + m))


def read_window(url: str, bounds):
    """Read the window covering bounds from a (remote) raster."""
    with rasterio.open(url) as src:
        win = from_bounds(*bounds, transform=src.transform).round_offsets().round_lengths()
        data = src.read(window=win)
        profile = src.profile.copy()
        profile.update(height=data.shape[1], width=data.shape[2],
                       transform=src.window_transform(win))
    return data, profile


def clip_raster(spec: dict, dst: Path, bounds, nodata=None) -> None:
    """Clip a remote raster to bounds and save it as a compressed GeoTIFF."""
    if dst.exists():
        print(f"[skip] {dst.name} already present")
    else:
        data, profile = read_window(spec["url"], bounds)
        profile.update(GTIFF)
        if nodata is not None:
            profile["nodata"] = nodata
        dst.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(dst, "w", **profile) as out:
            out.write(data)
        print(f"[ok] {dst.name}: {data.shape[2]} x {data.shape[1]} px, "
              f"range {np.nanmin(data)} to {np.nanmax(data)}")
    record(dst, spec["url"], spec["source"], spec["licence"],
           note=f"window clip to lon/lat {bounds}")


# --- DEM and land cover -------------------------------------------------------

def get_dem(cfg, bounds) -> None:
    clip_raster(cfg["data"]["fabdem"], DATA_RAW / "dem" / "fabdem_clip.tif", bounds)
    clip_raster(cfg["data"]["copernicus"], DATA_RAW / "dem" / "copernicus_glo30_clip.tif", bounds)


def get_worldcover(cfg, bounds) -> None:
    clip_raster(cfg["data"]["worldcover"],
                DATA_RAW / "landcover" / "worldcover_2021_clip.tif", bounds)


# --- OpenStreetMap (Plan §6) ----------------------------------------------------

def get_osm(cfg, bounds) -> None:
    spec = cfg["data"]["osm"]
    folder = DATA_RAW / "osm"
    dated = sorted(folder.glob("senegal-and-gambia-2*.osm.pbf"))
    if dated:
        pbf = dated[-1]
        print(f"[skip] {pbf.name} already present")
    else:
        tmp = download(spec["url"], folder / "senegal-and-gambia-latest.osm.pbf",
                       spec["source"], spec["licence"], overwrite=True)
        expected = requests.get(spec["url"] + ".md5", timeout=60).text.split()[0]
        actual = hashlib.md5(tmp.read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"MD5 mismatch for {tmp.name}: {actual} != {expected}")
        # Name the file by its OSM data timestamp, since "latest" changes daily.
        ts = subprocess.run(
            [OSMIUM, "fileinfo", "-g", "header.option.osmosis_replication_timestamp", str(tmp)],
            capture_output=True, text=True, check=True).stdout.strip()
        pbf = folder / f"senegal-and-gambia-{ts[:10]}.osm.pbf"
        tmp.replace(pbf)
        # Drop the manifest row for the temporary name by re-recording under the new one.
        _drop_manifest_row(tmp)
    ts = subprocess.run(
        [OSMIUM, "fileinfo", "-g", "header.option.osmosis_replication_timestamp", str(pbf)],
        capture_output=True, text=True, check=True).stdout.strip()
    record(pbf, spec["url"], spec["source"], spec["licence"],
           note=f"OSM data timestamp {ts}; MD5 verified")

    # Clip to the study-area bounds for fast reading later (not a raw file).
    out = DATA_INTERIM / "osm" / "greater_banjul.osm.pbf"
    out.parent.mkdir(parents=True, exist_ok=True)
    bbox = ",".join(str(b) for b in bounds)
    subprocess.run([OSMIUM, "extract", "-b", bbox, "-s", "smart", "--overwrite",
                    "-o", str(out), str(pbf)], check=True)
    print(f"[ok] {out.name}: {out.stat().st_size / 1e6:.1f} MB (OSM data {ts})")


def _drop_manifest_row(path: Path) -> None:
    import csv
    from flood.config import MANIFEST, ROOT
    if not MANIFEST.exists():
        return
    rel = path.relative_to(ROOT).as_posix()
    with open(MANIFEST, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields, rows = reader.fieldnames, [r for r in reader if r["file"] != rel]
    with open(MANIFEST, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


# --- CHIRPS rainfall (Plan §7) --------------------------------------------------

def fetch_series(items: list[tuple[str, str]], cache: Path, bounds, pause: float):
    """Window-read a list of (label, url) rasters, caching each result.

    Reruns resume from the cache, so an interrupted download is not repeated.
    """
    cache.mkdir(parents=True, exist_ok=True)
    meta_path = cache / "_profile.json"
    arrays, profile = [], None
    for i, (label, url) in enumerate(items, 1):
        npy = cache / f"{label}.npy"
        if not npy.exists():
            # The CHC server rate-limits (HTTP 429, no Retry-After header).
            # Back off for minutes, not seconds, so we stop adding load.
            backoff_min = [5, 10, 20, 30, 30, 30]
            for attempt in range(len(backoff_min) + 1):
                try:
                    data, prof = read_window("/vsicurl/" + url, bounds)
                    break
                except rasterio.errors.RasterioIOError as e:
                    if attempt == len(backoff_min):
                        raise
                    wait = backoff_min[attempt]
                    print(f"  {time.strftime('%H:%M')} rate-limited at {label}; "
                          f"waiting {wait} min ({str(e)[:60]})", flush=True)
                    time.sleep(wait * 60)
            np.save(npy, data[0])
            if not meta_path.exists():
                meta_path.write_text(json.dumps({
                    "crs": prof["crs"].to_string(),
                    "transform": list(prof["transform"])[:6],
                }))
            time.sleep(pause)
        arrays.append(np.load(npy))
        if i % 25 == 0 or i == len(items):
            print(f"  {cache.name}: {i}/{len(items)}")
    shapes = {a.shape for a in arrays}
    if len(shapes) != 1:
        raise SystemExit(f"Inconsistent window shapes in {cache.name}: {shapes}")
    profile = json.loads(meta_path.read_text())
    return np.stack(arrays), profile


def write_stack(stack, profile, labels, dst: Path, nodata) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        dst, "w", driver="GTiff", compress="deflate", dtype="float32",
        count=stack.shape[0], height=stack.shape[1], width=stack.shape[2],
        crs=profile["crs"], transform=rasterio.Affine(*profile["transform"]),
        nodata=nodata,
    ) as out:
        out.write(stack.astype("float32"))
        for b, label in enumerate(labels, 1):
            out.set_band_description(b, label)


def get_chirps(cfg, bounds) -> None:
    c = cfg["data"]["chirps"]
    r = cfg["rainfall"]
    pause = c["request_pause_s"]

    # Monthly totals for the climatology.
    y0, y1 = c["monthly_years"]
    months = pd.period_range(f"{y0}-01", f"{y1}-12", freq="M")
    dst = DATA_RAW / "chirps" / f"chirps_v3_monthly_{y0}-{y1}_clip.tif"
    if dst.exists():
        print(f"[skip] {dst.name} already present")
    else:
        items = [(str(p), c["monthly_url"].format(year=p.year, month=p.month)) for p in months]
        stack, prof = fetch_series(items, DATA_INTERIM / "chirps_monthly", bounds, pause)
        write_stack(stack, prof, [i[0] for i in items], dst, c["nodata"])
        print(f"[ok] {dst.name}: {stack.shape}")
    record(dst, c["monthly_url"], c["source"], c["licence"],
           note=f"{len(months)} monthly COGs {y0}-{y1}, window clip to {bounds}; "
                f"bands = months; nodata {c['nodata']}")

    # Daily values for one example wet season.
    year = r["example_season_year"]
    m0, m1 = r["example_season_months"]
    days = pd.date_range(f"{year}-{m0:02d}-01", pd.Timestamp(f"{year}-{m1:02d}-01")
                         + pd.offsets.MonthEnd(0))
    v = c["daily_variant"]
    dst = DATA_RAW / "chirps" / f"chirps_v3_{v}_daily_{year}_clip.tif"
    if dst.exists():
        print(f"[skip] {dst.name} already present")
    else:
        items = [(d.strftime("%Y-%m-%d"),
                  c["daily_url"].format(variant=v, year=d.year, month=d.month, day=d.day))
                 for d in days]
        stack, prof = fetch_series(items, DATA_INTERIM / f"chirps_daily_{v}_{year}", bounds, pause)
        write_stack(stack, prof, [i[0] for i in items], dst, c["nodata"])
        print(f"[ok] {dst.name}: {stack.shape}")
    record(dst, c["daily_url"], c["source"], c["licence"],
           note=f"{len(days)} daily files {days[0].date()} to {days[-1].date()}, variant '{v}' "
                f"(pentads disaggregated with IMERG timing; not independent daily data); "
                f"window clip to {bounds}; nodata {c['nodata']}")


STEPS = {"dem": get_dem, "worldcover": get_worldcover, "osm": get_osm, "chirps": get_chirps}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", default=",".join(STEPS),
                    help=f"comma-separated subset of: {', '.join(STEPS)}")
    args = ap.parse_args()
    cfg = load_config()
    ensure_dirs()
    bounds = clip_bounds(cfg)
    print(f"Clip bounds (lon/lat): {bounds}")
    for name in args.only.split(","):
        print(f"\n== {name} ==")
        STEPS[name.strip()](cfg, bounds)


if __name__ == "__main__":
    main()
