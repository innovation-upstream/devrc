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
# Idempotent. Restores the backup if the new config fails to evaluate.
# Run as root:
#
#     sudo bash nix/system/apply-journald-settings-migration.sh
# =============================================================================
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "This must run as root:  sudo bash nix/system/apply-journald-settings-migration.sh" >&2
  exit 1
fi

CFG="/etc/nixos/configuration.nix"
STAMP="$(date +%Y%m%d-%H%M%S)"
BAK="${CFG}.bak-journald-${STAMP}"

echo "=== journald extraConfig -> settings.Journal ==="

# ---------------------------------------------------------------------------- #
# 1. Idempotency.
# ---------------------------------------------------------------------------- #
if ! grep -q 'services\.journald\.extraConfig' "${CFG}"; then
  if grep -q 'services\.journald\.settings\.Journal' "${CFG}"; then
    echo "[1/4] Already migrated — nothing to do."
    exit 0
  fi
  echo "[1/4] ERROR: no services.journald.extraConfig in ${CFG}, and no settings.Journal either." >&2
  echo "      Nothing matches what this script was written against — inspect by hand." >&2
  exit 1
fi
echo "[1/4] Found services.journald.extraConfig."

# ---------------------------------------------------------------------------- #
# 2. Rewrite the block (exact match; refuses on anything unexpected).
# ---------------------------------------------------------------------------- #
cp -p "${CFG}" "${BAK}"
echo "[2/4] Backed up to ${BAK}"

python3 - "${CFG}" <<'PY'
import re, sys

path = sys.argv[1]
src = open(path).read()

# Match the whole assignment, capturing the ''-string body.
pat = re.compile(
    r'^([ \t]*)services\.journald\.extraConfig[ \t]*=[ \t]*\'\'\n(.*?)^[ \t]*\'\';[ \t]*\n',
    re.S | re.M,
)
matches = pat.findall(src)
if len(matches) != 1:
    sys.exit(f"ERROR: expected exactly 1 services.journald.extraConfig block, found {len(matches)}")

indent, body = matches[0]

pairs = []
for raw in body.splitlines():
    line = raw.strip()
    if not line or line.startswith('#'):
        continue
    if '=' not in line:
        sys.exit(f"ERROR: unparseable journald.conf line: {raw!r}")
    k, v = line.split('=', 1)
    k, v = k.strip(), v.strip()
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9]*', k):
        sys.exit(f"ERROR: unexpected journald.conf key: {k!r}")
    pairs.append((k, v))

if not pairs:
    sys.exit("ERROR: extraConfig block contained no settings")

inner = ''.join(f'{indent}  {k} = "{v}";\n' for k, v in pairs)
new = f'{indent}services.journald.settings.Journal = {{\n{inner}{indent}}};\n'

open(path, 'w').write(pat.sub(lambda _m: new, src, count=1))
print('       rewrote: ' + ', '.join(f'{k}={v}' for k, v in pairs))
PY

if grep -q 'services\.journald\.extraConfig' "${CFG}" \
  || ! grep -q 'services\.journald\.settings\.Journal' "${CFG}"; then
  echo "  -> ERROR: rewrite did not land; restoring backup." >&2
  cp -p "${BAK}" "${CFG}"
  exit 1
fi

echo "--- diff ---"
diff -u "${BAK}" "${CFG}" || true
echo "------------"

# ---------------------------------------------------------------------------- #
# 3. Evaluate before switching; restore on failure.
# ---------------------------------------------------------------------------- #
echo "[3/4] Parsing + dry-build..."
if ! nix-instantiate --parse "${CFG}" >/dev/null; then
  echo "  -> ERROR: ${CFG} no longer parses; restoring backup." >&2
  cp -p "${BAK}" "${CFG}"
  exit 1
fi
if ! nixos-rebuild dry-build; then
  echo "  -> ERROR: dry-build failed; restoring backup." >&2
  cp -p "${BAK}" "${CFG}"
  echo "     Original config restored — the rebuild error above is the thing to fix." >&2
  exit 1
fi

# ---------------------------------------------------------------------------- #
# 4. Switch, then verify the generated journald.conf actually carries the value.
# ---------------------------------------------------------------------------- #
echo "[4/4] Rebuilding (switch)..."
nixos-rebuild switch

echo
echo "=== verification ==="
echo "--- /etc/systemd/journald.conf ---"
cat /etc/systemd/journald.conf
echo "--- effective SystemMaxUse (systemd-analyze) ---"
systemd-analyze cat-config systemd/journald.conf 2>/dev/null | grep -i 'SystemMaxUse' || \
  echo "(no SystemMaxUse line found — CHECK THIS, the cap may not be applied)"
echo "--- journal disk usage ---"
journalctl --disk-usage
echo
echo "Done. Backup of the pre-change config: ${BAK}"
