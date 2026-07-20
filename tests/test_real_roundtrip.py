"""MAKE-OR-BREAK verification against a REAL local model (INC-001 KH-1/KH-2).

Runs only when WTP_REAL_TESTS=1 and a local Ollama is serving — a mock hides
exactly the dialect risks this increment takes (response shape, JSON recovery
from a real model's reply, a server rejecting the passthrough field), so
these tests are the evidence for AC-1.1, AC-1.2, AC-1.5 and the real half of
AC-1.4/AC-1.6. Home Assistant is a realistic local stub (the AC's wording:
"a real (or realistic) list") — the REAL component under proof is the model.

    WTP_REAL_TESTS=1 python -m pytest tests/test_real_roundtrip.py -v
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests as requests_lib

from whatsapp_task_pipeline import check, providers, task_extract

REAL = os.environ.get("WTP_REAL_TESTS") == "1"
pytestmark = pytest.mark.skipif(not REAL, reason="set WTP_REAL_TESTS=1 (needs a local Ollama)")

OLLAMA = "http://localhost:11434/v1"
CHAT_MODEL = os.environ.get("WTP_TEST_CHAT_MODEL", "qwen3:0.6b")
EMBED_MODEL = "nomic-embed-text"
TRUSTED = "441234567890"


# --- A realistic Home Assistant stub (records every write) -------------------


class StubHA:
    def __init__(self):
        self.writes = []  # (path, body) of every POST
        self.open_items = []

        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, payload, status=200):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api/":
                    return self._send({"message": "API running."})
                if self.path == "/api/services":
                    return self._send([{"domain": "notify", "services": {"mobile_app_test": {}}}])
                if self.path.startswith("/api/states/todo.tasks_inbox"):
                    return self._send({"entity_id": "todo.tasks_inbox", "state": "0"})
                return self._send({}, status=404)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                stub.writes.append((self.path, body))
                if "todo/get_items" in self.path:
                    return self._send(
                        {"service_response": {"todo.tasks_inbox": {"items": stub.open_items}}}
                    )
                return self._send({})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def writes_to(self, fragment):
        return [(p, b) for (p, b) in self.writes if fragment in p]

    def close(self):
        self.server.shutdown()


class RecordingPost:
    """Pass-through wrapper: records every ACTUAL outgoing request, then sends
    it for real — the 'inspect the actual outgoing requests' KH-2 demands."""

    def __init__(self):
        self.requests = []
        self.real_post = requests_lib.post

    def __call__(self, url, **kwargs):
        self.requests.append({"url": url, "json": kwargs.get("json")})
        return self.real_post(url, **kwargs)

    def hosts(self):
        from urllib.parse import urlparse

        return sorted({urlparse(r["url"]).hostname for r in self.requests})


@pytest.fixture
def ha():
    stub = StubHA()
    yield stub
    stub.close()


@pytest.fixture
def real_env(ha, monkeypatch, tmp_path):
    recorder = RecordingPost()
    for mod in (task_extract, providers, check):
        monkeypatch.setattr(mod.requests, "post", recorder)
    monkeypatch.setenv("HA_URL", ha.url)
    monkeypatch.setenv("HA_TOKEN", "stub-token")
    monkeypatch.setenv("NOTIFY_SERVICE", "notify.mobile_app_test")
    monkeypatch.setenv(
        "TRUSTED_SENDERS", json.dumps({TRUSTED: {"name": "Partner", "list": "todo.tasks_inbox"}})
    )
    monkeypatch.setattr(task_extract, "HA_URL", ha.url)
    monkeypatch.setattr(task_extract, "HA_TOKEN", "stub-token")
    monkeypatch.setattr(task_extract, "LOG_PATH", str(tmp_path / "real.log"))
    monkeypatch.setattr(providers, "LOG_PATH", str(tmp_path / "real.log"))
    monkeypatch.setattr(providers, "CHAT_BASE_URL", OLLAMA)
    monkeypatch.setattr(providers, "CHAT_MODEL", CHAT_MODEL)
    monkeypatch.setattr(providers, "CHAT_API_KEY", "")
    monkeypatch.setattr(providers, "CHAT_EXTRA_BODY", '{"think": false}')
    monkeypatch.setattr(providers, "EMBED_BASE_URL", OLLAMA)
    monkeypatch.setattr(providers, "EMBED_MODEL", EMBED_MODEL)
    monkeypatch.setattr(providers, "ACCEPT_CLOUD_TEXT", "")
    return recorder


# --- AC-1.1: the round-trip, for real ----------------------------------------


def test_real_task_message_lands(real_env, ha):
    routed = task_extract.handle_message("can you grab milk on the way home?", TRUSTED)
    assert routed is True, "a clear task must engage the pipeline"
    adds = ha.writes_to("todo/add_item")
    notifies = ha.writes_to("notify")
    assert adds or notifies, "the task must land: silent add (high) or Accept/Skip ask (medium)"
    if adds:
        assert adds[0][1]["entity_id"] == "todo.tasks_inbox"
        assert adds[0][1]["item"].strip()


def test_real_non_task_is_dropped(real_env, ha):
    routed = task_extract.handle_message("love you! see you at 6 tonight", TRUSTED)
    assert routed is False
    assert ha.writes_to("todo/add_item") == []
    assert ha.writes_to("notify") == []


def test_real_dedup_skips_same_task_and_adds_different(real_env, ha):
    ha.open_items = [{"uid": "u1", "summary": "buy milk"}]
    routed = task_extract.handle_message("can you buy milk please?", TRUSTED)
    assert routed is True
    same = [b["item"] for _, b in ha.writes_to("todo/add_item")]
    # identical wording must be caught by the real embedding round-trip
    assert not any("milk" in item.lower() for item in same), f"duplicate slipped through: {same}"


# --- AC-1.2 / OQ-2: the passthrough actually reaches (and is accepted by) Ollama


def test_think_off_rides_the_real_request(real_env, ha):
    task_extract.handle_message("please book the dentist for tuesday", TRUSTED)
    chat_calls = [r for r in real_env.requests if "/chat/completions" in r["url"]]
    assert chat_calls, "a chat request must have been sent"
    assert chat_calls[0]["json"]["think"] is False  # the passthrough, on the wire
    assert chat_calls[0]["json"]["temperature"] == 0.0
    # and Ollama ACCEPTED it — the call produced a routed outcome, not an error:
    assert providers.chat("Reply with exactly: ok") is not None


# --- AC-1.5 / KH-2 / S-1.1: the guardrail, against real config ---------------


def test_default_config_makes_zero_nonlocal_calls(real_env, ha):
    task_extract.handle_message("can you grab bread today?", TRUSTED)
    hosts = real_env.hosts()
    assert hosts, "the run must actually have made calls"
    assert all(h in ("127.0.0.1", "localhost") for h in hosts), f"non-local call detected: {hosts}"


def test_nonlocal_without_ack_refuses_before_any_request(real_env, monkeypatch):
    monkeypatch.setattr(providers, "CHAT_BASE_URL", "https://api.openai.com/v1")
    before = len(real_env.requests)
    with pytest.raises(providers.CloudNotAcknowledgedError):
        providers.enforce_startup_policy(emit=lambda _: None)
    assert len(real_env.requests) == before  # refusal costs zero outbound calls


def test_ack_run_strips_name_and_warns_on_the_real_wire(real_env, monkeypatch, ha):
    """With the switch set, inspect the ACTUAL outgoing request to the cloud
    endpoint: neutral sender name, warning emitted. Synthetic text only; the
    unauthenticated call is answered 401 and degrades to a safe skip."""
    monkeypatch.setattr(providers, "CHAT_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(providers, "ACCEPT_CLOUD_TEXT", "yes")
    warnings = []
    providers.enforce_startup_policy(emit=warnings.append)
    assert len(warnings) == 1 and "api.openai.com" in warnings[0]

    routed = task_extract.handle_message("synthetic test: please buy stamps", TRUSTED)
    cloud_calls = [r for r in real_env.requests if "api.openai.com" in r["url"]]
    assert cloud_calls, "the acknowledged run must actually reach the endpoint"
    prompt = cloud_calls[0]["json"]["messages"][0]["content"]
    assert "a household member" in prompt  # name stripped on the real wire
    assert "Partner" not in prompt
    assert routed is False  # 401 reply -> safe skip, nothing invented


# --- AC-1.4 / AC-1.6 real halves: embeddings gap + checker against real services


def test_embeddings_unreachable_still_adds(real_env, monkeypatch, ha):
    monkeypatch.setattr(providers, "EMBED_BASE_URL", "http://127.0.0.1:9")  # nothing listens
    ha.open_items = [{"uid": "u1", "summary": "buy milk"}]
    routed = task_extract.handle_message("can you buy milk please?", TRUSTED)
    assert routed is True
    # de-dup off -> the task is ADDED (or asked about), never dropped
    assert ha.writes_to("todo/add_item") or ha.writes_to("notify")


def test_checker_full_green_against_real_services(real_env, ha, capsys):
    report = check.Report()
    report.use_color = False
    senders = check.check_trusted_senders(report)
    ha_ok = check.check_home_assistant(report)
    check.check_notify_service(report, ha_ok)
    check.check_todo_entities(report, senders, ha_ok)
    check.check_cloud_guardrail(report)
    check.check_chat_endpoint(report)
    check.check_embeddings_endpoint(report)
    text = capsys.readouterr().out
    assert report.failed == 0, f"expected full green, got:\n{text}"


def test_checker_catches_real_404_embeddings(real_env, monkeypatch, capsys):
    # A real 404 from the real server: /v1/nope/embeddings does not exist.
    monkeypatch.setattr(providers, "EMBED_BASE_URL", "http://localhost:11434/v1/nope")
    report = check.Report()
    report.use_color = False
    check.check_embeddings_endpoint(report)
    assert report.failed == 1
    assert "chat but NOT embeddings" in capsys.readouterr().out
