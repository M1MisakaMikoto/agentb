from __future__ import annotations

import unittest

from rag.DAO.sqlite_vec_dao import SqliteVecDAO
from rag.service.ingestion.embedding_engine.OllamaEmbeddingEngine import OllamaEmbeddingEngine


class _FakeOllamaEmbeddingEngine(OllamaEmbeddingEngine):
    def __init__(self) -> None:
        super().__init__(base_url="http://127.0.0.1:11434", model="fake", max_workers=4)

    def _embed_one(self, text: str) -> list[float]:
        return [float(len(text))]


class TestSqliteVecWhereFilterSql(unittest.TestCase):
    def test_build_where_filter_sql_with_and_and_in(self) -> None:
        dao = SqliteVecDAO.__new__(SqliteVecDAO)
        where_filter = {
            "$and": [
                {"document_id": {"$in": [1, 2, 3]}},
                {"source_type": {"$in": ["pdf", "txt"]}},
                {"tenant": "1"},
            ]
        }

        clauses, params = dao._build_where_filter_sql(where_filter)

        self.assertEqual(3, len(clauses))
        self.assertIn("c.document_id IN (?,?,?)", clauses)
        self.assertIn("json_extract(c.metadata, '$.source_type') IN (?,?)", clauses)
        self.assertIn("json_extract(c.metadata, '$.tenant') = ?", clauses)
        self.assertEqual(["1", "2", "3", "pdf", "txt", "1"], params)

    def test_build_where_filter_sql_plain(self) -> None:
        dao = SqliteVecDAO.__new__(SqliteVecDAO)
        where_filter = {
            "document_id": "42",
            "source_type": "md",
        }

        clauses, params = dao._build_where_filter_sql(where_filter)
        self.assertEqual(["c.document_id = ?", "json_extract(c.metadata, '$.source_type') = ?"], clauses)
        self.assertEqual(["42", "md"], params)


class TestOllamaEmbeddingConcurrency(unittest.TestCase):
    def test_embed_texts_keeps_input_order(self) -> None:
        engine = _FakeOllamaEmbeddingEngine()
        texts = ["a", "abcd", "abc"]
        vectors = engine.embed_texts(texts)
        self.assertEqual([[1.0], [4.0], [3.0]], vectors)


if __name__ == "__main__":
    unittest.main()
