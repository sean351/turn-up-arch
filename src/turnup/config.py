#!/usr/bin/env python3
"""
config.py — Load and validate turnup configuration from config.json
"""

import json
import logging
import os
import sys

log = logging.getLogger("turnupd")

DEFAULT_CONFIG: dict = {
    "port": "/dev/ttyACM0",
    "baud": 115200,
    "knobs": {
        "0": {"action": "sink_volume",   "target": "default"},
        "1": {"action": "sink_volume",   "target": "default"},
        "2": {"action": "source_volume", "target": "default"},
        "3": {"action": "sink_volume",   "target": "default"},
        "4": {"action": "sink_volume",   "target": "default"},
    },
    "buttons": {
        "0": {"action": "mute_sink",   "target": "default"},
        "1": {"action": "mute_source", "target": "default"},
        "2": {"action": "command",     "target": ""},
        "3": {"action": "command",     "target": ""},
        "4": {"action": "command",     "target": ""},
    },
}

VALID_KNOB_ACTIONS: frozenset = frozenset(
    {"sink_volume", "source_volume", "app_volume", "group_volume"}
)
VALID_BUTTON_ACTIONS: frozenset = frozenset(
    {"mute_sink", "mute_source", "command"}
)

# Default config search path: ~/.config/turnup/config.json
_XDG_CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "turnup",
)
DEFAULT_CONFIG_PATH = os.path.join(_XDG_CONFIG_DIR, "config.json")


def load_config(path: str | None = None) -> dict:
    """Load configuration from *path*.

    If *path* is ``None`` the XDG-compliant location
    ``~/.config/turnup/config.json`` is used, falling back to the directory
    that contains this module for backwards compatibility.

    Returns the parsed configuration dictionary.  Exits with status 1 on
    malformed JSON.
    """
    if path is None:
        # Prefer XDG location; fall back to the directory next to this module.
        if os.path.exists(DEFAULT_CONFIG_PATH):
            path = DEFAULT_CONFIG_PATH
        else:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            path = os.path.join(script_dir, "config.json")

    if not os.path.exists(path):
        log.warning("No config.json found at %s — writing defaults", path)
        _write_default(path)
        return dict(DEFAULT_CONFIG)

    try:
        with open(path) as f:
            cfg: dict = json.load(f)
        log.info("Loaded config from %s", path)
        return cfg
    except json.JSONDecodeError as exc:
        log.error("Invalid JSON in %s: %s", path, exc)
        sys.exit(1)


def _write_default(path: str) -> None:
    """Write the default configuration to *path*, creating directories as needed."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
            f.write("\n")
        log.info("Created default config at %s", path)
    except OSError as exc:
        log.warning("Could not write default config: %s", exc)
