from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from singleton import get_session_service, get_session_history
from service.session_service.session import SessionService
from service.user_service.session_history import SessionHistory
from controller.VO.result import Result

router = APIRouter(prefix="/session", tags=["session"])


class CreateConversationBody(BaseModel):
    workspace_id: Optional[str] = None
    parent_conversation_id: Optional[str] = None


class ConversationPositionItem(BaseModel):
    conversation_id: str
    x: float
    y: float


class UpdateConversationPositionsBody(BaseModel):
    positions: list[ConversationPositionItem]


class CreateSessionBody(BaseModel):
    title: str = "新会话"


@router.get("/sessions")
async def list_sessions(
    request: Request,
    service: SessionHistory = None,
) -> Result:
    user = request.state.user
    service = get_session_history()
    sessions = await service.list_sessions_async(user["id"])
    return Result.success(data=[
        {
            "id": s.id,
            "title": s.title,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
        }
        for s in sessions
    ])


@router.post("/sessions")
async def create_session(
    request: Request,
    body: CreateSessionBody = None,
) -> Result:
    user = request.state.user
    service = get_session_history()
    if body is None:
        body = CreateSessionBody()
    session = await service.create_session_async(user["id"], body.title)
    return Result.success(data={
        "id": session.id,
        "title": session.title,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    })


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: int,
    service: SessionHistory = None,
) -> Result:
    service = get_session_history()
    session = await service.get_session_async(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return Result.success(data={
        "id": session.id,
        "user_id": session.user_id,
        "title": session.title,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    })


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: int,
    service: SessionHistory = None,
) -> Result:
    service = get_session_history()
    await service.delete_session_async(session_id)
    return Result.success()


@router.get("/sessions/{session_id}/conversations")
async def list_session_conversations(
    session_id: int,
    service: SessionService = None,
) -> Result:
    service = get_session_service()
    session = await service._dao.get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return Result.success(data=await service.list_conversation_summaries(session_id))


@router.put("/sessions/{session_id}/conversation-positions")
async def update_conversation_positions(
    session_id: int,
    body: UpdateConversationPositionsBody,
    service: SessionService = None,
) -> Result:
    service = get_session_service()
    session = await service._dao.get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        await service.update_conversation_positions(
            session_id,
            [item.model_dump() for item in body.positions],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return Result.success(data={"updated": len(body.positions)})


@router.post("/sessions/{session_id}/conversations")
async def create_conversation(
    session_id: int,
    body: CreateConversationBody,
    service: SessionService = None,
) -> Result:
    service = get_session_service()
    session = await service._dao.get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        result = await service.create_conversation(
            session_id=session_id,
            workspace_id=body.workspace_id,
            parent_conversation_id=body.parent_conversation_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return Result.success(data=result)
