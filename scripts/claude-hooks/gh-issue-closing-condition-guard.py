#!/usr/bin/env python3
"""PreToolUse(Bash) gate: no GitHub issue is CREATED without a closing condition.

WHY THIS EXISTS
---------------
`claude/RULES.md` (Deterministic Over Prose -> the "Out of scope" branch) says an
agent may file a work item only once it can name the CONDITION that ends it and
who or what checks it; naming neither means saying so in the reply instead of
minting an object nobody can close. The operational definition is question 1 of
`claude/skills/clawgate/flows/task-authoring.md`, which is the SINGLE source --
pinned by `scripts/tests/test_closing_condition_single_source.py`. Nothing here
restates it; this file points at it.

🔴 THE FAILURE THIS FIXES IS SALIENCE, NOT DELIVERY, AND THAT IS MEASURED.
Two blind runs, same design, private solo repos so the outward-facing half of the
rule could not confound:

    unbriefed subagent   6 issues filed,  0 carrying a closing condition
    briefed subagent    10 issues filed, 10 carrying one

BOTH agents had the rule -- subagents receive RULES.md. (An earlier claim that
they do not was a FALSE ZERO: a transcript does not record the system prompt, so
grepping transcripts for the rule text could never have found it. Do not repeat
that measurement.) So the prose arrives and does not fire, which is exactly the
case PRINCIPLES.md answers with a structural fix rather than more prose. Same
shape as `clawgate-task-interview-guard.py`, whose sibling rule sat in 🔴 prose
and was skipped 2/2 until the hook made it structural.

🔴 WHAT THIS GATE CAN AND CANNOT CHECK -- READ BEFORE TRUSTING IT AS COVERAGE
----------------------------------------------------------------------------
It checks that the body carries a level-2 heading (`## Closing condition` or its
other name, `## Acceptance criteria`) WITH NON-EMPTY CONTENT UNDER IT. Whether
that content is a genuine OBSERVABLE END-STATE, rather than a restatement of the
fix, is **NOT machine-checkable and is not checked**. That caveat is measured,
not defensive: in the briefed arm above all 10 issues carried the heading and
MOST wrote the remedy under it instead of a condition that ends the object. So
this gate raises the floor from "nothing" to "a filled-in section"; it does not
certify the semantics, and a report that says otherwise is wrong.

Both spellings are accepted on purpose. task-authoring.md question 1 treats the
acceptance-criteria heading and the closing condition as one requirement under
two names, and every clawgate task body already carries the second spelling --
so accepting only the first would deny a correctly-specified body for using the
house heading.

One NARROW decode, and its boundary is the point: a `--body` value that ARRIVES
ANSI-C-QUOTED (`$'…\n…'`, which `shlex` does not implement, so it reaches this
gate as one line beginning `$##`) is ALSO read with `\n`/`\t` decoded — see
`escape_expanded`. A plainly double-quoted `"…\n…"` is NOT decoded: bash leaves
those two characters literal, GitHub renders the body on one line, and there is
no heading on that page. Crediting it would have made this gate passable by
appending ~30 characters that produce nothing, which is a spelled guard, not a
structural one. The `$` prefix is the discriminator and it is exact.

ROUTES COVERED, AND THE ONES THAT ARE NOT
-----------------------------------------
COVERED (classified from a real argv, never from adjacency in the raw text):
  * `gh issue create …`            -- the route agents actually use
  * `gh api …/issues` as a POST    -- explicit `-X POST`/`--method POST`, or a
                                      `-f`/`-F`/`--input` payload, which is what
                                      makes gh default to POST
  * `curl` POST to a `…/issues` URL path

NOT COVERED, deliberately enumerated so this file cannot read as wider than it
is (a guard that does that stops people looking -- RULES.md):
  * any OTHER client: `python`/`requests`/`urllib`, `wget`, `httpie`/`http`,
    `node`, a Go or Rust binary, or the GitHub MCP tools -- a PreToolUse(Bash)
    hook only ever sees Claude Code's Bash tool
  * `xargs gh issue create`, `ssh <host> gh issue create`, and argv assembled
    from a variable (`$CMD issue create`) -- the command text does not carry it
  * 🔴 A CREATE PRECEDED BY A SHELL KEYWORD, i.e. inside a compound statement:
    `if …; then gh issue create …; fi`, `while …; do gh issue create …; done`,
    and the bare `then`/`do`/`else`/`!` prefixes. `guard_core._peel_variants`
    peels wrappers (`sudo`, `timeout`, `nohup`) and `VAR=` assignments but not
    keywords, so argv[0] is `then` and the argv is not a `gh` at all.
    `time`/`command`/`exec`/`eval` DO reach the gate; those four are not
    keywords in the peeler's sense. Measured 2026-08-25, identical before and
    after this fix round, and NOT fixed here: the peeler is `guard_core`'s and
    is shared with `bash-guard.py`, so widening it is its own change with its
    own blast radius. Recorded rather than left for someone to rediscover.
  * `gh issue create --web` / `-w`, which does NOT post: gh opens the browser's
    new-issue form and exits, so the object is created by a human in a form this
    process cannot see. Structurally exempt, like `--help`; it is a one-flag way
    past this gate and is recorded as one rather than hidden.
  * the GitHub web UI, the mobile app, and anything a systemd/cron process does
  * `gh issue transfer`, `gh issue develop`, and issue creation as a side effect
    of some other tool (a bot, a workflow, `gh workflow run`)
  * the SEMANTIC question above: heading present and filled in is all it knows

🔴 ONE KNOWN FALSE POSITIVE, recorded because a limit nobody wrote down is a
limit nobody can fix: a body that QUOTES a heredoc operator inside a code fence
(`## Closing condition … ```cat <<'EOF' > x``` `) denies. `heredocs` is
quote-blind on purpose, `scrub_inert_heredocs` therefore blanks bytes that sit
inside the quoted argument, the argument's quote no longer balances and the
fallback tokeniser hands `--body` a fragment. Pinned by
test_a_body_quoting_a_heredoc_operator_is_a_KNOWN_FALSE_POSITIVE. The fix is to
make the SCRUB quote-aware while body resolution stays blind; that is a change to
the mechanism the mention-is-not-an-invocation requirement rests on and has not
been made.

ONLY `create`. `gh issue comment|edit|close|reopen|list|view|status|develop`,
`gh pr create`, and a GET of `…/issues` all pass untouched. This gate exists to
stop an object being MINTED, not to tax every interaction with one.

🔴 WHEN THE BODY CANNOT BE SEEN, THIS BLOCKS. IT DOES NOT PASS.
`gh issue create --body "$(generate.sh)"` hands the gate an argument it cannot
evaluate; `--body-file -` puts the body on a stdin no PreToolUse hook can read.
Passing either through would make the guard walkable by changing the SHAPE of
the call rather than its content -- the "spelled, not structural" failure
RULES.md names. Both are denies, with a message that says how to make the body
readable (write it to a file with the Write tool, then `--body-file <path>`).
The cost is real and accepted.

🔴 AND A HEREDOC ONLY EVER ANSWERS FOR THE COMMAND IT FEEDS. This claim used to
be false in a way that voided the paragraph above: every fallback resolved
`heredoc_bodies(<the whole command line>)`, so ONE unrelated
`cat > /tmp/plan.md <<'EOF' … EOF` -- the ordinary agent workflow this gate
watches -- rescued `--body-file -`, `--body "$(gen.sh)"`, an unreadable
`--body-file`, an attached `-b…`, and a create with no body flag at all. It also
made a line filing TWO issues pass on the strength of the FIRST one's heredoc.
Body resolution is now scoped to the create's OWN command segment
(`command_spans`), so a heredoc opened by a different command on the same line is
not a candidate. Two deliberate consequences, recorded rather than hidden:
  * an unreadable `--body-file <path>` is still rescued by a heredoc that WRITES
    THAT EXACT PATH, wherever on the line it sits -- `cat > p.md <<'EOF' … EOF &&
    gh issue create --body-file p.md` is a correctly-specified create and must
    not deny. The match is on the path, not on adjacency.
  * `cat <<'EOF' … EOF | gh issue create --body-file -` DENIES. The pipe puts the
    heredoc in a different segment, and no cheap parse ties one command's stdin
    to another command's heredoc. Use `--body "$(cat <<'EOF' … EOF)"`.

🔴 A MENTION IS NOT AN INVOCATION, AND THAT IS A FIRST-CLASS REQUIREMENT.
`bash-guard.py` blocks a command line that merely QUOTES a banned shape, and its
DESIGN NOTE defends that trade for irreversible actions. It is the wrong trade
here: this gate fires on `gh`, the most-typed tool in these repos, and blocking
a `grep`/`echo`/`rg` for the command string would train people to reach for the
override -- a permanently-red gate is worse than no gate. So classification is
argv-based (`grep 'gh issue create' f` has argv[0]=`grep`), and a heredoc body
whose operator attaches to a KNOWN INERT SINK (`cat`/`tee`/`echo`/`printf`, not
piped onward) is not read as commands. That sink list is an ALLOWLIST, so an
unrecognised or unparseable attachment keeps the body -- the fail-CLOSED
direction. A heredoc that really executes (`bash <<EOF`) keeps full coverage.

THE OVERRIDE IS ONE SPELLING, ON PURPOSE
----------------------------------------
`GH_ISSUE_NO_CLOSING_CONDITION=1` -- as a command-position assignment on the call
itself, or in the hook process's environment. Exactly the value `1`; `true`,
`yes` and `0` do NOT override, because an override with several spellings is an
override nobody can grep for. It is deliberately noisy in the transcript, which
is the point: "when did we skip this" has to have an answer.

🔴 IT IS DECIDED BY A QUOTE-AWARE WALK, NEVER BY A REGEX OVER THE RAW TEXT. The
first version matched an anchored regex against the whole command line, and its
anchor class included `\n`, `;` and a backtick -- every one of which occurs
INSIDE an ordinary issue body. So a body that merely MENTIONED the override
string on its own line, or after a semicolon, or in a markdown code span,
disarmed the gate silently; and this gate's own deny message TELLS the reader
that string, so an issue about this guard turned it off. `_shell_walk` now
credits an assignment only at a real command position, outside every quote, and
at substitution depth 0 -- a `` `VAR=1` `` inside a body is a substitution whose
assignment cannot reach `gh`'s environment anyway.

I/O CONTRACT
------------
Reads PreToolUse JSON on stdin (`tool_name`, `tool_input.command`), prints
`hookSpecificOutput.permissionDecision = "deny"` with a reason, exits 0. Exit 0
with no output is ALLOW. Exit codes other than 2 are non-blocking for PreToolUse,
so this file never relies on a non-zero status to mean anything.

FAIL-CLOSED IS SCOPED, NOT GLOBAL
---------------------------------
`bash-guard.py` denies on ANY internal failure because every Bash call is in its
scope. This hook is in scope for a much smaller family, so a blanket deny-on-
crash would block `gh pr checks` on an unrelated bug. Instead: a crash (including
a failed `guard_core` import) denies ONLY when the raw command text still looks
like an issue create by a pure regex that cannot itself fail. Everything else
exits 0. That fallback IS text-based, so on the crash path -- and only there -- a
mere mention can be denied; the message names the override.
"""
import collections
import json
import os
import re
import sys

sys.dont_write_bytecode = True

# --------------------------------------------------------------------------- #
# Constants — every literal the tests pin lives here, spelled once.
# --------------------------------------------------------------------------- #

# The heading that satisfies the gate. Two accepted names, one requirement --
# see the module docstring.
#
# 🔴 EXACTLY TWO HASHES, matching the clawgate gate's detector rather than
# inventing a second convention. One pattern then finds every specified body in
# either corpus, which is the whole value of a machine-readable claim; `###`
# is blocked and the deny message says so.
#
# Up to three leading spaces is CommonMark's ATX-heading indent allowance; a
# fourth makes it an indented code block, and a missing space after `##` makes it
# not a heading at all. The `(?![^\W\d_])` stops `## Closing conditions of sale`
# style run-ons from being rejected while `## Closing conditionality` — a
# different word — is not silently credited.
CLOSING_HEADING = re.compile(
    r"^ {0,3}##[ \t]+(?:closing[ \t]+conditions?|acceptance[ \t]+criteria)"
    r"(?![^\W\d_])[^\n]*$",
    re.IGNORECASE,
)

# Any level-1 or level-2 ATX heading — what ENDS the section opened above.
# A `###` does NOT end it: a sub-heading with content under it is content.
SECTION_END = re.compile(r"^ {0,3}#{1,2}[ \t]")

# A fenced code block opener/closer, CommonMark-ish: >=3 backticks or tildes,
# indented at most 3 spaces.
FENCE = re.compile(r"^ {0,3}(?P<char>`{3,}|~{3,})(?P<info>.*)$")

OVERRIDE_ENV = "GH_ISSUE_NO_CLOSING_CONDITION"
# One spelling. See the module docstring.
OVERRIDE_VALUE = "1"
# 🔴 THE WHOLE TOKEN, COMPARED EXACTLY. There is no regex over the raw command
# text any more: `_shell_walk` finds command-position words and this is the one
# it accepts. `…=1x`, `…_EXTRA=1` and `…=true` are different strings and do not
# override; the same bytes inside a quoted issue body are never a command word.
OVERRIDE_ASSIGNMENT = OVERRIDE_ENV + "=" + OVERRIDE_VALUE

# Where the predicate is DEFINED. Both spellings are printed: the deployed path
# is what the model can open right now, the repo path is what it edits. This gate
# points at the definition and never restates it — see
# scripts/tests/test_closing_condition_single_source.py.
DEF_DEPLOYED = "~/.claude/skills/clawgate/flows/task-authoring.md"
DEF_REPO = "devrc/claude/skills/clawgate/flows/task-authoring.md"

# The pure-regex fallback classifier used ONLY on the crash path. It must never
# raise and must never need a parse.
CRASH_LOOKS_LIKE_CREATE = re.compile(r"\bissue\s+create\b|/issues(?![/\w])")

# The cheap pre-filter. A command line naming neither of these cannot be an issue
# create this gate covers, and returns before anything is imported or parsed.
PREFILTER = re.compile(r"\bgh\b|\bcurl\b")

# A body file larger than this is not an issue body; reading it would only be a
# way to make a per-Bash-call hook slow. Treated as UNRESOLVED (i.e. blocked),
# never as "no closing condition" — different facts, different messages.
MAX_BODY_FILE_BYTES = 1024 * 1024

# Shell constructs whose VALUE this process cannot know.
#
# 🔴 THE TWO PREDICATES ARE DIFFERENT ON PURPOSE, inherited from the clawgate
# gate where collapsing them was a measured false positive: an issue body is
# PROSE, so a lone `$VAR` inside it is text and only a command substitution (or a
# value that is NOTHING BUT a variable reference) actually hides the content. A
# body-FILE argument is a PATH, where any `$` at all makes the target unknowable.
# A backtick is deliberately NOT a substitution marker for a body value: markdown
# code spans are everywhere in an issue body.
SUBSTITUTION = re.compile(r"\$\(")
WHOLE_VARIABLE = re.compile(r"^\s*\$\{?[A-Za-z_][A-Za-z0-9_]*\}?\s*$")

STDIN_NAMES = ("-", "/dev/stdin", "/proc/self/fd/0")


def opaque_value(value):
    """True when a `--body`-shaped argument hides its content."""
    return bool(SUBSTITUTION.search(value) or WHOLE_VARIABLE.match(value))


def opaque_path(value):
    """True when a `--body-file`-shaped argument names an unknowable path."""
    return "$" in value or "`" in value


# Escapes an ANSI-C-quoted body (`--body $'…\n…'`) carries as two characters.
_ANSI_C_ESCAPES = (("\\n", "\n"), ("\\r\\n", "\n"), ("\\t", "\t"))


def escape_expanded(value):
    """`value` with a surviving `$'…'` marker dropped and `\\n`/`\\t` decoded.

    🔴 A FALSE-POSITIVE KILLER, AND IT MOVES IN THE ALLOW DIRECTION ONLY.
    `shlex` — which the shared core tokenises with — does not implement bash's
    ANSI-C quoting, so `--body $'## Closing condition\\nthe queue drains'` arrives
    as the ONE line `$## Closing condition\\nthe queue drains`: the `$` defeats
    the heading anchor and the escape keeps the content on the same line, so a
    correctly-specified body DENIES. That is the shape that makes a gate get
    routed around.

    Used only as an EXTRA candidate beside the raw value, never as a replacement:
    a candidate can only ever let a command through, so decoding cannot invent a
    deny.

    🔴 GATED ON THE `$` PREFIX, AND THAT GATE IS THE FIX, NOT A DETAIL. Decoding
    unconditionally meant ANY body passed by appending
    `\\n## Closing condition\\ndone` — about thirty characters that bash leaves
    literal, so the issue GitHub renders carries no heading at all. Only a value
    that really arrived ANSI-C-quoted is decoded; a double-quoted `"…\\n…"` is
    one line to bash and is judged as one line here.
    """
    if not value.startswith("$"):
        return value
    out = value[1:]
    for esc, real in _ANSI_C_ESCAPES:
        out = out.replace(esc, real)
    return out


def _inline_variants(value):
    """The readings of one `--body` argument this gate will accept a heading in."""
    expanded = escape_expanded(value)
    return [value] if expanded == value else [value, expanded]


# --------------------------------------------------------------------------- #
# Flag tables — per ROUTE, because the same letter means different things.
#
# 🔴 `-F` IS TWO DIFFERENT FLAGS. On `gh issue create` it is `--body-file`; on
# `gh api` it is `--raw-field`. One shared table would either read a raw field as
# a file path or a file path as a field, and both directions produce a confident
# wrong verdict. Hence separate tables, named after the route they belong to.
# --------------------------------------------------------------------------- #
GH_ISSUE_BODY_FLAGS = ("--body", "-b")
GH_ISSUE_BODY_FILE_FLAGS = ("--body-file", "-F")
# Every `gh issue create` flag that consumes a separate value token, plus the gh
# globals that may precede the verb.
#
# 🔴 DERIVED FROM `gh issue create --help` ON gh 2.97.0, AND WRONG IN BOTH
# DIRECTIONS BEFORE THAT. A value-taking flag MISSING here puts its value in the
# operand list, which defeats the `ops[:2] == ["issue", "create"]` prefix test
# whenever the flag precedes the verb — `--type`, `--parent`, `--blocked-by`,
# `--blocking` and `--recover` were all missing, i.e. five one-flag bypasses.
# A BOOLEAN flag listed here eats the following token instead: `--editor`
# (`-e`, a boolean) was listed and would swallow whatever came next.
GH_ISSUE_VALUE_FLAGS = GH_ISSUE_BODY_FLAGS + GH_ISSUE_BODY_FILE_FLAGS + (
    "-a", "--assignee", "-l", "--label", "-m", "--milestone", "-p", "--project",
    "-T", "--template", "-t", "--title", "-R", "--repo", "--hostname",
    "--type", "--parent", "--blocked-by", "--blocking", "--recover",
)
# gh api's own value-taking flags.
#
# 🔴 `-F` IS `--field` AND `-f` IS `--raw-field`, NOT THE OTHER WAY ROUND. The
# pairing was inverted here, which matters because gh documents the `@<path>`
# read-from-file form as a `--field` (`-F`) feature ONLY. See
# `_resolve_gh_api_field`.
GH_API_AT_FILE_FLAGS = ("-F", "--field")
GH_API_RAW_FIELD_FLAGS = ("-f", "--raw-field")
GH_API_FIELD_FLAGS = GH_API_AT_FILE_FLAGS + GH_API_RAW_FIELD_FLAGS
GH_API_VALUE_FLAGS = GH_API_FIELD_FLAGS + (
    "-X", "--method", "-H", "--header", "--input", "--hostname", "-q", "--jq",
    "-t", "--template", "--cache", "-p", "--preview",
)

# curl flags that carry a request body.
CURL_DATA_FLAGS = (
    "-d", "--data", "--data-raw", "--data-ascii", "--data-binary",
    "--data-urlencode", "--json", "-F", "--form", "-T", "--upload-file",
)
CURL_METHOD_FLAGS = ("-X", "--request")
# curl flags that take a value we must not mistake for a URL or a body.
CURL_VALUE_FLAGS = CURL_DATA_FLAGS + CURL_METHOD_FLAGS + (
    "-H", "--header", "-o", "--output", "-u", "--user", "-A", "--user-agent",
    "-e", "--referer", "-b", "--cookie", "-c", "--cookie-jar", "--url",
    "--connect-timeout", "--max-time", "-m", "--retry", "--cacert", "--cert",
    "--key", "--proxy", "-x", "--write-out", "-w",
)

# The ONE path suffix that CREATES an issue. `…/issues/<n>` is a read,
# `…/issues/comments` is a comment — neither is in scope.
CREATE_PATH_SUFFIX = "/issues"

HELP_FLAGS = ("--help", "-h")
# See the docstring: `--web` opens a browser form instead of posting.
WEB_FLAGS = ("--web", "-w")


# --------------------------------------------------------------------------- #
# Heredocs
# --------------------------------------------------------------------------- #
# `(?<!<)` keeps a HERE-STRING (`<<<word`) out: it has no body and no terminator,
# so reading one as a heredoc would swallow the rest of the command line as
# "body text" and hand the gate prose it must not credit.
_HEREDOC_OPEN = re.compile(
    r"(?<!<)<<(?P<dash>-?)\s*(?P<tag>'[^']*'|\"[^\"]*\"|\\?[A-Za-z0-9_.-]+)")

# Commands whose heredoc body is INERT TEXT rather than something to execute.
# 🔴 An ALLOWLIST, and that is the safety argument: an attachment this table does
# not recognise — `bash`, `sh`, `python3`, `xargs`, a variable, an unparseable
# fragment, nothing at all — leaves the body in the command scan, so the failure
# direction is over-blocking rather than a silent pass.
INERT_HEREDOC_SINKS = frozenset({"cat", "tee", "echo", "printf"})
# Anything on the operator's own line AFTER the tag that would send the sink's
# output somewhere executable. `cat <<EOF | bash` is not inert.
#
# 🔴 A PROCESS SUBSTITUTION IS AN EXECUTION TOO. `cat <<'EOF' > >(bash)` really
# runs the body; with only `[|&;]` here it scored INERT and the body was blanked
# out of the invocation scan — a one-redirect bypass. `>(` and `<(` are both
# spelled out because the class is "the sink's bytes reach a shell", not "a pipe".
_PIPES_ONWARD = re.compile(r"[|&;]|[<>]\(")
# Separators that end the command the heredoc operator attaches to.
_SEG_SPLIT = re.compile(r"\n|;|&&|\|\||\||&|\(|\)|`|\$\(")
_ASSIGN_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _attached_command(text, operator_start):
    """The command word the heredoc operator at `operator_start` attaches to.

    Returns "" when it cannot be determined, which the caller reads as NOT inert.
    """
    head = _SEG_SPLIT.split(text[:operator_start])[-1]
    for tok in head.split():
        if _ASSIGN_TOKEN.match(tok):
            continue
        return os.path.basename(tok.strip("\"'"))
    return ""


# `body`   the text between the operator's line and the terminator
# `start`  where the body begins; `body_end` where the TERMINATOR LINE begins
# `after`  past the terminator line — what a scan must skip to leave the body
# `op`     the offset of the `<<` itself, which is what attributes a heredoc to
#          the COMMAND that opened it (see `command_spans`)
_Heredoc = collections.namedtuple(
    "_Heredoc", "body start body_end inert op after head rest")


def heredocs(text):
    """Every heredoc in `text` as a `_Heredoc`, in operator order.

    🔴 DELIBERATELY QUOTE-BLIND for the BODY, unlike `guard_core._scan`. The
    dominant real shape is

        gh issue create --body "$(cat <<'EOF'
        ## Closing condition
        …
        EOF
        )"

    where the operator sits INSIDE a double-quoted command substitution. A
    quote-tracking scanner is right about what bash executes and wrong about what
    this gate needs, which is "where is the prose". Being blind to quoting can
    only ever surface MORE candidate body text, and a candidate is only ever used
    to let a command THROUGH after the heading is found in it — so the blindness
    cannot invent a pass that the text does not contain.

    A body with no terminator line runs to end-of-text, which is bash's own
    behaviour and the direction that keeps the text visible.
    """
    out, n = [], len(text)
    # `cmd <<A <<B` opens TWO heredocs on ONE line, and bash reads their bodies
    # back to back in the order the operators appear — B's body starts where A's
    # terminator ended, not on the line after the command.
    cursor, line_end, consumed = None, None, []
    for m in _HEREDOC_OPEN.finditer(text):
        # An operator that appears INSIDE a body already consumed is prose, not
        # an operator. Without this, an issue body quoting `<<EOF` would mint a
        # spurious body — and a spurious body is the one thing that could
        # manufacture a false ALLOW.
        if any(lo <= m.start() < hi for lo, hi in consumed):
            continue
        tag = m.group("tag")
        if tag[:1] in ("'", '"'):
            tag = tag[1:-1]
        elif tag[:1] == "\\":
            tag = tag[1:]
        if not tag:
            continue
        strip_tabs = bool(m.group("dash"))
        nl = text.find("\n", m.end())
        if nl == -1:
            continue
        start = cursor if (line_end == nl and cursor is not None) else nl + 1
        rest_of_line = text[m.end():nl]
        line_end = nl
        i, lines = start, []
        # 🔴 TWO END OFFSETS, NOT ONE. `body_end` is where the TERMINATOR LINE
        # begins; `after` is past it. Reporting `after` as the body's end made
        # `scrub_inert_heredocs` blank the terminator too, so the downstream
        # scanner saw an UNTERMINATED heredoc and swallowed the rest of the line —
        # a correctly-specified create then denied with "no closing condition".
        body_end = n
        while i < n:
            j = text.find("\n", i)
            line = text[i:j] if j != -1 else text[i:]
            nxt = (j + 1) if j != -1 else n
            # 🔴 `<<-` strips leading TABS from the body lines as well as from
            # the terminator. Stripping only the terminator leaves every body
            # line carrying a tab, which turns `\t## Closing condition` into a
            # four-space-indented code line and makes the heading invisible.
            probe = line.lstrip("\t") if strip_tabs else line
            if probe.rstrip("\r") == tag:
                body_end = i
                i = nxt
                break
            lines.append(probe)
            i = nxt
        consumed.append((start, i))
        cursor = i
        head = _SEG_SPLIT.split(text[:m.start()])[-1]
        inert = (
            _attached_command(text, m.start()) in INERT_HEREDOC_SINKS
            and not _PIPES_ONWARD.search(rest_of_line)
        )
        out.append(_Heredoc("\n".join(lines), start, body_end, inert,
                            m.start(), i, head, rest_of_line))
    return out


def heredoc_bodies(text):
    """Every heredoc body, inert or not — the body-resolution view."""
    return [h.body for h in heredocs(text)]


# A redirection or a `tee` argument naming the file a heredoc is about to write.
_REDIR = re.compile(r"^\d*>>?$")


def heredoc_bodies_writing(text, path):
    """Bodies of the heredocs on this line whose own command WRITES `path`.

    🔴 THE ATTRIBUTION THAT REPLACED "any heredoc anywhere on the line". An
    unreadable `--body-file p.md` is legitimately rescued by
    `cat > p.md <<'EOF' … EOF` earlier on the line — the file does not exist yet
    when a PreToolUse hook runs, and denying that shape denies a correctly
    specified create. Matching on the PATH rather than on adjacency is what keeps
    an UNRELATED heredoc from doing the same job.
    """
    want = os.path.abspath(os.path.expanduser(path))
    out = []
    for h in heredocs(text):
        toks = (h.head + " " + h.rest).split()
        is_tee = bool(toks) and os.path.basename(toks[0].strip("\"'")) == "tee"
        targets, redir = [], False
        for tok in toks[1:] if is_tee else toks:
            bare = tok.strip("\"'")
            if _REDIR.match(bare):
                redir = True
                continue
            if bare.startswith(">"):
                targets.append(bare.lstrip(">"))
                continue
            if redir:
                targets.append(bare)
                redir = False
                continue
            if is_tee and not bare.startswith("-"):
                targets.append(bare)
        if any(t and os.path.abspath(os.path.expanduser(t.strip("\"'"))) == want
               for t in targets):
            out.append(h.body)
    return out


def scrub_inert_heredocs(text):
    """`text` with INERT heredoc bodies blanked, for the INVOCATION scan only.

    🔴 THIS IS NOT `guard_core`'s REVERTED `_strip_message_text()` HELPER, and
    the difference is what makes it safe. That one decided which bytes of an
    ARGUMENT were inert message text, using regexes over quoting it could not
    parse, and three audit rounds each found a fresh shape where a genuinely
    EXECUTING command was blanked. This one blanks nothing inside an argument: it
    removes only a heredoc body whose operator attaches to a command in a
    four-entry allowlist of text sinks, with nothing piping that sink's output
    onward. Anything else — including anything it fails to parse — is kept.

    The terminator line and the line structure are preserved (each body becomes
    the same number of blank lines), so the downstream scanner still consumes the
    heredoc exactly as it would have.

    Body CONTENT is unaffected: `heredoc_bodies` reads the ORIGINAL text, so a
    `--body "$(cat <<'EOF' … EOF)"` is still resolved from its heredoc.
    """
    spans = [(h.start, h.body_end) for h in heredocs(text) if h.inert]
    if not spans:
        return text
    out, prev = [], 0
    for lo, hi in spans:
        out.append(text[prev:lo])
        out.append("\n" * text.count("\n", lo, hi))
        prev = hi
    out.append(text[prev:])
    return "".join(out)


# --------------------------------------------------------------------------- #
# A quote-aware walk of the command line
#
# 🔴 THE ONE THING A REGEX OVER RAW TEXT CANNOT DO IS TELL A COMMAND FROM PROSE,
# and both of the defects this exists to fix were exactly that mistake:
#
#   * the override was matched by a regex whose "command boundary" class held
#     `\n`, `;` and a backtick — all of which appear inside an ordinary issue
#     body, so quoting the override string in a body disarmed the gate;
#   * every body-resolution fallback read `heredoc_bodies(<the whole line>)`, so
#     a heredoc belonging to some OTHER command answered for a create.
#
# One walk answers both. It tracks single quotes, double quotes, backslash
# escapes, `$( … )` and backtick substitution depth, and it steps OVER heredoc
# bodies (whose bytes are data, not command text, and whose stray quotes would
# otherwise corrupt the state for everything after them).
# --------------------------------------------------------------------------- #
# Words that leave the NEXT word still in command position.
_CMD_KEEPERS = frozenset({"!", "then", "else", "elif", "do", "time", "command",
                          "{", "}", "nohup"})
# Words after which further `NAME=value` words are still assignments.
_ASSIGN_INTRODUCERS = frozenset({"export", "env", "declare", "local",
                                 "readonly", "typeset"})
_ASSIGN_WORD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SEPARATOR_CHARS = ";&|\n"
# A word ends at whitespace, at a separator, at a redirection, or at the start of
# a substitution. Quotes are consumed as part of the word so `'a b'` stays one.
_WORD_BREAK = " \t\r\n;&|()<>`"


def _read_word(text, i, n):
    """`(word, next index)` for the shell word starting at `i`."""
    out, quote = [], None
    while i < n:
        c = text[i]
        if quote:
            if c == "\\" and quote == '"' and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
                i += 1
                continue
            out.append(c)
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            out.append(text[i + 1])
            i += 2
            continue
        if c in _WORD_BREAK or (c == "$" and text[i + 1:i + 2] == "("):
            break
        out.append(c)
        i += 1
    return "".join(out), i


def _shell_walk(text):
    """`(spans, override)` — top-level command spans, and the override verdict.

    `spans` are `[lo, hi)` slices of `text`, one per TOP-LEVEL command segment,
    split on unquoted `;`/`&`/`|`/newline and on a subshell paren. A heredoc's
    body is kept INSIDE the span of the command that opened it, because that is
    exactly the relationship body resolution needs.

    `override` is True only for a bare `GH_ISSUE_NO_CLOSING_CONDITION=1` word in
    COMMAND POSITION at substitution depth 0 — the assignment bash would actually
    put in `gh`'s environment. Inside `$( … )` or a backtick it is a different
    process's environment and does not count; inside a quote it is prose.
    """
    n = len(text)
    skip = {}
    for h in heredocs(text):
        skip[h.start] = h.after
    spans, seg_lo = [], 0
    stack = []            # (saved quote, closing char) per open substitution
    quote, at_cmd, override, i = None, True, False, 0
    while i < n:
        if i in skip:
            i = skip[i]
            continue
        c = text[i]
        if quote == "'":
            if c == "'":
                quote = None
            i += 1
            continue
        if quote == '"':
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == "$" and text[i + 1:i + 2] == "(":
                stack.append((quote, ")"))
                quote, at_cmd, i = None, True, i + 2
                continue
            if c == "`":
                stack.append((quote, "`"))
                quote, at_cmd, i = None, True, i + 1
                continue
            if c == '"':
                quote = None
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            at_cmd = False
            i += 2
            continue
        if c == "$" and text[i + 1:i + 2] == "(":
            stack.append((quote, ")"))
            at_cmd, i = True, i + 2
            continue
        if stack and ((c == ")" and stack[-1][1] == ")")
                      or (c == "`" and stack[-1][1] == "`")):
            quote = stack.pop()[0]
            at_cmd, i = False, i + 1
            continue
        if c == "`":
            stack.append((quote, "`"))
            at_cmd, i = True, i + 1
            continue
        if c in ("'", '"'):
            quote, at_cmd, i = c, False, i + 1
            continue
        if c in _SEPARATOR_CHARS or (not stack and c in "()"):
            if not stack:
                # 🔴 A newline that OPENS a heredoc body ends the command, but
                # the body belongs to it: the span runs past the terminator so
                # `heredoc_bodies(span)` still finds it.
                end = skip.get(i + 1) if c == "\n" else None
                if end is not None:
                    spans.append((seg_lo, i))
                    seg_lo, i, at_cmd = end, end, True
                    continue
                spans.append((seg_lo, i))
                seg_lo = i + 1
            at_cmd, i = True, i + 1
            continue
        if c.isspace():
            i += 1
            continue
        word, j = _read_word(text, i, n)
        if at_cmd and not stack:
            if _ASSIGN_WORD.match(word):
                override = override or word == OVERRIDE_ASSIGNMENT
            elif word not in _ASSIGN_INTRODUCERS and word not in _CMD_KEEPERS:
                at_cmd = False
        else:
            at_cmd = False
        i = max(j, i + 1)
    spans.append((seg_lo, n))
    return [(lo, hi) for lo, hi in spans if text[lo:hi].strip()], override


def command_spans(text):
    """The `[lo, hi)` slice of every top-level command segment in `text`."""
    return _shell_walk(text)[0]


# --------------------------------------------------------------------------- #
# The closing-condition detector
# --------------------------------------------------------------------------- #
def _fence_states(body):
    """`[(line, inside-a-fenced-code-block), …]` for `body`.

    A fence DELIMITER line is reported as inside, so it can never be read as a
    heading and never ends a section.
    """
    out = []
    fence_char, fence_len = None, 0
    for line in body.splitlines():
        m = FENCE.match(line)
        if m:
            run = m.group("char")
            if fence_char is None:
                # An opening fence. Its info string may name a language; a
                # CLOSING fence may not have one, which is why the state is
                # tracked rather than toggled on every fence-looking line.
                fence_char, fence_len = run[0], len(run)
            elif run[0] == fence_char and len(run) >= fence_len and not m.group("info").strip():
                fence_char, fence_len = None, 0
            out.append((line, True))
            continue
        out.append((line, fence_char is not None))
    return out


def has_closing_condition(body):
    """True iff `body` carries a real closing-condition heading WITH CONTENT.

    🔴 TWO CONDITIONS, NOT ONE, AND THE SECOND IS WHY THIS IS NOT THE CLAWGATE
    DETECTOR. A bare heading with nothing under it is the exact near-miss a model
    produces when it pastes a template it did not fill in: the word is on the
    page, the object still cannot be closed. So the section must hold at least
    one non-blank line before the next `#`/`##` heading.

    🔴 A HEADING INSIDE A FENCED CODE BLOCK DOES NOT COUNT. The template this
    gate's own message prints is fenced; a draft that quotes the template while
    forgetting to fill it in is exactly the near-miss this has to catch, and a
    naive substring search passes it. Same class as RULES.md's "a field that
    exists in a DTO is not a guard".

    🔴 IT DOES NOT AND CANNOT CHECK THE SEMANTICS. See the module docstring: most
    of a measured briefed-arm corpus wrote the REMEDY under this heading rather
    than a condition that ends the issue, and every one of those passes here.
    """
    if not isinstance(body, str) or not body:
        return False
    lines = _fence_states(body)
    for idx, (line, fenced) in enumerate(lines):
        if fenced or not CLOSING_HEADING.match(line):
            continue
        for nxt, nxt_fenced in lines[idx + 1:]:
            if not nxt_fenced and SECTION_END.match(nxt):
                break
            if nxt.strip():
                return True
    return False


# --------------------------------------------------------------------------- #
# Command classification
# --------------------------------------------------------------------------- #
def _operands(argv, value_flags):
    """argv's non-flag tokens, with each known value flag's value skipped."""
    out, skip = [], False
    for tok in argv[1:]:
        if skip:
            skip = False
            continue
        if tok.startswith("-") and tok != "-":
            if tok in value_flags:
                skip = True
            continue
        out.append(tok)
    return out


def _has_flag(argv, flags, value_flags):
    """True when one of `flags` appears as a FLAG, not as some flag's value."""
    skip = False
    for tok in argv[1:]:
        if skip:
            skip = False
            continue
        if tok in value_flags:
            skip = True
            continue
        if tok in flags:
            return True
    return False


def _is_gh(argv):
    return bool(argv) and os.path.basename(argv[0]) == "gh"


def is_gh_issue_create(argv):
    """True for a `gh … issue create …` argv.

    Keyed on the OPERAND LIST rather than on argv[1:3], because a flag with a
    value can precede the verb (`gh -R owner/x issue create`) and `issue` is also
    a legal value of some other flag. Because the tokens come from a real lexer, a
    quoted `"gh issue create"` is ONE token and never matches — the
    mention-vs-invocation requirement.

    🔴 THE VERB MUST BE THE FIRST TWO OPERANDS, NOT AN ADJACENT PAIR ANYWHERE.
    The adjacency reading was written first and was a MEASURED false positive:
    `gh pr list  # gh issue create needs a closing condition` tokenises with `#`
    as an ordinary word (shlex does not strip shell comments), so `issue` and
    `create` appear adjacent in the operand list and a trailing COMMENT was
    classified as an invocation. gh takes no positional argument before the verb,
    so the prefix reading is exact rather than merely narrower.
    """
    if not _is_gh(argv):
        return False
    ops = _operands(argv, GH_ISSUE_VALUE_FLAGS)
    return ops[:2] == ["issue", "create"]


def _url_path(token):
    """The path component of a URL-ish token, or None."""
    if "://" not in token:
        return None
    rest = token.split("://", 1)[1]
    slash = rest.find("/")
    path = rest[slash:] if slash != -1 else "/"
    for sep in ("?", "#"):
        cut = path.find(sep)
        if cut != -1:
            path = path[:cut]
    return path.rstrip("/") or "/"


def _api_path(token):
    """Normalise a `gh api` endpoint operand to a leading-slash path."""
    path = _url_path(token)
    if path is None:
        path = token.split("?", 1)[0].split("#", 1)[0]
        if not path.startswith("/"):
            path = "/" + path
        path = path.rstrip("/") or "/"
    return path


def _creates_issue_path(path):
    return path.endswith(CREATE_PATH_SUFFIX)


def _flag_value(argv, names):
    """The FIRST value of `--flag value` / `--flag=value`, or None."""
    vals = _flag_values(argv, names)
    return vals[0] if vals else None


def is_gh_api_issue_create(argv):
    """True for a `gh api …/issues` POST.

    gh api defaults to GET, and to POST as soon as any field is supplied — so a
    create is "explicit POST" OR "fields, no explicit method". An explicit
    non-POST method is out of scope, and a plain `gh api repos/x/y/issues` is the
    LIST endpoint.
    """
    if not _is_gh(argv):
        return False
    ops = _operands(argv, GH_API_VALUE_FLAGS)
    if not ops or ops[0] != "api":
        return False
    if not any(_creates_issue_path(_api_path(o)) for o in ops[1:]):
        return False
    method = _flag_value(argv, ("-X", "--method"))
    if method is not None:
        return method.upper() == "POST"
    return bool(_flag_values(argv, GH_API_FIELD_FLAGS)
                or _flag_values(argv, ("--input",)))


def _curl_parts(argv):
    """(method, [url paths], [data values]) for a curl argv.

    🔴 EVERY FLAG READ THROUGH `_match_flag`, NOT BY EXACT-TOKEN EQUALITY. This
    used to test `tok in CURL_DATA_FLAGS`, so `--request=POST`, `--data=…`,
    `--data-raw=…`, `--json=…` and `--url=…` matched NOTHING: the call was not
    even classified as a create, and the gate never ran on it. The order below is
    load-bearing — the specific tables are consulted before the catch-all
    `CURL_VALUE_FLAGS`, which is a superset of both.
    """
    method, paths, data = None, [], []
    i = 1
    while i < len(argv):
        tok = argv[i]
        hit = _match_flag(argv, i, CURL_METHOD_FLAGS)
        if hit is not None:
            if hit[1] is not None:
                method = hit[1].upper()
            i = hit[2]
            continue
        hit = _match_flag(argv, i, CURL_DATA_FLAGS)
        if hit is not None:
            if hit[1] is not None:
                data.append(hit[1])
            i = hit[2]
            continue
        hit = _match_flag(argv, i, ("--url",))
        if hit is not None:
            p = _url_path(hit[1]) if hit[1] else None
            if p:
                paths.append(p)
            i = hit[2]
            continue
        hit = _match_flag(argv, i, CURL_VALUE_FLAGS)
        if hit is not None:
            i = hit[2]
            continue
        if not tok.startswith("-") or tok == "-":
            p = _url_path(tok)
            if p:
                paths.append(p)
        i += 1
    return method, paths, data


def is_curl_issue_create(argv):
    """True for a curl that POSTs a NEW issue.

    `…/issues/<n>` (a read) and `…/issues/comments` are out of scope by path, and
    an explicit non-POST method is out of scope by method. A curl to the create
    path with neither an explicit method nor any data flag is a GET of the issue
    list — also out of scope.
    """
    if not argv or os.path.basename(argv[0]) != "curl":
        return False
    method, paths, data = _curl_parts(argv)
    if not any(_creates_issue_path(p) for p in paths):
        return False
    if method is not None:
        return method == "POST"
    return bool(data)


def is_exempt_invocation(argv, route="gh-issue"):
    """True for a create-shaped argv that STRUCTURALLY cannot post an issue.

    🔴 Structural exemptions, not convenience ones, and both are recorded in the
    docstring's uncovered-routes list rather than hidden:

      * `--help`/`-h`, or a leading `help` operand — cobra prints usage and exits
        WITHOUT calling the command, so the argv cannot reach the API. `--body`
        alongside `--help` changes nothing: help still wins.
      * `--web`/`-w` — gh opens the browser's new-issue form and exits. Nothing
        is posted by this process; a human fills in a form this gate cannot see.

    🔴 Scoped to `gh` BY NAME. `-w` is curl's `--write-out` and `-h` is not a
    curl flag at all; curl posts the request regardless. Letting this predicate
    answer for a curl create would hand every curl caller a one-word bypass —
    the exact hole the clawgate gate's first `is_help_invocation` had.

    🔴 AND SCOPED BY ROUTE, because `gh issue create` and `gh api` do not share a
    flag table. Judging a `gh api` argv against the ISSUE table left gh api's own
    value flags (`-q`, `--jq`, `-H`, `--cache`, `--input`) unskipped, so a VALUE
    that happened to be `-h` or `-w` — a jq expression, a header — exempted the
    create. `route` is already known at the only call site.
    """
    if not _is_gh(argv):
        return False
    api = route == "gh-api"
    value_flags = GH_API_VALUE_FLAGS if api else GH_ISSUE_VALUE_FLAGS
    # `--web` is a `gh issue create` flag; `gh api` has no such thing, and `-w`
    # there is not a flag at all.
    exempting = HELP_FLAGS if api else HELP_FLAGS + WEB_FLAGS
    if _has_flag(argv, exempting, value_flags):
        return True
    return _operands(argv, value_flags)[:1] == ["help"]


# --------------------------------------------------------------------------- #
# Body resolution
# --------------------------------------------------------------------------- #
UNRESOLVED_OPAQUE = "the argument is a shell substitution this gate cannot evaluate"
UNRESOLVED_STDIN = "the body is piped in on stdin, where a PreToolUse hook cannot read it"
UNRESOLVED_UNREADABLE = "the body-file path could not be read"
UNRESOLVED_NONE = "the command names no body this gate can read"
UNRESOLVED_JSON = "the request payload is not JSON this gate can parse"


def _match_flag(argv, i, names):
    """`(name, value, next index)` when `argv[i]` carries one of `names`' values.

    🔴 ALL THREE SPELLINGS THE REAL TOOLS ACCEPT, because a flag table that knows
    only one of them is a table with holes in the other two. `--flag value` is
    obvious; `--flag=value` was already handled here and NOT in `_curl_parts`, so
    `--request=POST --data=…` was not classified as a create at all; and an
    ATTACHED short flag (`-b<body>`, `-F<path>`, `-XPOST`, `-d<payload>` — every
    one of them valid) was handled nowhere, so a CORRECTLY specified
    `gh issue create -b'## Closing condition …'` denied with "CANNOT SEE THE
    BODY", the most confusing message this gate can print.

    🔴 NO `--` EXCLUSION IS NEEDED HERE, AND ONE WAS DELETED FOR SAYING
    OTHERWISE. The first draft carried `and not tok.startswith("--")` with a
    comment claiming it stopped `--data-raw` being read as `-d` + a value. It
    cannot: `name[1] != "-"` already means a token beginning `--` can never
    satisfy `tok.startswith(name)`. A mutation sweep found the clause SURVIVED
    every test because it is provably unreachable — a guard that reads as
    coverage while providing none, which is worse than no guard.
    """
    tok = argv[i]
    if tok in names:
        return (tok, argv[i + 1], i + 2) if i + 1 < len(argv) else (tok, None, i + 1)
    for name in names:
        if tok.startswith(name + "="):
            return name, tok[len(name) + 1:], i + 1
        if (len(name) == 2 and name[0] == "-" and name[1] != "-"
                and len(tok) > 2 and tok.startswith(name)):
            return name, tok[len(name):], i + 1
    return None


def _flag_values(argv, names):
    """Values of every spelling of `names` in `argv`, in argv order."""
    out, i = [], 1
    while i < len(argv):
        hit = _match_flag(argv, i, names)
        if hit is None:
            i += 1
            continue
        _, value, i = hit
        if value is not None:
            out.append(value)
    return out


def _read_body_file(path):
    path = os.path.expanduser(path)
    if os.path.getsize(path) > MAX_BODY_FILE_BYTES:
        raise ValueError("body file too large")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _resolve_inline(value, text):
    """([body, …], reason-or-None) for a literal `--body` argument."""
    # 🔴 ONE ORDERING RULE, APPLIED ONCE: a heading PRESENT IN THE LITERAL
    # ARGUMENT always wins. It was stated below for the opacity branch and NOT
    # for this one, so a correctly specified body that merely QUOTES a heredoc —
    # `## Closing condition … ```cat <<'EOF' > x``` `, i.e. any issue documenting
    # a shell snippet — was resolved through the quoted heredoc instead of its
    # own text and denied. `heredocs` is deliberately quote-blind, so it cannot
    # tell a documented operator from a real one; the operator's own bytes can.
    variants = _inline_variants(value)
    if any(has_closing_condition(v) for v in variants):
        return variants, None
    inner = heredoc_bodies(value)
    if inner:
        # 🔴 THE ORIGINAL TEXT IS THE AUTHORITATIVE READING WHEN THE ARGUMENT
        # ITSELF CARRIES A HEREDOC. `argv` comes from a copy of the command line
        # whose inert heredoc bodies were blanked for the invocation scan, so
        # `--body "$(cat <<'EOF' … EOF)"` — the shape this gate's own message
        # recommends — resolves through `value` to a run of EMPTY lines and a
        # correctly-specified create denied. `inner` survives only as the
        # fallback for a value carrying a heredoc `text` somehow does not.
        #
        # 🔴 KNOWN LOOSENESS, recorded rather than hidden: `heredoc_bodies(text)`
        # is every heredoc on the LINE, so a command line that both writes a
        # well-specified heredoc elsewhere AND creates an issue whose own body is
        # a different heredoc passes on the strength of the former. This is the
        # same "aggregate the candidates" trade the clawgate gate makes, and it
        # is reachable only when the create's own `--body` carries a heredoc
        # operator — a plain `--body 'nope'` beside a good heredoc still DENIES
        # (pinned by test_an_unrelated_heredoc_does_not_satisfy_a_create).
        # An earlier version narrowed this to the all-blank case; a mutation
        # sweep showed no test could tell the two apart, i.e. the narrowing was
        # an unreachable guard reading as coverage. Removed rather than kept.
        #
        # 🔴 THE "KNOWN LOOSENESS" ABOVE IS NOW CLOSED, and this comment is kept
        # only so nobody re-derives it: `text` here is the create's OWN command
        # segment, not the whole line, so a well-specified heredoc belonging to a
        # different command is not in it.
        return (heredoc_bodies(text) or inner), None
    # 🔴 AN EMPTY VALUE IS AN ARTIFACT OF THE PARSE, NOT A BODY. `guard_core`'s
    # scanner LIFTS `$( … )` out of the segment it appears in, so
    # `--body "$(cat <<'EOF' … EOF)"` — the shape the deny message itself
    # recommends — arrives here as the empty string. Returning it as a readable
    # body made a correctly-specified create deny with "no closing condition",
    # which is both wrong and the most confusing possible message. Reporting
    # NOTHING SEEN instead lets `body_candidates` fall through to the heredoc on
    # the line, where the text actually is; if there is no heredoc either, the
    # command still denies — as "cannot see the body", which is the true fact.
    if not value.strip():
        return [], None
    # The literal-heading test that would sit here has been hoisted to the TOP of
    # this function — see the note there. `--body "## Closing condition\n…
    # $(date)"` is readable enough: whatever the substitution expands to, the
    # heading is in the bytes the operator typed, so consulting opacity first
    # would report "cannot see the body" about a body that is right there.
    if opaque_value(value):
        # Last chance: the operator may have opened the heredoc outside the
        # quoted argument (`--body "$(cat <<EOF)"` splits across both shapes).
        #
        # 🔴 GATED ON THE ARGUMENT ITSELF NAMING A HEREDOC. Without the `<<`
        # test this branch rescued `--body "$(gen.sh)"` — the exact shape the
        # docstring promises to BLOCK — from any heredoc that happened to be in
        # scope. A substitution that opens no heredoc has nothing here to read.
        if "<<" in value:
            outer = heredoc_bodies(text)
            if outer:
                return outer, None
        return [], UNRESOLVED_OPAQUE
    return variants, None


def _resolve_file(value, text, full=None):
    """([body, …], reason-or-None) for a `--body-file` argument.

    `text` is the create's OWN command segment; `full` is the whole command line,
    used ONLY for the path-matched write-rescue below.
    """
    full = text if full is None else full
    if value in STDIN_NAMES:
        # A body-file of `-` fed by a heredoc on THIS command IS readable; fed by
        # a pipe, or by some other command's heredoc, it is not.
        inner = heredoc_bodies(text)
        return (inner, None) if inner else ([], UNRESOLVED_STDIN)
    if opaque_path(value):
        return [], UNRESOLVED_OPAQUE
    try:
        return [_read_body_file(value)], None
    except Exception:
        # The file may be about to be written by a heredoc earlier on the same
        # command line, which this gate CAN read — but ONLY a heredoc that writes
        # THIS path. Accepting any heredoc anywhere let an unrelated
        # `cat > /tmp/plan.md <<'EOF'` answer for an unreadable `--body-file`.
        inner = heredoc_bodies_writing(full, value)
        return (inner, None) if inner else ([], UNRESOLVED_UNREADABLE)


def _resolve_json_payload(payloads):
    """([body, …], reason-or-None) for JSON request payloads."""
    bodies, failed = [], False
    for payload in payloads:
        try:
            obj = json.loads(payload)
        except Exception:
            failed = True
            continue
        if isinstance(obj, dict) and isinstance(obj.get("body"), str):
            bodies.append(obj["body"])
        else:
            failed = True
    if bodies:
        return bodies, None
    return [], (UNRESOLVED_JSON if failed else UNRESOLVED_NONE)


def _resolve_curl_data(value, text, full=None):
    """([body, …], reason-or-None) for one curl data argument."""
    if value.startswith("@"):
        # 🔴 `-d @file` / `-d @-` still has to be JSON-PARSED afterwards.
        # Returning the file's raw bytes as a body candidate is a real defect: a
        # payload `{"body":"## Closing condition\n…"}` carries the heading only
        # as an ESCAPED \n, so the line-based detector never sees a heading and a
        # correctly-specified create is denied.
        payloads, why = _resolve_file(value[1:], text, full)
        if why:
            return [], why
    else:
        payloads = heredoc_bodies(value) or ([] if opaque_value(value) else [value])
        if not payloads:
            outer = heredoc_bodies(text)
            if not outer:
                return [], UNRESOLVED_OPAQUE
            payloads = outer
    return _resolve_json_payload(payloads)


def _resolve_gh_api_field(value, text, at_file, full=None):
    """([body, …], reason-or-None) for one `gh api -F/-f` field argument.

    Only a field literally named `body` is a body; `title=…` and `labels[]=…` are
    not, and returning them would let a title carry the heading.

    🔴 `@` IS A `-F`/`--field` FEATURE ONLY — `at_file` carries that, and it is
    two bugs in one place. Applying `@`-file semantics to `-f`/`--raw-field` (a)
    read a file gh would have sent verbatim, and (b) denied a legitimate body
    that merely STARTS with an @mention — an ordinary way to open an issue — as
    an unreadable path.
    """
    if "=" not in value:
        return [], None
    key, val = value.split("=", 1)
    if key.strip() != "body":
        return [], None
    if at_file and val.startswith("@"):
        return _resolve_file(val[1:], text, full)
    return _resolve_inline(val, text)


def body_candidates(argv, text, route, full=None):
    """([every body text this gate could read], reason the rest were unreadable).

    The reason is None when everything named was resolved. Aggregating rather
    than picking ONE source is deliberate: a command may legitimately name a
    body-file that a heredoc on the same line is about to write, and a candidate
    is only ever used to let the command THROUGH.

    🔴 `text` IS THIS CREATE'S OWN COMMAND SEGMENT, NOT THE COMMAND LINE. That is
    the whole of the B2/B3 fix and it is a change of MEANING, not of degree —
    aggregation across sources on ONE command is deliberate, aggregation across
    COMMANDS was a bug. `full` is threaded through for the single place that
    still legitimately looks line-wide: a heredoc writing an as-yet-unwritten
    `--body-file` path.
    """
    full = text if full is None else full
    cands, reason = [], None

    def take(pair):
        nonlocal reason
        got, why = pair
        cands.extend(got)
        if why and reason is None:
            reason = why

    if route == "curl":
        _, _, data = _curl_parts(argv)
        for value in data:
            take(_resolve_curl_data(value, text, full))
    elif route == "gh-api":
        for value in _flag_values(argv, GH_API_AT_FILE_FLAGS):
            take(_resolve_gh_api_field(value, text, True, full))
        for value in _flag_values(argv, GH_API_RAW_FIELD_FLAGS):
            take(_resolve_gh_api_field(value, text, False))
        for value in _flag_values(argv, ("--input",)):
            if value in STDIN_NAMES:
                inner = heredoc_bodies(text)
                take(_resolve_json_payload(inner) if inner else ([], UNRESOLVED_STDIN))
                continue
            payloads, why = _resolve_file(value, text, full)
            take(([], why) if why else _resolve_json_payload(payloads))
    else:
        for value in _flag_values(argv, GH_ISSUE_BODY_FLAGS):
            take(_resolve_inline(value, text))
        for value in _flag_values(argv, GH_ISSUE_BODY_FILE_FLAGS):
            take(_resolve_file(value, text, full))

    if not cands and reason is None:
        # No body argument this gate could read. A heredoc on THIS COMMAND is
        # still the operator's body — that is how the shape the deny message
        # itself recommends, `--body "$(cat <<'EOF' … EOF)"`, resolves, since the
        # shared core lifts the substitution and the argument arrives empty. If
        # this command opened no heredoc, the gate has seen nothing.
        outer = heredoc_bodies(text)
        if outer:
            cands = outer
        else:
            reason = UNRESOLVED_NONE
    return cands, reason


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #
_WHY = (
    "Your RULES say a work item may only be filed once you can name the "
    "condition that ENDS it and who or what checks it -- name neither and the "
    "honest move is to say so in your reply rather than mint an object nobody "
    "can close.\n"
    "Definition (the single source -- point at it, do not restate it):\n"
    "    " + DEF_DEPLOYED + "   -> question 1\n"
    "    (repo: " + DEF_REPO + ")"
)

_HOW = (
    "Put this in the body, filled in:\n"
    "````\n"
    "## Closing condition\n"
    "<the observable end-state that ends this issue>\n"
    "Checked by: <a command that exits 0 / a merged PR / a cleared alert / a "
    "NAMED person reading NAMED evidence>\n"
    "````\n"
    "`## Acceptance criteria` is accepted as the same heading under its other "
    "name."
)

_LIMIT = (
    "What this gate checked: the heading is present, at level 2, outside a code "
    "fence, with at least one non-blank line under it. It CANNOT check that what "
    "you wrote is an observable end-state rather than a restatement of the fix -- "
    "that is not machine-checkable, and in the run that motivated this gate most "
    "bodies that cleared the heading still described the remedy. Passing here is "
    "a floor, not a verdict."
)

_ESCAPES = (
    "Escape hatches, both deliberate:\n"
    "  * a body that already carries the heading passes this gate SILENTLY -- the "
    "one-liner still works, it just has to say what ends the issue;\n"
    "  * for this one call only, prefix it with " + OVERRIDE_ENV + "=" +
    OVERRIDE_VALUE + " (greppable on purpose)."
)


def missing_text():
    return (
        "This creates a GitHub issue whose body names no closing condition.\n\n"
        + _WHY + "\n\n" + _HOW + "\n\n"
        "The heading must be a level-2 ATX heading -- `## Closing condition` or "
        "`## Acceptance criteria` (case-insensitive, trailing text allowed) -- "
        "and it must have CONTENT under it. `###`, bold text, a heading inside a "
        "``` fence, and a heading with nothing beneath it all fail, because none "
        "of them is a filled-in section.\n\n" + _LIMIT + "\n\n" + _ESCAPES
    )


def unseeable_text(reason):
    return (
        "This creates a GitHub issue, but this gate CANNOT SEE THE BODY (" +
        reason + "), so it cannot check for a closing condition.\n\n"
        "Blocking rather than passing it through, deliberately: a gate that fails "
        "open on a body it cannot read is walkable by changing the SHAPE of the "
        "call instead of its content -- a spelled guard, not a structural one "
        "(RULES.md).\n\n"
        "Hand the body over where the gate can read it:\n"
        "    --body '<the full markdown>'\n"
        "    --body-file <a real path>            (write it with the Write tool first)\n"
        "    --body \"$(cat <<'EOF' … EOF)\"        (a heredoc IS readable)\n\n"
        + _WHY + "\n\n" + _HOW + "\n\n" + _ESCAPES
    )


def crash_text(exc):
    return (
        "gh-issue-closing-condition-guard crashed while checking this command (" +
        str(exc) + "). It looks like a GitHub issue create, so it is denied "
        "rather than passed through unchecked.\n\n"
        "This crash path classifies by RAW TEXT, so a command that only MENTIONS "
        "`issue create` can land here even though the normal path would allow it. "
        "Report it; the command text is what reproduces it. To proceed now, "
        "prefix the call with " + OVERRIDE_ENV + "=" + OVERRIDE_VALUE + ".\n\n"
        "The definition: " + DEF_DEPLOYED
    )


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #
def override_requested(text, env):
    """True when the operator explicitly disarmed the gate.

    Two channels, one spelling. The inline assignment is found by a quote-aware
    walk (see `_shell_walk`), never by a regex over raw text; the process
    environment is read exactly, so `=true` / `=0` / an empty value are NOT
    overrides.

    🔴 A FAILURE HERE FAILS CLOSED. This is also called from the crash path, and
    an exception escaping it would leave the hook exiting non-zero — a silent
    ALLOW for PreToolUse. "Could not tell" means "not overridden".
    """
    if env.get(OVERRIDE_ENV) == OVERRIDE_VALUE:
        return True
    try:
        return _shell_walk(text)[1]
    except BaseException:  # noqa: BLE001 - see above
        return False


def _route(argv):
    """Which covered route this argv is, or None."""
    if is_gh_issue_create(argv):
        return "gh-issue"
    if is_gh_api_issue_create(argv):
        return "gh-api"
    if is_curl_issue_create(argv):
        return "curl"
    return None


def creating_invocations(text, guard_core):
    """[(argv, route, scope), …] for every REAL issue create on this line.

    🔴 The scan runs over `scrub_inert_heredocs(…)`, not the raw text. That is the
    mention-vs-invocation requirement: a `cat > notes.md <<'EOF'` body that spells
    out the command is documentation, and denying it would make this gate the
    thing people route around. Body resolution still reads the ORIGINAL text.

    🔴 `scope` IS THE THIRD ELEMENT AND THE POINT OF THIS FUNCTION NOW. Each
    create is enumerated inside its OWN top-level command segment and carries
    that segment's text, so `body_candidates` cannot reach a heredoc a different
    command opened. Two creates on one line therefore get two different scopes,
    which is what stops the first one's good body answering for the second.

    A create the segmentation does not attribute — malformed quoting, a shape the
    walk and the shared core read differently — is still enumerated by a
    whole-text pass and keeps the OLD line-wide scope. That is deliberate: the
    backstop must not be able to turn a create that used to ALLOW into a deny,
    only to keep one from disappearing.
    """
    out, seen = [], []
    for lo, hi in command_spans(text):
        scope = text[lo:hi]
        for argv in guard_core.commands(scrub_inert_heredocs(scope)):
            route = _route(argv)
            if route and not is_exempt_invocation(argv, route):
                out.append((argv, route, scope))
                seen.append(argv)
    for argv in guard_core.commands(scrub_inert_heredocs(text)):
        route = _route(argv)
        if route and not is_exempt_invocation(argv, route) and argv not in seen:
            out.append((argv, route, text))
    return out


def evaluate(text, env, guard_core):
    """The deny reason for this command line, or None to allow."""
    if not PREFILTER.search(text):
        return None
    creates = creating_invocations(text, guard_core)
    if not creates:
        return None
    if override_requested(text, env):
        return None
    # 🔴 Judged over ALL the creates on the line together. A line that files two
    # issues, one specified and one not, must not pass on the strength of the
    # good one — so the verdict is "every create had a closing condition", never
    # "some body somewhere did".
    worst = None
    for argv, route, scope in creates:
        cands, reason = body_candidates(argv, scope, route, text)
        if any(has_closing_condition(c) for c in cands):
            continue
        if cands:
            return missing_text()
        worst = worst or unseeable_text(reason or UNRESOLVED_NONE)
    return worst


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if not isinstance(data, dict) or data.get("tool_name") != "Bash":
        sys.exit(0)
    tool_input = data.get("tool_input")
    cmd = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    if not isinstance(cmd, str) or not cmd:
        sys.exit(0)
    # The pre-filter runs BEFORE the import so a session that never touches gh or
    # curl pays one regex and an interpreter start, nothing else.
    if not PREFILTER.search(cmd):
        sys.exit(0)

    # 🔴 BaseException, not Exception — the same widening bash-guard.py measured.
    # A SystemExit or KeyboardInterrupt escaping here would exit non-zero, and for
    # PreToolUse every non-2 status is a silent ALLOW.
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import guard_core
        reason = evaluate(cmd, os.environ, guard_core)
    except BaseException as exc:  # noqa: BLE001 - see above
        if CRASH_LOOKS_LIKE_CREATE.search(cmd) and not override_requested(cmd, os.environ):
            deny(crash_text(exc))
        sys.exit(0)

    if reason:
        deny(reason)
    sys.exit(0)


if __name__ == "__main__":
    main()
