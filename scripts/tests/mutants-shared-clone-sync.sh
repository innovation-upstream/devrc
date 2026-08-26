#!/usr/bin/env bash
# Mutation battery for scripts/lib/shared_clone_sync.py + scripts/sync-clones.py.
#
# Not run by CI — an author/reviewer instrument, kept in-tree so the claim
# "mutation-verified" can be RE-DERIVED instead of believed. Same shape and same
# reasons as scripts/tests/mutants-base-clone.sh; read that one too.
#
#   nix develop --command bash scripts/tests/mutants-shared-clone-sync.sh
#
# It EDITS the two files in place and restores them from a backup on exit
# (including on ^C). Run it on a clean tree.
#
# 🔴 HARNESS HISTORY — READ THIS BEFORE TRUSTING ANY VERDICT FROM ANY VERSION OF
# THIS FILE. The first version scored ALL SIXTEEN mutants SURVIVED while every
# one of them was in fact KILLED. It parsed pytest's summary with
#
#     sed -n 's/.*[^0-9]\([0-9]\+\) failed.*/\1/p'
#
# and the summary line "11 failed, 34 passed in 19.79s" STARTS with the digit, so
# `.*[^0-9]` can never match it; the companion `passed` expression then matched
# the "4" out of "34" and reported "SURVIVED (4 passed)". The verdict was a fact
# about the regex, not about the code — `claude/RULES.md`: PARSING a tool's
# output makes its FORMAT a dependency you did not pin. It now COUNTS
# `FAILED `/`ERROR ` lines and never parses the summary format at all.
#
# 🔴 BOTH CONTROLS RUN FIRST, and their output is the licence to read the rest:
#   * POSITIVE — the unmutated tree must be fully green (0 FAILED, 0 ERROR). A
#     battery whose baseline is already red attributes nothing.
#   * NEGATIVE — a deliberately broken module must be SEEN to go red. "Can this
#     harness observe a failure at all" is not answered by a green baseline.
#
# 🔴 PYTHONDONTWRITEBYTECODE=1 plus a __pycache__ purge before every run. CPython
# validates a cached module on source mtime-in-whole-SECONDS + size, so a
# SAME-LENGTH edit landing in the same second as the last import is invisible:
# the test imports the ORIGINAL bytecode and the mutant is scored SURVIVED
# without ever having executed.
#
# 🔴 Every mutant is DIFFED against the original before it runs. A `sed` that
# silently fails to match reports the UNMUTATED file's behaviour, which reads as
# "the guard held" — the most flattering possible wrong answer.
#
# MEASURED 2026-08-25 on the branch that introduced these files: 20 mutants, 20
# KILLED, positive control 49 passed / 0 FAILED / 0 ERROR, negative control 1
# collect-error. Two of them — `head-did-not-move-check-gone` and
# `ffonly-becomes-plain-merge` — SURVIVED on the first pass and are what
# `TestTheLastLineChecks` in the suite was written for; do not delete that class
# without re-running this file.
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1
CDPATH=
D="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
ROOT="$(cd "$D/../.." && pwd)"
LIB="$ROOT/scripts/lib/shared_clone_sync.py"
CLI="$ROOT/scripts/sync-clones.py"
SUITE="$D/test_shared_clone_sync.py"
for f in "$LIB" "$CLI" "$SUITE"; do
  [ -f "$f" ] || { printf 'mutants-shared-clone-sync: missing %s\n' "$f" >&2; exit 2; }
done

BAK="$(mktemp -d /tmp/scs-mut-XXXXXX)"
cp "$LIB" "$BAK/lib.py"; cp "$CLI" "$BAK/cli.py"
restore() { cp "$BAK/lib.py" "$LIB"; cp "$BAK/cli.py" "$CLI"; }
trap 'restore; rm -rf "$BAK"' EXIT
LOG="$BAK/out.txt"

pytest_run() {
  find "$ROOT/scripts" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
  # Run from OUTSIDE the repo: the suite locates everything from its own path,
  # and a cwd inside the tree lets an ambient conftest or rootdir decide things
  # the battery is trying to hold still.
  ( cd /tmp && python -m pytest "$SUITE" -q -p no:cacheprovider ) >"$LOG" 2>&1
}

run() { # run <name> <lib|cli> <sed-expr>
  local name="$1" which="$2" expr="$3" target ref
  if [ "$which" = lib ]; then target="$LIB"; ref="$BAK/lib.py"; else target="$CLI"; ref="$BAK/cli.py"; fi
  restore
  sed -i "$expr" "$target"
  if cmp -s "$ref" "$target"; then
    printf '  %-34s 🔴 MUTATION DID NOT APPLY — result meaningless\n' "$name"; return
  fi
  pytest_run
  local nfail nerr
  nfail="$(grep -c '^FAILED ' "$LOG")"
  nerr="$(grep -c '^ERROR ' "$LOG")"
  if [ "$nfail" -eq 0 ] && [ "$nerr" -eq 0 ]; then
    printf '  %-34s 🔴 SURVIVED  (%s)\n' "$name" "$(tail -1 "$LOG")"
  else
    printf '  %-34s KILLED    %s failed / %s collect-errors\n' "$name" "$nfail" "$nerr"
  fi
}

echo "== POSITIVE CONTROL: the UNMUTATED tree must be green (0 FAILED, 0 ERROR) =="
restore; pytest_run
echo "   $(tail -1 "$LOG")   FAILED-lines=$(grep -c '^FAILED ' "$LOG")  ERROR-lines=$(grep -c '^ERROR ' "$LOG")"

echo "== NEGATIVE CONTROL: a deliberately broken module must be SEEN to go RED =="
restore
printf '\nraise RuntimeError("negative control")\n' >> "$LIB"
pytest_run
echo "   FAILED-lines=$(grep -c '^FAILED ' "$LOG")  ERROR-lines=$(grep -c '^ERROR ' "$LOG")  (must be non-zero)"
restore

echo "== mutants (every one must be KILLED) =="
run dirty-reported-as-current      lib 's/^        res.status = STATUS_REFUSED_DIRTY$/        res.status = STATUS_CURRENT/'
run diverged-guard-disabled        lib 's/^    if res.ahead > 0:$/    if False:/'
run blocking-always-empty          lib 's/^    return sorted(set(hits))$/    return []/'
run untracked-dir-prefix-dropped   lib 's/^        elif d.endswith("\/") and any(c.startswith(d) for c in changed):$/        elif False:/'
run moved-always-zero              lib 's/^    res.moved = int(moved) if moved and moved.isdigit() else res.behind$/    res.moved = 0/'
run head-did-not-move-check-gone   lib 's/^    if res.head_after == res.head_before:$/    if False:/'
run behind-zero-becomes-always     lib 's/^    if res.behind == 0:$/    if res.behind >= 0:/'
run changed-by-ff-always-empty     lib 's/^    return \[p for p in out.splitlines() if p\]$/    return []/'
run no-renames-dropped             lib 's/\["diff", "--no-renames", "--name-only", "HEAD", upstream\]/["diff", "--name-only", "HEAD", upstream]/'
run fetch-skipped                  lib 's/^    if fetch:$/    if False:/'
run empty-roots-guard-gone         lib 's/^        if raw is not None and not raw.strip():$/        if False:/'
run worktree-filter-gone           lib 's/^            if (child \/ ".git").is_dir():$/            if (child \/ ".git").exists():/'
run worst-becomes-best             lib 's/^    return max((r.exit_code for r in results), default=0)$/    return min((r.exit_code for r in results), default=0)/'
run dirty-exit-code-collides       lib 's/^    STATUS_REFUSED_DIRTY: 3,$/    STATUS_REFUSED_DIRTY: 4,/'
run sentinel-collision             lib 's/^    STATUS_CURRENT: "already current",$/    STATUS_CURRENT: "fast-forwarded",/'
run detached-guard-gone            lib 's/^    if not branch:$/    if False:/'
run notarepo-guard-gone            lib 's/^    if _out(repo_s, \["rev-parse", "--git-dir"\]) is None:$/    if False:/'
run dirty-cap-ignored              lib 's/^        res.blocking_paths = blocking\[:DIRTY_PATH_CAP\]$/        res.blocking_paths = blocking/'
run zero-repos-exits-zero          cli 's/^        return EXIT_USAGE$/        return 0/'
run ffonly-becomes-plain-merge     lib 's/\["merge", "--ff-only", upstream\]/["merge", upstream]/'
echo "== done =="
