"""Redacted-by-default logging (INC-001 FR-1.6 / AC-1.7, fixes finding F-2).

The proof shape mirrors the acceptance check: run a real flow, then search
the actual log file for the known message content — present under verbose,
absent by default.
"""

from pathlib import Path

from whatsapp_task_pipeline import task_extract
from conftest import chat_reply, classification

TRUSTED = "441234567890"
SECRET_PHRASE = "pick up the anniversary ring"


def _run_add_flow(net):
    net.route("/chat/completions", chat_reply(classification(True, "high", [{"text": SECRET_PHRASE, "due_hint": None}])))
    net.route("todo/get_items", {"service_response": {"todo.tasks_inbox": {"items": []}}})
    net.route("todo/add_item", {})
    assert task_extract.handle_message(f"can you {SECRET_PHRASE}?", TRUSTED) is True


def test_default_log_holds_no_message_content(net):
    _run_add_flow(net)
    log_text = Path(task_extract.LOG_PATH).read_text()
    assert log_text.strip(), "the flow should still be logged"
    assert SECRET_PHRASE not in log_text  # the household's words stay out
    assert "redacted" in log_text  # and the redaction is visible, not silent


def test_verbose_log_restores_content(net, monkeypatch):
    monkeypatch.setattr(task_extract, "LOG_VERBOSE", True)
    _run_add_flow(net)
    log_text = Path(task_extract.LOG_PATH).read_text()
    assert SECRET_PHRASE in log_text  # explicit local opt-in for debugging


def test_parse_failure_raw_reply_redacted_by_default(net):
    net.route("/chat/completions", chat_reply(f"chatty reply mentioning {SECRET_PHRASE}, no json"))
    assert task_extract.handle_message(f"can you {SECRET_PHRASE}?", TRUSTED) is False
    log_text = Path(task_extract.LOG_PATH).read_text()
    assert SECRET_PHRASE not in log_text  # the F-2 site: raw reply on parse fail
