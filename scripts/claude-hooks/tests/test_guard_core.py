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
    "check_heredoc_to_file",
    "check_cd_then_git",
    "check_private_key",
    "check_secret_or_ip_publish",
]


def test_claude_code_policy_is_frozen_at_the_original_six():
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


IRREVERSIBLE_EXPECTED = [
    "check_talosctl_reset",
    "check_mkfs",
    "check_dd_to_block_device",
    "check_rm_rf_critical",
    "check_git_stash",
    "check_git_clean_force",
    "check_git_reset_hard_argv",
]


def test_opencode_policy_carries_the_irreversible_checks():
    oc = [f.__name__ for f in gc.POLICIES["opencode"]]
    assert oc[len(CLAUDE_CODE_EXPECTED):] == IRREVERSIBLE_EXPECTED


@pytest.mark.parametrize("command", [
    "talosctl -n 192.168.50.94 reset",
    "mkfs.ext4 /dev/sda",
    "dd if=x of=/dev/sda",
    "rm -rf /",
    "git stash push -m wip",
    "git clean -fd",
    # 🔴 `git -C <path> reset --hard` is the ONE row here that is a GAP in
    # Claude Code today, not merely a check we chose not to enable: the frozen
    # `check_git_reset_hard` is a raw-text regex anchored on `git reset`, so the
    # worktree-first spelling RULES.md mandates slips past it. Enabling the argv
    # version for claude-code is a new deny on the operator's primary tool and
    # is theirs to approve — see the PR body.
    "git -C /tmp/x reset --hard",
])
def test_the_new_checks_do_not_leak_into_claude_code(command):
    """🔴 The blast-radius assertion, by OUTCOME rather than by list membership.

    Each of these is denied under "opencode" and must resolve to no-deny under
    "claude-code" — that is what "Claude Code's behaviour is unchanged" means
    operationally.
    """
    assert gc.evaluate(command, "opencode") is not None
    assert gc.evaluate(command, "claude-code") is None


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
    import json
    assert json.loads(_cli("talosctl reset", "claude-code").stdout)["decision"] == "allow"
    assert json.loads(_cli("talosctl reset", "opencode").stdout)["decision"] == "deny"


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
