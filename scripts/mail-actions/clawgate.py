#!/usr/bin/env python3
"""Stage 4 (optional) — surface a NEW action item as a clawgate Task card.

Thin + swappable. POSTs a decision-shaped card to the clawgate hook-token endpoint.
If CLAWGATE_HOOK_TOKEN is unset, `emit_task` is a graceful no-op (returns False).

Contract note (fixed 2026-07-24): clawgate's `POST /api/tasks` handler
(`homelab-talos/containers/clawgate/internal/api/notes.go` `handleAPITaskCreate`)
decodes ONLY these JSON fields: `directory, body, model, repo, branch, privileges`.
There is NO `title` field — a `title` key is silently dropped by Go's JSON decoder.
The card's DISPLAY title is `directory` (`internal/ui/notes.go` `noteDispatchButton`:
`label := n.Directory`, falling back to the first body line when empty). This module
previously sent `{"title", "body"}`, so the title was dropped AND `directory` was
empty → the card showed only the truncated first body line. We now send the title
text as `directory` (mirroring `repo-cos/clawgate.py`, which already gets this right).
"""
from __future__ import annotations

import os

ENDPOINT = "http://192.168.50.250:30302/api/tasks"

# clawgate renders `directory` as the Task card's title; trim to a sane label length.
TITLE_MAX = 120


def build_task_payload(*, who: str, ask: str, deadline: str | None,
                       amount: str | None, source_ref: str) -> dict:
    """Build the `POST /api/tasks` JSON body for one action item.

    Pure + side-effect-free so it can be asserted in a unit test. The action's title
    goes in `directory` (clawgate's card-title field — NOT `title`, which the server
    ignores); the ask/deadline/amount/source lines go in `body`.
    """
    bits = [ask.strip()]
    if deadline:
        bits.append(f"Deadline: {deadline}")
    if amount:
        bits.append(f"Amount: {amount}")
    bits.append(f"Source: {source_ref}")
    return {
        "directory": f"\U0001F4E8 action-required · {who}"[:TITLE_MAX],
        "body": "\n".join(b for b in bits if b),
    }


def emit_task(*, who: str, ask: str, deadline: str | None, amount: str | None,
              source_ref: str, timeout: float = 10.0) -> bool:
    """Emit one clawgate Task card for an action item. Returns True if posted."""
    token = os.environ.get("CLAWGATE_HOOK_TOKEN")
    if not token:
        return False
    import requests

    body = build_task_payload(
        who=who, ask=ask, deadline=deadline, amount=amount, source_ref=source_ref,
    )
    resp = requests.post(
        ENDPOINT,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    resp.raise_for_status()
    return True
