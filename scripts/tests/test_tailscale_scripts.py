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
  * 🔴 and the SHAPE of that which survives a RESTART. `TailscaleIPs`, `Self.Expired` and
    `Self.KeyExpiry` are all netmap fields and the netmap is in-memory only, so one
    reboot on a node whose key had lapsed wiped every detector at once and the dead path
    read as a fresh install again -- rc 4, "no defect found". The durable evidence is the
    persisted profile in `tailscale debug prefs` (`Config`/`ipn.Prefs.Persist`), and the
    controls in the OTHER direction are the expensive ones: an EMPTY persisted profile is
    what a never-logged-in daemon carries, and reading that as an identity would make
    every first run rc 1 and roll the config back.
  * 🔴 a `tailscaled` that is STOPPED or CRASHED must be rc 1. With the daemon down there
    is no status and no prefs to read, so every claim came out unevaluated and the run
    exited 4 -- "NOT YET DETERMINABLE (no defect found)" -- about a backup path that is
    dead right now. The control in the other direction is the one that would cost most: a
    host where tailscale was NEVER installed has no unit, and `systemctl is-active` prints
    `inactive` there too, so the FAIL is gated on `LoadState=loaded` and the never-applied
    host stays rc 4. rc 1 there is `die` + rollback in apply-tailscale.sh, on every first
    run.
  * a SELF-CONTRADICTORY daemon (a non-logged-out backend holding no address) must not be
    rc 1. Its own message said "re-run this check before doing anything else"; apply's
    answer to rc 1 is `die` -> restore `configuration.nix` -> report that the RUNNING
    system was not restored, so the next `nixos-rebuild switch` by anyone would silently
    delete the backup path. It is rc 4 now, and the checker takes the second sample
    itself after a settle rather than telling a human to.
  * a READ of an option is not a DECLARATION of it. `config.services.tailscale.enable` in
    a `mkIf` or an assertion made the apply script print "already DECLARES ... Nothing to
    do. Exiting 0" on a host with no tailscale at all.
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
    tmp_path: Path,
    status: dict,
    prefs: dict | None,
    lan_route: bool = True,
    unit: tuple[str, str] = ("active", "loaded"),
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
    # 🔴 THE UNIT STUB ANSWERS BOTH READS THE CHECKER MAKES, AND THEY ARE DIFFERENT
    # QUESTIONS. `is-active` is liveness; `show -p LoadState --value` is existence, and
    # only the pair can separate "tailscaled is down" from "tailscale was never applied
    # to this host" -- MEASURED on systemd 261, `is-active` prints `inactive` for BOTH.
    # Default is a healthy unit; the cases below drive the other combinations. The
    # sandbox has no systemd at all, so this is stubbed rather than left to the `|| true`.
    unit_state, unit_load = unit
    write_exec(
        bindir / "systemctl",
        'case "$1" in\n'
        f'  is-active) printf "%s\\n" "{unit_state}"\n'
        f'             [ "{unit_state}" = active ] || exit 3\n'
        "             exit 0 ;;\n"
        f'  show)      printf "%s\\n" "{unit_load}"; exit 0 ;;\n'
        "esac\n"
        "exit 0\n",
    )
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
    # The settle re-read costs wall time and every case here is a STABLE fixture -- the
    # stub answers identically however many times it is called, so a second read can only
    # slow the suite down. `test_a_self_contradictory_daemon_is_re_read_after_a_settle`
    # turns it back on, against a stub that deliberately answers differently the second
    # time, so the mechanism itself is not left unexercised by this default.
    env["TS_SETTLE_SECS"] = "0"
    # The on-disk fallback must not read the REAL host's state file from a test. Pointed
    # at a path that does not exist unless a case creates it.
    env["TS_STATE_FILE"] = str(tmp_path / "tailscaled.state")
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
# `ipn.Prefs.Persist` is marshalled under the key `Config`. A non-empty NodeID/LoginName
# means a login was COMPLETED on this host at some point, and -- unlike everything in the
# netmap -- it is reloaded from `tailscaled.state` on every daemon start.
PERSISTED_PREFS = {
    "AdvertiseRoutes": [SUBNET],
    "RouteAll": False,
    "WantRunning": True,
    "Config": {
        "NodeID": "nEXAMPLECafeBeef",
        "UserProfile": {"LoginName": "operator@example.test"},
    },
}
# What a daemon that has NEVER logged in carries: the key is present, the profile is
# empty. Reading this as an identity would make every first run a FAIL.
EMPTY_PROFILE_PREFS = {
    "AdvertiseRoutes": None,
    "RouteAll": False,
    "WantRunning": False,
    "Config": {"NodeID": "", "UserProfile": {"LoginName": "", "DisplayName": ""}},
}


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
# 🔴 A STOPPED OR CRASHED `tailscaled` IS A FAILURE -- AND A HOST THAT NEVER HAD ONE IS NOT
#
# With the daemon down there is no `tailscale status` and no `tailscale debug prefs`, and
# the on-disk fallback needs root, so every claim in the checker comes out UNEVALUATED and
# the run exited 4: "NOT YET DETERMINABLE (no defect found)" -- about a backup path that is
# dead right now. Reproduced live against a real tailscaled 1.102.3: killing the daemon did
# not move the exit code.
#
# The other direction is the expensive one and is pinned right below it. On a host where
# tailscale was NEVER installed there is no unit at all, and `systemctl is-active` prints
# `inactive` there too -- byte-identical to a stopped unit (measured, systemd 261). Failing
# on `is-active` alone would make every first run a node-side FAIL, which is rc 1, which is
# `die` + rollback in apply-tailscale.sh: the config deleted by the run that installed it.
# `LoadState` is the signal that separates them.
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("unit_state", ["inactive", "failed"])
def test_a_dead_tailscaled_unit_is_a_failure_not_incomplete(tmp_path, unit_state):
    # No prefs: a daemon that is not running answers neither of the checker's reads. That
    # is what makes the old verdict rc 4 -- there is nothing left to find a defect in.
    env = _fake_tailscale(tmp_path, NEVER_AUTHED, None, unit=(unit_state, "loaded"))
    r = _run("bash", CHECK, "--role", "server", env=env)
    assert r.returncode == 1, (
        f"a tailscaled unit that EXISTS and is '{unit_state}' exited {r.returncode}. The "
        "backup path is down and rc 4 reports that as 'no defect found'.\n"
        + r.stdout
        + r.stderr
    )
    assert "IS INSTALLED ON THIS HOST BUT IS NOT RUNNING" in r.stdout, (
        "the run failed, but not with the dead-unit finding -- some other verdict is "
        "carrying this test:\n" + r.stdout
    )
    assert unit_state in r.stdout and "systemctl status tailscaled" in r.stdout


@pytest.mark.parametrize(
    "label,unit",
    [
        # `systemctl show -p LoadState` for a unit that does not exist.
        ("tailscale was never applied to this host", ("inactive", "not-found")),
        # No systemd at all, or a systemctl too old for `--value`: both reads come back
        # empty. The guard must stay silent rather than guess.
        ("nothing answers systemctl", ("", "")),
    ],
)
def test_a_host_with_no_tailscaled_unit_is_still_rc_4(tmp_path, label, unit):
    """🔴 THE REGRESSION THAT WOULD COST THE MOST. This is the pre-apply / first-run
    state; rc 1 here makes apply-tailscale.sh roll back the config it just installed."""
    env = _fake_tailscale(tmp_path, NEVER_AUTHED, NO_PREFS, unit=unit)
    r = _run("bash", CHECK, "--role", "server", env=env)
    assert r.returncode == 4, (
        f"{label}: exited {r.returncode}, not 4. The dead-unit FAIL must be gated on the "
        "unit EXISTING -- `is-active` prints 'inactive' for an absent unit too.\n"
        + r.stdout
        + r.stderr
    )
    assert "FAIL:" not in r.stdout
    assert "IS INSTALLED ON THIS HOST BUT IS NOT RUNNING" not in r.stdout


def test_a_healthy_unit_draws_no_unit_finding(tmp_path):
    """The control for both of the above: with the unit loaded AND active, rc 0 is still
    reachable. Without this, a guard that failed on every unit state would pass them."""
    env = _fake_tailscale(tmp_path, AUTHED_APPROVED, SERVER_PREFS, unit=("active", "loaded"))
    r = _run("bash", CHECK, "--role", "server", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "IS INSTALLED ON THIS HOST BUT IS NOT RUNNING" not in r.stdout


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


# --------------------------------------------------------------------------------------
# 🔴 THE RESTART HOLE: all three netmap detectors go blind together, and only the DISK
# still knows.
#
# `TailscaleIPs`, `Self.Expired` and `Self.KeyExpiry` are netmap fields, and the netmap is
# in-memory only -- it is fed by control-plane map responses and there is no
# restore-from-disk path. So one reboot, power cut or `nixos-rebuild switch` on a node
# whose key has already lapsed wipes ALL THREE at once: the daemon mints a new node key,
# control answers with an AuthURL, no map poll happens, and `tailscale status` reports
# `NeedsLogin` with nothing in it -- byte-identical to a fresh install. Every one of the
# three cases above then goes green while the backup path is dead, and the checker printed
# "THIS NODE HAS NEVER AUTHENTICATED ... It is NOT a node-side defect", rc 4.
#
# `lost` only survived while the daemon had run CONTINUOUSLY since before the lapse. Over
# a months-long absence that is not an assumption worth making.
# --------------------------------------------------------------------------------------
def test_an_expired_key_that_survived_a_restart_is_still_a_failure(tmp_path):
    """The status document here has NO evidence in it at all -- that is the point. The
    only thing separating it from a fresh install is the persisted profile in prefs."""
    env = _fake_tailscale(tmp_path, NEVER_AUTHED, PERSISTED_PREFS)
    r = _run("bash", CHECK, "--role", "server", env=env)
    out = r.stdout + r.stderr
    assert r.returncode == 1, (
        "a node whose key lapsed and which has since RESTARTED exited "
        f"{r.returncode}, not 1. Its netmap is empty, so the address, `Self.Expired` and "
        "`KeyExpiry` detectors are all blind and it reads as a fresh install -- while the "
        "backup path is dead.\n" + out
    )
    assert "HAD A TAILNET IDENTITY AND NO LONGER HAS A VALID ONE" in r.stdout, out
    assert "HAS NEVER AUTHENTICATED" not in out, out


@pytest.mark.parametrize(
    "label,prefs",
    [
        ("prefs carry an EMPTY profile, which is what a fresh daemon has",
         EMPTY_PROFILE_PREFS),
        ("prefs carry no `Config` key at all", NO_PREFS),
    ],
)
def test_a_fresh_install_is_still_rc_4_whatever_its_prefs_look_like(tmp_path, label, prefs):
    """THE CONTROL, and it is the expensive direction. If an empty persisted profile were
    read as an identity, EVERY first run would be rc 1 and apply-tailscale.sh would roll
    back the config it had just installed -- the exact bug this whole file exists over."""
    env = _fake_tailscale(tmp_path, NEVER_AUTHED, prefs)
    r = _run("bash", CHECK, "--role", "server", env=env)
    assert r.returncode == 4, f"{label}: {r.stdout}{r.stderr}"
    assert "HAS NEVER AUTHENTICATED" in r.stdout
    assert "FAIL:" not in r.stdout


def test_the_on_disk_state_file_is_the_fallback_when_prefs_cannot_be_read(tmp_path):
    """Prefs are the authority; when they cannot be read at all the daemon's own state
    file is the only remaining durable evidence. Driven in BOTH directions, plus the
    scoping control -- a readable prefs document must WIN over the file."""
    state = tmp_path / "tailscaled.state"

    # (a) prefs unreadable + a real profile entry on disk -> the identity was LOST.
    state.write_text('{"_machinekey":"cHJpdmtleQ","profile-a1b2":"eyJVc2VyUHJvZmlsZSI6e319"}')
    env = _fake_tailscale(tmp_path, NEVER_AUTHED, None)
    r = _run("bash", CHECK, "--role", "server", env=env)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "HAD A TAILNET IDENTITY AND NO LONGER HAS A VALID ONE" in r.stdout

    # (b) THE CONTROL. What a never-logged-in daemon writes: a machine key, and the
    # `_current-profile` pointer at the EMPTY profile. Keying on that pointer instead of
    # a `profile-` entry would fail every fresh host.
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "tailscaled.state").write_text(
        '{"_machinekey":"cHJpdmtleQ","_current-profile":""}'
    )
    env = _fake_tailscale(tmp_path / "b", NEVER_AUTHED, None)
    r = _run("bash", CHECK, "--role", "server", env=env)
    assert r.returncode == 4, r.stdout + r.stderr
    assert "HAS NEVER AUTHENTICATED" in r.stdout

    # (c) THE SCOPING CONTROL. Prefs READABLE and carrying no identity, with a profile
    # entry sitting on disk: prefs win, because a second opinion that can only contradict
    # the authority is not one worth acting on.
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "tailscaled.state").write_text('{"profile-a1b2":"eyJ4IjoxfQ"}')
    env = _fake_tailscale(tmp_path / "c", NEVER_AUTHED, NO_PREFS)
    r = _run("bash", CHECK, "--role", "server", env=env)
    assert r.returncode == 4, r.stdout + r.stderr


# The three EVIDENCE clauses the `lost` FAIL can carry, one per route into that state.
# They are asserted as an EXCLUSIVE set -- the right one present and the other two absent
# -- because the defect was not a missing sentence, it was the WRONG one: the text
# asserted "the netmap addresses it was issued are still present: <none>" followed by "a
# node that had never logged in would have NEITHER", printing the absence of its own
# evidence as evidence. A guard that only checked the offending words is walkable by any
# rewording that still names an address the run cannot see.
LOST_EVIDENCE = {
    "address": "still present:",
    "persisted": "there is no netmap address left",
    "expired-flag": "Self.Expired is the control plane's own word",
}


@pytest.mark.parametrize(
    "route,ips,prefs",
    [
        # Reached via the retained netmap address.
        ("address", ["100.100.10.5"], SERVER_PREFS),
        # Reached via the persisted profile, netmap gone -- the restart shape.
        ("persisted", [], PERSISTED_PREFS),
        # Reached via `Self.Expired` alone, with nothing else to point at.
        ("expired-flag", [], SERVER_PREFS),
    ],
)
def test_the_expired_fail_names_the_evidence_it_actually_has(tmp_path, route, ips, prefs):
    status = {
        "BackendState": "Running",
        "TailscaleIPs": ips,
        "Self": {"Online": True, "Expired": True},
    }
    env = _fake_tailscale(tmp_path, status, prefs)
    r = _run("bash", CHECK, "--role", "server", env=env)
    assert r.returncode == 1, r.stdout + r.stderr
    flat = " ".join(r.stdout.split())
    assert "HAD A TAILNET IDENTITY AND NO LONGER HAS A VALID ONE" in flat
    for name, clause in LOST_EVIDENCE.items():
        if name == route:
            assert clause in flat, (
                f"the {route} route does not name its own evidence:\n{r.stdout}"
            )
        else:
            assert clause not in flat, (
                f"the {route} route claims the {name} evidence, which this run does not "
                f"have:\n{r.stdout}"
            )
    if route == "address":
        assert "still present: 100.100.10.5" in flat, r.stdout
    else:
        # The exact shape of the original defect: an address slot rendered from nothing.
        assert "still present: <none>" not in flat, r.stdout
        assert "still present: ," not in flat, r.stdout
        assert "would have NEITHER" not in flat, r.stdout


# --------------------------------------------------------------------------------------
# 🔴 A SELF-CONTRADICTORY DAEMON IS NOT A DEFECT -- AND rc 1 FOR IT WAS AN APPLY-TIME
# ROLLBACK.
#
# The `incoherent` FAIL's own text says "if tailscaled was only just started it may still
# be fetching its netmap -- re-run this check before doing anything else". Apply's answer
# to rc 1 is `die` -> EXIT trap -> restore `$CFG` and report that the RUNNING system was
# not restored. It never re-ran, and there was no settle window anywhere: apply goes
# `systemctl is-active` -> `tailscale version` -> `bash "$CHECK"` with no sleep at all.
# The cost of that spurious rollback is not a wasted run -- `configuration.nix` loses the
# tailscale block while the running system keeps it, so the next `nixos-rebuild switch`
# by anyone silently deletes the backup path.
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "label,backend",
    [
        ("Running with an empty netmap", "Running"),
        # Reachable with a pre-existing state file: node key present, WantRunning=false,
        # netmap not fetched.
        ("Stopped with no address", "Stopped"),
        # `tailscale up` on a freshly-restarted, previously-down node.
        ("Starting with a nil netmap", "Starting"),
    ],
)
def test_a_self_contradictory_daemon_is_not_a_rollback(tmp_path, label, backend):
    status = {"BackendState": backend, "TailscaleIPs": [], "Self": {"Online": False}}
    env = _fake_tailscale(tmp_path, status, SERVER_PREFS)
    r = _run("bash", CHECK, "--role", "server", env=env)
    out = r.stdout + r.stderr
    assert r.returncode == 4, (
        f"{label}: exited {r.returncode}. rc 1 makes apply-tailscale.sh `die`, restore "
        "configuration.nix and leave the running system carrying a change the file no "
        "longer has -- for a state whose own message says to re-run the check.\n" + out
    )
    assert "SELF-CONTRADICTORY" in r.stdout, out
    assert "FAIL:" not in r.stdout, out
    # ...and it must NOT be quietly reclassified as the fresh-install state either: those
    # need different actions and only one of them is expected after an apply.
    assert "HAS NEVER AUTHENTICATED" not in out, out


def test_a_genuine_node_side_failure_is_still_rc_1_with_the_same_backend(tmp_path):
    """The control for the three cases above: `Stopped` is one of them, so moving
    `incoherent` off rc 1 must not have taken the real FAIL with it. Same backend, one
    piece of evidence added -- the retained address -- and it goes red again."""
    status = {
        "BackendState": "Stopped",
        "TailscaleIPs": ["100.100.10.5"],
        "Self": {"Online": False, "PrimaryRoutes": [SUBNET]},
    }
    env = _fake_tailscale(tmp_path, status, SERVER_PREFS)
    r = _run("bash", CHECK, "--role", "server", env=env)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL:" in r.stdout


def test_a_self_contradictory_daemon_is_re_read_after_a_settle(tmp_path):
    """The settle is not decoration: the checker takes the second sample itself instead
    of telling a human to. Driven with a stub that answers DIFFERENTLY the second time --
    an incoherent first read, a healthy one after -- so a settle that never re-reads
    reports rc 4 here instead of the rc 0 a working daemon deserves."""
    env = _fake_tailscale(tmp_path, AUTHED_APPROVED, SERVER_PREFS)
    bindir = tmp_path / "bin"
    incoherent = {"BackendState": "Starting", "TailscaleIPs": [], "Self": {"Online": False}}
    (tmp_path / "first.json").write_text(json.dumps(incoherent))
    write_exec(
        bindir / "tailscale",
        'case "$1 $2" in\n'
        '  "status --json")\n'
        f'    if [ -f "{tmp_path}/called" ]; then cat "{tmp_path}/status.json";\n'
        f'    else : >"{tmp_path}/called"; cat "{tmp_path}/first.json"; fi\n'
        "    exit 0 ;;\n"
        f'  "debug prefs") cat "{tmp_path}/prefs.json"; exit 0 ;;\n'
        "esac\nexit 1\n",
    )
    env["TS_SETTLE_SECS"] = "1"
    r = _run("bash", CHECK, "--role", "server", env=env)
    assert "settle  :" in r.stdout, (
        "the checker rendered a verdict off ONE instantaneous sample of a daemon that had "
        "not fetched its netmap yet:\n" + r.stdout
    )
    assert r.returncode == 0, (
        "the settle did not actually RE-READ the daemon -- the second answer was healthy "
        f"and the run still exited {r.returncode}:\n" + r.stdout + r.stderr
    )
    assert "SELF-CONTRADICTORY" not in r.stdout


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
    # 🔴 SCOPED TO THIS NODE. `Self.PrimaryRoutes` is a fact about this node only, so an
    # unqualified "the subnet carries NO traffic right now" is a claim about the whole
    # tailnet derived from one node's view -- and false the moment a second subnet router
    # is approved for the same subnet, which is planned here.
    assert "carries NO traffic over tailscale VIA THIS NODE right now" in flat, r.stdout
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
def _gate_probe(
    tmp_path: Path, dry_build_stderr: str, baseline_stderr: str | None = None
) -> tuple[str, str]:
    """Run `_drybuild_counts` on `dry_build_stderr`, feed the result to `_gate_reasons`.

    Returns (counts_record, gate_output). Empty gate output means "allowed". The record
    returned is always the CANDIDATE's.

    🔴 `baseline_stderr` EXISTS SO THE TWO SIDES CAN DIFFER. Without it this probe fed the
    same numbers to both the pending and the total axis of every gate, so each gate's twin
    always fired with it -- and a test asserting "some gate fired" passed with either one
    DELETED. Pass a different baseline and each gate can be isolated.
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
    base_fixture = tmp_path / "base.err"
    base_fixture.write_text(
        dry_build_stderr if baseline_stderr is None else baseline_stderr
    )
    probe = tmp_path / "seam.sh"
    # The fixture paths travel in the ENVIRONMENT, not in `$1`: `prefix` carries the
    # script's own argument parser, which rejects an unknown positional with exit 2.
    probe.write_text(
        prefix
        + '\nc=$(_drybuild_counts "$TS_SEAM_FIXTURE")\n'
        'IFS="|" read -r b f m <<<"$c"\n'
        'bc=$(_drybuild_counts "$TS_SEAM_BASELINE")\n'
        'IFS="|" read -r bb bf bm <<<"$bc"\n'
        'printf "%s\\n---\\n" "$c"\n'
        '_gate_reasons 26.11 26.11 "$bb" "$b" "$bm" "$m" "$bf" "$f"\n'
    )
    env = dict(
        os.environ,
        TS_SEAM_FIXTURE=str(fixture),
        TS_SEAM_BASELINE=str(base_fixture),
    )
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
    # 🔴 ASSERT WHICH GATE, NOT THAT SOME GATE FIRED. Every line here also trips the
    # FETCH COUNT gate on its 2400 paths, so a bare `assert gate` is satisfied by that
    # alone: `_is_num` could accept the literal string UNKNOWN, `_over_mib "UNKNOWN"`
    # would then be silently false, the whole download-volume axis would be dead, and
    # this test would stay green. Naming the reason is what can see that.
    if expect_mib == "UNKNOWN":
        assert "DOWNLOAD VOLUME COULD NOT BE MEASURED" in gate, (
            f"{label}: an unreadable size did not REFUSE on the download-volume axis -- "
            f"the only reasons given were:\n{gate}"
        )
    else:
        assert "TOTAL DOWNLOAD VOLUME:" in gate, (
            f"{label}: a size this script DOES understand must be compared, not refused "
            f"as unmeasurable:\n{gate}"
        )
        assert "COULD NOT BE MEASURED" not in gate, gate


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
    to fix. It is the axis that still has a number when the SIZE cannot be parsed.

    🔴 THE TWO SIDES CARRY DIFFERENT NUMBERS, and that is the whole fix to this test. It
    used to drive `pending == total == 2400`, which trips BOTH fetch gates, so `"FETCH
    COUNT" in gate` was satisfied with either one deleted -- each masked the other's
    absence. Each is now isolated: one case where only the pending limit (50) is crossed,
    one where only the total limit (100) is.
    """
    small = "these 3 paths will be fetched (1.0 MiB download, 2.0 MiB unpacked):\n"
    big = "these 2400 paths will be fetched (1.0 MiB download, 2.0 MiB unpacked):\n"

    # TOTAL only: 3 pending (under 50), 2400 total (over 100).
    counts, gate = _gate_probe(tmp_path, big, baseline_stderr=small)
    assert counts == "0|2400|1.0", counts
    assert "TOTAL FETCH COUNT" in gate, (
        "2400 paths would be fetched, at a 1.0 MiB size that silences both "
        f"download-volume gates, and nothing refused it:\n{gate}"
    )
    assert "PENDING FETCH COUNT" not in gate, (
        f"the PENDING gate fired on a baseline of 3 paths (limit 50):\n{gate}"
    )

    # PENDING only: 2400 already queued by the CURRENT config (over 50), 60 in the
    # candidate (under 100). `switch` applies the pending work too, which is why the
    # baseline is gated at all.
    (tmp_path / "pending").mkdir()
    counts, gate = _gate_probe(
        tmp_path / "pending",
        "these 60 paths will be fetched (1.0 MiB download, 2.0 MiB unpacked):\n",
        baseline_stderr=big,
    )
    assert counts == "0|60|1.0", counts
    assert "PENDING FETCH COUNT" in gate, (
        "2400 paths were already queued by the CURRENT config and nothing refused "
        f"it:\n{gate}"
    )
    assert "TOTAL FETCH COUNT" not in gate, (
        f"the TOTAL gate fired on a candidate of 60 paths (limit 100):\n{gate}"
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
        # 🔴 A READ IS NOT A DECLARATION, and neither of these is a comment or a string --
        # so nothing above can see the mutation that removes the lookbehind. Both were
        # MEASURED classifying as declarations, which makes the script print "already
        # DECLARES ... Nothing to do. Exiting 0" on a host with no tailscale at all.
        ('  networking.firewall.checkReversePath = '
         'lib.mkIf config.services.tailscale.enable "loose";', False),
        ('  assertions = [ { assertion = config.services.tailscale.enable; '
         'message = "x"; } ];', False),
        # ...and the control against a lookbehind that swallows real declarations: one
        # that does NOT start its line must still be found.
        ("  config = { services.tailscale.enable = true; };", True),
        # THE SECOND-RUN CONTROL: the shape this script itself appends must read as a
        # declaration next time, or every re-run adds another copy.
        ("  services.tailscale = {\n    enable = true;\n"
         '    useRoutingFeatures = "server";\n    openFirewall = true;\n  };', True),
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
