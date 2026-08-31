#!/usr/bin/env python3
"""PostToolUse nudge: a `git add` staged an untracked file this session did not
create — i.e. somebody else's uncommitted work, swept into your index.

--------------------------------------------------------------------------- #
THE INCIDENT
--------------------------------------------------------------------------- #
MEASURED 2026-08-30. An opencode session was adding two packages to
`nix/pkgs/default.nix`. Its `home-manager switch` failed, because a DIFFERENT
session's uncommitted `nix/graphical.nix` edit referenced `scripts/memory-detail`
via `home.file`, and a flake build copies TRACKED files only. The session ran:

    git -C ~/workspace/devrc add scripts/memory-detail && home-manager switch …

The switch then succeeded, and the session recorded the workaround as the
diagnosis: "the file exists but isn't tracked by git". The MECHANISM was right.
The SCOPE was wrong — that script was 181 lines of an unrelated, in-flight bar
feature belonging to another effort, and staging it made it a candidate for the
next commit. The session's own handoff then told the next reader to commit
`nix/pkgs/default.nix` + `scripts/memory-detail` together, which would have
landed half of someone else's feature: the script without its `nix/graphical.nix`
wiring and without its 20 tests.

--------------------------------------------------------------------------- #
WHY THIS IS A NUDGE AND NOT A `bash-guard` DENIAL
--------------------------------------------------------------------------- #
🔴 `guard_core` HAS NO WARN TIER — `evaluate()` returns a reason (DENY) or None.
A check there is a BLOCK, and blocking this would be wrong twice over:

  * `git add <path>` is the SAFE form the rules already steer you to;
    `check_git_add_all` exists to push callers toward exactly this spelling, and
    a guard that then blocks the safe spelling contradicts its own sibling.
  * in THIS repo staging a new file is MANDATORY, not optional — `CLAUDE.md`:
    "A NEW file must be `git add`ed or the flake silently omits it from the
    deploy." A false positive would block the deploy path for every new skill,
    reference doc, extension file, hook and test. `claude/RULES.md` calls a
    permanently-red gate worse than no gate, because it trains you to click
    through.

Staging is also trivially reversible (`git restore --staged`), so acting AFTER
the fact costs nothing. PostToolUse it is.

--------------------------------------------------------------------------- #
🔴 WHY IT ASKS GIT INSTEAD OF PARSING THE COMMAND
--------------------------------------------------------------------------- #
The obvious build is to parse the `git add` operands. That means re-deriving
`guard_core`'s argv handling — global-option hops (`git -C X add`), quoting,
`--`, subshells — a predicate `claude/RULES.md` says is "typically wrong at N−1"
of the sites that open-code it.

None of it is needed. This hook fires AFTER the command, so the index already
holds the answer: `git diff --cached --name-only --diff-filter=A` lists exactly
the paths staged as ADDED, which is precisely "was untracked, now staged". The
command text is used for ONE thing — deciding whether this call was plausibly a
`git add` at all — and being loose there is free: a false trigger just runs a
read-only query that finds nothing and stays silent.

--------------------------------------------------------------------------- #
🔴 WHAT "THIS SESSION DID NOT CREATE" MEANS, AND WHAT IT CANNOT MEAN
--------------------------------------------------------------------------- #
The signal is mtime older than the session's earliest transcript timestamp.

That is a HEURISTIC and it is deliberately biased toward SILENCE:

  * a RESUMED session keeps its old transcript records, so its earliest
    timestamp can predate the current run by days. Every file written in between
    then reads as "mine" and is NOT flagged. False NEGATIVE — the safe
    direction, and the reason this is not tightened to the current run.
  * a file the operator wrote by hand just before asking for it to be committed
    is "not created by this session" and WOULD be flagged. That is why the text
    asks rather than asserts, and why nothing is blocked.
  * `cp -a`, `git checkout` and a restore-from-backup all preserve or rewrite
    mtimes in ways this cannot see.

So the message must never say "this is not yours". It says the file predates the
session and asks you to confirm — the same shape as the incident, where one
glance at `git status` would have shown a second unrelated dirty file.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

#: Loose on purpose — see the module docstring. A false trigger costs one
#: read-only `git diff --cached` that returns nothing.
_GIT_ADD = re.compile(r"\bgit\b[^\n|;&]*\badd\b")

#: `git -C <dir>` / `git --git-dir=…` hop. Used only to pick WHICH repo to ask;
#: unresolvable means "fall back to cwd", never "guess".
_DASH_C = re.compile(r"\bgit\s+(?:-[^\s]+\s+)*-C\s+([^\s;|&]+)")

#: Cap the report. A `git add` of 40 new files is a legitimate bulk import and a
#: 40-line nudge is noise; the count still tells the whole story.
_SHOWN_MAX = 8

#: 🔴 THE ON-DISK NAME, ONE PLACE. Per-hook directory, matching
#: `claude-shell-env-nudge`'s convention rather than a shared `claude-hooks/`
#: bucket: `test_on_disk_artifact_names.py` pins every hook's storage names from
#: BOTH sides, and a shared directory makes "whose file is this" unanswerable
#: when one hook's entries have to be cleared.
CACHE_DIR = "~/.cache/claude-git-add-provenance"

_TIMEOUT = 5


def _git(repo: str, *args: str) -> str | None:
    """Read-only git, or None. NEVER raises into the hook."""
    try:
        p = subprocess.run(
            ("git", "-C", repo, *args),
            capture_output=True, text=True, timeout=_TIMEOUT,
        )
    except Exception:
        return None
    if p.returncode != 0:
        return None
    return p.stdout


def _session_start(transcript_path: str | None) -> float | None:
    """Epoch seconds of the session's earliest transcript timestamp, or None.

    🔴 None means DO NOT NUDGE. Without a clock every staged-new file looks
    equally old, and the hook would fire on every legitimate new-file add — the
    permanently-red shape the docstring rejects. Silence is the fail direction.
    """
    if not transcript_path:
        return None
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                ts = rec.get("timestamp")
                if not ts:
                    continue
                # The first RECORD is a `leafUuid` summary carrying no
                # timestamp, so this scans rather than reading line 1 — measured
                # at claude-code 2.1.232, where the first timestamped record was
                # line 5.
                return datetime.fromisoformat(
                    ts.replace("Z", "+00:00")
                ).astimezone(timezone.utc).timestamp()
    except Exception:
        return None
    return None


def _repo_dir(cmd: str, cwd: str | None) -> str | None:
    base = cwd or os.getcwd()
    m = _DASH_C.search(cmd)
    if m:
        cand = os.path.expanduser(m.group(1).strip("'\""))
        if not os.path.isabs(cand):
            cand = os.path.join(base, cand)
        if os.path.isdir(cand):
            return cand
        # An unresolvable -C (a variable, a typo) falls back rather than
        # guessing: the worst case is a query against the wrong repo, which
        # finds nothing and stays silent.
    return base if os.path.isdir(base) else None


def foreign_staged(repo: str, started: float) -> list[tuple[str, float]]:
    """`(path, mtime)` for each ADDED-to-index path whose mtime predates `started`.

    `--diff-filter=A` is the whole predicate: a path is listed exactly when the
    index has it and HEAD does not, i.e. it was untracked and is now staged.
    A path staged and then deleted from disk is skipped — `os.stat` fails and it
    carries no evidence either way.
    """
    out = _git(repo, "diff", "--cached", "--name-only", "--diff-filter=A", "-z")
    if not out:
        return []
    found: list[tuple[str, float]] = []
    for rel in out.split("\0"):
        if not rel:
            continue
        try:
            mtime = os.stat(os.path.join(repo, rel)).st_mtime
        except OSError:
            continue
        if mtime < started:
            found.append((rel, mtime))
    return found


def _already_nudged(session: str, repo: str, rel: str) -> bool:
    """Once per (session, repo, path). Re-reporting the same file on every
    subsequent `git add` in the session is how a nudge becomes wallpaper."""
    if not session:
        return False
    try:
        d = os.path.expanduser(CACHE_DIR)
        os.makedirs(d, exist_ok=True)
        # Sanitised, for `shell-env-nudge`'s reason: a session id is opaque and
        # goes into a filename, so anything that is not clearly safe becomes `_`.
        safe = "".join(c if c.isalnum() or c in "_.-" else "_" for c in session)
        f = os.path.join(d, safe)
        key = f"{repo}\0{rel}"
        seen: set[str] = set()
        if os.path.exists(f):
            with open(f, encoding="utf-8") as fh:
                seen = set(fh.read().split("\n"))
        if key in seen:
            return True
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(key + "\n")
        return False
    except Exception:
        return False


def build_nudge(rows: list[tuple[str, float]], started: float) -> str:
    shown = rows[:_SHOWN_MAX]
    elided = len(rows) - len(shown)
    lines = []
    for rel, mtime in shown:
        age = started - mtime
        if age >= 86400:
            when = f"{age / 86400:.1f}d before this session"
        elif age >= 3600:
            when = f"{age / 3600:.1f}h before this session"
        else:
            when = f"{age / 60:.0f}m before this session"
        lines.append(f"  • {rel}  (last written {when})")
    if elided:
        lines.append(f"  … and {elided} more")
    return (
        f"git-add provenance: {len(rows)} newly-staged file(s) were last written "
        f"BEFORE this session started, so they were probably not created by this "
        f"session's work:\n"
        + "\n".join(lines)
        + "\n  If they belong to another effort, unstage them — `git restore "
        "--staged <path>` — and commit only your own paths. Staging someone "
        "else's in-flight file makes it a candidate for your next commit, which "
        "lands half a feature: the file without whatever else it needs.\n"
        "  ⚠ mtime is a heuristic, not proof of authorship. If these ARE yours "
        "(edited before a resume, restored from a copy), carry on — this is a "
        "question, not a refusal, and nothing was blocked."
    )


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    try:
        if data.get("tool_name") != "Bash":
            sys.exit(0)
        cmd = (data.get("tool_input") or {}).get("command", "")
        if not cmd or not _GIT_ADD.search(cmd):
            sys.exit(0)
        cwd = data.get("cwd")
        cwd = cwd if isinstance(cwd, str) and cwd else None
        repo = _repo_dir(cmd, cwd)
        if not repo:
            sys.exit(0)
        started = _session_start(data.get("transcript_path"))
        if started is None:
            sys.exit(0)     # no clock ⇒ no nudge; see `_session_start`
        rows = foreign_staged(repo, started)
        session = data.get("session_id") or ""
        rows = [r for r in rows if not _already_nudged(session, repo, r[0])]
        if not rows:
            sys.exit(0)     # SILENT — no reassuring "0 foreign files" line
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": build_nudge(rows, started),
            }
        }))
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
