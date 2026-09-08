"""Guards for `nix/system/apply-tailscale.sh` + `nix/system/check-tailscale.sh`.

These two are the pre-departure BACKUP REMOTE PATH: nebula is currently the only way
into the workbench, and the operator leaves the LAN for months. Nothing here can run the
apply path (it needs root and a real `nixos-rebuild`), so these tests pin the parts that
CAN be exercised without one, chosen for the defects that were actually found:

  * the two scripts' `tailscale up` commands must be the SAME STRING. They are printed
    by two different files -- the apply script as a next step, the checker as its "Fix:"
    -- and an operator who follows one and then runs the other must not be told to run a
    different command. This is where `--accept-dns=false` drifted onto the server role
    only, silently handing the travelling laptop's resolver to MagicDNS.
  * the checker's exit-code vocabulary and the apply script's handling of it must agree.
    They disagreed: the checker returned 1 for a freshly-switched node and the apply
    script rolled the config back for it, on the FIRST run, every time.
  * the freshly-switched state (`NeedsLogin`, no address) must be rc 4, not a FAIL, and
    must NOT print "keyexpiry : disabled" -- an absent `KeyExpiry` on an unauthenticated
    node means "no node key exists", not "expiry is off".
  * 🔴 and the MIRROR of that, which is the more expensive error: a node that HAD an
    identity and LOST it -- an expired or revoked node key -- must be rc 1, never rc 4.
    Keys expire at 180 days by default, shorter than the trip. The checker used to
    require BackendState and the address list to AGREE and to resolve disagreement to
    "never authenticated", so an expired node printed "THIS NODE HAS NEVER AUTHENTICATED
    ... the EXPECTED state immediately after apply-tailscale.sh" beside the very address
    it had retained, and exited 4: "no defect found", about a dead backup path.
  * the closure preflight's DOWNLOAD gate must fail CLOSED. `_drybuild_counts` used to
    return 0.0 MiB for any size string it could not parse -- indistinguishable from
    nothing to download -- so a 2400-path substitutable world rebuild passed all four
    gates in silence. The parser and the gate were each tested; the defect lived in the
    SEAM between them, which is now driven directly.
  * `--help` must print the comment header and stop -- not truncate it, not run past it
    into `set -euo pipefail`. The guard for that must NOT be built from the same
    "stop at the first non-# line" rule the implementation uses, or it cannot see the
    implementation truncate: one inserted blank line cost 56 of 75 help lines with both
    tests green.
  * `--role` as the final argument must SAY something rather than exit 1 in silence.

Both scripts' own `--self-test` suites are run here too, so their internal controls are
part of the gate rather than something a human has to remember to invoke.

    run:  pytest scripts/tests/test_tailscale_scripts.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from testlib.mockbin import write_exec  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
APPLY = REPO / "nix" / "system" / "apply-tailscale.sh"
CHECK = REPO / "nix" / "system" / "check-tailscale.sh"

SUBNET = "192.168.50.0/24"


def _run(*argv, **kw):
    return subprocess.run(
        [str(a) for a in argv], capture_output=True, text=True, timeout=180, **kw
    )


# 🔴 THE HEADER IS BOUNDED BY `set -euo pipefail`, NOT BY "the first non-# line" -- AND
# THAT DIFFERENCE IS THE WHOLE GUARD. The previous helper walked from the shebang and
# STOPPED AT THE FIRST NON-`#` LINE: the exact rule the scripts' own `--help` awk uses.
# A guard built from the implementation's rule cannot see the implementation truncate.
# MUTATION-PROVEN, against the then-75-line header at dd207064: inserting ONE blank line
# into check-tailscale.sh's header dropped `--help` from 75 lines to 19 -- losing the
# entire exit-code vocabulary, the thing an operator reads this text for -- and BOTH
# `--help` tests still passed, because `want` had been computed with the same truncating
# rule and shrank to match.
#
# So the boundary is read from a DIFFERENT fact: both files end their header at the
# literal `set -euo pipefail`. Everything between the shebang and that line must be a
# comment; a blank or code line in there is the truncation itself, and is reported as
# such rather than being silently absorbed into the expectation.
HEADER_END = "set -euo pipefail"

# State pins, not rule restatements. The floors sit under the current lengths (98 and
# 148) with room for ordinary editing. Their job is the case the structural check above
# would still call well-formed: a wholesale deletion of most of the header, every
# remaining line still a comment.
HELP_MIN_LINES = {"check-tailscale.sh": 70, "apply-tailscale.sh": 110}


def _header_comment_lines(path: Path) -> list[str]:
    """The header block, delimited by `set -euo pipefail` -- what `--help` must print."""
    lines = path.read_text().splitlines()
    assert HEADER_END in lines, (
        f"{path.name} has no bare `{HEADER_END}` line, so this test cannot locate the "
        "end of the header independently of the awk that prints it"
    )
    block = lines[1:lines.index(HEADER_END)]
    intruders = [(i + 2, ln) for i, ln in enumerate(block) if not ln.startswith("#")]
    assert not intruders, (
        f"{path.name}: line(s) {[n for n, _ in intruders]} between the shebang and "
        f"`{HEADER_END}` do not start with '#'. The `--help` awk stops at the first such "
        "line, so everything after it is silently dropped from the help text -- one "
        "blank line here cost check-tailscale.sh 56 of its then-75 lines, including its "
        f"whole exit-code vocabulary. Offending line(s): {[ln for _, ln in intruders]!r}"
    )
    return [re.sub(r"^# ?", "", ln) for ln in block]


# --------------------------------------------------------------------------------------
# the two scripts must tell the operator to run the SAME command
# --------------------------------------------------------------------------------------
def _up_commands(path: Path) -> set[str]:
    """Every `tailscale up ...` command line either script prints, normalised."""
    found = set()
    for line in path.read_text().splitlines():
        m = re.search(r"(sudo tailscale up [^\"']*)", line)
        if m:
            cmd = m.group(1).strip()
            # both files interpolate the subnet the same way; compare the resolved form
            cmd = cmd.replace("${SUBNET}", SUBNET)
            found.add(cmd)
    return found


def test_the_two_scripts_print_the_same_tailscale_up_commands():
    apply_cmds = _up_commands(APPLY)
    check_cmds = _up_commands(CHECK)
    assert apply_cmds, "found no `tailscale up` command in apply-tailscale.sh"
    assert check_cmds, "found no `tailscale up` command in check-tailscale.sh"
    assert apply_cmds == check_cmds, (
        "the apply script and the checker disagree about what the operator should run.\n"
        f"  only in apply: {sorted(apply_cmds - check_cmds)}\n"
        f"  only in check: {sorted(check_cmds - apply_cmds)}"
    )


def test_both_roles_keep_tailscale_out_of_the_host_resolver():
    """`--accept-dns=false` on BOTH roles, not just the subnet router.

    The reasoning ("dnsmasq and the .lan names already own this host's resolver") is
    identical on the two machines, and the asymmetric version pointed MagicDNS at the
    laptop -- the one machine that leaves the LAN and the one nobody can fix remotely.
    """
    cmds = _up_commands(APPLY)
    assert len(cmds) == 2, f"expected one command per role, got {sorted(cmds)}"
    server = [c for c in cmds if "--advertise-routes" in c]
    client = [c for c in cmds if "--advertise-routes" not in c]
    assert len(server) == 1 and len(client) == 1, sorted(cmds)
    assert "--accept-dns=false" in server[0], server[0]
    assert "--accept-dns=false" in client[0], (
        "the CLIENT role does not pass --accept-dns=false, so MagicDNS takes over the "
        f"laptop's resolver: {client[0]}"
    )


# --------------------------------------------------------------------------------------
# the exit-code vocabularies must agree
# --------------------------------------------------------------------------------------
def test_apply_handles_every_exit_code_the_checker_documents():
    """A code the checker can return that the apply script does not name is a rollback
    decision made by a `*)` fallthrough. rc 4 was born exactly there."""
    documented = set(
        re.findall(r"^#(?:\s+Exit:)?\s+([0-4]) = ", CHECK.read_text(), flags=re.M)
    )
    assert documented >= {"0", "1", "2", "3", "4"}, (
        f"check-tailscale.sh's header documents only {sorted(documented)}"
    )
    verify = APPLY.read_text().split("chk_rc=0", 1)[1].split("esac", 1)[0]
    handled = set()
    for m in re.finditer(r"^\s{2}([0-9|]+)\)", verify, flags=re.M):
        handled.update(m.group(1).split("|"))
    assert handled == documented, (
        "the apply script's post-switch `case` does not enumerate the same codes the "
        f"checker documents.\n  documented: {sorted(documented)}\n  handled:    "
        f"{sorted(handled)}"
    )


def test_the_not_yet_authenticated_code_is_not_a_rollback():
    """rc 4 must be an accepted outcome. This is the whole first-run bug in one line."""
    verify = APPLY.read_text().split("chk_rc=0", 1)[1].split("esac", 1)[0]
    branch = re.search(r"^\s{2}4\)(.*?)^\s{4};;", verify, flags=re.M | re.S)
    assert branch, "no `4)` branch in the post-switch case"
    assert "die " not in branch.group(1), (
        "rc 4 (configured, not yet authenticated) rolls back -- that is the state EVERY "
        "first run lands in, because this script deliberately does not run `tailscale up`"
    )
    branch1 = re.search(r"^\s{2}1\)(.*?)^\s{4};;", verify, flags=re.M | re.S)
    assert branch1 and "die " in branch1.group(1), (
        "rc 1 (a definitive node-side failure) must STILL roll back -- otherwise the "
        "guard was not fixed, it was removed"
    )


# --------------------------------------------------------------------------------------
# --help prints the header, all of it, and nothing after it
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("script", [APPLY, CHECK], ids=lambda p: p.name)
def test_help_prints_exactly_the_comment_header(script):
    want = _header_comment_lines(script)
    got = _run("bash", script, "--help").stdout.splitlines()
    assert got == want, (
        f"{script.name} --help does not match its own comment header "
        f"({len(got)} lines printed vs {len(want)} in the header). A hardcoded line "
        "range truncated one of these mid-paragraph and ran the other past the end."
    )
    assert HEADER_END not in "\n".join(got)
    # A minimum length, pinned as STATE. `got == want` above compares the printed text to
    # the file's own header; if BOTH shrink together it stays green, which is how the
    # truncation this test now guards went unseen. This assertion is not derived from the
    # file at all.
    floor = HELP_MIN_LINES[script.name]
    assert len(got) >= floor, (
        f"{script.name} --help printed only {len(got)} lines; it has never been shorter "
        f"than {floor}. Something truncated the header."
    )


def test_check_help_carries_the_whole_exit_code_vocabulary():
    """The exit codes are what an operator reads `--help` FOR, and they live at the very
    END of check-tailscale.sh's header -- so they are the first thing a truncation loses.
    Asserted against the PRINTED output, on the code's meaning and not just its digit."""
    got = _run("bash", CHECK, "--help").stdout
    for code, meaning in (
        ("0", "every claim holds"),
        ("1", "definitive node-side FAIL"),
        ("2", "cannot determine"),
        ("3", "ADMIN-CONSOLE action"),
        ("4", "INCOMPLETE"),
    ):
        # `Exit: 0 = ...` sits on the same line as the label, the rest are indented.
        assert re.search(rf"(?:^|\s){code} = ", got, flags=re.M), (
            f"`--help` does not document exit code {code}:\n{got}"
        )
        assert meaning in got, (
            f"`--help` documents exit {code} without saying '{meaning}':\n{got}"
        )


@pytest.mark.parametrize("script", [APPLY, CHECK], ids=lambda p: p.name)
def test_role_as_the_final_argument_says_so(script):
    r = _run("bash", script, "--role")
    assert r.returncode != 0
    assert "--role" in (r.stdout + r.stderr), (
        f"{script.name} exited {r.returncode} printing NOTHING when --role was given as "
        "the last argument (`shift 2` failing under `set -e`)"
    )


# --------------------------------------------------------------------------------------
# each script's own controls are part of the gate
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("script", [APPLY, CHECK], ids=lambda p: p.name)
def test_self_test_passes(script):
    r = _run("bash", script, "--self-test")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all controls passed" in r.stdout
    # POSITIVE CONTROL: a self-test that ran nothing also "passes".
    assert r.stdout.count("-> ok") > 10, (
        f"{script.name} --self-test reported success having asserted almost nothing:\n"
        + r.stdout
    )


# --------------------------------------------------------------------------------------
# the freshly-switched node, driven end to end against a fake `tailscale`
# --------------------------------------------------------------------------------------
def _fake_tailscale(
    tmp_path: Path, status: dict, prefs: dict | None, lan_route: bool = True
) -> dict:
    """A PATH containing fakes for everything the checker reads from the host.

    🔴 EVERY HOST INPUT IS STUBBED, INCLUDING THE ROUTING TABLE — and that is not
    tidiness. The first version stubbed only `tailscale` and `/proc`, and let the real
    `ip -4 -o route show` answer the LAN-route question. It passed here because this
    workbench genuinely has a route to 192.168.50.0/24, and went RED in Tekton, whose
    sandbox has neither the route nor `ip` — two tests, on a claim that had nothing to
    do with the routing table. A fixture that lets the host decide is blind to exactly
    the dimension it pins, in whichever tier happens to disagree.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    route = f"{SUBNET} dev eth0 proto kernel scope link src 192.168.50.250\n" if lan_route else ""
    write_exec(
        bindir / "ip",
        f'if [ "$*" = "-4 -o route show" ]; then printf %s "{route}"; exit 0; fi\n'
        '# the role probe: `ip -4 -o addr show nebula.mesh`. Silent -- every case here\n'
        '# passes --role explicitly, so an answer would be ignored anyway.\n'
        "exit 0\n",
    )
    # `systemctl is-active` is CONTEXT ONLY in the checker (no verdict is keyed on it),
    # but the sandbox has no systemd at all, so stub it rather than depend on the `||
    # true` that currently covers its absence.
    write_exec(bindir / "systemctl", 'echo active\nexit 0\n')
    (tmp_path / "status.json").write_text(json.dumps(status))
    if prefs is not None:
        (tmp_path / "prefs.json").write_text(json.dumps(prefs))
    # 🔴 `testlib.mockbin.write_exec` owns the shebang. A test-written stub carrying
    # `#!/usr/bin/env bash` execs on this NixOS host and ENOENTs in the nix build
    # sandbox, so the defect is invisible in the tier most people run. The bodies here
    # are POSIX sh for the same reason. `test_runtime_shebangs.py` caught this file
    # doing it by hand on its first gate run.
    write_exec(
        bindir / "tailscale",
        'case "$1 $2" in\n'
        f'  "status --json") cat "{tmp_path}/status.json"; exit 0 ;;\n'
        + (
            f'  "debug prefs") cat "{tmp_path}/prefs.json"; exit 0 ;;\n'
            if prefs is not None
            else '  "debug prefs") exit 1 ;;\n'
        )
        + "esac\nexit 1\n",
    )
    proc = tmp_path / "proc"
    (proc / "sys/net/ipv4").mkdir(parents=True)
    (proc / "sys/net/ipv6/conf/all").mkdir(parents=True)
    (proc / "sys/net/ipv4/ip_forward").write_text("1\n")
    (proc / "sys/net/ipv6/conf/all/forwarding").write_text("1\n")
    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["TS_PROC_ROOT"] = str(proc)
    return env


AUTHED_APPROVED = {
    "BackendState": "Running",
    "TailscaleIPs": ["100.100.10.5"],
    "Self": {"Online": True, "PrimaryRoutes": [SUBNET]},
}
NEVER_AUTHED = {
    "BackendState": "NeedsLogin",
    "TailscaleIPs": [],
    "Self": {"Online": False},
}
SERVER_PREFS = {"AdvertiseRoutes": [SUBNET], "RouteAll": False, "WantRunning": True}
NO_PREFS = {"AdvertiseRoutes": None, "RouteAll": False, "WantRunning": False}


def test_a_freshly_switched_node_is_rc_4_not_a_failure(tmp_path):
    env = _fake_tailscale(tmp_path, NEVER_AUTHED, NO_PREFS)
    r = _run("bash", CHECK, "--role", "server", env=env)
    assert r.returncode == 4, (
        "the state straight after apply-tailscale.sh (installed, running, never logged "
        f"in) must be rc 4, not rc {r.returncode}. rc 1 makes the apply script roll back "
        "the config it just installed.\n" + r.stdout + r.stderr
    )
    assert "FAIL:" not in r.stdout


def test_absent_key_expiry_on_an_unauthenticated_node_is_unknown(tmp_path):
    """The reassuring branch is the expensive one to guess: node-key expiry defaults to
    180 days, which is shorter than the trip, and the lapse is silent."""
    env = _fake_tailscale(tmp_path, NEVER_AUTHED, NO_PREFS)
    r = _run("bash", CHECK, "--role", "server", env=env)
    assert "PASS  keyexpiry" not in r.stdout, (
        "an absent KeyExpiry on a node that has never authenticated was resolved to "
        "'expiry disabled':\n" + r.stdout
    )
    assert "NODE KEY EXPIRY IS UNKNOWN" in r.stdout


def test_absent_key_expiry_on_an_authenticated_node_is_disabled(tmp_path):
    """The other direction -- without this the test above is satisfied by a checker that
    never says 'disabled' at all, and the operator can never reach rc 0."""
    env = _fake_tailscale(tmp_path, AUTHED_APPROVED, SERVER_PREFS)
    r = _run("bash", CHECK, "--role", "server", env=env)
    assert "PASS  keyexpiry : disabled" in r.stdout, r.stdout + r.stderr
    assert r.returncode == 0, r.stdout + r.stderr


def test_an_authenticated_but_broken_node_is_still_a_failure(tmp_path):
    """rc 1 must remain reachable. Splitting 'not yet authenticated' out of FAIL is only
    correct if what is left can still go red."""
    broken = {
        "BackendState": "Stopped",
        "TailscaleIPs": ["100.100.10.5"],
        "Self": {"Online": False, "PrimaryRoutes": [SUBNET]},
    }
    env = _fake_tailscale(tmp_path, broken, SERVER_PREFS)
    r = _run("bash", CHECK, "--role", "server", env=env)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL:" in r.stdout


# --------------------------------------------------------------------------------------
# 🔴 AN EXPIRED OR REVOKED NODE KEY IS A FAILURE, NOT "not yet authenticated"
#
# Node keys expire after 180 days by default -- shorter than the trip -- so this is the
# single most likely way the backup path dies while it is being relied on. The checker
# used to require BackendState and the address list to AGREE and to resolve every
# disagreement to "never authenticated", which is rc 4: "NOT YET DETERMINABLE (no defect
# found)". It printed "THIS NODE HAS NEVER AUTHENTICATED ... That is the EXPECTED state
# immediately after apply-tailscale.sh" beside the very 100.x address the node had
# retained. A checker that answers "no defect" when the path is dead is worse than none.
#
# ⚠ WHICH JSON SHAPE A REAL EXPIRY PRODUCES HAS NOT BEEN CAPTURED FROM A LIVE DAEMON, so
# both documented shapes are pinned: the address survives while the backend drops to
# NeedsLogin, and `Self.Expired: true` under a backend that still reads Running.
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "label,status",
    [
        (
            "logged out but the netmap address was RETAINED",
            {
                "BackendState": "NeedsLogin",
                "TailscaleIPs": ["100.100.10.5"],
                "Self": {"Online": False, "PrimaryRoutes": [SUBNET]},
            },
        ),
        (
            "NoState with the address retained",
            {
                "BackendState": "NoState",
                "TailscaleIPs": ["100.100.10.5"],
                "Self": {"Online": False, "PrimaryRoutes": [SUBNET]},
            },
        ),
        (
            "Self.Expired under a backend that still says Running",
            {
                "BackendState": "Running",
                "TailscaleIPs": ["100.100.10.5"],
                "Self": {"Online": True, "PrimaryRoutes": [SUBNET], "Expired": True},
            },
        ),
    ],
    ids=["needslogin-retained-addr", "nostate-retained-addr", "running-expired-flag"],
)
def test_an_expired_or_revoked_node_key_is_a_failure_not_incomplete(tmp_path, label, status):
    env = _fake_tailscale(tmp_path, status, SERVER_PREFS)
    r = _run("bash", CHECK, "--role", "server", env=env)
    out = r.stdout + r.stderr
    assert r.returncode == 1, (
        f"{label}: a node that HAD an identity and lost it exited {r.returncode}, not 1. "
        "rc 4 says 'no defect found' about a dead backup path.\n" + out
    )
    assert "HAS NEVER AUTHENTICATED" not in out, (
        f"{label}: still claims the node never authenticated, next to the address it "
        "retained:\n" + out
    )
    assert "HAD A TAILNET IDENTITY AND NO LONGER HAS A VALID ONE" in r.stdout, out


def test_the_expired_shapes_are_not_reachable_by_the_fresh_install_path(tmp_path):
    """The control for the three cases above, in the other direction: strip BOTH pieces
    of evidence -- no retained address, no Expired flag -- and the SAME code must go back
    to rc 4. Without this, a checker that simply failed everything would satisfy them."""
    env = _fake_tailscale(tmp_path, NEVER_AUTHED, NO_PREFS)
    r = _run("bash", CHECK, "--role", "server", env=env)
    assert r.returncode == 4, r.stdout + r.stderr
    assert "HAS NEVER AUTHENTICATED" in r.stdout


def test_a_key_expiry_date_in_the_past_is_a_failure(tmp_path):
    """The third, independent detector: a daemon still reporting Running with no Expired
    flag, whose KeyExpiry has nonetheless lapsed. It reads a different field from either
    shape above, so a build that emits neither of those still cannot hide a dead key."""
    status = {
        "BackendState": "Running",
        "TailscaleIPs": ["100.100.10.5"],
        "Self": {
            "Online": True,
            "PrimaryRoutes": [SUBNET],
            "KeyExpiry": "2020-01-02T03:04:05Z",
        },
    }
    env = _fake_tailscale(tmp_path, status, SERVER_PREFS)
    r = _run("bash", CHECK, "--role", "server", env=env)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "KEY EXPIRY DATE IS IN THE PAST" in r.stdout, r.stdout
    # ...and the control: the SAME shape with a future date is an ACTION (rc 3), not a
    # FAIL. A guard that failed on any KeyExpiry at all would pass the assertion above.
    status["Self"]["KeyExpiry"] = "2099-01-02T03:04:05Z"
    (tmp_path / "future").mkdir()
    env = _fake_tailscale(tmp_path / "future", status, SERVER_PREFS)
    r = _run("bash", CHECK, "--role", "server", env=env)
    assert r.returncode == 3, r.stdout + r.stderr
    assert "KEY EXPIRY DATE IS IN THE PAST" not in r.stdout


def test_an_unreadable_prefs_run_does_not_assert_what_the_node_advertises(tmp_path):
    """One run printed BOTH `what this node ADVERTISES is UNKNOWN` and `<subnet> is
    advertised but NOT APPROVED` -- the second asserting, and sending the operator to the
    admin console over, the exact fact the first had declared unknowable. `PrimaryRoutes`
    being empty has two causes with different fixes (a browser vs `tailscale up` here),
    and a run that cannot read prefs has no evidence to choose between them."""
    unapproved = {
        "BackendState": "Running",
        "TailscaleIPs": ["100.100.10.5"],
        "Self": {"Online": True, "PrimaryRoutes": []},
    }
    env = _fake_tailscale(tmp_path, unapproved, None)  # prefs unreadable
    r = _run("bash", CHECK, "--role", "server", env=env)
    # The verdict paragraphs are hard-wrapped, so compare on whitespace-normalised text.
    flat = " ".join(r.stdout.split())
    assert "what this node ADVERTISES is UNKNOWN" in flat, r.stdout
    assert "is advertised but NOT APPROVED" not in flat, (
        "the run asserts the node advertises the route in the same breath as declaring "
        "that unknowable:\n" + r.stdout
    )
    assert "carries NO traffic over tailscale right now" in flat, r.stdout
    # THE CONTROL: with prefs READABLE and the route genuinely advertised, the admin
    # console action must still fire. Otherwise this test is satisfied by deleting it.
    (tmp_path / "readable").mkdir()
    env = _fake_tailscale(tmp_path / "readable", unapproved, SERVER_PREFS)
    r = _run("bash", CHECK, "--role", "server", env=env)
    assert r.returncode == 3, r.stdout + r.stderr
    assert "is advertised but NOT APPROVED" in r.stdout, r.stdout


def test_a_subnet_router_with_no_lan_route_still_fails(tmp_path):
    """The control for the stubbed routing table above: with the route REMOVED, a
    subnet router is a definitive FAIL even though everything else is perfect. Without
    this the `lan_route` stub is a fixture nothing can see, and `lan_route=True` would
    be indistinguishable from not reading `ip route` at all."""
    env = _fake_tailscale(tmp_path, AUTHED_APPROVED, SERVER_PREFS, lan_route=False)
    r = _run("bash", CHECK, "--role", "server", env=env)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "no non-tailscale route" in r.stdout, r.stdout


# --------------------------------------------------------------------------------------
# 🔴 THE SEAM: what the dry-build PARSER returns, fed to the GATE that reads it
#
# The parser was tested on well-formed fixtures and the gate was tested on numbers typed
# in by hand, and the defect lived in NEITHER -- it lived in the join. `_drybuild_counts`
# spelled "I could not read the download size" as `0.0`, and `_gate_reasons` read that as
# "there is nothing to download". MEASURED against the old code, all four shapes below
# returned `0|2400|0.0` and passed every gate in silence: a 2400-path, entirely
# substitutable world rebuild, which is exactly what the gate exists to stop.
#
# So this drives BOTH functions, out of the real script, exactly as a real run does.
# --------------------------------------------------------------------------------------
def _gate_probe(tmp_path: Path, dry_build_stderr: str) -> tuple[str, str]:
    """Run `_drybuild_counts` on `dry_build_stderr`, feed the result to `_gate_reasons`.

    Returns (counts_record, gate_output). Empty gate output means "allowed".
    """
    src = APPLY.read_text()
    # Everything up to the `--self-test` dispatcher: the limit variables and every
    # function, and nothing that touches the host.
    prefix = src.split('\nif [ "$SELFTEST" = "1" ]; then', 1)[0]
    assert "_gate_reasons()" in prefix and "_drybuild_counts()" in prefix, (
        "the seam probe no longer captures both functions -- the script was reordered"
    )
    fixture = tmp_path / "dry.err"
    fixture.write_text(dry_build_stderr)
    probe = tmp_path / "seam.sh"
    # The fixture path travels in the ENVIRONMENT, not in `$1`: `prefix` carries the
    # script's own argument parser, which rejects an unknown positional with exit 2.
    probe.write_text(
        prefix
        + '\nc=$(_drybuild_counts "$TS_SEAM_FIXTURE")\n'
        'IFS="|" read -r b f m <<<"$c"\n'
        'printf "%s\\n---\\n" "$c"\n'
        '_gate_reasons 26.11 26.11 "$b" "$b" "$m" "$m" "$f" "$f"\n'
    )
    env = dict(os.environ, TS_SEAM_FIXTURE=str(fixture))
    r = _run("bash", probe, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    counts, _, gate = r.stdout.partition("\n---\n")
    return counts.strip(), gate.strip()


# 🔴 `expect_mib` IS PINNED EXACTLY, NOT JUST "not 0.0". A first version of this test
# asserted only that the size was not `0.0` and that SOME gate fired, and a mutant that
# defaulted an unrecognised unit to MiB -- turning 4 EiB into 4.0 -- SURVIVED it: the
# number was not 0.0, and the fetch-count gate caught the change for an unrelated reason.
# Defence in depth is not a reason to leave the inner guard unpinned; the exact value is
# the only assertion that can see that mutation.
@pytest.mark.parametrize(
    "label,line,expect_mib",
    [
        ("no size parenthetical", "these 2400 paths will be fetched:", "UNKNOWN"),
        # A unit it DOES understand must be scaled, not refused -- 2.5 TiB read as
        # 2.5 MiB would be six orders of magnitude of "this is fine".
        ("TiB", "these 2400 paths will be fetched (2.5 TiB download, 9.0 TiB unpacked):",
         "2621440.0"),
        ("comma decimal separator",
         "these 2400 paths will be fetched (4096,0 MiB download, 9000,0 MiB unpacked):",
         "UNKNOWN"),
        ("an unknown unit",
         "these 2400 paths will be fetched (4.0 EiB download, 9.0 EiB unpacked):",
         "UNKNOWN"),
    ],
)
def test_a_world_sized_fetch_never_passes_the_gate_however_its_size_is_spelled(
    tmp_path, label, line, expect_mib
):
    counts, gate = _gate_probe(tmp_path, line + "\n")
    assert counts == f"0|2400|{expect_mib}", (
        f"{label}: got {counts}, want 0|2400|{expect_mib}. An unreadable size must be "
        "spelled UNKNOWN -- never as 0.0 (indistinguishable from nothing to download) "
        "and never as a number invented by defaulting the unit."
    )
    assert gate, f"{label}: {counts} passed every gate in silence"


def test_the_seam_still_allows_a_real_tailscale_sized_change(tmp_path):
    """The control. Both directions, or the four cases above are satisfied by a gate that
    refuses everything -- which is a permanent red light people learn to override."""
    counts, gate = _gate_probe(
        tmp_path,
        "these 7 derivations will be built:\n"
        "  /nix/store/aaaa-tailscale-1.102.3.drv\n"
        "these 25 paths will be fetched (17.6 MiB download, 61.1 MiB unpacked):\n"
        "  /nix/store/bbbb-thing\n",
    )
    assert counts == "7|25|17.6", counts
    assert gate == "", f"the measured tailscale-only delta was refused:\n{gate}"


def test_an_empty_dry_build_is_a_real_zero_and_is_allowed(tmp_path):
    """`0.0` must still MEAN zero when there genuinely is nothing to fetch -- otherwise
    the fix above would have turned the gate into an unconditional refusal."""
    counts, gate = _gate_probe(tmp_path, "building the system configuration...\n")
    assert counts == "0|0|0.0", counts
    assert gate == "", gate


def test_the_fetch_count_is_gated_and_not_merely_printed(tmp_path):
    """The count was parsed correctly and printed in the summary table, and read by no
    gate -- the identical 'decorative column' defect the download-volume gate was added
    to fix. It is the axis that still has a number when the SIZE cannot be parsed."""
    counts, gate = _gate_probe(
        tmp_path, "these 2400 paths will be fetched (1.0 MiB download, 2.0 MiB unpacked):\n"
    )
    assert counts == "0|2400|1.0", counts
    assert "FETCH COUNT" in gate, (
        "2400 paths to fetch passed with a 1.0 MiB size -- both build gates and both "
        f"download-volume gates are silent by construction here:\n{gate}"
    )


# --------------------------------------------------------------------------------------
# idempotency: a MENTION is not a DECLARATION
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "body,declared",
    [
        ("  # TODO: consider services.tailscale one day", False),
        # 🔴 THE DISCRIMINATING COMMENT CASE: a whole DECLARATION commented out. The
        # TODO above is rejected by the shape of the words alone, so it stays False even
        # with comment-stripping deleted -- it cannot see that mutation. This one can.
        ("  # services.tailscale = { enable = true; };", False),
        ("  /* services.tailscale = { enable = true; }; */", False),
        ("  environment.systemPackages = [ pkgs.tailscale ];", False),
        # 🔴 THE DISCRIMINATING NON-COMMENT CASE: `services.tailscale` in live code that
        # is not a setting. Only the `[.={]` anchor rejects this; a bare
        # `services\.tailscale` match accepts it and reports the host as already done.
        ('  warnings = [ "services.tailscale is not enabled here" ];', False),
        # 🔴 THE FIXTURES THAT DISCRIMINATE STRING-BLANKING FROM THAT ANCHOR. The case
        # above has no `[.={]` after the attribute path, so the anchor alone rejects it
        # and it stays False with every line of string handling deleted -- it cannot see
        # that mutation. These two can: each is a string whose CONTENTS are a
        # syntactically perfect declaration, and each made the script print "Nothing to
        # do. Exiting 0" on a host where nothing had been applied.
        ('  warnings = [ "you should run services.tailscale.enable = true; here" ];', False),
        ("  text = ''\n    services.tailscale.enable = true;\n  '';", False),
        # ...and the control against a scanner that blanks too much: a real declaration
        # after a CLOSED string must still be found.
        ('  warnings = [ "off" ];\n  services.tailscale.enable = true;', True),
        ("  services.tailscale.enable = true;", True),
        ("  services.tailscale = {\n    enable = true;\n  };", True),
        ("  services.tailscale={enable=true;};", True),
    ],
)
def test_only_a_real_declaration_counts_as_already_configured(tmp_path, body, declared):
    """`grep -q 'services\\.tailscale'` reported "Nothing to do. Exiting 0" for a config
    whose only occurrence was a TODO comment -- telling the operator the backup path was
    done on a host where nothing had been applied."""
    cfg = tmp_path / "configuration.nix"
    cfg.write_text("{ config, pkgs, ... }:\n{\n" + body + "\n}\n")
    # drive the predicate the script uses, through the script itself
    src = APPLY.read_text()
    fn = src.split("_cfg_tailscale_decls() {", 1)[1].split("\n}\n", 1)[0]
    probe = tmp_path / "probe.sh"
    probe.write_text("_cfg_tailscale_decls() {" + fn + "\n}\n_cfg_tailscale_decls \"$1\"\n")
    r = _run("bash", probe, cfg)
    assert r.returncode == 0, r.stderr
    got = bool(r.stdout.strip())
    assert got is declared, f"{body!r} -> declared={got}, want {declared}\n{r.stdout}"


# 🔴 THERE IS DELIBERATELY NO SHELLCHECK TEST HERE, and the omission is the finding.
# A first version ran `shellcheck -S warning` on both scripts and `pytest.skip`ped when
# the binary was absent. `shellcheck` is not in this repo's `gateTools`, so it is absent
# in EVERY gate run: the test could only ever skip, and `run-tests.sh` rejected it as an
# UNPINNED SKIP -- "a test that did not run" -- turning the whole pytest tier red with
# `failed=0`. Both remedies were worse than removing it:
#   * pinning it in EXPECTED_SKIPS ships a test that never executes anywhere, which is
#     the vacuous-guard shape this suite exists to prevent;
#   * adding shellcheck to `gateTools` changes the toolchain for every target and every
#     developer, to gate two files, in a PR about something else.
# So shellcheck stays a manual step. It was run for this change against
# `nix-shell -p shellcheck` (0.11.0) -- clean at `-S warning` on both scripts, with the
# scanner negative-controlled against a known-bad script first, since a clean report
# from a scanner that is not scanning is not a clean report.


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
