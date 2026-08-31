"""Unit tests for scripts/memory-detail — the float-terminal RAM consumers view.

All OFFLINE: alacritty/ps/proc are never touched. The pure logic is tested:
  - /proc/meminfo parse (key extraction, kB→bytes, missing keys),
  - `ps aux` parse (pid/rss/pct_mem, sorting, malformed rows),
  - summary line formatting,
  - table formatting (column alignment, truncation, empty input),
and the `--dump` CLI path is exercised via subprocess.

    run:  pytest scripts/tests/test_memory_detail.py
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


md = _load("memory-detail", "memory_detail")

GB = 1024 ** 3
MB = 1024 ** 2

# Representative /proc/meminfo (trimmed to the keys the script reads).
MEMINFO_SAMPLE = """\
MemTotal:       129426460 kB
MemFree:         8186488 kB
MemAvailable:   18312412 kB
Buffers:         5578052 kB
Cached:         45070828 kB
SwapTotal:      90598500 kB
SwapFree:        7823460 kB
"""

# Representative `ps aux` output (header + a few processes).
PS_SAMPLE = """\
USER         PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
root           1  0.1  0.0  31348 14412 ?        Ss   Aug04  50:46 /run/current-system/systemd/lib/systemd/systemd
zach       1234  2.3 12.5 8901234 16384000 ?    Sl   Aug04 120:00 /nix/store/abc-chrome/chrome --type=gpu-process
zach       5678  0.5  3.2 4500000 4194304 ?     Sl   Aug04  30:00 /nix/store/def-firefox/firefox --contentproc
nixbld     9999  0.0  0.0      0     0 ?        Z    Aug04   0:00 [kworker/u32:0]
"""


# --------------------------------------------------------------------------- #
# parse_meminfo
# --------------------------------------------------------------------------- #
def test_parse_meminfo_extracts_keys():
    info = md.parse_meminfo(MEMINFO_SAMPLE)
    assert "MemTotal" in info
    assert "MemFree" in info
    assert "SwapTotal" in info


def test_parse_meminfo_converts_kb_to_bytes():
    info = md.parse_meminfo(MEMINFO_SAMPLE)
    # MemTotal: 129426460 kB → 129426460 * 1024
    assert info["MemTotal"] == 129426460 * 1024


def test_parse_meminfo_ignores_non_numeric():
    text = "MemTotal:       notanumber kB\n"
    info = md.parse_meminfo(text)
    assert "MemTotal" not in info


def test_parse_meminfo_empty():
    assert md.parse_meminfo("") == {}


# --------------------------------------------------------------------------- #
# parse_ps
# --------------------------------------------------------------------------- #
def test_parse_ps_extracts_procs():
    procs = md.parse_ps(PS_SAMPLE)
    # header + 4 data lines, but the zombie [kworker] has RSS=0 and is still valid
    assert len(procs) == 4


def test_parse_ps_sorts_by_rss_desc():
    procs = md.parse_ps(PS_SAMPLE)
    rss_values = [p["rss"] for p in procs]
    assert rss_values == sorted(rss_values, reverse=True)


def test_parse_ps_converts_rss_kb_to_bytes():
    procs = md.parse_ps(PS_SAMPLE)
    chrome = next(p for p in procs if "chrome" in p["command"])
    assert chrome["rss"] == 16384000 * 1024  # 16384000 KB → bytes


def test_parse_ps_skips_header():
    procs = md.parse_ps(PS_SAMPLE)
    assert all(p["pid"] != 0 for p in procs)


def test_parse_ps_skips_malformed():
    bad = "USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND\n" \
          "root xyz 0.0 0.0 0 0 ? Ss Aug04 0:00 cmd\n"
    assert md.parse_ps(bad) == []


def test_parse_ps_empty():
    assert md.parse_ps("") == []


# --------------------------------------------------------------------------- #
# human
# --------------------------------------------------------------------------- #
def test_human_scales_units():
    assert md.human(500) == "500B"
    assert md.human(1536) == "1.5K"
    assert md.human(2 * GB) == "2G"
    assert md.human(1.8 * (1024**4)).endswith("T")


def test_human_bad_input():
    assert md.human(None) == "?"
    assert md.human("x") == "?"


# --------------------------------------------------------------------------- #
# format_summary
# --------------------------------------------------------------------------- #
def test_format_summary_shows_ram_and_swap():
    info = md.parse_meminfo(MEMINFO_SAMPLE)
    s = md.format_summary(info)
    assert "RAM:" in s
    assert "Swap:" in s
    assert "%" in s


def test_format_summary_ram_used_is_total_minus_available():
    info = md.parse_meminfo(MEMINFO_SAMPLE)
    s = md.format_summary(info)
    # MemTotal - MemAvailable = used
    total = info["MemTotal"]
    avail = info["MemAvailable"]
    used = total - avail
    assert md.human(used) in s


def test_format_summary_missing_keys():
    s = md.format_summary({})
    assert "RAM: 0B / 0B (0%)" in s


# --------------------------------------------------------------------------- #
# format_table
# --------------------------------------------------------------------------- #
def test_format_table_headers():
    procs = md.parse_ps(PS_SAMPLE)
    table = md.format_table(procs)
    assert "PID" in table
    assert "USER" in table
    assert "RSS" in table
    assert "%MEM" in table
    assert "COMMAND" in table


def test_format_table_truncates_long_commands():
    procs = [{"pid": 1, "user": "root", "rss": GB, "pct_mem": 1.0,
              "command": "a" * 100}]
    table = md.format_table(procs)
    assert "..." in table
    assert "a" * 100 not in table


def test_format_table_empty():
    assert "(no processes)" in md.format_table([])


def test_format_table_respects_n():
    procs = md.parse_ps(PS_SAMPLE)
    table = md.format_table(procs, n=2)
    # only 2 data rows (plus header + separator)
    lines = [l for l in table.splitlines() if l.strip() and not l.strip().startswith("-")]
    assert len(lines) == 3  # header + 2 data rows


# --------------------------------------------------------------------------- #
# --dump CLI (offline, no terminal)
# --------------------------------------------------------------------------- #
def test_dump_cli_prints_output():
    r = subprocess.run([sys.executable, str(SCRIPTS / "memory-detail"), "--dump"],
                       stdout=subprocess.PIPE, text=True, timeout=15)
    assert r.returncode == 0
    assert "RAM:" in r.stdout
    assert "Swap:" in r.stdout
