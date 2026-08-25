#!/usr/bin/env python3
"""Tests for register-nudge-hook.py — the per-host settings.json registrant.

Runs the script against a temp HOME whose ~/.claude/settings.json already holds
unrelated hooks (clawgate PermissionRequest/Stop, bash-guard, the PostToolUse
nudges), and asserts BOTH of the registrant's surfaces:

  APPEND (narrow)
  1. It appends claude-notify on the 3 turn-boundary events, append-only.
  2. It NEVER clobbers pre-existing hooks (the clawgate Stop hook survives).
  3. It is idempotent (a 2nd run makes no change, no duplicates).
  4. The final settings.json is valid JSON.
  5. The write is ATOMIC (uses os.replace) and leaves no .settings.*.tmp litter.

  REWRITE (wide, added 2026-08-20)
  6. Every managed hook command — bash-guard INCLUDED, which the append surface
     deliberately does not own — has its INTERPRETER TOKEN replaced with an
     absolute store python, while the entry keeps its position, its `matcher`,
     its `type` and every foreign key it arrived with.
  7. Foreign commands (a `.sh` hook, a python script that is NOT a managed hook,
     a command with a leading env assignment) come back BYTE-IDENTICAL.
  8. The append surface compares against the REWRITTEN strings, so the first
     post-migration run does not double-register everything.
  9. bash-guard is rewritten but never CREATED: a settings.json with no
     bash-guard entry comes back with no bash-guard entry.

  DE-DUP (narrowest, added 2026-08-20 as the follow-up to the rewrite)
 12. A settings.json an OLDER registrant double-registered — the state a
     `home-manager rollback` produces — HEALS to one entry per (scope, script),
     keeping the FIRST occurrence with its position and its foreign keys, while a
     same-script entry under a DIFFERENT matcher ON A MATCHER-SUPPORTING EVENT,
     one carrying ARGUMENTS, one carrying a foreign key and one whose
     registration this script does not own (bash-guard) all SURVIVE.

  THE VALIDATED SEAM AND THE WARNINGS (same follow-up)
 13. A hostile $DEVRC_HOOK_PYTHON — argument-carrying, relative, non-python,
     missing, a directory, or a real python file with no execute bit — is rejected
     with the warning THAT condition owns, and the resolved fallback is used, so
     three consecutive runs hold the same number of entries instead of growing
     without bound.
 14. Every command that NAMES a managed hook but is not in the pinnable form is
     reported on stderr, and nothing else is.

  PASS ORDERING AND THE NON-MATCHER EVENTS (2026-08-20, this round)
 15. De-dup runs BEFORE the interpreter rewrite, so a run that removes one entry
     and pins another names each in exactly ONE of the two report blocks.
 16. `matcher` is part of the de-dup identity only on an event that HAS matchers.
     On `Stop` / `UserPromptSubmit` — which always fire on every occurrence — two
     entries for one script are a double-fire whatever their matchers say, so they
     heal; and a `matcher` key that SURVIVES on such an event is named on stderr as
     the configuration error it is.

  THE UNKNOWN-EVENT DEFAULT AND THE REPORT LINES (2026-08-20, this round)
 17. An event in NEITHER ledger is treated as matcher-supporting, so two entries
     for one script under different matchers there BOTH survive and draw no
     warning — the 🔴 claim the registrant's own comment makes, which nothing
     measured. Same run: the double-fire ledger is scoped by matcher (so a
     cross-matcher pair on a matcher-supporting event is silent, which is what
     the de-dup docstring now says), two removals differing ONLY by matcher print
     distinguishable lines, and the removal header stops promising a pinning
     block when nothing was pinned.

🔴 THE 127 WINDOW, which surface 6 exists to close: `home-manager switch`
updates ~/.nix-profile as remove-then-install, so there is a ~1s window in which
the live profile generation is a partial closure with NO python3 on it. A hook
registered as a bare `python3 …` that fires in that window dies with
`python3: command not found`, and a PreToolUse hook exiting 127 is classified
NON-BLOCKING — i.e. bash-guard fails OPEN.

$DEVRC_HOOK_PYTHON pins the interpreter to a deterministic literal here rather
than letting the expectation depend on whatever interpreter runs this file. The
UNPINNED resolution (`os.path.realpath(sys.executable)`) is covered separately,
behaviourally, in test_registrar_activation.py.

Run: python3 test_register_nudge_hook.py
"""
import os, sys, json, shutil, tempfile, subprocess, atexit

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "register-nudge-hook.py")

# A synthetic store path — shaped like the real one, deliberately not any path
# that exists on this host, so nothing can pass by accident of the environment.
#
# 🔴 It is a REAL, executable file. $DEVRC_HOOK_PYTHON is a test seam with
# PRODUCTION REACH (activation inherits the operator's environment), so the
# registrant validates it — absolute, exists, executable, one `python*` token —
# and falls back to its own sys.executable with a warning when it is not. Scenario
# 13 below drives that validation with four hostile values.
_FAKE_STORE = tempfile.mkdtemp(prefix="devrc-fake-store-")
atexit.register(shutil.rmtree, _FAKE_STORE, True)


def fake_python(store_name, basename="python3.12"):
    """A stand-in interpreter path: real file, executable bit set, never RUN.

    The registrant only stat()s it (`isfile` + `access(X_OK)`) — nothing here
    ever execs it — so it deliberately carries NO shebang. Adding one would
    make it a runtime-written executable stub, which `scripts/tests/
    test_runtime_shebangs.py` gates repo-wide.
    """
    p = os.path.join(_FAKE_STORE, store_name, "bin", basename)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write("stand-in for an interpreter binary: stat()ed, never executed\n")
    os.chmod(p, 0o755)
    return p


PY = fake_python("0000000000000000000000000000000-python3-3.12.14")

NOTIFY = PY + " ~/.claude/hooks/claude-notify.py"
CLAWGATE_STOP = "/home/zach/.claude/clawgate-stop-hook.sh"
SEARCH_NUDGE = PY + " ~/.claude/hooks/search-tool-nudge.py"
NEXT_STEP = PY + " ~/.claude/hooks/next-step-nudge.py"
TMUX_STOP = "~/.config/tmux/task-hook.sh"
# 🔴 THE ONE MANAGED COMMAND WITH NO PYTHON IN IT — see scenario 18. It is spelled
# with a bare `bash` on purpose, and several assertions below that used to read
# "every command names the pinned interpreter" now have to say "every PYTHON
# command", because this one legitimately does not and must never be made to.
# Excluding it is not a weakening: scenario 18 asserts the exclusion positively
# (the interpreter stays `bash`), so a mutant that dropped the shell hook from the
# tables would go red there rather than quietly satisfying a widened `all()` here.
BCS = "bash ~/.claude/hooks/base-clone-staleness.sh"
LEDGER = PY + " ~/.claude/hooks/agent-ledger-hook.py"
WRITEBACK = PY + " ~/.claude/hooks/clawgate-writeback-guard.py"
INTERVIEW = PY + " ~/.claude/hooks/clawgate-task-interview-guard.py"
BASH_GUARD = PY + " ~/.claude/hooks/bash-guard.py"
# The backgrounded-command capture log (ClickUp 868ktvqf9) — the SECOND entry
# PRE_BASH_CMDS owns, and the reason several whole-list equalities below moved.
# It is instrumentation: no permissionDecision, no stdout, exit 0 always, so it
# widens what PreToolUse OBSERVES and not what it can refuse.
BG_CAPTURE = PY + " ~/.claude/hooks/bg-command-capture.py"

# The pre-migration spellings, i.e. what is on both hosts right now.
OLD_BASH_GUARD = "python3 ~/.claude/hooks/bash-guard.py"
OLD_AUDIT_NUDGE = "python3 ~/.claude/hooks/audit-pr-nudge.py"
OLD_SHELL_NUDGE = "python3 ~/.claude/hooks/shell-env-nudge.py"

# Commands the registrant must never touch. The last one is the trap: it names
# python AND a .py file, but the path is not under the hooks dir.
FOREIGN_ELSEWHERE = "python3 ~/elsewhere/thing.py"
FOREIGN_ENV_PREFIX = "CLAUDE_HOST=wb /home/zach/.claude/clawgate-hook.sh"
FOREIGN_UNMANAGED_HOOK = "python3 ~/.claude/hooks/not-a-devrc-hook.py"

failures = []


def check(name, cond):
    print(("PASS" if cond else "FAIL") + " - " + name)
    if not cond:
        failures.append(name)


def base_settings(home):
    """The fixture. `home` is needed because one command exercises the LITERAL
    expanded home prefix, which only the registrant's own $HOME can produce."""
    return {
        "hooks": {
            "PermissionRequest": [
                {"hooks": [{"type": "command", "command": FOREIGN_ENV_PREFIX, "timeout": 180}]}
            ],
            # 🔴 bash-guard sits SECOND, behind a foreign entry, and carries two
            # keys the registrant has no business knowing about. Position,
            # matcher and both foreign keys must survive the interpreter rewrite.
            "PreToolUse": [
                {"matcher": "Read", "hooks": [{"type": "command", "command": FOREIGN_ELSEWHERE}]},
                {
                    "matcher": "Bash",
                    "description": "the RULES.md enforcement guard",
                    "hooks": [
                        {"type": "command", "command": OLD_BASH_GUARD, "timeout": 12}
                    ],
                },
                {"matcher": "Bash", "hooks": [{"type": "command", "command": FOREIGN_UNMANAGED_HOOK}]},
            ],
            # 🔴 All THREE accepted spellings of the hooks dir are here, so the
            # prefix set is exercised and not merely declared — and two of them
            # are hooks the APPEND surface also owns, which is what proves the
            # append keys on the SCRIPT rather than on the exact string.
            # search-tool-nudge is deliberately ABSENT: it is the one that must
            # still be appended, so the fixture cannot pass by preserving only.
            "PostToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": OLD_AUDIT_NUDGE}]},
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 $HOME/.claude/hooks/shell-env-nudge.py"}]},
                {"hooks": [{"type": "command", "command": "python3 " + home + "/.claude/hooks/clawgate-writeback-guard.py"}]},
            ],
            # 🔴 Stop already has TWO foreign owners before this script ever runs: the
            # fuzzyclaw tmux writer and the clawgate stop hook (remote approval). Both are
            # in the fixture so "append-only" is tested against a populated event, not an
            # empty one — an append-only bug is invisible on an empty array.
            "Stop": [
                {"hooks": [{"type": "command", "command": TMUX_STOP}]},
                {"hooks": [{"type": "command", "command": CLAWGATE_STOP}]},
            ],
        }
    }


def run(env, cwd=None):
    """`cwd` matters for exactly one case: a RELATIVE $DEVRC_HOOK_PYTHON that
    resolves to a real executable from the directory the registrant is run in."""
    return subprocess.run([sys.executable, SCRIPT], env=env, cwd=cwd,
                          capture_output=True, text=True)


def cmds(data, event):
    return [h.get("command") for e in data.get("hooks", {}).get(event, []) for h in e.get("hooks", [])]


with tempfile.TemporaryDirectory() as tmp:
    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, ".claude"))
    settings = os.path.join(home, ".claude", "settings.json")
    with open(settings, "w") as f:
        json.dump(base_settings(home), f, indent=2)

    env = dict(os.environ)
    env["HOME"] = home
    env["DEVRC_HOOK_PYTHON"] = PY

    p1 = run(env)
    check("1: first run exit 0", p1.returncode == 0)

    with open(settings) as f:
        d = json.load(f)
    check("1: valid JSON after write", isinstance(d, dict))
    for ev in ("UserPromptSubmit", "Stop", "SubagentStop"):
        check("1: claude-notify registered on " + ev, NOTIFY in cmds(d, ev))
    check("2: pre-existing clawgate Stop hook preserved", CLAWGATE_STOP in cmds(d, "Stop"))
    check("2: pre-existing tmux Stop hook preserved", TMUX_STOP in cmds(d, "Stop"))

    # 🔴 EVERY Stop owner coexists — the two FOREIGN ones this script must never
    # disturb plus the four it appends. Asserted as a SET EQUALITY, not membership
    # checks: equality fails when the set GROWS (a hook registered by accident) as
    # well as when it SHRINKS (one clobbered), which membership checks cannot see.
    check("2: exactly the six expected Stop hooks, none clobbered, none extra",
          set(cmds(d, "Stop")) == {TMUX_STOP, CLAWGATE_STOP, NOTIFY, NEXT_STEP,
                                   LEDGER, WRITEBACK})
    check("1: next-step-nudge registered on Stop", NEXT_STEP in cmds(d, "Stop"))
    # Ordering: the two foreign hooks keep their original relative order and stay ahead
    # of everything this script appends. Appending (rather than inserting) is what makes
    # the nudge run last, after the notifiers have already observed the turn. (It no
    # longer BLOCKS — it returns additionalContext and exits 0 — but the turn still
    # continues, so the ordering argument is unchanged.)
    stop = cmds(d, "Stop")

    def idx(seq, value):
        """Position, or None. A missing command must report as a FAILED CHECK,
        not as a ValueError that aborts the whole suite — this file is run
        against pre-change code to prove it goes red, and a traceback there
        hides every check after it."""
        return seq.index(value) if value in seq else None

    order = [idx(stop, c) for c in (TMUX_STOP, CLAWGATE_STOP, NOTIFY, NEXT_STEP)]
    check("2: foreign Stop hooks keep their original relative order",
          None not in order[:2] and order[0] < order[1])
    check("2: appended hooks come after the pre-existing ones",
          None not in order and max(order[:2]) < min(order[2:]))
    # next-step-nudge is Stop-only: it must NOT have been added to SubagentStop.
    check("1: next-step-nudge NOT registered on SubagentStop",
          NEXT_STEP not in cmds(d, "SubagentStop"))

    # --- 6. THE INTERPRETER REWRITE, on the entry that matters most ------------
    # 🔴 bash-guard is a PreToolUse hook and exit 127 is NON-blocking, so a bare
    # `python3` here means the guard FAILS OPEN for the ~1s of every switch in
    # which the intermediate profile generation has no python3 on it.
    pre = cmds(d, "PreToolUse")
    check("6: bash-guard's interpreter is now an absolute store path",
          BASH_GUARD in pre)
    check("6: the bare-python3 bash-guard command is GONE",
          OLD_BASH_GUARD not in pre)
    # ...and the rewrite touched ONLY the interpreter token: position in the
    # array, matcher, type, and BOTH keys this script knows nothing about.
    pre_entries = d["hooks"]["PreToolUse"]
    guard_entry = pre_entries[1]
    check("6: the rewritten entry kept its POSITION in the array",
          guard_entry["hooks"][0]["command"] == BASH_GUARD)
    check("6: the rewritten entry kept its matcher",
          guard_entry.get("matcher") == "Bash")
    check("6: the rewritten entry kept a foreign ENTRY-level key",
          guard_entry.get("description") == "the RULES.md enforcement guard")
    check("6: the rewritten entry kept a foreign HOOK-level key",
          guard_entry["hooks"][0].get("timeout") == 12)
    check("6: the rewritten entry kept its type",
          guard_entry["hooks"][0].get("type") == "command")
    # 🔴 The REWRITE pass still never appends: the fixture's three entries come
    # back as three, and the ONE extra is the interview guard the APPEND table
    # owns — asserted by identity, not by a count, so "gained an entry" cannot be
    # confused with "gained the right entry". Before PRE_BASH_CMDS existed this
    # read `len(pre_entries) == 3`; PreToolUse was rewrite-only then.
    check("6: PreToolUse gained exactly TWO entries, and they are the two the "
          "append table owns — the interview guard and the capture log",
          len(pre_entries) == 5
          and pre_entries[3]["hooks"][0]["command"] == INTERVIEW
          and pre_entries[3].get("matcher") == "Bash"
          and pre_entries[4]["hooks"][0]["command"] == BG_CAPTURE
          and pre_entries[4].get("matcher") == "Bash")
    check("6: the three pre-existing PreToolUse entries kept their positions",
          [e["hooks"][0]["command"] for e in pre_entries[:3]]
          == [FOREIGN_ELSEWHERE, BASH_GUARD, FOREIGN_UNMANAGED_HOOK])

    # --- 7. FOREIGN COMMANDS COME BACK BYTE-IDENTICAL -------------------------
    check("7: a python command outside the hooks dir is untouched",
          FOREIGN_ELSEWHERE in pre)
    check("7: a python command naming a NON-managed hook script is untouched",
          FOREIGN_UNMANAGED_HOOK in pre)
    check("7: a command with a leading env assignment is untouched",
          cmds(d, "PermissionRequest") == [FOREIGN_ENV_PREFIX])
    check("7: the foreign PermissionRequest hook kept its timeout",
          d["hooks"]["PermissionRequest"][0]["hooks"][0].get("timeout") == 180)
    check("7: the two foreign .sh Stop hooks are untouched",
          TMUX_STOP in stop and CLAWGATE_STOP in stop)
    check("7: PreToolUse is the three commands it started with (one rewritten) "
          "plus the two the append table owns",
          pre == [FOREIGN_ELSEWHERE, BASH_GUARD, FOREIGN_UNMANAGED_HOOK,
                  INTERVIEW, BG_CAPTURE])
    check("7: the interview guard is registered exactly once",
          pre.count(INTERVIEW) == 1)
    check("7: the capture log is registered exactly once",
          pre.count(BG_CAPTURE) == 1)

    check("2: both PostToolUse nudges preserved",
          any(c.endswith("/audit-pr-nudge.py") for c in cmds(d, "PostToolUse"))
          and any(c.endswith("/shell-env-nudge.py") for c in cmds(d, "PostToolUse")))

    # --- 8. THE FIRST POST-MIGRATION RUN DOES NOT DOUBLE-REGISTER --------------
    # 🔴 The single most likely way to ship this fix broken: rewrite the commands
    # but leave the append step comparing against the OLD strings, and every hook
    # is registered a second time on the first post-migration run. All three
    # accepted hooks-dir spellings are in the fixture, and two of them name hooks
    # the append surface also owns — so a string-keyed append duplicates them.
    # Asserted as a SET EQUALITY plus a LENGTH over the whole event: equality
    # alone cannot see a duplicate.
    post = cmds(d, "PostToolUse")
    WB_EXPANDED = PY + " " + home + "/.claude/hooks/clawgate-writeback-guard.py"
    SHELL_HOMEVAR = PY + " $HOME/.claude/hooks/shell-env-nudge.py"
    check("8: PostToolUse holds exactly the three migrated hooks plus the two appended ones",
          set(post) == {PY + " ~/.claude/hooks/audit-pr-nudge.py",
                        SHELL_HOMEVAR, WB_EXPANDED, SEARCH_NUDGE, LEDGER,
                        BG_CAPTURE})
    check("8: no hook was double-registered", len(post) == 6)
    check("8: the $HOME/ spelling was rewritten in place, not re-added",
          post.count(SHELL_HOMEVAR) == 1
          and PY + " ~/.claude/hooks/shell-env-nudge.py" not in post)
    check("8: the expanded-home spelling was rewritten in place, not re-added",
          post.count(WB_EXPANDED) == 1 and WRITEBACK not in post)
    # ...and the hook that was genuinely ABSENT was appended, so the three checks
    # above are about de-duplication and not about a pass that appended nothing.
    check("8: the absent nudge WAS appended", SEARCH_NUDGE in post)

    # --- the agent activity ledger writer: FOUR events, one of them brand new --
    # 🔴 SessionStart is absent from the fixture entirely, so this also proves
    # the registrant CREATES an event array rather than only appending to one
    # that already exists.
    for ev in ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop"):
        check("1: agent-ledger registered on " + ev, LEDGER in cmds(d, ev))
    # ...and NOT on SubagentStop: a subagent's tool calls are not the operator
    # window's activity, and writing on them would keep a finished window
    # looking alive.
    check("1: agent-ledger NOT registered on SubagentStop",
          LEDGER not in cmds(d, "SubagentStop"))
    # 🔴 UNMATCHERED on PostToolUse, unlike the three Bash nudges. Scoping it to
    # Bash would stop the heartbeat during a long stretch of Read/Edit work, and
    # `classify_status` lets `stale` win over `busy` — so the window would render
    # stale while demonstrably working. Asserted on the ENTRY, because the
    # command string alone cannot show which matcher it was filed under.
    ledger_entries = [e for e in d["hooks"]["PostToolUse"]
                      if any(h.get("command") == LEDGER
                             for h in e.get("hooks", []))]
    check("1: agent-ledger PostToolUse entry carries NO matcher",
          len(ledger_entries) == 1 and "matcher" not in ledger_entries[0])
    # ...and the pre-existing Bash-scoped nudges kept THEIR matcher — proving
    # the assertion above is about this entry and not about a matcher-stripping
    # bug that would pass it vacuously.
    bash_entries = [e for e in d["hooks"]["PostToolUse"]
                    if any(h.get("command", "").endswith("/search-tool-nudge.py")
                           for h in e.get("hooks", []))]
    check("1: the Bash nudges keep their matcher",
          len(bash_entries) == 1 and bash_entries[0].get("matcher") == "Bash")

    # --- the clawgate write-back guard: PostToolUse + Stop ---------------------
    # By SUFFIX on PostToolUse: the fixture spells that one with the expanded
    # home path, and the point of this check is the EVENT, not the spelling.
    for ev in ("PostToolUse", "Stop"):
        check("1: clawgate-writeback-guard registered on " + ev,
              any(c.endswith("/clawgate-writeback-guard.py") for c in cmds(d, ev)))
    # Stop-and-PostToolUse only. It must NOT reach SubagentStop: a subagent's turn
    # never reaches the operator, so it owes them no write-back — and the hook
    # refuses that event itself as well, belt and braces, because registration is
    # per-host mutable state.
    check("1: clawgate-writeback-guard NOT registered on SubagentStop",
          not any(c.endswith("/clawgate-writeback-guard.py") for c in cmds(d, "SubagentStop")))
    check("1: clawgate-writeback-guard NOT registered on SessionStart",
          not any(c.endswith("/clawgate-writeback-guard.py") for c in cmds(d, "SessionStart")))
    # (The registrant's own PostToolUse entry for this guard is unmatchered; that
    # is asserted in scenario 9 below, where the registrant creates it rather
    # than finding it already in the fixture.)

    # Idempotency: a second run changes nothing and never duplicates.
    before_second = open(settings, "rb").read()
    p2 = run(env)
    check("3: second run exit 0", p2.returncode == 0)
    check("3: second run reports no change", "no change" in p2.stdout)
    # --- 4/d. ALREADY-MIGRATED INPUT IS A NO-OP THAT DOES NOT REWRITE THE FILE -
    # 🔴 settings.json is the operator's per-host file (permissions live in it).
    # The rewrite pass now runs on EVERY switch, so an unconditional write would
    # re-dump it — reordering nothing but rewriting everything — forever.
    check("3: second run left settings.json BYTE-IDENTICAL",
          open(settings, "rb").read() == before_second)
    with open(settings) as f:
        d2 = json.load(f)
    check("3: no duplicate claude-notify on Stop", cmds(d2, "Stop").count(NOTIFY) == 1)
    check("3: no duplicate search-tool-nudge",
          len([c for c in cmds(d2, "PostToolUse") if c.endswith("/search-tool-nudge.py")]) == 1)
    check("3: clawgate Stop still single + present", cmds(d2, "Stop").count(CLAWGATE_STOP) == 1)
    check("3: no duplicate next-step-nudge on Stop", cmds(d2, "Stop").count(NEXT_STEP) == 1)
    check("3: Stop set unchanged by the second run",
          set(cmds(d2, "Stop")) == {TMUX_STOP, CLAWGATE_STOP, NOTIFY, NEXT_STEP,
                                    LEDGER, WRITEBACK})
    check("3: no duplicate clawgate-writeback-guard on Stop",
          cmds(d2, "Stop").count(WRITEBACK) == 1)
    check("3: no duplicate clawgate-writeback-guard on PostToolUse",
          len([c for c in cmds(d2, "PostToolUse")
               if c.endswith("/clawgate-writeback-guard.py")]) == 1)
    check("3: no duplicate agent-ledger on Stop", cmds(d2, "Stop").count(LEDGER) == 1)
    check("3: no duplicate agent-ledger on PostToolUse",
          cmds(d2, "PostToolUse").count(LEDGER) == 1)
    check("3: bash-guard still single after the second run",
          cmds(d2, "PreToolUse").count(BASH_GUARD) == 1)
    check("3: no duplicate interview guard on PreToolUse",
          cmds(d2, "PreToolUse").count(INTERVIEW) == 1)

    # Atomicity: no temp litter left in the dir, and the source uses os.replace.
    leftovers = [n for n in os.listdir(os.path.dirname(settings)) if n.startswith(".settings.")]
    check("5: no leftover temp files after atomic write", leftovers == [])

# --- 9. bash-guard IS REWRITTEN, NEVER CREATED -------------------------------
# 🔴 The two surfaces have different widths and this is where they are told
# apart. This script owns bash-guard's INTERPRETER; it does not own its
# REGISTRATION. A host that has deliberately no bash-guard entry must come back
# with none — otherwise the rewrite surface has quietly become an append surface.
with tempfile.TemporaryDirectory() as tmp:
    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, ".claude"))
    settings = os.path.join(home, ".claude", "settings.json")
    with open(settings, "w") as f:
        json.dump({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": TMUX_STOP}]}]}}, f, indent=2)

    env = dict(os.environ)
    env["HOME"] = home
    env["DEVRC_HOOK_PYTHON"] = PY

    p3 = run(env)
    check("9: run on a bash-guard-less settings.json exits 0", p3.returncode == 0)
    with open(settings) as f:
        d3 = json.load(f)
    all_cmds = [c for ev in d3.get("hooks", {}) for c in cmds(d3, ev)]
    check("9: bash-guard was NOT registered where it was absent",
          not any("bash-guard.py" in (c or "") for c in all_cmds))
    # Anti-vacuity: the run DID do its normal work on that same file, so the
    # check above is about bash-guard and not about a run that did nothing.
    check("9: ...while the hooks it DOES own were registered",
          NEXT_STEP in cmds(d3, "Stop") and LEDGER in cmds(d3, "SessionStart"))
    # 🔴 THE SHAPE THE REGISTRANT WRITES, asserted where it actually writes it.
    # Two PostToolUse entries carry NO matcher — the ledger (scoping it to Bash
    # would stop the heartbeat through a long Read/Edit stretch, and
    # `classify_status` lets `stale` beat `busy`) and the write-back guard (half
    # of what it watches for IS an Edit/Write/NotebookEdit). The three nudges are
    # about the shape of a Bash command, so they keep `matcher: "Bash"`. Asserted
    # on the ENTRY: a command string cannot show which matcher it was filed under.
    def matchers_for(data, event, suffix):
        return [e.get("matcher", None) for e in data["hooks"][event]
                if any((h.get("command") or "").endswith(suffix)
                       for h in e.get("hooks", []))]

    check("9: the ledger's PostToolUse entry carries NO matcher",
          matchers_for(d3, "PostToolUse", "/agent-ledger-hook.py") == [None])
    check("9: the write-back guard's PostToolUse entry carries NO matcher",
          matchers_for(d3, "PostToolUse", "/clawgate-writeback-guard.py") == [None])
    check("9: the three nudges are filed under matcher Bash",
          matchers_for(d3, "PostToolUse", "/audit-pr-nudge.py") == ["Bash"]
          and matchers_for(d3, "PostToolUse", "/shell-env-nudge.py") == ["Bash"]
          and matchers_for(d3, "PostToolUse", "/search-tool-nudge.py") == ["Bash"])
    # Every command this run wrote carries the pinned absolute interpreter — no
    # bare `python3` survives anywhere in the file it produced.
    check("9: every managed PYTHON command it wrote names the absolute interpreter",
          all(c.startswith(PY + " ")
              for c in all_cmds if c not in (TMUX_STOP, BCS)))
    # Anti-vacuity for the exclusion just added: the shell hook really is in
    # this file, so the filter is removing something rather than describing an
    # empty case, and it is NOT carrying a python interpreter.
    check("9: ...and the one non-python managed command is present and bash-run",
          all_cmds.count(BCS) == 1)

# --- 10. THE RECOGNISER IS CONSERVATIVE -------------------------------------
# 🔴 Each command below names a MANAGED hook script under the hooks dir and must
# STILL come back byte-identical, because one of the recogniser's three
# conditions fails. Without these, dropping either the python-interpreter check
# or the start anchor is a mutation no test can see.
#
# ⚠ The assertion below is a PREFIX equality, not a whole-list equality: since the
# interview guard joined the append surface, PreToolUse also gains that one entry.
# The claim being made is about the four UNRECOGNISED commands coming back
# byte-identical AND in place, which the prefix pins exactly.
NOT_A_PYTHON_INTERP = "bash ~/.claude/hooks/bash-guard.py"
NOT_AT_THE_START = "CLAUDE_HOST=wb python3 ~/.claude/hooks/bash-guard.py"
QUOTED_PATH = "python3 '~/.claude/hooks/bash-guard.py'"
NOT_UNDER_THE_HOOKS_DIR = "python3 ~/.claude/other/bash-guard.py"
UNRECOGNISED = [NOT_A_PYTHON_INTERP, NOT_AT_THE_START, QUOTED_PATH,
                NOT_UNDER_THE_HOOKS_DIR]

with tempfile.TemporaryDirectory() as tmp:
    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, ".claude"))
    settings = os.path.join(home, ".claude", "settings.json")
    with open(settings, "w") as f:
        json.dump({"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": c}]}
            for c in UNRECOGNISED
        ]}}, f, indent=2)

    env = dict(os.environ)
    env["HOME"] = home
    env["DEVRC_HOOK_PYTHON"] = PY

    p4 = run(env)
    check("10: run exits 0", p4.returncode == 0)
    with open(settings) as f:
        d4 = json.load(f)
    check("10: every unrecognised command came back BYTE-IDENTICAL, in place",
          cmds(d4, "PreToolUse")[:len(UNRECOGNISED)] == UNRECOGNISED)
    check("10: ...and the ONLY things added to PreToolUse are the two the append "
          "table owns",
          cmds(d4, "PreToolUse") == UNRECOGNISED + [INTERVIEW, BG_CAPTURE])
    # Anti-vacuity: this run DID rewrite/append elsewhere in the same file, so
    # the check above is about the recogniser and not about a run that no-oped.
    check("10: ...while the run did its normal work on the same file",
          NEXT_STEP in cmds(d4, "Stop"))

# --- 11. A PYTHON BUMP: REWRITES WITH NO APPENDS ----------------------------
# 🔴 FOUND BY A SURVIVING MUTANT, not by review. Weakening the write condition
# from `not added and not rewritten` to `not added` survived every check above,
# because in all of them the first run appends something. But the steady state
# after this migration is the OPPOSITE: every hook is already registered, and
# the ONLY thing that changes is the interpreter — on every nixpkgs python bump,
# forever. Under that mutant the run prints "no change" and the stale store path
# stays in settings.json, which is the 127 window reopening the day that path is
# garbage-collected.
PY2 = fake_python("1111111111111111111111111111111-python3-3.12.15")

with tempfile.TemporaryDirectory() as tmp:
    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, ".claude"))
    settings = os.path.join(home, ".claude", "settings.json")
    with open(settings, "w") as f:
        json.dump({"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": OLD_BASH_GUARD}]}
        ]}}, f, indent=2)

    env = dict(os.environ)
    env["HOME"] = home
    env["DEVRC_HOOK_PYTHON"] = PY
    check("11: setup run exits 0", run(env).returncode == 0)
    # Everything is registered now, so a re-run at the SAME python is a no-op...
    p5 = run(env)
    check("11: re-run at the same python reports no change", "no change" in p5.stdout)

    # ...and a re-run at a BUMPED python must rewrite every command, with nothing
    # left to append.
    env["DEVRC_HOOK_PYTHON"] = PY2
    p6 = run(env)
    check("11: the bumped run exits 0", p6.returncode == 0)
    check("11: the bumped run did NOT report 'no change'",
          "no change" not in p6.stdout)
    check("11: the bumped run says it re-pinned the interpreter",
          PY2 in p6.stdout)
    check("11: the bumped run registered NOTHING — rewrites only",
          "registered hooks:" not in p6.stdout)
    with open(settings) as f:
        d5 = json.load(f)
    every = [c for ev in d5.get("hooks", {}) for c in cmds(d5, ev)]
    check("11: every PYTHON command now names the BUMPED interpreter",
          every and all(c.startswith(PY2 + " ") for c in every if c != BCS))
    # 🔴 The shell hook must NOT have been bumped -- an interpreter bump that
    # reached it would have rewritten `bash` to a python path and broken every
    # session start. Asserted here because this is the scenario that bumps.
    check("11: the shell hook was NOT bumped -- it still runs under bash",
          every.count(BCS) == 1)
    check("11: not one command still names the old interpreter",
          not any(c.startswith(PY + " ") for c in every))
    # Anti-vacuity: the file still holds the hooks it held before, in the same
    # number — a rewrite that emptied the file would satisfy an `all()` too.
    check("11: bash-guard is still registered exactly once",
          cmds(d5, "PreToolUse").count(PY2 + " ~/.claude/hooks/bash-guard.py") == 1)

# --- 12. A HOME-MANAGER ROLLBACK DOUBLE-REGISTERED EVERYTHING — IT MUST HEAL --
# 🔴 `home-manager rollback` / `--switch-generation` (and a hand-run from an older
# checkout) runs the PREVIOUS registrant against a settings.json this one already
# pinned. That code keyed "already registered?" on the exact string `python3 ~/…`,
# does not find it, and appends a SECOND copy of every hook it owns — 14 of them
# once the interview guard joined the append tables, which is exactly what this
# fixture holds in the pre-migration spelling. Re-running the CURRENT registrant then
# normalises both copies to identical strings and the append pass, which keys on
# the SCRIPT, sees the hook as present — so WITHOUT a de-dup pass both survive and
# every managed hook fires twice forever: duplicate ledger rows, double
# notifications, and the write-back guard (the only one that can BLOCK) running
# twice per event.
#
# Both directions are asserted, and the direction turns on the EVENT. On a
# matcher-supporting event (PostToolUse here) a same-script entry under a DIFFERENT
# matcher SURVIVES: it is a real hand-scoped configuration (see `registered_scripts`
# in the registrant), and collapsing it into the broad one would silently widen a
# scope somebody narrowed. On an event with NO matcher support (Stop) the same shape
# is NOT a scope — the event always fires on every occurrence — so it is a genuine
# double-fire and it is removed. Which events those are is an enumerated ledger in
# the registrant, pinned by test_registrar_activation.py.
OLD = "python3 ~/.claude/hooks/"
AUDIT = PY + " ~/.claude/hooks/audit-pr-nudge.py"
SHELL = PY + " ~/.claude/hooks/shell-env-nudge.py"
NOTIFY_ARGS = NOTIFY + " --verbose"


def one(cmd, matcher=None, entry_extra=None, hook_extra=None):
    """One settings.json entry holding one hook — the shape the registrant writes."""
    h = {"type": "command", "command": cmd}
    if hook_extra:
        h.update(hook_extra)
    e = {"hooks": [h]}
    if matcher is not None:
        e["matcher"] = matcher
    if entry_extra:
        e.update(entry_extra)
    return e


def entries(data, event):
    """(matcher, command) per hook, in file order.

    The de-dup identity is the PAIR, and a command string on its own cannot show
    which matcher its entry was filed under — which is the whole distinction
    between a duplicate and a deliberately narrowed registration.
    """
    return [(e.get("matcher"), h.get("command"))
            for e in data.get("hooks", {}).get(event, [])
            for h in e.get("hooks", [])]


DUPLICATED = {"hooks": {
    # 🔴 bash-guard, doubled. This script owns bash-guard's INTERPRETER and not its
    # REGISTRATION, so the second copy is NOT its to delete — it is rewritten and
    # left, and reported on stderr instead. The first copy carries two foreign keys.
    "PreToolUse": [
        one(BASH_GUARD, "Bash", {"description": "the RULES.md enforcement guard"},
            {"timeout": 12}),
        one(OLD + "bash-guard.py", "Bash"),
        # 🔴 …and the interview guard, doubled. This one IS this script's to
        # de-dup — it is in the append tables — so the pair must heal to ONE,
        # right beside a bash-guard pair that must NOT. Two duplicates on one
        # event, opposite verdicts, is what makes the ownership filter's
        # behaviour observable instead of asserted.
        one(INTERVIEW, "Bash"),
        one(OLD + "clawgate-task-interview-guard.py", "Bash"),
    ],
    "PostToolUse": [
        one(AUDIT, "Bash"), one(SHELL, "Bash"), one(SEARCH_NUDGE, "Bash"),
        one(LEDGER),
        # SURVIVOR A: same script, DIFFERENT matcher, on a MATCHER-SUPPORTING
        # event. THAT is where a matcher is a real scope and collapsing two
        # entries into one would widen something somebody narrowed. It used to
        # live on `Stop` and was described there as "a hand-scoped narrow entry",
        # which was simply wrong: Stop has no matcher support and always fires on
        # every occurrence, so that entry was a genuine double-fire, not a scope.
        one(LEDGER, "Bash"),
        one(WRITEBACK),
        one(OLD + "audit-pr-nudge.py", "Bash"),
        one(OLD + "shell-env-nudge.py", "Bash"),
        one(OLD + "search-tool-nudge.py", "Bash"),
        one(OLD + "agent-ledger-hook.py"),
        one(OLD + "clawgate-writeback-guard.py"),
    ],
    "Stop": [
        one(TMUX_STOP), one(CLAWGATE_STOP),
        one(NOTIFY), one(NEXT_STEP),
        # 🔴 NOT a survivor, and it used to be: a `matcher` on `Stop`. Stop has no
        # matcher support and always fires on every occurrence, so this is the
        # SAME registration twice and next-step-nudge fires twice per turn. It is
        # removed, and the surviving copy is the unmatchered FIRST one.
        one(NEXT_STEP, "Bash"),
        one(LEDGER), one(WRITEBACK),
        # SURVIVOR B: same script and matcher, but ARGUMENTS after the script path.
        one(NOTIFY_ARGS),
        # SURVIVOR C: same script and matcher, but a foreign hook-level key.
        one(LEDGER, None, None, {"timeout": 7}),
        # SURVIVOR D: same script and matcher, but a foreign ENTRY-level key — and
        # for a script this registrant DOES own, so the entry-shape check is the
        # only thing that can save it. Without this line, dropping that check is a
        # mutant the suite cannot see: the only other foreign-entry-keyed duplicate
        # in this fixture is bash-guard's, which the ownership filter already
        # protects, so it would die for the wrong guard's reason.
        one(WRITEBACK, None, {"description": "the write-back guard, annotated"}),
        one(OLD + "claude-notify.py"),
        one(OLD + "agent-ledger-hook.py"),
        one(OLD + "clawgate-writeback-guard.py"),
        one(OLD + "next-step-nudge.py"),
    ],
    "SessionStart": [one(LEDGER), one(OLD + "agent-ledger-hook.py")],
    "UserPromptSubmit": [
        one(NOTIFY), one(LEDGER),
        one(OLD + "claude-notify.py"), one(OLD + "agent-ledger-hook.py"),
    ],
    "SubagentStop": [one(NOTIFY), one(OLD + "claude-notify.py")],
}}

with tempfile.TemporaryDirectory() as tmp:
    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, ".claude"))
    settings = os.path.join(home, ".claude", "settings.json")
    with open(settings, "w") as f:
        json.dump(DUPLICATED, f, indent=2)

    env = dict(os.environ)
    env["HOME"] = home
    env["DEVRC_HOOK_PYTHON"] = PY

    p12 = run(env)
    check("12: the healing run exits 0", p12.returncode == 0)
    with open(settings) as f:
        d12 = json.load(f)

    # Literal expected end state per event — not derived from the fixture, so a
    # de-dup that removed the WRONG copy (or reordered) fails here.
    check("12: PostToolUse healed to one entry per (matcher, script), and the "
          "distinct-matcher entry SURVIVED on a matcher-supporting event",
          entries(d12, "PostToolUse") == [
              ("Bash", AUDIT), ("Bash", SHELL), ("Bash", SEARCH_NUDGE),
              (None, LEDGER), ("Bash", LEDGER), (None, WRITEBACK),
              ("Bash", BG_CAPTURE)])
    check("12: Stop healed, keeping the first copy of each and the three survivors",
          entries(d12, "Stop") == [
              (None, TMUX_STOP), (None, CLAWGATE_STOP),
              (None, NOTIFY), (None, NEXT_STEP),
              (None, LEDGER), (None, WRITEBACK),
              (None, NOTIFY_ARGS), (None, LEDGER), (None, WRITEBACK)])
    # 🔴 The matchered Stop copy is GONE, and the unmatchered one is what stayed.
    # Spelled out on its own because the list equality above would also pass if
    # BOTH had been removed, or if the wrong one had survived.
    check("12: the matchered Stop duplicate was removed, keeping the FIRST copy",
          entries(d12, "Stop").count((None, NEXT_STEP)) == 1
          and ("Bash", NEXT_STEP) not in entries(d12, "Stop"))
    check("12: SessionStart healed, and the shell hook appended after it",
          entries(d12, "SessionStart") == [(None, LEDGER), (None, BCS)])
    check("12: UserPromptSubmit healed",
          entries(d12, "UserPromptSubmit") == [(None, NOTIFY), (None, LEDGER)])
    check("12: SubagentStop healed", entries(d12, "SubagentStop") == [(None, NOTIFY)])
    # 🔴 THE OWNERSHIP BOUNDARY: bash-guard's second entry is rewritten, never
    # removed. Its registration belongs to the host.
    check("12: the doubled bash-guard entry is PINNED but NOT deleted",
          entries(d12, "PreToolUse") == [("Bash", BASH_GUARD), ("Bash", BASH_GUARD),
                                         ("Bash", INTERVIEW), ("Bash", BG_CAPTURE)])
    # The first bash-guard entry keeps both keys this script knows nothing about.
    check("12: the surviving bash-guard entry kept its foreign keys",
          d12["hooks"]["PreToolUse"][0].get("description")
          == "the RULES.md enforcement guard"
          and d12["hooks"]["PreToolUse"][0]["hooks"][0].get("timeout") == 12)
    check("12: the survivor with a foreign hook-level key kept it",
          [e for e in d12["hooks"]["Stop"]
           if e["hooks"][0].get("timeout") == 7] != [])
    check("12: the survivor with a foreign ENTRY-level key kept it",
          [e for e in d12["hooks"]["Stop"]
           if e.get("description") == "the write-back guard, annotated"] != [])
    # It says what it removed, and how many. The header is pinned as a WHOLE
    # normalised line, not by keyword: the block prints commands with the
    # spelling they were FOUND with, directly above a "pinned … to /nix/store/…"
    # block, and the clause that says so is the entire point of the line.
    check("12: it reports the removals under a header that says the spellings "
          "are pre-pinning",
          "removed duplicate hook registrations "
          "(shown as they were FOUND — de-dup runs before the pinning below):"
          in p12.stdout.splitlines())
    # 🔴 15, counted off the FIXTURE above and not off a previous value of this
    # literal. It is the number two independent changes each moved by one, from
    # the 13 that predates both: the interview guard's doubled PreToolUse pair
    # heals to one (+1), and the matchered `Stop` copy of next-step-nudge stopped
    # being a survivor (+1). Each side of the merge that produced this tree wrote
    # 14 — correct on its own branch, and wrong here — which is exactly what a
    # count assertion does when two changes to one fixture land together.
    check("12: exactly 15 duplicate registrations were removed",
          len([ln for ln in p12.stdout.splitlines() if ln.startswith("  - ")]) == 15)
    # ...and it is LOUD about the four double-fires it declined to touch. Counted
    # on the stem alone, because the scope clause after it now differs by event:
    # a matcher-supporting event names the matcher, a NO_MATCHER event says why
    # there is none to name.
    check("12: it warns about the duplicates it declined to remove",
          p12.stderr.count("is registered 2 times") == 4)
    check("12: only the MATCHER-SUPPORTING event's warning names a matcher",
          p12.stderr.count("is registered 2 times under matcher") == 1
          and "PreToolUse: bash-guard.py is registered 2 times under matcher "
              "'Bash'." in p12.stderr)
    check("12: the Stop warnings say Stop has no matcher support instead",
          p12.stderr.count("and Stop has no matcher support — every copy fires "
                           "on every occurrence") == 3)
    check("12: the declined warnings name all four scripts",
          all(s in p12.stderr for s in ("bash-guard.py", "claude-notify.py",
                                        "agent-ledger-hook.py",
                                        "clawgate-writeback-guard.py")))
    # 🔴 AND THE HEALED FILE IS A FIXED POINT. A de-dup that re-ran forever, or one
    # that healed into a state the append pass then re-populated, would fail here.
    healed = open(settings, "rb").read()
    p12b = run(env)
    check("12: the second run reports no change", "no change" in p12b.stdout)
    check("12: the healed file is BYTE-IDENTICAL after a second run",
          open(settings, "rb").read() == healed)

# --- 13. A HOSTILE $DEVRC_HOOK_PYTHON CANNOT GROW THE FILE WITHOUT BOUND ------
# 🔴 The override is a test seam with PRODUCTION REACH: home-manager activation
# inherits the operator's environment, so an exported value is written verbatim
# into every hook command. An interpreter carrying an argument, or one whose
# basename is not `python*`, makes `managed_script_of` fail to recognise the
# registrant's OWN output — so the append pass re-appends everything on EVERY run:
# 16 -> 32 -> 48 entries, unbounded and silent. The other two are not growth bugs
# but are just as wrong to write: a relative name reopens the exact PATH window
# this pinning closes, and a missing path turns every hook into a permanent 127.
#
# 🔴 EACH CONDITION IS ISOLATED BY ITS OWN CASE, AND EACH CASE PINS THE MESSAGE
# THAT CONDITION OWNS, because these checks MASK one another and a mutant that
# dies for the neighbour's reason proves nothing about the guard it removed.
# `os.access(X_OK)` is False for a path that does not exist, so it hides the
# exists check unless the value is a real EXECUTABLE DIRECTORY; the exists check
# in turn hides `os.access(X_OK)` unless the value is a real FILE with the
# execute bit off; and a bare relative name is not a file in any plausible cwd,
# so the exists check hides the absolute check unless the value really does
# resolve from the directory the registrant runs in. All four of those cases are
# here for exactly that reason — without them, dropping the matching check is a
# surviving mutant. The `reason` column is what makes each one attributable:
# asserting only that SOMETHING was rejected scores a kill for whichever
# neighbour fired.
NOT_A_PYTHON = fake_python("not-a-python", basename="sh")
_REL_BIN = os.path.dirname(fake_python("relative-store"))
EXEC_DIR = os.path.join(_FAKE_STORE, "a-directory", "bin", "python3.12")
os.makedirs(EXEC_DIR, exist_ok=True)
# A REAL regular file, absolute, with a `python*` basename — every other
# condition passes — whose only defect is the missing execute bit. 0644 has no
# execute bit at all, so `access(X_OK)` is False even for root.
NOT_EXECUTABLE = fake_python("no-execute-bit")
os.chmod(NOT_EXECUTABLE, 0o644)

HOSTILE_OVERRIDES = [
    (PY + " -X utf8", "carries an argument", None,
     "not recognised by this script's own recogniser"),
    ("python3", "relative", None, "it is not an absolute path"),
    ("python3.12", "relative but resolvable from the cwd", _REL_BIN,
     "it is not an absolute path"),
    (NOT_A_PYTHON, "absolute and executable but not a python", None,
     "not recognised by this script's own recogniser"),
    (PY + "-gone", "absolute python path that does not exist", None,
     "no such file"),
    (EXEC_DIR, "an executable DIRECTORY with a python name", None,
     "no such file"),
    (NOT_EXECUTABLE, "an absolute python FILE with no execute bit", None,
     "not executable"),
]
RESOLVED = os.path.realpath(sys.executable)

for hostile, why, hostile_cwd, expected_reason in HOSTILE_OVERRIDES:
    with tempfile.TemporaryDirectory() as tmp:
        home = os.path.join(tmp, "home")
        os.makedirs(os.path.join(home, ".claude"))
        settings = os.path.join(home, ".claude", "settings.json")
        with open(settings, "w") as f:
            json.dump({"hooks": {
                "PreToolUse": [one(OLD + "bash-guard.py", "Bash")],
                "Stop": [one(TMUX_STOP)],
            }}, f, indent=2)

        env = dict(os.environ)
        env["HOME"] = home
        env["DEVRC_HOOK_PYTHON"] = hostile

        runs, counts = [], []
        for _ in range(3):
            runs.append(run(env, cwd=hostile_cwd))
            with open(settings) as f:
                dd = json.load(f)
            counts.append(len([c for ev in dd.get("hooks", {})
                               for c in cmds(dd, ev)]))

        tag = "13 (%s)" % why
        check(tag + ": all three runs exit 0",
              all(r.returncode == 0 for r in runs))
        check(tag + ": the override is rejected OUT LOUD",
              all("DEVRC_HOOK_PYTHON" in r.stderr for r in runs))
        # 🔴 ...for THIS condition's own reason. Without this the whole loop is
        # satisfied by any rejection at all, so deleting the one check this case
        # exists to cover scores as KILLED by whichever neighbour happened to
        # fire — or, when no neighbour fires, the case is the only thing standing
        # between the guard and a silent survival.
        check(tag + ": rejected for the reason THIS condition owns",
              all(expected_reason in r.stderr for r in runs))
        # THE UNBOUNDED-GROWTH LOOP, CLOSED: a literal, not a comparison against
        # run 1 — a run that appended nothing at all would satisfy `a == b == c`.
        # 16 -> 18: the backgrounded-command capture log (868ktvqf9) joined BOTH
        # PRE_BASH_CMDS and POST_BASH_CMDS, so one converged run holds two more
        # commands than it did. The literal stays a literal — `a == b == c` is
        # satisfied by a run that appends nothing at all, which is the unbounded-
        # growth guard's own failure mode inverted.
        # 18 -> 19: base-clone-staleness.sh joined SINGLE_EVENT_CMDS on
        # SessionStart, so one converged run holds one more command.
        check(tag + ": three consecutive runs hold exactly 19 hook commands",
              counts == [19, 19, 19])
        with open(settings) as f:
            d13 = json.load(f)
        written = [c for ev in d13.get("hooks", {}) for c in cmds(d13, ev)
                   if c not in (TMUX_STOP, BCS)]
        check(tag + ": every PYTHON hook command names the resolved fallback instead",
              written and all(c.startswith(RESOLVED + " ") for c in written))
        # The hostile override must not have reached the shell hook either --
        # it has no python token to poison, and it must still be exactly one.
        check(tag + ": the shell hook survived the hostile override untouched",
              [c for ev in d13.get("hooks", {}) for c in cmds(d13, ev)
               if "base-clone-staleness.sh" in c] == [BCS])
        # By PREFIX, not substring: the resolved fallback is itself a path ending
        # in `python3`, so a substring test would report the relative override as
        # "reached" purely because the honest answer contains its spelling.
        check(tag + ": the hostile value is not the interpreter of any command",
              not any(c.startswith(hostile + " ") for c in written))

# --- 14. IT SAYS WHICH MANAGED HOOKS IT DECLINED TO PIN ----------------------
# 🔴 Conservative matching is right; conservative SILENCE is what let the 127
# window run for months. Each of these names a managed hook, comes back
# byte-identical, and stays on a PATH-resolved interpreter — so each one keeps
# dying mid-switch with nothing in the switch log to say so.
UNPINNABLE = [
    "python3 -u ~/.claude/hooks/bash-guard.py",
    "PYTHONPATH=/x python3 ~/.claude/hooks/bash-guard.py",
    "python3 ${HOME}/.claude/hooks/bash-guard.py",
    "python3 '~/.claude/hooks/bash-guard.py'",
    "cd /tmp && python3 ~/.claude/hooks/bash-guard.py",
]
# The other half of the claim: it must NOT warn about a command that names no
# managed hook. Without these the warning could be "print something for every
# unrecognised command" and still pass.
SILENT = ["python3 ~/.claude/hooks/not-a-devrc-hook.py", TMUX_STOP]

with tempfile.TemporaryDirectory() as tmp:
    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, ".claude"))
    settings = os.path.join(home, ".claude", "settings.json")
    with open(settings, "w") as f:
        json.dump({"hooks": {"PreToolUse": [one(c, "Bash")
                                            for c in UNPINNABLE + SILENT]}},
                  f, indent=2)

    env = dict(os.environ)
    env["HOME"] = home
    env["DEVRC_HOOK_PYTHON"] = PY

    p14 = run(env)
    check("14: run exits 0", p14.returncode == 0)
    with open(settings) as f:
        d14 = json.load(f)
    check("14: every unpinnable command came back BYTE-IDENTICAL",
          cmds(d14, "PreToolUse") == UNPINNABLE + SILENT + [INTERVIEW, BG_CAPTURE])
    check("14: exactly one warning per unpinnable command, and no more",
          p14.stderr.count("is not in the form this script can pin") == 5)
    for c in UNPINNABLE:
        check("14: it names the command it declined to pin: " + c,
              repr(c) in p14.stderr)
    for c in SILENT:
        check("14: it stays quiet about a command naming no managed hook: " + c,
              repr(c) not in p14.stderr)
    # Anti-vacuity: the run did its normal work on the same file, so the checks
    # above are about the warning and not about a run that died early.
    check("14: ...while the run did its normal work on the same file",
          NEXT_STEP in cmds(d14, "Stop"))

# --- 15. THE DE-DUP PASS RUNS BEFORE THE REWRITE PASS ------------------------
# 🔴 The registrant claims this ordering in a comment ("so the report cannot claim
# to have re-pinned an entry it is about to delete") and NOTHING measured it:
# swapping the two passes left the whole suite green. It is observable in the
# report, because the two blocks are built from what each pass touched.
#
# The fixture makes one entry removable and a DIFFERENT one pinnable in the same
# run, so the pinned block is non-empty either way — the positive control that
# stops "0 pinned lines" from passing this vacuously.
#
#   de-dup first (correct): the removed entry never reaches the rewrite, so it is
#   named ONLY under "removed"; the pinned block holds exactly bash-guard.
#   rewrite first (the mutant): the duplicate is pinned, reported, and THEN
#   deleted — so claude-notify appears in BOTH blocks and the pinned block holds
#   two lines.
with tempfile.TemporaryDirectory() as tmp:
    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, ".claude"))
    settings = os.path.join(home, ".claude", "settings.json")
    with open(settings, "w") as f:
        json.dump({"hooks": {
            # Pinnable and NOT a duplicate — it must appear under "pinned".
            "PreToolUse": [one(OLD + "bash-guard.py", "Bash")],
            # A duplicate in the pre-migration spelling — it must appear under
            # "removed", and nowhere else.
            "Stop": [one(NOTIFY), one(OLD + "claude-notify.py")],
        }}, f, indent=2)

    env = dict(os.environ)
    env["HOME"] = home
    env["DEVRC_HOOK_PYTHON"] = PY

    p15 = run(env)
    check("15: run exits 0", p15.returncode == 0)
    removed_lines = [ln for ln in p15.stdout.splitlines() if ln.startswith("  - ")]
    pinned_lines = [ln for ln in p15.stdout.splitlines() if ln.startswith("  ~ ")]
    check("15: exactly one duplicate was removed, and it is claude-notify",
          len(removed_lines) == 1 and "claude-notify.py" in removed_lines[0])
    # POSITIVE CONTROL: the pinned block CAN be non-empty in this run.
    check("15: exactly one entry was pinned, and it is bash-guard",
          len(pinned_lines) == 1 and "bash-guard.py" in pinned_lines[0])
    check("15: the entry it deleted is NOT reported as re-pinned",
          not any("claude-notify.py" in ln for ln in pinned_lines))
    check("15: no command is named in BOTH report blocks",
          {ln[4:] for ln in removed_lines}.isdisjoint({ln[4:] for ln in pinned_lines}))
    with open(settings) as f:
        d15 = json.load(f)
    check("15: the surviving claude-notify is the already-pinned one, exactly once",
          cmds(d15, "Stop").count(NOTIFY) == 1
          and OLD + "claude-notify.py" not in cmds(d15, "Stop"))

# --- 16. `matcher` IS A SCOPE ONLY ON AN EVENT THAT HAS MATCHERS -------------
# 🔴 `Stop`, `UserPromptSubmit` and the other NO_MATCHER events always fire on
# every occurrence, so a `matcher` there narrows nothing and two entries for one
# script BOTH run. The de-dup identity used to carry the matcher on every event,
# so those pairs survived and produced NO warning: the hook fired twice forever.
#
# Both halves are asserted here, plus the case that must NOT change: the same
# shape on a MATCHER-SUPPORTING event still survives.
STRAY = {"hooks": {
    "Stop": [
        one(TMUX_STOP),
        # (a) the exact shape the audit found: `matcher: ""` beside an absent one.
        one(NOTIFY), one(NOTIFY, ""),
        # (b) a NAMED matcher on Stop — a double-fire, not a narrowed scope.
        one(NEXT_STEP), one(NEXT_STEP, "Bash"),
        # (c) the matchered copy FIRST, so it is the SURVIVOR: de-dup keeps the
        #     first occurrence, the useless matcher rides along with it, and it
        #     is what the stray-matcher warning has to name.
        one(LEDGER, "Bash"), one(LEDGER),
        # (c2) the SURVIVING copy carries `matcher: ""`, which narrows nothing on
        #      ANY event — so it is a duplicate like the rest, but it must NOT be
        #      reported as a stray matcher: an empty matcher is not an
        #      event-specific mistake. Without this entry, narrowing the
        #      stray-matcher test from `in (None, "")` to `is None` is a mutant
        #      nothing can see.
        one(WRITEBACK, ""), one(WRITEBACK),
    ],
    # (d) THE CONTROL: identical shape on a matcher-supporting event. Here the
    #     matcher is a real scope and both entries must survive — without this
    #     line, "drop the matcher from every event's identity" is a mutant this
    #     scenario cannot see.
    "PostToolUse": [one(LEDGER), one(LEDGER, "Bash")],
    # (e) ONE entry listing the SAME hook twice. It fires twice, so the ledger of
    #     what is still doubled has to COUNT occurrences rather than ask whether
    #     an identity is present — and it is not removable (two hooks in the list
    #     is not a shape this script writes), so the only correct behaviour is to
    #     name it. Counting by set membership scores this entry as one.
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": NOTIFY},
                                    {"type": "command", "command": NOTIFY}]}],
}}

with tempfile.TemporaryDirectory() as tmp:
    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, ".claude"))
    settings = os.path.join(home, ".claude", "settings.json")
    with open(settings, "w") as f:
        json.dump(STRAY, f, indent=2)

    env = dict(os.environ)
    env["HOME"] = home
    env["DEVRC_HOOK_PYTHON"] = PY

    p16 = run(env)
    check("16: run exits 0", p16.returncode == 0)
    with open(settings) as f:
        d16 = json.load(f)
    # Literal end state, not derived from the fixture.
    check("16: Stop healed to one entry per script, keeping the FIRST of each",
          entries(d16, "Stop") == [
              (None, TMUX_STOP), (None, NOTIFY), (None, NEXT_STEP),
              ("Bash", LEDGER), ("", WRITEBACK)])
    check("16: exactly four Stop duplicates were removed",
          len([ln for ln in p16.stdout.splitlines() if ln.startswith("  - ")]) == 4)
    # THE CONTROL: unchanged on a matcher-supporting event.
    check("16: the same shape on PostToolUse kept BOTH entries",
          [i for i in entries(d16, "PostToolUse") if i[1] == LEDGER]
          == [(None, LEDGER), ("Bash", LEDGER)])
    # The surviving stray matcher is named on stderr — once, naming the event,
    # the matcher and the command.
    check("16: the surviving stray matcher is reported exactly once",
          p16.stderr.count("has no matcher support and always fires on every "
                           "occurrence") == 1)
    check("16: the stray-matcher warning names the event, the matcher and the command",
          "Stop: an entry carries matcher 'Bash', but Stop has no matcher support"
          in p16.stderr and repr(LEDGER) in p16.stderr)
    # ...and it does NOT complain about a stray matcher it just deleted, nor
    # about the SURVIVING empty-string one, which narrows nothing on ANY event
    # and so is not a mistake about THIS event.
    check("16: it does not report a stray matcher on an entry it removed, "
          "nor an empty one it kept",
          p16.stderr.count("an entry carries matcher") == 1
          and "an entry carries matcher ''" not in p16.stderr)
    check("16: nothing on Stop is left doubled, so Stop gets no double-fire warning",
          not any(ln.startswith("WARNING Stop:") and "is registered" in ln
                  for ln in p16.stderr.splitlines()))
    # 🔴 ...and the ONE entry that lists the same hook twice IS named. Counting by
    # set membership instead of by occurrence scores it as a single registration
    # and says nothing, while the hook fires twice on every prompt.
    check("16: two hooks inside ONE entry are counted as two registrations",
          p16.stderr.count("is registered 2 times") == 1
          and "UserPromptSubmit: claude-notify.py is registered 2 times, and "
              "UserPromptSubmit has no matcher support" in p16.stderr)
    # A FIXED POINT: the stray matcher survives, is warned about again, and does
    # not make the registrant rewrite the file on every run.
    healed16 = open(settings, "rb").read()
    p16b = run(env)
    check("16: the second run reports no change", "no change" in p16b.stdout)
    check("16: the healed file is BYTE-IDENTICAL after a second run",
          open(settings, "rb").read() == healed16)
    check("16: ...and the second run still names the stray matcher",
          "an entry carries matcher 'Bash'" in p16b.stderr)

# --- 17. THE UNKNOWN-EVENT DEFAULT, AND WHAT THE REPORT SAYS -----------------
# 🔴 THE CONSERVATIVE DEFAULT HAD DELETION POWER AND NO COVERAGE. The registrant
# claims, in a 🔴 comment, that an event in NEITHER ledger is treated as
# matcher-supporting and that this "can only ever make this script DECLINE to
# delete something". Nothing measured it. Two deletion-free one-token mutants
# survived BOTH suites and the full gate:
#
#   M2  `event not in NO_MATCHER_EVENTS`  ->  `event in MATCHER_EVENTS`
#   M8  add one event name to NO_MATCHER_EVENTS
#
# Both flip the default from conservative to DESTRUCTIVE for every event the
# ledgers do not name: the matcher leaves the identity, two genuinely distinct
# registrations collapse to one, and the survivor draws a false "has no matcher
# support" warning. M8 is now caught by the exact-equality pin in
# test_registrar_activation.py; M2 leaves both ledgers untouched and is caught
# HERE, behaviourally.
#
# The event name is deliberately one the hooks documentation has not shipped —
# every documented event is now in a ledger, so nothing real would exercise the
# default. This is the case the comment's claim is actually ABOUT.
UNKNOWN_EVENT = "AnEventTheHooksDocsHaveNotShippedYet"

# 🔴 Every command in this fixture is ALREADY pinned to PY, so the run rewrites
# NOTHING. That is load-bearing twice over: it is the state both real hosts are in
# after one switch, and it is what makes the "nothing needed pinning" report
# header reachable at all.
DEFAULTED = {"hooks": {
    # (a) THE SUBJECT: two entries, one script, DIFFERENT matchers, on an event
    #     nobody classified. Both are exactly the shape this script writes, so
    #     the entry-shape check cannot be what saves them — only the scope in the
    #     identity can. Under M2/M8 the second is deleted.
    UNKNOWN_EVENT: [one(LEDGER, "manual"), one(LEDGER, "auto")],
    # (b) THE SENTENCE THE DOCSTRING NOW MAKES: the same shape on a
    #     matcher-supporting event is not a duplicate AND draws no double-fire
    #     warning — even though both entries really do fire on every Bash call.
    #     The docstring used to promise a warning for "more than once on one
    #     event", which this state falsifies.
    "PostToolUse": [one(LEDGER), one(LEDGER, "Bash")],
    # (c) POSITIVE CONTROL for the whole scenario plus the report lines: three
    #     copies of one script on a NO_MATCHER event, two of them differing ONLY
    #     by `matcher`. A run that removed nothing would pass (a) and (b)
    #     vacuously; this makes the removal count non-zero, and the two NOTIFY
    #     removals print the same command, so their report lines can only differ
    #     if the line names the scope.
    "Stop": [one(NOTIFY), one(NOTIFY, "Bash"), one(NOTIFY, "Edit"),
             one(WRITEBACK), one(WRITEBACK)],
}}

with tempfile.TemporaryDirectory() as tmp:
    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, ".claude"))
    settings = os.path.join(home, ".claude", "settings.json")
    with open(settings, "w") as f:
        json.dump(DEFAULTED, f, indent=2)

    env = dict(os.environ)
    env["HOME"] = home
    env["DEVRC_HOOK_PYTHON"] = PY

    p17 = run(env)
    check("17: run exits 0", p17.returncode == 0)
    with open(settings) as f:
        d17 = json.load(f)

    # (a) THE CONSERVATIVE DEFAULT, MEASURED. Literal end state, in order.
    check("17: an UNCLASSIFIED event keeps BOTH same-script entries under their "
          "different matchers",
          entries(d17, UNKNOWN_EVENT) == [("manual", LEDGER), ("auto", LEDGER)])
    check("17: ...and says nothing about them being doubled",
          not any(ln.startswith("WARNING " + UNKNOWN_EVENT) and "is registered" in ln
                  for ln in p17.stderr.splitlines()))
    check("17: ...and does not call their matchers stray",
          UNKNOWN_EVENT + ": an entry carries matcher" not in p17.stderr)

    # (c) POSITIVE CONTROL: the harness CAN observe a removal in this same run.
    removed17 = [ln for ln in p17.stdout.splitlines() if ln.startswith("  - ")]
    check("17: POSITIVE CONTROL — three Stop duplicates were removed in the very "
          "run that removed neither unclassified entry", len(removed17) == 3)
    check("17: Stop healed to the FIRST copy of each script",
          [i for i in entries(d17, "Stop") if i[1] in (NOTIFY, WRITEBACK)]
          == [(None, NOTIFY), (None, WRITEBACK)])

    # (b) THE DOCSTRING'S SCOPE: no warning, and both entries survive.
    check("17: a cross-matcher pair on a MATCHER-supporting event survives",
          [i for i in entries(d17, "PostToolUse") if i[1] == LEDGER]
          == [(None, LEDGER), ("Bash", LEDGER)])
    check("17: ...and the double-fire ledger is scoped by matcher, so it says "
          "NOTHING about that pair — which is why the docstring says 'under one "
          "scope' and not 'on one event'",
          not any(ln.startswith("WARNING PostToolUse:") and "is registered" in ln
                  for ln in p17.stderr.splitlines()))

    # 🟢 THE REPORT LINES. Two removals name the SAME command and differ only in
    # the matcher their entry carried; without the scope note they are byte-
    # identical and the operator cannot tell that two things went.
    check("17: every removal line is distinct", len(set(removed17)) == 3)
    notify_lines = sorted(ln for ln in removed17 if NOTIFY in ln)
    check("17: the two removals that differ ONLY by matcher print differently, "
          "each naming the matcher it was found under",
          len(notify_lines) == 2
          and "(matcher 'Bash'; Stop has none to narrow)" in notify_lines[0]
          and "(matcher 'Edit'; Stop has none to narrow)" in notify_lines[1])
    check("17: an absent matcher is reported as absent, not as a value",
          [ln for ln in removed17 if WRITEBACK in ln
           and ln.endswith("  (no matcher; Stop has none to narrow)")] != [])

    # 🟢 THE REPORT HEADER. Nothing was pinned in this run — the state a host is
    # in after one switch — so the header must not send the reader looking for a
    # pinning block below it.
    pinned17 = [ln for ln in p17.stdout.splitlines() if ln.startswith("  ~ ")]
    check("17: POSITIVE CONTROL — this run genuinely pinned nothing",
          pinned17 == [] and not any(ln.startswith("pinned hook interpreters")
                                     for ln in p17.stdout.splitlines()))
    check("17: so the removal header does not promise a pinning block below it",
          "removed duplicate hook registrations (shown as they were FOUND — "
          "de-dup runs before the interpreter pinning, and nothing needed "
          "pinning this run):" in p17.stdout.splitlines())

    # A FIXED POINT, including the unclassified event: the default must not
    # oscillate, and the second run must not re-append anything.
    healed17 = open(settings, "rb").read()
    p17b = run(env)
    check("17: the second run reports no change", "no change" in p17b.stdout)
    check("17: the healed file is BYTE-IDENTICAL after a second run",
          open(settings, "rb").read() == healed17)

# --- 18. THE SHELL HOOK: REGISTERED, NEVER REWRITTEN, NEVER RE-APPENDED -------
# 🔴 base-clone-staleness.sh is the first NON-PYTHON hook this script registers,
# and it walks straight at the two defects the python side already paid for:
#
#   * THE UNBOUNDED RE-APPEND. The append surface asks "is this script already
#     registered" through a recogniser. While that recogniser was python-only it
#     could not read a `bash …` command back — including one it had just written
#     — so every run would append another copy. Measured on the python side at
#     14 -> 27 -> 40 over three runs. (c) below is what makes that visible: it is
#     the assertion that goes red if `hook_script_of` ever loses its shell half.
#   * THE REWRITE THAT DESTROYS IT. `normalized_command` replaces the FIRST token
#     with an absolute python. Applied here it produces
#     `<python> ~/.claude/hooks/base-clone-staleness.sh`, i.e. a SyntaxError on
#     every single session start. (b) pins that the rewrite pass does not see it.
#
# The fixture is EMPTY of SessionStart entries so the append is a real creation,
# not a preservation — an append bug is invisible on a populated event.
with tempfile.TemporaryDirectory() as tmp:
    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, ".claude"))
    settings = os.path.join(home, ".claude", "settings.json")
    with open(settings, "w") as f:
        json.dump({"hooks": {}}, f, indent=2)

    env = dict(os.environ)
    env["HOME"] = home
    env["DEVRC_HOOK_PYTHON"] = PY

    p18 = run(env)
    check("18: run exits 0", p18.returncode == 0)
    with open(settings) as f:
        d18 = json.load(f)

    ss = cmds(d18, "SessionStart")

    # (a) IT IS REGISTERED AT ALL — the gap this scenario exists to close. Before
    #     this change the switch delivered the script and registered nothing, so
    #     the hook fired only on the one host where the entry was hand-added.
    check("18: the shell hook is registered on SessionStart", BCS in ss)
    check("18: exactly once", ss.count(BCS) == 1)

    # POSITIVE CONTROL: SessionStart is an event the python side also writes to,
    # so a run that appended nothing at all cannot be what makes (a) green.
    check("18: POSITIVE CONTROL — the python hook landed on the same event too",
          LEDGER in ss)

    # (b) THE REWRITE PASS DOES NOT TOUCH IT. Asserted on the string, because the
    #     damage is exactly a substituted first token.
    check("18: the interpreter is still `bash`, NOT the pinned python",
          [c for c in ss if "base-clone-staleness.sh" in c] == [BCS])
    check("18: no SessionStart command pairs a python interpreter with a .sh",
          not any(c.endswith(".sh") and c.startswith(PY) for c in ss))
    check("18: ...and the run did not report it as pinned",
          "base-clone-staleness.sh" not in "".join(
              ln for ln in p18.stdout.splitlines() if ln.startswith("  ~ ")))

    # (c) THE FIXED POINT — the re-append defect, measured over a second run.
    after18 = open(settings, "rb").read()
    p18b = run(env)
    check("18: the second run reports no change", "no change" in p18b.stdout)
    check("18: the file is BYTE-IDENTICAL after a second run",
          open(settings, "rb").read() == after18)
    with open(settings) as f:
        check("18: still exactly one shell-hook registration after re-running",
              cmds(json.load(f), "SessionStart").count(BCS) == 1)

# --- 18b. THE HAND-PLACED ENTRY THIS SCRIPT MUST LEAVE ALONE ------------------
# 🔴 The host that pioneered the hook carries it with `timeout: 20` and a
# `statusMessage`, added by hand. Both are keys this script never writes, so it
# must neither duplicate the registration nor strip the keys — `removable_duplicate`
# reads any extra hook-level key as somebody else's configuration. A second, BARE
# copy is added alongside to prove the de-dup identity covers shell hooks too:
# without it the bare duplicate survives and the hook fires twice per session.
HANDPLACED = {"hooks": {"SessionStart": [
    {"hooks": [{"type": "command", "command": BCS,
                "timeout": 20, "statusMessage": "Checking base-clone staleness..."}]},
    {"hooks": [{"type": "command", "command": BCS}]},
]}}

with tempfile.TemporaryDirectory() as tmp:
    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, ".claude"))
    settings = os.path.join(home, ".claude", "settings.json")
    with open(settings, "w") as f:
        json.dump(HANDPLACED, f, indent=2)

    env = dict(os.environ)
    env["HOME"] = home
    env["DEVRC_HOOK_PYTHON"] = PY

    p18c = run(env)
    check("18b: run exits 0", p18c.returncode == 0)
    with open(settings) as f:
        d18c = json.load(f)

    ss_hooks = [h for e in d18c["hooks"]["SessionStart"] for h in e["hooks"]
                if "base-clone-staleness.sh" in h.get("command", "")]

    # ⚠ AN INVARIANT GUARD, labelled honestly — NOT regression coverage, and it
    # was demoted to this label after being mutation-tested rather than assumed.
    # TWO independent barriers stand in front of it, so no small mutant reaches
    # it: `_MANAGED_CMD_RE` is anchored on `\.py`, so a `.sh` command cannot match
    # the python recogniser however the ledgers are edited; and the rewrite pass
    # raises rather than writing a product it cannot read back. Both mutants that
    # SHOULD have reached it — widening `normalized_command` to `shell_match`,
    # with and without adding the `.sh` to MANAGED_HOOK_SCRIPTS — died on that
    # raise, which is the right failure but a different one.
    #
    # It stays because it states the consequence in one line, and the assertion
    # BELOW it is the one that actually caught both mutants (the file keeps two
    # unhealed copies when the registrar aborts mid-run).
    check("18b: the rewrite pass did NOT swap `bash` for a python interpreter",
          not any(c.endswith(".sh") and c.startswith(PY)
                  for c in cmds(d18c, "SessionStart")))
    check("18b: ...and the command is verbatim the one the tables write",
          [c for c in cmds(d18c, "SessionStart")
           if "base-clone-staleness.sh" in c] == [BCS])

    check("18b: the bare duplicate is healed away", len(ss_hooks) == 1)
    check("18b: ...and the survivor is the FIRST entry, keys intact",
          ss_hooks[:1] == [{"type": "command", "command": BCS, "timeout": 20,
                            "statusMessage": "Checking base-clone staleness..."}])
    check("18b: no THIRD copy was appended beside the hand-placed one",
          cmds(d18c, "SessionStart").count(BCS) == 1)


with open(SCRIPT) as f:
    src = f.read()
check("5: script uses os.replace for atomic write", "os.replace(" in src)

print()
if failures:
    print("%d FAILURE(S): %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("all register-nudge-hook tests passed")
