#!/usr/bin/env python3
"""Verbatim capture of every BACKGROUNDED (and every status-MASKING) Bash call.

WHY THIS EXISTS — ClickUp 868ktvqf9
-----------------------------------
A full unit-suite run routed through the civitai dev-server test queue exits
non-zero and the harness's background-task notification reports **exit 0**.
`cli.mjs test list` says `exitCode: 1`; the notification says `0`. At the moment
the notification arrives the run's output file is **0 bytes**, so the two
independent things an investigator would check BOTH report green over a red run.
Four hits in one day across four agents; never reproduced deliberately since.

The leading hypothesis is that a trailing `; echo` / `; tail` **after a
redirect** makes the notification carry THAT command's status rather than the
run's — which is why "no pipe is involved" did not rule it out. `;` is not a
pipe. The investigating agent did it to itself twice.

🔴 **THE SINGLE ARTIFACT THAT DISCRIMINATES IS THE VERBATIM COMMAND STRING THAT
WAS BACKGROUNDED**, and nothing in the harness keeps it anywhere an investigator
can reach after the fact in a form they can trust. This module is that record.
It is INSTRUMENTATION, not a fix: it does not change, wrap, rewrite or refuse any
command. It writes a line and gets out of the way.

WHAT THE HARNESS ACTUALLY GIVES US — MEASURED, not assumed
----------------------------------------------------------
Measured 2026-08-21 by reading real `~/.claude/projects/**/*.jsonl` transcripts
on this host, not from documentation (the docs are silent on three of the four):

  1. `PreToolUse{tool_name:"Bash"}.tool_input` is exactly
         {command, description, timeout, run_in_background}
     `command` is the VERBATIM string and `run_in_background` is the boolean.
     This is the one place the discriminating artifact is available at all.

  2. `PostToolUse{tool_name:"Bash"}.tool_response` for a BACKGROUNDED call is
         {stdout:"", stderr:"", interrupted:false, isImage:false,
          noOutputExpected:false, backgroundTaskId:"bqj4lb0w5"}
     🔴 **There is NO exit code, and there cannot be**: PostToolUse fires when the
     command is LAUNCHED, not when it finishes. stdout/stderr are empty strings
     because nothing has run yet. `backgroundTaskId` names the output file the
     operator later finds at 0 bytes.

  3. The completion notification is NOT a hook event. It is injected into the
     conversation as a user-role message reading, verbatim:
         <summary>Background command "DESCRIPTION" completed (exit code N)</summary>
     🔴 It is keyed on the `description` field — NOT on `backgroundTaskId`. That
     is why this record captures `description` verbatim alongside the command:
     the description is the ONLY join key between a captured command and the
     exit code the harness announced for it.

  4. No hook event fires at background completion. Searched every event this
     host registers; none carries a terminal status for a backgrounded task.

So the honest division of labour is:

     THIS MODULE (hook, live)   ->  the verbatim command + description + task id
     `report()` (CLI, later)    ->  joins that against the transcript's
                                    `completed (exit code N)` line

Two halves, in one file, both greppable. `report()` is where the two numbers are
put next to each other; the hook never claims to know an exit code it cannot see.
🔴 `harness_exit_field` on a PostToolUse record is therefore almost always null,
and that is a MEASUREMENT ("we looked for an exit field in tool_response and
there was none"), not a placeholder. `response_keys` is the asserted ledger
beside it: if a future harness starts supplying a status key, it appears there
and the null stops being the whole story.

WHAT IS RECORDED, AND WHAT IS NOT
---------------------------------
Recorded: an invocation that is BACKGROUNDED, **or** one whose command carries a
status-masking marker (see below). Everything else is skipped without touching
the disk — that predicate is a single pass over a string, no I/O, and it is what
keeps a hook on the hot path of every Bash call cheap.

🔴 The command is stored VERBATIM — no truncation, no expansion, no
normalisation, no shell parsing applied to the stored value. A normalised record
cannot answer the question this exists to answer. Boundedness is enforced at the
FILE (one rotation generation, hard ceiling `2 * max_bytes`), never at the
record: a truncated command is exactly the evidence that would be missing.

THE MARKERS
-----------
`REDIRECT_THEN_SEMI` is the one the ticket asks for: a top-level redirect appears
BEFORE the final top-level `;`/newline separator, and that separator has a real
command after it — i.e. `run > out.log 2>&1; echo done`, whose reported status is
`echo`'s. `TRAILING_SEMI` is the same shape with no redirect; `TRAILING_PIPE` is
its better-known sibling (`... | tail`), included because it is the variant the
investigating agent hit twice and it costs nothing to detect in the same pass.

They are TRIAGE MARKERS, not a gate. Nothing is blocked, warned or rewritten on
them; they exist so `grep REDIRECT_THEN_SEMI` finds the population. A marker is
allowed to be wrong in the false-positive direction — an extra grep hit costs a
glance. The scanner does track quotes, escapes, `$(...)`/backtick nesting,
comments and HEREDOC BODIES, because heredocs are pervasive on this host and an
untracked one turns every `;` in a Python payload into a false marker.

FAIL-OPEN, ALWAYS
-----------------
🔴 This runs before AND after every Bash call in every Claude Code session on
this box. A hook that raises breaks the operator's shell. Every entry point here
returns rather than raises, the adapter wraps everything in a bare `except
BaseException`, and nothing is ever written to stdout or stderr. The worst case
of a total failure of this module is that the next hit is not captured — which is
the status quo it is replacing, so degrading silently is strictly no worse.

Set `CLAUDE_BG_CAPTURE_DISABLE=1` to turn it off without a switch.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

# --- on-disk names -----------------------------------------------------------
# 🔴 Pinned behaviourally by tests/test_bg_command_capture.py, in the shape
# scripts/claude-hooks/tests/test_on_disk_artifact_names.py established: the real
# writer is driven against a throwaway $HOME and the COMPLETE set of resulting
# relative paths is compared to a literal list. Renaming any component here
# orphans whatever the previous deploy wrote, and a `home-manager switch`
# replaces this file underneath live sessions — so the rename is invisible to
# every behavioural test and visible only as evidence that quietly stops
# accumulating.
SCHEMA = "BG_COMMAND_CAPTURE_V1"
STATE_SUBDIR = os.path.join(".local", "state", "claude-bg-command-capture")
LOG_NAME = "commands.jsonl"
ROTATED_NAME = "commands.1.jsonl"

# 8 MiB live + 8 MiB rotated = a 16 MiB hard ceiling for the whole instrument.
# Sized against the measured Bash volume on this host (37.5k calls / 30 days, of
# which only the backgrounded and marker-carrying minority are written): months
# of history, and bounded whatever happens.
DEFAULT_MAX_BYTES = 8 * 1024 * 1024

MARK_REDIRECT_THEN_SEMI = "REDIRECT_THEN_SEMI"
MARK_TRAILING_SEMI = "TRAILING_SEMI"
MARK_TRAILING_PIPE = "TRAILING_PIPE"

# The completion notification, verbatim from the transcripts (see docstring §3).
#
# 🔴 `\\?"` — THE OPTIONAL BACKSLASH IS LOAD-BEARING AND WAS MEASURED, NOT
# ANTICIPATED. The notification lives inside a JSON string in the transcript, so
# on disk its quotes are ESCAPED: the bytes read `\"Run main unit suite\"`, not
# `"Run main unit suite"`. The first version of this pattern required a bare `"`,
# matched nothing, and `report()` returned `announced_exit_codes: []` for a
# record whose exit code was sitting in the file three inches away. That is the
# reassuring-zero shape this whole instrument exists to stop, reproduced inside
# the instrument — an empty result that reads as "the harness never announced
# one" when it means "my pattern is wrong". `parse_completions` therefore ships
# with a positive control in its own test: a line that MUST produce a non-zero
# count, in BOTH spellings.
#
# `.*?` is LAZY, and that too was measured rather than chosen. Greedy, the
# capture swallowed the escaping backslash itself — the optional `\\?` then
# matched zero characters and the joined key came back as
# `'Run main unit suite in background\\'`, which matches no record's
# description. The join failed for a SECOND reason after the first was fixed,
# still reporting an empty list rather than an error. Lazy plus the long literal
# anchor `" completed (exit code N)` gives the backslash to the delimiter where
# it belongs; a description containing that whole anchor phrase is not a case
# worth trading this for.
COMPLETION_RE = re.compile(
    r'Background command (?:\\)?"(.*?)(?:\\)?" completed \(exit code (-?\d+)\)'
)

# Keys in a `tool_response` that could plausibly carry a terminal status. Looked
# for explicitly so the null in `harness_exit_field` is a measurement rather than
# an assumption — see the docstring.
_EXIT_KEYS = ("exitCode", "exit_code", "exitStatus", "returnCode",
              "return_code", "returncode", "code", "status")


def state_dir() -> str:
    """The directory records are written to. `$CLAUDE_BG_CAPTURE_DIR` wins.

    Resolved per CALL, never into a module constant: the tests redirect `$HOME`,
    and a constant baked at import would bake the developer's real home into
    every one of them (the trap `test_on_disk_artifact_names.py` documents).
    """
    override = os.environ.get("CLAUDE_BG_CAPTURE_DIR")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), STATE_SUBDIR)


def log_path() -> str:
    return os.path.join(state_dir(), LOG_NAME)


def max_bytes() -> int:
    raw = os.environ.get("CLAUDE_BG_CAPTURE_MAX_BYTES")
    if raw:
        try:
            val = int(raw)
            if val > 0:
                return val
        except (TypeError, ValueError):
            pass
    return DEFAULT_MAX_BYTES


def disabled() -> bool:
    return (os.environ.get("CLAUDE_BG_CAPTURE_DISABLE") or "").strip() not in ("", "0")


# --------------------------------------------------------------------------- #
# The scanner
# --------------------------------------------------------------------------- #

def _scan(cmd):
    """One pass over `cmd`. Returns (redirects, separators, pipes) — the byte
    offsets of TOP-LEVEL redirect operators, `;`/newline separators and single
    `|` pipes.

    "Top level" means: not inside `'...'`, not inside `"..."`, not inside a
    `$(...)`/`(...)` group, not inside backticks, not in a `#` comment, and NOT
    inside a heredoc body. That last exclusion is the one that earns its
    complexity here — `python3 - <<'PY' … PY` payloads are pervasive on this
    host, and every `;` and newline in one would otherwise read as a top-level
    separator and mark the command.

    `&&` and `||` are deliberately NOT separators: they short-circuit, so the
    status of `a > f && echo` is `a`'s when `a` fails. They do not mask. `;`,
    a bare newline and `|` do.
    """
    redirects, separators, pipes = [], [], []
    if not isinstance(cmd, str):
        return redirects, separators, pipes
    i, n = 0, len(cmd)
    in_sq = in_dq = in_bt = False
    depth = 0
    heredocs = []  # queued (delimiter, strip_leading_tabs) awaiting a newline

    def top():
        return depth == 0 and not in_bt

    while i < n:
        c = cmd[i]

        if in_sq:
            if c == "'":
                in_sq = False
            i += 1
            continue
        if in_dq:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == '"':
                in_dq = False
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        if c == "'":
            in_sq = True
            i += 1
            continue
        if c == '"':
            in_dq = True
            i += 1
            continue
        if c == "`":
            in_bt = not in_bt
            i += 1
            continue
        if c == "#" and (i == 0 or cmd[i - 1] in " \t\n;|&("):
            j = cmd.find("\n", i)
            if j < 0:
                break
            i = j
            continue
        if c == "(":
            depth += 1
            i += 1
            continue
        if c == ")":
            if depth:
                depth -= 1
            i += 1
            continue

        if c == "<":
            # `<<<` is a here-STRING, not a heredoc. Checked first, and skipped
            # whole: advancing by one would leave the remaining `<<` looking like
            # a heredoc introducer and swallow the rest of the command as a body.
            if cmd.startswith("<<<", i):
                if top():
                    redirects.append(i)
                i += 3
                continue
            if cmd.startswith("<<", i):
                j = i + 2
                strip = False
                if j < n and cmd[j] == "-":
                    strip = True
                    j += 1
                while j < n and cmd[j] in " \t":
                    j += 1
                delim = ""
                if j < n and cmd[j] in "'\"":
                    q = cmd[j]
                    j += 1
                    start = j
                    while j < n and cmd[j] != q:
                        j += 1
                    delim = cmd[start:j]
                    if j < n:
                        j += 1
                else:
                    start = j
                    while j < n and (cmd[j].isalnum() or cmd[j] in "_-."):
                        j += 1
                    delim = cmd[start:j]
                if delim:
                    heredocs.append((delim, strip))
                    if top():
                        redirects.append(i)
                    i = j
                    continue
                i += 2
                continue
            if top():
                redirects.append(i)
            i += 1
            continue

        if c == ">":
            if top():
                redirects.append(i)
            i += 1
            continue

        if c == "\n":
            if heredocs:
                i += 1
                while heredocs:
                    delim, strip = heredocs.pop(0)
                    while i < n:
                        j = cmd.find("\n", i)
                        line = cmd[i:] if j < 0 else cmd[i:j]
                        i = n if j < 0 else j + 1
                        probe = line.lstrip("\t") if strip else line
                        if probe.rstrip("\r") == delim:
                            break
                # 🔴 THE SEPARATOR IS THE NEWLINE AFTER THE DELIMITER LINE, NOT
                # THE ONE THAT OPENED THE BODY. Recording the opening newline
                # would make `_real_tail` see the heredoc BODY as the trailing
                # command and mark every heredoc; recording nothing at all loses
                # the genuine separator in `python3 - <<'PY' > log\n…\nPY\necho
                # done`, which is exactly the masking shape, and the first draft
                # did precisely that — the suite caught it.
                if top() and i - 1 >= 0 and cmd[i - 1] == "\n":
                    separators.append(i - 1)
                continue
            if top():
                separators.append(i)
            i += 1
            continue

        if c == ";":
            # `;;` and `;&` are `case` terminators, not command separators.
            if cmd.startswith(";;", i) or cmd.startswith(";&", i):
                i += 2
                continue
            if top():
                separators.append(i)
            i += 1
            continue

        if c == "|":
            if cmd.startswith("||", i):
                i += 2
                continue
            if top():
                pipes.append(i)
            i += 1
            continue

        if c == "&":
            if cmd.startswith("&&", i):
                i += 2
                continue
            i += 1
            continue

        i += 1

    return redirects, separators, pipes


def _real_tail(cmd, pos):
    """Is there a REAL command after the separator at `pos`?

    A trailing `;`, trailing whitespace, or a trailing `# comment` is not a
    masking command — `a > f;` reports `a`'s status, so marking it would be a
    false positive on the shape the ticket is about.
    """
    tail = cmd[pos + 1:].strip()
    return bool(tail) and not tail.startswith("#")


def markers(cmd):
    """The status-masking markers this command carries. Never raises."""
    try:
        redirects, separators, pipes = _scan(cmd)
    except Exception:  # noqa: BLE001 — a marker is never worth breaking a shell
        return []
    out = []

    last_sep = None
    for pos in separators:
        if _real_tail(cmd, pos):
            last_sep = pos
    if last_sep is not None:
        out.append(MARK_TRAILING_SEMI)
        if any(r < last_sep for r in redirects):
            out.append(MARK_REDIRECT_THEN_SEMI)

    seg_start = 0 if last_sep is None else last_sep + 1
    if any(p >= seg_start and _real_tail(cmd, p) for p in pipes):
        out.append(MARK_TRAILING_PIPE)
    return out


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #

def now_iso(now=None):
    t = time.time() if now is None else now
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + "Z"


def should_record(tool_input, marks=None):
    """The write predicate: BACKGROUNDED, or status-MASKING.

    Both halves matter and neither subsumes the other. Backgrounded-only would
    miss the foreground `cmd | tail` that produced the same false green twice;
    marker-only would miss a backgrounded run whose masking shape nobody has
    thought of yet, which is precisely the open question.
    """
    if not isinstance(tool_input, dict):
        return False
    if bool(tool_input.get("run_in_background")):
        return True
    if marks is None:
        marks = markers(tool_input.get("command") or "")
    return bool(marks)


def build_record(event, payload, now=None):
    """Payload -> record, or None when this invocation is not ours to write.

    🔴 `command` and `description` are copied through UNMODIFIED. Everything
    else on the record is derived; these two are evidence.
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str) or not command:
        return None

    marks = markers(command)
    if not should_record(tool_input, marks):
        return None

    rec = {
        "schema": SCHEMA,
        "event": event,
        "ts": now_iso(now),
        "session_id": payload.get("session_id"),
        "tool_use_id": payload.get("tool_use_id"),
        "cwd": payload.get("cwd"),
        "transcript_path": payload.get("transcript_path"),
        "background": bool(tool_input.get("run_in_background")),
        "timeout_ms": tool_input.get("timeout"),
        # The JOIN KEY for the completion notification. See docstring §3 — the
        # notification quotes this string and nothing else that identifies the
        # run, so a record without it cannot be tied to an exit code.
        "description": tool_input.get("description"),
        "markers": marks,
        "command": command,
    }

    if event == "PostToolUse":
        resp = payload.get("tool_response")
        if isinstance(resp, dict):
            rec["background_task_id"] = resp.get("backgroundTaskId")
            # The asserted ledger: the COMPLETE key set the harness sent. If a
            # future harness starts carrying a terminal status, it shows up here
            # rather than being silently absent forever.
            rec["response_keys"] = sorted(resp.keys())
            rec["interrupted"] = resp.get("interrupted")
            for k in _EXIT_KEYS:
                if k in resp:
                    rec["harness_exit_field"] = {"key": k, "value": resp[k]}
                    break
            else:
                rec["harness_exit_field"] = None
            for name, key in (("stdout_len", "stdout"), ("stderr_len", "stderr")):
                val = resp.get(key)
                rec[name] = len(val) if isinstance(val, str) else None
        else:
            rec["background_task_id"] = None
            rec["response_keys"] = None
            rec["harness_exit_field"] = None
    return rec


def append_record(rec, path=None, cap=None):
    """Append one JSON line, then rotate if the file has outgrown its cap.

    Returns a small dict describing what happened; never raises.

    THE WRITE IS ONE `os.write()` ON AN `O_APPEND` FD. Concurrency here is real —
    many sessions share this host and each has its own hook process — and
    `O_APPEND` makes the offset update atomic, so two writers cannot overwrite
    one another. Interleaving WITHIN a single very large record (a multi-megabyte
    heredoc) is not formally excluded by POSIX; that is why every reader in this
    module COUNTS unparseable lines and reports the count rather than dropping
    them silently. A reassuring zero is not on offer.

    Rotation keeps exactly ONE generation, so the instrument's total footprint is
    hard-bounded no matter how long it runs. `os.replace` is atomic; if two
    processes rotate at once the loser's generation is lost, which costs history
    and cannot corrupt the live file.

    🔴 THE ROTATION HAPPENS BEFORE THE APPEND, NOT AFTER, and the ordering is
    behavioural rather than cosmetic. Rotating after leaves NO live file in the
    window between a rotation and the next write — so an investigator arriving in
    that window finds `commands.jsonl` missing and reads it as "the instrument
    never ran", which is the reassuring-absence this whole thing exists to
    remove. Checked before, the live file exists after every write.

    The exact ceiling that follows: each generation is at most `cap` plus ONE
    record, because a record is never split or truncated (see the module
    docstring — a truncated command is the evidence that would be missing), so
    the total is `2 * (cap + largest_record)`. Stating it as plain `2 * cap`
    would be wrong in the one direction that matters, on exactly the
    multi-megabyte heredoc commands this host runs.
    """
    out = {"written": False, "rotated": False, "error": None}
    if rec is None:
        return out
    try:
        path = path or log_path()
        cap = cap or max_bytes()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            if os.path.getsize(path) > cap:
                os.replace(path, os.path.join(os.path.dirname(path), ROTATED_NAME))
                out["rotated"] = True
        except FileNotFoundError:
            pass
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        blob = line.encode("utf-8")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, blob)
        finally:
            os.close(fd)
        out["written"] = True
    except Exception as exc:  # noqa: BLE001 — see the module docstring
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
    return out


def read_records(path=None, include_rotated=True):
    """Every record on disk, newest file last, plus a count of lines that did
    not parse. Returns `(records, unparseable)`.

    🔴 `unparseable` is returned, not swallowed. An investigator reading this log
    is answering a question about a false green; a reader that quietly drops rows
    it cannot understand would be the same defect one layer down.
    """
    path = path or log_path()
    paths = []
    if include_rotated:
        paths.append(os.path.join(os.path.dirname(path), ROTATED_NAME))
    paths.append(path)
    records, bad = [], 0
    for p in paths:
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:  # noqa: BLE001
                        bad += 1
                        continue
                    if isinstance(obj, dict) and obj.get("schema") == SCHEMA:
                        records.append(obj)
                    else:
                        bad += 1
        except FileNotFoundError:
            continue
        except Exception:  # noqa: BLE001
            continue
    return records, bad


def parse_completions(text):
    """`{description: [exit_code, ...]}` from a transcript's notification lines.

    The notification is keyed on the description (docstring §3), so a repeated
    description genuinely maps to several exit codes. Returning the LIST rather
    than the last value keeps that ambiguity visible instead of resolving it by
    a coin flip.
    """
    out = {}
    if not isinstance(text, str):
        return out
    for desc, code in COMPLETION_RE.findall(text):
        # The description was captured out of a JSON string literal, so it still
        # carries that literal's escapes. Undo them with the JSON decoder rather
        # than a hand-rolled replace: `\"`, `\\` and `\uXXXX` are all in scope and
        # a partial unescape would silently fail to join on exactly the
        # descriptions that contain them. Falls back to the raw capture if the
        # fragment is not a well-formed literal.
        try:
            desc = json.loads('"%s"' % desc)
        except Exception:  # noqa: BLE001
            pass
        out.setdefault(desc, []).append(int(code))
    return out


def report(path=None, transcript_reader=None, only_background=True):
    """Join captured commands against the exit codes the harness ANNOUNCED.

    This is the half the hook structurally cannot do (no completion hook event
    exists), and it is where the two numbers that disagree in 868ktvqf9 end up on
    one line. It is a READ — it opens the transcript each record names and looks
    for that record's own description.

    Returns `{"rows": [...], "unparseable": n, "transcripts_unreadable": n}`.
    """
    records, bad = read_records(path)
    if transcript_reader is None:
        def transcript_reader(p):
            with open(p, encoding="utf-8", errors="replace") as fh:
                return fh.read()

    cache, unreadable = {}, 0
    rows = []
    for rec in records:
        if only_background and not rec.get("background"):
            continue
        tp = rec.get("transcript_path")
        if tp and tp not in cache:
            try:
                cache[tp] = parse_completions(transcript_reader(tp))
            except Exception:  # noqa: BLE001
                cache[tp] = {}
                unreadable += 1
        codes = cache.get(tp, {}).get(rec.get("description"), [])
        rows.append({
            "ts": rec.get("ts"),
            "event": rec.get("event"),
            "session_id": rec.get("session_id"),
            "background_task_id": rec.get("background_task_id"),
            "description": rec.get("description"),
            "markers": rec.get("markers") or [],
            "announced_exit_codes": codes,
            "command": rec.get("command"),
        })
    return {"rows": rows, "unparseable": bad, "transcripts_unreadable": unreadable}


# --------------------------------------------------------------------------- #
# CLI — positive control + the investigator's read
# --------------------------------------------------------------------------- #

_SELFTEST_COMMAND = (
    "pnpm run test:unit:run > /tmp/bg-capture-selftest.log 2>&1; echo done"
)


def selftest(directory=None):
    """🔴 POSITIVE CONTROL. Drive the REAL writer with the REAL masking shape,
    read it back through the REAL reader, and print the pair of counts.

    An instrument nobody has watched succeed is a claim about the instrument. A
    log that is empty when the next hit lands is indistinguishable from a log
    wired to nothing, so this exists to be run BEFORE believing an empty one.

    It drives the shipped functions, not lookalikes, and it never touches the
    real log — `directory` defaults to a throwaway.
    """
    import shutil
    import tempfile
    tmp = directory or tempfile.mkdtemp(prefix="bg-command-capture-selftest-")
    owned = directory is None
    try:
        path = os.path.join(tmp, LOG_NAME)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "session_id": "selftest-session",
            "tool_use_id": "toolu_selftest",
            "cwd": tmp,
            "tool_input": {
                "command": _SELFTEST_COMMAND,
                "description": "selftest positive control",
                "timeout": 1800000,
                "run_in_background": True,
            },
        }
        rec = build_record("PreToolUse", payload)
        wrote = append_record(rec, path=path)
        back, bad = read_records(path)
        ok = (
            wrote["written"]
            and len(back) == 1
            and bad == 0
            and back[0]["command"] == _SELFTEST_COMMAND
            and MARK_REDIRECT_THEN_SEMI in back[0]["markers"]
        )
        print("wrote: %s  read back: %d  unparseable: %d" % (wrote["written"], len(back), bad))
        print("verbatim round-trip: %s" % (back[0]["command"] == _SELFTEST_COMMAND if back else False))
        print("markers: %s" % (back[0]["markers"] if back else []))
        print("positive control: 1 expected, %d observed -> %s"
              % (len(back), "PASS" if ok else "FAIL"))
        return 0 if ok else 1
    finally:
        if owned:
            shutil.rmtree(tmp, ignore_errors=True)


def _main(argv):
    if "--selftest" in argv:
        return selftest()
    if "--report" in argv:
        res = report()
        for row in res["rows"]:
            first = (row["command"] or "").splitlines()[:1]
            print("%s  %s  task=%s  exit=%s  marks=%s  desc=%r  cmd=%s"
                  % (row["ts"], row["event"], row["background_task_id"],
                     row["announced_exit_codes"] or "-",
                     ",".join(row["markers"]) or "-",
                     row["description"], first[0] if first else ""))
        print("rows: %d  unparseable: %d  transcripts_unreadable: %d"
              % (len(res["rows"]), res["unparseable"], res["transcripts_unreadable"]))
        return 0
    if "--dump" in argv:
        records, bad = read_records()
        for rec in records:
            print(json.dumps(rec, ensure_ascii=False))
        print("# records: %d  unparseable: %d" % (len(records), bad), file=sys.stderr)
        return 0
    print(__doc__)
    print("usage: bg_command_capture.py [--selftest | --report | --dump]")
    print("log: %s" % log_path())
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
