"""strategy/pricing.py: ราคาต่ำสุด ตัวปรับของรอบ การลดซ้อน สู้ราคา high season"""
import random
import unittest

from helpers import COMPETITION_8, RULES, cfg_patch, lot, state

import config as cfg
from strategy import pricing
from strategy.buyers import Profile

FIXED = dict(TYPE_FLOOR={"SPECIALTY": 1.00, "COMMERCIAL": 1.00, "INSTANT": 0.90}, MIN_MARKUP=0.90, MAX_MARKUP=1.30,
             MAX_ROUND_CUT=0.06, LAST_ROUND_SAFE=0.04, LAST_ROUND_CALM=0.0, CEILING_GAP=0.02, SCARCE_UP=0.03,
             LATE_UP=0.02, FESTIVAL_UP=0.02, FESTIVAL_UP_TYPE={"SPECIALTY": 0.03, "COMMERCIAL": 0.01, "INSTANT": 0.0})


def prof(btype="COMMERCIAL", target=1.04, state_="normal", above=None, cap=None):
    return Profile("B", btype, target=target, above=above, state=state_, cap=cap)


class Floors(unittest.TestCase):
    def test_floor_per_type(self):
        with cfg_patch(**FIXED):
            self.assertEqual(pricing.floor("COMMERCIAL"), 1.00)
            self.assertEqual(pricing.floor("INSTANT"), 0.90)
            self.assertEqual(pricing.floor("UNKNOWN"), 0.90)

    def test_clamp_floor_and_limit(self):
        with cfg_patch(**FIXED):
            self.assertEqual(pricing.clamp(0.5, prof("COMMERCIAL")), 1.00)
            self.assertEqual(pricing.clamp(2.0, prof("COMMERCIAL")), 1.30)
            self.assertEqual(pricing.clamp(1.10, prof("COMMERCIAL", above=1.06)), 1.04)   # limit = above − gap
            self.assertEqual(pricing.clamp(1.10, prof("COMMERCIAL", above=1.06), last=True), 1.02)


class Markup(unittest.TestCase):
    def test_stacked_cuts_capped(self):
        with cfg_patch(**FIXED):
            # เกม 68 R8: เป้า 0.95 กดดัน −0.04 รอบสุดท้ายแน่น −0.04 → ลดได้แค่ 0.06 แล้วติดต่ำสุด 1.00
            self.assertEqual(pricing.markup(prof("COMMERCIAL", 0.95, "crowded"), -0.04, True), 1.00)
            self.assertEqual(pricing.markup(prof("SPECIALTY", 1.10, "crowded"), -0.04, True), 1.04)

    def test_last_round_calm_vs_crowded(self):
        with cfg_patch(**FIXED):
            self.assertEqual(pricing.markup(prof("SPECIALTY", 1.10, "normal"), 0.0, True), 1.10)
            self.assertEqual(pricing.markup(prof("SPECIALTY", 1.10, "crowded"), 0.0, True), 1.06)

    def test_scarce_fights_up_to_cap(self):
        with cfg_patch(**FIXED):
            self.assertEqual(pricing.markup(prof("COMMERCIAL", 1.02, "scarce", cap=1.10), -0.04, False), 1.05)
            self.assertEqual(pricing.markup(prof("COMMERCIAL", 1.02, "scarce", cap=1.03), -0.04, False), 1.03)

    def test_late_but_crowded_does_not_fight(self):
        with cfg_patch(**FIXED):
            self.assertEqual(pricing.markup(prof("COMMERCIAL", 1.04, "crowded", cap=1.10), 0.0, False, late=True), 1.04)
            self.assertEqual(pricing.markup(prof("COMMERCIAL", 1.04, "normal", cap=1.10), 0.0, False, late=True), 1.06)

    def test_high_season_bonus_by_type(self):
        with cfg_patch(**FIXED):
            self.assertEqual(pricing.markup(prof("SPECIALTY", 1.06), 0.0, False, festival=True), 1.09)
            self.assertEqual(pricing.markup(prof("COMMERCIAL", 1.02), 0.0, False, festival=True), 1.03)
            self.assertEqual(pricing.markup(prof("INSTANT", 0.92), 0.0, False, festival=True), 0.92)

    def test_round_adjust(self):
        with cfg_patch(PRESSURE_STEPS=[(0.7, 0.02), (0.95, 0.0), (1.15, -0.02), (9e9, -0.04)], SLUMP_CUT=0.02):
            s3 = state([lot("L-1")], [], number=3)
            self.assertEqual(pricing.round_adjust(s3, RULES, COMPETITION_8, 0.5)[0], 0.02)
            self.assertEqual(pricing.round_adjust(s3, RULES, COMPETITION_8, 2.0)[0], -0.04)
            s7 = state([lot("L-1")], [], number=7)
            self.assertAlmostEqual(pricing.round_adjust(s7, RULES, COMPETITION_8, 1.0)[0], -0.04)   # ซบเซา + กดดัน


class TeamPolicy(unittest.TestCase):
    """ค่าใน config.py จริง (ไม่ตรึง): ไม่ว่ากรณีไหน markup ต้องไม่ต่ำกว่าราคาต่ำสุดของทีมตามประเภท"""

    def test_never_below_team_floor(self):
        rng = random.Random(7)
        for _ in range(3000):
            btype = rng.choice(["SPECIALTY", "COMMERCIAL", "INSTANT"])
            p = prof(btype, rng.uniform(0.5, 1.4), rng.choice(["normal", "crowded", "scarce", None]),
                     above=rng.choice([None, rng.uniform(0.8, 1.3)]), cap=rng.choice([None, rng.uniform(0.8, 1.3)]))
            m = pricing.markup(p, rng.uniform(-0.2, 0.1), rng.random() < 0.3, rng.random() < 0.3, rng.random() < 0.3)
            self.assertGreaterEqual(m + 1e-9, pricing.floor(btype), (btype, p, m))
            self.assertLessEqual(m, cfg.MAX_MARKUP + 1e-9)


if __name__ == "__main__":
    unittest.main()
