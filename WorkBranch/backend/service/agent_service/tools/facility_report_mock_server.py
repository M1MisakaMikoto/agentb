"""
设施研判报告接口模拟测试服务器

用于测试设施研判报告工具是否能正确发送请求。
启动方式: python facility_report_mock_server.py
默认监听: http://localhost:8001

API 端点:
1. POST /v1/file - 上传报告文件信息
2. POST /v1/facility/decision/report - 生成研判报告（需要先调用 file 接口）
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class FacilityReportMockHandler(BaseHTTPRequestHandler):
    """设施研判报告接口模拟处理器"""

    # 模拟存储已上传的报告
    _uploaded_reports = {}

    def log_message(self, format, *args):
        """覆盖以使用我们的 logger"""
        logger.info(f"{self.address_string()} - {format % args}")

    def _parse_body(self):
        """解析请求体"""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        return json.loads(body)

    def _parse_query_params(self):
        """解析查询参数"""
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        return parsed.path, parse_qs(parsed.query)

    def do_POST(self):
        """处理 POST 请求"""
        base_path, query_params = self._parse_query_params()

        if base_path == "/v1/file":
            self._handle_file_upload(query_params)
        elif base_path == "/v1/facility/decision/report":
            self._handle_decision_report(query_params)
        else:
            self.send_error_response(404, f"未找到接口: {base_path}")

    def _handle_file_upload(self, query_params):
        """处理文件上传接口"""
        content_type = self.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            self.send_error_response(400, "Content-Type 必须为 application/json")
            return

        try:
            data = self._parse_body()
            logger.info(f"[File API] 收到请求: {data}")
        except json.JSONDecodeError as e:
            self.send_error_response(400, f"JSON 解析失败: {e}")
            return

        # 验证必需字段
        required_fields = ["reportName", "userId", "facilityId", "facilityName", "reportFileUrl"]
        missing_fields = [f for f in required_fields if f not in data]
        if missing_fields:
            self.send_error_response(400, f"缺少必需字段: {missing_fields}")
            return

        # 生成报告ID（模拟）
        report_id = f"report_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        self._uploaded_reports[report_id] = {
            "reportId": report_id,
            "reportName": data.get("reportName"),
            "userId": data.get("userId"),
            "facilityId": data.get("facilityId"),
            "facilityName": data.get("facilityName"),
            "reportFileUrl": data.get("reportFileUrl"),
            "uploadedAt": datetime.now().isoformat(),
            "status": "uploaded"
        }

        response = {
            "success": True,
            "data": {
                "reportId": report_id,
                "message": f"报告文件信息上传成功，ID: {report_id}"
            }
        }

        logger.info(f"[File API] 生成响应: {response}")
        self.send_json_response(response)

    def _handle_decision_report(self, query_params):
        """处理研判报告生成接口"""
        content_type = self.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            self.send_error_response(400, "Content-Type 必须为 application/json")
            return

        try:
            data = self._parse_body()
            logger.info(f"[Decision API] 收到请求: {data}")
        except json.JSONDecodeError as e:
            self.send_error_response(400, f"JSON 解析失败: {e}")
            return

        # 验证必需字段
        required_fields = ["reportId"]
        missing_fields = [f for f in required_fields if f not in data]
        if missing_fields:
            self.send_error_response(400, f"缺少必需字段: {missing_fields}")
            return

        report_id = data.get("reportId")
        report_info = self._uploaded_reports.get(report_id)

        if not report_info:
            self.send_error_response(404, f"未找到报告: {report_id}，请先调用 /v1/file 接口上传报告")
            return

        # 生成研判报告（模拟）
        decision_id = f"decision_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        response = {
            "success": True,
            "data": {
                "decisionId": decision_id,
                "reportId": report_id,
                "facilityId": report_info.get("facilityId"),
                "facilityName": report_info.get("facilityName"),
                "reportName": report_info.get("reportName"),
                "status": "completed",
                "generatedAt": datetime.now().isoformat(),
                "message": f"研判报告生成成功，决策ID: {decision_id}"
            }
        }

        logger.info(f"[Decision API] 生成响应: {response}")
        self.send_json_response(response)

    def do_GET(self):
        """处理 GET 请求（健康检查）"""
        base_path, _ = self._parse_query_params()
        if base_path == "/health":
            self.send_json_response({
                "status": "healthy",
                "timestamp": datetime.now().isoformat(),
                "uploaded_reports_count": len(self._uploaded_reports)
            })
        else:
            self.send_error_response(404, f"未找到: {base_path}")

    def send_json_response(self, data: dict, status: int = 200):
        """发送 JSON 响应"""
        response_body = json.dumps(data, ensure_ascii=False, indent=2)
        response_bytes = response_body.encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(response_bytes))
        self.end_headers()
        self.wfile.write(response_bytes)

    def send_error_response(self, status: int, message: str):
        """发送错误响应"""
        response = {
            "success": False,
            "error": {
                "code": status,
                "message": message,
                "timestamp": datetime.now().isoformat()
            }
        }
        self.send_json_response(response, status)


def run_mock_server(host: str = "localhost", port: int = 8001):
    """启动模拟服务器"""
    server_address = (host, port)
    httpd = HTTPServer(server_address, FacilityReportMockHandler)

    print(f"""
+============================================================+
|           设施研判报告接口模拟测试服务器                       |
+============================================================+
|  地址: http://{host}:{port}                             |
|  接口:                                                    |
|    1. POST /v1/file                   (上传报告文件)        |
|    2. POST /v1/facility/decision/report (生成研判报告)      |
|    3. GET  /health                    (健康检查)           |
+============================================================+
|  测试步骤:                                                |
|  1. 先 POST /v1/file 上传报告，获得 reportId               |
|  2. 再 POST /v1/facility/decision/report 生成研判报告       |
+============================================================+
    """)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")
        httpd.shutdown()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="设施研判报告接口模拟服务器")
    parser.add_argument("--host", default="localhost", help="监听地址 (默认: localhost)")
    parser.add_argument("--port", type=int, default=8001, help="监听端口 (默认: 8001)")
    args = parser.parse_args()

    run_mock_server(args.host, args.port)