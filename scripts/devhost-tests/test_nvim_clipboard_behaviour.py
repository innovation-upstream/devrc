"""BEHAVIOURAL half of the neovim OSC 52 clipboard fix. Dev-host tier only.

The structural guards live in `scripts/tests/test_nvim_clipboard_osc52.py` and
run everywhere. These three drive a REAL neovim, so they belong in
DEVHOST_TARGETS: the nix sandbox has no nvim, and a `skipif` there would be an
UNPINNED SKIP -- which is how this file came to exist. The first version put
everything in the hermetic target, and the gate correctly refused it:

    ERROR: 3 UNPINNED skip group(s) -- coverage silently collapsed

Pinning them was not available: EXPECTED_SKIPS' only conditional predicate is
`unset:VAR` and it must be the SAME predicate the test uses, but the nvim skips
key on a missing BINARY, not an env var. A flat pin would have gone red on the
dev host, where nvim exists and the tests run -- exactly the failure the
SIGNAL_PG_DSN entry documents. So they moved to the tier that HAS nvim.

🔴 These deliberately load the config the way neovim really loads it -- through
`$DEVRC_DIR`, as init.vim does -- rather than `nvim --clean -c luafile`. The
clean form works and is hermetic, but it proves only that the block behaves in
ISOLATION; it cannot see the block failing to be REACHED (a rename, a source
line dropped from init.lua). The seam is the part worth testing.
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NATIVE_LUA = REPO / ".config" / "nvim" / "lua" / "config" / "native.lua"

OSC52_COPY = re.compile(rb"\x1b\]52;c;([A-Za-z0-9+/=]*)")


def _run_nvim_offdisplay(tmp: Path, keys: str) -> bytes:
    """Drive nvim under a fresh pty with no DISPLAY; return the raw pty bytes.

    A fresh pty matters: OSC 52 sets the clipboard of whatever terminal reads
    it, so running this against the operator's real terminal would clobber
    their clipboard. `script` gives us a captured one nobody is looking at.
    """
    probe = tmp / "probe.txt"
    probe.write_text("OSC52-FIXTURE-LINE\n")
    log = tmp / "pty.bin"

    env = dict(os.environ)
    env.pop("DISPLAY", None)
    env["SSH_TTY"] = "/dev/pts/0"
    env["DEVRC_DIR"] = str(REPO)

    subprocess.run(
        ["script", "-qfc", f"nvim {probe} {keys} -c 'qa!'", str(log)],
        env=env,
        capture_output=True,
        timeout=60,
    )
    return log.read_bytes()


def test_yank_off_display_emits_osc52_with_the_yanked_text():
    """`"+y` with no DISPLAY reaches the terminal instead of erroring.

    Pre-fix this logged `clipboard: No provider` and emitted nothing.
    """
    with tempfile.TemporaryDirectory() as td:
        out = _run_nvim_offdisplay(Path(td), "-c 'normal! \"+yy'")

    assert b"No provider" not in out, (
        "neovim reported `clipboard: No provider` -- the OSC 52 fallback in "
        f"{NATIVE_LUA.relative_to(REPO)} did not engage"
    )
    m = OSC52_COPY.search(out)
    assert m, "no OSC 52 copy sequence was emitted"

    # Pin the PAYLOAD, not merely the escape: a provider that emitted a
    # well-formed but empty sequence would satisfy the regex alone.
    assert base64.b64decode(m.group(1)).decode().strip() == "OSC52-FIXTURE-LINE"


def test_paste_off_display_does_not_block_on_the_terminal():
    """`"+p` must not wait on a terminal that will never answer.

    vim.ui.clipboard.osc52.paste gives up only after 1s + 9s, and alacritty is
    `osc52 = "OnlyCopy"` so it never replies. A generous ceiling still separates
    the two outcomes by an order of magnitude -- measured 1.3s served from the
    cache versus 12.7s for the mutant that wires the reader through.
    """
    with tempfile.TemporaryDirectory() as td:
        start = time.monotonic()
        _run_nvim_offdisplay(Path(td), "-c 'normal! \"+yy' -c 'normal! G\"+p'")
        elapsed = time.monotonic() - start

    assert elapsed < 8.0, (
        f"the yank+paste round trip took {elapsed:.1f}s; the OSC 52 reader's "
        "own timeout is 10s, so paste is being served off the terminal"
    )


def test_the_provider_matches_the_environment_it_runs_in():
    """BRANCHES on DISPLAY rather than skipping -- runs in both environments.

    🔴 An earlier version carried `skipif(not DISPLAY)`. That was an UNPINNED
    SKIP in the hermetic tier; and once moved here and pinned `unset:DISPLAY`
    it became the mirror-image accounting error -- the entry APPLIES in the
    sandbox (no DISPLAY there) while this target is not collected there at all,
    so the pin could never fire and the run reported "3 of 4 pinned entries
    apply" against 2 observed skips. A skip is an accounting liability in both
    directions; a branch is not, and it asserts something in each case instead
    of nothing in one.

    🔴 Performs NO yank, deliberately, in either branch. xclip is not stubbed
    by the no-launch plugin, so yanking with a display attached would overwrite
    the operator's real system clipboard -- the class of host side effect
    scripts/tests/conftest.py exists to prevent.
    """
    env = dict(os.environ)
    env["DEVRC_DIR"] = str(REPO)
    res = subprocess.run(
        [
            "nvim", "--headless",
            "-c", 'lua io.write(vim.g.clipboard and "SET" or "UNSET")',
            "-c", "qa!",
        ],
        env=env, capture_output=True, timeout=60,
    )
    out = res.stdout + res.stderr

    if os.environ.get("DISPLAY"):
        assert b"UNSET" in out, (
            "vim.g.clipboard was overridden while DISPLAY is set; that replaces "
            "neovim's working xclip autodetection -- which supports a real "
            "paste -- with a terminal round trip"
        )
    else:
        assert b"SET" in out, (
            "no DISPLAY, yet vim.g.clipboard is unset: the OSC 52 fallback did "
            "not install, so `\"+y` has no provider at all here"
        )


def test_the_tools_these_tests_need_are_actually_present():
    """Fail loudly rather than skip: this tier is DEFINED as the one with nvim.

    A skipif here would recreate the unpinned-skip problem that moved these
    tests out of the hermetic target in the first place.
    """
    assert shutil.which("nvim"), "nvim missing on the dev-host tier"
    assert shutil.which("script"), "util-linux `script` missing on the dev-host tier"
