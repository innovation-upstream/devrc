#!/usr/bin/env python3
"""Tests for the DELIVERY seam: register-hooks-activation.sh + its home.nix wiring.

WHAT THIS FILE IS FOR

  🔴 The bug this closes was invisible because the DEPLOY SUCCEEDED. next-step-nudge.py
  (#452) landed on both hosts, `home-manager switch` reported success, the file was
  present — and the hook did nothing, because `~/.claude/settings.json` never named it
  and nothing was ever going to make it. A test asserting "the activation step ran"
  would have passed throughout that bug's entire life. So every test here asserts the
  END STATE IN settings.json, or the wiring that produces it.

  The seam has three parts and each is owned by a section below:

    1. END STATE — drive the shipped wrapper against a throwaway HOME and read the
       resulting settings.json. Both directions: a file without the nudge gets it, a
       file that already has it comes back BYTE-IDENTICAL.
    2. NEVER FAIL THE SWITCH — activation runs under `set -eu -o pipefail`, so a
       non-zero status here aborts the whole switch and blocks every other change on
       that host. Four distinct broken environments, each asserted to exit 0, warn on
       stderr, and leave settings.json exactly as found.
    3. WIRING — home.nix must deploy the registrar AND every hook the registrar
       registers, and must run the wrapper AFTER the files land. Section 1 passes
       perfectly with home.nix untouched; that combination is precisely the shipped
       bug, which is why section 3 exists.

  🔴 NEVER touches the operator's real ~/.claude/settings.json: every test runs the
  wrapper with HOME pointed at a tmp_path, and the registrar resolves the file from
  HOME. Both hosts are registered correctly right now and must stay that way.

  Not tested here, deliberately: a chmod-000 settings.json. Whether it is readable
  depends on the euid of whoever runs the gate (root ignores the mode), so it would
  assert one thing on a dev host and another in a build sandbox. The
  directory-at-the-settings-path fixture is the uid-independent "cannot be read".

    run:  python -m pytest scripts/claude-hooks/tests/test_registrar_activation.py -q

Fixtures are synthetic. This repo is public: no real paths, hostnames or task titles.
"""
import ast
import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "claude-hooks" / "register-hooks-activation.sh"
REGISTRAR = ROOT / "scripts" / "claude-hooks" / "register-nudge-hook.py"
HOME_NIX = ROOT / "nix" / "home.nix"

BASH = shutil.which("bash") or "/bin/bash"

# The interpreter the registrant writes into every managed hook command. Pinned
# to a synthetic path via $DEVRC_HOOK_PYTHON so the expectations below do not
# depend on whichever interpreter happens to run pytest; the UNPINNED resolution
# is covered behaviourally by its own test at the bottom of section 1.
#
# 🔴 It must be a REAL, executable file. $DEVRC_HOOK_PYTHON is a test seam with
# PRODUCTION REACH — activation inherits the operator's environment — so the
# registrant validates it (absolute / exists / executable / one `python*` token)
# and falls back to its own sys.executable with a warning when it is not. The
# file is created under a private temp dir, never at a path that exists on this
# host, so nothing here can pass by accident of the environment.
_FAKE_STORE = tempfile.mkdtemp(prefix="devrc-fake-store-")
atexit.register(shutil.rmtree, _FAKE_STORE, True)


def fake_python(store_name):
    """A stand-in for a /nix/store python: real file, executable, `python*` name.

    The registrant only stat()s it (`isfile` + `access(X_OK)`); nothing execs it,
    so it deliberately carries NO shebang — a runtime-written executable stub is
    what `scripts/tests/test_runtime_shebangs.py` gates repo-wide.
    """
    p = Path(_FAKE_STORE) / store_name / "bin" / "python3.12"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("stand-in for an interpreter binary: stat()ed, never executed\n")
    p.chmod(0o755)
    return str(p)


HOOK_PY = fake_python("0000000000000000000000000000000-python3-3.12.14")

NEXT_STEP = HOOK_PY + " ~/.claude/hooks/next-step-nudge.py"
NOTIFY = HOOK_PY + " ~/.claude/hooks/claude-notify.py"
LEDGER = HOOK_PY + " ~/.claude/hooks/agent-ledger-hook.py"
WRITEBACK = HOOK_PY + " ~/.claude/hooks/clawgate-writeback-guard.py"
INTERVIEW = HOOK_PY + " ~/.claude/hooks/clawgate-task-interview-guard.py"
BASH_GUARD = HOOK_PY + " ~/.claude/hooks/bash-guard.py"
BG_CAPTURE = HOOK_PY + " ~/.claude/hooks/bg-command-capture.py"
GH_ISSUE = HOOK_PY + " ~/.claude/hooks/gh-issue-closing-condition-guard.py"
CLAWGATE_STOP = "/home/zach/.claude/clawgate-stop-hook.sh"
TMUX_STOP = "~/.config/tmux/task-hook.sh"

# The workbench's real shape before the manual fix: THREE foreign owners of Stop, one
# of which (the clawgate hook) drives remote approval. An append-only bug is invisible
# on an empty array, so the preservation fixtures are all populated.
THREE_FOREIGN_STOP_HOOKS = [TMUX_STOP, CLAWGATE_STOP, NOTIFY]


def settings_with(stop_commands, extra=None):
    data = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": "python3 ~/.claude/hooks/bash-guard.py"}
                    ],
                }
            ],
            "Stop": [
                {"hooks": [{"type": "command", "command": c}]} for c in stop_commands
            ],
        },
        "permissions": {"allow": ["Bash(git status)"]},
    }
    if extra:
        data.update(extra)
    return data


def make_home(tmp_path, settings, name="home"):
    """A throwaway HOME with ~/.claude/settings.json holding `settings`.

    `settings` may be a dict (written as JSON) or raw text (for the malformed case);
    None means "no settings.json at all".
    """
    home = tmp_path / name
    (home / ".claude").mkdir(parents=True)
    path = home / ".claude" / "settings.json"
    if isinstance(settings, dict):
        path.write_text(json.dumps(settings, indent=2) + "\n")
    elif settings is not None:
        path.write_text(settings)
    return home


def run_activation(home, registrar=REGISTRAR, python=None, args=None,
                   hook_python=HOOK_PY):
    """Drive the SHIPPED wrapper exactly as home.nix drives it.

    `hook_python=None` drops the $DEVRC_HOOK_PYTHON override so the registrant
    resolves the interpreter the way it does in production.
    """
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("DEVRC_HOOK_PYTHON", None)
    if hook_python is not None:
        env["DEVRC_HOOK_PYTHON"] = hook_python
    if args is None:
        args = [str(registrar), python or sys.executable]
    return subprocess.run(
        [BASH, str(SCRIPT)] + args, env=env, capture_output=True, text=True
    )


def stop_commands(home):
    data = json.loads((home / ".claude" / "settings.json").read_text())
    return [
        h.get("command")
        for entry in data.get("hooks", {}).get("Stop", [])
        for h in entry.get("hooks", [])
    ]


def settings_bytes(home):
    return (home / ".claude" / "settings.json").read_bytes()


# --------------------------------------------------------------------------- #
# 1. END STATE in settings.json — the assertion the shipped bug would have failed
# --------------------------------------------------------------------------- #


def test_a_settings_file_without_the_nudge_comes_back_with_it_registered(tmp_path):
    home = make_home(tmp_path, settings_with([TMUX_STOP]))
    assert NEXT_STEP not in stop_commands(home), "fixture must start WITHOUT the nudge"

    p = run_activation(home)

    assert p.returncode == 0, p.stderr
    assert NEXT_STEP in stop_commands(home), (
        "activation ran but settings.json does not name the nudge — this is the exact "
        "shipped bug: the step succeeded and the feature stayed inert.\n" + p.stdout + p.stderr
    )


def test_it_says_what_it_did_on_stdout(tmp_path):
    """A silent activation step is how the original failure went unnoticed."""
    home = make_home(tmp_path, settings_with([TMUX_STOP]))

    p = run_activation(home)

    assert p.returncode == 0
    assert "claude-hooks:" in p.stdout, p.stdout + p.stderr
    assert "registered hooks" in p.stdout, p.stdout
    assert NEXT_STEP in p.stdout, p.stdout


def test_three_foreign_stop_hooks_come_back_as_six_originals_intact_and_in_order(tmp_path):
    """🔴 Losing the clawgate Stop hook would silently break remote approval."""
    home = make_home(tmp_path, settings_with(THREE_FOREIGN_STOP_HOOKS))

    p = run_activation(home)
    after = stop_commands(home)

    assert p.returncode == 0, p.stderr
    # SET equality, not membership: it fails when the set SHRINKS (one clobbered) and
    # when it GROWS (something registered by accident), which membership cannot see.
    assert set(after) == set(THREE_FOREIGN_STOP_HOOKS) | {NEXT_STEP, LEDGER,
                                                        WRITEBACK}, after
    assert len(after) == 6, after
    # The three originals keep their identity AND their relative order, and stay ahead
    # of the appended ones — append-only, never a rewrite.
    assert after[:3] == THREE_FOREIGN_STOP_HOOKS, after
    # 🔴 The three appended hooks in the order the registrant adds them. Order is
    # load-bearing for next-step-nudge (it returns additionalContext and should
    # run after the notifiers have observed the turn) and irrelevant to the
    # ledger, which neither blocks nor prints — but it is pinned so a reordering
    # is a decision someone makes rather than a diff nobody reads. The write-back
    # guard is the only one here that can BLOCK, and it likewise lands after every
    # foreign owner has already seen the turn.
    assert after[3:] == [LEDGER, WRITEBACK, NEXT_STEP], after


def test_unrelated_settings_content_is_untouched(tmp_path):
    home = make_home(tmp_path, settings_with(THREE_FOREIGN_STOP_HOOKS))

    p = run_activation(home)

    # 🔴 Assert the run WORKED first. Without this line the test passes vacuously on a
    # tree where the wrapper does not exist at all — nothing is written, so nothing is
    # disturbed — which is exactly the tree this whole PR exists to leave behind.
    assert p.returncode == 0 and NEXT_STEP in stop_commands(home), p.stderr
    data = json.loads(settings_bytes(home))
    assert data["permissions"] == {"allow": ["Bash(git status)"]}
    pre = [
        h.get("command")
        for e in data["hooks"]["PreToolUse"]
        for h in e.get("hooks", [])
    ]
    # 🔴 bash-guard's INTERPRETER is normalised (a bare `python3` here dies with
    # 127 mid-switch, and a PreToolUse hook exiting 127 fails OPEN) while its
    # REGISTRATION is left exactly as it was — still ONE entry, still first, never
    # created and never duplicated. Two surfaces, two widths; see the registrant's
    # docstring. The other entries are ones the registrant DOES own: the clawgate
    # task interview guard, and the backgrounded-command capture log (868ktvqf9),
    # which is INSTRUMENTATION — it emits no permissionDecision and cannot refuse a
    # command, so its presence here widens what PreToolUse OBSERVES and not what it
    # can block. Asserted as a whole-list equality, IN ORDER, so an unexpected entry
    # or a doubled bash-guard fails it — the strictness is the point, and is why
    # adding a PreToolUse hook has to come through this line.
    #
    # 2026-08-25: GH_ISSUE joins them — the fourth entry and the THIRD that can
    # refuse, denying a `gh issue create` whose body names no closing condition. It
    # came through this line, as intended.
    assert pre == [BASH_GUARD, INTERVIEW, BG_CAPTURE, GH_ISSUE], pre


def test_a_settings_file_that_already_has_the_nudge_is_left_byte_identical(tmp_path):
    """The other direction: re-running must register nothing and rewrite nothing."""
    home = make_home(tmp_path, settings_with(THREE_FOREIGN_STOP_HOOKS))

    first = run_activation(home)
    assert first.returncode == 0, first.stderr
    registered = settings_bytes(home)

    second = run_activation(home)

    assert second.returncode == 0, second.stderr
    assert settings_bytes(home) == registered, "second run rewrote settings.json"
    assert "no change" in second.stdout, second.stdout
    assert stop_commands(home).count(NEXT_STEP) == 1, stop_commands(home)


def test_no_temp_files_are_left_beside_settings_json(tmp_path):
    home = make_home(tmp_path, settings_with([TMUX_STOP]))

    p = run_activation(home)

    # Same anti-vacuity line as above: a run that wrote nothing leaves no litter.
    assert p.returncode == 0 and NEXT_STEP in stop_commands(home), p.stderr
    leftovers = [n for n in os.listdir(home / ".claude") if n != "settings.json"]
    assert leftovers == [], leftovers


def test_a_pre_migration_settings_file_has_every_interpreter_pinned(tmp_path):
    """🔴 THE 127 WINDOW, at the seam that actually runs on every switch.

    `home-manager switch` updates ~/.nix-profile as remove-then-install, so for
    ~1s the live generation is a partial closure with NO python3 on it (measured
    on this host: 337 binaries against 625 in the final one). A hook registered
    as a bare `python3 …` firing in that window dies with
    `python3: command not found` — and bash-guard is a PreToolUse hook whose 127
    is classified NON-BLOCKING, so the guard fails OPEN exactly there.

    The fixture is the shape both hosts were in before this change.
    """
    home = make_home(
        tmp_path,
        settings_with([TMUX_STOP, "python3 ~/.claude/hooks/claude-notify.py"]),
    )

    p = run_activation(home)

    assert p.returncode == 0, p.stderr
    data = json.loads(settings_bytes(home))
    commands = [
        h.get("command")
        for arr in data["hooks"].values()
        for e in arr
        for h in e.get("hooks", [])
    ]
    unpinned = [c for c in commands if c.startswith("python3 ")]
    assert unpinned == [], (
        "these hook commands still start with a bare `python3`, so they die with "
        "127 during the switch's profile window: " + repr(unpinned)
    )
    # Anti-vacuity: the file really does still carry hooks, and one of them is
    # the guard whose 127 fails open.
    assert BASH_GUARD in commands, commands
    assert HOOK_PY in p.stdout, p.stdout


def test_the_interpreter_is_resolved_through_the_blinking_symlink(tmp_path):
    """🔴 THE RESOLUTION ITSELF, with $DEVRC_HOOK_PYTHON deliberately UNSET.

    Every other test here pins the interpreter to a literal, which would leave
    `os.path.realpath(sys.executable)` — the whole mechanism — untested. Run by
    hand, the registrant's sys.executable is `~/.nix-profile/bin/python3`: a
    symlink that BLINKS during a switch, which is the bug. Writing that path
    would close nothing.

    Launching through a symlink reproduces it hermetically: CPython leaves
    sys.executable as the path it was invoked by, so a registrant that skipped
    the realpath would write the symlink and this goes red.
    """
    blink = tmp_path / "blinking-profile" / "bin"
    blink.mkdir(parents=True)
    link = blink / "python3"
    link.symlink_to(sys.executable)
    resolved = os.path.realpath(link)
    assert resolved != str(link), "fixture must not resolve to itself"

    home = make_home(tmp_path, settings_with([TMUX_STOP]))
    p = run_activation(home, python=str(link), hook_python=None)

    assert p.returncode == 0, p.stderr
    written = stop_commands(home)
    nudges = [c for c in written if c.endswith("/next-step-nudge.py")]
    assert len(nudges) == 1, written
    interpreter = nudges[0].split(" ", 1)[0]
    assert interpreter == resolved, (
        "the registrant wrote %r; it must write the REALPATH %r, never the "
        "symlink it was invoked through" % (interpreter, resolved)
    )
    assert os.path.isabs(interpreter), interpreter
    assert str(link) not in nudges[0], nudges[0]


# --------------------------------------------------------------------------- #
# 2. NEVER FAIL THE SWITCH — activation runs under `set -eu -o pipefail`
# --------------------------------------------------------------------------- #


def assert_warned_and_survived(p, message):
    assert p.returncode == 0, (
        message + " — a non-zero status here ABORTS home-manager switch.\n"
        "stdout: " + p.stdout + "\nstderr: " + p.stderr
    )
    assert "WARNING" in p.stderr, (
        message + " — it exited 0 but said nothing; a silent failure is the shape of "
        "the original bug.\nstdout: " + p.stdout + "\nstderr: " + p.stderr
    )


def test_malformed_settings_json_warns_and_lets_the_switch_finish(tmp_path):
    home = make_home(tmp_path, '{"hooks": {"Stop": [ this is not json')
    before = settings_bytes(home)

    p = run_activation(home)

    assert_warned_and_survived(p, "malformed settings.json")
    assert settings_bytes(home) == before, "a malformed file must not be rewritten"


def test_an_unreadable_settings_json_warns_and_lets_the_switch_finish(tmp_path):
    """Unreadable in a uid-independent way: a DIRECTORY where the file should be."""
    home = tmp_path / "home"
    (home / ".claude" / "settings.json").mkdir(parents=True)

    p = run_activation(home)

    assert_warned_and_survived(p, "unreadable settings.json")
    assert (home / ".claude" / "settings.json").is_dir()


def test_a_missing_settings_json_warns_and_lets_the_switch_finish(tmp_path):
    home = make_home(tmp_path, None)

    p = run_activation(home)

    assert_warned_and_survived(p, "missing settings.json")
    assert not (home / ".claude" / "settings.json").exists(), (
        "the wrapper must not conjure a settings.json — that file is per-host, "
        "unmanaged, and holds the permission allowlist"
    )


def test_a_missing_registrar_warns_naming_the_path_and_lets_the_switch_finish(tmp_path):
    """The residual failure mode: the registrar's own delivery breaks."""
    home = make_home(tmp_path, settings_with(THREE_FOREIGN_STOP_HOOKS))
    before = settings_bytes(home)

    p = run_activation(home, registrar=tmp_path / "nowhere" / "register-nudge-hook.py")

    assert_warned_and_survived(p, "missing registrar")
    assert "register-nudge-hook.py" in p.stderr, p.stderr
    assert settings_bytes(home) == before


def test_a_broken_interpreter_warns_and_lets_the_switch_finish(tmp_path):
    home = make_home(tmp_path, settings_with(THREE_FOREIGN_STOP_HOOKS))
    before = settings_bytes(home)

    p = run_activation(home, python=str(tmp_path / "no-such-python"))

    assert_warned_and_survived(p, "broken interpreter")
    assert settings_bytes(home) == before


def test_with_no_arguments_it_looks_for_the_registrar_under_home(tmp_path):
    """Pins the DEFAULT home.nix relies on, without depending on a python3 on PATH."""
    home = make_home(tmp_path, settings_with([TMUX_STOP]))

    p = run_activation(home, args=[])

    assert_warned_and_survived(p, "no arguments, no deployed registrar")
    assert str(home / ".claude" / "hooks" / "register-nudge-hook.py") in p.stderr, p.stderr


# --------------------------------------------------------------------------- #
# 3. WIRING — section 1 passes with home.nix untouched; that IS the shipped bug
# --------------------------------------------------------------------------- #

# 🔴 `.py|.sh`, not `.py`. This pattern IS the coverage of
# test_home_nix_deploys_every_hook_the_registrar_registers below, and while it
# was python-only that test's sentence ("every hook the registrar registers")
# was wider than its implementation: the first non-python registration would
# have been invisible to it and shipped undelivered, which is the exact bug the
# test exists to catch, walked past by the guard meant to stop it.
HOOK_CMD_RE = re.compile(r"~/\.claude/hooks/([A-Za-z0-9_.-]+\.(?:py|sh))")
HOME_FILE_RE = re.compile(r'home\.file\."\.claude/hooks/([^"]+)"')


def registrar_registers():
    """Hook filenames the registrar writes into settings.json (parsed, never imported —
    importing it would read the REAL ~/.claude/settings.json)."""
    src = REGISTRAR.read_text()
    # Only the command tables at the top, not the docstring's prose.
    body = src.split('"""', 2)[-1]
    return set(HOOK_CMD_RE.findall(body))


def home_nix_deploys():
    return set(HOME_FILE_RE.findall(HOME_NIX.read_text()))


def test_the_parsers_find_something_positive_control():
    """A zero from either parser would make the two tests below vacuously green."""
    registers = registrar_registers()
    deploys = home_nix_deploys()
    assert len(registers) >= 4, registers
    assert "next-step-nudge.py" in registers, registers
    assert len(deploys) >= 5, deploys
    assert "bash-guard.py" in deploys, deploys
    # The `.sh` half of HOOK_CMD_RE, controlled separately: a pattern that had
    # silently lost its shell alternation would still satisfy every assertion
    # above, and the two tests below would go back to being python-only while
    # still reading as "every hook".
    assert "base-clone-staleness.sh" in registers, registers


def test_home_nix_deploys_every_hook_the_registrar_registers():
    """The bug class, pinned as a RELATIONSHIP: registering a hook that no home.file
    delivers produces a settings.json entry pointing at a file that does not exist.

    ⚠ Labelled honestly: this is an INVARIANT GUARD, not regression coverage. It is
    GREEN at the base commit — the shipped bug was the registrar's own delivery, and
    the registrar does not appear in its own command tables. It is here because the
    NEXT hook lands the same way, and it was mutation-checked reachable (adding a
    command for an undeployed hook turns it red naming that file)."""
    missing = registrar_registers() - home_nix_deploys()
    assert missing == set(), (
        "these hooks are registered in ~/.claude/settings.json by "
        "register-nudge-hook.py but have no home.file entry in nix/home.nix, so they "
        "are named but never delivered: " + ", ".join(sorted(missing))
    )


def test_home_nix_deploys_the_registrar_itself():
    assert "register-nudge-hook.py" in home_nix_deploys(), (
        "register-nudge-hook.py has no home.file entry — it was mentioned only in "
        "comments for weeks, which is why nothing ever registered the hooks"
    )
    assert REGISTRAR.exists()


# --- the REWRITE surface's managed set, DERIVED and pinned two-way ----------- #
def registrar_literal(name):
    """A module-level literal out of the registrar, read with `ast` — never by
    importing it, because importing runs it against the operator's real
    ~/.claude/settings.json. `frozenset({...})` is unwrapped to its set literal."""
    tree = ast.parse(REGISTRAR.read_text(), filename=str(REGISTRAR))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            continue
        value = node.value
        if isinstance(value, ast.Call) and getattr(value.func, "id", "") == "frozenset":
            value = value.args[0]
        return ast.literal_eval(value)
    raise AssertionError("%s is not a module-level literal in %s" % (name, REGISTRAR))


def test_the_managed_hook_set_is_pinned_two_way_against_home_nix():
    """🔴 THE REWRITE SURFACE'S LEDGER — the set of scripts whose interpreter the
    registrant will rewrite, pinned against the `home.file.".claude/hooks/*.py"`
    entries it is derived from. Same idiom as drift-check.sh's nix/pkgs scan:
    a literal a human reviewed, checked against the tree it claims to describe.

    Fails when the set GROWS — a new hook lands in home.nix and nobody decides
    whether its interpreter is managed, which is how the 127 window reopens for
    exactly one hook — and when it SHRINKS.

    It is a literal in the registrant rather than a scan because the DEPLOYED
    registrant is a /nix/store copy with no repo checkout to read; the price of
    that is one line per new hook, and this test is what charges it.
    """
    deployed = {n for n in home_nix_deploys() if n.endswith(".py")}
    managed = set(registrar_literal("MANAGED_HOOK_SCRIPTS"))
    libraries = set(registrar_literal("HOOK_LIBRARY_MODULES"))
    registrar = registrar_literal("REGISTRAR_SCRIPT")

    # Positive control: a parser that found nothing would make this vacuous.
    assert len(deployed) >= 8, deployed
    assert "bash-guard.py" in deployed, deployed

    assert deployed == managed | libraries | {registrar}, (
        "nix/home.nix's .claude/hooks/*.py set no longer matches the registrant's "
        "ledger. Unaccounted (deployed, but neither managed nor explicitly "
        "excluded): %r. Stale (claimed, but not deployed): %r. A new hook must be "
        "added to MANAGED_HOOK_SCRIPTS — or, if it is a library module that is "
        "never invoked as a hook, to HOOK_LIBRARY_MODULES."
        % (sorted(deployed - (managed | libraries | {registrar})),
           sorted((managed | libraries | {registrar}) - deployed))
    )


def test_the_shell_hook_set_is_pinned_two_way_against_home_nix():
    """🔴 The SHELL ledger, pinned the same way its python sibling above is.

    Separate test rather than a widened one, because the two sets answer
    different questions and the sibling's `.py` filter is what keeps the rewrite
    surface honest: MANAGED_HOOK_SCRIPTS means "whose INTERPRETER this script
    rewrites", and a bash hook must never be in it — normalising that command's
    first token to a python path turns every session start into a SyntaxError.

    Fails when a `.claude/hooks/*.sh` entry lands in home.nix that nobody has
    decided about, and when the registrant's set names one home.nix does not
    deliver.
    """
    deployed = {n for n in home_nix_deploys() if n.endswith(".sh")}
    shell = set(registrar_literal("MANAGED_SHELL_HOOK_SCRIPTS"))

    # Positive control: both sides non-empty, so an equality of two empty sets
    # can never be what makes this green.
    assert deployed, "no .claude/hooks/*.sh home.file entry found in nix/home.nix"
    assert shell, "MANAGED_SHELL_HOOK_SCRIPTS is empty"

    assert deployed == shell, (
        "nix/home.nix's .claude/hooks/*.sh set no longer matches the registrant's "
        "shell ledger. Unaccounted (deployed, not managed): %r. Stale (claimed, "
        "not deployed): %r."
        % (sorted(deployed - shell), sorted(shell - deployed))
    )


def test_the_two_ledgers_are_disjoint_and_the_shell_one_is_never_rewritten():
    """🔴 THE SEAM, pinned as a relationship rather than as two component facts.

    A `.sh` name reaching MANAGED_HOOK_SCRIPTS would hand it to the interpreter
    rewrite; a `.py` name in the shell set would put a python hook behind a
    `bash` recogniser. Both sets can be individually well-formed and this can
    still be wrong, which is why it is asserted about the PAIR.
    """
    managed = set(registrar_literal("MANAGED_HOOK_SCRIPTS"))
    shell = set(registrar_literal("MANAGED_SHELL_HOOK_SCRIPTS"))

    assert managed and shell                                    # positive control
    assert managed & shell == set(), sorted(managed & shell)
    assert all(n.endswith(".py") for n in managed), sorted(managed)
    assert all(n.endswith(".sh") for n in shell), sorted(shell)


def test_the_two_exclusions_are_explicit_and_justified():
    """🔴 The exclusions must be a DECISION, not an accident of omission.

    A hook missing from MANAGED_HOOK_SCRIPTS and from every exclusion list would
    fail the pin above; these assertions add the other half — that the things
    excluded really are excluded for the reason claimed, and that no script is
    both managed and excluded.
    """
    managed = set(registrar_literal("MANAGED_HOOK_SCRIPTS"))
    libraries = set(registrar_literal("HOOK_LIBRARY_MODULES"))
    registrar = registrar_literal("REGISTRAR_SCRIPT")

    assert managed & libraries == set(), managed & libraries
    assert registrar not in managed, registrar
    assert registrar == REGISTRAR.name, registrar
    # A library module is one that nothing ever registers AS a hook — so none of
    # them may appear in the registrant's own command tables.
    assert libraries & registrar_registers() == set(), (
        "a module excluded as a never-invoked library IS in the command tables: "
        + repr(sorted(libraries & registrar_registers()))
    )
    # ...and every hook the registrant registers must be accounted for by ONE of
    # the two ledgers, or the append surface would write a command no recogniser
    # can read back — the unbounded re-append defect.
    #
    # 🔴 This used to read `<= managed` alone, and its stated reason ("its
    # interpreter would never be re-pinned") is why: while every hook was python,
    # "registered" and "interpreter-managed" were the same set. They are not the
    # same question, and a shell hook is the case that separates them — it has no
    # python token to re-pin, so demanding its presence in `managed` would demand
    # exactly the rewrite that breaks it. The exhaustiveness is unchanged: a hook
    # in NEITHER ledger still fails here.
    shell = set(registrar_literal("MANAGED_SHELL_HOOK_SCRIPTS"))
    unaccounted = registrar_registers() - (managed | shell)
    assert unaccounted == set(), (
        "registered but in neither ledger, so nothing maintains it and this "
        "script cannot read its own command back: " + repr(sorted(unaccounted))
    )
    # And the python ledger is still exhaustive over the PYTHON registrations —
    # the original claim, kept at its own width rather than dissolved into the
    # union above, which would pass if a .py hook drifted into the shell set.
    assert {n for n in registrar_registers() if n.endswith(".py")} <= managed, (
        "a python hook is registered but not interpreter-managed, so it would "
        "never be re-pinned: "
        + repr(sorted({n for n in registrar_registers() if n.endswith(".py")}
                      - managed))
    )


def activation_block():
    src = HOME_NIX.read_text()
    m = re.search(
        r"home\.activation\.registerClaudeHooks\s*=(.*?)'';", src, re.S
    )
    return m.group(1) if m else None


def test_home_nix_runs_the_wrapper_on_switch():
    block = activation_block()
    assert block is not None, (
        "no home.activation.registerClaudeHooks entry — the registrar would be "
        "delivered and never invoked, which is the state that shipped"
    )
    assert "register-hooks-activation.sh" in block, block
    assert SCRIPT.exists(), SCRIPT
    assert "register-nudge-hook.py" in block, block


def test_the_activation_entry_runs_after_the_hook_files_land():
    """🔴 linkGeneration is the step that creates the home-file symlinks, and it is
    itself entryAfter ["writeBoundary"] — so writeBoundary alone orders this entry
    BEFORE the files it depends on (measured in the generated activate: two
    writeBoundary-only entries at lines 290/300, linkGeneration at 502)."""
    block = activation_block()
    assert block is not None
    m = re.search(r"entryAfter\s*\[([^\]]*)\]", block)
    assert m is not None, block
    deps = set(re.findall(r'"([^"]+)"', m.group(1)))
    assert "linkGeneration" in deps, (
        "the registrar would run before ~/.claude/hooks/ is populated on a fresh "
        "host; declared dependencies: " + ", ".join(sorted(deps))
    )


# --- the EVENT-MATCHER LEDGER, pinned against the tables that depend on it --- #
def registrar_dict_keys(name):
    """The KEYS of a module-level dict literal in the registrar.

    `registrar_literal` cannot read SINGLE_EVENT_CMDS: its values are
    `with_python(...)` CALLS, which `ast.literal_eval` refuses. The keys are
    plain strings, and the keys are the whole question here.
    """
    tree = ast.parse(REGISTRAR.read_text(), filename=str(REGISTRAR))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            continue
        assert isinstance(node.value, ast.Dict), name
        return [ast.literal_eval(k) for k in node.value.keys]
    raise AssertionError("%s is not a module-level dict in %s" % (name, REGISTRAR))


def registrar_events():
    """Every event the registrar's own command tables register a hook on."""
    events = set()
    for name in ("NOTIFY_EVENTS", "LEDGER_EVENTS", "WRITEBACK_EVENTS"):
        events |= set(registrar_literal(name))
    events |= set(registrar_dict_keys("SINGLE_EVENT_CMDS"))
    # POST_BASH_CMDS has no event table — its event is spelled in the loop that
    # appends it — so it is named here rather than derived.
    events.add("PostToolUse")
    return events


def test_every_event_the_registrar_writes_to_is_classified():
    """🔴 THE DE-DUP IDENTITY DEPENDS ON THIS CLASSIFICATION, so an event the
    registrar registers on must not fall through to the unknown-event default.

    `matcher` is part of the de-dup identity only on an event that HAS matchers;
    on `Stop` / `UserPromptSubmit` the event always fires on every occurrence, so
    two entries for one script are a double-fire whatever their matchers say.
    An unclassified event is treated as matcher-supporting — safe, because it can
    only make the registrar DECLINE to remove something — but for an event it
    registers on itself that silence is a decision nobody made.

    ⚠ Labelled honestly: an INVARIANT GUARD, not regression coverage. It is green
    for every event that exists in the tables today; it is here because the NEXT
    event added to a table lands the same way. Mutation-checked reachable: adding
    an event to a table without classifying it turns this red naming it.
    """
    no_matcher = set(registrar_literal("NO_MATCHER_EVENTS"))
    with_matcher = set(registrar_literal("MATCHER_EVENTS"))

    # Positive controls: a parser that found nothing would make this vacuous.
    assert len(registrar_events()) >= 5, registrar_events()
    assert "Stop" in registrar_events(), registrar_events()
    assert len(no_matcher) >= 5 and len(with_matcher) >= 5

    assert no_matcher & with_matcher == set(), (
        "an event is claimed BOTH to have and not to have matcher support: %r"
        % sorted(no_matcher & with_matcher))
    unclassified = registrar_events() - (no_matcher | with_matcher)
    assert unclassified == set(), (
        "the registrar registers hooks on these events but classifies neither "
        "way, so their de-dup identity falls through to the unknown-event "
        "default: " + ", ".join(sorted(unclassified)))


# 🔴 THE LEDGER'S CONTENT, PINNED LITERALLY — re-read from the Claude Code hooks
# documentation (code.claude.com/docs/en/hooks) on 2026-08-20, NOT derived from the
# registrar. The registrar's copy is the implementation under test; deriving the
# expectation from it would make this assert `x == x`.
#
# 🔴 EXACT EQUALITY, DELIBERATELY, AND THIS IS THE POINT OF THE TEST. These sets
# have DELETION POWER: `event_has_matchers` drops `matcher` from the de-dup
# identity for every event in NO_MATCHER_EVENTS, so moving one event across is a
# one-token, deletion-free edit that makes the registrar delete registrations it
# must keep. The predecessor of this test asserted `<=` over six events, so every
# event outside those six could be moved into the destructive direction with the
# whole suite — and the full gate — green. Measured on `PreCompact`: adding it to
# NO_MATCHER_EVENTS made a manual/auto pair for one script collapse to one entry
# plus a false "has no matcher support" warning, and nothing went red.
#
# The cost is real and is the intended trade: adding a hook event to the registrar
# requires editing this literal in the same commit. That is a decision somebody
# makes, which is exactly what the old subset let people skip.
#
# The docs' wording for the first group is "no matcher support" / "always fires on
# every occurrence". The second group is narrowed by `matcher`, though not always
# by a TOOL name — SessionStart's selects the source (startup/resume/clear/compact/
# fork), PreCompact's the trigger (manual/auto), DirectoryAdded's how the directory
# was added (slash_command/register_repo_root). A real scope either way, which is
# all the de-dup identity needs.
DOCUMENTED_NO_MATCHER_EVENTS = {
    "UserPromptSubmit", "PostToolBatch", "Stop", "TeammateIdle", "TaskCreated",
    "TaskCompleted", "WorktreeCreate", "WorktreeRemove", "CwdChanged",
    "MessageDisplay",
}

DOCUMENTED_MATCHER_EVENTS = {
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest",
    "PermissionDenied", "SessionStart", "SessionEnd", "Setup", "SubagentStart",
    "SubagentStop", "Notification", "PreCompact", "PostCompact", "ConfigChange",
    "DirectoryAdded", "FileChanged", "StopFailure", "InstructionsLoaded",
    "UserPromptExpansion", "Elicitation", "ElicitationResult",
}


def test_the_documented_classification_of_the_events_that_drive_the_behaviour():
    """Both ledgers, pinned by EXACT EQUALITY against the documented lists.

    A silent edit to either set changes how real entries are deleted, and until
    this pinned the whole set rather than a six-event subset, most such edits
    moved nothing else. See the block comment above for the measurement.
    """
    no_matcher = set(registrar_literal("NO_MATCHER_EVENTS"))
    with_matcher = set(registrar_literal("MATCHER_EVENTS"))

    # Positive control: a parser that returned nothing would make both equalities
    # fail loudly rather than pass — but say so, so a future reader does not have
    # to re-derive that this cannot go vacuous.
    assert len(no_matcher) >= 5 and len(with_matcher) >= 5, (no_matcher, with_matcher)

    assert no_matcher == DOCUMENTED_NO_MATCHER_EVENTS, (
        "NO_MATCHER_EVENTS no longer matches the documented list. This set has "
        "DELETION POWER — an event here loses its `matcher` from the de-dup "
        "identity, so two entries for one script collapse to one. Extra: %r. "
        "Missing: %r" % (sorted(no_matcher - DOCUMENTED_NO_MATCHER_EVENTS),
                         sorted(DOCUMENTED_NO_MATCHER_EVENTS - no_matcher)))
    assert with_matcher == DOCUMENTED_MATCHER_EVENTS, (
        "MATCHER_EVENTS no longer matches the documented list. Extra: %r. "
        "Missing: %r" % (sorted(with_matcher - DOCUMENTED_MATCHER_EVENTS),
                         sorted(DOCUMENTED_MATCHER_EVENTS - with_matcher)))


def test_the_dedup_warning_docstring_is_no_wider_than_the_warning_it_describes():
    """🔴 A DOCSTRING IS A CLAIM ABOUT COVERAGE, and this one was MEASURED FALSE.

    It read "Any MANAGED hook script left registered more than once on one event
    is named on stderr". It is not: the double-fire ledger counts by the de-dup
    IDENTITY, whose scope is the entry's `matcher` on a matcher-supporting event.
    So agent-ledger-hook.py registered twice on PostToolUse — once unmatchered,
    once under `Bash` — produces ZERO warnings while both entries fire on every
    Bash call. Scenario 17 of test_register_nudge_hook.py drives that case.

    Pinned as a WHOLE NORMALISED STRING rather than by keyword: the previous
    sentence and the corrected one share almost every word, so any keyword guard
    passes on both. A cosmetic reword fails this test — pay it, and re-check that
    the new wording is still true of `_counts` before updating the literal.
    """
    doc = ast.get_docstring(ast.parse(REGISTRAR.read_text())) or ""
    normalised = " ".join(doc.split())
    expected = (
        "Any MANAGED hook script left registered more than once UNDER ONE SCOPE "
        "is named on stderr — the same scope the identity above uses, i.e. the "
        "entry's `matcher` on an event that has matchers and the whole event on "
        "one that does not, so two entries for one script under DIFFERENT "
        "matchers on a matcher-supporting event are not counted and not reported."
    )
    assert expected in normalised, (
        "the de-dup docstring no longer carries the sentence this test pins. It "
        "described the stderr ledger as covering a whole EVENT when the code "
        "scopes it by matcher; if you reworded it, verify the new sentence "
        "against the `_counts` loop and update the literal here.\nExpected:\n%s"
        % expected)
