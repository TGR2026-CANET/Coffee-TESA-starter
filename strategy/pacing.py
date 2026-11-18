"""จังหวะการขายทั้งเกม: เหลือกี่รอบ ปฏิทินตลาด และความกดดัน

กติกา: ทีมเสนอรวมได้ไม่เกิน round_capacity_kg ต่อรอบ (≈ 1.5 × กก.ทั้งหมด ÷ จำนวนรอบ) · กก. ที่เสนอแล้วไม่ขาย
เสียโควตารอบนั้นไปเปล่าๆ · ของที่เหลือตอนจบได้ 0.80 × TV
→ ความกดดัน = กก. ที่เหลือ ÷ (โควตาต่อรอบ × รอบที่เหลือ × โอกาสขาย)
   < 0.6 สบาย ดันราคาได้ · ~1 พอดี · > 1 ขายไม่ทันถ้าไม่ลดราคา
ปฏิทิน (GET /v1/calendar → calendar): demand_weight ของแต่ละช่วง (ปกติ 1.0 · เทศกาล 1.4 · ซบเซา 0.7)
"""
import config as cfg


def rounds_total(state: dict, rules: dict) -> int:
    return (state.get("round") or {}).get("of") or rules["timeline"]["rounds"]


def rounds_left(state: dict, rules: dict) -> int:
    """รอบจริง (นับคะแนน) ที่เหลือ รวมรอบนี้ · รอบซ้อม = ยังเหลือทุกรอบ"""
    rnd, total = state["round"], rounds_total(state, rules)
    if rnd.get("kind") == "practice":
        return total
    return max(1, total - (rnd.get("number") or 1) + 1)


def is_last(state: dict, rules: dict) -> bool:
    rnd = state["round"]
    return rnd.get("kind") == "real" and (rnd.get("number") or 0) >= rounds_total(state, rules)


def round_numbers(spec) -> set:
    """รอบในปฏิทิน: [1, 2] · 3 · "1-8" · "1,2,5" · "R3-R4" (ตลาดฝึกส่ง list · ตลาด 202.29.227.22 ส่ง "1-8")"""
    if spec is None:
        return set()
    if isinstance(spec, int):
        return {spec}
    if isinstance(spec, (list, tuple, set)):
        out = set()
        for s in spec:
            out |= round_numbers(s)
        return out
    out = set()
    for part in str(spec).replace("R", "").replace("r", "").replace(" ", "").split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            if a.isdigit() and b.isdigit():
                out |= set(range(int(a), int(b) + 1))
        elif part.isdigit():
            out.add(int(part))
    return out


def season(calendar: list, number) -> dict:
    """ช่วงของปฏิทินที่รอบนี้อยู่ {label, demand_weight, specialty_share} (ไม่พบ = ปกติ)"""
    for c in calendar or []:
        if number in round_numbers(c.get("rounds")):
            return c
    return {"label": "?", "demand_weight": 1.0, "specialty_share": None}


def ahead(state: dict, rules: dict, calendar: list) -> list:
    """ป้ายของรอบจริงที่เหลือ (รวมรอบนี้) ไว้ส่งให้ LLM"""
    total = rounds_total(state, rules)
    start = total - rounds_left(state, rules) + 1
    return [(n, season(calendar, n).get("demand_weight", 1.0)) for n in range(start, total + 1)]


def weights(state: dict, rules: dict, calendar: list) -> list:
    """demand_weight ของรอบจริงที่เหลือ รวมรอบนี้ (ไม่มีปฏิทิน = 1.0 ทุกรอบ)"""
    return [w for _, w in ahead(state, rules, calendar)] or [1.0]


def peak_ahead(state: dict, rules: dict, calendar: list) -> bool:
    """ยังมีรอบ high season (demand_weight > 1) รออยู่ข้างหน้า (ไม่นับรอบนี้)"""
    return any(w > 1 for w in weights(state, rules, calendar)[1:])


def better_ahead(state: dict, rules: dict, calendar: list) -> bool:
    """ยังมีรอบที่ตลาดไม่ซบเซา (demand_weight ≥ 1) รออยู่ข้างหน้า · วันแข่งท้ายตลาดซบเซา 3 รอบ = ไม่มีแล้ว"""
    return any(w >= 1 for w in weights(state, rules, calendar)[1:])


def late(state: dict, rules: dict, calendar: list = None) -> bool:
    """ช่วงท้ายเกม (เหลือ ≤ LATE_ROUNDS รอบจริง) ตลาดไม่ซบเซา และของเราเหลือ ≤ LATE_SLACK × โควตาที่เหลือ
    = ทีมอื่นของเริ่มหมด และถ้าเราแพ้รอบนี้ยังมีโควตาให้ยื่นใหม่ จึงสู้ราคาได้
    ท้ายตลาดที่ผู้ซื้อต้องการน้อย (demand_weight < 1) ไม่สู้ราคา: ทีมอื่นเทขายแย่งผู้ซื้อที่เหลือน้อย"""
    if state["round"].get("kind") != "real" or rounds_left(state, rules) > cfg.LATE_ROUNDS:
        return False
    if any(w < 1 for w in weights(state, rules, calendar)):
        return False
    cap = state.get("round_capacity_kg") or rules.get("round_capacity")
    if not cap:
        return True
    left_kg = sum(l["kg_available"] for l in state["lots"])
    return left_kg <= cfg.LATE_SLACK * cap * rounds_left(state, rules)


def offer_budget(state: dict, rules: dict, p_sell: float, calendar: list = None):
    """กก. ที่ควรยื่นรอบนี้ (≤ capacity_left_kg) ให้ของหมดพอดีรอบสุดท้าย ไม่ใช่หมดก่อน
    = ของที่เหลือ × (demand_weight รอบนี้ ÷ ผลรวม demand_weight รอบที่เหลือ) ÷ โอกาสขาย (ปัดเป็นกระสอบ)
    → high season ได้ส่วนแบ่งมาก · ท้ายตลาดซบเซาได้น้อย (ขายให้หมดก่อนถึง) · รอบสุดท้าย = เต็มโควตา
    เกม 64: ยื่นเต็มโควตาทุกรอบ ชนะ 76% ของหมดตั้งแต่ R7 แต่ R8 ผู้ซื้อ COMMERCIAL ซื้อได้แค่ 25% (ทีมอื่นของหมด)"""
    cap = state.get("capacity_left_kg")
    n = rounds_left(state, rules)
    if cap is None or not cfg.PACE_BUDGET or n <= 1 or state["round"].get("kind") != "real":
        return cap
    bag = int(rules.get("bag_kg", 60))
    left_kg = sum(l["kg_available"] for l in state["lots"])
    ws = weights(state, rules, calendar)
    share = ws[0] / sum(ws) if sum(ws) > 0 else 1 / n
    want = left_kg * share / max(p_sell, cfg.PACE_MIN_P)
    return min(cap, max(bag, round(want / bag) * bag))


def pressure(state: dict, rules: dict, p_sell: float, calendar: list = None) -> float:
    """กก. ที่เหลือ ÷ (โควตา × รอบที่เหลือที่มีผล × โอกาสขาย) · รอบซบเซานับแค่ demand_weight ของมัน (0.7 รอบ)
    high season นับ 1 รอบ (โควตาจำกัดอยู่ดี) → ท้ายตลาดซบเซาทำให้ความกดดันขึ้นเร็ว ลดราคาขายก่อนถึง"""
    left_kg = sum(l["kg_available"] for l in state["lots"])
    cap = state.get("round_capacity_kg") or rules.get("round_capacity")
    if not cap:
        return 0.5                                  # ไม่จำกัดต่อรอบ
    eff = sum(min(w, 1.0) for w in weights(state, rules, calendar)) if calendar else rounds_left(state, rules)
    return left_kg / (cap * max(eff, 0.5) * max(p_sell, 0.2))
