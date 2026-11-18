"""เรียนรู้ผู้ซื้อแต่ละรายจากผลข้อเสนอของเรา + ผลตลาดแบบไม่บอกทีม (หลัก Ratio Ladder เดิม: ตีความจากพฤติกรรม)

แหล่งข้อมูลมีแค่ memory (ข้อเสนอที่เราส่ง + last_results ของทุกรอบ) คิดใหม่ทุกรอบ ไม่มี state ซ่อน

สถานะข้อเสนอบอกอะไร (ตลาดเรียง ราคา ÷ TV จริง จากต่ำไปสูง หลังตัดข้อเสนอที่ไม่ผ่านสเปกและเกินเพดาน):
  ACCEPTED x     ผ่านเพดาน และชนะ          → รอบหน้าลอง x + STEP_UP
  PARTIAL x      ผ่านเพดาน ได้บางส่วน      → ใกล้จุดพอดี คง x
  OUTBID x       ผ่านเพดาน แต่คนอื่นคุ้มกว่า → x − STEP_DOWN (เพดานลับ > x แน่)
  ABOVE_LIMIT x  เกินเพดานลับของผู้ซื้อรายนี้ → เพดาน < x · ไม่ยื่น ≥ x − CEILING_GAP อีก
  SPEC_MISMATCH  เรื่องของล็อต ไม่ใช่ราคา (strategy/plan.py จำว่าล็อตนั้นไม่ผ่านสเปกแบบนี้)

ผู้ซื้อแยกตามชื่อที่ตลาดให้ (`buyer`) เพราะเพดานลับเป็นของรายนั้น · รายที่ยังไม่มีหลักฐาน
ใช้ค่าก่อนตามประเภท (config.PRIOR) ผสมหลักฐานของรายอื่นในประเภทเดียวกัน (TYPE_WEIGHT)
"""
from dataclasses import dataclass, field

import config as cfg

PASSED = ("ACCEPTED", "PARTIAL", "OUTBID")   # ผ่านเพดานลับแล้ว
PRICED = PASSED + ("ABOVE_LIMIT",)            # สถานะที่บอกเรื่องราคา


@dataclass
class Profile:
    buyer: str
    btype: str
    passed: float = None        # markup สูงสุดที่ผ่านเพดาน (ACCEPTED/PARTIAL/OUTBID)
    above: float = None         # markup ต่ำสุดที่โดน ABOVE_LIMIT
    last: list = field(default_factory=list)   # ผลของเราในรอบล่าสุดที่ยื่นรายนี้ [(markup, status)]
    n_own: int = 0
    p_sell: float = 0.5         # สัดส่วน กก. ที่ขายได้ (ถ่วงหลักฐานใหม่มากกว่า)
    fill: float = None          # ตลาด: ซื้อได้ ÷ ต้องการ (3 รอบล่าสุด)
    offers: float = None        # ตลาด: ข้อเสนอเฉลี่ยต่อ RFQ
    prices: tuple = None        # ตลาด: ราคาที่ชนะ (ต่ำสุด, สูงสุด) บาท/กก. 3 รอบล่าสุด
    target: float = 1.0         # markup ที่ควรยื่นก่อนปรับตามรอบ
    state: str = None           # ตลาดรอบล่าสุด: scarce (ของขาด) / crowded (แย่งกันหนัก) / normal
    fill_last: float = None     # ซื้อได้ ÷ ต้องการ ในรอบล่าสุด
    offers_last: float = None   # ข้อเสนอต่อ RFQ ในรอบล่าสุด
    cap: float = None           # ขึ้นได้ไม่เกินนี้ตอนสู้ราคา: เพดานที่รู้ หรือ max(เพดานเดาตามประเภท − GAP, ที่เคยผ่าน)
    why: list = field(default_factory=list)

    @property
    def limit(self):
        """markup สูงสุดที่ยอมยื่นให้รายนี้ (ต่ำกว่าราคาที่เคยโดน ABOVE_LIMIT)"""
        return None if self.above is None else round(self.above - cfg.CEILING_GAP, 3)

    def brief(self):
        h = ",".join(f"x{m:.2f} {s[:5]}" for m, s in self.last) or "-"
        mk = "-" if self.fill is None else f"fill {self.fill:.0%} offers {self.offers:.1f}"
        if self.state:
            mk += f" | last {self.state} fill {self.fill_last:.0%} offers {self.offers_last:.1f} cap x{self.cap:.2f}"
        pr = "-" if not self.prices else f"{self.prices[0]:.0f}-{self.prices[1]:.0f}"
        return (f"{self.buyer} [{self.btype}] target x{self.target:.2f} limit {self.limit or '-'} "
                f"passed {self.passed or '-'} p {self.p_sell:.2f} | last {h} | market {mk} won {pr} | {' '.join(self.why)}")


def _prior(btype):
    return cfg.PRIOR.get(btype, cfg.PRIOR["?"])


def _above_streak(rows) -> int:
    """จำนวนรอบล่าสุดติดกัน (ในรอบที่เรายื่นรายนี้) ที่มี ABOVE_LIMIT"""
    k = 0
    for n in sorted({r["n"] for r in rows}, reverse=True):
        if not any(r["n"] == n and r["status"] == "ABOVE_LIMIT" for r in rows):
            break
        k += 1
    return k


def _next_from(rows):
    """markup ที่ควรลองต่อจากผลรอบล่าสุด (หลายล็อตในรอบเดียวกัน = เอาค่าที่ระวังที่สุด)"""
    if not rows:
        return None
    last_n = max(r["n"] for r in rows)
    streak = max(1, _above_streak(rows))
    nxt = []
    for r in rows:
        if r["n"] != last_n:
            continue
        x = r["markup"]
        up = cfg.PRACTICE_STEP_UP if r.get("kind") == "practice" else cfg.STEP_UP
        nxt.append({"ACCEPTED": x + up, "PARTIAL": x + cfg.STEP_PARTIAL, "OUTBID": _after_outbid(r),
                    "ABOVE_LIMIT": x - cfg.ABOVE_STEP * streak}[r["status"]])
    return min(nxt) if nxt else None


def _after_outbid(r) -> float:
    """แพ้ราคาที่ x: ปกติลด STEP_DOWN · ถ้าราคาเรา (บาท) สูงกว่าราคาสูงสุดที่ RFQ นี้ซื้อจริง ลดให้เข้าช่วงที่ผู้ซื้อซื้อ
    = x × ราคาสูงสุดที่ซื้อ ÷ ราคาเรา − OUTBID_UNDERCUT แต่ลดรอบเดียวไม่เกิน OUTBID_MAX_DROP
    (ราคาของทีมอื่นเป็นคนละล็อต จึงใช้เป็นแนวทาง ไม่ตามทั้งหมด · ต่ำสุดตามประเภทบังคับใน pricing.clamp)"""
    x, down = r["markup"], r["markup"] - cfg.STEP_DOWN
    price, top = r.get("price"), r.get("rfq_max")
    if not cfg.OUTBID_FIT or not price or not top or price <= top:
        return down
    fit = x * top / price - cfg.OUTBID_UNDERCUT
    return max(x - cfg.OUTBID_MAX_DROP, min(down, fit))


def _market_state(rows, now):
    """(state, fill, offers ต่อ RFQ) ของรอบล่าสุดใน rows ที่ไม่เก่ากว่า 2 รอบ · ไม่มีข้อมูล = (None, None, None)
      scarce  = ซื้อได้ < SCARCE_FILL และข้อเสนอ ≤ SCARCE_OFFERS ต่อ RFQ → ของขาดจริง ข้อเสนอที่ผ่านเพดานได้หมด
                (ซื้อไม่ครบแต่ข้อเสนอเยอะ = ข้อเสนอส่วนใหญ่เกินเพดาน ไม่ใช่ของขาด)
      crowded = ข้อเสนอ ≥ CROWDED_OFFERS ต่อ RFQ และซื้อได้เกือบครบ → ทีมอื่นเทขาย สู้ราคาไม่ได้"""
    rows = [r for r in rows if r["n"] >= now - 2]
    if not rows:
        return None, None, None
    last = max(r["n"] for r in rows)
    rows = [r for r in rows if r["n"] == last]
    wanted = sum(r["wanted_kg"] for r in rows)
    if not wanted:
        return None, None, None
    fill = sum(r["bought_kg"] for r in rows) / wanted
    offers = sum(r["offers"] for r in rows) / len(rows)
    if fill < cfg.SCARCE_FILL and offers <= cfg.SCARCE_OFFERS:
        return "scarce", fill, offers
    if offers >= cfg.CROWDED_OFFERS and fill >= 0.95:
        return "crowded", fill, offers
    return "normal", fill, offers


def _p_sell(rows, now, prior_p):
    w_sum, s_sum = 2.0, 2.0 * prior_p          # ค่าก่อน = หลักฐาน 2 ชิ้น
    for r in rows:
        w = cfg.DECAY ** (now - r["n"])
        w_sum += w
        s_sum += w * (r["filled_kg"] / r["kg"] if r["kg"] else 0)
    return s_sum / w_sum


def build(memory, rfqs) -> dict:
    """{buyer: Profile} ของผู้ซื้อทุกรายที่เห็นในรอบนี้หรือในประวัติ"""
    outs = [o for o in (memory.outcomes() if memory else []) if o["status"] in PRICED]
    market = memory.market_rows() if memory else []
    now = len(memory.rounds) if memory else 0
    types = {r["buyer"]: r["buyer_type"] for r in rfqs}
    for o in outs:
        types.setdefault(o["buyer"], o["buyer_type"])
    for m in market:
        types.setdefault(m["buyer"], m["buyer_type"])

    book = {}
    for buyer, btype in types.items():
        start, _, p0 = _prior(btype)
        p = Profile(buyer, btype)
        own = [o for o in outs if o["buyer"] == buyer]
        same_type = [o for o in outs if o["buyer_type"] == btype and o["buyer"] != buyer]
        p.n_own = len(own)
        ok = [o["markup"] for o in own if o["status"] in PASSED]
        bad = [o["markup"] for o in own if o["status"] == "ABOVE_LIMIT"]
        p.passed = max(ok) if ok else None
        p.above = min(bad) if bad else None
        if p.passed is not None and p.above is not None and p.passed >= p.above - cfg.CEILING_GAP:
            # หลักฐานขัดกัน (ล็อตหนึ่งผ่าน อีกล็อตเกินเพดานที่ราคาใกล้กัน เพราะ TV ที่เราประเมินคลาดต่างกันแต่ละล็อต)
            # นโยบายทีม: เกินเพดานแล้วไม่กลับไปยื่นราคานั้นอีก → เก็บเพดานไว้ (เดิมลบทิ้งแล้วไต่ขึ้นใหม่ โดนซ้ำ 26 ครั้ง)
            if cfg.CEILING_CONFLICT_TRUST_PASS:
                p.above = None
            p.why.append("ceiling-conflict")
        last_n = max((o["n"] for o in own), default=None)
        p.last = [(o["markup"], o["status"]) for o in own if o["n"] == last_n]

        nxt = _next_from(own)
        if nxt is not None:
            p.target = nxt
            p.why.append("own")
        else:
            type_nxt = _next_from(same_type)
            p.target = start if type_nxt is None else start + cfg.TYPE_WEIGHT * (type_nxt - start)
            p.why.append("prior" if type_nxt is None else "type")
        p.p_sell = _p_sell(own or same_type, now, p0)

        rows = [m for m in market if m["buyer"] == buyer and m["n"] >= now - 3]
        wanted = sum(m["wanted_kg"] for m in rows)
        if wanted:
            p.fill = sum(m["bought_kg"] for m in rows) / wanted
            p.offers = sum(m["offers"] for m in rows) / len(rows)
            won = [m for m in rows if m.get("price_min") is not None]
            if won:
                p.prices = (min(m["price_min"] for m in won), max(m["price_max"] for m in won))
        # ภาพตลาดรอบล่าสุด (รายนี้ ถ้าไม่มีใช้ทั้งประเภท) → scarce / crowded / normal ใช้ใน pricing.markup()
        p.state, p.fill_last, p.offers_last = _market_state([m for m in market if m["buyer"] == buyer], now)
        if p.state is None:
            p.state, p.fill_last, p.offers_last = _market_state([m for m in market if m["buyer_type"] == btype], now)
        if p.state:
            p.why.append(p.state)
        p.cap = round(max(_prior(btype)[1] - cfg.CEILING_GAP, p.passed or 0), 3)
        if p.limit is not None:
            p.cap = min(p.cap, p.limit)
        if p.limit is not None and p.target > p.limit:
            p.target = p.limit
            p.why.append("capped")
        p.target = round(p.target, 3)
        book[buyer] = p
    return book


def overall_p(book: dict, buyers) -> float:
    """โอกาสขายเฉลี่ยของผู้ซื้อที่เปิด RFQ รอบนี้"""
    ps = [book[b].p_sell for b in buyers if b in book]
    return sum(ps) / len(ps) if ps else 0.5
