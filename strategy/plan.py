"""ตัดสินใจข้อเสนอของรอบ: candidates → markup ต่อผู้ซื้อ (กฎ → LLM ในกรอบ) → จัดสรร กก. → guard

ทุกรอบ:
  1. candidates()  คู่ (ล็อต × RFQ) ที่ผ่านสเปก: safe (เผื่อ noise แล้ว) / risky (ชิดขอบ) · ตัดคู่ที่ล็อตเคยโดน
                   SPEC_MISMATCH กับสเปกแบบเดียวกันหรือเข้มกว่า
  2. markups       buyers.build() เรียนรู้เพดานลับต่อผู้ซื้อ → pricing.markup() + ตัวปรับของรอบ
                   → advisor.consult() ให้ LLM ปรับในกรอบ (ไม่ตอบ = ใช้กฎ)
  3. allocate()    เรียงคู่ตาม "ได้เกินค่าเศษ" ต่อ กก. = (markup − 0.80) × TV × โอกาสขาย แล้วเติม กก. จนเต็มโควตารอบ
                   รอบแรกของการเติม: ข้อเสนอเดียวไม่เกินครึ่งโควตา (กระจายความเสี่ยงไปหลาย RFQ) แล้วค่อยเติมส่วนที่เหลือ
  4. guard()       ตรวจทุกข้อเสนอก่อนส่ง: มีล็อต/RFQ จริง ผ่านสเปก กก. ทวีคูณกระสอบ ไม่เกินที่เหลือ/ที่ RFQ ต้องการ/
                   capacity_left_kg · markup อยู่ใน [MIN_MARKUP, MAX_MARKUP] · ราคา 0–1000
รอบซ้อม (practice): probe() ยื่นล็อตละ 1 กระสอบไปหลายผู้ซื้อที่ markup สูงกว่าปกติ เพื่อหาเพดานก่อนรอบจริง
"""
import random
from dataclasses import dataclass, field

import config as cfg

from . import advisor, buyers, pacing, pricing, specs


@dataclass
class Context:
    rules: dict                       # GET /v1/calendar → rules
    calendar: list                    # GET /v1/calendar → calendar
    tv: dict                          # lot_id → TV ที่เราประเมิน (บาท/กก.)
    rng: random.Random
    memory: object = None
    llm: object = None
    deadline: float = 0.0             # time.time() ที่รอบปิด
    notes: list = field(default_factory=list)

    @property
    def bag(self) -> int:
        return int(self.rules.get("bag_kg", 60))

    @property
    def salvage(self) -> float:
        return float(self.rules.get("salvage_ratio", 0.8))


def mismatches(memory) -> dict:
    """{lot_id: [สเปกที่ล็อตนี้เคยโดน SPEC_MISMATCH]}"""
    out = {}
    for o in memory.outcomes() if memory else []:
        if o["status"] == "SPEC_MISMATCH" and o.get("spec"):
            out.setdefault(o["lot_id"], []).append(o["spec"])
    return out


def candidates(state: dict, ctx: Context) -> list[dict]:
    bad = mismatches(ctx.memory)
    out = []
    for lot in state["lots"]:
        if lot["kg_available"] < ctx.bag:
            continue
        for rfq in state["rfqs"]:
            fit = specs.check(lot["sample"], rfq["spec"])
            if fit is None or any(specs.as_strict(rfq["spec"], f) for f in bad.get(lot["lot_id"], [])):
                continue
            out.append({"lot_id": lot["lot_id"], "rfq_id": rfq["rfq_id"], "buyer": rfq["buyer"],
                        "buyer_type": rfq["buyer_type"], "fit": fit, "tv": ctx.tv[lot["lot_id"]]})
    return out


TIER = {"SPECIALTY": 3, "COMMERCIAL": 2, "INSTANT": 1}


def _tier(c) -> int:
    return TIER.get(c["buyer_type"], 2)


def allocate(state: dict, ctx: Context, cands: list, markup: dict, book: dict, hold_below: float = None,
             rounds_left: int = None, budget_kg: int = None, deep_ok: bool = True, reserve_top: bool = False) -> list:
    """เติม กก. 3 ขั้น (ทุกขั้นเคารพ MAX_RFQS_PER_LOT, กก. ที่ล็อต/RFQ เหลือ และโควตา):
      1. pace   ทุกล็อตได้ส่วนแบ่งของรอบนี้ ≈ กก.ที่เหลือ ÷ รอบที่เหลือ ไปคู่ที่ดีที่สุดของล็อตนั้น
                (ล็อตที่ไปได้แค่ INSTANT ไม่ถูกดองไว้เทขายตอนท้ายเกม)
      2. spread ข้อเสนอเดียวไม่เกินครึ่งโควตา และผู้ซื้อรายเดียวไม่เกิน MAX_BUYER_SHARE ของโควตา
      3. fill   เติมที่เหลือตามลำดับเดิม ไม่จำกัด
    ลำดับ = ได้เกินค่าเศษต่อ กก. × DOWNGRADE_WEIGHT^(ขั้นที่ต่ำกว่าผู้ซื้อสเปกสูงสุดที่ล็อตนี้ไปได้ในรอบนี้)
    → ล็อตที่ผ่าน SPECIALTY ไป SPECIALTY ก่อน (จ่ายสูงกว่า แข่งน้อยกว่า) เหลือค่อยไป COMMERCIAL
    ล็อตที่ไปได้ถึง SPECIALTY ไม่ลงไป INSTANT (ต่ำกว่า 2 ขั้น) เว้นแต่ deep_ok (กดดันมาก/รอบสุดท้าย)"""
    bag = ctx.bag
    capped = state.get("capacity_left_kg") is not None
    budget = state["capacity_left_kg"] if capped else 10**9
    if budget_kg is not None:
        budget = min(budget, budget_kg)
    lot_left = {l["lot_id"]: l["kg_available"] // bag * bag for l in state["lots"]}
    rfq_left = {r["rfq_id"]: r["kg"] // bag * bag for r in state["rfqs"]}
    best = {}
    for c in cands:
        best[c["lot_id"]] = max(best.get(c["lot_id"], 0), _tier(c))
    scored = []
    for c in cands:
        m = markup.get(c["buyer"])
        if m is None or (hold_below is not None and m < hold_below):
            continue
        lim = book[c["buyer"]].limit
        if lim is not None and m > lim + 1e-9:       # ราคาต่ำสุดที่ยอม (TYPE_FLOOR) ยังเกินเพดานที่รายนี้เคยปฏิเสธ: ไม่ยื่นเสียโควตา
            continue
        drop = best[c["lot_id"]] - _tier(c)
        if drop >= 2 and not deep_ok:
            continue
        if reserve_top and drop >= 1 and best[c["lot_id"]] == TIER["SPECIALTY"]:
            continue                                  # ก่อน high season: ล็อตระดับ SPECIALTY ขายให้ SPECIALTY เท่านั้น ที่เหลือเก็บไว้
        p = book[c["buyer"]].p_sell * (cfg.RISKY_WEIGHT if c["fit"] == "risky" else 1)
        gain = ((m - ctx.salvage) * c["tv"] * p * cfg.DOWNGRADE_WEIGHT ** drop
                * (1 + cfg.JITTER * ctx.rng.uniform(-1, 1)))
        if gain > 0:
            scored.append((gain, c, m))
    scored.sort(key=lambda x: -x[0])
    plan, rfqs_of, buyer_kg = {}, {}, {}
    total = min(budget, sum(lot_left.values()))
    first_chunk = max(bag, (total // 2) // bag * bag)
    many_buyers = len({c["buyer"] for _, c, _ in scored}) > 1
    buyer_cap = max(bag, int(total * cfg.MAX_BUYER_SHARE) // bag * bag) if capped and many_buyers else 10**9

    def put(c, m, want):
        nonlocal budget
        key = (c["lot_id"], c["rfq_id"])
        if key not in plan and len(rfqs_of.get(c["lot_id"], ())) >= cfg.MAX_RFQS_PER_LOT:
            return 0
        kg = min(lot_left[c["lot_id"]], rfq_left[c["rfq_id"]], budget, want) // bag * bag
        if kg < bag:
            return 0
        plan.setdefault(key, {"lot_id": c["lot_id"], "rfq_id": c["rfq_id"], "kg": 0, "markup": m})["kg"] += kg
        rfqs_of.setdefault(c["lot_id"], set()).add(c["rfq_id"])
        buyer_kg[c["buyer"]] = buyer_kg.get(c["buyer"], 0) + kg
        lot_left[c["lot_id"]] -= kg
        rfq_left[c["rfq_id"]] -= kg
        budget -= kg
        return kg

    if cfg.PACE_EVERY_LOT and rounds_left:
        order = list(dict.fromkeys(c["lot_id"] for _, c, _ in scored))   # ล็อตที่คู่ดีที่สุดได้มากกว่ามาก่อน
        for lot_id in order:
            need = max(bag, round(lot_left[lot_id] / rounds_left / bag) * bag)
            for _, c, m in scored:
                if need < bag:
                    break
                if c["lot_id"] == lot_id:
                    need -= put(c, m, min(need, first_chunk, buyer_cap - buyer_kg.get(c["buyer"], 0)))
    for _, c, m in scored:
        put(c, m, min(first_chunk, buyer_cap - buyer_kg.get(c["buyer"], 0)))
    for _, c, m in scored:
        put(c, m, 10**9)
    return list(plan.values())


def probe(state: dict, ctx: Context, cands: list, markup: dict, book: dict) -> list:
    """รอบซ้อม: ผู้ซื้อละ 1 ข้อเสนอ 1 กระสอบ ล็อต TV สูงสุดที่ผ่านสเปกแบบ safe ที่ markup สูงกว่ากฎ PROBE_UP
    ล็อตหนึ่งใช้ไม่เกิน PROBE_BAGS_PER_LOT กระสอบ"""
    plan, used = [], {}
    avail = {l["lot_id"]: min(l["kg_available"], cfg.PROBE_BAGS_PER_LOT * ctx.bag) for l in state["lots"]}
    for buyer in sorted({c["buyer"] for c in cands}, key=lambda b: -markup.get(b, 0)):
        opts = sorted((c for c in cands if c["buyer"] == buyer and c["fit"] == "safe"), key=lambda c: -c["tv"])
        for c in opts:
            if used.get(c["lot_id"], 0) + ctx.bag <= avail[c["lot_id"]]:
                m = pricing.clamp(markup[buyer] + cfg.PROBE_UP, book[buyer])
                plan.append({"lot_id": c["lot_id"], "rfq_id": c["rfq_id"], "kg": ctx.bag, "markup": m})
                used[c["lot_id"]] = used.get(c["lot_id"], 0) + ctx.bag
                break
        if len(plan) >= cfg.PROBE_MAX_OFFERS:
            break
    return plan


def guard(state: dict, ctx: Context, plan: list) -> list:
    """แปลงแผนเป็นข้อเสนอที่ตลาดรับแน่ ไม่เชื่อตัวเลขจาก LLM หรือจากกฎตรงๆ"""
    bag = ctx.bag
    lots = {l["lot_id"]: l for l in state["lots"]}
    rfqs = {r["rfq_id"]: r for r in state["rfqs"]}
    room = state["capacity_left_kg"] if state.get("capacity_left_kg") is not None else 10**9
    lot_left = {i: l["kg_available"] for i, l in lots.items()}
    rfq_left = {i: r["kg"] for i, r in rfqs.items()}
    offers, seen = [], set()
    for p in plan:
        lot, rfq = lots.get(p["lot_id"]), rfqs.get(p["rfq_id"])
        key = (p["lot_id"], p["rfq_id"])
        if lot is None or rfq is None or key in seen or not specs.passes(lot["sample"], rfq["spec"]):
            ctx.notes.append(f"guard dropped {p}")
            continue
        kg = min(int(p["kg"]), lot_left[lot["lot_id"]], rfq_left[rfq["rfq_id"]], room) // bag * bag
        if kg < bag:
            continue
        markup = min(max(float(p["markup"]), pricing.floor(rfq.get("buyer_type"))), cfg.MAX_MARKUP)
        price = round(markup * ctx.tv[lot["lot_id"]], 2)
        if not 0 < price <= 1000:
            ctx.notes.append(f"guard dropped price {price} {p}")
            continue
        offers.append({"rfq_id": rfq["rfq_id"], "lot_id": lot["lot_id"], "kg": kg, "price_per_kg": price})
        seen.add(key)
        lot_left[lot["lot_id"]] -= kg
        rfq_left[rfq["rfq_id"]] -= kg
        room -= kg
    return offers


def decide(state: dict, ctx: Context) -> tuple[list, str, dict]:
    """คืน (offers, source, info) · source = llm / rules / probe / none"""
    cands = candidates(state, ctx)
    if not cands:
        return [], "none", {}
    rules = ctx.rules
    book = buyers.build(ctx.memory, state["rfqs"])
    open_buyers = sorted({c["buyer"] for c in cands})
    p = buyers.overall_p(book, open_buyers)
    press = pacing.pressure(state, rules, p, ctx.calendar)
    adj, why = pricing.round_adjust(state, rules, ctx.calendar, press)
    last = pacing.is_last(state, rules)
    late = pacing.late(state, rules, ctx.calendar) and not last
    w = pacing.season(ctx.calendar, state["round"].get("number")).get("demand_weight", 1.0)
    rule = {b: pricing.markup(book[b], adj, last, late, festival=w > 1) for b in open_buyers}
    if late:
        why.append("late")
    info = {"pressure": round(press, 2), "p_sell": round(p, 2), "adjust": why, "rule": rule,
            "market": {b: book[b].state for b in open_buyers if book[b].state}}
    practice = state["round"].get("kind") == "practice"
    if practice and cfg.PRACTICE_MODE == "skip":
        return [], "skip", info
    if practice and cfg.PRACTICE_MODE == "probe":
        return guard(state, ctx, probe(state, ctx, cands, rule, book)), "probe", info

    # ซบเซาแต่ยังไม่กดดัน: เก็บของไว้ขายรอบที่ดีกว่า · ถ้าข้างหน้าไม่มีรอบที่ดีกว่าแล้ว (ท้ายตลาดซบเซา) ไม่เก็บ
    hold = (cfg.SLUMP_MIN_MARKUP if (w < 1 and press < 0.85 and not last
                                     and pacing.better_ahead(state, rules, ctx.calendar)) else None)
    left = pacing.rounds_left(state, rules)
    budget = pacing.offer_budget(state, rules, p, ctx.calendar)
    info["budget_kg"] = budget
    deep = last or press > cfg.DEEP_DOWNGRADE_PRESSURE
    reserve = (cfg.SPEC_RESERVE and w <= 1 and press <= cfg.SPEC_RESERVE_MAX_PRESSURE
               and pacing.peak_ahead(state, rules, ctx.calendar))
    if reserve:
        why.append("spec-reserve")

    final, source = dict(rule), "rules"
    if ctx.llm is not None:
        got = advisor.consult(state, ctx, book, rule, press,
                              {"cands": cands, "budget_kg": budget, "late": late, "last": last, "adjust": why})
        if got:
            final.update(got[0])
            source = "llm"
    info["markups"] = final
    plan = allocate(state, ctx, cands, final, book, hold, left, budget, deep, reserve)
    if hold and not plan:                                    # ซบเซาแต่ไม่มีคู่ไหนถึงเกณฑ์ ก็ยังขายตามปกติ
        plan = allocate(state, ctx, cands, final, book, rounds_left=left, budget_kg=budget, deep_ok=deep,
                        reserve_top=reserve)
    return guard(state, ctx, plan), source, info


def fallback(state: dict, ctx: Context) -> list:
    """strategy พัง: ขายด้วยค่าก่อนตามประเภทผู้ซื้อ ไม่ใช้ประวัติ (ส่งข้อเสนอได้ทุกรอบเสมอ)"""
    cands = candidates(state, ctx)
    book = {c["buyer"]: buyers.Profile(c["buyer"], c["buyer_type"], target=cfg.PRIOR.get(
        c["buyer_type"], cfg.PRIOR["?"])[0], p_sell=0.5) for c in cands}
    markup = {b: pricing.clamp(p.target) for b, p in book.items()}
    return guard(state, ctx, allocate(state, ctx, cands, markup, book))
