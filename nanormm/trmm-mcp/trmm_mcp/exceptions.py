class NanormmError(Exception):
    """Base for all nanormm errors."""


class TrmmApiError(NanormmError):
    def __init__(self, status_code: int, url: str, message: str = ""):
        self.status_code = status_code
        self.url = url
        super().__init__(f"TRMM API error {status_code} at {url}: {message}")


class TrmmAuthError(TrmmApiError):
    """401/403 from TRMM."""


class TrmmNotFoundError(TrmmApiError):
    """404 from TRMM."""


class PolicyError(NanormmError):
    """Policy file missing, invalid, or unknown tool."""


class ApprovalError(NanormmError):
    """Approval registry error (Redis unreachable, bad state, expired)."""
