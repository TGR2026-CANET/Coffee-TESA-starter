"""ตรวจ Definition of Done ทีละข้อ แสดงว่าแต่ละข้อคืออะไร ผลเป็นยังไง แล้วเขียนหลักฐานลง evidence/

    python dod_check.py                ข้อ 1 + ข้อ 2 จากเกมล่าสุดที่เล่นจบใน log (ไม่ต่อตลาด)
    python dod_check.py --play         ข้อ 1 + รัน agent เล่นตลาดฝึกใหม่ 1 เกมเต็ม แล้วตรวจเกมนั้น
        --partial-ok                   (กับ --play) ยอมเข้าเกมที่กำลังเล่นอยู่ ไม่รอเกมใหม่
        --max-minutes 60               (กับ --play) เกินเวลานี้ = ไม่ผ่าน
    python dod_check.py --game 85      ข้อ 2 ตรวจเกมที่ระบุ
    python dod_check.py --log-dir logs ข้อ 2 ตรวจ log อื่น (ค่าเริ่ม logs-practice)

DoD ข้อ 1: โค้ดอยู่ใน git และ unit test ผ่านหมด
DoD ข้อ 2: เล่นตลาดฝึกได้โดยไม่ล้ม (ไม่มี crash และทุกรอบมี offer ส่ง · รายละเอียดเกณฑ์ใน check_log.py)
ผล: evidence/dod-<เวลา>.md (+ dod-<เวลา>-agent.log ถ้า --play) · exit 0 = ผ่านทุกข้อ
--play ใช้ .env-practice เสมอ ถ้าตลาดเป็น competition จะหยุดทันที ไม่รัน agent
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("ENV_FILE", str(HERE / ".env-practice"))
EVIDENCE = HERE / "evidence"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import check_log  # noqa: E402

LABEL = {"PASS": "ผ่าน", "FAIL": "ไม่ผ่าน", "WARN": "เตือน"}


def sh(*cmd: str, timeout: float = 60) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


class Item:
    """DoD หนึ่งข้อ: ชื่อ เกณฑ์ย่อย (ผ่าน/ไม่ผ่าน/เตือน) และรายละเอียดเพิ่ม"""

    def __init__(self, no: int, title: str, meaning: str, kind: str = "DoD") -> None:
        self.no, self.title, self.meaning, self.kind = no, title, meaning, kind
        self.checks: list[tuple[str, str, str]] = []
        self.extra: list[str] = []      # บรรทัด markdown เพิ่มเติม (ตาราง ผล test)
        self.info: list[str] = []

    def check(self, mark: str, label: str, detail: str = "") -> None:
        self.checks.append((mark, label, detail))

    @property
    def result(self) -> str:
        return "FAIL" if any(m == "FAIL" for m, _, _ in self.checks) or not self.checks else "PASS"

    def print(self) -> None:
        bar = "═" * 64
        print(f"\n{bar}\n{self.kind} ข้อที่ {self.no}: {self.title}\n  หมายถึง: {self.meaning}\n{bar}")
        for line in self.info:
            print(f"  {line}")
        for i, (m, label, detail) in enumerate(self.checks, 1):
            print(f"  {self.no}.{i} [{LABEL[m]:^7}] {label}" + (f"\n             → {detail}" if detail else ""))
        print(f"  ผลข้อที่ {self.no}: {'✅ ผ่าน' if self.result == 'PASS' else '❌ ไม่ผ่าน'}", flush=True)

    def markdown(self) -> list[str]:
        out = [f"## {self.kind} ข้อที่ {self.no}: {self.title} — {'✅ ผ่าน' if self.result == 'PASS' else '❌ ไม่ผ่าน'}",
               f"_หมายถึง: {self.meaning}_", ""]
        out += [f"- {line}" for line in self.info]
        out += ["", "| ข้อย่อย | เกณฑ์ | ผล | รายละเอียด |", "|---|---|---|---|"]
        out += [f"| {self.no}.{i} | {label} | {LABEL[m]} | {detail} |"
                for i, (m, label, detail) in enumerate(self.checks, 1)]
        return out + ([""] + self.extra if self.extra else []) + [""]


# ---------- DoD ข้อ 1 ----------

def dod1() -> Item:
    it = Item(1, "โค้ดอยู่ใน git และ unit test ผ่านหมด",
              "ทุกไฟล์ commit แล้ว push ขึ้น GitHub แล้ว และ python -m unittest ผ่านทุกตัว")
    _, branch = sh("git", "rev-parse", "--abbrev-ref", "HEAD")
    _, head = sh("git", "log", "-1", "--format=%h %ad %s", "--date=format:%Y-%m-%d %H:%M")
    it.info.append(f"branch {branch} · commit ล่าสุด {head}")

    _, status = sh("git", "status", "--porcelain")
    files = [l.split(None, 1)[1] for l in status.splitlines() if l.strip()]   # "XY path" (sh() ตัดช่องว่างหน้าแล้ว)
    dirty = [f for f in files if not f.startswith("evidence/")]
    it.check("FAIL" if dirty else "PASS", "ทุกไฟล์ commit แล้ว (working tree สะอาด)",
             "ยังไม่ commit: " + ", ".join(dirty) if dirty else "ไม่มีไฟล์ค้าง")

    code, upstream = sh("git", "rev-parse", "--abbrev-ref", "@{u}")
    _, local = sh("git", "rev-parse", "HEAD")
    if code != 0:
        it.check("FAIL", "push ขึ้น remote แล้ว", f"branch ยังไม่มี upstream (git push -u origin {branch})")
    else:
        code, remote = sh("git", "ls-remote", "origin", f"refs/heads/{branch}", timeout=30)
        if code == 0 and remote:
            same = remote.split()[0] == local
            it.check("PASS" if same else "FAIL", f"push ขึ้น origin/{branch} แล้ว",
                     f"GitHub {remote.split()[0][:7]} · เครื่องนี้ {local[:7]}" + ("" if same else " (ยังไม่ตรง ต้อง push)"))
        else:
            _, ahead = sh("git", "rev-list", "--count", f"{upstream}..HEAD")
            it.check("PASS" if ahead == "0" else "FAIL", f"push ขึ้น {upstream} แล้ว",
                     f"ต่อ GitHub ไม่ได้ เช็กจาก ref ในเครื่อง: นำหน้า {ahead} commit")

    code, out = sh(sys.executable, "-m", "unittest", "discover", "-s", "tests", timeout=600)
    ran = re.search(r"Ran (\d+) tests? in ([\d.]+)s", out)
    failed = re.search(r"FAILED \((.*)\)", out)
    ok = code == 0 and out.rstrip().endswith("OK")
    it.check("PASS" if ok else "FAIL", "unit test ผ่านหมด (python -m unittest discover -s tests)",
             (f"{ran.group(1)} tests ใน {ran.group(2)} วิ" if ran else "")
             + (f" · {failed.group(1)}" if failed else "") + (" · OK" if ok else ""))
    it.extra = ["ผล unit test:", "```", out if not ok else "\n".join(out.splitlines()[-3:]), "```"]
    return it


# ---------- DoD ข้อ 2 ----------

def wait_for_game(partial_ok: bool, deadline: float) -> dict:
    import config as cfg
    from market import Market
    if cfg.DRY_RUN:
        sys.exit("DRY_RUN=1 อยู่ ตั้งเป็น 0 ก่อน (DoD ต้องส่งจริงในตลาดฝึก)")
    api = Market(cfg.MARKET_URL, cfg.TEAM_KEY)
    last = None
    while time.time() < deadline:
        try:
            s = api.state()
        except Exception as e:  # noqa: BLE001
            print("state error:", e, flush=True)
            time.sleep(5)
            continue
        if s.get("market") == "competition":
            sys.exit("ENV_FILE ชี้ตลาดแข่ง (competition) · DoD ใช้ตลาดฝึกเท่านั้น หยุด")
        phase = s["phase"]
        if phase in ("WAITING", "SETUP", "APPRAISAL") or (partial_ok and phase != "CLOSED"):
            return s
        if phase != last:
            rnd = (s.get("round") or {}).get("round_id")
            print(f"ตลาดอยู่ที่ {phase} {rnd or ''} · รอเกมใหม่ให้ได้เล่นครบตั้งแต่ประเมิน (--partial-ok เพื่อเข้าเลย)",
                  flush=True)
            last = phase
        time.sleep(min(10.0, max(2.0, (s.get("seconds_left") or 5) + 1)))
    sys.exit("รอเกมใหม่เกินเวลา")


def run_agent(log_path: Path, deadline: float) -> tuple[int | None, int]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    env.pop("DRY_RUN", None)
    tracebacks = 0
    with open(log_path, "w", encoding="utf-8") as f:
        p = subprocess.Popen([sys.executable, "main.py"], cwd=HERE, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        assert p.stdout is not None
        for line in p.stdout:
            print(line, end="", flush=True)
            f.write(line)
            f.flush()
            tracebacks += line.startswith("Traceback")
            if time.time() > deadline:
                p.kill()
                f.write("\n[dod_check] เกินเวลา หยุด agent\n")
                return None, tracebacks
        return p.wait(), tracebacks


def dod2(log_dir: Path, game_id: str | None, play: bool, partial_ok: bool, max_minutes: float, stamp: str) -> Item:
    it = Item(2, "เล่นตลาดฝึกได้โดยไม่ล้ม",
              "agent เล่นตลาดฝึกเองครบ 1 เกมจนตลาดปิด ไม่มี crash และส่งข้อเสนอทุกรอบ")
    start = None
    if play:
        deadline = time.time() + max_minutes * 60
        s = wait_for_game(partial_ok, deadline)
        start = datetime.now().isoformat(timespec="seconds")
        log_path = EVIDENCE / f"dod-{stamp}-agent.log"
        code, tracebacks = run_agent(log_path, deadline)
        it.info.append(f"รัน agent ใหม่ (main.py) เริ่มที่ phase {s['phase']} · output ทั้งเกม: evidence/{log_path.name}")
        it.check("PASS" if code == 0 else "FAIL", "agent จบเองเมื่อตลาดปิด ไม่ล้ม (exit code 0)",
                 "เกินเวลา" if code is None else f"exit code {code}")
        it.check("FAIL" if tracebacks else "PASS", "ไม่มี Traceback (error ของ Python) ใน output",
                 f"พบ {tracebacks} ครั้ง" if tracebacks else "ไม่พบ")

    path = log_dir / "decisions.jsonl"
    if not path.exists():
        it.check("FAIL", "มี log ของ agent", f"ไม่พบ {path}")
        return it
    games = check_log.split_games(check_log.load(path))
    idx = [i for i, g in enumerate(games)
           if (game_id is None or str(g["id"]) == str(game_id)) and (start is None or g["time"] >= start
                                                                       or i == len(games) - 1)]
    if game_id is None and not play:
        idx = [i for i in idx if games[i]["closed"]]       # ไม่ระบุเกม: เกมล่าสุดที่เล่นจบแล้ว
    if not idx:
        it.check("FAIL", "พบเกมที่จะตรวจใน log", f"ไม่พบเกม{' ' + game_id if game_id else 'ที่เล่นจบ'} ใน {path}")
        return it
    i = idx[-1]
    g = games[i]
    it.info.append(f"ตรวจเกม {g['id']} ({g['market']}) เริ่ม {g['time'].replace('T', ' ')} · ของ {g['kg']} กก. "
                   f"· log {path.relative_to(HERE) if path.is_relative_to(HERE) else path}")
    checks, table = check_log.check_game(g, later_game=i < len(games) - 1)
    rename = {"ไม่มี strategy_crashed": "strategy ไม่ crash (ไม่มี strategy_crashed)"}
    for mark, text in checks:
        label, _, detail = text.partition(" — ")
        it.check(mark, rename.get(label, label), detail)
    if g["closed"]:
        it.check("PASS", "เล่นจนตลาดปิด (market_closed)", "")
    appraised = any(e["event"] == "appraisal_sent" for e in g["events"])
    it.check("PASS" if appraised else "WARN", "ส่งผลประเมิน TV ช่วง APPRAISAL", "" if appraised else "ไม่ได้ส่ง")
    if table:
        it.extra = ["ผลรายรอบ:", "", "| รอบ | source | ข้อเสนอ | กก. | ผล |", "|---|---|---|---|---|", *table]
    return it


def print_table(it: Item) -> None:
    rows = [r for r in it.extra if r.startswith("| ") and not r.startswith("| รอบ")]
    if not rows:
        return
    print(f"  ผลรายรอบ:  {'รอบ':<4} {'source':<8} {'ข้อเสนอ':>7} {'กก.':>6}  ผล")
    for r in rows:
        c = [x.strip().replace("**", "") for x in r.strip("|").split("|")]
        print(f"             {c[0]:<4} {c[1]:<8} {c[2]:>7} {c[3]:>6}  {c[4]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--play", action="store_true", help="รัน agent เล่นตลาดฝึกใหม่ 1 เกม")
    ap.add_argument("--partial-ok", action="store_true", help="(กับ --play) ยอมเข้าเกมที่กำลังเล่นอยู่")
    ap.add_argument("--max-minutes", type=float, default=60)
    ap.add_argument("--game", help="ข้อ 2 ตรวจ game_id นี้")
    ap.add_argument("--log-dir", default=None, help="ข้อ 2 อ่าน log จากโฟลเดอร์นี้ (ค่าเริ่ม logs-practice)")
    a = ap.parse_args()
    log_dir = Path(a.log_dir or os.getenv("LOG_DIR") or HERE / "logs-practice")
    if a.play:
        os.environ["LOG_DIR"] = str(log_dir)

    EVIDENCE.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    print(f"ตรวจ Definition of Done · {datetime.now().isoformat(sep=' ', timespec='seconds')}")
    items = [dod1()]
    items[0].print()
    items.append(dod2(log_dir, a.game, a.play, a.partial_ok, a.max_minutes, stamp))
    items[1].print()
    print_table(items[1])

    passed = sum(it.result == "PASS" for it in items)
    verdict = f"DoD ผ่าน {passed}/{len(items)} ข้อ" + (" ✅" if passed == len(items) else " ❌")
    print(f"\n{'═' * 64}\nสรุป: {verdict}")
    for it in items:
        print(f"  ข้อที่ {it.no} {it.title:<40} {'✅ ผ่าน' if it.result == 'PASS' else '❌ ไม่ผ่าน'}")

    lines = [f"# หลักฐาน Definition of Done · {datetime.now().isoformat(sep=' ', timespec='seconds')}",
             f"คำสั่ง `python dod_check.py {' '.join(sys.argv[1:])}` · Python {sys.version.split()[0]}", "",
             "| ข้อ | DoD | ผล |", "|---|---|---|",
             *[f"| {it.no} | {it.title} | {'✅ ผ่าน' if it.result == 'PASS' else '❌ ไม่ผ่าน'} |" for it in items],
             f"| | **สรุป** | **{verdict}** |", ""]
    for it in items:
        lines += it.markdown()
    out = EVIDENCE / f"dod-{stamp}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nหลักฐาน: {out}", flush=True)
    sys.exit(0 if passed == len(items) else 1)


if __name__ == "__main__":
    main()
