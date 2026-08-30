#!/usr/bin/env python3
"""Stage 4 (optional) — surface a NEW action item as a clawgate Task card.

Thin + swappable. POSTs a decision-shaped card to the clawgate hook-token endpoint.
If no CLAWGATE_HOOK_TOKEN can be resolved, `emit_task` is a graceful no-op
(returns False) — and says so on stderr.

🔴 THE TOKEN IS NOT READ HERE (clawgate task #307). It comes from
`scripts/lib/clawgate_env.resolve_hook_token`, the ONE resolver this and
`scripts/signal/clawgate.py` share, applying `clawgatectl`'s precedence —
`~/.claude/clawgate.env`, then the process environment. Both files used to read
`os.environ` alone, and the token is not in the environment on this host, so both
skipped every card in SILENCE. Fixing one would have left the other failing
identically and unnoticed, which is why there is one resolver and not two fixes.

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
import sys

ENDPOINT = "http://192.168.50.250:30302/api/tasks"

# clawgate renders `directory` as the Task card's title; trim to a sane label length.
TITLE_MAX = 120

#: Cached shared-resolver module. Loaded LAZILY (see `_clawgate_env`).
_ENV_LIB = None


def _clawgate_env():
    """Load `scripts/lib/clawgate_env.py` by EXPLICIT PATH — the ONE token resolver.

    Explicit path, not a `sys.path` import: this module is imported as a flat
    sibling (`from clawgate import emit_task`, with `scripts/mail-actions/` on
    `sys.path`), so there is no package to hang a relative import off, and
    `scripts/lib/` holds unrelated modules that must not be able to shadow
    anything by name. Resolved relative to THIS file first — `$DEVRC_DIR` is
    wrong inside a worktree and absent in the nix sandbox — with `$DEVRC_DIR`
    only as the fallback for a copy deployed away from its sibling `lib/`. Same
    recipe as `scripts/session-manager::_load_clawgate_tasks`, and byte-for-byte
    the same as `scripts/signal/clawgate.py`'s, which is the point.

    🔴 LAZY, so a missing resolver costs the CARD and not the RECORD. `extract.py`
    already stores the action item before calling `_emit_clawgate`, and this
    keeps the same property in `scripts/signal/clawgate.py`, where the ordering
    is tighter — see that file's copy of this docstring.
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
            loader = importlib.machinery.SourceFileLoader("_mail_clawgate_env",
                                                          path)
            spec = importlib.util.spec_from_file_location(
                "_mail_clawgate_env", path, loader=loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
            _ENV_LIB = mod
            return mod
    raise ImportError("scripts/lib/clawgate_env.py not found (tried the sibling "
                      "lib/ and $DEVRC_DIR/scripts/lib/)")


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
    """Emit one clawgate Task card for an action item. Returns True if posted.

    Graceful no-op (returns False, posts nothing) when no `CLAWGATE_HOOK_TOKEN`
    can be resolved from `~/.claude/clawgate.env` or the process environment.
    🔴 The no-op is now AUDIBLE — one stderr line naming the skip and where it
    looked. The action item is already in Postgres by the time this runs, so a
    False costs a notification and nothing else.
    """
    what = "the clawgate card for %s" % source_ref
    try:
        token = _clawgate_env().resolve_hook_token(what)
    except ImportError as exc:
        # The shared resolver is missing. One line, then degrade — never take
        # the (already stored) action item down over a notifier's plumbing.
        print("clawgate: %s — SKIPPED %s (the action item itself was stored)."
              % (exc, what), file=sys.stderr)
        return False
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
