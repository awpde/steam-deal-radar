"""游戏封面图：自己抓、自己存、自己发。

为什么不直接用 Steam 返回的图片地址？
实测 Steam 给的 `shared.st.dl.eccdnx.com` 在国内某些网络上解析不通（会返回 0 字节），
但同一个游戏的图片在 `cdn.akamai.steamstatic.com` 上是好的。
所以这里不信任返回的 URL，统一按 appid 拼地址、逐个 CDN 试，抓到后存本地磁盘，
之后浏览器直接读本地，秒开且离线也能看。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from . import config
from .netlayer import fetch_asset

# Steam 原本给的图片域名（shared.st.dl.eccdnx.com）在国内常解析不通，
# 但换成下面这几个"同一个内容、不同 CDN"的域名就能正常取到。
SHARED_HOSTS = ("shared.akamai.steamstatic.com", "shared.fastly.steamstatic.com")
# 老游戏还有一套不带哈希的地址，作为兜底
CDN_HOSTS = ("cdn.akamai.steamstatic.com", "cdn.cloudflare.steamstatic.com")
SIZES = ("capsule_231x87.jpg", "header.jpg")

BUDGET_SECONDS = 14.0        # 单张图最多折腾多久，超时就放弃，别拖住页面
FAIL_TTL = 3600              # 抓不到的，一小时内不再重试

_cache_dir = config.DATA_DIR / "images"
_mem: dict[int, bytes] = {}          # 内存缓存，避免反复读盘
_failed: dict[int, float] = {}       # appid -> 失败时间
_locks: dict[int, threading.Lock] = {}
_lock_guard = threading.Lock()


def _lock_for(appid: int) -> threading.Lock:
    with _lock_guard:
        lk = _locks.get(appid)
        if lk is None:
            lk = _locks[appid] = threading.Lock()
        return lk


def cache_path(appid: int) -> Path:
    return _cache_dir / f"{appid}.jpg"


def candidates(appid: int, cover: str = "") -> list[str]:
    """按成功率排序的候选地址。

    第一优先：Steam 自己给的路径（带哈希，新游戏只有这个），只把域名换掉；
    第二优先：老式不带哈希的路径。实测这样新旧游戏都能覆盖。
    """
    urls: list[str] = []
    if cover:
        path = urlparse(cover).path
        if path:
            for host in SHARED_HOSTS:
                urls.append(f"https://{host}{path}")
    for host in CDN_HOSTS:
        for size in SIZES:
            urls.append(f"https://{host}/steam/apps/{appid}/{size}")
    return urls


def get_image(appid: int, settings: dict, cover: str = "") -> tuple[bytes, str] | None:
    """返回 (图片字节, 类型)。本地有就读本地，没有才联网。"""
    if appid in _mem:
        return _mem[appid], "image/jpeg"

    path = cache_path(appid)
    if path.exists() and path.stat().st_size > 512:
        try:
            data = path.read_bytes()
            _mem[appid] = data
            return data, "image/jpeg"
        except OSError:
            pass

    with _lock_for(appid):
        # 双检：并发请求时别的线程可能已经抓好
        if appid in _mem:
            return _mem[appid], "image/jpeg"
        if path.exists() and path.stat().st_size > 512:
            data = path.read_bytes()
            _mem[appid] = data
            return data, "image/jpeg"

        # 最近失败过就先别再试，免得每次刷新都卡一遍
        if time.time() - _failed.get(appid, 0) < FAIL_TTL:
            return None

        deadline = time.monotonic() + BUDGET_SECONDS
        for url in candidates(appid, cover):
            if time.monotonic() > deadline:
                break
            ok, status, raw, ctype = fetch_asset(url, settings=settings, timeout=6, retries=0)
            if ok and len(raw) > 512:
                _mem[appid] = raw
                try:
                    _cache_dir.mkdir(parents=True, exist_ok=True)
                    tmp = path.with_suffix(".tmp")
                    tmp.write_bytes(raw)
                    tmp.replace(path)
                except OSError:
                    pass
                return raw, ctype or "image/jpeg"
        _failed[appid] = time.time()
    return None


def warm(items: list[tuple[int, str]], settings: dict, limit: int = 40, workers: int = 4) -> int:
    """后台预热：界面刚刷新完就把封面提前抓好，滚动时才不会一张张慢慢出。

    CDN 单张要 1~3 秒，串行抓 60 张要两三分钟，所以开 4 个线程并行。
    参数是 [(appid, cover_url)]，cover_url 来自搜索结果，能覆盖新游戏的哈希路径。
    """
    todo = [(a, c) for a, c in dict(items).items() if a not in _mem][:limit]
    if not todo:
        return 0

    def one(pair: tuple[int, str]) -> None:
        try:
            get_image(pair[0], settings, pair[1])
        except Exception:
            pass

    def run() -> None:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, todo))

    threading.Thread(target=run, daemon=True, name="img-warm").start()
    return len(todo)
