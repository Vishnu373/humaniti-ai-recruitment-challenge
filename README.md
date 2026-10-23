# Class Recording Quality Assessor

Grades an online class recording as Poor / Average / Good / Excellent with timestamped evidence. See `docs/design.md` for the architecture and build plan.

**Current state:** Pieces 1–5.

1. A recording is uploaded straight from the browser (or fetched from a public Google Drive link) into Cloudflare R2.
2. The server downloads it once; ffmpeg cuts 10-minute 16 kHz WAV slices (15 s overlap) straight from the video and queues each one as soon as it's cut, while 3 workers send slices to Sarvam Batch STT in parallel (producer-consumer).
3. The slice transcripts are merged into one timestamped transcript (overlap duplicates dropped).
4. Gemini marks every checklist item found / not found; every quote it cites is verified against the transcript in code.
5. A rule ladder (`docs/scoring_logic.md`) turns the found items into a tier.

The backend pushes each status change to the page (no polling), and at the end the page shows the grade and the full report. `docs/sample.log` is the server log of a real 46-minute class going through the whole pipeline.

## Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- ffmpeg and ffprobe on `PATH` (`sudo apt install ffmpeg`), used to cut audio slices from the video and by the tests
- A Cloudflare account with R2 enabled (setup step 2 creates the bucket and API token)
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

### 2. Create the R2 bucket and API token

In the [Cloudflare dashboard](https://dash.cloudflare.com):

1. **R2 Object Storage → Create bucket.** Pick any name; it becomes `R2_BUCKET`. Location and storage class can stay at their defaults.
2. **Copy your Account ID** from the R2 overview page (*Account Details*). It becomes `R2_ACCOUNT_ID`; the app builds the endpoint `https://<account_id>.r2.cloudflarestorage.com` from it.
3. **R2 Object Storage → Manage API tokens → Create API token**, then:
   - **Permissions:** **Object Read & Write** is enough for the app. Step 5 (the auto-delete rule) changes a bucket setting, which needs **Admin Read & Write**; either pick that here, or create a second, admin token just for step 5.
   - **Specify bucket(s):** apply the token to the bucket from step 1 only.
4. **Copy the Access Key ID and Secret Access Key** shown after creating the token. These become `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY`. The secret is shown only once. The *Token value* on the same screen is not used, because the app talks to R2 through the S3 API.

### 3. Configure environment variables

Add these to `.env` in the project root (never commit it). The app loads it with `python-dotenv`; real environment variables, such as Space secrets, take precedence:

```bash
R2_ACCOUNT_ID=...          # step 2.2: Cloudflare account ID
R2_ACCESS_KEY_ID=...       # step 2.4: Access Key ID
R2_SECRET_ACCESS_KEY=...   # step 2.4: Secret Access Key
R2_BUCKET=...              # step 2.1: bucket name
api_subscription_key=...   # Sarvam AI API key
GEMINI_API_KEY=...         # Gemini API key
# GEMINI_MODEL=...         # optional, defaults to gemini-3.5-flash-lite
```

The app refuses to start if any of them is missing.

### 4. Add a CORS rule to the bucket

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

The browser only sends one presigned `PUT`, so no other methods are needed. The origin must match exactly: `http://127.0.0.1:7860` is a different origin from `http://localhost:7860`, and the upload fails with *"Upload to storage failed: network error or CORS"*.

### 5. Auto-delete after 24 hours (optional)

Recordings are private, so the bucket can delete everything a day after it was written. This sets an R2 lifecycle rule once; R2 then deletes on its own (within about a day of expiry, so objects can live up to ~48 h). It covers videos, transcripts, findings and job records, and keeps any other lifecycle rules on the bucket.

Writing lifecycle rules needs an **Admin Read & Write** token (step 2.3). With an Object Read & Write token, `apply` and `remove` fail with `STORAGE_FAILED … AccessDenied`. If you made a separate admin token, put its keys in `.env` while you run these commands, then switch back:

```bash
python -m storage.r2_cleanup show     # list the bucket's lifecycle rules (default)
python -m storage.r2_cleanup apply    # add the "delete-after-24h" rule
python -m storage.r2_cleanup remove   # remove it again
```

## Run

```bash
uvicorn app.main:app --port 7860
```

Open <http://localhost:7860> (not the `127.0.0.1` link uvicorn prints, see the CORS note above). Add `--reload` while developing.

The server log shows each job's progress, including one line per slice (`cutting` → `queued` → `sent to Sarvam` → `transcribed`). httpx request lines are hidden because Sarvam's upload URLs carry temporary AWS tokens.

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
| `POST` | `/api/jobs/{job_id}/uploaded` | none | job record; starts the pipeline (a duplicate confirm is a no-op) |
| `POST` | `/api/drive` | `{"url": "https://drive.google.com/file/d/…/view"}` | job record; the download and then the pipeline run in the background |
| `GET` | `/api/jobs/{job_id}` | none | job record (for scripts; the page gets pushed updates instead) |

The `upload_url` is a presigned single `PUT`, valid for 1 hour. Send the file with the returned `content_type` as the `Content-Type` header; its size must match `size`.

Errors use one envelope:

```json
{ "error": { "code": "UPLOAD_TOO_LARGE", "message": "…", "job_id": null } }
```

Codes and their meaning are listed in `docs/design.md`.

## Checks

```bash
pyright                                  # typecheck (strict)
ruff check . && ruff format --check .    # lint + format
pytest                                   # tests
```

## Project layout

- `app/`: FastAPI endpoints (`main.py`), Gradio UI and report (`ui.py`), config and errors
- `storage/`: R2 client (`r2.py`), job records, pipeline runner and status notifier (`jobs.py`), 24 h auto-delete rule (`r2_cleanup.py`)
- `assessment/`: one module per pipeline step (audio, transcript, dead air, evaluate, evidence, scoring) + `prompts.md`
- `docs/`:
  - `design.md`: architecture, lookup table and build pieces
  - `class_quality_checklist.md`, `scoring_logic.md`: the quality criteria and tier rules (source of truth for item IDs)
  - `ai_interaction_history.md`: the prompts and decisions behind the build
  - `sample.log`: server log of one full run
- `tests/`: pytest unit tests

## Known limitations

- Single process: live status updates come from an in-process notifier, so the app runs as one uvicorn worker (fine for one Space container).
- Speaker labels come from separate Sarvam jobs per slice, so `SPEAKER_00` in one slice may be a different person in the next; Gemini infers speakers from context.
- A failed slice (e.g. a network timeout to Sarvam) fails the whole job; there are no retries yet.
- Video-only checklist items (camera on, etc.) are marked "Needs video check" until the frame check (edge case 2) is built.
