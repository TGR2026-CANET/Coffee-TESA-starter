"""report.py (ตารางบนจอ) และ ranks.py (อันดับต่อเกม)"""
import contextlib
import io
import unittest

from helpers import TMP, lot, rfq, state, summary_row

import ranks
import report


class Report(unittest.TestCase):
    RESULTS = {"round_id": "R2", "kind": "real",
               "my_offers": [{"rfq_id": "R2-1", "lot_id": "L-1", "kg": 120, "price_per_kg": 131.45,
                              "status": "OUTBID", "filled_kg": 0},
                             {"rfq_id": "R2-2", "lot_id": "L-1", "kg": 60, "price_per_kg": 120.0,
                              "status": "ACCEPTED", "filled_kg": 60}],
               "market_summary": [summary_row("R2-1", "ไทยคั่วบด", "COMMERCIAL", pmin=120, pmax=127.28),
                                  summary_row("R2-2", "สยามคอมเมิร์ซ", "COMMERCIAL", pmin=110, pmax=125)]}

    def test_summarize(self):
        s = report.summarize(self.RESULTS, {"L-1": 120.0})
        self.assertEqual((s["offered_kg"], s["sold_kg"], s["revenue"]), (180, 60, 7200.0))
        self.assertEqual(s["my_offers"][1]["markup"], 1.0)
        self.assertEqual(s["buyers"]["ไทยคั่วบด"]["rfqs"], 1)

    def test_outbid_shows_gap_to_winner(self):
        s = report.summarize(self.RESULTS, {"L-1": 120.0})
        self.assertIn("แพงกว่าคนชนะ 3.3%", report._status(s["my_offers"][0]))
        self.assertEqual(report._status(s["my_offers"][1]), "ขายได้")

    def test_thai_width(self):
        self.assertEqual(report._width("ผู้ซื้อ"), 3)                       # สระ/วรรณยุกต์ไม่กินที่
        lines = report.table(["ผู้ซื้อ", "กก."], [["ไทยคั่วบด", 60]], right={1}).splitlines()
        self.assertEqual(len({report._width(x) for x in lines}), 1)          # ทุกบรรทัดกว้างเท่ากัน

    def test_show_sent_prints_table(self):
        st = state([lot("L-1")], [rfq("C1", "ไทยคั่วบด", "COMMERCIAL")], capacity=360)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            report.show_sent(st, [{"rfq_id": "C1", "lot_id": "L-1", "kg": 120, "price_per_kg": 122.4}],
                             {"L-1": 120.0}, "rules", {"pressure": 0.9, "adjust": [], "market": {"ไทยคั่วบด": "scarce"},
                                                       "budget_kg": 240})
        out = buf.getvalue()
        self.assertIn("ไทยคั่วบด", out)
        self.assertIn("1.020", out)
        self.assertIn("ของขาด", out)
        self.assertIn("240 (โควตา 360", out)


class FakeApi:
    def __init__(self, body):
        self.body = body

    def call(self, method, path, tries=4):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class Ranks(unittest.TestCase):
    ST = {"market": "practice", "team_id": "T02", "game_id": 77,
          "standings": {"kg_sold": 1500, "kg_remaining": 60, "revenue_thb": 165000.0, "avg_price_per_kg": 110.0}}

    def test_snapshot_rows_format(self):
        api = FakeApi({"game_id": 77, "final": True, "rows": [
            {"rank": 1, "team_id": "B1", "bot": True}, {"rank": 2, "team_id": "T05", "bot": False},
            {"rank": 3, "team_id": "T02", "bot": False}]})
        snap = ranks.snapshot(api, self.ST)
        self.assertEqual((snap["rank"], snap["teams"], snap["rank_humans"], snap["humans"], snap["final"]),
                         (3, 3, 2, 2, True))

    def test_snapshot_ranking_format_without_bot_flag(self):
        api = FakeApi({"final": False, "ranking": [{"rank": 1, "team_id": "BOT01"}, {"rank": 2, "team_id": "T04"}]})
        snap = ranks.snapshot(api, {**self.ST, "team_id": "T04"})
        self.assertEqual((snap["rank"], snap["teams"], snap["rank_humans"], snap["humans"]), (2, 2, 1, 1))

    def test_snapshot_failures(self):
        self.assertIsNone(ranks.snapshot(FakeApi(RuntimeError("down")), self.ST))
        self.assertIsNone(ranks.snapshot(FakeApi({"rows": [{"rank": 1, "team_id": "X"}]}), self.ST))
        self.assertEqual(ranks.text(None), "อันดับ: ถามไม่ได้")

    def test_save_once_per_game_and_show(self):
        folder = TMP / "ranks-test"
        snap = {"game_id": 77, "rank": 2, "teams": 9, "rank_humans": 2, "humans": 3, "leader": "T05",
                "final": True, "frozen": False}
        self.assertTrue(ranks.save(folder, self.ST, snap, {"pr": 0.99, "total": 89.5}))
        self.assertFalse(ranks.save(folder, self.ST, snap, {"pr": 0.99, "total": 89.5}))      # รีสตาร์ตแล้วไม่ซ้ำ
        self.assertTrue(ranks.save(folder, {**self.ST, "game_id": 78}, {**snap, "game_id": 78}))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ranks.show(folder)
        self.assertIn("2/9", buf.getvalue())
        self.assertIn("อันดับ 2 เกมล่าสุด", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
