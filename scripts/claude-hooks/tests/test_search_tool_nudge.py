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
import os, sys, glob, json, time, shutil, subprocess, tempfile, threading, atexit, importlib.util

# --------------------------------------------------------------------------- #
# 🔴 HOME ISOLATION — must run BEFORE anything reads `~`, including the import
# of the hook below.
#
# This suite GLOBS AND DELETES state directories, and it drives the hook as a
# real subprocess a hundred-odd times. The hook resolves its CACHE_DIR from
# `os.path.expanduser("~")`, which on POSIX is `os.environ["HOME"]` (verified:
# posixpath.expanduser returns $HOME when set, and only falls back to the passwd
# entry when it is absent). Pointed at the operator's real home that means:
#
#   * the suite deletes real `~/.cache/claude-search-tool-nudge/` entries, and
#   * the LIVE search-tool-nudge hook — which fires on every Bash call of any
#     Claude Code session running on this box, including the one running the
#     gate — writes into the very state directory the suite is asserting on.
#     Measured: the suite fails spuriously that way, and anyone running
#     scripts/gate.sh from inside an active session hits it.
#
# So: one throwaway HOME, set before line-one of the constants below, exported
# into `os.environ` so EVERY subprocess spawned from here inherits it. Every
# spawn in this file either passes no `env=` (inherits `os.environ`) or builds
# it as `dict(os.environ, …)`, so all of them carry it —
# `scripts/tests/test_hook_suites_do_not_touch_the_inherited_home.py` pins that
# structurally so a hand-built `env=` cannot quietly reintroduce the leak.
# --------------------------------------------------------------------------- #
TEST_HOME = tempfile.mkdtemp(prefix="search-tool-nudge-HOME-")
os.environ["HOME"] = TEST_HOME
atexit.register(shutil.rmtree, TEST_HOME, ignore_errors=True)

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "search-tool-nudge.py")
HOME = os.path.expanduser("~")  # == TEST_HOME; the hook resolves the same value
assert HOME == TEST_HOME, (
    "HOME redirection did not take effect (%r != %r) — every path below would "
    "resolve into the operator's real home." % (HOME, TEST_HOME))

spec = importlib.util.spec_from_file_location("search_tool_nudge", HOOK)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
analyze = mod.analyze
CONTENT = mod.CONTENT

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
# MUST FIRE — a tree WALK by a searcher that honours .gitignore.
#
# 🔴 THE POSITIVE CONTROL for the whole retarget. Without it a silenced hook is
# indistinguishable from a broken one: every "must not fire" case below would pass
# just as well against a detector that was wired to nothing.
#
# The hazard these share, reproduced live on this host (see the hook's header):
# whole-tree `grep -rn` found 1 where GNU grep found 2, and bare `rg` found 1 where
# `rg --no-ignore` found 2.
# --------------------------------------------------------------------------- #
MUST_FIRE = [
    # grep -r / -R across a tree: the shell-snapshot function execs ugrep
    # --ignore-files, so the walk is gitignore-pruned.
    ("grep -r", "grep -r TODO src/", [CONTENT]),
    ("grep -R", "grep -R 'def main' .", [CONTENT]),
    ("grep clustered flags", "grep -rn --color=never foo lib/", [CONTENT]),
    ("grep --recursive", "grep --recursive pattern .", [CONTENT]),
    ("sudo grep -r", "sudo grep -r secret /etc", [CONTENT]),
    # ripgrep and friends walk from cwd AND honour .gitignore by default
    ("bare rg", "rg 'class Foo'", [CONTENT]),
    ("rg with path", "rg -n TODO scripts/", [CONTENT]),
    ("ag", "ag pattern src", [CONTENT]),
    # `rg --files` LISTS files rather than searching contents, so it used to be the
    # Glob-recommending kind. It stays a firing case under the retarget for a reason
    # measured on this host: it is gitignore-blind too — `rg --files` listed 1 where
    # `rg --files --no-ignore` listed 2. The hazard is the walk, not the output mode.
    ("rg --files", "rg --files", [CONTENT]),
    # multi-command: a search hidden after a &&
    ("chained after &&", "git status && grep -r foo .", [CONTENT]),
    # multi-line: shlex treats \n as plain whitespace, so without an explicit
    # newline->separator pass the whole block lexes as one `cd` command and the
    # search is invisible. Caught by mutant M1.
    ("search on a later LINE", "cd /repo\necho hi\ngrep -r foo src/", [CONTENT]),
    # one nudge, not two, when a call carries several blind searches
    ("two blind searches dedupe to one kind", "grep -r a . && rg b", [CONTENT]),
    # wrappers that do NOT change which binary runs must stay transparent
    ("env grep -r", "env grep -r TODO src/", [CONTENT]),
    ("time grep -r", "time grep -r TODO src/", [CONTENT]),
    ("nice rg", "nice rg TODO", [CONTENT]),
    ("VAR=x grep -r", "LC_ALL=C grep -r TODO src/", [CONTENT]),
    ("grep -r after a semicolon", "cd /repo; grep -r foo .", [CONTENT]),
    ("grep -r inside a pipeline HEAD", "grep -r foo . | head -20", [CONTENT]),
    ("grep --dereference-recursive", "grep --dereference-recursive foo .", [CONTENT]),
    ("ack", "ack pattern", [CONTENT]),
    # a flag cluster that merely CONTAINS r, and one where r is not first
    ("grep -inr cluster", "grep -inr TODO src/", [CONTENT]),
]
for name, cmd, want in MUST_FIRE:
    check("FIRE " + name, analyze(cmd), want)

# --------------------------------------------------------------------------- #
# MUST NOT FIRE — narrow/legitimate uses. False positives are the expensive
# failure mode here: this runs on EVERY Bash call.
# --------------------------------------------------------------------------- #
BENIGN = [
    # ---------------------------------------------------------------------- #
    # 🔴 THE RETARGET CASES — shapes this hook used to nudge about and can no
    # longer justify. Two distinct reasons, both MEASURED, not assumed:
    #
    #  (a) the old advice named Grep/Glob, which do not exist on this fleet:
    #      128 sessions reached for Grep and 0 used it; 5 reached for Glob and
    #      0 used it. `find -name` / `ls -R` / `rg --files` fired ONLY to
    #      recommend Glob, so with no tool to name there is nothing to say.
    #  (b) these shapes are not gitignore-blind anyway, so the NEW warning
    #      would be false: `find` is shadowed to `bfs`, which is not passed an
    #      ignore-files flag and DOES see gitignored paths; and a searcher fed
    #      EXPLICIT paths from xargs/-exec never prunes them.
    #      Control: `find … | xargs grep` returned the correct 2 where a bare
    #      tree walk returned 1.
    # ---------------------------------------------------------------------- #
    ("find -name (bfs sees gitignored files)", "find . -name '*.py'"),
    ("find -iname abs path", f"find {HOME}/workspace -iname 'README*'"),
    ("find -path", "find . -path '*/tests/*'"),
    ("find on a later LINE", "W=/tmp/x\nfind $W -name '*.py'"),
    ("ls -R", "ls -R nix/"),
    ("ls -Ral", "ls -Ral scripts"),
    ("find | xargs grep (explicit paths)", "find . -name '*.py' | xargs grep -l TODO"),
    ("find -print0 | xargs -0 grep", "find . -name '*.js' -print0 | xargs -0 grep foo"),
    ("find -exec grep", "find src -name '*.c' -exec grep -H TODO {} +"),
    ("xargs grep from a file list", "cat filelist.txt | xargs grep TODO"),
    # 🔴 The case that actually REACHES the via_xargs guard. Every other xargs shape is
    # already rejected by the recursion requirement (`xargs grep -l` is not recursive)
    # or by piped_in (`… | xargs rg`), so without a RECURSIVE grep behind xargs the
    # guard never executes and a mutation deleting it SURVIVES — measured, then fixed
    # by adding this case. Behaviour confirmed live: `find . -type f -print0 |
    # xargs -0 grep -rn` returned the correct 2, i.e. explicit paths are not pruned.
    ("xargs grep -r (explicit paths, -r is a no-op)",
     "find . -type f -print0 | xargs -0 grep -rn TODO"),
    # 🔴 The hook must not nag its own advice. `command grep` / an absolute path
    # bypass the shadowing function and ARE GNU grep; --no-ignore is the rg fix.
    ("command grep -r is the FIX", "command grep -r TODO src/"),
    ("absolute-path grep is GNU grep", "/usr/bin/grep -rn TODO src/"),
    ("nix-store grep is GNU grep", "/nix/store/abc-gnugrep-3.12/bin/grep -r TODO ."),
    ("rg --no-ignore is the FIX", "rg --no-ignore TODO"),
    ("rg --no-ignore-vcs is the FIX", "rg --no-ignore-vcs TODO src/"),
    ("rg -u is the FIX", "rg -u TODO"),
    ("rg --unrestricted is the FIX", "rg --unrestricted TODO"),
    ("rg -uu is the FIX", "rg -uu TODO"),
    ("rg -nu cluster is the FIX", "rg -nu TODO src/"),
    ("sudo command grep -r still bypasses", "sudo command grep -r TODO /etc"),
    ("env command grep -r still bypasses", "env command grep -r TODO src/"),
    ("relative-path grep bypasses", "./grep -r TODO src/"),
    # 🔴 Only the spelling `grep` is shadowed by the shell snapshot (it defines exactly
    # three functions: find, grep, pkill). These reach the real GNU binaries, so they
    # are NOT gitignore-blind and warning about them would be a false positive.
    # Measured on the planted-token fixture: `egrep -rn` and `fgrep -rn` both returned
    # the correct 2, where `grep -rn` returned 1.
    ("egrep -r is unshadowed GNU egrep", "egrep -r 'a|b' src/"),
    ("fgrep -R is unshadowed GNU fgrep", "fgrep -R literal ."),
    ("rgrep is unshadowed", "rgrep -r foo ."),
    ("ggrep -r is unshadowed", "ggrep -r foo src/"),
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
# 🔴 THE SHADOWED/UNSHADOWED LEDGER, enforced BOTH ways.
#
# The hazard exists only for the ONE spelling the shell snapshot shadows. The hook
# records that split as two named sets; without this block `UNSHADOWED_GREP` would be
# unused decoration, and a later "tidy-up" that folded it back into GREP_BINS would
# reintroduce the false positive with nothing to notice. So: every name in the
# unshadowed set must classify benign under a RECURSIVE invocation (the only shape
# that could fire at all), the sets must stay disjoint, and neither may go empty —
# an empty UNSHADOWED_GREP would make the first loop vacuous.
# --------------------------------------------------------------------------- #
check("the shadowed set is exactly {'grep'}", mod.GREP_BINS, {"grep"})
check("the unshadowed ledger is non-empty (else the loop below is vacuous)",
      len(mod.UNSHADOWED_GREP) > 0, True)
check("shadowed and unshadowed sets are disjoint",
      mod.GREP_BINS & mod.UNSHADOWED_GREP, set())
for _g in sorted(mod.UNSHADOWED_GREP):
    check("unshadowed %s -r is benign" % _g, analyze("%s -r foo src/" % _g), [])
# Positive control for that loop: the SAME call shape on the SHADOWED name must fire,
# so "all benign" cannot pass by the classifier being wired to nothing.
for _g in sorted(mod.GREP_BINS):
    check("shadowed %s -r fires (positive control)" % _g,
          analyze("%s -r foo src/" % _g), [CONTENT])

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


STATE_ROOT = os.path.join(HOME, ".cache", "claude-search-tool-nudge", "s")
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
check("io first-fire emits", bool(out) and "additionalContext" in out, True)
# It is a NUDGE: the payload must carry no deny/block decision of any kind.
parsed = json.loads(out) if out else {}
check("io hookEventName", parsed.get("hookSpecificOutput", {}).get("hookEventName"), "PostToolUse")
check("io never denies", any(k in out for k in ("permissionDecision", "\"deny\"", "block")), False)

# --------------------------------------------------------------------------- #
# 🔴 THE MESSAGE MUST NOT NAME AN ABSENT TOOL, and must not repeat the retired
# statistic. This is the assertion that actually pins the bug that prompted the
# retarget — the detector could be perfect while the text still told the agent to
# call something the session does not have.
#
# Pinned as an ENUMERATED ban rather than one spelling: "Grep tool" alone was
# walkable by rewording to "the Grep tool" or "Grep(". The old quoted figure is
# banned too, because it asserted a discipline failure the data does not support
# (it counted ATTEMPTS, essentially all of which failed).
# --------------------------------------------------------------------------- #
_ctx = parsed.get("hookSpecificOutput", {}).get("additionalContext", "")
for _banned in ("Grep", "Glob", "37.5k", "native tool", "structured results"):
    check("io message never mentions %r" % _banned, _banned in _ctx, False)
# Positive control for that ban: the SAME field is asserted to carry the thing it
# SHOULD say, so a message that went empty (or a field read that returns "") cannot
# pass the five bans above by vacuity.
check("io message says what IS actionable (.gitignore)", ".gitignore" in _ctx, True)
check("io message names the bypass", "command grep" in _ctx, True)

# second search of the same kind in the same session -> deduped, silent
rc2, out2 = run({"tool_name": "Bash", "session_id": sid,
                 "tool_input": {"command": "grep -r other ."}})
check("io dedupe silent", out2, "")
# a shape the hook can no longer justify -> silent even though the session is fresh
# for it. Paired with the fire above, this is the fire/no-fire pair for the IO path.
rc3, out3 = run({"tool_name": "Bash", "session_id": sid,
                 "tool_input": {"command": "find . -name '*.md'"}})
check("io retargeted-away kind is silent", (rc3, out3), (0, ""))

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
# STATE, CONCURRENCY AND FAIL-OPEN scenarios.
#
# 🔴 What is NOT here any more, deliberately: the availability-suppression suite that
# fed the hook a transcript containing `Error: No such tool available: Grep` and asserted
# the nudge went quiet. That machinery is gone from the hook (see its header — the signal
# only ever arrived AFTER the model had already eaten the failed tool call), so those
# tests would have been asserting against deleted code.
#
# Each scenario gets its OWN session id so one scenario's state cannot decide another's
# outcome. `fire()` still accepts a transcript_path because the payload really carries
# one; the hook now ignores it, and the fail-open cases below pin that a malformed value
# still cannot change the outcome or crash the hook.
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


def fire(sid, cmd, transcript_path=None, agent_id=None):
    """Run the real hook and report whether it emitted a nudge."""
    payload = {"tool_name": "Bash", "session_id": sid, "tool_input": {"command": cmd}}
    if transcript_path is not None:
        payload["transcript_path"] = transcript_path
    if agent_id is not None:
        payload["agent_id"] = agent_id
    rc, out = run(payload)
    check("hook exits 0 (%s)" % sid.split("-")[3], rc, 0)
    return bool(out)


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

# --- State key components are sanitized (no directory escape). ----------------
_sdir = internal("_state_dir")
_root = internal("STATE_ROOT")
check("a traversal in session/agent ids cannot escape the state root",
      os.path.dirname(_sdir({"session_id": "../../escape", "agent_id": "../x"}))
      if (_sdir and _root) else None, _root)

# A well-formed transcript, used below only to prove that a REALISTIC payload shape
# still behaves — the hook no longer reads the file, so these cases pin that the extra
# fields cannot change the outcome or crash it.
clean = transcript("clean", BENIGN_RECORDS)

for i, bad in enumerate((123, [], {"a": 1}, "", "/not/a/jsonl", "/etc")):
    # A wrong-typed transcript_path must never crash and never change the verdict.
    sid = session("badtype%d" % i)
    check("bad transcript_path %r -> nudge unchanged" % (bad,),
          fire(sid, "grep -r TODO src/", bad), True)

# 🔴 agent_id IS still read — it scopes the state directory — so a wrong-typed or
# traversal value must neither crash nor escape the state root (asserted above).
for i, bad_agent in enumerate((123, [], "../../etc", "a/b", "")):
    sid = session("badagent%d" % i)
    payload = {"tool_name": "Bash", "session_id": sid, "agent_id": bad_agent,
               "transcript_path": clean, "tool_input": {"command": "grep -r TODO src/"}}
    rc, out = run(payload)
    check("bad agent_id %r -> nudge unchanged" % (bad_agent,), (rc, bool(out)), (0, True))

# --------------------------------------------------------------------------- #
# 🔴 ISOLATION POSITIVE CONTROL — counted BEFORE the cleanup below wipes it.
#
# "the real home was untouched" is the same reassuring ZERO a suite that stopped
# exercising the hook entirely would produce. So the pair is reported: N files
# written under the throwaway HOME, 0 changes to the inherited one. The external
# half lives in scripts/tests/test_hook_suites_do_not_touch_the_inherited_home.py
# and PARSES the number printed at the bottom of this file, so a suite that
# silently stopped writing state cannot pass it either.
# --------------------------------------------------------------------------- #
TEMP_HOME_FILES = sum(len(fs) for _, _, fs in os.walk(TEST_HOME))
check("isolation positive control: the suite wrote state under the temp HOME",
      TEMP_HOME_FILES > 0, True)

clear_test_state()
leaked = sorted(os.path.basename(p) for pref in TEST_SID_PREFIXES
                for p in glob.glob(os.path.join(STATE_ROOT, pref + "*")))
# 🔴 IDEMPOTENCE GUARD. State left behind by one run makes the NEXT run fail — and during
# a mutation sweep that reads as "every mutant killed", including mutants that change
# nothing. That is exactly what happened while writing this file: a 31/31 perfect score
# from a harness that was simply dirty. Cheap to assert, so assert it.
check("no test state leaks into the next run", leaked, [])
shutil.rmtree(TMP, ignore_errors=True)

# Floor: a suite that silently shrinks is a vacuous green.
# 🔴 LOWERED 160 -> 130 on 2026-08-25, deliberately and once. The availability-suppression
# suite (~42 checks: transcript scanning, scan-cap boundaries, subagent transcript
# resolution, scan order) was removed because the hook code it asserted against was
# removed — see the hook's header. Those checks did not become flaky or get skipped, they
# stopped having a subject. The run measures 134, so 130 leaves the same ~4-check margin
# the old value carried; it is NOT set to the measured number, so a real shrink still
# trips it.
MIN_CHECKS = 130
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
      f"{len(SESSIONS)} state/concurrency scenarios)")
# Machine-readable, and PARSED by the external isolation guard — keep the wording.
# This half is only the number this file measured itself; the other half of the
# pair (writes to the INHERITED home, which must be 0) is measured from outside,
# because a suite cannot be its own witness for a home it no longer looks at.
print(f"isolation: {TEMP_HOME_FILES} files under the temp HOME")
