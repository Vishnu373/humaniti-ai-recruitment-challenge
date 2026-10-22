from collections.abc import Iterator
from typing import get_args

import pytest

from app import ui
from app.errors import AppError
from assessment import checklist
from storage.jobs import Job, JobStatus, Report


def job(status: JobStatus, **fields: object) -> Job:
    return Job.model_validate(
        {
            "job_id": "a" * 32,
            "source": "upload",
            "video_key": None,
            "size_bytes": None,
            "status": status,
            "created_at": "2026-10-22T00:00:00+00:00",
            **fields,
        }
    )


def test_every_status_except_failed_has_a_step() -> None:
    assert set(ui.STEPS) == set(get_args(JobStatus)) - {"FAILED"}


def test_progress_bar_follows_the_pipeline() -> None:
    html = ui.render_status(job("TRANSCRIBING"))

    assert '<progress value="2" max="5"' in html
    assert "Transcribing" in html


def test_complete_job_shows_full_bar() -> None:
    html = ui.render_status(job("COMPLETE"))

    assert '<progress value="5" max="5"' in html
    assert "Done" in html


def test_failed_job_shows_escaped_error_without_bar() -> None:
    error = AppError("ASR_FAILED", "Sarvam said <b>no</b>", "a" * 32).envelope()

    html = ui.render_status(job("FAILED", error=error))

    assert "<progress" not in html
    assert "ASR_FAILED" in html
    assert "&lt;b&gt;no&lt;/b&gt;" in html


def test_complete_job_shows_the_grade_and_reason() -> None:
    score = {"tier": "average", "provisional": False, "reason": "Average item found: avg01"}

    html = ui.render_status(job("COMPLETE", score=score))

    assert "Grade: <b>Average</b>" in html
    assert "Average item found: avg01" in html


def test_short_class_grade_is_on_track() -> None:
    score = {"tier": "good", "provisional": True, "reason": "2 Good items found: gd01, gd02"}

    html = ui.render_status(job("COMPLETE", score=score))

    assert "Grade: <b>On Track for Good</b>" in html


def report(**fields: object) -> Report:
    """A report covering every checklist item: exe03 and avg02 found, the rest not found."""
    items: list[dict[str, object]] = [
        {"id": item.id, "found": False, "evidence": [], "observation": f"No sign of {item.id}."}
        for item in checklist.load()
    ]
    found = {
        "exe03": ([{"start_s": 725, "quote": "why do you think it doubles?"}], "Asks why."),
        "avg02": ([{"start_s": 1830, "quote": "ok, do it <yourselves>"}], "4-min silence."),
    }
    for entry in items:
        if entry["id"] in found:
            entry["evidence"], entry["observation"] = found[str(entry["id"])]
            entry["found"] = True
    return Report.model_validate(
        {
            "items": items,
            "flagged_segments": [{"start_s": 2210, "end_s": 2218, "reason": "'kill' misheard?"}],
            "facts": {
                "duration_s": 2668,
                "first_speech_s": 40,
                "dead_air_gaps": [{"start_s": 1830, "end_s": 2080}],
                "utilization_pct": 88.4,
            },
            **fields,
        }
    )


def test_report_lists_every_item_grouped_by_tier_in_checklist_order() -> None:
    html = ui.render_report(report())

    positions = [html.index(f"<code>{item.id}</code>") for item in checklist.load()]
    assert positions == sorted(positions)
    headings = [html.index(f"<h3>{tier.capitalize()}</h3>") for tier in checklist.TIERS]
    assert headings == sorted(headings)


def test_report_shows_found_items_with_timestamped_escaped_quotes() -> None:
    html = ui.render_report(report())

    assert "✓ <code>exe03</code>" in html
    assert "[12:05] “why do you think it doubles?”" in html
    assert "[30:30] “ok, do it &lt;yourselves&gt;”" in html
    assert "✗ <code>exe01</code>" in html
    assert "No sign of exe01." in html


def test_report_shows_class_facts_and_flagged_lines() -> None:
    html = ui.render_report(report())

    assert "44:28" in html  # duration
    assert "00:40" in html  # first speech
    assert "88.4%" in html
    assert "30:30–34:40" in html  # dead-air gap
    assert "36:50–36:58" in html
    assert "&#x27;kill&#x27; misheard?" in html


def test_report_says_when_nothing_was_flagged() -> None:
    html = ui.render_report(report(flagged_segments=[]))

    assert "No lines flagged" in html


def test_report_does_not_show_rejected_quotes() -> None:
    rejected = [{"id": "exe01", "start_s": 5, "quote": "a made-up quote"}]

    html = ui.render_report(report(rejected_evidence=rejected))

    assert "a made-up quote" not in html


def test_complete_job_streams_status_then_report(monkeypatch: pytest.MonkeyPatch) -> None:
    score = {"tier": "average", "provisional": False, "reason": "Average item found: avg02"}
    done = job("COMPLETE", score=score, findings_key="findings/x.json")

    def watch(job_id: str) -> Iterator[Job]:
        yield from [job("EVALUATING"), done]

    def load_report(j: Job) -> Report:
        return report()

    monkeypatch.setattr(ui.jobs, "watch", watch)
    monkeypatch.setattr(ui.jobs, "load_report", load_report)

    renders = list(ui.watch_status("a" * 32))

    assert "Evaluating" in renders[0] and "<h3>Excellent</h3>" not in renders[0]
    assert "Grade: <b>Average</b>" in renders[1] and "<h3>Excellent</h3>" in renders[1]
