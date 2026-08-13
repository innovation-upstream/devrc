"""Is the `~/.claude/skills` mapping DECLARED — and not switched off?

SCOPE — HALF THE PROPERTY, ON PURPOSE
-------------------------------------
Four test modules pin SKILL.md files under `claude/skills/` so the pins are
claims about files that SHIP. This answers only the cheap half:

  * `home.file.".claude/skills"` exists and names a `source`;
  * it is not neutered by `enable = false` or a redirected `target` — the two
    smallest edits that stop the deploy while the mapping still reads fine.

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


def skills_mapping_problem(home_nix: Path | str) -> str | None:
    """A failure reason, or None when the mapping is declared and live."""
    home_nix = Path(home_nix)
    if not home_nix.is_file():
        return f"{home_nix} is not a file, {_FIX_IT}"
    if shutil.which("nix-instantiate") is None:
        return f"nix-instantiate is not on PATH, {_FIX_IT}"
    p = subprocess.run(
        ["nix-instantiate", "--eval", "--strict", "--json",
         "-E", _EXPR.replace("@PATH@", str(home_nix.resolve()))],
        capture_output=True, text=True, timeout=180,
    )
    if p.returncode != 0:
        return f"nix cannot evaluate {home_nix}, {_FIX_IT}\n{p.stderr.strip()[-600:]}"
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
