"""桌面通知 + 开机自启（纯标准库，不需要装任何东西）。

Windows 的 Toast 通知可以借 PowerShell 弹出来，脚本用 Base64 传进去，
这样中文、引号、路径空格都不会出问题。
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path

from . import config

APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "SteamDealRadar"

_TOAST_TEMPLATE = """
$ErrorActionPreference = 'Stop'
[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime]
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml({xml_literal})
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{app_id}').Show($toast)
"""


def _ps_escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def toast(title: str, message: str, *, silent: bool = False) -> bool:
    """弹一个 Windows 通知。

    主路径：进程内 ctypes 气泡通知（快、无依赖）。
    兜底：PowerShell（只在气泡失败时尝试，且不阻塞）。
    """
    if os.name != "nt":
        return False
    try:
        from . import toast_win
        if toast_win.balloon(title, message, seconds=6 if not silent else 4):
            return True
    except Exception:
        pass
    return _toast_via_powershell(title, message, silent=silent)


def _toast_via_powershell(title: str, message: str, *, silent: bool = False) -> bool:
    xml = (
        "<toast>"
        "<visual><binding template='ToastGeneric'>"
        f"<text>{_ps_escape(title)}</text>"
        f"<text>{_ps_escape(message)}</text>"
        "</binding></visual>"
        + ("<audio silent='true'/>" if silent else "")
        + "</toast>"
    )
    script = _TOAST_TEMPLATE.format(
        xml_literal="'" + xml.replace("'", "''") + "'", app_id=APP_ID
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
             "-EncodedCommand", encoded],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            proc.kill()          # 卡住就杀掉，绝不拖累主程序
            return False
        return proc.returncode == 0
    except Exception:
        return False


def notify_new_freebies(items: list[dict], settings: dict) -> int:
    """有新出现的限免就弹通知（一次刷新最多弹 3 条，避免刷屏）。"""
    if not settings.get("notify_enabled"):
        return 0
    items = [i for i in items if i.get("kind") == "ftk"]
    if not items:
        return 0
    sent = 0
    for it in items[:3]:
        toast("发现新的限时免费领取", it.get("name") or f"appid {it.get('appid')}")
        sent += 1
    if len(items) > 3:
        toast(f"另有 {len(items) - 3} 款新的限免", "打开 Steam 雷达查看完整列表")
        sent += 1
    return sent


def notify_run_result(result: dict, settings: dict) -> int:
    if not settings.get("notify_enabled"):
        return 0
    if result.get("new_ftk"):
        return notify_new_freebies(result["new_ftk"], settings)
    if settings.get("notify_only_free") is False:
        if result.get("ok"):
            toast("刷新完成", f"限免 {result.get('ftk', 0)} 款 · 折扣 {result.get('deals', 0)} 款")
        else:
            toast("刷新失败", "所有数据源都连不上，已显示缓存数据")
    return 0


# ------------------------------------------------------------------ 开机自启

def _run_script_path() -> Path:
    return config.ROOT / "run.py"


def autostart_command() -> str:
    """pythonw（源码运行）或 exe 自身（打包后）启动，都不会闪黑框。"""
    exe = config.python_gui_exe()
    if config.FROZEN:
        return f'"{exe}" --silent'
    return f'"{exe}" "{_run_script_path()}" --silent'


def autostart_enabled() -> bool:
    if os.name != "nt":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_autostart(enabled: bool) -> tuple[bool, str]:
    if os.name != "nt":
        return False, "当前系统不支持开机自启设置"
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, autostart_command())
                return True, "已设置开机自启"
            try:
                winreg.DeleteValue(key, RUN_VALUE_NAME)
            except FileNotFoundError:
                pass
            return True, "已关闭开机自启"
    except OSError as exc:
        return False, f"写入注册表失败：{exc}"


def open_in_browser(url: str) -> None:
    try:
        os.startfile(url)  # type: ignore[attr-defined]
    except Exception:
        import webbrowser
        webbrowser.open(url)
