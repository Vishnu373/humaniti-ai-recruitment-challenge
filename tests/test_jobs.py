import threading
from collections.abc import Iterator

import pytest

from storage import jobs, r2
from storage.jobs import Job, JobStatus

JOB_ID = "b" * 32


@pytest.fixture(autouse=True)
def fake_r2(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Record R2 reads instead of making them; writes are dropped."""
    reads: list[str] = []
    stored = Job(
        job_id=JOB_ID,
        source="upload",
        video_key=None,
        size_bytes=None,
        status="TRANSCRIBING",
        created_at="2026-10-22T00:00:00+00:00",
    ).model_dump(mode="json")

    def get_json(key: str) -> object:
        reads.append(key)
        return stored

    def put_json(key: str, data: object) -> None:
        pass

    monkeypatch.setattr(r2, "put_json", put_json)
    monkeypatch.setattr(r2, "get_json", get_json)
    monkeypatch.setattr(jobs, "_latest", {})
    yield reads


def advance(job: Job, statuses: list[JobStatus]) -> threading.Thread:
    """Save each status from another thread, as the pipeline does."""

    def run() -> None:
        for status in statuses:
            job.status = status
            jobs.save_job(job)

    return threading.Thread(target=run)


def test_watch_yields_each_pushed_status_until_complete(fake_r2: list[str]) -> None:
    job = jobs.create_job(JOB_ID, "upload", "UPLOADING")
    seen: list[JobStatus] = []
    watcher = jobs.watch(JOB_ID)
    seen.append(next(watcher).status)

    # One status per wait: the watcher picks each up before the next is saved.
    for status in ("EXTRACTING", "TRANSCRIBING", "EVALUATING", "SCORE_APPLYING", "COMPLETE"):
        t = advance(job, [status])
        t.start()
        seen.append(next(watcher).status)
        t.join()

    assert seen == [
        "UPLOADING",
        "EXTRACTING",
        "TRANSCRIBING",
        "EVALUATING",
        "SCORE_APPLYING",
        "COMPLETE",
    ]
    assert list(watcher) == []  # stream ends at COMPLETE
    assert fake_r2 == []  # never read R2


def test_watch_ends_on_failed() -> None:
    job = jobs.create_job(JOB_ID, "drive", "UPLOADING")
    watcher = jobs.watch(JOB_ID)
    next(watcher)
    t = advance(job, ["FAILED"])
    t.start()
    t.join()

    assert [j.status for j in watcher] == ["FAILED"]


def test_watch_repeats_the_job_on_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "HEARTBEAT_S", 0.01)
    jobs.create_job(JOB_ID, "upload", "EVALUATING")
    watcher = jobs.watch(JOB_ID)

    assert [next(watcher).status, next(watcher).status] == ["EVALUATING", "EVALUATING"]


def test_watch_unknown_job_reads_r2_once_and_stops(fake_r2: list[str]) -> None:
    assert [j.status for j in jobs.watch(JOB_ID)] == ["TRANSCRIBING"]
    assert fake_r2 == [f"jobs/{JOB_ID}.json"]
