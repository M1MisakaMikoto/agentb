#!/usr/bin/env python3
"""
submit 工具错误透传与路径解析测试

覆盖两个修复点：
- 阶段A (Bug1): ai_judgment_tool 的 HTTPError 分支能否透传扁平型错误结构的 message
                 用 user_id=404 触发 mock 返回 {"code":404,"message":"用户不存在"}
                 断言 tool_res.error 含 "用户不存在"（而非 "未知错误"）
- 阶段B (Bug2): facility_report_tool 的 _resolve_report_file_path 能否解析相对路径到工作区
                 prompt 让 agent 先 write_file 写 md 报告，再用 submit_facility_report 传相对路径
                 断言 tool_res.success=True 且 error 不含 "文件不存在"
"""

import asyncio
import time
from datetime import datetime
from pathlib import Path

from .base import (
    APIClient,
    TestResult,
    Colors,
    print_test_header,
    print_step,
    print_success,
    print_error,
    print_dim,
    print_warning,
    collect_stream_output,
    wait_for_conversation_state,
    extract_response_text,
)


# 阶段A: 触发扁平型 404 错误的 regionId（与 ai_judgment_mock_server.py 约定）
ERROR_REGION_ID = "404"

# 阶段B: 测试用的相对路径文件名（agent 会先 write_file 写入工作区，再用此相对路径提交）
RELATIVE_REPORT_FILE = "submit_tools_test_report.md"

STAGE_A_PROMPT = """## 任务：提交桥梁病害研判问题

### 场景背景
你是一座大型桥梁管理系统的**智能助手**。养护工程师张三在对 **花溪河大桥** 进行日常巡查时，发现桥面铺装存在一处约2米长的横向裂缝，疑似需要进一步研判处理。现在需要你将这个病害问题提交到 AI 研判系统。

### 设施信息
- **设施名称**: 花溪河大桥
- **设施ID**: 1
- **地区代码**: 404

### 病害详情
- **病害类型**: 桥面铺装裂缝
- **位置**: 桥面中部行车道
- **尺寸**: 长约2米，宽约2mm
- **初步判断**: 横向裂缝，建议进行灌缝处理防止扩展

### 巡查人员
- **姓名**: 张三
- **单位**: 市政养护一队

### 执行要求
请使用 `submit_ai_judgment_issue` 工具将上述病害问题提交到研判系统。提交时请包含：
- 设施ID、设施名称
- 研判标题（如"花溪河大桥桥面铺装裂缝研判"）
- 病害详细描述（含尺寸、位置、初步建议）
- 区域ID（使用地区代码 404）

### 完成后
用 `chat` 工具向用户汇报提交结果（含研判工单ID或错误信息）。
"""

STAGE_B_PROMPT = """## 任务：生成并提交花溪河大桥检测决策报告

### 场景背景
你是一座大型桥梁管理系统的**智能决策助手**。刚完成对 **花溪河大桥** 的专项检测，检测发现桥面铺装存在一处横向裂缝（长约2米）。现在需要你生成一份结构化的检测决策报告，并将报告文件提交到设施报告系统。

### 设施信息
- **设施名称**: 花溪河大桥
- **设施ID**: 1
- **地区代码**: 310101

### 执行步骤

#### 步骤1：生成决策报告文件
请先用 `write_file` 工具在工作区写入一份决策报告，文件名用 `{relative_file}`。报告内容需包含以下结构：

```
## 花溪河大桥检测决策报告

### 1. 基本信息
- 设施名称: 花溪河大桥
- 设施ID: 1
- 检测日期: 当前日期
- 病害类型: 桥面铺装裂缝

### 2. 病害分析
- 检测方法: 人工巡查
- 检测结果: 发现横向裂缝长约2米
- 严重程度: 中等
- 影响范围: 桥面中部行车道

### 3. 修复建议
- 短期措施: 进行灌缝处理
- 长期方案: 加强定期监测
```

#### 步骤2：提交设施报告
报告文件生成后，请使用 `submit_facility_report` 工具将报告提交到设施报告系统。提交时：
- **reportName**: "花溪河大桥桥面裂缝检测决策报告"
- **facilityId**: 1
- **facilityName**: "花溪河大桥"
- **reportFile**: 使用刚写入的报告文件名 `{relative_file}`（相对路径）
- **regionId**: 310101

#### 步骤3：汇报结果
用 `chat` 工具向用户汇报报告提交是否成功。
"""


async def _run_single_conversation(
    api: APIClient,
    prompt: str,
    result: TestResult,
    verbose: bool,
    timeout: float,
    stream_log_file: str,
) -> str:
    """跑一轮 session+conversation，返回 conversation_id。失败时填 result.errors 并返回 None。"""
    session_result = await api.create_session(title="submit_tools_error_path 测试")
    if not session_result.get("success", True):
        result.errors.append(f"create_session: {session_result.get('message')}")
        return None

    session_id = session_result.get("data", {}).get("id")
    workspace_id = session_result.get("data", {}).get("workspace_id")
    if not result.session_id:
        result.session_id = session_id
        result.workspace_id = workspace_id

    conv_result = await api.create_conversation(session_id, prompt)
    if not conv_result.get("success", True):
        result.errors.append(f"create_conversation: {conv_result.get('message')}")
        return None

    conversation_id = conv_result.get("data", {}).get("conversation_id")
    if not result.conversation_id:
        result.conversation_id = conversation_id

    await wait_for_conversation_state(api, conversation_id, "processing", timeout=15.0)
    await collect_stream_output(
        api, conversation_id, result,
        verbose=verbose, timeout=timeout, stream_log_file=stream_log_file,
    )

    # 等待完成
    completion_wait_start = time.time()
    max_wait = 300
    while time.time() - completion_wait_start < max_wait:
        final_result = await wait_for_conversation_state(api, conversation_id, "completed", timeout=60.0)
        if final_result:
            final_state = final_result.get("data", {}).get("state") if isinstance(final_result, dict) else None
            if final_state == "completed":
                if not result.response_text:
                    result.response_text = extract_response_text(final_result)
                break
            elif final_state != "running":
                if not result.response_text:
                    result.response_text = extract_response_text(final_result)
                break
        await asyncio.sleep(15)

    return conversation_id


def _find_tool_result(tool_results: list, tool_name: str) -> dict:
    """从 tool_results 列表中查找指定工具的最近一次结果"""
    for entry in reversed(tool_results):
        if entry.get("tool_name") == tool_name:
            return entry
    return {}


async def run_submit_tools_error_path_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True,
) -> TestResult:
    """运行 submit 工具错误透传与路径解析测试（单场景两阶段）"""
    result = TestResult("submit_tools_error_path", scenario_config)

    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print_test_header(scenario_config.get(
        "description",
        "submit 工具错误透传(Bug1) + 相对路径解析(Bug2) 测试"
    ))

    # ==================== 阶段A: Bug1 错误透传 ====================
    print_step(1, "阶段A: 测试 ai_judgment 扁平错误透传 (regionId=404)...", Colors.CYAN)

    if verbose:
        print_dim(f"通过 prompt 指定 regionId={ERROR_REGION_ID} 触发 mock 扁平 404")

    stage_a_log = str(log_dir / f"submit_tools_stage_a_{timestamp}.log")
    stage_a_timeout = scenario_config.get("stage_a_timeout", 180.0)

    await _run_single_conversation(
        api, STAGE_A_PROMPT, result, verbose, stage_a_timeout, stage_a_log
    )

    ai_judgment_res = _find_tool_result(result.tool_results, "submit_ai_judgment_issue")

    if not ai_judgment_res:
        print_error("FAIL 未捕获 submit_ai_judgment_issue 的 tool_res 事件")
        result.errors.append("stage_a_no_tool_res")
    else:
        error_text = str(ai_judgment_res.get("error") or "")
        if "用户不存在" in error_text:
            print_success("OK Bug1 验证通过: 错误信息透传 '用户不存在'")
        elif "未知错误" in error_text:
            print_error("FAIL Bug1 未修复: 仍返回 '未知错误'")
            result.errors.append("stage_a_error_not_propagated")
        else:
            print_error(f"FAIL Bug1 异常: error={error_text[:200]}")
            result.errors.append(f"stage_a_unexpected_error: {error_text[:200]}")

    # ==================== 阶段B: Bug2 相对路径解析 ====================
    print_step(2, "阶段B: 测试 facility_report 相对路径解析 (默认 user_id)...", Colors.CYAN)

    stage_b_prompt = STAGE_B_PROMPT.format(relative_file=RELATIVE_REPORT_FILE)
    stage_b_log = str(log_dir / f"submit_tools_stage_b_{timestamp}.log")
    stage_b_timeout = scenario_config.get("stage_b_timeout", 240.0)

    # 阶段B 用默认 api（user_id=1），但需要独立的 result 收集 tool_res
    stage_b_result = TestResult("submit_tools_stage_b", scenario_config)
    await _run_single_conversation(
        api, stage_b_prompt, stage_b_result, verbose, stage_b_timeout, stage_b_log
    )

    # 合并阶段B的 tool_results 到主 result
    result.tool_results.extend(stage_b_result.tool_results)
    result.tool_calls.extend(stage_b_result.tool_calls)

    facility_res = _find_tool_result(result.tool_results, "submit_facility_report")

    if not facility_res:
        print_error("FAIL 未捕获 submit_facility_report 的 tool_res 事件")
        result.errors.append("stage_b_no_tool_res")
    else:
        fr_error = str(facility_res.get("error") or "")
        fr_success = facility_res.get("success", False)
        if "文件不存在" in fr_error:
            print_error(f"FAIL Bug2 未修复: 相对路径未解析, error={fr_error[:200]}")
            result.errors.append("stage_b_path_not_resolved")
        elif not fr_success and fr_error:
            print_error(f"FAIL Bug2 异常: error={fr_error[:200]}")
            result.errors.append(f"stage_b_unexpected_error: {fr_error[:200]}")
        else:
            print_success("OK Bug2 验证通过: 相对路径解析成功，submit_facility_report 未报 '文件不存在'")

    # ==================== 汇总 ====================
    print_step(3, "测试汇总...", Colors.CYAN)

    output_log = log_dir / f"submit_tools_error_path_{timestamp}.md"
    with open(output_log, "w", encoding="utf-8") as f:
        f.write("# submit 工具错误透传与路径解析测试报告\n\n")
        f.write(f"- **时间戳**: {timestamp}\n")
        f.write(f"- **阶段A session**: {result.session_id}\n")
        f.write(f"- **阶段B session**: {stage_b_result.session_id}\n")
        f.write(f"- **工具调用**: {result.tool_calls}\n")
        f.write(f"- **tool_results 数**: {len(result.tool_results)}\n\n")
        f.write("## tool_results 详情\n\n")
        for entry in result.tool_results:
            f.write(f"- **{entry.get('tool_name')}** success={entry.get('success')} ")
            f.write(f"error={str(entry.get('error') or '')[:200]} ")
            f.write(f"result={str(entry.get('result') or '')[:200]}\n")
        f.write("\n## 错误\n\n")
        if result.errors:
            for e in result.errors:
                f.write(f"- {e}\n")
        else:
            f.write("无\n")

    if verbose:
        print_success(f"测试报告已保存: {output_log}")

    print(f"\n{Colors.CYAN}{'='*60}{Colors.ENDC}")
    if not result.errors:
        print(f"{Colors.GREEN}测试通过：Bug1 错误透传 + Bug2 路径解析 均验证成功{Colors.ENDC}")
    else:
        print(f"{Colors.RED}测试失败 ({len(result.errors)} 个错误):{Colors.ENDC}")
        for e in result.errors:
            print(f"   - {e}")
    print(f"{Colors.CYAN}{'='*60}{Colors.ENDC}\n")

    return result
