# AgentB multi-instance deployment

The production topology is three independently addressable, single-worker API
instances behind an Nginx consistent-hash router. Redis stores Session owner
leases, instance heartbeats, resumable conversation streams, and the RAG job
queue. MySQL remains the source of truth for Sessions and Conversations.

## Standalone mode

Copy `.env.compose.example` to `.env.compose`, replace every password and API
key, then start the stack:

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
