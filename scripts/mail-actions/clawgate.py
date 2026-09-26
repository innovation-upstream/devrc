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

🔴 THE BASE URL IS CONFIGURATION, NOT A LITERAL (fixed 2026-09-25). This module
used to hold `ENDPOINT = "http://<host>:<port>/api/tasks"` and read nothing but
the token from the environment, so it could not follow the task service's split
out of the permission router: whichever host the literal named was the host it
kept posting to. The precedence is NOT re-spelled here — it is
`scripts/lib/clawgate_tasks.task_base_url`, loaded by explicit path below, so
this producer and the board pollers cannot disagree about where the task service
is.

🔴 AND IT IS RESOLVED FROM ~/.claude/clawgate.env, not from `os.environ` alone.
An earlier revision of this fix read only the process environment, justified by
"a systemd unit's `Environment=`". That was measured FALSE: `--emit-clawgate` is
manual/on-demand (`claude/skills/mailbox/SKILL.md`), no unit sets any
`CLAWGATE_*`, and the host this runs on DOES have ~/.claude/clawgate.env
carrying both the token and the task base. So it resolved through the one
channel that carries nothing. `task_base_url` now layers the file under the
process environment; see its docstring for the five-step precedence.

⚠ THE TOKEN IS STILL TAKEN FROM `os.environ` ONLY, and that asymmetry is known
rather than overlooked. It is the subject of its own open PR
(`fix/307-clawgate-token-resolver`) and is deliberately not folded in here:
until it lands, this module is a graceful no-op unless the operator's shell
carries `CLAWGATE_HOOK_TOKEN` — and when it does, the base URL it posts at is
now the configured one rather than a guess.
"""
from __future__ import annotations

import os

#: Where scripts/lib/ is looked for when this module does not run beside it.
#: Same idiom (and the same variable) as scripts/bar-status-poll.
DEVRC_DIR = os.environ.get("DEVRC_DIR") or os.path.expanduser("~/workspace/devrc")

#: The POST path for creating a Task card. Deliberately NOT the shared module's
#: `TASKS_PATH`, which is the BOARD READ (`/api/tasks?summary=1`) — a query
#: string that belongs on a GET and must not ride along on this POST.
TASKS_PATH = "/api/tasks"

_CG = None


def _load_clawgate_tasks():
    """Load scripts/lib/clawgate_tasks.py by EXPLICIT PATH, memoised.

    Never via `sys.path`: this module's own directory is already on it (the
    mail-actions modules are siblings, not a package), so a sys.path import could
    be shadowed by any same-named file that lands there.

    🔴 NO FALLBACK COPY OF THE PRECEDENCE, deliberately. A local
    `os.environ.get("CLAWGATE_TASK_API_URL", ...)` used as a "safety net" is how
    this class of bug survives: two copies, and the silent one is stale. If the
    shared module cannot be loaded this raises, and `_emit_clawgate` in
    extract.py already turns any exception into a named stderr line plus a
    skipped card — an honest miss rather than a card posted at a guessed host.
    """
    global _CG
    if _CG is not None:
        return _CG
    import importlib.machinery
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, os.pardir, "lib", "clawgate_tasks.py"),
                 os.path.join(DEVRC_DIR, "scripts", "lib", "clawgate_tasks.py")):
        if os.path.exists(path):
            loader = importlib.machinery.SourceFileLoader("_mail_clawgate_tasks",
                                                          path)
            spec = importlib.util.spec_from_file_location(
                "_mail_clawgate_tasks", path, loader=loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
            _CG = mod
            return _CG
    raise ImportError("scripts/lib/clawgate_tasks.py not found (tried sibling "
                      "../lib/ and $DEVRC_DIR/scripts/lib/)")


def task_endpoint(env=None, path=None) -> str:
    """The `POST /api/tasks` URL for the TASK service, resolved at CALL TIME.

    Both arguments are pass-through to the shared resolver: `env` is the
    override layer (the process environment by default) and `path` the env file
    (~/.claude/clawgate.env by default), so a test can drive BOTH layers without
    touching the process environment or the real file.
    """
    return _load_clawgate_tasks().task_base_url(env, path) + TASKS_PATH


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
    """Emit one clawgate Task card for an action item. Returns True if posted.

    The endpoint is resolved AFTER the token check, so the no-token no-op stays
    exactly what it was: the shared module is not loaded, ~/.claude/clawgate.env
    is not opened, nothing is posted.
    """
    token = os.environ.get("CLAWGATE_HOOK_TOKEN")
    if not token:
        return False
    import requests

    url = task_endpoint()
    body = build_task_payload(
        who=who, ask=ask, deadline=deadline, amount=amount, source_ref=source_ref,
    )
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    resp.raise_for_status()
    return True
