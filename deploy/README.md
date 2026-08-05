# AgentB multi-instance deployment

The production topology is three independently addressable, single-worker API
instances behind an Nginx consistent-hash router. Redis stores Session owner
leases, instance heartbeats, resumable conversation streams, and the RAG job
queue. MySQL remains the source of truth for Sessions and Conversations.

## Standalone mode

Copy `.env.compose.example` to `.env.compose`, set the database passwords, and
point `AGENTB_SETTING_FILE` at the deployment settings file. The supplied
development configuration is `.dev/setting.json`. It is mounted read-only as
`/app/setting.json` in every API and RAG worker so all processes use the same
LLM, tool, and timeout settings. Empty `LLM_*` values preserve the values in
that file; non-empty environment values override them. Then start the stack:

```powershell
docker compose --env-file .env.compose -f compose.yml -f compose.standalone.yml up -d
```

The only published port is `127.0.0.1:8152` by default. Set `AGENTB_PORT` to
the next available port if 8152 is occupied. MySQL 3306 and Redis 6379 are only
exposed to the Compose network.

## Workspace persistence

The API resolves `workspace.base_dir` from `AGENTB_WORKSPACE_DIR`. Compose sets
it to `/app/workspaces`, where every workspace is stored as
`/app/workspaces/<session_id>/<workspace_id>/`. All three API containers mount
the shared `agentb-workspaces` volume at that exact path.

For the supplied Compose files, deploy and back up the `agentb-workspaces`
named volume. If the platform requires a bind mount, replace the volume source
with a durable host path but keep the container target `/app/workspaces`, for
example `/srv/agentb/workspaces:/app/workspaces`. The legacy single-instance
`docker-compose.yml` uses `./workspaces:/app/workspaces`.

The settings file is configuration, not workspace data. Deployments should
mount it at `/app/setting.json`; workspace persistence must remain mounted at
`/app/workspaces`.

## Platform mode

Set `AGENTB_REDIS_URL`, all `MYSQL_*` values, and
`AGENTB_PLATFORM_NETWORK` to resources supplied by the platform. The Redis ACL
user must be restricted to the configured `AGENTB_REDIS_PREFIX` and must allow
string, hash, list, scripting, and Stream commands. Then run:

```powershell
docker compose --env-file .env.compose -f compose.yml -f compose.platform.yml up -d
```

No host port is published in platform mode. The upstream proxy should send
traffic to `http://agentb-router:8080`, preserve `Host`,
`X-Forwarded-For`, and `X-Forwarded-Proto`, and keep response buffering off.

## Affinity contract

Business writes use `X-AgentB-Affinity-Key: <session_id>`. The initial Session
creation uses a client-generated UUID; affinity begins with the returned
Session ID on the next request. Native EventSource clients may use
`?affinity_key=<session_id>`. Missing keys are rejected with HTTP 400. A key
that does not match the database Session is rejected with HTTP 409. Owner lease
conflicts return HTTP 409 and `X-AgentB-Owner-ID`.

The frontend field lifecycle, per-endpoint requirements, SSE reconnection, and
error-handling contract are documented in [FRONTEND_AFFINITY.md](FRONTEND_AFFINITY.md).

Every response includes `X-AgentB-Instance-ID`. Streams emit SSE `id` fields,
accept `Last-Event-ID` or `last_seq`, retain terminal `done`, `error`, and
`cancelled` events, and are bounded by `AGENTB_STREAM_MAXLEN` and
`AGENTB_STREAM_RETENTION_SECONDS`.

## Operations

Before planned removal of an instance, call `POST /admin/drain` inside that
container and wait until `/health` reports `active_tasks: 0`:

```powershell
docker compose -f compose.yml -f compose.standalone.yml exec agentb-1 `
  curl -fsS -X POST -H "X-User-ID: 1" http://127.0.0.1:8000/admin/drain
```

The readiness endpoint returns 503 while draining. Remove the instance from
the router only after active tasks reach zero. Do not change the three-node
hash ring while long-running tasks are active.

For upgrades, back up MySQL, the `agentb-rag-data`, `agentb-rag-docs`, and
`agentb-workspaces` volumes, and the Redis AOF before replacing containers.
Apply the MySQL migration by starting one new API instance first; startup fails
if historical data violates the one-running-Conversation-per-Session unique
index. Reconcile those rows as `failed` before retrying.

For rollback, drain the new instances and temporarily reduce
`deploy/nginx/agentb.conf` to one healthy upstream. The legacy
`docker-compose.yml` remains a single-instance fallback on port 8152. Do not
run SQLite MQ files from more than one container.

RAG ingestion is performed only by `agentb-rag-worker`. The included named
volumes support a single Docker host. Multi-host deployment requires shared
durable document storage and migration of RAG metadata to a database that
supports concurrent hosts.

## Verification

After the stack is healthy, run the black-box test:

```powershell
python deploy/e2e/affinity_smoke.py
```

Optional variables are `AGENTB_E2E_BASE_URL`, `AGENTB_E2E_USER_ID`, and
`AGENTB_E2E_SESSION_COUNT`. The script creates and cleans up test Sessions and
verifies distribution, instance stability, idempotency, cancellation, and
invalid affinity behavior.

Useful diagnostics:

```powershell
curl.exe http://127.0.0.1:8152/router-health
curl.exe http://127.0.0.1:8152/api/health
docker compose -f compose.yml -f compose.standalone.yml ps
docker compose -f compose.yml -f compose.standalone.yml logs agentb-router agentb-1 redis
```

## Distributed regression suite

The legacy backend E2E client has been adapted to the affinity contract. With
an already healthy standalone stack on port 8152, run the affinity smoke test
and the selected 16-scenario regression suite together:

```powershell
powershell -ExecutionPolicy Bypass -File deploy/e2e/run_regression.ps1
```

Use `-Port 8153` (or the next configured router port) when 8152 is occupied.
Use `-ComposeMode platform` when collecting logs from the platform override.
The command does not start or stop containers. It writes the suite output,
affinity smoke output, Compose logs, and an exit-code summary under
`WorkBranch/backend/.test/logs/distributed_<timestamp>/`.
Before contacting the router it validates all fixture files declared by the
selected scenarios. Missing files are listed in the suite log and the command
stops with exit code 1 without creating test Sessions.
The local input files live under `.dev/fixture` and are intentionally ignored
by Git. See [e2e/FIXTURES.md](e2e/FIXTURES.md) for the exact file list.

For long local builds or diagnostics, start the standalone WSL control console:

```powershell
cmd.exe /d /c wsl.exe --cd D:\dev\projects\agentb python3 deploy/e2e/debug_console.py --port 8153
```

Then open `http://127.0.0.1:8153/`. The console process keeps the WSL instance
active while it runs. It exposes only fixed project actions, binds to localhost,
and injects a per-process token into the page. It can start dependencies and
workers in order, build or stop the stack, collect Compose logs, and launch the
16-scenario regression command. Stop it with `Ctrl+C` when monitoring is done.

For builds or regressions that need interactive monitoring, start the local
control console:

```powershell
.venv\Scripts\python.exe deploy/e2e/control_console.py --port 8153
```

Open `http://127.0.0.1:8153`. If that port is occupied, the console selects the
next free port through 8162 and prints the selected URL. It binds only to
localhost and exposes fixed Compose, log, WSL keepalive, and regression actions;
it does not accept arbitrary shell commands.
