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

_seed_tmp=$(mktemp -d)
trap 'rm -rf "$_seed_tmp"' EXIT
staged_list="$_seed_tmp/staged"

# 🔴 ONE PREDICATE FOR THE COUNT AND FOR THE COMPARISON, because two walks with
# different rules DID disagree and the script said OK anyway.
#
# `staged_entries` used to be `find "$d" -maxdepth 1` over `"$STAGE"/*/`. The
# trailing slash makes a SYMLINKED scope resolve, so that walk counted through
# it — while the comparison's `find .` does not descend a symlink and `tar`
# archives the link rather than its target. Measured: a store with `normal/n.md`
# and a symlinked `symscope/` printed
#
#     seed: PUSHED … remote_entries=1 staged_entries=2
#     seed: OK all 2 staged entries are present on the pod        (rc 0)
#
# — the two numbers visibly disagreeing one line apart, one entry silently not
# landed, and a completeness claim over it. 🔴 AND THE OLD COUNT-EQUALITY CHECK
# WOULD HAVE CAUGHT THIS (1 != 2 -> exit 7), so the claim this block used to
# make — that it "fails strictly more broken pushes than the count did" — was
# FALSE. It is true only now that both sides come from `_shippable_entries`.
#
# The predicate is "what the tar will ACTUALLY land": depth-2 `*.md` regular
# files, under a scope directory that is neither a dot-directory (the member
# list is `"$STAGE"/*/` without `dotglob`) nor a symlink (`find` does not
# descend one, and tar ships the link itself).
_shippable_entries() {
  ( cd "$1" && find . -mindepth 2 -maxdepth 2 ! -path './.*' -name '*.md' -type f ) \
    | sed 's|^\./||' | LC_ALL=C sort
}

_shippable_entries "$STAGE" > "$staged_list"
staged_entries=$(wc -l < "$staged_list" | tr -d ' ')

staged_scopes=0
excluded=()
for d in "$STAGE"/*/; do
  [[ -d "$d" ]] || continue
  staged_scopes=$((staged_scopes + 1))
  name=$(basename "$d")
  # `index(...)==1` is a LITERAL PREFIX test on purpose — twice over. A scope
  # name is not a regex, so `grep -c "^$name/"` mis-counts one containing `.` or
  # `[`; and `index(...) > 0` would be a SUBSTRING test, which with scopes `foo`
  # and `xfoo` counts `xfoo/n.md` against `foo`.
  # 🔴 Passed through ENVIRON, not `awk -v`: `-v` interprets escape sequences,
  # so a scope literally named `a\tb` would be searched for as `a<TAB>b` and
  # report entries=0 beside a non-zero total.
  n=$(P="$name/" awk 'index($0, ENVIRON["P"]) == 1 {c++} END {print c + 0}' "$staged_list")
  printf 'seed: staged scope %-28s entries=%s\n' "$name" "$n"
done

# 🔴 LOUD, NOT SILENT. A scope that ships nothing used to produce either a wrong
# MISMATCH (dot-directory) or a false OK (symlink). Excluding it from the
# verdict is correct; saying nothing about it would just move the lie.
#
# 🔴 BOTH ARMS USE THE SAME PREDICATE — "does it actually hold a `.md`" — and
# that is a fix, not symmetry for its own sake. The symlink arm used to fire
# unconditionally, so a symlinked scope holding NO markdown produced the header
# "1 scope director(ies) hold .md files that will NOT be shipped" over a
# directory that holds none. In a change whose whole subject is not claiming
# what has not been established, that sentence was exactly the wrong one to
# leave. `find "$d"` with the trailing slash follows the link deliberately here:
# the question is what the operator would LOSE, which is the target's contents.
_holds_md() { [[ -n "$(find "$1" -maxdepth 1 -name '*.md' -print -quit 2>/dev/null)" ]]; }

# `shopt -p` captures the CALLER's settings and the eval restores exactly those
# — `shopt -u` unconditionally would clear an option the caller had set.
#
# 🔴 `|| true` IS LOAD-BEARING, NOT DEFENSIVE. `shopt -p` EXITS 1 when any named
# option is UNSET — which is the ordinary case here — so under `set -e` the bare
# assignment aborted the script after the per-scope lines and before the stamp.
# Measured: rc 1, and 20 of the file's seed tests went red at once. It still
# PRINTS the correct restore commands on that non-zero exit, so the capture is
# sound; only the status is misleading.
_shopt_saved=$(shopt -p nullglob dotglob || true)
shopt -s nullglob dotglob
for d in "$STAGE"/*/; do
  name=$(basename "$d")
  if [[ "$name" == .* ]]; then
    _holds_md "$d" && excluded+=("$name (dot-directory — not in the tar member list)")
  elif [[ -L "${d%/}" ]]; then
    _holds_md "$d" && excluded+=("$name (symlink — tar ships the link, not its contents)")
  fi
done
eval "$_shopt_saved"
if [[ ${#excluded[@]} -gt 0 ]]; then
  echo "seed: NOTE ${#excluded[@]} scope director(ies) hold .md files that will NOT be shipped, and are excluded from the count and the verdict:"
  printf 'seed:   %s\n' "${excluded[@]}"
fi

# The count is printed BESIDE what produced it, never alone. A bare "0 entries"
# from a run that walked nothing reads exactly like a genuinely empty store —
# the same silent-zero the API's four-state rule exists to prevent.
echo "seed: STAGED scopes=$staged_scopes entries=$staged_entries from=$STORE to=$STAGE"

if [[ ${staged_scopes} -eq 0 ]]; then
  echo "seed: staged 0 scopes from a source that had ${#scopes[@]} — the copy did not happen." >&2
  exit 5
fi

# 🔴 DATE THE COPY, IN THE COPY. The server cannot otherwise know how old the
# tree it serves is, and it renders "ALL N entries ... none omitted" over it —
# a completeness claim that reads identically whether the content was copied
# this minute or four days ago. MEASURED 2026-08-20: the public endpoint served
# `ALL 5 entries in devrc/` against a source holding 9, with nothing in the
# payload able to say so. `server.snapshot_freshness` reads this file, and
# reports its ABSENCE as `seeded=UNSTAMPED` rather than omitting the line, so an
# unstamped store is loud rather than indistinguishable from a current one.
#
# 🔴 WRITTEN IN THE STAGING HALF, DELIBERATELY — not next to the push. Two
# reasons, and the first is a bug this was moved to fix:
#   * `--push` is optional and returns early, so a stamp written down there is
#     absent from every stage-only run — including the hermetic tests, which is
#     how the omission would have gone unnoticed;
#   * it dates the CONTENT SNAPSHOT, which is the honest thing for it to date.
#     The tar is built from the stage, so stamp and content are consistent even
#     if the push happens later; stamping at transfer time would claim currency
#     the bytes do not have.
#
# Into the STAGE, never the source — the source is read-only here (see header).
printf '%s staged_entries=%s host=%s\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$staged_entries" "${HOSTNAME:-unknown}" \
  > "$STAGE/.seed-stamp"
echo "seed: STAMPED $STAGE/.seed-stamp"

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
# 🔴 THE STAMP MUST BE NAMED EXPLICITLY. This member list is built from
# `"$STAGE"/*/` — DIRECTORIES ONLY — so a top-level file is silently left out
# of the archive, and the push would land content with no date on it while
# reporting OK. It is not `*.md`, so it does not disturb the entry counts the
# mismatch guard below compares.
members=()
for d in "$STAGE"/*/; do members+=("$(basename "$d")"); done
members+=(".seed-stamp")
tar -C "$STAGE" -cf - -- "${members[@]}" \
  | kubectl -n "$ns" exec -i "$pod" -- \
      tar -C "$DEST" --no-same-owner --no-same-permissions -xf -

# 🔴 CONTAINMENT ON NAMES, NOT EQUALITY OF COUNTS. This guard used to read
# `remote_entries != staged_entries -> exit 7`, which is only correct while
# exactly ONE host ever seeds.
#
# The store is PER-HOST and unreplicated, and the extract adds and overwrites
# but never deletes — so a second host's entries legitimately sit in $DEST that
# this stage never held. Counting then failed a CORRECT push, and did it AFTER
# the content had already landed: a failure verdict on a push that worked, which
# invites a retry that changes nothing. That is the same shape the tar member
# list above was fixed for. MEASURED 2026-08-28: this host staged 129 over a pod
# holding 75 (a strict subset, so equality happened to hold); the other host's
# ~26 would have staged against a remote of 129+ and exited 7 every time,
# leaving criterion 8 unfinishable by the tool meant to finish it.
#
# Equality was also WEAKER than it looked in the single-host case it was written
# for: 129 staged against 129 remote passes while one staged file is missing and
# one foreign file makes up the number. The question a push actually has to
# answer is "did everything I staged land?" — a SUBSET check on NAMES, which is
# also the `comm -23` the re-seed card prescribes.
#
# ⚠ CORRECTION, measured: this comment used to end "it fails strictly more
# broken pushes than the count did". That was FALSE as first written. A
# SYMLINKED scope produced `remote_entries=1 staged_entries=2` and then
# `seed: OK`, rc 0 — a push the old count-equality check WOULD have failed
# (1 != 2). Containment only dominates the count once BOTH sides come from one
# predicate, which is what `_shippable_entries` above now guarantees; before
# that, the two walks disagreed and the comparison believed the wrong one.
#
# Both sides use `-mindepth 2 -maxdepth 2` so they answer the SAME question —
# `<scope>/<entry>.md` and nothing else. The old remote count used `-maxdepth 2`
# alone, which would also have counted a stray top-level `*.md` the stage cannot
# contain.
remote_list="$_seed_tmp/remote"
missing_f="$_seed_tmp/missing";  foreign_f="$_seed_tmp/foreign"

# 🔴 `$staged_list` IS NOT RECOMPUTED HERE. It is the same file
# `_shippable_entries` wrote before the push, which is what makes
# `staged_entries` and the compared set one population rather than two walks
# that agree by luck. See that function for the symlink case where they did not.
#
# The remote side runs the IDENTICAL expression, on purpose: same `-mindepth 2
# -maxdepth 2` (a `<scope>/<entry>.md` and nothing above or below it), same
# dot-directory exclusion (the tar cannot put one there), same `-type f` (a
# DIRECTORY named `*.md` is not an entry — the server 503'd on exactly that).
# Any clause dropped from one side and not the other silently changes what the
# comparison means, so `test_the_two_find_expressions_are_IDENTICAL` pins them
# against each other.
kubectl -n "$ns" exec "$pod" -- \
  sh -c "cd '$DEST' && find . -mindepth 2 -maxdepth 2 ! -path './.*' -name '*.md' -type f" \
  | sed 's|^\./||' | LC_ALL=C sort > "$remote_list"

remote_entries=$(wc -l < "$remote_list" | tr -d ' ')

# 🔴 `LC_ALL=C` ON `comm` TOO, NOT ONLY ON THE SORTS. Both lists are sorted
# `LC_ALL=C` above; GNU `comm` compares AND order-checks in the AMBIENT locale,
# so C-sorted input is "not in sorted order" to a `comm` running under
# en_US.UTF-8 — which is this host (`LANG=en_US.UTF-8`, `LC_COLLATE` unset).
#
# It fires only when the two conditions the multi-host case guarantees are BOTH
# met: an UNPAIRABLE line (GNU comm arms the order check only after one) and an
# adjacency where C and locale order disagree — e.g. `<scope>/README.md` beside
# `<scope>/backblaze.md`, which the real store is full of. So the FIRST push
# after another host seeds would abort at `set -e` on comm's rc=1, printing
# three "not in sorted order" diagnostics and NO verdict at all: no PUSHED, no
# NOTE, no OK, no MISMATCH — content landed, exit code unexplained. Exactly the
# "failure verdict on a push that worked" this block was written to remove.
#
# The PR's own live run could not see it: it had staged == remote exactly, so
# nothing was unpairable and the order check never armed.
#
# 🔴 `--nocheck-order` is NOT the fix — it silences the diagnostic while the SET
# OPERATION still collates in the ambient locale and returns the WRONG CONTENT
# (measured: staged `[a/README.md, a/b.md]` vs remote `[a/b.md]` reports BOTH as
# missing). The collation has to be C on both sides of the comparison.
LC_ALL=C comm -23 "$staged_list" "$remote_list" > "$missing_f"
LC_ALL=C comm -13 "$staged_list" "$remote_list" > "$foreign_f"
n_missing=$(wc -l < "$missing_f" | tr -d ' ')
n_foreign=$(wc -l < "$foreign_f" | tr -d ' ')

echo "seed: PUSHED pod=$pod dest=$DEST remote_entries=$remote_entries staged_entries=$staged_entries"

# Printed BESIDE the verdict, never instead of it: a pod holding entries this
# host does not have is the NORMAL multi-host state, and silence about it would
# make the two numbers above look like a discrepancy nobody explained.
if [[ "$n_foreign" -gt 0 ]]; then
  echo "seed: NOTE $n_foreign entry file(s) on the pod were not staged by this host — expected when another host also seeds. They were left untouched, not deleted."
fi

if [[ "$n_missing" -gt 0 ]]; then
  echo "seed: MISMATCH — $n_missing of $staged_entries staged entry file(s) did NOT land on the pod:" >&2
  sed 's/^/  /' "$missing_f" >&2
  exit 7
fi
echo "seed: OK all $staged_entries staged entries are present on the pod"
