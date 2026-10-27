import uuid
from dataclasses import dataclass

from enterprise_rag.constants.auth import GROUP_NAME_RE


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Who is asking. Built only from verified JWT claims, never from the request body."""

    tenant_id: uuid.UUID
    groups: frozenset[str]

    def __post_init__(self) -> None:
        if not self.groups:
            raise ValueError("caller must belong to at least one group")
        bad = [g for g in self.groups if not GROUP_NAME_RE.fullmatch(g)]
        if bad:
            raise ValueError(f"invalid group names: {bad}")
