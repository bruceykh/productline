# -*- utf-8 -*-
"""运行时目录与通用工具。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
RAW_DIR = DATA_DIR / "raw"


def ensure_dirs(brand: str) -> dict[str, Path]:
    paths = {
        "brand": DATA_DIR / brand,
        "raw": DATA_DIR / brand / "raw",
        "images_raw": DATA_DIR / brand / "images_raw",
        "images_processed": DATA_DIR / brand / "images_processed",
        "output": OUTPUT_DIR / brand,
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths
