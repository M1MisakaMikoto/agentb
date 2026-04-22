import hashlib
import json
import re
from typing import Dict, Any


class CacheKeyGenerator:
    """缓存键生成器（仅基于目标记录）"""
    
    @staticmethod
    def normalize_content(content: str) -> str:
        """标准化内容"""
        content = re.sub(r'\s+', ' ', content)
        content = content.strip()
        content = content.replace('\r\n', '\n')
        return content
    
    @staticmethod
    def extract_key_info(message: Dict[str, Any]) -> str:
        """提取消息的关键信息"""
        role = message.get("role", "unknown")
        
        if isinstance(message, dict):
            if "parts" in message:
                text_parts = []
                for part in message["parts"]:
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                content = " ".join(text_parts)
            elif "content" in message:
                content = message["content"]
            else:
                content = str(message)
        else:
            content = str(message)
        
        return f"{role}:{content}"
    
    @staticmethod
    def generate(
        target_message: Dict[str, Any],
        target_ratio: float,
        compression_version: str = "v1"
    ) -> str:
        """
        生成缓存键（仅基于目标记录，不包含上下文）
        
        Args:
            target_message: 目标消息对象
            target_ratio: 目标压缩率
            compression_version: 压缩算法版本
        
        Returns:
            缓存键（64位十六进制字符串）
        """
        role = target_message.get("role", "unknown")
        content = CacheKeyGenerator.extract_key_info(target_message)
        
        normalized = CacheKeyGenerator.normalize_content(content)
        
        cache_factors = {
            "role": role,
            "content": normalized,
            "target_ratio": round(target_ratio, 2),
            "version": compression_version,
            "method": "convolution",
        }
        
        cache_str = json.dumps(cache_factors, sort_keys=True, ensure_ascii=False)
        
        cache_key = hashlib.sha256(cache_str.encode('utf-8')).hexdigest()
        
        return cache_key
