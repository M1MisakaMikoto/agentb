"""
统一工具决策 / 意图响应解析器

容错链（参考 hermes-agent 的 VerifiedHermesToolCallParser 工程实践）：
    1. 直接 json.loads
    2. 剥离 ```json ... ``` code fence
    3. 平衡括号提取首个 JSON 对象/数组（兼容 LLM 前后缀杂文本）
    4. 轻量修复（结尾逗号、截断补全）
    5. 分类报错（DecisionParseError），由上层统一注入 last_error

设计约定：
- 解析层只做“取回 JSON + 归一化”，业务字段校验交给 response_schema 的 pydantic。
- 所有解析失败统一抛 DecisionParseError（含 category / raw_text），
  上层不再各自 try/except json.JSONDecodeError / ValidationError。
- 意图分析解析失败按业务要求返回宽松默认值，不抛错。
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from pydantic import ValidationError

from .response_schema import parse_decision_dict, format_decision_validation_error


class DecisionParseError(Exception):
    """决策响应解析失败（统一分类）。"""

    def __init__(self, category: str, message: str, raw_text: str = ""):
        self.category = category
        self.raw_text = raw_text
        super().__init__(message)


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def _is_balanced_candidate(text: str, start: int) -> Optional[str]:
    """从 start 处字符开始做平衡括号扫描，返回候选片段（字符串感知）。"""
    opener = text[start]
    closer = "]" if opener == "[" else "}"
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _repair_trailing_commas(text: str) -> str:
    return _TRAILING_COMMA_RE.sub(r"\1", text)


def _repair_truncation(text: str) -> Optional[str]:
    """尝试补全截断 JSON：按未闭合的括号深度补右括号（上限 6 层）。"""
    opener = text.lstrip()[:1]
    if opener not in ("{", "["):
        return None
    depth = 0
    in_string = False
    escape = False
    for ch in text:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
    if depth <= 0 or depth > 6:
        return None
    candidate = text + ("}" if opener == "{" else "]") * depth
    try:
        json.loads(candidate)
        return candidate
    except json.JSONDecodeError:
        return None


def extract_json_object(text: str) -> Any:
    """
    从模型原始响应中容错提取 JSON 值。

    Raises:
        DecisionParseError: category="json_syntax"，无法从文本中提取有效 JSON。
    """
    if text is None:
        raise DecisionParseError("json_syntax", "响应为空", "")
    stripped = text.strip()
    if not stripped:
        raise DecisionParseError("json_syntax", "响应为空", text)

    candidates: list[str] = []

    # 1. 直接解析
    candidates.append(stripped)

    # 2. 剥 code fence
    match = _FENCE_RE.search(stripped)
    if match:
        candidates.append(match.group(1).strip())

    # 3. 平衡括号提取（含数组）
    for opener in ("{", "["):
        idx = stripped.find(opener)
        while idx != -1:
            candidate = _is_balanced_candidate(stripped, idx)
            if candidate:
                candidates.append(candidate)
                break
            idx = stripped.find(opener, idx + 1)

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        cleaned = _repair_trailing_commas(candidate)
        for attempt in (candidate, cleaned):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue
        repaired = _repair_truncation(cleaned)
        if repaired:
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                continue

    raise DecisionParseError(
        "json_syntax",
        "无法从模型响应中提取有效 JSON",
        text,
    )


def parse_tool_decision_response(response_text: str) -> dict[str, Any]:
    """
    解析工具决策响应（容错链 + pydantic schema 校验）。

    - 兼容 LLM 偶发返回 [{...}] 数组：非空取首个元素。
    - 顶层必须为对象，kind 必须为 tool/step_done/blocked。

    Raises:
        DecisionParseError: category 为 "json_syntax" / "schema" / "not_object"。
    """
    raw = extract_json_object(response_text)

    if isinstance(raw, list):
        if not raw:
            raise DecisionParseError("json_syntax", "模型返回空数组", response_text)
        raw = raw[0]

    if not isinstance(raw, dict):
        raise DecisionParseError(
            "not_object",
            f"决策响应顶层必须是 JSON 对象，实际类型: {type(raw).__name__}",
            response_text,
        )

    try:
        return parse_decision_dict(raw)
    except ValidationError as e:
        raise DecisionParseError(
            "schema",
            format_decision_validation_error(e),
            response_text,
        ) from e


def parse_leader_output(response_text: str) -> dict[str, Any]:
    """
    解析 V4 leader 输出（{type: text/tool_calls/done, content}）。

    leader 输出必须是完整的 JSON 对象，仅允许首尾空白。这里不复用
    extract_json_object 的提取/修复能力，避免 text.content 内的 JSON、
    code fence 或工具描述被误识别为外层协议。

    Raises:
        DecisionParseError: category 为 "json_syntax" / "schema" / "not_object"。
    """
    from ..v4.protocol import parse_leader_output_dict

    if response_text is None:
        raise DecisionParseError("json_syntax", "leader 输出为空", "")
    stripped = response_text.strip()
    if not stripped:
        raise DecisionParseError("json_syntax", "leader 输出为空", response_text)
    try:
        raw = json.loads(stripped)
    except json.JSONDecodeError as e:
        raise DecisionParseError(
            "json_syntax",
            f"leader 输出必须是完整 JSON: {e.msg} (line {e.lineno}, column {e.colno})",
            response_text,
        ) from e
    if not isinstance(raw, dict):
        raise DecisionParseError(
            "not_object",
            f"leader 输出顶层必须是 JSON 对象，实际类型: {type(raw).__name__}",
            response_text,
        )
    expected_keys = {"type", "content"}
    actual_keys = set(raw)
    if actual_keys != expected_keys:
        missing_keys = sorted(expected_keys - actual_keys)
        unexpected_keys = sorted(actual_keys - expected_keys)
        details = []
        if missing_keys:
            details.append(f"missing keys: {missing_keys}")
        if unexpected_keys:
            details.append(f"unexpected keys: {unexpected_keys}")
        raise DecisionParseError(
            "schema",
            "leader top-level keys must be exactly type and content; "
            + "; ".join(details),
            response_text,
        )
    try:
        return parse_leader_output_dict(raw)
    except ValidationError as e:
        raise DecisionParseError(
            "schema",
            format_decision_validation_error(e),
            response_text,
        ) from e


def parse_intent_response(
    response_text: str,
    original_message: str,
) -> Optional[dict[str, Any]]:
    """
    解析意图分析响应。

    Returns:
        成功返回 {"is_malicious", "rewritten_query"}；
        失败返回 None（由调用方决定降级策略，旧行为为使用原始消息）。
    """
    try:
        raw = extract_json_object(response_text)
    except DecisionParseError:
        return None

    if not isinstance(raw, dict):
        return None

    try:
        is_malicious = bool(raw.get("is_malicious", False))
        rewritten_query = raw.get("rewritten_query", original_message)
        return {
            "is_malicious": is_malicious,
            "rewritten_query": rewritten_query if rewritten_query else original_message,
        }
    except Exception:
        return None
