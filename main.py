"""Agent ขายสารกาแฟ (ตลาด v1 แบบ RFQ ของ TESA Top Gun Rally 2026): ดู → คิด → ส่ง → จำ ทุกรอบ ไม่ต้องมีคน

    python main.py              เล่นจนตลาดปิด
    python main.py --forever    ตลาดฝึก: จบเกมแล้วเล่นเกมใหม่ต่อเอง
    DRY_RUN=1 python main.py    คิดและบันทึก แต่ไม่ส่งผลประเมินและข้อเสนอ

ทำตาม phase ของตลาด: APPRAISAL → ส่งผลประเมินครั้งเดียว · ROUND_OPEN รอบใหม่ → ตัดสินใจครั้งเดียวต่อรอบ
· CLOSED → บันทึกผล (ตลาดฝึก: เรียก reveal เทียบ PR) · อ่านเวลาจากตลาดเสมอ ไม่เขียนเวลารอบตายตัว
ทุกข้อเสนอต้องมาจาก agent: คนแก้โค้ดและรีสตาร์ตได้ แต่ห้ามส่ง/แก้/ยกเลิกข้อเสนอเองทุกช่องทาง
log ทุกการตัดสินใจอยู่ใน logs/decisions.jsonl (กรรมการเทียบกับที่ตลาดได้รับ · pack.py ใส่ให้)
"""
import hashlib
import json
import os
import random
import sys
import time
from datetime import datetime

import config as cfg
import ranks
import report
import review
import strategy
from appraisal import appraisal_items, estimate_lots
from llm import LLM
from market import Market, MarketError
from memory import Memory
from strategy.buyers import build

sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # คอนโซล Windows พิมพ์ไทยไม่ได้ agent จะล้ม


def log(event: str, **data) -> None:
    cfg.LOGS.mkdir(exist_ok=True)
    row = {"time": datetime.now().isoformat(timespec="seconds"), "event": event, **data}
    with open(cfg.LOGS / "decisions.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    if event in ("round_summary", "offers_sent"):  # ตารางพิมพ์แล้วใน report.show() / show_sent() ลงไฟล์อย่างเดียว
        return
    short = {k: v for k, v in data.items() if k not in ("offers", "notes", "tv", "rule", "markups")}
    print(row["time"][11:], event, json.dumps(short, ensure_ascii=False)[:200], flush=True)


def send_offers(api: Market, offers: list[dict]) -> list[dict]:
    """ส่งทั้งชุดครั้งเดียว ถ้า batch ถูกปฏิเสธ (ผิดรายการเดียวก็ไม่บันทึกเลย) ส่งทีละรายการแล้วข้ามตัวที่ผิด"""
    if cfg.DRY_RUN:
        return offers
    try:
        return api.offer_batch(offers)["offers"]
    except MarketError as e:
        log("batch_rejected", error=str(e), allowed=e.allowed)
    sent = []
    for o in offers:
        try:
            sent.append(api.offer(o))
        except MarketError as e:
            log("offer_rejected", offer=o, error=str(e))
    return sent


def play(api: Market, llm: LLM | None, forever: bool) -> None:
    info = api.calendar()
    rules, calendar = info["rules"], info["calendar"]
    log("calendar", rounds=rules["timeline"]["rounds"], practice_rounds=rules["timeline"].get("practice_rounds"),
        capacity=rules.get("round_capacity"), scoring=rules.get("scoring"),
        calendar=[(c["rounds"], c["label"], c["demand_weight"]) for c in calendar])
    memory, tv, acted, game = None, {}, set(), None
    rng = random.Random()
    while True:
        try:
            state = api.state()
        except Exception as e:                      # noqa: BLE001 — เครือข่ายพังต้องไม่ทำให้ agent ตาย
            log("state_error", error=str(e))
            time.sleep(3)
            continue
        # ตลาดฝึกใช้ lot_id เดิมทุกเกม: ผลวัดตัวอย่างคือสิ่งที่แยกเกมหนึ่งจากอีกเกม
        lots_key = (state.get("game_id"),
                    tuple(sorted((l["lot_id"], json.dumps(l["sample"], sort_keys=True)) for l in state["lots"])))
        if state["lots"] and lots_key != game:      # เกมใหม่ (หรือตลาดฝึกรีเซ็ต): เริ่มใหม่หมด
            if game is not None:                    # กติกาอาจเปลี่ยนระหว่างตลาดฝึกกับวันแข่ง อ่านใหม่
                info = api.calendar()
                rules, calendar = info["rules"], info["calendar"]
            game, acted, tv = lots_key, set(), estimate_lots(rules, state["lots"])
            tag = hashlib.sha1(repr(lots_key).encode()).hexdigest()[:8]
            memory = Memory(cfg.LOGS / f"memory-{state['market']}-{tag}.json", tv)
            log("new_game", market=state["market"], game_id=state.get("game_id"), lots=len(state["lots"]),
                kg=sum(l["kg_total"] for l in state["lots"]), tv=tv)
        phase = state["phase"]
        if memory is not None and memory.record_results(state["last_results"]):   # ผลรอบใหม่ประกาศแล้ว: สรุปทันที
            summary = report.summarize(state["last_results"], tv)
            report.show(summary)
            report.save_csv(summary, cfg.LOGS, str(state.get("game_id") or tag))
            log("round_summary", **summary)
            if phase == "ROUND_OPEN":
                for p in build(memory, state["rfqs"]).values():
                    print("   ", p.brief())

        if phase == "APPRAISAL" and not state["appraisal_submitted"] and "appraisal" not in acted:
            if cfg.DRY_RUN:
                acted.add("appraisal")
                log("appraisal_dry_run", tv=tv)
            else:
                tries = sum(1 for k in acted if k.startswith("appraisal_try"))
                acted.add(f"appraisal_try{tries}")
                try:
                    api.appraise(appraisal_items(rules, state["lots"], tv))
                    acted.add("appraisal")
                    log("appraisal_sent", tv=tv)
                except MarketError as e:
                    log("appraisal_error", error=str(e), attempt=tries + 1)
                    if e.code == "APPRAISAL_ALREADY_SUBMITTED" or tries >= 2:
                        acted.add("appraisal")       # ส่งแล้ว หรือพลาด 3 ครั้ง: ไม่ลองอีก (ไม่ยิงถี่)

        elif phase == "ROUND_OPEN" and not state["paused"] and state["round"]["round_id"] not in acted:
            rid = state["round"]["round_id"]
            acted.add(rid)
            if any(o.get("round_id") == rid for o in state["my_open_offers"]):
                # รีสตาร์ตหลังส่งรอบนี้ไปแล้ว: ตลาดถือข้อเสนอของเราอยู่ ชุดใหม่จะเล็กกว่า (capacity_left_kg หักไปแล้ว)
                log("round_already_sent", round=rid, n=len(state["my_open_offers"]))
                continue
            ctx = strategy.Context(rules, calendar, tv, rng, memory, llm, time.time() + (state["seconds_left"] or 60))
            t0 = time.time()
            try:
                offers, source, why = strategy.decide(state, ctx)
            except Exception as e:                  # noqa: BLE001 — บั๊กใน strategy ต้องไม่หยุด agent
                log("strategy_crashed", error=repr(e))
                offers, source, why = strategy.fallback(state, ctx), "fallback", {}
            sent = send_offers(api, offers) if offers else []
            memory.record_sent(rid, offers, state, tv)
            log("offers_sent", round=rid, kind=state["round"].get("kind"), calendar=state["round"].get("calendar"),
                source=source, n=len(sent), kg=sum(o["kg"] for o in sent), seconds=round(time.time() - t0, 1),
                dry_run=cfg.DRY_RUN, **why, offers=offers, notes=ctx.notes)
            report.show_sent(state, sent if not cfg.DRY_RUN else offers, tv, source, why)
            for n in ctx.notes:
                if "failed" in n or "dropped" in n:
                    print("   !", n[:120], flush=True)

        elif phase == "CLOSED" and game is not None and "closed" not in acted:
            acted.add("closed")
            played = any(not k.startswith(("appraisal", "closed")) for k in acted)
            log("market_closed", standings=state["standings"], played=played)
            snap = ranks.snapshot(api, state)              # อันดับจบเกม (เก็บต่อเกม ไม่ใช่ต่อรอบ)
            if snap:
                log("rank", **snap)
            # เล่นเกมนี้จริงไหม ดูจาก memory (รอดการรีสตาร์ต) ไม่ใช่แค่ตั้งแต่เปิด agent ครั้งนี้
            played_game = played or any(r.get("my_offers") for r in memory.rounds)
            # ตลาดฝึกมี reveal (TV จริง และ PR) ให้ดูช่วงสั้นๆ หลังปิด · เฉพาะเกมที่เราเล่นจริง (ไม่ใช่ dry-run)
            rev = None
            if played and not cfg.DRY_RUN and state.get("market") != "competition":
                rev = review.record(api, os.getenv("CHANGE", "ไม่ได้ระบุ"))
            if played_game:                                # เกมที่ไม่ได้ยื่นเลย (เปิด agent หลังเกมจบ) ไม่นับในประวัติอันดับ
                saved = ranks.save(cfg.LOGS, state, snap, (rev or {}).get("score"), note=os.getenv("CHANGE", ""))
                print(f"   จบเกม · {ranks.text(snap)} · "
                      + (f"บันทึกที่ {cfg.LOGS / 'ranks.csv'}" if saved else "เกมนี้บันทึกไว้แล้ว"), flush=True)
                ranks.show(cfg.LOGS)                       # ตารางอันดับเกมล่าสุด (ไม่เกิน ranks.SHOW_GAMES เกม)
            if not forever:
                return

        left = state["seconds_left"] or 5             # อ่านเวลาจากตลาด
        time.sleep(min(5.0, max(cfg.POLL_SECONDS, left + 0.2)))


def main() -> None:
    if not cfg.TEAM_KEY:
        sys.exit("ไม่พบ TEAM_KEY ใน .env (คัดลอก .env.example เป็น .env แล้วใส่ MARKET_URL, TEAM_KEY, LLM_BASE_URL)")
    api = Market(cfg.MARKET_URL, cfg.TEAM_KEY)
    llm = None
    if cfg.USE_LLM and cfg.LLM_BASE_URL:
        llm = LLM(cfg.LLM_BASE_URL, cfg.LLM_MODEL, cfg.LLM_API_KEY, timeout=cfg.LLM_TIMEOUT, thinking=cfg.LLM_THINKING,
                  max_tokens=cfg.LLM_MAX_TOKENS, temperature=cfg.LLM_TEMPERATURE)
    print(f"ตลาด {cfg.MARKET_URL} · LLM {cfg.LLM_BASE_URL + ' ' + cfg.LLM_MODEL if llm else 'ปิด (กฎล้วน)'}"
          f" · env {os.getenv('ENV_FILE') or '.env'} · log {cfg.LOGS}"
          + (" · DRY_RUN ไม่ส่งจริง" if cfg.DRY_RUN else ""))
    play(api, llm, "--forever" in sys.argv)


if __name__ == "__main__":
    main()
