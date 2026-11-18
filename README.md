# Coffee-TESA-starter: AI Agent ขายสารกาแฟ (TESA Top Gun Rally 2026 · Track 3)

agent ที่ขายแทนทีมในตลาดปิดซองแบบ RFQ (API v1) ตัดสินใจและส่งเองทุกรอบ ตามกติกาวันที่ 18 พ.ย.: **ด่าน 2–3 ห้ามส่งด้วยมือ ใช้ LLM ได้เฉพาะ gateway ของงาน**

ใช้โครงของ starter ผู้จัด (ลูป phase, client ตลาด, LLM ที่ไม่ throw, pack.py) แต่เปลี่ยนสมองจาก `strategy.py` เดิม (markup 1.00 ทุกราย) เป็นกลยุทธ์ Ratio Ladder ในโฟลเดอร์ `strategy/`:
เรียนรู้เพดานลับของผู้ซื้อแต่ละรายจากผลข้อเสนอ · กระจายขายตามโควตาต่อรอบและปฏิทิน · เผื่อ noise ของตัวอย่างทั้งตอนประเมินและตรวจสเปก · LLM ปรับ markup ในกรอบที่ Python คุม
(`strategy.py` ตัวเดิมยังอยู่ในประวัติ git: `git show 48fb01b:strategy.py`)

## เริ่มใช้ (Python 3.10+)

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # Mac/Linux: .venv/bin/pip
cp .env.example .env                                # Windows: copy .env.example .env · ใส่ MARKET_URL, TEAM_KEY, LLM_BASE_URL
.venv/Scripts/python main.py --forever              # ตลาดฝึก: จบเกมแล้วเล่นเกมใหม่ต่อเอง
.venv/Scripts/python main.py                        # วันแข่ง: เล่นจนตลาดปิด
```

คำสั่งข้างบนใช้ได้ใน Git Bash และ PowerShell · ถ้าใช้ Command Prompt (cmd) เขียน `\` แทน `/`

- **รัน agent ทีละตัวต่อหนึ่ง key** · ถ้าเปิดสองตัว (เช่นโฟลเดอร์นี้กับอีกโฟลเดอร์) ชุดข้อเสนอที่ส่งทีหลังจะแทนชุดแรก log ไม่ตรงกับที่ตลาดได้รับ และยิง gateway เกินโควตา (2 คำขอพร้อมกัน 20 ครั้ง/นาที)
- แก้โค้ดแล้ว Ctrl+C และรันใหม่ · agent จำผลไว้ใน `logs/memory-*.json` รีสตาร์ตกลางเกมแล้วเล่นต่อได้ (ไม่ส่งทับข้อเสนอที่เปิดอยู่)
- `USE_LLM=0` = กฎล้วน (ต้องขายได้เสมอ เผื่อ gateway ล่ม) · `DRY_RUN=1` = คิดและบันทึก แต่ไม่ส่งผลประเมินและข้อเสนอ
- `LLM_BASE_URL` ลงท้ายที่ `/v1` (ถ้าใส่ `/chat/completions` มาด้วย agent ตัดให้) · ไม่ใส่ `LLM_API_KEY` = ใช้ `TEAM_KEY`
- `.env` ของวันแข่งเป็นคนละที่อยู่และคนละ key กับตลาดฝึก · ห้ามให้ key กับทีมอื่น

### ถ้าเจอ error ตอนติดตั้ง

| อาการ | สาเหตุ | แก้ |
|---|---|---|
| `Could not find a version that satisfies the requirement requirements.txt` | ลืม `-r` | `.venv/Scripts/pip install -r requirements.txt` |
| `No module named 'openai'` / `'httpx'` | ติดตั้งด้วย pip ของเครื่อง แต่รันด้วย `.venv` (หรือกลับกัน) | ใช้ตัวใน `.venv` ทั้งคู่ |
| `'.venv' is not recognized ...` | ใช้ cmd กับ `/` | เปลี่ยนเป็น `\` หรือใช้ PowerShell |
| `ไม่พบ TEAM_KEY ใน .env` | ยังไม่มี `.env` หรือยังไม่ใส่ค่า | คัดลอก `.env.example` เป็น `.env` แล้วใส่ค่าที่ทีมงานแจก |

## agent ทำอะไร

```
ทุก ~1–5 วิ GET /v1/state (อ่านเวลาจาก seconds_left)
  ├─ APPRAISAL   → TV ของทุกล็อต (มัธยฐานภายใต้ noise) → POST /v1/appraisals ครั้งเดียว
  ├─ ROUND_OPEN  → จำผลรอบก่อน → ตารางสรุปผล → Profile ผู้ซื้อทุกราย → decide() → POST /v1/offers:batch
  │                 (รอบซ้อม: probe ยื่นน้อยเพื่อหาเพดาน)
  └─ CLOSED      → บันทึกผล · ตลาดฝึก: reveal → PR / ประเมินคลาด → logs/experiments.csv
```

`decide()` ต่อรอบ: คู่ล็อต×RFQ ที่ผ่านสเปก (safe/risky) → markup ต่อผู้ซื้อ (เรียนจาก ACCEPTED / OUTBID / ABOVE_LIMIT + ความกดดัน + ปฏิทิน) → LLM ปรับในกรอบ → จัด กก. ให้คู่ที่ได้เกินค่าเศษต่อ กก. มากสุดจนเต็มโควตา → guard → ส่ง

รายละเอียดเหตุผลทุกข้อ ตัวเลข และกับดัก: [.claude/skills/coffee-auction-seller/SKILL.md](.claude/skills/coffee-auction-seller/SKILL.md) · กติกา: [track3-rules.md](.claude/skills/coffee-auction-seller/track3-rules.md) · API: [คู่มือ-API.md](คู่มือ-API.md)

## อ่าน log

```
offers_sent {"round": "R3", "kind": "real", "calendar": "เทศกาล", "source": "llm", "n": 4, "kg": 360, "pressure": 0.93, ...}
```

| source | ความหมาย |
|---|---|
| `llm` | LLM ปรับ markup แล้ว (ในกรอบ) |
| `rules` | ใช้กฎ (LLM ปิด ไม่ทันเวลา หรือตอบเสีย ดู `notes`) |
| `probe` | รอบซ้อม ยื่นน้อยเพื่อหาเพดาน |
| `fallback` | strategy พัง ใช้ค่าก่อนตามประเภทผู้ซื้อ (ดู `strategy_crashed`) |

**ทุกครั้งที่จบรอบ** (ตลาดประกาศผล) agent พิมพ์ตารางสรุปสองตาราง ต่อท้าย `logs/round_offers.csv` และ `logs/round_buyers.csv` (เปิดด้วย Excel ได้ สะสมทุกรอบทุกเกม) และบันทึก event `round_summary` ใน `logs/decisions.jsonl`:
- ข้อเสนอของเรา: ผู้ซื้อ ประเภท ล็อต กก. บาท/กก. markup ผล (ขายได้ / แพ้ราคา / เกินเพดาน / สเปกไม่ผ่าน) กก.ที่ขายได้ และช่วงราคาที่ RFQ นั้นซื้อจริง
- ผู้ซื้อทุกราย (ทุกทีมรวมกัน): ต้องการกี่ กก. ซื้อได้กี่ กก. มีข้อเสนอกี่รายการ ราคาที่ซื้อ ต่ำสุด / เฉลี่ย / สูงสุด

รายละเอียดทุกการตัดสินใจอยู่ใน `logs/decisions.jsonl` (ข้อเสนอ markup ต่อผู้ซื้อ เหตุผลของ LLM token ที่ใช้)

## ไฟล์

| ไฟล์ | หน้าที่ |
|---|---|
| `main.py` | ลูปหลัก log ส่งข้อเสนอ |
| **`config.py`** | **ตัวเลขทั้งหมดที่ปรับได้ ← แก้กลยุทธ์ที่นี่ก่อน** |
| `strategy/` | `plan.py` (decide/allocate/guard/probe) · `buyers.py` (เรียนรู้ผู้ซื้อ) · `pricing.py` · `pacing.py` · `specs.py` · `advisor.py` (LLM) |
| `appraisal.py` | สูตร TV จาก `/v1/calendar` + ประเมินภายใต้ noise |
| `memory.py` | จำข้อเสนอและผลทุกรอบ |
| `report.py` | ตารางสรุปผลทุกครั้งที่จบรอบ + CSV |
| `llm.py`, `market.py` | client ของ LLM gateway และตลาด |
| `review.py` | เทียบ PR หลายเกม และวัด noise ของตัวอย่าง (ตลาดฝึก) |
| `pack.py` | zip ส่งกรรมการ |
| `CLAUDE.md`, `.claude/skills/coffee-auction-seller/` | ให้ Claude Code ใช้ความรู้กลยุทธ์และกติกาก่อนตอบทุกครั้ง |
| `คู่มือ-API.md` | คู่มือ API ตลาดและ LLM gateway พร้อมตาราง error code |
| `notebooks/`, `slides/` | สื่ออบรมของผู้จัด (notebook อธิบาย `strategy.py` ตัวเดิม) |

## ทดลองให้เก่งขึ้น

แก้ทีละอย่าง ใส่ป้ายใน `.env` เช่น `CHANGE=SPECIALTY 1.10` → รัน `--forever` 2–3 เกม → `python review.py` ดู PR เฉลี่ยต่อการแก้ และค่า noise ของตัวอย่างที่ควรตั้ง (`DEFECT_SD`, `DEFECT_BIAS`, ...)

## ส่งโค้ดให้กรรมการ (`pack.py`)

ใช้เฉพาะช่วง 2–3 **เมื่อทีมงานเรียกทีมเท่านั้น** ต้องส่งภายใน 10 นาทีหลังตลาดปิด ไม่ส่ง = ไม่ได้รางวัลช่วงนั้น

```bash
python pack.py --send    # สร้าง handin.zip แล้วส่งให้ตลาด (ใช้ MARKET_URL และ TEAM_KEY ใน .env)
python pack.py           # สร้าง handin.zip อย่างเดียว แล้วอัปโหลดเองที่หน้า <MARKET_URL>/handin
```

- **ไม่ใส่**: `.env` `.venv` `__pycache__` `.git` ไฟล์ `.zip` และไฟล์ใหญ่เกิน 5 MB (ยกเว้น `logs/`) · zip ไม่เกิน 20 MB
- **ใส่**: โค้ดทั้งหมดและ `logs/` (กรรมการเทียบ `logs/decisions.jsonl` กับที่ตลาดได้รับ) · ก่อนวันแข่งควรย้าย log ของตลาดฝึกออกจาก `logs/`

## เช็กลิสต์ก่อนแข่ง

- [ ] รันกับตลาดฝึกครบหนึ่งเกมโดยไม่ล้ม และรันอยู่ตัวเดียว
- [ ] `.env` มี `MARKET_URL` / `TEAM_KEY` ของตลาดแข่ง และ `LLM_BASE_URL` = gateway ของงาน `LLM_MODEL=event-llm` (ไม่ใช่ Ollama/cloud)
- [ ] `USE_LLM=0` ก็ยังขายได้
- [ ] `logs/decisions.jsonl` มีบันทึกทุกรอบ · ไม่ได้ตั้ง `DRY_RUN=1` หรือ `LOG_DIR`
- [ ] ห้ามส่งคำสั่งด้วยมือระหว่างแข่ง · อ่านเวลาจาก `/v1/state` เสมอ · เรียก API ไม่เกิน 5 ครั้ง/วินาที
