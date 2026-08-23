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
#
# OPENCODE is in this list for a different reason than the rest, and it is the
# harness-blindness one: opencode's CLI exports OPENCODE=1 for every subcommand,
# so running THIS SUITE from inside an opencode session would silently change
# what every `claude:`-expecting test below derives. Clearing it makes the
# dimension explicit — the leak-guard tests set it themselves — instead of
# letting the answer depend on which agent happened to launch pytest.
#
# 🔴 OPENCODE_SESSION_ID is here for the SAME harness-blindness reason, and it is
# the sharper case: once scripts/opencode/plugin/session-env.js is deployed, an
# opencode bash tool exports it on EVERY call, so this suite run from inside
# opencode would derive `opencode:<id>` for every test that expects `claude:` —
# and, being tier 0, it would do so even where the test sets a claude var
# explicitly. Left unlisted, the whole file's meaning would depend on which agent
# launched pytest.
HIGHER_PRECEDENCE = ("OPENCODE_SESSION_ID", "CLAUDE_CODE_SESSION_ID",
                     "CLAUDE_SESSION_ID", "TMUX_PANE", "OPENCODE")

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


# 🔴 THE LIVE PROBE RUNS INSIDE A SESSION LEADER WE OWN.
#
# The two `_live` tests below used to read `/proc/{os.getsid(0)}/stat` — the
# AMBIENT session leader, a process this suite does not own and cannot keep
# alive. Under the gate that leader is a transient shell (nix-shell -> bash
# script -> pytest, often backgrounded), and it exits when its work is done.
# Measured 2026-08-21: a full gate run died with
# `FileNotFoundError: '/proc/1760394/stat'`, and the same suite passed 753/753
# on the very next run — a real timing dependency, not load. `claude/RULES.md`:
# a flaky test is FIXABLE; remove the timing dependency rather than re-running.
#
# `start_new_session=True` makes this bash its OWN session leader, so `$$` IS
# the session id and the process is alive for every read taken inside it. Both
# reads — awk's and the implementation's — now happen within one live process,
# so there is no window for the leader to vanish.
#
# `set -m` (job control) then puts the backgrounded CLI in its own process
# GROUP while leaving it in this session, which is what an interactive shell
# does to a foreground command. That is what keeps pgid != sid, so a `$3`
# (process-group) read stays distinguishable from a `$4` (session) read.
LIVE_PROBE = r"""
set -u
CLI="$1"; OUT="$2"
printf '%s' "$$" > "$OUT/leader"
awk '{print $22}' "/proc/$$/stat" > "$OUT/awk22"
sed -n 's/^[0-9]* (\(.*\)) .*/\1/p' "/proc/$$/stat" > "$OUT/comm"
set -m
bash "$CLI" --print-session-id > "$OUT/direct" 2> "$OUT/err" &
child=$!
printf '%s' "$child" > "$OUT/childpid"
wait "$child"
"""


def _live_probe(tmp_path, env=None):
    """Run the CLI inside a session leader THIS test owns. No ambient /proc."""
    out = tmp_path / "liveprobe"
    out.mkdir(exist_ok=True)
    cp = subprocess.run(["bash", "-c", LIVE_PROBE, "probe", str(CLI), str(out)],
                        env=env or _env(tmp_path), capture_output=True,
                        text=True, timeout=60, start_new_session=True)
    assert cp.returncode == 0, cp.stderr
    r = {n: (out / n).read_text().strip()
         for n in ("leader", "awk22", "comm", "direct", "childpid", "err")}
    # Harness guards: a probe that silently produced nothing would make every
    # assertion below vacuous.
    assert r["leader"].isdigit() and r["awk22"].isdigit(), r
    assert r["direct"], f"probe produced no session id: {r!r}"
    return r


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

    `set -m` gives the CLI its own process group while leaving it in the
    probe's session — exactly what an interactive shell does to a foreground
    command (and what a forking `$( … )` does NOT). So pgid != sid here, and a
    `$3` read would return the child's own pid.

    Runs inside a session leader this test owns; see LIVE_PROBE for why.
    """
    r = _live_probe(tmp_path)
    # The discriminator must actually discriminate, or this test is vacuous.
    assert r["childpid"] != r["leader"], (
        f"child pid == session id ({r['leader']}) — no pgrp/sid discrimination; "
        "job control did not give the child its own process group")
    assert r["direct"].startswith(f"sid:{r['leader']}:"), r
    assert not r["direct"].startswith(f"sid:{r['childpid']}:"), (
        f"read the process GROUP ({r['childpid']}), not the session "
        f"({r['leader']}): {r['direct']!r}")


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
    so the expectation is not derived from the implementation's own parse.

    Both reads happen INSIDE one live session leader this test owns, so the
    process cannot exit between them — see LIVE_PROBE.
    """
    r = _live_probe(tmp_path)
    # The comm-space caveat the ambient version had to guard against cannot
    # arise here: the probe is always `bash`, so awk's $22 never shifts. Pinned
    # rather than assumed, because a shell change would silently break the
    # field index and read as a starttime mismatch.
    assert " " not in r["comm"], (
        f"probe comm {r['comm']!r} contains a space — awk $22 would shift; "
        "the probe must stay a single-word command")
    assert r["direct"] == f"sid:{r['leader']}:{r['awk22']}", r


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
CLI_TIER_TAGS = {"claude", "opencode", "tmux", "sid", "ppid", "synthetic"}

# The tiers server.py will write into a JOIN column. A LEDGER, not a copy of the
# server's tuple: it is held against the server's set below, so growing one side
# alone fails. `opencode` is joinable because the bare half is the same
# `sessionID` that `source='opencode'` rows in activity.events carry — see
# scripts/collector/opencode/activity-plugin.js, which emits
# `session: input.sessionID`.
CLI_JOINABLE_TAGS = {"claude", "opencode"}


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


def test_the_servers_joinable_tags_are_ones_the_cli_actually_emits():
    """The other half of the seam: EVERY tag in server.py's joinable set must be
    one this CLI can produce. If either side renames alone, the `session` column
    silently goes back to empty — the exact bug this whole change fixes, restored
    with a green suite on both sides.

    Against the PARSE, not against a retyped literal — the same reason the
    vocabulary pin below is. A joinable tier the CLI cannot emit is dead
    allowlist entry; a tier the CLI emits and the server will not join is a
    silently empty column.
    """
    emitted = _emitted_tags()
    assert emitted, "the tag parser matched nothing — it is testing nothing"
    assert set(S.SESSION_JOINABLE_TIERS) <= CLI_TIER_TAGS
    assert set(S.SESSION_JOINABLE_TIERS) <= emitted, (
        f"server.py joins on {sorted(set(S.SESSION_JOINABLE_TIERS) - emitted)} "
        f"which the CLI never emits")
    # And the fail-closed token is NOT a tag anything can legitimately emit.
    assert S.SESSION_SRC_UNKNOWN not in CLI_TIER_TAGS


def test_the_joinable_set_is_pinned_and_is_a_strict_subset_of_the_vocabulary():
    """🔴 THE JOINABILITY DECISION IS ITS OWN LEDGER. The vocabulary pin above
    fails when a tier is ADDED, which forces someone to notice a new tier. It
    does NOT force a decision about whether that tier may fill a JOIN column —
    `SESSION_TIER_TAGS` and the joinable set are different questions, and adding
    a tier to both at once is a one-line change nothing else would catch.

    STRICT subset, deliberately: `tmux:`/`sid:`/`ppid:`/`synthetic:` exist
    precisely because they must never join. A joinable set that had grown to
    equal the vocabulary would mean that distinction had been erased.
    """
    joinable = set(S.SESSION_JOINABLE_TIERS)
    assert joinable == CLI_JOINABLE_TAGS, (
        f"the joinable tier set changed to {sorted(joinable)}. Every member is a "
        f"claim that the BARE id joins some other activity.events source's "
        f"`session` values. Update CLI_JOINABLE_TAGS only with that evidence.")
    assert joinable < set(S.SESSION_TIER_TAGS), (
        "the joinable set must stay a STRICT subset of the tier vocabulary — the "
        "non-joinable tiers are the whole point of the gate")
    # No duplicates: a tuple that says "claude" twice reads as two decisions.
    assert len(S.SESSION_JOINABLE_TIERS) == len(joinable)


def test_the_server_validation_set_equals_the_tags_parsed_from_the_cli():
    """🔴 THE ACTUAL TWO-WAY PIN. server.py's SESSION_TIER_TAGS is compared against
    the tags PARSED OUT OF THE CLI, not against a literal retyped beside it.

    THIS TEST EXISTS BECAUSE THE PIN IT REPLACES WAS FICTIONAL. server.py said
    SESSION_TIER_TAGS was "pinned two-way against the CLI by
    test_browser_session_id.py" and the symbol appeared nowhere in this file: the
    server set was checked against a literal in test_server.py, the CLI against a
    separate literal here, and NOTHING compared the two. A delta audit measured
    the consequence — grow the CLI by an `opencode:` tier and update
    CLI_TIER_TAGS, leave server.py alone: 400 passed, 0 failed, SURVIVED.

    That is not hypothetical. `browser`'s own FOLLOW-UP SLOT says an `opencode:`
    tier is the next planned change, and the failure is silent and fail-closed:
    the server normalises the unknown tag to `unknown`, drops the id, and the
    column this PR exists to fill just re-empties.

    Comparing against the PARSE is what makes it real — a literal can be updated
    in lockstep with the thing it is supposed to be checking, which is precisely
    how the fictional version passed.
    """
    parsed = _emitted_tags()
    assert parsed, "the tag parser matched nothing — it is testing nothing"
    assert set(S.SESSION_TIER_TAGS) == parsed, (
        f"server.py SESSION_TIER_TAGS={sorted(S.SESSION_TIER_TAGS)} does not match "
        f"the tags the CLI can actually emit {sorted(parsed)}. A tier present in "
        f"one and not the other is silently normalised to "
        f"{S.SESSION_SRC_UNKNOWN!r} and loses its id.")


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
    assert tag not in S.SESSION_JOINABLE_TIERS
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


# --------------------------------------------------------------------------- #
# 8. THE OPENCODE LEAK, and why it is an ORIGIN HEADER rather than a new tier.
#
# `CLAUDE_CODE_SESSION_ID` survives into opencode's tool shells. MEASURED TWO
# WAYS: (a) a live env dump from inside an opencode bash tool carried the outer
# Claude session's value; (b) opencode sets `process.env.OPENCODE="1"` in a yargs
# TOP-LEVEL `.middleware()` -- i.e. for every subcommand -- and hands its tool
# shells `{...process.env}`. Confirmed in the PINNED build (PINNED_VERSION in
# scripts/tests/test_opencode_engine.py) and in the newer build on this host's
# profile; identical in both, so it is not pin-specific.
#
# So inside opencode that variable names an ANCESTOR, not the caller, and a plain
# `opencode run …` shelling out to `browser` would have the bridge credit the
# OUTER session with browser usage it never did.
#
# 🔴 THE ID IS NOT TOUCHED. `browser agent` already answers the identical
# question -- "this command was issued by something nested under the id on the
# wire" -- with a separate `X-Session-Origin` header, leaving `X-Session-Id`
# alone. This case gets the SAME mechanism: one question, one mechanism. The
# payoff is that routing, tab ownership and `not_owned_tab` semantics are
# byte-identical to before any of this existed, which the first test below makes
# machine-checked rather than asserted in a comment.
# --------------------------------------------------------------------------- #
# (env overrides, the id that env must still produce). Pairwise distinct, and
# distinct from every literal the assertions name, so a mutant hardcoding one
# cannot satisfy the row next to it.
ROUTING_CASES = [
    ({"CLAUDE_CODE_SESSION_ID": "uuid-primary"}, "claude:uuid-primary"),
    ({"CLAUDE_SESSION_ID": "uuid-alternate"}, "claude:uuid-alternate"),
    ({"TMUX_PANE": "%41"}, "tmux:%41"),
]


@pytest.mark.parametrize("over,want_id", ROUTING_CASES)
def test_routing_equivalence_opencode_does_not_change_the_id(tmp_path, over, want_id):
    """🔴 ROUTING MUST NOT CHANGE -- machine-checked, not asserted in prose.

    For each environment the id is derived TWICE, differing only in whether
    OPENCODE is set, and both must equal the SAME PINNED LITERAL. That literal is
    what this environment produced before any of this PR existed, so the pair is
    exactly the claim "the wire id is byte-identical to before".

    Two assertions, and both are load-bearing:
      * the two arms equal EACH OTHER -- OPENCODE changes nothing;
      * they equal a literal taken from the contract rather than from the
        implementation, so a change that moved BOTH arms together still fails.
    An earlier draft of this fix re-tagged the id (`oc-inherited:<uuid>`); it
    would die on both halves here, which is why this test exists.

    BASELINES DIFFER PER ROW, so read them per row: the two `claude:` rows are
    RED at 84bf324 (that draft perturbed exactly those); the `tmux:` row is an
    INVARIANT GUARD there, because the draft never fired on a non-claude id. All
    three are red at da33356 only in the sense that OPENCODE was ignored
    entirely — the ids matched, so they pass there too. Mutant N6 is what proves
    the whole table reachable.
    """
    inside = _print_id(_env(tmp_path, OPENCODE="1", **over))
    outside = _print_id(_env(tmp_path, **over))
    assert outside == want_id, "the pre-PR id changed"
    assert inside == outside, "OPENCODE must not perturb the routing id"


def test_routing_equivalence_holds_for_the_derived_posix_tier(tmp_path):
    """The `sid:` tier has no literal to pin (it reads live procfs), so it gets
    the equality half only -- still enough to catch an id perturbed by OPENCODE.
    Kept separate rather than bent into the table above so the table's literals
    stay literal.

    INVARIANT GUARD — green at 84bf324, whose re-tagging only ever fired on a
    claude-tagged id, and at da33356. Proved reachable by mutant N6."""
    inside = _print_id(_env(tmp_path, OPENCODE="1"))
    outside = _print_id(_env(tmp_path))
    assert SID_RE.match(outside), outside
    assert inside == outside


@pytest.mark.parametrize("var", ["CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"])
@pytest.mark.skipif(shutil.which("curl") is None, reason="the CLI uses curl")
def test_an_inherited_claude_id_declares_the_opencode_origin(capture, var):
    """THE WIRE. Both Claude spellings feed the joinable tier, so a guard placed
    on only the primary one would leave the alternate wide open.

    Asserted on the request the stub actually received: the id rides through
    unchanged AND the origin header names this mechanism.
    """
    capture.run("emulate", "iphone-15", OPENCODE="1", **{var: "uuid-inherited"})
    assert capture.headers, "the stub recorded no request at all"
    h = capture.headers[0]
    assert h.get("X-Session-Id") == "claude:uuid-inherited", h
    assert h.get("X-Session-Origin") == "opencode-inherited", h


@pytest.mark.skipif(shutil.which("curl") is None, reason="the CLI uses curl")
def test_the_discriminating_pair_on_the_wire(capture):
    """🔴 THE PAIR, at the transport. Same id both times; the header is the
    ONLY difference, and it must be absent without OPENCODE.

    Both directions in one test on purpose: a guard that declared the origin
    unconditionally would suppress the session key for every ordinary Claude
    session -- silently re-emptying the column this PR exists to fill -- and
    would pass the leak test above on its own.
    """
    capture.run("emulate", "iphone-15", OPENCODE="1",
                CLAUDE_CODE_SESSION_ID="uuid-pair")
    inside = capture.headers[0]
    capture.headers.clear()
    capture.run("emulate", "iphone-15", CLAUDE_CODE_SESSION_ID="uuid-pair")
    outside = capture.headers[0]

    assert inside.get("X-Session-Id") == outside.get("X-Session-Id") == "claude:uuid-pair"
    assert inside.get("X-Session-Origin") == "opencode-inherited", inside
    assert "X-Session-Origin" not in outside, outside


@pytest.mark.skipif(shutil.which("curl") is None, reason="the CLI uses curl")
def test_an_opencode_session_with_no_claude_ancestor_declares_nothing(capture):
    """opencode run INTERACTIVELY, with no Claude ancestor: nothing was inherited,
    so there is nothing to disclaim. The id falls through to the `tmux:` tier and
    NO origin header is sent -- the row behaves exactly as it does today.

    This is the case a guard keyed on `OPENCODE` alone (rather than on having
    actually inherited a claude-tagged id) would get wrong.

    INVARIANT GUARD — green at 84bf324 and at da33356, where no origin header
    exists at all, so it is not regression coverage. Proved reachable by mutant
    N3 (the `claude:*` case widened to `*`), which this test alone kills.
    """
    capture.run("emulate", "iphone-15", OPENCODE="1", TMUX_PANE="%41")
    assert capture.headers, "the stub recorded no request at all"
    h = capture.headers[0]
    assert h.get("X-Session-Id") == "tmux:%41", h
    assert "X-Session-Origin" not in h, h


@pytest.mark.skipif(shutil.which("curl") is None, reason="the CLI uses curl")
def test_an_empty_opencode_is_not_opencode(capture):
    """`OPENCODE=` (exported empty) is not an opencode session, so no origin is
    declared.

    INVARIANT GUARD — green at 84bf324 and at da33356. It pins the guard's
    BOUNDARY: `-n` vs `-z` is a one-character difference that would disclaim
    every ordinary session and re-empty the column. Proved reachable by mutants
    N1 and N2."""
    capture.run("emulate", "iphone-15", OPENCODE="",
                CLAUDE_CODE_SESSION_ID="uuid-A")
    h = capture.headers[0]
    assert h.get("X-Session-Id") == "claude:uuid-A", h
    assert "X-Session-Origin" not in h, h


def test_the_cli_declares_the_token_the_server_tests_pin(tmp_path):
    """SEAM. The token is a string the CLI writes and the server records; nothing
    in either file fails if they drift. Read the literal out of the CLI source and
    hold it against the ledger the server suite pins, so a rename on one side
    cannot pass alone.

    The parser is asserted non-empty first -- a regex that matched nothing would
    make the comparison pass vacuously."""
    src = CLI.read_text()
    declared = set(re.findall(r'SESSION_ORIGIN="([a-z-]+)"', src))
    assert declared, "the origin-token parser matched nothing -- it tests nothing"
    assert declared == {"opencode-inherited"}, declared
    # The other producer of an origin token is the opencode tool, not this CLI;
    # the two must stay distinct or the populations merge in the column.
    assert "browser-agent" not in declared


# --------------------------------------------------------------------------- #
# 9. THE OPENCODE SESSION GETS AN ID OF ITS OWN.
#
# `scripts/opencode/plugin/session-env.js` registers a `shell.env` hook that
# exports OPENCODE_SESSION_ID into every bash-tool invocation. opencode builds
# the tool environment as `{...process.env, ...pluginEnv}` — PLUGIN WINS — so
# when the variable is present it was written for THIS tool call by the session
# issuing it. That is why it is tier 0, ABOVE the claude tiers: the claude vars
# can be a stale value inherited from an ancestor process, and section 8 is the
# whole story of what that cost.
#
# The bare half JOINS: activity-plugin.js emits `session: input.sessionID` on
# `source='opencode'` rows from `tool.execute.after`, and `shell.env` receives
# the same sessionID for the same call.
#
# BASELINE for everything below: RED at origin/main (4740e40) — the CLI has no
# `opencode:` tier there, so every id assertion gets `claude:…`/`tmux:…` instead,
# and every "no origin header" assertion gets `opencode-inherited`.
# --------------------------------------------------------------------------- #
OC_ID = "ses_7f3a91c0d2"          # distinct from every uuid literal in this file

# Both claude spellings, so a precedence check placed on only the primary one
# would leave the alternate reading the ancestor. Pairwise-distinct values, and
# distinct from OC_ID, so a mutant returning whichever id it had cannot satisfy
# the pair.
INHERITED_CLAUDE_VARS = [
    ("CLAUDE_CODE_SESSION_ID", "uuid-outer-primary"),
    ("CLAUDE_SESSION_ID", "uuid-outer-alternate"),
]


@pytest.mark.parametrize("var,inherited", INHERITED_CLAUDE_VARS)
def test_the_opencode_id_outranks_an_inherited_claude_id(tmp_path, var, inherited):
    """🔴 THE PRECEDENCE, which IS the fix. Inside an opencode bash tool BOTH are
    set: the opencode id was written for this call, the claude id rode down from
    an ancestor. Reading the claude var first credits the ancestor — the exact
    fabrication section 8's origin header exists to prevent, and the reason that
    header can now stand down for this population.

    The inherited uuid must appear NOWHERE in the result, not merely 'not be the
    whole answer': a tier that concatenated both would satisfy a prefix check.
    """
    got = _print_id(_env(tmp_path, OPENCODE="1", OPENCODE_SESSION_ID=OC_ID,
                         **{var: inherited}))
    assert got == f"opencode:{OC_ID}", got
    assert inherited not in got, got


def test_an_interactive_opencode_session_uses_its_own_id(tmp_path):
    """No claude ancestor at all — the tier still fires, and does not fall through
    to `tmux:`/`sid:`. This is the population that was previously indistinguishable
    from a plain shell."""
    got = _print_id(_env(tmp_path, OPENCODE="1", OPENCODE_SESSION_ID=OC_ID,
                         TMUX_PANE="%41"))
    assert got == f"opencode:{OC_ID}", got


def test_the_opencode_tier_does_not_require_the_opencode_marker(tmp_path):
    """The id is derived from the id VARIABLE, never from `OPENCODE`. Two vars for
    one fact would be a second predicate to keep in step; the plugin that sets the
    id is the only evidence the tier needs.

    Also a boundary: `OPENCODE` is what the FALLBACK guard keys on, and the two
    must not be confused for each other."""
    got = _print_id(_env(tmp_path, OPENCODE_SESSION_ID=OC_ID,
                         CLAUDE_CODE_SESSION_ID="uuid-no-marker"))
    assert got == f"opencode:{OC_ID}", got


@pytest.mark.parametrize("empty", ["", " "])
def test_an_unset_or_empty_opencode_id_falls_back_and_still_disclaims(tmp_path, empty):
    """🔴 THE FAIL-CLOSED HALF, and the reason the `opencode-inherited` token was
    NOT deleted with this change.

    `shell.env` fires on opencode's PTY path with `{cwd}` only — no sessionID —
    and the hook writes the variable EMPTY there rather than letting a parent's
    stale id survive in the overlay. The same empty-or-absent state covers deploy
    skew (this CLI is a live mkOutOfStoreSymlink; the plugin is a `home.file`
    copy that needs a switch), an opencode process older than the plugin, and a
    plugin-load failure.

    In every one of those the derived id is the INHERITED claude uuid again, so
    the suppression must come back. A ' ' row is included because the tier's test
    is `-n`, which a single space passes — proving the fallback is chosen by
    emptiness, not by a strip() nobody wrote.

    BASELINES DIFFER PER ROW, so read them per row: the `''` row is an INVARIANT
    GUARD (green at origin/main, where no tier exists so the id is `claude:`
    anyway) pinning the fallback DIRECTION; the `' '` row is RED there.
    """
    env = _env(tmp_path, OPENCODE="1", OPENCODE_SESSION_ID=empty,
               CLAUDE_CODE_SESSION_ID="uuid-fallback")
    got = _print_id(env)
    if empty == "":
        assert got == "claude:uuid-fallback", got
    else:
        # A non-empty value is a real id as far as the CLI is concerned; what is
        # pinned is that it does NOT silently become a claude attribution.
        assert got.startswith("opencode:"), got


def test_two_invocations_in_one_opencode_session_derive_the_same_id(tmp_path):
    """ROUTING. Per-session tab ownership only works if the id is stable across
    the many separate `browser` processes one session runs — including across a
    command substitution, which is the drift that created this whole file."""
    env = _env(tmp_path, OPENCODE="1", OPENCODE_SESSION_ID=OC_ID)
    first = _print_id(env)
    second = subprocess.run(
        ["bash", "-c", f'echo "$(bash {CLI} --print-session-id)"'],
        env=env, capture_output=True, text=True, timeout=60)
    assert second.returncode == 0, second.stderr
    assert first == second.stdout.strip() == f"opencode:{OC_ID}"


def test_the_opencode_tier_deliberately_moves_the_routing_id(tmp_path):
    """🔴 THE BEHAVIOUR CHANGE, ASSERTED RATHER THAN DISCOVERED. Section 8 pins
    that `OPENCODE` alone must NOT perturb the id (routing stayed byte-identical),
    and that still holds. This test pins the opposite for the id VARIABLE: it DOES
    move the routing id, so an opencode session that used to inherit its parent's
    tab now owns its own and must `open` one.

    Both halves in one test on purpose — a reader who saw only the section 8
    equivalence tests would conclude routing never changes, and that is now false
    in exactly one way, which this names.
    """
    over = {"CLAUDE_CODE_SESSION_ID": "uuid-routing"}
    without = _print_id(_env(tmp_path, OPENCODE="1", **over))
    with_id = _print_id(_env(tmp_path, OPENCODE="1", OPENCODE_SESSION_ID=OC_ID,
                             **over))
    assert without == "claude:uuid-routing", without
    assert with_id == f"opencode:{OC_ID}", with_id
    assert with_id != without


@pytest.mark.skipif(shutil.which("curl") is None, reason="the CLI uses curl")
def test_the_opencode_run_is_attributed_on_the_wire(capture):
    """🔴 THE POINT OF THE WHOLE CHANGE, at the transport. The tagged opencode id
    reaches the server AND no origin header is sent, which is what lets
    emit_cmd_event fill the `session` column instead of suppressing it.

    Asserted on the request the stub actually received — the CLI could derive the
    right id and still declare an origin, and that combination writes nothing.
    """
    capture.run("emulate", "iphone-15", OPENCODE="1", OPENCODE_SESSION_ID=OC_ID,
                CLAUDE_CODE_SESSION_ID="uuid-outer-wire")
    assert capture.headers, "the stub recorded no request at all"
    h = capture.headers[0]
    assert h.get("X-Session-Id") == f"opencode:{OC_ID}", h
    assert "X-Session-Origin" not in h, h


@pytest.mark.skipif(shutil.which("curl") is None, reason="the CLI uses curl")
def test_the_discriminating_pair_for_the_opencode_id_on_the_wire(capture):
    """🔴 THE PAIR. Same opencode session, same inherited claude ancestor; the
    ONLY difference is whether the plugin's variable made it through. With it the
    call is attributed to the opencode session; without it the call falls back and
    is DISCLAIMED, never credited to the ancestor.

    Both directions in one test because each alone is satisfiable by a wrong
    implementation: a CLI that always declared the origin passes the second half,
    and one that never did passes the first.
    """
    capture.run("emulate", "iphone-15", OPENCODE="1", OPENCODE_SESSION_ID=OC_ID,
                CLAUDE_CODE_SESSION_ID="uuid-pair-outer")
    with_id = capture.headers[0]
    capture.headers.clear()
    capture.run("emulate", "iphone-15", OPENCODE="1",
                CLAUDE_CODE_SESSION_ID="uuid-pair-outer")
    without = capture.headers[0]

    assert with_id.get("X-Session-Id") == f"opencode:{OC_ID}", with_id
    assert "X-Session-Origin" not in with_id, with_id
    assert without.get("X-Session-Id") == "claude:uuid-pair-outer", without
    assert without.get("X-Session-Origin") == "opencode-inherited", without
