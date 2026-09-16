"""Gate on the CAIRN ROUTING TABLE — `claude/cairn-routes.json`.

WHAT THE MECHANISM IS
---------------------
A cairn client can be pointed at more than one store. `cairn`'s
`lib/cairn_instances.py` owns which instances a host has and which INSTANCE a
scope belongs to; it owns none of the taxonomy, because the scope->alias map is
an INPUT FILE — a public tool that shipped somebody's scope list would be
publishing their org chart. `claude/cairn-routes.json` is devrc's copy of that
input, `nix/home.nix` deploys it to `~/.config/subsystem-store/routes.json`,
`scripts/lib/cairn_routes.py` reads it, and this module grades it.

🔴 THIS IS A TWO-WAY PIN OVER FIXTURES, NOT OVER REALITY, AND THE DIFFERENCE IS
THE FIRST THING A READER SHOULD TAKE FROM THIS FILE. Nothing here reads
`~/.cache/subsystem-store` or any other host-local path, so it runs identically
in both tiers — and so it CANNOT say the table matches the scopes this machine
holds. What it pins is that each direction of `Routing.check` has teeth in the
configuration where teeth exist (`routing_for` at TWO aliases), and that the
shipped table is well-formed and all-default.

⚠ THERE IS NO LIVE ARM, DELIBERATELY. A `drift-check.sh` arm running
`cairn routes --check` was written and WITHDRAWN before merge: on a one-instance
host over an all-default table, direction one produces no output at all
(nothing refuses, so `check` appends nothing), direction three cannot fire (the
only alias named is the one `discover()` always builds), and direction two is
the NOTE cairn's own docstring calls undecidable from a snapshot. It belongs
with the change that makes it reachable — the first host to configure a second
instance.

🔴 WHY EVERY VALUE IS `personal`, WHICH IS THE ASSERTION MOST LIKELY TO BE
"FIXED" BY SOMEONE READING IT AS A PLACEHOLDER. Phase B's closing condition is
"existing behaviour unchanged"; the scope CUTOVER is a later phase. And the
table is not inert until then: `Routing.alias_for` consults it FIRST whatever
`multi_instance` says, so an entry naming an alias this host has no config for
REFUSES — at ONE instance exactly as at many (row 3 of that method's docstring,
pinned below by `test_control_row_three_refuses_even_at_one_instance`). Writing
tomorrow's assignments today would therefore not lie dormant; it would make
those scopes unreadable on every host until the matching `instances/<alias>.env`
exists.

🔴 AND NOTE WHAT DIRECTION ONE NEEDS. "A scope exists and the table does not name
it" is only a PROBLEM with more than one instance — at one, `alias_for` resolves
it to the sole store and `check` reports nothing, correctly. So the fixtures
below grade through `routing_for` with TWO aliases. A fixture built at one alias
would assert a property this code does not have and would stay green over a
table that had lost every entry.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

import cairn_routes  # noqa: E402

HOME_NIX = REPO_ROOT / "nix" / "home.nix"

# 🔴 VACUITY FLOOR on the table itself. Every other assertion in this module is
# satisfied by an EMPTY table: "every value is personal" is true of no values,
# and both reconciliation directions report nothing. 25 scopes were measured on
# the synced store when this landed (2026-09-15); the floor is set below that so
# an ordinary prune is not a gate failure, but a table that has collapsed is.
MIN_SCOPES = 20

# The alias every entry carries today, and the one this repo's hosts have a
# config for. Not spelled — read from cairn, so a rename upstream reds this file
# rather than silently making the whole table unroutable.
PERSONAL = cairn_routes.DEFAULT_ALIAS

# A second alias NO host configures, used only to make `routing_for` model a
# multi-instance host. It is never written to a file.
OTHER = "fixture-other"


@pytest.fixture(scope="module")
def table():
    return cairn_routes.load_table()


# --------------------------------------------------------------------------- #
# The shipped table
# --------------------------------------------------------------------------- #

def test_the_table_is_not_empty(table):
    """POSITIVE CONTROL on the load itself.

    A reassuring zero from a table that parsed to nothing is indistinguishable
    from a clean one — and cairn's own `load_routes` docstring says the same
    thing about the client: a table that parsed to nothing would leave every
    scope unrouted, which LOOKS like a deliberate refusal and is not.
    """
    assert len(table) >= MIN_SCOPES, (
        f"claude/cairn-routes.json names only {len(table)} scope(s), below the "
        f"{MIN_SCOPES} vacuity floor. Re-derive the population with `cairn sync` "
        f"then `ls -1d ~/.cache/subsystem-store/*/`, and fix the table — do not "
        f"lower this floor to make a collapsed table pass."
    )


def test_the_shipped_table_parses_under_cairns_own_reader(table):
    """🔴 THE SHAPE GATE, and it is NOT `json.load`.

    `cairn_routes.load_table` goes through `cairn_instances.load_routes`, which
    accepts EXACTLY a flat object of string -> string and raises on everything
    else. Parsing it here with anything looser would let a table into the repo
    that the client refuses — and the client is what routes the writes, so its
    refusal is the one that matters. That this test reached its assertions at
    all is the gate; the assertions themselves pin the parsed shape.
    """
    assert isinstance(table, dict) and table
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in table.items())


def test_every_scope_routes_to_the_default_instance_today(table):
    """🔴 PHASE B's CLOSING CONDITION, asserted rather than described.

    Do not "finish the migration" by editing this file. An entry naming an alias
    no host has a config for REFUSES every read and write of that scope
    IMMEDIATELY — at one configured instance exactly as at many — so a premature
    cutover does not sit dormant until a second store exists, it breaks the
    scope on every machine. See `test_control_row_three_refuses_even_at_one_
    instance` below, which is that refusal watched happening.
    """
    wrong = sorted(s for s, a in table.items() if a != PERSONAL)
    assert wrong == [], (
        f"these scopes route somewhere other than `{PERSONAL}`: {wrong}.\n"
        f"Phase B ships the registry with existing behaviour UNCHANGED — "
        f"`{PERSONAL}` is the long-standing ~/.config/subsystem-store/env, "
        f"unmoved. A scope may only be re-pointed once some host actually "
        f"carries `instances/<alias>.env` for its new alias; until then the "
        f"entry refuses the scope outright rather than waiting."
    )
    assert cairn_routes.aliases_used(table) == [PERSONAL]


def test_the_keys_are_sorted_so_an_edit_is_a_one_line_diff(table):
    """The file is data AND a review artefact. Unsorted keys make every future
    addition a re-ordering diff nobody can read."""
    raw = json.loads(cairn_routes.TABLE_PATH.read_text(encoding="utf-8"))
    assert list(raw) == sorted(raw), (
        "claude/cairn-routes.json is not key-sorted; re-emit it sorted so the "
        "next edit shows as one added line."
    )


def test_the_table_carries_no_documentation_block(table):
    """🔴 THE TRAP THIS FILE CANNOT DOCUMENT IN ITSELF.

    `claude/skill-tiers.json` opens with a `_doc` array and a test that keeps it
    alive. This table must NOT copy that, and the reason is structural: an array
    value raises `RoutingConfigError`, which `main()` maps to exit 11 — measured
    against the pinned client, `routes`, `doctor`, `ls-entries` and `validate`
    all exit 11 over such a table while `--help` still exits 0, so the claim is
    "every verb that resolves an instance", not "every verb". A STRING value
    parses cleanly — as a route for a scope
    literally named `_doc`. The second failure is the dangerous one, because
    nothing errors. The prose lives in `scripts/lib/cairn_routes.py`'s module
    docstring and beside the `home.file` entry in `nix/home.nix`.
    """
    assert "_doc" not in table, (
        "`_doc` is a ROUTE in this file, not documentation — it claims a scope "
        "by that name. Put the prose in scripts/lib/cairn_routes.py instead."
    )


# --------------------------------------------------------------------------- #
# The reconciliation, RED IN BOTH DIRECTIONS — over FIXTURES
#
# Driven through `routing_for`, which builds a `Routing` with no filesystem —
# it exists precisely so a table can be graded without discovering a host.
#
# 🔴 EVERY CASE HERE MUST BE ABLE TO GO RED ON ITS OWN INPUT. A "check" whose
# inputs cannot disagree belongs nowhere in this block: one used to — it passed
# the shipped table's own values as the alias set, so all three directions were
# empty by construction and no table content could make it fail. It sat here
# reading as coverage. Before adding a case, name the input that reds it.
# --------------------------------------------------------------------------- #

def test_control_a_live_scope_the_table_does_not_name_is_a_problem():
    """DIRECTION ONE, isolated: one extra LIVE scope, the table untouched.

    Two aliases, because that is what makes it a defect — `alias_for` refuses an
    unnamed scope only when there is more than one place it could have gone.
    """
    problems, notes = cairn_routes.check(
        {"alpha": PERSONAL}, {"alpha", "beta"}, aliases=(PERSONAL, OTHER))
    assert len(problems) == 1, problems
    assert "beta" in problems[0] and "REFUSE" in problems[0], problems
    assert notes == (), notes


def test_control_a_table_entry_naming_no_live_scope_is_a_NOTE_not_a_problem():
    """DIRECTION TWO, isolated the other way: one extra TABLE entry, the live
    set untouched.

    🔴 IT IS A NOTE AND THE EXIT CODE MUST NOT MOVE. `Routing.check`'s docstring
    carries the measurement: the scope set is a cache DIRECTORY LISTING while a
    snapshot ships entry FILES, so a scope pre-registered before its first write
    looks IDENTICAL to one that was retired. Grading it at exit 11 made
    `routes --check` refuse every table that pre-registered a scope — which is
    the two-way registry the verb exists to be. Assert the NOTE; do not assert
    an exit code this code does not produce.
    """
    problems, notes = cairn_routes.check(
        {"alpha": PERSONAL, "ghost": PERSONAL}, {"alpha"},
        aliases=(PERSONAL, OTHER))
    assert problems == (), problems
    assert len(notes) == 1, notes
    assert "ghost" in notes[0], notes


def test_control_both_directions_are_clean_on_an_agreeing_pair():
    """The other side of the two controls above: a table that matches the live
    set reports NOTHING. Without this, a `check` hardwired to invent a finding
    would satisfy both of them."""
    problems, notes = cairn_routes.check(
        {"alpha": PERSONAL, "beta": OTHER}, {"alpha", "beta"},
        aliases=(PERSONAL, OTHER))
    assert (problems, notes) == ((), ()), (problems, notes)


def test_an_unregistered_scope_raises_UnroutedScope_carrying_the_scope():
    """🔴 THE SCOPE IS AN ATTRIBUTE, NOT A SUBSTRING OF THE MESSAGE.

    `UnroutedScope`'s own docstring is explicit: a caller that has to grep an
    f-string to learn which scope failed has pinned prose. So this asserts
    `.scope`, and asserts the message separately — a consumer branches on the
    former.
    """
    routing = cairn_routes.routing_for({"alpha": PERSONAL}, (PERSONAL, OTHER))
    with pytest.raises(cairn_routes.UnroutedScope) as excinfo:
        routing.alias_for("unregistered-scope")
    assert excinfo.value.scope == "unregistered-scope"
    assert "REFUSING" in str(excinfo.value)
    # ...and a scope the table DOES name resolves, so the refusal above is not
    # simply "this fixture can never route anything".
    assert routing.alias_for("alpha") == PERSONAL


def test_control_the_same_scope_resolves_at_ONE_instance():
    """🔴 THE CORRECTION, pinned. With one configured instance an unregistered
    scope is NOT a refusal — there is exactly one place it could have gone, so
    `alias_for` answers with it and nothing about the host changes.

    This is what makes the two-alias fixtures above load-bearing rather than
    decorative, and it is the half a one-instance-only test would have got
    backwards.
    """
    routing = cairn_routes.routing_for({"alpha": PERSONAL}, (PERSONAL,))
    assert routing.alias_for("unregistered-scope") == PERSONAL
    assert routing.multi_instance is False
    problems, notes = routing.check({"alpha", "unregistered-scope"})
    assert problems == (), problems


def test_control_row_three_refuses_even_at_one_instance():
    """🔴 THE CLAIM THIS WHOLE PHASE RESTS ON, watched happening.

    "Dropping a table onto a one-instance host changes nothing" is OVER-BROAD.
    Rows 1 and 2 of `alias_for`'s table resolve to the sole instance; row 3 — an
    entry naming an alias this host has NO CONFIG FOR — REFUSES, at one instance
    and at many, because resolving it would be a write landing in a store nobody
    decided on.

    That is the entire reason every value in the shipped table is `personal`
    today, so it is asserted rather than trusted.
    """
    routing = cairn_routes.routing_for({"alpha": OTHER}, (PERSONAL,))
    assert routing.multi_instance is False
    with pytest.raises(cairn_routes.UnroutedScope) as excinfo:
        routing.alias_for("alpha")
    assert excinfo.value.scope == "alpha"
    problems, _notes = routing.check({"alpha"})
    assert len(problems) == 1 and "alpha" in problems[0], problems


# --------------------------------------------------------------------------- #
# NEGATIVE CONTROLS on the reader — the shapes a table may not have
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("body,why", [
    ('{"a": ["doc", "lines"]}', "a _doc-style ARRAY value"),
    ('{"a": {"nested": "object"}}', "a nested object"),
    ('["a", "b"]', "a list document"),
    ('"just a string"', "a non-object document"),
    ('{"a": 1}', "a non-string value"),
    ('{"a": "Not_An_Alias"}', "a value that is not a usable alias"),
    ('{ not json', "unparseable JSON"),
])
def test_control_the_reader_refuses_every_shape_the_client_refuses(
        tmp_path, body, why):
    """🔴 NEGATIVE CONTROL, one case per shape, and the evidence behind this
    file's `_doc` claim.

    Each is a real way a hand-edited table goes wrong. They are graded through
    `cairn_routes.load_table`, so a change that swapped the reader for a looser
    parser moves these verdicts rather than leaving them green.
    """
    path = tmp_path / "routes.json"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(cairn_routes.RoutingConfigError):
        cairn_routes.load_table(path)


def test_control_a_doc_STRING_would_parse_as_a_ROUTE(tmp_path):
    """🔴 THE ONE THAT DOES NOT RAISE, which is why it is the dangerous shape.

    An array `_doc` is loud. A STRING `_doc` is accepted and becomes a route for
    a scope named `_doc` — the table loads, the client is happy, and the
    registry quietly claims a scope nobody has. `test_the_table_carries_no_
    documentation_block` is the guard on the shipped file; this is the proof
    that the guard is needed, because the reader cannot supply it.
    """
    path = tmp_path / "routes.json"
    path.write_text(json.dumps({"_doc": PERSONAL, "a": PERSONAL}),
                    encoding="utf-8")
    loaded = cairn_routes.load_table(path)
    assert loaded == {"_doc": PERSONAL, "a": PERSONAL}


def test_control_a_well_formed_fixture_loads(tmp_path):
    """POSITIVE CONTROL for the same path: the refusals above are not "this
    function can never succeed"."""
    path = tmp_path / "routes.json"
    path.write_text(json.dumps({"beta": PERSONAL, "alpha": "second-store"}),
                    encoding="utf-8")
    loaded = cairn_routes.load_table(path)
    assert loaded == {"alpha": "second-store", "beta": PERSONAL}
    assert cairn_routes.aliases_used(loaded) == [PERSONAL, "second-store"]


# --------------------------------------------------------------------------- #
# THE DEPLOY SEAM — the table is inert unless it lands where the client looks
# --------------------------------------------------------------------------- #

def test_home_nix_deploys_the_table_to_the_file_name_cairn_reads():
    """🔴 SEAM GUARD. The table, the loader and the tests can all be perfect and
    the mechanism still do nothing, because the client reads ONE path and
    nothing in this repo would say the deploy targets another.

    Both halves are asserted: the SOURCE is the checked-in table, and the
    DESTINATION's basename is cairn's own `ROUTES_FILE_NAME` rather than a
    spelled literal — so a rename upstream reds this instead of silently
    orphaning the deployed file.
    """
    src = HOME_NIX.read_text(encoding="utf-8")
    rel = cairn_routes.TABLE_PATH.relative_to(REPO_ROOT).as_posix()
    dest = f".config/subsystem-store/{cairn_routes.ROUTES_FILE_NAME}"
    m = re.search(
        r'home\.file\."%s"\.source\s*=\s*([^;]+);' % re.escape(dest), src)
    assert m, (
        f"nix/home.nix declares no `home.file.\"{dest}\"`, so "
        f"{rel} is never deployed and the client reads no table."
    )
    assign = m.group(1)
    assert "cairn-routes.json" in assign, (
        f"the deployed table is not sourced from {rel}: {assign!r}")
    # 🔴 A STORE COPY, NOT mkOutOfStoreSymlink. This file decides where durable
    # writes land; an out-of-store symlink would repoint it the moment anyone
    # checked out a branch in the primary clone. The neighbouring `cairn-who` /
    # `cairn-validate` entries ARE out-of-store, for a reason (a
    # checkout-relative `lib/` import) that does not apply to a data file.
    assert "mkOutOfStoreSymlink" not in assign, (
        "the routing table is deployed as an out-of-store symlink. It must be a "
        "home.file STORE COPY: a file that decides where durable writes land "
        "must not change when someone checks out a branch in the checkout."
    )
