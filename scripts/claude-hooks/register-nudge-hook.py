#!/usr/bin/env python3
"""Idempotently register the devrc-managed Claude Code hooks in ~/.claude/settings.json.

settings.json is per-host and unmanaged (holds permissions/allowlists/secrets), so the
hook *scripts* are symlinked by home-manager but their *registration* is applied by
running this once per host.

🔴 THIS SCRIPT HAS **TWO** SURFACES, AND THEY ARE DELIBERATELY DIFFERENT WIDTHS.
Until 2026-08-20 there was only the first, and its docstring said "strictly
APPEND-ONLY / never rewrites". That is no longer true — read both before editing:

  * APPEND surface (NARROW, unchanged): the command tables below. An entry is added
    only when its exact command string is absent from that event's existing entries.
    Nothing else is ever appended. In particular bash-guard is NOT in these tables and
    must never be: this script does not own its registration, only its interpreter, so
    if a host has no bash-guard entry it must come back with no bash-guard entry.
  * REWRITE surface (WIDE, new): every hook entry on every event whose command invokes
    a devrc-managed hook script gets its INTERPRETER TOKEN normalised to an absolute
    /nix/store python — bash-guard INCLUDED. Only that token changes. The entry keeps
    its position in its array, its `matcher`, its `type` and every other key verbatim,
    and a command that does not resolve to a managed hook script comes back
    byte-identical.

WHY THE REWRITE EXISTS — the 127 window.
`home-manager switch` updates ~/.nix-profile as remove-then-install, so every switch
produces TWO profile generations. The intermediate one is a partial closure with NO
python3 on it (measured on this host: 337 binaries vs 625 in the final generation). A
hook registered as a bare `python3 ~/.claude/hooks/X.py` that fires inside that ~1s
window dies with `/bin/sh: line 1: python3: command not found`. Correlating hook errors
in ~/.claude/projects/**/*.jsonl against ~/.local/state/nix/profiles/profile-*-link
mtimes matched four times to the second; it had hit 16 sessions across five repos.
The serious half is bash-guard: it is a PreToolUse hook, exit 127 is classified
non-blocking, so during that window the guard FAILS OPEN and `git add -A` /
`git reset --hard` pass unchecked. A /nix/store path is immutable and GC-rooted by the
current home-manager generation, so an absolute one closes the window completely.

The interpreter is `os.path.realpath(sys.executable)` (override: $DEVRC_HOOK_PYTHON,
which exists so tests can inject a deterministic path). Both invocation routes converge
on the same immutable path, VERIFIED on this host rather than assumed:
  * from activation, home.nix passes ${pkgs.python312}/bin/python3, so sys.executable is
    already a store path and realpath only resolves python3 -> python3.12;
  * run by hand as `python3 …/register-nudge-hook.py`, sys.executable is
    ~/.nix-profile/bin/python3 — the blinking symlink — and realpath resolves it to the
    same /nix/store/…-python3-3.12.14/bin/python3.12.
So no plumbing change to home.nix or to register-hooks-activation.sh was needed.

Registers (APPEND surface):
  * PostToolUse(Bash): audit-pr-nudge.py, shell-env-nudge.py, search-tool-nudge.py
  * UserPromptSubmit / Stop / SubagentStop: claude-notify.py (the turn-finished
    notifier — a best-effort side-effect hook, appended alongside any existing
    Stop/clawgate-stop/tmux hooks).
  * Stop: next-step-nudge.py
  * SessionStart / UserPromptSubmit / PostToolUse / Stop: agent-ledger-hook.py
  * PostToolUse / Stop: clawgate-writeback-guard.py (the hook that makes the clawgate
    task write-back non-optional — PostToolUse watches for a read of a specific task
    id and for real work after it, Stop re-reads the board live and blocks a turn
    that is about to end with the card still uncommented).

🔴 TWO of these carry NO `matcher` on PostToolUse — agent-ledger-hook.py and
clawgate-writeback-guard.py — unlike the three nudges above. Those three are about
the shape of a Bash COMMAND, so `Bash` is the right scope. The other two are not:

  * the ledger records that a tool call HAPPENED, and scoping it to Bash would
    silently stop the heartbeat for a session doing a long stretch of Read/Edit work
    — which then renders `stale` while it is demonstrably working, because
    `classify_status` lets `stale` win over `busy`.
  * the write-back guard's second job is detecting that REAL WORK followed a task
    read, and `Edit` / `Write` / `NotebookEdit` are exactly that work. Under a `Bash`
    matcher the guard would never see an agent that read a card and then edited files
    for an hour — the commonest shape of the failure it exists to catch — and would
    stay silent for it. The Bash half (`git commit` / `git push` / `gh pr create`) is
    the part a matcher WOULD cover, and it is the smaller half.

The cost, MEASURED rather than asserted: ~21 ms per call against ~9 ms for a bare
interpreter start, and the hook throttles at 30s, so the overwhelming majority of
those calls resolve the pane from `$TMUX_PANE`, read one small file and exit
without writing and without spawning `tmux`. An earlier version of this comment
said "one short-circuiting process per tool call" while the hook was in fact
shelling out to `tmux` BEFORE consulting the throttle — two processes, every
call. That ordering is now reversed and pinned by a test.

🔴 Stop is a SHARED event with three pre-existing owners this script does not own and
must never disturb: the fuzzyclaw tmux writer (~/.config/tmux/task-hook.sh), the
clawgate stop hook (drives remote approval) and claude-notify.py (drives turn-finished
notifications). Clobbering any of them is a serious regression, so the APPEND surface
never reorders or removes anything, and the REWRITE surface never touches a command it
cannot prove is a managed hook invocation — the tmux and clawgate hooks are `.sh`
scripts and are left byte-identical. tests/test_register_nudge_hook.py asserts every
owner coexists afterwards, as a SET EQUALITY so the assertion fails when the set grows
as well as when it shrinks.

🔴 THE FIRST POST-MIGRATION RUN MUST NOT DOUBLE-REGISTER EVERY HOOK — the readiest way
to ship this fix broken. Two things prevent it, and only the second is sufficient:
  * the rewrite pass runs FIRST, so by the time the append pass reads the file the
    commands already carry the new interpreter;
  * and the append pass keys on the SCRIPT (`managed_script_of`), not on the exact
    command string. Exact-string keying was already fragile before this change — a
    host spelling the hooks dir `$HOME/…` got the hook registered twice — and the
    rewrite widens it, because a rewritten command matches the table's string only for
    the `~/` spelling. One recogniser answers "is this hook already registered here"
    for both surfaces, so they cannot disagree.
tests/test_register_nudge_hook.py drives a fully pre-migration settings.json and
asserts SET EQUALITY over each event afterwards, which fails on a duplicate.

next-step-nudge is registered on Stop ONLY, not SubagentStop: a subagent's turn ends
without ever reaching the operator, so it owes them no next step. The hook refuses that
event itself as well — belt and braces, because the registration is per-host mutable
state and the hook is the thing that actually ships.

Run on each host after a home-manager switch that adds a new hook:
    python3 ~/workspace/devrc/scripts/claude-hooks/register-nudge-hook.py
"""
import json, os, re, sys, tempfile

SETTINGS = os.path.expanduser("~/.claude/settings.json")
HOME = os.path.expanduser("~")


def hook_python():
    """The absolute interpreter to write into every managed hook command.

    $DEVRC_HOOK_PYTHON is the test seam — without it a test would have to assert
    against whatever interpreter pytest happened to run under. The fallback to a
    bare name only fires when sys.executable is empty (an embedded interpreter),
    which is not a configuration this ever runs in.
    """
    override = os.environ.get("DEVRC_HOOK_PYTHON")
    if override:
        return override
    return os.path.realpath(sys.executable) if sys.executable else "python3"


PYTHON = hook_python()


def with_python(path):
    return PYTHON + " " + path


# --------------------------------------------------------------------------- #
# THE REWRITE SURFACE: which scripts count as a devrc-managed hook.
#
# Derived from nix/home.nix's `home.file.".claude/hooks/*.py"` entries and pinned
# TWO-WAY against that file by tests/test_registrar_activation.py (which already
# owns the home.nix parsers), mirroring the idiom
# scripts/drift-check.sh uses for its nix/pkgs set: adding a hook to
# home.nix without accounting for it here fails the suite, and so does removing
# one. It is a literal rather than a scan because the DEPLOYED registrar is a
# /nix/store copy with no repo checkout to read.
#
# 🔴 The two exclusions are explicit and asserted, not incidental:
#   * HOOK_LIBRARY_MODULES ship into the hooks dir so they sit on the hook's
#     sys.path, but nothing ever invokes them AS a hook — there is no interpreter
#     token of theirs to fix.
#   * the registrar itself is invoked by home.nix's activation entry, which
#     already passes ${pkgs.python312}/bin/python3, and never from settings.json.
# --------------------------------------------------------------------------- #
MANAGED_HOOK_SCRIPTS = frozenset({
    "agent-ledger-hook.py",
    "audit-pr-nudge.py",
    "bash-guard.py",
    "claude-notify.py",
    "clawgate-writeback-guard.py",
    "next-step-nudge.py",
    "search-tool-nudge.py",
    "shell-env-nudge.py",
})

HOOK_LIBRARY_MODULES = frozenset({"agent_ledger.py", "guard_core.py"})

REGISTRAR_SCRIPT = "register-nudge-hook.py"

# The three spellings of the hooks directory a settings.json command may use. A
# command whose script path does not start with one of these is not something
# this script can prove it owns, so it is left alone.
HOOK_DIR_PREFIXES = tuple(dict.fromkeys(
    ("~/.claude/hooks/", "$HOME/.claude/hooks/", HOME + "/.claude/hooks/")
))

# <interpreter> <hooks-dir><basename.py>[ <args>]  — anchored at the start, so a
# command with a leading env assignment (`CLAUDE_HOST=wb …`) or any other prefix
# does not match and comes back byte-identical.
_MANAGED_CMD_RE = re.compile(
    r"^(?P<interp>\S+)[ \t]+(?:%s)(?P<base>[A-Za-z0-9_.+-]+\.py)(?=$|[ \t])"
    % "|".join(re.escape(p) for p in HOOK_DIR_PREFIXES)
)


def managed_script_of(cmd):
    """The devrc-managed hook script this command invokes, or None.

    THE single recogniser — both surfaces key on it, so "what counts as a
    managed hook command" is answered in one place and cannot disagree between
    the rewrite pass and the append pass.

    Conservative on purpose — three independent conditions must all hold:
      1. the command is `<one-token> <hooks-dir-path>[ args]`;
      2. the script's basename is in MANAGED_HOOK_SCRIPTS;
      3. the token that would be replaced is itself a python interpreter.
    (3) is what stops this from mangling a hypothetical `bash <hooks-dir>/x.py`
    or a wrapper; merely containing the word python is never enough, because the
    match is anchored on the PATH.
    """
    if not isinstance(cmd, str):
        return None
    m = _MANAGED_CMD_RE.match(cmd)
    if not m:
        return None
    if m.group("base") not in MANAGED_HOOK_SCRIPTS:
        return None
    if not os.path.basename(m.group("interp")).startswith("python"):
        return None
    return m.group("base")


def normalized_command(cmd):
    """Rewrite ONLY the interpreter token of a devrc-managed hook invocation.

    Anything `managed_script_of` does not recognise is returned unchanged — the
    identical object, so a caller's `new != old` check is exact.
    """
    if managed_script_of(cmd) is None:
        return cmd
    return PYTHON + cmd[_MANAGED_CMD_RE.match(cmd).end("interp"):]


# --------------------------------------------------------------------------- #
# THE APPEND SURFACE: the command tables. Unchanged in width — only the
# interpreter half of each string moved.
#
# 🔴 Keep the `~/.claude/hooks/<name>.py` literals spelled out here rather than
# built from MANAGED_HOOK_SCRIPTS: tests/test_registrar_activation.py parses this
# file for exactly that pattern to check home.nix deploys everything registered,
# and a computed path would make that check silently find nothing. For the same
# reason, do not write a prefixed hook path into a comment or docstring below —
# it would read as a registration that is not one.
# --------------------------------------------------------------------------- #

# PostToolUse(Bash) nudge hooks to ensure are registered.
POST_BASH_CMDS = [
    with_python("~/.claude/hooks/audit-pr-nudge.py"),
    with_python("~/.claude/hooks/shell-env-nudge.py"),
    with_python("~/.claude/hooks/search-tool-nudge.py"),
]

# The turn-finished notifier fires on these three events (single script,
# dispatched on the event name in its stdin payload).
NOTIFY_CMD = with_python("~/.claude/hooks/claude-notify.py")
NOTIFY_EVENTS = ["UserPromptSubmit", "Stop", "SubagentStop"]

# The agent activity ledger's writer. Four events, and a PostToolUse entry with
# no matcher — see the module docstring for why it is not scoped to Bash.
LEDGER_CMD = with_python("~/.claude/hooks/agent-ledger-hook.py")
LEDGER_EVENTS = ["SessionStart", "UserPromptSubmit", "PostToolUse", "Stop"]

# The clawgate write-back guard. Two events, and — like the ledger above — a
# PostToolUse entry with NO matcher, because half of what it watches for is an
# Edit/Write/NotebookEdit tool call. See the module docstring.
WRITEBACK_CMD = with_python("~/.claude/hooks/clawgate-writeback-guard.py")
WRITEBACK_EVENTS = ["PostToolUse", "Stop"]

# Hooks registered on exactly one event each: {event: [command, ...]}.
SINGLE_EVENT_CMDS = {
    "Stop": [with_python("~/.claude/hooks/next-step-nudge.py")],
}

with open(SETTINGS) as f:
    data = json.load(f)

hooks = data.setdefault("hooks", {})
added = []
rewritten = []


def registered_scripts(event_arrays):
    """Which MANAGED hook scripts an event's entry list already invokes.

    🔴 The append surface keys on the SCRIPT, not on the exact command string.
    Exact-string keying was already fragile — a host spelling the hooks dir
    `$HOME/…` instead of `~/…` got the hook registered a SECOND time — and the
    interpreter rewrite widens that hazard, because the rewritten string and
    the table's string agree only for the `~/` spelling. Keying on the script
    means "this hook is already registered on this event" is one fact with one
    answer, whatever spelling the host used.

    It also preserves the pre-existing deliberate behaviour it replaces: the
    scan covers the WHOLE event array, matchered entries included, so a
    hand-edit that scoped a hook more narrowly is LEFT IN PLACE rather than
    silently duplicated. That is the right failure — the append surface never
    rewrites what it does not own.
    """
    found = {managed_script_of(h.get("command"))
             for entry in event_arrays for h in entry.get("hooks", [])}
    found.discard(None)
    return found


# --- PASS 1: normalise the interpreter of every managed hook, on every event --
# 🔴 THE WIDE SURFACE, and the only place this script writes to an entry it does
# not own. bash-guard is deliberately in scope here and deliberately absent from
# the append tables above: its registration belongs to the host, its interpreter
# belongs to this script. If a host has no bash-guard entry, none is created.
#
# Mutating `h["command"]` in place is what preserves the entry's position in its
# array along with its `matcher`, its `type` and any key this script has never
# heard of. Nothing is inserted, removed or reordered by this pass.
#
# It runs BEFORE the append pass so the append reads already-normalised strings.
# That ordering is belt, not braces: what actually prevents the first
# post-migration run from double-registering everything is that the append pass
# keys on `managed_script_of`, not on an exact string. See the docstring.
for _event in list(hooks):
    _arr = hooks.get(_event)
    if not isinstance(_arr, list):
        continue
    for _entry in _arr:
        if not isinstance(_entry, dict):
            continue
        _entry_hooks = _entry.get("hooks")
        if not isinstance(_entry_hooks, list):
            continue
        for _h in _entry_hooks:
            if not isinstance(_h, dict):
                continue
            _old = _h.get("command")
            _new = normalized_command(_old)
            if _new != _old:
                _h["command"] = _new
                rewritten.append("%s: %s" % (_event, _new))

# --- PostToolUse(Bash) nudge hooks (append-only, matcher=Bash) ---------------
post = hooks.setdefault("PostToolUse", [])
post_registered = registered_scripts(post)
for cmd in POST_BASH_CMDS:
    if managed_script_of(cmd) in post_registered:
        continue
    post.append({"matcher": "Bash", "hooks": [{"type": "command", "command": cmd}]})
    added.append("PostToolUse(Bash): " + cmd)

# --- claude-notify on the three turn-boundary events (append-only) -----------
# Preserve every existing entry on each event array (e.g. a clawgate-stop-hook or
# a tmux Stop hook) — only add claude-notify where it's missing.
for event in NOTIFY_EVENTS:
    arr = hooks.setdefault(event, [])
    if managed_script_of(NOTIFY_CMD) in registered_scripts(arr):
        continue
    arr.append({"hooks": [{"type": "command", "command": NOTIFY_CMD}]})
    added.append("%s: %s" % (event, NOTIFY_CMD))

# --- the agent activity ledger writer (append-only, same discipline) ---------
# 🔴 `registered_scripts` looks across the WHOLE event array, matchered entries
# included, so re-running this after a hand-edit that scoped the ledger to Bash
# would NOT add a second entry — it would leave the narrower one in place. That
# is the right failure (the append surface never rewrites what it does not own)
# and it is the one case where this script's idempotence hides a real
# misconfiguration; `tests/test_register_nudge_hook.py` pins the shape it writes.
for event in LEDGER_EVENTS:
    arr = hooks.setdefault(event, [])
    if managed_script_of(LEDGER_CMD) in registered_scripts(arr):
        continue
    arr.append({"hooks": [{"type": "command", "command": LEDGER_CMD}]})
    added.append("%s: %s" % (event, LEDGER_CMD))

# --- the clawgate write-back guard (append-only, same discipline) ------------
# 🔴 Stop already carries three foreign owners at this point; this one is appended
# after them, never inserted, so they have all observed the turn before it can add
# anything to it. It is the only entry here that can BLOCK, and it caps itself at two
# consecutive blocks per task — well inside the CLI's own cap of 8.
for event in WRITEBACK_EVENTS:
    arr = hooks.setdefault(event, [])
    if managed_script_of(WRITEBACK_CMD) in registered_scripts(arr):
        continue
    arr.append({"hooks": [{"type": "command", "command": WRITEBACK_CMD}]})
    added.append("%s: %s" % (event, WRITEBACK_CMD))

# --- single-event hooks (append-only, same discipline) -----------------------
for event, cmds in SINGLE_EVENT_CMDS.items():
    arr = hooks.setdefault(event, [])
    present = registered_scripts(arr)
    for cmd in cmds:
        if managed_script_of(cmd) in present:
            continue
        arr.append({"hooks": [{"type": "command", "command": cmd}]})
        added.append("%s: %s" % (event, cmd))

# 🔴 Only write when something actually moved. The rewrite pass now re-runs on
# every switch, so an unconditional write would re-dump (indent=2) the operator's
# per-host settings.json — permissions and all — every time, for nothing.
if not added and not rewritten:
    print("all devrc-managed hooks already registered — no change")
    sys.exit(0)

# Atomic write: settings.json gates permissions, so a crash mid-write must never
# leave it truncated/corrupt. Write a temp file in the same dir, then os.replace
# (atomic rename on the same filesystem) so readers only ever see the old file
# or the fully-written new one.
d = os.path.dirname(SETTINGS) or "."
fd, tmp = tempfile.mkstemp(dir=d, prefix=".settings.", suffix=".tmp")
try:
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, SETTINGS)
except Exception:
    try:
        os.remove(tmp)
    except OSError:
        pass
    raise
if rewritten:
    print("pinned hook interpreters to %s:" % PYTHON)
    for c in rewritten:
        print("  ~", c)
if added:
    print("registered hooks:")
    for c in added:
        print("  +", c)
