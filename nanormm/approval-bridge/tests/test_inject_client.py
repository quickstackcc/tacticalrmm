import httpx
import pytest
import respx

from approval_bridge.inject_client import InjectClient


@pytest.mark.asyncio
async def test_inject_card_posts_correct_payload():
    client = InjectClient("http://127.0.0.1:8765")

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("http://127.0.0.1:8765/internal/sessions/sess-1/inject-card").mock(
            return_value=httpx.Response(202, json={"accepted": True, "messageId": "m-1"})
        )
        await client.inject_card(
            session_id="sess-1",
            question_id="nrmact-act_xyz",
            title="Pending action",
            question="Kill PID 1234",
            options=[
                {"label": "Approve", "selectedLabel": "✅ Approved", "value": "approve"},
                {"label": "Reject", "selectedLabel": "❌ Rejected", "value": "reject"},
            ],
        )
        assert route.called
        sent = route.calls.last.request
        import json as _json
        body = _json.loads(sent.content)
        assert body == {
            "questionId": "nrmact-act_xyz",
            "title": "Pending action",
            "question": "Kill PID 1234",
            "options": [
                {"label": "Approve", "selectedLabel": "✅ Approved", "value": "approve"},
                {"label": "Reject", "selectedLabel": "❌ Rejected", "value": "reject"},
            ],
        }


@pytest.mark.asyncio
async def test_inject_card_raises_on_non_2xx():
    client = InjectClient("http://127.0.0.1:8765")
    with respx.mock() as mock:
        mock.post("http://127.0.0.1:8765/internal/sessions/bad/inject-card").mock(
            return_value=httpx.Response(404, json={"error": "unknown session"})
        )
        with pytest.raises(httpx.HTTPStatusError):
            await client.inject_card(
                session_id="bad", question_id="x", title="t", question="q", options=[]
            )


@pytest.mark.asyncio
async def test_inject_card_propagates_network_error():
    client = InjectClient("http://127.0.0.1:8765", timeout=0.1)
    with respx.mock() as mock:
        mock.post("http://127.0.0.1:8765/internal/sessions/sess-1/inject-card").mock(
            side_effect=httpx.ConnectError("nope")
        )
        with pytest.raises(httpx.ConnectError):
            await client.inject_card(
                session_id="sess-1", question_id="x", title="t", question="q", options=[]
            )


def test_base_url_trailing_slash_is_normalized():
    c = InjectClient("http://127.0.0.1:8765/")
    assert c._base == "http://127.0.0.1:8765"
