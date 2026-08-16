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
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "claude-hooks" / "register-hooks-activation.sh"
REGISTRAR = ROOT / "scripts" / "claude-hooks" / "register-nudge-hook.py"
HOME_NIX = ROOT / "nix" / "home.nix"

BASH = shutil.which("bash") or "/bin/bash"

NEXT_STEP = "python3 ~/.claude/hooks/next-step-nudge.py"
NOTIFY = "python3 ~/.claude/hooks/claude-notify.py"
LEDGER = "python3 ~/.claude/hooks/agent-ledger-hook.py"
WRITEBACK = "python3 ~/.claude/hooks/clawgate-writeback-guard.py"
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


def run_activation(home, registrar=REGISTRAR, python=None, args=None):
    """Drive the SHIPPED wrapper exactly as home.nix drives it."""
    env = dict(os.environ)
    env["HOME"] = str(home)
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
    assert pre == ["python3 ~/.claude/hooks/bash-guard.py"], pre


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

HOOK_CMD_RE = re.compile(r"~/\.claude/hooks/([A-Za-z0-9_.-]+\.py)")
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
