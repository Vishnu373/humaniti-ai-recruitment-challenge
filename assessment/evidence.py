"""Check that every quote Gemini cites is really in the transcript near its timestamp.

Items judged from the computed facts (gd04, avg02) are checked against those facts instead.
"""

import unicodedata
from collections.abc import Callable

from pydantic import BaseModel

from assessment.dead_air import Facts
from assessment.evaluate import Finding
from assessment.transcript import Segment

WINDOW_S = 30
GD04_MIN_UTILIZATION_PCT = 95
FILLER_WORDS = frozenset({"uh", "uhh", "um", "umm", "ah", "er", "hmm"})

# Items whose found/not found rests on the facts: found only if the gate passes, and then
# found even without a surviving transcript quote (Gemini may cite the facts themselves).
FACT_GATES: dict[str, Callable[[Facts], bool]] = {
    "gd04": lambda f: f.utilization_pct >= GD04_MIN_UTILIZATION_PCT,
    "avg02": lambda f: bool(f.dead_air_gaps),
}


class RejectedEvidence(BaseModel):
    id: str
    start_s: float
    quote: str


def _normalise(text: str) -> str:
    """Case-fold, drop punctuation and filler words, collapse whitespace."""
    no_punct = "".join(
        " " if unicodedata.category(c).startswith("P") else c for c in text.casefold()
    )
    return " ".join(w for w in no_punct.split() if w not in FILLER_WORDS)


def quote_found(quote: str, start_s: float, segments: list[Segment]) -> bool:
    """True if the quote appears in the text of segments within ±WINDOW_S of start_s."""
    nearby = " ".join(
        s.text
        for s in segments
        if s.start_s <= start_s + WINDOW_S and s.end_s >= start_s - WINDOW_S
    )
    needle = _normalise(quote)
    return bool(needle) and needle in _normalise(nearby)


def check(
    findings: list[Finding], segments: list[Segment], facts: Facts
) -> tuple[list[Finding], list[RejectedEvidence]]:
    """Drop quotes not in the transcript; a found item left with no quotes becomes not found.

    Fact-gated items are found only if Gemini found them and their facts gate passes.
    """
    checked: list[Finding] = []
    rejected: list[RejectedEvidence] = []
    for f in findings:
        kept = [e for e in f.evidence if quote_found(e.quote, e.start_s, segments)]
        rejected += [
            RejectedEvidence(id=f.id, start_s=e.start_s, quote=e.quote)
            for e in f.evidence
            if e not in kept
        ]
        gate = FACT_GATES.get(f.id)
        found = f.found and (gate(facts) if gate is not None else bool(kept))
        checked.append(f.model_copy(update={"evidence": kept, "found": found}))
    return checked, rejected
