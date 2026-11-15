"""解析层：把 Steam 返回的三种格式变成统一的 GameItem。

一旦某天 Steam 改版导致解析失败：
1. 程序会把原始响应存到 data/errors/ 目录（见 steam.py）
2. 你只需要改这个文件里的正则/字段名
3. 跑 `python -m unittest discover -s tests` 确认没弄坏其他格式
"""
from __future__ import annotations

import html as html_mod
import json
import re
from typing import Iterable

from .models import KIND_DEAL, KIND_FREE, KIND_FTK, GameItem, clip_text

# ---------------------------------------------------------------- 通用小工具

_PRICE_NUM_RE = re.compile(r"(\d[\d\s.,]*)")


def parse_price_cents(text: str) -> int | None:
    """把 "¥ 92.99" / "$19.99" / "免费" / "Free" 变成「分」为单位的整数。"""
    if text is None:
        return None
    t = str(text).strip()
    if not t:
        return None
    low = t.lower()
    if "免费" in t or low in {"free", "free to play"} or "free" == low:
        return 0
    m = _PRICE_NUM_RE.search(t)
    if not m:
        return None
    raw = m.group(1).replace(" ", "")
    # 逗号可能是千分位也可能是小数分隔符（欧洲区），按最后一段长度判断
    if "," in raw and "." in raw:
        raw = raw.replace(",", "")
    elif "," in raw:
        head, _, tail = raw.rpartition(",")
        raw = f"{head.replace(',', '')}.{tail}" if len(tail) in (1, 2) else raw.replace(",", "")
    try:
        return int(round(float(raw) * 100))
    except ValueError:
        return None


def parse_review(text: str) -> tuple[int | None, int | None, str]:
    """从评价提示气泡里抠出（好评率, 评价数, 文字评价）。"""
    if not text:
        return None, None, ""
    plain = html_mod.unescape(text).replace("<br>", "|").replace("<br/>", "|")
    parts = [p.strip() for p in plain.split("|") if p.strip()]
    desc = parts[0] if parts else ""
    joined = " ".join(parts)
    pct = None
    count = None
    m = re.search(r"(\d+)%\s*为好评", joined) or re.search(r"(\d+)%\s*of the", joined)
    if m:
        pct = int(m.group(1))
    m = re.search(r"([\d,]+)\s*篇用户评测", joined) or re.search(
        r"([\d,]+)\s*user reviews", joined
    )
    if m:
        count = int(m.group(1).replace(",", ""))
    # 只有百分比没有数量时，说明文案格式变了，仍然保留百分比
    return pct, count, clip_text(desc, 24)


def parse_year(text: str) -> int | None:
    if not text:
        return None
    m = re.search(r"(19|20)\d{2}", text)
    return int(m.group(0)) if m else None


# ---------------------------------------------------------------- 格式一：促销接口
# GET /api/featuredcategories?cc=cn&l=schinese
# 特点：条数少（specials 只有 10 条），但每条带 discount_expiration —— 唯一免费的截止时间来源

def parse_featuredcategories(payload: dict | str) -> dict:
    data = json.loads(payload) if isinstance(payload, str) else payload
    out = {"specials": [], "spotlights": [], "free_weekend_appids": set(), "free_weekend": []}
    if not isinstance(data, dict):
        return out

    for key, block in data.items():
        if not isinstance(block, dict):
            continue
        items = block.get("items") or []
        block_name = str(block.get("name") or "")
        is_spot = key.isdigit()
        if key == "specials":
            for it in items:
                item = _item_from_featured(it, source="featuredcategories")
                if item:
                    out["specials"].append(item)
        if is_spot:
            for it in items:
                name = str(it.get("name") or "")
                body = str(it.get("body") or "")
                m = re.search(r"/app/(\d+)", str(it.get("url") or ""))
                appid = int(m.group(1)) if m else None
                spot = {
                    "title": name,
                    "body": body,
                    "appid": appid,
                    "image": it.get("header_image") or it.get("large_capsule_image") or "",
                    "block": block_name,
                }
                out["spotlights"].append(spot)
                if appid and ("免费周末" in name or "free weekend" in name.lower()):
                    out["free_weekend_appids"].add(appid)
                    out["free_weekend"].append(spot)
    return out


def _item_from_featured(it: dict, source: str) -> GameItem | None:
    try:
        appid = int(it.get("id"))
    except (TypeError, ValueError):
        return None
    name = str(it.get("name") or "").strip()
    if not name:
        return None
    disc = int(it.get("discount_percent") or 0)
    orig = int(it.get("original_price") or 0)
    final = int(it.get("final_price") or 0)
    kind = KIND_FTK if disc >= 100 and final == 0 else KIND_DEAL
    if kind == KIND_DEAL and disc <= 0:
        return None
    return GameItem(
        appid=appid,
        name=name,
        cover=str(it.get("header_image") or it.get("large_capsule_image") or ""),
        orig_cents=orig,
        final_cents=final,
        discount=disc,
        currency=str(it.get("currency") or "CNY"),
        end_ts=int(it["discount_expiration"]) if it.get("discount_expiration") else None,
        kind=kind,
        confidence="high" if disc > 0 else "medium",
        source=source,
        extra={"from": "featuredcategories"},
    )


# ---------------------------------------------------------------- 格式二：搜索页
# GET /search/results/?specials=1&infinite=1  → JSON 外壳，results_html 里是 HTML 片段
# 特点：覆盖全站（实测 9600+ 条），但没有促销截止时间

_ROW_SPLIT_RE = re.compile(r'<a href="https://store\.steampowered\.com/app/(\d+)/')
_DISC_BLOCK_RE = re.compile(
    r'class="discount_block search_discount_block"[^>]*?data-price-final="(\d+)"'
    r'[^>]*?data-discount="(\d+)"'
)
_ORIG_RE = re.compile(r'discount_original_price[^"]*"[^>]*>([^<]+)<')
_FINAL_RE = re.compile(r'discount_final_price[^"]*"[^>]*>([^<]+)<')
_TITLE_RE = re.compile(r'<span class="title">(.*?)</span>', re.S)
_COVER_RE = re.compile(r'search_capsule"><img src="([^"]+)"')
_RELEASE_RE = re.compile(r'search_released[^>]*>\s*([^<]*)<', re.S)
_REVIEW_RE = re.compile(
    r'class="search_review_summary ([^"]*)"\s+data-tooltip-html="([^"]*)"', re.S
)
_PRICE_TEXT_RE = re.compile(r'class="search_price[^"]*"[^>]*>\s*([^<]*)<', re.S)


def parse_search_html(page_html: str, source: str = "search") -> list[GameItem]:
    """解析搜索页 HTML 片段，返回若干条（已带折扣或免费信息）。"""
    if not page_html:
        return []
    html = page_html
    marks = [(m.start(), int(m.group(1))) for m in _ROW_SPLIT_RE.finditer(html)]
    items: list[GameItem] = []
    for idx, (pos, appid) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(html)
        chunk = html[pos:min(end, pos + 6000)]   # 单条不会超过这个长度，截断加速
        item = _parse_row(chunk, appid, source)
        if item:
            items.append(item)
    return items


def _parse_row(chunk: str, appid: int, source: str) -> GameItem | None:
    if "data-ds-appid" not in chunk and "search_result_row" not in chunk:
        return None
    # 捆绑包（bundle）没有单独的应用页，跳过
    if 'data-ds-bundleid' in chunk and 'data-ds-appid' not in chunk:
        return None

    name = _clean(_TITLE_RE.search(chunk).group(1)) if _TITLE_RE.search(chunk) else ""
    cover = _COVER_RE.search(chunk).group(1) if _COVER_RE.search(chunk) else ""

    disc_m = _DISC_BLOCK_RE.search(chunk)
    review_pct, review_count, review_desc = (None, None, "")
    rv = _REVIEW_RE.search(chunk)
    if rv:
        review_pct, review_count, review_desc = parse_review(rv.group(2))
        if not review_desc:
            review_desc = _DESC_BY_CLASS.get(rv.group(1).strip(), "")

    release_m = _RELEASE_RE.search(chunk)
    release = _clean(release_m.group(1)) if release_m else ""

    if disc_m:
        final_cents = int(disc_m.group(1))
        discount = int(disc_m.group(2))
        orig_m = _ORIG_RE.search(chunk)
        orig_cents = parse_price_cents(orig_m.group(1)) if orig_m else None
        if not orig_cents and discount > 0 and final_cents:
            orig_cents = int(round(final_cents / (1 - discount / 100)))
        kind = KIND_FTK if discount >= 100 and final_cents == 0 else KIND_DEAL
        return GameItem(
            appid=appid,
            name=name,
            cover=cover,
            orig_cents=orig_cents or 0,
            final_cents=final_cents,
            discount=discount,
            end_ts=None,
            release=release,
            release_year=parse_year(release),
            review_pct=review_pct,
            review_count=review_count,
            review_desc=review_desc,
            kind=kind,
            confidence="high" if discount >= 100 else "high",
            source=source,
            extra={"from": "search"},
        )

    # 没有折扣块：可能是永久免费，也可能只是搜索结果里混进来的
    price_m = _PRICE_TEXT_RE.search(chunk)
    price_text = _clean(price_m.group(1)) if price_m else ""
    if price_text and ("免费" in price_text or price_text.lower() == "free"):
        return GameItem(
            appid=appid,
            name=name,
            cover=cover,
            final_cents=0,
            discount=0,
            release=release,
            release_year=parse_year(release),
            review_pct=review_pct,
            review_count=review_count,
            review_desc=review_desc,
            kind=KIND_FREE,
            confidence="low",     # 需要详情接口确认是「永久免费」还是「限免」
            source=source,
            extra={"from": "search"},
        )
    return None


_DESC_BY_CLASS = {
    "overwhelmingly_positive": "好评如潮",
    "very_positive": "特别好评",
    "positive": "好评",
    "mostly_positive": "多半好评",
    "mixed": "褒贬不一",
    "mostly_negative": "多半差评",
    "negative": "差评",
    "very_negative": "特别差评",
    "overwhelmingly_negative": "差评如潮",
}


def _clean(s: str) -> str:
    return html_mod.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def parse_search_pages(text: str) -> tuple[list[GameItem], int, int]:
    """解析 infinite 接口的 JSON 外壳，返回 (条目, 总数, 本页条数)。"""
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return [], 0, 0
    if isinstance(payload, list):     # 少数情况下直接返回数组
        return [], 0, 0
    rows = parse_search_html(str(payload.get("results_html") or ""))
    return rows, int(payload.get("total_count") or 0), len(rows)


# ---------------------------------------------------------------- 格式三：应用详情
# GET /api/appdetails?appids=570,227300&cc=cn&l=schinese
# 注意：不要传 filters 参数，写错一个值会让整个响应变成 null（实测）

def parse_appdetails(payload: dict | str) -> dict[int, dict]:
    data = json.loads(payload) if isinstance(payload, str) else payload
    out: dict[int, dict] = {}
    if not isinstance(data, dict):
        return out
    for key, val in data.items():
        try:
            appid = int(key)
        except (TypeError, ValueError):
            continue
        if not isinstance(val, dict) or not val.get("success"):
            continue
        d = val.get("data") or {}
        price = d.get("price_overview") or {}
        out[appid] = {
            "type": str(d.get("type") or ""),
            "name": str(d.get("name") or ""),
            "is_free": bool(d.get("is_free")),
            "discount": int(price.get("discount_percent") or 0),
            "final_cents": int(price.get("final") or 0),
            "orig_cents": int(price.get("initial") or 0),
            "currency": str(price.get("currency") or "CNY"),
            "release_date": str((d.get("release_date") or {}).get("date") or ""),
            "coming_soon": bool((d.get("release_date") or {}).get("coming_soon")),
            "short_description": str(d.get("short_description") or ""),
            "header_image": str(d.get("header_image") or ""),
            "developers": d.get("developers") or [],
            "genres": [g.get("description") for g in (d.get("genres") or []) if isinstance(g, dict)],
        }
    return out


# ---------------------------------------------------------------- 格式四：愿望单
# GET https://api.steampowered.com/IWishlistService/GetWishlist/v1/?steamid=xxx
# 公开愿望单无需 API Key

def parse_wishlist(payload: dict | str) -> list[dict]:
    data = json.loads(payload) if isinstance(payload, str) else payload
    resp = (data or {}).get("response") or {}
    items = resp.get("items") or []
    out = []
    for it in items:
        try:
            out.append(
                {
                    "appid": int(it.get("appid")),
                    "priority": int(it.get("priority") or 0),
                    "added_ts": int(it.get("date_added") or 0),
                }
            )
        except (TypeError, ValueError):
            continue
    return out


def dedupe(items: Iterable[GameItem]) -> list[GameItem]:
    """同一 appid 只留信息最全的一条（有截止时间 > 有评价数 > 先来的）。"""
    best: dict[int, GameItem] = {}
    for it in items:
        cur = best.get(it.appid)
        if cur is None:
            best[it.appid] = it
            continue
        score_new = (it.end_ts is not None) * 4 + (it.review_count or 0 > 0) * 2 + (1 if it.cover else 0)
        score_cur = (cur.end_ts is not None) * 4 + (cur.review_count or 0 > 0) * 2 + (1 if cur.cover else 0)
        if score_new > score_cur:
            # 合并：保留新条目的截止时间等，但补上旧条目的评价信息
            if it.review_pct is None and cur.review_pct is not None:
                it.review_pct = cur.review_pct
                it.review_count = cur.review_count
                it.review_desc = cur.review_desc
            best[it.appid] = it
        else:
            if cur.review_pct is None and it.review_pct is not None:
                cur.review_pct = it.review_pct
                cur.review_count = it.review_count
                cur.review_desc = it.review_desc
    return list(best.values())
