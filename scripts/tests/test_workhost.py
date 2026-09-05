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

import importlib.machinery
import importlib.util
import json
import os
import re
import shlex
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
BATCH=""
for a in "$@"; do
  case "$a" in
    *:127.0.0.1:*) LSPEC="$a" ;;
    BatchMode=yes) BATCH=1 ;;
  esac
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

# Real ssh exits 255 for EVERY connection-level failure — auth, DNS, timeout
# and host key alike. A stub that only ever exits 255 therefore cannot tell
# those apart, and a suite built on it ASSERTS the conflation: `255` is all it
# can ever see. So the three cases are separated the only way real ssh
# separates them, by the MESSAGE, and each is reachable from a test.
if [ $ok -ne 0 ]; then
  # A key that CHANGED is refused whether or not there is a human at the
  # keyboard: real ssh does not offer to accept it, it tells you to remove the
  # offending known_hosts line.
  for a in ${WH_HOSTKEY_CHANGED_ADDRS:-}; do
    if [ "$a" = "$ADDR" ]; then
      printf '@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@\\n' >&2
      printf 'IT IS POSSIBLE THAT SOMEONE IS DOING SOMETHING NASTY!\\n' >&2
      printf 'Host key verification failed.\\n' >&2
      exit 255
    fi
  done
  # A key never seen before is refused under BatchMode and ACCEPTED
  # interactively — which is the whole mechanism --accept-key relies on, so the
  # stub has to model the difference rather than exit 255 unconditionally.
  for a in ${WH_HOSTKEY_MISSING_ADDRS:-}; do
    if [ "$a" = "$ADDR" ]; then
      if [ -n "$BATCH" ]; then
        printf 'The authenticity of host %s cannot be established.\\n' "$ADDR" >&2
        printf 'Host key verification failed.\\n' >&2
        exit 255
      fi
      ok=0
    fi
  done
fi

if [ $ok -ne 0 ]; then
  printf 'someone@%s: Permission denied (publickey,password).\\n' "$ADDR" >&2
  exit 255
fi

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
    tailscale_raw=None,
    delay=None,
    tunnel=False,
    hostkey_missing=(),
    hostkey_changed=(),
    ssh=True,
):
    """Build a stub-only PATH and the env that drives it.

    ``tailscale`` is None for "no tailscale binary exists at all", or a list of
    peer dicts for a tailnet that does exist. Those two are different states and
    the tests below assert they stay different. ``tailscale_raw`` writes an
    arbitrary JSON body instead, for the valid-but-not-an-object cases.

    ``hostkey_missing`` / ``hostkey_changed`` list addresses for which the ssh
    stub reproduces ssh's two host-key refusals. They are separate from
    ``ok_addrs`` on purpose: an address in neither set gets an AUTH failure, so
    the three ways of exiting 255 are all reachable and can be pinned apart.

    ``ssh=False`` writes no ssh stub at all — the "no ssh binary on PATH" case,
    which is the client-side mirror of `tailscale` being absent.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "argv.d"
    log.mkdir(exist_ok=True)
    timedir = tmp_path / "times.d"
    timedir.mkdir(exist_ok=True)

    if ssh:
        mockbin.write_exec(bindir / "ssh", SSH_STUB)
    for name in ("scp", "rsync", "tmux", "kubectl"):
        mockbin.write_exec(bindir / name, RECORDER_STUB)
    mockbin.write_exec(bindir / "ip", _ip_stub(local_addrs))

    if tailscale_raw is not None:
        mockbin.write_exec(bindir / "tailscale", _tailscale_stub(tailscale_raw))
    elif tailscale is not None:
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
        "WH_HOSTKEY_MISSING_ADDRS": " ".join(hostkey_missing),
        "WH_HOSTKEY_CHANGED_ADDRS": " ".join(hostkey_changed),
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


#: Every backticked `workhost …` command the tool prints. A string that names a
#: command is a CLAIM ABOUT THE PARSER, and one of them was false — see
#: test_every_workhost_command_the_tool_prints_actually_parses.
WORKHOST_COMMAND_RE = re.compile(r"`(workhost [^`]*)`")


def printed_workhost_commands(*streams):
    out = []
    for text in streams:
        out += WORKHOST_COMMAND_RE.findall(text or "")
    return out


def parse_as_workhost(mod, command):
    """Feed a printed command back through the REAL argument pipeline.

    Returns ``(verb, verb_args, opts)``. This is the pin that a substring grep
    cannot be: `--accept-key` was present in the advice text the whole time it
    was being silently discarded by the parser, so any test that only looked
    for the substring passed against the broken behaviour.
    """
    tokens = shlex.split(command)
    assert tokens and tokens[0] == "workhost", command
    # Indexed rather than unpacked so the arity of split_argv's return is not
    # itself part of the assertion. Against the PRE-FIX source (a 3-tuple) this
    # test must go red on the CLAIM — `accept_key` is False — not on a
    # ValueError from unpacking, which would be red for a harness reason and
    # would prove nothing about the guard.
    parts = mod.split_argv(tokens[1:])
    globals_, verb, verb_args = parts[0], parts[1], parts[2]
    opts = mod.build_parser().parse_args(globals_)
    assert verb in mod.VERBS, (command, verb)
    return verb, verb_args, opts


def load_workhost():
    """Import `scripts/workhost` as a module, freshly, for unit-level checks.

    Used ONLY where the CLI cannot reach the branch — i.e. where the input is a
    `Host` that deliberately does not exist in the shipped table. A fresh module
    object each call so a test that rebinds a function cannot leak into another.
    """
    loader = importlib.machinery.SourceFileLoader("workhost_under_test", str(WORKHOST))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    # @dataclass resolves `cls.__module__` through sys.modules while the class
    # body executes, so the module must be registered BEFORE exec_module or the
    # decorator dies on a None lookup. Popped again so nothing leaks.
    sys.modules[loader.name] = mod
    try:
        loader.exec_module(mod)
    finally:
        sys.modules.pop(loader.name, None)
    return mod


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


@pytest.mark.parametrize("code", [1, 2, 42])
def test_exit_3_is_distinguishable_from_a_remote_failure(tmp_path, code):
    """Both are non-zero; for these codes they are not the same non-zero.

    Parametrised over more than one remote code on purpose. The single-value
    version of this test carried a name — and backed a docstring — far wider
    than what it proved: `[1]` alone says nothing about 3, which is exactly
    where the claim breaks. See the two collision tests below, which pin the
    boundary rather than let the name imply it does not exist.
    """
    _, _, up = sandbox(tmp_path / "u", ok_addrs=(LAN,))
    (tmp_path / "u").mkdir(exist_ok=True)
    _, _, down = sandbox(tmp_path / "d", ok_addrs=())
    (tmp_path / "d").mkdir(exist_ok=True)
    assert run(up, "run", "exit %d" % code).returncode == code
    assert run(down, "run", "exit %d" % code).returncode == 3


def test_exit_3_collides_with_a_remote_exit_3_and_json_disambiguates(tmp_path):
    """🔴 The honest boundary of the exit-code contract.

    `EXIT_NO_PATH = 3` is conventional, not reserved: a remote command that
    chooses `exit 3` produces the same status as "no path to the box". The
    docstring in `scripts/workhost` used to claim 3 was "distinct from any
    remote exit code", which is wider than the code. This test is what stops
    that claim regrowing, and names the channel that IS unambiguous — `--json`,
    where `selected` is null exactly when no path was usable.
    """
    _, _, up = sandbox(tmp_path / "u", ok_addrs=(LAN,))
    (tmp_path / "u").mkdir(exist_ok=True)
    _, _, down = sandbox(tmp_path / "d", ok_addrs=())
    (tmp_path / "d").mkdir(exist_ok=True)

    a = run(up, "--json", "run", "exit 3")
    b = run(down, "--json", "run", "exit 3")
    assert a.returncode == 3 and b.returncode == 3, (a.returncode, b.returncode)

    # The no-path run also writes a `workhost: …` line after the report, so the
    # JSON is decoded off the front rather than from the whole stream.
    decode = json.JSONDecoder().raw_decode
    assert decode(a.stderr)[0]["selected"] == "lan", a.stderr
    assert decode(b.stderr)[0]["selected"] is None, b.stderr


def test_usage_exit_2_collides_with_a_remote_exit_2(tmp_path):
    """The second collision the PR body used to gloss over.

    Discriminated by the `workhost:` prefix on stderr, which a remote command's
    own failure never produces.
    """
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    remote = run(env, "run", "exit 2")
    usage = run(env, "-H", "nosuchhost", "run", "exit 2")

    assert remote.returncode == 2 and usage.returncode == 2
    assert "workhost:" not in remote.stderr, remote.stderr
    assert "workhost: unknown host" in usage.stderr, usage.stderr


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
    # `--dry-run` shlex-joins now, so the -e value is printed as ONE quoted
    # argument — which is what it is. Asserting the quoted form rather than a
    # bare substring keeps the guard honest about what actually gets exec'd.
    assert "-e 'ssh -o HostKeyAlias=workbench" in r.stdout, r.stdout


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


def test_the_lan_address_alone_does_not_prove_we_are_the_target(tmp_path):
    """🔴 THE HAZARD, pinned — not merely that the fallback fires.

    A LAN address is unique within ONE SUBNET, so a laptop on a network that
    hands it 192.168.50.250 used to satisfy `is_local_host(workbench)`. Then
    `workhost run 'kubectl delete ns prod'` executed on the LAPTOP, exited 0,
    and in the quiet path printed nothing that said workbench was never
    touched: the same silent wrong-machine execution the hostname check was
    rejected to prevent, one layer down.

    workbench HAS a nebula address in the table, so its LAN address is no
    longer accepted as self-identification. The tool must go over the wire.
    """
    _, log, env = sandbox(tmp_path, ok_addrs=(LAN,), local_addrs=(LAN,))
    r = run(env, "run", "echo", "hello")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    remote = non_probe(log)
    assert remote, "ran LOCALLY on a bare LAN-address match — wrong machine"
    assert remote[0][0] == "ssh", remote
    assert LAN in remote[0], remote


def test_the_lan_address_is_accepted_when_the_host_has_no_nebula_address(tmp_path):
    """The tightening above is conditional, not a removal.

    Driven through the module rather than the CLI because HOSTS deliberately
    holds exactly one real host and that host HAS a nebula address — inventing a
    second table entry to test with is the thing `test_only_real_hosts_are_in_
    the_table` forbids. So the branch is exercised on a Host built in the test.
    """
    mod = load_workhost()
    addrs = {LAN, "10.9.9.9"}
    mod.local_ipv4_addresses = lambda: addrs

    with_nebula = mod.Host(name="w", lan=LAN, nebula=NEBULA)
    without_nebula = mod.Host(name="w", lan=LAN)

    assert mod.is_local_host(with_nebula) is False
    assert mod.is_local_host(without_nebula) is True

    # And the nebula address alone still identifies us, table entry or not.
    mod.local_ipv4_addresses = lambda: {NEBULA}
    assert mod.is_local_host(with_nebula) is True


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
    """The WHOLE argv, not just `-N` and `-L`.

    The earlier version asserted only that those two tokens appeared. That is
    satisfied by an ssh which binds nothing and stays alive anyway, because
    `ExitOnForwardFailure` defaults to `no` — so `forward 8080:…` against an
    already-bound 8080 blocked forever with no verdict while the operator
    believed the tunnel was up. Pinning the exact argv is what makes the fix
    un-droppable.
    """
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "--dry-run", "forward", "8080:localhost:80")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert shlex.split(r.stdout) == [
        "ssh",
        "-N",
        "-L", "8080:localhost:80",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "HostKeyAlias=workbench",
        "-o", "ConnectTimeout=5",
        LAN,
    ], r.stdout


def test_forward_with_no_spec_is_refused_rather_than_forwarding_nothing(tmp_path):
    """`spec = list(args)` was never validated, so a bare `workhost forward`
    built a fully authenticated `ssh -N` with ZERO `-L` flags and idled until
    killed — success-shaped, and under `--dry-run` it printed a plausible
    command. Nothing about that told the operator they had forwarded nothing."""
    _, log, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "forward")
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert "forwards nothing" in r.stderr, r.stderr
    assert non_probe(log) == [], "opened a session despite forwarding nothing"


@pytest.mark.parametrize("spec", ["8080", "8080:localhost", "http:localhost:80", ""])
def test_forward_rejects_a_spec_that_is_not_a_port_forward(tmp_path, spec):
    _, log, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "--dry-run", "forward", spec)
    assert r.returncode == 2, (spec, r.returncode, r.stdout, r.stderr)
    assert r.stdout == "", r.stdout
    assert non_probe(log) == [], non_probe(log)


def test_forward_accepts_an_explicit_bind_address(tmp_path):
    """The narrow shape check must not reject the four-field form."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "--dry-run", "forward", "127.0.0.1:8080:localhost:80")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert "-L 127.0.0.1:8080:localhost:80" in r.stdout, r.stdout


def test_the_kubectl_tunnel_also_exits_on_a_failed_bind(tmp_path):
    """Same defect, and worse: `free_local_port()` picks a port and lets go of
    it, so a thief can take it before ssh binds. Without this option ssh would
    survive the failed bind, the readiness probe would connect to the THIEF, and
    `kubectl --server` would be aimed at an unrelated local service."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "--dry-run", "kubectl", "get", "pods")
    assert "-o ExitOnForwardFailure=yes" in r.stdout, r.stdout


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
    ship nothing.

    🔴 NOT a skipif, and NOT one assertion. This suite runs in TWO TIERS and they
    are blind in opposite directions: the dev shell has the repo's `.git`, the
    nix sandbox does not (its source is copied in, so `git -C REPO ls-files`
    exits 128 `not a git repository`). An earlier version asserted only the git
    form; it was green in the dev shell and RED in CI, which is precisely the
    failure this file is supposed to be able to see rather than suffer.

    So each tier asserts the strongest claim IT can make, and neither is vacuous:

    * `.git` present  -> ask git directly whether the path is tracked.
    * `.git` absent   -> we are inside the sandbox, whose source came from the
      flake. The flake copies TRACKED files only, so the file being HERE AT ALL
      is the tracked-ness proof for this tier. An untracked `scripts/workhost`
      would simply not exist in the sandbox and this arm goes red.
    """
    has_git_metadata = (REPO / ".git").exists()

    if has_git_metadata:
        r = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "--error-unmatch", "scripts/workhost"],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, (
            "scripts/workhost is not git-tracked, so the flake will omit it and a "
            "green `home-manager switch` would ship nothing: %s" % r.stderr)
    else:
        assert WORKHOST.is_file(), (
            "scripts/workhost is absent from a checkout with no .git — i.e. from "
            "the nix sandbox, whose source is the flake's TRACKED-files-only copy. "
            "That means the file is untracked and the flake dropped it.")


# ---------------------------------------------------------------------------
# 15. The fourth state: an untrusted host key is not an unreachable host
# ---------------------------------------------------------------------------
#
# 🔴 Why this section exists at all. `-o HostKeyAlias=workbench` makes ssh look
# up `workbench` in known_hosts and IGNORE the address entries, and the probe
# runs `BatchMode=yes` while `StrictHostKeyChecking` defaults to `ask`. So on any
# client that has never trusted that alias — the laptop, i.e. the ONLY machine
# where this tool does anything, since on workbench it takes the local branch —
# every path reported `unreachable` on a perfectly healthy network and every
# verb refused with exit 3. The tool could not bootstrap itself, and said the
# network was down.
#
# Reproduced live twice before the fix: a fresh client with an empty known_hosts
# running the exact probe argv got `Host key verification failed.` and wrote
# ZERO bytes to known_hosts; this dev host, with the alias present at line 369 of
# 370, got `Permission denied (publickey,…)` instead. The only reason it looked
# fine here is a known_hosts line appended during development.
#
# The stub can now produce all three ways of exiting 255 — auth, key-missing,
# key-changed — so these are pinned APART rather than conflated, in BOTH
# directions.


def states(r):
    return {p["path"]: p for p in json.loads(r.stdout)["paths"]}


def test_a_host_key_refusal_is_untrusted_key_not_unreachable(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=(), hostkey_missing=(LAN, NEBULA))
    r = run(env, "--json", "path")
    by_path = states(r)
    assert by_path["lan"]["state"] == "untrusted-key", r.stdout
    assert by_path["nebula"]["state"] == "untrusted-key", r.stdout
    assert by_path["lan"]["address"] == LAN, r.stdout
    assert "known_hosts" in by_path["lan"]["detail"], by_path["lan"]["detail"]


def test_an_auth_failure_is_unreachable_not_untrusted_key(tmp_path):
    """The other direction. ssh exits 255 for auth failure AND for a host-key
    refusal, so a classifier keyed on the exit status would call both the same
    thing — which is what the suite used to assert."""
    _, _, env = sandbox(tmp_path, ok_addrs=())
    r = run(env, "--json", "path")
    by_path = states(r)
    assert by_path["lan"]["state"] == "unreachable", r.stdout
    assert by_path["nebula"]["state"] == "unreachable", r.stdout
    assert "Permission denied" in by_path["lan"]["detail"], by_path["lan"]["detail"]


def test_the_two_ssh_255_failures_are_told_apart_in_one_run(tmp_path):
    """Both in the SAME invocation, so no ordering or environment difference can
    be what separates them: LAN gets the host-key refusal, nebula the auth one.
    """
    _, _, env = sandbox(tmp_path, ok_addrs=(), hostkey_missing=(LAN,))
    r = run(env, "--json", "path")
    by_path = states(r)
    assert by_path["lan"]["state"] == "untrusted-key", r.stdout
    assert by_path["nebula"]["state"] == "unreachable", r.stdout


def test_a_changed_host_key_is_untrusted_key_with_its_own_detail(tmp_path):
    """Same state, different and more alarming cause: a key that CHANGED is a
    reinstall or an interception, never a first contact."""
    _, _, changed = sandbox(tmp_path / "c", ok_addrs=(), hostkey_changed=(LAN,))
    (tmp_path / "c").mkdir(exist_ok=True)
    _, _, missing = sandbox(tmp_path / "m", ok_addrs=(), hostkey_missing=(LAN,))
    (tmp_path / "m").mkdir(exist_ok=True)

    c = states(run(changed, "--json", "path"))["lan"]
    m = states(run(missing, "--json", "path"))["lan"]

    assert c["state"] == "untrusted-key" and m["state"] == "untrusted-key"
    assert "CHANGED" in c["detail"], c["detail"]
    assert c["detail"] != m["detail"], (c["detail"], m["detail"])


def test_untrusted_key_is_its_own_json_value(tmp_path):
    """Not folded into `unreachable`, and not invented as a detail string on an
    existing state — a machine consumer must be able to branch on it."""
    _, _, env = sandbox(tmp_path, ok_addrs=(), hostkey_missing=(LAN, NEBULA))
    r = run(env, "--json", "path")
    values = {p["state"] for p in json.loads(r.stdout)["paths"]}
    assert "untrusted-key" in values, r.stdout
    assert "unreachable" not in values, r.stdout


def test_the_report_names_the_cause_and_the_way_out(tmp_path):
    """A state name alone still leaves the operator to work out that ssh will
    never prompt, because the PROBE is the thing running BatchMode."""
    _, _, env = sandbox(tmp_path, ok_addrs=(), hostkey_missing=(LAN, NEBULA))
    r = run(env, "path")
    assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
    assert "untrusted-key" in r.stdout, r.stdout
    assert "not in known_hosts" in r.stdout, r.stdout
    assert "workhost ssh --accept-key" in r.stdout, r.stdout
    assert "ssh -o HostKeyAlias=workbench %s" % LAN in r.stdout, r.stdout


def test_a_changed_key_is_not_offered_the_accept_key_shortcut(tmp_path):
    """`--accept-key` is the wrong answer to a key that changed — real ssh does
    not offer to accept one, it tells you to remove the offending line."""
    _, _, env = sandbox(tmp_path, ok_addrs=(), hostkey_changed=(LAN, NEBULA))
    r = run(env, "path")
    assert "ssh-keygen -R workbench" in r.stdout, r.stdout
    assert "--accept-key" not in r.stdout, r.stdout


def test_no_advice_is_printed_when_a_path_is_actually_usable(tmp_path):
    """The advice block must not fire on a run that worked."""
    _, _, env = sandbox(tmp_path, ok_addrs=(NEBULA,), hostkey_missing=(LAN,))
    r = run(env, "path")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert "--accept-key" not in r.stdout, r.stdout


def test_an_action_verb_refuses_and_says_why_when_only_the_key_blocks(tmp_path):
    _, log, env = sandbox(tmp_path, ok_addrs=(), hostkey_missing=(LAN, NEBULA))
    r = run(env, "run", "echo", "hi")
    assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
    assert "host key is not trusted" in r.stderr, r.stderr
    assert "--accept-key" in r.stderr, r.stderr
    assert non_probe(log) == [], non_probe(log)


# --- the bootstrap escape hatch --------------------------------------------


def test_accept_key_lets_an_untrusted_path_be_used(tmp_path):
    """The stub models the real mechanism rather than a canned success: under
    `BatchMode=yes` it refuses the unknown key exactly as ssh does, and without
    BatchMode it proceeds, as ssh does once a human answers the prompt. So this
    test is only green if `--accept-key` actually causes an interactive (no
    BatchMode) connection to be attempted."""
    _, log, env = sandbox(tmp_path, ok_addrs=(), hostkey_missing=(LAN,))
    r = run(env, "--accept-key", "run", "echo", "bootstrapped")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert r.stdout.strip() == "bootstrapped", r.stdout
    remote = non_probe(log)
    assert remote and LAN in remote[0], remote
    assert "BatchMode=yes" not in remote[0], remote[0]


def test_accept_key_does_not_make_an_unreachable_path_usable(tmp_path):
    """It widens the acceptable set by exactly one state. A down path stays
    down, or the flag would be a way to paper over a real outage."""
    _, log, env = sandbox(tmp_path, ok_addrs=())
    r = run(env, "--accept-key", "run", "echo", "nope")
    assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
    assert non_probe(log) == [], non_probe(log)


def test_accept_key_still_prefers_a_path_that_is_already_ok(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=(NEBULA,), hostkey_missing=(LAN,))
    r = run(env, "--accept-key", "--json", "path")
    assert json.loads(r.stdout)["selected"] == "nebula", r.stdout


def test_forcing_an_untrusted_path_needs_the_opt_in(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=(), hostkey_missing=(LAN,))
    refused = run(env, "--path", "lan", "run", "echo", "x")
    assert refused.returncode == 3, (refused.returncode, refused.stderr)
    assert "untrusted-key" in refused.stderr, refused.stderr

    allowed = run(env, "--path", "lan", "--accept-key", "run", "echo", "x")
    assert allowed.returncode == 0, (allowed.returncode, allowed.stderr)


def test_the_probe_never_enables_tofu(tmp_path):
    """TOFU was explicitly rejected: a probe that silently accepts a new host
    key trusts the machine on the operator's behalf, every run, with no record
    that a decision was made."""
    _, log, env = sandbox(tmp_path, ok_addrs=(LAN,))
    run(env, "path")
    probes = [a for a in invocations(log) if a[-1] == "true"]
    assert probes, "no probe was recorded"
    for argv in probes:
        joined = " ".join(argv)
        assert "StrictHostKeyChecking" not in joined, argv
        assert "accept-new" not in joined, argv


def test_accept_key_cannot_reach_the_probe(tmp_path):
    """The opt-in must be structurally unable to leak into the probe: the probe
    argv is byte-identical with and without the flag."""
    _, plain_log, plain = sandbox(tmp_path / "p", ok_addrs=(), hostkey_missing=(LAN,))
    (tmp_path / "p").mkdir(exist_ok=True)
    _, opt_log, opted = sandbox(tmp_path / "o", ok_addrs=(), hostkey_missing=(LAN,))
    (tmp_path / "o").mkdir(exist_ok=True)

    run(plain, "path")
    run(opted, "--accept-key", "path")

    def probe_argvs(log):
        return sorted(tuple(a) for a in invocations(log) if a[-1] == "true")

    assert probe_argvs(plain_log) == probe_argvs(opt_log), (
        probe_argvs(plain_log),
        probe_argvs(opt_log),
    )


# ---------------------------------------------------------------------------
# 16. Failing loudly instead of crashing
# ---------------------------------------------------------------------------


def test_kubectl_without_a_local_kubectl_exits_127_not_a_traceback(tmp_path):
    """The irony this guard preserves: `workhost kubectl` tunnels PRECISELY so
    it can use the LOCAL kubectl, because the remote non-login shell on NixOS
    frequently has none — and the newly-depended-on local binary was the one
    unhandled case. `main`'s FileNotFoundError->127 guard sits on the other
    branch, so this died with a bare traceback and exit 1."""
    bindir, _, env = sandbox(tmp_path, ok_addrs=(LAN,), tunnel=True)
    (bindir / "kubectl").unlink()
    r = run(env, "--timeout", "8", "kubectl", "get", "nodes")
    assert r.returncode == 127, (r.returncode, r.stdout, r.stderr)
    assert "Traceback" not in r.stderr, r.stderr
    assert "kubectl" in r.stderr and "LOCAL" in r.stderr, r.stderr


@pytest.mark.parametrize("body", ["null", "[]", '"nope"', "3"])
def test_a_non_object_tailscale_status_is_not_configured_not_a_crash(tmp_path, body):
    """`null` and `[]` are VALID JSON and have no `.get()`. The AttributeError
    escaped as a traceback and exit 1 from every verb — `path` included, whose
    whole job is to REPORT a broken path rather than crash on one."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,), tailscale_raw=body)
    r = run(env, "--json", "path")
    assert r.returncode == 0, (body, r.returncode, r.stdout, r.stderr)
    assert "Traceback" not in r.stderr, r.stderr
    ts = states(r)["tailscale"]
    assert ts["state"] == "not-configured", r.stdout
    assert "non-object" in ts["detail"], ts["detail"]


def test_no_ssh_binary_is_not_configured_not_unreachable(tmp_path):
    """A missing ssh CLIENT is the same shape of problem as a missing
    `tailscale` binary, which the tool already calls `not-configured`. Calling
    it `unreachable` blamed the network for a missing package — and was
    asymmetric with the tool's own argument that the two must never be folded
    together."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,), ssh=False)
    r = run(env, "--json", "path")
    assert "Traceback" not in r.stderr, r.stderr
    by_path = states(r)
    assert by_path["lan"]["state"] == "not-configured", r.stdout
    assert by_path["nebula"]["state"] == "not-configured", r.stdout
    assert "no ssh binary" in by_path["lan"]["detail"], by_path["lan"]["detail"]


def test_a_junk_workhost_timeout_warns_and_still_runs(tmp_path):
    """`WORKHOST_TIMEOUT=10s` used to raise ValueError while BUILDING the
    parser, so EVERY invocation of every verb crashed — `--help` included — and
    nothing on screen named the variable."""
    _, log, env = sandbox(tmp_path, ok_addrs=(LAN,))
    env["WORKHOST_TIMEOUT"] = "10s"
    r = run(env, "path")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert "Traceback" not in r.stderr, r.stderr
    assert "WORKHOST_TIMEOUT" in r.stderr, r.stderr
    probes = [a for a in invocations(log) if a[-1] == "true"]
    assert probes and "ConnectTimeout=5" in probes[0], probes


@pytest.mark.parametrize("bad", ["", "-1", "0", "abc"])
def test_every_unusable_workhost_timeout_degrades_to_the_default(tmp_path, bad):
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    env["WORKHOST_TIMEOUT"] = bad
    r = run(env, "path")
    assert r.returncode == 0, (bad, r.returncode, r.stdout, r.stderr)
    assert "Traceback" not in r.stderr, r.stderr


def test_a_valid_workhost_timeout_is_still_honoured(tmp_path):
    """The guard degrades the unusable values only — it must not swallow the
    variable's actual purpose."""
    _, log, env = sandbox(tmp_path, ok_addrs=(LAN,))
    env["WORKHOST_TIMEOUT"] = "7"
    run(env, "path")
    probes = [a for a in invocations(log) if a[-1] == "true"]
    assert probes and "ConnectTimeout=7" in probes[0], probes


def test_the_timeout_flag_documents_the_env_var(tmp_path):
    """`--host` names `$WORKHOST_HOST` in its help; `--timeout` did not name
    `$WORKHOST_TIMEOUT`, so the variable that could break every run was
    undiscoverable from the tool itself."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "--help")
    assert "$WORKHOST_TIMEOUT" in r.stdout, r.stdout


# ---------------------------------------------------------------------------
# 17. --dry-run prints what actually runs
# ---------------------------------------------------------------------------


def test_dry_run_quotes_arguments_containing_spaces(tmp_path):
    """A bare space-join prints a command that is NOT what runs the moment an
    argument contains a space: `run 'a b'` printed `… -- a b`, which pastes as
    two arguments. --dry-run's only value is that the printed line and the
    executed argv are the same thing."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "--dry-run", "run", "echo a b")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert shlex.split(r.stdout)[-1] == "echo a b", r.stdout


def test_dry_run_round_trips_a_nasty_argument(tmp_path):
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "--dry-run", "ssh", NASTY)
    assert shlex.split(r.stdout)[-1] == NASTY, r.stdout


def test_the_kubectl_dry_run_admits_its_port_is_a_placeholder(tmp_path):
    """`main` passes `kube_port=0` for the dry run because the real port comes
    from `free_local_port()` at run time. `-L 0:…` reads as a command you could
    paste; it is not one."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "--dry-run", "kubectl", "get", "pods")
    assert "-L 0:127.0.0.1:6443" in r.stdout, r.stdout
    assert "placeholder" in r.stderr, r.stderr


# ---------------------------------------------------------------------------
# 18. A string that names a command is a claim about the parser
# ---------------------------------------------------------------------------
#
# 🔴 Why this section exists. The advice added for the untrusted-key state read
# "run `workhost ssh --accept-key` once to trust it" — and that exact command
# DID NOT WORK. Flags after an action verb belong to the verb, so `--accept-key`
# fell into ssh's pass-through args, was never parsed, and the run exited 3 with
# "re-run with --accept-key to trust it once" — the flag the operator had just
# passed. Measured on a fresh client with a real sshd:
#
#   workhost --accept-key ssh   -> reached real ssh, host-key prompt. WORKS.
#   workhost ssh --accept-key   -> rc 3, "re-run with --accept-key". LOOPS.
#
# The same "cannot bootstrap itself" defect one layer up, hitting exactly when
# the operator is most stuck and least able to guess the other word order.
#
# A test that grepped the advice for the substring `--accept-key` would have
# been GREEN against that, because the substring was there the whole time. So
# these tests pin the ADVICE and the PARSER to each other: the printed command
# is extracted from real output and fed back through `split_argv` + the parser.


def _drop_kubectl(bindir):
    (bindir / "kubectl").unlink()


#: The LEDGER of every situation in which workhost prints a backticked
#: `workhost …` command. Enumerated by reading every emitted string literal in
#: the script (the `sys.std*.write` / `ValueError` / `parser.error` call sites),
#: not by memory. Add a row when you add such a string.
#:
#: 🔴 `expect` is what the command must parse **TO**, not merely that it parses.
#: That distinction is load-bearing, and it was found by the mutation battery
#: rather than by inspection: the first version of this ledger asserted only
#: "it parses", and mutating the advice to `workhost ssh --accept-keys`
#: SURVIVED it. `--accept-keys` is not a workhost option, so `split_argv` drops
#: it into the VERB's arguments and the global half parses perfectly cleanly —
#: which is the exact shape of the bug this whole section exists to catch,
#: sailing through a guard written to catch it. A guard that reads as coverage
#: while providing none is worse than no guard, because it stops anyone looking.
#:
#: `expect` keys:
#:   "workhost_flags" — every `-`-prefixed token belongs to WORKHOST, so none may
#:                      be left in verb_args, and the named opts must be set.
#:   "verb_flags"     — the flags are meant for the verb's own tool; verb_args
#:                      must equal this list exactly.
#:   "verb"           — the verb the command must resolve to.
ADVICE_SCENARIOS = [
    # (id, sandbox kwargs, argv, prep, expect)
    ("untrusted-key-report",
     dict(ok_addrs=(), hostkey_missing=(LAN, NEBULA)), ("path",), None,
     {"verb": "ssh", "workhost_flags": {"accept_key": True}}),
    ("untrusted-key-action-verb",
     dict(ok_addrs=(), hostkey_missing=(LAN, NEBULA)), ("run", "true"), None,
     {"verb": "ssh", "workhost_flags": {"accept_key": True}}),
    # --json suppresses the text advice block, so the ONLY backticked command
    # left on stderr is select_path's own error. Without this row that message
    # would be covered only in aggregate — i.e. not at all, because the advice
    # block would satisfy the assertion on its behalf.
    ("untrusted-key-json-only",
     dict(ok_addrs=(), hostkey_missing=(LAN, NEBULA)), ("--json", "run", "true"), None,
     {"verb": "ssh", "workhost_flags": {"accept_key": True}}),
    ("forward-no-spec", dict(ok_addrs=(LAN,)), ("forward",), None,
     {"verb": "forward", "verb_flags": ["8080:localhost:80"]}),
    ("forward-bad-spec", dict(ok_addrs=(LAN,)), ("forward", "8080"), None,
     {"verb": "ssh", "verb_flags": ["-N", "-L", "8080"]}),
    ("no-local-kubectl",
     dict(ok_addrs=(LAN,), tunnel=True), ("--timeout", "8", "kubectl", "get", "nodes"),
     _drop_kubectl, {"verb": "kubectl", "verb_flags": []}),
]


@pytest.mark.parametrize(
    "kwargs,argv,prep,expect", [(k, a, p, e) for _, k, a, p, e in ADVICE_SCENARIOS],
    ids=[i for i, _, _, _, _ in ADVICE_SCENARIOS],
)
def test_every_workhost_command_the_tool_prints_parses_as_intended(
    tmp_path, kwargs, argv, prep, expect
):
    """Every printed command, fed back through the real pipeline and checked
    against what it is SUPPOSED to mean.

    Positive control is built in: the assertion below fails if NO command was
    found, so a scenario whose message stops naming a command cannot pass by
    matching nothing. That reassuring zero is exactly how this guard would rot.
    """
    bindir, _, env = sandbox(tmp_path, **kwargs)
    if prep is not None:
        prep(bindir)
    r = run(env, *argv)
    commands = printed_workhost_commands(r.stdout, r.stderr)
    assert commands, (argv, r.stdout, r.stderr)

    mod = load_workhost()
    for command in commands:
        verb, verb_args, opts = parse_as_workhost(mod, command)
        assert verb == expect["verb"], (command, verb, expect["verb"])

        if "workhost_flags" in expect:
            for attr, value in expect["workhost_flags"].items():
                assert getattr(opts, attr) is value, (command, attr, getattr(opts, attr))
            leftover = [t for t in verb_args if t.startswith("-")]
            assert leftover == [], (
                "%r: %r was handed to the verb instead of being parsed by "
                "workhost — that is the defect, not a detail" % (command, leftover))

        if "verb_flags" in expect:
            assert verb_args == expect["verb_flags"], (command, verb_args)


def test_the_advice_names_a_command_the_parser_actually_accepts(tmp_path):
    """🔴 THE pairing guard: the advice must yield `accept_key=True`.

    Extracted from real output, parsed by the real pipeline. If the advice is
    reworded into a form the parser drops, or the hoist is removed, this goes
    red — neither side can move without the other.
    """
    _, _, env = sandbox(tmp_path, ok_addrs=(), hostkey_missing=(LAN, NEBULA))
    r = run(env, "path")
    commands = [c for c in printed_workhost_commands(r.stdout) if "--accept-key" in c]
    assert commands, r.stdout

    mod = load_workhost()
    for command in commands:
        verb, verb_args, opts = parse_as_workhost(mod, command)
        assert opts.accept_key is True, (command, verb, verb_args)
        # And it must not ALSO still be sitting in the verb's pass-through args,
        # which would mean it reaches the remote command as a literal argument.
        assert "--accept-key" not in verb_args, (command, verb_args)


def test_following_the_printed_advice_actually_connects(tmp_path):
    """End to end: take the command the tool printed and RUN it.

    The stub refuses the unknown key under BatchMode and proceeds without it,
    exactly as ssh does, so this is green only if the advised invocation really
    reaches an interactive connection.
    """
    _, log, env = sandbox(tmp_path, ok_addrs=(), hostkey_missing=(LAN, NEBULA))
    stuck = run(env, "path")
    assert stuck.returncode == 3, (stuck.returncode, stuck.stdout)

    commands = [c for c in printed_workhost_commands(stuck.stdout) if "--accept-key" in c]
    assert commands, stuck.stdout
    tokens = shlex.split(commands[0])[1:]  # drop the `workhost` argv[0]

    followed = run(env, *tokens)
    assert followed.returncode == 0, (tokens, followed.returncode, followed.stderr)
    remote = non_probe(log)
    assert remote and LAN in remote[0], remote
    assert "BatchMode=yes" not in remote[0], remote[0]


def test_every_workhost_command_in_the_readme_parses_and_means_what_it_says():
    """The same class one file over. `scripts/README.md` also tells the reader
    to run `workhost ssh --accept-key`, and that instruction was false for
    exactly as long as the advice block's was — a doc is not exempt from being
    a claim about the parser."""
    readme = (REPO / "scripts" / "README.md").read_text(encoding="utf-8")
    commands = printed_workhost_commands(readme)
    assert commands, "scripts/README.md no longer names any workhost command"

    mod = load_workhost()
    for command in commands:
        verb, verb_args, opts = parse_as_workhost(mod, command)
        if "--accept-key" in command:
            assert opts.accept_key is True, (command, verb, verb_args)
            assert "--accept-key" not in verb_args, (command, verb_args)


def test_the_forward_example_is_a_spec_the_validator_itself_accepts(tmp_path):
    """The no-spec error names an example. An example the validator would
    reject is the same class of false claim one step further in."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "forward")
    commands = [c for c in printed_workhost_commands(r.stderr) if " forward " in c]
    assert commands, r.stderr

    mod = load_workhost()
    for command in commands:
        verb, verb_args, _ = parse_as_workhost(mod, command)
        assert verb == "forward", command
        mod.validate_forward_spec(verb_args)  # raises ValueError if it lied


def test_the_manual_ssh_line_uses_the_options_the_tool_itself_uses(tmp_path):
    """The `or: ssh …` fallback is derived from `ssh_options`, not spelled
    beside it. Advice that omitted the alias would tell the operator to create
    the second known_hosts entry this tool exists to prevent."""
    _, _, env = sandbox(tmp_path, ok_addrs=(), hostkey_missing=(LAN, NEBULA))
    r = run(env, "path")

    mod = load_workhost()
    expected = " ".join(
        ["ssh"] + mod.ssh_options(mod.HOSTS["workbench"]) + [LAN]
    )
    assert expected in r.stdout, (expected, r.stdout)


def test_the_changed_key_advice_removes_the_alias_not_an_address(tmp_path):
    """`ssh-keygen -R` keyed on an address deletes the wrong line — or none —
    because HostKeyAlias is what the offending entry is keyed on."""
    _, _, env = sandbox(tmp_path, ok_addrs=(), hostkey_changed=(LAN, NEBULA))
    r = run(env, "path")

    mod = load_workhost()
    alias = [
        o.split("=", 1)[1]
        for o in mod.ssh_options(mod.HOSTS["workbench"])
        if o.startswith("HostKeyAlias=")
    ]
    assert alias, mod.ssh_options(mod.HOSTS["workbench"])
    assert "ssh-keygen -R %s" % alias[0] in r.stdout, r.stdout
    assert "ssh-keygen -R %s" % LAN not in r.stdout, r.stdout
    assert "ssh-keygen -R %s" % NEBULA not in r.stdout, r.stdout


# ---------------------------------------------------------------------------
# 19. --accept-key works in BOTH argument positions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ("--accept-key", "run", "echo", "bootstrapped"),
        ("run", "--accept-key", "echo", "bootstrapped"),
        ("run", "echo", "bootstrapped", "--accept-key"),
    ],
    ids=["before-verb", "after-verb", "trailing"],
)
def test_accept_key_is_honoured_in_either_position(tmp_path, argv):
    """Operators will type it AFTER the verb, because that is the form the tool
    prints and because it reads naturally."""
    _, _, env = sandbox(tmp_path, ok_addrs=(), hostkey_missing=(LAN,))
    r = run(env, *argv)
    assert r.returncode == 0, (argv, r.returncode, r.stdout, r.stderr)
    assert r.stdout.strip() == "bootstrapped", (argv, r.stdout)


def test_a_hoisted_flag_is_removed_from_the_remote_argv_and_said_so(tmp_path):
    """Hoisting changes what the remote command receives, so it is audible.
    Silently dropping it is the one behaviour that is not acceptable."""
    _, log, env = sandbox(tmp_path, ok_addrs=(), hostkey_missing=(LAN,))
    r = run(env, "run", "--accept-key", "echo", "hi")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert "taken as a workhost flag" in r.stderr, r.stderr

    # The notice names the verb it was NOT passed to; that name is a claim too.
    mod = load_workhost()
    named = re.findall(r"not passed to `([^`]*)`", r.stderr)
    assert named, r.stderr
    for verb in named:
        assert verb in mod.VERBS, (verb, mod.VERBS)

    remote = non_probe(log)
    assert remote, remote
    assert "--accept-key" not in remote[0], remote[0]


def test_nothing_is_said_when_no_flag_was_hoisted(tmp_path):
    """The notice must not fire on an ordinary run, or it is noise that trains
    people to ignore it."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "run", "echo", "hi")
    assert "taken as a workhost flag" not in r.stderr, r.stderr


def test_only_declared_flags_are_hoisted(tmp_path):
    """The positional rule still holds for everything else: `workhost run
    --json` must send `--json` to the REMOTE command, not switch workhost into
    JSON mode. Hoisting is a narrow, enumerated exception."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "run", "echo", "--json", "--dry-run", "--verbose")
    assert r.stdout.strip() == "--json --dry-run --verbose", r.stdout
    assert "{" not in r.stdout, r.stdout


def test_a_quoted_accept_key_still_reaches_the_remote_shell(tmp_path):
    """The named escape for the one case hoisting costs: `run` joins its args
    and hands them to the remote shell, so quoting keeps the flag as text."""
    _, _, env = sandbox(tmp_path, ok_addrs=(LAN,))
    r = run(env, "run", "echo one --accept-key two")
    assert r.stdout.strip() == "one --accept-key two", r.stdout
    assert "taken as a workhost flag" not in r.stderr, r.stderr
