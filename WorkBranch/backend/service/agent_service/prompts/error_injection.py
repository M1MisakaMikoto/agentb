"""
错误信息注入模块
用于在提示词末尾注入错误工具调用提示，帮助 Agent 知道自己出错了
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ToolCallError:
    """
    工具调用错误结构

    Attributes:
        type: 错误类型（tool_name_error, param_name_error, param_missing_error, json_format_error 等）
        tool_name: 工具名
        error: 错误详情
        hints: 建议列表（如 ["read_file"]）
        original_json: 原始 JSON 字符串
    """
    type: str = ""
    tool_name: str = ""
    error: str = ""
    hints: List[str] = field(default_factory=list)
    original_json: str = ""


def format_error_for_prompt(error: ToolCallError) -> str:
    """
    将错误格式化为提示词文本，追加到 User Message 末尾。

    格式化示例：
    ⚠️ 上一次工具调用有错误：
    - 错误类型: tool_name_error
    - 错误详情: tool_name 不在白名单中
    - 原始JSON: {"kind":"tool","tool_name":"read_flile","tool_args":{}}
    - Did you mean 'read_file'?
    请根据以上错误信息重新决策。

    Args:
        error: ToolCallError 实例

    Returns:
        格式化的提示词文本
    """
    if error.type == "json_format_error":
        return "\n".join([
            "⚠️ 上一次决策格式错误：",
            f"- 错误详情: {error.error}",
            f"- 原始JSON: {error.original_json}",
            "请返回合法的顶层JSON对象后重新决策。",
        ])

    lines = [
        "⚠️ 上一次工具调用有错误：",
        f"- 错误类型: {error.type}",
        f"- 错误详情: {error.error}",
        f"- 原始JSON: {error.original_json}",
    ]

    # 添加 hints（如果有）
    if error.hints:
        if len(error.hints) == 1:
            lines.append(f"- Did you mean '{error.hints[0]}'?")
        else:
            hints_str = "', '".join(error.hints)
            lines.append(f"- Did you mean one of '{hints_str}'?")

    lines.append("请根据以上错误信息重新决策。")

    return "\n".join(lines)


def create_json_format_error(original_json: str, parse_error: str) -> ToolCallError:
    """
    创建 JSON 格式错误

    Args:
        original_json: 原始 JSON 字符串
        parse_error: 解析错误详情

    Returns:
        ToolCallError 实例
    """
    return ToolCallError(
        type="json_format_error",
        tool_name="",
        error=f"JSON 格式错误: {parse_error}",
        hints=[],
        original_json=original_json
    )


def create_tool_name_error(invalid_name: str, suggestions: List[str], original_json: str) -> ToolCallError:
    """
    创建工具名错误

    Args:
        invalid_name: 无效的工具名
        suggestions: 建议的工具名列表
        original_json: 原始 JSON 字符串

    Returns:
        ToolCallError 实例
    """
    return ToolCallError(
        type="tool_name_error",
        tool_name=invalid_name,
        error=f"工具名 '{invalid_name}' 不在白名单中",
        hints=suggestions,
        original_json=original_json
    )


def create_param_name_error(tool_name: str, invalid_param: str, valid_params: List[str], original_json: str) -> ToolCallError:
    """
    创建参数名错误

    Args:
        tool_name: 工具名
        invalid_param: 无效的参数名
        valid_params: 有效的参数名列表
        original_json: 原始 JSON 字符串

    Returns:
        ToolCallError 实例
    """
    return ToolCallError(
        type="param_name_error",
        tool_name=tool_name,
        error=f"工具 '{tool_name}' 的参数 '{invalid_param}' 不存在",
        hints=valid_params,
        original_json=original_json
    )


def create_param_missing_error(tool_name: str, missing_params: List[str], original_json: str) -> ToolCallError:
    """
    创建缺少必填参数错误

    Args:
        tool_name: 工具名
        missing_params: 缺少的必填参数列表
        original_json: 原始 JSON 字符串

    Returns:
        ToolCallError 实例
    """
    params_str = ", ".join(missing_params)
    return ToolCallError(
        type="param_missing_error",
        tool_name=tool_name,
        error=f"工具 '{tool_name}' 缺少必填参数: {params_str}",
        hints=[],
        original_json=original_json
    )


def create_param_type_error(tool_name: str, param_name: str, expected_type: str, original_json: str) -> ToolCallError:
    """
    创建参数类型错误

    Args:
        tool_name: 工具名
        param_name: 参数名
        expected_type: 期望的类型
        original_json: 原始 JSON 字符串

    Returns:
        ToolCallError 实例
    """
    return ToolCallError(
        type="param_type_error",
        tool_name=tool_name,
        error=f"工具 '{tool_name}' 的参数 '{param_name}' 类型错误，期望 {expected_type}",
        hints=[],
        original_json=original_json
    )
