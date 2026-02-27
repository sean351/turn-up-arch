#!/usr/bin/env python3
"""
turnupd — Turn Up mixer daemon for Linux

Bridges a USB serial device (knobs + buttons) to PipeWire/PulseAudio via
pulsectl, mapping hardware inputs to per-sink, per-source, and per-app
volume control as well as mute toggles and arbitrary shell commands.
"""

import logging
import signal
import subprocess
import sys
import time

import pulsectl
import serial

from turnup.config import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("turnupd")

# Maximum raw ADC value reported by the hardware.
KNOB_MAX: int = 1012
# Maximum output volume multiplier (1.5 = 150 %).
VOLUME_MAX: float = 1.5


# ── Protocol parser ────────────────────────────────────────────────────────────

def parse_messages(buf: bytearray) -> tuple[list[dict], bytearray]:
    """Parse framed messages out of *buf* and return ``(messages, remainder)``.

    Supported frame formats (all delimited by ``0xFE`` … ``0xFF``):

    +-----------+----------------------------+
    | Heartbeat | ``FE 02 FF``               |
    +-----------+----------------------------+
    | Button    | ``FE 06/07 <id> FF``       |
    +-----------+----------------------------+
    | Knob      | ``FE 03 <id> <hi> <lo> FF``|
    +-----------+----------------------------+
    """
    messages: list[dict] = []
    i = 0
    while i < len(buf):
        if buf[i] != 0xFE:
            i += 1
            continue

        remaining = len(buf) - i

        # Heartbeat: FE 02 FF  (3 bytes)
        if remaining >= 3 and buf[i + 1] == 0x02 and buf[i + 2] == 0xFF:
            messages.append({"type": "heartbeat"})
            i += 3

        # Button press/release: FE 06/07 <id> FF  (4 bytes)
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

        # Knob value: FE 03 <id> <hi> <lo> FF  (6 bytes)
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


# ── PulseAudio / PipeWire controller ──────────────────────────────────────────

class PulseController:
    """Thin wrapper around :class:`pulsectl.Pulse` for volume and mute control."""

    def __init__(self) -> None:
        self._pulse = pulsectl.Pulse("turnupd")

    def close(self) -> None:
        self._pulse.close()

    # -- Sink (output) ---------------------------------------------------------

    def set_sink_volume(self, sink_name: str, volume: float) -> None:
        """Set output volume for *sink_name*.  *volume* is clamped to 0.0–1.5."""
        volume = max(0.0, min(VOLUME_MAX, volume))
        try:
            if sink_name == "default":
                info = self._pulse.server_info()
                sink = self._pulse.get_sink_by_name(info.default_sink_name)
            else:
                sink = self._pulse.get_sink_by_name(sink_name)
            self._pulse.volume_set_all_chans(sink, volume)
        except Exception as exc:
            log.warning("set_sink_volume(%r) failed: %s", sink_name, exc)

    def toggle_mute_sink(self, sink_name: str) -> None:
        """Toggle the mute state of *sink_name*."""
        try:
            if sink_name == "default":
                info = self._pulse.server_info()
                sink = self._pulse.get_sink_by_name(info.default_sink_name)
            else:
                sink = self._pulse.get_sink_by_name(sink_name)
            new_mute = not sink.mute
            self._pulse.mute(sink, new_mute)
            log.info("Sink %r mute → %s", sink_name, new_mute)
        except Exception as exc:
            log.warning("toggle_mute_sink(%r) failed: %s", sink_name, exc)

    # -- Source (input / mic) --------------------------------------------------

    def set_source_volume(self, source_name: str, volume: float) -> None:
        """Set mic/input volume for *source_name*.  *volume* is clamped to 0.0–1.0."""
        volume = max(0.0, min(1.0, volume))
        try:
            if source_name == "default":
                info = self._pulse.server_info()
                source = self._pulse.get_source_by_name(info.default_source_name)
            else:
                source = self._pulse.get_source_by_name(source_name)
            self._pulse.volume_set_all_chans(source, volume)
        except Exception as exc:
            log.warning("set_source_volume(%r) failed: %s", source_name, exc)

    def toggle_mute_source(self, source_name: str) -> None:
        """Toggle the mute state of *source_name*."""
        try:
            if source_name == "default":
                info = self._pulse.server_info()
                source = self._pulse.get_source_by_name(info.default_source_name)
            else:
                source = self._pulse.get_source_by_name(source_name)
            new_mute = not source.mute
            self._pulse.mute(source, new_mute)
            log.info("Source %r mute → %s", source_name, new_mute)
        except Exception as exc:
            log.warning("toggle_mute_source(%r) failed: %s", source_name, exc)

    # -- Sink inputs (per-app) -------------------------------------------------

    def set_app_volume(self, app_name: str, volume: float) -> None:
        """Set volume for the sink input whose ``application.name`` or
        ``application.process.binary`` contains *app_name* (case-insensitive).

        *volume* is clamped to 0.0–1.5.
        """
        volume = max(0.0, min(VOLUME_MAX, volume))
        needle = app_name.lower()
        try:
            for inp in self._pulse.sink_input_list():
                name   = inp.proplist.get("application.name", "")
                binary = inp.proplist.get("application.process.binary", "")
                if needle in name.lower() or needle in binary.lower():
                    self._pulse.volume_set_all_chans(inp, volume)
                    return
            log.debug("App %r not found in sink inputs", app_name)
        except Exception as exc:
            log.warning("set_app_volume(%r) failed: %s", app_name, exc)


# ── Event handlers ─────────────────────────────────────────────────────────────

def handle_knob(
    knob_id: int, value: int, config: dict, pulse: PulseController
) -> None:
    """Dispatch a knob event according to the loaded configuration."""
    knob_cfg = config.get("knobs", {}).get(str(knob_id))
    if not knob_cfg:
        return

    action = knob_cfg.get("action", "sink_volume")
    target = knob_cfg.get("target", "default")

    if action == "sink_volume":
        vol = knob_to_volume(value)
        pulse.set_sink_volume(target, vol)
        log.info("Knob %d → sink %r = %.2f", knob_id, target, vol)

    elif action == "source_volume":
        vol = knob_to_norm(value)
        pulse.set_source_volume(target, vol)
        log.info("Knob %d → source %r = %.2f", knob_id, target, vol)

    elif action == "app_volume":
        vol = knob_to_volume(value)
        pulse.set_app_volume(target, vol)
        log.info("Knob %d → app %r = %.2f", knob_id, target, vol)

    elif action == "group_volume":
        vol = knob_to_volume(value)
        targets: list[str] = knob_cfg.get("targets", [])
        for t in targets:
            pulse.set_app_volume(t, vol)
        log.info("Knob %d → group %s = %.2f", knob_id, targets, vol)


def handle_button(
    button_id: int, action: str, config: dict, pulse: PulseController
) -> None:
    """Dispatch a button event according to the loaded configuration.

    Only ``press`` events are acted upon; ``release`` events are silently
    ignored.
    """
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
    port: str  = config.get("port", "/dev/ttyACM0")
    baud: int  = config.get("baud", 115200)

    log.info("Turn Up daemon starting — %s @ %d baud", port, baud)

    pulse = PulseController()
    buf   = bytearray()

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
                while True:
                    data = ser.read(64)
                    if not data:
                        continue
                    buf.extend(data)
                    messages, buf = parse_messages(buf)
                    for msg in messages:
                        if msg["type"] == "knob":
                            handle_knob(msg["id"], msg["value"], config, pulse)
                        elif msg["type"] == "button":
                            handle_button(msg["id"], msg["action"], config, pulse)

        except serial.SerialException as exc:
            log.warning("Serial error: %s — retrying in 3 s", exc)
            time.sleep(3)
        except Exception as exc:  # noqa: BLE001
            log.error("Unexpected error: %s — retrying in 3 s", exc)
            time.sleep(3)


if __name__ == "__main__":
    main()
