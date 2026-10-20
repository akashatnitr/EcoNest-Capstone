"""Tests for fast local command transcription settings."""

from pathlib import Path
from types import SimpleNamespace

from orchestrator.config import Settings
from orchestrator.core import local_transcription


def test_local_transcription_uses_low_latency_command_settings(monkeypatch) -> None:
    """Short commands should use one decoding pass without a VAD pre-pass."""
    captured: dict[str, object] = {}

    class FakeModel:
        def transcribe(self, audio_path: str, **kwargs: object):
            captured["audio_path"] = audio_path
            captured.update(kwargs)
            return [SimpleNamespace(text=" Turn off the light ")], None

    monkeypatch.setattr(local_transcription, "_whisper_model", lambda *_args: FakeModel())
    settings = Settings(LOCAL_STT_ENABLED=True)

    transcript = local_transcription.transcribe_audio(Path("/tmp/voice.webm"), settings)

    assert transcript == "Turn off the light"
    assert captured["beam_size"] == 1
    assert captured["vad_filter"] is False
    assert captured["condition_on_previous_text"] is False
    assert captured["without_timestamps"] is True


def test_meaningful_command_transcript_keeps_final_request_after_change_of_mind() -> None:
    """An explicitly abandoned spoken start does not become the command."""
    transcript = local_transcription.meaningful_command_transcript(
        "Turn, um, never mind. Uh, give me an energy recommendation."
    )

    assert transcript == "Give me an energy recommendation."


def test_meaningful_command_transcript_recognizes_nevermind_as_one_word() -> None:
    """Speech-to-text commonly writes the restart cue as one word."""
    transcript = local_transcription.meaningful_command_transcript(
        "Turn on the light nevermind, give me an energy recommendation."
    )

    assert transcript == "Give me an energy recommendation."


def test_meaningful_command_transcript_leaves_ambiguous_speech_unchanged() -> None:
    """Only explicit restart cues are cleaned automatically."""
    transcript = local_transcription.meaningful_command_transcript(
        "Could you turn on the hallway light?"
    )

    assert transcript == "Could you turn on the hallway light?"
