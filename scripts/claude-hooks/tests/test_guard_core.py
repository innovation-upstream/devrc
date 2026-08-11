#!/usr/bin/env python3
"""Tests for guard_core.py — the shared, caller-agnostic command guard.

WHAT THIS FILE IS FOR

  1. 🔴 Pinning that the "claude-code" policy is EXACTLY the six checks
     bash-guard.py has always run. That hook fires on every Bash call in every
     Claude Code session on both hosts; a new check landing there by accident is
     a change to the operator's primary tool. Pinned by FUNCTION NAME against a
     literal list, so adding a check to the shared core cannot leak into it.

  2. The MULTI-SPELLING matrix for the new argv-based checks. The previous
     glob-era suite pinned ONE spelling per pattern — always the one the pattern
     was written around — which is exactly why it was blind to
     `talosctl -n <ip> reset`. Every rule here is exercised bare, `VAR=`-
     prefixed, `sudo `/`doas `/`env `/`timeout `-wrapped, flag-interleaved,
     long-flag, `git -C <path> `-hopped, inside `bash -c '…'`, and after each of
     the five command separators.

  3. Negative cases: the near-miss spellings that must STAY allowed, so the
     guard is not merely a blanket refusal wearing a parser costume.

    run:  python -m pytest scripts/claude-hooks/tests/test_guard_core.py -q

Companion: scripts/claude-hooks/tests/test_bash_guard.py drives the Claude Code
adapter end-to-end through a subprocess and is the regression suite for every
raw-text bypass the original six checks close. It must stay green unchanged.
"""
import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import guard_core as gc  # noqa: E402


# --------------------------------------------------------------------------- #
# 0. harness self-validation (negative controls)
#
# 🔴 A harness that reports green while testing nothing is worse than no test.
# INVARIANT GUARDS on the harness — not regression coverage for any shipped bug.
# --------------------------------------------------------------------------- #
def test_evaluate_can_return_none():
    """If `evaluate` denied everything, every deny assertion below would pass
    for the wrong reason."""
    assert gc.evaluate("ls -la", "opencode") is None


def test_evaluate_can_return_a_reason():
    assert gc.evaluate("talosctl reset", "opencode") is not None


def test_unknown_policy_raises_rather_than_running_nothing():
    """A typo'd policy name must not silently degrade to "no checks"."""
    with pytest.raises(KeyError):
        gc.evaluate("talosctl reset", "opencodee")


# --------------------------------------------------------------------------- #
# 1. 🔴 policy composition — the Claude-Code-unchanged pin
# --------------------------------------------------------------------------- #
CLAUDE_CODE_EXPECTED = [
    "check_git_add_all",
    "check_git_reset_hard",
    # 🔴 Added 2026-08-02 by explicit operator decision — the SEVENTH check, and
    # the first ever added to this policy. The raw-text `check_git_reset_hard`
    # above is anchored on `\bgit\s+reset\b`, so it never saw
    # `git -C <path> reset --hard`: the worktree-first spelling RULES.md
    # MANDATES. Measured against the live hook before the move —
    #   DENY  `git reset --hard origin/main`
    #   ALLOW `git -C /tmp/wt reset --hard origin/main`
    # — i.e. the irreversible guard was blind to the spelling the rules push
    # agents toward, while `check_git_add_all` already handled that same hop.
    # It sits BEFORE check_cd_then_git deliberately: that ordering is what makes
    # `cd /x && git -C <p> reset --hard` report the reset reason rather than the
    # cd reason. See test_reset_hard_deny_is_attributed_to_the_reset_check.
    "check_git_reset_hard_argv",
    # 🔴 Added 2026-08-02 by explicit operator decision — the EIGHTH and NINTH.
    # Both are 🔴 CRITICAL in RULES.md and both were measured ALLOW against the
    # live ~/.claude/hooks/bash-guard.py before the move, with `git add -A` and
    # `git reset --hard HEAD` as DENY positive controls in the same sweep:
    #   ALLOW  git stash / git stash push -m wip / git stash pop
    #   ALLOW  git -C <repo> stash
    #   ALLOW  git clean -fd
    # `git stash` carries a documented incident (two parallel subagents stole
    # each other's work, 2026-07-25) and the ban was re-BROADENED 2026-08-01
    # after a subagent read the narrow wording and stashed anyway. `git clean
    # -f` deletes exactly the untracked handoff docs RULES calls unsaved work.
    # Neither has a benign in-repo use; the READ spellings (`stash list`,
    # `stash show`, `clean -n`) are untouched.
    # They sit BEFORE check_cd_then_git for the same reason the reset argv check
    # does — `cd /x && git stash` reports the stash reason, not the cd reason.
    "check_git_stash",
    "check_git_clean_force",
    # 🔴 Added later on 2026-08-02 by explicit operator decision — the TENTH,
    # ELEVENTH and TWELFTH. The three device/cluster-destruction families, each
    # measured ALLOW against the live ~/.claude/hooks/bash-guard.py before the
    # move, with `git add -A` and `git reset --hard` as DENY positive controls
    # in the same sweep:
    #   ALLOW  talosctl -n 192.168.50.94 reset
    #   ALLOW  mkfs.ext4 /dev/sdc
    #   ALLOW  dd if=/dev/zero of=/dev/sdc
    # None has a benign use from an agent here; the read/inspect neighbours
    # (`talosctl version`, `talosctl -n <ip> get members`, file-to-file `dd`,
    # `dd of=/dev/null`) do not match, and `mkfs` matches on the PROGRAM name so
    # naming it in an argument is not a command. See section 3c.
    # They sit BEFORE check_heredoc_to_file / check_cd_then_git so a command
    # tripping both reports the device-destruction reason.
    "check_talosctl_reset",
    "check_mkfs",
    "check_dd_to_block_device",
    # 🔴 Added 2026-08-10 by explicit operator decision — the THIRTEENTH and
    # FOURTEENTH. Both are 🔴 in RULES.md and both were measured ALLOW against the
    # live ~/.claude/hooks/bash-guard.py before the move, with `git add --all` and
    # `git stash` as DENY positive controls in the same sweep:
    #   ALLOW  git commit -m "wip"                       (cwd = devrc, on `main`)
    #   ALLOW  git commit --amend --no-edit
    #   ALLOW  git -C /home/zach/workspace/devrc commit -m "wip"
    #   ALLOW  pkill -f e2e/run.sh
    # commit-to-main is the clearest case in the rulebook where PROSE FAILED: 🔴 in
    # three separate files, violated twice in four days anyway (2026-08-06, two
    # un-pushed commits blocked `ship.sh` for hours; 2026-08-09, three more,
    # rescued as PR #366).
    # They sit AFTER the device-destruction checks and BEFORE check_heredoc_to_file
    # / check_cd_then_git — see test_commit_to_main_outranks_the_cd_reason and
    # test_node_wipe_still_outranks_the_commit_reason, which pin BOTH directions.
    "check_git_commit_to_main",
    "check_pkill_full_pattern",
    "check_heredoc_to_file",
    "check_cd_then_git",
    "check_private_key",
    "check_secret_or_ip_publish",
]


def test_claude_code_policy_is_pinned_by_name():
    """🔴 bash-guard.py runs this policy on EVERY Bash call in EVERY Claude Code
    session on both hosts. Adding a check here changes the operator's primary
    tool and must be an explicit, reported decision.

    Pinned by NAME against a literal list — deriving the expectation from
    guard_core's own `_CLAUDE_CODE_CHECKS` would make this test agree with
    whatever the implementation happens to say.
    """
    got = [f.__name__ for f in gc.POLICIES["claude-code"]]
    assert got == CLAUDE_CODE_EXPECTED, (
        f"the claude-code policy is {got}, expected {CLAUDE_CODE_EXPECTED}. If "
        f"this is intentional, it is a behaviour change to Claude Code on both "
        f"hosts — say so in the PR body so it can be vetoed."
    )


def test_bash_guard_adapter_uses_the_claude_code_policy():
    src = (Path(__file__).resolve().parents[1] / "bash-guard.py").read_text()
    assert 'POLICY = "claude-code"' in src


def test_opencode_policy_is_a_strict_superset():
    """opencode gets the fuller set — it must never LOSE a claude-code check."""
    oc = [f.__name__ for f in gc.POLICIES["opencode"]]
    assert oc[: len(CLAUDE_CODE_EXPECTED)] == CLAUDE_CODE_EXPECTED
    assert set(CLAUDE_CODE_EXPECTED) < set(oc)


# 🔴 THE ONE CHECK THAT REMAINS opencode-ONLY — and this list is expected to
# STAY a list of one.
#
# `check_git_stash` + `check_git_clean_force` were listed here and moved into
# the claude-code policy on 2026-08-02; `check_talosctl_reset`, `check_mkfs` and
# `check_dd_to_block_device` followed later the same day. `check_rm_rf_critical`
# was held back IN THAT SAME CHANGE, on purpose — it is not the residue of an
# unfinished migration:
#
#   `rm -rf` has legitimate, FREQUENT use on these hosts (build directories,
#   node_modules, .direnv, throwaway worktrees, /tmp scratch trees). The check
#   is narrow — only `/`, `~`/`$HOME`, `.`/`..` and the top-level system dirs —
#   but narrow is not never, and a guard that fires during routine cleanup
#   trains its subject to route around it. A routed-around guard is worse than
#   no guard because it still reports safety. Claude Code also falls back to a
#   PROMPT the operator sees, which is exactly the control opencode lacks
#   (`opencode run` auto-rejects an `ask`), so the deny buys much less here.
#
# 🔴 Do not "finish the job" by moving it. That is its own operator decision and
# it needs its own evidence — a measurement of how often the fatal-target set
# would actually fire on real sessions, not the observation that it is the last
# one left.
IRREVERSIBLE_EXPECTED = [
    "check_rm_rf_critical",
]


def test_opencode_policy_carries_the_irreversible_checks():
    oc = [f.__name__ for f in gc.POLICIES["opencode"]]
    assert oc[len(CLAUDE_CODE_EXPECTED):] == IRREVERSIBLE_EXPECTED


@pytest.mark.parametrize("command", [
    "rm -rf /",
    "rm -rf $HOME",
    "sudo rm -rf /etc",
    "rm -fr /usr",
    # NOTE: `git -C <path> reset --hard` USED to be listed here, asserting that
    # Claude Code allowed it. That was the gap, not a feature — it is now denied
    # under both policies and is asserted positively in section 1b below.
    # NOTE: `git stash push -m wip` and `git clean -fd` were listed here for the
    # same reason and moved for the same reason — see section 1c.
    # NOTE: `talosctl -n <ip> reset`, `mkfs.ext4 /dev/sda` and
    # `dd if=x of=/dev/sda` were listed here for the same reason and moved for
    # the same reason — see section 3c.
])
def test_the_new_checks_do_not_leak_into_claude_code(command):
    """🔴 The blast-radius assertion, by OUTCOME rather than by list membership.

    Each of these is denied under "opencode" and must resolve to no-deny under
    "claude-code" — the ONE family that stayed opencode-only, `rm -rf` of a
    critical path. This is the fence that keeps a later "while I'm here"
    widening honest, and after 2026-08-02 it is the whole fence: if these rows
    start denying under claude-code, someone finished a job that was
    deliberately left unfinished. Read IRREVERSIBLE_EXPECTED above first.
    """
    assert gc.evaluate(command, "opencode") is not None
    assert gc.evaluate(command, "claude-code") is None


# --------------------------------------------------------------------------- #
# 1b. 🔴 `git reset --hard` under the CLAUDE-CODE policy
#
# Closes a gap that was live on both hosts until 2026-08-02. `check_git_add_all`
# had always handled the `git [global-opts] <verb>` hop; the raw-text
# `check_git_reset_hard` never did, so the IRREVERSIBLE one of the two was blind
# to exactly the spelling RULES.md mandates. Probed against the live
# ~/.claude/hooks/bash-guard.py before the change:
#
#     DENY   git reset --hard origin/main
#     ALLOW  git -C /tmp/wt reset --hard origin/main      <- RULES-mandated
#     ALLOW  sudo -n git -C /tmp/wt reset --hard HEAD~5
#     DENY   git add -A
#     DENY   git -C /tmp/wt add -A                        <- add DOES hop
#
# Every deny test below is asserted BY REASON, not merely by "something denied".
# Three of these commands are ALSO caught by an unrelated check (check_cd_then_git
# for the `cd &&` form, and the raw-text check for any spelling where the literal
# bytes "git reset" happen to appear), so a bare `is not None` would pass with
# this change fully reverted — green for the wrong reason.
# --------------------------------------------------------------------------- #
RESET_REASON_MARK = "`git reset --hard` is blocked"


def _deny_reason(command, policy="claude-code"):
    reason = gc.evaluate(command, policy)
    assert reason is not None, f"{command!r} was ALLOWED under {policy}"
    return reason


def _assert_denied_as_reset_hard(command, policy="claude-code"):
    """Deny AND attribute it to a reset-hard check.

    The attribution is the load-bearing half: `cd /x && git -C <p> reset --hard`
    is denied by check_cd_then_git with this change reverted, so asserting only
    that it denies pins nothing about reset --hard at all.
    """
    reason = _deny_reason(command, policy)
    assert RESET_REASON_MARK in reason, (
        f"{command!r} denied under {policy}, but for the WRONG reason — got "
        f"{reason[:120]!r}. Some other check fired first; this asserts nothing "
        f"about reset --hard coverage."
    )
    return reason


# Deny fixtures. Paths are pairwise DISTINCT and — 🔴 critically — none of them
# ends in `.git`. A `--git-dir=/tmp/d/.git` fixture makes the raw command text
# contain the substring "git reset" (from ".git reset"), so the frozen raw-text
# regex matches the PATH and the case passes on unpatched code. That exact
# fixture silently masked the --git-dir gap during this work until the paths
# were made honest.
RESET_HARD_DENY = [
    "git reset --hard",
    "git -C /srv/repos/alpha reset --hard",
    "git -c core.fileMode=false reset --hard",
    "git --git-dir=/var/lib/beta reset --hard",
    "git --git-dir /opt/gamma reset --hard",
    "git --work-tree=/home/u/delta reset --hard",
    "sudo -n git -C /mnt/epsilon reset --hard HEAD~5",
    "DEBUG=1 git -C /data/zeta reset --hard",
    "cd /srv/eta && git -C /srv/repos/theta reset --hard",
    # `--hard` AFTER a ref — the flag is not adjacent to the subcommand at all
    "git -C /usr/local/iota reset origin/main --hard",
    "git -C /tmp/kappa reset --hard; echo done",
    "bash -c 'git -C /tmp/lambda reset --hard'",
    "/usr/bin/git -C /tmp/mu reset --hard",
]


@pytest.mark.parametrize("command", RESET_HARD_DENY)
def test_reset_hard_deny_is_attributed_to_the_reset_check(command):
    """🔴 RED at origin/main for every row except the two the raw-text check
    already covered (`git reset --hard`, which needs no hop). Attribution by
    reason is what makes the `cd &&` row a real regression test: at origin/main
    it denies with check_cd_then_git's message, so this assertion fails there.
    """
    _assert_denied_as_reset_hard(command, "claude-code")


@pytest.mark.parametrize("command", RESET_HARD_DENY)
def test_reset_hard_still_denied_under_opencode(command):
    """The move must not COST opencode anything. `POLICIES["opencode"]` is
    `_CLAUDE_CODE_CHECKS + _IRREVERSIBLE_CHECKS`, so the check is inherited
    rather than duplicated — this asserts the inheritance actually holds.
    """
    _assert_denied_as_reset_hard(command, "opencode")


def test_reset_hard_reason_is_reported_once_not_twice():
    """Both reset checks can match the same command. `evaluate` returns on the
    FIRST hit, so exactly one reason comes back — no double-reporting, and the
    message existing denials already produced is unchanged."""
    cmd = "git reset --hard origin/main"
    matching = [c.__name__ for c in gc.POLICIES["claude-code"] if c(cmd)]
    assert matching == ["check_git_reset_hard", "check_git_reset_hard_argv"], matching
    assert gc.evaluate(cmd, "claude-code") == gc.check_git_reset_hard(cmd)


def test_both_reset_checks_are_present_and_neither_is_redundant():
    """They cover different shapes. Deleting either loses real coverage:
      - raw-text only: `git -C <p> reset --hard` slips through;
      - argv only: quoted prose (below) stops being caught.
    """
    names = [c.__name__ for c in gc.POLICIES["claude-code"]]
    assert "check_git_reset_hard" in names and "check_git_reset_hard_argv" in names
    # raw-text catches what argv cannot
    assert gc.check_git_reset_hard("echo 'never use git reset --hard'") is not None
    assert gc.check_git_reset_hard_argv("echo 'never use git reset --hard'") is None
    # argv catches what raw-text cannot
    assert gc.check_git_reset_hard("git -C /srv/repos/nu reset --hard") is None
    assert gc.check_git_reset_hard_argv("git -C /srv/repos/nu reset --hard") is not None


# --- the ALLOW half -------------------------------------------------------- #
# 🔴 Built independently of the deny list — different paths, different refs,
# different flags — so a copy-paste symmetry cannot make both halves agree with
# the same bug. This half is what decides whether the new deny is safe to live
# with: `git reset --soft/--mixed` and `git -C <p> reset <ref>` are ordinary,
# non-destructive traffic and must not become collateral.
RESET_ALLOW = [
    "git reset",
    "git reset --soft HEAD~1",
    "git reset --mixed",
    "git reset HEAD -- src/main.py",
    "git -C /opt/service-xi reset HEAD~1",
    "git -C /opt/service-xi reset --soft HEAD~3",
    "git -c user.name=bot reset --mixed HEAD",
    "git --git-dir=/net/omicron reset --keep HEAD~1",
    "git -C /opt/service-xi log --oneline -3",
    "git -C /opt/service-xi diff --stat",
    "git restore src/app.ts",
    "git restore --staged docs/README.md",
    "git checkout -- src/app.ts",
    "git checkout origin/main -- config/prod.yaml",
    "git revert abc1234",
    "git reflog -20",
]


@pytest.mark.parametrize("command", RESET_ALLOW)
def test_non_destructive_reset_and_neighbours_stay_allowed(command):
    """INVARIANT GUARD (green before and after) — the false-positive fence.

    Not regression coverage for any bug: it pins that closing the gap did not
    widen into ordinary traffic. `--soft`/`--mixed`/`--keep` do not touch the
    working tree, and `git -C <p> reset <ref>` is the everyday index reset.
    """
    assert gc.evaluate(command, "claude-code") is None
    assert gc.evaluate(command, "opencode") is None


# --- pinned pre-existing behaviour ----------------------------------------- #
@pytest.mark.parametrize("command", [
    "echo 'never use git reset --hard'",
    "grep -rn 'git reset --hard' RULES.md",
])
def test_quoted_prose_mentioning_reset_hard_stays_denied(command):
    """INVARIANT GUARD, and a deliberate NON-fix.

    The raw-text check matches these today and must keep doing so. This is the
    accepted false positive the module docstring defends: a `_strip_message_text`
    helper that would have "fixed" it was built and REVERTED in PR #217 after
    three audit rounds each found a hole that let a genuinely-executing
    destructive command through. Deciding which bytes are inert message vs.
    executable command is shell parsing; regexes cannot do it.

    The workaround costs one step — write the text to a file and use
    `git commit -F <file>` / `gh pr create --body-file <file>`.

    Do NOT "improve" this by making these allow.
    """
    reason = _assert_denied_as_reset_hard(command, "claude-code")
    assert "commit -F" in reason  # the escape hatch is spelled out to the caller


def test_git_dir_ending_in_dot_git_passes_for_the_WRONG_reason():
    """🔴 Fixture-hygiene pin, kept as a warning to the next author.

    `--git-dir=/tmp/d/.git` puts the literal bytes "git reset" into the command
    text, so the RAW-TEXT check matches the PATH. Any --git-dir test written with
    a `.git`-suffixed value therefore passes with the argv check removed
    entirely, and proves nothing. The honest fixtures above avoid `.git`.
    """
    accidental = "git --git-dir=/tmp/d/.git reset --hard"
    assert gc.check_git_reset_hard(accidental) is not None  # matches the PATH
    honest = "git --git-dir=/tmp/d/objects-only reset --hard"
    assert gc.check_git_reset_hard(honest) is None          # raw text cannot see it
    assert gc.check_git_reset_hard_argv(honest) is not None  # argv can


def test_add_all_and_reset_hard_now_agree_about_the_global_option_hop():
    """The asymmetry that WAS the bug, pinned as a symmetry.

    `git -C <p> add -A` was denied while `git -C <p> reset --hard` was allowed —
    the guard was stricter about the recoverable action than the irreversible one.
    """
    for cmd in ("git -C /srv/repos/pi add -A", "git -C /srv/repos/pi reset --hard"):
        assert gc.evaluate(cmd, "claude-code") is not None, cmd


# --------------------------------------------------------------------------- #
# 2. the parser
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,expected_first_argv", [
    ("talosctl reset", ["talosctl", "reset"]),
    ("FOO=1 talosctl reset", ["talosctl", "reset"]),
    ("FOO=1 BAR=2 talosctl reset", ["talosctl", "reset"]),
    ("sudo talosctl reset", ["talosctl", "reset"]),
    ("sudo -n talosctl reset", ["talosctl", "reset"]),
    ("sudo -u root talosctl reset", ["talosctl", "reset"]),
    ("doas talosctl reset", ["talosctl", "reset"]),
    ("env FOO=1 talosctl reset", ["talosctl", "reset"]),
    ("timeout 90 talosctl reset", ["talosctl", "reset"]),
    ("nohup talosctl reset", ["talosctl", "reset"]),
    ("setsid talosctl reset", ["talosctl", "reset"]),
    ("command talosctl reset", ["talosctl", "reset"]),
    ("/usr/bin/talosctl reset", ["/usr/bin/talosctl", "reset"]),
    ("KUBECONFIG=$KC_HOMELAB sudo -n timeout 60 talosctl -n 1.2.3.4 reset",
     ["talosctl", "-n", "1.2.3.4", "reset"]),
])
def test_peeling_wrappers_and_assignments(text, expected_first_argv):
    """🔴 The whole point. Every one of these is the SAME action, and no glob
    spelling enumerates them."""
    assert gc.commands(text)[0] == expected_first_argv


@pytest.mark.parametrize("sep", ["&&", "||", ";", "|", "&", "\n"])
def test_every_separator_splits_commands(sep):
    argvs = gc.commands(f"echo hi {sep} talosctl reset")
    assert ["talosctl", "reset"] in argvs


def test_quoted_separators_do_not_split():
    """A separator inside quotes is data, not an operator."""
    argvs = gc.commands("echo 'a && b'")
    assert argvs == [["echo", "a && b"]]


def test_command_substitution_bodies_are_inspected():
    assert ["talosctl", "reset"] in gc.commands("echo $(talosctl reset)")
    assert ["talosctl", "reset"] in gc.commands("echo `talosctl reset`")


@pytest.mark.parametrize("wrapper", [
    "bash -c '{}'", 'sh -c "{}"', "zsh -c '{}'", "eval '{}'",
])
def test_nested_shell_bodies_are_inspected(wrapper):
    assert ["talosctl", "reset"] in gc.commands(wrapper.format("talosctl reset"))


def test_tokeniser_survives_an_unbalanced_quote():
    """A truncated heredoc leaves unlexable text. The guard must degrade to a
    crude split, never to an exception (which the caller would surface as a
    fail-closed refusal of an innocent command)."""
    assert gc.commands("git commit -m 'unterminated") != []


def test_recursion_is_bounded():
    """A hand-crafted nesting bomb must not spin the guard."""
    text = "talosctl reset"
    for _ in range(12):
        text = f"bash -c \"{text}\""
    gc.commands(text)  # must return, and quickly


# --------------------------------------------------------------------------- #
# 3. 🔴 the multi-spelling decision matrix for the new checks
#
# Each rule is exercised across an outer product of PREFIXES x spellings, not
# one canonical form. The glob-era suite's blindness came from pinning exactly
# the spelling the pattern was written around.
# --------------------------------------------------------------------------- #
PREFIXES = ["", "FOO=1 ", "sudo ", "sudo -n ", "doas ", "env FOO=1 ",
            "timeout 60 ", "nohup ", "KUBECONFIG=$KC_HOMELAB sudo "]
SEPARATOR_TEMPLATES = ["echo ok && {}", "echo ok; {}", "echo ok | {}",
                       "echo ok & {}", "false || {}", "bash -c '{}'",
                       "(cd /tmp && {})"]

# --- talosctl reset --------------------------------------------------------- #
TALOS_RESET_SPELLINGS = [
    "talosctl reset",
    "talosctl reset --graceful=false",
    "talosctl reset --system-labels-to-wipe STATE --system-labels-to-wipe EPHEMERAL",
    # 🔴 THE spelling the glob missed: the node selector sits BETWEEN the tool
    # and the verb, so `*talosctl reset*` never matched and it resolved ALLOW.
    "talosctl -n 192.168.50.94 reset",
    "talosctl --nodes 192.168.50.94 reset",
    "talosctl --nodes=192.168.50.94 reset",
    "talosctl -e 1.2.3.4 -n 5.6.7.8 reset",
    "talosctl -n 192.168.50.94 reset --graceful=false --reboot",
    "talosctl --talosconfig /tmp/tc -n 1.2.3.4 reset",
]


@pytest.mark.parametrize("spelling", TALOS_RESET_SPELLINGS)
@pytest.mark.parametrize("prefix", PREFIXES)
def test_talosctl_reset_is_denied_in_every_spelling(prefix, spelling):
    assert gc.check_talosctl_reset(prefix + spelling) is not None


@pytest.mark.parametrize("template", SEPARATOR_TEMPLATES)
def test_talosctl_reset_is_denied_behind_every_separator(template):
    assert gc.check_talosctl_reset(template.format("talosctl -n 1.2.3.4 reset")) is not None


@pytest.mark.parametrize("command", [
    "talosctl -n 192.168.50.94 get members",
    "talosctl -n 192.168.50.94 dmesg",
    "talosctl health",
    "talosctl get resetstatus",           # not a BARE `reset` token
    "talosctl -n 1.2.3.4 read /proc/uptime",
    'git commit -m "never talosctl reset a node"',   # argv[0] is git
    "grep -rn 'talosctl reset' docs/",               # argv[0] is grep
    "echo 'talosctl reset is banned'",
])
def test_talosctl_non_reset_stays_allowed(command):
    """🔴 Includes the cases that make this a PARSER and not a substring match:
    a commit message and a grep pattern both contain the exact blocked text and
    are correctly untouched, because argv[0] is not `talosctl`. This is NOT the
    reverted `_strip_message_text()` — no bytes are blanked; the quoted argument
    is simply one token."""
    assert gc.check_talosctl_reset(command) is None


# --- mkfs ------------------------------------------------------------------- #
MKFS_SPELLINGS = ["mkfs.ext4 /dev/sda1", "mkfs -t xfs /dev/sdb", "mkfs.xfs -f /dev/nvme0n1",
                  "mke2fs /dev/sdc", "mkswap /dev/sdd", "/sbin/mkfs.ext4 /dev/sda"]


@pytest.mark.parametrize("spelling", MKFS_SPELLINGS)
@pytest.mark.parametrize("prefix", PREFIXES)
def test_mkfs_is_denied_in_every_spelling(prefix, spelling):
    assert gc.check_mkfs(prefix + spelling) is not None


@pytest.mark.parametrize("command", [
    "ls /sbin/mkfs.ext4",
    "man mkfs",
    "echo 'do not run mkfs'",
    "rg mkfs scripts/",
])
def test_mkfs_lookalikes_stay_allowed(command):
    assert gc.check_mkfs(command) is None


# --- dd to a block device --------------------------------------------------- #
DD_BAD = ["dd if=/dev/zero of=/dev/sda bs=1M",
          "dd of=/dev/sda if=/dev/zero",              # operand order swapped
          "dd if=image.img of=/dev/nvme0n1 status=progress",
          "dd bs=4M if=x of=/dev/sdb1 conv=fsync"]


@pytest.mark.parametrize("spelling", DD_BAD)
@pytest.mark.parametrize("prefix", PREFIXES)
def test_dd_to_a_block_device_is_denied(prefix, spelling):
    assert gc.check_dd_to_block_device(prefix + spelling) is not None


@pytest.mark.parametrize("command", [
    "dd if=/dev/sda of=/tmp/backup.img bs=1M",     # READING a device is fine
    "dd if=/dev/urandom of=/dev/null count=1",
    "dd if=x of=/dev/stdout",
    "dd if=x of=./out.img",
    "dd if=x of=/dev/fd/1",
])
def test_dd_safe_targets_stay_allowed(command):
    assert gc.check_dd_to_block_device(command) is None


# --- rm -r of a catastrophic target ----------------------------------------- #
RM_FATAL_SPELLINGS = [
    "rm -rf /", "rm -fr /", "rm -Rf /", "rm -r -f /", "rm -f -r /",
    "rm --recursive --force /", "rm -rf /*", "rm -rf ~", "rm -rf ~/",
    "rm -rf $HOME", "rm -rf ${HOME}", 'rm -rf "$HOME"', "rm -rf $HOME/",
    "rm -rf .", "rm -rf ..", "rm -rf /etc", "rm -rf /usr/", "rm -rf /nix/*",
    "rm -Rf /var", "rm -rf /home", "rm -rf -- /", "rm -rvf /boot",
]


@pytest.mark.parametrize("spelling", RM_FATAL_SPELLINGS)
@pytest.mark.parametrize("prefix", PREFIXES)
def test_catastrophic_rm_is_denied_in_every_spelling(prefix, spelling):
    assert gc.check_rm_rf_critical(prefix + spelling) is not None


@pytest.mark.parametrize("command", [
    "rm -rf node_modules",
    "rm -rf /tmp/scratch",
    "rm -rf $SCRATCH",
    "rm -rf ~/.cache/browser-agent-opencode-config/node_modules",
    "rm -rf ./build",
    "rm -f foo.txt",
    "rm /",                        # not recursive — rm refuses anyway
    'git commit -m "do not rm -rf / ever"',
])
def test_ordinary_and_quoted_rm_stays_allowed(command):
    assert gc.check_rm_rf_critical(command) is None


# --- git stash -------------------------------------------------------------- #
GIT_HOPS = ["", "-C /tmp/x ", "-C /tmp/x -C /tmp/y ", "--git-dir=/tmp/x/store ",
            "--no-pager ", "-c user.name=x ", "-P "]
STASH_SUBS = ["stash", "stash push -m wip", "stash push -m 'wip on the diff'",
              "stash pop", "stash apply", "stash drop", "stash save wip",
              "stash create", "stash -u"]


@pytest.mark.parametrize("sub", STASH_SUBS)
@pytest.mark.parametrize("hop", GIT_HOPS)
def test_git_stash_is_denied_through_every_global_option_hop(hop, sub):
    """🔴 REGRESSION for the measured `review`-agent bypass: with only a
    `git -C * diff*` allow-list, `git -C <path> stash push -m 'wip on the diff'`
    EXECUTED and created a stash, because the glob's middle `*` is greedy across
    spaces. argv[1] is `stash` however many global options precede it."""
    assert gc.check_git_stash(f"git {hop}{sub}") is not None


@pytest.mark.parametrize("prefix", PREFIXES)
def test_git_stash_is_denied_behind_every_prefix(prefix):
    assert gc.check_git_stash(prefix + "git stash push -m wip") is not None


@pytest.mark.parametrize("command", [
    "git stash list",
    "git -C /tmp/x stash list",
    "git stash show -p",
    "git status",
    "git diff --stat",
    "git -C /tmp/x diff",
    "git log --oneline -5",
    "echo 'git stash is banned'",
])
def test_git_stash_reads_and_unrelated_git_stay_allowed(command):
    """RULES calls `git stash list` the safe diagnostic — denying it would push
    the operator toward guessing instead of looking."""
    assert gc.check_git_stash(command) is None


# --- git clean -f ----------------------------------------------------------- #
@pytest.mark.parametrize("spelling", ["clean -fd", "clean -f", "clean -fdx",
                                      "clean --force -d", "clean -d -f", "clean -xf"])
@pytest.mark.parametrize("hop", GIT_HOPS)
def test_git_clean_force_is_denied(hop, spelling):
    assert gc.check_git_clean_force(f"git {hop}{spelling}") is not None


@pytest.mark.parametrize("command", ["git clean -nd", "git clean --dry-run",
                                     "git clean -n", "git status"])
def test_git_clean_dry_run_stays_allowed(command):
    assert gc.check_git_clean_force(command) is None


# --------------------------------------------------------------------------- #
# 3b. 🔴 `git stash` and `git clean -f` under the CLAUDE-CODE policy
#
# Closes a gap that was live on both hosts until 2026-08-02. The two checks
# existed and were correct — they were simply not in the policy bash-guard.py
# runs. Probed against the live ~/.claude/hooks/bash-guard.py before the change,
# by piping PreToolUse JSON into it, WITH positive controls in the same sweep so
# a wired-to-nothing harness could not produce the reassuring answer:
#
#     DENY   git add -A                      <- positive control (guard is live)
#     DENY   git reset --hard HEAD           <- positive control
#     ALLOW  git stash
#     ALLOW  git stash push -m wip
#     ALLOW  git stash pop
#     ALLOW  git -C <repo> stash
#     ALLOW  git clean -fd
#     ALLOW  ls -la /tmp                     <- benign control
#
# Every deny below is asserted BY REASON. `cd /x && git stash` is denied by
# check_cd_then_git with this change fully reverted, so a bare `is not None`
# would be green for the wrong reason — the exact trap section 1b records.
# --------------------------------------------------------------------------- #
STASH_REASON_MARK = "`git stash` is blocked"
CLEAN_REASON_MARK = "`git clean -f` is blocked"


def _assert_denied_as(command, mark, policy="claude-code"):
    reason = _deny_reason(command, policy)
    assert mark in reason, (
        f"{command!r} denied under {policy}, but for the WRONG reason — got "
        f"{reason[:120]!r}. Some other check fired first; this asserts nothing "
        f"about {mark} coverage."
    )
    return reason


# The wrapped/prefixed spellings RULES requires the guard to survive. Paths are
# pairwise distinct and none of them contains the substring `stash`/`clean`,
# so a fixture cannot pass by accident of its own text.
STASH_DENY = [
    "git stash",
    "git stash push -m wip",
    "git stash pop",
    "git stash apply",
    "git stash drop",
    "VAR=1 git stash",
    "sudo git stash",
    "sudo -n git -C /srv/repos/alpha stash push -m wip",
    "git -C /srv/repos/beta stash",
    "git --git-dir=/var/lib/gamma stash pop",
    "bash -c 'git stash'",
    "echo ok && git stash",
    "cd /srv/delta && git stash",
    "git -C /tmp/epsilon stash; echo done",
    "/usr/bin/git -C /tmp/zeta stash push -m wip",
]

CLEAN_DENY = [
    "git clean -f",
    "git clean -fd",
    "git clean -fdx",
    "git clean --force -d",
    "VAR=1 git clean -fd",
    "sudo git clean -fd",
    "git -C /srv/repos/eta clean -fd",
    "bash -c 'git clean -fd'",
    "echo ok && git clean -fd",
    "cd /srv/theta && git clean -fd",
    "/usr/bin/git -C /tmp/iota clean -xf",
]


@pytest.mark.parametrize("command", STASH_DENY)
def test_git_stash_is_denied_under_claude_code(command):
    """🔴 RED at origin/main for every row: the check was opencode-only, so
    `claude-code` returned None (the `cd &&` row denied with the WRONG reason,
    which the attribution assertion catches)."""
    _assert_denied_as(command, STASH_REASON_MARK, "claude-code")


@pytest.mark.parametrize("command", CLEAN_DENY)
def test_git_clean_force_is_denied_under_claude_code(command):
    """🔴 RED at origin/main for every row — same mechanism as stash above."""
    _assert_denied_as(command, CLEAN_REASON_MARK, "claude-code")


@pytest.mark.parametrize("command", STASH_DENY)
def test_git_stash_still_denied_under_opencode(command):
    """The move must not COST opencode anything: `POLICIES["opencode"]` is
    `_CLAUDE_CODE_CHECKS + _IRREVERSIBLE_CHECKS`, so the check is inherited
    rather than duplicated. This asserts the inheritance actually holds."""
    _assert_denied_as(command, STASH_REASON_MARK, "opencode")


@pytest.mark.parametrize("command", CLEAN_DENY)
def test_git_clean_force_still_denied_under_opencode(command):
    _assert_denied_as(command, CLEAN_REASON_MARK, "opencode")


def test_cd_then_git_stash_reports_the_stash_reason_not_the_cd_reason():
    """🔴 The ORDERING pin. check_git_stash sits before check_cd_then_git, so
    the more serious of the two problems is the one named. At origin/main this
    command denies with the `cd` message — i.e. this is regression coverage for
    the ordering, not just for the policy membership."""
    reason = _deny_reason("cd /srv/kappa && git stash", "claude-code")
    assert STASH_REASON_MARK in reason
    assert "use `git -C <path>" not in reason


# --- the ALLOW half -------------------------------------------------------- #
# 🔴 Built independently of the deny list — different paths, different
# subcommands — so a copy-paste symmetry cannot make both halves agree with the
# same bug. INVARIANT GUARDS (green before and after): they are the
# false-positive fence, not regression coverage for any shipped bug.
STASH_CLEAN_ALLOW = [
    "git stash list",
    "git stash show -p",
    "git -C /opt/service-lambda stash list",
    "git clean -nd",
    "git clean --dry-run",
    "git clean -n -d",
    "git -C /opt/service-lambda clean -nd",
    "git status",
    "git -C /opt/service-lambda diff --stat",
    "git checkout -- src/app.ts",
    "git worktree list",
    "rm -rf /tmp/scratch-mu",
    # merely NAMING the commands, as an argument rather than as a command
    "echo 'git stash is banned'",
    "grep -rn 'git clean -fd' RULES.md",
    "rg 'git stash' claude/RULES.md",
    "git commit -m 'document why git stash is banned'",
]


@pytest.mark.parametrize("command", STASH_CLEAN_ALLOW)
def test_stash_and_clean_neighbours_stay_allowed_under_claude_code(command):
    """INVARIANT GUARD. `git stash list` is the diagnostic RULES tells you to
    run, `git clean -nd` is the one the deny message points at, and the argv
    parser means quoting the command as an ARGUMENT is not a command."""
    assert gc.evaluate(command, "claude-code") is None
    assert gc.evaluate(command, "opencode") is None


# --- the escape hatch ------------------------------------------------------ #
def test_new_deny_messages_carry_the_quoting_escape_hatch():
    """🔴 The convention `check_git_add_all` set, and the reason it exists: the
    operator's own test harness was blocked because an ARGUMENT contained the
    literal string `git add -A`. Both new messages must hand the caller the same
    documented way out."""
    for reason in (gc.check_git_stash("git stash"),
                   gc.check_git_clean_force("git clean -fd")):
        assert reason is not None
        assert "commit -F" in reason, reason
        assert "--body-file" in reason, reason
        assert "Write tool" in reason, reason


def test_the_one_real_quoting_false_positive_is_a_heredoc_body():
    """The accepted false positive, MEASURED rather than assumed.

    The argv parser splits on newlines, so a heredoc body LINE that begins with
    the command is parsed as a command. That is the case the escape hatch is
    for. The same text passed as a single quoted ARGUMENT is one token and is
    correctly allowed — asserted here so the docstring above cannot drift.
    """
    heredoc = "cat > /tmp/doc.md <<'EOF'\ngit stash push -m wip\nEOF"
    reason = _assert_denied_as(heredoc, STASH_REASON_MARK, "claude-code")
    assert "commit -F" in reason
    assert gc.evaluate("git commit -m 'git stash push -m wip'", "claude-code") is None


# --------------------------------------------------------------------------- #
# 3c. 🔴 talosctl reset / mkfs / dd-to-a-block-device under the CLAUDE-CODE policy
#
# Closes a gap that was live on both hosts until 2026-08-02. All three checks
# existed and were correct — they were simply not in the policy bash-guard.py
# runs, so wiping a cluster node, formatting a disk and overwriting a block
# device were all permitted, unprompted, on the operator's primary tool.
#
# Probed against the repo's guard_core under BOTH policies before the change,
# WITH controls in the same sweep so a wired-to-nothing harness could not have
# produced the reassuring answer:
#
#     DENY   git add -A                          <- positive control (guard live)
#     DENY   git reset --hard HEAD               <- positive control
#     ALLOW  talosctl -n 192.168.50.94 reset
#     ALLOW  mkfs.ext4 /dev/sdc
#     ALLOW  dd if=/dev/zero of=/dev/sdc
#     ALLOW  ls -la /tmp                         <- benign control
#
# (Independently measured against the DEPLOYED ~/.claude/hooks/bash-guard.py by
# the operator the same evening, with the same verdicts.)
#
# Every deny below is asserted BY REASON, not by `is not None`. The heredoc row
# is the reason that matters: with this change reverted it still DENIES, via
# check_heredoc_to_file — so a bare truthiness assertion would be green for the
# wrong reason and would pass with check_mkfs deleted from the policy entirely.
# --------------------------------------------------------------------------- #
TALOS_REASON_MARK = "`talosctl reset` is blocked"
# mkfs/dd interpolate the program name and the device into the message, so the
# mark is the stable clause rather than the leading backtick phrase.
MKFS_REASON_MARK = "FORMATS a filesystem"
DD_REASON_MARK = "writing to a block device overwrites the disk in place"


# The wrapped/prefixed spellings the parser exists to survive. Device paths and
# node IPs are pairwise distinct so no fixture can pass by accident of another's
# text, and none of the paths contains the substring `reset`/`mkfs`/`dd`.
TALOS_DENY = [
    "talosctl reset",
    "talosctl -n 192.168.50.94 reset",
    "talosctl --nodes 192.168.50.95 reset",
    "talosctl -n 192.168.50.96 reset --graceful=false",
    "VAR=1 talosctl reset",
    "sudo talosctl reset",
    "sudo -n talosctl -n 192.168.50.97 reset",
    "env talosctl reset",
    "timeout 60 talosctl -n 192.168.50.98 reset",
    "bash -c 'talosctl -n 192.168.50.99 reset'",
    "echo ok && talosctl reset",
    "kubectl get nodes; talosctl -n 192.168.50.100 reset",
    "/usr/local/bin/talosctl -n 192.168.50.101 reset",
]

MKFS_DENY = [
    "mkfs.ext4 /dev/sdc",
    "mkfs.xfs /dev/sdd1",
    "mkfs -t ext4 /dev/sde",
    "mke2fs /dev/sdf",
    "mkswap /dev/sdg",
    "mkdosfs /dev/sdh1",
    "VAR=1 mkfs.ext4 /dev/sdi",
    "sudo mkfs.ext4 /dev/sdj",
    "sudo -n mkfs.btrfs /dev/sdk",
    "env mkfs.ext4 /dev/sdl",
    "bash -c 'mkfs.ext4 /dev/sdm'",
    "echo ok && mkfs.ext4 /dev/sdn",
    "lsblk; mkfs.ext4 /dev/sdo",
    "/usr/sbin/mkfs.ext4 /dev/sdp",
]

DD_DENY = [
    "dd if=/dev/zero of=/dev/sdc",
    "dd if=image.iso of=/dev/sdd bs=4M status=progress",
    "dd of=/dev/sde if=/dev/zero",
    "dd if=/dev/zero of=/dev/nvme0n1",
    "dd if=/dev/zero of=/dev/mapper/vg0-lv0",
    "VAR=1 dd if=/dev/zero of=/dev/sdf",
    "sudo dd if=/dev/zero of=/dev/sdg",
    "sudo -n dd if=/dev/zero of=/dev/sdh",
    "env dd if=/dev/zero of=/dev/sdi",
    "bash -c 'dd if=/dev/zero of=/dev/sdj'",
    "echo ok && dd if=/dev/zero of=/dev/sdk",
    "lsblk; dd if=/dev/zero of=/dev/sdl",
    "/usr/bin/dd if=/dev/zero of=/dev/sdm",
]


@pytest.mark.parametrize("command", TALOS_DENY)
def test_talosctl_reset_is_denied_under_claude_code(command):
    """🔴 RED at origin/main for every row: the check was opencode-only, so
    `claude-code` returned None."""
    _assert_denied_as(command, TALOS_REASON_MARK, "claude-code")


@pytest.mark.parametrize("command", MKFS_DENY)
def test_mkfs_is_denied_under_claude_code(command):
    """🔴 RED at origin/main for every row — same mechanism."""
    _assert_denied_as(command, MKFS_REASON_MARK, "claude-code")


@pytest.mark.parametrize("command", DD_DENY)
def test_dd_to_block_device_is_denied_under_claude_code(command):
    """🔴 RED at origin/main for every row — same mechanism."""
    _assert_denied_as(command, DD_REASON_MARK, "claude-code")


@pytest.mark.parametrize("command", TALOS_DENY)
def test_talosctl_reset_still_denied_under_opencode(command):
    """The move must not COST opencode anything: `POLICIES["opencode"]` is
    `_CLAUDE_CODE_CHECKS + _IRREVERSIBLE_CHECKS`, so the check is inherited
    rather than duplicated. This asserts the inheritance actually holds."""
    _assert_denied_as(command, TALOS_REASON_MARK, "opencode")


@pytest.mark.parametrize("command", MKFS_DENY)
def test_mkfs_still_denied_under_opencode(command):
    _assert_denied_as(command, MKFS_REASON_MARK, "opencode")


@pytest.mark.parametrize("command", DD_DENY)
def test_dd_to_block_device_still_denied_under_opencode(command):
    _assert_denied_as(command, DD_REASON_MARK, "opencode")


def test_mkfs_in_a_heredoc_body_reports_the_mkfs_reason_not_the_heredoc_reason():
    """🔴 The ORDERING pin, and the one row that is NOT green-by-default.

    With this change reverted this command still denies — via
    check_heredoc_to_file, which sits later in the list but was the only check
    of the two in the claude-code policy. So `assert reason is not None` passes
    at origin/main and pins nothing. The attribution is the whole test: the
    three new checks sit BEFORE check_heredoc_to_file, so the reason names the
    device destruction rather than the token waste.
    """
    heredoc = (
        "cat > /tmp/runbook.md <<'EOF'\n"
        "mkfs.ext4 /dev/sdq\n"
        + ("filler line to clear the heredoc size threshold\n" * 40)
        + "EOF"
    )
    assert gc.check_heredoc_to_file(heredoc) is not None, (
        "fixture no longer trips the heredoc check, so this test would pass "
        "trivially — the ordering is no longer being exercised"
    )
    _assert_denied_as(heredoc, MKFS_REASON_MARK, "claude-code")


# --- the ALLOW half -------------------------------------------------------- #
# 🔴 Built independently of the deny lists — different subcommands, different
# device paths — so a copy-paste symmetry cannot make both halves agree with the
# same bug. INVARIANT GUARDS (green before and after): this is the
# false-positive fence, not regression coverage for any shipped bug.
DEVICE_ALLOW = [
    # talosctl reads and non-reset verbs
    "talosctl version",
    "talosctl -n 192.168.50.110 get members",
    "talosctl -n 192.168.50.111 get resetstatus",
    "talosctl -n 192.168.50.112 dmesg",
    "talosctl -n 192.168.50.113 health",
    "talosctl --nodes 192.168.50.114 services",
    # dd that is not aimed at a block device
    "dd if=in.img of=out.img bs=1M",
    "dd if=/dev/urandom of=/tmp/seed.bin count=1",
    "dd if=/dev/zero of=/dev/null bs=1M count=10",
    "dd if=/dev/sdz of=/tmp/backup.img",
    # mkfs matched on the PROGRAM name, so naming it is not running it
    "echo 'mkfs.ext4 is banned on these hosts'",
    "grep -rn 'mkfs' claude/RULES.md",
    "rg 'dd if=/dev/zero of=/dev/sda' docs/",
    "git commit -m 'document why mkfs is blocked'",
    # near-miss program names
    "mkdir -p /tmp/scratch-nu",
    "mktemp -d",
    "ddrescue --help",
]


@pytest.mark.parametrize("command", DEVICE_ALLOW)
def test_device_check_neighbours_stay_allowed_under_claude_code(command):
    """INVARIANT GUARD. Reads and inspections are how an operator diagnoses the
    thing the deny message tells them to do by hand; blocking those would push
    them toward guessing. `dd if=/dev/sdz of=/tmp/backup.img` is the asymmetry
    that matters — reading a device is not writing one."""
    assert gc.evaluate(command, "claude-code") is None
    assert gc.evaluate(command, "opencode") is None


# --- the escape hatch ------------------------------------------------------ #
def test_device_deny_messages_carry_the_quoting_escape_hatch():
    """🔴 The convention `check_git_add_all` set and #295 extended: every check
    in the claude-code policy fires on RAW TEXT in every Bash call in every
    session, so it WILL eventually fire on someone writing ABOUT the command.
    All three new messages must hand the caller the same documented way out."""
    for reason in (gc.check_talosctl_reset("talosctl reset"),
                   gc.check_mkfs("mkfs.ext4 /dev/sdr"),
                   gc.check_dd_to_block_device("dd if=/dev/zero of=/dev/sds")):
        assert reason is not None
        assert "commit -F" in reason, reason
        assert "--body-file" in reason, reason
        assert "Write tool" in reason, reason


def test_rm_rf_is_the_only_check_left_out_of_claude_code():
    """🔴 The DECISION pin, stated as an assertion so it cannot quietly become
    an oversight in either direction.

    Down: if `check_rm_rf_critical` is later added to claude-code this fails,
    and whoever does it must come with the evidence IRREVERSIBLE_EXPECTED asks
    for (how often the fatal-target set fires on real sessions) rather than the
    observation that it is the last one left.

    Up: it also fails if a NEW check is parked in `_IRREVERSIBLE_CHECKS` as a
    way of adding a guard without the claude-code conversation.
    """
    oc = [f.__name__ for f in gc.POLICIES["opencode"]]
    cc = [f.__name__ for f in gc.POLICIES["claude-code"]]
    assert [n for n in oc if n not in cc] == ["check_rm_rf_critical"]


# --------------------------------------------------------------------------- #
# 4. end-to-end through the policy, over the full spelling product
# --------------------------------------------------------------------------- #
IRREVERSIBLE_SAMPLES = [
    "talosctl -n 192.168.50.94 reset",
    "talosctl --nodes=192.168.50.94 reset",
    "mkfs.ext4 /dev/sda1",
    "dd if=/dev/zero of=/dev/sda",
    "rm -rf $HOME",
    "git -C /tmp/x stash push -m 'wip on the diff'",
    "git clean -fd",
]


@pytest.mark.parametrize("command", IRREVERSIBLE_SAMPLES)
@pytest.mark.parametrize("prefix", PREFIXES)
def test_opencode_policy_denies_every_irreversible_sample(prefix, command):
    assert gc.evaluate(prefix + command, "opencode") is not None


@pytest.mark.parametrize("command", IRREVERSIBLE_SAMPLES)
@pytest.mark.parametrize("template", SEPARATOR_TEMPLATES)
def test_opencode_policy_denies_behind_every_separator(template, command):
    assert gc.evaluate(template.format(command), "opencode") is not None


HIGH_FREQUENCY_ALLOWED = [
    "ls -la",
    "kubectl get pods -A",
    "KUBECONFIG=$KC_HOMELAB kubectl -n remix logs deploy/remix --tail=100",
    "git status",
    "git -C /tmp/x diff",
    "git -C /tmp/x log --oneline -5",
    "rg foo",
    "systemctl status foo",
    "flux get kustomizations -A",
    "kubectl rollout restart deploy/x",
    "talosctl -n 192.168.50.94 get members",
    "python3 -m pytest -q",
    "rm -rf /tmp/scratch",
    "git stash list",
]


@pytest.mark.parametrize("command", HIGH_FREQUENCY_ALLOWED)
def test_high_frequency_commands_are_not_denied_by_the_guard(command):
    """Prompt fatigue is a security failure, not a UX one — and a hard deny on a
    routine read is worse than a prompt."""
    assert gc.evaluate(command, "opencode") is None


# --------------------------------------------------------------------------- #
# 4b. 🔴 gaps found by an INDEPENDENTLY-CONSTRUCTED mutation sweep
#
# A 52-mutant sweep built differently from this suite (pattern-NARROWING mutants
# that keep the function, the policy entry and the deny action, and change only
# what matches) killed 39/50 and left 11 survivors. Every survivor was a hole in
# THIS FILE, not a defect in guard_core.py — the code already handled each case,
# nothing pinned it. That is exactly the failure mode the previous round had:
# a 10/10 sweep whose blind spots only a differently-built sweep could see.
#
# Each block below names the mutant it kills.
# --------------------------------------------------------------------------- #

# --- N16 / P14: argv[0] and wrapper tokens are BASENAME-normalised ---------- #
# On NixOS everything resolves through /run/current-system/sw/bin and sudo lives
# at /run/wrappers/bin/sudo, so an absolute argv[0] is the NORMAL spelling here,
# not an exotic one. Mutants that dropped `os.path.basename` on either the
# command or the wrapper token survived the whole suite.
ABS_PREFIXES = ["/usr/bin/", "/run/current-system/sw/bin/", "/bin/", "./"]


@pytest.mark.parametrize("command", [
    "talosctl -n 192.168.50.94 reset",
    "mkfs.ext4 /dev/sda1",
    "mke2fs /dev/sdc",
    "dd of=/dev/sda if=/dev/zero",
    "rm -rf $HOME",
    "git -C /repo stash push -m wip",
    "git clean -fd",
    "git -C /repo reset --hard",
])
@pytest.mark.parametrize("prefix", ABS_PREFIXES)
def test_absolute_argv0_is_still_matched(prefix, command):
    """kills N16."""
    head, _, tail = command.partition(" ")
    assert gc.evaluate(f"{prefix}{head} {tail}", "opencode") is not None


@pytest.mark.parametrize("wrapper", [
    "/usr/bin/sudo", "/run/wrappers/bin/sudo", "/usr/bin/env", "/usr/bin/doas",
    "/usr/bin/nohup", "/run/current-system/sw/bin/setsid",
])
def test_absolute_wrapper_paths_are_still_peeled(wrapper):
    """kills P14. `/run/wrappers/bin/sudo` is the real path on this host."""
    assert gc.evaluate(f"{wrapper} talosctl -n 1.2.3.4 reset", "opencode") is not None


def test_absolute_timeout_wrapper_consumes_its_positional():
    assert gc.evaluate("/usr/bin/timeout 60 talosctl reset", "opencode") is not None


# --- P04: the arity-BRANCHING safety net ------------------------------------ #
# `_peel_variants` branches on the other arity interpretation precisely so that
# a wrong entry in `_WRAPPER_VALUE_FLAGS` cannot fail OPEN — that is what the
# historical `sudo -n` bug was. A mutant returning only the primary peeling
# survived every other test in this file, i.e. the safety net was pure prose.
WRAPPER_FLAG_PAIRS = sorted(
    (w, f) for w, flags in gc._WRAPPER_VALUE_FLAGS.items() for f in flags
)


@pytest.mark.parametrize("wrapper,flag", WRAPPER_FLAG_PAIRS)
def test_a_value_flag_used_WITHOUT_a_value_still_denies(wrapper, flag):
    """kills P04.

    Every flag here is in the value-taking table, so the PRIMARY peeling
    swallows `talosctl` as the flag's value and sees `['reset']`. Only the
    alternate interpretation recovers the real command. If this ever goes red,
    the guard has the `sudo -n` bug again — for a different flag.
    """
    assert gc.evaluate(f"{wrapper} {flag} talosctl reset", "opencode") is not None


@pytest.mark.parametrize("command", [
    "sudo -u rm -rf /", "env -C mkfs.ext4 /dev/sda1", "nice -n rm -rf $HOME",
    "doas -u git -C /repo stash push -m x", "ionice -c dd of=/dev/sda if=/dev/zero",
])
def test_arity_branch_covers_the_other_checks_too(command):
    assert gc.evaluate(command, "opencode") is not None


# --- P13: bundled `-c` shells ----------------------------------------------- #
@pytest.mark.parametrize("wrapper", [
    "bash -xc '{}'", "bash -ec '{}'", "bash -exc '{}'", 'sh -lc "{}"',
    "zsh -ic '{}'", "bash -o pipefail -c '{}'",
])
@pytest.mark.parametrize("inner", ["talosctl reset", "rm -rf $HOME", "git stash"])
def test_bundled_dash_c_shells_are_inspected(wrapper, inner):
    """kills P13. The matrix above only used a bare `-c`."""
    assert gc.evaluate(wrapper.format(inner), "opencode") is not None


# --- S08: the UNLEXABLE-input fallback -------------------------------------- #
# A stray quote makes shlex raise and `_tokenise` falls back to a crude split.
# That branch was untested for EVERY check, not just dd — and it is reachable
# from any truncated heredoc or an unbalanced quote in a message.
@pytest.mark.parametrize("command", [
    'dd of="/dev/sda" if=/dev/zero',
    "talosctl -n 1.2.3.4 reset",
    "rm -rf $HOME",
    "mkfs.ext4 /dev/sda1",
    "git -C /repo stash push -m wip",
])
def test_unlexable_input_still_denies(command):
    """kills S08. The trailing `'` makes shlex.split raise."""
    assert gc.evaluate(command + " '", "opencode") is not None
    assert gc.evaluate(command + ' "', "opencode") is not None


# --- S09: `cd <path> ; git …`, not only `&&` -------------------------------- #
@pytest.mark.parametrize("sep", ["&&", ";", " && ", " ; "])
def test_cd_then_git_covers_both_separators(sep):
    """kills S09. Only the `&&` spelling was pinned."""
    assert gc.check_cd_then_git(f"cd /home/zach/repo{sep}git status") is not None


@pytest.mark.parametrize("command", ["git -C /home/zach/repo status", "cd /tmp",
                                     "cd /tmp && ls", "echo cd /tmp && git status"])
def test_cd_then_git_does_not_over_match(command):
    assert gc.check_cd_then_git(command) is None


# --- S11: every private-key header spelling --------------------------------- #
@pytest.mark.parametrize("kind", ["RSA ", "DSA ", "EC ", "OPENSSH ", "PGP ",
                                  "ENCRYPTED ", "", "RSA2 "])
def test_every_private_key_header_spelling_is_caught(kind):
    """kills S11. Only `RSA PRIVATE KEY` was pinned; the `[A-Z0-9 ]*` wildcard —
    which is what covers modern OPENSSH keys — was unpinned."""
    body = "-----BEGIN " + kind + "PRIVATE KEY-----\nAAAA\n"
    assert gc.check_private_key(f"echo '{body}' > /tmp/k") is not None


def test_private_key_check_does_not_fire_on_a_public_key():
    assert gc.check_private_key("ssh-add ~/.ssh/id_ed25519.pub") is None


# --- N14 / N15 / S12: the secret + public-IP publish guard ------------------ #
SECRET_SAMPLES = {
    "an AWS access key id": "AKIAIOSFODNN7EXAMPLE",
    "an AWS temporary access key id": "ASIAIOSFODNN7EXAMPLE",
    "a GitHub token": "ghp_" + "A" * 36,
    "a GitHub fine-grained PAT": "github_pat_" + "A" * 42,
    "a GitLab token": "glpat-" + "A" * 20,
    "an Anthropic API key": "sk-ant-" + "A" * 20,
    "an OpenRouter API key": "sk-or-v1-" + "A" * 20,
    "an OpenAI project key": "sk-proj-" + "A" * 20,
    "a Slack token": "xoxb-" + "A" * 12,
    "a Google API key": "AIza" + "A" * 35,
}


def test_every_secret_pattern_has_a_pinned_sample():
    """kills N14 (structurally). Deleting three `sk-*` shapes changed nothing,
    because no test pinned any INDIVIDUAL pattern."""
    labels = {label for _, label in gc.SECRET_PATTERNS}
    assert labels == set(SECRET_SAMPLES), (
        "SECRET_PATTERNS and the pinned samples have diverged — every shape must "
        "carry a sample, or deleting one is invisible"
    )


@pytest.mark.parametrize("label,sample", sorted(SECRET_SAMPLES.items()))
def test_each_secret_shape_is_caught_at_a_publish_sink(label, sample):
    """kills N14."""
    reason = gc.check_secret_or_ip_publish(f'git commit -m "creds {sample}"')
    assert reason is not None and label in reason


PUBLISH_SINKS = [
    "git commit -m 'X'",
    "git notes add -m 'X'",
    "gh pr create --body 'X'",
    "gh pr comment 12 --body 'X'",
    "gh pr edit 4 --body 'X'",
    "gh pr review 9 --body 'X'",
    "gh issue create --body 'X'",
    "gh issue comment 3 --body 'X'",
    "gh issue edit 3 --body 'X'",
    "gh release create v1 --notes 'X'",
    "gh gist create -d 'X' f.txt",
]


@pytest.mark.parametrize("sink", PUBLISH_SINKS)
def test_every_publish_sink_is_gated(sink):
    """kills N15. Only the `create` sinks were pinned, so narrowing
    PUBLISH_SINKS to `gh … create` let `gh pr comment` post a secret."""
    assert gc.check_secret_or_ip_publish(sink.replace("X", "AKIAIOSFODNN7EXAMPLE")) is not None


@pytest.mark.parametrize("sink", PUBLISH_SINKS)
def test_a_public_ip_is_gated_at_every_publish_sink(sink):
    """kills S12. This is the arm the design note says exists because a real
    session leaked an ingress origin IP into a public-repo comment — and it was
    entirely unpinned."""
    reason = gc.check_secret_or_ip_publish(sink.replace("X", "origin 5.161.118.55"))
    assert reason is not None and "5.161.118.55" in reason


@pytest.mark.parametrize("ip", ["192.168.50.94", "10.0.0.2", "127.0.0.1",
                                "100.64.0.1", "172.16.0.5", "169.254.1.1"])
def test_internal_ips_are_exempt_at_a_publish_sink(ip):
    """The EXEMPTION side was unpinned too. Zach's infra work is full of these;
    if they started tripping the guard, every commit would be blocked."""
    assert gc.check_secret_or_ip_publish(f"git commit -m 'node {ip}'") is None


def test_a_secret_without_a_publish_sink_passes():
    """Deliberate: writing a cred into a config file is normal work."""
    assert gc.check_secret_or_ip_publish(
        "echo AKIAIOSFODNN7EXAMPLE > ~/.config/x/env") is None


# --- S03: the `--` boundary in _flags_and_operands -------------------------- #
def test_flags_and_operands_respects_the_double_dash():
    """kills S03 — pinned on the HELPER directly.

    The sweep confirmed this branch is currently safety-NEUTRAL: no fatal `rm`
    target and no denied git subcommand begins with `-`, so removing the `--`
    handling could not open a hole across 1,102 brute-forced shapes. It becomes
    live the moment a check gains a dash-leading operand of interest, so pin the
    contract rather than an outcome that happens not to depend on it.
    """
    flags, operands = gc._flags_and_operands(["rm", "-rf", "--", "-weird", "x"])
    assert flags == {"-rf"}
    assert operands == ["-weird", "x"]


# --- weak kills the sweep flagged: N13, S01, S04 ---------------------------- #
@pytest.mark.parametrize("hop", ["-C /tmp/x ", "-C /tmp/x -C /tmp/y ",
                                 "--git-dir=/tmp/x/store ", "--no-pager ",
                                 "-c user.name=x ", "-P "])
@pytest.mark.parametrize("prefix", ["", "sudo ", "FOO=1 ", "timeout 60 "])
def test_git_reset_hard_through_every_global_option_hop(prefix, hop):
    """N13 was killed by exactly ONE test — a one-test-deep guard on its own
    headline case (`git -C <path> reset --hard`, the gap this work found)."""
    assert gc.check_git_reset_hard_argv(f"{prefix}git {hop}reset --hard") is not None


@pytest.mark.parametrize("command", ["git reset --soft HEAD~1", "git reset HEAD~1",
                                     "git -C /tmp/x reset --mixed", "git status"])
def test_git_reset_without_hard_stays_allowed(command):
    assert gc.check_git_reset_hard_argv(command) is None


@pytest.mark.parametrize("command", [
    "rm /", "rm /etc", "rm $HOME", "rm -f /", "rm -f $HOME", "rm -i ~",
])
def test_non_recursive_rm_of_a_fatal_target_is_not_denied(command):
    """S01 (`recursive` always True) was killed ONLY by an over-block test.
    Pinned here as a SECURITY-adjacent contract in its own right: `rm` without
    `-r` refuses a directory, so denying these would be pure prompt fatigue on
    commands that cannot do the damage."""
    assert gc.check_rm_rf_critical(command) is None


# --------------------------------------------------------------------------- #
# 5. the CLI seam the opencode plugin calls
# --------------------------------------------------------------------------- #
def _cli(command, policy="opencode", raw=None):
    import json
    import subprocess
    core = Path(__file__).resolve().parents[1] / "guard_core.py"
    payload = raw if raw is not None else json.dumps({"command": command})
    p = subprocess.run([sys.executable, str(core), "--policy", policy],
                       input=payload, capture_output=True, text=True, timeout=30)
    return p


def test_cli_allows_and_exits_zero():
    import json
    p = _cli("ls -la")
    assert p.returncode == 0
    assert json.loads(p.stdout) == {"decision": "allow"}


def test_cli_denies_with_a_reason():
    import json
    p = _cli("talosctl -n 1.2.3.4 reset")
    assert p.returncode == 0
    out = json.loads(p.stdout)
    assert out["decision"] == "deny" and "talosctl reset" in out["reason"]


def test_cli_honours_the_policy_flag():
    """The discriminating command must be one the two policies DISAGREE about,
    and after 2026-08-02 `talosctl reset` is no longer such a command — it moved
    into the claude-code policy, which turned this test red (correctly: it was
    asserting the gap). `rm -rf /` is the one remaining disagreement, so it is
    the only fixture that can still exercise the flag."""
    import json
    assert json.loads(_cli("rm -rf /", "claude-code").stdout)["decision"] == "allow"
    assert json.loads(_cli("rm -rf /", "opencode").stdout)["decision"] == "deny"


def test_cli_reports_bad_input_with_a_nonzero_exit():
    """🔴 The plugin fails CLOSED on a non-zero exit, so this exit code is the
    contract that makes malformed input a refusal rather than a pass."""
    p = _cli(None, raw="not json at all")
    assert p.returncode == 2
    import json
    assert json.loads(p.stdout)["decision"] == "error"


# --------------------------------------------------------------------------- #
# 6. the opencode plugin source
#
# These are FILE assertions, not behaviour — the plugin's behaviour is verified
# by executing opencode against a sandbox config dir (see the PR body); that is
# not hermetic enough to live in this suite.
# --------------------------------------------------------------------------- #
PLUGIN = Path(__file__).resolve().parents[2] / "opencode" / "plugin" / "guard.js"


def test_plugin_uses_tool_execute_before():
    """MEASURED on 1.18.4: `permission.ask` never fires (not on the allow path,
    not on the ask path), so it cannot express a decision at all here.
    `tool.execute.before` fires on every bash call and throwing hard-blocks."""
    src = PLUGIN.read_text()
    assert '"tool.execute.before"' in src
    assert '"permission.ask"' not in src


def test_plugin_fails_closed_on_every_failure_mode():
    src = PLUGIN.read_text()
    body = src.split('"tool.execute.before"', 1)[1]
    # one throw per failure mode: spawn error, non-zero/no-output, unparseable,
    # deny, unknown decision.
    assert body.count("throw new Error(") >= 5, (
        "the plugin must throw — not return — on spawn failure, non-zero exit, "
        "unparseable output and an unknown decision. A guard that degrades to "
        "'allow' reports safety it is not providing."
    )


def test_plugin_requests_the_opencode_policy():
    assert 'DEVRC_GUARD_POLICY || "opencode"' in PLUGIN.read_text()


# --------------------------------------------------------------------------- #
# 7. performance — this runs on every bash call
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("probe", [
    "git a" + "dd -" + "A" * 32000 + "!",
    'git commit -m "' + "\\" * 2000,
    "echo " + "a && " * 2000 + "true",
    "rm -rf " + " ".join(f"/tmp/p{i}" for i in range(2000)),
])
def test_no_pathological_slowness(probe):
    import time
    t0 = time.time()
    gc.evaluate(probe, "opencode")
    assert time.time() - t0 < 2.0


# --------------------------------------------------------------------------- #
# 8. 🔴 `time` — the wrapper that defeated BOTH layers at once
#
# MEASURED before `time` was added to `_WRAPPER_VALUE_FLAGS`:
#   time dd bs=1M if=/dev/zero of=/dev/sda   ->  glob=allow guard=allow
# argv[0] stayed "time", so `check_dd_to_block_device` bailed at its
# `basename(argv[0]) != "dd"` and never looked at the operands.
#
# 🔴 WHY dd SPECIFICALLY, and why this is not just one more wrapper: for every
# OTHER dangerous family the glob layer backstops an unpeeled prefix — measured,
# `time rm -rf /`, `time mkfs.ext4 …`, `time talosctl … reset` and `time git
# stash` all still resolve DENY on the globs alone. The dd globs are the only
# SPELLED ones (`"dd *"` anchored, `"*dd if=*"`/`"*dd of=*"` literal), so an
# unrecognised wrapper is the one input that defeats the glob and the guard
# together. That intersection is the whole finding.
# --------------------------------------------------------------------------- #
TIME_SPELLINGS = [
    "time dd bs=1M if=/dev/zero of=/dev/sda",
    # the alias/keyword bypass — the tokeniser normalises `\time` to `time`
    "\\time dd bs=1M of=/dev/sda",
    "/usr/bin/time dd bs=1M of=/dev/sda",
    "time -p dd bs=1M of=/dev/sda",
    "/usr/bin/time -v dd bs=1M of=/dev/sda",
    # GNU time's value-taking flags must consume their value, not the command
    "/usr/bin/time -o /tmp/t dd bs=1M of=/dev/sda",
    "/usr/bin/time -f %e dd bs=1M of=/dev/sda",
    "/usr/bin/time --output=/tmp/t dd bs=1M of=/dev/sda",
    # and composed with the wrappers that already worked
    "sudo time dd bs=1M of=/dev/sda",
    "FOO=1 time dd bs=1M of=/dev/sda",
    "time sudo dd bs=1M of=/dev/sda",
    "timeout 60 time dd bs=1M of=/dev/sda",
]


@pytest.mark.parametrize("command", TIME_SPELLINGS)
def test_time_wrapper_is_peeled_before_the_dd_check(command):
    """RED before `time` joined the wrapper table, GREEN after."""
    assert gc.evaluate(command, "opencode") is not None, (
        f"`time`-wrapped dd not caught: {command!r}. argv[0] must peel to `dd` "
        f"or check_dd_to_block_device never runs."
    )


@pytest.mark.parametrize("command", [
    "time rm -rf /", "time mkfs.ext4 /dev/sda", "time git stash",
    "time git add -A", "\\time rm -rf $HOME",
])
def test_time_wrapper_peels_for_the_other_families_too(command):
    """The same peel, for every other check — so `time` is fixed as a WRAPPER,
    not patched into one call site."""
    assert gc.evaluate(command, "opencode") is not None


@pytest.mark.parametrize("command", [
    "time ls -la", "time git status", "time rg foo", "time npm run build",
    "/usr/bin/time -v cargo build",
])
def test_time_wrapper_does_not_over_block_ordinary_commands(command):
    """The other half of the trade: peeling `time` must not make it a deny.

    Without this, "the guard got stricter" and "the guard got broken" look the
    same from the green side.
    """
    assert gc.evaluate(command, "opencode") is None, (
        f"{command!r} is an ordinary timed command and must stay allowed"
    )


def test_time_alone_is_not_a_crash():
    """A wrapper with nothing after it peels to an empty argv."""
    assert gc.evaluate("time", "opencode") is None
    assert gc.evaluate("/usr/bin/time -v", "opencode") is None


# --------------------------------------------------------------------------- #
# 8. 🔴 check_git_commit_to_main — the commit-to-main deny (2026-08-10)
#
# WHY THIS EXISTS AT ALL. "Never commit to main" is 🔴 in THREE separate files
# (claude/RULES.md "Git Workflow", devrc's own CLAUDE.md "Git discipline",
# ~/.claude/CLAUDE.md) and was violated TWICE in four days regardless:
#   2026-08-06  two un-pushed commits on the workbench blocked `ship.sh` for hours
#   2026-08-09  three more, rescued as PR #366
# Neither was a misunderstanding of the rule. Prose cannot re-assert itself inside
# a long session; a PreToolUse hook fires on every call. Measured ALLOW against the
# live hook before this landed — see CLAUDE_CODE_EXPECTED above for the sweep.
#
# 🔴 THIS SECTION USES REAL GIT REPOS, NOT MOCKS, AND THAT IS THE POINT.
# The check's whole job is to answer a question about the WORLD ("what branch is
# this repo on?"). A fixture that stubbed `_git_read` would be testing the branch
# names this file made up, i.e. it would pass with the implementation stubbed to a
# no-op — the exact vacuous-green shape RULES.md names ("Never derive a test's
# expectation from the implementation it tests"). Each fixture below is a real
# `git init` under tmp_path, and the branch really is what the test claims.
# --------------------------------------------------------------------------- #
import os
import subprocess


def _mkrepo(path, branch="main", remote="git@github.com:someone/some-repo.git",
            commit=False):
    """A REAL git repo at `path`, on `branch`, with `remote` as origin.

    `git branch --show-current` reports the initial branch on a repo with no
    commits at all, so most fixtures skip committing — but `commit=True` is
    available for the detached-HEAD case, which needs a rev to detach onto.
    """
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(["git", "-C", str(path), *a],
                                    capture_output=True, text=True, check=True)
    subprocess.run(["git", "init", "-b", branch, str(path)],
                   capture_output=True, text=True, check=True)
    run("config", "user.email", "test@example.invalid")
    run("config", "user.name", "guard test")
    if remote:
        run("remote", "add", "origin", remote)
    if commit:
        (path / "f.txt").write_text("x\n")
        run("add", "f.txt")
        run("commit", "-m", "init")
    return path


# --- 8a. harness self-validation (negative + positive control on the FIXTURE) - #
# 🔴 Before any assertion below is worth reading, the fixture itself has to be
# real. If `_mkrepo` silently produced a non-repo, every ALLOW assertion in this
# section would pass for the wrong reason — a repo that does not exist fails open.

def test_fixture_really_builds_a_git_repo_on_the_named_branch(tmp_path):
    """Positive control on the FIXTURE: git itself must agree about the branch."""
    repo = _mkrepo(tmp_path / "r", branch="main")
    out = subprocess.run(["git", "-C", str(repo), "branch", "--show-current"],
                         capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "main"
    assert gc._git_read(str(repo), "branch", "--show-current") == "main"
    assert gc._git_read(str(repo), "rev-parse", "--show-toplevel")


def test_git_read_returns_none_outside_a_repo(tmp_path):
    """Negative control on the read helper: a non-repo yields None, not a crash
    and not a stale value from the guard's own cwd."""
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert gc._git_read(str(plain), "rev-parse", "--show-toplevel") is None
    assert gc._git_read(str(tmp_path / "does-not-exist"), "branch", "--show-current") is None


def test_check_can_return_both_verdicts(tmp_path):
    """🔴 The blunt negative/positive control on the CHECK ITSELF. A checker that
    always returns None (the measured 84% base rate for generated enforcement
    rules) would pass every ALLOW test in this file. Both directions, one test."""
    on_main = _mkrepo(tmp_path / "main-repo", branch="main")
    on_feat = _mkrepo(tmp_path / "feat-repo", branch="feat/x")
    assert gc.check_git_commit_to_main('git commit -m "x"', str(on_main)) is not None
    assert gc.check_git_commit_to_main('git commit -m "x"', str(on_feat)) is None


# --- 8b. the DENY half ------------------------------------------------------- #

@pytest.mark.parametrize("branch", ["main", "master", "trunk"])
def test_commit_on_a_main_branch_is_denied(tmp_path, branch):
    """All three main-branch names. `trunk` is denied HERE because this repo is
    not allowlisted — the branch name is never what makes a commit legal."""
    repo = _mkrepo(tmp_path / f"r-{branch}", branch=branch)
    reason = gc.check_git_commit_to_main('git commit -m "wip"', str(repo))
    assert reason is not None, f"a commit on {branch!r} must be denied"
    assert f"`{branch}`" in reason


@pytest.mark.parametrize("command", [
    'git commit -m "wip"',
    "git commit",
    "git commit --amend --no-edit",
    "git commit -am wip",
    "git commit -F /tmp/msg.txt",
    "git commit --no-verify -m wip",          # -n is --no-verify, NOT a dry run
    "git commit -n -m wip",
    "sudo git commit -m wip",
    "FOO=bar git commit -m wip",
    "env GIT_AUTHOR_NAME=x git commit -m wip",
    "timeout 60 git commit -m wip",
    "/usr/bin/git commit -m wip",
    "ls && git commit -m wip",
    "ls; git commit -m wip",
    "ls || git commit -m wip",
    "ls | tee /dev/null && git commit -m wip",
    "bash -c 'git commit -m wip'",
    "git commit -m 'a message mentioning a feature branch'",
])
def test_commit_deny_across_spellings(tmp_path, command):
    """The multi-spelling matrix. The glob era pinned ONE spelling per rule and was
    blind to every other; this pins the wrapper-peeled, separator-split and
    nested-shell forms the parser is supposed to reach."""
    repo = _mkrepo(tmp_path / "r", branch="main")
    assert gc.check_git_commit_to_main(command, str(repo)) is not None, command


def test_dash_c_targets_the_repo_named_in_the_command_not_the_cwd(tmp_path):
    """🔴 `git -C <path> commit` — the spelling this repo's CLAUDE.md MANDATES.

    Both halves matter, and they are opposite: cwd is on a feature branch, the
    `-C` target is on main. A guard that read only the cwd would report ALLOW on
    the first and DENY on the second, i.e. exactly backwards.
    """
    on_main = _mkrepo(tmp_path / "main-repo", branch="main")
    on_feat = _mkrepo(tmp_path / "feat-repo", branch="feat/x")
    assert gc.check_git_commit_to_main(
        f'git -C {on_main} commit -m wip', str(on_feat)) is not None
    assert gc.check_git_commit_to_main(
        f'git -C {on_feat} commit -m wip', str(on_main)) is None


def test_dash_c_relative_path_resolves_against_the_cwd(tmp_path):
    on_main = _mkrepo(tmp_path / "main-repo", branch="main")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert gc.check_git_commit_to_main(
        "git -C ../main-repo commit -m wip", str(outside)) is not None


def test_multiple_dash_c_options_compose_like_git_does(tmp_path):
    """git's `-C` options are cumulative, not last-wins."""
    on_main = _mkrepo(tmp_path / "nest" / "main-repo", branch="main")
    assert gc.check_git_commit_to_main(
        f"git -C {tmp_path / 'nest'} -C main-repo commit -m wip", str(tmp_path)) is not None


def test_leading_cd_resolves_to_the_cd_target(tmp_path):
    """`cd <repo> && git commit` must be judged against <repo>, not the cwd.

    check_cd_then_git denies this shape too, but it runs LATER — so without this
    the compound would be resolved against the wrong directory on the way past.
    """
    on_main = _mkrepo(tmp_path / "main-repo", branch="main")
    on_feat = _mkrepo(tmp_path / "feat-repo", branch="feat/x")
    assert gc.check_git_commit_to_main(
        f"cd {on_main} && git commit -m wip", str(on_feat)) is not None


# --- 8c. the ALLOW half ------------------------------------------------------ #
# 🔴 Built from independent fixtures, not by negating the deny list. This half is
# what decides whether a 🔴 deny on the operator's PRIMARY TOOL is safe to live
# with — a guard that fires during correct routine work trains its subject to
# route around it, which is worse than no guard because it still reports safety.

@pytest.mark.parametrize("branch", ["feat/x", "fix-369", "wip", "mainline",
                                    "main-ish", "release/main", "trunkish"])
def test_commit_on_a_non_main_branch_is_allowed(tmp_path, branch):
    """Includes the near-miss names. `mainline`, `main-ish`, `release/main` and
    `trunkish` all CONTAIN a main-branch name — a substring check would block
    them. The guard compares the whole branch name."""
    repo = _mkrepo(tmp_path / "r", branch=branch)
    assert gc.check_git_commit_to_main('git commit -m "wip"', str(repo)) is None, branch


@pytest.mark.parametrize("command", [
    "git commit --dry-run",
    "git commit --dry-run --short",
    "git status",
    "git log --oneline -3",
    "git branch --show-current",
    "git diff HEAD",
    "git add scripts/foo.py",
    "git push origin HEAD",
    "git switch -c feat/x",
    "git show HEAD",
    "git rev-parse --show-toplevel",
    "git config user.email",
    "echo 'never git commit on main'",
    "rg 'git commit' scripts/",
])
def test_non_committing_commands_stay_allowed_even_on_main(tmp_path, command):
    """The near-miss set. `--dry-run` is a READ and must not be collateral;
    neither may any other git subcommand, nor merely NAMING the command."""
    repo = _mkrepo(tmp_path / "r", branch="main")
    assert gc.check_git_commit_to_main(command, str(repo)) is None, command


def test_repo_with_no_remotes_is_allowed_on_main(tmp_path):
    """🔴 The documented fail-open carve-out, asserted rather than assumed.

    `git init` defaults its first branch to main/master, so without this every
    scratch repo an agent builds under /tmp — INCLUDING this file's own fixtures —
    would trip a 🔴 deny during routine work. A repo with no remote cannot be a
    shared deploy target, which is the hazard the rule is about.
    """
    repo = _mkrepo(tmp_path / "scratch", branch="main", remote=None)
    assert gc.check_git_commit_to_main('git commit -m "x"', str(repo)) is None


def test_adding_a_remote_turns_the_same_repo_into_a_deny(tmp_path):
    """The carve-out above is about the REMOTE, not about /tmp or the repo name —
    proved by moving only that one variable and watching the verdict flip."""
    repo = _mkrepo(tmp_path / "scratch", branch="main", remote=None)
    assert gc.check_git_commit_to_main("git commit -m x", str(repo)) is None
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                    "git@github.com:someone/some-repo.git"],
                   capture_output=True, check=True)
    assert gc.check_git_commit_to_main("git commit -m x", str(repo)) is not None


def test_detached_head_is_allowed(tmp_path):
    """`branch --show-current` is empty when detached; a detached commit is not a
    commit to main."""
    repo = _mkrepo(tmp_path / "r", branch="main", commit=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "--detach", "HEAD"],
                   capture_output=True, check=True)
    assert gc.check_git_commit_to_main("git commit -m x", str(repo)) is None


def test_not_a_git_repo_is_allowed(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert gc.check_git_commit_to_main("git commit -m x", str(plain)) is None


def test_nonexistent_cwd_is_allowed_and_does_not_raise(tmp_path):
    assert gc.check_git_commit_to_main("git commit -m x", str(tmp_path / "nope")) is None


# --- 8d. 🔴 the ALLOWLIST ---------------------------------------------------- #
# RULES.md carves out exactly one exception to "feature branches only": a repo
# whose OWN CLAUDE.md states that committing to the main branch IS deploying.
# Today that is homelab-talos, where `trunk` is Flux-reconciled and its CLAUDE.md
# line 7 reads "Commit = live deploy".

def test_allowlist_is_pinned_by_name(tmp_path):
    """🔴 Pinned against a literal, like the policy list itself: an allowlist that
    can grow silently is a deny that can be switched off silently."""
    assert gc._TRUNK_DEPLOY_REPOS == frozenset({"ZacxDev/homelab-infra"}), (
        f"the trunk-deploy allowlist is {sorted(gc._TRUNK_DEPLOY_REPOS)}. Adding a repo "
        f"here disables a 🔴 deny for it and must be an explicit, reported decision — "
        f"it requires the target repo's OWN CLAUDE.md to state that committing to its "
        f"main branch IS deploying. Entries are `owner/repo` REMOTE SLUGS; a directory "
        f"name is not an identity and must never appear here."
    )


def test_allowlisted_repo_may_commit_to_trunk(tmp_path):
    repo = _mkrepo(tmp_path / "homelab-talos", branch="trunk",
                   remote="git@github.com:ZacxDev/homelab-infra.git")
    assert gc.check_git_commit_to_main("git commit -m deploy", str(repo)) is None


def test_a_directory_NAMED_like_the_allowlisted_repo_is_not_exempt(tmp_path):
    """🔴 Found by audit. The allowlist used to match the toplevel DIRECTORY name,
    so any checkout sitting in a folder called `homelab-talos` inherited the
    exemption regardless of its remote — including devrc itself. A directory name
    is chosen by whoever made the directory; only the remote identifies the repo.
    """
    impostor = _mkrepo(tmp_path / "homelab-talos", branch="main",
                       remote="git@github.com:innovation-upstream/devrc.git")
    assert gc.check_git_commit_to_main("git commit -m x", str(impostor)) is not None


def test_allowlisted_repo_is_matched_by_its_REMOTE_when_the_directory_differs(tmp_path):
    """🔴 THE CASE A PATH-KEYED ALLOWLIST WOULD HAVE MISSED.

    The working-tree directory is `homelab-talos`; the GitHub repo behind it is
    `ZacxDev/homelab-infra`. THE TWO NAMES DIFFER — verified against the real
    checkout's .git/config while building this guard. And homelab-talos's own
    CLAUDE.md tells agents to do commit-to-trunk work in a LINKED WORKTREE named
    `homelab-trunk`, which matches neither allowlist entry by directory. Without
    remote-matching, the guard would block the exact workflow that repo mandates.
    """
    wt = _mkrepo(tmp_path / "homelab-trunk", branch="trunk",
                 remote="git@github.com:ZacxDev/homelab-infra.git")
    assert gc.check_git_commit_to_main("git commit -m deploy", str(wt)) is None


@pytest.mark.parametrize("url", [
    "git@github.com:ZacxDev/homelab-infra.git",
    "https://github.com/ZacxDev/homelab-infra.git",
    "https://github.com/ZacxDev/homelab-infra",
    "ssh://git@github.com/ZacxDev/homelab-infra.git",
])
def test_allowlist_remote_matching_survives_every_url_form(tmp_path, url):
    """scp-style, https, and ssh:// URLs all name the same repo. A parser that
    handled only one form would allowlist by accident of how the remote was added."""
    repo = _mkrepo(tmp_path / "some-dir", branch="trunk", remote=url)
    assert gc.check_git_commit_to_main("git commit -m deploy", str(repo)) is None, url


def test_a_lookalike_repo_name_is_NOT_allowlisted(tmp_path):
    """The allowlist is exact-match. `homelab-infra-staging` is a different repo."""
    repo = _mkrepo(tmp_path / "homelab-infra-staging", branch="trunk",
                   remote="git@github.com:ZacxDev/homelab-infra-staging.git")
    assert gc.check_git_commit_to_main("git commit -m deploy", str(repo)) is not None


def test_an_unallowlisted_repo_on_trunk_is_still_denied(tmp_path):
    repo = _mkrepo(tmp_path / "other", branch="trunk",
                   remote="git@github.com:someone/other.git")
    assert gc.check_git_commit_to_main("git commit -m x", str(repo)) is not None


def test_devrc_itself_is_not_allowlisted(tmp_path):
    """The repo the rule was written FOR. If devrc ever landed in the allowlist,
    the guard would be inert for the only two incidents that motivated it."""
    repo = _mkrepo(tmp_path / "devrc", branch="main",
                   remote="git@github.com:innovation-upstream/devrc.git")
    assert gc.check_git_commit_to_main('git commit -m "wip"', str(repo)) is not None


# --- 8e. 🔴 REACHABILITY + ordering ------------------------------------------ #
# A guard that is never reached is green for the wrong reason: a mutation test
# still passes when an EARLIER check always wins, because a different check's
# error kills the test. These pin that check_git_commit_to_main is the check that
# actually fires, and that the two intended ordering trade-offs hold.

def test_commit_to_main_is_the_ONLY_check_that_fires_on_a_plain_commit(tmp_path):
    """🔴 Reachability. On the shape this guard exists for, no earlier check in
    the claude-code policy short-circuits — so the deny is attributable to THIS
    guard's specific message, not to a neighbour's."""
    repo = _mkrepo(tmp_path / "r", branch="main")
    cmd = 'git commit -m "wip"'
    matching = [c.__name__ for c in gc.POLICIES["claude-code"]
                if (c(cmd, str(repo)) if getattr(c, "wants_cwd", False) else c(cmd))]
    assert matching == ["check_git_commit_to_main"], matching
    assert gc.evaluate(cmd, "claude-code", str(repo)) == \
        gc.check_git_commit_to_main(cmd, str(repo))


def test_commit_to_main_outranks_the_cd_reason(tmp_path):
    """`cd <repo> && git commit` trips check_cd_then_git too. Ordering makes the
    reported reason the WRONG-BRANCH one — the more serious of the two."""
    repo = _mkrepo(tmp_path / "r", branch="main")
    cmd = f"cd {repo} && git commit -m wip"
    reason = gc.evaluate(cmd, "claude-code", str(repo))
    assert reason == gc.check_git_commit_to_main(cmd, str(repo))
    assert "feature branches only" in reason
    assert gc.check_cd_then_git(cmd) is not None   # the other check really does match


def test_node_wipe_still_outranks_the_commit_reason(tmp_path):
    """The other direction of the same ordering decision: a command that both
    wipes a Talos node and commits must report the NODE WIPE."""
    repo = _mkrepo(tmp_path / "r", branch="main")
    cmd = "talosctl -n 10.0.0.1 reset && git commit -m done"
    assert gc.evaluate(cmd, "claude-code", str(repo)) == gc.check_talosctl_reset(cmd)


def test_commit_to_main_is_inherited_by_the_opencode_policy(tmp_path):
    repo = _mkrepo(tmp_path / "r", branch="main")
    assert gc.evaluate('git commit -m "x"', "opencode", str(repo)) is not None


# --- 8f. the cwd seam -------------------------------------------------------- #

def test_evaluate_passes_cwd_only_to_checks_that_want_it(tmp_path):
    """The dispatch is the seam between `evaluate` and a world-reading check. If
    it silently stopped passing cwd, the guard would fall back to the hook
    process's own directory and quietly report on the wrong repo."""
    assert gc.check_git_commit_to_main.wants_cwd is True
    others = [c for c in gc.POLICIES["opencode"] if c is not gc.check_git_commit_to_main]
    assert not any(getattr(c, "wants_cwd", False) for c in others)


def test_evaluate_without_cwd_falls_back_and_does_not_raise():
    """Backwards compatibility: the old two-argument call must still work."""
    assert gc.evaluate("ls -la", "claude-code") is None
    assert gc.evaluate("git stash", "claude-code") is not None


def test_cli_accepts_and_honours_a_cwd_field(tmp_path):
    """The opencode seam end-to-end, through a real subprocess."""
    import json as _json
    repo = _mkrepo(tmp_path / "r", branch="main")
    core = str(Path(__file__).resolve().parents[1] / "guard_core.py")
    proc = subprocess.run(
        [sys.executable, core, "--policy", "claude-code"],
        input=_json.dumps({"command": 'git commit -m "x"', "cwd": str(repo)}),
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert _json.loads(proc.stdout)["decision"] == "deny"


def test_cli_still_works_when_cwd_is_omitted(tmp_path):
    """🔴 An older caller (today's opencode plugin sends only `command`) must still
    get a VERDICT. This seam is the one opencode fails CLOSED on, so tightening
    the schema here would deny every bash call in opencode."""
    import json as _json
    core = str(Path(__file__).resolve().parents[1] / "guard_core.py")
    proc = subprocess.run(
        [sys.executable, core, "--policy", "opencode"],
        input=_json.dumps({"command": "ls -la"}),
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert _json.loads(proc.stdout)["decision"] == "allow"


# --- 8g. the deny message ---------------------------------------------------- #

def test_deny_message_names_the_branch_the_repo_and_the_way_out(tmp_path):
    """A deny the model cannot act on becomes a deny it routes around."""
    repo = _mkrepo(tmp_path / "r", branch="main")
    reason = gc.check_git_commit_to_main("git commit -m x", str(repo))
    assert "`main`" in reason
    assert str(repo) in reason
    assert "git checkout -b" in reason        # the forward path
    assert "git reset --keep" in reason       # the recovery path, if already committed
    assert "--hard" not in reason             # 🔴 must never suggest the destructive one
    assert gc._QUOTING_ESCAPE_HATCH in reason


# --------------------------------------------------------------------------- #
# 9. 🔴 check_pkill_full_pattern — `pkill -f` matches the caller's OWN process
#
# RULES.md 🔴: "Never let a `-f` pattern reach `pkill`". `-f` matches the FULL
# command line of every process, so the pattern matches the very shell running the
# pkill — a background script that pkills "its own job" kills ITSELF. Measured
# ALLOW against the live hook before this landed.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("command", [
    "pkill -f e2e/run.sh",
    'pkill -f "opencode run"',
    "pkill --full e2e/run.sh",
    "pkill -9f e2e/run.sh",                 # bundled short flags
    "pkill -ef e2e/run.sh",
    "pkill -fu zach e2e/run.sh",
    "pkill -u zach -f e2e/run.sh",
    "sudo pkill -f e2e/run.sh",
    "FOO=1 pkill -f e2e/run.sh",
    "timeout 5 pkill -f e2e/run.sh",
    "/usr/bin/pkill -f e2e/run.sh",
    "ls && pkill -f e2e/run.sh",
    "ls; pkill -f e2e/run.sh",
    "bash -c 'pkill -f e2e/run.sh'",
    "pkill --signal TERM -f e2e/run.sh",
])
def test_pkill_full_pattern_is_denied(command):
    assert gc.check_pkill_full_pattern(command) is not None, command
    assert gc.evaluate(command, "claude-code") is not None, command


@pytest.mark.parametrize("command", [
    "pgrep -f e2e/run.sh",                  # the READ the deny message points at
    "pgrep -af opencode",
    "pkill firefox",                        # name match, cannot hit our own cmdline
    "pkill -u zach chromium",
    "pkill -F /var/run/thing.pid",          # uppercase -F is a pidfile, not --full
    "kill 12345",
    "kill -9 12345",
    "ps -ef | grep opencode",
    "echo 'never let a -f pattern reach pkill'",
])
def test_pkill_near_misses_stay_allowed(command):
    """The replacement recipe RULES prescribes runs THROUGH `pgrep -f`, so denying
    it would leave the deny message pointing at a blocked command."""
    assert gc.check_pkill_full_pattern(command) is None, command


def test_pkill_check_can_return_both_verdicts():
    """Negative + positive control on the check itself."""
    assert gc.check_pkill_full_pattern("pkill -f foo") is not None
    assert gc.check_pkill_full_pattern("pkill foo") is None


def test_pkill_is_the_only_check_that_fires_on_a_bare_pkill_dash_f():
    """🔴 Reachability: attributable to THIS guard, not to a neighbour."""
    cmd = "pkill -f e2e/run.sh"
    matching = [c.__name__ for c in gc.POLICIES["claude-code"]
                if (c(cmd, None) if getattr(c, "wants_cwd", False) else c(cmd))]
    assert matching == ["check_pkill_full_pattern"], matching


def test_pkill_deny_message_names_the_replacement_recipe():
    reason = gc.check_pkill_full_pattern("pkill -f e2e/run.sh")
    assert "pgrep -f" in reason
    assert "/proc/" in reason
    assert gc._QUOTING_ESCAPE_HATCH in reason


# 🔴 The handle these two tests use must be one that CANNOT resolve on any host.
# Written first as `$DEVRC` — the real house-style handle — which FAILED here for
# an instructive reason: `$DEVRC` IS exported on this workbench, so expandvars
# resolved it to the real devrc checkout (on `main`) and the test was measuring
# ambient host state instead of the fallback. It would have passed on a host
# without the handle and failed on one with it. `monkeypatch.delenv` below makes
# the unresolvable case unresolvable BY CONSTRUCTION.
_UNSET_HANDLE = "GUARD_TEST_HANDLE_THAT_IS_NEVER_SET"


def test_unresolvable_dash_c_falls_back_to_the_cwd(tmp_path, monkeypatch):
    """🔴 The house-style `git -C $DEVRC commit` spelling.

    The guard parses TEXT, so an unexported handle arrives unexpanded and names
    no directory. Measured before the fallback existed, with cwd on `main`:
        DENY   git commit -m x
        ALLOW  git -C $DEVRC commit -m x
    — blind to exactly the spelling this repo's CLAUDE.md pushes agents toward.
    An unresolvable `-C` now judges the cwd instead of learning nothing.
    """
    monkeypatch.delenv(_UNSET_HANDLE, raising=False)
    on_main = _mkrepo(tmp_path / "main-repo", branch="main")
    assert gc.check_git_commit_to_main(
        f"git -C ${_UNSET_HANDLE} commit -m x", str(on_main)) is not None
    assert gc.check_git_commit_to_main(
        "git -C /nonexistent/path commit -m x", str(on_main)) is not None


def test_unresolvable_dash_c_fallback_does_not_invent_a_deny(tmp_path, monkeypatch):
    """The other half: falling back to the cwd must not manufacture a deny when
    the cwd is itself fine. Without this, the fallback could read as 'deny more'
    rather than 'judge the best directory available'."""
    monkeypatch.delenv(_UNSET_HANDLE, raising=False)
    on_feat = _mkrepo(tmp_path / "feat-repo", branch="feat/x")
    assert gc.check_git_commit_to_main(
        f"git -C ${_UNSET_HANDLE} commit -m x", str(on_feat)) is None


def test_a_resolvable_dash_c_still_wins_over_the_cwd(tmp_path):
    """The fallback must not shadow the normal path: when `-C` DOES resolve, it
    is what gets judged."""
    on_main = _mkrepo(tmp_path / "main-repo", branch="main")
    on_feat = _mkrepo(tmp_path / "feat-repo", branch="feat/x")
    assert gc.check_git_commit_to_main(
        f"git -C {on_feat} commit -m x", str(on_main)) is None
    assert gc.check_git_commit_to_main(
        f"git -C {on_main} commit -m x", str(on_feat)) is not None


def test_expandvars_resolves_a_handle_that_IS_in_the_environment(tmp_path, monkeypatch):
    """`expandvars` is tried before the fallback, so a handle the hook really does
    inherit resolves to the repo it names rather than to the cwd."""
    on_main = _mkrepo(tmp_path / "main-repo", branch="main")
    on_feat = _mkrepo(tmp_path / "feat-repo", branch="feat/x")
    monkeypatch.setenv("SOME_REPO_HANDLE", str(on_main))
    assert gc.check_git_commit_to_main(
        "git -C $SOME_REPO_HANDLE commit -m x", str(on_feat)) is not None


# --- 8d(ii). 🔴 the allowlist is OWNER-qualified ----------------------------- #
# Found by an adversarial probe during review, not by a test looking for it:
# matching a remote on its BARE repo name allowlisted a FORK
# (`someoneelse/homelab-infra`) and `git@evil.example:x/homelab-infra`, because
# "the last path component" is not an identity. Entries are `owner/repo` now.

@pytest.mark.parametrize("label,url", [
    ("a fork under a different owner", "git@github.com:someoneelse/homelab-infra.git"),
    ("a lookalike host",               "git@evil.example:x/homelab-infra.git"),
    ("owner named like the repo",      "git@github.com:homelab-infra/other.git"),
    ("suffixed repo",                  "git@github.com:ZacxDev/homelab-infra-staging.git"),
    ("prefixed repo",                  "git@github.com:ZacxDev/my-homelab-infra.git"),
])
def test_allowlist_rejects_a_fork_or_a_lookalike_host(tmp_path, label, url):
    """None of these is the repo whose CLAUDE.md declares commit = live deploy,
    so none of them may inherit its exemption."""
    repo = _mkrepo(tmp_path / label.replace(" ", "_"), branch="trunk", remote=url)
    assert gc.check_git_commit_to_main("git commit -m x", str(repo)) is not None, url


def test_the_real_owner_repo_is_still_allowlisted(tmp_path):
    """The other half — tightening must not break the case it exists to allow."""
    repo = _mkrepo(tmp_path / "homelab-trunk", branch="trunk",
                   remote="git@github.com:ZacxDev/homelab-infra.git")
    assert gc.check_git_commit_to_main("git commit -m deploy", str(repo)) is None


@pytest.mark.parametrize("url,slug", [
    ("git@github.com:ZacxDev/homelab-infra.git",        "ZacxDev/homelab-infra"),
    ("https://github.com/ZacxDev/homelab-infra.git",    "ZacxDev/homelab-infra"),
    ("https://github.com/ZacxDev/homelab-infra",        "ZacxDev/homelab-infra"),
    ("ssh://git@github.com/ZacxDev/homelab-infra.git",  "ZacxDev/homelab-infra"),
    ("git://github.com/ZacxDev/homelab-infra.git",      "ZacxDev/homelab-infra"),
    ("https://github.com/ZacxDev/homelab-infra/",       "ZacxDev/homelab-infra"),
    ("git@github.com:someoneelse/homelab-infra.git",    "someoneelse/homelab-infra"),
    ("/srv/mirrors/deep/path/owner/repo.git",           "owner/repo"),
    ("just-a-name",                                     None),
    ("",                                                None),
    (None,                                              None),
])
def test_remote_slug_parses_every_url_spelling(url, slug):
    """Pinned against literal expected values rather than derived from the parser
    — a slug helper that agreed with itself would prove nothing."""
    assert gc._remote_slug(url) == slug


# --------------------------------------------------------------------------- #
# 10. 🔴 REGRESSIONS FOR THE ADVERSARIAL AUDIT OF #376
#
# Every case here was measured ALLOW (or a crash) against the first version of
# this guard. None was found by the tests above — they were found by an audit
# that went looking for the re-spelling a blocked model would try next. That is
# the point of the section: a deny whose adjacent re-spelling succeeds is worse
# than no deny, because it launders the action into one the guard has blessed.
# --------------------------------------------------------------------------- #

def test_a_deleted_cwd_does_not_crash_the_guard(tmp_path, monkeypatch):
    """🔴 HIGHEST-severity finding: `os.getcwd()` RAISES when the process's cwd
    has been deleted, and it ran on EVERY command. The exception escaped
    `evaluate`, and both callers fail CLOSED on an exception — bash-guard.py
    denies, and the opencode plugin denies on the CLI's rc=2. So a stale cwd (a
    worktree another session removed) would have refused EVERY bash call in the
    session, for every command, until the directory came back.

    Reproduced by actually deleting the process's cwd, not by mocking getcwd —
    a mock would have asserted the fix against my own idea of the failure.
    """
    doomed = tmp_path / "doomed"
    doomed.mkdir()
    original = os.getcwd()
    try:
        os.chdir(doomed)
        doomed.rmdir()
        with pytest.raises(OSError):          # the hazard is real, not theoretical
            os.getcwd()
        for policy in ("claude-code", "opencode"):
            assert gc.evaluate("ls -la", policy) is None
            assert gc.evaluate("git commit -m x", policy) is None
            assert gc.evaluate("git stash", policy) is not None   # unrelated denies survive
    finally:
        os.chdir(original)


def test_safe_getcwd_returns_none_rather_than_raising(tmp_path):
    doomed = tmp_path / "doomed2"
    doomed.mkdir()
    original = os.getcwd()
    try:
        os.chdir(doomed)
        doomed.rmdir()
        assert gc._safe_getcwd() is None
    finally:
        os.chdir(original)


@pytest.mark.parametrize("template", [
    "(cd {main} && git commit -m x)",                  # a subshell
    "ls; cd {main}; git commit -m x",                  # a NON-leading cd
    "git status && cd {main} && git commit -m x",
    "bash -c 'cd {main} && git commit -m x'",          # inside a nested shell
    "pushd {main} && git commit -m x",
    "cd {main} && ls && git commit -m x",
    "sh -c \"cd {main}; git commit -m x\"",
    "GIT_DIR={main}/.git GIT_WORK_TREE={main} git commit -m x",
    "git --git-dir={main}/.git --work-tree={main} commit -m x",
    "git --git-dir {main}/.git commit -m x",
])
def test_directory_hops_other_than_a_leading_cd_are_caught(tmp_path, template):
    """🔴 The guard was TEACHING ITS OWN BYPASS: `cd <main> && git commit` denied
    (via check_cd_then_git), while a subshell, a `bash -c`, a non-leading `cd`
    and the `--git-dir` hop all ALLOWED — measured end-to-end against the real
    adapter during audit. The cwd here is a FEATURE-branch repo, so a deny can
    only come from the hop being resolved."""
    on_main = _mkrepo(tmp_path / "main-repo", branch="main")
    on_feat = _mkrepo(tmp_path / "feat-repo", branch="feat/x")
    cmd = template.format(main=on_main)
    assert gc.check_git_commit_to_main(cmd, str(on_feat)) is not None, cmd


@pytest.mark.parametrize("template", [
    "(cd {feat} && git commit -m x)",
    "ls; cd {feat}; git commit -m x",
    "bash -c 'cd {feat} && git commit -m x'",
    "GIT_DIR={feat}/.git GIT_WORK_TREE={feat} git commit -m x",
    "git --git-dir={feat}/.git --work-tree={feat} commit -m x",
])
def test_the_same_hops_into_a_feature_branch_stay_allowed(tmp_path, template):
    """The other half — the hop must be RESOLVED, not merely treated as suspicious.
    Without this, "the guard got stricter" and "the guard now denies every commit
    that mentions a directory" look identical from the green side.

    The cwd is a NON-REPO here on purpose, to isolate the hop: the caller's cwd is
    always a candidate too (see the next test), so judging this from a cwd that
    sits on `main` would measure that rule instead of this one.
    """
    neutral = tmp_path / "neutral"
    neutral.mkdir()
    on_feat = _mkrepo(tmp_path / "feat-repo", branch="feat/x")
    cmd = template.format(feat=on_feat)
    assert gc.check_git_commit_to_main(cmd, str(neutral)) is None, cmd


def test_a_cd_MOVES_the_judged_directory_rather_than_adding_a_candidate(tmp_path):
    """🔴 A DELIBERATE BEHAVIOUR CHANGE (2026-08-11), replacing the earlier
    `test_the_cwd_REMAINS_a_candidate_even_when_the_line_cds_elsewhere`.

    That test pinned a fail-CLOSED over-approximation: for a bare `git commit`
    the guard judged the UNION of the caller's cwd and every `cd` target in the
    line, on the argument that it "cannot know which `cd` won". It can, if it
    tracks position and subshell scope — and the over-approximation was not free.
    It denied the mandated worktree spellings
        (cd <worktree-on-a-topic-branch> && git commit …)
        bash -c 'cd <worktree-on-a-topic-branch> && git commit …'
    from any session whose cwd merely HAPPENED to sit on a main branch, which is
    the normal state of a primary clone. A guard that fires on the workflow its
    own deny message RECOMMENDS is the failure mode `_IRREVERSIBLE_CHECKS`
    documents: it trains its subject to route around it.

    The cwd is no longer a candidate once the line has moved somewhere else. The
    directions that must NOT change are the next three tests: a cd INTO a blocked
    repo still denies, a cd that does not survive its subshell does not move
    anything, and an UNRESOLVABLE cd leaves the cwd in play.
    """
    on_main = _mkrepo(tmp_path / "main-repo", branch="main")
    on_feat = _mkrepo(tmp_path / "feat-repo", branch="feat/x")
    assert gc.check_git_commit_to_main(
        f"cd {on_feat} && git commit -m x", str(on_main)) is None
    # …and the POLICY outcome for this exact spelling is unchanged: `cd <path> &&
    # git …` is still denied end-to-end, by check_cd_then_git, which is what the
    # caller actually experiences. Asserted so "this loosened the guard" and "this
    # moved which check reports it" cannot be confused.
    assert gc.evaluate(f"cd {on_feat} && git commit -m x", "claude-code",
                       str(on_main)) is not None


def test_an_unresolvable_cd_leaves_the_callers_cwd_in_play(tmp_path):
    """The fail-CLOSED half of the change above. A `cd` the guard cannot resolve
    did not necessarily happen, so it must not move the judged directory — the
    commit may well land in the caller's own repo."""
    on_main = _mkrepo(tmp_path / "main-repo", branch="main")
    assert gc.check_git_commit_to_main(
        "cd /definitely/not/here && git commit -m x", str(on_main)) is not None


def test_a_cd_inside_a_subshell_does_not_leak_out_of_it(tmp_path):
    """🔴 The scope half, and the reason positional tracking needs `_scan`'s depth
    rather than a regex. `( cd <feat>; ls )` does NOT move the caller, so the
    `git commit` after it still runs — and is still judged — in the cwd."""
    on_main = _mkrepo(tmp_path / "main-repo", branch="main")
    on_feat = _mkrepo(tmp_path / "feat-repo", branch="feat/x")
    reason = gc.check_git_commit_to_main(
        f"(cd {on_feat}; ls); git commit -m x", str(on_main))
    assert reason is not None
    assert str(on_main) in reason, "the deny must name the directory it actually judged"
    # The mirror image: INSIDE the subshell the cd does apply.
    assert gc.check_git_commit_to_main(
        f"(cd {on_feat} && git commit -m x)", str(on_main)) is None


def test_an_explicit_named_repo_beats_the_cwd_and_the_cd_candidates(tmp_path):
    """The split between NAMED and INFERRED directories. `git -C <feat>` says
    where it acts, so neither the cwd nor a stray `cd` may override it — that is
    what keeps the documented `git -C $WT commit` spelling usable from a checkout
    sitting on main."""
    on_main = _mkrepo(tmp_path / "main-repo", branch="main")
    on_feat = _mkrepo(tmp_path / "feat-repo", branch="feat/x")
    assert gc.check_git_commit_to_main(
        f"cd {on_main} && git -C {on_feat} commit -m x", str(on_main)) is None


def test_cd_to_a_RELATIVE_path_resolves_against_the_payload_cwd(tmp_path):
    """🔴 Audit finding: the leading-cd branch called `os.path.isdir()` on the raw
    relative target, so it resolved against the GUARD PROCESS's cwd — naming a
    repo the command never touches. Now joined to the payload cwd, like `-C`."""
    _mkrepo(tmp_path / "nested" / "repo", branch="main")
    outer = tmp_path / "nested"
    assert gc.check_git_commit_to_main("cd repo && git commit -m x", str(outer)) is not None


@pytest.mark.parametrize("flag,is_dry", [
    ("--dry-run", True),
    ("--dry-ru", True),
    ("--dry-r", True),
    ("--dry", True),          # git accepts any unambiguous long-option prefix
    ("--dr", False),          # ambiguous-ish / not a prefix we honour
    ("-n", False),            # 🔴 --no-verify: COMMITS, skipping hooks
    ("--no-verify", False),
    ("--dry-run-nonsense", False),
])
def test_dry_run_abbreviations(tmp_path, flag, is_dry):
    """git accepts `--dry` as `--dry-run` (verified: rc=0, no commit created), so
    an exact-string test denied a READ. `-n` must NEVER be read as a dry run."""
    repo = _mkrepo(tmp_path / "r", branch="main")
    denied = gc.check_git_commit_to_main(f"git commit {flag}", str(repo)) is not None
    assert denied == (not is_dry), f"{flag} -> denied={denied}"


def test_the_deny_path_does_not_exec_git_twice_for_the_same_question(tmp_path, monkeypatch):
    """Audit finding: `git remote` was executed twice on every deny — once inside
    the name lookup and once for the no-remotes carve-out. Pinned as a COUNT, so
    a future refactor that reintroduces the duplicate is visible."""
    repo = _mkrepo(tmp_path / "r", branch="main")
    seen = []
    real = gc.subprocess.run

    def counting(argv, *a, **k):
        if isinstance(argv, (list, tuple)) and argv and argv[0] == "git":
            seen.append(tuple(argv[3:]))    # ['git', '-C', <dir>, <subcommand>, …]
        return real(argv, *a, **k)

    monkeypatch.setattr(gc.subprocess, "run", counting)
    assert gc.check_git_commit_to_main("git commit -m x", str(repo)) is not None
    # `("remote",)` is the LIST call; `("remote", "get-url", <name>)` is a
    # different question and is expected once per remote. Counting the bare tuple
    # keeps the two apart — an earlier version of this assertion conflated them
    # and reported a duplicate that was not one.
    listings = seen.count(("remote",))
    assert listings == 1, f"`git remote` (list) exec'd {listings}x: {seen}"


def test_ordinary_commands_never_exec_git(tmp_path, monkeypatch):
    """🔴 The hot-path promise, asserted rather than claimed: this hook runs on
    EVERY Bash call, so a non-commit command must pay zero subprocesses."""
    repo = _mkrepo(tmp_path / "r", branch="main")
    execs = []
    real = gc.subprocess.run

    def counting(argv, *a, **k):
        if isinstance(argv, (list, tuple)) and argv and argv[0] == "git":
            execs.append(argv)
        return real(argv, *a, **k)

    monkeypatch.setattr(gc.subprocess, "run", counting)
    for cmd in ["ls -la", "rg foo src/", "kubectl get pods", "npm run build",
                "git status", "git log --oneline -3", "git add scripts/x.py",
                "git commit --dry-run", "pkill -f x"]:
        gc.evaluate(cmd, "claude-code", str(repo))
    assert execs == [], f"{len(execs)} git exec(s) on non-commit commands: {execs[:3]}"


def test_expandvars_applies_to_cd_targets_too_not_only_to_dash_C(tmp_path, monkeypatch):
    """🔴 Added because a mutation sweep found a SURVIVING mutant: removing
    `expandvars` from `_resolve_dir` broke nothing, since the only handle test
    went through `_dash_c_dir`'s own duplicate copy of that logic. The duplicate
    is now consolidated, and this pins the path the duplicate was hiding —
    `cd $HANDLE && git commit`, which is how the handle actually gets used.
    """
    on_main = _mkrepo(tmp_path / "main-repo", branch="main")
    neutral = tmp_path / "neutral"
    neutral.mkdir()
    monkeypatch.setenv("GUARD_TEST_MAIN_REPO", str(on_main))
    assert gc.check_git_commit_to_main(
        "cd $GUARD_TEST_MAIN_REPO && git commit -m x", str(neutral)) is not None
    assert gc.check_git_commit_to_main(
        "bash -c 'cd $GUARD_TEST_MAIN_REPO && git commit -m x'", str(neutral)) is not None


def test_resolve_dir_is_the_single_path_resolver(tmp_path, monkeypatch):
    """The consolidation itself, asserted at the unit: `-C` and `cd` must agree,
    because they are now the same code. A future re-duplication makes them
    diverge and this goes red."""
    on_main = _mkrepo(tmp_path / "main-repo", branch="main")
    monkeypatch.setenv("GUARD_TEST_MAIN_REPO", str(on_main))
    neutral = tmp_path / "neutral2"
    neutral.mkdir()
    assert gc._resolve_dir("$GUARD_TEST_MAIN_REPO", str(neutral)) == str(on_main)
    assert gc._resolve_dir("main-repo", str(tmp_path)) == str(on_main)
    assert gc._resolve_dir(str(on_main / ".git"), None) == str(on_main)
    assert gc._resolve_dir("/definitely/not/here", None) is None
    assert gc._resolve_dir(None, None) is None


# =========================================================================== #
# 8x. 🔴 WHICH REPO GETS JUDGED — the named-target fail-OPEN (2026-08-11)
#
# THE REPORTED SYMPTOM was a false POSITIVE: with cwd on `trunk`,
#     git -C /home/zach/workspace/civit/spine-wan-peer-metric commit -F <msg>
# was denied naming the CWD's repo, even though the `-C` target sat on a feature
# branch. That direction is ALREADY FIXED — `_argv_named_repo_dirs` landed with
# #376 and the probe below is green on `origin/main`. These tests pin it so it
# stays fixed, and are labelled INVARIANT GUARDS, not regression coverage:
# `test_S2_*`, `test_S3_*`, `test_S4_*`, `test_S5_*` are all green at
# `origin/main`. Per claude/RULES.md, a guard pinning an invariant the bug never
# violated does not get counted as regression coverage.
#
# 🔴 WHAT WAS ACTUALLY BROKEN — found by probing the LIVE hook rather than by
# reading the report — is the fail-OPEN sibling of that same resolution step:
#     ALLOW  git -C <an-ordinary-directory> commit -m x      (cwd on trunk)
# Naming a repo hands the entire verdict to that repo. When the named directory
# EXISTS but is not a git worktree, `_resolve_dir` accepts it (it only asks
# `isdir`) and `_commit_to_main_reason` then fails open on "no branch" — so the
# cwd, which really was on trunk, is never evaluated at all. One `-C` at a
# harmless-looking path disables the check for the whole command. That is the
# fail-open the guard exists to prevent, and `test_S6_*` is REGRESSION coverage:
# it is RED at `origin/main` and green at HEAD.
# --------------------------------------------------------------------------- #

_FEATURE = "feat/wan-peer-availability-metric"     # the branch from the report
_TALOS = "git@github.com:ZacxDev/homelab-infra.git"  # the ONE allowlisted repo


def test_S1_bare_commit_from_a_trunk_cwd_is_still_blocked(tmp_path):
    """Scenario 1 — the core guarantee. Everything else here is a refinement of
    WHICH repo gets judged; if this ever goes green-allow the guard is inert."""
    repo = _mkrepo(tmp_path / "cwd-trunk", branch="trunk")
    assert gc.check_git_commit_to_main("git commit -m x", str(repo)) is not None


def test_S2_dash_C_to_a_feature_repo_from_a_trunk_cwd_is_allowed(tmp_path):
    """Scenario 2 — the reported false positive. INVARIANT GUARD (green at
    origin/main). The command said where it acts; the cwd's branch is not the
    question being asked."""
    cwd = _mkrepo(tmp_path / "cwd-trunk", branch="trunk")
    target = _mkrepo(tmp_path / "target-feat", branch=_FEATURE)
    assert gc.check_git_commit_to_main(
        f"git -C {target} commit -m x", str(cwd)) is None
    # …and with the exact `-F <msgfile>` spelling from the incident report.
    assert gc.check_git_commit_to_main(
        f"git -C {target} commit -F /tmp/msg.txt", str(cwd)) is None


def test_S3_dash_C_to_a_trunk_repo_from_a_feature_cwd_is_blocked(tmp_path):
    """Scenario 3 — the bidirectional half, and the one that would be a real
    hole. INVARIANT GUARD (green at origin/main). A feature-branch cwd must not
    vouch for a `-C` target sitting on trunk."""
    cwd = _mkrepo(tmp_path / "cwd-feat", branch=_FEATURE)
    target = _mkrepo(tmp_path / "target-trunk", branch="trunk")
    reason = gc.check_git_commit_to_main(f"git -C {target} commit -m x", str(cwd))
    assert reason is not None
    # The message must name the repo it JUDGED, not the cwd — the misattribution
    # in the report ("repo: …/civitai-gpu-fleet") is what made it unreadable.
    assert str(target) in reason and str(cwd) not in reason


def test_S4_dash_C_to_the_allowlisted_trunk_deploy_repo_is_allowed(tmp_path):
    """Scenario 4 — the allowlist must survive the `-C` hop. INVARIANT GUARD.
    Judging the cwd instead would deny a commit that IS the correct workflow."""
    cwd = _mkrepo(tmp_path / "cwd-trunk", branch="trunk")
    talos = _mkrepo(tmp_path / "homelab-talos", branch="trunk", remote=_TALOS)
    assert gc.check_git_commit_to_main(f"git -C {talos} commit -m deploy", str(cwd)) is None


def test_S5_dash_C_after_the_subcommand_is_not_a_repo_selector(tmp_path):
    """Scenario 5 — `git commit -C HEAD~1` is "reuse that commit's message", a
    completely different flag. Reading it as a repo path is the permissive
    misparse: it would hop the verdict away from the cwd. INVARIANT GUARD."""
    repo = _mkrepo(tmp_path / "cwd-trunk", branch="trunk")
    for cmd in ["git commit -C HEAD~1", "git commit -C HEAD", "git commit --reuse-message=HEAD~1"]:
        assert gc.check_git_commit_to_main(cmd, str(repo)) is not None, cmd
    # The collision made concrete: a real feature repo named AFTER the subcommand
    # must not launder the commit either — post-subcommand `-C` is not a path.
    feat = _mkrepo(tmp_path / "feat", branch=_FEATURE)
    assert gc.check_git_commit_to_main(f"git commit -C {feat}", str(repo)) is not None


def test_S6_a_named_target_that_is_not_a_repo_fails_CLOSED(tmp_path):
    """🔴 Scenario 6 — REGRESSION coverage. RED at origin/main, green at HEAD.

    An existing-but-not-a-git directory passed to `-C` used to suppress the check
    entirely (`_resolve_dir` accepts any real dir; `_commit_to_main_reason` then
    fails open on "no branch"), so the trunk cwd was never judged. The verdict
    must fall back to the cwd and BLOCK.
    """
    cwd = _mkrepo(tmp_path / "cwd-trunk", branch="trunk")
    plain = tmp_path / "just-a-directory"
    plain.mkdir()
    assert gc._git_read(str(plain), "rev-parse", "--show-toplevel") is None  # precondition
    assert gc.check_git_commit_to_main(f"git -C {plain} commit -m x", str(cwd)) is not None
    # …the same hole through git's other repo-selecting globals.
    assert gc.check_git_commit_to_main(
        f"git --git-dir={plain} commit -m x", str(cwd)) is not None
    assert gc.check_git_commit_to_main(
        f"git --work-tree {plain} commit -m x", str(cwd)) is not None


def test_S6b_a_junk_named_target_cannot_be_vouched_for_by_a_real_one(tmp_path):
    """The MIXED named set — one resolvable repo plus one junk target. Ambiguous
    about where the commit lands, so it fails closed onto the cwd. This is the
    test that distinguishes `not all(...)` from `not any(...)`."""
    cwd = _mkrepo(tmp_path / "cwd-trunk", branch="trunk")
    feat = _mkrepo(tmp_path / "feat", branch=_FEATURE)
    plain = tmp_path / "just-a-directory"
    plain.mkdir()
    assert gc.check_git_commit_to_main(
        f"git -C {feat} --git-dir={plain} commit -m x", str(cwd)) is not None


def test_S6d_the_deny_explains_WHY_it_judged_the_cwd_instead(tmp_path):
    """Falling back to the cwd produces a deny naming a repo the user never wrote
    in the command — unreadable unless it says why. The two fallbacks have
    DIFFERENT causes and must not share one message: `unresolved` is a path the
    guard could not turn into a directory (a shell variable), this one is a path
    that resolved fine and simply is not a repo. Asserted on the STATE (the named
    path appears, and the not-a-worktree wording) rather than on any single word
    another branch could also spell.
    """
    cwd = _mkrepo(tmp_path / "cwd-trunk", branch="trunk")
    plain = tmp_path / "just-a-directory"
    plain.mkdir()
    reason = gc.check_git_commit_to_main(f"git -C {plain} commit -m x", str(cwd))
    assert reason is not None
    assert str(plain) in reason
    assert "not a git worktree" in reason
    # …and it must NOT claim the path was unresolvable — that is the other cause.
    assert "could not resolve" not in reason


def test_S6c_failing_closed_does_not_block_a_harmless_cwd(tmp_path):
    """The other side of S6: falling back to the cwd is a FALLBACK, not a blanket
    deny. From a feature-branch cwd the same unjudgeable `-C` stays allowed —
    otherwise the fix would be indistinguishable from "always block"."""
    cwd = _mkrepo(tmp_path / "cwd-feat", branch=_FEATURE)
    plain = tmp_path / "just-a-directory"
    plain.mkdir()
    assert gc.check_git_commit_to_main(f"git -C {plain} commit -m x", str(cwd)) is None


@pytest.mark.parametrize("branch", ["main", "master", "trunk"])
def test_S7_every_main_branch_name_blocks_through_the_dash_C_hop(tmp_path, branch):
    """Scenario 7 — all three names, judged through `-C` rather than the cwd, so
    this pins the branch set on the RESOLVED repo and not just on the cwd path."""
    cwd = _mkrepo(tmp_path / "cwd-feat", branch=_FEATURE)
    target = _mkrepo(tmp_path / f"target-{branch.replace('/', '-')}", branch=branch)
    assert gc.check_git_commit_to_main(
        f"git -C {target} commit -m x", str(cwd)) is not None, branch


def test_is_git_worktree_can_return_both_verdicts(tmp_path):
    """Positive + negative control on the new helper itself. A helper stubbed to
    a constant would make S6 pass for the wrong reason in one direction and S2 in
    the other, so both are asserted here rather than inferred."""
    repo = _mkrepo(tmp_path / "r", branch="main")
    plain = tmp_path / "plain"
    plain.mkdir()
    assert gc._is_git_worktree(str(repo)) is True
    assert gc._is_git_worktree(str(plain)) is False
    assert gc._is_git_worktree(str(tmp_path / "absent")) is False
    assert gc._is_git_worktree(None) is False

# --- 9. 🔴 WHICH TREE, AND THE HEREDOC SWALLOW (2026-08-11) ----------------- #
#
# Two defects, found by replaying real session transcripts rather than by
# reading the code:
#
#   1. The guard resolved the branch from the AMBIENT repo. `git -C "$WT"
#      commit` — the spelling both this repo's and datapacket-talos's CLAUDE.md
#      MANDATE — carries the worktree in a shell variable the hook process has
#      never heard of, so `_resolve_dir` returned None, the guard fell back to
#      the caller's cwd, and denied a commit into a topic-branch worktree by
#      naming a primary clone the command does not touch. Same for the `cd`
#      spellings, which were judged as a UNION with the cwd.
#
#   2. A heredoc body is prose, and the scanner tracked quote state through it.
#      One apostrophe (`don't`, `it's`) opened a quote that never closed, and
#      EVERY command after the heredoc was swallowed into the quoted buffer
#      instead of being parsed — silently disabling every argv check for the
#      rest of the line, not just this one.
#
# Every test below was watched RED against the pre-change guard_core.py and
# GREEN at HEAD; the matrix is in the PR body.
# =========================================================================== #

def _mkworktree(repo, path, branch):
    """A real linked worktree of `repo` at `path`, checked out on `branch`."""
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", branch,
                    str(path)], capture_output=True, text=True, check=True)
    return path


def _trunk_and_worktree(tmp_path):
    """A clone on `trunk` + a linked worktree on a topic branch. The shape the
    whole worktree workflow is made of, and the one the guard got wrong."""
    repo = _mkrepo(tmp_path / "clone", branch="trunk", commit=True)
    wt = _mkworktree(repo, tmp_path / "wt-topic", "zach/topic")
    return repo, wt


# --- 9a. NEGATIVE CONTROLS — these must still BLOCK -------------------------- #
# 🔴 Read this section before the next one. A "fix" for a false positive is only
# as good as the denies it leaves standing, and the dangerous direction here is
# permissiveness. Each of these was verified to DENY both before and after.

@pytest.mark.parametrize("branch", ["main", "master", "trunk"])
def test_control_1_commit_in_a_clone_on_a_main_branch_still_blocks(tmp_path, branch):
    """Control 1: the base case the whole check exists for, re-asserted through
    the new resolution path."""
    repo = _mkrepo(tmp_path / "r", branch=branch)
    assert gc.evaluate(f"git commit -m x", "claude-code", str(repo)) is not None


@pytest.mark.parametrize("branch", ["main", "master", "trunk"])
def test_control_2_a_heredoc_message_body_does_not_hide_the_commit(tmp_path, branch):
    """Control 2: the same commit with its message in a heredoc. `-F -` plus a
    body must be exactly as blocked as `-F <file>` — the two spellings differ
    only in where the bytes come from."""
    repo = _mkrepo(tmp_path / "r", branch=branch)
    cmd = "git commit -q -F - <<'MSG'\ndocs: a message\n\nwith a body\nMSG"
    assert gc.evaluate(cmd, "claude-code", str(repo)) is not None
    # …and with an apostrophe in it, which is what broke the scanner.
    apostrophe = "git commit -q -F - <<'MSG'\ndocs: don't lose this\nMSG"
    assert gc.evaluate(apostrophe, "claude-code", str(repo)) is not None


def test_control_3_dash_c_resolution_is_not_a_bypass_vector(tmp_path):
    """Control 3: honouring `-C` must not become a way OUT. A `-C` that names a
    repo on a main branch denies, from any cwd — including a topic-branch
    worktree, which is the cwd an evader would pick."""
    repo, wt = _trunk_and_worktree(tmp_path)
    for cmd in [
        f"git -C {repo} commit -m x",
        f"git -C {repo} commit -q -F - <<'MSG'\nit's a message\nMSG",
        f"REPO={repo}\ngit -C \"$REPO\" commit -m x",
        f"cd {repo} && git commit -m x",
        f"(cd {repo} && git commit -m x)",
        f"bash -c 'cd {repo} && git commit -m x'",
        f"GIT_DIR={repo}/.git GIT_WORK_TREE={repo} git commit -m x",
    ]:
        assert gc.evaluate(cmd, "claude-code", str(wt)) is not None, cmd


def test_variable_resolution_never_turns_a_blocked_repo_into_an_allowed_one(tmp_path):
    """The generalisation of control 3: resolving `$VAR` is only ever ALLOWED to
    change WHICH repo is judged, never whether the verdict is read at all."""
    repo, wt = _trunk_and_worktree(tmp_path)
    env = tmp_path / "bad.env"
    env.write_text(f"WT={repo}\n")
    assert gc.check_git_commit_to_main(
        f". {env}\ngit -C \"$WT\" commit -m x", str(wt)) is not None


def test_a_variable_assigned_twice_is_not_guessed(tmp_path):
    """Ambiguity falls back to the cwd rather than picking a winner. Two
    different literals for one name means the guard does not know the value, and
    a guard that does not know must not pretend it does."""
    repo, wt = _trunk_and_worktree(tmp_path)
    cmd = f"WT={wt}\nWT={repo}\ngit -C \"$WT\" commit -m x"
    assert gc.check_git_commit_to_main(cmd, str(repo)) is not None


def test_a_heredoc_BODY_cannot_inject_a_variable(tmp_path):
    """🔴 A hole this change would have opened in itself, closed before it
    shipped. Variable resolution reads the real command line only — if a heredoc
    body's lines could register assignments, the COMMIT MESSAGE would get to
    decide which repository the guard judges. Here the body claims `WT` points at
    a harmless worktree; the deny must stand, because the message text does not
    get a vote."""
    repo, wt = _trunk_and_worktree(tmp_path)
    cmd = (f"git commit -q -F - <<'MSG'\n"
           f"docs: a message that happens to contain WT={wt}\nMSG")
    assert gc.check_git_commit_to_main(cmd, str(repo)) is not None
    # …and the same via `-C "$WT"`, where the injected value would be USED.
    cmd2 = (f"git -C \"$WT\" commit -q -F - <<'MSG'\n"
            f"WT={wt}\nMSG")
    assert gc.check_git_commit_to_main(cmd2, str(repo)) is not None


def test_an_unresolvable_dash_c_says_so_in_the_deny(tmp_path, monkeypatch):
    """The fallback still fires, and now EXPLAINS itself — a deny naming a repo
    the command never mentioned is what made the original false positive so hard
    to read."""
    monkeypatch.delenv(_UNSET_HANDLE, raising=False)
    repo = _mkrepo(tmp_path / "r", branch="trunk")
    reason = gc.check_git_commit_to_main(
        f"git -C ${_UNSET_HANDLE} commit -m x", str(repo))
    assert reason is not None
    assert "could not resolve" in reason
    assert _UNSET_HANDLE in reason


# --- 9b. 🔴 THE HEREDOC SWALLOW --------------------------------------------- #

@pytest.mark.parametrize("victim", [
    "git commit -m x",                      # this check
    "git stash push -m wip",                # …and every other argv check
    "git -C /tmp reset --hard origin/main",
    "git clean -fd",
    "talosctl -n 1.2.3.4 reset",
    "mkfs.ext4 /dev/sdc",
    "dd if=/dev/zero of=/dev/sdc",
    "pkill -f e2e/run.sh",
])
def test_a_heredoc_body_cannot_swallow_the_command_after_it(tmp_path, victim):
    """🔴 THE REGRESSION. Measured ALLOW on the shipped guard for every victim
    below — one apostrophe in a message body disabled the ENTIRE argv half of the
    guard for the rest of the command line.

    Parametrised over checks from different families on purpose: the bug was in
    the SHARED scanner, so a single-check test would have understated it as a
    commit-to-main quirk.
    """
    repo = _mkrepo(tmp_path / "r", branch="trunk")
    cmd = f"cat > /tmp/m.txt <<'EOF'\nfix: don't blindly stage\nEOF\n{victim}"
    assert gc.evaluate(cmd, "claude-code", str(repo)) is not None, cmd


def test_the_apostrophe_is_the_discriminator_not_the_heredoc(tmp_path):
    """🔴 The control that NAMES the mechanism. The two commands differ by one
    character. Without this pair, "heredocs break the guard" and "an unbalanced
    quote breaks the guard" are indistinguishable — and only the second is true,
    which is why the fix is in the scanner rather than in a heredoc special case.
    """
    repo = _mkrepo(tmp_path / "r", branch="trunk")
    balanced = "cat > /tmp/m <<'EOF'\ndo not stage\nEOF\ngit commit -m x"
    unbalanced = "cat > /tmp/m <<'EOF'\ndon't stage\nEOF\ngit commit -m x"
    assert gc.evaluate(balanced, "claude-code", str(repo)) is not None
    assert gc.evaluate(unbalanced, "claude-code", str(repo)) is not None
    # And at the parser, which is where the difference actually lived:
    assert any(s.strip() == "git commit -m x"
               for s in gc.split_commands(unbalanced)), gc.split_commands(unbalanced)


def test_a_heredoc_body_that_really_EXECUTES_is_still_checked(tmp_path):
    """🔴 NON-REGRESSION, and the line separating this fix from the reverted
    `_strip_message_text()` helper the module docstring forbids. That helper
    BLANKED bodies, so `bash <<EOF` stopped being checked. Nothing is blanked
    here: the body is lifted out of the surrounding quote state and then parsed
    as commands exactly as before, so a body that really runs still denies.
    """
    repo = _mkrepo(tmp_path / "r", branch="trunk")
    for cmd in ["bash <<'EOF'\ngit stash push -m wip\nEOF",
                "bash <<'EOF'\ntalosctl -n 1.2.3.4 reset\nEOF",
                "bash <<'EOF'\nit's fine\ngit stash push -m wip\nEOF"]:
        assert gc.evaluate(cmd, "claude-code", str(repo)) is not None, cmd


@pytest.mark.parametrize("cmd,expect_body", [
    ("cat <<'EOF'\nbody line\nEOF", "body line"),
    ('cat <<"EOF"\nbody line\nEOF', "body line"),
    ("cat <<EOF\nbody line\nEOF", "body line"),
    ("cat <<-EOF\n\tbody line\n\tEOF", "\tbody line"),
    ("cat <<EOF\nbody line", "body line"),            # unterminated -> runs to EOF
])
def test_scan_lifts_every_heredoc_spelling_out_as_a_body(cmd, expect_body):
    """The tag parser, per spelling. `<<-` strips leading TABS from the
    terminator only — the body text itself is preserved, because the guard reads
    it as commands and must not invent whitespace changes."""
    _segs, _subs, bodies = gc._scan(cmd)
    assert bodies == [expect_body], (cmd, bodies)


def test_a_here_STRING_is_not_a_heredoc():
    """`<<<` has no body and no terminator. Reading it as one would consume the
    rest of the line as inert text — the exact failure this change fixes, in a
    new place."""
    segs, _subs, bodies = gc._scan("cat <<< \"x\"; git stash push -m wip")
    assert bodies == []
    assert any("git stash" in s.text for s in segs)


def test_two_heredocs_on_one_line_are_consumed_in_order():
    """bash reads the bodies in the order the operators appear. Getting this
    wrong would leave the second body's text in the command stream — additive,
    but it would also mis-attribute the first."""
    segs, _subs, bodies = gc._scan("diff <<A <<B\nfirst\nA\nsecond\nB\ngit stash")
    assert bodies == ["first", "second"]
    assert any("git stash" in s.text for s in segs)


# --- 9c. THE WORKTREE POSITIVES — these must now be ALLOWED ----------------- #

def test_control_4_dash_c_into_a_worktree_on_a_topic_branch_is_allowed(tmp_path):
    """Control 4. The literal-path spelling, from a clone sitting on trunk."""
    repo, wt = _trunk_and_worktree(tmp_path)
    assert gc.evaluate(f"git -C {wt} commit -m x", "claude-code", str(repo)) is None


def test_control_5_a_cd_into_a_worktree_on_a_topic_branch_is_allowed(tmp_path):
    """Control 5. The subshell spelling — `(cd <wt> && git commit …)` — which
    was denied outright, naming a repo it never touches."""
    repo, wt = _trunk_and_worktree(tmp_path)
    assert gc.evaluate(f"(cd {wt} && git commit -m x)", "claude-code", str(repo)) is None


@pytest.mark.parametrize("template", [
    'git -C "$WT" commit -m x',
    'git -C "$WT" commit -q -F -',
    '(cd "$WT" && git commit -m x)',
    'bash -c \'cd "$WT" && git commit -m x\'',
    'git -C ${WT} commit -m x',
])
def test_a_variable_naming_the_worktree_resolves_from_the_command_text(tmp_path, template):
    """🔴 THE ORIGINAL FALSE POSITIVE. `WT=…` is set by the command itself, so
    the hook process's environment can never carry it — `expandvars` alone was
    structurally unable to answer, and the fallback then named the wrong repo.
    """
    repo, wt = _trunk_and_worktree(tmp_path)
    cmd = f"WT={wt}\n{template}"
    assert gc.check_git_commit_to_main(cmd, str(repo)) is None, cmd


def test_a_sourced_env_file_supplies_the_worktree_path(tmp_path):
    """🔴 THE EXACT INCIDENT SHAPE. The mandated recipe is `WT=/tmp/wt-$$`, whose
    value depends on the SHELL's pid and can never be read from the text — so the
    observed pattern is to write the expanded path into a scratchpad env file and
    `. ` it in later calls. Reading that file is the only way the guard can know
    which tree the commit lands in."""
    repo, wt = _trunk_and_worktree(tmp_path)
    env = tmp_path / "wt.env"
    env.write_text(f"WT={wt}\n")
    cmd = f". {env}\ngit -C \"$WT\" commit -q -F - <<'MSG'\ndocs: a message\nMSG"
    assert gc.check_git_commit_to_main(cmd, str(repo)) is None


@pytest.mark.parametrize("body", [
    "",                                  # empty file
    "# just a comment\n",
    "WT=$SOMETHING_ELSE\n",              # unresolvable value -> not a guess
    "WT=" + "x" * (70 * 1024) + "\n",    # over the size cap
])
def test_an_unusable_env_file_falls_back_rather_than_guessing(tmp_path, body):
    """Every way an env file can fail to answer must land on the cwd fallback,
    which for a clone on trunk means the deny stands."""
    repo = _mkrepo(tmp_path / "clone", branch="trunk", commit=True)
    env = tmp_path / "wt.env"
    env.write_text(body)
    assert gc.check_git_commit_to_main(
        f". {env}\ngit -C \"$WT\" commit -m x", str(repo)) is not None


def test_a_missing_env_file_does_not_raise(tmp_path):
    repo = _mkrepo(tmp_path / "clone", branch="trunk", commit=True)
    assert gc.check_git_commit_to_main(
        f". {tmp_path}/nope.env\ngit -C \"$WT\" commit -m x", str(repo)) is not None


def test_the_hot_path_reads_no_files_and_execs_no_git(tmp_path, monkeypatch):
    """🔴 THE HOT-PATH PROMISE, re-asserted for the NEW cost. Variable resolution
    can open a sourced env file, and this hook runs on EVERY Bash call — so the
    file read must sit behind the same "is there a real `git … commit` here at
    all?" gate the git subprocesses do. Pinned as a count of opens, because a
    refactor that hoists `_command_vars` above the gate is invisible otherwise.
    """
    repo = _mkrepo(tmp_path / "r", branch="trunk")
    env = tmp_path / "wt.env"
    env.write_text("WT=/tmp/whatever\n")
    opened = []
    real_open = gc.open if hasattr(gc, "open") else open
    monkeypatch.setattr("builtins.open",
                        lambda f, *a, **k: (opened.append(str(f)), real_open(f, *a, **k))[1])
    for cmd in ["ls -la", f". {env}; ls", "git status", "git add x",
                f". {env}\ngit commit --dry-run", "kubectl get pods"]:
        gc.evaluate(cmd, "claude-code", str(repo))
    assert not [o for o in opened if str(env) in o], opened
    # …and the positive control: with a REAL commit present, it IS read.
    gc.evaluate(f". {env}\ngit -C \"$WT\" commit -m x", "claude-code", str(repo))
    assert [o for o in opened if str(env) in o], "the env file was never read at all"
