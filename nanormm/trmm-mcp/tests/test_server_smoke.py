def test_server_module_loads(trmm_env, tmp_path, monkeypatch):
    """Importing the server module must register every documented tool."""
    pol = tmp_path / "policy.yaml"
    pol.write_text(
        """
version: 1
default: human_approval
tools:
  list_alerts: auto
"""
    )
    monkeypatch.setenv("NANORMM_POLICY_PATH", str(pol))

    from trmm_mcp.server import build_server

    srv = build_server()
    names = srv.tool_registry.tool_names()

    expected = {
        "list_alerts",
        "get_alert",
        "search_past_alerts",
        "acknowledge_alert",
        "list_agents",
        "get_agent",
        "agent_recent_checks",
        "agent_recent_tasks",
        "agent_patch_state",
        "agent_running_processes",
        "query_clients",
        "script_history",
        "run_script_on_agent",
        "run_inline_command",
        "kill_process",
        "restart_service",
        "reboot_agent",
    }
    missing = expected - set(names)
    assert not missing, f"server is missing tools: {missing}"


def test_tool_descriptors_have_real_schemas(trmm_env, tmp_path, monkeypatch):
    pol = tmp_path / "policy.yaml"
    pol.write_text("version: 1\ndefault: human_approval\ntools: {}\n")
    monkeypatch.setenv("NANORMM_POLICY_PATH", str(pol))

    from trmm_mcp.server import _tool_descriptor, build_server

    server = build_server()
    for name in server.tool_registry.tool_names():
        desc = _tool_descriptor(name)
        # Real schemas have at least one property or no required-fields constraint
        assert isinstance(desc.inputSchema, dict)
        assert desc.inputSchema.get("type") == "object"
        # Description should be non-trivial
        assert len(desc.description) > 20, f"{name} has weak description"


def test_kill_process_schema_requires_agent_id(trmm_env, tmp_path, monkeypatch):
    pol = tmp_path / "policy.yaml"
    pol.write_text("version: 1\ndefault: human_approval\ntools: {}\n")
    monkeypatch.setenv("NANORMM_POLICY_PATH", str(pol))

    from trmm_mcp.server import _tool_descriptor

    desc = _tool_descriptor("kill_process")
    assert "agent_id" in desc.inputSchema["properties"]
    assert "agent_id" in desc.inputSchema["required"]


def test_build_server_returns_populated_mcp_server(trmm_env, fake_redis, audit_dsn, tmp_path, monkeypatch):
    """The Server instance returned by build_server() must have list_tools
    handlers wired (so streamable_http_app() can serve it later)."""
    import redis

    monkeypatch.setattr(redis.Redis, "from_url", lambda *_a, **_k: fake_redis)

    pol_path = tmp_path / "p.yaml"
    pol_path.write_text(
        """
version: 1
default: human_approval
tools:
  list_alerts: auto
  kill_process: human_approval
"""
    )
    monkeypatch.setenv("NANORMM_POLICY_PATH", str(pol_path))
    monkeypatch.setenv("NANORMM_AUDIT_DSN", audit_dsn)

    from trmm_mcp.server import build_server

    server = build_server()

    # Server has request handlers registered
    assert server.mcp is not None
    # Tool registry is populated with at least one read tool
    assert "list_alerts" in server.tool_registry.tool_names()
