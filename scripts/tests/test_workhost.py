#!/usr/bin/env python3
"""Hermetic tests for `scripts/workhost`.

NO REAL NETWORK AND NO REAL SSH. Every binary workhost shells out to — ssh,
scp, rsync, tmux, kubectl, tailscale, ip — is a stub written by
`testlib.mockbin` into a tmp dir, and PATH is set to THAT DIR ALONE. Setting
PATH to only the stub dir is deliberate rather than prepending it: the
`not-configured` tests assert on the ABSENCE of a `tailscale` binary, and a
prepend would let a real tailscale installed on the dev host later silently
turn those assertions into a different test. The stub dir is the whole world.

The `ip` stub is load-bearing for the same reason in the other direction: this
suite is developed ON workbench, where the real `ip` reports 10.42.0.30 and
workhost would correctly conclude it is already on the target. Without a stub,
every "remote" test would exercise the local branch and pass for the wrong
reason.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from testlib import mockbin  # noqa: E402

WORKHOST = REPO / "scripts" / "workhost"

LAN = "192.168.50.250"
NEBULA = "10.42.0.30"
# 100.64.0.0/10 is the CGNAT range tailscale really uses, and is RFC6598
# private — safe to commit, and pinned as safe by test_no_public_ips.py.
TS = "100.64.0.5"

RS = "\x1e"  # record separator between logged invocations

#: Every executable `sandbox()` may place on the stub PATH. Asserted live by
#: test_the_stub_path_contains_only_declared_stubs, so this set cannot rot away
#: from what sandbox() actually writes — which is what lets
#: scripts/tests/test_no_real_launchers.py pin this file's PATH clobber by
#: ENUMERATION rather than by prose.
SANDBOX_BINARIES = {
    "ssh", "scp", "rsync", "tmux", "kubectl", "ip", "tailscale",  # stubs we write
    "bash", "cp",                                                 # real, by symlink
}

#: Names in the above set that are also real launchers on this host. They are
#: present ONLY as stubs this file writes; the assertion below proves it.
SANDBOX_FAKED_LAUNCHERS = {"ssh", "scp", "rsync", "tmux", "kubectl"}

#: Absolute paths captured from the REAL environment once, so the stubs can use
#: them while PATH itself stays restricted to the stub dir. A stub that reached
#: for a bare `cat` would exit 127 under that restricted PATH and be scored as
#: "the feature is broken" rather than "the harness is" — which is exactly what
#: happened on the first run of this file.
REAL_SLEEP = shutil.which("sleep") or "/bin/sleep"
REAL_CAT = shutil.which("cat") or "/bin/cat"
REAL_DATE = shutil.which("date") or "/bin/date"
REAL_BASH = shutil.which("bash")
REAL_CP = shutil.which("cp")


# ---------------------------------------------------------------------------
# Stub bodies
# ---------------------------------------------------------------------------

def _logging_prologue() -> str:
    """Record this invocation's exact argv under $WH_LOGDIR, NUL-delimited.

    One file PER PROCESS (`$$`), not one shared file. The three probes run
    concurrently by design, and concurrent appends to a single file interleave
    mid-record: the first version of this harness produced argv lists like
    `['-o','-o','BatchMode=yes','BatchMode=yes',...]` and the resulting failures
    read as tool bugs. Pids are unique among live processes, so a per-pid file
    cannot interleave; a later process reusing a pid simply appends another
    record, which the separator already handles.

    NUL-delimited so an argument containing spaces, quotes or newlines
    round-trips byte-exactly — the whole point of the forwarding tests.
    """
    return (
        'if [ -n "${WH_LOGDIR:-}" ]; then\n'
        '  { printf \'%s\\000\' "${0##*/}"; for a in "$@"; do printf \'%s\\000\' "$a"; done; '
        "printf '\\036'; } "
        '>> "$WH_LOGDIR/$$"\n'
        "fi\n"
    )


#: A stub ssh faithful enough to test against: it strips ssh's own options the
#: way ssh does, gates on whether the address is in $WH_OK_ADDRS (exiting 255,
#: which is what real ssh exits on a connection/auth failure), and otherwise
#: EXECUTES the remaining arguments joined by spaces — exactly ssh's own
#: semantics. That last part is what makes the exit-code passthrough tests real
#: rather than a mock returning a canned number.
SSH_STUB = (
    _logging_prologue()
    # Bracket the delay with timestamps so concurrency can be proven by INTERVAL
    # OVERLAP rather than by total wall time. Overlap is load-independent:
    # serial execution cannot produce overlapping intervals at any load, whereas
    # a wall-time budget fails on a busy box and would have to be widened until
    # it stopped meaning anything.
    + 'if [ -n "${WH_TIMEDIR:-}" ]; then printf \'S %%s\\n\' "$(%s +%%s.%%N)" >> "$WH_TIMEDIR/$$"; fi\n'
    % REAL_DATE
    + 'if [ -n "${WH_DELAY:-}" ]; then %s "$WH_DELAY"; fi\n' % REAL_SLEEP
    + 'if [ -n "${WH_TIMEDIR:-}" ]; then printf \'E %%s\\n\' "$(%s +%%s.%%N)" >> "$WH_TIMEDIR/$$"; fi\n'
    % REAL_DATE
    + """
LSPEC=""
for a in "$@"; do
  case "$a" in *:127.0.0.1:*) LSPEC="$a" ;; esac
done

while [ $# -gt 0 ]; do
  case "$1" in
    -o|-L) shift 2 ;;
    -N|-t) shift ;;
    -*) shift ;;
    *) break ;;
  esac
done
ADDR="${1:-}"
[ $# -gt 0 ] && shift
[ "${1:-}" = "--" ] && shift

ok=1
for a in ${WH_OK_ADDRS:-}; do
  [ "$a" = "$ADDR" ] && ok=0
done
[ $ok -eq 0 ] || exit 255

if [ -n "$LSPEC" ] && [ -n "${WH_TUNNEL:-}" ]; then
  PORT="${LSPEC%%:*}"
  exec "$WH_PYTHON" -c 'import socket,sys,time
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", int(sys.argv[1])))
s.listen(5)
time.sleep(60)' "$PORT"
fi

[ $# -eq 0 ] && exit 0
exec /bin/sh -c "$*"
"""
)

#: Generic recorder for the verbs that are not ssh itself.
RECORDER_STUB = _logging_prologue() + 'exit "${WH_RC:-0}"\n'


def _tailscale_stub(peers: str) -> str:
    return _logging_prologue() + "%s <<'JSON'\n%s\nJSON\n" % (REAL_CAT, peers)


def _ip_stub(addresses) -> str:
    lines = "".join(
        "1: eth0    inet %s/24 brd 10.0.0.255 scope global eth0\n" % a for a in addresses
    )
    return "%s <<'EOF'\n%sEOF\n" % (REAL_CAT, lines)


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------


def sandbox(
    tmp_path,
    ok_addrs=(),
    local_addrs=("10.9.9.9",),
    tailscale=None,
    delay=None,
    tunnel=False,
):
    """Build a stub-only PATH and the env that drives it.

    ``tailscale`` is None for "no tailscale binary exists at all", or a list of
    peer dicts for a tailnet that does exist. Those two are different states and
    the tests below assert they stay different.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "argv.d"
    log.mkdir(exist_ok=True)
    timedir = tmp_path / "times.d"
    timedir.mkdir(exist_ok=True)

    mockbin.write_exec(bindir / "ssh", SSH_STUB)
    for name in ("scp", "rsync", "tmux", "kubectl"):
        mockbin.write_exec(bindir / name, RECORDER_STUB)
    mockbin.write_exec(bindir / "ip", _ip_stub(local_addrs))

    if tailscale is not None:
        status = {"Self": {"HostName": "someclient", "TailscaleIPs": ["100.64.0.2"]}}
        status["Peer"] = {"nodekey:%d" % i: p for i, p in enumerate(tailscale)}
        mockbin.write_exec(bindir / "tailscale", _tailscale_stub(json.dumps(status)))

    # Real tools the stubs and the local-exec branch genuinely need, reached by
    # absolute path so PATH can stay restricted to the stub dir.
    for real, name in ((REAL_BASH, "bash"), (REAL_CP, "cp")):
        if real and not (bindir / name).exists():
            os.symlink(real, bindir / name)

    env = {
        "PATH": str(bindir),
        "HOME": str(tmp_path),
        "SHELL": "/bin/sh",
        "WH_LOGDIR": str(log),
        "WH_TIMEDIR": str(timedir),
        "WH_OK_ADDRS": " ".join(ok_addrs),
        "WH_PYTHON": sys.executable,
    }
    if delay is not None:
        env["WH_DELAY"] = str(delay)
    if tunnel:
        env["WH_TUNNEL"] = "1"
    return bindir, log, env


def run(env, *args, timeout=60):
    return subprocess.run(
        [sys.executable, str(WORKHOST), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def invocations(log: Path):
    """Every recorded invocation, as a list of exact argv lists.

    Files are read oldest-first so that the few assertions which index into the
    list (`[0]`) see roughly call order.
    """
    if not log.exists():
        return []
    out = []
    for path in sorted(log.iterdir(), key=lambda p: p.stat().st_mtime):
        raw = path.read_text(encoding="utf-8", errors="replace")
        for record in raw.split(RS):
            if not record:
                continue
            args = [a for a in record.split("\0") if a != ""]
            if args:
                out.append(args)
    return out


def non_probe(log: Path):
    """Invocations that are not the `… true` probe."""
    return [a for a in invocations(log) if a[-1] != "true"]


def peer(name, ip=TS):
    return {"HostName": name, "DNSName": "%s.example.test." % name, "TailscaleIPs": [ip]}


# ---------------------------------------------------------------------------
# 1. Path selection across the probe matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ok,expected",
    [
        ((LAN, NEBULA, TS), "lan"),
        ((LAN, NEBULA), "lan"),
        ((LAN,), "lan"),
        ((NEBULA, TS), "nebula"),
        ((NEBULA,), "nebula"),
        ((TS,), "tailscale"),
    ],
)
def test_best_available_path_is_selected(tmp_path, ok, expected):
    """Precedence is LAN > nebula > tailscale, whatever subset is up."""
    _, _, env = sandbox(tmp_path, ok_addrs=ok, tailscale=[peer("workbench")])
    r = run(env, "--json", "path")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert json.loads(r.stdout)["selected"] == expected, (ok, r.stdout)


def test_no_path_reachable_exits_3(tmp_path):
    """Exit 3 is the distinct 'I could not reach the box at all' code."""
    _, _, env = sandbox(tmp_path, ok_addrs=(), tailscale=[peer("workbench")])
    r = run(env, "path")
    assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)


def test_no_path_reachable_exits_3_for_an_action_verb(tmp_path):
    _, log, env = sandbox(tmp_path, ok_addrs=())
    r = run(env, "run", "echo", "hi")
    assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
    assert non_probe(log) == [], "no command should be attempted with no path up"


def test_all_three_paths_are_probed_every_time(tmp_path):
    """The tool reports the state of ALL paths, not just the first that works."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,), tailscale=[peer("workbench")])
    r = run(env, "--json", "path")
    paths = {p["path"] for p in json.loads(r.stdout)["paths"]}
    assert paths == {"lan", "nebula", "tailscale"}, r.stdout


# ---------------------------------------------------------------------------
# 2. Three states, never two
# ---------------------------------------------------------------------------


def test_tailscale_absent_is_not_configured(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,), tailscale=None)
    r = run(env, "--json", "path")
    ts = [p for p in json.loads(r.stdout)["paths"] if p["path"] == "tailscale"][0]
    assert ts["state"] == "not-configured", r.stdout
    assert ts["address"] is None, r.stdout
    assert "binary" in ts["detail"], ts["detail"]


def test_tailscale_present_but_down_is_unreachable_not_not_configured(tmp_path):
    """THE central distinction. Same absence of a connection, different cause.

    Collapsing these two into one 'failed' state is the defect this tool exists
    to avoid, so the two must not merely differ in prose — they must differ in
    the machine-readable state field.
    """
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,), tailscale=[peer("workbench")])
    r = run(env, "--json", "path")
    ts = [p for p in json.loads(r.stdout)["paths"] if p["path"] == "tailscale"][0]
    assert ts["state"] == "unreachable", r.stdout
    assert ts["address"] == TS, r.stdout


def test_the_two_tailscale_failures_produce_different_output(tmp_path):
    """Belt and braces: the two runs must not be byte-identical."""
    _, _, absent_env = sandbox(tmp_path / "a", ok_addrs=(LAN,), tailscale=None)
    _, _, down_env = sandbox(tmp_path / "b", ok_addrs=(LAN,), tailscale=[peer("workbench")])
    (tmp_path / "a").mkdir(exist_ok=True)
    (tmp_path / "b").mkdir(exist_ok=True)
    absent = run(absent_env, "path").stdout
    down = run(down_env, "path").stdout
    assert absent != down, "absent and down rendered identically"
    assert "not-configured" in absent and "unreachable" in down


def test_tailnet_without_this_host_is_not_configured(tmp_path):
    """A tailnet that exists but does not carry this host is still 'not yet
    configured' — a different fix from 'the tunnel is down'."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,), tailscale=[peer("someone-else")])
    r = run(env, "--json", "path")
    ts = [p for p in json.loads(r.stdout)["paths"] if p["path"] == "tailscale"][0]
    assert ts["state"] == "not-configured", r.stdout
    assert "not in the tailnet" in ts["detail"], ts["detail"]


def test_tailscale_lights_up_with_no_code_change(tmp_path):
    """The requirement that tailscale works the day it is deployed."""
    _, _, env = sandbox(tmp_path, ok_addrs=(TS,), tailscale=[peer("workbench")])
    r = run(env, "--json", "path")
    payload = json.loads(r.stdout)
    ts = [p for p in payload["paths"] if p["path"] == "tailscale"][0]
    assert ts["state"] == "ok", r.stdout
    assert payload["selected"] == "tailscale", r.stdout


# ---------------------------------------------------------------------------
# 3. Exit-code passthrough
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", [0, 1, 42, 7])
def test_remote_exit_code_passes_through_unchanged(tmp_path, code):
    """A tool that swallows remote exit codes is unusable in a script."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "run", "exit %d" % code)
    assert r.returncode == code, (code, r.returncode, r.stdout, r.stderr)


def test_run_false_exits_1(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "run", "false")
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)


def test_exit_3_is_distinguishable_from_a_remote_failure(tmp_path):
    """Both are non-zero; they must not be the same non-zero."""
    _, _, up = sandbox(tmp_path / "u", ok_addrs=(LAN,))
    (tmp_path / "u").mkdir(exist_ok=True)
    _, _, down = sandbox(tmp_path / "d", ok_addrs=())
    (tmp_path / "d").mkdir(exist_ok=True)
    assert run(up, "run", "exit 1").returncode == 1
    assert run(down, "run", "exit 1").returncode == 3


# ---------------------------------------------------------------------------
# 4. HostKeyAlias on every path x every ssh-based verb
# ---------------------------------------------------------------------------

SSH_BASED = [
    ("ssh", []),
    ("run", ["uptime"]),
    ("tmux", []),
    ("scp", ["./a.txt", ":/tmp/a.txt"]),
    ("rsync", ["-a", "./d/", ":/tmp/d/"]),
    ("forward", ["8080:localhost:80"]),
    ("kubectl", ["get", "pods"]),
]


@pytest.mark.parametrize("path,addr", [("lan", LAN), ("nebula", NEBULA), ("tailscale", TS)])
@pytest.mark.parametrize("verb,args", SSH_BASED, ids=[v for v, _ in SSH_BASED])
def test_host_key_alias_is_set_for_every_path_and_verb(tmp_path, path, addr, verb, args):
    """One machine, three addresses, ONE known_hosts entry.

    Without HostKeyAlias each path accrues its own known_hosts entry keyed on
    the address, and the day an address is reused for a different machine the
    operator hits a host-key-mismatch wall. Measured on the dev host before this
    tool existed: two raw-address entries for a single machine already present.
    """
    _, _, env = sandbox(tmp_path, ok_addrs=(addr,), tailscale=[peer("workbench")])
    r = run(env, "--path", path, "--dry-run", verb, *args)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert "HostKeyAlias=workbench" in r.stdout, (verb, path, r.stdout)
    assert addr in r.stdout, (verb, path, r.stdout)


def test_rsync_threads_the_alias_through_dash_e(tmp_path):
    """rsync opens its OWN ssh, so the options must reach it via -e or the
    alias is silently lost for exactly this verb."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "--dry-run", "rsync", "-a", "./d/", ":/tmp/d/")
    assert "-e ssh -o HostKeyAlias=workbench" in r.stdout, r.stdout


def test_the_probe_itself_carries_the_alias(tmp_path):
    """Probing without the alias would pollute known_hosts on every run, which
    is the same defect one layer down."""
    _, log, env = sandbox(tmp_path, ok_addrs=(LAN,))
    run(env, "path")
    probes = [a for a in invocations(log) if a[-1] == "true"]
    assert probes, "no probe was recorded"
    for argv in probes:
        assert "HostKeyAlias=workbench" in argv, argv


# ---------------------------------------------------------------------------
# 5. The probe is a real auth handshake
# ---------------------------------------------------------------------------


def test_probe_is_a_real_ssh_session_not_a_port_check(tmp_path):
    """A port that accepts a connection is not a session you can open.

    Measured on this fleet: both addresses complete TCP and the SSH transport
    layer and then fail auth with `Permission denied`, exiting 255. A TCP probe
    calls that reachable; only running a command over BatchMode proves it.
    """
    _, log, env = sandbox(tmp_path, ok_addrs=(LAN,))
    run(env, "path")
    probes = [a for a in invocations(log) if a[-1] == "true"]
    assert len(probes) >= 2, invocations(log)
    for argv in probes:
        assert argv[0] == "ssh", argv
        assert "BatchMode=yes" in argv, argv
        assert argv[-1] == "true", argv


def test_an_ssh_that_fails_auth_is_unreachable_not_ok(tmp_path):
    """The stub exits 255 exactly as real ssh does on auth failure."""
    _, _, env = sandbox(tmp_path, ok_addrs=())
    r = run(env, "--json", "path")
    states = {p["path"]: p["state"] for p in json.loads(r.stdout)["paths"]}
    assert states["lan"] == "unreachable", r.stdout
    assert states["nebula"] == "unreachable", r.stdout


# ---------------------------------------------------------------------------
# 6. Local vs remote execution
# ---------------------------------------------------------------------------


def test_runs_locally_when_the_nebula_address_is_ours(tmp_path):
    """On the target host, exec locally instead of SSHing to ourselves."""
    _, log, env = sandbox(tmp_path, ok_addrs=(LAN,), local_addrs=(NEBULA,))
    r = run(env, "run", "echo", "hello-local")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert r.stdout.strip() == "hello-local", r.stdout
    assert non_probe(log) == [], "should not have opened an ssh session to itself"


def test_runs_locally_when_the_lan_address_is_ours(tmp_path):
    _, log, env = sandbox(tmp_path, ok_addrs=(LAN,), local_addrs=(LAN,))
    r = run(env, "run", "echo", "hello-local")
    assert r.stdout.strip() == "hello-local", r.stdout
    assert non_probe(log) == [], non_probe(log)


def test_runs_remotely_when_no_address_is_ours(tmp_path):
    _, log, env = sandbox(tmp_path, ok_addrs=(LAN,), local_addrs=("10.9.9.9",))
    r = run(env, "run", "echo", "hello-remote")
    assert r.stdout.strip() == "hello-remote", r.stdout
    remote = non_probe(log)
    assert remote and remote[0][0] == "ssh", remote
    assert LAN in remote[0], remote


def test_hostname_is_not_the_discriminator(tmp_path):
    """Both NixOS hosts report the hostname `nixos`, so a hostname-based check
    would make one machine believe it is the other. Proven by making the
    ADDRESSES say remote while the hostname stays whatever the host's really is:
    the tool must go over the wire regardless."""
    _, log, env = sandbox(tmp_path, ok_addrs=(LAN,), local_addrs=("10.9.9.9",))
    env["HOSTNAME"] = "workbench"
    r = run(env, "run", "echo", "x")
    assert non_probe(log), "went local on a hostname match; addresses said remote"


def test_local_exec_still_works_when_no_path_is_reachable(tmp_path):
    """You do not need a network path to the machine you are sitting on.

    This is a regression guard: the first implementation returned exit 3 here,
    which made `workhost` unusable on workbench itself.
    """
    _, _, env = sandbox(tmp_path, ok_addrs=(), local_addrs=(NEBULA,))
    r = run(env, "run", "echo", "still-works")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert r.stdout.strip() == "still-works", r.stdout


def test_scp_becomes_a_local_copy_on_the_target(tmp_path):
    """The leading `:` still means "the host", which is now just here."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,), local_addrs=(NEBULA,))
    src = tmp_path / "src.txt"
    src.write_text("payload", encoding="utf-8")
    dst = tmp_path / "dst.txt"
    r = run(env, "scp", str(src), ":%s" % dst)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert dst.read_text(encoding="utf-8") == "payload", "local scp did not copy"


def test_tmux_attaches_locally_on_the_target(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,), local_addrs=(NEBULA,))
    r = run(env, "--dry-run", "tmux", "work")
    assert r.stdout.strip() == "tmux new-session -A -s work", r.stdout


def test_bare_ssh_on_the_target_is_a_local_shell(tmp_path):
    """SSHing to yourself to get a shell is theatre; run the shell."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,), local_addrs=(NEBULA,))
    env["SHELL"] = "/bin/sh"
    r = run(env, "--dry-run", "ssh")
    assert r.stdout.strip() == "/bin/sh", r.stdout


def test_forward_is_refused_on_the_target_itself(tmp_path):
    """Forwarding a port to yourself would hang on an ssh that never returns,
    so it fails loudly instead."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,), local_addrs=(NEBULA,))
    r = run(env, "forward", "8080:localhost:80")
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert "meaningless" in r.stderr, r.stderr


def test_path_verb_reports_local_and_exits_zero_on_the_target(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=(), local_addrs=(NEBULA,))
    r = run(env, "--json", "path")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert json.loads(r.stdout)["local"] is True, r.stdout


# ---------------------------------------------------------------------------
# 7. --path forcing
# ---------------------------------------------------------------------------


def test_forcing_a_down_path_fails_and_does_not_fall_back(tmp_path):
    """Silently using a different path would defeat the flag entirely."""
    _, log, env = sandbox(tmp_path, ok_addrs=(NEBULA,))
    r = run(env, "--path", "lan", "run", "echo", "nope")
    assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
    assert "lan" in r.stderr and "unreachable" in r.stderr, r.stderr
    assert non_probe(log) == [], "fell back to another path"


def test_forcing_a_not_configured_path_fails_clearly(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,), tailscale=None)
    r = run(env, "--path", "tailscale", "run", "echo", "nope")
    assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
    assert "not-configured" in r.stderr, r.stderr


def test_forcing_an_up_path_uses_exactly_that_path(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN, NEBULA))
    r = run(env, "--path", "nebula", "--dry-run", "ssh")
    assert NEBULA in r.stdout and LAN not in r.stdout, r.stdout


def test_forcing_a_path_overrides_local_exec(tmp_path):
    """Asking explicitly for the wire is a request to use the wire."""
    _, log, env = sandbox(tmp_path, ok_addrs=(LAN,), local_addrs=(NEBULA,))
    r = run(env, "--path", "lan", "run", "echo", "over-the-wire")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert non_probe(log), "forced path was ignored in favour of local exec"


# ---------------------------------------------------------------------------
# 8. --json shape
# ---------------------------------------------------------------------------


def test_json_shape_field_by_field(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=(NEBULA,), tailscale=None)
    r = run(env, "--json", "path")
    payload = json.loads(r.stdout)

    assert payload["host"] == "workbench"
    assert payload["selected"] == "nebula"
    assert payload["local"] is False
    assert isinstance(payload["paths"], list) and len(payload["paths"]) == 3

    by_path = {p["path"]: p for p in payload["paths"]}
    assert set(by_path) == {"lan", "nebula", "tailscale"}

    assert by_path["lan"]["state"] == "unreachable"
    assert by_path["lan"]["address"] == LAN
    assert by_path["nebula"]["state"] == "ok"
    assert by_path["nebula"]["address"] == NEBULA
    assert by_path["tailscale"]["state"] == "not-configured"
    assert by_path["tailscale"]["address"] is None

    for entry in payload["paths"]:
        assert set(entry) == {"path", "state", "address", "detail", "elapsed_ms"}
        assert isinstance(entry["elapsed_ms"], int)
        assert isinstance(entry["detail"], str)


def test_json_selected_is_null_when_nothing_is_up(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=())
    r = run(env, "--json", "path")
    assert json.loads(r.stdout)["selected"] is None, r.stdout


def test_flags_after_a_reporting_verb_are_honoured(tmp_path):
    """`workhost path --json` is what people actually type."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "path", "--json")
    json.loads(r.stdout)  # raises if the flag was swallowed


# ---------------------------------------------------------------------------
# 9. Parallelism and --timeout
# ---------------------------------------------------------------------------


def probe_intervals(timedir: Path):
    """(start, end) for each stub invocation that recorded timestamps."""
    spans = []
    for path in sorted(timedir.iterdir()):
        start = end = None
        for line in path.read_text(encoding="utf-8").splitlines():
            kind, _, value = line.partition(" ")
            if kind == "S":
                start = float(value)
            elif kind == "E":
                end = float(value)
        if start is not None and end is not None:
            spans.append((start, end))
    return spans


def test_probes_run_in_parallel_not_in_series(tmp_path):
    """All three probes must be in flight AT THE SAME MOMENT.

    Asserted by interval OVERLAP, not by total wall time. That distinction is
    the point: a wall-time budget ("3x1s probes in under 2.2s") fails on a
    loaded box and the only ways to make it pass again are to widen the margin
    until it asserts nothing or to re-run until green. Overlap has no such
    failure mode — SERIAL EXECUTION CANNOT PRODUCE OVERLAPPING INTERVALS AT ANY
    LOAD, because probe 2 does not start until probe 1 has returned. Load can
    stretch the intervals; it cannot make disjoint ones overlap.

    Serial probing would put three timeouts in front of every command, which is
    the difference between a tool you put in front of ssh and one you do not.
    """
    delay = 1.0
    _, log, env = sandbox(
        tmp_path, ok_addrs=(LAN, NEBULA, TS), tailscale=[peer("workbench")], delay=delay
    )
    r = run(env, "--timeout", "10", "path")

    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert len([a for a in invocations(log) if a[-1] == "true"]) == 3, invocations(log)

    spans = probe_intervals(tmp_path / "times.d")
    assert len(spans) == 3, spans
    # A single instant at which all three were running: the latest start still
    # precedes the earliest end.
    latest_start = max(s for s, _ in spans)
    earliest_end = min(e for _, e in spans)
    assert latest_start < earliest_end, (
        "probes did not overlap — looks serial. spans=%r" % (spans,)
    )


def test_timeout_is_honoured(tmp_path):
    """A hung path must not hang the tool.

    Asserted structurally rather than on wall time. The stub sleeps 10s while
    the probe is given 1s; the `timed out` detail is reachable ONLY through
    subprocess.TimeoutExpired, so seeing it proves the deadline fired and the
    10s sleep was abandoned. Were the timeout dropped, the probe would instead
    wait out the sleep and report `ok` — a different state, not a slower one.
    """
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,), delay=10)
    r = run(env, "--timeout", "1", "--json", "path")

    states = {p["path"]: p for p in json.loads(r.stdout)["paths"]}
    assert states["lan"]["state"] == "unreachable", r.stdout
    assert "timed out" in states["lan"]["detail"], states["lan"]["detail"]


def test_timeout_reaches_ssh_as_connect_timeout(tmp_path):
    _, log, env = sandbox(tmp_path, ok_addrs=(LAN,))
    run(env, "--timeout", "7", "path")
    probes = [a for a in invocations(log) if a[-1] == "true"]
    assert probes
    assert "ConnectTimeout=7" in probes[0], probes[0]


# ---------------------------------------------------------------------------
# 10. Argument forwarding, including spaces and quotes
# ---------------------------------------------------------------------------

NASTY = 'a b "c" \'d\' $e `f` \\g'


def test_ssh_forwards_arguments_verbatim(tmp_path):
    _, log, env = sandbox(tmp_path, ok_addrs=(LAN,))
    run(env, "ssh", "-N", NASTY)
    argv = non_probe(log)[0]
    assert NASTY in argv, argv


@pytest.mark.parametrize("verb", ["scp", "rsync", "tmux", "kubectl"])
def test_verbs_forward_arguments_verbatim(tmp_path, verb):
    _, log, env = sandbox(tmp_path, ok_addrs=(LAN,), tunnel=True)
    run(env, "--timeout", "5", verb, NASTY)
    recorded = [a for a in invocations(log) if a[0] == verb or a[0] == "ssh"]
    flat = [arg for argv in recorded for arg in argv]
    assert NASTY in flat, recorded


def test_run_joins_arguments_the_way_ssh_does(tmp_path):
    """`run` mirrors plain ssh: trailing args are joined and handed to the
    remote shell. That is what makes `run 'exit 42'` work AND `run ls -la`."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "run", "echo", "one", "two")
    assert r.stdout.strip() == "one two", r.stdout


def test_run_preserves_a_quoted_argument_end_to_end(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "run", "printf '%s' 'a b'")
    assert r.stdout == "a b", repr(r.stdout)


def test_scp_substitutes_the_address_for_a_leading_colon(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=(NEBULA,))
    r = run(env, "--dry-run", "scp", "./local.txt", ":/tmp/remote.txt")
    assert "%s:/tmp/remote.txt" % NEBULA in r.stdout, r.stdout
    assert "./local.txt" in r.stdout, r.stdout


def test_forward_builds_a_dash_L_tunnel(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "--dry-run", "forward", "8080:localhost:80")
    assert "-N" in r.stdout and "-L 8080:localhost:80" in r.stdout, r.stdout


def test_tmux_requests_a_pty(tmp_path):
    """tmux attach without a PTY fails; -t is not optional here."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "--dry-run", "tmux")
    assert r.stdout.startswith("ssh -t "), r.stdout


# ---------------------------------------------------------------------------
# 11. kubectl
# ---------------------------------------------------------------------------


def test_kubectl_runs_through_a_tunnel_to_the_hosts_loopback(tmp_path):
    """The nebula address is NOT in the k3s serving cert's SANs (measured), so
    a `--server=https://<address>:6443` rewrite is TLS-invalid over that path.
    Tunnelling to the host's own loopback keeps the cert valid over every path.
    """
    _, log, env = sandbox(tmp_path, ok_addrs=(LAN,), tunnel=True)
    r = run(env, "--timeout", "8", "kubectl", "get", "nodes")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)

    kube = [a for a in invocations(log) if a[0] == "kubectl"]
    assert kube, invocations(log)
    argv = kube[0]
    assert "--server" in argv, argv
    server = argv[argv.index("--server") + 1]
    assert server.startswith("https://127.0.0.1:"), server
    assert argv[-2:] == ["get", "nodes"], argv


def test_kubectl_tunnel_targets_the_hosts_apiserver_port(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "--dry-run", "kubectl", "get", "pods")
    assert ":127.0.0.1:6443" in r.stdout, r.stdout
    assert "-N" in r.stdout, r.stdout


def test_kubectl_runs_directly_when_on_the_target(tmp_path):
    """On the host itself the kubeconfig's 127.0.0.1:6443 is already correct."""
    _, log, env = sandbox(tmp_path, ok_addrs=(LAN,), local_addrs=(NEBULA,))
    r = run(env, "kubectl", "get", "nodes")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    kube = [a for a in invocations(log) if a[0] == "kubectl"]
    assert kube == [["kubectl", "get", "nodes"]], kube


# ---------------------------------------------------------------------------
# 12. Streams, hosts and usage
# ---------------------------------------------------------------------------


def test_stdout_carries_only_the_remote_output_in_the_quiet_case(tmp_path):
    """Note what this does NOT prove: in the quiet case no report is emitted at
    all, so it says nothing about WHICH stream a report would go to. The two
    tests below are the ones that pin that — a mutation battery caught this one
    passing vacuously against a report_stream mutant."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "run", "echo", "payload")
    assert r.stdout == "payload\n", repr(r.stdout)
    assert "workhost:" not in r.stdout, r.stdout


def test_the_report_goes_to_stderr_so_stdout_stays_pipeable(tmp_path):
    """With the report actually emitted, stdout must still be only the payload."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "-v", "run", "echo", "payload")
    assert r.stdout == "payload\n", repr(r.stdout)
    assert "workhost: workbench" in r.stderr, r.stderr


def test_the_json_report_also_goes_to_stderr_for_an_action_verb(tmp_path):
    """`workhost --json run …` must not corrupt the command's own stdout."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "--json", "run", "echo", "payload")
    assert r.stdout == "payload\n", repr(r.stdout)
    assert json.loads(r.stderr)["selected"] == "lan", r.stderr


def test_the_report_goes_to_stdout_for_the_path_verb(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "path")
    assert "workhost: workbench" in r.stdout, r.stdout


def test_check_is_an_alias_for_path(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    a = run(env, "--json", "path").stdout
    b = run(env, "--json", "check").stdout
    assert json.loads(a)["selected"] == json.loads(b)["selected"]
    assert json.loads(b)["host"] == "workbench"


def test_ssh_is_the_default_verb(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    bare = run(env, "--dry-run").stdout
    explicit = run(env, "--dry-run", "ssh").stdout
    assert bare == explicit, (bare, explicit)
    assert bare.startswith("ssh "), bare


def test_unknown_host_exits_2_and_names_the_known_ones(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "-H", "nosuchhost", "path")
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert "workbench" in r.stderr, r.stderr


def test_unknown_verb_is_a_usage_error(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "frobnicate")
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)


def test_workhost_host_env_var_selects_the_host(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    env["WORKHOST_HOST"] = "nosuchhost"
    r = run(env, "path")
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert "nosuchhost" in r.stderr, r.stderr


def test_dash_H_beats_the_env_var(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    env["WORKHOST_HOST"] = "nosuchhost"
    r = run(env, "-H", "workbench", "path")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)


def test_flags_after_an_action_verb_go_to_the_verb_not_to_workhost(tmp_path):
    """`workhost run --json` must send --json to the remote command."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "run", "echo", "--json")
    assert r.stdout.strip() == "--json", r.stdout
    assert "{" not in r.stdout, r.stdout


def test_no_traceback_on_any_tested_path(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=())
    for args in (["path"], ["run", "true"], ["--json", "check"], ["--dry-run", "ssh"]):
        r = run(env, *args)
        assert "Traceback" not in r.stderr, (args, r.stderr)


# ---------------------------------------------------------------------------
# 13. The host table stays a table
# ---------------------------------------------------------------------------


def test_only_real_hosts_are_in_the_table():
    """Inventing an address for a host nobody has measured produces a confident
    wrong connection, which is worse than the host being absent."""
    src = WORKHOST.read_text(encoding="utf-8")
    body = src.split("HOSTS = {", 1)[1].split("\n}", 1)[0]
    assert '"workbench"' in body
    for absent in ("laptop", "production", "homelab"):
        assert '"%s": Host(' % absent not in body, "%s got invented into the table" % absent


def test_adding_a_host_is_a_table_entry_not_a_code_fork():
    """The verbs must be driven by the Host dataclass, not by host name."""
    src = WORKHOST.read_text(encoding="utf-8")
    build = src.split("def build_remote_command", 1)[1].split("\ndef ", 1)[0]
    assert "workbench" not in build, "the command builder special-cases a host name"


# ---------------------------------------------------------------------------
# 14. Delivery
# ---------------------------------------------------------------------------


def test_the_stub_path_contains_only_declared_stubs(tmp_path):
    """The live invariant behind this file's PATH clobber.

    `sandbox()` REPLACES PATH rather than prepending to it, which
    scripts/tests/test_no_real_launchers.py pins as a deliberate site. Replacing
    is required, not stylistic: the `not-configured` assertions depend on
    `tailscale` being genuinely ABSENT, and no amount of PREPENDING can make a
    binary unfindable if one is ever installed on the dev host. The real `ip` is
    the mirror image — on workbench it reports the nebula address, so a
    prepended PATH would let the host's true identity leak into tests that must
    believe they are remote.

    What makes that safe is enumeration: the directory holds only the names
    below, and every launcher-shaped one among them is a stub THIS FILE wrote.
    Asserted here rather than asserted in prose, so it cannot rot.
    """
    bindir, _, _ = sandbox(tmp_path, ok_addrs=(LAN,), tailscale=[peer("workbench")])
    names = {p.name for p in bindir.iterdir()}

    assert names <= SANDBOX_BINARIES, "undeclared binary on the stub PATH: %s" % (
        names - SANDBOX_BINARIES,
    )
    # Every launcher-shaped name is a regular file we wrote inside tmp_path —
    # never a symlink or copy reaching a real system binary.
    for name in SANDBOX_FAKED_LAUNCHERS & names:
        path = bindir / name
        assert not path.is_symlink(), "%s escapes to a real binary" % name
        body = path.read_text(encoding="utf-8", errors="replace")
        assert "WH_LOGDIR" in body, "%s is not one of this file's stubs" % name


def test_workhost_is_executable():
    """home.file executable=true is not enough — the tree bit is what ships."""
    assert os.access(WORKHOST, os.X_OK), "%s is not executable" % WORKHOST


def test_workhost_is_deployed_by_home_manager():
    """A CLI that is not on PATH is not delivered."""
    nix = (REPO / "nix" / "home.nix").read_text(encoding="utf-8")
    assert '.local/bin/workhost' in nix, "workhost is not wired onto PATH in nix/home.nix"
    assert "devrc/scripts/workhost" in nix, "the symlink does not point at the script"


def test_workhost_is_tracked_by_git():
    """The flake silently omits an untracked file, so a green switch can still
    ship nothing."""
    r = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--error-unmatch", "scripts/workhost"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, "scripts/workhost is not git-tracked: %s" % r.stderr
