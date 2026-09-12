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

# 🔴 THE NUMBER IS THE FIRST ARGUMENT TO `Octo`, AND THE ORDER IS THE CONTRACT.
# octo's `M.octo(object, action, ...)` does `tonumber(object)` and, when that
# succeeds, treats `action` as the repository — `:Octo 42 owner/repo`. Passing
# a URL instead would take a different branch that reads the KIND out of the
# path, and `mention-open.py` cannot know the kind: it builds `/pull/{id}` for
# every mention and lets github.com redirect. The number lets octo's own
# `issueOrPullRequest` query answer that question against the server.
exec nvim -c "Octo $num $repo"
