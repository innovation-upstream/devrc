#!/usr/bin/env python3
"""Unit tests for `scripts/lib/host_label.py` — WHICH MACHINE IS THIS (#1601).

🔴 WHAT WAS BROKEN, AND WHY EVERY ASSERTION BELOW IS ABOUT A REFUSAL.
`local_host_label()` ended `return DEFAULT_LOCAL_HOST` — the literal
`"workbench"` — whenever neither `ACTIVITY_HOST` nor the collector's env file
supplied a valid label. On the LAPTOP in that state every consumer stamped the
laptop's data `workbench`, silently, exit 0. For `scripts/peer-host`, a ROUTING
tool, that is a WRONG ANSWER rather than a refusal: the local leg files the
laptop's registry under `workbench` while the `laptop` leg SSHes to itself.

🔴 HERMETIC BY CONSTRUCTION, AND THE HERMETICITY IS THE HARD PART HERE.
This suite runs on ONE OF THE TWO REAL MACHINES the module is about, so the
module's own third feeder — "do I hold this address" — will happily answer
truthfully in the middle of a test and make an assertion pass (or fail) for a
reason that has nothing to do with the code. Two autouse fixtures prevent it:

  1. `_no_real_env` deletes `ACTIVITY_HOST` and repoints `ACTIVITY_ENV` at a
     path under `tmp_path` that does not exist.
  2. `_no_real_addresses` sets `HOST_LABEL_ADDRS=""` — set-but-empty, i.e. "this
     machine holds none of the known addresses".

Every test that WANTS an address signal injects one explicitly (`holds=`,
`addrs=`, or its own `HOST_LABEL_ADDRS`), so no test's verdict depends on which
machine ran it. `test_the_hermeticity_fixtures_are_installed` is the POSITIVE
CONTROL on both — a guard nobody has watched work is not a guard.

🔴 FIXTURE VALUES ARE PAIRWISE DISTINCT AND DISTINCT FROM EVERY REAL CONSTANT.
The synthetic addresses are drawn from TEST-NET-1/2/3 (`192.0.2.x`,
`198.51.100.x`, `203.0.113.x`, RFC 5737) and the synthetic labels are `alpha` /
`beta` / `gamma`. None of them is a real fleet address or a real host name, so a
mutant that hardcodes `"workbench"`, `"laptop"`, `10.42.0.30` or `192.168.50.250`
cannot survive by accidentally equalling a fixture — and a test that only ever
used the real four could not tell "read the table" from "printed the literal".

🔴 WHICH TESTS ARE REGRESSION COVERAGE, AND AGAINST WHICH BASE. `RED_AT_BASE`
names the tests that fail at `origin/main` (af943906/e1b6a9e3) BECAUSE OF THE
DEFECT — not merely because a new symbol does not exist there.
`GAP_CLOSERS_PROVEN_BY_MUTATION` names the tests that close a hole this branch
LEFT, whose sensitivity is demonstrated by a named mutant rather than by a base
revision. Everything else in this file is an INVARIANT GUARD: it pins behaviour
that was already correct, or behaviour of code this branch introduced, and is
NOT counted as evidence that a bug was fixed. The distinction is written down
because a guard that never could have gone red reads as coverage and provides
none.

The MUTATION MATRIX this suite was checked against is `MUTATION_MATRIX` at the
bottom of this file: each mutant, and the test that kills it WITH ITS OWN
assertion.
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
_HOST_LABEL_PY = _SCRIPTS / "lib" / "host_label.py"
_HOST_ROLE_SH = _SCRIPTS / "lib" / "host-role.sh"
_BROWSER_BRIDGE = _SCRIPTS / "browser-bridge" / "server.py"

sys.path.insert(0, str(_SCRIPTS / "lib"))
import host_label as hl  # noqa: E402


#: Red at `origin/main` because of the DEFECT, i.e. because the module answered
#: `"workbench"` where it should have derived or refused. Measured, not assumed
#: — the matrix is in the PR body.
RED_AT_BASE = {
    "test_nothing_determines_it_REFUSES_instead_of_saying_workbench",
    "test_an_invalid_label_with_no_other_signal_REFUSES",
    "test_the_address_identifies_the_laptop_when_env_and_file_are_silent",
    "test_a_stated_label_the_address_contradicts_RAISES",
    "test_the_shell_entry_point_prints_nothing_and_exits_nonzero_on_a_refusal",
}

#: 🔴 A THIRD CATEGORY, AND IT EXISTS BECAUSE NEITHER OF THE TWO ABOVE WOULD BE
#: HONEST FOR IT. `test_a_stated_WORKBENCH_on_a_machine_whose_ADDRESS_says_laptop
#: _RAISES` covers the #1601 DIRECTION — a machine told it is the workbench while
#: its address says laptop — which is what this repo's own `.env.example` used to
#: provision onto a fresh host, and which nothing tested. Measured:
#:
#:   at HEAD                  GREEN — the guard already covered both directions,
#:                            so it is not regression coverage for this branch
#:   at `origin/main`         the scenario is UNREPRESENTABLE: that module has no
#:                            address signal at all (`hasattr(m,
#:                            "address_host_label")` is False), and the seeded
#:                            `ACTIVITY_HOST=workbench` simply returns
#:                            'workbench' on any machine. The test file errors at
#:                            fixture setup there on a missing symbol, which is
#:                            NOT the "red because of the defect" RED_AT_BASE
#:                            claims, so it is deliberately not listed above.
#:   under MUT-2b             RED, on this test's own assertion. Narrowing the
#:                            conflict guard to one direction left the suite
#:                            fully green before this test existed — 25 passed
#:                            when the round-0 audit measured it, re-measured at
#:                            28 passed with only this test removed.
#:
#: That mutation IS the sensitivity evidence. Listing the test as regression
#: coverage it cannot provide would be the inflation `RED_AT_BASE` exists to
#: prevent; leaving it unlabelled would read as an invariant guard, which
#: undersells it.
GAP_CLOSERS_PROVEN_BY_MUTATION = {
    "test_a_stated_WORKBENCH_on_a_machine_whose_ADDRESS_says_laptop_RAISES":
        "MUT-2b conflict-check-fires-in-ONE-direction-only",
    # 🔴 SECTION 8's TWO. Both PASS at `1fae3fdf` — the round-1 base — so neither
    # is regression coverage, and both close a hole a mutant walks straight
    # through. `TWICE…` is the behavioural replacement for the SPELLED
    # idempotence guard section 7 used to carry: with the old grep's pattern
    # neutered to `ZZZ_NEVER_MATCHES` (MUT-F1-2, the auditor's own mutant, now a
    # row in the battery) every switch appended another line to a systemd
    # EnvironmentFile and the structural check stayed green. `…probe_ships_BOTH
    # _files` pins a store LAYOUT no test touched: drop `host-role.sh` and the
    # module silently degrades to the nebula-only PEER_SSH subset.
    "test_running_the_activation_TWICE_leaves_exactly_one_ACTIVITY_HOST_line":
        "MUT-F1-2 idempotence check neutered (the old grep)",
    "test_the_built_activation_is_the_DEPLOYED_one_and_its_probe_ships_BOTH_files":
        "MUT-F1-3 probe ships host_label.py without host-role.sh",
}

#: 🔴 ROUND-1 REGRESSION COVERAGE, AND AGAINST A DIFFERENT BASE FROM `RED_AT_BASE`.
#: These fail at `1fae3fdf` — the round-0 fix commit, i.e. the tree these defects
#: were found in — not at `origin/main`, where the activation block is a different
#: shape entirely. Measured by copying THIS file into a detached worktree at that
#: commit: 6 failed, 34 passed (85 passed with test_transcript_push.py included).
#: The two bases are kept apart deliberately; collapsing them would let a test
#: that only ever saw the newer defect read as coverage for the original one.
RED_AT_ROUND1_BASE = {
    # F1-1, DATA LOSS: `>>` onto a file with no trailing newline extended
    # `CLICKHOUSE_PASSWORD=s3cret` into `…s3cretACTIVITY_HOST=workbench`.
    "test_the_activation_appends_to_a_file_whose_last_line_has_NO_NEWLINE",
    # F1-5 (three of the four rows): the activation's own grep and the module
    # disagreed about what "states a label" means. The `an-export-prefix` row is
    # deliberately NOT here — both spellings agree on it, so it is the table's
    # own control that the parametrisation is not simply always red.
    "test_the_activation_and_the_MODULE_agree_on_what_states_a_label",
    # F1-6: a non-writable env file was skipped with rc 0 and EMPTY output.
    "test_a_NON_WRITABLE_env_file_is_reported_rather_than_skipped_in_silence",
    # F1-4: the module's diagnosis went to /dev/null and a fixed, undistinguished
    # cause was printed in its place.
    "test_the_activation_forwards_the_MODULES_OWN_reason_for_refusing",
}

#: 🔴 DELIBERATELY *NOT* IN `RED_AT_BASE`, AND THE OMISSION IS THE POINT.
#: `test_the_address_identifies_the_workbench_when_env_and_file_are_silent`
#: asserts the WORKBENCH case, and at `origin/main` that scenario ALREADY
#: returned `"workbench"` — from the hardcoded default, for entirely the wrong
#: reason. Measured side by side:
#:
#:   env+file silent, machine holds laptop's addresses   base 'workbench'  HEAD 'laptop'
#:   env+file silent, machine holds workbench's          base 'workbench'  HEAD 'workbench'
#:
#: So it is an INVARIANT GUARD, not regression coverage, and counting it as the
#: latter would inflate this file's claim by a test that a broken module passes.
PASSES_AT_BASE_FOR_THE_WRONG_REASON = {
    "test_the_address_identifies_the_workbench_when_env_and_file_are_silent",
}

#: Synthetic, deliberately unlike anything real. See the module docstring.
A_ADDR = "192.0.2.11"        # alpha, "primary"
A_ADDR2 = "198.51.100.22"    # alpha, "secondary"
B_ADDR = "203.0.113.33"      # beta,  "primary"
B_ADDR2 = "192.0.2.44"       # beta,  "secondary"
FAKE_TABLE = (("alpha", A_ADDR), ("beta", B_ADDR),
              ("alpha", A_ADDR2), ("beta", B_ADDR2))


def holds_only(*addrs):
    """A `holds` probe that claims exactly these addresses. Contacts nothing."""
    wanted = frozenset(addrs)
    return lambda addr: addr in wanted


# =========================================================================== #
# Hermeticity harness
# =========================================================================== #
@pytest.fixture(autouse=True)
def _no_real_env(monkeypatch, tmp_path):
    monkeypatch.delenv("ACTIVITY_HOST", raising=False)
    monkeypatch.setattr(hl, "ACTIVITY_ENV", str(tmp_path / "absent-env"))


@pytest.fixture(autouse=True)
def _no_real_addresses(monkeypatch):
    monkeypatch.setenv(hl.HOST_LABEL_ADDRS_ENV, "")


def test_the_hermeticity_fixtures_are_installed(tmp_path):
    """POSITIVE CONTROL on both autouse fixtures.

    INVARIANT GUARD (it is about the harness, not the defect). Both halves are
    asserted by OBSERVING the module, not by re-reading the fixture: the env
    file must be absent AND the default probe must report nothing held, which
    together are the only reason the refusal tests below can mean anything.
    """
    assert not os.path.exists(hl.ACTIVITY_ENV)
    assert os.environ.get("ACTIVITY_HOST") is None
    # The DEFAULT probe — not an injected one — must see no address.
    assert hl.address_host_label() is None
    # …and it is the ENV that did that, not a broken probe: with the variable
    # unset the probe would reach the real machine, so this is the discriminator.
    assert os.environ[hl.HOST_LABEL_ADDRS_ENV] == ""


def test_the_default_probe_can_actually_see_an_address():
    """🔴 NEGATIVE CONTROL ON THE HERMETICITY ITSELF: prove the muted probe is
    muted, not merely wired to nothing.

    A `None` from `address_host_label()` above is indistinguishable between "the
    fixture silenced it" and "the address path never worked". Feeding
    `HOST_LABEL_ADDRS` a value that MUST match moves the answer, so the pair
    ("one under a populated env, none under the empty one") is reported rather
    than the zero alone.

    INVARIANT GUARD — it pins this branch's own new seam.
    """
    os.environ[hl.HOST_LABEL_ADDRS_ENV] = A_ADDR
    try:
        assert hl.address_host_label(addrs=FAKE_TABLE) == "alpha"
    finally:
        os.environ[hl.HOST_LABEL_ADDRS_ENV] = ""
    assert hl.address_host_label(addrs=FAKE_TABLE) is None


# =========================================================================== #
# 1. Precedence that must NOT change
# =========================================================================== #
def test_the_environment_still_wins():
    """INVARIANT GUARD — unchanged behaviour, pinned so the fix cannot move it."""
    f = Path(hl.ACTIVITY_ENV + "-file")
    f.write_text("ACTIVITY_HOST=laptop\n", encoding="utf-8")
    assert hl.local_host_label(env={"ACTIVITY_HOST": "workbench"},
                               env_file=str(f), holds=holds_only()) == "workbench"
    # …and case/whitespace are still normalised, not passed through.
    assert hl.local_host_label(env={"ACTIVITY_HOST": "  LapTop "},
                               env_file=str(f), holds=holds_only()) == "laptop"


def test_the_file_still_wins_over_the_address():
    """🔴 THE ADDRESS IS THE FALLBACK AND THE CROSS-CHECK, NEVER THE PRIMARY.

    INVARIANT GUARD. The file says `laptop`; the injected address table says the
    same, so there is no conflict — what is measured is that the FILE produced
    the answer, which the next test's mutant-visible variant confirms.
    """
    f = Path(hl.ACTIVITY_ENV + "-file")
    f.write_text("# comment\nCLICKHOUSE_URL=x\nACTIVITY_HOST='laptop'\n",
                 encoding="utf-8")
    assert hl.local_host_label(env={}, env_file=str(f),
                               addrs=(("laptop", A_ADDR),),
                               holds=holds_only(A_ADDR)) == "laptop"


def test_an_invalid_label_is_still_ignored_rather_than_passed_through():
    """A typo must never mint a third host. INVARIANT GUARD (pre-existing rule).

    `mars` in the env falls through to the FILE, which is valid, so the answer is
    the file's — not `mars`, and not a refusal.
    """
    f = Path(hl.ACTIVITY_ENV + "-file")
    f.write_text("ACTIVITY_HOST=workbench\n", encoding="utf-8")
    assert hl.local_host_label(env={"ACTIVITY_HOST": "mars"},
                               env_file=str(f), holds=holds_only()) == "workbench"


# =========================================================================== #
# 2. THE DEFECT: nothing determines it
# =========================================================================== #
def test_nothing_determines_it_REFUSES_instead_of_saying_workbench():
    """🔴 THE REGRESSION TEST FOR #1601. Red at `origin/main`, where this same
    call returned the string `"workbench"` on any machine at all.

    The assertion is deliberately BOTH halves — that it raises, AND that the
    exception is the specific `HostLabelUnresolved` rather than any error the
    code happens to hit first. A bare `pytest.raises(Exception)` would go green
    on an unrelated `AttributeError` and certify nothing.
    """
    with pytest.raises(hl.HostLabelUnresolved) as excinfo:
        hl.local_host_label(env={}, env_file=str(Path(hl.ACTIVITY_ENV)),
                            holds=holds_only(), addrs=FAKE_TABLE)
    # The message must name what to DO, not merely that it failed.
    assert "refusing to guess" in str(excinfo.value)
    assert "ACTIVITY_HOST" in str(excinfo.value)


def test_an_invalid_label_with_no_other_signal_REFUSES():
    """🔴 RED AT BASE. `not-a-real-host` + no file + no address returned
    `"workbench"`; the typo was ignored (correctly) and then replaced by a guess
    (the defect)."""
    with pytest.raises(hl.HostLabelUnresolved):
        hl.local_host_label(env={"ACTIVITY_HOST": "not-a-real-host"},
                            env_file=str(Path(hl.ACTIVITY_ENV)),
                            holds=holds_only(), addrs=FAKE_TABLE)


def test_the_workbench_default_is_GONE_as_a_name_too():
    """INVARIANT GUARD on the removal itself.

    Deleting the constant matters beyond the one `return`: a module-level
    `DEFAULT_LOCAL_HOST = "workbench"` is an invitation, and two consumers
    re-exported it. Its absence is what makes a re-introduction a compile-time
    failure at those consumers rather than a silent restoration.
    """
    assert not hasattr(hl, "DEFAULT_LOCAL_HOST")
    src = _HOST_LABEL_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    assigned = {t.id for node in ast.walk(tree)
                if isinstance(node, ast.Assign)
                for t in node.targets if isinstance(t, ast.Name)}
    assert "DEFAULT_LOCAL_HOST" not in assigned


# =========================================================================== #
# 3. THE FIX: derive from an address this machine holds
# =========================================================================== #
def test_the_address_identifies_the_laptop_when_env_and_file_are_silent():
    """🔴 RED AT BASE — this returned `"workbench"` on the laptop, which is the
    whole issue. Driven through the REAL table (host-role.sh's), because the
    laptop's real address is the value the fleet actually routes on."""
    table = hl.host_addrs()
    laptop_addrs = [a for lbl, a in table if lbl == "laptop"]
    assert laptop_addrs, "the shared table knows no laptop address"
    for addr in laptop_addrs:
        assert hl.local_host_label(env={},
                                   env_file=str(Path(hl.ACTIVITY_ENV)),
                                   holds=holds_only(addr)) == "laptop", addr


def test_the_address_identifies_the_workbench_when_env_and_file_are_silent():
    """INVARIANT GUARD — and it is NOT regression coverage; see
    `PASSES_AT_BASE_FOR_THE_WRONG_REASON`.

    The old code returned `"workbench"` for this scenario too, from its
    hardcoded default. Pinning it anyway is what stops a future "just default to
    workbench again" from looking correct on one of the two machines — but it
    could never have gone red at base, so it is not counted as evidence.
    """
    table = hl.host_addrs()
    wb_addrs = [a for lbl, a in table if lbl == "workbench"]
    assert wb_addrs
    for addr in wb_addrs:
        assert hl.local_host_label(env={},
                                   env_file=str(Path(hl.ACTIVITY_ENV)),
                                   holds=holds_only(addr)) == "workbench", addr


def test_holding_BOTH_of_one_hosts_addresses_is_ordinary_not_a_conflict():
    """🔴 THE CASE EVERY REAL MACHINE IS IN, AND THE ONE A NAIVE FIX BREAKS.

    Measured 2026-09-12: the workbench holds `192.168.50.250` AND `10.42.0.30`;
    the laptop holds `10.42.0.100` (and would hold `192.168.50.155` on the LAN).
    A multi-host guard written without the per-label dedupe fires on both real
    machines — i.e. the fix would refuse everywhere. INVARIANT GUARD on this
    branch's own code, and the killer for MUT-4.
    """
    assert hl.address_host_label(addrs=FAKE_TABLE,
                                 holds=holds_only(A_ADDR, A_ADDR2)) == "alpha"
    real = hl.host_addrs()
    for host in hl.HOST_NAMES:
        mine = [a for lbl, a in real if lbl == host]
        assert len(mine) >= 2, f"{host} should have a LAN and a nebula address"
        assert hl.address_host_label(addrs=real, holds=holds_only(*mine)) == host


def test_holding_TWO_hosts_addresses_REFUSES_rather_than_picking_one():
    """INVARIANT GUARD, and a deliberate DIVERGENCE from `host-role.sh`.

    `detect_role` documents that a list carrying both hosts' primaries resolves
    to `workbench` — right for a converger, wrong for a router. Reachable in
    production only via `net.ipv4.ip_nonlocal_bind=1` (measured `0` on both
    hosts) or a misconfigured interface, and reachable here by injection.
    """
    with pytest.raises(hl.HostLabelConflict) as excinfo:
        hl.address_host_label(addrs=FAKE_TABLE, holds=holds_only(A_ADDR, B_ADDR))
    assert "more than one host" in str(excinfo.value)


def test_a_stated_label_the_address_contradicts_RAISES():
    """🔴 RED AT BASE (the module had no cross-check at all).

    Both feeders are covered, because they are separate code paths: the env var
    and the file each get their own case, and the message must NAME the source
    so an operator knows which one to fix.
    """
    table = hl.host_addrs()
    wb = [a for lbl, a in table if lbl == "workbench"]

    # (a) the ENVIRONMENT lies. It is the higher-precedence feeder, so if the
    #     cross-check were applied only to the file this case would pass through.
    f = Path(hl.ACTIVITY_ENV + "-absent")
    with pytest.raises(hl.HostLabelConflict) as env_exc:
        hl.local_host_label(env={"ACTIVITY_HOST": "laptop"}, env_file=str(f),
                            addrs=table, holds=holds_only(*wb))
    assert "ACTIVITY_HOST" in str(env_exc.value), (
        "the conflict must name the ENV VAR that made the wrong claim")

    # (b) the FILE lies, with the environment silent — a separate code path.
    f2 = Path(hl.ACTIVITY_ENV + "-file")
    f2.write_text("ACTIVITY_HOST=laptop\n", encoding="utf-8")
    with pytest.raises(hl.HostLabelConflict) as file_exc:
        hl.local_host_label(env={}, env_file=str(f2), addrs=table,
                            holds=holds_only(*wb))
    assert str(f2) in str(file_exc.value), (
        "the conflict must name the FILE that made the wrong claim, not just "
        "that a conflict happened — the operator has to know which to edit")
    for both in ("'laptop'", "'workbench'"):
        assert both in str(file_exc.value), (
            "the message must carry BOTH the claim and the measurement; one "
            "alone does not tell you which is wrong")


def test_a_stated_WORKBENCH_on_a_machine_whose_ADDRESS_says_laptop_RAISES():
    """🔴 THE #1601 DIRECTION, AND IT WAS THE ONE DIRECTION NOTHING COVERED.

    The test above asserts the MIRROR — stated `laptop`, address says
    `workbench`. This asserts the direction the defect actually produces: a
    machine told it is the WORKBENCH while its own address says LAPTOP. That is
    what `scripts/collector/.env.example` used to bake into every freshly
    provisioned host (it read `ACTIVITY_HOST=workbench`, copied verbatim onto any
    machine lacking the file), and it is the exact shape of the silent
    wrong-answer #1601 closed: the laptop believing it is the workbench.

    🔴 PROOF THE GAP WAS REAL, NOT A SYMMETRY ARGUMENT. Narrowing the guard in
    `local_host_label` to

        if stated and derived and stated != derived and stated == "laptop":

    left the WHOLE suite green — 25 passed as the round-0 audit measured it, and
    re-measured here at 28 passed with this file's other new tests present and
    only THIS one removed — and `MUT-3` still SURVIVED under it. With this test
    present that narrowing fails HERE, on this test's own assertion; see `MUT-2b`
    in `scripts/tests/mutants-host-label.sh`.

    ⚠ NOT REGRESSION COVERAGE FOR THIS BRANCH, AND CLASSIFIED AS SUCH: it is
    GREEN at HEAD, and at `origin/main` the scenario cannot even be expressed.
    See `GAP_CLOSERS_PROVEN_BY_MUTATION` at the top of this file for the three
    measurements and why neither existing category fits.

    Both feeders get their own arm, because they are separate code paths and the
    higher-precedence one (the env var) is the one a guard applied only to the
    file would pass straight through.
    """
    table = hl.host_addrs()
    laptop = [a for lbl, a in table if lbl == "laptop"]
    assert laptop, "the shared table knows no laptop address"

    # (a) the ENVIRONMENT states `workbench`; the machine holds the LAPTOP's
    #     addresses.
    absent = Path(hl.ACTIVITY_ENV + "-absent")
    with pytest.raises(hl.HostLabelConflict) as env_exc:
        hl.local_host_label(env={"ACTIVITY_HOST": "workbench"}, env_file=str(absent),
                            addrs=table, holds=holds_only(*laptop))
    assert "ACTIVITY_HOST" in str(env_exc.value), (
        "the conflict must name the ENV VAR that made the wrong claim")
    for both in ("'workbench'", "'laptop'"):
        assert both in str(env_exc.value), (
            "the message must carry BOTH the claim and the measurement")

    # (b) the FILE states `workbench` — this is the provisioned-template case
    #     verbatim, and the reason F1's fix is a provisioning fix.
    f = Path(hl.ACTIVITY_ENV + "-seeded")
    f.write_text("ACTIVITY_HOST=workbench\n", encoding="utf-8")
    with pytest.raises(hl.HostLabelConflict) as file_exc:
        hl.local_host_label(env={}, env_file=str(f), addrs=table,
                            holds=holds_only(*laptop))
    assert str(f) in str(file_exc.value), (
        "the conflict must name the FILE that made the wrong claim — on a freshly "
        "provisioned host that file is the whole bug, and the operator has to be "
        "told which path to edit")
    for both in ("'workbench'", "'laptop'"):
        assert both in str(file_exc.value)


def test_a_stated_label_the_address_AGREES_with_is_returned():
    """INVARIANT GUARD — the cross-check must not refuse the normal case."""
    table = hl.host_addrs()
    for host in hl.HOST_NAMES:
        mine = [a for lbl, a in table if lbl == host]
        assert hl.local_host_label(env={"ACTIVITY_HOST": host},
                                   env_file=str(Path(hl.ACTIVITY_ENV)),
                                   addrs=table,
                                   holds=holds_only(*mine)) == host


# =========================================================================== #
# 4. ONE TABLE: host-role.sh owns the addresses
# =========================================================================== #
def _shell_ip_constants(text):
    """`{(LABEL, RANK): addr}` straight out of host-role.sh, parsed here with a
    DIFFERENT expression from the module's, so the two can disagree."""
    out = {}
    for m in re.finditer(r'^(\w+)_IP_(PRIMARY|SECONDARY)="([\d.]+)"\s*$',
                         text, re.MULTILINE):
        out[(m.group(1).lower(), m.group(2))] = m.group(3)
    return out


def test_the_address_table_is_host_role_shs_own():
    """🔴 THE ONE-TABLE GUARD. INVARIANT GUARD (this branch created the link).

    `host_label.py` must be READING host-role.sh, not carrying a second copy of
    the same four addresses. Checked by parsing the shell file with an
    INDEPENDENT regex written here and comparing: a module that had quietly
    reverted to literals would still pass an "is the value 10.42.0.30" test and
    fails this one only if the two genuinely disagree… so the stronger half is
    below — the module's table must also CHANGE when the file does.
    """
    shell = _shell_ip_constants(_HOST_ROLE_SH.read_text(encoding="utf-8"))
    assert shell, "host-role.sh's IP constants are no longer parseable at all"
    expected = tuple(
        [(h, shell[(h, "PRIMARY")]) for h in hl.HOST_NAMES if (h, "PRIMARY") in shell]
        + [(h, shell[(h, "SECONDARY")]) for h in hl.HOST_NAMES if (h, "SECONDARY") in shell]
    )
    assert hl.host_addrs() == expected
    # And the file really is the one next to the module.
    assert hl.HOST_ROLE_SH == str(_HOST_ROLE_SH)


def test_the_table_FOLLOWS_the_shell_file_rather_than_being_a_copy():
    """🔴 THE DISCRIMINATING HALF: change the file, the answer must move.

    A module holding hardcoded literals passes the equality test above whenever
    the literals happen to match. This one feeds a DIFFERENT file with synthetic
    hosts and requires the parse to produce THEM. INVARIANT GUARD.
    """
    fixture = Path(hl.ACTIVITY_ENV + "-role.sh")
    fixture.write_text(
        'WORKBENCH_IP_PRIMARY="192.0.2.101"\n'
        'WORKBENCH_IP_SECONDARY="192.0.2.102"\n'
        'LAPTOP_IP_PRIMARY="203.0.113.201"\n'
        'LAPTOP_IP_SECONDARY="203.0.113.202"\n',
        encoding="utf-8")
    hl._reset_host_addrs_cache()
    try:
        assert hl.host_addrs(str(fixture)) == (
            ("workbench", "192.0.2.101"), ("laptop", "203.0.113.201"),
            ("workbench", "192.0.2.102"), ("laptop", "203.0.113.202"))
    finally:
        hl._reset_host_addrs_cache()


def test_a_table_missing_a_WHOLE_HOST_fails_CLOSED():
    """🔴 LOSING EVERY ADDRESS OF ONE HOST IS THE OUTCOME THAT COULD MISLABEL.

    If host-role.sh loses the laptop's constants, a table holding only the
    workbench's would answer `workbench` for anything the workbench holds and
    nothing for the laptop — i.e. it would recreate the defect for one host. The
    parse must return `()` instead. INVARIANT GUARD.

    ⚠ SCOPE, AND THE DOCSTRING USED TO OVERSTATE IT. This name and this body are
    about a WHOLE HOST going missing, which is all the guard in
    `parse_host_addrs` checks. Losing ONE of a host's two constants yields a
    3-entry PARTIAL table that is NOT refused — measured, and pinned as its own
    behaviour by the test below. The old name (`..._a_HOST_fails_CLOSED`) and the
    old docstring ("a partial table is the one outcome forbidden") read as
    coverage of both and covered only this one.
    """
    assert hl.parse_host_addrs('WORKBENCH_IP_PRIMARY="192.0.2.101"\n') == ()
    assert hl.parse_host_addrs("") == ()
    assert hl.parse_host_addrs("nothing to see here") == ()
    # Not-an-address is not an address.
    assert hl.parse_host_addrs(
        'WORKBENCH_IP_PRIMARY="${SOME_VAR}"\nLAPTOP_IP_PRIMARY="10.0.0.1"\n') == ()


def test_a_reformatted_SINGLE_constant_degrades_to_a_PARTIAL_table_that_cannot_MISLABEL():
    """🔴 THE BEHAVIOUR THE 'FAILS CLOSED' COMMENTS DENIED, PINNED AS WHAT IT IS.

    Three comments (`parse_host_addrs`, the test above, `host-role.sh`) claimed a
    reformat of one of the four `*_IP_*` constants makes the parse refuse. It does
    not: the guard is per-HOST, so a trailing comment / an `export` prefix /
    single quotes / a templated value on ONE line drops that line and leaves a
    3-entry table. This test measures the drop AND the property that makes the
    drop harmless, so the corrected comments are machine-checked rather than
    merely reworded.

    THE SAFETY PROPERTY, IN BOTH DIRECTIONS:
      * every surviving entry is still a CORRECT (host, addr) pair, so a machine
        holding any of them is still named CORRECTLY — never as the other host;
      * a machine whose only remaining evidence was the dropped line is not
        named at all, and `local_host_label()` REFUSES.
    Degradation is refusal, never mislabel. INVARIANT GUARD.
    """
    # Four real reformats, each dropping the workbench's PRIMARY and nothing else.
    for label, body in (
        ("trailing comment",
         'WORKBENCH_IP_PRIMARY="192.0.2.101"  # the LAN one\n'),
        ("export prefix", 'export WORKBENCH_IP_PRIMARY="192.0.2.101"\n'),
        ("single quotes", "WORKBENCH_IP_PRIMARY='192.0.2.101'\n"),
        ("templated", 'WORKBENCH_IP_PRIMARY="${WB_IP:-192.0.2.101}"\n'),
    ):
        src = (body
               + 'WORKBENCH_IP_SECONDARY="192.0.2.102"\n'
                 'LAPTOP_IP_PRIMARY="203.0.113.201"\n'
                 'LAPTOP_IP_SECONDARY="203.0.113.202"\n')
        table = hl.parse_host_addrs(src)
        assert table == (("laptop", "203.0.113.201"),
                         ("workbench", "192.0.2.102"),
                         ("laptop", "203.0.113.202")), (
            f"{label}: expected a 3-entry PARTIAL table (the guard is per-HOST, "
            f"not per-constant), got {table}")

        # The workbench is still named CORRECTLY from what survived…
        assert hl.address_host_label(
            addrs=table, holds=holds_only("192.0.2.102")) == "workbench", label
        # …the laptop is unaffected…
        assert hl.address_host_label(
            addrs=table, holds=holds_only("203.0.113.201")) == "laptop", label
        # …and a machine holding ONLY the dropped address is not named at all,
        # which upstream is a refusal rather than a wrong answer.
        assert hl.address_host_label(
            addrs=table, holds=holds_only("192.0.2.101")) is None, label
        with pytest.raises(hl.HostLabelUnresolved):
            hl.local_host_label(env={}, env_file=str(Path(hl.ACTIVITY_ENV)),
                                addrs=table, holds=holds_only("192.0.2.101"))

    # POSITIVE CONTROL: the same four lines UNreformatted parse to all four, so
    # the 3-entry results above are the reformat's doing and not a parser that
    # cannot read this fixture at all.
    whole = ('WORKBENCH_IP_PRIMARY="192.0.2.101"\n'
             'WORKBENCH_IP_SECONDARY="192.0.2.102"\n'
             'LAPTOP_IP_PRIMARY="203.0.113.201"\n'
             'LAPTOP_IP_SECONDARY="203.0.113.202"\n')
    assert len(hl.parse_host_addrs(whole)) == 4


def test_an_unreadable_shell_file_degrades_to_the_PEER_SSH_subset_not_to_a_guess():
    """The documented fallback, and its SUBSET property. INVARIANT GUARD.

    `scripts/peer-host` splices this module into `python3 -` on the far host,
    where `__file__` is `'<stdin>'` and host-role.sh cannot be found. The
    fallback must be the nebula addresses this module already owns — never a
    default label, and never an address the shell file does not also carry.
    """
    hl._reset_host_addrs_cache()
    try:
        got = hl.host_addrs(str(Path(hl.ACTIVITY_ENV) / "no" / "such" / "file"))
    finally:
        hl._reset_host_addrs_cache()
    assert got == hl._peer_ssh_addrs()
    assert set(got) <= set(hl.host_addrs()), (
        "the fallback carries an address host-role.sh does not — it can now "
        "DISAGREE with the shared table instead of merely knowing less")


def test_peer_ssh_addresses_are_in_the_shared_table():
    """🔴 MECHANICAL AGREEMENT between this module's own `PEER_SSH` and
    host-role.sh. INVARIANT GUARD; fails if EITHER side moves an address."""
    table = set(hl.host_addrs())
    for label, addr, _user in hl.PEER_SSH:
        assert (label, addr) in table, (
            f"PEER_SSH says {label} is at {addr}, host-role.sh disagrees: "
            f"{sorted(table)}")


def test_browser_bridges_ip_table_agrees():
    """🔴 THE THIRD COPY, WHICH CANNOT BE DELETED — SO IT IS PINNED.

    `scripts/browser-bridge/server.py::_HOST_IP_ORDER` is a real duplicate of
    this table. It cannot import `host_label`: home-manager deploys it as a lone
    flattened symlink at `~/.config/browser-bridge/server.py` with no `lib/`
    sibling. Read by AST rather than by importing the module (it pulls in the
    bridge's own dependencies) and rather than by regex (which would match the
    address inside a comment). INVARIANT GUARD.
    """
    tree = ast.parse(_BROWSER_BRIDGE.read_text(encoding="utf-8"))
    found = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_HOST_IP_ORDER"
                        for t in node.targets)):
            found = ast.literal_eval(node.value)
    assert found is not None, "_HOST_IP_ORDER is gone from browser-bridge"
    # server.py stores (ip, label); this module stores (label, ip).
    assert {(lbl, ip) for ip, lbl in found} == set(hl.host_addrs()), (
        "browser-bridge's host IP table and host-role.sh's have drifted. Both "
        "decide WHICH MACHINE THIS IS, and a disagreement is silent at both "
        "ends. Fix the copy in server.py — host-role.sh is the owner.")


# =========================================================================== #
# 5. The probe itself
# =========================================================================== #
def test_the_bind_probe_answers_truthfully_about_THIS_machine():
    """🔴 THE ONE TEST THAT DELIBERATELY TOUCHES THE REAL MACHINE, and it is
    written so it says the same thing on BOTH hosts.

    It does not assert WHICH host this is. It asserts the probe agrees with an
    independently obtained fact: a loopback address IS held, and an address from
    TEST-NET-1 that nothing routes is NOT. That is the positive/negative control
    pair for the mechanism — without the positive half a probe wired to nothing
    would look identical to a correct one. INVARIANT GUARD.
    """
    assert hl._bind_holds_address("127.0.0.1") is True
    assert hl._bind_holds_address(A_ADDR) is False
    # …and the fact is obtainable a second way, so the probe is not its own
    # oracle: binding the same address through a plain socket must agree.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
    finally:
        sock.close()
    with pytest.raises(OSError):
        sock2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock2.bind((A_ADDR, 0))
        finally:
            sock2.close()


def test_the_probe_never_shells_out():
    """🔴 STRUCTURAL, AND IT IS LOAD-BEARING FOR THE DEPLOY, NOT STYLE.

    `nix/home.nix` gives the transcript-push unit a PATH of
    `bash coreutils curl gnused python3` — no `iproute2`, no `ip`. A probe that
    forked `ip -4 -o addr` would work in a login shell and fail in the unit,
    which is the class of bug that made every stored transcript row say `nixos`.
    INVARIANT GUARD.
    """
    tree = ast.parse(_HOST_LABEL_PY.read_text(encoding="utf-8"))
    imported = {n.names[0].name.split(".")[0]
                for n in ast.walk(tree) if isinstance(n, ast.Import)}
    imported |= {(n.module or "").split(".")[0]
                 for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    for banned in ("subprocess", "shutil", "shlex", "pty"):
        assert banned not in imported, (
            f"host_label.py imports {banned}: the units that call it carry no "
            f"shell tooling on PATH")
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    for banned in ("system", "popen", "spawnv", "execv", "fork"):
        assert banned not in called, f"host_label.py calls os.{banned}"


def test_the_env_seam_parses_both_separators_and_means_empty_when_empty():
    """INVARIANT GUARD on this branch's own test/ops seam. It is the mechanism
    every other suite uses to stay host-independent, so it gets its own test
    rather than being trusted implicitly."""
    assert hl._holds_from_env(f"{A_ADDR} {B_ADDR}")(A_ADDR) is True
    assert hl._holds_from_env(f"{A_ADDR},{B_ADDR}")(B_ADDR) is True
    assert hl._holds_from_env(f"{A_ADDR}")(B_ADDR) is False
    assert hl._holds_from_env("")(A_ADDR) is False
    # UNSET is different from SET-BUT-EMPTY: unset means "probe for real".
    prior = os.environ.pop(hl.HOST_LABEL_ADDRS_ENV, None)
    try:
        assert hl._default_holds() is hl._bind_holds_address
    finally:
        if prior is not None:
            os.environ[hl.HOST_LABEL_ADDRS_ENV] = prior
    os.environ[hl.HOST_LABEL_ADDRS_ENV] = ""
    assert hl._default_holds() is not hl._bind_holds_address


# =========================================================================== #
# 6. The shell entry point transcript-push.sh depends on
# =========================================================================== #
def _run_module(env_extra):
    env = dict(os.environ, **env_extra)
    env.pop("ACTIVITY_HOST", None)
    return subprocess.run([sys.executable, str(_HOST_LABEL_PY)],
                          capture_output=True, text=True, env=env, timeout=60)


def test_the_shell_entry_point_still_prints_a_BARE_label():
    """INVARIANT GUARD — `transcript-push.sh` captures this stdout as the host
    NAME (`HOST_NAME="$(python3 …)"`), so anything else on stdout becomes the
    host name."""
    table = hl.host_addrs()
    for host in hl.HOST_NAMES:
        mine = " ".join(a for lbl, a in table if lbl == host)
        out = _run_module({"HOST_LABEL_ENV_FILE": str(Path(hl.ACTIVITY_ENV)),
                           "HOST_LABEL_ADDRS": mine})
        assert out.returncode == 0, out.stderr
        assert out.stdout == host + "\n", repr(out.stdout)


def test_the_shell_entry_point_prints_nothing_and_exits_nonzero_on_a_refusal():
    """🔴 RED AT BASE: at `origin/main` this printed `workbench` and exited 0,
    so `transcript-push.sh`'s "A FAILURE HERE IS FATAL" guard could never fire
    for an unresolvable host — only for a missing module file.

    BOTH signals are asserted, because the consumer checks both
    (`if ! HOST_NAME="$(…)" || [ -z "$HOST_NAME" ]`), and a traceback on STDOUT
    would be captured as the host name.
    """
    out = _run_module({"HOST_LABEL_ENV_FILE": str(Path(hl.ACTIVITY_ENV)),
                       "HOST_LABEL_ADDRS": ""})
    assert out.returncode != 0
    assert out.stdout == "", (
        "stdout must stay EMPTY on a refusal: transcript-push.sh assigns it to "
        "HOST_NAME, so a diagnostic there becomes the host name")
    assert "host_label:" in out.stderr


# =========================================================================== #
# 7. THE REPO'S OWN PROVISIONING MUST NOT STATE A HOST
# =========================================================================== #
_ENV_TEMPLATE = _SCRIPTS / "collector" / ".env.example"
_HOME_NIX = _SCRIPTS.parent / "nix" / "home.nix"


def test_the_provisioning_template_STATES_NO_HOST():
    """🔴 A TEMPLATE CANNOT KNOW WHICH MACHINE IT LANDS ON, AND THIS ONE CLAIMED
    TO. `scripts/collector/.env.example` carried `ACTIVITY_HOST=workbench`, and
    `nix/home.nix`'s `home.activation.activityCollectorEnv` copies that file
    VERBATIM onto ANY host that lacks `~/.config/activity-collector/env`. So a
    fresh LAPTOP was provisioned with a stated label its own address contradicts.

    Before #1601 that was a wrong column. After #1601 it is an OUTAGE: the stated
    label is cross-checked against an address the machine holds, so every
    consumer raises `HostLabelConflict` — transcript-push every 5 minutes,
    tmux-reply-agent FAILED after 5 restarts, tmux-snapshot-push every 2 minutes,
    `peer-host` rc 4 — on an otherwise-healthy machine. Latent only because this
    laptop's file had been hand-edited years earlier; a reinstall, a new machine
    or `rm`ing that file arms it. NOTHING in `scripts/tests` or
    `scripts/collector` read this template's value before this test.

    🔴 CHECKED WITH THE MODULE'S OWN PARSER, not a regex written here. What makes
    the template dangerous is precisely what `_file_stated_label` would read out
    of it at runtime, so that is the question asked — one rule, not a second
    spelling of it that can drift from the first.
    """
    body = _ENV_TEMPLATE.read_text(encoding="utf-8")

    assert hl._file_stated_label(body) == "", (
        "the provisioning template states a host label. It is copied verbatim onto "
        "any machine that lacks the real file, so whichever label it names is WRONG "
        "on the other host — and since #1601 a wrong stated label is a refusal in "
        "every consumer, not a cosmetic error. Leave it unset and let the activation "
        "derive it; see nix/home.nix::activityCollectorEnv")

    # 🔴 POSITIVE CONTROL: the parser CAN read a label out of this exact body, so
    # the "" above is the template's doing and not a parser wired to nothing.
    assert hl._file_stated_label(body + "\nACTIVITY_HOST=laptop\n") == "laptop"
    # 🔴 AND A CONTROL ON THE FILE: it is still a real template with real
    # settings, so the "" is not the answer for an empty or vanished file.
    assert "CLICKHOUSE_URL=" in body and "ACTIVITY_BATCH_SIZE=" in body


def test_the_activation_DERIVES_the_label_instead_of_templating_one():
    """🔴 THE OTHER HALF OF F1: removing the value from the template is only safe
    if something supplies it. The collector stamps `host` from `ACTIVITY_HOST`
    and falls back to `hostname` — `nixos` on BOTH machines — so a template that
    states nothing and an activation that derives nothing would trade a
    conflicting label for a colliding one.

    The activation must therefore RUN `host_label.py`, which is also what keeps
    this to ONE source of truth: the same module every consumer reads, rather
    than a third place the fleet's identity is decided.

    Structural (the block's text), because the alternative is running
    `home-manager switch` — which is a deploy, not a test.
    """
    text = _HOME_NIX.read_text(encoding="utf-8")
    # 🔴 THE ASSIGNMENT, NOT THE NAME. `home.activation.activityCollectorEnv`
    # also appears inside a COMMENT in the `let` block (the `hostLabelProbe`
    # binding explains why it exists), and matching the bare name landed on that
    # comment — a block that contains neither the script nor its writes, so this
    # test failed for a reason that had nothing to do with the activation.
    start = text.index("home.activation.activityCollectorEnv = ")
    block = text[start:text.index("'';", start)]
    # POSITIVE CONTROL on the extraction: this must be the activation BODY, not
    # prose about it. Without this the assertions below could be measuring any
    # region of the file that happens to mention the name.
    assert 'envFile="$HOME/.config/activity-collector/env"' in block, (
        f"the extracted block is not the activation script: {block[:200]!r}")

    assert "host_label.py" in block, (
        "the activation no longer derives the host label. With the template "
        "stating none, nothing would set ACTIVITY_HOST and the collector would "
        "stamp `hostname` — `nixos` on both machines, which is the collision "
        "ACTIVITY_HOST exists to prevent")
    assert "ACTIVITY_HOST=" in block, (
        "the activation runs host_label.py but never writes ACTIVITY_HOST")
    # 🔴 APPEND, NEVER REWRITE — and that is ALL this assertion claims.
    #
    # It used to read `">>" in block and "grep" in block` under a message that
    # claimed the whole idempotence property ("…and only when the file states
    # none … A rewrite would clobber a hand-edited value on every switch"). That
    # was a SPELLED guard, not a structural one: measured, replacing the grep's
    # pattern with `^[ ]*ZZZ_NEVER_MATCHES=` — under which EVERY switch appends
    # another line to a systemd EnvironmentFile, forever — left this test GREEN
    # (1 passed, 28 deselected). A guard that reads as coverage while providing
    # none is worse than no guard, because it stops the next person looking.
    #
    # The idempotence claim is now BEHAVIOURAL and lives in section 8 below:
    # `test_running_the_activation_TWICE_leaves_exactly_one_ACTIVITY_HOST_line`
    # and `test_a_file_that_already_states_a_label_is_left_BYTE_IDENTICAL` run
    # the real nix-evaluated snippet in a throwaway `HOME`.
    assert ">>" in block, (
        "the activation must APPEND the derived label rather than rewrite the "
        "file; see section 8 for the behavioural idempotence guards")


# =========================================================================== #
# 8. THE ACTIVATION, RUN FOR REAL
# =========================================================================== #
# 🔴 WHY THIS SECTION EXISTS: A SOURCE-TEXT GREP CANNOT SEE WHAT THIS BLOCK DOES.
# Section 7 asserts that the activation MENTIONS things. Two defects lived
# happily underneath that: an `>>` onto a file with no trailing newline
# CONCATENATES onto the last line (mangling `CLICKHOUSE_PASSWORD` in a systemd
# `EnvironmentFile=`, then appending again on every later switch because the
# mangled line no longer states a label), and a neutered idempotence pattern
# left the structural guard fully green while every switch grew the file.
#
# So these run the REAL nix-evaluated snippet: the `''…''` literal is read out of
# `nix/home.nix` verbatim, evaluated by nix with the file's OWN `hostLabelProbe`
# binding (also read verbatim), built, and executed under a throwaway `HOME`.
# Nothing here touches the operator's real `~/.config/activity-collector/env`.
#
# ⚠ WHAT IT IS STILL NOT: a `home-manager switch`. The DAG placement
# (`entryAfter ["writeBoundary"]`) and home-manager's own `set -eu`/PATH are not
# under test; the tests run the script under `bash -eu` to approximate the
# latter. Section 7's structural check is what pins the block into the module
# system at all.
_ACTIVATION_ANCHOR = "home.activation.activityCollectorEnv = "
_PROBE_ANCHOR = "hostLabelProbe = pkgs.runCommandLocal"


def _nix_indented_string(text, anchor):
    """The `''…''` literal that follows `anchor` in a Nix file, verbatim."""
    start = text.index(anchor)
    opened = text.index("''", start)
    closed = text.index("'';", opened + 2)
    return text[opened:closed + 2]


@pytest.fixture(scope="module")
def activation_script(tmp_path_factory):
    """The activation block, EVALUATED BY NIX, as an executable file.

    🔴 BOTH halves come out of `nix/home.nix` rather than being retyped here.
    Retyping `hostLabelProbe` would have hidden exactly the defect its own
    comment warns about — a probe that ships `host_label.py` WITHOUT
    `host-role.sh` beside it — behind a fixture that always ships both.

    ⚠ ONE THING HERE IS NOT THE DEPLOYED ARTICLE: `pkgs` is `<nixpkgs>` (the
    channel), not the flake's pinned input, because `nix eval`ing the whole
    `homeConfigurations."zach"` to reach this one attribute costs minutes. The
    block's own text and its `hostLabelProbe` ARE the deployed ones; only
    `python3`/`coreutils` could differ. Measured 2026-09-13, they do not — both
    routes resolve `python3-3.14.7` to the same store path — but that is a fact
    about today's pins, not a guarantee, so if a mutant ever dies here for a
    reason that smells like a toolchain difference, this is why.
    """
    if shutil.which("nix-build") is None:
        pytest.fail(
            "nix-build not on PATH. These are the only tests that observe what "
            "the activation DOES rather than what it says; a skip here is how "
            "an env-file-corrupting append ships. Run under `nix develop` / "
            "scripts/gate.sh.")
    text = _HOME_NIX.read_text(encoding="utf-8")
    expr = (
        "let\n"
        "  pkgs = import <nixpkgs> { };\n"
        '  hostLabelProbe = pkgs.runCommandLocal "devrc-host-label-probe" { } %s;\n'
        'in pkgs.writeText "activity-collector-env-activation" (%s)\n'
        % (_nix_indented_string(text, _PROBE_ANCHOR),
           _nix_indented_string(text, _ACTIVATION_ANCHOR))
    )
    # cwd is `nix/` so the block's own relative paths (`../scripts/…`) resolve
    # the way they do in home.nix.
    p = subprocess.run(["nix-build", "--no-out-link", "-E", expr],
                       capture_output=True, text=True,
                       cwd=str(_SCRIPTS.parent / "nix"), timeout=1800)
    assert p.returncode == 0, (
        "evaluating the activation block failed:\n" + p.stdout + p.stderr)
    return Path(p.stdout.strip().splitlines()[-1])


def _addrs_of(label):
    """The addresses `host-role.sh` gives `label`, as HOST_LABEL_ADDRS wants them.

    Read from the module's own table, never typed here: a literal would pin this
    suite to the fleet's current IPs, and `test_peer_host.py` polices exactly
    that kind of second copy.
    """
    return " ".join(a for lbl, a in hl.host_addrs() if lbl == label)


def _run_activation(script, home, addrs, extra_env=None):
    """Run the built snippet under a throwaway `HOME`. Returns CompletedProcess."""
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
        # The module's address seam: "this machine holds exactly these".
        hl.HOST_LABEL_ADDRS_ENV: addrs,
    }
    env.update(extra_env or {})
    return subprocess.run(["bash", "-eu", str(script)],
                          capture_output=True, text=True, env=env, timeout=300)


def _env_file(home):
    return home / ".config" / "activity-collector" / "env"


def _seeded_home(tmp_path, body, mode=0o600):
    home = tmp_path / "home"
    (home / ".config" / "activity-collector").mkdir(parents=True)
    f = _env_file(home)
    f.write_text(body, encoding="utf-8")
    f.chmod(mode)
    return home


def _valid_label_lines(body):
    """Every line the MODULE would read as a valid `ACTIVITY_HOST=` statement."""
    return [ln for ln in body.splitlines() if hl._file_stated_label(ln)]


def test_the_built_activation_is_the_DEPLOYED_one_and_its_probe_ships_BOTH_files(
        activation_script):
    """POSITIVE CONTROL on the harness, plus the store-layout half of F1-3.

    Two ways this fixture could measure nothing: it could have built some other
    text, or it could have built a probe directory that does not contain the
    module the block runs. Both are checked against the repo's own bytes.

    🔴 AND THE SIBLING. `host_label.py` locates `host-role.sh` via `__file__`, so
    a probe holding only the `.py` degrades — silently, exit 0 — to the
    nebula-only `PEER_SSH` subset, which is wrong exactly when nebula is down.
    Every other test in this section would still pass in that state, because
    they inject `HOST_LABEL_ADDRS` and never reach the table.
    """
    body = activation_script.read_text(encoding="utf-8")
    assert 'envFile="$HOME/.config/activity-collector/env"' in body, (
        f"the built script is not the activation: {body[:200]!r}")

    probes = sorted({Path(tok).parent for tok in re.findall(
        r"/nix/store/[^\s\"']*devrc-host-label-probe[^\s\"']*", body)})
    assert len(probes) == 1, (
        f"expected exactly one host-label probe store path, found {probes}")
    probe = probes[0]
    for name in ("host_label.py", "host-role.sh"):
        shipped = probe / name
        assert shipped.is_file(), (
            f"{name} is missing from {probe}. host_label.py opens host-role.sh "
            "beside itself for the fleet's address table; without the sibling it "
            "falls back to the nebula-only PEER_SSH subset — right on the mesh, "
            "silently non-deriving off it")
        assert shipped.read_bytes() == (_SCRIPTS / "lib" / name).read_bytes(), (
            f"{shipped} is not the repo's scripts/lib/{name}")


def test_the_activation_appends_to_a_file_whose_last_line_has_NO_NEWLINE(
        activation_script, tmp_path):
    """🔴 DATA LOSS. `printf … >> "$envFile"` on a file that does not end in a
    newline EXTENDS the last line rather than adding one. Measured before the
    fix:

        before  CLICKHOUSE_PASSWORD=s3cret          (no trailing newline)
        after   CLICKHOUSE_PASSWORD=s3cretACTIVITY_HOST=workbench
                ACTIVITY_HOST=workbench

    This file is a systemd `EnvironmentFile=`, so the mangled line is the
    credential the collector authenticates with — it 401s and telemetry stops,
    silently — and the block prints its SUCCESS message while doing it. Note the
    second line: because the mangled line no longer states a label, the next
    switch appends again, unbounded.

    Reachable by the path `SECRETS.md` step 3 prescribes: provision off-mesh (no
    label appended), hand-edit `CLICKHOUSE_PASSWORD` in, switch again on-mesh.
    """
    secret = "CLICKHOUSE_PASSWORD=s3cret"
    home = _seeded_home(tmp_path, "CLICKHOUSE_URL=http://example/\n" + secret)
    out = _run_activation(activation_script, home, _addrs_of("workbench"))
    assert out.returncode == 0, out.stderr

    body = _env_file(home).read_text(encoding="utf-8")
    assert secret in body.splitlines(), (
        f"the append mangled the last line: {body!r}")
    assert _valid_label_lines(body) == ["ACTIVITY_HOST=workbench"], body


def test_running_the_activation_TWICE_leaves_exactly_one_ACTIVITY_HOST_line(
        activation_script, tmp_path):
    """🔴 THE IDEMPOTENCE CLAIM, MEASURED RATHER THAN SPELLED. Section 7 used to
    assert `">>" in block and "grep" in block` under a message claiming this
    property; with the grep's pattern neutered to `ZZZ_NEVER_MATCHES` that check
    stayed green while every switch appended another line to a systemd
    EnvironmentFile.

    Run 1 seeds from the template (which states no host) and derives. Run 2 must
    change NOTHING — asserted byte-for-byte, not by counting lines, so a rewrite
    that happens to produce the same number of lines is caught too.
    """
    home = tmp_path / "home"
    home.mkdir()
    first = _run_activation(activation_script, home, _addrs_of("workbench"))
    assert first.returncode == 0, first.stderr
    assert "derived ACTIVITY_HOST=workbench" in first.stdout, first.stdout
    after_one = _env_file(home).read_bytes()
    assert _valid_label_lines(after_one.decode()) == ["ACTIVITY_HOST=workbench"]

    second = _run_activation(activation_script, home, _addrs_of("workbench"))
    assert second.returncode == 0, second.stderr
    assert _env_file(home).read_bytes() == after_one, (
        "the second run changed the env file. Every `home-manager switch` would "
        "grow it: %r" % _env_file(home).read_text())
    assert "derived" not in second.stdout, (
        "the second run re-derived; it must not even look: " + second.stdout)


#: The four inputs on which the activation's old open-coded grep and the module
#: disagreed, with what each disagreement cost. `states` is the MODULE's answer,
#: which is the one every consumer acts on.
_DISAGREEMENT_TABLE = [
    # (id, file body, module sees a label?)
    ("an-invalid-value", "ACTIVITY_HOST=nixos\n", False),
    ("a-TAB-indented-line", "\tACTIVITY_HOST=laptop\n", True),
    ("a-space-after-the-equals", "ACTIVITY_HOST= laptop\n", True),
    ("an-export-prefix", "export ACTIVITY_HOST=laptop\n", False),
]


@pytest.mark.parametrize("case,body,states", _DISAGREEMENT_TABLE,
                         ids=[c for c, _, _ in _DISAGREEMENT_TABLE])
def test_the_activation_and_the_MODULE_agree_on_what_states_a_label(
        activation_script, tmp_path, case, body, states):
    """🔴 ONE RULE, ONE PLACE — AND THIS IS THE TABLE THAT PROVED THERE WERE TWO.

    The block used to ask `grep -qE '^[ ]*ACTIVITY_HOST=[^ ]'`; the module asks
    `line.strip().startswith("ACTIVITY_HOST=")` and then validates the value.
    They disagreed on all four rows below, and each disagreement had a cost:

      `ACTIVITY_HOST=nixos`        grep: stated  module: invalid → never repaired
      `\\tACTIVITY_HOST=laptop`     grep: none    module: laptop  → duplicate append,
                                                                   or a PERMANENT
                                                                   false "could not
                                                                   derive" message
      `ACTIVITY_HOST= laptop`      grep: none    module: laptop  → same
      `export ACTIVITY_HOST=…`     grep: none    module: ignored → a second,
                                                                   contradicting line

    The fix is not a cleverer grep — that mints a third spelling. The block ASKS
    the module (`--file-states-label`), so the table collapses by construction.

    🔴 IT ALSO CLOSES THE FIRST/LAST SPLIT. Python takes the FIRST valid
    `ACTIVITY_HOST=`; systemd's `EnvironmentFile=` takes the LAST assignment. That
    only bites when a file ends up holding two contradicting lines — which rows 2
    and 3 above could create. So every row asserts BOTH: at most one line the
    module reads as valid, and that line is the LAST `ACTIVITY_HOST` line in the
    file, i.e. the one systemd will use.
    """
    home = _seeded_home(tmp_path, body)
    before = _env_file(home).read_bytes()
    out = _run_activation(activation_script, home, _addrs_of("workbench"))
    assert out.returncode == 0, out.stderr
    after = _env_file(home).read_text(encoding="utf-8")

    if states:
        assert after.encode() == before, (
            "the file already states a label the module accepts, so the "
            f"activation must leave it byte-identical; got {after!r}")
        # 🔴 THE WHOLE OUTPUT, NOT A KEYWORD. A file that already states a label
        # is not the activation's business, so it must say NOTHING — and this is
        # the assertion that catches F1-4's permanent false message: at base,
        # rows 2 and 3 slipped past the grep, reached the derive step, hit
        # `HostLabelConflict` against their own stated label, and printed "could
        # not derive … re-run the switch on-network" on EVERY switch, forever, on
        # a healthy on-network host. A `"could not be derived" not in out.stdout`
        # check was green against that exact text — a spelled guard, walkable by
        # rewording.
        assert out.stdout.strip() == "", (
            "the file already states a label the module accepts, so the "
            "activation must print nothing at all; got: " + out.stdout)
    else:
        assert after != before.decode(), (
            "the file states no label the module accepts, so one must be "
            f"appended; got {after!r}")

    valid = _valid_label_lines(after)
    assert len(valid) == 1, (
        f"expected exactly one line the module reads as a valid label, got {valid}")
    last = [ln for ln in after.splitlines() if "ACTIVITY_HOST=" in ln][-1]
    assert last == valid[0], (
        "python reads the FIRST valid ACTIVITY_HOST and systemd the LAST "
        f"assignment; they disagree here — last={last!r} valid={valid[0]!r}")


def test_a_NON_WRITABLE_env_file_is_reported_rather_than_skipped_in_silence(
        activation_script, tmp_path):
    """🔴 A root-owned or `chmod 400` env file used to fall out of the whole
    block on `[ -w "$envFile" ]` with rc 0 and EMPTY output. The operator got no
    signal at all that `ACTIVITY_HOST` had not been set — and an unset
    ACTIVITY_HOST is the `hostname`-collision (`nixos` on both machines) this
    whole change exists to prevent."""
    home = _seeded_home(tmp_path, "CLICKHOUSE_URL=http://example/\n", mode=0o400)
    before = _env_file(home).read_bytes()
    out = _run_activation(activation_script, home, _addrs_of("workbench"))
    assert out.returncode == 0, out.stderr
    assert _env_file(home).read_bytes() == before
    assert "not writable" in out.stdout, (
        "a declined write must be announced; got stdout=%r stderr=%r"
        % (out.stdout, out.stderr))
    assert str(_env_file(home)) in out.stdout, (
        "the message must name the file the operator has to edit: " + out.stdout)


def test_the_activation_forwards_the_MODULES_OWN_reason_for_refusing(
        activation_script, tmp_path):
    """🔴 IT USED TO DISCARD THE DIAGNOSIS AND THEN STATE A CAUSE IT HAD NOT
    DISTINGUISHED. `2>/dev/null` plus a fixed "re-run the switch on-network"
    printed the same sentence for every refusal — including, measured on the
    workbench ON-network, a `HostLabelConflict` that re-running cannot fix.

    Two DIFFERENT refusals are exercised and the printed text must differ and
    must each carry the module's own words; a message that merely mentions the
    file would satisfy a single-case assertion while still being generic.
    """
    home = _seeded_home(tmp_path, "CLICKHOUSE_URL=http://example/\n")

    unresolved = _run_activation(activation_script, home, "")
    assert unresolved.returncode == 0, unresolved.stderr
    assert "cannot identify this machine" in unresolved.stdout, unresolved.stdout

    conflict = _run_activation(activation_script, home, _addrs_of("workbench"),
                               extra_env={"ACTIVITY_HOST": "laptop"})
    assert conflict.returncode == 0, conflict.stderr
    assert "refusing to name it" in conflict.stdout, conflict.stdout
    assert "the address it holds says" in conflict.stdout, conflict.stdout

    assert unresolved.stdout != conflict.stdout, (
        "both refusals print the same sentence, so the reason is not being "
        "forwarded: " + unresolved.stdout)
    # NEGATIVE CONTROL on the pair: neither refusal may have written anything.
    assert _valid_label_lines(_env_file(home).read_text()) == []


# =========================================================================== #
# MUTATION MATRIX
# =========================================================================== #
#: Each row: the mutation applied to `scripts/lib/host_label.py`, and the ONE
#: test whose OWN assertion fails because of it. Re-derivable with
#: `scripts/tests/mutants-host-label.sh`. Run under PYTHONDONTWRITEBYTECODE=1:
#: CPython validates a cached module on mtime-in-whole-SECONDS plus size, so a
#: same-length edit landing in the same second as the last import is invisible
#: and scores SURVIVED without ever executing.
MUTATION_MATRIX = {
    "MUT-1 unresolved returns 'workbench' instead of raising":
        "test_nothing_determines_it_REFUSES_instead_of_saying_workbench",
    "MUT-2 the stated/derived conflict check is dropped":
        "test_a_stated_label_the_address_contradicts_RAISES",
    # 🔴 THE GUARD'S OTHER HALF, AND THE ONE THE SUITE COULD NOT SEE. Narrowing
    # the conflict check to `… and stated == "laptop"` left the whole 25-test
    # suite GREEN before its killer existed — the #1601 direction (stated
    # workbench, address says laptop) was simply untested, and it is the
    # direction the repo's own provisioning produced.
    "MUT-2b the conflict check fires for only ONE of the two directions":
        "test_a_stated_WORKBENCH_on_a_machine_whose_ADDRESS_says_laptop_RAISES",
    # 🔴 EQUIVALENT MUTANT, KEPT ON PURPOSE. Swapping the two returns in
    # `local_host_label` is unobservable: the conflict guard above has already
    # refused the only input that distinguishes them (both set and DIFFERENT),
    # and in every other case one of the two is falsy. It survives, correctly,
    # and the row exists so the next reader who writes this mutant gets the
    # answer instead of re-deriving it.
    #
    # ⚠ IT IS NOT A TRIPWIRE ON THE CONFLICT GUARD, AND THIS ROW SAID IT WAS.
    # The claim ("a future change that weakens the conflict guard turns it red")
    # is false in BOTH available shapes, measured: with the guard REMOVED
    # in-source the battery aborts at its own red baseline before this line ever
    # runs, and with the guard NARROWED this mutant still SURVIVES and the
    # battery still prints ok. The guard's real tripwires are MUT-2 and MUT-2b,
    # each with a named killer above.
    "MUT-3 `derived` preferred over `stated` — EQUIVALENT, must SURVIVE":
        "(no killer, and NOT a tripwire: see mutants-host-label.sh)",
    "MUT-3b the FILE is preferred over the environment":
        "test_the_environment_still_wins",
    "MUT-4 the per-label dedupe in address_host_label is removed":
        "test_holding_BOTH_of_one_hosts_addresses_is_ordinary_not_a_conflict",
    "MUT-5 the multi-host refusal becomes `hits[0]`":
        "test_holding_TWO_hosts_addresses_REFUSES_rather_than_picking_one",
    "MUT-6 parse_host_addrs accepts a table missing a WHOLE HOST":
        "test_a_table_missing_a_WHOLE_HOST_fails_CLOSED",
    "MUT-7 host_addrs ignores host-role.sh and uses PEER_SSH always":
        "test_the_table_FOLLOWS_the_shell_file_rather_than_being_a_copy",
    # The guard's WIDTH: per-HOST (`any`), not per-rank (`all`). Three comments
    # claimed the latter; this row is what keeps the corrected wording honest.
    "MUT-11 the per-HOST address guard is widened to per-RANK":
        "test_a_reformatted_SINGLE_constant_degrades_to_a_PARTIAL_table_that_cannot_MISLABEL",
    "MUT-8 the bind probe returns True unconditionally":
        "test_the_bind_probe_answers_truthfully_about_THIS_machine",
    "MUT-9 __main__ prints the refusal to stdout and exits 0":
        "test_the_shell_entry_point_prints_nothing_and_exits_nonzero_on_a_refusal",
    "MUT-10 an invalid ACTIVITY_HOST is passed through instead of ignored":
        "test_an_invalid_label_is_still_ignored_rather_than_passed_through",
}

#: 🔴 THE SECOND MUTATION TARGET: `nix/home.nix`'s activation block, which is
#: where the round-1 findings actually lived. Same battery, same contract (the
#: named test must fail on its OWN assertion); `mutants-host-label.sh` gained a
#: `run_nix` for it and a comment-only negative control on that file too, since
#: section 8 nix-evaluates and RUNS the block rather than reading its text.
ACTIVATION_MUTATION_MATRIX = {
    "MUT-F1-5 --file-states-label answers with a substring rule (the old grep, "
    "moved into the module)":
        "test_the_activation_and_the_MODULE_agree_on_what_states_a_label",
    "MUT-F1-5b the --file-states-label exit code is inverted":
        "test_running_the_activation_TWICE_leaves_exactly_one_ACTIVITY_HOST_line",
    "MUT-F1-2 the idempotence check is the old grep with a never-matching pattern":
        "test_running_the_activation_TWICE_leaves_exactly_one_ACTIVITY_HOST_line",
    "MUT-F1-1 the trailing-newline guard before the append is removed":
        "test_the_activation_appends_to_a_file_whose_last_line_has_NO_NEWLINE",
    "MUT-F1-4 the module's stderr is sent to /dev/null again":
        "test_the_activation_forwards_the_MODULES_OWN_reason_for_refusing",
    "MUT-F1-6 a non-writable env file is declined in silence again":
        "test_a_NON_WRITABLE_env_file_is_reported_rather_than_skipped_in_silence",
    "MUT-F1-3 hostLabelProbe ships host_label.py without host-role.sh":
        "test_the_built_activation_is_the_DEPLOYED_one_and_its_probe_ships_BOTH_files",
    # 🔴 THE SAME MUTANT, SCORED AGAINST THE OTHER SUITE, BY HAND. The battery
    # only runs test_host_label_identity.py, so the DERIVED ledger in
    # test_transcript_push.py is not in its blast radius. Applied to a /tmp copy
    # of `scripts/` + `nix/`: baseline 1 passed, mutant 1 FAILED on its own
    # assertion ("hostLabelProbe copies ['host_label.py'] but host_label.py
    # hard-depends on ['host-role.sh'] beside itself").
    "MUT-F1-3 (ledger arm, run by hand — not in the battery)":
        "test_transcript_push.py::"
        "test_the_host_label_PROBE_ships_every_file_the_module_OPENS_BESIDE_ITSELF",
}
