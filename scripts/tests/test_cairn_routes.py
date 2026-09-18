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
shipped table is well-formed and names only CONFIGURED aliases.

🔴 THERE IS STILL NO LIVE ARM, AND AS OF PHASE E THAT IS OWED WORK RATHER THAN A
DECISION. A `drift-check.sh` arm running `cairn routes --check` was written and
WITHDRAWN before merge, because on a one-instance host over an all-default table
direction one produces no output at all (nothing refuses, so `check` appends
nothing), direction three cannot fire (the only alias named is the one
`discover()` always builds), and direction two is the NOTE cairn's own docstring
calls undecidable from a snapshot. Its stated return condition was "the first
host to configure a second instance" — **that condition was met on 2026-09-18 and
this file's guard is a DECLARED ledger standing in for the live check.** The
substitution is named here so it is not mistaken for the arm.

🔴 VALUES ARE NO LONGER ALL `personal` — phase E (2026-09-18) re-pointed five
scopes and added one, after BOTH hosts were confirmed to carry
`instances/civitai.env`. The rule that replaced the all-default pin is
`CONFIGURED_ALIASES`: a value may name any alias some host is declared to have a
config for. **The table was never inert, and that is why the order matters:**
`Routing.alias_for` consults it FIRST whatever `multi_instance` says, so an entry
naming an alias this host has no config for REFUSES — at ONE instance exactly as
at many (row 3 of that method's docstring, pinned below by
`test_control_row_three_refuses_even_at_one_instance`). Writing tomorrow's
assignments today does not lie dormant; it makes those scopes unreadable on every
host until the matching `instances/<alias>.env` exists.

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
# satisfied by an EMPTY table: "every value names a configured alias" is true of
# no values, and both reconciliation directions report nothing. 25 scopes were
# measured on the synced store when this landed (2026-09-15) and 26 after phase E
# added a row (2026-09-18); the floor is set below that so an ordinary prune is
# not a gate failure, but a table that has collapsed is.
MIN_SCOPES = 20

# The DEFAULT alias — the one a host has without configuring anything. Since phase
# E it is no longer the only value in the table. Not spelled — read from cairn, so
# a rename upstream reds this file rather than silently making the whole table
# unroutable.
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
        f"then "
        f"`ls -1d ~/.cache/subsystem-store*/*/ | xargs -n1 basename | sort -u | wc -l`. "
        f"🔴 BOTH halves of that pipeline matter and each fixes a DIFFERENT "
        f"miscount, in OPPOSITE directions. The trailing `*` on the ROOT is "
        f"needed because extra instances use SIBLING caches "
        f"(`subsystem-store-<alias>`), so the un-starred glob UNDER-counts on a "
        f"multi-instance host. The `basename | sort -u` is needed because a "
        f"scope present in two instances then appears TWICE, so the starred "
        f"glob alone OVER-counts by exactly the number of shared scopes — "
        f"measured 2026-09-18 on a two-instance host: 25 un-starred, 38 starred, "
        f"26 de-duplicated, against a 26-row table. An operator reads this "
        f"message only when the floor has already tripped, so a count that is "
        f"12 too high walks them toward adding rows that already exist — the "
        f"same 'fix the table' error this message warns against, larger and in "
        f"the other direction. Then fix the table — do not "
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


#: 🔴 THE ALIASES SOME HOST IS KNOWN TO HAVE A CONFIG FILE FOR. Adding a member is
#: only correct AFTER every host carries
#: `~/.config/subsystem-store/instances/<alias>.env`.
#:
#: 🔴 BE HONEST ABOUT WHAT THIS IS: a DECLARATION, not a measurement, and it CANNOT
#: CHECK ITS OWN STATED CONDITION. The instance files live on two hosts and CI has
#: none, so nothing here observes whether an alias is really configured. It is a
#: stand-in for the `drift-check.sh` `[routes]` arm described in this module's
#: docstring, which became reachable at phase E and has not been built.
#: **Do not read a green run as evidence that an alias is configured anywhere.**
#: What it does buy: a widening cannot be a silent side effect of re-pointing a
#: scope, because it has to appear here, next to this paragraph.
#:
#: 🔴 TWO DIRECTIONS IT DOES NOT COVER, NAMED SO THEY ARE NOT MISTAKEN FOR COVERED:
#:  1. REMOVAL. Retire `instances/<alias>.env` from the hosts and this set still
#:     names the alias, the suite still passes, and every scope routed there
#:     refuses `rc 11` fleet-wide. Membership here is not evidence of presence.
#:  2. MIGRATION. This says an alias is CONFIGURED; it says nothing about whether
#:     a scope's ENTRIES were copied to that store first. Re-point a scope whose
#:     entries are not there and the suite passes, while the client returns
#:     `scope-empty` / `status=scope-absent` at **rc 0** with reassuring prose —
#:     no refusal anywhere. That is this table's worst failure mode and it is
#:     currently guarded by NOTHING. Migrate first, verify, then flip.
#:     🔴 AND THE VARIANT THAT ACTUALLY FIRED, WHICH THE PARAGRAPH ABOVE DOES NOT
#:     DESCRIBE: total absence is the LOUD case — it at least yields `scope-empty`.
#:     The live one is PARTIAL STALENESS, which yields NO signal at all. A scope
#:     whose ref NAMES all match but whose BYTES are behind reads back rc 0, full
#:     entry list, clean banner, content silently missing. Measured 2026-09-18 on
#:     `civitai-gpu-fleet` — one of the scopes this very commit re-points — where
#:     two entries were behind by one bullet each, one of them a 🔴 record of a
#:     measured live security incident. **So a name-set comparison is NOT
#:     sufficient verification; compare BYTES.** A `revision` is the leading 16
#:     hex of the entry's own sha256, so this is checkable rather than opaque.
#:     🔴 AND IT DECAYS: until the table flips, writes still route to the old
#:     instance, so a verified scope goes stale again within hours (measured: 4
#:     diverged entries at 19:20Z, 13 by 22:26Z the same day). **Verification is
#:     only valid immediately before the `home-manager switch`, never at
#:     PR-authoring time.** The cutover procedure in
#:     `claudedocs/handoff-cairn-oss-multi-instance.md` carries the steps; CI
#:     cannot do any of this, because it has no store access.
#:
#: An earlier revision also pinned this set with `== {"personal", "civitai"}` in a
#: second test. That was deleted: with one source three lines above the assertion,
#: it compared a constant to its own literal, re-hardcoded the alias `PERSONAL`
#: deliberately does not spell, and duplicated the subset check below.
#:
#:   personal — `~/.config/subsystem-store/env`, the long-standing default.
#:   civitai  — `instances/civitai.env`, added 2026-09-18 (phase D) and verified
#:              present on BOTH hosts (0600, matching token fingerprint) BEFORE
#:              the first scope was re-pointed here.
CONFIGURED_ALIASES = {PERSONAL, "civitai"}


def test_every_scope_routes_to_a_CONFIGURED_instance(table):
    """🔴 SUPERSEDES `test_every_scope_routes_to_the_default_instance_today`.

    That test pinned every value to `personal` and told you not to edit the file —
    correct while exactly one instance existed, and phase B's closing condition.
    Phase D configured a second instance on both hosts, so the invariant it was
    standing in for is now expressible directly: a scope may name any alias some
    host actually has a config for, and nothing else.

    See `test_control_row_three_refuses_even_at_one_instance` below, which is the
    refusal this guard exists to prevent, watched happening.
    """
    unknown = sorted(
        f"{s} -> {a}" for s, a in table.items() if a not in CONFIGURED_ALIASES
    )
    assert unknown == [], (
        f"these scopes route to an alias no host is declared to have a config "
        f"for: {unknown}.\n"
        f"`Routing.alias_for` REFUSES such an entry immediately, on every host, "
        f"at any instance count — it does not wait for a second store to exist. "
        f"Add `~/.config/subsystem-store/instances/<alias>.env` to EVERY host "
        f"first, verify with `cairn routes`, and only then add the alias to "
        f"CONFIGURED_ALIASES above."
    )


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

    That is the entire reason a value may name only an alias some host has a
    config for, so it is asserted rather than trusted.
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
