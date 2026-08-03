#!/usr/bin/env python3
"""Unit tests for search-tool-nudge.analyze() + the hook's IO contract.

Structure mirrors test_shell_env_nudge.py (hand-rolled asserts + sys.exit, not
pytest-collectable) because run-tests.sh runs these directly.

Two things this file does that a plain assert list does not, both required because the
reassuring answer here is a ZERO (no false positives):

  * HARNESS NEGATIVE CONTROL — feed check() a case it MUST fail and confirm it records
    a failure. Until that has been watched go red, a green run is a fact about the
    harness, not about the hook.
  * BENIGN-SET POSITIVE CONTROL — the benign set is asserted to fire 0 times. A counter
    wired to nothing also reports 0, so the SAME counting path is first fed a command
    that MUST fire, and the count is asserted to be exactly 1.

Run: python3 scripts/claude-hooks/tests/test_search_tool_nudge.py
"""
import os, sys, json, subprocess, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "search-tool-nudge.py")
HOME = os.path.expanduser("~")

spec = importlib.util.spec_from_file_location("search_tool_nudge", HOOK)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
analyze = mod.analyze
CONTENT, FILES = mod.CONTENT, mod.FILES

fails = []


ran = []


def check(name, got, want):
    """Every assertion is COUNTED, not just exit-coded — the runner reports pass/fail
    only, so the count printed at the end is how a silently-shrunk suite is spotted."""
    ran.append(name)
    if got != want:
        fails.append(f"{name}: got {got!r} want {want!r}")


# --------------------------------------------------------------------------- #
# HARNESS NEGATIVE CONTROL — prove check() can go red before trusting any green.
# --------------------------------------------------------------------------- #
check("harness negative control", 1, 2)
ran.clear()
if len(fails) != 1:
    print("HARNESS BROKEN: check() did not record a deliberate mismatch — a green "
          "result from this file would mean nothing. Aborting.")
    sys.exit(2)
fails.clear()

# --------------------------------------------------------------------------- #
# MUST FIRE — tree searches the native tools already do.
# --------------------------------------------------------------------------- #
MUST_FIRE = [
    # grep -r / -R across a tree
    ("grep -r", "grep -r TODO src/", [CONTENT]),
    ("grep -R", "grep -R 'def main' .", [CONTENT]),
    ("grep clustered flags", "grep -rn --color=never foo lib/", [CONTENT]),
    ("grep --recursive", "grep --recursive pattern .", [CONTENT]),
    ("sudo grep -r", "sudo grep -r secret /etc", [CONTENT]),
    # ripgrep and friends default to recursive-from-cwd
    ("bare rg", "rg 'class Foo'", [CONTENT]),
    ("rg with path", "rg -n TODO scripts/", [CONTENT]),
    ("rg --files", "rg --files", [FILES]),
    ("ag", "ag pattern src", [CONTENT]),
    # find by name
    ("find -name", "find . -name '*.py'", [FILES]),
    ("find -iname abs path", f"find {HOME}/workspace -iname 'README*'", [FILES]),
    ("find -path", "find . -path '*/tests/*'", [FILES]),
    # find piped into grep
    ("find | xargs grep", "find . -name '*.py' | xargs grep -l TODO", [CONTENT]),
    ("find -print0 | xargs -0 grep", "find . -name '*.js' -print0 | xargs -0 grep foo", [CONTENT]),
    ("find -exec grep", "find src -name '*.c' -exec grep -H TODO {} +", [CONTENT]),
    ("xargs grep from a file list", "cat filelist.txt | xargs grep TODO", [CONTENT]),
    # ls -R
    ("ls -R", "ls -R nix/", [FILES]),
    ("ls -Ral", "ls -Ral scripts", [FILES]),
    # multi-command: a search hidden after a &&
    ("chained after &&", "git status && grep -r foo .", [CONTENT]),
    # multi-line: shlex treats \n as plain whitespace, so without an explicit
    # newline->separator pass the whole block lexes as one `cd` command and the
    # search is invisible. Caught by mutant M1.
    ("search on a later LINE", "cd /repo\necho hi\ngrep -r foo src/", [CONTENT]),
    ("find on a later LINE", "W=/tmp/x\nfind $W -name '*.py'", [FILES]),
    # both kinds in one call
    ("both kinds", "grep -r foo . && find . -name '*.md'", [CONTENT, FILES]),
]
for name, cmd, want in MUST_FIRE:
    check("FIRE " + name, analyze(cmd), want)

# --------------------------------------------------------------------------- #
# MUST NOT FIRE — narrow/legitimate uses. False positives are the expensive
# failure mode here: this runs on EVERY Bash call.
# --------------------------------------------------------------------------- #
BENIGN = [
    # grep against a single named file
    ("grep single file", "grep TODO README.md"),
    ("grep -n single file", "grep -n 'def main' scripts/foo.py"),
    ("grep -c single file", "grep -c error /var/log/syslog"),
    # grep as a pipeline filter over another command's output
    ("git log | grep push", "git log | grep push"),  # RULES.md notes this exact over-match hazard
    ("systemctl | grep", "systemctl list-units | grep browser-bridge"),
    ("ps | grep", "ps aux | grep -i python"),
    ("kubectl | grep -v", "kubectl get pods | grep -v Running"),
    ("cat | grep", "cat /etc/hosts | grep nixos"),
    ("rg as stdin filter", "journalctl -u foo | rg error"),
    # find doing non-search work
    ("find -exec rm", "find /tmp/cache -name '*.png' -exec rm {} +"),
    ("find -exec chmod", "find . -name '*.sh' -exec chmod +x {} \\;"),
    ("find -delete", "find /tmp -name '*.tmp' -delete"),
    ("find -mtime no name", "find /var/log -mtime +30"),
    # ls without -R
    ("plain ls", "ls -la scripts/"),
    # search-shaped text that is only a string, not a command
    ("grep -r inside a quoted string", "echo 'grep -r foo .'"),
    ("grep -r in a quoted ssh remote command", "ssh host 'grep -r foo /etc'"),
    # heredoc BODY: these run on a REMOTE host / are file content, not local searches.
    # Found by running the detector over 27,201 real transcript Bash commands.
    ("ssh heredoc body", "ssh host 'bash -s' <<'EOF'\ngrep -r foo /etc\nfind / -name '*.conf'\nEOF"),
    ("cat > file heredoc body", "cat > /tmp/s.sh <<'SH'\nfind . -name '*.py'\nSH\nchmod +x /tmp/s.sh"),
    # a multi-line remote script inside quotes is not a local search either
    ("multi-line quoted ssh script", "ssh host '\ngrep -r foo /etc\n'"),
    ("backslash line-continuation into a grep filter", "git log \\\n  | grep push"),
    # ordinary non-search commands
    ("git status", "git status && git log --oneline -3"),
    ("home-manager switch", "home-manager switch --flake ~/workspace/devrc --impure"),
    ("nix build", "nix build .#checks.x86_64-linux.pytests"),
]


def fire_count(cases):
    """The single counting path used for BOTH the positive control and the benign set."""
    return sum(1 for _, cmd in cases if analyze(cmd))


# BENIGN-SET POSITIVE CONTROL — the counter must be able to move off zero.
check("benign-counter positive control", fire_count([("pos", "grep -r foo src/")]), 1)
check("benign-set false positives", fire_count(BENIGN), 0)
# Per-case detail so a regression names the offender rather than just a number.
for name, cmd in BENIGN:
    check("BENIGN " + name, analyze(cmd), [])

# --------------------------------------------------------------------------- #
# Robustness — a malformed command must be silent, never an exception.
# --------------------------------------------------------------------------- #
check("unbalanced quote is silent", analyze("grep -r 'unterminated"), [])
check("empty command", analyze(""), [])

# --------------------------------------------------------------------------- #
# IO contract: real subprocess, PostToolUse payload, exit 0 and never a deny.
# --------------------------------------------------------------------------- #
def run(payload):
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                       capture_output=True, text=True)
    return p.returncode, p.stdout.strip()


sid = "test-session-search-nudge-DO-NOT-COLLIDE"
cache = os.path.join(HOME, ".cache", "claude-search-tool-nudge",
                     "".join(c if c.isalnum() or c in "_.-" else "_" for c in sid))
if os.path.exists(cache):
    os.remove(cache)

rc, out = run({"tool_name": "Bash", "session_id": sid,
               "tool_input": {"command": "grep -r TODO src/"}})
check("io rc", rc, 0)
check("io first-fire emits", bool(out) and "Grep tool" in out and "additionalContext" in out, True)
# It is a NUDGE: the payload must carry no deny/block decision of any kind.
parsed = json.loads(out) if out else {}
check("io hookEventName", parsed.get("hookSpecificOutput", {}).get("hookEventName"), "PostToolUse")
check("io never denies", any(k in out for k in ("permissionDecision", "\"deny\"", "block")), False)

# second search of the same kind in the same session -> deduped, silent
rc2, out2 = run({"tool_name": "Bash", "session_id": sid,
                 "tool_input": {"command": "grep -r other ."}})
check("io dedupe silent", out2, "")
# a DIFFERENT kind still fires once
rc3, out3 = run({"tool_name": "Bash", "session_id": sid,
                 "tool_input": {"command": "find . -name '*.md'"}})
check("io other kind fires", "Glob tool" in out3, True)

# benign command -> silent
rc4, out4 = run({"tool_name": "Bash", "session_id": sid,
                 "tool_input": {"command": "git log | grep push"}})
check("io benign silent", (rc4, out4), (0, ""))

# non-Bash tool -> silent, rc 0
rc5, out5 = run({"tool_name": "Read", "tool_input": {"file_path": "/x"}})
check("io non-bash silent", (rc5, out5), (0, ""))

# malformed stdin -> never crashes
p = subprocess.run([sys.executable, HOOK], input="not json", capture_output=True, text=True)
check("io malformed rc", p.returncode, 0)

if os.path.exists(cache):
    os.remove(cache)

MIN_CHECKS = 55  # floor: a suite that silently shrinks is a vacuous green
if fails:
    print("FAIL:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
if len(ran) < MIN_CHECKS:
    print(f"FAIL: only {len(ran)} checks ran, floor is {MIN_CHECKS} — the suite shrank.")
    sys.exit(1)
print(f"all search-tool-nudge tests passed ({len(ran)} checks: "
      f"{len(MUST_FIRE)} must-fire, {len(BENIGN)} benign, "
      f"1 harness negative control, 1 benign-counter positive control)")
