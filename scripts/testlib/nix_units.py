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
    """Drop `#` comments — whole-line AND trailing — without corrupting strings.

    🔴 TRAILING COMMENTS COUNT. An earlier version of this function stripped
    whole-line comments only, and its docstring still claimed it returned False
    "when the only occurrences are inside comments". An audit measured the gap:
    `someAttr = 1;  # retired: systemd.user.timers.analyze-service-index-backup
    = {` left `declares()` answering **True** for a unit that does not exist.
    That is `claude/RULES.md`'s "guards narrower than their description" — the
    sentence promised comment-blindness, the body delivered half of it.

    🔴 BUT A `#` INSIDE A STRING IS NOT A COMMENT — a URL fragment or a colour
    literal would be truncated by a naive cut at the first `#`, silently
    changing the value a caller reads. So this tracks double-quote parity and
    cuts only at a `#` outside a string. Nix's `''…''` blocks are not handled;
    no unit in `nix/home.nix` puts a `#` inside one, and a reader that guessed
    would be worse than one that says so here.
    """
    out = []
    for ln in block.splitlines():
        if ln.lstrip().startswith("#"):
            continue
        in_str = False
        cut = None
        i = 0
        while i < len(ln):
            c = ln[i]
            if c == "\\" and in_str:
                i += 2
                continue
            if c == '"':
                in_str = not in_str
            elif c == "#" and not in_str:
                cut = i
                break
            i += 1
        out.append(ln if cut is None else ln[:cut].rstrip())
    return "\n".join(out)


def directive(name: str, block: str) -> str | None:
    """The VALUE of a `Name = value;` assignment in `block`, or None.

    🔴 A SUBSTRING SEARCH IS NOT A DIRECTIVE CHECK, and the difference is a
    measured hole. `nix/home.nix` declares the backup service with

        ExecStart = "…/scripts/analyze-service-index/backup.py";
        X-Restart-Triggers = [ "${…/scripts/analyze-service-index/backup.py}" ];

    on consecutive lines. A guard asking `"analyze-service-index/backup.py" in
    block` therefore stays GREEN with the entire ExecStart line deleted — the
    NEXT line still carries the substring, and a `Type=oneshot` unit with no
    ExecStart cannot run at all. The same shape hid a timer whose
    `WantedBy = [ "timers.target" ]` had been changed to `After =
    [ "timers.target" ]`: declared, never enabled, never fires, guard green.

    So callers ask for the directive by NAME and read its value.

    🔴 AN AMBIGUOUS MULTI-LINE VALUE RAISES RATHER THAN TRUNCATING. A value
    wrapped in `pkgs.writeShellScript '' … ''` or an `Environment = { … }` block
    is one ordinary refactor away. A non-greedy `.*?;$` under `re.S` would stop
    at the FIRST line-terminal `;` anywhere inside such a value and hand back a
    plausible truncated string — not None. That is the worst of the three
    outcomes: `_nix_wires_a_backup` would answer False, and the guard would
    instruct a maintainer to paste "the unit has been RETIRED" into a shipped
    skill reference while the unit is live. A loud NotImplementedError is a
    smaller bill than a published falsehood.

    🔴 A WRAPPED DECLARATION IS NOT A MULTI-LINE VALUE, AND THE FIRST VERSION
    OF THIS REFUSED BOTH. `ExecStart =` on one line with a long path on the next
    is an ordinary nix line-wrap; the VALUE is single-line, only the declaration
    is split. Refusing it turned a benign reformat of `nix/home.nix` into a red
    REQUIRED merge check on both tiers. So continuation lines are joined up to
    the terminating `;`, and the result is refused only when it is genuinely
    ambiguous — an unbalanced `''`, brace or bracket, i.e. the
    `writeShellScript '' … ''` and `Environment = { … }` shapes where the first
    line-terminal `;` is INSIDE the value rather than ending it.
    """
    lines = block.splitlines()
    for i, ln in enumerate(lines):
        m = re.match(rf"\s*{re.escape(name)}\s*=\s*(.*)$", ln)
        if not m:
            continue
        parts = [m.group(1).strip()]
        j = i
        while not parts[-1].endswith(";") and j + 1 < len(lines):
            j += 1
            parts.append(lines[j].strip())
        value = " ".join(p for p in parts if p).strip()
        if not value.endswith(";"):
            raise NotImplementedError(
                f"`{name}`'s declaration is never terminated by `;` in this "
                f"block, so its value cannot be read. Line: {ln.strip()!r}"
            )
        value = value[:-1].strip()
        if (value.count("''") % 2
                or value.count("{") != value.count("}")
                or value.count("[") != value.count("]")):
            raise NotImplementedError(
                f"`{name}` has a multi-line value with unbalanced "
                f"''/{{}}/[] — the first line-terminal `;` is INSIDE it, so any "
                "answer here would be a truncation. This reader refuses rather "
                "than returning a WRONG value a caller would act on. Extend "
                f"`directive()` to handle it. Got: {value!r}"
            )
        return value
    return None


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

    🔴 THE SPAN IS `=` TO THE BODY'S OPENING BRACE, NOT THE `=` LINE. An earlier
    version read only the remainder of the `=` line, and an audit measured it
    INERT on the very unit it was written for: the backup SERVICE is declared

        systemd.user.services.analyze-service-index-backup =
          let
            pyEnv = pkgs.python3.withPackages (p: [ p.minio ]);
          in
          {

    so the `=` line is EMPTY, and a `lib.mkIf` written where nix style puts it
    for that shape — on a following line — was invisible. The round of mutation
    testing that "proved" the clause only ever exercised the TIMER, which is
    declared `= {` on one line. Breakable on one unit and inert on the other is
    exactly `claude/RULES.md`'s "prove it REACHABLE, not just breakable".
    """
    stripped = strip_nix_comments(src)
    needle = f"{attr_path} ="
    assert needle in stripped, f"{needle!r} is not a live declaration"
    after = stripped.split(needle, 1)[1]
    brace = after.find("{")
    span = after if brace == -1 else after[:brace]
    return "mkIf" in span
