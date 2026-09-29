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

🔴 THE BASE URL IS CONFIGURATION, NOT A LITERAL (fixed 2026-09-29). This module
used to hold `ENDPOINT = "http://192.168.50.250:30302/api/tasks"` and read
nothing from the environment but the token, so it could not follow the task
service's split out of the permission router: whichever host the literal named
was the host it kept posting to. That host is the ROUTER, and the router now
answers `POST /api/tasks` with **404** — measured live 2026-09-29 — so every
card this module posted was silently lost.

Its mail-actions twin was fixed in #1878; this one was deferred there on the
grounds that "the deployed pod never runs `draft`". That is true of the POD and
false of the DEFECT: `consumer.py draft` is an OPERATOR command run from the
workbench CLI, where ~/.claude/clawgate.env exists and names the task service.
The `draft` subcommand is also in the image's own subcommand ledger
(`build-push.sh` control 3), so a `kubectl exec … draft` reaches this code too.

🔴 THE PRECEDENCE IS NOT RE-SPELLED HERE. It is
`scripts/lib/clawgate_tasks.task_base_url`, loaded by explicit path below, so
this producer, its mail-actions twin, the board pollers and the write-back guard
cannot disagree about where the task service is.

🔴 AND THE IMAGE MUST CARRY THAT MODULE. `scripts/signal/Dockerfile` COPYs by
name (devrc is public; `COPY . .` bakes a working tree into a pushed layer), so
a module loaded at runtime from outside `scripts/signal/` is an ImportError in
the container unless the COPY list names it. `SHARED_TASKS_MODULE` below is that
name, and `tests/test_image_deps.py` pins it against the Dockerfile and the
dockerignore allowlist in both directions — an ImportError this module cannot
swallow is exactly the failure that gets discovered in production otherwise.

🔴 THIS MODULE CANNOT SEND A SIGNAL MESSAGE. It notifies a human that a draft is
waiting. Transmission happens only in `consumer.transmit_approved()`, which
demands a capability minted from an APPROVED draft row.
"""
from __future__ import annotations

import os

import _mentions

#: 🔴 THE SHARED MODULE THIS PRODUCER LOADS AT RUNTIME, repo-root-relative and
#: spelled ONCE. Both the loader below and the image guard in
#: `tests/test_image_deps.py` read this constant, so "what the code needs" and
#: "what the image ships" are one statement rather than two that can drift.
SHARED_TASKS_MODULE = "scripts/lib/clawgate_tasks.py"

#: Where scripts/lib/ is looked for when this module does not run beside it.
#: Same idiom (and the same variable) as `scripts/bar-status-poll` and
#: `scripts/mail-actions/clawgate.py`. In the container the SIBLING path wins —
#: the Dockerfile reproduces the repo layout under /app — and this is the
#: off-cluster CLI's fallback.
DEVRC_DIR = os.environ.get("DEVRC_DIR") or os.path.expanduser("~/workspace/devrc")

#: The POST path for creating a Task card. Deliberately NOT the shared module's
#: `TASKS_PATH`, which is the BOARD READ (`/api/tasks?summary=1`) — a query
#: string that belongs on a GET and must not ride along on this POST.
TASKS_PATH = "/api/tasks"

_CG = None


def _load_clawgate_tasks():
    """Load `SHARED_TASKS_MODULE` by EXPLICIT PATH, memoised.

    Never via `sys.path`: this module's own directory is already on it (the
    signal modules are siblings, not a package, and the image puts `/app/scripts/
    signal` on the path), so a sys.path import could be shadowed by any
    same-named file that lands there.

    🔴 NO FALLBACK COPY OF THE PRECEDENCE, deliberately. A local
    `os.environ.get("CLAWGATE_TASK_API_URL", ...)` used as a "safety net" is how
    this class of bug survives: two copies, and the silent one is stale. If the
    shared module cannot be loaded this raises — and `emit_draft_task`'s caller
    (`consumer.py draft`) lets that surface, because a card posted at a guessed
    host is worse than a named miss.
    """
    global _CG
    if _CG is not None:
        return _CG
    import importlib.machinery
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    tail = SHARED_TASKS_MODULE.split("/")[1:]        # drop the "scripts" root
    for path in (os.path.join(here, os.pardir, *tail),
                 os.path.join(DEVRC_DIR, *SHARED_TASKS_MODULE.split("/"))):
        if os.path.exists(path):
            loader = importlib.machinery.SourceFileLoader(
                "_signal_clawgate_tasks", path)
            spec = importlib.util.spec_from_file_location(
                "_signal_clawgate_tasks", path, loader=loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
            _CG = mod
            return _CG
    raise ImportError(
        "%s not found (tried the sibling ../lib/ the image COPYs it to, and "
        "$DEVRC_DIR). The Task card's base URL is resolved from it and is NOT "
        "re-spelled here." % SHARED_TASKS_MODULE)


def task_endpoint(env=None, path=None) -> str:
    """The `POST /api/tasks` URL for the TASK service, resolved at CALL TIME.

    Both arguments are pass-through to the shared resolver: `env` is the
    override layer (the process environment by default) and `path` the env file
    (~/.claude/clawgate.env by default), so a test can drive BOTH layers without
    touching the process environment or the real file.
    """
    return _load_clawgate_tasks().task_base_url(env, path) + TASKS_PATH

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

    The endpoint is resolved AFTER the token check, so the no-token no-op stays
    exactly what it was: the shared module is not loaded, ~/.claude/clawgate.env
    is not opened, nothing is posted.
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
