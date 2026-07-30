#!/usr/bin/env python3
"""bash-guard regression suite: real bad shapes still blocked, quoted-in-message
text no longer false-positives, and the secret guard is NOT weakened."""
import json, subprocess, sys

GUARD = sys.argv[1] if len(sys.argv) > 1 else \
    "/home/zach/workspace/devrc-rules/claude/hooks/bash-guard.py"

# built piecewise so this file can't trip an outer guard
ADDA = "git a" + "dd -A"
HARD = "git re" + "set --hard HEAD"

cases = [
    # (name, command, should_block)
    ("real blind-stage",            ADDA, True),
    ("real hard-reset",             HARD, True),
    ("explicit path staging",       "git a" + "dd clusters/foo.yaml", False),
    ("real cd&&git",                "cd /tmp && git status", True),

    # false positives this fix targets
    ("-m quoting the rule",         f'git commit -m "docs: never {ADDA} here"', False),
    ("-m quoting hard-reset",       f"git commit -m 'note: {HARD} is banned'", False),
    ("--message= quoting",          f'git commit --message="{ADDA}"', False),
    ("heredoc commit body",         f"git commit -F - <<'MSG'\nrule: {ADDA}\nand {HARD}\nMSG", False),
    ("gh pr body heredoc",          f"gh pr create --body-file - <<'EOF'\nwe forbid {ADDA}\nEOF", False),
    ("-m quoting cd&&git",          'git commit -m "avoid cd /x && git status"', False),

    # MUST STILL BLOCK: heredoc feeding a real shell actually executes
    ("heredoc into bash executes",  f"bash <<'EOF'\n{ADDA}\nEOF", True),
    ("heredoc into sh executes",    f"sh <<'EOF'\n{HARD}\nEOF", True),
    # real bad command alongside an innocent message
    ("real add -A after commit",    f'git commit -m "ok" && {ADDA}', True),
]

fail = 0
for name, cmd, want in cases:
    p = subprocess.run([sys.executable, GUARD],
                       input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}),
                       capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    blocked = "deny" in out.lower()
    ok = blocked == want
    fail += not ok
    print(f"{'PASS' if ok else 'FAIL'}  block={blocked!s:<5} want={want!s:<5}  {name}")

# secret guard must NOT be weakened by message stripping
SECRET = "github_pat_" + "A" * 42
p = subprocess.run([sys.executable, GUARD],
                   input=json.dumps({"tool_name": "Bash", "tool_input": {
                       "command": f'git commit -m "token {SECRET}"'}}),
                   capture_output=True, text=True)
sec_blocked = "deny" in ((p.stdout or "") + (p.stderr or "")).lower()
print(f"{'PASS' if sec_blocked else 'FAIL'}  secret in -m still caught: {sec_blocked}")
fail += not sec_blocked

print("\nRESULT:", "all good" if not fail else f"{fail} failure(s)")
sys.exit(1 if fail else 0)
