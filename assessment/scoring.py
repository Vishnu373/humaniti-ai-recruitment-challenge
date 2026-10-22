"""Piece 5: found items → tier, by the ladder in docs/scoring_logic.md. Plain rules, no LLM."""

from pydantic import BaseModel

from assessment import checklist
from assessment.checklist import Tier

RED_FLAGS = ("pr01", "pr02", "pr07")
EXCELLENT_REQUIRED = ("exe01", "exe03")
POOR_MIN = 2
GOOD_MIN = 2
SHORT_CLASS_S = 5 * 60


class Score(BaseModel):
    tier: Tier
    provisional: bool  # under 5 minutes: shown as "On Track for <tier>"
    reason: str


def score(found: set[str], duration_s: float) -> Score:
    items = checklist.load()
    unknown = found - {i.id for i in items}
    if unknown:
        raise ValueError(f"Found items not in the checklist: {sorted(unknown)}")

    def found_ids(tier: Tier, main_only: bool = False) -> list[str]:
        # Checklist order, so the reason reads the same on every run.
        return [
            i.id for i in items if i.tier == tier and i.id in found and (i.main or not main_only)
        ]

    def result(tier: Tier, reason: str) -> Score:
        return Score(tier=tier, provisional=duration_s < SHORT_CLASS_S, reason=reason)

    red = [i for i in RED_FLAGS if i in found]
    poor = found_ids("poor")
    average = found_ids("average", main_only=True)
    good = found_ids("good", main_only=True)
    if red:
        return result("poor", f"Red flag found: {', '.join(red)}")
    if len(poor) >= POOR_MIN:
        return result("poor", f"{len(poor)} Poor items found: {', '.join(poor)}")
    if average:
        return result("average", f"Average item found: {', '.join(average)}")
    if poor:
        return result("average", f"Poor item {poor[0]} caps the grade at Average")
    if all(i in found for i in EXCELLENT_REQUIRED):
        return result("excellent", f"{' and '.join(EXCELLENT_REQUIRED)} found")
    if len(good) >= GOOD_MIN:
        return result("good", f"{len(good)} Good items found: {', '.join(good)}")
    return result("average", "Neither the Excellent nor the Good threshold is met")
