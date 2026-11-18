"""ตรวจ log ของ agent (decisions.jsonl) ว่า ไม่มี crash และทุกรอบมี offer ส่ง แล้วเขียนหลักฐานลง evidence/

    python check_log.py                         ทุกเกมใน logs-practice/ (หรือ LOG_DIR)
    python check_log.py --last 1                เฉพาะเกมล่าสุด
    python check_log.py --game 85               เฉพาะ game_id ที่ระบุ
    python check_log.py --log-dir logs          log ของตลาดแข่ง
    python check_log.py --console evidence/dod-*-agent.log    หา Traceback ใน output ของ agent ด้วย

ต่อเกม (แยกเกมด้วย event new_game):
  FAIL  strategy_crashed / ข้อเสนอจาก fallback / รอบที่ agent อยู่แต่ไม่มี offers_sent
        / ส่ง 0 ข้อเสนอทั้งที่ยังมีของเหลือ / เกมก่อนหน้าไม่มี market_closed (agent หยุดกลางเกมแล้วไม่กลับมา)
        / Traceback ในไฟล์ --console
  WARN  agent รีสตาร์ตกลางเกม (log แยกไม่ได้ว่า Ctrl+C หรือล้ม) / เข้าเกมกลางคัน / ตลาดปฏิเสธข้อเสนอ
        / ส่งรอบเดียวกันซ้ำ (agent 2 ตัวใช้ key เดียวกัน) / เกมยังไม่จบ
  ส่ง 0 ข้อเสนอเพราะของหมดแล้ว = ผ่าน
exit 0 = ไม่มี FAIL
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def split_games(rows: list[dict]) -> list[dict]:
    """แบ่ง event เป็นเกม · new_game ที่ game_id เดิมซ้ำ = agent รีสตาร์ตในเกมเดิม"""
    games: list[dict] = []
    cal, opened = None, False
    for row in rows:
        ev = row["event"]
        if ev == "calendar":                      # main.py เพิ่งเปิด (log calendar ก่อน new_game เสมอ)
            cal, opened = row, True
            continue
        if ev == "new_game":
            gid = row.get("game_id")
            restart, opened = opened, False
            if games and gid is not None and games[-1]["id"] == gid:
                games[-1]["starts"] += restart    # agent เปิดใหม่ในเกมเดิม
                continue
            games.append({"id": gid if gid is not None else f"#{len(games) + 1}", "market": row.get("market"),
                          "kg": row.get("kg") or 0, "time": row["time"], "cal": cal, "events": [],
                          "closed": False, "starts": 1})
            continue
        if not games:
            continue
        games[-1]["events"].append(row)
        if ev == "market_closed":
            games[-1]["closed"] = True
    return games


def round_ids(cal: dict | None) -> list[str]:
    if not cal:
        return []
    return ([f"P{i}" for i in range(1, (cal.get("practice_rounds") or 0) + 1)]
            + [f"R{i}" for i in range(1, (cal.get("rounds") or 0) + 1)])


def check_game(g: dict, later_game: bool) -> tuple[list[tuple[str, str]], list[str]]:
    """คืน (ผลตรวจ [(PASS/WARN/FAIL, ข้อความ)], แถวตารางรายรอบ)"""
    out: list[tuple[str, str]] = []
    evs = g["events"]
    by = lambda name: [e for e in evs if e["event"] == name]  # noqa: E731

    crashed = by("strategy_crashed")
    out.append(("FAIL" if crashed else "PASS", "ไม่มี strategy_crashed"
                + (f" — {len(crashed)} ครั้ง: {crashed[0].get('error', '')[:120]}" if crashed else "")))

    if g["starts"] > 1:
        out.append(("WARN", f"agent เปิดใหม่กลางเกม {g['starts'] - 1} ครั้ง (log แยกไม่ได้ว่า Ctrl+C หรือล้ม "
                            "ใช้ --console ดู Traceback)"))
    if not g["closed"]:
        out.append(("FAIL", "ไม่มี market_closed แต่มีเกมถัดไปแล้ว = agent หยุดกลางเกมแล้วไม่กลับมา") if later_game
                   else ("WARN", "เกมยังไม่จบ (ไม่มี market_closed) ตรวจเฉพาะรอบที่ผ่านมาแล้ว"))

    # ของเหลือก่อนแต่ละรอบ (หักที่ขายได้จาก round_summary)
    sold_before: dict[str, float] = {}
    sold = 0.0
    sent: dict[str, list[dict]] = {}
    already: set[str] = set()
    summarized: set[str] = set()
    for e in evs:
        r = e.get("round")
        if e["event"] == "offers_sent":
            sold_before.setdefault(r, sold)
            sent.setdefault(r, []).append(e)
        elif e["event"] == "round_already_sent":
            already.add(r)
        elif e["event"] == "round_summary":
            summarized.add(r)
            sold += e.get("sold_kg") or 0

    expected = round_ids(g["cal"])
    seen = [r for r in expected if r in sent or r in already or r in summarized]
    if not g["closed"] and seen:                      # เกมยังไม่จบ: ตรวจถึงรอบล่าสุดที่เห็น
        expected = expected[:expected.index(seen[-1]) + 1]
    first = next((i for i, r in enumerate(expected) if r in sent or r in already), None)
    appraised = bool(by("appraisal_sent"))
    if first is None:
        out.append(("FAIL" if expected else "WARN", "ไม่พบ offers_sent เลยในเกมนี้"))
        return out, []
    if first > 0 and not appraised:
        out.append(("WARN", f"เข้าเกมกลางคัน เริ่มที่ {expected[first]} (ไม่นับ {', '.join(expected[:first])})"))
        expected = expected[first:]

    table = []
    missing, empty, dup, fallback = [], [], [], []
    for r in expected:
        rows = sent.get(r, [])
        if not rows:
            if r in already:
                table.append(f"| {r} | ส่งแล้วก่อนรีสตาร์ต (round_already_sent) | | | PASS |")
            else:
                missing.append(r)
                table.append(f"| {r} | — | — | — | **FAIL** ไม่มี offers_sent |")
            continue
        e = rows[-1]
        left = g["kg"] - sold_before.get(r, 0)
        note = "PASS"
        if (e.get("n") or 0) == 0:
            if left <= 0:
                note = "PASS ของหมดแล้ว"
            else:
                empty.append(r)
                note = f"**FAIL** ส่ง 0 ข้อเสนอ เหลือ ~{left:.0f} กก."
        if e.get("source") == "fallback":
            fallback.append(r)
            note = "**FAIL** fallback (strategy ล้ม)"
        if len(rows) > 1:
            dup.append(r)
            note += f" · ส่ง {len(rows)} ครั้ง"
        table.append(f"| {r} | {e.get('source')} | {e.get('n')} | {e.get('kg')} | {note} |")

    n_ok = len(expected) - len(missing) - len(empty)
    out.append(("FAIL" if missing or empty else "PASS",
                f"ทุกรอบมี offer ส่ง {n_ok}/{len(expected)} รอบ"
                + (f" — ไม่มี offers_sent: {', '.join(missing)}" if missing else "")
                + (f" — ส่ง 0 ทั้งที่มีของ: {', '.join(empty)}" if empty else "")))
    if fallback:
        out.append(("FAIL", f"ใช้ fallback (strategy ล้ม) รอบ {', '.join(fallback)}"))
    if dup:
        out.append(("WARN", f"ส่งรอบเดียวกันมากกว่า 1 ครั้ง ({', '.join(dup)}) agent 2 ตัวใช้ key เดียวกันหรือเปล่า"))
    rej = by("batch_rejected") + by("offer_rejected")
    if rej:
        out.append(("WARN", f"ตลาดปฏิเสธข้อเสนอ {len(rej)} ครั้ง: {str(rej[0].get('error'))[:120]}"))
    return out, table


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log-dir", default=os.getenv("LOG_DIR") or str(HERE / "logs-practice"))
    ap.add_argument("--last", type=int, help="ตรวจเฉพาะ N เกมล่าสุด")
    ap.add_argument("--game", help="ตรวจเฉพาะ game_id นี้")
    ap.add_argument("--console", nargs="*", default=[], help="ไฟล์ output ของ agent (หา Traceback)")
    ap.add_argument("--no-save", action="store_true", help="ไม่เขียนไฟล์ลง evidence/")
    a = ap.parse_args()

    path = Path(a.log_dir) / "decisions.jsonl"
    if not path.exists():
        sys.exit(f"ไม่พบ {path}")
    games = split_games(load(path))
    picked = [(i, g) for i, g in enumerate(games) if a.game is None or str(g["id"]) == str(a.game)]
    if a.last:
        picked = picked[-a.last:]
    if not picked:
        sys.exit("ไม่พบเกมที่เลือกใน log")

    lines = [f"# ตรวจ log: ไม่มี crash และทุกรอบมี offer ส่ง · {datetime.now().isoformat(timespec='seconds')}",
             f"- log `{path}` · {len(picked)} เกม · คำสั่ง `python check_log.py {' '.join(sys.argv[1:])}`", ""]
    fails = 0
    for i, g in picked:
        checks, table = check_game(g, later_game=i < len(games) - 1)
        worst = "FAIL" if any(c[0] == "FAIL" for c in checks) else "WARN" if any(c[0] == "WARN" for c in checks) else "PASS"
        fails += worst == "FAIL"
        head = f"## เกม {g['id']} ({g['market']}, เริ่ม {g['time']}, {g['kg']} กก.) — {worst}"
        print("\n" + head.replace("## ", ""))
        lines.append(head)
        for mark, text in checks:
            print(f"  [{mark}] {text}")
            lines.append(f"- **{mark}** {text}")
        if table:
            lines += ["", "| รอบ | source | ข้อเสนอ | กก. | ผล |", "|---|---|---|---|---|", *table]
        lines.append("")

    files = [f for pat in a.console for f in glob.glob(pat)]
    if files:
        lines.append("## output ของ agent (Traceback)")
        print()
        for f in files:
            text = Path(f).read_text(encoding="utf-8", errors="replace")
            n = text.count("Traceback (most recent call last)")
            fails += n > 0
            msg = f"`{f}` — " + (f"พบ Traceback {n} ครั้ง" if n else "ไม่พบ Traceback")
            print(f"  [{'FAIL' if n else 'PASS'}] {msg}")
            lines.append(f"- **{'FAIL' if n else 'PASS'}** {msg}")
        lines.append("")

    lines.append(f"## สรุป: {'ผ่าน' if not fails else f'ไม่ผ่าน ({fails} รายการ)'}")
    print(f"\n{'ผ่าน' if not fails else 'ไม่ผ่าน'}", end="")
    if not a.no_save:
        (HERE / "evidence").mkdir(exist_ok=True)
        out = HERE / "evidence" / f"log-check-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f" · หลักฐาน: {out}", end="")
    print()
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
