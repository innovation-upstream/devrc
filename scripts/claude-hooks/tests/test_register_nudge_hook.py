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
     `home-manager rollback` produces — HEALS to one entry per (matcher, script),
     keeping the FIRST occurrence with its position and its foreign keys, while a
     same-script entry under a DIFFERENT matcher, one carrying ARGUMENTS, one
     carrying a foreign key and one whose registration this script does not own
     (bash-guard) all SURVIVE.

  THE VALIDATED SEAM AND THE WARNINGS (same follow-up)
 13. A hostile $DEVRC_HOOK_PYTHON — an argument-carrying, relative, non-python or
     missing interpreter — is rejected with a warning and the resolved fallback is
     used, so three consecutive runs hold the same number of entries instead of
     growing without bound.
 14. Every command that NAMES a managed hook but is not in the pinnable form is
     reported on stderr, and nothing else is.

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
LEDGER = PY + " ~/.claude/hooks/agent-ledger-hook.py"
WRITEBACK = PY + " ~/.claude/hooks/clawgate-writeback-guard.py"
INTERVIEW = PY + " ~/.claude/hooks/clawgate-task-interview-guard.py"
BASH_GUARD = PY + " ~/.claude/hooks/bash-guard.py"

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
    check("6: PreToolUse gained exactly ONE entry, and it is the appended interview guard",
          len(pre_entries) == 4
          and pre_entries[3]["hooks"][0]["command"] == INTERVIEW
          and pre_entries[3].get("matcher") == "Bash")
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
    check("7: PreToolUse is the three commands it started with (one rewritten) plus the interview guard",
          pre == [FOREIGN_ELSEWHERE, BASH_GUARD, FOREIGN_UNMANAGED_HOOK, INTERVIEW])
    check("7: the interview guard is registered exactly once",
          pre.count(INTERVIEW) == 1)

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
                        SHELL_HOMEVAR, WB_EXPANDED, SEARCH_NUDGE, LEDGER})
    check("8: no hook was double-registered", len(post) == 5)
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
    check("9: every managed command it wrote names the absolute interpreter",
          all(c.startswith(PY + " ") for c in all_cmds if c != TMUX_STOP))

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
    check("10: ...and the ONLY thing added to PreToolUse is the interview guard",
          cmds(d4, "PreToolUse") == UNRECOGNISED + [INTERVIEW])
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
    check("11: every command now names the BUMPED interpreter",
          every and all(c.startswith(PY2 + " ") for c in every))
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
# does not find it, and appends a SECOND copy of every hook it owns — 13 of them,
# which is exactly what this fixture holds. Re-running the CURRENT registrant then
# normalises both copies to identical strings and the append pass, which keys on
# the SCRIPT, sees the hook as present — so WITHOUT a de-dup pass both survive and
# every managed hook fires twice forever: duplicate ledger rows, double
# notifications, and the write-back guard (the only one that can BLOCK) running
# twice per event.
#
# Both directions are asserted: the duplicates go, and a genuinely DISTINCT
# registration — same script, different `matcher` — SURVIVES. A hand-scoped narrow
# entry is a real configuration (see `registered_scripts` in the registrant), and
# collapsing it into the broad one would silently widen a scope somebody narrowed.
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
        one(LEDGER), one(WRITEBACK),
        one(OLD + "audit-pr-nudge.py", "Bash"),
        one(OLD + "shell-env-nudge.py", "Bash"),
        one(OLD + "search-tool-nudge.py", "Bash"),
        one(OLD + "agent-ledger-hook.py"),
        one(OLD + "clawgate-writeback-guard.py"),
    ],
    "Stop": [
        one(TMUX_STOP), one(CLAWGATE_STOP),
        one(NOTIFY), one(NEXT_STEP),
        # SURVIVOR A: same script, DIFFERENT matcher — a real configuration.
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
    check("12: PostToolUse healed to one entry per (matcher, script)",
          entries(d12, "PostToolUse") == [
              ("Bash", AUDIT), ("Bash", SHELL), ("Bash", SEARCH_NUDGE),
              (None, LEDGER), (None, WRITEBACK)])
    check("12: Stop healed, keeping the first copy of each and all three survivors",
          entries(d12, "Stop") == [
              (None, TMUX_STOP), (None, CLAWGATE_STOP),
              (None, NOTIFY), (None, NEXT_STEP), ("Bash", NEXT_STEP),
              (None, LEDGER), (None, WRITEBACK),
              (None, NOTIFY_ARGS), (None, LEDGER), (None, WRITEBACK)])
    check("12: SessionStart healed", entries(d12, "SessionStart") == [(None, LEDGER)])
    check("12: UserPromptSubmit healed",
          entries(d12, "UserPromptSubmit") == [(None, NOTIFY), (None, LEDGER)])
    check("12: SubagentStop healed", entries(d12, "SubagentStop") == [(None, NOTIFY)])
    # 🔴 THE OWNERSHIP BOUNDARY: bash-guard's second entry is rewritten, never
    # removed. Its registration belongs to the host.
    check("12: the doubled bash-guard entry is PINNED but NOT deleted",
          entries(d12, "PreToolUse") == [("Bash", BASH_GUARD), ("Bash", BASH_GUARD),
                                         ("Bash", INTERVIEW)])
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
    # It says what it removed, and how many.
    check("12: it reports the removals", "removed duplicate hook registrations:"
          in p12.stdout)
    check("12: exactly 14 duplicate registrations were removed",
          len([ln for ln in p12.stdout.splitlines() if ln.startswith("  - ")]) == 14)
    # ...and it is LOUD about the three double-fires it declined to touch.
    check("12: it warns about the duplicates it declined to remove",
          p12.stderr.count("is registered 2 times under matcher") == 4)
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
# 🔴 EACH CONDITION IS ISOLATED BY ITS OWN CASE, because these checks MASK one
# another and a mutant that dies for the neighbour's reason proves nothing about
# the guard it removed. `os.access(X_OK)` is False for a path that does not
# exist, so it hides the exists check unless the value is a real EXECUTABLE
# DIRECTORY; and a bare relative name is not a file in any plausible cwd, so the
# exists check hides the absolute check unless the value really does resolve from
# the directory the registrant runs in. Both of those cases are here for exactly
# that reason — without them, dropping either check is a surviving mutant.
NOT_A_PYTHON = fake_python("not-a-python", basename="sh")
_REL_BIN = os.path.dirname(fake_python("relative-store"))
EXEC_DIR = os.path.join(_FAKE_STORE, "a-directory", "bin", "python3.12")
os.makedirs(EXEC_DIR, exist_ok=True)

HOSTILE_OVERRIDES = [
    (PY + " -X utf8", "carries an argument", None),
    ("python3", "relative", None),
    ("python3.12", "relative but resolvable from the cwd", _REL_BIN),
    (NOT_A_PYTHON, "absolute and executable but not a python", None),
    (PY + "-gone", "absolute python path that does not exist", None),
    (EXEC_DIR, "an executable DIRECTORY with a python name", None),
]
RESOLVED = os.path.realpath(sys.executable)

for hostile, why, hostile_cwd in HOSTILE_OVERRIDES:
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
        # THE UNBOUNDED-GROWTH LOOP, CLOSED: a literal, not a comparison against
        # run 1 — a run that appended nothing at all would satisfy `a == b == c`.
        check(tag + ": three consecutive runs hold exactly 16 hook commands",
              counts == [16, 16, 16])
        with open(settings) as f:
            d13 = json.load(f)
        written = [c for ev in d13.get("hooks", {}) for c in cmds(d13, ev)
                   if c != TMUX_STOP]
        check(tag + ": every hook command names the resolved fallback instead",
              written and all(c.startswith(RESOLVED + " ") for c in written))
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
          cmds(d14, "PreToolUse") == UNPINNABLE + SILENT + [INTERVIEW])
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

with open(SCRIPT) as f:
    src = f.read()
check("5: script uses os.replace for atomic write", "os.replace(" in src)

print()
if failures:
    print("%d FAILURE(S): %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("all register-nudge-hook tests passed")
