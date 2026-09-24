"""Harness for `nix/system/apply-mosh-nebula-firewall.sh` — a script that runs as
ROOT, rewrites /etc/nixos/configuration.nix and can activate the result.

🔴 NOTHING HERE TOUCHES THE REAL SYSTEM. Every test runs the script with

    MOSH_FW_CFG  -> a fixture configuration.nix inside tmp_path
    PATH         -> a shim directory FIRST, holding `id`, `ip`,
                    `nixos-rebuild`, `nix-instantiate`

so `id -u` answers 0 without sudo, `ip` answers with a mesh address of the
test's choosing, and `nixos-rebuild` RECORDS what it was asked to do (and
fabricates plausible dry-build output) instead of doing it. There is no path
from this file to `nixos-rebuild switch`, to /nix/var/nix/profiles/system, or to
/etc/nixos.

Modelled on `test_nebula_relay_apply.py`, which is the surviving shape for this
class of script in this repo.

WHAT EACH TEST IS FOR — the audit findings the script was reworked for:
  BLOCKER A  the default run must NOT switch. /etc/nixos has no flake, so a
             bare `nixos-rebuild switch` pulls the whole root-channel delta
             (measured 2026-09-23: 444 to build / 1,420 to fetch off an
             UNEDITED config) onto a host the operator cannot physically reach.
             The run dry-builds the current AND candidate config, prints both
             plus the delta, edits, and stops. MOSH_FW_SWITCH=1 opts in.
  F1         a failure after the file is in place restores the backup, and the
             message says which of three states the run actually reached.
  F2         `nix-instantiate --parse` is a SYNTAX gate, not an eval gate: keys
             inserted at the wrong OPTION SCOPE parse fine and fail evaluation.
             Closed twice — a structural refusal of the one-line firewall block
             that produced it, and the candidate dry-build.
  F3         idempotence keys on the STRUCTURE (the attribute paths), not on a
             filename a comment can spell — $CFG already carries `# Added by
             nix/system/apply-tailscale.sh.`-style comments.
  F4         the root guard checks `id -u`, not `[ -r "$CFG" ]` (0644 on both
             hosts, so the old guard passed for an ordinary user).
  F5         the printed verify command derives THIS host's mesh address.
  F7         the config's mode survives the patch.
  F8         a parse failure prints the parser's own stderr.
  F9         the exposure claim names the nebula group set, not just the laptop.
  D1         the insert uses `programs.mosh` (for the utempter wrapper and
             mosh-server on the system PATH) with `openFirewall = false`.
  CITATION   the blackout measurement is restated inline, and the handoff-doc
             citation names PR #1866 AND its merge commit — never a PR state.
             See R3-2 below for why that distinction is the whole guard.

ROUND-2 AUDIT FINDINGS closed here:
  R2-1       MOSH_FW_SWITCH=1 over an ALREADY APPLIED config converges with a
             switch instead of falling through into a second patch pass (which
             produced duplicate attributes and could never succeed).
  R2-2       the half-configured guard's mosh half accepts the dotted
             `programs.mosh.enable` spelling its own die message recommends.
  R2-3       the citation names #1866, not the merged #1861. SUPERSEDED by R3-2
             — the citation it installed was itself false within the hour.
  R2-7       a config with no trailing newline is patched, not blamed.
  CLAIM-9    the rc-127 claim is pinned to the guard that actually delivers it
             (the preflight `command -v` loop), not to the parse gate.

ROUND-3 AUDIT FINDINGS closed here:
  R3-1       ALREADY APPLIED requires `programs.mosh.openFirewall = false`, not
             merely the presence of the two attribute paths. openFirewall
             DEFAULTS TO TRUE, so the old check accepted — and under
             MOSH_FW_SWITCH=1 ACTIVATED — a config opening 60000-61000 on every
             interface, WAN included. Closed for the whole class (dotted and
             braced, absent and explicitly true), scoped to mosh's own block so
             another module's `openFirewall = false` cannot satisfy it.
  R3-2       the citation states MERGED facts (#1866 + its merge commit
             b7a30bc3) and may not assert a PR state, which expires.
  R3-3       the converge path's printed counts are asserted, with fixture
             values distinct from every constant the script or this module
             names — the round-2 rig's defaults WERE 444/1420, so a hardcode
             mutant was invisible to it.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

# 🔴 The shims are written at RUNTIME and then EXECED, so their shebang must
# exist in BOTH tiers — `/usr/bin/env` is absent from the nix build sandbox.
from testlib.mockbin import write_exec  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

# TEST SEAM, same convention as test_nebula_relay_apply.py: a mutation battery
# copies nix/system/ into a `mktemp -d`, edits the copy, and points this at it.
#
# 🔴 A RED-BASELINE MEASUREMENT AGAINST 5f968207 DOES NOT WORK THAT WAY, AND
# RUNNING IT PRIVILEGED WOULD EDIT THE REAL SYSTEM FILE. The pre-fix script has
# NO `MOSH_FW_CFG` seam at all — it hardcodes `CFG="/etc/nixos/configuration.nix"`
# — so pointing DEVRC_TEST_MOSH_FW_DIR at a copy of it makes every test operate
# on the live /etc/nixos. Measured unprivileged: 25 red / 1 green, and 22 of the
# reds are `Permission denied` writing the REAL
# `/etc/nixos/configuration.nix.bak-mosh`, i.e. reds that say nothing about the
# change. As root the same run would have PATCHED THE LIVE CONFIG. Never run a
# baseline under sudo.
#
# To measure a real baseline: copy nix/system/ to a temp dir, hand-add the seam
# to the copy (`CFG="${MOSH_FW_CFG:-/etc/nixos/configuration.nix}"`, plus the
# root/tool/iface seams a given test needs), and say in the report that the reds
# were taken against a HAND-SEAMED copy — not against 5f968207 as committed.
# Where a test can only go red through a seam the old script lacks, that is the
# honest claim, and it is not the same as a clean red.
_SYSDIR = Path(os.environ.get("DEVRC_TEST_MOSH_FW_DIR",
                              str(REPO_ROOT / "nix" / "system"))).resolve()
APPLY = _SYSDIR / "apply-mosh-nebula-firewall.sh"
PKGS = REPO_ROOT / "nix" / "pkgs" / "default.nix"

MESH_IP = "10.42.0.30"

# The two attribute paths the script inserts, exactly.
MOSH_BLOCK = (
    "  programs.mosh = {\n"
    "    enable = true;\n"
    "    openFirewall = false;\n"
    "  };\n"
)
RANGE_BLOCK = (
    '    interfaces."nebula.mesh".allowedUDPPortRanges = [\n'
    "      { from = 60000; to = 61000; }\n"
    "    ];\n"
)

# A configuration.nix with the shape the real ones have: a top-level, two-space
# indented, multi-line `networking.firewall = {` block. Addresses are RFC1918 or
# TEST-NET — this repo is PUBLIC.
CONFIG_NIX = """\
{ config, pkgs, ... }:

{
  imports = [ ./hardware-configuration.nix ];

  # Added by nix/system/apply-tailscale.sh. This host is a PLAIN CLIENT.
  services.tailscale = {
    enable = true;
    useRoutingFeatures = "client";
  };

  networking.firewall = {
    allowedUDPPorts = [ 51820 7844 ];
    allowedTCPPorts = [ 7844 80 443 ];
  };

  system.stateVersion = "23.05";
}
"""

# F2's latent shape: the whole firewall block on ONE line. The previous script's
# regex matched it, inserted after it, and put the keys at module top level —
# which PARSES and then fails NixOS evaluation.
CONFIG_NIX_ONE_LINE = CONFIG_NIX.replace(
    "  networking.firewall = {\n"
    "    allowedUDPPorts = [ 51820 7844 ];\n"
    "    allowedTCPPorts = [ 7844 80 443 ];\n"
    "  };\n",
    "  networking.firewall = { allowedUDPPorts = [ 51820 ]; };\n",
    1,
)
assert CONFIG_NIX_ONE_LINE != CONFIG_NIX

# F3's collision: a COMMENT naming this script, with no rule anywhere. The
# previous script grepped its own filename and reported "Already configured".
CONFIG_NIX_COMMENT_COLLISION = CONFIG_NIX.replace(
    "  networking.firewall = {\n",
    "  # See nix/system/apply-mosh-nebula-firewall.sh for why this is narrow.\n"
    "  networking.firewall = {\n",
    1,
)
assert CONFIG_NIX_COMMENT_COLLISION != CONFIG_NIX


class Rig:
    """A sandboxed invocation of apply-mosh-nebula-firewall.sh."""

    def __init__(self, tmp_path: Path, config_text: str = CONFIG_NIX):
        self.root = tmp_path
        self.state = tmp_path / "state"
        self.bin = tmp_path / "bin"
        self.tmpdir = tmp_path / "tmp"
        self.etc = tmp_path / "etc"
        for d in (self.state, self.bin, self.tmpdir, self.etc):
            d.mkdir(parents=True, exist_ok=True)

        self.cfg = self.etc / "configuration.nix"
        self.cfg.write_text(config_text)
        self.original_cfg_text = config_text

        self.set("mesh_ip", MESH_IP)
        self.set("uid", "0")
        self.set("instantiate_rc", "0")
        self.set("switch_rc", "0")
        self.set("delete_backup_on_switch", "0")
        # dry-build answers, per config. Deliberately pairwise-distinct so a
        # mutant that prints the wrong column, or hardcodes one, is visible.
        self.set("before_rc", "0")
        self.set("before_built", "444")
        self.set("before_fetch", "1420")
        self.set("after_rc", "0")
        self.set("after_built", "447")
        self.set("after_fetch", "1423")
        (self.state / "rebuild.log").write_text("")
        self._write_shims()

    # ---- state helpers
    def set(self, key: str, value: str) -> None:
        (self.state / key).write_text(value)

    def log(self, name: str) -> list[str]:
        text = (self.state / f"{name}.log").read_text()
        return [ln for ln in text.splitlines() if ln.strip()]

    def backups(self) -> list[Path]:
        return sorted(self.etc.glob("configuration.nix.bak-mosh-fw-*"))

    # ---- shims
    def _write_shims(self) -> None:
        S = str(self.state)

        write_exec(self.bin / "id", f'''
if [ "$1" = "-u" ]; then cat {S}/uid; exit 0; fi
echo "id shim: unexpected args: $*" >&2
exit 64
''')

        write_exec(self.bin / "ip", f'''
# `ip -4 -o addr show <iface>` -> one line in real `-o` layout; $4 is the CIDR.
mesh=$(cat {S}/mesh_ip)
[ -n "$mesh" ] || exit 1
for a in "$@"; do iface="$a"; done
echo "3: $iface    inet $mesh/24 scope global $iface\\\\       valid_lft forever preferred_lft forever"
''')

        # 🔴 THIS SHIM MUST BE ABLE TO SAY NO FOR A REASON THE TEST DID NOT ASK
        # FOR. An unconditional `exit 0` made it blind to the exact defect the
        # converge path had: re-running the patch pass over an already-applied
        # config inserts a SECOND `programs.mosh` block and a SECOND
        # `allowedUDPPortRanges`, which the real `nix-instantiate --parse`
        # rejects with `attribute … already defined`. With a rubber-stamp shim a
        # test asserting "the converge run succeeds" passes over a config the
        # real parser would refuse — green for the wrong reason. So the shim
        # models that one rule. `instantiate_rc` still forces a syntax error on
        # demand; the duplicate check is independent of it and always armed.
        write_exec(self.bin / "nix-instantiate", f'''
rc=$(cat {S}/instantiate_rc)
if [ "$rc" != "0" ]; then
  echo "error: syntax error, unexpected ID, at $2:12:3" >&2
  exit "$rc"
fi
code=$(grep -v '^[[:space:]]*#' "$2")
n_mosh=$(printf '%s\\n' "$code" | grep -cE 'programs\\.mosh[[:space:]]*=[[:space:]]*\\{{' || true)
n_range=$(printf '%s\\n' "$code" | grep -cE 'allowedUDPPortRanges[[:space:]]*=' || true)
if [ "${{n_mosh:-0}}" -gt 1 ]; then
  echo "error: attribute 'programs.mosh.enable' at $2:20:5 already defined at $2:11:5" >&2
  exit 1
fi
if [ "${{n_range:-0}}" -gt 1 ]; then
  echo "error: attribute 'allowedUDPPortRanges' at $2:30:5 already defined at $2:21:5" >&2
  exit 1
fi
exit 0
''')

        # The dry-build half answers per CONFIG: whichever path `-I
        # nixos-config=` names is compared against $MOSH_FW_CFG, so "before" and
        # "after" are distinguishable and the delta the script prints is a real
        # subtraction of two different measurements.
        write_exec(self.bin / "nixos-rebuild", f'''
sub="$1"; shift
cfgarg=""
for a in "$@"; do
  case "$a" in nixos-config=*) cfgarg="${{a#nixos-config=}}" ;; esac
done
case "$sub" in
  dry-build)
    if [ "$cfgarg" = "$MOSH_FW_CFG" ]; then label=before; else label=after; fi
    echo "dry-build $label" >> {S}/rebuild.log
    rc=$(cat {S}/${{label}}_rc)
    if [ "$rc" != "0" ]; then
      echo "error: The option \\`interfaces' does not exist. Definition values:" >&2
      exit "$rc"
    fi
    echo "these $(cat {S}/${{label}}_built) derivations will be built:"
    echo "  /nix/store/xxxx-system.drv"
    echo "these $(cat {S}/${{label}}_fetch) paths will be fetched (512.00 MiB download, 2048.00 MiB unpacked):"
    exit 0 ;;
  switch)
    echo "switch" >> {S}/rebuild.log
    if [ "$(cat {S}/delete_backup_on_switch)" = "1" ]; then
      rm -f "$MOSH_FW_CFG".bak-mosh-fw-*
    fi
    rc=$(cat {S}/switch_rc)
    if [ "$rc" != "0" ]; then
      echo "nixos-rebuild switch: simulated failure" >&2
      exit "$rc"
    fi
    exit 0 ;;
esac
echo "unexpected: $sub $*" >> {S}/rebuild.log
exit 0
''')

    # ---- running it
    def run(self, cfg: Path | None = None, extra_env: dict | None = None,
            timeout: int = 120, apply: Path | None = None):
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}:{env.get('PATH', '')}"
        env["TMPDIR"] = str(self.tmpdir)
        env["MOSH_FW_CFG"] = str(cfg or self.cfg)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", str(apply or APPLY)], env=env, capture_output=True,
            text=True, timeout=timeout, cwd=str(self.root),
        )


@pytest.fixture()
def rig(tmp_path):
    return Rig(tmp_path)


# ------------------------------------------------------------------ BLOCKER A
def test_the_default_run_edits_and_does_NOT_switch(rig):
    """🔴 THE BLOCKER. A bare `nixos-rebuild switch` here activates the whole
    root-channel delta — a full system update — on a host the operator may be
    unable to reach. The default must dry-build both configs, write the edit,
    and stop."""
    r = rig.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert rig.log("rebuild") == ["dry-build before", "dry-build after"], r.stdout
    assert "switch" not in rig.log("rebuild")
    assert "=== EDITED, NOT SWITCHED ===" in r.stdout
    # The file IS edited — "stops without switching", not "does nothing".
    text = rig.cfg.read_text()
    assert MOSH_BLOCK in text
    assert RANGE_BLOCK in text
    # And the operator is told exactly how to complete it.
    assert "sudo nixos-rebuild switch" in r.stdout
    assert len(rig.backups()) == 1
    assert rig.backups()[0].read_text() == rig.original_cfg_text
    assert not list(rig.etc.glob("configuration.nix.new.*"))


def test_the_printed_counts_are_the_measured_ones_and_the_delta_subtracts(rig):
    """POSITIVE CONTROL for the count parser. The four numbers are pairwise
    distinct and none equals a delta, so a mutant that prints one column twice,
    hardcodes a literal, or subtracts the wrong way cannot produce this text."""
    rig.set("before_built", "444"); rig.set("before_fetch", "1420")
    rig.set("after_built", "451"); rig.set("after_fetch", "1429")
    r = rig.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "current   : 444 to build, 1420 to fetch" in r.stdout, r.stdout
    assert "candidate : 451 to build, 1429 to fetch" in r.stdout, r.stdout
    assert "delta     : 7 to build, 9 to fetch" in r.stdout, r.stdout
    # The interpretation matters as much as the numbers: the big column is
    # pre-existing channel drift, not this change.
    assert "NO EDIT AT ALL" in r.stdout


def test_zero_counts_are_reported_as_zero_not_as_a_missing_measurement(rig):
    """`nix` prints NO 'will be built' line when there is nothing to do. That
    must read as 0, not as an empty string in the arithmetic."""
    write_exec(rig.bin / "nixos-rebuild", f'''
sub="$1"; shift
for a in "$@"; do case "$a" in nixos-config=*) c="${{a#nixos-config=}}" ;; esac; done
if [ "$c" = "$MOSH_FW_CFG" ]; then l=before; else l=after; fi
echo "dry-build $l" >> {rig.state}/rebuild.log
[ "$l" = "after" ] && echo "these 2 derivations will be built:"
exit 0
''')
    r = rig.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "current   : 0 to build, 0 to fetch" in r.stdout, r.stdout
    assert "candidate : 2 to build, 0 to fetch" in r.stdout, r.stdout
    assert "delta     : 2 to build, 0 to fetch" in r.stdout, r.stdout


def test_the_switch_happens_only_behind_the_explicit_opt_in(rig):
    r = rig.run(extra_env={"MOSH_FW_SWITCH": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert rig.log("rebuild") == ["dry-build before", "dry-build after", "switch"]
    assert "=== SWITCHED ===" in r.stdout
    assert MOSH_BLOCK in rig.cfg.read_text()


# ------------------------------------------------------------------------- F1
def test_a_failed_switch_rolls_the_config_back_and_names_the_state(rig):
    """F1. The previous version moved the edit into place and then ran the
    switch bare under `set -e`: on failure the config stayed modified, the
    backup was never restored, and nothing on the failure path even named it."""
    rig.set("switch_rc", "1")
    r = rig.run(extra_env={"MOSH_FW_SWITCH": "1"})
    assert r.returncode != 0
    assert rig.log("rebuild") == ["dry-build before", "dry-build after", "switch"]
    assert rig.cfg.read_text() == rig.original_cfg_text, "the config must be restored"
    assert "ROLLED BACK" in r.stderr
    assert str(rig.backups()[0]) in r.stderr
    # The three states are mutually exclusive: a started-but-unreported switch
    # is NOT "never started" and NOT "already switched".
    #
    # ⚠ THE TWO NEGATIVE ASSERTIONS ARE WEAKER THAN THEY LOOK, and the script's
    # header now says so too. "ALREADY SWITCHED" needs PERSISTED=1 with OK=0 —
    # set on adjacent lines with nothing between them — and "NEVER STARTED"
    # needs a failure between the `mv` and the switch, where only `echo`s live.
    # Neither is reachable from realistic input, so these lines cannot fail on a
    # BEHAVIOUR change; what they still catch is a MESSAGE change that makes the
    # trap print more than one state at once.
    assert "STARTED, OUTCOME UNKNOWN" in r.stderr
    assert "NEVER STARTED" not in r.stderr
    assert "ALREADY SWITCHED" not in r.stderr


def test_a_missing_backup_fails_loudly_instead_of_silently_skipping(rig):
    rig.set("switch_rc", "1")
    rig.set("delete_backup_on_switch", "1")
    r = rig.run(extra_env={"MOSH_FW_SWITCH": "1"})
    assert r.returncode != 0
    assert "ROLLBACK FAILED — your config is still patched at" in r.stderr
    assert str(rig.cfg) in r.stderr
    assert "is GONE, so there is nothing to restore from" in r.stderr
    assert "ROLLED BACK" not in r.stderr
    # The message says the config is still patched; it must be.
    assert MOSH_BLOCK in rig.cfg.read_text()


# ------------------------------------------------------------------------- F2
def test_a_one_line_firewall_block_is_refused_before_any_write(tmp_path):
    """F2, the shape that produced it. `networking.firewall = { … };` on one
    line still matched the old anchor regex, so the keys were inserted at
    MODULE TOP LEVEL: `nix-instantiate --parse` returns 0 and NixOS evaluation
    then fails with "The option `interfaces' does not exist"."""
    r = Rig(tmp_path, config_text=CONFIG_NIX_ONE_LINE)
    res = r.run()
    assert res.returncode == 1, res.stdout + res.stderr
    assert "cannot locate exactly one top-level" in res.stderr, res.stdout + res.stderr
    assert "single-line" in res.stderr
    assert r.cfg.read_text() == r.original_cfg_text
    assert r.log("rebuild") == [], "nothing may be evaluated or switched"
    assert r.backups() == []


def test_a_candidate_that_fails_to_EVALUATE_is_refused_before_any_write(rig):
    """The second closure for F2, independent of the anchor's shape: the
    candidate is evaluated with `nixos-rebuild dry-build -I nixos-config=<temp>`
    BEFORE it is moved into place, so an option-scope error — whatever produced
    it — aborts with $CFG untouched."""
    rig.set("after_rc", "1")
    r = rig.run()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "The option `interfaces' does not exist" in r.stderr, r.stdout + r.stderr
    assert "CANDIDATE config does not evaluate" in r.stderr
    assert rig.cfg.read_text() == rig.original_cfg_text
    assert rig.backups() == []
    assert not list(rig.etc.glob("configuration.nix.new.*"))


def test_a_pre_existing_eval_failure_is_blamed_on_the_current_config(rig):
    """The other side of the split: if the CURRENT config does not dry-build,
    that is pre-existing and must not be reported as this change."""
    rig.set("before_rc", "1")
    r = rig.run()
    assert r.returncode == 1
    assert "CURRENT config does not even dry-build" in r.stderr, r.stdout + r.stderr
    assert "CANDIDATE config does not evaluate" not in r.stderr
    assert rig.cfg.read_text() == rig.original_cfg_text
    assert rig.log("rebuild") == ["dry-build before"]


# ------------------------------------------------------------------------- F3
def test_a_comment_naming_this_script_is_not_an_idempotence_marker(tmp_path):
    """F3. The marker was the script's own filename, grepped anywhere. The real
    $CFG already carries `# See nix/system/apply-tmp-churn-retention.sh.` and
    `# Added by nix/system/apply-tailscale.sh.`, so the colliding convention is
    live in the very file being edited — and a comment is not a firewall rule."""
    r = Rig(tmp_path, config_text=CONFIG_NIX_COMMENT_COLLISION)
    res = r.run()
    assert res.returncode == 0, res.stdout + res.stderr
    assert "ALREADY" not in res.stdout, res.stdout
    text = r.cfg.read_text()
    assert MOSH_BLOCK in text, "the comment suppressed a real edit"
    assert RANGE_BLOCK in text


def test_a_second_run_is_idempotent_on_the_structure(rig):
    first = rig.run()
    assert first.returncode == 0, first.stdout + first.stderr
    after_first = rig.cfg.read_text()
    (rig.state / "rebuild.log").write_text("")

    second = rig.run()
    assert second.returncode == 0, second.stdout + second.stderr
    assert "ALREADY APPLIED" in second.stdout
    assert rig.cfg.read_text() == after_first, "the second run must write nothing"
    assert rig.log("rebuild") == [], "no evaluation, no switch"
    assert len(rig.backups()) == 1, "a no-op run must not take a second backup"


def test_the_converge_run_switches_instead_of_re_patching(rig):
    """🟡 ROUND-2 FINDING 1. `MOSH_FW_SWITCH=1` over an ALREADY APPLIED config is
    the second half of the two-step workflow this script exists to create: edit
    now on a host you cannot reach, converge later when you are at its keyboard.

    It used to print "converging with a switch anyway" and then FALL THROUGH
    into the anchor check and the awk patch pass, inserting a second
    `programs.mosh` block and a second `allowedUDPPortRanges`. Measured at
    9dd99f25: two of each, TWO backups — and the real parser then rejects the
    result with `attribute 'programs.mosh.enable' already defined`, so the
    advertised converge could never succeed. It failed closed, which is why
    nothing louder happened; it was still a regression against 5f968207.

    The pins here are the ones that separate "converged" from "re-patched": the
    file is byte-identical to what the first run left, there is still exactly
    ONE backup (the converge writes nothing, so it takes none), each attribute
    appears once, and the rebuild log shows a switch with NO second
    `dry-build after` — an `after` label can only come from evaluating a
    candidate, i.e. from a patch pass that should not have run."""
    first = rig.run()
    assert first.returncode == 0, first.stdout + first.stderr
    applied = rig.cfg.read_text()
    assert len(rig.backups()) == 1
    (rig.state / "rebuild.log").write_text("")

    r = rig.run(extra_env={"MOSH_FW_SWITCH": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ALREADY APPLIED" in r.stdout
    assert "CONVERGING" in r.stdout, r.stdout
    assert rig.cfg.read_text() == applied, "the converge run must write nothing"
    assert applied.count("programs.mosh = {") == 1
    assert applied.count("allowedUDPPortRanges") == 1
    assert len(rig.backups()) == 1, "a converge run must not take a second backup"
    assert not list(rig.etc.glob("configuration.nix.new.*"))
    assert rig.log("rebuild") == ["dry-build before", "switch"], r.stdout
    assert "=== SWITCHED" in r.stdout


def test_a_failed_converge_switch_does_not_claim_a_rollback_it_did_not_do(rig):
    """The converge path arms no rollback trap, and must not pretend otherwise:
    it never touched $CFG, so there is nothing of ours to restore — while the
    SYSTEM may still have moved, which is the separate claim worth making."""
    first = rig.run()
    assert first.returncode == 0, first.stdout + first.stderr
    applied = rig.cfg.read_text()
    rig.set("switch_rc", "1")

    r = rig.run(extra_env={"MOSH_FW_SWITCH": "1"})
    assert r.returncode != 0
    assert "switch` FAILED" in r.stderr, r.stdout + r.stderr
    assert "is UNTOUCHED" in r.stderr
    assert "ROLLED BACK" not in r.stderr, "nothing was patched, so nothing rolled back"
    assert "ROLLBACK FAILED" not in r.stderr
    assert "list-generations" in r.stderr, "the system-side claim must be made"
    assert rig.cfg.read_text() == applied
    assert len(rig.backups()) == 1


def test_the_dotted_spelling_of_programs_mosh_counts_as_applied(tmp_path):
    """🟡 ROUND-2 FINDING 2. The two halves of the half-configured guard
    disagreed about spelling. The range half is `grep -qF` (a substring, so the
    dotted form matches fine); the mosh half was a regex whose RIGHT boundary
    `[^.[:alnum:]_]` EXCLUDED a following dot — so `programs.mosh.enable =
    true;`, the most idiomatic NixOS spelling and close to what this script's
    own die message hands the operator, never matched.

    Measured at 9dd99f25: a fully and correctly hand-applied dotted config was
    refused as "HALF configured", with remediation advice that produces
    `already defined`. This fixture is exactly that config — both halves
    present, both in dotted form — and the only correct verdict is ALREADY
    APPLIED with nothing written."""
    cfg = CONFIG_NIX.replace(
        "  networking.firewall = {\n",
        "  programs.mosh.enable = true;\n"
        "  programs.mosh.openFirewall = false;\n"
        "\n"
        "  networking.firewall = {\n", 1)
    cfg = cfg.replace(
        "    allowedTCPPorts = [ 7844 80 443 ];\n",
        "    allowedTCPPorts = [ 7844 80 443 ];\n"
        '    interfaces."nebula.mesh".allowedUDPPortRanges = '
        "[ { from = 60000; to = 61000; } ];\n", 1)
    assert cfg != CONFIG_NIX

    r = Rig(tmp_path, config_text=cfg)
    res = r.run()
    assert res.returncode == 0, res.stdout + res.stderr
    assert "ALREADY APPLIED" in res.stdout, res.stdout + res.stderr
    assert "HALF configured" not in res.stderr, res.stderr
    assert r.cfg.read_text() == r.original_cfg_text, "nothing may be written"
    assert r.backups() == []
    assert r.log("rebuild") == []


# --------------------------------------------------- R3-1: the openFirewall gate
# Helpers for the round-3 fixtures. `mosh_text` is dropped in above the firewall
# block at top level; `extra_fw` goes INSIDE it. The range is added by default
# because every fixture below is about a config that already carries it.
def _cfg_with(mosh_text: str, *, range_present: bool = True,
              extra_top: str = "") -> str:
    cfg = CONFIG_NIX.replace(
        "  networking.firewall = {\n",
        extra_top + mosh_text + "\n  networking.firewall = {\n", 1)
    assert cfg != CONFIG_NIX
    if range_present:
        cfg = cfg.replace(
            "    allowedTCPPorts = [ 7844 80 443 ];\n",
            "    allowedTCPPorts = [ 7844 80 443 ];\n"
            '    interfaces."nebula.mesh".allowedUDPPortRanges = '
            "[ { from = 60000; to = 61000; } ];\n", 1)
    return cfg


def _assert_refused_for_wan_exposure(res, rig_obj, why: str | None = None):
    """Every R3-1 refusal must be the SAME refusal: non-zero, naming the WAN
    exposure and the safe form, writing nothing, evaluating nothing — and NOT
    claiming ALREADY APPLIED."""
    assert res.returncode != 0, res.stdout + res.stderr
    assert "ALREADY APPLIED" not in res.stdout, res.stdout
    assert "openFirewall" in res.stderr, res.stdout + res.stderr
    assert "WAN" in res.stderr, res.stderr
    # 🟢 ROUND-4, "also worth closing": the docstring above says "the safe
    # form", and claim 1 names TWO spellings — the braced block and the dotted
    # line. This pinned only the substring both happen to share, so a refusal
    # that dropped the dotted half, or that spelled the braced half as the
    # ONE-LINE `programs.mosh = { enable = true; openFirewall = false; };`,
    # still passed. The one-line spelling matters because the round-4 scanner
    # does not read it: handing it to the operator is advice whose result is a
    # second refusal. The guard is now as wide as the sentence.
    assert "openFirewall = false;" in res.stderr, (
        "the refusal must SPELL the safe form, not just name the problem")
    assert "programs.mosh.openFirewall = false;" in res.stderr, (
        "the refusal must spell the DOTTED safe form")
    assert re.search(
        r"programs\.mosh = \{\n\s+enable = true;\n\s+openFirewall = false;",
        res.stderr), (
        "the refusal must spell the braced safe form MULTI-LINE — the one-line "
        f"spelling is not one the idempotence check reads\n{res.stderr}")
    if why is not None:
        # "you wrote it true" and "you never wrote it" are DIFFERENT mistakes
        # with different fixes in the reader's head, and the script distinguishes
        # them. Nothing asserted that, and a mutation battery found the `true`
        # arm SURVIVED every test: both spellings refuse, so only the wording
        # tells them apart.
        assert why in res.stderr, (
            f"the refusal must diagnose WHICH mistake this is; expected "
            f"{why!r}\n{res.stderr}")
    assert rig_obj.cfg.read_text() == rig_obj.original_cfg_text, "nothing may be written"
    assert rig_obj.backups() == []
    assert rig_obj.log("rebuild") == [], "nothing may be evaluated or switched"


_ABSENT = "ABSENT, and the option DEFAULTS TO TRUE"
_TRUE = "explicitly set to `true`"
# R4-F1's third refusal: the scan could not read the flag with confidence, so
# it is treated as ON. A DIFFERENT mistake from ABSENT — the flag may already
# say false, in a spelling this script declines to certify.
_UNKNOWN = "CANNOT READ with confidence"


@pytest.mark.parametrize("label,mosh_text,why", [
    # The single most likely hand-written spelling: the dotted `enable` line the
    # script's own die message recommends, with no `openFirewall` at all.
    ("dotted, openFirewall absent", "  programs.mosh.enable = true;", _ABSENT),
    # The braced form. This shape was ALREADY APPLIED at 9dd99f25 too, so the
    # class predates R2-2's widening — R2-2 made it bigger, it did not create it.
    ("braced, openFirewall absent",
     "  programs.mosh = {\n    enable = true;\n  };", _ABSENT),
    # Explicitly wrong rather than merely missing. The verdict is the same
    # refusal; only the DIAGNOSIS differs, which is why `why` is asserted.
    ("dotted, openFirewall true",
     "  programs.mosh.enable = true;\n  programs.mosh.openFirewall = true;",
     _TRUE),
    ("braced, openFirewall true",
     "  programs.mosh = {\n    enable = true;\n    openFirewall = true;\n  };",
     _TRUE),
])
def test_programs_mosh_without_openFirewall_false_is_refused_not_ALREADY_APPLIED(
        tmp_path, label, mosh_text, why):
    """🟡 ROUND-3 FINDING 1 — REGRESSION COVERAGE. `programs.mosh.openFirewall`
    DEFAULTS TO TRUE, and turning it off is this script's whole reason to write
    the module explicitly: left on it adds 60000-61000 to
    `networking.firewall.allowedUDPPortRanges`, i.e. 1001 UDP ports on EVERY
    interface, WAN included.

    The idempotence check tested only that the two attribute PATHS were present.
    So a config carrying `programs.mosh.enable = true;` plus the range — with no
    `openFirewall` line anywhere — read as `state : ALREADY APPLIED`, exit 0, and
    under MOSH_FW_SWITCH=1 went down the converge path and SWITCHED, activating
    the WAN-wide opening the script exists to avoid.

    Measured at ee6b0ce9: all four rows here exit 0 with ALREADY APPLIED. (The
    braced rows were ALREADY APPLIED at 9dd99f25 as well; R2-2's widening of the
    mosh regex added the dotted rows, which had been refused as HALF configured
    with advice that spells the safe form. So this closes a hole that got bigger,
    not one that was created — and it closes the whole class, not the spelling
    the audit happened to name.)"""
    r = Rig(tmp_path, config_text=_cfg_with(mosh_text))
    _assert_refused_for_wan_exposure(r.run(), r, why=why)


def test_a_config_reading_both_true_and_false_is_refused_not_accepted(tmp_path):
    """R3-1 regression coverage for the gate's FAIL-SAFE TIE-BREAK. RED at
    ee6b0ce9 — but ⚠ read that red honestly: at ee6b0ce9 openFirewall was not
    looked at AT ALL, so this fixture was ALREADY APPLIED for the same reason
    every other R3-1 row was, not because the tie-break was inverted. The base
    red therefore proves the CLASS, not this test's specific subject. What
    proves the subject is mutation M4d — flipping the awk END block so
    `saw_false` wins over `saw_true` fails THIS test and no other.

    It exists because the scanner is crude (it does not understand Nix strings,
    `mkIf`, or a second module file), so it CAN read both `true` and `false` for
    one host. Every such ambiguity must resolve to REFUSE — the direction where
    being wrong costs a re-run, not 1001 WAN-facing UDP ports. `saw_true` winning
    over `saw_false` in the awk END block is the line that delivers that, and a
    mutation battery found it otherwise uncovered: with the `true` arm dead both
    spellings still refused, so only the diagnosis wording and this tie-break
    could see it."""
    r = Rig(tmp_path, config_text=_cfg_with(
        "  programs.mosh = {\n    enable = true;\n    openFirewall = false;\n  };",
        extra_top="  programs.mosh.openFirewall = true;\n"))
    _assert_refused_for_wan_exposure(r.run(), r, why=_TRUE)


def test_the_converge_path_refuses_a_config_that_would_open_the_WAN(tmp_path):
    """🟡 ROUND-3 FINDING 1 — the half that actually reaches the machine. The
    refusal above is only worth something if MOSH_FW_SWITCH=1 cannot walk past
    it: at ee6b0ce9 this exact config printed ALREADY APPLIED and then ran
    `nixos-rebuild switch`."""
    r = Rig(tmp_path, config_text=_cfg_with("  programs.mosh.enable = true;"))
    res = r.run(extra_env={"MOSH_FW_SWITCH": "1"})
    _assert_refused_for_wan_exposure(res, r)
    assert "switch" not in r.log("rebuild")
    assert "SWITCHED" not in res.stdout, res.stdout


def test_another_modules_openFirewall_false_does_not_satisfy_the_mosh_gate(tmp_path):
    """🟡 ROUND-3 FINDING 1 — the NARROWING boundary, and the reason this guard
    is not a bare `grep openFirewall = false`. `openFirewall` is a common NixOS
    option name; a config that turns some OTHER service's copy off says nothing
    about mosh's. The distractor sits ABOVE the mosh block so a whole-file grep
    would find it first."""
    r = Rig(tmp_path, config_text=_cfg_with(
        "  programs.mosh.enable = true;",
        extra_top="  services.jellyfin.openFirewall = false;\n"))
    _assert_refused_for_wan_exposure(r.run(), r)


def test_openFirewall_false_inside_the_braced_mosh_block_is_ALREADY_APPLIED(tmp_path):
    """⚠ INVARIANT GUARD for the R3-1 gate's permitted direction, not regression
    coverage: GREEN at ee6b0ce9 too (the pre-fix check ignored `openFirewall`
    entirely, so of course it accepted this). It is here because R3-1 NARROWS the
    ALREADY-APPLIED verdict, and a narrowing needs a pin saying how far — the
    correctly-applied braced form, which is exactly what this script writes, must
    still be accepted with nothing written."""
    r = Rig(tmp_path, config_text=_cfg_with(MOSH_BLOCK.rstrip("\n")))
    res = r.run()
    assert res.returncode == 0, res.stdout + res.stderr
    assert "ALREADY APPLIED" in res.stdout, res.stdout + res.stderr
    assert r.cfg.read_text() == r.original_cfg_text
    assert r.backups() == []
    assert r.log("rebuild") == []


# ------------------------------------------- R4-F1: the scanner's wrong-`false`
# 🔴 ROUND-4 FINDING 1 — REGRESSION COVERAGE, five shapes, each reproduced
# end to end against the UNMUTATED script at 8e389f18.
#
# `mosh_openfirewall_state` at 8e389f18 tracked `{`/`}` depth and credited ANY
# `openFirewall = false;` seen while the depth was positive. Each fixture below
# made it print `false` for a config whose mosh openFirewall is ABSENT — i.e.
# defaulting to TRUE, 1001 UDP ports on EVERY interface, WAN included. The run
# then printed `state : ALREADY APPLIED`, printed the affirmative "and
# `programs.mosh.openFirewall` is set false", exited 0, and under
# MOSH_FW_SWITCH=1 ran `nixos-rebuild switch`.
#
# MEASURED at 8e389f18: all five rows exit 0 with ALREADY APPLIED. They are the
# reason the 🔴 comment claiming "every direction it can be wrong in errs toward
# REFUSING" was deleted rather than reworded — it was false, and a wrong
# affirmative security claim is worse than silence.
_FOREIGN_FALSE = "  services.jellyfin.openFirewall = false;\n"

_WRONG_FALSE_SHAPES = [
    # 1. `#` inside a Nix string eats the closing `};`: `code_only` truncates
    #    the line, depth never returns to 0, and a FOREIGN `openFirewall =
    #    false;` BELOW the block is credited to mosh.
    ("hash inside a string swallows the closer",
     "  programs.mosh = {\n"
     "    enable = true;\n"
     '    motd = "a#b"; };\n'
     + _FOREIGN_FALSE.rstrip("\n")),
    # 2. Same mechanism, different truncation: a `{` inside a string inflates
    #    the depth, so the real `};` leaves it at 1 and the block stays open.
    ("brace inside a string inflates the depth",
     "  programs.mosh = {\n"
     "    enable = true;\n"
     '    motd = "{";\n'
     "  };\n"
     + _FOREIGN_FALSE.rstrip("\n")),
    # 3. The DOTTED regex matching inside a string literal. Nothing to do with
    #    braces: `"` satisfied the old left boundary `[^.[:alnum:]_]`.
    ("the dotted form matched inside a string literal",
     "  programs.mosh.enable = true;\n"
     '  environment.etc."n".text = "programs.mosh.openFirewall = false;";'),
    # 4. The closer and a foreign key on ONE line. The old code ran the
    #    openFirewall test BEFORE decrementing the depth, so the foreign
    #    `false` on the far side of the `};` was still read as in-block.
    ("the closer shares a line with a foreign openFirewall",
     "  programs.mosh = {\n"
     "    enable = true;\n"
     "  }; services.jellyfin.openFirewall = false;"),
    # 5. A `let` binding that merely SPELLS the option name. In scope it is a
    #    local variable, not `programs.mosh.openFirewall` at all.
    ("a let-bound local named openFirewall",
     "  programs.mosh = {\n"
     "    enable = true;\n"
     "    withUtempter = let openFirewall = false; in true;\n"
     "  };"),
]


@pytest.mark.parametrize("label,mosh_text",
                         _WRONG_FALSE_SHAPES,
                         ids=[s[0] for s in _WRONG_FALSE_SHAPES])
def test_a_false_the_scanner_cannot_read_confidently_is_refused(
        tmp_path, label, mosh_text):
    """🔴 R4-F1 REGRESSION COVERAGE. RED at 8e389f18 (exit 0, ALREADY APPLIED,
    the affirmative banner printed) — green here. The gate's failure mode is
    opening the operator's WAN, so it must fail CLOSED on uncertainty: a `false`
    is credited only from a dotted line anchored at the start of its own line,
    or from a bare `openFirewall = <bool>;` inside a well-formed braced block
    whose closer is `};` alone. A foreign `openFirewall = false` — above the
    block, below it, or on the closer's own line — can never satisfy it."""
    r = Rig(tmp_path, config_text=_cfg_with(mosh_text))
    _assert_refused_for_wan_exposure(r.run(), r, why=_UNKNOWN)


def test_the_converge_path_refuses_an_unreadable_false_under_the_switch_opt_in(
        tmp_path):
    """🔴 R4-F1 — the half that reaches the machine, and the shape I reproduced
    end to end. At 8e389f18 this exact config exited 0, printed the affirmative
    "`programs.mosh.openFirewall` is set false" over an openFirewall that is
    ABSENT, and ran `nixos-rebuild switch`."""
    r = Rig(tmp_path, config_text=_cfg_with(_WRONG_FALSE_SHAPES[0][1]))
    res = r.run(extra_env={"MOSH_FW_SWITCH": "1"})
    _assert_refused_for_wan_exposure(res, r, why=_UNKNOWN)
    assert "switch" not in r.log("rebuild")
    assert "is set false" not in res.stdout, (
        "🔴 the affirmative security claim must not be printed for a state the "
        f"scanner could not read\n{res.stdout}")


def test_the_unreadable_diagnosis_is_distinct_from_absent_and_from_true(tmp_path):
    """🟡 R4-F1. Three refusals, three different fixes in the reader's head:
    ABSENT means "add the line", `true` means "change the value", and
    UNREADABLE means "the line may already say false — rewrite it in a spelling
    this check reads". Collapsing them would still refuse, so only the wording
    can see it; that is exactly the arm a round-3 mutation battery found
    uncovered for `true`."""
    unreadable = Rig(tmp_path / "u", config_text=_cfg_with(
        "  programs.mosh = { enable = true; openFirewall = false; };"))
    res = unreadable.run()
    assert res.returncode != 0
    assert _UNKNOWN in res.stderr, res.stderr
    assert _ABSENT not in res.stderr, res.stderr
    assert _TRUE not in res.stderr, res.stderr
    # ... and the message must hand over a way to FIND the line it cannot read.
    assert "grep -n" in res.stderr, res.stderr

    absent = Rig(tmp_path / "a",
                 config_text=_cfg_with("  programs.mosh.enable = true;"))
    res_a = absent.run()
    assert _ABSENT in res_a.stderr, res_a.stderr
    assert _UNKNOWN not in res_a.stderr, res_a.stderr


# --------------------------------------------- R4-F2: the scanner's own shapes
# 🟡 ROUND-4 FINDING 2. At 8e389f18 the brace tracker and the block-open regex
# were entirely uncovered: the only scoping test put its distractor ABOVE the
# block, the one position from which the tracker cannot leak, and five mutants
# of those lines SURVIVED the whole module 42/42. Each test below is written to
# be the one that dies for a specific loosening — see the commit message for the
# mutant-to-test map.
def test_a_foreign_openFirewall_BELOW_the_mosh_block_does_not_satisfy_the_gate(
        tmp_path):
    """🔴 R4-F2 REGRESSION COVERAGE — the position the round-3 distractor test
    could not reach. RED at 8e389f18 *as a class*: with the block left open by
    any of the five R4-F1 shapes, a `false` below it was credited. Here the
    block is WELL FORMED and closes cleanly, so the foreign line below is simply
    out of scope — which is what pins that the scan stops at the closer.

    ⚠ Honest about the base: with a well-formed block this exact fixture was
    ALSO refused at 8e389f18 (the depth returned to 0). It is a REACHABILITY
    guard for the closer, not regression coverage — what makes it earn its place
    is that deleting the `if (s == "};") { inblock = 0 }` arm cannot be seen by
    any other test in this module from the refusing side."""
    r = Rig(tmp_path, config_text=_cfg_with(
        "  programs.mosh = {\n    enable = true;\n  };\n"
        + _FOREIGN_FALSE.rstrip("\n")))
    _assert_refused_for_wan_exposure(r.run(), r, why=_ABSENT)


def test_a_block_opener_must_be_programs_mosh_itself_not_merely_mosh_shaped(
        tmp_path):
    """🔴 R4-F2. The block-open regex is the most carefully written line in the
    scanner and NOTHING distinguished it from a bare `/mosh/` at 8e389f18. Here
    a DIFFERENT attribute whose path ends in `programs.mosh` — `services.
    programs.mosh`, the same key the half-configured guard's left boundary
    excludes — opens a block containing `openFirewall = false;`. Only mosh's own
    `programs.mosh = {` may open the scan, so the verdict is ABSENT and the run
    refuses.

    Kills: dropping the `^` anchor from the opener's regex (the foreign path
    then opens the block and its `false` is credited)."""
    r = Rig(tmp_path, config_text=_cfg_with(
        "  programs.mosh.enable = true;\n"
        "  services.programs.mosh = {\n"
        "    openFirewall = false;\n"
        "  };"))
    _assert_refused_for_wan_exposure(r.run(), r, why=_ABSENT)


def test_a_dotted_false_on_a_FOREIGN_path_does_not_satisfy_the_gate(tmp_path):
    """⚠ R4-F2 MUTATION COVERAGE, labelled rather than counted as regression
    coverage. `services.programs.mosh.openFirewall = false;` is a different key
    and was ALSO refused at 8e389f18 (the old regex's left boundary excluded a
    preceding `.`), so the verdict does not move — only the diagnosis does,
    from ABSENT to UNREADABLE, which is the honest one for a line that names
    the option in a path this script does not own.

    What it earns its place for: the round-3 battery's fifth surviving mutant
    was exactly "drop the left boundary on the dotted `false` regex", and
    NOTHING in the module could see it. Its round-4 equivalent — dropping the
    `^` anchor from the dotted credit — makes this fixture credit a foreign
    key's `false` and report ALREADY APPLIED, and this test is the one that
    dies for it."""
    r = Rig(tmp_path, config_text=_cfg_with(
        "  programs.mosh.enable = true;\n"
        "  services.programs.mosh.openFirewall = false;"))
    _assert_refused_for_wan_exposure(r.run(), r, why=_UNKNOWN)


def test_a_credited_false_does_NOT_outrank_a_second_unreadable_definition(
        tmp_path):
    """🔴 R4-F2 — the fail-closed PRECEDENCE, which nothing else can see. This
    config carries BOTH a correctly-credited `openFirewall = false;` inside a
    well-formed block AND a second `programs.mosh.openFirewall` the scanner
    cannot read. The second one may be a `mkForce true` that wins at
    evaluation, so the only safe verdict is the unreadable one: `unknown`
    outranks a credited `false`, exactly as an explicit `true` outranks both.

    Kills: swapping the two `else if` arms in the awk END block. That mutant
    SURVIVED the first round-4 battery — every other fixture sets at most one
    of the two flags, so the order between them was never exercised."""
    r = Rig(tmp_path, config_text=_cfg_with(
        "  programs.mosh = {\n"
        "    enable = true;\n"
        "    openFirewall = false;\n"
        "  };\n"
        "  programs.mosh.openFirewall = lib.mkForce true;"))
    _assert_refused_for_wan_exposure(r.run(), r, why=_UNKNOWN)


def test_a_mosh_block_that_never_closes_is_unreadable_even_if_it_credits_false(
        tmp_path):
    """🔴 R4-F2 — REACHABILITY for the END block's never-closed escalation, and
    it took work to reach: in any file whose mosh block is followed by more Nix,
    the very next brace trips the in-block guard instead, so the END arm looks
    like defence-in-depth. The one shape that reaches it is a file TRUNCATED
    inside the block — an interrupted write, a half-finished hand edit — where
    the credited `false` is the last thing the scanner sees and nothing after it
    can set the flag.

    Kills: deleting `if (inblock) ambiguous = 1` from END. That mutant SURVIVED
    the first round-4 battery."""
    truncated = CONFIG_NIX.replace(
        "    allowedTCPPorts = [ 7844 80 443 ];\n",
        "    allowedTCPPorts = [ 7844 80 443 ];\n"
        '    interfaces."nebula.mesh".allowedUDPPortRanges = '
        "[ { from = 60000; to = 61000; } ];\n", 1)
    truncated = truncated.split('  system.stateVersion')[0] + (
        "  programs.mosh = {\n"
        "    enable = true;\n"
        "    openFirewall = false;\n")
    assert truncated.count("programs.mosh") == 1
    r = Rig(tmp_path, config_text=truncated)
    _assert_refused_for_wan_exposure(r.run(), r, why=_UNKNOWN)


# ------------------------------------------------- R4: the narrowing, measured
# 🟡 What the round-4 scanner REFUSES that a Nix evaluator would accept. Every
# row here is a correct config that this script now declines to certify. That is
# the right side of the trade — refusing costs a re-run, a wrong `false` costs
# 1001 WAN-facing UDP ports — but it is a cost, so it is enumerated rather than
# left to be discovered.
#
# 🔴 ONLY ONE ROW IS A BEHAVIOUR CHANGE. Measured row by row at 8e389f18: the
# one-line braced form was ACCEPTED there and is refused here. The other four
# were refused at 8e389f18 too (as ABSENT, because the old regexes did not match
# them either); what changed for those is only the DIAGNOSIS, from "you never
# wrote it" to "I cannot read what you wrote", which is the more accurate of the
# two and points at the right fix.
_NARROWED = [
    ("one-line braced block — the ONLY row whose verdict changed in round 4",
     "  programs.mosh = { enable = true; openFirewall = false; };"),
    ("lib.mkIf wrapping the block",
     "  programs.mosh = lib.mkIf true {\n"
     "    enable = true;\n"
     "    openFirewall = false;\n"
     "  };"),
    ("the opening brace on the next line",
     "  programs.mosh =\n"
     "    {\n"
     "      enable = true;\n"
     "      openFirewall = false;\n"
     "    };"),
    ("openFirewall's value on the next line",
     "  programs.mosh = {\n"
     "    enable = true;\n"
     "    openFirewall =\n"
     "      false;\n"
     "  };"),
    ("lib.mkForce false",
     "  programs.mosh = {\n"
     "    enable = true;\n"
     "    openFirewall = lib.mkForce false;\n"
     "  };"),
]


@pytest.mark.parametrize("label,mosh_text", _NARROWED,
                         ids=[s[0].split(" —")[0] for s in _NARROWED])
def test_a_correct_config_in_an_unreadable_spelling_fails_CLOSED(
        tmp_path, label, mosh_text):
    """⚠ COST LEDGER, not regression coverage. Each row is a config whose
    openFirewall really IS false and which this script refuses anyway, because
    it will not certify a spelling it cannot read. The refusal must be the
    UNREADABLE one — not ABSENT, which would tell the operator to add a line
    that is already there — and it must name the two spellings that work."""
    r = Rig(tmp_path, config_text=_cfg_with(mosh_text))
    _assert_refused_for_wan_exposure(r.run(), r, why=_UNKNOWN)


def test_the_converge_path_prints_the_measured_current_counts(tmp_path):
    """🟢 ROUND-3 FINDING 3 — COVERAGE GAP, not a defect: this is GREEN at
    ee6b0ce9 by construction, because the script already printed the right
    numbers. Nothing ASSERTED them, so a mutant replacing
    `$(count_of before built)/$(count_of before fetched)` with the literals
    `444`/`1420` survived the whole module — invisible because the rig's own
    defaults ARE 444 and 1420, and a fixture that can only ever produce a
    constant's own value cannot see a mutant that hardcodes it.

    So the fixture values here are chosen to be distinct from every constant the
    script or this module names: not 444/1420 (the rig defaults and the header's
    example), not 447/1423 or 451/1429 (the other tests' `after` values), not
    60000/61000/17/0/1. They are also distinct from each other, so a mutant
    printing one column twice cannot pass.

    These counts are what the operator reads to decide whether to let a switch
    run on a host they cannot physically reach, so a wrong one misleads at the
    exact moment the decision is irreversible."""
    r = Rig(tmp_path)
    first = r.run()
    assert first.returncode == 0, first.stdout + first.stderr
    (r.state / "rebuild.log").write_text("")
    r.set("before_built", "39")
    r.set("before_fetch", "2601")

    res = r.run(extra_env={"MOSH_FW_SWITCH": "1"})
    assert res.returncode == 0, res.stdout + res.stderr
    assert "CONVERGING" in res.stdout, res.stdout
    assert "current   : 39 to build, 2601 to fetch" in res.stdout, res.stdout
    # The converge path prints no candidate/delta columns -- there is no
    # candidate. A mutant that resurrects them would be a different bug.
    assert "delta     :" not in res.stdout, res.stdout
    assert "candidate :" not in res.stdout, res.stdout
    assert r.log("rebuild") == ["dry-build before", "switch"], res.stdout


def test_a_similar_but_different_attribute_is_not_mistaken_for_programs_mosh(tmp_path):
    """⚠ INVARIANT GUARD, not regression coverage: measured GREEN at 9dd99f25
    too — the pre-fix regex was NARROWER, so of course it rejected these. It is
    here because R2-2 widens a boundary, and a widening needs a pin saying how
    far: `programs.moshpit` and `services.programs.mosh` are different keys and
    must still NOT satisfy the mosh half, so with the range present a file
    carrying only those is HALF configured."""
    cfg = CONFIG_NIX.replace(
        "  networking.firewall = {\n",
        "  programs.moshpit.enable = true;\n"
        "  services.programs.mosh = true;\n"
        "\n"
        "  networking.firewall = {\n", 1)
    cfg = cfg.replace(
        "    allowedTCPPorts = [ 7844 80 443 ];\n",
        "    allowedTCPPorts = [ 7844 80 443 ];\n"
        '    interfaces."nebula.mesh".allowedUDPPortRanges = '
        "[ { from = 60000; to = 61000; } ];\n", 1)
    r = Rig(tmp_path, config_text=cfg)
    res = r.run()
    assert res.returncode == 1, res.stdout + res.stderr
    assert "HALF configured" in res.stderr, res.stdout + res.stderr
    assert "`programs.mosh` is not" in res.stderr, res.stderr
    assert r.cfg.read_text() == r.original_cfg_text


def test_the_nix_instantiate_shim_can_reject_a_duplicated_attribute(rig):
    """🔴 NEGATIVE CONTROL FOR THE INSTRUMENT, not for the script. The converge
    test above is only worth anything if the parse shim can say no to the shape
    the old fall-through produced. This drives the shim directly with a config
    carrying two `programs.mosh = {` blocks and watches it refuse; a shim that
    rubber-stamps everything fails here, and every test that trusts its 0 is
    then known to be trusting nothing."""
    dup = rig.etc / "dup.nix"
    dup.write_text(CONFIG_NIX.replace(
        "  networking.firewall = {\n",
        MOSH_BLOCK + "\n" + MOSH_BLOCK + "\n  networking.firewall = {\n", 1))
    env = dict(os.environ)
    env["PATH"] = f"{rig.bin}:{env.get('PATH', '')}"
    res = subprocess.run(["nix-instantiate", "--parse", str(dup)],
                         env=env, capture_output=True, text=True, timeout=60)
    assert res.returncode != 0, res.stdout + res.stderr
    assert "already defined" in res.stderr, res.stderr

    ok = rig.etc / "ok.nix"
    ok.write_text(CONFIG_NIX)
    res2 = subprocess.run(["nix-instantiate", "--parse", str(ok)],
                          env=env, capture_output=True, text=True, timeout=60)
    assert res2.returncode == 0, res2.stdout + res2.stderr


def test_a_half_configured_file_is_refused_rather_than_merged(rig):
    """Only one of the two attribute paths present: refuse and say which."""
    rig.cfg.write_text(rig.original_cfg_text.replace(
        "  networking.firewall = {\n",
        MOSH_BLOCK + "\n  networking.firewall = {\n", 1))
    r = rig.run()
    assert r.returncode == 1
    assert "HALF configured" in r.stderr, r.stdout + r.stderr
    assert "programs.mosh" in r.stderr
    assert "allowedUDPPortRanges" in r.stderr
    assert rig.log("rebuild") == []


# ------------------------------------------------------------------------- F4
def test_a_non_root_run_aborts_before_any_write(rig):
    """F4. The old guard was `[ -r "$CFG" ]`, and configuration.nix is 0644 on
    both hosts — so it passed for an ordinary user and the run died later,
    part-way, on the first write."""
    rig.set("uid", "1000")
    r = rig.run()
    assert r.returncode == 1
    assert "must run as root" in r.stderr, r.stdout + r.stderr
    assert rig.cfg.read_text() == rig.original_cfg_text
    assert rig.log("rebuild") == []
    assert rig.backups() == []


# ------------------------------------------------------------------------- F5
def test_the_verify_command_names_THIS_hosts_mesh_address(rig):
    """F5. The old heredoc was quoted, so `zach@10.42.0.30` was literal: run on
    the laptop it told the operator to mosh into the WORKBENCH — testing the
    wrong host's rule and yielding a false green."""
    rig.set("mesh_ip", "10.42.0.100")
    r = rig.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "zach@10.42.0.100" in r.stdout, r.stdout
    assert "10.42.0.30" not in r.stdout, "a hardcoded other-host address survived"


def test_a_missing_mesh_address_is_reported_rather_than_faked(rig):
    rig.set("mesh_ip", "")
    r = rig.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "has NO IPv4 address on this host" in r.stdout
    assert "zach@10.42.0" not in r.stdout


# ------------------------------------------------------------------------- F7
@pytest.mark.parametrize("mode", [0o600, 0o644])
def test_the_configs_mode_survives_the_patch(rig, mode):
    """F7. `awk > $CFG.new` then `mv` gave the result the shell's umask, so a
    0600 config came back 0644. Two modes, because one point does not show that
    the mode is PRESERVED rather than hardcoded."""
    rig.cfg.chmod(mode)
    r = rig.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert stat.S_IMODE(rig.cfg.stat().st_mode) == mode
    assert stat.S_IMODE(rig.backups()[0].stat().st_mode) == mode


# ------------------------------------------------------------------------- F8
def test_a_parse_failure_prints_the_parsers_own_output(rig):
    """F8. `>/dev/null 2>&1` made a MISSING nix-instantiate (rc 127)
    indistinguishable from a broken file, and both claimed the file does not
    parse — a claim about $CFG that is false in the first case."""
    rig.set("instantiate_rc", "1")
    r = rig.run()
    assert r.returncode == 1
    assert "syntax error, unexpected ID" in r.stderr, r.stdout + r.stderr
    assert "does not parse as Nix" in r.stderr
    assert rig.cfg.read_text() == rig.original_cfg_text
    assert rig.backups() == []
    assert rig.log("rebuild") == [], "no dry-build after a parse failure"


def test_a_missing_nix_instantiate_aborts_in_the_PREFLIGHT_tool_check(rig):
    """CLAIM-9 RESIDUE. The parse gate's comment used to say a missing
    `nix-instantiate` (rc 127) was distinguished there from a genuinely broken
    file. It is not — and cannot be: the preflight `command -v` loop lists
    `nix-instantiate` and aborts long before anything is patched, so the parse
    gate is unreachable for that case. This proves WHICH guard delivers the
    claim, by running with a PATH that has every other tool the loop checks and
    not that one.

    ⚠ REACHABILITY GUARD, not regression coverage: measured GREEN at 9dd99f25
    — the preflight loop already listed the tool there. What was wrong at that
    revision was the parse gate's COMMENT, which claimed a guarantee this guard
    provides and that gate does not. A comment is not testable, so this pins the
    guard the corrected comment now names.

    Mutation: drop `nix-instantiate` from the preflight list and this goes red
    on its own assertion — the run gets as far as the parse gate and dies
    claiming the candidate "does not parse as Nix", which is exactly the false
    claim about the file that the comment now says is impossible."""
    import shutil
    minbin = rig.root / "minbin"
    minbin.mkdir()
    # Everything the preflight loop checks, EXCEPT nix-instantiate, plus what
    # the script runs before it (mktemp for $SCRATCH) and just after it.
    for tool in ("bash", "awk", "sed", "grep", "cp", "mv", "rm", "mktemp",
                 "date", "head", "cut", "wc", "diff", "readlink", "cat",
                 "tail", "chmod", "printf"):
        src = shutil.which(tool)
        if src:
            (minbin / tool).symlink_to(src)
    for shim in ("id", "ip", "nixos-rebuild"):
        (minbin / shim).symlink_to(rig.bin / shim)
    assert not (minbin / "nix-instantiate").exists()

    r = rig.run(extra_env={"PATH": str(minbin)})
    assert r.returncode == 1, r.stdout + r.stderr
    assert "`nix-instantiate` is not on PATH" in r.stderr, r.stdout + r.stderr
    assert "does not parse as Nix" not in r.stderr, (
        "rc 127 must not be reported as a claim about the file")
    assert rig.cfg.read_text() == rig.original_cfg_text
    assert rig.backups() == []
    assert rig.log("rebuild") == []


def test_a_config_with_no_trailing_newline_is_patched_not_blamed(tmp_path):
    """🟢 ROUND-2 FINDING 7. The +17 check measured both sides with `wc -l`,
    which counts NEWLINES. A config whose last line is unterminated counts one
    short, while awk's output always ends with a newline — so the run died with
    "expected the patch to add exactly 17 lines, it added 18", a message
    blaming the patch for the shape of the input. Fail-safe but misleading, and
    latent only because both hosts' real files end with a newline."""
    r = Rig(tmp_path, config_text=CONFIG_NIX.rstrip("\n"))
    res = r.run()
    assert res.returncode == 0, res.stdout + res.stderr
    assert "it added 18" not in res.stderr, res.stderr
    assert "candidate : +17 lines" in res.stdout, res.stdout
    text = r.cfg.read_text()
    assert MOSH_BLOCK in text
    assert RANGE_BLOCK in text


# ------------------------------------------------------------------------- D1
def test_the_insert_uses_the_module_with_openFirewall_off(rig):
    """D1. `programs.mosh` brings the utempter setgid wrapper (its
    `withUtempter` option, default true — without it mosh cannot write utmp, so
    `who` misses the session) and mosh-server on the SYSTEM PATH. Its
    `openFirewall` defaults to TRUE and would add 60000-61000 to
    `networking.firewall.allowedUDPPortRanges` — 1001 UDP ports on EVERY
    interface, WAN included — so it must be explicitly false, with the
    interface-scoped range as the narrow replacement."""
    r = rig.run()
    assert r.returncode == 0, r.stdout + r.stderr
    text = rig.cfg.read_text()
    assert MOSH_BLOCK in text, text
    assert RANGE_BLOCK in text, text
    assert "allowedUDPPortRanges" in text
    # The global form must NOT appear: that is the thing being avoided.
    assert not re.search(r"^\s*allowedUDPPortRanges\s*=", text, re.M), text
    assert "openFirewall = true" not in text


def test_the_range_lands_inside_the_firewall_block_and_the_module_above_it(rig):
    """Placement, not just presence: the range is a key of
    `networking.firewall`, the module is a top-level attribute."""
    r = rig.run()
    assert r.returncode == 0, r.stdout + r.stderr
    text = rig.cfg.read_text()
    fw = text.index("  networking.firewall = {")
    assert text.index(MOSH_BLOCK) < fw, "programs.mosh must be OUTSIDE the firewall block"
    assert text.index(RANGE_BLOCK) > fw, "the range must be INSIDE it"
    # ... and inside means before the block's closing brace.
    close = text.index("\n  };", fw)
    assert text.index(RANGE_BLOCK) < close, text


# ------------------------------------------------------------- F9 + the citation
def test_the_exposure_claim_names_the_nebula_group_set_in_both_files():
    """F9. "only the admin laptop" understates it: inside the mesh the gate is
    nebula's own `firewall.inbound`, which allows any/any from every listed
    GROUP — measured 2026-09-23 on the laptop: three of them.

    ⚠ WHAT THIS CHECKS. It is a word-level guard on prose, so it is walkable by
    rewording; it is here because the alternative — pinning the whole normalised
    paragraph — makes every copy-edit a test failure for a claim that is really
    about meaning. It pins what was actually WRONG: the exposure must be stated
    in terms of nebula's inbound GROUPS, and must ENUMERATE the measured set
    rather than naming the one peer the original sentence named.

    ⚠ It deliberately does NOT assert the absence of a phrase like "only the
    admin laptop": a substring test cannot see negation, and the first draft of
    this guard failed against a header sentence reading "it is NOT only the
    admin laptop" — i.e. against the correction it was written to require.

    ⚠ MEASURED while building this guard: an EARLIER version of it — "mentions
    `inbound`, mentions `group`, names at least two of the three" — passed
    against the pre-fix script, which said "the workbench's nebula inbound rules
    allow any/any from `admin`" and used the word "workbench" for an unrelated
    reason ("run it on the WORKBENCH"). A guard that is green on the text it was
    written to reject is no guard, so the pin is the ENUMERATION: all three
    measured groups by name, in both files. `lighthouse` is the discriminator —
    the pre-fix text contains it zero times.
    """
    for path in (APPLY, PKGS):
        src = path.read_text()
        assert "inbound" in src, path
        assert "group" in src.lower(), path
        missing = [g for g in ("lighthouse", "admin", "workbench") if g not in src]
        assert not missing, (
            f"{path} does not enumerate the measured nebula group set; missing "
            f"{missing}. The exposure is every peer whose cert is in ANY group "
            "the target host's firewall.inbound allows, not the admin laptop "
            "alone.")


# A citation may not assert a PR's *state*: every one of these has a shelf life
# measured in minutes, and each has already been WRONG in this comment.
# Enumerated, not a pattern — an unlisted spelling is not covered, and the
# docstring says so.
_EXPIRING_STATE_PHRASES = (
    # ⚠ "open and unmerged" used to be listed here beside "unmerged". The check
    # is a substring test, so the longer spelling could never fire on its own —
    # every string containing it contains "unmerged" too. Removed in round 4 as
    # redundant, not as a narrowing: the set this ledger matches is unchanged.
    "unmerged",
    "not yet merged",
    "still open",
    "which is open",
    "is open,",
    "until #1866 lands",
    "nothing merged corrects",
    "contradicts the paragraph above",
    "copy on `main` contradicts",
)

# The merge commit of PR #1866, verified against `origin/main` 2026-09-24:
#   gh pr view 1866 --json state,mergedAt,mergeCommit
#     -> MERGED, 2026-09-24T03:41:52Z, b7a30bc3ae2d62ef0963e1311f33f4bef699534d
# and `git show origin/main:claudedocs/handoff-laptop-airvpn-tunnel.md` carries
# "**Leading hypothesis (high confidence, measured)** — ⚠ **SUPERSEDED, kept for
# the record.**".
_CORRECTION_MERGE_SHA = "b7a30bc3"


def test_the_blackout_citation_states_merged_facts_not_an_expiring_pr_state():
    """🟡 ROUND-3 FINDING 2 — REGRESSION COVERAGE for the citation, and a
    WIDENING of what it pins.

    History: both files first cited PR #1861 as carrying the corrected text of
    `claudedocs/handoff-laptop-airvpn-tunnel.md`. #1861 merged as `5834b4c5`
    WITHOUT it. Round 2 replaced that with #1866 and asserted it was "OPEN and
    unmerged, so nothing merged corrects it today" — and told the reader `main`
    CONTRADICTS the paragraph. That expired almost immediately: #1866 merged as
    `b7a30bc3` at 2026-09-24T03:41:52Z, and `origin/main`'s copy of the doc now
    carries the correction with the old single-cause line marked "SUPERSEDED,
    kept for the record". Re-verified here before this test was written.

    The round-2 test pinned only `"#1866" in src`, and stayed GREEN across the
    entire life of that falsehood — the number was right while everything said
    about it was wrong. So the pin moves from the NUMBER to the CLASS: a
    citation may name a PR and its MERGE COMMIT (immutable facts), and may not
    assert a PR *state* (a fact with a shelf life).

    ⚠ WHAT THIS DOES NOT DO. It is a word-level guard on prose, so it is walkable
    by rewording — `_EXPIRING_STATE_PHRASES` is an enumeration and cannot see a
    spelling nobody listed. It is not a pin on the whole normalised paragraph,
    because these two comments are edited on every round and that would make
    each copy-edit a failure for a claim that is really about meaning. What it
    CAN do is fail the exact shape that has now been wrong twice, and require
    the merge sha — which no draft written before 2026-09-24T03:41Z could have
    contained."""
    for path in (APPLY, PKGS):
        src = path.read_text()
        assert "6.5" in src, f"{path} must restate the measured blackout inline"
        if "handoff-laptop-airvpn-tunnel" not in src:
            continue
        assert "#1866" in src, (
            f"{path} cites the handoff doc without naming PR #1866, which "
            "carries the correction that makes the doc readable as this "
            "change's justification")
        assert _CORRECTION_MERGE_SHA in src, (
            f"{path} names PR #1866 without its merge commit "
            f"{_CORRECTION_MERGE_SHA}. A bare PR number leaves the reader to "
            "look up a state; the merge commit is the immutable fact and is "
            "what stops the citation expiring again.")
        low = src.lower()
        stale = [p for p in _EXPIRING_STATE_PHRASES if p in low]
        assert not stale, (
            f"{path} asserts an expiring PR/doc STATE: {stale}. #1866 is "
            f"MERGED ({_CORRECTION_MERGE_SHA}) and `main` now AGREES with this "
            "change's justification. Cite merged facts — a number and a sha — "
            "never a state.")


# --------------------------------------------------------------- housekeeping
def test_the_script_is_executable_and_passes_bash_n():
    """⚠ INVARIANT GUARD, not regression coverage: measured GREEN at 5f968207
    too (the pre-fix script was also executable and syntactically valid). It is
    here to keep it that way, and it earns its place — the rework tripped
    `bash -n` twice while being written, once on a `'` inside `${x:-…}`."""
    assert os.stat(APPLY).st_mode & stat.S_IXUSR, APPLY
    res = subprocess.run(["bash", "-n", str(APPLY)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_no_predictable_tmp_literal_survives_in_the_source():
    """⚠ HALF INVARIANT GUARD, half regression coverage — measured, and labelled
    because the two halves are not worth the same. The `> /tmp/` assertion was
    already GREEN at 5f968207: that shape never existed in the pre-fix script,
    so it pins an invariant rather than catching a bug. Only the `mktemp -d`
    half goes red there (the old script used no scratch dir at all).

    The invariant half still earns its place: the hazardous shape is a literal
    `/tmp/<name>.$$`, which as root is an arbitrary-file-overwrite primitive for
    any local user who pre-creates the symlink — worth keeping out."""
    src = APPLY.read_text()
    assert not re.search(r">\s*/tmp/", src), src
    assert "mktemp -d" in src


def test_the_scratch_and_the_temp_sibling_are_cleaned_up(rig):
    """⚠ INVARIANT GUARD on the SUCCESS path: measured GREEN at 5f968207 as
    well, because the old script's `$CFG.new` was moved rather than leaked and
    it used no scratch dir at all. It pins the new scratch/temp machinery
    against leaking on the path that used not to have any."""
    r = rig.run()
    assert r.returncode == 0
    assert list(rig.tmpdir.iterdir()) == [], list(rig.tmpdir.iterdir())
    assert not list(rig.etc.glob("configuration.nix.new.*"))


def test_a_symlinked_config_is_refused_rather_than_replaced(rig):
    """`mv` would replace the link with a regular file and orphan the real one;
    resolving it and writing through it as root is the other hazard."""
    real = rig.root / "repo" / "configuration.nix"
    real.parent.mkdir()
    real.write_text(rig.original_cfg_text)
    link = rig.root / "etc-link" / "configuration.nix"
    link.parent.mkdir()
    link.symlink_to(real)

    r = rig.run(cfg=link)
    assert r.returncode == 1
    assert link.is_symlink()
    assert real.read_text() == rig.original_cfg_text
    assert "is a symlink" in r.stderr
    assert rig.log("rebuild") == []
