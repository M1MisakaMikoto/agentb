"""
设施研判报告工具 - 用于生成设施检测研判报告

工具名称: submit_facility_report
功能: 上传 PDF 文件后生成研判报告

API 流程（两步）:
1. POST /v1/file/upload - 上传 PDF 文件，获得 fileUrl
2. POST /v1/facility/decision/report - 用 fileUrl + 业务字段生成研判报告

预测报告工具名称: submit_facility_forecast
功能: 上传 PDF 文件后提交预测数据

API 流程（两步）:
1. POST /v1/file/upload - 上传 PDF 文件，获得 fileUrl
2. POST /v1/facility/forecast/report - 用 fileUrl 提交预测数据

使用方法:
    submit_facility_report(
        reportName="2026年5月沪渝高速大桥定期检测报告",
        facilityId="BR-001",
        facilityName="沪渝高速大桥",
        reportFile="/path/to/report.pdf",
        regionId="region-001"
    )

注意:
    - regionId 由调用方通过工具参数传入，会通过 X-Region-Id 请求头发送
    - 接口地址通过配置项 facility_report_api_url 设置，默认 http://localhost:8001
"""
import os
import json
import logging
import uuid
from typing import Optional, Dict, Any
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from http.client import HTTPConnection
import mimetypes

from .registry import ToolDefinition, ToolRegistry

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_API_URL = "http://localhost:8001"
DEFAULT_TIMEOUT = 30  # 超时时间（秒）


def _resolve_report_file_path(
    report_file: str,
    message_context: Optional[Dict[str, Any]] = None,
) -> str:
    """将 reportFile 相对路径解析为工作区内的绝对路径

    Args:
        report_file: agent 传入的报告文件路径
        message_context: 消息上下文，含 workspace_id / workspace_service

    Returns:
        解析后的绝对路径（原始路径若为绝对路径且存在则原样返回；
        若无法解析则返回原始路径，由后续校验处理）
    """
    if not report_file:
        return report_file

    # 已是绝对路径：不再解析，仅记录
    if os.path.isabs(report_file):
        exists = os.path.exists(report_file)
        logger.info(
            f"[路径解析] 输入=绝对路径, 值={report_file}, "
            f"解析=否, 存在={exists}"
        )
        return report_file

    # 相对路径：尝试拼接工作区目录
    workspace_id = message_context.get("workspace_id") if message_context else None
    workspace_service = message_context.get("workspace_service") if message_context else None

    if workspace_id and workspace_service:
        try:
            allowed, resolved = workspace_service.resolve_path(workspace_id, report_file)
            if allowed:
                logger.info(
                    f"[路径解析] 输入=相对路径, 值={report_file}, "
                    f"解析=是, 结果={resolved}, 存在={os.path.exists(resolved)}"
                )
                return resolved
            else:
                logger.warning(
                    f"[路径解析] 输入=相对路径, 值={report_file}, "
                    f"解析=失败({resolved})"
                )
        except Exception as e:
            logger.warning(
                f"[路径解析] 输入=相对路径, 值={report_file}, "
                f"解析=异常({e})"
            )
    else:
        logger.info(
            f"[路径解析] 输入=相对路径, 值={report_file}, "
            f"解析=否(无 workspace 上下文)"
        )

    return report_file


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


def _send_multipart_upload(
    url: str,
    file_path: str,
    field_name: str = "file",
    headers: Optional[Dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT
) -> Dict[str, Any]:
    """发送 multipart/form-data 文件上传请求

    Args:
        url: 请求URL
        file_path: 本地文件路径
        field_name: 表单字段名，默认 "file"
        headers: 额外请求头
        timeout: 超时时间（秒）

    Returns:
        响应数据字典
    """
    if not os.path.exists(file_path):
        return {
            "success": False,
            "error": {"code": -1, "message": f"文件不存在: {file_path}"}
        }

    boundary = uuid.uuid4().hex
    content_type = f"multipart/form-data; boundary={boundary}"

    # 构建 multipart body
    with open(file_path, "rb") as f:
        file_data = f.read()

    filename = os.path.basename(file_path)
    mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

    body_parts = []
    body_parts.append(f"--{boundary}".encode())
    body_parts.append(
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"'.encode()
    )
    body_parts.append(f"Content-Type: {mime_type}".encode())
    body_parts.append(b"")
    body_parts.append(file_data)
    body_parts.append(f"--{boundary}--".encode())

    request_body = b"\r\n".join(body_parts)

    default_headers = {
        "Content-Type": content_type,
        "Accept": "application/json",
    }
    if headers:
        default_headers.update(headers)

    request = Request(
        url,
        data=request_body,
        headers=default_headers,
        method="POST"
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
            return json.loads(response_body)
    except HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        logger.error(f"[Upload] HTTP Error {e.code}: {error_body}")
        try:
            error_data = json.loads(error_body)
            return {"success": False, "error": error_data.get("error", {}), "http_status": e.code}
        except json.JSONDecodeError:
            return {"success": False, "error": {"code": e.code, "message": error_body or str(e)}, "http_status": e.code}
    except URLError as e:
        logger.error(f"[Upload] URL Error: {e.reason}")
        return {"success": False, "error": {"code": -1, "message": f"上传失败: {e.reason}"}}
    except Exception as e:
        logger.error(f"[Upload] 异常: {e}")
        return {"success": False, "error": {"code": -1, "message": f"上传异常: {str(e)}"}}


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


def execute_submit_facility_report(
    tool_args: dict,
    message_context: Optional[Dict[str, Any]] = None
) -> dict:
    """执行设施研判报告工具（两步流程）

    步骤1: POST /v1/file/upload - 上传 PDF 文件，获得 fileUrl
    步骤2: POST /v1/facility/decision/report - 用 fileUrl + 业务字段生成研判报告

    Args:
        tool_args: 工具参数，包含:
            - reportName: 报告名称 (必需)
            - facilityId: 设施ID (必需)
            - facilityName: 设施名称 (必需)
            - reportFile: 报告PDF文件本地路径 (必需)
            - regionId: 区域ID (必需)
        message_context: 消息上下文

    Returns:
        包含 result 或 error 的字典
    """
    # 提取参数
    report_name = tool_args.get("reportName")
    facility_id = tool_args.get("facilityId")
    facility_name = tool_args.get("facilityName")
    report_file = tool_args.get("reportFile")
    region_id = tool_args.get("regionId")

    # 参数校验
    if not report_name:
        return {"result": None, "error": "缺少必需参数: reportName (报告名称)"}
    if not facility_id:
        return {"result": None, "error": "缺少必需参数: facilityId (设施ID)"}
    if not facility_name:
        return {"result": None, "error": "缺少必需参数: facilityName (设施名称)"}
    if not report_file:
        return {"result": None, "error": "缺少必需参数: reportFile (报告PDF文件路径)"}
    if not region_id:
        return {"result": None, "error": "缺少必需参数: regionId (区域ID)"}

    # 解析 reportFile 相对路径到工作区绝对路径
    report_file = _resolve_report_file_path(report_file, message_context)

    # regionId 校验：与原始元数据比对，防止 agent 错位/漏传
    passed, err_msg = _validate_region_id(region_id, message_context)
    if not passed:
        return {"result": None, "error": err_msg}

    # 构建请求头（regionId 通过 X-Region-Id 传递）
    region_headers = {"X-Region-Id": str(region_id)}

    # 获取 API 地址（settings_service配置 > 硬编码默认值）
    api_url = DEFAULT_API_URL
    config_source = "硬编码默认值"
    settings_service = message_context.get("settings_service") if message_context else None
    if settings_service:
        try:
            api_url = settings_service.get("agent_tools:facility_report_api_url")
            config_source = "settings.json > agent_tools.facility_report_api_url"
        except KeyError:
            pass
    if api_url == DEFAULT_API_URL and settings_service:
        logger.info(f"[设施研判报告] ⚠️ 使用默认地址，如需修改请在settings.json的agent_tools.facility_report_api_url配置")

    logger.info(f"[设施研判报告] 开始处理报告: {report_name}")
    logger.info(f"[设施研判报告] 设施: {facility_name} (ID: {facility_id})")
    logger.info(f"[设施研判报告] 文件: {report_file}")
    logger.info(f"[设施研判报告] regionId: {region_id}")
    logger.info(f"[设施研判报告] API地址: {api_url} (来源: {config_source})")

    # ========== 步骤1: 上传 PDF 文件 ==========
    upload_url = f"{api_url}/v1/file/upload"

    logger.info(f"[设施研判报告] 步骤1/2 - 上传PDF文件到: {upload_url}")

    upload_response = _send_multipart_upload(upload_url, report_file, headers=region_headers)

    if not upload_response.get("success"):
        error_info = upload_response.get("error", {})
        error_msg = error_info.get("message", "未知错误")
        logger.error(f"[设施研判报告] 步骤1失败: {error_msg}")
        return {"result": None, "error": f"上传PDF失败: {error_msg}"}

    file_url = upload_response.get("data", {}).get("fileUrl")
    if not file_url:
        logger.error("[设施研判报告] 步骤1响应中无 fileUrl")
        return {"result": None, "error": "上传成功但未返回 fileUrl"}

    logger.info(f"[设施研判报告] 步骤1完成 - 获得 fileUrl: {file_url}")

    # ========== 步骤2: 生成研判报告 ==========
    decision_url = f"{api_url}/v1/facility/decision/report"
    decision_request_body = {
        "regionId": region_id,
        "reportName": report_name,
        "facilityId": facility_id,
        "facilityName": facility_name,
        "reportFileUrl": file_url,
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
- 文件URL: {file_url}
- 设施: {facility_name} (ID: {facility_id})

📋 研判决策:
- 决策ID: {decision_data.get('decisionId')}
- 状态: {decision_data.get('status')}
- 生成时间: {decision_data.get('generatedAt')}
- 消息: {decision_data.get('message')}"""

    logger.info(f"[设施研判报告] 全部完成 - decisionId: {decision_data.get('decisionId')}")
    return {"result": result_message, "error": None}


# 更新注册函数（统一注册研判+预测）
def register_facility_report_tools():
    """注册设施研判报告及预测报告相关工具"""
    tools = [
        ToolDefinition(
            name="submit_facility_report",
            description="生成设施研判报告 - 两步流程：先上传PDF文件获得fileUrl，再提交业务数据生成研判报告。串联 /v1/file/upload 和 /v1/facility/decision/report 两个接口。注意：若尚无PDF文件，先用 document w 工具生成PDF（传入Markdown内容即可自动转换为PDF）。",
            params='submit_facility_report:{"reportName":"(报告名称，必填)","facilityId":"(设施ID，必填)","facilityName":"(设施名称，必填)","reportFile":"(报告PDF文件本地路径，必填)","regionId":"(区域ID，必填)"}',
            category="facility_report",
            executor=execute_submit_facility_report
        ),
        ToolDefinition(
            name="submit_facility_forecast",
            description="提交设施预测报告 - 两步流程：先上传PDF文件获得fileUrl，再提交预测数据。串联 /v1/file/upload 和 /v1/facility/forecast/report 两个接口。注意：若尚无PDF文件，先用 document w 工具生成PDF（传入Markdown内容即可自动转换为PDF）。",
            params='submit_facility_forecast:{"regionId":"(区域ID，必填)","facilityId":"(设施ID，必填)","predictYear":"(预测年份，必填)","reportFile":"(报告PDF文件本地路径，必填)","facilityName":"(设施名称，可选)","predictedHealthScore":"(预测健康分数，可选)","predictedRiskLevel":"(风险等级，可选: HIGH/MEDIUM/LOW)","summary":"(预测结论摘要，可选)"}',
            category="facility_report",
            executor=execute_submit_facility_forecast_report
        ),
    ]

    for tool in tools:
        ToolRegistry.register(tool)

    logger.info("设施报告工具注册完成（含研判+预测）")


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
    """执行设施预测报告提交工具（两步流程）

    步骤1: POST /v1/file/upload - 上传 PDF 文件，获得 fileUrl
    步骤2: POST /v1/facility/forecast/report - 用 fileUrl 作为 reportUrl 提交预测数据

    Args:
        tool_args: 工具参数，包含:
            - regionId: 区域ID (必需)
            - facilityId: 设施ID (必需)
            - predictYear: 预测年份 (必需)
            - reportFile: 报告PDF文件本地路径 (必需)
            - facilityName: 设施名称 (可选)
            - predictedHealthScore: 预测健康分数 (可选)
            - predictedRiskLevel: 风险等级 (可选，如 HIGH/MEDIUM/LOW)
            - summary: 预测结论摘要 (可选)
        message_context: 消息上下文

    Returns:
        包含 result 或 error 的字典
    """
    # 提取必填参数
    region_id = tool_args.get("regionId")
    facility_id = tool_args.get("facilityId")
    predict_year = tool_args.get("predictYear")
    report_file = tool_args.get("reportFile")

    if not region_id:
        return {"result": None, "error": "缺少必需参数: regionId (区域ID)"}
    if not facility_id:
        return {"result": None, "error": "缺少必需参数: facilityId (设施ID)"}
    if not predict_year:
        return {"result": None, "error": "缺少必需参数: predictYear (预测年份)"}
    if not report_file:
        return {"result": None, "error": "缺少必需参数: reportFile (报告PDF文件路径)"}

    # 解析 reportFile 相对路径到工作区绝对路径
    report_file = _resolve_report_file_path(report_file, message_context)

    # regionId 校验：与原始元数据比对，防止 agent 错位/漏传
    passed, err_msg = _validate_region_id(region_id, message_context)
    if not passed:
        return {"result": None, "error": err_msg}

    # 获取 API 地址（settings_service配置 > 硬编码默认值）
    api_url = DEFAULT_API_URL
    config_source = "硬编码默认值"
    settings_service = message_context.get("settings_service") if message_context else None
    if settings_service:
        try:
            api_url = settings_service.get("agent_tools:facility_report_api_url")
            config_source = "settings.json > agent_tools.facility_report_api_url"
        except KeyError:
            pass
    if api_url == DEFAULT_API_URL and settings_service:
        logger.info(f"[设施预测报告] ⚠️ 使用默认地址，如需修改请在settings.json的agent_tools.facility_report_api_url配置")

    logger.info(f"[设施预测报告] 开始处理预测报告")
    logger.info(f"[设施预测报告] 区域ID: {region_id}, 设施ID: {facility_id}, 年份: {predict_year}")
    logger.info(f"[设施预测报告] 文件: {report_file}")
    logger.info(f"[设施预测报告] API地址: {api_url} (来源: {config_source})")

    # ========== 步骤1: 上传 PDF 文件 ==========
    upload_url = f"{api_url}/v1/file/upload"

    logger.info(f"[设施预测报告] 步骤1/2 - 上传PDF文件到: {upload_url}")

    upload_response = _send_multipart_upload(upload_url, report_file)

    if not upload_response.get("success"):
        error_info = upload_response.get("error", {})
        error_msg = error_info.get("message", "未知错误")
        logger.error(f"[设施预测报告] 步骤1失败: {error_msg}")
        return {"result": None, "error": f"上传PDF失败: {error_msg}"}

    file_url = upload_response.get("data", {}).get("fileUrl")
    if not file_url:
        logger.error("[设施预测报告] 步骤1响应中无 fileUrl")
        return {"result": None, "error": "上传成功但未返回 fileUrl"}

    logger.info(f"[设施预测报告] 步骤1完成 - 获得 fileUrl: {file_url}")

    # ========== 步骤2: 提交预测报告 ==========
    forecast_url = f"{api_url}/v1/facility/forecast/report"
    request_body = {
        "regionId": region_id,
        "facilityId": facility_id,
        "predictYear": int(predict_year),
        "reportUrl": file_url,
    }

    # 可选字段按需添加
    optional_fields = {
        "facilityName": tool_args.get("facilityName"),
        "predictedHealthScore": tool_args.get("predictedHealthScore"),
        "predictedRiskLevel": tool_args.get("predictedRiskLevel"),
        "summary": tool_args.get("summary"),
    }
    for key, value in optional_fields.items():
        if value is not None:
            request_body[key] = value

    logger.info(f"[设施预测报告] 步骤2/2 - 提交预测数据: {forecast_url}")

    try:
        response = _send_http_request(forecast_url, "POST", request_body)
    except Exception as e:
        error_msg = f"步骤2失败 - 提交预测报告异常: {str(e)}"
        logger.error(f"[设施预测报告] {error_msg}")
        return {"result": None, "error": error_msg}

    if response.get("http_status") == 400:
        return {"result": None, "error": response.get("error", {}).get("message", "请求参数错误或用户未分配区域")}
    elif response.get("http_status") == 401:
        return {"result": None, "error": "未登录或认证失败"}
    elif not response.get("success") and response.get("error"):
        error_info = response.get("error", {})
        error_msg = error_info.get("message", "未知错误")
        return {"result": None, "error": f"提交预测报告失败: {error_msg}"}

    report_id = response.get("data") or response.get("result")

    result_message = f"""设施预测报告提交成功！

📋 报告信息:
- 报告ID: {report_id}
- 设施ID: {facility_id}
- 预测年份: {predict_year}
- 文件URL: {file_url}"""

    logger.info(f"[设施预测报告] 全部完成 - ID: {report_id}")
    return {"result": result_message, "error": None}