"""Provider-layer tests: request shapes and the cloud guardrail.

These prove the universal request style is formed correctly and the Option A
guardrail logic holds at the unit level. They deliberately do NOT stand in
for the make-or-break real-model round-trip (INC-001 KH-1) — a mock can't
catch a real server rejecting a field. That proof runs against live Ollama.
"""

import pytest
import requests as requests_lib

from whatsapp_task_pipeline import providers
from conftest import FakeResponse


@pytest.fixture
def pnet(monkeypatch, tmp_path):
    """Capture provider-layer POSTs."""
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers or {}, "json": json})
        if "/chat/completions" in url:
            return FakeResponse({"choices": [{"message": {"content": "ok"}}]})
        if "/embeddings" in url:
            return FakeResponse({"data": [{"embedding": [1.0, 0.0]}]})
        raise AssertionError(f"unrouted provider POST: {url}")

    monkeypatch.setattr(providers.requests, "post", fake_post)
    monkeypatch.setattr(providers, "LOG_PATH", str(tmp_path / "test.log"))
    return calls


# --- Request shapes (the universal style) ------------------------------------


def test_chat_posts_openai_style(pnet, monkeypatch):
    monkeypatch.setattr(providers, "CHAT_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(providers, "CHAT_MODEL", "test-model")
    monkeypatch.setattr(providers, "CHAT_API_KEY", "")
    monkeypatch.setattr(providers, "CHAT_EXTRA_BODY", "")

    assert providers.chat("hello") == "ok"
    call = pnet[0]
    assert call["url"] == "http://localhost:11434/v1/chat/completions"
    assert call["json"]["model"] == "test-model"
    assert call["json"]["messages"] == [{"role": "user", "content": "hello"}]
    assert call["json"]["temperature"] == 0.0
    assert "Authorization" not in call["headers"]  # no key -> no header


def test_chat_sends_bearer_key_when_configured(pnet, monkeypatch):
    monkeypatch.setattr(providers, "CHAT_API_KEY", "sk-secret")
    providers.chat("hello")
    assert pnet[0]["headers"]["Authorization"] == "Bearer sk-secret"


def test_extra_body_passthrough_carries_think_off(pnet, monkeypatch):
    monkeypatch.setattr(providers, "CHAT_EXTRA_BODY", '{"think": false}')
    providers.chat("hello")
    assert pnet[0]["json"]["think"] is False


def test_extra_body_absent_by_default(pnet, monkeypatch):
    monkeypatch.setattr(providers, "CHAT_EXTRA_BODY", "")
    providers.chat("hello")
    assert "think" not in pnet[0]["json"]  # absence never breaks a provider


def test_malformed_extra_body_is_ignored_not_fatal(pnet, monkeypatch):
    monkeypatch.setattr(providers, "CHAT_EXTRA_BODY", "not json{")
    assert providers.chat("hello") == "ok"
    assert "think" not in pnet[0]["json"]


def test_embed_posts_openai_style_and_parses_vector(pnet, monkeypatch):
    monkeypatch.setattr(providers, "EMBED_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(providers, "EMBED_MODEL", "embed-model")
    assert providers.embed("some text") == [1.0, 0.0]
    call = pnet[0]
    assert call["url"] == "http://localhost:11434/v1/embeddings"
    assert call["json"] == {"model": "embed-model", "input": "some text"}


def test_chat_failure_returns_none(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise requests_lib.ConnectionError("down")

    monkeypatch.setattr(providers.requests, "post", boom)
    monkeypatch.setattr(providers, "LOG_PATH", str(tmp_path / "t.log"))
    assert providers.chat("hello") is None
    assert providers.embed("hello") is None


def test_unexpected_response_shape_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(
        providers.requests, "post", lambda *a, **k: FakeResponse({"weird": True})
    )
    monkeypatch.setattr(providers, "LOG_PATH", str(tmp_path / "t.log"))
    assert providers.chat("hello") is None
    assert providers.embed("hello") is None


# --- The local / non-local boundary (INC-001 OQ-1) ---------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://localhost:11434/v1", True),
        ("http://127.0.0.1:11434/v1", True),
        ("http://[::1]:11434/v1", True),
        ("http://192.168.1.20:11434/v1", True),
        ("http://10.0.0.5:8080/v1", True),
        ("http://172.16.0.1/v1", True),
        ("http://172.31.255.254/v1", True),
        ("http://169.254.10.10/v1", True),
        ("http://homeassistant.local:8123/v1", True),
        ("http://server.lan/v1", True),
        ("http://box.home/v1", True),
        ("http://ai.internal/v1", True),
        ("http://mygpubox:11434/v1", True),  # bare single-label = LAN machine
        ("http://100.125.68.127:11434/v1", True),  # Tailscale CGNAT = own mesh (D-0017)
        ("http://100.64.0.0/v1", True),  # CGNAT range start
        ("http://100.127.255.254/v1", True),  # CGNAT range end
        ("http://100.128.0.1/v1", False),  # just past the CGNAT range
        ("http://ollama.tail1234.ts.net:11434/v1", True),  # Tailscale MagicDNS
        ("https://api.openai.com/v1", False),
        ("https://openrouter.ai/api/v1", False),
        ("http://172.32.0.1/v1", False),  # just past the RFC1918 172 range
        ("http://8.8.8.8/v1", False),
        ("https://my-ollama.example.com/v1", False),  # public domain = non-local
        ("", False),  # unparseable errs toward the guardrail
    ],
)
def test_local_boundary(url, expected):
    assert providers.is_local_endpoint(url) is expected


# --- The guardrail itself (INC-001 D2 / FR-1.4) ------------------------------


def test_all_local_policy_is_silent(monkeypatch):
    monkeypatch.setattr(providers, "CHAT_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(providers, "EMBED_BASE_URL", "http://192.168.1.5:11434/v1")
    emitted = []
    providers.enforce_startup_policy(emit=emitted.append)
    assert emitted == []


def test_nonlocal_without_ack_refuses_to_start(monkeypatch):
    monkeypatch.setattr(providers, "CHAT_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(providers, "ACCEPT_CLOUD_TEXT", "")
    with pytest.raises(providers.CloudNotAcknowledgedError) as exc:
        providers.enforce_startup_policy(emit=lambda _: None)
    assert "api.openai.com" in str(exc.value)
    assert "ACCEPT_CLOUD_TEXT" in str(exc.value)  # tells the user the exact fix


def test_nonlocal_with_ack_warns_and_names_destination(monkeypatch):
    monkeypatch.setattr(providers, "CHAT_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(providers, "EMBED_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(providers, "ACCEPT_CLOUD_TEXT", "yes")
    emitted = []
    providers.enforce_startup_policy(emit=emitted.append)
    assert len(emitted) == 1
    assert "api.openai.com" in emitted[0]
    assert "WARNING" in emitted[0]


def test_sender_name_stripped_for_nonlocal_chat(monkeypatch):
    monkeypatch.setattr(providers, "CHAT_BASE_URL", "https://api.openai.com/v1")
    assert providers.outbound_sender_name("Partner") == providers.NEUTRAL_SENDER


def test_sender_name_kept_for_local_chat(monkeypatch):
    monkeypatch.setattr(providers, "CHAT_BASE_URL", "http://localhost:11434/v1")
    assert providers.outbound_sender_name("Partner") == "Partner"
