from typing import List, Dict, Any, Optional, Generator, Callable

import httpx
import time
import traceback
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_openai import ChatOpenAI

from core.logging import console, open_trace_log
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
        except (OSError, ConnectionError, httpx.ConnectError, httpx.ReadError) as oe:
            error_detail = f"System/Network Error during LLM call: {type(oe).__name__}: {oe}"
            full_traceback = "".join(traceback.format_exception(type(oe), oe, oe.__traceback__))
            
            self._log_llm_event(
                "ERROR",
                "llm.call.os_error",
                error_detail,
                extra=self._build_llm_extra(operation, start_time, error=str(oe), error_type=type(oe).__name__),
                exception=full_traceback,
            )
            
            import datetime
            timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
            try:
                with open_trace_log() as f:
                    f.write(f"\n{'='*80}\n")
                    f.write(f"[{timestamp}] === ❌ LLM SERVICE OS ERROR ===\n")
                    f.write(f"Operation: {operation}\n")
                    f.write(f"Exception Type: {type(oe).__name__}\n")
                    f.write(f"Exception Message: {str(oe)}\n")
                    f.write(f"Error Code: {getattr(oe, 'errno', 'N/A')}\n")
                    f.write(f"Full Traceback:\n{full_traceback}\n")
                    f.write(f"{'='*80}\n")
                    f.flush()
            except Exception:
                pass
            
            raise TypeError(error_detail) from oe
        except Exception as exc:
            # 🔴 强制打印所有异常到控制台
            print(f"\n{'='*80}")
            print(f"[LLM-ERROR] ❌ LLM 调用失败!")
            print(f"[LLM-ERROR] Operation: {operation}")
            print(f"[LLM-ERROR] Exception Type: {type(exc).__name__}")
            print(f"[LLM-ERROR] Exception Message: {str(exc)}")
            print(f"[LLM-ERROR] Full Traceback:\n{traceback.format_exc()}")
            print(f"{'='*80}\n")

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
            return parts_to_plain_text(parts)
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
        allow_multimodal: bool = True,
    ) -> str:
        """
        发送聊天请求

        Args:
            messages: 消息列表
            system_prompt: 系统提示词
            allow_multimodal: 是否允许多模态输入（图片），默认True

        Returns:
            AI 响应文本
        """
        if http_client is not None or http_async_client is not None:
            llm = self._build_llm(http_client=http_client, http_async_client=http_async_client)
        else:
            llm = self._get_llm()

        lc_messages = self._build_lc_messages(messages, system_prompt, allow_multimodal=allow_multimodal)

        try:
            # 限制消息输出行数，避免递归和输出过长问题
            safe_messages = []
            for msg in lc_messages:  # 最多20条消息
                content = msg.content if hasattr(msg, 'content') else str(msg)
                safe_messages.append(type(msg)(content=content))
            console.messages_box("LLM 原始提示词", safe_messages)
        except Exception:
            console.info(f"LLM 原始提示词: {len(lc_messages)} 条消息")
        console.info(f"发送请求: {len(lc_messages)} 条消息")

        response = self._invoke_with_logging("chat", lambda: llm.invoke(lc_messages))

        response_text = response.content if isinstance(response.content, str) else str(response.content)
        try:
            console.success(f"收到响应: {len(response_text)} 字符")
            console.response_box(response_text, char_count=len(response_text))
        except Exception:
            console.success(f"收到响应: {len(response_text)} 字符")
        return response_text

    def chat_with_json_mode(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        http_client: Any = None,
        http_async_client: Any = None,
    ) -> str:
        """
        发送聊天请求，强制厂商 JSON Mode（response_format=json_object）。

        适用于期望 LLM 直接返回纯 JSON 字符串的场景（如 director agent 决策、计划生成）。
        提示词中必须包含 "json" 关键词，否则百炼 API 会返回 400 错误：
        'messages' must contain the word 'json' in some form, to use 'response_format' of type 'json_object'.

        复用 chat() 的消息构造、日志、token 统计逻辑，仅在 LLM 实例上链一个
        bind(response_format=...) 来注入 JSON Mode。
        """
        if http_client is not None or http_async_client is not None:
            llm = self._build_llm(http_client=http_client, http_async_client=http_async_client)
        else:
            llm = self._get_llm()

        # 关键：用 bind() 把 response_format 注入到 LLM 调用链
        json_llm = llm.bind(response_format={"type": "json_object"})

        lc_messages = self._build_lc_messages(messages, system_prompt, allow_multimodal=True)

        try:
            safe_messages = []
            for msg in lc_messages:
                content = msg.content if hasattr(msg, 'content') else str(msg)
                safe_messages.append(type(msg)(content=content))
            console.messages_box("LLM 原始提示词(JSON Mode)", safe_messages)
        except Exception:
            console.info(f"LLM 原始提示词(JSON Mode): {len(lc_messages)} 条消息")
        console.info(f"发送 JSON Mode 请求: {len(lc_messages)} 条消息")

        response = self._invoke_with_logging("chat_with_json_mode", lambda: json_llm.invoke(lc_messages))

        # 🔴 强制立即打印 response 对象（调试用，定位阻塞问题）
        print(f"\n{'='*80}")
        print(f"[LLM-DEBUG] 收到 response 对象: {type(response)}")
        print(f"[LLM-DEBUG] response 属性: {dir(response)}")
        if hasattr(response, 'content'):
            print(f"[LLM-DEBUG] response.content 类型: {type(response.content)}, 值: {repr(response.content)}")
        if hasattr(response, 'response_metadata'):
            print(f"[LLM-DEBUG] response_metadata: {response.response_metadata}")
        if hasattr(response, 'id'):
            print(f"[LLM-DEBUG] id: {response.id}")
        print(f"{'='*80}\n")

        response_text = response.content if isinstance(response.content, str) else str(response.content)

        # ✅ 强制输出完整响应内容（无论成功失败，禁止截断）
        if not response_text or not response_text.strip():
            console.warning("[LLM-ERROR] DashScope API 返回 HTTP 200 但响应体为空!")
            raise AssertionError("DashScope API 返回空响应体，无法继续处理")

        # 🔴 直接用 print() 输出，100% 无截断（不经过任何封装层）
        print(f"\n{'='*80}")
        print(f"[LLM-RAW-RESPONSE] 完整响应内容 ({len(response_text)} 字符):")
        print(f"{'='*80}")
        print(response_text)  # 原生 print()，绝对无截断
        print(f"{'='*80}\n")

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

        try:
            console.messages_box("LLM 原始提示词", lc_messages)
        except Exception:
            console.info(f"LLM 原始提示词: {len(lc_messages)} 条消息")
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


class FastLLMService:
    """快速模型服务：用于压缩、摘要等轻量任务"""

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
        """获取缓存的 LLM 实例"""
        if self._llm is None:
            self._llm = ChatOpenAI(
                api_key=self._settings.get("llm:api_key"),
                base_url=self._settings.get("llm:fast_base_url"),
                model=self._settings.get("llm:fast_model"),
                temperature=self._settings.get("llm:fast_temperature"),
                max_tokens=self._settings.get("llm:fast_max_tokens"),
                timeout=httpx.Timeout(120.0, connect=30.0),
            )
        return self._llm

    def chat(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        http_client: Any = None,
        http_async_client: Any = None,
    ) -> str:
        """
        发送聊天请求（同步）

        Args:
            messages: 消息列表
            system_prompt: 系统提示词

        Returns:
            AI 响应文本
        """
        if http_client is not None or http_async_client is not None:
            llm = ChatOpenAI(
                api_key=self._settings.get("llm:api_key"),
                base_url=self._settings.get("llm:fast_base_url"),
                model=self._settings.get("llm:fast_model"),
                temperature=self._settings.get("llm:fast_temperature"),
                max_tokens=self._settings.get("llm:fast_max_tokens"),
                timeout=httpx.Timeout(120.0, connect=30.0),
                http_client=http_client,
                http_async_client=http_async_client,
            )
        else:
            llm = self._get_llm()

        lc_messages = self._build_lc_messages(messages, system_prompt, allow_multimodal=False)

        from core.logging import console
        console.info(f"[FastLLM] 发送请求: {len(lc_messages)} 条消息，模型: {self._settings.get('llm:fast_model')}")

        start_time = time.perf_counter()
        response = llm.invoke(lc_messages)

        from singleton import get_logging_runtime
        logger = get_logging_runtime().get_logger("agent")
        logger.info(
            event="llm.call.completed",
            msg="fast llm call completed",
            extra={
                "operation": "fast_chat",
                "provider": "openai_compatible",
                "model": self._settings.get("llm:fast_model"),
                "latency_ms": round((time.perf_counter() - start_time) * 1000),
            },
        )

        response_text = response.content if isinstance(response.content, str) else str(response.content)
        return response_text

    def _build_lc_messages(self, messages: List[Dict[str, Any]], system_prompt: Optional[str], *, allow_multimodal: bool) -> List[Any]:
        """构建 LangChain 消息列表"""
        from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
        from service.session_service.message_content import (
            build_user_message,
            has_image_parts,
            normalize_chat_messages,
            parts_to_plain_text,
        )

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

    def _to_langchain_content(self, message: Dict[str, Any], *, role: str, allow_multimodal: bool) -> Any:
        """转换消息内容"""
        parts = message.get("parts") or []
        if not parts:
            return message.get("content", "")

        if role != "user":
            return parts_to_plain_text(parts)

        if not has_image_parts(parts):
            return parts_to_plain_text(parts)

        return parts_to_plain_text(parts)


def get_fast_llm_service(settings_service=None) -> FastLLMService:
    """获取快速 LLM 服务单例"""
    return FastLLMService(settings_service)
