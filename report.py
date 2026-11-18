"""สรุปผลทุกครั้งที่จบรอบ: เรายื่นให้ใคร เท่าไร ขายได้ไหม และผู้ซื้อทุกรายซื้อที่ราคาเท่าไร

ข้อมูลมาจาก last_results ที่ตลาดประกาศ (my_offers + market_summary) ไม่ใช่จากสิ่งที่เราคิดว่าส่ง
พิมพ์บนจอเป็นตาราง · ต่อท้าย logs/round_offers.csv และ logs/round_buyers.csv (เปิดด้วย Excel ได้)
และคืน dict ไว้บันทึกเป็น event "round_summary" ใน logs/decisions.jsonl
"""
import csv
import unicodedata
from collections import defaultdict

SOLD = ("ACCEPTED", "PARTIAL")
THAI = {"ACCEPTED": "ขายได้", "PARTIAL": "ขายได้บางส่วน", "OUTBID": "แพ้ราคา", "ABOVE_LIMIT": "เกินเพดาน",
        "SPEC_MISMATCH": "สเปกไม่ผ่าน", "CANCELLED": "ยกเลิก"}


def _f(x, nd=2):
    return "-" if x is None else f"{x:,.{nd}f}"


def summarize(results: dict, tv: dict) -> dict:
    rfq = {m["rfq_id"]: m for m in results.get("market_summary", [])}
    mine = []
    for o in results.get("my_offers", []):
        m = rfq.get(o["rfq_id"], {})
        price, filled = o.get("price_per_kg"), o.get("filled_kg") or 0
        mine.append({"rfq_id": o["rfq_id"], "buyer": m.get("buyer", "?"), "buyer_type": m.get("buyer_type", "?"),
                     "lot_id": o["lot_id"], "kg": o["kg"], "price_per_kg": price,
                     "markup": round(price / tv[o["lot_id"]], 3) if price and tv.get(o["lot_id"]) else None,
                     "status": o["status"], "filled_kg": filled, "revenue": round((price or 0) * filled, 2),
                     # ราคาที่ผู้ซื้อรายนี้ซื้อจริงใน RFQ นี้ (ของทุกทีม) ไว้เทียบกับของเรา
                     "rfq_price_min": m.get("price_min"), "rfq_price_max": m.get("price_max")})
    buyers = defaultdict(lambda: {"buyer_type": "?", "rfqs": 0, "wanted_kg": 0, "bought_kg": 0, "offers": 0,
                                  "price_min": None, "price_max": None, "_sum": 0.0})
    for m in results.get("market_summary", []):
        b = buyers[m["buyer"]]
        b["buyer_type"] = m["buyer_type"]
        b["rfqs"] += 1
        b["wanted_kg"] += m["wanted_kg"]
        b["bought_kg"] += m["bought_kg"]
        b["offers"] += m["offers"]
        if m.get("price_min") is not None:
            b["price_min"] = m["price_min"] if b["price_min"] is None else min(b["price_min"], m["price_min"])
            b["price_max"] = m["price_max"] if b["price_max"] is None else max(b["price_max"], m["price_max"])
            b["_sum"] += (m.get("price_avg") or 0) * m["bought_kg"]
    by_buyer = {}
    for name, b in buyers.items():
        avg = round(b.pop("_sum") / b["bought_kg"], 2) if b["bought_kg"] else None
        by_buyer[name] = {**b, "price_avg": avg}
    sold_kg = sum(x["filled_kg"] for x in mine)
    return {"round": results.get("round_id"), "kind": results.get("kind"), "my_offers": mine,
            "offered_kg": sum(x["kg"] for x in mine), "sold_kg": sold_kg,
            "revenue": round(sum(x["revenue"] for x in mine), 2), "buyers": by_buyer}


def _width(text: str) -> int:
    """ความกว้างบนจอ: สระบน/ล่างและวรรณยุกต์ไทยไม่กินที่ · อักษรจีน/ญี่ปุ่นกิน 2"""
    w = 0
    for ch in str(text):
        if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def _pad(text, width: int, right: bool) -> str:
    text = str(text)
    gap = " " * max(0, width - _width(text))
    return gap + text if right else text + gap


def table(headers: list, rows: list, right: set = frozenset()) -> str:
    """ตารางมีเส้นขอบ · right = ลำดับคอลัมน์ที่ชิดขวา (ตัวเลข)"""
    widths = [max([_width(h)] + [_width(r[i]) for r in rows]) for i, h in enumerate(headers)]

    def line(cells):
        return "│ " + " │ ".join(_pad(c, w, i in right) for i, (c, w) in enumerate(zip(cells, widths))) + " │"

    def rule(left, mid, end):
        return left + mid.join("─" * (w + 2) for w in widths) + end

    out = [rule("┌", "┬", "┐"), line(headers), rule("├", "┼", "┤")]
    out += [line(r) for r in rows]
    out.append(rule("└", "┴", "┘"))
    return "\n".join(out)


OFFER_HEAD = ["ผู้ซื้อ", "ประเภท", "RFQ", "ล็อต", "เสนอ กก.", "บาท/กก.", "markup", "ผล", "ขายได้ กก.", "รายได้",
              "RFQ นี้ซื้อที่ (บาท/กก.)"]
BUYER_HEAD = ["ผู้ซื้อ", "ประเภท", "RFQ", "ต้องการ กก.", "ซื้อได้ กก.", "ซื้อได้ %", "ข้อเสนอ", "ต่ำสุด", "เฉลี่ย", "สูงสุด"]


def _n(x, nd, raw):
    """ตัวเลขบนจอ = มีจุลภาค · ใน CSV = ตัวเลขล้วน (Excel บวกได้)"""
    return ("" if x is None else x) if raw else _f(x, nd)


def _status(x) -> str:
    """ผลเป็นภาษาไทย · แพ้ราคาแต่แพงกว่าราคาสูงสุดที่ RFQ นี้ซื้อ = บอกว่าแพงกว่ากี่ %"""
    t = THAI.get(x["status"], x["status"])
    p, top = x.get("price_per_kg"), x.get("rfq_price_max")
    if x["status"] == "OUTBID" and p and top and p > top:
        t += f" (แพงกว่าคนชนะ {p / top - 1:.1%})"
    return t


def offer_rows(s: dict, raw: bool = False) -> list:
    return [[x["buyer"], x["buyer_type"], x["rfq_id"], x["lot_id"], x["kg"], _n(x["price_per_kg"], 2, raw),
             _n(x["markup"], 3, raw), _status(x), x["filled_kg"], _n(x["revenue"], 2, raw),
             f"{_f(x['rfq_price_min'])} – {_f(x['rfq_price_max'])}".replace(",", "")] for x in s["my_offers"]]


def buyer_rows(s: dict, raw: bool = False) -> list:
    items = sorted(s["buyers"].items(), key=lambda kv: (kv[1]["buyer_type"], kv[0]))
    pct = (lambda b: round(b["bought_kg"] / b["wanted_kg"], 3)) if raw else (lambda b: f"{b['bought_kg'] * 100 // b['wanted_kg']}%")
    return [[name, b["buyer_type"], b["rfqs"], b["wanted_kg"], b["bought_kg"], pct(b) if b["wanted_kg"] else "",
             b["offers"], _n(b["price_min"], 2, raw), _n(b["price_avg"], 2, raw), _n(b["price_max"], 2, raw)]
            for name, b in items]


def show(s: dict) -> None:
    rate = f"{s['sold_kg'] * 100 // s['offered_kg']}%" if s["offered_kg"] else "-"
    print(f"\n══ สรุปผลรอบ {s['round']} ({s['kind']}) · ยื่น {s['offered_kg']} กก. · ขายได้ {s['sold_kg']} กก. ({rate})"
          f" · รายได้ {_f(s['revenue'])} บาท ══")
    print("ข้อเสนอของเรา")
    if s["my_offers"]:
        rows = offer_rows(s)
        rows.append(["รวม", "", "", "", s["offered_kg"], "", "", "", s["sold_kg"], _f(s["revenue"]), ""])
        print(table(OFFER_HEAD, rows, right={4, 5, 6, 8, 9}))
    else:
        print("  (รอบนี้เราไม่ได้ยื่น)")
    print("ผู้ซื้อทุกราย (ทุกทีมรวมกัน · ราคาที่ซื้อจริง บาท/กก.)")
    print(table(BUYER_HEAD, buyer_rows(s), right={2, 3, 4, 5, 6, 7, 8, 9}), flush=True)


SOURCE = {"llm": "LLM ปรับในกรอบ", "rules": "กฎ", "probe": "รอบซ้อม (หยั่งเพดาน)", "fallback": "สำรอง (strategy ล้ม)",
          "none": "-", "skip": "ข้าม"}
MARKET = {"scarce": "ของขาด ↑", "crowded": "แย่งกันหนัก", "normal": "ปกติ"}
SENT_HEAD = ["ผู้ซื้อ", "ประเภท", "ตลาดรอบก่อน", "ล็อต", "กก.", "บาท/กก.", "markup"]


def show_sent(state: dict, offers: list, tv: dict, source: str, info: dict) -> None:
    """ตอนส่ง: เรายื่นให้ใคร ล็อตไหน กี่ กก. ราคาเท่าไร (เรียงตามผู้ซื้อ) + บรรทัดสรุปเหตุผลของรอบ"""
    rnd = state["round"]
    rfq = {r["rfq_id"]: r for r in state["rfqs"]}
    market = info.get("market", {})
    cap = state.get("capacity_left_kg")
    if info.get("budget_kg") is not None and cap is not None and info["budget_kg"] < cap:
        cap = f"{info['budget_kg']} (โควตา {cap} · เก็บไว้ขายรอบท้าย)"
    kg = sum(o["kg"] for o in offers)
    why = ", ".join(info.get("adjust", [])) or "-"
    print(f"\n── ยื่นรอบ {rnd.get('round_id')} ({rnd.get('calendar') or rnd.get('kind')}) · {len(offers)} ข้อเสนอ "
          f"{kg}{'/' + str(cap) if cap is not None else ''} กก. · ราคาจาก{SOURCE.get(source, source)} "
          f"· ความกดดัน {info.get('pressure', '-')} · ปรับ {why} ──")
    if not offers:
        print("  (รอบนี้ไม่ยื่น)", flush=True)
        return
    rows, unknown = [], {"buyer": "?", "buyer_type": "?"}
    for o in sorted(offers, key=lambda o: (rfq.get(o.get("rfq_id"), unknown)["buyer_type"],
                                           rfq.get(o.get("rfq_id"), unknown)["buyer"], o.get("lot_id", ""))):
        r = rfq.get(o.get("rfq_id"), unknown)
        m = o["price_per_kg"] / tv[o["lot_id"]] if tv.get(o["lot_id"]) else None
        rows.append([r["buyer"], r["buyer_type"], MARKET.get(market.get(r["buyer"]), "-"), o["lot_id"],
                     o["kg"], _f(o["price_per_kg"]), _f(m, 3)])
    rows.append(["รวม", "", "", "", kg, "", ""])
    print(table(SENT_HEAD, rows, right={4, 5, 6}), flush=True)


def save_csv(s: dict, folder, game: str = "") -> None:
    """ต่อท้ายตารางทั้งสองลง CSV (utf-8-sig = เปิดใน Excel แล้วภาษาไทยไม่เพี้ยน) สะสมทุกรอบทุกเกม
    logs/round_offers.csv = ข้อเสนอของเรา · logs/round_buyers.csv = ผู้ซื้อทุกราย"""
    folder.mkdir(parents=True, exist_ok=True)
    for name, head, rows in (("round_offers.csv", OFFER_HEAD, offer_rows(s, raw=True)),
                             ("round_buyers.csv", BUYER_HEAD, buyer_rows(s, raw=True))):
        path = folder / name
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["เกม", "รอบ", "ชนิดรอบ"] + head)
            for r in rows:
                w.writerow([game, s["round"], s["kind"]] + r)
