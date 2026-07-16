from __future__ import annotations

import logging

import httpx

from .models import Alert

log = logging.getLogger("tripwire")

_EMOJI = {"critical": "🚨", "warning": "⚠️"}


def format_alert(alert: Alert) -> str:
    emoji = _EMOJI.get(alert.severity, "ℹ️")
    return f"{emoji} *[tripwire:{alert.rule}]* {alert.title}\n{alert.detail}"


def post_alert(webhook: str, alert: Alert, timeout: float = 10.0) -> bool:
    try:
        resp = httpx.post(webhook, json={"text": format_alert(alert)}, timeout=timeout)
        if resp.status_code == 200:
            return True
        log.error("slack webhook returned %s: %s", resp.status_code, resp.text[:200])
    except httpx.HTTPError as exc:
        log.error("slack webhook post failed: %s", exc)
    return False


def post_text(webhook: str, text: str, timeout: float = 10.0) -> bool:
    try:
        return httpx.post(webhook, json={"text": text}, timeout=timeout).status_code == 200
    except httpx.HTTPError as exc:
        log.error("slack webhook post failed: %s", exc)
        return False
