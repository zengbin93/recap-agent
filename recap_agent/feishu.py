from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib import request


@dataclass(frozen=True)
class FeishuTarget:
    url: str
    secret: str | None = None


@dataclass(frozen=True)
class SendResult:
    status_code: int
    body: str
    dry_run: bool


@dataclass(frozen=True)
class FeishuConfig:
    default: FeishuTarget | None
    tasks: dict[str, FeishuTarget]

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "FeishuConfig":
        values = env or os.environ
        default = _target_from_values(values.get("FEISHU_WEBHOOK_URL"), values.get("FEISHU_WEBHOOK_SECRET"))
        tasks = _targets_from_json(values.get("FEISHU_WEBHOOKS_JSON"))
        for task in ("daily", "weekly", "monthly"):
            prefix = f"FEISHU_{task.upper()}_"
            target = _target_from_values(values.get(prefix + "WEBHOOK_URL"), values.get(prefix + "WEBHOOK_SECRET"))
            if target:
                tasks[task] = target
        return cls(default=default, tasks=tasks)

    def resolve(self, task: str) -> FeishuTarget:
        if task in self.tasks:
            return self.tasks[task]
        if self.default:
            return self.default
        raise ValueError(f"no feishu webhook configured for task {task}")


def _target_from_values(url: str | None, secret: str | None) -> FeishuTarget | None:
    if not url:
        return None
    return FeishuTarget(url=url, secret=secret or None)


def _targets_from_json(raw: str | None) -> dict[str, FeishuTarget]:
    if not raw:
        return {}
    parsed = json.loads(raw)
    targets: dict[str, FeishuTarget] = {}
    for name, value in parsed.items():
        if isinstance(value, str):
            targets[name] = FeishuTarget(url=value)
        else:
            url = value.get("url") or value.get("webhook_url")
            if url:
                targets[name] = FeishuTarget(url=url, secret=value.get("secret") or value.get("webhook_secret"))
    return targets


def build_signed_payload(payload: Mapping[str, Any], secret: str | None, timestamp: int | None = None) -> dict[str, Any]:
    body = dict(payload)
    if not secret:
        return body
    ts = str(timestamp or int(time.time()))
    string_to_sign = f"{ts}\n{secret}".encode("utf-8")
    sign = base64.b64encode(hmac.new(string_to_sign, b"", digestmod=hashlib.sha256).digest()).decode("utf-8")
    body["timestamp"] = ts
    body["sign"] = sign
    return body


class FeishuSender:
    def __init__(self, dry_run: bool = False, timeout: int = 10):
        self.dry_run = dry_run
        self.timeout = timeout

    def send(self, target: FeishuTarget, payload: Mapping[str, Any]) -> SendResult:
        signed = build_signed_payload(payload, target.secret)
        encoded = json.dumps(signed, ensure_ascii=False).encode("utf-8")
        if self.dry_run:
            return SendResult(status_code=0, body=encoded.decode("utf-8"), dry_run=True)
        req = request.Request(
            target.url,
            data=encoded,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with request.urlopen(req, timeout=self.timeout) as response:  # noqa: S310 - user-configured webhook.
            body = response.read().decode("utf-8")
            return SendResult(status_code=response.status, body=body, dry_run=False)

