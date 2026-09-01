#!/usr/bin/env python3
"""Surface an outbound Signal DRAFT as a clawgate Task card (decision D3).

A clone of `scripts/mail-actions/clawgate.py`, including its graceful no-op:
with `CLAWGATE_HOOK_TOKEN` unset, `emit_draft_task` posts nothing and returns
False rather than raising — the draft is already durably stored, so a missing
token degrades notification, never the record.

Contract note (inherited, verified in mail-actions): clawgate's
`POST /api/tasks` handler decodes ONLY `directory, body, model, repo, branch,
privileges`. There is NO `title` field — a `title` key is silently dropped, and
the card's DISPLAY title is `directory`. So the human-readable label goes in
`directory`.

🔴 THIS MODULE CANNOT SEND A SIGNAL MESSAGE. It notifies a human that a draft is
waiting. Transmission happens only in `consumer.transmit_approved()`, which
demands a capability minted from an APPROVED draft row.
"""
from __future__ import annotations

import os

import _mentions

ENDPOINT = "http://192.168.50.250:30302/api/tasks"

# clawgate renders `directory` as the card title; trim to a sane label length.
TITLE_MAX = 120
# How much of the draft body goes on the card. The full text is in Postgres.
BODY_PREVIEW_MAX = 800


def build_draft_payload(*, draft_id: int, recipient: str, body: str,
                        mentions: list | None = None,
                        author_names: dict | None = None) -> dict:
    """Build the `POST /api/tasks` JSON body for one pending Signal draft.

    Pure + side-effect-free so it can be asserted in a unit test.

    🔴 THE CARD MUST NAME WHO GETS PINGED. A mention is not visible in the body
    preview as anything but the plain text `@Ann` — which the operator's eye
    reads as ordinary prose, because that is exactly what it is until the
    `mentions` array turns it into a notification that bypasses the recipient's
    mute settings and names them to the whole group. Approving a message without
    seeing who it notifies is the failure mode this line exists to close, so the
    resolved `author` ids go on the card explicitly, above the body.

    🔴 AND THE ID ALONE IS NOT ENOUGH. `author` is usually a bare uuid — five of
    the seven members of the real group are uuid-only — and a human cannot check
    a uuid against anything. `author_names` (`{author: display name}`, built by
    `_mentions.author_names()` from the rows the resolver matched) puts the NAME
    on the line beside the id, which is the question the card is answering.
    """
    preview = (body or "").strip()
    if len(preview) > BODY_PREVIEW_MAX:
        preview = preview[:BODY_PREVIEW_MAX] + "…"
    # 🔴 The card deliberately does NOT print the `approve` command. An earlier
    # revision did, which handed the drafting agent — the one that just posted
    # this card — the exact incantation to approve its own draft. The card's job
    # is to tell a HUMAN a draft is waiting; approval is an operator step run
    # from an operator shell (it needs `SIGNAL_APPROVAL_TOKEN`, which no agent
    # environment carries).
    lines = [f"To: {recipient}"]
    if mentions:
        lines.append(f"⚠ PINGS {len(mentions)} member(s) — this notifies them "
                     f"THROUGH their mute settings:")
        for who in _mentions.describe_mentions(mentions, author_names):
            lines.append(f"  · {who}")
    lines += [
        "",
        preview,
        "",
        f"Waiting for your approval — draft #{draft_id}.",
        "Approve from your own shell; see the `signal` skill.",
    ]
    return {
        "directory": f"\U0001F5E8 signal draft #{draft_id} · {recipient}"[:TITLE_MAX],
        "body": "\n".join(lines),
    }


def emit_draft_task(*, draft_id: int, recipient: str, body: str,
                    mentions: list | None = None,
                    author_names: dict | None = None,
                    timeout: float = 10.0) -> bool:
    """Post one clawgate Task card for a pending draft. True if posted.

    Graceful no-op (returns False, posts nothing) when `CLAWGATE_HOOK_TOKEN` is
    unset — mirroring `mail-actions/clawgate.py`.
    """
    token = os.environ.get("CLAWGATE_HOOK_TOKEN")
    if not token:
        return False
    import requests

    payload = build_draft_payload(draft_id=draft_id, recipient=recipient,
                                  body=body, mentions=mentions,
                                  author_names=author_names)
    resp = requests.post(
        ENDPOINT,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    return True
