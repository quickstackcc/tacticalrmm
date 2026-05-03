"""HTTP client that posts approval cards into nanoclaw's internal endpoint.

Used by the Dispatcher's HUMAN_APPROVAL branch: after gating an action and
recording the audit row, the dispatcher tells nanoclaw to post a Slack card
into the originating session's outbound queue. nanoclaw's existing chat-sdk
delivery path then renders the card and handles button clicks.
"""

from typing import Any

import httpx


class InjectClient:
    """Single-purpose HTTP client for /internal/sessions/<sid>/inject-card.

    No retries: failure raises and the dispatcher surfaces it to the agent.
    """

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    async def inject_card(
        self,
        *,
        session_id: str,
        question_id: str,
        title: str,
        question: str,
        options: list[dict[str, Any]],
    ) -> None:
        url = f"{self._base}/internal/sessions/{session_id}/inject-card"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                url,
                json={
                    "questionId": question_id,
                    "title": title,
                    "question": question,
                    "options": options,
                },
            )
            r.raise_for_status()
