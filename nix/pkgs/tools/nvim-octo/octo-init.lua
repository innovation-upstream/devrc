-- octo.nvim configuration for `nvim-octo` — the review TUI a clicked GitHub
-- mention opens in. Sourced by the generated init.vim (`luafile`), so it runs
-- BEFORE the `-c "Octo <N> <owner/repo>"` the wrapper appends.
--
-- 🔴 THIS FILE IS PARSED BY A TEST. `scripts/tests/test_nvim_octo.py` reads the
-- `mappings.pull_request` table structurally and asserts that NO merge key is
-- declared in it, plus `default_merge_method`, `default_delete_branch` and
-- `picker`. Reword the comments freely; do not move those settings into a
-- computed expression, because a value the reader cannot see is a value nobody
-- is checking.

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
-- the user tables below are merged in (octo `config.lua`'s `M.setup`), so what
-- is declared here is the COMPLETE set of octo keymaps. Merging is then
-- reachable only by TYPING `:Octo pr merge`, which is a deliberate act.
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
