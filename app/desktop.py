"""独立窗口模式：不开浏览器标签，直接弹一个应用窗口。

原理：Windows 自带的 Edge（或 Chrome）支持 `--app=URL`，
打开后没有地址栏、没有标签页、没有书签栏，看起来就是一个独立软件窗口。
这样既不用引入 PySide/pywebview 这种重依赖（那些还要额外的运行库，
发给别人容易翻车），又能获得桌面程序的观感。

关闭窗口 = 退出程序：我们等的就是这个浏览器进程退出。
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from . import config

# 常见安装位置，按优先级试
_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_browser() -> str | None:
    local = os.environ.get("LOCALAPPDATA", "")
    extra = [
        os.path.join(local, r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(local, r"Google\Chrome\Application\chrome.exe"),
    ]
    for path in _CANDIDATES + extra:
        if path and Path(path).exists():
            return path
    return None


def open_window(url: str, *, width: int = 1180, height: int = 880) -> tuple[bool, str]:
    """弹独立窗口并等它关闭。返回 (是否成功, 说明)。"""
    exe = find_browser()
    if not exe:
        return False, "没找到 Edge 或 Chrome，改用默认浏览器"

    # 用独立的浏览器配置目录：这样不会跟用户自己的浏览器窗口混在一起，
    # 每次启动都是干净的应用窗口（也避免被已有实例接管后进程立刻退出）
    profile = config.DATA_DIR / "window-profile"
    try:
        profile.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    args = [
        exe,
        f"--app={url}",
        f"--user-data-dir={profile}",
        f"--window-size={width},{height}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,MediaRouter",
        "--disable-background-networking",
    ]
    try:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return False, f"启动窗口失败：{exc}"

    # 给窗口一点时间起来，期间如果进程直接挂了要能察觉
    time.sleep(1.2)
    if proc.poll() is not None:
        return False, "窗口进程意外退出，改用默认浏览器"
    try:
        proc.wait()      # 关掉窗口就走到这里
    except KeyboardInterrupt:
        pass
    return True, "窗口已关闭"
