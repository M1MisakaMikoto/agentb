import os

from data.file_storage_system import FileStorageSystem


def _merge_missing_defaults(defaults, current):
    """Deep-merge defaults into current, only filling missing keys.

    Returns: (merged, changed)
    """
    if not isinstance(defaults, dict):
        return current, False

    if not isinstance(current, dict):
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
            next_value, nested_changed = _merge_missing_defaults(default_value, current_value)
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
        "tool_timeout_seconds": 300,
        "special_tool_timeout_seconds": 600
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
    }
}

ENV_SETTING_MAPPINGS = {
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
        self._data[key] = value
        self._persist()
        return True

    def update_settings(self, updates: dict) -> bool:
        """批量修改顶层设置项并持久化。"""
        self._data.update(updates)
        self._persist()
        return True

    def reload(self):
        """从文件重新加载设置。"""
        self._reload()
