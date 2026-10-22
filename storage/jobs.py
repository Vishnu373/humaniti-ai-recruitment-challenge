"""Job record stored in R2 at jobs/{job_id}.json, the background pipeline runner, and an
in-process notifier that pushes each status change to watchers (no R2 polling)."""

import logging
import tempfile
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.config import get_settings
from app.errors import AppError, ErrorEnvelope
from assessment import audio, dead_air, evaluate, evidence, scoring, transcript
from storage import r2

log = logging.getLogger(__name__)

JobStatus = Literal[
    "UPLOADING",
    "EXTRACTING",
    "TRANSCRIBING",
    "EVALUATING",
    "SCORE_APPLYING",
    "COMPLETE",
    "FAILED",
]
FINAL_STATUSES: frozenset[JobStatus] = frozenset({"COMPLETE", "FAILED"})

# A watcher with no news re-yields the job this often, so a closed page stops its stream.
HEARTBEAT_S = 30.0


class Job(BaseModel):
    job_id: str
    source: Literal["upload", "drive"]
    video_key: str | None
    size_bytes: int | None
    status: JobStatus
    error: ErrorEnvelope | None = None
    created_at: str
    duration_s: float | None = None
    transcript_key: str | None = None
    findings_key: str | None = None
    score: scoring.Score | None = None


class Report(BaseModel):
    """The parts of findings/{job_id}.json the page shows; rejected quotes and usage stay hidden."""

    items: list[evaluate.Finding]
    flagged_segments: list[evaluate.FlaggedSegment]
    facts: dead_air.Facts


def load_report(job: Job) -> Report:
    if job.findings_key is None:
        raise AppError("STORAGE_FAILED", "Job has no findings", job.job_id)
    data = r2.get_json(job.findings_key)
    if data is None:
        raise AppError("STORAGE_FAILED", f"{job.findings_key} is missing", job.job_id)
    return Report.model_validate(data)


def _key(job_id: str) -> str:
    return f"jobs/{job_id}.json"


def new_job_id() -> str:
    return uuid.uuid4().hex


def create_job(
    job_id: str,
    source: Literal["upload", "drive"],
    status: JobStatus,
    video_key: str | None = None,
    size_bytes: int | None = None,
) -> Job:
    job = Job(
        job_id=job_id,
        source=source,
        video_key=video_key,
        size_bytes=size_bytes,
        status=status,
        created_at=datetime.now(UTC).isoformat(),
    )
    save_job(job)
    return job


# Latest saved copy of every job this process has touched; save_job notifies watchers.
_changed = threading.Condition()
_latest: dict[str, Job] = {}


def save_job(job: Job) -> None:
    r2.put_json(_key(job.job_id), job.model_dump(mode="json"))
    with _changed:
        _latest[job.job_id] = job.model_copy(deep=True)
        _changed.notify_all()


def watch(job_id: str) -> Iterator[Job]:
    """Yield the job now, then on every status change until it is COMPLETE or FAILED.

    Reads R2 only when this process has never saved the job (e.g. after a restart). No
    pipeline is running for such a job here, so it is yielded once and the watch ends.
    Repeats the current job every HEARTBEAT_S while nothing changes.
    """
    with _changed:
        job = _latest.get(job_id)
    if job is None:
        yield load_job(job_id)
        return
    yield job
    while job.status not in FINAL_STATUSES:
        with _changed:
            _changed.wait_for(
                lambda status=job.status: _latest[job_id].status != status, timeout=HEARTBEAT_S
            )
            job = _latest[job_id]
        yield job


def load_job(job_id: str) -> Job:
    # Job IDs are uuid4 hex; anything else can't be ours and must not reach the R2 key.
    if len(job_id) != 32 or not all(c in "0123456789abcdef" for c in job_id):
        raise AppError("JOB_NOT_FOUND", f"No job {job_id!r}", job_id)
    data = r2.get_json(_key(job_id))
    if data is None:
        raise AppError("JOB_NOT_FOUND", f"No job {job_id!r}", job_id)
    return Job.model_validate(data)


def fail_job(job: Job, error: AppError) -> None:
    error.job_id = job.job_id
    job.status = "FAILED"
    job.error = error.envelope()
    save_job(job)


def run_pipeline(job_id: str) -> None:
    """Background task: process an uploaded job. Any failure is recorded on the job."""
    job = load_job(job_id)
    if job.status != "EXTRACTING":
        # A failed Drive download, or a job already further along.
        log.info("Job %s: not starting pipeline, status is %s", job_id, job.status)
        return
    try:
        _transcribe(job)
        _evaluate(job)
    except AppError as e:
        fail_job(job, e)
    except Exception:
        # Unexpected: record it so the job doesn't sit mid-pipeline forever, then re-raise.
        fail_job(job, AppError("INTERNAL_ERROR", f"Unexpected error while {job.status}"))
        raise


def _transcribe(job: Job) -> None:
    """Pieces 2–3: videos/{job_id}.{ext} → transcripts/{job_id}.json (+ the raw Sarvam output).

    The video is downloaded once; slices are cut from it straight to 16 kHz WAV.
    """
    if job.video_key is None:
        raise AppError("MEDIA_FAILED", "Job has no video", job.job_id)

    with tempfile.TemporaryDirectory() as tmp:
        video = Path(tmp) / Path(job.video_key).name
        r2.download_file(job.video_key, video)
        job.duration_s = audio.check_audio(video)
        job.status = "TRANSCRIBING"
        save_job(job)
        log.info("Job %s: video ready (%.0f s), transcribing", job.job_id, job.duration_s)
        result, raw = transcript.transcribe(video, job.duration_s, Path(tmp), job.job_id)

    r2.put_json(f"transcripts/{job.job_id}.raw.json", raw)
    transcript_key = f"transcripts/{job.job_id}.json"
    r2.put_json(transcript_key, result.model_dump(mode="json"))

    # Saved with the EVALUATING status at the start of the next step.
    job.transcript_key = transcript_key
    log.info("Job %s: %d segments at %s", job.job_id, len(result.segments), transcript_key)


def _evaluate(job: Job) -> None:
    """Pieces 4–5: transcript → findings/{job_id}.json (items, quotes verified) and the tier."""
    if job.transcript_key is None or job.duration_s is None:
        raise AppError("LLM_FAILED", "Job has no transcript", job.job_id)
    job.status = "EVALUATING"
    save_job(job)

    data = r2.get_json(job.transcript_key)
    if data is None:
        raise AppError("STORAGE_FAILED", f"{job.transcript_key} is missing", job.job_id)
    segments = transcript.Transcript.model_validate(data).segments
    facts = dead_air.compute_facts(segments, job.duration_s)
    findings, flags, usage = evaluate.evaluate(segments, facts)
    findings, rejected = evidence.check(findings, segments, facts)
    job.status = "SCORE_APPLYING"
    save_job(job)
    score = scoring.score({f.id for f in findings if f.found}, job.duration_s)

    findings_key = f"findings/{job.job_id}.json"
    r2.put_json(
        findings_key,
        {
            "items": [f.model_dump(mode="json") for f in findings],
            "flagged_segments": [f.model_dump(mode="json") for f in flags],
            "rejected_evidence": [r.model_dump(mode="json") for r in rejected],
            "facts": facts.model_dump(mode="json"),
            "model": get_settings().gemini_model,
            "usage": usage.model_dump(mode="json"),
            "score": score.model_dump(mode="json"),
        },
    )

    job.findings_key = findings_key
    job.score = score
    job.status = "COMPLETE"
    save_job(job)
    found = sum(f.found for f in findings)
    log.info(
        "Job %s: %d items found, %d quotes rejected, tier %s (%s)",
        job.job_id,
        found,
        len(rejected),
        score.tier,
        score.reason,
    )
