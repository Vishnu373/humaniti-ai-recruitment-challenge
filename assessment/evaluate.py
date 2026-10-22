"""Piece 4: the only module that calls Gemini. Builds the prompt and validates the response."""

import logging
import re
from functools import cache
from pathlib import Path
from typing import Final

from google import genai
from google.genai._gaos.lib.compat_errors import (  # pyright: ignore[reportPrivateUsage, reportMissingTypeStubs]
    APIError as InteractionsAPIError,
)
from google.genai._gaos.types.interactions import (
    Interaction,
)
from google.genai._gaos.types.interactions import (
    Usage as InteractionUsage,
)
from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings
from app.errors import AppError
from assessment import checklist
from assessment.dead_air import Facts
from assessment.transcript import Segment

log = logging.getLogger(__name__)

PROMPTS_PATH = Path(__file__).resolve().parent / "prompts.md"
TIMEOUT_S = 5 * 60
ATTEMPTS = 2
NEEDS_VIDEO_CHECK = "Needs video check."
THINKING_LEVEL: Final = "medium"  # "low" produced no thoughts and over-marked items

_MM_SS = r"^\d+:[0-5]\d$"


# What Gemini returns (response_format schema).
# Times stay as the transcript's mm:ss; code converts them.
class _LlmEvidence(BaseModel):
    time: str = Field(pattern=_MM_SS)
    quote: str


class _LlmItem(BaseModel):
    id: str
    found: bool
    evidence: list[_LlmEvidence]
    observation: str


class _LlmFlag(BaseModel):
    start: str = Field(pattern=_MM_SS)
    end: str = Field(pattern=_MM_SS)
    reason: str


class _LlmEvaluation(BaseModel):
    items: list[_LlmItem]
    flagged_segments: list[_LlmFlag]


# What we store in findings/{job_id}.json.
class Evidence(BaseModel):
    start_s: float
    quote: str


class Finding(BaseModel):
    id: str
    found: bool
    evidence: list[Evidence]
    observation: str


class FlaggedSegment(BaseModel):
    start_s: float
    end_s: float
    reason: str


class Usage(BaseModel):
    input_tokens: int
    cached_tokens: int
    thought_tokens: int
    output_tokens: int


def _seconds(mm_ss: str) -> float:
    minutes, seconds = mm_ss.split(":")
    return int(minutes) * 60 + int(seconds)


def mm_ss(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60:02d}:{total % 60:02d}"


def format_transcript(segments: list[Segment]) -> str:
    return "\n".join(f"[{mm_ss(s.start_s)}] {s.speaker or 'UNKNOWN'}: {s.text}" for s in segments)


def format_facts(facts: Facts) -> str:
    first = mm_ss(facts.first_speech_s) if facts.first_speech_s is not None else "no speech"
    gaps = (
        "\n".join(
            f"  - {mm_ss(g.start_s)}–{mm_ss(g.end_s)} ({g.duration_s / 60:.1f} min)"
            for g in facts.dead_air_gaps
        )
        or "  - none"
    )
    return (
        f"- Recording duration: {mm_ss(facts.duration_s)}\n"
        f"- First speech at: {first}\n"
        f"- Dead-air gaps of 3 min or more:\n{gaps}\n"
        f"- Time utilization (share of the recording not in silences of 30 s or more): "
        f"{facts.utilization_pct:.1f}%"
    )


def validate(text: str) -> tuple[list[Finding], list[FlaggedSegment]]:
    """Parse Gemini's JSON and check it covers every checklist item exactly once."""
    try:
        raw = _LlmEvaluation.model_validate_json(text)
    except ValidationError as e:
        raise AppError("LLM_BAD_OUTPUT", f"Response doesn't match the schema: {e}") from e

    items = checklist.load()
    expected = [i.id for i in items]
    got = [i.id for i in raw.items]
    problems: list[str] = []
    if missing := [i for i in expected if i not in got]:
        problems.append(f"missing {missing}")
    if unknown := [i for i in got if i not in expected]:
        problems.append(f"unknown {unknown}")
    if duplicate := sorted({i for i in got if got.count(i) > 1}):
        problems.append(f"duplicate {duplicate}")
    if problems:
        raise AppError("LLM_BAD_OUTPUT", "Checklist items " + "; ".join(problems))

    video_only = {i.id for i in items if i.video_only}
    by_id = {i.id: i for i in raw.items}
    findings: list[Finding] = []
    for item_id in expected:
        llm = by_id[item_id]
        if item_id in video_only:
            # Decided later by the camera check; not found until then (docs/scoring_logic.md).
            findings.append(
                Finding(id=item_id, found=False, evidence=[], observation=NEEDS_VIDEO_CHECK)
            )
            continue
        findings.append(
            Finding(
                id=item_id,
                found=llm.found,
                evidence=[Evidence(start_s=_seconds(e.time), quote=e.quote) for e in llm.evidence],
                observation=llm.observation,
            )
        )
    flags = [
        FlaggedSegment(start_s=_seconds(f.start), end_s=_seconds(f.end), reason=f.reason)
        for f in raw.flagged_segments
    ]
    return findings, flags


@cache
def _prompts() -> dict[str, str]:
    """`## Heading` → body, from prompts.md."""
    sections = re.split(r"^## ", PROMPTS_PATH.read_text(encoding="utf-8"), flags=re.MULTILINE)
    return {
        title.strip(): body.strip() for title, _, body in (s.partition("\n") for s in sections[1:])
    }


def _system_instruction() -> str:
    # Static prefix: identical across calls and jobs so Gemini's implicit cache can reuse it.
    return f"{_prompts()['System']}\n\n# Checklist\n\n{checklist.markdown()}"


def _client() -> genai.Client:
    return genai.Client(api_key=get_settings().gemini_api_key)


def evaluate(
    segments: list[Segment], facts: Facts
) -> tuple[list[Finding], list[FlaggedSegment], Usage]:
    """Gemini #1 via the Interactions API: mark every checklist item. Retries once on bad output."""
    client = _client()
    # Per-job part (facts + transcript) before the per-call instruction, for prefix caching.
    job_part = f"# Facts\n\n{format_facts(facts)}\n\n# Transcript\n\n{format_transcript(segments)}"

    last_error: AppError | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            interaction = client.interactions.create(
                model=get_settings().gemini_model,
                system_instruction=_system_instruction(),
                input=f"{job_part}\n\n{_prompts()['Evaluate']}",
                generation_config={"thinking_level": THINKING_LEVEL},
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": _LlmEvaluation.model_json_schema(),
                },
                stream=False,
                timeout=TIMEOUT_S,
            )
        except InteractionsAPIError as e:
            raise AppError("LLM_FAILED", f"Gemini API error {e.status_code}: {e.message}") from e

        if not isinstance(interaction, Interaction):  # stream=False, so never a Stream
            raise AppError("LLM_FAILED", f"Unexpected Gemini response {type(interaction)}")
        if interaction.status != "completed":
            raise AppError("LLM_FAILED", f"Gemini interaction ended {interaction.status}")
        usage = _usage(interaction.usage)
        log.info("Gemini attempt %d: %s", attempt, usage.model_dump())
        try:
            findings, flags = validate(interaction.output_text or "")
        except AppError as e:
            log.warning("Gemini attempt %d: %s", attempt, e.message)
            last_error = e
            continue
        return findings, flags, usage

    assert last_error is not None
    raise last_error


def _usage(usage: InteractionUsage | None) -> Usage:
    if usage is None:
        return Usage(input_tokens=0, cached_tokens=0, thought_tokens=0, output_tokens=0)
    return Usage(
        input_tokens=usage.total_input_tokens or 0,
        cached_tokens=usage.total_cached_tokens or 0,
        thought_tokens=usage.total_thought_tokens or 0,
        output_tokens=usage.total_output_tokens or 0,
    )
