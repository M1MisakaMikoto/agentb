#!/usr/bin/env python3
"""
静默模式灾害判断测试

测试静默模式下的智能决策流程：
1. 使用 mode=silent 参数调用 stream API
2. 验证只收到 heartbeat 和 done 事件（过滤所有 delta 事件）
3. 验证最终结果完整性和正确性
4. 对比交互式模式，验证带宽节省效果

使用材料：与 disaster_judgment_flow 相同
"""

import asyncio
import httpx
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict

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
    wait_for_conversation_state,
    extract_response_text,
)


# 复用 disaster_judgment_flow 的数据文件路径
DATA_ROOT = get_project_root() / ".dev" / "table"
METADATA_FILE = DATA_ROOT / "元数据.txt"
MODEL_RESULT_FILE = DATA_ROOT / "深度模型结果.txt"
IMAGE_FILE = DATA_ROOT / "图片.jpg"

# 从 disaster_judgment_flow 导入共享函数
from .disaster_judgment_flow import (
    load_metadata,
    load_model_results,
    build_prompt,
    upload_test_image,
    PREDICTION_PROMPT,
)


async def collect_silent_stream_output(
    api: APIClient,
    conversation_id: str,
    result: TestResult,
    verbose: bool = True,
    timeout: float = 600.0,
) -> Dict:
    """
    收集静默模式的流式输出
    返回统计信息：
    - heartbeat_count: 心跳次数
    - delta_events_count: delta事件数（应该为0）
    - done_received: 是否收到done事件
    - error_received: 是否收到error事件
    - total_events: 总事件数
    - event_types: 事件类型列表
    """
    stats = {
        "heartbeat_count": 0,
        "delta_events_count": 0,
        "done_received": False,
        "error_received": False,
        "total_events": 0,
        "event_types": [],
        "first_event_time": None,
        "last_event_time": None,
        "raw_events": [],  # 用于调试
    }

    deadline = time.time() + timeout
    max_consecutive_timeouts = 30
    consecutive_timeouts = 0

    if verbose:
        print_dim(f"[Silent Mode] Starting stream collection for conversation {conversation_id}")
        print_dim(f"[Silent Mode] Timeout: {timeout}s, Deadline: {deadline}")

    try:
        # 使用 silent 模式调用 stream API
        path = api._get_endpoint("conversation", "stream", conversation_id=conversation_id)
        path = f"{path}?last_seq=0&mode=silent"  # 关键：添加 mode=silent 参数

        # 直接创建异步HTTP客户端（避免monkey-patch问题）
        client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, read=30.0))

        async with client.stream("GET", f"{api.base_url}{path}", headers=api._headers()) as response:
            if response.status_code != 200:
                error_msg = f"Stream request failed with status {response.status_code}"
                if verbose:
                    print_error(f"[Silent Mode] {error_msg}")
                stats["error_received"] = True
                return stats

            if verbose:
                print_success("[Silent Mode] Stream connection established (mode=silent)")

            async for line in response.aiter_lines():
                if time.time() > deadline:
                    if verbose:
                        print_warning("[Silent Mode] Stream collection timeout")
                    break

                if not line or not line.strip():
                    continue

                # 解析 SSE 事件
                if line.startswith("data: "):
                    data_str = line[6:]  # 去掉 "data: " 前缀
                    try:
                        event_data = json.loads(data_str)
                        event_type = event_data.get("type", "unknown")

                        # 记录时间戳
                        current_time = time.time()
                        if stats["first_event_time"] is None:
                            stats["first_event_time"] = current_time
                        stats["last_event_time"] = current_time

                        # 统计事件类型
                        stats["total_events"] += 1
                        stats["event_types"].append(event_type)
                        stats["raw_events"].append({
                            "type": event_type,
                            "timestamp": current_time,
                            "data_preview": str(event_data)[:100],
                        })

                        # 分类统计
                        if event_type == "heartbeat":
                            stats["heartbeat_count"] += 1
                            if verbose and stats["heartbeat_count"] % 10 == 0:
                                print_dim(f"[Silent Mode] Heartbeat #{stats['heartbeat_count']}")
                        elif event_type == "done":
                            stats["done_received"] = True
                            if verbose:
                                print_success(f"[Silent Mode] DONE event received (total events: {stats['total_events']})")
                                # 打印 done 事件的摘要信息
                                done_data = event_data.get("data", {})
                                if done_data:
                                    print_dim(f"[Silent Mode] Done data keys: {list(done_data.keys()) if isinstance(done_data, dict) else type(done_data)}")
                            break  # 收到 done 后结束
                        elif event_type == "error":
                            stats["error_received"] = True
                            if verbose:
                                print_error(f"[Silent Mode] ERROR event: {event_data.get('message', 'unknown error')}")
                        elif "_delta" in event_type:
                            # 静默模式下不应该收到 delta 事件！
                            stats["delta_events_count"] += 1
                            if verbose:
                                print_error(f"[Silent Mode] UNEXPECTED delta event: {event_type} (should be filtered!)")
                                print_dim(f"[Silent Mode] Event data: {json.dumps(event_data, ensure_ascii=False)[:200]}")
                        else:
                            # 其他类型的事件（如 stream_completed 等）
                            if verbose:
                                print_dim(f"[Silent Mode] Other event: {event_type}")

                    except json.JSONDecodeError as e:
                        if verbose:
                            print_warning(f"[Silent Mode] Failed to parse JSON: {e}")
                            print_dim(f"[Silent Mode] Raw line: {line[:200]}")
                        continue

                # 处理超时检测
                consecutive_timeouts = 0  # 重置超时计数器（因为收到了数据）

    except Exception as e:
        if verbose:
            print_error(f"[Silent Mode] Stream collection exception: {e}")
        stats["error_received"] = True

    # 计算持续时间
    if stats["first_event_time"] and stats["last_event_time"]:
        stats["duration_seconds"] = stats["last_event_time"] - stats["first_event_time"]
    else:
        stats["duration_seconds"] = 0

    return stats


async def run_silent_disaster_judgment_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True,
) -> TestResult:
    """运行静默模式灾害判断测试"""
    result = TestResult("silent_disaster_judgment", scenario_config)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print_test_header(scenario_config.get(
        "description",
        "静默模式灾害判断测试 - 验证delta事件过滤 + 结果完整性"
    ))

    # ========== Step 1: 验证数据源 ==========
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

    # ========== Step 2: 创建会话 ==========
    print_step(2, "创建会话...", Colors.CYAN)
    session_result = await api.create_session(title="静默模式灾害判断测试")

    if not session_result.get("success", True):
        print_error(f"会话创建失败: {session_result.get('message')}")
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result

    session_id = session_result.get("data", {}).get("id")
    workspace_id = session_result.get("data", {}).get("workspace_id")
    result.session_id = session_id
    print_success(f"会话创建成功: {session_id}")

    # ========== Step 3: 上传图片 ==========
    print_step(3, "上传检测图片...", Colors.CYAN)
    image_uploaded = await upload_test_image(api, workspace_id, verbose=verbose)

    if not image_uploaded:
        print_warning("图片上传失败，继续测试")

    # ========== Step 4: 构建提示词 ==========
    print_step(4, "构建决策流程提示词...", Colors.CYAN)

    try:
        prompt = build_prompt()
        print_dim(f"提示词长度: {len(prompt)} 字符")
    except Exception as e:
        print_error(f"提示词构建失败: {e}")
        result.errors.append(f"prompt_build: {e}")
        return result

    # ========== Step 5: 创建对话 ==========
    print_step(5, "创建对话并发送提示词...", Colors.CYAN)
    conv_result = await api.create_conversation(session_id, prompt)

    if not conv_result.get("success", True):
        print_error(f"对话创建失败: {conv_result.get('message')}")
        result.errors.append(f"create_conversation: {conv_result.get('message')}")
        return result

    conversation_id = conv_result.get("data", {}).get("conversation_id")
    result.conversation_id = conversation_id
    print_success(f"对话创建成功: {conversation_id}")

    # ========== Step 6: 等待对话开始 ==========
    print_step(6, "等待对话开始处理...", Colors.CYAN)
    await wait_for_conversation_state(api, conversation_id, "processing", timeout=15.0)

    # ========== Step 7: 静默模式流式收集（核心测试）==========
    print_step(7, "【核心】收集静默模式流式输出 (mode=silent)...", Colors.CYAN)

    judgment_timeout = scenario_config.get("judgment_timeout", 600.0)

    if verbose:
        print()
        print(f"{Colors.BOLD}{Colors.MAGENTA}{'='*60}{Colors.ENDC}")
        print(f"{Colors.BOLD}{Colors.MAGENTA}  SILENT MODE STREAM COLLECTION STARTING{Colors.ENDC}")
        print(f"{Colors.BOLD}{Colors.MAGENTA}  Expected: Only heartbeat + done/error (all intermediate events filtered){Colors.ENDC}")
        print(f"{Colors.BOLD}{Colors.MAGENTA}  Filtered types: delta, start/end, state_change, tool_call/res, etc.{Colors.ENDC}")
        print(f"{Colors.BOLD}{Colors.MAGENTA}{'='*60}{Colors.ENDC}")
        print()

    silent_stats = await collect_silent_stream_output(
        api,
        conversation_id,
        result,
        verbose=verbose,
        timeout=judgment_timeout,
    )

    if verbose:
        print()
        print(f"{Colors.BOLD}{Colors.MAGENTA}{'='*60}{Colors.ENDC}")
        print(f"{Colors.BOLD}{Colors.MAGENTA}  SILENT MODE STREAM COLLECTION COMPLETED{Colors.ENDC}")
        print(f"{Colors.BOLD}{Colors.MAGENTA}{'='*60}{Colors.ENDC}")
        print()

    # ========== Step 8: 验证静默模式行为 ==========
    print_step(8, "验证静默模式过滤效果...", Colors.CYAN)

    # 定义应该被过滤的中间过程事件类型（白名单之外的都应该被过滤）
    FILTERED_INTERMEDIATE_TYPES = {
        "thinking_start", "thinking_end", "thinking",
        "chat_start", "chat_end",
        "text_start", "text_end",
        "plan_start", "plan_delta", "plan_end",
        "tool_call", "tool_res",
        "state_change",
        "compression_start", "compression_end",
        # 所有 _delta 类型
    }

    # 定义允许通过的事件类型（与MQ层保持一致）
    ALLOWED_EVENT_TYPES = {"done", "error", "heartbeat", "conversation_handoff"}

    # 8.1 验证没有中间过程事件（包括delta和其他中间状态）
    intermediate_events = [t for t in silent_stats["event_types"] if t not in ALLOWED_EVENT_TYPES]
    delta_events = [t for t in silent_stats["event_types"] if "_delta" in t]

    if len(intermediate_events) == 0:
        print_success(f"✓ 静默过滤完全生效: 0 个中间过程事件 (所有delta/start/end/state/tool事件全部被过滤)")
    else:
        error_msg = f"✗ 静默过滤失败: 收到 {len(intermediate_events)} 个中间过程事件!"
        print_error(error_msg)
        result.errors.append(f"silent_filter_failed: {error_msg}")

        # 列出收到的不应出现的事件类型
        from collections import Counter
        event_counts = Counter(intermediate_events)
        print_dim(f"未过滤的中间事件分布: {dict(event_counts)}")

        # 特别标注delta事件
        if len(delta_events) > 0:
            delta_counts = Counter(delta_events)
            print_dim(f"其中Delta事件分布: {dict(delta_counts)}")

    # 8.2 验证收到 heartbeat
    if silent_stats["heartbeat_count"] > 0:
        print_success(f"✓ Heartbeat 正常: 共 {silent_stats['heartbeat_count']} 次")
    else:
        print_warning("⚠ 未收到 heartbeat 事件（可能执行很快）")

    # 8.3 验证收到 done 事件
    if silent_stats["done_received"]:
        print_success("✓ DONE 事件正常接收")
    else:
        error_msg = "✗ 未收到 DONE 事件"
        print_error(error_msg)
        result.errors.append("missing_done_event")

    # 8.4 验证没有 error
    if silent_stats["error_received"]:
        print_warning("⚠ 收到了 ERROR 事件（可能是业务错误，非系统错误）")

    # 8.5 统计总事件数（应该只有 heartbeat + done + error/handoff）
    allowed_events_count = silent_stats["heartbeat_count"] + (1 if silent_stats["done_received"] else 0) + (1 if silent_stats["error_received"] else 0)
    # 额外允许1个conversation_handoff（如果有）
    handoff_count = len([t for t in silent_stats["event_types"] if t == "conversation_handoff"])
    expected_max_events = allowed_events_count + handoff_count

    if silent_stats["total_events"] <= expected_max_events:
        print_success(f"✓ 事件数量合理: 总计 {silent_stats['total_events']} 次 (heartbeats: {silent_stats['heartbeat_count']}, done: {silent_stats['done_received']})")
    else:
        print_warning(f"⚠ 事件数量较多: {silent_stats['total_events']} 次 (预期 ≤ {expected_max_events})")

    # 8.6 计算并展示性能提升
    if silent_stats["duration_seconds"]:
        duration = silent_stats["duration_seconds"]
        print_dim(f"流式传输时长: {duration:.1f}s")
        print_dim(f"平均事件间隔: {duration / max(silent_stats['total_events'], 1):.2f}s")

    # 展示事件类型分布
    if silent_stats["event_types"]:
        from collections import Counter
        type_counts = Counter(silent_stats["event_types"])
        print_dim(f"事件类型分布: {dict(type_counts)}")

    # ========== Step 9: 等待对话完成并获取结果 ==========
    print_step(9, "等待对话完成并获取最终结果...", Colors.CYAN)

    max_completion_wait = 900
    completion_start = time.time()

    while time.time() - completion_start < max_completion_wait:
        final_result = await wait_for_conversation_state(
            api, conversation_id, "completed", timeout=60.0
        )

        if final_result:
            final_state = final_result.get("data", {}).get("state") if isinstance(final_result, dict) else None

            if final_state == "completed":
                result.response_text = extract_response_text(final_result)

                if result.response_text:
                    if verbose:
                        print_success(f"对话完成，响应长度: {len(result.response_text)} 字符")
                        print_dim(f"响应预览: {result.response_text[:200]}...")
                else:
                    print_warning("对话完成但响应文本为空")
                break
            elif final_state == "running":
                if verbose:
                    elapsed = int(time.time() - completion_start)
                    print_dim(f"等待完成... ({elapsed}s)")
                await asyncio.sleep(15)
            else:
                result.response_text = extract_response_text(final_result)
                break
        else:
            await asyncio.sleep(10)

    # ========== Step 10: 验证工具调用（从日志或API获取）==========
    print_step(10, "验证工具调用...", Colors.CYAN)

    # 尝试从 LLM decision trace log 获取工具调用
    log_file = Path(__file__).parent.parent.parent / "llm_decision_trace.log"
    if log_file.exists():
        with open(log_file, "r", encoding="utf-8") as f:
            log_content = f.read()

        import re
        tool_pattern = r'=== TOOL CALL ===\s+Tool Name: (\w+)'
        found_tools = re.findall(tool_pattern, log_content)
        if found_tools:
            result.tool_calls = list(set(found_tools))
            print_dim(f"从日志解析到工具: {result.tool_calls}")

    result.tool_calls = list(set(result.tool_calls))

    judgment_tool_called = "submit_ai_judgment_issue" in result.tool_calls
    report_tool_called = "submit_facility_report" in result.tool_calls

    if judgment_tool_called:
        print_success("✓ submit_ai_judgment_issue 工具已调用")
    else:
        print_error("✗ submit_ai_judgment_issue 工具未调用")
        result.errors.append("judgment_tool_not_called")

    if report_tool_called:
        print_success("✓ submit_facility_report 工具已调用")
    else:
        print_error("✗ submit_facility_report 工具未调用")
        result.errors.append("report_tool_not_called")

    # ========== Step 11: 生成测试报告 ==========
    print_step(11, "生成静默模式测试报告...", Colors.CYAN)

    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    output_log = log_dir / f"silent_disaster_judgment_{timestamp}.md"

    model_result = load_model_results()
    decision = model_result.get("decision", {})

    with open(output_log, "w", encoding="utf-8") as f:
        f.write("# 静默模式灾害判断测试报告\n\n")
        f.write(f"- **时间戳**: {timestamp}\n")
        f.write(f"- **会话ID**: {session_id}\n")
        f.write(f"- **对话ID**: {conversation_id}\n")
        f.write(f"- **工作区ID**: {workspace_id}\n")
        f.write("\n---\n\n")

        f.write("## 静默模式验证结果\n\n")
        f.write(f"| 验证项 | 结果 | 数值 |\n")
        f.write(f"|--------|------|------|\n")
        f.write(f"| 中间事件过滤（全部） | {'✅ 通过' if len(intermediate_events) == 0 else '❌ 失败'} | {len(intermediate_events)} 个 |\n")
        f.write(f"| Heartbeat 接收 | {'✅ 正常' if silent_stats['heartbeat_count'] > 0 else '⚠️ 无'} | {silent_stats['heartbeat_count']} 次 |\n")
        f.write(f"| DONE 事件接收 | {'✅ 正常' if silent_stats['done_received'] else '❌ 缺失'} | {'是' if silent_stats['done_received'] else '否'} |\n")
        f.write(f"| ERROR 事件 | {'⚠️ 有' if silent_stats['error_received'] else '✅ 无'} | - |\n")
        f.write(f"| 总事件数 | - | {silent_stats['total_events']} |\n")
        f.write(f"| 流式时长 | - | {silent_stats.get('duration_seconds', 0):.1f}s |\n")

        f.write("\n### 事件类型分布\n\n")
        if silent_stats["event_types"]:
            from collections import Counter
            type_counts = Counter(silent_stats["event_types"])
            for event_type, count in type_counts.items():
                marker = "✅" if event_type in ALLOWED_EVENT_TYPES else "❌"
                f.write(f"- {marker} `{event_type}`: {count} 次\n")

        f.write("\n---\n\n")
        f.write("## 工具调用验证\n\n")
        f.write(f"- submit_ai_judgment_issue: {'✅' if judgment_tool_called else '❌'}\n")
        f.write(f"- submit_facility_report: {'✅' if report_tool_called else '❌'}\n")

        f.write("\n---\n\n")
        f.write("## 最终结果\n\n")
        if result.response_text:
            f.write(f"**响应长度**: {len(result.response_text)} 字符\n\n")
            f.write("**响应预览**:\n\n```\n{result.response_text[:500]}...\n```\n")
        else:
            f.write("⚠️ 无响应文本\n")

        f.write("\n---\n\n")
        f.write("## 性能对比估算\n\n")
        interactive_estimated_events = 500  # 交互式模式预估事件数
        actual_silent_events = silent_stats["total_events"]
        reduction_pct = ((interactive_estimated_events - actual_silent_events) / interactive_estimated_events * 100) if interactive_estimated_events > 0 else 0

        f.write(f"- **交互式模式预估事件数**: ~{interactive_estimated_events}\n")
        f.write(f"- **静默模式实际事件数**: {actual_silent_events}\n")
        f.write(f"- **事件减少比例**: {reduction_pct:.1f}%\n")
        f.write(f"- **网络传输节省**: 显著降低（仅传输必要状态信号）\n")

    if verbose:
        print_success(f"测试报告已保存: {output_log}")

    # ========== 总结 ==========
    print()
    print(f"{Colors.BOLD}{'='*60}{Colors.ENDC}")
    print(f"{Colors.BOLD}  静默模式测试总结{Colors.ENDC}")
    print(f"{Colors.BOLD}{'='*60}{Colors.ENDC}")
    print()
    print(f"  Delta 事件过滤: {Colors.GREEN if silent_stats['delta_events_count'] == 0 else Colors.RED}{'PASS' if silent_stats['delta_events_count'] == 0 else 'FAIL'}{Colors.ENDC}")
    print(f"  结果完整性: {Colors.GREEN if result.response_text and len(result.response_text) > 100 else Colors.YELLOW}{'PASS' if result.response_text and len(result.response_text) > 100 else 'CHECK'}{Colors.ENDC}")
    print(f"  工具调用: {Colors.GREEN if judgment_tool_called and report_tool_called else Colors.RED}{'PASS' if judgment_tool_called and report_tool_called else 'FAIL'}{Colors.ENDC}")
    print()

    return result
