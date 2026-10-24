from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]

class Settings(BaseSettings):
    database_url: str
    redis_url: str
    model_config = SettingsConfigDict(env_file=ROOT_DIR/".env", extra="ignore")

settings= Settings()