"""取数编排：把各个接口串起来，并负责「坏了怎么办」。

三条保命规则：
1. 任何一个源失败都不影响其他源，最后拼出能拼的部分
2. 全部失败时回落到本地缓存（并标出数据时间），缓存也没有则用内置示例数据
3. 解析异常时把原始响应存到 data/errors/，方便事后 5 分钟内修好
"""
from __future__ import annotations

import json
import re
import time
import traceback
from pathlib import Path

from . import config, db, parse, rules
from .models import KIND_DEAL, KIND_FTK, GameItem
from .netlayer import FetchResult, fetch

FEATURED_URL = "https://store.steampowered.com/api/featuredcategories?cc={cc}&l={lang}"
SEARCH_URL = (
    "https://store.steampowered.com/search/results/?query&start={start}&count=100"
    "&specials=1&category1=998&infinite=1&cc={cc}&l={lang}{sort}"
)
SEARCH_FREE_URL = (
    "https://store.steampowered.com/search/results/?query&start=0&count=100"
    "&specials=1&maxprice=free&category1=998&infinite=1&cc={cc}&l={lang}"
)
APPDETAILS_URL = "https://store.steampowered.com/api/appdetails?appids={ids}&cc={cc}&l={lang}"
WISHLIST_URL = "https://api.steampowered.com/IWishlistService/GetWishlist/v1/?steamid={sid}"
VANITY_URL = "https://steamcommunity.com/id/{vanity}/?xml=1"

BATCH_DETAIL = 20          # 详情接口每批多少 appid（太大容易被拒）
QUICK_PAGES = 2            # 快刷扫几页搜索
MAX_DETAIL_LOOKUPS = 60    # 单次刷新最多核验多少个 appid


class Health:
    """记录每个数据源本次的状态，界面上直接展示，出问题一眼看到是哪一环。"""

    def __init__(self) -> None:
        self.sources: dict[str, dict] = {}

    def add(self, name: str, label: str, ok: bool, status: int = 0,
            elapsed: float = 0.0, count: int = 0, error: str = "", via: str = "") -> None:
        self.sources[name] = {
            "name": name,
            "label": label,
            "ok": ok,
            "status": status,
            "elapsed": round(elapsed, 2),
            "count": count,
            "error": error,
            "via": via,
            "ts": int(time.time()),
        }

    def ok_count(self) -> int:
        return sum(1 for s in self.sources.values() if s["ok"])

    def to_list(self) -> list[dict]:
        return list(self.sources.values())

    def summary(self) -> str:
        return "；".join(
            f"{s['label']}{'正常' if s['ok'] else '失败'}" for s in self.sources.values()
        )


def _dump_error(tag: str, text: str) -> None:
    """保存原始响应，解析器改坏了也能拿真数据复现。"""
    try:
        config.ensure_dirs()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = config.ERROR_DIR / f"{stamp}-{tag}.txt"
        path.write_text((text or "")[:400_000], encoding="utf-8", errors="replace")
    except Exception:
        pass


def _settings() -> dict:
    return config.load_settings()


# ------------------------------------------------------------------ 各个数据源

def fetch_search_pages(
    settings: dict, health: Health, pages: int,
    sort: str = "&sort_by=Price_ASC", deadline: float | None = None,
) -> list[GameItem]:
    items: list[GameItem] = []
    total = 0
    ok_any = False
    first_error = ""
    for page in range(pages):
        start = page * 100
        if total and start >= total:
            break
        if deadline and time.monotonic() > deadline:
            health.add("search_budget", "搜索页", False, error="超出本次刷新时间预算，已停止翻页")
            break
        url = SEARCH_URL.format(start=start, cc=settings["cc"], lang=settings["lang"], sort=sort)
        res = fetch(url, settings=settings)
        if not res.ok:
            first_error = first_error or res.error
            # 排序参数可能不被支持 → 去掉它再试一次
            if sort:
                return items + fetch_search_pages(settings, health, pages, sort="", deadline=deadline)
            continue
        rows, page_total, _ = parse.parse_search_pages(res.text)
        if page_total == 0 and not rows:
            # 有时排序参数会返回空，退回无排序
            if sort and page == 0:
                return fetch_search_pages(settings, health, pages, sort="", deadline=deadline)
        total = page_total or total
        items.extend(rows)
        ok_any = True
        health.add(f"search{page}", f"搜索页 第{page + 1}页", True, res.status, res.elapsed,
                   count=len(rows), via=res.via)
        if not sort and page == 0:
            health.add("search_order", "搜索排序参数", False,
                       error="Price_ASC 不被支持，已退回默认排序（限免可能被漏掉，建议做一次深刷）")
    if not ok_any:
        health.add("search0", "搜索页", False, error=first_error or "全部页面失败")
    return items


def fetch_free_only(settings: dict, health: Health) -> list[GameItem]:
    """专抓「0 元 + 打折中」的条目，是发现限免最省请求的一条路。"""
    url = SEARCH_FREE_URL.format(cc=settings["cc"], lang=settings["lang"])
    res = fetch(url, settings=settings)
    if not res.ok:
        health.add("free", "0元筛选", False, res.status, res.elapsed, error=res.error, via=res.via)
        return []
    rows, total, _ = parse.parse_search_pages(res.text)
    # 打个标记：这些是从「0 元 + 促销中」筛出来的，本身就是强证据
    for it in rows:
        it.source = "free_filter"
    health.add("free", "0元筛选", True, res.status, res.elapsed, count=len(rows), via=res.via)
    return rows


def fetch_details(appids: list[int], settings: dict, health: Health) -> dict[int, dict]:
    out: dict[int, dict] = {}
    if not appids:
        return out
    batches = [appids[i:i + BATCH_DETAIL] for i in range(0, len(appids), BATCH_DETAIL)]
    ok = failed = 0
    last_err = ""
    for batch in batches:
        url = APPDETAILS_URL.format(ids=",".join(str(a) for a in batch),
                                    cc=settings["cc"], lang=settings["lang"])
        res = fetch(url, settings=settings)
        if not res.ok:
            failed += 1
            last_err = res.error
            continue
        try:
            parsed = parse.parse_appdetails(res.text)
        except Exception:
            _dump_error("appdetails", res.text)
            failed += 1
            last_err = "解析失败（已存原始响应）"
            continue
        out.update(parsed)
        ok += 1
    health.add("details", "详情核验", failed == 0, 200 if ok else 0,
               count=len(out), error=last_err if failed else "", via="")
    return out


def resolve_steamid(raw: str, settings: dict) -> tuple[str | None, str]:
    """把用户粘贴的东西（数字 ID / 主页链接 / 自定义名）变成 SteamID64。"""
    raw = (raw or "").strip()
    if not raw:
        return None, "没有填写 SteamID"
    m = re.search(r"(7656119\d{10})", raw)
    if m:
        return m.group(1), ""
    m = re.search(r"/profiles/(\d{17})", raw)
    if m:
        return m.group(1), ""
    vanity = ""
    m = re.search(r"steamcommunity\.com/id/([^/\s?#]+)", raw)
    if m:
        vanity = m.group(1)
    elif re.fullmatch(r"[A-Za-z0-9_\-.]{2,64}", raw):
        vanity = raw
    if not vanity:
        return None, "看不懂这个输入，请粘贴个人主页链接或 17 位 SteamID"
    res = fetch(VANITY_URL.format(vanity=vanity), settings=settings)
    if not res.ok:
        return None, f"无法解析自定义链接（{res.error}）"
    m = re.search(r"<steamID64>(\d+)</steamID64>", res.text)
    if not m:
        return None, "该自定义链接查不到 SteamID（可能改名了，或网络返回了错误页）"
    return m.group(1), ""


def sync_wishlist(steamid: str, settings: dict) -> dict:
    sid, err = resolve_steamid(steamid, settings)
    if not sid:
        return {"ok": False, "error": err, "count": 0}
    res = fetch(WISHLIST_URL.format(sid=sid), settings=settings)
    if not res.ok:
        return {"ok": False, "error": f"愿望单接口失败：{res.error}", "count": 0}
    try:
        items = parse.parse_wishlist(res.text)
    except Exception:
        _dump_error("wishlist", res.text)
        return {"ok": False, "error": "愿望单解析失败", "count": 0}
    n = db.replace_wishlist(items)
    db.set_meta("wishlist_synced_at", str(int(time.time())))
    db.set_meta("wishlist_steamid", sid)
    # 顺便把愿望单里正在打折的补上价格信息
    appids = [i["appid"] for i in items][:120]
    details = fetch_details(appids, settings, Health())
    wl_items = []
    for aid, d in details.items():
        if d.get("discount"):
            wl_items.append(GameItem(
                appid=aid, name=d.get("name", ""), cover=d.get("header_image", ""),
                orig_cents=d.get("orig_cents", 0), final_cents=d.get("final_cents", 0),
                discount=d.get("discount", 0), currency=d.get("currency", "CNY"),
                release=d.get("release_date", ""), kind=KIND_FTK if d.get("discount", 0) >= 100 else KIND_DEAL,
                confidence="high", source="wishlist", extra={"in_wishlist": True},
            ))
    if wl_items:
        db.upsert_games(wl_items)
    return {"ok": True, "count": n, "steamid": sid, "discounted": len(wl_items)}


# ------------------------------------------------------------------ 主流程

BUDGET_SECONDS = {"quick": 50, "deep": 420}   # 单次刷新的硬性时间上限
PROBE_TIMEOUT = 7.0                           # 探针超时：不通就 7 秒内判断出来


def _probe_featured(settings: dict, health: Health) -> tuple[dict | None, bool]:
    """先用短超时探一次，判断「此刻到底通不通」。

    这一步解决的是体验问题：断网时如果按正常超时重试，用户要干等好几分钟。
    现在直连、代理各试 7 秒，都不通就立刻回退到缓存并明确告知。
    返回 (解析结果, 本次该走代理吗)。
    """
    url = FEATURED_URL.format(cc=settings["cc"], lang=settings["lang"])
    plans = [bool(settings.get("proxy_enabled"))]
    if settings.get("proxy_url"):
        plans.append(not plans[0])      # 没有代理地址时不要瞎试
    last_error = ""
    for use_proxy in plans:
        res = fetch(url, settings=settings, timeout=PROBE_TIMEOUT, retries=0,
                    force_proxy=use_proxy)
        if not res.ok:
            last_error = res.error
            continue
        try:
            parsed = parse.parse_featuredcategories(res.text)
        except Exception:
            _dump_error("featuredcategories", res.text)
            last_error = "解析失败（已存原始响应）"
            continue
        label = "官方促销接口（代理）" if use_proxy else "官方促销接口（直连）"
        health.add("featured", label, True, res.status, res.elapsed,
                   count=len(parsed["specials"]), via=res.via)
        return parsed, use_proxy
    health.add("featured", "官方促销接口", False, error=last_error or "连接超时")
    return None, bool(settings.get("proxy_enabled"))


def _offline_result(mode: str, started: float, health: Health,
                    errors: list[str], settings: dict) -> dict:
    """完全连不上时的统一出口：给对方能看懂的原因，而不是一堆报错。"""
    has_cache = bool(db.load_games(limit=1))
    last = int(db.get_meta("last_success_ts") or 0)
    if has_cache:
        errors.append("连不上 Steam（直连和代理都试过了），当前显示的是本地缓存，"
                      "数据时间见顶部。过几分钟再试，或在设置里打开代理。")
    else:
        errors.append("连不上 Steam，本地也还没有缓存，所以先显示内置示例数据。"
                      "请检查网络；如果开了加速器，请在设置里把代理地址填上。")
    elapsed = time.time() - started
    db.record_run(mode, "cache" if has_cache else "demo", 0, 0, 0, elapsed,
                  health.summary(), errors[-1])
    return {
        "ok": False,
        "status": "cache" if has_cache else "demo",
        "mode": mode,
        "elapsed": round(elapsed, 2),
        "total": 0, "ftk": 0, "deals": 0, "weekend": 0,
        "new_ftk": [], "new_deals": [],
        "errors": errors,
        "health": health.to_list(),
        "updated_at": last or int(time.time()),
    }


def refresh(mode: str = "quick", *, trigger: str = "manual") -> dict:
    settings = _settings()
    started = time.time()
    deadline = time.monotonic() + BUDGET_SECONDS.get(mode, 50)
    health = Health()
    errors: list[str] = []

    # 源 A：官方促销接口（唯一带截止时间），同时充当连通性探针
    featured, use_proxy = _probe_featured(settings, health)
    if featured is None:
        return _offline_result(mode, started, health, errors, settings)

    # 探通了就用刚才成功的通道跑完整个刷新，避免一半直连一半代理来回折腾
    settings = dict(settings)
    settings["proxy_enabled"] = use_proxy

    pages = QUICK_PAGES if mode == "quick" else max(1, int(settings["deep_pages"]))

    # 源 B：搜索页
    search_items = fetch_search_pages(settings, health, pages, deadline=deadline)

    # 源 C：0 元专项筛选（快刷时额外打一枪，成本 1 个请求）
    free_items = fetch_free_only(settings, health) if mode == "quick" else []

    candidates = parse.dedupe([*featured.get("specials", []), *free_items, *search_items])

    # 挑出需要二次核验的：可能限免的（价格 0 / 折扣 100 / 没有折扣块却显示免费）
    need_check = [
        g.appid for g in candidates
        if (g.discount >= 90 or g.final_cents == 0) and g.kind != "freeweekend"
    ]
    wishlist_appids = db.wishlist_appids()
    need_check += [a for a in wishlist_appids if a not in need_check]
    need_check = list(dict.fromkeys(need_check))[:MAX_DETAIL_LOOKUPS]

    details = fetch_details(need_check, settings, health) if need_check else {}

    groups = rules.classify(
        candidates,
        details=details,
        free_weekend_appids=featured.get("free_weekend_appids", set()),
        wishlist_appids=wishlist_appids,
    )

    ftk = groups["ftk"]
    deals = rules.pick_top(groups["deals"], int(settings["max_items"]), int(settings["min_review_pct"]))
    all_deals = groups["deals"]
    weekend = groups["weekend"]

    live = health.ok_count() > 0 and (len(candidates) > 0)
    status = "live" if live else ("cache" if db.load_games(limit=1) else "demo")

    if live:
        fresh = db.upsert_games([*ftk, *all_deals, *weekend])
        db.set_meta("last_success_ts", str(int(time.time())))
        db.set_meta("last_mode", mode)
        db.set_meta("initialized", "1")
        db.set_meta("last_health", json.dumps(health.to_list(), ensure_ascii=False))
        new_ftk = [f for f in fresh if f["kind"] == KIND_FTK]
        new_deals = [f for f in fresh if f["kind"] == KIND_DEAL]
    else:
        new_ftk, new_deals = [], []
        if not any(s["ok"] for s in health.sources.values()):
            errors.append("所有数据源都连不上 Steam，已显示本地缓存。")
        else:
            errors.append("接口能连上但没有解析出内容，可能 Steam 改版了（原始响应已存到 data/errors/）。")

    elapsed = time.time() - started
    db.record_run(mode, status, len(candidates), len(ftk), len(deals), elapsed,
                  health.summary(), "; ".join(errors))

    if trigger == "auto" and new_ftk:
        try:
            from . import notify
            notify.notify_new_freebies(new_ftk, settings)
        except Exception:
            pass

    return {
        "ok": live,
        "status": status,
        "mode": mode,
        "elapsed": round(elapsed, 2),
        "total": len(candidates),
        "ftk": len(ftk),
        "deals": len(deals),
        "weekend": len(weekend),
        "new_ftk": new_ftk,
        "new_deals": new_deals,
        "errors": errors,
        "health": health.to_list(),
        "updated_at": int(time.time()),
    }


def load_demo_items() -> list[GameItem]:
    """首次运行且完全没网时，用项目自带样本渲染界面（明确标注为示例）。"""
    items: list[GameItem] = []
    fc = config.SAMPLE_DIR / "featuredcategories.json"
    if fc.exists():
        try:
            parsed = parse.parse_featuredcategories(fc.read_text(encoding="utf-8"))
            items.extend(parsed["specials"])
        except Exception:
            pass
    sp = config.SAMPLE_DIR / "search_specials.json"
    if sp.exists():
        try:
            rows, _, _ = parse.parse_search_pages(sp.read_text(encoding="utf-8"))
            items.extend(rows)
        except Exception:
            pass
    for it in items:
        it.source = "sample"
        it.extra["sample"] = True
    return parse.dedupe(items)
