#!/usr/bin/env bash
#
# install.sh — point GLOBAL git at devrc's tracked githooks/ dir.
#
# Sets `core.hooksPath <this dir>` in git's GLOBAL config so the
# version-controlled pre-push dispatcher runs for every repo that does NOT
# override core.hooksPath locally. It composes with repo-local
# .git/hooks/pre-push (chains to it first).
#
# The AUDIT flag defaults to SHADOW (installing changes nothing about the audit
# side of your push UX until you flip AUDIT_ON_PUSH=on). The TEST GATE, however,
# defaults to ON *in the devrc repo only* — devrc pushes will run the Python
# suite and block on a genuine failure (TESTS_ON_PUSH; DEVRC_SKIP_TESTS=1 to
# override a single push). It is a no-op in every other repo.
# Disable everything with: $HOME/workspace/devrc/githooks/install.sh --uninstall
# (Invoke by ABSOLUTE path. A relative invocation is a documented hazard when
#  CDPATH is exported — see the $DIR guard below.)
#
set -euo pipefail

# 🔴 `CDPATH= cd -P --` IS LOAD-BEARING, NOT BOILERPLATE. When CDPATH is exported
# (it is, on the workbench host: `.:/home/zach/workspace:…`) bash `cd` ECHOES the
# resolved directory to stdout whenever it finds the target via CDPATH — and a
# RELATIVE invocation (`githooks/install.sh`, the shape this file's own header
# used to document) makes `dirname` return a relative path, so `.` in CDPATH
# hits. The command substitution then captures cd's echo AND pwd, and $DIR
# becomes a TWO-LINE string.
#
# MEASURED, and this is why it is a correctness fix rather than hygiene: $DIR is
# interpolated into the stamp below, so the second line lands in ~/.gitconfig as
# config rather than as a comment. Every subsequent git command in EVERY repo on
# the box then dies `fatal: bad config line 4 in file ~/.gitconfig` — including
# `--uninstall`, which cannot parse the file it would repair. Only a manual
# `rm ~/.gitconfig` recovers. `-P` also resolves symlinks; `--` stops an
# argument beginning with `-` being read as an option.
# shellcheck disable=SC1007  # `CDPATH= cd` is a deliberate prefix assignment, not a typo
DIR="$(CDPATH= cd -P -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 🔴 STRUCTURAL REFUSAL, because the line above is one edit away from silently
# regressing and the damage is host-wide. Validate the VALUE, do not trust the
# construct that produced it: anything that is not a single-line path to a real
# directory must never reach the stamp or the config write.
#
# 🔴 THE NEWLINE IS A LITERAL IN A SINGLE-QUOTED ASSIGNMENT, AND IT HAS TO BE.
# `$(printf '\n')` looks like the obvious spelling and is WRONG: command
# substitution strips trailing newlines, so it expands to the EMPTY string, the
# case pattern degrades to `**`, and the guard fires on every well-formed $DIR.
# Measured — it took the whole suite from green to 14 failures, refusing a
# perfectly good single-line path. A guard that always fires is not fail-safe;
# it is just broken in the other direction.
#
# 🔴 ONE PREDICATE, ONE MESSAGE. The two rejections below were separate `case` and
# `[ ! -d ]` blocks with different diagnostics, and that split was itself a defect:
# a multi-line $DIR is also not a directory, so which of the two spoke depended on
# which mutation you were running, and a caller could not assert on either without
# being wrong half the time. Consolidated so there is exactly one refusal path to
# assert on — and one narrow anchor to mutate.
_NL='
'
_reject_bad_dir() {  # $1 = the resolved installer directory
  local d="$1" ok=1
  case "$d" in *"$_NL"*) ok=0 ;; esac
  [ -d "$d" ] || ok=0
  # An explicit `if`, not `[ "$ok" -eq 1 ] && return 0`. The && form is correct
  # under `set -e` (a non-final command in an AND-list is exempt) but relies on a
  # rule most readers have to look up, in the one function whose job is to abort
  # safely. Say it plainly instead.
  if [ "$ok" -eq 1 ]; then
    return 0
  fi

  # 🔴 NOT `$*` — inside a function that is the FUNCTION's arguments, i.e. $DIR,
  # so the "re-run with" line would echo the directory back instead of the flags
  # the operator actually passed. The script's own argv is captured at top level.
  echo "ERROR: refusing to continue — the installer's own directory did not resolve" >&2
  echo "       to a single-line path to a real directory, which would corrupt the" >&2
  echo "       global git config:" >&2
  printf '%s\n' "$d" | sed 's/^/       | /' >&2
  echo "       This is the CDPATH hazard described above: with CDPATH exported, bash" >&2
  echo "       'cd' echoes the directory it resolved, so a RELATIVE invocation yields" >&2
  echo "       a two-line \$DIR. Re-run with an absolute path:" >&2
  echo "         \$HOME/workspace/devrc/githooks/install.sh${_ARGV:+ $_ARGV}" >&2
  exit 1
}
_ARGV="$*"
_reject_bad_dir "$DIR"

# Every path below is anchored on $HOME. Under `set -u` an unset HOME aborts with
# a bare `HOME: unbound variable` from whichever line happens to touch it first,
# which tells the operator nothing about what to do.
if [ -z "${HOME:-}" ]; then
  echo "ERROR: HOME is not set, so there is no global git config to install into." >&2
  echo "       Re-run with HOME pointing at your home directory." >&2
  exit 1
fi

# 🔴 PROVENANCE, NOT SIZE. `--uninstall` may delete a `~/.gitconfig` only if THIS
# script created it. An earlier revision tested `size == 0` and called that
# "the file this installer had to create" — it is not. MEASURED: an operator's
# own empty `~/.gitconfig` (the exact manual workaround someone would apply for
# for #905) was silently deleted by `--uninstall`, after which their next
# `git config --global …` write fails against the read-only store all over again.
# So the file is STAMPED on creation and the stamp is what authorises removal.
MARKER_TOKEN="devrc-githooks-install-created-this-file"

# 🔴 ONE LINE, AND THAT IS A CORRECTNESS CONSTRAINT, NOT A STYLE CHOICE. The
# predicate below accepts a line only if it CONTAINS the token, so a multi-line
# stamp makes its own continuation lines look like foreign content and the file
# is then never removable. Measured: a 3-line stamp broke both the uninstall
# cleanup and the failed-install rollback. Keep the whole stamp on this line.
_stamp_marker() {  # $1 = a file this run created
  printf '# %s — created by devrc githooks/install.sh (#905); remove with: %s/install.sh --uninstall\n' \
    "$MARKER_TOKEN" "$DIR" >> "$1"
}

# True only when the file carries OUR stamp and holds nothing else of substance.
# git leaves an empty `[section]` header behind after unsetting a section's last
# key (measured), so a bare header is not content; ANY other comment or line is,
# and blocks removal. Deliberately conservative: a file we are not certain about
# is left alone.
_only_devrc_marker() {  # $1 = file
  awk -v tok="$MARKER_TOKEN" '
    /^[[:space:]]*$/                      { next }
    index($0, tok)                        { seen = 1; next }
    /^[[:space:]]*\[[^]]*\][[:space:]]*$/ { next }
                                          { other = 1 }
    END { exit !(seen && !other) }
  ' "$1"
}

# --------------------------------------------------------------------------- #
# 🔴 WHERE A "GLOBAL" SETTING CAN ACTUALLY BE WRITTEN
# --------------------------------------------------------------------------- #
# This script used to run `git config --global core.hooksPath "$DIR"`. On a
# home-manager host — which is every machine devrc targets — THAT CANNOT WORK.
#
# MEASURED on the workbench host 2026-08-26:
#   * `git config --global` resolves to `~/.config/git/config`
#   * that path is a symlink into the READ-ONLY nix store
#     (`/nix/store/…-hm_gitconfig`), generated by `nix/programs/git/default.nix`
#   * `~/.gitconfig` does not exist
#   * so the installer died on its FIRST config write:
#         error: could not lock config file …/.config/git/config:
#         Read-only file system
#     rc=255 — before chmod'ing nothing further, before seeding
#     `~/.claude/audit-on-push.env`, before printing anything useful.
#
# Consequence: `core.hooksPath` was never set on this host, so the pre-push test
# gate and the audit hook have never been installable here at all.
#
# 🔴 THE FIX IS NOT "DECLARE IT IN NIX". `nix/programs/git/default.nix` is where
# `core.sshCommand` went (#782/#887) and that was right for THAT setting: an
# ssh keepalive is a host-wide invariant with no opt-out. `core.hooksPath` is
# the opposite — it is an explicitly opt-in install with a documented
# `--uninstall`, and a nix-declared value CANNOT be unset by this script, so
# declaring it there would make `--uninstall` unsatisfiable. It would also be
# inert until someone ran `home-manager switch`.
#
# 🔴 THERE IS NO THIRD LOCATION. git consults exactly two files for global
# scope: `$XDG_CONFIG_HOME/git/config` (default `~/.config/git/config`) and
# `~/.gitconfig`. When home-manager owns the first, the second is the ONLY
# writable global location — so that is the fallback, and it is not a
# workaround, it is the other half of git's own documented pair.
#
# MEASURED, and this is what makes the fallback correct rather than merely
# writable: git reads BOTH files, and `~/.gitconfig` WINS for a key set in both.
# `git config --list --show-origin` with both present shows the home-manager
# file's `rebase.autostash=false`, `diff.algorithm=histogram` and `user.name`
# STILL IN EFFECT alongside `~/.gitconfig`'s `core.hooksPath`. Creating
# `~/.gitconfig` does NOT mask the home-manager config for real git operations.
#
# ⚠ ONE REAL SIDE EFFECT, STATED. Once `~/.gitconfig` exists it becomes the
# single file `--global` READS as well as writes: `git config --global --get
# user.name` then returns nothing on this host, because the home-manager values
# live in the XDG file and `--global` no longer looks there. Effective config is
# unchanged (`git config --get user.name` still answers); only the `--global`
# SCOPE view narrows, and a human debugging with `--global --list` sees less than
# they expect. `--uninstall` removes a `~/.gitconfig` THIS SCRIPT created (see the
# stamp above), which restores the original resolution.
#
# 🔴 AN EARLIER REVISION OF THIS COMMENT CLAIMED "no script in devrc reads
# `git config --global` — checked across the tree". THAT WAS FALSE, and it was
# asserted rather than measured: the enumeration behind it was a broken shell
# pipeline that inspected nothing and returned a confident empty result. Readers
# that DO exist:
#   * `scripts/run-tests.sh` — `--list --show-origin` to build the protected set,
#     and a `--global` write probe that verifies the isolation lever governs;
#   * `scripts/testlib/nogit_plugin.py` — the same write/read control;
#   * `scripts/present/measure.py` (`m_hook_gate_install`) — reads
#     `core.hooksPath` at `--local` then `--global`.
# None of them breaks today: run-tests.sh UNIONS the discovered origins with the
# two hardcoded paths, so its protected set can only grow.
#
# ⚠ ONE LATENT CASE, NOT FIXED HERE AND NOT REACHABLE TODAY. If `core.hooksPath`
# ever lands in the XDG file (e.g. someone declares it in nix) AND a
# `~/.gitconfig` later appears, `measure.py`'s `--global` read looks only at
# `~/.gitconfig` and would report "no blocking pre-push gate installed" while one
# is in fact armed. That is a defect in the READER, not here; it is recorded so
# whoever hits it does not have to rediscover the mechanism.

_xdg_config() { printf '%s/git/config\n' "${XDG_CONFIG_HOME:-$HOME/.config}"; }

# The file git ITSELF would write a `--global` setting to, by git's own rules:
# `~/.gitconfig` if it exists, else the XDG file if THAT exists, else
# `~/.gitconfig` (created). Reproduced here so the happy path on a non-nix host
# is byte-for-byte the behaviour this script always had.
_git_write_pick() {
  if [ -n "${GIT_CONFIG_GLOBAL:-}" ]; then printf '%s\n' "$GIT_CONFIG_GLOBAL"; return 0; fi
  if [ -e "$HOME/.gitconfig" ]; then printf '%s\n' "$HOME/.gitconfig"; return 0; fi
  local xdg; xdg="$(_xdg_config)"
  if [ -e "$xdg" ]; then printf '%s\n' "$xdg"; return 0; fi
  printf '%s\n' "$HOME/.gitconfig"
}

# Every file that could be holding a global `core.hooksPath` we wrote. Used by
# --uninstall, which must find our value wherever install.sh put it.
_global_candidates() {
  if [ -n "${GIT_CONFIG_GLOBAL:-}" ]; then printf '%s\n' "$GIT_CONFIG_GLOBAL"; return 0; fi
  printf '%s\n' "$HOME/.gitconfig"
  _xdg_config
}

_set_hookspath() {  # $1 = config file. stdout/stderr = git's own diagnostic.
  mkdir -p "$(dirname "$1")" 2>/dev/null || true
  git config --file "$1" core.hooksPath "$DIR"
}

# The EFFECTIVE value, as any later `git push` will see it — not the value we
# think we wrote.
#
# 🔴 `mktemp -d` IS NOT NECESSARILY OUTSIDE A REPOSITORY. An earlier comment here
# claimed cwd was "moved out of any working tree"; it is not — `mktemp -d` honours
# $TMPDIR, and if TMPDIR sits inside a git checkout then git discovers that repo
# by walking UP from the scratch dir and answers with its REPO-LOCAL
# core.hooksPath. MEASURED: with TMPDIR inside a repo whose local core.hooksPath
# was `/REPO/LOCAL/POISON`, this function returned `/REPO/LOCAL/POISON` — which
# would fail the effectiveness check on a perfectly good install (a false RED)
# or, in the mirror case, hide a real failure.
#
# GIT_CEILING_DIRECTORIES stops the upward search, so no repository is found and
# only system+global config answers — which is exactly the scope being verified.
#
# 🔴 IT MUST NAME THE PARENT, NOT THE SCRATCH DIR ITSELF. MEASURED both ways:
# with the ceiling set to the scratch directory git STILL found the enclosing
# repo and returned `/REPO/LOCAL/POISON`; with the parent it returned empty.
# A ceiling bounds where git may CHDIR TO, so the directory that must be
# out of bounds is the one above the search start.
_effective_hookspath() {
  local scratch; scratch="$(mktemp -d)"
  local out=""
  out="$(env -u GIT_DIR -u GIT_WORK_TREE \
         GIT_CEILING_DIRECTORIES="$(dirname "$scratch")" \
         git -C "$scratch" config --get core.hooksPath 2>/dev/null || true)"
  rmdir "$scratch" 2>/dev/null || true
  printf '%s\n' "$out"
}

# --------------------------------------------------------------------------- #
# UNINSTALL
# --------------------------------------------------------------------------- #
# 🔴 ONLY EVER UNSETS A VALUE EQUAL TO $DIR. A global core.hooksPath pointing
# somewhere else belongs to someone else and is left exactly as found — this
# script must never remove a setting it did not write.
if [ "${1:-}" = "--uninstall" ]; then
  cleared=0
  seen=""
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    case " $seen " in *" $f "*) continue ;; esac
    seen="$seen $f"
    [ -f "$f" ] || continue
    current="$(git config --file "$f" --get core.hooksPath 2>/dev/null || true)"
    [ "$current" = "$DIR" ] || continue
    if git config --file "$f" --unset core.hooksPath 2>/dev/null; then
      echo "uninstalled: core.hooksPath cleared from $f (was $DIR)"
      cleared=$((cleared + 1))
      # Remove the file only if THIS SCRIPT created it — proven by the stamp,
      # not inferred from its size. See the MARKER_TOKEN header: an operator's
      # own empty ~/.gitconfig is a legitimate manual workaround for #905 and
      # deleting it re-breaks their `git config --global` writes.
      if [ "$f" = "$HOME/.gitconfig" ] \
         && [ -z "$(git config --file "$f" --list 2>/dev/null)" ] \
         && _only_devrc_marker "$f"; then
        rm -f "$f" && echo "  removed $f (this installer created it; nothing else was in it)"
      elif [ "$f" = "$HOME/.gitconfig" ] && [ ! -s "$f" ]; then
        echo "  left $f in place — it is empty but carries no devrc stamp, so it"
        echo "  is not ours to delete. Remove it yourself if you want git's"
        echo "  --global reads to go back to the XDG config."
      fi
    else
      echo "WARNING: core.hooksPath in $f is ours but could NOT be unset" >&2
      echo "         (read-only?) — the hooks will still run. File: $f" >&2
      exit 1
    fi
  done <<EOF
$(_global_candidates)
EOF

  if [ "$cleared" -eq 0 ]; then
    effective="$(_effective_hookspath)"
    echo "nothing to do: no global core.hooksPath of ours found"
    echo "               (effective core.hooksPath is '${effective:-<unset>}', not '$DIR')"
  fi
  exit 0
fi

# --------------------------------------------------------------------------- #
# INSTALL
# --------------------------------------------------------------------------- #
chmod +x "$DIR/pre-push" "$DIR/audit-on-push.sh" "$DIR/tests-on-push.sh" 2>/dev/null || true

PICK="$(_git_write_pick)"
prev="$(git config --file "$PICK" --get core.hooksPath 2>/dev/null || true)"
if [ -z "$prev" ]; then prev="$(_effective_hookspath)"; fi
if [ -n "$prev" ] && [ "$prev" != "$DIR" ]; then
  echo "WARNING: global core.hooksPath was already set to: $prev"
  echo "         overwriting with: $DIR"
  echo "         (your previous global hooks dir will no longer run; move its hooks here if needed)"
fi

TARGET=""
CREATED_FILE=""
FALLBACK="$HOME/.gitconfig"
# 🔴 RECORDED OUT HERE, BEFORE THE CALL. `_set_hookspath` is invoked inside a
# command substitution — a SUBSHELL — so any variable it sets is discarded on
# return. The rollback below has to know which file THIS RUN brought into
# existence, so the existence test cannot live inside the function.
PICK_EXISTED=0; if [ -e "$PICK" ]; then PICK_EXISTED=1; fi
FB_EXISTED=0;   if [ -e "$FALLBACK" ]; then FB_EXISTED=1; fi

if err="$(_set_hookspath "$PICK" 2>&1)"; then
  TARGET="$PICK"
  if [ "$PICK_EXISTED" -eq 0 ]; then CREATED_FILE="$PICK"; fi
elif [ -n "${GIT_CONFIG_GLOBAL:-}" ]; then
  # 🔴 NO FALLBACK WHEN GIT_CONFIG_GLOBAL IS SET. That variable is an explicit
  # override — the test suite's isolation guard uses it to keep a real installer
  # run away from the operator's config. Falling back to $HOME/.gitconfig here
  # would write OUTSIDE the file the caller nominated, which is precisely the
  # escape that guard exists to prevent.
  echo "ERROR: GIT_CONFIG_GLOBAL is set to '$GIT_CONFIG_GLOBAL' but is not writable." >&2
  echo "       Refusing to fall back — an explicit override is honoured or nothing is." >&2
  echo "       git said: $err" >&2
  exit 1
elif [ "$PICK" != "$FALLBACK" ] && err2="$(_set_hookspath "$FALLBACK" 2>&1)"; then
  TARGET="$FALLBACK"
  if [ "$FB_EXISTED" -eq 0 ]; then CREATED_FILE="$FALLBACK"; fi
  echo "NOTE: $PICK is not writable (home-manager owns it — it is a symlink into"
  echo "      the read-only nix store), so core.hooksPath went to $TARGET instead."
  echo "      git reads both; ~/.gitconfig takes precedence. Nothing else moved."
else
  echo "ERROR: could not write core.hooksPath to ANY global git config." >&2
  echo "       tried: $PICK" >&2
  echo "       git said: $err" >&2
  if [ "${err2+set}" = set ]; then
    echo "       tried: $HOME/.gitconfig" >&2
    echo "       git said: $err2" >&2
  fi
  exit 1
fi

# 🔴 WROTE IS NOT INSTALLED. A write to the XDG file loses to a `core.hooksPath`
# already present in ~/.gitconfig, and the write would still report success — so
# the claim is checked against what git actually resolves, from outside any repo.
if [ -n "$CREATED_FILE" ]; then _stamp_marker "$CREATED_FILE"; fi

effective="$(_effective_hookspath)"
if [ "$effective" != "$DIR" ]; then
  echo "ERROR: wrote core.hooksPath to $TARGET, but git resolves it to" >&2
  echo "       '${effective:-<unset>}' — the install did NOT take effect." >&2
  echo "       Something with higher precedence is overriding it." >&2
  # 🔴 ROLL BACK A FILE THIS RUN CREATED. Leaving a new ~/.gitconfig behind after
  # a FAILED install permanently narrows `git config --global` on the host — for
  # nothing, since the install did not take. Only a file we created, still
  # carrying nothing but our own stamp and our own key, is removed.
  if [ -n "$CREATED_FILE" ]; then
    git config --file "$CREATED_FILE" --unset core.hooksPath 2>/dev/null || true
    if [ -z "$(git config --file "$CREATED_FILE" --list 2>/dev/null)" ] \
       && _only_devrc_marker "$CREATED_FILE"; then
      rm -f "$CREATED_FILE"
      echo "       (rolled back $CREATED_FILE, which this run had created)" >&2
    else
      echo "       NOTE: $CREATED_FILE was created by this run and could not be" >&2
      echo "       rolled back cleanly. Remove it with: $DIR/install.sh --uninstall" >&2
    fi
  fi
  exit 1
fi

# Seed the flag config file at shadow if it doesn't exist yet.
CONF="$HOME/.claude/audit-on-push.env"
if [ ! -f "$CONF" ]; then
  mkdir -p "$(dirname "$CONF")"
  cp "$DIR/audit-on-push.env.example" "$CONF" 2>/dev/null || true
  echo "seeded $CONF (AUDIT_ON_PUSH=shadow — sends nothing until you flip it to 'on')"
fi

echo "installed: core.hooksPath -> $DIR (in $TARGET)"
echo "active hooks: $(ls "$DIR" | grep -vE '\.(sh|md|example)$' | tr '\n' ' ')"
echo
echo "Audit flag is SHADOW by default (logs what it WOULD send, sends nothing)."
echo "  watch shadow decisions: tail -f ~/.claude/audit-on-push.log"
echo "  go live:  echo 'AUDIT_ON_PUSH=on' >> ~/.claude/audit-on-push.env"
echo "  back off: set AUDIT_ON_PUSH=off in ~/.claude/audit-on-push.env"
echo
echo "Test gate is ON by default IN DEVRC ONLY (devrc pushes run the Python"
echo "suite + block on a genuine failure; no-op elsewhere)."
echo "  warn-only: set TESTS_ON_PUSH=shadow in ~/.claude/audit-on-push.env"
echo "  disable:   set TESTS_ON_PUSH=off   in ~/.claude/audit-on-push.env"
echo "  skip one push: DEVRC_SKIP_TESTS=1 git push …"
echo
echo "  uninstall global hook: $DIR/install.sh --uninstall"
