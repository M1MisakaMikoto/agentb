from __future__ import annotations

from pydantic import BaseModel


class KnowledgeBaseMutationVO(BaseModel):
    ok: bool
    id: int
