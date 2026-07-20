"""Frozen-behavior baseline for the routing core (finding F-1 / AC-1.3).

Written BEFORE the provider rewrite: this suite pins how handle_message
behaves today — the gate, the confidence routing, the de-dup threshold on
both sides, and every fail-open path. The rewrite is only safe while this
stays green (the seam it mocks is the network, not the internals).
"""

import math

import pytest
import requests as requests_lib

from whatsapp_task_pipeline import task_extract
from conftest import FakeResponse, chat_reply, classification

TRUSTED = "441234567890"
UNKNOWN = "440000000001"


def unit(x):
    """A 2-d unit vector [x, sqrt(1-x^2)] — cosine against [1,0] is exactly x."""
    return [x, math.sqrt(1.0 - x * x)]


# --- The trusted-sender gate -------------------------------------------------


def test_unknown_sender_is_ignored_before_any_call(net):
    assert task_extract.handle_message("can you grab milk", UNKNOWN) is False
    assert net.calls == []  # nothing left the process — no model, no HA


def test_empty_message_is_ignored(net):
    assert task_extract.handle_message("   ", TRUSTED) is False
    assert net.calls == []


# --- Confidence routing ------------------------------------------------------


def test_high_confidence_task_is_added(net):
    net.route("/chat/completions", chat_reply(classification(True, "high", [{"text": "pick up milk", "due_hint": None}])))
    net.route("todo/get_items", {"service_response": {"todo.tasks_inbox": {"items": []}}})
    net.route("todo/add_item", {})

    assert task_extract.handle_message("can you grab milk", TRUSTED) is True
    adds = net.calls_to("todo/add_item")
    assert len(adds) == 1
    assert adds[0][1]["item"] == "pick up milk"
    assert adds[0][1]["entity_id"] == "todo.tasks_inbox"
    assert net.calls_to("notify") == []  # high never pings the phone


def test_medium_confidence_sends_actionable_notification(net):
    net.route("/chat/completions", chat_reply(classification(True, "medium", [{"text": "order food", "due_hint": "tonight"}])))
    net.route("notify", {})

    assert task_extract.handle_message("maybe order food tonight?", TRUSTED) is True
    assert net.calls_to("todo/add_item") == []  # medium never writes directly
    notifies = net.calls_to("notify")
    assert len(notifies) == 1
    actions = notifies[0][1]["data"]["actions"]
    assert actions[0]["action"].startswith("ACCEPT_TASK_")
    assert actions[1]["action"].startswith("SKIP_TASK_")
    # The whole pending decision travels in the payload (stateless by design).
    assert notifies[0][1]["data"]["task"]["text"] == "order food"
    assert notifies[0][1]["data"]["task"]["entity_id"] == "todo.tasks_inbox"


def test_low_confidence_is_dropped(net):
    net.route("/chat/completions", chat_reply(classification(True, "low", [{"text": "x", "due_hint": None}])))
    assert task_extract.handle_message("did you do the thing?", TRUSTED) is False
    assert net.calls_to("todo/add_item") == []
    assert net.calls_to("notify") == []


def test_not_a_task_is_dropped(net):
    net.route("/chat/completions", chat_reply(classification(False, "high", [])))
    assert task_extract.handle_message("love you, see you at 6", TRUSTED) is False
    assert net.calls_to("todo/add_item") == []


def test_multi_task_message_adds_each(net):
    net.route(
        "/chat/completions",
        chat_reply(
            classification(
                True,
                "high",
                [{"text": "get milk", "due_hint": None}, {"text": "get bread", "due_hint": None}],
            )
        ),
    )
    net.route("todo/get_items", {"service_response": {"todo.tasks_inbox": {"items": []}}})
    net.route("todo/add_item", {})

    assert task_extract.handle_message("get milk and bread", TRUSTED) is True
    assert [c[1]["item"] for c in net.calls_to("todo/add_item")] == ["get milk", "get bread"]


# --- The de-dup threshold, both sides of 0.85 --------------------------------


def _dedup_setup(net, similarity):
    """One open item; embeddings crafted so cosine(new, open) == similarity."""
    net.route("/chat/completions", chat_reply(classification(True, "high", [{"text": "buy milk", "due_hint": None}])))
    net.route(
        "todo/get_items",
        {"service_response": {"todo.tasks_inbox": {"items": [{"uid": "u1", "summary": "pick up milk"}]}}},
    )
    net.route("todo/add_item", {})
    vectors = {"buy milk": unit(1.0), "pick up milk": unit(similarity)}
    net.route("/embeddings", lambda url, body: FakeResponse({"data": [{"embedding": vectors[body["input"]]}]}))


def test_just_above_threshold_is_duplicate_and_skipped(net):
    _dedup_setup(net, similarity=0.86)  # threshold is 0.85
    assert task_extract.handle_message("buy milk", TRUSTED) is True  # engaged, not added
    assert net.calls_to("todo/add_item") == []


def test_just_below_threshold_is_added(net):
    _dedup_setup(net, similarity=0.84)
    assert task_extract.handle_message("buy milk", TRUSTED) is True
    assert len(net.calls_to("todo/add_item")) == 1


# --- Fail-open paths: infrastructure failure never eats a task ---------------


def _raise_network(url, body):
    raise requests_lib.ConnectionError("boom")


def test_model_unreachable_is_a_safe_false(net):
    net.route("/chat/completions", _raise_network)
    assert task_extract.handle_message("can you grab milk", TRUSTED) is False
    assert net.calls_to("todo/add_item") == []  # nothing invented


def test_malformed_model_reply_is_a_safe_skip(net):
    net.route("/chat/completions", chat_reply("sure thing, happy to help! no json here"))
    assert task_extract.handle_message("can you grab milk", TRUSTED) is False
    assert net.calls_to("todo/add_item") == []


def test_dedup_fetch_failure_adds_anyway(net):
    net.route("/chat/completions", chat_reply(classification(True, "high", [{"text": "buy milk", "due_hint": None}])))
    net.route("todo/get_items", _raise_network)
    net.route("todo/add_item", {})
    assert task_extract.handle_message("buy milk", TRUSTED) is True
    assert len(net.calls_to("todo/add_item")) == 1  # a dupe costs one tap; a drop costs more


def test_embed_failure_adds_anyway(net):
    net.route("/chat/completions", chat_reply(classification(True, "high", [{"text": "buy milk", "due_hint": None}])))
    net.route(
        "todo/get_items",
        {"service_response": {"todo.tasks_inbox": {"items": [{"uid": "u1", "summary": "pick up milk"}]}}},
    )
    net.route("/embeddings", _raise_network)
    net.route("todo/add_item", {})
    assert task_extract.handle_message("buy milk", TRUSTED) is True
    assert len(net.calls_to("todo/add_item")) == 1


# --- JSON recovery (the soft contract) ---------------------------------------


def test_extract_json_strips_markdown_fences():
    got = task_extract._extract_json('```json\n{"is_task": true}\n```')
    assert got == {"is_task": True}


def test_extract_json_recovers_from_chatty_reply():
    got = task_extract._extract_json('Sure! Here is the JSON:\n{"is_task": false, "confidence": "low", "tasks": []}\nHope that helps!')
    assert got == {"is_task": False, "confidence": "low", "tasks": []}


def test_extract_json_returns_none_on_garbage():
    assert task_extract._extract_json("no braces at all") is None
    assert task_extract._extract_json("{broken: json") is None
