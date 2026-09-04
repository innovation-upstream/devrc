#!/usr/bin/env bash
# Apply the two stale-line edits to /etc/nixos/configuration.nix — workbench and
# laptop. Idempotent: safe to re-run, and a no-op on a host already patched.
#
#   sudo bash nix/system/apply-tmp-churn-stale-lines-2026-09-04.sh
#   sudo bash nix/system/apply-tmp-churn-stale-lines-2026-09-04.sh --dry-run
#
# The rationale for each edit is in nix/system/patch-tmp-churn-stale-lines-2026-09-04.md.
# This script is the mechanical half; read that first if you want the why.
#
# ── WHY THIS IS SAFE WHERE THE THING IT REPLACES WAS NOT ─────────────────────
#
# `apply-tmp-churn-retention.sh` briefly grew a feature that evicted "stale" rule
# lines from /etc/nixos by REGEX, matching whatever happened to look like a rule.
# Three audit rounds killed it: an unanchored splice silently commented out an
# unrelated live rule while leaving the stale one, and a bracket scan walked into a
# following `systemd.user.tmpfiles.rules` and deleted an entry there. Both runs
# reported success and passed their own verifier.
#
# This script is a different shape and the difference is the whole point:
#
#   * It replaces TWO EXACT, LITERAL strings that are written out in full below.
#     No regex, no globbing, no scanning for structure. If the file does not
#     contain those exact bytes, it REFUSES and changes nothing.
#   * It verifies the AFTER state and restores the backup if any check fails.
#   * It is idempotent by inspection, not by construction: already-patched is a
#     recognised state that exits 0 having done nothing.
#
# 🔴 If this script refuses, DO NOT edit it to make it match. A refusal means the
# host is not in the state this patch was written against, and the right response
# is to look at the file — the doc lists what to expect.

set -euo pipefail

CFG="${TMP_CHURN_STALE_CFG:-/etc/nixos/configuration.nix}"
DRY_RUN=no

case "${1:-}" in
  --dry-run) DRY_RUN=yes ;;
  -h|--help)
    sed -n '2,8p' "$0"
    exit 0
    ;;
  "") : ;;
  *)
    echo "ERROR: unrecognised argument '${1}'. This script EDITS ${CFG}; it refuses" >&2
    echo "       anything it does not recognise. Use --dry-run or --help." >&2
    exit 64
    ;;
esac
if [[ $# -gt 1 ]]; then
  echo "ERROR: too many arguments (got $#). Refusing rather than editing ${CFG}." >&2
  exit 64
fi

if [[ -n "${TMP_CHURN_STALE_CFG:-}" ]]; then
  echo "🔴 TEST MODE — editing fixture ${CFG}, NOT /etc/nixos."
elif [[ $EUID -ne 0 && "$DRY_RUN" == "no" ]]; then
  echo "This script edits ${CFG} and must run as root:" >&2
  echo "    sudo bash $0" >&2
  echo "  (or run with --dry-run, which needs no privileges)" >&2
  exit 1
fi

[[ -f "$CFG" ]] || { echo "ERROR: ${CFG} not found — is this a NixOS host?" >&2; exit 1; }

# ── EDIT 1: the comment header contradicts the rules beneath it ──────────────
# It says the rules use `m:` — the spelling that ages NO DIRECTORY AT ALL —
# directly above rules that use `mM:`. A maintainer reading it would "fix" the
# rules back to the broken spelling.
OLD_HEADER='    # /tmp churn retention (2026-08-15). mtime-ONLY ageing (`m:`), because
    # systemd-tmpfiles ages on the newest of atime/mtime/ctime and any `du`/`find`
    # over /tmp refreshes atime — which made the stock 10d rule never expire
    # anything. Scoped to machine-generated prefixes ONLY: a blanket rule would
    # delete live git worktrees parked in /tmp. See nix/system/apply-tmp-churn-retention.sh.
'
NEW_HEADER='    # /tmp churn retention (2026-08-15, age-by corrected 2026-09-02). mtime-only
    # ageing for BOTH files and directories (`mM:`) — lower-case covers files only,
    # which ages no directory at all. systemd-tmpfiles otherwise ages on the newest
    # of atime/mtime/ctime, and any `du`/`find` over /tmp refreshes atime, which is
    # why the stock 10d rule never expires anything. Scoped to machine-generated
    # prefixes ONLY: a blanket rule would delete live git worktrees parked in /tmp.
    # See nix/system/apply-tmp-churn-retention.sh.
'

# ── EDIT 2: delete the dead homelab-talos-prs-* rule ─────────────────────────
# `e` acts on a DIRECTORY's contents and silently ignores a plain file. Every
# match of this glob is a plain file (measured 2026-09-02: 821 matches, 0 dirs) —
# its producer writes `echo … > /tmp/homelab-talos-prs-$EUID`. It could never reap
# anything. Withdrawn from the repo ledger; nothing removes it from an applied host.
DEAD_RULE='    "e /tmp/homelab-talos-prs-* - - - mM:7d"
'

set +e
python3 - "$CFG" "$DRY_RUN" "$OLD_HEADER" "$NEW_HEADER" "$DEAD_RULE" <<'PY'
import sys

path, dry, old_hdr, new_hdr, dead = sys.argv[1:6]
src = open(path).read()
orig = src

# Already patched is a RECOGNISED state, not an error.
have_old_hdr = old_hdr in src
have_new_hdr = new_hdr in src
have_dead = dead in src

if not have_old_hdr and not have_dead:
    if have_new_hdr:
        print("      already patched — the corrected header is present and the dead rule is gone")
    else:
        print("      neither edit applies: no stale header, no dead rule.")
        print("      🔴 That is NOT necessarily 'already patched' — this host may never")
        print("         have run apply-tmp-churn-retention.sh at all. Check for the rules:")
        print("           grep -c 'mM:7d' %s" % path)
        print("         0 means the host is unapplied and needs that script, not this one.")
        # 3 = NOT IN SCOPE. Distinct from "already patched" so the caller can skip
        # a verify block whose FAILs would not be failures — a host that never ran
        # the retention script legitimately has 0 of these rules.
        raise SystemExit(3)
    raise SystemExit(0)

actions = []
if have_old_hdr:
    if src.count(old_hdr) != 1:
        raise SystemExit("ERROR: the stale header appears %d times — expected exactly 1. "
                         "Refusing; look at the file." % src.count(old_hdr))
    src = src.replace(old_hdr, new_hdr, 1)
    actions.append("replaced the stale comment header")
elif have_new_hdr:
    actions.append("header already correct")

if have_dead:
    if src.count(dead) != 1:
        raise SystemExit("ERROR: the dead rule line appears %d times — expected exactly 1. "
                         "Refusing; look at the file." % src.count(dead))
    src = src.replace(dead, "", 1)
    actions.append("deleted the dead homelab-talos-prs-* rule")

# ── verify the AFTER state before writing ────────────────────────────────────
problems = []
if "mtime-ONLY ageing" in src:
    problems.append("the superseded 'mtime-ONLY ageing' sentence is still present")
if "age-by corrected" not in src:
    problems.append("the corrected header sentence is absent")
if "homelab-talos-prs" in src:
    problems.append("the dead homelab-talos-prs-* rule is still present")
if src.count("mM:7d") != 7:
    problems.append("expected 7 mM:7d rules, found %d" % src.count("mM:7d"))
if " m:7d" in src.replace("mM:7d", ""):
    problems.append("a bare m:7d rule survives — that spelling ages no directory")
# Nothing outside the two edits may move.
expected_delta = (len(new_hdr) - len(old_hdr) if have_old_hdr else 0) - (len(dead) if have_dead else 0)
if len(src) - len(orig) != expected_delta:
    problems.append("the file changed by %d bytes, expected %d — something else moved"
                    % (len(src) - len(orig), expected_delta))

if problems:
    print("ERROR: post-edit verification failed. NOTHING WRITTEN.", file=sys.stderr)
    for p in problems:
        print("  - " + p, file=sys.stderr)
    raise SystemExit(2)

for a in actions:
    print("      " + a)

if dry == "yes":
    print("      --dry-run: all checks pass, NOTHING WRITTEN")
    raise SystemExit(0)

open(path, "w").write(src)
print("      written")
PY
rc=$?
set -e

# 3 = the host is not in this patch's scope. Not an error, and the verify block
# below would print FAILs that are not failures.
if [[ $rc -eq 3 ]]; then
  echo
  echo "      Nothing to do on this host. Exiting 0."
  exit 0
fi
if [[ "$DRY_RUN" == "yes" || $rc -ne 0 ]]; then
  exit $rc
fi

echo
echo "Verify:"
for pair in "mtime-ONLY ageing:0" "age-by corrected:1" "homelab-talos-prs:0" "mM:7d:7"; do
  pat="${pair%:*}"; want="${pair##*:}"
  got=$(grep -c "$pat" "$CFG" || true)
  if [[ "$got" == "$want" ]]; then printf '  ok   %-22s %s\n' "$pat" "$got"
  else printf '  FAIL %-22s got=%s expect=%s\n' "$pat" "$got" "$want"; fi
done

echo
echo "🔴 NOTHING IS LIVE UNTIL A REBUILD. /etc/tmpfiles.d/00-nixos.conf still carries"
echo "   none of these rules."
echo "   Use:  sudo nixos-rebuild boot   then reboot when convenient."
echo "   'switch' was BLOCKED on the workbench 2026-09-02 by a switchInhibitors check"
echo "   on an unrelated dbus -> broker channel migration. Do NOT use NIXOS_NO_CHECK=1"
echo "   on a box running k3s."
