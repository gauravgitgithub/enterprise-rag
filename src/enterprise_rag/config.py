from pydantic_settings import BaseSettings, SettingsConfigDict


class MigrationSettings(BaseSettings):
    """What Alembic needs. Loaded from environment variables, then .env; env vars win."""

    model_config = SettingsConfigDict(env_prefix="RAG_", env_file=".env", extra="ignore")

    # No defaults: a missing DB URL must fail at startup, not fall back to a guess.
    # Schema owner, used only by Alembic.
    database_admin_url: str


class Settings(MigrationSettings):
    """What the app needs."""

    # App role: non-owner, NOBYPASSRLS. Every query it runs is tenant-filtered by Postgres.
    database_url: str
