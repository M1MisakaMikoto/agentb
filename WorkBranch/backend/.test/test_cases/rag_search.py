#!/usr/bin/env python3
"""Distributed RAG search E2E scenario."""

from datetime import datetime
from typing import Dict, List, Tuple

from .base import (
    APIClient,
    Colors,
    TestResult,
    collect_stream_output,
    extract_response_text,
    print_dim,
    print_error,
    print_step,
    print_success,
    print_test_header,
    wait_for_conversation_state,
)


async def cleanup_test_knowledge_base(
    api: APIClient,
    kb_id: int,
    document_ids: List[int],
) -> List[str]:
    errors: List[str] = []
    for document_id in document_ids:
        deleted = await api.delete_rag_document(document_id)
        if not deleted.get("success") and deleted.get("code") != 404:
            errors.append(f"document {document_id}: {deleted.get('message')}")
    deleted_kb = await api.delete_rag_knowledge_base(kb_id)
    if not deleted_kb.get("success") and deleted_kb.get("code") != 404:
        errors.append(f"knowledge base {kb_id}: {deleted_kb.get('message')}")
    return errors


async def create_test_knowledge_base(
    api: APIClient,
    scenario_config: dict,
) -> Tuple[int, Dict, List[int]]:
    kb_name = f"test_kb_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    created = await api.create_rag_knowledge_base(
        kb_name,
        "Knowledge base for distributed AgentB E2E testing",
    )
    if not created.get("success") or not created.get("id"):
        raise RuntimeError(f"Knowledge base creation failed: {created}")

    kb_id = int(created["id"])
    document_ids: List[int] = []
    test_documents = scenario_config.get(
        "test_documents",
        [
            {
                "title": "product_manual",
                "category": "technical",
                "content": "AgentB product manual and operating guide.",
            },
            {
                "title": "sales_report_2024",
                "category": "business",
                "content": "The 2024 product sales report contains quarterly totals.",
            },
            {
                "title": "employee_training",
                "category": "training",
                "content": "Employee product training and support handbook.",
            },
        ],
    )
    expected = {"kb_id": kb_id, "documents": []}

    try:
        for index, document in enumerate(test_documents, start=1):
            title = str(document["title"])
            uploaded = await api.upload_rag_document(
                kb_id,
                f"e2e_{index}_{title}.txt",
                str(document.get("content", "")),
            )
            if not uploaded.get("success") or not uploaded.get("id"):
                raise RuntimeError(f"RAG document upload failed: {uploaded}")

            document_id = int(uploaded["id"])
            document_ids.append(document_id)
            job_id = (uploaded.get("ingest") or {}).get("job_id")
            if not job_id:
                raise RuntimeError(f"RAG upload returned no ingestion job: {uploaded}")
            job = await api.wait_for_rag_job(
                int(job_id),
                timeout=float(scenario_config.get("ingestion_timeout", 180.0)),
            )
            if not job.get("success"):
                raise RuntimeError(f"RAG ingestion failed: {job}")

            expected["documents"].append(
                {
                    "id": document_id,
                    "title": title,
                    "category": document.get("category", ""),
                }
            )
        return kb_id, expected, document_ids
    except Exception:
        await cleanup_test_knowledge_base(api, kb_id, document_ids)
        raise


async def run_rag_search_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True,
) -> TestResult:
    result = TestResult("rag_search", scenario_config)
    kb_id = 0
    document_ids: List[int] = []

    print_test_header(scenario_config.get("description", "RAG Search Test"))
    print_step(1, "Creating and ingesting test knowledge base...", Colors.CYAN)
    try:
        kb_id, expected, document_ids = await create_test_knowledge_base(
            api,
            scenario_config,
        )
        print_success(f"Knowledge base ready: {kb_id}")
        print_dim(f"Documents: {len(expected['documents'])}")
    except Exception as exc:
        print_error(f"Failed to prepare knowledge base: {exc}")
        result.errors.append(f"create_knowledge_base: {exc}")
        return result

    try:
        print_step(2, "Creating session...", Colors.CYAN)
        session_result = await api.create_session(title="RAG Search Test")
        if not session_result.get("success"):
            result.errors.append(f"create_session: {session_result.get('message')}")
            return result
        session_id = int(session_result["data"]["id"])
        result.session_id = session_id

        print_step(3, "Creating RAG search conversation...", Colors.CYAN)
        question = (
            f"Use the RAG search tool to search knowledge base {kb_id} "
            "for product information and summarize the matching documents."
        )
        conversation = await api.create_conversation(session_id, question)
        if not conversation.get("success"):
            result.errors.append(
                f"create_conversation: {conversation.get('message')}"
            )
            return result
        conversation_id = str(conversation["data"]["conversation_id"])
        result.conversation_id = conversation_id

        await wait_for_conversation_state(
            api,
            conversation_id,
            "processing",
            timeout=10.0,
        )
        print_step(4, "Streaming response...", Colors.CYAN)
        await collect_stream_output(
            api,
            conversation_id,
            result,
            verbose=verbose,
            timeout=float(scenario_config.get("query_timeout", 180.0)),
        )
        final = await wait_for_conversation_state(
            api,
            conversation_id,
            "completed",
            timeout=float(scenario_config.get("query_timeout", 180.0)),
        )
        result.response_text = extract_response_text(final)

        rag_tools = {"rag_search", "knowledge_search", "search_knowledge"}
        if not any(tool in rag_tools for tool in result.tool_calls):
            result.errors.append(
                f"RAG search tool was not called; tools={result.tool_calls}"
            )
        if not result.response_text:
            result.errors.append("No response text found")
        if not result.errors:
            print_success("RAG search completed through the deployed worker")
        return result
    finally:
        print_step(5, "Cleaning up RAG test data...", Colors.CYAN)
        cleanup_errors = await cleanup_test_knowledge_base(api, kb_id, document_ids)
        if cleanup_errors:
            result.errors.extend(f"cleanup: {error}" for error in cleanup_errors)
            print_error("; ".join(cleanup_errors))
        else:
            print_success("RAG test data removed")
