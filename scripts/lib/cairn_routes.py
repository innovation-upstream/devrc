#!/usr/bin/env python3
"""The checked-in cairn ROUTING TABLE — `claude/cairn-routes.json` — read once.

WHAT THE TABLE IS
-----------------
A cairn client may be pointed at more than one store. `cairn`'s own
`lib/cairn_instances.py` owns the mechanism: which instances a host has, which
INSTANCE a scope belongs to, and whether the two agree. It deliberately owns
NONE of the taxonomy — the scope->alias map is an INPUT FILE, never a constant
in that repository, because a public tool that shipped somebody's scope list
would be publishing their org chart.

This module is devrc's half of that seam: the table is the input file, and it
lives here so it is reviewed, diffable and deployed like everything else.

  * `claude/cairn-routes.json`            the table (data)
  * `nix/home.nix`                        deploys it to
                                          ~/.config/subsystem-store/routes.json
  * `scripts/tests/test_cairn_routes.py`  grades it, deterministically
  * `scripts/drift-check.sh`              grades it LIVE, host-locally

so the parsing, the reconciliation and the deploy path are named HERE, once.
`claude/RULES.md` -> "One rule, one place".

🔴 THE FILE CANNOT CARRY ITS OWN DOCUMENTATION, WHICH IS WHY THIS DOCSTRING IS
LONG. `claude/skill-tiers.json` opens with a `_doc` array and a test that keeps
it alive. This table cannot do that, and the reason is structural rather than
stylistic: `cairn_instances.load_routes` accepts EXACTLY a flat JSON object of
string -> string and refuses everything else. A `_doc` array raises
`RoutingConfigError` and takes the whole table down; a `_doc` STRING is worse —
it parses cleanly as a route for a scope literally named `_doc`, so the file
loads, the client is happy, and the table quietly claims a scope nobody has.
So the table ships flat and documentation-free, and the prose lives here and in
the pointer comment beside the `home.file` entry in `nix/home.nix`.

🔴 EVERY VALUE IS `personal` TODAY, AND THAT IS THE POINT OF THE PHASE, NOT AN
OVERSIGHT. `personal` is `subsystem_read_store.DEFAULT_ALIAS` — the alias of the
long-standing `~/.config/subsystem-store/env`, unmoved. Phase B's closing
condition is "existing behaviour unchanged": the table is DEPLOYED and GRADED
before any scope is re-assigned, so the registry exists before the cutover
needs it (that cutover is phase E).

⚠ AND ROUTING A SCOPE SOMEWHERE ELSE TODAY WOULD BREAK IT, WHICH IS THE
NON-OBVIOUS HALF. It is tempting to read "one instance means routing is off" and
conclude a table can say anything harmlessly until a second store exists. It
cannot. `Routing.alias_for` consults the TABLE FIRST whatever `multi_instance`
says, and its docstring's row 3 is explicit: a table entry naming an alias this
host has NO CONFIG FOR raises `UnroutedScope` — at one instance exactly as at
many. So an entry `"civitai": "civitai"` written before any host carries
`instances/civitai.env` does not lie dormant; it REFUSES every read and write of
that scope, on every host, immediately. The two-way pin below cannot catch that
one — `cairn routes --check` can, and does, which is why the live arm exists.

WHAT THE TWO CHECKS SEE, AND WHAT THEY CANNOT
---------------------------------------------
`Routing.check` grades in both directions and this module does not re-implement
either of them:

  direction one  a scope that EXISTS with no table entry -> a PROBLEM, because
                 its next write refuses ... *when there is more than one
                 instance*. At one instance `alias_for` resolves it to the sole
                 store, so `check` reports nothing — correctly, and that is why
                 the deterministic suite drives it through `routing_for` with
                 TWO aliases rather than against this host.
  direction two  a table entry naming a scope that exists NOWHERE -> a NOTE,
                 never a problem. The scope set is a cache DIRECTORY LISTING and
                 a snapshot ships entry FILES, so a scope that is empty —
                 pre-registered before its first write, or pruned back to
                 nothing — is indistinguishable from one that was retired. Read
                 `Routing.check`'s own docstring before "fixing" that to a
                 problem; the measurement and the closing condition are there.
  direction three  a table entry naming an alias no instance provides -> a
                 PROBLEM, at any instance count. This is the one above.

🔴 SO A GREEN `routes --check` ON A ONE-INSTANCE HOST IS A NARROWER CLAIM THAN
IT LOOKS. It says the table names no unreachable alias and no scope that has
gone missing; it CANNOT say every live scope is registered, because at one
instance that is not yet a defect. The deterministic suite is what pins
direction one, by grading a fixture at two aliases.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TABLE_PATH = REPO_ROOT / "claude" / "cairn-routes.json"

# 🔴 THE VOCABULARY BELOW IS CAIRN'S, IMPORTED, NEVER RESTATED. `DEFAULT_ALIAS`,
# the table's file name, its environment variable and every predicate come from
# the pinned client through `cairn_pin`, so a rename upstream is an ImportError
# here rather than a table that routes every scope to a word nothing answers to.
# `cairn_pin.ensure()` APPENDS, so devrc-local modules still win; it RAISES
# rather than degrading, because there is no local copy to fall back to.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cairn_pin  # noqa: E402

cairn_pin.ensure()

from cairn_instances import (  # noqa: E402
    ROUTES_ENV,
    ROUTES_FILE_NAME,
    RoutingConfigError,
    UnroutedScope,
    load_routes,
    routes_file,
    routing_for,
)
from subsystem_read_store import DEFAULT_ALIAS  # noqa: E402

__all__ = [
    "DEFAULT_ALIAS",
    "REPO_ROOT",
    "ROUTES_ENV",
    "ROUTES_FILE_NAME",
    "RoutingConfigError",
    "TABLE_PATH",
    "UnroutedScope",
    "aliases_used",
    "check",
    "deployed_path",
    "load_table",
    "routing_for",
    "scopes",
]


def load_table(path: Path | None = None) -> dict[str, str]:
    """The checked-in scope->alias table, parsed by CAIRN'S OWN READER.

    🔴 NOT A SECOND PARSER, AND THAT IS THE WHOLE VALUE OF THIS FUNCTION. The
    consumer of this file is the pinned client; a `json.load` here would accept
    shapes the client refuses, so the suite could be green over a table that
    takes every `cairn` verb on the host to exit 11. Going through
    `load_routes` means "it parsed here" and "it parses there" are the same
    claim.
    """
    return load_routes(path or TABLE_PATH)


def scopes(table: dict[str, str]) -> list[str]:
    """Every scope the table names, sorted."""
    return sorted(table)


def aliases_used(table: dict[str, str]) -> list[str]:
    """Every INSTANCE alias the table routes to, sorted and deduplicated.

    This is the set a host must be able to configure. Today it is exactly
    `[DEFAULT_ALIAS]`; the day it grows, a host that has not grown with it
    REFUSES the scopes pointing at the new alias (row 3 in the module
    docstring), which is what makes this worth naming rather than deriving
    inline at each call site.
    """
    return sorted(set(table.values()))


def check(table: dict[str, str], live_scopes, aliases=None):
    """`(problems, notes)` for `table` graded against `live_scopes`.

    🔴 IT DELEGATES. `Routing.check` already grades all three directions and
    asks `Routing.alias_for` for the third rather than re-deriving it; a second
    implementation here would be free to disagree with the client that actually
    routes the writes. `aliases` defaults to the table's own alias set, which is
    the configuration under which the table is INTERNALLY consistent — pass a
    different one to grade a host.
    """
    return routing_for(table, aliases or aliases_used(table)).check(live_scopes)


def deployed_path() -> Path:
    """Where the deployed table lives on THIS host.

    Asked of cairn (`routes_file`), never spelled, because the rule is the
    client's: `$CAIRN_ROUTES` if set, else `routes.json` beside the default
    instance's config file. The `home.file` entry in `nix/home.nix` writes the
    default location; a host that has moved `$SUBSYSTEM_STORE_CONFIG` has moved
    this with it, and a spelled constant here would report the wrong file.
    """
    return routes_file()[0]
