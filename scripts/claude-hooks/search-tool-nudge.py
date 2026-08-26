#!/usr/bin/env python3
"""PostToolUse nudge: when a Bash call runs a RECURSIVE tree content-search
(`grep -r`, bare `rg`), warn that the search is .gitignore-BLIND and can return a
confident zero.

--------------------------------------------------------------------------- #
WHAT THIS HOOK USED TO SAY, AND WHY IT WAS WRONG  (retargeted 2026-08-25)
--------------------------------------------------------------------------- #
It used to point the same detector at the native Grep/Glob tools, quoting
"Bash 37.5k calls vs Grep+Glob 50" as proof of an agent-discipline failure.

🔴 That statistic counted ATTEMPTS, and essentially every attempt FAILED. Measured
across the 2,460 session transcripts under ~/.claude/projects with an mtime inside
14 days:

    Grep: 128 sessions reached for it — 0 used it without an error,
          128 got "No such tool available: Grep".
    Glob:   5 sessions reached for it — 0 used it,  5 got the same error.

Grep and Glob are not underused on this fleet; they are ABSENT from it. The old
message did not describe a discipline gap, it described a tool roster — and a
nudge toward a tool that cannot be called can never once be correct, which is the
"permanently-red gate" shape from RULES.md: it trains the reader to ignore the hook.

It was also actively harmful, not merely inert. In the same corpus the nudge fired
in 319 sessions, and in 61 of them it came BEFORE a failed `Grep` call — the hook
talked the agent into a tool call that could only error, costing tokens twice.

Ruled out before retargeting, so this is a root-cause fix and not a workaround: the
absence is NOT local configuration. `~/.claude/settings.json` has an empty
`permissions.deny`, no `disallowedTools`, and no Grep/Glob entry; the roster is
decided server-side.

🔴 AVAILABILITY IS NOT KNOWABLE FROM ANYTHING THIS HOOK RECEIVES. Measured, not
assumed, at claude-code 2.1.232:
  * the PostToolUse payload carries exactly 12 keys and none is a tool roster —
    captured live via a stdin-dumping hook: session_id, transcript_path, cwd,
    prompt_id, permission_mode, effort, hook_event_name, tool_name, tool_input,
    tool_response, tool_use_id, duration_ms;
  * a freshly captured transcript has no `"tools"` key, no `availableTools`, and
    zero occurrences of the names of the tools the session was not given.
The previous revision inferred absence from the transcript's
`Error: No such tool available: <Tool>` record. That worked, but only AFTER the
model had already attempted the tool and eaten the failure — the one cost worth
avoiding. It is removed rather than left in place, because a guard that cannot act
before the damage reads as coverage while providing none.

--------------------------------------------------------------------------- #
WHAT IT WARNS ABOUT NOW, AND WHY THAT IS WORTH A NUDGE
--------------------------------------------------------------------------- #
The same commands, but for a hazard that needs no tool at all — and a CORRECTNESS
hazard rather than a token-cost nag.

In the Bash tool's shell, `grep` is not GNU grep. Claude Code's shell snapshot
installs a function that execs `ugrep -G --ignore-files --hidden -I …`, and
`--ignore-files` makes it honour `.gitignore`. `rg` honours `.gitignore` by
default too. So a recursive search SKIPS ignored and generated paths — caches,
venvs, build output, `__pycache__` — and reports a confident 0 or an undercount
with no indication anything was skipped.

Reproduced live on this host (a token planted in both a tracked and a gitignored
file), with the controls that make the number mean something:

    positive control, tracked file : shadowed `grep -rn` 1  |  GNU grep 1
    explicit path into ignored dir : shadowed `grep -rn` 1  |  GNU grep 1
    🔴 WHOLE TREE (`grep -rn … .`) : shadowed `grep -rn` 1  |  GNU grep 2
    bare `rg`                      : 1   |   `rg --no-ignore` 2

This is already a written rule — `claude/RULES.md` "Shell & Tooling Gotchas"
("`grep` here is a FUNCTION wrapping ugrep, and `-r` HONOURS `.gitignore` … Never
quote a `-r` zero about an ignored or generated path", → archive:
grep-gitignore-blind). That rule was READ IN-SESSION and the trap was hit anyway,
which is the same "prose did not work" evidence that motivated the original hook.
Per RULES.md "Deterministic Over Prose" the enforcement has to be structural, so
this fires at the moment the blind search runs. The rule text stays — unlike the
retired "Tool Optimization" section, this one is TRUE and still worth reading;
the hook is its in-the-moment reminder, not its replacement.

WHAT DELIBERATELY NO LONGER FIRES, because the same controls show it is SAFE:
  * `find … | xargs grep` and `find … -exec grep` — the paths are explicit, so
    `--ignore-files` never applies (measured: 2, the correct answer);
  * `find -name` / `ls -R` — `find` is shadowed to `bfs`, which is NOT passed an
    ignore-files flag and does see gitignored files (measured: it found the
    generated file);
  * non-recursive `grep`, and `cmd | rg` as a stdin filter — no tree walk.
This is why the old FILES ("Glob") kind is gone entirely rather than reworded:
there is no hazard on that side to warn about.

Design constraints, in priority order:
  1. NEVER blocks. It is a PostToolUse nudge — it only ever adds context, and always
     exits 0. (bash-guard.py is the only hook here that denies; this is not that.)
  2. False positives are expensive. This runs on EVERY Bash call, and a hook that cries
     wolf trains the operator to ignore it. So the detector is deliberately conservative:
     it fires only on the shapes that are unambiguously a gitignore-honouring tree walk.
  3. Once per KIND per session, so a session sees at most one of these ever.

The FILENAME is deliberately unchanged. `search-tool-nudge.py` is registered by path
in register-nudge-hook.py, nix/home.nix, run-tests.sh and three test files, and — the
reason that matters — a rename would leave the OLD file in place at
~/.claude/hooks/search-tool-nudge.py on every host that has already deployed it, still
firing the retired Grep/Glob message with nothing to supersede it. Renaming has to be
sequenced with a removal; it is not a free cleanup.

Fail-open: any error -> exit 0 silently; it must never break the Bash tool.
"""
import sys, json, os, re, shlex

HOME = os.path.expanduser("~")
CACHE_DIR = f"{HOME}/.cache/claude-search-tool-nudge"

# 🔴 EXACTLY ONE name, and it is not a stylistic choice. Claude Code's shell snapshot
# defines precisely three functions — `find`, `grep`, `pkill` — so ONLY the spelling
# `grep` is redirected to `ugrep --ignore-files`. `egrep`/`fgrep`/`rgrep`/`ggrep` reach
# the real GNU binaries and are NOT gitignore-blind.
#
# MEASURED against the same planted-token fixture, which is how this was caught after
# being written the other way round (all five names) first:
#     grep -rn  -> 1   (blind: the gitignored copy is invisible)
#     egrep -rn -> 2   (correct)
#     fgrep -rn -> 2   (correct)
# Widening this set back out would make the hook fire on commands that have no hazard.
GREP_BINS = {"grep"}
# Unshadowed grep spellings, kept NAMED rather than merely absent so the next reader
# sees they were considered and excluded on evidence, not overlooked.
UNSHADOWED_GREP = {"egrep", "fgrep", "rgrep", "ggrep"}
# Searchers that walk the tree from cwd AND honour .gitignore by default.
RG_BINS = {"rg", "ag", "ack", "ack-grep"}
# `command grep` / `\grep` / an absolute path BYPASS the shadowing function, so they
# are GNU grep and carry no hazard. 🔴 `command` is therefore NOT a transparent
# wrapper here the way `sudo`/`time` are — it changes which binary runs, which is
# exactly the fix this hook recommends. Keeping it in WRAPPERS would make the hook
# fire on its own advice.
WRAPPERS = {"sudo", "time", "nice", "ionice", "builtin", "stdbuf", "env"}
BYPASS_WRAPPERS = {"command", "\\"}
SEPARATORS = {"|", "|&", "||", "&&", ";", ";;", "&", "\n"}

CONTENT = "content"

HINTS = {
    CONTENT: (
        "before quoting a zero, re-run with `command grep -r` or `rg --no-ignore`",
        "or enumerate: `find … -print0 | xargs -0 grep <pat>`",
    ),
}


# --------------------------------------------------------------------------- #
# Per-session state, and the claim protocol
#
# 🔴 Concurrency is the normal case here, not an edge: parallel subagents share one
# session, so several hook processes run at once. The old layout — one state FILE per
# session, read-modify-write — raced. MEASURED, 12 truly-concurrent invocations:
# 10-11 duplicate nudges. N copies of the nudge in one turn is precisely the
# "trains the operator to ignore it" failure this hook exists to avoid.
#
# (The race USED to be much wider, because a transcript scan for tool availability sat
# between the read and the write. That scan is gone — see the header — so the window is
# back to microseconds. The atomic claim is kept regardless: it is what makes the
# once-per-kind guarantee hold, and it was never the scan that made it necessary.)
#
# So state is a DIRECTORY per session and each token is an empty marker FILE created with
# O_CREAT|O_EXCL — an atomic test-and-set, no lock, so nothing can block the Bash call.
# The kind is claimed BEFORE anything is emitted, so exactly one process per kind can
# emit; the losers exit silently and pay nothing.
#
# What is actually guaranteed, stated precisely because the old comment overclaimed:
#   * at most ONE nudge per kind per session, including under concurrency, as long as
#     the state directory is writable and on a POSIX filesystem (O_EXCL);
#   * if state is UNWRITABLE the hook fails OPEN — it may nudge more than once, exactly
#     as the pre-existing hook did, rather than swallow the nudge or raise;
#   * a process that claims a kind and then dies burns that kind's nudge for the session.
#     Deliberate: one lost nudge is far cheaper than N duplicates.
#
# Tokens: a KIND ("content") = that nudge is spent for this session. Stored VERBATIM as
# a filename. The retired revision also wrote "no:<Tool>" markers recording a tool proven
# unavailable; nothing writes those any more, but an already-deployed host still has them
# on disk. They are inert — _read_state only ever compares against a KIND — so no
# migration is needed and the stale files simply age out with the cache directory.
# --------------------------------------------------------------------------- #
STATE_ROOT = f"{CACHE_DIR}/s"


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

    Returns (command_name, args, via_xargs, bypassed).

    `via_xargs` means the searcher is fed EXPLICIT paths from a pipe, which is the
    shape the live control showed is SAFE (`--ignore-files` never applies to an
    explicitly named file), so it now suppresses instead of firing.

    `bypassed` means this invocation does NOT reach the shell-snapshot `grep`
    function — `command grep`, or any name containing a slash (`/usr/bin/grep`,
    `./grep`). Those are the real GNU grep, carry no gitignore blindness, and are
    literally what this hook tells you to do; firing on them would make the hook
    nag its own advice.

    🔴 Honest scope: `\\grep` ALSO bypasses the function in a real shell, but
    shlex(posix=True) consumes the backslash, so `\\grep` is indistinguishable from
    `grep` by the time we see tokens. That is a known false-positive shape, not a
    covered one — it costs one spurious nudge per session and nothing else.
    """
    i = 0
    via_xargs = False
    bypassed = False
    while i < len(tokens):
        t = tokens[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t) or t in WRAPPERS:
            i += 1
            continue
        if t in BYPASS_WRAPPERS:
            bypassed = True
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
        return None, [], via_xargs, bypassed
    if "/" in tokens[i]:
        bypassed = True
    return os.path.basename(tokens[i]), tokens[i + 1:], via_xargs, bypassed


def _has_short_flag(args, letters):
    """True if any short-flag token carries one of `letters` (handles clusters like -rn)."""
    for a in args:
        if a.startswith("--") or not a.startswith("-") or a == "-":
            continue
        body = a.split("=", 1)[0]
        if any(c in body[1:] for c in letters):
            return True
    return False


def _classify_segment(cmd_name, args, piped_in, via_xargs, bypassed):
    """Return CONTENT / None for one already-unwrapped command.

    CONTENT means: this invocation WALKS A TREE ITSELF and the walker honours
    .gitignore, so its result can silently omit ignored/generated paths.
    """
    if cmd_name is None or bypassed:
        return None
    # Fed explicit paths from a pipe -> `--ignore-files` cannot skip them. Measured
    # SAFE (`find … | xargs grep` returned the correct 2 where a tree walk returned 1).
    if via_xargs:
        return None

    # --- grep family -----------------------------------------------------------
    # Fires ONLY when recursive, because only a recursive invocation makes the
    # shadowing ugrep function WALK, which is when `--ignore-files` starts pruning.
    # A non-recursive grep is a named-file read or a pipeline filter
    # (`git log | grep push`) — it sees exactly the bytes it was handed.
    if cmd_name in GREP_BINS:
        recursive = _has_short_flag(args, "rR") or any(
            a.split("=", 1)[0] in ("--recursive", "--dereference-recursive") for a in args
        )
        return CONTENT if recursive else None

    # --- ripgrep / ag / ack ----------------------------------------------------
    # These default to recursive-from-cwd AND to honouring .gitignore, so a bare
    # invocation IS a blind tree search. `cmd | rg foo` is a stdin filter — no walk.
    # An explicit --no-ignore / -u is the fix, so it must not fire on itself.
    if cmd_name in RG_BINS:
        if piped_in:
            return None
        if any(a.split("=", 1)[0] in ("--no-ignore", "--no-ignore-vcs", "--unrestricted")
               for a in args):
            return None
        if _has_short_flag(args, "u"):
            return None
        return CONTENT

    # 🔴 `find` and `ls -R` deliberately absent: `find` is shadowed to `bfs`, which is
    # NOT passed an ignore-files flag and DOES see gitignored paths (measured). There
    # is no hazard on that side, so warning about it would be a pure false positive.
    return None


def analyze(cmd):
    """Pure: return the de-duplicated list of kinds `cmd` earns — [] or [CONTENT].

    A list (rather than a bool) so the once-per-KIND state protocol and the IO
    contract stay identical to the shape they had when this hook carried two kinds.
    """
    segments = _tokenize(cmd)
    if not segments:
        return []

    kinds = []
    for tokens, piped_in, _pid in segments:
        name, args, via_xargs, bypassed = _strip_wrappers(tokens)
        k = _classify_segment(name, args, piped_in, via_xargs, bypassed)
        if k and k not in kinds:
            kinds.append(k)
    return kinds


def _sanitize(part):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", part)[:120]


def _state_dir(data):
    """Per-session state directory, scoped per AGENT too.

    A subagent shares its parent's session_id but not necessarily its tool set, so a
    subagent that proves Grep missing must not silence the parent's still-actionable
    nudge.

    🔴 The components are joined with "@", which `_sanitize` maps to "_" and therefore
    cannot appear INSIDE a sanitized component. That is what makes the key injective:
    joining sanitized parts with any character the sanitizer can emit (e.g. "_") would
    make session="a" + agent="b" collide with session="a_b" alone, and the first would
    then silence the second.

    Residual aliasing, stated at its real scope: two raw session ids that sanitize to the
    same string alias. The character-substitution half of that is pre-existing; the [:120]
    TRUNCATION half is new here and is mine, not inherited. Both are unreachable with the
    UUID/hex ids the harness actually sends.
    """
    session = data.get("session_id") or ""
    if not isinstance(session, str) or not session:
        return None
    agent = data.get("agent_id")
    parts = [_sanitize(session)]
    if isinstance(agent, str) and agent:
        parts.append(_sanitize(agent))
    return os.path.join(STATE_ROOT, "@".join(parts))


def _token_file(state_dir, token):
    # Tokens are stored verbatim: ":" is a legal filename byte on Linux (the only OS
    # these hooks run on), so an encode/decode pair here would be complexity that
    # nothing can observe — a mutation sweep deletes either half without any test
    # noticing, which is the definition of a guard not worth shipping.
    return os.path.join(state_dir, token)


def _read_state(state_dir):
    """Tokens already recorded: a KIND handled, or `no:<Tool>` proven unavailable.
    Fail-open — an unreadable/absent directory reads as "nothing recorded yet"."""
    if not state_dir:
        return set()
    try:
        return set(os.listdir(state_dir))
    except Exception:
        return set()


def _claim(state_dir, token):
    """Atomically claim `token` for this process. True iff THIS process created it.

    O_CREAT|O_EXCL is a single atomic test-and-set, so of N concurrent hook processes
    exactly one gets True. It never blocks — there is no lock to wait on, which is why
    this cannot hang a Bash call.

    Fails OPEN, and that direction is deliberate: if the state directory cannot be
    written we return True (proceed, possibly duplicating a nudge) rather than False
    (silently swallow it). Duplicating restores the pre-existing behaviour; swallowing
    would be a new failure mode invisible to the operator.
    """
    if not state_dir:
        return True
    # 🔴 The two steps have SEPARATE handlers on purpose. `makedirs(exist_ok=True)` also
    # raises FileExistsError — when the path is an existing regular file or symlink, not
    # a directory (e.g. a leftover from the pre-directory state layout). Sharing one
    # handler with the open below made that indistinguishable from "lost the race", so
    # the hook returned False and the nudge vanished for the whole session: fail CLOSED,
    # contradicting both this docstring and the module comment. Only the O_EXCL open may
    # ever mean "someone else got there first".
    try:
        os.makedirs(state_dir, exist_ok=True)
    except Exception:
        return True
    try:
        os.close(os.open(_token_file(state_dir, token),
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        return True
    except FileExistsError:
        return False
    except Exception:
        return True


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

        state_dir = _state_dir(data)
        # Fast path, NOT a correctness guard — honest scope note, same convention as the
        # byte prefilter: a mutation sweep confirms this read SURVIVES deletion, because
        # the atomic claim below already rejects an already-handled kind. It is kept so
        # the overwhelmingly common case (kind already handled) costs one listdir instead
        # of a mkdir + open, on a hook that runs after every Bash call.
        fresh = [k for k in kinds if k not in _read_state(state_dir)]
        if not fresh:
            sys.exit(0)

        # 🔴 CLAIM BEFORE EMITTING. Everything past this point is done by exactly one
        # process per kind, so concurrent invocations cannot each emit a nudge. The
        # losers exit silently, paying nothing.
        fresh = [k for k in fresh if _claim(state_dir, k)]
        if not fresh:
            sys.exit(0)

        lines = "\n".join(f"  • {HINTS[k][0]}\n  • {HINTS[k][1]}" for k in fresh)
        nudge = (
            "search-tool: that recursive search is .gitignore-BLIND — here `grep` execs "
            "`ugrep --ignore-files` and `rg` honours .gitignore, so both silently SKIP "
            "ignored/generated paths (caches, venvs, build output). Measured on this "
            "host: whole-tree `grep -rn` found 1 where GNU grep found 2.\n"
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
