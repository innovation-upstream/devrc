#!/usr/bin/env python3
"""Claude Code adapter for the backgrounded-command capture log — ClickUp 868ktvqf9.

All the logic lives in `bg_command_capture.py`; read ITS docstring first, which
carries the measured harness payload shapes and the reasoning. This file is the
adapter and nothing more, in the arrangement `bash-guard.py` has with
`guard_core.py` and `agent-ledger-hook.py` has with `agent_ledger.py`: deployed,
both files sit in `~/.claude/hooks/`; in the repo the module is in
`scripts/lib/` and only this file is under `scripts/claude-hooks/`.

WHICH EVENTS, AND WHY EACH ONE

    PreToolUse(Bash)   🔴 THE LOAD-BEARING ONE. `tool_input.command` is the
                       verbatim string and `tool_input.run_in_background` is the
                       boolean, and this is the ONLY event where either exists.
                       Everything the ticket needs is captured here.
    PostToolUse(Bash)  the `backgroundTaskId` — which names the output file the
                       operator finds at 0 bytes — appears only in
                       `tool_response`, and only on this event. It carries NO
                       exit code, because it fires at LAUNCH, not completion.

There is deliberately no third event: no hook fires when a backgrounded task
finishes. The exit code the harness announces is injected into the transcript as
`<summary>Background command "…" completed (exit code N)</summary>`, joined back
to a captured command by its `description` — that join is `--report`, run by an
investigator, not by a hook on anyone's hot path.

🔴 FAIL-OPEN, ALWAYS, AND SILENTLY. This fires before and after EVERY Bash call
in EVERY session on this host. A hook that raises breaks the operator's shell,
and a PreToolUse hook that writes junk to stdout is read as a permission verdict.
So: every path returns 0, nothing is ever printed, and the outermost handler is
`BaseException` — not `Exception`. That widening is not defensive decoration; it
is the measured lesson from `bash-guard.py`, where a `SystemExit` out of an
imported module escaped an `except Exception` and changed the hook's verdict.
Here the direction is the opposite one (this hook has no verdict to change) but
the mechanism is identical, and a traceback on stderr for every Bash call would
be its own kind of broken.

This hook is INSTRUMENTATION. It never blocks, warns, rewrites or refuses
anything. Turn it off with `CLAUDE_BG_CAPTURE_DISABLE=1`.

Registered by `register-nudge-hook.py`; deployed by `nix/home.nix` alongside
`bg_command_capture.py`. Both need a `home-manager switch` to take effect.
"""
import json
import os
import sys

sys.dont_write_bytecode = True
_HERE = os.path.dirname(os.path.abspath(__file__))

EVENTS = ("PreToolUse", "PostToolUse")


def _load():
    """Import `bg_command_capture` from the deployed sibling, else the repo lib/.

    Deployed, both files sit in `~/.claude/hooks/`. In the repo only this file is
    under `scripts/claude-hooks/`, so the tests exercise the second branch and
    the host exercises the first — the same two-branch arrangement
    `agent-ledger-hook.py` uses, and for the same reason.
    """
    import importlib.machinery
    import importlib.util
    for path in (os.path.join(_HERE, "bg_command_capture.py"),
                 os.path.join(_HERE, os.pardir, "lib", "bg_command_capture.py")):
        if os.path.exists(path):
            loader = importlib.machinery.SourceFileLoader("_bg_command_capture", path)
            spec = importlib.util.spec_from_file_location(
                "_bg_command_capture", path, loader=loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
            return mod
    raise ImportError("bg_command_capture.py not found beside the hook or in ../lib/")


def run(stdin=None):
    """The whole hook. Returns an exit status; never raises, never prints."""
    try:
        BC = _load()
    except BaseException:  # noqa: BLE001 — see the fail-open note in the docstring
        return 0
    try:
        if BC.disabled():
            return 0
        payload = json.load(stdin if stdin is not None else sys.stdin)
        event = (payload or {}).get("hook_event_name")
        # A missing key means the event is not ours. Registration is per-host
        # mutable state, so the hook decides what it handles rather than
        # trusting settings.json to have been edited correctly.
        if event not in EVENTS:
            return 0
        rec = BC.build_record(event, payload)
        if rec is None:
            return 0
        BC.append_record(rec)
    except BaseException:  # noqa: BLE001 — see the fail-open note in the docstring
        return 0
    return 0


def main():
    # `--selftest` is the positive control and is the ONE mode that may print and
    # may report failure: it is run by hand, never by the harness.
    if "--selftest" in sys.argv[1:]:
        try:
            return _load().selftest()
        except BaseException as exc:  # noqa: BLE001
            print("selftest error: %s: %s" % (type(exc).__name__, exc))
            return 1
    return run()


if __name__ == "__main__":
    sys.exit(main())
