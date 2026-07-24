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
# and signals completion two ways so a FAILED deploy is never invisible.
#
# 🔴 Headless-safe: `npm run release` is fired on the WORKBENCH, which runs
# headless (server-mode, no DISPLAY/DBUS) — there `notify-send` silently no-ops.
# If output only went to the log, a failed civitai-dp-prod deploy would be
# completely invisible. So on completion we ALSO write a status line to
# /dev/tty: a backgrounded+disowned `&!` job keeps its controlling tty, so the
# line lands in the tmux pane you backgrounded from even with no DBUS. The
# `notify-send` toast is kept as a bonus for the graphical laptop.
#
# `_release_run` is the synchronous core (guard → lock → fetch → release →
# notify-by-rc) factored out so it can be driven in the foreground by tests;
# `release` just backgrounds it with zsh `&!` (stdin detached from /dev/null so
# a credential/GPG prompt fails fast instead of SIGTTIN-suspending silently).
# `_release_run` is deliberately written bash/POSIX-compatible (no zsh-only
# expansions) so the test can source it.
# >>> _release_run >>>
_release_run() {
  # $1: optional explicit log path (release passes one; tests omit it).
  local root base log dir lock rc
  root=$(git rev-parse --show-toplevel 2>/dev/null)
  base=${root##*/}
  if [ "$base" != "civitai" ]; then
    printf 'release: only runs inside the civitai repo (repo root basename must be "civitai", got "%s")\n' "${base:-none}" >&2
    return 1
  fi
  log=${1:-"$HOME/.cache/civitai-release/$(date +%Y%m%d-%H%M%S).log"}
  dir=${log%/*}
  mkdir -p "$dir"
  find "$dir" -name '*.log' -mtime +14 -delete 2>/dev/null   # best-effort prune

  # Concurrency lock: two release fires would race two `git push
  # --force-with-lease` on the `release` branch. mkdir is atomic — no tooling.
  lock="$dir/.lock"
  if ! mkdir "$lock" 2>/dev/null; then
    printf 'release: another release is in flight (lock held: %s) — not starting\n' "$lock" >&2
    return 1
  fi

  {
    git fetch origin --quiet   # replaces the redundant manual `g pull`
    npm run release
  } >>"$log" 2>&1
  rc=$?

  rmdir "$lock" 2>/dev/null   # release the lock on every exit path (success/fail)

  if [ "$rc" -eq 0 ]; then
    notify-send -u low "✅ civitai release" "complete" 2>/dev/null
    printf '✅ civitai release complete (%s)\n' "$log" >/dev/tty 2>/dev/null
  else
    notify-send -u critical "❌ civitai release FAILED (rc=$rc)" "see $log" 2>/dev/null
    printf '❌ civitai release FAILED rc=%s — see %s\n' "$rc" "$log" >/dev/tty 2>/dev/null
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
  # stdin from /dev/null so a credential/GPG prompt fails fast instead of
  # SIGTTIN-suspending the disowned job silently.
  _release_run "$log" </dev/null &!
}
