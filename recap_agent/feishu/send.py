"""飞书自定义机器人 webhook 发送（urllib，零依赖）。

- :func:`webhook_url`  —— webhook key 拼完整 URL（或原样放行完整 URL）。
- :func:`sign`         —— 飞书官方 HMAC-SHA256 签名。
- :func:`send_card`    —— 发送交互卡片；签名/网络错误捕获为结构化结果，不抛异常。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.request
from typing import Any, Callable, Optional

_HOOK_PREFIX = "https://open.feishu.cn/open-apis/bot/v2/hook/"


def webhook_url(key: str) -> str:
    """webhook key → 完整 URL；若 key 已是 http(s) URL 则原样返回。"""
    if key.startswith("http://") or key.startswith("https://"):
        return key
    return _HOOK_PREFIX + key


def sign(secret: str, timestamp: int) -> str:
    """飞书自定义机器人签名：HMAC-SHA256(key="{timestamp}\\n{secret}", msg="") → base64。"""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_card(
    key: str,
    card: dict,
    *,
    sign_secret: Optional[str] = None,
    timestamp: Optional[int] = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
    timeout: float = 10.0,
) -> dict:
    """向飞书 webhook 发送交互卡片。

    返回 ``{"ok": bool, "status": int, "body": ..., "error"?: str}``。
    网络错误和飞书返回非 2xx 都落到 ``ok=False``，不向上抛——批量推送时单条失败不拖垮整批。
    """
    if timestamp is None:
        timestamp = int(time.time())

    payload: dict = {"msg_type": "interactive", "card": card}
    if sign_secret:
        payload["timestamp"] = str(timestamp)
        payload["sign"] = sign(sign_secret, timestamp)

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url(key),
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        resp = opener(req, timeout=timeout)
    except OSError as exc:
        return {"ok": False, "status": 0, "error": str(exc)}

    try:
        with resp:
            body = resp.read()
            status = getattr(resp, "status", None)
            if status is None:
                status = resp.getcode()
    except OSError as exc:
        return {"ok": False, "status": 0, "error": str(exc)}

    status = int(status)
    ok = 200 <= status < 300
    result: dict = {"ok": ok, "status": status}
    try:
        result["body"] = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        result["body"] = body.decode("utf-8", errors="replace")
    if not ok:
        result["error"] = f"feishu webhook returned HTTP {status}"
    return result
