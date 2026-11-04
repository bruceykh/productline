# -*- utf-8 -*-
"""字段解析与归一化：把网页原文转成标准值，并生成矩阵卡片文案。"""

import re
from typing import Any

from src.config import (
    F_AH,
    F_ATTACH,
    F_BAT_COUNT,
    F_BAT_TEXT,
    F_BRAKE,
    F_BRUSHLESS,
    F_CFM,
    F_CHAIN_SPEED,
    F_CUT_CAP,
    F_DRIVE,
    F_HANDHELD,
    F_HEIGHTS,
    F_MH,
    F_MPH,
    F_MSRP,
    F_PLATFORM,
    F_POLE,
    F_PRICE,
    F_PROPULSION,
    F_SIZE,
)
from src.models import Sku

INCH_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:[\"”″]|''|(?:-|\s)inch(?:es)?\b)", re.I)
VOLT_RE = re.compile(r"(\d{2,3}(?:\.\d+)?)\s*(?:V\b|Volt)", re.I)
AH_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:Ah|Amp[-\s]?Hour)", re.I)
CFM_RE = re.compile(r"(\d{2,4})\s*cfm", re.I)
MPH_RE = re.compile(r"(\d{2,3})\s*mph", re.I)
COUNT_RE = re.compile(r"(?:\(|（)\s*(\d)\s*(?:\)|）)|(\d)\s*[x×]\s*\d|(\b(?:two|three|four)\b)\s*\(", re.I)
NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4}


def clean(text: Any) -> str:
    if text is None:
        return ""
    s = str(text).replace("\u00a0", " ")
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def parse_inch(text: str) -> float | None:
    m = INCH_RE.search(text or "")
    return float(m.group(1)) if m else None


def parse_voltage(text: str) -> int | None:
    m = VOLT_RE.search(text or "")
    if not m:
        return None
    v = float(m.group(1))
    return int(v) if v == int(v) else round(v, 1)


def parse_ah(text: str) -> float | None:
    m = AH_RE.search(text or "")
    return float(m.group(1)) if m else None


def parse_cfm(text: str) -> int | None:
    m = CFM_RE.search(text or "")
    return int(m.group(1)) if m else None


def parse_mph(text: str) -> int | None:
    m = MPH_RE.search(text or "")
    return int(m.group(1)) if m else None


def parse_battery_count(text: str, has_ah: bool) -> int | None:
    """从 'Two (2) 4.0Ah' / '2x 4.0Ah' / '(2) Batteries' 推断电池数量。"""
    t = text or ""
    m = COUNT_RE.search(t)
    if m:
        for g in m.groups():
            if not g:
                continue
            if g.isdigit():
                return int(g)
            if g.lower() in NUM_WORDS:
                return NUM_WORDS[g.lower()]
    if re.search(r"\b(d\+?|dual)[- ]?battery\b", t, re.I):
        return 2
    return 1 if has_ah else None


def parse_propulsion(text: str) -> str | None:
    t = lower(text)
    if "self-propelled" in t or "self propelled" in t or re.search(r"\bsp\b", t):
        return "self-propelled"
    if re.search(r"\bpush\b", t):
        return "push"
    return None


def parse_brushless(text: str) -> bool | None:
    t = lower(text)
    if "brushless" in t:
        return True
    if "brushed" in t:
        return False
    return None


def detect_kit_or_bare(title: str, ah: float | None) -> str:
    t = lower(title)
    if "tool only" in t or "bare tool" in t or re.search(r"\bbare\b", t):
        return "bare"
    if ah is not None or re.search(r"\bw/\s", t) or "battery" in t:
        return "kit"
    return "unknown"


def lower(text: Any) -> str:
    return clean(text).lower()


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def spec_get(specs: dict[str, str], *names: str) -> str:
    """按标签名忽略大小写地从 PDP 规格表取值。"""
    if not specs:
        return ""
    norm = {re.sub(r"[^a-z0-9]+", "", k.lower()): v for k, v in specs.items()}
    for name in names:
        key = re.sub(r"[^a-z0-9]+", "", name.lower())
        if key in norm:
            return norm[key]
    return ""


def fmt_price(value: Any) -> str:
    v = to_float(value)
    if v is None:
        return "—"
    return f"${v:,.2f}"


def fmt_battery(voltage: Any, ah: Any, count: Any) -> str:
    parts = []
    if voltage not in (None, ""):
        parts.append(f"{int(voltage)}V" if float(voltage) == int(voltage) else f"{voltage}V")
    if ah not in (None, ""):
        prefix = f"{int(count)}×" if count and int(count) > 1 else ""
        parts.append(f"{prefix}{ah:g}Ah")
    return " ".join(parts) if parts else "—"


SPEC_LABELS = {
    F_SIZE: ("Deck Size", "Cutting Width", "Cutting Swath", "Blade Length", "Bar Length", "Guide Bar Length"),
    F_CFM: ("Max CFM", "Air Volume", "Blower CFM", "CFM"),
    F_MPH: ("Max Air Speed", "Air Speed", "MPH"),
    F_PROPULSION: ("Drive System", "Self-Propelled", "Propulsion"),
    F_HEIGHTS: ("Cutting Heights", "Height Positions"),
    F_BRUSHLESS: ("Motor Type", "Brushless Motor"),
    F_CHAIN_SPEED: ("Chain Speed",),
    F_BRAKE: ("Chain Brake", "Safety Brake"),
    F_CUT_CAP: ("Cutting Capacity", "Cut Capacity"),
    F_POLE: ("Pole Hedge Trimmer",),
}


def apply_specs(sku: Sku, specs: dict[str, str], evidence: str) -> None:
    """用 PDP 规格表补齐/校正标题里缺失的字段。"""
    if not specs:
        return

    def fill(key: str, value: Any, raw: str, method: str = "html") -> None:
        if value in (None, ""):
            return
        old = sku.fields.get(key)
        if old and old.norm not in (None, ""):
            return
        sku.set(key, raw, value, evidence, method)

    if sku.category in ("mower", "trimmer", "chainsaw", "hedge"):
        raw = spec_get(specs, *SPEC_LABELS[F_SIZE])
        if parse_inch(raw):
            fill(F_SIZE, parse_inch(raw), raw)

    raw_cfm = spec_get(specs, *SPEC_LABELS[F_CFM])
    if parse_cfm(raw_cfm):
        fill(F_CFM, parse_cfm(raw_cfm), raw_cfm)

    raw_mph = spec_get(specs, *SPEC_LABELS[F_MPH])
    if parse_mph(raw_mph):
        fill(F_MPH, parse_mph(raw_mph), raw_mph)

    raw_prop = spec_get(specs, *SPEC_LABELS[F_PROPULSION])
    if raw_prop:
        prop = parse_propulsion(raw_prop)
        if prop:
            fill(F_PROPULSION, prop, raw_prop)

    raw_h = spec_get(specs, *SPEC_LABELS[F_HEIGHTS])
    if raw_h.isdigit():
        fill(F_HEIGHTS, int(raw_h), raw_h)

    raw_motor = spec_get(specs, *SPEC_LABELS[F_BRUSHLESS])
    if raw_motor:
        b = parse_brushless(raw_motor)
        if b is None and raw_motor.strip().lower() in ("yes", "no"):
            b = raw_motor.strip().lower() == "yes"
        if b is not None:
            fill(F_BRUSHLESS, b, raw_motor)

    raw_chain = spec_get(specs, *SPEC_LABELS[F_CHAIN_SPEED])
    if raw_chain:
        fill(F_CHAIN_SPEED, clean(raw_chain), raw_chain)

    raw_brake = spec_get(specs, *SPEC_LABELS[F_BRAKE])
    if raw_brake:
        fill(F_BRAKE, clean(raw_brake), raw_brake)

    raw_cut = spec_get(specs, *SPEC_LABELS[F_CUT_CAP])
    if parse_inch(raw_cut):
        fill(F_CUT_CAP, parse_inch(raw_cut), raw_cut)

    raw_pole = spec_get(specs, *SPEC_LABELS[F_POLE])
    if raw_pole:
        yn = raw_pole.strip().lower()
        if yn in ("yes", "no"):
            fill(F_POLE, yn == "yes", raw_pole)
        else:
            b = parse_brushless(raw_pole)
            fill(F_POLE, b if b is not None else clean(raw_pole), raw_pole)


def build_spec_lines(sku: Sku, category: str) -> list[str]:
    """生成卡片下方的文案。顺序严格按用户的约定。"""
    size = sku.get(F_SIZE)
    size_txt = f'{size:g}"' if isinstance(size, (int, float)) else "—"
    battery = fmt_battery(sku.get(F_PLATFORM), sku.get(F_AH), sku.get(F_BAT_COUNT))
    price = fmt_price(sku.get(F_MSRP))

    if category == "mower":
        prop = sku.get(F_PROPULSION)
        prop_txt = {"push": "Push", "self-propelled": "Self-Propelled"}.get(prop, "—")
        lines = [size_txt, prop_txt, battery, price]
    elif category == "blower":
        cfm = sku.get(F_CFM)
        lines = [f"{int(cfm)} CFM" if cfm else "—", battery, price]
    else:
        lines = [size_txt, battery, price]

    if sku.kit_or_bare == "bare":
        lines.append("Bare Tool")
    return lines
