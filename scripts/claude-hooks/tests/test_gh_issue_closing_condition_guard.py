#!/usr/bin/env python3
"""Tests for gh-issue-closing-condition-guard.py — the gate that makes a GitHub
issue CREATE name the condition that ends it.

WHAT THIS FILE IS FOR

  1. 🔴 BOTH DIRECTIONS, AND A MENTION IS NOT AN INVOCATION. This hook DENIES a
     Bash call, on `gh` — the most-typed tool in these repos. Firing it on a
     `grep`/`echo`/`rg` for the command string, on a heredoc that DOCUMENTS it, or
     on the string appearing inside some OTHER command's `--body`, would put a
     block in front of work that owes nothing to anyone. That is not an edge case
     here: `bash-guard.py` blocked a real `grep` for exactly this reason while
     this gate was being specified. Every one of those shapes is a case below, not
     an argument in a comment. And a guard nobody has watched ALLOW is as unproven
     as one nobody has watched deny.

  2. 🔴 EVERY BODY SOURCE, INCLUDING THE ONES THAT MUST BLOCK. `--body`, `-b`,
     `--body-file <path>`, `-F <path>`, a heredoc, a `gh api -f body=…` field and
     a curl JSON payload are readable; a body from a generator substitution, from
     stdin (`--body-file -`), or from a file that does not exist is NOT, and the
     gate must BLOCK there. Failing open on an unreadable body would make the
     guard walkable by changing the SHAPE of the call rather than its content — a
     spelled guard, not a structural one. Asserted with the two verdicts kept
     DISTINCT: "no closing condition" and "cannot see the body" are different
     facts with different messages, so a test cannot pass by observing "it
     blocked" alone.

  3. 🔴 THE DETECTOR IS PINNED AS A LITERAL TABLE, never derived from the regex it
     tests — and BOTH of its conditions are exercised. A bare heading with nothing
     under it is a REJECT, because the near-miss this gate exists to catch is a
     pasted template that was never filled in. `###`, bold text, a missing space
     after `##`, and a heading that only appears inside a ``` fence are rejects
     too.

  4. 🔴 THE OVERRIDE IS STRUCTURAL, NOT SPELLED. `GH_ISSUE_NO_CLOSING_CONDITION=1`
     quoted INSIDE an issue body must NOT disarm the gate: a guard you can switch
     off by naming it in prose is not a guard. Both real channels (a
     command-position assignment, the process environment) are driven through a
     REAL subprocess, and the near-miss spellings (`=true`, `=yes`, `=0`, empty)
     are asserted NOT to override.

  5. THE I/O CONTRACT, THROUGH A REAL PROCESS. A PreToolUse hook that exits
     non-zero for any reason other than 2 is a silent ALLOW, so "exits 0" is
     checked on the malformed-input paths too — where an in-process assertion
     could not see a traceback.

  6. 🔴 THE MESSAGE MUST CARRY ITS OWN LIMITS. The gate cannot check that what was
     written under the heading is an observable end-state rather than a restated
     fix; a message that reads as if it could would be a guard claiming coverage
     it does not have. Asserted on both deny shapes, along with the pointer to the
     definition site — which is asserted to EXIST, because a router pointing at
     nothing is worse than no router.

  7. THE WIRING SEAM. A hook that ships and is never registered is inert and says
     nothing about it (#452). home.nix must deploy it and the registrar must
     register it on PreToolUse(Bash); both are asserted here rather than left to
     the delivery-seam suite alone.

  8. NEGATIVE AND POSITIVE CONTROLS ON THIS FILE ITSELF. `test_control_*` feed the
     harness one case that MUST deny and one that MUST allow. A suite that
     reported a reassuring zero because it was wired to nothing would fail them.
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.abspath(
    os.path.join(HERE, os.pardir, "gh-issue-closing-condition-guard.py"))
ROOT = Path(HERE).resolve().parents[2]
DEFINITION = ROOT / "claude" / "skills" / "clawgate" / "flows" / "task-authoring.md"
HOME_NIX = ROOT / "nix" / "home.nix"
REGISTRAR = ROOT / "scripts" / "claude-hooks" / "register-nudge-hook.py"
RULES = ROOT / "claude" / "RULES.md"


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


guard = _load("gh_issue_closing_condition_guard_undertest", HOOK)
# The hook resolves `guard_core` from its own directory; do the same here so the
# in-process helpers below see exactly the module the deployed hook would.
sys.path.insert(0, os.path.abspath(os.path.join(HERE, os.pardir)))
import guard_core  # noqa: E402

# --------------------------------------------------------------------------- #
# LITERALS, never `guard.<CONSTANT>`.
#
# 🔴 An expectation read out of the module under test asserts only that the module
# agrees with itself: a mutant that renames the definition path, or widens the
# override spelling, changes BOTH sides at once and survives. This is the lesson
# test_clawgate_task_interview_guard.py records after two such mutants survived
# its first sweep.
#
# The command word is built from pieces for a second reason: this repo's own
# `bash-guard.py` matches raw command text, and a test fixture that spells a
# guarded shape end-to-end has blocked tooling before.
# --------------------------------------------------------------------------- #
GH = "gh"
CREATE = GH + " issue create"
OVERRIDE = "GH_ISSUE_NO_CLOSING_CONDITION"
OVERRIDE_ON = OVERRIDE + "=1"
DEPLOYED_DEF = "~/.claude/skills/clawgate/flows/task-authoring.md"
REPO_DEF = "devrc/claude/skills/clawgate/flows/task-authoring.md"
HOOK_BASENAME = "gh-issue-closing-condition-guard.py"

# The two verdict families, identified by a phrase that appears in exactly one of
# them. Asserting on these keeps "it blocked" from standing in for "it blocked for
# the right reason".
MISSING_MARK = "names no closing condition"
UNSEEABLE_MARK = "CANNOT SEE THE BODY"
LIMIT_MARK = "not machine-checkable"

REASON_STDIN = "the body is piped in on stdin, where a PreToolUse hook cannot read it"
REASON_OPAQUE = "the argument is a shell substitution this gate cannot evaluate"
REASON_NONE = "the command names no body this gate can read"
REASON_UNREADABLE = "the body-file path could not be read"
BODY_FILE_CAP = 1024 * 1024

# A well-formed body and a specified-nothing body. Values are deliberately
# distinct from every constant this suite asserts against.
CC = ("## Closing condition\n"
      "the wedge counter reads 0 for 24h after the rollout\n"
      "Checked by: the alert clearing in Grafana")
AC = ("## Acceptance criteria\n"
      "the poller writes a row every 5 minutes\n"
      "Checked by: a query returning 288 rows for a day")
NO_CC = "make the pill nicer, you know the one"


def q(s):
    """Single-quote for the shell, keeping real newlines real."""
    return "'" + s.replace("'", "'\\''") + "'"


# 🔴 A FRESH, EMPTY DIRECTORY — NOT A HARD-CODED `/tmp/…` NAME. Several cases
# below assert a verdict that depends on a body-file path NOT existing
# (`REASON_UNREADABLE`) or on one that a heredoc is about to write. A literal
# `/tmp/x.md` makes those a claim about this machine's `/tmp`: a leftover file
# from an earlier debugging session silently flips the verdict, and the test
# reports green or red for a reason that has nothing to do with the code.
# Module-scoped rather than `tmp_path` because the module-level command TABLES
# need it at import time.
SCRATCH = tempfile.mkdtemp(prefix="ghccg-test-")


def scratch(name):
    return os.path.join(SCRATCH, name)


# --------------------------------------------------------------------------- #
# drivers
# --------------------------------------------------------------------------- #
def verdict(cmd, env=None, tool_name="Bash", raw=None, hook=None):
    """Run the REAL hook as a subprocess. Returns (returncode, parsed-json-or-None).

    A subprocess and not `guard.evaluate`, because the claims that matter most
    here — "always exits 0", "prints nothing on an allow" — are claims about the
    PROCESS. An in-process call cannot observe a traceback or a stray write to
    stdout.

    `hook` points the run at a COPY of the hook, which is how the crash-path
    tests drive a real import failure.
    """
    e = dict(os.environ)
    e.pop(OVERRIDE, None)
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    if env:
        e.update(env)
    payload = raw if raw is not None else json.dumps(
        {"tool_name": tool_name, "hook_event_name": "PreToolUse",
         "cwd": str(ROOT), "tool_input": {"command": cmd}})
    p = subprocess.run([sys.executable, hook or HOOK], input=payload,
                       capture_output=True, text=True, env=e)
    assert p.returncode == 0, (
        f"a PreToolUse hook must exit 0; got {p.returncode}\n{p.stderr}")
    if not p.stdout.strip():
        return p.returncode, None
    return p.returncode, json.loads(p.stdout)


def reason_of(out):
    assert out is not None, "expected a deny, got an allow"
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    return hso["permissionDecisionReason"]


def deny_reason(cmd, env=None):
    _, out = verdict(cmd, env=env)
    return reason_of(out)


def assert_allowed(cmd, env=None):
    rc, out = verdict(cmd, env=env)
    assert out is None, (
        f"expected ALLOW for {cmd!r}\n"
        f"got: {out['hookSpecificOutput']['permissionDecisionReason'][:400]}")


# In-process evaluation, for the table-sized batteries where a subprocess per case
# would make the suite slow. The subprocess driver above covers the process
# contract; this covers volume.
def ev(cmd, env=None):
    return guard.evaluate(cmd, env or {}, guard_core)


# =========================================================================== #
# 1. THE DETECTOR — a literal table, both of its conditions.
# =========================================================================== #
ACCEPTS = [
    "## Closing condition\nthe queue drains to 0",
    "## closing condition\nthe queue drains to 0",
    "## CLOSING CONDITION\nthe queue drains to 0",
    "## Closing conditions\nboth queues drain",
    "## Closing condition: the queue drains\nChecked by: a query",
    "## Acceptance criteria\nthe suite is green",
    "## acceptance criteria\nthe suite is green",
    "  ## Closing condition\n  the queue drains to 0",
    "   ## Closing condition\n   the queue drains to 0",
    "##\tClosing\tcondition\nthe queue drains to 0",
    "intro prose\n\n## Closing condition\nthe queue drains to 0\n\nmore prose",
    "## Closing condition\n\n\nthe queue drains after two blank lines",
    "## Closing condition\n### how\nby a query",
    "## Closing condition\n```\na command exiting 0\n```",
    "## Notes\nx\n## Closing condition\nthe queue drains to 0",
    "## Closing condition\nthe queue drains\n## Notes\nunrelated",
    "```\n## Closing condition\nin a fence\n```\n## Closing condition\nreal one",
]

REJECTS = [
    "",
    "make the pill nicer",
    "## Closing condition",                       # heading, nothing under it
    "## Closing condition\n",
    "## Closing condition\n\n",
    "## Closing condition\n\n## Notes\nunrelated",  # next heading, no content
    "## Closing condition\n# Notes\nunrelated",
    "### Closing condition\nthe queue drains",     # level 3
    "# Closing condition\nthe queue drains",       # level 1
    "#### Closing condition\nthe queue drains",
    "**Closing condition**\nthe queue drains",
    "##Closing condition\nthe queue drains",       # no space after ##
    "    ## Closing condition\nthe queue drains",  # 4 spaces = code block
    "## Closing conditionality\nthe queue drains",
    "## Closingcondition\nthe queue drains",
    "## Acceptancecriteria\nthe suite is green",
    "## Acceptance criterion\nthe suite is green",
    "closing condition: the queue drains",
    "```\n## Closing condition\nonly inside a fence\n```",
    "~~~\n## Closing condition\nonly inside a tilde fence\n~~~",
    "```md\n## Closing condition\ntemplate quoted, never filled in\n```",
    "## Closing condition\n```\n```\n".replace("```\n```", "\n"),  # blank-only section
]


@pytest.mark.parametrize("body", ACCEPTS)
def test_the_detector_accepts(body):
    assert guard.has_closing_condition(body) is True, body


@pytest.mark.parametrize("body", REJECTS)
def test_the_detector_rejects(body):
    assert guard.has_closing_condition(body) is False, body


@pytest.mark.parametrize("bad", [None, 0, [], {}, b"## Closing condition\nx"])
def test_the_detector_rejects_non_strings(bad):
    assert guard.has_closing_condition(bad) is False


def test_a_heading_with_no_content_is_the_named_near_miss():
    """🔴 The condition that separates this detector from the clawgate one.

    Pinned on its own, with the CONTENT-bearing twin beside it, so a mutant that
    drops the emptiness check cannot survive by passing the accept table alone.
    """
    assert guard.has_closing_condition("## Closing condition") is False
    assert guard.has_closing_condition("## Closing condition\nx") is True


def test_a_third_level_heading_is_not_a_section_boundary():
    """`###` under the heading is still inside the section, so it is content."""
    assert guard.has_closing_condition("## Closing condition\n### detail\nx") is True


# =========================================================================== #
# 2. A MENTION IS NOT AN INVOCATION — the first-class requirement.
# =========================================================================== #
MENTIONS = [
    "grep " + q(CREATE) + " notes.md",
    "grep -rn " + q(CREATE + " --body") + " claude/",
    "rg -n " + q(CREATE) + " docs/",
    'echo "' + CREATE + '"',
    "echo " + q(CREATE + " --body " + q(NO_CC)),
    "printf '%s\\n' " + q(CREATE),
    # the command text inside ANOTHER command's body
    "gh pr create --title t --body " + q("do not run " + CREATE + " --body x"),
    "gh issue comment 12 --body " + q("we used " + CREATE + " --body x"),
    "git commit -F /tmp/msg.md",
    # a heredoc that DOCUMENTS the command
    "cat > /tmp/notes.md <<'EOF'\n" + CREATE + " --body x\nEOF",
    "cat > /tmp/notes.md <<EOF\n" + CREATE + " --body x\nEOF",
    "tee /tmp/notes.md <<'EOF'\n" + CREATE + " --body x\nEOF",
    "tee -a /tmp/notes.md <<'EOF'\n" + CREATE + " --body x\nEOF",
    "cat <<'EOF'\n" + CREATE + " --body x\nEOF",
    "cat <<-'EOF'\n\t" + CREATE + " --body x\n\tEOF",
    "printf '%s' \"$(cat <<'EOF'\n" + CREATE + " --body x\nEOF\n)\"",
    # a comment
    "gh pr list  # " + CREATE + " needs a closing condition",
]


@pytest.mark.parametrize("cmd", MENTIONS)
def test_a_mention_is_allowed(cmd):
    assert ev(cmd) is None, cmd


@pytest.mark.parametrize("cmd", MENTIONS[:6] + MENTIONS[9:12])
def test_a_mention_is_allowed_through_the_real_process(cmd):
    assert_allowed(cmd)


HEREDOCS_THAT_EXECUTE = [
    "bash <<'EOF'\n" + CREATE + " -t t --body x\nEOF",
    "sh <<EOF\n" + CREATE + " -t t --body x\nEOF",
    "zsh <<'EOF'\n" + CREATE + " -t t --body x\nEOF",
    "cat <<'EOF' | bash\n" + CREATE + " -t t --body x\nEOF",
    "cat <<'EOF' | sh -\n" + CREATE + " -t t --body x\nEOF",
    "python3 - <<'EOF'\n" + CREATE + " -t t --body x\nEOF",
    "xargs -0 sh -c <<'EOF'\n" + CREATE + " -t t --body x\nEOF",
]


@pytest.mark.parametrize("cmd", HEREDOCS_THAT_EXECUTE)
def test_a_heredoc_that_can_execute_is_still_checked(cmd):
    """🔴 The allowlist's other side. Blanking an inert sink's body must not cost
    coverage of a body that really runs — otherwise the mention exemption IS the
    bypass."""
    assert MISSING_MARK in (ev(cmd) or ""), cmd


def test_the_sink_allowlist_is_an_allowlist_not_a_denylist():
    """An attachment the table does not know keeps the body — fail CLOSED."""
    unknown = "some-unknown-wrapper <<'EOF'\n" + CREATE + " -t t --body x\nEOF"
    assert ev(unknown) is not None


def test_an_inert_sink_piped_onward_is_not_inert():
    piped = "cat <<'EOF' | bash\n" + CREATE + " -t t --body x\nEOF"
    plain = "cat <<'EOF'\n" + CREATE + " -t t --body x\nEOF"
    assert ev(piped) is not None
    assert ev(plain) is None


def test_scrubbing_preserves_line_structure():
    text = "cat > /tmp/n.md <<'EOF'\nline one\nline two\nEOF\ntrue"
    out = guard.scrub_inert_heredocs(text)
    assert out.count("\n") == text.count("\n")
    assert "line one" not in out
    assert "EOF" in out


def test_scrubbing_is_a_no_op_without_an_inert_heredoc():
    text = "bash <<'EOF'\nline one\nEOF"
    assert guard.scrub_inert_heredocs(text) == text


# =========================================================================== #
# 3. OTHER SUBCOMMANDS AND NEIGHBOURING VERBS — the non-matches.
# =========================================================================== #
NOT_A_CREATE = [
    GH + " issue comment 12 --body " + q(NO_CC),
    GH + " issue edit 12 --body " + q(NO_CC),
    GH + " issue close 12 --comment " + q(NO_CC),
    GH + " issue reopen 12",
    GH + " issue list",
    GH + " issue list --label bug",
    GH + " issue view 3",
    GH + " issue view 3 --json body",
    GH + " issue status",
    GH + " issue develop 3 --name zach/fix",
    GH + " issue transfer 3 other/repo",
    GH + " issue pin 3",
    GH + " pr create --title t --body " + q(NO_CC),
    GH + " pr comment 4 --body " + q(NO_CC),
    GH + " release create v1 --notes " + q(NO_CC),
    GH + " gist create /tmp/x.md",
    GH + " api repos/o/r/issues",
    GH + " api repos/o/r/issues?state=open",
    GH + " api repos/o/r/issues/3",
    GH + " api repos/o/r/issues/3/comments -f body=" + q(NO_CC),
    GH + " api -X GET repos/o/r/issues",
    GH + " api -X PATCH repos/o/r/issues/3 -f body=" + q(NO_CC),
    GH + " api /repos/o/r/issues/events",
    "curl https://api.github.com/repos/o/r/issues",
    "curl -X GET https://api.github.com/repos/o/r/issues",
    "curl -X POST https://api.github.com/repos/o/r/issues/3/comments -d " + q('{"body":"hi"}'),
    "curl https://api.github.com/repos/o/r/issues/3",
    GH + " pr checks 4",
    GH + " repo view",
]


@pytest.mark.parametrize("cmd", NOT_A_CREATE)
def test_a_non_create_is_allowed(cmd):
    assert ev(cmd) is None, cmd


@pytest.mark.parametrize("cmd", NOT_A_CREATE[:8])
def test_a_non_create_is_allowed_through_the_real_process(cmd):
    assert_allowed(cmd)


@pytest.mark.parametrize("cmd", [
    CREATE + " --help",
    CREATE + " -h",
    GH + " help issue create",
    CREATE + " --help --body " + q(NO_CC),
    CREATE + " --web -t t",
    CREATE + " -w -t t",
    CREATE + " --web --body " + q(NO_CC),
])
def test_a_structurally_exempt_shape_is_allowed(cmd):
    """`--help` and `--web` cannot post from this process — docstring records both
    as known one-flag gaps rather than hiding them."""
    assert ev(cmd) is None, cmd


def test_the_exemption_does_not_reach_curl():
    """🔴 `-w` is curl's `--write-out` and curl posts anyway. The clawgate gate's
    first `is_help_invocation` had exactly this hole."""
    cmd = ("curl -w '%{http_code}' -X POST https://api.github.com/repos/o/r/issues "
           "-d " + q('{"body":"nope"}'))
    assert ev(cmd) is not None


def test_a_flag_value_that_looks_like_help_does_not_exempt():
    """`--title --help` reads `--help` as the title's VALUE."""
    assert ev(CREATE + " --title --help --body " + q(NO_CC)) is not None


# =========================================================================== #
# 4. BODY SOURCES — readable, and the ones that must block.
# =========================================================================== #
def test_a_body_flag_with_a_closing_condition_is_allowed():
    assert_allowed(CREATE + " --title t --body " + q(CC))


def test_a_body_flag_without_one_is_denied():
    assert MISSING_MARK in deny_reason(CREATE + " --title t --body " + q(NO_CC))


def test_the_short_body_flag_is_covered():
    assert MISSING_MARK in (ev(CREATE + " -t t -b " + q(NO_CC)) or "")
    assert ev(CREATE + " -t t -b " + q(CC)) is None


def test_an_equals_spelling_is_covered():
    assert MISSING_MARK in (ev(CREATE + " -t t --body=" + q(NO_CC)) or "")
    assert ev(CREATE + " -t t --body=" + q(CC)) is None


def test_a_body_file_is_read(tmp_path):
    good = tmp_path / "good.md"
    good.write_text(CC)
    bad = tmp_path / "bad.md"
    bad.write_text(NO_CC)
    assert ev(CREATE + " -t t --body-file " + str(good)) is None
    assert MISSING_MARK in (ev(CREATE + " -t t --body-file " + str(bad)) or "")


def test_the_short_body_file_flag_is_covered(tmp_path):
    """🔴 On `gh issue create`, `-F` is `--body-file`; on `gh api` it is
    `--raw-field`. One shared table would read one as the other."""
    good = tmp_path / "good.md"
    good.write_text(CC)
    assert ev(CREATE + " -t t -F " + str(good)) is None
    bad = tmp_path / "bad.md"
    bad.write_text(NO_CC)
    assert MISSING_MARK in (ev(CREATE + " -t t -F " + str(bad)) or "")


def test_a_missing_body_file_blocks_with_the_unreadable_reason(tmp_path):
    missing = tmp_path / "nope.md"
    reason = deny_reason(CREATE + " -t t --body-file " + str(missing))
    assert UNSEEABLE_MARK in reason
    assert REASON_UNREADABLE in reason


def test_an_unreadable_body_file_blocks(tmp_path):
    """A directory is a path that exists and cannot be read as a body."""
    d = tmp_path / "adir"
    d.mkdir()
    assert UNSEEABLE_MARK in deny_reason(CREATE + " -t t --body-file " + str(d))


def test_an_oversized_body_file_blocks_rather_than_reading_it(tmp_path):
    big = tmp_path / "big.md"
    big.write_text(CC + "\n" + ("x" * (BODY_FILE_CAP + 1)))
    reason = deny_reason(CREATE + " -t t --body-file " + str(big))
    assert UNSEEABLE_MARK in reason
    assert MISSING_MARK not in reason


def test_a_stdin_body_blocks_and_names_the_remedy():
    reason = deny_reason(CREATE + " -t t --body-file -")
    assert UNSEEABLE_MARK in reason
    assert REASON_STDIN in reason
    assert "write it with the Write tool first" in reason


@pytest.mark.parametrize("spelling", ["-", "/dev/stdin", "/proc/self/fd/0"])
def test_every_stdin_spelling_blocks(spelling):
    assert REASON_STDIN in (ev(CREATE + " -t t --body-file " + spelling) or "")


def test_a_generator_substitution_blocks():
    reason = deny_reason(CREATE + ' -t t --body "$(generate-spec.sh)"')
    assert UNSEEABLE_MARK in reason
    assert REASON_OPAQUE in reason


def test_a_whole_variable_body_blocks():
    assert REASON_OPAQUE in (ev(CREATE + ' -t t --body "$BODY"') or "")
    assert REASON_OPAQUE in (ev(CREATE + ' -t t --body "${BODY}"') or "")


def test_a_variable_body_file_path_blocks():
    assert REASON_OPAQUE in (ev(CREATE + ' -t t --body-file "$F/x.md"') or "")


@pytest.mark.parametrize("cmd", [
    CREATE + " -t t --body $(generate-spec.sh)",   # UNQUOTED substitution
    CREATE + " -t t --body ''",
    CREATE + ' -t t --body ""',
])
def test_an_empty_argument_is_not_read_as_an_empty_body(cmd):
    """🔴 The shared core LIFTS an unquoted `$( … )` out of its segment, so the
    `--body` argument arrives here as the empty string. Scoring that as a body
    would report "no closing condition" about a body the gate never saw — the
    wrong fact and the most confusing possible message."""
    reason = deny_reason(cmd)
    assert UNSEEABLE_MARK in reason
    assert REASON_NONE in reason
    assert MISSING_MARK not in reason


def test_a_create_with_no_body_at_all_blocks():
    reason = deny_reason(CREATE + " -t t")
    assert UNSEEABLE_MARK in reason
    assert REASON_NONE in reason


@pytest.mark.parametrize("body", [
    "$'## Closing condition\\nthe queue drains to 0'",
    "$'## Acceptance criteria\\nthe suite is green'",
    "$'## Closing condition\\r\\nthe queue drains to 0'",
    "$'## Closing condition\\tthe queue drains\\nChecked by: a query'",
])
def test_an_ansi_c_quoted_body_is_decoded(body):
    """🔴 `shlex` does not implement bash's `$'…'`, so a correctly-specified body
    written that way arrives as ONE line starting `$##` and DENIES. A gate that
    false-positives on correct usage is the gate everyone routes around."""
    assert ev(CREATE + " -t t --body " + body) is None, body


@pytest.mark.parametrize("body", [
    '"' + NO_CC + '\\n## Closing condition\\nit is done"',
    '"## Closing condition\\nthe queue drains to 0"',
    "'" + NO_CC + "\\n## Acceptance criteria\\nshipped'",
])
def test_a_literal_backslash_n_body_is_not_decoded(body):
    """🔴 THE DECODE IS GATED ON `$`, AND THAT GATE IS A GUARD, NOT A DETAIL.

    Decoding unconditionally let ANY body pass by appending about thirty
    characters: bash leaves `"\\n"` as two literal characters, so the issue
    GitHub renders carries no heading on any line. This was recorded in the
    guard's docstring as a "deliberate over-acceptance"; it was a bypass, and the
    docstring now says the opposite because the code does."""
    assert MISSING_MARK in (ev(CREATE + " -t t --body " + body) or ""), body


def test_the_ansi_c_decoding_cannot_manufacture_a_pass():
    """It only ever adds a candidate; a body with no heading still denies."""
    assert ev(CREATE + " -t t --body $'nothing specified\\nhere either'") is not None
    assert guard.escape_expanded("plain text") == "plain text"
    assert guard.escape_expanded("a\\nb") == "a\\nb"
    assert guard.escape_expanded("$a\\nb") == "a\nb"


def test_a_lone_variable_inside_prose_is_not_opaque():
    """🔴 A measured false positive on the sibling gate: an issue body is PROSE,
    so `$PATH` inside it is text, not a hidden value."""
    body = CC + "\nset $PATH correctly first"
    assert ev(CREATE + " -t t --body " + q(body)) is None


def test_a_substitution_after_the_heading_still_passes():
    """🔴 Ordering: a heading PRESENT IN THE LITERAL ARGUMENT wins over opacity.
    Consulting opacity first reports "cannot see the body" about a body that is
    right there."""
    ok = CREATE + " -t t --body \"$(printf '%s' x)" + "\n" + CC + '"'
    assert ev(ok) is None
    # …and the same shape WITHOUT a heading is still unreadable, not "missing".
    hidden = CREATE + " -t t --body \"$(printf '%s' x)\""
    assert REASON_OPAQUE in (ev(hidden) or "")


def test_a_heredoc_body_is_readable():
    good = (CREATE + " -t t --body \"$(cat <<'EOF'\n" + CC + "\nEOF\n)\"")
    bad = (CREATE + " -t t --body \"$(cat <<'EOF'\n" + NO_CC + "\nEOF\n)\"")
    assert ev(good) is None
    assert MISSING_MARK in (ev(bad) or "")


def test_a_tab_stripped_heredoc_body_is_readable():
    """🔴 `<<-` strips leading TABS from body lines too. Stripping only the
    terminator turns a tabbed heading into an indented code line."""
    body = "\n".join("\t" + line for line in CC.splitlines())
    cmd = CREATE + " -t t --body \"$(cat <<-'EOF'\n" + body + "\n\tEOF\n)\""
    assert ev(cmd) is None


# 🔴 THE SHAPE THAT RESCUED EVERY UNSEEABLE BODY. One `cat > /tmp/plan.md
# <<'EOF' … EOF` — the ordinary agent workflow this gate watches — sat on the
# same line, and EVERY fallback read `heredoc_bodies(<the whole line>)`. The
# original test pinned only `--body '<no condition>'`, which is the one shape
# that already worked; the five below all ALLOWED, in direct contradiction of
# the docstring's headline "WHEN THE BODY CANNOT BE SEEN, THIS BLOCKS".
PLAN_HEREDOC = "cat > " + scratch("notes.md") + " <<'EOF'\n" + CC + "\nEOF\n"

UNRELATED_HEREDOC_CASES = [
    (CREATE + " -t t --body " + q(NO_CC), MISSING_MARK),
    (CREATE + " -t t --body-file -", REASON_STDIN),
    (CREATE + ' -t t --body "$(generate-spec.sh)"', REASON_OPAQUE),
    (CREATE + " -t t", REASON_NONE),
    (CREATE + " -t t -b" + NO_CC.replace(" ", "-"), MISSING_MARK),
    (CREATE + " -t t --body-file " + scratch("no-such-file.md"), REASON_UNREADABLE),
]


@pytest.mark.parametrize("tail,mark", UNRELATED_HEREDOC_CASES)
def test_an_unrelated_heredoc_does_not_satisfy_a_create(tail, mark):
    """🔴 A well-specified heredoc written earlier on the line, for a DIFFERENT
    command, must not answer for a create — whatever the reason the create's own
    body is unreadable. The mark asserted is the SPECIFIC reason, so a case
    cannot pass by observing "it blocked"."""
    assert mark in (ev(PLAN_HEREDOC + tail) or ""), tail


# =========================================================================== #
# 🔴 THE SAME-PHYSICAL-LINE HALF — the one-sidedness that let B2 reopen.
#
# Every case in `UNRELATED_HEREDOC_CASES` puts the foreign heredoc on a PRECEDING
# line, and a preceding line is the single arrangement where "the body that
# starts after the newline ending this command" and "the body this command
# opened" are the same bytes. So a walk that attributes a body BY POSITION passes
# that whole table while being exactly wrong about the shape below.
#
# When two commands on ONE physical line each open a heredoc, bash queues the
# bodies back to back in OPERATOR order after the last of them. "The body after
# my newline" then names the FIRST opener's body — the EARLIER command's — while
# the later command's own body starts where that one ended and falls outside
# every segment. The verdicts invert: a junk-bodied create is credited with a
# well-specified body it never opened, and a well-specified create is denied on
# the strength of junk it never opened either. Both directions are pinned.
#
# Measured at 2bd3d1e7 (the parent of the commit adding these): every one of the
# SEVEN cases in this table ALLOWED, and `test_the_same_line_good_heredoc_still_
# allows` DENIED — 8 red, driven. The two cases that were already GREEN at that
# ref are deliberately NOT in this table: they are invariant guards on the fix
# itself, written as their own tests and labelled as such, because a guard the
# bug never violated is not regression coverage and must not be counted as any.
# The fixtures each name their own file under the per-run scratch dir, so no
# leftover path can answer a `--body-file` question for them.
# =========================================================================== #
def _line(first_target, joiner, create_tail, first_body, own_body, op="<<"):
    """One physical line: `cat <<A > <file> <joiner> <create> <tail>`, then the
    queued bodies — A's first, the create's second, exactly as bash orders them.
    """
    pre = "" if op == "<<" else "\t"
    def wrap(s):
        return "\n".join(pre + line for line in s.splitlines())
    return ("cat " + op + "'A' > " + first_target + " " + joiner + " " +
            CREATE + " " + create_tail + "\n" +
            wrap(first_body) + "\n" + pre + "A\n" +
            wrap(own_body) + "\n" + pre + "B\n")


SAME_LINE_HEREDOC_CASES = [
    # `&&` — the shape a real agent types: write the plan, then file the issue.
    ("&&", _line(scratch("sl-and.md"), "&&", "-t t --body-file - " + "<<'B'",
                 CC, NO_CC), MISSING_MARK),
    # `;` — a different separator must not change the attribution.
    (";", _line(scratch("sl-semi.md"), ";", "-t t --body-file - " + "<<'B'",
                CC, NO_CC), MISSING_MARK),
    # A pipe between the first opener and its sink, still two openers on the line.
    ("| tee",
     "cat <<'A' | tee " + scratch("sl-pipe.md") + " && " + CREATE +
     " -t t --body-file - <<'B'\n" + CC + "\nA\n" + NO_CC + "\nB\n",
     MISSING_MARK),
    # No body flag at all: the create's OWN heredoc feeds nothing gh reads, but
    # it is still the operator's text — and it is still not the FIRST body.
    ("no body flag", _line(scratch("sl-noflag.md"), "&&", "-t t <<'B'",
                           CC, NO_CC), MISSING_MARK),
    # `<<-` strips leading tabs from the bodies AND the terminators.
    ("<<- tab stripped",
     _line(scratch("sl-dash.md"), "&&", "-t t --body-file - " + "<<-'B'",
           CC, NO_CC, op="<<-"), MISSING_MARK),
    # CRLF: the reassembled segment must keep the line structure the parser needs.
    ("CRLF", _line(scratch("sl-crlf.md"), "&&", "-t t --body-file - " + "<<'B'",
                   CC, NO_CC).replace("\n", "\r\n"), MISSING_MARK),
    # Three openers: the create's body is the THIRD in the queue, so a walk that
    # takes "the next body" is wrong by two, not by one.
    ("three chained heredocs",
     "cat <<'A' > " + scratch("sl-1.md") + " && cat <<'B' > " +
     scratch("sl-2.md") + " && " + CREATE + " -t t --body-file - <<'C'\n" +
     CC + "\nA\n" + AC + "\nB\n" + NO_CC + "\nC\n", MISSING_MARK),
]


@pytest.mark.parametrize("label,cmd,mark", SAME_LINE_HEREDOC_CASES,
                         ids=[c[0] for c in SAME_LINE_HEREDOC_CASES])
def test_a_same_line_heredoc_belongs_to_the_command_that_opened_it(label, cmd, mark):
    """🔴 A well-specified heredoc opened by a DIFFERENT command on the SAME line
    must not answer for a create — the bodies are queued in operator order, so
    position cannot attribute them and only the `<<` offset can. The mark is the
    SPECIFIC reason, so a case cannot pass by observing "it blocked"."""
    assert mark in (ev(cmd) or ""), cmd


def test_the_same_line_good_heredoc_still_allows():
    """🔴 THE OTHER DIRECTION, and the reason this is not just "block more". With
    the bodies queued the other way round, position-based attribution hands the
    create the FOREIGN junk body and denies a correctly-specified create. A fix
    that only tightened would fail here."""
    good = _line(scratch("sl-ok.md"), "&&", "-t t --body-file - " + "<<'B'",
                 "junk the create never opened", CC)
    assert ev(good) is None, good
    # …and with the create's own body emptied of a condition it denies again,
    # so the ALLOW above is about the body, not about the shape.
    bad = _line(scratch("sl-ok2.md"), "&&", "-t t --body-file - " + "<<'B'",
                "junk the create never opened", NO_CC)
    assert MISSING_MARK in (ev(bad) or ""), bad


def test_a_create_with_its_own_heredoc_is_not_credited_with_an_earlier_line_s():
    """⚠️ AN INVARIANT GUARD — green at 2bd3d1e7, and written because a mutation
    sweep found nothing else that could see it. It is the input that separates a
    segment starting at the create from one starting at byte 0: the create opens
    its OWN heredoc (so re-parsing its segment DOES find an operator), and a
    well-specified heredoc sits on an earlier line. Widen the segment to the whole
    line and the earlier body answers — the literal B2 bug, scored SURVIVED by
    the suite as shipped."""
    cmd = ("cat <<'A' > " + scratch("sl-prev.md") + "\n" + CC + "\nA\n" +
           CREATE + " -t t --body-file - <<'B'\njunk\nB\n")
    assert MISSING_MARK in (ev(cmd) or ""), cmd
    # …and the create's own well-specified heredoc still ALLOWS on that same
    # shape, so the assertion above is about attribution, not about denying more.
    ok = ("cat <<'A' > " + scratch("sl-prev2.md") + "\njunk\nA\n" +
          CREATE + " -t t --body-file - <<'B'\n" + CC + "\nB\n")
    assert ev(ok) is None, ok


def test_a_create_with_no_heredoc_of_its_own_sees_no_body():
    """⚠️ AN INVARIANT GUARD, NOT REGRESSION COVERAGE — green at 2bd3d1e7 too, and
    labelled so nobody counts it. It passed there for a reason that has nothing
    to do with attribution: the widened span carried the foreign BODY but not the
    foreign `<<` OPERATOR, so re-parsing the segment found no heredoc at all. It
    is here because the fix reassembles segment text by hand, and an assembler
    that appended every body rather than this command's would break it.

    The reason must be "no body this gate can read", not "no closing condition" —
    the gate saw nothing; it did not read a body and find it wanting."""
    cmd = ("cat <<'A' > " + scratch("sl-none.md") + " && " + CREATE + " -t t\n" +
           CC + "\nA\n")
    assert REASON_NONE in (ev(cmd) or ""), cmd


def test_the_override_still_reaches_a_command_after_a_heredoc_body():
    """⚠️ ALSO AN INVARIANT GUARD — green at 2bd3d1e7. The walk steps OVER a
    heredoc body; the first word after it is still in command position. Losing
    that makes a legitimate override stop working, which is a permanently-red
    gate — the failure direction nobody reports. It is pinned because the fix
    rewrote the branch that used to re-arm `at_cmd` after a body (mutant M5)."""
    line = ("cat <<'A' > " + scratch("sl-ovr.md") + "\nplan\nA\n" +
            OVERRIDE_ON + " " + CREATE + " -t t -b nope")
    assert ev(line) is None, line
    # …and without the assignment the same line still denies.
    assert MISSING_MARK in (ev(line.replace(OVERRIDE_ON + " ", "")) or "")


def test_a_heredoc_that_writes_the_body_file_still_satisfies_it():
    """🔴 The other side, and why the rescue is matched on the PATH rather than
    deleted: the file does not exist yet when a PreToolUse hook runs, so a
    correctly specified create would otherwise deny."""
    target = scratch("body.md")
    good = ("cat > " + target + " <<'EOF'\n" + CC + "\nEOF\n" +
            CREATE + " -t t --body-file " + target)
    assert ev(good) is None
    # …and the same line whose heredoc writes some OTHER path does not.
    other = ("cat > " + scratch("elsewhere.md") + " <<'EOF'\n" + CC + "\nEOF\n" +
             CREATE + " -t t --body-file " + target)
    assert REASON_UNREADABLE in (ev(other) or "")


def test_an_opaque_body_is_not_rescued_by_a_heredoc_it_does_not_name():
    """🔴 REACHABLE ONLY WHEN THE HEREDOC IS ON THE CREATE'S OWN COMMAND, which is
    why the segment-scoping cases above could not see this guard at all — a
    mutation sweep scored it SURVIVED. `gh` reads stdin for the body ONLY with
    `--body-file -`, so a bare heredoc here feeds nothing; crediting it would let
    `--body "$(gen.sh)"` — the shape the docstring promises to block — pass."""
    cmd = (CREATE + " -t t --body \"$(generate-spec.sh)\" <<'EOF'\n" + CC + "\nEOF")
    assert REASON_OPAQUE in (ev(cmd) or "")
    # …while an argument that really DOES open the heredoc still resolves.
    ok = CREATE + " -t t --body \"$(cat <<'EOF'\n" + CC + "\nEOF\n)\""
    assert ev(ok) is None


def test_an_inert_sink_feeding_a_process_substitution_is_marked_not_inert():
    """🔴 PINNED AT THE FUNCTION, AND HERE IS WHY THAT IS THE HONEST PLACE.

    `cat <<'EOF' > >(bash)` really executes the body, and `_PIPES_ONWARD` — the
    "is this sink's output going somewhere executable" test — matched only
    `[|&;]`, so it scored INERT. No end-to-end command currently discriminates
    that mutant, because `command_segments` splits on the substitution's own parens
    and re-exposes the body anyway. Two independent mechanisms is the point; a
    test that leans on the OTHER one would report coverage this guard does not
    have, so the claim is asserted where it is made."""
    for sink in ("> >(bash)", "> >(sh -)", "< <(bash)"):
        text = "cat <<'EOF' " + sink + "\n" + CREATE + " -t t --body x\nEOF"
        got = guard.heredocs(text)
        assert len(got) == 1, text
        assert got[0].inert is False, text
    plain = "cat <<'EOF' > " + scratch("n.md") + "\n" + CREATE + " -t t --body x\nEOF"
    assert guard.heredocs(plain)[0].inert is True


def test_a_body_file_dash_is_fed_only_by_its_own_commands_heredoc():
    own = CREATE + " -t t --body-file - <<'EOF'\n" + CC + "\nEOF"
    assert ev(own) is None
    foreign = ("cat <<'EOF'\n" + CC + "\nEOF\n" +
               CREATE + " -t t --body-file -")
    assert REASON_STDIN in (ev(foreign) or "")


def test_the_scrub_keeps_the_heredoc_terminator():
    """🔴 Blanking the terminator too leaves the downstream scanner reading an
    UNTERMINATED heredoc, which swallows the rest of the line — measured as a
    correctly-specified create denying with 'no closing condition'."""
    text = "cat > /tmp/n.md <<'EOF'\nbody line\nEOF\ntrue"
    out = guard.scrub_inert_heredocs(text)
    assert "\nEOF\n" in out
    assert "body line" not in out


def test_a_here_string_is_not_read_as_a_heredoc():
    """A here-string has no body and no terminator; reading one as a heredoc would
    swallow the rest of the line as prose the gate must not credit.

    🔴 THE OLD VERSION OF THIS TEST PASSED WITH OR WITHOUT THE `(?<!<)` IT IS
    NAMED FOR — it asserted only "something blocked", and deleting the protection
    changed the REASON, not the verdict. Both halves below move when it goes: the
    parse yields a body, and the create's verdict flips from "cannot see the
    body" to "no closing condition" (about a body it invented)."""
    swallowed = "wc -l <<<word\n" + CC + "\nword\n"
    assert guard.heredoc_bodies(swallowed) == [], (
        "`<<<word` was read as a heredoc opening tag `word`")
    assert REASON_STDIN in (ev(CREATE + " -t t --body-file - <<<" + q(CC)) or "")


def test_an_unparseable_heredoc_attachment_is_not_read_as_inert():
    """🔴 `_attached_command`'s `""` fallback is the ALLOWLIST's fail-CLOSED half,
    and it was untested: flipping the fallback to `"cat"` — i.e. treating an
    unrecognisable attachment as a text sink — left all 251 tests green. It is
    reachable whenever the operator opens the segment or is preceded only by an
    assignment."""
    assert guard._attached_command("<<'EOF'\nx\nEOF", 0) == ""
    assert guard._attached_command("V=1 <<'EOF'\nx\nEOF", 4) == ""
    for cmd in ("true | <<'EOF'\n" + CREATE + " -t t --body x\nEOF",
                "V=1 <<'EOF'\n" + CREATE + " -t t --body x\nEOF"):
        assert MISSING_MARK in (ev(cmd) or ""), cmd


def test_a_process_substitution_sink_does_not_hide_a_create():
    """The end-to-end half of the claim above: a body that really executes stays
    in the invocation scan, while an ordinary file redirect does not."""
    for cmd in ("cat <<'EOF' > >(bash)\n" + CREATE + " -t t --body x\nEOF",
                "cat <<'EOF' > >(sh -)\n" + CREATE + " -t t --body x\nEOF"):
        assert MISSING_MARK in (ev(cmd) or ""), cmd
    assert ev("cat <<'EOF' > " + scratch("n.md") + "\n" + CREATE +
              " -t t --body x\nEOF") is None


# =========================================================================== #
# 5. THE gh api ROUTE.
# =========================================================================== #
def test_gh_api_field_body_is_checked():
    assert MISSING_MARK in (ev(GH + " api repos/o/r/issues -f title=t -f body=" + q(NO_CC)) or "")
    assert ev(GH + " api repos/o/r/issues -f title=t -f body=" + q(CC)) is None


def test_gh_api_raw_field_body_is_checked():
    assert MISSING_MARK in (ev(GH + " api repos/o/r/issues -F body=" + q(NO_CC)) or "")
    assert ev(GH + " api repos/o/r/issues -F body=" + q(CC)) is None


def test_gh_api_only_the_body_field_counts():
    """A title carrying the heading must not satisfy the gate."""
    assert ev(GH + " api repos/o/r/issues -f title=" + q(CC) + " -f body=" + q(NO_CC)) is not None


def test_gh_api_explicit_post_is_a_create():
    assert ev(GH + " api -X POST repos/o/r/issues -f body=" + q(NO_CC)) is not None
    assert ev(GH + " api --method POST repos/o/r/issues -f body=" + q(NO_CC)) is not None


def test_gh_api_a_full_url_is_recognised():
    assert ev(GH + " api https://api.github.com/repos/o/r/issues -f body=" + q(NO_CC)) is not None


def test_gh_api_input_file_is_read(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"title": "t", "body": CC}))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"title": "t", "body": NO_CC}))
    assert ev(GH + " api -X POST repos/o/r/issues --input " + str(good)) is None
    assert MISSING_MARK in (ev(GH + " api -X POST repos/o/r/issues --input " + str(bad)) or "")


def test_gh_api_input_stdin_blocks():
    assert REASON_STDIN in (ev(GH + " api -X POST repos/o/r/issues --input -") or "")


def test_gh_api_field_reading_a_file_is_read(tmp_path):
    f = tmp_path / "b.md"
    f.write_text(CC)
    assert ev(GH + " api repos/o/r/issues -F body=@" + str(f)) is None


# =========================================================================== #
# 6. THE curl ROUTE.
# =========================================================================== #
def test_curl_post_is_checked():
    bad = ('curl -X POST -H "Accept: application/vnd.github+json" '
           "https://api.github.com/repos/o/r/issues -d " + q(json.dumps({"body": NO_CC})))
    good = bad.replace(q(json.dumps({"body": NO_CC})), q(json.dumps({"body": CC})))
    assert MISSING_MARK in (ev(bad) or "")
    assert ev(good) is None


def test_curl_with_data_and_no_method_is_a_create():
    assert ev("curl https://api.github.com/repos/o/r/issues -d " +
              q(json.dumps({"body": NO_CC}))) is not None


def test_curl_data_from_a_file_is_json_parsed(tmp_path):
    """🔴 `-d @file` still has to be JSON-parsed. Handing the raw bytes to a
    line-based detector denies a correctly-specified create, because the heading
    is only there as an escaped \\n."""
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"title": "t", "body": CC}))
    assert ev("curl -X POST https://api.github.com/repos/o/r/issues -d @" + str(f)) is None


def test_curl_with_a_non_json_payload_blocks():
    reason = deny_reason("curl -X POST https://api.github.com/repos/o/r/issues -d 'body=nope'")
    assert UNSEEABLE_MARK in reason


def test_curl_to_another_host_is_still_covered():
    """The path is what identifies the endpoint; GHES lives on another host."""
    assert ev("curl -X POST https://ghe.example.com/api/v3/repos/o/r/issues -d " +
              q(json.dumps({"body": NO_CC}))) is not None


# =========================================================================== #
# 7. COMPOUND AND QUOTED SHAPES.
# =========================================================================== #
COMPOUND_DENIES = [
    "true && " + CREATE + " -t t --body " + q(NO_CC),
    "true; " + CREATE + " -t t --body " + q(NO_CC),
    "true || " + CREATE + " -t t --body " + q(NO_CC),
    "true | " + CREATE + " -t t --body " + q(NO_CC),
    "( " + CREATE + " -t t --body " + q(NO_CC) + " )",
    "git -C /tmp/x log -1 && " + CREATE + " -t t --body " + q(NO_CC),
    'bash -c "' + CREATE + " -t t --body '" + NO_CC + "'\"",
    "sudo " + CREATE + " -t t --body " + q(NO_CC),
    "timeout 60 " + CREATE + " -t t --body " + q(NO_CC),
    "GH_TOKEN=x " + CREATE + " -t t --body " + q(NO_CC),
    "nohup " + CREATE + " -t t --body " + q(NO_CC),
    CREATE + " --body " + q(NO_CC) + " -t t",
    CREATE + " -R owner/repo --body " + q(NO_CC) + " --title t",
    GH + " -R owner/repo issue create --body " + q(NO_CC) + " -t t",
    CREATE + " -t t -l bug -a zach --body " + q(NO_CC),
    CREATE + " -t t --body " + q(NO_CC) + " && echo done",
]


@pytest.mark.parametrize("cmd", COMPOUND_DENIES)
def test_a_compound_shape_is_still_checked(cmd):
    assert MISSING_MARK in (ev(cmd) or ""), cmd


COMPOUND_ALLOWS = [
    "true && " + CREATE + " -t t --body " + q(CC),
    "true; " + CREATE + " -t t --body " + q(CC),
    'bash -c "' + CREATE + " -t t --body '" + CC + "'\"",
    CREATE + " --body " + q(CC) + " -t t -R owner/repo",
    GH + " -R owner/repo issue create --body " + q(CC) + " -t t",
    "sudo " + CREATE + " -t t --body " + q(CC),
]


@pytest.mark.parametrize("cmd", COMPOUND_ALLOWS)
def test_a_compound_shape_with_a_condition_is_allowed(cmd):
    assert ev(cmd) is None, cmd


def _hd(body, tag="EOF"):
    """The `--body "$(cat <<'TAG' … TAG)"` spelling the deny message recommends."""
    return " --body \"$(cat <<'" + tag + "'\n" + body + "\n" + tag + "\n)\""


def test_two_creates_are_judged_together():
    """🔴 A line that files two issues, one specified and one not, must not pass on
    the strength of the good one."""
    cmd = (CREATE + " -t a --body " + q(CC) + " && " +
           CREATE + " -t b --body " + q(NO_CC))
    assert MISSING_MARK in (ev(cmd) or "")
    both = (CREATE + " -t a --body " + q(CC) + " && " +
            CREATE + " -t b --body " + q(AC))
    assert ev(both) is None


@pytest.mark.parametrize("sep", [" && ", " ; ", "\n", " || "])
def test_two_creates_in_the_heredoc_spelling_are_judged_separately(sep):
    """🔴 THE SPELLING THIS GATE'S OWN `_HOW` MESSAGE RECOMMENDS, USED TWICE.

    The plain `--body '…'` pinning above is the case that already worked. In the
    heredoc spelling the shared core LIFTS the `$( … )`, so create #2's `--body`
    arrives EMPTY, the no-body fallback fired, and it was handed BOTH heredocs on
    the line — so the first issue's closing condition let the second through.
    Each create now resolves inside its own command segment."""
    bad = CREATE + " -t a" + _hd(CC) + sep + CREATE + " -t b" + _hd(NO_CC, "EOF2")
    assert MISSING_MARK in (ev(bad) or ""), bad
    # the FIRST one unspecified is caught too — not just the last
    other = CREATE + " -t a" + _hd(NO_CC) + sep + CREATE + " -t b" + _hd(CC, "EOF2")
    assert MISSING_MARK in (ev(other) or ""), other
    good = CREATE + " -t a" + _hd(CC) + sep + CREATE + " -t b" + _hd(AC, "EOF2")
    assert ev(good) is None, good


def test_two_creates_in_the_heredoc_spelling_deny_through_the_real_process():
    bad = (CREATE + " -t a" + _hd(CC) + " && " +
           CREATE + " -t b" + _hd(NO_CC, "EOF2"))
    assert MISSING_MARK in deny_reason(bad)


# =========================================================================== #
# 7b. FLAG TABLES — every spelling the real tools accept.
#
# 🔴 A FLAG TABLE IS A CLAIM ABOUT ANOTHER PROGRAM, so these are checked against
# `gh issue create --help` / `gh api --help` / curl(1) on gh 2.97.0, not against
# what the guard believes.
# =========================================================================== #
@pytest.mark.parametrize("flag", ["--type", "--parent", "--blocked-by",
                                  "--blocking", "--recover"])
def test_a_value_taking_gh_flag_before_the_verb_does_not_hide_the_create(flag):
    """🔴 A value-taking flag MISSING from the table leaves its value in the
    operand list, which breaks the `["issue", "create"]` PREFIX test — one flag,
    one bypass. All five of these were missing."""
    cmd = GH + " " + flag + " somevalue issue create -t t --body " + q(NO_CC)
    assert MISSING_MARK in (ev(cmd) or ""), cmd


@pytest.mark.parametrize("flag", ["--type Bug", "--parent 100",
                                  "--blocked-by 200", "--blocking 300",
                                  "--recover /tmp/r-821.json", "--editor",
                                  "-e"])
def test_a_real_gh_issue_create_flag_does_not_break_a_good_body(flag):
    assert ev(CREATE + " -t t " + flag + " --body " + q(CC)) is None, flag


def test_a_boolean_gh_flag_does_not_eat_the_next_token():
    """🔴 `--editor` (`-e`) is BOOLEAN in gh; listing it as value-taking made it
    swallow whatever followed.

    The third assertion is the one that DISCRIMINATES, and it took a mutation
    sweep to find: with `--editor` back in the value table the first two still
    deny, because `_flag_values` looks the body flag up independently of the
    table. What actually breaks is a flag whose presence is read through the
    table — `--web`, which makes the call structurally exempt. Swallowed, the
    create denies, i.e. a correct call is blocked."""
    assert MISSING_MARK in (ev(CREATE + " --editor --body " + q(NO_CC)) or "")
    assert MISSING_MARK in (ev(CREATE + " -e --body " + q(NO_CC)) or "")
    assert ev(CREATE + " --editor --web -t t") is None


@pytest.mark.parametrize("cmd", [
    CREATE + " -t t -b" + q(CC),
    CREATE + " -t t -b" + q(AC),
])
def test_an_attached_short_body_flag_is_read(cmd):
    """🔴 gh accepts `-b<body>` and `-F<path>`. Reading neither meant a CORRECTLY
    specified create denied with "CANNOT SEE THE BODY" — the most confusing
    message this gate can print, and the kind that gets a gate switched off."""
    assert ev(cmd) is None, cmd


def test_an_attached_short_body_flag_without_a_condition_denies():
    reason = deny_reason(CREATE + " -t t -b" + q(NO_CC))
    assert MISSING_MARK in reason
    assert UNSEEABLE_MARK not in reason


def test_an_attached_short_body_file_flag_is_read(tmp_path):
    good = tmp_path / "good.md"
    good.write_text(CC)
    assert ev(CREATE + " -t t -F" + str(good)) is None
    bad = tmp_path / "bad.md"
    bad.write_text(NO_CC)
    assert MISSING_MARK in (ev(CREATE + " -t t -F" + str(bad)) or "")


CURL_ATTACHED = [
    "curl --request=POST --data=" + q(json.dumps({"body": NO_CC})) +
    " https://api.github.com/repos/o/r/issues",
    "curl --url=https://api.github.com/repos/o/r/issues --data-raw=" +
    q(json.dumps({"body": NO_CC})),
    "curl -X POST https://api.github.com/repos/o/r/issues --json=" +
    q(json.dumps({"body": NO_CC})),
    "curl -XPOST https://api.github.com/repos/o/r/issues -d" +
    q(json.dumps({"body": NO_CC})),
    "curl --request POST --data-binary=" + q(json.dumps({"body": NO_CC})) +
    " https://api.github.com/repos/o/r/issues",
]


@pytest.mark.parametrize("cmd", CURL_ATTACHED)
def test_a_curl_name_equals_value_flag_is_still_classified(cmd):
    """🔴 `_curl_parts` matched method/data/url flags as EXACT TOKENS, so
    `--request=POST --data=…` was not classified as a create AT ALL — the gate
    never ran, rather than running and passing."""
    assert MISSING_MARK in (ev(cmd) or ""), cmd


@pytest.mark.parametrize("cmd", [c.replace(json.dumps({"body": NO_CC}),
                                           json.dumps({"body": CC}))
                                 for c in CURL_ATTACHED])
def test_a_curl_name_equals_value_flag_with_a_condition_is_allowed(cmd):
    assert ev(cmd) is None, cmd


@pytest.mark.parametrize("pair", ["-q -h", "--jq -w", "-H -h", "--cache -w",
                                  "-t -h"])
def test_a_gh_api_flag_value_that_looks_like_help_does_not_exempt(pair):
    """🔴 `is_exempt_invocation` judged a `gh api` argv against the ISSUE flag
    table, so gh api's OWN value flags were not skipped and a VALUE equal to
    `-h`/`-w` exempted the create."""
    cmd = GH + " api -X POST repos/o/r/issues " + pair + " -f body=" + q(NO_CC)
    assert MISSING_MARK in (ev(cmd) or ""), cmd


def test_gh_api_help_is_still_exempt():
    assert ev(GH + " api --help repos/o/r/issues -f body=" + q(NO_CC)) is None


def test_the_at_file_form_belongs_to_field_not_raw_field(tmp_path):
    """🔴 gh documents `@<path>` as a `-F`/`--field` feature ONLY. Applying it to
    `-f`/`--raw-field` read a file gh would send verbatim, AND denied a body that
    merely opens with an @mention as an unreadable path."""
    f = tmp_path / "b.md"
    f.write_text(CC)
    assert ev(GH + " api repos/o/r/issues -F body=@" + str(f)) is None
    assert ev(GH + " api repos/o/r/issues --field body=@" + str(f)) is None
    # `-f` sends the bytes, so the body IS `@<path>` and names no condition.
    assert MISSING_MARK in (ev(GH + " api repos/o/r/issues -f body=@" + str(f)) or "")


def test_a_body_opening_with_an_at_mention_is_read_as_a_body():
    body = "@zach please pick this up\n\n" + CC
    assert ev(GH + " api repos/o/r/issues -f body=" + q(body)) is None
    assert ev(GH + " api repos/o/r/issues --raw-field body=" + q(body)) is None
    assert MISSING_MARK in (
        ev(GH + " api repos/o/r/issues -f body=" + q("@zach have a look")) or "")


def test_a_quoted_verb_pair_is_one_token_and_never_matches():
    assert ev(GH + " " + q("issue create") + " --body " + q(NO_CC)) is None


# =========================================================================== #
# 8. THE OVERRIDE — one spelling, structural not spelled.
# =========================================================================== #
def test_the_inline_override_allows():
    assert_allowed(OVERRIDE_ON + " " + CREATE + " -t t --body " + q(NO_CC))


def test_the_exported_inline_override_allows():
    assert_allowed("export " + OVERRIDE_ON + "; " + CREATE + " -t t --body " + q(NO_CC))


def test_the_environment_override_allows():
    assert_allowed(CREATE + " -t t --body " + q(NO_CC), env={OVERRIDE: "1"})


@pytest.mark.parametrize("value", ["true", "yes", "0", "", "1 ", "01", "TRUE"])
def test_a_near_miss_environment_value_does_not_override(value):
    assert ev(CREATE + " -t t --body " + q(NO_CC), env={OVERRIDE: value}) is not None


@pytest.mark.parametrize("spelling", [
    "=true", "=yes", "=0", "=2", "=1x", "_EXTRA=1",
])
def test_a_near_miss_inline_spelling_does_not_override(spelling):
    cmd = OVERRIDE + spelling + " " + CREATE + " -t t --body " + q(NO_CC)
    assert ev(cmd) is not None, cmd


# 🔴 EVERY PLACE THE OVERRIDE STRING CAN SIT INSIDE A BODY, not just the one
# that happened to work. The shipped regex anchored on `[\n;&|(){}`]`, so a
# newline, a semicolon or a backtick INSIDE A QUOTED BODY read as a command
# boundary and disarmed the gate. Only the mid-line case was pinned, which is
# precisely the case the broken regex got right — a mutant narrowing the anchor
# to `^` survived all 251 tests. The gate's own deny message names this string,
# so an issue ABOUT this guard is the shape that walked it.
OVERRIDE_IN_PROSE = [
    NO_CC + "\nwe considered " + OVERRIDE_ON + " but did not use it",   # mid-line
    NO_CC + "\n" + OVERRIDE_ON + "\nis the escape hatch",               # own line
    NO_CC + " ; " + OVERRIDE_ON + " was considered",                    # after `;`
    NO_CC + " `" + OVERRIDE_ON + "` in a code span",                    # backtick
    NO_CC + " (" + OVERRIDE_ON + ") in parens",
    NO_CC + " | " + OVERRIDE_ON + " after a pipe character",
    NO_CC + " & " + OVERRIDE_ON + " after an ampersand",
    "## Notes\n" + OVERRIDE_ON + "\n## Nothing else",
]


@pytest.mark.parametrize("body", OVERRIDE_IN_PROSE)
def test_the_override_quoted_inside_a_body_does_not_disarm_the_gate(body):
    """🔴 A guard you can switch off by naming it in prose is not a guard."""
    assert MISSING_MARK in (ev(CREATE + " -t t --body " + q(body)) or ""), body


@pytest.mark.parametrize("body", OVERRIDE_IN_PROSE[:4])
def test_the_override_in_prose_does_not_disarm_the_real_process(body):
    assert deny_reason(CREATE + " -t t --body " + q(body))


def test_the_override_inside_a_heredoc_body_does_not_disarm_the_gate():
    cmd = (CREATE + " -t t --body \"$(cat <<'EOF'\n" + NO_CC + "\n" +
           OVERRIDE_ON + "\nEOF\n)\"")
    assert MISSING_MARK in (ev(cmd) or "")


def test_the_override_inside_a_substitution_does_not_disarm_the_gate():
    """🔴 An assignment inside `$( … )` sets a SUBSHELL's environment, so it can
    never reach the `gh` process this gate is judging. Crediting it would be the
    backtick hole under a second spelling."""
    cmd = ("echo \"$(" + OVERRIDE_ON + " true)\" && " +
           CREATE + " -t t --body " + q(NO_CC))
    assert MISSING_MARK in (ev(cmd) or "")


@pytest.mark.parametrize("cmd", [
    OVERRIDE_ON + " " + CREATE + " -t t --body " + q(NO_CC),
    "export " + OVERRIDE_ON + "; " + CREATE + " -t t --body " + q(NO_CC),
    "true && " + OVERRIDE_ON + " " + CREATE + " -t t --body " + q(NO_CC),
    "true; " + OVERRIDE_ON + " " + CREATE + " -t t --body " + q(NO_CC),
    OVERRIDE_ON + " GH_TOKEN=x " + CREATE + " -t t --body " + q(NO_CC),
    "GH_TOKEN=x " + OVERRIDE_ON + " " + CREATE + " -t t --body " + q(NO_CC),
])
def test_a_real_command_position_override_still_works(cmd):
    """🔴 The other half. Tightening the override until the documented escape
    hatch stops working would make this a permanently-red gate, which is worse
    than no gate."""
    assert ev(cmd) is None, cmd


def test_the_override_is_named_in_both_deny_messages():
    assert OVERRIDE_ON in deny_reason(CREATE + " -t t --body " + q(NO_CC))
    assert OVERRIDE_ON in deny_reason(CREATE + " -t t --body-file -")


# =========================================================================== #
# 9. THE MESSAGES — they must carry their own limits and point somewhere real.
# =========================================================================== #
def test_the_definition_file_exists():
    """A router pointing at nothing is worse than no router."""
    assert DEFINITION.is_file(), DEFINITION


@pytest.mark.parametrize("cmd", [
    CREATE + " -t t --body " + q(NO_CC),
    CREATE + " -t t --body-file -",
    CREATE + " -t t",
])
def test_every_deny_names_the_definition_site(cmd):
    reason = deny_reason(cmd)
    assert DEPLOYED_DEF in reason
    assert REPO_DEF in reason


def test_the_missing_verdict_states_what_it_cannot_check():
    """🔴 Measured: in the briefed arm all 10 issues carried the heading and most
    wrote the remedy under it. A message that reads as if the gate verified the
    semantics would be claiming coverage it does not have. Asserted on the
    MISSING verdict, which is the one that follows an actual check — the
    unseeable verdict follows no check at all and says so in its own words."""
    reason = deny_reason(CREATE + " -t t --body " + q(NO_CC))
    assert LIMIT_MARK in reason
    assert "a floor, not a verdict" in reason


def test_the_unseeable_verdict_names_the_readable_shapes():
    reason = deny_reason(CREATE + " -t t --body-file -")
    assert LIMIT_MARK not in reason
    assert "--body-file <a real path>" in reason
    assert "a heredoc IS readable" in reason


def test_the_two_verdicts_are_distinct():
    missing = deny_reason(CREATE + " -t t --body " + q(NO_CC))
    unseeable = deny_reason(CREATE + " -t t --body-file -")
    assert MISSING_MARK in missing and UNSEEABLE_MARK not in missing
    assert UNSEEABLE_MARK in unseeable and MISSING_MARK not in unseeable


def test_the_missing_message_shows_a_template_and_names_both_headings():
    reason = deny_reason(CREATE + " -t t --body " + q(NO_CC))
    assert "## Closing condition" in reason
    assert "## Acceptance criteria" in reason


# 🔴 NOT ASSERTED HERE: that the guard stays a POINTER at the closing-condition
# definition rather than restating it. `scripts/tests/test_closing_condition_
# single_source.py` already owns that claim REPO-WIDE, and its corpus covers
# `scripts/**.py`, so the new guard is in scope automatically — one rule, one
# place.
#
# 🔴 And a duplicate here would not merely be redundant, it would be a
# SELF-INFLICTED VIOLATION: asserting the sentences are absent means SPELLING
# them, and the owning module counts occurrences across the tree. Measured — an
# earlier draft of this file did exactly that and turned that module's two
# relationship tests red, which is why it excludes ITSELF and says so in its own
# docstring.


# =========================================================================== #
# 10. THE I/O CONTRACT, through a real process.
# =========================================================================== #
@pytest.mark.parametrize("raw", [
    "",
    "not json",
    "[]",
    "null",
    '{"tool_name": "Bash"}',
    '{"tool_name": "Bash", "tool_input": null}',
    '{"tool_name": "Bash", "tool_input": {}}',
    '{"tool_name": "Bash", "tool_input": {"command": null}}',
    '{"tool_name": "Bash", "tool_input": {"command": ""}}',
    '{"tool_name": "Bash", "tool_input": {"command": 12}}',
    '{"tool_input": {"command": "' + CREATE + '"}}',
])
def test_malformed_input_exits_zero_and_says_nothing(raw):
    rc, out = verdict(None, raw=raw)
    assert rc == 0
    assert out is None


@pytest.mark.parametrize("tool", ["Write", "Edit", "Read", "Task", "WebFetch"])
def test_a_non_bash_tool_is_ignored(tool):
    rc, out = verdict(CREATE + " -t t --body " + q(NO_CC), tool_name=tool)
    assert out is None


def test_the_prefilter_returns_before_any_parse():
    assert guard.PREFILTER.search("ls -la") is None
    assert guard.PREFILTER.search(CREATE) is not None
    assert guard.PREFILTER.search("curl https://x/y/issues") is not None


def test_an_unrelated_command_is_allowed():
    assert_allowed("kubectl get pods -n monitoring")


POISON = "poisoned-core-8f21"


def _poisoned_hook(tmp_path):
    """A COPY of the hook beside a `guard_core.py` that raises on import.

    The hook puts its OWN directory first on `sys.path` before importing the
    shared core, so this copy imports the poison and never the real module.
    """
    sandbox = tmp_path / "hooks"
    sandbox.mkdir()
    copy = sandbox / HOOK_BASENAME
    copy.write_text(Path(HOOK).read_text(encoding="utf-8"), encoding="utf-8")
    (sandbox / "guard_core.py").write_text(
        "raise RuntimeError(" + repr(POISON) + ")\n", encoding="utf-8")
    return str(copy)


def test_the_crash_path_denies_a_create_shape(tmp_path):
    """🔴 A crash must not become a silent allow — DRIVEN, not asserted around.

    This test used to run neither `main()` nor the crash path: it asserted
    `pytest.raises` on a local stub, a `re.search`, a substring of `crash_text`,
    and `assert payload` (truthiness of a non-empty JSON string, vacuous). An
    `if False:` mutant on the crash branch survived the WHOLE suite, so the
    fail-closed backstop for the entire hook was unexercised. Its own comment
    claimed driving a real import failure "is not possible from here"; it is —
    copy the hook next to a `guard_core.py` that raises, and run the process.
    """
    rc, out = verdict(CREATE + " -t t --body " + q(NO_CC),
                      hook=_poisoned_hook(tmp_path))
    reason = reason_of(out)
    assert rc == 0
    assert "crashed while checking this command" in reason
    assert POISON in reason, "the crash message must carry the real exception"
    assert OVERRIDE_ON in reason


def test_the_crash_path_still_allows_a_non_create(tmp_path):
    """🔴 The negative control for the test above. A blanket deny-on-crash would
    block `gh pr checks` on an unrelated bug, which is why the fallback is scoped
    by a pure regex — and a test that only ever watches it DENY cannot tell a
    scoped fallback from a blanket one."""
    rc, out = verdict("gh pr checks 4", hook=_poisoned_hook(tmp_path))
    assert rc == 0
    assert out is None


def test_the_crash_path_respects_the_override(tmp_path):
    rc, out = verdict(OVERRIDE_ON + " " + CREATE + " -t t --body " + q(NO_CC),
                      hook=_poisoned_hook(tmp_path))
    assert rc == 0
    assert out is None


def test_the_crash_fallback_respects_the_override():
    assert guard.override_requested(OVERRIDE_ON + " " + CREATE, {}) is True
    assert guard.override_requested(CREATE, {OVERRIDE: "1"}) is True
    assert guard.override_requested(CREATE, {OVERRIDE: "true"}) is False


# =========================================================================== #
# 11. THE WIRING SEAM — a hook that ships unregistered is inert (#452).
# =========================================================================== #
def test_home_nix_deploys_the_hook():
    text = HOME_NIX.read_text(encoding="utf-8")
    assert '.claude/hooks/' + HOOK_BASENAME + '"' in text, (
        "nix/home.nix has no home.file entry for the guard, so a switch would "
        "report success with the hook absent")


def test_the_registrar_registers_it_on_pretooluse_bash():
    text = REGISTRAR.read_text(encoding="utf-8")
    assert '"' + HOOK_BASENAME + '"' in text, (
        "the guard is not in MANAGED_HOOK_SCRIPTS, so its interpreter is never "
        "pinned to an absolute store path")
    marker = '~/.claude/hooks/' + HOOK_BASENAME
    assert marker in text, "the guard is in no command table"
    pre = text.split("PRE_BASH_CMDS", 1)
    assert len(pre) == 2, "PRE_BASH_CMDS is gone from the registrar"
    tail = pre[1].split("SINGLE_EVENT_CMDS", 1)[0]
    assert marker in tail, (
        "the guard is registered somewhere, but not on PreToolUse(Bash) — it "
        "would never see a command")


def test_the_rule_this_gate_enforces_still_exists():
    """The gate is downstream of a rule; if the rule is gone the gate is orphaned.

    🔴 A TWO-WORD SUBSTRING ANYWHERE IN THE FILE WAS TOO WEAK: any other bullet
    mentioning the phrase satisfied it, so the test could not tell "the rule
    exists" from "the words exist". Pinned instead as a RELATIONSHIP — the
    requirement lives in the proactivity gate's `Out of scope` branch, and names
    BOTH halves (what ends the item, and who or what checks it), which is exactly
    what this hook asks a body to carry."""
    text = " ".join(RULES.read_text(encoding="utf-8").split())
    assert "Out of scope" in text, "the proactivity gate's branch is gone"
    branch = text.split("Out of scope", 1)[1].split("**Fork**", 1)[0]
    assert "CLOSING CONDITION that ends it and who or what checks it" in branch, (
        "the Out-of-scope branch no longer requires a closing condition; this "
        "gate is enforcing a rule that has moved or been dropped")


# =========================================================================== #
# 12. CONTROLS ON THIS FILE ITSELF.
# =========================================================================== #
def test_control_a_case_that_must_deny():
    """Positive control: if this passes as an allow, the harness is wired to
    nothing and every assertion above is vacuous."""
    rc, out = verdict(CREATE + " --title t --body " + q(NO_CC))
    assert out is not None, "the harness observed no deny on a case that must deny"
    assert rc == 0


def test_control_a_case_that_must_allow():
    """Negative control: a guard nobody has watched ALLOW is as unproven as one
    nobody has watched deny."""
    rc, out = verdict(CREATE + " --title t --body " + q(CC))
    assert out is None, "the harness observed a deny on a case that must allow"
    assert rc == 0


def test_control_the_detector_table_is_non_empty():
    assert len(ACCEPTS) >= 15
    assert len(REJECTS) >= 15
    assert len(MENTIONS) >= 12
    assert len(NOT_A_CREATE) >= 25
    assert len(REALISTIC_ALLOWS) >= 30
    assert len(OVERRIDE_IN_PROSE) >= 6


# =========================================================================== #
# 13. 🔴 THE ALLOW DIRECTION, AS ONE BATTERY.
#
# The tightenings this file's newer cases pin all move in the DENY direction, and
# a gate that starts false-positiving gets switched off — at which point it
# protects nothing. This is the standing regression set for the other half: every
# entry is a shape a person or an agent would really type, and every one of them
# must pass SILENTLY. It is deliberately redundant with the sections above,
# because a battery you can run as one name is a battery that actually gets run
# after a fix round.
# =========================================================================== #
REALISTIC_ALLOWS = [
    # the plain spellings
    CREATE + " --title t --body " + q(CC),
    CREATE + " -t t -b " + q(AC),
    CREATE + " -t t --body=" + q(CC),
    CREATE + " -t t -b" + q(CC),
    CREATE + " -t t --body " + q(CC + "\n\n## Notes\ncontext, links, a repro"),
    CREATE + " -t t --body " + q("## Summary\nprose first\n\n" + CC),
    CREATE + " -t t --body $'## Closing condition\\nthe queue drains to 0'",
    # the heredoc spellings the deny message recommends
    CREATE + " -t t --body \"$(cat <<'EOF'\n" + CC + "\nEOF\n)\"",
    CREATE + " -t t --body \"$(cat <<'EOF'\n" + AC + "\nEOF\n)\"",
    CREATE + " -t t --body-file - <<'EOF'\n" + CC + "\nEOF",
    "cat > " + scratch("allow.md") + " <<'EOF'\n" + CC + "\nEOF\n" +
    CREATE + " -t t --body-file " + scratch("allow.md"),
    # bodies that carry shell-ish prose
    CREATE + " -t t --body " + q(CC + "\nrun `make test` and set $PATH first"),
    CREATE + " -t t --body " + q(CC + "\nsee `gh issue list; gh pr list`"),
    CREATE + " -t t --body " + q(CC + "\n```\nkubectl get pods -n monitoring\n```"),
    CREATE + " -t t --body " + q(CC + "\ncost is $5 & rising | fast"),
    CREATE + " -t t --body " + q("## Acceptance criteria\n- [ ] a\n- [ ] b"),
    # flags around the body
    CREATE + " -t t -l bug -a zach -m sprint-3 --body " + q(CC),
    CREATE + " -t t --type Bug --parent 100 --body " + q(CC),
    CREATE + " -t t --blocked-by 200,201 --blocking 300 --body " + q(CC),
    CREATE + " -R owner/repo -t t --body " + q(CC),
    GH + " -R owner/repo issue create -t t --body " + q(CC),
    CREATE + " -t t --project Roadmap -T bug-report --body " + q(CC),
    # compounds and wrappers
    "true && " + CREATE + " -t t --body " + q(CC),
    "git -C /tmp/x log -1 && " + CREATE + " -t t --body " + q(CC),
    'bash -c "' + CREATE + " -t t --body '" + CC + "'\"",
    "sudo " + CREATE + " -t t --body " + q(CC),
    "timeout 60 " + CREATE + " -t t --body " + q(CC),
    "GH_TOKEN=x " + CREATE + " -t t --body " + q(CC),
    CREATE + " -t a --body " + q(CC) + " && " + CREATE + " -t b --body " + q(AC),
    # the API routes
    GH + " api repos/o/r/issues -f title=t -f body=" + q(CC),
    GH + " api -X POST repos/o/r/issues -F body=" + q(AC),
    "curl -X POST -H 'Accept: application/vnd.github+json' "
    "https://api.github.com/repos/o/r/issues -d " + q(json.dumps({"body": CC})),
    "curl --request=POST --data=" + q(json.dumps({"body": AC})) +
    " https://api.github.com/repos/o/r/issues",
    # the documented escape hatches
    OVERRIDE_ON + " " + CREATE + " -t t --body " + q(NO_CC),
    CREATE + " --web -t t",
    CREATE + " --help",
]


@pytest.mark.parametrize("cmd", REALISTIC_ALLOWS)
def test_a_realistic_correct_call_is_allowed(cmd):
    assert ev(cmd) is None, cmd


@pytest.mark.parametrize("cmd", REALISTIC_ALLOWS[:12])
def test_a_realistic_correct_call_is_allowed_through_the_real_process(cmd):
    assert_allowed(cmd)


@pytest.mark.parametrize("cmd", [
    "then " + CREATE + " -t t --body " + q(NO_CC),
    "do " + CREATE + " -t t --body " + q(NO_CC),
    "else " + CREATE + " -t t --body " + q(NO_CC),
    "if true; then " + CREATE + " -t t --body " + q(NO_CC) + "; fi",
    "while read x; do " + CREATE + " -t t --body " + q(NO_CC) + "; done",
])
def test_a_shell_keyword_prefix_is_a_KNOWN_UNCOVERED_ROUTE(cmd):
    """🔴 PINNED AS A GAP, NOT LEFT FOR SOMEONE TO REDISCOVER — and PRE-EXISTING,
    identical on the pre-fix hook.

    `guard_core._peel_variants` peels wrappers and `VAR=` assignments but not
    shell keywords, so a create inside a compound statement has argv[0] == `then`
    and is not classified as `gh` at all. Fixing it means widening the shared
    peeler, which `bash-guard.py` also depends on — its own change, its own blast
    radius. This is the docstring's NOT-COVERED entry made machine-readable, so
    the claim cannot rot: if the peeler is widened, these go red and the
    docstring entry has to go with them."""
    assert ev(cmd) is None, cmd


@pytest.mark.parametrize("word", ["time", "command", "exec", "eval"])
def test_a_non_keyword_prefix_does_still_reach_the_gate(word):
    """The boundary of the gap above — asserted, because a NOT-COVERED entry that
    over-claims is as misleading as one that under-claims."""
    assert MISSING_MARK in (ev(word + " " + CREATE + " -t t --body " + q(NO_CC)) or "")


def test_a_body_quoting_a_heredoc_operator_is_a_KNOWN_FALSE_POSITIVE():
    """🔴 PINNED AS A LIMIT, NOT LEFT INVISIBLE — and it is PRE-EXISTING, byte for
    byte identical on the pre-fix hook.

    `heredocs()` is deliberately quote-BLIND (it has to find body prose inside a
    `"$(cat <<'EOF' … )"`), so a `<<'EOF'` written inside a fenced code block in
    an issue body looks like a real operator. `scrub_inert_heredocs` then blanks
    those bytes for the invocation scan, which unbalances the argument's quote,
    and the fallback tokeniser hands `--body` a fragment. The create denies with
    "no closing condition" about a body that has one.

    Fixing it means teaching the SCRUB about quoting while leaving body
    resolution blind — a change to the mechanism the whole mention-is-not-an-
    invocation requirement rests on, so it is recorded here rather than smuggled
    into a fix round for something else. If you fix it, this test goes red: make
    it an ALLOW assertion and move the case into REALISTIC_ALLOWS."""
    body = CC + "\n```\ncat <<'EOF' > x\nEOF\n```"
    assert MISSING_MARK in (ev(CREATE + " -t t --body " + q(body)) or "")
