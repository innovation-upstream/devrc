-- octo.nvim configuration for `nvim-octo` — the review TUI a clicked GitHub
-- mention opens in. Sourced by the generated init.vim (`luafile`), so it runs
-- BEFORE the `-c "Octo <N> <owner/repo>"` the wrapper appends.
--
-- 🔴 THIS FILE IS PARSED **AND EXECUTED** BY A TEST.
-- `scripts/tests/test_nvim_octo.py` reads the `mappings.pull_request` table
-- structurally (asserting that none of octo's own unconfirmed merge actions is
-- declared in it, plus `default_merge_method`, `default_delete_branch` and
-- `picker`), and then RUNS this whole file under `luajit` with a stubbed `vim`
-- and a stubbed `require`, driving `NvimOcto` below. Reword the comments
-- freely; do not move those settings into a computed expression, because a
-- value the reader cannot see is a value nobody is checking.
--
-- ⚠ `--` LINE COMMENTS ONLY. The test's comment stripper does not understand
-- Lua long-bracket comments (`--[[ … ]]`) and would hand every structural
-- reader the body of one as if it were configuration.
--
-- ⚠ LuaJIT 5.1 SYNTAX ONLY, for the same reason: the hermetic tier runs this
-- file through `luajit`, not through neovim.

-- --------------------------------------------------------------------------
-- MERGE SAFETY — why this config re-declares ~45 mappings by hand
-- --------------------------------------------------------------------------
-- 🔴 octo.nvim HAS NO MERGE CONFIRMATION. `commands.lua`'s `pr merge` calls
-- `gh.pr.merge(opts)` with no prompt of any kind, and upstream binds it to FOUR
-- keystrokes in a PR buffer: `<localleader>pm`, `<localleader>psm`,
-- `<localleader>prm` and `<localleader>pk` (merge the whole stack). A mistyped
-- localleader sequence in a buffer opened by CLICKING A LINK would merge a pull
-- request. That is the hazard this file exists to close.
--
-- `mappings_disable_default = true` clears every default mapping table before
-- the user tables below are merged in (octo `config.lua`'s M.setup), so what
-- is declared here is the COMPLETE set of OCTO keymaps, and none of them
-- merges.
--
-- 🔴 THERE IS NOW A MERGE KEYSTROKE AGAIN, AND IT IS NOT ONE OF OCTO'S. This
-- reverses the original decision that merging must require typing
-- `:Octo pr merge`. The operator asked for a keymap; the stated mitigation in
-- the original decision was "a confirmation step on destructive verbs, not a
-- narrower scope", and what actually shipped was the narrower scope. So the
-- keystroke is back WITH the confirmation that was supposed to accompany it.
--
-- ⚠ THE DISTINCTION IS LOAD-BEARING, NOT BOOKKEEPING. Re-declaring `merge_pr`
-- in the table below would bind OCTO's action, which calls `gh.pr.merge(opts)`
-- with no prompt of any kind. The keymap installed at the bottom of this file
-- is OURS: it resolves the PR, ASKS, and only then calls the same
-- `octo.commands.merge_pr` octo would have called. `mappings.pull_request`
-- therefore still declares no merge action, and the test still asserts that.
--
-- 🔴 `pr_options` IS DELIBERATELY NOT RE-DECLARED, AND THAT IS NOT AN
-- OVERSIGHT. Upstream binds it to `<CR>` — a single keystroke — and its menu
-- (octo `mappings.lua`) offers "Merge PR", "Squash and Merge PR" and "Delete
-- Branch" alongside the read-only entries. Re-declaring it would have put merge
-- back one `<CR>` away while every merge KEYMAP was gone, which is the shape of
-- a guard that reads as coverage and provides none. Everything useful that menu
-- offered has its own direct mapping below (`list_commits`, `list_changed_
-- files`, `show_pr_diff`, `toggle_checks`, `review_start`, `approve_pr`) or is
-- one typed `:Octo pr …` away.
--
-- ⚠ `issue_options` IS re-declared: its menu carries no merge and no branch
-- deletion. The asymmetry is the point.
--
-- 🔴 THE PICKER'S `<C-r>` MERGE IS A SECOND, SEPARATE PATH, AND
-- `mappings_disable_default` DOES NOT TOUCH IT. `picker_config.mappings` lives
-- outside the `mappings` table that flag clears, so upstream's
-- `merge_pr = { lhs = "<C-r>" }` survives it. What closes it is `picker =
-- "fzf-lua"`: MEASURED against octo 2026-08-28, the whole
-- `lua/octo/pickers/fzf-lua/` tree contains no occurrence of "merge" at all —
-- only the telescope provider wires `picker_config.mappings.merge_pr.lhs` to a
-- merge action. So the picker choice below is a SAFETY setting, not a taste
-- one, and the test asserts it for that reason.

-- --------------------------------------------------------------------------
-- EDITOR OPTIONS FIRST — and the ORDER IS LOAD-BEARING, not tidiness.
-- --------------------------------------------------------------------------
-- 🔴 `require("octo").setup()` CAN THROW, AND IT THROWS FOR A REASON THIS
-- WRAPPER CAN PLAUSIBLY HIT. MEASURED against octo 2026-08-28 with `gh` absent
-- from PATH: octo's own guard is `if not vim.fn.executable(cfg.gh_cmd) then`,
-- and `vim.fn.executable()` returns the NUMBER 0 — which is TRUTHY in Lua. So
-- `not 0` is false, the guard never fires, and execution falls through to
-- `gh.setup()`, where plenary's `Job:new` raises "command must be executable".
-- Upstream bug; not ours to fix from here, but ours to survive.
--
-- With the setup call last, that traceback aborted the rest of this file: no
-- colorscheme, no options — a window that looks broken in a second, unrelated
-- way on top of the real failure. Everything that does not depend on octo is
-- therefore done BEFORE the call, and the call itself is wrapped.
vim.o.termguicolors = true
vim.o.background = "dark"
vim.o.laststatus = 2
vim.o.number = true
vim.o.signcolumn = "yes"

-- Gruvbox, to match every other terminal this operator looks at (the alacritty
-- palette in `nix/programs/alacritty/default.nix` is the same theme).
-- `pcall` because a colorscheme failure must not be able to cost the review.
pcall(vim.cmd.colorscheme, "gruvbox")

local ok, err = pcall(function()
  require("octo").setup({
  -- See the picker note above: this is load-bearing, not preference.
  picker = "fzf-lua",

  -- 🔴 SQUASH, because upstream's default is "merge" (a merge commit) and this
  -- operator's repositories squash. Left at the default, the first merge made
  -- from this TUI would produce the wrong commit shape on a repo whose last
  -- five merges were all squashes.
  default_merge_method = "squash",

  -- 🔴 FALSE — which is also upstream's default, and it is kept EXPLICIT
  -- because it is load-bearing here rather than incidental. These repos set
  -- `delete_branch_on_merge`, and deleting a STACKED PARENT's branch makes
  -- GitHub auto-close the child PR and then REFUSE to reopen it: the branch is
  -- restorable, the PR object is not, and the child's review thread is lost.
  default_delete_branch = false,

  -- Reviewing needs no local checkout: octo only offers to check one out when
  -- this is true (`reviews/init.lua`). A review opened from a clicked link must
  -- never touch a working tree — other sessions share these checkouts.
  use_local_fs = false,

  mappings_disable_default = true,

  mappings = {
    -- Copied from octo 2026-08-28's own defaults, MINUS the seven merge
    -- entries (merge_pr, squash_and_merge_pr, rebase_and_merge_pr,
    -- merge_pr_stack, merge_pr_queue, squash_and_merge_queue,
    -- rebase_and_merge_queue) and minus pr_options. Same left-hand sides, so
    -- upstream documentation still describes this buffer.
    pull_request = {
      checkout_pr = { lhs = "<localleader>po", desc = "checkout PR" },
      list_commits = { lhs = "<localleader>pc", desc = "list PR commits" },
      list_changed_files = { lhs = "<localleader>pf", desc = "list PR changed files" },
      show_pr_diff = { lhs = "<localleader>pd", desc = "show PR diff" },
      add_reviewer = { lhs = "<localleader>va", desc = "add reviewer" },
      remove_reviewer = { lhs = "<localleader>vd", desc = "remove reviewer request" },
      close_issue = { lhs = "<localleader>ic", desc = "close PR" },
      reopen_issue = { lhs = "<localleader>io", desc = "reopen PR" },
      list_issues = { lhs = "<localleader>il", desc = "list open issues on same repo" },
      reload = { lhs = "<C-r>", desc = "reload PR" },
      approve_pr = { lhs = "<leader>qa", desc = "approve PR" },
      open_in_browser = { lhs = "<C-b>", desc = "open PR in browser" },
      copy_url = { lhs = "<C-y>", desc = "copy url to system clipboard" },
      copy_sha = { lhs = "<C-e>", desc = "copy commit SHA to system clipboard" },
      goto_file = { lhs = "gf", desc = "go to file" },
      stack_up = { lhs = "]s", desc = "open the next PR up the stack" },
      stack_down = { lhs = "[s", desc = "open the next PR down the stack" },
      goto_check = { lhs = "<localleader>gc", desc = "open the CI check under the cursor" },
      toggle_checks = { lhs = "<localleader>tc", desc = "fold or unfold the CI checks list" },
      add_assignee = { lhs = "<localleader>aa", desc = "add assignee" },
      remove_assignee = { lhs = "<localleader>ad", desc = "remove assignee" },
      create_label = { lhs = "<localleader>lc", desc = "create label" },
      add_label = { lhs = "<localleader>la", desc = "add label" },
      remove_label = { lhs = "<localleader>ld", desc = "remove label" },
      goto_issue = { lhs = "<localleader>gi", desc = "navigate to a local repo issue" },
      add_comment = { lhs = "<localleader>ca", desc = "add comment" },
      add_reply = { lhs = "<localleader>cr", desc = "add reply" },
      delete_comment = { lhs = "<localleader>cd", desc = "delete comment" },
      comment_edits = { lhs = "<localleader>ce", desc = "show comment edit history" },
      reference_in_new_issue = { lhs = "<localleader>ri", desc = "reference comment in new issue" },
      next_comment = { lhs = "]c", desc = "go to next comment" },
      prev_comment = { lhs = "[c", desc = "go to previous comment" },
      react_hooray = { lhs = "<localleader>rp", desc = "add/remove hooray reaction" },
      react_heart = { lhs = "<localleader>rh", desc = "add/remove heart reaction" },
      react_eyes = { lhs = "<localleader>re", desc = "add/remove eyes reaction" },
      react_thumbs_up = { lhs = "<localleader>r+", desc = "add/remove thumbs-up reaction" },
      react_thumbs_down = { lhs = "<localleader>r-", desc = "add/remove thumbs-down reaction" },
      react_rocket = { lhs = "<localleader>rr", desc = "add/remove rocket reaction" },
      react_laugh = { lhs = "<localleader>rl", desc = "add/remove laugh reaction" },
      react_confused = { lhs = "<localleader>rc", desc = "add/remove confused reaction" },
      review_start = { lhs = "<localleader>vs", desc = "start a review for the current PR" },
      review_resume = { lhs = "<localleader>vr", desc = "resume a pending review for the current PR" },
      resolve_thread = { lhs = "<localleader>rt", desc = "resolve PR thread" },
      unresolve_thread = { lhs = "<localleader>rT", desc = "unresolve PR thread" },
    },

    issue = {
      issue_options = { lhs = "<CR>", desc = "show issue options" },
      close_issue = { lhs = "<localleader>ic", desc = "close issue" },
      reopen_issue = { lhs = "<localleader>io", desc = "reopen issue" },
      list_issues = { lhs = "<localleader>il", desc = "list open issues on same repo" },
      reload = { lhs = "<C-r>", desc = "reload issue" },
      open_in_browser = { lhs = "<C-b>", desc = "open issue in browser" },
      copy_url = { lhs = "<C-y>", desc = "copy url to system clipboard" },
      add_assignee = { lhs = "<localleader>aa", desc = "add assignee" },
      remove_assignee = { lhs = "<localleader>ad", desc = "remove assignee" },
      create_label = { lhs = "<localleader>lc", desc = "create label" },
      add_label = { lhs = "<localleader>la", desc = "add label" },
      remove_label = { lhs = "<localleader>ld", desc = "remove label" },
      goto_issue = { lhs = "<localleader>gi", desc = "navigate to a local repo issue" },
      add_comment = { lhs = "<localleader>ca", desc = "add comment" },
      add_reply = { lhs = "<localleader>cr", desc = "add reply" },
      delete_comment = { lhs = "<localleader>cd", desc = "delete comment" },
      comment_edits = { lhs = "<localleader>ce", desc = "show comment edit history" },
      reference_in_new_issue = { lhs = "<localleader>ri", desc = "reference comment in new issue" },
      next_comment = { lhs = "]c", desc = "go to next comment" },
      prev_comment = { lhs = "[c", desc = "go to previous comment" },
    },

    review_thread = {
      goto_issue = { lhs = "<localleader>gi", desc = "navigate to a local repo issue" },
      add_comment = { lhs = "<localleader>ca", desc = "add comment" },
      add_reply = { lhs = "<localleader>cr", desc = "add reply" },
      add_suggestion = { lhs = "<localleader>sa", desc = "add suggestion" },
      delete_comment = { lhs = "<localleader>cd", desc = "delete comment" },
      comment_edits = { lhs = "<localleader>ce", desc = "show comment edit history" },
      next_comment = { lhs = "]c", desc = "go to next comment" },
      prev_comment = { lhs = "[c", desc = "go to previous comment" },
      select_next_entry = { lhs = "]q", desc = "move to next changed file" },
      select_prev_entry = { lhs = "[q", desc = "move to previous changed file" },
      select_first_entry = { lhs = "[Q", desc = "move to first changed file" },
      select_last_entry = { lhs = "]Q", desc = "move to last changed file" },
      select_next_unviewed_entry = { lhs = "]u", desc = "move to next unviewed file" },
      select_prev_unviewed_entry = { lhs = "[u", desc = "move to previous unviewed file" },
      close_review_tab = { lhs = "<C-c>", desc = "close review tab" },
      resolve_thread = { lhs = "<localleader>rt", desc = "resolve PR thread" },
      unresolve_thread = { lhs = "<localleader>rT", desc = "unresolve PR thread" },
    },

    -- The review VERDICT window. `<C-r>` is "request changes" here, in a
    -- buffer that exists only while a review is being submitted — it is not
    -- the PR buffer's `<C-r>`, which reloads.
    submit_win = {
      approve_review = { lhs = "<C-a>", desc = "approve review", mode = { "n" } },
      comment_review = { lhs = "<C-m>", desc = "comment review", mode = { "n" } },
      request_changes = { lhs = "<C-r>", desc = "request changes review", mode = { "n" } },
      close_review_tab = { lhs = "<C-c>", desc = "close review tab", mode = { "n" } },
    },

    review_diff = {
      submit_review = { lhs = "<localleader>vs", desc = "submit review" },
      discard_review = { lhs = "<localleader>vd", desc = "discard review" },
      add_review_comment = { lhs = "<localleader>ca", desc = "add a new review comment", mode = { "n", "x" } },
      add_review_suggestion = { lhs = "<localleader>sa", desc = "add a new review suggestion", mode = { "n", "x" } },
      focus_files = { lhs = "<localleader>e", desc = "move focus to changed file panel" },
      toggle_files = { lhs = "<localleader>b", desc = "hide/show changed files panel" },
      next_thread = { lhs = "]t", desc = "move to next thread" },
      prev_thread = { lhs = "[t", desc = "move to previous thread" },
      select_next_entry = { lhs = "]q", desc = "move to next changed file" },
      select_prev_entry = { lhs = "[q", desc = "move to previous changed file" },
      select_first_entry = { lhs = "[Q", desc = "move to first changed file" },
      select_last_entry = { lhs = "]Q", desc = "move to last changed file" },
      select_next_unviewed_entry = { lhs = "]u", desc = "move to next unviewed file" },
      select_prev_unviewed_entry = { lhs = "[u", desc = "move to previous unviewed file" },
      close_review_tab = { lhs = "<C-c>", desc = "close review tab" },
      toggle_viewed = { lhs = "<localleader><space>", desc = "toggle viewer viewed state" },
      goto_file = { lhs = "gf", desc = "go to file" },
      copy_sha = { lhs = "<C-e>", desc = "copy commit SHA to system clipboard" },
      review_commits = { lhs = "<localleader>C", desc = "review PR commits" },
    },

    file_panel = {
      submit_review = { lhs = "<localleader>vs", desc = "submit review" },
      discard_review = { lhs = "<localleader>vd", desc = "discard review" },
      next_entry = { lhs = "j", desc = "move to next changed file" },
      prev_entry = { lhs = "k", desc = "move to previous changed file" },
      select_entry = { lhs = "<cr>", desc = "show selected changed file diffs" },
      refresh_files = { lhs = "R", desc = "refresh changed files panel" },
      focus_files = { lhs = "<localleader>e", desc = "move focus to changed file panel" },
      toggle_files = { lhs = "<localleader>b", desc = "hide/show changed files panel" },
      select_next_entry = { lhs = "]q", desc = "move to next changed file" },
      select_prev_entry = { lhs = "[q", desc = "move to previous changed file" },
      select_first_entry = { lhs = "[Q", desc = "move to first changed file" },
      select_last_entry = { lhs = "]Q", desc = "move to last changed file" },
      select_next_unviewed_entry = { lhs = "]u", desc = "move to next unviewed file" },
      select_prev_unviewed_entry = { lhs = "[u", desc = "move to previous unviewed file" },
      close_review_tab = { lhs = "<C-c>", desc = "close review tab" },
      toggle_viewed = { lhs = "<localleader><space>", desc = "toggle viewer viewed state" },
      review_commits = { lhs = "<localleader>C", desc = "review PR commits" },
    },

    -- `repo_options` is dropped for the same reason `pr_options` is: its `<CR>`
    -- menu mutates (Star / Unstar / Change Subscription / Create Issue). Not a
    -- merge hazard, but not something a click should be one keypress from
    -- either, and the entries are all one typed `:Octo repo …` away.
    repo = {
      open_in_browser = { lhs = "<C-b>", desc = "open repo in browser" },
    },

    release = {
      open_in_browser = { lhs = "<C-b>", desc = "open release in browser" },
    },

    discussion = {
      open_in_browser = { lhs = "<C-b>", desc = "open discussion in browser" },
      copy_url = { lhs = "<C-y>", desc = "copy url to system clipboard" },
      add_comment = { lhs = "<localleader>ca", desc = "add comment" },
      add_reply = { lhs = "<localleader>cr", desc = "add reply" },
      delete_comment = { lhs = "<localleader>cd", desc = "delete comment" },
      comment_edits = { lhs = "<localleader>ce", desc = "show comment edit history" },
      next_comment = { lhs = "]c", desc = "go to next comment" },
      prev_comment = { lhs = "[c", desc = "go to previous comment" },
    },
  },
  })
end)

-- 🔴 A FAILURE IS ANNOUNCED, NEVER SWALLOWED. The `pcall` exists to stop a
-- setup error tearing the rest of this file in half, not to hide one — and the
-- consequence of an error here is specific and otherwise baffling: `commands
-- .setup()` never runs, so the `Octo` user command is never created, and the
-- `-c "Octo <N> <owner/repo>"` the wrapper appends fails with `E492: Not an
-- editor command: Octo` in a window that otherwise looks fine.
--
-- The message names the cause that can actually reach this arm. `gh` is pinned
-- onto this wrapper's PATH by store path through `runtimeInputs`, so a missing
-- `gh` means the deployed wrapper is not the one this repo builds.
if not ok then
  vim.notify(
    "nvim-octo: octo.nvim failed to initialise, so `:Octo` does not exist.\n"
      .. "The commonest cause is `gh` missing from this wrapper's PATH, which "
      .. "nix pins — so a stale deploy rather than a config error. Run "
      .. "`home-manager switch --flake ~/workspace/devrc --impure`.\n"
      .. tostring(err),
    vim.log.levels.ERROR
  )
end

-- ==========================================================================
-- THE LEGEND (`?`), AND THE CONFIRMED MERGE (`<localleader>pm`)
-- ==========================================================================
-- WHY THIS EXISTS: the operator opened a review buffer and asked "how do I
-- merge?" — 131 keymaps across nine buffer kinds and nothing on screen names
-- one of them. Two of the three asks turn out to be the SAME ask:
-- "open in browser" was ALREADY bound (`<C-b>`, on five of the nine tables);
-- it was simply undiscoverable. So this adds no second browser binding.
--
-- 🔴 THE LEGEND IS GENERATED FROM `config.values.mappings[kind]` AT KEYPRESS
-- TIME, NEVER HAND-WRITTEN. A curated list is stale the first time a binding
-- changes, and it goes stale silently — it is a comment that looks like a
-- feature. Every key, every description and the count all come from the live
-- config; the only key names spelled as literals in this file are the two
-- bindings the wrapper itself INSTALLS, and each is spelled exactly once, in
-- `EXTRA_BINDINGS` below, which is the same table the keymaps are set from.
--
-- 🔴 `<localleader>` IS A BACKSLASH HERE. Neither `mapleader` nor
-- `maplocalleader` is set by this wrapper, so both are neovim's default `\`,
-- and every `<localleader>xy` in the tables above is really `\xy`. A legend
-- that printed `<localleader>pd` would be close to useless, so the rows are
-- RESOLVED against `vim.g.maplocalleader` / `vim.g.mapleader`. Key names that
-- are not leaders (`<C-b>`, `<CR>`, `]c`) stay in vim's own notation, because
-- that is how they are typed and how every other vim document writes them.
--
-- ⚠ `_G.NvimOcto` IS THE OBSERVATION SURFACE, and that is why it is a global
-- in a file that otherwise needs none. It is what the hermetic test drives:
-- stub the ask with "no" and watch the merge NOT happen, stub it with "yes"
-- and watch it happen. A confirmation nothing can drive is a confirmation
-- nobody has watched work.
local M = {}
_G.NvimOcto = M

-- The leader characters, resolved the way neovim resolves them: unset (or
-- empty) means the default backslash. Read at CALL time rather than captured,
-- so the legend cannot drift from what vim would bind.
function M.leader_chars()
  local function resolved(value)
    if value == nil or value == "" then
      return "\\"
    end
    return tostring(value)
  end
  return resolved(vim.g.mapleader), resolved(vim.g.maplocalleader)
end

-- `<localleader>po` -> `\po`. `%b<>` matches one balanced `<…>` token at a
-- time, so `<localleader><space>` yields `\<space>` and `<C-b>` is returned
-- untouched — a gsub over the raw string would have mangled both.
function M.resolve_lhs(lhs)
  local leader, localleader = M.leader_chars()
  local out = tostring(lhs):gsub("%b<>", function(token)
    local name = token:sub(2, -2):lower()
    if name == "localleader" then
      return localleader
    end
    if name == "leader" then
      return leader
    end
    return token
  end)
  return out
end

-- 🔴 A SENTINEL, NOT A DEFAULT, AND THE DIFFERENCE IS THE WHOLE POINT. The
-- merge METHOD is read out of the live config rather than restated, so
-- `default_merge_method` above stays the single place it is decided. When it
-- cannot be read, this must NOT quietly substitute a plausible value: a merge
-- dispatched with a method nobody chose is the wrong commit shape on a repo
-- whose last five merges were all squashes, and the prompt would have named
-- the guess as though it were the setting. `confirm_and_merge` REFUSES on this
-- value rather than asking.
--
-- ⚠ FOUND BY A MUTATION SWEEP, not by review: replacing this return with
-- `"squash"` survived a fully green suite, because nothing reached the arm.
-- The repair was to make it reachable AND consequential.
M.MERGE_METHOD_UNKNOWN = "UNKNOWN-METHOD"

function M.merge_method()
  local got, conf = pcall(function()
    return require("octo.config").values
  end)
  if got and type(conf) == "table" and type(conf.default_merge_method) == "string" then
    return conf.default_merge_method
  end
  return M.MERGE_METHOD_UNKNOWN
end

function M.merge_desc()
  return string.format(
    "merge this PR (%s) — ASKS FOR CONFIRMATION FIRST", M.merge_method())
end

-- 🔴 ONE TABLE, TWO CONSUMERS: the keymaps are SET from this and the legend is
-- RENDERED from this. That is what makes "the legend shows what is bound" a
-- structural property rather than a promise — a binding added here appears in
-- the legend with no second edit, and a legend row cannot describe a keystroke
-- that was never installed.
--
-- `["*"]` applies to every buffer kind; a kind key adds to it.
-- `make(kind)` returns the callback, closing over the kind so the legend a
-- buffer shows is ITS OWN table and not whichever buffer happened to be open
-- when the config loaded.
local EXTRA_BINDINGS = {
  ["*"] = {
    {
      id = "legend",
      lhs = "?",
      desc = "show this legend",
      make = function(kind)
        return function()
          M.show_legend(kind)
        end
      end,
    },
  },
  -- Only on `pull_request`: octo's merge resolves the CURRENT buffer, so a
  -- merge key on the diff or file-panel surface could not act on anything.
  -- The same condition therefore decides both the keymap and the legend's
  -- merge footer, because both read this table.
  pull_request = {
    {
      id = "merge",
      lhs = "<localleader>pm",
      desc = function()
        return M.merge_desc()
      end,
      make = function()
        return function()
          M.confirm_and_merge()
        end
      end,
    },
  },
}

-- The wrapper's own bindings for one kind, with every `desc` resolved to a
-- string.
function M.extra_for(kind)
  local out = {}
  local groups = { EXTRA_BINDINGS["*"], EXTRA_BINDINGS[kind] }
  for _, group in ipairs(groups) do
    if type(group) == "table" then
      for _, binding in ipairs(group) do
        local desc = binding.desc
        if type(desc) == "function" then
          desc = desc()
        end
        out[#out + 1] = {
          id = binding.id,
          lhs = binding.lhs,
          desc = tostring(desc),
          make = binding.make,
        }
      end
    end
  end
  return out
end

-- Plain keys and `<C-…>` first, leader groups after; alphabetical within each,
-- with the action name as a deterministic tie-break. Sorting by the RESOLVED
-- key is what puts all the `\…` sequences together, which is the grouping a
-- reader is actually looking for.
local function leader_group(lhs)
  local leader, localleader = M.leader_chars()
  if lhs:sub(1, #localleader) == localleader then
    return 1
  end
  if lhs:sub(1, #leader) == leader then
    return 1
  end
  return 0
end

function M.row_order(a, b)
  local ga, gb = leader_group(a.lhs), leader_group(b.lhs)
  if ga ~= gb then
    return ga < gb
  end
  if a.lhs ~= b.lhs then
    return a.lhs < b.lhs
  end
  return a.action < b.action
end

-- 🔴 THE ROWS MIRROR `octo.utils.apply_mappings`'s OWN FILTER, deliberately.
-- That function binds an entry only when the action exists in `octo.mappings`
-- and carries a non-empty `lhs`, so a legend that listed the raw config table
-- would advertise keystrokes that are not bound. The count in the title is
-- therefore a count of BINDINGS, which is the number a reader can act on.
function M.legend_rows(kind)
  local rows = {}
  local got_conf, conf = pcall(function()
    return require("octo.config").values
  end)
  local got_actions, actions = pcall(require, "octo.mappings")
  if not got_actions then
    actions = nil
  end
  if got_conf and type(conf) == "table" and type(conf.mappings) == "table" then
    local declared = conf.mappings[kind]
    if type(declared) == "table" then
      for action, value in pairs(declared) do
        local bound = (actions == nil) or (actions[action] ~= nil)
        if bound and type(value) == "table"
            and type(value.lhs) == "string" and value.lhs ~= "" then
          local desc = value.desc
          if type(desc) ~= "string" or desc == "" then
            desc = (action:gsub("_", " "))
          end
          rows[#rows + 1] = {
            action = action,
            lhs = M.resolve_lhs(value.lhs),
            desc = desc,
            source = "octo",
          }
        end
      end
    end
  end
  for _, binding in ipairs(M.extra_for(kind)) do
    rows[#rows + 1] = {
      action = binding.id,
      lhs = M.resolve_lhs(binding.lhs),
      desc = binding.desc,
      source = "nvim-octo",
    }
  end
  table.sort(rows, M.row_order)
  return rows
end

-- `#text` counts BYTES, and these lines carry em dashes; `strdisplaywidth` is
-- the only thing that knows how wide they render. Guarded because this file is
-- also executed outside neovim by the hermetic test, where falling back to the
-- byte count merely over-estimates the window width by a few columns.
local function display_width(text)
  local got, width = pcall(function()
    return vim.fn.strdisplaywidth(text)
  end)
  if got and type(width) == "number" then
    return width
  end
  return #text
end

-- The rendered legend, as an array of lines. Split out from `show_legend` so
-- the text is readable without a window — which is how the tests read it, and
-- how a headless check reads it.
function M.legend_lines(kind)
  local rows = M.legend_rows(kind)
  local keywidth = 1
  for _, row in ipairs(rows) do
    if #row.lhs > keywidth then
      keywidth = #row.lhs
    end
  end
  local body = {}
  for _, row in ipairs(rows) do
    body[#body + 1] = string.format("  %-" .. keywidth .. "s   %s", row.lhs, row.desc)
  end
  local lines = {
    string.format("octo legend — %s buffer — %d bindings", kind, #rows),
    "",
  }
  for _, line in ipairs(body) do
    lines[#lines + 1] = line
  end
  -- The merge footer is emitted from the SAME table the merge keymap is
  -- installed from, so it appears exactly where a merge is actually bound and
  -- names exactly the key that was bound.
  for _, binding in ipairs(M.extra_for(kind)) do
    if binding.id == "merge" then
      lines[#lines + 1] = ""
      lines[#lines + 1] = string.format(
        "MERGE: %s  — %s. Any answer other than `yes` aborts and sends nothing.",
        M.resolve_lhs(binding.lhs), binding.desc)
    end
  end
  lines[#lines + 1] = ""
  lines[#lines + 1] = "q / <Esc> / ?   close this legend"
  return lines
end

-- A floating window, sized to its content and CAPPED to the editor: a
-- `pull_request` legend is 45 rows and must not run off a 30-line terminal.
-- Over the cap the window scrolls rather than overflowing.
function M.show_legend(kind)
  local lines = M.legend_lines(kind)
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  vim.bo[buf].modifiable = false
  vim.bo[buf].bufhidden = "wipe"

  local width = 1
  for _, line in ipairs(lines) do
    local w = display_width(line)
    if w > width then
      width = w
    end
  end
  local columns = tonumber(vim.o.columns) or 80
  local editor_lines = tonumber(vim.o.lines) or 24
  width = math.min(width + 2, math.max(20, columns - 4))
  local height = math.min(#lines, math.max(3, editor_lines - 6))

  local win = vim.api.nvim_open_win(buf, true, {
    relative = "editor",
    width = width,
    height = height,
    row = math.max(0, math.floor((editor_lines - height) / 2) - 1),
    col = math.max(0, math.floor((columns - width) / 2)),
    style = "minimal",
    border = "rounded",
  })

  local function close()
    if vim.api.nvim_win_is_valid(win) then
      vim.api.nvim_win_close(win, true)
    end
  end
  for _, key in ipairs({ "q", "<Esc>", "?" }) do
    vim.keymap.set("n", key, close,
      { buffer = buf, silent = true, noremap = true, nowait = true,
        desc = "close the octo legend" })
  end
  return win, buf
end

-- --------------------------------------------------------------------------
-- THE CONFIRMATION
-- --------------------------------------------------------------------------
-- 🔴 THE THREE SEAMS ARE SEPARATE FUNCTIONS ON PURPOSE. `current_pr`, `ask`
-- and `perform_merge` are the buffer, the human and GitHub; splitting them is
-- what lets a test hold two of them still and move the third, so "a `no`
-- answer does not reach the merge" is WATCHED rather than reasoned about. A
-- confirmation written as one inline block can only be read, and a read
-- confirmation is exactly the kind of guard this repo keeps finding inert.
function M.current_pr()
  local got, utils = pcall(require, "octo.utils")
  if not got or type(utils) ~= "table" or type(utils.get_current_buffer) ~= "function" then
    return nil
  end
  local buffer = utils.get_current_buffer()
  if type(buffer) ~= "table" or type(buffer.isPullRequest) ~= "function" then
    return nil
  end
  if not buffer:isPullRequest() then
    return nil
  end
  return { number = buffer.number, repo = buffer.repo }
end

function M.ask(prompt)
  return vim.fn.input(prompt)
end

-- The SAME call octo's own `merge_pr` action makes. Driving a private path
-- would be a second implementation of merging, which is how the two would
-- drift; this way `default_merge_method` and `default_delete_branch` are read
-- by the code that has always read them.
function M.perform_merge(method)
  require("octo.commands").merge_pr(method)
end

local function trimmed(text)
  local out = tostring(text):gsub("^%s+", "")
  out = out:gsub("%s+$", "")
  return out
end

-- Returns true only when a merge was actually dispatched, so the caller — and
-- a test — can tell "aborted" from "merged" without inspecting the world.
function M.confirm_and_merge()
  local pr = M.current_pr()
  if pr == nil then
    vim.notify(
      "nvim-octo: this is not a pull request buffer, so there is nothing to merge.",
      vim.log.levels.WARN)
    return false
  end
  local method = M.merge_method()
  if method == M.MERGE_METHOD_UNKNOWN then
    vim.notify(
      "nvim-octo: `default_merge_method` could not be read from octo's live "
        .. "config, so the merge method is unknown. REFUSING rather than "
        .. "guessing — a merge dispatched with a method nobody chose produces "
        .. "the wrong commit shape. Merge from the web UI, or fix the wrapper.",
      vim.log.levels.ERROR)
    return false
  end
  local prompt = string.format(
    "MERGE pull request #%s in %s, method %s? Type yes to confirm (anything else aborts): ",
    tostring(pr.number), tostring(pr.repo), method)
  -- 🔴 `pcall`, because `<C-c>` at an `input()` prompt RAISES rather than
  -- returning an empty string. An unguarded call would propagate the
  -- interrupt out of the keymap; guarded, the interrupt lands in the abort
  -- arm, which is the only safe direction for this particular error.
  local answered, answer = pcall(M.ask, prompt)
  if not answered or type(answer) ~= "string" or trimmed(answer):lower() ~= "yes" then
    vim.notify(
      string.format(
        "nvim-octo: merge of #%s in %s ABORTED — nothing was sent to GitHub.",
        tostring(pr.number), tostring(pr.repo)),
      vim.log.levels.WARN)
    return false
  end
  M.perform_merge(method)
  return true
end

-- --------------------------------------------------------------------------
-- THE SEAM — why this rides `utils.apply_mappings` and not `FileType octo`
-- --------------------------------------------------------------------------
-- 🔴 A `FileType octo` AUTOCMD IS THE OBVIOUS HOOK AND IT IS INCOMPLETE.
-- MEASURED against octo 2026-08-28: `filetype = "octo"` is set only on the
-- PR / issue / discussion buffers. The three REVIEW surfaces never get it —
-- `review_diff` is applied to a buffer holding the diffed FILE (so its
-- filetype is that file's language), and `file_panel` and `submit_win` are
-- plain scratch buffers. A FileType hook would therefore have missed 40 of
-- the 131 bindings, in exactly the buffers a review happens in.
--
-- `octo.utils.apply_mappings(kind, bufnr)` is the one seam every kind passes
-- through, and — the part that matters — it CARRIES THE KIND, which is what a
-- per-buffer legend needs.
--
-- 🔴 WRAPPING A PLUGIN'S MODULE FUNCTION IS FRAGILE ACROSS UPSTREAM UPDATES,
-- SO IT FAILS LOUDLY. If a future octo renames or moves `apply_mappings`, the
-- wrap silently stops applying and `?` quietly stops existing — a legend that
-- is gone looks exactly like a legend nobody pressed. A review TUI that
-- refuses to start is the better outcome, so this raises rather than
-- degrading.
if ok then
  local got_utils, octo_utils = pcall(require, "octo.utils")
  if not got_utils or type(octo_utils) ~= "table"
      or type(octo_utils.apply_mappings) ~= "function" then
    error(
      "nvim-octo: octo.utils.apply_mappings is not a function, so the `?` "
        .. "legend and the confirmed-merge keymap cannot be installed. octo.nvim "
        .. "has moved or renamed that seam; this wrapper's octo-init.lua must be "
        .. "updated to the new one. Failing loudly on purpose — the alternative "
        .. "is a review TUI whose legend has silently disappeared.",
      0)
  end

  local original_apply_mappings = octo_utils.apply_mappings
  octo_utils.apply_mappings = function(kind, bufnr)
    original_apply_mappings(kind, bufnr)
    for _, binding in ipairs(M.extra_for(kind)) do
      -- The RAW lhs goes to vim, exactly as octo passes `value.lhs`, so vim
      -- expands `<localleader>` itself; only the LEGEND resolves it, and it
      -- resolves it from the same `vim.g` vim expanded from.
      vim.keymap.set("n", binding.lhs, binding.make(kind), {
        buffer = bufnr,
        silent = true,
        noremap = true,
        nowait = true,
        desc = binding.desc,
      })
    end
  end
end
