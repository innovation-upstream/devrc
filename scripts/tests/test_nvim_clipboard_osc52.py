"""STRUCTURAL guards on the neovim OSC 52 clipboard fallback. Fully hermetic.

Background, measured 2026-08-29 on nvim 0.12.5 at two independent points --
`--headless` with the real config, and a pty with `-u NONE` -- with no DISPLAY
(any ssh session, any bare TTY):

    clipboard: No provider. Try ":checkhealth" or ":h clipboard".

Neovim >=0.10 ships an OSC 52 provider but does NOT auto-enable it here, so
`"+y` failed outright off-display and took `:Absc` (config/native.lua, which
does setreg('+')) with it. tmux already emits OSC 52 for its own copy-mode
yanks (.tmux.conf), so the gap was neovim-specific.

🔴 WHY STRUCTURAL GUARDS EXIST BESIDE THE BEHAVIOURAL ONES. The behavioural
tests drive a real neovim and therefore live in the dev-host tier
(scripts/devhost-tests/test_nvim_clipboard_behaviour.py) -- the nix sandbox
gate has no nvim. Everything below runs in BOTH tiers by reading source text,
so the two hazards most likely to be reintroduced are guarded even where nvim
is absent:

  1. paste wired to `osc52.paste`, which waits 1s + 9s for a reply alacritty
     (`osc52 = "OnlyCopy"`) never sends -- a 10s hang on every `"+p`. This is
     what `:h clipboard`'s OWN example does, so it is the likely "fix".
  2. a bare `require`, which would abort native.lua mid-load and take
     map/native.lua, plugins.lua and nvim_lsp.lua with it.

Neither can be caught by a behavioural test in the sandbox, and hazard 1 costs
only latency -- nothing fails, so nobody notices.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NATIVE_LUA = REPO / ".config" / "nvim" / "lua" / "config" / "native.lua"


def _guarded_block() -> str:
    """The whole `if vim.env.DISPLAY ... end` block, or '' if absent.

    Deliberately the WHOLE block rather than the `vim.g.clipboard` table
    literal: the table wires local helpers (`copy('+')`), and the calls into
    the osc52 module live in those helpers, above the assignment. Scoping to
    the literal makes the copy-half assertion look at source that could never
    contain what it is checking for -- which is exactly how it failed first.
    """
    src = NATIVE_LUA.read_text()
    start = src.find("if vim.env.DISPLAY")
    if start == -1 or "vim.g.clipboard" not in src:
        return ""
    end = src.find("\nend", src.find("vim.g.clipboard", start))
    return src[start : end + 4] if end != -1 else src[start:]


def test_paste_is_not_wired_to_the_blocking_reader():
    """The 10s-hang hazard, guarded where no nvim exists to demonstrate it."""
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
    """A bare `require` here would break far more than the clipboard.

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
    # Checked for PRESENCE before the ordering compare on purpose -- str.find
    # returns -1 when absent, and -1 < <any real index> is true, so dropping
    # the branch entirely would satisfy an ordering-only assertion vacuously.
    # The mutant that keeps pcall and drops the branch is what proved this.
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
    """An unconditional provider would break the working local X11 path."""
    src = NATIVE_LUA.read_text()
    idx = src.find("vim.g.clipboard")
    assert idx != -1, f"no vim.g.clipboard assignment in {NATIVE_LUA}"

    guard = src[:idx]
    assert "vim.env.DISPLAY" in guard, (
        "the OSC 52 provider is not guarded on DISPLAY being absent; with an X "
        "display neovim's xclip autodetection is better (it supports a real "
        "paste) and must be left alone"
    )


def test_the_behavioural_half_is_registered_on_the_dev_host_tier():
    """The seam: these guards are only HALF the coverage, by construction.

    If the behavioural file is dropped or renamed without updating
    DEVHOST_TARGETS, the structural guards keep passing and the red->green
    evidence silently stops running -- in BOTH tiers, since the hermetic one
    never ran it. Assert the relationship, not just the file.
    """
    behavioural = REPO / "scripts" / "devhost-tests" / "test_nvim_clipboard_behaviour.py"
    assert behavioural.is_file(), (
        f"{behavioural.relative_to(REPO)} is missing; the red->green coverage "
        "for this fix runs nowhere"
    )

    # 🔴 Parse the DEVHOST_TARGETS ARRAY, never `"scripts/devhost-tests" in
    # runner`. That substring form SURVIVED its mutant: unregistering the target
    # left the path spelled in a comment and in the EXPECTED_SKIPS pin, so the
    # assertion passed while the behavioural suite was collected by no tier at
    # all -- a guard that read as coverage and provided none.
    runner = (REPO / "scripts" / "run-tests.sh").read_text()
    start = runner.find("DEVHOST_TARGETS=(")
    assert start != -1, "DEVHOST_TARGETS array not found in run-tests.sh"
    body = runner[start + len("DEVHOST_TARGETS=(") : runner.index(")", start)]
    registered = {
        line.strip() for line in body.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    assert "scripts/devhost-tests" in registered, (
        "scripts/devhost-tests is not in the DEVHOST_TARGETS array, so the "
        f"behavioural tests are collected by no tier at all. Registered: {registered or '{}'}"
    )
