#!/usr/bin/env python3
"""
日常巡查记录提交测试

测试日常巡查记录工具（submit_dailypatrol_record）的完整流程：
1. Agent 接收巡查任务描述
2. 调用 submit_dailypatrol_record 工具提交记录
3. 验证工具调用成功且数据格式正确

使用场景：
- 桥梁日常巡查任务
- 包含主表信息 + 检测指标明细（dtoList）
- 无需token认证，直接传递用户信息
"""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .base import (
    APIClient,
    TestResult,
    Colors,
    get_project_root,
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


# 测试数据常量
TEST_FACILITY_NAME = "沪渝高速大桥"
TEST_FACILITY_ID = "BR-001"
TEST_REGION_ID = "310101"
TEST_ORG_NAME = "市政养护一队"
TEST_ORG_ID = 2001


DAILYPATROL_PROMPT = """## 任务：提交日常巡查记录

### 场景背景
你是一座大型桥梁管理系统的**智能助手**。巡查人员完成了对 **{facility_name}** 的日常巡查工作，现在需要你将巡查记录提交到系统。

### 巡查基本信息
- **设施名称**: {facility_name}
- **设施ID**: {facility_id}
- **设施类型**: 桥梁 (typeid=1)
- **地区代码**: {region_id}
- **是否定检任务**: 否 (isdjrw=0)
- **是否需要养护保养**: 是 (isyhby=1)

### 巡查人员信息
- **姓名**: 张三
- **手机号**: 13800138000
- **单位ID**: {org_id}
- **单位名称**: {org_name}

### 巡查时间
- **巡查日期**: 当前时间
- **巡查标题**: "{facility_name}日常巡查 - {date_str}"

### 检测指标明细（dtoList）

本次巡查发现以下问题，必须包含在提交请求中：

1. **桥面系 - 桥面铺装**
   - 病害类型: 裂缝
   - 养护意见: 建议进行灌缝处理，防止裂缝扩展
   - 工程量: 5.5 m²
   - 备注: 横向裂缝，宽度约2mm

2. **上部结构 - 梁体**
   - 病害类型: 混凝土剥落
   - 养护意见: 建议修补并做防水处理
   - 工程量: 2.0 m²
   - 备注: 局部露筋，需及时处理

3. **支座 - 板式橡胶支座**
   - 病害类型: 老化开裂
   - 养护意见: 建议列入下次维修计划更换
   - 工程量: 4 个
   - 备注: 橡胶老化明显，有细小裂纹

### 执行要求

请使用 `submit_dailypatrol_record` 工具完成巡查记录的提交。

### 完成后的输出

向用户汇报：
- 提交成功的记录ID
- 巡查设施名称
- 发现的问题数量
- 主要问题描述摘要

---
"""


def build_prompt() -> str:
    """构建测试提示词"""
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")

    return DAILYPATROL_PROMPT.format(
        facility_name=TEST_FACILITY_NAME,
        facility_id=TEST_FACILITY_ID,
        region_id=TEST_REGION_ID,
        org_id=TEST_ORG_ID,
        org_name=TEST_ORG_NAME,
        date_str=date_str,
    )


async def run_dailypatrol_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True,
) -> TestResult:
    """运行日常巡查记录提交测试"""
    result = TestResult("dailypatrol_submit", scenario_config)

    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stream_log_file = str(log_dir / f"dailypatrol_test_{timestamp}.log")

    print_test_header(scenario_config.get(
        "description",
        "日常巡查记录提交测试 - Agent调用submit_dailypatrol_record工具"
    ))

    # ========== 步骤1: 构建提示词 ==========
    print_step(1, "构建日常巡查提示词...", Colors.CYAN)

    try:
        prompt = build_prompt()
        if verbose:
            print_dim(f"提示词长度: {len(prompt)} 字符")
            print_dim(f"测试设施: {TEST_FACILITY_NAME}")
            print_dim(f"检测明细数: 3项")
    except Exception as e:
        print_error(f"提示词构建失败: {e}")
        result.errors.append(f"prompt_build: {e}")
        return result

    print_success("提示词构建完成")

    # ========== 步骤2: 创建会话 ==========
    print_step(2, "创建测试会话...", Colors.CYAN)

    session_result = await api.create_session(title="日常巡查记录提交测试")

    if not session_result.get("success", True):
        print_error(f"会话创建失败: {session_result.get('message')}")
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result

    session_id = session_result.get("data", {}).get("id")
    workspace_id = session_result.get("data", {}).get("workspace_id")
    result.session_id = session_id
    result.workspace_id = workspace_id

    if verbose:
        print_success(f"会话创建成功: {session_id}")
        print_dim(f"Workspace ID: {workspace_id}")

    # ========== 步骤3: 创建对话并发送提示词 ==========
    print_step(3, "创建对话并发送提示词...", Colors.CYAN)

    conv_result = await api.create_conversation(session_id, prompt)

    if not conv_result.get("success", True):
        print_error(f"对话创建失败: {conv_result.get('message')}")
        result.errors.append(f"create_conversation: {conv_result.get('message')}")
        return result

    conversation_id = conv_result.get("data", {}).get("conversation_id")
    result.conversation_id = conversation_id

    if verbose:
        print_success(f"对话创建成功: {conversation_id}")

    # ========== 步骤4: 等待对话开始处理 ==========
    print_step(4, "等待对话开始处理...", Colors.CYAN)
    await wait_for_conversation_state(api, conversation_id, "processing", timeout=15.0)

    # ========== 步骤5: 收集流式响应 ==========
    print_step(5, "收集流式响应（等待Agent执行工具调用）...", Colors.CYAN)

    dailypatrol_timeout = scenario_config.get("dailypatrol_timeout", 300.0)
    await collect_stream_output(
        api,
        conversation_id,
        result,
        verbose=verbose,
        timeout=dailypatrol_timeout,
        stream_log_file=stream_log_file,
    )

    # 处理可能的 PLAN mode 或 follow-up 对话
    max_followups = 3
    followup_count = 0

    while (result.next_conversation_id or result.detected_mode == "PLAN") and followup_count < max_followups:
        followup_count += 1
        if result.next_conversation_id:
            if verbose:
                print(f"{Colors.YELLOW}[Follow-up #{followup_count}] 继续对话: {result.next_conversation_id}{Colors.ENDC}")
            next_conv_id = result.next_conversation_id
            result.next_conversation_id = None
            conversation_id = next_conv_id
            result.conversation_id = conversation_id
            await wait_for_conversation_state(api, conversation_id, "processing", timeout=10.0)
            await collect_stream_output(
                api,
                conversation_id,
                result,
                verbose=verbose,
                timeout=dailypatrol_timeout,
                stream_log_file=stream_log_file,
            )
        elif result.detected_mode == "PLAN":
            if verbose:
                print(f"{Colors.YELLOW}[Follow-up #{followup_count}] PLAN模式，发送审批...{Colors.ENDC}")
            try:
                approve_result = await api.approve_plan(result.workspace_id, approved=True)
                if verbose:
                    print(f"{Colors.DIM}[Follow-up] 审批结果: {approve_result.get('message', 'ok')}{Colors.ENDC}")
            except Exception as e:
                if verbose:
                    print(f"{Colors.DIM}[Follow-up] 审批失败: {e}{Colors.ENDC}")

            conv_result = await api.create_conversation(session_id, "可以继续")
            if conv_result.get("success", True):
                next_conv_id = conv_result.get("data", {}).get("conversation_id")
                if next_conv_id:
                    conversation_id = next_conv_id
                    result.conversation_id = conversation_id
                    result.detected_mode = None
                    await wait_for_conversation_state(api, conversation_id, "processing", timeout=10.0)
                    await collect_stream_output(
                        api,
                        conversation_id,
                        result,
                        verbose=verbose,
                        timeout=dailypatrol_timeout,
                        stream_log_file=stream_log_file,
                    )

    # ========== 步骤6: 等待对话完成 ==========
    print_step(6, "等待对话完成...", Colors.CYAN)

    max_completion_wait = 600
    completion_wait_start = time.time()

    while time.time() - completion_wait_start < max_completion_wait:
        final_result = await wait_for_conversation_state(
            api, conversation_id, "completed", timeout=60.0
        )
        if final_result:
            final_state = final_result.get("data", {}).get("state") if isinstance(final_result, dict) else None
            if final_state == "completed":
                result.response_text = extract_response_text(final_result)
                if verbose:
                    print_success(f"对话完成，响应长度: {len(result.response_text) if result.response_text else 0} 字符")
                break
            elif final_state == "running":
                elapsed = int(time.time() - completion_wait_start)
                if verbose:
                    print(f"{Colors.DIM}[Completion] 仍在运行... ({elapsed}s){Colors.ENDC}")
                await asyncio.sleep(15)
            else:
                result.response_text = extract_response_text(final_result)
                break
        else:
            await asyncio.sleep(10)

    # ========== 步骤7: 验证工具调用 ==========
    print_step(7, "验证工具调用...", Colors.CYAN)

    # 从 LLM decision trace log 解析工具调用
    log_file = Path(__file__).parent.parent.parent / "llm_decision_trace.log"
    if log_file.exists():
        with open(log_file, "r", encoding="utf-8") as f:
            log_content = f.read()

        import re
        tool_pattern = r'=== TOOL CALL ===\s+Tool Name: (\w+)'
        found_tools = re.findall(tool_pattern, log_content)
        if found_tools:
            result.tool_calls = list(set(found_tools))
            if verbose:
                print_dim(f"从日志解析到工具: {result.tool_calls}")

    result.tool_calls = list(set(result.tool_calls))

    # 核心验证：检查 submit_dailypatrol_record 是否被调用
    dailypatrol_tool_called = "submit_dailypatrol_record" in result.tool_calls

    if dailypatrol_tool_called:
        print_success("OK submit_dailypatrol_record 工具已调用")
    else:
        print_error("FAIL submit_dailypatrol_record 工具未调用")
        result.errors.append("dailypatrol_tool_not_called")

    # ========== 步骤8: 验证响应内容 ==========
    print_step(8, "验证响应内容...", Colors.CYAN)

    if result.response_text:
        response_lower = result.response_text.lower()

        # 检查响应中是否包含关键信息
        checks = [
            ("设施名称", TEST_FACILITY_NAME in result.response_text),
            ("提交成功标识", any(kw in response_lower for kw in ["成功", "提交", "记录"])),
            ("问题数量", "3" in result.response_text or "三" in result.response_text or "三项" in response_lower),
        ]

        for check_name, check_passed in checks:
            if check_passed:
                if verbose:
                    print_success(f"OK 响应包含{check_name}")
            else:
                if verbose:
                    print_warning(f"响应可能缺少{check_name}")
                # 不作为硬性错误，仅警告

        if verbose:
            print_dim(f"\n--- 响应预览 ---\n{result.response_text[:500]}...\n--- 结束 ---")
    else:
        print_warning("未捕获到响应文本")

    # ========== 步骤9: 生成测试报告 ==========
    print_step(9, "生成测试报告...", Colors.CYAN)

    output_log = log_dir / f"dailypatrol_test_{timestamp}.md"

    with open(output_log, "w", encoding="utf-8") as f:
        f.write("# 日常巡查记录提交测试报告\n\n")
        f.write(f"- **时间戳**: {timestamp}\n")
        f.write(f"- **会话ID**: {session_id}\n")
        f.write(f"- **对话ID**: {conversation_id}\n")
        f.write(f"- **工作区ID**: {workspace_id}\n")
        f.write("\n---\n\n")

        f.write("## 测试配置\n\n")
        f.write(f"- **设施名称**: {TEST_FACILITY_NAME}\n")
        f.write(f"- **设施ID**: {TEST_FACILITY_ID}\n")
        f.write(f"- **区域ID**: {TEST_REGION_ID}\n")
        f.write(f"- **单位名称**: {TEST_ORG_NAME}\n")
        f.write("- **检测明细数量**: 3项\n")
        f.write("\n---\n\n")

        f.write("## 执行结果\n\n")
        f.write(f"- **工具调用次数**: {len(result.tool_calls)}\n")
        f.write(f"- **调用的工具**: {', '.join(result.tool_calls) if result.tool_calls else '无'}\n")
        f.write(f"- **submit_dailypatrol_record**: {'✅ 已调用' if dailypatrol_tool_called else '❌ 未调用'}\n")
        f.write(f"- **响应长度**: {len(result.response_text) if result.response_text else 0} 字符\n")
        f.write(f"- **错误数量**: {len(result.errors)}\n")

        if result.errors:
            f.write("\n## 错误详情\n\n")
            for error in result.errors:
                f.write(f"- ❌ {error}\n")

        f.write("\n---\n\n")
        f.write("## 响应内容\n\n")
        if result.response_text:
            f.write(f"```\n{result.response_text}\n```\n")
        else:
            f.write("*无响应内容*\n")

    if verbose:
        print_success(f"测试报告已保存: {output_log}")

    # 返回结果
    if verbose:
        print(f"\n{Colors.CYAN}{'='*60}{Colors.ENDC}")
        if not result.errors:
            print(f"{Colors.GREEN}✅ 测试通过{Colors.ENDC}")
        else:
            print(f"{Colors.RED}❌ 测试失败 ({len(result.errors)} 个错误){Colors.ENDC}")
            for error in result.errors:
                print(f"   - {error}")
        print(f"{Colors.CYAN}{'='*60}{Colors.ENDC}\n")

    return result
