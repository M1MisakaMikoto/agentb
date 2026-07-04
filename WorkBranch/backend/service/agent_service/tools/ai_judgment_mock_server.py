"""
AI 研判接口模拟测试服务器

用于测试 AI 研判工具是否能正确发送请求到接口。
启动方式: python ai_judgment_mock_server.py
默认监听: http://localhost:8080
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


class AIJudgmentMockHandler(BaseHTTPRequestHandler):
    """AI 研判接口模拟处理器"""

    def log_message(self, format, *args):
        """覆盖以使用我们的 logger"""
        logger.info(f"{self.address_string()} - {format % args}")

    def do_POST(self):
        """处理 POST 请求"""
        from urllib.parse import urlparse
        parsed = urlparse(self.path)
        base_path = parsed.path

        if base_path != "/v1/ai-judgment/issues":
            self.send_error_response(404, f"未找到接口: {base_path}")
            return

        # 检查 Content-Type
        content_type = self.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            self.send_error_response(400, "Content-Type 必须为 application/json")
            return

        # 解析请求体
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            request_data = json.loads(body)
            logger.info(f"收到请求体: {request_data}")
        except json.JSONDecodeError as e:
            self.send_error_response(400, f"JSON 解析失败: {e}")
            return

        # 获取 regionId (从请求体)
        region_id = request_data.get("regionId")

        if not region_id:
            self.send_error_response(400, "缺少 regionId 参数")
            return

        # 模拟扁平型错误结构（用于测试 ai_judgment_tool 的错误透传逻辑）
        # 当 regionId == "404" 时返回扁平型 404，不带 error 包装字段
        if str(region_id) == "404":
            self.send_flat_error_response(404, "用户不存在")
            return

        # 验证必需字段
        required_fields = ["regionId", "facilityId", "facilityName", "title"]
        missing_fields = [f for f in required_fields if f not in request_data]
        if missing_fields:
            self.send_error_response(400, f"缺少必需字段: {missing_fields}")
            return

        # 使用 regionId 生成 area_id
        area_id = f"area_{region_id}"

        # 生成模拟响应
        issue_id = f"issue_{datetime.now().strftime('%Y%m%d%H%M%S')}_{hash(region_id) % 10000}"
        response = {
            "success": True,
            "data": {
                "issueId": issue_id,
                "regionId": region_id,
                "areaId": area_id,
                "facilityId": request_data.get("facilityId"),
                "facilityName": request_data.get("facilityName"),
                "title": request_data.get("title"),
                "description": request_data.get("description", ""),
                "status": "pending",
                "createdAt": datetime.now().isoformat(),
                "message": f"AI 研判工单已创建成功，ID: {issue_id}，区域: {area_id}"
            }
        }

        logger.info(f"生成响应: {response}")
        self.send_json_response(response)

    def do_GET(self):
        """处理 GET 请求（健康检查）"""
        from urllib.parse import urlparse
        parsed = urlparse(self.path)
        base_path = parsed.path
        if base_path == "/health":
            self.send_json_response({"status": "healthy", "timestamp": datetime.now().isoformat()})
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

    def send_flat_error_response(self, status: int, message: str):
        """发送扁平型错误响应（不带 error 包装字段）

        模拟真实后端返回的扁平结构：
        {"code": 404, "message": "用户不存在"}
        用于测试 ai_judgment_tool._send_http_request 的 HTTPError 分支
        能否正确透传扁平错误结构中的 message 字段。
        """
        response = {
            "code": status,
            "message": message,
        }
        self.send_json_response(response, status)


def run_mock_server(host: str = "localhost", port: int = 8080):
    """启动模拟服务器"""
    server_address = (host, port)
    httpd = HTTPServer(server_address, AIJudgmentMockHandler)

    print(f"""
╔══════════════════════════════════════════════════════════╗
║           AI 研判接口模拟测试服务器                         ║
╠══════════════════════════════════════════════════════════╣
║  地址: http://{host}:{port}                              ║
║  接口: POST /v1/ai-judgment/issues                       ║
║  健康检查: GET /health                                    ║
╠══════════════════════════════════════════════════════════╣
║  测试示例:                                                ║
║  curl -X POST http://{host}:{port}/v1/ai-judgment/issues  ║
║    -H "Content-Type: application/json"                  ║
║    -d '{{                                                   ║
║         "regionId": "130117562645086249",                 ║
║         "facilityId": 1001,                               ║
║         "facilityName": "测试桥梁",                         ║
║         "title": "桥面出现裂缝",                            ║
║         "description": "裂缝约2米长"                         ║
║       }}'                                                  ║
╚══════════════════════════════════════════════════════════╝
    """)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")
        httpd.shutdown()


if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="AI 研判接口模拟服务器")
    parser.add_argument("--host", default="localhost", help="监听地址 (默认: localhost)")
    parser.add_argument("--port", type=int, default=8080, help="监听端口 (默认: 8080)")
    args = parser.parse_args()

    run_mock_server(args.host, args.port)