import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from enterprise_rag.constants.documents import (
    CONTENT_HASH_LEN,
    DOCUMENT_STATUS_PENDING,
    STATUS_MAX_LEN,
    TENANT_NAME_MAX_LEN,
    TITLE_MAX_LEN,
)
from enterprise_rag.constants.outbox import TOPIC_MAX_LEN
from enterprise_rag.constants.retrieval import EMBEDDING_DIM, FTS_LANGUAGE


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.uuidv7())


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(TENANT_NAME_MAX_LEN), unique=True, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "content_hash", "acl_groups", name="uq_documents_tenant_hash_acl"
        ),
        # Target for the chunks composite FK below.
        UniqueConstraint("id", "tenant_id", name="uq_documents_id_tenant"),
        CheckConstraint("cardinality(acl_groups) > 0", name="ck_documents_acl_not_empty"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(TITLE_MAX_LEN), nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(CONTENT_HASH_LEN), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Fail-closed: a document with no groups is visible to nobody, so empty is rejected.
    acl_groups: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    status: Mapped[str] = mapped_column(
        String(STATUS_MAX_LEN), nullable=False, default=DOCUMENT_STATUS_PENDING
    )
    created_at: Mapped[datetime] = _created_at()


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        CheckConstraint("cardinality(acl_groups) > 0", name="ck_chunks_acl_not_empty"),
        # A chunk can never point at another tenant's document.
        ForeignKeyConstraint(
            ["document_id", "tenant_id"],
            ["documents.id", "documents.tenant_id"],
            ondelete="CASCADE",
            name="fk_chunks_document_same_tenant",
        ),
        Index("ix_chunks_tenant_document", "tenant_id", "document_id"),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("ix_chunks_acl_groups", "acl_groups", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)  # already PII-redacted
    section_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    page: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    # Denormalized from the document so ACL filtering happens inside the retrieval query.
    acl_groups: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed(f"to_tsvector('{FTS_LANGUAGE}', content)", persisted=True)
    )
    created_at: Mapped[datetime] = _created_at()


class OutboxEvent(Base):
    """Written in the same transaction as the change it announces; deleted once published."""

    __tablename__ = "outbox"

    id: Mapped[uuid.UUID] = _pk()  # uuidv7: ordering by id is ordering by creation
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    topic: Mapped[str] = mapped_column(String(TOPIC_MAX_LEN), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created_at()
