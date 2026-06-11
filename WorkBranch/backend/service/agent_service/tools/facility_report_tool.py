"""
设施研判报告工具 - 用于生成设施检测研判报告

工具名称: submit_facility_report
功能: 将检测报告信息上传到系统，然后生成研判报告

API 流程:
1. POST /v1/file - 上传报告文件信息，获得 reportId
2. POST /v1/facility/decision/report - 根据 reportId 生成研判报告

使用方法:
    submit_facility_report(
        reportName="2026年5月沪渝高速大桥定期检测报告",
        facilityId="BR-001",
        facilityName="沪渝高速大桥",
        reportFileUrl="/files/2026/05/report_001.pdf"
    )

注意:
    - regionId 由调用方通过工具参数传入，会通过 X-Region-Id 请求头发送
    - 接口地址通过配置项 facility_report_api_url 设置，默认 http://localhost:8001
"""
import os
import json
import logging
from typing import Optional, Dict, Any
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from .registry import ToolDefinition, ToolRegistry

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_API_URL = os.environ.get("FACILITY_REPORT_API_URL", "http://localhost:8001")
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
    env_user_id = os.environ.get("FACILITY_REPORT_USER_ID")
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


def execute_submit_facility_report(
    tool_args: dict,
    message_context: Optional[Dict[str, Any]] = None
) -> dict:
    """执行设施研判报告工具

    Args:
        tool_args: 工具参数，包含:
            - reportName: 报告名称 (必需)
            - facilityId: 设施ID (必需)
            - facilityName: 设施名称 (必需)
            - reportFileUrl: 报告文件URL (必需)
            - regionId: 区域ID (必需，通过 X-Region-Id 请求头发送)
        message_context: 消息上下文（当前未使用，保留兼容）

    Returns:
        包含 result 或 error 的字典
    """
    # 提取参数
    report_name = tool_args.get("reportName")
    facility_id = tool_args.get("facilityId")
    facility_name = tool_args.get("facilityName")
    report_file_url = tool_args.get("reportFileUrl")
    region_id = tool_args.get("regionId")

    # 参数校验
    if not report_name:
        return {"result": None, "error": "缺少必需参数: reportName (报告名称)"}
    if not facility_id:
        return {"result": None, "error": "缺少必需参数: facilityId (设施ID)"}
    if not facility_name:
        return {"result": None, "error": "缺少必需参数: facilityName (设施名称)"}
    if not report_file_url:
        return {"result": None, "error": "缺少必需参数: reportFileUrl (报告文件URL)"}
    if not region_id:
        return {"result": None, "error": "缺少必需参数: regionId (区域ID)"}

    # 构建请求头（regionId 通过 X-Region-Id 传递）
    region_headers = {"X-Region-Id": str(region_id)}

    # 获取 API 地址
    api_url = tool_args.get("api_url") or os.environ.get("FACILITY_REPORT_API_URL") or DEFAULT_API_URL

    logger.info(f"[设施研判报告] 开始处理报告: {report_name}")
    logger.info(f"[设施研判报告] 设施: {facility_name} (ID: {facility_id})")
    logger.info(f"[设施研判报告] regionId: {region_id}")
    logger.info(f"[设施研判报告] API地址: {api_url}")

    # ========== 步骤1: 上传报告文件信息 ==========
    file_url = f"{api_url}/v1/file"
    file_request_body = {
        "reportName": report_name,
        "facilityId": facility_id,
        "facilityName": facility_name,
        "reportFileUrl": report_file_url,
    }

    logger.info(f"[设施研判报告] 步骤1/2 - 上传报告文件信息到: {file_url}")

    try:
        file_response = _send_http_request(file_url, "POST", file_request_body, headers=region_headers)
    except Exception as e:
        error_msg = f"步骤1失败 - 上传报告文件异常: {str(e)}"
        logger.error(f"[设施研判报告] {error_msg}")
        return {"result": None, "error": error_msg}

    if not file_response.get("success"):
        error_info = file_response.get("error", {})
        error_msg = error_info.get("message", "未知错误")
        logger.error(f"[设施研判报告] 步骤1失败: {error_msg}")
        return {"result": None, "error": f"上传报告文件失败: {error_msg}"}

    file_data = file_response.get("data", {})
    report_id = file_data.get("reportId")

    logger.info(f"[设施研判报告] 步骤1完成 - 获得 reportId: {report_id}")

    # ========== 步骤2: 生成研判报告 ==========
    decision_url = f"{api_url}/v1/facility/decision/report"
    decision_request_body = {
        "reportId": report_id,
    }

    logger.info(f"[设施研判报告] 步骤2/2 - 生成研判报告: {decision_url}")

    try:
        decision_response = _send_http_request(decision_url, "POST", decision_request_body, headers=region_headers)
    except Exception as e:
        error_msg = f"步骤2失败 - 生成研判报告异常: {str(e)}"
        logger.error(f"[设施研判报告] {error_msg}")
        return {"result": None, "error": error_msg}

    if not decision_response.get("success"):
        error_info = decision_response.get("error", {})
        error_msg = error_info.get("message", "未知错误")
        logger.error(f"[设施研判报告] 步骤2失败: {error_msg}")
        return {"result": None, "error": f"生成研判报告失败: {error_msg}"}

    # ========== 成功 - 汇总结果 ==========
    decision_data = decision_response.get("data", {})
    result_message = f"""设施研判报告生成成功！

📋 报告信息:
- 报告名称: {report_name}
- 报告ID: {report_id}
- 设施: {facility_name} (ID: {facility_id})

📋 研判决策:
- 决策ID: {decision_data.get('decisionId')}
- 状态: {decision_data.get('status')}
- 生成时间: {decision_data.get('generatedAt')}
- 消息: {decision_data.get('message')}"""

    logger.info(f"[设施研判报告] 全部完成 - decisionId: {decision_data.get('decisionId')}")
    return {"result": result_message, "error": None}


def register_facility_report_tools():
    """注册设施研判报告相关工具"""
    tools = [
        ToolDefinition(
            name="submit_facility_report",
            description="生成设施研判报告 - 将检测报告上传后自动生成研判报告。串联 /v1/file 和 /v1/facility/decision/report 两个接口。",
            params='submit_facility_report:{"reportName":"(报告名称，必填)","facilityId":"(设施ID，必填)","facilityName":"(设施名称，必填)","reportFileUrl":"(报告文件URL，必填)","regionId":"(区域ID，必填)","api_url":"(API地址，可选)"}',
            category="facility_report",
            executor=execute_submit_facility_report
        ),
    ]

    for tool in tools:
        ToolRegistry.register(tool)

    logger.info("设施研判报告工具注册完成")


# 导出工具定义常量（方便其他地方引用）
FACILITY_REPORT_TOOLS = {"submit_facility_report"}
FACILITY_FORECAST_TOOLS = {"submit_facility_forecast"}
ALL_FACILITY_TOOLS = FACILITY_REPORT_TOOLS | FACILITY_FORECAST_TOOLS
FACILITY_REPORT_CATEGORY = "facility_report"


# ==================== 预测报告工具 ====================

def execute_submit_facility_forecast_report(
    tool_args: dict,
    message_context: Optional[Dict[str, Any]] = None
) -> dict:
    """执行设施预测报告提交工具

    Args:
        tool_args: 工具参数，包含:
            - facilityId: 设施ID (必需)
            - predictYear: 预测年份 (必需)
            - facilityName: 设施名称 (可选)
            - reportUrl: 报告文件地址 (可选)
            - predictedHealthScore: 预测健康分数 (可选)
            - predictedRiskLevel: 风险等级 (可选，如高/中/低风险)
            - summary: 预测结论摘要 (可选)
        message_context: 消息上下文

    Returns:
        包含 result 或 error 的字典
    """
    # 提取必填参数
    facility_id = tool_args.get("facilityId")
    predict_year = tool_args.get("predictYear")

    if not facility_id:
        return {"result": None, "error": "缺少必需参数: facilityId (设施ID)"}
    if not predict_year:
        return {"result": None, "error": "缺少必需参数: predictYear (预测年份)"}

    # 获取 userId
    user_id = _get_user_id_from_context(message_context)
    if not user_id:
        return {"result": None, "error": "无法获取用户ID，请确保消息上下文包含用户信息"}

    # 构造请求体（只传业务字段，areaId 由服务端自动写入）
    request_body = {
        "facilityId": facility_id,
        "predictYear": int(predict_year),
    }

    # 可选字段按需添加
    optional_fields = {
        "facilityName": tool_args.get("facilityName"),
        "reportUrl": tool_args.get("reportUrl"),
        "predictedHealthScore": tool_args.get("predictedHealthScore"),
        "predictedRiskLevel": tool_args.get("predictedRiskLevel"),
        "summary": tool_args.get("summary"),
    }
    for key, value in optional_fields.items():
        if value is not None:
            request_body[key] = value

    # 获取 API 地址
    api_url = tool_args.get("api_url") or os.environ.get("FACILITY_REPORT_API_URL") or DEFAULT_API_URL
    forecast_url = f"{api_url}/v1/facility/forecast/report"

    # 构建认证头
    auth_headers = {"X-User-Id": str(user_id)}

    logger.info(f"[设施预测报告] 开始提交预测报告")
    logger.info(f"[设施预测报告] 设施ID: {facility_id}, 年份: {predict_year}")
    logger.info(f"[设施预测报告] 请求URL: {forecast_url}")

    try:
        response = _send_http_request(forecast_url, "POST", request_body, headers=auth_headers)
    except Exception as e:
        error_msg = f"提交预测报告异常: {str(e)}"
        logger.error(f"[设施预测报告] {error_msg}")
        return {"result": None, "error": error_msg}

    if response.get("http_status") == 400:
        return {"result": None, "error": response.get("error", {}).get("message", "请求参数错误或用户未分配区域")}
    elif response.get("http_status") == 401:
        return {"result": None, "error": "未登录或认证失败"}

    report_id = response.get("data") or response.get("result")

    result_message = f"""设施预测报告提交成功！

📋 报告信息:
- 报告ID: {report_id}
- 设施ID: {facility_id}
- 预测年份: {predict_year}"""

    logger.info(f"[设施预测报告] 提交成功 - ID: {report_id}")
    return {"result": result_message, "error": None}


# 更新注册函数
def register_facility_report_tools():
    """注册设施研判报告及预测报告相关工具"""
    tools = [
        ToolDefinition(
            name="submit_facility_report",
            description="生成设施研判报告 - 将检测报告上传后自动生成研判报告。串联 /v1/file 和 /v1/facility/decision/report 两个接口。",
            params='submit_facility_report:{"reportName":"(报告名称，必填)","facilityId":"(设施ID，必填)","facilityName":"(设施名称，必填)","reportFileUrl":"(报告文件URL，必填)","regionId":"(区域ID，必填)","api_url":"(API地址，可选)"}',
            category="facility_report",
            executor=execute_submit_facility_report
        ),
        ToolDefinition(
            name="submit_facility_forecast",
            description="提交设施预测报告 - 将桥梁预测分析结果上传到系统。调用 POST /v1/facility/forecast/report 接口。",
            params='submit_facility_forecast:{"facilityId":"(设施ID，必填)","predictYear":"(预测年份，必填)","facilityName":"(设施名称，可选)","reportUrl":"(报告文件地址，可选)","predictedHealthScore":"(预测健康分数，可选)","predictedRiskLevel":"(风险等级，可选: 高/中/低)","summary":"(预测结论摘要，可选)","api_url":"(API地址，可选)"}',
            category="facility_report",
            executor=execute_submit_facility_forecast_report
        ),
    ]

    for tool in tools:
        ToolRegistry.register(tool)

    logger.info("设施报告工具注册完成（含研判+预测）")