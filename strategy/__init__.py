"""กลยุทธ์ขาย (Ratio Ladder ฉบับตลาด RFQ v1)

  specs.py     ล็อตผ่านสเปก RFQ ไหม แบบเผื่อ noise ของตัวอย่าง (safe / risky)
  buyers.py    เรียนรู้เพดานลับและโอกาสขายของผู้ซื้อแต่ละราย จากผลข้อเสนอของเรา + ผลตลาด → Profile
  pacing.py    รอบที่เหลือ ปฏิทินตลาด ความกดดัน (ขายทันไหมภายใต้โควตาต่อรอบ)
  pricing.py   markup ต่อผู้ซื้อของรอบนี้ (เป้าที่เรียนรู้ + ตัวปรับของรอบ + ขอบเขต)
  advisor.py   LLM ปรับ markup ต่อผู้ซื้อภายในกรอบ
  plan.py      candidates → markup → allocate → guard (decide) · probe รอบซ้อม · fallback ตอนโค้ดพัง

ตัวเลขที่ปรับได้อยู่ใน config.py ทั้งหมด
"""
from .plan import Context, decide, fallback, guard

__all__ = ["Context", "decide", "fallback", "guard"]
