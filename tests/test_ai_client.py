import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_client import AIClient, AIConfig, AIError  # noqa: E402
from fake_server import FakeAIServer  # noqa: E402


KEY = "sk-" + "test" * 5
LEAK = "sk-" + "abcdefghijklmnopqrstuvwx"


def cfg(url, provider="openai", **kw):
    base = dict(provider=provider, model="m1", temperature=0.2, max_tokens=123, api_key=KEY, base_url=url, timeout=2)
    base.update(kw)
    return AIConfig(**base)


class OpenAIRequestTest(unittest.TestCase):
    def test_request_shape_and_params(self):
        with FakeAIServer() as srv:
            client = AIClient(cfg(srv.base_url))
            out = client.generate("SYS", "USER")
        self.assertIn("테스트 응답", out)
        self.assertEqual(client.calls, 1)
        req = srv.requests[0]
        self.assertEqual(req["path"], "/v1/chat/completions")
        self.assertEqual(req["headers"]["Authorization"], "Bearer " + KEY)
        j = req["json"]
        self.assertEqual((j["model"], j["temperature"], j["max_tokens"]), ("m1", 0.2, 123))
        self.assertEqual([m["role"] for m in j["messages"]], ["system", "user"])


class ReasoningModelRequestTest(unittest.TestCase):
    def test_gpt5_uses_max_completion_tokens_and_no_temperature(self):
        with FakeAIServer() as srv:
            AIClient(cfg(srv.base_url, model="gpt-5-mini")).generate("S", "U")
        j = srv.requests[0]["json"]
        self.assertNotIn("temperature", j)
        self.assertNotIn("max_tokens", j)
        self.assertEqual(j["max_completion_tokens"], 123 + 1500)

    def test_gpt4o_keeps_classic_params(self):
        with FakeAIServer() as srv:
            AIClient(cfg(srv.base_url, model="gpt-4o-mini")).generate("S", "U")
        j = srv.requests[0]["json"]
        self.assertEqual((j["temperature"], j["max_tokens"]), (0.2, 123))
        self.assertNotIn("max_completion_tokens", j)

    def test_anthropic_never_treated_as_reasoning(self):
        with FakeAIServer() as srv:
            AIClient(cfg(srv.base_url, provider="anthropic", model="gpt-5-x")).generate("S", "U")
        j = srv.requests[0]["json"]
        self.assertEqual((j["temperature"], j["max_tokens"]), (0.2, 123))


class AnthropicRequestTest(unittest.TestCase):
    def test_request_shape(self):
        with FakeAIServer() as srv:
            client = AIClient(cfg(srv.base_url, provider="anthropic"))
            out = client.generate("SYS", "USER")
        self.assertIn("테스트 응답", out)
        req = srv.requests[0]
        self.assertEqual(req["path"], "/v1/messages")
        headers = {k.lower(): v for k, v in req["headers"].items()}  # urllib 이 헤더명을 X-api-key 식으로 바꿔 보냄
        self.assertEqual(headers["x-api-key"], KEY)
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        j = req["json"]
        self.assertEqual((j["system"], j["max_tokens"]), ("SYS", 123))
        self.assertEqual(j["messages"], [{"role": "user", "content": "USER"}])


class ErrorHandlingTest(unittest.TestCase):
    def call(self, **srv_kw):
        with FakeAIServer(**srv_kw) as srv:
            client = AIClient(cfg(srv.base_url))
            with self.assertRaises(AIError) as cm:
                client.generate("s", "u")
        return str(cm.exception), client

    def test_401_auth_and_key_echo_is_masked(self):
        msg, client = self.call(status=401, body={"error": {"message": "Incorrect API key provided: " + LEAK + "."}})
        self.assertIn("HTTP 401", msg)
        self.assertIn("인증 실패", msg)
        self.assertNotIn(LEAK, msg)
        self.assertEqual(client.calls, 1)

    def test_429(self):
        msg, _ = self.call(status=429, body={"error": {"message": "rate limit"}})
        self.assertIn("한도", msg)

    def test_500(self):
        msg, _ = self.call(status=500, body={"error": "boom"})
        self.assertIn("서버 오류", msg)

    def test_invalid_json(self):
        msg, _ = self.call(raw_body="<html>not json</html>")
        self.assertIn("JSON", msg)

    def test_unexpected_shape(self):
        msg, _ = self.call(body={"hello": "world"})
        self.assertIn("예상한 형식", msg)

    def test_empty_content(self):
        msg, _ = self.call(body={"choices": [{"message": {"content": ""}}]})
        self.assertIn("비어 있습니다", msg)

    def test_timeout(self):
        with FakeAIServer(delay=1.0) as srv:
            client = AIClient(cfg(srv.base_url, timeout=0.3))
            with self.assertRaises(AIError) as cm:
                client.generate("s", "u")
        self.assertIn("시간 초과", str(cm.exception))

    def test_connection_refused(self):
        client = AIClient(cfg("http://127.0.0.1:1/v1", timeout=10))  # Windows 는 연결 거부 보고에 ~2초 걸림
        with self.assertRaises(AIError) as cm:
            client.generate("s", "u")
        self.assertIn("네트워크 오류", str(cm.exception))

    def test_unknown_provider(self):
        with self.assertRaises(AIError):
            AIClient(cfg("http://x", provider="nope"))


if __name__ == "__main__":
    unittest.main()
