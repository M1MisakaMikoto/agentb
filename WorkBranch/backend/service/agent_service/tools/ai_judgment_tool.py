"""
AI 研判工具 - 用于向 AI 研判系统提交问题

工具名称: submit_ai_judgment_issue
功能: 将设施问题提交到 AI 研判系统，等待 AI 分析并返回研判结果

使用方法:
    submit_ai_judgment_issue(
        regionId="130117562645086249",
        facilityId=1001,
        facilityName="测试桥梁",
        title="桥面出现裂缝",
        description="裂缝约2米长，建议尽快处理"
    )

注意:
    - regionId 由调用方通过工具参数传入，会放入请求体中
    - 接口地址通过配置项 ai_judgment_api_url 设置，默认 http://localhost:8080
"""
import json
import logging
from typing import Optional, Dict, Any
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from .registry import ToolDefinition, ToolRegistry

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_API_URL = "http://localhost:8080"
DEFAULT_TIMEOUT = 30  # 超时时间（秒）


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
            # 兼容两种后端错误结构：
            #   1. 包装型: {"error": {"code":..., "message":...}}
            #   2. 扁平型: {"code":404, "message":"用户不存在"}
            error_info = error_data.get("error")
            if not isinstance(error_info, dict):
                error_info = error_data if isinstance(error_data, dict) else {}
            return {
                "success": False,
                "error": error_info,
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


def execute_submit_ai_judgment_issue(
    tool_args: dict,
    message_context: Optional[Dict[str, Any]] = None
) -> dict:
    """执行 AI 研判问题提交工具

    Args:
        tool_args: 工具参数，包含:
            - regionId: 区域ID (必需)
            - facilityId: 设施ID (必需)
            - facilityName: 设施名称 (必需)
            - title: 问题标题 (必需)
            - description: 问题描述 (可选)
        message_context: 消息上下文

    Returns:
        包含 result 或 error 的字典
    """
    # 提取参数
    region_id = tool_args.get("regionId")
    facility_id = tool_args.get("facilityId")
    facility_name = tool_args.get("facilityName")
    title = tool_args.get("title")
    description = tool_args.get("description", "")

    # 参数校验
    if not region_id:
        return {"result": None, "error": "缺少必需参数: regionId (区域ID)"}
    if not facility_id:
        return {"result": None, "error": "缺少必需参数: facilityId (设施ID)"}
    if not facility_name:
        return {"result": None, "error": "缺少必需参数: facilityName (设施名称)"}
    if not title:
        return {"result": None, "error": "缺少必需参数: title (问题标题)"}

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
        "regionId": region_id,
        "facilityId": facility_id,
        "facilityName": facility_name,
        "title": title,
    }
    if description:
        request_body["description"] = description

    # 构建 URL
    url = f"{api_url}/v1/ai-judgment/issues"

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
            # 补全失败日志：记录 http_status、完整 error_info、请求体关键字段，便于诊断真因
            http_status = response.get("http_status")
            logger.error(
                f"[AI 研判] 提交失败: {error_msg} | "
                f"http_status={http_status} | url={url} | "
                f"facilityId={facility_id} | regionId={region_id} | "
                f"error_info={error_info}"
            )
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
            params='submit_ai_judgment_issue:{"regionId":"(区域ID，必填)","facilityId":"(设施ID，必填)","facilityName":"(设施名称，必填)","title":"(问题标题，必填)","description":"(问题描述，可选)"}',
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