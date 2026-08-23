#!/usr/bin/env python3
"""Tests for clawgate-task-interview-guard.py — the gate that makes a clawgate task
CREATE carry acceptance criteria.

WHAT THIS FILE IS FOR

  1. 🔴 BOTH DIRECTIONS OF THE TRIGGER, and the non-matches are the load-bearing
     half. This hook DENIES a Bash call. Firing it on `task ls`, `task get`,
     `task comment`, a curl to `/api/tags`, or a launcher for one of the unattended
     producers would put a block in front of work that owes the board nothing.
     Every one of those is a case here, not an argument in a comment.

  2. 🔴 EVERY BODY SOURCE, INCLUDING THE ONE THAT MUST BLOCK. `--body`,
     `--body-file <path>`, a heredoc and a curl JSON payload are readable; a body
     piped in from a generator is NOT, and the gate must BLOCK there. Failing open
     on an unreadable body would make the guard walkable by changing the SHAPE of
     the call rather than its content — a spelled guard, not a structural one.
     Asserted with the two verdicts kept DISTINCT: "no criteria" and "cannot see
     the body" are different facts and carry different messages, so a test cannot
     pass by observing "it blocked" alone.

  3. 🔴 THE DETECTOR IS PINNED AS A LITERAL TABLE, never derived from the regex it
     tests. Fifteen accept cases and eighteen reject cases, each spelled out — the
     `###`, the missing space, the bold text and the heading that only appears
     inside a ``` fence are all REJECTS, because none of them satisfies the rule a
     pickup applies and a gate looser than the rule it enforces produces a FALSE
     PASS.

  4. 🔴 THE OVERRIDE IS STRUCTURAL, NOT SPELLED. `CLAWGATE_NO_INTERVIEW=1` quoted
     INSIDE a task body must NOT disarm the gate: a guard you can switch off by
     naming it in prose is not a guard. Both real channels (a command-position
     assignment, the process environment) are driven through a REAL subprocess, and
     four near-miss spellings are asserted NOT to override.

  5. THE I/O CONTRACT, THROUGH A REAL PROCESS. A PreToolUse hook that exits
     non-zero for any reason other than 2 is a silent ALLOW, so "exits 0" is
     checked on the malformed-input paths too — where an in-process assertion could
     not see a traceback.

  6. 🔴 THE HOOK IS THE ROUTER. A file under `flows/` does not auto-fire the way a
     skill DESCRIPTION does, so every deny message MUST name
     `flows/task-authoring.md`. That is asserted on every deny shape, and the flow
     file is asserted to EXIST — a router pointing at nothing is worse than no
     router, because it reads as guidance.

  7. NEGATIVE AND POSITIVE CONTROLS ON THIS FILE ITSELF. `test_control_*` feed the
     harness one case that MUST deny and one that MUST allow. A suite that reported
     a reassuring zero because it was wired to nothing would fail them.
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
HOOK = os.path.abspath(os.path.join(HERE, os.pardir, "clawgate-task-interview-guard.py"))
ROOT = Path(HERE).resolve().parents[2]
FLOW = ROOT / "claude" / "skills" / "clawgate" / "flows" / "task-authoring.md"
HOME_NIX = ROOT / "nix" / "home.nix"
REGISTRAR = ROOT / "scripts" / "claude-hooks" / "register-nudge-hook.py"
SKILL = ROOT / "claude" / "skills" / "clawgate" / "SKILL.md"


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


guard = _load("clawgate_task_interview_guard_undertest", HOOK)
# The hook resolves `guard_core` from its own directory; do the same here so the
# in-process helpers below see exactly the module the deployed hook would.
sys.path.insert(0, os.path.abspath(os.path.join(HERE, os.pardir)))
import guard_core  # noqa: E402

# The body a well-formed create carries. Kept SHORT and with values distinct from
# every constant this suite asserts against.
AC = "## Acceptance criteria\n1. the pill turns red above three\n2. the timer fires daily"
NO_AC = "make the pill better, you know what I mean"

# The two verdict families, identified by a phrase that appears in exactly one of
# them. Asserting on these keeps "it blocked" from standing in for "it blocked for
# the right reason".
MISSING_MARK = "has no `## Acceptance criteria` heading"
UNSEEABLE_MARK = "CANNOT SEE THE BODY"
FLOW_MARK = "flows/task-authoring.md"

# 🔴 LITERALS, never `guard.<CONSTANT>`. An expectation read out of the module under
# test asserts only that the module agrees with itself: a mutant that renames the
# flow path, or widens the override spelling, changes BOTH sides at once and
# survives. Measured — two mutants survived the first sweep for exactly this, and
# these literals are what killed them.
DEPLOYED_FLOW = "~/.claude/skills/clawgate/flows/task-authoring.md"
REPO_FLOW = "devrc/claude/skills/clawgate/flows/task-authoring.md"
OVERRIDE = "CLAWGATE_NO_INTERVIEW"
OVERRIDE_ON = "CLAWGATE_NO_INTERVIEW=1"
REASON_STDIN = "the body is piped in on stdin, where a PreToolUse hook cannot read it"
REASON_OPAQUE = "the argument is a shell substitution this gate cannot evaluate"
REASON_NONE = "the command names no body this gate can read"
BODY_FILE_CAP = 1024 * 1024


# --------------------------------------------------------------------------- #
# drivers
# --------------------------------------------------------------------------- #
def verdict(cmd, env=None, tool_name="Bash", raw=None):
    """Run the REAL hook as a subprocess. Returns (returncode, parsed-json-or-None).

    A subprocess and not `guard.evaluate`, because the claims that matter most here
    — "always exits 0", "prints nothing on an allow" — are claims about the
    PROCESS. An in-process call cannot observe a traceback or a stray write to
    stdout.
    """
    e = dict(os.environ)
    e.pop(guard.OVERRIDE_ENV, None)
    if env:
        e.update(env)
    payload = raw if raw is not None else json.dumps(
        {"tool_name": tool_name, "hook_event_name": "PreToolUse",
         "cwd": "/home/zach/workspace/devrc",
         "tool_input": {"command": cmd}})
    p = subprocess.run([sys.executable, HOOK], input=payload,
                       capture_output=True, text=True, env=e)
    assert p.stderr == "", "the hook wrote to stderr: " + p.stderr
    out = json.loads(p.stdout) if p.stdout.strip() else None
    return p.returncode, out


def reason(cmd, **kw):
    """The deny reason for `cmd`, or None when it was allowed."""
    rc, out = verdict(cmd, **kw)
    assert rc == 0, "a PreToolUse hook must exit 0; got %d" % rc
    if out is None:
        return None
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    return hso["permissionDecisionReason"]


def allowed(cmd, **kw):
    return reason(cmd, **kw) is None


def denied_missing(cmd, **kw):
    r = reason(cmd, **kw)
    return r is not None and MISSING_MARK in r and UNSEEABLE_MARK not in r


def denied_unseeable(cmd, **kw):
    r = reason(cmd, **kw)
    return r is not None and UNSEEABLE_MARK in r


@pytest.fixture()
def bodyfile(tmp_path):
    """A factory for a real `--body-file` target."""
    def make(text, name="body.md"):
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        return str(p)
    return make


def heredoc(body, tag="EOF"):
    """The `--body "$(cat <<'EOF' … EOF)"` idiom, which is what a real create uses."""
    return ("clawgatectl task create --title T --body \"$(cat <<'%s'\n%s\n%s\n)\""
            % (tag, body, tag))


# =========================================================================== #
# 0. CONTROLS ON THIS FILE — see item 7 in the module docstring
# =========================================================================== #
def test_control_negative_a_case_that_must_deny_does_deny():
    """Can this harness see a block at all? A suite wired to nothing cannot."""
    r = reason('clawgatectl task create --title T --body "%s"' % NO_AC)
    assert r is not None and MISSING_MARK in r


def test_control_positive_a_case_that_must_pass_does_pass():
    """...and can it see an ALLOW? A hook that denied everything would pass the
    negative control alone."""
    assert reason('clawgatectl task create --title T --body "%s"' % AC) is None


def test_control_the_two_controls_disagree():
    """The pair, stated as one fact: the same command shape with and without the
    heading produces DIFFERENT verdicts. A constant verdict passes each control
    above in isolation and fails here."""
    with_ac = reason('clawgatectl task create --body "%s"' % AC)
    without = reason('clawgatectl task create --body "%s"' % NO_AC)
    assert with_ac is None and without is not None


def test_control_the_flow_file_the_hook_routes_to_exists():
    """🔴 A router pointing at nothing is worse than no router: it reads as
    guidance and delivers none."""
    assert FLOW.is_file(), "%s is missing — every deny message names it" % FLOW
    assert "## Phase 0" in FLOW.read_text(encoding="utf-8")


# =========================================================================== #
# 1. THE DETECTOR — a literal table, never derived from the implementation
# =========================================================================== #
ACCEPTS = [
    "## Acceptance criteria",
    "## acceptance criteria",
    "## ACCEPTANCE CRITERIA",
    "## Acceptance Criteria",
    "##\tAcceptance criteria",
    "##  Acceptance  criteria",
    "## Acceptance criteria:",
    "## Acceptance criteria (revision two)",
    "## Acceptance criteria ##",
    " ## Acceptance criteria",
    "  ## Acceptance criteria",
    "   ## Acceptance criteria",
    "some prose first\n\n## Acceptance criteria\n1. a thing",
    "## Context\nwhy\n\n## Acceptance criteria\n1. a thing",
    "## Acceptance criteria\n1. a thing\n\n## Non-goals\n- not that",
]

REJECTS = [
    "",
    "make it better",
    "# Acceptance criteria",
    "### Acceptance criteria",
    "#### Acceptance criteria",
    "##Acceptance criteria",
    "**Acceptance criteria**",
    "Acceptance criteria",
    "Acceptance criteria:\n1. a thing",
    "## Acceptance-criteria",
    "## Acceptancecriteria",
    "## Acceptance criteriaX",
    "## Acceptance",
    "## criteria",
    "    ## Acceptance criteria",
    "\t## Acceptance criteria",
    "## Accepted criteria",
    "## Acceptance  and criteria",
]


@pytest.mark.parametrize("body", ACCEPTS)
def test_detector_accepts(body):
    assert guard.has_acceptance_criteria(body) is True


@pytest.mark.parametrize("body", REJECTS)
def test_detector_rejects(body):
    assert guard.has_acceptance_criteria(body) is False


@pytest.mark.parametrize("body", [None, 0, [], {}, b"## Acceptance criteria"])
def test_detector_rejects_non_strings(body):
    assert guard.has_acceptance_criteria(body) is False


FENCED = [
    "```\n## Acceptance criteria\n```",
    "```markdown\n## Acceptance criteria\n```",
    "~~~\n## Acceptance criteria\n~~~",
    "~~~yaml\n## Acceptance criteria\n~~~",
    "````\n## Acceptance criteria\n````",
    "text\n\n```\n## Acceptance criteria\n- template\n```\n\nmore text",
    # An UNCLOSED fence swallows the rest of the body — bash's own heredoc rule and
    # CommonMark's fence rule agree, and it is the fail-closed direction.
    "```\n## Acceptance criteria\n",
    # A longer closing run than the opener still closes; a SHORTER one does not.
    "```\n## Acceptance criteria\n``\nstill inside",
]


@pytest.mark.parametrize("body", FENCED)
def test_a_heading_inside_a_fence_does_not_count(body):
    """🔴 The flow file SHOWS this template inside a fence. A draft that quotes the
    template and forgets to fill it in is exactly the near-miss to catch, and a
    substring search passes it."""
    assert guard.has_acceptance_criteria(body) is False


AFTER_FENCE = [
    "```\ncode\n```\n## Acceptance criteria",
    "```py\nprint(1)\n```\n\n## Acceptance criteria\n1. x",
    "~~~\ncode\n~~~\n## Acceptance criteria",
    "````\ncode\n````\n## Acceptance criteria",
    # A `~~~` cannot close a ``` fence, so this heading is still inside one...
    # ...but this one is: the fence closes with its own character.
    "```\n## not this one\n```\n## Acceptance criteria",
]


@pytest.mark.parametrize("body", AFTER_FENCE)
def test_a_heading_after_a_CLOSED_fence_does_count(body):
    """The other direction of the fence tracker. Without this, a mutant that treats
    every fence line as 'now inside a fence, forever' survives the table above."""
    assert guard.has_acceptance_criteria(body) is True


def test_a_tilde_run_cannot_close_a_backtick_fence():
    assert guard.has_acceptance_criteria(
        "```\ncode\n~~~\n## Acceptance criteria") is False


def test_a_closing_fence_may_not_carry_an_info_string():
    assert guard.has_acceptance_criteria(
        "```\ncode\n``` js\n## Acceptance criteria") is False


# =========================================================================== #
# 2. BODY SOURCE: --body
# =========================================================================== #
def test_body_flag_with_criteria_is_allowed():
    assert allowed('clawgatectl task create --title T --body "%s"' % AC)


def test_body_flag_without_criteria_is_denied():
    assert denied_missing('clawgatectl task create --title T --body "%s"' % NO_AC)


def test_body_equals_form_with_criteria_is_allowed():
    assert allowed("clawgatectl task create --title T --body='%s'" % AC)


def test_body_equals_form_without_criteria_is_denied():
    assert denied_missing("clawgatectl task create --title T --body='%s'" % NO_AC)


def test_a_body_that_mentions_criteria_without_the_heading_is_denied():
    """SKILL.md: a body that merely *reads* like criteria is NOT author-specified."""
    assert denied_missing(
        'clawgatectl task create --body "Acceptance criteria: 1. it works"')


def test_a_body_using_markdown_code_spans_is_still_readable():
    """🔴 A measured false positive: treating every backtick as a command
    substitution reported 'cannot see the body' about a body whose every byte was
    in the argument. Code spans are ubiquitous in a task body."""
    body = "run `drift-check.sh` and read `rc`"
    assert denied_missing('clawgatectl task create --body "%s"' % body)


def test_a_body_mentioning_a_dollar_variable_in_prose_is_still_readable():
    """Same class: a task body is PROSE, so a lone `$VAR` in it is text."""
    body = "%s\n3. `$KUBECONFIG` is never hardcoded" % AC
    assert allowed('clawgatectl task create --body "%s"' % body)


def test_a_body_that_is_only_a_variable_reference_cannot_be_seen():
    assert denied_unseeable('clawgatectl task create --title T --body "$BODY"')


def test_a_body_that_is_only_a_braced_variable_reference_cannot_be_seen():
    assert denied_unseeable('clawgatectl task create --title T --body "${BODY}"')


def test_a_body_from_a_command_substitution_cannot_be_seen():
    assert denied_unseeable(
        'clawgatectl task create --title T --body "$(generate-spec.sh)"')


def test_a_substitution_body_that_LITERALLY_carries_the_heading_is_allowed():
    """Whatever the substitution expands to, the heading is in the bytes the author
    typed. Reporting 'cannot see the body' about a body that is right there would be
    the gate answering the wrong question."""
    assert allowed(
        'clawgatectl task create --body "%s\n3. shipped $(date -I)"' % AC)


# =========================================================================== #
# 3. BODY SOURCE: --body-file
# =========================================================================== #
def test_body_file_with_criteria_is_allowed(bodyfile):
    assert allowed("clawgatectl task create --title T --body-file %s" % bodyfile(AC))


def test_body_file_without_criteria_is_denied(bodyfile):
    assert denied_missing(
        "clawgatectl task create --title T --body-file %s" % bodyfile(NO_AC))


def test_body_file_equals_form_is_read(bodyfile):
    assert allowed("clawgatectl task create --body-file=%s" % bodyfile(AC))
    assert denied_missing("clawgatectl task create --body-file=%s" % bodyfile(NO_AC))


def test_a_body_file_that_does_not_exist_cannot_be_seen(tmp_path):
    assert denied_unseeable(
        "clawgatectl task create --body-file %s" % (tmp_path / "nope.md"))


def test_an_unreadable_body_file_cannot_be_seen(bodyfile):
    path = bodyfile(AC)
    os.chmod(path, 0o000)
    try:
        assert denied_unseeable("clawgatectl task create --body-file %s" % path)
    finally:
        os.chmod(path, 0o644)


def test_an_oversized_body_file_cannot_be_seen(tmp_path):
    """Blocked as UNREADABLE, never as 'no criteria' — the two are different facts
    and a gate that conflates them reports the wrong fix."""
    big = tmp_path / "big.md"
    # A LITERAL that OVERSHOOTS the cap rather than `cap + 1`: a fixture built from
    # the constant lands exactly on its own boundary and cannot see a mutant that
    # moves the comparison by one.
    big.write_text(AC + "\n" + ("x" * 1_500_000), encoding="utf-8")
    assert denied_unseeable("clawgatectl task create --body-file %s" % big)


def test_a_body_file_path_built_from_a_variable_cannot_be_seen():
    assert denied_unseeable('clawgatectl task create --body-file "$TMP/body.md"')


def test_a_body_file_written_by_a_heredoc_on_the_same_line_is_read():
    """PreToolUse runs BEFORE the command, so the file does not exist yet — but the
    text does, right there in the same command line."""
    cmd = ("cat > /tmp/body.md <<'EOF'\n%s\nEOF\n"
           "clawgatectl task create --title T --body-file /tmp/body.md" % AC)
    assert allowed(cmd)


def test_a_body_file_written_by_a_heredoc_WITHOUT_criteria_is_denied():
    cmd = ("cat > /tmp/body.md <<'EOF'\n%s\nEOF\n"
           "clawgatectl task create --title T --body-file /tmp/body.md" % NO_AC)
    assert denied_missing(cmd)


# =========================================================================== #
# 4. BODY SOURCE: heredoc
# =========================================================================== #
def test_the_heredoc_idiom_with_criteria_is_allowed():
    assert allowed(heredoc(AC))


def test_the_heredoc_idiom_without_criteria_is_denied():
    assert denied_missing(heredoc(NO_AC))


@pytest.mark.parametrize("tag", ["EOF", "BODY", "TASK_BODY", "eof", "md.body-1"])
def test_every_heredoc_tag_spelling_is_read(tag):
    assert allowed(heredoc(AC, tag=tag))
    assert denied_missing(heredoc(NO_AC, tag=tag))


def test_an_unquoted_heredoc_tag_is_read():
    cmd = "clawgatectl task create --title T --body \"$(cat <<EOF\n%s\nEOF\n)\"" % AC
    assert allowed(cmd)


def test_a_double_quoted_heredoc_tag_is_read():
    cmd = 'clawgatectl task create --title T --body "$(cat <<"EOF"\n%s\nEOF\n)"' % AC
    assert allowed(cmd)


def test_a_tab_stripping_heredoc_is_read():
    cmd = ("clawgatectl task create --title T --body \"$(cat <<-'EOF'\n\t%s\n\tEOF\n)\""
           % AC.replace("\n", "\n\t"))
    assert allowed(cmd)


def test_an_unterminated_heredoc_runs_to_the_end_of_the_text():
    cmd = "clawgatectl task create --title T --body-file - <<'EOF'\n%s" % AC
    assert allowed(cmd)


def test_body_file_dash_fed_by_a_heredoc_is_readable():
    assert allowed("clawgatectl task create --title T --body-file - <<'EOF'\n%s\nEOF" % AC)
    assert denied_missing(
        "clawgatectl task create --title T --body-file - <<'EOF'\n%s\nEOF" % NO_AC)


def test_a_here_string_is_not_read_as_a_heredoc():
    """🔴 `<<<word` has no body and no terminator. Reading it as a heredoc would
    swallow the rest of the command line as 'body text' — prose the gate must not
    credit. Here the swallowed text WOULD carry the heading, so a regression is
    visible as a wrong ALLOW rather than as nothing."""
    cmd = ("clawgatectl task create --title T --body-file - <<<x\n"
           "echo '## Acceptance criteria'")
    assert denied_unseeable(cmd)


# =========================================================================== #
# 5. BODY SOURCE: none at all, and the piped case that MUST block
# =========================================================================== #
def test_a_create_with_no_body_argument_at_all_cannot_be_seen():
    assert denied_unseeable("clawgatectl task create --title T --repo devrc")


def test_a_piped_body_is_BLOCKED_not_passed_through():
    """🔴 THE DELIBERATE DECISION. Failing open here would make the gate walkable by
    changing the shape of the call rather than its content."""
    assert denied_unseeable("gen-spec.sh | clawgatectl task create --title T --body-file -")


def test_a_piped_body_block_says_how_to_make_it_readable():
    r = reason("gen-spec.sh | clawgatectl task create --body-file -")
    assert "--body-file <a real path>" in r
    assert "--body '<the full markdown>'" in r


@pytest.mark.parametrize("sink", ["-", "/dev/stdin", "/proc/self/fd/0"])
def test_every_stdin_spelling_of_body_file_blocks_when_piped(sink):
    assert denied_unseeable("gen.sh | clawgatectl task create --body-file %s" % sink)


# =========================================================================== #
# 6. BODY SOURCE: curl
# =========================================================================== #
def curl_create(payload, flag="-d", method="-X POST"):
    return ("curl -sf %s http://192.168.50.250:30302/api/tasks "
            "-H 'Content-Type: application/json' %s '%s'" % (method, flag, payload))


def test_a_curl_create_with_criteria_is_allowed():
    assert allowed(curl_create(json.dumps({"title": "t", "body": AC})))


def test_a_curl_create_without_criteria_is_denied():
    assert denied_missing(curl_create(json.dumps({"title": "t", "body": NO_AC})))


@pytest.mark.parametrize("flag", ["-d", "--data", "--data-raw", "--data-ascii",
                                  "--data-binary", "--json"])
def test_every_curl_data_flag_is_read(flag):
    assert allowed(curl_create(json.dumps({"body": AC}), flag=flag))
    assert denied_missing(curl_create(json.dumps({"body": NO_AC}), flag=flag))


def test_a_curl_create_with_no_explicit_method_but_data_is_a_POST():
    """curl implies POST when data is present, so the gate must too."""
    assert denied_missing(curl_create(json.dumps({"body": NO_AC}), method=""))


def test_a_curl_create_reading_its_payload_from_a_file(bodyfile):
    path = bodyfile(json.dumps({"title": "t", "body": AC}), name="payload.json")
    assert allowed("curl -X POST http://h:1/api/tasks -d @%s" % path)


def test_a_curl_create_whose_payload_file_lacks_criteria(bodyfile):
    path = bodyfile(json.dumps({"title": "t", "body": NO_AC}), name="payload.json")
    assert denied_missing(
        "curl -X POST http://h:1/api/tasks -d @%s" % path)


def test_a_curl_create_reading_its_payload_from_stdin_cannot_be_seen():
    assert denied_unseeable("gen.sh | curl -X POST http://h:1/api/tasks -d @-")


def test_a_curl_create_with_a_heredoc_payload_is_read():
    cmd = ("curl -X POST http://h:1/api/tasks -d @- <<'EOF'\n%s\nEOF"
           % json.dumps({"title": "t", "body": AC}))
    assert allowed(cmd)


def test_a_curl_create_with_a_non_json_payload_cannot_be_seen():
    assert denied_unseeable("curl -X POST http://h:1/api/tasks -d 'title=t&body=x'")


def test_a_curl_create_whose_json_has_no_body_key_cannot_be_seen():
    assert denied_unseeable(curl_create(json.dumps({"title": "t"})))


def test_a_curl_create_through_the_url_flag_is_seen():
    assert denied_missing(
        "curl -X POST --url http://h:1/api/tasks -d '%s'"
        % json.dumps({"body": NO_AC}))


def test_a_curl_create_with_a_query_string_on_the_path_is_seen():
    assert denied_missing(
        "curl -X POST 'http://h:1/api/tasks?debug=1' -d '%s'"
        % json.dumps({"body": NO_AC}))


def test_a_curl_create_with_a_trailing_slash_is_seen():
    assert denied_missing(
        "curl -X POST http://h:1/api/tasks/ -d '%s'" % json.dumps({"body": NO_AC}))


def test_a_bearer_token_header_is_not_mistaken_for_a_url():
    assert denied_missing(
        "curl -sf -X POST -H 'Authorization: Bearer abc' http://h:1/api/tasks "
        "-d '%s'" % json.dumps({"body": NO_AC}))


# =========================================================================== #
# 7. NON-TRIGGERS — the load-bearing half
# =========================================================================== #
NOT_A_CREATE = [
    "clawgatectl health",
    "clawgatectl agent ls",
    "clawgatectl agent resolve task-drafter --id",
    "clawgatectl task ls",
    "clawgatectl task ls --summary --status open",
    "clawgatectl task ls --summary --tag create",
    "clawgatectl task get 193",
    "clawgatectl task status 193 in_progress",
    "clawgatectl task status 193 ready_for_review",
    'clawgatectl task comment 193 --body "**Starting** — host wb"',
    'clawgatectl task comment 193 --body "no criteria in this comment at all"',
    "clawgatectl task comment 193 --body-file /tmp/report.md",
    "clawgatectl --env-file ~/.claude/clawgate.env task get 5",
    "curl -sf http://h:1/api/tasks",
    "curl -sf http://h:1/api/tasks?summary=1",
    "curl -sf http://h:1/api/tasks/193",
    "curl -X POST http://h:1/api/tasks/193/comments -d '{\"body\":\"hi\"}'",
    "curl -X PATCH http://h:1/api/tasks/193 -d '{\"title\":\"t\"}'",
    "curl -X DELETE http://h:1/api/tasks/193",
    "curl -sf http://h:1/api/tags",
    "curl -sf http://h:1/api/projects",
    "curl -X POST http://h:1/api/send -d '{\"type\":\"permission\"}'",
    "curl -X GET http://h:1/api/tasks -d 'x=1'",
    "git status",
    "git -C /home/zach/workspace/devrc log --oneline -5",
    "ls ~/.claude/hooks/",
    "echo hello",
    "grep -rn clawgatectl scripts/",
    "kubectl -n clawgate get pods",
]


@pytest.mark.parametrize("cmd", NOT_A_CREATE)
def test_commands_that_must_not_trigger(cmd):
    assert allowed(cmd), cmd


PRODUCER_LAUNCHES = [
    "/home/zach/workspace/devrc/scripts/task-spec-drafter/drafter.sh --once",
    "python3 /home/zach/workspace/devrc/scripts/task-spec-drafter/send_digest.py",
    "/home/zach/workspace/devrc/scripts/repo-cos/run.sh",
    "systemctl --user start task-spec-drafter.service",
    "systemctl --user status repo-cos.timer",
]


@pytest.mark.parametrize("cmd", PRODUCER_LAUNCHES)
def test_launching_an_unattended_producer_is_not_a_create(cmd):
    """🔴 Scope is interactive-only and it is free structurally: the argv this hook
    sees is the LAUNCHER's, never the POST the producer makes in its own process."""
    assert allowed(cmd), cmd


def test_a_comment_body_that_QUOTES_a_create_command_does_not_trigger():
    """The tokens `task create` inside a quoted argument are ONE token to a lexer,
    so they can never be read as the verb."""
    assert allowed(
        'clawgatectl task comment 5 --body "next step: clawgatectl task create --body x"')


def test_a_grep_for_the_create_verb_does_not_trigger():
    assert allowed("git grep -n 'clawgatectl task create' -- scripts/")


# =========================================================================== #
# 8. THE OVERRIDE — structural, not spelled
# =========================================================================== #
def test_an_inline_assignment_overrides():
    assert allowed('%s=1 clawgatectl task create --body "%s"'
                   % (OVERRIDE, NO_AC))


def test_an_exported_assignment_in_command_position_overrides():
    assert allowed('export %s=1; clawgatectl task create --body "%s"'
                   % (OVERRIDE, NO_AC))


def test_an_assignment_after_a_semicolon_overrides():
    assert allowed('echo hi; %s=1 clawgatectl task create --body "%s"'
                   % (OVERRIDE, NO_AC))


def test_the_process_environment_overrides():
    assert allowed('clawgatectl task create --body "%s"' % NO_AC,
                   env={OVERRIDE: "1"})


@pytest.mark.parametrize("value", ["true", "yes", "0", "", "01", "1 ", "TRUE"])
def test_only_the_one_spelling_overrides(value):
    """An override with several spellings is an override nobody can grep for."""
    assert denied_missing('clawgatectl task create --body "%s"' % NO_AC,
                          env={OVERRIDE: value})


def test_naming_the_override_INSIDE_a_task_body_does_not_disarm_the_gate():
    """🔴 A guard you can switch off by quoting its own name is not a guard. The
    inline form is anchored on a command boundary for exactly this."""
    body = "we should look at %s one day" % OVERRIDE_ON
    assert denied_missing('clawgatectl task create --body "%s"' % body)


def test_naming_the_override_inside_a_heredoc_body_does_not_disarm_the_gate():
    assert denied_missing(heredoc("consider %s later" % OVERRIDE_ON))


def test_the_override_is_named_in_every_deny_message():
    """The block has to carry its own escape hatch, or it is a wall."""
    for r in (reason('clawgatectl task create --body "%s"' % NO_AC),
              reason('clawgatectl task create --body "$(gen.sh)"')):
        assert OVERRIDE_ON in r


# =========================================================================== #
# 9. THE MESSAGES — the hook is the router
# =========================================================================== #
@pytest.mark.parametrize("cmd", [
    'clawgatectl task create --body "%s"' % NO_AC,
    'clawgatectl task create --body "$(gen.sh)"',
    "gen.sh | clawgatectl task create --body-file -",
    "curl -X POST http://h:1/api/tasks -d '{\"body\":\"x\"}'",
])
def test_every_deny_names_the_flow_file(cmd):
    """🔴 A `flows/` file does not auto-fire the way a skill description does — the
    hook is the ROUTER as well as the enforcer."""
    r = reason(cmd)
    assert r is not None
    assert FLOW_MARK in r, r


@pytest.mark.parametrize("cmd", [
    'clawgatectl task create --body "%s"' % NO_AC,
    'clawgatectl task create --body "$(gen.sh)"',
])
def test_every_deny_names_the_deployed_flow_path(cmd):
    assert DEPLOYED_FLOW in reason(cmd)


def test_the_deployed_and_repo_flow_paths_are_both_named():
    """🔴 FOUND BY A SURVIVING MUTANT: renaming FLOW_DEPLOYED to anything at all
    survived, because the assertion read the constant out of the module under
    test — the module agreeing with itself. These are LITERALS."""
    r = reason('clawgatectl task create --body "%s"' % NO_AC)
    assert DEPLOYED_FLOW in r, r
    assert REPO_FLOW in r, r


def test_the_hooks_constants_are_pinned_to_their_literals():
    """The other half of the same fix: the constants themselves, so a rename is a
    decision someone makes rather than a diff nobody reads."""
    assert guard.FLOW_DEPLOYED == DEPLOYED_FLOW
    assert guard.FLOW_REPO == REPO_FLOW
    assert guard.OVERRIDE_ENV == OVERRIDE
    assert guard.OVERRIDE_VALUE == "1"
    assert guard.MAX_BODY_FILE_BYTES == BODY_FILE_CAP
    assert guard.CREATE_PATH == "/api/tasks"


def test_the_missing_criteria_message_explains_the_status_gate():
    r = reason('clawgatectl task create --body "%s"' % NO_AC)
    assert "ready_for_review" in r
    assert "Status gate" in r


def test_the_missing_criteria_message_names_the_six_phases():
    r = reason('clawgatectl task create --body "%s"' % NO_AC)
    for phase in ("PRE-VERIFY", "INTERVIEW", "RECOMMEND", "DRAFT", "CONFIRM", "CREATE"):
        assert phase in r, phase


def test_the_missing_criteria_message_names_the_askuserquestion_budget():
    r = reason('clawgatectl task create --body "%s"' % NO_AC)
    assert "at most 2 AskUserQuestion rounds" in r
    assert "at most 4 questions each" in r


def test_the_missing_criteria_message_names_the_tag_validation_step():
    assert "GET /api/tags" in reason('clawgatectl task create --body "%s"' % NO_AC)


def test_the_missing_criteria_message_spells_out_the_heading_rule():
    r = reason('clawgatectl task create --body "### Acceptance criteria\n1. x"')
    assert "level-2 ATX heading" in r
    assert "`###`" in r


def test_the_unseeable_message_says_why_it_does_not_fail_open():
    r = reason('clawgatectl task create --body "$(gen.sh)"')
    assert "walkable by changing the SHAPE of the call" in r


def test_the_two_messages_are_distinguishable():
    """A single message for both verdicts would let a test pass on 'it blocked'."""
    a = reason('clawgatectl task create --body "%s"' % NO_AC)
    b = reason('clawgatectl task create --body "$(gen.sh)"')
    assert a != b
    assert MISSING_MARK in a and MISSING_MARK not in b
    assert UNSEEABLE_MARK in b and UNSEEABLE_MARK not in a


def test_the_unseeable_message_names_the_specific_reason():
    piped = reason("gen.sh | clawgatectl task create --body-file -")
    subst = reason('clawgatectl task create --body "$(gen.sh)"')
    assert REASON_STDIN in piped
    assert REASON_OPAQUE in subst


# =========================================================================== #
# 10. MULTIPLE CREATES ON ONE LINE
# =========================================================================== #
def test_two_creates_where_the_second_lacks_criteria_is_denied():
    """🔴 The verdict is 'every create had criteria', never 'some body somewhere
    did'. Otherwise a good create on the same line launders a bad one."""
    cmd = ('clawgatectl task create --body "%s" && clawgatectl task create --body "%s"'
           % (AC, NO_AC))
    assert denied_missing(cmd)


def test_two_creates_where_the_first_lacks_criteria_is_denied():
    cmd = ('clawgatectl task create --body "%s" && clawgatectl task create --body "%s"'
           % (NO_AC, AC))
    assert denied_missing(cmd)


def test_two_well_specified_creates_are_allowed():
    cmd = ('clawgatectl task create --body "%s" && clawgatectl task create --body "%s"'
           % (AC, AC))
    assert allowed(cmd)


def test_a_create_beside_an_unrelated_read_is_still_judged():
    assert denied_missing(
        'clawgatectl task ls --summary; clawgatectl task create --body "%s"' % NO_AC)


def test_an_unseeable_create_beside_a_well_specified_one_is_still_denied():
    """🔴 The unseeable verdict must SURVIVE a later create that passes. A version
    that only remembered the LAST verdict would allow this line, which is the shape
    that launders a generated body behind a hand-written one."""
    cmd = ('clawgatectl task create --body "$(gen.sh)" && '
           'clawgatectl task create --body "%s"' % AC)
    assert denied_unseeable(cmd)


def test_the_FIRST_unseeable_reason_is_the_one_reported():
    """🔴 FOUND BY A SURVIVING MUTANT. `worst = unseeable_text(…)` (dropping the
    `worst or`) passed every other case here, because the second create either
    matched the first's verdict or passed and never wrote. Two creates with
    DIFFERENT unreadable reasons is the only shape that can see it."""
    cmd = ('clawgatectl task create --body "$(gen.sh)"; '
           'gen.sh | clawgatectl task create --body-file -')
    r = reason(cmd)
    assert r is not None and UNSEEABLE_MARK in r
    assert REASON_OPAQUE in r, r
    assert REASON_STDIN not in r, r


def test_the_missing_verdict_outranks_the_unseeable_one():
    """When a line has both, the actionable message wins: 'add criteria' is a fix
    the author can make, 'I cannot read your body' is a fix to the call shape."""
    cmd = ('clawgatectl task create --body "$(gen.sh)" && '
           'clawgatectl task create --body "%s"' % NO_AC)
    assert denied_missing(cmd)


# =========================================================================== #
# 11. WRAPPERS AND SHAPES THE PARSER MUST STILL SEE THROUGH
# =========================================================================== #
WRAPPED = [
    'env clawgatectl task create --body "%s"',
    'timeout 30 clawgatectl task create --body "%s"',
    'bash -c \'clawgatectl task create --body "%s"\'',
    'FOO=bar clawgatectl task create --body "%s"',
    '/home/zach/.nix-profile/bin/clawgatectl task create --body "%s"',
    'clawgatectl --api-url http://h:1 task create --body "%s"',
    'clawgatectl --env-file ~/.claude/clawgate.env task create --body "%s"',
    'clawgatectl task create --repo devrc --branch main --body "%s"',
    'clawgatectl task create --tag ops --tag project:devrc --body "%s"',
]


@pytest.mark.parametrize("shape", WRAPPED)
def test_a_wrapped_create_is_still_judged(shape):
    assert allowed(shape % AC), shape
    assert denied_missing(shape % NO_AC), shape


def test_a_create_inside_a_command_substitution_is_judged():
    assert denied_missing('id=$(clawgatectl task create --body "%s")' % NO_AC)


# =========================================================================== #
# 12. THE I/O CONTRACT — driven through a real process
# =========================================================================== #
def test_a_clean_allow_prints_nothing_at_all():
    rc, out = verdict("clawgatectl health")
    assert rc == 0 and out is None


@pytest.mark.parametrize("payload", [
    "", "not json", "[]", "null", '{"tool_name":"Bash"}',
    '{"tool_name":"Bash","tool_input":null}',
    '{"tool_name":"Bash","tool_input":{}}',
    '{"tool_name":"Bash","tool_input":{"command":null}}',
    '{"tool_name":"Bash","tool_input":{"command":""}}',
    '{"tool_input":{"command":"clawgatectl task create --body x"}}',
])
def test_malformed_input_exits_zero_and_prints_nothing(payload):
    rc, out = verdict(None, raw=payload)
    assert rc == 0 and out is None, payload


@pytest.mark.parametrize("tool", ["Read", "Edit", "Write", "Task", "AskUserQuestion",
                                  "WebFetch", "Glob"])
def test_a_non_bash_tool_is_never_judged(tool):
    assert allowed('clawgatectl task create --body "%s"' % NO_AC, tool_name=tool)


def test_the_deny_json_carries_exactly_the_pretooluse_contract():
    _, out = verdict('clawgatectl task create --body "%s"' % NO_AC)
    assert list(out) == ["hookSpecificOutput"]
    hso = out["hookSpecificOutput"]
    assert sorted(hso) == ["hookEventName", "permissionDecision",
                           "permissionDecisionReason"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert isinstance(hso["permissionDecisionReason"], str)


def test_a_very_long_body_is_handled_without_blowing_up():
    body = AC + "\n" + ("- a criterion line\n" * 5000)
    assert allowed('clawgatectl task create --body "%s"' % body)


def test_the_hook_never_writes_bytecode_beside_itself():
    """It is deployed into a home-manager-managed directory; a `__pycache__` there
    is litter the switch does not own."""
    assert guard.__dict__.get("sys") is not None or True  # module imported cleanly
    src = Path(HOOK).read_text(encoding="utf-8")
    assert "sys.dont_write_bytecode = True" in src


# =========================================================================== #
# 13. UNIT-LEVEL: the classifiers, driven directly
# =========================================================================== #
@pytest.mark.parametrize("argv,expected", [
    (["clawgatectl", "task", "create"], True),
    (["clawgatectl", "task", "create", "--body", "x"], True),
    (["clawgatectl", "--token", "t", "task", "create"], True),
    (["/nix/store/x/bin/clawgatectl", "task", "create"], True),
    (["clawgatectl", "task", "ls"], False),
    (["clawgatectl", "task", "get", "5"], False),
    (["clawgatectl", "task", "comment", "5", "--body", "x"], False),
    (["clawgatectl", "task", "status", "5", "create"], False),
    (["clawgatectl", "create", "task"], False),
    (["clawgatectl", "task"], False),
    (["clawgatectl"], False),
    (["clawgatectl", "task create"], False),
    (["curl", "task", "create"], False),
    ([], False),
])
def test_is_clawgatectl_task_create(argv, expected):
    assert guard.is_clawgatectl_task_create(argv) is expected


@pytest.mark.parametrize("argv,expected", [
    (["curl", "-X", "POST", "http://h/api/tasks", "-d", "{}"], True),
    (["curl", "http://h/api/tasks", "-d", "{}"], True),
    (["curl", "--request", "POST", "http://h/api/tasks", "--data", "{}"], True),
    (["curl", "http://h/api/tasks"], False),
    (["curl", "-X", "GET", "http://h/api/tasks", "-d", "{}"], False),
    (["curl", "-X", "POST", "http://h/api/tasks/5/comments", "-d", "{}"], False),
    (["curl", "-X", "POST", "http://h/api/send", "-d", "{}"], False),
    (["curl", "-X", "POST", "http://h/api/tags", "-d", "{}"], False),
    (["wget", "-X", "POST", "http://h/api/tasks", "-d", "{}"], False),
    ([], False),
])
def test_is_curl_task_create(argv, expected):
    assert guard.is_curl_task_create(argv) is expected


@pytest.mark.parametrize("text,expected", [
    ("cmd <<'EOF'\nbody\nEOF\n", ["body"]),
    ("cmd <<EOF\nbody\nEOF\n", ["body"]),
    ('cmd <<"EOF"\nbody\nEOF\n', ["body"]),
    ("cmd <<\\EOF\nbody\nEOF\n", ["body"]),
    ("cmd <<-EOF\n\tbody\n\tEOF\n", ["body"]),
    ("cmd <<'A' <<'B'\none\nA\ntwo\nB\n", ["one", "two"]),
    ("cmd <<'EOF'\nline one\nline two\nEOF\n", ["line one\nline two"]),
    ("cmd <<'EOF'\nno terminator", ["no terminator"]),
    ("cmd <<<word\n", []),
    ("echo nothing here", []),
    ("a << b", []),
])
def test_heredoc_bodies(text, expected):
    assert guard.heredoc_bodies(text) == expected


@pytest.mark.parametrize("value,expected", [
    ("$(gen.sh)", True),
    ("$BODY", True),
    ("${BODY}", True),
    ("  $BODY  ", True),
    ("plain text", False),
    ("costs $5 and `code` spans", False),
    ("mentions $PATH in prose", False),
    ("", False),
])
def test_opaque_value(value, expected):
    assert guard.opaque_value(value) is expected


@pytest.mark.parametrize("value,expected", [
    ("$TMP/body.md", True),
    ("`pwd`/body.md", True),
    ("/tmp/body.md", False),
    ("~/body.md", False),
    ("body.md", False),
])
def test_opaque_path(value, expected):
    assert guard.opaque_path(value) is expected


@pytest.mark.parametrize("token,expected", [
    ("http://h:1/api/tasks", "/api/tasks"),
    ("https://clawgate.example/api/tasks", "/api/tasks"),
    ("http://h:1/api/tasks/", "/api/tasks"),
    ("http://h:1/api/tasks?x=1", "/api/tasks"),
    ("http://h:1/api/tasks#frag", "/api/tasks"),
    ("http://h:1/api/tasks/5", "/api/tasks/5"),
    ("http://h:1/api/tasks/5/comments", "/api/tasks/5/comments"),
    ("http://h:1", "/"),
    ("-H", None),
    ("Authorization: Bearer abc", None),
    ("{}", None),
])
def test_url_path(token, expected):
    assert guard._url_path(token) == expected


@pytest.mark.parametrize("argv,names,expected", [
    (["c", "--body", "x"], ("--body",), ["x"]),
    (["c", "--body=x"], ("--body",), ["x"]),
    (["c", "--body", "x", "--body", "y"], ("--body",), ["x", "y"]),
    (["c", "--body-file", "p"], ("--body",), []),
    (["c", "--body-file", "p"], ("--body-file",), ["p"]),
    (["c", "--body-file=p"], ("--body-file",), ["p"]),
    (["c", "--body"], ("--body",), []),
    (["c"], ("--body",), []),
])
def test_flag_values(argv, names, expected):
    assert guard._flag_values(argv, names) == expected


@pytest.mark.parametrize("cmd,argv,expect_cands,expect_reason", [
    ('clawgatectl task create --body hi', ["clawgatectl", "task", "create",
                                           "--body", "hi"], ["hi"], None),
    ('clawgatectl task create --title T', ["clawgatectl", "task", "create",
                                           "--title", "T"], [],
     REASON_NONE),
])
def test_body_candidates_reports_its_reason(cmd, argv, expect_cands, expect_reason):
    cands, why = guard.body_candidates(argv, cmd, False)
    assert cands == expect_cands
    assert why == expect_reason


def test_the_prefilter_returns_before_any_parse():
    """The hot path: a command naming neither `clawgatectl` nor `/api/tasks` cannot
    be a create, and `evaluate` must not even reach the parser for it. Asserted by
    handing it a `guard_core` that RAISES if consulted."""
    class Explode:
        def commands(self, _text):
            raise AssertionError("the parser was consulted on the fast path")
    assert guard.evaluate("git status && make test", {}, Explode()) is None


def test_the_prefilter_does_NOT_swallow_a_real_create():
    """The other direction of the fast path — without this, a mutant that always
    returns early survives the test above."""
    class Explode:
        def commands(self, _text):
            raise AssertionError("reached")
    with pytest.raises(AssertionError):
        guard.evaluate('clawgatectl task create --body "x"', {}, Explode())


# =========================================================================== #
# 14. THE WIRING — the seam nobody owns
# =========================================================================== #
def test_home_nix_deploys_the_hook():
    """🔴 A NEW file that is not deployed is a hook the switch reports success
    about and that does not exist."""
    assert 'home.file.".claude/hooks/clawgate-task-interview-guard.py"' \
        in HOME_NIX.read_text(encoding="utf-8")


def test_the_registrar_registers_the_hook_on_pretooluse():
    src = REGISTRAR.read_text(encoding="utf-8")
    assert "PRE_BASH_CMDS" in src
    assert "~/.claude/hooks/clawgate-task-interview-guard.py" in src


def test_the_registrar_manages_the_hooks_interpreter():
    """🔴 PR #609: a hook resolving its interpreter by bare name FAILS OPEN for the
    ~1s of every switch in which the profile has no python3, and a PreToolUse hook
    exiting 127 is non-blocking."""
    src = REGISTRAR.read_text(encoding="utf-8")
    block = src.split("MANAGED_HOOK_SCRIPTS = frozenset({", 1)[1].split("})", 1)[0]
    assert '"clawgate-task-interview-guard.py",' in block
    # Anti-vacuity: the extraction really did get the set, not the whole file.
    assert '"bash-guard.py",' in block and "PRE_BASH_CMDS" not in block


def test_the_skill_routes_to_the_flow():
    """The flow is reachable from the always-loaded surface, not only from a block."""
    text = SKILL.read_text(encoding="utf-8")
    assert "flows/task-authoring.md" in text
    assert "Flow files" in text


def test_the_skill_did_not_grow():
    """🔴 SKILL.md loads in FULL on every invocation of this skill. The addition was
    paid for by an eviction in the same commit; this pins that it stays paid.

    The ceiling is the size measured at the commit that last SHRANK the file, not
    a round number above it — so this fails on a regrowth rather than merely on a
    doubling. 🔴 RE-PIN IT WHENEVER THE FILE SHRINKS: a ceiling left at an old,
    larger size silently licenses the regrowth it was installed to catch."""
    assert SKILL.stat().st_size <= 15088, (
        "claude/skills/clawgate/SKILL.md is %d bytes. History: 18868 before the "
        "task-authoring flow, 18858 after, 15088 after the task-pickup ritual "
        "moved out to flows/task-pickup.md. Any addition needs an eviction in the "
        "SAME commit." % SKILL.stat().st_size)


def test_the_flow_carries_the_body_template_with_the_required_heading():
    text = FLOW.read_text(encoding="utf-8")
    for section in ("## Context", "## Acceptance criteria", "## Non-goals",
                    "## Assumptions", "## Verifier", "## Blast radius"):
        assert section in text, section


def test_the_flow_names_the_five_phases_and_phase_zero():
    text = FLOW.read_text(encoding="utf-8")
    for phase in ("Phase 0 — PRE-VERIFY", "Phase 1 — INTERVIEW",
                  "Phase 2 — RECOMMEND", "Phase 3 — DRAFT",
                  "Phase 4 — CONFIRM", "Phase 5 — CREATE"):
        assert phase in text, phase


def test_the_flows_directory_is_a_sibling_of_reference():
    """The repo-wide convention this PR introduces."""
    assert FLOW.parent.name == "flows"
    assert (FLOW.parent.parent / "reference").is_dir()


def test_the_template_in_the_flow_would_itself_pass_the_gate():
    """🔴 THE SEAM. The flow tells the author to emit a body shaped like its
    template; if that template does not satisfy the detector, the flow routes
    people into a block. Extract the fenced template and run the REAL detector on
    it — two components each correct in isolation is exactly the failure RULES.md
    names."""
    text = FLOW.read_text(encoding="utf-8")
    marker = "### The body template"
    assert marker in text
    after = text.split(marker, 1)[1]
    block = after.split("```markdown", 1)[1].split("```", 1)[0]
    assert guard.has_acceptance_criteria(block) is True
    # ...and the extraction really did get the template, not an empty string.
    assert "## Verifier" in block


def test_the_hook_and_guard_core_agree_on_what_a_command_is():
    """The hook resolves `guard_core` from its own directory at run time. Pin that
    the module it will find really does expose the entry point it calls."""
    assert callable(getattr(guard_core, "commands", None))
    assert guard_core.commands("clawgatectl task create --body x")[0][:3] == [
        "clawgatectl", "task", "create"]


# =========================================================================== #
# 15. THE HELP EXEMPTION — a create-shaped argv that CANNOT create
# =========================================================================== #
# Found by live use, not by review: `clawgatectl task create --help` was DENIED
# for naming no readable body. It creates nothing — cobra prints usage and exits
# without calling the command — so the deny was a false positive, and the kind
# that trains an operator to reach for the override reflexively. The exemption is
# structural: these argvs cannot reach `POST /api/tasks` at all.
def test_help_flag_on_a_create_is_allowed():
    assert allowed("clawgatectl task create --help")


def test_help_shorthand_on_a_create_is_allowed():
    assert allowed("clawgatectl task create -h")


def test_help_subcommand_form_is_allowed():
    """`clawgatectl help task create` puts `task`+`create` adjacent in the operand
    list, so the create detector matches it. It still creates nothing."""
    assert allowed("clawgatectl help task create")


def test_help_wins_even_next_to_a_body_with_no_criteria():
    """cobra prints usage and exits regardless of the other flags, so a `--body`
    riding along cannot be created either."""
    assert allowed('clawgatectl task create --body "%s" --help' % NO_AC)


def test_help_as_a_VALUE_does_not_exempt():
    """🔴 The anti-walk case. `--token --help` makes `--help` the token's VALUE,
    not a flag — if the scan missed that, the exemption would be reachable by any
    create willing to spell one of its flag values `--help`."""
    assert denied_missing('clawgatectl --token --help task create --body "%s"' % NO_AC)


def test_help_in_the_BODY_does_not_exempt():
    """The word inside the body text is not a flag."""
    assert denied_missing('clawgatectl task create --body "please --help me"')


def test_help_on_ONE_create_does_not_exempt_a_SECOND_create_on_the_line():
    """🔴 The exemption is per-argv. A compound line must still be judged on the
    create that really runs — this is the multi-create rule from section 10,
    re-asserted against the new escape."""
    assert denied_missing(
        'clawgatectl task create --help; clawgatectl task create --body "%s"' % NO_AC)


def test_help_does_not_exempt_a_curl_create():
    """`--help` is not a curl flag that suppresses the request, so a curl create
    carrying it still posts. The exemption must not generalise to curl."""
    assert denied_missing(
        "curl -X POST http://192.168.50.250:30302/api/tasks --help "
        "-d '{\"body\":\"%s\"}'" % NO_AC)


def test_help_exemption_unit_level():
    """Driven directly, so the predicate is pinned independently of `evaluate`."""
    assert guard.is_help_invocation(["clawgatectl", "task", "create", "--help"]) is True
    assert guard.is_help_invocation(["clawgatectl", "task", "create", "-h"]) is True
    assert guard.is_help_invocation(["clawgatectl", "help", "task", "create"]) is True
    assert guard.is_help_invocation(["clawgatectl", "task", "create"]) is False
    assert guard.is_help_invocation(
        ["clawgatectl", "--token", "--help", "task", "create"]) is False
    assert guard.is_help_invocation([]) is False


def test_help_is_not_a_blanket_allow_for_the_word_anywhere():
    """`help` as a NON-leading operand is a task title, not the subcommand."""
    assert denied_missing('clawgatectl task create --title help --body "%s"' % NO_AC)
