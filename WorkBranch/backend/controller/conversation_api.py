import asyncio
import json
import os
import time
import traceback
from datetime import datetime
from pathlib import Path
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

STREAM_LOG_DIR = Path(__file__).resolve().parents[1] / "logs" / "stream_traces"
STREAM_LOG_ENABLED = os.environ.get("STREAM_TRACE_LOG", "true").lower() in ("true", "1", "yes")


class StreamTraceLogger:
    """流式数据追踪日志器 - 记录所有后端发给前端的SSE事件"""
    
    _instances: dict[str, "StreamTraceLogger"] = {}
    
    def __init__(self, conversation_id: str):
        self.conversation_id = conversation_id
        self.event_count = 0
        self.start_time = None
        self.log_file = None
        
        if STREAM_LOG_ENABLED:
            try:
                STREAM_LOG_DIR.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                log_filename = f"stream_trace_{conversation_id}_{timestamp}.log"
                self.log_file = open(STREAM_LOG_DIR / log_filename, "w", encoding="utf-8")
                self.start_time = time.perf_counter()
                
                header = "=" * 80 + "\n"
                header += f"Stream Trace Log\n"
                header += f"Conversation ID: {conversation_id}\n"
                header += f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')}\n"
                header += "=" * 80 + "\n\n"
                self._write(header)
            except Exception as e:
                print(f"[StreamLogger] ⚠ Failed to init logger: {e}")
    
    @classmethod
    def get(cls, conversation_id: str) -> "StreamTraceLogger":
        if conversation_id not in cls._instances:
            cls._instances[conversation_id] = cls(conversation_id)
        return cls._instances[conversation_id]
    
    def log(self, event_data: dict, seq: int):
        if not STREAM_LOG_ENABLED or not self.log_file:
            return
        
        self.event_count += 1
        event_type = event_data.get("type", "unknown")
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        
        line = f"[{timestamp}] [SEQ:{seq:04d}] [{event_type}]\n"
        self._write(line)
        
        json_str = json.dumps(event_data, ensure_ascii=False, indent=2)
        self._write(json_str + "\n\n")
        
        print(f"[STREAM-TRACE] ✓ SEQ:{seq:04d} | type={event_type} | events={self.event_count}")
    
    def log_heartbeat(self, timeout_counter: int):
        if not STREAM_LOG_ENABLED or not self.log_file:
            return
        
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{timestamp}] [HEARTBEAT] timeout=#{timeout_counter}\n"
        self._write(line)
    
    def close(self):
        if self.log_file and not self.log_file.closed:
            duration_ms = round((time.perf_counter() - self.start_time) * 1000) if self.start_time else 0
            
            footer = "\n" + "=" * 80 + "\n"
            footer += f"Ended: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')}\n"
            footer += f"Total Events: {self.event_count} | Duration: {duration_ms:.3f}ms\n"
            footer += "=" * 80 + "\n"
            
            try:
                self._write(footer)
                self.log_file.close()
                print(f"[STREAM-TRACE] ✅ Log closed: {self.event_count} events, {duration_ms:.0f}ms")
            except Exception as e:
                print(f"[STREAM-TRACE] ⚠ Error closing log: {e}")
        
        self._instances.pop(self.conversation_id, None)
    
    def _write(self, content: str):
        try:
            if self.log_file and not self.log_file.closed:
                self.log_file.write(content)
                self.log_file.flush()
        except Exception as e:
            print(f"[StreamLogger] Write error: {e}")


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
    mode: str = "interactive",  # 模式: interactive(完整流式) | silent(仅heartbeat+done)
) -> StreamingResponse:
    """流式发送消息 - 支持交互式/静默双模式
    
    Args:
        conversation_id: 对话ID
        last_seq: 上次接收的最后消息序号，用于断点续传
        mode: 运行模式，interactive为默认交互式模式，silent为静默模式（过滤流式中间结果）
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

        # 验证mode参数
        if mode not in ("interactive", "silent"):
            yield f"data: {json.dumps({'type': 'error', 'message': f'Invalid mode: {mode}. Must be interactive or silent'})}\n\n"
            return

        stream_start = time.perf_counter()
        first_chunk_logged = False
        done_received = False
        timeout_counter = 0
        max_timeout = STREAM_MAX_TIMEOUT_TICKS
        subscriber = None

        stream_logger = StreamTraceLogger.get(conversation_id)

        with bind_ctx(**request_ctx):
            logger.info(
                event="stream.started",
                msg=f"conversation stream started (mode={mode})",
                extra={"conversation_id": conversation_id, "last_seq": last_seq, "mode": mode},
            )

            try:
                if stream_state["is_completed"]:
                    messages_after = mq.get_messages_after(conversation_id, last_seq)
                    if messages_after:
                        for idx, msg in enumerate(messages_after):
                            event_data = msg.to_dict()
                            event_data["seq"] = last_seq + idx + 1
                            stream_logger.log(event_data, event_data["seq"])
                            yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                    elif last_seq == 0:
                        # 首次请求且 Agent 已完成，从数据库获取结果
                        from singleton import get_conversation_dao
                        dao = get_conversation_dao()
                        persisted_conv = await dao.get_conversation_by_id(conversation_id)
                        if persisted_conv and persisted_conv.assistant_content:
                            # 构造 chat_start + chat_delta + chat_end 事件
                            chat_start = {'type': 'chat_start', 'conversation_id': conversation_id, 'message_id': persisted_conv.id, 'content': ''}
                            stream_logger.log(chat_start, 1)
                            yield f"data: {json.dumps(chat_start, ensure_ascii=False)}\n\n"

                            chat_delta = {'type': 'chat_delta', 'conversation_id': conversation_id, 'message_id': persisted_conv.id, 'content': persisted_conv.assistant_content}
                            stream_logger.log(chat_delta, 2)
                            yield f"data: {json.dumps(chat_delta, ensure_ascii=False)}\n\n"

                            chat_end = {'type': 'chat_end', 'conversation_id': conversation_id, 'message_id': persisted_conv.id, 'content': ''}
                            stream_logger.log(chat_end, 3)
                            yield f"data: {json.dumps(chat_end, ensure_ascii=False)}\n\n"
                        else:
                            done_event = {'type': 'stream_completed', 'conversation_id': conversation_id, 'last_seq': last_seq, 'message': '对话已完成，请调用历史API获取完整数据'}
                            stream_logger.log(done_event, 0)
                            yield f"data: {json.dumps(done_event, ensure_ascii=False)}\n\n"
                    else:
                        done_event = {'type': 'stream_completed', 'conversation_id': conversation_id, 'last_seq': last_seq, 'message': '对话已完成，请调用历史API获取完整数据'}
                        stream_logger.log(done_event, 0)
                        yield f"data: {json.dumps(done_event, ensure_ascii=False)}\n\n"
                    end_event = {'type': 'done'}
                    stream_logger.log(end_event, 0)
                    yield f"data: {json.dumps(end_event, ensure_ascii=False)}\n\n"
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
                        msg=f"creating send_message task (mode={mode})",
                        extra={"conversation_id": conversation_id, "state": conversation.get("state"), "mode": mode},
                    )
                    # 根据模式传递silent_mode参数
                    is_silent = (mode == "silent")
                    task = asyncio.create_task(service.send_message(conversation_id, silent_mode=is_silent))
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
                        print(f"[STREAM-DEBUG] Waiting for message (timeout=10s, iteration #{timeout_counter+1})...")

                        message, seq = await asyncio.wait_for(
                            subscriber.get(),
                            timeout=10.0,
                        )
                        
                        print(f"[STREAM-DEBUG] ✓ Got message: type={message.type}, seq={seq}")

                        event_data = message.to_dict()
                        event_data["seq"] = seq

                        stream_logger.log(event_data, seq)

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

                        # [DEBUG] 打印实际yield的JSON
                        event_type = event_data.get("type", "unknown")
                        yield_json = json.dumps(event_data, ensure_ascii=False)
                        print(f"[STREAM-YIELD] type={event_type}, yield_size={len(yield_json)}")
                        yield f"data: {yield_json}\n\n"
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
                        
                        stream_logger.log_heartbeat(timeout_counter)
                        
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
                            error_event = {'type': 'error', 'content': error_message}
                            stream_logger.log(error_event, -1)
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
                            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"
                        elif state == "cancelled":
                            done_received = True
                            cancel_event = {'type': 'error', 'content': state}
                            stream_logger.log(cancel_event, -1)
                            logger.error(
                                event="stream.failed",
                                msg="conversation stream cancelled from state",
                                extra={
                                    "conversation_id": conversation_id,
                                    "reason": "conversation_cancelled",
                                    "latency_ms": round((time.perf_counter() - stream_start) * 1000),
                                },
                            )
                            yield f"data: {json.dumps(cancel_event, ensure_ascii=False)}\n\n"

                if not done_received:
                    timeout_event = {'type': 'error', 'content': 'Timeout'}
                    stream_logger.log(timeout_event, -1)
                    logger.error(
                        event="stream.failed",
                        msg="conversation stream timed out",
                        extra={
                            "conversation_id": conversation_id,
                            "reason": "timeout",
                            "latency_ms": round((time.perf_counter() - stream_start) * 1000),
                        },
                    )
                    yield f"data: {json.dumps(timeout_event, ensure_ascii=False)}\n\n"

            except Exception as e:
                exception_event = {'type': 'error', 'content': str(e)}
                stream_logger.log(exception_event, -1)
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
                yield f"data: {json.dumps(exception_event, ensure_ascii=False)}\n\n"
            finally:
                if subscriber is not None:
                    mq.unsubscribe(conversation_id, subscriber)
                stream_logger.close()

    return RawStreamingResponse(
        event_generator(),
        status_code=200,
        headers={
            "X-Request-Id": request_ctx.get("request_id") or "",
        },
    )
