#!/usr/bin/env bash
# Seed the cluster store from the LOCAL one. Phase 1 (proposal §4).
#
# 🔴 THE LOCAL STORE IS THE ONLY COPY, AND THIS SCRIPT NEVER WRITES TO IT.
# `~/.claude/analyze-service-index/` is client-confidential, has no off-machine
# backup, and phase 1's whole premise is "local stays authoritative and
# untouched". So the source is read ONLY:
#   * rsync runs SOURCE -> STAGE. `--delete` is passed, and it is a statement
#     about the STAGE: rsync's --delete removes files from the DESTINATION that
#     the source does not have. It cannot reach the source.
#   * the source path is never the second argument to anything.
#   * the tar that pushes to the pod is created FROM THE STAGE, not the source,
#     so even a mis-typed pod name cannot involve the live store.
# `tests/test_subsystem_store_api.py::TestSeedIsNonDestructive` asserts this
# behaviourally — it hashes every file in the source either side of a run — and
# carries a positive control proving the hasher can see a change at all.
#
# Usage:
#   seed.sh --store <src> --stage <dir>            # stage only (what tests drive)
#   seed.sh --store <src> --stage <dir> --push <ns>/<deploy> [--dest /data]
#
# The two halves are split on purpose: staging is hermetic and testable, pushing
# needs a cluster. A green stage says nothing about the push, so the script
# prints them as separate lines and never one combined verdict.

set -euo pipefail

STORE=""
STAGE=""
PUSH=""
DEST="/data"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --store) STORE="${2:?--store needs a path}"; shift 2 ;;
    --stage) STAGE="${2:?--stage needs a path}"; shift 2 ;;
    --push)  PUSH="${2:?--push needs <namespace>/<deployment>}"; shift 2 ;;
    --dest)  DEST="${2:?--dest needs a path}"; shift 2 ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) echo "seed: unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$STORE" ]] || { echo "seed: --store is required" >&2; exit 2; }
[[ -n "$STAGE" ]] || { echo "seed: --stage is required" >&2; exit 2; }

# Guard 1 — the source exists. Reachable with a valid --stage, and it is its own
# failure: seeding from a path that is not there would create an EMPTY stage and
# then push it over a populated /data. That is the destructive shape this whole
# script is arranged to make impossible.
if [[ ! -d "$STORE" ]]; then
  echo "seed: store root not found: $STORE — nothing was staged, and nothing was pushed." >&2
  exit 3
fi

# Guard 2 — the source has at least one scope. Reachable with an existing
# directory, which is exactly the case guard 1 cannot see: an empty store root
# is a real state (a fresh checkout, a wrong path that happens to exist) and
# staging it would be a silent wipe of the destination.
shopt -s nullglob
scopes=("$STORE"/*/)
shopt -u nullglob
if [[ ${#scopes[@]} -eq 0 ]]; then
  echo "seed: store root $STORE holds NO scope directories — refusing to stage an empty tree over a populated one." >&2
  exit 4
fi

mkdir -p "$STAGE"

# 🔴 SOURCE FIRST, STAGE SECOND. --delete makes the STAGE match the source; it
# is not a flag about the source and cannot remove anything from it.
rsync -a --delete "$STORE"/ "$STAGE"/

staged_scopes=0
staged_entries=0
for d in "$STAGE"/*/; do
  [[ -d "$d" ]] || continue
  staged_scopes=$((staged_scopes + 1))
  n=$(find "$d" -maxdepth 1 -name '*.md' -type f | wc -l)
  staged_entries=$((staged_entries + n))
  printf 'seed: staged scope %-28s entries=%s\n' "$(basename "$d")" "$n"
done

# The count is printed BESIDE what produced it, never alone. A bare "0 entries"
# from a run that walked nothing reads exactly like a genuinely empty store —
# the same silent-zero the API's four-state rule exists to prevent.
echo "seed: STAGED scopes=$staged_scopes entries=$staged_entries from=$STORE to=$STAGE"

if [[ ${staged_scopes} -eq 0 ]]; then
  echo "seed: staged 0 scopes from a source that had ${#scopes[@]} — the copy did not happen." >&2
  exit 5
fi

if [[ -z "$PUSH" ]]; then
  echo "seed: PUSH skipped (no --push given). This run proves nothing about any pod."
  exit 0
fi

ns="${PUSH%%/*}"
deploy="${PUSH##*/}"
if [[ "$ns" == "$PUSH" || -z "$ns" || -z "$deploy" ]]; then
  echo "seed: --push wants <namespace>/<deployment>, got: $PUSH" >&2
  exit 2
fi

pod=$(kubectl -n "$ns" get pod -l "app=$deploy" -o jsonpath='{.items[0].metadata.name}')
[[ -n "$pod" ]] || { echo "seed: no pod for app=$deploy in ns=$ns" >&2; exit 6; }

echo "seed: pushing $STAGE -> $ns/$pod:$DEST"
# 🔴 THE MEMBER LIST IS THE SCOPE DIRECTORIES, NOT `.`, AND THE EXTRACT DROPS
# OWNER AND MODE. MEASURED, not defensive: `tar -cf - .` puts a `./` member in
# the archive, and the pod runs as UID 65532 against a PVC root the kubelet
# created as root, so the extract ends with
#     tar: .: Cannot utime: Operation not permitted
#     tar: .: Cannot change mode to rwxr-xr-x: Operation not permitted
#     tar: Exiting with failure status
# — after the CONTENT has already landed. That is the worst possible shape: a
# non-zero exit on a push that mostly worked, which reads as "nothing was
# seeded" and invites a retry that changes nothing.
members=()
for d in "$STAGE"/*/; do members+=("$(basename "$d")"); done
tar -C "$STAGE" -cf - -- "${members[@]}" \
  | kubectl -n "$ns" exec -i "$pod" -- \
      tar -C "$DEST" --no-same-owner --no-same-permissions -xf -

remote_entries=$(kubectl -n "$ns" exec "$pod" -- \
  find "$DEST" -maxdepth 2 -name '*.md' -type f | wc -l)
echo "seed: PUSHED pod=$pod dest=$DEST remote_entries=$remote_entries local_entries=$staged_entries"

if [[ "$remote_entries" != "$staged_entries" ]]; then
  echo "seed: MISMATCH — remote holds $remote_entries entry files, the stage held $staged_entries." >&2
  exit 7
fi
echo "seed: OK"
