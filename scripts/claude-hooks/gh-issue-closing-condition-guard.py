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

One deliberate OVER-acceptance, recorded rather than hidden: a `--body` whose
text contains the two characters `\n` is ALSO read with those decoded (see
`escape_expanded`). `shlex` does not implement bash's `$'…'`, so an ANSI-C-quoted
body arrives as one line beginning `$##` and would deny; and a plainly
double-quoted `"…\n…"` really is one line to bash, which GitHub would render on
one line too. Crediting both is a knowing trade: an author who typed `\n` between
a heading and its content meant a newline, and denying them is the false positive
that gets a gate routed around.

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
  * `gh issue create --web` / `-w`, which does NOT post: gh opens the browser's
    new-issue form and exits, so the object is created by a human in a form this
    process cannot see. Structurally exempt, like `--help`; it is a one-flag way
    past this gate and is recorded as one rather than hidden.
  * the GitHub web UI, the mobile app, and anything a systemd/cron process does
  * `gh issue transfer`, `gh issue develop`, and issue creation as a side effect
    of some other tool (a bot, a workflow, `gh workflow run`)
  * the SEMANTIC question above: heading present and filled in is all it knows

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
# The assignment in COMMAND POSITION, or via `export`. Anchored on a command
# boundary so the same bytes quoted inside an issue body do not silently disarm
# the gate (a guard that can be turned off by QUOTING its own name is not a
# guard).
OVERRIDE_INLINE = re.compile(
    r"(?:^|[\n;&|(){}`]|\$\()\s*(?:(?:then|else|do|!)\s+)*(?:export\s+)?"
    + OVERRIDE_ENV + r"=" + OVERRIDE_VALUE + r"(?![\w.-])"
)

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
    deny. It also cannot invent a pass that the operator did not write — a body
    whose literal text is `## Closing condition\\n…` is one whose author meant a
    newline.
    """
    out = value[1:] if value.startswith("$") else value
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
GH_ISSUE_VALUE_FLAGS = GH_ISSUE_BODY_FLAGS + GH_ISSUE_BODY_FILE_FLAGS + (
    "-a", "--assignee", "-l", "--label", "-m", "--milestone", "-p", "--project",
    "-T", "--template", "-t", "--title", "-R", "--repo", "--editor",
    "--hostname",
)
# gh api's own value-taking flags.
GH_API_FIELD_FLAGS = ("-f", "--field", "-F", "--raw-field")
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
_PIPES_ONWARD = re.compile(r"[|&;]")
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


def heredocs(text):
    """Every heredoc in `text` as `(body, start, end, inert)`, in operator order.

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
        inert = (
            _attached_command(text, m.start()) in INERT_HEREDOC_SINKS
            and not _PIPES_ONWARD.search(rest_of_line)
        )
        out.append(("\n".join(lines), start, body_end, inert))
    return out


def heredoc_bodies(text):
    """Every heredoc body, inert or not — the body-resolution view."""
    return [h[0] for h in heredocs(text)]


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
    spans = [(lo, hi) for _, lo, hi, inert in heredocs(text) if inert]
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
    """(method, [url paths], [data values]) for a curl argv."""
    method, paths, data = None, [], []
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok in CURL_METHOD_FLAGS and i + 1 < len(argv):
            method = argv[i + 1].upper()
            i += 2
            continue
        if tok in CURL_DATA_FLAGS and i + 1 < len(argv):
            data.append(argv[i + 1])
            i += 2
            continue
        if tok == "--url" and i + 1 < len(argv):
            p = _url_path(argv[i + 1])
            if p:
                paths.append(p)
            i += 2
            continue
        if tok in CURL_VALUE_FLAGS:
            i += 2
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


def is_exempt_invocation(argv):
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
    """
    if not _is_gh(argv):
        return False
    if _has_flag(argv, HELP_FLAGS + WEB_FLAGS, GH_ISSUE_VALUE_FLAGS):
        return True
    return _operands(argv, GH_ISSUE_VALUE_FLAGS)[:1] == ["help"]


# --------------------------------------------------------------------------- #
# Body resolution
# --------------------------------------------------------------------------- #
UNRESOLVED_OPAQUE = "the argument is a shell substitution this gate cannot evaluate"
UNRESOLVED_STDIN = "the body is piped in on stdin, where a PreToolUse hook cannot read it"
UNRESOLVED_UNREADABLE = "the body-file path could not be read"
UNRESOLVED_NONE = "the command names no body this gate can read"
UNRESOLVED_JSON = "the request payload is not JSON this gate can parse"


def _flag_values(argv, names):
    """Values of `--flag value` and `--flag=value`, in argv order."""
    out, i = [], 1
    while i < len(argv):
        tok = argv[i]
        if tok in names and i + 1 < len(argv):
            out.append(argv[i + 1])
            i += 2
            continue
        for name in names:
            if tok.startswith(name + "="):
                out.append(tok[len(name) + 1:])
                break
        i += 1
    return out


def _read_body_file(path):
    path = os.path.expanduser(path)
    if os.path.getsize(path) > MAX_BODY_FILE_BYTES:
        raise ValueError("body file too large")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _resolve_inline(value, text):
    """([body, …], reason-or-None) for a literal `--body` argument."""
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
    # 🔴 Ordered so that a heading PRESENT IN THE LITERAL ARGUMENT always wins.
    # `--body "## Closing condition\n… $(date)"` is readable enough: whatever the
    # substitution expands to, the heading is in the bytes the operator typed.
    # Consulting opacity first would report "cannot see the body" about a body
    # that is right there.
    variants = _inline_variants(value)
    if any(has_closing_condition(v) for v in variants):
        return variants, None
    if opaque_value(value):
        # Last chance: the operator may have opened the heredoc outside the
        # quoted argument (`--body "$(cat <<EOF)"` splits across both shapes).
        outer = heredoc_bodies(text)
        if outer:
            return outer, None
        return [], UNRESOLVED_OPAQUE
    return variants, None


def _resolve_file(value, text):
    """([body, …], reason-or-None) for a `--body-file` argument."""
    if value in STDIN_NAMES:
        # A body-file of `-` fed by a heredoc IS readable; fed by a pipe it is
        # not.
        inner = heredoc_bodies(text)
        return (inner, None) if inner else ([], UNRESOLVED_STDIN)
    if opaque_path(value):
        return [], UNRESOLVED_OPAQUE
    try:
        return [_read_body_file(value)], None
    except Exception:
        # The file may be about to be written by a heredoc earlier on the same
        # command line, which this gate CAN read.
        inner = heredoc_bodies(text)
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


def _resolve_curl_data(value, text):
    """([body, …], reason-or-None) for one curl data argument."""
    if value.startswith("@"):
        # 🔴 `-d @file` / `-d @-` still has to be JSON-PARSED afterwards.
        # Returning the file's raw bytes as a body candidate is a real defect: a
        # payload `{"body":"## Closing condition\n…"}` carries the heading only
        # as an ESCAPED \n, so the line-based detector never sees a heading and a
        # correctly-specified create is denied.
        payloads, why = _resolve_file(value[1:], text)
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


def _resolve_gh_api_field(value, text):
    """([body, …], reason-or-None) for one `gh api -f/-F` field argument.

    Only a field literally named `body` is a body; `title=…` and `labels[]=…` are
    not, and returning them would let a title carry the heading.
    """
    if "=" not in value:
        return [], None
    key, val = value.split("=", 1)
    if key.strip() != "body":
        return [], None
    if val.startswith("@"):
        return _resolve_file(val[1:], text)
    return _resolve_inline(val, text)


def body_candidates(argv, text, route):
    """([every body text this gate could read], reason the rest were unreadable).

    The reason is None when everything named was resolved. Aggregating rather
    than picking ONE source is deliberate: a command may legitimately name a
    body-file that a heredoc on the same line is about to write, and a candidate
    is only ever used to let the command THROUGH.
    """
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
            take(_resolve_curl_data(value, text))
    elif route == "gh-api":
        for value in _flag_values(argv, GH_API_FIELD_FLAGS):
            take(_resolve_gh_api_field(value, text))
        for value in _flag_values(argv, ("--input",)):
            if value in STDIN_NAMES:
                inner = heredoc_bodies(text)
                take(_resolve_json_payload(inner) if inner else ([], UNRESOLVED_STDIN))
                continue
            payloads, why = _resolve_file(value, text)
            take(([], why) if why else _resolve_json_payload(payloads))
    else:
        for value in _flag_values(argv, GH_ISSUE_BODY_FLAGS):
            take(_resolve_inline(value, text))
        for value in _flag_values(argv, GH_ISSUE_BODY_FILE_FLAGS):
            take(_resolve_file(value, text))

    if not cands and reason is None:
        # No body argument at all. The heredoc on the line, if any, is still the
        # operator's body — and if there is none, the gate has seen nothing.
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

    Two channels, one spelling. The inline assignment is anchored on a command
    boundary; the process environment is read exactly, so `=true` / `=0` / an
    empty value are NOT overrides.
    """
    if env.get(OVERRIDE_ENV) == OVERRIDE_VALUE:
        return True
    return bool(OVERRIDE_INLINE.search(text))


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
    """[(argv, route), …] for every REAL issue create on this command line.

    🔴 The scan runs over `scrub_inert_heredocs(text)`, not `text`. That is the
    mention-vs-invocation requirement: a `cat > notes.md <<'EOF'` body that spells
    out the command is documentation, and denying it would make this gate the
    thing people route around. Body resolution still reads the ORIGINAL text.
    """
    out = []
    for argv in guard_core.commands(scrub_inert_heredocs(text)):
        route = _route(argv)
        if route and not is_exempt_invocation(argv):
            out.append((argv, route))
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
    for argv, route in creates:
        cands, reason = body_candidates(argv, text, route)
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
