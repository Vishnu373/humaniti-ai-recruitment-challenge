import pytest

from assessment import dead_air
from assessment.transcript import Segment


def seg(start_s: float, end_s: float) -> Segment:
    return Segment(start_s=start_s, end_s=end_s, speaker="SPEAKER_00", text="…")


def test_finds_four_minute_gap_and_ignores_two_minute_one() -> None:
    segments = [seg(0, 60), seg(180, 240), seg(480, 540)]  # 2-min gap, then 4-min gap

    facts = dead_air.compute_facts(segments, duration_s=540)

    assert [(g.start_s, g.end_s) for g in facts.dead_air_gaps] == [(240, 480)]


def test_overlapping_segments_do_not_create_false_gaps() -> None:
    segments = [seg(0, 400), seg(10, 20), seg(390, 500)]

    facts = dead_air.compute_facts(segments, duration_s=500)

    assert facts.dead_air_gaps == []


def test_utilization_counts_gaps_of_30s_or_more_including_edges() -> None:
    # 60-min class: 60 s silent before the first line, 60 s mid-class gap, 60 s after the last line.
    # A 20 s gap is too short to count.
    segments = [seg(60, 1000), seg(1020, 2000), seg(2060, 3540)]

    facts = dead_air.compute_facts(segments, duration_s=3600)

    assert facts.utilization_pct == pytest.approx(95.0)
    assert facts.first_speech_s == 60


def test_empty_transcript_is_zero_utilization() -> None:
    facts = dead_air.compute_facts([], duration_s=600)

    assert facts.utilization_pct == 0
    assert facts.first_speech_s is None
