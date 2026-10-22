import logging
from pathlib import Path

import pytest

from app import config

R2_VARS = (
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET",
    "api_subscription_key",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in R2_VARS:
        monkeypatch.delenv(name, raising=False)
    config.get_settings.cache_clear()


def test_settings_load_from_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "R2_ACCOUNT_ID=acct\nR2_ACCESS_KEY_ID=key\nR2_SECRET_ACCESS_KEY=secret\nR2_BUCKET=bucket\n"
        "api_subscription_key=sarvam\nGEMINI_API_KEY=gemini\n"
    )
    monkeypatch.setattr(config, "ENV_FILE", env_file)

    settings = config.get_settings()

    assert settings == config.Settings(
        "acct", "key", "secret", "bucket", "sarvam", "gemini", config.DEFAULT_GEMINI_MODEL
    )


def test_real_environment_wins_over_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "R2_ACCOUNT_ID=acct\nR2_ACCESS_KEY_ID=key\nR2_SECRET_ACCESS_KEY=secret\nR2_BUCKET=from-file\n"
        "api_subscription_key=sarvam\nGEMINI_API_KEY=gemini\n"
    )
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    monkeypatch.setenv("R2_BUCKET", "from-space-secret")

    assert config.get_settings().r2_bucket == "from-space-secret"


def test_missing_variable_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "missing.env")

    with pytest.raises(RuntimeError, match="R2_ACCOUNT_ID"):
        config.get_settings()


def test_logging_hides_httpx_request_urls() -> None:
    # httpx logs every request URL at INFO; Sarvam's are presigned with live AWS tokens.
    config.configure_logging()

    assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING
