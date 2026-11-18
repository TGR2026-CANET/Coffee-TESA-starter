"""strategy/plan.py: เลือกคู่ล็อต×RFQ แบ่งกระสอบ ตรวจก่อนส่ง และ decide() ทั้งรอบ"""
import unittest

from helpers import cfg_patch, context, lot, memory_with, rfq, state, summary_row

import config as cfg
import strategy
from strategy import plan, pricing
from strategy.buyers import Profile

ALLOC = dict(MAX_BUYER_SHARE=0.5, MAX_RFQS_PER_LOT=3, DOWNGRADE_WEIGHT=0.6, JITTER=0.0, RISKY_WEIGHT=0.5,
             PACE_EVERY_LOT=False, DEFECT_MARGIN=0.3, MOISTURE_MARGIN=0.1, SCREEN_MARGIN=0,
             DEFECT_BIAS=0.0, MOISTURE_BIAS=0.0)


def book(*specs):
    return {name: Profile(name, btype, p_sell=p, above=above) for name, btype, p, above in specs}


def by_buyer(state_, offers):
    rf = {r["rfq_id"]: r for r in state_["rfqs"]}
    out = {}
    for o in offers:
        out[rf[o["rfq_id"]]["buyer"]] = out.get(rf[o["rfq_id"]]["buyer"], 0) + o["kg"]
    return out


class Candidates(unittest.TestCase):
    def test_spec_and_mismatch_filter(self):
        s = state([lot("L-good", defect=1.0), lot("L-low", defect=10.0)],
                  [rfq("S1", "ดอยคราฟต์", "SPECIALTY"), rfq("C1", "ไทยคั่วบด", "COMMERCIAL"), rfq("I1", "ซองดี", "INSTANT")])
        with cfg_patch(**ALLOC):
            pairs = {(c["lot_id"], c["rfq_id"]) for c in plan.candidates(s, context({"L-good": 130, "L-low": 90}))}
        self.assertIn(("L-good", "S1"), pairs)
        self.assertNotIn(("L-low", "S1"), pairs)
        self.assertNotIn(("L-low", "C1"), pairs)
        self.assertIn(("L-low", "I1"), pairs)

        # L-good เคยโดน SPEC_MISMATCH กับ COMMERCIAL → ไม่ยื่น COMMERCIAL/SPECIALTY อีก แต่ INSTANT (หลวมกว่า) ยังได้
        mem = memory_with([{"round_id": "R1", "kind": "real",
                            "my_offers": [{"rfq_id": "C0", "lot_id": "L-good", "kg": 60, "price_per_kg": 130,
                                           "status": "SPEC_MISMATCH", "filled_kg": 0}],
                            "market_summary": [summary_row("C0", "ไทยคั่วบด", "COMMERCIAL")]}],
                          {"L-good": 130, "L-low": 90},
                          sent={"R1": [{"rfq_id": "C0", "lot_id": "L-good", "spec": rfq("x", "x", "COMMERCIAL")["spec"],
                                        "buyer": "ไทยคั่วบด", "buyer_type": "COMMERCIAL", "markup": 1.0}]})
        with cfg_patch(**ALLOC):
            pairs = {(c["lot_id"], c["rfq_id"]) for c in plan.candidates(s, context({"L-good": 130, "L-low": 90}, mem))}
        self.assertNotIn(("L-good", "C1"), pairs)
        self.assertNotIn(("L-good", "S1"), pairs)
        self.assertIn(("L-good", "I1"), pairs)


class Allocate(unittest.TestCase):
    def run_alloc(self, s, markups, bk, tv, **kw):
        ctx = context(tv)
        cands = plan.candidates(s, ctx)
        return plan.allocate(s, ctx, cands, markups, bk, **kw)

    def test_capacity_bags_and_buyer_share(self):
        s = state([lot("L-1", 1200, defect=4.0)],
                  [rfq("C1", "A", "COMMERCIAL", 1200), rfq("C2", "B", "COMMERCIAL", 1200)], capacity=360)
        with cfg_patch(**ALLOC):
            got = self.run_alloc(s, {"A": 1.04, "B": 1.02}, book(("A", "COMMERCIAL", 0.9, None),
                                                                 ("B", "COMMERCIAL", 0.9, None)), {"L-1": 110})
        self.assertEqual(sum(o["kg"] for o in got), 360)
        self.assertTrue(all(o["kg"] % 60 == 0 for o in got))
        self.assertEqual(by_buyer(s, got), {"A": 180, "B": 180})           # รายเดียวไม่เกินครึ่งโควตา

    def test_good_lot_goes_to_specialty_first(self):
        # เหลือกระสอบเดียว ราคาและโอกาสขายเท่ากัน → ต้องไป SPECIALTY (COMMERCIAL ได้ลำดับ × DOWNGRADE_WEIGHT)
        s = state([lot("L-1", 60, defect=1.0)],
                  [rfq("S1", "ดอยคราฟต์", "SPECIALTY", 600), rfq("C1", "ไทยคั่วบด", "COMMERCIAL", 600)], capacity=360)
        with cfg_patch(**ALLOC):
            got = self.run_alloc(s, {"ดอยคราฟต์": 1.04, "ไทยคั่วบด": 1.04},
                                 book(("ดอยคราฟต์", "SPECIALTY", 0.8, None), ("ไทยคั่วบด", "COMMERCIAL", 0.8, None)),
                                 {"L-1": 130})
        self.assertEqual(by_buyer(s, got), {"ดอยคราฟต์": 60})

    def test_specialty_lot_not_dumped_to_instant(self):
        s = state([lot("L-1", 600, defect=1.0)],
                  [rfq("S1", "ดอยคราฟต์", "SPECIALTY", 60), rfq("I1", "ซองดี", "INSTANT", 600)], capacity=360)
        bk = book(("ดอยคราฟต์", "SPECIALTY", 0.8, None), ("ซองดี", "INSTANT", 0.8, None))
        with cfg_patch(**ALLOC):
            blocked = self.run_alloc(s, {"ดอยคราฟต์": 1.06, "ซองดี": 0.92}, bk, {"L-1": 130}, deep_ok=False)
            allowed = self.run_alloc(s, {"ดอยคราฟต์": 1.06, "ซองดี": 0.92}, bk, {"L-1": 130}, deep_ok=True)
        self.assertNotIn("ซองดี", by_buyer(s, blocked))
        self.assertIn("ซองดี", by_buyer(s, allowed))

    def test_skip_buyer_when_floor_above_known_ceiling(self):
        s = state([lot("L-1", 600, defect=4.0)],
                  [rfq("C1", "A", "COMMERCIAL", 600), rfq("C2", "B", "COMMERCIAL", 600)], capacity=360)
        bk = book(("A", "COMMERCIAL", 0.9, 1.01), ("B", "COMMERCIAL", 0.9, None))   # A ปฏิเสธที่ 1.01 → limit 0.99
        with cfg_patch(**ALLOC, CEILING_GAP=0.02):
            got = self.run_alloc(s, {"A": 1.00, "B": 1.02}, bk, {"L-1": 110})
        self.assertNotIn("A", by_buyer(s, got))

    def test_budget_limits_offer(self):
        s = state([lot("L-1", 1200, defect=4.0)], [rfq("C1", "A", "COMMERCIAL", 1200)], capacity=360)
        with cfg_patch(**ALLOC):
            got = self.run_alloc(s, {"A": 1.02}, book(("A", "COMMERCIAL", 0.9, None)), {"L-1": 110}, budget_kg=180)
        self.assertEqual(sum(o["kg"] for o in got), 180)


class Guard(unittest.TestCase):
    def test_floor_spec_duplicates_and_caps(self):
        s = state([lot("L-1", 120, defect=4.0), lot("L-low", 600, defect=10.0)],
                  [rfq("C1", "A", "COMMERCIAL", 120)], capacity=360)
        ctx = context({"L-1": 110.0, "L-low": 90.0})
        with cfg_patch(TYPE_FLOOR={"SPECIALTY": 1.0, "COMMERCIAL": 1.0, "INSTANT": 0.9}, MIN_MARKUP=0.9, MAX_MARKUP=1.3):
            out = plan.guard(s, ctx, [
                {"lot_id": "L-1", "rfq_id": "C1", "kg": 600, "markup": 0.5},     # ต่ำกว่าต่ำสุด + เกินที่ RFQ ต้องการ
                {"lot_id": "L-1", "rfq_id": "C1", "kg": 60, "markup": 1.1},      # คู่ซ้ำ
                {"lot_id": "L-low", "rfq_id": "C1", "kg": 60, "markup": 1.1},    # ไม่ผ่านสเปก
                {"lot_id": "L-x", "rfq_id": "C1", "kg": 60, "markup": 1.1},      # ไม่มีล็อตนี้
            ])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kg"], 120)
        self.assertAlmostEqual(out[0]["price_per_kg"], 110.0)                    # 1.00 × TV
        self.assertEqual(len([n for n in ctx.notes if "guard dropped" in n]), 3)


class FakeLLM:
    def __init__(self, answer):
        self.answer, self.last_error, self.seconds, self.usage, self.calls = answer, None, 0.1, None, 0

    def ask_json(self, system, user, schema, timeout=None):
        self.calls += 1
        if self.answer is None:
            self.last_error = "fake failure"
        return self.answer


def round_state(number=3):
    return state([lot("L-1", 600, defect=1.0), lot("L-2", 600, defect=5.5), lot("L-3", 600, defect=10.0)],
                 [rfq("S1", "ดอยคราฟต์", "SPECIALTY", 300), rfq("C1", "ไทยคั่วบด", "COMMERCIAL", 600),
                  rfq("C2", "สยามคอมเมิร์ซ", "COMMERCIAL", 180), rfq("I1", "ซองดี", "INSTANT", 600)],
                 number=number, capacity=420, round_capacity=420)


TV = {"L-1": 130.0, "L-2": 112.0, "L-3": 90.0}


class Decide(unittest.TestCase):
    def check_offers(self, s, offers):
        rf = {r["rfq_id"]: r for r in s["rfqs"]}
        self.assertTrue(offers)
        self.assertLessEqual(sum(o["kg"] for o in offers), s["capacity_left_kg"])
        for o in offers:
            self.assertEqual(o["kg"] % 60, 0)
            m = o["price_per_kg"] / TV[o["lot_id"]]
            self.assertGreaterEqual(m + 0.005, pricing.floor(rf[o["rfq_id"]]["buyer_type"]), o)   # ราคาต่ำสุดของทีม
            self.assertLessEqual(m, cfg.MAX_MARKUP + 0.005)

    def test_rules_only(self):
        s = round_state()
        offers, source, info = strategy.decide(s, context(TV))
        self.assertEqual(source, "rules")
        self.assertIn("pressure", info)
        self.check_offers(s, offers)

    def test_llm_answer_is_clamped(self):
        s = round_state()
        llm = FakeLLM({"markups": [{"b": "B1", "markup": 9.9}, {"b": "B2", "markup": 0.1},
                                   {"b": "B3", "markup": 0.1}, {"b": "B4", "markup": 0.1}], "reason": "test"})
        offers, source, _ = strategy.decide(s, context(TV, llm=llm))
        self.assertEqual(source, "llm")
        self.assertEqual(llm.calls, 1)
        self.check_offers(s, offers)

    def test_llm_failure_falls_back_to_rules(self):
        s = round_state()
        ctx = context(TV, llm=FakeLLM(None))
        offers, source, _ = strategy.decide(s, ctx)
        self.assertEqual(source, "rules")
        self.assertTrue(any("llm failed" in n for n in ctx.notes))
        self.check_offers(s, offers)

    def test_practice_probe(self):
        s = round_state(number=1)
        s["round"]["kind"] = "practice"
        with cfg_patch(PRACTICE_MODE="probe", PROBE_MAX_OFFERS=6, PROBE_BAGS_PER_LOT=2):
            offers, source, _ = strategy.decide(s, context(TV))
        self.assertEqual(source, "probe")
        self.assertTrue(all(o["kg"] == 60 for o in offers))
        self.assertLessEqual(len(offers), 6)

    def test_no_candidates(self):
        s = state([lot("L-1", 30)], [rfq("C1", "A", "COMMERCIAL")])     # น้อยกว่า 1 กระสอบ
        self.assertEqual(strategy.decide(s, context({"L-1": 100.0}))[:2], ([], "none"))

    def test_fallback(self):
        s = round_state()
        offers = strategy.fallback(s, context(TV))
        self.check_offers(s, offers)


if __name__ == "__main__":
    unittest.main()
