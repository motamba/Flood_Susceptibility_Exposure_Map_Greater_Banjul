"""Load config/config.yaml and expose standard project paths.

Implementation Plan §4: every parameter lives in one config file so that
results can be reproduced and every choice is visible (ToR §9).
"""
import os
import sys
from pathlib import Path

import yaml

# When a script runs with the environment's python without "activating" the
# environment, GDAL/PROJ cannot find their data files. Point them at it.
_share = Path(sys.prefix) / "share"
for _var, _dir in (("PROJ_DATA", _share / "proj"), ("GDAL_DATA", _share / "gdal")):
    if _var not in os.environ and _dir.exists():
        os.environ[_var] = str(_dir)

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "config.yaml"

DATA_RAW = ROOT / "data" / "raw"
DATA_INTERIM = ROOT / "data" / "interim"
DATA_PROCESSED = ROOT / "data" / "processed"
FIGURES = ROOT / "outputs" / "figures"
TABLES = ROOT / "outputs" / "tables"
WEB_DATA = ROOT / "docs" / "data"
MANIFEST = DATA_RAW / "MANIFEST.csv"


def load_config(path: Path = CONFIG_PATH) -> dict:
    """Return the parsed configuration dictionary."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def ensure_dirs() -> None:
    """Create the standard output folders if they do not exist."""
    for d in (DATA_RAW, DATA_INTERIM, DATA_PROCESSED, FIGURES, TABLES, WEB_DATA):
        d.mkdir(parents=True, exist_ok=True)


def active_weights(cfg: dict) -> dict[str, float]:
    """Return factor weights, rescaled to sum to 1 if land cover is switched off.

    Implementation Plan §5.2: fallback weights when land cover is dropped.
    """
    weights = {k: v["weight"] for k, v in cfg["factors"].items()}
    if not cfg.get("use_landcover", True):
        weights.pop("landcover", None)
    total = sum(weights.values())
    return {k: w / total for k, w in weights.items()}
