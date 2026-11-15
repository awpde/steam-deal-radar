"""解析层回归测试。

这些样本是 2026-11-15 从 Steam 真实抓下来的响应，冻结在 tests/fixtures/。
以后不管谁改代码、或者 Steam 改版后我们更新解析器，都必须先跑通这些测试 ——
这是「半年后还能用」的第一道保险。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import parse, rules                      # noqa: E402
from app.models import KIND_DEAL, KIND_FREE, KIND_FTK, GameItem   # noqa: E402

FIX = Path(__file__).parent / "fixtures"


class TestPriceAndReview(unittest.TestCase):
    def test_price_cents(self):
        self.assertEqual(parse.parse_price_cents("¥ 92.99"), 9299)
        self.assertEqual(parse.parse_price_cents("¥1,299"), 129900)
        self.assertEqual(parse.parse_price_cents("免费"), 0)
        self.assertEqual(parse.parse_price_cents("Free"), 0)
        self.assertEqual(parse.parse_price_cents("$19.99"), 1999)
        self.assertIsNone(parse.parse_price_cents(""))

    def test_review(self):
        raw = "好评如潮&lt;br&gt;此游戏的 52,986 篇用户评测中有 96% 为好评。"
        pct, count, desc = parse.parse_review(raw)
        self.assertEqual(pct, 96)
        self.assertEqual(count, 52986)
        self.assertEqual(desc, "好评如潮")

    def test_review_english(self):
        raw = "Very Positive<br>96% of the 1,234 user reviews are positive."
        pct, count, _ = parse.parse_review(raw)
        self.assertEqual(pct, 96)
        self.assertEqual(count, 1234)


class TestFeaturedCategories(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (FIX / "featuredcategories.json").read_text(encoding="utf-8")
        cls.parsed = parse.parse_featuredcategories(cls.text)

    def test_specials_have_expiration(self):
        specials = self.parsed["specials"]
        self.assertGreater(len(specials), 0)
        # 这个接口的价值就是带截止时间，必须解析出来
        with_end = [s for s in specials if s.end_ts]
        self.assertGreater(len(with_end), 0)
        for s in specials:
            self.assertGreater(s.appid, 0)
            self.assertTrue(s.name)

    def test_free_weekend_detected(self):
        self.assertTrue(self.parsed["free_weekend_appids"], "免费周末条目没被识别出来")
        self.assertTrue(all(isinstance(a, int) for a in self.parsed["free_weekend_appids"]))


class TestSearchPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (FIX / "search_specials.json").read_text(encoding="utf-8")
        cls.rows, cls.total, cls.page_size = parse.parse_search_pages(cls.text)

    def test_total_and_rows(self):
        self.assertEqual(self.total, 9661)
        self.assertEqual(len(self.rows), 100, "一页应该解析出 100 条")

    def test_every_row_has_name(self):
        missing = [r.appid for r in self.rows if not r.name]
        self.assertEqual(missing, [], f"这些 appid 没解析出名字：{missing}")

    def test_known_item(self):
        item = next((r for r in self.rows if r.appid == 227300), None)
        self.assertIsNotNone(item)
        self.assertEqual(item.discount, 75)
        self.assertEqual(item.final_cents, 2324)
        self.assertEqual(item.orig_cents, 9299)
        self.assertEqual(item.review_pct, 96)
        self.assertEqual(item.release_year, 2012)
        self.assertEqual(item.kind, KIND_DEAL)

    def test_discounts_are_sane(self):
        for r in self.rows:
            if r.discount:
                self.assertTrue(0 < r.discount <= 100, f"{r.name} 折扣异常 {r.discount}")
                self.assertGreater(r.orig_cents, 0, f"{r.name} 原价缺失")


class TestAppDetails(unittest.TestCase):
    def test_parse(self):
        text = (FIX / "appdetails.json").read_text(encoding="utf-8")
        details = parse.parse_appdetails(text)
        self.assertIn(227300, details)
        d = details[227300]
        self.assertFalse(d["is_free"])
        self.assertEqual(d["discount"], 75)
        self.assertEqual(d["final_cents"], 2324)


class TestRules(unittest.TestCase):
    def _details(self, appid, *, is_free=False, discount=100, final=0, type_="game"):
        return {appid: {"type": type_, "is_free": is_free, "discount": discount,
                        "final_cents": final, "orig_cents": 1000, "coming_soon": False}}

    def test_free_to_keep(self):
        item = GameItem(appid=1, name="限免游戏", discount=100, final_cents=0,
                        orig_cents=1000, review_pct=90)
        out = rules.classify([item], details=self._details(1))
        self.assertEqual(len(out["ftk"]), 1)
        self.assertEqual(out["ftk"][0].kind, KIND_FTK)
        self.assertEqual(out["ftk"][0].confidence, "high")

    def test_permanently_free_is_excluded(self):
        item = GameItem(appid=2, name="免费游戏", discount=100, final_cents=0)
        out = rules.classify([item], details=self._details(2, is_free=True))
        self.assertEqual(out["ftk"], [], "永久免费的游戏不能被当成限免")

    def test_dlc_excluded(self):
        item = GameItem(appid=3, name="某DLC", discount=100, final_cents=0)
        out = rules.classify([item], details=self._details(3, type_="dlc"))
        self.assertEqual(out["ftk"], [])

    def test_free_weekend_separated(self):
        item = GameItem(appid=4, name="周末游戏", discount=100, final_cents=0)
        out = rules.classify([item], free_weekend_appids={4})
        self.assertEqual(out["ftk"], [], "免费周末不能算限免")
        self.assertEqual(len(out["weekend"]), 1)

    def test_unverified_still_listed_but_low_confidence(self):
        item = GameItem(appid=5, name="未核验", discount=100, final_cents=0)
        out = rules.classify([item], details={})
        self.assertEqual(len(out["ftk"]), 1)
        self.assertEqual(out["ftk"][0].confidence, "medium")

    def test_score_orders_better_game_first(self):
        good = GameItem(appid=11, name="好游戏", discount=80, final_cents=2000,
                        orig_cents=10000, review_pct=95, review_count=50000, release_year=2024)
        bad = GameItem(appid=12, name="差游戏", discount=80, final_cents=2000,
                       orig_cents=10000, review_pct=35, review_count=50000, release_year=2010)
        self.assertGreater(rules.score(good), rules.score(bad))


class TestDedupe(unittest.TestCase):
    def test_prefers_item_with_end_time(self):
        a = GameItem(appid=7, name="A", discount=50, final_cents=500, end_ts=None)
        b = GameItem(appid=7, name="A", discount=50, final_cents=500, end_ts=1795456811)
        merged = parse.dedupe([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].end_ts, 1795456811)


if __name__ == "__main__":
    unittest.main(verbosity=2)
