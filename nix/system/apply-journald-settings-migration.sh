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
# never overwritten), the new file is moved into place, and from that moment an
# EXIT trap restores the backup on any failure. MEASURED: bash runs that trap on
# SIGTERM. Ctrl-C (SIGINT) was NOT established either way — do not read the trap
# as a guarantee there.
#
# `nixos-rebuild test` runs before `switch`, so an ACTIVATION failure does not
# leave a registered generation and a rewritten bootloader behind. 🔴 But `test`
# ACTIVATES: between it and `switch` the change IS running, so a rollback in that
# window restores the FILE while the system keeps running the change until a
# reboot. The trap says so explicitly rather than claiming nothing is running.
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
ACTIVATION_ATTEMPTED=0  # `nixos-rebuild test` was entered (may have partly applied)
SWITCH_ATTEMPTED=0      # `nixos-rebuild switch` was entered — bootloader ALREADY written
ACTIVATED=0   # `nixos-rebuild test` returned 0 — activated, not persisted
SWITCHED=0    # `nixos-rebuild switch` has returned (activated AND persisted)
OK=0

finish() {
  local rc=$?
  rm -f "$TMP" "$KEYS" ${MERGED:+"$MERGED"}
  if [ "$OK" = "1" ]; then return; fi
  if [ "$PATCHED" = "1" ] && [ -f "$BAK" ]; then
    echo >&2
    # Without `|| { … }` a failing cp aborts the trap under the inherited `set -e`, so
    # neither the rollback line NOR the running-state advisory below would be printed —
    # a silently failed rollback, which is worse than a loud one.
    if cp -p "$BAK" "$CFG"; then
      echo "ROLLED BACK: $CFG restored from $BAK" >&2
    else
      echo "🔴 ROLLBACK FAILED: could not restore $CFG from $BAK." >&2
      echo "   Do it by hand:  sudo cp $BAK $CFG" >&2
    fi
    # 🔴 Three states, not two. `nixos-rebuild test` ACTIVATES the configuration (it
    # only skips the boot menu), so the window between `test` returning and `switch`
    # returning is one where the file is restored while the change IS running. Saying
    # "nothing is running the change" there is false, and it is said at exactly the
    # moment the operator is deciding what to do next.
    if [ "$SWITCHED" = "1" ]; then
      echo "🔴 The system had ALREADY been switched. The FILE is restored but the RUNNING" >&2
      echo "   system is not — run \`sudo nixos-rebuild switch\` to return it." >&2
    elif [ "$SWITCH_ATTEMPTED" = "1" ]; then
      echo "🔴 \`nixos-rebuild switch\` was entered but did not complete. It installs the" >&2
      echo "   bootloader BEFORE activating, so the migrated generation is most likely" >&2
      echo "   ALREADY IN THE BOOT MENU — a reboot does NOT reliably revert it. The file" >&2
      echo "   is restored; run \`sudo nixos-rebuild switch\` to bring the system and the" >&2
      echo "   boot entry back to it." >&2
    elif [ "$ACTIVATED" = "1" ]; then
      echo "🔴 \`nixos-rebuild test\` had ALREADY ACTIVATED the change, so it IS running now," >&2
      echo "   even though the file is restored. It was never added to the boot menu, so a" >&2
      echo "   REBOOT reverts it — or run \`sudo nixos-rebuild switch\` to activate the" >&2
      echo "   restored config immediately." >&2
    elif [ "$ACTIVATION_ATTEMPTED" = "1" ]; then
      echo "🔴 \`nixos-rebuild test\` was entered but did not complete, so the change may be" >&2
      echo "   PARTLY APPLIED to the running system. The file is restored; run" >&2
      echo "   \`sudo nixos-rebuild switch\` to bring the system back to it." >&2
    else
      echo "   Activation was never attempted, so nothing is running the change." >&2
    fi
  fi
  exit $rc
}
trap finish EXIT

# The rewriter reads $CFG and writes $TMP; it never modifies its input, and it exits
# non-zero without writing when it does not fully understand the block.
python3 "$MIGRATE" "$CFG" --out "$TMP" --print-keys 2>"$KEYS" \
  || { sed 's/^/    | /' "$KEYS" >&2; die "the rewriter refused — $CFG is untouched"; }

# Filter to well-formed KEY=VALUE lines. --print-keys shares stderr with anything else
# python writes there (a DeprecationWarning, PYTHONWARNINGS, a sitecustomize), and a
# stray line would become a phantom key that the post-switch check then cannot find —
# rolling back a migration that actually worked.
mapfile -t MIGRATED < <(grep -E '^[A-Za-z][A-Za-z0-9]*=' "$KEYS" || true)
noise=$(grep -cvE '^[A-Za-z][A-Za-z0-9]*=' "$KEYS" || true)
[ "${noise:-0}" = "0" ] || {
  echo "  note      : ignored $noise non-key line(s) on the rewriter's stderr:" >&2
  grep -vE '^[A-Za-z][A-Za-z0-9]*=' "$KEYS" | sed 's/^/    | /' >&2
}
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

# PATCHED is armed BEFORE the mv: a fatal signal landing between the two would
# otherwise leave the file patched with the trap declining to restore it. The trap
# also gates on `[ -f "$BAK" ]`, so an unnecessary restore is a harmless no-op.
PATCHED=1
mv "$TMP" "$CFG"
echo "  applied   : $CFG"
echo

# ---------------------------------------------------------------------------- rebuild
echo "== nixos-rebuild dry-build =="
nixos-rebuild dry-build

# `test` activates WITHOUT registering a generation or touching the bootloader, so an
# activation failure here is recoverable; `switch` does both BEFORE activating.
echo "== nixos-rebuild test =="
# Armed BEFORE the call: `test` failing PARTWAY through activation has already applied
# some of the change, so "never activated" would be false there too — the same defect
# as the switch/test boundary, one step earlier.
ACTIVATION_ATTEMPTED=1
nixos-rebuild test
ACTIVATED=1

echo "== nixos-rebuild switch =="
# 🔴 Armed BEFORE the call, and the reason is not symmetry for its own sake:
# switch-to-configuration runs do_install_bootloader for Action::Switch BEFORE
# activation (nixpkgs 26.11, switch-to-configuration-ng/src/main.rs). So at the only
# moment a `switch` can fail mid-activation, the new generation IS already in the boot
# menu — and telling the operator "a reboot reverts it" would be exactly backwards.
SWITCH_ATTEMPTED=1
nixos-rebuild switch
SWITCHED=1
echo

# ----------------------------------------------------------------------------- verify
# Check the keys THIS RUN migrated, not a hardcoded one: the rewriter handles any
# journald.conf(5) key, and a hardcoded check reports a false alarm on any host whose
# config carried a different setting.
echo "== verify =="
# 🔴 `systemd-analyze cat-config` CONCATENATES the main file and every drop-in, with
# `# <path>` headers between them — it does NOT resolve precedence. So a plain
# `grep -qxF` over its output matches an overridden line and reports ok, and it also
# matches a line that lives ONLY in a stale drop-in and never reached journald.conf.
# Both were measured. We resolve precedence ourselves below: LAST assignment wins,
# which is the order cat-config emits and the order systemd applies.
MERGED="$(mktemp -t journald-merged-XXXXXXXX)"
DROPINS_SEEN=no
if systemd-analyze cat-config systemd/journald.conf > "$MERGED" 2>/dev/null && [ -s "$MERGED" ]; then
  DROPINS_SEEN=yes
  echo "  reading   : systemd-analyze cat-config systemd/journald.conf (main file + drop-ins)"
else
  echo "  reading   : /etc/systemd/journald.conf (systemd-analyze unavailable — DROP-INS NOT CHECKED)" >&2
  cat /etc/systemd/journald.conf > "$MERGED" 2>/dev/null \
    || die "neither systemd-analyze nor /etc/systemd/journald.conf could be read"

fi

# TWO independent facts per key, because either alone can be true while the migration
# is broken:
#   (a) LANDED   — the pair is in /etc/systemd/journald.conf, the file NixOS generates
#                  from settings.Journal. Proves the migration itself worked.
#   (b) IN EFFECT — it is the LAST assignment across the concatenation. Proves nothing
#                  overrides it.
# Checking only (b) passes when a stale drop-in happens to supply the same value and the
# migration never landed; checking only (a) passes when a drop-in overrides it. Measured
# both ways in audit round 3.
missing=0
for kv in "${MIGRATED[@]}"; do
  key="${kv%%=*}"
  landed=no
  grep -qxF "$kv" /etc/systemd/journald.conf 2>/dev/null && landed=yes
  # Comment lines (`# <path>`, `#SystemMaxUse=`) cannot match: the key is anchored at
  # start-of-line.
  eff="$(grep -E "^${key}=" "$MERGED" | tail -1 || true)"

  if [ "$landed" = "yes" ] && [ "$eff" = "$kv" ]; then
    # Only claim the second fact when it was actually a SEPARATE read. In the fallback
    # branch $MERGED is a copy of journald.conf, so `landed` and `eff` are the same
    # bytes and "in effect" would be asserting a check that never happened — the same
    # shape as the mislabelled "merged" output this verify loop was rewritten to fix.
    if [ "$DROPINS_SEEN" = "yes" ]; then
      echo "  ok        : $kv  (in journald.conf, and in effect)"
    else
      echo "  ok        : $kv  (in journald.conf; DROP-INS NOT CHECKED, so 'in effect' is unverified)"
    fi
  elif [ "$landed" = "no" ] && [ "$eff" = "$kv" ]; then
    echo "  NOT LANDED: $kv is in effect, but NOT in /etc/systemd/journald.conf —" >&2
    echo "              something other than settings.Journal is supplying it." >&2
    missing=$((missing + 1))
  elif [ -n "$eff" ]; then
    # Deliberately does not name the cause: the later assignment is USUALLY a drop-in,
    # but the same key appearing twice in journald.conf produces this too, and when the
    # pair never landed the `landed=no` half is what matters. Print what was measured.
    echo "  NOT IN EFFECT: $kv — last assignment of ${key} is '${eff}' (landed=${landed})" >&2
    missing=$((missing + 1))
  else
    echo "  MISSING   : $kv — no assignment of ${key} anywhere in the journald config" >&2
    missing=$((missing + 1))
  fi
done
[ "$missing" = "0" ] || die "$missing migrated setting(s) did not land or are not in effect"

echo
echo "--- merged journald config ---"
cat "$MERGED"
echo "--- journal disk usage ---"
journalctl --disk-usage || true

OK=1
echo
echo "=== DONE ==="
echo "Backup of the previous config: $BAK"
echo "To revert:  sudo cp $BAK $CFG && sudo nixos-rebuild switch"
