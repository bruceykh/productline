# -*- utf-8 -*-
"""产品线矩阵渲染：5 个横行，SKU 过多时自动在当前行下方换行。"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from src import normalize as nz
from src.config import CATEGORIES, CATEGORY_ORDER
from src.models import Sku

def _find_fonts() -> tuple[str | None, str | None]:
    """多平台字体探测：优先 CJK 字体（msyh / Noto Sans CJK / PingFang），fallback 到系统默认。"""
    candidates = [
        # Windows
        ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"),
        # Linux（Streamlit Cloud / 一般发行版）
        ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
         "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
         "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
         "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
        # macOS
        ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/PingFang.ttc"),
    ]
    for reg, bold in candidates:
        if Path(reg).exists() and Path(bold).exists():
            return reg, bold
    return None, None


FONT_REG, FONT_BOLD = _find_fonts()


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD if bold else FONT_REG
    if path:
        return ImageFont.truetype(path, size)
    # fallback：PIL 默认（DejaVu，仅英文）。PNG 里实际上全是英文，不会出问题。
    return ImageFont.load_default()


@dataclass
class RenderOptions:
    page_width_mm: float = 420
    margin_mm: float = 12
    label_w_mm: float = 30
    card_w_mm: float = 42
    gap_mm: float = 3
    img_h_mm: float = 15
    dpi: int = 300
    brand_color: str = "#2e7d32"

    def px(self, mm: float) -> int:
        return int(round(mm / 25.4 * self.dpi))


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if draw.textlength(text[:mid] + ell, font=font) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + ell


def _wrap_title(draw, text: str, font, max_w: int, lines: int = 2) -> list[str]:
    words = text.split()
    out: list[str] = []
    cur = ""
    for word in words:
        cand = f"{cur} {word}".strip()
        if draw.textlength(cand, font=font) <= max_w:
            cur = cand
        else:
            if cur:
                out.append(cur)
            cur = word
    if cur:
        out.append(cur)
    if len(out) > lines:
        out = out[: lines - 1] + [" ".join(out[lines - 1:])]
    out = [_ellipsize(draw, line, font, max_w) for line in out]
    while len(out) < lines:
        out.append("")
    return out[:lines]


def _wrap_lines(draw, text: str, font, max_w: int, max_lines: int = 3) -> list[str]:
    words = text.replace("/", " / ").split()
    out: list[str] = []
    cur = ""
    for word in words:
        if word == "/" and not cur:
            continue
        cand = f"{cur} {word}".strip()
        if draw.textlength(cand, font=font) <= max_w:
            cur = cand
        else:
            if cur:
                out.append(cur)
            cur = word
    if cur:
        out.append(cur)
    return [_ellipsize(draw, line, font, max_w) for line in out[:max_lines]] or [text]


def _dashed_line(draw, x0, y, x1, dash=12, gap=8, width=3, fill="#9a9a9a") -> None:
    x = x0
    while x < x1:
        draw.line([(x, y), (min(x + dash, x1), y)], fill=fill, width=width)
        x += dash + gap


def render_matrix(
    skus: list[Sku],
    brand_name: str,
    out_png: str | Path,
    manifest_path: str | Path | None = None,
    options: RenderOptions | None = None,
) -> dict:
    opt = options or RenderOptions()
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)

    grouped: dict[str, list[Sku]] = {key: [] for key in CATEGORY_ORDER}
    for sku in skus:
        if sku.category in grouped:
            grouped[sku.category].append(sku)

    margin = opt.px(opt.margin_mm)
    label_w = opt.px(opt.label_w_mm)
    card_w = opt.px(opt.card_w_mm)
    gap = opt.px(opt.gap_mm)
    img_h = opt.px(opt.img_h_mm)
    page_w = opt.px(opt.page_width_mm)
    inner_w = page_w - 2 * margin - label_w
    capacity = max(1, (inner_w + gap) // (card_w + gap))

    f_header = _font(opt.px(7), True)
    f_sub = _font(opt.px(3.2))
    f_label = _font(opt.px(3.2), True)
    f_title = _font(opt.px(2.6))
    f_spec = _font(opt.px(2.9))
    f_price = _font(opt.px(3.2), True)
    f_note = _font(opt.px(2.4))

    title_h = int(opt.px(2.6) * 1.25) * 2
    spec_h = int(opt.px(2.9) * 1.3)
    card_h = img_h + title_h + spec_h * 5 + opt.px(3)
    header_h = opt.px(24)
    block_gap = opt.px(10)

    # 先算总高度
    total_h = margin + header_h
    for key in CATEGORY_ORDER:
        n = len(grouped[key])
        if n:
            rows = max(1, -(-n // capacity))
            total_h += rows * (card_h + gap) + block_gap
        else:
            total_h += opt.px(14) + block_gap
    total_h += opt.px(16)

    page = Image.new("RGB", (page_w, total_h), "white")
    draw = ImageDraw.Draw(page)

    # ---- 页头 ----
    draw.text((margin, margin), f"{brand_name}", font=f_header, fill="#111111")
    header_w = draw.textlength(brand_name, font=f_header)
    draw.text(
        (margin + header_w + opt.px(4), margin + opt.px(4.5)),
        f"OPE 产品线矩阵 · {len(skus)} SKUs · {datetime.now().strftime('%Y-%m-%d')} · {opt.dpi}dpi",
        font=f_sub,
        fill="#666666",
    )
    legend = "字段顺序  Mower: 尺寸 / Push·Self-Propelled / 电池 / MSRP    Blower: CFM / 电池 / MSRP    其他: 尺寸 / 电池 / MSRP"
    draw.text((margin, margin + opt.px(11)), legend, font=f_note, fill="#888888")
    draw.line(
        [(margin, margin + header_h - 4), (page_w - margin, margin + header_h - 4)],
        fill=opt.brand_color,
        width=4,
    )

    y = margin + header_h
    manifest_cards = []

    for key in CATEGORY_ORDER:
        cat = CATEGORIES[key]
        items = grouped[key]
        if items:
            rows = max(1, -(-len(items) // capacity))
            block_h = rows * (card_h + gap)
        else:
            rows = 0
            block_h = opt.px(14)

        # 行标题（按列宽自动折行，最多 3 行）
        ly = y + opt.px(1)
        for line in _wrap_lines(draw, cat.label, f_label, label_w - opt.px(2), 3):
            draw.text((margin, ly), line, font=f_label, fill=opt.brand_color)
            ly += int(f_label.size * 1.22)
        draw.text((margin, ly + 2), f"{len(items)} SKU", font=f_note, fill="#999999")

        if not items:
            draw.text(
                (margin + label_w, y + opt.px(3)),
                "该品类暂无有效 SKU",
                font=f_sub,
                fill="#bbbbbb",
            )

        x0 = margin + label_w
        for idx, sku in enumerate(items):
            row = idx // capacity
            col = idx % capacity
            cx = x0 + col * (card_w + gap)
            cy = y + row * (card_h + gap)
            _draw_card(page, draw, sku, cx, cy, card_w, card_h, img_h, f_title, f_spec, f_price)
            manifest_cards.append(
                {
                    "sku_id": sku.sku_id,
                    "category": key,
                    "x": cx,
                    "y": cy,
                    "w": card_w,
                    "h": card_h,
                }
            )

        # 子行之间浅虚线，品类末尾深虚线
        for r in range(1, rows):
            ly2 = y + r * (card_h + gap) - gap // 2
            _dashed_line(draw, x0, ly2, page_w - margin, dash=5, gap=10, width=1, fill="#dddddd")

        y += block_h
        _dashed_line(draw, margin, y + block_gap // 2, page_w - margin, dash=14, gap=10, width=3, fill="#8a8a8a")
        y += block_gap

    draw.text(
        (margin, y + 2),
        "图片按 contain 等比缩放（高度 1.5cm），未裁切产品本体；MSRP 优先取 compare_at_price，其次取当前挂牌价。",
        font=f_note,
        fill="#999999",
    )

    page.save(out_png, "PNG")

    manifest = {
        "brand": brand_name,
        "generated_at": datetime.now().isoformat(timespec="minutes"),
        "png": str(out_png),
        "dpi": opt.dpi,
        "capacity_per_row": capacity,
        "total_skus": len(skus),
        "category_counts": {k: len(v) for k, v in grouped.items()},
        "cards": manifest_cards,
        "options": asdict(opt),
    }
    if manifest_path:
        Path(manifest_path).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    return manifest


def _draw_card(page, draw, sku: Sku, x, y, w, h, img_h, f_title, f_spec, f_price) -> None:
    pad = int(w * 0.04)
    draw.rounded_rectangle(
        [(x, y), (x + w, y + h)],
        radius=int(w * 0.03),
        outline="#e2e2e2",
        width=2,
    )

    # 图片区（已按 contain 处理好）
    img_w = w - pad * 2
    if sku.image_path and Path(sku.image_path).exists():
        with Image.open(sku.image_path) as im:
            im = im.convert("RGBA")
            if im.width > img_w or im.height > img_h:
                ratio = min(img_w / im.width, img_h / im.height)
                im = im.resize((int(im.width * ratio), int(im.height * ratio)), Image.LANCZOS)
            px = x + (w - im.width) // 2
            py = y + pad + (img_h - im.height) // 2
            page.paste(im, (px, py), im)
    else:
        draw.rectangle(
            [(x + pad, y + pad), (x + w - pad, y + pad + img_h)],
            outline="#eeeeee",
        )
        draw.text(
            (x + pad + 6, y + pad + img_h // 2 - 14),
            "无图",
            font=f_spec,
            fill="#cccccc",
        )

    # 标题（两行）
    ty = y + pad + img_h + int(f_title.size * 0.2)
    lines = _wrap_title(draw, sku.model_name, f_title, img_w, 2)
    for line in lines:
        draw.text((x + pad, ty), line, font=f_title, fill="#222222")
        ty += int(f_title.size * 1.25)

    # 规格文案
    sy = ty + 2
    specs = nz.build_spec_lines(sku, sku.category)
    for i, line in enumerate(specs):
        is_price = i == len(specs) - 1 - (1 if sku.kit_or_bare == "bare" else 0)
        font = f_price if is_price else f_spec
        color = "#111111" if is_price else "#555555"
        draw.text((x + pad, sy), line, font=font, fill=color)
        sy += int(f_spec.size * 1.3)
