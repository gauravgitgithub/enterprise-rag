from pydantic_settings import BaseSettings, SettingsConfigDict


class MigrationSettings(BaseSettings):
    """What Alembic needs. Loaded from environment variables, then .env; env vars win."""

    model_config = SettingsConfigDict(env_prefix="RAG_", env_file=".env", extra="ignore")

    # No defaults: a missing DB URL must fail at startup, not fall back to a guess.
    # Schema owner, used only by Alembic.
    database_admin_url: str


class Settings(BaseSettings):
    """What the app needs. Deliberately not the admin URL: app pods never hold owner creds."""

    model_config = MigrationSettings.model_config

    # App role: non-owner, NOBYPASSRLS. Every query it runs is tenant-filtered by Postgres.
    database_url: str


class QueueSettings(BaseSettings):
    """Where Celery jobs go. Shared by the relay (producer) and the worker (consumer)."""

    model_config = SettingsConfigDict(env_prefix="RAG_", env_file=".env", extra="ignore")

    aws_region: str
    # Locally, AWS_ENDPOINT_URL_SQS / AWS_ENDPOINT_URL_S3 point boto3 at ElasticMQ / SeaweedFS.
    ingest_queue_url: str


class RelaySettings(QueueSettings):
    """What the outbox relay needs. Its own DB role: reads every tenant's outbox, nothing else."""

    relay_database_url: str


class WorkerSettings(Settings, QueueSettings):
    """What the ingest worker needs on top of the app settings."""

    s3_bucket: str
    tei_url: str
