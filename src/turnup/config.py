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
    "leds": {
        # Global default LED behaviour — overridable per-knob via a "led": {}
        # block inside each knob entry.
        #
        # mode:
        #   "volume"  — interpolate low_color→high_color based on knob position
        #   "static"  — always show high_color regardless of volume
        #   "off"     — LEDs disabled
        "mode": "volume",
        "low_color":  [255, 0, 0],   # red   at volume 0.0
        "high_color": [0, 255, 0],   # green at volume 1.0
    },
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
VALID_LED_MODES: frozenset = frozenset({"volume", "static", "off"})

_XDG_CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "turnup",
)
DEFAULT_CONFIG_PATH = os.path.join(_XDG_CONFIG_DIR, "config.json")


# ── LED helpers ────────────────────────────────────────────────────────────────

def _validate_color(color: object, name: str, fallback: list) -> list:
    """Return *color* if it is a valid [R, G, B] list, else *fallback*."""
    if (
        isinstance(color, (list, tuple))
        and len(color) == 3
        and all(isinstance(c, int) and 0 <= c <= 255 for c in color)
    ):
        return list(color)
    log.warning("Invalid LED %s %r — using default", name, color)
    return fallback


def _validate_leds(leds: object, context: str = "leds") -> dict:
    """Validate and normalise a LED config block.

    *context* is used only in warning messages (e.g. ``"knobs.2.led"``).
    Falls back field-by-field to the global defaults so partial overrides work.
    """
    global_defaults = DEFAULT_CONFIG["leds"]
    if not isinstance(leds, dict):
        if leds is not None:
            log.warning("%s is not a dict — using defaults", context)
        return dict(global_defaults)

    mode = leds.get("mode", global_defaults["mode"])
    if mode not in VALID_LED_MODES:
        log.warning("%s: unknown mode %r — falling back to 'volume'", context, mode)
        mode = "volume"

    return {
        "mode":       mode,
        "low_color":  _validate_color(
            leds.get("low_color"),  "low_color",  global_defaults["low_color"]
        ),
        "high_color": _validate_color(
            leds.get("high_color"), "high_color", global_defaults["high_color"]
        ),
    }


def get_knob_led_cfg(config: dict, knob_id: int) -> dict:
    """Return the effective LED config for *knob_id*.

    If the knob entry contains a ``"led"`` key it is used (merged with global
    defaults for any missing fields); otherwise the top-level ``"leds"`` block
    is returned.
    """
    global_leds = config.get("leds", DEFAULT_CONFIG["leds"])
    knob_cfg    = config.get("knobs", {}).get(str(knob_id), {})
    knob_led    = knob_cfg.get("led")

    if knob_led is None:
        return global_leds

    # Knob has its own led block — validate it, falling back to global values
    # for any field it doesn't specify.
    merged = {
        "mode":       global_leds.get("mode",       DEFAULT_CONFIG["leds"]["mode"]),
        "low_color":  global_leds.get("low_color",  DEFAULT_CONFIG["leds"]["low_color"]),
        "high_color": global_leds.get("high_color", DEFAULT_CONFIG["leds"]["high_color"]),
    }
    if isinstance(knob_led, dict):
        if "mode" in knob_led:
            merged["mode"] = knob_led["mode"]
        if "low_color" in knob_led:
            merged["low_color"] = knob_led["low_color"]
        if "high_color" in knob_led:
            merged["high_color"] = knob_led["high_color"]

    return _validate_leds(merged, context=f"knobs.{knob_id}.led")


def get_led_color(led_cfg: dict, norm: float) -> tuple[int, int, int]:
    """Return an ``(r, g, b)`` tuple for a knob at normalised position *norm* (0.0-1.0).

    Behaviour depends on ``led_cfg["mode"]``:

    * ``"off"``    -> ``(0, 0, 0)``
    * ``"static"`` -> ``high_color`` always
    * ``"volume"`` -> linear interpolation between ``low_color`` and ``high_color``
    """
    mode = led_cfg.get("mode", "volume")

    if mode == "off":
        return (0, 0, 0)

    high = led_cfg.get("high_color", DEFAULT_CONFIG["leds"]["high_color"])

    if mode == "static":
        return tuple(high)

    low = led_cfg.get("low_color", DEFAULT_CONFIG["leds"]["low_color"])
    t   = max(0.0, min(1.0, norm))
    return tuple(int(low[i] + (high[i] - low[i]) * t) for i in range(3))


# ── Config I/O ─────────────────────────────────────────────────────────────────

def load_config(path: str | None = None) -> dict:
    """Load configuration from *path*.

    If *path* is ``None`` the XDG-compliant location
    ``~/.config/turnup/config.json`` is used, falling back to the directory
    that contains this module for backwards compatibility.

    Returns the parsed and validated configuration dictionary.
    Exits with status 1 on malformed JSON.
    """
    if path is None:
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
        cfg["leds"] = _validate_leds(cfg.get("leds", {}))
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
