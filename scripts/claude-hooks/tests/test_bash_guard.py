#!/usr/bin/env python3
"""Regression suite for bash-guard.py.

Covers three things:
  1. the real bad command shapes are still blocked;
  2. a command that merely QUOTES a blocked shape in a commit message / PR body
     is NOT blocked (the false positive that motivated _strip_message_text);
  3. message-stripping does not open a BYPASS -- every case below marked
     "bypass" was proven exploitable by an adversarial audit of PR #217 and must
     stay blocked.

Run directly (hand-rolled asserts, not pytest-collectable -- the guard calls
main() at import time, so it is only ever driven via subprocess):
    python3 scripts/claude-hooks/tests/test_bash_guard.py
"""
import json, os, subprocess, sys, time

GUARD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bash-guard.py")
GUARD = os.path.normpath(GUARD)

# Dangerous substrings are built piecewise so this file can never trip a live
# guard that is inspecting the command used to run it.
ADDA = "git a" + "dd -A"
HARD = "git re" + "set --hard HEAD"


def run(cmd):
    """Return (blocked, error). `error` is non-empty if the guard misbehaved."""
    try:
        p = subprocess.run([sys.executable, GUARD],
                           input=json.dumps({"tool_name": "Bash",
                                             "tool_input": {"command": cmd}}),
                           capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        # A hang is a FAIL line, not a traceback -- catastrophic backtracking
        # used to surface here as an uncaught TimeoutExpired.
        return False, "guard TIMED OUT (>30s) -- catastrophic backtracking?"
    if p.returncode != 0:
        return False, f"guard exited {p.returncode}: {p.stderr.strip()[:200]}"
    if p.stderr.strip():
        return False, f"guard wrote stderr: {p.stderr.strip()[:200]}"
    out = p.stdout.strip()
    if not out:
        return False, ""                      # allowed
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return False, f"guard emitted non-JSON: {out[:200]}"
    decision = (data.get("hookSpecificOutput") or {}).get("permissionDecision")
    if decision != "deny":
        return False, f"unexpected decision: {decision!r}"
    return True, ""


CASES = [
    # --- real bad shapes: MUST BLOCK ------------------------------------
    ("real blind-stage",              ADDA, True),
    ("real hard-reset",               HARD, True),
    ("real cd&&git",                  "cd /tmp && git status", True),
    ("real add -A after a commit",    f'git commit -m "ok" && {ADDA}', True),

    # --- quoted in a message: MUST NOT BLOCK ----------------------------
    ("explicit path staging",         "git a" + "dd clusters/foo.yaml", False),
    ("-m quoting the rule",           f'git commit -m "docs: never {ADDA} here"', False),
    ("-m quoting hard-reset",         f"git commit -m 'note: {HARD} is banned'", False),
    ("--message= quoting",            f'git commit --message="{ADDA}"', False),
    ("heredoc commit body",           f"git commit -F - <<'MSG'\nrule: {ADDA}\nand {HARD}\nMSG", False),
    ("gh pr body heredoc",            f"gh pr create --body-file - <<'EOF'\nwe forbid {ADDA}\nEOF", False),
    ("-m quoting cd&&git",            'git commit -m "avoid cd /x && git status"', False),

    # --- BYPASSES proven by the PR #217 audit: MUST BLOCK ---------------
    # (a) a second heredoc reusing the tag must not be stripped as well
    ("bypass: 2nd heredoc same tag",
     f"git commit -F - <<'EOF'\nnotes\nEOF\nbash <<'EOF'\n{ADDA}\nEOF", True),
    ("bypass: shell heredoc first",
     f"bash <<'EOF'\n{ADDA}\nEOF\ngit commit -F - <<'EOF'\nnotes\nEOF", True),
    # (b) a heredoc inside a substitution feeds the INNER command
    ("bypass: heredoc in <( )",
     f"git commit -F <(bash <<'EOF'\n{ADDA}\nEOF\n)", True),
    ("bypass: heredoc in $( )",
     f"git commit -m \"$(bash <<'EOF'\n{ADDA}\nEOF\n)\"", True),
    # (c) stale offsets after a long first body reclassified later heredocs
    ("bypass: long body shifts offsets",
     "git commit -F - <<'M1'\n" + "\n".join(f"body line {i}" for i in range(30))
     + f"\nM1\nbash <<'B'\n{ADDA}\nB\ngit commit -m 'ok'", True),
    # the shell has no escapes inside '...', so -m must stop at the first quote
    ("bypass: escaped single quote",
     f"git commit -m 'msg \\' && {ADDA} && echo 'done'", True),
    ("bypass: escaped quote + hard-reset",
     f"git commit -m 'msg \\' && {HARD} && echo 'done'", True),
    # (round 2) `&` backgrounds the commit and feeds the heredoc to a REAL
    # shell -- proven to execute, and was allowed while `&&`/`;` were blocked
    ("bypass: & backgrounds the commit",
     f"git commit -m 'ok' & bash <<'EOF'\n{ADDA}\nEOF", True),
    ("bypass: & with sh + hard-reset",
     f"git commit -m 'ok' & sh <<'EOF'\n{HARD}\nEOF", True),
    ("bypass: & after gh pr create",
     f"gh pr create --body x & bash <<'EOF'\n{ADDA}\nEOF", True),
    # an unterminated heredoc must not swallow a following real command.
    # (Blocked at every revision -- a guard against future regression, not a
    # bypass that was ever open.)
    ("unterminated heredoc stays visible",
     f"git commit -F - <<'NOPE'\nsome text\n{ADDA}", True),

    # --- stripping must stay narrowly scoped ----------------------------
    ("non-git -m untouched",          "docker run -m '2g' myimage", False),
    ("plain gh pr create",            'gh pr create --title "x" --body "y"', False),

    # --- (round 2) false positives that must NOT block ------------------
    # conventional-commit title: the `(` in `fix(guard):` must not sever the
    # heredoc from its own command
    ("FP: conventional-commit title + heredoc",
     f"gh pr create --title \"fix(guard): close bypasses\" --body-file - <<'EOF'\nnever {ADDA} ever\nEOF", False),
    ("FP: author with parens + heredoc",
     f"git commit --author \"A (bot) <a@b.c>\" -F - <<'EOF'\nban {ADDA}\nEOF", False),
    # message flags on other message-carrying commands
    ("FP: git tag -m",                f"git tag -a v1 -m 'never {ADDA} ever'", False),
    ("FP: git notes add -m",          f"git notes add -m 'never {ADDA}'", False),
    ("FP: git merge --no-ff -m",      f"git merge --no-ff x -m 'never {ADDA}'", False),
    ("FP: git stash push -m",         f"git stash push -m 'never {ADDA}'", False),
    # --body / --title / -am were never stripped
    ("FP: gh pr create --body",       f"gh pr create --body 'never {ADDA} ever'", False),
    ("FP: gh issue comment --body",   f"gh issue comment 1 --body 'never {ADDA}'", False),
    ("FP: gh pr --title only",        f"gh pr create --title 'ban {ADDA} always' --body x", False),
    ("FP: git commit -am",            f"git commit -am 'docs: never {ADDA} ever'", False),
]

fail = 0
for name, cmd, want in CASES:
    blocked, err = run(cmd)
    if err:
        print(f"ERROR block={blocked!s:<5} want={want!s:<5}  {name}\n       {err}")
        fail += 1
        continue
    ok = blocked == want
    fail += not ok
    print(f"{'PASS' if ok else 'FAIL'}  block={blocked!s:<5} want={want!s:<5}  {name}")

# The secret/IP guard must still scan the FULL command, message included --
# message-stripping must never be applied to it.
SECRET = "github_pat_" + "A" * 42
blocked, err = run(f'git commit -m "token {SECRET}"')
ok = blocked and not err
fail += not ok
print(f"{'PASS' if ok else 'FAIL'}  secret inside -m still caught{'  ' + err if err else ''}")

# ReDoS guard: an unterminated quote with a long backslash run must not stall.
# The old ambiguous pattern took minutes here.
redos = 'git commit -m "' + "\\" * 40
t0 = time.time()
_, err = run(redos)
elapsed = time.time() - t0
ok = elapsed < 2.0 and not err
fail += not ok
print(f"{'PASS' if ok else 'FAIL'}  no catastrophic backtracking ({elapsed:.2f}s)")

print("\nRESULT:", "all good" if not fail else f"{fail} failure(s)")
sys.exit(1 if fail else 0)
