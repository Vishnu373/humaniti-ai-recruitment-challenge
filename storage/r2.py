"""Thin Cloudflare R2 client (S3 API via boto3). Every failure becomes STORAGE_FAILED."""

import json
from functools import cache
from pathlib import Path
from typing import IO, Protocol, TypedDict, cast

# boto3/botocore ship no type stubs; the calls used are typed by _S3 below.
import boto3  # pyright: ignore[reportMissingTypeStubs]
from botocore.client import Config  # pyright: ignore[reportMissingTypeStubs]
from botocore.exceptions import (  # pyright: ignore[reportMissingTypeStubs]
    BotoCoreError,
    ClientError,
)

from app.config import get_settings
from app.errors import AppError


class _HeadResult(TypedDict):
    ContentLength: int


class _GetResult(TypedDict):
    Body: IO[bytes]


class _S3(Protocol):
    """The S3 calls used here. boto3 ships no type hints, so this is declared by hand."""

    def generate_presigned_url(
        self, ClientMethod: str, Params: dict[str, object], ExpiresIn: int
    ) -> str: ...

    def head_object(self, *, Bucket: str, Key: str) -> _HeadResult: ...

    def get_object(self, *, Bucket: str, Key: str) -> _GetResult: ...

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> object: ...

    def upload_file(
        self, Filename: str, Bucket: str, Key: str, ExtraArgs: dict[str, str]
    ) -> None: ...

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None: ...


@cache
def _client() -> _S3:
    settings = get_settings()
    # cast: boto3.client() is untyped; _S3 declares the subset of methods this module calls.
    client = boto3.client(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType] -- no stubs
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
        # Above boto3's default of 10: concurrent jobs' transfers share this one client.
        config=Config(signature_version="s3v4", max_pool_connections=15),
    )
    return cast(_S3, client)


def _is_not_found(e: ClientError) -> bool:
    # cast: botocore types ClientError.response loosely; the S3 error code is a str when present.
    code = cast(dict[str, dict[str, str]], e.response).get("Error", {}).get("Code")
    return code in ("404", "NoSuchKey", "NotFound")


def _bucket() -> str:
    return get_settings().r2_bucket


def presign_put(key: str, content_type: str, size_bytes: int, expires_s: int) -> str:
    """Single-PUT URL. ContentLength and ContentType are signed, so R2 rejects anything else."""
    try:
        return _client().generate_presigned_url(
            "put_object",
            Params={
                "Bucket": _bucket(),
                "Key": key,
                "ContentType": content_type,
                "ContentLength": size_bytes,
            },
            ExpiresIn=expires_s,
        )
    except (BotoCoreError, ClientError) as e:
        raise AppError("STORAGE_FAILED", f"Could not presign upload for {key}: {e}") from e


def object_size(key: str) -> int | None:
    """Size of the object in bytes, or None if it doesn't exist."""
    try:
        head = _client().head_object(Bucket=_bucket(), Key=key)
    except ClientError as e:
        if _is_not_found(e):
            return None
        raise AppError("STORAGE_FAILED", f"Could not read {key}: {e}") from e
    except BotoCoreError as e:
        raise AppError("STORAGE_FAILED", f"Could not read {key}: {e}") from e
    return head["ContentLength"]


def upload_file(path: Path, key: str, content_type: str) -> None:
    try:
        _client().upload_file(str(path), _bucket(), key, ExtraArgs={"ContentType": content_type})
    except (BotoCoreError, ClientError) as e:
        raise AppError("STORAGE_FAILED", f"Could not upload {key}: {e}") from e


def download_file(key: str, path: Path) -> None:
    try:
        _client().download_file(_bucket(), key, str(path))
    except (BotoCoreError, ClientError) as e:
        raise AppError("STORAGE_FAILED", f"Could not download {key}: {e}") from e


def put_json(key: str, data: object) -> None:
    try:
        _client().put_object(
            Bucket=_bucket(),
            Key=key,
            Body=json.dumps(data).encode(),
            ContentType="application/json",
        )
    except (BotoCoreError, ClientError) as e:
        raise AppError("STORAGE_FAILED", f"Could not write {key}: {e}") from e


def get_json(key: str) -> object | None:
    """Parsed JSON at key, or None if it doesn't exist."""
    try:
        obj = _client().get_object(Bucket=_bucket(), Key=key)
    except ClientError as e:
        if _is_not_found(e):
            return None
        raise AppError("STORAGE_FAILED", f"Could not read {key}: {e}") from e
    except BotoCoreError as e:
        raise AppError("STORAGE_FAILED", f"Could not read {key}: {e}") from e
    return json.loads(obj["Body"].read())
