#!/usr/bin/env python3
"""Gate on the THREE systemd user units that host `scripts/present/`.

    present-regen.service   oneshot — rebuild both page variants
    present-regen.timer     daily
    present-serve.service   static file server on the workbench LAN

WHAT THIS FILE CAN AND CANNOT SEE
---------------------------------
It reads `nix/home.nix` as TEXT. It cannot evaluate the flake (the hermetic
sandbox has no network and no flake inputs), so it cannot tell you the unit
started, bound its port, or served a byte — that is the live verification in the
PR description, and the two claims are different. What it CAN pin is everything
that is decided at authoring time and that goes wrong silently:

  * the units exist, and all three are `serverMode`-gated (an ungated unit would
    be emitted on the laptop, where the LAN address is not assignable and the
    server would crash-loop — the failure that already bit `initiatives-viewer`)
  * both services carry `OnFailure = notify-failure@%n.service`, which is the
    OPERATOR half of the staleness contract. Without it a failed regeneration is
    silent and the reader's banner is the only signal, hours late.
  * the port does not collide with any OTHER port this host BINDS, measured
    against the declared set rather than one `ss` reading
  * the bind address is the workbench's own, and is NOT the homelab node
  * the timer's schedule is what the comment claims, and the staleness threshold
    is wider than the cadence (a threshold at or below the cadence would banner
    every healthy page, and a banner that is always on is a banner nobody reads)
  * the two units agree on ONE artefact directory — the seam neither file owns
  * every path an ExecStart names is git-TRACKED. 🔴 CLAUDE.md, verbatim: a new
    file must be `git add`ed or the flake silently omits it from the deploy; the
    switch succeeds and the file is simply not there.

WHAT COUNTS AS REGRESSION COVERAGE HERE
---------------------------------------
Nothing. These units are new in the commit that adds this file. They are all
**INVARIANT GUARDS**. Each block that scans for something carries a positive
control, because a regex that matches nothing would satisfy every assertion
below by finding no violations.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOME_NIX = REPO_ROOT / "nix" / "home.nix"
NIX_DIR = REPO_ROOT / "nix"

UNITS = (
    "systemd.user.services.present-regen",
    "systemd.user.timers.present-regen",
    "systemd.user.services.present-serve",
)

#: The workbench's own LAN address (eth1).
WORKBENCH_LAN = "192.168.50.250"
#: 🔴 A homelab node — kube-apiserver and NodePorts. Not assignable here;
#: binding it crash-loops the unit.
HOMELAB_NODE = "192.168.50.94"


def _text() -> str:
    return HOME_NIX.read_text(encoding="utf-8")


def unit_block(attr: str) -> str:
    """The braced body of `<attr> = … {  … };`, by brace balance.

    Brace-balanced rather than line-based: a unit body contains `${…}`
    interpolations and nested attrsets, and an indentation heuristic gets the
    end of the block wrong in exactly the cases where it matters.
    """
    src = _text()
    m = re.search(rf"^\s*{re.escape(attr)}\s*=\s*", src, re.M)
    if not m:
        raise AssertionError(f"{attr} is not declared in nix/home.nix")
    i = src.index("{", m.end())
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
    raise AssertionError(f"unbalanced braces after {attr}")


def _env(block: str) -> dict[str, str]:
    """The unit's `Environment = [ "K=V" … ]` entries, as a dict."""
    return dict(
        m.groups() for m in re.finditer(r'"([A-Z_][A-Z0-9_]*)=([^"]*)"', block))


# --------------------------------------------------------------------------- #
# Controls
# --------------------------------------------------------------------------- #

def test_positive_control_the_block_extractor_finds_a_known_unit():
    """If `unit_block` returned an empty string every assertion below would be a
    substring check against nothing."""
    block = unit_block("systemd.user.services.initiatives-viewer")
    assert "INITIATIVES_VIEWER_PORT=8899" in block
    assert len(block) > 500


def test_positive_control_the_block_extractor_stops_at_the_right_brace():
    """A extractor that ran to the end of the file would make every
    'this unit contains X' assertion true for every X in home.nix."""
    block = unit_block("systemd.user.timers.present-regen")
    assert "OnCalendar" in block
    assert "PRESENT_SERVE_PORT" not in block, (
        "the timer's block bled into a neighbouring unit — the extractor is not "
        "stopping at its own closing brace")


# --------------------------------------------------------------------------- #
# The units exist and are gated
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("attr", UNITS)
def test_the_unit_is_declared(attr):
    assert unit_block(attr)


@pytest.mark.parametrize("attr", UNITS)
def test_the_unit_is_servermode_gated(attr):
    """Workbench-only, same rationale as initiatives-viewer: the LAN address is
    this host's, and the laptop is nebula-only."""
    src = _text()
    m = re.search(rf"^\s*{re.escape(attr)}\s*=\s*(.*)$", src, re.M)
    assert m and "lib.mkIf serverMode" in m.group(1), (
        f"{attr} is not gated on serverMode; it would be emitted on the laptop, "
        f"where {WORKBENCH_LAN} is not assignable and the unit crash-loops")


@pytest.mark.parametrize("attr", (
    "systemd.user.services.present-regen",
    "systemd.user.services.present-serve",
))
def test_the_service_toasts_on_failure(attr):
    """🔴 The OPERATOR half of the staleness contract. The reader gets the age
    banner; the operator gets this. Neither covers both audiences."""
    assert 'OnFailure = [ "notify-failure@%n.service" ];' in unit_block(attr), (
        f"{attr} has no OnFailure handler — a failed regeneration would be "
        "entirely silent until a reader noticed the banner")


# --------------------------------------------------------------------------- #
# 🔴 THE PORT LEDGER
#
# An enumeration, not a pattern: every `*_PORT` literal declared under nix/ must
# be classified here, so adding a port tomorrow forces a decision rather than
# defaulting into an unchecked bucket. The same shape drift-check.sh uses for
# the settings.json key allowlist, and for the same reason.
# --------------------------------------------------------------------------- #

#: Ports THIS HOST BINDS. These must be pairwise distinct — two units claiming
#: one port is a crash-loop that only shows up after a reboot.
LOCAL_BIND_PORT_VARS = {
    "BROWSER_RECEIVER_PORT",     # scripts/collector activity receiver
    "BROWSER_BRIDGE_PORT",       # scripts/browser-bridge loopback server
    "DL_ROUTER_PORT",            # scripts/dl-router sidecar
    "INITIATIVES_VIEWER_PORT",   # scripts/initiatives viewer
    "PRESENT_SERVE_PORT",        # this change
}

#: Ports of REMOTE services reached through a `kubectl port-forward` on an
#: ephemeral LOCAL port. They name a port in a cluster, not one here, so two
#: units naming the same value is correct and not a collision.
REMOTE_SERVICE_PORT_VARS = {
    "RECAP_SERVICE_PORT",        # homelab ns promptver, svc/vllm-recap:8000
    "AGENT_PORT",                # homelab ns devpod-initiatives, svc/…:18789
}


def _declared_port_vars() -> dict[str, set[int]]:
    found: dict[str, set[int]] = {}
    for path in sorted(NIX_DIR.rglob("*.nix")):
        for name, val in re.findall(r'"([A-Z0-9_]*PORT)=(\d+)"',
                                    path.read_text(encoding="utf-8")):
            found.setdefault(name, set()).add(int(val))
    return found


def test_positive_control_the_port_scan_finds_the_known_ports():
    """A scan that found nothing would make the ledger and the collision check
    vacuously clean."""
    found = _declared_port_vars()
    assert found.get("INITIATIVES_VIEWER_PORT") == {8899}, found
    assert found.get("BROWSER_BRIDGE_PORT") == {8788}, found
    assert len(found) >= 6, f"only {len(found)} port vars found: {sorted(found)}"


def test_every_declared_port_var_is_classified():
    """Two-way pin. An unclassified var fails; a classified var that names
    nothing in the tree fails too — an accounting entry describing nothing is
    how a ledger stops being evidence."""
    found = set(_declared_port_vars())
    classified = LOCAL_BIND_PORT_VARS | REMOTE_SERVICE_PORT_VARS
    assert found - classified == set(), (
        f"unclassified port var(s) {sorted(found - classified)} — add each to "
        "LOCAL_BIND_PORT_VARS (this host binds it) or REMOTE_SERVICE_PORT_VARS "
        "(it names a port in a cluster). Defaulting is not on offer: an "
        "unclassified bind is exactly the collision this ledger exists to catch.")
    assert classified - found == set(), (
        f"classified but not declared anywhere under nix/: "
        f"{sorted(classified - found)}")


def test_no_two_locally_bound_ports_collide():
    """🔴 The whole point of the ledger. `ss -lptn` answers what is bound RIGHT
    NOW; this answers what two units would fight over after a reboot."""
    found = _declared_port_vars()
    by_port: dict[int, set[str]] = {}
    for name in LOCAL_BIND_PORT_VARS:
        for val in found.get(name, ()):
            by_port.setdefault(val, set()).add(name)
    clashes = {p: sorted(n) for p, n in by_port.items() if len(n) > 1}
    assert not clashes, f"two units bind the same port: {clashes}"
    assert 8900 in by_port and by_port[8900] == {"PRESENT_SERVE_PORT"}, (
        f"PRESENT_SERVE_PORT is not the sole claimant of 8900: {by_port.get(8900)}")


# --------------------------------------------------------------------------- #
# The bind address
# --------------------------------------------------------------------------- #

def test_the_server_binds_the_workbench_and_never_the_homelab_node():
    block = unit_block("systemd.user.services.present-serve")
    env = _env(block)
    assert env.get("PRESENT_SERVE_HOST") == WORKBENCH_LAN, (
        f"expected the workbench's own eth1 address {WORKBENCH_LAN}, got "
        f"{env.get('PRESENT_SERVE_HOST')!r}")
    assert HOMELAB_NODE not in block, (
        f"{HOMELAB_NODE} is a homelab node (kube-apiserver / NodePorts). It is "
        "not assignable on this host and binding it crash-loops the unit — it "
        "already cost initiatives-viewer an outage.")


def test_the_serve_default_in_the_code_matches_the_unit():
    """The unit sets the address explicitly AND the code defaults to it. Two
    spellings of one fact drift; pin them together rather than hoping."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from present import serve  # noqa: PLC0415
    env = _env(unit_block("systemd.user.services.present-serve"))
    assert serve.DEFAULT_HOST == env["PRESENT_SERVE_HOST"]
    assert str(serve.DEFAULT_PORT) == env["PRESENT_SERVE_PORT"]


def test_the_page_is_not_wired_into_the_public_gateway():
    """🔴 Deliberately LAN/nebula-only, exactly like initiatives-viewer. The
    off-LAN reader is served by the portable SANITIZED export, not by public
    hosting. A `zacx.dev` hostname anywhere in these units would mean that
    decision was reversed without being argued."""
    for attr in UNITS:
        block = unit_block(attr)
        assert "zacx.dev" not in block, (
            f"{attr} names a public hostname. Public exposure is a separate, "
            "explicit choice — and it would be a choice about the sanitized "
            "copy only.")


# --------------------------------------------------------------------------- #
# The schedule, and its relationship to the staleness threshold
# --------------------------------------------------------------------------- #

def test_the_timer_fires_daily_and_catches_up_a_missed_run():
    block = unit_block("systemd.user.timers.present-regen")
    assert 'OnCalendar = "*-*-* 05:00:00";' in block, (
        "the claimed cadence is daily at 05:00 — the staleness threshold below "
        "is derived from it, so a change here is a change there")
    assert "Persistent = true;" in block, (
        "without Persistent a run missed while the host was down is never made "
        "up, and the page silently ages past its own threshold")
    assert "RandomizedDelaySec = 600;" in block


def test_the_staleness_threshold_is_wider_than_the_cadence():
    """🔴 A RELATIONSHIP, not a constant.

    The threshold must EXCEED one cadence + the jitter, or a perfectly healthy
    page banners itself just before every scheduled run — and a banner that is
    always on is a banner nobody reads. It must also not be so wide that a whole
    extra scheduled run can be missed in silence, which is the failure being
    guarded against, only slower.
    """
    timer = unit_block("systemd.user.timers.present-regen")
    serve_env = _env(unit_block("systemd.user.services.present-serve"))
    threshold = int(serve_env["PRESENT_STALE_AFTER_SEC"])
    jitter = int(re.search(r"RandomizedDelaySec = (\d+);", timer).group(1))
    cadence = 86400  # daily, per the OnCalendar pinned above
    assert threshold > cadence + jitter, (
        f"threshold {threshold}s does not clear one cadence + jitter "
        f"({cadence + jitter}s) — every healthy page would banner itself")
    assert threshold < 2 * cadence, (
        f"threshold {threshold}s lets a SECOND scheduled run be missed before "
        "the reader is told anything")


def test_the_code_default_and_the_unit_agree_on_the_threshold():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from present import serve  # noqa: PLC0415
    env = _env(unit_block("systemd.user.services.present-serve"))
    assert str(serve.DEFAULT_STALE_AFTER) == env["PRESENT_STALE_AFTER_SEC"] or \
        serve.DEFAULT_STALE_AFTER == int(env["PRESENT_STALE_AFTER_SEC"]), (
        f"serve.py defaults to {serve.DEFAULT_STALE_AFTER}s and the unit sets "
        f"{env['PRESENT_STALE_AFTER_SEC']}s — a hand-run and the unit would "
        "disagree about what stale means")


# --------------------------------------------------------------------------- #
# 🔴 THE SEAM: the writer and the reader must name ONE directory
# --------------------------------------------------------------------------- #

def test_the_regen_and_the_server_agree_on_the_artefact_directory():
    """Each unit is correct in isolation while disagreeing about a path, and the
    symptom is a permanently 'absent' page served by a perfectly healthy timer.
    Neither unit owns this; the assertion does."""
    regen = _env(unit_block("systemd.user.services.present-regen"))
    serve_ = _env(unit_block("systemd.user.services.present-serve"))
    assert "PRESENT_ARTEFACT_DIR" in regen and "PRESENT_ARTEFACT_DIR" in serve_
    assert regen["PRESENT_ARTEFACT_DIR"] == serve_["PRESENT_ARTEFACT_DIR"], (
        f"the writer puts pages in {regen['PRESENT_ARTEFACT_DIR']} and the "
        f"server reads {serve_['PRESENT_ARTEFACT_DIR']}")


def test_the_artefacts_live_outside_the_working_tree():
    """A working tree is a place other sessions run `git checkout` in, and the
    server holds these files for weeks. Same reason the browser-bridge extension
    is unpacked under ~/.local/share."""
    d = _env(unit_block("systemd.user.services.present-serve"))["PRESENT_ARTEFACT_DIR"]
    assert d.startswith("%h/.local/share/"), d
    assert "workspace/devrc" not in d


# --------------------------------------------------------------------------- #
# 🔴 EVERY ExecStart PATH MUST BE GIT-TRACKED
# --------------------------------------------------------------------------- #

def _exec_start_repo_paths() -> list[str]:
    out = []
    for attr in UNITS:
        for m in re.finditer(r'ExecStart = "[^"]*?%h/workspace/devrc/([^"]+)"',
                             unit_block(attr)):
            out.append(m.group(1))
    return out


def test_positive_control_the_exec_start_scan_finds_both_entry_points():
    paths = _exec_start_repo_paths()
    assert set(paths) == {
        "scripts/present/run-regen.sh",
        "scripts/present/serve.py",
    }, paths


def test_every_exec_start_path_exists_and_is_git_tracked():
    """🔴 CLAUDE.md: a NEW file must be `git add`ed or the flake silently omits
    it from the deploy. The switch SUCCEEDS and the file is simply not there —
    which for these two units means a unit that dies on the first tick with
    'No such file or directory', long after anyone is watching."""
    paths = _exec_start_repo_paths()
    assert paths, "the ExecStart scan found nothing — this check is vacuous"
    tracked = set(subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "scripts/present"],
        capture_output=True, text=True, check=True).stdout.split())
    for rel in paths:
        assert (REPO_ROOT / rel).is_file(), f"{rel} does not exist"
        assert rel in tracked, (
            f"{rel} is NOT git-tracked. `git add` it — the flake builds from the "
            "tracked tree, so the switch will succeed and the file will be absent.")


def test_the_regen_trigger_covers_the_whole_present_package():
    """A spelled trigger (six file paths) goes stale the day a seventh module is
    added; a directory trigger does not. Structural over spelled."""
    block = unit_block("systemd.user.services.present-regen")
    assert 'X-Restart-Triggers = [ "${../scripts/present}" ];' in block


def test_the_server_is_static_no_credentials_no_subprocess():
    """The design claim, made checkable. Unlike initiatives-viewer — whose
    refresh button shells out and therefore needs sops and gh on its PATH — this
    unit needs neither, and a future edit that adds one should have to argue for
    it here."""
    block = unit_block("systemd.user.services.present-serve")
    for forbidden in ("pkgs.sops", "pkgs.gh", "pkgs.kubectl", "KUBECONFIG"):
        assert forbidden not in block, (
            f"present-serve names {forbidden}. It serves a file off disk: it has "
            "no refresh path, runs no subprocess and holds no credential. If "
            "that changed, the comment block above the unit is now wrong too.")
