import base64
import unittest
from types import SimpleNamespace as NS

from project.backend.app.core.capabilities import IMAGE_GEN
from project.backend.app.logging_utils import REDACTOR
from project.backend.app.providers.anthropic_adapter import AnthropicAdapter
from project.backend.app.providers.base import (CredentialStore, GenerationRequest, ProviderError, UnsupportedCapability, classify_exception)
from project.backend.app.providers.demo import DemoProvider
from project.backend.app.providers.gemini_adapter import GeminiAdapter
from project.backend.app.providers.openai_adapter import OpenAIAdapter

REQ = GenerationRequest(system="sys", prompt="hello", json_mode=True, max_output_tokens=50)


class HttpErr(Exception):
    def __init__(self, status, msg="boom"):
        super().__init__(msg)
        self.status_code = status


def creds(**keys):
    c = CredentialStore(env={})
    for k, v in keys.items():
        c.set_session_key(k, v)
    return c


class FakeOpenAI:
    def __init__(self, behaviour=None):
        self.behaviour, self.kwargs = behaviour, None
        self.responses = NS(create=self._create)
        self.images = NS(generate=lambda **kw: NS(data=[NS(b64_json=base64.b64encode(b"PNGDATA").decode())]))
        self.models = NS(list=lambda: [NS(id="m1"), NS(id="m2")])

    def _create(self, **kw):
        self.kwargs = kw
        if self.behaviour:
            raise self.behaviour
        return NS(output_text='{"ok": true}', usage=NS(input_tokens=11, output_tokens=7), status="completed")


class AdapterTests(unittest.TestCase):
    def test_openai_success_and_json_mode(self):
        fake = FakeOpenAI()
        a = OpenAIAdapter(creds(openai="sk-test-12345678"), client_factory=lambda k: fake)
        r = a.generate_text(REQ)
        self.assertEqual((r.text, r.input_tokens, r.output_tokens, r.simulated), ('{"ok": true}', 11, 7, False))
        self.assertEqual(fake.kwargs["text"], {"format": {"type": "json_object"}})
        self.assertTrue(a.last_ok)

    def test_openai_image(self):
        a = OpenAIAdapter(creds(openai="sk-test-12345678"), client_factory=lambda k: FakeOpenAI())
        self.assertIn(IMAGE_GEN, a.capabilities)
        self.assertEqual(a.generate_image("cat").data, b"PNGDATA")

    def test_image_capability_requires_configured_model(self):
        a = OpenAIAdapter(creds(openai="sk-test-12345678"), image_model="", client_factory=lambda k: FakeOpenAI())
        self.assertNotIn(IMAGE_GEN, a.capabilities)
        with self.assertRaises(UnsupportedCapability):
            a.generate_image("cat")

    def test_error_classification(self):
        cases = [(HttpErr(401), "auth", False), (HttpErr(403), "auth", False), (HttpErr(429, "slow down"), "rate_limit", True),
                 (HttpErr(429, "You exceeded your current quota, check billing"), "quota", False),
                 (HttpErr(503), "unavailable", True), (HttpErr(400), "bad_request", False), (TimeoutError("timed out"), "timeout", True)]
        for exc, code, retry in cases:
            a = OpenAIAdapter(creds(openai="sk-test-12345678"), client_factory=lambda k, e=exc: FakeOpenAI(e))
            with self.assertRaises(ProviderError) as cm:
                a.generate_text(REQ)
            self.assertEqual((cm.exception.code, cm.exception.retryable), (code, retry), str(exc))
            self.assertFalse(a.last_ok)
            self.assertEqual(a.last_error_code, code)

    def test_not_configured(self):
        a = OpenAIAdapter(CredentialStore(env={}), client_factory=lambda k: FakeOpenAI())
        self.assertFalse(a.is_configured())
        with self.assertRaises(ProviderError) as cm:
            a.generate_text(REQ)
        self.assertEqual(cm.exception.code, "not_configured")

    def test_empty_output_is_bad_response(self):
        fake = FakeOpenAI()
        fake.responses = NS(create=lambda **kw: NS(output_text="", usage=None))
        a = OpenAIAdapter(creds(openai="sk-test-12345678"), client_factory=lambda k: fake)
        with self.assertRaises(ProviderError) as cm:
            a.generate_text(REQ)
        self.assertEqual(cm.exception.code, "bad_response")

    def test_anthropic_success_parses_blocks_and_adds_json_instruction(self):
        seen = {}

        def create(**kw):
            seen.update(kw)
            return NS(content=[NS(type="text", text="Hello "), NS(type="tool_use"), NS(type="text", text="world")],
                      usage=NS(input_tokens=5, output_tokens=2), stop_reason="end_turn")
        fake = NS(messages=NS(create=create), models=NS(list=lambda limit=50: [NS(id="c1")]))
        a = AnthropicAdapter(creds(anthropic="sk-ant-test-12345678"), client_factory=lambda k: fake)
        r = a.generate_text(REQ)
        self.assertEqual((r.text, r.input_tokens), ("Hello world", 5))
        self.assertIn("JSON", seen["system"])
        self.assertEqual(seen["max_tokens"], 50)

    def test_anthropic_has_no_image_generation(self):
        a = AnthropicAdapter(creds(anthropic="sk-ant-test-12345678"), client_factory=lambda k: NS())
        self.assertNotIn(IMAGE_GEN, a.capabilities)
        with self.assertRaises(UnsupportedCapability):
            a.generate_image("x")

    def test_anthropic_auth_error(self):
        def create(**kw):
            raise HttpErr(401, "invalid x-api-key")
        a = AnthropicAdapter(creds(anthropic="sk-ant-test-12345678"), client_factory=lambda k: NS(messages=NS(create=create)))
        with self.assertRaises(ProviderError) as cm:
            a.generate_text(REQ)
        self.assertEqual(cm.exception.code, "auth")

    def test_gemini_text_and_usage(self):
        calls = {}

        def gen(model, contents, config):
            calls.update(model=model, contents=contents)
            return NS(text="gem", usage_metadata=NS(prompt_token_count=3, candidates_token_count=4))
        fake = NS(models=NS(generate_content=gen, list=lambda: [NS(name="models/x")]))
        a = GeminiAdapter(creds(gemini="AIzaTestKey1234567890abcd"), client_factory=lambda k: fake)
        try:
            from google.genai import types  # noqa: F401
        except ImportError:
            self.skipTest("google-genai SDK not installed: config construction needs it (adapter converts this into a ProviderError)")
        r = a.generate_text(REQ)
        self.assertEqual((r.text, r.input_tokens, r.output_tokens), ("gem", 3, 4))

    def test_gemini_missing_sdk_is_reported_not_crashed(self):
        try:
            import google.genai  # noqa: F401
            self.skipTest("SDK installed")
        except ImportError:
            pass
        a = GeminiAdapter(creds(gemini="AIzaTestKey1234567890abcd"), client_factory=lambda k: NS())
        with self.assertRaises(ProviderError) as cm:
            a.generate_text(REQ)
        self.assertEqual(cm.exception.code, "unavailable")

    def test_gemini_image_extraction(self):
        part = NS(inline_data=NS(data=b"IMG", mime_type="image/png"))
        fake = NS(models=NS(generate_content=lambda **kw: NS(candidates=[NS(content=NS(parts=[NS(inline_data=None), part]))])))
        a = GeminiAdapter(creds(gemini="AIzaTestKey1234567890abcd"), image_model="some-image-model", client_factory=lambda k: fake)
        try:
            from google.genai import types  # noqa: F401
        except ImportError:
            self.skipTest("google-genai SDK not installed")
        self.assertEqual(a.generate_image("p").data, b"IMG")

    def test_check_connection_uses_model_list_and_never_exposes_key(self):
        a = OpenAIAdapter(creds(openai="sk-supersecretkey-1234567890"), client_factory=lambda k: FakeOpenAI())
        s = a.check_connection()
        self.assertEqual(s.connection, "ok")
        self.assertNotIn("supersecret", repr(s.to_dict()))
        self.assertEqual(s.credential_source, "session")

    def test_check_connection_reports_auth_failure(self):
        fake = FakeOpenAI()
        fake.models = NS(list=lambda: (_ for _ in ()).throw(HttpErr(401, "bad key sk-supersecretkey-1234567890")))
        a = OpenAIAdapter(creds(openai="sk-supersecretkey-1234567890"), client_factory=lambda k: fake)
        s = a.check_connection()
        self.assertEqual(s.connection, "error")
        self.assertEqual(s.last_error_code, "auth")
        self.assertNotIn("supersecret", s.last_error or "")      # key redacted out of error text

    def test_credentials_validation_and_env(self):
        c = CredentialStore(env={"ANTHROPIC_API_KEY": "sk-ant-envkey-12345678"})
        self.assertEqual(c.source("anthropic"), "env")
        c.set_session_key("anthropic", "sk-ant-session-12345678")
        self.assertEqual((c.source("anthropic"), c.get("anthropic")), ("session", "sk-ant-session-12345678"))
        c.clear_session_key("anthropic")
        self.assertEqual(c.source("anthropic"), "env")
        with self.assertRaises(ValueError):
            c.set_session_key("openai", "has space")

    def test_demo_provider_is_always_simulated(self):
        d = DemoProvider(CredentialStore(env={}))
        r = d.generate_text(GenerationRequest("s", "p", metadata={"task_kind": "writing", "task_title": "T"}))
        self.assertTrue(r.simulated)
        self.assertIn("SIMULATED", r.text)
        self.assertTrue(d.generate_image("x").simulated)
        self.assertFalse(d.status().may_charge)


if __name__ == "__main__":
    unittest.main()
