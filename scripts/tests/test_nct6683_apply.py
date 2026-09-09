"""Tests for `nix/system/apply-nct6683-module.sh` — the staged sudo script.

🔴 WHY THIS FILE EXISTS. The script EDITS `/etc/nixos/configuration.nix` and
runs `nixos-rebuild switch`. Its sibling in the same directory
(`apply-nebula-relay.sh`) carries 20 tests and a host guard; this one shipped
with neither, which an audit called out: the edit was correct under 19
hand-probed variants, but nothing repeatable held it that way.

HERMETIC. Every test drives the script against a FIXTURE config via `NCT_CFG`,
with a shim directory first on PATH holding `nixos-rebuild` (which records what
it was asked to do instead of doing it). There is no path from this file to a
real `nixos-rebuild`, to `/etc/nixos`, or to `/etc/modules-load.d`.

🔴 Setting `NCT_CFG` also disables the host guard by design — a fixture run is
not a claim about this machine's hardware. The guard itself is exercised
separately, by NOT setting `NCT_CFG`.

`write_exec` owns the shim shebang: the sandbox tier has no `/usr/bin/env`, and
a repo ledger forbids a test writing its own.

    run:  pytest scripts/tests/test_nct6683_apply.py
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "nix" / "system" / "apply-nct6683-module.sh"
sys.path.insert(0, str(REPO / "scripts"))
from testlib.mockbin import write_exec  # noqa: E402

PLAIN = '  boot.kernelModules = [ "i2c-dev" "vfio_pci" "vfio" "vfio_iommu_type1" ];\n'


@pytest.fixture
def rig(tmp_path):
    """A fixture config, a shim PATH, and a runner. Returns a small namespace."""
    class Rig:
        pass

    r = Rig()
    r.dir = tmp_path
    r.cfg = tmp_path / "configuration.nix"
    r.loadconf = tmp_path / "nixos.conf"
    r.bin = tmp_path / "bin"
    r.bin.mkdir()
    r.calls = tmp_path / "rebuild-calls"

    # Records the invocation instead of performing it. Writing LOADCONF here is
    # what a real activation does, so the script's verify step has something
    # true to read — the stub stands in for activation, not for the check.
    write_exec(r.bin / "nixos-rebuild", f'''
echo "nixos-rebuild $*" >> "{r.calls}"
printf 'atkbd\\nnct6683\\n' > "{r.loadconf}"
''')

    # The host guard runs on EVERY invocation (it is no longer skipped by
    # NCT_CFG — that coupling is what made it unobservable). So the default rig
    # presents a hwmon root that HAS the chip; the guard's own tests override
    # NCT_HWMON_ROOT to exercise the refusing branch.
    r.hwmon = tmp_path / "hwmon-ok"
    (r.hwmon / "hwmon0").mkdir(parents=True)
    (r.hwmon / "hwmon0" / "name").write_text("nct6687\n")

    def run(cfg_text=PLAIN, env=None, cfg=True, **kw):
        if cfg_text is not None:
            r.cfg.write_text(cfg_text)
        e = dict(os.environ)
        e["PATH"] = f"{r.bin}:{e.get('PATH', '')}"
        e["NCT_LOADCONF"] = str(r.loadconf)
        e["NCT_HWMON_ROOT"] = str(r.hwmon)
        if cfg:
            e["NCT_CFG"] = str(r.cfg)
        e.update(env or {})
        return subprocess.run(["bash", str(SCRIPT)], capture_output=True,
                              text=True, env=e, timeout=60, **kw)

    r.run = run
    return r


# --------------------------------------------------------------------------
# the edit
# --------------------------------------------------------------------------
def test_it_inserts_the_module_and_keeps_the_nix_valid(rig):
    p = rig.run()
    assert p.returncode == 0, p.stderr
    line = [l for l in rig.cfg.read_text().splitlines()
            if "boot.kernelModules" in l][0]
    assert '"nct6683"' in line
    # every original entry survives, and the list still closes
    for orig in ("i2c-dev", "vfio_pci", "vfio", "vfio_iommu_type1"):
        assert f'"{orig}"' in line
    assert line.rstrip().endswith("];")


def test_it_is_idempotent(rig):
    first = rig.run()
    assert first.returncode == 0
    after_one = rig.cfg.read_text()
    second = rig.run(cfg_text=None)          # re-run against the EDITED file
    assert second.returncode == 0
    assert "Already present" in second.stdout
    assert rig.cfg.read_text() == after_one, "second run must not edit again"


def test_a_substring_match_does_not_count_as_present(rig):
    """`"nct6683-x"` contains the module name but is a DIFFERENT module. Reading
    it as present would silently skip the edit and leave the driver unpersisted
    while reporting success."""
    p = rig.run('  boot.kernelModules = [ "nct6683-x" ];\n')
    assert p.returncode == 0, p.stderr
    line = [l for l in rig.cfg.read_text().splitlines()
            if "boot.kernelModules" in l][0]
    assert '"nct6683"' in line and '"nct6683-x"' in line


def test_it_makes_a_backup_before_editing(rig):
    rig.run()
    baks = list(rig.dir.glob("configuration.nix.bak-nct6683-*"))
    assert len(baks) == 1, baks
    assert "nct6683" not in baks[0].read_text(), "backup must be the PRE-edit copy"


# --------------------------------------------------------------------------
# the refusals — each must leave the file untouched
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name,text", [
    ("multi-line list",
     '  boot.kernelModules = [\n    "i2c-dev"\n    "vfio"\n  ];\n'),
    ("two assignments",
     PLAIN + '  boot.kernelModules = [ "extra" ];\n'),
    ("no assignment at all", '  boot.kernelPackages = pkgs.linuxPackages;\n'),
    ("only a commented-out one",
     '  # boot.kernelModules = [ "i2c-dev" ];\n'),
])
def test_it_REFUSES_an_ambiguous_shape_and_changes_nothing(rig, name, text):
    p = rig.run(text)
    assert p.returncode == 1, (name, p.stdout, p.stderr)
    assert "expected exactly 1" in p.stderr, (name, p.stderr)
    assert rig.cfg.read_text() == text, f"{name}: the file was modified anyway"
    assert not list(rig.dir.glob("*.bak-nct6683-*")), f"{name}: made a backup"


def test_a_commented_assignment_does_not_shield_a_real_one(rig):
    """Positive control for the refusal above: with a commented line AND a real
    one, the real one is still edited. Without this, a regex that matched
    nothing would pass the refusal tests by refusing everything."""
    p = rig.run('  # boot.kernelModules = [ "old" ];\n' + PLAIN)
    assert p.returncode == 0, p.stderr
    assert '"nct6683"' in rig.cfg.read_text()


# --------------------------------------------------------------------------
# dry run
# --------------------------------------------------------------------------
def test_dry_run_writes_nothing_and_never_rebuilds(rig):
    p = rig.run(env={"NCT_DRY_RUN": "1"})
    assert p.returncode == 0, p.stderr
    assert rig.cfg.read_text() == PLAIN, "dry run edited the config"
    assert not rig.calls.exists(), "dry run invoked nixos-rebuild"
    assert not list(rig.dir.glob("*.bak-nct6683-*"))


@pytest.mark.parametrize("val", ["0", "false", "FALSE", "no", "NO", ""])
def test_a_FALSY_dry_run_value_means_OFF(rig, val):
    """🔴 `[ -n "$DRY" ]` alone made `NCT_DRY_RUN=0` turn dry-run ON — the exact
    inversion of what anyone typing that means, and it fails SAFE, so it would
    read as "the script did nothing again" rather than as a bug."""
    p = rig.run(env={"NCT_DRY_RUN": val})
    assert p.returncode == 0, p.stderr
    assert '"nct6683"' in rig.cfg.read_text(), f"{val!r} was treated as dry"
    assert rig.calls.exists(), f"{val!r} skipped the rebuild"


@pytest.mark.parametrize("val", ["1", "yes", "true", "anything"])
def test_a_TRUTHY_dry_run_value_means_ON(rig, val):
    p = rig.run(env={"NCT_DRY_RUN": val})
    assert p.returncode == 0, p.stderr
    assert rig.cfg.read_text() == PLAIN, f"{val!r} was not treated as dry"


# --------------------------------------------------------------------------
# the rebuild + the verification
# --------------------------------------------------------------------------
def test_it_rebuilds_and_verifies_against_MODULES_LOAD_D_not_lsmod(rig):
    """🔴 The whole point of the verify step. `lsmod` says yes whether or not
    the change landed (the module is loaded by hand); modules-load.d is what
    systemd replays at boot, so its content IS the persistence claim."""
    p = rig.run()
    assert p.returncode == 0, p.stderr
    assert rig.calls.read_text().strip() == "nixos-rebuild switch"
    assert "OK: nct6683 is in" in p.stdout

    # 🔴 Assert on EXECUTABLE lines, not on the file text. The first version of
    # this was `"lsmod" not in SCRIPT.read_text()` and it failed on the COMMENT
    # that explains why lsmod is not used — a guard a comment can decide is a
    # guard, in either direction. Strip comment-only lines and check the code.
    code = [l for l in SCRIPT.read_text().splitlines()
            if not l.lstrip().startswith("#")]
    assert not [l for l in code if "lsmod" in l], \
        "the verification must not consult lsmod:\n" + \
        "\n".join(l for l in code if "lsmod" in l)
    # Positive control: the stripper kept real code, so the emptiness above is a
    # reading and not an artefact of stripping everything.
    assert [l for l in code if "LOADCONF" in l], \
        "comment-stripping removed the code too — the assertion above is vacuous"


def test_it_FAILS_when_the_rebuild_did_not_persist_the_module(rig):
    """The activation ran but modules-load.d does not carry the module: the
    script must NOT report success. Without this the operator is told it is
    persisted when the next boot will not load it."""
    write_exec(rig.bin / "nixos-rebuild", f'''
echo "nixos-rebuild $*" >> "{rig.calls}"
printf 'atkbd\\n' > "{rig.loadconf}"
''')
    p = rig.run()
    assert p.returncode == 1, p.stdout
    assert "NOT in" in p.stderr
    assert "Do NOT" in p.stderr and "applied" in p.stderr


def test_a_substring_line_in_modules_load_d_does_not_satisfy_the_check(rig):
    """`grep -qx` anchors the whole line. A `nct6683_extra` entry must not read
    as the module being present."""
    write_exec(rig.bin / "nixos-rebuild", f'''
echo "nixos-rebuild $*" >> "{rig.calls}"
printf 'nct6683_extra\\n' > "{rig.loadconf}"
''')
    p = rig.run()
    assert p.returncode == 1, p.stdout


# --------------------------------------------------------------------------
# the host guard (the ONE group that does not set NCT_CFG)
# --------------------------------------------------------------------------
def _hwmon(tmp_path, name, chip):
    """Build a fake hwmon root holding one device called `chip`."""
    root = tmp_path / name
    (root / "hwmon0").mkdir(parents=True)
    (root / "hwmon0" / "name").write_text((chip or "nvme") + "\n")
    return root


def test_the_host_guard_REFUSES_a_machine_without_the_chip(rig, tmp_path):
    """🔴 DETERMINISTIC ON ANY HOST — and the first version of this was not.

    It branched on whether the BUILDER had the chip, so on the workbench it
    asserted `returncode == 0`, which is equally true when the guard is
    DELETED: a mutation removing the whole guard left the suite 26/26 green.
    `NCT_HWMON_ROOT` makes both branches reachable everywhere, which is the
    only reason this assertion means anything.
    """
    p = rig.run(env={"NCT_HWMON_ROOT": str(_hwmon(tmp_path, "no-chip", "nvme"))})
    assert p.returncode == 4, (p.returncode, p.stdout, p.stderr)
    assert "no nct6687/nct6683 hwmon device" in p.stderr
    assert "NCT_SKIP_HOST_CHECK=1" in p.stderr
    assert rig.cfg.read_text() == PLAIN, "refused but edited anyway"


def test_an_ABSENT_hwmon_tree_is_also_refused(rig, tmp_path):
    """The nix sandbox has no /sys/class/hwmon at all — the glob matches
    nothing, which must refuse rather than crash or pass."""
    p = rig.run(env={"NCT_HWMON_ROOT": str(tmp_path / "does-not-exist")})
    assert p.returncode == 4, (p.returncode, p.stderr)
    assert rig.cfg.read_text() == PLAIN


@pytest.mark.parametrize("chip", ["nct6687", "nct6683"])
def test_the_host_guard_PASSES_when_the_chip_is_present(rig, tmp_path, chip):
    """🔴 The positive control. Without it, a guard that refused
    unconditionally would satisfy both tests above while being permanently
    broken — and the operator's fix would be to delete it."""
    p = rig.run(env={"NCT_HWMON_ROOT": str(_hwmon(tmp_path, "chip-" + chip, chip))})
    assert p.returncode == 0, p.stderr
    assert '"nct6683"' in rig.cfg.read_text()


def test_a_SUBSTRING_chip_name_does_not_satisfy_the_host_guard(rig, tmp_path):
    """`grep -qxs` anchors the whole line: a device called `nct6687_other` is
    not this chip."""
    p = rig.run(env={"NCT_HWMON_ROOT": str(
        _hwmon(tmp_path, "substr", "nct6687_other"))})
    assert p.returncode == 4, (p.returncode, p.stderr)


def test_the_host_guard_can_be_overridden_deliberately(rig, tmp_path):
    """A new machine with the same chip, or an operator who knows better."""
    p = rig.run(env={"NCT_SKIP_HOST_CHECK": "1",
                     "NCT_HWMON_ROOT": str(_hwmon(tmp_path, "override", "nvme"))})
    assert p.returncode == 0, p.stderr
    assert '"nct6683"' in rig.cfg.read_text()


def test_an_unreadable_config_is_reported_not_crashed(rig):
    p = rig.run(cfg_text=None, env={"NCT_CFG": str(rig.dir / "nope.nix")})
    assert p.returncode == 2
    assert "cannot read" in p.stderr
