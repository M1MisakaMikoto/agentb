from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
import json
import time

from ..cache import CompressionCache
from ..prompts.compression_prompts import CONVOLUTION_COMPRESSION_PROMPT, COMPRESSION_SYSTEM_PROMPT
from service.session_service.canonical import SegmentType


@dataclass
class ConvolutionWindow:
    """卷积窗口"""
    prev: Optional[Dict[str, Any]]
    target: Dict[str, Any]
    next: Optional[Dict[str, Any]]
    target_index: int


class TokenCalculator:
    """Token计算器"""
    
    CONTEXT_WINDOWS = {
        "gpt-4o-mini": 128000,
        "gpt-4o": 128000,
        "gpt-4-turbo": 128000,
        "gpt-3.5-turbo": 16385,
        "claude-3-opus": 200000,
        "claude-3-sonnet": 200000,
    }
    
    def __init__(self, settings_service):
        self.settings = settings_service
        self.context_window = self._get_context_window_size()
    
    def _get_context_window_size(self) -> int:
        """根据模型获取上下文窗口大小"""
        try:
            model = self.settings.get("llm:model")
            return self.CONTEXT_WINDOWS.get(model, 128000)
        except:
            return 128000
    
    def estimate_tokens(self, content: str) -> int:
        """估算文本token数量"""
        try:
            import tiktoken
            encoding = tiktoken.encoding_for_model("gpt-4")
            return len(encoding.encode(content))
        except:
            chinese_chars = sum(1 for c in content if '\u4e00' <= c <= '\u9fff')
            other_chars = len(content) - chinese_chars
            return int(chinese_chars / 1.5 + other_chars / 4)
    
    def calculate_usage_rate(self, messages: List[dict], system_prompt: str = "") -> float:
        """计算当前上下文使用率"""
        total_tokens = self.estimate_tokens(system_prompt)
        
        for msg in messages:
            content = self._extract_content(msg)
            total_tokens += self.estimate_tokens(content)
        
        return total_tokens / self.context_window
    
    def _extract_content(self, message: Dict[str, Any]) -> str:
        """提取消息内容"""
        if isinstance(message, dict):
            if "parts" in message:
                text_parts = []
                for part in message["parts"]:
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                return " ".join(text_parts)
            elif "content" in message:
                return str(message["content"])
        return str(message)


class ConvolutionCompressor:
    """卷积式压缩器"""
    
    def __init__(self, settings_service, llm_service, cache: CompressionCache):
        self.settings = settings_service
        self.llm_service = llm_service
        self.cache = cache
        self.token_calculator = TokenCalculator(settings_service)
        
        self.keep_recent = settings_service.get("compression:keep_recent")
        self.min_length = settings_service.get("compression:min_length_to_compress")
        self.trigger_threshold = settings_service.get("compression:trigger_threshold")
        self.target_min = settings_service.get("compression:target_min")
        self.target_max = settings_service.get("compression:target_max")
        self.compression_version = settings_service.get("compression:compression_version")
    
    def compress_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """压缩消息列表"""
        if not messages:
            return messages
        
        total = len(messages)
        recent_start = max(0, total - self.keep_recent)
        
        usage_rate = self.token_calculator.calculate_usage_rate(messages)
        if usage_rate < self.trigger_threshold:
            return messages
        
        target_ratio = self._calculate_target_ratio(usage_rate)
        
        result = []
        
        for i in range(total):
            if i >= recent_start:
                result.append(messages[i])
                continue
            
            content = self._extract_content(messages[i])
            if len(content) < self.min_length:
                result.append(messages[i])
                continue
            
            window = self._build_window(messages, i)
            
            compressed = self._compress_with_window(window, target_ratio)
            result.append(compressed)
        
        return result
    
    def _build_window(self, messages: List[Dict], target_index: int) -> ConvolutionWindow:
        """构建卷积窗口"""
        prev = messages[target_index - 1] if target_index > 0 else None
        target = messages[target_index]
        next_msg = messages[target_index + 1] if target_index < len(messages) - 1 else None
        
        return ConvolutionWindow(
            prev=prev,
            target=target,
            next=next_msg,
            target_index=target_index
        )
    
    def _compress_with_window(self, window: ConvolutionWindow, target_ratio: float) -> Dict[str, Any]:
        """使用卷积窗口压缩"""
        
        cached = self.cache.get(window.target, target_ratio)
        if cached:
            return self._build_compressed_message(window.target, cached, window.target_index)
        
        prompt = self._build_convolution_prompt(window, target_ratio)
        
        compressed_json = self._call_llm(prompt)
        
        try:
            compressed_result = json.loads(compressed_json)
        except json.JSONDecodeError:
            compressed_result = {
                "role": window.target.get("role"),
                "summary": compressed_json[:500],
                "context_relation": "",
                "key_points": [],
                "result": ""
            }
        
        original_tokens = self.token_calculator.estimate_tokens(
            self._extract_content(window.target)
        )
        compressed_tokens = self.token_calculator.estimate_tokens(
            json.dumps(compressed_result, ensure_ascii=False)
        )
        
        self.cache.set(
            window.target,
            target_ratio,
            compressed_result,
            original_tokens,
            compressed_tokens
        )
        
        return self._build_compressed_message(window.target, compressed_result, window.target_index)
    
    def _build_convolution_prompt(self, window: ConvolutionWindow, target_ratio: float) -> str:
        """构建卷积提示词"""
        
        prev_context = ""
        if window.prev:
            prev_role = window.prev.get("role", "unknown")
            prev_content = self._extract_content(window.prev)
            prev_context = f"[{prev_role}]: {prev_content[:500]}..." if len(prev_content) > 500 else f"[{prev_role}]: {prev_content}"
        
        next_context = ""
        if window.next:
            next_role = window.next.get("role", "unknown")
            next_content = self._extract_content(window.next)
            next_context = f"[{next_role}]: {next_content[:500]}..." if len(next_content) > 500 else f"[{next_role}]: {next_content}"
        
        target_content = self._extract_content(window.target)
        target_role = window.target.get("role", "unknown")
        
        original_tokens = self.token_calculator.estimate_tokens(target_content)
        target_tokens = int(original_tokens * target_ratio)
        
        prompt = CONVOLUTION_COMPRESSION_PROMPT.format(
            prev_context=prev_context or "(无上一条记录)",
            next_context=next_context or "(无下一条记录)",
            target_content=f"[{target_role}]: {target_content}",
            target_tokens=target_tokens,
            compression_ratio=int(target_ratio * 100)
        )
        
        return prompt
    
    def _build_compressed_message(
        self, 
        original: Dict[str, Any], 
        compressed_result: Dict[str, Any],
        index: int
    ) -> Dict[str, Any]:
        """构建压缩后的消息"""
        return {
            "role": original.get("role"),
            "content": f"[压缩记录 #{index}]\n{json.dumps(compressed_result, ensure_ascii=False, indent=2)}",
            "compressed": True,
            "original_length": len(self._extract_content(original)),
            "compressed_length": len(json.dumps(compressed_result, ensure_ascii=False)),
        }
    
    def _extract_content(self, message: Dict[str, Any]) -> str:
        """提取消息内容"""
        if isinstance(message, dict):
            if "parts" in message:
                text_parts = []
                for part in message["parts"]:
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                return " ".join(text_parts)
            elif "content" in message:
                return str(message["content"])
        return str(message)
    
    def _calculate_target_ratio(self, current_rate: float) -> float:
        """计算目标压缩率"""
        target_rate = (self.target_min + self.target_max) / 2
        return min(target_rate / current_rate, 1.0)
    
    def _call_llm(self, prompt: str) -> str:
        """调用LLM"""
        messages = [
            {"role": "system", "content": COMPRESSION_SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]
        
        response = self.llm_service.chat(messages, temperature=0.3)
        return response


class CompressionService:
    """压缩服务"""
    
    def __init__(self, settings_service, llm_service):
        self.settings = settings_service
        self.llm_service = llm_service
        
        self.enabled = settings_service.get("compression:enabled")
        self.compression_version = settings_service.get("compression:compression_version")
        
        self.cache = CompressionCache(settings_service, self.compression_version)
        self.compressor = ConvolutionCompressor(settings_service, llm_service, self.cache)
        
        self._metrics = {
            "total_requests": 0,
            "compressed_requests": 0,
            "total_compression_time": 0,
        }
    
    def compress_messages(
        self, 
        messages: List[Dict[str, Any]], 
        message_context: Optional[dict] = None,
        source: str = "unknown"
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        压缩消息列表，并发送信号给前端
        
        Args:
            messages: 待压缩的消息列表
            message_context: 消息上下文，包含 send_message 方法
            source: 压缩来源，如 "parent_chain" 或 "current_conversation"
        
        Returns:
            (压缩后的消息列表, 压缩统计信息)
        """
        if not self.enabled or not messages:
            return messages, {}
        
        send_message = message_context.get("send_message") if message_context else None
        
        original_tokens = sum(
            self.compressor.token_calculator.estimate_tokens(
                self.compressor._extract_content(msg)
            )
            for msg in messages
        )
        
        start_time = time.time()
        
        if send_message:
            send_message("", SegmentType.COMPRESSION_START, {
                "source": source,
                "message_count": len(messages),
                "original_tokens": original_tokens,
                "is_start": True
            })
        
        self._metrics["total_requests"] += 1
        
        result = self.compressor.compress_messages(messages)
        
        compression_time = time.time() - start_time
        self._metrics["total_compression_time"] += compression_time
        
        compressed_count = sum(1 for msg in result if msg.get("compressed"))
        if compressed_count > 0:
            self._metrics["compressed_requests"] += 1
        
        compressed_tokens = sum(
            self.compressor.token_calculator.estimate_tokens(
                self.compressor._extract_content(msg)
            )
            for msg in result
        )
        
        if send_message:
            send_message("", SegmentType.COMPRESSION_END, {
                "source": source,
                "message_count": len(messages),
                "compressed_count": compressed_count,
                "original_tokens": original_tokens,
                "compressed_tokens": compressed_tokens,
                "compression_time": round(compression_time, 2),
                "is_end": True
            })
        
        stats = {
            "original_tokens": original_tokens,
            "compressed_tokens": compressed_tokens,
            "compression_time": compression_time,
            "compressed_count": compressed_count
        }
        
        return result, stats
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        cache_stats = self.cache.get_hit_rate()
        
        avg_compression_time = (
            self._metrics["total_compression_time"] / self._metrics["total_requests"]
            if self._metrics["total_requests"] > 0
            else 0
        )
        
        return {
            "total_requests": self._metrics["total_requests"],
            "compressed_requests": self._metrics["compressed_requests"],
            "avg_compression_time": f"{avg_compression_time:.2f}s",
            **cache_stats
        }
