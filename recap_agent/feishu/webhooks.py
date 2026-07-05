"""FEISHU_WEBHOOKS JSON 解析与 daily/weekly/monthly 回退。

契约：FEISHU_WEBHOOKS 是一个 JSON 对象
    {
      "daily":   {"key": "<webhook-key-or-url>", "sign_secret": "<可选>"},
      "weekly":  {"key": "..."},
      "monthly": {"key": "..."}
    }
- ``daily`` 必填且必须有 ``key``，否则抛 ValueError；
- ``weekly`` / ``monthly`` 缺失或没给 ``key`` 时，:meth:`Webhooks.resolve` 回退到 daily。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

TASKS = ("daily", "weekly", "monthly")


@dataclass(frozen=True)
class WebhookTarget:
    """单个飞书任务的推送目标。"""

    key: str
    sign_secret: Optional[str] = None


@dataclass(frozen=True)
class Webhooks:
    """三个复盘任务的 webhook 解析结果。weekly/monthly 为 None 表示未配置。"""

    daily: WebhookTarget
    weekly: Optional[WebhookTarget] = None
    monthly: Optional[WebhookTarget] = None

    def resolve(self, task: str) -> WebhookTarget:
        """返回某任务的推送目标；weekly/monthly 未配置时回退到 daily。"""
        if task == "daily":
            return self.daily
        if task == "weekly":
            return self.weekly or self.daily
        if task == "monthly":
            return self.monthly or self.daily
        raise ValueError(
            f"unknown feishu task: {task!r} (expected one of {TASKS})"
        )


def _target_from(obj: object, name: str) -> Optional[WebhookTarget]:
    """从配置项构造 WebhookTarget；未配置或无 key 时返回 None（用于回退）。"""
    if obj is None:
        return None
    if not isinstance(obj, dict):
        raise ValueError(f"FEISHU_WEBHOOKS.{name} must be an object")
    key = obj.get("key")
    if not key or not isinstance(key, str):
        return None
    return WebhookTarget(key=key, sign_secret=obj.get("sign_secret"))


def parse_webhooks(raw: Optional[str]) -> Webhooks:
    """解析 FEISHU_WEBHOOKS 原始字符串。空、非法 JSON、缺 daily 均抛 ValueError。"""
    if raw is None or not isinstance(raw, str) or not raw.strip():
        raise ValueError("FEISHU_WEBHOOKS is empty; daily webhook is required")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"FEISHU_WEBHOOKS is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("FEISHU_WEBHOOKS must be a JSON object")

    daily = _target_from(data.get("daily"), "daily")
    if daily is None:
        raise ValueError(
            "FEISHU_WEBHOOKS.daily is required and must have a 'key'"
        )
    weekly = _target_from(data.get("weekly"), "weekly")
    monthly = _target_from(data.get("monthly"), "monthly")
    return Webhooks(daily=daily, weekly=weekly, monthly=monthly)
