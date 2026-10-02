"""First test coverage for `scripts/muse/muse` — the Meta Muse wrapper.

🔴 WHY THESE EXIST. The wrapper is 454 lines of bash that can send a message
from the operator's LIVE Muse account across Meta's wire, and until 2026-10-01
NOTHING tested it. A four-agent inventory found the defects pinned below.

🔴 THE RED/GREEN MATRIX, MEASURED -- not asserted. Run against the PRE-FIX
wrapper (`origin/main` at 298e4c57) this module is **6 failed, 4 passed**;
against the fixed tree, **10 passed**. So six are genuine REGRESSION tests:

    test_pace_check_fails_closed_on_a_nonnumeric_gap
    test_usage_advertises_no_flag_the_parser_ignores
    test_the_token_never_reaches_curl_argv
    test_b1_state_does_not_clobber_the_login_name
    test_the_enter_retry_rechecks_the_signal_the_first_check_used
    test_pace_mark_fires_on_confirmed_submit_not_after_the_reply

The other four are INVARIANT GUARDS and are labelled as such in their own
docstrings -- they pass in both trees by design and must not be counted as
regression coverage. To re-measure: `git stash` is banned here, so copy the
wrapper aside, `git checkout -- scripts/muse/muse`, run, then copy back.

🔴 HOW TO WRITE A TEST HERE WITHOUT SENDING A REAL MESSAGE. On 2026-10-01 an
agent auditing this wrapper sent a real message to the operator's account. It
built a stub environment as a STRING and interpolated it unquoted:

    E="MUSE_BB=/bin/false MUSE_CLI=stub"
    env $E bash muse send "a task"      # zsh does NOT word-split $E

zsh passed `$E` to `env` as ONE argument, so none of the overrides applied and
the command ran against the real browser-bridge. So:

  * Build env as a **dict** and pass it to `subprocess.run(env=...)`. Never as
    an interpolated shell string.
  * Point `MUSE_BB` at a **stub that records being called**, and assert on the
    marker. A stub whose absence is undetectable cannot prove the real binary
    was not reached — that is the whole lesson.
  * Give every test its own `XDG_STATE_HOME` so the pacing stamp is scratch.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from testlib.mockbin import write_exec

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MUSE = REPO_ROOT / "scripts" / "muse" / "muse"


def source_without_comments() -> str:
    """The wrapper's CODE, with `#` comment lines stripped.

    🔴 Every structural guard below must read THIS, not the raw file. The
    comments here deliberately quote the defective code they replaced (that is
    how the fix explains itself), so a naive substring scan matches the
    explanation and reports the bug as still present. Measured while writing
    this module: `test_the_enter_retry_...` failed against the FIXED tree
    because the comment above the fix contains the old expression verbatim.

    Line-level stripping only -- adequate because every quoted expression here
    sits in a whole-line comment, and a trailing-comment variant would still be
    caught by the assertions that require the NEW signals to be present.
    """
    return "\n".join(
        ln for ln in MUSE.read_text().splitlines() if not ln.lstrip().startswith("#")
    )

# A stub standing in for the browser-bridge. It appends its argv to $MARKER and
# exits 1, so the wrapper's B1 path cannot get past its first bridge call --
# and the marker proves whether it was reached at all. The exit-1 matters: a
# stub that succeeds would let the wrapper proceed toward a real send path.
#
# 🔴 NO SHEBANG HERE -- testlib.mockbin.write_exec owns it, and refuses a body
# that brings its own. A hand-written `#!/usr/bin/env bash` execs fine on this
# NixOS dev host and ENOENTs in the nix build sandbox, which is the tier that
# gates. Caught by scripts/tests/test_runtime_shebangs.py while writing this
# module -- the first draft did exactly that and was green locally.
STUB_BODY = """printf '%s\\n' "$*" >> "$MARKER"
exit 1
"""


@pytest.fixture()
def env(tmp_path: pathlib.Path):
    """A hermetic environment: stubbed bridge, scratch state dir, marker file."""
    stub = write_exec(tmp_path / "bb-stub", STUB_BODY)
    marker = tmp_path / "bridge-was-called"
    return {
        # 🔴 INHERIT PATH, never replace it. Two reasons, both measured while
        # writing this: a literal "/usr/bin:/bin" has no `bash` on NixOS (every
        # subprocess test died FileNotFoundError), and conftest.py PREPENDS the
        # testlib/nolaunch stub dir to PATH for the whole session -- replacing
        # it would hand these tests the operator's real systemctl/notify-send.
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "MUSE_BB": str(stub),
        "MUSE_CLI": str(stub),
        "MARKER": str(marker),
        "_marker": marker,  # test-side handle; stripped before exec
    }


def run(env: dict, *args: str) -> subprocess.CompletedProcess:
    real = {k: v for k, v in env.items() if not k.startswith("_")}
    return subprocess.run(
        ["bash", str(MUSE), *args],
        env=real,
        capture_output=True,
        text=True,
        timeout=60,
    )


def stamp_path(env: dict) -> pathlib.Path:
    return pathlib.Path(env["XDG_STATE_HOME"]) / "muse" / "last-send"


def test_the_stub_is_reachable_and_records(env):
    """POSITIVE CONTROL for the whole module.

    Every other test here asserts the bridge was NOT reached. A zero marker is
    indistinguishable from a harness wired to nothing, so one test must show
    the marker CAN move. Without this, `test_pace_check_fails_closed...` would
    pass just as happily against a stub path that never resolves.
    """
    env["XDG_STATE_HOME"] = str(pathlib.Path(env["HOME"]) / "fresh")
    run(env, "send", "a benign task")
    assert env["_marker"].exists(), (
        "the stub bridge was never invoked — this module's negative assertions "
        "would be vacuous"
    )


def test_pace_check_fails_closed_on_a_nonnumeric_gap(env):
    """REGRESSION (red before the 2026-10-01 fix, green after).

    `[ "$gap" -lt N ]` with a non-numeric operand makes `[` ERROR, and an
    erroring `[` inside an `if` is simply FALSE -- so the guard fell through
    and ALLOWED the send after printing "integer expected" to stderr.
    """
    stamp = stamp_path(env)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text("0")  # exists and is brand new -> inside any sane gap

    env["MUSE_MIN_SEND_GAP_MIN"] = "10m"
    proc = run(env, "send", "a benign task")

    assert proc.returncode != 0, "a non-integer gap must refuse, not fall through"
    assert "must be a non-negative integer" in proc.stderr
    assert not env["_marker"].exists(), (
        "the bridge was reached despite an unusable pacing gap -- the guard "
        "failed OPEN, which is the defect this test pins"
    )


def test_a_fresh_stamp_refuses_a_second_send(env):
    """INVARIANT GUARD (not a regression -- this path always worked).

    Pins the ordinary refusal so a future refactor of pace_check cannot quietly
    remove it while the fail-closed test above still passes.
    """
    stamp = stamp_path(env)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text("0")  # mtime = now

    proc = run(env, "send", "a benign task")

    assert proc.returncode == 2
    assert "pacing" in proc.stderr
    assert not env["_marker"].exists()


def test_gap_zero_disables_the_guard(env):
    """INVARIANT GUARD -- documented escape hatch stays open.

    Asserts via the marker (the bridge IS reached) rather than via exit code,
    because the stub makes the run fail for an unrelated reason afterwards.
    """
    stamp = stamp_path(env)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text("0")

    env["MUSE_MIN_SEND_GAP_MIN"] = "0"
    run(env, "send", "a benign task")

    assert env["_marker"].exists(), "gap=0 must bypass the guard as documented"


def test_help_survives_an_unusable_pacing_gap(env):
    """REGRESSION against the FIX, not the original bug.

    The first draft validated the gap at top level, which made a bad env var
    break `--help`, `b1` and `setup` too. The validation belongs on the send
    path only. This is the test that pins that scoping decision.
    """
    env["MUSE_MIN_SEND_GAP_MIN"] = "10m"
    for verb in ("--help", "b1", "setup"):
        proc = run(env, verb)
        assert proc.returncode == 0, f"`muse {verb}` must not die on a bad gap"


def test_usage_advertises_no_flag_the_parser_ignores(env):
    """REGRESSION (red before the fix).

    `usage()` advertised `poll [--wait N]`; `cmd_poll` never parsed `--wait` and
    `cmd_poll_b1` ignored its arguments entirely -- so an agent reading the help
    passed `--wait 300` and silently got the hardcoded behaviour.

    Two-way by construction: it reads the help TEXT and the parser SOURCE, so it
    fails if either side grows a flag the other lacks.
    """
    help_text = run(env, "--help").stdout
    source = source_without_comments()

    poll_line = next(
        ln for ln in help_text.splitlines() if ln.strip().startswith("muse poll")
    )
    advertised = {tok.strip("[]") for tok in poll_line.split() if tok.startswith("[--")}

    # The flags cmd_poll actually parses, read from its own case arms.
    body = source.split("cmd_poll()", 1)[1].split("cmd_vm", 1)[0]
    parsed = {tok for tok in ("--cli", "--thread", "--limit", "--wait") if f"{tok})" in body}

    assert advertised <= parsed, (
        f"usage() advertises {sorted(advertised - parsed)} for `poll`, which "
        "cmd_poll does not parse -- a phantom flag"
    )


def test_the_token_never_reaches_curl_argv():
    """REGRESSION (red before the fix).

    `-H "Authorization: Bearer $token"` placed the bearer token in curl's argv.
    /proc here is mounted without hidepid and /proc/<pid>/cmdline is 0444, so
    any local user could read a cluster-wide-read token for the call's duration.

    A structural assertion on the source: argv-passing and stdin-passing are
    distinguishable in the text, and this is the narrowest expression that can
    be wrong. A behavioural test would need the real sops key.
    """
    source = source_without_comments()
    curl_lines = [ln for ln in source.splitlines() if "curl " in ln and "-sS" in ln]
    assert curl_lines, "no curl invocation found -- this guard has gone stale"
    for line in curl_lines:
        assert "Authorization" not in line, (
            f"the bearer token is on curl's command line: {line.strip()!r} -- "
            "pass it via --config - on stdin so it stays out of /proc and ps"
        )
    assert "--config -" in source


def test_b1_state_does_not_clobber_the_login_name():
    """REGRESSION (red before the fix).

    `read -r ... USER ...` wrote the user-message COUNT into $USER, and
    assignment to an already-exported name keeps the export -- so every
    subsequent bridge child inherited `USER=<a number>`.
    """
    source = source_without_comments()
    read_line = next(
        ln for ln in source.splitlines() if ln.strip().startswith("read -r COMPOSER")
    )
    fields = read_line.split("<<<")[0].split()[2:]
    assert "USER" not in fields, (
        f"b1_state reads into $USER, clobbering the login-name env var: {fields}"
    )
    assert "UMSGS" in fields


def test_the_enter_retry_rechecks_the_signal_the_first_check_used():
    """REGRESSION (red before the fix) for a guard that could only ever FAIL.

    The retry's success check tested `[ "$COMPOSER" = "no" ]` -- whether the
    textarea ELEMENT was gone -- while the comment directly above it records
    that the element always stays mounted. So every send taking the retry path
    died with "Enter was inert twice" even when the retry had worked.

    Pinned as a RELATIONSHIP, not a spelling: the retry must branch on the same
    two signals (composer VALUE length, user-node count) as the first check.
    """
    source = source_without_comments()
    retry = source.split("submit retry", 1)[1].split("Poll the delta", 1)[0]

    assert '"$COMPOSER" = "no"' not in retry, (
        "the retry still gates on the composer ELEMENT being gone, which the "
        "code's own comment says never happens -- it can only ever fail"
    )
    assert "$TALEN" in retry and "$UMSGS" in retry, (
        "the retry must re-check the composer VALUE and the user-node count, "
        "the same two signals the first submit check uses"
    )


def test_pace_mark_fires_on_confirmed_submit_not_after_the_reply():
    """REGRESSION (red before the fix) for the agent-loop spam path.

    pace_mark ran only after the reply poll, so a send that SUBMITTED and then
    timed out never stamped -- and a caller retrying on exit 1 re-sent with no
    gap. The message had already reached Meta.

    Pinned by ORDER within cmd_send_b1: pace_mark must precede the poll loop.
    """
    source = source_without_comments()
    body = source.split("cmd_send_b1()", 1)[1].split("cmd_send_cli", 1)[0]

    assert "pace_mark" in body, "cmd_send_b1 no longer stamps at all"
    # Anchor on CODE, not a comment -- source_without_comments() strips the
    # latter, and an anchor that vanishes raises ValueError rather than
    # failing the assertion, which reads as a broken test instead of a finding.
    poll_loop = "while [ $SECONDS -lt $deadline ]"
    assert poll_loop in body, "the poll loop moved -- re-anchor this guard"
    assert body.index("pace_mark") < body.index(poll_loop), (
        "pace_mark runs after the reply poll, so a submit-then-timeout leaves "
        "the pacing gate unmarked and an immediate retry re-sends"
    )
