"""ดูผลจริงหลังตลาดฝึกปิด แล้วเทียบหลายเกมแบบนักวิทยาศาสตร์ (แก้ทีละอย่าง เล่นหลายเกม เทียบ PR)

    python review.py "SPECIALTY 1.10"     # เรียก /v1/sandbox/reveal บันทึกผลเกมนี้พร้อมป้ายว่าแก้อะไร
    python review.py                      # ดูตารางเทียบทุกเกม + ค่า SD ของ noise ที่ควรตั้ง

main.py เรียก record() เองทุกครั้งที่เห็น market_closed (ป้ายมาจาก CHANGE ใน .env)
reveal มีเฉพาะตลาดฝึก และดูได้ภายใน ~5 นาทีหลังปิด ก่อนตลาดฝึกเริ่มเกมใหม่ · วันแข่งไม่มี
บันทึกใน logs/experiments.csv (ต่อเกม) และ logs/reveal_lots.csv (ต่อล็อต: ตัวอย่างเทียบของจริง)
"""
import csv
import statistics
import sys

import config as cfg
from market import Market, MarketError

GAMES = cfg.LOGS / "experiments.csv"
LOTS = cfg.LOGS / "reveal_lots.csv"


def _append(path, row: dict):
    cfg.LOGS.mkdir(exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)


def _rows(path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def record(api: Market, change: str) -> dict | None:
    try:
        rev = api.reveal()
    except MarketError as e:
        print("  reveal ไม่ได้:", e.message)
        return None
    key = "|".join(f"{l['true_value_per_kg']}" for l in rev["lots"])
    if any(r.get("key") == key for r in _rows(GAMES)):
        print("  เกมนี้บันทึกไว้แล้ว")
        return rev
    for lot in rev["lots"]:
        s, t = lot["sample"], lot["true"]
        ye = lot.get("your_estimate")
        est = ye.get("est_value_per_kg") if isinstance(ye, dict) else ye
        _append(LOTS, {"key": key, "lot_id": lot["lot_id"], "s_defect": s["defect_pct"], "t_defect": t["defect_pct"],
                       "s_moisture": s["moisture_pct"], "t_moisture": t["moisture_pct"],
                       "s_screen": s["screen"], "t_screen": t["screen"], "true_grade": lot.get("true_grade"),
                       "tv_true": lot["true_value_per_kg"], "tv_est": est})
        err = "" if not est else f" เราประเมิน {est:.2f} ({(est - lot['true_value_per_kg']) / lot['true_value_per_kg']:+.1%})"
        print(f"  {lot['lot_id']} ตัวอย่าง d{s['defect_pct']} m{s['moisture_pct']} s{s['screen']} → จริง d{t['defect_pct']} "
              f"m{t['moisture_pct']} s{t['screen']} · TV จริง {lot['true_value_per_kg']:.2f}{err}")
    sc = rev["score"]
    _append(GAMES, {"key": key, "change": change, "pr": sc.get("pr"), "mape": sc.get("mape"),
                    "pr_score": sc.get("pr_score"), "appraisal_score": sc.get("appraisal_score"), "total": sc.get("total")})
    mape = "-" if sc.get("mape") is None else f"{sc['mape']:.1%}"
    print(f"  PR {sc.get('pr')} · ประเมินคลาด {mape} · คะแนนขาย {sc.get('pr_score')}/80 "
          f"+ ประเมิน {sc.get('appraisal_score')}/20 = {sc.get('total')}")
    return rev


def table():
    games = _rows(GAMES)
    by = {}
    for g in games:
        by.setdefault(g["change"], []).append(float(g["pr"]))
    print("PR เฉลี่ยตามสิ่งที่แก้ (เกมเดียวอาจฟลุก เล่น 2–3 เกมต่อการแก้):")
    for change, prs in by.items():
        print(f"  {change:<30} PR {statistics.mean(prs):.4f}  ({len(prs)} เกม)")
    lots = _rows(LOTS)
    if len(lots) >= 4:
        dd = [float(r["t_defect"]) - float(r["s_defect"]) for r in lots]
        dm = [float(r["t_moisture"]) - float(r["s_moisture"]) for r in lots]
        ds = [int(r["t_screen"]) - int(r["s_screen"]) for r in lots]
        print(f"noise จาก {len(lots)} ล็อต (จริง − ตัวอย่าง):")
        print(f"  defect   เฉลี่ย {statistics.mean(dd):+.2f} SD {statistics.pstdev(dd):.2f}  "
              f"(ตั้งอยู่ BIAS {cfg.DEFECT_BIAS:+.2f} SD {cfg.DEFECT_SD})")
        print(f"  ความชื้น เฉลี่ย {statistics.mean(dm):+.2f} SD {statistics.pstdev(dm):.2f}  "
              f"(ตั้งอยู่ BIAS {cfg.MOISTURE_BIAS:+.2f} SD {cfg.MOISTURE_SD})")
        print(f"  ขนาดตะแกรงคลาด {sum(1 for x in ds if x)}/{len(ds)} ล็อต (ต่างได้ {sorted(set(ds))})")
        print("  → ถ้าเฉลี่ยห่างจาก 0 ชัด (หลายเกม) ตั้ง DEFECT_BIAS / MOISTURE_BIAS ใน .env · SD ตั้ง DEFECT_SD / MOISTURE_SD"
              " · ระยะเผื่อสเปก (config.py DEFECT_MARGIN ...) ราว 1 SD · ตะแกรงคลาดบ่อย ตั้ง SCREEN_MARGIN = 1")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 1:
        record(Market(cfg.MARKET_URL, cfg.TEAM_KEY), " ".join(sys.argv[1:]))
    table()
