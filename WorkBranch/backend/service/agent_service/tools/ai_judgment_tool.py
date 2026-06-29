"""
AI 研判工具 - 用于向 AI 研判系统提交问题

工具名称: submit_ai_judgment_issue
功能: 将设施问题提交到 AI 研判系统，等待 AI 分析并返回研判结果

使用方法:
    submit_ai_judgment_issue(
        facilityId=1001,
        facilityName="测试桥梁",
        title="桥面出现裂缝",
        description="裂缝约2米长，建议尽快处理"
    )

注意:
    - userId 会自动从当前会话上下文获取，不需要手动传入
    - area_id 由服务端自动根据 userId 关联的 MarketId 赋值
    - 接口地址通过配置项 ai_judgment_api_url 设置，默认 http://localhost:8080
"""
import os
import json
import logging
from typing import Optional, Callable, Dict, Any
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from .registry import ToolDefinition, ToolRegistry

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_API_URL = "http://localhost:8080"
DEFAULT_TIMEOUT = 30  # 超时时间（秒）


def _get_user_id_from_context(message_context: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """从消息上下文中获取 userId

    Args:
        message_context: 消息上下文，包含 workspace_id 等信息

    Returns:
        userId 字符串，如果未找到则返回 None
    """
    if message_context:
        # 优先从 message_context 直接获取 user_id（由 agent_service 注入）
        user_id = message_context.get("user_id")
        if user_id:
            return str(user_id)

        # 从 settings_service 获取 userId
        settings_service = message_context.get("settings_service")
        if settings_service:
            try:
                user_id = settings_service.get("user:id")
                if user_id:
                    return str(user_id)
            except Exception:
                pass

        # 从 workspace_info 获取
        workspace_id = message_context.get("workspace_id")
        if workspace_id:
            workspace_service = message_context.get("workspace_service")
            if workspace_service:
                try:
                    info = workspace_service.get_workspace_info(workspace_id)
                    user_id = info.get("user_id") or info.get("userId")
                    if user_id:
                        return str(user_id)
                except Exception:
                    pass

    # 环境变量作为兜底
    env_user_id = os.environ.get("AI_JUDGMENT_USER_ID")
    if env_user_id:
        return env_user_id

    return None


def _send_http_request(
    url: str,
    method: str,
    body: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT
) -> Dict[str, Any]:
    """发送 HTTP 请求

    Args:
        url: 请求URL
        method: HTTP 方法 (GET, POST, etc.)
        body: 请求体字典，会序列化为 JSON
        headers: 请求头
        timeout: 超时时间（秒）

    Returns:
        响应数据字典

    Raises:
        HTTPError: HTTP 错误时抛出
    """
    default_headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
    }
    if headers:
        default_headers.update(headers)

    request_body = None
    if body:
        request_body = json.dumps(body, ensure_ascii=False).encode("utf-8")

    request = Request(
        url,
        data=request_body,
        headers=default_headers,
        method=method
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
            return json.loads(response_body)
    except HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        logger.error(f"HTTP Error {e.code}: {error_body}")
        try:
            error_data = json.loads(error_body)
            return {
                "success": False,
                "error": error_data.get("error", {}),
                "http_status": e.code
            }
        except json.JSONDecodeError:
            return {
                "success": False,
                "error": {
                    "code": e.code,
                    "message": error_body or str(e)
                },
                "http_status": e.code
            }
    except URLError as e:
        logger.error(f"URL Error: {e.reason}")
        return {
            "success": False,
            "error": {
                "code": -1,
                "message": f"网络请求失败: {e.reason}"
            }
        }


def _validate_region_id(
    agent_region_id: str,
    message_context: Optional[Dict[str, Any]] = None,
) -> tuple[bool, str]:
    """校验 agent 传入的 regionId 是否与原始元数据匹配

    Args:
        agent_region_id: agent 通过工具参数传入的 regionId
        message_context: 消息上下文，包含 handoff_metadata

    Returns:
        (是否通过, 错误信息)
    """
    if not message_context:
        logger.warning("[regionId 校验] 无 message_context，跳过校验")
        return True, ""

    metadata = message_context.get("handoff_metadata") or {}
    original_region_id = None

    # 从 handoff_metadata 中提取原始 regionId
    if isinstance(metadata, dict):
        original_region_id = metadata.get("regionId") or metadata.get("region_id")

    if not original_region_id:
        logger.warning("[regionId 校验] handoff_metadata 中无原始 regionId，跳过校验")
        return True, ""

    if str(agent_region_id).strip() != str(original_region_id).strip():
        error = f"regionId 不匹配: 期望 '{original_region_id}'，收到 '{agent_region_id}'"
        logger.error(f"[regionId 校验] {error}")
        return False, error

    logger.info(f"[regionId 校验] 通过: {agent_region_id}")
    return True, ""


def execute_submit_ai_judgment_issue(
    tool_args: dict,
    message_context: Optional[Dict[str, Any]] = None
) -> dict:
    """执行 AI 研判问题提交工具

    Args:
        tool_args: 工具参数，包含:
            - facilityId: 设施ID (必需)
            - facilityName: 设施名称 (必需)
            - title: 问题标题 (必需)
            - description: 问题描述 (可选)
            - regionId: 区域ID (必需，用于身份校验与接口调用)
        message_context: 消息上下文，用于获取原始元数据做 regionId 校验

    Returns:
        包含 result 或 error 的字典
    """
    # 提取参数
    facility_id = tool_args.get("facilityId")
    facility_name = tool_args.get("facilityName")
    title = tool_args.get("title")
    description = tool_args.get("description", "")
    region_id = tool_args.get("regionId")

    # 参数校验
    if not facility_id:
        return {"result": None, "error": "缺少必需参数: facilityId (设施ID)"}
    if not facility_name:
        return {"result": None, "error": "缺少必需参数: facilityName (设施名称)"}
    if not title:
        return {"result": None, "error": "缺少必需参数: title (问题标题)"}
    if not region_id:
        return {"result": None, "error": "缺少必需参数: regionId (区域ID)"}

    # regionId 校验：与原始元数据比对，防止 agent 错位/漏传
    passed, err_msg = _validate_region_id(region_id, message_context)
    if not passed:
        return {"result": None, "error": err_msg}

    # 构建请求（使用 regionId 作为用户标识）
    # 获取 API 地址（settings_service配置 > 硬编码默认值）
    api_url = DEFAULT_API_URL
    config_source = "硬编码默认值"
    settings_service = message_context.get("settings_service") if message_context else None
    if settings_service:
        try:
            api_url = settings_service.get("agent_tools:ai_judgment_api_url")
            config_source = "settings.json > agent_tools.ai_judgment_api_url"
        except KeyError:
            pass
    if api_url == DEFAULT_API_URL and settings_service:
        logger.info(f"[AI 研判] ⚠️ 使用默认地址，如需修改请在settings.json的agent_tools.ai_judgment_api_url配置")

    request_body = {
        "facilityId": facility_id,
        "facilityName": facility_name,
        "title": title,
    }
    if description:
        request_body["description"] = description

    # 构建 URL（regionId 作为查询参数，替代原有 userId）
    url = f"{api_url}/v1/ai-judgment/issues?userId={region_id}"

    logger.info(f"[AI 研判] 提交问题: {title}")
    logger.info(f"[AI 研判] 设施: {facility_name} (ID: {facility_id})")
    logger.info(f"[AI 研判] regionId: {region_id}")
    logger.info(f"[AI 研判] URL: {url} (来源: {config_source})")

    # 发送请求
    try:
        response = _send_http_request(url, "POST", request_body)

        if response.get("success"):
            data = response.get("data", {})
            result_message = f"""AI 研判问题提交成功！

📋 工单信息:
- 工单ID: {data.get('issueId')}
- 设施: {data.get('facilityName')} (ID: {data.get('facilityId')})
- 区域: {data.get('areaId')}
- 状态: {data.get('status')}
- 创建时间: {data.get('createdAt')}
- 消息: {data.get('message')}"""
            logger.info(f"[AI 研判] 提交成功: {data.get('issueId')}")
            return {"result": result_message, "error": None}
        else:
            error_info = response.get("error", {})
            error_msg = error_info.get("message", "未知错误")
            logger.error(f"[AI 研判] 提交失败: {error_msg}")
            return {"result": None, "error": f"提交失败: {error_msg}"}

    except Exception as e:
        error_msg = f"请求异常: {str(e)}"
        logger.error(f"[AI 研判] {error_msg}")
        return {"result": None, "error": error_msg}


def register_ai_judgment_tools():
    """注册 AI 研判相关工具"""
    tools = [
        ToolDefinition(
            name="submit_ai_judgment_issue",
            description="提交 AI 研判问题 - 将设施问题提交到 AI 研判系统进行分析。返回工单ID、区域、状态等信息。",
            params='submit_ai_judgment_issue:{"facilityId":"(设施ID，必填)","facilityName":"(设施名称，必填)","title":"(问题标题，必填)","description":"(问题描述，可选)","regionId":"(区域ID，必填)"}',
            category="ai_judgment",
            executor=execute_submit_ai_judgment_issue
        ),
    ]

    for tool in tools:
        ToolRegistry.register(tool)

    logger.info("AI 研判工具注册完成")


# 导出工具定义常量（方便其他地方引用）
AI_JUDGMENT_TOOLS = {"submit_ai_judgment_issue"}
AI_JUDGMENT_CATEGORY = "ai_judgment"