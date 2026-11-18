"""appraisal.py: สูตร TV และค่าประเมินภายใต้ noise ของผลวัดตัวอย่าง"""
import unittest

import helpers
from helpers import RULES, cfg_patch, sample

import appraisal


class GradeAndFactors(unittest.TestCase):
    def test_grade_steps(self):
        self.assertEqual(appraisal.grade(RULES, 0.5), ("PREMIUM", 1.08))
        self.assertEqual(appraisal.grade(RULES, 1.0), ("PREMIUM", 1.08))      # ขั้นรวมขอบบน
        self.assertEqual(appraisal.grade(RULES, 1.01)[0], "GRADE_1")
        self.assertEqual(appraisal.grade(RULES, 6.9)[0], "GRADE_3")
        self.assertEqual(appraisal.grade(RULES, 12.0)[0], "BELOW_STANDARD")
        self.assertEqual(appraisal.grade(RULES, 150.0)[0], "REJECT")          # เกินขั้นสุดท้าย = ขั้นสุดท้าย

    def test_moisture_factor(self):
        mo = RULES["moisture"]
        self.assertEqual(appraisal.moisture_factor(RULES, mo["ref"]), 1.0)
        self.assertEqual(appraisal.moisture_factor(RULES, 10.0), 1.0)
        mid = appraisal.moisture_factor(RULES, mo["ref"] + 1)
        self.assertTrue(0 < mid < 1)
        self.assertEqual(appraisal.moisture_factor(RULES, mo["hard_limit"] + 0.1), mo["hard_factor"])

    def test_screen_factor_clamps_to_table(self):
        codes = sorted(int(k) for k in RULES["screen_factors"])
        self.assertEqual(appraisal.screen_factor(RULES, codes[0] - 5), RULES["screen_factors"][str(codes[0])])
        self.assertEqual(appraisal.screen_factor(RULES, codes[-1] + 5), RULES["screen_factors"][str(codes[-1])])

    def test_true_value_matches_market(self):
        # เกม 39 LOT-T02-3: ตัวอย่าง d0.31 m10.63 s15 → agent ประเมิน 129.2 (สูตรตรง)
        self.assertAlmostEqual(appraisal.true_value(RULES, sample(0.31, 10.63, 15)), 129.2, delta=0.05)


class Estimate(unittest.TestCase):
    def test_zero_noise_equals_formula(self):
        s = sample(2.0, 11.0, 16)
        with cfg_patch(DEFECT_SD=0.0, MOISTURE_SD=0.0, DEFECT_BIAS=0.0, MOISTURE_BIAS=0.0):
            self.assertAlmostEqual(appraisal.estimate(RULES, s), appraisal.true_value(RULES, s), places=6)

    def test_mid_step_lot_unchanged_by_noise(self):
        s = sample(2.0, 11.0, 16)                      # กลางขั้น GRADE_1 ห่างขอบ 1 และ 3
        with cfg_patch(DEFECT_SD=0.3, MOISTURE_SD=0.1, DEFECT_BIAS=0.0, MOISTURE_BIAS=0.0):
            self.assertAlmostEqual(appraisal.estimate(RULES, s), appraisal.true_value(RULES, s), delta=0.6)

    def test_lot_at_step_edge_picks_a_grade_not_the_average(self):
        # ตัวอย่าง d6.93 ชิดขั้น 7%: มัธยฐานต้องเป็น TV ของเกรดใดเกรดหนึ่ง ไม่ใช่ค่ากลางระหว่างสองเกรด
        s = sample(6.93, 11.0, 15)
        with cfg_patch(DEFECT_SD=0.3, MOISTURE_SD=0.0, DEFECT_BIAS=0.0, MOISTURE_BIAS=0.0):
            est = appraisal.estimate(RULES, s)
        tv_g3 = appraisal.true_value(RULES, sample(6.93, 11.0, 15))
        tv_below = appraisal.true_value(RULES, sample(7.05, 11.0, 15))
        self.assertTrue(min(abs(est - tv_g3) / tv_g3, abs(est - tv_below) / tv_below) < 0.01, est)

    def test_distribution_probabilities_sum_to_one(self):
        with cfg_patch(DEFECT_SD=0.3, MOISTURE_SD=0.1, DEFECT_BIAS=0.0, MOISTURE_BIAS=0.0):
            dist = appraisal.distribution(RULES, sample(3.0, 12.4, 15))
        self.assertAlmostEqual(sum(p for _, p in dist), 1.0, places=6)

    def test_appraisal_items(self):
        lots = [helpers.lot("L-1", defect=0.5), helpers.lot("L-2", defect=6.0)]
        tv = appraisal.estimate_lots(RULES, lots)
        items = appraisal.appraisal_items(RULES, lots, tv)
        self.assertEqual([i["lot_id"] for i in items], ["L-1", "L-2"])
        self.assertEqual([i["grade"] for i in items], ["PREMIUM", "GRADE_3"])
        self.assertEqual(items[0]["est_value_per_kg"], tv["L-1"])


if __name__ == "__main__":
    unittest.main()
