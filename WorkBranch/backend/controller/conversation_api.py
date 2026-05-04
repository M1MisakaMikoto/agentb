import asyncio
import json
import time
import traceback
from typing import Any, AsyncGenerator, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel

from controller.VO.result import Result
from core.logging import bind_ctx, get_ctx
from singleton import get_logging_runtime, get_message_queue, get_conversation_service
from service.session_service.mq import MessageQueue
from service.session_service.canonical import SegmentType, Message
from raw_streaming_response import RawStreamingResponse
from service.session_service.message_content import MessageContentError, normalize_user_content

router = APIRouter(prefix="/session/conversations", tags=["conversations"])
STREAM_MAX_TIMEOUT_TICKS = 300


class SendConversationMessageBody(BaseModel):
    message: str = ""
    message_parts: Optional[list[dict[str, Any]]] = None
    enable_context: bool = False


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str) -> Result:
    service = get_conversation_service()
    conversation = await service.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")
    return Result.success(data=conversation)


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str) -> Result:
    service = get_conversation_service()
    await service.delete_conversation(conversation_id)
    return Result.success()


@router.post("/{conversation_id}/cancel")
async def cancel_conversation(conversation_id: str) -> Result:
    service = get_conversation_service()
    await service.cancel_conversation(conversation_id)
    return Result.success()


@router.delete("/{conversation_id}/cascade")
async def cascade_delete_conversation(conversation_id: str) -> Result:
    """删除该对话以及之后的所有对话（回退功能）"""
    service = get_conversation_service()
    deleted_count = await service.delete_conversations_after(conversation_id)
    return Result.success(data={
        "deleted_count": deleted_count,
        "conversation_id": conversation_id,
    })


@router.post("/{conversation_id}/messages")
async def prepare_conversation_message(
    conversation_id: str,
    body: SendConversationMessageBody,
) -> Result:
    """准备消息 - 更新用户消息内容，返回消息ID，不执行 Agent"""
    service = get_conversation_service()
    conversation = await service.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")

    if conversation.get("state") == "running":
        raise HTTPException(status_code=400, detail="对话正在运行中")

    try:
        raw_message = body.message_parts if body.message_parts is not None else body.message
        normalized_parts = normalize_user_content(raw_message)
    except MessageContentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = await service.prepare_message(conversation_id, normalized_parts)
    return Result.success(data=result)


@router.get("/{conversation_id}/stream")
async def stream_conversation_message(
    conversation_id: str,
    last_seq: int = 0,
) -> StreamingResponse:
    """流式发送消息 - 支持断点续传
    
    Args:
        conversation_id: 对话ID
        last_seq: 上次接收的最后消息序号，用于断点续传
    """
    service = get_conversation_service()
    mq = get_message_queue()
    
    conversation = await service.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")

    if conversation.get("state") == "pending" and last_seq > 0:
        raise HTTPException(status_code=400, detail="对话尚未开始，无法断点续传")

    logger = get_logging_runtime().get_logger("api")
    request_ctx = get_ctx()
    request_ctx["conversation_id"] = conversation_id
    request_ctx["workspace_id"] = conversation.get("workspace_id") or request_ctx.get("workspace_id")

    stream_state = mq.get_stream_state(conversation_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        
        stream_start = time.perf_counter()
        first_chunk_logged = False
        done_received = False
        timeout_counter = 0
        max_timeout = STREAM_MAX_TIMEOUT_TICKS

        subscriber = None

        with bind_ctx(**request_ctx):
            logger.info(
                event="stream.started",
                msg="conversation stream started",
                extra={"conversation_id": conversation_id, "last_seq": last_seq},
            )

            try:
                if stream_state["is_completed"]:
                    messages_after = mq.get_messages_after(conversation_id, last_seq)
                    if messages_after:
                        for msg in messages_after:
                            yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                    else:
                        yield f"data: {json.dumps({'type': 'stream_completed', 'conversation_id': conversation_id, 'last_seq': last_seq, 'message': '对话已完成，请调用历史API获取完整数据'}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                    logger.info(
                        event="stream.completed_from_history",
                        msg="stream completed from history",
                        extra={"conversation_id": conversation_id, "last_seq": last_seq},
                    )
                    return

                await mq.start_consumer()
                subscriber = mq.subscribe(conversation_id, last_seq=last_seq)
                
                print(f"[DEBUG] stream_state: {stream_state}, last_seq: {last_seq}, state: {conversation.get('state')}")

                if last_seq == 0 and conversation.get("state") != "running":
                    print(f"[DEBUG] Creating send_message task for conversation {conversation_id}, state={conversation.get('state')}")
                    logger.info(
                        event="send_message_task_creating",
                        msg="creating send_message task",
                        extra={"conversation_id": conversation_id, "state": conversation.get("state")},
                    )
                    task = asyncio.create_task(service.send_message(conversation_id))
                    def task_callback(t):
                        try:
                            exc = t.exception()
                            if exc:
                                logger.error(
                                    event="send_message_task_failed",
                                    msg=f"send_message task failed: {exc}",
                                    extra={"conversation_id": conversation_id}
                                )
                            else:
                                logger.info(
                                    event="send_message_task_completed",
                                    msg="send_message task completed",
                                    extra={"conversation_id": conversation_id}
                                )
                        except asyncio.CancelledError:
                            logger.warning(
                                event="send_message_task_cancelled",
                                msg="send_message task was cancelled",
                                extra={"conversation_id": conversation_id}
                            )
                    task.add_done_callback(task_callback)

                print(f"[STREAM-DEBUG] Starting main loop, done_received={done_received}, timeout_counter={timeout_counter}")
                
                while not done_received and timeout_counter < max_timeout:
                    try:
                        print(f"[STREAM-DEBUG] Waiting for message (timeout=5s, iteration #{timeout_counter+1})...")
                        
                        message, seq = await asyncio.wait_for(
                            subscriber.get(),
                            timeout=5.0,
                        )
                        
                        print(f"[STREAM-DEBUG] ✓ Got message: type={message.type}, seq={seq}")

                        event_data = message.to_dict()
                        event_data["seq"] = seq

                        if not first_chunk_logged:
                            logger.info(
                                event="stream.first_chunk",
                                msg="conversation stream first chunk sent",
                                extra={
                                    "conversation_id": conversation_id,
                                    "latency_ms": round((time.perf_counter() - stream_start) * 1000),
                                },
                            )
                            first_chunk_logged = True

                        yield f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"
                        print(f"[STREAM-DEBUG] ✓ Yielded message to client")

                        if message.type == SegmentType.DONE:
                            done_received = True
                            logger.info(
                                event="stream.completed",
                                msg="conversation stream completed",
                                extra={
                                    "conversation_id": conversation_id,
                                    "latency_ms": round((time.perf_counter() - stream_start) * 1000),
                                },
                            )

                        timeout_counter = 0

                    except asyncio.TimeoutError:
                        timeout_counter += 1
                        print(f"[STREAM-DEBUG] ✗ Timeout #{timeout_counter}, sending heartbeat")
                        yield ": heartbeat\n\n"

                        current = await service.get_conversation(conversation_id)
                        if not current:
                            continue

                        request_ctx["workspace_id"] = current.get("workspace_id") or request_ctx.get("workspace_id")
                        state = current.get("state")
                        if state == "completed":
                            continue
                        elif state == "failed":
                            done_received = True
                            error_message = current.get("error") or state
                            logger.error(
                                event="stream.failed",
                                msg="conversation stream failed from state",
                                extra={
                                    "conversation_id": conversation_id,
                                    "reason": "conversation_failed",
                                    "latency_ms": round((time.perf_counter() - stream_start) * 1000),
                                    "conversation_error": error_message,
                                },
                            )
                            yield f"data: {json.dumps({'type': 'error', 'content': error_message}, ensure_ascii=False)}\n\n"
                        elif state == "cancelled":
                            done_received = True
                            logger.error(
                                event="stream.failed",
                                msg="conversation stream cancelled from state",
                                extra={
                                    "conversation_id": conversation_id,
                                    "reason": "conversation_cancelled",
                                    "latency_ms": round((time.perf_counter() - stream_start) * 1000),
                                },
                            )
                            yield f"data: {json.dumps({'type': 'error', 'content': state}, ensure_ascii=False)}\n\n"

                if not done_received:
                    logger.error(
                        event="stream.failed",
                        msg="conversation stream timed out",
                        extra={
                            "conversation_id": conversation_id,
                            "reason": "timeout",
                            "latency_ms": round((time.perf_counter() - stream_start) * 1000),
                        },
                    )
                    yield f"data: {json.dumps({'type': 'error', 'content': 'Timeout'}, ensure_ascii=False)}\n\n"

            except Exception as e:
                logger.error(
                    event="stream.failed",
                    msg="conversation stream raised exception",
                    extra={
                        "conversation_id": conversation_id,
                        "reason": "exception",
                        "latency_ms": round((time.perf_counter() - stream_start) * 1000),
                    },
                    exception="".join(traceback.format_exception(type(e), e, e.__traceback__)),
                )
                yield f"data: {json.dumps({'type': 'error', 'content': str(e)}, ensure_ascii=False)}\n\n"
            finally:
                if subscriber is not None:
                    mq.unsubscribe(conversation_id, subscriber)

    return RawStreamingResponse(
        event_generator(),
        status_code=200,
        headers={
            "X-Request-Id": request_ctx.get("request_id") or "",
        },
    )
