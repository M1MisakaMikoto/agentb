import logging
import os

from data.file_storage_system import FileStorageSystem

logger = logging.getLogger(__name__)


def _merge_missing_defaults(defaults, current, path=""):
    """Deep-merge defaults into current, only filling missing keys.

    Returns: (merged, changed)
    """
    if not isinstance(defaults, dict):
        return current, False

    if not isinstance(current, dict):
        full_path = path or "root"
        logger.warning(
            f"配置节点 '{full_path}' 类型异常(expected dict, got {type(current).__name__})，已降级为默认值"
        )
        return defaults, True

    merged = dict(current)
    changed = False

    for key, default_value in defaults.items():
        if key not in merged:
            merged[key] = default_value
            changed = True
            continue

        current_value = merged[key]
        if isinstance(default_value, dict):
            child_path = f"{path}.{key}" if path else key
            next_value, nested_changed = _merge_missing_defaults(default_value, current_value, child_path)
            if nested_changed:
                merged[key] = next_value
                changed = True

    return merged, changed

# 开发时 修改默认值后记得更新setting.json
DEFAULT_SETTINGS = {
    "mysql": {
        "host": "localhost",
        "port": 3306,
        "user": "root",
        "password": "0502",
        "database": "agentb",
        "min_pool_size": 5,
        "max_pool_size": 20,
        "pool_recycle": 3600,
        "echo": False
    },
    "session": {
        "token_expire_hours": 168,
        "max_sessions_per_user": 100
    },
    "llm": {
        "api_key": "",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.6-plus",
        "temperature": 0.1,
        "max_tokens": 16384,
        "supports_vision": True,
        "vision_input_mode": "url",
        "reject_image_when_unsupported": True,
        "structured_output": "auto",
        "fast_model": "qwen3-vl-flash",
        "fast_temperature": 0.3,
        "fast_max_tokens": 2048,
        "fast_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"
    },
    "workspace": {
        "base_dir": "workspaces"
    },
    "mq": {
        "max_size": 1000
    },
    "agent": {
        "memory_mode": "accumulate",
        "memory_window_size": 3,
        "plan_auto_approve": True,
        "orchestration_version": "v4",
        "tool_parallelism": 3,
        "closuring_enabled": True,
        "closure_max_rounds": 8,
        "ask_user_auto_approve": False,
        "tool_timeout_seconds": 1200,
        "special_tool_timeout_seconds": 1200,
        "subagent_timeout_seconds": 1800,
        "iterations": {
            "director": {"max": 32, "hard_limit": 256},
            "prediction": {"max": 32},
            "explore": {"max": 32},
            "review": {"max": 32}
        }
    },
    "agent_tools": {
        "facility_report_api_url": "http://localhost:8001",
        "dailypatrol_api_url": "http://localhost:8002",
        "ai_judgment_api_url": "http://localhost:8080"
    },
    "logging": {
        "enabled": True,
        "level": "INFO",
        "base_dir": "logs",
        "max_file_size_mb": 10,
        "frontend": {
            "enabled": True
        },
        "conversation_content": {
            "enabled": True
        },
        "sensitive_fields": ["api_key", "token", "password", "secret", "key"],
        "api_log_enabled": True,
        "retention": {
            "enabled": False,
            "max_runs": None,
            "max_days": None
        }
    },
    "compression": {
        "enabled": True,
        "compression_version": "v1",
        "trigger_threshold": 0.8,
        "target_min": 0.4,
        "target_max": 0.5,
        "keep_recent": 3,
        "min_length_to_compress": 200,
        "cache_enabled": True,
        "cache_ttl_seconds": 3600,
        "l1_cache_size": 100,
        "max_workers": 3,
        "compression_timeout": 30
    },
    "intent_analysis": {
        "enabled": True,
        "rule_keywords": [],
        "timeout_seconds": 60
    },
    "debug": {
        "consistency_check": False
    }
}


def _validate_runtime_limits(data: dict) -> None:
    agent = data.get("agent")
    if not isinstance(agent, dict):
        raise ValueError("agent 设置必须是对象")

    iterations = agent.get("iterations")
    if not isinstance(iterations, dict):
        raise ValueError("agent.iterations 设置必须是对象")

    director = iterations.get("director")
    if not isinstance(director, dict) or director.get("hard_limit") != 256:
        raise ValueError("agent.iterations.director.hard_limit 必须为 256")

    for agent_name in ("director", "prediction", "explore", "review"):
        agent_iterations = iterations.get(agent_name)
        max_iterations = agent_iterations.get("max") if isinstance(agent_iterations, dict) else None
        if isinstance(max_iterations, bool) or not isinstance(max_iterations, int):
            raise ValueError(f"agent.iterations.{agent_name}.max 必须是整数")
        if not 1 <= max_iterations <= 256:
            raise ValueError(f"agent.iterations.{agent_name}.max 必须在 1 到 256 之间")

    for key in ("tool_timeout_seconds", "special_tool_timeout_seconds", "subagent_timeout_seconds"):
        value = agent.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"agent.{key} 必须是正整数")


DEFAULT_SETTINGS_METADATA = {
    "ui": {
        "scale": {
            "type": "number",
            "control": "slider",
            "min": 0.7,
            "max": 1.3,
            "step": 0.1,
        },
        "diagram_double_click_delay_ms": {
            "type": "number",
            "control": "slider",
            "min": 150,
            "max": 600,
            "step": 10,
        }
    },
    "agent": {
        "tool_timeout_seconds": {"type": "number", "control": "slider", "min": 60, "max": 3600, "step": 60},
        "special_tool_timeout_seconds": {"type": "number", "control": "slider", "min": 60, "max": 3600, "step": 60},
        "subagent_timeout_seconds": {"type": "number", "control": "slider", "min": 60, "max": 7200, "step": 60},
        "iterations": {
            "director": {"max": {"type": "number", "control": "slider", "min": 1, "max": 256, "step": 1}},
            "prediction": {"max": {"type": "number", "control": "slider", "min": 1, "max": 256, "step": 1}},
            "explore": {"max": {"type": "number", "control": "slider", "min": 1, "max": 256, "step": 1}},
            "review": {"max": {"type": "number", "control": "slider", "min": 1, "max": 256, "step": 1}},
        },
    },
}

ENV_SETTING_MAPPINGS = {
    "workspace:base_dir": ("AGENTB_WORKSPACE_DIR", str),
    "mysql:host": ("MYSQL_HOST", str),
    "mysql:port": ("MYSQL_PORT", int),
    "mysql:user": ("MYSQL_USER", str),
    "mysql:password": ("MYSQL_PASSWORD", str),
    "mysql:database": ("MYSQL_DATABASE", str),
    "llm:api_key": ("LLM_API_KEY", str),
    "llm:base_url": ("LLM_BASE_URL", str),
    "llm:model": ("LLM_MODEL", str),
    "llm:temperature": ("LLM_TEMPERATURE", float),
    "llm:max_tokens": ("LLM_MAX_TOKENS", int),
    "agent_tools:sql:default_database": ("AGENT_SQL_DEFAULT_DATABASE", str),
    "agent_tools:sql:databases:BTManager:host": ("AGENT_SQL_BT_HOST", str),
    "agent_tools:sql:databases:BTManager:port": ("AGENT_SQL_BT_PORT", int),
    "agent_tools:sql:databases:BTManager:user": ("AGENT_SQL_BT_USER", str),
    "agent_tools:sql:databases:BTManager:password": ("AGENT_SQL_BT_PASSWORD", str),
}


def _cast_env_value(raw_value: str, caster):
    if caster is bool:
        return raw_value.strip().lower() in {"1", "true", "yes", "on"}
    return caster(raw_value)


def _set_nested_value(target: dict, key: str, value) -> None:
    parts = key.split(":")
    node = target
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _apply_env_overrides(current: dict) -> dict:
    merged = dict(current)
    for key, (env_name, caster) in ENV_SETTING_MAPPINGS.items():
        raw_value = os.getenv(env_name)
        if raw_value is None or raw_value == "":
            continue
        _set_nested_value(merged, key, _cast_env_value(raw_value, caster))
    return merged


class SettingsService:
    """设置服务层：解析配置文件并对外提供读取与修改接口。"""

    def __init__(self):
        self._fs = FileStorageSystem()
        self._fs.ensure_setting_file(DEFAULT_SETTINGS)
        self._reload()

    # ── 私有工具 ────────────────────────────────────────────────────────────────

    def _reload(self):
        data = self._fs.read_settings()
        merged, changed = _merge_missing_defaults(DEFAULT_SETTINGS, data)
        self._data = _apply_env_overrides(merged)
        _validate_runtime_limits(self._data)
        if changed:
            self._persist()

    def _persist(self):
        self._fs.write_settings(self._data)

    # ── 读取设置 ────────────────────────────────────────────────────────────────

    def get(self, key: str) -> str:
        """读取设置项，支持用 ':' 访问嵌套层级。

        Examples:
            get("apikey")           -> "your_api_key_here"
            get("groupA:settingA")  -> "valueA"

        Raises:
            KeyError: 键路径不存在时抛出。
        """
        parts = key.split(":")
        node = self._data
        for part in parts:
            if not isinstance(node, dict) or part not in node:
                raise KeyError(f"Setting key not found: '{key}'")
            node = node[part]
        return node

    def get_all(self) -> dict:
        """返回所有设置项的副本。"""
        return dict(self._data)

    def get_metadata(self) -> dict:
        """返回设置元数据。"""
        return dict(DEFAULT_SETTINGS_METADATA)

    # ── 修改设置 ────────────────────────────────────────────────────────────────

    def update_setting(self, key: str, value) -> bool:
        """修改单个顶层设置项并持久化。"""
        candidate = dict(self._data)
        candidate[key] = value
        _validate_runtime_limits(candidate)
        self._data = candidate
        self._persist()
        return True

    def update_settings(self, updates: dict) -> bool:
        """批量修改顶层设置项并持久化。"""
        candidate = dict(self._data)
        candidate.update(updates)
        _validate_runtime_limits(candidate)
        self._data = candidate
        self._persist()
        return True

    def reload(self):
        """从文件重新加载设置。"""
        self._reload()
