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
import os, sys, glob, json, time, shutil, subprocess, tempfile, threading, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "search-tool-nudge.py")
HOME = os.path.expanduser("~")

# --------------------------------------------------------------------------- #
# 🔴 STATE ISOLATION — BEFORE the hook is imported, because it resolves its cache
# root at IMPORT time into a module constant.
#
# This file is a CONCURRENCY suite. It asserts "exactly one nudge across 12
# parallel invocations" for a session id it owns and "no test state leaks into the
# next run" for a prefix it cleans — and neither is true of a directory another
# process is writing. Against the old hardcoded `$HOME/.cache/…` root that is
# exactly what happened: measured 2026-08-20 with three to four gate runs in
# flight, this file went red twice on those two checks, inside the gate runner's
# own positive controls, and the FALSE RED was attributed to an unrelated PR.
#
# `scripts/run-tests.sh` (GUARD 9) exports a per-run root, and that value is what
# is honoured when present. The mkdtemp below is for the OTHER way this file runs —
# by hand, per the docstring — so a hand run is isolated too rather than isolated
# only under the gate. os.environ is set (not just a local) because every check
# here fires the hook as a SUBPROCESS, which inherits it.
#
# Deliberately NOT the seam pin: with this fallback in place, a runner that stopped
# exporting the variable would leave this file green. That claim belongs to
# `scripts/tests/test_search_nudge_state_isolation.py`, which pins the runner's
# export, the hook's read, and that the two spell the same name.
# --------------------------------------------------------------------------- #
_OWN_STATE_ROOT = None
if not os.environ.get("SEARCH_TOOL_NUDGE_CACHE_DIR"):
    _OWN_STATE_ROOT = tempfile.mkdtemp(prefix="search-tool-nudge-state-")
    os.environ["SEARCH_TOOL_NUDGE_CACHE_DIR"] = _OWN_STATE_ROOT

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


# 🔴 Taken from the HOOK, never recomputed. A second copy of the layout agrees with
# the first until the day one side moves, and the glob below would then clean and
# inspect a directory the hook does not use — every "no state leaked" check reading
# a reassuring zero from a counter wired to nothing.
STATE_ROOT = mod.STATE_ROOT
# ...and the isolation the header set up must actually be in force. If this suite is
# reading a root shared with the whole box, its concurrency and leak checks are not
# measuring the hook.
check("this suite's state root is the isolated one, not $HOME's",
      STATE_ROOT.startswith(os.environ["SEARCH_TOOL_NUDGE_CACHE_DIR"] + os.sep)
      and not STATE_ROOT.startswith(os.path.join(HOME, ".cache")), True)
# Every test session id starts with one of these, and the suite wipes their state dirs
# both before and after. 🔴 This suite MUST be idempotent: state that survives a run
# makes the NEXT run fail, which during a mutation sweep reads as "mutant killed" for
# every mutant — a broken harness reporting a perfect score. Guarded below by
# re-running the whole file and asserting the second run is identical.
TEST_SID_PREFIXES = ("test-session-search-nudge-", "test-search-nudge-", "test-search-nudge-collide")


def clear_test_state():
    for pref in TEST_SID_PREFIXES:
        for p in glob.glob(os.path.join(STATE_ROOT, pref + "*")):
            shutil.rmtree(p, ignore_errors=True)
        # Also the LEGACY layout (one flat FILE per session, pre-dating the state dir).
        # Not housekeeping: without it, running this file against an older revision of
        # the hook leaves state the newer cleanup cannot see, and the next run reads as
        # "everything suppressed" — which is exactly how a contaminated measurement gets
        # mistaken for 44 regressions.
        for p in glob.glob(os.path.join(os.path.dirname(STATE_ROOT), pref + "*")):
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


clear_test_state()
sid = "test-session-search-nudge-DO-NOT-COLLIDE"

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

clear_test_state()

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


def internal(name):
    """Resolve a hook internal, recording a LOUD failure if it is missing.

    Not a skip: a check that quietly vanishes when the symbol is gone is worse than no
    check. This also lets the whole file run against an OLDER revision of the hook — the
    missing symbols report as red and every other check still executes, which is what
    makes a per-check red/green matrix across revisions possible at all.
    """
    obj = getattr(mod, name, None)
    check("hook exposes %s" % name, obj is not None, True)
    return obj


def _san(part):
    return "".join(c if c.isalnum() or c in "_.-" else "_" for c in part)[:120]


def cache_path(sid, agent=None):
    """The hook's per-session state DIRECTORY. Components are sanitized individually and
    joined with a literal "@" — see _state_dir's note on why that separator is the thing
    that makes the key injective."""
    key = "@".join([_san(sid)] + ([_san(agent)] if agent else []))
    return os.path.join(STATE_ROOT, key)


def recorded_tokens(sid, agent=None):
    try:
        return sorted(os.listdir(cache_path(sid, agent)))
    except OSError:
        return []


def session(name):
    """A fresh session id whose state dir is cleared now and removed at exit."""
    sid = "test-search-nudge-%s-DO-NOT-COLLIDE" % name
    SESSIONS.append(sid)
    for p in glob.glob(os.path.join(STATE_ROOT, _san(sid) + "*")):
        shutil.rmtree(p, ignore_errors=True)
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
# The same distinction inside ONE command that earns BOTH kinds: the Grep half must be
# dropped and the Glob half still delivered. A session-wide "any tool missing -> stay
# silent" passes every check above and fails only here.
sid = session("grep-gone-both")
gone2 = transcript("grep-gone-both", [unavailable_record("Grep")])
rc_b, out_b = run({"tool_name": "Bash", "session_id": sid, "transcript_path": gone2,
                   "tool_input": {"command": "grep -r foo . && find . -name '*.md'"}})
check("one command, both kinds: only the unavailable half is dropped",
      ("Glob tool" in out_b, "Grep tool" in out_b), (True, False))

# --- Both proven unavailable -> both suppressed. -------------------------------
sid = session("both-gone")
both = transcript("both-gone", [unavailable_record("Grep"), unavailable_record("Glob")])
check("both tools gone: content suppressed", fire(sid, "grep -r TODO src/", both), False)
check("both tools gone: files suppressed", fire(sid, "find . -name '*.py'", both), False)

# --- The verdict is CACHED: a later call with no transcript at all stays quiet. -
# Proves the short-circuit works (and that the scan is not re-run on every call).
check("suppression persists once recorded", fire(sid, "grep -R other .", None), False)
# ...and it is cached as a TOOL VERDICT, not merely by spending the nudge budget.
# Asserted on the state dir because the behavioural check above cannot tell the two
# apart: a hook that simply emitted both nudges and marked both kinds used would also
# fall silent here. Pre-change this reads ["content", "files"] with no verdict at all.
# (The kinds ARE present too — a kind is claimed before the scan, so that the scan runs
# once and only one process can emit; see the claim protocol.)
check("suppression records a tool verdict alongside the spent kinds",
      recorded_tokens(sid), ["content", "files", "no:Glob", "no:Grep"])

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

# --- ANCHORED: the marker must START the error text, not merely appear in it. --
# A genuinely failed Bash call whose stderr quotes the marker is an is_error result, so
# is_error alone does not separate it — only the anchor does. Kills `.match -> .search`;
# there is deliberately no `^` in the pattern for a mutant to delete for free.
sid = session("mid-text")
midtext = transcript("mid-text", [{
    "type": "user", "message": {"role": "user", "content": [{
        "type": "tool_result", "is_error": True, "tool_use_id": "toolu_z",
        "content": "grep: exit 2\nlog line: Error: No such tool available: Grep. "
                   "Grep is not available in this session"}]}}])
check("marker mid-text in a real error does NOT suppress",
      fire(sid, "grep -r TODO src/", midtext), True)
# 🔴 SAME LINE, no newline before the marker. The fixture above is not enough on its
# own: `.` does not cross newlines, so a mutant that loosens the pattern to
# `.*No such tool available: (…)` still fails to match it and survives. This is the
# realistic shape — a failed command whose own stderr quotes the marker on one line —
# and under that mutant it is FALSELY SUPPRESSED.
sid = session("mid-line")
midline = transcript("mid-line", [{
    "type": "user", "message": {"role": "user", "content": [{
        "type": "tool_result", "is_error": True, "tool_use_id": "toolu_z1",
        "content": "Command failed with exit code 2: hook.py: Error: No such tool "
                   "available: Grep. (matched 0 files)"}]}}])
check("marker mid-LINE in a real error does NOT suppress",
      fire(sid, "grep -r TODO src/", midline), True)

# --- The list-form content branch is real, not speculative. -------------------
sid = session("list-form")
listform = transcript("list-form", [{
    "type": "user", "message": {"role": "user", "content": [{
        "type": "tool_result", "is_error": True, "tool_use_id": "toolu_l",
        "content": [{"type": "text",
                     "text": "Error: No such tool available: Grep. gone."}]}]}}])
check("list-form error content is understood", fire(sid, "grep -r TODO src/", listform), False)

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

# A NESTED agent (an agent spawned by an agent) lives one level deeper; the glob
# fallback is what finds it. Without it this transcript is never consulted.
sid = session("nested-agent")
nested_parent = transcript("nested-agent", BENIGN_RECORDS)
nested_dir = os.path.join(nested_parent[: -len(".jsonl")], "subagents", "outer")
os.makedirs(nested_dir, exist_ok=True)
with open(os.path.join(nested_dir, "agent-inner1.jsonl"), "w") as fh:
    fh.write(json.dumps(unavailable_record("Grep")) + "\n")
check("nested subagent transcript is found via the glob fallback",
      fire(sid, "grep -r TODO src/", nested_parent, agent_id="inner1"), False)

# --- agent_id VALIDATION, asserted on the RESOLVED SCAN TARGET. ---------------
# The outcome alone cannot see this: `_transcript_paths` is called directly so the
# assertion is about which files would be READ, not about whether a nudge appeared.
# A glob metacharacter is the realistic leak — unvalidated, "*" makes the fallback
# glob match a SIBLING agent's transcript and inherit its verdict.
sid = session("agent-glob")
sib_parent = transcript("agent-glob", BENIGN_RECORDS)
agent_transcript(sib_parent, "realsibling", [unavailable_record("Grep")])
_paths = internal("_transcript_paths")
check("a glob metacharacter in agent_id resolves to NO extra scan target",
      _paths({"transcript_path": sib_parent, "agent_id": "*"}) if _paths else None,
      [sib_parent])
check("...and a traversal in agent_id resolves to no extra scan target either",
      _paths({"transcript_path": sib_parent, "agent_id": "../../etc"}) if _paths else None,
      [sib_parent])
# Behavioural companion: it must not inherit the sibling's verdict.
check("agent_id='*' does not inherit a sibling's suppression",
      fire(sid, "grep -r TODO src/", sib_parent, agent_id="*"), True)

# --- STATE KEY IS INJECTIVE ---------------------------------------------------
# session="…a" + agent="b" must not share a state dir with session="…a_b" alone.
# Joining sanitized components with any character the sanitizer can EMIT (e.g. "_")
# makes these collide, and the first call then silences the second.
# The pair has to be EXACT for the collision to exist, so these ids are built raw
# rather than through session(): X with agent "b", versus the session literally named
# X_b. sanitize(X + "@" + "b") == "X_b" == sanitize("X_b") — one state dir for both.
X = "test-search-nudge-collide"
for _sid in (X, X + "_b"):
    SESSIONS.append(_sid)
for _p in glob.glob(os.path.join(STATE_ROOT, X + "*")):
    shutil.rmtree(_p, ignore_errors=True)
check("state key: agent-scoped call nudges",
      fire(X, "grep -r TODO src/", None, agent_id="b"), True)
check("state key: the session that ALIASES it still gets its own nudge",
      fire(X + "_b", "grep -r TODO src/", None), True)

# 🔴 Pin the PROPERTY, not the separator's spelling. The pair above only exercises the
# "_" alias, so widening the sanitizer's allowed set to include "@" keeps it green while
# silently reintroducing exactly the non-injectivity it was written to catch: the key
# is injective only because "@" cannot survive _sanitize.
_san_fn = internal("_sanitize")
check("the join separator cannot survive sanitization",
      _san_fn("@") if _san_fn else None, "_")
# ...and the behavioural pair that "@" makes collide if the property is broken.
Y = "test-search-nudge-atcollide"
for _sid in (Y, Y + "@b"):
    SESSIONS.append(_sid)
for _p in glob.glob(os.path.join(STATE_ROOT, Y + "*")):
    shutil.rmtree(_p, ignore_errors=True)
check("state key: '@' pair — agent-scoped call nudges",
      fire(Y, "grep -r TODO src/", None, agent_id="b"), True)
check("state key: '@' pair — the raw '@' session still gets its own nudge",
      fire(Y + "@b", "grep -r TODO src/", None), True)

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

# --- SCAN CAP: truncation fails OPEN, and the boundary is pinned at 2 points. --
# Measured at both ends rather than one: a cap that admits the error line exactly, and
# one byte less. Without both, an off-by-one on the comparison is invisible.
cap_records = BENIGN_RECORDS + [unavailable_record("Grep")]
cap_file = transcript("cap", cap_records)
with open(cap_file, "rb") as fh:
    _cap_bytes = fh.read()
EXACT = len(_cap_bytes)  # budget that admits the final (error) line in full


def fire_with_cap_agent(sid, cap, transcript_path, agent_id=None):
    payload = {"tool_name": "Bash", "session_id": sid, "transcript_path": transcript_path,
               "tool_input": {"command": "grep -r TODO src/"}}
    if agent_id is not None:
        payload["agent_id"] = agent_id
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                       capture_output=True, text=True,
                       env=dict(os.environ, SEARCH_TOOL_NUDGE_MAX_SCAN_BYTES=str(cap)))
    check("cap rc 0", p.returncode, 0)
    return bool(p.stdout.strip())


def fire_with_cap(sid, cap, transcript_path):
    return fire_with_cap_agent(sid, cap, transcript_path)


sid = session("cap-exact")
check("cap exactly covering the error line still detects it",
      fire_with_cap(sid, EXACT, cap_file), False)
sid = session("cap-short")
check("cap one byte short truncates and fails OPEN (nudge delivered)",
      fire_with_cap(sid, EXACT - 1, cap_file), True)
sid = session("cap-tiny")
check("a tiny cap fails OPEN rather than suppressing",
      fire_with_cap(sid, 1, cap_file), True)

# 🔴 The override is read at IMPORT time, outside main()'s try. An unparseable value
# there escapes as a non-zero exit with a traceback on EVERY Bash call in the session —
# the one thing the module docstring promises never happens, and a typo in a shell
# profile is enough to trigger it. Every junk shape must exit 0 AND still nudge.
for _i, _bad in enumerate(("abc", "", "  ", "-1", "0", "1e9", "9" * 400, "12abc", "0x10")):
    sid = session("cap-junk-%d" % _i)
    _p = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps({"tool_name": "Bash", "session_id": sid,
                          "tool_input": {"command": "grep -r TODO src/"}}),
        capture_output=True, text=True,
        env=dict(os.environ, SEARCH_TOOL_NUDGE_MAX_SCAN_BYTES=_bad))
    check("junk scan-cap %r: exit 0, no traceback, nudge delivered" % _bad,
          (_p.returncode, "Traceback" in _p.stderr, bool(_p.stdout.strip())),
          (0, False, True))

# ...and a junk value must FALL BACK to the default, not silently disable detection.
# Asserting "no crash" alone cannot see that: with a non-positive cap accepted verbatim,
# every scan truncates on its first line, which also fails open and also nudges — the
# same observable, a completely different reason. Only a transcript that SHOULD suppress
# separates them.
for _i, _bad in enumerate(("0", "-1", "abc")):
    sid = session("cap-fallback-%d" % _i)
    _p = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps({"tool_name": "Bash", "session_id": sid, "transcript_path": gone,
                          "tool_input": {"command": "grep -r TODO src/"}}),
        capture_output=True, text=True,
        env=dict(os.environ, SEARCH_TOOL_NUDGE_MAX_SCAN_BYTES=_bad))
    check("junk scan-cap %r falls back to the default (detection still works)" % _bad,
          (_p.returncode, bool(_p.stdout.strip())), (0, False))

# --- CONCURRENCY: at most ONE nudge per kind per session, under real parallelism.
# 12 processes released by a shared barrier so their read-modify-write windows overlap.
# Measured pre-fix on this exact harness: 10-11 duplicates against a large transcript.
sid = session("concurrent")
# 🔴 Fixture SIZE is what makes this check able to fail on broken code, and it is not a
# free parameter. Pre-fix, the race window IS the transcript scan sitting between the
# state read and the state write, so it scales with the file. At ~100 KB the check was
# flaky against the pre-fix hook — 11 runs gave 2,3,3,3,3,5,5,6,10 duplicates and ONCE
# gave 1, i.e. GREEN on code that was broken. A test that can pass on the bug it exists
# to catch is not a test. Sized so the window is milliseconds, not microseconds; the
# post-fix hook claims before scanning, so it pays this scan exactly once. Measured
# after: reliably red on the pre-fix hook, 5 runs of 5.
#
# Scope of that claim, so nobody later "fixes" the remainder: this is reliable against
# the pre-fix hook, whose window IS the scan. It stays a coin flip (2/5) against base
# main, which never reads a transcript at all — that race is genuinely microseconds wide
# and NO fixture size widens it. Base main's narrow race is a real but separate defect,
# and this check is not the instrument for it.
CONC_BYTES = 8 * 1024 * 1024
conc_transcript = os.path.join(TMP, "concurrent-big.jsonl")
_filler = {"type": "assistant", "message": {"role": "assistant", "content": [
    {"type": "text", "text": "x" * 400}]}}
with open(conc_transcript, "w") as fh:
    _line = json.dumps(_filler) + "\n"
    for _ in range(CONC_BYTES // len(_line)):
        fh.write(_line)


def _one(_i, out, idx, barrier):
    p = subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    # 🔴 Let every child finish interpreter startup and block on stdin BEFORE the
    # barrier releases. Without this the ~30 ms of Python start-up staggers the
    # workers by far more than the ~1 ms of hook work, so they serialise and the
    # race never happens — a concurrency test that cannot observe a broken claim.
    # Measured: with the stagger, deleting O_EXCL still produced exactly 1 nudge.
    time.sleep(0.6)
    barrier.wait()
    stdout, _e = p.communicate(json.dumps(
        {"tool_name": "Bash", "session_id": sid, "transcript_path": conc_transcript,
         "tool_input": {"command": "grep -r TODO src/"}}))
    out[idx] = (p.returncode, stdout.strip())


NPROC = 12
_out = [None] * NPROC
_barrier = threading.Barrier(NPROC)
_threads = [threading.Thread(target=_one, args=(i, _out, i, _barrier)) for i in range(NPROC)]
for _t in _threads:
    _t.start()
for _t in _threads:
    _t.join()
check("concurrency: every invocation exits 0",
      [rc for rc, _ in _out], [0] * NPROC)
check("concurrency: exactly one nudge across %d parallel invocations" % NPROC,
      sum(1 for _rc, o in _out if o), 1)

# --- The claim PRIMITIVE, asserted directly. ----------------------------------
# 🔴 The end-to-end check above cannot see a broken claim: with the claim placed ahead
# of the scan the read->claim window is microseconds wide, so O_EXCL can be deleted and
# 12 racing processes STILL produce one nudge (measured — that mutant survived the
# end-to-end test). The atomicity is therefore pinned where it actually lives.
_claim = internal("_claim")
_claimdir = os.path.join(TMP, "claimdir")
check("claim: first caller wins", _claim(_claimdir, "content") if _claim else None, True)
check("claim: second caller loses (O_EXCL test-and-set)",
      _claim(_claimdir, "content") if _claim else None, False)
check("claim: a different token is independent",
      _claim(_claimdir, "files") if _claim else None, True)
# Fail OPEN, not closed: an unwritable state dir must let the nudge through (the
# pre-existing behaviour) rather than silently swallow it.
_ro = os.path.join(TMP, "readonly")
os.makedirs(_ro, exist_ok=True)
os.chmod(_ro, 0o500)
if not os.access(_ro, os.W_OK):
    # The state dir cannot be CREATED -> the makedirs handler.
    check("claim: unwritable parent (dir cannot be created) fails OPEN",
          _claim(os.path.join(_ro, "sess"), "content") if _claim else None, True)
os.chmod(_ro, 0o700)
# The state dir EXISTS but the marker cannot be created -> the open()'s generic handler,
# a different branch entirely. Without this the two are conflated and "fail closed on a
# generic error" survives: every existing test reaches the makedirs handler instead.
_ro2 = os.path.join(TMP, "readonly-existing")
os.makedirs(_ro2, exist_ok=True)
os.chmod(_ro2, 0o500)
if not os.access(_ro2, os.W_OK):
    check("claim: existing but unwritable state dir fails OPEN",
          _claim(_ro2, "content") if _claim else None, True)
os.chmod(_ro2, 0o700)
check("claim: no state dir at all fails OPEN", _claim(None, "content") if _claim else None, True)
# 🔴 makedirs(exist_ok=True) ALSO raises FileExistsError — when the path is a regular
# file or a symlink rather than a directory (a leftover flat state file from the old
# layout is exactly that). Sharing one handler with the O_EXCL open made that
# indistinguishable from "lost the race", so the nudge vanished for the whole session:
# fail CLOSED, contradicting the docstring. Each blocker is checked separately because
# they reach makedirs by different routes.
for _label, _mk in (
    ("regular file", lambda p: open(p, "w").close()),
    ("dangling symlink", lambda p: os.symlink(os.path.join(TMP, "nonexistent"), p)),
    ("symlink to a file", lambda p: (open(p + ".t", "w").close(), os.symlink(p + ".t", p))),
):
    _blocked = os.path.join(TMP, "blocked-" + _label.replace(" ", "-"))
    _mk(_blocked)
    check("claim: state path is a %s -> fails OPEN" % _label,
          _claim(_blocked, "content") if _claim else None, True)

# 🔴 The claim's ANSWER must be honoured by main(), not just computed. Proving that
# needs a state where the marker EXISTS but the directory listing does NOT show it —
# otherwise the cheap `_read_state` fast-path filters the kind first and the claim is
# never consulted, so "ignore the claim result" is invisible. A write+execute but
# NOT readable directory is exactly that state: listdir() fails (fail-open -> "nothing
# recorded"), while open(O_EXCL) on the existing marker still raises FileExistsError.
# Deterministic — the alternative, inferring it from the 12-way race, is flaky: that
# mutant was killed on one sweep and survived the next.
sid = session("claim-honoured")
_cdir = cache_path(sid)
os.makedirs(_cdir, exist_ok=True)
open(os.path.join(_cdir, "content"), "w").close()
os.chmod(_cdir, 0o300)
if not os.access(_cdir, os.R_OK):
    check("the claim's answer is honoured (marker present, listing blind)",
          fire(sid, "grep -r TODO src/", None), False)
os.chmod(_cdir, 0o700)

# --- Scan ORDER is load-bearing, not cosmetic. --------------------------------
# Most-specific-first only shows up when the budget cannot cover both files: the
# subagent's own transcript must be read BEFORE the parent's, or a large parent eats
# the budget and the agent's own verdict is never seen.
sid = session("scan-order")
order_parent = transcript("scan-order", BENIGN_RECORDS * 300)
agent_transcript(order_parent, "agentO", [unavailable_record("Grep")])
_agent_len = os.path.getsize(os.path.join(order_parent[: -len(".jsonl")], "subagents",
                                          "agent-agentO.jsonl"))
check("agent transcript is scanned before the parent (budget covers only one)",
      fire_with_cap_agent(sid, _agent_len, order_parent, "agentO"), False)

# --- Only a .jsonl is treated as a transcript. --------------------------------
sid = session("not-jsonl")
_notjson = os.path.join(TMP, "notatranscript.txt")
with open(_notjson, "w") as fh:
    fh.write(json.dumps(unavailable_record("Grep")) + "\n")
check("a non-.jsonl transcript_path is not scanned", fire(sid, "grep -r TODO src/", _notjson), True)

# --- State key components are sanitized (no directory escape). ----------------
_sdir = internal("_state_dir")
_root = internal("STATE_ROOT")
check("a traversal in session/agent ids cannot escape the state root",
      os.path.dirname(_sdir({"session_id": "../../escape", "agent_id": "../x"}))
      if (_sdir and _root) else None, _root)

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

clear_test_state()
leaked = sorted(os.path.basename(p) for pref in TEST_SID_PREFIXES
                for p in glob.glob(os.path.join(STATE_ROOT, pref + "*")))
# 🔴 IDEMPOTENCE GUARD. State left behind by one run makes the NEXT run fail — and during
# a mutation sweep that reads as "every mutant killed", including mutants that change
# nothing. That is exactly what happened while writing this file: a 31/31 perfect score
# from a harness that was simply dirty. Cheap to assert, so assert it.
check("no test state leaks into the next run", leaked, [])
shutil.rmtree(TMP, ignore_errors=True)
# Only the root THIS file created — never the one the runner handed down, which is
# the runner's to remove and may be shared with the other hook tests in the run.
if _OWN_STATE_ROOT:
    shutil.rmtree(_OWN_STATE_ROOT, ignore_errors=True)

MIN_CHECKS = 160  # floor: a suite that silently shrinks is a vacuous green
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
