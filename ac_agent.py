"""ทดสอบ Acceptance Criteria ของ agent (AC ไฟล์ 1) ทีละข้อ แล้วเขียนหลักฐานลง evidence/ac-agent-<เวลา>.md

    python ac_agent.py --sim-results <ไฟล์ผล simulator>          ทุกข้อ (เกมหลัง 13:00 ของวันใน log)
    python ac_agent.py --since 12:30 --sim-results sim.csv       นับเกม "กติกาบ่าย" ตั้งแต่ 12:30
    python ac_agent.py --log-dir logs-practice                    อ่าน log เฉพาะโฟลเดอร์นี้ (ใส่ได้หลายครั้ง)

AC 1  ประเมินราคาตรงกับสูตรของตลาด
AC 2  ปรับราคาเองตามผลรอบก่อน และยื่นไม่เกิน capacity
AC 3  LLM ล่มแล้วยังส่งข้อเสนอได้ทุกรอบ
AC 4  ใน simulator ได้ PR สูงกว่า starter (0.965 เทียบ 0.892)
AC 5  เล่นตลาดฝึกกติกาบ่ายครบ 2 เกมโดยไม่ล้ม

ไม่ต่อเน็ต ไม่เขียน logs/ (ใช้ unit test, log ที่มีอยู่ และจำลองในเครื่อง) · exit 0 = ผ่านทุกข้อ

ไฟล์ผล simulator (--sim-results) ที่อ่านได้:
  CSV  มีคอลัมน์ชื่อ agent (agent/strategy/name/bot/policy) และคอลัมน์ PR (pr) หลายแถวต่อ agent = เฉลี่ย
  JSON {"ours": 0.965, "starter": 0.892} · {"ours": {"pr": ...}, ...} · [{"agent": "...", "pr": ...}, ...]
  agent ที่ชื่อมีคำว่า starter = starter · ที่เหลือ = ของเรา (เลือกเองด้วย --sim-ours)
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "tests"))
import helpers  # noqa: E402,F401  ต้องมาก่อน config: LOG_DIR ชั่วคราว + กติกา TV จาก tests/fixtures

import appraisal  # noqa: E402
import check_log  # noqa: E402
import config as cfg  # noqa: E402
import strategy  # noqa: E402
from dod_check import EVIDENCE, Item  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
EXPECT_OURS, EXPECT_STARTER = 0.965, 0.892
CAUTION = {"ABOVE_LIMIT": 4, "SPEC_MISMATCH": 3, "OUTBID": 2, "PARTIAL": 1, "ACCEPTED": 0}


def run_tests(names: list[str]) -> tuple[bool, str]:
    p = subprocess.run([sys.executable, "-m", "unittest", *names], cwd=HERE / "tests", capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=300)
    out = (p.stdout + p.stderr).strip()
    ran = re.search(r"Ran (\d+) tests?", out)
    ok = p.returncode == 0 and out.endswith("OK")
    return ok, (f"{ran.group(1)} tests " if ran else "") + ("OK" if ok else "ไม่ผ่าน: " + out.splitlines()[-1])


def load_games(dirs: list[Path]) -> list[dict]:
    games = []
    for d in dirs:
        path = d / "decisions.jsonl"
        if path.exists():
            for g in check_log.split_games(check_log.load(path)):
                g["dir"] = d.name
                games.append(g)
    return games


# ---------- AC 1 ----------

def ac1(dirs: list[Path]) -> Item:
    it = Item(1, "ประเมินราคาตรงกับสูตรของตลาด",
              "TV ที่ agent คิด (appraisal.py) ใช้สูตรเดียวกับตลาด: ใส่คุณภาพจริงที่ตลาดเปิดเผยหลังจบเกม (reveal) "
              "ต้องได้ TV และเกรดเท่ากับที่ตลาดประกาศ", kind="AC")
    ok, msg = run_tests(["test_appraisal"])
    it.check("PASS" if ok else "FAIL", "unit test สูตร TV (tests/test_appraisal.py)", msg)

    rows = []
    for d in dirs:
        f = d / "reveal_lots.csv"
        if f.exists():
            with open(f, encoding="utf-8") as fh:
                rows += [{**r, "_dir": d.name} for r in csv.DictReader(fh)]
    if not rows:
        it.check("FAIL", "มีข้อมูล reveal จากตลาดฝึกให้เทียบ", "ไม่พบ reveal_lots.csv (เล่นตลาดฝึกจนจบเกมก่อน)")
        return it
    diffs, bad_grade, bad = [], 0, []
    for r in rows:
        q = {"defect_pct": float(r["t_defect"]), "moisture_pct": float(r["t_moisture"]), "screen": int(float(r["t_screen"]))}
        mine = round(appraisal.true_value(helpers.RULES, q), 2)
        diff = abs(mine - float(r["tv_true"]))
        diffs.append(diff)
        if diff > 0.011:
            bad.append(f"{r['lot_id']} สูตร {mine} ตลาด {r['tv_true']}")
        bad_grade += appraisal.grade(helpers.RULES, q["defect_pct"])[0] != r["true_grade"]
    n = len(rows)
    it.check("PASS" if not bad else "FAIL", f"สูตรของเราได้ TV เท่ากับ TV จริงของตลาด {n - len(bad)}/{n} ล็อต",
             f"ต่างกันมากสุด {max(diffs):.3f} บาท/กก. (ยอม ≤ 0.01 จากการปัดเศษ)"
             + (" · ไม่ตรง: " + "; ".join(bad[:3]) if bad else ""))
    it.check("PASS" if not bad_grade else "FAIL", f"เกรดตรงกับตลาด {n - bad_grade}/{n} ล็อต", "")

    mapes = []
    for d in dirs:
        f = d / "experiments.csv"
        if f.exists():
            with open(f, encoding="utf-8") as fh:
                mapes += [float(r["mape"]) for r in csv.DictReader(fh) if r.get("mape")]
    if mapes:
        avg = sum(mapes) / len(mapes)
        it.check("PASS" if avg < 0.05 else "WARN", f"ค่าที่ส่งประเมินคลาดจาก TV จริงเฉลี่ย (MAPE) {avg:.2%} จาก {len(mapes)} เกม",
                 f"≈ คะแนนประเมิน {20 * max(0.0, 1 - avg / 0.10):.1f}/20 · คลาดเพราะผลวัดตัวอย่างต่างจากของจริง ไม่ใช่สูตร")
    it.info.append(f"กติกา TV จาก tests/fixtures/calendar.json · reveal {n} ล็อต จาก "
                   + ", ".join(sorted({r['_dir'] for r in rows})))
    return it


# ---------- AC 2 ----------

def ac2(games: list[dict]) -> Item:
    it = Item(2, "ปรับราคาเองตามผลรอบก่อน และยื่นไม่เกิน capacity",
              "หลังผลรอบก่อน agent ขยับ markup ต่อผู้ซื้อเอง (ACCEPTED ขึ้น · OUTBID ลง · ABOVE_LIMIT ลงและไม่กลับไปยื่น"
              " ≥ ราคาที่โดน − 0.02 อีก) และ กก. ที่ยื่นแต่ละรอบไม่เกินโควตาของรอบ", kind="AC")
    ok, msg = run_tests(["test_buyers", "test_pricing", "test_plan.Allocate", "test_plan.Guard"])
    it.check("PASS" if ok else "FAIL", "unit test การปรับราคาและโควตา (test_buyers, test_pricing, test_plan)", msg)

    over, n_sent, moves, above_bad, above_n = [], 0, defaultdict(Counter), [], 0
    for g in games:
        cap = g["cal"].get("capacity") if g["cal"] else None
        cap = cap if isinstance(cap, (int, float)) else None
        for e in g["events"]:
            if e["event"] != "offers_sent":
                continue
            n_sent += 1
            # budget_kg มาจาก state ของตลาด (capacity_left_kg) ตอนส่ง = ค่าจริง · capacity ใน calendar ของ log
            # อาจเก่า (main.py log calendar ตอนเปิด agent เท่านั้น) ใช้เมื่อไม่มี budget_kg
            limit = e.get("budget_kg") if e.get("budget_kg") is not None else cap
            if limit is not None and (e.get("kg") or 0) > limit:
                over.append(f"เกม {g['id']} {e['round']} ยื่น {e['kg']} > {limit}")
        # ผลรอบตามลำดับ: ผู้ซื้อ → (markup, ผลที่ระวังที่สุด)
        hist: dict[str, list[tuple[str, float, str]]] = defaultdict(list)
        for e in g["events"]:
            if e["event"] != "round_summary":
                continue
            per = defaultdict(list)
            for o in e.get("my_offers") or []:
                if o.get("markup") is not None:
                    per[o["buyer"]].append(o)
            for b, offs in per.items():
                worst = max(offs, key=lambda o: CAUTION.get(o["status"], 0))
                hist[b].append((e["round"], sum(o["markup"] for o in offs) / len(offs), worst["status"],
                                min((o["markup"] for o in offs if o["status"] == "ABOVE_LIMIT"), default=None)))
        for b, seq in hist.items():
            for (r0, m0, st, _), (r1, m1, _, _) in zip(seq, seq[1:]):
                moves[st]["ขึ้น" if m1 > m0 + 0.004 else "ลง" if m1 < m0 - 0.004 else "เท่าเดิม"] += 1
            ceiling = None
            for r, m, st, above_x in seq:
                if ceiling is not None:
                    above_n += 1
                    if m > ceiling + 0.006:
                        above_bad.append(f"เกม {g['id']} {b} {r} ยื่น {m:.3f} > เพดาน {ceiling:.3f}")
                if above_x is not None:
                    ceiling = min(ceiling if ceiling is not None else 9, above_x - cfg.CEILING_GAP)

    it.info.append(f"จาก {len(games)} เกม · offers_sent {n_sent} รอบ")
    it.check("PASS" if not over and n_sent else "FAIL" if over else "WARN",
             f"กก. ที่ยื่นไม่เกินโควตา {n_sent - len(over)}/{n_sent} รอบ", "; ".join(over[:3]) or "เทียบกับ budget_kg และ capacity")
    it.check("PASS" if not above_bad else "FAIL",
             f"หลัง ABOVE_LIMIT ไม่กลับไปยื่น ≥ ราคาที่โดน − {cfg.CEILING_GAP} ({above_n - len(above_bad)}/{above_n} ข้อเสนอถัดมา)",
             "; ".join(above_bad[:3]))
    changed = sum(c["ขึ้น"] + c["ลง"] for c in moves.values())
    total = sum(sum(c.values()) for c in moves.values())
    it.check("PASS" if changed else "FAIL", f"ปรับ markup ต่อผู้ซื้อเองระหว่างรอบ {changed}/{total} ครั้ง", "")
    table = ["| ผลรอบก่อน | รอบถัดไป ขึ้น | เท่าเดิม | ลง |", "|---|---|---|---|"]
    for st in ("ACCEPTED", "PARTIAL", "OUTBID", "ABOVE_LIMIT", "SPEC_MISMATCH"):
        c = moves.get(st)
        if c:
            table.append(f"| {st} | {c['ขึ้น']} | {c['เท่าเดิม']} | {c['ลง']} |")
    it.extra = ["markup ต่อผู้ซื้อ เทียบรอบก่อน → รอบถัดไป (ลงได้แม้ ACCEPTED เพราะความกดดัน/ช่วงตลาด):", "", *table]
    return it


# ---------- AC 3 ----------

class DownLLM:
    """gateway ล่ม: timeout (คืน None แบบ llm.py) หรือโยน exception"""

    def __init__(self, mode: str) -> None:
        self.mode, self.last_error, self.seconds, self.usage, self.calls = mode, None, 0.0, None, 0

    def ask_json(self, system, user, schema, timeout=None):
        self.calls += 1
        if self.mode == "raise":
            raise ConnectionError("gateway down (จำลอง)")
        self.last_error = "APITimeoutError: gateway down (จำลอง)"
        return None


def simulate(mode: str) -> list[tuple[str, int, int, str]]:
    from helpers import context, lot, rfq, state
    tv = {"L-1": 130.0, "L-2": 112.0, "L-3": 90.0}
    out = []
    for i, rid in enumerate(["P1"] + [f"R{k}" for k in range(1, 9)]):
        s = state([lot("L-1", 600, defect=1.0), lot("L-2", 600, defect=5.5), lot("L-3", 600, defect=10.0)],
                  [rfq("S1", "ดอยคราฟต์", "SPECIALTY", 300), rfq("C1", "ไทยคั่วบด", "COMMERCIAL", 600),
                   rfq("C2", "สยามคอมเมิร์ซ", "COMMERCIAL", 180), rfq("I1", "ซองดี", "INSTANT", 600)],
                  number=max(i, 1), kind="practice" if i == 0 else "real", capacity=420, round_capacity=420)
        s["round"]["round_id"] = rid
        ctx = context(tv, llm=DownLLM(mode), seed=i)
        try:                                          # เหมือน main.py: strategy พัง → fallback
            offers, source, _ = strategy.decide(s, ctx)
        except Exception:  # noqa: BLE001
            offers, source = strategy.fallback(s, ctx), "fallback"
        out.append((rid, len(offers), sum(o["kg"] for o in offers), source))
    return out


def ac3(games: list[dict]) -> Item:
    it = Item(3, "LLM ล่มแล้วยังส่งข้อเสนอได้ทุกรอบ",
              "ถ้า gateway LLM ไม่ตอบ/ช้า/error agent ใช้กฎแทนและยังส่งข้อเสนอครบทุกรอบ ไม่ข้ามรอบ", kind="AC")
    ok, msg = run_tests(["test_plan.Decide.test_llm_failure_falls_back_to_rules", "test_advisor.Consult.test_failure_and_no_time"])
    it.check("PASS" if ok else "FAIL", "unit test LLM ล้มแล้วใช้กฎ (test_plan, test_advisor)", msg)
    table = ["| แบบที่ล่ม | " + " | ".join(["P1"] + [f"R{k}" for k in range(1, 9)]) + " |", "|---" * 10 + "|"]
    for mode, label in (("timeout", "LLM timeout / ตอบเสีย"), ("raise", "LLM โยน exception")):
        res = simulate(mode)
        sent = sum(1 for _, n, _, _ in res if n > 0)
        srcs = Counter(src for *_, src in res)
        it.check("PASS" if sent == len(res) else "FAIL", f"จำลอง {label}: ส่งข้อเสนอได้ {sent}/{len(res)} รอบ",
                 " · ".join(f"{k} {v}" for k, v in srcs.items()))
        table.append(f"| {label} | " + " | ".join(f"{n} ข้อ/{kg} กก. ({src})" for _, n, kg, src in res) + " |")

    failed_rounds = [(g["id"], e) for g in games for e in g["events"]
                     if e["event"] == "offers_sent" and any("llm failed" in n for n in e.get("notes") or [])]
    if failed_rounds:
        # LLM ล้มแล้วกฎต้องตัดสินใจต่อ (source rules/probe) ไม่ใช่ fallback หรือข้ามรอบ
        ruled = [(gid, e) for gid, e in failed_rounds if e.get("source") in ("rules", "probe", "none")]
        sent = sum(1 for _, e in failed_rounds if (e.get("n") or 0) > 0)
        empty = [f"เกม {gid} {e['round']}" for gid, e in ruled if (e.get("n") or 0) == 0]
        it.check("PASS" if len(ruled) == len(failed_rounds) else "FAIL",
                 f"ตลาดจริง: รอบที่ LLM ล้ม ({len(failed_rounds)} รอบ) กฎตัดสินใจต่อได้ {len(ruled)}/{len(failed_rounds)} รอบ"
                 f" · ส่งข้อเสนอ {sent} รอบ",
                 "เกม " + ", ".join(sorted({str(gid) for gid, _ in failed_rounds})))
        if empty:
            it.check("WARN", "รอบที่กฎตัดสินใจแล้วไม่ยื่นเอง (กฎทำงาน แต่ไม่มีคู่ล็อต×ผู้ซื้อที่ยื่นได้ ดู notes ใน log)",
                     ", ".join(empty))
    else:
        it.check("WARN", "ตลาดจริง: ยังไม่มีรอบที่ LLM ล้มใน log ให้ดู", "ใช้ผลจำลองด้านบนแทน")
    it.extra = ["จำลอง 9 รอบด้วย strategy.decide จริง โดย LLM ล่มทุกรอบ:", "", *table]
    return it


# ---------- AC 4 ----------

def read_sim(path: Path) -> dict[str, float]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            return {k: float(v["pr"] if isinstance(v, dict) else v) for k, v in data.items()
                    if isinstance(v, (int, float)) or (isinstance(v, dict) and "pr" in v)}
        rows = data
    else:
        with open(path, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
    if not rows:
        return {}
    cols = {c.lower(): c for c in rows[0]}
    name_col = next((cols[c] for c in ("agent", "strategy", "name", "bot", "policy", "team") if c in cols), None)
    pr_col = next((cols[c] for c in cols if c == "pr" or c.startswith("pr")), None)
    if not name_col or not pr_col:
        raise ValueError(f"ไม่พบคอลัมน์ชื่อ agent/PR ในไฟล์ (มี {list(rows[0])})")
    acc = defaultdict(list)
    for r in rows:
        if str(r.get(pr_col, "")).strip():
            acc[str(r[name_col])].append(float(r[pr_col]))
    return {k: sum(v) / len(v) for k, v in acc.items()}


def ac4(sim: str | None, ours_name: str | None) -> Item:
    it = Item(4, f"ใน simulator ได้ PR สูงกว่า starter ({EXPECT_OURS} เทียบ {EXPECT_STARTER})",
              "อ่านผล simulator ที่ทีมรันไว้ แล้วเทียบ PR ของ agent เรากับ starter", kind="AC")
    if not sim:
        it.check("FAIL", "ระบุไฟล์ผล simulator", "ใส่ --sim-results <ไฟล์ .csv หรือ .json>")
        return it
    path = Path(sim)
    try:
        prs = read_sim(path)
    except (OSError, ValueError, KeyError, TypeError) as e:
        it.check("FAIL", f"อ่านไฟล์ {path.name}", str(e)[:150])
        return it
    starter = {k: v for k, v in prs.items() if "starter" in k.lower()}
    ours = {k: v for k, v in prs.items() if k not in starter and (ours_name is None or k == ours_name)}
    it.info.append(f"ไฟล์ {path} · PR: " + " · ".join(f"{k} {v:.3f}" for k, v in prs.items()))
    if not starter or not ours:
        it.check("FAIL", "พบผลของ starter และของเรา", f"starter {list(starter)} · ของเรา {list(ours)}")
        return it
    (s_name, s_pr), (o_name, o_pr) = next(iter(starter.items())), max(ours.items(), key=lambda kv: kv[1])
    it.check("PASS" if o_pr > s_pr else "FAIL", f"PR ของเรา ({o_name}) {o_pr:.3f} สูงกว่า starter ({s_name}) {s_pr:.3f}",
             f"ต่างกัน {o_pr - s_pr:+.3f}")
    near = abs(o_pr - EXPECT_OURS) <= 0.005 and abs(s_pr - EXPECT_STARTER) <= 0.005
    it.check("PASS" if near else "WARN", f"ตัวเลขตรงกับที่ระบุใน AC ({EXPECT_OURS} / {EXPECT_STARTER} ±0.005)",
             f"ได้ {o_pr:.3f} / {s_pr:.3f}")
    return it


# ---------- AC 5 ----------

def ac5(games: list[dict], since: str) -> Item:
    it = Item(5, "เล่นตลาดฝึกกติกาบ่ายครบ 2 เกมโดยไม่ล้ม",
              f"เกมตลาดฝึกที่เริ่มตั้งแต่ {since.replace('T', ' ')} อย่างน้อย 2 เกม เล่นจนตลาดปิด ไม่มี crash "
              "และส่งข้อเสนอครบ P1 + R1–R8", kind="AC")
    practice = [g for g in games if g["market"] == "practice"]
    table = ["| เกม | เริ่ม | เปิด agent | ผล | หมายเหตุ |", "|---|---|---|---|---|"]
    good = 0
    for i, g in enumerate(practice):
        later = any(x["time"] > g["time"] for x in practice)
        checks, _ = check_log.check_game(g, later_game=later)
        fails = [t for m, t in checks if m == "FAIL"]
        warns = [t for m, t in checks if m == "WARN"]
        done = g["closed"] and not fails
        good += done
        table.append(f"| {g['id']} | {g['time'][11:16]} | {g['starts']} ครั้ง | {'ครบ ไม่ล้ม' if done else 'ไม่ผ่าน'} "
                     f"| {'; '.join(fails or warns)[:140]} |")
    it.check("PASS" if good >= 2 else "FAIL", f"เกมที่เล่นครบโดยไม่ล้ม {good} เกม (ต้อง ≥ 2)",
             f"จาก {len(practice)} เกมตลาดฝึกหลัง {since[11:16]}")
    it.extra = ["เกมตลาดฝึกกติกาบ่าย (ตรวจด้วยเกณฑ์เดียวกับ check_log.py):", "", *table]
    return it


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log-dir", action="append", help="โฟลเดอร์ log (ค่าเริ่ม logs-practice และ logs)")
    ap.add_argument("--since", default="13:00", help="เกม 'กติกาบ่าย' เริ่มตั้งแต่ HH:MM (วันของเกมล่าสุด) หรือ ISO เต็ม")
    ap.add_argument("--sim-results", help="ไฟล์ผล simulator (.csv / .json)")
    ap.add_argument("--sim-ours", help="ชื่อ agent ของเราในไฟล์ simulator (ไม่ใส่ = ตัวที่ไม่ใช่ starter ที่ PR สูงสุด)")
    a = ap.parse_args()

    dirs = [Path(d) if Path(d).is_absolute() else HERE / d for d in (a.log_dir or ["logs-practice", "logs"])]
    games = load_games(dirs)
    if re.fullmatch(r"\d{1,2}:\d{2}", a.since):
        day = max((g["time"][:10] for g in games), default=datetime.now().strftime("%Y-%m-%d"))
        since = f"{day}T{int(a.since.split(':')[0]):02d}:{a.since.split(':')[1]}:00"
    else:
        since = a.since
    recent = [g for g in games if g["time"] >= since]

    print(f"ทดสอบ AC ของ agent · {datetime.now().isoformat(sep=' ', timespec='seconds')} · log "
          + ", ".join(d.name for d in dirs) + f" · เกมตั้งแต่ {since.replace('T', ' ')}: {len(recent)} เกม")
    items = [ac1(dirs), ac2(recent), ac3(recent), ac4(a.sim_results, a.sim_ours), ac5(recent, since)]
    for it in items:
        it.print()
        for line in it.extra:
            if line.startswith("|") and not line.startswith("|---"):
                print("   " + line)

    passed = sum(it.result == "PASS" for it in items)
    verdict = f"AC ผ่าน {passed}/{len(items)} ข้อ" + (" ✅" if passed == len(items) else " ❌")
    print(f"\n{'═' * 64}\nสรุป: {verdict}")
    for it in items:
        print(f"  ข้อที่ {it.no} {it.title:<55} {'✅ ผ่าน' if it.result == 'PASS' else '❌ ไม่ผ่าน'}")

    EVIDENCE.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    lines = [f"# ทดสอบ Acceptance Criteria ของ agent · {datetime.now().isoformat(sep=' ', timespec='seconds')}",
             f"คำสั่ง `python ac_agent.py {' '.join(sys.argv[1:])}` · เกมตั้งแต่ {since.replace('T', ' ')}", "",
             "| ข้อ | AC | ผล |", "|---|---|---|",
             *[f"| {it.no} | {it.title} | {'✅ ผ่าน' if it.result == 'PASS' else '❌ ไม่ผ่าน'} |" for it in items],
             f"| | **สรุป** | **{verdict}** |", ""]
    for it in items:
        lines += it.markdown()
    out = EVIDENCE / f"ac-agent-{stamp}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nหลักฐาน: {out}", flush=True)
    sys.exit(0 if passed == len(items) else 1)


if __name__ == "__main__":
    main()
