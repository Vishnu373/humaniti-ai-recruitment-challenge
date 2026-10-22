## Scoring Logic

The LLM marks each checklist item as **found** or **not found**. This deterministic Python code (`assessment/scoring.py`) then decides the tier. It follows the "Primary Threshold" column and the red flags of `class_quality_checklist.md` (v2.0).

### The ladder
Check the rules in order; the first one that matches decides the tier.

| # | Rule | Tier |
|---|---|---|
| 1 | Any red flag found: `pr01`, `pr02` or `pr07` | **Poor** |
| 2 | 2 or more Poor items found (any of `pr01–07`) | **Poor** |
| 3 | Any Average primary item found (`avg01–04`) | **Average** |
| 4 | Exactly 1 Poor item found: it caps the grade | **Average** |
| 5 | `exe01` (all 4 stages) and `exe03` (Socratic questioning) both found | **Excellent** |
| 6 | 2 or more of the Good primary items found (`gd01–03`) | **Good** |
| 7 | None of the above: no flaws, but no clear strengths | **Average** |

- The other Excellent items (`exe02`, `exe04–07`), `gd04` and `avg05` never decide the tier. They are reported as supporting evidence. `pr06` counts towards rules 2 and 4 like any Poor item.
- Every found item stays in `findings/{job_id}.json` for the report, whatever the tier.

### Short classes
A recording under **5 minutes** is an excerpt or demo. It gets the same ladder, but the result is provisional and shown as **"On Track for [Tier]"**.

### Example
| Found | Result |
|---|---|
| `pr07`, `exe01`, `exe03` | Rule 1 → **Poor** |
| `pr06`, `exe01`, `exe03` | Rule 4 → **Average** (`pr06` caps it) |
| `avg01`, `exe03`, `gd02`, `gd03` | Rule 3 → **Average** |
| `exe01`, `exe03`, `gd01` | Rule 5 → **Excellent** |
| `exe03`, `gd02`, `gd03` | Rule 6 → **Good** |
| `exe03`, `gd01` | Rule 7 → **Average** |

### Partial rescore
After a review, the affected items are marked found or not found again and the ladder runs again. No LLM call is needed for the scoring itself.

### Known behaviour (accepted)
- No N/A status. Items that need the video or camera check (`exe02`, `exe05`, `pr01`) count as **not found** until that check runs.
- `pr03` (instructional abandonment) is not a red flag: it may have an emergency reason. Alone it caps the grade at Average (rule 4), and it is always shown to the reviewer.
- One Average primary item outweighs all strengths. A strong class with a single real flaw is Average.
