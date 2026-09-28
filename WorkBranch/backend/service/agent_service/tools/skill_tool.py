"""skill 工具：列出 / 读取本部署的技能（skill）指导。

技能用于承载「非通用」指导（地区数据库规则、报告格式模板等），由部署方维护，
agent 通过 skill list 查看、skill read 读取后再执行任务。

目录约定：每个技能一个子目录，目录内一个 SKILL.md，文件头为 frontmatter：

    ---
    name: <技能名>
    description: <用途，以及何时该读取该技能>
    ---

    <技能正文>

技能根目录由配置项 agent_tools:skills:dir 指定；相对路径以 WorkBranch 为基准。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .registry import ToolDefinition, ToolRegistry

SKILL_FILE_NAME = "SKILL.md"
SKILL_TOOLS = {"skill"}
SKILL_CATEGORY = "skill"

# .../WorkBranch/backend/service/agent_service/tools/skill_tool.py -> .../WorkBranch
_WORKBRANCH_DIR = Path(__file__).resolve().parents[4]


def get_skills_dir() -> Path:
    """解析技能根目录：配置项 agent_tools:skills:dir，相对路径以 WorkBranch 为基准。"""
    from singleton import get_settings_service

    configured = get_settings_service().get("agent_tools:skills:dir")
    path = Path(str(configured)).expanduser()
    if not path.is_absolute():
        path = _WORKBRANCH_DIR / path
    return path


def _parse_skill_file(skill_file: Path) -> Dict[str, Any]:
    """解析 SKILL.md 的 frontmatter 与正文；格式不合法时直接报错。"""
    text = skill_file.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"{skill_file} 缺少 frontmatter（文件必须以 --- 开头）")
    end = text.find("\n---", 3)
    if end < 0:
        raise ValueError(f"{skill_file} frontmatter 未闭合（缺少结束的 ---）")

    header = text[3:end]
    body = text[end + 4 :].lstrip("\n")
    meta: Dict[str, str] = {}
    for raw_line in header.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise ValueError(f"{skill_file} frontmatter 行格式错误：{line}")
        meta[key.strip()] = value.strip()

    name = meta.get("name")
    description = meta.get("description")
    if not name:
        raise ValueError(f"{skill_file} frontmatter 缺少 name")
    if not description:
        raise ValueError(f"{skill_file} frontmatter 缺少 description")
    if name != skill_file.parent.name:
        raise ValueError(
            f"{skill_file} frontmatter 的 name（{name}）与目录名"
            f"（{skill_file.parent.name}）不一致"
        )
    if not body.strip():
        raise ValueError(f"{skill_file} 正文为空")

    return {"name": name, "description": description, "body": body, "path": str(skill_file)}


def _load_skills(skills_dir: Path) -> List[Dict[str, Any]]:
    if not skills_dir.is_dir():
        raise FileNotFoundError(f"技能目录不存在：{skills_dir}")

    skills: List[Dict[str, Any]] = []
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue
        skill_file = child / SKILL_FILE_NAME
        if not skill_file.is_file():
            continue
        skills.append(_parse_skill_file(skill_file))
    return skills


def execute_skill(tool_args: dict) -> dict:
    """执行 skill 工具：operation=list 列出技能，operation=read 读取指定技能全文。"""
    operation = str(tool_args.get("operation") or "").strip().lower()
    if operation not in {"list", "read"}:
        return {
            "result": None,
            "error": f"无效的 operation：{operation or '(空)'}，仅支持 list/read",
        }

    try:
        skills_dir = get_skills_dir()
        skills = _load_skills(skills_dir)
    except Exception as e:
        return {"result": None, "error": f"技能读取失败：{e}"}

    if operation == "list":
        if not skills:
            return {"result": "当前部署没有可用技能。", "error": None}
        lines = [f"可用技能（{len(skills)} 个，技能根目录：{skills_dir}）："]
        for skill in skills:
            lines.append(f"- {skill['name']}：{skill['description']}")
        return {"result": "\n".join(lines), "error": None}

    name = tool_args.get("name")
    if not name:
        return {"result": None, "error": "skill read 缺少 name 参数"}
    for skill in skills:
        if skill["name"] == name:
            result = f"技能 {skill['name']}（{skill['path']}）：\n\n{skill['body']}"
            return {"result": result, "error": None}

    available = "、".join(skill["name"] for skill in skills) or "无"
    return {"result": None, "error": f"技能不存在：{name}；可用技能：{available}"}


def register_skill_tools() -> None:
    """注册 skill 工具到 ToolRegistry。"""
    ToolRegistry.register(
        ToolDefinition(
            name="skill",
            description="列出/读取本部署技能（非通用指导：地区规则、报告格式等）",
            params=(
                'skill:{"operation":"(必填)list|read",'
                '"name":"(read 必填，取自 list 返回的技能名)"}'
            ),
            category=SKILL_CATEGORY,
            executor=execute_skill,
        )
    )
