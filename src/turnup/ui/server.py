#!/usr/bin/env python3
"""
ui/server.py — TurnUp web UI (FastAPI + uvicorn)

Serves a PWA at http://127.0.0.1:5173 that lets you edit
~/.config/turnup/config.toml and manage named TOML presets.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from ..config import DEFAULT_CONFIG_PATH, _XDG_CONFIG_DIR, load_config

log = logging.getLogger("turnup-ui")

PRESETS_DIR = Path(_XDG_CONFIG_DIR) / "presets"
STATIC_DIR = Path(__file__).parent / "static"

# ── TOML serializer ────────────────────────────────────────────────────────────
# tomllib (stdlib) is read-only; we write our own minimal serialiser so we
# don't need an extra runtime dep (tomli-w).

_SAFE_NAME = re.compile(r"^[A-Za-z0-9 _\-\.]+$")


def _s(v: str) -> str:
    """Quote a string value for TOML."""
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _color(c: list[int]) -> str:
    return f"[{int(c[0])}, {int(c[1])}, {int(c[2])}]"


def config_to_toml(cfg: dict) -> str:
    lines: list[str] = []

    lines.append(f'port = {_s(cfg.get("port", "/dev/ttyACM0"))}')
    lines.append(f'baud = {int(cfg.get("baud", 115200))}')
    lines.append("")

    leds = cfg.get("leds") or {}
    lines.append("[leds]")
    lines.append(f'mode       = {_s(leds.get("mode", "volume"))}')
    lines.append(f'low_color  = {_color(leds.get("low_color",  [255, 0, 0]))}')
    lines.append(f'high_color = {_color(leds.get("high_color", [0, 255, 0]))}')
    lines.append("")

    knobs = cfg.get("knobs") or {}
    for i in range(5):
        knob = knobs.get(str(i))
        if not knob:
            continue
        action = knob.get("action", "sink_volume")
        lines.append(f"[knobs.{i}]")
        lines.append(f'action = {_s(action)}')
        if action == "group_volume":
            tgts = knob.get("targets") or []
            lines.append(f'targets = [{", ".join(_s(t) for t in tgts)}]')
        else:
            lines.append(f'target = {_s(knob.get("target", "default"))}')
        # Optional per-knob LED override (written as an inline table)
        knob_led = knob.get("led")
        if knob_led and isinstance(knob_led, dict):
            parts: list[str] = []
            if "mode" in knob_led:
                parts.append(f'mode = {_s(knob_led["mode"])}')
            if "low_color" in knob_led:
                parts.append(f'low_color = {_color(knob_led["low_color"])}')
            if "high_color" in knob_led:
                parts.append(f'high_color = {_color(knob_led["high_color"])}')
            if parts:
                lines.append(f'led = {{{", ".join(parts)}}}')
        lines.append("")

    buttons = cfg.get("buttons") or {}
    for i in range(5):
        btn = buttons.get(str(i))
        if not btn:
            continue
        lines.append(f"[buttons.{i}]")
        lines.append(f'action = {_s(btn.get("action", "mute_sink"))}')
        lines.append(f'target = {_s(btn.get("target", "default"))}')
        lines.append("")

    return "\n".join(lines)


# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI(title="TurnUp UI", docs_url=None, redoc_url=None)


# ── Config API ─────────────────────────────────────────────────────────────────

@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return load_config()


@app.post("/api/config")
async def save_config(request: Request) -> dict[str, bool]:
    cfg = await request.json()
    Path(DEFAULT_CONFIG_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(DEFAULT_CONFIG_PATH).write_text(config_to_toml(cfg))
    return {"ok": True}


# ── Running apps API ──────────────────────────────────────────────────────────

@app.get("/api/apps")
def list_running_apps() -> list[str]:
    """Return unique app names from currently active PulseAudio/PipeWire sink inputs.

    Returns both ``application.name`` and ``application.process.binary`` values
    (deduplicated) because the daemon's matching logic accepts either.  Falls
    back to an empty list if pulsectl is unavailable or no server is reachable.
    """
    try:
        import pulsectl  # optional — only present when [ui] extra is installed
        with pulsectl.Pulse("turnup-ui-apps") as pulse:
            seen: set[str] = set()
            for si in pulse.sink_input_list():
                for field in ("application.name", "application.process.binary"):
                    val = (si.proplist.get(field) or "").strip()
                    if val:
                        seen.add(val)
            return sorted(seen, key=str.lower)
    except Exception:
        return []


# ── Presets API ────────────────────────────────────────────────────────────────

def _preset_path(name: str) -> Path:
    if not name or not _SAFE_NAME.match(name):
        raise HTTPException(status_code=400, detail="Invalid preset name — use letters, numbers, spaces, hyphens, underscores, dots only")
    return PRESETS_DIR / f"{name}.toml"


@app.get("/api/presets")
def list_presets() -> list[str]:
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(p.stem for p in PRESETS_DIR.glob("*.toml"))


@app.get("/api/presets/{name}")
def get_preset(name: str) -> dict[str, Any]:
    path = _preset_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Preset not found")
    return load_config(str(path))


@app.post("/api/presets/{name}/save")
async def save_preset(name: str, request: Request) -> dict[str, bool]:
    path = _preset_path(name)
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    cfg = await request.json()
    path.write_text(config_to_toml(cfg))
    return {"ok": True}


@app.post("/api/presets/{name}/apply")
def apply_preset(name: str) -> dict[str, bool]:
    path = _preset_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Preset not found")
    cfg = load_config(str(path))
    Path(DEFAULT_CONFIG_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(DEFAULT_CONFIG_PATH).write_text(config_to_toml(cfg))
    return {"ok": True}


@app.delete("/api/presets/{name}")
def delete_preset(name: str) -> dict[str, bool]:
    path = _preset_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Preset not found")
    path.unlink()
    return {"ok": True}


# ── Static files ───────────────────────────────────────────────────────────────
# Must come AFTER all /api/* routes so the catch-all doesn't shadow them.
# Vite outputs hashed assets under assets/ and references them as /assets/…
# so we serve everything straight off STATIC_DIR at the URL root.

@app.get("/")
def root() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/{filepath:path}")
def static_file(filepath: str) -> FileResponse:
    path = (STATIC_DIR / filepath).resolve()
    # Safety: disallow path traversal outside STATIC_DIR
    try:
        path.relative_to(STATIC_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=403)
    if not path.exists() or not path.is_file():
        # SPA fallback — return index.html for unknown paths
        return FileResponse(str(STATIC_DIR / "index.html"))
    return FileResponse(str(path))


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s  %(name)s  %(message)s",
    )
    log.info("TurnUp UI → http://127.0.0.1:5173")
    uvicorn.run(app, host="127.0.0.1", port=5173, log_level="warning")


if __name__ == "__main__":
    main()
