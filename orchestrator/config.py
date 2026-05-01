"""Pydantic Settings with .env support for the EcoNest orchestrator."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    VERSION: str = "0.1.0"

    # ArcadeDB
    ARCADEDB_HOST: str = "localhost"
    ARCADEDB_PORT: int = 2480
    ARCADEDB_USER: str = "root"
    ARCADEDB_PASSWORD: str = "playwithdata"
    ARCADEDB_DATABASE: str = "econest"

    # MySQL
    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = "econest"
    MYSQL_DATABASE: str = "econest"
    MYSQL_POOL_SIZE: int = 10

    # Ollama / LLM
    OLLAMA_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "gemma4"
    OLLAMA_FALLBACK_MODEL: str = "mistral"

    # Home Assistant
    HA_URL: str = "http://localhost:8123"
    HA_TOKEN: str = ""

    # Security
    SECRET_KEY: str = "change-me-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ALGORITHM: str = "HS256"

    # Orchestrator
    ORCHESTRATOR_URL: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return cached Settings instance."""
    return Settings()
