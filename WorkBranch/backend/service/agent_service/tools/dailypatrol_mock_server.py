"""
日常巡查记录接口模拟测试服务器

用于测试日常巡查记录工具是否能正确发送请求。
启动方式: python dailypatrol_mock_server.py [--port 8002]
默认监听: http://localhost:8002

API 端点:
1. POST /dailypatrol/agent/add - Agent回写巡查记录（需认证）
2. GET  /health - 健康检查

认证:
- Header: agent-secret-key: daily-patrol-agent
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import logging
from datetime import datetime
import argparse

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Agent 认证密钥
AGENT_SECRET_KEY = "daily-patrol-agent"


class DailypatrolMockHandler(BaseHTTPRequestHandler):
    """日常巡查记录接口模拟处理器"""

    # 模拟存储已提交的巡查记录
    _submitted_records = []

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

    def _verify_agent_key(self) -> bool:
        """验证 Agent 认证密钥"""
        secret_key = self.headers.get("agent-secret-key", "")
        return secret_key == AGENT_SECRET_KEY

    def do_POST(self):
        """处理 POST 请求"""
        base_path, query_params = self._parse_query_params()

        if base_path == "/dailypatrol/agent/add":
            self._handle_dailypatrol_add()
        elif base_path == "/v1/dailypatrol/no-auth/add":
            # 兼容旧端点
            self._handle_dailypatrol_add()
        else:
            self.send_error_response(404, f"未找到接口: {base_path}")

    def _handle_dailypatrol_add(self):
        """处理日常巡查记录新增接口"""
        # 验证认证
        if not self._verify_agent_key():
            logger.warning(f"[Dailypatrol API] 认证失败: secret_key={self.headers.get('agent-secret-key', '未提供')}")
            self.send_error_response(401, "Agent回写认证失败")
            return

        content_type = self.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            self.send_error_response(400, "Content-Type 必须为 application/json")
            return

        try:
            data = self._parse_body()
            logger.info(f"[Dailypatrol API] 收到请求: {json.dumps(data, ensure_ascii=False, indent=2)}")
        except json.JSONDecodeError as e:
            self.send_error_response(400, f"JSON 解析失败: {e}")
            return

        # 验证必填字段（对应 Java DTO: DailypatrolNoAuthModel）
        required_fields = [
            "title",           # 巡查标题
            "xcdate",          # 巡查日期（时间戳，毫秒）
            "typeid",          # 设施类型
            "typename",        # 设施类型名称
            "nameid",          # 设施名称id
            "ssname",          # 设施名称
            "xcunitname",      # 巡查单位名称
            "dq",              # 地区
            "isdjrw",          # 是否定检任务
            "isyhby",          # 是否需要养护保养
            "xcperson",        # 巡查人姓名
            "xcphone",         # 巡查人电话
            "xcunitid",        # 巡查单位ID
        ]

        missing_fields = [f for f in required_fields if f not in data or data[f] is None]
        if missing_fields:
            self.send_error_response(400, f"缺少必填字段: {missing_fields}")
            return

        # 验证 dtoList 格式（如果存在）
        dto_list = data.get("dtoList")
        if dto_list is not None:
            if not isinstance(dto_list, list):
                self.send_error_response(400, "dtoList 必须是列表类型")
                return

            for idx, dto in enumerate(dto_list):
                if not isinstance(dto, dict):
                    self.send_error_response(400, f"dtoList[{idx}] 必须是字典类型")
                    return

                # 验证 DailypatrolDetailDTO 关键字段
                detail_required = ["testingitemid", "testingstatus", "opinion"]
                for field in detail_required:
                    if field in dto and dto[field] is not None:
                        pass  # 字段存在且不为空即可

        # 生成模拟响应
        record_id = f"DP_{datetime.now().strftime('%Y%m%d%H%M%S')}_{len(self._submitted_records) + 1}"

        record = {
            "id": record_id,
            "title": data.get("title"),
            "facilityName": data.get("ssname"),
            "facilityType": data.get("typename"),
            "patrolDate": data.get("xcdate"),
            "patrolPerson": data.get("xcperson"),
            "patrolPhone": data.get("xcphone"),
            "orgName": data.get("xcunitname"),
            "orgId": data.get("xcunitid"),
            "isYHBY": data.get("isyhby"),
            "isdjrw": data.get("isdjrw"),
            "detailCount": len(dto_list) if dto_list else 0,
            "status": "submitted",
            "createdAt": datetime.now().isoformat(),
            "message": f"日常巡查记录提交成功，记录ID: {record_id}"
        }

        # 存储记录
        self._submitted_records.append({
            **record,
            "rawData": data
        })

        response = {
            "success": True,
            "data": {"id": record_id},
            "code": 200,
            "message": "操作成功"
        }

        logger.info(f"[Dailypatrol API] 生成响应: record_id={record_id}, title={data.get('title')}, details={len(dto_list) if dto_list else 0}项")
        self.send_json_response(response)

    def do_GET(self):
        """处理 GET 请求（健康检查 + 记录查询）"""
        base_path, _ = self._parse_query_params()

        if base_path == "/health":
            self.send_json_response({
                "status": "healthy",
                "service": "dailypatrol-mock",
                "timestamp": datetime.now().isoformat(),
                "submitted_records_count": len(self._submitted_records)
            })
        elif base_path == "/v1/dailypatrol/records":
            # 查询已提交的记录（用于测试验证）
            self.send_json_response({
                "success": True,
                "data": {
                    "total": len(self._submitted_records),
                    "records": [
                        {
                            "id": r["id"],
                            "title": r["title"],
                            "facilityName": r["facilityName"],
                            "createdAt": r["createdAt"]
                        }
                        for r in self._submitted_records
                    ]
                }
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


def run_mock_server(host: str = "localhost", port: int = 8002):
    """启动模拟服务器"""
    server_address = (host, port)
    httpd = HTTPServer(server_address, DailypatrolMockHandler)

    print(f"""
+============================================================+
|       日常巡查记录接口模拟测试服务器                           |
+============================================================+
|  地址: http://{host}:{port}                             |
|  接口:                                                    |
|    1. POST /dailypatrol/agent/add   (Agent回写，需认证)     |
|    2. GET  /v1/dailypatrol/records  (查询已提交记录)        |
|    3. GET  /health                 (健康检查)              |
+============================================================+
|  认证:                                                    |
|    Header: agent-secret-key: daily-patrol-agent            |
+============================================================+
|  支持字段 (Java DTO 对应):                                 |
|  主表: title, xcdate, typeid, typename, nameid, ssname,   |
|        xcunitname, dq, isdjrw, isyhby, xcperson, xcphone,|
|        xcunitid + 可选字段                                  |
|  明细: dtoList (List<DailypatrolDetailDTO>)               |
+============================================================+
    """)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")
        httpd.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="日常巡查记录接口模拟服务器")
    parser.add_argument("--host", default="localhost", help="监听地址 (默认: localhost)")
    parser.add_argument("--port", type=int, default=8002, help="监听端口 (默认: 8002)")
    args = parser.parse_args()

    run_mock_server(args.host, args.port)
