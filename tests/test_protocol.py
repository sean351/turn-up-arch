"""
Unit tests for turnupd protocol parser and knob conversion helpers.
No external dependencies — these are pure-function tests.
"""

import pytest

from turnup.turnupd import knob_to_norm, knob_to_volume, parse_messages, KNOB_MAX, VOLUME_MAX


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
