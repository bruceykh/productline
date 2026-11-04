# -*- coding: utf-8 -*-
"""SKU 与字段数据模型：每个业务字段都保存 raw / normalized / evidence 三值。"""

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class FieldValue:
    raw: str = ""
    norm: Any = None
    evidence_url: str = ""
    method: str = ""          # json / html / rule / manual / llm
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FieldValue":
        return cls(
            raw=d.get("raw", ""),
            norm=d.get("norm"),
            evidence_url=d.get("evidence_url", ""),
            method=d.get("method", ""),
            confidence=d.get("confidence", 1.0),
        )


@dataclass
class Sku:
    brand: str
    category: str
    model_name: str
    sku_code: str
    source_url: str
    kit_or_bare: str = "unknown"          # kit / bare / unknown
    fields: dict[str, FieldValue] = field(default_factory=dict)
    image_url_raw: str = ""
    image_candidates: list = field(default_factory=list)   # 候选主图（按优先级）
    image_raw_path: str = ""
    image_path: str = ""
    image_trim_margins: list = field(default_factory=list)
    image_status: str = "pending"         # pending / ok / trimmed / failed
    review_status: str = "pending"        # pending / approved / rejected
    notes: str = ""
    collected_at: str = ""
    variant_title: str = ""

    # ---------- 便捷读取 ----------
    def get(self, key: str, default: Any = None) -> Any:
        fv = self.fields.get(key)
        return default if fv is None or fv.norm in (None, "") else fv.norm

    def raw(self, key: str) -> str:
        fv = self.fields.get(key)
        return fv.raw if fv else ""

    def set(self, key: str, raw: Any, norm: Any, evidence: str, method: str = "rule") -> None:
        self.fields[key] = FieldValue(
            raw="" if raw is None else str(raw).strip(),
            norm=norm,
            evidence_url=evidence,
            method=method,
        )

    @property
    def sku_id(self) -> str:
        return f"{self.brand}:{self.sku_code}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["fields"] = {k: v.to_dict() for k, v in self.fields.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Sku":
        d = dict(d)
        d["fields"] = {k: FieldValue.from_dict(v) for k, v in d.get("fields", {}).items()}
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in allowed})
