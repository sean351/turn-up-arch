# turnup

A lightweight daemon that bridges a USB serial device (physical knobs and
buttons) to PipeWire/PulseAudio on Linux.  Map each knob to per-sink,
per-source, or per-application volume; assign buttons to mute toggles or
arbitrary shell commands.

## Requirements

- Python 3.10+
- [pyserial](https://pypi.org/project/pyserial/)
- [pulsectl](https://pypi.org/project/pulsectl/)
- PipeWire (with `pipewire-pulse`) or PulseAudio
- `playerctl` *(optional — for media key bindings)*

## Installation

### Arch Linux (AUR)

```sh
yay -S turnup
```

Or manually:

```sh
git clone https://aur.archlinux.org/turnup.git
cd turnup
makepkg -si
```

### From source

```sh
git clone https://github.com/sean351/turn-up-arch.git
cd turn-up-arch
pip install .
```

### From source (pip)

```sh
pip install .
```

## Configuration

On first run `turnupd` writes a default config to
`~/.config/turnup/config.json`.  Edit it to match your device layout.
See [`contrib/config.example.json`](contrib/config.example.json) for a
fully commented example.

```jsonc
{
  "port": "/dev/ttyACM0",
  "baud": 115200,

  "knobs": {
    "0": { "action": "sink_volume",  "target": "default" },
    "1": { "action": "group_volume", "targets": ["vlc", "spotify"] },
    "2": { "action": "app_volume",   "target": "Brave" },
    "3": { "action": "source_volume","target": "default" }
  },

  "buttons": {
    "0": { "action": "mute_sink",  "target": "default" },
    "1": { "action": "command",    "target": "playerctl previous" },
    "2": { "action": "command",    "target": "playerctl play-pause" },
    "3": { "action": "command",    "target": "playerctl next" },
    "4": { "action": "mute_source","target": "default" }
  }
}
```

### Knob actions

| Action | Description |
|---|---|
| `sink_volume` | Output device volume (0–150 %) |
| `source_volume` | Mic / input volume (0–100 %) |
| `app_volume` | Single application volume, matched by name or binary |
| `group_volume` | Multiple applications at once — use `"targets": [...]` |

### Button actions

| Action | Description |
|---|---|
| `mute_sink` | Toggle output mute |
| `mute_source` | Toggle mic mute |
| `command` | Run an arbitrary shell command |

## Running as a service

A systemd user service unit is included:

```sh
# After install via AUR or pip:
systemctl --user enable --now turnupd.service
```

To view logs:

```sh
journalctl --user -u turnupd -f
```

## Running manually

```sh
turnupd
```

Pass a custom config path with the `TURNUP_CONFIG` environment variable
*(planned — currently edit `~/.config/turnup/config.json` directly)*.

## Hardware

The daemon expects a USB-serial device speaking a simple binary protocol:

| Frame | Bytes | Description |
|---|---|---|
| Heartbeat | `FE 02 FF` | Keepalive |
| Button | `FE 06/07 <id> FF` | `06` = press, `07` = release |
| Knob | `FE 03 <id> <hi> <lo> FF` | 10-bit ADC value, big-endian |

Tested with an RP2040-based board.  Any microcontroller that enumerates as a
USB-CDC serial port and speaks the protocol above will work.

## License

MIT — see [LICENSE](LICENSE).
