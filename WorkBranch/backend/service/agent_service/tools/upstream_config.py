"""上游特定工具的配置读取。

报告上传 / 巡查记录 / AI 研判这几个上游工具已迁到项目内自建的 MCP 服务
（WorkBranch/mcp_servers/upstream_tools），其 api_url、密钥、超时由该 MCP 服务
自身的配置管理（config.json + 环境变量）提供，不再从后端 settings 服务读取。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

# .../WorkBranch/backend/service/agent_service/tools/upstream_config.py -> .../WorkBranch
_WORKBRANCH_DIR = Path(__file__).resolve().parents[4]


def upstream_tool_config(tool_name: str) -> Dict[str, Any]:
    """按工具名读取上游工具 MCP 服务的配置段（缺配置直接报错）。"""
    if str(_WORKBRANCH_DIR) not in sys.path:
        sys.path.insert(0, str(_WORKBRANCH_DIR))

    from mcp_servers.upstream_tools.config import tool_config

    return tool_config(tool_name)
