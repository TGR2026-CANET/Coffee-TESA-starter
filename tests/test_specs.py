"""strategy/specs.py: ผ่านสเปกแบบ safe / risky / ไม่ผ่าน"""
import unittest

from helpers import SPEC, cfg_patch, sample

from strategy import specs

MARGINS = dict(DEFECT_MARGIN=0.3, MOISTURE_MARGIN=0.1, SCREEN_MARGIN=0, DEFECT_BIAS=0.0, MOISTURE_BIAS=0.0)


class Check(unittest.TestCase):
    def test_safe_risky_none(self):
        with cfg_patch(**MARGINS):
            self.assertEqual(specs.check(sample(2.0, 11.0, 16), SPEC["SPECIALTY"]), "safe")
            self.assertEqual(specs.check(sample(2.8, 11.0, 16), SPEC["SPECIALTY"]), "risky")   # ห่างขอบ < 0.3
            self.assertIsNone(specs.check(sample(3.1, 11.0, 16), SPEC["SPECIALTY"]))
            self.assertIsNone(specs.check(sample(2.0, 11.0, 14), SPEC["SPECIALTY"]))           # ตะแกรงเล็กไป
            self.assertEqual(specs.check(sample(2.0, 12.45, 16), SPEC["SPECIALTY"]), "risky")  # ความชื้นชิดขอบ

    def test_null_fields_mean_no_limit(self):
        with cfg_patch(**MARGINS):
            self.assertEqual(specs.check(sample(6.0, 11.0, 10), SPEC["COMMERCIAL"]), "safe")
            self.assertEqual(specs.check(sample(1, 1, 1), {"max_defect_pct": None, "max_moisture_pct": None,
                                                           "min_screen": None}), "safe")

    def test_bias_makes_lot_riskier(self):
        with cfg_patch(**{**MARGINS, "DEFECT_BIAS": 0.5}):
            self.assertEqual(specs.check(sample(2.4, 11.0, 16), SPEC["SPECIALTY"]), "risky")

    def test_passes_uses_sample_only(self):
        self.assertTrue(specs.passes(sample(3.0, 12.5, 15), SPEC["SPECIALTY"]))
        self.assertFalse(specs.passes(sample(3.01, 12.5, 15), SPEC["SPECIALTY"]))


class AsStrict(unittest.TestCase):
    def test_same_or_stricter(self):
        failed = SPEC["COMMERCIAL"]
        self.assertTrue(specs.as_strict(SPEC["COMMERCIAL"], failed))
        self.assertTrue(specs.as_strict(SPEC["SPECIALTY"], failed))      # SPECIALTY เข้มกว่าทุกข้อ
        self.assertFalse(specs.as_strict(SPEC["INSTANT"], failed))       # INSTANT หลวมกว่า ยังลองได้


if __name__ == "__main__":
    unittest.main()
