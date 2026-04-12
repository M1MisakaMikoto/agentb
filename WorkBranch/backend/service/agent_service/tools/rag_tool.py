from typing import Optional

from .registry import ToolRegistry, ToolDefinition


def execute_rag_search(tool_args: dict) -> dict:
    """执行 rag_search 工具，直接调用 RAG service 层完成知识库语义检索。"""
    query = tool_args.get("query")
    if not query:
        return {"result": None, "error": "缺少 query 参数"}

    kb_ids_raw = tool_args.get("kb_ids")
    top_k: int = int(tool_args.get("top_k", 5))
    min_score: float = float(tool_args.get("min_score", 0.0))

    try:
        from rag.service.RAG_service import RAG_service
        from rag.tool_schema import RAGSearchRequest

        kb_ids: Optional[list] = None
        if kb_ids_raw:
            if isinstance(kb_ids_raw, list):
                kb_ids = [int(x) for x in kb_ids_raw]
            else:
                kb_ids = [int(kb_ids_raw)]

        request = RAGSearchRequest(
            query=query,
            kb_ids=kb_ids,
            top_k=top_k,
            min_score=min_score,
        )

        service = RAG_service()
        try:
            response = service.rag_search(request)
        finally:
            service.close()

        if response.error:
            return {"result": None, "error": response.error.message}

        chunks = response.chunks or []
        if not chunks:
            return {"result": "知识库中未找到相关内容。", "error": None}

        lines = [f"知识库检索结果（查询：{query}，命中 {len(chunks)} 条）：\n"]
        for i, chunk in enumerate(chunks, 1):
            source = getattr(chunk, "source", "") or ""
            score = getattr(chunk, "score", None)
            text = getattr(chunk, "text", "") or ""
            score_str = f"  相关度：{score:.4f}" if score is not None else ""
            lines.append(f"{i}. [{source}]{score_str}")
            truncated = text[:300] + "..." if len(text) > 300 else text
            lines.append(f"   {truncated}\n")

        return {"result": "\n".join(lines), "error": None}

    except ImportError as e:
        return {"result": None, "error": f"RAG 模块未加载：{e}"}
    except Exception as e:
        return {"result": None, "error": f"知识库检索失败：{e}"}


RAG_TOOLS = {"rag_search"}


def register_rag_tools() -> None:
    """注册 RAG 相关工具到 ToolRegistry。"""
    ToolRegistry.register(
        ToolDefinition(
            name="rag_search",
            description="在知识库中进行语义检索，返回与查询最相关的文档片段",
            params="query(必填), kb_ids(知识库ID列表，可选), top_k(返回条数，默认5), min_score(最低相关度，默认0.0)",
            category="rag",
            executor=execute_rag_search,
        )
    )
