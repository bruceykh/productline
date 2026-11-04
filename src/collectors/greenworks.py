# -*- utf-8 -*-
"""Greenworks 采集器。

站点是 Shopify：
- 品类列表：/collections/{handle}/products.json?limit=250&page=N
- 商品详情：/products/{handle}（服务端渲染的规格表 .feature-item）

优先使用列表 JSON + 标题规则解析；只有在必采字段缺失时才请求详情页规格表。
"""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from src import normalize as nz
from src.collectors.base import ProgressCb
from src.config import (
    F_AH,
    F_BAT_COUNT,
    F_BAT_TEXT,
    F_BRUSHLESS,
    F_CFM,
    F_MPH,
    F_MSRP,
    F_PLATFORM,
    F_PRICE,
    F_PROPULSION,
    F_SIZE,
    HTTP_HEADERS,
    Brand,
    Category,
)
from src.models import Sku
from src.paths import ensure_dirs

SPEC_RE = re.compile(
    r'<div class="feature-item">\s*<span>(.*?)</span>\s*<strong>(.*?)</strong>',
    re.S,
)
# 部分 PDP 改用 <table class="product-specifications"> 的 <tr><td>标签</td><td><strong>值</strong></td></tr>
TABLE_SPEC_RE = re.compile(
    r'<table[^>]*class="[^"]*product-specifications[^"]*"[^>]*>(.*?)</table>',
    re.S,
)
ROW_SPEC_RE = re.compile(
    r"<tr>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*</tr>",
    re.S,
)
BAD_TITLE = (
    "replacement", "spare", "accessor", " combo", "combo kit", "spool",
    "gift card", "apparel", "merch", "open box", "renewed", "refurb",
)
MOWER_SKIP = ("riding", "robotic", "zero-turn", "zero turn", "reel")
# 油锯品类里混入的套装/附件（如 chainsaw + pole saw + hedge attachment）
CHAINSAW_SKIP = ("pole saw", "pruner saw", "attachment")
IMAGE_SKIP = ("hover", "lifestyle", "banner", "logo", "icon", "swatch")


class GreenworksCollector:
    def __init__(self, brand: Brand, min_interval: float = 2.0) -> None:
        self.brand = brand
        self.min_interval = min_interval
        self.paths = ensure_dirs(brand.key)
        self.session = requests.Session()
        self.session.headers.update(HTTP_HEADERS)
        self._last_request = 0.0

    # ---------- 网络 ----------
    def _request(self, url: str, cache_path: Path) -> requests.Response:
        if cache_path.exists() and cache_path.stat().st_size > 0:
            return _CachedResponse(cache_path.read_bytes())
        last_err = None
        for attempt in range(3):
            gap = time.time() - self._last_request
            if gap < self.min_interval:
                time.sleep(self.min_interval - gap)
            try:
                resp = self.session.get(url, timeout=40)
                self._last_request = time.time()
                if resp.status_code == 200 and resp.content:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_bytes(resp.content)
                    return resp
                last_err = f"HTTP {resp.status_code}"
            except requests.RequestException as exc:
                last_err = str(exc)
                self._last_request = time.time()
            time.sleep(2 ** attempt + 1)
        raise RuntimeError(f"请求失败：{url}（{last_err}）")

    def _get_json(self, url: str, cache_name: str) -> dict:
        resp = self._request(url, self.paths["raw"] / "json" / cache_name)
        return json.loads(resp.content)

    def _get_html(self, handle: str) -> str:
        url = f"{self.brand.base_url}/products/{handle}"
        resp = self._request(url, self.paths["raw"] / "pdp" / f"{handle}.html")
        return resp.content.decode("utf-8", "ignore")

    # ---------- 列表 ----------
    def list_products(self, category: Category) -> list[dict]:
        products: list[dict] = []
        page = 1
        while page <= 6:
            data = self._get_json(
                f"{self.brand.base_url}/collections/{category.collection}/products.json?limit=250&page={page}",
                f"{category.collection}-p{page}.json",
            )
            batch = data.get("products") or []
            if not batch:
                break
            products.extend(batch)
            if len(batch) < 250:
                break
            page += 1
        return products

    def keep_product(self, product: dict, category: Category) -> bool:
        title = nz.lower(product.get("title"))
        if any(k in title for k in BAD_TITLE):
            return False
        if re.match(r"^(battery|batteries|charger)\b", title):
            return False
        if category.key == "mower" and any(k in title for k in MOWER_SKIP):
            return False
        if category.key == "chainsaw" and any(k in title for k in CHAINSAW_SKIP):
            return False
        # 标题必须命中品类关键词，避免把 pole saw / pruner saw / shear 混进主体品类
        if not any(k in title for k in category.title_keywords):
            return False

        types = set()
        for tag in product.get("tags") or []:
            if tag.lower().startswith("tool type:"):
                types.add(tag.split(":", 1)[1].strip())
        if types & {"Accessories", "Spare Parts", "Combo Kits"}:
            return False
        return True

    # ---------- 详情 ----------
    def parse_specs(self, html: str) -> dict[str, str]:
        specs = {}
        for label, value in SPEC_RE.findall(html or ""):
            key = nz.clean(nz.clean(label))
            val = nz.clean(nz.clean(value))
            if key and val:
                specs.setdefault(key, val)
        # 只解析产品规格表，避免把页面里的其他 <tr><td> 表格（改价脚本等）混进来
        for block in TABLE_SPEC_RE.findall(html or ""):
            for label, value in ROW_SPEC_RE.findall(block):
                key = nz.clean(nz.clean(label))
                val = nz.clean(nz.clean(value))
                if key and val:
                    specs.setdefault(key, val)
        return specs

    def needs_detail(self, sku: Sku) -> bool:
        for key in (F_SIZE, F_CFM, F_PROPULSION, F_BRUSHLESS):
            if sku.get(key) is None:
                return True
        return False

    # ---------- 组装 ----------
    def build_skus(self, product: dict, category: Category) -> list[Sku]:
        title = nz.clean(product.get("title"))
        handle = product.get("handle") or str(product.get("id"))
        url = f"{self.brand.base_url}/products/{handle}"
        candidates = self.pick_images(product)
        image_url = candidates[0] if candidates else ""

        voltage = nz.parse_voltage(title)
        ah = nz.parse_ah(title)
        count = nz.parse_battery_count(title, ah is not None)
        size = nz.parse_inch(title)
        cfm = nz.parse_cfm(title)
        mph = nz.parse_mph(title)
        brushless = nz.parse_brushless(title)
        propulsion = nz.parse_propulsion(title)
        kit_or_bare = nz.detect_kit_or_bare(title, ah)

        # 电池配置原文：截取标题里电池相关片段
        bat_raw = ""
        m = re.search(r"(w/\s*)?(\(?[\w\s]*\d+(?:\.\d+)?\s*Ah[\w\s,&()\-]*)", title, re.I)
        if m:
            bat_raw = m.group(0).strip(" ,")

        skus: list[Sku] = []
        variants = product.get("variants") or [{}]
        for variant in variants:
            v_title = nz.clean(variant.get("title"))
            price = nz.to_float(variant.get("price"))
            compare = nz.to_float(variant.get("compare_at_price"))
            msrp = compare if compare and compare > (price or 0) else price
            msrp_source = "compare_at_price" if compare and compare > (price or 0) else "list_price"

            model_name = title
            code = handle
            if v_title and v_title.lower() != "default title":
                model_name = f"{title} · {v_title}"
                code = f"{handle}-{re.sub(r'[^a-z0-9]+', '-', v_title.lower()).strip('-')}"

            sku = Sku(
                brand=self.brand.key,
                category=category.key,
                model_name=model_name,
                sku_code=code,
                source_url=url,
                kit_or_bare=kit_or_bare,
                image_url_raw=image_url,
                image_candidates=candidates[:5],
                collected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                variant_title=v_title if v_title.lower() != "default title" else "",
            )
            if voltage is not None:
                sku.set(F_PLATFORM, f"{voltage}V", voltage, url, "json+rule")
            if ah is not None:
                sku.set(F_AH, f"{ah:g}Ah", ah, url, "json+rule")
            if count is not None:
                sku.set(F_BAT_COUNT, str(count), count, url, "json+rule")
            if bat_raw:
                sku.set(F_BAT_TEXT, bat_raw, bat_raw, url, "json+rule")
            if size is not None:
                sku.set(F_SIZE, f'{size:g}"', size, url, "json+rule")
            if cfm is not None:
                sku.set(F_CFM, f"{cfm} CFM", cfm, url, "json+rule")
            if mph is not None:
                sku.set(F_MPH, f"{mph} MPH", mph, url, "json+rule")
            if brushless is not None:
                sku.set(F_BRUSHLESS, "Brushless" if brushless else "Brushed", brushless, url, "json+rule")
            if propulsion is not None:
                sku.set(F_PROPULSION, propulsion, propulsion, url, "json+rule")
            if msrp is not None:
                sku.set(F_MSRP, nz.fmt_price(msrp), msrp, url, f"json:{msrp_source}")
            if price is not None:
                sku.set(F_PRICE, nz.fmt_price(price), price, url, "json")
            skus.append(sku)
        return skus

    def pick_images(self, product: dict) -> list[str]:
        """按 position 排序，排除 hover/场景/logo 图，返回候选主图列表。"""
        images = sorted(product.get("images") or [], key=lambda x: x.get("position", 99))
        out: list[str] = []
        seen: set[str] = set()
        for img in images:
            src = (img.get("src") or "").split("?")[0]
            name = src.rsplit("/", 1)[-1].lower()
            if not src or src in seen:
                continue
            if any(k in name for k in IMAGE_SKIP):
                continue
            seen.add(src)
            out.append(img.get("src"))
        return out

    # ---------- 主流程 ----------
    def collect(self, category: Category, limit: int = 0, progress: ProgressCb | None = None) -> list[Sku]:
        raw = self.list_products(category)
        kept = [p for p in raw if self.keep_product(p, category)]
        if progress:
            progress("列表", 0, len(kept), f"{category.zh}：{len(raw)} 个候选 → {len(kept)} 个有效")

        all_skus: list[Sku] = []
        for product in kept:
            all_skus.extend(self.build_skus(product, category))

        deduped: dict[str, Sku] = {}
        for sku in all_skus:
            deduped.setdefault(sku.sku_code, sku)
        # 同标题同 KIT 标记视为重复条目（不同 handle 但实际同一 SKU）
        by_model: dict[tuple, Sku] = {}
        for sku in deduped.values():
            key = (sku.category, nz.lower(sku.model_name), sku.kit_or_bare)
            by_model.setdefault(key, sku)
        deduped = {s.sku_code: s for s in by_model.values()}

        def sort_key(s: Sku):
            return (
                s.get(F_PLATFORM) or 0,
                s.get(F_SIZE) or s.get(F_CFM) or 0,
                s.get(F_MSRP) or 0,
            )

        ordered = sorted(deduped.values(), key=sort_key)
        if limit and limit > 0:
            ordered = ordered[:limit]

        # 只对入选 SKU 请求详情页，避免无谓的网络开销
        fetched: dict[str, dict[str, str]] = {}
        for idx, sku in enumerate(ordered, 1):
            handle = sku.source_url.rsplit("/products/", 1)[-1]
            if self.needs_detail(sku) and handle not in fetched:
                fetched[handle] = self.parse_specs(self._get_html(handle))
            specs = fetched.get(handle)
            if specs:
                nz.apply_specs(sku, specs, sku.source_url)
            if progress:
                progress("详情", idx, len(ordered), sku.model_name[:60])

        ordered.sort(key=sort_key)
        return ordered


class _CachedResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.status_code = 200
        self.text = content.decode("utf-8", "ignore")
