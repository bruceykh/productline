# -*- utf-8 -*-
"""端到端编排：采集 → 图片 → 渲染 → 质检 → 导出。"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src import exporter, qa, store
from src.collectors.greenworks import GreenworksCollector
from src.config import BRANDS, CATEGORIES, CATEGORY_ORDER, Brand, Category
from src.images import ImageAsset, ImagePipeline
from src.models import Sku
from src.paths import ensure_dirs
from src.render import RenderOptions, render_matrix

ProgressCb = Callable[[str, int, int, str], None]


@dataclass
class RunResult:
    brand: str
    png_path: Path
    excel_path: Path
    manifest_path: Path
    skus: list[Sku]
    issues: list[qa.Issue]
    stats: dict

    @property
    def blocking_errors(self) -> int:
        return len([i for i in self.issues if i.level == "error"])


def get_collector(brand_key: str) -> GreenworksCollector:
    brand = BRANDS[brand_key]
    if not brand.enabled:
        raise NotImplementedError(f"{brand.name} 的采集适配器尚未接入（第一版先验证 Greenworks）")
    if brand.adapter == "shopify_greenworks":
        return GreenworksCollector(brand)
    raise NotImplementedError(f"未知适配器：{brand.adapter}")


def collect(brand_key: str, categories: list[str], limit: int, progress: ProgressCb | None = None) -> list[Sku]:
    brand: Brand = BRANDS[brand_key]
    collector = get_collector(brand_key)
    result: list[Sku] = []
    for i, key in enumerate(categories, 1):
        cat: Category = CATEGORIES[key]
        if progress:
            progress("品类", i, len(categories), f"开始采集 {cat.zh}")
        result.extend(collector.collect(cat, limit=limit, progress=progress))
    return result


def process_images(brand_key: str, skus: list[Sku], opts: RenderOptions, progress: ProgressCb | None = None) -> dict[str, ImageAsset]:
    brand_dir = ensure_dirs(brand_key)
    pipe = ImagePipeline(
        brand_key,
        target_h_px=opts.px(opts.img_h_mm),
        max_w_px=opts.px(opts.card_w_mm) - 2 * int(opts.px(opts.card_w_mm) * 0.04),
    )
    if progress:
        progress("图片", 0, len(skus), "下载原图并去留白")
    assets = pipe.process_many(skus)
    for sku in skus:
        asset = assets.get(sku.sku_id)
        if not asset:
            continue
        sku.image_path = asset.processed_path
        sku.image_raw_path = asset.raw_path
        sku.image_trim_margins = list(asset.trim_px or [])
        sku.image_status = asset.status
        if asset.source_url:
            sku.image_url_raw = asset.source_url
        if asset.status == "failed":
            sku.notes = (sku.notes + "；" if sku.notes else "") + f"图片失败：{asset.message[:80]}"
        elif asset.status == "suspect":
            sku.notes = (sku.notes + "；" if sku.notes else "") + "图片疑似促销横幅，待人工复核"
    if progress:
        ok = len([a for a in assets.values() if a.status != "failed"])
        progress("图片", len(skus), len(skus), f"图片完成 {ok}/{len(skus)}")
    return assets


def render_and_export(
    brand_key: str,
    skus: list[Sku],
    opts: RenderOptions,
    categories: list[str] | None = None,
) -> RunResult:
    brand = BRANDS[brand_key]
    dirs = ensure_dirs(brand_key)
    out_dir = dirs["output"]
    png = out_dir / f"{brand_key}_ope_matrix.png"
    manifest_path = out_dir / f"{brand_key}_matrix_manifest.json"
    excel = out_dir / f"{brand_key}_ope_skus.xlsx"

    selected = [s for s in skus if categories is None or s.category in categories]
    selected.sort(key=lambda s: (CATEGORY_ORDER.index(s.category), s.get("platform_voltage_v") or 0, s.get("size_in") or s.get("cfm") or 0, s.get("price_msrp") or 0))

    manifest = render_matrix(selected, brand.name, png, manifest_path, opts)
    issues = qa.run_checks(selected, manifest)
    exporter.export_excel(selected, issues, excel)

    stats = {
        "skus": len(selected),
        "by_category": {k: len([s for s in selected if s.category == k]) for k in CATEGORY_ORDER},
        "qa": qa.summary(issues),
        "png": str(png),
        "excel": str(excel),
    }
    (out_dir / f"{brand_key}_run_summary.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), "utf-8"
    )
    return RunResult(brand_key, png, excel, manifest_path, selected, issues, stats)


def run_all(
    brand_key: str,
    categories: list[str],
    limit: int = 0,
    opts: RenderOptions | None = None,
    progress: ProgressCb | None = None,
    refresh: bool = True,
) -> RunResult:
    opts = opts or RenderOptions()
    if refresh:
        new_skus = collect(brand_key, categories, limit, progress)
        existing = store.load_skus(brand_key)
        skus = store.merge_skus(existing, new_skus, categories)
        store.save_skus(brand_key, skus)
    else:
        skus = store.load_skus(brand_key)

    process_images(brand_key, skus, opts, progress)
    store.save_skus(brand_key, skus)
    return render_and_export(brand_key, skus, opts, categories)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="OPE 竞品产品线调研")
    parser.add_argument("--brand", default="greenworks")
    parser.add_argument("--categories", default=",".join(CATEGORY_ORDER))
    parser.add_argument("--limit", type=int, default=0, help="每个品类最多保留多少个 SKU，0 为全部")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--no-refresh", action="store_true", help="只重新渲染，不重新采集")
    args = parser.parse_args()

    cats = [c.strip() for c in args.categories.split(",") if c.strip()]

    def progress(stage: str, done: int, total: int, msg: str) -> None:
        print(f"[{stage}] {done}/{total} {msg}", flush=True)

    result = run_all(
        args.brand,
        cats,
        limit=args.limit,
        opts=RenderOptions(dpi=args.dpi),
        progress=progress,
        refresh=not args.no_refresh,
    )
    print(json.dumps(result.stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
