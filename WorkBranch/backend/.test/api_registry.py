#!/usr/bin/env python3
"""
API Registry - API 注册表加载和验证模块

用于静态验证测试代码的正确性
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple
import re

import yaml


class APIRegistry:
    """API 注册表管理器"""

    def __init__(self, registry_path: Optional[Path] = None):
        if registry_path is None:
            registry_path = Path(__file__).parent / "api_registry.yaml"
        self.registry_path = registry_path
        self._registry: Dict = {}
        self._load()

    def _load(self):
        """加载注册表"""
        if not self.registry_path.exists():
            raise FileNotFoundError(f"Registry file not found: {self.registry_path}")

        with open(self.registry_path, "r", encoding="utf-8") as f:
            self._registry = yaml.safe_load(f)

    def get_api(self, category: str, name: str) -> Optional[Dict]:
        """获取指定 API 的注册信息"""
        return self._registry.get("apis", {}).get(category, {}).get(name)

    def list_apis(self) -> List[Tuple[str, str, Dict]]:
        """列出所有注册的 API

        Returns:
            List of (category, name, info) tuples
        """
        apis = []
        for category, endpoints in self._registry.get("apis", {}).items():
            for name, info in endpoints.items():
                apis.append((category, name, info))
        return apis

    def get_expected_path(self, category: str, name: str) -> Optional[str]:
        """获取 API 的预期路径"""
        api = self.get_api(category, name)
        return api.get("path") if api else None

    def get_expected_method(self, category: str, name: str) -> Optional[str]:
        """获取 API 的预期 HTTP 方法"""
        api = self.get_api(category, name)
        return api.get("method") if api else None

    def get_param_types(self, category: str, name: str) -> Dict[str, str]:
        """获取 API 的参数类型定义"""
        api = self.get_api(category, name)
        return api.get("param_types", {}) if api else {}

    def extract_path_params(self, path: str) -> set:
        """从路径中提取 {param} 格式的占位符"""
        return set(re.findall(r'\{(\w+)\}', path))

    def validate_config(self, config: Dict) -> List[str]:
        """
        验证 test_config.yaml 中的 endpoints 是否都已在注册表中登记
        并检查路径是否匹配

        Returns:
            List[str]: 错误信息列表，为空表示验证通过
        """
        errors = []
        endpoints = config.get("api", {}).get("endpoints", {})

        for category, category_endpoints in endpoints.items():
            if not isinstance(category_endpoints, dict):
                continue

            for name, path in category_endpoints.items():
                if not isinstance(path, str):
                    errors.append(
                        f"[INVALID PATH] {category}.{name}: path must be string, got {type(path).__name__}"
                    )
                    continue

                registered = self.get_api(category, name)

                if registered is None:
                    errors.append(
                        f"[MISSING REGISTRY] {category}.{name} not found in api_registry.yaml"
                    )
                    continue

                # 检查路径是否匹配
                registered_path = registered.get("path", "")
                if registered_path != path:
                    errors.append(
                        f"[PATH MISMATCH] {category}.{name}: "
                        f"config='{path}', registry='{registered_path}'"
                    )

                # 检查方法是否存在
                method = registered.get("method", "").upper()
                valid_methods = {"GET", "POST", "PUT", "DELETE", "PATCH"}
                if method not in valid_methods:
                    errors.append(
                        f"[INVALID METHOD] {category}.{name}: method='{method}' not in {valid_methods}"
                    )

        return errors

    def validate_path_params(self, config: Dict) -> List[str]:
        """
        验证路径中的占位符是否在 param_types 中声明

        Returns:
            List[str]: 错误信息列表
        """
        errors = []
        endpoints = config.get("api", {}).get("endpoints", {})

        for category, category_endpoints in endpoints.items():
            if not isinstance(category_endpoints, dict):
                continue

            for name, path in category_endpoints.items():
                if not isinstance(path, str):
                    continue

                registered = self.get_api(category, name)
                if not registered:
                    continue

                # 提取路径中的占位符
                path_params = self.extract_path_params(path)

                # 获取声明的参数类型
                param_types = registered.get("param_types", {})

                # 检查每个占位符是否在 param_types 中声明
                for param in path_params:
                    param_lower = param.lower()
                    # 检查是否在 param_types 中（不区分大小写）
                    found = any(
                        p.lower() == param_lower
                        for p in param_types.keys()
                    )
                    if not found:
                        errors.append(
                            f"[MISSING PARAM TYPE] {category}.{name}: "
                            f"placeholder '{{{param}}}' not in param_types={list(param_types.keys())}"
                        )

        return errors

    def get_registry_summary(self) -> Dict:
        """获取注册表摘要统计"""
        apis = self.list_apis()
        categories = set(c for c, _, _ in apis)

        summary = {
            "total_apis": len(apis),
            "categories": len(categories),
            "category_details": {},
        }

        for category, _, _ in apis:
            count = sum(1 for c, _, _ in apis if c == category)
            summary["category_details"][category] = count

        return summary


def load_config(config_path: Optional[Path] = None) -> Dict:
    """加载测试配置文件"""
    if config_path is None:
        config_path = Path(__file__).parent / "test_config.yaml"

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_validation(registry_path: Optional[Path] = None, config_path: Optional[Path] = None) -> Tuple[bool, List[str]]:
    """
    运行完整的注册表验证

    Returns:
        Tuple of (success, errors)
    """
    errors = []

    try:
        registry = APIRegistry(registry_path)
        config = load_config(config_path)

        # 验证配置完整性
        config_errors = registry.validate_config(config)
        errors.extend(config_errors)

        # 验证路径参数
        param_errors = registry.validate_path_params(config)
        errors.extend(param_errors)

        return len(errors) == 0, errors

    except Exception as e:
        errors.append(f"[ERROR] Validation failed: {e}")
        return False, errors


if __name__ == "__main__":
    # 独立运行验证
    success, errors = run_validation()

    if success:
        print("[OK] API Registry validation passed")
    else:
        print("[FAIL] API Registry validation failed:")
        for error in errors:
            print(f"  - {error}")
        exit(1)