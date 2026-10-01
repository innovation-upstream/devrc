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

🔴 AND THE TOKEN GOES THROUGH THE SAME TWO LAYERS (fixed, task #307). This
paragraph used to read "THE TOKEN IS STILL TAKEN FROM `os.environ` ONLY, and
that asymmetry is known rather than overlooked … the subject of its own open PR
(`fix/307-clawgate-token-resolver`)". The asymmetry was the whole remaining
defect: on this host the token is in ~/.claude/clawgate.env and nothing exports
it, so the module resolved the right base URL and then skipped every card in
silence for want of a credential it was not looking in the right place for. The
token now comes from `scripts/lib/clawgate_tasks.hook_token` — the SAME file,
the SAME layering, ONE definition shared with the base URL.
"""
from __future__ import annotations

import os
import sys

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


def hook_token(env=None, path=None):
    """The clawgate hook token, or `None`. Resolved at CALL TIME, SHARED rule.

    🔴 NOT `os.environ.get("CLAWGATE_HOOK_TOKEN")`, which is what this line was
    until now. `--emit-clawgate` is manual/on-demand, no unit exports any
    `CLAWGATE_*`, and the host it runs on keeps the token in
    ~/.claude/clawgate.env — so an environment-only read resolved `None` on
    exactly the host that has the credential, and every card was skipped in
    SILENCE. Same direction, same file, same fix as the base URL above: the
    precedence is `scripts/lib/clawgate_tasks.hook_token` and is NOT re-spelled
    here.

    Both arguments are pass-through, as for `task_endpoint`.
    """
    return _load_clawgate_tasks().hook_token(env, path)


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
              source_ref: str, timeout: float = 10.0,
              env=None, path=None) -> bool:
    """Emit one clawgate Task card for an action item. Returns True if posted.

    🔴 GRACEFUL NO-OP, RECORD INTACT. With no token resolvable from EITHER
    source this posts nothing, names the miss on stderr and returns False. It
    never raises on that path: the mail row and its extraction are already
    stored, and `extract.py` counts the card as not-emitted rather than failing
    the run — a missing token degrades NOTIFICATION, never the record.

    🔴 THE WARNING IS NOT OPTIONAL. The no-token path used to `return False` in
    total silence, so `clawgate_emitted: 0` in the run summary was
    indistinguishable from "there was nothing to emit". One line naming the
    variable and the file it looked in is the whole remedy.

    ⚠ THE SHARED MODULE IS NOW LOADED ON THE NO-TOKEN PATH TOO, and that is a
    deliberate change. It could previously be skipped because the token came
    from `os.environ`; the token lives in the file this module reads, so "is
    there a token" can no longer be answered without it. An unloadable shared
    module therefore still RAISES here rather than degrading (the
    NO-FALLBACK-COPY policy in `_load_clawgate_tasks`) — on both paths now, not
    just the posting one — and `_emit_clawgate` in extract.py already turns that
    into a named stderr line plus a skipped card.

    `env` / `path` are pass-through to the resolvers, so a test can drive both
    configuration layers without touching the process environment or the real
    ~/.claude/clawgate.env.
    """
    token = hook_token(env, path)
    if not token:
        cg = _load_clawgate_tasks()
        # 🔴 NAMES THE VARIABLE AND THE PATH, NEVER A VALUE.
        print("clawgate: no %s in %s or the process environment — action-item "
              "card NOT posted for %s (the extraction itself is stored)"
              % (cg.HOOK_TOKEN_VAR, cg.env_file_path(path), source_ref),
              file=sys.stderr)
        return False
    import requests

    url = task_endpoint(env, path)
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
