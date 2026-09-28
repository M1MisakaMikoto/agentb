"""上游工具 MCP 服务（项目内自建，随项目部署的独立服务）。

暴露 4 个上游特定工具：报告上传（研判/预测）、日常巡查记录、AI 研判问题。
工具实现直接复用后端 WorkBranch/backend 下的既有 HTTP 逻辑，配置由本服务
自身的 config.py 提供（不再读取后端 settings 服务）。

启动方式（streamable-http 传输）：

    python -m mcp_servers.upstream_tools.server

环境变量：
    AGENTB_MCP_HOST / AGENTB_MCP_PORT          监听地址（默认 127.0.0.1:8181）
    AGENTB_MCP_UPSTREAM_CONFIG                 配置文件路径（默认同目录 config.json）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional

from pydantic import Field

_UPSTREAM_DIR = Path(__file__).resolve().parent
_WORKBRANCH_DIR = _UPSTREAM_DIR.parents[1]
_BACKEND_DIR = _WORKBRANCH_DIR / "backend"
for _path in (_BACKEND_DIR, _WORKBRANCH_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from mcp.server.fastmcp import FastMCP

from service.agent_service.tools.ai_judgment_tool import (
    execute_submit_ai_judgment_issue,
)
from service.agent_service.tools.dailypatrol_tool import (
    execute_submit_dailypatrol_record,
)
from service.agent_service.tools.facility_report_tool import (
    execute_submit_facility_forecast_report,
    execute_submit_facility_report,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8181


def _server_settings() -> Dict[str, Any]:
    return {
        "host": os.getenv("AGENTB_MCP_HOST") or DEFAULT_HOST,
        "port": int(os.getenv("AGENTB_MCP_PORT") or DEFAULT_PORT),
    }


mcp = FastMCP("agentb-upstream-tools", **_server_settings())


@mcp.tool(
    name="submit_facility_report",
    description=(
        "生成设施研判报告 - 将检测报告(DOCX)上传后自动生成研判报告。"
        "注意：若尚无DOCX文件，先用 document w 工具生成DOCX，再传 reportFile 给本工具。"
    ),
)
def submit_facility_report(
    reportName: Annotated[str, Field(description="报告名称（必填）")],
    facilityId: Annotated[str, Field(description="设施ID（必填）")],
    facilityName: Annotated[str, Field(description="设施名称（必填）")],
    reportFile: Annotated[str, Field(description="报告DOCX文件路径（必填）")],
    regionId: Annotated[str, Field(description="区域ID（必填）")],
) -> Dict[str, Any]:
    """生成设施研判报告。"""
    return execute_submit_facility_report(
        {
            "reportName": reportName,
            "facilityId": facilityId,
            "facilityName": facilityName,
            "reportFile": reportFile,
            "regionId": regionId,
        }
    )


@mcp.tool(
    name="submit_facility_forecast",
    description=(
        "提交设施预测报告 - 将桥梁预测分析结果(DOCX)上传到系统。"
        "注意：若尚无DOCX文件，先用 document w 工具生成DOCX，再传 reportFile 给本工具。"
    ),
)
def submit_facility_forecast(
    regionId: Annotated[str, Field(description="区域ID（必填，从元数据中获取）")],
    facilityId: Annotated[str, Field(description="设施ID（必填）")],
    predictYear: Annotated[int, Field(description="预测年份（必填）")],
    reportFile: Annotated[str, Field(description="报告DOCX文件路径（必填）")],
    facilityName: Annotated[Optional[str], Field(description="设施名称（可选）")] = None,
    predictedHealthScore: Annotated[
        Optional[float], Field(description="预测健康分数（可选）")
    ] = None,
    predictedRiskLevel: Annotated[
        Optional[str], Field(description="风险等级（可选: HIGH/MEDIUM/LOW）")
    ] = None,
    summary: Annotated[Optional[str], Field(description="预测结论摘要（可选）")] = None,
) -> Dict[str, Any]:
    """提交设施预测报告。"""
    return execute_submit_facility_forecast_report(
        {
            "regionId": regionId,
            "facilityId": facilityId,
            "predictYear": predictYear,
            "reportFile": reportFile,
            "facilityName": facilityName,
            "predictedHealthScore": predictedHealthScore,
            "predictedRiskLevel": predictedRiskLevel,
            "summary": summary,
        }
    )


@mcp.tool(
    name="submit_dailypatrol_record",
    description="提交日常巡查记录 - 将日常巡查任务记录（Agent回写版本）提交到后端系统，支持主表信息+检测指标明细(dtoList)一并提交。",
)
def submit_dailypatrol_record(
    title: Annotated[str, Field(description="巡查标题（必填，max100）")],
    xcdate: Annotated[int, Field(description="巡查日期-时间戳毫秒（必填）")],
    typeid: Annotated[int, Field(description="设施类型（必填）")],
    typename: Annotated[str, Field(description="设施类型名称（必填，max100）")],
    nameid: Annotated[int, Field(description="设施名称ID（必填）")],
    ssname: Annotated[str, Field(description="设施名称（必填，max100）")],
    xcunitname: Annotated[str, Field(description="巡查单位名称（必填，max100）")],
    dq: Annotated[int, Field(description="地区（必填）")],
    isdjrw: Annotated[int, Field(description="是否定检任务（必填）")],
    isyhby: Annotated[int, Field(description="是否需要养护保养（必填）")],
    xcperson: Annotated[str, Field(description="巡查人姓名（必填）")],
    xcphone: Annotated[str, Field(description="巡查人电话（必填）")],
    xcunitid: Annotated[int, Field(description="巡查单位ID（必填）")],
    userId: Annotated[Optional[int], Field(description="用户ID（可选）")] = None,
    status: Annotated[Optional[int], Field(description="保养状态（可选）")] = None,
    remark: Annotated[Optional[str], Field(description="说明（可选）")] = None,
    source: Annotated[Optional[int], Field(description="数据来源（可选）")] = None,
    dzdtisvalid: Annotated[
        Optional[int], Field(description="坐标是否有效距离（可选）")
    ] = None,
    dzdt: Annotated[Optional[str], Field(description="电子地图坐标（可选）")] = None,
    xcbegintime: Annotated[
        Optional[int], Field(description="开始时间戳（可选）")
    ] = None,
    xcendtime: Annotated[Optional[int], Field(description="结束时间戳（可选）")] = None,
    checktodate: Annotated[
        Optional[int], Field(description="截止日期时间戳（可选）")
    ] = None,
    photoannex: Annotated[Optional[str], Field(description="照片附件（可选）")] = None,
    qrdzdt: Annotated[Optional[str], Field(description="二维码巡查坐标（可选）")] = None,
    reveal: Annotated[Optional[int], Field(description="是否展示0/1（可选）")] = None,
    videoModel: Annotated[
        Optional[int], Field(description="是否视频巡查1/0（可选）")
    ] = None,
    dtoList: Annotated[
        Optional[List[Dict[str, Any]]], Field(description="检测指标明细列表（可选）")
    ] = None,
) -> Dict[str, Any]:
    """提交日常巡查记录。"""
    args: Dict[str, Any] = {
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
    optional_args = {
        "userId": userId,
        "status": status,
        "remark": remark,
        "source": source,
        "dzdtisvalid": dzdtisvalid,
        "dzdt": dzdt,
        "xcbegintime": xcbegintime,
        "xcendtime": xcendtime,
        "checktodate": checktodate,
        "photoannex": photoannex,
        "qrdzdt": qrdzdt,
        "reveal": reveal,
        "videoModel": videoModel,
        "dtoList": dtoList,
    }
    args.update({key: value for key, value in optional_args.items() if value is not None})
    return execute_submit_dailypatrol_record(args)


@mcp.tool(
    name="submit_ai_judgment_issue",
    description="提交 AI 研判问题 - 将设施问题提交到 AI 研判系统，等待 AI 分析并返回研判结果。",
)
def submit_ai_judgment_issue(
    facilityId: Annotated[str, Field(description="设施ID（必填）")],
    facilityName: Annotated[str, Field(description="设施名称（必填）")],
    title: Annotated[str, Field(description="问题标题（必填）")],
    regionId: Annotated[str, Field(description="区域ID（必填）")],
    description: Annotated[Optional[str], Field(description="问题描述（可选）")] = None,
) -> Dict[str, Any]:
    """提交 AI 研判问题。"""
    args: Dict[str, Any] = {
        "facilityId": facilityId,
        "facilityName": facilityName,
        "title": title,
        "regionId": regionId,
    }
    if description is not None:
        args["description"] = description
    return execute_submit_ai_judgment_issue(args)


def main() -> None:
    """以 streamable-http 传输启动 MCP 服务。"""
    settings = _server_settings()
    print(
        f"[upstream-tools-mcp] serving on http://{settings['host']}:{settings['port']}/mcp",
        flush=True,
    )
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
