#!/usr/bin/env python3
"""PostToolUse nudge: when a Bash call does a SEARCH that the native Grep/Glob tools
already do (`grep -r`, bare `rg`, `find <path> -name …`, `ls -R`, `find | xargs grep`),
inject context pointing at the native tool.

Why this exists: measured over a 30-day window of activity telemetry, Bash is 71% of all
Claude tool calls (workbench 31,355 / laptop 6,164) while Grep+Glob together were used
50 times — and ZERO times on the laptop. RULES.md's "Tool Optimization" section already
says "Grep over bash grep, Glob over find"; that prose rule demonstrably does not work.
Per RULES.md "Deterministic Over Prose" the replacement has to be structural, so this
fires at the moment the search-shaped command runs.

Design constraints, in priority order:
  1. NEVER blocks. It is a PostToolUse nudge — it only ever adds context, and always
     exits 0. (bash-guard.py is the only hook here that denies; this is not that.)
  2. False positives are expensive. This runs on EVERY Bash call, and a hook that cries
     wolf trains the operator to ignore it. So the detector is deliberately conservative:
     it fires only on the shapes that are unambiguously a tree search, and stays silent
     on every narrow/legitimate use (single-file `grep`, `git log | grep`, `find -exec`
     doing non-search work).
  3. Once per KIND per session (two kinds: content-search, file-search), so a session
     sees at most two of these ever.
  4. NEVER recommend a tool this session does not have. Grep/Glob are not present in
     every session — the harness answers a call to a missing one with
     `Error: No such tool available: Grep. …`. A nudge pointing at a tool that cannot
     be called is pure token cost AND trains the operator to ignore the hook, which
     degrades it for the sessions where it IS actionable. See "Availability" below.

Fail-open: any error -> exit 0 silently; it must never break the Bash tool.
"""
import sys, json, os, re, shlex, glob

HOME = os.path.expanduser("~")
CACHE_DIR = f"{HOME}/.cache/claude-search-tool-nudge"

# Content-search binaries: they read file *contents*, so the native answer is Grep.
GREP_BINS = {"grep", "egrep", "fgrep", "rgrep", "ggrep"}
RG_BINS = {"rg", "ag", "ack", "ack-grep"}
# Leading words that wrap a real command without changing what it is.
WRAPPERS = {"sudo", "time", "nice", "ionice", "command", "builtin", "stdbuf", "env", "\\"}
SEPARATORS = {"|", "|&", "||", "&&", ";", ";;", "&", "\n"}
# `find` actions that mean the find is doing WORK, not searching for Claude to read.
FIND_ACTIONS = {"-exec", "-execdir", "-ok", "-okdir"}
FIND_NAME_PREDS = {"-name", "-iname", "-path", "-ipath", "-regex", "-iregex", "-wholename"}

CONTENT = "content"
FILES = "files"

HINTS = {
    CONTENT: (
        "Grep tool",
        "Grep(pattern=\"…\", path=\"…\", glob=\"*.py\", output_mode=\"content\")",
    ),
    FILES: (
        "Glob tool",
        "Glob(pattern=\"**/*.py\", path=\"…\")",
    ),
}

# The native tool each kind points at. Suppression is per-TOOL, not per-session-wide:
# a session that has only ever PROVEN Grep missing still gets its one Glob nudge, which
# then proves Glob too if it is also gone. Costs at most one extra nudge and keeps the
# claim scoped to what was actually observed.
NATIVE_TOOL = {CONTENT: "Grep", FILES: "Glob"}

# --------------------------------------------------------------------------- #
# Availability
#
# The PostToolUse payload does NOT enumerate the session's tools, and the wording of
# the unavailability error is NOT in the claude-code bundle (checked: 2.1.220 contains
# neither "Grep is not available" nor "via the Bash tool instead") — the tool set is
# decided server-side, so there is no local config file to consult. The only observable
# is the transcript: when the model calls a tool the session lacks, the harness writes
#
#   {"type":"user","message":{"role":"user","content":[{"type":"tool_result",
#     "content":"<tool_use_error>Error: No such tool available: Grep. Grep is not
#      available in this session — search file contents with `grep` via the Bash tool
#      instead.</tool_use_error>",
#     "is_error":true,"tool_use_id":"…"}]}, …}
#
# So availability is INFERRED, and only ever in the safe direction: silence a tool once
# this session has proven it missing. Known gap, stated rather than papered over — the
# signal only exists once the model has ATTEMPTED the tool. A session that never reaches
# for Grep (because it can see Grep is not in its tool list) produces no signal and keeps
# getting the nudge. Nothing else the hook receives distinguishes that case.
#
# 🔴 The match is STRUCTURAL, not spelled: only a tool_result block with `is_error` true
# whose text STARTS with the marker counts. That matters — this very error text also
# appears in the parent transcript as ordinary prose (quoted inside an Agent tool_use
# prompt, and inside a subagent's final report, both `is_error` false). A substring grep
# over the raw transcript would match those and silence a session whose tools work fine.
# --------------------------------------------------------------------------- #
UNAVAILABLE_MARKER = b"No such tool available: "
_UNAVAILABLE_RE = re.compile(r"^Error: No such tool available: ([A-Za-z][A-Za-z0-9_]*)")
# Cheap by construction: the scan runs at most once per KIND per session, because the
# outcome either way is written to the state file and short-circuits every later call.
# That bound needs a session_id to key the state file on — the harness always sends one
# (it and transcript_path come from the same payload builder), so the unbounded shape is
# "transcript_path but no session_id", which the harness does not produce.
# Measured on the largest transcript on this host — 66.5 MB — a full byte-prefiltered
# pass costs 73 ms; a typical transcript is under 1 MB. The cap bounds the pathological
# case; hitting it just means "no signal", i.e. nudge exactly as before.
MAX_SCAN_BYTES = 64 * 1024 * 1024


def _strip_heredocs(cmd):
    """Remove heredoc BODIES before lexing.

    Measured against 27,187 real transcript Bash commands: without this, the body of an
    `ssh host 'bash -s' <<'EOF' … EOF` block lexes as if it were local commands, so a
    `find`/`grep -r` meant for a REMOTE host produced a nudge pointing at Grep/Glob —
    which cannot reach that host at all. Also covers `cat > f <<'EOF'` file bodies.
    An unterminated heredoc swallows the rest of the command, i.e. it fails SILENT.
    """
    lines = cmd.split("\n")
    kept = []
    i = 0
    while i < len(lines):
        line = lines[i]
        kept.append(line)
        delims = [d for _, d in re.findall(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", line)]
        i += 1
        for d in delims:
            while i < len(lines) and lines[i].strip() != d:
                i += 1
            i += 1  # drop the terminator line too
    return "\n".join(kept)


def _newlines_to_separators(cmd):
    """Turn UNQUOTED newlines into `;` so a multi-line command lexes as several commands.

    shlex treats `\\n` as ordinary whitespace, so without this a command like
    `cd /repo\\ngrep -r foo .` lexes as ONE segment whose command name is `cd`, and the
    grep is never seen. Quote state is tracked so a newline INSIDE a quoted string (a
    multi-line `ssh host '…'` remote script, a quoted python -c body) stays inside its
    token and cannot be mistaken for a local command. A backslash line-continuation is
    likewise left alone so `git log \\<newline> | grep push` stays one command.

    Honest scope note: the quote/backslash tracking is belt-and-braces, not observably
    load-bearing — a mutation sweep showed both branches SURVIVE, because shlex re-parses
    quotes and escapes itself, so a `;` injected inside a quoted region stays inside the
    token anyway. It is kept because it makes this pass correct on its own terms; if the
    lexer is ever swapped out, dropping it would silently regress.
    """
    out = []
    quote = None
    i = 0
    while i < len(cmd):
        c = cmd[i]
        if quote is None and c == "\\" and i + 1 < len(cmd):
            out.append(c)
            out.append(cmd[i + 1])
            i += 2
            continue
        if quote is None and c in "'\"":
            quote = c
        elif quote == c:
            quote = None
        out.append(";" if (c == "\n" and quote is None) else c)
        i += 1
    return "".join(out)


def _tokenize(cmd):
    """Split a shell command into [(tokens, piped_in, pipeline_id), …].

    Uses shlex with punctuation_chars so `|`, `&&`, `;` come back as their own tokens and
    quoting is honoured — a `grep -r` that only appears INSIDE a quoted string collapses
    into one token and can therefore never be mistaken for a command. Returns [] on any
    lexing error (unbalanced quotes etc.), which makes the whole hook silent: fail-open.
    """
    lexer = shlex.shlex(_newlines_to_separators(_strip_heredocs(cmd)),
                        posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""  # '#' is only a comment at word start in sh; don't guess
    try:
        toks = list(lexer)
    except ValueError:
        return []

    segments = []
    cur = []
    piped_in = False
    pipeline_id = 0
    skip_next = False
    for t in toks:
        if skip_next:  # redirection target, e.g. the /dev/null in `2>/dev/null`
            skip_next = False
            continue
        if t in ("<", ">", ">>", "<<", "<<<", ">|", "&>", ">&"):
            skip_next = True
            continue
        if t in SEPARATORS:
            segments.append((cur, piped_in, pipeline_id))
            cur = []
            if t in ("|", "|&"):
                piped_in = True
            else:
                piped_in = False
                pipeline_id += 1
            continue
        cur.append(t)
    segments.append((cur, piped_in, pipeline_id))
    return [s for s in segments if s[0]]


def _strip_wrappers(tokens):
    """Drop leading `VAR=val` assignments and no-op wrappers (`sudo`, `time`, `xargs`…).

    Returns (command_name, args, via_xargs). `via_xargs` matters because
    `find … | xargs grep` is a tree content-search even though the grep has no -r.
    """
    i = 0
    via_xargs = False
    while i < len(tokens):
        t = tokens[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t) or t in WRAPPERS:
            i += 1
            continue
        if os.path.basename(t) == "xargs":
            via_xargs = True
            i += 1
            # Skip xargs' own flags up to the child command. Flags that take a separate
            # argument (`-I {}`, `-n 1`) consume the next token too.
            while i < len(tokens) and tokens[i].startswith("-"):
                takes_arg = tokens[i] in ("-I", "-n", "-P", "-L", "-d", "-s", "-E")
                i += 2 if takes_arg else 1
            continue
        break
    if i >= len(tokens):
        return None, [], via_xargs
    return os.path.basename(tokens[i]), tokens[i + 1:], via_xargs


def _has_short_flag(args, letters):
    """True if any short-flag token carries one of `letters` (handles clusters like -rn)."""
    for a in args:
        if a.startswith("--") or not a.startswith("-") or a == "-":
            continue
        body = a.split("=", 1)[0]
        if any(c in body[1:] for c in letters):
            return True
    return False


def _classify_segment(cmd_name, args, piped_in, via_xargs):
    """Return CONTENT / FILES / None for one already-unwrapped command."""
    if cmd_name is None:
        return None

    # --- grep family -----------------------------------------------------------
    # Fires ONLY when recursive (or fed by xargs from a find). A non-recursive grep is
    # either a single-file read or a pipeline filter (`git log | grep push`) — both
    # legitimate, neither expressible as the Grep tool, so both stay silent.
    if cmd_name in GREP_BINS:
        recursive = _has_short_flag(args, "rR") or any(
            a.split("=", 1)[0] in ("--recursive", "--dereference-recursive") for a in args
        )
        if recursive:
            return CONTENT
        if via_xargs:
            return CONTENT
        return None

    # --- ripgrep / ag / ack ----------------------------------------------------
    # These default to recursive-from-cwd, so a bare invocation IS a tree search. But
    # `cmd | rg foo` is a stdin filter — same legitimate case as grep — so skip when
    # the segment receives a pipe.
    if cmd_name in RG_BINS:
        if piped_in:
            return None
        return FILES if "--files" in args else CONTENT

    # --- find ------------------------------------------------------------------
    if cmd_name == "find":
        if "-delete" in args:
            return None  # a deletion, not a search
        for i, a in enumerate(args):
            if a in FIND_ACTIONS:
                child = args[i + 1] if i + 1 < len(args) else ""
                # `find … -exec grep …` is a tree content-search; `-exec rm/chmod/…`
                # is real work and must stay silent.
                return CONTENT if os.path.basename(child) in GREP_BINS else None
        if any(a in FIND_NAME_PREDS for a in args):
            return FILES
        return None

    # --- ls -R -----------------------------------------------------------------
    if cmd_name == "ls" and _has_short_flag(args, "R"):
        return FILES

    return None


def analyze(cmd):
    """Pure: return the ordered, de-duplicated list of kinds (CONTENT/FILES) `cmd` earns.

    Ordered so output is deterministic; de-duplicated so one command yields at most one
    nudge per kind.
    """
    segments = _tokenize(cmd)
    if not segments:
        return []

    kinds = []
    # Pipeline-level pass first: `find … | xargs grep …` / `find … | grep …` is a tree
    # content-search that neither segment alone reveals.
    by_pipeline = {}
    for tokens, piped_in, pid in segments:
        by_pipeline.setdefault(pid, []).append((tokens, piped_in))
    find_to_grep = set()
    for pid, members in by_pipeline.items():
        names = [_strip_wrappers(t)[0] for t, _ in members]
        if "find" in names and any(n in GREP_BINS for n in names[names.index("find") + 1:]):
            find_to_grep.add(pid)
            if CONTENT not in kinds:
                kinds.append(CONTENT)

    for tokens, piped_in, pid in segments:
        name, args, via_xargs = _strip_wrappers(tokens)
        # In a `find … | xargs grep` pipeline the find is the *plumbing* for a content
        # search, not a file search — one CONTENT nudge is the right answer, so don't
        # also emit FILES for the find half.
        if pid in find_to_grep and name == "find":
            continue
        k = _classify_segment(name, args, piped_in, via_xargs)
        if k and k not in kinds:
            kinds.append(k)
    return kinds


def _error_texts(rec):
    """Yield the text of every tool_result block in `rec` that the harness marked as an
    ERROR. Anything else — assistant tool_use inputs, ordinary tool output, a subagent's
    report — is deliberately not a witness (see the Availability note above)."""
    msg = rec.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict) or not block.get("is_error"):
            continue
        c = block.get("content")
        if isinstance(c, list):  # some versions wrap it as [{"type":"text","text":…}]
            c = "".join(b.get("text", "") for b in c if isinstance(b, dict))
        if not isinstance(c, str):
            continue
        yield c.replace("<tool_use_error>", "").replace("</tool_use_error>", "").strip()


def _scan_transcript(path, wanted, budget):
    """Names from `wanted` that `path` records as unavailable. Byte-prefiltered so all
    but a handful of lines are rejected without a JSON parse.

    Honest scope note, same convention as _newlines_to_separators above: the prefilter
    is a pure performance optimisation and a mutation sweep confirms it SURVIVES
    deletion — semantics are identical either way, only the cost changes (json.loads on
    every line of a multi-MB transcript vs a substring test). Kept for the cost.
    """
    found = set()
    with open(path, "rb") as fh:
        for raw in fh:
            budget[0] -= len(raw)
            if budget[0] < 0:
                break
            if UNAVAILABLE_MARKER not in raw:
                continue
            try:
                rec = json.loads(raw)
            except Exception:
                continue
            if not isinstance(rec, dict):
                continue
            for text in _error_texts(rec):
                m = _UNAVAILABLE_RE.match(text)
                if m and m.group(1) in wanted:
                    found.add(m.group(1))
                    if found >= wanted:
                        return found
    return found


def _transcript_paths(data):
    """Transcripts that can witness THIS agent's tool errors, most specific first.

    `transcript_path` is derived from the SESSION id, which a subagent shares with its
    parent — so a subagent's own tool errors are NOT in it. They live in the per-agent
    transcript, whose layout is (verified on disk, and in the claude-code 2.1.220
    bundle's `K0()`):
        <project>/<session>.jsonl                        <- transcript_path
        <project>/<session>/subagents/[<nested>/]agent-<agent_id>.jsonl
    """
    tp = data.get("transcript_path")
    if not isinstance(tp, str) or not tp.endswith(".jsonl"):
        return []
    paths = []
    agent = data.get("agent_id")
    if isinstance(agent, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", agent):
        base = os.path.join(tp[: -len(".jsonl")], "subagents")
        direct = os.path.join(base, "agent-%s.jsonl" % agent)
        if os.path.isfile(direct):
            paths.append(direct)
        else:  # nested agent-spawned-agent case
            paths.extend(sorted(glob.glob(os.path.join(base, "**", "agent-%s.jsonl" % agent),
                                          recursive=True))[:2])
    if os.path.isfile(tp):
        paths.append(tp)
    return paths


def _unavailable_native_tools(data, wanted):
    """Which of `wanted` this session has PROVEN unavailable.

    Fail-open, in exactly two places — both individually reachable, so neither is an
    unkillable belt-and-braces guard: a malformed payload that breaks path resolution,
    and a transcript that cannot be read. Either yields no signal, i.e. the nudge is
    delivered exactly as it was before this hook learned about availability.
    """
    found = set()
    budget = [MAX_SCAN_BYTES]
    try:
        paths = _transcript_paths(data)
    except Exception:
        return found
    for p in paths:
        try:
            found |= _scan_transcript(p, wanted - found, budget)
        except Exception:
            continue
        if found >= wanted or budget[0] < 0:
            break
    return found


def _state_path(data):
    """Per-session state file, scoped per AGENT too.

    A subagent shares its parent's session_id but not necessarily its tool set, so a
    subagent that proves Grep missing must not silence the parent's still-actionable
    nudge. A payload with no agent_id keys exactly as before.
    """
    session = data.get("session_id") or ""
    if not isinstance(session, str) or not session:
        return None
    agent = data.get("agent_id")
    key = session + ("@" + agent if isinstance(agent, str) and agent else "")
    return os.path.join(CACHE_DIR, re.sub(r"[^A-Za-z0-9_.-]", "_", key))


def _read_state(path):
    """Tokens recorded for this session: a KIND already nudged, or `no:<Tool>` for a
    native tool proven unavailable. Fail-open on IO error."""
    if not path:
        return set()
    try:
        with open(path) as fh:
            return set(fh.read().split())
    except Exception:
        return set()


def _append_state(path, tokens):
    if not path or not tokens:
        return
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "a") as fh:
            for t in tokens:
                fh.write(t + "\n")
    except Exception:
        pass


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    try:
        if not isinstance(data, dict) or data.get("tool_name") != "Bash":
            sys.exit(0)
        cmd = (data.get("tool_input") or {}).get("command", "")
        if not cmd:
            sys.exit(0)
        kinds = analyze(cmd)
        if not kinds:
            sys.exit(0)

        state_path = _state_path(data)
        state = _read_state(state_path)
        fresh = [k for k in kinds if k not in state]
        if not fresh:
            sys.exit(0)

        # Don't recommend a tool this session has proven it doesn't have. The scan is
        # reached at most once per kind: whichever way it goes, the outcome is recorded
        # below and every later call short-circuits on the state file.
        blocked = {t for t in NATIVE_TOOL.values() if "no:" + t in state}
        unknown = {NATIVE_TOOL[k] for k in fresh} - blocked
        if unknown:
            newly = _unavailable_native_tools(data, unknown)
            if newly:
                blocked |= newly
                _append_state(state_path, ["no:" + t for t in sorted(newly)])
        fresh = [k for k in fresh if NATIVE_TOOL[k] not in blocked]
        if not fresh:
            sys.exit(0)

        _append_state(state_path, fresh)
        lines = "\n".join(f"  • {HINTS[k][0]} → {HINTS[k][1]}" for k in fresh)
        nudge = (
            "search-tool: that Bash call was a tree search. The native tools do the same "
            "job with structured results, no shell quoting, and far fewer tokens — and "
            "measured over 30 days they are effectively unused (Bash 37.5k calls vs "
            "Grep+Glob 50). Prefer them next time:\n"
            f"{lines}"
        )
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": nudge,
            }
        }))
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
