# -*- utf-8 -*-
"""产品图管线：下载原图 → 有效性校验 → 去留白 → contain 缩放 → 缓存。"""

import hashlib
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import requests
from PIL import Image

from src.config import HTTP_HEADERS
from src.models import Sku
from src.paths import ensure_dirs

Image.MAX_IMAGE_PIXELS = 80_000_000


@dataclass
class ImageAsset:
    source_url: str
    raw_path: str = ""
    processed_path: str = ""
    status: str = "failed"          # ok / trimmed / failed
    width: int = 0
    height: int = 0
    trim_px: tuple = (0, 0, 0, 0)
    message: str = ""


class ImagePipeline:
    def __init__(
        self,
        brand: str,
        target_h_px: int = 177,
        max_w_px: int = 460,
        tolerance: int = 20,
    ) -> None:
        self.brand = brand
        self.target_h_px = target_h_px
        self.max_w_px = max_w_px
        self.tolerance = tolerance
        self.paths = ensure_dirs(brand)
        self.index_path = self.paths["images_processed"] / "index.json"
        self.index: dict[str, dict] = {}
        if self.index_path.exists():
            try:
                self.index = json.loads(self.index_path.read_text("utf-8"))
            except json.JSONDecodeError:
                self.index = {}

    # ---------- 对外 ----------
    def process(self, sku: Sku) -> ImageAsset:
        urls = [u for u in (sku.image_candidates or []) if u]
        if not urls and sku.image_url_raw:
            urls = [sku.image_url_raw]
        if not urls:
            return ImageAsset(source_url="", status="failed", message="无原图 URL")

        fallback: ImageAsset | None = None
        last_fail: ImageAsset | None = None
        for url in urls[:3]:
            asset = self._process_url(url)
            if asset.status in ("ok", "trimmed"):
                return asset
            if asset.status in ("banner", "infographic"):
                fallback = fallback or asset
            else:
                last_fail = asset
        if fallback:
            fallback.status = "suspect"
            fallback.message = "候选主图均为促销横幅/信息图，请人工复核"
            return fallback
        return last_fail or ImageAsset(source_url=urls[0], status="failed", message="候选图全部处理失败")

    def _process_url(self, url: str) -> ImageAsset:
        dl_key, proc_key = self._keys(url)
        cached = self.index.get(proc_key)
        if cached and Path(cached["processed_path"]).exists():
            return ImageAsset(**{k: cached[k] for k in ImageAsset.__dataclass_fields__})

        asset = ImageAsset(source_url=url)
        try:
            raw_path = self._download(url, dl_key)
            asset.raw_path = str(raw_path)
            with Image.open(raw_path) as im:
                im.load()
                asset = self._process_image(im, asset)
        except Exception as exc:  # 网络 / 解码失败都归为图片失败，交给审核页处理
            asset.status = "failed"
            asset.message = f"{type(exc).__name__}: {exc}"
        self.index[proc_key] = asdict(asset)
        return asset

    def _keys(self, url: str) -> tuple[str, str]:
        """下载缓存 key 固定（复用历史原图）；处理缓存 key 带版本号，规则升级后自动失效。"""
        base = f"{url}|{self.target_h_px}|{self.max_w_px}"
        dl_key = hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]
        proc_key = hashlib.sha1((base + "|v3").encode("utf-8")).hexdigest()[:16]
        return dl_key, proc_key

    def process_many(self, skus: list[Sku], workers: int = 4) -> dict[str, ImageAsset]:
        results: dict[str, ImageAsset] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self.process, s): s for s in skus if s.image_url_raw}
            for fut in as_completed(futures):
                sku = futures[fut]
                try:
                    results[sku.sku_id] = fut.result()
                except Exception as exc:
                    results[sku.sku_id] = ImageAsset(
                        source_url=sku.image_url_raw,
                        status="failed",
                        message=f"{type(exc).__name__}: {exc}",
                    )
        for sku in skus:
            if not sku.image_url_raw:
                results[sku.sku_id] = ImageAsset(status="failed", message="无原图 URL")
        self.save_index()
        return results

    def save_index(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(self.index, ensure_ascii=False, indent=2), "utf-8")

    # ---------- 内部 ----------
    def _download(self, url: str, key: str) -> Path:
        suffix = Path(url.split("?")[0]).suffix or ".jpg"
        out = self.paths["images_raw"] / f"{key}{suffix}"
        if out.exists() and out.stat().st_size > 1000:
            return out
        headers = dict(HTTP_HEADERS)
        headers["Referer"] = "https://www.greenworkstools.com/"
        last_err = None
        for attempt in range(3):
            resp = requests.get(url, headers=headers, timeout=40)
            if resp.status_code == 200 and len(resp.content) > 1000:
                out.write_bytes(resp.content)
                return out
            last_err = f"HTTP {resp.status_code}"
            time.sleep(1 + attempt)
        raise RuntimeError(f"图片下载失败：{last_err}")

    def _process_image(self, im: Image.Image, asset: ImageAsset) -> ImageAsset:
        img = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
        w, h = img.size
        if min(w, h) < 400:
            raise ValueError(f"图片尺寸过小：{w}x{h}")

        # 透明图先铺白底，再判断横幅色带与纯色
        if img.mode == "RGBA":
            rgba = img.convert("RGBA")
            flat = Image.new("RGB", img.size, (255, 255, 255))
            flat.paste(rgba, mask=rgba.getchannel("A"))
            arr = np.asarray(flat).astype(np.int16)
        else:
            arr = np.asarray(img.convert("RGB")).astype(np.int16)
        if float(arr.std()) < 4:
            raise ValueError("图片接近纯色，判定无效")

        banner = self._banner_like(arr)
        infographic = self._dark_background(arr)

        bbox, trimmed = self._content_bbox(img)
        if bbox is None:
            raise ValueError("未识别到产品主体")
        left, top, right, bottom = bbox
        if (right - left) * (bottom - top) < 0.03 * w * h:
            raise ValueError("产品主体占比过小，疑似 logo/占位图")

        # 保留少量内边距，避免切到产品本体
        pad = int(min(right - left, bottom - top) * 0.02)
        box = (
            max(0, left - pad),
            max(0, top - pad),
            min(w, right + pad),
            min(h, bottom + pad),
        )
        crop = img.crop(box)

        scale = min(self.max_w_px / crop.width, self.target_h_px / crop.height)
        new_size = (max(1, int(crop.width * scale)), max(1, int(crop.height * scale)))
        crop = crop.resize(new_size, Image.LANCZOS)

        canvas = Image.new("RGBA", (self.max_w_px, self.target_h_px), (0, 0, 0, 0))
        canvas.paste(crop, ((self.max_w_px - new_size[0]) // 2, (self.target_h_px - new_size[1]) // 2))

        key = hashlib.sha1(
            f"{asset.source_url}|{self.target_h_px}|{self.max_w_px}|v3".encode("utf-8")
        ).hexdigest()[:16]
        out = self.paths["images_processed"] / f"{key}.png"
        canvas.save(out)

        asset.processed_path = str(out)
        if infographic:
            asset.status = "infographic"
            asset.message = "深色背景的卖点信息图"
        elif banner:
            asset.status = "banner"
            asset.message = "顶部存在促销横幅色带"
        else:
            asset.status = "trimmed" if trimmed else "ok"
        asset.width, asset.height = new_size
        asset.trim_px = (left, top, w - right, h - bottom)
        return asset

    @staticmethod
    def _banner_like(arr: np.ndarray) -> bool:
        """顶部 15% 是满幅高饱和色带、底部接近纯白 → 促销横幅合成图。"""
        h = arr.shape[0]
        if h < 200:
            return False
        top = np.median(arr[: max(1, int(h * 0.15))].reshape(-1, 3), axis=0)
        bot = np.median(arr[int(h * 0.85):].reshape(-1, 3), axis=0)
        top_sat = float(top.max() - top.min())
        bot_sat = float(bot.max() - bot.min())
        gap = float(np.abs(top - bot).max())
        return top_sat >= 60 and bot_sat <= 20 and gap >= 60

    @staticmethod
    def _dark_background(arr: np.ndarray) -> bool:
        """四角接近深灰/黑 → 卖点信息图，不是干净的产品主图。"""
        edge = 8
        corners = np.concatenate(
            [
                arr[:edge, :edge].reshape(-1, 3),
                arr[:edge, -edge:].reshape(-1, 3),
                arr[-edge:, :edge].reshape(-1, 3),
                arr[-edge:, -edge:].reshape(-1, 3),
            ]
        )
        bg = np.median(corners, axis=0)
        return float(bg.mean()) < 80

    def _content_bbox(self, img: Image.Image) -> tuple[tuple[int, int, int, int] | None, bool]:
        w, h = img.size
        if img.mode == "RGBA":
            alpha = np.asarray(img.getchannel("A"))
            if int(alpha.min()) < 250 and float((alpha < 250).mean()) > 0.02:
                ys, xs = np.where(alpha > 8)
                if len(xs) == 0:
                    return None, False
                bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
                trimmed = bbox != (0, 0, w, h)
                return bbox, trimmed

        arr = np.asarray(img.convert("RGB")).astype(np.int16)
        edge = 8
        corners = np.concatenate(
            [
                arr[:edge, :edge].reshape(-1, 3),
                arr[:edge, -edge:].reshape(-1, 3),
                arr[-edge:, :edge].reshape(-1, 3),
                arr[-edge:, -edge:].reshape(-1, 3),
            ]
        )
        bg = np.median(corners, axis=0)
        diff = np.abs(arr - bg).max(axis=2)
        fg = diff > self.tolerance
        ys, xs = np.where(fg)
        if len(xs) == 0:
            return None, False
        bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        margins = [bbox[0], bbox[1], w - bbox[2], h - bbox[3]]
        trimmed = max(margins) > int(0.02 * max(w, h))
        return bbox, trimmed
