#!/usr/bin/env python3
"""
验证chat工具上下文传递修复的单元测试
"""
import sys
import os

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, backend_dir)

# 将输出写入文件
log_file = os.path.join(os.path.dirname(__file__), 'test_result.txt')

with open(log_file, 'w', encoding='utf-8') as f:
    def log(msg):
        print(msg)
        f.write(msg + '\n')
        f.flush()

    try:
        from service.agent_service.graph.subgraphs.tool_executor import _build_child_agent_chat_prompt
        from service.agent_service.prompts.templates.system_prompts import CHAT_SYSTEM_PROMPT

        log("=" * 60)
        log("测试1: 验证原始用户消息注入")
        log("=" * 60)

        task_description = "向用户说明深度模型因图片I/O错误识别失败"
        user_message = """## 任务：日常巡查病害辅助识别

巡查图片URL: http://192.168.110.191/.../test.jpg
设施名称: 测试大桥
"""

        system_prompt, context_prompt = _build_child_agent_chat_prompt(
            task_description=task_description,
            previous_results=[],
            parent_chain_messages=[],
            current_conversation_messages=[],
            user_message=user_message,
        )

        tests_pass = True

        checks = [
            ("用户原始请求段落标题存在", "用户原始请求/业务上下文" in context_prompt),
            ("任务内容注入正确", "日常巡查病害辅助识别" in context_prompt),
            ("图片URL注入正确", "http://192.168.110.191" in context_prompt),
        ]

        for name, result in checks:
            if result:
                log(f"✅ PASS: {name}")
            else:
                log(f"❌ FAIL: {name}")
                tests_pass = False

        log("\n" + "=" * 60)
        log("测试2: 验证工具错误结果自动注入")
        log("=" * 60)

        state = {
            "user_message": user_message,
            "tool_history": [
                {
                    "tool_name": "dailypatrol_ai_identify",
                    "error": None,
                    "result": {
                        "success": False,
                        "errorMessage": 'I/O error on GET request for "http://192.168.110.191/test.jpg": Unexpected end of file from server',
                        "code": 500,
                        "aiResult": None
                    }
                }
            ],
            "last_tool_name": "dailypatrol_ai_identify",
            "last_tool_result": {
                "success": False,
                "errorMessage": 'I/O error on GET request for "http://192.168.110.191/test.jpg": Unexpected end of file from server',
                "code": 500,
                "aiResult": None
            }
        }

        previous_results = state.get("previous_results", []) or []
        if not previous_results:
            tool_history = state.get("tool_history", []) or []
            if tool_history:
                previous_results = []
                for item in tool_history:
                    result_entry = {"tool_name": item.get("tool_name", "unknown")}
                    if item.get("error"):
                        result_entry["result"] = f"执行失败: {item.get('error')}"
                        if item.get("result"):
                            result_entry["result"] += f"\n结果: {item.get('result')}"
                    else:
                        result_entry["result"] = item.get("result", "")
                    previous_results.append(result_entry)

        user_message_from_state = state.get("user_message", "") or ""
        last_tool_result = state.get("last_tool_result", "")
        if last_tool_result and not any(str(last_tool_result) in str(r.get("result", "")) for r in previous_results):
            previous_results.append({
                "tool_name": state.get("last_tool_name", "last_tool"),
                "result": last_tool_result
            })

        system_prompt2, context_prompt2 = _build_child_agent_chat_prompt(
            task_description=task_description,
            previous_results=previous_results,
            parent_chain_messages=[],
            current_conversation_messages=[],
            user_message=user_message_from_state,
        )

        checks2 = [
            ("执行结果段落标题存在", "之前任务的执行结果" in context_prompt2),
            ("I/O错误信息注入正确", "I/O error" in context_prompt2),
            ("完整错误信息存在", "Unexpected end of file" in context_prompt2),
            ("工具名称注入正确", "dailypatrol_ai_identify" in context_prompt2),
        ]

        for name, result in checks2:
            if result:
                log(f"✅ PASS: {name}")
            else:
                log(f"❌ FAIL: {name}")
                tests_pass = False

        log("\n" + "=" * 60)
        log("测试3: 验证CHAT_SYSTEM_PROMPT约束规则")
        log("=" * 60)

        checks3 = [
            ("禁止编造规则存在", "绝对禁止编造" in CHAT_SYSTEM_PROMPT),
            ("具体禁止示例存在（图片模糊）", "图片模糊" in CHAT_SYSTEM_PROMPT),
            ("I/O错误示例存在", "I/O错误" in CHAT_SYSTEM_PROMPT),
            ("禁止编造失败理由条款存在", "禁止编造失败理由" in CHAT_SYSTEM_PROMPT),
        ]

        for name, result in checks3:
            if result:
                log(f"✅ PASS: {name}")
            else:
                log(f"❌ FAIL: {name}")
                tests_pass = False

        log("\n" + "=" * 60)
        if tests_pass:
            log("✅ 所有测试通过！修复验证成功")
            log("=" * 60)
            log("\n生成的context_prompt预览（前1500字符）：")
            log("-" * 60)
            log(context_prompt2[:1500])
            log("-" * 60)
        else:
            log("❌ 部分测试失败")
            sys.exit(1)

    except Exception as e:
        log(f"\n❌ 测试异常: {e}")
        import traceback
        log(traceback.format_exc())
        sys.exit(1)

print(f"测试结果已写入: {log_file}")
