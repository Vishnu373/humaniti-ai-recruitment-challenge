# Class Recording Quality Assessor

Grades an online class recording as Poor / Average / Good / Excellent with timestamped evidence. See `design.md` for the architecture and build plan.

**Current state:** Pieces 1–5. A recording is uploaded (or fetched from a public Google Drive link) into Cloudflare R2, its audio is extracted to a 16 kHz mono WAV, Sarvam Batch STT turns it into a timestamped transcript, Gemini marks every checklist item found / not found with quotes verified against the transcript, and a rule ladder (`docs/scoring_logic.md`) turns the found items into a tier. A job record tracks the status and the page shows the grade.

## Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- ffmpeg and ffprobe on `PATH` (`sudo apt install ffmpeg`), used for audio extraction and by the tests
- A Cloudflare R2 bucket and an R2 API token with **Object Read & Write** on it
- A [Sarvam AI](https://dashboard.sarvam.ai) API key
- A [Gemini](https://aistudio.google.com/apikey) API key

## Setup

### 1. Install dependencies

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install -r requirements.txt
```

Every command below assumes the virtualenv is active (`source .venv/bin/activate`).

### 2. Configure environment variables

Add these to `.env` in the project root (never commit it). The app loads it with `python-dotenv`; real environment variables, such as Space secrets, take precedence:

```bash
R2_ACCOUNT_ID=...          # Cloudflare account ID (from the R2 dashboard)
R2_ACCESS_KEY_ID=...       # R2 API token access key ID
R2_SECRET_ACCESS_KEY=...   # R2 API token secret
R2_BUCKET=...              # bucket name
api_subscription_key=...   # Sarvam AI API key
GEMINI_API_KEY=...         # Gemini API key
# GEMINI_MODEL=...         # optional, defaults to gemini-3.5-flash-lite
```

The app refuses to start if any of them is missing.

### 3. Add a CORS rule to the bucket

The browser uploads the video straight to R2, so the bucket must allow `PUT` from the app's origin. In the Cloudflare dashboard: **R2 → your bucket → Settings → CORS Policy**, add:

```json
[
  {
    "AllowedOrigins": ["http://localhost:7860"],
    "AllowedMethods": ["PUT"],
    "AllowedHeaders": ["content-type"],
    "MaxAgeSeconds": 3600
  }
]
```

When deployed, add the Space's origin (e.g. `https://<user>-<space>.hf.space`) to `AllowedOrigins`.

## Run

```bash
uvicorn app.main:app --port 7860
```

Open <http://localhost:7860>. Add `--reload` while developing.

## Using the app

- **Upload a file:** choose an `.mp4`, `.mkv`, `.webm` or `.mov` file (≤ 500 MB) and click **Upload**. A progress line shows the upload to R2.
- **Drive link:** paste a public `drive.google.com/file/d/…` link and click **Submit link**. The server downloads it and copies it to R2 in the background.

Either way, the **Job ID** appears and the backend pushes each status change to the page (no polling):

| Status | Meaning |
|---|---|
| `UPLOADING` | Upload URL issued and the file is not yet confirmed in R2, or the server is fetching the Drive file |
| `EXTRACTING` | Video is in R2 at `videos/{job_id}.{ext}`; the server downloads it and checks its duration and audio track |
| `TRANSCRIBING` | The job record has `duration_s`; ffmpeg is cutting 10-min 16 kHz WAV slices straight from the video (15 s overlap) into a queue while 3 workers send each slice to Sarvam Batch STT; per-slice progress is in the server log |
| `EVALUATING` | Transcript is in R2 at `transcripts/{job_id}.json`; Gemini is marking the checklist items |
| `SCORE_APPLYING` | Quotes are verified; the scoring ladder is turning the found items into a tier |
| `COMPLETE` | Findings and score are in R2 at `findings/{job_id}.json`; the page shows the grade and the full report: every checklist item by tier with timestamped quotes, class facts and flagged lines |
| `FAILED` | Something went wrong; the error code and message are shown (e.g. `NO_AUDIO_STREAM`, `MEDIA_FAILED` for a corrupt file, `ASR_FAILED` for a Sarvam error, `LLM_FAILED` / `LLM_BAD_OUTPUT` for Gemini) |

Job records live in R2 at `jobs/{job_id}.json`.

## API

| Method | Path | Body | Returns |
|---|---|---|---|
| `POST` | `/api/upload-url` | `{"filename": "class.mp4", "size": 123456}` | `{job_id, upload_url, content_type}` |
| `POST` | `/api/jobs/{job_id}/uploaded` | none | job record; starts audio extraction |
| `POST` | `/api/drive` | `{"url": "https://drive.google.com/file/d/…/view"}` | job record; download then audio extraction run in the background |
| `GET` | `/api/jobs/{job_id}` | none | job record |

The `upload_url` is a presigned single `PUT`, valid for 1 hour. Send the file with the returned `content_type` as the `Content-Type` header; its size must match `size`.

Errors use one envelope:

```json
{ "error": { "code": "UPLOAD_TOO_LARGE", "message": "…", "job_id": null } }
```

Codes and their meaning are listed in `design.md`.

## Checks

```bash
pyright                                  # typecheck (strict)
ruff check . && ruff format --check .    # lint + format
pytest                                   # tests
```

## Project layout

- `app/`: FastAPI endpoints, Gradio UI, config and errors
- `storage/`: R2 client and job records
- `assessment/`: one module per pipeline step (see `design.md`)
- `docs/`: quality checklist and scoring rules
- `design.md`: architecture, lookup table and build pieces
