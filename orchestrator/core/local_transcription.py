"""Local, ephemeral speech-to-text for the Command Center."""

import re
from functools import lru_cache
from pathlib import Path

from orchestrator.config import Settings

_RESTART_CUE = re.compile(
    r"\b(?:never\s*mind|scratch\s+that|forget\s+(?:that|it)|actually|instead)\b"
    r"[\s,.;:!\-]*(?P<request>.+)$",
    flags=re.IGNORECASE,
)
_LEADING_FILLER = re.compile(r"^(?:(?:uh+|um+|er+|okay|well)[\s,.-]*)+", flags=re.IGNORECASE)


class LocalTranscriptionError(RuntimeError):
    """Raised when local speech-to-text cannot produce a transcript."""


def meaningful_command_transcript(transcript: str) -> str:
    """Prefer the final request after an explicit spoken change of mind.

    Speech-to-text faithfully includes abandoned starts such as "turn on... never
    mind." For a command interface, the request after that clear restart cue is
    more useful. This is deliberately limited to explicit cues; ambiguous speech
    remains unchanged for the resident to review.
    """
    normalized = " ".join(transcript.split())
    match = _RESTART_CUE.search(normalized)
    if match is None:
        return normalized
    request = _LEADING_FILLER.sub("", match.group("request")).strip()
    if not request:
        return normalized
    return request[0].upper() + request[1:]


@lru_cache(maxsize=2)
def _whisper_model(model_name: str, model_directory: str):
    """Load and cache a local Whisper model without retaining user audio."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:  # pragma: no cover - deployment configuration
        raise LocalTranscriptionError(
            "Local speech-to-text is not installed on this EcoNest instance."
        ) from error
    return WhisperModel(
        model_name,
        device="cpu",
        compute_type="int8",
        download_root=model_directory,
    )


def transcribe_audio(audio_path: Path, settings: Settings) -> str:
    """Transcribe a temporary audio file locally and return its plain text."""
    if not settings.LOCAL_STT_ENABLED:
        raise LocalTranscriptionError("Local speech-to-text is disabled.")
    try:
        model = _whisper_model(settings.LOCAL_STT_MODEL, settings.LOCAL_STT_MODEL_DIR)
        segments, _ = model.transcribe(
            str(audio_path),
            language=settings.LOCAL_STT_LANGUAGE,
            beam_size=settings.LOCAL_STT_BEAM_SIZE,
            vad_filter=settings.LOCAL_STT_VAD_FILTER,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        transcript = " ".join(segment.text.strip() for segment in segments).strip()
    except LocalTranscriptionError:
        raise
    except Exception as error:
        raise LocalTranscriptionError(
            "EcoNest could not transcribe that recording locally."
        ) from error
    if not transcript:
        raise LocalTranscriptionError("No speech was detected in that recording.")
    return transcript
