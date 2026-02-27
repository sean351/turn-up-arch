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
import queue
import signal
import subprocess
import sys
import threading
import time

import pulsectl
import serial

from turnup.config import DEFAULT_CONFIG_PATH, get_knob_led_cfg, get_led_color, load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("turnupd")

# Maximum raw ADC value reported by the hardware.
KNOB_MAX: int = 1012
# Maximum output volume multiplier (1.0 = 100 %).
VOLUME_MAX: float = 1.0
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


# ── MPRIS2 controller (playerctl-backed) ──────────────────────────────────────

class MPRISController:
    """Uses *playerctl* to read/write per-app volume via the MPRIS2 D-Bus interface.

    Caches the player list for ``_CACHE_TTL`` seconds to avoid spawning a new
    subprocess on every single call.
    """

    _CACHE_TTL: float = 3.0  # seconds between ``playerctl --list-all`` calls

    def __init__(self) -> None:
        self._players: list[str] = []
        self._players_ts: float = 0.0
        self._lock = threading.Lock()

    # ── internal helpers ──────────────────────────────────────────────────────

    def _run(self, *args: str, timeout: float = 2.0) -> tuple[bool, str]:
        """Run ``playerctl <args>`` and return ``(success, stdout.strip())``."""
        try:
            result = subprocess.run(
                ["playerctl", *args],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.returncode == 0, result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            log.debug("playerctl call failed: %s", exc)
            return False, ""

    def _refresh_players(self, *, force: bool = False) -> None:
        """Refresh the cached player list if it has expired (or *force* is set)."""
        now = time.monotonic()
        if not force and (now - self._players_ts) < self._CACHE_TTL:
            return
        ok, out = self._run("--list-all")
        with self._lock:
            self._players = [p.strip() for p in out.splitlines() if p.strip()] if ok else []
            self._players_ts = now

    # ── public API ────────────────────────────────────────────────────────────

    def find_player(self, app_name: str) -> str | None:
        """Return the first cached player whose name contains *app_name* (case-insensitive)."""
        self._refresh_players()
        needle = app_name.lower()
        with self._lock:
            for player in self._players:
                if needle in player.lower():
                    return player
        return None

    def get_volume(self, app_name: str) -> float | None:
        """Return the MPRIS volume (0.0–1.0) for *app_name*, or ``None`` if unavailable."""
        player = self.find_player(app_name)
        if player is None:
            return None
        ok, out = self._run("--player", player, "volume")
        if not ok or not out:
            return None
        try:
            return max(0.0, min(1.0, float(out)))
        except ValueError:
            return None

    def set_volume(self, app_name: str, volume: float) -> bool:
        """Set the MPRIS volume for *app_name*.  Returns ``True`` on success."""
        player = self.find_player(app_name)
        if player is None:
            return False
        volume = max(0.0, min(1.0, volume))
        ok, _ = self._run("--player", player, "volume", f"{volume:.4f}")
        if ok:
            log.debug("MPRIS: %r volume → %.4f", player, volume)
        return ok


# ── PulseAudio / PipeWire controller ──────────────────────────────────────────

class PulseController:
    """Thin wrapper around :class:`pulsectl.Pulse` for volume and mute control.

    When *mpris* is supplied, ``set_app_volume`` and ``get_app_volume_norm``
    will prefer the MPRIS2 path for any app that has a live playerctl player,
    falling back to the PulseAudio stream only when MPRIS is unavailable.

    A background watcher thread (started by :meth:`start_watching`) listens
    for PulseAudio sink-input events and pushes indices onto ``_event_q`` so
    the main loop can trigger an immediate reapply for PA-only apps instead of
    waiting for the 1-second timer.
    """

    def __init__(self, mpris: MPRISController | None = None) -> None:
        self._pulse = pulsectl.Pulse("turnupd")
        self._mpris = mpris
        self._event_q: queue.Queue[int] = queue.Queue()
        self._watcher_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def close(self) -> None:
        self._stop_event.set()
        self._pulse.close()

    # ── PA event watcher ──────────────────────────────────────────────────────

    def start_watching(self) -> None:
        """Spawn the background PA event listener thread (idempotent)."""
        if self._watcher_thread and self._watcher_thread.is_alive():
            return
        self._stop_event.clear()
        t = threading.Thread(target=self._event_loop, daemon=True, name="pa-watcher")
        t.start()
        self._watcher_thread = t
        log.debug("PA watcher thread started")

    def _event_loop(self) -> None:
        """Background thread: open a *separate* Pulse connection and listen for events."""
        try:
            with pulsectl.Pulse("turnupd-watcher") as watch_pulse:
                def _cb(ev: pulsectl.PulseEventInfo) -> None:  # type: ignore[name-defined]
                    if ev.facility == "sink_input":
                        self._event_q.put(int(ev.index))
                    raise pulsectl.PulseLoopStop

                watch_pulse.event_mask_set("sink_input")
                watch_pulse.event_callback_set(_cb)
                while not self._stop_event.is_set():
                    try:
                        watch_pulse.event_listen(timeout=1.0)
                    except pulsectl.PulseLoopStop:
                        pass
                    except Exception as exc:
                        log.debug("PA event loop error: %s", exc)
                        time.sleep(0.5)
        except Exception as exc:
            log.warning("PA watcher thread exiting: %s", exc)

    def drain_events(self) -> bool:
        """Drain all pending PA events.  Returns ``True`` if any events were present."""
        had_events = False
        while True:
            try:
                self._event_q.get_nowait()
                had_events = True
            except queue.Empty:
                break
        return had_events

    def set_sink_volume(self, sink_name: str, volume: float) -> None:
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
        try:
            if sink_name == "default":
                info = self._pulse.server_info()
                sink = self._pulse.get_sink_by_name(info.default_sink_name)
            else:
                sink = self._pulse.get_sink_by_name(sink_name)
            self._pulse.mute(sink, not sink.mute)
            log.info("Sink %r mute toggled", sink_name)
        except Exception as exc:
            log.warning("toggle_mute_sink(%r) failed: %s", sink_name, exc)

    def set_source_volume(self, source_name: str, volume: float) -> None:
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
        try:
            if source_name == "default":
                info = self._pulse.server_info()
                source = self._pulse.get_source_by_name(info.default_source_name)
            else:
                source = self._pulse.get_source_by_name(source_name)
            self._pulse.mute(source, not source.mute)
            log.info("Source %r mute toggled", source_name)
        except Exception as exc:
            log.warning("toggle_mute_source(%r) failed: %s", source_name, exc)

    def set_app_volume(self, app_name: str, volume: float) -> None:
        volume = max(0.0, min(VOLUME_MAX, volume))

        # Prefer the MPRIS2 path — it writes to the app's internal slider so the
        # volume survives song transitions (e.g. Spotify resetting on new tracks).
        if self._mpris and self._mpris.set_volume(app_name, volume):
            log.debug("MPRIS set_volume: %r = %.4f", app_name, volume)
            return

        # Fall back to PulseAudio stream volume.
        needle = app_name.lower()
        found  = False
        try:
            for inp in self._pulse.sink_input_list():
                name   = inp.proplist.get("application.name", "")
                binary = inp.proplist.get("application.process.binary", "")
                if needle in name.lower() or needle in binary.lower():
                    self._pulse.volume_set_all_chans(inp, volume)
                    found = True
            if not found:
                log.debug("App %r not found in sink inputs", app_name)
        except Exception as exc:
            log.warning("set_app_volume(%r) failed: %s", app_name, exc)

    def get_sink_volume_norm(self, sink_name: str) -> float | None:
        """Return the current sink volume normalised to 0.0–1.0, or None on error."""
        try:
            if sink_name == "default":
                info = self._pulse.server_info()
                sink = self._pulse.get_sink_by_name(info.default_sink_name)
            else:
                sink = self._pulse.get_sink_by_name(sink_name)
            return min(1.0, sink.volume.value_flat / VOLUME_MAX)
        except Exception:
            return None

    def get_source_volume_norm(self, source_name: str) -> float | None:
        """Return the current source volume normalised to 0.0–1.0, or None on error."""
        try:
            if source_name == "default":
                info = self._pulse.server_info()
                source = self._pulse.get_source_by_name(info.default_source_name)
            else:
                source = self._pulse.get_source_by_name(source_name)
            return min(1.0, source.volume.value_flat)
        except Exception:
            return None

    def get_app_volume_norm(self, app_name: str) -> float | None:
        """Return the current app volume normalised to 0.0–1.0, or None if not found."""
        # Prefer MPRIS — more accurate for apps like Spotify.
        if self._mpris:
            vol = self._mpris.get_volume(app_name)
            if vol is not None:
                return vol

        # Fall back to PulseAudio stream.
        needle = app_name.lower()
        try:
            for inp in self._pulse.sink_input_list():
                name   = inp.proplist.get("application.name", "")
                binary = inp.proplist.get("application.process.binary", "")
                if needle in name.lower() or needle in binary.lower():
                    return min(1.0, inp.volume.value_flat / VOLUME_MAX)
        except Exception:
            pass
        return None


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
) -> None:
    """Dispatch a knob event, update PulseAudio, then refresh the LEDs."""
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
    send_leds(ser, all_led_colors(config, knob_norms))


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
                    if pulse.drain_events() or now - last_reapply >= 1.0:
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
