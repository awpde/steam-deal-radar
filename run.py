"""Steam 限免 & 折扣雷达 —— 程序入口。

双击「启动.bat」就是跑这个文件。
"""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import config, db, notify, server, steam   # noqa: E402


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
    parser.add_argument("--silent", action="store_true", help="不自动打开浏览器（开机自启用）")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--refresh-once", action="store_true", help="只刷新一次然后退出")
    args = parser.parse_args()

    config.ensure_dirs()
    db.connection()
    settings = config.load_settings()
    port = args.port or int(settings.get("port") or 8731)

    if args.refresh_once:
        result = steam.refresh("quick", trigger="manual")
        print(f"状态：{result['status']}｜候选 {result['total']} 条｜限免 {result['ftk']} 条"
              f"｜折扣 {result['deals']} 条｜耗时 {result['elapsed']}s")
        for err in result.get("errors") or []:
            print("提示：", err)
        return 0 if result["ok"] else 1

    # 已经在跑就直接把浏览器叫起来，不要重复启动
    if server.port_in_use(port):
        notify.open_in_browser(f"http://127.0.0.1:{port}/")
        return 0

    httpd = server.make_server(port)
    threading.Thread(target=_scheduler, daemon=True, name="scheduler").start()

    url = f"http://127.0.0.1:{port}/"
    print(f"Steam 雷达已启动：{url}")
    print("关闭这个窗口就会停止程序。")
    if not args.silent:
        threading.Timer(0.9, lambda: notify.open_in_browser(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
