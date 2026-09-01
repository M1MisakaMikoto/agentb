import os
import sys


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BACKEND_DIR)

from service.agent_service.tools import dailypatrol_tool


VALID_ARGS = {
    "title": "测试道路病害巡查",
    "xcdate": 1746057600000,
    "typeid": 2,
    "typename": "道路",
    "nameid": 134,
    "ssname": "测试道路",
    "xcunitname": "测试单位",
    "dq": "江北区",
    "isdjrw": 0,
    "isyhby": 1,
    "xcperson": "测试人",
    "xcphone": "13800138000",
    "xcunitid": "123",
}


def test_non_dict_response_returns_clear_error(monkeypatch):
    monkeypatch.setattr(
        dailypatrol_tool,
        "_send_http_request",
        lambda *_args, **_kwargs: 'some string response',
    )

    result = dailypatrol_tool.execute_submit_dailypatrol_record(VALID_ARGS)

    assert result["result"] is None
    assert "接口返回格式异常" in result["error"]
    assert "some string response" in result["error"]
    assert "str" in result["error"]


def test_dict_success_response_keeps_working(monkeypatch):
    monkeypatch.setattr(
        dailypatrol_tool,
        "_send_http_request",
        lambda *_args, **_kwargs: {"success": True, "data": {"id": 123}},
    )

    result = dailypatrol_tool.execute_submit_dailypatrol_record(VALID_ARGS)

    assert result["error"] is None
    assert "123" in result["result"]


def test_dict_error_response_keeps_working(monkeypatch):
    monkeypatch.setattr(
        dailypatrol_tool,
        "_send_http_request",
        lambda *_args, **_kwargs: {"success": False, "error": {"message": "业务校验失败"}},
    )

    result = dailypatrol_tool.execute_submit_dailypatrol_record(VALID_ARGS)

    assert result["result"] is None
    assert "业务校验失败" in result["error"]
