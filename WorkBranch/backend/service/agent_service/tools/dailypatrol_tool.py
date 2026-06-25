"""
日常巡查记录工具 - 用于提交日常巡查任务记录（无需token版本）

工具名称: submit_dailypatrol_record
功能: 提交日常巡查记录数据到后端接口

API 流程:
POST /dailypatrol/agent/add - Agent 回写巡查记录
认证: agent-secret-key: daily-patrol-agent

使用方法:
    submit_dailypatrol_record(
        title="沪渝高速大桥日常巡查",
        xcdate=1718582400000,
        typeid=1,
        typename="桥梁",
        nameid=1001,
        ssname="沪渝高速大桥",
        xcunitname="市政养护一队",
        dq=310101,
        isdjrw=0,
        isyhby=1,
        xcperson="张三",
        xcphone="13800138000",
        xcunitid=2001,
        dtoList=[{
            "testingitemid": "桥面系",
            "testingsubitem": "铺装层",
            "testingstatus": "裂缝",
            "opinion": "建议修复",
            "estimate": "5.5",
            "dw": "m²"
        }]
    )
"""

import json
import logging
import os
from typing import Optional, Dict, Any, List

from .registry import ToolDefinition, ToolRegistry

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_API_URL = "http://localhost:8002"
DEFAULT_TIMEOUT = 30  # 超时时间（秒）

# Agent 认证密钥
AGENT_SECRET_KEY = "daily-patrol-agent"


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
    """
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError

    default_headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
    }
    if headers:
        default_headers.update(headers)

    request_body = None
    if body:
        request_body = json.dumps(body, ensure_ascii=False).encode("utf-8")

    request = Request(url, data=request_body, headers=default_headers, method=method)

    try:
        with urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
            return json.loads(response_body)
    except HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        logger.error(f"HTTP Error {e.code}: {error_body}")
        try:
            error_data = json.loads(error_body)
            return {"success": False, "error": error_data.get("error", {}), "http_status": e.code}
        except json.JSONDecodeError:
            return {"success": False, "error": {"code": e.code, "message": error_body or str(e)}, "http_status": e.code}
    except URLError as e:
        logger.error(f"URL Error: {e.reason}")
        return {"success": False, "error": {"code": -1, "message": f"网络请求失败: {e.reason}"}}
    except Exception as e:
        logger.error(f"请求异常: {e}")
        return {"success": False, "error": {"code": -1, "message": f"请求异常: {str(e)}"}}


def execute_submit_dailypatrol_record(
    tool_args: dict,
    message_context: Optional[Dict[str, Any]] = None
) -> dict:
    """执行日常巡查记录提交工具

    POST /dailypatrol/agent/add 提交巡查记录
    认证: agent-secret-key: daily-patrol-agent

    Args:
        tool_args: 工具参数，包含 DailypatrolNoAuthModel 所有字段
        message_context: 消息上下文

    Returns:
        包含 result 或 error 的字典
    """
    # ========== 提取必填参数 ==========
    title = tool_args.get("title")
    xcdate = tool_args.get("xcdate")
    typeid = tool_args.get("typeid")
    typename = tool_args.get("typename")
    nameid = tool_args.get("nameid")
    ssname = tool_args.get("ssname")
    xcunitname = tool_args.get("xcunitname")
    dq = tool_args.get("dq")
    isdjrw = tool_args.get("isdjrw")
    isyhby = tool_args.get("isyhby")
    xcperson = tool_args.get("xcperson") or tool_args.get("realName")  # 兼容旧参数名
    xcphone = tool_args.get("xcphone") or tool_args.get("mobile")  # 兼容旧参数名
    xcunitid = tool_args.get("xcunitid") or tool_args.get("orgId")  # 兼容旧参数名

    # ========== 参数校验 ==========
    required_fields = {
        "title (巡查标题)": title,
        "xcdate (巡查日期)": xcdate,
        "typeid (设施类型)": typeid,
        "typename (设施类型名称)": typename,
        "nameid (设施名称ID)": nameid,
        "ssname (设施名称)": ssname,
        "xcunitname (巡查单位名称)": xcunitname,
        "dq (地区)": dq,
        "isdjrw (是否定检任务)": isdjrw,
        "isyhby (是否需要养护保养)": isyhby,
        "xcperson (巡查人姓名)": xcperson,
        "xcphone (巡查人电话)": xcphone,
        "xcunitid (巡查单位ID)": xcunitid,
    }

    for field_name, value in required_fields.items():
        if not value and value != 0:
            return {"result": None, "error": f"缺少必需参数: {field_name}"}

    # ========== 构建请求体 ==========
    request_body = {
        # 必填字段 - 使用 Java DTO 字段名
        "title": title,
        "xcdate": xcdate,
        "typeid": typeid,
        "typename": typename,
        "nameid": nameid,
        "ssname": ssname,
        "xcunitname": xcunitname,
        "dq": dq,
        "isdjrw": isdjrw,
        "isyhby": isyhby,
        "xcperson": xcperson,
        "xcphone": xcphone,
        "xcunitid": xcunitid,
    }

    # 可选字段映射
    optional_fields = [
        "userId", "status", "remark", "source", "dzdtisvalid", "dzdt",
        "xcbegintime", "xcendtime", "checktodate", "photoannex",
        "qrdzdt", "reveal", "videoModel"
    ]

    for field in optional_fields:
        if field in tool_args and tool_args[field] is not None:
            request_body[field] = tool_args[field]

    # 处理检测指标明细列表
    if "dtoList" in tool_args and tool_args["dtoList"]:
        dto_list = tool_args["dtoList"]
        if not isinstance(dto_list, list):
            return {"result": None, "error": "dtoList 必须是列表类型"}

        processed_dto_list = []
        for idx, dto in enumerate(dto_list):
            if not isinstance(dto, dict):
                return {"result": None, "error": f"dtoList[{idx}] 必须是字典类型"}

            processed_dto = {}
            # DailypatrolDetailDTO 字段
            detail_fields = ["id", "relmainid", "testingitemid", "testingsubitem",
                           "testingstatus", "estimate", "dw", "opinion", "remark",
                           "photoannex", "jczbid"]

            for field in detail_fields:
                if field in dto and dto[field] is not None:
                    processed_dto[field] = dto[field]

            processed_dto_list.append(processed_dto)

        request_body["dtoList"] = processed_dto_list

    # ========== 获取 API 地址 ==========
    api_url = tool_args.get("api_url") or os.environ.get("DAILYPATROL_API_URL") or DEFAULT_API_URL

    # ========== 构建请求头 ==========
    request_headers = {
        "agent-secret-key": AGENT_SECRET_KEY
    }

    logger.info(f"[日常巡查记录] 开始提交巡查记录")
    logger.info(f"[日常巡查记录] 标题: {title}, 设施: {ssname}")
    logger.info(f"[日常巡查记录] 巡查人: {xcperson} ({xcphone})")
    logger.info(f"[日常巡查记录] API地址: {api_url}")

    # ========== 发送请求 ==========
    endpoint = "/dailypatrol/agent/add"
    full_url = f"{api_url}{endpoint}"

    logger.info(f"[日常巡查记录] POST {full_url}")

    response = _send_http_request(full_url, "POST", request_body, headers=request_headers)

    # ========== 处理响应 ==========
    if not response.get("success"):
        error_info = response.get("error", {})
        error_msg = error_info.get("message", "未知错误")
        http_status = response.get("http_status", "N/A")
        logger.error(f"[日常巡查记录] 提交失败 (HTTP {http_status}): {error_msg}")
        return {"result": None, "error": f"提交日常巡查记录失败: {error_msg}"}

    # 成功响应
    data = response.get("data", {})
    record_id = data.get("id") or data.get("recordId")

    result_message = f"""日常巡查记录提交成功！

📋 记录信息:
- 标题: {title}
- 设施: {ssname} ({typename})
- 巡查日期: {xcdate}
- 巡查人: {xcperson} ({xcphone})
- 巡查单位: {xcunitname}

📋 系统返回:
- 记录ID: {record_id}
- 状态: {data.get('status', 'success')}"""

    logger.info(f"[日常巡查记录] 提交成功 - 记录ID: {record_id}")
    return {"result": result_message, "error": None}


def register_dailypatrol_tools():
    """注册日常巡查记录相关工具"""
    tools = [
        ToolDefinition(
            name="submit_dailypatrol_record",
            description="提交日常巡查记录 - 将日常巡查任务记录（Agent回写版本）提交到后端系统。支持主表信息+检测指标明细(dtoList)一并提交。",
            params='submit_dailypatrol_record:{'
                   '"title":"(巡查标题，必填，max100)",'
                   '"xcdate":"(巡查日期-时间戳毫秒，必填)",'
                   '"typeid":"(设施类型，必填)",'
                   '"typename":"(设施类型名称，必填，max100)",'
                   '"nameid":"(设施名称ID，必填)",'
                   '"ssname":"(设施名称，必填，max100)",'
                   '"xcunitname":"(巡查单位名称，必填，max100)",'
                   '"dq":"(地区，必填)",'
                   '"isdjrw":"(是否定检任务，必填)",'
                   '"isyhby":"(是否需要养护保养，必填)",'
                   '"xcperson":"(巡查人姓名，必填)",'
                   '"xcphone":"(巡查人电话，必填)",'
                   '"xcunitid":"(巡查单位ID，必填)",'
                   '"userId":"(用户ID，可选)",'
                   '"status":"(保养状态，可选)",'
                   '"remark":"(说明，可选)",'
                   '"source":"(数据来源，可选)",'
                   '"dzdtisvalid":"(坐标是否有效距离，可选)",'
                   '"dzdt":"(电子地图坐标，可选)",'
                   '"xcbegintime":"(开始时间戳，可选)",'
                   '"xcendtime":"(结束时间戳，可选)",'
                   '"checktodate":"(截止日期时间戳，可选)",'
                   '"photoannex":"(照片附件，可选)",'
                   '"qrdzdt":"(二维码巡查坐标，可选)",'
                   '"reveal":"(是否展示0/1，可选)",'
                   '"videoModel":"(是否视频巡查1/0，可选)",'
                   '"dtoList":"(检测指标明细列表，可选)",'
                   '"api_url":"(API地址，可选)"'
                   '}',
            category="dailypatrol",
            executor=execute_submit_dailypatrol_record
        ),
    ]

    for tool in tools:
        ToolRegistry.register(tool)

    logger.info("日常巡查记录工具注册完成")


# 导出常量
DAILYPATROL_TOOLS = {"submit_dailypatrol_record"}
DAILYPATROL_CATEGORY = "dailypatrol"
