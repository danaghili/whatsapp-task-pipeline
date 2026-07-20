"""whatsapp-task-pipeline — trusted-sender messages → Home Assistant to-do
items, classified by an LLM you point it at (local by default), with a
reminder loop behind it.

The public entry points are the three console commands defined in
pyproject.toml: wtp-listen (the listener), wtp-remind (the reminder loop),
and wtp-check (the config checker).
"""

__version__ = "0.2.0"
