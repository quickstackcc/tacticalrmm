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
        "list_alerts", "get_alert", "search_past_alerts", "acknowledge_alert",
        "list_agents", "get_agent",
        "agent_recent_checks", "agent_recent_tasks",
        "agent_patch_state", "agent_running_processes",
        "query_clients",
        "script_history", "run_script_on_agent", "run_inline_command",
        "kill_process", "restart_service", "reboot_agent",
        "collect_artifacts", "isolate_host", "unisolate_host",
        "disable_account", "pause_scheduled_task",
    }
    missing = expected - set(names)
    assert not missing, f"server is missing tools: {missing}"
