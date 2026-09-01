#!/usr/bin/env bash
# /tmp churn retention — workbench, 2026-08-15.
#
# Claude cannot run `sudo nixos-rebuild`, so the /etc/nixos edit is staged here.
# Idempotent: safe to re-run.
#
#   sudo bash nix/system/apply-tmp-churn-retention.sh
#
# Safe on a non-TTY (pipe, ssh without -t, agent shell): the prompt is skipped
# rather than aborting mid-edit, and any error restores the backup via an ERR
# trap — same shape as apply-perf-tuning-2026-07-30.sh next door.
#
# ── THE PROBLEM, AND WHY THE OBVIOUS FIX IS NOT IT ───────────────────────────
#
# workbench went DiskPressure=True on 2026-08-13 and evicted node-wide (clawgate,
# supabase, remix-worker, auditloop, orchestrator-devpod). The disk genuinely
# filled: 1.7 TB of 1.8 TB, down to 101 GiB free against a ~92 GiB eviction line.
#
# 🔴 Do NOT reach for the two fixes the incident handoff proposed — both are
# no-ops, measured 2026-08-15:
#   - "relax eviction-hard to 5%": it is ALREADY 5%. That is a k3s BUILT-IN
#     default, present in no config file, surviving every rebuild. (And writing
#     the flag by hand is hazardous: eviction-hard REPLACES the whole default
#     set, so spelling only the two disk signals silently drops memory.available
#     and both inodesFree guards.)
#   - "add systemd-tmpfiles ageing on /tmp": it ALREADY exists. systemd ships
#     `q /tmp 1777 root root 10d` and systemd-tmpfiles-clean.timer runs daily and
#     succeeds. `systemd-tmpfiles --clean --dry-run` reclaims 0.55 GiB.
#
# ── ROOT CAUSE: atime keeps stale dirs immortal ──────────────────────────────
#
# systemd-tmpfiles ages on the MOST RECENT of atime/mtime/ctime. Measured on
# real /tmp entries:
#
#   /tmp/nix-shell-3304470-3498967483   m=2026-08-02  a=2026-08-14  c=2026-08-02
#   /tmp/nix-shell-2760455-2726689738   m=2026-07-30  a=2026-08-14  c=2026-07-30
#
# mtime/ctime are weeks old; atime is yesterday. So the 10d rule sees "1 day old"
# and never expires them.
#
# 🔴 And what refreshes atime is SCANNING /tmp with du or find — precisely what
# investigating a full disk requires. Every investigation resets the clock on
# everything it looks at. That is why /tmp sat at 362 GB with a working cleaner.
#
# ── THE FIX: mtime-only ageing, SCOPED to machine-generated churn ────────────
#
# systemd >= 253 accepts an age-by prefix (`m:7d` = consider mtime only). Verified
# on this host's systemd 258 by control pair: `z:7d` -> "Invalid age-by 'z'",
# `m:7d` -> parses and matches.
#
# 🔴 It is scoped by GLOB, not applied to /tmp as a whole, because /tmp holds
# real work that a blanket rule would destroy. Measured: a blanket mtime-only
# 7d rule would have deleted TWO LIVE GIT WORKTREES —
#   /tmp/wt-apps-ui-3497  (14d)  gitdir: ~/workspace/civit/civitai/.git/worktrees/...
#   /tmp/wt-waitunit      (10d)  gitdir: ~/workspace/civit/civitai/.git/worktrees/...
# possibly carrying uncommitted work. Hence: machine-generated prefixes only.
#
# Coverage, measured 2026-08-15 (dirs / of those with mtime >7d):
#   nix-shell-*           1003 /  988      go-build*              114 /  98
#   nix-develop-*         1196 /  392      chromedp-runner*       158 / 158
#   nix-[0-9]*             778 /  612      homelab-talos-prs-*   2554 / 2537
# Every glob matches real entries — none is a dead rule.
#
# 🔴 A HYPHEN IS NOT A WILDCARD: `nix-shell-*` CANNOT MATCH `nix-shell.<mktemp>`.
# nix-shell writes its TMPDIR in two spellings and this rule set only ever saw
# one of them. Re-measured live on the workbench 2026-08-22, top-level /tmp:
#
#   nix-shell.*   3340   <- UNCOVERED until this rule
#   nix-shell-*   1017      covered since 2026-08-15
#
# The dot form outnumbers the hyphen form 3.3:1, and it is the form `gate.sh`
# produces on every agent run (its log lands in /tmp/nix-shell.<x>/devrc-gate-*/),
# so it is the one that grows fastest on a machine driven by agents. The glob
# below closes it. Control for the count above: an unrestricted `find /tmp
# -maxdepth 1` returns 119,066 — the zeros here are real zeros, not a walk that
# never ran.
#
# 🔴 Still UNCOVERED after this change, and deliberately not taken here — each is
# larger than everything this file reaps put together, and none has been checked
# against live work the way the globs above were:
#   cgparent-*  17064    fx-excerpt-*  14012    cbf-*  5268    tmp.*  4227
# `tmp.*` in particular is bare mktemp's default and would need its own audit.
#
# Dry-run of the exact rule set below: 4,255,261 entries would be removed, and
# ZERO matches against /tmp/wt-*, /tmp/claude-1000 or the named project dirs
# (civitai-integration, peakmaps, chunkcache, pnpmvar, audit187). Estimated
# reclaim ~47 GB, a LOWER BOUND — the dry-run ran unprivileged and cannot see
# root-owned paths.
#
# NOT covered on purpose:
#   - /tmp/claude-1000 (52 GB): Claude session scratchpads. The stock 10d /tmp
#     rule already covers them and they are the user's own working state; a
#     mid-session sweep is not worth 52 GB.
#   - /tmp/wt-*: real git worktrees. Never age these automatically.
#
# Rollback: cp -a the backup this script prints, then nixos-rebuild switch. The
# rules are additive and delete nothing at rebuild time — cleanup happens on the
# next systemd-tmpfiles-clean.timer firing.

set -euo pipefail

# TMP_CHURN_CFG points the edit at a fixture instead of the real config. It exists
# so the insert/idempotence logic can be exercised at all: without it the only way
# to test this script is to run it against /etc/nixos as root, which is precisely
# the thing you want tested BEFORE you do. Test mode needs no root because it
# touches no system file — and it says so loudly, so a real run can never be
# mistaken for one.
CFG="${TMP_CHURN_CFG:-/etc/nixos/configuration.nix}"
BAK="${CFG}.bak-tmp-churn-$(date +%Y%m%d-%H%M%S)"
MARKER='/tmp churn retention'

if [[ -n "${TMP_CHURN_CFG:-}" ]]; then
  echo "🔴 TEST MODE — editing fixture ${CFG}, NOT /etc/nixos. No system change."
elif [[ $EUID -ne 0 ]]; then
  echo "This script edits ${CFG} and must run as root:" >&2
  echo "    sudo bash $0" >&2
  exit 1
fi

if [[ ! -f "$CFG" ]]; then
  echo "ERROR: ${CFG} not found — is this workbench?" >&2
  exit 1
fi

cp -a "$CFG" "$BAK"
echo "Backup: ${BAK}"

restore_on_err() {
  echo "ERROR — restoring ${CFG} from ${BAK}" >&2
  cp -a "$BAK" "$CFG"
}
trap restore_on_err ERR

# 🔴 THE SKIP IS PER-RULE, NOT PER-MARKER. This block used to return early on
# `grep -qF "$MARKER"` alone, which meant a host that had ALREADY run the script
# could never receive a rule added later — the run printed "already present" and
# exited 0 over a config missing the new glob. That is how `nix-shell.*` would
# have shipped to nobody: the workbench is unapplied today, but the laptop's
# /etc/nixos is not readable from here, so its state is UNMEASURED, not clean.
# The ledger below is the single source of truth; the script inserts exactly the
# rules that are absent and then re-reads the file to prove every one landed.
# ── THE RULE LEDGER — ONE definition, read by BOTH the inserter and the verifier.
# 🔴 These used to be two hardcoded lists (a Python one that inserted, a bash one
# that verified). Nothing tied them together, so a rule added to the inserter and
# not to the verifier would be written and then "verified present" by a loop that
# never looked for it — the verification would report success over an unchecked
# rule. `claude/RULES.md` -> "One rule, one place": a predicate duplicated across
# call sites regenerates the same bug at every site.
TMPFILES_RULES=(
  'e /tmp/nix-shell-* - - - m:7d'
  'e /tmp/nix-shell.* - - - m:7d'
  'e /tmp/nix-develop-* - - - m:7d'
  'e /tmp/nix-[0-9]* - - - m:7d'
  'e /tmp/go-build* - - - m:7d'
  'e /tmp/chromedp-runner* - - - m:7d'
  'e /tmp/homelab-talos-prs-* - - - m:7d'
  # Added 2026-09-01. devrc's own scripts/run3 capture dirs: its header states they
  # are deliberately NOT removed ("reading the two files afterwards is the point"),
  # so they need retention, not a code fix. 1266 dirs, 438 with mtime >7d; none
  # carries a .git. Dry-run: 1066 entries would be removed.
  'e /tmp/run3.* - - - m:7d'
)
# 🔴 `e` ACTS ON A DIRECTORY'S CONTENTS AND SILENTLY IGNORES A PLAIN FILE. Every
# glob in this ledger must therefore name DIRECTORIES. Measured 2026-09-01 on a
# clean fixture (a 60-day-old file and a 60-day-old dir, rule `e <glob> - - - m:7d`):
# the file glob removed 0 with no error or warning; the directory removed its
# content. So these three, added and then withdrawn the same day, are DEAD RULES —
# they match thousands of real entries and reap none of them:
#     gh-status-response.*  18925 entries / 10065 mtime>7d  (0-byte .json)   -> 0
#     htoken.*               3202 /  2842  (13-byte "letsgethookie" marker)  -> 0
#     health.*               2949 /  2613  (2-byte json, "[]")               -> 0
# Do not re-add them in `e` form. Plain files at the top level of /tmp are reached
# only by the /tmp rule ITSELF, which is the stock atime-based `q /tmp ... 10d`
# this whole file exists to work around — and changing that one to mtime-only is
# the blanket rule the 2026-08-15 revision refused, because it would delete live
# git worktrees parked in /tmp. So this is a genuine gap, not an oversight.
#
# 🔴 The dry-run that catches a dead rule prints "Would remove" on STDERR, not
# stdout. `… 2>/dev/null | grep -c 'Would remove'` returns 0 for a rule that works
# perfectly — measured here as 0 on stdout against 1066 on stderr for the same
# command. Always read both streams before believing a zero.
# 🔴 DELIBERATELY NOT ADDED, and this is a measurement not a hunch. Sampled
# 2026-09-01: cgparent-* 19044 dirs averaging 3 inodes (~57K), fx-excerpt-*
# 15002 averaging 1 (~15K), cbf-* 7260 averaging 1, tmp.* 4088 averaging 1.
# Together ~80K inodes against a filesystem holding 96-97M — reaping them buys
# nothing measurable, and none of their producers has been identified. `tmp.*`
# is bare mktemp's default and would need its own audit. The 2026-08-15 revision
# declined these for lack of verification; the inode measurement now says the
# trade was never worth making.

echo "[1/1] reconciling tmpfiles rules in systemd.tmpfiles.rules"
python3 - "$CFG" "${TMPFILES_RULES[@]}" <<'PY'
import sys

path = sys.argv[1]
src = open(path).read()

HEADER = '''    # /tmp churn retention (2026-08-15). mtime-ONLY ageing (`m:`), because
    # systemd-tmpfiles ages on the newest of atime/mtime/ctime and any `du`/`find`
    # over /tmp refreshes atime — which made the stock 10d rule never expire
    # anything. Scoped to machine-generated prefixes ONLY: a blanket rule would
    # delete live git worktrees parked in /tmp. See nix/system/apply-tmp-churn-retention.sh.
'''

# 🔴 A hyphen is a LITERAL. `nix-shell-*` and `nix-shell.*` are two globs, and
# nix-shell writes both spellings — see the header for the 3340-vs-1017 count.
# The ledger arrives on argv from TMPFILES_RULES so the inserter and the verifier
# below cannot disagree about what the rule set is.
RULES = ['    "%s"' % r for r in sys.argv[2:]]
if not RULES:
    raise SystemExit("ERROR: no rules passed on argv — TMPFILES_RULES is empty")

missing = [r for r in RULES if r.strip() not in src]
if not missing:
    print("      all %d rules already present — nothing to insert" % len(RULES))
    raise SystemExit(0)

anchor = "  systemd.tmpfiles.rules = [\n"
if anchor not in src:
    raise SystemExit("ERROR: could not find 'systemd.tmpfiles.rules = [' in configuration.nix")

# Re-applying onto a config that already carries the block must not duplicate the
# comment header; a first application must not omit it.
block = "".join(r + "\n" for r in missing)
if HEADER.splitlines()[0] not in src:
    block = HEADER + block

open(path, "w").write(src.replace(anchor, anchor + block, 1))
print("      inserted %d of %d rules (%d already present)"
      % (len(missing), len(RULES), len(RULES) - len(missing)))
for r in missing:
    print("        + " + r.strip())
PY

# Verify by RE-READING the file, every rule individually — the insert reporting
# success is a claim about the writer, not about what is now on disk.
_verified=0
for _rule in "${TMPFILES_RULES[@]}"; do
  grep -qF "$_rule" "$CFG" || { echo "ERROR: rule missing after edit: $_rule" >&2; false; }
  _verified=$((_verified + 1))
done
# A loop over an empty array exits 0 having checked nothing — the "verified" that
# means least. Assert it actually iterated, and that it saw the whole ledger.
if [[ "$_verified" -ne "${#TMPFILES_RULES[@]}" || "$_verified" -eq 0 ]]; then
  echo "ERROR: verified $_verified of ${#TMPFILES_RULES[@]} rules — not a pass" >&2
  false
fi
echo "      verified $_verified of ${#TMPFILES_RULES[@]} rules individually"
grep -qF "$MARKER" "$CFG" || { echo "ERROR: comment header missing after edit" >&2; false; }
echo "      all rules verified present on disk"

trap - ERR

echo
echo "Config edited. Validate the rule syntax BEFORE rebuilding:"
echo "    systemd-tmpfiles --clean --dry-run 2>&1 | head"
echo "  (a bad age-by prefix reports 'Invalid age-by' and names the file:line)"
echo

if [[ -t 0 ]]; then
  read -r -p "Run 'nixos-rebuild switch' now? [y/N] " ans || ans=n
else
  echo "Non-interactive shell — skipping the rebuild prompt."
  ans=n
fi

if [[ "$ans" == "y" || "$ans" == "Y" ]]; then
  if ! nixos-rebuild switch; then
    echo >&2
    echo "nixos-rebuild FAILED. The config edits are still in place." >&2
    echo "Rollback: cp -a ${BAK} ${CFG} && nixos-rebuild switch" >&2
    exit 1
  fi
  echo
  echo "Switched. Cleanup runs on the next systemd-tmpfiles-clean.timer firing;"
  echo "to trigger it now:  systemctl start systemd-tmpfiles-clean.service"
else
  echo "Config edited and validated; nixos-rebuild NOT run. Run it when ready:"
  echo "    sudo nixos-rebuild switch"
fi

echo
echo "Rollback: cp -a ${BAK} ${CFG} && nixos-rebuild switch"
