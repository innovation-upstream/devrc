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

🔴 THE TOKEN IS NOT READ HERE (clawgate task #307). It comes from
`scripts/lib/clawgate_env.resolve_hook_token`, the ONE resolver both producers
share, which applies `clawgatectl`'s precedence — `~/.claude/clawgate.env`, then
the process environment. This module used to read `os.environ` alone; the token
is not in the environment on this host, so every card was skipped in SILENCE.
The no-op is still graceful (D3) — it is just no longer inaudible.
"""
from __future__ import annotations

import os
import sys

ENDPOINT = "http://192.168.50.250:30302/api/tasks"

# clawgate renders `directory` as the card title; trim to a sane label length.
TITLE_MAX = 120

#: Cached shared-resolver module. Loaded LAZILY (see `_clawgate_env`).
_ENV_LIB = None


def _clawgate_env():
    """Load `scripts/lib/clawgate_env.py` by EXPLICIT PATH — the ONE token resolver.

    Explicit path, not a `sys.path` import: this module is imported as a flat
    sibling (`import clawgate` with `scripts/signal/` as cwd/sys.path[0]), so
    there is no package to hang a relative import off, and `scripts/lib/` holds
    unrelated modules that must not be able to shadow anything by name. Resolved
    relative to THIS file first — `$DEVRC_DIR` is wrong inside a worktree and
    absent in the nix sandbox — with `$DEVRC_DIR` only as the fallback for a
    copy deployed away from its sibling `lib/`. Same recipe as
    `scripts/session-manager::_load_clawgate_tasks`.

    🔴 LAZY, AND THE LAZINESS IS LOAD-BEARING. `consumer.py`'s `draft` branch
    does `import clawgate` BEFORE `db.draft_message(...)`, so a module-scope
    `raise ImportError` here would abort the CLI *before the draft was stored* —
    turning a notification defect into a record-loss defect, which is precisely
    the trade D3 forbids. Raising from inside `emit_draft_task` instead lets the
    caller keep its record and lose only the card.

    The image carries this file (`Dockerfile`, `Dockerfile.dockerignore`, and
    `tests/test_image_deps.py` pin the COPY both ways), so a pod that somehow
    reached this code would find it.
    """
    global _ENV_LIB
    if _ENV_LIB is not None:
        return _ENV_LIB
    import importlib.machinery
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    devrc = os.environ.get("DEVRC_DIR") or os.path.join(
        os.path.expanduser("~"), "workspace", "devrc")
    for path in (os.path.join(here, os.pardir, "lib", "clawgate_env.py"),
                 os.path.join(devrc, "scripts", "lib", "clawgate_env.py")):
        if os.path.exists(path):
            loader = importlib.machinery.SourceFileLoader("_signal_clawgate_env",
                                                          path)
            spec = importlib.util.spec_from_file_location(
                "_signal_clawgate_env", path, loader=loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
            _ENV_LIB = mod
            return mod
    raise ImportError("scripts/lib/clawgate_env.py not found (tried the sibling "
                      "lib/ and $DEVRC_DIR/scripts/lib/)")
# How much of the draft body goes on the card. The full text is in Postgres.
BODY_PREVIEW_MAX = 800


def build_draft_payload(*, draft_id: int, recipient: str, body: str) -> dict:
    """Build the `POST /api/tasks` JSON body for one pending Signal draft.

    Pure + side-effect-free so it can be asserted in a unit test. Carries the
    approval command the operator runs, so the card is actionable on its own.
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
    lines = [
        f"To: {recipient}",
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
                    timeout: float = 10.0) -> bool:
    """Post one clawgate Task card for a pending draft. True if posted.

    Graceful no-op (returns False, posts nothing) when no `CLAWGATE_HOOK_TOKEN`
    can be resolved from `~/.claude/clawgate.env` or the process environment —
    mirroring `mail-actions/clawgate.py`, which shares the resolver.

    🔴 THE NO-OP IS NOW AUDIBLE. The caller has already stored the draft, so a
    False here costs a notification and nothing else (D3) — but it writes one
    line to stderr saying so, because the silent version is how a real draft came
    to sit in Postgres with no card and no trace of the skip.
    """
    what = "the clawgate card for signal draft #%s" % draft_id
    try:
        token = _clawgate_env().resolve_hook_token(what)
    except ImportError as exc:
        # The shared resolver is missing. One line, then degrade — never take
        # the (already stored) draft down over a notifier's plumbing.
        print("clawgate: %s — SKIPPED %s (the draft itself was stored)."
              % (exc, what), file=sys.stderr)
        return False
    if not token:
        return False
    import requests

    payload = build_draft_payload(draft_id=draft_id, recipient=recipient, body=body)
    resp = requests.post(
        ENDPOINT,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    return True
