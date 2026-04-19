from typing import List, Dict, Any, Optional, Generator, Callable

import httpx
import time
import traceback
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_openai import ChatOpenAI

from core.logging import console
from service.session_service.message_content import (
    build_user_message,
    has_image_parts,
    normalize_chat_messages,
    parts_to_plain_text,
)


class LLMService:
    """LLM 服务：封装 LangChain OpenAI 调用"""

    _instance = None

    def __new__(cls, settings_service=None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, settings_service=None):
        if self._initialized:
            return

        self._settings = settings_service
        self._llm = None
        self._initialized = True

    def _get_llm(self) -> ChatOpenAI:
        """获取默认缓存 LLM 实例"""
        if self._llm is None:
            self._llm = self._build_llm()
        return self._llm

    def _build_llm(self, http_client: Any = None, http_async_client: Any = None) -> ChatOpenAI:
        """构造一个可选自定义 HTTP 客户端的 LLM 实例"""
        if self._settings is None:
            raise ValueError("Settings service not initialized")

        api_key = self._settings.get("llm:api_key")
        base_url = self._settings.get("llm:base_url")
        model = self._settings.get("llm:model")
        temperature = self._settings.get("llm:temperature")
        max_tokens = self._settings.get("llm:max_tokens")

        if not api_key:
            raise ValueError("LLM API key not configured. Please set llm:api_key in settings.")

        return ChatOpenAI(
            api_key=api_key,
            base_url=base_url,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=httpx.Timeout(120.0, connect=30.0),
            http_client=http_client,
            http_async_client=http_async_client,
        )

    def _log_llm_event(self, level: str, event: str, msg: str, extra: Optional[dict] = None, exception: str | None = None) -> None:
        from singleton import get_logging_runtime

        logger = get_logging_runtime().get_logger("agent")
        if level == "ERROR":
            logger.error(event=event, msg=msg, extra=extra, exception=exception)
        else:
            logger.info(event=event, msg=msg, extra=extra)

    def _build_llm_extra(self, operation: str, start_time: float, **kwargs) -> dict:
        extra = {
            "operation": operation,
            "provider": "openai_compatible",
            "model": None,
            "latency_ms": round((time.perf_counter() - start_time) * 1000),
        }
        try:
            extra["model"] = self._settings.get("llm:model") if self._settings is not None else None
        except KeyError:
            extra["model"] = None
        extra.update({k: v for k, v in kwargs.items() if v is not None})
        return extra

    def _extract_usage(self, result: Any) -> dict[str, int]:
        usage = getattr(result, "usage_metadata", None)
        if not isinstance(usage, dict):
            response_metadata = getattr(result, "response_metadata", None)
            if isinstance(response_metadata, dict):
                token_usage = response_metadata.get("token_usage")
                if isinstance(token_usage, dict):
                    usage = token_usage

        if not isinstance(usage, dict):
            return {}

        prompt_tokens = usage.get("input_tokens")
        if prompt_tokens is None:
            prompt_tokens = usage.get("prompt_tokens")

        completion_tokens = usage.get("output_tokens")
        if completion_tokens is None:
            completion_tokens = usage.get("completion_tokens")

        total_tokens = usage.get("total_tokens")
        if total_tokens is None and isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
            total_tokens = prompt_tokens + completion_tokens

        extracted = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
        return {k: v for k, v in extracted.items() if isinstance(v, int)}

    def _invoke_with_logging(self, operation: str, invoke_fn):
        start_time = time.perf_counter()
        self._log_llm_event(
            "INFO",
            "llm.call.started",
            "llm call started",
            extra=self._build_llm_extra(operation, start_time),
        )
        try:
            result = invoke_fn()
        except Exception as exc:
            self._log_llm_event(
                "ERROR",
                "llm.call.failed",
                "llm call failed",
                extra=self._build_llm_extra(operation, start_time, error=str(exc)),
                exception="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            )
            raise
        self._log_llm_event(
            "INFO",
            "llm.call.completed",
            "llm call completed",
            extra=self._build_llm_extra(operation, start_time, **self._extract_usage(result)),
        )
        return result

    def _stream_with_logging(self, operation: str, stream_fn) -> Generator[str, None, None]:
        start_time = time.perf_counter()
        usage: dict[str, int] = {}
        self._log_llm_event(
            "INFO",
            "llm.call.started",
            "llm call started",
            extra=self._build_llm_extra(operation, start_time),
        )
        try:
            for chunk in stream_fn():
                if isinstance(chunk, tuple):
                    text, chunk_usage = chunk
                    if isinstance(chunk_usage, dict) and chunk_usage:
                        usage = chunk_usage
                    yield text
                else:
                    yield chunk
        except Exception as exc:
            self._log_llm_event(
                "ERROR",
                "llm.call.failed",
                "llm call failed",
                extra=self._build_llm_extra(operation, start_time, error=str(exc)),
                exception="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            )
            raise
        self._log_llm_event(
            "INFO",
            "llm.call.completed",
            "llm call completed",
            extra=self._build_llm_extra(operation, start_time, **usage),
        )

    def _get_capabilities(self) -> dict:
        settings = self._settings
        if settings is None:
            return {
                "supports_vision": False,
                "vision_input_mode": "url",
                "reject_image_when_unsupported": False,
            }

        try:
            supports_vision = bool(settings.get("llm:supports_vision"))
        except KeyError:
            supports_vision = False
        try:
            vision_input_mode = settings.get("llm:vision_input_mode")
        except KeyError:
            vision_input_mode = "url"
        try:
            reject_image_when_unsupported = bool(settings.get("llm:reject_image_when_unsupported"))
        except KeyError:
            reject_image_when_unsupported = False

        return {
            "supports_vision": supports_vision,
            "vision_input_mode": vision_input_mode,
            "reject_image_when_unsupported": reject_image_when_unsupported,
        }

    def _to_langchain_content(self, message: Dict[str, Any], *, role: str, allow_multimodal: bool) -> Any:
        parts = message.get("parts") or []
        if not parts:
            return message.get("content", "")

        if role != "user":
            return parts_to_plain_text(parts)

        if not has_image_parts(parts):
            return parts_to_plain_text(parts)

        capabilities = self._get_capabilities()
        if not allow_multimodal:
            raise ValueError("当前调用场景不支持图片输入")
        if not capabilities.get("supports_vision"):
            if capabilities.get("reject_image_when_unsupported"):
                raise ValueError("当前模型不支持图像理解")
            return parts_to_plain_text(parts)

        content_blocks = []
        for part in parts:
            part_type = part.get("type")
            if part_type == "text":
                content_blocks.append({"type": "text", "text": str(part.get("text", ""))})
            elif part_type == "image":
                image_url = {"url": str(part.get("image_url", ""))}
                if part.get("detail"):
                    image_url["detail"] = str(part.get("detail"))
                content_blocks.append({
                    "type": "image_url",
                    "image_url": image_url,
                })
        return content_blocks or parts_to_plain_text(parts)

    def _build_lc_messages(self, messages: List[Dict[str, Any]], system_prompt: Optional[str], *, allow_multimodal: bool) -> List[Any]:
        normalized_messages = normalize_chat_messages(messages)
        lc_messages = []

        if system_prompt:
            lc_messages.append(SystemMessage(content=system_prompt))

        for msg in normalized_messages:
            role = msg.get("role", "user")
            content = self._to_langchain_content(msg, role=role, allow_multimodal=allow_multimodal)

            if role == "user":
                lc_messages.append(HumanMessage(content=content))
            elif role == "assistant":
                lc_messages.append(AIMessage(content=content))
            elif role == "system":
                lc_messages.append(SystemMessage(content=content))

        return lc_messages

    def chat(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        http_client: Any = None,
        http_async_client: Any = None,
    ) -> str:
        """
        发送聊天请求

        Args:
            messages: 消息列表
            system_prompt: 系统提示词

        Returns:
            AI 响应文本
        """
        if http_client is not None or http_async_client is not None:
            llm = self._build_llm(http_client=http_client, http_async_client=http_async_client)
        else:
            llm = self._get_llm()

        lc_messages = self._build_lc_messages(messages, system_prompt, allow_multimodal=True)

        console.messages_box("LLM 原始提示词", lc_messages)
        console.info(f"发送请求: {len(lc_messages)} 条消息")

        response = self._invoke_with_logging("chat", lambda: llm.invoke(lc_messages))

        response_text = response.content if isinstance(response.content, str) else str(response.content)
        console.success(f"收到响应: {len(response_text)} 字符")
        return response_text

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        stream_callback: Optional[Callable[[str], None]] = None,
        http_client: Any = None,
        http_async_client: Any = None,
    ) -> Generator[str, None, None]:
        """流式聊天请求"""
        if http_client is not None or http_async_client is not None:
            llm = self._build_llm(http_client=http_client, http_async_client=http_async_client)
        else:
            llm = self._get_llm()

        lc_messages = self._build_lc_messages(messages, system_prompt, allow_multimodal=True)

        console.messages_box("LLM 原始提示词", lc_messages)
        console.info(f"发送流式请求: {len(lc_messages)} 条消息")
        console.section("流式输出")

        def stream_chunks():
            for chunk in llm.stream(lc_messages):
                chunk_usage = self._extract_usage(chunk)
                chunk_content = chunk.content
                if not chunk_content:
                    continue
                if isinstance(chunk_content, list):
                    text = "".join(str(item) for item in chunk_content)
                else:
                    text = str(chunk_content)
                print(text, end="", flush=True)
                if stream_callback:
                    stream_callback(text)
                yield text, chunk_usage

        yield from self._stream_with_logging("chat_stream", stream_chunks)
        console.section_end()

    def chat_with_history(
        self,
        user_message: Any,
        history: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        http_client: Any = None,
        http_async_client: Any = None,
    ) -> str:
        """带历史记录的聊天"""
        messages = history + [build_user_message("user", user_message)]
        return self.chat(messages, system_prompt, http_client=http_client, http_async_client=http_async_client)

    def structured_output(
        self,
        messages: List[Dict[str, Any]],
        schema: Any,
        system_prompt: Optional[str] = None,
        http_client: Any = None,
        http_async_client: Any = None,
    ) -> Any:
        """结构化输出"""
        if http_client is not None or http_async_client is not None:
            llm = self._build_llm(http_client=http_client, http_async_client=http_async_client)
        else:
            llm = self._get_llm()

        structured_llm = llm.with_structured_output(schema)
        lc_messages = self._build_lc_messages(messages, system_prompt, allow_multimodal=False)

        console.info(f"结构化输出请求: {len(lc_messages)} 条消息")

        response = self._invoke_with_logging(
            "structured_output",
            lambda: structured_llm.invoke(lc_messages),
        )

        console.success("结构化输出完成")
        return response


def get_llm_service(settings_service=None) -> LLMService:
    """获取 LLM 服务单例"""
    return LLMService(settings_service)
