"""memory.py: จำข้อเสนอและผลรอบ รีสตาร์ตแล้วไม่ลืม (รูปแบบไฟล์ต้องคงเดิม)"""
import json
import unittest

from helpers import TMP, memory_with, rfq, state, lot, summary_row

from memory import Memory


class MemoryFile(unittest.TestCase):
    def test_format_and_reload(self):
        path = TMP / "memory-format.json"
        m = Memory(path, {"L-1": 100.0})
        s = state([lot("L-1")], [rfq("R1-1", "ไทยคั่วบด", "COMMERCIAL")], number=1)
        m.record_sent("R1", [{"rfq_id": "R1-1", "lot_id": "L-1", "kg": 60, "price_per_kg": 104.0}], s, {"L-1": 100.0})
        result = {"round_id": "R1", "kind": "real",
                  "my_offers": [{"rfq_id": "R1-1", "lot_id": "L-1", "kg": 60, "price_per_kg": 104.0,
                                 "status": "ACCEPTED", "filled_kg": 60}],
                  "market_summary": [summary_row("R1-1", "ไทยคั่วบด", "COMMERCIAL", pmin=101, pmax=110)]}
        self.assertTrue(m.record_results(result))
        self.assertFalse(m.record_results(result))                     # รอบเดิมไม่บันทึกซ้ำ
        self.assertFalse(m.record_results(None))

        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(set(data), {"sent", "rounds"})                # โครงสร้างไฟล์ต้องคงเดิม
        sent = data["sent"]["R1"][0]
        self.assertEqual((sent["buyer"], sent["buyer_type"], sent["markup"], sent["kind"]),
                         ("ไทยคั่วบด", "COMMERCIAL", 1.04, "real"))

        again = Memory(path, {"L-1": 100.0})                           # รีสตาร์ต
        self.assertEqual(len(again.rounds), 1)
        self.assertEqual(again.sent["R1"][0]["rfq_id"], "R1-1")


class Outcomes(unittest.TestCase):
    def test_fields_from_market_results(self):
        m = memory_with([{"round_id": "R1", "kind": "real",
                          "my_offers": [{"rfq_id": "R1-1", "lot_id": "L-1", "kg": 120, "price_per_kg": 131.45,
                                         "status": "OUTBID", "filled_kg": 0}],
                          "market_summary": [summary_row("R1-1", "ดอยคราฟต์", "SPECIALTY", pmin=120, pmax=127.28)]}],
                        {"L-1": 125.19})
        o = m.outcomes()[0]
        self.assertEqual((o["buyer"], o["buyer_type"], o["status"], o["kind"], o["n"]),
                         ("ดอยคราฟต์", "SPECIALTY", "OUTBID", "real", 0))
        self.assertAlmostEqual(o["markup"], round(131.45 / 125.19, 3))
        self.assertEqual((o["price"], o["rfq_min"], o["rfq_max"]), (131.45, 120, 127.28))

    def test_unknown_tv_without_sent_is_skipped(self):
        m = memory_with([{"round_id": "R1", "kind": "real",
                          "my_offers": [{"rfq_id": "R1-1", "lot_id": "L-9", "kg": 60, "price_per_kg": 100,
                                         "status": "ACCEPTED", "filled_kg": 60}],
                          "market_summary": [summary_row("R1-1", "A", "COMMERCIAL")]}], {})
        self.assertEqual(m.outcomes(), [])

    def test_market_rows_and_summary(self):
        m = memory_with([{"round_id": "R1", "kind": "real", "my_offers": [],
                          "market_summary": [summary_row("R1-1", "A", "COMMERCIAL", wanted=600, bought=300)]}], {})
        self.assertEqual(m.market_rows()[0]["n"], 0)
        self.assertIn("COMMERCIAL bought 50% of wanted", m.summary())


if __name__ == "__main__":
    unittest.main()
