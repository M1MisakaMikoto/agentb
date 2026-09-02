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


def test_numeric_fields_are_coerced_to_int(monkeypatch):
    captured = {}

    def fake_send(url, method, body, headers=None, timeout=30):
        captured["body"] = body
        return {"success": True, "data": {"id": 123}}

    monkeypatch.setattr(dailypatrol_tool, "_send_http_request", fake_send)
    args = dict(VALID_ARGS)
    args.update({
        "typeid": "2",
        "nameid": "134",
        "xcunitid": "169295800118025732",
        "isdjrw": "0",
        "isyhby": "1",
        "dq": "320100",
        "xcdate": "1746057600000",
        "status": "1",
        "source": "2",
    })

    result = dailypatrol_tool.execute_submit_dailypatrol_record(args)
    body = captured["body"]

    assert result["error"] is None
    assert body["typeid"] == 2
    assert body["nameid"] == 134
    assert body["xcunitid"] == 169295800118025732
    assert body["isdjrw"] == 0
    assert body["isyhby"] == 1
    assert body["dq"] == 320100
    assert body["xcdate"] == 1746057600000
    assert body["status"] == 1
    assert body["source"] == 2


def test_non_numeric_string_passes_through(monkeypatch):
    captured = {}

    def fake_send(url, method, body, headers=None, timeout=30):
        captured["body"] = body
        return {"success": True, "data": {"id": 123}}

    monkeypatch.setattr(dailypatrol_tool, "_send_http_request", fake_send)
    args = dict(VALID_ARGS)
    args["dq"] = "江北区"  # ???

    result = dailypatrol_tool.execute_submit_dailypatrol_record(args)

    assert result["error"] is None
    assert captured["body"]["dq"] == "江北区"


def test_bool_values_coerced_to_int(monkeypatch):
    captured = {}

    def fake_send(url, method, body, headers=None, timeout=30):
        captured["body"] = body
        return {"success": True, "data": {"id": 123}}

    monkeypatch.setattr(dailypatrol_tool, "_send_http_request", fake_send)
    args = dict(VALID_ARGS)
    args["isdjrw"] = True
    args["isyhby"] = False

    result = dailypatrol_tool.execute_submit_dailypatrol_record(args)

    assert result["error"] is None
    assert captured["body"]["isdjrw"] == 1
    assert captured["body"]["isyhby"] == 0


def test_dtolist_numeric_fields_coerced(monkeypatch):
    captured = {}

    def fake_send(url, method, body, headers=None, timeout=30):
        captured["body"] = body
        return {"success": True, "data": {"id": 123}}

    monkeypatch.setattr(dailypatrol_tool, "_send_http_request", fake_send)
    args = dict(VALID_ARGS)
    args["dtoList"] = [
        {"id": "10", "relmainid": "20", "jczbid": "30", "testingitemid": "T1"}
    ]

    result = dailypatrol_tool.execute_submit_dailypatrol_record(args)

    assert result["error"] is None
    dto = captured["body"]["dtoList"][0]
    assert dto["id"] == 10
    assert dto["relmainid"] == 20
    assert dto["jczbid"] == 30
