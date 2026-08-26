#!/usr/bin/env bash
# Regenerate the devrc explainer page — BOTH variants — into the served artefact
# directory. Run by `present-regen.service` (daily timer) or by hand.
#
#   scripts/present/run-regen.sh
#
# Env (the systemd unit sets all three; the defaults make a hand-run work):
#   PRESENT_REPO          repo to measure          (default: this checkout)
#   PRESENT_ARTEFACT_DIR  where the pages land     (default: ~/.local/share/present)
#
# WHY THE ARTEFACTS DO NOT LIVE IN THE REPO: the server holds them open for
# weeks and a working tree is a place other sessions run `git checkout` in. A
# path under ~/.local/share is immune to that, the same reason the browser-bridge
# extension is unpacked there.
#
# ── 🔴 THE STALENESS CONTRACT, WRITER SIDE ───────────────────────────────────
# `generate.py` exits 3 and writes NOTHING when every fact came back UNMEASURED,
# and 4 when the page it produced would reach the network. Both are builds that
# must never reach a reader. So this wrapper writes to a TEMPORARY file and
# promotes it with `mv` (atomic rename within one filesystem) ONLY on exit 0.
#
# Two consequences, both deliberate:
#   * A failed run leaves the PREVIOUS page exactly where it was. Nothing is
#     deleted; there is always a last-good copy to read.
#   * A failed run exits non-zero, so systemd marks the unit failed and
#     `OnFailure = notify-failure@%n.service` toasts. The reader's half of that
#     signal is the age banner in `scripts/present/serve.py`; this is the
#     operator's half. Neither covers both audiences alone.
#
# A partially-written page is not a case that can arise: the reader only ever
# opens the promoted name, and rename is atomic.
#
# BOTH VARIANTS ARE PRODUCED BY THIS ONE RUN — the full page and the `--sanitize`
# shareable export. Producing the shareable copy from the same trigger is the
# point: a flag you have to remember later is a flag that gets forgotten, and
# the copy that leaves the LAN is the one it matters on.
#
# They are promoted INDEPENDENTLY. A sanitize failure must not withhold a good
# full page (and vice versa) — they are two artefacts, not two halves of one.
# The exit status still reports any failure, so a half-failed run is loud.
set -uo pipefail

# 🔴 `CDPATH=` on every `cd` whose output is CAPTURED. An exported CDPATH makes
# `cd` ECHO the directory it resolved, so `$(cd … && pwd)` comes back as TWO
# lines and every path built from it is a well-formed string naming nothing.
# Measured here: the first run of this script produced
# `cd: $'…/scripts/present\n…/scripts/present/../..'`. The interactive shell on
# this host exports CDPATH; a systemd unit does not — so the bug is invisible
# in the unit and fires for anyone running the wrapper by hand.
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DEFAULT="$(CDPATH='' cd -- "${SCRIPT_DIR}/../.." && pwd)"

REPO="${PRESENT_REPO:-$REPO_DEFAULT}"
OUT_DIR="${PRESENT_ARTEFACT_DIR:-${HOME}/.local/share/present}"

if [ ! -d "$REPO/.git" ] && [ ! -f "$REPO/.git" ]; then
  echo "present-regen: FATAL — PRESENT_REPO=$REPO is not a git checkout." >&2
  echo "present-regen:   every provenance row would come back UNMEASURED and the" >&2
  echo "present-regen:   generator would exit 3 on a tree that is simply the wrong one." >&2
  exit 2
fi

mkdir -p "$OUT_DIR" || {
  echo "present-regen: FATAL — could not create $OUT_DIR" >&2
  exit 2
}

# One temp dir for the run, removed on every exit path including a signal, so a
# failed build cannot leave a half-page lying next to the served one.
#
# 🔴 IT LIVES INSIDE $OUT_DIR, and that is the whole atomicity claim. `mv` is a
# rename() — genuinely atomic — only WITHIN one filesystem; across a boundary it
# silently degrades to copy-then-unlink, and a reader can then open a page that
# is half-written. The first cut used `${TMPDIR:-/tmp}`, which on this host is a
# tmpfs while ~/.local/share is on disk: every promote was a cross-device copy
# and the comment above it claimed atomicity it did not have.
#
# Dot-prefixed so it is invisible to a casual `ls`, and irrelevant to the server
# either way — `serve.py` answers an explicit two-name route table, so nothing in
# this directory is reachable except the two promoted pages. A SIGKILL can leave
# one of these behind; it is inert.
TMP_DIR="$(mktemp -d "$OUT_DIR/.regen.XXXXXX")" || exit 2
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT INT TERM

PY="${PRESENT_PYTHON:-python3}"

failures=0

# $1 = artefact basename, $2 = human label, rest = extra generator flags
build_one() {
  local name="$1" label="$2"; shift 2
  local tmp="$TMP_DIR/$name"
  local rc=0

  echo "present-regen: building $label -> $OUT_DIR/$name" >&2
  ( CDPATH='' cd -- "$REPO" && "$PY" -m scripts.present.generate \
      --repo "$REPO" -o "$tmp" "$@" ) || rc=$?

  if [ "$rc" -ne 0 ]; then
    # 3 = every fact UNMEASURED (no file written). 4 = the page would reach the
    # network. 2 = usage / unwritable. None of them may be promoted, and the
    # distinction is worth printing because they need different fixes.
    case "$rc" in
      3) echo "present-regen: 🔴 $label FAILED (exit 3) — every fact came back UNMEASURED." >&2
         echo "present-regen:   Nothing was written and the previous $name is UNTOUCHED." >&2 ;;
      4) echo "present-regen: 🔴 $label FAILED (exit 4) — the page is NOT self-contained." >&2
         echo "present-regen:   It would reach the network, so it was NOT promoted." >&2 ;;
      *) echo "present-regen: 🔴 $label FAILED (exit $rc)." >&2 ;;
    esac
    echo "present-regen:   The server keeps serving the last GOOD $name, with an" >&2
    echo "present-regen:   age banner once it crosses PRESENT_STALE_AFTER_SEC." >&2
    failures=$((failures + 1))
    return 1
  fi

  if [ ! -s "$tmp" ]; then
    # Exit 0 with no bytes is not a case generate.py has, but promoting an empty
    # file over a good page is unrecoverable, so it is checked rather than assumed.
    echo "present-regen: 🔴 $label reported success and produced no bytes — NOT promoted." >&2
    failures=$((failures + 1))
    return 1
  fi

  if ! mv -f "$tmp" "$OUT_DIR/$name"; then
    echo "present-regen: 🔴 could not promote $label into $OUT_DIR/$name" >&2
    failures=$((failures + 1))
    return 1
  fi
  echo "present-regen: promoted $name ($(wc -c <"$OUT_DIR/$name" | tr -d ' ') B)" >&2
  return 0
}

# 🔴 The two names here are the SAME two names `scripts/present/serve.py` puts
# in its ROUTES table. That relationship is pinned by
# scripts/tests/test_present_serve.py — a writer that renames its output while
# the server keeps asking for the old name produces a permanently "absent"
# page with a perfectly healthy timer, which is the seam neither file owns.
build_one "present.html"           "full page"
build_one "present-sanitized.html" "sanitized export" --sanitize

if [ "$failures" -ne 0 ]; then
  echo "present-regen: RESULT: FAIL ($failures of 2 artefacts not regenerated)" >&2
  exit 1
fi
echo "present-regen: RESULT: PASS (2 of 2 artefacts regenerated)" >&2
exit 0
