"""สิ่งที่ agent จำข้ามรอบ บันทึกเป็น JSON (logs/memory-<ตลาด>-<เกม>.json) รีสตาร์ตกลางเกมแล้วไม่ลืม

จำแค่ข้อเท็จจริง 2 อย่าง: ข้อเสนอที่เราส่ง (พร้อมผู้ซื้อ ประเภท markup) และผลรอบที่ตลาดประกาศ
การตีความ (เพดานลับของผู้ซื้อ โอกาสขาย) คิดใหม่จากสองอย่างนี้ทุกรอบใน strategy/buyers.py
"""
import json
from collections import defaultdict
from pathlib import Path


class Memory:
    def __init__(self, path, tv: dict = None):
        self.path = Path(path)
        self.tv = tv or {}                              # lot_id -> TV ที่เราประเมิน (ใช้แปลงราคาในผลเป็น markup)
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.sent: dict = data.get("sent", {})         # round_id -> ข้อเสนอที่ส่ง (+ buyer, buyer_type, markup, kind)
        self.rounds: list = data.get("rounds", [])     # ผลรอบที่จับคู่แล้ว เก่าก่อน

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"sent": self.sent, "rounds": self.rounds}, ensure_ascii=False, indent=1),
                             encoding="utf-8")

    def record_sent(self, round_id: str, offers: list[dict], state: dict, tv: dict) -> None:
        rfqs = {r["rfq_id"]: r for r in state["rfqs"]}
        kind = (state.get("round") or {}).get("kind")
        self.sent[round_id] = [{**o, "buyer": rfqs[o["rfq_id"]]["buyer"], "buyer_type": rfqs[o["rfq_id"]]["buyer_type"],
                                "spec": rfqs[o["rfq_id"]]["spec"],
                                "markup": round(o["price_per_kg"] / tv[o["lot_id"]], 3), "kind": kind}
                               for o in offers]
        self.save()

    def record_results(self, results: dict | None) -> bool:
        """คืน True ถ้าเป็นผลรอบใหม่"""
        if not results or not results.get("round_id") or any(r["round_id"] == results["round_id"] for r in self.rounds):
            return False
        self.rounds.append(results)
        self.save()
        return True

    def outcomes(self, tv: dict = None):
        """ทุกข้อเสนอของเราที่รู้ผลแล้ว: [{round_id, n (ลำดับรอบ), buyer, buyer_type, lot_id, markup, kg, filled_kg, status}]
        ใช้ราคาและผู้ซื้อจากผลที่ตลาดประกาศ (my_offers + market_summary) เป็นหลัก ไม่ใช่จากสิ่งที่เราคิดว่าส่งไป
        → รีสตาร์ต ไฟล์ memory หาย หรือรัน DRY_RUN ข้างๆ agent ตัวจริง ก็ยังเรียนจากราคาที่ตลาดเห็นจริง"""
        tv = tv or self.tv
        out = []
        for n, rnd in enumerate(self.rounds):
            sent = {(o["rfq_id"], o["lot_id"]): o for o in self.sent.get(rnd["round_id"], [])}
            rfq = {m["rfq_id"]: m for m in rnd.get("market_summary", [])}
            for o in rnd.get("my_offers", []):
                s = sent.get((o["rfq_id"], o["lot_id"]), {})
                m = rfq.get(o["rfq_id"], {})
                buyer, btype = m.get("buyer", s.get("buyer")), m.get("buyer_type", s.get("buyer_type"))
                price = o.get("price_per_kg")
                if tv and o["lot_id"] in tv and price:
                    markup = round(price / tv[o["lot_id"]], 3)
                elif s.get("markup") is not None and (not price or price == s.get("price_per_kg")):
                    markup = s["markup"]
                else:
                    continue                      # ไม่รู้ TV ของล็อตนี้ แปลงเป็น markup ไม่ได้
                if buyer is None:
                    continue
                out.append({"round_id": rnd["round_id"], "n": n, "kind": rnd.get("kind"), "buyer": buyer, "buyer_type": btype,
                            "lot_id": o["lot_id"], "markup": markup, "kg": o["kg"], "spec": s.get("spec"),
                            "filled_kg": o.get("filled_kg") or 0, "status": o["status"],
                            # ราคา (บาท) ของเรา และช่วงราคาที่ RFQ นี้ซื้อจริง (ทุกทีม) · คิดจากผลที่ตลาดประกาศ ไม่ได้เขียนลงไฟล์
                            "price": price, "rfq_min": m.get("price_min"), "rfq_max": m.get("price_max")})
        return out

    def market_rows(self):
        """ผลตลาดทุกรอบ (ไม่บอกทีม): [{n, rfq_id, buyer, buyer_type, wanted_kg, bought_kg, offers, price_*}]"""
        return [{**row, "n": n} for n, rnd in enumerate(self.rounds) for row in rnd.get("market_summary", [])]

    def summary(self, last: int = 3) -> str:
        """สรุปสั้นสำหรับ LLM: ข้อเสนอของเราและตลาดต่อประเภท ใน `last` รอบล่าสุด"""
        lines = []
        for rnd in self.rounds[-last:]:
            sent = {(o["rfq_id"], o["lot_id"]): o for o in self.sent.get(rnd["round_id"], [])}
            mine = []
            for o in rnd["my_offers"]:
                s = sent.get((o["rfq_id"], o["lot_id"]), {})
                mine.append(f"{s.get('buyer', '?')} x{s.get('markup', '?')} {o['status']}")
            by_type = defaultdict(lambda: [0, 0])
            for row in rnd["market_summary"]:
                by_type[row["buyer_type"]][0] += row["wanted_kg"]
                by_type[row["buyer_type"]][1] += row["bought_kg"]
            market = ", ".join(f"{t} bought {b * 100 // max(w, 1)}% of wanted" for t, (w, b) in by_type.items())
            lines.append(f"{rnd['round_id']}: mine [{'; '.join(mine) or 'none'}]; market: {market}")
        return "\n".join(lines) or "none yet"
