#!/usr/bin/env python3
"""
桥梁病害智能决策测试

测试智能决策流程：
1. AI 分析检测数据（深度学习模型结果 + 图片）
2. 生成结构化决策报告（分析、评估、修复建议）
3. submit_ai_judgment_issue - 提交问题到研判系统
4. submit_facility_report - 提交完整报告

使用材料：
- 元数据.txt：花溪河大桥设施信息
- 深度模型结果.txt：U-Net检测结果（坑洞4.15%）
- 图片.jpg：检测图像
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

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


DATA_ROOT = get_project_root() / ".dev" / "table"
METADATA_FILE = DATA_ROOT / "元数据.txt"
MODEL_RESULT_FILE = DATA_ROOT / "深度模型结果.txt"
IMAGE_FILE = DATA_ROOT / "图片.jpg"


PREDICTION_PROMPT = """## 任务：桥梁病害智能决策与报告生成

### 场景背景
你是一座大型桥梁管理系统的**智能决策助手**。系统检测到一处疑似病害，需要你进行：
1. 深度分析病害情况
2. 生成结构化决策报告
3. 提交问题到研判系统
4. 生成并提交完整检测报告

### 已上传材料

**1. 设施元数据（元数据.txt）：**
```
{metadata_content}
```

**2. 深度学习模型检测结果（深度模型结果.txt）：**
```json
{model_result_content}
```

**3. 检测图像（图片.jpg）** - 已上传至工作区，可通过图片理解分析病害实际情况

### 模型检测数据摘要
- **病害类型**: {selected_category}
- **灾害判定**: {is_disaster}
- **判定状态**: {status}
- **最终评分**: {final_score}
- **U-Net坑洞检测得分**: {unet_score}
- **目标像素占比**: {target_ratio}%

---

## 决策流程（必须严格按顺序执行）

### 阶段1：深度分析
请使用 `thinking` 工具进行系统化分析：

1. **病害识别分析**
   - 根据模型检测结果，分析病害类型、位置、范围
   - 结合图片理解，验证检测结果的可靠性
   - 评估病害严重程度（轻微/中等/严重/危急）

2. **风险评估**
   - 评估对桥梁结构安全的影响
   - 判断是否为紧急情况
   - 考虑病害发展趋势

3. **决策依据整理**
   - 汇总分析要点
   - 制定初步处理建议

### 阶段2：提交问题到研判系统

**必须调用 `submit_ai_judgment_issue` 工具**

参数要求：
- `facilityId`: 从元数据获取设施ID
- `facilityName`: "花溪河大桥"
- `title`: "花溪河大桥{病害类型}病害识别研判"
- `description`: 包含以下内容：
  - 病害分析结果
  - 模型检测数据摘要
  - 初步处理建议
- `regionId`: **从元数据或系统上下文获取区域ID（必填）**

### 阶段3：生成结构化决策报告

在提交问题后，必须生成完整的决策报告。决策报告应包含以下结构：

```
## 桥梁病害决策报告

### 1. 基本信息
- 设施名称: 花溪河大桥
- 设施ID: [从元数据获取]
- 检测日期: [当前日期]
- 病害类型: {病害类型}

### 2. 病害分析
- **检测方法**: U-Net深度学习模型
- **检测结果**: [详细描述检测到的病害]
- **严重程度**: [轻微/中等/严重/危急]
- **影响范围**: [像素占比、面积估算]

### 3. 风险评估
- **结构安全影响**: [评估结果]
- **灾害等级**: {is_disaster}
- **紧急程度**: [是否需要立即处理]

### 4. 修复建议
- **短期措施**: [1-7天内应采取的措施]
- **中期计划**: [1-3个月内的修复计划]
- **长期方案**: [根本性解决方案]

### 5. 优先级评定
- **优先级**: [P0紧急/P1重要/P2一般/P3低优先级]
- **建议处理时限**: [具体时间要求]
```

### 阶段4：提交设施报告

**必须调用 `submit_facility_report` 工具**

参数要求：
- `reportName`: "花溪河大桥{病害类型}病害检测决策报告"
- `facilityId`: 从元数据获取
- `facilityName`: "花溪河大桥"
- `reportFileUrl`: "/files/predictions/[基于日期的标识].png"
- `regionId`: **从元数据或系统上下文获取区域ID（必填）**

---

## 执行要求

1. **必须依次执行**：分析 → submit_ai_judgment_issue → 决策报告 → submit_facility_report
2. **决策报告必须完整**：包含上述5个部分，缺一不可
3. **修复建议必须具体**：不能只说"需要修复"，要给出具体措施
4. **两个工具都必须调用成功**：才算任务完成

## 输出格式

完成所有步骤后，使用 `chat` 工具向用户汇报：
- 分析结论摘要
- 决策报告核心内容（精简版）
- 已提交的项目（issue ID 和 report ID）
"""


def load_metadata() -> str:
    """加载设施元数据"""
    if not METADATA_FILE.exists():
        raise FileNotFoundError(f"元数据文件不存在: {METADATA_FILE}")
    with open(METADATA_FILE, "r", encoding="utf-8") as f:
        return f.read()


def load_model_results() -> dict:
    """加载并解析深度模型结果"""
    if not MODEL_RESULT_FILE.exists():
        raise FileNotFoundError(f"模型结果文件不存在: {MODEL_RESULT_FILE}")
    with open(MODEL_RESULT_FILE, "r", encoding="utf-8") as f:
        content = f.read()
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"模型结果JSON解析失败: {e}")


def build_prompt() -> str:
    """构建决策流程提示词"""
    metadata_content = load_metadata()
    model_result = load_model_results()

    decision = model_result.get("decision", {})
    unet_scores = model_result.get("unet_scores", {})

    # 提取坑洞检测数据
    pothole = unet_scores.get("pothole", {})
    pothole_stats = pothole.get("stats", {})

    return PREDICTION_PROMPT.format(
        metadata_content=metadata_content,
        model_result_content=json.dumps(model_result, ensure_ascii=False, indent=2),
        selected_category=model_result.get("selected_category", "N/A"),
        病害类型=model_result.get("selected_category", "N/A"),
        is_disaster=decision.get("is_disaster", "N/A"),
        status=decision.get("status", "N/A"),
        final_score=decision.get("final_score", "N/A"),
        unet_score=pothole.get("score", "N/A"),
        target_ratio=pothole_stats.get("target_ratio_percent", "N/A"),
    )


async def upload_test_image(
    api: APIClient,
    workspace_id: str,
    verbose: bool = True,
) -> bool:
    """上传测试图片到工作区"""
    if not IMAGE_FILE.exists():
        print_error(f"测试图片不存在: {IMAGE_FILE}")
        return False

    if verbose:
        print_dim(f"上传图片: {IMAGE_FILE.name}")

    try:
        upload_result = await api.upload_workspace_file(workspace_id, IMAGE_FILE)
        if not upload_result.get("success", True):
            print_error(f"图片上传失败: {upload_result.get('message')}")
            return False

        if verbose:
            print_success(f"图片上传成功: {IMAGE_FILE.name}")
        return True
    except Exception as e:
        print_error(f"图片上传异常: {e}")
        return False


async def run_decision_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True,
) -> TestResult:
    """运行智能决策流程测试"""
    result = TestResult("bridge_decision_test", scenario_config)

    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stream_log_file = str(log_dir / f"decision_test_{timestamp}.log")

    print_test_header(scenario_config.get(
        "description",
        "桥梁病害智能决策测试 - 分析 → 研判提交 → 决策报告 → 报告提交"
    ))

    output_log = log_dir / f"disaster_judgment_{timestamp}.md"

    print_step(1, "验证数据源文件...", Colors.CYAN)

    missing_files = []
    for name, path in [
        ("元数据", METADATA_FILE),
        ("深度模型结果", MODEL_RESULT_FILE),
        ("图片", IMAGE_FILE),
    ]:
        if not path.exists():
            missing_files.append(f"{name}: {path}")

    if missing_files:
        error_msg = f"缺失数据源文件: {'; '.join(missing_files)}"
        print_error(error_msg)
        result.errors.append(error_msg)
        return result

    print_success("所有数据源文件就绪")

    print_step(2, "创建会话...", Colors.CYAN)
    session_result = await api.create_session(title="桥梁病害智能决策测试 - 花溪河大桥")

    if not session_result.get("success", True):
        print_error(f"会话创建失败: {session_result.get('message')}")
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result

    session_id = session_result.get("data", {}).get("id")
    workspace_id = session_result.get("data", {}).get("workspace_id")
    result.session_id = session_id
    print_success(f"会话创建成功: {session_id}")
    print_dim(f"Workspace ID: {workspace_id}")

    print_step(3, "上传检测图片...", Colors.CYAN)
    image_uploaded = await upload_test_image(api, workspace_id, verbose=verbose)

    if not image_uploaded:
        print_warning("图片上传失败，继续测试（可能不影响工具调用）")

    print_step(4, "构建决策流程提示词...", Colors.CYAN)

    try:
        prompt = build_prompt()
        print_dim(f"提示词长度: {len(prompt)} 字符")
    except Exception as e:
        print_error(f"提示词构建失败: {e}")
        result.errors.append(f"prompt_build: {e}")
        return result

    print_step(5, "创建对话并发送提示词...", Colors.CYAN)
    conv_result = await api.create_conversation(session_id, prompt)

    if not conv_result.get("success", True):
        print_error(f"对话创建失败: {conv_result.get('message')}")
        result.errors.append(f"create_conversation: {conv_result.get('message')}")
        return result

    conversation_id = conv_result.get("data", {}).get("conversation_id")
    result.conversation_id = conversation_id
    print_success(f"对话创建成功: {conversation_id}")

    print_step(6, "等待对话开始处理...", Colors.CYAN)
    await wait_for_conversation_state(api, conversation_id, "processing", timeout=15.0)

    print_step(7, "收集流式响应...", Colors.CYAN)
    judgment_timeout = scenario_config.get("judgment_timeout", 600.0)
    await collect_stream_output(api, conversation_id, result, verbose=verbose, timeout=judgment_timeout, stream_log_file=stream_log_file)

    max_followups = 3
    followup_count = 0

    while (result.next_conversation_id or result.detected_mode == "PLAN") and followup_count < max_followups:
        followup_count += 1
        if result.next_conversation_id:
            if verbose:
                print(f"{Colors.YELLOW}[Follow-up #{followup_count}] Auto-approved plan detected, continuing with conversation: {result.next_conversation_id}{Colors.ENDC}")
            next_conv_id = result.next_conversation_id
            result.next_conversation_id = None
            conversation_id = next_conv_id
            result.conversation_id = conversation_id
            await wait_for_conversation_state(api, conversation_id, "processing", timeout=10.0)
            await collect_stream_output(api, conversation_id, result, verbose=verbose, timeout=judgment_timeout, stream_log_file=stream_log_file)
        elif result.detected_mode == "PLAN":
            if verbose:
                print(f"{Colors.YELLOW}[Follow-up #{followup_count}] PLAN mode detected, sending approval...{Colors.ENDC}")
            try:
                approve_result = await api.approve_plan(result.workspace_id, approved=True)
                if verbose:
                    print(f"{Colors.DIM}[Follow-up #{followup_count}] Plan approval result: {approve_result.get('message', 'ok')}{Colors.ENDC}")
            except Exception as e:
                if verbose:
                    print(f"{Colors.DIM}[Follow-up #{followup_count}] Approve plan failed: {e}, trying create_conversation...{Colors.ENDC}")

            conv_result = await api.create_conversation(session_id, "可以")
            if conv_result.get("success", True):
                next_conv_id = conv_result.get("data", {}).get("conversation_id")
                if next_conv_id:
                    conversation_id = next_conv_id
                    result.conversation_id = conversation_id
                    result.detected_mode = None
                    await wait_for_conversation_state(api, conversation_id, "processing", timeout=10.0)
                    await collect_stream_output(api, conversation_id, result, verbose=verbose, timeout=judgment_timeout, stream_log_file=stream_log_file)
                else:
                    break
            else:
                print_error(f"Failed to create follow-up: {conv_result.get('message')}")
                break

    if followup_count > 0:
        if verbose:
            print_success(f"Completed {followup_count} follow-up conversation(s)")

    # [方案A] 增强错误恢复: 检查流输出状态并尝试恢复
    print_step(7.5, "Checking stream output status and recovery...", Colors.CYAN)

    conv_check = await api.get_conversation(conversation_id)
    current_state = conv_check.get("data", {}).get("state")

    if current_state == "running" and not result.response_text:
        if verbose:
            print(f"{Colors.YELLOW}[Recovery] Stream interrupted but conversation still running (state={current_state}){Colors.ENDC}")
            print(f"{Colors.DIM}[Recovery] Event count: {result.event_count}, Tool calls: {result.tool_calls}{Colors.ENDC}")

        extra_wait_timeout = min(600.0, judgment_timeout)
        if verbose:
            print(f"{Colors.DIM}[Recovery] Waiting up to {extra_wait_timeout:.0f}s for AI to complete...{Colors.ENDC}")

        recovery_result = await wait_for_conversation_state(
            api, conversation_id, "completed", timeout=extra_wait_timeout
        )

        recovery_state = recovery_result.get("data", {}).get("state")
        result.response_text = extract_response_text(recovery_result)

        if result.response_text:
            if verbose:
                print_success(f"[Recovery] Successfully captured response after recovery ({len(result.response_text)} chars)")
        else:
            if verbose:
                print_warning(f"[Recovery] No response captured, final state: {recovery_state}")
    elif current_state == "completed":
        if verbose:
            print_success("[Recovery] Conversation already completed")
        result.response_text = extract_response_text(conv_check)
    else:
        if verbose:
            print(f"{Colors.DIM}[Recovery] Current state: {current_state}{Colors.ENDC}")

    print_step(8, "等待对话完成...", Colors.CYAN)

    max_completion_wait = 900
    completion_wait_start = time.time()
    max_retries = 5
    retry_count = 0
    min_response_length = 100

    while time.time() - completion_wait_start < max_completion_wait:
        final_result = await wait_for_conversation_state(
            api, conversation_id, "completed", timeout=60.0
        )
        if not final_result:
            if verbose:
                print_warning(f"[Step 8] API returned None, retrying...")
            await asyncio.sleep(10)
            continue

        final_state = final_result.get("data", {}).get("state") if isinstance(final_result, dict) else None

        if final_state == "completed":
            result.response_text = extract_response_text(final_result)

            if result.response_text and len(result.response_text) < min_response_length:
                if verbose:
                    print_warning(f"Response too short ({len(result.response_text)} chars), may be confirmation message")
                    print(f"{Colors.DIM}[Response Preview] {result.response_text[:200]}...{Colors.ENDC}")

                if retry_count < max_retries:
                    if verbose:
                        print(f"{Colors.YELLOW}[Extended Wait] Waiting for actual execution...{Colors.ENDC}")
                    await asyncio.sleep(30)

                    try:
                        session_detail = await api.get_session(session_id)
                        conversations = session_detail.get("data", {}).get("conversations", [])
                        if conversations:
                            latest_conv = conversations[-1]
                            new_conv_id = latest_conv.get("id")
                            if new_conv_id and new_conv_id != conversation_id:
                                if verbose:
                                    print(f"{Colors.DIM}[Extended Wait] Found newer conversation: {new_conv_id}{Colors.ENDC}")
                                conversation_id = new_conv_id
                                result.conversation_id = conversation_id
                                retry_count += 1
                                continue
                    except Exception:
                        pass

                break
            else:
                if verbose:
                    print_success(f"Conversation completed with response ({len(result.response_text) if result.response_text else 0} chars)")
            break

        elif final_state == "running":
            if verbose:
                elapsed = int(time.time() - completion_wait_start)
                print(f"{Colors.DIM}[Completion Wait] Still running... ({elapsed}s elapsed){Colors.ENDC}")
            await asyncio.sleep(15)
        else:
            result.response_text = extract_response_text(final_result)
            break

    if time.time() - completion_wait_start >= max_completion_wait:
        if verbose:
            print_warning(f"[Completion Wait] Timeout after {max_completion_wait}s, capturing current state")

    # 验证工具调用
    print_step(9, "验证工具调用...", Colors.CYAN)

    # 尝试从 LLM decision trace log 获取工具调用
    log_file = Path(__file__).parent.parent.parent / "llm_decision_trace.log"
    if log_file.exists():
        with open(log_file, "r", encoding="utf-8") as f:
            log_content = f.read()

        # 解析 tool_call 事件
        import re
        tool_pattern = r'=== TOOL CALL ===\s+Tool Name: (\w+)'
        found_tools = re.findall(tool_pattern, log_content)
        if found_tools:
            result.tool_calls = list(set(found_tools))
            print_dim(f"从 LLM decision log 解析到工具: {result.tool_calls}")

    result.tool_calls = list(set(result.tool_calls))

    judgment_tool_called = "submit_ai_judgment_issue" in result.tool_calls
    report_tool_called = "submit_facility_report" in result.tool_calls

    if judgment_tool_called:
        print_success("OK submit_ai_judgment_issue 工具已调用")
    else:
        print_error("FAIL submit_ai_judgment_issue 工具未调用")
        result.errors.append("judgment_tool_not_called")

    if report_tool_called:
        print_success("OK submit_facility_report 工具已调用")
    else:
        print_error("FAIL submit_facility_report 工具未调用")
        result.errors.append("report_tool_not_called")

    print_step(10, "生成测试报告...", Colors.CYAN)

    model_result = load_model_results()
    decision = model_result.get("decision", {})
    unet_scores = model_result.get("unet_scores", {})

    with open(output_log, "w", encoding="utf-8") as f:
        f.write("# 桥梁病害智能决策测试报告\n\n")
        f.write(f"- **时间戳**: {timestamp}\n")
        f.write(f"- **会话ID**: {session_id}\n")
        f.write(f"- **对话ID**: {conversation_id}\n")
        f.write(f"- **工作区ID**: {workspace_id}\n")
        f.write("\n---\n\n")

        f.write("## 数据源文件\n\n")
        f.write(f"- 元数据: `{METADATA_FILE.name}`\n")
        f.write(f"- 模型结果: `{MODEL_RESULT_FILE.name}`\n")
        f.write(f"- 检测图片: `{IMAGE_FILE.name}` (已上传: {image_uploaded})\n")
        f.write("\n---\n\n")

        f.write("## 模型检测结果摘要\n\n")
        f.write(f"- **选定类别**: {model_result.get('selected_category')}\n")
        f.write(f"- **灾害判定**: {decision.get('is_disaster')}\n")
        f.write(f"- **状态**: {decision.get('status')}\n")
        f.write(f"- **最终分数**: {decision.get('final_score')}\n")
        f.write(f"- **决策来源**: {decision.get('decision_source')}\n")
        f.write("\n### U-Net坑洞检测\n\n")
        pothole = unet_scores.get("pothole", {})
        pothole_stats = pothole.get("stats", {})
        f.write(f"- **置信度**: {pothole.get('score')}\n")
        f.write(f"- **目标像素**: {pothole_stats.get('target_pixels')}\n")
        f.write(f"- **占比**: {pothole_stats.get('target_ratio_percent')}%\n")
        f.write(f"- **最大连通区域**: {pothole_stats.get('largest_component_area')} 像素\n")
        f.write("\n---\n\n")

        f.write("## 决策流程验证\n\n")
        f.write(f"- **thinking 工具调用**: {'需要查看日志确认' if result.thinking_content else '未检测到（可能未调用）'}\n")
        f.write(f"- **submit_ai_judgment_issue**: {'OK' if judgment_tool_called else 'FAIL'}\n")
        f.write(f"- **submit_facility_report**: {'OK' if report_tool_called else 'FAIL'}\n")
        f.write("\n### 所有工具调用\n\n")
        for tool in result.tool_calls:
            f.write(f"- {tool}\n")
        f.write("\n---\n\n")

        f.write("## AI 响应摘要\n\n")
        if result.response_text:
            f.write(f"长度: {len(result.response_text)} 字符\n\n")
            f.write("```\n")
            preview = result.response_text[:3000]
            f.write(preview)
            if len(result.response_text) > 3000:
                f.write(f"\n... (截断，共 {len(result.response_text)} 字符)")
            f.write("\n```\n")
        else:
            f.write("无响应内容\n")
        f.write("\n---\n\n")

        f.write("## 测试结果\n\n")
        if result.errors:
            f.write(f"**Status**: FAIL test failed\n")
            f.write(f"**Errors**: {result.errors}\n")
        elif judgment_tool_called and report_tool_called:
            f.write("**Status**: PASS test passed\n")
            f.write("决策流程完成！所有必需工具已成功调用。\n")
        else:
            f.write(f"**Status**: PARTIAL PASS\n")
            f.write(f"- submit_ai_judgment_issue: {'OK' if judgment_tool_called else 'FAIL'}\n")
            f.write(f"- submit_facility_report: {'OK' if report_tool_called else 'FAIL'}\n")
        f.write("\n---\n\n")

        f.write("## 事件统计\n\n")
        f.write(f"- **事件总数**: {result.event_count}\n")
        f.write(f"- **工具调用数**: {len(result.tool_calls)}\n")
        f.write(f"- **检测到的模式**: {result.detected_mode or 'N/A'}\n")
        f.write(f"- **思考内容长度**: {len(result.thinking_content)} 字符\n")

    print_success(f"测试报告已保存: {output_log}")

    print_step(11, "测试完成摘要...", Colors.CYAN)

    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.GREEN}  桥梁病害智能决策测试完成{Colors.ENDC}")
    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")

    print(f"{Colors.CYAN}决策流程验证:{Colors.ENDC}")
    print(f"  - submit_ai_judgment_issue: {'OK' if judgment_tool_called else 'FAIL'}")
    print(f"  - submit_facility_report: {'OK' if report_tool_called else 'FAIL'}")

    print(f"\n{Colors.CYAN}统计:{Colors.ENDC}")
    print(f"  - 事件数: {result.event_count}")
    print(f"  - 工具调用: {result.tool_calls}")
    print(f"  - 思考内容: {len(result.thinking_content)} 字符")
    print(f"  - 错误: {result.errors if result.errors else '无'}")

    print(f"\n{Colors.CYAN}报告: {output_log}{Colors.ENDC}\n")

    if result.errors:
        print(f"{Colors.RED}*** 测试完成但有错误 ***{Colors.ENDC}")
    elif judgment_tool_called and report_tool_called:
        print(f"{Colors.GREEN}*** 测试通过 ***{Colors.ENDC}")
    else:
        print(f"{Colors.YELLOW}*** 测试部分通过 ***{Colors.ENDC}")

    return result


# 别名，保持向后兼容
run_disaster_judgment_test = run_decision_test
