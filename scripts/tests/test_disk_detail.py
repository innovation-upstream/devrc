"""Unit tests for scripts/disk-detail — the rofi gauge list for the disk block.

All OFFLINE: rofi/df/xdg-open are never touched. The rofi UI isn't unit-testable,
so the LOGIC is factored into pure functions and tested here:
  - `df -B1` parse (header/pseudo-fs filtering, pct derivation, malformed rows),
  - human byte formatting, the usage gauge + its color tiers,
  - the pango-markup row builder,
and the `--dump` CLI path is exercised via subprocess. Mirrors test_media_menu.py.

    run:  pytest scripts/tests/test_disk_detail.py
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


dd = _load("disk-detail", "disk_detail")

TB = 1024 ** 4
GB = 1024 ** 3

# A representative `df -B1 --output=source,fstype,size,used,avail,target` block:
# a real ext4 root, a vfat /boot, and two pseudo mounts that MUST be filtered.
DF_SAMPLE = """\
Filesystem     Type       1B-blocks         Used      Available Mounted on
/dev/nvme0n1p2 ext4  1979120929792 1160000000000  700000000000 /
/dev/nvme0n1p1 vfat     1071624192     69206016    1002418176 /boot
tmpfs          tmpfs   16000000000       500000   15999500000 /run
efivarfs       efivarfs      131072        12288        118784 /sys/firmware/efi/efivars
"""


# --------------------------------------------------------------------------- #
# parse_df
# --------------------------------------------------------------------------- #
def test_parse_df_keeps_real_drops_pseudo_and_header():
    disks = dd.parse_df(DF_SAMPLE)
    assert [d["target"] for d in disks] == ["/", "/boot"]
    assert all(d["fstype"] not in dd.SKIP_FSTYPES for d in disks)


def test_parse_df_derives_pct_from_used_over_size():
    root = dd.parse_df(DF_SAMPLE)[0]
    assert root["pct"] == round(100 * 1160000000000 / 1979120929792)  # ~59


def test_parse_df_skips_malformed_and_zero_size():
    bad = "Filesystem Type 1B-blocks Used Available Mounted on\n" \
          "/dev/x ext4 notanumber 1 1 /x\n" \
          "/dev/y ext4 0 0 0 /y\n"
    disks = dd.parse_df(bad)
    # malformed row dropped entirely; zero-size row kept but pct guarded to 0
    assert [d["target"] for d in disks] == ["/y"]
    assert disks[0]["pct"] == 0


# --------------------------------------------------------------------------- #
# human
# --------------------------------------------------------------------------- #
def test_human_scales_units():
    assert dd.human(500) == "500B"
    assert dd.human(1536) == "1.5K"
    assert dd.human(2 * GB) == "2G"
    assert dd.human(1.8 * TB).endswith("T")


def test_human_bad_input():
    assert dd.human(None) == "?"
    assert dd.human("x") == "?"


# --------------------------------------------------------------------------- #
# gauge + color tiers
# --------------------------------------------------------------------------- #
def test_gauge_width_and_fill():
    g = dd.gauge(50, width=10)
    assert len(g) == 10
    assert g.count("█") == 5 and g.count("░") == 5


def test_gauge_clamps_out_of_range():
    assert dd.gauge(-5, width=8) == "░" * 8
    assert dd.gauge(150, width=8) == "█" * 8


def test_gauge_color_tiers():
    assert dd.gauge_color(10) == dd.GREEN
    assert dd.gauge_color(69) == dd.GREEN
    assert dd.gauge_color(70) == dd.YELLOW
    assert dd.gauge_color(89) == dd.YELLOW
    assert dd.gauge_color(90) == dd.RED
    assert dd.gauge_color(99) == dd.RED


# --------------------------------------------------------------------------- #
# format_row / build_rows
# --------------------------------------------------------------------------- #
def test_format_row_has_markup_and_pct():
    d = {"target": "/", "pct": 59, "avail": 700 * GB, "size": 1.8 * TB}
    row = dd.format_row(d)
    assert "<span" in row and "59%" in row
    assert dd.GREEN in row  # 59% -> green tier


def test_format_row_truncates_long_mountpoint():
    d = {"target": "/home/zach/workspace/very/deep/nested/mount",
         "pct": 5, "avail": GB, "size": 2 * GB}
    row = dd.format_row(d)
    assert "…" in row


def test_build_rows_pairs_markup_with_mountpoint():
    disks = dd.parse_df(DF_SAMPLE)
    rows = dd.build_rows(disks)
    assert [t for _, t in rows] == ["/", "/boot"]
    assert all("<span" in m for m, _ in rows)


# --------------------------------------------------------------------------- #
# --dump CLI (offline, no rofi)
# --------------------------------------------------------------------------- #
def test_dump_cli_runs_without_rofi():
    r = subprocess.run([sys.executable, str(SCRIPTS / "disk-detail"), "--dump"],
                       stdout=subprocess.PIPE, text=True, timeout=15)
    assert r.returncode == 0
    # every emitted line is "<mountpoint>\t<markup>"; real disks carry a gauge span
    for line in r.stdout.splitlines():
        assert "\t" in line
