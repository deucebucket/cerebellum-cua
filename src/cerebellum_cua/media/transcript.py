"""Optional audio transcript for the WITH-audio case.

The silent-visual-event case ignores this module entirely; it exists for clips
where speech carries the meaning, so the agent can reason over timed text rather
than the waveform. The transcription backend (``faster-whisper``, falling back to
``whisper``) is imported lazily inside :func:`transcribe`, so importing this
module never requires either package. If neither is installed, :func:`transcribe`
raises a typed :class:`MediaError`.
"""

from __future__ import annotations

from typing import Any

from cerebellum_cua.media.errors import MediaError


def transcribe(path: str, model_size: str = "base") -> list[dict[str, Any]]:
    """Transcribe ``path`` into timed segments ``[{start, end, text}]``.

    Tries ``faster-whisper`` first (CTranslate2 backend), then the reference
    ``whisper`` package.

    Args:
        path: Audio or video file to transcribe.
        model_size: Whisper model size (``"tiny"`` … ``"large"``).

    Returns:
        A list of ``{"start": float, "end": float, "text": str}`` segments.

    Raises:
        MediaError: If no transcript backend is installed, or transcription
            fails.
    """
    segments = _transcribe_faster_whisper(path, model_size)
    if segments is not None:
        return segments
    segments = _transcribe_whisper(path, model_size)
    if segments is not None:
        return segments
    raise MediaError(
        "transcript backend unavailable: install the optional 'media' extra "
        "(faster-whisper) or the 'whisper' package."
    )


def _transcribe_faster_whisper(
    path: str, model_size: str
) -> list[dict[str, Any]] | None:
    """Transcribe with ``faster-whisper``; return None if it is not installed."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    try:
        model = WhisperModel(model_size)
        seg_iter, _info = model.transcribe(path)
        return [
            {"start": float(s.start), "end": float(s.end), "text": s.text.strip()}
            for s in seg_iter
        ]
    except Exception as exc:  # noqa: BLE001 - backend errors are opaque
        raise MediaError(f"faster-whisper transcription failed: {exc}") from exc


def _transcribe_whisper(path: str, model_size: str) -> list[dict[str, Any]] | None:
    """Transcribe with the reference ``whisper``; return None if not installed."""
    try:
        import whisper
    except ImportError:
        return None
    try:
        model = whisper.load_model(model_size)
        result = model.transcribe(path)
        return [
            {
                "start": float(s["start"]),
                "end": float(s["end"]),
                "text": str(s["text"]).strip(),
            }
            for s in result.get("segments", [])
        ]
    except Exception as exc:  # noqa: BLE001 - backend errors are opaque
        raise MediaError(f"whisper transcription failed: {exc}") from exc


__all__ = ["transcribe"]
