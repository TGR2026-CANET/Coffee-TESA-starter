"""strategy/buyers.py: เรียนเพดานลับรายต่อราย ปรับหลังรู้ผล ภาพตลาด"""
import unittest

from helpers import cfg_patch, memory_with, summary_row

from strategy import buyers

STEPS = dict(STEP_UP=0.02, STEP_PARTIAL=0.0, STEP_DOWN=0.02, ABOVE_STEP=0.03, PRACTICE_STEP_UP=0.0, CEILING_GAP=0.02,
             OUTBID_FIT=True, OUTBID_UNDERCUT=0.005, OUTBID_MAX_DROP=0.06, TYPE_WEIGHT=0.5,
             SCARCE_FILL=0.8, SCARCE_OFFERS=2.5, CROWDED_OFFERS=6.0, CEILING_CONFLICT_TRUST_PASS=False,
             PRIOR={"SPECIALTY": (1.06, 1.12, 0.65), "COMMERCIAL": (1.02, 1.06, 0.75),
                    "INSTANT": (0.92, 0.94, 0.6), "?": (1.0, 1.05, 0.6)})


def row(n, status, markup, kind="real", price=None, top=None):
    return {"n": n, "status": status, "markup": markup, "kind": kind, "price": price, "rfq_max": top}


class NextFrom(unittest.TestCase):
    def test_each_status(self):
        with cfg_patch(**STEPS):
            self.assertAlmostEqual(buyers._next_from([row(0, "ACCEPTED", 1.04)]), 1.06)
            self.assertAlmostEqual(buyers._next_from([row(0, "PARTIAL", 1.04)]), 1.04)
            self.assertAlmostEqual(buyers._next_from([row(0, "OUTBID", 1.04)]), 1.02)
            self.assertAlmostEqual(buyers._next_from([row(0, "ABOVE_LIMIT", 1.04)]), 1.01)
            self.assertIsNone(buyers._next_from([]))

    def test_practice_accept_does_not_step_up(self):
        with cfg_patch(**STEPS):
            self.assertAlmostEqual(buyers._next_from([row(0, "ACCEPTED", 1.10, kind="practice")]), 1.10)

    def test_most_cautious_of_last_round(self):
        with cfg_patch(**STEPS):
            rows = [row(0, "ACCEPTED", 1.00), row(1, "ACCEPTED", 1.04), row(1, "OUTBID", 1.04)]
            self.assertAlmostEqual(buyers._next_from(rows), 1.02)     # รอบล่าสุดเท่านั้น เลือกค่าที่ระวังสุด

    def test_above_streak_drops_faster(self):
        with cfg_patch(**STEPS):
            rows = [row(0, "ABOVE_LIMIT", 1.06), row(1, "ABOVE_LIMIT", 1.03)]
            self.assertAlmostEqual(buyers._next_from(rows), 1.03 - 0.06)


class AfterOutbid(unittest.TestCase):
    def test_fit_into_winning_range(self):
        with cfg_patch(**STEPS):
            # ยื่น 1.05 (131.45 บาท) คนชนะสูงสุด 127.28 → 1.05 × 127.28/131.45 − 0.005
            self.assertAlmostEqual(buyers._after_outbid(row(0, "OUTBID", 1.05, price=131.45, top=127.28)),
                                   1.05 * 127.28 / 131.45 - 0.005)

    def test_max_drop_per_round(self):
        with cfg_patch(**STEPS):
            self.assertAlmostEqual(buyers._after_outbid(row(0, "OUTBID", 1.10, price=150, top=110)), 1.04)

    def test_inside_range_or_no_data_steps_down(self):
        with cfg_patch(**STEPS):
            self.assertAlmostEqual(buyers._after_outbid(row(0, "OUTBID", 1.04, price=120, top=133)), 1.02)
            self.assertAlmostEqual(buyers._after_outbid(row(0, "OUTBID", 1.04)), 1.02)
        with cfg_patch(**{**STEPS, "OUTBID_FIT": False}):
            self.assertAlmostEqual(buyers._after_outbid(row(0, "OUTBID", 1.05, price=131.45, top=127.28)), 1.03)


class MarketState(unittest.TestCase):
    def rows(self, n, wanted, bought, offers):
        return [{"n": n, "wanted_kg": wanted, "bought_kg": bought, "offers": offers}]

    def test_states(self):
        with cfg_patch(**STEPS):
            self.assertEqual(buyers._market_state(self.rows(4, 600, 240, 2), 5)[0], "scarce")
            self.assertEqual(buyers._market_state(self.rows(4, 600, 600, 9), 5)[0], "crowded")
            self.assertEqual(buyers._market_state(self.rows(4, 600, 300, 8), 5)[0], "normal")   # เพดานตัน ไม่ใช่ของขาด
            self.assertEqual(buyers._market_state(self.rows(1, 600, 240, 2), 5), (None, None, None))  # เก่าเกิน


def rnd(rid, kind, offers, summary):
    return {"round_id": rid, "kind": kind, "my_offers": offers, "market_summary": summary}


def offer(rfq_id, lot_id, price, status, kg=60, filled=None):
    return {"rfq_id": rfq_id, "lot_id": lot_id, "kg": kg, "price_per_kg": price, "status": status,
            "filled_kg": kg if filled is None and status == "ACCEPTED" else (filled or 0)}


class Build(unittest.TestCase):
    TV = {"L-1": 100.0}

    def mem(self):
        # R1: ไทยคั่วบดปฏิเสธที่ 1.04 (เกินเพดาน) · R2: รับล็อตเดียวกันที่ 1.03 (หลักฐานขัดกัน)
        return memory_with([
            rnd("R1", "real", [offer("R1-1", "L-1", 104.0, "ABOVE_LIMIT")], [summary_row("R1-1", "ไทยคั่วบด", "COMMERCIAL")]),
            rnd("R2", "real", [offer("R2-1", "L-1", 103.0, "ACCEPTED")], [summary_row("R2-1", "ไทยคั่วบด", "COMMERCIAL")]),
        ], self.TV)

    def test_ceiling_is_remembered_all_game(self):
        with cfg_patch(**STEPS):
            p = buyers.build(self.mem(), [])["ไทยคั่วบด"]
        self.assertAlmostEqual(p.above, 1.04)
        self.assertAlmostEqual(p.limit, 1.02)
        self.assertLessEqual(p.target, p.limit)               # ACCEPTED แล้วจะขึ้น 1.05 แต่ถูกกดไว้ใต้เพดาน
        self.assertLessEqual(p.cap, p.limit)
        self.assertIn("ceiling-conflict", p.why)

    def test_old_behaviour_switch(self):
        with cfg_patch(**{**STEPS, "CEILING_CONFLICT_TRUST_PASS": True}):
            p = buyers.build(self.mem(), [])["ไทยคั่วบด"]
        self.assertIsNone(p.limit)
        self.assertAlmostEqual(p.target, 1.05)

    def test_new_buyer_uses_prior_and_type_evidence(self):
        with cfg_patch(**STEPS):
            book = buyers.build(self.mem(), [{"buyer": "สยามคอมเมิร์ซ", "buyer_type": "COMMERCIAL"},
                                             {"buyer": "ดอยคราฟต์", "buyer_type": "SPECIALTY"}])
        self.assertAlmostEqual(book["ดอยคราฟต์"].target, 1.06)                 # ไม่มีหลักฐาน = ค่าก่อน
        self.assertAlmostEqual(book["สยามคอมเมิร์ซ"].target, 1.02 + 0.5 * (1.05 - 1.02))   # ผสมหลักฐานประเภทเดียวกันครึ่งหนึ่ง

    def test_overall_p(self):
        with cfg_patch(**STEPS):
            book = buyers.build(None, [{"buyer": "A", "buyer_type": "COMMERCIAL"}, {"buyer": "B", "buyer_type": "INSTANT"}])
        self.assertAlmostEqual(buyers.overall_p(book, ["A", "B"]), (0.75 + 0.6) / 2)
        self.assertEqual(buyers.overall_p(book, []), 0.5)


if __name__ == "__main__":
    unittest.main()
