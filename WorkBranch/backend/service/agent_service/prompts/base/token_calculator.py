"""
Token计算器
提供文本Token估算和上下文使用率计算功能
"""

from typing import List, Dict, Any, Optional


class TokenCalculator:
    """Token计算器"""

    DEFAULT_CONTEXT_WINDOW = 1_000_000
    
    CONTEXT_WINDOWS = {
        "gpt-4o-mini": 128000,
        "gpt-4o": 128000,
        "gpt-4-turbo": 128000,
        "gpt-3.5-turbo": 16385,
        "claude-3-opus": 200000,
        "claude-3-sonnet": 200000,
    }
    
    def __init__(self, settings_service=None):
        self.settings = settings_service
        self.context_window = self._get_context_window_size()
    
    def _get_context_window_size(self) -> int:
        """根据模型获取上下文窗口大小"""
        if not self.settings:
            return self.DEFAULT_CONTEXT_WINDOW
        try:
            model = self.settings.get("llm:model")
            return self.CONTEXT_WINDOWS.get(model, self.DEFAULT_CONTEXT_WINDOW)
        except Exception:
            return self.DEFAULT_CONTEXT_WINDOW
    
    def estimate_tokens(self, content: str) -> int:
        """估算文本token数量"""
        if not content:
            return 0
            
        try:
            import tiktoken
            encoding = tiktoken.encoding_for_model("gpt-4")
            return len(encoding.encode(content))
        except Exception:
            chinese_chars = sum(1 for c in content if '\u4e00' <= c <= '\u9fff')
            other_chars = len(content) - chinese_chars
            return int(chinese_chars / 1.5 + other_chars / 4)
    
    def calculate_usage_rate(
        self, 
        messages: List[dict], 
        system_prompt: str = ""
    ) -> float:
        """计算当前上下文使用率"""
        total_tokens = self.estimate_tokens(system_prompt)
        
        for msg in messages:
            content = self._extract_content(msg)
            total_tokens += self.estimate_tokens(content)
        
        return total_tokens / self.context_window if self.context_window > 0 else 0
    
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
    
    def should_compress(self, usage_rate: float, threshold: float = 0.6) -> bool:
        """判断是否需要压缩"""
        return usage_rate >= threshold
    
    def calculate_target_ratio(
        self, 
        usage_rate: float, 
        target_min: float = 0.3, 
        target_max: float = 0.7
    ) -> float:
        """根据使用率计算目标压缩比例"""
        if usage_rate < 0.6:
            return 1.0
        elif usage_rate < 0.8:
            return target_min + (target_max - target_min) * (usage_rate - 0.6) / 0.2
        else:
            return target_min
