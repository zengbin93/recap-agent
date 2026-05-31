"""Small Tushare Pro HTTP client using only the Python standard library."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TUSHARE_URL = "http://api.tushare.pro"


class TushareError(RuntimeError):
    """Raised when Tushare returns an error or malformed payload."""


class TushareClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        api_url: str = TUSHARE_URL,
        timeout: float = 30.0,
        retries: int = 2,
        retry_sleep: float = 1.0,
        cache_dir: Path | None = None,
        use_cache: bool = True,
    ) -> None:
        self.token = token or os.environ.get("TUSHARE_TOKEN", "")
        if not self.token:
            raise TushareError(
                "Missing Tushare token. Set TUSHARE_TOKEN or pass --token."
            )
        self.api_url = api_url
        self.timeout = timeout
        self.retries = retries
        self.retry_sleep = retry_sleep
        self.cache_dir = cache_dir
        self.use_cache = use_cache

    def query(
        self,
        api_name: str,
        *,
        params: dict[str, Any] | None = None,
        fields: list[str] | str | None = None,
        cache_key: str | None = None,
    ) -> list[dict[str, Any]]:
        params = params or {}
        fields_text = ",".join(fields) if isinstance(fields, list) else fields or ""
        cache_path = self._cache_path(api_name, cache_key)
        if self.use_cache and cache_path and cache_path.exists():
            return self._rows_from_payload(json.loads(cache_path.read_text(encoding="utf-8")))

        payload = {
            "api_name": api_name,
            "token": self.token,
            "params": params,
            "fields": fields_text,
        }
        raw_payload = self._post(payload)
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(raw_payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return self._rows_from_payload(raw_payload)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            self.api_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        last_error: Exception | None = None
        attempts = max(1, self.retries + 1)
        for attempt in range(1, attempts + 1):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    text = response.read().decode("utf-8")
                return json.loads(text)
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
                last_error = error
                if attempt < attempts:
                    time.sleep(self.retry_sleep * attempt)
        raise TushareError(f"Tushare request failed: {last_error}")

    def _cache_path(self, api_name: str, cache_key: str | None) -> Path | None:
        if not self.cache_dir or not cache_key:
            return None
        safe_key = cache_key.replace("/", "_").replace(":", "_")
        return self.cache_dir / api_name / f"{safe_key}.json"

    def _rows_from_payload(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        code = payload.get("code")
        if code != 0:
            raise TushareError(f"Tushare error {code}: {payload.get('msg')}")
        data = payload.get("data") or {}
        fields = data.get("fields") or []
        items = data.get("items") or []
        if not isinstance(fields, list) or not isinstance(items, list):
            raise TushareError("Malformed Tushare payload: missing data.fields/items")
        return [dict(zip(fields, item)) for item in items]
