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

🔴 THE BASE URL IS CONFIGURATION, NOT A LITERAL (fixed 2026-09-25). This module
used to hold `ENDPOINT = "http://<host>:<port>/api/tasks"`, so it could not
follow the task service's split out of the permission router — whichever host
the literal named was the host it kept posting to.

🔴 AND THE PRECEDENCE BELOW IS A DELIBERATE SECOND COPY. Say so loudly, because
a silent one is how this class of bug regenerates: the shared definition lives
in the repo's lib directory (loaded by explicit path by the board pollers and by
the mail-actions twin of this module), and THIS module cannot reach it. It runs
from an image whose Dockerfile COPYs the five files of this directory BY NAME —
`COPY . .` is refused on purpose, devrc is public — and whose dockerignore
denies `**` and re-admits only those names. Two build-time controls assert the
image's file set EXACTLY, in both directions, and a gate test pins the COPY list
against this directory's contents. So there is no import that works here without
widening a deliberately narrow, security-motivated allowlist to carry a
six-hundred-line module for three lines of it.

⚠ THAT WIDENING IS POSSIBLE, AND SOMEBODY HAS PROPOSED IT — say so rather than
leave a future reader thinking the door is locked. An open PR
(`fix/307-clawgate-token-resolver`, #655) ships a DIFFERENT shared module into
this image for the hook token, by adding one COPY line, one dockerignore entry
and a pin in tests/test_image_deps.py. The judgement here is a trade, not an
impossibility: three lines of precedence did not seem worth a fourth ledger
plus a six-hundred-line module in a pod that only ever runs the ingest loop. If
that PR lands, replacing this copy with an import is the obvious follow-up and
the guard below is what will tell you the copy is still here.

The copy is therefore pinned MECHANICALLY rather than by good intentions:
scripts/tests/test_clawgate_task_base_url_single_source.py asserts this module's
ledger, its default and its resolution BEHAVIOUR against the shared module's,
and fails the moment the two disagree.
"""
from __future__ import annotations

import os

import _mentions

# --------------------------------------------------------------------------- #
# 🔴 A COPY OF THE SHARED TASK-URL PRECEDENCE. See the module docstring for why
# it cannot be an import, and for the guard that keeps it honest. Change nothing
# here without changing the shared definition; the guard fails either way round.
# --------------------------------------------------------------------------- #
#: Specific key first, general key as the FALLBACK. Order is the contract: swap
#: these and a host that only knows `CLAWGATE_API_URL` keeps working while one
#: told about the split silently un-splits the two services again.
TASK_API_URL_VARS = ("CLAWGATE_TASK_API_URL", "CLAWGATE_API_URL")

#: What both keys being unset resolves to — the LAN NodePort this pod reached
#: when the URL was a literal, so introducing the lookup moves no request.
DEFAULT_API_URL = "http://192.168.50.250:30302"

#: The POST path for creating a Task card (NOT the board read's `?summary=1`).
TASKS_PATH = "/api/tasks"


def task_base_url(env=None) -> str:
    """First non-empty value among `TASK_API_URL_VARS`, else the default.

    An EMPTY value counts as unset (matching the shell's `${A:-$B}`), so a
    half-written `CLAWGATE_TASK_API_URL=` falls through to the router key rather
    than resolving to a bare path. Trailing slashes are stripped so the caller
    can concatenate a path without doubling the separator.
    """
    src = os.environ if env is None else env
    for name in TASK_API_URL_VARS:
        value = src.get(name)
        if value:
            return value.rstrip("/")
    return DEFAULT_API_URL.rstrip("/")


def task_endpoint(env=None) -> str:
    """The `POST /api/tasks` URL for the TASK service, resolved at CALL TIME."""
    return task_base_url(env) + TASKS_PATH


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
    unset — mirroring `mail-actions/clawgate.py`. The endpoint is resolved AFTER
    the token check, so that no-op reads nothing at all.
    """
    token = os.environ.get("CLAWGATE_HOOK_TOKEN")
    if not token:
        return False
    import requests

    url = task_endpoint()
    payload = build_draft_payload(draft_id=draft_id, recipient=recipient,
                                  body=body, mentions=mentions,
                                  author_names=author_names)
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    return True
