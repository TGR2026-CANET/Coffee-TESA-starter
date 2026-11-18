"""ทดสอบ Acceptance Criteria ของวันแข่ง (AC ไฟล์ 2) จาก logs/decisions.jsonl แล้วเขียนหลักฐานลง evidence/ac-gameday-<เวลา>.md

    python ac_gameday.py                                  เกมล่าสุดใน logs/ · .env · ทีม T04 · เปิด agent ภายใน 14:35
    python ac_gameday.py --list                           รายชื่อเกมใน log (เลือกด้วย --game)
    python ac_gameday.py --start-by 14:35 --team T04
    python ac_gameday.py --appraisal-start 14:40:00       เวลาเริ่มช่วงประเมินจริง (ไม่ใส่ = ดูจากลำดับ event)
    python ac_gameday.py --ssh pi@192.168.1.20            ตรวจ main.py บนเครื่องอื่นด้วย (ใส่ได้หลายครั้ง)
    python ac_gameday.py --offline                        ไม่เรียก GET /v1/results /v1/state (ใช้ผลที่ log บันทึกไว้)

AC 1  เปิด agent ภายใน 14:35 และเห็น new_game ใน log ก่อนช่วงประเมินราคาเริ่ม
AC 2  มี appraisal_sent 1 ครั้งในช่วงประเมินราคา และไม่มี appraisal_error
AC 3  มี offers_sent ครบ 9 รอบ (P1 + R1–R8)
AC 4  ไม่มี strategy_crashed และไม่มี batch_rejected ตลอดเกม
AC 5  ไม่มี state_error ติดกันเกิน 3 บรรทัด
AC 6  มีเครื่องเดียวที่รัน key T04 ตลอดเกม
AC 7  ไม่มีคำสั่งมือเลย: ทุกข้อเสนอในตลาดต้องมีแถว offers_sent ตรงกันใน log
AC 8  logs/decisions.jsonl ครบตั้งแต่ new_game ถึง market_closed
อ่านตลาดด้วย GET อย่างเดียว ไม่ส่ง แก้ หรือยกเลิกข้อเสนอ · ไม่แสดงค่า key · exit 0 = ผ่านทุกข้อ
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import check_log  # noqa: E402
from dod_audit import analyse, find_agents, market_results, okey, read_env  # noqa: E402
from dod_check import EVIDENCE, Item  # noqa: E402

ROUNDS = ["P1"] + [f"R{i}" for i in range(1, 9)]
MAX_STATE_ERROR_RUN = 3


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def first(g: dict, name: str) -> dict | None:
    return next((e for e in g["events"] if e["event"] == name), None)


# ---------- AC 1–5 ----------

def ac1(g: dict, start_by: str, appraisal_start: str | None) -> Item:
    it = Item(1, f"เปิด agent ภายใน {start_by} และเห็น new_game ก่อนช่วงประเมินราคาเริ่ม",
              f"เปิด agent ภายใน {start_by} และเห็น new_game ใน log ก่อนช่วงประเมินราคาเริ่ม", kind="AC")
    started = (g["cal"] or {}).get("time") or g["time"]
    deadline = f"{g['time'][:10]}T{start_by}:00"[:19]
    it.check("PASS" if started <= deadline else "FAIL", f"เปิด agent (event calendar) {started[11:19]} ≤ {start_by}",
             f"วันที่ {started[:10]}")
    appr = first(g, "appraisal_sent")
    if appraisal_start:
        limit = f"{g['time'][:10]}T{appraisal_start}"[:19]
        it.check("PASS" if g["time"] <= limit else "FAIL",
                 f"new_game {g['time'][11:19]} ก่อนช่วงประเมินเริ่ม {appraisal_start}", "")
    elif appr:
        # agent เห็นล็อตช่วง SETUP → log new_game แล้วรอจนช่วงประเมินเปิดจึงส่ง · เปิดช้า (ช่วงประเมินแล้ว) จะส่งทันทีในวินาทีเดียวกัน
        gap = (ts(appr["time"]) - ts(g["time"])).total_seconds()
        it.check("PASS" if gap >= 3 else "FAIL", f"new_game {g['time'][11:19]} ก่อนช่วงประเมินเริ่ม",
                 f"ห่างจาก appraisal_sent {gap:.0f} วิ (≥ 3 วิ = เห็นล็อตตั้งแต่ช่วง SETUP) · "
                 "ระบุเวลาจริงได้ด้วย --appraisal-start")
    else:
        it.check("FAIL", "new_game ก่อนช่วงประเมินเริ่ม", "ไม่มี appraisal_sent ให้เทียบ (ใส่ --appraisal-start)")
    return it


def ac2(g: dict) -> Item:
    it = Item(2, "มี appraisal_sent 1 ครั้งในช่วงประเมินราคา และไม่มี appraisal_error",
              "มี appraisal_sent 1 ครั้งในช่วงประเมินราคา (ก่อนรอบ P1) และไม่มี appraisal_error", kind="AC")
    sent = [e for e in g["events"] if e["event"] == "appraisal_sent"]
    errs = [e for e in g["events"] if e["event"] == "appraisal_error"]
    p1 = next((e["time"] for e in g["events"] if e["event"] == "offers_sent"), None)
    it.check("PASS" if len(sent) == 1 else "FAIL", f"appraisal_sent {len(sent)} ครั้ง (ต้อง 1)",
             f"เวลา {sent[0]['time'][11:19]} · {len(sent[0].get('tv') or {})} ล็อต" if sent else "")
    if sent and p1:
        it.check("PASS" if sent[0]["time"] <= p1 else "FAIL", "ส่งในช่วงประเมิน (ก่อนส่งข้อเสนอรอบแรก)",
                 f"ส่งข้อเสนอรอบแรก {p1[11:19]}")
    it.check("PASS" if not errs else "FAIL", f"appraisal_error {len(errs)} ครั้ง (ต้อง 0)",
             str(errs[0].get("error"))[:120] if errs else "")
    return it


def ac3(per: dict) -> Item:
    it = Item(3, "มี offers_sent ครบ 9 รอบ (P1 + R1–R8)", "มี offers_sent ครบ 9 รอบ (P1 + R1–R8)", kind="AC")
    have = [r for r in ROUNDS if per.get(r, {}).get("sent")]
    missing = [r for r in ROUNDS if r not in have]
    it.check("PASS" if not missing else "FAIL", f"offers_sent ครบ {len(have)}/9 รอบ",
             ("ขาด: " + ", ".join(missing)) if missing else "")
    empty = [r for r in have if (per[r]["sent"][-1].get("n") or 0) == 0]
    if empty:
        it.check("WARN", "รอบที่ offers_sent มี 0 ข้อเสนอ", ", ".join(empty))
    table = ["| รอบ | offers_sent | ข้อเสนอ | กก. | source | เวลา |", "|---|---|---|---|---|---|"]
    for r in ROUNDS:
        s = per.get(r, {}).get("sent") or []
        e = s[-1] if s else {}
        table.append(f"| {r} | {len(s)} | {e.get('n', '')} | {e.get('kg', '')} | {e.get('source', '')} "
                     f"| {e.get('time', '')[11:19]} |")
    it.extra = ["offers_sent ทีละรอบ:", "", *table]
    return it


def ac4(g: dict) -> Item:
    it = Item(4, "ไม่มี strategy_crashed และไม่มี batch_rejected ตลอดเกม",
              "ไม่มี strategy_crashed และไม่มี batch_rejected ตลอดเกม", kind="AC")
    for name in ("strategy_crashed", "batch_rejected"):
        hits = [e for e in g["events"] if e["event"] == name]
        it.check("PASS" if not hits else "FAIL", f"{name} {len(hits)} ครั้ง (ต้อง 0)",
                 f"ครั้งแรก {hits[0]['time'][11:19]}: {str(hits[0].get('error'))[:100]}" if hits else "")
    return it


def ac5(g: dict) -> Item:
    it = Item(5, f"ไม่มี state_error ติดกันเกิน {MAX_STATE_ERROR_RUN} บรรทัด",
              f"ไม่มี state_error ติดกันเกิน {MAX_STATE_ERROR_RUN} บรรทัด", kind="AC")
    runs, cur, start = [], 0, None
    for e in g["events"] + [{"event": "_end", "time": ""}]:
        if e["event"] == "state_error":
            cur, start = cur + 1, start or e["time"]
        else:
            if cur:
                runs.append((cur, start))
            cur, start = 0, None
    total = sum(n for n, _ in runs)
    worst = max(runs, default=(0, None))
    it.check("PASS" if worst[0] <= MAX_STATE_ERROR_RUN else "FAIL",
             f"state_error ติดกันยาวสุด {worst[0]} บรรทัด (ต้อง ≤ {MAX_STATE_ERROR_RUN})",
             f"ทั้งเกม {total} บรรทัด {len(runs)} ช่วง" + (f" · ช่วงยาวสุดเริ่ม {worst[1][11:19]}" if worst[1] else ""))
    return it


# ---------- AC 6–8 ----------

def team_of(g: dict, env: dict, offline: bool, log_dir: Path) -> tuple[str | None, str]:
    if not offline and env.get("MARKET_URL") and env.get("TEAM_KEY"):
        try:
            from market import Market
            return Market(env["MARKET_URL"], env["TEAM_KEY"]).state().get("team_id"), "GET /v1/state ด้วย TEAM_KEY ใน .env"
        except Exception:  # noqa: BLE001
            pass
    f = log_dir / "ranks.csv"
    if f.exists():
        with open(f, encoding="utf-8-sig") as fh:
            row = next((r for r in csv.DictReader(fh) if r["เวลา"] >= g["time"] and r["ตลาด"] == g["market"]), None)
        if row:
            return row["ทีม"], f"{f.name} ตอนจบเกม"
    return None, "ไม่พบ"


def ac6(g: dict, per: dict, team: str, env: dict, offline: bool, log_dir: Path, hosts: list[str],
        foreign: list[str]) -> Item:
    it = Item(6, f"มีเครื่องเดียวที่รัน key {team} ตลอดเกม", f"มีเครื่องเดียวที่รัน key {team} ตลอดเกม", kind="AC")
    tid, src = team_of(g, env, offline, log_dir)
    it.check("PASS" if tid == team else ("WARN" if tid is None else "FAIL"), f"key ใน .env และ log เป็นของทีม {team}",
             f"ได้ {tid or '?'} จาก {src} · ไม่แสดงค่า key")
    dup = [r for r in ROUNDS if len(per.get(r, {}).get("sent") or []) > 1]
    it.check("PASS" if not dup else "FAIL", "log: แต่ละรอบส่งครั้งเดียว (ไม่มี agent อีกตัวส่งซ้ำ)",
             ("ส่งซ้ำ: " + ", ".join(dup)) if dup else "")
    orphan = [r for r in ROUNDS if per.get(r, {}).get("already") and not per[r]["sent"]]
    it.check("PASS" if not orphan else "FAIL", "log: round_already_sent ทุกครั้งเป็นข้อเสนอที่เครื่องนี้ส่งเองก่อนรีสตาร์ต",
             ("ข้อเสนอเปิดอยู่แต่ log ไม่ได้ส่ง: " + ", ".join(orphan)) if orphan else
             f"รีสตาร์ต {g['starts'] - 1} ครั้ง")
    it.check("PASS" if not foreign else "FAIL", "ตลาด: ไม่มีข้อเสนอที่ไม่ได้มาจาก log เครื่องนี้ (ดู AC 7)",
             ", ".join(foreign))
    found, failed = find_agents(hosts)
    it.check("FAIL" if len(found) > 1 else "PASS", f"ตอนตรวจ: มี main.py รันอยู่ {len(found)} ตัว (ต้อง ≤ 1)",
             " | ".join(found) if found else "ไม่มี")
    if hosts:
        it.check("WARN" if failed else "PASS", f"ตรวจเครื่องอื่นด้วย pgrep ผ่าน ssh {len(hosts) - len(failed)}/{len(hosts)} เครื่อง",
                 ("ต่อไม่ได้: " + ", ".join(failed)) if failed else ", ".join(hosts))
    else:
        it.check("WARN", "ยังไม่ได้ตรวจเครื่องอื่นโดยตรง", "ใส่ --ssh user@host หรือรัน pgrep -fl main.py บน Pi/เครื่องเพื่อน")
    return it


def ac7(g: dict, per: dict, env: dict, offline: bool) -> tuple[Item, list[str]]:
    it = Item(7, "ไม่มีคำสั่งมือเลย: ทุกข้อเสนอในตลาดมีแถว offers_sent ตรงกันใน log",
              "ไม่มีคำสั่งมือเลย: ทุกข้อเสนอในตลาดต้องมีแถว offers_sent ตรงกันใน log", kind="AC")
    results, source = (None, "--offline") if offline else market_results(env, g, ROUNDS)
    if results is None:
        it.check("WARN", "ใช้ผลรอบจาก log (round_summary = my_offers ที่ตลาดประกาศ) แทน GET /v1/results", source)
        results = {r: (per[r]["summary"][0].get("my_offers") if per.get(r, {}).get("summary") else None) for r in ROUNDS}
        source = "round_summary ใน log"
    it.info.append(f"ข้อเสนอในตลาดจาก: {source} · เทียบ rfq_id, lot_id, กก., ราคา/กก.")
    table = ["| รอบ | ตลาดมี | log ส่ง | ไม่มีใน log | หมายเหตุ |", "|---|---|---|---|---|"]
    manual, foreign, unknown, total = [], [], [], 0
    for r in ROUNDS:
        got = results.get(r)
        logged = Counter(okey(o) for e in per.get(r, {}).get("sent") or [] for o in e.get("offers") or [])
        if got is None:
            unknown.append(r)
            table.append(f"| {r} | ? | {sum(logged.values())} | ? | ไม่มีผลรอบให้เทียบ |")
            continue
        market = Counter(okey(o) for o in got)
        extra = market - logged
        total += sum(market.values())
        if extra:
            manual.append(r)
            if not logged:
                foreign.append(r)
        note = "; ".join(f"{k[0]} {k[1]} {k[2]} กก. @{k[3]}" for k in list(extra)[:2])
        table.append(f"| {r} | {sum(market.values())} | {sum(logged.values())} | {sum(extra.values())} | {note} |")
    it.check("PASS" if not manual else "FAIL",
             f"ข้อเสนอในตลาด {total} รายการ มีแถว offers_sent ตรงกันทุกรายการ" if not manual
             else f"มีข้อเสนอในตลาดที่ไม่มีใน log รอบ {', '.join(manual)}", "")
    if unknown:
        it.check("WARN", "รอบที่ไม่มีผลให้เทียบ", ", ".join(unknown))
    it.extra = ["ข้อเสนอในตลาด เทียบกับ offers_sent:", "", *table]
    return it, foreign


def ac8(g: dict, path: Path, per: dict) -> Item:
    it = Item(8, "logs/decisions.jsonl ครบตั้งแต่ new_game ถึง market_closed",
              "logs/decisions.jsonl ครบตั้งแต่ new_game ถึง market_closed (ไม่มีบรรทัดเสีย ไม่มีช่วงที่ agent หายไป)", kind="AC")
    closed = first(g, "market_closed")
    it.check("PASS", f"มี new_game {g['time'][11:19]}", "")
    it.check("PASS" if closed else "FAIL", "มี market_closed" + (f" {closed['time'][11:19]}" if closed else ""),
             "" if closed else "เกมยังไม่จบ หรือ agent ไม่ได้อยู่ตอนตลาดปิด")
    start, end = (g["cal"] or {}).get("time") or g["time"], (closed or g["events"][-1] if g["events"] else {"time": "9"})["time"]
    bad, inside = 0, False
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            t = json.loads(line).get("time", "")
            inside = start <= t <= end
        except json.JSONDecodeError:
            bad += inside
    it.check("PASS" if not bad else "FAIL", f"บรรทัดที่อ่านเป็น JSON ไม่ได้ในช่วงเกม {bad} บรรทัด", "")
    times = [g["time"]] + [e["time"] for e in g["events"]]
    back = sum(1 for a, b in zip(times, times[1:]) if b < a)
    it.check("PASS" if not back else "FAIL", "เวลาเรียงจากต้นไปท้าย ไม่ย้อน", f"ย้อน {back} ครั้ง" if back else "")
    miss = [r for r in ROUNDS if not per.get(r, {}).get("summary")]
    it.check("PASS" if not miss else "FAIL", f"มีผลรอบ (round_summary) ครบ {9 - len(miss)}/9", ", ".join(miss))
    ends = sorted(per[r]["summary"][0]["time"] for r in ROUNDS if per.get(r, {}).get("summary"))
    step = statistics.median([(ts(b) - ts(a)).total_seconds() for a, b in zip(ends, ends[1:])]) if len(ends) > 2 else 300
    gaps = [((ts(b) - ts(a)).total_seconds(), a) for a, b in zip(times, times[1:])]
    gap, at = max(gaps, default=(0, None))
    limit = 1.5 * step + 30
    it.check("PASS" if gap <= limit else "FAIL", f"ไม่มีช่วงเงียบเกิน {limit:.0f} วิ (1.5 × ความยาวรอบ {step:.0f} วิ + 30)",
             f"ช่วงเงียบยาวสุด {gap:.0f} วิ" + (f" หลัง {at[11:19]}" if at else ""))
    return it


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log-dir", default="logs")
    ap.add_argument("--env", help="ไฟล์ .env (ค่าเริ่ม .env)")
    ap.add_argument("--game", help="game_id หรือ #ลำดับ (ดู --list) · ค่าเริ่ม เกมล่าสุด")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--team", default="T04")
    ap.add_argument("--start-by", default="14:35", help="ต้องเปิด agent ภายในเวลานี้ (HH:MM)")
    ap.add_argument("--appraisal-start", help="เวลาเริ่มช่วงประเมินราคาจริง HH:MM:SS (ถ้ารู้)")
    ap.add_argument("--ssh", action="append", default=[], help="user@host ที่จะตรวจ main.py (ใส่ได้หลายครั้ง)")
    ap.add_argument("--offline", action="store_true", help="ไม่เรียกตลาด")
    a = ap.parse_args()

    log_dir = Path(a.log_dir) if Path(a.log_dir).is_absolute() else HERE / a.log_dir
    path = log_dir / "decisions.jsonl"
    if not path.exists():
        sys.exit(f"ไม่พบ {path}")
    env_path = Path(a.env) if a.env else HERE / ".env"
    games = check_log.split_games(check_log.load(path))
    if a.list:
        for g in games:
            print(f"{g['id']!s:>5}  {g['market']:<11} เริ่ม {g['time'].replace('T', ' ')}  {g['kg']:>5} กก."
                  f"  {'จบ' if g['closed'] else 'ไม่จบ'}  เปิด agent {g['starts']} ครั้ง")
        return
    pick = [g for g in games if a.game is None or str(g["id"]) == a.game.strip()]
    if not pick:
        sys.exit(f"ไม่พบเกม {a.game} (ดู --list)")
    g = pick[-1]
    _, per = analyse(g)
    env = read_env(env_path)

    print(f"ทดสอบ AC วันแข่ง · {datetime.now().isoformat(sep=' ', timespec='seconds')}\n"
          f"เกม {g['id']} ({g['market']}) เริ่ม {g['time'].replace('T', ' ')} · {g['kg']} กก. · "
          f"{'จบแล้ว' if g['closed'] else 'ยังไม่จบ'} · เปิด agent {g['starts']} ครั้ง · log {log_dir.name}/{path.name}"
          f" · env {env_path.name}")
    it7, foreign = ac7(g, per, env, a.offline)
    items = [ac1(g, a.start_by, a.appraisal_start), ac2(g), ac3(per), ac4(g), ac5(g),
             ac6(g, per, a.team, env, a.offline, log_dir, a.ssh, foreign), it7, ac8(g, path, per)]
    for it in items:
        it.print()
        for line in it.extra:
            if line.startswith("|") and not line.startswith("|---"):
                print("   " + line)

    passed = sum(it.result == "PASS" for it in items)
    verdict = f"AC ผ่าน {passed}/{len(items)} ข้อ" + (" ✅" if passed == len(items) else " ❌")
    print(f"\n{'═' * 64}\nสรุป: {verdict}")
    for it in items:
        print(f"  ข้อที่ {it.no} {it.title:<62} {'✅ ผ่าน' if it.result == 'PASS' else '❌ ไม่ผ่าน'}")

    EVIDENCE.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    lines = [f"# ทดสอบ Acceptance Criteria วันแข่ง · {datetime.now().isoformat(sep=' ', timespec='seconds')}",
             f"คำสั่ง `python ac_gameday.py {' '.join(sys.argv[1:])}` · เกม {g['id']} ({g['market']}) "
             f"เริ่ม {g['time'].replace('T', ' ')} · log `{log_dir.name}/{path.name}` · env `{env_path.name}`", "",
             "| ข้อ | AC | ผล |", "|---|---|---|",
             *[f"| {it.no} | {it.title} | {'✅ ผ่าน' if it.result == 'PASS' else '❌ ไม่ผ่าน'} |" for it in items],
             f"| | **สรุป** | **{verdict}** |", ""]
    for it in items:
        lines += it.markdown()
    out = EVIDENCE / f"ac-gameday-{stamp}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nหลักฐาน: {out}", flush=True)
    sys.exit(0 if passed == len(items) else 1)


if __name__ == "__main__":
    main()
