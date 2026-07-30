#!/usr/bin/env python3
"""Regression suite for bash-guard.py.

Two jobs:
  1. the real bad command shapes are blocked;
  2. the shapes that a reverted `_strip_message_text()` helper once let through
     STAY blocked. Each "was-a-bypass" case below was proven to execute a
     destructive command in a real shell while that helper was in place (PR
     #217, three audit rounds). They are the reason the helper is gone -- see
     the DESIGN NOTE in bash-guard.py before reintroducing anything like it.

Accepted trade-off: because the checks match raw command text, a command that
merely QUOTES a blocked shape is blocked too. The "FP (accepted)" cases below
assert that deliberately, so the behaviour is documented rather than
rediscovered. Workaround is in the deny message: write the text to a file and
use `git commit -F <file>`.

Run directly (hand-rolled asserts, not pytest-collectable -- the guard calls
main() at import time, so it is only ever driven via subprocess):
    python3 scripts/claude-hooks/tests/test_bash_guard.py
"""
import json, os, subprocess, sys, time

GUARD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bash-guard.py")
GUARD = os.path.normpath(GUARD)

# Dangerous substrings are built piecewise so this file can never trip a live
# guard that is inspecting the command used to run it.
# NOTE: keep a trailing word after -A. `check_git_add_all` requires -A to be
# followed by whitespace or end-of-string, so a payload ending `-A'` silently
# matches nothing and makes a test vacuous (this hid a real bypass once).
ADDA = "git a" + "dd -A ever"
HARD = "git re" + "set --hard HEAD"


def run(cmd):
    """Return (blocked, error). `error` is non-empty if the guard misbehaved."""
    try:
        p = subprocess.run([sys.executable, GUARD],
                           input=json.dumps({"tool_name": "Bash",
                                             "tool_input": {"command": cmd}}),
                           capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
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
    ("explicit path staging",         "git a" + "dd clusters/foo.yaml", False),

    # --- was-a-bypass under _strip_message_text: MUST STAY BLOCKED ------
    # round 1: same-tag heredoc / substitution / stale offsets / escaped quote
    ("was-bypass: 2nd heredoc same tag",
     f"git commit -F - <<'EOF'\nnotes\nEOF\nbash <<'EOF'\n{ADDA}\nEOF", True),
    ("was-bypass: shell heredoc first",
     f"bash <<'EOF'\n{ADDA}\nEOF\ngit commit -F - <<'EOF'\nnotes\nEOF", True),
    ("was-bypass: heredoc in <( )",   f"git commit -F <(bash <<'EOF'\n{ADDA}\nEOF\n)", True),
    ("was-bypass: heredoc in $( )",   f"git commit -m \"$(bash <<'EOF'\n{ADDA}\nEOF\n)\"", True),
    ("was-bypass: long body shifts offsets",
     "git commit -F - <<'M1'\n" + "\n".join(f"body line {i}" for i in range(30))
     + f"\nM1\nbash <<'B'\n{ADDA}\nB\ngit commit -m 'ok'", True),
    ("was-bypass: escaped single quote",
     f"git commit -m 'msg \\' && {ADDA} && echo 'done'", True),
    # round 2: `&` backgrounds the commit, heredoc feeds a real shell
    ("was-bypass: & backgrounds the commit",
     f"git commit -m 'ok' & bash <<'EOF'\n{ADDA}\nEOF", True),
    ("was-bypass: & with sh + hard-reset",
     f"git commit -m 'ok' & sh <<'EOF'\n{HARD}\nEOF", True),
    # round 3: decoy argument satisfied a substring command test
    ("was-bypass: decoy 'git merge' arg",
     f"bash -s -- 'git merge' <<'EOF'\n{ADDA}\nEOF", True),
    ("was-bypass: decoy 'git tag' arg",
     f"bash -s -- 'git tag' <<'EOF'\n{ADDA}\nEOF", True),
    ("was-bypass: decoy via python3 -",
     f"python3 - 'jj describe' <<'EOF'\nimport os; os.system('{ADDA}')\nEOF", True),
    # never closed while stripping existed: ${ } funsub
    ("was-bypass: ${ } funsub",
     f"git commit -m \"${{ bash <<'EOF'\n{ADDA}\nEOF\n}}\"", True),
    ("was-bypass: unterminated heredoc",
     f"git commit -F - <<'NOPE'\nsome text\n{ADDA}", True),

    # --- accepted false positives (quoting a rule) ----------------------
    # Deliberate: the guard matches raw text. The deny message tells you to use
    # `git commit -F <file>`. Asserted so the trade-off stays visible.
    ("FP (accepted): -m quoting the rule",
     f'git commit -m "docs: never {ADDA}"', True),
    ("FP (accepted): heredoc commit body",
     f"git commit -F - <<'MSG'\nrule: {ADDA}\nMSG", True),
    ("FP (accepted): gh pr --body",
     f"gh pr create --body 'never {ADDA}'", True),

    # --- unrelated commands stay untouched ------------------------------
    ("non-git -m untouched",          "docker run -m '2g' myimage", False),
    ("plain gh pr create",            'gh pr create --title "x" --body "y"', False),
    ("plain commit",                  "git commit -F /tmp/msg.txt", False),
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

# The secret/IP guard must scan the FULL command, message included.
SECRET = "github_pat_" + "A" * 42
blocked, err = run(f'git commit -m "token {SECRET}"')
ok = blocked and not err
fail += not ok
print(f"{'PASS' if ok else 'FAIL'}  secret inside -m still caught{'  ' + err if err else ''}")

# No pathological backtracking anywhere in the remaining patterns.
t0 = time.time()
_, err = run('git commit -m "' + "\\" * 200)
elapsed = time.time() - t0
ok = elapsed < 2.0 and not err
fail += not ok
print(f"{'PASS' if ok else 'FAIL'}  no catastrophic backtracking ({elapsed:.2f}s)")

print("\nRESULT:", "all good" if not fail else f"{fail} failure(s)")
sys.exit(1 if fail else 0)
