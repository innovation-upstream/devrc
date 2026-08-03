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
