from __future__ import annotations

import logging
from typing import List, Optional

import torch
from sentence_transformers import SentenceTransformer

from .base_embedding_engine import BaseEmbeddingEngine

from pathlib import Path

LOGGER = logging.getLogger(__name__)


class BgeEmbeddingEngine(BaseEmbeddingEngine):
    name = "bge_m3"

    def __init__(self, model_name_or_path: Optional[str] = None) -> None:
        if model_name_or_path is None:
            # Check local path first
            # The structure from cache is models--BAAI--bge-m3/snapshots/[hash]
            root_dir = Path(__file__).resolve().parents[4]
            local_model_dir = root_dir / "models--BAAI--bge-m3" / "snapshots" / "5617a9f61b028005a4858fdac845db406aefb181"
            
            if local_model_dir.exists() and local_model_dir.is_dir():
                model_name_or_path = str(local_model_dir)
            else:
                model_name_or_path = "BAAI/bge-m3"

        device = "cuda" if torch.cuda.is_available() else "cpu"
        LOGGER.info(f"Loading {model_name_or_path} on {device}...")
        self.model = SentenceTransformer(model_name_or_path, device=device)
        LOGGER.info("BGE-M3 loaded successfully.")

    def embed_texts(self, texts: List[str]) -> Optional[List[List[float]]]:
        if not texts:
            return []
        
        # BGE-M3 outputs normalized embeddings out of the box when using encode
        # We manually pass normalize_embeddings=True for standard cosine similarity
        vectors = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        
        # vectors is typically a NumPy array or PyTorch tensor; convert to list of floats
        return [vec.tolist() for vec in vectors]