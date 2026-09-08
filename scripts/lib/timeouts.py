#!/usr/bin/env python3
"""One predicate for "is this a usable timeout", shared by every bounded call.

🔴 CONSOLIDATING THIS IS WHAT FOUND THE BUG IT GUARDS AGAINST. The rule was once
open-coded at two call sites — `_run` in `cairn_who.py` and `fetch_snapshot` in
`scripts/cairn` — and the copies DISAGREED: only one excluded `bool`. A comment
claiming the store side refused "the way `cairn_who._run` always has" was
therefore false, and the weaker copy was measured running `_run(cmd, True)` with
a ONE-SECOND bound while reporting `did not answer within Trues` — verbatim the
"nobody notices" failure `_run`'s own docstring describes.

🔴 AND THIS MODULE IS WHY THE `who` SPLIT DID NOT REOPEN THAT BUG. `who` used to
live inside `scripts/cairn`, so the store path could reach the predicate by
importing the `who` module. Moving `who` out to its own `cairn-who` binary broke
that reach, and the cheap repair — re-open-coding the check on the store side —
is exactly the two-copies state above. The predicate lives here instead, imported
by BOTH binaries, so the two halves cannot drift apart again now that they are
two programs rather than one.
`test_the_timeout_predicate_has_exactly_ONE_implementation` pins that
structurally, over `scripts/cairn`, `scripts/cairn-who`, `cairn_who.py` and
this file.

⚠ THERE IS NO SHARED `DEFAULT_TIMEOUT` HERE, DELIBERATELY. The two callers have
DIFFERENT bounds for measured reasons — the store's 20s is tuned for an HTTP
snapshot fetch, `cairn_who.DEFAULT_TIMEOUT`'s 60s for shelling into tmux on two
hosts with one of them possibly asleep. A single constant in this module would
either be imported by neither (dead) or unify two bounds whose whole point is
that they differ. Each owns its own default; only the PREDICATE is shared.
"""
from __future__ import annotations


def unbounded_timeout_reason(value) -> str | None:
    """Why `value` is not a usable timeout, or `None` if it is fine.

    `bool` is the trap: it subclasses `int`, so a plain `isinstance(x, int)`
    accepts `True` and silently yields a 1-second timeout. `None` is the other:
    it means NO timeout to both `subprocess` and `urlopen` — an unbounded wait
    rather than a default.

    Returns a REASON rather than raising, so each caller can raise its own
    exception type (`WhoError`, `StoreUnreachable`) without coupling them to one
    another's error class. That is what makes one predicate serviceable to two
    separate binaries.
    """
    if isinstance(value, bool):
        return (f"timeout={value!r} is a bool — it subclasses int, so this "
                "would silently run with a 1-second bound")
    if not isinstance(value, int):
        return (f"timeout={value!r} is not an int — a missing bound is an "
                "UNBOUNDED wait, not a default")
    if value <= 0:
        return (f"timeout={value!r} is not positive — a non-positive bound is "
                "an UNBOUNDED wait, not a default")
    return None
