"""Settings read from environment variables (and `.env` locally). Missing values fail at startup."""

import logging
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from dotenv import load_dotenv

# Real environment variables (Space secrets) take precedence over this file.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

MAX_UPLOAD_BYTES = 500 * 1024 * 1024
ALLOWED_EXTENSIONS = frozenset({"mp4", "mkv", "webm", "mov"})
UPLOAD_URL_EXPIRES_S = 3600
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"


@dataclass(frozen=True)
class Settings:
    r2_account_id: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket: str
    sarvam_api_key: str
    gemini_api_key: str
    gemini_model: str


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable {name}")
    return value


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO)
    # httpx logs every request URL at INFO, and Sarvam's upload/download URLs are presigned
    # with live AWS session tokens; keep its warnings and errors only.
    logging.getLogger("httpx").setLevel(logging.WARNING)


@cache
def get_settings() -> Settings:
    load_dotenv(ENV_FILE)
    return Settings(
        r2_account_id=_require("R2_ACCOUNT_ID"),
        r2_access_key_id=_require("R2_ACCESS_KEY_ID"),
        r2_secret_access_key=_require("R2_SECRET_ACCESS_KEY"),
        r2_bucket=_require("R2_BUCKET"),
        sarvam_api_key=_require("api_subscription_key"),
        gemini_api_key=_require("GEMINI_API_KEY"),
        gemini_model=os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL,
    )
