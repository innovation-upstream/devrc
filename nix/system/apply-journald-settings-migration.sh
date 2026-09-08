#!/usr/bin/env bash
# =============================================================================
# Migrate services.journald.extraConfig -> services.journald.settings.Journal
# =============================================================================
# After a `nix-channel --update` onto nixos-26.11, `nixos-rebuild switch` fails:
#
#   Failed assertions:
#   - The option definition `services.journald.extraConfig' in
#     `/etc/nixos/configuration.nix' no longer has any effect; please remove it.
#     Use services.journald.settings.Journal instead.
#
# The option was removed via mkRemovedOptionModule in
# nixos/modules/system/boot/systemd/journald.nix. `settings.Journal` is a
# freeform attrset of journald.conf(5) keys, so the ini text becomes attrs.
#
#     services.journald.extraConfig = ''         services.journald.settings.Journal = {
#       SystemMaxUse=2G                    ->      SystemMaxUse = "2G";
#     '';                                        };
#
# The parsing lives in scripts/lib/journald_migrate.py, which is unit-tested
# (scripts/tests/test_journald_migrate.py) and REFUSES anything it does not fully
# understand. It handles both the ''-block and the one-line "..." form; the
# workbench carried the first and the laptop the second.
#
# SAFETY. Nothing is written until the rewrite has been produced AND parsed as
# valid Nix. Then, in order: a uniquely-named backup is taken (an existing one is
# never overwritten), the new file is moved into place, and from that moment a
# trap restores the backup on ANY failure or interrupt. `nixos-rebuild test`
# runs before `switch` so an ACTIVATION failure does not leave a registered
# generation and a rewritten bootloader behind.
#
# Idempotent: a config that is already migrated exits 0 without touching anything.
#
# Run as root:
#
#     sudo bash nix/system/apply-journald-settings-migration.sh
#
# Overrides (optional):
#   JOURNALD_CFG=/etc/nixos/configuration.nix
# =============================================================================
set -euo pipefail

CFG="${JOURNALD_CFG:-/etc/nixos/configuration.nix}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIGRATE="${HERE}/../../scripts/lib/journald_migrate.py"

die() { echo "ABORT: $*" >&2; exit 1; }

echo "=== journald extraConfig -> settings.Journal ==="

# ---------------------------------------------------------------- preflight (no writes)
[ "$(id -u)" = "0" ] || die "must run as root: sudo bash ${BASH_SOURCE[0]}"

for t in python3 nix-instantiate nixos-rebuild; do
  command -v "$t" >/dev/null 2>&1 || die "\`$t\` is not on PATH.
  sudo inherits the CALLER's PATH here (there is no secure_path), and python3 in
  particular is NOT in /run/current-system/sw/bin. Run this from a shell that has it:
    sudo env \"PATH=\$PATH\" bash ${BASH_SOURCE[0]}"
done

[ -r "$MIGRATE" ] || die "cannot read the rewriter at $MIGRATE (run this from the repo checkout)"
[ -f "$CFG" ] && [ -w "$CFG" ] || die "$CFG is not a writable regular file"

# Idempotency. Ask the file, before doing any work.
if ! grep -q 'services\.journald\.extraConfig' "$CFG"; then
  if grep -q 'services\.journald\.settings\.Journal' "$CFG"; then
    echo "  state     : ALREADY MIGRATED — nothing to do."
    exit 0
  fi
  die "no services.journald.extraConfig in $CFG, and no settings.Journal either.
  Nothing matches what this script was written against — inspect by hand."
fi
echo "  state     : services.journald.extraConfig present — proceeding"

# ------------------------------------------------------------------------ patch (temp)
TMP="${CFG}.new.$$"
KEYS="$(mktemp -t journald-keys-XXXXXXXX)"
BAK="${CFG}.bak-journald-$(date +%Y%m%d-%H%M%S)-$$"
PATCHED=0
SWITCHED=0
OK=0

finish() {
  local rc=$?
  rm -f "$TMP" "$KEYS"
  if [ "$OK" = "1" ]; then return; fi
  if [ "$PATCHED" = "1" ] && [ -f "$BAK" ]; then
    cp -p "$BAK" "$CFG"
    echo >&2
    echo "ROLLED BACK: $CFG restored from $BAK" >&2
    if [ "$SWITCHED" = "1" ]; then
      echo "🔴 The system had ALREADY been switched. The FILE is restored but the RUNNING" >&2
      echo "   system is not — run \`sudo nixos-rebuild switch\` to return it." >&2
    else
      echo "   The system was never switched, so nothing is running the change." >&2
    fi
  fi
  exit $rc
}
trap finish EXIT

# The rewriter reads $CFG and writes $TMP; it never modifies its input, and it exits
# non-zero without writing when it does not fully understand the block.
python3 "$MIGRATE" "$CFG" --out "$TMP" --print-keys 2>"$KEYS" \
  || { sed 's/^/    | /' "$KEYS" >&2; die "the rewriter refused — $CFG is untouched"; }

mapfile -t MIGRATED < "$KEYS"
[ "${#MIGRATED[@]}" -gt 0 ] || die "the rewriter reported no migrated keys — refusing to continue"
echo "  migrating : ${MIGRATED[*]}"

nix-instantiate --parse "$TMP" >/dev/null 2>&1 \
  || die "the rewritten file is not valid Nix — $CFG is untouched"
echo "  nix parse : OK"

echo "  diff:"
diff -u "$CFG" "$TMP" | sed 's/^/    /' || true

# Backup only now: a refusal above must not litter /etc/nixos with orphaned backups.
[ -e "$BAK" ] && die "backup path $BAK already exists; refusing to overwrite it"
cp -p "$CFG" "$BAK"
echo "  backup    : $BAK"

mv "$TMP" "$CFG"
PATCHED=1
echo "  applied   : $CFG"
echo

# ---------------------------------------------------------------------------- rebuild
echo "== nixos-rebuild dry-build =="
nixos-rebuild dry-build

# `test` activates WITHOUT registering a generation or touching the bootloader, so an
# activation failure here is recoverable; `switch` does both BEFORE activating.
echo "== nixos-rebuild test =="
nixos-rebuild test

echo "== nixos-rebuild switch =="
nixos-rebuild switch
SWITCHED=1
echo

# ----------------------------------------------------------------------------- verify
# Check the keys THIS RUN migrated, not a hardcoded one: the rewriter handles any
# journald.conf(5) key, and a hardcoded check reports a false alarm on any host whose
# config carried a different setting.
echo "== verify =="
JCONF=/etc/systemd/journald.conf
[ -r "$JCONF" ] || die "$JCONF is not readable after the switch"

missing=0
for kv in "${MIGRATED[@]}"; do
  if grep -qxF "$kv" "$JCONF"; then
    echo "  ok        : $kv"
  else
    echo "  MISSING   : $kv — not present in $JCONF as written" >&2
    missing=$((missing + 1))
  fi
done
[ "$missing" = "0" ] || die "$missing migrated setting(s) did not reach $JCONF"

echo
echo "--- $JCONF ---"
cat "$JCONF"
echo "--- journal disk usage ---"
journalctl --disk-usage || true

OK=1
echo
echo "=== DONE ==="
echo "Backup of the previous config: $BAK"
echo "To revert:  sudo cp $BAK $CFG && sudo nixos-rebuild switch"
