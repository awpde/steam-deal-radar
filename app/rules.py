"""规则引擎：判定「这是不是限时免费」、给折扣排序打分。

这里不联网、不读数据库，纯函数，所以可以拿历史数据反复回放测试。
"""
from __future__ import annotations

import math
import re
import time

from .models import KIND_DEAL, KIND_FREE, KIND_FREE_WEEKEND, KIND_FTK, GameItem

# 好评率低于这个值、且评价数足够多的游戏，不进入「精选」但保留在「全站折扣」
HARD_MIN_REVIEW = 60
MIN_REVIEW_COUNT_FOR_FILTER = 100

# 拿不到详情接口时的兜底判断：名字像原声带/DLC/季票的，降权处理
# （详情接口能连上时以 type 字段为准，更准确）
EXTRA_NAME_RE = re.compile(
    r"(soundtrack|original\s*soundtrack|\bost\b|artbook|art\s*book|season\s*pass|"
    r"expansion\s*pass|upgrade|dlc|原声|音乐包|季票|美术集|资料片)",
    re.I,
)


def classify(
    items: list[GameItem],
    *,
    details: dict[int, dict] | None = None,
    free_weekend_appids: set[int] | None = None,
    wishlist_appids: set[int] | None = None,
) -> dict:
    """把候选集分成三组，并标注可信度。

    三层判定（缺一层就会误报）：
      ① 价格层：折扣 100% 且到手价 0
      ② 语义层：详情接口 is_free=False（排除「永久免费」游戏，它们本来就不要钱）
      ③ 类型层：详情接口 type=game（排除 DLC/试玩/原声），并排除「免费周末」（只能玩不能领）
    """
    details = details or {}
    free_weekend_appids = free_weekend_appids or set()
    wishlist_appids = wishlist_appids or set()
    now = time.time()

    ftk: list[GameItem] = []
    deals: list[GameItem] = []
    weekend: list[GameItem] = []
    ignored: list[GameItem] = []

    for it in items:
        detail = details.get(it.appid)

        # 免费周末：活动期间价格也是 0，但结束后游戏会从库里消失，必须单独归类
        if it.appid in free_weekend_appids:
            it.kind = KIND_FREE_WEEKEND
            it.confidence = "high"
            it.extra["note"] = "免费周末（只能在活动期间玩，不能永久保留）"
            weekend.append(it)
            continue

        if detail is not None:
            if detail.get("coming_soon"):
                ignored.append(it)
                continue
            if detail.get("type") and detail.get("type") != "game":
                ignored.append(it)          # DLC / 原声 / 试玩
                continue
            if detail.get("is_free"):
                it.kind = KIND_FREE
                it.confidence = "high"
                ignored.append(it)          # 永久免费，不是促销
                continue
            # 以详情接口的价格为准（搜索页可能滞后）
            if detail.get("discount"):
                it.discount = detail["discount"]
                it.final_cents = detail.get("final_cents", it.final_cents)
                it.orig_cents = detail.get("orig_cents") or it.orig_cents

        # 限免判据：折扣 100% 且到手 0 元；
        # 或者来自「0 元 + 促销中」专项筛选（这本身已经证明它在活动里）
        promo_zero = (it.discount >= 100 and it.final_cents == 0) or (
            it.final_cents == 0 and it.source == "free_filter"
        )
        if promo_zero:
            it.kind = KIND_FTK
            if detail is None:
                it.confidence = "medium"    # 没拿到详情：可能是限免，也可能漏判
                it.extra["note"] = "价格显示 0 元且在促销中，但未能二次核实（网络或接口问题）"
            else:
                it.confidence = "high"
            ftk.append(it)
        elif it.discount > 0:
            it.kind = KIND_DEAL
            deals.append(it)
        else:
            ignored.append(it)

    # 折扣榜打分；顺带处理「详情接口挂了」时的矮子里拔将军
    for it in deals:
        value = score(it, now)
        if it.appid not in details and EXTRA_NAME_RE.search(it.name or ""):
            it.extra["suspect_extra"] = True
            it.extra["note"] = "名字像是原声/DLC，未能核实（详情接口不可用），已降权"
            value *= 0.45
        if it.appid in wishlist_appids:
            value *= 1.18
            it.extra["in_wishlist"] = True
        it.extra["score"] = round(value, 3)

    ftk.sort(key=lambda g: (-(g.review_pct or 0), g.name))
    deals.sort(key=lambda g: -g.extra.get("score", 0))
    weekend.sort(key=lambda g: g.name)
    return {"ftk": ftk, "deals": deals, "weekend": weekend, "ignored": ignored}


def score(item: GameItem, now: float | None = None) -> float:
    """0~1 的推荐分：折扣力度、口碑、热度、新旧各占权重。"""
    now = now or time.time()
    disc = min(item.discount / 100.0, 1.0)
    review = (item.review_pct if item.review_pct is not None else 65) / 100.0
    volume = min(math.log10(max(item.review_count or 1, 1)) / 5.0, 1.0)  # 10 万条评价即满分
    year = item.release_year or 0
    age = (time.gmtime(now).tm_year - year) if year else 99
    fresh = 1.0 if age <= 2 else (0.75 if age <= 5 else 0.5)
    base = 0.45 * disc + 0.30 * review + 0.15 * volume + 0.10 * fresh
    if (
        item.review_pct is not None
        and item.review_pct < HARD_MIN_REVIEW
        and (item.review_count or 0) >= MIN_REVIEW_COUNT_FOR_FILTER
    ):
        base *= 0.45                       # 差评如潮的骨折价，别推
    return round(base, 4)


def is_worth_showing(item: GameItem, min_review_pct: int) -> bool:
    """精选榜的最低门槛：评价数够多且好评率达标。评价数太少的不做判断。"""
    if item.review_pct is None:
        return True
    if (item.review_count or 0) < MIN_REVIEW_COUNT_FOR_FILTER:
        return True
    return item.review_pct >= min_review_pct


def pick_top(items: list[GameItem], limit: int, min_review_pct: int) -> list[GameItem]:
    good = [i for i in items if is_worth_showing(i, min_review_pct)]
    return good[:limit]
