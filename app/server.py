"""本地 Web 服务：界面和后台逻辑之间的桥。

浏览器打开 http://127.0.0.1:8731 就能看到界面。
刷新动作放到后台线程里跑，所以哪怕深刷要几分钟，页面也不会卡住。
"""
from __future__ import annotations

import csv
import io
import json
import mimetypes
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config, db, notify, steam

JOB: dict = {"running": False, "mode": None, "started": 0, "result": None, "error": ""}
JOB_LOCK = threading.Lock()


# ------------------------------------------------------------------ 状态组装

def _status_of() -> str:
    if db.get_meta("last_success_ts"):
        return "live"
    return "never"


def build_state() -> dict:
    settings = config.load_settings()
    last_success = int(db.get_meta("last_success_ts") or 0)
    games = db.load_games(limit=3000)
    wishlist = db.wishlist_appids()

    ftk = [g for g in games if g["kind"] == "ftk"]
    deals = [g for g in games if g["kind"] == "deal"]
    weekend = [g for g in games if g["kind"] == "freeweekend"]

    for g in games:
        g["in_wishlist"] = g["appid"] in wishlist
        g["score"] = (g.get("extra") or {}).get("score", 0)

    ftk.sort(key=lambda g: (-(g.get("review_pct") or 0), g["name"]))
    deals.sort(key=lambda g: (-g.get("score", 0), -g.get("discount", 0)))
    wishlist_deals = sorted(
        [g for g in deals if g["appid"] in wishlist],
        key=lambda g: (-g.get("discount", 0), g["name"]),
    )

    result = JOB.get("result") or {}
    new_ids = {i["appid"] for i in (result.get("new_ftk") or [])}
    for g in ftk:
        g["is_new"] = g["appid"] in new_ids

    status = "live" if last_success else "never"
    demo = []
    if not games:
        # 一次都没成功过：用项目自带样本渲染，明确告诉用户这是示例
        demo = [i.to_dict() for i in steam.load_demo_items()]
        status = "demo"

    limit = int(settings["max_items"])
    return {
        "settings": settings,
        "status": status,
        "updated_at": JOB.get("finished_at") or last_success or 0,
        "last_success_ts": last_success,
        "last_success_text": _fmt_ts(last_success),
        "job": {
            "running": JOB["running"],
            "mode": JOB.get("mode"),
            "started": JOB.get("started", 0),
            "error": JOB.get("error", ""),
        },
        "result": {
            "ok": result.get("ok"),
            "mode": result.get("mode"),
            "elapsed": result.get("elapsed"),
            "errors": result.get("errors") or [],
            "new_ftk": result.get("new_ftk") or [],
        },
        "counts": {"ftk": len(ftk), "deals": len(deals), "weekend": len(weekend),
                   "wishlist": len(wishlist_deals)},
        "ftk": ftk[:limit],
        "deals": deals[:limit],
        "weekend": weekend[:limit],
        "wishlist_deals": wishlist_deals[:limit],
        "demo": demo[:limit],
        "health": json.loads(db.get_meta("last_health") or "[]"),
        "autostart": notify.autostart_enabled(),
        "runs": db.recent_runs(8),
        "server_time": int(time.time()),
    }


def _fmt_ts(ts: int) -> str:
    if not ts:
        return "从未成功"
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


# ------------------------------------------------------------------ 后台刷新

def start_refresh(mode: str = "quick", trigger: str = "manual") -> dict:
    with JOB_LOCK:
        if JOB["running"]:
            return {"ok": False, "running": True, "message": "正在刷新，请稍等"}
        JOB.update({"running": True, "mode": mode, "started": int(time.time()),
                    "error": "", "result": None})

    def worker() -> None:
        try:
            result = steam.refresh(mode, trigger=trigger)
            JOB["result"] = result
            JOB["finished_at"] = int(time.time())
        except Exception as exc:  # 任何未预料的异常都不该让服务挂掉
            JOB["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            JOB["running"] = False

    threading.Thread(target=worker, daemon=True, name="refresh").start()
    return {"ok": True, "running": True}


# ------------------------------------------------------------------ HTTP 处理

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "SteamDealRadar/1.0"

    def log_message(self, fmt: str, *args) -> None:   # 静音，避免刷屏
        pass

    # ---------- 工具方法
    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, data, code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _serve_file(self, rel: str) -> None:
        base = config.WEB_DIR.resolve()
        target = (base / rel).resolve()
        if not str(target).startswith(str(base)) or not target.is_file():
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        ctype = CONTENT_TYPES.get(target.suffix.lower()) or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype)

    # ---------- 路由
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._serve_file("index.html")
            return
        if path.startswith("/static/"):
            self._serve_file(path[len("/static/"):])
            return
        if path == "/api/state":
            self._json(build_state())
            return
        if path == "/api/health":
            self._json({"ok": True, "sources": json.loads(db.get_meta("last_health") or "[]"),
                        "job": JOB["running"]})
            return
        if path == "/api/export":
            self._export(str((query.get("what") or ["deals"])[0]))
            return
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        data = self._body()

        if path == "/api/refresh":
            mode = str(data.get("mode") or "quick")
            if mode not in ("quick", "deep"):
                mode = "quick"
            self._json(start_refresh(mode, trigger=str(data.get("trigger") or "manual")))
            return

        if path == "/api/settings":
            patch = data.get("patch") if isinstance(data.get("patch"), dict) else data
            saved = config.save_settings(patch)
            message = "已保存"
            if "autostart_enabled" in (patch or {}):
                ok, msg = notify.set_autostart(bool(saved.get("autostart_enabled")))
                message = msg
            self._json({"ok": True, "settings": saved, "message": message})
            return

        if path == "/api/wishlist/sync":
            settings = config.load_settings()
            raw = str(data.get("steamid") or settings.get("wishlist_steamid") or "")
            if raw:
                config.save_settings({"wishlist_steamid": raw})
            result = steam.sync_wishlist(raw, config.load_settings())
            self._json(result)
            return

        if path == "/api/notify/test":
            ok = notify.toast("Steam 雷达", "通知功能正常，发现新的限免时会这样提醒你")
            self._json({"ok": ok})
            return

        if path == "/api/open-store":
            appid = str(data.get("appid") or "")
            if appid.isdigit():
                notify.open_in_browser(f"https://store.steampowered.com/app/{appid}/")
            self._json({"ok": True})
            return

        self._send(404, b"not found", "text/plain; charset=utf-8")

    # ---------- 导出
    def _export(self, what: str) -> None:
        state = build_state()
        rows = {"deals": state["deals"], "ftk": state["ftk"],
                "wishlist": state["wishlist_deals"]}.get(what, state["deals"])
        buf = io.StringIO()
        buf.write("\ufeff")      # BOM，Excel 打开中文不乱码
        writer = csv.writer(buf)
        writer.writerow(["名称", "好评率", "评价数", "原价(元)", "现价(元)", "折扣%", "结束时间", "链接"])
        for g in rows:
            writer.writerow([
                g.get("name", ""),
                g.get("review_pct") or "",
                g.get("review_count") or "",
                round((g.get("orig_cents") or 0) / 100, 2),
                round((g.get("final_cents") or 0) / 100, 2),
                g.get("discount") or 0,
                datetime.fromtimestamp(g["end_ts"]).strftime("%Y-%m-%d %H:%M") if g.get("end_ts") else "",
                g.get("url", ""),
            ])
        body = buf.getvalue().encode("utf-8")
        name = f"steam-{what}-{datetime.now().strftime('%Y%m%d-%H%M')}.csv"
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def make_server(port: int | None = None) -> ThreadingHTTPServer:
    settings = config.load_settings()
    port = port or int(settings.get("port") or 8731)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def port_in_use(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0
