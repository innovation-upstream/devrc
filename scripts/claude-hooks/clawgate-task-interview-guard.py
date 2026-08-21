#!/usr/bin/env python3
"""PreToolUse(Bash) gate: no clawgate task is CREATED without acceptance criteria.

WHY THIS EXISTS
---------------
`claude/skills/clawgate/SKILL.md` documents the task API wire contract and the
task PICKUP ritual in detail and says NOTHING about task AUTHORING. Creating a
task is one line — `clawgatectl task create --body …` — so the cheapest thing an
agent can do is post whatever the operator said, unverified and unspecified.

That is expensive twice over:

  * `scripts/task-spec-drafter/README.md` measured the unattended version of this
    problem on a real 8-ticket batch: a deep-context verification pass scored 8/8
    against a naive title-only pass at ~2/8, and of the 8 inbound "tickets" only
    ONE was genuinely dispatch-ready — the rest dissolved on verification (already
    done / stale / underspecified / deliberately-off). One of the naive drafts
    would have crashed a deliberately-suspended service. The drafter is the
    UNATTENDED path; this hook plus `flows/task-authoring.md` is its INTERACTIVE
    counterpart, not a second copy of it.

  * A body with no `## Acceptance criteria` heading structurally forces every
    pickup to end at `ready_for_review` — SKILL.md's status gate says an agent may
    not grade an exam it wrote itself. So the missing heading is not a style nit:
    it is the one lever the task AUTHOR has, and dropping it converts every
    dispatch into a human read.

🔴 THE TRIGGER IS THE ABSENCE OF ACCEPTANCE CRITERIA, NOT "A TASK IS BEING MADE".
A body that already carries `## Acceptance criteria` passes SILENTLY. That is the
operator's deliberate one-liner escape hatch: he can still write a task in one
command, he just has to say what "done" means while doing it.

🔴 THE HOOK IS THE ROUTER AS WELL AS THE ENFORCER. A file under a skill's
`flows/` directory does not auto-fire the way a skill DESCRIPTION does — nothing
loads it unless something names it. So every block message names
`flows/task-authoring.md` by path. Prose alone was already measured to lose on
the sibling problem: the pickup ritual sat in 🔴 prose in SKILL.md for months and
was skipped 2/2 until `clawgate-writeback-guard.py` made it structural.

🔴 SCOPE: INTERACTIVE SESSIONS ONLY, AND THAT IS FREE STRUCTURALLY.
A PreToolUse hook only ever sees Claude Code's own Bash tool. The unattended
producers — repo-cos, the task-spec drafter, clickup-mirror, the browser
extension — are systemd/cron/extension code that POSTs to `/api/tasks` in its own
process and never crosses this boundary. Nothing here needs a producer allowlist,
and adding one would be a spelled guard around a hazard that does not exist. A
Bash call that LAUNCHES a producer (`drafter.sh`, `send_digest.py`) is likewise
invisible to this hook: the argv it sees is the launcher's, not the POST's.

🔴 WHEN THE BODY CANNOT BE SEEN, THIS BLOCKS. IT DOES NOT PASS.
`clawgatectl task create --body "$(generate-spec.sh)"` hands the gate an argument
it cannot evaluate. Passing that through would make the guard walkable by
changing the SHAPE of the call rather than its content — the "spelled, not
structural" failure RULES.md names. So an unreadable body is a BLOCK with a
message that says how to make it readable (`--body <text>` / `--body-file
<path>`), never a silent allow. The cost is real and accepted: a generator
pipeline has to land its output in a file first.

THE OVERRIDE IS ONE SPELLING, ON PURPOSE
----------------------------------------
`CLAWGATE_NO_INTERVIEW=1` — as a command-position assignment on the call itself,
or in the hook process's environment. Exactly the value `1`; `true`/`yes`/`0` do
NOT override, because an override with several spellings is an override nobody
can grep for. It is deliberately noisy in the transcript, which is the point: the
audit question "when did we skip the interview" has to have an answer.

I/O CONTRACT
------------
Reads PreToolUse JSON on stdin (`tool_name`, `tool_input.command`), prints
`hookSpecificOutput.permissionDecision = "deny"` with a reason, exits 0. Exit 0
with no output is ALLOW. Exit codes other than 2 are non-blocking for PreToolUse,
so this file never relies on a non-zero status to mean anything.

FAIL-CLOSED IS SCOPED, NOT GLOBAL
---------------------------------
`bash-guard.py` denies on ANY internal failure because it guards irreversible
actions and every Bash call is in scope. This hook is in scope for a much smaller
family, so a blanket deny-on-crash would block `clawgatectl health` on an
unrelated bug. Instead: a crash (including a failed `guard_core` import) denies
ONLY when the raw command text still looks like a task create by a pure regex
that cannot itself fail. Everything else exits 0.
"""
import json
import os
import re
import sys

sys.dont_write_bytecode = True

# --------------------------------------------------------------------------- #
# Constants — every literal the tests pin lives here, spelled once.
# --------------------------------------------------------------------------- #

# The heading that unlocks agent self-completion. SKILL.md's "Acceptance-criteria
# detector": the body contains a heading matching `## Acceptance criteria`
# (case-insensitive).
#
# 🔴 EXACTLY TWO HASHES, and that is the STRICTER of the two readings on purpose.
# The detector this gate exists to satisfy is applied by a HUMAN-READ rule in
# SKILL.md, not by server code (grepped: clawgate's Go source has no such
# detector). A gate looser than the rule it enforces produces a FALSE PASS — a
# body that clears this hook and still comes back `ready_for_review` — which is
# the worst outcome available, because it reports safety it did not deliver.
# `### Acceptance criteria` is therefore blocked, and the message says so.
#
# Up to three leading spaces is CommonMark's ATX-heading indent allowance; a
# fourth makes it an indented code block, and a missing space after `##` makes it
# not a heading at all.
ACCEPTANCE_HEADING = re.compile(
    r"^ {0,3}##[ \t]+acceptance[ \t]+criteria(?![^\W\d_])[^\n]*$",
    re.IGNORECASE,
)

# A fenced code block opener/closer, CommonMark-ish: >=3 backticks or tildes,
# indented at most 3 spaces.
FENCE = re.compile(r"^ {0,3}(?P<char>`{3,}|~{3,})(?P<info>.*)$")

OVERRIDE_ENV = "CLAWGATE_NO_INTERVIEW"
# One spelling. See the module docstring.
OVERRIDE_VALUE = "1"
# The assignment in COMMAND POSITION, or via `export`. Anchored on a command
# boundary so the same bytes quoted inside a body do not silently disarm the gate
# (a guard that can be turned off by QUOTING its own name is not a guard).
OVERRIDE_INLINE = re.compile(
    r"(?:^|[\n;&|(){}`]|\$\()\s*(?:(?:then|else|do|!)\s+)*(?:export\s+)?"
    + OVERRIDE_ENV + r"=" + OVERRIDE_VALUE + r"(?![\w.-])"
)

# Where the interview lives. BOTH spellings are printed: the deployed path is
# what the model can open right now, the repo path is what it edits.
FLOW_DEPLOYED = "~/.claude/skills/clawgate/flows/task-authoring.md"
FLOW_REPO = "devrc/claude/skills/clawgate/flows/task-authoring.md"

# The pure-regex fallback classifier used ONLY on the crash path. It must never
# raise and must never need a parse.
CRASH_LOOKS_LIKE_CREATE = re.compile(r"\btask\s+create\b|/api/tasks(?![/\w])")

# The cheap pre-filter. A command line naming neither of these cannot be a task
# create, and returns before anything is imported or parsed.
PREFILTER = re.compile(r"clawgatectl|/api/tasks")

# A body file larger than this is not a task body; reading it would only be a way
# to make a per-Bash-call hook slow. Treated as UNRESOLVED (i.e. blocked), never
# as "no criteria" — the two are different facts and get different messages.
MAX_BODY_FILE_BYTES = 1024 * 1024

# Shell constructs whose VALUE this process cannot know.
#
# 🔴 THE TWO PREDICATES ARE DIFFERENT ON PURPOSE, and collapsing them was a
# measured false positive during development: a perfectly readable body that
# happened to say "set $PATH correctly" tripped a bare-`$` test and came back as
# "cannot see the body". A task body is PROSE, so a lone `$VAR` inside it is
# text; only a command substitution, or a value that is NOTHING BUT a variable
# reference, actually hides the content. A body-file argument is a PATH, where
# any `$` at all makes the target unknowable.
#
# 🔴 A BACKTICK IS NOT A SUBSTITUTION MARKER HERE, and that is the second
# measured false positive. Markdown code spans are everywhere in a task body, so
# treating any backtick as `…` substitution reported "cannot see the body" about
# bodies whose every byte was in the argument. Nothing is lost: a genuine
# backtick substitution leaves a literal with no heading in it, which this gate
# blocks anyway — one command shape moves from the "unreadable" message to the
# "no criteria" message, and neither is a pass.
SUBSTITUTION = re.compile(r"\$\(")
WHOLE_VARIABLE = re.compile(r"^\s*\$\{?[A-Za-z_][A-Za-z0-9_]*\}?\s*$")


def opaque_value(value):
    """True when a `--body`-shaped argument hides its content."""
    return bool(SUBSTITUTION.search(value) or WHOLE_VARIABLE.match(value))


def opaque_path(value):
    """True when a `--body-file`-shaped argument names an unknowable path."""
    return "$" in value or "`" in value

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

# The ONE path that creates a task. `/api/tasks/<id>` is a read, and
# `/api/tasks/<id>/comments` is the write-back ritual — neither is in scope.
CREATE_PATH = "/api/tasks"

# clawgatectl's persistent flags that take a separate value token.
CLAWGATECTL_VALUE_FLAGS = ("--api-url", "--token", "--env-file")


# --------------------------------------------------------------------------- #
# Heredocs
# --------------------------------------------------------------------------- #
# `(?<!<)` keeps a HERE-STRING (`<<<word`) out: it has no body and no terminator,
# so reading one as a heredoc would swallow the rest of the command line as
# "body text" and hand the gate prose it must not credit.
_HEREDOC_OPEN = re.compile(
    r"(?<!<)<<(?P<dash>-?)\s*(?P<tag>'[^']*'|\"[^\"]*\"|\\?[A-Za-z0-9_.-]+)")


def heredoc_bodies(text):
    """Every heredoc body in `text`, in the order the operators appear.

    🔴 DELIBERATELY QUOTE-BLIND, unlike `guard_core._scan`. The dominant real
    shape is

        clawgatectl task create --body "$(cat <<'EOF'
        ## Acceptance criteria
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
    bodies, n = [], len(text)
    # `cmd <<A <<B` opens TWO heredocs on ONE line, and bash reads their bodies
    # back to back in the order the operators appear — B's body starts where A's
    # terminator ended, not on the line after the command. `cursor` is what carries
    # that; `line_end` is the line the previous operator sat on, so a later
    # operator on a DIFFERENT line restarts from its own line instead.
    cursor, line_end, consumed = None, None, []
    for m in _HEREDOC_OPEN.finditer(text):
        # An operator that appears INSIDE a body already consumed is prose, not an
        # operator. Without this a task body quoting `<<EOF` would mint a spurious
        # body — and a spurious body is the one thing that could manufacture a
        # false ALLOW.
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
        line_end = nl
        i, lines = start, []
        while i < n:
            j = text.find("\n", i)
            line = text[i:j] if j != -1 else text[i:]
            nxt = (j + 1) if j != -1 else n
            # 🔴 `<<-` strips leading TABS from the body lines as well as from the
            # terminator. Stripping only the terminator left every body line
            # carrying a tab, which turned `\t## Acceptance criteria` into a
            # four-space-indented code line and made the heading invisible.
            if strip_tabs:
                line = line.lstrip("\t")
            if line.rstrip("\r") == tag:
                i = nxt
                break
            lines.append(line)
            i = nxt
        consumed.append((start, i))
        cursor = i
        bodies.append("\n".join(lines))
    return bodies


# --------------------------------------------------------------------------- #
# The acceptance-criteria detector
# --------------------------------------------------------------------------- #
def has_acceptance_criteria(body):
    """True iff `body` carries a real `## Acceptance criteria` heading.

    🔴 A HEADING INSIDE A FENCED CODE BLOCK DOES NOT COUNT, and that is not a
    nicety. The flow file this gate routes to SHOWS the body template inside a
    fence; a draft that quotes the template while forgetting to fill it in is
    exactly the near-miss this has to catch, and a naive substring search passes
    it. Same class as RULES.md's "a field that exists in a DTO is not a guard".
    """
    if not isinstance(body, str) or not body:
        return False
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
            continue
        if fence_char is not None:
            continue
        if ACCEPTANCE_HEADING.match(line):
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


def is_clawgatectl_task_create(argv):
    """True for a `clawgatectl … task create …` argv.

    Keyed on `task` and `create` being ADJACENT OPERANDS rather than on argv[1:3]:
    a global flag can precede the verb (`clawgatectl --env-file /x task create`),
    and `task` is also a legal value of some other flag. Adjacency in the operand
    list is what distinguishes the verb from a coincidence — and because the
    tokens come from a real lexer, a quoted `"task create"` is ONE token and never
    matches.
    """
    if not argv or os.path.basename(argv[0]) != "clawgatectl":
        return False
    ops = _operands(argv, CLAWGATECTL_VALUE_FLAGS)
    return any(a == "task" and b == "create" for a, b in zip(ops, ops[1:]))


HELP_FLAGS = ("--help", "-h")


def is_help_invocation(argv):
    """True for a create-shaped argv that only ASKS FOR HELP and cannot create.

    🔴 This is a structural exemption, not a convenience one. cobra prints usage
    and exits for any of these forms WITHOUT calling the command, so the argv
    genuinely cannot reach `POST /api/tasks` — allowing it removes a false
    positive rather than opening a hole. `--body` alongside `--help` changes
    nothing: help still wins and nothing is created.

    Both spellings cobra accepts are covered:
      * a `--help`/`-h` FLAG anywhere (`clawgatectl task create --help`)
      * the `help` SUBCOMMAND leading the operands (`clawgatectl help task create`)

    The flag scan skips value-flag values with the same rule `_operands` uses, so
    `--token --help` reads `--help` as the token's VALUE and does NOT exempt —
    otherwise the exemption would be reachable by a token that merely looks like
    a flag.

    🔴 Scoped to `clawgatectl` BY NAME. `--help` is cobra's; it is not a curl flag
    and curl posts the request anyway, so letting this predicate answer for a curl
    create would hand every curl producer a one-word bypass. Caught by
    `test_help_does_not_exempt_a_curl_create` — the first version of this function
    had exactly that hole.
    """
    if not argv or os.path.basename(argv[0]) != "clawgatectl":
        return False
    skip = False
    for tok in argv[1:]:
        if skip:
            skip = False
            continue
        if tok in CLAWGATECTL_VALUE_FLAGS:
            skip = True
            continue
        if tok in HELP_FLAGS:
            return True
    return _operands(argv, CLAWGATECTL_VALUE_FLAGS)[:1] == ["help"]


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


def is_curl_task_create(argv):
    """True for a curl that POSTs a NEW task.

    `/api/tasks/<id>` (a read) and `/api/tasks/<id>/comments` (the write-back
    ritual) are out of scope by path, and an explicit non-POST method is out of
    scope by method. A curl to the create path with neither an explicit method nor
    any data flag is a GET of the task list — also out of scope.
    """
    if not argv or os.path.basename(argv[0]) != "curl":
        return False
    method, paths, data = _curl_parts(argv)
    if CREATE_PATH not in paths:
        return False
    if method is not None:
        return method == "POST"
    return bool(data)


# --------------------------------------------------------------------------- #
# Body resolution
# --------------------------------------------------------------------------- #
UNRESOLVED_OPAQUE = "the argument is a shell substitution this gate cannot evaluate"
UNRESOLVED_STDIN = "the body is piped in on stdin, where a PreToolUse hook cannot read it"
UNRESOLVED_UNREADABLE = "the --body-file path could not be read"
UNRESOLVED_NONE = "the command names no body this gate can read"
UNRESOLVED_JSON = "the curl payload is not JSON this gate can parse"


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
        return inner, None
    # 🔴 Ordered so that a heading PRESENT IN THE LITERAL ARGUMENT always wins.
    # `--body "## Acceptance criteria … $(date)"` is readable enough: whatever the
    # substitution expands to, the heading is in the bytes the operator typed.
    # Consulting opacity first would report "cannot see the body" about a body
    # that is right there.
    if has_acceptance_criteria(value):
        return [value], None
    if opaque_value(value):
        # Last chance: the operator may have opened the heredoc outside the
        # quoted argument (`--body "$(cat <<EOF)"` splits across both shapes).
        outer = heredoc_bodies(text)
        if outer:
            return outer, None
        return [], UNRESOLVED_OPAQUE
    return [value], None


def _resolve_file(value, text):
    """([body, …], reason-or-None) for a `--body-file` argument."""
    if value in ("-", "/dev/stdin", "/proc/self/fd/0"):
        # `--body-file -` fed by a heredoc IS readable; fed by a pipe it is not.
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


def _resolve_curl_data(value, text):
    """([body, …], reason-or-None) for one curl data argument."""
    if value.startswith("@"):
        # 🔴 `-d @file` / `-d @-` still has to be JSON-PARSED afterwards. Returning
        # the file's raw bytes as a body candidate was a real defect: a payload
        # `{"body":"## Acceptance criteria\n…"}` carries the heading only as an
        # ESCAPED \n, so the line-based detector never sees a heading and a
        # correctly-specified create was denied.
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
    return [], UNRESOLVED_JSON if failed else UNRESOLVED_NONE


def body_candidates(argv, text, curl):
    """([every body text this gate could read], reason the rest were unreadable).

    The reason is None when everything named was resolved. Aggregating rather
    than picking ONE source is deliberate: a command may legitimately name a
    `--body-file` that a heredoc on the same line is about to write, and a
    candidate is only ever used to let the command THROUGH.
    """
    cands, reason = [], None

    def take(pair):
        nonlocal reason
        got, why = pair
        cands.extend(got)
        if why and reason is None:
            reason = why

    if curl:
        _, _, data = _curl_parts(argv)
        for value in data:
            take(_resolve_curl_data(value, text))
    else:
        for value in _flag_values(argv, ("--body",)):
            take(_resolve_inline(value, text))
        for value in _flag_values(argv, ("--body-file",)):
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
    "A task body with no `## Acceptance criteria` heading structurally forces "
    "EVERY pickup to end at `ready_for_review`: the agent derives the criteria "
    "itself and may not grade an exam it wrote (clawgate SKILL.md -> \"Status "
    "gate\"). That heading is the one lever the task AUTHOR has."
)

_HOW = (
    "Run the alignment interview BEFORE creating the task:\n"
    "    " + FLOW_DEPLOYED + "\n"
    "    (repo: " + FLOW_REPO + ")\n"
    "Phases: 0 PRE-VERIFY (already done? already a task? deliberately off?) -> "
    "1 INTERVIEW (at most 2 AskUserQuestion rounds, at most 4 questions each) -> "
    "2 RECOMMEND (scope cuts, a simpler approach, explicit non-goals) -> "
    "3 DRAFT + validate every tag against `GET /api/tags` -> "
    "4 CONFIRM the rendered body with Zach -> 5 CREATE."
)

_ESCAPES = (
    "Escape hatches, both deliberate:\n"
    "  * a body that already carries a `## Acceptance criteria` heading passes "
    "this gate SILENTLY -- the one-liner still works, it just has to say what "
    "\"done\" means;\n"
    "  * for this one call only, prefix it with " + OVERRIDE_ENV + "=" +
    OVERRIDE_VALUE + " (greppable on purpose)."
)


def missing_text():
    return (
        "This creates a clawgate task whose body has no `## Acceptance criteria` "
        "heading.\n\n" + _WHY + "\n\n" + _HOW + "\n\n"
        "The heading must be a level-2 ATX heading -- exactly `## Acceptance "
        "criteria` (case-insensitive, trailing text allowed). `###`, bold text and "
        "a heading inside a ``` fence do NOT count, because they do not satisfy "
        "the detector a pickup applies.\n\n" + _ESCAPES
    )


def unseeable_text(reason):
    return (
        "This creates a clawgate task, but this gate CANNOT SEE THE BODY (" +
        reason + "), so it cannot check for `## Acceptance criteria`.\n\n"
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
        "clawgate-task-interview-guard crashed while checking this command (" +
        str(exc) + "). It looks like a clawgate task create, so it is denied "
        "rather than passed through unchecked -- an unchecked create is how a "
        "task ships with no acceptance criteria and comes back "
        "`ready_for_review`.\n\n"
        "Report this; the command text is what reproduces it. To proceed now, "
        "prefix the call with " + OVERRIDE_ENV + "=" + OVERRIDE_VALUE + ".\n\n"
        "The interview: " + FLOW_DEPLOYED
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


def evaluate(text, env, guard_core):
    """The deny reason for this command line, or None to allow."""
    if not PREFILTER.search(text):
        return None
    creates = [argv for argv in guard_core.commands(text)
               if (is_clawgatectl_task_create(argv) or is_curl_task_create(argv))
               and not is_help_invocation(argv)]
    if not creates:
        return None
    if override_requested(text, env):
        return None
    # 🔴 Judged over ALL the create commands on the line together. A line that
    # creates two tasks, one specified and one not, must not pass on the strength
    # of the good one -- so the verdict is "every create had criteria", never
    # "some body somewhere did".
    worst = None
    for argv in creates:
        cands, reason = body_candidates(argv, text, is_curl_task_create(argv))
        if any(has_acceptance_criteria(c) for c in cands):
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
    # The pre-filter runs BEFORE the import so a session that never touches
    # clawgate pays one regex and an interpreter start, nothing else.
    if not PREFILTER.search(cmd):
        sys.exit(0)

    # 🔴 BaseException, not Exception -- the same widening bash-guard.py measured.
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
