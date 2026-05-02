# Plan 4 spike results

## Spike 0.1: MCP header pass-through

- **Result:** PASS
- **STATUS observed:** 200
- **BODY observed:** `{"result":"sid='test-session-001'"}`
- **Decision:** Proceed with middleware+contextvar design

### Details

Tested that a Starlette `BaseHTTPMiddleware` can extract the `x-nanoclaw-session` header from an incoming request and store it in a Python `ContextVar`. The async tool handler (simulating an MCP `@server.call_tool()` decorated function) was able to read the ContextVar and confirm the value survived the middleware → handler transition.

This validates the core assumption for the production design: session_id can flow pure-plumbing-style from container env → MCP request header → middleware → tool handler → Dispatcher without invasive tool-args mutation.

### Test code

The spike ran the production-shape ASGI stack: a real `mcp.server.Server`, wrapped in a real `StreamableHTTPSessionManager` (`stateless=True, json_response=True`), wrapped in a `SessionHeaderMiddleware`, mounted under FastAPI at `/mcp`. The middleware extracts `x-nanoclaw-session` into a `ContextVar`; the `@server.call_tool()` handler reads the same `ContextVar` and returns its value. The driver does the JSON-RPC `initialize` + `tools/call` handshake against the real session-manager path.

```
POST /mcp/ initialize, then POST /mcp/ tools/call (name="spike")
  with header "x-nanoclaw-session: test-session-001"
Expected: tool handler returns "sid='test-session-001'"
Actual: BODY {"result":"sid='test-session-001'"}
```

Note on middleware shape: the spike used `BaseHTTPMiddleware` for convenience, but the production design uses raw ASGI middleware (`async def __call__(self, scope, receive, send)`). The raw ASGI form preserves `ContextVar`s at least as well as `BaseHTTPMiddleware` (no task-group hop) — the spike result is sufficient evidence for the production design.

---

## Spike 0.2: session schema lookup

### sessions table columns

```
CREATE TABLE sessions (
        id                 TEXT PRIMARY KEY,
        agent_group_id     TEXT NOT NULL REFERENCES agent_groups(id),
        messaging_group_id TEXT REFERENCES messaging_groups(id),
        thread_id          TEXT,
        agent_provider     TEXT,
        status             TEXT DEFAULT 'active',
        container_status   TEXT DEFAULT 'stopped',
        last_active        TEXT,
        created_at         TEXT NOT NULL
      )
```

### messaging_groups table columns

```
CREATE TABLE messaging_groups (
        id                    TEXT PRIMARY KEY,
        channel_type          TEXT NOT NULL,
        platform_id           TEXT NOT NULL,
        name                  TEXT,
        is_group              INTEGER DEFAULT 0,
        unknown_sender_policy TEXT NOT NULL DEFAULT 'strict',
        created_at            TEXT NOT NULL, denied_at TEXT,
        UNIQUE(channel_type, platform_id)
      )
```

### messaging_group_agents table columns

```
CREATE TABLE messaging_group_agents (
        id                 TEXT PRIMARY KEY,
        messaging_group_id TEXT NOT NULL REFERENCES messaging_groups(id),
        agent_group_id     TEXT NOT NULL REFERENCES agent_groups(id),
        session_mode       TEXT DEFAULT 'shared',
        priority           INTEGER DEFAULT 0,
        created_at         TEXT NOT NULL, engage_mode            TEXT, engage_pattern         TEXT, sender_scope           TEXT, ignored_message_policy TEXT,
        UNIQUE(messaging_group_id, agent_group_id)
      )
```

### outbound.db schema (per-session)

```
CREATE TABLE messages_out (
  id             TEXT PRIMARY KEY,
  seq            INTEGER UNIQUE,
  in_reply_to    TEXT,
  timestamp      TEXT NOT NULL,
  deliver_after  TEXT,
  recurrence     TEXT,
  kind           TEXT NOT NULL,
  platform_id    TEXT,
  channel_type   TEXT,
  thread_id      TEXT,
  content        TEXT NOT NULL
)

CREATE TABLE processing_ack (
  message_id     TEXT PRIMARY KEY,
  status         TEXT NOT NULL,
  status_changed TEXT NOT NULL
)

CREATE TABLE session_state (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL
)

CREATE TABLE container_state (
  id                       INTEGER PRIMARY KEY CHECK (id = 1),
  current_tool             TEXT,
  tool_declared_timeout_ms INTEGER,
  tool_started_at          TEXT,
  updated_at               TEXT NOT NULL
)
```

### Routing fields the inject endpoint must populate

For the per-session outbound row, the bridge inject endpoint needs to know:

- **platform_id source:** `messaging_groups.platform_id` — joined via `sessions.messaging_group_id → messaging_groups.id`
- **channel_type source:** `messaging_groups.channel_type` — joined via `sessions.messaging_group_id → messaging_groups.id`
- **thread_id source:** `sessions.thread_id` — direct column on sessions table

### Key observations

1. The plan's assumed `origin_platform_id` / `origin_channel_type` / `origin_thread_id` column names **do not exist** on the `sessions` table. These routing fields must be fetched via JOIN to `messaging_groups`.
2. The `messages_out` table in `outbound.db` has the exact column names expected for the inject: `platform_id`, `channel_type`, `thread_id`, `kind`, `content`, `timestamp`, plus additional fields like `deliver_after`, `recurrence`, `in_reply_to`.
3. The inject endpoint must perform a two-table lookup: read `sessions.messaging_group_id`, then join to `messaging_groups` to fetch `platform_id` and `channel_type`, and directly use `sessions.thread_id`.
4. No divergence detected between the outbound schema and the plan's expected INSERT statement shape.
