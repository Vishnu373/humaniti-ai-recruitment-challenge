"""Piece 1: get a recording into R2, by presigned browser upload or from a public Drive link."""

import logging
import re
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import gdown
from gdown.exceptions import DownloadCancelled, DownloadError

from app.config import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES, UPLOAD_URL_EXPIRES_S
from app.errors import AppError
from storage import jobs, r2

log = logging.getLogger(__name__)

CONTENT_TYPES = {
    "mp4": "video/mp4",
    "mkv": "video/x-matroska",
    "webm": "video/webm",
    "mov": "video/quicktime",
}

_DRIVE_ID = re.compile(r"^[A-Za-z0-9_-]{10,}$")


def validate_video(filename: str, size_bytes: int) -> str:
    """Return the lower-cased extension, or raise if the size or type isn't accepted."""
    if size_bytes <= 0:
        raise AppError("UNSUPPORTED_FILE_TYPE", f"{filename!r} is empty")
    if size_bytes > MAX_UPLOAD_BYTES:
        raise AppError(
            "UPLOAD_TOO_LARGE",
            f"{filename!r} is {size_bytes / 1024**2:.0f} MB; the limit is "
            f"{MAX_UPLOAD_BYTES // 1024**2} MB",
        )
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in ALLOWED_EXTENSIONS:
        raise AppError(
            "UNSUPPORTED_FILE_TYPE",
            f"{filename!r} is not one of: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )
    return ext


def parse_drive_file_id(url: str) -> str:
    """Extract the file ID from a drive.google.com file link (folders are rejected)."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or parsed.hostname != "drive.google.com":
        raise AppError("INVALID_DRIVE_LINK", "Not a drive.google.com link")

    file_id: str | None = None
    match = re.match(r"^/file/d/([^/]+)", parsed.path)
    if match:
        file_id = match.group(1)
    elif parsed.path in ("/open", "/uc"):
        file_id = parse_qs(parsed.query).get("id", [None])[0]

    if file_id is None or not _DRIVE_ID.match(file_id):
        raise AppError("INVALID_DRIVE_LINK", "Not a Google Drive file link")
    return file_id


# Path A: browser uploads straight to R2


def create_upload(filename: str, size_bytes: int) -> tuple[jobs.Job, str, str]:
    """Create the job and a presigned PUT URL. Returns (job, upload_url, content_type)."""
    ext = validate_video(filename, size_bytes)
    job_id = jobs.new_job_id()
    video_key = f"videos/{job_id}.{ext}"
    content_type = CONTENT_TYPES[ext]
    upload_url = r2.presign_put(video_key, content_type, size_bytes, UPLOAD_URL_EXPIRES_S)
    job = jobs.create_job(job_id, "upload", "UPLOADING", video_key, size_bytes)
    return job, upload_url, content_type


def confirm_upload(job_id: str) -> tuple[jobs.Job, bool]:
    """Move the job to EXTRACTING once R2 has the whole object.

    Returns (job, started); started is False for a duplicate confirm, so the caller must not
    queue the pipeline again.
    """
    job = jobs.load_job(job_id)
    if job.status not in ("UPLOADING", "FAILED"):
        # Already confirmed and further along the pipeline: a duplicate confirm is a no-op.
        return job, False
    # A Drive job has no video_key until its download finishes.
    if job.status != "UPLOADING" or job.video_key is None:
        raise AppError("UPLOAD_INCOMPLETE", f"Job is {job.status}, not awaiting upload", job_id)

    actual = r2.object_size(job.video_key)
    if actual is None:
        raise AppError("UPLOAD_INCOMPLETE", "The video isn't in storage yet", job_id)
    if actual != job.size_bytes:
        error = AppError(
            "STORAGE_FAILED", f"Stored size {actual} != declared size {job.size_bytes}", job_id
        )
        jobs.fail_job(job, error)
        raise error

    job.status = "EXTRACTING"
    jobs.save_job(job)
    return job, True


# Path B: public Google Drive link, downloaded on the server


def start_drive(url: str) -> tuple[jobs.Job, str]:
    """Validate the link and create the job. Returns (job, drive_file_id)."""
    file_id = parse_drive_file_id(url)
    job = jobs.create_job(jobs.new_job_id(), "drive", "UPLOADING")
    return job, file_id


def run_drive_download(job_id: str, file_id: str) -> None:
    """Background task: Drive → temp → R2. Any failure is recorded on the job."""
    job = jobs.load_job(job_id)
    try:
        _drive_to_r2(job, file_id)
    except AppError as e:
        jobs.fail_job(job, e)
    except Exception:
        # Unexpected: record it so the job doesn't sit in UPLOADING forever, then re-raise.
        jobs.fail_job(job, AppError("INTERNAL_ERROR", "Unexpected error during Drive download"))
        raise


def _drive_to_r2(job: jobs.Job, file_id: str) -> None:
    cancel = threading.Event()

    def stop_if_too_large(downloaded: int, total: int | None) -> None:
        if downloaded > MAX_UPLOAD_BYTES or (total is not None and total > MAX_UPLOAD_BYTES):
            cancel.set()

    with tempfile.TemporaryDirectory() as tmp:
        try:
            out = gdown.download(
                id=file_id,
                output=tmp + "/",
                quiet=True,
                progress=stop_if_too_large,
                cancel=cancel,
            )
        except DownloadCancelled as e:
            raise AppError(
                "UPLOAD_TOO_LARGE", f"Drive file is over {MAX_UPLOAD_BYTES // 1024**2} MB"
            ) from e
        except DownloadError as e:
            raise AppError(
                "DOWNLOAD_FAILED", f"Could not download from Drive (is it public?): {e}"
            ) from e

        if not isinstance(out, str):
            raise AppError("DOWNLOAD_FAILED", "Drive download returned no file")
        path = Path(out)
        size_bytes = path.stat().st_size
        ext = validate_video(path.name, size_bytes)
        video_key = f"videos/{job.job_id}.{ext}"
        r2.upload_file(path, video_key, CONTENT_TYPES[ext])

    job.video_key = video_key
    job.size_bytes = size_bytes
    job.status = "EXTRACTING"
    jobs.save_job(job)
    log.info("Job %s: Drive file %s uploaded to %s", job.job_id, file_id, video_key)
