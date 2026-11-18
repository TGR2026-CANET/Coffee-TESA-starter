"""ล็อตผ่านสเปกของ RFQ ไหม โดยเผื่อ noise ของผลวัดตัวอย่าง

ผู้ซื้อตรวจกับ "คุณภาพจริง" ไม่ใช่ตัวอย่าง ล็อตที่ตัวอย่างอยู่ชิดขอบสเปกอาจโดน SPEC_MISMATCH
  safe   = ผ่านแม้ค่าจริงแย่กว่าตัวอย่างเท่าระยะเผื่อ (config.DEFECT_MARGIN, MOISTURE_MARGIN, SCREEN_MARGIN)
  risky  = ผ่านด้วยค่าตัวอย่าง แต่ไม่ผ่านระยะเผื่อ (ยื่นได้ แต่ลำดับหลัง)
  None   = ไม่ผ่าน
ช่องสเปกที่เป็น null = ไม่กำหนด
"""
import config as cfg


def _ok(sample: dict, spec: dict, dm: float, mm: float, sm: float) -> bool:
    """dm, mm, sm = ระยะเผื่อ · ระยะเผื่อ > 0 ใช้ค่าตัวอย่างที่ปรับ bias แล้ว (config.DEFECT_BIAS, MOISTURE_BIAS)"""
    d = sample["defect_pct"] + (cfg.DEFECT_BIAS if dm else 0)
    m = sample["moisture_pct"] + (cfg.MOISTURE_BIAS if mm else 0)
    if spec.get("max_defect_pct") is not None and d > spec["max_defect_pct"] - dm:
        return False
    if spec.get("max_moisture_pct") is not None and m > spec["max_moisture_pct"] - mm:
        return False
    if spec.get("min_screen") is not None and sample["screen"] < spec["min_screen"] + sm:
        return False
    return True


def check(sample: dict, spec: dict) -> str | None:
    if not _ok(sample, spec, 0, 0, 0):
        return None
    if _ok(sample, spec, cfg.DEFECT_MARGIN, cfg.MOISTURE_MARGIN, cfg.SCREEN_MARGIN):
        return "safe"
    return "risky"


def passes(sample: dict, spec: dict) -> bool:
    """ผ่านด้วยค่าตัวอย่าง (ใช้ใน guard)"""
    return _ok(sample, spec, 0, 0, 0)


def as_strict(spec: dict, failed: dict) -> bool:
    """spec เข้มเท่าหรือเข้มกว่า spec ที่ล็อตนี้เคยโดน SPEC_MISMATCH ทุกข้อ = ล็อตนี้ไม่ผ่านแน่"""
    def lim(s, k, none):
        return none if s.get(k) is None else s[k]
    return (lim(spec, "max_defect_pct", 1e9) <= lim(failed, "max_defect_pct", 1e9)
            and lim(spec, "max_moisture_pct", 1e9) <= lim(failed, "max_moisture_pct", 1e9)
            and lim(spec, "min_screen", -1) >= lim(failed, "min_screen", -1))
