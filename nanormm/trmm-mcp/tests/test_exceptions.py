import pytest


def test_exception_hierarchy():
    from trmm_mcp.exceptions import (
        NanormmError,
        TrmmApiError,
        TrmmAuthError,
        TrmmNotFoundError,
        PolicyError,
        ApprovalError,
    )

    assert issubclass(TrmmApiError, NanormmError)
    assert issubclass(TrmmAuthError, TrmmApiError)
    assert issubclass(TrmmNotFoundError, TrmmApiError)
    assert issubclass(PolicyError, NanormmError)
    assert issubclass(ApprovalError, NanormmError)


def test_trmm_api_error_carries_status_and_url():
    from trmm_mcp.exceptions import TrmmApiError

    err = TrmmApiError(status_code=500, url="https://api.example/agents", message="boom")
    assert err.status_code == 500
    assert err.url == "https://api.example/agents"
    assert "500" in str(err)
    assert "boom" in str(err)
