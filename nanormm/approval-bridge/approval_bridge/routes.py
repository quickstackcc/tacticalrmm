from fastapi import APIRouter, Header, Request, status
from fastapi.responses import JSONResponse

from trmm_mcp.exceptions import ApprovalError, TrmmApiError

from .models import ActionResponse, ErrorResponse, ExecuteRequest, RejectRequest  # noqa: F401

# Two routers so create_app can apply Bearer auth selectively:
# `public_router` carries unauthenticated endpoints (healthz);
# `authed_router` carries the Slack-callable endpoints (/api/nanoclaw/*).
public_router = APIRouter()
authed_router = APIRouter()


@public_router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _approver_label(slack_user_id: str | None, slack_user_name: str | None) -> str:
    """Prefer the human-readable name; fall back to Slack ID; finally 'unknown'."""
    if slack_user_name:
        return slack_user_name
    if slack_user_id:
        return slack_user_id
    return "unknown"


@authed_router.post(
    "/api/nanoclaw/actions/execute/",
    response_model=ActionResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def execute_action(
    request: Request,
    body: ExecuteRequest,
    x_slack_user_id: str | None = Header(default=None, alias="X-Slack-User-ID"),
    x_slack_user_name: str | None = Header(default=None, alias="X-Slack-User-Name"),
) -> ActionResponse:
    dispatcher = request.app.state.dispatcher
    approver = _approver_label(x_slack_user_id, x_slack_user_name)

    pending = dispatcher._approvals.get(body.token)
    if pending is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "Action expired or unknown"},
        )

    current_status = pending["status"]

    if current_status == "rejected":
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "Action was rejected"},
        )

    if current_status == "executed":
        # Slack retry — return cached summary, do not re-execute.
        return ActionResponse(message=f"Executed: {pending['summary']}")

    if current_status == "expired":
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "Action expired or unknown"},
        )

    # status is 'pending' or 'approved' (the latter only if a previous
    # bridge invocation marked approved and crashed before resume completed).
    try:
        if current_status == "pending":
            dispatcher._approvals.mark_approved(body.token, approved_by=approver)
            dispatcher._audit.record_approval(action_id=body.token, approved_by=approver)
        await dispatcher.resume(body.token)
    except ApprovalError as e:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": str(e)},
        )
    except TrmmApiError as e:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"error": f"{type(e).__name__}: {e}"},
        )

    return ActionResponse(message=f"Executed: {pending['summary']}")
