#!/usr/bin/env python3
"""
config.py — Load and validate turnup configuration from config.toml
"""

import logging
import os
import sys

try:
    import tomllib
except ImportError:                 # Python < 3.11
    import tomli as tomllib         # type: ignore[no-reuse-ignore]

log = logging.getLogger("turnupd")

# In-memory defaults used as fallback when the config file is missing or a
# field is omitted.  The on-disk template (DEFAULT_CONFIG_TOML) is the
# authoritative human-readable version of these values.
DEFAULT_CONFIG: dict = {
    "port": "/dev/ttyACM0",
    "baud": 115200,
    "leds": {
        "mode": "volume",
        "low_color":  [255, 0, 0],
        "high_color": [0, 255, 0],
    },
    "knobs": {
        "0": {"action": "sink_volume",   "target": "default"},
        "1": {"action": "group_volume",  "targets": ["spotify", "vlc", "Cider"]},
        "2": {"action": "app_volume",    "target": "brave"},
        "3": {"action": "source_volume", "target": "default"},
        "4": {"action": "sink_volume",   "target": "default"},
    },
    "buttons": {
        "0": {"action": "mute_sink",   "target": "default"},
        "1": {"action": "command",     "target": "playerctl previous"},
        "2": {"action": "command",     "target": "playerctl play-pause"},
        "3": {"action": "command",     "target": "playerctl next"},
        "4": {"action": "mute_source", "target": "default"},
    },
}

# Annotated TOML template written to ~/.config/turnup/config.toml on first run.
DEFAULT_CONFIG_TOML: str = """\
# turnup configuration — edit this file to match your device layout.
# Changes are picked up automatically (no restart needed).
# See https://github.com/sean351/turn-up-arch for full documentation.

# Serial port your device enumerates as (check `ls /dev/ttyACM*`).
port = "/dev/ttyACM0"
baud = 115200

# ── LED settings ──────────────────────────────────────────────────────────────
[leds]
# mode: "volume"  — fade from low_color → high_color based on the knob position
#       "static"  — always show high_color regardless of volume
#       "off"     — LEDs disabled
mode = "volume"
low_color  = [255, 0, 0]   # red   at 0 %
high_color = [0, 255, 0]   # green at 100 %

# ── Knobs ─────────────────────────────────────────────────────────────────────
# Available actions:
#   sink_volume   — output device volume (0–150 %)
#   source_volume — mic / input volume (0–100 %)
#   app_volume    — single application; set target = "AppName"
#   group_volume  — multiple applications at once; set targets = ["app1", "app2"]
#
# Each knob can optionally include a [knobs.N.led] block to override the
# global LED colours for that specific knob.

[knobs.0]
# Knob 0 — master output volume
action = "sink_volume"
target = "default"

[knobs.1]
# Knob 1 — media players (group volume example)
action = "group_volume"
targets = ["spotify", "vlc", "Cider"]

[knobs.2]
# Knob 2 — browser (app volume example)
action = "app_volume"
target = "brave"

[knobs.3]
# Knob 3 — microphone / input
action = "source_volume"
target = "default"

[knobs.4]
# Knob 4 — unused; change to taste
action = "sink_volume"
target = "default"

# ── Buttons ───────────────────────────────────────────────────────────────────
# Available actions:
#   mute_sink   — toggle output mute
#   mute_source — toggle mic mute
#   command     — run an arbitrary shell command; set target = "command args"

[buttons.0]
# Button 0 — mute output
action = "mute_sink"
target = "default"

[buttons.1]
# Button 1 — previous track (requires playerctl)
action = "command"
target = "playerctl previous"

[buttons.2]
# Button 2 — play / pause (requires playerctl)
action = "command"
target = "playerctl play-pause"

[buttons.3]
# Button 3 — next track (requires playerctl)
action = "command"
target = "playerctl next"

[buttons.4]
# Button 4 — mute microphone
action = "mute_source"
target = "default"
"""

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
DEFAULT_CONFIG_PATH = os.path.join(_XDG_CONFIG_DIR, "config.toml")


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
        return (high[0], high[1], high[2])

    low = led_cfg.get("low_color", DEFAULT_CONFIG["leds"]["low_color"])
    t   = max(0.0, min(1.0, norm))
    return (
        int(low[0] + (high[0] - low[0]) * t),
        int(low[1] + (high[1] - low[1]) * t),
        int(low[2] + (high[2] - low[2]) * t),
    )


# ── Config I/O ─────────────────────────────────────────────────────────────────

def load_config(path: str | None = None) -> dict:
    """Load configuration from *path*.

    If *path* is ``None`` the XDG-compliant location
    ``~/.config/turnup/config.toml`` is used.

    Returns the parsed and validated configuration dictionary.
    Exits with status 1 on malformed TOML.
    """
    if path is None:
        path = DEFAULT_CONFIG_PATH

    if not os.path.exists(path):
        # Hint for users upgrading from the old JSON format.
        legacy_path = path.replace(".toml", ".json")
        if os.path.exists(legacy_path):
            log.warning(
                "Found legacy JSON config at %s — "
                "please migrate to TOML format at %s",
                legacy_path, path,
            )
        log.warning("No config.toml found at %s — writing defaults", path)
        _write_default(path)
        return dict(DEFAULT_CONFIG)

    try:
        with open(path, "rb") as f:
            cfg: dict = tomllib.load(f)
        cfg["leds"] = _validate_leds(cfg.get("leds", {}))
        log.info("Loaded config from %s", path)
        return cfg
    except tomllib.TOMLDecodeError as exc:
        log.error("Invalid TOML in %s: %s", path, exc)
        sys.exit(1)


def _write_default(path: str) -> None:
    """Write the annotated default configuration to *path*, creating directories as needed."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(DEFAULT_CONFIG_TOML)
        log.info("Created default config at %s", path)
    except OSError as exc:
        log.warning("Could not write default config: %s", exc)
