from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    app_name: str = "ExamFlow"
    app_env: str = "development"
    debug: bool = False
    secret_key: str = Field(
        default="change-this-development-secret-before-deployment",
        min_length=32,
    )
    database_url: str = (
        "postgresql+psycopg://exam_user:exam_password@localhost:5432/exam_system"
    )
    app_timezone: str = "Asia/Kolkata"
    session_cookie_name: str = "exam_sentinel_session"
    session_max_age_seconds: int = 60 * 60 * 24 * 14
    session_https_only: bool = False
    max_exam_upload_bytes: int = 2 * 1024 * 1024
    allow_instructor_registration: bool = True
    proctor_heartbeat_seconds: int = 10

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

