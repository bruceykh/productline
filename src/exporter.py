# -*- utf-8 -*-
"""Excel 明细导出：标准视图 + 字段溯源 + 质检 + 覆盖统计。"""

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from src.config import CATEGORIES, FIELD_LABELS
from src.models import Sku
from src.qa import Issue

HEADER_FILL = PatternFill("solid", fgColor="1F3B2C")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)
THIN = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(vertical="top", wrap_text=True)


def _norm(sku: Sku, key: str):
    return sku.get(key)


def _wide_columns() -> list[tuple[str, callable]]:
    return [
        ("品牌", lambda s: s.brand),
        ("品类", lambda s: CATEGORIES[s.category].zh if s.category in CATEGORIES else s.category),
        ("型号名称", lambda s: s.model_name),
        ("SKU编码", lambda s: s.sku_code),
        ("KIT/Bare", lambda s: s.kit_or_bare),
        ("尺寸(in)", lambda s: _norm(s, "size_in")),
        ("风量CFM", lambda s: _norm(s, "cfm")),
        ("风速MPH", lambda s: _norm(s, "mph")),
        ("Push/Self-Propelled", lambda s: _norm(s, "propulsion")),
        ("Brush/Brushless", lambda s: {True: "Brushless", False: "Brushed"}.get(_norm(s, "brushless"), "")),
        ("电池平台(V)", lambda s: _norm(s, "platform_voltage_v")),
        ("电池容量(Ah)", lambda s: _norm(s, "battery_ah")),
        ("电池数量", lambda s: _norm(s, "battery_count")),
        ("电池配置原文", lambda s: s.raw("battery_config_text")),
        ("现价", lambda s: _norm(s, "price_current")),
        ("MSRP", lambda s: _norm(s, "price_msrp")),
        ("MSRP来源", lambda s: s.fields["price_msrp"].method if "price_msrp" in s.fields else ""),
        ("来源链接", lambda s: s.source_url),
        ("原图URL", lambda s: s.image_url_raw),
        ("本地图片", lambda s: s.image_path),
        ("图片状态", lambda s: s.image_status),
        ("审核状态", lambda s: s.review_status),
        ("备注", lambda s: s.notes),
    ]


def export_excel(skus: list[Sku], issues: list[Issue], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "SKU明细"
    cols = _wide_columns()

    for c, (name, _) in enumerate(cols, 1):
        cell = ws.cell(row=1, column=c, value=name)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER
    ws.row_dimensions[1].height = 28

    for r, sku in enumerate(skus, 2):
        for c, (_name, getter) in enumerate(cols, 1):
            value = getter(sku)
            if isinstance(value, float):
                value = round(value, 2)
            cell = ws.cell(row=r, column=c, value=value)
            cell.border = BORDER
            cell.alignment = WRAP
            if c in (6, 11, 12, 15, 16):
                cell.alignment = Alignment(horizontal="center", vertical="top")

    widths = [10, 12, 52, 34, 9, 9, 9, 9, 16, 12, 10, 10, 8, 26, 10, 10, 16, 44, 52, 34, 9, 9, 24]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{max(1, len(skus) + 1)}"

    # ---- 字段溯源 ----
    ws2 = wb.create_sheet("字段溯源")
    headers = ["SKU编码", "品类", "字段", "raw_value", "normalized_value", "evidence_url", "method", "confidence"]
    for c, name in enumerate(headers, 1):
        cell = ws2.cell(row=1, column=c, value=name)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.border = BORDER
    r = 2
    for sku in skus:
        for key, fv in sku.fields.items():
            values = [
                sku.sku_code,
                CATEGORIES[sku.category].zh if sku.category in CATEGORIES else sku.category,
                FIELD_LABELS.get(key, key),
                fv.raw,
                fv.norm,
                fv.evidence_url,
                fv.method,
                fv.confidence,
            ]
            for c, v in enumerate(values, 1):
                cell = ws2.cell(row=r, column=c, value=v)
                cell.border = BORDER
                cell.alignment = WRAP
            r += 1
    for i, w in enumerate([34, 12, 18, 40, 18, 52, 18, 10], 1):
        ws2.column_dimensions[get_column_letter(i)].width = w
    ws2.freeze_panes = "A2"
    ws2.auto_filter.ref = f"A1:H{max(1, r - 1)}"

    # ---- 质检 ----
    ws3 = wb.create_sheet("质检")
    headers = ["检查项", "级别", "SKU", "说明"]
    for c, name in enumerate(headers, 1):
        cell = ws3.cell(row=1, column=c, value=name)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.border = BORDER
    for r, issue in enumerate(issues, 2):
        for c, v in enumerate([issue.check, issue.level, issue.sku, issue.message], 1):
            cell = ws3.cell(row=r, column=c, value=v)
            cell.border = BORDER
            cell.alignment = WRAP
            if c == 2 and issue.level == "error":
                cell.font = Font(color="C00000", bold=True)
    for i, w in enumerate([20, 8, 34, 80], 1):
        ws3.column_dimensions[get_column_letter(i)].width = w
    ws3.freeze_panes = "A2"
    ws3.auto_filter.ref = f"A1:D{max(1, len(issues) + 1)}"

    # ---- 覆盖统计 ----
    ws4 = wb.create_sheet("覆盖统计")
    for c, name in enumerate(["品类", "SKU数", "已审核", "图片失败"], 1):
        cell = ws4.cell(row=1, column=c, value=name)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.border = BORDER
    for r, (key, cat) in enumerate(CATEGORIES.items(), 2):
        items = [s for s in skus if s.category == key]
        values = [
            cat.zh,
            len(items),
            len([s for s in items if s.review_status == "approved"]),
            len([s for s in items if s.image_status == "failed"]),
        ]
        for c, v in enumerate(values, 1):
            cell = ws4.cell(row=r, column=c, value=v)
            cell.border = BORDER
    for i, w in enumerate([16, 10, 10, 12], 1):
        ws4.column_dimensions[get_column_letter(i)].width = w

    wb.save(path)
    return path
