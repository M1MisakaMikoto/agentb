from __future__ import annotations

import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set
from uuid import uuid4

from rag.DAO.document_meta_dao import DocumentMetaDAO
from rag.DAO.RAG_DAO import RAG_DAO
from rag.DAO.base_dao import BaseRAGDAO
from rag.DAO.knowledge_base_dao import KnowledgeBaseDAO
from rag.service.ingestion.embedding_engine.OllamaEmbeddingEngine import OllamaEmbeddingEngine
from rag.service.rerank_strategy import (
    ChunkDocTwoStageRerankStrategy,
    ChunkScoreRerankStrategy,
    RetrievalCandidate,
    RerankStrategyRegistry,
    TitleBoostChunkDocRerankStrategy,
)
from rag.tool_schema import (
    RAGChunkHit,
    RAGSearchDebugTrace,
    RAGSearchError,
    RAGSearchFilters,
    RAGSearchRequest,
    RAGSearchResponse,
    ScoreType,
)


class RAG_service:
    """Service layer that maps tool schema <-> DAO standard interface."""

    def __init__(
        self,
        dao: Optional[BaseRAGDAO] = None,
        document_meta_dao: Optional[DocumentMetaDAO] = None,
    ) -> None:
        self.dao = dao or RAG_DAO()
        self.document_meta_dao = document_meta_dao or DocumentMetaDAO()
        self.embedding_engine = OllamaEmbeddingEngine(base_url="http://127.0.0.1:11434", model="bge-m3:latest")
        self.rerank_registry = RerankStrategyRegistry()
        self.rerank_registry.register(ChunkScoreRerankStrategy())
        self.rerank_registry.register(ChunkDocTwoStageRerankStrategy())
        self.rerank_registry.register(TitleBoostChunkDocRerankStrategy())
        self.default_rerank_strategy_name = "title_boost_chunk_doc_v1"

    @staticmethod
    def _normalize_distance(distance: float) -> float:
        if distance < 0:
            return 0.0
        return round(1.0 / (1.0 + distance), 6)

    @staticmethod
    def _to_doc_id(value: Any) -> int:
        try:
            return int(value)
        except Exception:
            return 0

    @staticmethod
    def _build_where(filters: Optional[RAGSearchFilters]) -> Optional[Dict[str, Any]]:
        if filters is None:
            return None

        clauses = []
        if filters.doc_ids:
            clauses.append({"document_id": {"$in": [str(d) for d in filters.doc_ids]}})
        if filters.source_types:
            clauses.append({"source_type": {"$in": filters.source_types}})
        if filters.metadata:
            for k, v in filters.metadata.items():
                clauses.append({k: v})

        if not clauses:
            return None
        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}

    @staticmethod
    def _rewrite_query_text(query: str) -> str:
        # Keep it deterministic and local (no extra model dependency).
        normalized = unicodedata.normalize("NFKC", query).strip()
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized

    @staticmethod
    def _tokenize_for_lexical_score(text: str) -> List[str]:
        if not text:
            return []
        lowered = text.lower()
        return re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", lowered)

    @classmethod
    def _lexical_score(cls, query: str, text: str) -> float:
        q_tokens = set(cls._tokenize_for_lexical_score(query))
        if not q_tokens:
            return 0.0
        t_tokens = set(cls._tokenize_for_lexical_score(text))
        if not t_tokens:
            return 0.0
        overlap = len(q_tokens.intersection(t_tokens))
        return round(overlap / max(len(q_tokens), 1), 6)

    @staticmethod
    def _in_collection_filter(metadata: Dict[str, Any], collection_ids: Sequence[int]) -> bool:
        if not collection_ids:
            return True

        wanted = {str(c) for c in collection_ids}
        primary = str(metadata.get("primary_category_id", "")).strip()
        if primary and primary in wanted:
            return True

        category_csv = str(metadata.get("category_ids", "")).strip()
        if not category_csv:
            return False
        cats = {c.strip() for c in category_csv.split(",") if c.strip()}
        return len(cats.intersection(wanted)) > 0

    def _load_live_category_map(self, doc_ids: Sequence[int]) -> Dict[int, Set[int]]:
        try:
            return self.document_meta_dao.get_doc_category_map(doc_ids)
        except Exception:
            return {}

    def _load_live_title_map(self, doc_ids: Sequence[int]) -> Dict[int, str]:
        try:
            return self.document_meta_dao.get_doc_title_map(doc_ids)
        except Exception:
            return {}

    def _load_live_category_and_title_map(
        self, doc_ids: Sequence[int]
    ) -> tuple[Dict[int, Set[int]], Dict[int, str]]:
        try:
            return self.document_meta_dao.get_doc_category_and_title_map(doc_ids)
        except Exception:
            return {}, {}

    @staticmethod
    def _compute_recall_k(top_k: int, mode: str, use_rerank: bool) -> int:
        if not use_rerank:
            return top_k
        if mode == "hybrid":
            return min(top_k * 6, 100)
        return min(top_k * 3, 100)

    def rag_search(self, req: RAGSearchRequest | Dict[str, Any]) -> RAGSearchResponse:
        started_at = time.time()
        trace_id = uuid4().hex

        try:
            request = req if isinstance(req, RAGSearchRequest) else RAGSearchRequest(**req)
        except Exception as exc:
            return RAGSearchResponse(
                ok=False,
                trace_id=trace_id,
                query=req.get("query", "") if isinstance(req, dict) else "",
                error=RAGSearchError(code="INVALID_INPUT", message=str(exc)),
                debug=RAGSearchDebugTrace(latency_ms=int((time.time() - started_at) * 1000)),
            )

        try:
            rewritten_query = self._rewrite_query_text(request.query) if request.rewrite_query else request.query
            recall_k = self._compute_recall_k(
                top_k=request.top_k,
                mode=request.mode.value,
                use_rerank=request.use_rerank,
            )

            # 知识库隔离：根据 kb_id 路由到对应的 Chroma collection
            collection_name = KnowledgeBaseDAO.get_collection_name(request.kb_id)
            where = self._build_where(request.filters)
            
            # 使用本地模型�?query 转换�?vector
            query_vector = self.embedding_engine.embed_texts([rewritten_query])[0]
            
            raw = self.dao.search(
                query_vector=query_vector,
                collection_name=collection_name,
                top_k=recall_k,
                where=where,
            )

            ids = (raw.get("ids") or [[]])[0]
            docs = (raw.get("documents") or [[]])[0]
            metadatas = (raw.get("metadatas") or [[]])[0]
            distances = (raw.get("distances") or [[]])[0]

            candidate_rows: List[RetrievalCandidate] = []
            for idx, chunk_id in enumerate(ids):
                text = docs[idx] if idx < len(docs) else ""
                metadata = metadatas[idx] if idx < len(metadatas) and metadatas[idx] else {}
                distance = distances[idx] if idx < len(distances) else 0.0
                vector_score = self._normalize_distance(float(distance))

                lexical_score = self._lexical_score(rewritten_query, text)
                if request.mode.value == "hybrid":
                    fused_score = round(0.7 * vector_score + 0.3 * lexical_score, 6)
                else:
                    fused_score = vector_score

                score = fused_score if request.use_rerank else vector_score
                threshold_score = vector_score
                if request.min_score is not None and threshold_score < request.min_score:
                    continue

                source = str(metadata.get("source", ""))
                source_type = str(metadata.get("source_type", "")).lower()
                if not source_type and source:
                    source_type = Path(source).suffix.replace(".", "").lower()

                doc_title = str(metadata.get("doc_title") or metadata.get("title") or source or "unknown")
                doc_id = self._to_doc_id(metadata.get("document_id"))

                candidate_rows.append(
                    RetrievalCandidate(
                        score=score,
                        recall_index=idx,
                        hit=RAGChunkHit(
                            chunk_id=str(chunk_id),
                            doc_id=doc_id,
                            doc_title=doc_title,
                            source=source,
                            source_type=source_type,
                            text=text,
                            score=score,
                            score_type=ScoreType.similarity,
                            rank=idx + 1,
                            metadata=metadata,
                        ),
                        metadata={**metadata, "_query": rewritten_query},
                    )
                )

            scored_rows: List[RetrievalCandidate] = []
            live_title_map: Dict[int, str] = {}
            all_doc_ids = list({row.hit.doc_id for row in candidate_rows if row.hit.doc_id > 0})

            if request.filters and request.filters.collection_ids:
                wanted = {int(c) for c in request.filters.collection_ids}
                # 一�?DB 往返同时取分类映射和标题（解决问题 6+7�?                live_cat_map, live_title_map = self._load_live_category_and_title_map(all_doc_ids)
                for row in candidate_rows:
                    if row.hit.doc_id > 0 and row.hit.doc_id in live_cat_map:
                        if live_cat_map[row.hit.doc_id].intersection(wanted):
                            scored_rows.append(row)
                        continue
                    # Fallback: if mapping query fails or missing doc row, use chunk snapshot.
                    if self._in_collection_filter(row.metadata, request.filters.collection_ids):
                        scored_rows.append(row)
            else:
                scored_rows.extend(candidate_rows)
                if all_doc_ids:
                    live_title_map = self._load_live_title_map(all_doc_ids)

            if request.use_rerank:
                strategy = self.rerank_registry.get(self.default_rerank_strategy_name)
                ranked_rows = strategy.rank(scored_rows, request.top_k)
            else:
                ranked_rows = sorted(scored_rows, key=lambda row: row.recall_index)[: request.top_k]

            items: List[RAGChunkHit] = []
            for rank, row in enumerate(ranked_rows, start=1):
                hit = row.hit
                if hit.doc_id > 0 and hit.doc_id in live_title_map:
                    hit.doc_title = live_title_map[hit.doc_id]
                hit.rank = rank
                items.append(hit)

            return RAGSearchResponse(
                ok=True,
                trace_id=trace_id,
                query=request.query,
                items=items,
                debug=RAGSearchDebugTrace(
                    rewritten_query=rewritten_query if request.rewrite_query else None,
                    recall_k=recall_k,
                    rerank_model=self.default_rerank_strategy_name if request.use_rerank else None,
                    latency_ms=int((time.time() - started_at) * 1000),
                ),
            ) 
        except Exception as exc:
            return RAGSearchResponse(
                ok=False,
                trace_id=trace_id,
                query=request.query,
                error=RAGSearchError(code="VECTORSTORE_ERROR", message=str(exc)),
                debug=RAGSearchDebugTrace(latency_ms=int((time.time() - started_at) * 1000)),
            )

    def tool_run(self, query: str) -> str:
        """Backward-compatible simple entry used by old callers."""
        result = self.rag_search({"query": query, "top_k": 3})
        if not result.ok:
            err = result.error
            if err is not None:
                return f"RAG search failed: {err.code} - {err.message}"
            return "RAG search failed: unknown error"
        if not result.items:
            return "No relevant context found."
        top = result.items[0]
        return f"[{top.source}] {top.text[:200]}"

    def close(self) -> None:
        self.dao.close()
