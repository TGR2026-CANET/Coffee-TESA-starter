"""ตรวจ Definition of Done แล้วเขียนหลักฐานลง evidence/

    python dod_check.py            DoD 1: โค้ดอยู่ใน git (สะอาด + push แล้ว) และ unit test ผ่านหมด
    python dod_check.py --play     + DoD 2: รัน agent เล่นตลาดฝึก 1 เกมเต็ม (ประเมิน → P1 → R1–R8 → ปิด) โดยไม่ล้ม
        --partial-ok               ยอมเข้าเกมที่กำลังเล่นอยู่ (ไม่รอเกมใหม่)
        --max-minutes 60           เกินเวลานี้ = ไม่ผ่าน

ใช้ .env-practice และ logs-practice/ เสมอ (ตั้ง ENV_FILE / LOG_DIR เองได้) · ถ้าตลาดเป็น competition จะหยุดทันที ไม่รัน agent
ผล: evidence/dod-<เวลา>.md (สรุป) และ evidence/dod-<เวลา>-agent.log (output ของ agent ทั้งเกม) · exit 0 = ผ่านทุกข้อ
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("ENV_FILE", str(HERE / ".env-practice"))
os.environ.setdefault("LOG_DIR", str(HERE / "logs-practice"))
EVIDENCE = HERE / "evidence"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def sh(*cmd: str, timeout: float = 60) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


class Report:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.ok = True

    def check(self, passed: bool | None, label: str, detail: str = "") -> None:
        """passed: True ผ่าน · False ไม่ผ่าน · None แค่เตือน"""
        mark = {True: "PASS", False: "FAIL", None: "WARN"}[passed]
        if passed is False:
            self.ok = False
        self.lines.append(f"- **{mark}** {label}" + (f" — {detail}" if detail else ""))
        print(f"[{mark}] {label}" + (f" — {detail}" if detail else ""), flush=True)

    def add(self, text: str = "") -> None:
        self.lines.append(text)


# ---------- DoD 1: git + tests ----------

def check_git(r: Report) -> None:
    r.add("## DoD 1a: โค้ดอยู่ใน git")
    _, branch = sh("git", "rev-parse", "--abbrev-ref", "HEAD")
    _, head = sh("git", "log", "-1", "--format=%H %ad %s", "--date=iso")
    r.add(f"- branch `{branch}` · HEAD `{head}`")
    _, status = sh("git", "status", "--porcelain")
    dirty = [l for l in status.splitlines() if l.strip() and not l[3:].startswith("evidence/")]
    r.check(not dirty, "working tree สะอาด (ทุกไฟล์ที่ track อยู่ commit แล้ว)",
            "ค้าง: " + ", ".join(l[3:] for l in dirty) if dirty else "")
    code, upstream = sh("git", "rev-parse", "--abbrev-ref", "@{u}")
    if code != 0:
        r.check(False, "branch มี upstream บน remote", "ยังไม่ได้ push (git push -u origin " + branch + ")")
        return
    code, remote = sh("git", "ls-remote", "origin", f"refs/heads/{branch}", timeout=30)
    _, local = sh("git", "rev-parse", "HEAD")
    if code == 0 and remote:
        r.check(remote.split()[0] == local, f"HEAD อยู่บน origin/{branch} แล้ว (เช็กกับ remote จริง)",
                f"remote {remote.split()[0][:7]} · local {local[:7]}")
    else:
        _, ahead = sh("git", "rev-list", "--count", f"{upstream}..HEAD")
        r.check(ahead == "0", f"HEAD ไม่นำหน้า {upstream} (ต่อ remote ไม่ได้ เช็กจาก ref ในเครื่อง)", f"นำหน้า {ahead} commit")


def check_tests(r: Report) -> None:
    r.add("")
    r.add("## DoD 1b: unit test")
    code, out = sh(sys.executable, "-m", "unittest", "discover", "-s", "tests", timeout=600)
    ran = re.search(r"Ran (\d+) tests? in ([\d.]+)s", out)
    tail = "\n".join(out.splitlines()[-4:])
    r.check(code == 0 and out.rstrip().endswith("OK"), "python -m unittest discover -s tests",
            f"{ran.group(1)} tests {ran.group(2)}s" if ran else "")
    r.add("```\n" + (out if code else tail) + "\n```")


# ---------- DoD 2: เล่นตลาดฝึก ----------

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


def run_agent(log_path: Path, deadline: float) -> tuple[int | None, bool]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    env.pop("DRY_RUN", None)
    traceback = False
    with open(log_path, "w", encoding="utf-8") as f:
        p = subprocess.Popen([sys.executable, "main.py"], cwd=HERE, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        assert p.stdout is not None
        for line in p.stdout:
            print(line, end="", flush=True)
            f.write(line)
            f.flush()
            traceback |= line.startswith("Traceback")
            if time.time() > deadline:
                p.kill()
                f.write("\n[dod_check] เกินเวลา หยุด agent\n")
                return None, traceback
        return p.wait(), traceback


def check_play(r: Report, stamp: str, partial_ok: bool, max_minutes: float) -> None:
    r.add("")
    r.add("## DoD 2: เล่นตลาดฝึกได้โดยไม่ล้ม")
    deadline = time.time() + max_minutes * 60
    s = wait_for_game(partial_ok, deadline)
    r.add(f"- ตลาด `{s.get('market')}` · เริ่มที่ phase `{s['phase']}` · ENV_FILE `{Path(os.environ['ENV_FILE']).name}`"
          f" · LOG_DIR `{Path(os.environ['LOG_DIR']).name}`")
    start = datetime.now().isoformat(timespec="seconds")
    log_path = EVIDENCE / f"dod-{stamp}-agent.log"
    code, traceback = run_agent(log_path, deadline)
    r.add(f"- output ของ agent ทั้งเกม: `evidence/{log_path.name}`")

    rows = []
    with open(Path(os.environ["LOG_DIR"]) / "decisions.jsonl", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("time", "") >= start:
                rows.append(row)
    ev = lambda name: [x for x in rows if x["event"] == name]  # noqa: E731

    r.check(code == 0, "agent จบเองเมื่อตลาดปิด (exit code 0)", "เกินเวลา" if code is None else f"exit {code}")
    r.check(not traceback, "ไม่มี Traceback ใน output")
    crashed = ev("strategy_crashed")
    r.check(not crashed, "strategy ไม่ crash (ไม่ต้องพึ่ง fallback)", f"{len(crashed)} ครั้ง" if crashed else "")
    closed = ev("market_closed")
    r.check(bool(closed) and bool(closed[-1].get("played")), "เล่นจนตลาดปิด (market_closed, played=true)")
    appr = ev("appraisal_sent")
    r.check(True if appr else (None if partial_ok else False), "ส่งผลประเมิน TV ช่วง APPRAISAL",
            f"{len(appr[-1].get('tv', {}))} ล็อต" if appr else "ไม่ได้ส่ง")
    sent = ev("offers_sent")
    cal = (ev("calendar") or [{}])[-1]
    expected = (cal.get("rounds") or 0) + (cal.get("practice_rounds") or 0)
    full = len(sent) >= expected if expected else bool(sent)
    r.check(full if (full or not partial_ok) else None, "ตัดสินใจและส่งข้อเสนอทุกรอบ",
            f"{len(sent)}/{expected or '?'} รอบ")
    rej = ev("batch_rejected") + ev("offer_rejected")
    r.check(None if rej else True, "ตลาดไม่ปฏิเสธข้อเสนอ (batch_rejected/offer_rejected)",
            f"{len(rej)} ครั้ง" if rej else "")
    errs = ev("state_error")
    if errs:
        r.check(None, "เครือข่ายสะดุด (state_error) แต่ agent ไปต่อได้", f"{len(errs)} ครั้ง")

    if sent:
        r.add("")
        r.add("| รอบ | ชนิด | ช่วงตลาด | source | ข้อเสนอ | กก. | วินาที |")
        r.add("|---|---|---|---|---|---|---|")
        for x in sent:
            r.add(f"| {x.get('round')} | {x.get('kind')} | {x.get('calendar') or ''} | {x.get('source')} | {x.get('n')} "
                  f"| {x.get('kg')} | {x.get('seconds')} |")
    for x in ev("rank")[-1:]:
        r.add("")
        r.add("- อันดับจบเกม: `" + json.dumps({k: v for k, v in x.items() if k not in ("time", "event")},
                                             ensure_ascii=False)[:400] + "`")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--play", action="store_true", help="รัน agent เล่นตลาดฝึก 1 เกม (DoD 2)")
    ap.add_argument("--partial-ok", action="store_true", help="ยอมเข้าเกมที่กำลังเล่นอยู่")
    ap.add_argument("--max-minutes", type=float, default=60)
    a = ap.parse_args()

    EVIDENCE.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    r = Report()
    r.add(f"# หลักฐาน DoD · {datetime.now().isoformat(timespec='seconds')}")
    r.add(f"- Python {sys.version.split()[0]} · คำสั่ง `python dod_check.py {' '.join(sys.argv[1:])}`")
    r.add("")
    check_git(r)
    check_tests(r)
    if a.play:
        check_play(r, stamp, a.partial_ok, a.max_minutes)
    r.add("")
    r.add(f"## สรุป: {'ผ่านทุกข้อ' if r.ok else 'ยังไม่ผ่าน'}")
    out = EVIDENCE / f"dod-{stamp}.md"
    out.write_text("\n".join(r.lines) + "\n", encoding="utf-8")
    print(f"\n{'ผ่าน' if r.ok else 'ไม่ผ่าน'} · หลักฐาน: {out}", flush=True)
    sys.exit(0 if r.ok else 1)


if __name__ == "__main__":
    main()
