"""Download helper that records every raw file in data/raw/MANIFEST.csv.

ToR §4 and §9: every layer's source, date and licence must be stated.
Implementation Plan §11 Day 2: MANIFEST.csv holds URL, download date,
licence and checksum for each raw file.
"""
import csv
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import requests
from tqdm import tqdm

from flood.config import MANIFEST, ROOT

FIELDS = ["file", "url", "source", "licence", "downloaded_utc", "bytes", "sha256"]


def sha256sum(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _record(path: Path, url: str, source: str, licence: str) -> None:
    rows = []
    if MANIFEST.exists():
        with open(MANIFEST, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    rel = path.relative_to(ROOT).as_posix()
    rows = [r for r in rows if r["file"] != rel]
    rows.append({
        "file": rel,
        "url": url,
        "source": source,
        "licence": licence,
        "downloaded_utc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bytes": path.stat().st_size,
        "sha256": sha256sum(path),
    })
    rows.sort(key=lambda r: r["file"])
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def download(url: str, dest: Path, source: str, licence: str,
             overwrite: bool = False) -> Path:
    """Download url to dest (skipped if present) and record it in the manifest."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not overwrite:
        print(f"[skip] {dest.relative_to(ROOT)} already present")
    else:
        tmp = dest.with_suffix(dest.suffix + ".part")
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0)) or None
            with open(tmp, "wb") as f, tqdm(total=total, unit="B", unit_scale=True,
                                            desc=dest.name) as bar:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                    bar.update(len(chunk))
        tmp.replace(dest)
    _record(dest, url, source, licence)
    return dest
