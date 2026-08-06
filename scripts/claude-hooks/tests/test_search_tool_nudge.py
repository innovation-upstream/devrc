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
import os, sys, glob, json, shutil, subprocess, tempfile, importlib.util

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

# --------------------------------------------------------------------------- #
# AVAILABILITY SUPPRESSION — never recommend a tool this session does not have.
#
# The hook infers availability from the transcript, because nothing it receives
# enumerates the session's tools and the unavailability wording is server-side (not in
# the claude-code bundle). Each scenario below gets its OWN session id so one scenario's
# cache file cannot decide another's outcome.
#
# The pairing that makes these readable: every "suppressed" assertion is preceded by the
# SAME payload shape against a transcript with no error record, asserted to FIRE. A
# passing "no nudge" is otherwise indistinguishable from a harness wired to nothing.
# --------------------------------------------------------------------------- #
TMP = tempfile.mkdtemp(prefix="search-tool-nudge-tests-")
SESSIONS = []


def cache_path(sid, agent=None):
    key = sid + ("@" + agent if agent else "")
    return os.path.join(HOME, ".cache", "claude-search-tool-nudge",
                        "".join(c if c.isalnum() or c in "_.-" else "_" for c in key))


def session(name):
    """A fresh session id whose cache file is cleared now and removed at exit."""
    sid = "test-search-nudge-%s-DO-NOT-COLLIDE" % name
    SESSIONS.append(sid)
    for p in (cache_path(sid), cache_path(sid, "agentX"), cache_path(sid, "agentY")):
        if os.path.exists(p):
            os.remove(p)
    return sid


def unavailable_record(tool):
    """The record the harness really writes — shape copied from a live transcript
    (claude-code 2.1.220), including the <tool_use_error> wrapper and is_error."""
    return {
        "type": "user",
        "isSidechain": False,
        "message": {"role": "user", "content": [{
            "type": "tool_result",
            "content": ("<tool_use_error>Error: No such tool available: %s. %s is not "
                        "available in this session — search file contents with "
                        "`grep` via the Bash tool instead.</tool_use_error>" % (tool, tool)),
            "is_error": True,
            "tool_use_id": "toolu_01FAKE",
        }]},
        "toolUseResult": "Error: No such tool available: %s." % tool,
    }


BENIGN_RECORDS = [
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "content": "ok", "is_error": False, "tool_use_id": "t1"}]}},
    {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "looking at the code"}]}},
]


def transcript(name, records):
    """Write a transcript at the REAL on-disk layout, so the subagent-path derivation
    (<project>/<session>.jsonl + <project>/<session>/subagents/agent-<id>.jsonl) is
    exercised rather than assumed."""
    d = os.path.join(TMP, name)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "sess.jsonl")
    with open(p, "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return p


def agent_transcript(main_path, agent_id, records):
    d = os.path.join(main_path[: -len(".jsonl")], "subagents")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "agent-%s.jsonl" % agent_id)
    with open(p, "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return p


def fire(sid, cmd, transcript_path=None, agent_id=None):
    """Run the real hook and report whether it emitted a nudge."""
    payload = {"tool_name": "Bash", "session_id": sid, "tool_input": {"command": cmd}}
    if transcript_path is not None:
        payload["transcript_path"] = transcript_path
    if agent_id is not None:
        payload["agent_id"] = agent_id
    rc, out = run(payload)
    check("suppression rc 0 (%s)" % sid.split("-")[3], rc, 0)
    return bool(out)


# --- POSITIVE CONTROL: a transcript with no error record must still nudge. -----
sid = session("clean")
clean = transcript("clean", BENIGN_RECORDS)
check("clean transcript still nudges (positive control)", fire(sid, "grep -r TODO src/", clean), True)

# --- NEGATIVE: Grep proven unavailable -> the CONTENT nudge is suppressed. -----
sid = session("grep-gone")
gone = transcript("grep-gone", BENIGN_RECORDS + [unavailable_record("Grep")])
check("Grep unavailable suppresses the content nudge", fire(sid, "grep -r TODO src/", gone), False)
# Per-TOOL, not session-wide: only Grep was proven missing, so the Glob nudge still
# fires. Pins the scope of the claim — a session-wide flag would silence this too.
check("Glob nudge still fires when only Grep was proven gone",
      fire(sid, "find . -name '*.py'", gone), True)

# --- Both proven unavailable -> both suppressed. -------------------------------
sid = session("both-gone")
both = transcript("both-gone", [unavailable_record("Grep"), unavailable_record("Glob")])
check("both tools gone: content suppressed", fire(sid, "grep -r TODO src/", both), False)
check("both tools gone: files suppressed", fire(sid, "find . -name '*.py'", both), False)

# --- The verdict is CACHED: a later call with no transcript at all stays quiet. -
# Proves the short-circuit works (and that the scan is not re-run on every call).
check("suppression persists once recorded", fire(sid, "grep -R other .", None), False)
# ...and it is cached as a TOOL VERDICT, not by spending the nudge budget. Asserted on
# the state file because the behavioural check above cannot tell the two apart: a hook
# that simply emitted both nudges and marked both kinds used would also fall silent
# here. Pre-change this file reads ["content", "files"].
recorded = sorted(open(cache_path(sid)).read().split()) if os.path.exists(cache_path(sid)) else []
check("suppression records a tool verdict, not a spent nudge budget",
      recorded, ["no:Glob", "no:Grep"])

# --- STRUCTURAL, not spelled: the same TEXT in a non-error position must not ----
# suppress. This is the real transcript hazard — the error wording appears verbatim
# inside an Agent tool_use prompt and inside a subagent's report, both is_error false.
sid = session("prose")
_echoed = unavailable_record("Grep")["message"]["content"][0]["content"]
prose = transcript("prose", [
    {"type": "assistant", "message": {"role": "assistant", "content": [{
        "type": "tool_use", "id": "toolu_x", "name": "Agent",
        "input": {"prompt": "the harness said: Error: No such tool available: Grep. "
                            "Grep is not available in this session — fix the hook"}}]}},
    {"type": "user", "message": {"role": "user", "content": [{
        "type": "tool_result", "is_error": False, "tool_use_id": "toolu_x",
        "content": "report: Error: No such tool available: Grep. (quoted, not an error)"}]}},
    # 🔴 The one that actually pins `is_error` as the discriminator: a tool_result whose
    # text is BYTE-IDENTICAL to the real error record's, differing ONLY in the flag.
    # This is what a Bash call that prints a transcript record produces — i.e. exactly
    # what debugging this hook does. Without it, dropping the is_error check survives:
    # the other two fixtures are rejected by the leading anchor instead, so they exercise
    # the regex anchor, not the structural guard.
    {"type": "user", "message": {"role": "user", "content": [{
        "type": "tool_result", "is_error": False, "tool_use_id": "toolu_y",
        "content": _echoed}]},
     "toolUseResult": _echoed},
])
check("quoted error prose does NOT suppress", fire(sid, "grep -r TODO src/", prose), True)

# --- A DIFFERENT tool being unavailable must not suppress Grep/Glob. -----------
sid = session("other-tool")
other = transcript("other-tool", [unavailable_record("WebFetch")])
check("an unrelated missing tool does not suppress", fire(sid, "grep -r TODO src/", other), True)

# --- SUBAGENT SCOPING ---------------------------------------------------------
# transcript_path is derived from the SESSION id, which a subagent shares with its
# parent — so a subagent's own errors are only in its per-agent transcript.
sid = session("subagent")
parent = transcript("subagent", BENIGN_RECORDS)
agent_transcript(parent, "agentX", [unavailable_record("Grep")])
check("subagent's own transcript is consulted",
      fire(sid, "grep -r TODO src/", parent, agent_id="agentX"), False)
# ...and it must NOT leak: the parent session (same session_id, no agent_id) reads only
# the parent transcript, which is clean, so its nudge is still delivered.
check("subagent suppression does not leak to the parent session",
      fire(sid, "grep -r TODO src/", parent), True)
# ...nor to a SIBLING agent whose own transcript is clean.
agent_transcript(parent, "agentY", BENIGN_RECORDS)
check("subagent suppression does not leak to a sibling agent",
      fire(sid, "grep -r TODO src/", parent, agent_id="agentY"), True)

# --- FAIL OPEN: every detection failure leaves behaviour exactly as before. ----
sid = session("missing-file")
check("nonexistent transcript path -> nudge unchanged",
      fire(sid, "grep -r TODO src/", os.path.join(TMP, "nope", "sess.jsonl")), True)

sid = session("garbage")
garbage = os.path.join(TMP, "garbage.jsonl")
with open(garbage, "w") as fh:
    fh.write("not json\n{\"truncated\": \n\x00\x01binary\n")
check("unparseable transcript -> nudge unchanged", fire(sid, "grep -r TODO src/", garbage), True)

sid = session("is-a-dir")
check("transcript path is a directory -> nudge unchanged", fire(sid, "grep -r TODO src/", TMP), True)

sid = session("unreadable")
unreadable = os.path.join(TMP, "unreadable.jsonl")
with open(unreadable, "w") as fh:
    fh.write(json.dumps(unavailable_record("Grep")) + "\n")
os.chmod(unreadable, 0o000)
# Skipped under a uid that ignores the mode (root in some CI images) — asserting there
# would pin the sandbox's uid, not the hook.
if not os.access(unreadable, os.R_OK):
    check("unreadable transcript -> nudge unchanged", fire(sid, "grep -r TODO src/", unreadable), True)
os.chmod(unreadable, 0o644)

sid = session("nul-byte")
# Reaches the OTHER fail-open branch — the one around path RESOLUTION rather than the
# one around reading. Getting there needs both an agent_id and a NUL: os.path.isfile()
# swallows ValueError and returns False (checked — a NUL path alone proves nothing),
# but the nested-subagent glob does not: `scandir: embedded null character in path`.
# Without that handler the exception escapes into main()'s catch-all, which exits 0
# silently — so a detection ERROR would have become a silent SUPPRESSION.
check("embedded NUL in transcript_path -> nudge unchanged",
      fire(sid, "grep -r TODO src/", "/tmp/a\x00b.jsonl", agent_id="agentZ"), True)

sid = session("no-transcript-key")
check("payload without transcript_path -> nudge unchanged (legacy shape)",
      fire(sid, "grep -r TODO src/", None), True)

for i, bad in enumerate((123, [], {"a": 1}, "", "/not/a/jsonl", "/etc")):
    # A wrong-typed / non-jsonl transcript_path must never crash and never suppress.
    sid = session("badtype%d" % i)
    check("bad transcript_path %r -> nudge unchanged" % (bad,),
          fire(sid, "grep -r TODO src/", bad), True)

# Same for a wrong-typed agent_id (a `..` traversal must not become a scan target).
for i, bad_agent in enumerate((123, [], "../../etc", "a/b", "")):
    sid = session("badagent%d" % i)
    payload = {"tool_name": "Bash", "session_id": sid, "agent_id": bad_agent,
               "transcript_path": clean, "tool_input": {"command": "grep -r TODO src/"}}
    rc, out = run(payload)
    check("bad agent_id %r -> nudge unchanged" % (bad_agent,), (rc, bool(out)), (0, True))

for p in glob.glob(os.path.join(HOME, ".cache", "claude-search-tool-nudge",
                                "test-search-nudge-*")):
    try:
        os.remove(p)
    except OSError:
        pass
shutil.rmtree(TMP, ignore_errors=True)

MIN_CHECKS = 100  # floor: a suite that silently shrinks is a vacuous green
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
      f"1 harness negative control, 1 benign-counter positive control, "
      f"{len(SESSIONS)} availability-suppression scenarios)")
