from __future__ import annotations

from fastapi import HTTPException, Request

from singleton import get_conversation_dao
from service.runtime import get_runtime_state


def _request_user_id(request: Request) -> int:
    user = getattr(request.state, "user", None)
    if not user or "id" not in user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return int(user["id"])


def _verify_affinity(request: Request, session_id: int | str) -> None:
    affinity_key = getattr(request.state, "affinity_key", None)
    if affinity_key is None:
        return
    if str(affinity_key) != str(session_id):
        raise HTTPException(
            status_code=409,
            detail="Affinity key does not match the requested session",
        )


async def require_owned_session(
    request: Request, session_id: int, *, claim_owner: bool = False
):
    session = await get_conversation_dao().get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    if int(session.user_id) != _request_user_id(request):
        raise HTTPException(status_code=403, detail="无权访问该会话")
    _verify_affinity(request, session_id)
    if claim_owner:
        runtime = get_runtime_state()
        claim = await runtime.claim_session(session_id)
        if claim.acquired:
            await get_conversation_dao().fail_stale_running_conversations(
                session_id, runtime.instance_id
            )
    return session


async def require_owned_conversation(
    request: Request, conversation_id: str, *, claim_owner: bool = False
):
    dao = get_conversation_dao()
    conversation = await dao.get_conversation_by_id(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")
    await require_owned_session(
        request, conversation.session_id, claim_owner=claim_owner
    )
    return conversation
