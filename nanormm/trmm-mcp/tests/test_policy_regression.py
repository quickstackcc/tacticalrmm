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


def test_every_gated_tool_in_policy_has_a_renderer(trmm_env, monkeypatch):
    """Every tool the production policy.yaml gates as `human_approval` must
    declare a `render` callable in SCHEMAS. The renderer is what the human
    sees on the approval card; missing it would silently fall back to caller
    text (the C3 vulnerability) — except dispatch() now hard-fails instead.
    This test catches "added a new gated tool, forgot the renderer" at PR time
    rather than at production-card-injection time.
    """
    repo_root = Path(__file__).resolve().parents[3]
    pol_path = repo_root / "nanormm" / "policy.yaml"
    monkeypatch.setenv("NANORMM_POLICY_PATH", str(pol_path))

    from trmm_mcp.policy import Authority, Policy
    from trmm_mcp.tools._schemas import SCHEMAS

    pol = Policy.load(pol_path)
    gated = [
        name
        for name in pol._model.tools  # noqa: SLF001
        if pol.authority(name) is Authority.HUMAN_APPROVAL
    ]
    missing = [
        name
        for name in gated
        if name not in SCHEMAS or not callable(SCHEMAS[name].get("render"))
    ]
    assert not missing, (
        f"gated tools without a render() in SCHEMAS: {sorted(missing)}. "
        f"Add a `render` lambda to each entry in trmm_mcp/tools/_schemas.py."
    )
