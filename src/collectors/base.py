# -*- utf-8 -*-
"""采集器基类定义。"""

from typing import Callable, Protocol

from src.config import Category
from src.models import Sku

ProgressCb = Callable[[str, int, int, str], None]


class Collector(Protocol):
    def collect(self, category: Category, limit: int, progress: ProgressCb | None = None) -> list[Sku]:
        ...
