"""strategy/advisor.py: prompt ที่ส่ง LLM กรอบราคาที่ LLM ขยับได้ และการบีบคำตอบ"""
import json
import unittest

from helpers import TMP, cfg_patch, context, lot, memory_with, rfq, state, summary_row

from strategy import advisor, buyers


class Capture:
    def __init__(self, answer):
        self.answer, self.last_error, self.seconds, self.usage = answer, None, 0.2, {"prompt": 1, "answer": 1}
        self.system = self.user = None

    def ask_json(self, system, user, schema, timeout=None):
        self.system, self.user = system, user
        return self.answer


def setup():
    s = state([lot("LOT-T02-1", 600, defect=1.0), lot("LOT-T02-2", 600, defect=5.0)],
              [rfq("S1", "ดอยคราฟต์", "SPECIALTY", 300), rfq("C1", "ไทยคั่วบด", "COMMERCIAL", 600)], number=3)
    tv = {"LOT-T02-1": 130.0, "LOT-T02-2": 110.0}
    mem = memory_with([{"round_id": "R2", "kind": "real",
                        "my_offers": [{"rfq_id": "R2-1", "lot_id": "LOT-T02-2", "kg": 60, "price_per_kg": 115.5,
                                       "status": "OUTBID", "filled_kg": 0}],
                        "market_summary": [summary_row("R2-1", "ไทยคั่วบด", "COMMERCIAL", offers=9, pmax=112)]}], tv)
    return s, tv, mem


class Bounds(unittest.TestCase):
    def test_floor_and_limit(self):
        with cfg_patch(LLM_DOWN=0.05, LLM_UP=0.04, MAX_MARKUP=1.3, MIN_MARKUP=0.9, CEILING_GAP=0.02,
                       TYPE_FLOOR={"SPECIALTY": 1.0, "COMMERCIAL": 1.0, "INSTANT": 0.9}):
            self.assertEqual(advisor.bounds(1.02, buyers.Profile("A", "COMMERCIAL"), False), (1.0, 1.06))
            self.assertEqual(advisor.bounds(1.02, buyers.Profile("A", "COMMERCIAL", above=1.05), False), (1.0, 1.03))
            self.assertEqual(advisor.bounds(1.02, buyers.Profile("A", "COMMERCIAL"), True), (1.0, 1.02))   # รอบสุดท้ายห้ามขึ้น


class Consult(unittest.TestCase):
    def test_prompt_contains_history_and_answer_is_clamped(self):
        s, tv, mem = setup()
        llm = Capture({"markups": [{"b": "B1", "markup": 5.0}, {"b": "B2", "markup": 0.2}, {"b": "B9", "markup": 1}],
                       "reason": "ok"})
        ctx = context(tv, mem, llm)
        book = buyers.build(mem, s["rfqs"])
        rule = {"ดอยคราฟต์": 1.06, "ไทยคั่วบด": 1.02}
        got, reason = advisor.consult(s, ctx, book, rule, 1.0, {"cands": [], "budget_kg": 360, "late": False,
                                                                  "last": False, "adjust": []})
        data = json.loads(llm.user.split("DATA:\n", 1)[1].split("\n\nTASK", 1)[0])
        self.assertEqual([b["b"] for b in data["buyers"]], ["B1", "B2"])
        thai = {b["b"]: b for b in data["buyers"]}["B2"]                        # ไทยคั่วบด
        self.assertTrue(any("OUTBID" in h for h in thai["hist"]))                # ประวัติจาก log ถูกส่ง
        self.assertTrue(thai["mkt"])
        self.assertEqual([l["id"] for l in data["lots"]], ["L1", "L2"])
        for b in data["buyers"]:
            self.assertGreaterEqual(got[{"B1": "ดอยคราฟต์", "B2": "ไทยคั่วบด"}[b["b"]]], b["lo"])
        self.assertEqual(got["ดอยคราฟต์"], {b["b"]: b for b in data["buyers"]}["B1"]["hi"])   # 5.0 → hi
        self.assertEqual(got["ไทยคั่วบด"], thai["lo"])                                         # 0.2 → lo
        self.assertEqual(reason, "ok")
        self.assertTrue(any("bad item" in n for n in ctx.notes))                 # B9 ไม่มีจริง ถูกข้าม
        log = (TMP / "llm_prompts.jsonl").read_text(encoding="utf-8")
        self.assertIn("ดอยคราฟต์", log)                                           # บันทึก prompt ลง LOG_DIR ชั่วคราว

    def test_failure_and_no_time(self):
        s, tv, mem = setup()
        book = buyers.build(mem, s["rfqs"])
        ctx = context(tv, mem, Capture(None))
        self.assertIsNone(advisor.consult(s, ctx, book, {"ไทยคั่วบด": 1.02}, 1.0))
        ctx = context(tv, mem, Capture({"markups": [], "reason": ""}), seconds=5)   # เหลือเวลาไม่พอถาม
        self.assertIsNone(advisor.consult(s, ctx, book, {"ไทยคั่วบด": 1.02}, 1.0))
        self.assertIn("no time for the LLM this round", ctx.notes)


if __name__ == "__main__":
    unittest.main()
