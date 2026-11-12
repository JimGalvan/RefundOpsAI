"""Offline checks for media confinement, timing and secret-safe narration errors."""

import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import common  # noqa: E402
import cut  # noqa: E402
import recorder  # noqa: E402
import tts  # noqa: E402


@pytest.fixture
def media(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(common, "ROOT", tmp_path)
    monkeypatch.setattr(common, "VIDEO", tmp_path / "video")
    return tmp_path


def test_output_cannot_escape_media_root(media: Path) -> None:
    assert common.media_path("video/voice/intro.mp3", "voice") == media / "video/voice/intro.mp3"
    for path in ("elsewhere.mp3", "video/voice/../../escape.mp3", "video/exports/a.mp3"):
        with pytest.raises(ValueError, match="must be under"):
            common.media_path(path, "voice")


def test_environment_credentials_do_not_read_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    import dotenv

    monkeypatch.setenv("ELEVENLABS_API_KEY", "offline-key")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "offline-voice")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Must not read dotenv when environment is complete")

    monkeypatch.setattr(dotenv, "dotenv_values", forbidden)
    assert tts.credentials() == ("offline-key", "offline-voice")


def test_missing_credentials_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import dotenv

    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    monkeypatch.setattr(dotenv, "dotenv_values", lambda *args, **kwargs: {})
    with pytest.raises(ValueError, match="ELEVENLABS_API_KEY is missing"):
        tts.credentials()


def test_http_error_does_not_echo_response_or_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise urllib.error.HTTPError("https://example.invalid", 401, "private response", {}, None)

    monkeypatch.setattr(tts.urllib.request, "urlopen", fail)
    with pytest.raises(ValueError, match="HTTP 401") as error:
        tts.synthesize("Hello", "model", "secret-key", "voice")
    assert "secret" not in str(error.value)
    assert "private" not in str(error.value)


def test_speed_label_and_narration_fit(media: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cut, "duration", lambda path: 60 if path.suffix == ".mp4" else 4)
    item = {"clip": "video/footage/take.mp4", "start": 0, "end": 15, "speed": 3,
            "label": "Investigation", "narration": "video/voice/intro.mp3"}
    segment = cut.validate([item])[0]
    assert segment["length"] == 5
    assert segment["label"] == "3x | Investigation"
    with pytest.raises(ValueError, match="narration exceeds"):
        cut.validate([{**item, "end": 3}])
    with pytest.raises(ValueError, match="end must follow"):
        cut.validate([{**item, "end": 61}])


def test_recorder_keeps_static_hold(media: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    take = media / "video/footage/take"
    (take / "frames").mkdir(parents=True)
    (take / "frames/00000000.jpg").touch()
    entries = [{"file": "00000000.jpg", "time": 0.2}, {"file": None, "time": 8}]
    (take / "frames.jsonl").write_text("\n".join(json.dumps(entry) for entry in entries))
    commands = []
    monkeypatch.setattr(recorder, "run", commands.append)
    recorder.build(take, media / "video/footage/take.mp4")
    assert "duration 8.000000000" in (take / "frames.txt").read_text()
    assert "cfr" in commands[0]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_invalid_times_rejected(value: float) -> None:
    with pytest.raises(ValueError):
        common.number(value, "start")
