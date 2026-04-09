from __future__ import annotations

from typing import List, Optional

from openai import OpenAI

from .base_embedding_engine import BaseEmbeddingEngine


class OpenAIEmbeddingEngine(BaseEmbeddingEngine):
    name = "openai_embedding"

    def __init__(self, model: str = "text-embedding-3-small", client: Optional[OpenAI] = None) -> None:
        self.model = model
        self.client = client or OpenAI()

    def embed_texts(self, texts: List[str]) -> Optional[List[List[float]]]:
        if not texts:
            return []
        resp = self.client.embeddings.create(model=self.model, input=texts)
        return [row.embedding for row in resp.data]

