#!/usr/bin/env python3
"""
turnupd — Turn Up mixer daemon for Linux

Bridges a USB serial device (knobs + buttons) to PipeWire/PulseAudio via
pulsectl, mapping hardware inputs to per-sink, per-source, and per-app
volume control as well as mute toggles and arbitrary shell commands.

LED feedback: after every knob move the device's RGB LEDs are updated to
reflect the current volume using the per-knob (or global) colour scheme
from config.  LEDs are also refreshed on every heartbeat so they stay lit
even when no knob is being moved.
"""

import logging
import os
import signal
import subprocess
import sys
import time

import serial

from turnup.audio import VOLUME_MAX, MPRISController, PulseController
from turnup.config import DEFAULT_CONFIG_PATH, get_knob_led_cfg, get_led_color, load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("turnupd")

# Maximum raw ADC value reported by the hardware.
KNOB_MAX: int = 1012
# Number of physical knobs (and therefore LED groups).
NUM_KNOBS: int = 5
# Number of LEDs per knob.
LEDS_PER_KNOB: int = 3


# ── Protocol parser ────────────────────────────────────────────────────────────

def parse_messages(buf: bytearray) -> tuple[list[dict], bytearray]:
    """Parse framed messages out of *buf* and return ``(messages, remainder)``."""
    messages: list[dict] = []
    i = 0
    while i < len(buf):
        if buf[i] != 0xFE:
            i += 1
            continue

        remaining = len(buf) - i

        if remaining >= 3 and buf[i + 1] == 0x02 and buf[i + 2] == 0xFF:
            messages.append({"type": "heartbeat"})
            i += 3

        elif (
            remaining >= 4
            and buf[i + 1] in (0x06, 0x07)
            and buf[i + 3] == 0xFF
        ):
            messages.append({
                "type": "button",
                "action": "press" if buf[i + 1] == 0x06 else "release",
                "id": buf[i + 2],
            })
            i += 4

        elif (
            remaining >= 6
            and buf[i + 1] == 0x03
            and buf[i + 5] == 0xFF
        ):
            messages.append({
                "type": "knob",
                "id": buf[i + 2],
                "value": (buf[i + 3] << 8) | buf[i + 4],
            })
            i += 6

        else:
            i += 1

    return messages, bytearray(buf[i:])


def knob_to_volume(value: int) -> float:
    """Convert a raw knob value (0–``KNOB_MAX``) to a sink volume (0.0–1.5)."""
    return round((value / KNOB_MAX) * VOLUME_MAX, 4)


def knob_to_norm(value: int) -> float:
    """Convert a raw knob value (0–``KNOB_MAX``) to a normalised float (0.0–1.0)."""
    return round(value / KNOB_MAX, 4)


# ── LED control ────────────────────────────────────────────────────────────────

def build_led_packet(colors: list[tuple[int, int, int]]) -> bytes:
    """Build the 47-byte LED packet for all 5 knobs.

    Frame format: ``FE 05 [R G B * LEDS_PER_KNOB] * NUM_KNOBS FF``
    """
    assert len(colors) == NUM_KNOBS
    payload = bytearray([0xFE, 0x05])
    for r, g, b in colors:
        payload += bytes([r, g, b]) * LEDS_PER_KNOB
    payload.append(0xFF)
    return bytes(payload)


def send_leds(ser: serial.Serial, colors: list[tuple[int, int, int]]) -> None:
    """Write an LED packet to the open serial port, swallowing any I/O errors."""
    try:
        ser.write(build_led_packet(colors))
    except serial.SerialException as exc:
        log.warning("LED write failed: %s", exc)


def all_led_colors(
    config: dict, knob_norms: list[float]
) -> list[tuple[int, int, int]]:
    """Return one ``(r, g, b)`` per knob based on each knob's LED config."""
    return [
        get_led_color(get_knob_led_cfg(config, i), knob_norms[i])
        for i in range(NUM_KNOBS)
    ]


# ── Startup helpers ────────────────────────────────────────────────────────────

def init_knob_norms(config: dict, pulse: PulseController) -> list[float]:
    """Query PulseAudio for current volumes and return an initial knob_norms list.

    This ensures the LEDs show the correct colour gradient immediately on
    connect rather than starting from all-zero (low_color) until the user
    moves each knob.
    """
    norms = [0.0] * NUM_KNOBS
    for knob_id_str, knob_cfg in config.get("knobs", {}).items():
        try:
            knob_id = int(knob_id_str)
        except ValueError:
            continue

        action = knob_cfg.get("action", "sink_volume")
        target = knob_cfg.get("target", "default")
        norm: float | None = None

        if action == "sink_volume":
            norm = pulse.get_sink_volume_norm(target)
        elif action == "source_volume":
            norm = pulse.get_source_volume_norm(target)
        elif action in ("app_volume", "group_volume"):
            # For group_volume use the first target; apps may not be running yet.
            t = target if action == "app_volume" else (knob_cfg.get("targets") or [None])[0]
            if t:
                norm = pulse.get_app_volume_norm(t)

        if norm is not None and 0 <= knob_id < NUM_KNOBS:
            norms[knob_id] = norm

    return norms


def build_app_volume_map(config: dict, knob_norms: list[float]) -> dict[str, float]:
    """Return ``{app_name_lower: volume}`` for every app/group knob in *config*.

    Used by :func:`reapply_app_volumes` to know what volume each configured
    application should currently be at, based on the last knob positions.
    """
    app_volumes: dict[str, float] = {}
    for knob_id_str, knob_cfg in config.get("knobs", {}).items():
        try:
            knob_id = int(knob_id_str)
        except ValueError:
            continue
        if knob_id >= NUM_KNOBS:
            continue
        action = knob_cfg.get("action", "")
        vol    = round(knob_norms[knob_id] * VOLUME_MAX, 4)
        if action == "app_volume":
            t = knob_cfg.get("target", "")
            if t:
                app_volumes[t.lower()] = vol
        elif action == "group_volume":
            for t in knob_cfg.get("targets", []):
                if t:
                    app_volumes[t.lower()] = vol
    return app_volumes


def reapply_app_volumes(config: dict, pulse: PulseController, knob_norms: list[float]) -> None:
    """Re-apply stored knob volumes to every matching active sink input.

    Called on a 1-second timer and whenever a PA sink-input event fires so
    that new streams (e.g. Spotify starting a new song) are brought back to
    the last knob position rather than being left at the 100 % default that
    ``module-stream-restore`` restores them to.

    For MPRIS-capable apps the volume is written via playerctl (which updates
    the app's own internal slider).  For PA-only apps (e.g. Brave) the volume
    is corrected on the PulseAudio stream level.
    """
    app_volumes = build_app_volume_map(config, knob_norms)
    if not app_volumes:
        return

    # Split targets into MPRIS-handled vs PA-only.
    mpris = pulse._mpris
    pa_only: dict[str, float] = {}

    for app_name, vol in app_volumes.items():
        if mpris and mpris.set_volume(app_name, vol):
            log.debug("reapply MPRIS: %r → %.4f", app_name, vol)
        else:
            pa_only[app_name] = vol

    if not pa_only:
        return

    # PA stream correction for non-MPRIS apps.
    try:
        for inp in pulse._pulse.sink_input_list():
            name   = inp.proplist.get("application.name", "").lower()
            binary = inp.proplist.get("application.process.binary", "").lower()
            for needle, vol in pa_only.items():
                if needle in name or needle in binary:
                    current = inp.volume.value_flat
                    if abs(current - vol) > 0.01:
                        pulse._pulse.volume_set_all_chans(inp, vol)
                        log.debug(
                            "reapply PA: %r volume %.2f → %.2f",
                            inp.proplist.get("application.name", needle),
                            current,
                            vol,
                        )
                    break  # one needle is enough per stream
    except Exception as exc:
        log.debug("reapply_app_volumes (PA) failed: %s", exc)


# ── Event handlers ─────────────────────────────────────────────────────────────

def handle_knob(
    knob_id: int,
    value: int,
    config: dict,
    pulse: PulseController,
    ser: serial.Serial,
    knob_norms: list[float],
    last_led_colors: list[tuple[int, int, int]],
    last_knob_event: list[float],
) -> None:
    """Dispatch a knob event, update PulseAudio, then refresh the LEDs.

    *last_led_colors* is a length-5 list used to suppress duplicate LED
    packets — if the computed colours are identical to the last send we skip
    the write, eliminating the LED storm that causes visible flicker during a
    fast knob turn.

    *last_knob_event* is a single-element list (mutable float box) whose
    value is updated to ``time.monotonic()`` on every call so the main loop
    can gate ``reapply_app_volumes`` on a quiet period after knob activity.
    """
    knob_cfg = config.get("knobs", {}).get(str(knob_id))
    if not knob_cfg:
        return

    action = knob_cfg.get("action", "sink_volume")
    target = knob_cfg.get("target", "default")
    norm   = knob_to_norm(value)

    if action == "sink_volume":
        vol = knob_to_volume(value)
        pulse.set_sink_volume(target, vol)
        log.info("Knob %d → sink %r = %.2f", knob_id, target, vol)

    elif action == "source_volume":
        pulse.set_source_volume(target, norm)
        log.info("Knob %d → source %r = %.2f", knob_id, target, norm)

    elif action == "app_volume":
        vol = knob_to_volume(value)
        pulse.set_app_volume(target, vol)
        log.info("Knob %d → app %r = %.2f", knob_id, target, vol)

    elif action == "group_volume":
        vol = knob_to_volume(value)
        for t in knob_cfg.get("targets", []):
            pulse.set_app_volume(t, vol)
        log.info("Knob %d → group %s = %.2f", knob_id, knob_cfg.get("targets"), vol)

    knob_norms[knob_id] = norm
    last_knob_event[0] = time.monotonic()

    # Only send an LED packet when the colour actually changes.  A single
    # physical knob turn generates 20-50 ADC samples in rapid succession;
    # without this guard every sample triggers a write and the firmware can't
    # keep up, causing visible flicker.
    new_colors = all_led_colors(config, knob_norms)
    if new_colors != last_led_colors:
        send_leds(ser, new_colors)
        last_led_colors[:] = new_colors


def handle_button(
    button_id: int, action: str, config: dict, pulse: PulseController
) -> None:
    """Dispatch a button press event."""
    if action != "press":
        return

    btn_cfg = config.get("buttons", {}).get(str(button_id))
    if not btn_cfg:
        return

    btn_action = btn_cfg.get("action", "")
    target     = btn_cfg.get("target", "default")

    if btn_action == "mute_sink":
        pulse.toggle_mute_sink(target)
    elif btn_action == "mute_source":
        pulse.toggle_mute_source(target)
    elif btn_action == "command":
        try:
            subprocess.Popen(target, shell=True)  # noqa: S602
            log.info("Button %d → command %r", button_id, target)
        except Exception as exc:
            log.warning("Button %d command failed: %s", button_id, exc)


# ── Main loop ──────────────────────────────────────────────────────────────────

def main() -> None:
    config = load_config()
    port: str = config.get("port", "/dev/ttyACM0")
    baud: int = config.get("baud", 115200)

    log.info("Turn Up daemon starting — %s @ %d baud", port, baud)

    mpris      = MPRISController()
    pulse      = PulseController(mpris)
    pulse.start_watching()
    knob_norms = init_knob_norms(config, pulse)
    buf        = bytearray()

    # Mutable state shared between the main loop and handle_knob:
    #   last_led_colors — suppress duplicate LED writes during fast knob turns
    #   last_knob_event — timestamp of most recent knob message; used to gate
    #                     reapply_app_volumes so we don't stall the loop
    #                     mid-turn (200 ms quiet period required)
    last_led_colors: list[tuple[int, int, int]] = [(0, 0, 0)] * NUM_KNOBS
    last_knob_event: list[float] = [0.0]

    # Track config file mtime so we can restart when it changes.
    try:
        config_mtime: float | None = os.stat(DEFAULT_CONFIG_PATH).st_mtime
    except OSError:
        config_mtime = None
    last_config_check = time.monotonic()
    # Re-apply app volumes periodically so new streams (e.g. Spotify new song)
    # are brought to the last knob position rather than resetting to 100 %.
    last_reapply = time.monotonic()

    def _shutdown(sig: int, _frame: object) -> None:
        log.info("Received signal %d — shutting down", sig)
        pulse.close()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    while True:
        try:
            with serial.Serial(port, baud, timeout=0.1) as ser:
                log.info("Connected to %s", port)
                buf.clear()
                send_leds(ser, all_led_colors(config, knob_norms))

                while True:
                    data = ser.read(64)
                    if data:
                        buf.extend(data)
                        messages, buf = parse_messages(buf)
                        for msg in messages:
                            if msg["type"] == "knob":
                                handle_knob(
                                    msg["id"], msg["value"],
                                    config, pulse, ser, knob_norms,
                                    last_led_colors, last_knob_event,
                                )
                            elif msg["type"] == "button":
                                handle_button(
                                    msg["id"], msg["action"], config, pulse
                                )
                            elif msg["type"] == "heartbeat":
                                send_leds(ser, all_led_colors(config, knob_norms))

                    # Check for config changes every 2 s (serial read timeout = 0.1 s).
                    now = time.monotonic()
                    if now - last_config_check >= 2.0:
                        last_config_check = now
                        try:
                            new_mtime = os.stat(DEFAULT_CONFIG_PATH).st_mtime
                            if config_mtime is not None and new_mtime != config_mtime:
                                log.info("Config changed — restarting daemon")
                                pulse.close()
                                os.execv(sys.executable, [sys.executable] + sys.argv)
                        except OSError:
                            pass

                    # Re-apply configured app volumes every 1 s to catch new
                    # streams (e.g. Spotify starting a new song resets to 100 %).
                    # Also trigger immediately on any PA sink-input event so
                    # PA-only apps (e.g. Brave) are corrected within ~0.1 s.
                    # Guard on a 200 ms knob-quiet period: calling playerctl /
                    # pulsectl while the user is actively turning a knob can
                    # stall the main loop long enough for serial data to back
                    # up, which in turn causes heartbeat misses and LED flicker.
                    knob_quiet = now - last_knob_event[0] >= 0.2
                    if (pulse.drain_events() or now - last_reapply >= 1.0) and knob_quiet:
                        last_reapply = now
                        reapply_app_volumes(config, pulse, knob_norms)

        except serial.SerialException as exc:
            log.warning("Serial error: %s — retrying in 3 s", exc)
            time.sleep(3)
        except Exception as exc:  # noqa: BLE001
            log.error("Unexpected error: %s — retrying in 3 s", exc)
            time.sleep(3)


if __name__ == "__main__":
    main()
