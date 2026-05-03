"""MCP server entrypoint for trmm-mcp."""

from dataclasses import dataclass
from typing import Any

import redis
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .approvals import ApprovalRegistry
from .audit import AuditLog
from .exceptions import PolicyError
from .policy import Policy
from .settings import Settings
from .tools import actions, agents, alerts, clients, scripts
from .tools._base import Dispatcher, ToolRegistry
from .trmm_client import TrmmClient


@dataclass
class TrmmMcpServer:
    mcp: Server
    tool_registry: ToolRegistry
    dispatcher: Dispatcher
    trmm_client: TrmmClient


def build_dispatcher(settings: Settings | None = None) -> tuple[Dispatcher, ToolRegistry, TrmmClient]:
    """Construct the Dispatcher graph (policy + approvals + audit + tool
    registry + trmm client). Used by both the MCP server and approval-bridge
    so the two processes share construction logic.
    """
    settings = settings or Settings()
    policy = Policy.load(settings.policy_path)
    approvals = ApprovalRegistry(
        redis.Redis.from_url(settings.redis_url, decode_responses=True),
        ttl_seconds=settings.pending_action_ttl_seconds,
    )
    audit = AuditLog(settings.audit_dsn)
    trmm = TrmmClient(base_url=settings.trmm_api_base, token=settings.trmm_api_token)

    registry = ToolRegistry()
    _register_all(registry, trmm)

    dispatcher = Dispatcher(registry=registry, policy=policy, approvals=approvals, audit=audit)
    return dispatcher, registry, trmm


def build_server() -> TrmmMcpServer:
    dispatcher, registry, trmm = build_dispatcher()

    mcp = Server("trmm-mcp")

    @mcp.list_tools()
    async def _list_tools() -> list[Tool]:
        return [_tool_descriptor(name) for name in registry.tool_names()]

    @mcp.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        # Read X-Nanoclaw-Session populated by the bridge's middleware. Lazy
        # import: trmm-mcp's stdio path doesn't have approval_bridge installed,
        # so try/except keeps the stdio path independent.
        session_id: str | None = None
        try:
            from approval_bridge.mcp_app import SESSION_ID_VAR  # type: ignore
            session_id = SESSION_ID_VAR.get()
        except ImportError:
            pass

        result = await dispatcher.dispatch(name, arguments, session_id=session_id)
        return [TextContent(type="text", text=_serialize(result))]

    return TrmmMcpServer(mcp=mcp, tool_registry=registry, dispatcher=dispatcher, trmm_client=trmm)


def _register_all(registry: ToolRegistry, trmm: TrmmClient) -> None:
    """Bind every tool function to the registry, partial-applying the TRMM client."""

    def bind(name: str, fn):
        async def wrapped(**kwargs):
            return await fn(client=trmm, **kwargs)

        registry.register(name=name)(wrapped)

    bind("list_alerts", alerts.list_alerts)
    bind("get_alert", alerts.get_alert)
    bind("search_past_alerts", alerts.search_past_alerts)
    bind("acknowledge_alert", alerts.acknowledge_alert)

    bind("list_agents", agents.list_agents)
    bind("get_agent", agents.get_agent)
    bind("agent_recent_checks", agents.agent_recent_checks)
    bind("agent_recent_tasks", agents.agent_recent_tasks)
    bind("agent_patch_state", agents.agent_patch_state)
    bind("agent_running_processes", agents.agent_running_processes)

    bind("query_clients", clients.query_clients)

    bind("script_history", scripts.script_history)
    bind("run_script_on_agent", scripts.run_script_on_agent)
    bind("run_inline_command", scripts.run_inline_command)

    bind("kill_process", actions.kill_process)
    bind("restart_service", actions.restart_service)
    bind("reboot_agent", actions.reboot_agent)


def _tool_descriptor(name: str) -> Tool:
    from .tools._schemas import SCHEMAS

    entry = SCHEMAS.get(name)
    if entry is None:
        raise PolicyError(f"no schema for tool {name}; add to tools/_schemas.py")
    return Tool(
        name=name,
        description=entry["description"],
        inputSchema=entry["schema"],
    )


def _serialize(payload: Any) -> str:
    import json

    return json.dumps(payload, default=str, separators=(",", ":"))


async def run() -> None:
    server = build_server()
    # Replay any approved-but-unexecuted actions from before a crash
    await server.dispatcher.recover()
    async with stdio_server() as (read, write):
        await server.mcp.run(read, write, server.mcp.create_initialization_options())
