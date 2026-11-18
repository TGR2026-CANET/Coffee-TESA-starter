"""strategy/pacing.py: ปฏิทิน รอบที่เหลือ ความกดดัน งบต่อรอบ ช่วงท้ายเกม"""
import unittest

from helpers import COMPETITION_8, NORMAL_8, RULES, cfg_patch, lot, state

from strategy import pacing


def st(number, kg, kind="real", cap=360):
    return state([lot("L-1", kg)], [], number=number, kind=kind, capacity=cap, round_capacity=cap)


class Calendar(unittest.TestCase):
    def test_round_numbers_formats(self):
        self.assertEqual(pacing.round_numbers("1-8"), set(range(1, 9)))      # ตลาด 202.29.227.22
        self.assertEqual(pacing.round_numbers([1, 2]), {1, 2})               # ตลาดฝึก
        self.assertEqual(pacing.round_numbers("R3-R4"), {3, 4})
        self.assertEqual(pacing.round_numbers("1,2,5"), {1, 2, 5})
        self.assertEqual(pacing.round_numbers(7), {7})
        self.assertEqual(pacing.round_numbers(None), set())
        self.assertEqual(pacing.round_numbers("abc"), set())

    def test_season(self):
        self.assertEqual(pacing.season(NORMAL_8, 5)["label"], "ปกติ")
        self.assertEqual(pacing.season(COMPETITION_8, 3)["demand_weight"], 1.4)
        self.assertEqual(pacing.season(COMPETITION_8, 99)["demand_weight"], 1.0)   # ไม่พบ = ปกติ

    def test_rounds_left_and_last(self):
        self.assertEqual(pacing.rounds_left(st(3, 600), RULES), 6)
        self.assertEqual(pacing.rounds_left(st(1, 600, kind="practice"), RULES), 8)
        self.assertFalse(pacing.is_last(st(7, 600), RULES))
        self.assertTrue(pacing.is_last(st(8, 600), RULES))

    def test_peak_and_better_ahead(self):
        self.assertTrue(pacing.peak_ahead(st(2, 600), RULES, COMPETITION_8))
        self.assertFalse(pacing.peak_ahead(st(4, 600), RULES, COMPETITION_8))      # รอบนี้ไม่นับ
        self.assertTrue(pacing.better_ahead(st(5, 600), RULES, COMPETITION_8))
        self.assertFalse(pacing.better_ahead(st(7, 600), RULES, COMPETITION_8))    # ท้ายตลาดซบเซา


class Pressure(unittest.TestCase):
    def test_counts_slump_rounds_as_less(self):
        s = st(5, 1000)
        normal = pacing.pressure(s, RULES, 0.7, NORMAL_8)
        slump_end = pacing.pressure(s, RULES, 0.7, COMPETITION_8)
        self.assertAlmostEqual(normal, 1000 / (360 * 4 * 0.7))
        self.assertAlmostEqual(slump_end, 1000 / (360 * 3.4 * 0.7))
        self.assertGreater(slump_end, normal)

    def test_p_sell_floor(self):
        self.assertAlmostEqual(pacing.pressure(st(5, 1000), RULES, 0.0, NORMAL_8), 1000 / (360 * 4 * 0.2))


class Budget(unittest.TestCase):
    def test_off_means_full_quota(self):
        with cfg_patch(PACE_BUDGET=False):
            self.assertEqual(pacing.offer_budget(st(3, 120), RULES, 0.7, NORMAL_8), 360)

    def test_on_weights_by_calendar(self):
        with cfg_patch(PACE_BUDGET=True, PACE_MIN_P=0.5):
            # รอบ 5: น้ำหนักที่เหลือ [1, 1, 0.7, 0.7] → ส่วนแบ่ง 1/3.4 · 340 กก. ÷ 3.4 ÷ 0.5 = 200 → 3 กระสอบ
            self.assertEqual(pacing.offer_budget(st(5, 340), RULES, 0.5, COMPETITION_8), 180)
            self.assertEqual(pacing.offer_budget(st(8, 340), RULES, 0.5, COMPETITION_8), 360)   # รอบสุดท้าย = เต็ม
            self.assertEqual(pacing.offer_budget(st(5, 5000), RULES, 0.5, COMPETITION_8), 360)  # ไม่เกินโควตา


class Late(unittest.TestCase):
    def test_late_only_when_not_slump(self):
        with cfg_patch(LATE_ROUNDS=2, LATE_SLACK=0.8):
            self.assertTrue(pacing.late(st(7, 400), RULES, NORMAL_8))
            self.assertFalse(pacing.late(st(7, 400), RULES, COMPETITION_8))   # ท้ายตลาดซบเซา ไม่สู้ราคา
            self.assertFalse(pacing.late(st(5, 400), RULES, NORMAL_8))        # ยังไม่ถึงช่วงท้าย
            self.assertFalse(pacing.late(st(7, 700), RULES, NORMAL_8))        # ของเหลือเยอะ ไม่มีที่แก้ตัว


if __name__ == "__main__":
    unittest.main()
