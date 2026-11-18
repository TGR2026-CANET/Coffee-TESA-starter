"""เรียก LLM แบบ OpenAI-compatible (gateway ของงาน "event-llm") ให้ตอบเป็น JSON

ask_json() ไม่เคย throw: timeout, 429, key ผิด, JSON ขาด → คืน None แล้วผู้เรียกใช้กฎแทน
ช่วงแข่ง (ด่าน 2–3) ใช้ได้เฉพาะ gateway ของงาน · gateway ล่ม = เล่นต่อด้วยกฎ ห้ามสลับไป LLM อื่น
gateway: context รวม 8,192 token ต่อครั้ง · ทำพร้อมกันได้ 2 คำขอต่อทีม · ไม่เกิน 20 คำขอ/นาที · max_tokens ≤ 2000
"""
import json
import re
import time

from openai import OpenAI

# วิธีเปิด/ปิด thinking ต่างกันตามเซิร์ฟเวอร์ ลองทีละแบบแล้วจำแบบที่ใช้ได้
THINKING_STYLES = [
    lambda on: {"reasoning_effort": "medium" if on else "none"},                    # gateway ของงาน, Ollama
    lambda on: {"extra_body": {"chat_template_kwargs": {"enable_thinking": on}}},    # vLLM
    lambda on: {},                                                                   # OpenAI ล้วน
]


class LLM:
    def __init__(self, base_url: str, model: str, api_key: str, timeout: float = 40, thinking: bool = False,
                 max_tokens: int = 700, temperature: float = 0.3):
        self.client = OpenAI(base_url=base_url, api_key=api_key or "none", timeout=timeout, max_retries=0)
        self.model, self.thinking, self.timeout = model, thinking, timeout
        self.max_tokens, self.temperature = max_tokens, temperature
        self.style = 0
        self.last_error = None
        self.seconds = None
        self.usage = None

    def ask_json(self, system: str, user: str, schema: dict, timeout: float | None = None) -> dict | None:
        self.last_error, self.usage = None, None
        client = self.client.with_options(timeout=min(timeout, self.timeout)) if timeout else self.client
        t0 = time.time()
        for style in range(self.style, len(THINKING_STYLES)):
            try:
                r = client.chat.completions.create(
                    model=self.model, temperature=self.temperature, max_tokens=self.max_tokens,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    response_format={"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}},
                    **THINKING_STYLES[style](self.thinking))
            except Exception as e:                       # noqa: BLE001 — พลาดแบบไหนก็ตาม = ใช้กฎ
                self.last_error = f"{type(e).__name__}: {e}"[:300]
                if getattr(e, "status_code", None) == 400:   # เซิร์ฟเวอร์ไม่รู้จักสวิตช์ thinking แบบนี้ ลองแบบถัดไป
                    continue
                return None
            self.style = style
            self.seconds = round(time.time() - t0, 1)
            if r.usage:
                self.usage = {"prompt": r.usage.prompt_tokens, "answer": r.usage.completion_tokens}
            text = r.choices[0].message.content or ""
            answer = parse_json(text)
            if answer is None:
                self.last_error = f"not JSON (finish={r.choices[0].finish_reason}): {text[:200]!r}"
            return answer
        return None


def parse_json(text: str) -> dict | None:
    """รับ JSON ล้วน, JSON ใน ``` หรือ JSON หลัง <think>"""
    text = re.sub(r"<think>.*?(</think>|$)", "", text, flags=re.S).strip()
    text = re.sub(r"```(?:json)?", "", text)
    for candidate in (text, *re.findall(r"\{.*\}", text, flags=re.S)):
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    return None


def tokens(text: str) -> int:
    """ประมาณ token แบบเผื่อไว้ (อังกฤษ/JSON ~3 ตัวอักษรต่อ token · ไทยใช้มากกว่านี้ จึงส่งเป็นอังกฤษ)"""
    return len(text) // 3 + 1
