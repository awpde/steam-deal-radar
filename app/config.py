"""配置与路径管理。

设计原则：所有可能因为 Steam 改版而需要调整的东西（域名、参数、阈值、间隔）
都放在这里的默认值或由界面写入 settings.json，改配置不需要改代码。
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

# 路径分两种运行方式：
#   源码运行 → ROOT 是项目目录
#   打包成 exe → 代码和网页被解压到临时目录(_MEIPASS)，数据要写在 exe 旁边（用户能找到、能备份）
FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    ROOT = Path(sys.executable).resolve().parent
    RES_DIR = Path(getattr(sys, "_MEIPASS", ROOT))
else:
    ROOT = Path(__file__).resolve().parent.parent
    RES_DIR = ROOT

DATA_DIR = ROOT / "data"
WEB_DIR = RES_DIR / "web"
SETTINGS_FILE = DATA_DIR / "settings.json"
DB_FILE = DATA_DIR / "radar.db"
LOG_DIR = DATA_DIR / "logs"
ERROR_DIR = DATA_DIR / "errors"      # 解析失败时保存原始响应，便于事后定位
SAMPLE_DIR = RES_DIR / "tests" / "fixtures"

DEFAULT_SETTINGS: dict = {
    # 网络
    "proxy_enabled": False,                    # 是否走代理（加速器）
    "proxy_url": "http://127.0.0.1:7890",      # 常见加速器本地端口，可在界面改
    "timeout_connect": 10,
    "timeout_read": 25,
    "retries": 2,
    # 刷新
    "auto_refresh_minutes": 30,                # 0 = 关闭定时刷新
    "refresh_on_start": True,
    "deep_pages": 20,                          # 深刷最多扫多少页（每页 100 条）
    "detail_lookup_limit": 12,                 # 单次刷新最多逐条核验多少个 appid（详情接口限流）
    "max_items": 60,                           # 每个榜单最多展示多少条
    # 提醒
    "notify_enabled": True,
    "notify_only_free": True,                  # 只为「新出现的限免」弹通知
    "autostart_enabled": False,
    # 个人化
    "wishlist_steamid": "",                    # 17 位 SteamID64 或主页链接
    # 地区/语言（影响价格与文案）
    "cc": "cn",
    "lang": "schinese",
    # 评分过滤
    "min_review_pct": 70,
    # 本地服务
    "port": 8731,
}

_lock = threading.RLock()
_cache: dict | None = None


def ensure_dirs() -> None:
    for d in (DATA_DIR, LOG_DIR, ERROR_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict:
    """读取设置，缺失的键用默认值补齐（这样升级版本不会丢配置）。"""
    global _cache
    with _lock:
        if _cache is not None:
            return dict(_cache)
        data = dict(DEFAULT_SETTINGS)
        if SETTINGS_FILE.exists():
            try:
                saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    data.update({k: v for k, v in saved.items() if k in DEFAULT_SETTINGS})
            except Exception:
                # 配置损坏不致命，回退默认值即可
                pass
        _cache = data
        return dict(data)


def save_settings(patch: dict) -> dict:
    """只接受已知键，避免界面写进奇怪的字段把程序搞坏。"""
    global _cache
    ensure_dirs()
    with _lock:
        current = load_settings()
        for k, v in (patch or {}).items():
            if k in DEFAULT_SETTINGS:
                # 类型对齐，防止 "30" 这样的字符串混进来
                if isinstance(DEFAULT_SETTINGS[k], bool):
                    current[k] = bool(v)
                elif isinstance(DEFAULT_SETTINGS[k], int):
                    try:
                        current[k] = int(v)
                    except (TypeError, ValueError):
                        pass
                else:
                    current[k] = str(v)
        SETTINGS_FILE.write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _cache = current
        return dict(current)


def python_gui_exe() -> str:
    """返回不带控制台窗口的解释器路径（用于开机自启，避免每次弹黑框）。"""
    if FROZEN:
        return str(Path(sys.executable).resolve())
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)
