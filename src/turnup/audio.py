"""
audio.py — MPRIS2 and PulseAudio/PipeWire volume controllers for turnupd.

Two classes are provided:

* :class:`MPRISController` — reads and writes per-app volume via the MPRIS2
  D-Bus interface using ``playerctl``.  Preferred for apps that support it
  (Spotify, VLC, Cider, …) because it sets the app's *internal* slider rather
  than just the PA stream, so the volume survives song transitions.

* :class:`PulseController` — wraps ``pulsectl`` for sink, source, and app-
  stream volume/mute.  When an :class:`MPRISController` instance is supplied
  it delegates app-volume calls to MPRIS first, falling back to the PA stream
  for apps that have no MPRIS player (e.g. Brave/Chromium).
  Also runs a background thread that queues PA sink-input events so the main
  loop can trigger an immediate reapply instead of waiting for the 1-second
  polling timer.
"""

import logging
import queue
import subprocess
import threading
import time

import pulsectl

log = logging.getLogger("turnupd")

# Imported by callers that need the ceiling constant.
VOLUME_MAX: float = 1.0


# ── MPRIS2 controller ──────────────────────────────────────────────────────────

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

    # ── Sink / source ─────────────────────────────────────────────────────────

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

    # ── App volume (MPRIS-first, PA fallback) ─────────────────────────────────

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
