# AgentB 前端 Affinity 接入改造方案

> 状态：待前端重新启用时实施
> 记录日期：2026-08-03
> 适用后端：多实例 affinity Compose 架构

## 1. 目标与边界

前端不负责选择具体 Worker，也不管理 Redis owner lease、实例心跳或 draining。
前端只负责把一次业务操作所属的 Session 上下文稳定地传给后端路由，并在网络重试时保持幂等和 SSE 续传信息。

旧前端当前已停用，因此本方案只记录未来接入契约，不在本次后端改造中修改前端代码。

## 2. 字段定义

| 客户端字段 | 线上协议名称 | 如何获取 | 何时生成或更新 | 传入位置 |
| --- | --- | --- | --- | --- |
| `affinityKey` | `X-AgentB-Affinity-Key` / `affinity_key` | 正常情况下取当前 `sessionId` | Session 创建成功后更新为响应中的 `data.id` | 普通 HTTP 使用请求头；原生 EventSource 使用查询参数 |
| `provisionalAffinityKey` | `X-AgentB-Affinity-Key` | 客户端调用 `crypto.randomUUID()` | 每次发起“创建 Session”操作时生成一次；同一次网络重试必须复用 | `POST /api/session/sessions` 请求头 |
| `sessionId` | 路径参数或响应字段 `id` / `session_id` | 创建 Session 响应，或 Session/Conversation 数据 | 创建或加载业务对象时写入客户端状态 | 用作正式 `affinityKey`，也用于 Session 路径参数 |
| `idempotencyKey` | JSON 字段 `idempotency_key` | 客户端调用 `crypto.randomUUID()` | 每次“创建 Conversation”用户操作生成一次；同一次重试必须复用 | 创建 Conversation 的 JSON 请求体 |
| `lastEventId` | SSE `id` / `Last-Event-ID` | 从每条 SSE 事件的 `event.lastEventId` 读取 | 每收到一条带 `id` 的事件后更新 | 原生 EventSource 自动重连时由浏览器发送 |
| `lastSeq` | 查询参数 `last_seq` | 将最后一个 SSE `id` 解析为整数 | 手动关闭并重建流之前保留 | 手动重连 SSE 时作为查询参数 |
| `instanceId` | 响应头 `X-AgentB-Instance-ID` | 后端响应 | 每次响应可记录 | 仅日志、诊断和 E2E 使用，不参与业务路由 |
| `ownerInstanceId` | 响应头 `X-AgentB-Owner-ID` | owner 冲突的 `409` 响应 | 仅错误发生时读取 | 仅日志和故障诊断，不作为下一次 affinity key |

`affinityKey` 不是 JSON 业务字段，也不是权限凭证。后端仍会根据当前登录用户和数据库记录验证 Session 所有权。

## 3. Affinity 生命周期

### 3.1 创建 Session

1. 用户触发创建 Session。
2. 前端生成一个 `provisionalAffinityKey = crypto.randomUUID()`。
3. 调用 `POST /api/session/sessions`，请求头携带该临时 key。
4. 若发生网络级重试，复用原来的临时 key。
5. 请求成功后，从 `response.data.id` 取得 `sessionId`。
6. 从此以后，该 Session 的正式 `affinityKey` 固定为 `String(sessionId)`，不再使用临时 key。

临时 key 只用于 Session 尚不存在时让 router 完成首次分发，不应保存为 Session 的长期路由键。
它不等于 Session 创建幂等键。当前创建 Session 接口没有幂等字段；若请求可能已经到达后端但响应丢失，客户端不能仅凭相同临时 key 判断是否应再次创建，应先刷新 Session 列表或要求用户确认，避免产生重复 Session。

### 3.2 加载已有 Session

从 Session 列表、Session 详情或 Conversation 数据中取得 `sessionId`。所有从该 Session 发起的后续操作都使用 `String(sessionId)` 作为 affinity key。

前端状态必须保留 `conversationId -> sessionId` 关系。只有 `conversationId` 的取消、删除、发消息和订阅接口，不能临时猜测或使用“当前选中的 Session”。

### 3.3 Session 结束

Session 删除成功后，可以清理该 Session 的本地 affinity 上下文、Conversation 映射和 SSE 游标。切换页面或关闭 SSE 不会改变 affinity key。

## 4. API 传入规则

| 操作 | 方法与路径 | Affinity 要求 | 其他字段 |
| --- | --- | --- | --- |
| 获取 Session 列表 | `GET /api/session/sessions` | 不传，没有单一 Session 上下文 | 无 |
| 创建 Session | `POST /api/session/sessions` | 请求头必传 `provisionalAffinityKey` | 请求体可包含 `title` |
| 获取 Session 详情 | `GET /api/session/sessions/{sessionId}` | 后端当前不强制；建议请求头传 `sessionId` | 无 |
| 获取 Session 的 Conversation | `GET /api/session/sessions/{sessionId}/conversations` | 后端当前不强制；建议请求头传 `sessionId` | 无 |
| 生成 Session 标题 | `POST /api/session/sessions/{sessionId}/title:generate` | 请求头必传 `sessionId` | 无 |
| 删除 Session | `DELETE /api/session/sessions/{sessionId}` | 请求头必传 `sessionId` | 无 |
| 创建 Conversation | `POST /api/session/sessions/{sessionId}/conversations` | 请求头必传 `sessionId` | 请求体传 `idempotency_key` |
| 获取 Conversation | `GET /api/session/conversations/{conversationId}` | 后端当前不强制；建议请求头传对应 `sessionId` | 无 |
| 删除 Conversation | `DELETE /api/session/conversations/{conversationId}` | 请求头必传对应 `sessionId` | 无 |
| 级联删除 Conversation | `DELETE /api/session/conversations/{conversationId}/cascade` | 请求头必传对应 `sessionId` | 无 |
| 取消 Conversation | `POST /api/session/conversations/{conversationId}/cancel` | 请求头必传对应 `sessionId` | 无 |
| 准备 Conversation 消息 | `POST /api/session/conversations/{conversationId}/messages` | 请求头必传对应 `sessionId` | 原有消息请求体 |
| 订阅 Conversation SSE | `GET /api/session/conversations/{conversationId}/stream` | 查询参数必传 `affinity_key=sessionId` | 重连时传 `last_seq` 或由浏览器发送 `Last-Event-ID` |

通用规则：`/session/sessions/{...}` 下的 `POST/PUT/PATCH/DELETE`、`/session/conversations/{...}` 下的 `POST/PUT/PATCH/DELETE` 以及 Conversation stream 都必须携带 affinity。

Workspace 文件接口当前不在 affinity 强制范围内。多实例 Compose 中三个 API 实例共享 `/app/workspaces`，不要把 `workspaceId` 当作 affinity key。

## 5. 前端代码结构建议

### 5.1 请求上下文

定义显式的 Session 请求上下文：

```ts
export interface SessionRequestContext {
  sessionId: string | number
}

export interface RequestOptions {
  affinityKey?: string | number
}
```

HTTP 封装仅在 `affinityKey` 存在时写入：

```ts
headers['X-AgentB-Affinity-Key'] = String(options.affinityKey)
```

不要从全局“当前 Session”自动注入。用户可能同时打开多个 Session、后台 SSE 或并行请求，全局值会把请求路由到错误 Session。

### 5.2 API 函数签名

Conversation 路径中没有 `sessionId` 的函数必须显式增加参数，例如：

```ts
cancelConversation(conversationId, sessionId)
deleteConversation(conversationId, sessionId)
cascadeDeleteConversation(conversationId, sessionId)
fetchConversationDetail(conversationId, sessionId)
fetchConversationMessages(conversationId, sessionId)
subscribeConversation(conversationId, sessionId, lastSeq?)
```

Session 路径已经包含 `sessionId` 的函数可以直接用路径参数构造 affinity header。

## 6. Conversation 幂等

创建 Conversation 时生成：

```ts
const idempotencyKey = crypto.randomUUID()
```

请求体示例：

```json
{
  "user_content": "用户输入",
  "idempotency_key": "70c470b6-8eab-4ac8-83b0-73ee866ed665"
}
```

同一次点击产生的超时重试、断网重试或页面恢复重试必须复用同一个 `idempotencyKey`。用户明确发起下一次新 Conversation 时才生成新值。

## 7. SSE 与断线续传

原生 EventSource 不能设置自定义请求头，因此首次连接使用：

```text
/api/session/conversations/{conversationId}/stream?affinity_key={sessionId}
```

后端 SSE 事件包含：

```text
id: <整数序号>
data: <JSON>
```

前端应保留 `event.lastEventId`。同一个 EventSource 的浏览器自动重连会发送 `Last-Event-ID`；如果应用主动销毁并重建 EventSource，则使用：

```text
/api/session/conversations/{conversationId}/stream?affinity_key={sessionId}&last_seq={lastEventId}
```

只有收到带 `id` 的事件才更新游标。`done`、`error` 或 `cancelled` 终态后关闭连接并清理游标。

## 8. 错误处理

| HTTP 状态 | 含义 | 前端行为 |
| --- | --- | --- |
| `400` | affinity 缺失、非法，或 SSE 游标非法 | 记录为客户端协议错误；不要无条件重试 |
| `401` / `403` | 未登录或资源不属于当前用户 | 按现有认证流程处理 |
| `404` | Session 或 Conversation 不存在 | 刷新本地列表并清理失效映射 |
| `409` | affinity 与资源 Session 不一致、owner 冲突，或 Session 已有 running Conversation | 保留原 `sessionId`；不要更换 affinity key；读取错误正文并记录 `X-AgentB-Owner-ID` |
| `503` + `Retry-After` | 命中的实例正在 draining，不能创建新 Session | 使用同一个临时 affinity key，按 `Retry-After` 延迟重试 |

`X-AgentB-Instance-ID` 和 `X-AgentB-Owner-ID` 只用于诊断。前端不能把它们回传为 affinity key，也不能绕过 router 直连某个 Worker。

若前后端跨域，网关和 CORS 必须允许请求头 `X-AgentB-Affinity-Key`，并在需要前端诊断时暴露响应头 `X-AgentB-Instance-ID`、`X-AgentB-Owner-ID` 和 `Retry-After`。

## 9. E2E 验收

前端重新启用前至少验证：

1. 创建 Session 使用临时 UUID，成功后所有相关请求切换为返回的 Session ID。
2. 同一 Session 的创建 Conversation、消息、SSE、取消和删除响应具有稳定的 `X-AgentB-Instance-ID`。
3. 不同 Session 能分布到至少两个 API 实例。
4. 同一个 `idempotency_key` 重试不会创建第二个 Conversation。
5. SSE 断线后从最后 `id` 恢复，不重复展示已处理事件。
6. 缺少 affinity 返回 `400`，错误 Session affinity 返回 `409`。
7. Conversation API 即使只给出 `conversationId`，也能从客户端状态取得正确 `sessionId`。
8. draining 返回 `503` 时保持同一个临时 key，并遵守 `Retry-After`。

后端黑盒基准脚本为 `deploy/e2e/affinity_smoke.py`。前端 E2E 应复用同一协议断言，而不是通过页面元素间接猜测实例路由结果。

## 10. 推荐实施顺序

1. 扩展 HTTP 请求封装，支持显式 `affinityKey`。
2. 建立 `conversationId -> sessionId` 状态映射。
3. 改造 Session 和 Conversation API 函数签名及调用点。
4. 改造 EventSource URL 和 SSE 游标保存。
5. 增加 Conversation `idempotency_key` 生命周期管理。
6. 增加错误分类、诊断日志和 CORS 配置。
7. 编写并运行前端协议 E2E。
