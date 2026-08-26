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
# ── THE MECHANISM, IN ONE SENTENCE ────────────────────────────────────────────
# Publishing an ORPHAN commit to `refs/heads/claim/<slug>` on `origin` IS the
# claim, and because every claim commit is an unrelated orphan root, a push to an
# ALREADY-CLAIMED ref is rejected NON-FAST-FORWARD by the receiving git.
#
# 🔴 THAT IS THE WHOLE POINT, AND IT IS NOT A CHECK-THEN-ACT. The lock is git's
# own ref compare-and-swap, performed under the ref transaction lock on the
# server at update time — not a read we take and then act on. Two simultaneous
# first movers therefore resolve to EXACTLY ONE winner with no TOCTOU window.
# `scripts/tests/test_claim_work.py::test_the_lock_is_gits_own_ref_compare_and_
# swap_not_a_check_then_act` verifies that empirically against a real git server
# process rather than asserting it, and a mutation control in the same file
# proves the assertion is load-bearing by defeating the lock with `--force`.
#
# 🔴 IT PROTECTS THE **FIRST** MOVER, which is the half a pre-flight check
# structurally cannot. `gh pr list` only ever tells the LATER session that
# somebody else started; whoever moves first cannot see the second session
# because it does not exist yet at branch-creation time. The claim happens at
# DRAW time, before any work, so the first mover is the one it covers.
#
# 🔴 WORKTREE ISOLATION IS NOT AN ALTERNATIVE TO THIS AND NEVER WAS. Every
# colliding session was already in its own worktree. A worktree prevents a
# FILESYSTEM collision; this is a TASK-ALLOCATION collision, and isolation is
# what HIDES it. Do not "fix" a collision by adding more isolation.
#
# ── 🔴 IT FAILS OPEN, ON PURPOSE ──────────────────────────────────────────────
# No origin, no network, no auth, not a git repo, git missing, remote hung ⇒
# WARN ON STDERR AND EXIT 0, degrading to the behaviour we had before this
# script existed. A bug in here is felt by EVERY `/resume`, so it must never be
# able to block one. Every network call is wrapped in a bounded `timeout` so a
# hung remote cannot hang a resume either. `--strict` turns a degraded run into
# rc 20 instead; it exists for tests and CI, and must never be the default.
#
# ── THIS SCRIPT NEVER TOUCHES THE CALLER'S REPOSITORY ─────────────────────────
# It READS one thing from it — `remote.origin.url` — and does everything else in
# a throwaway BARE repo under a mktemp dir that is removed on exit. No index, no
# working tree, no local branch, no FETCH_HEAD, no stash, no objects written into
# the caller's object database. That is deliberate: this runs at the start of
# every resumed session, frequently in a shared checkout, and a claim tool that
# can perturb the tree it is claiming work in would be worse than the collision.
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
#   10  ALREADY CLAIMED, and the claim is LIVE. WHO/WHEN/WHAT are printed. Do
#         NOT start this item; pick another, or talk to the claimer.
#   11  ALREADY CLAIMED but the claim is STALE (older than DEVRC_CLAIM_TTL_DAYS,
#         default 7). A stale ref would otherwise block an item forever. Decide:
#         `--steal <slug>` to take it over, or `--release <slug>` to drop it.
#   20  DEGRADED **and** `--strict` was passed. Never emitted without --strict.
#
# ── ENVIRONMENT ───────────────────────────────────────────────────────────────
#   DEVRC_CLAIM_REMOTE      override the remote URL (else: origin of --repo)
#   DEVRC_CLAIM_TTL_DAYS    staleness threshold in days (default 7)
#   DEVRC_CLAIM_TIMEOUT     per-network-call timeout, `timeout` syntax (default 20s)
set -euo pipefail

PROG=claim-work

# The ref namespace. Under refs/heads/ ON PURPOSE: a normal branch ref is what
# every git server enforces the fast-forward rule on, and it is what `git
# ls-remote` / the GitHub UI will show without extra configuration. A custom
# refs/claims/* namespace would need server-side rules we do not control.
CLAIM_NS="refs/heads/claim/"

TTL_DAYS="${DEVRC_CLAIM_TTL_DAYS:-7}"
NET_TIMEOUT="${DEVRC_CLAIM_TIMEOUT:-20s}"

DEFAULT_TTL_DAYS=7

RC_USAGE=2
RC_TAKEN=10
RC_TAKEN_STALE=11
RC_DEGRADED_STRICT=20

MODE=claim
SLUG=""
SUBJECT=""
REPO="$PWD"
REMOTE_OVERRIDE="${DEVRC_CLAIM_REMOTE:-}"
STRICT=0
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

usage() {
  cat >&2 <<'EOF'
claim-work — claim one ranked next-step item so a second session cannot start it.

  claim-work <slug> [--subject "<human text>"]   claim it        (0 won / 10 taken / 11 stale)
  claim-work --check <slug>                      is it taken?    (0 free / 10 taken / 11 stale)
  claim-work --list                              every live claim, with age + subject
  claim-work --release <slug>                    drop the claim
  claim-work --steal <slug> [--subject "..."]    take over a stale/abandoned claim
  claim-work --slug-for <handoff-doc> [<rank>]   print the CANONICAL slug for an item

Options:
  --repo <path>     repository whose `origin` to use (default: cwd)
  --remote <url>    use this remote instead of resolving one
  --strict          exit 20 instead of 0 when it cannot reach origin (tests/CI)

It FAILS OPEN: no origin / no network / no auth ⇒ warning on stderr, exit 0.
Exit codes and the design argument are documented at the top of this file.
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
      if [ "$#" -gt 0 ] && printf '%s' "$1" | grep -Eq '^[0-9]+$'; then
        SLUG_RANK="$1"; shift
      fi
      ;;
    --subject)   SUBJECT="${2:-}"; shift 2 || die_usage "--subject needs text" ;;
    --repo)      REPO="${2:-}";    shift 2 || die_usage "--repo needs a path" ;;
    --remote)    REMOTE_OVERRIDE="${2:-}"; shift 2 || die_usage "--remote needs a url" ;;
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
# (or an agent) reads. Do not claim it catches reworded duplicates. It does not.
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
validate_slug() {
  local s="$1"
  [ -n "$s" ] || die_usage "no slug given"
  printf '%s' "$s" | grep -Eq '^[a-z0-9][a-z0-9._-]*$' \
    || die_usage "malformed slug '$s' — lowercase a-z 0-9 . _ - only, must start alphanumeric"
  case "$s" in
    *..*) die_usage "malformed slug '$s' — '..' is not allowed in a ref name" ;;
    *.)   die_usage "malformed slug '$s' — a ref component may not end in '.'" ;;
  esac
  [ "${#s}" -le 100 ] || die_usage "slug too long (${#s} > 100)"
}

if [ "$MODE" = "slug-for" ]; then
  [ -n "$SLUG_DOC" ] || die_usage "--slug-for needs a handoff-doc path"
  out="$(derive_slug "$SLUG_DOC" "$SLUG_RANK")" \
    || die_usage "cannot derive a slug from '$SLUG_DOC'"
  validate_slug "$out"
  printf '%s\n' "$out"
  exit 0
fi

[ "$MODE" = "list" ] || validate_slug "$SLUG"

# ── plumbing ──────────────────────────────────────────────────────────────────
command -v git >/dev/null 2>&1 || degrade "git is not on PATH"

# Bounded network calls. A hung remote must never hang a resume. `timeout` is
# coreutils and is present on both hosts and in the nix sandbox; if it somehow
# is not, we still run — degrading to "slow" is better than degrading to
# "unusable", and git's own transport timeouts below remain in force.
TIMEOUT_BIN="$(command -v timeout || true)"
gitnet() {
  if [ -n "$TIMEOUT_BIN" ]; then
    "$TIMEOUT_BIN" "$NET_TIMEOUT" git "$@"
  else
    git "$@"
  fi
}

# Never sit waiting for a human to type a password into an agent's session.
export GIT_TERMINAL_PROMPT=0
unset GIT_ASKPASS SSH_ASKPASS 2>/dev/null || true
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o BatchMode=yes -o ConnectTimeout=10}"

# 🔴 NOT a function whose output is captured. `degrade` EXITS, and an `exit`
# inside a `$(command substitution)` only leaves the subshell — the script would
# sail on with an empty remote and a warning nobody acted on. So the resolution
# assigns a global in the CURRENT shell instead.
REMOTE_URL=""
if [ -n "$REMOTE_OVERRIDE" ]; then
  REMOTE_URL="$REMOTE_OVERRIDE"
else
  git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 \
    || degrade "'$REPO' is not a git repository (and no --remote/DEVRC_CLAIM_REMOTE given)"
  REMOTE_URL="$(git -C "$REPO" remote get-url origin 2>/dev/null || true)"
  [ -n "$REMOTE_URL" ] \
    || degrade "'$REPO' has no 'origin' remote (and no --remote/DEVRC_CLAIM_REMOTE given)"
fi

# The throwaway bare repo. Everything below happens HERE, never in $REPO.
WS="$(mktemp -d -t claim-work.XXXXXXXX)"
cleanup() { rm -rf "$WS"; }
trap cleanup EXIT

git init -q --bare "$WS" >/dev/null 2>&1 || degrade "could not create a scratch repository under $WS"
git -C "$WS" remote add origin "$REMOTE_URL" >/dev/null 2>&1 \
  || degrade "could not attach remote '$REMOTE_URL'"

# ── identity: WHO the claim names ─────────────────────────────────────────────
# Read from the CALLER's git config so the claim carries a real person/agent,
# not the scratch repo's (absent) identity. Falls back to $USER@<host> so a
# hermetic environment with no git config can still claim.
ident_name="$(git -C "$REPO" config --get user.name 2>/dev/null || true)"
ident_mail="$(git -C "$REPO" config --get user.email 2>/dev/null || true)"
[ -n "$ident_name" ] || ident_name="${USER:-unknown}"
[ -n "$ident_mail" ] || ident_mail="${USER:-unknown}@$(uname -n 2>/dev/null || echo localhost)"

remote_ref_sha() {
  # Prints the sha of the claim ref on origin, or nothing. Returns non-zero ONLY
  # when the remote could not be reached at all — the caller must distinguish
  # "not claimed" from "could not find out", because collapsing those two is
  # how a claim tool starts silently reporting FREE for everything.
  local s="$1" out
  out="$(gitnet -C "$WS" ls-remote origin "${CLAIM_NS}${s}" 2>/dev/null)" || return 1
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

# Prints WHO / WHEN / WHAT for an existing claim and returns the right rc:
# RC_TAKEN while live, RC_TAKEN_STALE once past the TTL.
report_existing() {
  local s="$1" who when subject epoch now age
  if ! fetch_claim "$s"; then
    # The ref exists (ls-remote saw it) but we could not read the commit. Still
    # a claim — report TAKEN with what we know rather than pretending it is free.
    printf '%s: ALREADY CLAIMED — %s%s (details unreadable)\n' "$PROG" "$CLAIM_NS" "$s"
    return "$RC_TAKEN"
  fi
  who="$(git -C "$WS" log -1 --format='%an <%ae>' "refs/claims/$s")"
  when="$(git -C "$WS" log -1 --format='%aI' "refs/claims/$s")"
  epoch="$(git -C "$WS" log -1 --format='%at' "refs/claims/$s")"
  subject="$(git -C "$WS" log -1 --format='%s' "refs/claims/$s")"
  now="$(date +%s)"
  age=$(( now - epoch ))
  [ "$age" -ge 0 ] || age=0

  printf '%s: ALREADY CLAIMED — %s%s\n' "$PROG" "$CLAIM_NS" "$s"
  printf '  who:   %s\n' "$who"
  printf '  when:  %s  (%s ago)\n' "$when" "$(human_age "$age")"
  printf '  what:  %s\n' "$subject"

  if [ "$age" -gt $(( TTL_DAYS * 86400 )) ]; then
    printf '  STALE: older than %s day(s) — it may be abandoned.\n' "$TTL_DAYS"
    printf '         %s --steal %s   (take it over)\n' "$PROG" "$s"
    printf '         %s --release %s (drop it)\n' "$PROG" "$s"
    return "$RC_TAKEN_STALE"
  fi
  printf '  DO NOT start this item. Pick another, or coordinate with the claimer.\n'
  return "$RC_TAKEN"
}

make_claim_commit() {
  # An ORPHAN commit: `commit-tree` with NO `-p`, over the empty tree. Every
  # claim is therefore an unrelated root, which is precisely what makes a second
  # push to the same ref a NON-FAST-FORWARD and hence rejected. Do not give this
  # a parent, and do not build it from an existing branch.
  local s="$1" subj="$2" tree msg
  tree="$(git -C "$WS" mktree </dev/null)"
  if [ -n "$subj" ]; then
    msg="claim($s): $subj"
  else
    msg="claim($s)"
  fi
  # The nonce guarantees two concurrent claimants produce DIFFERENT shas, so a
  # rejected push can never be misread as "I already had it".
  GIT_AUTHOR_NAME="$ident_name" GIT_AUTHOR_EMAIL="$ident_mail" \
  GIT_COMMITTER_NAME="$ident_name" GIT_COMMITTER_EMAIL="$ident_mail" \
    git -C "$WS" commit-tree "$tree" <<EOF
$msg

claimed-by: $ident_name <$ident_mail>
host: $(uname -n 2>/dev/null || echo unknown)
cwd: $REPO
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
      [ "$age" -gt $(( TTL_DAYS * 86400 )) ] && flag="  [STALE]"
      printf '%-40s %6s ago  %-24s %s%s\n' "$ref" "$(human_age "$age")" "$who" "$subject" "$flag"
    done <<EOF
$(git -C "$WS" for-each-ref --sort=-authordate \
    --format='%(refname:lstrip=2)|%(authordate:unix)|%(authorname)|%(contents:subject)' \
    refs/claims 2>/dev/null)
EOF
    if [ "$n" -eq 0 ]; then
      printf '%s: no live claims on %s\n' "$PROG" "$REMOTE_URL"
    else
      printf '\n%s: %d live claim(s). 🔴 The SUBJECT column is a SOFT signal — scan it for a\n' "$PROG" "$n"
      printf '  near-duplicate of your item whose SLUG differs. Only an exact slug match locks.\n'
    fi
    exit 0
    ;;

  release)
    sha="$(remote_ref_sha "$SLUG")" || degrade "could not reach '$REMOTE_URL' to release '$SLUG'"
    if [ -z "$sha" ]; then
      printf '%s: nothing to release — %s%s does not exist\n' "$PROG" "$CLAIM_NS" "$SLUG"
      exit 0
    fi
    if fetch_claim "$SLUG"; then
      printf '%s: releasing %s%s (was: %s)\n' "$PROG" "$CLAIM_NS" "$SLUG" \
        "$(git -C "$WS" log -1 --format='%s — %an, %aI' "refs/claims/$SLUG")"
    fi
    gitnet -C "$WS" push -q origin ":${CLAIM_NS}${SLUG}" \
      || degrade "could not delete ${CLAIM_NS}${SLUG} on '$REMOTE_URL'"
    printf '%s: RELEASED %s%s\n' "$PROG" "$CLAIM_NS" "$SLUG"
    exit 0
    ;;

  steal)
    # The escape hatch for a STALE claim, and the reason a stale ref cannot block
    # an item forever. Deliberately a SEPARATE, EXPLICIT verb — the claim path
    # below must never force, or the lock stops being a lock.
    sha="$(make_claim_commit "$SLUG" "$SUBJECT")" || degrade "could not build the claim commit"
    if fetch_claim "$SLUG"; then
      printf '%s: stealing %s%s from %s\n' "$PROG" "$CLAIM_NS" "$SLUG" \
        "$(git -C "$WS" log -1 --format='%an, %aI' "refs/claims/$SLUG")"
    fi
    gitnet -C "$WS" push -q --force origin "${sha}:${CLAIM_NS}${SLUG}" \
      || degrade "could not steal ${CLAIM_NS}${SLUG} on '$REMOTE_URL'"
    printf '%s: STOLEN — %s%s is now yours\n' "$PROG" "$CLAIM_NS" "$SLUG"
    exit 0
    ;;

  claim)
    sha="$(make_claim_commit "$SLUG" "$SUBJECT")" || degrade "could not build the claim commit"

    # 🔴🔴 THIS LINE IS THE LOCK. No `--force`, no `--force-with-lease`, no
    # preceding "does it exist?" read that we then act on. The orphan commit is
    # unrelated to whatever is already there, so the receiving git rejects it
    # NON-FAST-FORWARD under its own ref transaction lock — an atomic
    # compare-and-swap, decided on the server at update time. Adding a force
    # flag here does not make the tool more robust; it deletes the entire
    # guarantee. There is a mutation control in the test suite that proves it.
    push_err="$WS/push.err"
    if gitnet -C "$WS" push -q origin "${sha}:${CLAIM_NS}${SLUG}" 2>"$push_err"; then
      printf '%s: CLAIMED %s%s\n' "$PROG" "$CLAIM_NS" "$SLUG"
      [ -n "$SUBJECT" ] && printf '  what:  %s\n' "$SUBJECT"
      printf '  who:   %s <%s>\n' "$ident_name" "$ident_mail"
      printf '  release it when done or abandoned:  %s --release %s\n' "$PROG" "$SLUG"
      exit 0
    fi

    # The push failed. WHY it failed decides everything, and "nothing happened"
    # cannot distinguish the two mechanisms — so go and ask the remote which one
    # it was instead of assuming the one we expect.
    existing="$(remote_ref_sha "$SLUG")" \
      || degrade "push to '$REMOTE_URL' failed and the remote could not be re-read: $(tr '\n' ' ' <"$push_err")"
    if [ -n "$existing" ] && [ "$existing" != "$sha" ]; then
      rc=0; report_existing "$SLUG" || rc=$?
      exit "$rc"
    fi
    degrade "push to '$REMOTE_URL' failed, but ${CLAIM_NS}${SLUG} does not exist there — not a collision: $(tr '\n' ' ' <"$push_err")"
    ;;

  *)
    die_usage "internal: unknown mode '$MODE'"
    ;;
esac
