"""Unit tests for scripts/disk-explore — the disk block's right-click (ncdu on
the fullest mount).

OFFLINE: ncdu/df are never touched. The pure `fullest_mount` picker is tested
against fixture parse_df output (reusing disk-detail's real parse_df), plus the
`--dry-run` CLI path via subprocess. Mirrors test_disk_detail.py.
"""
import importlib.machinery
import importlib.util
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]


def _load(name, modname):
    loader = importlib.machinery.SourceFileLoader(modname, str(SCRIPTS / name))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


de = _load("disk-explore", "disk_explore")
dd = _load("disk-detail", "disk_detail_for_explore")

# Real ext4 root (59%), a nearly-full data mount (91%), a vfat /boot (7%).
DF_SAMPLE = """\
Filesystem     Type       1B-blocks         Used      Available Mounted on
/dev/nvme0n1p2 ext4  1979120929792 1160000000000  700000000000 /
/dev/sda1      ext4  4000000000000 3640000000000  360000000000 /data
/dev/nvme0n1p1 vfat     1071624192     69206016    1002418176 /boot
tmpfs          tmpfs   16000000000       500000   15999500000 /run
"""


def test_fullest_mount_picks_highest_pct():
    disks = dd.parse_df(DF_SAMPLE)
    assert de.fullest_mount(disks) == "/data"      # 91% > 59% > 7%


def test_fullest_mount_defaults_when_empty():
    assert de.fullest_mount([]) == "/"
    assert de.fullest_mount(None) == "/"


def test_fullest_mount_custom_default_and_junk():
    # junk entries / missing target / bad pct are skipped, not crashed on
    disks = ["x", {"pct": 80}, {"target": "", "pct": 99},
             {"target": "/keep", "pct": "notanint"}]
    assert de.fullest_mount(disks, default="/fallback") == "/fallback"


def test_fullest_mount_single_real_mount():
    disks = dd.parse_df(
        "Filesystem Type 1B-blocks Used Available Mounted on\n"
        "/dev/x ext4 1000 500 500 /only\n")
    assert de.fullest_mount(disks) == "/only"


def test_dry_run_cli_prints_a_mountpoint():
    r = subprocess.run([sys.executable, str(SCRIPTS / "disk-explore"),
                        "--dry-run"], stdout=subprocess.PIPE, text=True,
                       timeout=15)
    assert r.returncode == 0
    out = r.stdout.strip()
    assert out.startswith("/")            # a real mountpoint (fullest, or the / default)
