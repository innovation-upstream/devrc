"""First test coverage for `scripts/muse/muse` — the Meta Muse wrapper.

🔴 WHY THESE EXIST. The wrapper is 454 lines of bash that can send a message
from the operator's LIVE Muse account across Meta's wire, and until 2026-10-01
NOTHING tested it. A four-agent inventory found the defects pinned below.

🔴 THE RED/GREEN MATRIX, MEASURED at every ref with a sha256 restore control,
after EVERY change to this module. Re-measure when you add, remove or rename a
test -- an earlier version was labelled "MEASURED" and was wrong in all three
cells because three tests were added and nobody re-ran it (#1976 r4), and the
version after that was right about the numbers and wrong about what they meant
(#1976 r5).

    298e4c57  (the PR's base)   12 failed,  4 passed
    c82a16c1  (round 1's tip)    2 failed, 14 passed
    dff18206  (round 2's tip)    2 failed, 14 passed
    HEAD                        16 passed

⚠ Measured WITH muse-cli installed is irrelevant to these numbers -- the test
that needs the binary lives in scripts/devhost-tests/ and is not in this file.

🔴 GREEN AT BASE DOES NOT MEAN "INVARIANT GUARD". An earlier version said "the
other five are INVARIANT GUARDS, labelled as such", and only two of them were.
A reader pruning "invariant guards" would have deleted a real regression test
that round 0 explicitly decided to KEEP. The four green-at-base tests are:

    test_the_stub_is_reachable_and_records      POSITIVE CONTROL for the module
    test_a_fresh_stamp_refuses_a_second_send    INVARIANT GUARD
    test_gap_zero_disables_the_guard            INVARIANT GUARD
    test_help_survives_an_unusable_pacing_gap   REGRESSION -- against a draft
                                                fix made DURING this PR, so the
                                                defect never existed at base

That last row is the phenomenon worth keeping: a test can be green at base and
still be regression coverage, because this ladder's own fix rounds introduced
defects. Read each test's OWN docstring label; do not infer it from the column.

⚠ The counts above are test ITEMS; four of them come from one parametrised
function, so the twelve red-at-base items are nine distinct functions.

To re-measure: `git stash` is banned here, so copy the wrapper aside, then
`git checkout HEAD -- scripts/muse/muse`, run, and copy back.
🔴 `HEAD` IS LOAD-BEARING. A bare `git checkout -- <path>` restores from the
INDEX, so if the fix is already staged it restores the FIX and the "pre-fix"
run comes back fully GREEN -- a false all-green that reads as the regression
tests being vacuous. That happened while writing this module. Prove the
swap landed before trusting the run.
⚠ THE OBVIOUS CONTROL DOES NOT WORK: `grep -c 'COMPOSER" = "no"'` returns 1
at the base AND at HEAD -- at base it matches the live defective code, at
HEAD it matches the comment that quotes it -- so it cannot distinguish the
two trees and reads as a pass either way. Round 3 found it had never been
able to go red. Compare the content instead:
    test $(sha256sum <restored> | cut -d" " -f1) \
       = $(git cat-file blob 298e4c57:scripts/muse/muse | sha256sum | cut -d" " -f1)

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
import re
import subprocess

import pytest

from testlib.mockbin import write_exec

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MUSE = REPO_ROOT / "scripts" / "muse" / "muse"


def source_without_comments() -> str:
    """The wrapper's CODE, with `#` comment lines stripped.

    🔴 Required by the guards that assert an expression is ABSENT, because the
    wrapper's comments deliberately QUOTE the defective code they replaced --
    that is how each fix explains itself. A naive substring scan matches the
    explanation and reports the bug as still present. Measured while writing
    this module: `test_the_enter_retry_...` failed against the FIXED tree
    because the comment above the fix contains the old expression verbatim.

    ⚠ Only the absence-asserting guards strictly need it; a presence
    assertion is unaffected by comments either way. Do not name specific
    examples here -- an earlier version listed `"--config -"` as a presence
    assertion, and the very next round FLIPPED it to `not in source`, which
    strictly requires stripping. The docstring's own example became its
    counter-example inside one round. Two earlier drafts were wrong in the
    opposite direction ("every structural guard below must read THIS"), and a
    third cited a line number that was already off by 22 when it was written.
    This paragraph has now been wrong four times; it names nothing that can
    drift.

    🔴 THE RULE, WITHOUT AN EXAMPLE TO GO STALE: if your assertion is `not
    in`, you MUST read the stripped source -- the comments quote the very
    expressions the fixes removed. If it is `in`, either works.

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

    ONE-WAY, deliberately: it asserts `advertised <= parsed`, i.e. the help may
    not promise a flag the parser ignores. A flag the parser gains and the help
    omits PASSES -- `--thread` is exactly that today.

    🔴 This docstring claimed "two-way by construction … fails if either side
    grows a flag the other lacks", which the assertion does not provide. Caught
    by #1976 round 0. That is RULES.md -> "guards-narrower": a description
    claiming coverage the body has not got is worse than no description,
    because it stops the next reader looking. Equality is not the fix -- it
    would fail today on `--thread` for no defect.
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


def test_force_reaches_the_cli_channel(env):
    """REGRESSION (red before the round-1 fix) -- behavioural, not structural.

    `cmd_send` consumed `--force` and did not forward it, so `cmd_send_cli`
    re-initialised force=0 and ran pace_check a SECOND time. Measured on the
    pre-fix wrapper: `muse send --force --cli` exits 2 with the message "Use
    --force to override deliberately" -- while --force was given.

    #1976 round 2 found the round-1 fix shipped with NO test: removing the
    forwarding, and re-spelling it as the `${force:+--force}` inversion the
    code's own comment warns about, both SURVIVED a green suite.
    """
    stamp = stamp_path(env)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text("0")  # fresh -> the gate would refuse without --force

    proc = run(env, "send", "a benign task", "--force", "--cli")

    assert env["_marker"].exists(), (
        "--force did not reach the --cli channel: the pacing gate refused a "
        f"send the operator explicitly forced (rc={proc.returncode}, "
        f"stderr={proc.stderr.strip()!r})"
    )
    assert "pacing" not in proc.stderr


def test_the_cli_channel_does_not_pace_a_send_it_never_made(env):
    """REGRESSION against ROUND 2's fix, which carved the WRONG exit code.

    muse-cli 0.3.2, muse_cli/cli.py:713-721:
        AuthError    -> exit 2   cookies expired; NEVER sent
        GatewayError -> exit 3   stream reset / non-2xx; MAY have sent
        TimeoutError -> exit 4   MAY have sent

    Round 2 believed rc 3 was auth and carved THAT out. Measured at its tip:
    rc 2 (the real expired cookie) STAMPED -- the regression round 2 said it
    had removed -- while rc 3, which belongs in the ambiguous stamping class,
    did not. One mislabel, wrong in both directions.
    """
    write_exec(pathlib.Path(env["MUSE_CLI"]), 'printf \'%s\\n\' "$*" >> "$MARKER"\nexit 2\n')
    assert not stamp_path(env).exists()

    proc = run(env, "send", "a benign task", "--cli")

    assert proc.returncode == 3, (
        "muse-cli's AuthError (rc 2) must surface as this wrapper's exit 3, "
        f"'muse-cli auth needed'; got {proc.returncode}"
    )
    assert not stamp_path(env).exists(), (
        "an expired cookie stamped the pacing gate -- the operator is paced "
        "out of both channels after a send that never left this machine"
    )


@pytest.mark.parametrize("rc", [1, 3, 4, 127])
def test_the_cli_channel_paces_every_ambiguous_failure(env, rc):
    """The other side of the pair -- the class rc 2 must NOT be widened into.

    A TABLE, not one case: round 2's single rc-1 test could not see that the
    carve-out had been pointed at rc 3. rc 3 (GatewayError) and rc 4
    (TimeoutError) are the ones that MAY have delivered, so they must stamp;
    127 stands for "the binary vanished", which is also not a proof of
    non-delivery by anything the wrapper can observe.
    """
    # f-string, NOT %-formatting: the stub body itself contains `%s` (the
    # printf that records argv), which `%` would consume as a placeholder.
    write_exec(
        pathlib.Path(env["MUSE_CLI"]),
        f'printf \'%s\\n\' "$*" >> "$MARKER"\nexit {rc}\n',
    )

    proc = run(env, "send", "a benign task", "--cli")

    # 🔴 BOTH halves. Round 3's parametrisation widened the rc coverage and
    # silently DROPPED round 2's `assert proc.returncode == 1` -- wider on one
    # axis, narrower on another. Round 4 measured the cost: mutating the
    # non-auth arm to `exit 0` or `exit 7` both SURVIVED a green suite. An
    # `exit 0` there is the dangerous one: `muse send --cli ... || <B1
    # fallback>` -- the fallback b1_hint itself advertises -- would see
    # success and never fall back, and a loop would record a delivery that
    # never happened.
    assert proc.returncode == 1, (
        f"muse-cli rc {rc} must surface as the wrapper's exit 1 (dispatch "
        f"failed); got {proc.returncode}. Only rc 2 maps elsewhere."
    )
    assert stamp_path(env).exists(), (
        f"muse-cli rc {rc} did not stamp -- it is not provably a non-send, so "
        "a caller retrying on failure may deliver a second time with no gap"
    )


def test_the_token_never_reaches_curl_argv():
    """REGRESSION (red before the fix).

    `-H "Authorization: Bearer $token"` placed the bearer token in curl's argv.
    /proc here is mounted without hidepid and /proc/<pid>/cmdline is 0444, so
    any local user could read a cluster-wide-read token for the call's duration.

    A structural assertion on the source: argv-passing and stdin-passing are
    distinguishable in the text. A behavioural test would need the real sops key.

    🔴 SCANS THE WHOLE INVOCATION, NOT ONE PHYSICAL LINE. #1976 round 1 showed
    the line-scoped version SURVIVED a mutant that re-added
    `-H "Authorization: Bearer $token"` on a backslash CONTINUATION line.
    Continuations are joined before the check.

    🔴 AND IT REQUIRES `-H @-`, NOT `--config -`. Both keep the token off argv,
    but `--config` applies C-escape processing and CORRUPTS the value:
    measured against a loopback echo server, `has"quote` arrived as `has` and
    `has\back` as `hasback`, while `-H @-` passed all five probe shapes
    verbatim. A corrupted token reads as a 401, i.e. as a credential problem
    that does not exist.
    """
    source = source_without_comments()
    # Join backslash continuations so a multi-line invocation is one unit.
    joined = source.replace("\\\n", " ")
    curl_cmds = [ln for ln in joined.splitlines() if "curl " in ln and "-sS" in ln]
    assert curl_cmds, "no curl invocation found -- this guard has gone stale"
    for cmd in curl_cmds:
        # Only what curl itself receives. The header NAME legitimately appears
        # UPSTREAM of the pipe, in the `printf` that feeds stdin -- that is the
        # fix, not the defect, so scanning the whole line rejects correct code.
        argv = cmd[cmd.index("curl ") :]
        assert "Authorization" not in argv, (
            f"the bearer token is on curl's command line: {argv.strip()!r} -- "
            "pipe it via `-H @-` on stdin so it stays out of /proc and ps"
        )
        # 🔴 STRUCTURAL, not a word-guard. Round 2 showed the check above is
        # walkable by building the header in a variable first:
        # `hdr="Authorization: Bearer $token"; … -H "$hdr"` puts the token
        # straight back on argv while the literal string "Authorization"
        # never appears in the curl invocation. Assert the SHAPE instead --
        # every `-H` curl receives must be exactly `@-`.
        # Both spellings. Round 3 walked the short-only version with
        # `--header "$hdr"`, which put the token back on argv while
        # h_args == ["@-"] kept this loop satisfied.
        h_args = re.findall(r"(?:-H|--header)\s*=?\s*(\S+)", argv)
        assert h_args, "curl no longer receives a -H argument -- re-anchor"
        for h in h_args:
            assert h == "@-", (
                f"curl receives -H {h!r}; the only permitted value is `@-` "
                "(read from stdin). Anything else risks the token on argv."
            )
    assert "-H @-" in source, (
        "the token must reach curl via `-H @-` on stdin; `--config -` is NOT "
        "an acceptable substitute -- it C-escape-mangles the token"
    )
    assert "--config -" not in source, (
        "`--config -` silently truncates a token at a double quote -- use -H @-"
    )


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

    Pinned as a RELATIONSHIP: the retry must branch on the same two signals
    (composer VALUE length, user-node count) as the first check, WITH THE SAME
    COMPARISONS -- the whole normalised condition, not the tokens in it.

    🔴 THIS GUARD WAS BROKEN TWICE AND #1976 round 1 caught both.
    (a) It sliced on `"Poll the delta"`, a `#` COMMENT that
        `source_without_comments()` strips. `str.split` on a missing separator
        returns the WHOLE string, so `retry` was the entire 8,631-char tail of
        the file -- every later function included. A mutant that deleted the
        check and parked the tokens in dead code at EOF SURVIVED. The module
        docstring 20 lines below states this exact lesson for the pace_mark
        guard, which does it right; this one violated it one function above.
    (b) It asserted only that `$TALEN` and `$UMSGS` were PRESENT, so mutating
        `-le` to `-lt` SURVIVED -- and that mutant is live: Enter inert twice
        with the composer emptied by the UI gives UMSGS == base_umsgs, `-lt`
        false, no die, pace_mark fires, and the operator is told a message
        was sent that was not.

    Both anchors below are CODE. Both are asserted present first, so a moved
    boundary fails loudly here instead of silently widening the slice.
    """
    source = source_without_comments()

    start = 'die "B1: state unreadable after submit retry" 1'
    # ⚠ NOT `pace_mark` as the end anchor: round 1's own fix moved pace_mark
    # ABOVE this block, so the next occurrence after `start` is in
    # cmd_send_cli and the slice silently grew to 2,836 chars. The length
    # assertion below caught it -- which is the whole reason it is there.
    end = "local deadline=$((SECONDS + wait))"
    assert start in source, "retry-block start anchor moved -- re-anchor this guard"
    assert end in source, "retry-block end anchor moved -- re-anchor this guard"
    retry = source.split(start, 1)[1].split(end, 1)[0]
    # The slice must be the retry's tail only, not the rest of the file.
    assert len(retry) < 600, (
        f"the retry slice is {len(retry)} chars -- an anchor stopped matching "
        "and the guard is now scanning unrelated code"
    )
    assert "cmd_status" not in retry and "cmd_poll" not in retry, (
        "the retry slice has swallowed later functions -- see failure (a)"
    )

    assert '"$COMPOSER" = "no"' not in retry, (
        "the retry still gates on the composer ELEMENT being gone, which the "
        "code's own comment says never happens -- it can only ever fail"
    )
    # Pin the WHOLE condition, normalised on whitespace: a token-presence
    # assertion is walkable by changing the operator (failure (b) above).
    expected = '[ "$TALEN" -ne 0 ] || [ "$UMSGS" -le "$base_umsgs" ]'
    assert expected in " ".join(retry.split()), (
        "the retry must re-check the composer VALUE and the user-node count "
        f"with the same comparisons the first submit check uses: {expected}"
    )

    # 🔴 (c) AND THE FIRST CHECK ITSELF. Round 2 found this guard's docstring
    # and failure message both saying "the same comparisons THE FIRST SUBMIT
    # CHECK uses" while the slice above reads the RETRY side only -- the first
    # check sat outside it, asserted by nothing. Mutating it `-le` -> `-lt`
    # and `-ne 0` -> `-eq 0` BOTH survived a green suite. That is the same
    # failure as (b), moved one check up, under a sentence that reads as
    # covering it: RULES.md -> guards-narrower, third occurrence in this
    # module. The live mutant: an inert first Enter with the composer cleared
    # by the UI gives UMSGS == base_umsgs, so under `-lt` the whole condition
    # is false, the retry block is SKIPPED, and the run reaches the poll loop
    # to die with "no reply ... (submit WAS confirmed)" -- asserting a submit
    # that never happened.
    #
    # The two checks must be IDENTICAL; that relationship is the invariant, so
    # assert the string appears on BOTH sides rather than only naming it.
    # 🔴 SLICE the first check too -- do NOT count occurrences. Round 3 showed
    # `count(expected) >= 2` pins ARITY, not identity: a mutant that drifted
    # the first check to `-lt` AND parked a copy of the condition in dead code
    # SURVIVED, and so did a realistic one that added a legitimate third
    # user of the condition. Round 1's failure (a) -- "tokens in dead code" --
    # re-admitted in a new shape, in this same guard.
    f_start = '"$B1_REF" key Enter'
    f_end = 'die "B1: state unreadable right after submit"'
    assert f_start in source and f_end in source, (
        "the first-submit-check anchors moved -- re-anchor this guard"
    )
    # ⚠ ORDER, not just presence. `in source` for both does not establish
    # that f_end FOLLOWS f_start, and the chained split then raises
    # IndexError instead of the message above -- which reads as a broken test
    # rather than a finding. Round 4 reproduced it by moving the die above
    # the Enter.
    assert source.index(f_start) < source.index(f_end), (
        "the first-submit-check anchors are out of order -- re-anchor this "
        "guard rather than reading the slice between them"
    )
    after = source.split(f_start, 1)[1].split(f_end, 1)[1].split("if [", 1)
    assert len(after) > 1, "no condition follows the anchors -- re-anchor"
    first_check = "if [" + after[1].split("then", 1)[0]
    assert expected in " ".join(first_check.split()), (
        "the FIRST submit check no longer uses the same condition as the "
        f"retry ({expected}). The two must be identical -- that identity is "
        "the invariant, and a count of occurrences does not pin it."
    )
    # ⚠ If you ever consolidate these two into a shared helper -- which is the
    # better design, and which muse's own comment recommends for the sibling
    # `pace_check` duplication -- this assertion must be re-anchored onto the
    # helper. It failing on a correct refactor is a false positive, not a bug
    # in the refactor.


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

    # 🔴 STRONGER, after #1976 round 1: not merely "before the poll" but before
    # EVERY exit between the first Enter and the confirmation. Round 1 found
    # two `state unreadable` dies in that window, each able to leave a
    # delivered message unstamped. The stamp must sit above the first of them.
    first_die = 'die "B1: state unreadable right after submit" 1'
    assert first_die in body, "the post-Enter failure path moved -- re-anchor"
    assert body.index("pace_mark") < body.index(first_die), (
        "pace_mark runs AFTER a path that can exit 1 with the message already "
        "delivered -- a caller retrying on exit 1 then re-sends with no gap"
    )
    # ...and the stamp must come after the Enter that makes delivery possible,
    # not before it (which would stamp on every call, sent or not).
    enter = '"$B1_REF" key Enter'
    assert body.index(enter) < body.index("pace_mark"), (
        "pace_mark fires before any Enter is pressed -- it would stamp for "
        "calls that never attempted a send"
    )


# ---------------------------------------------------------------------------
# `muse status` and the bridge's namespace allowlist (rank 25)
#
# These exercise cmd_status END TO END through a stubbed `curl` and `sops`,
# rather than scanning the source, because the defect was never a missing
# expression -- it was a status code nothing BRANCHED on. A source assertion
# would have passed against the broken tree the moment the string "400"
# appeared anywhere, including inside the comment that caused the bug by
# enumerating 401 and 404 and stopping.
#
# 🔴 Hermetic in BOTH tiers: the stubs are prepended to PATH, so they win over
# a real `sops`/`curl` whether or not either is installed. `sops_bin` resolves
# `sops` from PATH first (muse:615-616), which is what makes this possible
# without a devhost-tests entry.
# ---------------------------------------------------------------------------

# Consumes the `-H @-` header on stdin (so the upstream printf cannot SIGPIPE),
# records its argv, and reproduces real curl's output shape EXACTLY: the body,
# then `\n[<code>]` with no trailing newline.
CURL_STUB = """cat >/dev/null
printf '%s\\n' "$*" >> "$CURL_ARGV"
printf '%s' "$STUB_BODY"
printf '\\n[%s]' "$STUB_CODE"
"""

SOPS_STUB = """printf 'stub-bridge-token\\n'
"""

TOKEN_ENC_REL = "clusters/homelab/apps/muse/muse-bridge-token.enc.yaml"


@pytest.fixture()
def status_env(tmp_path: pathlib.Path):
    """cmd_status with `curl` and `sops` stubbed, and a fake homelab checkout."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    write_exec(bindir / "curl", CURL_STUB)
    write_exec(bindir / "sops", SOPS_STUB)

    homelab = tmp_path / "homelab"
    enc = homelab / TOKEN_ENC_REL
    enc.parent.mkdir(parents=True)
    enc.write_text("stub\n")
    key = homelab / ".secrets" / "age.key"
    key.parent.mkdir(parents=True)
    key.write_text("stub\n")

    argv = tmp_path / "curl-argv"
    return {
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "MUSE_HOMELAB_REPO": str(homelab),
        "MUSE_BRIDGE_URL": "https://bridge.invalid",
        "CURL_ARGV": str(argv),
        "STUB_CODE": "200",
        "STUB_BODY": '{"items":[],"count":0}',
        "_argv": argv,
    }


def test_the_status_stubs_are_reachable(status_env):
    """POSITIVE CONTROL for every assertion below.

    Without this, each one would pass just as happily if `cmd_status` died in
    bridge_token and never reached curl at all -- an absent explanation and an
    unreached code path are the same observable on stderr.
    """
    proc = run(status_env, "status")
    assert proc.returncode == 0, f"cmd_status did not complete: {proc.stderr}"
    assert status_env["_argv"].exists(), (
        "the curl stub was never invoked -- cmd_status failed upstream of it, "
        "so this module's stderr assertions prove nothing"
    )
    assert "/v1/health" in status_env["_argv"].read_text()


def test_a_disallowed_namespace_is_explained_as_policy_not_a_typo(status_env):
    """REGRESSION (red at origin/main) for rank 25.

    homelab-infra #942/#944 deployed a server-side namespace allowlist, so
    every `muse status pods <ns>` outside it answers 400. The wrapper branched
    on no code at all and its trailing comment enumerated only 401 and 404, so
    the operator saw a bare error body and read a POLICY DENIAL as a typo.
    """
    env = {**status_env, "STUB_CODE": "400",
           "STUB_BODY": '{"error":"namespace not served: this bridge is '
                        'restricted to an allowlist"}'}
    proc = run(env, "status", "pods", "flux-system")

    assert "POLICY ANSWER" in proc.stderr, (
        "a 400 produced no policy explanation -- the operator cannot tell a "
        f"refused namespace from a misspelled one. stderr={proc.stderr!r}"
    )
    # The refused namespace itself, so the message names WHAT was refused.
    assert "flux-system" in proc.stderr
    # Where the list actually lives -- without this the explanation says "not
    # allowed" and leaves the reader with nowhere to go.
    assert "MUSE_BRIDGE_NS_ALLOW" in proc.stderr
    # The body is still the authority on which half refused, so it must survive
    # to stdout rather than being replaced by the explanation.
    assert "namespace not served" in proc.stdout
    assert "[400]" in proc.stdout


def test_a_nonverb_first_word_is_named_as_a_namespace_guess(status_env):
    """REGRESSION (red at origin/main) for the INVERSE misread.

    cmd_status reinterprets any non-verb first word as a namespace, so
    `muse status pdos` is a pods query for ns=pdos. Once the allowlist landed
    that answers 400 -- identical on the wire to a real namespace being
    refused. Only this branch can produce a verb typo, so only it can say so.
    """
    env = {**status_env, "STUB_CODE": "400",
           "STUB_BODY": '{"error":"namespace not served"}'}
    proc = run(env, "status", "pdos")

    assert "NOT A VERB" in proc.stderr, (
        "a mistyped verb was reported purely as a refused namespace, which is "
        f"the misdirection rank 25 exists to remove. stderr={proc.stderr!r}"
    )
    assert "pdos" in proc.stderr
    # The real verbs, so the reader does not have to go find usage().
    for verb in ("health", "nodes", "pods", "workloads", "flux"):
        assert verb in proc.stderr


def test_an_explicit_verb_is_not_reported_as_a_verb_typo(status_env):
    """NEGATIVE CONTROL for the guess branch -- it must not fire on a real verb.

    Without this, `guessed_ns=1` unconditionally would pass the test above and
    tell the operator `flux-system` is not a verb on every refusal.
    """
    env = {**status_env, "STUB_CODE": "400",
           "STUB_BODY": '{"error":"namespace not served"}'}
    proc = run(env, "status", "workloads", "flux-system")

    assert "POLICY ANSWER" in proc.stderr, "the policy branch stopped firing"
    assert "NOT A VERB" not in proc.stderr, (
        "`workloads` IS a verb and was reported as a mistyped one"
    )


def test_a_route_that_sends_no_ns_is_not_blamed_on_the_allowlist(status_env):
    """NEGATIVE CONTROL: /v1/nodes carries no ns, so a 400 there is not a
    namespace refusal and must not be explained as one. `nodes` is outside the
    allowlist BY DESIGN (it carries no namespace identity), so conflating the
    two would send a reader to widen a list that was never consulted.
    """
    env = {**status_env, "STUB_CODE": "400",
           "STUB_BODY": '{"error":"something else"}'}
    proc = run(env, "status", "nodes")

    assert "POLICY ANSWER" in proc.stderr
    assert "MUSE_BRIDGE_NS_ALLOW" not in proc.stderr, (
        "a 400 on a route that sends no ns was blamed on the namespace "
        "allowlist"
    )


@pytest.mark.parametrize("code", ["200", "401", "404", "500"])
def test_only_400_gets_the_policy_explanation(status_env, code):
    """NEGATIVE CONTROL for the whole branch.

    A `case` that matched too widely -- or an unconditional printf -- would
    pass every positive assertion above while telling the operator that a
    successful query was a policy refusal.
    """
    env = {**status_env, "STUB_CODE": code, "STUB_BODY": '{"items":[]}'}
    proc = run(env, "status", "pods", "muse")
    assert "POLICY ANSWER" not in proc.stderr, (
        f"HTTP {code} was explained as a 400 policy refusal"
    )


def test_the_body_and_status_code_still_reach_stdout(status_env):
    """INVARIANT GUARD (not a regression) on the output contract.

    Reading the status code required CAPTURING curl's output instead of
    streaming it. That is exactly the shape that silently stops printing the
    body -- and `status` has no other output, so a caller would see nothing.
    """
    env = {**status_env, "STUB_CODE": "200",
           "STUB_BODY": '{"items":[{"name":"muse-bridge"}],"count":1}'}
    proc = run(env, "status", "pods", "muse")

    assert proc.returncode == 0, proc.stderr
    assert '"name":"muse-bridge"' in proc.stdout, (
        "the response body no longer reaches stdout"
    )
    assert proc.stdout.rstrip().endswith("[200]"), (
        "the `[<code>]` suffix no longer terminates stdout -- anything parsing "
        f"it breaks. stdout={proc.stdout!r}"
    )


def test_the_status_exit_code_is_unchanged_by_the_400_branch(status_env):
    """INVARIANT GUARD: `status` exits 0 for every HTTP answer, 400 included.

    Deliberate and documented (muse's header legend; SKILL.md). The inability
    of a caller to branch on it is a REAL defect, batched as M3/M4 in
    claudedocs/handoff-muse-system-inventory.md -- this guard exists so rank 25
    cannot change that contract as a side effect, and so that whoever DOES fix
    M3 has to come here and change it on purpose.
    """
    for code in ("200", "400", "401", "404", "500"):
        env = {**status_env, "STUB_CODE": code, "STUB_BODY": "{}"}
        proc = run(env, "status", "pods", "muse")
        assert proc.returncode == 0, (
            f"HTTP {code} changed cmd_status's exit status to "
            f"{proc.returncode}; if that is intended, M3 is being fixed and "
            "this guard plus the header legend must move together"
        )
