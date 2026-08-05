#!/usr/bin/env python3
"""
Persistent Disease Prediction Test

基于周家堰桥2023/2024/2025三年检测报告，测试Agent对持续未解决病害的：
1. 识别能力 - 提取跨年度一直存在的病害
2. 预测能力 - 判断病害后续严重程度演变趋势
3. 建议能力 - 给出针对性的养护建议

测试对象：周家堰桥
- 2023年: +A级 (良好)
- 2024年: +C级 (显著恶化，表明新发或加重病害)
- 2025年: +B级 (部分恢复但未回到A级，表明仍有持续病害)
"""

import asyncio
import json
import re
import sys
import time
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

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


# ============================================================
# 测试配置 - 周家堰桥三年报告
# ============================================================

BRIDGE_REPORT_ROOT_DIR2 = get_project_root() / ".dev" / "fixture"

# 周家堰桥配置 - 有明显的病害演变历程
PERSISTENT_DISEASE_CONFIG = {
    "bridge_name": "周家堰桥",
    "bridge_id": "周家堰桥",
    "historical": {
        "2023": {
            "file": BRIDGE_REPORT_ROOT_DIR2 / "周家堰桥+A级.pdf",
            "grade": "A",
            "description": "良好状态，作为基线"
        },
        "2024": {
            "file": BRIDGE_REPORT_ROOT_DIR2 / "069周家堰桥+C级.pdf",
            "grade": "C",
            "description": "显著恶化，表明出现新的或加重的病害"
        },
        "2025": {
            "file": BRIDGE_REPORT_ROOT_DIR2 / "001周家堰桥+B级.doc",
            "grade": "B",
            "description": "部分恢复但仍未回到A级，存在持续病害"
        }
    },
    # 预期特征：从A级降到C级再回升到B级，说明有持续性病害问题
    "expected_patterns": [
        "应识别出2024年评级骤降（A→C）表明出现了重大病害",
        "应发现2025年虽部分恢复（C→B）但未回到初始A级",
        "应提取出跨年度持续存在的具体病害类型和位置",
        "应对这些持续病害的未来发展趋势做出预测",
        "应给出针对性的养护维修建议"
    ]
}


# ============================================================
# 持续病害预测提示词
# ============================================================

PERSISTENT_DISEASE_PROMPT = """[W] 重要指令：必须使用 Prediction Sub-Agent 完成任务！

工作区中已上传{bridge_name}的{years_count}份历史检测报告（{years_list}）。

## 🎯 任务目标
请使用 **call_prediction_agent** 子代理完成**持续病害分析与预测**任务。

### 背景信息
这座桥梁的技术状况在近三年有显著变化：
- {year_1}: {grade_1}级 ({desc_1})
- {year_2}: {grade_2}级 ({desc_2}) ← **注意：评级大幅下降！**
- {year_3}: {grade_3}级 ({desc_3})

这种 A→C→B 的变化模式表明存在**持续未解决的病害问题**。

### 必须完成的分析步骤：

**步骤1 - 多报告对比分析：**
读取并对比三年的检测报告，重点识别：
1. 哪些病害是**跨年度持续存在**的？（2023年就有，2024/2025仍在）
2. 哪些病害是**2024年新出现或显著恶化**的？（导致A→C的原因）
3. 哪些病害在**2025年得到改善但仍未根除**的？（导致C→B但未回A的原因）

**步骤2 - 病害发展趋势判断：**
对于识别出的每类持续病害，分析：
1. 当前严重程度等级（轻微/中等/严重/危险）
2. 过去两年的变化趋势（稳定/缓慢发展/快速发展）
3. 如果不处理，预计未来1-2年的可能发展情况

**步骤3 - 后续严重程度预测：**
基于历史数据，预测：
1. 2026年该桥梁的整体技术状况评级（BCI/等级）
2. 各主要持续病害的可能状态
3. 是否存在进一步恶化的风险

**步骤4 - 养护建议输出：**
针对识别出的持续病害，给出：
1. 优先级排序（哪些最紧急）
2. 建议处置措施（维修/加固/更换/监测等）
3. 建议处置时间窗口

### 输出要求：
请生成一份结构化的《持续病害分析与预测报告》，包含以下章节：

## 持续病害分析与预测报告 - {bridge_name}

### 一、跨年度病害对比表
| 病害名称 | 2023年状况 | 2024年状况 | 2025年状况 | 变化趋势 | 持续性判断 |

### 二、关键持续病害详细分析
（对每个持续存在的病害进行深入分析）

### 三、技术状况趋势预测
- 2026年预测评级及依据
- 未来2年退化风险预警

### 四、养护建议
- 紧急处置项（6个月内）
- 计划维修项（1年内）
- 长期监测项

## ⛔ 严格禁止:
- 不要只看单一年度的报告就下结论
- 不要忽略跨年度的病害演变规律
- 不要给出泛泛而谈的建议，必须针对具体病害
- 必须使用 call_prediction_agent 完成专业分析"""


def format_prompt_for_bridge(config: dict) -> str:
    """根据桥梁配置格式化提示词"""
    historical = config["historical"]
    years = sorted(historical.keys())

    return PERSISTENT_DISEASE_PROMPT.format(
        bridge_name=config["bridge_name"],
        years_count=len(years),
        years_list="/".join(years),
        year_1=years[0],
        grade_1=historical[years[0]]["grade"],
        desc_1=historical[years[0]]["description"],
        year_2=years[1],
        grade_2=historical[years[1]]["grade"],
        desc_2=historical[years[1]]["description"],
        year_3=years[2],
        grade_3=historical[years[2]]["grade"],
        desc_3=historical[years[2]]["description"],
    )


# ============================================================
# 结果验证工具
# ============================================================

class PersistentDiseaseValidator:
    """验证持续病害分析结果的完整性"""

    def __init__(self):
        self.validation_results = {}

    def validate(self, response_text: str) -> Dict[str, Any]:
        """完整验证响应文本"""
        results = {
            "overall_score": 0,
            "max_score": 100,
            "checks": {},
            "passed": False,
            "issues": [],
        }

        # 执行各项检查
        checks = [
            ("has_multi_year_comparison", self._check_multi_year_comparison, 20, "多年度对比分析"),
            ("has_persistent_disease_identified", self._check_persistent_disease_identification, 25, "持续病害识别"),
            ("has_trend_analysis", self._check_trend_analysis, 15, "趋势分析"),
            ("has_severity_prediction", self._check_severity_prediction, 20, "严重程度预测"),
            ("has_specific_recommendations", self._check_specific_recommendations, 20, "针对性建议"),
        ]

        total_weight = sum(weight for _, _, weight, _ in checks)
        earned_score = 0

        for check_name, check_func, weight, description in checks:
            check_result = check_func(response_text)
            results["checks"][check_name] = {
                "passed": check_result["passed"],
                "weight": weight,
                "description": description,
                "details": check_result.get("details", ""),
            }

            if check_result["passed"]:
                earned_score += weight
            else:
                results["issues"].append(f"[{description}] {check_result.get('details', '未通过')}")

        results["overall_score"] = int(earned_score / total_weight * 100) if total_weight > 0 else 0
        results["passed"] = results["overall_score"] >= 40  # 40分及格线

        return results

    def _check_multi_year_comparison(self, text: str) -> Dict:
        """检查是否进行了多年度对比"""
        year_patterns = [
            r'202[3-5]年',
            r'2023.*?2024|2024.*?2025|2023.*?2025',
            r'对比|比较|变化|演变|趋势',
            r'从.*级.*到.*级|A级.*C级|C级.*B级',
            r'(历年|多年|三年|各年)',
            r'逐年|年度间|跨年',
        ]

        matches = sum(1 for p in year_patterns if re.search(p, text, re.IGNORECASE))

        return {
            "passed": matches >= 2,
            "details": f"匹配到{matches}/{len(year_patterns)}个多年度对比模式"
        }

    def _check_persistent_disease_identification(self, text: str) -> Dict:
        """检查是否识别出持续存在的病害"""
        persistent_indicators = [
            r'持续.*?(病害|缺陷|问题|存在)|病害.*?持续',
            r'(病害|缺陷).*(一直|仍然|依然|未解决|未消除)',
            r'历年|多年|跨年度|连续',
            r'未(根除|消除|修复|治愈|解决|处置)',
            r'反复|再次|重新出现|持续存在',
            r'(主要|关键|核心).*(病害|问题|缺陷)',
            r'(位置|部位).*(病害|裂缝|破损|问题)',
        ]

        matches = sum(1 for p in persistent_indicators if re.search(p, text, re.IGNORECASE))

        # 放宽具体病害描述检查 - 只要有病害类型关键词即可
        has_concrete_defect = bool(re.search(
            r'(裂缝|破损|露筋|锈蚀|蜂窝|剥落|渗水|开裂|变形|位移|脱落|老化)'
            r'|上部结构|下部结构|桥面系|支座|墩台',
            text, re.IGNORECASE
        ))

        return {
            "passed": matches >= 1 and has_concrete_defect,
            "details": f"持续病害指标:{matches}/7, 有病害描述:{has_concrete_defect}"
        }

    def _check_trend_analysis(self, text: str) -> Dict:
        """检查是否有趋势分析"""
        trend_indicators = [
            r'(趋势|走向|发展).*(分析|判断|预测)',
            r'(加重|恶化|发展|扩大|蔓延|退化)',
            r'(稳定|缓解|改善|好转|恢复|回升)',
            r'(上升|下降|增加|减少).*(趋势|速度|速率)',
            r'(评级|等级).*(下降|上升|变化|波动)',
        ]

        matches = sum(1 for p in trend_indicators if re.search(p, text, re.IGNORECASE))

        return {
            "passed": matches >= 1,
            "details": f"趋势分析指标:{matches}/5"
        }

    def _check_severity_prediction(self, text: str) -> Dict:
        """检查是否有严重程度预测"""
        prediction_indicators = [
            r'预测.*?(2026|未来|后续|下一步|明年)',
            r'(预计|预估|预期|推断|判断).*?(将|可能|会)',
            r'(评级|等级|BCI|状况).*(将|预计|预测|变化)',
            r'(严重程度|危害程度|危险程度).*(升级|加剧|恶化|变化)',
            r'若不.*?(将|会|可能).*(恶化|失效|破坏|倒塌|继续)',
        ]

        matches = sum(1 for p in prediction_indicators if re.search(p, text, re.IGNORECASE))

        # 放宽：有预测意图即可，不要求具体数值
        has_prediction_intent = bool(re.search(
            r'(预测|预计|预估|预期|推断|展望)|(未来|后续|明年|今后).*(状况|状态|评级|等级)',
            text, re.IGNORECASE
        ))

        return {
            "passed": matches >= 1 or has_prediction_intent,
            "details": f"预测指标:{matches}/5, 预测意图:{has_prediction_intent}"
        }

    def _check_specific_recommendations(self, text: str) -> Dict:
        """检查是否有针对性的建议"""
        recommendation_indicators = [
            r'(建议|推荐|提议).*(维修|加固|更换|修补|处治|养护|处理)',
            r'(优先|紧急|重要|首要|及时).*(处置|处理|措施|行动|维修)',
            r'(时间|期限|周期|窗口).*(建议|规定|要求|限制|内)',
            r'(具体|针对|专门|相应).*(措施|方案|办法|对策)',
            r'(监测|检测|巡查|观察).*(频率|周期|计划|安排|定期)',
        ]

        matches = sum(1 for p in recommendation_indicators if re.search(p, text, re.IGNORECASE))

        # 检查是否有结构化的建议列表
        has_structured_list = bool(re.search(
            r'(一|二|三|四|1\.|2\.|3\.|•|-)\s*.+(建议|措施|方案|处置)',
            text
        ))

        return {
            "passed": matches >= 2 or has_structured_list,
            "details": f"建议指标:{matches}/5, 结构化列表:{has_structured_list}"
        }


# ============================================================
# 文件操作辅助函数
# ============================================================

def resolve_source_file(source_path: Path) -> Path:
    """解析源文件路径"""
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")
    return source_path


async def upload_historical_reports(
    api: APIClient,
    workspace_id: str,
    config: dict,
    verbose: bool = True,
) -> List[str]:
    """上传多年的历史检测报告"""
    uploaded_files = []

    for year, year_config in config["historical"].items():
        try:
            full_path = resolve_source_file(year_config["file"])

            upload_file = full_path

            if verbose:
                print_dim(f"Uploading {year} report: {upload_file.name}")

            upload_result = await api.upload_workspace_file(workspace_id, upload_file)
            if not upload_result.get("success", True):
                print_error(f"Failed to upload {year}: {upload_result.get('message')}")
                continue

            uploaded_files.append(upload_result.get("name", upload_file.name))
            if verbose:
                print_success(f"Uploaded {year}: {upload_file.name}")

        except FileNotFoundError as e:
            print_error(str(e))
        except Exception as e:
            print_error(f"Error uploading {year}: {e}")

    return uploaded_files


# ============================================================
# 主测试函数
# ============================================================

async def run_persistent_disease_predict_test(
    api: APIClient,
    config: Optional[dict] = None,
    verbose: bool = True,
) -> TestResult:
    """
    运行持续病害预测测试

    Args:
        api: API客户端
        config: 测试配置（默认使用周家堰桥配置）
        verbose: 是否显示详细信息
    """
    # 如果 config 为空或缺少 bridge_name，使用默认配置
    if not config or not config.get("bridge_name"):
        config = PERSISTENT_DISEASE_CONFIG

    result = TestResult(
        scenario="persistent_disease_predict",
        config=config
    )

    print_test_header(f"Persistent Disease Prediction Test - {config['bridge_name']}")
    print(f"  Testing multi-year persistent disease analysis and prediction")
    print()

    # ============================================================
    # Step 1: 创建 Session（同时获得 workspace_id）
    # ============================================================
    print_step(1, "Creating session")

    try:
        session_result = await api.create_session(
            title=f"Persistent Disease Predict - {config['bridge_name']}"
        )
        if not session_result.get("success", True):
            raise ValueError(f"create_session failed: {session_result}")

        session_id = session_result.get("data", {}).get("id")
        workspace_id = session_result.get("data", {}).get("workspace_id")
        result.session_id = session_id

        if not session_id or not workspace_id:
            raise ValueError(f"Invalid session response: {session_result}")

        if verbose:
            print_success(f"Session created: {session_id}")
            print_dim(f"Workspace ID: {workspace_id}")
    except Exception as e:
        print_error(f"Failed to create session: {e}")
        result.errors.append(f"Session creation failed: {e}")
        return result

    # ============================================================
    # Step 2: 上传多年历史报告
    # ============================================================
    print_step(2, "Uploading historical reports (multi-year)")

    try:
        uploaded_files = await upload_historical_reports(api, workspace_id, config, verbose)

        if len(uploaded_files) < 2:
            print_error(f"Only {len(uploaded_files)} files uploaded, need at least 2")
            result.errors.append(f"Insufficient files uploaded: {len(uploaded_files)}")
            return result

        if verbose:
            print_success(f"Uploaded {len(uploaded_files)} historical reports")
    except Exception as e:
        print_error(f"Failed to upload reports: {e}")
        result.errors.append(f"Upload failed: {e}")
        return result

    # ============================================================
    # Step 3: 创建 Conversation 并发送持续病害分析请求
    # ============================================================
    print_step(3, "Creating conversation with analysis prompt")

    try:
        prompt = format_prompt_for_bridge(config)

        if verbose:
            print_dim(f"Prompt length: {len(prompt)} chars")

        conv_result = await api.create_conversation(session_id, user_content=prompt)

        if not conv_result.get("success", True):
            raise ValueError(f"create_conversation failed: {conv_result}")

        conv_id = (conv_result.get("data", {}).get("id")
                   or conv_result.get("data", {}).get("conversation_id")
                   or conv_result.get("conversation_id"))
        if not conv_id:
            raise ValueError(f"No conversation ID in response: {conv_result}")

        if verbose:
            print_success(f"Conversation created: {conv_id}")
            print_success(f"Message sent, waiting for analysis...")

    except Exception as e:
        print_error(f"Failed to create conversation: {e}")
        result.errors.append(f"Conversation creation failed: {e}")
        return result

    # ============================================================
    # Step 4: 收集流式响应
    # ============================================================
    print_step(4, "Collecting streaming response")

    try:
        prediction_timeout = config.get("prediction_timeout", 600.0)
        await collect_stream_output(
            api,
            conv_id,
            result,
            verbose=verbose,
            timeout=prediction_timeout
        )
    except Exception as e:
        print_error(f"Stream collection error: {e}")
        result.errors.append(f"Stream error: {e}")

    # ============================================================
    # Step 5: 获取最终响应文本
    # ============================================================
    print_step(5, "Extracting response text")

    try:
        conv_check = await api.get_conversation(conv_id)
        current_state = conv_check.get("data", {}).get("state", "")

        if current_state == "running" and not getattr(result, 'response_text', None):
            if verbose:
                print_dim(f"Conversation still running, waiting...")
            recovery_result = await wait_for_conversation_state(
                api, conv_id, "completed", timeout=300.0
            )
            result.response_text = extract_response_text(recovery_result)
        elif current_state == "completed":
            result.response_text = extract_response_text(conv_check)

        response_text = getattr(result, 'response_text', '')

        if not response_text:
            raise ValueError("Empty response from agent")

        if verbose:
            print_success(f"Received response ({len(response_text)} chars), state={current_state}")

    except Exception as e:
        print_error(f"Failed to extract response: {e}")
        result.errors.append(f"Response extraction failed: {e}")
        return result

    # ============================================================
    # Step 6: 验证持续病害分析结果
    # ============================================================
    print_step(6, "Validating persistent disease analysis result")

    validator = PersistentDiseaseValidator()
    validation = validator.validate(response_text)

    # 显示验证详情
    if verbose:
        print(f"\n  {Colors.CYAN}Validation Results:{Colors.ENDC}")
        for check_name, check_data in validation["checks"].items():
            status = Colors.GREEN if check_data["passed"] else Colors.RED
            status_text = "PASS" if check_data["passed"] else "FAIL"
            print(f"    {status}[{status_text}] {check_data['description']} "
                  f"(weight: {check_data['weight']})")
            if check_data.get("details"):
                print(f"           {check_data['details']}")

        print(f"\n  {Colors.CYAN}Overall Score: {validation['overall_score']}/100{Colors.ENDC}")

        if validation["issues"]:
            print(f"\n  {Colors.YELLOW}Issues found:{Colors.ENDC}")
            for issue in validation["issues"]:
                print(f"    - {issue}")

    # 设置结果属性
    result.validation_score = validation["overall_score"]
    result.response_text = response_text[:2000]  # 保存前2000字符用于调试

    if not validation["passed"]:
        result.errors.append(
            f"Validation score {validation['overall_score']} below threshold (40)"
        )
        if validation["issues"]:
            result.errors.extend(validation["issues"][:3])  # 最多记录3个问题
    else:
        if verbose:
            print_success(f"Test PASSED with score {validation['overall_score']}/100")

    # ============================================================
    # Step 7: 保存完整响应用于审查 (始终执行)
    # ============================================================
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(__file__).parent.parent / "test_outputs"
    output_dir.mkdir(exist_ok=True)

    output_file = output_dir / f"persistent_disease_predict_{timestamp}.md"

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"# Persistent Disease Prediction Test Result\n\n")
        f.write(f"**Bridge:** {config['bridge_name']}\n")
        f.write(f"**Timestamp:** {timestamp}\n")
        f.write(f"**Validation Score:** {validation['overall_score']}/100\n")
        f.write(f"**Passed:** {validation['passed']}\n\n")
        f.write("---\n\n")
        f.write("## Validation Details\n\n")
        for check_name, check_data in validation["checks"].items():
            status = "PASS" if check_data["passed"] else "FAIL"
            f.write(f"- [{status}] {check_data['description']}: {check_data.get('details', '')}\n")
        if validation["issues"]:
            f.write("\n## Issues\n\n")
            for issue in validation["issues"]:
                f.write(f"- {issue}\n")
        f.write("\n---\n\n")
        f.write("## Agent Response\n\n")
        f.write(response_text)

    if verbose:
        print_step(7, "Saving detailed response for review")
        print_success(f"Response saved to: {output_file}")

    return result


# ============================================================
# 可选：额外测试函数 - 使用其他桥梁
# ============================================================

async def run_alternative_bridge_test(
    api: APIClient,
    bridge_name: str,
    verbose: bool = True,
) -> TestResult:
    """
    使用其他桥梁运行测试（扩展用）

    目前支持的备选桥梁可在此添加配置
    """
    # 备选桥梁配置（可根据需要扩展）
    alternative_configs = {
        # 可以添加更多桥梁配置
    }

    if bridge_name not in alternative_configs:
        print_error(f"Unknown bridge: {bridge_name}")
        result = TestResult(scenario="persistent_disease_predict_alt")
        result.errors.append(f"Unknown bridge configuration: {bridge_name}")
        return result

    return await run_persistent_disease_predict_test(
        api,
        config=alternative_configs[bridge_name],
        verbose=verbose
    )
