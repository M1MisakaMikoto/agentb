"""
通用消息处理器
提供自动压缩、去重、长度控制等通用功能
"""

from typing import List, Dict, Any, Optional, Tuple

from .token_calculator import TokenCalculator


class MessageProcessor:
    """通用消息处理器"""
    
    def __init__(self, settings_service=None):
        self.settings = settings_service
        self.token_calc = TokenCalculator(settings_service)
        self._compression_service = None
    
    def _get_compression_service(self):
        """懒加载压缩服务"""
        if self._compression_service is None:
            try:
                from singleton import get_compression_service
                self._compression_service = get_compression_service()
            except Exception:
                self._compression_service = None
        return self._compression_service
    
    def process_conversation_context(
        self,
        messages: List[dict],
        source_type: str = "current_conversation",
        message_context: Optional[dict] = None,
        title: str = None
    ) -> str:
        """
        处理对话上下文（自动压缩）
        
        Args:
            messages: 消息列表
            source_type: 消息来源类型 ("parent_chain" | "current_conversation")
            message_context: 消息上下文（用于压缩服务）
            title: 区块标题
        
        Returns:
            格式化后的文本块
        """
        if not messages:
            return ""
        
        compressed_messages = self._compress_if_needed(messages, source_type, message_context)
        
        title = title or self._get_default_title(source_type)
        
        lines = [title, ""]
        
        for msg in compressed_messages:
            role = msg.get("role", "unknown")
            
            if msg.get("compressed"):
                content = msg.get("content", "")
                role_label = "用户" if role == "user" else "助手"
                lines.append(f"{role_label}: {content}")
                original_len = msg.get("original_length", 0)
                lines.append(f"*(已压缩，原始长度: {original_len}字符)*")
            else:
                content = self._safe_text(msg)
                role_label = f"{role}"
                lines.append(f"{role_label}: {content}")
            
            lines.append("")
        
        return "\n".join(lines)
    
    def process_tool_history(
        self,
        tool_history: List[dict],
        last_tool_result: Optional[str] = None,
        max_items: int = 5,
        max_summary_length: int = 200,
        max_detail_length: int = 1000
    ) -> str:
        """
        处理工具执行记录（自动去重合并）
        
        输出格式：
        📋 执行摘要：
          → 第N步: tool_name ✓ 结果概要（一行）
        
        📄 最新详情：
          [最近一次的完整结果]
        """
        if not tool_history and not last_tool_result:
            return ""
        
        parts = []
        
        # 执行摘要（最近N步）
        recent_history = tool_history[-max_items:] if tool_history else []
        if recent_history:
            summary_lines = ["📋 执行摘要：", ""]
            for idx, item in enumerate(recent_history, 1):
                tool_name = item.get("tool", "unknown")
                result = str(item.get("result") or "")
                
                # 生成一行摘要
                result_preview = self._truncate_text(result, max_summary_length)
                status = "✓" if result else "✗"
                summary_lines.append(f"  → 第{idx}步: {tool_name} {status} {result_preview}")
            
            summary_lines.append("")
            parts.append("\n".join(summary_lines))
        
        # 最新详情（仅最后一步）
        if last_tool_result:
            detail_lines = ["📄 最新结果详情：", ""]
            detail_text = self._truncate_text(last_tool_result, max_detail_length)
            detail_lines.append(detail_text)
            detail_lines.append("")
            parts.append("\n".join(detail_lines))
        elif recent_history:
            # 如果没有单独的最后结果，使用历史的最后一个
            last_item = recent_history[-1]
            last_result = str(last_item.get("result") or "")
            if last_result:
                detail_lines = ["📄 最新结果详情：", ""]
                detail_text = self._truncate_text(last_result, max_detail_length)
                detail_lines.append(detail_text)
                detail_lines.append("")
                parts.append("\n".join(detail_lines))
        
        return "\n\n".join(parts) if parts else ""
    
    def build_dynamic_section(
        self,
        user_message: str,
        workspace_id: str,
        todos: List[str] = None,
        current_todo_index: int = 0,
        plan_content: Optional[str] = None,
        include_iteration: bool = False,
        iteration_count: int = 0,
        max_iterations: int = 32
    ) -> str:
        """
        构建动态元数据区域
        
        Args:
            include_iteration: 是否包含已执行轮次（默认False）
        """
        parts = [f"原始用户请求: {user_message}", f"当前工作区ID: {workspace_id}"]
        
        # 已执行轮次（可选，默认不显示）
        if include_iteration:
            parts.append(f"已执行轮次: {iteration_count}/{max_iterations}")
        
        # Plan信息
        if plan_content:
            parts.append("")
            parts.append(
                f"当前工作区存在计划文件: plan.md\n"
                f"如果上一条历史对话提到了 plan.md，并且当前用户消息表达了批准/继续执行方案的语义，"
                f"那么你应主动使用 read_file 读取该 plan.md，再严格遵守该计划执行；"
                f"否则不要因为计划文件存在就默认按计划执行。"
            )
        
        # Todo信息
        if todos:
            parts.append("")
            todo_block = self._format_todo_block(todos, current_todo_index)
            parts.append(todo_block)
        
        return "\n".join(parts)
    
    def build_full_user_message(
        self,
        static_content: str,
        dynamic_content: str,
        tool_history_section: str = "",
        conversation_context: str = ""
    ) -> str:
        """
        构建完整的User Message（按优先级拼接）
        
        顺序：静态 → 元数据 → 工具记录 → 对话上下文
        """
        sections = []
        
        if static_content:
            sections.append(static_content)
        
        if dynamic_content:
            sections.append(dynamic_content)
        
        if tool_history_section:
            sections.append(tool_history_section)
        
        if conversation_context:
            sections.append(conversation_context)
        
        return "\n\n".join(sections)
    
    def _compress_if_needed(
        self,
        messages: List[dict],
        source_type: str,
        message_context: Optional[dict] = None
    ) -> List[dict]:
        """根据需要压缩消息"""
        compression_svc = self._get_compression_service()
        
        if not compression_svc:
            return messages
        
        try:
            usage_rate = self.token_calc.calculate_usage_rate(messages)
            
            if not self.token_calc.should_compress(usage_rate):
                return messages
            
            compressed, _ = compression_svc.compress_messages(
                messages,
                message_context=message_context,
                source=source_type
            )
            return compressed
            
        except Exception as e:
            print(f"[MessageProcessor] 压缩失败，使用原始消息: {e}")
            return messages
    
    def _get_default_title(self, source_type: str) -> str:
        """获取默认标题"""
        titles = {
            "parent_chain": "[历史对话]",
            "current_conversation": "[当前对话内历史]"
        }
        return titles.get(source_type, "[对话历史]")
    
    def _safe_text(self, msg: dict) -> str:
        """安全提取消息文本"""
        try:
            from service.agent_service.prompts.graph_prompts import build_prompt_safe_text
            return build_prompt_safe_text(msg)
        except Exception:
            content = msg.get("content", "")
            if isinstance(content, list):
                return " ".join(str(p) for p in content)
            return str(content)
    
    def _truncate_text(self, text: str, max_length: int) -> str:
        """截断文本"""
        if not text:
            return ""
        if len(text) <= max_length:
            return text
        return text[:max_length] + "..."
    
    def _format_todo_block(self, todos: List[str], current_index: int) -> str:
        """格式化Todo块"""
        lines = ["当前 TODO 列表（完整状态）:", ""]
        for idx, todo in enumerate(todos):
            marker = " <= 当前执行项" if idx == current_index else ""
            lines.append(f"{idx + 1}. {todo}{marker}")
        lines.append("")
        return "\n".join(lines)
