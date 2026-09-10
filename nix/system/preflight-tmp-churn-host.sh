#!/usr/bin/env bash
# /tmp churn retention — bring a host that has NEVER run apply-tmp-churn-retention.sh
# up to the same state as the workbench, or say precisely why it cannot.
#
#   sudo bash preflight-tmp-churn-host.sh            # READ-ONLY report, changes nothing
#   sudo bash preflight-tmp-churn-host.sh --apply    # edit /etc/nixos, switch, then VERIFY
#   sudo bash preflight-tmp-churn-host.sh --init     # --apply, but CREATE the attribute
#                                                    # on a config that has none
#
# Why this wrapper exists: apply-tmp-churn-stale-lines-2026-09-04.sh exits 0 with
# "nothing to do" on an UNAPPLIED host — a reassuring green that means "I could not
# tell", not "already patched". It prints the discriminating grep itself; this runs
# that grep, plus the three other reads that decide whether the retention script can
# work here at all, and refuses to apply when any of them says no.
#
# 🔴 apply-tmp-churn-retention.sh has NO --dry-run. Run with no argument it EDITS
#    /etc/nixos immediately. `--emit-rules` is the read-only surface, and this script
#    uses it as a positive control for the expected rule set — but ONLY after
#    probing that the copy on this host actually HAS that surface. See
#    `retention_ok` below: a pre-2026-09-04 copy (06597151) has no argument handling
#    at all, so `bash "$RETENTION" --emit-rules` on it performs the FULL /etc/nixos
#    edit, with every diagnostic swallowed by the command substitution. Measured:
#    that revision contains zero occurrences of `--emit-rules` and zero of
#    `TMP_CHURN_CFG`, so neither the flag nor the fixture variable can contain it.
set -euo pipefail

usage() {
  cat <<'USAGE'
preflight-tmp-churn-host.sh — classify a host's /tmp-churn retention state, and
optionally bring it up to the workbench's.

  sudo bash preflight-tmp-churn-host.sh          READ-ONLY report. Changes nothing.
  sudo bash preflight-tmp-churn-host.sh --apply  Run apply-tmp-churn-retention.sh
                                                 (which EDITS the config), then
                                                 nixos-rebuild switch, then VERIFY.
  sudo bash preflight-tmp-churn-host.sh --init   As --apply, but for a config that
                                                 has NO `systemd.tmpfiles.rules`
                                                 attribute: appends a new one before
                                                 the file's final `}`. This is the
                                                 most invasive mode.
  bash preflight-tmp-churn-host.sh --help        This text.

Exactly one argument. `--init --dry-run` is REFUSED, not read as `--init`.

Environment:
  TMP_CHURN_CFG        Edit this file instead of /etc/nixos/configuration.nix and
                       make NO system change: no root needed, no nixos-rebuild.
                       Exported to the retention script so it edits the same
                       fixture. This is the test surface.
  TMP_CHURN_RETENTION  Path to apply-tmp-churn-retention.sh. Default is
                       <$SUDO_USER's home>/workspace/devrc/nix/system/apply-tmp-churn-retention.sh
  TMP_CHURN_FLAKE_ACK  Set to 1 to proceed when the config directory contains a
                       flake.nix. nixos-rebuild PREFERS a flake, so without this
                       acknowledgement the edit could be to a file the system never
                       evaluates. Set it only after checking the flake imports it.
USAGE
}

# ── arguments ───────────────────────────────────────────────────────────────
# 🔴 BRANCH ON $#, NOT ON "${1:-}". `case "${1:-}"` cannot distinguish NO argument
# from ONE EMPTY argument, and it silently ignores every argument after the first:
# measured, `--init --dry-run` took the `--init` arm and performed the full apply
# and switch. apply-tmp-churn-retention.sh rejects both shapes explicitly (see its
# "BRANCH ON $#" block); this script EDITS THE SAME FILE and must match it.
if [[ $# -gt 1 ]]; then
  echo "ERROR: exactly one argument, got $#: $*" >&2
  echo "This script EDITS the NixOS config with --apply/--init, so it refuses a" >&2
  echo "command line it cannot interpret unambiguously (e.g. '--init --dry-run'," >&2
  echo "which is NOT a dry run — there is no such mode)." >&2
  exit 64
fi
if [[ $# -eq 1 && -z "${1}" ]]; then
  echo "ERROR: empty argument. Refusing rather than treating it as no argument." >&2
  exit 64
fi

APPLY=no
INIT=no
case "${1:-}" in
  "")        ;;
  --apply)   APPLY=yes ;;
  --init)    APPLY=yes; INIT=yes ;;
  -h|--help) usage; exit 0 ;;
  *) echo "ERROR: unrecognised argument '${1}'." >&2
     echo "This script EDITS the NixOS config with --apply/--init, so it refuses anything else." >&2
     echo "Run with --help for usage." >&2
     exit 64 ;;
esac

CFG="${TMP_CHURN_CFG:-/etc/nixos/configuration.nix}"
CFG_DIR="$(dirname "$CFG")"
ANCHOR_RE='^  systemd\.tmpfiles\.rules = \['
TEST_MODE=no
if [[ -n "${TMP_CHURN_CFG:-}" ]]; then
  TEST_MODE=yes
  # The retention script has the same variable and the same contract. Export it so
  # the child edits THIS fixture — unexported, the child would fall back to
  # /etc/nixos/configuration.nix and a "test" run would edit the real system.
  export TMP_CHURN_CFG
fi

# ── who owns the repo ───────────────────────────────────────────────────────
# Resolve the repo through the INVOKING user, not root: sudo resets HOME to /root,
# so $HOME/workspace/devrc would silently point at a path that does not exist.
#
# 🔴 `getent passwd <unknown>` EXITS 2. Under `set -e` + `pipefail` the original
# spelling — `owner_home="$(getent passwd "$owner" | cut -d: -f6)"` — killed the
# whole run at that line: exit 2, zero bytes of output, no diagnostic. Measured.
owner="${SUDO_USER:-$(id -un)}"
owner_home=""
if _pw="$(getent passwd "$owner" 2>/dev/null)"; then
  owner_home="$(printf '%s' "$_pw" | cut -d: -f6)"
fi
if [[ -n "${TMP_CHURN_RETENTION:-}" ]]; then
  RETENTION="$TMP_CHURN_RETENTION"
elif [[ -z "$owner_home" || "$owner_home" != /* ]]; then
  # An empty or relative home field would yield `RETENTION=/workspace/devrc/...`,
  # an absolute path this script would then hand to `bash` as root.
  echo "ERROR: could not resolve a home directory for '$owner'" >&2
  echo "       (getent passwd '$owner' -> field 6 = '${owner_home}')." >&2
  echo "       Pass TMP_CHURN_RETENTION=/path/to/apply-tmp-churn-retention.sh" >&2
  exit 3
else
  RETENTION="$owner_home/workspace/devrc/nix/system/apply-tmp-churn-retention.sh"
fi

if [[ "$TEST_MODE" == yes ]]; then
  echo "🔴 TEST MODE — TMP_CHURN_CFG is set, so this run edits the fixture ${CFG},"
  echo "   NOT /etc/nixos, and runs NO nixos-rebuild. No system change, no root needed."
elif [[ $EUID -ne 0 ]]; then
  echo "ERROR: needs root — $CFG is mode 0600 on some hosts, so even the reads need it." >&2
  echo "       sudo bash $0 ${1:-}" >&2
  exit 1
fi
[[ -r "$CFG" ]] || { echo "ERROR: cannot read $CFG" >&2; exit 1; }

# ── is the retention script SAFE TO EXECUTE? ────────────────────────────────
# 🔴 THIS IS A ROOT EXECUTION OF A PATH DERIVED FROM A $SUDO_USER LOOKUP, and
# `bash "$RETENTION" --emit-rules` on a copy that predates d8fe0bce (2026-09-04)
# does not print rules — it EDITS the config, inserting the superseded files-only
# `m:7d` ruleset and the withdrawn dead `homelab-talos-prs-*` rule, with all of its
# output consumed by the `$( )`. It ran here on the NO-ARGUMENT invocation that this
# file's header documents as "READ-ONLY report, changes nothing".
#
# The probe is on the case ARM, not on a mention of the string anywhere in the file:
# a comment naming `--emit-rules` would satisfy a bare `grep -q -- '--emit-rules'`
# while the code still fell through to the edit. The emitted output is then
# validated too — structure alone cannot prove the arm does what it is named after.
retention_ok=no
retention_why=""
if [[ ! -r "$RETENTION" ]]; then
  retention_why="🔴 MISSING (not readable)"
elif ! command grep -qE '^[[:space:]]*--emit-rules\)' "$RETENTION"; then
  retention_why="🔴 PRESENT BUT TOO OLD — no '--emit-rules)' case arm. Running it would EDIT the config."
else
  retention_ok=yes
  retention_why="(present, has --emit-rules)"
fi

# The ledger, read ONCE, from the one source that has it. Every later use — the
# expected count, the --init block, the verification — reads this array, so there
# is no second execution of $RETENTION and no second copy of the rules.
LEDGER_RULES=()
# One EXIT trap for every temp file this script creates. `$block`/`$new`/`$block.err`
# are appended to `_tmpfiles` as they are made; `$new` used to have no trap at all
# and leaked a /tmp/tmp.* on every run — the very prefix this tool exists to reap.
_tmpfiles=()
_cleanup() { [[ ${#_tmpfiles[@]} -gt 0 ]] && rm -f "${_tmpfiles[@]}"; return 0; }
trap _cleanup EXIT
if [[ "$retention_ok" == yes ]]; then
  _emit_err="$(mktemp)"; _tmpfiles+=("$_emit_err")
  if _emit="$(bash "$RETENTION" --emit-rules 2>"$_emit_err")"; then
    while IFS= read -r _line; do
      [[ -z "$_line" || "$_line" == \#* ]] && continue
      LEDGER_RULES+=("$_line")
    done <<< "$_emit"
  else
    retention_ok=no
    retention_why="🔴 --emit-rules FAILED: $(head -3 "$_emit_err" | tr '\n' ' ')"
  fi
  # Behavioural check on top of the structural one: --emit-rules must print
  # tmpfiles rules, not a log, a prompt, or nothing.
  if [[ "$retention_ok" == yes ]]; then
    if [[ ${#LEDGER_RULES[@]} -eq 0 ]]; then
      retention_ok=no
      retention_why="🔴 --emit-rules printed no rules"
    else
      for _r in "${LEDGER_RULES[@]}"; do
        if [[ "$_r" != "e /tmp/"* ]]; then
          retention_ok=no
          retention_why="🔴 --emit-rules printed a line that is not a /tmp rule: '$_r'"
          break
        fi
      done
    fi
  fi
fi
expected=${#LEDGER_RULES[@]}

# ── reads ───────────────────────────────────────────────────────────────────
# `grep -c` prints 0 AND exits 1 on no match. `|| true` keeps the 0 and drops the
# status; `|| echo 0` would emit a SECOND line and break every integer test below.
cfg_rules=$(grep -c 'mM:7d' "$CFG" || true)
anchor=$(grep -cE "$ANCHOR_RE" "$CFG" || true)
live_before="$(systemd-tmpfiles --cat-config 2>/dev/null || true)"
live_rules=$(printf '%s\n' "$live_before" | grep -c 'mM:7d' || true)
stale_rules=$(printf '%s\n' "$live_before" | grep -c ' m:7d' || true)

tmp_src=$(findmnt -no SOURCE --target /tmp 2>/dev/null || echo '?')
root_src=$(findmnt -no SOURCE --target /  2>/dev/null || echo '?')
tmp_entries=$(ls -U /tmp 2>/dev/null | wc -l)

cfg_label="$CFG"
if [[ -L "$CFG" ]]; then
  cfg_label="$CFG -> $(readlink -f "$CFG") 🔴 SYMLINK"
fi
echo "host:            $(hostname 2>/dev/null || echo '?')"
echo "config:          $cfg_label (mtime $(stat -c '%y' "$CFG" | cut -d. -f1))"
if [[ -L "$CFG" ]]; then
  echo "                 ⇒ the edit writes THROUGH the link, into the target — which for"
  echo "                   a git-managed NixOS config means the repo working tree goes"
  echo "                   dirty. Every backup THIS script takes uses 'cp -aL', so it is"
  echo "                   a real copy and not a second link to the file about to be"
  echo "                   rewritten."
  echo "                 🔴 apply-tmp-churn-retention.sh does NOT: its 'cp -a \"\$CFG\""
  echo "                   \"\$BAK\"' preserves the link, so the rollback its footer"
  echo "                   documents is a no-op on a host like this. --apply therefore"
  echo "                   takes its own dereferenced backup before delegating."
fi
echo "retention script: $RETENTION $retention_why"
echo "rules in config: $cfg_rules"
echo "rules LIVE:      $live_rules   (stale ' m:7d' lines live: $stale_rules)"
echo "expected rules:  $expected   (from --emit-rules; 0 here means the control did not run)"
echo "anchor lines:    $anchor   ('  systemd.tmpfiles.rules = [')"
if [[ -e "$CFG_DIR/flake.nix" ]]; then
  echo "flake:           🔴 $CFG_DIR/flake.nix EXISTS — nixos-rebuild PREFERS a flake, so"
  echo "                 $CFG may be a leftover the system never evaluates."
else
  echo "flake:           none in $CFG_DIR"
fi
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
elif [[ "$retention_ok" != yes ]]; then
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
    fi
    if [[ "$retention_ok" != yes ]]; then
      echo "VERDICT: 🔴 CANNOT --init — the rule ledger is unavailable: $retention_why" >&2
      exit 3
    fi ;;
  unapplied-no-script)
    echo "VERDICT: 🔴 CANNOT APPLY — $retention_why"
    echo "         Path checked: $RETENTION"
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

# ── what must be live at the end, decided BEFORE anything is touched ────────
# 🔴 The old verification compared a COUNT of 'mM:7d' across the WHOLE live
# tmpfiles config against the repo ledger's count. That passes over a completely
# inert edit whenever the host already has that many mM:7d rules from anywhere
# else — an imported module, an /etc/tmpfiles.d drop-in, or a flake that never
# imports $CFG. Measured: seven unrelated live rules + a no-op nixos-rebuild
# printed `RESULT: PASS`, exit 0.
#
# Two independent claims replace it, both required:
#   IDENTITY — every rule we intend to apply is live, matched as a STRING.
#   DELTA    — the live count moved by exactly the number of those rules that were
#              NOT already live. `already` is measured here, before the edit, so a
#              rule that legitimately pre-exists does not read as a failure.
#
# 🟡 `want` is state-dependent, and that is finding 8: `applied-not-rebuilt` is
# decided from $CFG alone, so a host in that state with an unreadable retention
# script used to get `expected=0` and a 🔴 FAIL over a fully successful rebuild.
# In that state the honest claim is about the rules the CONFIG carries, and the
# ledger is not needed at all.
want=()
if [[ "$state" == applied-not-rebuilt ]]; then
  while IFS= read -r _line; do
    [[ -n "$_line" ]] && want+=("${_line//\"/}")
  done < <(grep -o '"[^"]*mM:7d[^"]*"' "$CFG" || true)
  want_src="the $cfg_rules rule(s) already in $CFG"
else
  want=("${LEDGER_RULES[@]}")
  want_src="the $expected rule(s) from $RETENTION --emit-rules"
fi
if [[ ${#want[@]} -eq 0 ]]; then
  echo "🔴 REFUSING: nothing to verify against — could not determine which rules should" >&2
  echo "   end up live. A pass with an empty expectation is the pass that means least." >&2
  exit 3
fi
already=0
for _r in "${want[@]}"; do
  # `if`, not `grep … && already=…`: an && list whose left side fails leaves the
  # loop body's status non-zero, and reasoning about whether `set -e` exempts that
  # is not worth the ambiguity in a script that edits /etc/nixos as root.
  if printf '%s\n' "$live_before" | grep -qF -- "$_r"; then
    already=$((already + 1))
  fi
done
echo "will verify:     ${#want[@]} rule(s) — $want_src"
echo "                 ($already of them are ALREADY live, so the live count must rise by $(( ${#want[@]} - already )))"
echo

# ── apply ───────────────────────────────────────────────────────────────────
if [[ "$state" == unapplied-can-apply ]]; then
  echo "=== applying (this EDITS $CFG) ==="
  # 🔴 The delegate takes its own backup with a plain `cp -a`, which on a SYMLINKED
  # config produces a second link to the file it is about to rewrite — so its
  # documented "Rollback: cp -a ${BAK} ${CFG}" is a no-op there and the original is
  # gone. Not fixable from this file, so take a dereferenced one first. Only on the
  # symlink case: on a regular file the delegate's own backup is already correct and
  # a second copy is noise.
  if [[ -L "$CFG" ]]; then
    delegate_backup="$CFG.bak-preflight-$(date +%Y%m%d-%H%M%S)"
    cp -aL "$CFG" "$delegate_backup"
    echo "      pre-delegate backup: $delegate_backup (cp -aL; the delegate's own"
    echo "      'cp -a' backup would be a second symlink to the file it rewrites)"
  fi
  bash "$RETENTION"
fi

if [[ "$state" == unapplied-no-anchor && "$INIT" == yes ]]; then
  echo "=== applying --init (this EDITS $CFG) ==="
  # 🔴 PURE APPEND OF A NEW TOP-LEVEL ATTRIBUTE, BEFORE THE FINAL `}`. It never
  # rewrites or deletes an existing line. That restraint is the whole design: the
  # eviction feature this effort DELETED removed lines by regex and cost two
  # deploy-blocking regressions in three audit rounds — an unanchored splice that
  # commented out an unrelated live rule, and a bracket scan that walked into a
  # FOLLOWING attribute. Both printed success and passed their own verifier.
  #
  # Four preconditions, each REFUSING rather than guessing:
  #   1. `systemd.tmpfiles` appears NOWHERE in ANY .nix under the config directory
  #      — else there is an attribute to merge with and a blind append would define
  #      it twice. 🔴 THE FILE-SCOPED VERSION OF THIS CHECK WAS TOO NARROW.
  #      `systemd.tmpfiles.rules` is a `listOf str` MERGED ACROSS MODULES: two
  #      modules each defining it is not an error, it is a concatenation, so a host
  #      whose rules live in an imported module passed a one-file grep and got a
  #      silently duplicated rule set. (The SAME-FILE duplicate is caught by
  #      precondition 4 — measured: `nix-instantiate --parse` on a file defining
  #      `systemd.tmpfiles.rules` twice exits 1 with "attribute … already
  #      defined". It is the cross-file case that escaped.) A .nix file that is not
  #      actually imported produces a FALSE refusal here; that is the safe
  #      direction, and the message says how to proceed by hand.
  #   2. nixos-rebuild must actually evaluate $CFG — see the flake check below.
  #   3. the last non-blank line is exactly `}` — the file closes the way a plain
  #      NixOS module does. This is a CHECKABLE condition; scanning for a matching
  #      bracket is what went wrong before, so it is not attempted.
  #   4. the parse succeeds AFTER the edit — else the backup is restored.
  mentions=()
  while IFS= read -r _f; do
    [[ -n "$_f" ]] && mentions+=("$_f")
  done < <(find "$CFG_DIR" -name '*.nix' -type f -print0 2>/dev/null \
             | xargs -0 -r grep -l 'systemd\.tmpfiles' 2>/dev/null || true)
  if [[ ${#mentions[@]} -gt 0 ]]; then
    echo "🔴 REFUSING: 'systemd.tmpfiles' already appears in ${#mentions[@]} .nix file(s) under" >&2
    echo "   $CFG_DIR, but not as '  systemd.tmpfiles.rules = [' in $CFG." >&2
    echo "   systemd.tmpfiles.rules is a listOf MERGED across modules, so appending here" >&2
    echo "   would silently define a second, duplicate set rather than erroring." >&2
    printf '     %s\n' "${mentions[@]}" >&2
    echo "   If none of those files is imported by the evaluated config, add the block" >&2
    echo "   by hand — 'bash $RETENTION --emit-rules' prints the rules." >&2
    exit 3
  fi

  # 🔴 nixos-rebuild AUTO-PREFERS /etc/nixos/flake.nix. Read out of the shipped
  # implementation, not from memory — nixos-rebuild-ng 26.11,
  # `lib/.../nixos_rebuild/models.py:121` ("Use /etc/nixos/flake.nix if it exists")
  # and `__init__.py:118`, whose `--no-flake` flag is documented as "Do not imply
  # --flake if /etc/nixos/flake.nix exists". Nothing above establishes
  # that $CFG is the file the host evaluates, so on a flake host this whole edit can
  # land in a file nothing imports — the exact shape that made the old count-based
  # verification pass over an inert change. The deterministic check would be to
  # compare `nixos-rebuild --dry-build` before and after; that costs a full
  # evaluation and is not attempted here. This refuses instead, and takes an
  # explicit human acknowledgement to proceed.
  if [[ -e "$CFG_DIR/flake.nix" && "${TMP_CHURN_FLAKE_ACK:-}" != 1 ]]; then
    echo "🔴 REFUSING: $CFG_DIR/flake.nix exists. nixos-rebuild prefers a flake, so" >&2
    echo "   editing $CFG may change a file the system never evaluates — and the" >&2
    echo "   verification below would then be checking rules that came from somewhere" >&2
    echo "   else entirely." >&2
    echo "   Confirm the flake's nixosConfigurations import $CFG, then re-run with" >&2
    echo "   TMP_CHURN_FLAKE_ACK=1." >&2
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
  # 🔴 `cp -a` IMPLIES `-d`. On a symlinked config — the standard layout for anyone
  # keeping /etc/nixos in a git repo, which is what this repository is — a plain
  # `cp -a` backup is a SECOND SYMLINK TO THE FILE ABOUT TO BE EDITED. Measured:
  # after the edit the "backup" read back the edited content, so the restore path
  # was a no-op and the original was gone. `-L` dereferences.
  cp -aL "$CFG" "$backup"
  echo "      backup: $backup (cp -aL — a real copy even if $CFG is a symlink)"

  block=$(mktemp); _tmpfiles+=("$block" "$block.err")
  new=$(mktemp);   _tmpfiles+=("$new")
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
    printf '    "%s"\n' "${LEDGER_RULES[@]}"
    echo "  ];"
  } > "$block"
  echo "      inserting ${#LEDGER_RULES[@]} rule(s) before line $last_brace:"
  sed 's/^/        /' "$block"

  { head -n $((last_brace - 1)) "$CFG"; cat "$block"; tail -n +"$last_brace" "$CFG"; } > "$new"
  # 🔴 NOT `cp -a "$new" "$CFG"`. That copies the MKTEMP'S 0600 onto the
  # destination, so a 0644 config silently became root-only after a SUCCESSFUL run
  # (measured). A redirect writes the bytes and leaves the destination's mode,
  # owner and inode alone — and follows a symlink to the real file, which is what
  # we want here.
  cat "$new" > "$CFG"

  if ! nix-instantiate --parse "$CFG" >/dev/null 2>"$block.err"; then
    echo "🔴 the edited $CFG DOES NOT PARSE — restoring $backup and aborting." >&2
    # 🔴 The stderr used to be discarded, which made a MISSING nix-instantiate
    # (exit 127) indistinguishable from a syntax error. Fails safe either way, but
    # it misdiagnoses, so print it.
    sed 's/^/   | /' "$block.err" >&2 || true
    # 🔴 The restore must not run under bare `set -e`: a failing restore would exit
    # immediately and neither the re-parse nor the "inspect BY HAND" message — the
    # two things this block claims to do — would ever run. Measured as written.
    #
    # 🔴 AND IT MUST NOT BE `cat "$backup" > "$CFG"` EITHER. A redirect TRUNCATES
    # the destination before the source is read, so an unreadable backup would take
    # $CFG to ZERO BYTES — strictly worse than the edited-but-parseable file it was
    # trying to undo. Stage it, then write only what was successfully read.
    restore_stage=$(mktemp); _tmpfiles+=("$restore_stage")
    if ! cat "$backup" > "$restore_stage" 2>/dev/null; then
      echo "   🔴 THE RESTORE ITSELF FAILED — $backup could not be read." >&2
      echo "   $CFG is left EDITED (and unparseable); it was NOT truncated." >&2
      echo "   Recover from $backup — put it back BY HAND." >&2
      exit 1
    fi
    if ! cat "$restore_stage" > "$CFG" 2>/dev/null; then
      echo "   🔴 THE RESTORE ITSELF FAILED — $CFG could not be written." >&2
      echo "   The original content is in $backup — put it back BY HAND." >&2
      exit 1
    fi
    if nix-instantiate --parse "$CFG" >/dev/null 2>&1; then
      echo "   restored copy parses clean; nothing was changed." >&2
    else
      echo "   🔴 THE RESTORED COPY ALSO FAILS TO PARSE — inspect $backup BY HAND." >&2
    fi
    exit 1
  fi
  echo "      parse OK ($(grep -c 'mM:7d' "$CFG") rule(s) now in $CFG)"
fi

# ── the config-level claim, independent of the live system ──────────────────
cfg_missing=()
for _r in "${want[@]}"; do
  grep -qF -- "$_r" "$CFG" || cfg_missing+=("$_r")
done
if [[ ${#cfg_missing[@]} -gt 0 ]]; then
  echo "🔴 FAIL — ${#cfg_missing[@]} rule(s) are NOT in $CFG after the edit:" >&2
  printf '     %s\n' "${cfg_missing[@]}" >&2
  exit 1
fi
echo "config carries all ${#want[@]} rule(s)."

if [[ "$TEST_MODE" == yes ]]; then
  echo "=== TEST MODE — NOT running nixos-rebuild ==="
  echo "(TMP_CHURN_CFG points at a fixture. A rebuild here would build the REAL system"
  echo " from an /etc/nixos this run never touched, and the operator would watch it"
  echo " scroll past and reasonably conclude the fixture's rules had been applied.)"
else
  echo "=== nixos-rebuild switch ==="
  echo "(no NIXOS_NO_CHECK — if a switchInhibitor blocks, that is a real signal; a reboot"
  echo " clears a pending channel migration, and this host has none as of the last check.)"
  nixos-rebuild switch
fi

echo "=== VERIFY — the runtime symptom, not the rollout ==="
live_after="$(systemd-tmpfiles --cat-config 2>/dev/null || true)"
after_live=$(printf '%s\n' "$live_after" | grep -c 'mM:7d' || true)
after_stale=$(printf '%s\n' "$live_after" | grep -c ' m:7d' || true)
missing=()
for _r in "${want[@]}"; do
  printf '%s\n' "$live_after" | grep -qF -- "$_r" || missing+=("$_r")
done
delta=$(( after_live - live_rules ))
want_delta=$(( ${#want[@]} - already ))
echo "rules LIVE now:  $after_live   (was $live_rules; delta $delta, expected $want_delta)"
echo "of the ${#want[@]} rule(s) we required, ${#missing[@]} are NOT live"
echo "stale ' m:7d':   $after_stale   (was $stale_rules — this script never removes them)"
if [[ ${#missing[@]} -eq 0 && "$delta" -eq "$want_delta" && "$after_stale" -eq "$stale_rules" ]]; then
  echo "RESULT: PASS — all ${#want[@]} required rule(s) matched by STRING in the live"
  echo "        tmpfiles config, and the live count moved by exactly $want_delta."
  echo "        The first reap runs on the next systemd-tmpfiles-clean.timer fire:"
  # `sed -n 2p` on a failing systemctl used to print nothing and, being the last
  # command before `exit 0`, take the whole script's status with it: RESULT: PASS
  # followed by exit 1.
  systemctl list-timers systemd-tmpfiles-clean.timer --no-pager 2>/dev/null | sed -n 2p || true
  exit 0
fi
echo "RESULT: 🔴 FAIL — the rules are not live as expected."
if [[ ${#missing[@]} -gt 0 ]]; then
  echo "        NOT LIVE (matched as strings, so a count of unrelated rules cannot mask this):"
  printf '          %s\n' "${missing[@]}"
fi
if [[ "$delta" -ne "$want_delta" ]]; then
  echo "        The live mM:7d count moved by $delta, not $want_delta. A count that"
  echo "        happens to equal the ledger's is NOT evidence this edit did anything;"
  echo "        the delta is."
fi
if [[ "$after_stale" -ne "$stale_rules" ]]; then
  echo "        Stale ' m:7d' lines went $stale_rules -> $after_stale. This script only"
  echo "        appends mM rules; it cannot create or remove those."
fi
if [[ "$TEST_MODE" == yes ]]; then
  echo "        (TEST MODE ran NO nixos-rebuild, so nothing this run wrote can be live."
  echo "         A FAIL here is expected unless the live system already carried them.)"
fi
echo "        A deploy reporting success is a claim about the DEPLOY, not the consumer."
echo "        Inspect:  systemd-tmpfiles --cat-config | grep -n '7d'"
exit 1
