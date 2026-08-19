"""Session-id derivation contract for the `browser` CLI.

WHY THIS FILE EXISTS (measured live, workbench over ssh, 2026-08-01):

    $ echo "in subst:  $(browser --print-session-id)"
    in subst:  ppid:2484606:c4b88b1d9de41681
    $ browser --print-session-id
               ppid:2484584:3216410da01ef5e2      # DIFFERENT

The last-resort fallback was a random token cached under `$PPID`. A `$( … )`
that forks gives `browser` a different parent pid, so the cache key missed and a
brand-new id was minted. That silently breaks per-session tab ownership in the
idiom the docs teach:

    T=$(browser open https://example.com | grep -oE '[0-9]+')   # owner = id A
    browser --tab "$T" emulate iphone-15                        # presents id B
    → browser: op 'emulate' may only run on a tab THIS session owns …

Invisible under normal Claude Code use (`CLAUDE_CODE_SESSION_ID` is set, so the
fallback never runs); it bites over ssh, cron, and any non-Claude shell.

Fix: derive from the POSIX SESSION id (`sid:<sid>:<leader-starttime>`). A
subshell cannot leave its session — only setsid(2) can — so it is stable across
a command substitution, while sshd / tmux / systemd / cron all setsid, so
genuinely distinct sessions still get distinct ids and keep their tab isolation.

BOTH directions are asserted here on purpose: a "fix" that collapsed every
invocation onto one shared id would pass the stability test and destroy the
isolation the ownership model exists for.

HARNESS VALIDATION (how I know these tests can fail):
`BROWSER_BRIDGE_PROC` lets a test point the procfs read at a nonexistent path,
which forces the OLD last-resort branch. `test_harness_control_old_algorithm_drifts`
runs the exact stability probe against that branch and asserts it DRIFTS — so the
probe is proven able to detect the bug it guards, in-process, every run. (It is
the in-suite counterpart of `git checkout 127090e -- browser`, which was also
run; see the PR body's mutation table.)

Run: nix-shell -p python312Packages.pytest --run \\
       "pytest scripts/browser-bridge/tests/test_browser_session_id.py -q"
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

BB = Path(__file__).resolve().parent.parent          # scripts/browser-bridge
CLI = BB / "browser"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="the browser CLI is bash")

# Env vars that would short-circuit the derivation before the fallback we are
# testing. Cleared for every fallback test.
HIGHER_PRECEDENCE = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "TMUX_PANE")

SID_RE = re.compile(r"^sid:([1-9][0-9]*):([0-9]+|x)$")


def _env(tmp_path, **over):
    env = dict(os.environ)
    for k in HIGHER_PRECEDENCE:
        env.pop(k, None)
    env["HOME"] = str(tmp_path)
    env["XDG_RUNTIME_DIR"] = str(tmp_path)          # keep any cache file local
    env.update(over)
    return env


def _print_id(env, **kw):
    cp = subprocess.run(["bash", str(CLI), "--print-session-id"], env=env,
                        capture_output=True, text=True, timeout=60, **kw)
    assert cp.returncode == 0, cp.stderr
    return cp.stdout.strip()


# --------------------------------------------------------------------------- #
# The subshell probe: one shell, three ways of invoking the CLI.
#
# `direct` MUST NOT be captured with $( ) — that is the whole point. It goes to
# a file. `subst` and `pipe` are the two shapes a caller actually writes; `pipe`
# is the one that is guaranteed to fork (`$(a | b)` cannot be exec-optimised).
# --------------------------------------------------------------------------- #
PROBE = r"""
set -u
CLI="$1"; OUT="$2"
bash "$CLI" --print-session-id > "$OUT/direct"
printf '%s' "$(bash "$CLI" --print-session-id)"        > "$OUT/subst"
printf '%s' "$(bash "$CLI" --print-session-id | cat)"  > "$OUT/pipe"
printf '%s' "$$" > "$OUT/shellpid"
"""


def _probe(tmp_path, env, new_session=False):
    out = tmp_path / f"probe-{'ns' if new_session else 'inh'}"
    out.mkdir(exist_ok=True)
    cp = subprocess.run(["bash", "-c", PROBE, "probe", str(CLI), str(out)],
                        env=env, capture_output=True, text=True, timeout=60,
                        start_new_session=new_session)
    assert cp.returncode == 0, cp.stderr
    return {n: (out / n).read_text().strip()
            for n in ("direct", "subst", "pipe", "shellpid")}


# --------------------------------------------------------------------------- #
# 1. THE LOAD-BEARING TEST — stable across a command substitution …
# --------------------------------------------------------------------------- #
def test_session_id_stable_across_command_substitution(tmp_path):
    r = _probe(tmp_path, _env(tmp_path))
    assert r["direct"] == r["subst"] == r["pipe"], (
        "session id drifts between a direct call and a command substitution "
        f"in the SAME shell: {r!r}")
    assert SID_RE.match(r["direct"]), r["direct"]


# --------------------------------------------------------------------------- #
# 2. … and DISTINCT across genuinely different sessions.
#    Without this, "return one constant" would pass test 1 and silently destroy
#    per-session tab isolation (concurrent drivers would fight over one tab).
# --------------------------------------------------------------------------- #
def test_session_id_distinct_across_distinct_sessions(tmp_path):
    env = _env(tmp_path)
    a = _print_id(env, start_new_session=True)
    b = _print_id(env, start_new_session=True)
    assert SID_RE.match(a) and SID_RE.match(b), (a, b)
    assert a != b, f"two separate POSIX sessions collapsed onto one id: {a}"


def test_session_id_is_the_real_posix_session_id(tmp_path):
    """Pin the VALUE, not just the shape.

    `start_new_session=True` makes the child bash a session leader, so its sid
    IS its pid — a number this test learns from the OS (`$$`), independently of
    the implementation under test.
    """
    r = _probe(tmp_path, _env(tmp_path), new_session=True)
    assert r["direct"].startswith(f"sid:{r['shellpid']}:"), r
    m = SID_RE.match(r["direct"])
    assert m and m.group(2).isdigit(), (
        f"expected a numeric leader starttime (pid-reuse guard), got {r['direct']}")


# --------------------------------------------------------------------------- #
# 3. HARNESS CONTROL — the same probe, pointed at the OLD algorithm, must FAIL.
#    BROWSER_BRIDGE_PROC=/nonexistent forces the PPID-cached last-resort branch.
# --------------------------------------------------------------------------- #
def test_harness_control_old_algorithm_drifts(tmp_path):
    env = _env(tmp_path, BROWSER_BRIDGE_PROC=str(tmp_path / "no-such-procfs"))
    r = _probe(tmp_path, env)
    assert r["direct"].startswith("ppid:"), r
    assert r["pipe"] != r["direct"], (
        "the probe did NOT detect drift on the known-bad algorithm — it is "
        f"proving nothing: {r!r}")


# --------------------------------------------------------------------------- #
# 3b. THE /proc PARSE ITSELF.
#
# An independent 9-mutant sweep found 5 survivors, ALL here — the tests above
# pin the *outcome* (stable / distinct) and every one of these mutations happens
# to preserve it on THIS host's process tree. Two are not cosmetic:
#   • `sid="$4"` → `"$3"` reads the process GROUP instead of the session.
#     Measured under a real pty: at an interactive prompt a bare command gets its
#     OWN process group while `$( … )` keeps the shell's — so pgrp-instead-of-
#     session silently REINTRODUCES the exact bug this file exists for.
#     `test_session_id_is_the_real_posix_session_id` cannot see it because
#     `start_new_session=True` makes pid == pgid == sid there (an incidental
#     value-pin). The discriminator below forces pgid ≠ sid instead.
#   • `st="${20}"` → `${19}` reads stat field 21 (`itrealvalue`, always 0), which
#     silently kills the PID-reuse guard.
# BROWSER_BRIDGE_PROC (the seam already used by the harness control) lets these
# pin the field indices against a synthetic /proc with known values.
# --------------------------------------------------------------------------- #
def _stat(pid, comm, ppid, pgrp, sess, starttime):
    """One /proc/<pid>/stat line with EXPLICIT field values.

    1=pid 2=(comm) 3=state 4=ppid 5=pgrp 6=session 7..21=filler 22=starttime.
    The filler is `2xx` per index so an off-by-one lands on a recognisable
    wrong number instead of on something that happens to look plausible.
    """
    filler = " ".join(str(200 + i) for i in range(7, 22))     # fields 7..21
    return f"{pid} ({comm}) S {ppid} {pgrp} {sess} {filler} {starttime} 0 0\n"


def _fake_proc(tmp_path, self_stat, leader=None, leader_stat=None):
    root = tmp_path / "fakeproc"
    (root / "self").mkdir(parents=True, exist_ok=True)
    (root / "self" / "stat").write_text(self_stat)
    if leader is not None:
        (root / str(leader)).mkdir(parents=True, exist_ok=True)
        (root / str(leader) / "stat").write_text(leader_stat)
    return root


def _run_id(env):
    return subprocess.run(["bash", str(CLI), "--print-session-id"], env=env,
                          capture_output=True, text=True, timeout=60)


def test_comm_containing_a_close_paren_space_is_parsed_by_the_LAST_paren(tmp_path):
    """A process whose comm contains `") "` must not truncate the field split.

    With a first-`)` cut the remainder starts mid-comm and field 4 lands on the
    pgrp (555) instead of the session (888).
    """
    root = _fake_proc(
        tmp_path,
        _stat(123, "my (weird) proc", 1, 555, 888, 111),
        leader=888, leader_stat=_stat(888, "bash", 1, 888, 888, 987654))
    cp = _run_id(_env(tmp_path, BROWSER_BRIDGE_PROC=str(root)))
    assert cp.stdout.strip() == "sid:888:987654", (cp.stdout, cp.stderr)


def test_session_field_is_used_not_the_process_group_synthetic(tmp_path):
    """Field 6 (session), not field 5 (pgrp). Pinned with the two set apart."""
    root = _fake_proc(
        tmp_path,
        _stat(123, "bash", 1, 555, 888, 111),
        leader=888, leader_stat=_stat(888, "bash", 1, 888, 888, 987654))
    cp = _run_id(_env(tmp_path, BROWSER_BRIDGE_PROC=str(root)))
    assert cp.stdout.strip() == "sid:888:987654", (cp.stdout, cp.stderr)


def test_session_field_is_used_not_the_process_group_live(tmp_path):
    """The same pin against the REAL kernel, in the shape that actually bit.

    `process_group=0` gives the child its own process group while leaving it in
    our session — exactly what an interactive shell does to a foreground command
    (and what a forking `$( … )` does NOT). So pgid ≠ sid here, and a `$3` read
    would return the child's own pid.
    """
    sess = os.getsid(0)
    p = subprocess.Popen(["bash", str(CLI), "--print-session-id"],
                         env=_env(tmp_path), stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, process_group=0)
    out, err = p.communicate(timeout=60)
    assert p.returncode == 0, err
    # The discriminator must actually discriminate, or this test is vacuous.
    assert p.pid != sess, "child pid == session id — no pgrp/sid discrimination"
    assert out.strip().startswith(f"sid:{sess}:"), (out, p.pid, sess)
    assert not out.strip().startswith(f"sid:{p.pid}:"), (
        f"read the process GROUP ({p.pid}), not the session ({sess}): {out!r}")


def test_leader_starttime_is_stat_field_22_synthetic(tmp_path):
    """Field 22 (starttime), not 21 (`itrealvalue`). The leader's field 21 is
    the filler 221, so an off-by-one is unmistakable."""
    root = _fake_proc(
        tmp_path,
        _stat(123, "bash", 1, 888, 888, 111),
        leader=888, leader_stat=_stat(888, "bash", 1, 888, 888, 987654))
    cp = _run_id(_env(tmp_path, BROWSER_BRIDGE_PROC=str(root)))
    assert cp.stdout.strip() == "sid:888:987654", (cp.stdout, cp.stderr)
    assert "221" not in cp.stdout, f"read stat field 21, not 22: {cp.stdout!r}"


@pytest.mark.skipif(shutil.which("awk") is None, reason="cross-check uses awk")
def test_leader_starttime_matches_awk_field_22_live(tmp_path):
    """Cross-check the real value with a DIFFERENT tool (awk whitespace split),
    so the expectation is not derived from the implementation's own parse."""
    sess = os.getsid(0)
    raw = Path(f"/proc/{sess}/stat").read_text()
    if " " in raw[raw.index("(") + 1:raw.rindex(")")]:
        pytest.skip("session leader's comm contains a space; awk $22 would shift")
    expect = subprocess.run(["awk", "{print $22}", f"/proc/{sess}/stat"],
                            capture_output=True, text=True).stdout.strip()
    assert expect.isdigit(), expect
    assert _print_id(_env(tmp_path)) == f"sid:{sess}:{expect}"


@pytest.mark.parametrize("label,self_stat", [
    # Too few fields after the comm cut → the `[ $# -ge 4 ]` guard.
    ("no-parens", "garbage\n"),
    ("truncated", "123 (bash) S 1\n"),
    # Session 0 / non-numeric → the `case "$sid"` guard.
    ("session-zero", _stat(123, "bash", 1, 0, 0, 111)),
    ("session-nan", "123 (bash) S 1 555 notanumber 1 2 3\n"),
])
def test_malformed_stat_degrades_to_the_ppid_fallback_silently(
        tmp_path, label, self_stat):
    """Every malformed shape must fall through to `ppid:` — never to an empty
    id, a constant, or `sid:0:`/`sid:notanumber:`.

    stderr MUST stay empty: with the `[ $# -ge 4 ]` guard removed, `set -u`
    makes `$4` an unbound-variable error, which still lands on the ppid
    fallback but spews a shell diagnostic on every single invocation. Asserting
    silence is what distinguishes the two.
    """
    root = _fake_proc(tmp_path, self_stat)
    cp = _run_id(_env(tmp_path, BROWSER_BRIDGE_PROC=str(root)))
    assert cp.returncode == 0, cp.stderr
    assert cp.stdout.strip().startswith("ppid:"), (label, cp.stdout)
    assert cp.stderr == "", (label, cp.stderr)


# --------------------------------------------------------------------------- #
# 4. Precedence chain, explicitly.
# --------------------------------------------------------------------------- #
def test_claude_code_session_id_wins_over_everything(tmp_path):
    env = _env(tmp_path, CLAUDE_CODE_SESSION_ID="uuid-A",
               CLAUDE_SESSION_ID="uuid-B", TMUX_PANE="%9")
    assert _print_id(env) == "claude:uuid-A"


def test_claude_session_id_is_the_defensive_alternate(tmp_path):
    env = _env(tmp_path, CLAUDE_SESSION_ID="uuid-B", TMUX_PANE="%9")
    assert _print_id(env) == "claude:uuid-B"


def test_tmux_pane_beats_the_process_tree(tmp_path):
    assert _print_id(_env(tmp_path, TMUX_PANE="%9")) == "tmux:%9"


def test_posix_session_id_is_used_when_no_env_source_exists(tmp_path):
    assert SID_RE.match(_print_id(_env(tmp_path)))


def test_ppid_cache_is_the_last_resort_only(tmp_path):
    """With procfs unreadable the CLI still produces an id — it must not die."""
    env = _env(tmp_path, BROWSER_BRIDGE_PROC=str(tmp_path / "no-such-procfs"))
    assert _print_id(env).startswith("ppid:")


def test_claude_var_still_wins_with_procfs_available(tmp_path):
    """The laptop relies on this: CLAUDE_CODE_SESSION_ID must never be shadowed
    by the new sid branch."""
    r = _probe(tmp_path, _env(tmp_path, CLAUDE_CODE_SESSION_ID="uuid-A"))
    assert r["direct"] == r["subst"] == r["pipe"] == "claude:uuid-A", r


# --------------------------------------------------------------------------- #
# 5. The not_owned_tab error message.
#
# A stub bridge that always answers 409 not_owned_tab, so the CLI's guidance is
# asserted on the wire shape the real server produces.
# --------------------------------------------------------------------------- #
class _Refuser(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        raw = json.dumps({"ok": False, "error": "not_owned_tab"}).encode()
        self.send_response(409)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


@pytest.fixture
def refuser(tmp_path):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Refuser)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tok = tmp_path / "token"
    tok.write_text("test-token-abc123\n")
    env = _env(tmp_path, BROWSER_BRIDGE_HOST="127.0.0.1",
               BROWSER_BRIDGE_PORT=str(srv.server_address[1]),
               BROWSER_BRIDGE_TOKEN_FILE=str(tok),
               CLAUDE_CODE_SESSION_ID="uuid-under-test")

    class _R:
        @staticmethod
        def run(*args):
            return subprocess.run(["bash", str(CLI), *args], env=env,
                                  capture_output=True, text=True, timeout=60)
    try:
        yield _R
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.skipif(shutil.which("curl") is None, reason="the CLI uses curl")
def test_not_owned_tab_with_explicit_tab_names_the_tab_and_this_session_id(refuser):
    cp = refuser.run("--tab", "4242", "emulate", "iphone-15")
    assert cp.returncode != 0
    err = cp.stderr
    assert "tab 4242 is not owned by THIS session" in err, err
    assert "claude:uuid-under-test" in err, err
    assert "--print-session-id" in err, err
    # The old message's advice, which sent a real operator hunting the flag for
    # 20 minutes while the cause was a session-id mismatch.
    assert "drop --tab" not in err, err


@pytest.mark.skipif(shutil.which("curl") is None, reason="the CLI uses curl")
def test_not_owned_tab_without_a_tab_flag_says_open_first(refuser):
    cp = refuser.run("emulate", "iphone-15")
    assert cp.returncode != 0
    err = cp.stderr
    assert "this session owns no tab" in err, err
    assert "browser open" in err, err
    assert "claude:uuid-under-test" in err, err
    # No --tab was passed, so nothing may accuse a tab id.
    assert "is not owned by THIS session" not in err, err


# --------------------------------------------------------------------------- #
# 6. The tier TAG is now load-bearing for TELEMETRY, not just for collision
#    avoidance.
#
# server.py reads the tag before the FIRST `:` to decide whether the id is a
# joinable key for the activity.events `session` column: `claude:` is Claude
# Code's own session uuid (the same value `source='claude'` rows store, so it
# JOINS), while `tmux:`/`sid:`/`ppid:` are not — a pane id like %3 is stable
# across many UNRELATED sessions and recording it would silently merge them.
#
# That makes the tag a SEAM: the CLI writes it, a different file reads it, and
# nothing in either file fails if they drift. A rename here would not break a
# single existing test and would silently empty the column again.
# --------------------------------------------------------------------------- #
sys.path.insert(0, str(BB))
import server as S  # noqa: E402


# Every tag `derive_session_id` (and the recreate-close re-tag) can put on the
# wire. Pinned two-way: the parse below must find exactly this set, so a NEW tier
# fails here until someone decides whether it is joinable.
CLI_TIER_TAGS = {"claude", "tmux", "sid", "ppid", "synthetic"}


def _emitted_tags():
    """Parse the tags the CLI can emit out of its own source.

    Each is a literal at the START of a printf format or an assignment, followed
    by `:`. The parser is asserted non-empty by its caller — a regex that silently
    matched nothing would make every claim below pass vacuously.
    """
    src = CLI.read_text()
    tags = set(re.findall(r"printf '([a-z]+):%s", src))
    tags |= set(re.findall(r'SESSION_ID="([a-z]+):', src))
    tags |= set(re.findall(r'tok="([a-z]+):', src))
    return tags


def test_the_cli_tier_tag_vocabulary_is_pinned():
    """SEAM LEDGER. Fails when the set GROWS (a new tier nobody classified as
    joinable-or-not) or SHRINKS (a tag renamed or dropped) — neither of which any
    behavioural test in this file would notice, because they all assert the tag
    they were written with."""
    found = _emitted_tags()
    assert found, "the tag parser matched nothing — it is testing nothing"
    assert found == CLI_TIER_TAGS, f"CLI tier tags drifted: {found}"


def test_the_servers_joinable_tag_is_one_the_cli_actually_emits():
    """The other half of the seam: server.py's allowlist must name a tag this CLI
    can produce. If either side renames alone, the `session` column silently goes
    back to empty — the exact bug this whole change fixes, restored with a green
    suite on both sides."""
    assert S.SESSION_SRC_JOINABLE in CLI_TIER_TAGS
    assert S.SESSION_SRC_JOINABLE in _emitted_tags()
    # And the fail-closed token is NOT a tag anything can legitimately emit.
    assert S.SESSION_SRC_UNKNOWN not in CLI_TIER_TAGS


def test_the_throwaway_recreate_close_id_is_re_tagged_not_just_suffixed():
    """🔴 REGRESSION. `emulate --recreate` sends its close under a THROWAWAY id so
    the close cannot evict the ownership mapping it just created. That id was
    built by APPENDING to the real one — which left the live session's `claude:`
    tag on a value that is not that session's key, so the bridge would have
    stored `<uuid>+recreate-close` in the `session` column as a near-duplicate of
    the real key, inflating distinct-session counts with fabricated rows.

    Structural because the behaviour needs a full recreate round-trip against a
    real extension; what is pinned is the property that actually matters — the
    throwaway is re-TAGGED with a tier the server does not join on."""
    src = CLI.read_text()
    m = re.search(r'SESSION_ID="([^"]*\+recreate-close)"', src)
    assert m, "the recreate-close throwaway id assignment is gone or renamed"
    throwaway = m.group(1)
    tag = throwaway.split(":", 1)[0]
    assert tag == "synthetic", f"throwaway id wears tier tag {tag!r}"
    assert tag != S.SESSION_SRC_JOINABLE
    assert throwaway.startswith("synthetic:${SESSION_ID}"), throwaway


# --------------------------------------------------------------------------- #
# 7. The WIRE. A stub bridge that RECORDS the request headers, so what is
#    asserted is what the CLI actually sends — not what a reader of the source
#    believes it sends.
#
# INVARIANT GUARDS: these pass at base too. They exist because server.py now
# DEPENDS on the tagged form reaching it, and nothing else asserts that the tag
# survives the transport rather than only the deriver.
# --------------------------------------------------------------------------- #
_CAPTURED = []


class _Capture(BaseHTTPRequestHandler):
    def _record(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        _CAPTURED.append({k: v for k, v in self.headers.items()})
        raw = json.dumps({"ok": False, "error": "not_owned_tab"}).encode()
        self.send_response(409)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_POST = _record
    do_GET = _record

    def log_message(self, *a):
        pass


@pytest.fixture
def capture(tmp_path):
    _CAPTURED.clear()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Capture)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tok = tmp_path / "token"
    tok.write_text("test-token-abc123\n")
    base = dict(BROWSER_BRIDGE_HOST="127.0.0.1",
                BROWSER_BRIDGE_PORT=str(srv.server_address[1]),
                BROWSER_BRIDGE_TOKEN_FILE=str(tok))

    class _C:
        headers = _CAPTURED

        @staticmethod
        def run(*args, **over):
            env = _env(tmp_path, **{**base, **over})
            return subprocess.run(["bash", str(CLI), *args], env=env,
                                  capture_output=True, text=True, timeout=60)
    try:
        yield _C
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.skipif(shutil.which("curl") is None, reason="the CLI uses curl")
@pytest.mark.parametrize("envvars,want_id", [
    ({"CLAUDE_CODE_SESSION_ID": "uuid-onwire"}, "claude:uuid-onwire"),
    ({"TMUX_PANE": "%41"}, "tmux:%41"),
])
def test_the_tagged_id_survives_the_transport(capture, envvars, want_id):
    """PIN THE WIRE FORMAT: the tag is on the header value the server receives.
    Two tiers with pairwise-distinct ids, so a stub that hardcoded either literal
    could not satisfy both rows."""
    capture.run("emulate", "iphone-15", **envvars)
    assert capture.headers, "the stub recorded no request at all"
    h = capture.headers[0]
    assert h.get("X-Session-Id") == want_id, h


@pytest.mark.skipif(shutil.which("curl") is None, reason="the CLI uses curl")
def test_an_ordinary_cli_call_declares_no_nested_origin(capture):
    """X-Session-Origin marks a NESTED browser-agent run, whose forwarded id must
    not be credited as usage. The direct CLI must never send it — if it did,
    every ordinary command would stop filling the session column."""
    capture.run("health", CLAUDE_CODE_SESSION_ID="uuid-direct")
    assert capture.headers, "the stub recorded no request at all"
    h = capture.headers[0]
    assert h.get("X-Session-Id") == "claude:uuid-direct", h
    assert "X-Session-Origin" not in h, h
