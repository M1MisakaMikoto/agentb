# 上游工具 MCP 服务（随 AgentB 部署自带）

AgentB 需要把结果回写到外部业务系统（设施报告上传、设施研判预测提交、日常巡查记录、AI 研判问题）时，不再由后端进程直连上游，而是由**随 AgentB 一起部署的 MCP 服务**统一承担：AgentB 作为 MCP 客户端，MCP 服务作为上游业务系统的唯一出口。

## 一、部署形态

Compose 部署（单机模式与平台模式都会启动该服务）：

| 项 | 值 |
| --- | --- |
| 服务名 | `agentb-mcp-upstream-tools` |
| 镜像 | 与 AgentB API 同一镜像、同一份代码（`Dockerfile` 内 `COPY . .`） |
| 启动命令 | `python -m mcp_servers.upstream_tools.server`（工作目录 `/app/WorkBranch`） |
| 协议/端口 | MCP streamable-http，容器内 8181（`expose`，**不发布宿主端口**） |
| 健康检查 | `curl -fsS http://127.0.0.1:8181/healthz` |
| 资源限制 | 默认 `cpus=1.0`、`memory=1G`（可用 `AGENTB_MCP_CPU_LIMIT` / `AGENTB_MCP_MEMORY_LIMIT` 调整） |
| 重启策略 | `unless-stopped` |
| 卷 | 区域配置文件（只读挂载到 `/app/mcp-config/config.json`）+ 与 API 共享的 `agentb-workspaces` |

因此「部署 AgentB」即等于「带上上游工具 MCP 服务」，对接方无需单独部署。

AgentB 侧的连接配置（`setting.json` → `agent_tools.mcp.upstream_tools`，非空环境变量优先）：

| 配置项 | 环境变量 | 默认 |
| --- | --- | --- |
| `url` | `AGENTB_MCP_UPSTREAM_TOOLS_URL` | `http://agentb-mcp-upstream-tools:8181/mcp`（容器外/裸机为 `http://127.0.0.1:8181/mcp`） |
| `timeout_seconds` | `AGENTB_MCP_UPSTREAM_TOOLS_TIMEOUT_SECONDS` | `60` |
| `probe_timeout_seconds` | `AGENTB_MCP_UPSTREAM_TOOLS_PROBE_TIMEOUT_SECONDS` | `5` |
| `tools` | — | 登记到本系统的工具清单（默认 4 个；未登记的工具不暴露给模型） |
| `tool_options` | — | 按工具的选项覆盖（如文件类参数、单工具超时） |

## 二、区域配置（新区域必改）

MCP 服务**不读取** AgentB 的 settings 服务，只认自己的配置文件与环境变量：

- 配置文件路径：容器内固定为 `/app/mcp-config/config.json`（由 `AGENTB_MCP_UPSTREAM_CONFIG` 指定）；
- 宿主机文件由 `AGENTB_MCP_UPSTREAM_CONFIG_FILE` 决定，默认 `./WorkBranch/mcp_servers/upstream_tools/config.example.json`；
- **默认值是示例文件，里面的地址与密钥都是占位值，部署前必须替换**，否则服务会在启动阶段直接报错退出。

字段与说明见 `upstream-tools-config.template.json`：

| 段 | 必填字段 | 说明 |
| --- | --- | --- |
| `facility_report` | `api_url`、`timeout_seconds` | 报告上传与研判预测提交（同一系统） |
| `dailypatrol` | `api_url`、`timeout_seconds`、`secret_key` | 巡查记录回写，密钥建议用环境变量注入 |
| `ai_judgment` | `api_url`、`timeout_seconds` | AI 研判问题提交 |

环境变量覆盖（优先级高于配置文件，密钥建议只走环境变量）：

| 环境变量 | 覆盖字段 |
| --- | --- |
| `AGENTB_MCP_FACILITY_REPORT_API_URL` / `AGENTB_MCP_FACILITY_REPORT_TIMEOUT_SECONDS` | `facility_report.*` |
| `AGENTB_MCP_DAILYPATROL_API_URL` / `AGENTB_MCP_DAILYPATROL_TIMEOUT_SECONDS` / `AGENTB_MCP_DAILYPATROL_SECRET_KEY` | `dailypatrol.*` |
| `AGENTB_MCP_AI_JUDGMENT_API_URL` / `AGENTB_MCP_AI_JUDGMENT_TIMEOUT_SECONDS` | `ai_judgment.*` |
| `AGENTB_MCP_HOST` / `AGENTB_MCP_PORT` | 服务监听地址（默认 `127.0.0.1:8181`） |
| `AGENTB_MCP_UPSTREAM_CONFIG` | 配置文件路径 |

## 三、提供的 4 个工具

| 工具 | 必要参数 | 说明 |
| --- | --- | --- |
| `submit_facility_report` | `reportName`、`facilityId`、`facilityName`、`reportFile`、`regionId` | 上传设施报告并触发研判报告生成；`reportFile` 为工作区内文件路径 |
| `submit_facility_forecast` | `regionId`、`facilityId`、`predictYear`、`reportFile` | 提交设施预测结果（可选 `facilityName`、`predictedHealthScore`、`predictedRiskLevel`、`summary`） |
| `submit_dailypatrol_record` | `title`、`xcdate`、`typeid`、`typename`、`nameid`、`ssname`、`xcunitname`、`dq`、`isdjrw`、`isyhby`、`xcperson`、`xcphone`、`xcunitid` | 回写日常巡查记录（其余字段可选） |
| `submit_ai_judgment_issue` | `facilityId`、`facilityName`、`title`、`regionId` | 提交 AI 研判问题（可选 `description`） |

工具的参数说明由 MCP 服务的 `inputSchema` 自动转译进模型提示词，因此参数名必须与上表一致。

## 四、启动校验与健康检查

1. 服务在 `mcp.run()` 之前读取并校验配置：缺文件、缺段、缺字段（含密钥为空）都直接以非 0 退出，不会「带病启动」。
2. `GET /healthz` 返回 `200` 的条件：配置可加载 **且** 4 个工具都已注册：

```json
{"status":"ok","tools":["submit_ai_judgment_issue","submit_dailypatrol_record","submit_facility_forecast","submit_facility_report"]}
```

3. 不满足时返回 `503`，body 带 `reason`（`config_invalid` / `tools_missing`）与具体缺项；Compose 依赖该端点判定容器健康，因此「配置没替换」「工具少注册」会在部署阶段就暴露。

## 五、可用性语义（重要）

1. AgentB 启动时探活是**后台任务**，不阻塞启动；失败只告警（日志 + `/health.mcp`），首个会话可能早于探活完成。
2. MCP 服务不可用时，这 4 个工具**不会出现在模型可见的工具协议里**；提示词会注入服务告警，要求 Agent 说明「该功能依赖的上游服务不可用」。
3. 调用期失败（运行中掉线）会如实失败，并在日志、工具事件与会话流的 `system_alert` 段重复告警；上游业务错误（如「用户不存在」）不算服务故障，不会误报。
4. 不做本地兜底、不做工具清单缓存；服务恢复后需重启 AgentB 实例才会重新登记工具。

## 六、部署与验证

Compose：

```bash
cp .env.compose.example .env.compose          # 至少设置 MYSQL_PASSWORD / MYSQL_ROOT_PASSWORD / LLM_*
# 准备区域配置并指向它
AGENTB_MCP_UPSTREAM_CONFIG_FILE=/etc/agentb/mcp_upstream_tools.json
docker compose --env-file .env.compose -f compose.yml -f compose.standalone.yml up -d --build

docker compose ps                              # agentb-mcp-upstream-tools 应为 healthy
docker compose logs agentb-mcp-upstream-tools  # 启动失败时看这里（会给出缺哪个字段）
curl -fsS http://127.0.0.1:8152/api/health     # 看 mcp.available / mcp.registered
docker compose exec agentb-mcp-upstream-tools curl -fsS http://127.0.0.1:8181/healthz
```

裸机（无 Docker）：三个进程用统一脚本启动，脚本会先校验虚拟环境、区域配置与 gunicorn：

```bash
AGENTB_MCP_UPSTREAM_CONFIG=/etc/agentb/mcp_upstream_tools.json \
    bash deploy/bare-metal/start-all.sh
```

本地开发：`WorkBranch/run_dev.py` 默认随后端一起启动 MCP 服务，可用 `--no-mcp` 只启后端，或用 `--mcp-config <路径>` 指定区域配置（默认取 `AGENTB_MCP_UPSTREAM_CONFIG`，再退回 `WorkBranch/mcp_servers/upstream_tools/config.json`；两者都不存在时启动器会给出明确提示并退出）。

## 七、排障

| 现象 | 原因与处理 |
| --- | --- |
| 容器反复重启，日志报「配置文件不存在 / 配置缺少段 / 配置段缺少字段」 | 区域配置未替换或挂载路径不对；检查 `AGENTB_MCP_UPSTREAM_CONFIG_FILE` 与容器内 `/app/mcp-config/config.json` |
| `/healthz` 返回 503 且 `reason=config_invalid` | 配置文件在运行期被改坏或权限不可读 |
| `/healthz` 返回 503 且 `reason=tools_missing` | 服务版本与内置工具不一致（应为 4 个工具），确认镜像与代码版本一致 |
| `/health.mcp.available=false` | AgentB 连不上 MCP 服务：核对 `AGENTB_MCP_UPSTREAM_TOOLS_URL`、容器网络与服务健康状态 |
| 工具调用成功但上游报错 | 业务错误：检查区域配置里的上游地址、巡查密钥与上游系统日志；这类错误不会触发 `system_alert` |
| 报告类工具报「文件不存在」 | 工作区卷未共享或文件路径不是工作区内的路径；确认 MCP 服务与 API 挂载了同一个 `agentb-workspaces` |
