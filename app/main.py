"""FastAPI app: ingest API under /api, Gradio UI mounted at /."""

import logging

import gradio as gr
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import configure_logging, get_settings
from app.errors import AppError
from app.ui import build_ui
from assessment import ingest
from storage import jobs

configure_logging()
log = logging.getLogger(__name__)

get_settings()  # fail at startup if R2 settings are missing

app = FastAPI(title="Class Recording Quality Assessor")


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.envelope())


@app.exception_handler(Exception)
async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
    # Logged here; Starlette's ServerErrorMiddleware re-raises it after this response.
    log.exception("Unhandled error", exc_info=exc)
    return JSONResponse(
        status_code=500, content=AppError("INTERNAL_ERROR", "Internal server error").envelope()
    )


class UploadUrlRequest(BaseModel):
    filename: str
    size: int


class UploadUrlResponse(BaseModel):
    job_id: str
    upload_url: str
    content_type: str


class DriveRequest(BaseModel):
    url: str


@app.post("/api/upload-url")
def upload_url(req: UploadUrlRequest) -> UploadUrlResponse:
    job, url, content_type = ingest.create_upload(req.filename, req.size)
    return UploadUrlResponse(job_id=job.job_id, upload_url=url, content_type=content_type)


@app.post("/api/jobs/{job_id}/uploaded")
def uploaded(job_id: str, background: BackgroundTasks) -> jobs.Job:
    job, started = ingest.confirm_upload(job_id)
    if started:
        background.add_task(jobs.run_pipeline, job.job_id)
    return job


@app.post("/api/drive")
def drive(req: DriveRequest, background: BackgroundTasks) -> jobs.Job:
    job, file_id = ingest.start_drive(req.url)
    # Tasks run in order; run_pipeline skips the job if the download failed.
    background.add_task(ingest.run_drive_download, job.job_id, file_id)
    background.add_task(jobs.run_pipeline, job.job_id)
    return job


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> jobs.Job:
    return jobs.load_job(job_id)


# Gradio leaves the `auth` callable unannotated, so its signature is partially unknown.
app = gr.mount_gradio_app(app, build_ui(), path="/")  # pyright: ignore[reportUnknownMemberType]
