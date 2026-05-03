"""JSON Schemas + descriptions for each tool, surfaced to MCP clients."""

from typing import Any

_AGENT_ID = {"type": "string", "description": "TRMM agent UUID"}
_INT_LIMIT = {"type": "integer", "minimum": 1, "maximum": 200, "default": 20}

SCHEMAS: dict[str, dict[str, Any]] = {
    "list_alerts": {
        "description": (
            "List TRMM alerts. Optionally filter by status (all|unresolved|snoozed), "
            "since timestamp (ISO 8601), or client ID."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["all", "unresolved", "snoozed"]},
                "since": {"type": "string", "description": "ISO 8601 timestamp"},
                "client_id": {"type": "integer"},
            },
        },
    },
    "get_alert": {
        "description": "Fetch full detail for one alert by integer ID.",
        "schema": {
            "type": "object",
            "properties": {"alert_id": {"type": "integer"}},
            "required": ["alert_id"],
        },
    },
    "search_past_alerts": {
        "description": "Find historical alerts on a given agent since an ISO timestamp.",
        "schema": {
            "type": "object",
            "properties": {
                "agent_id": _AGENT_ID,
                "since": {"type": "string", "description": "ISO 8601 timestamp"},
            },
            "required": ["agent_id", "since"],
        },
    },
    "acknowledge_alert": {
        "description": "Mark an alert as resolved with an optional note (write).",
        "schema": {
            "type": "object",
            "properties": {
                "alert_id": {"type": "integer"},
                "note": {"type": "string"},
            },
            "required": ["alert_id"],
        },
    },
    "list_agents": {
        "description": "List TRMM agents. Filter by online status, client ID, or site ID.",
        "schema": {
            "type": "object",
            "properties": {
                "online": {"type": "boolean"},
                "client_id": {"type": "integer"},
                "site_id": {"type": "integer"},
            },
        },
    },
    "get_agent": {
        "description": "Fetch the full record for one agent.",
        "schema": {
            "type": "object",
            "properties": {"agent_id": _AGENT_ID},
            "required": ["agent_id"],
        },
    },
    "agent_recent_checks": {
        "description": "Recent check results for an agent (pass/fail + output).",
        "schema": {
            "type": "object",
            "properties": {"agent_id": _AGENT_ID, "n": _INT_LIMIT},
            "required": ["agent_id"],
        },
    },
    "agent_recent_tasks": {
        "description": "Recent scheduled-task runs for an agent.",
        "schema": {
            "type": "object",
            "properties": {"agent_id": _AGENT_ID, "n": _INT_LIMIT},
            "required": ["agent_id"],
        },
    },
    "agent_patch_state": {
        "description": "Windows Update state: installed, missing, failed, pending-reboot KBs.",
        "schema": {
            "type": "object",
            "properties": {"agent_id": _AGENT_ID},
            "required": ["agent_id"],
        },
    },
    "agent_running_processes": {
        "description": "Live process list from an agent (PID, name, CPU, memory).",
        "schema": {
            "type": "object",
            "properties": {"agent_id": _AGENT_ID},
            "required": ["agent_id"],
        },
    },
    "query_clients": {
        "description": "Return the full client to site tree.",
        "schema": {"type": "object", "properties": {}},
    },
    "script_history": {
        "description": "Recent script runs on an agent (stdout, stderr, retcode).",
        "schema": {
            "type": "object",
            "properties": {"agent_id": _AGENT_ID, "n": _INT_LIMIT},
            "required": ["agent_id"],
        },
    },
    "run_script_on_agent": {
        "description": (
            "Invoke a TRMM-library script by integer ID on an agent. "
            "Cannot ship a script body — only IDs of scripts already in TRMM. (write)"
        ),
        "schema": {
            "type": "object",
            "properties": {
                "agent_id": _AGENT_ID,
                "script_id": {"type": "integer"},
                "args": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["agent_id", "script_id"],
        },
    },
    "run_inline_command": {
        "description": (
            "Run an arbitrary shell command on an agent. PERMANENTLY GATED — every "
            "invocation requires human approval. (write)"
        ),
        "schema": {
            "type": "object",
            "properties": {
                "agent_id": _AGENT_ID,
                "shell": {"type": "string", "enum": ["cmd", "powershell", "bash"]},
                "command": {"type": "string"},
            },
            "required": ["agent_id", "shell", "command"],
        },
    },
    "kill_process": {
        "description": "Kill a process on an agent by PID. (write)",
        "schema": {
            "type": "object",
            "properties": {
                "agent_id": _AGENT_ID,
                "pid": {"type": "integer"},
            },
            "required": ["agent_id", "pid"],
        },
    },
    "restart_service": {
        "description": (
            "Restart a Windows service on an agent. service_name is the service "
            "short name (e.g. W3SVC, Spooler), not the display name. (write)"
        ),
        "schema": {
            "type": "object",
            "properties": {
                "agent_id": _AGENT_ID,
                "service_name": {"type": "string"},
            },
            "required": ["agent_id", "service_name"],
        },
    },
    "reboot_agent": {
        "description": "Reboot an agent host. (write)",
        "schema": {
            "type": "object",
            "properties": {"agent_id": _AGENT_ID},
            "required": ["agent_id"],
        },
    },
}
