"""
设施研判报告接口模拟测试服务器

用于测试设施研判报告工具是否能正确发送请求。
启动方式: python facility_report_mock_server.py
默认监听: http://localhost:8001

API 端点:
1. POST /v1/file - 上传报告文件信息（Header: X-Region-Id）【决策报告用】
2. POST /v1/facility/decision/report - 生成研判报告（需要先调用 file 接口）
3. POST /v1/file/upload - 上传 PDF 文件（MultipartFile）【预测报告用】
4. POST /v1/facility/forecast/report - 提交预测报告（需要先调用 upload 接口）
5. GET  /health - 健康检查
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

        if base_path == "/v1/file/upload":
            self._handle_file_multipart_upload()
        elif base_path == "/v1/facility/decision/report":
            self._handle_decision_report()
        elif base_path == "/v1/facility/forecast/report":
            self._handle_forecast_report()
        else:
            self.send_error_response(404, f"未找到接口: {base_path}")

    def _handle_decision_report(self):
        """处理研判报告生成接口（接收 reportFileUrl + 业务字段，含 regionId）"""
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
        required_fields = ["regionId", "reportName", "facilityId", "facilityName", "reportFileUrl"]
        missing_fields = [f for f in required_fields if f not in data]
        if missing_fields:
            self.send_error_response(400, f"缺少必需字段: {missing_fields}")
            return

        # 验证 reportFileUrl 是否来自已上传的文件
        file_url = data.get("reportFileUrl")
        uploaded_file = None
        for info in self._uploaded_reports.values():
            if info.get("fileUrl") == file_url:
                uploaded_file = info
                break

        if not uploaded_file:
            self.send_error_response(404, f"未找到上传记录: {file_url}，请先调用 /v1/file/upload 接口上传文件")
            return

        # 生成研判报告（模拟）
        decision_id = f"decision_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        response = {
            "success": True,
            "data": {
                "decisionId": decision_id,
                "fileUrl": file_url,
                "facilityId": data.get("facilityId"),
                "facilityName": data.get("facilityName"),
                "reportName": data.get("reportName"),
                "status": "completed",
                "generatedAt": datetime.now().isoformat(),
                "message": f"研判报告生成成功，决策ID: {decision_id}"
            }
        }

        logger.info(f"[Decision API] 生成响应: {response}")
        self.send_json_response(response)

    def _handle_file_multipart_upload(self):
        """处理 PDF 文件上传接口（MultipartFile）- 预测报告用"""
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self.send_error_response(400, "Content-Type 必须为 multipart/form-data")
            return

        # 解析 multipart 数据
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        # 提取 boundary
        boundary = content_type.split("boundary=")[-1].strip() if "boundary=" in content_type else None
        if not boundary:
            self.send_error_response(400, "无法解析 boundary")
            return

        # 简单解析：提取文件数据（模拟，不严格按 RFC 解析）
        try:
            parts = body.split(f"--{boundary}".encode())
            file_data = None
            filename = None

            for part in parts:
                if b"filename=" in part:
                    # 提取 filename
                    header_end = part.find(b"\r\n\r\n")
                    header = part[:header_end].decode("utf-8", errors="ignore")
                    for line in header.split("\r\n"):
                        if "filename=" in line:
                            filename = line.split("filename=")[-1].strip('"')
                            break
                    # 提取文件内容
                    file_data = part[header_end + 4:].rstrip(b"\r\n--")
                    break

            if not file_data or not filename:
                self.send_error_response(400, "未找到文件数据或文件名")
                return

            logger.info(f"[Upload API] 收到文件: {filename}, 大小: {len(file_data)} bytes")

            # 模拟存储并返回 fileUrl
            file_id = f"file_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            file_url = f"/files/forecast/{file_id}_{filename}"

            self._uploaded_reports[file_id] = {
                "fileId": file_id,
                "fileName": filename,
                "fileUrl": file_url,
                "size": len(file_data),
                "uploadedAt": datetime.now().isoformat(),
            }

            response = {
                "success": True,
                "data": {
                    "fileUrl": file_url,
                    "fileName": filename,
                    "fileId": file_id,
                    "message": f"PDF 文件上传成功"
                }
            }

            logger.info(f"[Upload API] 生成响应: {response}")
            self.send_json_response(response)

        except Exception as e:
            logger.error(f"[Upload API] 解析异常: {e}")
            self.send_error_response(500, f"文件上传处理失败: {str(e)}")

    def _handle_forecast_report(self):
        """处理预测报告提交接口"""
        content_type = self.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            self.send_error_response(400, "Content-Type 必须为 application/json")
            return

        try:
            data = self._parse_body()
            logger.info(f"[Forecast API] 收到请求: {data}")
        except json.JSONDecodeError as e:
            self.send_error_response(400, f"JSON 解析失败: {e}")
            return

        # 验证必需字段
        required_fields = ["regionId", "facilityId", "predictYear", "reportUrl"]
        missing_fields = [f for f in required_fields if f not in data]
        if missing_fields:
            self.send_error_response(400, f"缺少必需字段: {missing_fields}")
            return

        # 验证 reportUrl 是否来自已上传的文件
        report_url = data.get("reportUrl")
        uploaded_file = None
        for info in self._uploaded_reports.values():
            if info.get("fileUrl") == report_url:
                uploaded_file = info
                break

        if not uploaded_file:
            self.send_error_response(404, f"未找到上传记录: {report_url}，请先调用 /v1/file/upload 接口上传文件")
            return

        # 生成预测报告（模拟）
        forecast_id = f"forecast_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        response = {
            "success": True,
            "data": forecast_id,
            "forecastId": forecast_id,
            "regionId": data.get("regionId"),
            "facilityId": data.get("facilityId"),
            "facilityName": data.get("facilityName"),
            "predictYear": data.get("predictYear"),
            "reportUrl": report_url,
            "predictedHealthScore": data.get("predictedHealthScore"),
            "predictedRiskLevel": data.get("predictedRiskLevel"),
            "summary": data.get("summary"),
            "status": "completed",
            "generatedAt": datetime.now().isoformat(),
            "message": f"预测报告提交成功，ID: {forecast_id}"
        }

        logger.info(f"[Forecast API] 生成响应: {response}")
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
|           设施报告接口模拟测试服务器                           |
+============================================================+
|  地址: http://{host}:{port}                             |
|  接口:                                                    |
|    1. POST /v1/file/upload           (上传PDF文件)         |
|    2. POST /v1/facility/decision/report (生成研判报告)      |
|    3. POST /v1/facility/forecast/report (提交预测报告)      |
|    4. GET  /health                    (健康检查)           |
+============================================================+
|  测试流程:                                                |
|  决策/预测报告均为两步: POST /v1/file/upload → 业务接口     |
|  预测报告请求体需包含: regionId, facilityId, predictYear,  |
|                       reportUrl, facilityName(可选)等       |
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