from pathlib import Path


def test_production_policy_yaml_covers_every_registered_tool(trmm_env, monkeypatch):
    """The shipped nanormm/policy.yaml must list every tool the server registers."""
    repo_root = Path(__file__).resolve().parents[3]
    pol_path = repo_root / "nanormm" / "policy.yaml"
    monkeypatch.setenv("NANORMM_POLICY_PATH", str(pol_path))

    from trmm_mcp.policy import Policy
    from trmm_mcp.server import build_server

    server = build_server()
    registered = set(server.tool_registry.tool_names())

    pol = Policy.load(pol_path)
    listed = set(pol._model.tools.keys())  # noqa: SLF001

    missing = registered - listed
    assert not missing, (
        f"policy.yaml is missing entries for: {sorted(missing)}. "
        f"Add explicit `auto`/`human_approval`/`forbidden` lines to nanormm/policy.yaml."
    )

    extra = listed - registered
    assert not extra, (
        f"policy.yaml lists tools that are not registered with the server: {sorted(extra)}. "
        f"Either register the tool or remove the policy entry."
    )
