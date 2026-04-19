from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from singleton import get_conversation_dao, get_conversation_service, get_session_history
from controller.VO.result import Result
from service.session_service.message_content import MessageContentError, normalize_user_content

router = APIRouter(prefix="/session", tags=["session"])


class CreateSessionBody(BaseModel):
    title: str = "新会话"


class CreateConversationBody(BaseModel):
    user_content: str = ""
    user_content_parts: Optional[list[dict[str, Any]]] = None


@router.get("/sessions")
async def list_sessions(request: Request) -> Result:
    user = request.state.user
    dao = get_conversation_dao()
    sessions = await dao.list_sessions_by_user(user["id"])
    return Result.success(data=[
        {
            "id": s.id,
            "title": s.title,
            "workspace_id": s.workspace_id,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
        }
        for s in sessions
    ])


@router.post("/sessions")
async def create_session(request: Request, body: CreateSessionBody = None) -> Result:
    user = request.state.user
    session_history = get_session_history()
    if body is None:
        body = CreateSessionBody()
    session = await session_history.create_session_async(user["id"], body.title)
    return Result.success(data={
        "id": session.id,
        "title": session.title,
        "workspace_id": session.workspace_id,
    })


@router.post("/sessions/{session_id}/title:generate")
async def generate_session_title(session_id: int, request: Request) -> Result:
    user = request.state.user
    session_history = get_session_history()

    try:
        session = await session_history.generate_session_title_async(session_id, user["id"])
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return Result.success(data={
        "session_id": session.id,
        "title": session.title,
    })


@router.get("/sessions/{session_id}")
async def get_session(session_id: int) -> Result:
    dao = get_conversation_dao()
    session = await dao.get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return Result.success(data={
        "id": session.id,
        "user_id": session.user_id,
        "title": session.title,
        "workspace_id": session.workspace_id,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    })


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: int) -> Result:
    dao = get_conversation_dao()
    await dao.delete_session(session_id)
    return Result.success()


@router.get("/sessions/{session_id}/conversations")
async def list_session_conversations(session_id: int) -> Result:
    service = get_conversation_service()
    conversations = await service.list_conversations(session_id)
    return Result.success(data=conversations)


@router.post("/sessions/{session_id}/conversations")
async def create_conversation(session_id: int, body: CreateConversationBody) -> Result:
    dao = get_conversation_dao()
    session = await dao.get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    service = get_conversation_service()
    try:
        raw_content = body.user_content_parts if body.user_content_parts is not None else body.user_content
        normalized_parts = normalize_user_content(raw_content)
        conversation_id = await service.create_conversation(
            session_id=session_id,
            user_content=normalized_parts,
        )
    except MessageContentError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        if "already has a running conversation" in str(e):
            raise HTTPException(status_code=409, detail="当前会话已有正在执行的对话，无法创建新对话") from e
        raise
    return Result.success(data={
        "conversation_id": conversation_id,
        "session_id": session_id,
    })
