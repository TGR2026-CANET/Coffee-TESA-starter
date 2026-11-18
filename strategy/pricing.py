"""ตั้ง markup ต่อผู้ซื้อของรอบนี้ = เป้าที่เรียนรู้ได้ (buyers.Profile.target) + ตัวปรับของรอบ แล้วบีบเข้าขอบเขต

ตัวปรับของรอบ (บวกกันได้):
  high season (demand_weight > 1)             +FESTIVAL_UP_TYPE ตามประเภท (SPECIALTY รับซื้อมากกว่าปกติ ได้มากสุด)
  ซบเซา (demand_weight < 1) และกดดัน ≥ 0.85     −SLUMP_CUT   (ไม่กดดัน = ไม่ลด แต่ไม่ยื่นต่ำกว่า SLUMP_MIN_MARKUP)
  ความกดดัน (pacing.pressure)                  ตาม PRESSURE_STEPS
  รอบสุดท้ายที่นับคะแนน                         −LAST_ROUND_SAFE และต่ำกว่าเพดานที่รู้อย่างน้อยเท่านั้น
ขอบเขต: MIN_MARKUP ≤ markup ≤ min(MAX_MARKUP, เพดานที่เรียนรู้ได้ − CEILING_GAP)
"""
import config as cfg

from . import pacing


def round_adjust(state: dict, rules: dict, calendar: list, press: float) -> tuple[float, list]:
    adj, why = 0.0, []
    w = pacing.season(calendar, state["round"].get("number")).get("demand_weight", 1.0)
    if w > 1:
        why.append("high-season")                  # โบนัสต่อประเภทผู้ซื้อ บวกใน markup(festival=True)
    elif w < 1 and press >= 0.85:
        adj -= cfg.SLUMP_CUT
        why.append(f"slump-{cfg.SLUMP_CUT}")
    for upto, step in cfg.PRESSURE_STEPS:
        if press <= upto:
            if step:
                adj += step
                why.append(f"pressure{press:.2f}{step:+.2f}")
            break
    return adj, why


def floor(btype) -> float:
    """markup ต่ำสุดที่ยอมยื่นให้ผู้ซื้อประเภทนี้"""
    return max(cfg.MIN_MARKUP, cfg.TYPE_FLOOR.get(btype, cfg.MIN_MARKUP))


def clamp(m: float, profile=None, last: bool = False) -> float:
    lo = floor(profile.btype) if profile is not None else cfg.MIN_MARKUP
    hi = cfg.MAX_MARKUP
    if profile is not None and profile.limit is not None:
        hi = min(hi, profile.limit - (cfg.LAST_ROUND_SAFE - cfg.CEILING_GAP if last else 0))
    return round(min(max(m, lo), max(hi, lo)), 3)


def markup(profile, adj: float, last: bool, late: bool = False, festival: bool = False) -> float:
    """เป้า + ตัวปรับของรอบ แยก 3 ท่าตามภาพตลาดของผู้ซื้อรายนี้ (buyers.Profile.state):
      สู้ราคา  scarce (ของขาดรอบล่าสุด) หรือ late (ช่วงท้ายเกม ตลาดไม่แน่น และเรายังมีโควตาเหลือให้แก้ตัว)
               → ขึ้นเป็น เป้า + SCARCE_UP / LATE_UP ได้ แต่ไม่เกิน profile.cap (ระดับที่เคยผ่านจริง หรือเพดานเดา − GAP)
               และไม่ต่ำกว่าท่าปกติ · ถ้าเป้าเกิน cap อยู่แล้ว ไม่ลดตามความกดดันต่ำกว่า cap
      รอบสุดท้าย ตลาดไม่แน่น → ลดแค่ LAST_ROUND_CALM · แน่น (crowded) → LAST_ROUND_SAFE เต็ม
      ปกติ     เป้า + ตัวปรับ"""
    st = profile.state
    bonus = cfg.FESTIVAL_UP_TYPE.get(profile.btype, cfg.FESTIVAL_UP) if festival else 0.0
    cut = adj
    if last:
        cut -= cfg.LAST_ROUND_SAFE if st == "crowded" else cfg.LAST_ROUND_CALM
    m = profile.target + bonus + max(cut, -cfg.MAX_ROUND_CUT)  # ลดซ้อนกันได้ไม่เกิน MAX_ROUND_CUT
    normal = clamp(m, profile, last)
    if st == "scarce" or (late and st != "crowded"):
        up = (cfg.SCARCE_UP if st == "scarce" else cfg.LATE_UP) + bonus
        fight = profile.target + up if profile.cap is None else min(profile.target + up, profile.cap)
        return max(normal, clamp(fight, profile))
    return normal
