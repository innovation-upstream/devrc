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

Fail-open: any error -> exit 0 silently; it must never break the Bash tool.
"""
import sys, json, os, re, shlex

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


def _already_nudged(session, kind):
    """Per-session dedupe: each kind is suggested at most once. Fail-open on IO error."""
    if not session:
        return False
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        f = os.path.join(CACHE_DIR, re.sub(r"[^A-Za-z0-9_.-]", "_", session))
        seen = set()
        if os.path.exists(f):
            with open(f) as fh:
                seen = set(fh.read().split())
        if kind in seen:
            return True
        with open(f, "a") as fh:
            fh.write(kind + "\n")
        return False
    except Exception:
        return False


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    try:
        if data.get("tool_name") != "Bash":
            sys.exit(0)
        cmd = (data.get("tool_input") or {}).get("command", "")
        if not cmd:
            sys.exit(0)
        session = data.get("session_id") or ""
        fresh = [k for k in analyze(cmd) if not _already_nudged(session, k)]
        if not fresh:
            sys.exit(0)
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
