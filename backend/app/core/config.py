from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]

class Settings(BaseSettings):
    database_url: str
    redis_url: str
    jwt_secret: str
    jwt_algorithm : str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    hold_minutes: int = 5
    payment_webhook_secret: str
    run_workers: bool = True             # set RUN_WORKERS=false to start the API without background jobs
    cleanup_interval_seconds: int = 30
    outbox_interval_seconds: int = 5
    outbox_max_attempts: int = 5
    model_config = SettingsConfigDict(env_file=ROOT_DIR/".env", extra="ignore")

settings= Settings()