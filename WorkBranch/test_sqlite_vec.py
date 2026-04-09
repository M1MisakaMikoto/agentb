import os
import uuid
from pathlib import Path
from rag.service.ingestion.embedding_engine.bge_embedding_engine import BgeEmbeddingEngine
from rag.DAO.sqlite_vec_dao import SqliteVecDAO

def test():
    print("Testing BgeEmbeddingEngine...")
    engine = BgeEmbeddingEngine()
    vecs = engine.embed_texts(["这是一个测试"])
    assert len(vecs) == 1
    assert len(vecs[0]) == 1024
    print("BGE-M3 test pass.")
    
    print("Testing SqliteVecDAO...")
    db_path = Path("rag/file_meta.sqlite3")
    dao = SqliteVecDAO(db_path)
    
    test_chunk_id = f"test_chunk_{uuid.uuid4().hex[:8]}"
    test_doc_id = f"test_doc_{uuid.uuid4().hex[:8]}"
    
    chunks = [
        {
            "chunk_id": test_chunk_id,
            "text": "这是一个很好的测试文本",
            "embedding": vecs[0],
            "metadata": {"test": "yes"}
        }
    ]
    
    print("Adding chunk...")
    dao.add_chunks_batch(chunks, kb_id=99, document_id=test_doc_id)
    
    print("Searching chunk...")
    search_res = dao.search(vecs[0], kb_id=99, top_k=2)
    assert len(search_res["ids"][0]) > 0
    assert search_res["ids"][0][0] == test_chunk_id
    assert search_res["metadatas"][0][0]["test"] == "yes"
    print("Search test pass.")
    
    print("Deleting chunk...")
    rows_deleted = dao.delete_doc(test_doc_id)
    assert rows_deleted == 1
    print("Delete test pass.")

if __name__ == "__main__":
    test()