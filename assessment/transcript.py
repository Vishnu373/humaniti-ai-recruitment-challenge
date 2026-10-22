"""Piece 3: speech-to-text as a producer-consumer pipeline, and normalisation to segments.

ffmpeg (the producer) cuts 10-minute 16 kHz WAV slices that overlap by 15 s straight from the
video (no full-length WAV) and puts each on a queue as soon as it's cut; CONSUMERS threads take
slices off the queue and run one Sarvam Batch job each.
"""

import json
import logging
import math
import queue
import threading
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import BaseModel, ValidationError
from sarvamai import SarvamAI
from sarvamai.core.api_error import ApiError

from app.config import get_settings
from app.errors import AppError
from assessment import audio

log = logging.getLogger(__name__)

SLICE_S = 600  # 10 min per slice: 9 min 45 s of its own + 15 s shared with the next
OVERLAP_S = 15
STEP_S = SLICE_S - OVERLAP_S
CONSUMERS = 3  # Sarvam jobs running at once
JOB_TIMEOUT_S = 30 * 60
UPLOAD_TIMEOUT_S = 10 * 60
POLL_INTERVAL_S = 10


class Segment(BaseModel):
    start_s: float
    end_s: float
    speaker: str | None
    text: str


class Transcript(BaseModel):
    language: str | None
    segments: list[Segment]


# Sarvam Batch output file (one per input file). Unknown fields are ignored.
class _Entry(BaseModel):
    transcript: str
    start_time_seconds: float
    end_time_seconds: float
    speaker_id: str


class _Diarized(BaseModel):
    entries: list[_Entry]


class _Timestamps(BaseModel):
    words: list[str]
    start_time_seconds: list[float]
    end_time_seconds: list[float]


class _Output(BaseModel):
    transcript: str
    language_code: str | None = None
    timestamps: _Timestamps | None = None
    diarized_transcript: _Diarized | None = None


def plan_slices(duration_s: float) -> list[tuple[float, float]]:
    """(offset_s, length_s) for each slice: SLICE_S long, starting every STEP_S seconds.

    Stops once a slice reaches the end, so no slice lies wholly inside the previous overlap.
    """
    slices: list[tuple[float, float]] = []
    offset_s = 0.0
    while True:
        slices.append((offset_s, min(SLICE_S, duration_s - offset_s)))
        if offset_s + SLICE_S >= duration_s:
            return slices
        offset_s += STEP_S


def normalise(raw: object, offset_s: float) -> tuple[str | None, list[Segment]]:
    """Turn one Sarvam output file into segments, shifted by the slice's offset."""
    try:
        out = _Output.model_validate(raw)
    except ValidationError as e:
        raise AppError("ASR_FAILED", f"Unexpected Sarvam output format: {e}") from e

    if out.diarized_transcript is not None and out.diarized_transcript.entries:
        segments = [
            Segment(
                start_s=e.start_time_seconds + offset_s,
                end_s=e.end_time_seconds + offset_s,
                speaker=e.speaker_id,
                text=e.transcript.strip(),
            )
            for e in out.diarized_transcript.entries
        ]
    elif out.timestamps is not None:
        ts = out.timestamps
        segments = [
            Segment(start_s=start + offset_s, end_s=end + offset_s, speaker=None, text=text.strip())
            for text, start, end in zip(
                ts.words, ts.start_time_seconds, ts.end_time_seconds, strict=True
            )
        ]
    else:
        segments = []
    return out.language_code, [s for s in segments if s.text]


def merge(outputs: list[tuple[object, float]]) -> Transcript:
    """Normalise each (raw output, offset_s) in slice order and concatenate.

    Neighbouring slices both hear their shared OVERLAP_S seconds; the midpoint of that overlap
    is the cut, so each line is kept once, from the slice that heard it whole.
    """
    cuts = [offset_s + OVERLAP_S / 2 for _, offset_s in outputs[1:]]
    language: str | None = None
    segments: list[Segment] = []
    for i, (raw, offset_s) in enumerate(outputs):
        start = cuts[i - 1] if i > 0 else -math.inf
        end = cuts[i] if i < len(cuts) else math.inf
        slice_language, slice_segments = normalise(raw, offset_s)
        language = language or slice_language
        segments.extend(s for s in slice_segments if start <= s.start_s < end)
    if not segments:
        raise AppError("ASR_FAILED", "Sarvam returned an empty transcript")
    return Transcript(language=language, segments=segments)


@dataclass(frozen=True)
class _Slice:
    index: int
    path: Path
    offset_s: float


def transcribe(
    media: Path, duration_s: float, work_dir: Path, job_id: str
) -> tuple[Transcript, dict[str, object]]:
    """Cut slices into a queue while CONSUMERS threads transcribe them; return (transcript, raw
    Sarvam outputs by slice file). The first failure stops the rest and is raised here."""
    planned = plan_slices(duration_s)
    total = len(planned)
    # Bounded so ffmpeg runs at most one slice per consumer ahead of Sarvam.
    pending: queue.Queue[_Slice | None] = queue.Queue(maxsize=CONSUMERS)
    outputs: dict[int, object] = {}
    errors: list[Exception] = []
    stop = threading.Event()

    def consume() -> None:
        while (item := pending.get()) is not None:
            n = item.index + 1
            if stop.is_set():
                log.info("Job %s: slice %d/%d skipped, an earlier slice failed", job_id, n, total)
                continue
            log.info("Job %s: slice %d/%d sent to Sarvam", job_id, n, total)
            try:
                outputs[item.index] = _sarvam_transcribe(
                    item.path, work_dir / f"sarvam_{item.index:02d}"
                )
            except Exception as e:
                # Re-raised by transcribe() once every thread has stopped.
                log.error("Job %s: slice %d/%d failed: %s", job_id, n, total, e)
                errors.append(e)
                stop.set()
                continue
            log.info("Job %s: slice %d/%d transcribed", job_id, n, total)

    consumers = [
        threading.Thread(target=consume, name=f"sarvam-{job_id[:8]}-{c}")
        for c in range(min(CONSUMERS, total))
    ]
    for t in consumers:
        t.start()
    try:
        for i, (offset_s, length_s) in enumerate(planned):
            if stop.is_set():
                break
            path = work_dir / f"slice_{i:02d}.wav"
            log.info("Job %s: slice %d/%d cutting (%.0f–%.0f s)", job_id, i + 1, total,
                     offset_s, offset_s + length_s)  # fmt: skip
            audio.cut(media, path, offset_s, length_s)
            pending.put(_Slice(i, path, offset_s))
            log.info("Job %s: slice %d/%d queued", job_id, i + 1, total)
    except Exception:
        stop.set()
        raise
    finally:
        for _ in consumers:
            pending.put(None)
        for t in consumers:
            t.join()
    if errors:
        raise errors[0]

    names = [f"slice_{i:02d}.wav" for i in range(total)]
    raw = {names[i]: outputs[i] for i in range(total)}
    return merge([(outputs[i], offset_s) for i, (offset_s, _) in enumerate(planned)]), raw


def _sarvam_transcribe(wav: Path, out_dir: Path) -> object:
    """Run one Sarvam Batch job for one file and return its parsed output."""
    client = SarvamAI(api_subscription_key=get_settings().sarvam_api_key)
    try:
        job = client.speech_to_text_job.create_job(
            model="saaras:v4",
            mode="transcribe",
            language_code="unknown",
            with_diarization=True,
            with_timestamps=True,
        )
        log.info("Sarvam job %s: uploading %s", job.job_id, wav.name)
        job.upload_files([str(wav)], timeout=UPLOAD_TIMEOUT_S)
        job.start()
        status = job.wait_until_complete(poll_interval=POLL_INTERVAL_S, timeout=JOB_TIMEOUT_S)
        if status.job_state.lower() != "completed":
            raise AppError("ASR_FAILED", f"Sarvam job {job.job_id} failed: {status.error_message}")
        failed = job.get_file_results()["failed"]
        if failed:
            reasons = "; ".join(f"{f['file_name']}: {f['error_message']}" for f in failed)
            raise AppError("ASR_FAILED", f"Sarvam job {job.job_id} failed for {reasons}")
        job.download_outputs(str(out_dir))
    except ApiError as e:
        raise AppError("ASR_FAILED", f"Sarvam API error {e.status_code}: {e.body}") from e
    except TimeoutError as e:
        raise AppError("ASR_FAILED", f"Sarvam job timed out after {JOB_TIMEOUT_S} s") from e
    # The SDK raises RuntimeError for a failed upload/download and httpx errors on the network.
    except (RuntimeError, httpx.HTTPError) as e:
        raise AppError("ASR_FAILED", f"Sarvam transfer failed: {e}") from e

    result = out_dir / f"{wav.name}.json"
    if not result.exists():
        raise AppError("ASR_FAILED", f"Sarvam returned no output for {wav.name}")
    return json.loads(result.read_text(encoding="utf-8"))
