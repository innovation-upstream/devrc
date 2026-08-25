"""Is the `~/.claude/skills` mapping DECLARED — and not switched off?

SCOPE — HALF THE PROPERTY, ON PURPOSE
-------------------------------------
Four test modules pin SKILL.md files under `claude/skills/` so the pins are
claims about files that SHIP. This answers only the cheap half, and only
**as `nix/home.nix` itself declares it**:

  * `home.file.".claude/skills"` exists there and names a `source`;
  * that declaration is not neutered by `enable = false` or a redirected
    `target` — the two smallest edits that stop the deploy while the mapping
    still reads fine.

🔴 FILE-SCOPED, NOT CONFIG-SCOPED. The real `home.file` is a MERGE of every
module, so a sibling can switch this mapping off and this still returns None —
verified with `imports = [ ./off.nix ];` + `enable = lib.mkForce false;`. That
idiom is live here (`graphical.nix` already does `lib.mkIf` on `home.file`).
Closing it means evaluating the whole `homeConfiguration`, which is the cost
this module was cut to escape. ship.sh does NOT backstop it either: a disabled
mapping produces no managed link, so nothing shows up dangling.

🔴 It does NOT trace what the `source` RESOLVES to, and must not grow that back.
Following it (the source is a derivation built from `../claude/skills`) meant
`$out` analysis, `cp`/`rm` parsing, let-binding resolution and comment
stripping — heuristics that still admitted constructible false ALL-CLEARs, and
whose only firing ever was a FALSE POSITIVE. That half is checked against
REALITY, on both hosts, at the moment it matters: `scripts/ship.sh` prints
`✅ managed artifacts resolve — N checked, 0 dangling, 0 absent` on every deploy
and `scripts/drift-check.sh` reports the same passively (rc 14). Those stat the
real filesystem; a text parser re-deriving it from nix source is strictly worse
and always will be.

HOW
---
`nix-instantiate --eval` on the real file, so NIX answers the structural
questions — dotted vs attrset form, comments, where the attribute nests. What
nix cannot evaluate FAILS loudly as "needs updating"; nothing passes silently.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

MAPPING = 'home.file.".claude/skills"'

#: home-manager's default `target` is the attribute name itself.
TARGET = ".claude/skills"

#: `config`/`pkgs`/`lib` are stubs: nix is lazy, so nothing outside this one
#: attribute is forced, and a stub that IS forced fails closed.
_EXPR = """
let fs = (import @PATH@ { config = {}; pkgs = {}; lib = {}; }).home.file or {};
    m = fs.".claude/skills" or {};
in { declared = fs ? ".claude/skills";
     source = m ? source;
     enable = m.enable or true;
     target = m.target or ".claude/skills"; }
"""

_FIX_IT = (
    "so this check cannot answer it. FIX THE CHECK — do NOT delete it: without "
    "it, every SKILL.md pin under claude/skills/ silently stops being a claim "
    "about a deployed file."
)

#: MEASURED, idle 24-core workbench: 0.02 s, and COLD == WARM (no nixpkgs
#: import, nothing realised, so there is no cache to warm). Only CPU contention
#: moves it, roughly with the oversubscription factor: 0.06 s at 1x, 0.26 s at
#: 4x, 1.8 s at 10x. CI runs this in the `checks.pytests` sandbox beside ~15k
#: tests where cgroup throttling stacks on that, and the old hardcoded 60 s was
#: reached for real (devrc-ci-ztn92) — turning "cannot answer" into a red
#: REQUIRED gate on unrelated PRs. 180 s is ~100x the worst measured contended
#: run: a deadman, not a performance budget, so reaching it means WEDGED.
_TIMEOUT_ENV = "DEVRC_SKILLS_MAPPING_TIMEOUT_S"
_DEFAULT_TIMEOUT_S = 180.0


def _budget() -> float | str:
    """Seconds to allow, or a "cannot answer" reason when the override is junk.

    🔴 A junk override must NOT fall back to the default. This knob can only
    widen or narrow a safety check, so ignoring an unreadable one is how `…=0`
    or a typo switches the check off while it still reads green.
    """
    raw = os.environ.get(_TIMEOUT_ENV)
    if raw is None or not raw.strip():
        return _DEFAULT_TIMEOUT_S
    try:
        seconds = float(raw)
    except ValueError:
        seconds = float("nan")
    if not seconds > 0 or seconds == float("inf"):
        return (
            f"{_TIMEOUT_ENV}={raw!r} is not a positive, finite number of "
            f"seconds, {_FIX_IT}"
        )
    return seconds


def skills_mapping_problem(home_nix: Path | str) -> str | None:
    """A failure reason, or None when the mapping is declared and live."""
    home_nix = Path(home_nix)
    # NEVER interpolate the raw argument: a caller passing the file's TEXT
    # instead of its path makes every message below ~160 KB of nix source.
    # Depending on the filesystem that argument either raises ENAMETOOLONG out
    # of is_file() or simply reads as a missing file, so truncating is the fix
    # that holds in both cases — catching the errno alone does not.
    shown = (lambda t: t if len(t) <= 120 else t[:117] + "...")(str(home_nix))
    try:
        is_file = home_nix.is_file()
    except OSError as exc:
        return f"cannot stat {shown!r} ({exc.strerror}), {_FIX_IT}"
    if not is_file:
        return f"{shown} is not a file, {_FIX_IT}"
    if shutil.which("nix-instantiate") is None:
        return f"nix-instantiate is not on PATH, {_FIX_IT}"
    budget = _budget()
    if isinstance(budget, str):
        return budget
    try:
        p = subprocess.run(
            ["nix-instantiate", "--eval", "--strict", "--json",
             "-E", _EXPR.replace("@PATH@", str(home_nix.resolve()))],
            capture_output=True, text=True, timeout=budget,
        )
    except subprocess.TimeoutExpired:
        # Idle runtime is ~0.02 s (see _DEFAULT_TIMEOUT_S), so reaching this is
        # a wedged environment — a broken check, not a broken home.nix.
        return (
            f"nix-instantiate did not finish within {budget:g}s, {_FIX_IT} "
            f"If the host is merely slow rather than wedged, widen the budget "
            f"with {_TIMEOUT_ENV}=<seconds> — do not shrink the check."
        )
    if p.returncode != 0:
        return f"nix cannot evaluate {shown}, {_FIX_IT}\n{p.stderr.strip()[-600:]}"
    m = json.loads(p.stdout)
    if not m["declared"]:
        return (
            f"nix/home.nix no longer declares {MAPPING} — nothing deploys "
            "claude/skills/, so docs pinned under it may not ship at all."
        )
    if not m["source"]:
        return f"{MAPPING} declares no `source =`, so it deploys nothing."
    if m["enable"] is not True:
        return (
            f"{MAPPING} is switched OFF (`enable = {json.dumps(m['enable'])}`): "
            "declared, and deployed by nothing."
        )
    if m["target"] != TARGET:
        return (
            f"{MAPPING} redirects `target` to {m['target']!r}: claude/skills/ "
            f"lands at ~/{m['target']}, not ~/{TARGET}."
        )
    return None


def assert_skills_mapping_declared(home_nix: Path | str) -> None:
    """Raise AssertionError unless nix/home.nix declares a live skills mapping."""
    problem = skills_mapping_problem(home_nix)
    assert problem is None, problem
