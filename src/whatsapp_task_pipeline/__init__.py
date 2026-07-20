"""whatsapp-task-pipeline — trusted-sender messages → Home Assistant to-do
items, classified by an LLM you point it at (local by default), with a
reminder loop behind it.

The public entry points are the three console commands defined in
pyproject.toml: wtp-listen (the listener), wtp-remind (the reminder loop),
and wtp-check (the config checker).
"""

import os
from pathlib import Path

__version__ = "0.2.0"


def _load_dotenv() -> bool:
    """Read ./.env from the current directory into the environment.

    Every command loads it on package import, so a stranger runs `wtp-check`
    or `wtp-listen` from their checkout and it just works — no `source .env`
    (which shells refuse on the unquoted JSON of TRUSTED_SENDERS; found the
    hard way in the dogfood run). Values already present in the real
    environment always win — a supervisor (launchd / systemd / docker
    env_file) that injects env keeps full control.
    """
    path = Path(".env")
    if not path.is_file():
        return False
    loaded = False
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip()
            loaded = True
    return loaded


# Runs at import time on purpose: the modules read their configuration into
# module-level constants at import, so the file must be absorbed first.
DOTENV_LOADED = _load_dotenv()
