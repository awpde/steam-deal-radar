"""Steam 限免 & 折扣雷达 —— 程序入口。

四种运行方式：
    双击 exe / python run.py        独立应用窗口（默认，像桌面软件）
    --browser                       用默认浏览器打开
    --silent                        后台静默运行（开机自启用，无界面）
    --refresh-once                  只刷新一次，命令行打印结果
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

# 打包成无控制台程序时，sys.stdout 是 None，直接 print 会抛异常
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import __version__, config, db, desktop, notify, server, steam   # noqa: E402


def _fatal(message: str) -> None:
    """无控制台时用系统弹窗告知失败原因，别让用户双击后什么都看不到。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "Steam 限免 & 折扣雷达", 0x10)
    except Exception:
        print(message)


def _scheduler() -> None:
    """后台定时刷新：快刷按设置的间隔，深刷每天一次。"""
    last_quick = 0.0
    last_deep = 0.0
    first = True
    while True:
        try:
            settings = config.load_settings()
            now = time.time()
            if first:
                first = False
                if settings.get("refresh_on_start"):
                    time.sleep(2)
                    server.start_refresh("quick", trigger="auto")
                    last_quick = now
            minutes = int(settings.get("auto_refresh_minutes") or 0)
            if minutes > 0 and now - last_quick > minutes * 60:
                server.start_refresh("quick", trigger="auto")
                last_quick = now
            # 每天 03:00 之后做一次深刷（扫更多页，补上快刷漏掉的长尾）
            local_hour = time.localtime().tm_hour
            if 3 <= local_hour < 6 and now - last_deep > 20 * 3600:
                server.start_refresh("deep", trigger="auto")
                last_deep = now
        except Exception:
            pass
        time.sleep(30)


def main() -> int:
    parser = argparse.ArgumentParser(description="Steam 限免 & 折扣雷达")
    parser.add_argument("--browser", action="store_true", help="用默认浏览器打开而不是独立窗口")
    parser.add_argument("--silent", action="store_true", help="后台静默运行，不显示界面（开机自启用）")
    parser.add_argument("--refresh-once", action="store_true", help="只刷新一次然后退出")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    try:
        config.ensure_dirs()
        db.connection()
    except Exception as exc:
        _fatal(f"初始化失败：{exc}")
        return 1

    settings = config.load_settings()
    port = args.port or int(settings.get("port") or 8731)
    url = f"http://127.0.0.1:{port}/"

    if args.refresh_once:
        result = steam.refresh("quick", trigger="manual")
        print(f"版本 {__version__}｜状态：{result['status']}｜候选 {result['total']} 条"
              f"｜限免 {result['ftk']} 条｜折扣 {result['deals']} 条｜耗时 {result['elapsed']}s")
        for err in result.get("errors") or []:
            print("提示：", err)
        return 0 if result["ok"] else 1

    # 端口被占用 = 已经有一个在跑，直接开个窗口给它，别启动第二个
    if server.port_in_use(port):
        if args.silent:
            return 0
        if args.browser or not desktop.find_browser():
            notify.open_in_browser(url)
        else:
            desktop.open_window(url)
        return 0

    try:
        httpd = server.make_server(port)
    except OSError as exc:
        _fatal(f"本地服务启动失败（端口 {port}）：{exc}")
        return 1

    threading.Thread(target=httpd.serve_forever, daemon=True, name="http").start()
    threading.Thread(target=_scheduler, daemon=True, name="scheduler").start()
    print(f"Steam 雷达 v{__version__} 已启动：{url}")

    try:
        if args.silent:
            threading.Event().wait()          # 后台常驻，直到进程被杀
        elif args.browser:
            notify.open_in_browser(url)
            threading.Event().wait()
        else:
            ok, message = desktop.open_window(url)
            if ok:
                # 窗口被关掉 = 用户想退出，服务一起收掉
                pass
            else:
                # 没有 Edge/Chrome 之类的情况，退回默认浏览器
                notify.open_in_browser(url)
                print(f"{message}，已在浏览器打开：{url}")
                threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:      # 兜底：任何崩溃都要让用户看到原因
        import traceback
        _fatal(f"程序异常退出：\n\n{exc}\n\n{traceback.format_exc()[-800:]}")
        raise SystemExit(1)
