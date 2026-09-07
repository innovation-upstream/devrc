#!/usr/bin/env bash
# /tmp churn retention — bring a host that has NEVER run apply-tmp-churn-retention.sh
# up to the same state as the workbench, or say precisely why it cannot.
#
#   sudo bash preflight-tmp-churn-host.sh            # READ-ONLY report, changes nothing
#   sudo bash preflight-tmp-churn-host.sh --apply    # edit /etc/nixos, switch, then VERIFY
#
# Why this wrapper exists: apply-tmp-churn-stale-lines-2026-09-04.sh exits 0 with
# "nothing to do" on an UNAPPLIED host — a reassuring green that means "I could not
# tell", not "already patched". It prints the discriminating grep itself; this runs
# that grep, plus the three other reads that decide whether the retention script can
# work here at all, and refuses to apply when any of them says no.
#
# 🔴 apply-tmp-churn-retention.sh has NO --dry-run. Run with no argument it EDITS
#    /etc/nixos immediately. `--emit-rules` is the read-only surface, and this script
#    uses it as a positive control for the expected rule count.
set -euo pipefail

CFG="${TMP_CHURN_CFG:-/etc/nixos/configuration.nix}"
ANCHOR_RE='^  systemd\.tmpfiles\.rules = \['

APPLY=no
INIT=no
case "${1:-}" in
  "")        ;;
  --apply)   APPLY=yes ;;
  --init)    APPLY=yes; INIT=yes ;;
  -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
  *) echo "ERROR: unrecognised argument '${1}'." >&2
     echo "This script EDITS /etc/nixos with --apply/--init, so it refuses anything else." >&2
     exit 64 ;;
esac

# Resolve the repo through the INVOKING user, not root: sudo resets HOME to /root,
# so $HOME/workspace/devrc would silently point at a path that does not exist.
owner="${SUDO_USER:-$(id -un)}"
owner_home="$(getent passwd "$owner" | cut -d: -f6)"
RETENTION="${TMP_CHURN_RETENTION:-$owner_home/workspace/devrc/nix/system/apply-tmp-churn-retention.sh}"

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: needs root — $CFG is mode 0600 on some hosts, so even the reads need it." >&2
  echo "       sudo bash $0 ${1:-}" >&2
  exit 1
fi
[[ -r "$CFG" ]] || { echo "ERROR: cannot read $CFG" >&2; exit 1; }

# `grep -c` prints 0 AND exits 1 on no match. `|| true` keeps the 0 and drops the
# status; `|| echo 0` would emit a SECOND line and break every integer test below.
cfg_rules=$(grep -c 'mM:7d' "$CFG" || true)
anchor=$(grep -cE "$ANCHOR_RE" "$CFG" || true)
live_rules=$(systemd-tmpfiles --cat-config 2>/dev/null | grep -c 'mM:7d' || true)
stale_rules=$(systemd-tmpfiles --cat-config 2>/dev/null | grep -c ' m:7d' || true)

expected=0
if [[ -r "$RETENTION" ]]; then
  expected=$(bash "$RETENTION" --emit-rules 2>/dev/null | grep -c 'mM:7d' || true)
fi

tmp_src=$(findmnt -no SOURCE --target /tmp 2>/dev/null || echo '?')
root_src=$(findmnt -no SOURCE --target /  2>/dev/null || echo '?')
tmp_entries=$(ls -U /tmp 2>/dev/null | wc -l)

echo "host:            $(hostname)"
echo "config:          $CFG (mtime $(stat -c '%y' "$CFG" | cut -d. -f1))"
echo "retention script: $RETENTION $([[ -r $RETENTION ]] && echo '(present)' || echo '🔴 MISSING')"
echo "rules in config: $cfg_rules"
echo "rules LIVE:      $live_rules   (stale ' m:7d' lines live: $stale_rules)"
echo "expected rules:  $expected   (from --emit-rules; 0 here means the control did not run)"
echo "anchor lines:    $anchor   ('  systemd.tmpfiles.rules = [')"
echo "/tmp backing:    $tmp_src   (root is $root_src) — $tmp_entries top-level entries"
if [[ "$tmp_src" == "$root_src" ]]; then
  echo "                 ⇒ /tmp is on the ROOT filesystem, so nothing clears it at boot."
else
  echo "                 ⇒ /tmp is NOT on the root filesystem; this host may not have the problem."
fi
df -h /  | awk 'NR==2{printf "disk:            %s used of %s (%s), %s avail\n",$3,$2,$5,$4}'
df -i /  | awk 'NR==2{printf "inodes:          %s used of %s (%s)\n",$3,$2,$5}'
echo

# ── classify ────────────────────────────────────────────────────────────────
if   [[ "$cfg_rules" -gt 0 && "$live_rules" -gt 0 ]]; then
  state=applied-and-live
elif [[ "$cfg_rules" -gt 0 && "$live_rules" -eq 0 ]]; then
  state=applied-not-rebuilt
elif [[ "$anchor" -eq 0 ]]; then
  state=unapplied-no-anchor
elif [[ ! -r "$RETENTION" ]]; then
  state=unapplied-no-script
else
  state=unapplied-can-apply
fi

case "$state" in
  applied-and-live)
    echo "VERDICT: ALREADY APPLIED AND LIVE — $live_rules rule(s) active. Nothing to do."
    exit 0 ;;
  applied-not-rebuilt)
    echo "VERDICT: config carries $cfg_rules rule(s) but NONE are live — needs a rebuild only."
    echo "         sudo nixos-rebuild switch"
    [[ "$APPLY" == yes ]] || exit 0 ;;
  unapplied-no-anchor)
    echo "VERDICT: no '  systemd.tmpfiles.rules = [' line in $CFG — the attribute does"
    echo "         not exist here, so apply-tmp-churn-retention.sh (which APPENDS INTO an"
    echo "         existing list) cannot run. --init CREATES the attribute instead."
    if [[ "$INIT" != yes ]]; then
      echo "         Re-run with --init to create it, switch, and verify."
      echo "         Preview the rules first (no root, changes nothing):"
      echo "           bash $RETENTION --emit-rules"
      exit 3
    fi ;;
  unapplied-no-script)
    echo "VERDICT: 🔴 CANNOT APPLY — retention script not readable at $RETENTION."
    echo "         Pass TMP_CHURN_RETENTION=/path/to/apply-tmp-churn-retention.sh"
    exit 3 ;;
  unapplied-can-apply)
    echo "VERDICT: UNAPPLIED, and the anchor is present — the retention script can run here."
    if [[ "$APPLY" != yes ]]; then
      echo "         Re-run with --apply to edit $CFG, switch, and verify."
      echo "         Preview the rules first (no root, changes nothing):"
      echo "           bash $RETENTION --emit-rules"
      exit 0
    fi ;;
esac

# ── apply ───────────────────────────────────────────────────────────────────
echo "=== applying (this EDITS $CFG) ==="
if [[ "$state" == unapplied-can-apply ]]; then
  bash "$RETENTION"
fi

if [[ "$state" == unapplied-no-anchor && "$INIT" == yes ]]; then
  # 🔴 PURE APPEND OF A NEW TOP-LEVEL ATTRIBUTE, BEFORE THE FINAL `}`. It never
  # rewrites or deletes an existing line. That restraint is the whole design: the
  # eviction feature this effort DELETED removed lines by regex and cost two
  # deploy-blocking regressions in three audit rounds — an unanchored splice that
  # commented out an unrelated live rule, and a bracket scan that walked into a
  # FOLLOWING attribute. Both printed success and passed their own verifier.
  #
  # Three preconditions, each REFUSING rather than guessing:
  #   1. `systemd.tmpfiles` appears NOWHERE — else there is an attribute to merge
  #      with and a blind append would define it twice (a Nix eval error at best,
  #      and at worst a duplicate the reader has to reconcile).
  #   2. the last non-blank line is exactly `}` — the file closes the way a plain
  #      NixOS module does. This is a CHECKABLE condition; scanning for a matching
  #      bracket is what went wrong before, so it is not attempted.
  #   3. the parse succeeds AFTER the edit — else the backup is restored.
  if grep -q 'systemd\.tmpfiles' "$CFG"; then
    echo "🔴 REFUSING: $CFG already mentions 'systemd.tmpfiles' somewhere, but not as" >&2
    echo "   '  systemd.tmpfiles.rules = ['. Appending would risk defining it twice." >&2
    grep -n 'systemd\.tmpfiles' "$CFG" >&2
    exit 3
  fi
  last_brace=$(grep -n '^}$' "$CFG" | tail -1 | cut -d: -f1 || true)
  last_nonblank=$(awk 'NF{n=NR} END{print n}' "$CFG")
  if [[ -z "$last_brace" || "$last_brace" != "$last_nonblank" ]]; then
    echo "🔴 REFUSING: the last non-blank line of $CFG is line $last_nonblank, and the" >&2
    echo "   last line that is exactly '}' is ${last_brace:-<none>}. This inserter only" >&2
    echo "   handles a file that closes with a bare '}' on its own final line." >&2
    echo "   Add the block by hand — 'bash $RETENTION --emit-rules' prints the rules." >&2
    exit 3
  fi

  backup="$CFG.bak-$(date +%Y%m%d-%H%M%S)"
  cp -a "$CFG" "$backup"
  echo "      backup: $backup"

  block=$(mktemp); trap 'rm -f "$block"' EXIT
  {
    echo ""
    echo "  # /tmp churn retention. mtime-only ageing for BOTH files and directories"
    echo "  # (\`mM:\`) — lower-case covers files only, which ages no directory at all."
    echo "  # systemd-tmpfiles otherwise ages on the newest of atime/mtime/ctime, and any"
    echo "  # \`du\`/\`find\` over /tmp refreshes atime, which is why the stock 10d rule"
    echo "  # never expires anything. Scoped to machine-generated prefixes ONLY: a blanket"
    echo "  # rule would delete live git worktrees parked in /tmp."
    echo "  # Generated by nix/system/preflight-tmp-churn-host.sh --init; the ledger lives"
    echo "  # in nix/system/apply-tmp-churn-retention.sh (--emit-rules prints it)."
    echo "  systemd.tmpfiles.rules = ["
    bash "$RETENTION" --emit-rules | grep -v '^#' | sed 's/.*/    "&"/'
    echo "  ];"
  } > "$block"
  echo "      inserting $(grep -c 'mM:7d' "$block") rule(s) before line $last_brace:"
  sed 's/^/        /' "$block"

  new=$(mktemp)
  { head -n $((last_brace - 1)) "$CFG"; cat "$block"; tail -n +"$last_brace" "$CFG"; } > "$new"
  cp -a "$new" "$CFG"; rm -f "$new"

  if ! nix-instantiate --parse "$CFG" >/dev/null 2>&1; then
    echo "🔴 the edited $CFG DOES NOT PARSE — restoring $backup and aborting." >&2
    cp -a "$backup" "$CFG"
    nix-instantiate --parse "$CFG" >/dev/null 2>&1 \
      && echo "   restored copy parses clean; nothing was changed." >&2 \
      || echo "   🔴 THE RESTORED COPY ALSO FAILS TO PARSE — inspect $backup BY HAND." >&2
    exit 1
  fi
  echo "      parse OK ($(grep -c 'mM:7d' "$CFG") rule(s) now in $CFG)"
fi

echo "=== nixos-rebuild switch ==="
echo "(no NIXOS_NO_CHECK — if a switchInhibitor blocks, that is a real signal; a reboot"
echo " clears a pending channel migration, and this host has none as of the last check.)"
nixos-rebuild switch

echo "=== VERIFY — the runtime symptom, not the rollout ==="
after_live=$(systemd-tmpfiles --cat-config 2>/dev/null | grep -c 'mM:7d' || true)
after_stale=$(systemd-tmpfiles --cat-config 2>/dev/null | grep -c ' m:7d' || true)
echo "rules LIVE now:  $after_live   (expected $expected)"
echo "stale ' m:7d':   $after_stale   (expected 0)"
if [[ "$expected" -gt 0 && "$after_live" -eq "$expected" && "$after_stale" -eq 0 ]]; then
  echo "RESULT: PASS — the rules are live. The first reap runs on the next"
  echo "        systemd-tmpfiles-clean.timer fire:"
  systemctl list-timers systemd-tmpfiles-clean.timer --no-pager 2>/dev/null | sed -n 2p
  exit 0
fi
echo "RESULT: 🔴 FAIL — the switch reported success but the rules are not live as expected."
echo "        A deploy reporting success is a claim about the DEPLOY, not the consumer."
echo "        Inspect:  systemd-tmpfiles --cat-config | grep -n '7d'"
exit 1
