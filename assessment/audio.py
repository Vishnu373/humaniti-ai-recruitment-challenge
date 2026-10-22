"""Every ffmpeg/ffprobe call lives here. Failures and timeouts become MEDIA_FAILED."""

import json
import subprocess
from pathlib import Path

from app.errors import AppError

PROBE_TIMEOUT_S = 60
EXTRACT_TIMEOUT_S = 15 * 60


def _run(cmd: list[str], timeout_s: int) -> str:
    """Run a command and return its stdout; raise MEDIA_FAILED with stderr on failure."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        raise AppError("MEDIA_FAILED", f"{cmd[0]} timed out after {timeout_s} s") from e
    if result.returncode != 0:
        stderr = result.stderr.strip()[-1000:]
        raise AppError("MEDIA_FAILED", f"{cmd[0]} failed: {stderr}")
    return result.stdout


def probe(path: Path) -> tuple[float, bool]:
    """Return (duration in seconds, whether there's an audio stream)."""
    out = _run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration:stream=codec_type",
            "-of", "json",
            str(path),
        ],
        PROBE_TIMEOUT_S,
    )  # fmt: skip
    info = json.loads(out)
    try:
        duration_s = float(info["format"]["duration"])
    except (KeyError, ValueError) as e:
        raise AppError("MEDIA_FAILED", "ffprobe reported no duration") from e
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    return duration_s, has_audio


def check_audio(video: Path) -> float:
    """Return the video's duration in seconds; raise NO_AUDIO_STREAM if it has no audio track."""
    duration_s, has_audio = probe(video)
    if not has_audio:
        raise AppError("NO_AUDIO_STREAM", "The video has no audio track")
    return duration_s


def cut(src: Path, out: Path, start_s: float, length_s: float) -> None:
    """Write `length_s` seconds of `src`'s audio from `start_s` as 16 kHz mono 16-bit WAV.

    `src` can be the video itself: only that segment is read, and since the audio is decoded
    the seek is exact, not snapped to a keyframe.
    """
    _run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-ss", str(start_s), "-t", str(length_s),
            "-i", str(src),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
            str(out),
        ],
        EXTRACT_TIMEOUT_S,
    )  # fmt: skip
