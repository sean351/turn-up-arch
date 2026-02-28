"""
Unit tests for turnup.audio — MPRISController and PulseController.

All external dependencies (pulsectl, subprocess/playerctl) are mocked so
these tests run without a running PulseAudio/PipeWire server or playerctl.
"""

from unittest.mock import MagicMock, patch, call
import pytest

from turnup.audio import MPRISController, PulseController, VOLUME_MAX


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_sink_input(name: str, binary: str, volume: float) -> MagicMock:
    """Return a fake pulsectl sink-input object."""
    inp = MagicMock()
    inp.proplist = {"application.name": name, "application.process.binary": binary}
    inp.volume.value_flat = volume
    return inp


# ── MPRISController ───────────────────────────────────────────────────────────

class TestMPRISControllerFindPlayer:
    def _make(self, players: list[str]) -> MPRISController:
        ctrl = MPRISController()
        # Pre-populate cache so _refresh_players is a no-op during tests.
        ctrl._players = players
        ctrl._players_ts = float("inf")  # never expires
        return ctrl

    def test_exact_match(self):
        ctrl = self._make(["org.mpris.MediaPlayer2.spotify"])
        assert ctrl.find_player("spotify") == "org.mpris.MediaPlayer2.spotify"

    def test_case_insensitive(self):
        ctrl = self._make(["org.mpris.MediaPlayer2.Spotify"])
        assert ctrl.find_player("SPOTIFY") == "org.mpris.MediaPlayer2.Spotify"

    def test_no_match_returns_none(self):
        ctrl = self._make(["org.mpris.MediaPlayer2.vlc"])
        assert ctrl.find_player("brave") is None

    def test_empty_player_list_returns_none(self):
        ctrl = self._make([])
        assert ctrl.find_player("spotify") is None

    def test_returns_first_match(self):
        ctrl = self._make([
            "org.mpris.MediaPlayer2.spotify",
            "org.mpris.MediaPlayer2.spotify.instance2",
        ])
        assert ctrl.find_player("spotify") == "org.mpris.MediaPlayer2.spotify"


class TestMPRISControllerGetVolume:
    def _make_with_player(self, player: str) -> MPRISController:
        ctrl = MPRISController()
        ctrl._players = [player]
        ctrl._players_ts = float("inf")
        return ctrl

    def test_returns_float_on_success(self):
        ctrl = self._make_with_player("org.mpris.MediaPlayer2.spotify")
        with patch.object(ctrl, "_run", return_value=(True, "0.75")):
            assert ctrl.get_volume("spotify") == pytest.approx(0.75)

    def test_clamps_above_one(self):
        ctrl = self._make_with_player("org.mpris.MediaPlayer2.spotify")
        with patch.object(ctrl, "_run", return_value=(True, "1.5")):
            assert ctrl.get_volume("spotify") == pytest.approx(1.0)

    def test_clamps_below_zero(self):
        ctrl = self._make_with_player("org.mpris.MediaPlayer2.spotify")
        with patch.object(ctrl, "_run", return_value=(True, "-0.1")):
            assert ctrl.get_volume("spotify") == pytest.approx(0.0)

    def test_returns_none_when_player_not_found(self):
        ctrl = MPRISController()
        ctrl._players = []
        ctrl._players_ts = float("inf")
        assert ctrl.get_volume("spotify") is None

    def test_returns_none_on_playerctl_failure(self):
        ctrl = self._make_with_player("org.mpris.MediaPlayer2.spotify")
        with patch.object(ctrl, "_run", return_value=(False, "")):
            assert ctrl.get_volume("spotify") is None

    def test_returns_none_on_invalid_output(self):
        ctrl = self._make_with_player("org.mpris.MediaPlayer2.spotify")
        with patch.object(ctrl, "_run", return_value=(True, "not-a-number")):
            assert ctrl.get_volume("spotify") is None


class TestMPRISControllerSetVolume:
    def _make_with_player(self, player: str) -> MPRISController:
        ctrl = MPRISController()
        ctrl._players = [player]
        ctrl._players_ts = float("inf")
        return ctrl

    def test_returns_true_on_success(self):
        ctrl = self._make_with_player("org.mpris.MediaPlayer2.spotify")
        with patch.object(ctrl, "_run", return_value=(True, "")) as mock_run:
            result = ctrl.set_volume("spotify", 0.5)
        assert result is True
        mock_run.assert_called_once_with(
            "--player", "org.mpris.MediaPlayer2.spotify", "volume", "0.5000"
        )

    def test_clamps_volume_to_one(self):
        ctrl = self._make_with_player("org.mpris.MediaPlayer2.spotify")
        with patch.object(ctrl, "_run", return_value=(True, "")) as mock_run:
            ctrl.set_volume("spotify", 1.5)
        _, _, _, sent_vol = mock_run.call_args[0]
        assert float(sent_vol) == pytest.approx(1.0)

    def test_clamps_volume_to_zero(self):
        ctrl = self._make_with_player("org.mpris.MediaPlayer2.spotify")
        with patch.object(ctrl, "_run", return_value=(True, "")) as mock_run:
            ctrl.set_volume("spotify", -0.5)
        _, _, _, sent_vol = mock_run.call_args[0]
        assert float(sent_vol) == pytest.approx(0.0)

    def test_returns_false_when_no_player(self):
        ctrl = MPRISController()
        ctrl._players = []
        ctrl._players_ts = float("inf")
        assert ctrl.set_volume("spotify", 0.5) is False

    def test_returns_false_on_playerctl_failure(self):
        ctrl = self._make_with_player("org.mpris.MediaPlayer2.spotify")
        with patch.object(ctrl, "_run", return_value=(False, "")):
            assert ctrl.set_volume("spotify", 0.5) is False


class TestMPRISControllerRefreshPlayers:
    def test_populates_cache_from_playerctl(self):
        ctrl = MPRISController()
        with patch.object(ctrl, "_run", return_value=(True, "spotify\nvlc\n")):
            ctrl._refresh_players(force=True)
        assert ctrl._players == ["spotify", "vlc"]

    def test_empty_cache_on_playerctl_failure(self):
        ctrl = MPRISController()
        ctrl._players = ["old-player"]
        with patch.object(ctrl, "_run", return_value=(False, "")):
            ctrl._refresh_players(force=True)
        assert ctrl._players == []

    def test_skips_refresh_when_cache_valid(self):
        ctrl = MPRISController()
        ctrl._players = ["spotify"]
        ctrl._players_ts = float("inf")
        with patch.object(ctrl, "_run") as mock_run:
            ctrl._refresh_players()
        mock_run.assert_not_called()


# ── PulseController ───────────────────────────────────────────────────────────

@pytest.fixture
def mock_pulse_lib():
    """Patch pulsectl.Pulse so PulseController never touches a real server."""
    with patch("turnup.audio.pulsectl.Pulse") as mock_cls:
        mock_cls.return_value.__enter__ = lambda s: s
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)
        yield mock_cls


class TestPulseControllerSetAppVolume:
    def test_uses_mpris_when_available(self, mock_pulse_lib):
        mpris = MagicMock(spec=MPRISController)
        mpris.set_volume.return_value = True

        pulse = PulseController(mpris=mpris)
        pulse.set_app_volume("spotify", 0.6)

        mpris.set_volume.assert_called_once_with("spotify", pytest.approx(0.6))
        # PA stream should NOT be touched.
        pulse._pulse.sink_input_list.assert_not_called()

    def test_falls_back_to_pa_when_mpris_fails(self, mock_pulse_lib):
        mpris = MagicMock(spec=MPRISController)
        mpris.set_volume.return_value = False

        inp = _make_sink_input("Brave", "brave", 1.0)
        pulse = PulseController(mpris=mpris)
        pulse._pulse.sink_input_list.return_value = [inp]

        pulse.set_app_volume("brave", 0.4)

        pulse._pulse.volume_set_all_chans.assert_called_once_with(inp, pytest.approx(0.4))

    def test_falls_back_to_pa_when_no_mpris(self, mock_pulse_lib):
        inp = _make_sink_input("Brave", "brave", 1.0)
        pulse = PulseController(mpris=None)
        pulse._pulse.sink_input_list.return_value = [inp]

        pulse.set_app_volume("brave", 0.3)

        pulse._pulse.volume_set_all_chans.assert_called_once_with(inp, pytest.approx(0.3))

    def test_clamps_volume_at_volume_max(self, mock_pulse_lib):
        mpris = MagicMock(spec=MPRISController)
        mpris.set_volume.return_value = True

        pulse = PulseController(mpris=mpris)
        pulse.set_app_volume("spotify", 2.0)

        mpris.set_volume.assert_called_once_with("spotify", pytest.approx(VOLUME_MAX))

    def test_matches_by_binary_name(self, mock_pulse_lib):
        inp = _make_sink_input("", "spotify", 1.0)
        pulse = PulseController(mpris=None)
        pulse._pulse.sink_input_list.return_value = [inp]

        pulse.set_app_volume("spotify", 0.5)

        pulse._pulse.volume_set_all_chans.assert_called_once_with(inp, pytest.approx(0.5))

    def test_updates_all_matching_streams(self, mock_pulse_lib):
        """All streams for an app (e.g. Spotify crossfade) should be updated."""
        inp1 = _make_sink_input("Spotify", "spotify", 1.0)
        inp2 = _make_sink_input("Spotify", "spotify", 1.0)
        pulse = PulseController(mpris=None)
        pulse._pulse.sink_input_list.return_value = [inp1, inp2]

        pulse.set_app_volume("spotify", 0.5)

        assert pulse._pulse.volume_set_all_chans.call_count == 2


class TestPulseControllerGetAppVolumeNorm:
    def test_prefers_mpris(self, mock_pulse_lib):
        mpris = MagicMock(spec=MPRISController)
        mpris.get_volume.return_value = 0.7

        pulse = PulseController(mpris=mpris)
        result = pulse.get_app_volume_norm("spotify")

        assert result == pytest.approx(0.7)
        pulse._pulse.sink_input_list.assert_not_called()

    def test_falls_back_to_pa_when_mpris_returns_none(self, mock_pulse_lib):
        mpris = MagicMock(spec=MPRISController)
        mpris.get_volume.return_value = None

        inp = _make_sink_input("Brave", "brave", 0.55)
        pulse = PulseController(mpris=mpris)
        pulse._pulse.sink_input_list.return_value = [inp]

        result = pulse.get_app_volume_norm("brave")

        assert result == pytest.approx(0.55)

    def test_returns_none_when_app_not_found(self, mock_pulse_lib):
        pulse = PulseController(mpris=None)
        pulse._pulse.sink_input_list.return_value = []

        assert pulse.get_app_volume_norm("nonexistent") is None

    def test_clamps_pa_volume_at_one(self, mock_pulse_lib):
        inp = _make_sink_input("Spotify", "spotify", 1.5)
        pulse = PulseController(mpris=None)
        pulse._pulse.sink_input_list.return_value = [inp]

        assert pulse.get_app_volume_norm("spotify") == pytest.approx(1.0)


class TestPulseControllerDrainEvents:
    def test_returns_false_when_empty(self, mock_pulse_lib):
        pulse = PulseController()
        assert pulse.drain_events() is False

    def test_returns_true_when_events_present(self, mock_pulse_lib):
        pulse = PulseController()
        pulse._event_q.put(1)
        pulse._event_q.put(2)
        assert pulse.drain_events() is True
        assert pulse._event_q.empty()
