# AgentB 对外接入包

本目录可直接打包外发，供外部系统（业务平台、其他智能体/编排系统）对接 AgentB 使用。

## 包内清单

| 文件 | 用途 |
| --- | --- |
| `AgentB对外接入文档.md` | 主文档：接入地址、鉴权与实例契约、SSE 流式协议、会话/对话/工作区/计划/知识库接口、错误码、边界约束 |
| `mcp/上游工具MCP服务说明.md` | AgentB 自带上游工具 MCP 服务：部署形态、配置、四个工具、可用性语义与验证命令 |
| `mcp/upstream-tools-config.template.json` | 上游工具 MCP 服务的区域配置模板（部署前必须替换占位符） |
| `examples/quickstart.curl.md` | 生产口径 curl 示例：建会话 → 发对话 → 接流 → 取结果 → 回填问询 → 传文件 |
| `examples/agentb_client.py` | Python 客户端示例（`httpx` 流式解析、心跳、断点续传、`awaiting_user_input` 回填、错误映射） |
| `openapi.json` | 导出时刻的 OpenAPI 快照（字段级权威定义请以运行实例 `/openapi.json` 为准） |

## 五分钟快速开始（生产口径）

前置：拿到的接入地址、调用方用户 ID（`X-User-ID`）、以及是否经网关前缀转发。

```bash
# 1) 准备变量：直连实例不带 /api 前缀；经 Nginx 路由访问时地址带 /api
BASE_URL=http://<agentb-host>:8000          # 直连；经路由时形如 http://<host>:8152/api
USER_ID=<调用方用户ID>

# 2) 健康检查（公开路径，无需鉴权）
curl -fsS "$BASE_URL/health" | head -c 400

# 3) 建会话（写操作需要亲和键；单实例部署也建议固定带上）
SID=$(curl -fsS -X POST "$BASE_URL/session/sessions" \
  -H "X-User-ID: $USER_ID" -H "X-AgentB-Affinity-Key: 1" \
  -H 'Content-Type: application/json' -d '{"title":"对接验证"}' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["data"]["id"])')

# 4) 建对话（带幂等键，重试不会重复建对话）
curl -fsS -X POST "$BASE_URL/session/sessions/$SID/conversations" \
  -H "X-User-ID: $USER_ID" -H "X-AgentB-Affinity-Key: $SID" \
  -H 'Content-Type: application/json' \
  -d '{"user_content":"用一句话说明你是做什么的","idempotency_key":"probe-001"}'

# 5) 接流（SSE；last_seq 支持断点续传，mode=silent 只关心终态时使用）
curl -N "$BASE_URL/session/conversations/<conversation_id>/stream?last_seq=0&mode=interactive" \
  -H "X-User-ID: $USER_ID" -H "X-AgentB-Affinity-Key: $SID"
```

## 对接验收 checklist

1. `GET /health` 返回 `status=ok`；多实例部署时 `/health` 内 `mcp.available=true`。
2. 建会话 / 建对话 / 接流三步返回 200，流内能收到文本事件与 `done` 终态。
3. 缺少 `X-User-ID` 返回 401；缺少亲和键的写操作返回 400；会话不属当前用户返回 403。
4. 断线后用 `last_seq=<已收到的最后 seq>` 重连，能从断点继续收到后续事件，不重复已消费内容。
5. 触发一次 `ask_user_question`（或多轮交互任务），能收到 `user_input_request`，调用 `resume` 后流内出现 `chat_delta` 与 `done`。
6. 上传一个文件到工作区后，`GET /workspaces/{workspace_id}/files` 能看到该文件，下载链接可用。
7. 需要上游工具（报告上传/巡查记录/AI 研判）时，确认 `/health` 的 `mcp.registered` 包含 4 个工具，且一次真实提交返回成功。

更细的字段、事件与错误语义见 `AgentB对外接入文档.md`。
