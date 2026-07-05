"""tushare 采集封装：重试 3 次（限流/网络），3 次后跳过降级，对外只吐 list[dict]。

tushare / pandas 在 :func:`_default_fetcher` 内按需 import；测试通过注入 fetcher 在边界 mock，
因此单测不需要安装 tushare。
"""

from __future__ import annotations

import time
from typing import Callable, Optional

# tushare 限流时异常消息里通常出现的字样（中英文都覆盖）
_RETRYABLE_HINTS = ("频次", "频率", "每分钟", "次数", "limit", "rate", "too many", "频繁")


class TushareError(Exception):
    """tushare 调用通用错误（默认不可重试，如认证失败、参数非法）。"""


class RateLimitError(TushareError):
    """可重试的瞬态错误：限流、网络抖动、空结果。"""


class SkipDataset(Exception):
    """重试 ``retries`` 次后仍失败，跳过该数据集（降级）。"""


def _default_fetcher(api_name, token, params):
    """实际调用 tushare 的 fetcher。运行时 import tushare/pandas，对外只吐 list[dict]。"""
    import tushare  # 按需 import，避免成为测试期的硬依赖

    pro = tushare.pro_api(token)
    method = getattr(pro, api_name, None)
    if method is None:
        raise TushareError(f"unknown tushare api: {api_name}")
    try:
        df = method(**params)
    except Exception as exc:  # noqa: BLE001 —— tushare 抛的异常类型不稳定，按消息分类
        msg = str(exc)
        if isinstance(exc, OSError) or any(h in msg.lower() for h in _RETRYABLE_HINTS):
            # 网络 / 限流：交给调用方重试
            raise RateLimitError(f"tushare {api_name} transient error: {exc}") from exc
        raise TushareError(f"tushare {api_name} failed: {exc}") from exc
    if df is None or len(df) == 0:
        # tushare 限流的另一种表现：返回空，按可重试处理
        raise RateLimitError(f"tushare {api_name} returned empty for {params}")
    return df.to_dict("records")


def fetch_dataset(
    api_name: str,
    token: str,
    params: dict,
    *,
    fetcher: Optional[Callable] = None,
    retries: int = 3,
    backoff_base: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
):
    """调用 tushare ``api_name(params)``，限流/网络错误重试，3 次后抛 :class:`SkipDataset`。

    - 限流、网络错误、空结果：可重试；
    - 其它 :class:`TushareError`（认证/参数）：立即向上抛，不重试；
    - 重试耗尽：抛 :class:`SkipDataset`，由调用方按"跳过该数据集"降级。
    """
    fetcher = fetcher or _default_fetcher
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            rows = fetcher(api_name, token, params)
        except (RateLimitError, OSError) as exc:
            last_err = exc
            if attempt < retries:
                sleep(backoff_base * attempt)
            continue
        # 非 RateLimitError 的 TushareError 在此不被捕获，直接向上抛（不重试）
        if not rows:
            last_err = RateLimitError(f"{api_name} returned empty rows")
            if attempt < retries:
                sleep(backoff_base * attempt)
            continue
        return rows
    raise SkipDataset(f"{api_name} skipped after {retries} retries: {last_err}")
