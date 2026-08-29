"""neovim's `"+y` must work when there is no X display (ssh, bare TTY).

MEASURED 2026-08-29, nvim 0.12.5, two independent points -- `--headless` with
the real config, and a pty with `-u NONE`:

    clipboard: No provider. Try ":checkhealth" or ":h clipboard".

Neovim >=0.10 ships an OSC 52 provider but does NOT auto-enable it here, so
`"+y` failed outright off-display and took `:Absc` (config/native.lua) with
it. tmux already emits OSC 52 for its own copy-mode yanks (.tmux.conf), so the
gap was neovim-specific.

🔴 THE PASTE HALF IS THE TRAP, and it is why a structural guard sits beside the
behavioural one. `vim.ui.clipboard.osc52.paste` queries the terminal and waits
1s, then a further 9s, before giving up -- and alacritty is deliberately
configured `osc52 = "OnlyCopy"` (nix/programs/alacritty), so it NEVER answers.
Wiring that function straight through -- exactly what nvim's own `:h clipboard`
example does -- hangs for 10s on every `"+p`. The provider therefore serves
paste from a local cache, and `test_paste_is_not_wired_to_the_blocking_reader`
fails if someone "fixes" it back to the documented shape. That test is
hermetic on purpose: the sandbox gate tier has no nvim, so the behavioural
tests below skip there and would leave the hazard completely unguarded.
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NATIVE_LUA = REPO / ".config" / "nvim" / "lua" / "config" / "native.lua"

# The escape neovim emits to hand the terminal a clipboard write: OSC 52,
# clipboard 'c', base64 payload.
OSC52_COPY = re.compile(rb"\x1b\]52;c;([A-Za-z0-9+/=]*)")

needs_nvim = pytest.mark.skipif(
    shutil.which("nvim") is None or shutil.which("script") is None,
    reason="needs a real nvim and util-linux `script` (absent in the nix sandbox tier)",
)


def _guarded_block() -> str:
    """The whole `if vim.env.DISPLAY ... end` block, or '' if absent.

    Deliberately the WHOLE block rather than the `vim.g.clipboard` table
    literal: the table wires local helpers (`copy('+')`), and the calls into
    the osc52 module live in those helpers, above the assignment. Scoping to
    the literal makes the copy-half assertion look at source that could never
    contain what it is checking for.
    """
    src = NATIVE_LUA.read_text()
    start = src.find("if vim.env.DISPLAY")
    if start == -1 or "vim.g.clipboard" not in src:
        return ""
    end = src.find("\nend", src.find("vim.g.clipboard", start))
    return src[start : end + 4] if end != -1 else src[start:]


def _run_nvim_offdisplay(tmp: Path, keys: str) -> bytes:
    """Drive nvim under a fresh pty with no DISPLAY; return the raw pty bytes.

    A fresh pty matters: OSC 52 sets the clipboard of whatever terminal reads
    it, so running this against the operator's real terminal would clobber
    their clipboard. `script` gives us a captured one that nobody is looking at.
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


@needs_nvim
def test_yank_off_display_emits_osc52_with_the_yanked_text():
    """`"+y` with no DISPLAY reaches the terminal instead of erroring."""
    with tempfile.TemporaryDirectory() as td:
        out = _run_nvim_offdisplay(Path(td), "-c 'normal! \"+yy'")

    assert b"No provider" not in out, (
        "neovim reported `clipboard: No provider` -- the OSC 52 fallback in "
        f"{NATIVE_LUA.relative_to(REPO)} did not engage"
    )
    m = OSC52_COPY.search(out)
    assert m, "no OSC 52 copy sequence was emitted"

    # Pin the PAYLOAD, not merely the escape: a provider that emits a
    # well-formed but empty sequence would satisfy the regex alone.
    assert base64.b64decode(m.group(1)).decode().strip() == "OSC52-FIXTURE-LINE"


@needs_nvim
def test_paste_off_display_does_not_block_on_the_terminal():
    """`"+p` must not wait on a terminal that will never answer.

    The blocking reader gives up only after 1s + 9s. A generous ceiling still
    separates the two outcomes by an order of magnitude.
    """
    with tempfile.TemporaryDirectory() as td:
        import time

        start = time.monotonic()
        _run_nvim_offdisplay(Path(td), "-c 'normal! \"+yy' -c 'normal! G\"+p'")
        elapsed = time.monotonic() - start

    assert elapsed < 8.0, (
        f"the yank+paste round trip took {elapsed:.1f}s; the OSC 52 reader's "
        "own timeout is 10s, so paste is being served off the terminal"
    )


@needs_nvim
@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="needs an X display")
def test_display_present_leaves_neovims_own_autodetection_alone():
    """With DISPLAY set, the block must not fire -- xclip already works.

    Deliberately asserts on `vim.g.clipboard` and performs NO yank: xclip is
    not stubbed by the no-launch plugin, so a yank here would overwrite the
    operator's real system clipboard.
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
    assert b"UNSET" in res.stdout + res.stderr, (
        "vim.g.clipboard was overridden while DISPLAY is set; that replaces "
        "neovim's working xclip autodetection with a terminal round trip"
    )


def test_paste_is_not_wired_to_the_blocking_reader():
    """HERMETIC. The hazard the behavioural tests cannot guard in the sandbox.

    `osc52.paste` is what `:h clipboard`'s own example wires in, and it is
    wrong for this host because alacritty is `OnlyCopy`. Assert the shape
    rather than the timing so the sandbox tier -- which has no nvim -- still
    catches a regression to the documented-but-hanging form.
    """
    block = _guarded_block()
    assert block, f"no DISPLAY-guarded vim.g.clipboard block found in {NATIVE_LUA}"

    assert "osc52.paste" not in block, (
        "the clipboard provider calls osc52.paste, which waits 10s for an OSC 52 "
        "reply that alacritty (osc52 = \"OnlyCopy\") never sends. Serve paste "
        "from the local cache instead."
    )
    # And the copy half must still be the real emitter -- a cache-only provider
    # would satisfy the line above while sending nothing to the terminal at all.
    assert "osc52.copy" in block, (
        "the clipboard provider no longer calls osc52.copy, so a yank reaches "
        "nothing outside neovim"
    )


def test_the_module_load_cannot_abort_the_rest_of_the_config():
    """HERMETIC. A bare `require` here would break far more than the clipboard.

    native.lua is sourced by init.lua, which goes on to source map/native.lua,
    plugins.lua and nvim_lsp.lua. An error raised at require time aborts all of
    them, so a moved or missing clipboard module would present as a broken
    editor -- on precisely the ssh sessions this block exists to serve. Failing
    soft leaves neovim's own `clipboard: No provider`, i.e. the pre-fix
    behaviour, which is a bad clipboard rather than a bad editor.
    """
    block = _guarded_block()
    assert block, f"no DISPLAY-guarded vim.g.clipboard block found in {NATIVE_LUA}"

    assert "pcall(require" in block, (
        "the osc52 module is loaded with a bare require; an error would abort "
        "native.lua and take map/native.lua, plugins.lua and nvim_lsp.lua with it"
    )
    # Pin the CONSEQUENCE, not just the call: the provider must be installed
    # only on the success branch, or a failed require still reaches osc52.copy
    # on a nil value and raises the very error pcall was added to prevent.
    #
    # Checked for presence BEFORE the ordering compare on purpose -- str.find
    # returns -1 when absent, and -1 < <any real index> is true, so dropping
    # the branch entirely would satisfy an ordering-only assertion vacuously.
    ok_branch = block.find("if ok then")
    assign = block.find("vim.g.clipboard")
    assert ok_branch != -1, (
        "no `if ok then` branch: pcall's result is never tested, so a failed "
        "require falls straight through to osc52.copy on a nil value"
    )
    assert ok_branch < assign, (
        "vim.g.clipboard is assigned outside the `if ok then` branch, so a "
        "failed require still crashes native.lua"
    )


def test_the_fallback_is_conditional_on_there_being_no_display():
    """HERMETIC. An unconditional provider would break the local X11 path."""
    src = NATIVE_LUA.read_text()
    idx = src.find("vim.g.clipboard")
    assert idx != -1, f"no vim.g.clipboard assignment in {NATIVE_LUA}"

    guard = src[:idx]
    assert "vim.env.DISPLAY" in guard, (
        "the OSC 52 provider is not guarded on DISPLAY being absent; with an X "
        "display neovim's xclip autodetection is better (it supports a real "
        "paste) and must be left alone"
    )
