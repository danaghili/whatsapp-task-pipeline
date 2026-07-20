"""Unit tests for the tool-side Accept/Skip handling (actions.py).

These exercise the pending store and the action resolver directly, with a
fake add_todo — no network, no Home Assistant. The design fact under test:
only the tid round-trips from the phone, so the store is what makes Accept
actually add the right task to the right list.
"""

from pathlib import Path

import pytest

from whatsapp_task_pipeline import actions


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(actions, "PENDING_PATH", Path(tmp_path / "pending.json"))
    return actions


class Recorder:
    def __init__(self):
        self.adds = []
        self.logs = []

    def add_todo(self, entity_id, text, due_hint, sender):
        self.adds.append((entity_id, text, due_hint, sender))
        return True

    def log(self, line):
        self.logs.append(line)


def _stage(store, tid="abc123"):
    store.stage(
        tid,
        text="order food",
        due_hint="tonight",
        sender="Partner",
        sender_number="441234567890",
        entity_id="todo.tasks_inbox",
    )
    return tid


def test_parse_action_recognises_prefixes(store):
    assert store.parse_action("ACCEPT_TASK_deadbeef01") == ("accept", "deadbeef01")
    assert store.parse_action("SKIP_TASK_deadbeef01") == ("skip", "deadbeef01")
    assert store.parse_action("SOME_OTHER_ACTION") == (None, None)


def test_accept_adds_the_staged_task(store):
    tid = _stage(store)
    rec = Recorder()
    handled = store.handle_action(f"ACCEPT_TASK_{tid}", add_todo=rec.add_todo, log=rec.log)
    assert handled is True
    assert rec.adds == [("todo.tasks_inbox", "order food", "tonight", "Partner")]


def test_skip_adds_nothing(store):
    tid = _stage(store)
    rec = Recorder()
    handled = store.handle_action(f"SKIP_TASK_{tid}", add_todo=rec.add_todo, log=rec.log)
    assert handled is True
    assert rec.adds == []


def test_second_tap_is_idempotent(store):
    tid = _stage(store)
    rec = Recorder()
    store.handle_action(f"ACCEPT_TASK_{tid}", add_todo=rec.add_todo, log=rec.log)
    store.handle_action(f"ACCEPT_TASK_{tid}", add_todo=rec.add_todo, log=rec.log)
    assert len(rec.adds) == 1  # pop-first means a duplicate delivery can't double-add


def test_unknown_action_is_not_ours(store):
    rec = Recorder()
    handled = store.handle_action("ACCEPT_SOMETHING_ELSE", add_todo=rec.add_todo, log=rec.log)
    assert handled is False
    assert rec.adds == []


def test_expired_tid_is_a_logged_noop(store):
    rec = Recorder()
    handled = store.handle_action("ACCEPT_TASK_neverstaged", add_todo=rec.add_todo, log=rec.log)
    assert handled is True  # recognised as ours...
    assert rec.adds == []   # ...but nothing to add
    assert any("unknown/expired" in line for line in rec.logs)


def test_ttl_prunes_stale_entries(store, monkeypatch):
    monkeypatch.setattr(store, "PENDING_TTL_SECONDS", 100)
    # Stage, then age the entry past the TTL by rewriting created_at.
    tid = _stage(store)
    data = store._load()
    data[tid]["created_at"] = store._now() - 200
    store._save(data)
    rec = Recorder()
    handled = store.handle_action(f"ACCEPT_TASK_{tid}", add_todo=rec.add_todo, log=rec.log)
    assert handled is True
    assert rec.adds == []  # pruned before it could be resolved


def test_stage_survives_reload(store):
    tid = _stage(store)
    # Simulate a fresh process reading the persisted store.
    assert tid in store._load()
