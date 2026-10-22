"""Facts computed from segment timestamps: first speech, dead-air gaps and time utilization."""

from pydantic import BaseModel, computed_field

from assessment.transcript import Segment

DEAD_AIR_MIN_S = 180  # avg02: 3+ minutes of silence
UNUSED_GAP_MIN_S = 30  # gd04: silences this long count as unused time


class Gap(BaseModel):
    start_s: float
    end_s: float

    @computed_field
    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


class Facts(BaseModel):
    duration_s: float
    first_speech_s: float | None
    dead_air_gaps: list[Gap]
    utilization_pct: float


def _gaps(segments: list[Segment], min_s: float) -> list[Gap]:
    """Silences of at least `min_s` between segments. Overlapping segments never make a gap."""
    gaps: list[Gap] = []
    ordered = sorted(segments, key=lambda s: s.start_s)
    for prev_end, seg in zip(_running_max_end(ordered), ordered[1:], strict=False):
        if seg.start_s - prev_end >= min_s:
            gaps.append(Gap(start_s=prev_end, end_s=seg.start_s))
    return gaps


def _running_max_end(ordered: list[Segment]) -> list[float]:
    ends: list[float] = []
    for seg in ordered:
        ends.append(max(seg.end_s, ends[-1]) if ends else seg.end_s)
    return ends


def compute_facts(segments: list[Segment], duration_s: float) -> Facts:
    if not segments:
        return Facts(
            duration_s=duration_s, first_speech_s=None, dead_air_gaps=[], utilization_pct=0
        )

    first_s = min(s.start_s for s in segments)
    last_s = max(s.end_s for s in segments)
    edges = [first_s, duration_s - last_s]
    unused_s = sum(e for e in edges if e >= UNUSED_GAP_MIN_S) + sum(
        g.duration_s for g in _gaps(segments, UNUSED_GAP_MIN_S)
    )
    return Facts(
        duration_s=duration_s,
        first_speech_s=first_s,
        dead_air_gaps=_gaps(segments, DEAD_AIR_MIN_S),
        utilization_pct=max(0.0, (duration_s - unused_s) / duration_s * 100),
    )
