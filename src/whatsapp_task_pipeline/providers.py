"""The provider layer: one universal way to talk to any chat/embeddings AI.

Both AI calls (classification chat, de-dup embeddings) speak the common
OpenAI-style HTTP format that local servers (Ollama, LM Studio, llama.cpp)
and cloud providers (OpenAI, OpenRouter, …) all accept:

    POST {BASE_URL}/chat/completions   {"model", "messages", ...}
    POST {BASE_URL}/embeddings         {"model", "input"}

configured by three settings per role: a base URL, an optional API key, and
a model name (INC-001 D1). Ollama is reached through its own OpenAI-compatible
surface (http://localhost:11434/v1), so nothing local is lost.

Provider-specific tuning rides an optional JSON passthrough (CHAT_EXTRA_BODY,
merged verbatim into the chat request body — INC-001 FR-1.2 / OQ-2). It exists
for exactly one known case: Ollama's "think": false switch, which measurably
helps small local models on short structured-output tasks. It defaults to
empty, and an empty passthrough never breaks any provider.

The cloud guardrail (INC-001 D2 / FR-1.4 — the code-level protection for the
recorded intolerable event, "private message text leaking"):

  * Endpoints default to local (Ollama on localhost).
  * A non-local endpoint refuses to run until ACCEPT_CLOUD_TEXT is set — a
    one-time, eyes-open acknowledgment that message text will leave the house.
  * Every process start against a non-local endpoint emits one warning line
    naming exactly where text is going.
  * The sender's name is replaced with a neutral placeholder in anything sent
    to a non-local endpoint (the sender's number never reaches any model).
  * No free-text scrubbing is attempted — see INC-001 out-of-scope: a scrubber
    that misses things is worse than an honest boundary.

"Local" (INC-001 OQ-1, verified under KH-2): loopback addresses and names,
RFC 1918 private ranges, link-local, .local/.lan/.home/.internal suffixes,
and single-label hostnames (a bare "mygpubox" is a LAN machine). Everything
else — public domains, public IPs — is non-local and triggers the guardrail.
"""

import ipaddress
import json
import os
import time
from typing import Optional
from urllib.parse import urlparse

import requests

# --- Configuration (all via environment; see .env.example) -------------------

CHAT_BASE_URL = os.environ.get("CHAT_BASE_URL", "http://localhost:11434/v1").rstrip("/")
CHAT_API_KEY = os.environ.get("CHAT_API_KEY", "")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "qwen3:32b")

EMBED_BASE_URL = os.environ.get("EMBED_BASE_URL", "http://localhost:11434/v1").rstrip("/")
EMBED_API_KEY = os.environ.get("EMBED_API_KEY", "")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")

# Provider-specific chat options, merged verbatim into the request body.
# Documented use: CHAT_EXTRA_BODY={"think": false} against Ollama. Leave unset
# for cloud providers — OpenAI-style APIs may reject unknown fields.
CHAT_EXTRA_BODY = os.environ.get("CHAT_EXTRA_BODY", "")

# The one-time cloud acknowledgment (INC-001 D2). Setting this to any truthy
# value ("yes", "true", "1") records that the operator knowingly accepts their
# message text being sent to the non-local endpoint(s) they configured.
ACCEPT_CLOUD_TEXT = os.environ.get("ACCEPT_CLOUD_TEXT", "")

# Neutral placeholder used instead of the sender's real name on non-local sends.
NEUTRAL_SENDER = "a household member"

_LOCAL_SUFFIXES = (".local", ".lan", ".home", ".internal")


class CloudNotAcknowledgedError(RuntimeError):
    """Raised when a non-local endpoint is configured without ACCEPT_CLOUD_TEXT.

    Deliberately fail-closed and loud: the process must not run in a state
    where message text could leave the network without the operator having
    said yes once, explicitly.
    """


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


def is_local_endpoint(url: str) -> bool:
    """Classify an endpoint URL as local (LAN/host) or non-local (internet).

    The boundary (INC-001 OQ-1): loopback, RFC 1918 private, link-local, and
    unique-local IPs are local; so are loopback names, .local/.lan/.home/
    .internal suffixes, and single-label hostnames. Anything unparseable is
    treated as NON-local — when unsure, the guardrail must err toward asking.
    """
    try:
        host = urlparse(url).hostname
    except ValueError:
        return False
    if not host:
        return False
    host = host.lower().rstrip(".")
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private or ip.is_link_local
    except ValueError:
        pass  # not an IP literal — judge the hostname shape
    if host.endswith(_LOCAL_SUFFIXES):
        return True
    if "." not in host:  # bare single-label name, e.g. "mygpubox"
        return True
    return False


def endpoint_summary() -> list:
    """[(role, base_url, is_local)] for every configured AI endpoint."""
    return [
        ("chat", CHAT_BASE_URL, is_local_endpoint(CHAT_BASE_URL)),
        ("embeddings", EMBED_BASE_URL, is_local_endpoint(EMBED_BASE_URL)),
    ]


def nonlocal_endpoints() -> list:
    return [(role, url) for role, url, local in endpoint_summary() if not local]


def enforce_startup_policy(emit=print) -> None:
    """Apply the cloud guardrail at process start (INC-001 FR-1.4).

    Non-local endpoint + no acknowledgment -> refuse to run (raises).
    Non-local endpoint + acknowledgment    -> one warning line per endpoint,
    naming exactly where message text will be sent. All-local -> silent.
    """
    remote = nonlocal_endpoints()
    if not remote:
        return
    if not _truthy(ACCEPT_CLOUD_TEXT):
        names = ", ".join(f"{role}: {url}" for role, url in remote)
        raise CloudNotAcknowledgedError(
            "Refusing to start: a non-local AI endpoint is configured "
            f"({names}) but ACCEPT_CLOUD_TEXT is not set. Sending household "
            "message text to an outside service is the one thing this tool "
            "otherwise protects against. If you accept that trade, set "
            "ACCEPT_CLOUD_TEXT=yes in your .env (a one-time, deliberate "
            "choice). See README: 'Using a cloud provider'."
        )
    for role, url in remote:
        emit(
            f"[cloud] WARNING: {role} messages will be sent to non-local "
            f"endpoint {url} (ACCEPT_CLOUD_TEXT is set)."
        )


def outbound_sender_name(sender_name: str) -> str:
    """The sender name as it may appear in model requests.

    Real name for local endpoints; a neutral placeholder when the chat
    endpoint is non-local (INC-001 D2 — strip the structured 'who', honestly
    leave the 'what'). The sender's number never reaches any model.
    """
    if is_local_endpoint(CHAT_BASE_URL):
        return sender_name
    return NEUTRAL_SENDER


def _headers(api_key: str) -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _extra_body() -> dict:
    """Parse the passthrough; a malformed value is ignored loudly, not fatally."""
    raw = CHAT_EXTRA_BODY.strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    _log("[providers] CHAT_EXTRA_BODY is not a JSON object; ignoring it")
    return {}


LOG_PATH = os.path.expanduser(os.environ.get("TASK_LOG_PATH", "~/task_pipeline.log"))


def _log(line: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
    except Exception:
        pass


def chat(prompt: str, timeout: int = 60) -> Optional[str]:
    """One chat completion via the universal style. Returns the reply text,
    or None on any failure (logged) — the caller treats None as a safe skip."""
    body = {
        "model": CHAT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        # temperature 0 for determinism — the classifier wants the most
        # likely tokens, not creativity (unchanged from the original design).
        "temperature": 0.0,
        "stream": False,
    }
    body.update(_extra_body())
    try:
        r = requests.post(
            f"{CHAT_BASE_URL}/chat/completions",
            headers=_headers(CHAT_API_KEY),
            json=body,
            timeout=timeout,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        _log(f"[providers] chat endpoint unreachable/failed: {e}")
        return None
    try:
        return r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as e:
        _log(f"[providers] unexpected chat response shape: {e}")
        return None


def embed(text: str, timeout: int = 20) -> Optional[list]:
    """One embedding via the universal style. Returns the vector, or None on
    any failure (logged) — no embeddings means the de-dup check is skipped,
    never a dropped task (INC-001 D3)."""
    body = {"model": EMBED_MODEL, "input": text}
    try:
        r = requests.post(
            f"{EMBED_BASE_URL}/embeddings",
            headers=_headers(EMBED_API_KEY),
            json=body,
            timeout=timeout,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        _log(f"[providers] embeddings endpoint unreachable/failed: {e}")
        return None
    try:
        return r.json()["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError, ValueError) as e:
        _log(f"[providers] unexpected embeddings response shape: {e}")
        return None
