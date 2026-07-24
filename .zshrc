# Source nix
. "$HOME/.nix-profile/etc/profile.d/hm-session-vars.sh"
. "$HOME/workspace/devrc/nix/bin/source-nix.sh"

export DEVRC_DIR=${DEVRC_DIR:-$HOME/workspace/devrc}

# Directory jumping via CDPATH
export CDPATH=".:$HOME/workspace:$HOME/workspace/civit"


# Set keyboard repeat rate (X11 only, skip inside tmux to avoid running per-pane)
[[ -z "$TMUX" && -n "$DISPLAY" ]] && xset r rate 180 30

# ── civitai `release` wrapper ────────────────────────────────────────────
# Replaces the manual `g pull && npm run release` toil loop in the civitai
# monorepo. `npm run release` already pulls internally (so the by-hand `g pull`
# was redundant) and BLOCKS the terminal for ~90s (up to ~35min) while it
# builds → Tekton → Flux → Flagger canary. This wrapper: auto-fetches (kills the
# manual pull), runs BACKGROUNDED+disowned so the terminal returns immediately,
# and fires a desktop notification on completion/failure so you can switch
# workspaces and only get pulled back if it fails.
#
# `_release_run` is the synchronous core (guard → fetch → release → notify-by-rc)
# factored out so it can be driven in the foreground by tests; `release` just
# backgrounds it with zsh `&!`. `_release_run` is deliberately written
# bash/POSIX-compatible (no zsh-only expansions) so the test can source it.
# >>> _release_run >>>
_release_run() {
  # $1: optional explicit log path (release passes one; tests omit it).
  local root base log dir rc
  root=$(git rev-parse --show-toplevel 2>/dev/null)
  base=${root##*/}
  if [ "$base" != "civitai" ]; then
    printf 'release: only runs inside the civitai repo (repo root basename must be "civitai", got "%s")\n' "${base:-none}" >&2
    return 1
  fi
  log=${1:-"$HOME/.cache/civitai-release/$(date +%Y%m%d-%H%M%S).log"}
  dir=${log%/*}
  mkdir -p "$dir"
  {
    git fetch origin --quiet   # replaces the redundant manual `g pull`
    npm run release
  } >>"$log" 2>&1
  rc=$?
  if [ "$rc" -eq 0 ]; then
    notify-send -u low "✅ civitai release" "complete" 2>/dev/null
  else
    notify-send -u critical "❌ civitai release FAILED (rc=$rc)" "see $log" 2>/dev/null
  fi
  return "$rc"
}
# <<< _release_run <<<

release() {
  # Guard up-front so we can give immediate terminal feedback before backgrounding.
  local root base log
  root=$(git rev-parse --show-toplevel 2>/dev/null)
  base=${root##*/}
  if [[ "$base" != "civitai" ]]; then
    printf 'release: only runs inside the civitai repo (repo root basename must be "civitai", got "%s")\n' "${base:-none}" >&2
    return 1
  fi
  log="$HOME/.cache/civitai-release/$(date +%Y%m%d-%H%M%S).log"
  print "release: running 'git fetch + npm run release' in the background → $log"
  _release_run "$log" &!
}
