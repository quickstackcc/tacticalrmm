from pathlib import Path

import pytest


@pytest.fixture
def good_policy(tmp_path: Path) -> Path:
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
version: 1
default: human_approval
tools:
  list_alerts: auto
  kill_process: human_approval
  uninstall_agent: forbidden
"""
    )
    return p


def test_policy_loads_from_file(good_policy: Path):
    from trmm_mcp.policy import Policy

    p = Policy.load(good_policy)
    assert p.version == 1
    assert p.default == "human_approval"


def test_policy_authority_returns_explicit(good_policy: Path):
    from trmm_mcp.policy import Authority, Policy

    p = Policy.load(good_policy)
    assert p.authority("list_alerts") == Authority.AUTO
    assert p.authority("kill_process") == Authority.HUMAN_APPROVAL
    assert p.authority("uninstall_agent") == Authority.FORBIDDEN


def test_policy_authority_falls_back_to_default(good_policy: Path):
    from trmm_mcp.policy import Authority, Policy

    p = Policy.load(good_policy)
    assert p.authority("brand_new_tool") == Authority.HUMAN_APPROVAL


def test_policy_missing_file_raises(tmp_path: Path):
    from trmm_mcp.exceptions import PolicyError
    from trmm_mcp.policy import Policy

    with pytest.raises(PolicyError):
        Policy.load(tmp_path / "missing.yaml")


def test_policy_invalid_yaml_raises(tmp_path: Path):
    from trmm_mcp.exceptions import PolicyError
    from trmm_mcp.policy import Policy

    p = tmp_path / "bad.yaml"
    p.write_text("::: not yaml :::")
    with pytest.raises(PolicyError):
        Policy.load(p)


def test_policy_unknown_authority_value_raises(tmp_path: Path):
    from trmm_mcp.exceptions import PolicyError
    from trmm_mcp.policy import Policy

    p = tmp_path / "bad.yaml"
    p.write_text(
        """
version: 1
default: human_approval
tools:
  some_tool: maybe_dunno
"""
    )
    with pytest.raises(PolicyError):
        Policy.load(p)


def test_policy_wrong_version_raises(tmp_path: Path):
    from trmm_mcp.exceptions import PolicyError
    from trmm_mcp.policy import Policy

    p = tmp_path / "v9.yaml"
    p.write_text(
        """
version: 9
default: human_approval
tools: {}
"""
    )
    with pytest.raises(PolicyError):
        Policy.load(p)
