#!/usr/bin/env python3
"""Gate on the THREE systemd user units that host `scripts/present/`.

    present-regen.service   oneshot — rebuild both page variants
    present-regen.timer     daily
    present-serve.service   static file server, bound to the workbench's own LAN
                            address and reachable only FROM the workbench

WHAT THIS FILE CAN AND CANNOT SEE
---------------------------------
It reads `nix/home.nix` as TEXT. It cannot evaluate the flake (the hermetic
sandbox has no network and no flake inputs), so it cannot tell you the unit
started, bound its port, or served a byte — that is the live verification in the
PR description, and the two claims are different.

🔴 AND IT CANNOT SEE THE FIREWALL AT ALL. `/etc/nixos/configuration.nix` is not
in this repo, so nothing below knows that 8900 is absent from
`networking.firewall.allowedTCPPorts` and that every off-host SYN is therefore
dropped. Measured from the laptop 2026-08-25: 22 OPEN, 443 OPEN, 8899 CLOSED,
8900 CLOSED — 8899 being `initiatives-viewer`, on the identical address, with
the identical gap. A same-host `curl` succeeds over `lo`, which the firewall
accepts unconditionally, and proves nothing about a second machine. That is the
current, deliberate scope: the off-workbench reader is served by the sanitized
portable export. Do not read a green run here as reachability evidence.

What it CAN pin is everything that is decided at authoring time and that goes
wrong silently:

  * the units exist, and all three are `serverMode`-gated (an ungated unit would
    be emitted on the laptop, where the LAN address is not assignable and the
    server would crash-loop — the failure that already bit `initiatives-viewer`)
  * both services carry `OnFailure = notify-failure@%n.service`, which is the
    OPERATOR half of the staleness contract. Without it a failed regeneration is
    silent and the reader's banner is the only signal, hours late.
  * the port does not collide with any other port DECLARED IN CODE under `nix/`
    — see the ledger's own header for exactly which declaration shapes that
    scan does and does not see, because the sentence that used to be here
    ("any OTHER port this host BINDS") was wider than the implementation
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

#: The workbench's own LAN address (eth0 — `ip -4 -o addr`; this constant, the
#: matching comment in nix/home.nix and serve.py's `DEFAULT_HOST` all said eth1,
#: and there is no eth1 on this host).
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
# WHAT THIS SCAN SEES — read this before quoting it as coverage.
#
# It reads every `nix/**/*.nix` file, STRIPS `#` COMMENTS, and collects three
# declaration shapes:
#
#   1. `"NAME_PORT=NNNN"`            — a systemd `Environment` literal
#   2. `HOST:NNNN`                   — but only for a host THIS MACHINE CAN BIND
#                                      (loopback, the workbench's own LAN and
#                                      nebula addresses). `192.168.50.94:6443`
#                                      is a homelab node and is excluded
#                                      structurally, not by a ledger entry.
#   3. `someNamePort = NNNN;`        — a nix binding interpolated into a
#                                      `--listen-addr`-style flag
#
# An earlier cut collected shape 1 ONLY, under a header claiming it covered
# "any OTHER port this host BINDS". It did not, and the gap was demonstrated
# rather than argued: changing `nix/observability.nix`'s alloy UI from
# `--server.http.listen-addr=127.0.0.1:12349` to `…:8900` — a real local-bind
# collision with present-serve — left the suite fully green. Shapes 2 and 3 are
# what closes that.
#
# 🔴 WHAT IT STILL CANNOT SEE, stated so nobody reads a green run as more than
# it is:
#
#   * a port bound by a program whose flag lives in `scripts/`, not `nix/`
#   * a port mentioned only in a COMMENT — deliberately: prose is not a bind,
#     and including it made the initiatives-viewer's own comment alias its unit
#   * TWO different units in ONE file binding the same loopback port. Labels are
#     `<file>::<host>`, so the two collapse into one claim and cancel out. The
#     `*_PORT=` env shape (1) does not have this blind spot, and that is the
#     shape the units in `nix/home.nix` actually use.
#   * anything the firewall does. `/etc/nixos` is not in this repo — see the
#     module docstring.
#
# An enumeration, not a pattern: every label the scan produces must be
# classified here, so adding a port tomorrow forces a decision rather than
# defaulting into an unchecked bucket. The same shape drift-check.sh uses for
# the settings.json key allowlist, and for the same reason.
# --------------------------------------------------------------------------- #

#: Addresses THIS HOST can bind. A `HOST:PORT` literal naming anything else
#: describes a port somewhere ELSE and is not a collision candidate.
BINDABLE_HOSTS = ("127.0.0.1", "0.0.0.0", "localhost", r"\[::1\]",
                  "192.168.50.250",   # workbench LAN, eth0
                  "10.42.0.30")       # workbench nebula
_HOSTPORT_RE = re.compile(
    r"(?<![\w.])(" + "|".join(BINDABLE_HOSTS) + r"):(\d{2,5})(?!\d)")
_ENVPORT_RE = re.compile(r'"([A-Z0-9_]*PORT)=(\d+)"')
_NIXPORT_RE = re.compile(r"^\s*([a-zA-Z][\w']*[Pp]ort[\w']*)\s*=\s*(\d{2,5});", re.M)

#: Ports THIS HOST BINDS, label -> the ONE thing that binds it. Two labels may
#: name the same owner (an env var and the flag that consumes it); two OWNERS on
#: one port is the crash-loop this ledger exists to catch.
LOCAL_BIND_CLAIMS = {
    "BROWSER_RECEIVER_PORT": "activity receiver (scripts/collector)",
    "BROWSER_BRIDGE_PORT": "browser-bridge loopback server",
    "DL_ROUTER_PORT": "dl-router sidecar",
    "INITIATIVES_VIEWER_PORT": "initiatives-viewer",
    "PRESENT_SERVE_PORT": "present-serve",                       # this change
    "nix/home.nix::127.0.0.1": "homelab-kube-tunnel SOCKS proxy",
    # 9090/3100 here are PLACEHOLDER URLs fed to `alloy validate` at build time
    # and 12349 is the real alloy UI bind. All three are loopback ports spoken
    # for by this file; over-claiming a loopback port costs a rename, and
    # under-claiming it is the collision.
    "nix/observability.nix::127.0.0.1": "obs-ship / alloy",
    "nix/observability.nix::nodeExporterPort": "node-exporter",
    "nix/graphical.nix::192.168.50.250": "k3s NodePort (clawgate bar pill)",
}

#: Ports of REMOTE services reached through a `kubectl port-forward` on an
#: ephemeral LOCAL port. They name a port in a cluster, not one here, so two
#: units naming the same value is correct and not a collision.
REMOTE_SERVICE_CLAIMS = {
    "RECAP_SERVICE_PORT": "homelab ns promptver, svc/vllm-recap:8000",
    "AGENT_PORT": "homelab ns devpod-initiatives, svc/…:18789",
}


def _strip_nix_comments(text: str) -> str:
    """Drop `#`-to-end-of-line, but not a `#` inside a string literal.

    Counting quotes to the left of the `#` is the cheap test and it is
    deliberately conservative: a mis-strip can only ever HIDE a declaration, and
    the positive controls below prove the real ones survive.
    """
    out = []
    for line in text.splitlines():
        i = 0
        while True:
            j = line.find("#", i)
            if j < 0:
                break
            if line.count('"', 0, j) % 2 == 0:
                line = line[:j]
                break
            i = j + 1
        out.append(line)
    return "\n".join(out)


def _declared_ports() -> dict[str, set[int]]:
    """Every port DECLARED IN CODE under nix/, by label. See the header."""
    found: dict[str, set[int]] = {}
    for path in sorted(NIX_DIR.rglob("*.nix")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        code = _strip_nix_comments(path.read_text(encoding="utf-8"))
        for name, val in _ENVPORT_RE.findall(code):
            found.setdefault(name, set()).add(int(val))
        for host, val in _HOSTPORT_RE.findall(code):
            found.setdefault(f"{rel}::{host}", set()).add(int(val))
        for name, val in _NIXPORT_RE.findall(code):
            found.setdefault(f"{rel}::{name}", set()).add(int(val))
    return found


def test_positive_control_the_port_scan_finds_the_known_ports():
    """A scan that found nothing would make the ledger and the collision check
    vacuously clean. One assertion per DECLARATION SHAPE — a regex that silently
    stopped matching one of the three would otherwise leave the other two
    carrying a claim about all of them."""
    found = _declared_ports()
    # shape 1 — the env literal
    assert found.get("INITIATIVES_VIEWER_PORT") == {8899}, found
    assert found.get("BROWSER_BRIDGE_PORT") == {8788}, found
    # shape 2 — the host:port literal that shape 1 alone could not see. This is
    # the exact declaration whose mutation to :8900 used to pass.
    assert 12349 in found.get("nix/observability.nix::127.0.0.1", set()), found
    assert found.get("nix/home.nix::127.0.0.1") == {1080}, found
    # shape 3 — the nix binding interpolated into --web.listen-address
    assert found.get("nix/observability.nix::nodeExporterPort") == {9101}, found
    assert len(found) >= 10, f"only {len(found)} labels found: {sorted(found)}"


def test_the_scan_ignores_ports_on_hosts_this_machine_cannot_bind():
    """Negative control for shape 2. `192.168.50.94:6443` is the homelab
    kube-apiserver and appears four times in nix/home.nix; treating it as a
    local claim would make the ledger a list of everything anyone ever dialled."""
    found = _declared_ports()
    assert not any("192.168.50.94" in label for label in found), sorted(found)
    assert not any(6443 in ports for ports in found.values()), {
        k: sorted(v) for k, v in found.items() if 6443 in v}


def test_the_comment_stripper_removes_prose_without_removing_code():
    """Both directions, because a stripper that ate everything would produce an
    empty scan that every assertion above is written to catch — and one that ate
    nothing would re-introduce the aliasing this ledger cannot represent.

    The prose case is real: nix/home.nix's initiatives-viewer comment block
    names `192.168.50.250:8899` in English. That is a description of a bind, not
    a bind, and attributing it to the file's label would make any future
    `192.168.50.250:<other>` mention read as a second owner.
    """
    src = HOME_NIX.read_text(encoding="utf-8")
    assert "192.168.50.250:8899" in src, (
        "the fixture this control depends on is gone — find another commented "
        "host:port in nix/home.nix or delete this test, do not weaken it")
    stripped = _strip_nix_comments(src)
    assert "192.168.50.250:8899" not in stripped
    assert "127.0.0.1:1080" in stripped, (
        "the SOCKS proxy's ExecStart is code, not a comment — the stripper is "
        "eating declarations and every scan above is now under-counting")


def test_every_declared_port_is_classified():
    """Two-way pin. An unclassified label fails; a classified label that names
    nothing in the tree fails too — an accounting entry describing nothing is
    how a ledger stops being evidence."""
    found = set(_declared_ports())
    classified = set(LOCAL_BIND_CLAIMS) | set(REMOTE_SERVICE_CLAIMS)
    assert found - classified == set(), (
        f"unclassified port declaration(s) {sorted(found - classified)} — add "
        "each to LOCAL_BIND_CLAIMS (this host binds it, mapped to the ONE thing "
        "that binds it) or REMOTE_SERVICE_CLAIMS (it names a port in a "
        "cluster). Defaulting is not on offer: an unclassified bind is exactly "
        "the collision this ledger exists to catch.")
    assert classified - found == set(), (
        f"classified but not declared anywhere under nix/: "
        f"{sorted(classified - found)}")


def test_no_two_locally_bound_ports_collide():
    """🔴 The whole point of the ledger. `ss -lptn` answers what is bound RIGHT
    NOW; this answers what two units would fight over after a reboot.

    Grouped by OWNER and not by label, so an env var and the flag that consumes
    it are one claim while two genuinely different programs on one port are two.
    """
    found = _declared_ports()
    by_port: dict[int, set[str]] = {}
    for label, owner in LOCAL_BIND_CLAIMS.items():
        for val in found.get(label, ()):
            by_port.setdefault(val, set()).add(owner)
    clashes = {p: sorted(o) for p, o in by_port.items() if len(o) > 1}
    assert not clashes, (
        f"two different things bind the same port: {clashes}. On this host that "
        "is not a warning — the second one to start fails to bind and the unit "
        "crash-loops, typically first noticed after a reboot.")
    assert by_port.get(8900) == {"present-serve"}, (
        f"present-serve is not the sole claimant of 8900: {by_port.get(8900)}")


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


def _tracked_under(prefix: str) -> set[str] | None:
    """`git ls-files <prefix>`, or None when this tree has no git dir.

    🔴 IT IS None IN THE TIER THE MERGE IS GATED ON. `nix build
    .#checks.x86_64-linux.pytests` runs against a `cp -r ${./.}` STORE COPY with
    no `.git`, so `git ls-files` exits 128 there. The first cut used
    `check=True` and took the sandbox tier red with a `CalledProcessError` while
    the dev-host tier was green — the two-tier hazard, in a check written to
    catch a deploy hazard.
    """
    r = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", prefix],
                       capture_output=True, text=True)
    return set(r.stdout.split()) if r.returncode == 0 else None


def test_every_exec_start_path_is_present_in_the_tree_that_will_deploy():
    """🔴 CLAUDE.md: a NEW file must be `git add`ed or the flake silently omits
    it from the deploy. The switch SUCCEEDS and the file is simply not there —
    which for these two units means a unit that dies on the first tick with
    'No such file or directory', long after anyone is watching.

    NO SKIP, and each tier proves the same thing by a different route:

      * dev host — the tree is a git checkout, so `git ls-files` answers
        directly and the existence check is the weaker half.
      * nix sandbox — the tree IS the flake source, i.e. the git-tracked tree
        already materialised. An un-`git add`ed file is simply ABSENT there, so
        the existence check is not the weaker half: it is the same claim,
        evaluated by the build that will actually ship.
    """
    paths = _exec_start_repo_paths()
    assert paths, "the ExecStart scan found nothing — this check is vacuous"

    for rel in paths:
        assert (REPO_ROOT / rel).is_file(), (
            f"{rel} does not exist in this tree. On the dev host that means the "
            "path is wrong; in the nix sandbox it means the file was never "
            "`git add`ed, so the flake omitted it and the deploy will not carry it.")

    tracked = _tracked_under("scripts/present")
    if tracked is None:
        # The sandbox arm. Assert the reason rather than passing quietly — a
        # `None` that is really "git is broken on the dev host" must not read
        # as "correctly running in the sandbox".
        assert not (REPO_ROOT / ".git").exists(), (
            "`git ls-files` failed in a tree that HAS a .git — that is a broken "
            "git, not the sandbox, and this check silently degraded to an "
            "existence test without saying so.")
        return
    for rel in paths:
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
