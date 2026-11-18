"""ประเมิน TV (มูลค่าจริง บาท/กก.) ของล็อตจากผลวัดตัวอย่าง ด้วยสูตรที่ตลาดประกาศใน GET /v1/calendar → rules

TV = p0 × G(d) × M(m) × S(s)   (ตัวเลขทุกตัวมาจาก rules ไม่เขียนตายตัว)

ต่างจาก starter เดิม: ผลวัดตัวอย่างคลาดจากของจริง ล็อตที่ defect ชิดขั้นเกรด (1, 3, 5, 7, 15%) อาจเป็นอีกเกรดจริงๆ
และ TV กระโดดตามขั้น (เช่น 1.04 → 1.00) จึงคิดการกระจายของ TV เมื่อค่าจริงกระจายรอบค่าตัวอย่าง
(config.DEFECT_SD, MOISTURE_SD) แล้วเลือกค่าที่คาดว่าคลาดเป็น % น้อยที่สุด (estimate)
คะแนนประเมิน 20 คะแนน คิดจากความคลาดเฉลี่ย (MAPE) เทียบ TV จริง · คลาด 10% ขึ้นไป = 0
"""
import math

import config as cfg


def grade(rules: dict, defect_pct: float) -> tuple[str, float]:
    for step in rules["defect_steps"]:
        if defect_pct <= step["max_defect_pct"]:
            return step["grade"], step["step"]
    last = rules["defect_steps"][-1]
    return last["grade"], last["step"]


def moisture_factor(rules: dict, m: float) -> float:
    mo = rules["moisture"]
    if m <= mo["ref"]:
        return 1.0
    if m <= mo["hard_limit"]:
        return (1 - m / 100) / (1 - mo["ref"] / 100) - mo["penalty_per_pp"] * (m - mo["ref"])
    return mo["hard_factor"]


def screen_factor(rules: dict, screen: int) -> float:
    factors = {int(k): v for k, v in rules["screen_factors"].items()}
    codes = sorted(factors)
    return factors[min(max(int(screen), codes[0]), codes[-1])]


def true_value(rules: dict, sample: dict) -> float:
    """สูตรตรงจากค่าตัวอย่าง (แบบ starter เดิม)"""
    d, m = sample["defect_pct"], sample["moisture_pct"]
    g = (1 - d / 100) * grade(rules, d)[1]
    return rules["p0"] * g * moisture_factor(rules, m) * screen_factor(rules, sample["screen"])


def _normal_points(sd: float, n: int = 13):
    """จุดและน้ำหนักของการแจกแจงปกติ ช่วง ±3 SD (sd = 0 → จุดเดียว)"""
    if sd <= 0:
        return [(0.0, 1.0)]
    xs = [(-3 + 6 * i / (n - 1)) * sd for i in range(n)]
    ws = [math.exp(-0.5 * (x / sd) ** 2) for x in xs]
    total = sum(ws)
    return [(x, w / total) for x, w in zip(xs, ws)]


def distribution(rules: dict, sample: dict, defect_sd: float = None, moisture_sd: float = None) -> list:
    """[(TV, ความน่าจะเป็น)] เมื่อ defect จริง ~ N(ตัวอย่าง, defect_sd) และความชื้นจริง ~ N(ตัวอย่าง, moisture_sd)"""
    defect_sd = cfg.DEFECT_SD if defect_sd is None else defect_sd
    moisture_sd = cfg.MOISTURE_SD if moisture_sd is None else moisture_sd
    out = []
    d0, m0 = sample["defect_pct"] + cfg.DEFECT_BIAS, sample["moisture_pct"] + cfg.MOISTURE_BIAS
    for dx, dw in _normal_points(defect_sd):
        for mx, mw in _normal_points(moisture_sd, 7):
            s = {**sample, "defect_pct": max(0.0, d0 + dx), "moisture_pct": max(0.0, m0 + mx)}
            out.append((true_value(rules, s), dw * mw))
    return out


def estimate(rules: dict, sample: dict) -> float:
    """ค่าที่ทำให้ความคลาดเป็น % ที่คาดหวังต่ำสุด = มัธยฐานถ่วงน้ำหนัก (น้ำหนัก p ÷ TV)
    คะแนนประเมินลดเป็นเส้นตรงตาม MAPE จึงใช้มัธยฐาน ไม่ใช่ค่าเฉลี่ย: ล็อตที่ defect 6.93 (ขั้น 7%) ค่าเฉลี่ยจะอยู่กลาง
    ระหว่างสองเกรดและคลาดทุกกรณี ส่วนมัธยฐานเลือกเกรดที่น่าจะเป็นกว่า (ถูกเป๊ะเมื่อเดาถูก)"""
    dist = sorted((tv, p / tv) for tv, p in distribution(rules, sample) if tv > 0)
    half, acc = sum(w for _, w in dist) / 2, 0.0
    for tv, w in dist:
        acc += w
        if acc >= half:
            return tv
    return true_value(rules, sample)


def estimate_lots(rules: dict, lots: list[dict]) -> dict[str, float]:
    return {lot["lot_id"]: round(estimate(rules, lot["sample"]), 2) for lot in lots}


def appraisal_items(rules: dict, lots: list[dict], tv: dict[str, float]) -> list[dict]:
    return [{"lot_id": lot["lot_id"], "grade": grade(rules, lot["sample"]["defect_pct"])[0],
             "est_value_per_kg": tv[lot["lot_id"]]} for lot in lots]
