"""เก็บประวัติอันดับต่อเกม (ไม่ใช่ต่อรอบ): ถาม GET /v1/leaderboard ตอนตลาดปิด

ตลาดปิด = อันดับจบเกม → ต่อท้าย <LOG_DIR>/ranks.csv หนึ่งแถวต่อเกม (เปิดด้วย Excel ได้) + event "rank" ใน decisions.jsonl
แล้วพิมพ์ตารางอันดับ SHOW_GAMES เกมล่าสุด · พังเมื่อไรก็ข้าม ไม่กระทบการขาย
"""
SHOW_GAMES = 6           # ทีมเก็บผล 6 เกม: ตารางท้ายเกมแสดงเกมล่าสุดเท่านี้ (ranks.csv เก็บทุกเกม)
import csv
from datetime import datetime

from market import Market

HEAD = ["เวลา", "ตลาด", "เกม", "ทีม", "อันดับ", "จากทั้งหมด", "อันดับ (เฉพาะทีมคน)", "ทีมคน", "อันดับ 1",
        "final", "PR", "คะแนนขาย", "คะแนนประเมิน", "คะแนนรวม", "ขายได้ กก.", "เหลือ กก.", "รายได้", "ราคาเฉลี่ย", "หมายเหตุ"]


def snapshot(api: Market, state: dict) -> dict | None:
    """อันดับของเราตอนนี้ · None ถ้าถามไม่ได้ หรือไม่เจอทีมเราในตาราง"""
    try:
        lb = api.call("GET", "/v1/leaderboard", tries=2)
    except Exception:                                  # noqa: BLE001
        return None
    # ตลาดฝึก fly.dev ส่ง {"rows": [{rank, team_id, bot}]} · ตลาด 202.29.227.22 ส่ง {"ranking": [{rank, team_id, name}]}
    rows = lb.get("rows") or lb.get("ranking") or lb.get("leaderboard") or []
    me = next((r for r in rows if r.get("team_id") == state.get("team_id")), None)
    if me is None:
        return None

    def bot(r):
        return r["bot"] if "bot" in r else str(r.get("team_id", "")).upper().startswith("BOT")

    humans = [r for r in rows if not bot(r)]
    return {"game_id": lb.get("game_id", state.get("game_id")), "rank": me.get("rank"), "teams": len(rows),
            "rank_humans": 1 + sum(1 for r in humans if (r.get("rank") or 0) < (me.get("rank") or 0)),
            "humans": len(humans), "leader": rows[0].get("team_id") if rows else None,
            "final": bool(lb.get("final")), "frozen": bool(lb.get("frozen"))}


def _same_game(row: dict, state: dict, snap: dict) -> bool:
    """แถวเดิมเป็นเกมเดียวกันไหม (รีสตาร์ต agent ตอนตลาดปิด = เห็นเกมเดิมซ้ำ) · ตลาดที่ไม่มี game_id ดูจากยอดขาย"""
    st = state.get("standings") or {}
    gid = snap.get("game_id", state.get("game_id"))
    return (row.get("ตลาด") == str(state.get("market")) and row.get("เกม") == ("" if gid is None else str(gid))
            and row.get("ขายได้ กก.") == str(st.get("kg_sold", "")) and row.get("รายได้") == str(st.get("revenue_thb", "")))


def save(folder, state: dict, snap: dict | None, score: dict | None = None, note: str = "") -> bool:
    """ต่อท้าย ranks.csv หนึ่งแถว (เรียกตอนตลาดปิด) · เกมที่บันทึกแล้วไม่บันทึกซ้ำ → คืน False"""
    snap, score, st = snap or {}, score or {}, state.get("standings") or {}
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "ranks.csv"
    new = not path.exists()
    if not new:
        with open(path, encoding="utf-8-sig") as f:
            if any(_same_game(r, state, snap) for r in csv.DictReader(f)):
                return False
    with open(path, "a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(HEAD)
        w.writerow([datetime.now().isoformat(timespec="seconds"), state.get("market"),
                    snap.get("game_id", state.get("game_id")), state.get("team_id"),
                    snap.get("rank", ""), snap.get("teams", ""), snap.get("rank_humans", ""), snap.get("humans", ""),
                    snap.get("leader", ""), snap.get("final", ""), score.get("pr", ""), score.get("pr_score", ""),
                    score.get("appraisal_score", ""), score.get("total", ""), st.get("kg_sold", ""),
                    st.get("kg_remaining", ""), st.get("revenue_thb", ""), st.get("avg_price_per_kg", ""), note])
    return True


def show(folder, n: int = SHOW_GAMES) -> None:
    """พิมพ์อันดับ n เกมล่าสุดจาก ranks.csv"""
    import report
    path = folder / "ranks.csv"
    if not path.exists():
        return
    with open(path, encoding="utf-8-sig") as f:
        games = list(csv.DictReader(f))[-n:]
    rows = [[i + 1, g["เวลา"][5:16].replace("T", " "), g["เกม"], f"{g['อันดับ']}/{g['จากทั้งหมด']}",
             g["PR"], g["คะแนนรวม"], g["ขายได้ กก."], g["เหลือ กก."]] for i, g in enumerate(games)]
    print(f"อันดับ {len(games)} เกมล่าสุด (ทั้งหมดใน {path})")
    print(report.table(["#", "เวลา", "เกม", "อันดับ", "PR", "คะแนนรวม", "ขายได้ กก.", "เหลือ กก."], rows,
                       right={0, 3, 4, 5, 6, 7}), flush=True)


def text(snap: dict | None) -> str:
    if not snap:
        return "อันดับ: ถามไม่ได้"
    extra = f" · เฉพาะทีมคน {snap['rank_humans']}/{snap['humans']}" if snap["humans"] != snap["teams"] else ""
    return f"อันดับ {snap['rank']}/{snap['teams']}{extra}" + (" (แช่แข็ง)" if snap["frozen"] else "")
