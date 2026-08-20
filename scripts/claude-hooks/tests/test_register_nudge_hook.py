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
import os, sys, json, tempfile, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "register-nudge-hook.py")

# A synthetic store path — shaped like the real one, deliberately not any path
# that exists on this host, so nothing can pass by accident of the environment.
PY = "/nix/store/0000000000000000000000000000000-python3-3.12.14/bin/python3.12"

NOTIFY = PY + " ~/.claude/hooks/claude-notify.py"
CLAWGATE_STOP = "/home/zach/.claude/clawgate-stop-hook.sh"
SEARCH_NUDGE = PY + " ~/.claude/hooks/search-tool-nudge.py"
NEXT_STEP = PY + " ~/.claude/hooks/next-step-nudge.py"
TMUX_STOP = "~/.config/tmux/task-hook.sh"
LEDGER = PY + " ~/.claude/hooks/agent-ledger-hook.py"
WRITEBACK = PY + " ~/.claude/hooks/clawgate-writeback-guard.py"
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


def run(env):
    return subprocess.run([sys.executable, SCRIPT], env=env, capture_output=True, text=True)


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
    check("6: PreToolUse gained no entries — the rewrite never appends",
          len(pre_entries) == 3)

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
    check("7: PreToolUse is EXACTLY the three commands it started with, one rewritten",
          pre == [FOREIGN_ELSEWHERE, BASH_GUARD, FOREIGN_UNMANAGED_HOOK])

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
# conditions fails. They live on PreToolUse, the one event the append surface
# never writes to, so "unchanged" is a clean claim about the rewrite pass alone.
# Without these, dropping either the python-interpreter check or the start
# anchor is a mutation no test can see.
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
    check("10: every unrecognised command came back BYTE-IDENTICAL",
          cmds(d4, "PreToolUse") == UNRECOGNISED)
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
PY2 = "/nix/store/1111111111111111111111111111111-python3-3.12.15/bin/python3.12"

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

with open(SCRIPT) as f:
    src = f.read()
check("5: script uses os.replace for atomic write", "os.replace(" in src)

print()
if failures:
    print("%d FAILURE(S): %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("all register-nudge-hook tests passed")
