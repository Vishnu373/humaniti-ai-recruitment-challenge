# CLAUDE.md

Prototype that grades an online class recording as Poor / Average / Good / Excellent with timestamped evidence. Keep it simple: ~5–6 h of work. **Read `design.md` before exploring the code.**

## Stack

Python 3.12, FastAPI + Gradio (one process), Cloudflare R2 (boto3), Sarvam ASR, Gemini, ffmpeg. Deployed as a Docker Hugging Face Space.

## Project structure

- `app/`: FastAPI endpoints (`main.py`), Gradio UI (`ui.py`), shared `config.py` and `errors.py`
- `storage/`: R2 client (`r2.py`) and job records (`jobs.py`)
- `assessment/`: one module per pipeline step (ingest, audio, transcript, checklist, dead_air, evaluate, evidence, scoring) + `prompts.md`
- `docs/`: quality checklist and scoring rules (source of truth for item IDs and tier logic)
- `scripts/`: one-off tools, e.g. the labelled-set evaluation
- `tests/`: pytest unit tests
- `design.md`: architecture and lookup table
- `.env`: local secrets (never commit, never print)

## Commands

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 7860   # run locally (reads R2_* vars from .env)
pyright                                     # typecheck (strict)
ruff check . && ruff format --check .       # lint
pytest                                      # tests
```

## Rules

- **Clarify first.** If requirements, scope or intent are unclear, ask. Don't guess silently.
- **Types:** pyright `strict`. No `Any`, `# type: ignore` or `cast` without a comment explaining why. Typecheck must pass before work is called done.
- **Testing loop:** write a failing test, make it pass, then run pyright + ruff + pytest. Loop until green. Report the real output.
- **Keep the LLM out of deterministic logic.** Scoring, dead air and evidence checks are plain Python and unit-tested.
- **LLM output is untrusted.** Validate it against a schema; a malformed response raises `AppError("LLM_BAD_OUTPUT")`.
- **Checklist item IDs come from `docs/`.** Don't hard-code a second copy.

## Error handling

- Fail early and loudly. No empty `except`, no `except: print(e)`.
- Raise `AppError(code, message)` from `app/errors.py` with a stable code. Unexpected errors surface as 500.
- Every error response uses this envelope:
  ```json
  { "error": { "code": "ASR_FAILED", "message": "…", "job_id": "…" } }
  ```

## End-to-end and UI testing

- Developer/user will test it. No automatic testing for this. 
- Only test the code.

## Dependencies

- Ask before adding one. Check weekly downloads > ~100k, a release in the last 6 months, and more than one maintainer. Pin exact versions in `requirements.txt`.
- Approved exceptions: `gdown` (single maintainer) for Drive links; `sarvamai` (~41k weekly downloads), the official Sarvam SDK. Both chosen by the user.

## Failure log
- Type new S3 calls by extending the `_S3` Protocol in `storage/r2.py`; boto3 has no stubs and `boto3-stubs` is not an approved dependency.
- Keep `app/__init__.py` empty: `storage/` and `assessment/` import `app.config`/`app.errors` while `app.main` imports them, so any import in it creates a cycle.
- Call Gemini through the Interactions API exactly as the current docs show (`client.interactions.create`, `generation_config={"thinking_level": "medium"}`), not `models.generate_content` with typed enums.
- After editing `docs/class_quality_checklist.md`, run `pytest tests/test_checklist.py`: `checklist.py` parses its exact line format (`` * **`id` Title:** text **[Source]** ``), and a format change silently yields zero items.
- Keep `thinking_level` at "medium" for the evaluation: on "low", flash-lite returns zero thought tokens and marks items found on surface matches; more prompt text (rules, few-shot examples) did not fix that.
