"""
Plan File Service - 计划文件管理服务

参考 Claude Code 的设计：
- Plan 阶段：生成计划并写入文件
- 审批环节：用户查看/编辑/批准计划
- Execute 阶段：读取计划文件作为执行输入
"""
import os
import json
from datetime import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path


PLAN_FILENAME = "plan.md"
PLAN_META_FILENAME = "plan_meta.json"


class PlanFileService:
    """计划文件管理服务"""
    
    def __init__(self, base_dir: str = "workspaces"):
        self.base_dir = base_dir
    
    def get_plan_dir(self, session_id: str, workspace_id: str) -> str:
        """获取计划文件所在目录"""
        return os.path.join(self.base_dir, session_id, workspace_id)
    
    def get_plan_file_path(self, session_id: str, workspace_id: str) -> str:
        """获取计划文件路径"""
        return os.path.join(self.get_plan_dir(session_id, workspace_id), PLAN_FILENAME)
    
    def get_plan_meta_path(self, session_id: str, workspace_id: str) -> str:
        """获取计划元数据文件路径"""
        return os.path.join(self.get_plan_dir(session_id, workspace_id), PLAN_META_FILENAME)
    
    def plan_exists(self, session_id: str, workspace_id: str) -> bool:
        """检查计划文件是否存在"""
        plan_file = self.get_plan_file_path(session_id, workspace_id)
        return os.path.exists(plan_file)
    
    def create_plan(
        self,
        session_id: str,
        workspace_id: str,
        plan_content: str,
        plan_steps: List[Dict[str, Any]] = None,
        metadata: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        创建计划文件
        
        Args:
            session_id: 会话ID
            workspace_id: 工作区ID
            plan_content: 计划内容（Markdown格式）
            plan_steps: 计划步骤列表（结构化数据）
            metadata: 额外元数据
            
        Returns:
            创建结果
        """
        plan_dir = self.get_plan_dir(session_id, workspace_id)
        os.makedirs(plan_dir, exist_ok=True)
        
        plan_file = self.get_plan_file_path(session_id, workspace_id)
        meta_file = self.get_plan_meta_path(session_id, workspace_id)
        
        with open(plan_file, "w", encoding="utf-8") as f:
            f.write(plan_content)
        
        meta = {
            "workspace_id": workspace_id,
            "session_id": session_id,
            "created_at": datetime.now().isoformat(),
            "status": "pending",
            "steps": plan_steps or [],
            "extra": metadata or {}
        }
        
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        
        return {
            "success": True,
            "plan_file": plan_file,
            "meta_file": meta_file,
            "message": "计划文件创建成功"
        }
    
    def read_plan(self, session_id: str, workspace_id: str) -> Dict[str, Any]:
        """
        读取计划文件
        
        Args:
            session_id: 会话ID
            workspace_id: 工作区ID
            
        Returns:
            计划内容和元数据
        """
        plan_file = self.get_plan_file_path(session_id, workspace_id)
        meta_file = self.get_plan_meta_path(session_id, workspace_id)
        
        if not os.path.exists(plan_file):
            return {
                "success": False,
                "error": "计划文件不存在",
                "content": None,
                "meta": None
            }
        
        with open(plan_file, "r", encoding="utf-8") as f:
            content = f.read()
        
        meta = {}
        if os.path.exists(meta_file):
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
        
        return {
            "success": True,
            "content": content,
            "meta": meta,
            "plan_file": plan_file
        }
    
    def update_plan(
        self,
        session_id: str,
        workspace_id: str,
        plan_content: str = None,
        plan_steps: List[Dict[str, Any]] = None,
        metadata: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        更新计划文件
        
        Args:
            session_id: 会话ID
            workspace_id: 工作区ID
            plan_content: 新的计划内容（可选）
            plan_steps: 新的计划步骤（可选）
            metadata: 新的元数据（可选）
            
        Returns:
            更新结果
        """
        plan_file = self.get_plan_file_path(session_id, workspace_id)
        meta_file = self.get_plan_meta_path(session_id, workspace_id)
        
        if plan_content is not None:
            with open(plan_file, "w", encoding="utf-8") as f:
                f.write(plan_content)
        
        if plan_steps is not None or metadata is not None:
            existing_meta = {}
            if os.path.exists(meta_file):
                with open(meta_file, "r", encoding="utf-8") as f:
                    existing_meta = json.load(f)
            
            if plan_steps is not None:
                existing_meta["steps"] = plan_steps
            if metadata is not None:
                existing_meta["extra"] = metadata
            
            existing_meta["updated_at"] = datetime.now().isoformat()
            
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(existing_meta, f, ensure_ascii=False, indent=2)
        
        return {
            "success": True,
            "message": "计划文件更新成功"
        }
    
    def approve_plan(
        self,
        session_id: str,
        workspace_id: str,
        approved: bool = True,
        feedback: str = None
    ) -> Dict[str, Any]:
        """
        审批计划
        
        Args:
            session_id: 会话ID
            workspace_id: 工作区ID
            approved: 是否批准
            feedback: 用户反馈
            
        Returns:
            审批结果
        """
        meta_file = self.get_plan_meta_path(session_id, workspace_id)
        
        if not os.path.exists(meta_file):
            return {
                "success": False,
                "error": "计划元数据文件不存在"
            }
        
        with open(meta_file, "r", encoding="utf-8") as f:
            meta = json.load(f)
        
        meta["status"] = "approved" if approved else "rejected"
        meta["approved_at"] = datetime.now().isoformat()
        if feedback:
            meta["feedback"] = feedback
        
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        
        return {
            "success": True,
            "status": meta["status"],
            "message": "计划已批准" if approved else "计划已拒绝"
        }
    
    def delete_plan(self, session_id: str, workspace_id: str) -> Dict[str, Any]:
        """
        删除计划文件
        
        Args:
            session_id: 会话ID
            workspace_id: 工作区ID
            
        Returns:
            删除结果
        """
        plan_file = self.get_plan_file_path(session_id, workspace_id)
        meta_file = self.get_plan_meta_path(session_id, workspace_id)
        
        deleted = []
        
        if os.path.exists(plan_file):
            os.remove(plan_file)
            deleted.append("plan_file")
        
        if os.path.exists(meta_file):
            os.remove(meta_file)
            deleted.append("meta_file")
        
        return {
            "success": True,
            "deleted": deleted,
            "message": f"已删除: {', '.join(deleted)}" if deleted else "无文件需要删除"
        }
    
    def get_plan_status(self, session_id: str, workspace_id: str) -> Dict[str, Any]:
        """
        获取计划状态
        
        Args:
            session_id: 会话ID
            workspace_id: 工作区ID
            
        Returns:
            计划状态信息
        """
        meta_file = self.get_plan_meta_path(session_id, workspace_id)
        
        if not os.path.exists(meta_file):
            return {
                "exists": False,
                "status": None
            }
        
        with open(meta_file, "r", encoding="utf-8") as f:
            meta = json.load(f)
        
        return {
            "exists": True,
            "status": meta.get("status"),
            "created_at": meta.get("created_at"),
            "approved_at": meta.get("approved_at"),
            "steps_count": len(meta.get("steps", []))
        }
    
    def format_plan_as_markdown(
        self,
        task_description: str,
        steps: List[Dict[str, Any]]
    ) -> str:
        """
        将结构化计划格式化为 Markdown
        
        Args:
            task_description: 任务描述
            steps: 计划步骤列表
            
        Returns:
            Markdown 格式的计划
        """
        lines = [
            "# 执行计划",
            "",
            f"**任务**: {task_description}",
            "",
            f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## 执行步骤",
            ""
        ]
        
        phase_names = {
            "research": "研究阶段",
            "synthesis": "综合阶段",
            "implementation": "实现阶段",
            "verification": "验证阶段"
        }
        
        current_phase = None
        
        for step in steps:
            phase = step.get("phase", "implementation")
            phase_name = phase_names.get(phase, phase)
            
            if phase != current_phase:
                lines.append(f"### {phase_name}")
                lines.append("")
                current_phase = phase
            
            step_id = step.get("id", "?")
            description = step.get("description", "")
            tool = step.get("tool", "thinking")
            
            lines.append(f"{step_id}. **{description}**")
            lines.append(f"   - 工具: `{tool}`")
            lines.append("")
        
        lines.extend([
            "---",
            "",
            "*此计划由 Agent 自动生成，请审核后批准执行。*"
        ])
        
        return "\n".join(lines)


plan_file_service = PlanFileService()
