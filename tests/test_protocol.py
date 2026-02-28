"""
Unit tests for turnupd protocol parser and knob conversion helpers.
No external dependencies — these are pure-function tests.
"""

import pytest
from unittest.mock import MagicMock, call, patch

from turnup.turnupd import (
    KNOB_MAX,
    NUM_KNOBS,
    VOLUME_MAX,
    handle_knob,
    knob_to_norm,
    knob_to_volume,
    parse_messages,
)


# ── knob_to_norm ──────────────────────────────────────────────────────────────

class TestKnobToNorm:
    def test_min_value(self):
        assert knob_to_norm(0) == pytest.approx(0.0)

    def test_max_value(self):
        assert knob_to_norm(KNOB_MAX) == pytest.approx(1.0)

    def test_midpoint(self):
        result = knob_to_norm(KNOB_MAX // 2)
        assert 0.49 < result < 0.51

    def test_result_is_rounded(self):
        # Result must have at most 4 decimal places.
        result = knob_to_norm(333)
        assert result == round(result, 4)


# ── knob_to_volume ────────────────────────────────────────────────────────────

class TestKnobToVolume:
    def test_min_is_zero(self):
        assert knob_to_volume(0) == pytest.approx(0.0)

    def test_max_is_volume_max(self):
        assert knob_to_volume(KNOB_MAX) == pytest.approx(VOLUME_MAX)

    def test_proportional(self):
        half = knob_to_volume(KNOB_MAX // 2)
        full = knob_to_volume(KNOB_MAX)
        assert half == pytest.approx(full / 2, rel=0.01)


# ── parse_messages ────────────────────────────────────────────────────────────

class TestParseMessages:
    # ── heartbeat ─────────────────────────────────────────────────────────────

    def test_heartbeat(self):
        buf = bytearray([0xFE, 0x02, 0xFF])
        msgs, remainder = parse_messages(buf)
        assert msgs == [{"type": "heartbeat"}]
        assert remainder == bytearray()

    def test_heartbeat_leaves_trailing_bytes(self):
        # Trailing non-0xFE bytes are consumed (no partial frame to preserve).
        buf = bytearray([0xFE, 0x02, 0xFF, 0x01, 0x02])
        msgs, remainder = parse_messages(buf)
        assert len(msgs) == 1
        assert remainder == bytearray()

    # ── button ────────────────────────────────────────────────────────────────

    def test_button_press(self):
        buf = bytearray([0xFE, 0x06, 0x03, 0xFF])
        msgs, _ = parse_messages(buf)
        assert msgs == [{"type": "button", "action": "press", "id": 3}]

    def test_button_release(self):
        buf = bytearray([0xFE, 0x07, 0x02, 0xFF])
        msgs, _ = parse_messages(buf)
        assert msgs == [{"type": "button", "action": "release", "id": 2}]

    def test_all_button_ids(self):
        for btn_id in range(5):
            buf = bytearray([0xFE, 0x06, btn_id, 0xFF])
            msgs, _ = parse_messages(buf)
            assert msgs[0]["id"] == btn_id

    # ── knob ──────────────────────────────────────────────────────────────────

    def test_knob_message(self):
        # value = 0x03F4 = 1012
        buf = bytearray([0xFE, 0x03, 0x01, 0x03, 0xF4, 0xFF])
        msgs, _ = parse_messages(buf)
        assert msgs == [{"type": "knob", "id": 1, "value": 1012}]

    def test_knob_value_zero(self):
        buf = bytearray([0xFE, 0x03, 0x00, 0x00, 0x00, 0xFF])
        msgs, _ = parse_messages(buf)
        assert msgs[0]["value"] == 0

    def test_knob_value_max(self):
        buf = bytearray([0xFE, 0x03, 0x04, 0x03, 0xF4, 0xFF])
        msgs, _ = parse_messages(buf)
        assert msgs[0]["value"] == KNOB_MAX

    # ── multi-message ─────────────────────────────────────────────────────────

    def test_multiple_messages_in_one_buffer(self):
        heartbeat = bytearray([0xFE, 0x02, 0xFF])
        button    = bytearray([0xFE, 0x06, 0x00, 0xFF])
        knob      = bytearray([0xFE, 0x03, 0x02, 0x01, 0xF4, 0xFF])
        msgs, remainder = parse_messages(heartbeat + button + knob)
        assert len(msgs) == 3
        assert msgs[0]["type"] == "heartbeat"
        assert msgs[1]["type"] == "button"
        assert msgs[2]["type"] == "knob"
        assert remainder == bytearray()

    def test_garbage_bytes_skipped(self):
        buf = bytearray([0x00, 0x11, 0xFE, 0x02, 0xFF])
        msgs, _ = parse_messages(buf)
        assert msgs == [{"type": "heartbeat"}]

    def test_incomplete_frame_stays_in_remainder(self):
        # The parser advances byte-by-byte when a frame is unrecognised, so an
        # incomplete knob frame (missing terminator 0xFF) is consumed as garbage.
        buf = bytearray([0xFE, 0x03, 0x01, 0x03])
        msgs, remainder = parse_messages(buf)
        assert msgs == []
        assert remainder == bytearray()

    def test_empty_buffer(self):
        msgs, remainder = parse_messages(bytearray())
        assert msgs == []
        assert remainder == bytearray()


# ── handle_knob ───────────────────────────────────────────────────────────────

def _make_knob_fixtures(knob_id: int = 0, action: str = "sink_volume"):
    """Return (config, pulse_mock, ser_mock, knob_norms, last_led_colors, last_knob_event)."""
    config = {
        "knobs": {
            str(knob_id): {
                "action": action,
                "target": "default",
                "led": {"low_color": "#000000", "high_color": "#ffffff"},
            }
        }
    }
    pulse = MagicMock()
    ser   = MagicMock()
    knob_norms       = [0.0] * NUM_KNOBS
    last_led_colors  = [(0, 0, 0)] * NUM_KNOBS
    last_knob_event  = [0.0]
    return config, pulse, ser, knob_norms, last_led_colors, last_knob_event


class TestHandleKnobLEDDedup:
    """Fix 1 — duplicate LED packets must be suppressed."""

    def test_sends_leds_when_color_changes(self):
        config, pulse, ser, knob_norms, llc, lke = _make_knob_fixtures()
        # Turn knob 0 to max; color should change from (0,0,0).
        handle_knob(0, KNOB_MAX, config, pulse, ser, knob_norms, llc, lke)
        assert ser.write.called

    def test_skips_leds_when_color_unchanged(self):
        config, pulse, ser, knob_norms, llc, lke = _make_knob_fixtures()
        # First call sets the color.
        handle_knob(0, KNOB_MAX, config, pulse, ser, knob_norms, llc, lke)
        write_count_after_first = ser.write.call_count
        # Second call with same value — color is identical, no new write.
        handle_knob(0, KNOB_MAX, config, pulse, ser, knob_norms, llc, lke)
        assert ser.write.call_count == write_count_after_first

    def test_last_led_colors_updated_after_send(self):
        config, pulse, ser, knob_norms, llc, lke = _make_knob_fixtures()
        handle_knob(0, KNOB_MAX, config, pulse, ser, knob_norms, llc, lke)
        # last_led_colors must not remain all-zero after a successful send.
        assert llc != [(0, 0, 0)] * NUM_KNOBS

    def test_color_change_after_stable_triggers_send(self):
        config, pulse, ser, knob_norms, llc, lke = _make_knob_fixtures()
        # Reach a stable state at max value.
        handle_knob(0, KNOB_MAX, config, pulse, ser, knob_norms, llc, lke)
        handle_knob(0, KNOB_MAX, config, pulse, ser, knob_norms, llc, lke)
        count_stable = ser.write.call_count
        # Now change to zero — should trigger another send.
        handle_knob(0, 0, config, pulse, ser, knob_norms, llc, lke)
        assert ser.write.call_count == count_stable + 1


class TestHandleKnobLastKnobEvent:
    """Fix 2 — last_knob_event[0] must be updated on every call."""

    def test_last_knob_event_updated(self):
        import time
        config, pulse, ser, knob_norms, llc, lke = _make_knob_fixtures()
        before = time.monotonic()
        handle_knob(0, 500, config, pulse, ser, knob_norms, llc, lke)
        after = time.monotonic()
        assert before <= lke[0] <= after

    def test_last_knob_event_updated_even_when_led_unchanged(self):
        import time
        config, pulse, ser, knob_norms, llc, lke = _make_knob_fixtures()
        handle_knob(0, KNOB_MAX, config, pulse, ser, knob_norms, llc, lke)
        first_ts = lke[0]
        import time as _t; _t.sleep(0.01)
        handle_knob(0, KNOB_MAX, config, pulse, ser, knob_norms, llc, lke)
        # Timestamp must advance even though color did not change.
        assert lke[0] > first_ts
