from __future__ import annotations

import os
import re
from typing import List, Set

from .BaseRerankStrategy import BaseRerankStrategy
from .RetrievalCandidate import RetrievalCandidate

try:
    import jieba
    jieba.initialize()
    _JIEBA_AVAILABLE = True
except ImportError:
    _JIEBA_AVAILABLE = False

_DEFAULT_BOOST_KEYWORDS = {
    "安防", "井盖", "密码检�?, "密码井盖",
    "安全", "监控", "预警", "检�?, "规范",
    "标准", "技术规�?, "施工", "养护", "设计",
}


def _load_boost_keywords() -> Set[str]:
    env_val = os.environ.get("RAG_BOOST_KEYWORDS", "")
    if not env_val:
        return _DEFAULT_BOOST_KEYWORDS
    return {k.strip() for k in env_val.split(",") if k.strip()}


def _load_boost_weight() -> float:
    env_val = os.environ.get("RAG_BOOST_WEIGHT", "")
    if not env_val:
        return 0.3
    try:
        return float(env_val)
    except ValueError:
        return 0.3


class TitleBoostRerankStrategy(BaseRerankStrategy):
    BOOST_WEIGHT = 0.3
    MIN_MATCH_TOKENS = 1

    def __init__(
        self,
        boost_keywords: Set[str] | None = None,
        boost_weight: float | None = None,
        min_match_tokens: int = 1,
    ) -> None:
        self.boost_keywords = boost_keywords or _load_boost_keywords()
        self.boost_weight = boost_weight if boost_weight is not None else _load_boost_weight()
        self.min_match_tokens = min_match_tokens

    @property
    def name(self) -> str:
        return "title_boost_v1"

    @staticmethod
    def _tokenize(text: str) -> Set[str]:
        if not text:
            return set()
        lowered = text.lower()
        chars = re.findall(r"[\u4e00-\u9fff]+|[a-z0-9_]+", lowered)
        tokens: Set[str] = set()
        for chunk in chars:
            if re.search(r"[\u4e00-\u9fff]", chunk):
                if _JIEBA_AVAILABLE:
                    tokens.update(jieba.cut(chunk, cut_all=False))
                else:
                    tokens.update(chunk[i:i+2] for i in range(len(chunk) - 1))
            else:
                tokens.add(chunk)
        return tokens

    def _compute_title_boost(self, query: str, doc_title: str) -> float:
        q_tokens = self._tokenize(query)
        d_tokens = self._tokenize(doc_title)
        if not q_tokens or not d_tokens:
            return 0.0
        matched = q_tokens.intersection(d_tokens)
        extra_keywords_matched = matched.intersection(self.boost_keywords)
        if len(extra_keywords_matched) < self.min_match_tokens:
            return 0.0
        return self.boost_weight * len(extra_keywords_matched)

    def rank(self, candidates: List[RetrievalCandidate], top_k: int) -> List[RetrievalCandidate]:
        if top_k <= 0 or not candidates:
            return []

        boosted: List[RetrievalCandidate] = []
        for row in candidates:
            doc_title = row.hit.doc_title or ""
            boost = self._compute_title_boost(row.metadata.get("_query", ""), doc_title)
            boosted_row = RetrievalCandidate(
                score=row.score + boost,
                recall_index=row.recall_index,
                hit=row.hit,
                metadata=row.metadata,
            )
            boosted.append(boosted_row)

        boosted.sort(key=lambda row: (-row.score, row.recall_index))
        return boosted[:top_k]
