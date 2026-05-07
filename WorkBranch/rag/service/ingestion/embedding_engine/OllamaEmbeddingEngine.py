from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .base_embedding_engine import BaseEmbeddingEngine


class OllamaEmbeddingEngine(BaseEmbeddingEngine):
    name = "ollama_bge_m3"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "bge-m3:latest",
        max_workers: int = 4,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_workers = max(1, int(max_workers))

    def _embed_one(self, text: str) -> List[float]:
        payload = {"model": self.model, "prompt": text}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/embeddings",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=120) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama embedding request failed: HTTP {exc.code}, detail={detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Ollama embedding request failed: {exc}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Ollama embedding response is not valid JSON: {raw[:200]}") from exc

        vector = data.get("embedding")
        if not isinstance(vector, list):
            raise RuntimeError(f"Ollama embedding response missing 'embedding': {data}")
        return [float(v) for v in vector]

    def embed_texts(self, texts: List[str]) -> Optional[List[List[float]]]:
        if not texts:
            return []
        if len(texts) == 1:
            return [self._embed_one(texts[0])]

        vectors: List[Optional[List[float]]] = [None] * len(texts)
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(texts))) as executor:
            future_to_index = {executor.submit(self._embed_one, text): idx for idx, text in enumerate(texts)}
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                vectors[idx] = future.result()

        if any(v is None for v in vectors):
            raise RuntimeError("embedding generation incomplete")
        return [v for v in vectors if v is not None]
