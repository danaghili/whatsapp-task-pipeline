"""Shared fixtures for the routing-core suite.

The discipline here is the project's "mock external only" rule: the network
boundary (requests) is faked; everything inside the pipeline runs for real.
Tests must run offline and fast — no Ollama, no Home Assistant.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class FakeResponse:
    """Minimal stand-in for requests.Response — just what the code touches."""

    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"status {self.status_code}")


class FakeNetwork:
    """Routes fake POSTs by URL substring and records every call.

    route(substr, payload_or_callable) — first matching substring wins.
    A callable route receives (url, json_body) and returns a FakeResponse
    or raises (to simulate network failure).
    """

    def __init__(self):
        self.routes = []
        self.calls = []  # (url, json_body) tuples, in order

    def route(self, substr, handler):
        self.routes.append((substr, handler))

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json))
        for substr, handler in self.routes:
            if substr in url:
                if callable(handler):
                    return handler(url, json)
                return FakeResponse(handler)
        raise AssertionError(f"unrouted fake POST: {url}")

    def calls_to(self, substr):
        return [(u, b) for (u, b) in self.calls if substr in u]


def chat_reply(content):
    """OpenAI-style /chat/completions response envelope around a message body."""
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def classification(is_task, confidence, tasks):
    return json.dumps({"is_task": is_task, "confidence": confidence, "tasks": tasks})


@pytest.fixture
def net(monkeypatch, tmp_path):
    """A wired FakeNetwork patched into task_extract, with safe env."""
    from whatsapp_task_pipeline import providers, task_extract

    fake = FakeNetwork()
    # Both network seams are faked: HA calls leave via task_extract's requests,
    # AI calls via the provider layer's. Same fake, so ordering is preserved.
    monkeypatch.setattr(task_extract.requests, "post", fake.post)
    monkeypatch.setattr(providers.requests, "post", fake.post)
    monkeypatch.setattr(task_extract, "HA_TOKEN", "test-token")
    monkeypatch.setattr(task_extract, "LOG_PATH", str(tmp_path / "test.log"))
    monkeypatch.setattr(providers, "LOG_PATH", str(tmp_path / "test.log"))
    monkeypatch.setenv(
        "TRUSTED_SENDERS",
        json.dumps({"441234567890": {"name": "Partner", "list": "todo.tasks_inbox"}}),
    )
    return fake
