from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from singleton import get_conversation_dao, get_conversation_service, get_session_history
from controller.VO.result import Result

router = APIRouter(prefix="/session", tags=["session"])


class CreateSessionBody(BaseModel):
    title: str = "新会话"


class CreateConversationBody(BaseModel):
    user_content: str


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
    conversation_id = await service.create_conversation(
        session_id=session_id,
        user_content=body.user_content,
    )
    return Result.success(data={
        "conversation_id": conversation_id,
        "session_id": session_id,
    })
