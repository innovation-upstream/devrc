#!/usr/bin/env python3
"""Stage 4 (optional) — surface a NEW action item as a clawgate Task card.

Thin + swappable. POSTs a decision-shaped card to the clawgate hook-token endpoint.
If CLAWGATE_HOOK_TOKEN is unset, `emit_task` is a graceful no-op (returns False).
"""
from __future__ import annotations

import os

ENDPOINT = "http://192.168.50.250:30302/api/tasks"


def emit_task(*, who: str, ask: str, deadline: str | None, amount: str | None,
              source_ref: str, timeout: float = 10.0) -> bool:
    """Emit one clawgate Task card for an action item. Returns True if posted."""
    token = os.environ.get("CLAWGATE_HOOK_TOKEN")
    if not token:
        return False
    import requests

    bits = [ask.strip()]
    if deadline:
        bits.append(f"Deadline: {deadline}")
    if amount:
        bits.append(f"Amount: {amount}")
    bits.append(f"Source: {source_ref}")
    body = {
        "title": f"\U0001F4E8 action-required · {who}"[:120],
        "body": "\n".join(b for b in bits if b),
    }
    resp = requests.post(
        ENDPOINT,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    resp.raise_for_status()
    return True
