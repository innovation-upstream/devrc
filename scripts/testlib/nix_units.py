"""Read `nix/home.nix` the way a systemd unit means it, not the way grep sees it.

WHY THIS IS SHARED RATHER THAN OPEN-CODED
-----------------------------------------
🔴 A substring search over `nix/home.nix` answers questions about the PROSE,
not the configuration, and it does it silently. These blocks are heavily
commented and several comments QUOTE directives verbatim
(`#   ProtectHome=read-only …`, `# BindPaths = [ … "-%h" ];`), so a grep for a
unit name matches a comment that says the unit was DELETED just as happily as
the declaration itself.

That is not hypothetical. `scripts/tests/test_index_store_backup_claim.py`
originally open-coded `"systemd.user.services.analyze-service-index-backup" in
src`, and an audit disabled the whole backup — both attribute paths renamed,
zero live declarations left, only `# DISABLED …:` comment lines still naming
them — and the guard stayed **green** while the docs went on promising daily
off-machine bundles. `test_analyze_service_index_backup.py` already had a
comment-stripping reader for the identical strings in the same directory; the
second, weaker copy is the "one rule, one place" failure `claude/RULES.md`
describes, and consolidating is what made the disagreement audible.

🔴 STRIPPING IS NOT COSMETIC — IT IS THE WHOLE POINT. A caller that reads the
raw text cannot distinguish "this unit is declared" from "this unit is
mentioned", and those are opposite facts when a unit is being retired.
"""

from __future__ import annotations

import re

# A top-level unit declaration in this file always starts at exactly two spaces
# of indentation: `  systemd.user.services.<name> = …`. Anything deeper is
# nested inside another attribute set.
_TOP_LEVEL_UNIT = re.compile(r"^  systemd\.user\.(?:services|timers|paths|sockets)\.", re.M)


def strip_nix_comments(block: str) -> str:
    """Drop whole-line `#` comments.

    Whole-line only, deliberately: a trailing `#` inside a nix string literal
    (a URL fragment, a colour) is not a comment, and cutting at the first `#`
    on the line would corrupt the value being read.
    """
    return "\n".join(
        ln for ln in block.splitlines() if not ln.lstrip().startswith("#")
    )


def declares(attr_path: str, src: str) -> bool:
    """Is `attr_path` a LIVE top-level declaration in `src` (comments stripped)?

    Returns False when the only occurrences are inside comments — which is what
    a retired-but-documented unit looks like.
    """
    return f"{attr_path} =" in strip_nix_comments(src)


def unit_source(attr_path: str, src: str) -> str:
    """The declaration body of one top-level unit, comments stripped.

    Bounded at the NEXT top-level unit declaration rather than by a byte count
    or by a caller-supplied end marker, so the window cannot silently run into
    the following unit when the comments between them change length.

    Raises AssertionError if `attr_path` is not a live declaration, or appears
    more than once — a reader that silently picked one of two would answer
    about an arbitrary unit.
    """
    stripped = strip_nix_comments(src)
    needle = f"{attr_path} ="
    count = stripped.count(needle)
    assert count == 1, (
        f"{needle!r} appears {count}x as a live declaration in nix/home.nix; "
        "this reader assumes exactly one and would otherwise answer about an "
        "arbitrary one (0 = declared only inside comments, or not at all)"
    )
    body = stripped.split(needle, 1)[1]
    nxt = _TOP_LEVEL_UNIT.search(body)
    return body[: nxt.start()] if nxt else body


def is_conditional(attr_path: str, src: str) -> bool:
    """Is the unit gated behind `lib.mkIf`, so it deploys only on some hosts?

    Several units here are legitimately `lib.mkIf serverMode`. The distinction
    matters to any DOC that promises the unit runs: an unconditional promise
    about a conditional unit is false on every host the condition excludes.
    """
    stripped = strip_nix_comments(src)
    needle = f"{attr_path} ="
    assert needle in stripped, f"{needle!r} is not a live declaration"
    # The gate, when present, sits on the same line as the `=`.
    line = stripped.split(needle, 1)[1].splitlines()[0]
    return "lib.mkIf" in line or "mkIf" in line
