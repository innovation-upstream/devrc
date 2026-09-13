# nvim-octo <owner/repo> <number>
#
# The body of the `writeShellApplication` in ./default.nix. It carries NO
# shebang and NO `set` line on purpose: `writeShellApplication` prepends both
# (`set -o errexit -o nounset -o pipefail`) and runs shellcheck over the
# assembled result, so spelling them here would duplicate them.
#
# 🔴 IT IS A SEPARATE FILE, NOT AN INLINE NIX STRING, SO A TEST CAN RUN IT.
# `scripts/tests/test_nvim_octo.py` executes this exact text under
# `bash -euo pipefail` with a STUB `nvim` on PATH — which is the only way to
# watch the validation below reject a bad argument, and the only way to see
# what ex-command a good one produces. A string interpolated into default.nix
# would be reachable only by building the derivation and running the real
# editor, which this repo must never do from a test.
#
# 🔴 WHY THE EX-COMMAND IS ASSEMBLED HERE AND NOT IN THE PYTHON HANDLER.
# `mention-open.py` spawns `alacritty … -e nvim-octo <repo> <num>` — two plain
# argv entries, no quoting anywhere. Building `-c "Octo 42 owner/repo"` in
# Python would put a shell-quoting hazard in the click path AND would make the
# spawn's argv depend on data, which the AST ledger in test_mention_open.py
# reads as `<computed>`. The composition belongs on this side of the boundary.

if [ "$#" -ne 2 ]; then
  printf 'usage: nvim-octo <owner/repo> <number>\n' >&2
  exit 64
fi

repo="$1"
num="$2"

# `owner/repo` — exactly one slash, no traversal, and only the characters
# GitHub actually allows in an owner or a repository name.
#
# ⚠ A `case` GLOB, NOT A REGEX, and the negative arm is FIRST so it wins. The
# first pattern rejects any forbidden character, a second slash, a leading or
# trailing slash, and any `..` segment; only then does the positive arm require
# that a slash is present at all. Order matters: `*/*` alone would accept
# `../../etc/passwd`.
case "$repo" in
  *[!A-Za-z0-9._/-]* | */*/* | /* | */ | *..*)
    printf 'nvim-octo: not an owner/repo: %s\n' "$repo" >&2
    exit 65
    ;;
  */*) : ;;
  *)
    printf 'nvim-octo: not an owner/repo: %s\n' "$repo" >&2
    exit 65
    ;;
esac

# Digits only, and at least one. `""` rather than the shell's usual empty-string
# spelling because this text is read verbatim into a Nix `''…''` string, where
# two single quotes are the escape sequence.
case "$num" in
  "" | *[!0-9]*)
    printf 'nvim-octo: not a reference number: %s\n' "$num" >&2
    exit 66
    ;;
esac

# 🔴 THIS IS WHAT MAKES THE WRAPPER HERMETIC, AND WITHOUT IT THE WORD "HERMETIC"
# IN ./default.nix WAS FALSE. `NVIM_APPNAME` renames the directory neovim reads
# its user config and site packages from — `~/.config/$NVIM_APPNAME` and
# `~/.local/share/$NVIM_APPNAME/site` instead of the `nvim` ones. Those paths do
# not exist for this name, which is the point: nothing of the operator's loads.
#
# 🔴 MEASURED FAILURE THIS FIXES, not a hypothetical. Selecting a row in the
# picker opened a review buffer that errored with `module 'lyaml' not found`. The
# trace named
# `~/.local/share/nvim/site/pack/packer/start/qdr.nvim/lua/qdr-nvim/qdr.lua:2` —
# a PACKER-INSTALLED PLUGIN OF THE OPERATOR'S, loaded into this wrapper because
# neovim's default `packpath` includes that site directory whatever `-u` says.
# Their daily editor supplies the rock (`ps.lyaml` in nix/programs/neovim), this
# derivation does not, so the plugin loaded and then failed.
#
# 🔴 SO THE FIX IS NOT `lyaml`. Adding the missing rock would have made THIS
# plugin load successfully inside a review TUI that has no business running it,
# and left every other plugin in that directory able to break the review surface
# on the next unrelated editor change. ./default.nix claims the plugin set
# "cannot be changed by an edit to an editor config, and it cannot be broken by
# one either" — that claim was aspirational, and this line is what makes it true.
#
# ⚠ IT OVERRIDES AN INHERITED `NVIM_APPNAME` ON PURPOSE. The whole call chain
# starts in an alacritty hint spawned with the DISPLAY MANAGER's environment, so
# whatever is set out there is not a choice anybody made for this window.
#
# VERIFIED against the built wrapper's own neovim before shipping: with this set,
# a headless load emits NOTHING (the lyaml trace is gone), `require("octo")`
# returns true, and `vim.fn.exists(":Octo")` is 2 — so octo's `setup()` still ran
# and still found `gh`. The store-path plugins come from the nix wrapper's own
# packpath entries, which are absolute and unaffected by this variable.
export NVIM_APPNAME=nvim-octo

# 🔴 THE NUMBER IS THE FIRST ARGUMENT TO `Octo`, AND THE ORDER IS THE CONTRACT.
# octo's `M.octo(object, action, ...)` does `tonumber(object)` and, when that
# succeeds, treats `action` as the repository — `:Octo 42 owner/repo`. Passing
# a URL instead would take a different branch that reads the KIND out of the
# path, and `mention-open.py` cannot know the kind: it builds `/pull/{id}` for
# every mention and lets github.com redirect. The number lets octo's own
# `issueOrPullRequest` query answer that question against the server.
exec nvim -c "Octo $num $repo"
