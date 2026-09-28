"""上游工具 MCP 服务自身的配置管理。

该服务不读取后端的 settings 服务，配置只来自本服务自己的配置文件与环境变量：

- 配置文件：默认与本模块同目录的 config.json，可用环境变量 AGENTB_MCP_UPSTREAM_CONFIG 指定；
- 环境变量覆盖：见 _ENV_OVERRIDES（密钥等敏感项建议用环境变量注入）；
- 配置缺项直接报错，不做静默兜底。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

CONFIG_FILE_ENV = "AGENTB_MCP_UPSTREAM_CONFIG"
DEFAULT_CONFIG_FILE = Path(__file__).resolve().parent / "config.json"

# MCP 工具名 -> 配置段名
TOOL_SECTIONS: Dict[str, str] = {
    "submit_facility_report": "facility_report",
    "submit_facility_forecast": "facility_report",
    "submit_dailypatrol_record": "dailypatrol",
    "submit_ai_judgment_issue": "ai_judgment",
}

_REQUIRED_FIELDS: Dict[str, tuple] = {
    "facility_report": ("api_url", "timeout_seconds"),
    "dailypatrol": ("api_url", "timeout_seconds", "secret_key"),
    "ai_judgment": ("api_url", "timeout_seconds"),
}

_ENV_OVERRIDES: Dict[str, str] = {
    "facility_report.api_url": "AGENTB_MCP_FACILITY_REPORT_API_URL",
    "facility_report.timeout_seconds": "AGENTB_MCP_FACILITY_REPORT_TIMEOUT_SECONDS",
    "dailypatrol.api_url": "AGENTB_MCP_DAILYPATROL_API_URL",
    "dailypatrol.timeout_seconds": "AGENTB_MCP_DAILYPATROL_TIMEOUT_SECONDS",
    "dailypatrol.secret_key": "AGENTB_MCP_DAILYPATROL_SECRET_KEY",
    "ai_judgment.api_url": "AGENTB_MCP_AI_JUDGMENT_API_URL",
    "ai_judgment.timeout_seconds": "AGENTB_MCP_AI_JUDGMENT_TIMEOUT_SECONDS",
}

_INT_FIELDS = {"timeout_seconds"}


def config_file_path() -> Path:
    configured = os.getenv(CONFIG_FILE_ENV)
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_CONFIG_FILE


def load_config() -> Dict[str, Any]:
    """读取并校验配置；缺文件、缺段、缺字段都直接抛错。"""
    path = config_file_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"上游工具 MCP 服务配置文件不存在：{path}"
            f"（可用环境变量 {CONFIG_FILE_ENV} 指定，参考同目录 config.example.json）"
        )

    data = json.loads(path.read_text(encoding="utf-8"))
    for section_name, required in _REQUIRED_FIELDS.items():
        section = data.get(section_name)
        if not isinstance(section, dict):
            raise ValueError(f"配置缺少段：{section_name}（文件：{path}）")
        missing = [field for field in required if section.get(field) in (None, "")]
        if missing:
            raise ValueError(
                f"配置段 {section_name} 缺少字段：{', '.join(missing)}（文件：{path}）"
            )

    for dotted_key, env_name in _ENV_OVERRIDES.items():
        raw_value = os.getenv(env_name)
        if raw_value is None or raw_value == "":
            continue
        section_name, field = dotted_key.split(".", 1)
        data[section_name][field] = (
            int(raw_value) if field in _INT_FIELDS else raw_value
        )

    return data


def tool_config(tool_name: str) -> Dict[str, Any]:
    """按 MCP 工具名取上游配置段。"""
    section_name = TOOL_SECTIONS.get(tool_name)
    if section_name is None:
        raise KeyError(f"未知的上游工具：{tool_name}")
    return load_config()[section_name]
