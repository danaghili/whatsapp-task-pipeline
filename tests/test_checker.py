"""Checker catch-list matrix (INC-001 AC-1.6, offline half).

Each deliberately-broken config must produce the RIGHT red line — a plain
reason naming the fix, never a stack trace. The full-green path and the
against-real-services runs happen in the make-or-break verification.
"""

import json

import pytest
import requests as requests_lib

from whatsapp_task_pipeline import check, providers
from conftest import FakeResponse

GOOD_SENDERS = json.dumps({"441234567890": {"name": "Partner", "list": "todo.tasks_inbox"}})


@pytest.fixture
def report():
    r = check.Report()
    r.use_color = False
    return r


@pytest.fixture
def base_env(monkeypatch):
    monkeypatch.setenv("HA_URL", "http://homeassistant.local:8123")
    monkeypatch.setenv("HA_TOKEN", "a-real-looking-token")
    monkeypatch.setenv("NOTIFY_SERVICE", "notify.mobile_app_pixel")
    monkeypatch.setenv("TRUSTED_SENDERS", GOOD_SENDERS)
    monkeypatch.delenv("OLLAMA_CHAT_URL", raising=False)
    monkeypatch.setattr(providers, "CHAT_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(providers, "EMBED_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(providers, "ACCEPT_CLOUD_TEXT", "")


def out(capsys):
    return capsys.readouterr().out


# --- TRUSTED_SENDERS ---------------------------------------------------------


def test_invalid_trusted_senders_is_a_red_line(base_env, monkeypatch, report, capsys):
    monkeypatch.setenv("TRUSTED_SENDERS", "{not json")
    assert check.check_trusted_senders(report) == {}
    assert report.failed == 1
    assert "must be a JSON object" in out(capsys)


def test_missing_trusted_senders_names_the_env_line(base_env, monkeypatch, report, capsys):
    monkeypatch.setenv("TRUSTED_SENDERS", "")
    check.check_trusted_senders(report)
    assert report.failed == 1
    assert "TRUSTED_SENDERS line" in out(capsys)


# --- Home Assistant ----------------------------------------------------------


def test_bad_ha_token_is_named_without_printing_it(base_env, monkeypatch, report, capsys):
    monkeypatch.setattr(check, "_get", lambda *a, **k: FakeResponse({}, status=401))
    assert check.check_home_assistant(report) is False
    text = out(capsys)
    assert "rejected the token" in text
    assert "HA_TOKEN line" in text
    assert "a-real-looking-token" not in text  # S-1.4: validity, never the value


def test_unreachable_ha_is_a_plain_red_line(base_env, monkeypatch, report, capsys):
    def boom(*a, **k):
        raise requests_lib.ConnectionError("no route")

    monkeypatch.setattr(check, "_get", boom)
    assert check.check_home_assistant(report) is False
    assert "could not connect" in out(capsys)


def test_placeholder_token_caught_before_any_network_call(base_env, monkeypatch, report, capsys):
    monkeypatch.setenv("HA_TOKEN", "replace_me")
    assert check.check_home_assistant(report) is False
    assert "Long-lived access tokens" in out(capsys)  # says where to get one


# --- Notify service & to-do entities -----------------------------------------


def test_missing_notify_service_is_red(base_env, monkeypatch, report, capsys):
    monkeypatch.setattr(
        check, "_get",
        lambda *a, **k: FakeResponse([{"domain": "notify", "services": {"mobile_app_other": {}}}]),
    )
    check.check_notify_service(report, ha_ok=True)
    assert report.failed == 1
    assert "not a service your Home Assistant offers" in out(capsys)


def test_missing_todo_entity_says_how_to_create_it(base_env, monkeypatch, report, capsys):
    monkeypatch.setattr(check, "_get", lambda *a, **k: FakeResponse({}, status=404))
    check.check_todo_entities(report, json.loads(GOOD_SENDERS), ha_ok=True)
    assert report.failed == 1
    assert "does not exist in Home Assistant" in out(capsys)


# --- AI endpoints ------------------------------------------------------------


def test_unreachable_chat_endpoint_mentions_ollama_serve(base_env, monkeypatch, report, capsys):
    def boom(*a, **k):
        raise requests_lib.ConnectionError("refused")

    monkeypatch.setattr(check, "_get", boom)
    check.check_chat_endpoint(report)
    assert report.failed == 1
    assert "ollama serve" in out(capsys)


def test_missing_chat_model_lists_what_is_available(base_env, monkeypatch, report, capsys):
    monkeypatch.setattr(providers, "CHAT_MODEL", "qwen3:32b")
    monkeypatch.setattr(
        check, "_get", lambda *a, **k: FakeResponse({"data": [{"id": "llama3.2:1b"}]})
    )
    check.check_chat_endpoint(report)
    assert report.failed == 1
    text = out(capsys)
    assert "'qwen3:32b' is not on" in text
    assert "llama3.2:1b" in text


def test_chat_only_embeddings_endpoint_names_the_silent_trap(base_env, monkeypatch, report, capsys):
    monkeypatch.setattr(check.requests, "post", lambda *a, **k: FakeResponse({}, status=404))
    check.check_embeddings_endpoint(report)
    assert report.failed == 1
    text = out(capsys)
    assert "chat but NOT embeddings" in text
    assert "silent trap" in text


# --- The cloud guardrail flag ------------------------------------------------


def test_nonlocal_without_ack_is_a_loud_red_flag(base_env, monkeypatch, report, capsys):
    monkeypatch.setattr(providers, "CHAT_BASE_URL", "https://api.openai.com/v1")
    check.check_cloud_guardrail(report)
    assert report.failed == 1
    text = out(capsys)
    assert "api.openai.com" in text
    assert "ACCEPT_CLOUD_TEXT" in text


def test_nonlocal_with_ack_is_a_warning_not_a_failure(base_env, monkeypatch, report, capsys):
    monkeypatch.setattr(providers, "CHAT_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(providers, "ACCEPT_CLOUD_TEXT", "yes")
    check.check_cloud_guardrail(report)
    assert report.failed == 0
    text = out(capsys)
    assert "you accepted this" in text


def test_all_local_is_green(base_env, report, capsys):
    check.check_cloud_guardrail(report)
    assert report.failed == 0
    assert "no message text leaves your network" in out(capsys)


# --- Courtesy: retired settings ----------------------------------------------


def test_old_ollama_vars_get_a_migration_warning(base_env, monkeypatch, report, capsys):
    monkeypatch.setenv("OLLAMA_CHAT_URL", "http://localhost:11434")
    check.check_retired_settings(report)
    assert report.failed == 0  # warning, not failure
    assert "no longer read" in out(capsys)
