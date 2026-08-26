#!/usr/bin/env bash
# claim-work — an ATOMIC, cross-runtime claim on one ranked next-step item.
#
# ── WHY THIS EXISTS ───────────────────────────────────────────────────────────
# A handoff doc's `## Next steps (ranked)` is a WORK QUEUE WITH NO LOCK. Every
# `/resume` session draws from the same list and nothing marks an item taken, so
# two sessions build the same thing. Measured 2026-08-24 — four instances in one
# day, one of them (homelab-infra #388) closed after two adversarial audit rounds
# of work had already gone into it. The evidence and the refutations live in
# `claude/skills/handoff/reference/shared-queue.md`; the design argument lives in
# `claudedocs/design-claim-by-push.md`. Read those before changing the mechanism.
#
# ── 🔴 THE CLAIM NAMESPACE IS GLOBAL, NOT PER-REPO ────────────────────────────
# The queue it locks is GLOBAL: handoff docs live in `devrc/claudedocs/` while the
# work they rank happens in homelab-infra, datapacket-talos, civitai, … The very
# incident this tool exists for is exactly that shape — a devrc handoff doc
# (`claudedocs/handoff-devrc-ci-gate.md`) whose colliding PRs were homelab-infra
# `#386`/`#388`/`#389`.
#
# So every claim lands on ONE canonical remote, resolved from THE LOCATION OF
# THIS SCRIPT — never from the caller's cwd. `readlink -f` this file, walk to its
# repository root, read THAT repo's `origin`. It works from any cwd, in any repo,
# with no configuration, because the deployed `claim-work` is a symlink onto
# `devrc/scripts/claim-work.sh`.
#
# 🔴 IT WAS `$PWD` UNTIL 2026-08-26 AND THE WHOLE MECHANISM WAS INERT CROSS-REPO.
# Measured: the same canonical slug claimed from `~/workspace/devrc` and from
# `~/workspace/homelab-talos` BOTH returned rc 0 CLAIMED, one ref on each origin,
# no warning — and the two claims carried different git identities, so even the
# refusal text could not have disambiguated them. Natural usage (derive the slug
# from the devrc handoff doc, run the command in the repo you are working in) put
# two sessions in two namespaces and both won.
#
# 🔴 IF THE CANONICAL REMOTE CANNOT BE RESOLVED, DEGRADE — NEVER FALL BACK TO THE
# CWD'S ORIGIN. A cwd fallback reinstates exactly the bug above and hides it: the
# tool would report CLAIMED, on the wrong remote, with no warning. Resolution
# order, all documented in `usage()`:
#   1. `--remote <url>`                (explicit, wins)
#   2. `--repo <path>`'s origin        (explicit; CHANGES THE NAMESPACE, tests/CI)
#   3. `$DEVRC_CLAIM_REMOTE`           (explicit, environment)
#   4. this script's own repo's origin (THE DEFAULT — global, cwd-independent)
#   5. degrade
#
# ── THE MECHANISM, AND THE TWO REFUSALS IT ACTUALLY GETS ──────────────────────
# Publishing an ORPHAN commit to `refs/heads/claim/<slug>` on the canonical remote
# IS the claim. A second claim on the same slug is refused — but by which of two
# different mechanisms, and NEITHER is spelled "non-fast-forward" in the shape
# this script uses. Measured 2026-08-26 with real git against a real repo:
#
#   (a) TWO TRUE CONCURRENT FIRST MOVERS. Both clients saw NO ref, so both send
#       the update with old = 0000…0 — a CREATE. The receiving git's REF
#       TRANSACTION is a compare-and-swap on that expected value, so exactly one
#       create can land; the loser gets
#           cannot lock ref '<ref>': reference already exists
#       🔴 THIS is the atomicity, it is server-side, and it is the half that
#       covers the FIRST mover. The orphan-root property plays NO part in it.
#
#   (b) SERIALIZED — the second session starts after the first ref exists. The
#       connection advertises the ref at a sha this scratch repo does not have,
#       so git's own fast-forward check refuses CLIENT-SIDE before anything is
#       sent:
#           ! [rejected] <sha> -> claim/<slug> (fetch first)
#       (a repo that DID hold the winner's object would print `non-fast-forward`
#       instead — that is where the orphan root earns its keep, since an
#       unrelated root can never be a descendant.)
#
# Either way the winner's ref is untouched, and the script does not trust the
# MESSAGE: after a failed push it re-reads the remote and reports what it finds.
# `scripts/tests/test_claim_work.py` pins both mechanisms separately, and a
# mutation control defeats the lock with `--force` to prove the assertions are
# load-bearing.
#
# 🔴 IT IS NOT A CHECK-THEN-ACT. The claim path never asks "is it free?" before
# pushing; it pushes, and reads the remote only to explain a failure. There is no
# TOCTOU window.
#
# 🔴 IT PROTECTS THE **FIRST** MOVER, which is the half a pre-flight check
# structurally cannot. `gh pr list` only ever tells the LATER session that
# somebody else started; whoever moves first cannot see the second session
# because it does not exist yet at branch-creation time. The claim happens at
# DRAW time, before any work, so the first mover is the one it covers.
# ⚠ It does NOT replace that sweep. A duplicate that was never claimed is
# invisible to this tool and visible to `gh pr list` — run both.
#
# 🔴 WORKTREE ISOLATION IS NOT AN ALTERNATIVE TO THIS AND NEVER WAS. Every
# colliding session was already in its own worktree. A worktree prevents a
# FILESYSTEM collision; this is a TASK-ALLOCATION collision, and isolation is
# what HIDES it. Do not "fix" a collision by adding more isolation.
#
# ── 🔴 IT FAILS OPEN, ON PURPOSE ──────────────────────────────────────────────
# No canonical remote, no network, no auth, git missing, remote hung ⇒ WARN ON
# STDERR AND EXIT 0, degrading to the behaviour we had before this script
# existed. A bug in here is felt by EVERY `/resume`, so it must never be able to
# block one. Every network call is wrapped in a bounded `timeout` so a hung
# remote cannot hang a resume either. `--strict` turns a degraded run into rc 20
# instead; it exists for tests and CI, and must never be the default.
#
# ── THIS SCRIPT NEVER TOUCHES THE CALLER'S REPOSITORY ─────────────────────────
# It READS from it — `user.name` / `user.email` — and does everything else in a
# throwaway BARE repo under a mktemp dir that is removed on exit. No index, no
# working tree, no local branch, no FETCH_HEAD, no stash, no objects written into
# the caller's object database. That is deliberate: this runs at the start of
# every resumed session, frequently in a shared checkout, and a claim tool that
# can perturb the tree it is claiming work in would be worse than the collision.
#
# 🔴 AND IT NEVER RUNS THE OPERATOR'S HOOKS. The scratch repo is `git init`ed, so
# it inherits `core.hooksPath` from `~/.gitconfig` — measured 2026-08-26: a global
# `pre-push` fires on a claim push, and a global hook that BLOCKS makes this lock
# silently INERT (push fails ⇒ degrade ⇒ exit 0 ⇒ "unclaimed"). Every git call
# below therefore goes through `git_`/`gitnet`, which pin
# `-c core.hooksPath=/dev/null`, and every push adds `--no-verify`.
#
# ── 🔴 WHAT A CLAIM PUBLISHES, AND THIS REPO IS PUBLIC ────────────────────────
# A claim commit is pushed to the canonical origin, where anyone with read access
# to that remote can see it. It carries: the claimant's git name/email, the
# HOSTNAME, an opaque `cwd-id` (a hash — deliberately NOT the absolute path, which
# would leak a client repo's name into a public remote), a nonce, and the
# `--subject` TEXT YOU TYPED. **The subject is public. Do not put client detail,
# a real hostname, a media path or captured text in it** — describe the item in
# generic words. See `CLAUDE.md` → "This repo is PUBLIC".
#
# ── EXIT CODES ────────────────────────────────────────────────────────────────
# Aligned with the rc-vocabulary style of scripts/ship.sh and scripts/drift-check.sh.
#
#    0  SUCCESS, and — deliberately — also DEGRADED (see "fails open" above).
#         claim   : you WON the claim, the ref is yours
#         --check : the slug is FREE
#         --list  : listed (possibly zero claims)
#         --release / --steal : done
#         degraded: could not reach origin; a warning was printed to stderr
#    2  USAGE — bad flag, missing/malformed slug. NOT failed open: a typo'd slug
#         would otherwise claim nothing while the caller believes it claimed
#         something, which is the exact failure this tool exists to remove.
#   10  ALREADY CLAIMED, and the claim is LIVE. WHO/WHEN/WHERE/WHAT are printed.
#         Do NOT start this item; pick another, or talk to the claimer.
#         ALSO: a `--release` / `--steal` REFUSED because the claim is not yours
#         and not stale, or because its owner could not be read. `--force`
#         overrides — deliberately, and only when you mean it.
#   11  ALREADY CLAIMED but the claim is STALE (older than DEVRC_CLAIM_TTL_DAYS,
#         default 7). A stale ref would otherwise block an item forever. Decide:
#         `--steal <slug>` to take it over, or `--release <slug>` to drop it.
#   20  DEGRADED **and** `--strict` was passed. Never emitted without --strict.
#
# ── CLEANUP ───────────────────────────────────────────────────────────────────
# Nothing prunes `refs/heads/claim/*` automatically and nothing ever will
# implicitly: deleting somebody's claim is a decision, not maintenance. The
# supported story is manual and two commands — `claim-work --list` flags every
# claim past the TTL with `[STALE]`, and `claim-work --release <slug>` drops one
# (your own, or a stale one; anything else needs `--force`). Release your own
# claims when the work lands; that is what keeps the namespace small.
#
# ── ENVIRONMENT ───────────────────────────────────────────────────────────────
#   DEVRC_CLAIM_REMOTE      canonical remote URL (else: this script's repo's origin)
#   DEVRC_CLAIM_TTL_DAYS    staleness threshold in days (default 7)
#   DEVRC_CLAIM_TIMEOUT     per-network-call timeout, `timeout` syntax (default 20s)
set -euo pipefail

PROG=claim-work

# The ref namespace. Under refs/heads/ ON PURPOSE: a normal branch ref is what
# every git server enforces the fast-forward rule on, and it is what `git
# ls-remote` / the GitHub UI will show without extra configuration. A custom
# refs/claims/* namespace would need server-side rules we do not control.
CLAIM_NS="refs/heads/claim/"

# 🔴 ONE literal per default. `7` used to be spelled twice (the `:-7` fallback
# and DEFAULT_TTL_DAYS) and a mutation sweep found the pair: changing one of them
# left the other to keep the tests green.
DEFAULT_TTL_DAYS=7
DEFAULT_NET_TIMEOUT=20s

TTL_DAYS="${DEVRC_CLAIM_TTL_DAYS:-$DEFAULT_TTL_DAYS}"
NET_TIMEOUT="${DEVRC_CLAIM_TIMEOUT:-$DEFAULT_NET_TIMEOUT}"

RC_USAGE=2
RC_TAKEN=10
RC_TAKEN_STALE=11
RC_DEGRADED_STRICT=20

MODE=claim
SLUG=""
SUBJECT=""
REPO=""                 # --repo: explicit ident repo AND explicit remote source
REMOTE_FLAG=""          # --remote
REMOTE_ENV="${DEVRC_CLAIM_REMOTE:-}"
STRICT=0
FORCE=0
SLUG_DOC=""
SLUG_RANK=""

warn() { printf '%s: %s\n' "$PROG" "$*" >&2; }

# 🔴 A NON-NUMERIC TTL MUST NOT REACH THE ARITHMETIC. Measured on bash under this
# file's own `set -euo pipefail`, all three shapes, because the intuition here is
# wrong in two directions:
#   DEVRC_CLAIM_TTL_DAYS=7d    -> `$(( TTL_DAYS * 86400 ))` ABORTS the script
#                                 ("value too great for base", rc 1)
#   DEVRC_CLAIM_TTL_DAYS=week  -> ABORTS ("week: unbound variable", rc 1 — `set -u`
#                                 catches the bare-identifier case that would
#                                 otherwise evaluate to a silent 0)
#   an EMPTY value             -> silently 0, so every live claim reads STALE
# The first two are the worse pair: a tool whose entire contract is "never block a
# resume" would die on a typo in an environment variable. Fall back loudly.
case "$TTL_DAYS" in
  ''|*[!0-9]*)
    warn "DEVRC_CLAIM_TTL_DAYS='$TTL_DAYS' is not a whole number of days — using $DEFAULT_TTL_DAYS"
    TTL_DAYS="$DEFAULT_TTL_DAYS"
    ;;
esac

# 🔴 AND THE TIMEOUT GETS THE SAME TREATMENT, because garbage here is WORSE than
# garbage in the TTL: `timeout <junk> git …` exits **125** without running git at
# all, so every network call reads as a failure and the tool silently degrades to
# "proceeding UNCLAIMED" on every single call. That is a lock that has stopped
# locking while reporting exit 0. `timeout`'s DURATION is a number with an
# optional s/m/h/d suffix.
if ! [[ $NET_TIMEOUT =~ ^[0-9]+(\.[0-9]+)?[smhd]?$ ]] || [[ $NET_TIMEOUT =~ ^0+(\.0+)?[smhd]?$ ]]; then
  warn "DEVRC_CLAIM_TIMEOUT='$NET_TIMEOUT' is not a \`timeout\` duration — using $DEFAULT_NET_TIMEOUT"
  NET_TIMEOUT="$DEFAULT_NET_TIMEOUT"
fi

usage() {
  cat >&2 <<'EOF'
claim-work — claim one ranked next-step item so a second session cannot start it.

  claim-work <slug> [--subject "<human text>"]   claim it        (0 won / 10 taken / 11 stale)
  claim-work --check <slug>                      is it taken?    (0 free / 10 taken / 11 stale)
  claim-work --list                              every live claim, with age + subject
  claim-work --release <slug>                    drop the claim  (yours, or a stale one)
  claim-work --steal <slug> [--subject "..."]    take over a stale/abandoned claim
  claim-work --slug-for <handoff-doc> [<rank>]   print the CANONICAL slug for an item

Options:
  --remote <url>    claim on THIS remote instead of the canonical one
  --repo <path>     take the remote from this repo's `origin`, and read the git
                    identity from it. 🔴 THIS CHANGES THE CLAIM NAMESPACE — it is
                    for tests and one-off cross-remote work, never routine use.
  --force           allow --release/--steal of a live claim that is not yours
  --strict          exit 20 instead of 0 when it cannot reach origin (tests/CI)

🔴 THE CLAIM NAMESPACE IS GLOBAL, NOT PER-REPO. The queue is global — handoff
docs live in devrc while the work happens in other repos — so by default every
claim lands on ONE canonical remote: the `origin` of the repository containing
THIS SCRIPT, resolved from its own path. The cwd is NOT consulted, and a cwd
fallback is deliberately absent: if the canonical remote cannot be resolved this
degrades, because falling back per-repo is how the same slug got claimed twice.

🔴 WHAT YOU PUBLISH IS PUBLIC. A claim commit is pushed to the canonical origin
carrying your git name/email, hostname, an opaque cwd-id, and the `--subject`
text verbatim. Keep the subject generic — no client names, paths or captured text.

It FAILS OPEN: no canonical remote / no network / no auth ⇒ warning on stderr,
exit 0. Exit codes and the design argument are documented at the top of this file.
EOF
}

die_usage() {
  warn "$*"
  usage
  exit "$RC_USAGE"
}

# 🔴 THE FAIL-OPEN PATH. Everything that is "we could not find out" lands here,
# and by default it exits 0 so a resumed session proceeds exactly as it did
# before this script existed. It is loud on stderr so the degradation is never
# silent, and it says what the caller should do instead.
degrade() {
  warn "DEGRADED — $*"
  warn "  no claim was made or verified. Proceeding UNCLAIMED, as if this tool did not exist."
  warn "  Fall back to the manual half: check \`gh pr list --state open\` and push your branch immediately."
  if [ "$STRICT" -eq 1 ]; then
    exit "$RC_DEGRADED_STRICT"
  fi
  exit 0
}

# ── argument parsing ──────────────────────────────────────────────────────────
[ "$#" -gt 0 ] || { usage; exit "$RC_USAGE"; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check)     MODE=check;   SLUG="${2:-}"; shift 2 || die_usage "--check needs a slug" ;;
    --release)   MODE=release; SLUG="${2:-}"; shift 2 || die_usage "--release needs a slug" ;;
    --steal)     MODE=steal;   SLUG="${2:-}"; shift 2 || die_usage "--steal needs a slug" ;;
    --list)      MODE=list;    shift ;;
    --slug-for)
      MODE=slug-for
      SLUG_DOC="${2:-}"
      shift 2 || die_usage "--slug-for needs a handoff-doc path"
      # An optional trailing rank, only if it is a bare number — otherwise it is
      # the next flag and must not be swallowed.
      if [ "$#" -gt 0 ] && [[ ${1} =~ ^[0-9]+$ ]]; then
        SLUG_RANK="$1"; shift
      fi
      ;;
    --subject)   SUBJECT="${2:-}"; shift 2 || die_usage "--subject needs text" ;;
    --repo)      REPO="${2:-}";    shift 2 || die_usage "--repo needs a path" ;;
    --remote)    REMOTE_FLAG="${2:-}"; shift 2 || die_usage "--remote needs a url" ;;
    --force)     FORCE=1; shift ;;
    --strict)    STRICT=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    --*)         die_usage "unknown option: $1" ;;
    *)
      [ -z "$SLUG" ] || die_usage "unexpected extra argument: $1"
      SLUG="$1"; shift ;;
  esac
done

# ── slug derivation + validation ──────────────────────────────────────────────
#
# 🔴 SLUG DETERMINISM IS THE CRUX AND THE HONEST WEAK POINT. The hard lock only
# engages on an EXACT ref-name match, so two sessions must derive the SAME slug
# from the same item or nothing is locked at all. That is why the derivation is
# CODE here rather than a convention in prose: both runtimes run this function
# and get the same answer.
#
# What it CANNOT do: catch a semantically identical item that two sessions
# describe differently, or two different handoff docs covering one piece of work.
# `--list` prints every claim's human SUBJECT for exactly that reason — the
# exact-slug match is the HARD lock, the subject list is a SOFT signal a human
# (or an agent) reads. Do not claim it catches reworded duplicates.
derive_slug() {
  local doc="$1" rank="$2" base
  base="$(basename -- "$doc")"
  base="$(printf '%s' "$base" | tr '[:upper:]' '[:lower:]')"
  base="${base%.md}"
  # 🔴 CASE-FOLD BEFORE STRIPPING THE PREFIX, not after: `${base#handoff-}` is a
  # literal, case-SENSITIVE match, so `Handoff-x.md` would keep the prefix and
  # derive a different slug from `handoff-x.md` for the same doc. Two spellings,
  # two slugs, no lock.
  base="${base#handoff-}"     # the repo's own handoff-<topic>.md convention
  base="$(printf '%s' "$base" \
    | sed -e 's/[^a-z0-9]\{1,\}/-/g' -e 's/^-\{1,\}//' -e 's/-\{1,\}$//')"
  [ -n "$base" ] || return 1
  if [ -n "$rank" ]; then
    printf '%s-%s\n' "$base" "$rank"
  else
    printf '%s\n' "$base"
  fi
}

# 🔴 A slug becomes a REF NAME, so this is a safety check, not tidiness. An
# unvalidated slug could inject `../`, a leading `-`, or whitespace into the
# refspec on both the local and the remote side.
#
# 🔴 THE MATCH IS WHOLE-STRING, NOT PER-LINE, AND THAT IS THE BUG THIS ONCE HAD.
# It used `printf … | grep -Eq`, and grep is LINE-based: `$'good\nBAD SLUG'`
# matched on line 1, so a slug git would never accept sailed through validation
# and the run exited 0 DEGRADED instead of rc 2. bash's own `[[ =~ ]]` has no
# line semantics — `^`/`$` anchor the whole string — so it rejects it. Verified
# both ways 2026-08-26.
#
# 🔴 AND THE PATTERN ALONE IS NOT THE WHOLE CONTRACT: `foo.lock` matches it and is
# ILLEGAL as a git ref — the same failure in a different shape. So `..`, a
# trailing `.` and a `.lock` suffix are spelled out below (they must be rc 2 even
# when git is missing, since validation deliberately runs before the
# `command -v git` check), and the real ref name is then handed to
# `git check-ref-format`, the only authority on what git will accept.
#
# ⚠ HOW MUCH `check-ref-format` ACTUALLY ADDS, MEASURED rather than assumed:
# over all 157,120 strings of length ≤ 6 in `{a,b,0,1,9,._-}`, there is NOT ONE
# that the pattern plus the three cases accept and `check-ref-format` rejects. So
# for a single-line slug it is pure belt-and-braces. Its one measured unique
# catch is the MULTI-LINE shape — i.e. it is the backstop for exactly the bug
# above, which is why both stay. Consequence for the test suite: mutating either
# guard alone SURVIVES, because the other one covers it; only mutating BOTH goes
# red. That is recorded in `test_claim_work.py` so nobody reads the survivor as a
# coverage gap.
validate_slug() {
  local s="$1"
  [ -n "$s" ] || die_usage "no slug given"
  [[ $s =~ ^[a-z0-9][a-z0-9._-]*$ ]] \
    || die_usage "malformed slug '$s' — lowercase a-z 0-9 . _ - only, must start alphanumeric, single line"
  case "$s" in
    *..*)    die_usage "malformed slug '$s' — '..' is not allowed in a ref name" ;;
    *.)      die_usage "malformed slug '$s' — a ref component may not end in '.'" ;;
    *.lock)  die_usage "malformed slug '$s' — git reserves the '.lock' suffix on a ref component" ;;
  esac
  [ "${#s}" -le 100 ] || die_usage "slug too long (${#s} > 100)"
  if command -v git >/dev/null 2>&1; then
    git check-ref-format "${CLAIM_NS}${s}" 2>/dev/null \
      || die_usage "malformed slug '$s' — git rejects '${CLAIM_NS}${s}' as a ref name"
  fi
}

if [ "$MODE" = "slug-for" ]; then
  [ -n "$SLUG_DOC" ] || die_usage "--slug-for needs a handoff-doc path"
  # A flag swallowed as the doc path would otherwise derive a slug from it:
  # `--slug-for --strict` printed `strict` and exited 0.
  case "$SLUG_DOC" in
    -*) die_usage "--slug-for needs a handoff-doc path, got the flag '$SLUG_DOC'" ;;
  esac
  out="$(derive_slug "$SLUG_DOC" "$SLUG_RANK")" \
    || die_usage "cannot derive a slug from '$SLUG_DOC'"
  validate_slug "$out"
  printf '%s\n' "$out"
  exit 0
fi

[ "$MODE" = "list" ] || validate_slug "$SLUG"

# ── plumbing ──────────────────────────────────────────────────────────────────
command -v git >/dev/null 2>&1 || degrade "git is not on PATH"

# 🔴 EVERY GIT CALL GOES THROUGH ONE OF THESE TWO. `-c core.hooksPath=/dev/null`
# is the load-bearing part: the scratch repo below is `git init`ed and therefore
# inherits the OPERATOR's global `core.hooksPath`, and a global `pre-push` that
# blocks turns this lock silently inert (push fails ⇒ degrade ⇒ exit 0). Measured
# 2026-08-26 with a real global hook. Do not call bare `git` below.
git_() { git -c core.hooksPath=/dev/null "$@"; }

# Bounded network calls. A hung remote must never hang a resume. `timeout` is
# coreutils and is present on both hosts and in the nix sandbox; if it somehow
# is not, we still run — degrading to "slow" is better than degrading to
# "unusable", and git's own transport timeouts below remain in force.
TIMEOUT_BIN="$(command -v timeout || true)"
gitnet() {
  if [ -n "$TIMEOUT_BIN" ]; then
    "$TIMEOUT_BIN" "$NET_TIMEOUT" git -c core.hooksPath=/dev/null "$@"
  else
    git_ "$@"
  fi
}

# Never sit waiting for a human to type a password into an agent's session.
export GIT_TERMINAL_PROMPT=0
unset GIT_ASKPASS SSH_ASKPASS 2>/dev/null || true
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o BatchMode=yes -o ConnectTimeout=10}"

# ── 🔴 the CANONICAL remote ───────────────────────────────────────────────────
#
# 🔴 NOT a function whose output is captured. `degrade` EXITS, and an `exit`
# inside a `$(command substitution)` only leaves the subshell — the script would
# sail on with an empty remote and a warning nobody acted on. So the resolution
# assigns a global in the CURRENT shell instead.
#
# The DEFAULT is resolved from THIS SCRIPT's own path, so it is the same remote
# from every cwd and every repo. There is deliberately NO cwd fallback: see the
# header. `readlink -f` follows the `~/.local/bin/claim-work` symlink chain to
# `devrc/scripts/claim-work.sh`.
script_repo_origin() {
  local src dir root
  src="${BASH_SOURCE[0]}"
  src="$(readlink -f -- "$src" 2>/dev/null || printf '%s' "$src")"
  dir="$(dirname -- "$src")"
  root="$(git_ -C "$dir" rev-parse --show-toplevel 2>/dev/null || true)"
  [ -n "$root" ] || return 1
  git_ -C "$root" remote get-url origin 2>/dev/null
}

REMOTE_URL=""
REMOTE_SOURCE=""
if [ -n "$REMOTE_FLAG" ]; then
  REMOTE_URL="$REMOTE_FLAG"; REMOTE_SOURCE="--remote"
elif [ -n "$REPO" ]; then
  # Explicit, and it CHANGES THE NAMESPACE — announced, never silent.
  git_ -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 \
    || degrade "--repo '$REPO' is not a git repository"
  REMOTE_URL="$(git_ -C "$REPO" remote get-url origin 2>/dev/null || true)"
  [ -n "$REMOTE_URL" ] || degrade "--repo '$REPO' has no 'origin' remote"
  REMOTE_SOURCE="--repo $REPO"
elif [ -n "$REMOTE_ENV" ]; then
  REMOTE_URL="$REMOTE_ENV"; REMOTE_SOURCE="DEVRC_CLAIM_REMOTE"
else
  REMOTE_URL="$(script_repo_origin || true)"
  [ -n "$REMOTE_URL" ] || degrade \
    "could not resolve the CANONICAL claim remote from this script's own location ($(readlink -f -- "${BASH_SOURCE[0]}" 2>/dev/null || printf '%s' "${BASH_SOURCE[0]}")). Not falling back to the cwd's origin — that is a per-repo namespace and the same slug would be claimable once per remote. Pass --remote <url> or set DEVRC_CLAIM_REMOTE"
  REMOTE_SOURCE="canonical (this script's repo)"
fi

# The throwaway bare repo. Everything below happens HERE, never in the caller's.
WS="$(mktemp -d -t claim-work.XXXXXXXX)"
cleanup() { rm -rf "$WS"; }
# 🔴 EXIT ONLY, AND THAT IS A MEASUREMENT, NOT AN OVERSIGHT. An audit asked for
# `INT TERM HUP` here on the theory that a killed run leaks its `mktemp -d`.
# Measured 2026-08-26 on bash 5.3.3: it does NOT. bash's own terminating-signal
# handler runs the EXIT trap, so a run SIGTERMed mid-push while parked in a
# hanging `git push` cleaned up with `trap cleanup EXIT` alone (rc -15, zero
# `claim-work.*` left in TMPDIR).
#
# 🔴 And adding them would have been WORSE than useless: a `trap cleanup TERM`
# handler RETURNS, it does not exit — so the script would delete $WS and then
# carry on using it (`$WS/push.err`), turning a clean kill into an exit 0 over a
# deleted scratch repo. Measured too: with the signal traps added the same run
# exited **0**, i.e. a killed resume reported success. If this ever needs
# widening, the trap must re-raise (`trap - TERM; cleanup; kill -s TERM $$`).
trap cleanup EXIT

git_ init -q --bare "$WS" >/dev/null 2>&1 || degrade "could not create a scratch repository under $WS"
git_ -C "$WS" remote add origin "$REMOTE_URL" >/dev/null 2>&1 \
  || degrade "could not attach remote '$REMOTE_URL'"

# ── identity: WHO the claim names, and WHICH SESSION owns it ──────────────────
# The identity is read from the CALLER's git config so the claim carries a real
# person/agent rather than the scratch repo's (absent) one. Falls back to
# $USER@<host> so a hermetic environment with no git config can still claim.
IDENT_REPO="${REPO:-$PWD}"
ident_name="$(git_ -C "$IDENT_REPO" config --get user.name 2>/dev/null || true)"
ident_mail="$(git_ -C "$IDENT_REPO" config --get user.email 2>/dev/null || true)"
[ -n "$ident_name" ] || ident_name="${USER:-unknown}"
[ -n "$ident_mail" ] || ident_mail="${USER:-unknown}@$(uname -n 2>/dev/null || echo localhost)"

# 🔴 IDENTITY CANNOT IDENTIFY A SESSION, so it cannot decide ownership. Both
# hosts run as the same git identity, and two agents in two worktrees on one host
# are the same person to git. The session token is therefore host + an opaque
# cwd-id, recorded in the claim commit and compared on --release/--steal.
#
# 🔴 cwd-id IS A HASH, NOT THE PATH. The claim is pushed to a remote and this
# repo is PUBLIC; an absolute cwd leaks a client repo's directory name. The hash
# is computed with git's own hash-object (git is already required) so it needs no
# extra dependency and is stable for a given absolute path.
MY_HOST="$(uname -n 2>/dev/null || echo unknown)"
MY_CWD_ID="$(printf '%s' "$IDENT_REPO" | git_ hash-object --stdin 2>/dev/null | cut -c1-12)"
[ -n "$MY_CWD_ID" ] || MY_CWD_ID="unknown"

remote_ref_sha() {
  # Prints the sha of the claim ref on origin, or nothing. Returns non-zero ONLY
  # when the remote could not be reached at all — the caller must distinguish
  # "not claimed" from "could not find out", because collapsing those two is
  # how a claim tool starts silently reporting FREE for everything.
  local s="$1" out
  out="$(gitnet -C "$WS" ls-remote origin "${CLAIM_NS}${s}" 2>/dev/null)" || return 1
  # Field 1 is the OBJECT NAME; field 2 is the ref name. The sha is what every
  # caller compares against, including the "did my push actually land?" branch
  # in the claim path, where a ref name would never equal our commit's sha.
  printf '%s' "$out" | awk 'NR==1{print $1}'
}

fetch_claim() {
  # Bring one claim commit into the scratch repo so its author/date/subject can
  # be read. Local ref name mirrors the slug.
  local s="$1"
  gitnet -C "$WS" fetch -q --no-tags origin \
    "+${CLAIM_NS}${s}:refs/claims/${s}" >/dev/null 2>&1
}

human_age() {
  local secs="$1"
  if   [ "$secs" -lt 3600 ];   then printf '%dm' $(( secs / 60 ))
  elif [ "$secs" -lt 86400 ];  then printf '%dh' $(( secs / 3600 ))
  else                              printf '%dd' $(( secs / 86400 ))
  fi
}

# The machine prefix `claim(<slug>): ` is for the ref, not for a reader. Strip it
# so the human field shows what the claimant actually typed — and say so plainly
# when they typed nothing, rather than printing the bare machine string.
human_subject() {
  local s="$1" subj="$2"
  subj="${subj#claim($s): }"
  if [ "$subj" = "claim($s)" ] || [ -z "$subj" ]; then
    printf '(no --subject was given)'
  else
    printf '%s' "$subj"
  fi
}

# One trailer out of a claim commit's body, or nothing.
claim_field() {
  local ref="$1" key="$2"
  git_ -C "$WS" log -1 --format='%B' "$ref" 2>/dev/null \
    | awk -v k="${key}:" '$1==k { sub(/^[^ ]+[ ]*/, ""); print; exit }'
}

# 🔴 OWNERSHIP, AND WHY IT IS NOT THE AUTHOR. See MY_CWD_ID above: the git
# identity is identical across both hosts and across every agent on one host, so
# `%an <%ae>` cannot tell two sessions apart. host + cwd-id can.
claim_is_mine() {
  local ref="$1" h c
  h="$(claim_field "$ref" host)"
  c="$(claim_field "$ref" cwd-id)"
  [ -n "$h" ] && [ -n "$c" ] && [ "$h" = "$MY_HOST" ] && [ "$c" = "$MY_CWD_ID" ]
}

claim_age_secs() {
  # Always exits 0 and always prints a number: it is read through a command
  # substitution in an ASSIGNMENT, and under `set -e` a non-zero there aborts the
  # script. An unreadable date prints 0 — i.e. "fresh" — which is the SAFE
  # default for the ownership gate below (fresh ⇒ refuse), not the convenient one.
  local ref="$1" epoch now age
  epoch="$(git_ -C "$WS" log -1 --format='%at' "$ref" 2>/dev/null || true)"
  [ -n "$epoch" ] || { printf '0'; return 0; }
  now="$(date +%s)"
  age=$(( now - epoch ))
  # A future-dated claim (clock skew between the two hosts) clamps to 0 rather
  # than going negative and reading as freshly-made-in-the-future.
  [ "$age" -ge 0 ] || age=0
  printf '%s' "$age"
}

is_stale() { [ "$1" -gt $(( TTL_DAYS * 86400 )) ]; }

# Prints WHO / WHEN / WHERE / WHAT for an existing claim and returns the right rc:
# RC_TAKEN while live, RC_TAKEN_STALE once past the TTL.
report_existing() {
  local s="$1" who when subject age host cwdid
  if ! fetch_claim "$s"; then
    # The ref exists (ls-remote saw it) but we could not read the commit. Still
    # a claim — report TAKEN with what we know rather than pretending it is free.
    printf '%s: ALREADY CLAIMED — %s%s (details unreadable)\n' "$PROG" "$CLAIM_NS" "$s"
    printf '  DO NOT start this item. Pick another, or coordinate with the claimer.\n'
    return "$RC_TAKEN"
  fi
  who="$(git_ -C "$WS" log -1 --format='%an <%ae>' "refs/claims/$s")"
  when="$(git_ -C "$WS" log -1 --format='%aI' "refs/claims/$s")"
  subject="$(git_ -C "$WS" log -1 --format='%s' "refs/claims/$s")"
  age="$(claim_age_secs "refs/claims/$s")"
  host="$(claim_field "refs/claims/$s" host)"
  cwdid="$(claim_field "refs/claims/$s" cwd-id)"

  printf '%s: ALREADY CLAIMED — %s%s\n' "$PROG" "$CLAIM_NS" "$s"
  printf '  who:   %s\n' "$who"
  printf '  when:  %s  (%s ago)\n' "$when" "$(human_age "$age")"
  # 🔴 WHERE, not just WHO. `%an <%ae>` is the SAME identity for every session on
  # both hosts, so a refusal naming only the author names a party that cannot
  # discriminate — the reader cannot tell somebody else's claim from their own.
  printf '  where: host %s, cwd-id %s%s\n' "${host:-unknown}" "${cwdid:-unknown}" \
    "$(claim_is_mine "refs/claims/$s" && printf ' — THIS SESSION (you already hold it)' || true)"
  printf '  what:  %s\n' "$(human_subject "$s" "$subject")"

  if is_stale "$age"; then
    printf '  STALE: older than %s day(s) — it may be abandoned.\n' "$TTL_DAYS"
    printf '         %s --steal %s   (take it over)\n' "$PROG" "$s"
    printf '         %s --release %s (drop it)\n' "$PROG" "$s"
    return "$RC_TAKEN_STALE"
  fi
  printf '  DO NOT start this item. Pick another, or coordinate with the claimer.\n'
  return "$RC_TAKEN"
}

# 🔴 THE OWNERSHIP GATE ON THE TWO DESTRUCTIVE VERBS. `--release` deletes and
# `--steal` overwrites somebody else's ref; rc 10 prints "DO NOT start this item"
# with `--steal` one flag away, so without this gate the refusal is advice, not a
# lock. Measured 2026-08-26 before the gate existed: session B released session
# A's 0-second-old live claim, rc 0, silently — and stole it, rc 0.
#
# Allowed without --force: a claim that is MINE (host + cwd-id match), or one
# past the TTL (that is exactly what the stale escape hatch is for), or a slug
# nobody holds. Refused: a live claim belonging to another session, and a claim
# whose owner could not be READ — "could not find out" must not authorise a
# destructive write on somebody else's lock.
require_ownership_or_force() {
  local s="$1" verb="$2" age readable=1
  fetch_claim "$s" || readable=0
  if [ "$FORCE" -eq 1 ]; then
    warn "--force: skipping the ownership and staleness check on ${CLAIM_NS}${s} (--$verb)"
    return 0
  fi
  if [ "$readable" -eq 0 ]; then
    printf '%s: REFUSED — cannot read %s%s to check whose it is\n' "$PROG" "$CLAIM_NS" "$s"
    printf '  A claim whose owner is unknown is not yours by default.\n'
    printf '  If you mean it:  %s --%s %s --force\n' "$PROG" "$verb" "$s"
    return "$RC_TAKEN"
  fi
  age="$(claim_age_secs "refs/claims/$s")"
  if claim_is_mine "refs/claims/$s"; then
    return 0
  fi
  if is_stale "$age"; then
    return 0
  fi
  printf '%s: REFUSED — %s%s is NOT yours and is NOT stale (%s old, TTL %s day(s))\n' \
    "$PROG" "$CLAIM_NS" "$s" "$(human_age "$age")" "$TTL_DAYS"
  printf '  held by:  %s\n' "$(git_ -C "$WS" log -1 --format='%an <%ae>' "refs/claims/$s")"
  printf '  where:    host %s, cwd-id %s\n' \
    "$(claim_field "refs/claims/$s" host)" "$(claim_field "refs/claims/$s" cwd-id)"
  printf '  you are:  host %s, cwd-id %s\n' "$MY_HOST" "$MY_CWD_ID"
  printf '  what:     %s\n' \
    "$(human_subject "$s" "$(git_ -C "$WS" log -1 --format='%s' "refs/claims/$s")")"
  printf '  Coordinate with the claimer, or wait for the TTL. If you mean to override:\n'
  printf '            %s --%s %s --force\n' "$PROG" "$verb" "$s"
  return "$RC_TAKEN"
}

make_claim_commit() {
  # An ORPHAN commit: `commit-tree` with NO `-p`, over the empty tree. Every
  # claim is an unrelated root, which is a second line of defence in the
  # SERIALIZED case (an unrelated root can never fast-forward over the winner).
  # It is NOT what refuses two true concurrent first movers — that is the
  # server's ref-transaction CAS on `old = 0000…`; see the header. Do not give
  # this a parent, and do not build it from an existing branch.
  local s="$1" subj="$2" tree msg
  tree="$(git_ -C "$WS" mktree </dev/null)"
  if [ -n "$subj" ]; then
    msg="claim($s): $subj"
  else
    msg="claim($s)"
  fi
  # 🔴 THE NONCE IS LOAD-BEARING, NOT DECORATION. Without it two claims that
  # agree on identity, message, cwd and second are BYTE-IDENTICAL, hence the same
  # sha — and pushing the sha a ref already holds is "Everything up-to-date",
  # exit 0, so a SECOND session would print CLAIMED for an item it does not hold.
  # A mutation sweep found a constant here surviving the whole suite;
  # `test_two_claims_that_would_be_byte_identical_still_collide` is the cover.
  #
  # 🔴 `cwd-id` NOT `cwd`. This commit is pushed to a remote and this repo is
  # public — an absolute path leaks a client repo's name. The hash is enough to
  # tell two sessions apart, which is all ownership needs.
  GIT_AUTHOR_NAME="$ident_name" GIT_AUTHOR_EMAIL="$ident_mail" \
  GIT_COMMITTER_NAME="$ident_name" GIT_COMMITTER_EMAIL="$ident_mail" \
    git_ -C "$WS" commit-tree "$tree" <<EOF
$msg

claimed-by: $ident_name <$ident_mail>
host: $MY_HOST
cwd-id: $MY_CWD_ID
nonce: $$-$(date +%s%N 2>/dev/null || date +%s)
EOF
}

# ── modes ─────────────────────────────────────────────────────────────────────
case "$MODE" in

  check)
    # 🔴 READ-ONLY. This path must never create a ref. It exists so a session can
    # ask "is this taken?" without taking it — and a check that claimed as a side
    # effect would make every dry run a collision.
    sha="$(remote_ref_sha "$SLUG")" || degrade "could not reach '$REMOTE_URL' to check '$SLUG'"
    if [ -z "$sha" ]; then
      printf '%s: FREE — %s%s is unclaimed\n' "$PROG" "$CLAIM_NS" "$SLUG"
      exit 0
    fi
    rc=0; report_existing "$SLUG" || rc=$?
    exit "$rc"
    ;;

  list)
    if ! gitnet -C "$WS" fetch -q --no-tags origin \
        "+${CLAIM_NS}*:refs/claims/*" >/dev/null 2>&1; then
      # An empty namespace is not an error; a genuinely unreachable remote is.
      remote_ref_sha "zzz-probe-that-cannot-exist" >/dev/null \
        || degrade "could not reach '$REMOTE_URL' to list claims"
    fi
    n=0
    now="$(date +%s)"
    while IFS='|' read -r ref epoch who subject; do
      [ -n "$ref" ] || continue
      n=$(( n + 1 ))
      age=$(( now - epoch ))
      [ "$age" -ge 0 ] || age=0
      flag=""
      is_stale "$age" && flag="  [STALE]"
      printf '%-40s %6s ago  %-24s %s%s\n' "$ref" "$(human_age "$age")" "$who" \
        "$(human_subject "$ref" "$subject")" "$flag"
    done <<EOF
$(git_ -C "$WS" for-each-ref --sort=-authordate \
    --format='%(refname:lstrip=2)|%(authordate:unix)|%(authorname)|%(contents:subject)' \
    refs/claims 2>/dev/null)
EOF
    if [ "$n" -eq 0 ]; then
      printf '%s: no live claims on %s\n' "$PROG" "$REMOTE_URL"
    else
      printf '\n%s: %d live claim(s) on %s [%s]\n' "$PROG" "$n" "$REMOTE_URL" "$REMOTE_SOURCE"
      printf '  🔴 The SUBJECT column is a SOFT signal — scan it for a near-duplicate of\n'
      printf '  your item whose SLUG differs. Only an exact slug match locks.\n'
    fi
    exit 0
    ;;

  release)
    sha="$(remote_ref_sha "$SLUG")" || degrade "could not reach '$REMOTE_URL' to release '$SLUG'"
    if [ -z "$sha" ]; then
      printf '%s: nothing to release — %s%s does not exist\n' "$PROG" "$CLAIM_NS" "$SLUG"
      exit 0
    fi
    rc=0; require_ownership_or_force "$SLUG" release || rc=$?
    [ "$rc" -eq 0 ] || exit "$rc"
    printf '%s: releasing %s%s (was: %s — %s)\n' "$PROG" "$CLAIM_NS" "$SLUG" \
      "$(human_subject "$SLUG" "$(git_ -C "$WS" log -1 --format='%s' "refs/claims/$SLUG" 2>/dev/null || true)")" \
      "$(git_ -C "$WS" log -1 --format='%an, %aI' "refs/claims/$SLUG" 2>/dev/null || printf 'details unreadable')"
    gitnet -C "$WS" push -q --no-verify origin ":${CLAIM_NS}${SLUG}" \
      || degrade "could not delete ${CLAIM_NS}${SLUG} on '$REMOTE_URL'"
    printf '%s: RELEASED %s%s\n' "$PROG" "$CLAIM_NS" "$SLUG"
    exit 0
    ;;

  steal)
    # The escape hatch for a STALE claim, and the reason a stale ref cannot block
    # an item forever. Deliberately a SEPARATE, EXPLICIT verb — the claim path
    # below must never force, or the lock stops being a lock. And it is gated:
    # `--steal` of a LIVE claim that is not yours is refused, because otherwise
    # rc 10's "DO NOT start this item" is one flag away from being ignored.
    sha_existing="$(remote_ref_sha "$SLUG")" \
      || degrade "could not reach '$REMOTE_URL' to steal '$SLUG'"
    if [ -n "$sha_existing" ]; then
      rc=0; require_ownership_or_force "$SLUG" steal || rc=$?
      [ "$rc" -eq 0 ] || exit "$rc"
      printf '%s: stealing %s%s from %s\n' "$PROG" "$CLAIM_NS" "$SLUG" \
        "$(git_ -C "$WS" log -1 --format='%an, %aI' "refs/claims/$SLUG" 2>/dev/null || printf 'an unreadable claim')"
    fi
    sha="$(make_claim_commit "$SLUG" "$SUBJECT")" || degrade "could not build the claim commit"
    gitnet -C "$WS" push -q --no-verify --force origin "${sha}:${CLAIM_NS}${SLUG}" \
      || degrade "could not steal ${CLAIM_NS}${SLUG} on '$REMOTE_URL'"
    printf '%s: STOLEN — %s%s is now yours\n' "$PROG" "$CLAIM_NS" "$SLUG"
    exit 0
    ;;

  claim)
    sha="$(make_claim_commit "$SLUG" "$SUBJECT")" || degrade "could not build the claim commit"

    # 🔴🔴 THIS LINE IS THE LOCK. No `--force`, no `--force-with-lease`, no
    # preceding "does it exist?" read that we then act on. Two true concurrent
    # first movers both send `old = 0000…`, and the receiving git's ref
    # transaction is a compare-and-swap on that value — so exactly one create
    # lands, decided on the server at update time. A serialized second mover is
    # refused client-side instead, because it cannot fast-forward onto a sha it
    # does not have. Adding a force flag here does not make the tool more robust;
    # it deletes the entire guarantee. There is a mutation control in the test
    # suite that proves it. (`--no-verify` is NOT a force: it only stops the
    # OPERATOR's global pre-push hook from making this push fail — see git_.)
    push_err="$WS/push.err"
    if gitnet -C "$WS" push -q --no-verify origin "${sha}:${CLAIM_NS}${SLUG}" 2>"$push_err"; then
      printf '%s: CLAIMED %s%s\n' "$PROG" "$CLAIM_NS" "$SLUG"
      [ -n "$SUBJECT" ] && printf '  what:  %s\n' "$SUBJECT"
      printf '  who:   %s <%s>\n' "$ident_name" "$ident_mail"
      printf '  where: host %s, cwd-id %s\n' "$MY_HOST" "$MY_CWD_ID"
      printf '  on:    %s [%s]\n' "$REMOTE_URL" "$REMOTE_SOURCE"
      printf '  release it when done or abandoned:  %s --release %s\n' "$PROG" "$SLUG"
      exit 0
    fi

    # The push failed. WHY it failed decides everything, and "nothing happened"
    # cannot distinguish the mechanisms — so go and ask the remote which one it
    # was instead of assuming the one we expect.
    existing="$(remote_ref_sha "$SLUG")" \
      || degrade "push to '$REMOTE_URL' failed and the remote could not be re-read: $(tr '\n' ' ' <"$push_err")"

    # 🔴 OUR OWN COMMIT IS ON THE REF: THE PUSH LANDED AND THE CLIENT FAILED
    # AFTERWARDS. This case used to fall through to "does not exist there — not a
    # collision" and exit 0 "Proceeding UNCLAIMED" — so the holder of a live
    # claim believed it held nothing, and the ref sat there blocking the item for
    # the whole TTL with nobody to release it. Reproduced deterministically by
    # injecting a post-push client failure, and organically once in ~100 tries
    # under a tight DEVRC_CLAIM_TIMEOUT. WE WON: say so.
    if [ -n "$existing" ] && [ "$existing" = "$sha" ]; then
      warn "the push reported failure but ${CLAIM_NS}${SLUG} on '$REMOTE_URL' carries OUR commit — the claim landed and the client failed afterwards: $(tr '\n' ' ' <"$push_err")"
      printf '%s: CLAIMED %s%s (the push errored AFTER the ref landed)\n' "$PROG" "$CLAIM_NS" "$SLUG"
      [ -n "$SUBJECT" ] && printf '  what:  %s\n' "$SUBJECT"
      printf '  who:   %s <%s>\n' "$ident_name" "$ident_mail"
      printf '  where: host %s, cwd-id %s\n' "$MY_HOST" "$MY_CWD_ID"
      printf '  on:    %s [%s]\n' "$REMOTE_URL" "$REMOTE_SOURCE"
      printf '  sha:   %s\n' "$existing"
      printf '  release it when done or abandoned:  %s --release %s\n' "$PROG" "$SLUG"
      exit 0
    fi

    if [ -n "$existing" ]; then
      rc=0; report_existing "$SLUG" || rc=$?
      exit "$rc"
    fi
    degrade "push to '$REMOTE_URL' failed, but ${CLAIM_NS}${SLUG} does not exist there — not a collision: $(tr '\n' ' ' <"$push_err")"
    ;;

  *)
    die_usage "internal: unknown mode '$MODE'"
    ;;
esac
