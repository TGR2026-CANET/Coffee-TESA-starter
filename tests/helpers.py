"""ของใช้ร่วมของเทสต์ · import โมดูลนี้ก่อนโมดูลของโปรเจกต์เสมอ

- ตั้ง LOG_DIR เป็นโฟลเดอร์ชั่วคราว ก่อน config ถูก import → เทสต์ไม่เขียนทับ logs/ จริง
- กติกา TV (/v1/calendar) อ่านจาก tests/fixtures/calendar.json → เทสต์ไม่ต่อเน็ต
- ตัวสร้างข้อมูลตลาดปลอม (ล็อต RFQ state memory) และ cfg_patch() สำหรับตรึงค่าใน config ระหว่างเทสต์
"""
import json
import os
import random
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="coffee-tests-"))
os.environ["LOG_DIR"] = str(TMP)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config as cfg  # noqa: E402

CALENDAR = json.loads((ROOT / "tests" / "fixtures" / "calendar.json").read_text(encoding="utf-8"))
RULES = CALENDAR["rules"]
NORMAL_8 = [{"rounds": "1-8", "label": "ปกติ", "demand_weight": 1.0, "specialty_share": None}]
COMPETITION_8 = [{"rounds": [1, 2], "label": "ปกติ", "demand_weight": 1.0},
                 {"rounds": [3, 4], "label": "high season", "demand_weight": 1.4},
                 {"rounds": [5, 6], "label": "ปกติ", "demand_weight": 1.0},
                 {"rounds": [7, 8], "label": "ท้ายตลาด", "demand_weight": 0.7}]

SPEC = {"SPECIALTY": {"max_defect_pct": 3.0, "max_moisture_pct": 12.5, "min_screen": 15},
        "COMMERCIAL": {"max_defect_pct": 7.0, "max_moisture_pct": 12.5, "min_screen": None},
        "INSTANT": {"max_defect_pct": 15.0, "max_moisture_pct": 14.0, "min_screen": None}}


def cfg_patch(**values):
    """ตรึงค่าใน config ระหว่างเทสต์ (ค่าที่ทีมปรับบ่อยจะไม่ทำให้เทสต์ตรรกะพัง)"""
    return mock.patch.multiple(cfg, **values)


def sample(defect=2.0, moisture=11.0, screen=16):
    return {"defect_pct": defect, "moisture_pct": moisture, "screen": screen}


def lot(lot_id, kg=600, **q):
    return {"lot_id": lot_id, "kg_total": kg, "kg_available": kg, "sample": sample(**q)}


def rfq(rfq_id, buyer, btype, kg=600, spec=None):
    return {"rfq_id": rfq_id, "buyer": buyer, "buyer_type": btype, "kg": kg, "spec": spec or SPEC[btype]}


def state(lots, rfqs, number=3, of=8, kind="real", capacity=360, round_capacity=360):
    return {"market": "test", "team_id": "T01", "game_id": 1,
            "round": {"round_id": f"R{number}", "kind": kind, "number": number, "of": of},
            "lots": lots, "rfqs": rfqs, "capacity_left_kg": capacity, "round_capacity_kg": round_capacity,
            "standings": {"revenue_thb": 1000.0, "kg_sold": 120, "kg_remaining": 480, "avg_price_per_kg": 110.0}}


def summary_row(rfq_id, buyer, btype, wanted=600, bought=600, offers=4, pmin=100.0, pmax=120.0):
    return {"rfq_id": rfq_id, "buyer": buyer, "buyer_type": btype, "wanted_kg": wanted, "bought_kg": bought,
            "offers": offers, "price_min": pmin, "price_max": pmax, "price_avg": (pmin + pmax) / 2}


def memory_with(rounds, tv, sent=None, name="memory-test.json"):
    """Memory ในไฟล์ชั่วคราวที่มีผลรอบตามที่ให้ (รูปแบบเดียวกับที่ตลาดประกาศ)"""
    from memory import Memory
    path = TMP / f"{random.random():.12f}-{name}"
    path.write_text(json.dumps({"sent": sent or {}, "rounds": rounds}, ensure_ascii=False), encoding="utf-8")
    return Memory(path, tv)


def context(tv, memory=None, llm=None, calendar=None, seconds=60, seed=0):
    import time

    import strategy
    return strategy.Context(RULES, calendar if calendar is not None else NORMAL_8, tv, random.Random(seed),
                            memory, llm, time.time() + seconds)
