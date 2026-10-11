# 生产口径 curl 示例

变量约定（直连实例不带 `/api`；经 Nginx 路由时地址形如 `http://<host>:8152/api`）：

```bash
export BASE_URL=http://<agentb-host>:8000
export USER_ID=<调用方用户ID>
```

统一说明：所有 `-f` 表示非 2xx 直接失败；写操作与接流都带 `X-AgentB-Affinity-Key`（键值=会话 ID）；`--connect-timeout` 用于快速失败，读超时按任务规模放宽。

## 1. 健康检查（公开路径）

```bash
curl -fsS --connect-timeout 5 "$BASE_URL/health" | python -m json.tool | head -n 30
curl -fsS --connect-timeout 5 "$BASE_URL/health/ready"
```

## 2. 建会话

```bash
SESSION=$(curl -fsS -X POST "$BASE_URL/session/sessions" \
  -H "X-User-ID: $USER_ID" \
  -H "X-AgentB-Affinity-Key: 1" \
  -H 'Content-Type: application/json' \
  -d '{"title":"对接验证"}')
SESSION_ID=$(printf '%s' "$SESSION" | python -c 'import sys,json;print(json.load(sys.stdin)["data"]["id"])')
WORKSPACE_ID=$(printf '%s' "$SESSION" | python -c 'import sys,json;print(json.load(sys.stdin)["data"]["workspace_id"])')
echo "session_id=$SESSION_ID workspace_id=$WORKSPACE_ID"
```

## 3. 上传文件（可选，需走工作区）

```bash
curl -fsS -X POST "$BASE_URL/workspaces/$WORKSPACE_ID/files" \
  -H "X-User-ID: $USER_ID" \
  -F 'files=@./检测报告.docx' \
  -F 'sub_dir=inputs'

curl -fsS "$BASE_URL/workspaces/$WORKSPACE_ID/files" -H "X-User-ID: $USER_ID"
```

## 4. 建对话（带幂等键，可安全重试）

```bash
CONV=$(curl -fsS -X POST "$BASE_URL/session/sessions/$SESSION_ID/conversations" \
  -H "X-User-ID: $USER_ID" \
  -H "X-AgentB-Affinity-Key: $SESSION_ID" \
  -H 'Content-Type: application/json' \
  -d '{"user_content":"读取 inputs/检测报告.docx，输出整体状况结论","idempotency_key":"probe-001"}')
CONVERSATION_ID=$(printf '%s' "$CONV" | python -c 'import sys,json;print(json.load(sys.stdin)["data"]["conversation_id"])')
echo "conversation_id=$CONVERSATION_ID"
```

## 5. 接流（SSE）

```bash
# interactive：全量事件；按行观察事件类型
curl -N --connect-timeout 10 \
  "$BASE_URL/session/conversations/$CONVERSATION_ID/stream?last_seq=0&mode=interactive" \
  -H "X-User-ID: $USER_ID" \
  -H "X-AgentB-Affinity-Key: $SESSION_ID"

# silent：只关心终态时使用（心跳 + done/error/cancelled + 会话交接）
curl -N --connect-timeout 10 \
  "$BASE_URL/session/conversations/$CONVERSATION_ID/stream?last_seq=0&mode=silent" \
  -H "X-User-ID: $USER_ID" \
  -H "X-AgentB-Affinity-Key: $SESSION_ID"
```

断线续传：把已收到的最后一个 `id:` 值作为 `last_seq` 重新连接，也可改用请求头 `Last-Event-ID`：

```bash
curl -N "$BASE_URL/session/conversations/$CONVERSATION_ID/stream?last_seq=42" \
  -H "X-User-ID: $USER_ID" -H "X-AgentB-Affinity-Key: $SESSION_ID"
```

## 6. 反问回填（收到 `user_input_request` 时）

```bash
# content 是 JSON 字符串：{"type":"ask_user_question","call_seq":1,"question":"...","options":[...]}
curl -fsS -X POST "$BASE_URL/session/conversations/$CONVERSATION_ID/resume" \
  -H "X-User-ID: $USER_ID" \
  -H "X-AgentB-Affinity-Key: $SESSION_ID" \
  -H 'Content-Type: application/json' \
  -d '{"answer":"按 2024 年数据","call_seq":1}'
```

## 7. 取最终结果 / 取消 / 删除

```bash
curl -fsS "$BASE_URL/session/conversations/$CONVERSATION_ID" -H "X-User-ID: $USER_ID" | python -m json.tool

# 取消正在执行的任务
curl -fsS -X POST "$BASE_URL/session/conversations/$CONVERSATION_ID/cancel" \
  -H "X-User-ID: $USER_ID" -H "X-AgentB-Affinity-Key: $SESSION_ID"

# 删除该对话（含之后全部对话用 /cascade）
curl -fsS -X DELETE "$BASE_URL/session/conversations/$CONVERSATION_ID" \
  -H "X-User-ID: $USER_ID" -H "X-AgentB-Affinity-Key: $SESSION_ID"
```

## 8. 常见错误对照

```bash
# 401：缺 X-User-ID
curl -s -o - -w '\nHTTP %{http_code}\n' "$BASE_URL/session/sessions"

# 400：缺亲和键
curl -s -o - -w '\nHTTP %{http_code}\n' -X POST "$BASE_URL/session/sessions" \
  -H "X-User-ID: $USER_ID" -H 'Content-Type: application/json' -d '{"title":"t"}'

# 404：会话不存在（错误体为 {"detail":"会话不存在"}）
curl -s -o - -w '\nHTTP %{http_code}\n' "$BASE_URL/session/sessions/999999" -H "X-User-ID: $USER_ID"
```
