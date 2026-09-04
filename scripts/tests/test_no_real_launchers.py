"""🔴 The guard: no test in this suite may reach a REAL host launcher.

This file exists because a GREEN suite was firing real desktop toasts and
creating real transient systemd timers on the operator's machine — 15 launches
in 29 passing tests, measured on the dev host at a67f795. The nix build sandbox
that gates merges has none of those binaries, so it can never observe the
class; these tests are written to fail in BOTH tiers.

🔴 SCOPE, stated so the title cannot be read as more than it is: this covers
`scripts/tests`, the launchers in `nolaunch.HOST_LAUNCHERS`, and `systemctl`'s
MUTATING verbs. It does not cover other suites (they have their own conftests),
and it cannot cover a test that replaces PATH wholesale — the two sites that do
are pinned below rather than protected.

What is asserted, and why each is not enough alone:

  * RESOLUTION  — `which(<launcher>)` lands inside the stub dir. On the dev host
    that IS the behavioural claim (a real binary exists to be shadowed); in the
    sandbox it degenerates to "a stub exists", which is why the others follow.
  * ORDERING    — the stub dir is PATH[0]. "On PATH" is not the property that
    matters: an entry AFTER the ambient dirs shadows nothing.
  * BEHAVIOUR   — invoking a launcher through a shell RECORDS to the stub log
    and exits 0 — and the stub's whole body is pinned against a literal, because
    a stub that records correctly AND has a side effect passes every
    behavioural assertion here.
  * AUTOUSE     — one test deliberately does NOT request the fixture, so
    deleting `autouse=True` goes red. Every other test requesting it by name
    made that deletion invisible.
  * THE SEAM    — the real hazard paths (monitor-blackout's `systemd-run`
    scheduling and its `cancel_timer` systemctl storm, rig-control's `openrgb` +
    `notify-send`, and bar-status-poll's `fire_toast`) land in the stub log. A
    component-scoped check would pass with the fixture deleted as long as some
    stub file existed somewhere.
  * THE TOAST   — `fire_toast` is driven FOR REAL, in a subprocess, with its
    seam UNPATCHED. That is the launch that escaped to the operator's desktop
    on 2026-08-11, and it escaped precisely because the file that owns it
    protects itself with a monkeypatch on the seam the test exists to bypass.
    The set of test files that LOAD the poller is pinned alongside it: each gets
    its own module object, so a per-file patch covers exactly one of them.
  * THE LEDGER  — pinned against the TREE (`testlib.launcher_scan`), not only
    against itself. `dunstctl`, `rofi` and `yad` were all reachable while the
    self-pinned list said the set was complete.
  * THE ABSENT HOST — the systemctl tests each have a "this host has no real
    systemctl" arm that carries the whole claim for the nix sandbox tier, and
    the dev host anyone measures from can never take it. All six were measured
    by `scripts/dead-guard-scan.py` as branch bodies that NEVER EXECUTE, and
    each carried a `# pragma: no cover` saying so — a note about a gap, not a
    closing of it. The arms are now module-level functions
    (`check_systemctl_stub_matches_host`, `unless_the_host_has_no_systemctl`)
    driven IN-PROCESS with a synthetic host under "THE SYNTHETIC HOST" below.
  * THE TAG     — every recorded line carries the writing pytest-xdist worker's
    id as its SECOND field, and the readers filter on it. Without that, the
    `before = len(...)` / `== before + 1` shape used throughout this file is a
    claim about THIS worker measured on a counter every sibling worker
    increments — a few-ms flake on a REQUIRED gate. Pinned deterministically
    (an injected foreign line), never raced for, plus the placement constraint
    `run-tests.sh`'s `^`-anchored greps impose on where the tag may sit.
"""
from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from testlib import launcher_scan  # noqa: E402
from testlib import nolaunch  # noqa: E402
from testlib.mockbin import write_exec  # noqa: E402

# Same reasoning as the other suites here: resolve the interpreter once, to an
# absolute path, because `/usr/bin/env` does not exist in the nix sandbox and a
# bare "bash" would be looked up in the child's (stub-only) PATH.
_BASH = shutil.which("bash")
if _BASH is None:  # pragma: no cover — both tiers ship bash
    raise RuntimeError("bash not found on PATH; this suite cannot run hermetically")
_SH = "/bin/sh"

STUB_DIR_ENV = "DEVRC_TEST_LAUNCH_STUB_DIR"


def _stub_dir_from_env() -> Path:
    """The stub dir as the FIXTURE published it, not as a fixture argument.

    🔴 Deliberate: a test that takes `no_real_launchers` as a parameter requests
    the fixture explicitly, so it still runs with `autouse=True` deleted. Tests
    that go through this helper depend on the fixture having run for EVERYONE,
    which is the property the fixture exists for.
    """
    raw = os.environ.get(STUB_DIR_ENV)
    assert raw, (
        f"{STUB_DIR_ENV} is unset — the session fixture in scripts/tests/"
        "conftest.py did not run for a test that never asked for it, so the "
        "suite is unprotected for every test that forgets to request it")
    return Path(raw)


def _real_binary(name: str) -> str | None:
    """Resolve `name` on the AMBIENT PATH, ignoring the stub dir.

    Lets a test ask "does this host actually have one?" without the fixture's
    answer getting in the way — the question both tiers must be asked
    differently about.
    """
    stub = str(_stub_dir_from_env())
    entries = [p for p in os.environ["PATH"].split(os.pathsep) if p and p != stub]
    return shutil.which(name, path=os.pathsep.join(entries))


# --------------------------------------------------------------------------- #
# The "this host has no systemctl" arm, hoisted out of the tests so it can be
# DRIVEN rather than merely declared
# --------------------------------------------------------------------------- #
# 🔴 WHY THESE ARE FUNCTIONS AND NOT `if` BLOCKS INSIDE THE TESTS. Every
# systemctl test below has two arms, and this dev host can only ever take one of
# them: `_real_binary("systemctl")` resolves here, so the `real is None` arm —
# the one the nix sandbox tier takes on EVERY run — never executed in-process on
# the machine anyone measures from. `scripts/dead-guard-scan.py` flagged all six
# of those arms as never-executed branch bodies, and the honest reading is (b)
# in `scripts/lib/dead_guard.py`'s taxonomy: a reporting branch with no positive
# control. Six sites carried a `# pragma: no cover` saying exactly that.
#
# Hoisting the arm into a module-level function makes it drivable with a
# SYNTHETIC `real`/`resolved` pair, which is what the two controls under "THE
# SYNTHETIC HOST" at the end of this file do. They call these functions
# DIRECTLY, in this interpreter, because `sys.settrace` is per-interpreter: a
# control that drove them through `subprocess.run` would leave the branch
# recorded as never-executed and buy nothing at all.
#
# It also collapses a predicate that was open-coded at FIVE sites into one, so
# `_assert_nothing_answers_to_systemctl`'s message is written once. Four of
# those five spelled it as a bare `assert shutil.which("systemctl") is None`
# with no message; they now carry the fifth's.
def _assert_nothing_answers_to_systemctl(resolved) -> None:
    """On a host with no real systemctl, nothing may answer to the name.

    `resolved` is what `shutil.which("systemctl")` says on the LIVE PATH — i.e.
    with the fixture's stub dir in front. A stub installed where there is
    nothing to shadow would make `command -v systemctl` start succeeding and
    change which branch every script under test takes.
    """
    assert resolved is None, (
        "no real systemctl, yet something answers to the name")


def check_systemctl_stub_matches_host(real, stub, stub_dir, resolved) -> None:
    """🔴 The invariant, asserted in BOTH directions rather than skipped in one.

    Extracted verbatim from `test_systemctl_is_stubbed_exactly_when_a_real_one_
    exists`; the test now calls this and nothing else, so both arms are one
    another's siblings in one place.

      `real`     — the systemctl on the AMBIENT path (`_real_binary`), or None.
      `stub`     — where the fixture would have written a systemctl stub.
      `stub_dir` — the fixture's stub directory.
      `resolved` — `shutil.which("systemctl")` on the LIVE path.
    """
    if real is None:
        assert not stub.exists(), (
            "a systemctl stub was installed on a host that has no systemctl — "
            "that changes `command -v systemctl` for every script under test")
        _assert_nothing_answers_to_systemctl(resolved)
    else:
        assert stub.exists(), (
            f"a real systemctl exists at {real} and nothing shadows it — "
            "mutating verbs from the scripts under test reach the live session")
        assert Path(resolved).parent == stub_dir


def unless_the_host_has_no_systemctl(real, resolved, body):
    """Run `body()` where a real systemctl exists; assert the claim where none does.

    The four tests below that used to open with

        if _real_binary("systemctl") is None:
            assert shutil.which("systemctl") is None
            return

    now route through here. Deliberately NOT `pytest.skip`: this file's whole
    argument is that a tier which cannot reach the hazard should still ASSERT
    the absence rather than fall silent, and a skipped test is invisible in the
    tier that is authoritative for merges.
    """
    if real is None:
        _assert_nothing_answers_to_systemctl(resolved)
        return
    body()


def only_when_a_real_systemctl_exists(fn):
    """`unless_the_host_has_no_systemctl` as a decorator, for the tests whose
    check sits at the very TOP of the body.

    Not used on `test_monitor_blackout_restore_cannot_stop_a_real_timer`: there
    the check sits deliberately AFTER the script has been run, and hoisting it
    to the top would stop that tier running monitor-blackout.sh at all.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return unless_the_host_has_no_systemctl(
            _real_binary("systemctl"), shutil.which("systemctl"),
            lambda: fn(*args, **kwargs))
    return wrapper


# --------------------------------------------------------------------------- #
# The ledger — pinned against itself AND against the tree
# --------------------------------------------------------------------------- #
def test_the_stubbed_launcher_set_is_pinned():
    """Fails when the set GROWS **or** SHRINKS — both must be deliberate.

    Shrinking is the dangerous direction (a launcher silently becomes reachable
    again); growing is welcome, and updating this list is the acknowledgement.
    """
    assert set(nolaunch.HOST_LAUNCHERS) == {
        "systemd-run", "notify-send", "dunstify", "dunstctl", "rofi", "yad",
        "openrgb", "ddcutil", "xdg-open", "i3-msg", "xdotool", "espanso",
    }
    # systemctl is NOT record-only — it is verb-split, and lives in its own
    # tests below. Putting it here would swallow `is-active`, which scripts and
    # tests branch on.
    assert "systemctl" not in nolaunch.HOST_LAUNCHERS


# Every HAZARD_VOCABULARY name the top-level scripts reach that is NOT stubbed,
# with the reason it is safe. 🔴 This table is the acknowledgement, not a
# silencer: an entry here is a claim that nothing in scripts/tests can reach it.
#
# 🔴 THE FILE SET IS THE ACKNOWLEDGEMENT, NOT THE NAME. Pinning the name alone
# re-opened this PR's founding failure mode one entry at a time — MEASURED:
# adding `home-manager switch …` to `scripts/rig-control.sh`, a script this
# suite EXECUTES, produced a real `home-manager` launch with all 54 guard tests
# green, because the name was already acknowledged for other files. The reasons
# were wrong too: this entry used to say "ship.sh/drift-check.sh only" while
# seven scripts named it.
ACKNOWLEDGED_UNSTUBBED = {
    "systemctl": (
        {"airvpn-menu", "keylog-spin-capture.sh",
         "monitor-blackout.sh", "run-tests.sh", "sync-claude-permissions.py"},
        "verb-split rather than record-only — see the systemctl tests below. "
        "run-tests.sh is a THIRD case, re-justified rather than absorbed: its "
        "only occurrences of the name are GUARD 7's accounting, which counts "
        "`systemctl(read)` LINES IN THE LAUNCH LOG (`grep -c '^systemctl(read)'`) "
        "and reports them per target. It never invokes systemctl — it reads the "
        "record of calls the stub already classified. "
        "sync-claude-permissions.py is a DIFFERENT case from the other four and "
        "is re-justified rather than absorbed: its only occurrence of the name "
        "is the literal string `Bash(systemctl status:*)` inside its CURATED "
        "table of permission RULES, and the script spawns no subprocess at all — "
        "it imports none of subprocess / os.system / os.exec* / os.popen, which "
        "test_sync_claude_permissions.py asserts STRUCTURALLY so this "
        "justification cannot rot into a claim about a file that has changed"),
    "home-manager": (
        {"bar-status-poll", "drift-check.sh", "keylog-spin-capture.sh",
         "notify-failure.sh", "playwright-nixos", "resume-state.sh",
         "session-manager", "session-resolve", "ship.sh", "tmux-post-save.sh",
         "tmux-scratch-slots.sh"},
        "MEASURED unreachable: a whole-tier run under a recording interceptor "
        "logged ZERO calls. TWO of these are executed by scripts/tests and "
        "neither can reach the binary: notify-failure.sh names home-manager in "
        "a journal hint, and session-manager (added 2026-08-13 with the agent "
        "activity ledger) names it in ONE docstring — `_load_agent_ledger`, "
        "explaining that the hook loads a nix-store COPY of agent_ledger.py so "
        "writer and reader agree only at the instant of a switch. This scan is "
        "a TEXT scan (launcher_scan.hazard_hits regexes the file body), so a "
        "prose mention is a hit; verified by grep that the file carries no "
        "call site, and re-justified here rather than reworded to dodge the "
        "scanner. session-resolve (added 2026-08-19) is the THIRD of exactly "
        "this shape and is re-justified the same way: its single occurrence "
        "is one word of module-docstring prose, explaining that the tmux "
        "bindings are generated from the slot table at home-manager build "
        "time and therefore that the table must be PARSED rather than "
        "re-hardcoded. It is not a call site. The complete set of argv[0] "
        "literals the script can spawn is `tmux`, `git` and `gh` (plus "
        "sys.executable for session-manager), and its tmux seam is further "
        "narrowed by an ALLOWLIST of list-panes/list-windows/list-clients "
        "that raises on anything else — test_session_resolve.py pins that "
        "allowlist in both directions, so this justification cannot rot into "
        "a claim about a file that has grown a launcher. "
        "tmux-scratch-slots.sh (added 2026-08-19) is the FOURTH of this shape "
        "and carries the STRONGEST form of the justification: the other three "
        "merely lack a call site, whereas this file has no executable "
        "statement at all. It is the slot table that session-resolve's entry "
        "above refers to, and its entire non-comment body is a single "
        "`SCRATCH_SLOTS=( ... )` array literal of 20 quoted "
        "session:key:colour:name strings — measured with "
        "`grep -vE '^\\s*#|^\\s*$'`, which returns the array and nothing else. "
        "There is no command substitution, pipe, exec or eval anywhere in it, "
        "and it is SOURCED rather than executed. Its single `home-manager` "
        "occurrence is one word of comment prose at line 4, recording that the "
        "tmux `bind -n M-<key>` popup toggles are GENERATED from this table by "
        "nix/programs/tmux/default.nix (`builtins.readFile`) and that the "
        "table is therefore the source of truth rather than a mirror. That "
        "sentence was added to correct a comment which had documented the "
        "hotkey as a `$mod+Shift+<key>` i3 chord bound to nothing; the "
        "correction is what put the file in this scanner's sights, and it is "
        "re-justified here rather than reworded to dodge the scanner. "
        "resume-state.sh (added 2026-08-25 with the SKILL-freshness block) is "
        "the FIFTH of this shape and the one that most needed checking, "
        "because unlike the four above it IS executed by scripts/tests — three "
        "suites run it end to end. Its SIX occurrences of the name were "
        "grepped and are all PROSE: three in comments (the skill_block header, "
        "the rationale for the three-way provenance fork, and a note that "
        "CLAUDE.md recommends `home-manager switch --flake …` off a feature "
        "branch), and three inside strings the block PRINTS — the `store copy "
        "at … — only a home-manager switch replaces it` label, the UNMANAGED "
        "label that says a switch will NOT replace a hand-placed foreign file, "
        "and the DRIFT line telling the reader that a switch, not a `git "
        "pull`, is what replaces a stale deployed skill. (The count read THREE "
        "until the audit round added the provenance fork; a stale count here "
        "is the same class of defect as a stale comment anywhere else.) "
        "Naming the fix is the whole point of those "
        "sentences, so they are re-justified here rather than reworded to "
        "dodge the scanner. The set of binaries the script can spawn is "
        "`git`, `readlink`, `timeout`, `ssh` (only as git's transport, via "
        "GIT_SSH_COMMAND), `mktemp`, `stat`, `date`, `jq`, `awk`/`sed`/`grep` "
        "and `clawgatectl`; the SKILL block adds only `git` and `readlink`"),
    "nixos-rebuild": (
        {"airvpn-sudo", "ship.sh"},
        "MEASURED unreachable in the same whole-tier run; both call sites are "
        "behind sudo and neither script is executed by scripts/tests"),
    "wmctrl": (
        {"session-write"},
        "The FOURTH occurrence of the prose-mention shape already justified "
        "three times under `home-manager` above, and justified here rather "
        "than reworded away — this scan is a TEXT scan "
        "(launcher_scan.hazard_hits regexes the file body), so naming a binary "
        "in order to promise you never call it is indistinguishable from "
        "calling it. session-write (added 2026-08-19) names `wmctrl` in ONE "
        "line of module-docstring prose, in the sentence declaring that the "
        "i3 workspace is OUT OF SCOPE for its `focus` verb: the tool is "
        "tmux-only, so it changes a tmux client's session and a tmux session's "
        "active window and NOTHING a window manager owns. Deleting the word to "
        "get green would delete the guarantee. Verified by grep that the file "
        "carries no call site: the complete set of argv[0] literals it can "
        "spawn is `tmux` alone, and even that is narrowed by an ALLOWLIST "
        "(send-keys / select-window / switch-client / detach-client, plus "
        "session-resolve's three read verbs) that RAISES on anything else — "
        "test_session_write.py pins that allowlist in both directions, so this "
        "justification cannot rot into a claim about a file that has grown a "
        "launcher. `i3-msg` and `xdotool` appear in the same sentence and need "
        "no entry: both are in HOST_LAUNCHERS and therefore stubbed."),
}


def test_every_hazardous_binary_the_scripts_reach_is_stubbed_or_acknowledged():
    """🔴 The ledger, pinned against the TREE instead of against itself.

    An earlier revision asserted only that HOST_LAUNCHERS contained what it
    contained. `dunstctl` (notif-center, i3status-notifs, bar-status-poll),
    `rofi` (five scripts) and `yad` (rig-control's panel) were all invoked by
    scripts this suite executes, none was listed, and nothing went red.

    TWO directions now, because a name-only pin let an acknowledgement absorb a
    NEW reacher silently:
      * an unknown name is a failure;
      * a known-but-unstubbed name whose FILE SET changed is a failure — in
        either direction, so the table cannot rot into describing a tree that
        no longer exists.
    """
    hits = launcher_scan.hazard_hits(SCRIPTS)
    stubbed = set(nolaunch.HOST_LAUNCHERS)

    unknown = {name: files for name, files in hits.items()
               if name not in stubbed and name not in ACKNOWLEDGED_UNSTUBBED}
    assert not unknown, (
        "these host-affecting binaries are reached by scripts/ but are neither "
        f"stubbed nor acknowledged: {unknown}. Add them to "
        "nolaunch.HOST_LAUNCHERS, or to ACKNOWLEDGED_UNSTUBBED with the exact "
        "file set and the reason nothing in scripts/tests can reach them.")

    for name, (pinned_files, _why) in ACKNOWLEDGED_UNSTUBBED.items():
        found = set(hits.get(name, []))
        assert found == pinned_files, (
            f"the acknowledgement for {name!r} names {sorted(pinned_files)} but "
            f"the tree now says {sorted(found)}. A NEW file reaching an "
            "acknowledged binary is exactly what the acknowledgement does NOT "
            "cover — stub it, or re-justify it for the new file set.")


def test_the_hazard_scan_can_actually_find_something(tmp_path):
    """POSITIVE CONTROL. A scan that matches nothing would pass the test above
    for every possible tree, which is the reassuring zero RULES.md warns about."""
    fake = tmp_path / "scripts"
    fake.mkdir()
    # write_exec, not write_text: these fixtures carry a shebang, and a test
    # file that spells one itself is what test_runtime_shebangs.py scans for
    # (it went red on the first version of these two lines).
    write_exec(fake / "some-script.sh", "rofi -dmenu\n")
    write_exec(fake / "quiet.sh", "echo hello\n")
    hits = launcher_scan.hazard_hits(fake)
    assert hits == {"rofi": ["some-script.sh"]}, hits


def test_no_repo_file_can_shadow_a_stub_by_name():
    """`test_rig_control.py` prepends `scripts/` to PATH BEFORE the ambient
    entries, so a file named `scripts/openrgb` would out-rank the stub dir for
    that suite. None exists today; this fails the day one does."""
    shadowing = [n for n in nolaunch.HOST_LAUNCHERS if (SCRIPTS / n).exists()]
    assert not shadowing, (
        f"scripts/ contains {shadowing}, which a suite that prepends scripts/ to "
        "PATH would resolve INSTEAD of the stub")


# --------------------------------------------------------------------------- #
# Resolution + ordering
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("launcher", nolaunch.HOST_LAUNCHERS)
def test_every_launcher_resolves_into_the_stub_dir(launcher, no_real_launchers):
    """`which` must find the STUB, never the real binary.

    Not `which(...) is not None` — that is satisfied by the real thing. The
    assertion is on the resolved PARENT directory, which is what a real binary
    earlier on PATH would break.
    """
    resolved = shutil.which(launcher)
    assert resolved is not None, (
        f"{launcher} resolves to nothing — the stub dir is not on PATH, so a "
        f"host that HAS {launcher} would reach the real one")
    assert Path(resolved).parent == no_real_launchers, (
        f"{launcher} resolves to {resolved}, outside the stub dir "
        f"{no_real_launchers} — a real binary is winning the PATH lookup")


def test_the_stub_dir_is_first_on_path(no_real_launchers):
    """FIRST, not merely present.

    This is the assertion the sandbox tier can still make: there, no real
    launcher exists to shadow, so a stub dir appended to the END of PATH would
    satisfy every resolution check above while providing zero protection on the
    dev host — where the real binaries live in `/run/current-system/sw/bin` and
    `~/.nix-profile/bin`.
    """
    entries = os.environ["PATH"].split(os.pathsep)
    assert entries[0] == str(no_real_launchers), (
        "the stub dir must be the FIRST PATH entry; PATH starts with "
        f"{entries[:3]}")


def test_a_test_that_never_asked_for_the_fixture_is_still_protected():
    """A non-requesting test finds the stub — in THIS session.

    ⚠ ORDER-DEPENDENT, and that is stated rather than hidden: the fixture is
    session-scoped, so once ANY test requests it the environment stays patched
    for everything after. This test therefore cannot distinguish "autouse" from
    "somebody earlier asked" — MEASURED: with `autouse=False` it still passed.
    `test_autouse_is_what_protects_a_test_that_never_asks` below is the pin;
    this one is the cheap in-session check that the patched state is real.
    """
    stub_dir = _stub_dir_from_env()
    resolved = shutil.which("openrgb")
    assert resolved is not None, (
        "openrgb resolves to nothing for a test that did not request the "
        "fixture — the protection is opt-in, which is the bug")
    assert Path(resolved).parent == stub_dir, (
        f"openrgb resolves to {resolved}, outside {stub_dir}: a test that never "
        "asked for the fixture is unprotected")


_PROBE_TEST = '''\
"""Asserts protection WITHOUT requesting the fixture, and alone in its session."""
import os
import shutil
from pathlib import Path


def test_protected_without_asking():
    stub = os.environ.get("DEVRC_TEST_LAUNCH_STUB_DIR")
    assert stub, "the session fixture never ran for a test that did not ask"
    resolved = shutil.which("openrgb")
    assert resolved and Path(resolved).parent == Path(stub), resolved
'''


def test_autouse_is_what_protects_a_test_that_never_asks(tmp_path):
    """🔴 THE AUTOUSE PIN, as a control/mutant PAIR in a separate session.

    MEASURED first, which is why this exists: deleting `autouse=True` changed
    NOTHING across the whole scripts/tests directory — every guard test took the
    fixture as a parameter, so the first one to run set it up for all the rest,
    and the in-session check above passed on the mutant while unrelated files
    fired real `systemd-run` calls. An earlier check always winning is the
    unreachable-guard trap from claude/RULES.md.

    The only way to observe autouse is a session where NOBODY asks:

      control : real conftest      -> the probe PASSES (positive control — the
                                      fixture reaches a test that never asked)
      mutant  : autouse=False      -> the probe FAILS

    Both halves run the real `scripts/tests/conftest.py`, so this pins the
    shipped file rather than a paraphrase of it.
    """
    # 🔴 The fixture MOVED (see conftest.py's header): the implementation now
    # lives in testlib/nolaunch_plugin.py so that run-tests.sh can load the same
    # module for all 17 targets with `-p`, instead of 17 conftests. This pin
    # follows it — it must mutate the SHIPPED file, not a paraphrase, so the
    # tree is copied rather than symlinked and the copy is what gets mutated.
    plugin_rel = Path("testlib") / "nolaunch_plugin.py"
    plugin_src = (SCRIPTS / plugin_rel).read_text(encoding="utf-8")
    needle = "autouse=" + "True"
    assert plugin_src.count(needle) == 1, (
        f"expected exactly one autouse declaration to mutate, found "
        f"{plugin_src.count(needle)}")
    conftest_src = (SCRIPTS / "tests" / "conftest.py").read_text(encoding="utf-8")

    def _probe_session(where: Path, plugin_text: str):
        root = where / "scripts"
        (root / "tests").mkdir(parents=True)
        shutil.copytree(SCRIPTS / "testlib", root / "testlib",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (root / plugin_rel).write_text(plugin_text, encoding="utf-8")
        (root / "tests" / "conftest.py").write_text(conftest_src, encoding="utf-8")
        probe = root / "tests" / "test_probe.py"
        probe.write_text(_PROBE_TEST, encoding="utf-8")
        # `_run_nested` strips this session's stub dir from PATH — without that
        # the child is already protected by the PARENT and both halves pass,
        # which is exactly how the first version of this pin went green on its
        # own mutant.
        return _run_nested(str(probe), where / "basetemp")

    control = _probe_session(tmp_path / "control", plugin_src)
    assert control.returncode == 0, (
        "a test that never requested the fixture was NOT protected — autouse is "
        f"not doing its job:\n{control.stdout}")

    mutant = _probe_session(tmp_path / "mutant",
                            plugin_src.replace(needle, "autouse=" + "False"))
    assert mutant.returncode != 0, (
        "with autouse removed the probe STILL passed, so nothing here observes "
        f"autouse and the protection is silently opt-in:\n{mutant.stdout}")
    assert "the session fixture never ran" in mutant.stdout, mutant.stdout


# --------------------------------------------------------------------------- #
# Behaviour — and the stub's exact shape
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("launcher", nolaunch.HOST_LAUNCHERS)
def test_invoking_a_launcher_records_and_launches_nothing(launcher, no_real_launchers):
    """A shell — the way every script under test invokes these — reaches the stub.

    The recorded line is the positive control: a log that never moves is
    indistinguishable from a harness wired to nothing.
    """
    before = len(nolaunch.recorded(no_real_launchers))
    marker = f"--guard-probe-{launcher}"
    p = subprocess.run(
        [_SH, "-c", f"{launcher} {marker}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=15, env=dict(os.environ),
    )
    assert p.returncode == 0, (
        f"{launcher} stub exited {p.returncode}: {p.stdout}{p.stderr}")

    lines = nolaunch.recorded(no_real_launchers)
    assert len(lines) == before + 1, (
        f"expected exactly one new recorded launch, got {lines[before:]}")
    # The `[<worker>]` field is pinned HERE, by exact equality, because the
    # per-worker filter that makes `before + 1` sound under xdist reads exactly
    # this position. A stub that stopped emitting it would still record, still
    # exit 0, and quietly make every filtered read return nothing.
    assert lines[-1] == f"{launcher} [{nolaunch.worker_tag()}] {marker}", lines[-1]


def test_the_stubs_exit_zero_because_a_failing_stub_would_change_the_script(
        no_real_launchers):
    """Exit status 0 is part of the contract, not an accident.

    monitor-blackout.sh and rig-control.sh run under `set -e` and treat these
    launchers as fire-and-forget. A stub that exited non-zero would abort
    `blackout()` at the `systemd-run` line, so every later assertion in
    test_monitor_blackout.py would be measuring the STUB instead of the script.
    """
    for launcher in nolaunch.HOST_LAUNCHERS:
        p = subprocess.run(
            [str(no_real_launchers / launcher), "probe"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15,
            env=dict(os.environ))
        assert p.returncode == 0, f"{launcher} stub exited {p.returncode}"


@pytest.mark.parametrize("launcher", nolaunch.HOST_LAUNCHERS)
def test_a_stub_does_nothing_but_record(launcher, no_real_launchers):
    """🔴 Pins the stub's ENTIRE body against a literal written here.

    Every behavioural assertion above is satisfied by a stub that records
    correctly and ALSO does something else: MEASURED, a stub given an extra side
    effect (`: > "<log>.sideeffect"`) left the suite green before this test
    existed. The only way to pin "records and nothing more" is to pin the whole
    text, and to write the expectation HERE rather than derive it from
    `nolaunch.launcher_body`, which the mutation would change too.

    ⚠ An earlier version of this docstring said "kept 52/52 green while
    performing 36 of them". Neither number was reproducible — 52 was never a
    collected count of anything here, and 36 is the base-clone systemctl figure
    from a DIFFERENT experiment. Retracted rather than re-derived.
    """
    log = nolaunch.log_path(no_real_launchers)
    # 🔴 The shebang is spelled in TWO PIECES on purpose. Written whole it is a
    # quoted `#!` in a test file, which is precisely what
    # `test_runtime_shebangs.py` scans for — it went red on this line. Splitting
    # keeps the expectation a LITERAL (deriving it from `mockbin.SHEBANG` would
    # make a mutation of that constant invisible here) while staying out of the
    # scanner's needles.
    # 🔴 The `[%s]` worker field and its `${PYTEST_XDIST_WORKER:-main}` source
    # are spelled out HERE as literals, not built from `nolaunch.WORKER_ENV` /
    # `SERIAL_WORKER`, for the same reason as the shebang below: a mutation of
    # those constants would change the expectation and this pin with it. Under
    # xdist a stub that read the WRONG variable would tag every line `main` and
    # still look perfectly well-formed in the log.
    expected = (
        "#" + "!/bin/sh\n"
        "printf '%s [%s] %s\\n' \"" + launcher + "\" "
        "\"${PYTEST_XDIST_WORKER:-main}\" \"$*\" >> \"" + str(log) + "\"\n"
        "exit 0\n"
    )
    actual = (no_real_launchers / launcher).read_text(encoding="utf-8")
    assert actual == expected, (
        f"the {launcher} stub is not a pure recorder any more:\n{actual!r}")


# --------------------------------------------------------------------------- #
# THE PER-WORKER TAG — the xdist cross-worker race, closed deterministically
# --------------------------------------------------------------------------- #
# 🔴 The bug these pin: ONE run-wide `launches.log`, and since #841 every pytest
# target runs `-n N --dist loadfile`. `loadfile` keeps THIS file's tests on one
# worker, so this file does not race itself — but ~8 sibling files in
# `scripts/tests` also invoke `systemctl`/`openrgb`/`systemd-run`/`notify-send`,
# run on SIBLING workers, and append to the same file. Every assertion here is
# `before = len(...)` → act → `== before + 1`, i.e. a claim about THIS worker's
# launches measured on a counter four workers increment.
#
# The window is a few ms and the original report got NOT REPRODUCED in 2/2 runs,
# so none of these races for it. The foreign line is INJECTED between the sample
# and the assertion, which is the same state the race produces and is reached
# every single run.
def _private_stubs(tmp_path) -> Path:
    """A stub dir of this test's OWN, never the session's run-wide one.

    🔴 Load-bearing, twice over. (1) One of these tests APPENDS a fabricated
    foreign line to the log; in the run-wide log that line would be counted by
    `run-tests.sh`'s GUARD 7 accounting as a real launcher reach, and would move
    the `before` sample of every other test in this file. (2) They all assert an
    EXACT total, which the shared log can never offer.

    🔴 AND THE `systemctl` STUB IS RE-POINTED, not left as `install()` wrote it.
    `install()` resolves "the real systemctl" through `shutil.which`, and by the
    time any test runs, PATH[0] is the SESSION stub dir — so a plain `install()`
    here writes a systemctl stub whose read branch execs the SESSION stub, which
    records into the SESSION log. `install()`'s own self-exec assertion cannot
    catch that: it only compares against the dir being installed into, and these
    are two different dirs. Inert today (only the classifier test invokes
    systemctl, and it overwrites this file first) — closed anyway, because the
    next test to call `systemctl` through this helper would silently measure the
    wrong file and read as a passing test.
    """
    stubs = tmp_path / "own-stubs"
    nolaunch.install(stubs)
    real = _real_binary("systemctl")
    if real is None:
        # What `install()` would have written on a PATH with no stub dir on it:
        # nothing. Never fabricate an answer — the module's own rule.
        # ⚠ DEFENSIVE, AND UNREACHABLE IN BOTH TIERS TODAY — do not count it as
        # exercised: on the dev host a real systemctl exists, and in the sandbox
        # `install()` never wrote a systemctl stub for this branch to remove.
        (stubs / "systemctl").unlink(missing_ok=True)
    else:
        write_exec(stubs / "systemctl",
                   nolaunch.systemctl_body(real, nolaunch.log_path(stubs)))
    sysctl = stubs / "systemctl"
    if sysctl.exists():
        body = sysctl.read_text(encoding="utf-8")
        assert str(_stub_dir_from_env()) not in body, (
            "this private stub dir's systemctl still chains into the SESSION "
            "stub dir, so a read verb through it would exec the session stub "
            f"and be recorded in the wrong log:\n{body}")
    return stubs


def _through_a_shell(stubs: Path, command: str, **envextra):
    """Invoke a stub the way a script under test does: a PATH lookup in a child.

    Deliberately NOT `subprocess.run([str(stubs / name)])`: an absolute path to a
    ledgered basename is exactly what the plugin's L2 layer rewrites, and it
    would rewrite it to the SESSION stub dir — so the launch would be recorded in
    the wrong log and this test would measure nothing.
    """
    env = dict(os.environ)
    env["PATH"] = str(stubs) + os.pathsep + env["PATH"]
    env.update(envextra)
    return subprocess.run([_SH, "-c", command],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=15, env=env)


def test_a_sibling_workers_line_does_not_count_as_this_workers_launch(tmp_path):
    """🔴 REGRESSION for the xdist cross-worker race — deterministic, not raced.

    RED AT origin/main, but say how: there the module has no per-worker concept
    at all, so this test cannot even be expressed and dies on `AttributeError`.
    The meaningful red is the mutant that restores origin/main's SEMANTICS while
    keeping the API — `recorded()` accepting `worker=` and ignoring it. MEASURED
    against that mutant, this test fails on the assertion below with its own
    message: the injected sibling lines land inside `before + 1` and the count
    is 3.
    """
    stubs = _private_stubs(tmp_path)
    log = nolaunch.log_path(stubs)
    mine = nolaunch.worker_tag()
    sibling = f"{mine}-sibling"
    assert sibling != mine

    before = len(nolaunch.recorded(stubs))

    # THE RACE. A sibling xdist worker appending between the sample and the
    # assertion — two lines, because both readers are exposed.
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"notify-send [{sibling}] --a-sibling-workers-toast\n")
        fh.write(f"systemctl(blocked) [{sibling}] --user stop sibling.timer\n")

    marker = "--own-launch-probe"
    p = _through_a_shell(stubs, f"notify-send {marker}")
    assert p.returncode == 0, f"{p.stdout}{p.stderr}"

    lines = nolaunch.recorded(stubs)
    assert len(lines) == before + 1, (
        "a SIBLING xdist worker's launches were counted as this worker's. That "
        "is the race: `before + 1` is a claim about what THIS test launched, "
        f"and it was measured against every worker's lines:\n{lines}")
    assert lines[-1] == f"notify-send [{mine}] {marker}", lines[-1]

    assert nolaunch.blocked_systemctl(stubs) == [], (
        "the sibling worker's blocked systemctl call was attributed to this "
        "worker — `blocked_systemctl` is exposed to the same race")

    # 🔴 POSITIVE CONTROL. Everything above is equally satisfied by an injection
    # that never happened, or by a `recorded()` pointed at the wrong file. The
    # unfiltered view must SEE the foreign lines for the filtered view's silence
    # to mean anything.
    every = nolaunch.recorded(stubs, worker=nolaunch.ALL_WORKERS)
    assert len(every) == before + 3, (
        f"the foreign lines are not in the log at all, so filtering them out "
        f"proves nothing:\n{every}")
    assert [ln for ln in every if nolaunch.line_worker(ln) == sibling] == [
        f"notify-send [{sibling}] --a-sibling-workers-toast",
        f"systemctl(blocked) [{sibling}] --user stop sibling.timer",
    ], every
    assert len(nolaunch.blocked_systemctl(stubs, worker=nolaunch.ALL_WORKERS)) == 1


def test_the_stub_takes_its_tag_from_the_xdist_worker_variable(tmp_path):
    """The MECHANISM, measured at both ends of the dimension it depends on.

    MEASURED on the workbench before this was written (6 files, `-n 4 --dist
    loadfile`): four distinct ids `gw0`..`gw3` reached a `sh -c` child; the same
    files run serially left `PYTEST_XDIST_WORKER` UNSET there. Both points are
    pinned here rather than raced for, because a stub that read the variable but
    had no fallback would tag serial runs with an empty field and a stub that
    ignored the variable would tag every xdist worker `main` — and BOTH still
    produce a well-formed-looking log.
    """
    stubs = _private_stubs(tmp_path)

    # Point 1: a worker id in the environment is the tag.
    p = _through_a_shell(stubs, "openrgb --under-a-worker",
                         PYTEST_XDIST_WORKER="gw7")
    assert p.returncode == 0, f"{p.stdout}{p.stderr}"

    # Point 2: no worker id — the serial fallback, one namespace with no gap.
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_XDIST_WORKER"}
    env["PATH"] = str(stubs) + os.pathsep + os.environ["PATH"]
    q = subprocess.run([_SH, "-c", "openrgb --serial"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, timeout=15, env=env)
    assert q.returncode == 0, f"{q.stdout}{q.stderr}"

    # 🔴 Point 3, THE BOUNDARY BETWEEN THEM: set-but-EMPTY. Neither of the two
    # points above can see it, and it is where the shell and Python spellings
    # can silently disagree — `${PYTEST_XDIST_WORKER:-main}` falls back on
    # empty, and so must `worker_tag()`. MEASURED: rewriting that `or` as
    # `os.environ.get(WORKER_ENV, SERIAL_WORKER)` SURVIVES a set/unset-only
    # test, and would then have Python looking for `""` while the stub wrote
    # `[main]` — every filtered read empty, every line well-formed.
    r = _through_a_shell(stubs, "openrgb --empty-worker", PYTEST_XDIST_WORKER="")
    assert r.returncode == 0, f"{r.stdout}{r.stderr}"
    prev = os.environ.get("PYTEST_XDIST_WORKER")
    os.environ["PYTEST_XDIST_WORKER"] = ""
    try:
        assert nolaunch.worker_tag() == nolaunch.SERIAL_WORKER, (
            "an EMPTY PYTEST_XDIST_WORKER made Python disagree with the stub: "
            "the sh `:-` fallback wrote the serial tag and this did not")
    finally:
        if prev is None:
            os.environ.pop("PYTEST_XDIST_WORKER", None)
        else:
            os.environ["PYTEST_XDIST_WORKER"] = prev

    every = nolaunch.recorded(stubs, worker=nolaunch.ALL_WORKERS)
    assert every == [
        "openrgb [gw7] --under-a-worker",
        f"openrgb [{nolaunch.SERIAL_WORKER}] --serial",
        f"openrgb [{nolaunch.SERIAL_WORKER}] --empty-worker",
    ], every
    assert nolaunch.recorded(stubs, worker="gw7") == [
        "openrgb [gw7] --under-a-worker"], every


def test_the_readers_default_to_this_worker_and_can_still_see_everything(tmp_path):
    """The `worker=` contract, pinned against a hand-written log.

    🔴 THE DEFAULT IS THE POINT. A `recorded()` that defaulted to the whole log
    would leave the race armed for the next test somebody writes — the shape of
    the bug, not a variant of it — so "the default filters" is asserted, not
    left to be inferred from the call sites that happen to exist today.

    The `nolaunch(session)` marker is here on purpose: it is deliberately
    untagged (`run-tests.sh` owns its per-target accounting), so it belongs to
    no worker and must appear under ALL_WORKERS and nowhere else.
    """
    stubs = tmp_path / "hand-written"
    stubs.mkdir()
    mine = nolaunch.worker_tag()
    log = nolaunch.log_path(stubs)
    log.write_text(
        "nolaunch(session) scripts/tests -q\n"
        f"systemd-run [{mine}] --user --unit=ours --on-active=2h true\n"
        "systemctl(blocked) [gwZ] --user stop theirs.timer\n"
        f"systemctl(blocked) [{mine}] --user stop ours.timer\n"
        "systemctl(read) [gwZ] --user is-active theirs.timer\n"
        "i3-msg(abs) [gwZ] workspace 9\n",
        encoding="utf-8")

    assert nolaunch.recorded(stubs) == [
        f"systemd-run [{mine}] --user --unit=ours --on-active=2h true",
        f"systemctl(blocked) [{mine}] --user stop ours.timer",
    ], nolaunch.recorded(stubs)
    assert nolaunch.blocked_systemctl(stubs) == [
        f"systemctl(blocked) [{mine}] --user stop ours.timer"]

    assert len(nolaunch.recorded(stubs, worker=nolaunch.ALL_WORKERS)) == 6
    assert nolaunch.recorded(stubs, worker="gwZ") == [
        "systemctl(blocked) [gwZ] --user stop theirs.timer",
        "systemctl(read) [gwZ] --user is-active theirs.timer",
        "i3-msg(abs) [gwZ] workspace 9",
    ]
    assert nolaunch.line_worker("nolaunch(session) scripts/tests -q") is None
    assert nolaunch.line_worker("i3-msg(abs) [gwZ] workspace 9") == "gwZ"
    # 🔴 `line_worker`'s PARSE, pinned at the width its comment claims — each
    # case below is a piece of the regex that SURVIVES deletion without it.
    # An argv containing a bracketed word must not be mistaken for the tag
    # (this one is carried by the `^\S+ ` anchor, not by the tag class).
    # 🔴 THE TRAILING ` --urgency low` IS LOAD-BEARING. With the bracketed word
    # at END of string, `^\S+ ` widened to `^.* ` behaves IDENTICALLY — the
    # greedy match has to backtrack to the only `] ` there is — so the fixture
    # could not see the mutant its own comment describes. MEASURED: with the
    # word at the end, `\S+` → `.*` SURVIVES; with something after it, the
    # widened form yields `not-a-tag` and this goes red.
    assert nolaunch.line_worker(
        "notify-send [gw1] -t 2500 [not-a-tag] --urgency low") == "gw1"
    # The tag class excludes whitespace — a malformed field is NOT a tag, and
    # the module comment names the bounded silent-zero this implies:
    assert nolaunch.line_worker("notify-send [gw 1] hello") is None
    # …and excludes `]`, so the tag cannot swallow its own delimiter and keep
    # hunting for a later one. Without that exclusion this yields `gw]x`:
    assert nolaunch.line_worker("notify-send [gw]x] hello") is None
    # The ordinary well-formed shape still parses, so the case above is a
    # rejection of malformed input and not the class being broken outright:
    assert nolaunch.line_worker("notify-send [gw] hello") == "gw"
    # The delimiter must be followed by the argv separator:
    assert nolaunch.line_worker("notify-send [gw1]nospace") is None
    # A missing classifier token is not a tagged line either:
    assert nolaunch.line_worker("[gw1] notify-send hello") is None


def _guard7_grep_patterns() -> tuple[str, str, str]:
    """PARSE the three log classifiers out of run-tests.sh's GUARD 7 block.

    🔴 EXTRACTED, not copied — an earlier version of this helper asserted three
    `<literal> in runner` substrings and its docstring claimed it "read the
    patterns out of the runner". That was false in the way that matters: a
    substring check cannot notice a FOURTH classifier appearing, and it cannot
    tell you which pattern the runner actually applies to what.

    🔴 AND THE PARSE ITSELF WAS THEN TOO NARROW — the same shape as the finding
    it was written to fix, one layer down. It matched only `grep -c '…'` and
    `grep -vE '…'`, so MEASURED, inserting `grep -cE '^brandnew\\(kind\\)'` into
    the block left this GREEN. Two things now stand in for "what the runner will
    run": the quote-and-flag-agnostic pattern extraction below, AND a pin on the
    SET OF `grep` INVOCATIONS in the block, which catches a new classifier
    whatever its flags or quoting — including one with no quoted pattern at all.

    Scoped to the GUARD 7 evaluation block by its own section banners, so a
    `grep -c` belonging to GUARD 8 or 10 cannot be mistaken for one of these.
    """
    runner = (SCRIPTS / "run-tests.sh").read_text(encoding="utf-8")
    start = runner.find("# --- GUARD 7 (evaluation)")
    end = runner.find("# --- GUARD 8 (evaluation)", start + 1)
    assert start != -1 and end > start, (
        "run-tests.sh's GUARD 7 evaluation block is no longer delimited by the "
        "banners this parse anchors on — it cannot say what the runner runs, "
        "and a pass here would mean nothing")
    block = runner[start:end]

    # EVERY grep in the block, by flag, in source order. The fourth is the
    # unquoted `grep -c .` that counts the hit lines — it takes no classifier
    # pattern, which is exactly why a pin on quoted patterns alone cannot see a
    # grep appear or disappear.
    invocations = re.findall(r"\bgrep\s+(-[\w-]+)", block)
    assert invocations == ["-c", "-c", "-vE", "-c"], (
        "GUARD 7 no longer runs exactly these four greps over the launch log. "
        "A classifier added or removed changes what 'unclassified = a real "
        f"launcher reach' means, which is the whole verdict it emits: {invocations}")

    # Flag- and quote-agnostic, so `-cE` or a double-quoted pattern is seen.
    quoted = [(flag, pat) for flag, _q, pat
              in re.findall(r"\bgrep\s+(-[\w-]+)\s+(['\"])(.+?)\2", block)]
    assert quoted == [
        ("-c", "^nolaunch(session)"),
        ("-c", "^systemctl(read)"),
        ("-vE", r"^(nolaunch\(session\)|systemctl\(read\)|$)"),
    ], f"GUARD 7's classifier patterns changed: {quoted}"
    return quoted[0][1], quoted[1][1], quoted[2][1]


# ⚠ INVARIANT GUARD, not regression coverage — THIS TEST, not the helper above.
# It passes at origin/main too, because there the tag does not exist and the
# classifiers match trivially. It pins the constraint the tag's PLACEMENT had to
# satisfy — `run-tests.sh` reads this log with `^`-anchored greps on the FIRST
# token, so a tag at line START would stop the session marker and every
# `systemctl(read)` passthrough from matching and report both as real launcher
# reaches, failing the whole run. Nothing in Python covered those greps before;
# they are the log's real consumer.
def test_the_runners_anchored_classifiers_still_match_the_tagged_format(tmp_path):
    """Run `run-tests.sh`'s OWN grep patterns against a log this tree produced.

    The patterns are PARSED out of the runner (see `_guard7_grep_patterns`), and
    executed by real `grep` rather than re-implemented in `re`, because the
    claim is about what the runner does to this file.
    """
    marker_pat, read_pat, hit_pat = _guard7_grep_patterns()

    grep = shutil.which("grep")
    assert grep is not None, (
        "no grep on PATH — run-tests.sh cannot classify its own launch log "
        "without one, so this environment cannot run the gate either")

    # 🔴 EVERY LINE BELOW IS PRODUCED BY THIS TREE'S OWN CODE, none typed out
    # here. Hand-writing the `systemctl(read)` and `nolaunch(session)` lines is
    # what makes this guard vacuous: those two are the ONLY ones the runner
    # classifies by their first token, so a hand-written copy stays in the old
    # format under the very mutation this test exists to catch — MEASURED, an
    # earlier draft of this test stayed green under a tag-at-line-start mutant.
    #
    # 🔴 AND EVERY STUB IS DRIVEN UNDER A PROBE TAG, NOT THE AMBIENT ONE. This
    # is the fixture trap from claude/RULES.md, measured here: comparing against
    # `worker_tag()` collapses to the literal `main` in ANY serial run, which is
    # the same constant the sh stub falls back to — so `w = _WORKER_SH` mutated
    # to `w = '"main"'` in `systemctl_body` SURVIVED the whole suite serially
    # (90 passed) and died only under `-n 2`. A fixture that can only ever
    # produce the constant's own value cannot see a mutant that hardcodes the
    # literal. `gwSYSPROBE` is a value `SERIAL_WORKER` CANNOT equal, so this
    # test now measures the same thing whichever way the suite is invoked.
    # (`launcher_body` has the equivalent control via `gw7` plus a literal-text
    # pin, and the L2 layer via `gwABSPROBE`; this was the branch without one.)
    probe = "gwSYSPROBE"
    assert probe != nolaunch.SERIAL_WORKER
    stubs = _private_stubs(tmp_path)
    log = nolaunch.log_path(stubs)
    from testlib import nolaunch_plugin
    nolaunch_plugin._log_marker(stubs, "scripts/tests -q")
    for cmd in ("notify-send a-toast", "systemd-run --user --unit=u true"):
        assert _through_a_shell(
            stubs, cmd, PYTEST_XDIST_WORKER=probe).returncode == 0
    # The systemctl stub, generated by `systemctl_body` and pointed at a
    # passthrough target of our own — so ALL THREE of its branches run in BOTH
    # tiers, where `install()` writes no systemctl stub at all when the host has
    # none: the VERB read, the mutating block, and the position-independent
    # `--version`/`--help` flag read, which no test in this tree exercised at
    # all before (it has its own printf, so it is its own mutation site).
    passthrough = tmp_path / "passthrough-systemctl"
    write_exec(passthrough, "exit 0\n")
    write_exec(stubs / "systemctl",
               nolaunch.systemctl_body(str(passthrough), log))
    for cmd in ("systemctl --user status u",      # VERB read
                "systemctl --user stop u",        # mutating -> blocked
                "systemctl --version"):           # position-independent FLAG read
        assert _through_a_shell(
            stubs, cmd, PYTEST_XDIST_WORKER=probe).returncode == 0

    def _grep(args):
        p = subprocess.run([grep, *args, str(log)], stdout=subprocess.PIPE,
                           text=True, timeout=15)
        return [ln for ln in p.stdout.splitlines() if ln]

    assert _grep(["-c", marker_pat]) == ["1"], (
        "the session marker stopped matching the runner's ^-anchored count — "
        "GUARD 7 would report the plugin as never loaded in this target")
    reads = _grep([read_pat])
    assert len(reads) == 2, (
        "a systemctl READ stopped matching the runner's ^-anchored count and "
        f"would now be reported as a real launcher reach: {reads}")
    # 🔴 THE READ LINES CARRY THE TAG TOO — asserted here because NOTHING ELSE
    # CAN SEE IT. A read is by construction not a `hit`, so the hits assertion
    # below never inspects it, and in the nix sandbox tier (no real systemctl)
    # `install()` writes no systemctl stub, so no other test in the file
    # produces a read line at all. MEASURED: with this assertion absent,
    # deleting `[%s]` from ONLY the `systemctl(read)` branch of
    # `systemctl_body` survived that tier at 74 passed / exit 0, while the
    # identical mutation of its `systemctl(blocked)` sibling was killed. Both
    # read branches are covered: `--user status u` is the VERB one and
    # `--version` is the position-independent FLAG one.
    assert [nolaunch.line_worker(ln) for ln in reads] == [probe, probe], (
        "a `systemctl(read)` line did not carry the worker id its stub was RUN "
        "under. It still classifies correctly for the runner, so nothing in "
        f"run-tests.sh notices — but every per-worker read drops it: {reads}")

    hits = _grep(["-vE", hit_pat])
    assert len(hits) == 3, (
        "the runner's HIT selector no longer classifies the log the way this "
        "tree writes it — the marker and both systemctl READS must be excluded "
        f"and the three launcher lines kept; a tag at line START breaks exactly "
        f"that:\n{hits}")
    assert sorted(ln.split(" [", 1)[0] for ln in hits) == [
        "notify-send", "systemctl(blocked)", "systemd-run"], hits
    # Same probe tag, same reason: `systemctl(blocked)` and the two record-only
    # launchers each have their own printf, so each is its own mutation site.
    assert [nolaunch.line_worker(ln) for ln in hits] == [probe] * 3, hits


# --------------------------------------------------------------------------- #
# systemctl — verb-split, and installed only when there is something to shadow
# --------------------------------------------------------------------------- #
def test_systemctl_is_stubbed_exactly_when_a_real_one_exists(no_real_launchers):
    """🔴 The invariant, asserted in BOTH directions rather than skipped in one.

    Installing a systemctl stub where none exists (the nix sandbox) would make
    `command -v systemctl` start succeeding and change which branch every script
    under test takes — a behaviour change in the authoritative tier bought for
    no protection at all. Installing NONE where a real one exists is the hazard.
    """
    check_systemctl_stub_matches_host(
        _real_binary("systemctl"),
        no_real_launchers / "systemctl",
        no_real_launchers,
        shutil.which("systemctl"))


@only_when_a_real_systemctl_exists
def test_a_mutating_systemctl_verb_is_recorded_and_swallowed(no_real_launchers):
    """`stop` must not reach the real binary.

    The discriminator is not just the log line: a REAL `systemctl --user stop`
    of a unit that is not loaded exits non-zero and writes to stderr, so
    rc == 0 with empty stderr is itself evidence the real one did not run.
    """
    before = len(nolaunch.blocked_systemctl(no_real_launchers))
    unit = "devrc-guard-probe-never-exists.timer"
    p = subprocess.run(
        [_SH, "-c", f"systemctl --user stop {unit}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=15, env=dict(os.environ))

    assert p.returncode == 0, f"{p.stdout}{p.stderr}"
    assert p.stderr == "", (
        f"the real systemctl appears to have run: {p.stderr!r}")
    blocked = nolaunch.blocked_systemctl(no_real_launchers)
    # 🔴 THE CROSS-WORKER RACE THIS USED TO CARRY IS CLOSED — read the note, it
    # is what makes `before + 1` sound again. This comment previously said the
    # assertion was "sound only because pytest runs this suite sequentially in
    # one process", which the xdist change (`-n N --dist loadfile`) made false:
    # loadfile keeps THIS file on one worker, but ~8 sibling files in this tree
    # also invoke `systemctl` and append to the SAME run-wide
    # `$DEVRC_TEST_LAUNCH_STUB_DIR/launches.log` that `before` was sampled from.
    #
    # It is not fixed by per-worker LOG FILES — that WOULD need the runner's
    # ledger model changed, since it attributes intercepts to targets by line
    # offset into one file. It is fixed by a per-worker TAG inside that one log:
    # `blocked_systemctl()` now returns only THIS worker's lines by default, so
    # a sibling worker's `systemctl(blocked)` can no longer land inside the
    # window. See `testlib/nolaunch.py`'s docstring, and the deterministic pins
    # under "THE PER-WORKER TAG" above — the race is injected there rather than
    # raced for, because the window is a few ms and it was NOT REPRODUCED in 2/2
    # runs when it was open.
    assert len(blocked) == before + 1, blocked[before:]
    assert unit in blocked[-1] and "stop" in blocked[-1], blocked[-1]


# 🔴 Each of these reached the REAL binary under the first implementation, which
# scanned every argument instead of classifying the verb: a UNIT NAMED after a
# read verb promoted a mutation to a passthrough. Nothing in this tree is named
# that way, so there was no live reach — but the guard's value is that it does
# not depend on what units happen to be called.
@pytest.mark.parametrize("argv", [
    "--user stop status",
    "--user restart cat",
    "--user kill status",
    "--user stop show",
    "--user poweroff",
    "--user isolate rescue.target",
    "--user daemon-reload",
    "--user mask devrc-guard-probe.service",
    "-M somehost is-active devrc-guard-probe.service",
    "FOO=cat stop devrc-guard-probe.service",
])
@only_when_a_real_systemctl_exists
def test_an_argument_that_spells_a_read_verb_does_not_promote_a_mutation(
        argv, no_real_launchers):
    """The verb is the FIRST NON-FLAG token, exactly as systemd parses it."""
    before = len(nolaunch.blocked_systemctl(no_real_launchers))
    p = subprocess.run(
        [_SH, "-c", f"systemctl {argv}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=15, env=dict(os.environ))
    assert p.returncode == 0 and p.stderr == "", (
        f"`systemctl {argv}` reached the real binary: rc={p.returncode} "
        f"stderr={p.stderr!r}")
    blocked = nolaunch.blocked_systemctl(no_real_launchers)
    assert len(blocked) == before + 1, (
        f"`systemctl {argv}` was not recorded as blocked: {blocked[before:]}")


@only_when_a_real_systemctl_exists
def test_the_read_passthrough_forwards_stdout_stderr_and_status_faithfully(
        no_real_launchers):
    """🔴 FIDELITY, compared against the REAL binary rather than asserted.

    A passthrough that dropped stderr, or normalised the exit status, would keep
    every other test here green while quietly changing what the scripts under
    test observe. So run the same read verb twice — once through the stub, once
    through the real binary resolved off the ambient PATH — and require all
    three channels to match.
    """
    real = _real_binary("systemctl")
    args = ["--user", "cat", "devrc-guard-probe-never-exists.service"]
    through_stub = subprocess.run(
        [shutil.which("systemctl"), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=15, env=dict(os.environ))
    through_real = subprocess.run(
        [real, *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=15, env=dict(os.environ))

    got = (through_stub.returncode, through_stub.stdout, through_stub.stderr)
    want = (through_real.returncode, through_real.stdout, through_real.stderr)
    # 🔴 POSITIVE CONTROL, inside the comparison. Two empty answers are equal,
    # so without this the test passes when the reference is itself a silent
    # stub — MEASURED: under a recording harness whose systemctl answered every
    # read with exit 0 and no output, the "passthrough drops stderr" mutant
    # SURVIVED this very assertion. A comparison must have content to compare.
    assert want[0] != 0 or want[1] or want[2], (
        "the reference systemctl produced nothing at all (rc=0, no output), so "
        f"comparing against it proves nothing: {real}")
    assert got == want, (
        f"the stub does not forward the real binary's answer faithfully: "
        f"stub={got!r} real={want!r}")
    assert any(ln.startswith("systemctl(read)") for ln in
               nolaunch.recorded(no_real_launchers))


@only_when_a_real_systemctl_exists
def test_a_read_only_systemctl_verb_still_reaches_the_real_binary(no_real_launchers):
    """The other half: swallowing reads would fabricate system state.

    `is-active` on a unit that does not exist must still answer the way the real
    binary answers (non-zero), because scripts and tests branch on it —
    monitor-blackout's `status` is exactly that branch.
    """
    p = subprocess.run(
        [_SH, "-c", "systemctl --user is-active devrc-guard-probe-never-exists.timer"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=15, env=dict(os.environ))
    # A SWALLOWING stub would exit 0 with empty output — exactly what the
    # mutating branch above does. Non-zero here is the discriminator, and the
    # `systemctl(read)` line proves the passthrough branch is the one that ran.
    # (The exact wording of the real binary's answer is deliberately not
    # asserted: it differs between a host with a user bus and one without, and
    # this test is about which BRANCH ran, not about systemd's phrasing.)
    assert p.returncode != 0, (
        "is-active answered 0 for a unit that does not exist — the stub is "
        f"fabricating state instead of passing the read through: {p.stdout!r}")
    assert any(ln.startswith("systemctl(read)") for ln in
               nolaunch.recorded(no_real_launchers))


# --------------------------------------------------------------------------- #
# The seam: the REAL hazard paths must land in the stub
# --------------------------------------------------------------------------- #
def _canonical_copy(tmp_path, script_name):
    """Copy `scripts/<script_name>` to `<tmp>/workspace/devrc/scripts/`.

    monitor-blackout.sh refuses to run from anywhere but
    `${HOME}/workspace/devrc/scripts/monitor-blackout.sh` (#374), and this
    suite must exercise the path that is ACCEPTED — that is where the timer
    creation lives. Same construction as test_monitor_blackout.py.
    """
    home = tmp_path / "canon-home"
    dest = home / "workspace" / "devrc" / "scripts" / script_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SCRIPTS / script_name, dest)
    return home, dest


def test_monitor_blackout_scheduling_reaches_the_stub_not_systemd(tmp_path):
    """🔴 The exact launch that created 8h timers on the operator's machine.

    Fails in BOTH tiers if the fixture is removed: on the dev host the real
    `systemd-run` would take the call (and create the timer) leaving the log
    empty; in the sandbox there is no `systemd-run` at all, so the script dies
    and the log is empty just the same.

    Takes the stub dir from the ENVIRONMENT, not as a fixture parameter, so
    `autouse=True` is load-bearing here too.
    """
    stub_dir = _stub_dir_from_env()
    home, canon = _canonical_copy(tmp_path, "monitor-blackout.sh")
    # This test's OWN stubs, in tmp_path, which sits before the session stub dir
    # in the child PATH — the two mechanisms compose, they do not fight.
    write_exec(tmp_path / "ddcutil", textwrap.dedent("""\
        case "$*" in
            *detect*) echo "i2c-5" ;;
            *getvcp*10*) echo "VCP 10 100 75" ;;
            *) echo "ok" ;;
        esac
        exit 0
    """))
    write_exec(tmp_path / "systemctl", 'exit 0\n')

    before = len(nolaunch.recorded(stub_dir))
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    env["XDG_RUNTIME_DIR"] = str(tmp_path)
    p = subprocess.run([_BASH, str(canon), "2h"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                       timeout=60, env=env)
    assert p.returncode == 0, f"{p.stdout}{p.stderr}"

    new = nolaunch.recorded(stub_dir)[before:]
    scheduled = [ln for ln in new if ln.startswith("systemd-run ")]
    assert len(scheduled) == 1, (
        f"expected monitor-blackout.sh's schedule_restore to hit the stub; "
        f"recorded: {new}")
    assert "--unit=monitor-blackout-restore-v2" in scheduled[0], scheduled[0]
    assert "--on-active=2h" in scheduled[0], scheduled[0]


def test_monitor_blackout_restore_cannot_stop_a_real_timer(tmp_path):
    """🔴 `restore()` calls `cancel_timer` as its FIRST statement — BEFORE any
    ddcutil call — so the ddcutil stub does NOT close this path.

    MEASURED at the revision that claimed it did: `restore` reached 12 mutating
    systemctl calls (stop/kill/reset-failed x 2 units x 2 rounds), and in the
    base-clone condition `test_rig_control.py` (16 tests, all green) reached 36
    of them. When a blackout is pending, that is a `git push` that kills the
    auto-restore timer and leaves the panel dark.
    """
    stub_dir = _stub_dir_from_env()
    home, canon = _canonical_copy(tmp_path, "monitor-blackout.sh")
    write_exec(tmp_path / "ddcutil", textwrap.dedent("""\
        case "$*" in
            *detect*) echo "i2c-5" ;;
            *getvcp*10*) echo "VCP 10 100 60" ;;
            *) echo "ok" ;;
        esac
        exit 0
    """))
    before = len(nolaunch.blocked_systemctl(stub_dir))

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    env["XDG_RUNTIME_DIR"] = str(tmp_path)
    p = subprocess.run([_BASH, str(canon), "restore"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                       timeout=60, env=env)
    assert p.returncode == 0, f"{p.stdout}{p.stderr}"

    # 🔴 THE CHECK SITS HERE, NOT AT THE TOP OF THE TEST, AND THAT PLACEMENT IS
    # LOAD-BEARING — which is why this one site calls the dispatcher by hand
    # instead of wearing `@only_when_a_real_systemctl_exists`. On a host with no
    # systemctl there is nothing to protect (cancel_timer's calls could not have
    # reached one) and the claim to assert is THAT, rather than skipping — but
    # monitor-blackout.sh must still have been RUN, which is the tier where the
    # script itself is exercised. Hoisting the check to the top would take that
    # run away from exactly the tier that is authoritative for merges.
    def tail():
        blocked = nolaunch.blocked_systemctl(stub_dir)[before:]
        verbs = {v for ln in blocked
                 for v in ("stop", "kill", "reset-failed") if v in ln}
        assert verbs == {"stop", "kill", "reset-failed"}, (
            f"cancel_timer's mutating verbs did not all land in the stub: {blocked}")
        assert any("monitor-blackout-restore-v2" in ln for ln in blocked), blocked

    unless_the_host_has_no_systemctl(
        _real_binary("systemctl"), shutil.which("systemctl"), tail)


def test_rig_control_notify_and_rgb_reach_the_stub(tmp_path):
    """rig-control's other two real launches: `openrgb` and the toast.

    `rgb-off` is the shortest path that fires both. Uses the environment rather
    than the fixture parameter, for the autouse reason above.
    """
    stub_dir = _stub_dir_from_env()
    before = len(nolaunch.recorded(stub_dir))
    env = dict(os.environ)
    env["XDG_CACHE_HOME"] = str(tmp_path)
    p = subprocess.run([_BASH, str(SCRIPTS / "rig-control.sh"), "rgb-off"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                       timeout=30, env=env)
    assert p.returncode == 0, f"{p.stdout}{p.stderr}"

    new = nolaunch.recorded(stub_dir)[before:]
    assert any(ln.startswith("openrgb ") and "000000" in ln for ln in new), new
    assert any(ln.startswith("notify-send ") and "Chassis RGB off" in ln
               for ln in new), new


# --------------------------------------------------------------------------- #
# The bar-status TOAST seam — the third real hazard path, and the one a
# per-file fixture structurally cannot hold
# --------------------------------------------------------------------------- #
# Loads `scripts/bar-status-poll` the way the suite's own tests do and calls
# `fire_toast` FOR REAL: no `runner=` injection, no patched `_toast_runner`, and
# the session-bus precondition satisfied so the call cannot short-circuit before
# reaching the launcher. Run as a SUBPROCESS on purpose — an in-process probe
# could be credited to some other test's monkeypatch, and the property under
# test is the one that survives a fresh interpreter: the PATH.
_TOAST_PROBE = '''\
import importlib.machinery, importlib.util, sys
loader = importlib.machinery.SourceFileLoader("_poll_toast_seam", sys.argv[1])
spec = importlib.util.spec_from_loader("_poll_toast_seam", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)
print("DISPATCHED=%s" % mod.fire_toast("critical", "SEAMPROBESUMMARY",
                                       "SEAMPROBEBODY"))
'''


def test_the_bar_status_toast_reaches_the_stub_not_the_desktop(tmp_path):
    """🔴 THE POSITIVE CONTROL: a test that genuinely TRIES to toast the operator.

    This is the launch that actually escaped. MEASURED on the workbench, in the
    user journal at 2026-08-11 13:14:58:

        Started [systemd-run] …/bash -c "a=$(dunstify -a bar-status -u \\"$1\\"
        \\"$2\\" \\"$3\\")" bar-status critical sum body

    — the literal fixture arguments of `test_bar_status.py`'s seam test, on a
    real desktop. It escaped because that file's protection is a monkeypatch of
    `poll._toast_runner`, and the whole point of the seam test is to run
    `fire_toast` in a state where it may NOT route through that attribute. A
    patch on a seam cannot stop a launch that bypasses the seam; only something
    below the process boundary can, which is what the PATH fixture is.

    So this test does the forbidden thing deliberately and asserts the stub
    caught it. `DISPATCHED=True` is load-bearing as its own positive control: if
    `fire_toast` returned False it skipped at the session-bus check and never
    reached a launcher, and an empty log would prove nothing at all.

    Fails in BOTH tiers without the fixture: on the dev host the real
    `systemd-run` takes the call and the log stays empty; in the sandbox there
    is no `systemd-run`, `fire_toast` swallows the OSError, `DISPATCHED=False`
    and the assertion below names that instead.
    """
    stub_dir = _stub_dir_from_env()
    poller = SCRIPTS / "bar-status-poll"
    before = len(nolaunch.recorded(stub_dir))

    env = dict(os.environ)
    # Satisfy `_borrow_desktop_env` so it returns immediately and the bus check
    # passes — otherwise the toast is skipped and this test measures nothing.
    env["DISPLAY"] = ":0"
    env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/dev/null"
    env["XAUTHORITY"] = str(tmp_path / "xauth")
    env["DEVRC_DIR"] = str(SCRIPTS.parent)
    env["HOME"] = str(tmp_path)
    p = subprocess.run([sys.executable, "-B", "-c", _TOAST_PROBE, str(poller)],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, timeout=60, env=env)
    assert p.returncode == 0, p.stdout
    assert "DISPATCHED=True" in p.stdout, (
        "fire_toast did not reach a launcher at all, so an empty stub log would "
        f"be meaningless — it returned early:\n{p.stdout}")

    new = nolaunch.recorded(stub_dir)[before:]
    launched = [ln for ln in new if ln.startswith("systemd-run ")]
    assert len(launched) == 1, (
        "bar-status-poll's fire_toast did not land in the stub — it reached a "
        f"REAL systemd-run and put a toast on someone's screen. recorded: {new}")
    assert "dunstify -a bar-status" in launched[0], launched[0]
    assert "SEAMPROBESUMMARY" in launched[0] and "SEAMPROBEBODY" in launched[0], \
        launched[0]


# 🔴 A RELATIONSHIP, not a component. `test_bar_status.py`'s autouse fixture
# patches the `_toast_runner` of the module object THAT FILE loaded; a different
# file loading the same script gets its own module object with a live launcher
# and inherits nothing. Two such files exist and neither patches the seam — the
# PATH fixture is their ONLY protection, which is exactly why this set has to be
# pinned in both directions rather than left to whoever adds the next one.
POLLER_LOADING_TESTS = {
    "test_bar_status.py":  "patches poll._toast_runner (autouse, whole file) AND "
                           "is covered by the PATH fixture",
    "test_airvpn_menu.py": "loads the poller for its airvpn parsers only; does "
                           "NOT patch the toast seam — PATH fixture only",
    "test_bar_url.py":     "loads the poller for _bar_url_action; does NOT patch "
                           "the toast seam — PATH fixture only",
}


def test_every_test_file_that_loads_the_poller_is_pinned():
    """A NEW file loading `bar-status-poll` is a new unprotected toast reacher.

    Grows-or-shrinks, for the reason `ACKNOWLEDGED_UNSTUBBED` is bound to a file
    set: a ledger that only says "these names are fine" absorbs the next reacher
    silently. The fix when this goes red is to read the new file and decide
    whether the PATH fixture is enough for it — not to append a line.
    """
    found = launcher_scan.module_loaders(SCRIPTS / "tests", "bar-status-poll")
    assert set(found) == set(POLLER_LOADING_TESTS), (
        "the set of test files that LOAD scripts/bar-status-poll changed.\n"
        f"  pinned: {sorted(POLLER_LOADING_TESTS)}\n"
        f"  tree:   {sorted(found)}\n"
        "Each one gets its own module object, so test_bar_status.py's autouse "
        "_toast_runner patch does NOT cover it; the PATH fixture is what does.")


def test_the_module_loader_scan_can_actually_find_something(tmp_path):
    """Positive control for the scan itself — a zero from a scan wired to
    nothing is indistinguishable from a zero that means "no loaders"."""
    (tmp_path / "test_decoy.py").write_text(
        '_load("bar-status-poll", "x")\n', encoding="utf-8")
    (tmp_path / "test_mentions_only.py").write_text(
        'DATA = {"service": "bar-status-poll"}\n', encoding="utf-8")
    found = launcher_scan.module_loaders(tmp_path, "bar-status-poll")
    assert set(found) == {"test_decoy.py"}, (
        f"the scan sees a loader but not a bare mention; got {found}")
    assert launcher_scan.module_loaders(tmp_path, "no-such-script") == {}


# --------------------------------------------------------------------------- #
# The PATH-clobber sites the fixture CANNOT cover
# --------------------------------------------------------------------------- #
# (file, lineno-independent needle, why it is safe today)
#
# 🔴 SELF-MATCH, and it bit on the first run: written whole, each needle below is
# ITSELF a PATH-clobbering line in this file, so the scan reported this file as
# a third unpinned site. Split across a `+` the same way `testlib/shebang_scan.py`
# assembles its patterns — which keeps THIS file inside the scan's scope instead
# of excluding it, so a real clobber added here would still be caught.
PINNED_PATH_CLOBBERS = {
    "test_session_stamp_seam.py": (
        '"PATH"' + ': str(empty_bin)',
        "an EMPTY directory in tmp_path, justified by emptiness like "
        "test_claude_log_rotate.py above. The absence is the whole point of the "
        "test: the installer-GENERATED prepare-commit-msg must exit 0 when no "
        "python3 can be found, because as a `#!/usr/bin/env python3` hook an "
        "unresolvable interpreter made `git commit` exit 1 with the commit "
        "REFUSED (measured, paired control). No amount of PREPENDING can make "
        "an interpreter unfindable, so the clobber is required to reach the "
        "condition at all. 🔴 It removes python3 and everything else — the test "
        "asserts only the hook's exit status and that the message file is "
        "byte-identical, both of which are independent of what else is on PATH"),
    "test_rig_control.py": (
        '"PATH"' + ': "/usr/bin/false"',
        "deliberately makes yad unfindable; /usr/bin/false holds no binaries, so "
        "nothing hazardous is reachable from it either"),
    "test_claude_log_rotate.py": (
        'e["PATH"]' + ' = str(tmp_path / "empty-bin")',
        "an empty directory in tmp_path — the point of the test is that "
        "logrotate is absent; nothing else is present either"),
    "test_standup_local_health.py": (
        'env["PATH"]' + ' = str(self._restricted_bin())',
        "the FIRST pinned clobber whose replacement directory is not empty, so "
        "it is justified by ENUMERATION rather than by emptiness: "
        "Harness.RESTRICTED_BIN lists the nine coreutils standup needs to run "
        "at all, the harness asserts the directory's contents are a subset of "
        "that list, and it asserts systemctl is absent — which is the point of "
        "the test: standup.sh must skip its host-health section gracefully "
        "when the systemctl BINARY IS NOT INSTALLED, and no amount of "
        "PREPENDING can make a binary unfindable. 🔴 That is ALL it removes — "
        "it says nothing about a systemctl that is present while the user "
        "manager/bus is unreachable, which is a different condition with a "
        "different (and once-broken) rendering; that one is covered by the "
        "SC_FAIL_ALL/SC_FAIL_SHOW modes of the stub, with systemctl very much "
        "on PATH. No launcher in HAZARD_VOCABULARY is reachable "
        "from it: no systemctl, kubectl, gh, ssh, home-manager or pkill"),
    "test_resume_state_clawgate.py": (
        'env["PATH"]' + ' = f"{nocg}',
        "justified by ENUMERATION, like test_standup_local_health.py above. The "
        "replacement is two directories the test CONSTRUCTS: `nocg`, holding "
        "copies of this suite's gh/kubectl/curl tripwire stubs and nothing "
        "else, and `_sandbox_bin`, holding symlinks to exactly its "
        "_SANDBOX_TOOLS list (coreutils + bash/git/jq) — which the helper "
        "ASSERTS is a superset of the directory's real contents, so this is a "
        "live invariant rather than prose that can rot. No HAZARD_VOCABULARY "
        "name is reachable from either: no systemd-run, systemctl, "
        "notify-send, rofi, yad, xdotool, i3-msg, openrgb, espanso, "
        "home-manager or nixos-rebuild. 🔴 REPLACING is the point: the case "
        "under test is `clawgatectl` NOT INSTALLED — resume-state.sh must emit "
        "a `!` gap rather than a clean reconcile — and clawgatectl IS "
        "installed on the dev host, so no amount of PREPENDING can make it "
        "unfindable. A prepending version measured a live call to the real "
        "board on this host while the nix sandbox (which has no clawgatectl) "
        "measured the intended case: two tiers, opposite blind spots"),
    "test_workhost.py": (
        '"PATH"' + ': str(bindir)',
        "justified by ENUMERATION, like test_standup_local_health.py above, and "
        "it is the first entry whose replacement directory deliberately "
        "CONTAINS launcher names: ssh, scp, rsync, tmux and kubectl are all "
        "present. They are STUBS the file itself writes via "
        "testlib.mockbin.write_exec, never the real binaries, and that is the "
        "whole point — workhost's job is to shell out to ssh, so the suite has "
        "to answer those calls without a network. The invariant is LIVE, not "
        "prose: test_the_stub_path_contains_only_declared_stubs asserts the "
        "directory's contents are a subset of SANDBOX_BINARIES, that no "
        "launcher-shaped entry is a symlink, and that each one's body contains "
        "this suite's own WH_LOGDIR marker — so a real binary leaking in fails "
        "a test rather than rotting a comment. 🔴 REPLACING is required, not "
        "stylistic, in BOTH directions: the `not-configured` assertions depend "
        "on `tailscale` being genuinely ABSENT and no amount of PREPENDING can "
        "make a binary unfindable if one is later installed; and the real `ip` "
        "on workbench reports the nebula address 10.42.0.30, so a prepended "
        "PATH would let the host's true identity leak into the tests that must "
        "believe they are remote, silently exercising the local-exec branch and "
        "passing for the wrong reason"),
    "test_devshell_satisfies_required_tools.py": (
        '{"PATH"' + ': str(stub)',
        "a clobber justified by ENUMERATION rather than emptiness, and the "
        "strongest of those. (It used to say \"the SECOND … of the two\": there "
        "are THREE enumeration-justified entries, and next to the \"TWO sites\" "
        "below the ordinal read as if it counted those instead.) 🔴 This needle "
        "now matches TWO sites in "
        "that file — the `emitted_fatal` fixture (`only-bash`) and "
        "`test_guard1_classifies_by_cause_not_by_site` (`only-bash-cause`) — so "
        "it cannot tell them apart; BOTH carry the one-entry assertion, which "
        "is what keeps this justification live for each. In both, the "
        "replacement directory is created by the test itself near the clobber "
        "(`tmp_path_factory.mktemp(...)`, then one "
        "`(stub / \"bash\").symlink_to(bash)`), so its contents are not merely "
        "audited but CONSTRUCTED — it holds exactly one entry, a bash symlink, "
        "and nothing else can appear in a freshly-minted tmp dir. No "
        "HAZARD_VOCABULARY name is reachable from it: no systemd-run, "
        "systemctl, notify-send, rofi, yad, xdotool, i3-msg, openrgb, espanso, "
        "home-manager or nixos-rebuild. 🔴 REPLACING is the point, not an "
        "oversight: the fixture drives run-tests.sh's tool precondition, whose "
        "whole job is to react to binaries being ABSENT, and no amount of "
        "PREPENDING can make a binary unfindable — inside the nix sandbox every "
        "REQUIRED_TOOLS binary IS present, so a prepending version would "
        "measure the environment instead of the code. The fixture ASSERTS the "
        "one-entry contents itself, so this justification is a live invariant "
        "rather than prose that can rot"),
}


def test_every_path_clobbering_site_is_pinned():
    """🔴 The one shape this fixture structurally cannot protect.

    A test that REPLACES PATH instead of prepending to it drops the stub dir
    entirely. Both sites today are harmless — the replacement paths contain no
    binaries — but the third one, added tomorrow with a real directory in it,
    would be unprotected and nothing would go red. So the SET is pinned: a new
    clobber fails here until someone writes down why it is safe.
    """
    found = launcher_scan.path_clobbers(SCRIPTS / "tests")
    by_file = {}
    for name, lineno, line in found:
        by_file.setdefault(name, []).append((lineno, line))

    assert set(by_file) == set(PINNED_PATH_CLOBBERS), (
        f"PATH-clobbering sites changed: found {sorted(by_file)}, pinned "
        f"{sorted(PINNED_PATH_CLOBBERS)}. A new one must be justified here; a "
        "vanished one must be unpinned.")
    for name, (needle, _why) in PINNED_PATH_CLOBBERS.items():
        assert any(needle in line for _ln, line in by_file[name]), (
            f"{name} still clobbers PATH but not in the pinned shape: "
            f"{by_file[name]}")


# 🔴 Every one of these was MISSED by the line-based scan this replaced, and each
# is a way to drop the ambient PATH — including two that are one refactor away
# from an existing pinned site (moving the PATH key before another, or adding a
# trailing comment). `PATH` is split across a `+` in the sources for the
# self-match reason above.
_CLOBBER_SHAPES = {
    "test_dict_literal.py":
        'env = {"PATH"' + ': "/somewhere/else"}\n',
    "test_later_key_inherits.py":
        'env = {"PATH"' + ': "/usr/bin/false", "HOME": os.environ["HOME"]}\n',
    "test_trailing_comment.py":
        'env["PATH"]' + ' = "/x"  # rebuilt from os.environ elsewhere\n',
    "test_dict_kwarg.py":
        'p = run(env=dict(PATH="/x"))\n',
    "test_setdefault.py":
        'env.setdefault("PATH"' + ', "/x")\n',
    "test_update_kwarg.py":
        'env.update(PATH="/x")\n',
}
_INHERITING_SHAPES = {
    "test_inherits.py":
        'env = {"PATH": str(tmp) + ":" + os.environ["PATH"]}\n',
    # MULTI-LINE — reading only the first line reports it as a clobber, which is
    # what this guard file's own code did.
    "test_inherits_across_lines.py":
        'env["PATH"] = os.pathsep.join(\n'
        '    p for p in os.environ["PATH"].split(os.pathsep) if p)\n',
    # Via a LOCAL HELPER's return value — the shape used six times in
    # test_analyze_service_index_commit.py.
    "test_inherits_via_helper.py":
        'def _shim(d):\n'
        '    return f"{d}:{os.environ[\'PATH\']}"\n'
        '\n'
        'p = run(store, PATH=_shim(tmp))\n',
}


def test_the_clobber_scan_can_actually_find_something(tmp_path):
    """POSITIVE CONTROLS for every shape a review measured as MISSED, and
    NEGATIVE controls beside them — a scan that reports the inheriting shapes
    too would drown the two real pins in noise and get itself deleted."""
    d = tmp_path / "tests"
    d.mkdir()
    for name, src in {**_CLOBBER_SHAPES, **_INHERITING_SHAPES}.items():
        (d / name).write_text(src, encoding="utf-8")

    reported = {h[0] for h in launcher_scan.path_clobbers(d)}
    assert reported == set(_CLOBBER_SHAPES), (
        f"missed: {set(_CLOBBER_SHAPES) - reported}; "
        f"false positives: {reported - set(_CLOBBER_SHAPES)}")


def test_the_clobber_scan_reads_conftest_too(tmp_path):
    """`conftest.py` is where the fixture that does the protecting LIVES, and it
    was outside the old `test_*.py` glob."""
    d = tmp_path / "tests"
    d.mkdir()
    (d / "conftest.py").write_text(
        'env = {"PATH"' + ': "/somewhere/else"}\n', encoding="utf-8")
    assert [h[0] for h in launcher_scan.path_clobbers(d)] == ["conftest.py"]


# --------------------------------------------------------------------------- #
# The fixture must not silently satisfy the runner's tool precondition
# --------------------------------------------------------------------------- #
def test_no_required_tool_is_satisfied_by_a_stub():
    """`run-tests.sh`'s GUARD 1 checks `command -v <tool>`, and its unit test
    checks `shutil.which(t) is not None` — from a process whose PATH now has 12
    stubs on it. No overlap today; this is what keeps it that way, instead of a
    comment saying so."""
    runner = (SCRIPTS / "run-tests.sh").read_text(encoding="utf-8")
    m = re.search(r"^REQUIRED_TOOLS=\((?P<body>[^)]*)\)", runner, re.M)
    assert m, "REQUIRED_TOOLS not found in run-tests.sh"
    required = set(m.group("body").split())
    stubbed = set(nolaunch.HOST_LAUNCHERS) | {"systemctl"}
    assert not (required & stubbed), (
        f"{sorted(required & stubbed)} is both a REQUIRED_TOOLS entry and a "
        "stub this fixture installs — the precondition would be satisfied by "
        "the stub and stop meaning anything")


# --------------------------------------------------------------------------- #
# MUTATION: prove the seam assertion is what makes the seam test red
# --------------------------------------------------------------------------- #
_HARNESS_CONFTEST = '''\
"""Harness conftest: the host stays PROTECTED, but the guard is pointed at an
EMPTY stub dir — the observable shape of "the launch did not land in the stub".

Never lets a real launcher run: a decoy stub dir (with its own log) is first on
PATH, so the subprocess under test still cannot reach systemd-run.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from testlib import nolaunch


@pytest.fixture(scope="session", autouse=True)
def no_real_launchers(tmp_path_factory):
    decoy = Path(tmp_path_factory.mktemp("decoy"))
    nolaunch.install(decoy)
    empty = Path(tmp_path_factory.mktemp("empty"))
    os.environ["PATH"] = str(decoy) + os.pathsep + os.environ.get("PATH", "")
    os.environ["DEVRC_TEST_LAUNCH_STUB_DIR"] = str(empty)
    yield empty
'''

_SEAM_TEST = "test_monitor_blackout_scheduling_reaches_the_stub_not_systemd"
# 🔴 SELF-MATCH. Assembled from two pieces so this line is not itself an
# occurrence of the needle — the same reason `testlib/shebang_scan.py` builds
# its patterns from character codes. Spelled whole, `src.count(...)` would be 2
# and the "exactly one site to mutate" check would fail on its own source.
_SEAM_NEEDLE = "assert len(scheduled) == " + "1, ("


def _harness_tree(tmp_path, source: str) -> Path:
    """A runnable copy of THIS file whose `testlib` and scripts resolve."""
    root = tmp_path / "scripts"
    (root / "tests").mkdir(parents=True)
    (root / "testlib").symlink_to(SCRIPTS / "testlib")
    for script in ("monitor-blackout.sh", "rig-control.sh"):
        (root / script).symlink_to(SCRIPTS / script)
    (root / "tests" / "conftest.py").write_text(_HARNESS_CONFTEST, encoding="utf-8")
    target = root / "tests" / "test_no_real_launchers.py"
    target.write_text(source, encoding="utf-8")
    return target


def _nested_env() -> dict:
    """The environment a nested pytest session must start from: UNPATCHED.

    🔴 Two reasons, both measured. (1) A child that inherits this session's
    prepended stub dir is already protected, so a mutant can pass on the
    PARENT's protection — that is how the first autouse pin went green on its
    own mutant. (2) The child's `install()` then resolves "the real systemctl"
    to the parent's STUB and chains its log into the parent's, which is
    harmless but makes two sessions share one file.
    """
    stub_dir = str(_stub_dir_from_env())
    env = {k: v for k, v in os.environ.items() if k != STUB_DIR_ENV}
    env["PATH"] = os.pathsep.join(
        p for p in os.environ["PATH"].split(os.pathsep) if p != stub_dir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run_nested(target: str, basetemp: Path) -> subprocess.CompletedProcess:
    """Run one nested pytest session.

    `--basetemp` is not cosmetic: without it each nested session pays pytest's
    temp-root housekeeping over an accumulating `/tmp/pytest-of-<user>` tree.
    MEASURED on the dev host with four nested sessions and a 108 MB temp root:
    22.3s without, 2.7s with.
    """
    return subprocess.run(
        [sys.executable, "-B", "-m", "pytest", target, "-q",
         "-p", "no:cacheprovider", "--basetemp", str(basetemp)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=300,
        env=_nested_env())


def _run_seam(target: Path) -> subprocess.CompletedProcess:
    return _run_nested(f"{target}::{_SEAM_TEST}",
                       target.parent.parent.parent / "basetemp")


def test_the_seam_assertion_is_what_makes_the_seam_test_red(tmp_path):
    """🔴 Break this file's own assertion on purpose; the seam case must go GREEN.

    An audit found this the one surviving mutant, and the "no test can
    meta-guard its own asserts" answer was wrong — `test_run_tests_floors.py`
    does exactly this, in this directory. Run as a PAIR under one env, because
    "the mutant went green" means nothing next to a control that never went red:

      control : intact test + an empty stub dir  -> RED, naming this assertion
      mutant  : assertion neutered               -> GREEN

    The host is never at risk in either half: the harness conftest still puts a
    working (decoy) stub dir first on PATH, so `systemd-run` is intercepted —
    what is simulated is the guard LOOKING at the wrong place, which is the same
    observable as the launch escaping.
    """
    src = Path(__file__).read_text(encoding="utf-8")
    assert src.count(_SEAM_NEEDLE) == 1, (
        f"expected exactly one seam assertion to mutate, found "
        f"{src.count(_SEAM_NEEDLE)} — the mutation would not land where intended")

    control = _run_seam(_harness_tree(tmp_path / "control", src))
    assert control.returncode != 0 and "1 failed" in control.stdout, (
        "the CONTROL half did not go red, so the mutant going green would prove "
        f"nothing.\n{control.stdout}")
    assert "expected monitor-blackout.sh's schedule_restore to hit the stub" in \
        control.stdout, control.stdout

    # Built from _SEAM_NEEDLE for the same self-match reason as the needle.
    mutated = src.replace(
        _SEAM_NEEDLE,
        "scheduled = scheduled or ['systemd-run --unit=monitor-blackout-restore-v2"
        " --on-active=2h']\n    " + _SEAM_NEEDLE)
    mutant = _run_seam(_harness_tree(tmp_path / "mutant", mutated))
    assert mutant.returncode == 0, (
        "with the seam assertion neutered the test is STILL red, so it is red "
        f"for some other reason and proves nothing about this assertion.\n"
        f"{mutant.stdout}")


# --------------------------------------------------------------------------- #
# MUTATION: the poller ledger must not be relaxable into exempting everything
# --------------------------------------------------------------------------- #
_LEDGER_TEST = "test_every_test_file_that_loads_the_poller_is_pinned"
# Self-match, assembled: spelled whole, `src.count()` below would be 2.
_LEDGER_NEEDLE = "assert set(found) == set(POLLER_LOADING_TESTS)" + ", ("

# Minimal files carrying the LOADER SHAPE the scan looks for — enough for
# `module_loaders` to report them, without dragging in the real suites.
_FAKE_LOADER = '_load("bar-status-poll", "m")\n'


def _ledger_harness(tmp_path, source: str) -> Path:
    """A tests dir holding the three pinned loaders PLUS one new reacher.

    The extra file is the event the ledger exists to notice: a new test file
    that loads the poller, and therefore does NOT inherit `test_bar_status.py`'s
    per-file `_toast_runner` patch.
    """
    root = tmp_path / "scripts"
    tests = root / "tests"
    tests.mkdir(parents=True)
    (root / "testlib").symlink_to(SCRIPTS / "testlib")
    (tests / "conftest.py").write_text(_HARNESS_CONFTEST, encoding="utf-8")
    for name in (*POLLER_LOADING_TESTS, "test_a_brand_new_reacher.py"):
        (tests / name).write_text(_FAKE_LOADER, encoding="utf-8")
    target = tests / "test_no_real_launchers.py"
    target.write_text(source, encoding="utf-8")
    return target


def test_the_ledger_equality_is_what_makes_a_new_reacher_red(tmp_path):
    """🔴 A ledger relaxed to a SUBSET absorbs the next reacher in silence.

    MEASURED as a surviving mutant before this test existed: changing the
    ledger's `==` to `>=` and adding a new poller-loading test file left the
    whole guard file green — the exact "acknowledgement absorbs a new reacher"
    failure that `ACKNOWLEDGED_UNSTUBBED` was already bound to file sets for.

    Run as a PAIR, because "the mutant went green" means nothing without a
    control that went red for the RIGHT reason:

      control : `==` + an extra reacher in the tree -> RED, naming the ledger
      mutant  : `>=` + the same tree               -> GREEN

    Nothing can launch in either half: the harness conftest still puts a working
    stub dir first on PATH, and these nested files only parse source.
    """
    src = Path(__file__).read_text(encoding="utf-8")
    assert src.count(_LEDGER_NEEDLE) == 1, (
        f"expected exactly one ledger assertion to mutate, found "
        f"{src.count(_LEDGER_NEEDLE)} — the mutation would not land where intended")

    def run(where, source):
        target = _ledger_harness(tmp_path / where, source)
        return _run_nested(f"{target}::{_LEDGER_TEST}",
                           tmp_path / where / "basetemp")

    control = run("control", src)
    assert control.returncode != 0 and "1 failed" in control.stdout, (
        "the CONTROL half did not go red, so the mutant going green would prove "
        f"nothing.\n{control.stdout}")
    assert "test_a_brand_new_reacher.py" in control.stdout, (
        "the control went red without naming the new reacher, so it is red for "
        f"some other reason.\n{control.stdout}")

    mutant = run("mutant", src.replace(
        _LEDGER_NEEDLE, "assert set(found) >= set(POLLER_LOADING_TESTS)" + ", ("))
    assert mutant.returncode == 0, (
        "with the ledger relaxed to a subset the test is STILL red, so it is "
        f"red for some other reason and proves nothing.\n{mutant.stdout}")


# --------------------------------------------------------------------------- #
# THE SYNTHETIC HOST — positive controls for the arm this machine cannot take
# --------------------------------------------------------------------------- #
# 🔴 WHAT THESE ARE FOR, stated so nobody reads them as duplicate coverage. Six
# `if _real_binary("systemctl") is None:` bodies in this file were measured by
# `scripts/dead-guard-scan.py` as NEVER EXECUTED: this host has a real
# systemctl, so only the other arm ever ran, and the arm that carries the whole
# claim for the nix sandbox tier had nothing driving it. Each site had a
# `# pragma: no cover` saying precisely that, which is a note about a gap, not a
# closing of it.
#
# The arms now live in `check_systemctl_stub_matches_host` and
# `unless_the_host_has_no_systemctl`, and the tests below drive them with a
# SYNTHETIC (real, resolved, stub) triple. Everything here is an ordinary
# in-process call — no `subprocess.run`, deliberately: `sys.settrace` is
# per-interpreter, so a control that spawned a child would leave the branch
# recorded as never-executed and close nothing.
#
# Each control asserts on THIS guard's own message, not merely that "something
# raised" — a neighbouring assertion firing instead would fail the `match=`.
#
# 🔴 EVERY SYNTHETIC PATH IS BUILT FROM `tmp_path`, NEVER WRITTEN AS A LITERAL.
# `test_no_real_launchers_all_targets.py::test_the_absolute_path_sites_are_
# pinned` pins the set of absolute launcher-path LITERALS in the tree both ways,
# because a literal like "/usr/bin/systemctl" performs no PATH lookup and so no
# stub dir can shadow it. A first draft of these controls wrote exactly that
# spelling and turned that pin red — correctly. Nothing here is ever executed,
# but the pin is about the SHAPE, and adding an exemption to it to make a
# convenience literal legal would be the wrong direction entirely.
_NO_SYSTEMCTL_MSG = "no real systemctl, yet something answers to the name"
_STUB_WITHOUT_A_HOST_MSG = (
    "a systemctl stub was installed on a host that has no systemctl")


def test_the_no_systemctl_arm_fires_when_a_stub_was_installed_anyway(tmp_path):
    """`check_systemctl_stub_matches_host`, `real is None`, FIRST assertion.

    A stub present on a host that has no systemctl is the behaviour change the
    guard exists to refuse: `command -v systemctl` starts succeeding and every
    script under test takes a different branch.

    🔴 `resolved=None` ISOLATES THE MUTATION. The realistic pairing is
    `resolved=str(stub)` — a stub on PATH does resolve — but MEASURED with that
    input, neutering `stub.exists()` killed this test via the SECOND assertion
    in the same arm (`no real systemctl, yet something answers to the name`),
    i.e. the mutant died to a neighbouring guard and this one's own firing was
    never observed. Feeding an unresolved stub leaves exactly one assertion in
    the arm that can fail.
    """
    # `write_exec`, not a hand-written shebang: `test_runtime_shebangs.py`
    # pins that no test writes its own, and the first draft of this control
    # tripped it. Same family as the absolute-literal note above — both guards
    # live in OTHER files, so a green run of this one could not see either.
    stub = tmp_path / "systemctl"
    write_exec(stub, "exit 0\n")

    with pytest.raises(AssertionError, match=re.escape(_STUB_WITHOUT_A_HOST_MSG)):
        check_systemctl_stub_matches_host(None, stub, tmp_path, resolved=None)

    # The clean half of the same arm: no real systemctl, no stub, nothing
    # answering — this must pass, or the control above is red for a reason that
    # has nothing to do with the stub.
    check_systemctl_stub_matches_host(
        None, tmp_path / "absent-systemctl", tmp_path, resolved=None)


def test_the_no_systemctl_arm_fires_when_something_still_answers(tmp_path):
    """`check_systemctl_stub_matches_host`, `real is None`, SECOND assertion —
    reached only once the stub-absence assertion above has passed, so it needs
    its own input: no stub file, yet `which` resolves.

    That is the shape a PATH entry outside the fixture's stub dir produces, and
    the first assertion is blind to it.
    """
    elsewhere = tmp_path / "some-other-path-entry" / "systemctl"
    with pytest.raises(AssertionError, match=re.escape(_NO_SYSTEMCTL_MSG)):
        check_systemctl_stub_matches_host(
            None, tmp_path / "absent-systemctl", tmp_path,
            resolved=str(elsewhere))


def test_the_dispatcher_asserts_instead_of_running_the_body_with_no_systemctl(
        tmp_path):
    """`unless_the_host_has_no_systemctl` — the arm shared by four tests.

    Three claims, because two of them are each other's control:
      * with nothing answering, the body is NOT run and nothing is raised;
      * with something answering, THIS guard's message is raised;
      * with a real systemctl, the body IS run — without which a silent "the
        body did not run" would be indistinguishable from a dispatcher wired to
        nothing, and every assertion in the four tests it fronts would be
        vacuous.
    """
    ran = []
    ambient = str(tmp_path / "ambient-bin" / "systemctl")
    shadowing = str(tmp_path / "stub-bin" / "systemctl")

    unless_the_host_has_no_systemctl(None, None, lambda: ran.append("body"))
    assert ran == [], (
        "the body ran on a host with no systemctl — the four tests this fronts "
        "would try to drive a binary that does not exist")

    with pytest.raises(AssertionError, match=re.escape(_NO_SYSTEMCTL_MSG)):
        unless_the_host_has_no_systemctl(
            None, ambient, lambda: ran.append("body"))
    assert ran == [], "the body ran even though the guard fired"

    unless_the_host_has_no_systemctl(
        ambient, shadowing, lambda: ran.append("body"))
    assert ran == ["body"], (
        "the body did NOT run on a host that has a real systemctl, so this "
        "dispatcher is wired to nothing and every test it fronts is vacuous")

