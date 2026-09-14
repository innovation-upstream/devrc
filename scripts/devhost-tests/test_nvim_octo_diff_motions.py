"""BEHAVIOURAL: the legend's `vim built-ins` section, checked against real vim.

Dev-host tier -- drives a REAL neovim, which the nix sandbox has not got.

🔴 WHY THIS FILE EXISTS. The legend in `nix/pkgs/tools/nvim-octo/octo-init.lua`
is GENERATED from `octo.config.values.mappings[kind]`, and that is deliberate:
a curated list of keys goes stale silently. Its new `vim built-ins` section
cannot be generated that way, because vim's own motions are not enumerable from
Lua -- `]c`, `[c` and the `z…` fold commands are compiled into the editor. So
`M.NATIVE_MOTIONS` is the ONE hand-written list in the feature, and a
hand-written list of keystrokes is exactly the staleness surface the generated
legend was built to avoid.

What closes that is this tier. Every entry in that list is driven, in a real
neovim, in a real diff built the way octo builds one (`diffthis`, plus the
`foldmethod = "diff"` / `foldlevel = 0` that `reviews/file-entry.lua` sets), and
each must DO what its own description says. A motion upstream neovim removes or
changes fails here rather than sitting in the legend telling the operator
something false.

🔴 AND IT IS PINNED TWO-WAY. `_PROBES` below must cover `NATIVE_MOTIONS`
exactly: a motion added to the Lua with no probe here fails, and a probe naming
a motion the Lua no longer lists fails. Adding a row to the legend therefore
costs a behavioural check, which is the whole point.

⚠ WHAT THIS TIER DOES **NOT** LOAD IS octo.nvim. Its plugin tree lives at a nix
store path that nothing on PATH resolves (the wrapper is deliberately absent
from `home.packages`), so requiring it here would mean either a hardcoded store
path -- stale the next time the derivation changes -- or a `nix build` inside a
test. The octo-side half of the claim is covered elsewhere and not by omission:
the hermetic suite drives the `maparg` filter that drops any motion something
has mapped, and `test_nvim_octo.py` pins the legend's sections against octo's
live config. Everything in THIS file is a claim about vim, which is what the
hand-written list is a claim about.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INIT_LUA = REPO / "nix" / "pkgs" / "tools" / "nvim-octo" / "octo-init.lua"

# The motions the operator asked for by name. Restated here rather than
# imported, so this tier and the hermetic one are two independent readings of
# the same requirement.
REQUIRED = ("]c", "[c", "zo", "zc", "zr", "zm", "zi")

# A healthy run of one probe is tens of milliseconds (MEASURED: eight of them in
# 0.18s wall). See `_drive` for why this is deliberately tight.
_NVIM_TIMEOUT_S = 45

# 🔴 ONE PROBE PER MOTION, AND EACH ASSERTS THE MOTION'S OWN CLAIM RATHER THAN
# "something changed". The diff fixture differs at lines 5 and 20 of 30, so a
# probe that merely moved the cursor somewhere, or toggled some fold, would not
# pass: the expected line numbers and fold states are specific to it.
#
# ⚠ THE FIXTURE'S CHANGED LINES ARE 5 AND 20 ON PURPOSE -- neither is 1, neither
# is the last line, and they are far enough apart that line 12 is inside the
# unchanged run between them and therefore genuinely foldable. A fixture whose
# only difference sat at line 1 would make `]c` pass without moving.
_PROBES = {
    "]c": """
        cursor(1)
        vim.cmd("normal! ]c")
        expect("]c", cursor_line(), 5)
    """,
    "[c": """
        cursor(30)
        vim.cmd("normal! [c")
        expect("[c", cursor_line(), 20)
    """,
    "zo": """
        reset_folds()
        expect("zo:precondition", vim.fn.foldclosed(12), 12)
        cursor(12)
        vim.cmd("normal! zo")
        expect("zo", vim.fn.foldclosed(12), -1)
    """,
    "zc": """
        reset_folds()
        cursor(12)
        vim.cmd("normal! zo")
        expect("zc:precondition", vim.fn.foldclosed(12), -1)
        vim.cmd("normal! zc")
        expect("zc", vim.fn.foldclosed(12), 12)
    """,
    "zr": """
        reset_folds()
        expect("zr:precondition", vim.fn.foldclosed(12), 12)
        vim.cmd("normal! zr")
        expect("zr", vim.fn.foldclosed(12), -1)
    """,
    "zm": """
        reset_folds()
        vim.cmd("normal! zr")
        expect("zm:precondition", vim.fn.foldclosed(12), -1)
        vim.cmd("normal! zm")
        expect("zm", vim.fn.foldclosed(12), 12)
    """,
    "zi": """
        reset_folds()
        expect("zi:precondition", tostring(vim.wo[0].foldenable), "true")
        vim.cmd("normal! zi")
        expect("zi", tostring(vim.wo[0].foldenable), "false")
        vim.cmd("normal! zi")
        expect("zi:again", tostring(vim.wo[0].foldenable), "true")
    """,
}

_PRELUDE = r"""
local OUT = assert(io.open(os.getenv("PROBE_OUT"), "w"))
local function say(k, v) OUT:write("KV\t" .. tostring(k) .. "\t" .. tostring(v) .. "\n") end
local function expect(what, got, want)
  if tostring(got) == tostring(want) then
    say("probe:" .. what, "ok")
  else
    say("probe:" .. what, "WRONG got=" .. tostring(got) .. " want=" .. tostring(want))
  end
end
local function cursor(line) vim.api.nvim_win_set_cursor(0, {line, 0}) end
local function cursor_line() return vim.api.nvim_win_get_cursor(0)[1] end

-- The diff octo would have built: two windows, `diffthis` on both, then the
-- window options `reviews/file-entry.lua` sets on each side.
local left, right = {}, {}
for i = 1, 30 do left[i] = "line " .. i; right[i] = "line " .. i end
right[5] = "CHANGED five"
right[20] = "CHANGED twenty"

vim.cmd("enew")
vim.api.nvim_buf_set_lines(0, 0, -1, false, left)
vim.cmd("diffthis")
vim.cmd("belowright vsp")
vim.cmd("enew")
local rbuf = vim.api.nvim_get_current_buf()
vim.api.nvim_buf_set_lines(rbuf, 0, -1, false, right)
vim.cmd("diffthis")
vim.wo[0].foldmethod = "diff"
vim.wo[0].foldlevel = 0
local DIFF_WIN = vim.api.nvim_get_current_win()

local function reset_folds()
  vim.api.nvim_set_current_win(DIFF_WIN)
  vim.wo[0].foldenable = true
  vim.wo[0].foldmethod = "diff"
  vim.wo[0].foldlevel = 0
  cursor(1)
end
reset_folds()
"""

_EPILOGUE = r"""
say("done", "yes")
OUT:close()
vim.cmd("qa!")
"""


def _drive(tmp_path: Path, body: str) -> dict[str, list[str]]:
    """Run the wrapper's own init plus `body` in a real headless neovim.

    🔴 THE INIT IS SOURCED THE WAY THE WRAPPER SOURCES IT -- as a `-c luafile`
    on the command line, not from inside another Lua chunk. MEASURED: sourcing
    it with `vim.cmd("luafile …")` promotes the "octo failed to initialise"
    notification into an `E5113` that ABANDONS the rest of the chunk, so the
    probe never ran and the failure looked like a broken test. octo is absent on
    this tier by design (see the module docstring), so that notification always
    fires and the load path has to survive it.
    """
    assert INIT_LUA.exists(), f"{INIT_LUA} is missing -- was it `git add`ed?"
    probe = tmp_path / "probe.lua"
    probe.write_text(_PRELUDE + body + _EPILOGUE, encoding="utf-8")
    out = tmp_path / "probe.kv"
    env = dict(os.environ)
    env["PROBE_OUT"] = str(out)
    try:
        subprocess.run(
            ["nvim", "--headless", "-u", "NONE", "-i", "NONE",
             "-c", f"luafile {INIT_LUA}", "-c", f"luafile {probe}"],
            env=env, capture_output=True, text=True, timeout=_NVIM_TIMEOUT_S,
            stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired as exc:
        # 🔴 A HANG HERE IS A DIAGNOSIS, NOT A FLAKE, AND IT IS WORTH NAMING.
        # MEASURED: when the probe raises — e.g. it reaches for a field the
        # init no longer defines — the resulting `E5113` puts headless neovim
        # on a `Press ENTER` prompt that `stdin=DEVNULL` does not clear. A
        # healthy run of this file takes tens of MILLISECONDS, so the timeout
        # is set low on purpose: the interesting failure is "the probe could
        # not run", and waiting three minutes to say so helps nobody.
        raise AssertionError(
            f"neovim did not exit within {_NVIM_TIMEOUT_S}s. That is almost "
            f"always the probe raising inside `luafile` and neovim then "
            f"waiting at a hit-enter prompt — read {probe} against the "
            f"current {INIT_LUA.name}, rather than treating this as slowness."
        ) from exc
    kv: dict[str, list[str]] = {}
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) == 3 and parts[0] == "KV":
                kv.setdefault(parts[1], []).append(parts[2])
    return kv


def test_the_tools_this_tier_needs_are_present():
    """Fail loudly rather than skip -- an unpinned skip would report motion
    behaviour this file never measured."""
    assert shutil.which("nvim"), "nvim missing on the dev-host tier"


def test_the_probe_harness_can_actually_fire(tmp_path):
    """🔴 INSTRUMENT VALIDATION, BOTH DIRECTIONS, BEFORE ANY VERDICT BELOW.

    NEGATIVE CONTROL -- an `expect` that is wrong must be REPORTED as wrong. A
    harness whose mismatches came back `ok` would green every probe in this
    file.

    POSITIVE CONTROL -- the harness reaches the real editor and the real init:
    `NvimOcto` exists and its motion list is non-empty. A run that produced no
    KV lines at all is indistinguishable from a run where everything passed,
    until one of them has been watched to differ.
    """
    kv = _drive(tmp_path, textwrap.dedent("""
        expect("deliberate-mismatch", 1, 2)
        expect("deliberate-match", 7, 7)
        say("nvim_octo", type(_G.NvimOcto))
        say("motion_count", #_G.NvimOcto.NATIVE_MOTIONS)
    """))
    assert kv.get("done") == ["yes"], (
        f"the probe never reached its end -- nothing below is evidence: {kv}")
    assert kv["probe:deliberate-mismatch"][0].startswith("WRONG"), (
        "a deliberately wrong expectation came back clean, so this harness "
        f"cannot report a failure: {kv}")
    assert kv["probe:deliberate-match"] == ["ok"], kv
    assert kv["nvim_octo"] == ["table"], (
        f"the wrapper's init did not define `NvimOcto` in a real neovim: {kv}")
    assert int(kv["motion_count"][0]) >= len(REQUIRED), kv


def test_the_probe_table_COVERS_the_lua_motion_list_exactly(tmp_path):
    """🔴 THE TWO-WAY PIN. The hand-written list in the Lua and the behavioural
    probes here must name the same set. A motion added to the legend with no
    probe would be an unchecked claim on screen; a probe for a motion the legend
    no longer lists is a test measuring nothing.
    """
    kv = _drive(tmp_path, textwrap.dedent("""
        for _, m in ipairs(_G.NvimOcto.NATIVE_MOTIONS) do
          say("lhs", m.lhs)
        end
    """))
    declared = set(kv.get("lhs", []))
    assert declared, f"the init declared no native motions at all: {kv}"
    probed = set(_PROBES)
    assert declared == probed, (
        f"the legend's native motions and this file's probes disagree.\n"
        f"  in the legend, not probed here: {sorted(declared - probed)}\n"
        f"  probed here, not in the legend: {sorted(probed - declared)}\n"
        f"Adding a row to the legend costs a behavioural probe -- that is what "
        f"stops the one hand-written list in the feature from going stale.")
    assert set(REQUIRED) <= declared, sorted(declared)


def test_every_listed_motion_DOES_WHAT_ITS_DESCRIPTION_SAYS(tmp_path):
    """🔴 THE CLAIM THE LEGEND MAKES, MEASURED. Each motion is driven in a real
    diff and its specific effect checked: `]c` lands on line 5 from line 1,
    `[c` on line 20 from line 30, and each fold command moves the fold at line
    12 in its own direction.

    Every probe also asserts its own PRECONDITION, so a probe cannot pass by
    the fold already being in the state it was going to check.

    ⚠ INVARIANT GUARD, NOT REGRESSION COVERAGE, AND IT IS LABELLED BECAUSE IT
    WAS MEASURED: this is green at `e8fa6fca` too, because its body touches only
    vim. That is exactly what it is for — it is a claim about NEOVIM, which is
    the half of the legend's new section this repo does not control and cannot
    generate. The test that goes red when the legend's list drifts is
    `test_the_probe_table_COVERS_the_lua_motion_list_exactly`; this one is what
    makes that coverage mean something.
    """
    body = "\n".join(textwrap.dedent(src) for src in _PROBES.values())
    kv = _drive(tmp_path, body)
    assert kv.get("done") == ["yes"], f"the probe run did not complete: {kv}"
    wrong = {k: v for k, v in kv.items()
             if k.startswith("probe:") and v != ["ok"]}
    assert not wrong, (
        f"a motion the legend advertises does not behave as described in a "
        f"real neovim diff: {wrong}")
    # The count, so a run that silently probed nothing cannot read as clean.
    assert len([k for k in kv if k.startswith("probe:")]) >= len(_PROBES), kv


def test_every_listed_motion_is_UNMAPPED_in_a_real_diff_buffer(tmp_path):
    """A built-in stops being a built-in the moment something maps it. This is
    the live reading of the filter the hermetic suite drives with a stub --
    asked here of a real buffer, through `vim.fn.maparg`, which is the function
    the legend itself asks."""
    kv = _drive(tmp_path, textwrap.dedent("""
        for _, m in ipairs(_G.NvimOcto.NATIVE_MOTIONS) do
          say("free:" .. m.lhs, tostring(_G.NvimOcto.key_is_unmapped(m.lhs)))
        end
        -- POSITIVE CONTROL on the checker: map one and watch it say so.
        vim.keymap.set("n", "]c", "<Nop>", {buffer = true})
        say("after_mapping", tostring(_G.NvimOcto.key_is_unmapped("]c")))
    """))
    taken = {k: v for k, v in kv.items()
             if k.startswith("free:") and v != ["true"]}
    assert not taken, (
        f"the legend advertises these as vim's own, but they are mapped in a "
        f"real diff buffer: {taken}")
    assert kv["after_mapping"] == ["false"], (
        "`key_is_unmapped` still said a freshly mapped key was free, so the "
        f"shadow filter is wired to nothing: {kv}")


def test_the_legend_RENDERS_the_native_section_in_a_real_diff_window(tmp_path):
    """The whole thing, end to end, in a real editor: in a window vim really
    has put into diff mode, the rendered legend carries the separately labelled
    section and every motion in it -- and in a window that is not in diff mode,
    it does not."""
    kv = _drive(tmp_path, textwrap.dedent("""
        say("in_diff", tostring(_G.NvimOcto.in_diff_mode()))
        for _, line in ipairs(_G.NvimOcto.legend_lines("review_diff")) do
          say("diffline", line)
        end
        vim.cmd("tabnew")
        say("in_diff_elsewhere", tostring(_G.NvimOcto.in_diff_mode()))
        for _, line in ipairs(_G.NvimOcto.legend_lines("review_diff")) do
          say("plainline", line)
        end
    """))
    assert kv["in_diff"] == ["true"], kv
    assert kv["in_diff_elsewhere"] == ["false"], (
        "a fresh tab still reports diff mode, so the gate is not reading the "
        f"window at all: {kv}")
    diff_blob = "\n".join(kv["diffline"])
    plain_blob = "\n".join(kv["plainline"])
    assert "VIM'S OWN diff motions" in diff_blob, diff_blob
    assert "NOT octo's" in diff_blob, diff_blob
    assert "VIM'S OWN diff motions" not in plain_blob, plain_blob
    for motion in REQUIRED:
        assert any(line.strip().startswith(motion + " ")
                   for line in kv["diffline"]), (
            f"{motion!r} is missing from the legend a real diff window "
            f"renders:\n{diff_blob}")


def test_the_real_editor_gets_the_tuned_diff_layout(tmp_path):
    """The layout half, read back off the running editor rather than off the
    source. `wrap` off is the one that matters most -- a wrapped long line in
    one pane de-aligns every row after it in a side-by-side diff -- and
    `diffopt` must be MERGED, keeping the settings this wrapper has no opinion
    about."""
    kv = _drive(tmp_path, textwrap.dedent("""
        say("wrap", tostring(vim.o.wrap))
        say("diffopt", vim.o.diffopt)
        say("fillchars", vim.o.fillchars)
        say("panel_short", _G.NvimOcto.file_panel_size(24))
        say("panel_tall", _G.NvimOcto.file_panel_size(80))
    """))
    assert kv["wrap"] == ["false"], kv
    diffopt = kv["diffopt"][0].split(",")
    for kept in ("internal", "filler"):
        assert kept in diffopt, (
            f"`{kept}` was dropped from diffopt -- the merge overwrote "
            f"neovim's own settings instead of replacing ours: {diffopt}")
    assert "algorithm:histogram" in diffopt, diffopt
    assert "linematch:60" in diffopt and "linematch:40" not in diffopt, diffopt
    assert "diff:" in kv["fillchars"][0], kv["fillchars"]
    # Two points, because the panel height is a function OF the terminal.
    assert int(kv["panel_short"][0]) < int(kv["panel_tall"][0]), kv


def test_wrap_flips_BOTH_ways_when_a_real_window_is_REUSED(tmp_path):
    """🔴 THE BUG A REAL EDITOR FOUND. `wrap` is window-local, so a hook that
    only turns wrapping ON for octo's prose buffers leaves that window wrapping
    for whatever is loaded into it next -- and octo's review layout loads every
    file's diff into the SAME two windows. Driven here as the sequence that
    reproduces it: prose, then code, in ONE window.
    """
    kv = _drive(tmp_path, textwrap.dedent("""
        vim.cmd("tabnew")
        say("fresh", tostring(vim.wo[0].wrap))
        vim.bo[0].filetype = "octo"
        say("prose", tostring(vim.wo[0].wrap))
        vim.cmd("enew")
        vim.bo[0].filetype = "lua"
        say("code_same_window", tostring(vim.wo[0].wrap))
        vim.cmd("enew")
        vim.bo[0].filetype = "octo"
        say("prose_again", tostring(vim.wo[0].wrap))
        local win = _G.NvimOcto.show_legend("review_diff")
        say("legend", tostring(vim.wo[win].wrap))
    """))
    assert kv["fresh"] == ["false"], kv
    assert kv["prose"] == ["true"], (
        f"octo's prose buffers are not wrapping: {kv}")
    assert kv["code_same_window"] == ["false"], (
        "the same window kept wrapping after a non-prose buffer was loaded "
        f"into it -- this is the window-local leak: {kv}")
    assert kv["prose_again"] == ["true"], kv
    assert kv["legend"] == ["true"], (
        f"the legend float does not wrap, so a heading wider than it is "
        f"simply not on screen: {kv}")
