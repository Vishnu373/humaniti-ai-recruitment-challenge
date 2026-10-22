"""Run saved transcripts through evaluation + scoring, without the pipeline or R2.

Usage: .venv/bin/python -m scripts.evaluate_transcript test_transcripts/transcript_03.json ...
Writes <name>.findings.json next to each input.
"""

import json
import sys
from pathlib import Path

from assessment import dead_air, evaluate, evidence, scoring, transcript


def _mm_ss(seconds: float) -> str:
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


def run(path: Path) -> None:
    segments = transcript.Transcript.model_validate_json(path.read_text("utf-8")).segments
    # No video here, so the last speech end stands in for the recording length.
    duration_s = max(s.end_s for s in segments)
    facts = dead_air.compute_facts(segments, duration_s)
    findings, flags, usage = evaluate.evaluate(segments, facts)
    findings, rejected = evidence.check(findings, segments, facts)
    score = scoring.score({f.id for f in findings if f.found}, duration_s)

    out = path.with_name(f"{path.stem}.findings.json")
    out.write_text(
        json.dumps(
            {
                "items": [f.model_dump(mode="json") for f in findings],
                "flagged_segments": [f.model_dump(mode="json") for f in flags],
                "rejected_evidence": [r.model_dump(mode="json") for r in rejected],
                "facts": facts.model_dump(mode="json"),
                "usage": usage.model_dump(mode="json"),
                "score": score.model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=1,
        ),
        "utf-8",
    )

    print(f"\n=== {path.name}: {len(segments)} lines, {_mm_ss(duration_s)} ===")
    print(
        f"first speech {_mm_ss(facts.first_speech_s or 0)}, gaps {len(facts.dead_air_gaps)}, "
        f"utilization {facts.utilization_pct:.1f}%"
    )
    print(f"tokens {usage.model_dump()}")
    for f in findings:
        if f.found:
            print(f"\n  {f.id} FOUND  {f.observation}")
            for e in f.evidence:
                print(f"      [{_mm_ss(e.start_s)}] {e.quote}")
    print(f"\n  not found: {', '.join(f.id for f in findings if not f.found)}")
    for r in rejected:
        print(f"  rejected quote: {r.model_dump()}")
    grade = f"On Track for {score.tier}" if score.provisional else score.tier
    print(f"\n  GRADE: {grade.upper()}  ({score.reason})")
    print(f"  saved {out}")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        run(Path(arg))
