"""磁盘缓存：按 dataset + params 隔离，不缓存空/脏/截断数据。

每个缓存条目单文件，内容 ``{"count": N, "rows": [...]}``；
读取时校验 ``count == len(rows)``，不符（截断/被改写）视为未命中。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional


def _cache_key(dataset: str, **params: Any) -> str:
    payload = json.dumps({"dataset": dataset, "params": params}, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


class Cache:
    """文件缓存。root 目录下按 dataset__hash.json 存放。"""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, dataset: str, **params: Any) -> Path:
        return self.root / f"{dataset}__{_cache_key(dataset, **params)}.json"

    def get(self, dataset: str, **params: Any) -> Optional[list]:
        """命中返回 list[dict]，未命中/损坏/截断返回 None。"""
        path = self._path(dataset, **params)
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            rows = record["rows"]
            if record.get("count") != len(rows):
                return None  # 截断或被改写：当作脏数据丢弃
            return rows
        except (ValueError, KeyError, TypeError):
            return None  # 损坏的 JSON：当作未命中而非崩溃

    def put(self, dataset: str, rows, **params: Any) -> None:
        """写入；空结果不缓存（避免缓存"脏/空"数据）。"""
        if not rows:
            return
        path = self._path(dataset, **params)
        record = {"count": len(rows), "rows": list(rows)}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)  # 原子替换，避免半写文件被当成有效缓存
