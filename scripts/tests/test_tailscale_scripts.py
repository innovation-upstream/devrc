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
  * `--help` must print the comment header and stop -- not truncate it, not run past it
    into `set -euo pipefail`.
  * `--role` as the final argument must SAY something rather than exit 1 in silence.

Both scripts' own `--self-test` suites are run here too, so their internal controls are
part of the gate rather than something a human has to remember to invoke.

    run:  pytest scripts/tests/test_tailscale_scripts.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
APPLY = REPO / "nix" / "system" / "apply-tailscale.sh"
CHECK = REPO / "nix" / "system" / "check-tailscale.sh"

SUBNET = "192.168.50.0/24"


def _run(*argv, **kw):
    return subprocess.run(
        [str(a) for a in argv], capture_output=True, text=True, timeout=180, **kw
    )


def _header_comment_lines(path: Path) -> list[str]:
    """The `#` block after the shebang -- the text `--help` is supposed to print."""
    out = []
    for i, line in enumerate(path.read_text().splitlines()):
        if i == 0:
            continue
        if not line.startswith("#"):
            break
        out.append(re.sub(r"^# ?", "", line))
    return out


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
    assert "set -euo pipefail" not in "\n".join(got)


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
def _fake_tailscale(tmp_path: Path, status: dict, prefs: dict | None) -> dict:
    """A PATH containing a `tailscale` that answers from fixtures, plus a fake /proc."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (tmp_path / "status.json").write_text(json.dumps(status))
    if prefs is not None:
        (tmp_path / "prefs.json").write_text(json.dumps(prefs))
    shim = bindir / "tailscale"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        'case "$1 $2" in\n'
        f'  "status --json") cat "{tmp_path}/status.json"; exit 0 ;;\n'
        + (
            f'  "debug prefs") cat "{tmp_path}/prefs.json"; exit 0 ;;\n'
            if prefs is not None
            else '  "debug prefs") exit 1 ;;\n'
        )
        + "esac\nexit 1\n"
    )
    shim.chmod(0o755)
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


def test_shellcheck_is_clean_at_warning_level():
    sc = shutil.which("shellcheck")
    if not sc:
        pytest.skip("shellcheck not on PATH")
    # NEGATIVE CONTROL first: a clean report from a scanner that is not scanning is not
    # a clean report.
    bad = Path(os.environ.get("TMPDIR", "/tmp")) / f"ts-shellcheck-control-{os.getpid()}.sh"
    bad.write_text("#!/usr/bin/env bash\nfoo=1\ncat $1 | grep x\n")
    try:
        control = _run(sc, "-S", "warning", bad)
        assert control.returncode != 0, "shellcheck reported a known-bad script as clean"
    finally:
        bad.unlink(missing_ok=True)
    r = _run(sc, "-S", "warning", APPLY, CHECK)
    assert r.returncode == 0, r.stdout + r.stderr


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
