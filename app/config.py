"""Centralized app configuration, loaded from environment variables / .env."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    openai_api_key: str
    openai_model: str = "gpt-5-nano"

    jwt_secret: str
    session_cookie_name: str = "ledgerask_session"
    session_max_age_days: int = 1
    remember_me_max_age_days: int = 30

    business_db_path: str = "data/ledgerask_business.db"
    app_db_path: str = "data/ledgerask_app.db"
    query_log_path: str = "logs/queries.log"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def business_db_full_path(self) -> Path:
        return BASE_DIR / self.business_db_path

    @property
    def app_db_full_path(self) -> Path:
        return BASE_DIR / self.app_db_path

    @property
    def query_log_full_path(self) -> Path:
        return BASE_DIR / self.query_log_path


settings = Settings()
