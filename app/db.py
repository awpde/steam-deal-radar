"""本地存储：SQLite。

作用有两个：
1. 缓存 —— Steam 连不上时，界面还能显示「上次成功抓到的数据」，并明确告诉你数据有多旧
2. 历史 —— 记住哪些游戏你之前见过，这样才能判断「这次新出现了哪些限免」
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from . import config
from .models import GameItem

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    appid       INTEGER PRIMARY KEY,
    name        TEXT,
    cover       TEXT,
    orig_cents  INTEGER,
    final_cents INTEGER,
    discount    INTEGER,
    currency    TEXT,
    end_ts      INTEGER,
    release     TEXT,
    release_year INTEGER,
    review_pct  INTEGER,
    review_count INTEGER,
    review_desc TEXT,
    kind        TEXT,
    confidence  TEXT,
    source      TEXT,
    extra       TEXT,
    updated_at  INTEGER
);
CREATE TABLE IF NOT EXISTS seen (
    appid    INTEGER,
    kind     TEXT,
    first_ts INTEGER,
    PRIMARY KEY (appid, kind)
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      INTEGER,
    mode    TEXT,
    status  TEXT,
    total   INTEGER,
    ftk     INTEGER,
    deals   INTEGER,
    elapsed REAL,
    via     TEXT,
    error   TEXT
);
CREATE TABLE IF NOT EXISTS wishlist (
    appid    INTEGER PRIMARY KEY,
    priority INTEGER,
    added_ts INTEGER,
    synced_at INTEGER
);
"""


def connection() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            config.ensure_dirs()
            _conn = sqlite3.connect(config.DB_FILE, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.executescript(SCHEMA)
            _conn.commit()
        return _conn


def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        cur = connection().execute(sql, params)
        connection().commit()
        return cur


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with _lock:
        return connection().execute(sql, params).fetchall()


# ------------------------------------------------------------------ 游戏数据

def upsert_games(items: list[GameItem]) -> list[dict]:
    """写入/更新游戏，并返回「以前从没见过」的那些（用于桌面提醒）。"""
    now = int(time.time())
    first_run = get_meta("initialized") != "1"
    fresh: list[dict] = []
    with _lock:
        conn = connection()
        for it in items:
            conn.execute(
                """INSERT INTO games (appid,name,cover,orig_cents,final_cents,discount,currency,
                                      end_ts,release,release_year,review_pct,review_count,review_desc,
                                      kind,confidence,source,extra,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(appid) DO UPDATE SET
                     name=excluded.name, cover=excluded.cover,
                     orig_cents=excluded.orig_cents, final_cents=excluded.final_cents,
                     discount=excluded.discount, currency=excluded.currency,
                     end_ts=COALESCE(excluded.end_ts, games.end_ts),
                     release=excluded.release, release_year=excluded.release_year,
                     review_pct=COALESCE(excluded.review_pct, games.review_pct),
                     review_count=COALESCE(excluded.review_count, games.review_count),
                     review_desc=COALESCE(NULLIF(excluded.review_desc,''), games.review_desc),
                     kind=excluded.kind, confidence=excluded.confidence,
                     source=excluded.source, extra=excluded.extra, updated_at=excluded.updated_at""",
                (
                    it.appid, it.name, it.cover, it.orig_cents, it.final_cents, it.discount,
                    it.currency, it.end_ts, it.release, it.release_year, it.review_pct,
                    it.review_count, it.review_desc, it.kind, it.confidence, it.source,
                    json.dumps(it.extra, ensure_ascii=False), now,
                ),
            )
            row = conn.execute(
                "SELECT first_ts FROM seen WHERE appid=? AND kind=?", (it.appid, it.kind)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT OR IGNORE INTO seen (appid,kind,first_ts) VALUES (?,?,?)",
                    (it.appid, it.kind, now),
                )
                if not first_run:
                    fresh.append({"appid": it.appid, "name": it.name, "kind": it.kind,
                                  "discount": it.discount, "cover": it.cover,
                                  "final_cents": it.final_cents})
        conn.commit()
    return fresh


def load_games(kind: str | None = None, limit: int = 500) -> list[dict]:
    if kind:
        rows = query(
            "SELECT * FROM games WHERE kind=? ORDER BY discount DESC, review_pct DESC LIMIT ?",
            (kind, limit),
        )
    else:
        rows = query("SELECT * FROM games ORDER BY discount DESC LIMIT ?", (limit,))
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["extra"] = json.loads(d.get("extra") or "{}")
        except json.JSONDecodeError:
            d["extra"] = {}
        d["url"] = f"https://store.steampowered.com/app/{d['appid']}/"
        out.append(d)
    return out


def purge_stale(days: int = 14) -> int:
    """清掉很久没在促销里出现过的记录，避免数据库越长越大。"""
    cutoff = int(time.time()) - days * 86400
    cur = execute("DELETE FROM games WHERE updated_at < ?", (cutoff,))
    return cur.rowcount or 0


# ------------------------------------------------------------------ 元信息

def get_meta(key: str, default: str = "") -> str:
    rows = query("SELECT value FROM meta WHERE key=?", (key,))
    return rows[0]["value"] if rows else default


def set_meta(key: str, value: str) -> None:
    execute(
        "INSERT INTO meta (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def record_run(mode: str, status: str, total: int, ftk: int, deals: int,
               elapsed: float, via: str = "", error: str = "") -> None:
    execute(
        "INSERT INTO runs (ts,mode,status,total,ftk,deals,elapsed,via,error) VALUES (?,?,?,?,?,?,?,?,?)",
        (int(time.time()), mode, status, total, ftk, deals, round(elapsed, 2), via, error[:500]),
    )
    execute("DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY id DESC LIMIT 100)")


def recent_runs(limit: int = 10) -> list[dict]:
    return [dict(r) for r in query("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))]


# ------------------------------------------------------------------ 愿望单

def replace_wishlist(items: list[dict]) -> int:
    now = int(time.time())
    with _lock:
        conn = connection()
        conn.execute("DELETE FROM wishlist")
        conn.executemany(
            "INSERT OR REPLACE INTO wishlist (appid,priority,added_ts,synced_at) VALUES (?,?,?,?)",
            [(i["appid"], i.get("priority", 0), i.get("added_ts", 0), now) for i in items],
        )
        conn.commit()
    return len(items)


def wishlist_appids() -> set[int]:
    return {r["appid"] for r in query("SELECT appid FROM wishlist")}
