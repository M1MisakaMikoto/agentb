import importlib.util
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "backend" / "service" / "agent_service" / "tools"


@pytest.fixture(scope="module")
def prediction_tools():
    package = types.ModuleType("prediction_test_package")
    package.__path__ = [str(TOOLS_DIR)]
    sys.modules["prediction_test_package"] = package

    registry_spec = importlib.util.spec_from_file_location(
        "prediction_test_package.registry", TOOLS_DIR / "registry.py"
    )
    registry = importlib.util.module_from_spec(registry_spec)
    sys.modules["prediction_test_package.registry"] = registry
    registry_spec.loader.exec_module(registry)

    module_spec = importlib.util.spec_from_file_location(
        "prediction_test_package.prediction_tools", TOOLS_DIR / "prediction_tools.py"
    )
    module = importlib.util.module_from_spec(module_spec)
    sys.modules["prediction_test_package.prediction_tools"] = module
    module_spec.loader.exec_module(module)
    return module


def test_auto_anchors_latest_value_and_flags_jump(prediction_tools):
    history = [
        {"year": 2018, "bci": 90},
        {"year": 2020, "bci": 89},
        {"year": 2022, "bci": 70},
    ]

    result = prediction_tools.predict_trend(history)

    assert result["success"] is True
    assert result["selected_method"] == "anchored_trend"
    assert result["forecast_meta"]["baseline_year"] == 2022
    assert result["forecast_meta"]["baseline_bci"] == 70.0
    assert result["forecast_meta"]["jump_status"] == "possible_jump"
    assert result["forecast_meta"]["review_required"] is True
    assert result["predictions"][0]["baseline"] == 70.0


def test_auto_backtests_when_history_is_long_enough(prediction_tools):
    history = [
        {"year": 2018, "bci": 90},
        {"year": 2019, "bci": 88},
        {"year": 2020, "bci": 86},
        {"year": 2022, "bci": 84},
    ]

    result = prediction_tools.predict_trend(history)

    assert result["success"] is True
    assert result["forecast_meta"]["backtest_metrics"]
    assert result["forecast_meta"]["selection_reason"].startswith("基于滚动回测")


@pytest.mark.parametrize(
    "method",
    [
        "anchored_trend",
        "linear_regression",
        "polynomial",
        "exponential",
        "conservative",
        "ensemble",
        "degradation_rate",
    ],
)
def test_explicit_models_keep_latest_value_as_baseline(prediction_tools, method):
    history = [
        {"year": 2018, "bci": 90},
        {"year": 2020, "bci": 89},
        {"year": 2022, "bci": 70},
    ]

    result = prediction_tools.predict_trend(history, method=method)

    assert result["success"] is True
    assert result["forecast_meta"]["baseline_bci"] == 70.0
    assert result["predictions"][0]["bci"] >= 0
    assert result["predictions"][0]["bci"] <= 100


def test_invalid_history_is_rejected(prediction_tools):
    assert prediction_tools.predict_trend([{"year": 2020, "bci": 101}])["success"] is False
    assert prediction_tools.predict_trend(
        [{"year": 2020, "bci": 80}, {"year": 2020, "bci": 70}]
    )["success"] is False
    assert prediction_tools.predict_trend(
        [{"year": 2020, "bci": 80}], method="unknown"
    )["success"] is False


def test_maintenance_event_changes_jump_status(prediction_tools):
    history = [
        {"year": 2018, "bci": 80},
        {"year": 2020, "bci": 78},
        {"year": 2022, "bci": 90},
    ]

    result = prediction_tools.predict_trend(
        history,
        maintenance_events=[{"year": 2022, "description": "桥面系维修后复测"}],
    )

    assert result["forecast_meta"]["jump_status"] == "maintenance_change"
    assert result["forecast_meta"]["maintenance_events"]


def test_maintenance_event_parser_returns_source_context(prediction_tools):
    events = prediction_tools._extract_maintenance_events(
        [
            {
                "file": "bridge_2022.docx",
                "content": "2022年完成桥面系维修加固，随后开展复测。",
            }
        ]
    )

    assert events
    assert events[0]["year"] == 2022
    assert events[0]["source_file"] == "bridge_2022.docx"


def test_calculate_bci_rejects_missing_component_scores(prediction_tools):
    report = "2022年检测：桥面系：82，上部结构：80。"

    result = prediction_tools.calculate_bci([report], target_year=2024)

    assert result["success"] is False
    assert "缺少部件评分" in result["error"]
    assert "下部结构" in result["error"]
    assert "支座" in result["error"]
    assert "基础" in result["error"]


def test_numeric_history_without_years_is_rejected(prediction_tools):
    result = prediction_tools.predict_trend([80, 70])

    assert result["success"] is False
    assert "历史数据必须是对象" in result["error"]


def test_reported_bci_mode_uses_explicit_overall_scores(prediction_tools):
    reports = [
        "2018年桥梁整体 BCI=81.36。",
        "2020年桥梁整体 BCI=84.69。",
        "2022年桥梁整体 BCI=89.61。",
    ]

    result = prediction_tools.calculate_bci(reports, target_year=2024)

    assert result["success"] is True
    assert result["calculation_summary"]["method"] == "reported_bci"
    assert result["bci_history"][-1]["bci"] == 89.61


def test_calculate_bci_reads_absolute_text_report_path(prediction_tools, tmp_path):
    report_path = tmp_path / "bridge_2022.txt"
    report_path.write_text("2022年桥梁整体 BCI=89.61。", encoding="utf-8")

    result = prediction_tools.calculate_bci([str(report_path)], target_year=2024)

    assert result["success"] is True
    assert result["calculation_summary"]["method"] == "reported_bci"


def test_component_parser_reads_section_bci_formats(prediction_tools):
    scores = prediction_tools._extract_component_scores([{"content": "桥面系技术状况：BCIm=82.10\\n上部结构技术状况：BCI~k~=81.08\\n下部结构技术状况：BCIx=99.70"}])

    assert scores["桥面系"] == 82.10
    assert scores["上部结构"] == 81.08
    assert scores["下部结构"] == 99.70


def test_bci_parser_prefers_latest_report_value(prediction_tools):
    result = prediction_tools._extract_bci_from_text("上期 BCI=89.80；本期整体 BCI=89.61。", "bridge_2022.docx")

    assert result["bci"] == 89.61
