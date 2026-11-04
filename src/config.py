# -*- coding: utf-8 -*-
"""品牌 / 品类 / 字段配置。

新增品牌或品类时只需要改这里 + 增加对应 collector 实现。
"""

from dataclasses import dataclass

# ---------- 字段 key ----------
F_SIZE = "size_in"                 # mower/trimmer 切割宽度、chainsaw 导板、hedge 刀片（英寸）
F_PROPULSION = "propulsion"        # push / self-propelled
F_CFM = "cfm"
F_MPH = "mph"
F_BRUSHLESS = "brushless"
F_DRIVE = "drive_type"
F_HEIGHTS = "height_positions"     # 割草高度档位
F_ATTACH = "attachment_capable"    # 是否支持 Attachment
F_MH = "multi_head"                # 是否 Multi-Head
F_HANDHELD = "handheld_or_backpack"
F_CHAIN_SPEED = "chain_speed"
F_BRAKE = "safety_brake"
F_CUT_CAP = "cut_capacity_in"      # hedge 切割能力
F_POLE = "pole_hedge"              # 是否 Pole Hedge Trimmer
F_PLATFORM = "platform_voltage_v"  # 电池平台电压，与容量严格分离
F_AH = "battery_ah"                # 单块电池容量
F_BAT_COUNT = "battery_count"
F_BAT_TEXT = "battery_config_text"
F_MSRP = "price_msrp"
F_PRICE = "price_current"

# 展示到 Excel / 矩阵的字段中文名
FIELD_LABELS = {
    F_SIZE: "尺寸(in)",
    F_PROPULSION: "Push/Self-Propelled",
    F_CFM: "风量(CFM)",
    F_MPH: "风速(MPH)",
    F_BRUSHLESS: "Brush/Brushless",
    F_DRIVE: "驱动方式",
    F_HEIGHTS: "割草高度档位",
    F_ATTACH: "支持Attachment",
    F_MH: "Multi-Head",
    F_HANDHELD: "手持/背负",
    F_CHAIN_SPEED: "链速",
    F_BRAKE: "安全制动",
    F_CUT_CAP: "切割能力(in)",
    F_POLE: "Pole Hedge Trimmer",
    F_PLATFORM: "电池平台(V)",
    F_AH: "电池容量(Ah)",
    F_BAT_COUNT: "电池数量",
    F_BAT_TEXT: "电池配置原文",
    F_MSRP: "MSRP",
    F_PRICE: "现价",
}


@dataclass(frozen=True)
class Category:
    key: str
    label: str            # 矩阵行标题
    zh: str
    collection: str       # Greenworks Shopify collection handle
    tool_types: tuple     # 有效的 Tool Type 标签
    title_keywords: tuple # 标题兜底关键词
    required: tuple       # 必采字段
    optional: tuple       # 可选字段


CATEGORIES = {
    "mower": Category(
        key="mower",
        label="MOWER",
        zh="割草机",
        collection="csh-lawn-mowers",
        tool_types=("Lawn Mowers",),
        title_keywords=("mower",),
        required=(F_SIZE, F_PROPULSION, F_PLATFORM, F_AH, F_MSRP),
        optional=(F_BRUSHLESS, F_DRIVE, F_HEIGHTS),
    ),
    "trimmer": Category(
        key="trimmer",
        label="STRING TRIMMER / EDGER",
        zh="打草机/修边机",
        collection="csh-string-trimmers-edgers",
        tool_types=("String Trimmers & Edgers", "String Trimmers", "Edgers"),
        title_keywords=("trimmer", "edger"),
        required=(F_SIZE, F_PLATFORM, F_AH, F_MSRP),
        optional=(F_BRUSHLESS, F_ATTACH, F_MH),
    ),
    "blower": Category(
        key="blower",
        label="BLOWER",
        zh="吹风机",
        collection="csh-leaf-blowers",
        tool_types=("Leaf Blowers",),
        title_keywords=("blower",),
        required=(F_CFM, F_PLATFORM, F_AH, F_MSRP),
        optional=(F_MPH, F_BRUSHLESS, F_HANDHELD),
    ),
    "chainsaw": Category(
        key="chainsaw",
        label="CHAINSAW",
        zh="油锯",
        collection="csh-chainsaws",
        tool_types=("Chainsaws",),
        title_keywords=("chainsaw",),
        required=(F_SIZE, F_PLATFORM, F_AH, F_MSRP),
        optional=(F_BRUSHLESS, F_CHAIN_SPEED, F_BRAKE),
    ),
    "hedge": Category(
        key="hedge",
        label="HEDGE TRIMMER",
        zh="绿篱机",
        collection="csh-hedge-trimmers",
        tool_types=("Hedge Trimmers",),
        title_keywords=("hedge trimmer",),
        required=(F_SIZE, F_PLATFORM, F_AH, F_MSRP),
        optional=(F_BRUSHLESS, F_CUT_CAP, F_POLE),
    ),
}

CATEGORY_ORDER = ("mower", "trimmer", "blower", "chainsaw", "hedge")


@dataclass(frozen=True)
class Brand:
    key: str
    name: str
    adapter: str
    base_url: str
    entry_url: str
    amazon_fallback: bool = False
    enabled: bool = True


BRANDS = {
    "greenworks": Brand(
        key="greenworks",
        name="Greenworks",
        adapter="shopify_greenworks",
        base_url="https://www.greenworkstools.com",
        entry_url="https://www.greenworkstools.com/collections/csh-lawn-mowers",
        enabled=True,
    ),
    "ryobi": Brand(
        key="ryobi",
        name="RYOBI",
        adapter="ryobi",
        base_url="https://www.ryobitools.com",
        entry_url="https://www.ryobitools.com/outdoor",
        amazon_fallback=False,
        enabled=False,
    ),
    "worx": Brand(
        key="worx",
        name="WORX",
        adapter="worx",
        base_url="https://www.worx.com",
        entry_url="https://www.worx.com/outdoor-power-equipment",
        amazon_fallback=True,
        enabled=False,
    ),
    "craftsman": Brand(
        key="craftsman",
        name="CRAFTSMAN",
        adapter="craftsman",
        base_url="https://www.craftsman.com",
        entry_url="https://www.craftsman.com/lawn-garden",
        amazon_fallback=False,
        enabled=False,
    ),
}

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
