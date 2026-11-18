"""llm.py · market.py · config.py · main.log: client ของ LLM และตลาด (ไม่ต่อเน็ตจริง)"""
import json
import unittest
from unittest import mock

import helpers
from helpers import TMP

import httpx

import config as cfg
import llm
import main
import market


class ParseJson(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(llm.parse_json('{"a": 1}'), {"a": 1})
        self.assertEqual(llm.parse_json('```json\n{"a": 2}\n```'), {"a": 2})
        self.assertEqual(llm.parse_json('<think>hmm {"x": 0}</think>{"a": 3}'), {"a": 3})
        self.assertEqual(llm.parse_json('answer: {"a": 4} done'), {"a": 4})
        self.assertIsNone(llm.parse_json("no json here"))
        self.assertIsNone(llm.parse_json("[1, 2]"))

    def test_ask_json_never_raises(self):
        client = llm.LLM("http://127.0.0.1:9/v1", "m", "k", timeout=1)
        with mock.patch.object(client.client.chat.completions, "create", side_effect=RuntimeError("boom")):
            self.assertIsNone(client.ask_json("s", "u", {}))
        self.assertIn("boom", client.last_error)


class LlmUrl(unittest.TestCase):
    def test_strips_completion_path(self):
        self.assertEqual(cfg._llm_url("http://h:4000/v1/chat/completions"), "http://h:4000/v1")
        self.assertEqual(cfg._llm_url("http://h:4000/v1/"), "http://h:4000/v1")
        self.assertEqual(cfg._llm_url(None), "")

    def test_log_dir_is_temporary(self):
        self.assertEqual(cfg.LOGS.resolve(), TMP.resolve())        # เทสต์ไม่เขียนลง logs/ จริง


def fake_market(handler):
    m = market.Market("http://market.test", "sk-test")
    m.http = httpx.Client(base_url="http://market.test", transport=httpx.MockTransport(handler))
    return m


class MarketClient(unittest.TestCase):
    def test_success_and_auth_header(self):
        seen = {}

        def handler(request):
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"phase": "ROUND_OPEN"})
        m = market.Market("http://market.test", "sk-test")
        m.http = httpx.Client(base_url="http://market.test", transport=httpx.MockTransport(handler),
                              headers=m.http.headers)
        self.assertEqual(m.state(), {"phase": "ROUND_OPEN"})
        self.assertEqual(seen["auth"], "Bearer sk-test")

    def test_error_body_becomes_market_error(self):
        m = fake_market(lambda r: httpx.Response(409, json={"error": {"code": "APPRAISAL_ALREADY_SUBMITTED",
                                                                      "message": "done", "hint": "once"}}))
        with self.assertRaises(market.MarketError) as e:
            m.appraise([])
        self.assertEqual((e.exception.status, e.exception.code), (409, "APPRAISAL_ALREADY_SUBMITTED"))

    def test_retries_429_then_succeeds(self):
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(429, headers={"Retry-After": "0"}) if len(calls) == 1 else httpx.Response(200, json={})
        with mock.patch.object(market.time, "sleep"):
            self.assertEqual(fake_market(handler).calendar(), {})
        self.assertEqual(len(calls), 2)

    def test_batch_payload(self):
        body = {}

        def handler(request):
            body.update(json.loads(request.content))
            return httpx.Response(200, json={"offers": []})
        fake_market(handler).offer_batch([{"rfq_id": "R1-1"}])
        self.assertEqual(body, {"offers": [{"rfq_id": "R1-1"}], "replace_existing": True})


class MainLog(unittest.TestCase):
    def test_log_writes_jsonl_to_log_dir(self):
        with mock.patch("builtins.print"):
            main.log("unit_test_event", value=1)
        rows = [json.loads(x) for x in (TMP / "decisions.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(r["event"] == "unit_test_event" and r["value"] == 1 for r in rows))


if __name__ == "__main__":
    unittest.main()
