#!/usr/bin/env bash
# Seed the cluster store from the LOCAL one. Phase 1 (proposal §4).
#
# 🔴 THE LOCAL STORE IS *NOT* AUTHORITATIVE ANY MORE — THE POD IS. This header
# said the opposite until 2026-09-04, and the sentence outlived the fact by the
# whole life of the Cairn cutover, which made the hosted store canonical and
# froze `~/.claude/analyze-service-index/` to a per-host mirror.
#
# What still holds, unchanged: THIS SCRIPT NEVER WRITES TO THE LOCAL STORE. It is
# client-confidential and not re-derivable by re-running recon, so it is read
# ONLY, for the reasons enumerated below.
#
# What CHANGED is the direction of the risk. The push can no longer be reasoned
# about as "authoritative source refreshing a derivative": it is a DERIVATIVE
# overwriting the AUTHORITY, and the extract adds and overwrites but never
# deletes. That is why the pre-flight before the tar refuses any staged ENTRY
# FILE whose bytes differ from the pod's
#
# 🔴 AND THAT IS ~12% OF WHAT THE PUSH OVERWRITES — SAY SO. MEASURED on the live
# pod 2026-09-04: 1,755 files under /data, of which 211 are the depth-2
# `<scope>/<entry>.md` this guard compares. The tar members are whole scope
# DIRECTORIES, so the push also carries every depth-3+ path — including FIFTEEN
# per-scope `.git` repositories, one measurably divergent (pod
# `devrc/.git/refs/heads/trunk` = 68aef530, this host = e2f21cf8). A push that
# passes this guard still rewinds that ref. Widening the comparison is the real
# fix; until then the limit is stated rather than left to read as a guarantee — measured 2026-09-02/03, a re-seed would
# have reverted five pod-newer bullets, two of them `OPEN:` -> `RESOLVED`
# closures, and reported success.
#
# ⚠ This block used to open "THE LOCAL STORE IS THE ONLY COPY … has no
# off-machine backup". Both halves are now false — daily age-encrypted bundles go
# to MinIO (`analyze-service-index-backup.service`) and the pod this script seeds
# holds a copy too. Neither weakens the rule below: the OTHER copies are lagging
# derivatives (bundle = last daily commit, pod = last seed), so a write that
# corrupted the source would be replicated outward, not repaired from them.
#
# So the source is read ONLY:
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
#   … --allow-overwrite   proceed even when staged entries differ from the pod's
#                         copy. DELIBERATE override: the pre-flight refuses by
#                         default because the pod is authoritative post-cutover.
#                         It still PRINTS what it replaced.
#
# The two halves are split on purpose: staging is hermetic and testable, pushing
# needs a cluster. A green stage says nothing about the push, so the script
# prints them as separate lines and never one combined verdict.

set -euo pipefail

STORE=""
STAGE=""
PUSH=""
DEST="/data"
ALLOW_OVERWRITE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --store) STORE="${2:?--store needs a path}"; shift 2 ;;
    --stage) STAGE="${2:?--stage needs a path}"; shift 2 ;;
    --push)  PUSH="${2:?--push needs <namespace>/<deployment>}"; shift 2 ;;
    --dest)  DEST="${2:?--dest needs a path}"; shift 2 ;;
    --allow-overwrite) ALLOW_OVERWRITE=1; shift ;;
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
# 🔴 THREE STATES, NOT TWO — AND THE THIRD IS WHY. This probe has now been
# wrong in both directions, one round apart:
#
#   * with `2>/dev/null` it was SILENT about a symlinked scope whose target is
#     unreadable — no NOTE, rc 0, `seed: OK`, and the operator loses that
#     scope's contents with nothing to say so. Wrong direction under a block
#     headed "LOUD, NOT SILENT".
#   * announcing on any error then fixed the silence but reused the "holds .md
#     files" wording, so an unreadable target with NO markdown in it was
#     announced as holding some — the exact unestablished claim this whole
#     change exists to stop making, reintroduced by the fix for the previous
#     defect.
#
# So the caller gets a state, not a boolean, and picks wording that matches what
# was actually established: 0 = holds markdown · 1 = does not · 2 = COULD NOT
# CHECK. `find`'s own exit status is the discriminator (measured: rc 1 on an
# unreadable directory, rc 0 on a readable one), which is why stderr is
# discarded rather than captured — the rc carries the whole signal, and
# capturing the text only risked it reaching a message.
#
# `-maxdepth 1` is deliberate: only depth-1 `*.md` inside the scope is shippable
# at all, so widening it would announce a scope over files that no scope, dot or
# otherwise, could ever ship.
#
# 🔴 EVIDENCE BEATS THE ERROR, and the order matters. `|| return 2` alone
# discards `$out` unread, so a probe that BOTH matched a file AND exited
# non-zero would report "could not check" over a match it actually had. Not
# reachable at `-maxdepth 1` — the only rc≠0 sources there are the starting
# point itself (measured across GNU find 4.11 and bfs 4.1: an unreadable
# SUBdirectory, a broken symlink child and a child directory named `*.md` all
# exit 0) — but it becomes reachable the moment someone widens the depth, and
# nothing in the old comment said the discriminator depended on that. Checking
# the match first makes the function correct at any depth.
_md_state() {
  local out rc
  out=$(find "$1" -maxdepth 1 -name '*.md' -print -quit 2>/dev/null); rc=$?
  [[ -n "$out" ]] && return 0
  [[ $rc -ne 0 ]] && return 2
  return 1
}

# 🔴 UNCONDITIONAL `-u`, AND A RESTORE-THE-CALLER'S-SETTINGS VERSION WAS TRIED
# HERE AND REVERTED ON EVIDENCE. It looked tidier and was measurably worse:
#
#   * The tar member list below is `"$STAGE"/*/`, correct ONLY while `dotglob`
#     is OFF. Restoring a caller who had it ON made a dot-scope SHIP — measured
#     under `BASHOPTS=dotglob`, `.hidden/` landed on the pod while the remote
#     listing's `! -path './.*'` still excluded it, so it was shipped, never
#     verified, and the NOTE saying "not in the tar member list" became FALSE.
#     The unconditional reset makes that independent of the ambient shell.
#   * The property it claimed to protect is UNOBSERVABLE: this file is only ever
#     run as `bash seed.sh` (checked every reference), and shell options do not
#     propagate out of a script. The sole consumer of the restore was the member
#     list twenty lines below it.
#   * And it was already violated for the other half of the pair — line 62 does
#     an unconditional `shopt -u nullglob` long before here, so a caller's
#     `nullglob` is gone regardless.
shopt -s nullglob dotglob
for d in "$STAGE"/*/; do
  name=$(basename "$d")
  # `|| st=$?` keeps a non-zero state out of errexit's way — a bare call would
  # abort the script on the ordinary "does not hold markdown" answer.
  st=0
  if [[ "$name" == .* ]]; then
    _md_state "$d" || st=$?
    case $st in
      0) excluded+=("$name (dot-directory — not in the tar member list)") ;;
      2) excluded+=("$name (dot-directory — UNREADABLE, contents could not be checked)") ;;
    esac
  elif [[ -L "${d%/}" ]]; then
    _md_state "$d" || st=$?
    case $st in
      0) excluded+=("$name (symlink — tar ships the link, not its contents)") ;;
      2) excluded+=("$name (symlink — target UNREADABLE, contents could not be checked)") ;;
    esac
  fi
done
shopt -u nullglob dotglob
if [[ ${#excluded[@]} -gt 0 ]]; then
  # 🔴 THE HEADER ASSERTS NOTHING ABOUT CONTENTS. It used to say the listed
  # directories "hold .md files" — true for the two states that established it,
  # FALSE for the `could not check` one. Each entry states its own reason.
  # 🔴 "CONTRIBUTE NO ENTRIES", NOT "WILL NOT SHIP" — the third wording of this
  # line, and the second correction. "hold .md files" asserted contents the
  # probe could not always read; "will NOT ship" then over-claimed on a
  # different axis, because a symlinked scope DOES ship — as a symlink. Measured:
  # the tar lands `symscope` on the pod as a link, which the very next line of
  # output says ("tar ships the link, not its contents"), so the block
  # contradicted itself two rows apart. What is true of every state that reaches
  # here is that the directory contributes nothing to the entry set.
  # ⚠ "the ENTRY count", not "the count". The second clause was left unexamined
  # while the first was corrected twice, and it is loose: `staged_scopes` counts
  # a SYMLINKED scope and not a dot one, so a store of 3 scope dirs can print
  # "2 … excluded" beside `scopes=2` and the numbers do not reconcile. What is
  # excluded on every arm is the ENTRY count and the verdict.
  echo "seed: NOTE ${#excluded[@]} scope director(ies) contribute NO entries, and are excluded from the entry count and the verdict:"
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

echo "seed: preparing push $STAGE -> $ns/$pod:$DEST"
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
# --- 🔴 REFUSE TO OVERWRITE A POD ENTRY WHOSE BYTES DIFFER ------------------- #
# THE CUTOVER INVERTED THE AUTHORITY AND THIS SCRIPT WAS NEVER UPDATED. The tar
# extract below "adds and overwrites but never deletes" — which was safe while
# the LOCAL store was authoritative, and is silent data loss now that the pod is.
# A shared entry whose pod copy has moved on (a bullet appended through
# `cairn append`, an `OPEN:` rewritten `RESOLVED <sha>:`) is replaced by this
# host's older copy, and the verdict below still prints OK because the NAME
# landed.
#
# MEASURED 2026-09-02/03 on the real store: of 25 bullets present locally but not
# on the pod, FIVE were the pod being NEWER — including two `OPEN:` -> `RESOLVED`
# closures carrying ~20 lines of later corrections. A re-seed would have reverted
# all five and reported success.
#
# 🔴 WHY THIS IS A PRE-FLIGHT AND NOT A LOUDER VERDICT. The existing containment
# check runs AFTER the extract, so by the time it can speak the bytes are gone.
# The only useful place for this question is before the push.
#
# 🔴 WHY IT COMPARES BYTES AND NOT MTIME OR "NEWNESS". We cannot order two copies
# — a local edit not yet pushed and a pod edit not yet pulled both read as
# "different". Post-cutover the pod is the authority, so ANY difference means
# this push would overwrite the authoritative copy with a derivative. That is
# exactly what must not happen silently, so difference is the right predicate and
# it needs no clock.
#
# 🔴 IT ASKS THE POD ABOUT `$staged_list` AND ADDS NO THIRD `find`. Reusing the
# one list `_shippable_entries` already wrote keeps this comparison on the SAME
# population as the verdict below — the symlink case in that function is what
# happens when two walks are allowed to disagree — and leaves
# `test_the_two_find_expressions_are_IDENTICAL` pinning exactly two listings.
# `test -f` on the pod yields the INTERSECTION for free: a staged path the pod
# does not have is a pure addition and cannot clobber anything.
_clobber_f="$_seed_tmp/clobber"; : > "$_clobber_f"
_n_present=0
_n_answered=0
if [[ -s "$staged_list" ]]; then
  _local_h="$_seed_tmp/local-h"; _remote_h="$_seed_tmp/remote-h"
  _probe_raw="$_seed_tmp/probe-raw"
  # 🔴 `-I{}` ON THE LOCAL SIDE TOO, MATCHING THE REMOTE. Bare `xargs sha256sum`
  # word-splits and parses quotes: a staged `sc/two words.md` became two missing
  # arguments (rc 123) and `sc/it's.md` an unmatched quote (rc 1) — both with NO
  # `seed:` line, the failure shape the `|| :` note below exists to prevent,
  # reintroduced on the other side. Base handled both at rc 0.
  # 🔴 `-d '\n'`, NOT JUST `-I{}`. `-I{}` stops WORD-SPLITTING but leaves
  # `xargs`'s INPUT QUOTE PARSING on, so a staged `sc/it's.md` still died with
  # `xargs: unmatched single quote`, rc 1, no `seed:` line — byte-identical to
  # base. An earlier round claimed `-I{}` fixed the quote case; MEASURED, it did
  # not, and the claim shipped without being re-run. Only `-d` (or `-0`) turns
  # the quote parsing off.
  #
  # 🔴 AND THE KEY IS TAB-SEPARATED, because `awk '{print $2" "$1}'` rebuilt the
  # line from FIELDS and so truncated any path at its first blank. MEASURED: two
  # BYTE-IDENTICAL entries `sc/two words.md` and `sc/two other.md` both collapsed
  # to key `sc/two`, the join degenerated into a cross-product, and the guard
  # printed `differing=2` and refused — naming `sc/two` twice, a path that does
  # not exist. A confident false refusal whose only offered remedy is
  # `--allow-overwrite` is worse than the crash it replaced. `sub()` strips the
  # digest and its separator and leaves the REST OF THE LINE intact.
  ( cd "$STAGE" && xargs -r -d '\n' -I{} sha256sum {} ) < "$staged_list" \
    | awk '{h=$1; sub(/^[^ ]+ +/,""); print $0"\t"h}' | LC_ALL=C sort > "$_local_h"
  # 🔴 EVERY PATH IS ANSWERED — a hash, ABSENT, or UNREADABLE. The probe used to
  # emit a line only for files the pod HAS, so "the pod holds none of them" and
  # "the probe never ran" were the same observation (zero lines, rc 0) and the
  # guard read both as "nothing differs". MEASURED in review: with a silenced
  # probe the push destroyed the pod's newer bytes and printed `seed: OK`. It is
  # REACHABLE: this is the only command whose input reaches the pod over stdin,
  # and `xargs -r` on a stream that closed early is silence at rc 0 BY DESIGN.
  #
  # 🔴 REFUSING ON "0 PRESENT" WAS THE WRONG FIX. A pod holding none of the
  # staged entries is the ORDINARY first-seed case — measured, that rule failed
  # 18 legitimate tests. The answerable question is whether the probe SAW the
  # whole list, so ABSENT is an answer and a MISSING line is the fault.
  #
  # 🔴 THE INNER `sh` MUST EXIT 0 FOR EVERY PATH: `xargs` exits 123 when any child
  # does, and a path the pod lacks is ordinary. The `if/else` is what guarantees
  # that now — an earlier revision used a bare `|| :` and this comment still
  # described it two rounds after it was replaced. UNREADABLE cannot equal a hex
  # digest, so such an entry always lands in the clobber set rather than being
  # silently treated as a pure addition.
  # `_ {}` passes the path as "$1" so it is never re-parsed as shell text.
  kubectl -n "$ns" exec -i "$pod" -- \
    sh -c "cd '$DEST' && xargs -r -d '\n' -I{} sh -c 'if [ -f \"\$1\" ]; then sha256sum \"\$1\" 2>/dev/null || echo \"UNREADABLE  \$1\"; else echo \"ABSENT  \$1\"; fi' _ {}" \
    < "$staged_list" \
    | awk '{h=$1; sub(/^[^ ]+ +/,""); print $0"\t"h}' | LC_ALL=C sort > "$_probe_raw"
  _n_answered=$(wc -l < "$_probe_raw" | tr -d ' ')
  # 🔴 `|| [ $? -eq 1 ]`, NOT `|| :`. `grep -v` exits 1 on NO MATCH (the ordinary
  # first-seed case, so it must be tolerated) and 2 on an I/O ERROR. `|| :`
  # swallowed both: an error left `_remote_h` empty, the join empty, `differing=0`
  # and the push proceeding — the silent-zero shape this round exists to remove,
  # one line below the gate that removes it.
  LC_ALL=C grep -v "$(printf '\t')ABSENT$" "$_probe_raw" > "$_remote_h" || [ $? -eq 1 ]
  _n_present=$(wc -l < "$_remote_h" | tr -d ' ')
  # 🔴 `LC_ALL=C` on the join too. GNU join order-checks in the AMBIENT locale,
  # so C-sorted input is "not sorted" to a join under en_US.UTF-8 — this host.
  # MEASURED: it MISSES the differing pair AND exits 1, which `set -e` turns
  # into a run with no verdict. Needs an unpairable line plus a README/lowercase
  # adjacency, which the real store is full of.
  # `-t` a literal TAB so the key is the whole path, spaces included — join's
  # default whitespace splitting would re-introduce exactly the truncation the
  # `awk` above was fixed to stop.
  LC_ALL=C join -t "$(printf '\t')" "$_local_h" "$_remote_h" \
    | awk -F'\t' '$2 != $3 {print $1}' > "$_clobber_f"
fi
_n_clobber=$(wc -l < "$_clobber_f" | tr -d ' ')

# 🔴 PRINTED ON EVERY PATH, NOT ONLY ON REFUSAL — this file's own silent-zero
# rule ("a bare 0 from a run that walked nothing reads exactly like a genuinely
# empty store"), applied to the guard itself.
echo "seed: PRE-FLIGHT staged=$staged_entries answered=$_n_answered present_on_pod=$_n_present differing=$_n_clobber"
if [[ "$_n_answered" -ne "$staged_entries" ]]; then
  echo "seed: PRE-FLIGHT COULD NOT COMPARE — asked the pod about $staged_entries staged entries, got $_n_answered answers." >&2
  echo "seed:   Every path is answered (a hash, ABSENT, or UNREADABLE), so a SHORT reply means the" >&2
  echo "seed:   probe did not see the whole list — stdin is piped to the pod, and a stream that" >&2
  echo "seed:   closes early is silence at rc 0, indistinguishable from 'nothing differs'." >&2
  echo "seed: NOTHING WAS PUSHED." >&2
  exit 9
fi

if [[ "$_n_clobber" -gt 0 && "$ALLOW_OVERWRITE" != "1" ]]; then
  echo "seed: REFUSING — $_n_clobber staged entry file(s) EXIST ON THE POD WITH DIFFERENT BYTES." >&2
  sed 's/^/  /' "$_clobber_f" >&2
  echo "seed: the pod is the authority since the Cairn cutover; this push would replace its copy" >&2
  echo "seed:   with this host's, and the verdict would still say OK because the NAME landed." >&2
  # 🔴 THE REMEDY NAMED HERE MUST BE ONE THAT CAN CLEAR THE REFUSAL. This first
  # read "reconcile first (`cairn sync`, then `cairn put` …)". MEASURED: neither
  # touches what this guard compares — `cairn sync` refreshes
  # ~/.cache/subsystem-store, NOT the --store tree, and `cairn put` writes the
  # POD, moving it FURTHER from the mirror. The mirror is frozen read-only by
  # cairn-cutover.py P5. A refusal whose prescribed fix cannot work is how
  # --allow-overwrite becomes the habitual invocation.
  echo "seed: 🔴 A PUSH IS PROBABLY NO LONGER THE RIGHT VERB FROM THIS HOST. The local tree is a" >&2
  echo "seed:   FROZEN pre-cutover mirror; the pod has moved on via \`cairn append\`/\`cairn put\`." >&2
  echo "seed:   No shipped tool refreshes the mirror FROM the pod, so this will not clear itself" >&2
  echo "seed:   and re-running changes nothing." >&2
  echo "seed:   To publish specific local content, send it entry-by-entry: \`cairn put\` (replace)" >&2
  echo "seed:   or \`cairn create\` (new) — those go through the API and cannot silently revert." >&2
  echo "seed:   --allow-overwrite is for a DELIBERATE decision that this host's staged copy wins." >&2
  echo "seed: NOTHING WAS PUSHED." >&2
  exit 8
fi
if [[ "$_n_clobber" -gt 0 ]]; then
  echo "seed: WARNING --allow-overwrite given; REPLACING $_n_clobber pod entry file(s) with this host's copy:"
  sed 's/^/  /' "$_clobber_f"
fi

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
