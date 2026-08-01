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
