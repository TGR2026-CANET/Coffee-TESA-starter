"""ตรวจ log ของ agent ตาม DoD 3 ข้อ แสดงว่าแต่ละข้อคืออะไร ผลเป็นยังไง แล้วเขียนหลักฐานลง evidence/audit-<เวลา>.md

    python dod_audit.py                              เกมล่าสุดใน logs/decisions.jsonl · อ่าน .env
    python dod_audit.py --log-dir logs-practice      ตลาดฝึก (อ่าน .env-practice ให้เอง)
    python dod_audit.py --list                       รายชื่อเกมใน log
    python dod_audit.py --game 85                    เลือกเกม (game_id หรือ #ลำดับ จาก --list)
    python dod_audit.py --ssh pi@192.168.1.20 --ssh friend@laptop    ตรวจ main.py บนเครื่องอื่นด้วย
    python dod_audit.py --offline                    ไม่เรียก GET /v1/results (เทียบกับผลที่ log บันทึกไว้แทน)
    python dod_audit.py --console "evidence/*-agent.log"             หา Traceback ใน output ของ agent ด้วย

ข้อ 1  นับ event ใน decisions.jsonl ทีละรอบ ครบทุกรอบ ไม่มีรอบไหนหาย
ข้อ 2  ค้นคำที่บอกปัญหาใน log ต้องไม่เจอหรือไม่เกินเกณฑ์ (WORDS) · state_error ทำให้รอบไหนไม่ได้ส่งไหม
ข้อ 3  my_offers จาก GET /v1/results?round=Rx ตรงกับ offers_sent ใน log ทุกรอบ
       · .env: LLM_BASE_URL / LLM_MODEL (ไม่แสดงค่า key) · ไม่มี main.py รันซ้อนบนเครื่องนี้/เครื่องอื่น
อ่านตลาดด้วย GET อย่างเดียว ไม่ส่ง แก้ หรือยกเลิกข้อเสนอ · exit 0 = ผ่านทุกข้อ
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import check_log  # noqa: E402
from dod_check import EVIDENCE, Item, sh  # noqa: E402

# (คำที่ค้นใน log, จำนวนที่ยอมได้ต่อเกม, ความหมาย) · ปรับเกณฑ์ได้ที่นี่
WORDS = [
    ("Traceback", 0, "Python error"),
    ("strategy_crashed", 0, "โค้ด strategy พัง รอบนั้นใช้ fallback"),
    ('"source": "fallback"', 0, "ข้อเสนอมาจาก fallback ไม่ใช่ strategy"),
    ("batch_rejected", 0, "ตลาดไม่รับข้อเสนอทั้งชุด"),
    ("offer_rejected", 0, "ตลาดไม่รับข้อเสนอบางรายการ"),
    ("appraisal_error", 0, "ส่งผลประเมิน TV ไม่สำเร็จ (ไม่นับ APPRAISAL_ALREADY_SUBMITTED หลังรีสตาร์ต)"),
    ("llm failed", 2, "LLM ตอบไม่ทัน/ตอบผิด รอบนั้นใช้กฎแทน"),
    ("state_error", 5, "ติดต่อตลาดไม่ได้ชั่วคราว"),
]
# LLM ที่ห้ามใช้วันแข่ง (นอก gateway ของงาน)
FORBIDDEN_LLM = ("openai.com", "anthropic.com", "googleapis.com", "groq.com", "together", "openrouter",
                 "mistral.ai", "deepseek", "localhost", "127.0.0.1", ":11434")


def read_env(path: Path) -> dict[str, str]:
    """อ่านแบบเดียวกับ config._load_env แต่ไม่ใส่ลง os.environ"""
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return out


def analyse(g: dict) -> tuple[list[str], dict[str, dict]]:
    expected = check_log.round_ids(g["cal"])
    per = {r: {"sent": [], "already": 0, "summary": []} for r in expected}
    for e in g["events"]:
        r = e.get("round")
        if r is None:
            continue
        p = per.setdefault(r, {"sent": [], "already": 0, "summary": []})
        if e["event"] == "offers_sent":
            p["sent"].append(e)
        elif e["event"] == "round_already_sent":
            p["already"] += 1
        elif e["event"] == "round_summary":
            p["summary"].append(e)
    if not g["closed"]:                                  # เกมยังไม่จบ: ตรวจถึงรอบล่าสุดที่เห็น
        seen = [r for r in expected if per[r]["sent"] or per[r]["already"] or per[r]["summary"]]
        if seen:
            expected = expected[:expected.index(seen[-1]) + 1]
    return expected, per


# ---------- ข้อ 1 ----------

def item1(g: dict, expected: list[str], per: dict) -> tuple[Item, list[str]]:
    it = Item(1, "นับ event ใน decisions.jsonl ครบทุกรอบ",
              "เปิด logs/decisions.jsonl แล้วนับ event ตามตัวเลข ดูว่าครบทุกรอบ ไม่มีรอบไหนหายไป "
              "(ทุกรอบ P1, R1–R8 ต้องมี offers_sent และ round_summary)")
    counts = Counter(e["event"] for e in g["events"])
    it.info.append("จำนวน event ทั้งเกม: " + " · ".join(f"{k} {v}" for k, v in counts.most_common()))
    missing_sent = [r for r in expected if not per[r]["sent"] and not per[r]["already"]]
    last_open = expected[-1] if expected and not g["closed"] else None
    missing_sum = [r for r in expected if not per[r]["summary"] and r != last_open]
    dup = [r for r in expected if len(per[r]["sent"]) > 1]
    empty = [r for r in expected if per[r]["sent"] and (per[r]["sent"][-1].get("n") or 0) == 0]

    n = len(expected)
    it.check("FAIL" if missing_sent or not n else "PASS", f"offers_sent ครบทุกรอบ {n - len(missing_sent)}/{n}",
             "หาย: " + ", ".join(missing_sent) if missing_sent else "")
    it.check("FAIL" if missing_sum else "PASS", f"round_summary ครบทุกรอบ {n - len(missing_sum)}/{n}"
             + (" (รอบล่าสุดยังไม่ประกาศผล)" if last_open else ""),
             "หาย: " + ", ".join(missing_sum) if missing_sum else "")
    it.check("PASS" if counts["appraisal_sent"] else "FAIL", f"appraisal_sent {counts['appraisal_sent']} ครั้ง",
             "" if counts["appraisal_sent"] else "ไม่ได้ส่งผลประเมิน = เสียคะแนนประเมิน 20")
    it.check("PASS" if g["closed"] else "WARN", f"market_closed {counts['market_closed']} ครั้ง",
             "" if g["closed"] else "เกมยังไม่จบ")
    if dup:
        it.check("WARN", "offers_sent รอบเดียวกันเกิน 1 ครั้ง", ", ".join(dup) + " (agent 2 ตัว หรือรีสตาร์ตกลางรอบ)")
    if empty:
        it.check("WARN", "รอบที่ส่ง 0 ข้อเสนอ", ", ".join(empty))

    table = ["| รอบ | offers_sent | ส่งก่อนรีสตาร์ต | round_summary | ข้อเสนอ | กก. | source | ผล |",
             "|---|---|---|---|---|---|---|---|"]
    for r in expected:
        p = per[r]
        e = p["sent"][-1] if p["sent"] else {}
        ok = (p["sent"] or p["already"]) and (p["summary"] or r == last_open)
        table.append(f"| {r} | {len(p['sent'])} | {p['already']} | {len(p['summary'])} | {e.get('n', '')} "
                     f"| {e.get('kg', '')} | {e.get('source', '')} | {'ครบ' if ok else 'หาย'} |")
    it.extra = ["นับ event ทีละรอบ:", "", *table]
    return it, missing_sent


# ---------- ข้อ 2 ----------

def item2(g: dict, expected: list[str], per: dict, missing: list[str], consoles: list[str]) -> Item:
    it = Item(2, "ค้นคำที่บอกปัญหาใน log ไม่เจอ/ไม่เกินเกณฑ์",
              "ค้นคำเหล่านี้ใน log ต้องไม่เจอ หรือเจอไม่เกินเกณฑ์ ถ้าเจอ state_error "
              "ให้ดูเวลาว่ามีรอบไหนที่ไม่ได้ส่งข้อเสนอเพราะเหตุนี้หรือไม่")
    lines = [json.dumps(e, ensure_ascii=False) for e in g["events"]]
    for word, limit, meaning in WORDS:
        hits = [l for l in lines if word in l]
        if word == "appraisal_error":
            hits = [l for l in hits if "APPRAISAL_ALREADY_SUBMITTED" not in l]
        it.check("PASS" if len(hits) <= limit else "FAIL", f'"{word}" เจอ {len(hits)} ครั้ง (เกณฑ์ ≤ {limit})',
                 meaning + (f" · ครั้งแรก {json.loads(hits[0])['time'][11:]}" if hits else ""))
    for f in consoles:
        n = Path(f).read_text(encoding="utf-8", errors="replace").count("Traceback (most recent call last)")
        it.check("FAIL" if n else "PASS", f"Traceback ใน {Path(f).name} เจอ {n} ครั้ง (เกณฑ์ 0)", "output ของ agent")

    # state_error ↔ รอบ: ช่วงเวลาของรอบ r = หลังผลรอบก่อนหน้า ถึงผลของรอบ r
    errs = [e["time"] for e in g["events"] if e["event"] == "state_error"]
    ends = {r: per[r]["summary"][0]["time"] for r in expected if per[r]["summary"]}
    begin, impact, notes = g["time"], [], []
    for i, r in enumerate(expected):
        end = ends.get(r) or next((ends[x] for x in expected[i + 1:] if x in ends), "9999")
        inside = [t for t in errs if begin < t <= end]
        if r in missing:
            if inside:
                impact.append(f"{r} ({len(inside)} ครั้ง {inside[0][11:]}–{inside[-1][11:]})")
            else:
                notes.append(f"{r} หายโดยไม่มี state_error (agent ไม่ได้รันช่วงนั้น?)")
        elif inside:
            notes.append(f"{r} มี state_error {len(inside)} ครั้ง แต่ยังส่งทัน")
        begin = end
    it.check("FAIL" if impact else "PASS", "state_error ไม่ทำให้รอบไหนไม่ได้ส่งข้อเสนอ",
             ("ไม่ได้ส่งเพราะ state_error: " + ", ".join(impact)) if impact
             else ("ไม่มี state_error" if not errs else f"state_error {len(errs)} ครั้ง ไม่กระทบการส่ง"))
    it.info += notes
    return it


# ---------- ข้อ 3 ----------

def okey(o: dict) -> tuple:
    return (o.get("rfq_id"), o.get("lot_id"), int(o.get("kg") or 0), round(float(o.get("price_per_kg") or 0), 2))


def market_results(env: dict, g: dict, rounds: list[str]) -> tuple[dict | None, str]:
    url, key = env.get("MARKET_URL"), env.get("TEAM_KEY")
    if not url or not key:
        return None, "ไม่มี MARKET_URL/TEAM_KEY ใน env"
    from market import Market
    api = Market(url, key)
    try:
        s = api.state()
    except Exception as e:  # noqa: BLE001
        return None, f"ต่อตลาดไม่ได้ ({str(e)[:80]})"
    if isinstance(g["key"], tuple):                      # ไม่มี game_id: เทียบชุดล็อตและ กก.
        lots = set(json.loads(g["key"][2] or "{}"))
        same = (s.get("market") == g["market"] and {l["lot_id"] for l in s.get("lots", [])} == lots
                and sum(l.get("kg_total", 0) for l in s.get("lots", [])) == g["kg"])
    else:
        same = s.get("game_id") == g["key"]
    if not same:
        return None, f"เกมใน log ไม่ใช่เกมปัจจุบันของตลาด (ตลาดอยู่ที่เกม {s.get('game_id') or '?'} {s.get('phase')})"
    out = {}
    for r in rounds:
        try:
            out[r] = api.call("GET", "/v1/results", params={"round": r}).get("my_offers") or []
        except Exception as e:  # noqa: BLE001
            out[r] = None
            print(f"   GET /v1/results?round={r} ไม่ได้: {str(e)[:80]}")
    return out, f"GET /v1/results?round=… จาก {urlparse(url).netloc}"


def find_agents(hosts: list[str]) -> tuple[list[str], list[str]]:
    """คืน (agent ที่พบ [เครื่อง: pid cmd], เครื่องที่ตรวจไม่ได้)"""
    found, failed = [], []
    me = os.getpid()
    if os.name == "nt":
        ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'main[.]py' } | "
              "ForEach-Object { \"$($_.ProcessId)`t$($_.ParentProcessId)`t$($_.CommandLine)\" }")
        code, out = sh("powershell", "-NoProfile", "-Command", ps)
        procs = [l.split("\t", 2) for l in out.splitlines() if l.count("\t") >= 2]
        procs = [p for p in procs if re.search(r"(^|[\s\\/\"])main\.py\b", p[2]) and int(p[0]) != me]
        by_pid = {p[0]: p for p in procs}
        for pid, ppid, cmd in procs:              # venv บน Windows = 2 process ต่อ agent (launcher + python จริง)
            if ppid in by_pid and by_pid[ppid][2].split()[-1] == cmd.split()[-1]:
                continue
            found.append(f"เครื่องนี้: PID {pid} {cmd.strip()[:90]}")
    else:
        code, out = sh("pgrep", "-af", "[m]ain\\.py")
        if code > 1:
            code, out = sh("pgrep", "-fl", "[m]ain\\.py")
        found += [f"เครื่องนี้: {l}" for l in out.splitlines() if l.strip() and not l.startswith(str(me))]
    for host in hosts:
        try:
            code, out = sh("ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host,
                           "pgrep -af '[m]ain\\.py' || pgrep -fl '[m]ain\\.py' || true", timeout=20)
        except subprocess.TimeoutExpired:
            code, out = 255, "timeout"
        if code != 0:
            failed.append(f"{host} ({out.splitlines()[-1][:60] if out else code})")
        else:
            found += [f"{host}: {l}" for l in out.splitlines() if l.strip()]
    return found, failed


def item3(g: dict, expected: list[str], per: dict, env_path: Path, offline: bool, hosts: list[str]) -> Item:
    it = Item(3, "ข้อเสนอตรงกับตลาด · LLM ถูกตัว · ไม่มี agent รันซ้อน",
              "เทียบ my_offers ในผลแต่ละรอบ (GET /v1/results?round=Rx) กับ offers_sent ใน log ให้ตรงกันทุกรอบ "
              "เปิด .env ดู LLM_BASE_URL / LLM_MODEL โดยไม่เปิดดูค่า key "
              "ตรวจว่าเครื่องอื่น (Pi, เครื่องเพื่อน) ไม่มี main.py รันอยู่ด้วย pgrep -fl main.py")
    env = read_env(env_path)

    # 3.1 เทียบข้อเสนอ
    results, source = (None, "--offline") if offline else market_results(env, g, expected)
    if results is None:
        it.check("WARN", "ใช้ผลรอบจาก log (round_summary) แทน GET /v1/results", source)
        results = {r: (per[r]["summary"][0].get("my_offers") if per[r]["summary"] else None) for r in expected}
        source = "round_summary ใน log"
    it.info.append(f"ผลรอบจาก: {source}")
    table = ["| รอบ | log ส่ง | ตลาดบันทึก | ตรงกัน | หมายเหตุ |", "|---|---|---|---|---|"]
    bad, foreign = [], []
    for r in expected:
        sent = per[r]["sent"][-1]["offers"] if per[r]["sent"] else []
        got = results.get(r)
        if got is None:
            table.append(f"| {r} | {len(sent)} | ? | ? | ไม่มีผลรอบให้เทียบ |")
            continue
        a, b = Counter(map(okey, sent)), Counter(map(okey, got))
        only_log, only_mkt = a - b, b - a
        note = []
        if only_log:
            note.append(f"log มีแต่ตลาดไม่มี {sum(only_log.values())} รายการ")
        if only_mkt:
            note.append(f"ตลาดมีแต่ log ไม่มี {sum(only_mkt.values())} รายการ")
            if not sent:
                foreign.append(r)
        if only_log or only_mkt:
            bad.append(r)
        table.append(f"| {r} | {len(sent)} | {len(got)} | {'ไม่ตรง' if note else 'ตรง'} | {' · '.join(note)} |")
    it.check("FAIL" if bad else "PASS", f"my_offers ตรงกับ offers_sent {len(expected) - len(bad)}/{len(expected)} รอบ",
             ("ไม่ตรง: " + ", ".join(bad)) if bad else "เทียบ rfq_id, lot_id, กก., ราคา/กก.")

    # 3.2 .env
    name = env_path.name
    if not env:
        it.check("FAIL", f"อ่าน {name}", "ไม่พบไฟล์หรือไฟล์ว่าง")
    else:
        use_llm = env.get("USE_LLM", "0").strip().lower() in ("1", "true", "yes")
        url, model = env.get("LLM_BASE_URL", ""), env.get("LLM_MODEL", "")
        same_key = bool(env.get("LLM_API_KEY")) and env.get("LLM_API_KEY") == env.get("TEAM_KEY")
        it.info.append(f"{name}: MARKET_URL {env.get('MARKET_URL', '-')} · USE_LLM {env.get('USE_LLM', '-')} · "
                       f"LLM_BASE_URL {url or '-'} · LLM_MODEL {model or '-'} · "
                       f"TEAM_KEY {'ตั้งแล้ว' if env.get('TEAM_KEY') else 'ว่าง'} · "
                       f"LLM_API_KEY {'ตั้งแล้ว' if env.get('LLM_API_KEY') else 'ว่าง'}"
                       f"{' (ค่าเดียวกับ TEAM_KEY)' if same_key else ''} · ไม่แสดงค่า key")
        comp = g["market"] == "competition"
        if use_llm:
            import config                                   # ใช้ตัวตัด /chat/completions ตัวเดียวกับ agent
            norm = config._llm_url(url)
            it.check("PASS" if norm.endswith("/v1") else "FAIL", "LLM_BASE_URL ลงท้าย /v1 (หลัง agent ตัด /chat/completions)",
                     norm or "ว่าง")
            bad_host = next((h for h in FORBIDDEN_LLM if h in url.lower()), None)
            if comp:
                it.check("FAIL" if bad_host else "PASS", "LLM_BASE_URL ไม่ใช่ LLM นอกงาน (ตลาดแข่ง)",
                         f"พบ {bad_host} = ผิดกติกา" if bad_host else urlparse(url).netloc)
                it.check("PASS" if model == "event-llm" else "FAIL", "LLM_MODEL = event-llm (gateway ของงาน)", model)
                it.check("PASS" if same_key else "WARN", "LLM_API_KEY ใช้ key เดียวกับ TEAM_KEY (ตามคู่มือ)",
                         "" if same_key else "ไม่ตรง ตรวจกับคู่มือของวันแข่ง")
            srcs = Counter(e.get("source") for r in expected for e in per[r]["sent"])
            it.check("PASS" if srcs.get("llm") else "WARN", "USE_LLM=1 และ log มีรอบที่ใช้ LLM จริง",
                     " · ".join(f"{k} {v}" for k, v in srcs.items()) + ("" if srcs.get("llm") else
                                                                        " (LLM ไม่ทำงาน ดู note llm failed)"))
        else:
            it.check("PASS", "USE_LLM=0 agent ใช้กฎล้วน ไม่ต่อ LLM", "ไม่ต้องตรวจ LLM_BASE_URL")

    # 3.3 agent ซ้อน
    found, failed = find_agents(hosts)
    it.check("FAIL" if len(found) > 1 else "PASS", f"มี main.py รันอยู่ {len(found)} ตัว (ต้องไม่เกิน 1)",
             " | ".join(found) if found else "ไม่มี agent รันอยู่บนเครื่องที่ตรวจ")
    if hosts:
        it.check("WARN" if failed else "PASS", f"ตรวจเครื่องอื่นด้วย ssh {len(hosts) - len(failed)}/{len(hosts)} เครื่อง",
                 ("ต่อไม่ได้: " + ", ".join(failed)) if failed else ", ".join(hosts))
    else:
        it.check("WARN", "ยังไม่ได้ตรวจเครื่องอื่นโดยตรง",
                 "ใส่ --ssh user@host หรือรัน pgrep -fl main.py บน Pi/เครื่องเพื่อน · ด้านล่างคือหลักฐานทางอ้อมจาก log")
    dup = [r for r in expected if len(per[r]["sent"]) > 1]
    it.check("FAIL" if foreign else ("WARN" if dup else "PASS"),
             "หลักฐานจาก log: ไม่มีข้อเสนอจากเครื่องอื่น",
             (f"ตลาดมีข้อเสนอรอบ {', '.join(foreign)} แต่ log นี้ไม่ได้ส่ง" if foreign else
              f"ส่งรอบเดียวกันซ้ำ: {', '.join(dup)}" if dup else "ข้อเสนอในตลาดทุกรายการมาจาก log นี้"))
    it.extra = ["เทียบข้อเสนอทีละรอบ:", "", *table]
    return it


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log-dir", default="logs")
    ap.add_argument("--env", help="ไฟล์ .env (ค่าเริ่ม: logs → .env · logs-practice → .env-practice)")
    ap.add_argument("--game", help="game_id หรือ #ลำดับ (ดู --list) · ค่าเริ่ม เกมล่าสุด")
    ap.add_argument("--list", action="store_true", help="แสดงรายชื่อเกมใน log แล้วจบ")
    ap.add_argument("--ssh", action="append", default=[], help="user@host ที่จะตรวจ main.py (ใส่ได้หลายครั้ง)")
    ap.add_argument("--offline", action="store_true", help="ไม่เรียกตลาด")
    ap.add_argument("--console", nargs="*", default=[], help="ไฟล์ output ของ agent ที่จะค้น Traceback")
    a = ap.parse_args()

    log_dir = Path(a.log_dir)
    log_dir = log_dir if log_dir.is_absolute() else HERE / log_dir
    env_path = Path(a.env) if a.env else HERE / (".env" + log_dir.name[len("logs"):] if log_dir.name.startswith("logs")
                                                 else ".env")
    path = log_dir / "decisions.jsonl"
    if not path.exists():
        sys.exit(f"ไม่พบ {path}")
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
    expected, per = analyse(g)
    consoles = [f for pat in a.console for f in glob.glob(pat)]

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    print(f"ตรวจ log ตาม DoD · {datetime.now().isoformat(sep=' ', timespec='seconds')}\n"
          f"เกม {g['id']} ({g['market']}) เริ่ม {g['time'].replace('T', ' ')} · {g['kg']} กก. · "
          f"{'จบแล้ว' if g['closed'] else 'ยังไม่จบ'} · log {path.relative_to(HERE) if path.is_relative_to(HERE) else path}"
          f" · env {env_path.name}")
    it1, missing = item1(g, expected, per)
    items = [it1, item2(g, expected, per, missing, consoles), item3(g, expected, per, env_path, a.offline, a.ssh)]
    for it in items:
        it.print()
        for line in it.extra:
            if line.startswith("|") and not line.startswith("|---"):
                print("   " + line)

    passed = sum(it.result == "PASS" for it in items)
    verdict = f"ผ่าน {passed}/{len(items)} ข้อ" + (" ✅" if passed == len(items) else " ❌")
    print(f"\n{'═' * 64}\nสรุป: {verdict}")
    for it in items:
        print(f"  ข้อที่ {it.no} {it.title:<48} {'✅ ผ่าน' if it.result == 'PASS' else '❌ ไม่ผ่าน'}")

    EVIDENCE.mkdir(exist_ok=True)
    lines = [f"# ตรวจ log ตาม DoD · {datetime.now().isoformat(sep=' ', timespec='seconds')}",
             f"คำสั่ง `python dod_audit.py {' '.join(sys.argv[1:])}` · เกม {g['id']} ({g['market']}) "
             f"เริ่ม {g['time'].replace('T', ' ')} · log `{path.name}` ใน `{log_dir.name}` · env `{env_path.name}`", "",
             "| ข้อ | DoD | ผล |", "|---|---|---|",
             *[f"| {it.no} | {it.title} | {'✅ ผ่าน' if it.result == 'PASS' else '❌ ไม่ผ่าน'} |" for it in items],
             f"| | **สรุป** | **{verdict}** |", ""]
    for it in items:
        lines += it.markdown()
    out = EVIDENCE / f"audit-{stamp}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nหลักฐาน: {out}", flush=True)
    sys.exit(0 if passed == len(items) else 1)


if __name__ == "__main__":
    main()
