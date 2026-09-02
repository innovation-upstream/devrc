#!/usr/bin/env python3
"""Idempotently register the devrc-managed Claude Code hooks in ~/.claude/settings.json.

settings.json is per-host and unmanaged (holds permissions/allowlists/secrets), so the
hook *scripts* are symlinked by home-manager but their *registration* is applied by
running this once per host.

🔴 THIS SCRIPT HAS **THREE** SURFACES, AND THEY ARE DELIBERATELY DIFFERENT WIDTHS.
Until 2026-08-20 there was only the first, and its docstring said "strictly
APPEND-ONLY / never rewrites". That is no longer true — read all three before editing:

  * APPEND surface (NARROW, oldest): the command tables below. An entry is added
    only when that event does not already invoke the script. Nothing else is ever
    appended. In particular bash-guard is NOT in these tables and must never be:
    this script does not own its registration, only its interpreter, so if a host
    has no bash-guard entry it must come back with no bash-guard entry.
  * REWRITE surface (WIDE): every hook entry on every event whose command invokes
    a devrc-managed hook script gets its INTERPRETER TOKEN normalised to an absolute
    /nix/store python — bash-guard INCLUDED. Only that token changes. The entry keeps
    its position in its array, its `matcher`, its `type` and every other key verbatim,
    and a command that does not resolve to a managed hook script comes back
    byte-identical.
  * DE-DUP surface (NARROWEST, newest): a second registration of the SAME managed
    script on the SAME event under the SAME SCOPE is deleted, keeping the FIRST
    occurrence with its position and every key it arrived with. SCOPE is the
    entry's `matcher` on an event that HAS matchers, and nothing at all on an
    event that does not — the two are enumerated in NO_MATCHER_EVENTS and
    MATCHER_EVENTS below, from the hooks documentation. That distinction is
    load-bearing rather than pedantic: `Stop` and `UserPromptSubmit`, where this
    script registers most of its hooks, fire on every occurrence, so two entries
    for one script there BOTH run however their matchers differ. It is narrower
    than the append surface in one direction and equal in the other: it removes
    only an entry of EXACTLY the shape this script writes (no foreign key, one
    hook, no arguments after the script path) AND only for a script this script
    registers — so a doubled bash-guard, whose registration belongs to the host,
    is reported rather than removed. Any MANAGED hook script left registered
    more than once UNDER ONE SCOPE is named on stderr — the same scope the
    identity above uses, i.e. the entry's `matcher` on an event that has
    matchers and the whole event on one that does not, so two entries for one
    script under DIFFERENT matchers on a matcher-supporting event are not
    counted and not reported. A `matcher` key sitting on an event that has no
    matchers is named too. A doubled FOREIGN command is NOT:
    the recogniser cannot read it, so this script can neither count it reliably
    nor tell a deliberate double from an accident in config it does not own.
    See "WHY THE DE-DUP EXISTS" below.

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

WHY THE DE-DUP EXISTS — a home-manager ROLLBACK re-runs an OLDER registrar.
`home-manager rollback` / `--switch-generation` (and a hand-run from an older
checkout) runs the PREVIOUS registrar against a settings.json this one has already
pinned. That older code keyed "is it already registered?" on the exact string
`python3 ~/…`, does not find it, and appends a SECOND copy of every hook it owns —
13 measured. Re-running THIS registrar then normalises both copies to identical
strings, and because the append pass only asks "is this script registered on this
event", it sees the hook as present and BOTH copies survive: every managed hook fires
twice, forever. Duplicate ledger rows, double notifications, and the write-back guard
— the only one that can BLOCK — running twice per event. So the file must HEAL, not
merely fail to get worse.

The interpreter is `os.path.realpath(sys.executable)` (override: $DEVRC_HOOK_PYTHON,
which exists so tests can inject a deterministic path). Both invocation routes converge
on the same immutable path, VERIFIED on this host rather than assumed:
  * from activation, home.nix passes ${pkgs.python312}/bin/python3, so sys.executable is
    already a store path and realpath only resolves python3 -> python3.12;
  * run by hand as `python3 …/register-nudge-hook.py`, sys.executable is
    ~/.nix-profile/bin/python3 — the blinking symlink — and realpath resolves it to the
    same /nix/store/…-python3-3.12.14/bin/python3.12.
So no plumbing change to home.nix or to register-hooks-activation.sh was needed.

🔴 $DEVRC_HOOK_PYTHON IS A TEST SEAM WITH PRODUCTION REACH, so it is VALIDATED.
Activation inherits the operator's environment, so an exported override is a value
this script would otherwise write verbatim into all 14 hook commands. An override
that is relative, missing, non-executable, or not a single `python*` token is
REJECTED with a warning and the resolved fallback is used instead — see
`hook_python`, which also explains why it falls back rather than hard-failing.

Registers (APPEND surface):
  * PostToolUse(Bash): audit-pr-nudge.py, shell-env-nudge.py, search-tool-nudge.py,
    git-add-provenance-nudge.py
  * UserPromptSubmit / Stop / SubagentStop: claude-notify.py (the turn-finished
    notifier — a best-effort side-effect hook, appended alongside any existing
    Stop/clawgate-stop/tmux hooks).
  * Stop: next-step-nudge.py
  * SessionStart: base-clone-staleness.sh — THE ONLY NON-PYTHON HOOK REGISTERED
    HERE, and the reason there are two recognisers below. It refreshes CLAUDE.md
    and .claude/skills/** in the repo at cwd from that repo's upstream branch,
    because those two surfaces load into agent context FROM THE WORKING TREE, so
    a base clone that has fallen behind serves stale, authoritative-looking
    instructions with nothing on screen to indicate it.
  * SessionStart / UserPromptSubmit / PostToolUse / Stop: agent-ledger-hook.py
  * PostToolUse / Stop: clawgate-writeback-guard.py (the hook that makes the clawgate
    task write-back non-optional — PostToolUse watches for a read of a specific task
    id and for real work after it, Stop re-reads the board live and blocks a turn
    that is about to end with the card still uncommented).
  * PostToolUse / Stop: handoff-write-guard.py (the same shape on the OTHER record —
    PostToolUse watches for a READ of a specific handoff doc and for real work after
    it, Stop measures whether any handoff was written since that read and blocks a
    turn about to end without one. Armed on the read, never on session start).
  * PreToolUse(Bash) / PostToolUse(Bash): bg-command-capture.py — INSTRUMENTATION,
    not a guard and not a nudge. It has no verdict, writes nothing to stdout and
    returns 0 on every input; it appends the VERBATIM command string of every
    backgrounded (or status-masking) Bash call to a bounded log, because that
    string is the one artifact that discriminates between the open hypotheses in
    ClickUp 868ktvqf9 and the harness keeps it nowhere reachable afterwards. Both
    events, because they carry different halves: PreToolUse has the verbatim
    command and `run_in_background`, PostToolUse has the `backgroundTaskId` that
    names the run's output file.

🔴 THREE of these carry NO `matcher` on PostToolUse — agent-ledger-hook.py,
clawgate-writeback-guard.py and handoff-write-guard.py — unlike the three nudges
above. The NUDGES are about the shape of a Bash COMMAND, so `Bash` is the right
scope for them. These three are not:

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
  * the handoff write guard has the same second job, on the other record: it is
    watching for REAL WORK after a handoff READ, and — additionally — for the
    handoff WRITE itself, which is a `Write`/`Edit` of a `claudedocs/handoff-*.md`
    whenever the doc was not produced by `handoff_doc.py`. Under a `Bash` matcher
    it would be blind to both, i.e. it would nag sessions that HAD recorded their
    work and miss the ones that had not.

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
never reorders or removes anything, the REWRITE surface never touches a command it
cannot prove is a managed hook invocation (the tmux and clawgate hooks are `.sh`
scripts and are left byte-identical), and the DE-DUP surface deletes only an entry
whose every key it can account for. tests/test_register_nudge_hook.py asserts every
owner coexists afterwards, as a SET EQUALITY so the assertion fails when the set grows
as well as when it shrinks.

🔴 THE FIRST POST-MIGRATION RUN MUST NOT DOUBLE-REGISTER EVERY HOOK — the readiest way
to ship this fix broken. Two things prevent it, and only the second is sufficient:
  * the rewrite pass runs before the append pass, so by the time the append pass reads
    the file the commands already carry the new interpreter;
  * and the append pass keys on the SCRIPT (`managed_script_of`), not on the exact
    command string. Exact-string keying was already fragile before this change — a
    host spelling the hooks dir `$HOME/…` got the hook registered twice — and the
    rewrite widens it, because a rewritten command matches the table's string only for
    the `~/` spelling. One recogniser answers "is this hook already registered here"
    for both surfaces, so they cannot disagree.
tests/test_register_nudge_hook.py drives a fully pre-migration settings.json and
asserts SET EQUALITY over each event afterwards, which fails on a duplicate.

🔴 CONSERVATIVE MATCHING IS NOT SILENT MATCHING. `python3 -u ~/…`, a leading
`PYTHONPATH=…`, `${HOME}/…`, a quoted path and a `…; …` compound all come back
byte-identical from the rewrite pass — correctly, since this script cannot prove it
owns them — and each one stays in the 127 window. This whole change exists because a
silent failure ran for months, so every command that NAMES a managed hook script and
is not in the pinnable form is reported on stderr, which the activation wrapper
relays into the switch log.

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


def warn(msg):
    """Every diagnostic goes to stderr under one greppable token.

    register-hooks-activation.sh captures the registrar with `2>&1` and relays the
    block line by line into the switch log, so a warning here is something the
    operator actually sees on the next `home-manager switch`.
    """
    print("WARNING " + msg, file=sys.stderr)


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
    "bg-command-capture.py",
    "claude-notify.py",
    "clawgate-task-interview-guard.py",
    "clawgate-writeback-guard.py",
    "gh-issue-closing-condition-guard.py",
    "git-add-provenance-nudge.py",
    "handoff-write-guard.py",
    "next-step-nudge.py",
    "search-tool-nudge.py",
    "session-stamp.py",
    "shell-env-nudge.py",
})

# --------------------------------------------------------------------------- #
# THE SHELL-HOOK LEDGER — a SEPARATE set, and the separation is the whole design.
#
# 🔴 MANAGED_HOOK_SCRIPTS above is the REWRITE surface's ledger: "scripts whose
# INTERPRETER TOKEN this script may replace with an absolute /nix/store python".
# A bash hook has no business there. Adding `base-clone-staleness.sh` to that set
# would hand it to `normalized_command`, which rewrites the first token
# unconditionally — turning the hook's leading `bash` into an absolute python,
# i.e. a SyntaxError on every session start. The recogniser's third condition
# (basename starts with `python`) is what stands between those two facts, so it
# is never relaxed.
#
# 🔴 The path is deliberately NOT spelled with its hooks-dir prefix anywhere in
# this comment. test_registrar_activation.py parses this file for that exact
# pattern and reads every hit as a REGISTRATION — the warning above the command
# tables says so — and a prefixed path in prose is therefore a phantom
# registration that keeps the delivery-seam test green after the real table
# entry is gone. Measured: with this comment written the long way, deleting the
# SessionStart registration outright left that suite fully green.
#
# This set feeds only the IDENTITY question — "which hook script does this command
# invoke", used by the append and de-dup surfaces. Identity is about WHICH SCRIPT;
# the rewrite is about WHICH INTERPRETER. They were one function while every hook
# was python; a non-python hook is what forces them apart.
#
# 🔴 It must be non-empty for the append pass to be safe: a command this script
# WRITES but cannot READ BACK is re-appended on every run — measured on the
# python side at 14 -> 27 -> 40 commands over three runs, +13 every time, silent
# and unbounded. `with_bash` re-proves that round-trip for the strings actually
# written, exactly as `with_python` does.
# --------------------------------------------------------------------------- #
MANAGED_SHELL_HOOK_SCRIPTS = frozenset({
    "base-clone-staleness.sh",
})

HOOK_LIBRARY_MODULES = frozenset({"agent_ledger.py", "bg_command_capture.py",
                                  "guard_core.py", "session_trailer.py"})

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


def managed_match(cmd):
    """The regex match for a devrc-managed hook invocation, or None.

    THE single recogniser — all three surfaces key on it, so "what counts as a
    managed hook command" is answered in one place and cannot disagree between
    the rewrite pass, the de-dup pass and the append pass. It returns the MATCH
    rather than a bool so the two callers that need a span (`bare_managed_command`,
    `normalized_command`) reuse it instead of re-running the regex — a second
    `.match()` is not only redundant, it is a `.end()` on an `Optional[Match]`
    that only the earlier call proves is not None, which pyright flags and a
    future refactor could make true.

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
    return m


def managed_script_of(cmd):
    """The devrc-managed hook script this command invokes, or None.

    🔴 PYTHON ONLY, and every caller that keys on it is making a claim about the
    INTERPRETER, not about identity. For "which hook script is this", use
    `hook_script_of` — see MANAGED_SHELL_HOOK_SCRIPTS for why the two are
    separate functions.
    """
    m = managed_match(cmd)
    return None if m is None else m.group("base")


# <bash> <hooks-dir><basename.sh>[ <args>] — the shell mirror of _MANAGED_CMD_RE,
# anchored identically so a leading env assignment or any other prefix does not
# match. The interpreter condition is `bash` rather than `python*`; it is checked
# in `shell_match` for the same reason its sibling checks it, so that a
# hypothetical `python3 <hooks-dir>x.sh` is not mistaken for one of ours.
_SHELL_CMD_RE = re.compile(
    r"^(?P<interp>\S+)[ \t]+(?:%s)(?P<base>[A-Za-z0-9_.+-]+\.sh)(?=$|[ \t])"
    % "|".join(re.escape(p) for p in HOOK_DIR_PREFIXES)
)


def shell_match(cmd):
    """The regex match for a devrc-managed SHELL hook invocation, or None.

    Same three conservative conditions as `managed_match`, with `bash` in place of
    `python*`. Deliberately does NOT accept `sh`, `zsh` or a bare `./path`: the
    hook is a bash script (it uses arrays and `[[`), home.nix's activation and the
    tables below both spell it `bash`, and widening the accepted spellings here
    would let this script claim ownership of an entry somebody else wrote
    differently on purpose.
    """
    if not isinstance(cmd, str):
        return None
    m = _SHELL_CMD_RE.match(cmd)
    if not m:
        return None
    if m.group("base") not in MANAGED_SHELL_HOOK_SCRIPTS:
        return None
    if os.path.basename(m.group("interp")) != "bash":
        return None
    return m


def hook_script_of(cmd):
    """THE IDENTITY RECOGNISER — which managed hook script this command invokes.

    🔴 The union of the python and shell recognisers, and the single answer to
    "is this hook already registered on this event". Every append-surface
    membership test and the whole de-dup identity key go through here, so the two
    surfaces cannot disagree about what "already registered" means — the same
    property `registered_scripts` documents for the cross-spelling case, now
    holding across interpreters too.

    NOT used by the rewrite pass. `normalized_command` keys on `managed_match`
    alone, because rewriting the interpreter of a bash hook to a python one is
    precisely the damage MANAGED_SHELL_HOOK_SCRIPTS exists to prevent.
    """
    base = managed_script_of(cmd)
    if base is not None:
        return base
    m = shell_match(cmd)
    return None if m is None else m.group("base")


def bare_managed_command(cmd):
    """A managed hook invocation with NOTHING after the script path.

    The de-dup surface removes only these. `<interp> <path> --flag` names the same
    script but is a DIFFERENT configuration — somebody typed those arguments — and
    deleting it would be this script overwriting a decision it did not make.

    Covers BOTH interpreters, because the de-dup surface it serves keys on
    `hook_script_of`. A shell hook registered with an argument is somebody's
    configuration in exactly the way a python one is.
    """
    m = managed_match(cmd) or shell_match(cmd)
    return m is not None and m.end() == len(cmd)


def normalized_command(cmd):
    """Rewrite ONLY the interpreter token of a devrc-managed hook invocation.

    Anything the recogniser does not recognise is returned unchanged — the
    identical object, so a caller's `new != old` check is exact.
    """
    m = managed_match(cmd)
    if m is None:
        return cmd
    return PYTHON + cmd[m.end("interp"):]


# The probe the interpreter self-checks below are built from: the first managed
# script under the first accepted hooks-dir prefix. COMPUTED, never spelled out —
# tests/test_registrar_activation.py parses this file for a hooks-dir path literal
# and reads every hit as a registration this script performs, so a literal here
# would read as one.
_PROBE_SCRIPT = sorted(MANAGED_HOOK_SCRIPTS)[0]


def unusable_interpreter(interp):
    """Why `interp` may not be written into a hook command — or None if it may.

    🔴 THE SELF-CHECK, and the first condition is the important one: a command
    built from this interpreter must be one `managed_script_of` RECOGNISES. Without
    it, an interpreter carrying an argument (`python3 -X utf8`) or a non-python
    basename makes the registrar's own output invisible to its own recogniser, so
    the append pass re-appends every hook on every run — MEASURED against the
    pre-fix code at 14 -> 27 -> 40 commands over three runs, +13 every time,
    unbounded and silent. Deriving the check from the recogniser
    rather than restating its rules is what stops the two from drifting apart.

    The remaining three are about the value being a usable interpreter at all:
    relative would reopen the very PATH window this pinning closes, and a missing
    or non-executable path would turn every hook into a permanent 127.
    """
    if managed_script_of(
            interp + " " + HOOK_DIR_PREFIXES[0] + _PROBE_SCRIPT) != _PROBE_SCRIPT:
        return ("commands built from it are not recognised by this script's own "
                "recogniser — it must be ONE token whose basename starts with "
                "`python` (no arguments, no wrapper, no spaces)")
    if not os.path.isabs(interp):
        return ("it is not an absolute path, and a PATH-resolved interpreter is "
                "the exact failure this pinning exists to close")
    if not os.path.isfile(interp):
        return "no such file"
    if not os.access(interp, os.X_OK):
        return "not executable"
    return None


def hook_python():
    """The absolute interpreter to write into every managed hook command.

    $DEVRC_HOOK_PYTHON is the test seam — without it a test would have to assert
    against whatever interpreter pytest happened to run under. It is validated
    because activation inherits the operator's environment, so an exported value
    reaches production and would otherwise be written verbatim into every hook.

    🔴 A REJECTED OVERRIDE FALLS BACK; IT DOES NOT HARD-FAIL. Deliberate: the
    fallback (`realpath(sys.executable)`) is what activation wants anyway, since
    home.nix invokes this under ${pkgs.python312}/bin/python3 — so falling back
    produces a CORRECTLY pinned settings.json, while hard-failing would leave the
    hooks on whatever they carried before, i.e. it would keep the 127 window open
    on the strength of a stray environment variable. It also cannot endanger the
    wrapper's exit-0 contract, because there is nothing to contain: no non-zero
    status is produced. The warning is the loud part.

    🔴 THE BARE-NAME FALLBACK IS NEVER WRITTEN ANYWHERE. It fires only when
    sys.executable is empty (an embedded interpreter), and `python3` is relative,
    so the module-level guard below refuses to touch settings.json and exits 2.
    That is the whole point of spelling it: it makes "this process has no
    interpreter path at all" refuse with `it is not an absolute path` instead of
    with whatever `os.path.realpath("")` — the CWD — happens to trip, which would
    blame the recogniser for something else entirely.
    """
    fallback = os.path.realpath(sys.executable) if sys.executable else "python3"
    override = os.environ.get("DEVRC_HOOK_PYTHON")
    if not override:
        return fallback
    reason = unusable_interpreter(override)
    if reason is None:
        return override
    warn("$DEVRC_HOOK_PYTHON=%r IGNORED: %s. Using %s instead. Unset it, or point "
         "it at an absolute python binary." % (override, reason, fallback))
    return fallback


PYTHON = hook_python()

# 🔴 INVARIANT GUARD, labelled honestly: it covers the case where the RESOLVED
# interpreter — not an override, which hook_python already rejected — is one this
# script may not write. TWO routes reach it, and neither is constructible
# hermetically from a test here:
#   * a CPython whose realpath basename is not `python*`, which fails the
#     recogniser condition; and
#   * an empty sys.executable, which makes hook_python fall back to the bare name
#     `python3` — caught by the ABSOLUTE-PATH condition, not the recogniser. So
#     that fallback can never actually be written into a hook command; see
#     hook_python for why it is spelled anyway.
# It is mutation-checked reachable instead (drop the
# validation from hook_python and run with DEVRC_HOOK_PYTHON=/bin/sh, and this
# fires with this message). Refusing to write is the right failure: the wrapper
# turns a non-zero status into a WARNING and exits 0, and settings.json — which
# has not been opened yet — keeps whatever registrations it already had.
_UNUSABLE = unusable_interpreter(PYTHON)
if _UNUSABLE is not None:
    warn("refusing to touch %s: the interpreter this script resolved (%r) cannot be "
         "written into a hook command — %s. The file is left exactly as it was."
         % (SETTINGS, PYTHON, _UNUSABLE))
    sys.exit(2)


def with_python(path):
    """Build a command for the append tables — and prove this script can read it back.

    Second half of the self-check above, applied to the strings actually written
    rather than to a probe. Same invariant-guard caveat: with PYTHON validated it
    cannot fire, and it exists so that "my own output is unrecognisable to me"
    cannot come back through some future refactor of the tables.
    """
    cmd = PYTHON + " " + path
    if managed_script_of(cmd) is None:
        raise AssertionError(
            "register-nudge-hook.py would write a command its own recogniser does "
            "not recognise, so every run would re-append it: %r" % cmd)
    return cmd


def with_bash(path):
    """Build a SHELL hook command for the append tables — same round-trip proof.

    🔴 `bash` is spelled bare and PATH-resolved, unlike the python interpreter,
    and that asymmetry is deliberate rather than an oversight:

      * the python pinning exists to close a specific window — during ~1s of every
        home-manager switch the intermediate profile generation has no `python3`
        on PATH, so a hook firing then dies with `command not found`, and
        bash-guard FAILS OPEN when it does. `bash` is not on that profile; it
        comes from the system, so the window does not exist for it.
      * settings.json is read by the CLI, which runs hook commands through a
        shell — a shell that, by definition, already exists.
      * and this hook is a SessionStart reporter with no verdict: if it somehow
        failed to launch, the cost is a missing banner, not a guard that fails
        open.

    Pinning it would also mean writing a /nix/store bash path that a
    `home-manager rollback` would leave dangling, which is a live hazard the
    de-dup surface exists to recover from. Bare is the safer end of that trade.
    """
    cmd = "bash " + path
    if hook_script_of(cmd) is None:
        raise AssertionError(
            "register-nudge-hook.py would write a shell command its own recogniser "
            "does not recognise, so every run would re-append it: %r" % cmd)
    return cmd


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
    # git-add-provenance: warns when a `git add` staged an untracked file whose
    # mtime predates the session — another effort's in-flight work swept into the
    # index. PostToolUse, not a guard_core check: staging is reversible, and in this
    # repo staging a NEW file is mandatory, so a blocking version would fire on the
    # deploy path for every new skill, hook and test.
    with_python("~/.claude/hooks/git-add-provenance-nudge.py"),
    # Not a nudge — INSTRUMENTATION. It injects nothing and blocks nothing; on
    # this event it exists only to capture `tool_response.backgroundTaskId`,
    # which names the output file a backgrounded run writes to and appears on NO
    # other event. See scripts/lib/bg_command_capture.py (ClickUp 868ktvqf9).
    with_python("~/.claude/hooks/bg-command-capture.py"),
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

# The handoff write-back guard — the write-back guard's counterpart on the OTHER
# record. That one makes a clawgate pickup report back to the board; this one makes
# a session that RESUMED a handoff doc write one before it stops. Same two events and
# the same NO-matcher PostToolUse entry, for the same reason: half of what it watches
# for (the work, and the handoff write itself) is an Edit/Write tool call.
#
# 🔴 NOT registered on SessionStart, deliberately, and that is the design rather than
# an omission: arming there would fire on every session that never touched a handoff.
# It arms on the READ — see the hook's module docstring for the 253-session
# measurement that selects the read as the trigger.
HANDOFF_GUARD_CMD = with_python("~/.claude/hooks/handoff-write-guard.py")
HANDOFF_GUARD_EVENTS = ["PostToolUse", "Stop"]

# 🔴 THE FIRST PreToolUse HOOK THIS SCRIPT REGISTERS, and that is a widening of
# the append surface, not a rewording of it. Until now PreToolUse was
# rewrite-only: bash-guard's INTERPRETER was this script's, its REGISTRATION was
# the host's. That asymmetry exists because bash-guard was hand-placed on both
# hosts before the registrar existed and this script cannot prove it owns the
# entry. The interview guard has no such history — it has never been registered
# anywhere — so leaving it to a hand edit would reproduce #452 exactly: a hook
# that ships to both hosts, reports a successful switch, and sits INERT.
#
# bash-guard is still never CREATED (test 9 in tests/test_register_nudge_hook.py
# pins that): the append surface adds only what is in this table.
PRE_BASH_CMDS = [
    with_python("~/.claude/hooks/clawgate-task-interview-guard.py"),
    # 🔴 THE LOAD-BEARING HALF of the 868ktvqf9 instrument, and the SECOND
    # PreToolUse entry this script registers. `tool_input.command` (verbatim) and
    # `tool_input.run_in_background` exist on this event and on no other — a
    # backgrounded run's PostToolUse response carries only a task id, and the
    # completion notification is not a hook event at all. If this entry is
    # missing, the one artifact that discriminates between the open hypotheses is
    # never recorded and the next hit needs a reconstruction again.
    #
    # 🔴 Unlike its neighbour above, this hook has NO verdict: it never emits a
    # permissionDecision, never writes to stdout, and returns 0 on every input.
    # Registering it does not widen what PreToolUse can REFUSE — only what it
    # observes.
    with_python("~/.claude/hooks/bg-command-capture.py"),
    # 🔴 THE THIRD PreToolUse entry, and — unlike the one above — the SECOND that
    # can REFUSE. It denies a `gh issue create` whose body names no closing
    # condition, the RULES.md rule whose failure mode was measured to be SALIENCE
    # rather than delivery (6 issues / 0 conditions unbriefed vs 10 / 10 briefed,
    # with BOTH agents holding the rule). Registered here rather than left to a
    # hand edit for the #452 reason its neighbours record: a hook that ships
    # unregistered reports a successful switch and sits INERT with nothing on
    # screen to say so.
    with_python("~/.claude/hooks/gh-issue-closing-condition-guard.py"),
]

# Hooks registered on exactly one event each: {event: [command, ...]}.
#
# 🔴 The SessionStart entry is the first NON-PYTHON registration this script makes.
# It is appended in the bare `{"type","command"}` shape like every other — NOT with
# a `timeout` — even though the host that pioneered this hook by hand carries
# `timeout: 20`. Two reasons, both load-bearing: `removable_duplicate` treats any
# extra hook-level key as somebody else's configuration, so a timeout this script
# wrote would be a duplicate it could never heal after a rollback; and that
# hand-placed entry is exactly what the append surface must LEAVE ALONE. It does —
# `registered_scripts` keys on the script, so the richer entry counts as registered
# and nothing is added beside it.
SINGLE_EVENT_CMDS = {
    "Stop": [with_python("~/.claude/hooks/next-step-nudge.py")],
    "SessionStart": [with_bash("~/.claude/hooks/base-clone-staleness.sh")],
}

# 🔴 THE OWNERSHIP BOUNDARY OF THE DE-DUP SURFACE, derived from the tables above
# so it cannot drift from them. This script may delete a duplicate registration
# only of a script it REGISTERS. bash-guard is the case that makes the
# distinction real: its interpreter is managed here, its registration is not, so
# a doubled bash-guard entry came from something else and is not this script's to
# remove. It is reported instead — see the end of the de-dup pass.
REGISTERED_SCRIPTS = frozenset(
    hook_script_of(c)
    for c in POST_BASH_CMDS + PRE_BASH_CMDS + [NOTIFY_CMD, LEDGER_CMD, WRITEBACK_CMD,
                                               HANDOFF_GUARD_CMD]
    + [c for cmds in SINGLE_EVENT_CMDS.values() for c in cmds]
)

# 🔴 A None in there would mean a table command the recogniser cannot read back —
# the unbounded re-append defect — reaching the ownership boundary as a wildcard
# that matches every unrecognised entry. `with_python`/`with_bash` each raise on
# their own output, so this is a second, cheaper net across the COMBINED set.
assert None not in REGISTERED_SCRIPTS, (
    "a command table entry is unreadable by this script's own identity recogniser")

with open(SETTINGS) as f:
    data = json.load(f)

hooks = data.setdefault("hooks", {})
added = []
rewritten = []
deduped = []


def registered_scripts(event_arrays):
    """Which managed hook scripts an event's entry list already invokes.

    Keyed on `hook_script_of`, so a shell hook counts as registered exactly the
    way a python one does — see MANAGED_SHELL_HOOK_SCRIPTS for why identity and
    interpreter are two questions.

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
    found = {hook_script_of(h.get("command"))
             for entry in event_arrays for h in entry.get("hooks", [])}
    found.discard(None)
    return found


def _entry_hook_dicts(entry):
    """The hook dicts inside one settings.json entry, skipping anything malformed."""
    if not isinstance(entry, dict):
        return []
    hs = entry.get("hooks")
    if not isinstance(hs, list):
        return []
    return [h for h in hs if isinstance(h, dict)]


# --------------------------------------------------------------------------- #
# WHICH EVENTS HAVE A `matcher` AT ALL — an explicit, enumerated ledger.
#
# From the Claude Code hooks documentation: the events in NO_MATCHER_EVENTS have
# NO matcher support and "always fire on every occurrence". The ones in
# MATCHER_EVENTS are narrowed by their `matcher` key — SessionStart's selects the
# SOURCE (startup/resume/clear/compact/fork) rather than a tool name, but it is a
# real scope either way, so it stays part of the de-dup identity.
#
# 🔴 An ENUMERATION, deliberately not a heuristic on the event name. An event in
# NEITHER set is treated as matcher-supporting: that is the conservative default,
# because keeping the matcher in the identity can only ever make this script
# DECLINE to delete something. That default is not merely asserted here — it is
# driven behaviourally by scenario 17 of tests/test_register_nudge_hook.py, on an
# event name the docs have not shipped, and both sets are pinned by EXACT EQUALITY
# (not a subset) in tests/test_registrar_activation.py. Between them, flipping the
# default's direction or moving one event across is red. Before that, moving an
# event into NO_MATCHER_EVENTS was a one-token, deletion-free change that turned
# the default destructive with the whole suite green.
#
# 🔴 BOTH SETS ARE COMPLETE AGAINST THE DOCUMENTED EVENT LIST as of 2026-08-20,
# re-read from code.claude.com/docs/en/hooks rather than remembered. The 14 events
# added to MATCHER_EVENTS in that pass were ALL already handled correctly by the
# unknown-event default, so completing the ledger changed NO behaviour — it moved
# them from an untested default to a decision somebody made. DirectoryAdded is the
# one worth naming: it looks like a fire-and-forget event and it is NOT — the docs
# give it a matcher over how the directory was added (slash_command /
# register_repo_root), so filing it under NO_MATCHER_EVENTS would license deleting
# a genuinely distinct registration.
#
# 🔴 AND THE HONEST LIMIT OF THE CLAIM: the docs do not say what Claude Code does
# with a `matcher` key that is present on a non-matcher event, so nothing here
# asserts it is "ignored". It does not need to. The event fires on every
# occurrence, so two entries for one script on such an event BOTH run whatever
# their matchers say — that, and not a claim about how the key is parsed, is why
# the matcher is dropped from the identity below.
#
# Both sets are pinned by tests/test_registrar_activation.py: they must be
# disjoint, and every event this script's own tables register on must appear in
# one of them — otherwise the identity for one of THIS script's hooks would fall
# through to the unknown-event default without anybody deciding that.
# --------------------------------------------------------------------------- #
NO_MATCHER_EVENTS = frozenset({
    "UserPromptSubmit", "PostToolBatch", "Stop", "TeammateIdle", "TaskCreated",
    "TaskCompleted", "WorktreeCreate", "WorktreeRemove", "CwdChanged",
    "MessageDisplay",
})

MATCHER_EVENTS = frozenset({
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest",
    "PermissionDenied", "SessionStart", "SessionEnd", "Setup",
    "SubagentStart", "SubagentStop", "Notification", "PreCompact",
    "PostCompact", "ConfigChange", "DirectoryAdded", "FileChanged",
    "StopFailure", "InstructionsLoaded", "UserPromptExpansion", "Elicitation",
    "ElicitationResult",
})


def event_has_matchers(event):
    """Does a `matcher` key narrow anything on this event? Unknown events: YES."""
    return event not in NO_MATCHER_EVENTS


def stray_matcher_value(entry, event):
    """A non-empty `matcher` sitting on an event that has none — or None.

    An empty string is NOT reported: `""` narrows nothing on any event, so
    flagging it here would blame the event for something that is not
    event-specific. A named matcher (`"Bash"`) is the misunderstanding worth
    hearing about — somebody believed they had scoped a Stop hook.
    """
    if event_has_matchers(event) or not isinstance(entry, dict):
        return None
    matcher = entry.get("matcher")
    return None if matcher in (None, "") else matcher


def entry_identity_list(entry, event):
    """The (scope, script) pairs this entry registers — one per hook, in order.

    🔴 THE IDENTITY KEY FOR THE DE-DUP SURFACE, and every part of it is
    load-bearing:

      * the SCRIPT, not the command string — the same identity `registered_scripts`
        uses for the append surface, so the two cannot disagree about what "already
        registered" means. It also catches the cross-spelling duplicate (`~/…` and
        `$HOME/…` naming the same hook) that a string key would miss.
      * the SCOPE, which is the entry's `matcher` ONLY on an event that HAS
        matchers. There, two entries for one script under DIFFERENT matchers are
        not duplicates: a hand-scoped narrow entry is a real configuration this
        script deliberately leaves in place (see `registered_scripts`), and
        collapsing it into the unmatchered one would silently widen a scope the
        operator narrowed. On a NO_MATCHER event there is no scope to narrow — the
        event fires on every occurrence — so the identity is the script alone and
        two such entries are a real double-fire this script may heal.

    A missing `matcher` and an explicit null are the same identity on every event:
    both mean "every tool", so two such entries really are one registration twice.

    Returns a LIST, not a set, so a caller counting OCCURRENCES sees an entry that
    lists the same hook twice inside its own `hooks` array. `entry_identities`
    collapses it for the callers that want membership.
    """
    if event_has_matchers(event):
        scope = entry.get("matcher") if isinstance(entry, dict) else None
        if scope is not None and not isinstance(scope, str):
            # Not a shape this script writes. Keep it hashable and keep it
            # distinct from both "absent" and every string, so ON THIS BRANCH it
            # cannot collapse into another entry's identity and license a
            # deletion.
            #
            # 🔴 THAT PROTECTION IS BRANCH-LOCAL, NOT UNCONDITIONAL — this whole
            # arm is inside `if event_has_matchers(event)`. On a NO_MATCHER event
            # the `else` below drops the scope to None for EVERY entry, so a
            # list-valued matcher DOES collapse into an unmatchered copy of the
            # same script and the later one is deleted. Measured, and correct:
            # such an event fires on every occurrence, so the two entries really
            # are one registration twice however the key is spelled. The claim
            # this branch makes is only "a matcher this script cannot read is
            # never mistaken for a matcher it can" — read as blanket protection
            # it would be false, which is why it says where it applies.
            scope = ("<non-str matcher>", repr(scope))
    else:
        scope = None
    ids = [(scope, hook_script_of(h.get("command")))
           for h in _entry_hook_dicts(entry)]
    return [i for i in ids if i[1] is not None]


def entry_identities(entry, event):
    """`entry_identity_list` as a set — membership, for the de-dup decision."""
    return set(entry_identity_list(entry, event))


def removal_scope_note(entry, event):
    """How a REMOVED entry was scoped, for the report line.

    🔴 The command alone does not identify which entry went. Two entries for one
    script differing ONLY by `matcher` both print the same command, so a report
    keyed on the command emits byte-identical lines and the operator cannot tell
    which of their registrations this script deleted — nor that it deleted two.

    The note states the matcher AS FOUND (absent and `null` are distinguished
    from `""`, which is a different thing somebody typed), and on a NO_MATCHER
    event it also says the key had nothing to narrow — the reason the entry was
    a duplicate at all rather than a scope.
    """
    if isinstance(entry, dict) and "matcher" in entry:
        found = "matcher %r" % (entry["matcher"],)
    else:
        found = "no matcher"
    if event_has_matchers(event):
        return "  (%s)" % found
    return "  (%s; %s has none to narrow)" % (found, event)


def removable_duplicate(entry):
    """Is this entry one THIS script wrote, and therefore one it may delete?

    Exactly the shape the append pass emits and nothing else: `{"hooks": [h]}` or
    `{"matcher": M, "hooks": [h]}` with h exactly `{"type": "command", "command": …}`
    and the command carrying no arguments. Any extra key — an entry-level
    `description`, a hook-level `timeout`, a second hook in the list, an argument
    after the script path — means somebody configured something this script never
    wrote, so it is kept even when it duplicates. A duplicate is an annoyance; a
    deleted foreign entry is a broken host.

    A `matcher` sitting on a NO_MATCHER event passes this shape check, and should:
    the key narrows nothing there, so the entry is a plain duplicate of the
    unmatchered one rather than a scope somebody chose. The stray-matcher warning
    below names that key whenever such an entry SURVIVES the pass.
    """
    if not isinstance(entry, dict) or set(entry) - {"matcher", "hooks"}:
        return False
    hs = entry.get("hooks")
    if not isinstance(hs, list) or len(hs) != 1 or not isinstance(hs[0], dict):
        return False
    h = hs[0]
    if set(h) != {"type", "command"} or h.get("type") != "command":
        return False
    if not bare_managed_command(h.get("command")):
        return False
    return hook_script_of(h.get("command")) in REGISTERED_SCRIPTS


# --- PASS 1: heal a file an OLDER registrar double-registered ----------------
# 🔴 Runs FIRST, before the rewrite, so the report cannot claim to have re-pinned
# an entry it is about to delete. Pinned behaviourally by scenario 15 of
# tests/test_register_nudge_hook.py: a run that both removes a duplicate and pins
# a survivor must name each in exactly one of the two report blocks. Swap these
# two passes and the removed entry appears in BOTH.
#
# Keeping the FIRST occurrence is what preserves position, matcher and every
# foreign key: the survivor is untouched and only later copies go. `seen` is fed
# by EVERY entry, including ones this script may not delete, so a foreign-shaped
# first copy still suppresses a plain second one rather than the other way round.
# See the module docstring for how a file gets into this state (a home-manager
# rollback runs the previous registrar, which cannot see the new spelling).
for _event in list(hooks):
    _arr = hooks.get(_event)
    if not isinstance(_arr, list):
        continue
    _seen = set()
    _kept = []
    for _entry in _arr:
        _ids = entry_identities(_entry, _event)
        if _ids and _ids <= _seen and removable_duplicate(_entry):
            deduped.append("%s: %s%s" % (_event, _entry["hooks"][0]["command"],
                                         removal_scope_note(_entry, _event)))
            continue
        _seen |= _ids
        _kept.append(_entry)
    if len(_kept) != len(_arr):
        hooks[_event] = _kept
    # 🔴 AND SAY WHAT IT DECLINED TO REMOVE. A MANAGED hook script still registered
    # more than once under one scope after this pass is a real double-fire that this
    # script would not touch — a foreign key, an argument after the script path, or a
    # script whose registration it does not own. Leaving that silent is the same
    # failure mode as the unpinnable commands reported below.
    #
    # Counted by OCCURRENCE, not by set membership, so an entry that lists the same
    # hook twice inside its own `hooks` array is counted twice — it fires twice.
    # A doubled FOREIGN command is out of scope and the module docstring says so:
    # `entry_identity_list` drops what the recogniser cannot read.
    _counts = {}
    for _entry in _kept:
        for _id in entry_identity_list(_entry, _event):
            _counts[_id] = _counts.get(_id, 0) + 1
    for _id in sorted(_counts, key=lambda i: (i[1], repr(i[0]))):
        if _counts[_id] > 1:
            _scope = (" under matcher %r" % (_id[0],) if event_has_matchers(_event)
                      else ", and %s has no matcher support — every copy fires on "
                           "every occurrence" % _event)
            warn("%s: %s is registered %d times%s. This script "
                 "removed only the duplicates it wrote itself; the rest carry keys, "
                 "arguments or a registration it does not own, so they were left in "
                 "place and the hook fires %d times per event. Remove the extras by "
                 "hand." % (_event, _id[1], _counts[_id], _scope, _counts[_id]))
    # 🔴 AND SAY SO ABOUT A `matcher` THAT CANNOT NARROW ANYTHING. On a NO_MATCHER
    # event the key is a configuration error: somebody believed they had scoped the
    # hook, and the hook runs every time regardless. Reported over the SURVIVORS, so
    # a stray matcher this pass has just deleted as a duplicate is not complained
    # about. Foreign commands included — this is a fact about the EVENT, not about
    # which script the entry invokes.
    for _entry in _kept:
        _stray = stray_matcher_value(_entry, _event)
        if _stray is None:
            continue
        warn("%s: an entry carries matcher %r, but %s has no matcher support and "
             "always fires on every occurrence — the key narrows nothing and this "
             "hook runs every time. Remove the key, or move the entry to an event "
             "that has matchers. Command(s): %s"
             % (_event, _stray, _event,
                ", ".join(repr(_h.get("command"))
                          for _h in _entry_hook_dicts(_entry))))

# --- PASS 2: normalise the interpreter of every managed hook, on every event --
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
                if managed_script_of(_new) is None:
                    # Same invariant guard as `with_python`, on the other write
                    # path: a rewrite whose product this script cannot read back
                    # would be re-appended on every subsequent run.
                    raise AssertionError(
                        "the interpreter rewrite produced a command this script's "
                        "own recogniser does not recognise: %r" % _new)
                _h["command"] = _new
                rewritten.append("%s: %s" % (_event, _new))

# --- PASS 2b: SAY SO about the managed hooks this script declined to pin ------
# 🔴 Conservative matching is correct; conservative SILENCE is what let the 127
# window run for months. A command naming a managed hook in any shape the
# recogniser refuses (`python3 -u …`, a leading `PYTHONPATH=…`, `${HOME}/…`, a
# quoted path, a `…; …` compound) is left byte-identical AND still resolves its
# interpreter from PATH, so it keeps dying mid-switch. Warn, never rewrite: this
# script cannot prove it owns those, and guessing is how a foreign hook gets
# mangled. Emitted whether or not anything else changed, so the "no change" run
# reports it too.
for _event in sorted(hooks):
    _arr = hooks.get(_event)
    if not isinstance(_arr, list):
        continue
    for _entry in _arr:
        for _h in _entry_hook_dicts(_entry):
            _cmd = _h.get("command")
            if not isinstance(_cmd, str) or managed_script_of(_cmd) is not None:
                continue
            _named = sorted(s for s in MANAGED_HOOK_SCRIPTS if s in _cmd)
            if not _named:
                continue
            warn("%s: this command names the managed hook %s but is not in the form "
                 "this script can pin (<absolute-python> <hooks-dir>%s), so its "
                 "interpreter is still resolved from PATH and it dies with `command "
                 "not found` during the ~1s of every home-manager switch in which the "
                 "intermediate profile generation has no python3 on it. Left "
                 "byte-identical; rewrite it to that form by hand. The command: %r"
                 % (_event, ", ".join(_named), _named[0], _cmd))

# --- PreToolUse(Bash) gates (append-only, matcher=Bash) ----------------------
# 🔴 APPENDED, so it runs AFTER whatever the host already had on PreToolUse —
# bash-guard included. Both can deny; ordering only decides which reason the
# operator reads first, and bash-guard guards irreversible actions while this one
# guards a task's specification. The irreversible verdict should be the one that
# lands, so it goes first, which is what appending gives us for free.
pre = hooks.setdefault("PreToolUse", [])
pre_registered = registered_scripts(pre)
for cmd in PRE_BASH_CMDS:
    if hook_script_of(cmd) in pre_registered:
        continue
    pre.append({"matcher": "Bash", "hooks": [{"type": "command", "command": cmd}]})
    added.append("PreToolUse(Bash): " + cmd)

# --- PostToolUse(Bash) nudge hooks (append-only, matcher=Bash) ---------------
post = hooks.setdefault("PostToolUse", [])
post_registered = registered_scripts(post)
for cmd in POST_BASH_CMDS:
    if hook_script_of(cmd) in post_registered:
        continue
    post.append({"matcher": "Bash", "hooks": [{"type": "command", "command": cmd}]})
    added.append("PostToolUse(Bash): " + cmd)

# --- claude-notify on the three turn-boundary events (append-only) -----------
# Preserve every existing entry on each event array (e.g. a clawgate-stop-hook or
# a tmux Stop hook) — only add claude-notify where it's missing.
for event in NOTIFY_EVENTS:
    arr = hooks.setdefault(event, [])
    if hook_script_of(NOTIFY_CMD) in registered_scripts(arr):
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
    if hook_script_of(LEDGER_CMD) in registered_scripts(arr):
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
    if hook_script_of(WRITEBACK_CMD) in registered_scripts(arr):
        continue
    arr.append({"hooks": [{"type": "command", "command": WRITEBACK_CMD}]})
    added.append("%s: %s" % (event, WRITEBACK_CMD))

# --- the handoff write guard (append-only, same discipline) ------------------
# 🔴 THE SECOND **Stop** ENTRY HERE THAT CAN BLOCK — the fourth blocking hook this
# script registers overall, the other two being PreToolUse gates — and it is
# appended AFTER the clawgate one so the order of the two Stop blockers is a
# decision rather than an accident of table position. Each
# caps itself independently — two consecutive blocks per task and per doc — so a
# session holding both a clawgate card and a resumed handoff can in principle stack
# toward the CLI's cap of 8, at which point the CLI ends the turn with a warning. A
# graceful ceiling, named rather than hidden; the same interaction the write-back
# guard's own docstring records for MAX_TASKS.
for event in HANDOFF_GUARD_EVENTS:
    arr = hooks.setdefault(event, [])
    if hook_script_of(HANDOFF_GUARD_CMD) in registered_scripts(arr):
        continue
    arr.append({"hooks": [{"type": "command", "command": HANDOFF_GUARD_CMD}]})
    added.append("%s: %s" % (event, HANDOFF_GUARD_CMD))

# --- single-event hooks (append-only, same discipline) -----------------------
for event, cmds in SINGLE_EVENT_CMDS.items():
    arr = hooks.setdefault(event, [])
    present = registered_scripts(arr)
    for cmd in cmds:
        if hook_script_of(cmd) in present:
            continue
        arr.append({"hooks": [{"type": "command", "command": cmd}]})
        added.append("%s: %s" % (event, cmd))

# 🔴 Only write when something actually moved. The rewrite pass now re-runs on
# every switch, so an unconditional write would re-dump (indent=2) the operator's
# per-host settings.json — permissions and all — every time, for nothing.
if not added and not rewritten and not deduped:
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
if deduped:
    # 🔴 The spelling matters here. The de-dup pass runs BEFORE the interpreter
    # rewrite, so a removed entry is reported with the command it was FOUND with —
    # typically the bare `python3 …` an older registrar wrote. Printed above a
    # "pinned hook interpreters to /nix/store/…" block, that reads as if the pinning
    # had missed one. Say which it is instead of leaving the reader to guess.
    # 🔴 ...and the clause only says "below" when a pinning block actually
    # follows. `rewritten` is what builds that block, so pointing at it
    # unconditionally sends the reader looking for a section that is not there
    # whenever every command was already pinned — the commonest healing run once
    # both hosts have been through one switch.
    print("removed duplicate hook registrations (shown as they were FOUND — %s):"
          % ("de-dup runs before the pinning below" if rewritten else
             "de-dup runs before the interpreter pinning, and nothing needed "
             "pinning this run"))
    for c in deduped:
        print("  -", c)
if rewritten:
    print("pinned hook interpreters to %s:" % PYTHON)
    for c in rewritten:
        print("  ~", c)
if added:
    print("registered hooks:")
    for c in added:
        print("  +", c)
