"""网络层：一切对外请求都走这里。

为什么单独一层？
Steam 的域名在中国大陆时通时不通（实测几分钟内可从 200 变成全部超时）。
把「重试、退避、压缩、直连/代理切换、限速」全部收敛在这一处，
上层的解析逻辑就不用关心网络怎么坏的。
"""
from __future__ import annotations

import gzip
import random
import threading
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, field
from typing import Iterable

# 伪装成普通浏览器，同时保留可识别标识（礼貌爬取，出问题能被认出来）
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 "
    "SteamDealRadar/1.0 (personal use)"
)

BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/javascript, text/html, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    # 年龄门/成人内容标记，缺少会拿不到部分游戏的详情
    "Cookie": "birthtime=628473601; wants_mature_content=1; Steam_Language=schinese",
}

# 全局限速：同一个进程内所有请求串行排队，最小间隔防止把 IP 打进限流
_rate_lock = threading.Lock()
_last_request_at = 0.0
MIN_INTERVAL = 1.2  # 秒


@dataclass
class FetchResult:
    ok: bool
    status: int = 0
    text: str = ""
    elapsed: float = 0.0
    url: str = ""
    via: str = "direct"
    error: str = ""
    attempts: int = 0
    headers: dict = field(default_factory=dict)

    def json(self):
        import json

        return json.loads(self.text)


def _throttle() -> None:
    global _last_request_at
    with _rate_lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def _decoders() -> tuple:
    return (gzip.decompress, zlib.decompress)


def _decode_body(raw: bytes, encoding: str) -> str:
    encoding = (encoding or "").lower()
    if "gzip" in encoding:
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    elif "deflate" in encoding:
        try:
            raw = zlib.decompress(raw)
        except Exception:
            try:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            except Exception:
                pass
    # Steam 有时用 utf-8，老页面可能夹带别的编码，容错处理
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _build_opener(use_proxy: bool, proxy_url: str) -> urllib.request.OpenerDirector:
    handlers: list = []
    if use_proxy and proxy_url:
        handlers.append(
            urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        )
    else:
        # 关键：显式清空代理，忽略系统/环境变量里的代理设置
        handlers.append(urllib.request.ProxyHandler({}))
    handlers.append(urllib.request.HTTPRedirectHandler())
    return urllib.request.build_opener(*handlers)


def fetch(
    url: str,
    *,
    settings: dict,
    timeout: float | None = None,
    retries: int | None = None,
    extra_headers: dict | None = None,
    force_proxy: bool | None = None,
) -> FetchResult:
    """抓一个 URL。失败会按指数退避重试，并且直连失败时自动尝试一次代理。"""
    use_proxy = settings.get("proxy_enabled", False)
    if force_proxy is not None:
        use_proxy = force_proxy
    proxy_url = settings.get("proxy_url", "")
    attempts_allowed = int(settings.get("retries", 2)) + 1 if retries is None else retries + 1
    timeout = timeout or float(settings.get("timeout_read", 25))

    headers = dict(BASE_HEADERS)
    if extra_headers:
        headers.update(extra_headers)

    last = FetchResult(ok=False, url=url)
    plans = [use_proxy]
    # 直连失败时补一次走代理（反之亦然），这对「时通时不通」的网络非常有效
    if not use_proxy and proxy_url:
        plans.append(True)
    elif use_proxy:
        plans.append(False)

    attempt = 0
    for plan_proxy in plans:
        opener = _build_opener(plan_proxy, proxy_url)
        for i in range(attempts_allowed):
            attempt += 1
            _throttle()
            started = time.monotonic()
            req = urllib.request.Request(url, headers=headers)
            try:
                with opener.open(req, timeout=timeout) as resp:
                    raw = resp.read()
                    body = _decode_body(raw, resp.headers.get("Content-Encoding", ""))
                    return FetchResult(
                        ok=True,
                        status=resp.status,
                        text=body,
                        elapsed=time.monotonic() - started,
                        url=url,
                        via="proxy" if plan_proxy else "direct",
                        attempts=attempt,
                        headers=dict(resp.headers),
                    )
            except urllib.error.HTTPError as exc:
                elapsed = time.monotonic() - started
                try:
                    body = _decode_body(exc.read(), exc.headers.get("Content-Encoding", ""))
                except Exception:
                    body = ""
                last = FetchResult(
                    ok=False,
                    status=exc.code,
                    text=body,
                    elapsed=elapsed,
                    url=url,
                    via="proxy" if plan_proxy else "direct",
                    error=f"HTTP {exc.code}",
                    attempts=attempt,
                    headers=dict(exc.headers or {}),
                )
                # 4xx 里除了 429 都是「问错了」，重试没意义
                if exc.code == 429:
                    retry_after = (exc.headers or {}).get("Retry-After", "5")
                    try:
                        wait = min(float(retry_after), 30)
                    except (TypeError, ValueError):
                        wait = 5
                    time.sleep(wait)
                    continue
                if 400 <= exc.code < 500:
                    break
                time.sleep(min(2 ** i, 8) + random.uniform(0, 0.8))
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                elapsed = time.monotonic() - started
                reason = getattr(exc, "reason", exc)
                last = FetchResult(
                    ok=False,
                    status=0,
                    elapsed=elapsed,
                    url=url,
                    via="proxy" if plan_proxy else "direct",
                    error=f"连接失败：{reason}",
                    attempts=attempt,
                )
                if i < attempts_allowed - 1:
                    time.sleep(min(1.5 * (2 ** i), 6) + random.uniform(0, 0.6))
    return last


def fetch_many(
    urls: Iterable[str], *, settings: dict, **kwargs
) -> list[FetchResult]:
    """顺序抓取多个 URL（保持限速，不并发）。"""
    return [fetch(u, settings=settings, **kwargs) for u in urls]


def fetch_asset(url: str, *, settings: dict, timeout: float = 8.0,
                retries: int = 1) -> tuple[bool, int, bytes, str]:
    """抓图片这类静态资源：不走全局限速。

    图片在 CDN 上（和 store 接口不是同一套限流），浏览器一次要几十张，
    如果还按 1.2 秒一张排队，页面要等一分多钟。所以这里单独开一条通道。
    返回 (是否成功, 状态码, 内容, Content-Type)。
    """
    use_proxy = bool(settings.get("proxy_enabled"))
    proxy_url = settings.get("proxy_url", "")
    plans = [use_proxy] + ([not use_proxy] if proxy_url else [])
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    }
    last_status = 0
    for plan in plans:
        opener = _build_opener(plan, proxy_url)
        for _ in range(retries + 1):
            try:
                req = urllib.request.Request(url, headers=headers)
                with opener.open(req, timeout=timeout) as resp:
                    raw = resp.read()
                    ctype = resp.headers.get("Content-Type", "image/jpeg")
                    return True, resp.status, raw, ctype
            except urllib.error.HTTPError as exc:
                last_status = exc.code
                if 400 <= exc.code < 500:
                    break
            except (urllib.error.URLError, TimeoutError, OSError):
                last_status = 0
    return False, last_status, b"", ""
