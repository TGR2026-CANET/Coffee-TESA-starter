"""simulator ตลาด RFQ v1 (ไม่ต่อตลาดจริง): เทียบ PR ของ agent เรากับ starter ของผู้จัด ด้วยโลกเดียวกันทุก seed

    python sim.py                         30 เกม · คู่แข่ง 9 ทีม → evidence/sim_results.csv
    python sim.py --games 100 --teams 15  เกมมากขึ้น/ทีมมากขึ้น
    python sim.py --out evidence/sim_results.csv

แต่ละ seed สร้างโลกครั้งเดียว (ล็อตทุกทีม ผู้ซื้อ เพดานลับ RFQ ทุกรอบ นิสัยคู่แข่ง) แล้วเล่น 2 ครั้ง:
  ours    = strategy/ ปัจจุบัน + appraisal.estimate_lots + Memory (กฎล้วน ไม่ใช้ LLM)
  starter = strategy.py + appraisal.py ของ starter ผู้จัด (git commit 48fb01b: markup 1.00 ทุกผู้ซื้อ สูตร TV ตรง)
คู่แข่ง: ส่วนหนึ่งเป็น starter ของผู้จัด ที่เหลือเป็นบอตบันได (เปิดราคาตามประเภท ขยับตามผล ลดเมื่อของค้าง)

สมมติฐานของตลาดจำลอง (ใช้เทียบสองกลยุทธ์กันเอง ไม่ใช่ทำนายคะแนนจริง · ตัวเลขจากตลาดฝึกใน SKILL.md):
  กติกา TV จาก tests/fixtures/calendar.json · ปฏิทินวันแข่ง P1 + ปกติ 2 · high season 2 (1.4) · ปกติ 2 · ท้ายตลาด 2 (0.7)
  เพดานลับ (เทียบ TV จริง): SPECIALTY 1.12–1.22 · COMMERCIAL 1.04–1.08 · INSTANT 0.86–0.90
  ผู้ซื้อตรวจสเปกกับคุณภาพจริง เรียงราคา ÷ TV จริงจากถูกไปแพง · ความต้องการรวมต่อรอบ ≈ 0.9 × ของทั้งตลาด ÷ 8 × demand_weight
  ผลวัดตัวอย่าง = จริง + N(0, 0.3) defect, N(0, 0.1) ความชื้น, ตะแกรงคลาด ±1 (15%) · รอบซ้อมไม่หักของ ไม่นับรายได้
  PR = (รายได้ + 0.80 × TV จริงของที่เหลือ) ÷ TV จริงทั้งหมด
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import statistics
import subprocess
import sys
import tempfile
import time
import types
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
if os.environ.get("PYTHONHASHSEED") != "0" and __name__ == "__main__":
    # ลำดับของ set/dict ของ str สุ่มใหม่ทุก process → ตรึงไว้ให้ seed เดียวกันได้ผลเดิมทุกครั้ง
    sys.exit(subprocess.run([sys.executable, *sys.argv], env={**os.environ, "PYTHONHASHSEED": "0"}).returncode)
TMP = Path(tempfile.mkdtemp(prefix="coffee-sim-"))
os.environ["LOG_DIR"] = str(TMP)                      # ก่อน import config: ไม่เขียน logs/ จริง
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import appraisal  # noqa: E402
import config as cfg  # noqa: E402
import strategy  # noqa: E402
from memory import Memory  # noqa: E402

RULES = json.loads((HERE / "tests" / "fixtures" / "calendar.json").read_text(encoding="utf-8"))["rules"]
RULES = {**RULES, "timeline": {**RULES["timeline"], "practice_rounds": 1, "rounds": 8}}
CALENDAR = [{"rounds": [1, 2], "label": "ปกติ", "demand_weight": 1.0, "specialty_share": 0.2},
            {"rounds": [3, 4], "label": "high season", "demand_weight": 1.4, "specialty_share": 0.35},
            {"rounds": [5, 6], "label": "ปกติ", "demand_weight": 1.0, "specialty_share": 0.2},
            {"rounds": [7, 8], "label": "ท้ายตลาด", "demand_weight": 0.7, "specialty_share": 0.2}]
ROUNDS = [("P1", "practice", 1)] + [(f"R{i}", "real", i) for i in range(1, 9)]
BAG = 60
STARTER_COMMIT = "48fb01b"
CEILING = {"SPECIALTY": (1.12, 1.22), "COMMERCIAL": (1.04, 1.08), "INSTANT": (0.86, 0.90)}
RANK = {"SPECIALTY": 0, "COMMERCIAL": 1, "INSTANT": 2}


def load_starter() -> tuple[types.ModuleType, types.ModuleType]:
    """starter ของผู้จัดจาก git (ไม่แก้สักบรรทัด)"""
    mods = []
    for name in ("strategy", "appraisal"):
        src = subprocess.run(["git", "show", f"{STARTER_COMMIT}:{name}.py"], cwd=HERE, capture_output=True,
                             text=True, encoding="utf-8", check=True).stdout
        mod = types.ModuleType(f"starter_{name}")
        sys.modules[mod.__name__] = mod                  # dataclass ต้องหา module เจอ
        exec(compile(src, f"{STARTER_COMMIT}:{name}.py", "exec"), mod.__dict__)
        mods.append(mod)
    return mods[0], mods[1]


S_STRAT, S_APPR = load_starter()


# ---------- โลก ----------

def r60(x: float) -> int:
    return max(0, int(round(x / BAG)) * BAG)


def make_lot(rng: random.Random, lot_id: str) -> dict:
    kind = rng.choices(["spec", "comm", "inst"], [0.3, 0.5, 0.2])[0]
    d, m, s = {"spec": (rng.uniform(0.4, 2.7), rng.uniform(10.5, 12.3), rng.randint(15, 18)),
               "comm": (rng.uniform(2.6, 6.8), rng.uniform(10.5, 12.4), rng.randint(13, 17)),
               "inst": (rng.uniform(6.5, 12.0), rng.uniform(11.0, 13.8), rng.randint(12, 16))}[kind]
    true = {"defect_pct": round(d, 2), "moisture_pct": round(m, 2), "screen": s}
    sample = {"defect_pct": round(max(0.0, d + rng.gauss(0, 0.3)), 2),
              "moisture_pct": round(m + rng.gauss(0, 0.1), 2),
              "screen": s + (rng.choice([-1, 1]) if rng.random() < 0.15 else 0)}
    kg = rng.choice([240, 300, 360, 420, 480, 540, 600])
    return {"lot_id": lot_id, "kg_total": kg, "true": true, "sample": sample,
            "tv_true": appraisal.true_value(RULES, true)}


def make_world(seed: int, n_teams: int, starter_share: float) -> dict:
    rng = random.Random(seed)
    teams = ["T01"] + [f"B{i:02d}" for i in range(1, n_teams)]
    lots = {t: [make_lot(rng, f"LOT-{t}-{j}") for j in range(1, rng.randint(3, 4) + 1)] for t in teams}
    buyers = []
    for btype, n in (("SPECIALTY", 2), ("COMMERCIAL", 3), ("INSTANT", 2)):
        for k in range(n):
            spec = {"SPECIALTY": {"max_defect_pct": rng.choice([2.5, 3.0]), "max_moisture_pct": 12.5,
                                  "min_screen": rng.choice([15, 16])},
                    "COMMERCIAL": {"max_defect_pct": 7.0, "max_moisture_pct": 12.5, "min_screen": None},
                    "INSTANT": {"max_defect_pct": 15.0, "max_moisture_pct": 14.0, "min_screen": None}}[btype]
            buyers.append({"buyer": f"{btype[:4]}-{k + 1}", "buyer_type": btype, "spec": spec,
                           "ceiling": rng.uniform(*CEILING[btype])})
    supply = sum(l["kg_total"] for ls in lots.values() for l in ls) / 8
    rfqs = {}
    for rid, kind, num in ROUNDS:
        season = next(c for c in CALENDAR if num in c["rounds"])
        total = supply * 0.9 * season["demand_weight"]
        share = {"SPECIALTY": season["specialty_share"], "INSTANT": 0.25}
        share["COMMERCIAL"] = 1 - share["SPECIALTY"] - share["INSTANT"]
        out = []
        for i, b in enumerate(buyers):
            same = sum(1 for x in buyers if x["buyer_type"] == b["buyer_type"])
            kg = r60(total * share[b["buyer_type"]] / same * rng.uniform(0.7, 1.3))
            if kg >= BAG:
                out.append({"rfq_id": f"{rid}-{i + 1:02d}", "buyer": b["buyer"], "buyer_type": b["buyer_type"],
                            "kg": kg, "bags": kg // BAG, "spec": b["spec"], "_ceiling": b["ceiling"]})
        rfqs[rid] = out
    bots = {t: ("starter" if rng.random() < starter_share else "ladder") for t in teams[1:]}
    style = {t: {"open": {"SPECIALTY": rng.uniform(0.98, 1.08), "COMMERCIAL": rng.uniform(0.96, 1.03),
                          "INSTANT": rng.uniform(0.87, 0.95)},
                 "up": rng.choice([0.01, 0.02]), "down": rng.choice([0.02, 0.03])} for t in teams[1:]}
    return {"seed": seed, "teams": teams, "lots": lots, "rfqs": rfqs, "bots": bots, "style": style}


def passes(q: dict, spec: dict) -> bool:
    return (q["defect_pct"] <= spec["max_defect_pct"] and q["moisture_pct"] <= spec["max_moisture_pct"]
            and (spec.get("min_screen") is None or q["screen"] >= spec["min_screen"]))


# ---------- ผู้เล่น ----------

class Ours:
    def __init__(self, team: str, lots: list[dict], seed: int):
        self.tv = appraisal.estimate_lots(RULES, lots)
        self.memory = Memory(TMP / f"mem-{seed}-{team}.json", self.tv)
        self.rng = random.Random(seed * 31 + 1)
        self.crashes = 0

    def decide(self, state: dict) -> list[dict]:
        ctx = strategy.Context(RULES, CALENDAR, self.tv, self.rng, self.memory, None, time.time() + 60)
        try:
            offers, _, _ = strategy.decide(state, ctx)
        except Exception:  # noqa: BLE001  เหมือน main.py
            self.crashes += 1
            offers = strategy.fallback(state, ctx)
        self.memory.record_sent(state["round"]["round_id"], offers, state, self.tv)
        return offers

    def learn(self, results: dict) -> None:
        self.memory.record_results(results)


class Starter:
    def __init__(self, team: str, lots: list[dict], seed: int):
        self.tv = S_APPR.estimate_lots(RULES, lots)
        self.rng = random.Random(seed * 31 + 2 + (int(team[1:]) if team[1:].isdigit() else 0))

    def decide(self, state: dict) -> list[dict]:
        ctx = S_STRAT.Context(RULES, CALENDAR, self.tv, self.rng, None, None, 999)
        return S_STRAT.decide(state, ctx)[0]

    def learn(self, results: dict) -> None:
        pass


class Ladder:
    """บอตบันได: markup ต่อผู้ซื้อ ขยับตามผล · ลดเมื่อของค้างมาก · ล็อตไปผู้ซื้อสเปกสูงสุดที่ผ่าน"""

    def __init__(self, team: str, lots: list[dict], seed: int, style: dict):
        self.tv = S_APPR.estimate_lots(RULES, lots)
        self.style, self.m, self.ban = style, {}, set()
        self.rng = random.Random(seed * 31 + 3 + int(team[1:]))
        self.total = sum(l["kg_total"] for l in lots)

    def decide(self, state: dict) -> list[dict]:
        rnd = state["round"]
        left = 8 if rnd["kind"] == "practice" else 9 - rnd["number"]
        avail = sum(l["kg_available"] for l in state["lots"])
        push = -0.03 if avail > 0.9 * state["round_capacity_kg"] * left else 0.0
        room = state["capacity_left_kg"]
        rfqs = sorted(state["rfqs"], key=lambda r: (RANK[r["buyer_type"]], self.rng.random()))
        offers = []
        for lot in sorted(state["lots"], key=lambda l: -self.tv[l["lot_id"]]):
            budget = min(lot["kg_available"], r60(math.ceil(lot["kg_available"] * min(1.0, 1.4 / left) / BAG) * BAG))
            ok = [r for r in rfqs if passes(lot["sample"], r["spec"]) and (lot["lot_id"], r["buyer"]) not in self.ban]
            if not ok:
                continue
            best = RANK[ok[0]["buyer_type"]]
            for r in [r for r in ok if RANK[r["buyer_type"]] <= best + 1][:2]:
                kg = min(budget, r["kg"], room) // BAG * BAG
                if kg < BAG:
                    break
                mk = self.m.get(r["buyer"], self.style["open"][r["buyer_type"]]) + push
                mk = min(max(mk, 0.85), 1.30)
                offers.append({"rfq_id": r["rfq_id"], "lot_id": lot["lot_id"], "kg": kg,
                               "price_per_kg": round(mk * self.tv[lot["lot_id"]], 2)})
                budget, room = budget - kg, room - kg
        return offers

    def learn(self, results: dict) -> None:
        rfq_buyer = {m["rfq_id"]: m["buyer"] for m in results["market_summary"]}
        nxt: dict[str, float] = {}
        for o in results["my_offers"]:
            b = rfq_buyer[o["rfq_id"]]
            cur = o["price_per_kg"] / self.tv[o["lot_id"]]
            step = {"ACCEPTED": self.style["up"], "PARTIAL": 0.0, "OUTBID": -self.style["down"],
                    "ABOVE_LIMIT": -0.03}.get(o["status"])
            if step is None:
                self.ban.add((o["lot_id"], b))
                continue
            nxt[b] = min(nxt.get(b, 9.0), cur + step)       # หลายล็อตไปรายเดียวกัน: ใช้ค่าที่ระวังที่สุด
        self.m.update(nxt)


# ---------- เล่นหนึ่งเกม ----------

def play(world: dict, subject: str) -> dict[str, dict]:
    teams, lots0 = world["teams"], world["lots"]
    lots = {t: [{"lot_id": l["lot_id"], "kg_total": l["kg_total"], "kg_available": l["kg_total"],
                 "sample": l["sample"]} for l in lots0[t]] for t in teams}
    truth = {l["lot_id"]: l for t in teams for l in lots0[t]}
    cap = {t: math.ceil(RULES.get("round_capacity_factor", 1.5) * sum(l["kg_total"] for l in lots0[t]) / BAG / 8) * BAG
           for t in teams}
    seed = world["seed"]
    players = {"T01": (Ours if subject == "ours" else Starter)("T01", lots["T01"], seed)}
    for t in teams[1:]:
        players[t] = (Starter(t, lots[t], seed) if world["bots"][t] == "starter"
                      else Ladder(t, lots[t], seed, world["style"][t]))
    revenue = {t: 0.0 for t in teams}
    last = {t: None for t in teams}
    statuses = Counter()
    oid = 0
    for rid, kind, num in ROUNDS:
        season = next(c for c in CALENDAR if num in c["rounds"])
        public = [{k: v for k, v in r.items() if not k.startswith("_")} for r in world["rfqs"][rid]]
        book = []
        for t in teams:
            state = {"market": "sim", "team_id": t, "game_id": seed,
                     "round": {"round_id": rid, "kind": kind, "number": num, "of": 8,
                               "counts_for_score": kind == "real", "calendar": season["label"]},
                     "lots": [dict(l) for l in lots[t] if l["kg_available"] > 0], "rfqs": public,
                     "capacity_left_kg": cap[t], "round_capacity_kg": cap[t], "last_results": last[t],
                     "my_open_offers": [], "standings": {}}
            for o in players[t].decide(state):
                oid += 1
                book.append({**o, "team": t, "offer_id": f"O{oid:05d}"})
        # จับคู่: ต่อ RFQ ตรวจสเปกกับคุณภาพจริง → เพดานลับ → ถูกสุด (ราคา ÷ TV จริง) ได้ก่อน
        tie = random.Random(seed * 7919 + num + (0 if kind == "real" else 100))
        status, filled, summary = {}, {}, []
        for r in world["rfqs"][rid]:
            offs = [o for o in book if o["rfq_id"] == r["rfq_id"]]
            ok = []
            for o in offs:
                lot = truth[o["lot_id"]]
                if not passes(lot["true"], r["spec"]):
                    status[o["offer_id"]] = "SPEC_MISMATCH"
                elif o["price_per_kg"] / lot["tv_true"] > r["_ceiling"]:
                    status[o["offer_id"]] = "ABOVE_LIMIT"
                else:
                    ok.append((o["price_per_kg"] / lot["tv_true"], tie.random(), o))
            need, prices, bought = r["kg"], [], 0
            for _, _, o in sorted(ok, key=lambda x: (x[0], x[1])):
                take = min(need, o["kg"])
                filled[o["offer_id"]] = take
                status[o["offer_id"]] = "ACCEPTED" if take == o["kg"] else "PARTIAL" if take > 0 else "OUTBID"
                need -= take
                bought += take
                if take:
                    prices.append(o["price_per_kg"])
            summary.append({"rfq_id": r["rfq_id"], "buyer": r["buyer"], "buyer_type": r["buyer_type"],
                            "wanted_kg": r["kg"], "bought_kg": bought, "offers": len(offs),
                            "price_min": min(prices) if prices else None, "price_max": max(prices) if prices else None,
                            "price_avg": round(sum(prices) / len(prices), 2) if prices else None})
        for t in teams:
            mine = []
            for o in (o for o in book if o["team"] == t):
                f = filled.get(o["offer_id"], 0)
                mine.append({"offer_id": o["offer_id"], "rfq_id": o["rfq_id"], "lot_id": o["lot_id"], "kg": o["kg"],
                             "price_per_kg": o["price_per_kg"], "status": status.get(o["offer_id"], "OUTBID"),
                             "filled_kg": f})
                if t == "T01" and kind == "real":
                    statuses[mine[-1]["status"]] += 1
                if kind == "real" and f:
                    revenue[t] += f * o["price_per_kg"]
                    lot = next(l for l in lots[t] if l["lot_id"] == o["lot_id"])
                    lot["kg_available"] -= f
            last[t] = {"round_id": rid, "kind": kind, "my_offers": mine, "market_summary": summary}
            players[t].learn(last[t])
    out = {}
    for t in teams:
        tv_all = sum(truth[l["lot_id"]]["tv_true"] * l["kg_total"] for l in lots[t])
        salvage = sum(truth[l["lot_id"]]["tv_true"] * l["kg_available"] for l in lots[t]) * RULES["salvage_ratio"]
        out[t] = {"pr": (revenue[t] + salvage) / tv_all, "revenue": revenue[t],
                  "sold_kg": sum(l["kg_total"] - l["kg_available"] for l in lots[t]),
                  "unsold_kg": sum(l["kg_available"] for l in lots[t])}
    out["T01"]["status"] = statuses
    out["T01"]["crashes"] = getattr(players["T01"], "crashes", 0)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--games", type=int, default=30)
    ap.add_argument("--teams", type=int, default=10, help="จำนวนทีมรวมทีมเรา")
    ap.add_argument("--starter-share", type=float, default=0.3, help="สัดส่วนคู่แข่งที่ใช้ starter ของผู้จัด")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="evidence/sim_results.csv")
    a = ap.parse_args()

    rows, t0 = [], time.time()
    stat = {"ours": Counter(), "starter": Counter()}
    crashes = 0
    print(f"simulator ตลาด RFQ v1 · {a.games} เกม · {a.teams} ทีม (คู่แข่งเป็น starter {a.starter_share:.0%}) · "
          f"starter = commit {STARTER_COMMIT} · ours = โค้ดปัจจุบัน (กฎล้วน ไม่ใช้ LLM)")
    print(f"{'seed':>5} {'ours':>7} {'starter':>8} {'ต่าง':>7} {'ดีสุดในตลาด(ours)':>18}")
    for seed in range(a.seed, a.seed + a.games):
        world = make_world(seed, a.teams, a.starter_share)
        res = {s: play(world, s) for s in ("ours", "starter")}
        o, s = res["ours"]["T01"], res["starter"]["T01"]
        stat["ours"].update(o["status"])
        stat["starter"].update(s["status"])
        crashes += o["crashes"]
        best = max(v["pr"] for v in res["ours"].values())
        print(f"{seed:>5} {o['pr']:>7.3f} {s['pr']:>8.3f} {o['pr'] - s['pr']:>+7.3f} {best:>18.3f}", flush=True)
        for name, r in (("ours", o), ("starter", s)):
            rows.append({"agent": name, "seed": seed, "pr": round(r["pr"], 4), "revenue": round(r["revenue"], 2),
                         "sold_kg": r["sold_kg"], "unsold_kg": r["unsold_kg"]})
    ours = [r["pr"] for r in rows if r["agent"] == "ours"]
    star = [r["pr"] for r in rows if r["agent"] == "starter"]
    wins = sum(1 for x, y in zip(ours, star) if x > y)
    print(f"\nPR เฉลี่ย: ours {statistics.mean(ours):.3f} · starter {statistics.mean(star):.3f} · "
          f"ต่าง {statistics.mean(ours) - statistics.mean(star):+.3f} · ours ชนะ {wins}/{len(ours)} เกม · "
          f"{time.time() - t0:.0f} วิ")
    for name, c in stat.items():
        n = sum(c.values()) or 1
        print(f"ผลข้อเสนอของ {name:<7} (รอบจริง): " + " · ".join(f"{k} {v * 100 / n:.0f}%" for k, v in c.most_common()))
    print(f"strategy ของเรา crash แล้วใช้ fallback: {crashes} ครั้ง")
    out = Path(a.out) if Path(a.out).is_absolute() else HERE / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"ผล: {out}  (ใช้กับ python ac_agent.py --sim-results {out.relative_to(HERE) if out.is_relative_to(HERE) else out})")


if __name__ == "__main__":
    main()
