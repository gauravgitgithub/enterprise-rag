from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from enterprise_rag.constants.retrieval import (
    DEFAULT_HNSW_EF_SEARCH,
    DEFAULT_RRF_K,
    DEFAULT_SEARCH_CANDIDATES,
    DEFAULT_SEARCH_TOP_N,
)


class Settings(BaseSettings):
    """Loaded from environment variables, then .env. Real env vars win (k8s injects them)."""

    model_config = SettingsConfigDict(env_prefix="RAG_", env_file=".env", extra="ignore")

    # No defaults: a missing DB URL must fail at startup, not fall back to a guess.
    # App role: non-owner, NOBYPASSRLS. Every query it runs is tenant-filtered by Postgres.
    database_url: str
    # Schema owner, used only by Alembic.
    database_admin_url: str

    rrf_k: int = Field(DEFAULT_RRF_K, gt=0)
    search_candidates: int = Field(DEFAULT_SEARCH_CANDIDATES, gt=0)
    search_top_n: int = Field(DEFAULT_SEARCH_TOP_N, gt=0)
    hnsw_ef_search: int = Field(DEFAULT_HNSW_EF_SEARCH, gt=0)

    @model_validator(mode="after")
    def _check_search_limits(self) -> Settings:
        if self.hnsw_ef_search < self.search_candidates:
            raise ValueError("RAG_HNSW_EF_SEARCH must be >= RAG_SEARCH_CANDIDATES")
        if self.search_top_n > self.search_candidates:
            raise ValueError("RAG_SEARCH_TOP_N must be <= RAG_SEARCH_CANDIDATES")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from env/.env
