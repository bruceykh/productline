# -*- utf-8 -*-
"""数据落盘：每个品牌的 SKU 清单保存为 JSON。"""

import json
from pathlib import Path

from src.models import Sku
from src.paths import ensure_dirs


def sku_file(brand: str) -> Path:
    return ensure_dirs(brand)["brand"] / "skus.json"


def load_skus(brand: str) -> list[Sku]:
    path = sku_file(brand)
    if not path.exists():
        return []
    data = json.loads(path.read_text("utf-8"))
    return [Sku.from_dict(d) for d in data]


def save_skus(brand: str, skus: list[Sku]) -> None:
    path = sku_file(brand)
    path.write_text(
        json.dumps([s.to_dict() for s in skus], ensure_ascii=False, indent=2),
        "utf-8",
    )


def merge_skus(existing: list[Sku], incoming: list[Sku], categories: list[str]) -> list[Sku]:
    """同一品牌下按 sku_code 合并；只替换本次采集的品类，其他品类保留。"""
    kept = {s.sku_code: s for s in existing if s.category not in categories}
    for s in incoming:
        kept[s.sku_code] = s
    return list(kept.values())
