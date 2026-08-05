#!/usr/bin/env python3
"""
Bridge Inspection Report Prediction Test

基于陈家阁大桥2018/2020/2022历史检测报告，预测2024年报告并与真实报告对比
"""

import asyncio
import json
import os
import re
import sys
import time
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional, Dict

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
# 健康评级提取工具
# ============================================================

GRADE_PATTERNS = [
    r'\+([A-E])级',           # +C级, +B级
    r'([A-E])级',             # C级, B级
    r'技术状况等级[：:\s]*([A-E])',  # 技术状况等级：C
    r'技术评定[：:\s]*([A-E])',  # 技术评定：C
    r'状况等级[：:\s]*([A-E])',   # 状况等级：C
    r'评定等级[：:\s]*([A-E])',   # 评定等级：C
    r'BCI.*?等级[：:\s]*([A-E])',  # BCI...等级：C
    r'总体评级[：:\s]*([A-E])',   # 总体评级：C
    r'桥梁评级[：:\s]*([A-E])',   # 桥梁评级：C
]

GRADE_DESCRIPTIONS = {
    "A": "完好",
    "B": "良好",
    "C": "合格",
    "D": "不合格",
    "E": "危险",
}

GRADE_THRESHOLDS = [
    (90, "A", "完好"),
    (80, "B", "良好"),
    (66, "C", "合格"),
    (50, "D", "不合格"),
    (0, "E", "危险"),
]


def extract_grade_from_filename(filename: str) -> Optional[str]:
    """从文件名中提取健康评级（如 '+C级' -> 'C'）"""
    for pattern in GRADE_PATTERNS[:2]:  # 只用文件名相关的模式
        match = re.search(pattern, filename)
        if match:
            return match.group(1)
    return None


def extract_grade_from_text(text: str) -> Optional[str]:
    """从报告内容中提取健康评级"""
    # 优先搜索明确的等级描述
    priority_patterns = [
        r'技术状况等级[：:\s]*([A-E])',
        r'评定等级[：:\s]*([A-E])',
        r'总体评级[：:\s]*([A-E])',
        r'桥梁评级[：:\s]*([A-E])',
        r'技术评定[：:\s]*([A-E])',
    ]

    for pattern in priority_patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)

    # 搜索 BCI 值对应的等级
    bci_match = re.search(r'BCI[值\s：:]*(\d{2}\.?\d*)', text)
    if bci_match:
        bci_value = float(bci_match.group(1))
        grade = get_grade_from_bci(bci_value)
        if grade:
            return grade

    # 通用模式搜索
    for pattern in GRADE_PATTERNS[3:]:
        match = re.search(pattern, text)
        if match:
            return match.group(1)

    return None


def get_grade_from_bci(bci: float) -> Optional[str]:
    """根据 BCI 分数确定等级"""
    for threshold, grade, desc in GRADE_THRESHOLDS:
        if bci >= threshold:
            return grade
    return "E"


def get_bci_from_grade(grade: str) -> Tuple[float, float]:
    """根据等级推断 BCI 范围（用于搜索匹配）"""
    grade_ranges = {
        "A": (90, 100),
        "B": (80, 90),
        "C": (66, 80),
        "D": (50, 66),
        "E": (0, 50),
    }
    return grade_ranges.get(grade, (0, 100))


def compare_grades(predicted_grade: str, ground_truth_grade: str) -> Dict:
    """比对预测评级和真实评级"""
    result = {
        "predicted": predicted_grade,
        "ground_truth": ground_truth_grade,
        "match": False,
        "match_description": "",
        "grade_distance": 0,
        "bci_approx_match": False,
    }

    if predicted_grade == ground_truth_grade:
        result["match"] = True
        result["match_description"] = f"[OK] 完全匹配 - 预测评级与真实评级均为 {predicted_grade}级"
    else:
        # 计算等级距离（用于评估接近程度）
        grade_order = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1}
        distance = abs(grade_order.get(predicted_grade, 0) - grade_order.get(ground_truth_grade, 0))
        result["grade_distance"] = distance

        if distance == 1:
            result["match_description"] = f"[W] 相邻等级 - 预测{predicted_grade}级 vs 真实{ground_truth_grade}级（差1级）"
        else:
            result["match_description"] = f"[X] 等级偏差 - 预测{predicted_grade}级 vs 真实{ground_truth_grade}级（差{distance}级）"

    # BCI 近似匹配检查
    if predicted_grade and ground_truth_grade:
        pred_range = get_bci_from_grade(predicted_grade)
        truth_range = get_bci_from_grade(ground_truth_grade)

        # 检查 BCI 范围是否有重叠
        if pred_range[0] <= truth_range[1] and pred_range[1] >= truth_range[0]:
            result["bci_approx_match"] = True

    return result


def analyze_grade_in_prediction(text: str) -> Optional[Dict]:
    """分析预测文本中的等级信息 - 优先提取预测值"""
    result = {
        "grade": None,
        "bci": None,
        "confidence": "low",
        "source": None,
    }

    if not text:
        return result

    # ============================================================
    # 第一步：优先提取预测年份的 BCI 值（最重要）
    # ============================================================

    # 预测相关模式：优先匹配"预计"、"预测"、"将达"等关键词后的 BCI 值
    # [^0-9]* 允许任意非数字字符干扰（中文括号、markdown加粗符号等）
    prediction_bci_patterns = [
        r'预计\d{4}年.*?BCI[^0-9]*?(?:将达|为|是|达到)?[^0-9]*?(\d{2}\.?\d*)',  # 预计2024年...BCI将达93.5
        r'预测\d{4}年.*?BCI[^0-9]*?(?:为|是|达到)?[^0-9]*?(\d{2}\.?\d*)',  # 预测2024年...BCI为93.5
        r'\d{4}年.*?预测.*?BCI[^0-9]*?(?:为|是|达到)?[^0-9]*?(\d{2}\.?\d*)',  # 2024年...预测...BCI为**75.1分**
        r'未来.*?BCI[^0-9]*?(?:将达|为|是)?[^0-9]*?(\d{2}\.?\d*)',  # 未来...BCI将达93.5
        r'BCI[^0-9]*?(?:将达|为|是|达到)?[^0-9]*?(\d{2}\.?\d*)',  # 通用BCI+动词+数字模式
        r'将达\s*(\d{2}\.?\d*)',  # BCI将达93.5
        r'升至\s*(\d{2}\.?\d*)',  # 升至93.5
        r'达到\s*(\d{2}\.?\d*)',  # 达到93.5
    ]

    bci_value = None
    for pattern in prediction_bci_patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            try:
                potential_bci = float(match)
                if 50 <= potential_bci <= 100:
                    bci_value = potential_bci
                    break
            except ValueError:
                continue
        if bci_value:
            break

    # ============================================================
    # 第二步：如果没有找到预测 BCI，提取所有 BCI 并取最新的
    # ============================================================
    if not bci_value:
        # 通用 BCI 提取模式 - 允许任意非数字字符干扰
        general_bci_patterns = [
            r'BCI[^0-9]*?(\d{2}\.?\d*)',  # 通用BCI后接任意非数字再到数字
            r'BCI[值\s：:＝=：为]*(\d{2}\.?\d*)',  # BCI:93.5, BCI值93.5, BCI值为93.5
            r'BCI\s*[为是]+\s*(\d{2}\.?\d*)',  # BCI为93.5
            r'BCI[值为]\s*(\d{2}\.?\d*)',  # BCI值为93.5, BCI为93.5
            r'(\d{2}\.?\d*)\s*分',  # 75.1分
            r'(\d{2}\.?\d*)\s*级',  # 93.5级
            r'技术状况指数[^0-9]*?(\d{2}\.?\d*)',  # 技术状况指数为75.1
        ]

        all_bcis = []
        for pattern in general_bci_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                try:
                    potential_bci = float(match)
                    if 50 <= potential_bci <= 100:
                        all_bcis.append(potential_bci)
                except ValueError:
                    continue

        # 取最后一个（通常是最新/预测的）
        if all_bcis:
            bci_value = all_bcis[-1]

    # ============================================================
    # 第三步：如果找到 BCI，计算等级
    # ============================================================
    if bci_value:
        result["bci"] = bci_value
        result["grade"] = get_grade_from_bci(bci_value)
        result["confidence"] = "high"
        result["source"] = "bci_calculation"

    # ============================================================
    # 第四步：如果没有 BCI，尝试直接提取等级
    # ============================================================
    if not result["grade"]:
        grade = extract_grade_from_text(text)
        if grade:
            result["grade"] = grade
            result["confidence"] = "medium"
            result["source"] = "text_pattern"

    # ============================================================
    # 第五步：尝试从文本描述中推断等级
    # ============================================================
    if not result["grade"] and not result["bci"]:
        # 搜索明确的等级描述
        grade_match = re.search(r'([A-E])级\s*[（(]?(?:完好|良好|合格|不合格|危险)', text)
        if grade_match:
            result["grade"] = grade_match.group(1)
            result["confidence"] = "medium"
            result["source"] = "text_pattern"
        else:
            grade_match2 = re.search(r'等级[：为：:：]*\s*([A-E])', text)
            if grade_match2:
                result["grade"] = grade_match2.group(1)
                result["confidence"] = "medium"
                result["source"] = "text_pattern"

    # ============================================================
    # 第六步：如果仍未提取到等级，尝试从 "BCI值XX级" 格式中提取
    # 例如："89.69（B级，良好）" 或 "BCI值89.69，B级"
    # ============================================================
    if not result["grade"]:
        # 匹配 BCI值XX级 或 (XX级 格式
        grade_in_parentheses = re.search(r'[（(]\s*([A-E])级', text)
        if grade_in_parentheses:
            result["grade"] = grade_in_parentheses.group(1)
            result["confidence"] = "medium"
            result["source"] = "parentheses_pattern"
        else:
            # 匹配 "B级" 在数字附近的模式
            grade_near_number = re.search(r'(\d{2}\.?\d*)[^（(]*[（(]?\s*([A-E])级', text)
            if grade_near_number:
                result["grade"] = grade_near_number.group(2)
                result["confidence"] = "medium"
                result["source"] = "near_number_pattern"

    # ============================================================
    # 第七步：特殊处理 "BCI值为89.69（B级）" 这种格式
    # 同时提取 BCI 值和等级
    # ============================================================
    bci_with_grade = re.search(r'BCI[值为\s]*(\d{2}\.?\d*)[^）)]*[（(]\s*([A-E])级', text)
    if bci_with_grade:
        if not result["bci"]:
            result["bci"] = float(bci_with_grade.group(1))
        if not result["grade"]:
            result["grade"] = bci_with_grade.group(2)
            result["confidence"] = "high"
            result["source"] = "bci_grade_combined"

    return result


def grade_evaluation_score(comparison: Dict) -> int:
    """计算评级比对的得分（用于测试评估）"""
    if comparison["match"]:
        return 100
    elif comparison["bci_approx_match"] and comparison["grade_distance"] == 1:
        return 70
    elif comparison["bci_approx_match"]:
        return 50
    elif comparison["grade_distance"] == 1:
        return 40
    elif comparison["grade_distance"] == 2:
        return 20
    else:
        return 0


BRIDGE_REPORT_ROOT = get_project_root() / ".dev" / "fixture" / "桥梁检测报告"
BRIDGE_REPORT_ROOT_DIR2 = get_project_root() / ".dev" / "fixture" / "桥梁检测报告2"
FIXTURE_ROOT = get_project_root() / ".dev" / "fixture"

# 桥梁列表配置 - 每座桥包含历史报告和真实报告路径
BRIDGE_CONFIGS = {
    "陈家阁大桥": {
        "historical": {
            "2018": FIXTURE_ROOT / "12陈家阁大桥定期检测2018.10.docx",
            "2020": FIXTURE_ROOT / "03 陈家阁大桥.doc",
            "2022": FIXTURE_ROOT / "09 陈家阁立交.doc",
        },
        "ground_truth": FIXTURE_ROOT / "003 陈家阁大桥+C级.doc",
    },
    "朝阳寺立交桥": {
        "historical": {
            "2018": BRIDGE_REPORT_ROOT / "2018" / "02朝阳寺立交桥.docx",
            "2020": BRIDGE_REPORT_ROOT / "2020" / "07 朝阳寺立交桥.doc",
            "2022": BRIDGE_REPORT_ROOT / "2022" / "02 朝阳寺立交桥.doc",
        },
        "ground_truth": BRIDGE_REPORT_ROOT / "2024" / "007 朝阳寺立交桥+B级.doc",
    },
    "陈家湾桥": {
        "historical": {
            "2018": BRIDGE_REPORT_ROOT / "2018" / "10陈家湾桥2018.10.docx",
            "2020": BRIDGE_REPORT_ROOT / "2020" / "09 陈家湾桥.doc",
            "2022": BRIDGE_REPORT_ROOT / "2022" / "07 陈家湾桥-已完成.doc",
        },
        "ground_truth": BRIDGE_REPORT_ROOT / "2024" / "009陈家湾桥+A级.doc",
    },
    "九中立交桥": {
        "historical": {
            "2018": BRIDGE_REPORT_ROOT / "2018" / "04九中立交桥.docx",
            "2020": BRIDGE_REPORT_ROOT / "2020" / "01 九中立交桥.doc",
            "2022": BRIDGE_REPORT_ROOT / "2022" / "03 九中立交桥.doc",
        },
        "ground_truth": BRIDGE_REPORT_ROOT / "2024" / "001九中立交桥+B级 .doc",
    },
}

# 数据集2 - 大渡口桥检-修改 7.29 (2022历史) -> 2024年PDF真实验证
BRIDGE_CONFIGS_DIR2 = {
    "金家湾立交桥": {
        "historical": {
            "2022": BRIDGE_REPORT_ROOT_DIR2 / "大渡口桥检-修改 7.29" / "05 金家湾立交桥.doc",
        },
        "ground_truth": BRIDGE_REPORT_ROOT_DIR2 / "2024年大渡口区定期检查报告扫描件" / "002 金家湾立交桥+A级.pdf",
        "ground_truth_year": 2024,
    },
    "建胜大桥": {
        "historical": {
            "2022": BRIDGE_REPORT_ROOT_DIR2 / "大渡口桥检-修改 7.29" / "08 建胜大桥.doc",
        },
        "ground_truth": BRIDGE_REPORT_ROOT_DIR2 / "2024年大渡口区定期检查报告扫描件" / "005建胜大桥+A级.pdf",
        "ground_truth_year": 2024,
    },
    "朝阳寺立交桥": {
        "historical": {
            "2022": BRIDGE_REPORT_ROOT_DIR2 / "大渡口桥检-修改 7.29" / "02 朝阳寺立交桥.doc",
        },
        "ground_truth": BRIDGE_REPORT_ROOT_DIR2 / "2024年大渡口区定期检查报告扫描件" / "007 朝阳寺立交桥+B级.pdf",
        "ground_truth_year": 2024,
    },
}

# 当前测试使用的桥梁配置（默认陈家阁大桥）
HISTORICAL_FILES = BRIDGE_CONFIGS["陈家阁大桥"]["historical"]
GROUND_TRUTH_2024 = BRIDGE_CONFIGS["陈家阁大桥"]["ground_truth"]

# 设施ID映射表（用于 submit_facility_forecast 工具）
FACILITY_ID_MAP = {
    "陈家阁大桥": "BR-CJG",
    "朝阳寺立交桥": "BR-CYS",
    "陈家湾桥": "BR-CJW",
    "九中立交桥": "BR-JZL",
    "金家湾立交桥": "BR-JJW",
    "建胜大桥": "BR-JS",
}

PREDICTION_PROMPT_TEMPLATE = """[W] 必须使用 call_prediction_agent 子代理完成预测任务！

工作区已上传 {historical_years} 历史检测报告。

任务目标：完成桥梁技术状况预测分析

步骤：
1. 调用 call_prediction_agent，task_description 要求：
   - 读取并分析历史报告
   - calculate_bci 计算每年BCI
   - predict_trend 预测 {next_year} 年趋势
   - query_standard 查询 CJJ 99-2017 规范
   - document(operation=w) 生成 {next_year}年桥梁检测报告预测.docx，包含工程概况、规范依据、检测结果、BCI评定、趋势预测、结论建议

2. 等待 Prediction Agent 完成后检查报告文件

3. 调用 submit_facility_forecast 提交记录：
   facilityId="{facility_id}", facilityName="{bridge_name}", predictYear={next_year},
   predictedHealthScore=BCI分数, predictedRiskLevel=等级(A/B低/C中/D高), summary=结论摘要

禁止事项：
- 不得直接使用 read_document 或 document 处理历史报告
- 不得自行BCI计算或趋势分析
- 不得跳过子代理直接生成报告
- 必须委托给 call_prediction_agent 处理
- 不得省略 submit_facility_forecast 提交步骤"""

PREDICTION_PROMPT = PREDICTION_PROMPT_TEMPLATE.format(
    next_year=2024,
    historical_years="2018/2020/2022年",  # 默认值，数据集1
    facility_id="BR-CJG",
    bridge_name="陈家阁大桥",
)


def resolve_source_file(source_path: Path) -> Path:
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")
    return source_path



async def upload_historical_reports(
    api: APIClient,
    workspace_id: str,
    verbose: bool = True,
    historical_files: dict = None,
) -> List[str]:
    """上传历史检测报告

    Args:
        api: API客户端
        workspace_id: 工作区ID
        verbose: 是否显示详细信息
        historical_files: 历史文件映射，格式为 {"2018": Path, "2020": Path, ...}
                         如果为None，则使用默认的 HISTORICAL_FILES
    """
    if historical_files is None:
        historical_files = HISTORICAL_FILES

    uploaded_files = []

    for year, file_path in historical_files.items():
        try:
            full_path = resolve_source_file(file_path)

            upload_file = full_path
            if verbose:
                print_dim(f"Uploading {year} report: {upload_file.name}")

            if verbose:
                print_dim(f"Uploading {year} report: {upload_file.name}")

            upload_result = await api.upload_workspace_file(workspace_id, upload_file)
            if not upload_result.get("success", True):
                print_error(f"Failed to upload {year}: {upload_result.get('message')}")
                continue

            uploaded_files.append(upload_file.name)
            if verbose:
                print_success(f"Uploaded {year}: {upload_file.name}")
        except FileNotFoundError as e:
            print_error(str(e))
        except Exception as e:
            print_error(f"Error uploading {year}: {e}")

    return uploaded_files


async def read_ground_truth_report(ground_truth_path: Path) -> str:
    full_path = resolve_source_file(ground_truth_path)

    # 本机可能没有 Word/LibreOffice/pandoc 等 .doc/.pdf 解析工具，
    # 读取失败时降级为空内容（评级仍可从文件名提取），不让场景直接失败。
    try:
        suffix = full_path.suffix.lower()
        backend_dir = str(Path(__file__).resolve().parents[2])
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        from service.agent_service.tools.document_tools import _docx_read, _convert_doc_to_docx, _pdf_read
        import time as _time

        if suffix == ".docx":
            result = _docx_read(str(full_path))
        elif suffix == ".doc":
            docx_path = None
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    docx_path = _convert_doc_to_docx(str(full_path))
                    if docx_path:
                        break
                    if attempt < max_retries - 1:
                        _time.sleep(2 * (attempt + 1))
                except Exception:
                    if attempt < max_retries - 1:
                        _time.sleep(2 * (attempt + 1))

            if not docx_path:
                print_warning(
                    f"Failed to convert .doc ground truth locally ({full_path.name}), "
                    "falling back to filename grade only"
                )
                return ""
            result = _docx_read(docx_path)
        elif suffix == ".pdf":
            # PDF格式支持 - 使用pymupdf4llm进行LLM友好的解析
            result = _pdf_read(str(full_path), use_llm_parsing=True)
        else:
            result = {"error": f"Unsupported format: {suffix}", "result": None}

        if result.get("error"):
            print_warning(f"Ground truth read skipped: {result['error']}")
            return ""
        return result["result"].get("content", "")
    except Exception as exc:
        print_warning(f"Ground truth read skipped: {exc}")
        return ""


async def run_bridge_predict_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True,
    bridge_name: str = "陈家阁大桥",
) -> TestResult:
    """
    运行桥梁预测测试

    Args:
        api: API客户端
        scenario_config: 场景配置
        verbose: 是否显示详细信息
        bridge_name: 桥梁名称，用于从 BRIDGE_CONFIGS 选择配置
                   默认 "陈家阁大桥"，可选择 "陈家阁大桥"、"朝阳寺立交桥"、"陈家湾桥"、"九中立交桥"
    """
    # 获取桥梁配置 - 优先从数据集2查找，然后从数据集1查找
    bridge_config = None
    if bridge_name in BRIDGE_CONFIGS_DIR2:
        bridge_config = BRIDGE_CONFIGS_DIR2[bridge_name]
    elif bridge_name in BRIDGE_CONFIGS:
        bridge_config = BRIDGE_CONFIGS[bridge_name]

    if bridge_config is None:
        all_bridges = list(BRIDGE_CONFIGS.keys()) + list(BRIDGE_CONFIGS_DIR2.keys())
        raise ValueError(f"Unknown bridge: {bridge_name}. Available: {all_bridges}")

    historical_files = bridge_config["historical"]
    ground_truth_path = bridge_config["ground_truth"]
    ground_truth_year = bridge_config.get("ground_truth_year", 2024)

    result = TestResult("bridge_predict", scenario_config)
    result.bridge_name = bridge_name
    result.ground_truth_year = ground_truth_year

    stream_log_dir = get_project_root() / "logs" / "e2e_stream_traces"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stream_log_file = str(stream_log_dir / f"bridge_predict_{bridge_name}_{timestamp}.log")

    print_test_header(scenario_config.get(
        "description",
        f"Bridge Inspection Report - {bridge_name} Prediction Test"
    ))

    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    output_log = log_dir / f"bridge_predict_{bridge_name}_{timestamp}.md"

    print_step(1, f"Validating {bridge_name} historical report files...", Colors.CYAN)
    missing_files = []
    for year, path in historical_files.items():
        if not path.exists():
            missing_files.append(f"{year}: {path.name}")

    if missing_files:
        error_msg = f"Missing historical files for {bridge_name}: {'; '.join(missing_files)}"
        print_error(error_msg)
        result.errors.append(error_msg)
        return result

    has_ground_truth = ground_truth_path.exists()
    if has_ground_truth:
        print_success(f"Ground truth 2024 found: {ground_truth_path.name}")
    else:
        print_dim(f"Ground truth 2024 not found (comparison will be skipped): {ground_truth_path.name}")

    print_step(2, "Creating session...", Colors.CYAN)
    session_result = await api.create_session(title=f"Bridge Report Prediction - {bridge_name}")
    if not session_result.get("success", True):
        print_error(f"Failed to create session: {session_result.get('message')}")
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result

    session_id = session_result.get("data", {}).get("id")
    workspace_id = session_result.get("data", {}).get("workspace_id")
    result.session_id = session_id
    result.workspace_id = workspace_id
    print_success(f"Session created: {session_id}")
    print_dim(f"Workspace ID: {workspace_id}")

    print_step(3, f"Uploading {bridge_name} historical reports...", Colors.CYAN)
    # 生成历史年份描述
    historical_years_desc = "/".join(historical_files.keys()) if historical_files else "unknown"
    print(f"{Colors.DIM}  Historical years: {historical_years_desc}{Colors.ENDC}")

    uploaded = await upload_historical_reports(api, workspace_id, verbose=verbose, historical_files=historical_files)

    min_reports = len(historical_files)
    if len(uploaded) < min_reports:
        error_msg = f"Only {len(uploaded)}/{min_reports} historical reports uploaded for {bridge_name}"
        print_error(error_msg)
        result.errors.append(error_msg)
        return result

    print_success(f"All {len(uploaded)} historical reports uploaded")

    print_step(4, "Creating conversation with prediction prompt...", Colors.CYAN)
    # 根据桥梁名称生成提示词 - 支持动态历史年份和预测年份
    prompt_template = scenario_config.get("prompt", PREDICTION_PROMPT)
    prompt_years_desc = "/".join([f"{y}年" for y in historical_files.keys()]) if historical_files else "2018/2020/2022年"

    # 构建格式化参数字典
    format_args = {
        "bridge_name": bridge_name,
        "next_year": ground_truth_year,
        "historical_years": prompt_years_desc,
        "facility_id": FACILITY_ID_MAP.get(bridge_name, "BR-UNKNOWN"),
    }

    try:
        prompt = prompt_template.format(**format_args)
    except KeyError as e:
        # 如果模板不包含某些占位符，使用默认PREDICTION_PROMPT
        print_warning(f"Prompt format KeyError: {e}, using default PREDICTION_PROMPT")
        prompt = PREDICTION_PROMPT

    # DEBUG: 打印实际发送的提示词（确认步骤3和facility_id）
    print_step(4.5, f"Formatted prompt length: {len(prompt)} chars", Colors.YELLOW)
    if "submit_facility_forecast" in prompt:
        print_success("✅ Prompt contains submit_facility_forecast instruction")
    else:
        print_warning("⚠️ Prompt does NOT contain submit_facility_forecast!")
    if "BR-CJG" in prompt or format_args.get("facility_id") in prompt:
        print_success(f"✅ Prompt contains facility_id: {format_args.get('facility_id')}")
    else:
        print_warning(f"⚠️ Prompt facility_id missing! args: {list(format_args.keys())}")

    conv_result = await api.create_conversation(session_id, prompt)

    if not conv_result.get("success", True):
        print_error(f"Failed to create conversation: {conv_result.get('message')}")
        result.errors.append(f"create_conversation: {conv_result.get('message')}")
        return result

    conversation_id = conv_result.get("data", {}).get("conversation_id")
    result.conversation_id = conversation_id
    print_success(f"Conversation created: {conversation_id}")
    
    print_step(5, "Waiting for conversation to start processing...", Colors.CYAN)
    await wait_for_conversation_state(api, conversation_id, "processing", timeout=15.0)
    
    print_step(6, "Streaming prediction response...", Colors.CYAN)
    prediction_timeout = scenario_config.get("prediction_timeout", 600.0)
    await collect_stream_output(api, conversation_id, result, verbose=verbose, timeout=prediction_timeout, stream_log_file=stream_log_file)
    
    max_followups = 3
    followup_count = 0
    while (result.next_conversation_id or result.detected_mode == "PLAN") and followup_count < max_followups:
        followup_count += 1
        if result.next_conversation_id:
            if verbose:
                print(f"{Colors.YELLOW}[Follow-up #{followup_count}] Auto-approved plan detected, continuing with conversation: {result.next_conversation_id}{Colors.ENDC}")
            next_conv_id = result.next_conversation_id
            result.next_conversation_id = None
            result.detected_mode = None
            conversation_id = next_conv_id
            result.conversation_id = conversation_id
            await wait_for_conversation_state(api, conversation_id, "processing", timeout=10.0)
            await collect_stream_output(api, conversation_id, result, verbose=verbose, timeout=prediction_timeout, stream_log_file=stream_log_file)
        elif result.detected_mode == "PLAN":
            if verbose:
                print(f"{Colors.YELLOW}[Follow-up #{followup_count}] PLAN mode detected, sending approval conversation...{Colors.ENDC}")
            conv_result = await api.create_conversation(session_id, "可以")
            if conv_result.get("success", False):
                next_conv_id = conv_result.get("data", {}).get("conversation_id")
                if next_conv_id:
                    conversation_id = next_conv_id
                    result.conversation_id = conversation_id
                    result.detected_mode = None
                    await wait_for_conversation_state(api, conversation_id, "processing", timeout=10.0)
                    await collect_stream_output(api, conversation_id, result, verbose=verbose, timeout=prediction_timeout, stream_log_file=stream_log_file)
                else:
                    break
            else:
                error = f"Failed to create follow-up: {conv_result.get('message')}"
                print_error(error)
                result.errors.append(error)
                break
    
    if followup_count > 0:
        if verbose:
            print_success(f"Completed {followup_count} follow-up conversation(s)")
    if result.next_conversation_id or result.detected_mode == "PLAN":
        result.errors.append(f"Follow-up limit reached ({max_followups})")
    
    # [方案A] 增强错误恢复: 检查流输出状态并尝试恢复
    print_step(6.5, "Checking stream output status and recovery...", Colors.CYAN)
    
    conv_check = await api.get_conversation(conversation_id)
    current_state = conv_check.get("data", {}).get("state")
    
    if current_state == "running" and not result.response_text:
        if verbose:
            print(f"{Colors.YELLOW}[Recovery] Stream interrupted but conversation still running (state={current_state}){Colors.ENDC}")
            print(f"{Colors.DIM}[Recovery] Event count: {result.event_count}, Tool calls: {result.tool_calls}{Colors.ENDC}")
        
        # 额外等待 AI 完成（最多10分钟）
        extra_wait_timeout = min(600.0, prediction_timeout)
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
    
    print_step(7, "Waiting for conversation to complete...", Colors.CYAN)
    
    # 多轮等待: 处理长时间运行和短响应
    max_completion_wait = 900  # 最多额外等15分钟
    completion_wait_start = time.time()
    max_retries = 5
    retry_count = 0
    min_response_length = 500  # 最小响应长度阈值
    
    while time.time() - completion_wait_start < max_completion_wait:
        final_result = await wait_for_conversation_state(
            api, conversation_id, "completed", timeout=60.0
        )
        if not final_result:
            if verbose:
                print_warning(f"[Step 7] API returned None, retrying...")
            await asyncio.sleep(10)
            continue
        final_state = final_result.get("data", {}).get("state") if isinstance(final_result, dict) else None
        
        if final_state == "completed":
            result.response_text = extract_response_text(final_result)
            
            # 检查响应是否太短（可能是确认消息而非最终输出）
            if result.response_text and len(result.response_text) < min_response_length:
                if verbose:
                    print_warning(f"Response too short ({len(result.response_text)} chars), may be confirmation message")
                    print(f"{Colors.DIM}[Response Preview] {result.response_text[:200]}...{Colors.ENDC}")
                
                # 检查是否有后续 conversation
                if retry_count < max_retries:
                    if verbose:
                        print(f"{Colors.YELLOW}[Extended Wait] Waiting for actual execution...{Colors.ENDC}")
                    await asyncio.sleep(30)
                    
                    # 尝试获取最新的 conversation
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
    
    print_step(8, "Reading ground truth 2024 report for comparison...", Colors.CYAN)
    ground_truth_content = ""
    ground_truth_grade = None
    ground_truth_filename = str(ground_truth_path.name)
    if has_ground_truth:
        try:
            ground_truth_content = await read_ground_truth_report(ground_truth_path)
            print_success(f"Ground truth content length: {len(ground_truth_content)} chars")
        except Exception as e:
            print_error(f"Failed to read ground truth: {e}")
            result.errors.append(f"ground_truth_read: {e}")

    # ============================================================
    # 新增：提取并比对健康评级
    # ============================================================
    print_step(8.1, "Extracting health grades for comparison...", Colors.CYAN)

    # 8.1.1 从 Ground Truth 文件名提取真实评级
    ground_truth_grade = extract_grade_from_filename(ground_truth_filename)
    if not ground_truth_grade and ground_truth_content:
        ground_truth_grade = extract_grade_from_text(ground_truth_content)
    if ground_truth_grade:
        print_success(f"Ground truth grade extracted: {ground_truth_grade}级 ({GRADE_DESCRIPTIONS.get(ground_truth_grade, '')})")
    else:
        print_warning("Could not extract ground truth grade")
    result.ground_truth_grade = ground_truth_grade

    # 8.1.2 从 AI 预测结果中分析预测评级
    predicted_grade_info = analyze_grade_in_prediction(result.response_text)
    predicted_grade = predicted_grade_info.get("grade")
    predicted_bci = predicted_grade_info.get("bci")

    if predicted_grade:
        print_success(f"Predicted grade analyzed: {predicted_grade}级 (BCI: {predicted_bci}, confidence: {predicted_grade_info.get('confidence')})")
    else:
        print_warning("Could not extract predicted grade from AI response")
    result.predicted_grade = predicted_grade
    result.predicted_bci = predicted_bci

    # 8.1.3 执行评级比对
    grade_comparison = None
    grade_score = 0
    if ground_truth_grade and predicted_grade:
        grade_comparison = compare_grades(predicted_grade, ground_truth_grade)
        grade_score = grade_evaluation_score(grade_comparison)
        print(f"\n{Colors.CYAN}{'-'*50}{Colors.ENDC}")
        print(f"{Colors.CYAN}  Health Grade Comparison{Colors.ENDC}")
        print(f"{Colors.CYAN}{'-'*50}{Colors.ENDC}")
        print(f"  Ground Truth Grade: {Colors.YELLOW}{ground_truth_grade}级{Colors.ENDC}")
        print(f"  Predicted Grade:    {Colors.YELLOW}{predicted_grade}级{Colors.ENDC}")
        print(f"  BCI (predicted):     {Colors.YELLOW}{predicted_bci}{Colors.ENDC}")
        print(f"  Match Status:       {grade_comparison.get('match_description', '')}")
        print(f"  Grade Distance:     {grade_comparison.get('grade_distance', 0)} levels")
        print(f"  BCI Range Overlap:   {'Yes' if grade_comparison.get('bci_approx_match') else 'No'}")
        print(f"  {Colors.BOLD}Grade Score:        {grade_score}/100{Colors.ENDC}")
        print(f"{Colors.CYAN}{'-'*50}{Colors.ENDC}\n")
    elif ground_truth_grade:
        print_warning(f"No predicted grade found, but ground truth is {ground_truth_grade}级")
    elif predicted_grade:
        print_warning(f"No ground truth grade found, but predicted grade is {predicted_grade}级")

    # 存储评级比对结果
    result.grade_comparison = grade_comparison
    result.grade_score = grade_score
    
    # [方案B] 工作区轮询: 验证预测报告是否已生成
    print_step(8.5, "Checking workspace for generated prediction report...", Colors.CYAN)
    
    prediction_report_found = False
    prediction_report_info = None
    
    max_workspace_wait = 120  # 最多额外等待2分钟
    workspace_poll_start = time.time()
    
    while time.time() - workspace_poll_start < max_workspace_wait:
        try:
            files_result = await api.list_workspace_files(workspace_id)
            files_data = files_result.get("data") or []
            
            if verbose:
                print(f"{Colors.DIM}[Workspace Check] Total files: {len(files_data)}{Colors.ENDC}")
            
            # 查找预测报告 (包含2024且为.docx格式)
            pred_files = [
                f for f in files_data 
                if ("2024" in f.get("name", "") or "预测" in f.get("name", "") or "预测" in f.get("name", ""))
                and f.get("name", "").endswith(".docx")
            ]
            
            # 也检查任何新生成的 .docx 文件（排除已上传的历史文件）
            original_filenames = {path.name for path in historical_files.values()}
            new_docx_files = [
                f for f in files_data 
                if f.get("name", "").endswith(".docx")
                and f.get("name", "") not in original_filenames
                and not f["name"].startswith("bridge_")  # 排除缓存的转换文件
            ]
            
            if pred_files:
                prediction_report_found = True
                prediction_report_info = pred_files[0]
                if verbose:
                    print_success(f"[Workspace] Found prediction report: {prediction_report_info['name']} ({prediction_report_info.get('size', 0):,} bytes)")
                break
            elif new_docx_files:
                prediction_report_found = True
                prediction_report_info = new_docx_files[0]
                if verbose:
                    print_success(f"[Workspace] Found new docx file: {prediction_report_info['name']} ({prediction_report_info.get('size', 0):,} bytes)")
                break
            else:
                if verbose and int(time.time() - workspace_poll_start) % 30 == 0:
                    elapsed = int(time.time() - workspace_poll_start)
                    print(f"{Colors.DIM}[Workspace] Waiting... ({elapsed}s elapsed){Colors.ENDC}")
                
                await asyncio.sleep(10)
                
        except Exception as e:
            if verbose:
                print_warning(f"[Workspace] Failed to list files: {e}")
            await asyncio.sleep(10)
    
    if not prediction_report_found:
        if verbose:
            print_warning("[Workspace] No prediction report found after extended wait")
        result.errors.append("workspace_check: No prediction report (.docx) found in workspace")
    
    # 记录工作区状态到结果
    result.workspace_files_checked = True
    result.prediction_report_found = prediction_report_found
    if prediction_report_info:
        result.prediction_report_name = prediction_report_info.get("name")
        result.prediction_report_size = prediction_report_info.get("size", 0)
    
    print_step(9, "Generating comparison report...", Colors.CYAN)
    
    with open(output_log, "w", encoding="utf-8") as f:
        f.write("# Bridge Report Prediction Test\n\n")
        f.write(f"- **Timestamp**: {timestamp}\n")
        f.write(f"- **Session ID**: {session_id}\n")
        f.write(f"- **Conversation ID**: {conversation_id}\n")
        f.write(f"- **Workspace ID**: {workspace_id}\n")
        f.write(f"- **Historical Files Used**:\n")
        for year, path in historical_files.items():
            f.write(f"  - {year}: {path.name}\n")

        # DEBUG: 写入完整提示词调试信息
        f.write(f"\n---\n\n## Prompt Debug Info\n\n")
        f.write(f"- **Prompt Length**: {len(prompt)} chars\n")
        f.write(f"- **Has submit_facility_forecast**: {'✅ Yes' if 'submit_facility_forecast' in prompt else '❌ No'}\n")
        f.write(f"- **Facility ID in prompt**: {'✅ ' + format_args.get('facility_id', '') if format_args.get('facility_id') and format_args.get('facility_id') in prompt else '❌ Not found'}\n")
        f.write(f"- **Format Args Keys**: {list(format_args.keys())}\n")
        f.write(f"\n### 完整提示词（{len(prompt)}字符）\n\n```\n{prompt}\n```\n")

        f.write(f"\n---\n\n")
        
        f.write("## AI Predicted 2024 Report\n\n")
        f.write("```\n")
        f.write(result.response_text or "(No response text captured)")
        f.write("\n```\n\n")
        
        f.write("---\n\n")
        
        if ground_truth_content:
            f.write("## Ground Truth 2024 Report\n\n")
            f.write("```\n")
            f.write(ground_truth_content[:10000])
            if len(ground_truth_content) > 10000:
                f.write(f"\n... (truncated, total {len(ground_truth_content)} chars)")
            f.write("\n```\n\n")
            
            f.write("---\n\n")
            f.write("## Comparison Notes\n\n")
            f.write("**Please manually compare the above two reports:**\n")
            f.write("- Technical condition rating consistency\n")
            f.write("- Major defect identification accuracy\n")
            f.write("- Trend analysis reasonableness\n")
            f.write("- Overall format and completeness\n\n")
        else:
            f.write("## Ground Truth 2024 Report\n\n")
            f.write("*Not available for comparison*\n\n")
        
        f.write(f"\n---\n\n")
        f.write("## Test Metadata\n\n")
        f.write(f"- **Tool Calls**: {result.tool_calls}\n")
        f.write(f"- **Event Count**: {result.event_count}\n")
        f.write(f"- **Detected Mode**: {result.detected_mode}\n")
        f.write(f"- **Errors**: {result.errors if result.errors else 'None'}\n")
        
        # [方案B] 添加工作区检查结果到日志
        if result.workspace_files_checked:
            f.write(f"\n---\n\n")
            f.write("## Workspace Check Results\n\n")
            f.write(f"- **Prediction Report Found**: {'[OK] Yes' if result.prediction_report_found else '[X] No'}\n")
            if result.prediction_report_found:
                f.write(f"- **Report Name**: {result.prediction_report_name}\n")
                f.write(f"- **Report Size**: {result.prediction_report_size:,} bytes ({result.prediction_report_size/1024:.1f} KB)\n")

        # ============================================================
        # 新增：健康评级比对结果
        # ============================================================
        f.write(f"\n---\n\n")
        f.write("## Health Grade Comparison (健康评级比对)\n\n")

        f.write(f"| 项目 | 数值 |\n")
        f.write(f"|------|------|\n")
        f.write(f"| 真实评级 (Ground Truth) | {ground_truth_grade or 'N/A'}级 |\n")
        f.write(f"| 预测评级 (Predicted) | {predicted_grade or 'N/A'}级 |\n")
        f.write(f"| 预测 BCI 值 | {predicted_bci or 'N/A'} |\n")
        f.write(f"| 评级是否匹配 | {'[OK] 完全匹配' if grade_comparison and grade_comparison.get('match') else '[X] 不匹配'} |\n")
        f.write(f"| 等级差距 | {grade_comparison.get('grade_distance', 'N/A') if grade_comparison else 'N/A'} 级 |\n")
        f.write(f"| BCI范围重叠 | {'Yes' if grade_comparison and grade_comparison.get('bci_approx_match') else 'No'} |\n")
        f.write(f"| **评级得分** | **{grade_score}/100** |\n")
        f.write(f"\n")

        if grade_comparison:
            f.write(f"### 比对结论\n\n")
            f.write(f"{grade_comparison.get('match_description', '')}\n\n")

        # 评级得分标准说明
        f.write("### 评级得分标准\n\n")
        f.write("- **100分**: 完全匹配（预测评级 = 真实评级）\n")
        f.write("- **70分**: BCI范围重叠且相差1级\n")
        f.write("- **50分**: BCI范围重叠但相差≥2级\n")
        f.write("- **40分**: 相差1级但BCI范围不重叠\n")
        f.write("- **20分**: 相差2级\n")
        f.write("- **0分**: 相差≥3级\n\n")

        # 评级分布图示
        if ground_truth_grade and predicted_grade:
            f.write("### 评级分布图示\n\n")
            grade_order = ["E", "D", "C", "B", "A"]
            for g in grade_order:
                marker = "【真实】" if g == ground_truth_grade else ("【预测】" if g == predicted_grade else "")
                bar_len = 10 if g in (ground_truth_grade, predicted_grade) else 0
                f.write(f"- {g}级: {''.join(['█' for _ in range(bar_len)])} {marker}\n")

        f.write(f"\n---\n\n")
        f.write("## 综合测试结论\n\n")

        # 综合判断
        base_pass = not result.errors
        grade_pass = grade_score >= 70 if grade_comparison else True

        if base_pass and grade_pass:
            f.write("[OK] **测试通过** - 所有检查项均满足要求\n")
        elif base_pass and not grade_pass:
            f.write("[W] **测试基本通过，评级偏差较大** - 功能正常但预测评级与真实评级差异明显\n")
        else:
            f.write("[X] **测试未通过** - 存在错误或预测结果不符合预期\n")

        f.write(f"\n详细评分:\n")
        f.write(f"- 功能检查: {'[OK] 通过' if base_pass else '[X] 未通过'}\n")
        f.write(f"- 评级匹配: {'[OK] 通过' if grade_pass else '[W] 未通过'} (得分: {grade_score}/100)\n")
        f.write(f"- 综合评级: {'[OK] PASS' if (base_pass and grade_pass) else ('[W] MARGINAL' if base_pass else '[X] FAIL')}\n")
    
    print_success(f"Comparison report saved to: {output_log}")
    
    print_step(10, "Validation summary...", Colors.CYAN)
    
    if result.response_text and len(result.response_text) > 100:
        print_success(f"Prediction generated: {len(result.response_text)} chars")
    elif result.response_text:
        print_error(f"Prediction too short: {len(result.response_text)} chars")
        result.errors.append("prediction_too_short")
    else:
        print_error("No prediction response received")
        result.errors.append("no_prediction_response")
    
    # [方案B] 验证工作区检查结果
    if result.workspace_files_checked:
        if result.prediction_report_found:
            print_success(f"Prediction report found in workspace: {result.prediction_report_name} ({result.prediction_report_size/1024:.1f} KB)")
        else:
            print_warning("No prediction report found in workspace (AI may have output text only)")
    
    read_tools = ["read_document", "read_file"]
    used_read_tool = any(t in result.tool_calls for t in read_tools)
    if used_read_tool:
        print_success("Document reading tool was called")
    else:
        print_dim("Document reading tool may not have been called")

    write_tools = ["write_file", "document"]
    used_write_tool = any(t in result.tool_calls for t in write_tools)
    if used_write_tool:
        print_success("File writing tool was called (report may be saved as file)")

    # ============================================================
    # 新增：评级比对最终评估
    # ============================================================
    print(f"\n{Colors.CYAN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.CYAN}  Health Grade Evaluation{Colors.ENDC}")
    print(f"{Colors.CYAN}{'='*60}{Colors.ENDC}")

    if ground_truth_grade and predicted_grade:
        # 最终测试通过/失败判定
        base_pass = not result.errors
        grade_pass = grade_score >= 70

        print(f"  Ground Truth Grade: {Colors.YELLOW}{ground_truth_grade}级{Colors.ENDC}")
        print(f"  Predicted Grade:     {Colors.YELLOW}{predicted_grade}级{Colors.ENDC}")
        print(f"  Grade Score:        {Colors.BOLD}{grade_score}/100{Colors.ENDC}")
        print(f"")
        print(f"  {Colors.BOLD}Test Result:{Colors.ENDC}")
        if base_pass and grade_pass:
            print(f"  {Colors.GREEN}[OK] PASS - All checks passed{Colors.ENDC}")
        elif base_pass and not grade_pass:
            print(f"  {Colors.YELLOW}[W] MARGINAL - Functional OK, grade deviation{Colors.ENDC}")
        else:
            print(f"  {Colors.RED}[X] FAIL - Errors or significant grade deviation{Colors.ENDC}")
    elif ground_truth_grade or predicted_grade:
        print(f"  {Colors.YELLOW}[W] PARTIAL - Could not extract both grades{Colors.ENDC}")
        print(f"  Ground Truth: {ground_truth_grade or 'N/A'}级")
        print(f"  Predicted:    {predicted_grade or 'N/A'}级")
    else:
        print(f"  {Colors.YELLOW}[W] SKIPPED - No grade comparison available{Colors.ENDC}")

    print(f"{Colors.CYAN}{'='*60}{Colors.ENDC}\n")

    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.GREEN}  Bridge Predict Test Completed{Colors.ENDC}")
    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")
    print(f"{Colors.CYAN}Comparison log: {output_log}{Colors.ENDC}\n")

    return result


# ============================================================
# 批量桥梁测试 - 计算整体正确率
# ============================================================

class MultiBridgeTestResult:
    """多桥梁批量测试结果"""
    def __init__(self):
        self.bridge_results: Dict[str, TestResult] = {}
        self.total_bridges = 0
        self.passed_bridges = 0
        self.grade_matched = 0
        self.grade_adjacent = 0
        self.total_grade_score = 0
        self.overall_accuracy_rate = 0.0
        self.overall_grade_score = 0

    def add_result(self, bridge_name: str, result: TestResult):
        self.bridge_results[bridge_name] = result
        self.total_bridges += 1

        # 统计功能通过
        if not result.errors:
            self.passed_bridges += 1

        # 统计评级匹配
        if result.ground_truth_grade and result.predicted_grade:
            if result.ground_truth_grade == result.predicted_grade:
                self.grade_matched += 1
            elif result.grade_comparison and result.grade_comparison.get("grade_distance", 999) == 1:
                self.grade_adjacent += 1

        # 累加评级得分
        if hasattr(result, "grade_score"):
            self.total_grade_score += result.grade_score

    def calculate_overall(self):
        """计算整体统计指标"""
        if self.total_bridges > 0:
            # 整体正确率 = (完全匹配 + 相邻匹配) / 总数
            self.overall_accuracy_rate = (self.grade_matched + self.grade_adjacent) / self.total_bridges * 100
            # 整体评级得分 = 平均分
            self.overall_grade_score = self.total_grade_score / self.total_bridges

    def get_summary(self) -> dict:
        """获取测试汇总"""
        self.calculate_overall()
        return {
            "total_bridges": self.total_bridges,
            "passed_bridges": self.passed_bridges,
            "grade_matched": self.grade_matched,
            "grade_adjacent": self.grade_adjacent,
            "overall_accuracy_rate": self.overall_accuracy_rate,
            "overall_grade_score": self.overall_grade_score,
        }


async def run_all_bridges_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True,
    bridges: List[str] = None,
) -> MultiBridgeTestResult:
    """
    运行所有配置桥梁的批量测试

    Args:
        api: API客户端
        scenario_config: 场景配置
        verbose: 是否显示详细信息
        bridges: 要测试的桥梁名称列表，默认为所有配置的桥梁

    Returns:
        MultiBridgeTestResult: 批量测试结果，包含整体统计
    """
    if bridges is None:
        bridges = list(BRIDGE_CONFIGS.keys())

    print_test_header("Multi-Bridge Batch Test - Overall Accuracy Assessment")
    print(f"{Colors.CYAN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.CYAN}  Testing {len(bridges)} bridges: {', '.join(bridges)}{Colors.ENDC}")
    print(f"{Colors.CYAN}{'='*60}{Colors.ENDC}\n")

    multi_result = MultiBridgeTestResult()

    for idx, bridge_name in enumerate(bridges, 1):
        print(f"\n{Colors.BOLD}{'#'*60}{Colors.ENDC}")
        print(f"{Colors.BOLD}  Bridge #{idx}/{len(bridges)}: {bridge_name}{Colors.ENDC}")
        print(f"{Colors.BOLD}{'#'*60}{Colors.ENDC}\n")

        try:
            result = await run_bridge_predict_test(
                api,
                scenario_config,
                verbose=verbose,
                bridge_name=bridge_name,
            )
            multi_result.add_result(bridge_name, result)
        except Exception as e:
            print_error(f"Test failed for {bridge_name}: {e}")
            # 创建空结果记录失败
            failed_result = TestResult(f"bridge_predict_{bridge_name}", scenario_config)
            failed_result.bridge_name = bridge_name
            failed_result.errors.append(f"test_exception: {e}")
            multi_result.add_result(bridge_name, failed_result)

    # 计算整体统计
    summary = multi_result.get_summary()

    # 打印汇总报告
    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.GREEN}  Multi-Bridge Batch Test Summary{Colors.ENDC}")
    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")

    print(f"{Colors.CYAN}Individual Bridge Results:{Colors.ENDC}")
    for bridge_name, result in multi_result.bridge_results.items():
        status = "[OK]" if not result.errors else "[X]"
        grade_info = ""
        if result.ground_truth_grade and result.predicted_grade:
            grade_info = f" | Grade: {result.predicted_grade}级 vs {result.ground_truth_grade}级 (score: {result.grade_score})"
        elif result.ground_truth_grade:
            grade_info = f" | Grade: N/A vs {result.ground_truth_grade}级"
        print(f"  {status} {bridge_name}{grade_info}")

    print(f"\n{Colors.CYAN}Overall Statistics:{Colors.ENDC}")
    print(f"  Total Bridges:     {summary['total_bridges']}")
    print(f"  Passed (no error): {summary['passed_bridges']}/{summary['total_bridges']}")
    print(f"  Grade Matched:     {summary['grade_matched']}/{summary['total_bridges']}")
    print(f"  Grade Adjacent:    {summary['grade_adjacent']}/{summary['total_bridges']}")
    print(f"  {Colors.BOLD}Overall Accuracy:  {summary['overall_accuracy_rate']:.1f}%{Colors.ENDC}")
    print(f"  {Colors.BOLD}Overall Grade Score: {summary['overall_grade_score']:.1f}/100{Colors.ENDC}")

    print(f"\n{Colors.CYAN}Grade Score Distribution:{Colors.ENDC}")
    score_buckets = {"100": 0, "70-99": 0, "40-69": 0, "0-39": 0}
    for result in multi_result.bridge_results.values():
        score = result.grade_score if hasattr(result, "grade_score") else 0
        if score == 100:
            score_buckets["100"] += 1
        elif score >= 70:
            score_buckets["70-99"] += 1
        elif score >= 40:
            score_buckets["40-69"] += 1
        else:
            score_buckets["0-39"] += 1
    for bucket, count in score_buckets.items():
        bar = "█" * count + "░" * (summary['total_bridges'] - count)
        print(f"  {bucket:>7}: {bar} ({count})")

    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")

    # 保存汇总报告
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    summary_file = log_dir / f"multi_bridge_summary_{timestamp}.md"

    with open(summary_file, "w", encoding="utf-8") as f:
        f.write("# Multi-Bridge Batch Test Summary\n\n")
        f.write(f"- **Timestamp**: {timestamp}\n")
        f.write(f"- **Total Bridges**: {summary['total_bridges']}\n")
        f.write(f"- **Bridges Tested**: {', '.join(bridges)}\n\n")

        f.write("## Individual Results\n\n")
        f.write("| Bridge | Status | Ground Truth | Predicted | Grade Score |\n")
        f.write("|--------|--------|-------------|-----------|-------------|\n")
        for bridge_name, result in multi_result.bridge_results.items():
            status = "[OK] PASS" if not result.errors else "[X] FAIL"
            gt = result.ground_truth_grade or "N/A"
            pred = result.predicted_grade or "N/A"
            score = result.grade_score if hasattr(result, "grade_score") else "N/A"
            f.write(f"| {bridge_name} | {status} | {gt}级 | {pred}级 | {score} |\n")

        f.write("\n## Overall Statistics\n\n")
        f.write("| Metric | Value |\n")
        f.write("|--------|-------|\n")
        f.write(f"| Total Bridges | {summary['total_bridges']} |\n")
        f.write(f"| Passed (no error) | {summary['passed_bridges']} |\n")
        f.write(f"| Grade Matched (exact) | {summary['grade_matched']} |\n")
        f.write(f"| Grade Adjacent (distance=1) | {summary['grade_adjacent']} |\n")
        f.write(f"| **Overall Accuracy Rate** | **{summary['overall_accuracy_rate']:.1f}%** |\n")
        f.write(f"| **Overall Grade Score** | **{summary['overall_grade_score']:.1f}/100** |\n")

        f.write("\n## Grade Score Distribution\n\n")
        for bucket, count in score_buckets.items():
            percentage = count / summary['total_bridges'] * 100 if summary['total_bridges'] > 0 else 0
            f.write(f"- {bucket}: {count} ({percentage:.1f}%)\n")

        f.write("\n## Conclusion\n\n")
        if summary['overall_accuracy_rate'] >= 75 and summary['overall_grade_score'] >= 70:
            f.write("[OK] **Batch Test PASSED** - Overall accuracy rate and grade score meet requirements\n")
        elif summary['overall_accuracy_rate'] >= 50:
            f.write("[W] **Batch Test MARGINAL** - Overall accuracy rate is acceptable but grade score needs improvement\n")
        else:
            f.write("[X] **Batch Test FAILED** - Overall accuracy rate is below acceptable threshold\n")

    print(f"{Colors.CYAN}Summary report saved to: {summary_file}{Colors.ENDC}\n")

    return multi_result
