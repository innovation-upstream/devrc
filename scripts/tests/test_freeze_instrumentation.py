"""Coverage for nix/system/apply-freeze-instrumentation.sh.

The script edits a root-owned /etc/nixos/configuration.nix, so the value of a
test here is entirely in the EDIT LOGIC: does each declaration land inside the
attrset it has to be in, and does the script refuse rather than guess when its
anchor is not what it assumed?

Placement is asserted STRUCTURALLY (by brace-matching the enclosing block), not
by grepping for the text. `NMI_WATCHDOG = 1;` present *somewhere* in the file is
worthless — the whole point of the change is that it sits inside
services.tlp.settings, because anywhere else TLP reverts it on the next
AC<->battery transition.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "nix" / "system" / "apply-freeze-instrumentation.sh"

# Mirrors the real laptop configuration.nix: a services.tlp block with a nested
# settings attrset, and a top-level boot.kernel.sysctl block.
FIXTURE = """\
{ config, pkgs, ... }:

{
  services.tlp = {
    enable = true;
    settings = {
      CPU_SCALING_GOVERNOR_ON_AC = "powersave";
      CPU_BOOST_ON_AC = 1;
      CPU_BOOST_ON_BAT = 0;
    };
  };

  boot.kernel.sysctl = {
    "fs.inotify.max_user_watches" = 1048576;
    "fs.file-max" = 300000;
  };

  boot.kernelPackages = pkgs.linuxPackages_latest;
}
"""


def run(cfg: Path, expect_ok: bool = True):
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--edit-only"],
        # Inherit the environment and override only CFG. Replacing PATH outright
        # would drop the suite's launcher-stub dir — see test_no_real_launchers.py,
        # which pins the set of files that clobber PATH.
        env={**os.environ, "CFG": str(cfg)},
        capture_output=True,
        text=True,
    )
    if expect_ok:
        assert proc.returncode == 0, f"script failed:\n{proc.stdout}\n{proc.stderr}"
    return proc


def block_range(text: str, header_re: str) -> range:
    """Line range (0-indexed, half-open) of the attrset opened on the header line."""
    import re

    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if re.match(header_re, ln)]
    assert len(starts) == 1, f"expected 1 header for {header_re!r}, got {len(starts)}"
    start = starts[0]
    depth = 0
    for i in range(start, len(lines)):
        depth += lines[i].count("{") - lines[i].count("}")
        if depth == 0 and i > start:
            return range(start, i + 1)
    raise AssertionError(f"unbalanced braces after {header_re!r}")


def line_index(text: str, needle: str) -> int:
    hits = [i for i, ln in enumerate(text.splitlines()) if needle in ln]
    assert len(hits) == 1, f"expected exactly 1 line containing {needle!r}, got {len(hits)}"
    return hits[0]


@pytest.fixture
def cfg(tmp_path: Path) -> Path:
    p = tmp_path / "configuration.nix"
    p.write_text(FIXTURE)
    return p


def test_nmi_watchdog_lands_inside_the_tlp_settings_block(cfg: Path):
    """Anywhere else and TLP reverts it on the next power event."""
    run(cfg)
    text = cfg.read_text()
    settings = block_range(text, r"^\s*settings\s*=\s*\{")
    assert line_index(text, "NMI_WATCHDOG = 1;") in settings


def test_panic_sysctls_land_inside_boot_kernel_sysctl(cfg: Path):
    run(cfg)
    text = cfg.read_text()
    sysctl = block_range(text, r"^\s*boot\.kernel\.sysctl\s*=")
    assert line_index(text, '"kernel.hardlockup_panic" = 1;') in sysctl
    assert line_index(text, '"kernel.panic" = 20;') in sysctl


def test_journald_lands_at_top_level_not_inside_the_sysctl_attrset(cfg: Path):
    """It is inserted immediately before the sysctl anchor; off-by-one would bury
    a `services.journald.extraConfig = ...` string inside boot.kernel.sysctl,
    which parses but declares a nonexistent sysctl."""
    run(cfg)
    text = cfg.read_text()
    sysctl = block_range(text, r"^\s*boot\.kernel\.sysctl\s*=")
    assert line_index(text, "services.journald.extraConfig") not in sysctl


def test_result_still_parses_as_nix(cfg: Path):
    if not shutil.which("nix-instantiate"):
        pytest.skip("nix-instantiate unavailable")
    run(cfg)
    proc = subprocess.run(
        ["nix-instantiate", "--parse", str(cfg)], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr


def test_is_idempotent(cfg: Path):
    run(cfg)
    once = cfg.read_text()
    run(cfg)
    assert cfg.read_text() == once, "second run mutated the file again"


def test_refuses_when_the_tlp_anchor_is_missing(cfg: Path):
    cfg.write_text(FIXTURE.replace("services.tlp =", "services.notlp ="))
    before = cfg.read_text()
    proc = run(cfg, expect_ok=False)
    assert proc.returncode != 0
    assert "services.tlp" in proc.stderr
    assert cfg.read_text() == before, "failed run left the config mutated"


def test_refuses_when_an_anchor_is_duplicated(cfg: Path):
    """A `count=1` edit against a pattern occurring twice lands on whichever
    match sed reaches first — not necessarily the one we pictured."""
    cfg.write_text(FIXTURE + "\n{\n  boot.kernel.sysctl = {\n    \"vm.swappiness\" = 10;\n  };\n}\n")
    before = cfg.read_text()
    proc = run(cfg, expect_ok=False)
    assert proc.returncode != 0
    assert "found 2" in proc.stderr
    assert cfg.read_text() == before
