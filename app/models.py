"""统一数据结构。

无论数据来自「促销接口」「搜索页」还是「详情接口」，最终都归一化成 GameItem。
上层（规则引擎、数据库、界面）只认这个结构 —— 这是抵抗 Steam 改版的核心。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

STORE_APP_URL = "https://store.steampowered.com/app/{appid}/"

# kind 取值
KIND_FTK = "ftk"              # 限时免费领取（100% off，活动期间入库永久保留）
KIND_DEAL = "deal"            # 限时打折
KIND_FREE_WEEKEND = "freeweekend"   # 免费周末（只能玩、不能领）
KIND_FREE = "free"            # 永久免费（不是促销，只作背景信息）


@dataclass
class GameItem:
    appid: int
    name: str = ""
    cover: str = ""
    orig_cents: int = 0
    final_cents: int = 0
    discount: int = 0
    currency: str = "CNY"
    end_ts: int | None = None          # 促销结束时间（Unix 秒），可能拿不到
    release: str = ""
    release_year: int | None = None
    review_pct: int | None = None      # 好评率
    review_count: int | None = None
    review_desc: str = ""
    kind: str = KIND_DEAL
    confidence: str = "high"           # high / medium / low
    source: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def url(self) -> str:
        return STORE_APP_URL.format(appid=self.appid)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["url"] = self.url
        return d


def clip_text(s: str, limit: int = 200) -> str:
    s = (s or "").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"
