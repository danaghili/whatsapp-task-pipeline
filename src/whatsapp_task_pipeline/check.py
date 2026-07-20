"""wtp-check — validate a whole setup and say exactly what's wrong.

One command, one green/red line per check (INC-001 D4 / FR-1.5). This is the
"did I get it right?" feedback of the setup flow: a stranger copies
.env.example to .env, fills it in, runs this, and every misconfiguration is
named in plain words BEFORE they hit a confusing runtime failure.

Principles (from the increment spec):
  * A failing check is a red line with a plain-language reason — never a
    stack trace.
  * Secrets are reported by VALIDITY only (the token works / the key is
    missing) — their values are never printed (S-1.4).
  * The checker validates everything it can reach. It cannot confirm the
    WhatsApp bridge actually delivers events until a real message arrives —
    that prerequisite is documented in the README, not checked here.
  * The cloud guardrail is surfaced loudly (FR-1.4): a non-local endpoint
    without ACCEPT_CLOUD_TEXT is a red flag, with the ack it is a visible
    warning naming the destination.
"""

import json
import os
import sys

import requests

# The ./.env absorption happens at package import (values already in the
# environment win) — every wtp-* command gets it, not just the checker.
from . import DOTENV_LOADED

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"


class Report:
    def __init__(self):
        self.failed = 0
        self.use_color = sys.stdout.isatty()

    def _mark(self, symbol, color, label, detail):
        if self.use_color:
            print(f"  {color}{symbol}{RESET} {label}" + (f" — {detail}" if detail else ""))
        else:
            print(f"  {symbol} {label}" + (f" — {detail}" if detail else ""))

    def ok(self, label, detail=""):
        self._mark("✓", GREEN, label, detail)

    def fail(self, label, detail):
        self.failed += 1
        self._mark("✗", RED, label, detail)

    def warn(self, label, detail):
        self._mark("!", YELLOW, label, detail)


def _get(url, token=None, timeout=8):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return requests.get(url, headers=headers, timeout=timeout)


def check_trusted_senders(report) -> dict:
    raw = os.environ.get("TRUSTED_SENDERS", "").strip()
    if not raw:
        report.fail(
            "TRUSTED_SENDERS",
            "not set — add the TRUSTED_SENDERS line to .env: a JSON map of "
            'number → {"name": …, "list": …} (see .env.example)',
        )
        return {}
    try:
        senders = json.loads(raw)
        assert isinstance(senders, dict) and senders
        for num, cfg in senders.items():
            assert isinstance(cfg, dict) and cfg.get("name") and cfg.get("list")
    except (json.JSONDecodeError, AssertionError):
        report.fail(
            "TRUSTED_SENDERS",
            "set but not valid — it must be a JSON object mapping each sender "
            'number to {"name": …, "list": "todo.…"}; check quotes and braces '
            "against .env.example",
        )
        return {}
    report.ok("TRUSTED_SENDERS", f"{len(senders)} trusted sender(s) configured")
    return senders


def check_home_assistant(report) -> bool:
    ha_url = os.environ.get("HA_URL", "").rstrip("/")
    token = os.environ.get("HA_TOKEN", "")
    if not ha_url:
        report.fail("Home Assistant URL", "HA_URL is not set — add it to .env")
        return False
    if not token or token == "replace_me":
        report.fail(
            "Home Assistant token",
            "HA_TOKEN is missing — create one in Home Assistant (your profile "
            "→ Security → Long-lived access tokens) and put it on the "
            "HA_TOKEN line of .env",
        )
        return False
    try:
        r = _get(f"{ha_url}/api/", token)
    except requests.RequestException as e:
        report.fail(
            "Home Assistant reachable",
            f"could not connect to {ha_url} ({e.__class__.__name__}) — is the "
            "URL right and Home Assistant running?",
        )
        return False
    if r.status_code == 401:
        report.fail(
            "Home Assistant token",
            "Home Assistant rejected the token (HTTP 401) — the HA_TOKEN line "
            "in .env holds an invalid or expired token; create a fresh one in "
            "your profile → Security → Long-lived access tokens",
        )
        return False
    if r.status_code != 200:
        report.fail("Home Assistant reachable", f"unexpected reply HTTP {r.status_code} from {ha_url}/api/")
        return False
    report.ok("Home Assistant reachable, token valid", ha_url)
    return True


def check_notify_service(report, ha_ok):
    notify = os.environ.get("NOTIFY_SERVICE", "")
    if not notify or notify == "notify.mobile_app_your_phone":
        report.fail(
            "Phone notify service",
            "NOTIFY_SERVICE still holds the placeholder — set it to your "
            "phone's service (Home Assistant → Developer tools → Actions, "
            "search 'notify.mobile_app')",
        )
        return
    if not ha_ok:
        report.warn("Phone notify service", "skipped — fix Home Assistant connectivity first")
        return
    domain, _, service = notify.partition(".")
    try:
        r = _get(f"{os.environ.get('HA_URL', '').rstrip('/')}/api/services", os.environ.get("HA_TOKEN"))
        r.raise_for_status()
        services = {
            f"{entry.get('domain')}.{name}"
            for entry in r.json()
            for name in (entry.get("services") or {})
        }
    except (requests.RequestException, ValueError):
        report.warn("Phone notify service", "could not list Home Assistant services to verify it")
        return
    if notify in services:
        report.ok("Phone notify service exists", notify)
    else:
        report.fail(
            "Phone notify service",
            f"{notify} is not a service your Home Assistant offers — is the "
            "Companion app installed and logged in on your phone?",
        )


def check_todo_entities(report, senders, ha_ok):
    if not senders:
        return
    if not ha_ok:
        report.warn("To-do lists", "skipped — fix Home Assistant connectivity first")
        return
    ha_url = os.environ.get("HA_URL", "").rstrip("/")
    token = os.environ.get("HA_TOKEN")
    for num, cfg in senders.items():
        entity = cfg.get("list", "")
        label = f"To-do list for {cfg.get('name', num)}"
        try:
            r = _get(f"{ha_url}/api/states/{entity}", token)
        except requests.RequestException:
            report.warn(label, f"could not look up {entity}")
            continue
        if r.status_code == 200:
            report.ok(label, entity)
        else:
            report.fail(
                label,
                f"{entity} does not exist in Home Assistant — create the "
                "to-do list (Settings → Devices & services → Helpers → "
                "Create helper → To-do list) or fix the entity id in "
                "TRUSTED_SENDERS",
            )


def check_cloud_guardrail(report):
    from . import providers

    remote = providers.nonlocal_endpoints()
    if not remote:
        report.ok("Privacy: all AI endpoints are local", "no message text leaves your network")
        return
    acked = providers.ACCEPT_CLOUD_TEXT.strip().lower() in ("1", "true", "yes", "y", "on")
    for role, url in remote:
        if acked:
            report.warn(
                f"Privacy: {role} endpoint is NOT local",
                f"message text will be sent to {url} (you accepted this via "
                "ACCEPT_CLOUD_TEXT — the sender's name is stripped, the "
                "number is never sent)",
            )
        else:
            report.fail(
                f"Privacy: {role} endpoint is NOT local",
                f"{url} is outside your network and ACCEPT_CLOUD_TEXT is not "
                "set — the tool will refuse to start. Either point this "
                "endpoint at a local server, or make the deliberate choice "
                "to send message text there by setting ACCEPT_CLOUD_TEXT=yes "
                "in .env (see README: 'Using a cloud provider')",
            )


def check_chat_endpoint(report):
    from . import providers

    base = providers.CHAT_BASE_URL
    model = providers.CHAT_MODEL
    key_set = bool(providers.CHAT_API_KEY)
    try:
        r = _get(f"{base}/models", providers.CHAT_API_KEY or None, timeout=10)
    except requests.RequestException as e:
        report.fail(
            "Chat AI reachable",
            f"could not connect to {base} ({e.__class__.__name__}) — is your "
            "AI server running? (for Ollama: `ollama serve`, and note the "
            "URL must end in /v1)",
        )
        return
    if r.status_code == 401:
        detail = (
            "the endpoint rejected the API key on the CHAT_API_KEY line of .env"
            if key_set
            else "the endpoint requires an API key — set CHAT_API_KEY in .env "
            "(from your provider's dashboard)"
        )
        report.fail("Chat AI credentials", detail + " (the key itself is never printed)")
        return
    if r.status_code != 200:
        report.warn("Chat AI reachable", f"{base} answered HTTP {r.status_code} to /models; continuing")
        return
    try:
        ids = [m.get("id", "") for m in r.json().get("data", [])]
    except (ValueError, AttributeError):
        report.warn("Chat AI models", "endpoint reachable but the model list was unreadable")
        return
    if model in ids:
        report.ok("Chat AI reachable, model available", f"{model} @ {base}")
    else:
        shown = ", ".join(ids[:5]) or "none"
        report.fail(
            "Chat model available",
            f"'{model}' is not on {base} (it offers: {shown}…) — pull it "
            "(for Ollama: `ollama pull {0}`) or fix the CHAT_MODEL line".format(model),
        )


def check_embeddings_endpoint(report):
    from . import providers

    base = providers.EMBED_BASE_URL
    model = providers.EMBED_MODEL
    try:
        r = requests.post(
            f"{base}/embeddings",
            headers={"Authorization": f"Bearer {providers.EMBED_API_KEY}"} if providers.EMBED_API_KEY else {},
            json={"model": model, "input": "ping"},
            timeout=15,
        )
    except requests.RequestException as e:
        report.fail(
            "Duplicate-check (embeddings) AI reachable",
            f"could not connect to {base} ({e.__class__.__name__}) — without "
            "it the duplicate check silently switches off (tasks are never "
            "dropped, but repeats won't be caught)",
        )
        return
    if r.status_code == 404:
        report.fail(
            "Duplicate-check (embeddings) capability",
            f"{base} answers chat but NOT embeddings (HTTP 404) — this is the "
            "silent trap: your duplicate check would be off with no visible "
            "error. Point EMBED_BASE_URL at a server with an embedding model "
            f"(for Ollama: `ollama pull {model}`)",
        )
        return
    if r.status_code == 401:
        report.fail(
            "Duplicate-check (embeddings) credentials",
            "the endpoint rejected the key on the EMBED_API_KEY line of .env "
            "(the key itself is never printed)",
        )
        return
    if r.status_code != 200:
        report.fail("Duplicate-check (embeddings)", f"{base} answered HTTP {r.status_code}")
        return
    try:
        vector = r.json()["data"][0]["embedding"]
        assert isinstance(vector, list) and vector
    except (ValueError, KeyError, IndexError, AssertionError):
        report.fail(
            "Duplicate-check (embeddings) model",
            f"'{model}' did not return an embedding — is EMBED_MODEL really "
            "an embedding model (not a chat model)?",
        )
        return
    report.ok("Duplicate-check (embeddings) working", f"{model} @ {base}")


def check_retired_settings(report):
    retired = [v for v in ("OLLAMA_CHAT_URL", "OLLAMA_EMBED_URL", "CLASSIFIER_MODEL") if os.environ.get(v)]
    if retired:
        report.warn(
            "Old settings detected",
            f"{', '.join(retired)} are no longer read — the same things are "
            "now CHAT_BASE_URL / CHAT_MODEL / EMBED_BASE_URL (note the /v1 "
            "suffix; see .env.example)",
        )


def main() -> None:
    report = Report()
    print("wtp-check — validating your setup\n")
    if DOTENV_LOADED:
        print("  (reading .env from the current directory)\n")

    senders = check_trusted_senders(report)
    ha_ok = check_home_assistant(report)
    check_notify_service(report, ha_ok)
    check_todo_entities(report, senders, ha_ok)
    check_retired_settings(report)
    check_cloud_guardrail(report)
    check_chat_endpoint(report)
    check_embeddings_endpoint(report)

    print()
    if report.failed:
        print(f"{report.failed} check(s) failed — fix the red lines above and re-run.")
        sys.exit(1)
    print("All checks passed. Start the listener (wtp-listen) and schedule the reminders (wtp-remind).")
    sys.exit(0)


if __name__ == "__main__":
    main()
