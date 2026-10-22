"""AppError with stable codes and the error envelope used by every error response."""

from typing import Literal, TypedDict

ErrorCode = Literal[
    "UPLOAD_TOO_LARGE",
    "UNSUPPORTED_FILE_TYPE",
    "INVALID_DRIVE_LINK",
    "STORAGE_FAILED",
    "DOWNLOAD_FAILED",
    "MEDIA_FAILED",
    "NO_AUDIO_STREAM",
    "ASR_FAILED",
    "LLM_FAILED",
    "LLM_BAD_OUTPUT",
    "JOB_NOT_FOUND",
    "UPLOAD_INCOMPLETE",
    "INTERNAL_ERROR",
]

HTTP_STATUS: dict[ErrorCode, int] = {
    "UPLOAD_TOO_LARGE": 413,
    "UNSUPPORTED_FILE_TYPE": 415,
    "INVALID_DRIVE_LINK": 400,
    "STORAGE_FAILED": 502,
    "DOWNLOAD_FAILED": 502,
    "MEDIA_FAILED": 500,
    "NO_AUDIO_STREAM": 422,
    "ASR_FAILED": 502,
    "LLM_FAILED": 502,
    "LLM_BAD_OUTPUT": 502,
    "JOB_NOT_FOUND": 404,
    "UPLOAD_INCOMPLETE": 409,
    "INTERNAL_ERROR": 500,
}


class ErrorBody(TypedDict):
    code: ErrorCode
    message: str
    job_id: str | None


class ErrorEnvelope(TypedDict):
    error: ErrorBody


class AppError(Exception):
    def __init__(self, code: ErrorCode, message: str, job_id: str | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code: ErrorCode = code
        self.message = message
        self.job_id = job_id

    @property
    def status_code(self) -> int:
        return HTTP_STATUS[self.code]

    def envelope(self) -> ErrorEnvelope:
        return {"error": {"code": self.code, "message": self.message, "job_id": self.job_id}}
