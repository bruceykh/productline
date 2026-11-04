# -*- utf-8 -*-
"""导出前质检：8 项固定检查 + 必采字段缺失检查。"""

from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
from PIL import Image

from src import normalize as nz
from src.config import CATEGORIES, F_AH, F_CFM, F_MSRP, F_PLATFORM, F_PROPULSION
from src.models import Sku

@dataclass
class Issue:
    check: str
    level: str          # error / warn
    sku: str
    message: str


def check_duplicates(skus: list[Sku]) -> list[Issue]:
    issues = []
    seen_code: dict[str, int] = {}
    seen_model: dict[tuple, int] = {}
    for s in skus:
        seen_code[s.sku_code] = seen_code.get(s.sku_code, 0) + 1
        seen_model[(s.category, nz.lower(s.model_name), s.kit_or_bare)] = (
            seen_model.get((s.category, nz.lower(s.model_name), s.kit_or_bare), 0) + 1
        )
    for code, n in seen_code.items():
        if n > 1:
            issues.append(Issue("SKU重复", "error", code, f"SKU 编码重复 {n} 次"))
    for key, n in seen_model.items():
        if n > 1:
            issues.append(Issue("SKU重复", "warn", key[1][:60], f"同型号+同 KIT 标记重复 {n} 次"))
    return issues


def check_images(skus: list[Sku]) -> list[Issue]:
    issues = []
    for s in skus:
        if not s.image_url_raw:
            issues.append(Issue("图片归属", "error", s.sku_code, "缺少原图 URL"))
            continue
        if s.image_status == "failed" or not s.image_path:
            issues.append(Issue("图片归属", "error", s.sku_code, "图片处理失败，未关联产品图"))
            continue
        if not Path(s.image_path).exists():
            issues.append(Issue("图片归属", "error", s.sku_code, "处理后的图片文件不存在"))
            continue
        if s.image_status == "suspect":
            issues.append(Issue("图片归属", "warn", s.sku_code, "所有候选主图疑似促销横幅，需人工确认"))
    return issues


def check_msrp(skus: list[Sku]) -> list[Issue]:
    issues = []
    for s in skus:
        fv = s.fields.get(F_MSRP)
        if not fv or fv.norm in (None, "") or float(fv.norm or 0) <= 0:
            issues.append(Issue("MSRP来源", "error", s.sku_code, "缺少 MSRP"))
        elif not fv.evidence_url:
            issues.append(Issue("MSRP来源", "error", s.sku_code, "MSRP 无来源链接"))
        elif fv.method not in ("json:compare_at_price", "json:list_price", "html", "manual"):
            issues.append(Issue("MSRP来源", "warn", s.sku_code, f"MSRP 来源方式存疑：{fv.method}"))
    return issues


def check_battery(skus: list[Sku]) -> list[Issue]:
    issues = []
    for s in skus:
        v = s.get(F_PLATFORM)
        ah = s.get(F_AH)
        raw_v = s.raw(F_PLATFORM)
        raw_ah = s.raw(F_AH)
        if v is None and ah is None:
            issues.append(Issue("电池平台/容量", "error", s.sku_code, "电池信息缺失"))
            continue
        if v is not None:
            try:
                if not 8 <= float(v) <= 120:
                    issues.append(Issue("电池平台/容量", "error", s.sku_code, f"平台电压异常：{v}V"))
            except (TypeError, ValueError):
                issues.append(Issue("电池平台/容量", "error", s.sku_code, f"平台电压非数值：{v}"))
        if ah is not None:
            try:
                ah_f = float(ah)
                if ah_f > 40:
                    issues.append(Issue("电池平台/容量", "error", s.sku_code, f"容量疑似混入平台值：{ah}"))
                if v is not None and abs(ah_f - float(v)) < 0.001:
                    issues.append(Issue("电池平台/容量", "error", s.sku_code, "容量与平台电压数值相同，疑似混淆"))
            except (TypeError, ValueError):
                issues.append(Issue("电池平台/容量", "error", s.sku_code, f"容量非数值：{ah}"))
        if "V" in raw_ah and "Ah" not in raw_ah:
            issues.append(Issue("电池平台/容量", "warn", s.sku_code, "容量字段混入了电压原文"))
        if "Ah" in raw_v and "V" not in raw_v.replace("Ah", ""):
            issues.append(Issue("电池平台/容量", "warn", s.sku_code, "平台字段混入了容量原文"))
    return issues


def check_mower(skus: list[Sku]) -> list[Issue]:
    issues = []
    for s in skus:
        if s.category != "mower":
            continue
        prop = s.get(F_PROPULSION)
        if prop not in ("push", "self-propelled"):
            issues.append(Issue("Mower推进方式", "error", s.sku_code, f"未标记 Push / Self-Propelled（当前 {prop}）"))
    return issues


def check_blower(skus: list[Sku]) -> list[Issue]:
    issues = []
    for s in skus:
        if s.category != "blower":
            continue
        cfm = s.get(F_CFM)
        if cfm is None:
            issues.append(Issue("Blower CFM", "error", s.sku_code, "未提取 CFM"))
        else:
            try:
                if not 50 <= float(cfm) <= 1500:
                    issues.append(Issue("Blower CFM", "error", s.sku_code, f"CFM 数值异常：{cfm}"))
            except (TypeError, ValueError):
                issues.append(Issue("Blower CFM", "error", s.sku_code, f"CFM 非数值：{cfm}"))
    return issues


def check_cropped(skus: list[Sku]) -> list[Issue]:
    issues = []
    for s in skus:
        if not s.image_path or not Path(s.image_path).exists():
            continue
        try:
            with Image.open(s.image_path) as im:
                alpha = np.asarray(im.convert("RGBA").getchannel("A"))
        except Exception as exc:
            issues.append(Issue("图片裁切", "warn", s.sku_code, f"图片读取失败：{exc}"))
            continue
        ys, xs = np.where(alpha > 8)
        if len(xs) == 0:
            issues.append(Issue("图片裁切", "error", s.sku_code, "处理后图片为空白"))
            continue
        margins = s.image_trim_margins or []
        if s.image_status == "suspect":
            continue
        zero_edges = len([m for m in margins if int(m) <= 1])
        if zero_edges >= 2:
            issues.append(
                Issue("图片裁切", "warn", s.sku_code, f"原图主体多边贴边，可能已裁切（留白 {margins}）")
            )
    return issues


def check_required(skus: list[Sku]) -> list[Issue]:
    issues = []
    for s in skus:
        cat = CATEGORIES.get(s.category)
        if not cat:
            continue
        skip = {F_PLATFORM, F_AH} if s.kit_or_bare == "bare" else set()
        for key in cat.required:
            if key in skip:
                continue
            if s.get(key) is None:
                issues.append(Issue("必采字段缺失", "warn", s.sku_code, f"缺少 {key}"))
    return issues


def check_count(skus: list[Sku], manifest: dict | None) -> list[Issue]:
    if not manifest:
        return [Issue("PNG/Excel数量", "error", "-", "缺少 PNG 渲染清单，无法核对数量")]
    png_ids = {c["sku_id"] for c in manifest.get("cards", [])}
    excel_ids = {s.sku_id for s in skus}
    issues = []
    if png_ids != excel_ids:
        missing = excel_ids - png_ids
        extra = png_ids - excel_ids
        issues.append(
            Issue(
                "PNG/Excel数量",
                "error",
                "-",
                f"数量不一致：PNG {len(png_ids)} / Excel {len(excel_ids)}；缺失 {len(missing)}，多余 {len(extra)}",
            )
        )
    if manifest.get("total_skus") != len(skus):
        issues.append(Issue("PNG/Excel数量", "error", "-", "渲染清单 SKU 总数与明细不一致"))
    return issues


CHECKS = (
    ("1 SKU是否重复", check_duplicates),
    ("2 图片归属", check_images),
    ("3 MSRP来源", check_msrp),
    ("4 电池平台/容量", check_battery),
    ("5 Mower推进方式", check_mower),
    ("6 Blower CFM", check_blower),
    ("7 图片裁切", check_cropped),
    ("8 PNG/Excel数量", check_count),
)


def run_checks(skus: list[Sku], manifest: dict | None = None) -> list[Issue]:
    issues: list[Issue] = []
    for _name, fn in CHECKS:
        if fn is check_count:
            issues.extend(fn(skus, manifest))
        else:
            issues.extend(fn(skus))
    issues.extend(check_required(skus))
    return issues


def summary(issues: list[Issue]) -> dict:
    errors = [i for i in issues if i.level == "error"]
    warns = [i for i in issues if i.level == "warn"]
    by_check: dict[str, int] = {}
    for i in issues:
        by_check[i.check] = by_check.get(i.check, 0) + 1
    return {
        "total": len(issues),
        "errors": len(errors),
        "warnings": len(warns),
        "blocking": len(errors) > 0,
        "by_check": by_check,
    }


def to_dicts(issues: list[Issue]) -> list[dict]:
    return [asdict(i) for i in issues]
