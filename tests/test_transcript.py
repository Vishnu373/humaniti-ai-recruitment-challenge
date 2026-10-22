import subprocess
from pathlib import Path

import pytest

from app.errors import AppError
from assessment import audio, transcript

# Shape of one Sarvam Batch output file with diarization and timestamps on.
# Replace with a real saved response in tests/fixtures/ after the first live run.
DIARIZED_OUTPUT: dict[str, object] = {
    "request_id": "req-1",
    "transcript": "Good morning everyone. Good morning sir.",
    "language_code": "en-IN",
    "timestamps": {
        "words": ["Good morning everyone.", "Good morning sir."],
        "start_time_seconds": [1.0, 4.5],
        "end_time_seconds": [3.2, 6.0],
    },
    "diarized_transcript": {
        "entries": [
            {
                "transcript": "Good morning everyone.",
                "start_time_seconds": 1.0,
                "end_time_seconds": 3.2,
                "speaker_id": "SPEAKER_00",
            },
            {
                "transcript": "Good morning sir.",
                "start_time_seconds": 4.5,
                "end_time_seconds": 6.0,
                "speaker_id": "SPEAKER_01",
            },
        ]
    },
}


def output(*entries: tuple[float, str]) -> dict[str, object]:
    """A Sarvam output file with one diarized entry per (start_s within the slice, text)."""
    return {
        "transcript": " ".join(text for _, text in entries),
        "language_code": "en-IN",
        "diarized_transcript": {
            "entries": [
                {
                    "transcript": text,
                    "start_time_seconds": start,
                    "end_time_seconds": start + 2,
                    "speaker_id": "SPEAKER_00",
                }
                for start, text in entries
            ]
        },
    }


def make_wav(path: Path, seconds: int) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono",
         "-t", str(seconds), "-c:a", "pcm_s16le", str(path)],
        check=True,
    )  # fmt: skip
    return path


def test_plan_slices_short_audio_is_one_slice() -> None:
    assert transcript.plan_slices(600) == [(0, 600)]
    assert transcript.plan_slices(300) == [(0, 300)]


def test_plan_slices_are_10_min_starting_every_9_45() -> None:
    assert transcript.plan_slices(1300) == [(0, 600), (585, 600), (1170, 130)]


def test_plan_slices_skip_a_tail_already_inside_the_overlap() -> None:
    # 0–600 already covers 595 s; a 585–595 slice would be all overlap.
    assert transcript.plan_slices(595) == [(0, 595)]
    assert transcript.plan_slices(1185) == [(0, 600), (585, 600)]


def test_cut_audio_into_overlapping_slices(tmp_path: Path) -> None:
    src = make_wav(tmp_path / "long.wav", 1300)

    durations: list[float] = []
    for i, (start_s, length_s) in enumerate(transcript.plan_slices(1300)):
        out = tmp_path / f"slice_{i}.wav"
        audio.cut(src, out, start_s, length_s)
        durations.append(audio.probe(out)[0])

    assert durations == [
        pytest.approx(600, abs=1),
        pytest.approx(600, abs=1),
        pytest.approx(130, abs=1),
    ]


def test_merge_splits_the_overlap_at_its_midpoint() -> None:
    # Slice 2 starts at 585 s; both slices hear 585–600 s and the midpoint is 592.5 s.
    first = output((10, "Hello."), (590, "Open your books."), (595, "Page twelve."))
    second = output((5, "Open your books."), (10, "Page twelve."), (100, "Next."))

    merged = transcript.merge([(first, 0), (second, 585)])

    assert [(s.start_s, s.text) for s in merged.segments] == [
        (10, "Hello."),
        (590, "Open your books."),  # starts before 592.5: kept from slice 1
        (595, "Page twelve."),  # starts after 592.5: kept from slice 2
        (685, "Next."),
    ]
    assert merged.language == "en-IN"


def test_transcribe_cuts_queues_and_transcribes_every_slice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wav = make_wav(tmp_path / "audio.wav", 1300)
    # Every slice reports a line 1 s in (inside the overlap with the previous slice, so dropped
    # for slices 2 and 3) and one at 100 s (owned by that slice).
    sent: list[str] = []

    def fake_sarvam(path: Path, out_dir: Path) -> object:
        sent.append(path.name)
        return output((1, f"start of {path.stem}"), (100, f"middle of {path.stem}"))

    monkeypatch.setattr(transcript, "_sarvam_transcribe", fake_sarvam)

    result, raw = transcript.transcribe(wav, 1300, tmp_path, "job1")

    assert sorted(sent) == ["slice_00.wav", "slice_01.wav", "slice_02.wav"]
    assert sorted(raw) == sorted(sent)
    assert [(s.start_s, s.text) for s in result.segments] == [
        (1, "start of slice_00"),
        (100, "middle of slice_00"),
        (685, "middle of slice_01"),  # 586 s is before the 592.5 s midpoint: slice_00's
        (1270, "middle of slice_02"),  # 1171 s is before the 1177.5 s midpoint: slice_01's
    ]


def test_failed_slice_fails_the_transcription(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wav = make_wav(tmp_path / "audio.wav", 1300)

    def fake_sarvam(path: Path, out_dir: Path) -> object:
        if path.name == "slice_01.wav":
            raise AppError("ASR_FAILED", "Sarvam job failed")
        return output((1, "fine"))

    monkeypatch.setattr(transcript, "_sarvam_transcribe", fake_sarvam)

    with pytest.raises(AppError) as exc:
        transcript.transcribe(wav, 1300, tmp_path, "job1")

    assert exc.value.code == "ASR_FAILED"


def test_normalise_uses_diarized_entries() -> None:
    language, segments = transcript.normalise(DIARIZED_OUTPUT, offset_s=0)

    assert language == "en-IN"
    assert [(s.start_s, s.end_s, s.speaker, s.text) for s in segments] == [
        (1.0, 3.2, "SPEAKER_00", "Good morning everyone."),
        (4.5, 6.0, "SPEAKER_01", "Good morning sir."),
    ]


def test_normalise_falls_back_to_timestamps_without_diarization() -> None:
    raw = {k: v for k, v in DIARIZED_OUTPUT.items() if k != "diarized_transcript"}

    _, segments = transcript.normalise(raw, offset_s=0)

    assert [(s.start_s, s.speaker, s.text) for s in segments] == [
        (1.0, None, "Good morning everyone."),
        (4.5, None, "Good morning sir."),
    ]


def test_empty_transcript_raises_asr_failed() -> None:
    raw: dict[str, object] = {
        "request_id": "r",
        "transcript": "",
        "diarized_transcript": {"entries": []},
    }

    with pytest.raises(AppError) as exc:
        transcript.merge([(raw, 0)])

    assert exc.value.code == "ASR_FAILED"


def test_unexpected_output_shape_raises_asr_failed() -> None:
    with pytest.raises(AppError) as exc:
        transcript.normalise({"something": "else"}, offset_s=0)

    assert exc.value.code == "ASR_FAILED"
