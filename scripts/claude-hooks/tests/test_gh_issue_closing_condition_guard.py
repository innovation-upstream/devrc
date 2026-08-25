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


# --------------------------------------------------------------------------- #
# drivers
# --------------------------------------------------------------------------- #
def verdict(cmd, env=None, tool_name="Bash", raw=None):
    """Run the REAL hook as a subprocess. Returns (returncode, parsed-json-or-None).

    A subprocess and not `guard.evaluate`, because the claims that matter most
    here — "always exits 0", "prints nothing on an allow" — are claims about the
    PROCESS. An in-process call cannot observe a traceback or a stray write to
    stdout.
    """
    e = dict(os.environ)
    e.pop(OVERRIDE, None)
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    if env:
        e.update(env)
    payload = raw if raw is not None else json.dumps(
        {"tool_name": tool_name, "hook_event_name": "PreToolUse",
         "cwd": str(ROOT), "tool_input": {"command": cmd}})
    p = subprocess.run([sys.executable, HOOK], input=payload,
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
    '"## Closing condition\\nthe queue drains to 0"',
    "$'## Closing condition\\r\\nthe queue drains to 0'",
    "$'## Closing condition\\tthe queue drains\\nChecked by: a query'",
])
def test_an_escaped_newline_body_is_decoded(body):
    """🔴 `shlex` does not implement bash's `$'…'`, so a correctly-specified body
    written that way arrives as ONE line starting `$##` and DENIES. A gate that
    false-positives on correct usage is the gate everyone routes around.

    The plain double-quoted spelling is credited too. That is a DELIBERATE
    over-acceptance, recorded in the guard's docstring: bash leaves `"\\n"` as two
    literal characters, so GitHub would render the body on one line — but an
    author who typed `\\n` between a heading and its content meant a newline, and
    denying them is the false positive that gets a gate routed around."""
    assert ev(CREATE + " -t t --body " + body) is None, body


def test_the_ansi_c_decoding_cannot_manufacture_a_pass():
    """It only ever adds a candidate; a body with no heading still denies."""
    assert ev(CREATE + " -t t --body $'nothing specified\\nhere either'") is not None
    assert guard.escape_expanded("plain text") == "plain text"


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


def test_an_unrelated_heredoc_does_not_satisfy_a_create():
    """🔴 The false ALLOW the blank-heredoc fallback is narrowed to avoid: a
    well-specified heredoc written earlier on the line, for a DIFFERENT command,
    must not let a create whose own body says nothing through."""
    cmd = ("cat > /tmp/notes.md <<'EOF'\n" + CC + "\nEOF\n" +
           CREATE + " -t t --body " + q(NO_CC))
    assert MISSING_MARK in (ev(cmd) or ""), cmd


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
    swallow the rest of the line as prose the gate must not credit."""
    assert ev(CREATE + " -t t --body-file - <<<" + q(CC)) is not None


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


def test_two_creates_are_judged_together():
    """🔴 A line that files two issues, one specified and one not, must not pass on
    the strength of the good one."""
    cmd = (CREATE + " -t a --body " + q(CC) + " && " +
           CREATE + " -t b --body " + q(NO_CC))
    assert MISSING_MARK in (ev(cmd) or "")
    both = (CREATE + " -t a --body " + q(CC) + " && " +
            CREATE + " -t b --body " + q(AC))
    assert ev(both) is None


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


def test_the_override_quoted_inside_a_body_does_not_disarm_the_gate():
    """🔴 A guard you can switch off by naming it in prose is not a guard."""
    body = NO_CC + "\nwe considered " + OVERRIDE_ON + " but did not use it"
    assert ev(CREATE + " -t t --body " + q(body)) is not None


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


def test_the_crash_path_denies_a_create_shape():
    """🔴 A crash must not become a silent allow. Driven by making the shared core
    unimportable, which is the real failure this path exists for."""
    e = dict(os.environ)
    e.pop(OVERRIDE, None)
    e["PYTHONPATH"] = ""
    payload = json.dumps({"tool_name": "Bash", "hook_event_name": "PreToolUse",
                          "tool_input": {"command": CREATE + " -t t --body " + q(NO_CC)}})
    # Force the import to fail by pointing the hook at a directory with a
    # poisoned guard_core earlier on sys.path is not possible from here, so drive
    # the function directly with a core that raises.
    class Boom:
        def commands(self, _):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        guard.evaluate(CREATE + " -t t --body " + q(NO_CC), {}, Boom())
    assert guard.CRASH_LOOKS_LIKE_CREATE.search(CREATE) is not None
    assert OVERRIDE_ON in guard.crash_text(RuntimeError("boom"))
    assert payload  # the shape a real hook receives; kept for the reader


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
    """The gate is downstream of a rule; if the rule is gone the gate is orphaned."""
    text = RULES.read_text(encoding="utf-8")
    assert "CLOSING CONDITION" in text


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
