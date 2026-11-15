"""游戏封面图：自己抓、自己存、自己发。

为什么不直接用 Steam 返回的图片地址？
实测 Steam 给的 `shared.st.dl.eccdnx.com` 在国内某些网络上解析不通（会返回 0 字节），
但同一个游戏的图片在 `cdn.akamai.steamstatic.com` 上是好的。
所以这里不信任返回的 URL，统一按 appid 拼地址、逐个 CDN 试，抓到后存本地磁盘，
之后浏览器直接读本地，秒开且离线也能看。
"""
from __future__ import annotations

import threading
from pathlib import Path

from . import config
from .netlayer import fetch_asset

# 同一个游戏的图片，这几个地址是等价的，按顺序试
CDN_HOSTS = ("cdn.akamai.steamstatic.com", "cdn.cloudflare.steamstatic.com")
# 优先用横版小图（卡片比例），没有就退回大图
SIZES = ("capsule_231x87.jpg", "header.jpg")

_cache_dir = config.DATA_DIR / "images"
_mem: dict[int, bytes] = {}          # 内存缓存，避免反复读盘
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


def candidates(appid: int) -> list[str]:
    """先在同一台服务器上换尺寸试，不行再换服务器 —— 少走冤枉路。"""
    urls = []
    for host in CDN_HOSTS:
        for size in SIZES:
            urls.append(f"https://{host}/steam/apps/{appid}/{size}")
    return urls


def get_image(appid: int, settings: dict) -> tuple[bytes, str] | None:
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

        for url in candidates(appid):
            ok, status, raw, ctype = fetch_asset(url, settings=settings)
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
    return None


def warm(appids: list[int], settings: dict, limit: int = 40, workers: int = 4) -> int:
    """后台预热：界面刚刷新完就把封面提前抓好，滚动时才不会一张张慢慢出。

    CDN 单张要 1~3 秒，串行抓 60 张要两三分钟，所以开 4 个线程并行。
    """
    todo = [a for a in dict.fromkeys(appids) if a not in _mem][:limit]
    if not todo:
        return 0

    def one(appid: int) -> None:
        try:
            get_image(appid, settings)
        except Exception:
            pass

    def run() -> None:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, todo))

    threading.Thread(target=run, daemon=True, name="img-warm").start()
    return len(todo)
