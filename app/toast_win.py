"""Windows 桌面通知（纯 ctypes 实现，不依赖 PowerShell 或第三方库）。

为什么不用 PowerShell？
实测在这台机器上 `powershell -EncodedCommand` 会被安全策略拦住并卡住 30 秒，
弹通知这种小事不该有这种副作用。改成直接调 shell32 的 Shell_NotifyIcon（气泡通知），
进程内完成、毫秒级返回、失败了也不影响主流程。
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes as w

NIM_ADD = 0x0
NIM_MODIFY = 0x1
NIM_DELETE = 0x2

NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
NIF_INFO = 0x10

NIIF_INFO = 0x01
NIIF_NOSOUND = 0x10

WM_APP = 0x8000
WM_TRAY = WM_APP + 1


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", w.DWORD),
        ("Data2", w.WORD),
        ("Data3", w.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.DWORD),
        ("hWnd", w.HWND),
        ("uID", w.UINT),
        ("uFlags", w.UINT),
        ("uCallbackMessage", w.UINT),
        ("hIcon", w.HICON),
        ("szTip", w.WCHAR * 128),
        ("dwState", w.DWORD),
        ("dwStateMask", w.DWORD),
        ("szInfo", w.WCHAR * 256),
        ("uVersion", w.UINT),
        ("szInfoTitle", w.WCHAR * 64),
        ("dwInfoFlags", w.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", w.HICON),
    ]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_void_p, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.UINT),
        ("style", w.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", w.HINSTANCE),
        ("hIcon", w.HICON),
        ("hCursor", w.HANDLE),
        ("hbrBackground", w.HBRUSH),
        ("lpszMenuName", w.LPCWSTR),
        ("lpszClassName", w.LPCWSTR),
        ("hIconSm", w.HICON),
    ]


def _load_libs():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    user32.RegisterClassExW.restype = w.ATOM

    user32.CreateWindowExW.argtypes = [
        w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID,
    ]
    user32.CreateWindowExW.restype = w.HWND

    user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user32.DefWindowProcW.restype = ctypes.c_void_p

    user32.LoadIconW.argtypes = [w.HINSTANCE, w.LPCWSTR]
    user32.LoadIconW.restype = w.HICON

    user32.PeekMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT]
    user32.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
    user32.DestroyWindow.argtypes = [w.HWND]

    shell32.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    shell32.Shell_NotifyIconW.restype = w.BOOL

    kernel32.GetModuleHandleW.argtypes = [w.LPCWSTR]
    kernel32.GetModuleHandleW.restype = w.HMODULE
    return user32, shell32, kernel32

_MESSAGE_LOCK = threading.Lock()


def _show_blocking(title: str, message: str, seconds: float = 5.0) -> bool:
    user32, shell32, kernel32 = _load_libs()
    hinst = kernel32.GetModuleHandleW(None)
    class_name = "SteamDealRadarNotifyWnd"

    @WNDPROC
    def _wndproc(hwnd, msg, wparam, lparam):
        if msg == WM_DESTROY:
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = _wndproc
    wc.hInstance = hinst
    wc.lpszClassName = class_name
    user32.RegisterClassExW(ctypes.byref(wc))     # 已存在时返回 0，忽略即可

    hwnd = user32.CreateWindowExW(
        0, class_name, "SteamDealRadar", 0, 0, 0, 0, 0, None, None, hinst, None
    )
    if not hwnd:
        return False

    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = hwnd
    nid.uID = 1
    nid.uFlags = NIF_ICON | NIF_TIP | NIF_MESSAGE
    nid.uCallbackMessage = WM_TRAY
    nid.hIcon = user32.LoadIconW(None, w.LPCWSTR(32512))   # IDI_APPLICATION
    nid.szTip = "Steam 限免 & 折扣雷达"
    shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    nid.uFlags = NIF_INFO
    nid.szInfoTitle = (title or "")[:63]
    nid.szInfo = (message or "")[:255]
    nid.dwInfoFlags = NIIF_INFO
    ok = bool(shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)))

    # 让气泡有机会展示出来，同时把消息队列处理掉，避免窗口卡死
    deadline = time.monotonic() + seconds
    msg = w.MSG()
    while time.monotonic() < deadline:
        while user32.PeekMessageW(ctypes.byref(msg), hwnd, 0, 0, 1):   # PM_REMOVE
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.05)

    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
    user32.DestroyWindow(hwnd)
    return ok


def balloon(title: str, message: str, seconds: float = 5.0) -> bool:
    """弹一个气泡通知。成功返回 True；任何异常都吞掉，绝不影响主程序。"""
    if not hasattr(ctypes, "WinDLL"):
        return False
    if not _MESSAGE_LOCK.acquire(blocking=False):
        return False          # 上一条还没展示完，别叠加
    try:
        return _show_blocking(title, message, seconds)
    except Exception:
        return False
    finally:
        _MESSAGE_LOCK.release()


def balloon_async(title: str, message: str, seconds: float = 5.0) -> bool:
    t = threading.Thread(target=balloon, args=(title, message, seconds), daemon=True)
    t.start()
    return True
