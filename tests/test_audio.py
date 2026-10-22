import array
import subprocess
import wave
from pathlib import Path

import pytest

from app.errors import AppError
from assessment import audio


def make_video(path: Path, with_audio: bool, seconds: int = 5) -> Path:
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=160x120:rate=10:d={seconds}",
    ]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if with_audio:
        cmd += ["-c:a", "aac", "-shortest"]
    subprocess.run([*cmd, str(path)], check=True)
    return path


def make_video_with_late_tone(path: Path) -> Path:
    """20 s video (H.264 + AAC) whose audio is silent for 10 s, then a 440 Hz tone."""
    subprocess.run(
        ["ffmpeg", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:d=20",
         "-f", "lavfi", "-i", "aevalsrc=if(gte(t\\,10)\\,0.5*sin(2*PI*440*t)\\,0):d=20",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )  # fmt: skip
    return path


def rms(frames: bytes) -> float:
    samples = array.array("h", frames)
    return (sum(x * x for x in samples) / len(samples)) ** 0.5


def test_cut_turns_a_video_segment_into_16khz_mono_wav(tmp_path: Path) -> None:
    video = make_video_with_late_tone(tmp_path / "class.mp4")
    out = tmp_path / "slice.wav"

    audio.cut(video, out, start_s=8, length_s=4)

    with wave.open(str(out)) as wav:
        assert wav.getframerate() == 16000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getnframes() / 16000 == pytest.approx(4, abs=0.1)
        frames = wav.readframes(wav.getnframes())
    # The seek is exact: 8–10 s is silence and 10–12 s is the tone (checked away from the edge).
    second = 16000 * 2  # bytes per second of 16-bit mono
    assert rms(frames[: int(1.8 * second)]) < 50
    assert rms(frames[int(2.2 * second) :]) > 5000


def test_check_audio_returns_the_duration(tmp_path: Path) -> None:
    video = make_video(tmp_path / "class.mp4", with_audio=True)

    assert audio.check_audio(video) == pytest.approx(5, abs=0.5)


def test_video_without_audio_raises_no_audio_stream(tmp_path: Path) -> None:
    video = make_video(tmp_path / "silent.mp4", with_audio=False)

    with pytest.raises(AppError) as exc:
        audio.check_audio(video)

    assert exc.value.code == "NO_AUDIO_STREAM"


def test_corrupt_file_raises_media_failed(tmp_path: Path) -> None:
    video = tmp_path / "notes.mp4"
    video.write_text("this is a text file renamed to .mp4")

    with pytest.raises(AppError) as exc:
        audio.check_audio(video)

    assert exc.value.code == "MEDIA_FAILED"
