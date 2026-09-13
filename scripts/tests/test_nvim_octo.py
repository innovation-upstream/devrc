"""`nvim-octo` — the review TUI a clicked GitHub mention opens in.

🔴 WHAT THIS FILE CAN AND CANNOT SEE, STATED FIRST SO A GREEN RUN IS NOT READ
AS MORE THAN IT IS.

It asserts three things, all from files in this repo:

  * the octo.nvim configuration the wrapper ships — parsed STRUCTURALLY, so the
    claim is about the table that will be handed to `setup()`, not about words
    in a comment;
  * the legend, the `?` binding and the merge confirmation — by EXECUTING
    `octo-init.lua` under `luajit` with `vim` and `require` stubbed, so a `no`
    answer is watched failing to merge rather than reasoned about;
  * the wrapper's argument validation — by RUNNING the shell text with `nvim`
    stubbed, so a rejection is watched rather than reasoned about.

🔴 THE FIRST TIER IS STRUCTURALLY BLIND TO THE SECOND, AND THAT IS WHY THE
SECOND EXISTS. A keymap installed with `vim.keymap.set` is not a table entry,
so every structural reader here is blind to it — a merge keystroke can appear,
and an UNCONFIRMED one can appear, with the config section fully green. That
happened: `test_no_merge_keystroke_is_declared_for_a_pull_request_buffer` read
as "no merge keystroke exists" and kept passing when one was added.

It cannot see, and does not claim: that alacritty maps a window; that neovim
starts; that `setup()` ran; that `Octo <N> <owner/repo>` resolved the right
buffer KIND (a live GraphQL call); that real neovim accepts the option tables
the lua tier passes to `vim.keymap.set` / `nvim_open_win`; that `?` is
reachable in a LIVE review; or that the float looks right on screen. A config
file saying a mapping is gone is a claim about the file, and a stubbed `vim` is
a claim about our logic — only a running editor proves the buffer. Those were
measured by hand on the built derivation and recorded in the PR, because the
measurement needs a nix build and a real editor, and a test that skips itself
when it cannot get one is worse than no test.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from testlib.mockbin import write_exec  # noqa: E402

# Resolved once, to an ABSOLUTE path, for the same reason the sibling suites do
# it: `/usr/bin/env` does not exist in the nix build sandbox, and a bare "bash"
# would be looked up in the child's PATH — which this harness deliberately
# front-loads with a stub directory.
_BASH = shutil.which("bash")

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "nix" / "pkgs" / "tools" / "nvim-octo"
INIT_LUA = PKG / "octo-init.lua"
WRAPPER_SH = PKG / "nvim-octo.sh"
PKG_NIX = PKG / "default.nix"

# 🔴 THE MERGE FAMILY, READ OFF octo 2026-08-28's OWN `get_default_values()`.
# Four of these are keymaps that merge IMMEDIATELY — octo's `pr merge` calls
# `gh.pr.merge(opts)` with no confirmation of any kind — and three queue a merge.
# The brief named four; the other three were found by reading the defaults, and
# they are here for the same reason the first four are.
MERGE_MAPPINGS = (
    "merge_pr",
    "squash_and_merge_pr",
    "rebase_and_merge_pr",
    "merge_pr_stack",
    "merge_pr_queue",
    "squash_and_merge_queue",
    "rebase_and_merge_queue",
)

# `pr_options` is `<CR>` — ONE keystroke — and its menu (octo's `mappings.lua`)
# offers "Merge PR", "Squash and Merge PR" and "Delete Branch". Stripping every
# merge KEYMAP while re-declaring this would be a guard that reads as coverage
# and provides none, so it is treated as part of the same family.
MERGE_MENUS = ("pr_options",)


# --------------------------------------------------------------------------- #
# A STRUCTURAL LUA TABLE READER
#
# 🔴 NOT A GREP, AND THE DIFFERENCE IS THE WHOLE POINT. A `grep` for
# `mappings_disable_default = true` is walkable two ways: by rewording, and —
# far worse — by a later `mappings.pull_request` table re-adding `merge_pr`
# UNDERNEATH it, which the grep would never see. The reader below answers "which
# keys does this table declare", which is the state that decides what is bound.
# --------------------------------------------------------------------------- #
def _strip_comments(source: str) -> str:
    """Lua source with every `--` line comment removed, strings left intact.

    🔴 THIS IS NOT TIDINESS — WITHOUT IT EVERY READER BELOW IS WRONG, AND IT WAS.
    MEASURED on the first run of this file: `_scalar(lua, "picker")` returned
    `-- "fzf-lua"`: MEASURED against octo 2026-08-28` — a sentence out of the
    comment that EXPLAINS the setting, not the setting. The comments here are
    prose ABOUT the config and name every value they discuss, so reading them as
    config makes a passing test a fact about documentation. The alacritty
    `makeBinPath` ledger in test_mention_open.py strips comments for exactly
    this reason, and hit exactly this bug.

    ⚠ A `--` INSIDE A STRING IS NOT A COMMENT, so this tracks quoting rather
    than cutting at the first `--` on a line. Nothing in the config uses that
    today; a `desc = "foo -- bar"` added tomorrow would silently truncate the
    line and drop whatever followed it on that line from every reader.
    Long-bracket comments (`--[[ … ]]`) are NOT handled and do not occur here —
    if one is added, this returns its body as code and the control test below is
    what should be extended first.
    """
    out: list[str] = []
    for line in source.split("\n"):
        quote = None
        i = 0
        cut = len(line)
        while i < len(line):
            ch = line[i]
            if quote:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote = None
            elif ch in ('"', "'"):
                quote = ch
            elif ch == "-" and line[i + 1:i + 2] == "-":
                cut = i
                break
            i += 1
        out.append(line[:cut])
    return "\n".join(out)


def _table_body(source: str, key: str, *, start: int = 0) -> tuple[str, int]:
    """The brace-balanced body of `key = { … }`, and the index just past it.

    Raises rather than returning a sentinel: a table this cannot find means the
    config was restructured, and an empty string would make every "the merge
    keys are absent" assertion below pass VACUOUSLY. That is the exact shape of
    a reassuring zero, so it fails loudly instead.
    """
    m = re.search(r"(?<![\w.])" + re.escape(key) + r"\s*=\s*\{", source[start:])
    if not m:
        raise AssertionError(
            f"no `{key} = {{` table in the config — it was restructured, and "
            f"every assertion about its contents would now pass vacuously")
    open_at = start + m.end() - 1
    depth = 0
    for i in range(open_at, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[open_at + 1:i], i + 1
    raise AssertionError(f"unbalanced braces after `{key} = {{`")


def _top_level_keys(body: str) -> set[str]:
    """The `name =` keys declared at depth 0 of a table body.

    Depth-tracked, so a nested `{ lhs = …, desc = … }` contributes nothing —
    `lhs` and `desc` appear under every single entry and would otherwise swamp
    the answer.
    """
    keys: set[str] = set()
    depth = 0
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif depth == 0:
            m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", body[i:])
            if m and (i == 0 or not re.match(r"[\w.]", body[i - 1])):
                keys.add(m.group(1))
                i += m.end()
                continue
        i += 1
    return keys


def _scalar(source: str, key: str) -> str:
    """The literal a top-level `key = <value>` is set to, comments stripped."""
    m = re.search(r"(?<![\w.])" + re.escape(key) + r"\s*=\s*([^,\n]+)", source)
    assert m, f"`{key}` is not set in {INIT_LUA.name} — it takes the upstream default"
    return m.group(1).strip().strip('"').strip("'")


def test_the_lua_table_reader_can_actually_fire():
    """🔴 POSITIVE **AND** NEGATIVE CONTROL ON THE INSTRUMENT, before any verdict
    it produces is believed. A reader that matched nothing would report an empty
    key set forever, and every "no merge key is declared" assertion below would
    be a fact about the reader rather than about the config."""
    sample = """
    mappings = {
      pull_request = {
        checkout_pr = { lhs = "<localleader>po", desc = "checkout PR" },
        merge_pr = { lhs = "<localleader>pm", desc = "merge commit PR" },
      },
      issue = {
        close_issue = { lhs = "<localleader>ic", desc = "close issue" },
      },
    }
    """
    body, _ = _table_body(sample, "pull_request")
    keys = _top_level_keys(body)
    # POSITIVE: it finds the keys that ARE there…
    assert keys == {"checkout_pr", "merge_pr"}, keys
    # …and does NOT report the nested `lhs`/`desc` of every entry.
    assert "lhs" not in keys and "desc" not in keys
    # NEGATIVE: it can see a merge key when one is present. Without this, "no
    # merge key found" would be indistinguishable from a reader wired to
    # nothing — the reassuring zero this repo keeps paying for.
    assert MERGE_MAPPINGS[0] in keys
    # It also does not leak into the SIBLING table.
    assert "close_issue" not in keys
    # A table that is not there is an ERROR, never an empty set.
    with pytest.raises(AssertionError):
        _table_body(sample, "no_such_table")


def test_the_comment_stripper_can_actually_fire():
    """🔴 THE CONTROL FOR THE BUG THIS FILE ALREADY HIT ONCE. Before the
    stripper existed, `_scalar(lua, "picker")` read a value out of the comment
    that explains the setting. Both directions are asserted: prose goes, code
    stays, and a `--` inside a STRING is not a comment."""
    src = '\n'.join([
        '-- picker = "telescope" is what upstream defaults to',
        'picker = "fzf-lua",           -- and this is the real one',
        'desc = "a -- b",              -- a double dash inside a string',
    ])
    out = _strip_comments(src)
    # The prose value is gone…
    assert "telescope" not in out, out
    # …the real one survives, with its trailing comment removed…
    assert _scalar(out, "picker") == "fzf-lua"
    assert "the real one" not in out, out
    # …and a `--` inside a string is NOT treated as a comment start.
    assert 'desc = "a -- b"' in out, out
    # NEGATIVE CONTROL: on source with no comments it changes nothing, so a
    # stripper that simply deleted lines could not pass this pair.
    assert _strip_comments('picker = "fzf-lua",') == 'picker = "fzf-lua",'


# --------------------------------------------------------------------------- #
# THE CONFIG — asserted as STATE
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def lua() -> str:
    assert INIT_LUA.exists(), f"{INIT_LUA} is missing — was it `git add`ed?"
    raw = INIT_LUA.read_text(encoding="utf-8")
    stripped = _strip_comments(raw)
    # 🔴 A CHECK ON THE STRIPPER ITSELF, AGAINST THE REAL FILE RATHER THAN A
    # FIXTURE. The comments in this config quote every value they discuss, so a
    # stripper that silently stopped working would hand every reader below the
    # prose again — and the readers would happily find the right answers in the
    # wrong place. The file is heavily commented by design, so "something was
    # removed" is a safe and meaningful invariant.
    assert len(stripped) < len(raw) * 0.75, (
        "the comment stripper removed almost nothing — every reader below is "
        "now at risk of reading prose as configuration")
    return stripped


@pytest.fixture(scope="module")
def pr_keys(lua: str) -> set[str]:
    # Anchored INSIDE the `mappings = {` table, so a future top-level key
    # called `pull_request` somewhere else cannot be read instead.
    mappings_body, _ = _table_body(lua, "mappings")
    pr_body, _ = _table_body(mappings_body, "pull_request")
    return _top_level_keys(pr_body)


def test_the_pull_request_table_is_NOT_empty(pr_keys: set[str]):
    """🔴 THE POSITIVE CONTROL FOR EVERY ABSENCE ASSERTED BELOW. "No merge key
    is declared" is trivially true of a table that declares nothing at all —
    which would also mean reviewing is impossible, and would pass every other
    test in this section. Reviewing is the point of the feature, so the table
    must be substantial AND must carry the keys a review actually needs."""
    assert len(pr_keys) > 30, sorted(pr_keys)
    for needed in ("review_start", "review_resume", "approve_pr",
                   "add_comment", "list_changed_files", "show_pr_diff",
                   "toggle_checks", "resolve_thread"):
        assert needed in pr_keys, (
            f"`{needed}` is not declared, so the review surface this feature "
            f"exists for is incomplete: {sorted(pr_keys)}")


@pytest.mark.parametrize("key", MERGE_MAPPINGS + MERGE_MENUS)
def test_octos_OWN_UNCONFIRMED_merge_actions_are_NOT_declared_in_the_table(
        pr_keys: set[str], key: str):
    """🔴 THIS GUARD NO LONGER MEANS "NO MERGE KEYSTROKE EXISTS", AND SAYING SO
    IS THE POINT OF THIS DOCSTRING.

    It used to. The decision then was that merging must require typing `:Octo
    pr merge`; the operator has since asked for a keymap, and one now exists
    (`<localleader>pm`). This guard kept passing across that change without a
    single edit — a merge keystroke appeared and the test that read as "no
    merge keystroke exists" stayed green — which is precisely the "reads as
    coverage, provides none" shape. It is retained with a NARROWER and now
    TRUE claim, and the behavioural half lives in
    `test_every_path_to_a_merge_passes_through_the_confirmation` and
    `test_the_merge_KEYMAP_itself_is_on_the_confirmed_path` below.

    THE NARROWER CLAIM: none of OCTO's own merge actions may be declared here.
    Those are the unconfirmed ones — `octo.commands.merge_pr` calls
    `gh.pr.merge(opts)` with no prompt of any kind, and `pr_options` puts
    "Merge PR" / "Squash and Merge PR" / "Delete Branch" behind a single `<CR>`.
    Declaring one would bind THAT, which is the unconfirmed merge this config
    exists to prevent — and it would do so while the wrapper's own confirmed
    keymap sat beside it looking like the safety.
    """
    assert key not in pr_keys, (
        f"`{key}` is declared in mappings.pull_request, which binds OCTO's own "
        f"merge action. That action prompts for NOTHING. The wrapper's merge "
        f"keymap must stay the only one, because it is the only one that asks.")


def test_the_default_mappings_are_cleared_before_ours_are_merged(lua: str):
    """Without this flag `vim.tbl_deep_extend` MERGES our table into upstream's,
    so every merge keymap survives and the test above would be asserting the
    absence of something that is nonetheless bound. Structural: the flag and the
    declared-key set are two halves of one guarantee, and neither alone is it."""
    assert _scalar(lua, "mappings_disable_default") == "true"


def test_the_merge_method_is_squash(lua: str):
    """Upstream defaults to `"merge"` — a merge commit. These repositories
    squash, so left at the default the FIRST merge made from this TUI would
    produce the wrong commit shape."""
    assert _scalar(lua, "default_merge_method") == "squash"


def test_the_branch_is_NOT_deleted_on_merge(lua: str):
    """Also upstream's default, and kept EXPLICIT because it is load-bearing
    here rather than incidental: these repos set `delete_branch_on_merge`, and
    deleting a STACKED PARENT's branch makes GitHub auto-close the child PR and
    then refuse to reopen it — the branch is restorable, the PR object is
    not."""
    assert _scalar(lua, "default_delete_branch") == "false"


def test_the_review_never_wants_a_local_checkout(lua: str):
    """octo offers to check a PR out only when this is true. A review opened
    from a clicked link must never touch a working tree — other sessions share
    these checkouts."""
    assert _scalar(lua, "use_local_fs") == "false"


def test_the_picker_is_fzf_lua_which_is_a_SAFETY_setting(lua: str):
    """🔴 A SECOND, SEPARATE MERGE PATH THAT `mappings_disable_default` DOES NOT
    TOUCH. `picker_config.mappings` lives OUTSIDE the `mappings` table that flag
    clears, so upstream's `merge_pr = { lhs = "<C-r>" }` for the picker survives
    it — MEASURED on the built derivation: after setup,
    `picker_config.mappings.merge_pr.lhs` is still `<C-r>`.

    What closes it is the picker CHOICE: measured against octo 2026-08-28, the
    whole `lua/octo/pickers/fzf-lua/` tree contains no occurrence of "merge" at
    all, while `pickers/telescope/provider.lua` wires
    `cfg.picker_config.mappings.merge_pr.lhs` to a merge action in two places.
    So this line is the guard, and switching pickers silently re-opens the
    path."""
    assert _scalar(lua, "picker") == "fzf-lua"


def test_every_other_mapping_table_the_flag_cleared_is_re_declared(lua: str):
    """`mappings_disable_default` empties NINE tables, not just
    `pull_request`. A table left undeclared is a surface with no keymaps at
    all — `submit_win` is how a review is approved or has changes requested, and
    an empty one would make the feature's stated scope unreachable while every
    merge-safety test above stayed green."""
    mappings_body, _ = _table_body(lua, "mappings")
    declared = _top_level_keys(mappings_body)
    for table in ("pull_request", "issue", "review_thread", "submit_win",
                  "review_diff", "file_panel", "repo", "release",
                  "discussion"):
        assert table in declared, (
            f"`{table}` is cleared by mappings_disable_default and never "
            f"re-declared, so it has NO keymaps at all")
    submit, _ = _table_body(mappings_body, "submit_win")
    submit_keys = _top_level_keys(submit)
    assert {"approve_review", "request_changes"} <= submit_keys, submit_keys


def test_the_config_is_referenced_by_the_derivation(lua: str):
    """The seam between the two files. A config nothing loads is a config that
    describes no editor — and the failure would be silent, because every
    assertion above reads the FILE."""
    nix = PKG_NIX.read_text(encoding="utf-8")
    assert "./octo-init.lua" in nix, nix
    assert "luafile" in nix, (
        "the derivation no longer sources the init file, so the merge-safety "
        "settings asserted above reach no editor")
    assert "octo-nvim" in nix and "fzf-lua" in nix, (
        "octo.nvim declares NO runtime dependencies (a bare buildVimPlugin), "
        "so each plugin must be listed explicitly or the config errors at "
        "startup")


# --------------------------------------------------------------------------- #
# THE LEGEND AND THE CONFIRMED MERGE — EXECUTED, not read
#
# 🔴 THE CONFIG SECTION ABOVE CANNOT SEE ANY OF THIS, AND THAT IS WHY THIS
# SECTION EXISTS. A merge keymap installed by `vim.keymap.set` is not a table
# entry, so every structural reader above is blind to it: a merge keystroke can
# appear, and an unconfirmed one can appear, with the whole section green. The
# only honest guard is to RUN the file and watch what a `no` answer does.
#
# HOW: `octo-init.lua` is executed under `luajit` — the same Lua dialect neovim
# embeds — with `vim` and `require` stubbed. The stub records every
# `vim.keymap.set`, every `vim.notify`, every prompt, and every call that would
# have reached `octo.commands.merge_pr`. Nothing touches a network, a terminal
# or GitHub.
#
# ⚠ WHAT THIS TIER CANNOT SEE, stated so a green run is not read as more than
# it is. The stub is not neovim. It proves OUR logic — which callback a key is
# bound to, that a `no` answer never reaches the merge, that the rows are
# resolved — and it CANNOT prove that real neovim accepts the option tables we
# pass to `vim.keymap.set` / `nvim_open_win`, that `?` is reachable in a live
# review, or that the float looks right. Those were driven by hand against a
# real headless neovim and recorded in the PR.
# --------------------------------------------------------------------------- #

# Resolved to an ABSOLUTE path for the same reason `_BASH` is. Asserted rather
# than skipped: `luajit` is in flake.nix's `gateTools` and in run-tests.sh's
# REQUIRED_TOOLS, so its absence is an environment fault the runner reports by
# name — and a suite that quietly skipped instead would report merge safety it
# never measured, which is the failure this whole file is written against.
_LUAJIT = shutil.which("luajit")

# 🔴 FIXTURE VALUES, CHOSEN TO BE PAIRWISE DISTINCT AND DISTINCT FROM EVERY
# CONSTANT THE ASSERTIONS NAME. `default_merge_method` in the config is
# "squash", so the mutation-probe method below must NOT be "squash" or a mutant
# hardcoding that literal would survive. Likewise the PR number and repo are
# nothing that appears anywhere in `octo-init.lua`.
FIXTURE_PR_NUMBER = "4207"
FIXTURE_PR_REPO = "gardenersguild/trowelcast"
FIXTURE_OTHER_METHOD = "rebase"

# Every buffer kind `mappings_disable_default` clears, i.e. every kind that can
# reach `apply_mappings`. 🔴 THREE OF THESE NEVER GET `filetype = "octo"` —
# review_diff, file_panel and submit_win — which is the measured reason the
# legend rides `apply_mappings` instead of a FileType autocmd.
ALL_KINDS = ("pull_request", "issue", "review_thread", "submit_win",
             "review_diff", "file_panel", "repo", "release", "discussion")

_LUA_PRELUDE = r"""
-- Stubs for everything octo-init.lua reaches outside itself. Records rather
-- than performs, so the scenario below can read what the file DID.
RECORD = {keymaps = {}, notify = {}, merges = {}, prompts = {}, wins = {},
          applied = {}, buflines = nil}
ANSWERS = {}
CURRENT_BUFFER = nil
OCTO_MAPPINGS_MISSING = nil

function KV(k, v) io.write("KV\t", tostring(k), "\t", tostring(v), "\n") end
function FAIL(msg) error("SCENARIO-FAILED: " .. tostring(msg), 0) end

local function autotable()
  return setmetatable({}, {__index = function(t, k)
    local v = {}; rawset(t, k, v); return v
  end})
end

vim = {
  g = {},
  o = {columns = 120, lines = 40},
  bo = autotable(),
  wo = autotable(),
  log = {levels = {ERROR = 4, WARN = 3, INFO = 2}},
  cmd = {colorscheme = function() end},
  fn = {
    strdisplaywidth = function(s) return #s end,
    input = function(prompt)
      RECORD.prompts[#RECORD.prompts + 1] = prompt
      local a = table.remove(ANSWERS, 1)
      -- `<C-c>` at a real `input()` RAISES; this models that, because the
      -- abort arm must cover it and an unguarded call would propagate out.
      if a == "<INTERRUPT>" then error("Keyboard interrupt") end
      return a
    end,
  },
  notify = function(msg, lvl)
    RECORD.notify[#RECORD.notify + 1] = {msg = msg, lvl = lvl}
  end,
  keymap = {
    set = function(mode, lhs, rhs, opts)
      RECORD.keymaps[#RECORD.keymaps + 1] =
        {mode = mode, lhs = lhs, rhs = rhs, opts = opts or {}}
    end,
  },
  api = {
    nvim_create_buf = function() return 99 end,
    nvim_buf_set_lines = function(_, _, _, _, lines) RECORD.buflines = lines end,
    nvim_open_win = function(_, _, cfg)
      RECORD.wins[#RECORD.wins + 1] = cfg; return 77
    end,
    nvim_win_is_valid = function() return true end,
    nvim_win_close = function() RECORD.closed = true end,
  },
}

-- Every octo action name resolves, mirroring a real octo where each key in the
-- config tables names a function in `octo.mappings`. A scenario can knock one
-- out via OCTO_MAPPINGS_MISSING to prove the legend filters on it.
local OCTO_ACTIONS = setmetatable({}, {__index = function(_, k)
  if k == OCTO_MAPPINGS_MISSING then return nil end
  return function() end
end})

CONF = {values = {}}

MODULES = {
  ["octo"] = {
    setup = function(opts)
      for k, v in pairs(opts) do CONF.values[k] = v end
    end,
  },
  ["octo.config"] = CONF,
  ["octo.mappings"] = OCTO_ACTIONS,
  ["octo.commands"] = {
    merge_pr = function(method) RECORD.merges[#RECORD.merges + 1] = method end,
  },
}

-- A scenario models "upstream renamed the seam" by clearing this field in its
-- `setup`, which runs after the prelude and before the file is loaded.
MODULES["octo.utils"] = {
  get_current_buffer = function() return CURRENT_BUFFER end,
  apply_mappings = function(kind, bufnr)
    RECORD.applied[#RECORD.applied + 1] = {kind = kind, bufnr = bufnr}
  end,
}

local real_require = require
require = function(name)
  if MODULES[name] ~= nil then return MODULES[name] end
  return real_require(name)
end

function PR_BUFFER(number, repo)
  return {number = number, repo = repo,
          isPullRequest = function() return true end}
end

-- Every keymap recorded for one lhs, most recent last.
function KEYMAPS_FOR(lhs)
  local out = {}
  for _, m in ipairs(RECORD.keymaps) do
    if m.lhs == lhs then out[#out + 1] = m end
  end
  return out
end

function LOAD_INIT() dofile(INIT_PATH) end
"""


def _run_lua(tmp_path: Path, body: str, *, setup: str = ""):
    """Execute `octo-init.lua` under luajit with the stubs, then run `body`.

    `setup` runs BEFORE the file is loaded (it is how a scenario removes
    `apply_mappings` from the stub, or seeds a different config). `body` runs
    after. Everything the scenario wants observed is printed with `KV`.

    Returns `(proc, kv)` where `kv` maps a key to the LIST of values printed
    for it — a list, not a scalar, because several assertions below are about
    "one row per kind".
    """
    assert INIT_LUA.exists(), f"{INIT_LUA} is missing — was it `git add`ed?"
    assert _LUAJIT, (
        "luajit is not on PATH, so the legend and the merge confirmation are "
        "UNMEASURED. It is declared in flake.nix `gateTools` and in "
        "run-tests.sh REQUIRED_TOOLS; enter the dev shell (`nix develop "
        f"{ROOT}`) rather than letting this suite skip.")
    script = tmp_path / "scenario.lua"
    script.write_text(
        f'INIT_PATH = {json.dumps(str(INIT_LUA))}\n'
        + _LUA_PRELUDE + "\n" + setup + "\nLOAD_INIT()\n" + body + "\n",
        encoding="utf-8")
    proc = subprocess.run([_LUAJIT, str(script)],
                          capture_output=True, text=True, timeout=60)
    kv: dict[str, list[str]] = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0] == "KV":
            kv.setdefault(parts[1], []).append(parts[2])
    return proc, kv


def _ok_lua(tmp_path: Path, body: str, *, setup: str = ""):
    """`_run_lua`, asserting the scenario ran to completion."""
    proc, kv = _run_lua(tmp_path, body, setup=setup)
    assert proc.returncode == 0, (
        f"the lua scenario failed (exit={proc.returncode}):\n{proc.stderr}")
    return kv


def test_the_lua_harness_can_actually_fire(tmp_path):
    """🔴 INSTRUMENT VALIDATION, BOTH DIRECTIONS, BEFORE ANY VERDICT BELOW IS
    BELIEVED.

    NEGATIVE CONTROL — the harness can go RED. A scenario that calls `FAIL`
    must exit non-zero and say so; a harness whose failures were swallowed
    would report every assertion below as passing.

    POSITIVE CONTROL — the counts it reports can MOVE off zero, and they come
    from the real file. A reassuring `0 merges` is indistinguishable from a
    harness wired to nothing until a non-zero has been watched.
    """
    red, _ = _run_lua(tmp_path, 'FAIL("deliberate")')
    assert red.returncode != 0, (
        "a scenario that raised still exited 0 — this harness cannot report a "
        f"failure, so nothing it prints is evidence.\n{red.stdout}")
    assert "SCENARIO-FAILED: deliberate" in red.stderr, red.stderr

    kv = _ok_lua(tmp_path, "\n".join([
        'if type(NvimOcto) ~= "table" then FAIL("the file defined no NvimOcto") end',
        'KV("rows", #NvimOcto.legend_rows("pull_request"))',
        'CURRENT_BUFFER = PR_BUFFER(%s, %s)' % (
            FIXTURE_PR_NUMBER, json.dumps(FIXTURE_PR_REPO)),
        'ANSWERS = {"yes"}',
        'NvimOcto.confirm_and_merge()',
        'KV("merges", #RECORD.merges)',
    ]))
    assert int(kv["rows"][0]) > 30, kv
    # The positive control on the merge counter: it CAN reach 1. Every abort
    # assertion below reads this same counter, and a counter that could only
    # ever be 0 would make all of them vacuous.
    assert kv["merges"] == ["1"], kv


def test_the_legend_seam_FAILS_LOUDLY_when_apply_mappings_disappears(tmp_path):
    """🔴 REGRESSION COVERAGE FOR THE FRAGILITY THIS DESIGN CHOSE.

    The `?` binding rides `octo.utils.apply_mappings`, which is a PLUGIN's
    module function. If a future octo renames or moves it, a wrap that merely
    stopped applying would make `?` silently cease to exist — and a legend
    nobody can open looks exactly like a legend nobody pressed. So the file
    asserts the seam and RAISES.

    Driven, not read: the scenario deletes `apply_mappings` from the stub
    before loading the file, and the file must abort. The message must name
    the seam, or the operator gets an unexplained startup error.
    """
    proc, _ = _run_lua(
        tmp_path, 'KV("loaded", "yes")',
        setup='MODULES["octo.utils"].apply_mappings = nil')
    assert proc.returncode != 0, (
        "octo-init.lua loaded cleanly with `apply_mappings` absent. The wrap "
        "silently applied nothing, so `?` does not exist and nothing said so."
        f"\n{proc.stdout}")
    assert "apply_mappings" in proc.stderr, proc.stderr
    # NEGATIVE CONTROL on this test: with the seam present the same scenario
    # must load. Without this, "it raised" could be any error at all.
    ok_kv = _ok_lua(tmp_path, 'KV("loaded", "yes")')
    assert ok_kv["loaded"] == ["yes"], ok_kv


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_every_buffer_kind_gets_the_legend_key(tmp_path, kind: str):
    """🔴 THE MEASURED REASON THE SEAM IS `apply_mappings` AND NOT `FileType
    octo`, asserted per kind rather than argued.

    `filetype = "octo"` is set on the PR / issue / discussion buffers only.
    `review_diff` is applied to a buffer holding the DIFFED FILE, so its
    filetype is that file's language; `file_panel` and `submit_win` are plain
    scratch buffers. A FileType hook would bind `?` on three kinds and miss
    six — including all three review surfaces, which is where a reviewer
    spends the time. Parametrised so a kind that stops getting it names
    ITSELF.
    """
    kv = _ok_lua(tmp_path, "\n".join([
        'RECORD.keymaps = {}',
        'require("octo.utils").apply_mappings(%s, 12)' % json.dumps(kind),
        'KV("legend_maps", #KEYMAPS_FOR("?"))',
        'KV("delegated", #RECORD.applied)',
        'KV("delegated_kind", RECORD.applied[1] and RECORD.applied[1].kind)',
    ]))
    assert kv["legend_maps"] == ["1"], (
        f"a `{kind}` buffer got {kv['legend_maps']} `?` bindings, not one")
    # The wrap must DELEGATE, not replace: octo's own 131 bindings still have
    # to be applied. A wrap that forgot the original call would leave the
    # buffer with a legend and nothing to list.
    assert kv["delegated"] == ["1"] and kv["delegated_kind"] == [kind], kv


def test_the_legend_keys_are_RESOLVED_not_localleader_placeholders(tmp_path):
    """🔴 `<localleader>` IS A BACKSLASH, AND A LEGEND THAT SAID
    `<localleader>pd` WOULD BE CLOSE TO USELESS.

    Neither `mapleader` nor `maplocalleader` is set by this wrapper, so both
    are neovim's default `\\`. Asserted in three parts, because the first two
    alone are satisfiable by an empty row set: rows EXIST, none of them spells
    a leader placeholder, and at least one actually starts with the resolved
    character.
    """
    kv = _ok_lua(tmp_path, "\n".join([
        'local rows = NvimOcto.legend_rows("pull_request")',
        'KV("n", #rows)',
        'for _, r in ipairs(rows) do KV("lhs", r.lhs) end',
    ]))
    assert int(kv["n"][0]) > 30, kv
    placeholders = [k for k in kv["lhs"] if "leader" in k.lower()]
    assert not placeholders, (
        f"the legend prints unresolved leader placeholders: {placeholders}")
    assert any(k.startswith("\\") for k in kv["lhs"]), (
        "no row resolved to a backslash sequence, so the resolver may not be "
        f"running at all: {kv['lhs']}")
    # Non-leader key names stay in vim's own notation, which is how they are
    # typed and how every other vim document writes them.
    assert "<C-b>" in kv["lhs"], kv["lhs"]
    # The MOVING control on the resolver is its own test below — a resolver
    # that hardcoded a backslash would survive everything asserted here.


def test_the_resolver_FOLLOWS_the_configured_leaders_SEPARATELY(tmp_path):
    """🔴 THE MOVING CONTROL ON THE RESOLVER, and it moves the two leaders to
    DIFFERENT characters on purpose.

    The default makes `mapleader` and `maplocalleader` both `\\`, so a resolver
    that confused the two — or that returned a hardcoded backslash, or read the
    values once at load time — is indistinguishable from a correct one under
    the config as shipped. Moving them apart separates all three: the config's
    forty-odd `<localleader>` keys must follow one character, `approve_pr`'s
    lone `<leader>qa` must follow the other, and neither may keep the
    backslash. The two probe characters appear nowhere in `octo-init.lua`.
    """
    kv = _ok_lua(tmp_path, "\n".join([
        'vim.g.maplocalleader = ","',
        'vim.g.mapleader = ";"',
        'for _, r in ipairs(NvimOcto.legend_rows("pull_request")) do',
        '  KV(r.action, r.lhs)',
        'end',
    ]))
    assert kv["show_pr_diff"] == [",pd"], kv["show_pr_diff"]
    assert kv["approve_pr"] == [";qa"], kv["approve_pr"]
    assert kv["merge"] == [",pm"], kv["merge"]
    everything = [v for values in kv.values() for v in values]
    assert not any(k.startswith("\\") for k in everything), (
        f"rows still resolve to a backslash after both leaders moved: "
        f"{[k for k in everything if k.startswith(chr(92))]}")
    # NEGATIVE CONTROL on the probe itself: the untouched keys must NOT have
    # moved, or this is measuring a resolver that rewrites everything.
    assert kv["open_in_browser"] == ["<C-b>"], kv["open_in_browser"]
    assert kv["next_comment"] == ["]c"], kv["next_comment"]


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_the_legend_row_count_is_DERIVED_from_the_live_config(tmp_path, kind):
    """🔴 GENERATED, NOT CURATED — asserted as an equality against the config
    the editor was actually handed, per kind.

    The count in the title is what a reader trusts, so it is pinned to
    `config.values.mappings[kind]` plus the wrapper's own bindings for that
    kind. A hand-written legend would drift from this the first time a mapping
    changed, silently.
    """
    kv = _ok_lua(tmp_path, "\n".join([
        'local declared = 0',
        'for _ in pairs(CONF.values.mappings[%s]) do declared = declared + 1 end'
        % json.dumps(kind),
        'KV("declared", declared)',
        'KV("extra", #NvimOcto.extra_for(%s))' % json.dumps(kind),
        'KV("rows", #NvimOcto.legend_rows(%s))' % json.dumps(kind),
        'KV("title", NvimOcto.legend_lines(%s)[1])' % json.dumps(kind),
    ]))
    declared, extra, rows = (int(kv[k][0]) for k in ("declared", "extra", "rows"))
    assert rows == declared + extra, (
        f"{kind}: the legend shows {rows} rows for a config declaring "
        f"{declared} mappings plus {extra} wrapper bindings")
    assert f"{rows} bindings" in kv["title"][0], kv["title"]
    assert kind in kv["title"][0], kv["title"]


def test_the_legend_lists_only_what_is_actually_BOUND(tmp_path):
    """`octo.utils.apply_mappings` binds an entry only when the action exists
    in `octo.mappings`. A legend built from the raw config table would
    advertise a keystroke that is not bound — a row that reads as coverage and
    provides none, on screen. Driven by removing one action from the stub."""
    body = "\n".join([
        'local rows = NvimOcto.legend_rows("pull_request")',
        'KV("rows", #rows)',
        'for _, r in ipairs(rows) do KV("action", r.action) end',
    ])
    full = _ok_lua(tmp_path, body)
    holed = _ok_lua(tmp_path, body,
                    setup='OCTO_MAPPINGS_MISSING = "approve_pr"')
    assert "approve_pr" in full["action"], full["action"]
    assert "approve_pr" not in holed["action"], holed["action"]
    assert int(holed["rows"][0]) == int(full["rows"][0]) - 1, (full, holed)


def test_open_in_browser_was_ALREADY_bound_and_is_now_DISCOVERABLE(tmp_path):
    """🔴 THE SECOND OPERATOR ASK WAS NOT A MISSING BINDING, IT WAS A MISSING
    LEGEND. `open_in_browser = { lhs = "<C-b>" }` is declared on five of the
    nine tables and always has been; the operator could not find it. This pins
    that the legend now names it on every kind that declares it — and that no
    SECOND browser binding was added, which would have been the wrong fix.
    """
    kinds = [k for k in ALL_KINDS]
    body = ["local declaring = 0"]
    for kind in kinds:
        body += [
            'if CONF.values.mappings[%s].open_in_browser then' % json.dumps(kind),
            '  declaring = declaring + 1',
            '  local seen = 0',
            '  for _, r in ipairs(NvimOcto.legend_rows(%s)) do' % json.dumps(kind),
            '    if r.action == "open_in_browser" then',
            '      seen = seen + 1',
            '      KV("browser_row", %s .. "=" .. r.lhs)' % json.dumps(kind),
            '    end',
            '  end',
            '  if seen ~= 1 then FAIL(%s .. " has " .. seen .. " browser rows") end'
            % json.dumps(kind),
            'end',
        ]
    body.append('KV("declaring", declaring)')
    kv = _ok_lua(tmp_path, "\n".join(body))
    assert int(kv["declaring"][0]) == 5, (
        f"expected five tables to declare open_in_browser, got "
        f"{kv['declaring']}: {kv.get('browser_row')}")
    assert all(row.endswith("=<C-b>") for row in kv["browser_row"]), (
        f"the legend renders a browser key that is not `<C-b>`, which means a "
        f"second binding was added instead of making the first one "
        f"discoverable: {kv['browser_row']}")


# --------------------------------------------------------------------------- #
# THE MERGE CONFIRMATION — the one guard the structural section cannot make
# --------------------------------------------------------------------------- #

# 🔴 EVERY ANSWER THAT MUST ABORT. `y` is on this list deliberately: the prompt
# demands the word, and a wrapper that accepted a single keystroke would have
# reintroduced most of the hazard it exists to close. `<INTERRUPT>` models the
# `<C-c>` a real `input()` raises.
ABORTING_ANSWERS = ("no", "n", "y", "Y", "", "  ", "yes please", "nope",
                    "yesterday", "<INTERRUPT>")
CONFIRMING_ANSWERS = ("yes", "YES", "Yes", "  yes  ")


@pytest.mark.parametrize("answer", ABORTING_ANSWERS)
def test_every_path_to_a_merge_passes_through_the_confirmation(tmp_path, answer):
    """🔴 THE OPERATOR REQUIREMENT, ASSERTED BEHAVIOURALLY: an answer that is
    not an explicit `yes` must reach NO merge.

    Watched, not grepped. A test that looked for the word "confirm" in the
    source would pass against a comment; this one holds the buffer and the
    merge still and moves only the human's answer, so a confirmation that had
    been reduced to decoration fails here.
    """
    kv = _ok_lua(tmp_path, "\n".join([
        'CURRENT_BUFFER = PR_BUFFER(%s, %s)' % (
            FIXTURE_PR_NUMBER, json.dumps(FIXTURE_PR_REPO)),
        'ANSWERS = {%s}' % json.dumps(answer),
        'KV("returned", tostring(NvimOcto.confirm_and_merge()))',
        'KV("merges", #RECORD.merges)',
        'KV("prompts", #RECORD.prompts)',
        'for _, n in ipairs(RECORD.notify) do KV("notify", n.msg) end',
    ]))
    assert kv["merges"] == ["0"], (
        f"answering {answer!r} MERGED the pull request")
    assert kv["returned"] == ["false"], kv
    # It must have ASKED — an implementation that refused to merge by never
    # reaching the prompt would satisfy the counter above while being broken.
    assert kv["prompts"] == ["1"], kv
    # And it must SAY it aborted. A silent no-op on a key the legend advertises
    # reads as a broken keymap.
    assert any("ABORTED" in n for n in kv["notify"]), kv.get("notify")


@pytest.mark.parametrize("answer", CONFIRMING_ANSWERS)
def test_an_explicit_yes_DOES_merge(tmp_path, answer):
    """🔴 THE OTHER HALF, AND IT IS NOT OPTIONAL. Without it, a confirmation
    that aborted on EVERYTHING would pass every assertion above — a merge key
    that never merges is a broken feature that reads as a safe one."""
    kv = _ok_lua(tmp_path, "\n".join([
        'CURRENT_BUFFER = PR_BUFFER(%s, %s)' % (
            FIXTURE_PR_NUMBER, json.dumps(FIXTURE_PR_REPO)),
        'ANSWERS = {%s}' % json.dumps(answer),
        'KV("returned", tostring(NvimOcto.confirm_and_merge()))',
        'KV("merges", #RECORD.merges)',
        'for _, m in ipairs(RECORD.merges) do KV("method", m) end',
    ]))
    assert kv["merges"] == ["1"] and kv["returned"] == ["true"], kv
    assert kv["method"] == ["squash"], (
        "the merge was dispatched with a method other than the configured "
        f"one: {kv['method']}")


def test_the_merge_KEYMAP_itself_is_on_the_confirmed_path(tmp_path):
    """🔴 THE SEAM GUARD, AND THE ONE THAT MATTERS MOST.

    The tests above prove `confirm_and_merge` asks. They say NOTHING about what
    the keystroke is wired to — and a keymap wired straight to
    `octo.commands.merge_pr` would merge with no prompt while every one of them
    stayed green. That is this repo's "verified in isolation" shape exactly: two
    surfaces each tested, the defect living in the seam nobody owns.

    So this takes the callback THE WRAP REGISTERED for the merge key, out of
    the recorder, and invokes it — once with `no`, once with `yes`.
    """
    kv = _ok_lua(tmp_path, "\n".join([
        'RECORD.keymaps = {}',
        'require("octo.utils").apply_mappings("pull_request", 12)',
        # Find the wrapper's merge binding by its CALLBACK's effect, not by a
        # name: whatever lhs it was registered under, exactly one registered
        # callback must ask before merging.
        'local candidates = {}',
        'for _, m in ipairs(RECORD.keymaps) do',
        '  if m.lhs ~= "?" then candidates[#candidates + 1] = m end',
        'end',
        'KV("candidates", #candidates)',
        'if #candidates ~= 1 then FAIL("expected one non-legend binding") end',
        'local merge_map = candidates[1]',
        'KV("lhs", merge_map.lhs)',
        'KV("desc", merge_map.opts.desc)',
        'KV("buffer", tostring(merge_map.opts.buffer))',
        'CURRENT_BUFFER = PR_BUFFER(%s, %s)' % (
            FIXTURE_PR_NUMBER, json.dumps(FIXTURE_PR_REPO)),
        'ANSWERS = {"no"}',
        'merge_map.rhs()',
        'KV("merges_after_no", #RECORD.merges)',
        'KV("prompts_after_no", #RECORD.prompts)',
        'ANSWERS = {"yes"}',
        'merge_map.rhs()',
        'KV("merges_after_yes", #RECORD.merges)',
    ]))
    assert kv["merges_after_no"] == ["0"], (
        "pressing the merge key and answering `no` MERGED the pull request — "
        "the keymap is not routed through the confirmation")
    assert kv["prompts_after_no"] == ["1"], (
        "pressing the merge key asked nothing, so there is no confirmation on "
        "this path at all")
    assert kv["merges_after_yes"] == ["1"], kv
    # The binding is BUFFER-LOCAL, like every octo mapping — a global merge key
    # would be live in any buffer the operator opened.
    assert kv["buffer"] == ["12"], kv
    # And it is upstream's own merge lhs, so upstream documentation still
    # describes this buffer.
    assert kv["lhs"] == ["<localleader>pm"], kv


def test_the_merge_prompt_NAMES_the_pull_request_the_repo_and_the_method(tmp_path):
    """A confirmation that says "are you sure?" confirms nothing — the
    operator has several review buffers open. The prompt must identify what is
    about to be merged and how. Fixtures are pairwise distinct and appear
    nowhere in `octo-init.lua`, so a prompt hardcoding a literal cannot pass."""
    kv = _ok_lua(tmp_path, "\n".join([
        'CURRENT_BUFFER = PR_BUFFER(%s, %s)' % (
            FIXTURE_PR_NUMBER, json.dumps(FIXTURE_PR_REPO)),
        'ANSWERS = {"no"}',
        'NvimOcto.confirm_and_merge()',
        'KV("prompt", RECORD.prompts[1])',
    ]))
    prompt = kv["prompt"][0]
    for needed in (FIXTURE_PR_NUMBER, FIXTURE_PR_REPO, "squash", "yes"):
        assert needed in prompt, (
            f"the confirmation prompt does not name {needed!r}: {prompt!r}")


def test_the_merge_method_is_READ_from_the_config_not_restated(tmp_path):
    """🔴 A MOVING CONTROL, because `"squash"` is also the literal in the
    config — an assertion that only ever saw that value could not tell a live
    read from a hardcoded string. The scenario moves the live config to a
    method that appears NOWHERE in `octo-init.lua`, and both the prompt and the
    dispatched merge must follow it."""
    kv = _ok_lua(tmp_path, "\n".join([
        'CONF.values.default_merge_method = %s' % json.dumps(FIXTURE_OTHER_METHOD),
        'CURRENT_BUFFER = PR_BUFFER(%s, %s)' % (
            FIXTURE_PR_NUMBER, json.dumps(FIXTURE_PR_REPO)),
        'ANSWERS = {"yes"}',
        'NvimOcto.confirm_and_merge()',
        'KV("prompt", RECORD.prompts[1])',
        'KV("method", RECORD.merges[1])',
        'for _, r in ipairs(NvimOcto.legend_rows("pull_request")) do',
        '  if r.action == "merge" then KV("legend_desc", r.desc) end',
        'end',
    ]))
    assert kv["method"] == [FIXTURE_OTHER_METHOD], kv
    assert FIXTURE_OTHER_METHOD in kv["prompt"][0], kv["prompt"]
    assert "squash" not in kv["prompt"][0], kv["prompt"]
    # The legend describes the same live value, so the two cannot disagree.
    assert FIXTURE_OTHER_METHOD in kv["legend_desc"][0], kv["legend_desc"]


def test_a_non_pull_request_buffer_cannot_be_merged(tmp_path):
    """The key is bound on PR buffers, but `get_current_buffer` answers about
    whatever is focused. A merge attempt with no PR must abort BEFORE asking —
    a prompt naming nothing is worse than no prompt."""
    kv = _ok_lua(tmp_path, "\n".join([
        'CURRENT_BUFFER = nil',
        'ANSWERS = {"yes"}',
        'KV("returned", tostring(NvimOcto.confirm_and_merge()))',
        'KV("merges", #RECORD.merges)',
        'KV("prompts", #RECORD.prompts)',
    ]))
    assert kv["merges"] == ["0"] and kv["returned"] == ["false"], kv
    assert kv["prompts"] == ["0"], kv


def test_the_legend_footer_names_the_SAME_key_the_merge_was_bound_to(tmp_path):
    """🔴 A RELATIONSHIP, NOT A WORD. Asserting that the footer contains
    "confirm" would be walkable by a comment; this pins that the key the
    footer PRINTS is the resolved form of the key the wrap actually
    REGISTERED, so a legend cannot advertise a keystroke that was never
    installed or name the wrong one."""
    kv = _ok_lua(tmp_path, "\n".join([
        'RECORD.keymaps = {}',
        'require("octo.utils").apply_mappings("pull_request", 12)',
        'for _, m in ipairs(RECORD.keymaps) do',
        '  if m.lhs ~= "?" then KV("bound", NvimOcto.resolve_lhs(m.lhs)) end',
        'end',
        'for _, line in ipairs(NvimOcto.legend_lines("pull_request")) do',
        '  if line:sub(1, 6) == "MERGE:" then KV("footer", line) end',
        'end',
    ]))
    assert len(kv["bound"]) == 1, kv
    assert len(kv["footer"]) == 1, (
        f"expected exactly one MERGE: footer line, got {kv.get('footer')}")
    assert kv["bound"][0] in kv["footer"][0], (
        f"the footer advertises a key the wrap did not bind: "
        f"bound={kv['bound'][0]!r} footer={kv['footer'][0]!r}")


@pytest.mark.parametrize("kind", ["issue", "review_diff", "file_panel",
                                  "submit_win", "discussion", "repo",
                                  "release", "review_thread"])
def test_NO_merge_key_is_bound_outside_a_pull_request_buffer(tmp_path, kind):
    """The merge binding is scoped to `pull_request` because octo's merge
    resolves the CURRENT buffer — a merge key on the file panel could not act
    on anything, and a key that does nothing is a key the operator learns to
    distrust. Asserted per kind so a widening names the kind it widened to."""
    kv = _ok_lua(tmp_path, "\n".join([
        'RECORD.keymaps = {}',
        'require("octo.utils").apply_mappings(%s, 12)' % json.dumps(kind),
        'for _, m in ipairs(RECORD.keymaps) do KV("lhs", m.lhs) end',
    ]))
    assert kv["lhs"] == ["?"], (
        f"a `{kind}` buffer got bindings beyond the legend: {kv['lhs']}")


def test_the_legend_window_is_CAPPED_to_the_editor(tmp_path):
    """🔴 MEASURED AT TWO POINTS, because a size claim that holds at one is a
    claim about that one. A `pull_request` legend is ~48 lines; on a tall
    terminal it must show in full, and on a short one it must be clamped
    rather than opened taller than the screen.

    The float scrolls when clamped — the rows are not lost, the window just
    cannot overflow."""
    body = "\n".join([
        'vim.o.lines = LINES',
        'vim.o.columns = COLUMNS',
        'NvimOcto.show_legend("pull_request")',
        'local cfg = RECORD.wins[1]',
        'KV("h", cfg.height)',
        'KV("w", cfg.width)',
        'KV("row", cfg.row)',
        'KV("col", cfg.col)',
        'KV("content", #NvimOcto.legend_lines("pull_request"))',
    ])
    short = _ok_lua(tmp_path, body.replace("LINES", "24").replace("COLUMNS", "60"))
    tall = _ok_lua(tmp_path, body.replace("LINES", "120").replace("COLUMNS", "200"))

    content = int(tall["content"][0])
    assert content > 40, (
        f"the pull_request legend is only {content} lines — this test is no "
        f"longer measuring a legend that could overflow")

    assert int(short["h"][0]) <= 24 - 6, short
    assert int(short["w"][0]) <= 60 - 4, short
    assert int(short["row"][0]) >= 0 and int(short["col"][0]) >= 0, short
    # On a tall terminal nothing is clipped…
    assert int(tall["h"][0]) == content, tall
    # …and the two measurements actually DIFFER, so the cap is doing work
    # rather than the content happening to fit at both points.
    assert int(short["h"][0]) < int(tall["h"][0]), (short, tall)


def test_the_legend_closes_on_q_and_Esc(tmp_path):
    """The float takes focus, so it must be dismissable without knowing which
    buffer it is. Asserted as the set of close keys bound INSIDE the legend
    buffer, plus that invoking one actually closes the window."""
    kv = _ok_lua(tmp_path, "\n".join([
        'RECORD.keymaps = {}',
        'local win, buf = NvimOcto.show_legend("pull_request")',
        'KV("buf", tostring(buf))',
        'for _, m in ipairs(RECORD.keymaps) do',
        '  if m.opts.buffer == buf then KV("close_key", m.lhs) end',
        'end',
        'for _, m in ipairs(RECORD.keymaps) do',
        '  if m.lhs == "q" and m.opts.buffer == buf then m.rhs() end',
        'end',
        'KV("closed", tostring(RECORD.closed))',
    ]))
    assert sorted(kv["close_key"]) == sorted(["q", "<Esc>", "?"]), kv
    assert kv["closed"] == ["true"], kv


# --------------------------------------------------------------------------- #
# THE WRAPPER — driven, not read
# --------------------------------------------------------------------------- #
def _run_wrapper(tmp_path: Path, *args: str):
    """Run the wrapper's shell text with `nvim` STUBBED.

    🔴 THE STUB IS WHAT MAKES THIS SAFE AND WHAT MAKES IT A MEASUREMENT. The
    real binary would take over a terminal and reach GitHub; the stub records
    its argv into a file, so the ex-command the wrapper composes is observable.
    `bash -euo pipefail` reproduces the preamble `writeShellApplication` adds —
    verified against the built derivation, whose first four lines are the
    shebang plus exactly those three `set -o` lines.

    🔴 THE EXISTENCE CHECK IS NOT DEFENSIVE — IT CLOSES A MEASURED VACUOUS
    GREEN. Run against a tree where `nvim-octo.sh` does not exist (the base
    commit of the branch that added it), `bash` exits non-zero with an empty
    argv log, which satisfied every "rejected, and nvim was never reached"
    assertion below exactly as a correct wrapper does: 18 of them passed on a
    tree with NO WRAPPER AT ALL. That is why the callers now assert the
    wrapper's OWN exit code and OWN message rather than "non-zero".
    """
    assert WRAPPER_SH.exists(), (
        f"{WRAPPER_SH} is missing — was it `git add`ed? Without this check a "
        f"missing wrapper makes every rejection test below pass vacuously")
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "nvim-argv"
    # 🔴 `write_exec`, NOT `write_text` + a hand-written shebang. It owns the
    # shebang precisely so a call site cannot reintroduce `#!/usr/bin/env`,
    # which does not exist in the nix build sandbox — the flake's
    # `patchShebangs src/scripts` is there for the same reason.
    #
    # This is a TWO-TIER blind spot, caught by the repo-wide guard
    # `test_no_test_writes_a_usr_bin_env_shebang_at_runtime` running in CI and
    # NOT by any dev-host run of this file: `/usr/bin/env` resolves on the
    # workbench, so the stub executed and every test here was green while the
    # sandbox tier could not have run them at all.
    # 🔴 THE STUB RECORDS THE ENVIRONMENT AS WELL AS argv, because the wrapper's
    # hermeticity is carried by an EXPORTED VARIABLE and not by an argument. A
    # stub that only logs `"$@"` is structurally blind to it: `NVIM_APPNAME`
    # could be deleted from the wrapper and every assertion here would stay
    # green. `_appname` below is what makes that observable.
    envlog = tmp_path / "nvim-env"
    write_exec(bindir / "nvim",
               f'printf "%s\\n" "$@" > {log}\n'
               f'printf "%s" "${{NVIM_APPNAME-<UNSET>}}" > {envlog}\n'
               f'exit 0\n')
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")
    # 🔴 A DELIBERATELY WRONG INHERITED VALUE, NOT AN ABSENT ONE. If the harness
    # left `NVIM_APPNAME` unset, a wrapper that merely failed to unset something
    # would be indistinguishable from one that exports the right name. Seeding a
    # value the wrapper MUST override means the assertion can only pass if the
    # wrapper actually wrote it — and this string cannot be confused with the
    # expected one or with `<UNSET>`.
    env["NVIM_APPNAME"] = "inherited-from-the-display-manager"
    assert _BASH, "bash is not on PATH — this harness cannot run the wrapper"
    proc = subprocess.run(
        [_BASH, "-euo", "pipefail", str(WRAPPER_SH), *args],
        capture_output=True, text=True, env=env, timeout=60)
    argv = log.read_text().splitlines() if log.exists() else []
    appname = envlog.read_text() if envlog.exists() else None
    return proc, argv, appname


def test_a_good_invocation_composes_the_NUMBER_FIRST_ex_command(tmp_path):
    """🔴 THE ORDER IS THE CONTRACT, AND IT IS INVERTED FROM THE WRAPPER'S OWN
    ARGUMENTS. The wrapper takes `<owner/repo> <number>` because that is the
    order `mention-open.py` has them in, and it must emit `Octo <number>
    <owner/repo>` because octo's `M.octo(object, action, …)` does
    `tonumber(object)` and treats the SECOND word as the repository. Get it
    backwards and octo takes an entirely different branch.

    The fixtures are pairwise distinct and distinct from every constant the
    assertion names, so a mutant hardcoding a literal cannot survive.
    """
    proc, argv, _appname = _run_wrapper(tmp_path, "gardenersguild/trowelcast", "1559")
    assert proc.returncode == 0, proc.stderr
    assert argv == ["-c", "Octo 1559 gardenersguild/trowelcast"], argv


def test_a_number_is_passed_rather_than_a_url(tmp_path):
    """A URL would assert the reference KIND, which the handler cannot know —
    `mention-open.py` builds `/pull/{id}` for every mention and lets github.com
    redirect.

    ⚠ THE POSITIVE ASSERTION COMES FIRST, AND IT HAS TO. An earlier version of
    this test asserted only that `http` and `/pull/` were ABSENT from the argv,
    which an EMPTY argv satisfies — and an empty argv is what a missing wrapper
    produces. Pin what was passed, then observe what it is not."""
    proc, argv, _appname = _run_wrapper(tmp_path, "rivalorg/spadeworks", "42")
    assert proc.returncode == 0, proc.stderr
    assert argv == ["-c", "Octo 42 rivalorg/spadeworks"], argv
    assert "http" not in " ".join(argv), argv
    assert "/pull/" not in " ".join(argv), argv


# The wrapper's own exit codes. Asserted by VALUE rather than as "non-zero",
# because non-zero is also what a missing file, a syntax error and an unbound
# variable produce — see `_run_wrapper`'s note on the 18 tests that passed
# against a tree with no wrapper at all.
RC_USAGE = 64
RC_BAD_REPO = 65
RC_BAD_NUM = 66


@pytest.mark.parametrize("repo", [
    "notarepo",                     # no slash at all
    "too/many/slashes",
    "/leadingslash",
    "trailing/",
    "../../etc/passwd",             # traversal, and it HAS a slash
    "owner/repo;rm -rf /",          # shell metacharacters
    "owner/repo with space",
    "owner/$(whoami)",
    "",
])
def test_a_bad_repository_is_REJECTED_and_nvim_is_never_reached(tmp_path, repo):
    """Three halves, and the first is what makes the other two mean anything:
    THIS guard's own exit code, THIS guard's own message, and no editor. An exit
    code alone would be satisfied by a wrapper that launched first and
    complained after — or by no wrapper at all."""
    proc, argv, _appname = _run_wrapper(tmp_path, repo, "1559")
    assert proc.returncode == RC_BAD_REPO, (proc.returncode, proc.stderr)
    assert "not an owner/repo" in proc.stderr, proc.stderr
    assert argv == [], argv


@pytest.mark.parametrize("num", ["", "abc", "12a", "-1", "1.5", "1 2",
                                 "$(id)", "42;ls"])
def test_a_bad_number_is_REJECTED_and_nvim_is_never_reached(tmp_path, num):
    """🔴 A DIFFERENT EXIT CODE FROM THE REPOSITORY GUARD, ON PURPOSE. A mutation
    test that broke the repository check and watched "a test fail" would
    otherwise be green for the wrong reason if the NUMBER guard was the one that
    fired. Distinct codes plus distinct messages make each guard's kill
    attributable to itself."""
    proc, argv, _appname = _run_wrapper(tmp_path, "gardenersguild/trowelcast", num)
    assert proc.returncode == RC_BAD_NUM, (proc.returncode, proc.stderr)
    assert "not a reference number" in proc.stderr, proc.stderr
    assert argv == [], argv


@pytest.mark.parametrize("args", [(), ("only-one",),
                                  ("a/b", "1", "extra")])
def test_the_wrong_number_of_arguments_is_REJECTED(tmp_path, args):
    """🔴 `set -u` MAKES THE ARITY CHECK LOAD-BEARING RATHER THAN POLITE. Without
    it, `"$1"` on a zero-argument call is an unbound-variable error whose message
    says nothing about usage — and with `set -e` it is still non-zero, so a test
    asserting only the exit code would pass while the operator got
    `nvim-octo.sh: line 23: $1: unbound variable`."""
    proc, argv, _appname = _run_wrapper(tmp_path, *args)
    assert proc.returncode == RC_USAGE, (proc.returncode, proc.stderr)
    assert argv == []
    assert "usage:" in proc.stderr, proc.stderr


@pytest.mark.parametrize("repo", [
    "gardenersguild/trowelcast",
    "civitai/talos-infra",
    "ZacxDev/naida-ai",
    "innovation-upstream/devrc",
    "a-b.c/d_e.f",
])
def test_real_shaped_repository_names_are_ACCEPTED(tmp_path, repo):
    """🔴 THE NEGATIVE CONTROL ON THE VALIDATOR, built from REALISTIC data. A
    rejector that refused everything would pass every rejection test above —
    which is the instrument-validation trap RULES.md names. Dots, dashes,
    underscores and mixed case all occur in these owners' real repository
    names."""
    proc, argv, _appname = _run_wrapper(tmp_path, repo, "7")
    assert proc.returncode == 0, proc.stderr
    assert argv == ["-c", f"Octo 7 {repo}"], argv


def test_the_wrapper_text_carries_no_shebang_and_no_set_line():
    """`writeShellApplication` prepends both. A second copy here would be
    harmless in production and would make `_run_wrapper`'s `bash -euo pipefail`
    a claim about a file that sets its own options — i.e. the harness would stop
    reproducing the deployed preamble."""
    text = WRAPPER_SH.read_text(encoding="utf-8")
    assert not text.startswith("#!"), text.splitlines()[:1]
    assert not re.search(r"^\s*set\s+-", text, re.M), text


def test_the_wrapper_ISOLATES_neovims_app_namespace(tmp_path):
    """🔴 REGRESSION COVERAGE for a MEASURED breakage, not a hypothetical.

    Selecting a row in the mention picker opened a review buffer that errored with
    `module 'lyaml' not found`. The trace named
    `~/.local/share/nvim/site/pack/packer/start/qdr.nvim/lua/qdr-nvim/qdr.lua:2` —
    a PACKER-INSTALLED PLUGIN OF THE OPERATOR'S, loaded into this wrapper because
    neovim's default `packpath` includes that site directory whatever `-u` says.
    Their daily editor supplies the rock, this derivation does not.

    🔴 THE FIX IS NOT THE MISSING ROCK, AND THAT IS WHY THIS GUARD PINS THE
    NAMESPACE RATHER THAN A DEPENDENCY. Adding `lyaml` would make that one plugin
    load successfully inside a review TUI with no business running it, and leave
    every other plugin in that directory able to break the review surface on the
    next unrelated editor change. `default.nix` claims the plugin set "cannot be
    changed by an edit to an editor config, and it cannot be broken by one
    either"; `NVIM_APPNAME` is what makes that claim true.

    ⚠ WHAT THIS DOES AND DOES NOT ESTABLISH. It proves the wrapper EXPORTS the
    variable — observable because the stub records its own environment. It does
    NOT prove neovim honours it; that was measured by hand against the built
    derivation (headless load silent, `require("octo")` true, `exists(":Octo")`
    == 2) and is recorded in the PR, not asserted here, because asserting it
    needs a nix build and a real editor.

    The harness seeds a WRONG inherited value, so this can only pass if the
    wrapper actually wrote its own — an absent variable would be satisfied by a
    wrapper that merely failed to unset something.
    """
    proc, argv, appname = _run_wrapper(tmp_path, "gardenersguild/trowelcast", "1559")
    assert proc.returncode == 0, proc.stderr
    assert argv, "nvim was never reached, so the environment proves nothing"
    assert appname == "nvim-octo", (
        "the wrapper handed neovim NVIM_APPNAME=%r. It must export "
        "'nvim-octo' so neovim reads ~/.local/share/nvim-octo/site instead of "
        "the operator's ~/.local/share/nvim/site — otherwise their packer "
        "plugins load into the review TUI and break it (measured: qdr.nvim "
        "requiring 'lyaml', which this derivation does not ship). %s"
        % (appname,
           "The harness seeded a deliberately wrong value, so this is what the "
           "wrapper wrote." if appname != "<UNSET>" else
           "'<UNSET>' means the wrapper unset it rather than setting it."))
