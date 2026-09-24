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
  CITATION   the blackout measurement is restated inline and the handoff-doc
             citation names PR #1861, because the corrected text of that doc is
             not on `main`.
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
# (or a red-baseline measurement against an older revision) copies nix/system/
# into a `mktemp -d` and points this at the copy.
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

        write_exec(self.bin / "nix-instantiate", f'''
rc=$(cat {S}/instantiate_rc)
if [ "$rc" != "0" ]; then
  echo "error: syntax error, unexpected ID, at $2:12:3" >&2
  exit "$rc"
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


def test_the_blackout_citation_does_not_depend_on_an_unmerged_branch():
    """The corrected text of `claudedocs/handoff-laptop-airvpn-tunnel.md` is on
    PR #1861 and is NOT merged; as `main` stands that doc blames the laptop's
    own tailscale subnet route "with high confidence", i.e. the opposite. So
    wherever the doc is cited, the citation must name #1861 — and the
    measurement itself is restated inline so the justification stands alone."""
    for path in (APPLY, PKGS):
        src = path.read_text()
        if "handoff-laptop-airvpn-tunnel" in src:
            assert "#1861" in src, (
                f"{path} cites the handoff doc without naming PR #1861, whose "
                "unmerged branch carries the corrected text")
        assert "6.5" in src, f"{path} must restate the measured blackout inline"


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
    """Structural companion to the mktemp scratch: the hazardous shape is a
    literal `/tmp/<name>.$$`, which as root is an arbitrary-file-overwrite
    primitive for any local user who pre-creates the symlink."""
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
