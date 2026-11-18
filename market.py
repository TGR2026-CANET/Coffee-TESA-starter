"""Client ของตลาด REST v1: ใส่ key ลองใหม่ รอเมื่อเจอ 429 แปลง error ให้อ่านง่าย

endpoint ทั้งหมดอยู่ใน คู่มือ-API.md · ทุก path ขึ้นต้นด้วย /v1
"""
import time

import httpx


class MarketError(Exception):
    """error ที่ตลาดตอบ: {"error": {code, message, field, hint, allowed}}"""

    def __init__(self, status: int, body: dict):
        err = body.get("error", {}) if isinstance(body, dict) else {}
        self.status = status
        self.code = err.get("code", f"HTTP_{status}")
        self.message = err.get("message", str(body))
        self.hint = err.get("hint")
        self.allowed = err.get("allowed")
        super().__init__(f"{self.code}: {self.message}" + (f" (hint: {self.hint})" if self.hint else ""))


class Market:
    def __init__(self, base_url: str, key: str, timeout: float = 10.0):
        self.http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout,
                                 headers={"Authorization": f"Bearer {key}", "User-Agent": "coffee-starter/2.0"})

    def call(self, method: str, path: str, json=None, params=None, tries: int = 4):
        for attempt in range(tries):
            try:
                r = self.http.request(method, path, json=json, params=params)
            except httpx.TransportError:            # เครือข่ายสะดุด รอแล้วลองใหม่
                if attempt == tries - 1:
                    raise
                time.sleep(1 + attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:   # 429 = เกิน 5 ครั้ง/วินาที รอตามที่ตลาดบอก
                if attempt == tries - 1:
                    break
                time.sleep(float(r.headers.get("Retry-After", 1 + attempt)))
                continue
            break
        try:
            body = r.json() if r.content else {}
        except ValueError:
            body = {"error": {"code": f"HTTP_{r.status_code}", "message": r.text[:200]}}
        if r.status_code >= 400:
            raise MarketError(r.status_code, body)
        return body

    def state(self) -> dict:
        return self.call("GET", "/v1/state")

    def calendar(self) -> dict:
        return self.call("GET", "/v1/calendar")

    def appraise(self, items: list[dict]) -> dict:
        return self.call("POST", "/v1/appraisals", json={"items": items})

    def offer_batch(self, offers: list[dict], replace_existing: bool = True) -> dict:
        return self.call("POST", "/v1/offers:batch", json={"offers": offers, "replace_existing": replace_existing})

    def offer(self, o: dict) -> dict:
        return self.call("POST", "/v1/offers", json=o)

    def reveal(self) -> dict:
        """ตลาดฝึกเท่านั้น หลังปิดตลาด: คุณภาพจริง TV จริง และคะแนน"""
        return self.call("GET", "/v1/sandbox/reveal", tries=1)
