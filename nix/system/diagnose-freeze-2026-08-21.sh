#!/usr/bin/env bash
# Read-only diagnostic — LAPTOP host, 2026-08-21, after the 17th unclean stop.
#
#   sudo bash nix/system/diagnose-freeze-2026-08-21.sh
#
# Answers two questions this session cannot answer without root:
#
#   A. WHAT DID THE 2026-08-21 18:45 FREEZE CAPTURE?
#      systemd-pstore archived three new records at 19:06 (epoch ~1787355936,
#      which is 18:45:36 — the stop). The decoded dmesg.txt files are 0600 root.
#      This is the first freeze WITH #616's instrumentation in place, so its
#      trace is the highest-value evidence available.
#
#   B. WHO TURNS THE NMI WATCHDOG BACK OFF?
#      kernel.nmi_watchdog reads 0 on this boot even though #616 put
#      NMI_WATCHDOG=1 in TLP's config and TLP applied it. Observed timeline:
#          19:06:31  kernel: "NMI watchdog: Enabled"
#          19:06:38  tlp: "Applying power save settings...done."
#          19:06:38  powertop --auto-tune ("Powertop tunings" finished)
#          now:      /proc/sys/kernel/nmi_watchdog = 0
#      powertop's binary contains the string /proc/sys/kernel/nmi_watchdog and
#      --auto-tune disables it. That is a STRONG CORRELATION, NOT A PROOF — both
#      units finished in the same second, so the journal cannot separate them.
#      Section B runs the discriminating control: set the value to 1, restart
#      ONLY powertop.service, and re-read. If it flips to 0, powertop is the
#      writer. If it stays 1, it is not, and the search continues.
#
#      This matters more than it sounds: hardlockup_panic=1 (set by #616) is
#      INERT while nmi_watchdog=0. The hard-lockup detector never runs, so it
#      can never fire the panic. The instrumentation from #616 is, in its
#      headline case, currently doing nothing.
#
# The only state this script writes is /proc/sys/kernel/nmi_watchdog, and it
# restores the value it found. Everything else is a read.
set -Eeuo pipefail

[[ $EUID -eq 0 ]] || { echo "must run as root (sudo bash $0)"; exit 1; }

OUT=${OUT:-/tmp/freeze-diag-2026-08-21.txt}
: >"$OUT"
chmod 644 "$OUT"
exec > >(tee -a "$OUT") 2>&1

echo "===================== A. CRASH DUMPS ====================="
# Only the records archived on 2026-08-21 — the earlier ones (1785525502 etc.)
# were already read in the 07-31 analysis and are in the handoff doc.
found=0
while IFS= read -r f; do
  found=$((found + 1))
  echo
  echo "----- $f -----"
  echo "(size: $(stat -c %s "$f") bytes, mtime: $(stat -c %y "$f"))"
  cat "$f"
done < <(find /var/lib/systemd/pstore/1787355936 \
              /var/lib/systemd/pstore/1787355937 \
              /var/lib/systemd/pstore/1787355939 \
              -name 'dmesg.txt' 2>/dev/null | sort)

echo
echo "decoded dmesg.txt files found: $found"
if [[ $found -eq 0 ]]; then
  echo "🔴 ZERO decoded dumps. That is NOT 'the freeze left no trace' — the raw"
  echo "   dmesg-efi_pstore-* fragments are still there. Falling back to them:"
  find /var/lib/systemd/pstore/17873559?? -type f 2>/dev/null | sort | head -40
fi

echo
echo "===================== B. NMI WATCHDOG WRITER ====================="
NMIWD=/proc/sys/kernel/nmi_watchdog
orig=$(cat "$NMIWD")
echo "value now (before control): $orig"

restore() { echo "$orig" >"$NMIWD" 2>/dev/null || true; }
trap restore EXIT

echo "--- positive control: can this file be written at all? ---"
echo 1 >"$NMIWD"
after_write=$(cat "$NMIWD")
echo "wrote 1, reads back: $after_write"
if [[ $after_write -ne 1 ]]; then
  echo "🔴 The kernel REFUSED the write (reads $after_write). Neither TLP nor"
  echo "   powertop is the cause — the hard-lockup detector is unavailable on"
  echo "   this kernel/CPU (no usable PMU counter?). Stop here; section B's"
  echo "   result below would be meaningless."
  echo "   Check: dmesg | grep -i 'nmi watchdog'"
  exit 0
fi

echo
echo "--- the control: restart ONLY powertop.service ---"
systemctl restart powertop.service
sleep 2
after_powertop=$(cat "$NMIWD")
echo "after powertop.service restart: $after_powertop"

echo
echo "--- cross-check: restart ONLY tlp.service (should KEEP it at 1) ---"
echo 1 >"$NMIWD"
systemctl restart tlp.service
sleep 2
after_tlp=$(cat "$NMIWD")
echo "after tlp.service restart:      $after_tlp"

echo
echo "===================== VERDICT ====================="
if [[ $after_powertop -eq 0 && $after_tlp -eq 1 ]]; then
  echo "CONFIRMED: powertop --auto-tune is the writer. TLP is correctly holding"
  echo "it at 1 (#616 works); powertop runs after and stomps it."
elif [[ $after_powertop -eq 1 && $after_tlp -eq 0 ]]; then
  echo "UNEXPECTED: TLP is the writer and #616's NMI_WATCHDOG=1 is not taking"
  echo "effect. Re-read /etc/tlp.conf and TLP's config precedence."
elif [[ $after_powertop -eq 1 && $after_tlp -eq 1 ]]; then
  echo "NEITHER unit clears it in isolation. The writer is something else, or it"
  echo "only acts on a power-source transition. Next probe: move the charger and"
  echo "re-read, then audit units ordered After=multi-user.target."
else
  echo "BOTH cleared it (powertop=$after_powertop tlp=$after_tlp). Treat powertop"
  echo "as the boot-time writer and TLP as a second one; fix must cover both."
fi

echo
echo "value restored on exit to: $orig"
echo
echo "Full output saved to $OUT (world-readable, so the session can read it)."
