#!/usr/bin/env bash
# Clear 5 of the workbench's 6 `nixos-rebuild` evaluation warnings.
#
#   sudo bash nix/system/apply-nixos-option-renames-2026-09-09.sh
#   bash nix/system/apply-nixos-option-renames-2026-09-09.sh --dry-run   # no root needed
#
# ── WHY ──────────────────────────────────────────────────────────────────────
#
# MEASURED 2026-09-09 on the workbench (NixOS 26.11 "Zokor", plain channels, not
# flakes). `nixos-rebuild dry-build` emits SIX lines beginning
# `evaluation warning:`. Five are pure option/attribute RENAMES that nixpkgs has
# already aliased; the sixth is not a rename at all and is deliberately left.
#
#   1. services.dnsmasq.servers        -> services.dnsmasq.settings.server
#   2. services.gnome.tracker.enable   -> services.gnome.tinysparql.enable
#   3. services.gnome.tracker-miners.enable -> services.gnome.localsearch.enable
#   4. xorg.xrandr                     -> xrandr
#   5. xdg-desktop-portal 1.17 wants an explicit `xdg.portal.config`
#   6. wineWowPackages                 -> wineWow64Packages    ← NOT TOUCHED
#
# ── PATCH A (items 1-4): PROVEN BYTE-IDENTICAL ───────────────────────────────
# Verified 2026-09-09 by evaluating the patched config: the SAME system
# derivation (`q97dj70ph64md5vjsf0x757jq6vjbmni`), 0 derivations built, no new
# generation, no unit restarts. These four are the alias being spelled out.
#
#   * `settings` is a `pkgs.formats` key-value format with
#     `listsAsDuplicateKeys = true`, so the list becomes repeated `server=`
#     lines in dnsmasq.conf — byte-for-byte what `servers` already produced.
#   * `xorg.xrandr` and `xrandr` are the same derivation. NOTE the config
#     ALREADY uses the correct `${pkgs.xrandr}` in `sessionCommands`; only the
#     i3 `extraPackages` entry is stale, and this script changes only that one.
#
# ── PATCH B (item 5): 5 TRIVIAL DERIVATIONS, ONE NEW FILE ────────────────────
# Adding `config.common.default = "*";` inside the existing `xdg.portal` block
# creates `/etc/xdg/xdg-desktop-portal/portals.conf` (which does not exist on
# this host today — the directory is empty). `"*"` reproduces the pre-1.17
# behaviour: "use the first portal that implements the interface", which on this
# host is the single installed `xdg-desktop-portal-gtk`. It is not a behaviour
# change; it is writing down the behaviour that was previously implicit.
#
# ── WHY WARNING 6 STAYS: wineWowPackages IS NOT A RENAME ─────────────────────
# 🔴 DO NOT "FINISH THE JOB" BY ADDING IT. `wineWowPackages` is a genuine 32-bit
# + 64-bit pair; `wineWow64Packages` is a SINGLE 64-bit binary relying on
# upstream's new WoW64 thunking layer to run 32-bit code. Different mechanism,
# different bug surface. The config's own comment records that the current
# spelling was tuned until Lutris / Ubisoft Connect worked, so swapping it is a
# functional change to a working setup, dressed as a deprecation cleanup.
#
# It is also the LEAST likely of the six to ever break: its nixpkgs alias
# carries no `# Added <date>` comment, and nixpkgs' `remove-old-aliases.py`
# skips undated entries by construction — so the mechanical alias-reaper that
# eventually deletes the other five structurally cannot delete this one.
#
# One warning is the correct steady state here. If you want it gone, that is a
# separate, TESTED change: swap it, then actually launch Ubisoft Connect.
#
# ── 🔴 HAZARD 1: NIX_PATH POISONING WOULD ROLL THE SYSTEM BACKWARDS ──────────
# MEASURED 2026-09-09. zach's interactive `NIX_PATH` begins with
# `$HOME/.nix-defexpr/channels`, which is searched BEFORE the explicit
# `nixpkgs=` entry. That channel is `nixpkgs-unstable` at 26.11pre1068924 —
# OLDER than root's `nixos` channel 26.11pre1068949, which is what the running
# system was built from. `sudo -E nixos-rebuild switch` (the natural form,
# because it preserves the PATH that hazard 2 needs) therefore evaluates the
# config against an OLDER nixpkgs and rebuilds ~34 derivations, moving the
# system backwards, silently.
#
# Plain `sudo` happens to be safe because sudoers has `env_reset`. This script
# does not depend on how it was invoked: it sets NIX_PATH explicitly, to root's
# channel and nothing else, and verifies that path resolves before rebuilding.
#
# 🔴 It SETS rather than UNSETS. Unsetting also removes the poisoning entry, but
# NixOS's `nix.nixPath` default is what supplies the `nixpkgs=` alias in the
# first place, and root's channel directory is named `nixos`, not `nixpkgs` —
# so with NIX_PATH unset, `<nixpkgs>` is resolved by nix's built-in default
# instead, whose first component is again `$HOME/.nix-defexpr/channels`. Under
# `sudo -E` (HOME preserved) that is zach's older channel a second time.
# Setting the value removes the whole class instead of one instance of it.
#
# ── 🔴 HAZARD 2: root HAS NO python3 (OR dig) ────────────────────────────────
# MEASURED: neither `python3` nor `dig` is in `/run/current-system/sw/bin`. Both
# come from zach's user profile, so root only has them if the caller's PATH is
# carried in. This script PREFLIGHTS python3 BEFORE it touches anything and
# prints the exact remedy, rather than taking a backup and then half-applying.
#
# ── ROLLBACK ─────────────────────────────────────────────────────────────────
#   sudo nixos-rebuild switch --rollback
# and the timestamped `configuration.nix.bak-*` this script leaves beside the
# config restores the source.
set -euo pipefail

# `NIXOPTS_CFG` / `NIXOPTS_PORTALS_CONF` exist so scripts/tests can drive this
# against a fixture. Setting either one puts the script in TEST MODE: it does
# not require root and it says so loudly.
CFG="${NIXOPTS_CFG:-/etc/nixos/configuration.nix}"
PORTALS_CONF="${NIXOPTS_PORTALS_CONF:-/etc/xdg/xdg-desktop-portal/portals.conf}"
ROOT_CHANNELS="${NIXOPTS_ROOT_CHANNELS:-/nix/var/nix/profiles/per-user/root/channels}"
TEST_MODE=no
[[ -n "${NIXOPTS_CFG:-}${NIXOPTS_PORTALS_CONF:-}${NIXOPTS_ROOT_CHANNELS:-}" ]] && TEST_MODE=yes

DRY_RUN=no
case "${1:-}" in
  --dry-run) DRY_RUN=yes ;;
  -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
  "") : ;;
  *)
    echo "ERROR: unrecognised argument '${1}'. This script EDITS ${CFG} and runs" >&2
    echo "       nixos-rebuild; it refuses anything it does not recognise." >&2
    echo "       Use --dry-run or --help." >&2
    exit 64
    ;;
esac
if [[ $# -gt 1 ]]; then
  echo "ERROR: too many arguments (got $#). Refusing rather than editing ${CFG}." >&2
  exit 64
fi

if [[ "$TEST_MODE" == "yes" ]]; then
  echo "🔴 TEST MODE — editing fixture ${CFG}, NOT /etc/nixos."
elif [[ "$DRY_RUN" == "no" && $EUID -ne 0 ]]; then
  echo "This script edits ${CFG} and runs nixos-rebuild; it must run as root:" >&2
  echo "    sudo bash $0" >&2
  echo "  (or run with --dry-run, which needs no privileges and writes nothing)" >&2
  exit 1
fi

[[ -f "$CFG" ]] || { echo "ERROR: ${CFG} not found — is this a NixOS host?" >&2; exit 1; }
[[ -r "$CFG" ]] || { echo "ERROR: cannot read ${CFG}" >&2; exit 1; }

# ── PREFLIGHT (hazard 2) — before the backup, before any write ───────────────
if ! command -v python3 >/dev/null 2>&1; then
  # 🔴 BUILTINS ONLY IN THIS BRANCH. It fires precisely when the environment is
  # impoverished, so it must not itself depend on finding `cat` on PATH.
  echo "ERROR: no python3 on PATH." >&2
  echo >&2
  echo "  /run/current-system/sw/bin has no python3 on this host — it comes from" >&2
  echo "  zach's user profile — so a plain \`sudo\` (env_reset) leaves root without" >&2
  echo "  one. NOTHING HAS BEEN CHANGED: no backup, no edit, no rebuild." >&2
  echo >&2
  echo "  Re-run carrying your PATH in:" >&2
  echo >&2
  echo "      sudo env \"PATH=\$PATH\" bash $0" >&2
  echo >&2
  echo "  That also gives the verify section a \`dig\`, which is missing for the" >&2
  echo "  same reason. Do NOT work around this by editing the file by hand halfway." >&2
  exit 1
fi

echo "python3: $(command -v python3)"
if command -v dig >/dev/null 2>&1; then
  echo "dig:     $(command -v dig)"
else
  echo "dig:     NOT ON PATH — two verify checks will be printed for you to run"
  echo "         yourself instead of being run here. Re-run as"
  echo "         \`sudo env \"PATH=\$PATH\" bash $0\` to have them run inline."
fi

# ── HAZARD 1 — pin NIX_PATH to root's channel, explicitly ────────────────────
if [[ "$DRY_RUN" == "no" && "$TEST_MODE" == "no" ]]; then
  [[ -e "${ROOT_CHANNELS}/nixos" ]] || {
    echo "ERROR: ${ROOT_CHANNELS}/nixos does not exist." >&2
    echo "       This host is not on plain channels the way this patch assumes." >&2
    echo "       Nothing has been changed. Sort out the channel first." >&2
    exit 1
  }
  echo
  echo "NIX_PATH inherited: ${NIX_PATH:-<unset>}"
  export NIX_PATH="nixpkgs=${ROOT_CHANNELS}/nixos:nixos-config=${CFG}:${ROOT_CHANNELS}"
  echo "NIX_PATH pinned to: ${NIX_PATH}"
  echo "  root channel rev: $(cat "${ROOT_CHANNELS}/nixos/.version-suffix" 2>/dev/null || echo '?')"
  echo "  (If the inherited value began with a \$HOME/.nix-defexpr entry, that is"
  echo "   hazard 1 and pinning above is what stops it rolling the system back.)"
fi

# ── the edits ────────────────────────────────────────────────────────────────
if [[ "$DRY_RUN" == "no" ]]; then
  BACKUP="${CFG}.bak-$(date +%Y%m%d-%H%M%S)"
  cp -a "$CFG" "$BACKUP"
  echo
  echo "backup: ${BACKUP}"
fi

echo
echo "Editing ${CFG}:"

# 🔴 THE `set -e` TRAP, BOTH DIRECTIONS. `python3 …; rc=$?` is DEAD CODE — under
# `set -e` a non-zero exit kills the shell before the assignment runs. And
# `python3 … || rc=$?` WITHOUT the `rc=0` first crashes under `set -u` on the
# SUCCESS path, because nothing ever assigns rc. Only this form is correct.
rc=0
python3 - "$CFG" "$DRY_RUN" <<'PYBLOCK' || rc=$?
import re
import sys

path, dry = sys.argv[1], sys.argv[2] == "yes"
src = open(path, encoding="utf-8").read()
orig = src
actions = []
expected_delta = 0


def refuse(msg):
    print("\nREFUSING: " + msg, file=sys.stderr)
    print("The config has drifted from what this patch was written against.",
          file=sys.stderr)
    print("NOTHING HAS BEEN WRITTEN. Apply the change by hand rather than "
          "letting the script guess which occurrence to edit.", file=sys.stderr)
    raise SystemExit(2)


# ── EDIT 1: services.dnsmasq.servers -> services.dnsmasq.settings.server ─────
#
# 🔴 STRUCTURAL, NOT LITERAL — ON PURPOSE. The value being moved is a list of
# resolver addresses, and this repo is PUBLIC with a gate that rejects a
# routable IP literal in a tracked file (scripts/tests/test_no_public_ips.py).
# So the list is never retyped: it is CAPTURED from the live config and
# re-emitted verbatim. The script therefore contains no address at all, and it
# also cannot silently substitute a stale copy of the list.
#
# The regex requires the key at the start of a line after only whitespace, so
# the commented-out `#servers = [...]` line directly above it cannot match.
SERVERS_RE = re.compile(
    r'^(?P<indent>[ \t]*)servers = (?P<list>\[[^\n]*\]);(?P<trail>[^\n]*)\n', re.M)
SETTINGS_SERVER_RE = re.compile(r'^[ \t]*server = \[', re.M)
# A comment line the migration should CARRY. The trailing space is load-bearing:
# it stops the commented-out `#servers = [...]` line above from being swallowed
# into the block and re-indented into the settings attrset as live-looking text.
COMMENT_RE = re.compile(r'^[ \t]*# ')

matches = list(SERVERS_RE.finditer(src))
if not matches:
    if SETTINGS_SERVER_RE.search(src):
        actions.append("dnsmasq: already migrated (settings.server present)")
    else:
        refuse("no `servers = [...];` line, and no `server = [...]` inside "
               "`settings` either — cannot tell which state this config is in")
elif len(matches) != 1:
    refuse("expected exactly 1 `servers = [...];` line, found %d" % len(matches))
else:
    m = matches[0]
    indent = m.group("indent")

    # Walk back over the contiguous run of `# ` comment lines above it.
    before = src[:m.start()].split("\n")
    if before[-1] != "":
        refuse("internal: the `servers` match did not start on a line boundary")
    i = len(before) - 2
    comments = []
    while i >= 0 and COMMENT_RE.match(before[i]):
        comments.insert(0, before[i])
        i -= 1
    block = "".join(c + "\n" for c in comments)
    cut = m.start() - len(block)
    if src[cut:m.start()] != block:
        refuse("internal: comment-block slice did not round-trip")

    # The move target must be the very next line, and at the same indent.
    after = src[m.end():]
    sm = re.match(r'^(?P<i>[ \t]*)settings = \{[ \t]*\n', after)
    if not sm:
        refuse("the line after `servers = [...];` is not `settings = {` — "
               "refusing to guess where inside the attrset to move it")
    if sm.group("i") != indent:
        refuse("`settings = {` is indented %r but `servers` is %r — refusing"
               % (sm.group("i"), indent))
    if SETTINGS_SERVER_RE.search(src):
        refuse("a `server = [...]` key already exists inside `settings` while a "
               "top-level `servers` also exists — refusing to create a duplicate")

    inner = indent + "  "
    moved = "".join(inner + c.lstrip() + "\n" for c in comments)
    moved += "%sserver = %s;%s\n" % (inner, m.group("list"), m.group("trail"))

    src = src[:cut] + after[:sm.end()] + moved + after[sm.end():]
    expected_delta += len(moved) - (m.end() - cut)
    actions.append("dnsmasq: moved `servers` into `settings.server`, carrying "
                   "%d comment line(s)" % len(comments))

# ── EDITS 2 & 3: the GNOME tracker renames ───────────────────────────────────
# `services.gnome.tracker.enable` is NOT a prefix of
# `services.gnome.tracker-miners.enable` (`.` vs `-`), so the two are unambiguous
# as plain substrings and order does not matter.
for old, new in (("services.gnome.tracker.enable", "services.gnome.tinysparql.enable"),
                 ("services.gnome.tracker-miners.enable", "services.gnome.localsearch.enable")):
    n_old, n_new = src.count(old), src.count(new)
    if n_old == 0 and n_new >= 1:
        actions.append("gnome: %s already renamed" % old)
        continue
    if n_old != 1:
        refuse("expected exactly 1 occurrence of `%s`, found %d" % (old, n_old))
    if n_new != 0:
        refuse("`%s` is present %d time(s) while `%s` still exists — refusing to "
               "create a duplicate definition" % (new, n_new, old))
    src = src.replace(old, new, 1)
    expected_delta += len(new) - len(old)
    actions.append("gnome: %s -> %s" % (old, new))

# ── EDIT 4: xorg.xrandr -> xrandr, in i3's extraPackages ONLY ────────────────
# 🔴 The config ALSO uses `${pkgs.xrandr}` in `sessionCommands`, which is already
# correct. The anchors below match a bare package name on a line of its own —
# the `with pkgs; [ … ]` list element — so the interpolation cannot match.
XORG_XRANDR_RE = re.compile(r'^(?P<i>[ \t]*)xorg\.xrandr[ \t]*$', re.M)
BARE_XRANDR_RE = re.compile(r'^[ \t]*xrandr[ \t]*$', re.M)
xr = list(XORG_XRANDR_RE.finditer(src))
if not xr:
    if BARE_XRANDR_RE.search(src):
        actions.append("xrandr: already renamed")
    else:
        refuse("neither an `xorg.xrandr` nor a bare `xrandr` package-list entry "
               "was found — cannot tell which state this config is in")
elif len(xr) != 1:
    refuse("expected exactly 1 `xorg.xrandr` package-list entry, found %d" % len(xr))
else:
    src = XORG_XRANDR_RE.sub(lambda mo: mo.group("i") + "xrandr", src, count=1)
    expected_delta += len("xrandr") - len("xorg.xrandr")
    actions.append("xrandr: xorg.xrandr -> xrandr (i3 extraPackages)")

# ── EDIT 5 (patch B): an explicit xdg.portal default ─────────────────────────
#
# 🔴 EVERY PATTERN HERE IS LINE-ANCHORED PAST WHITESPACE ONLY, SO A `#` KILLS IT.
# That is not stylistic. This config carries a large COMMENTED-OUT `xdg.portal`
# block (an old termfilechooser experiment) which contains, among other things,
# `#xdg.portal.config.common.default = "*";` and a `#    extraPortals = with
# pkgs; [`. A plain `in src` idempotency check MATCHED that dead line and made
# the script report "default already set" and skip patch B entirely — measured,
# 2026-09-09, on the first fixture run. A commented line is not a definition.
PORTAL_BLOCK_RE = re.compile(r'^[ \t]*xdg\.portal = \{[ \t]*$', re.M)
EXTRA_PORTALS_RE = re.compile(
    r'^(?P<i>[ \t]*)extraPortals = \[[^\n]*\];[ \t]*\n', re.M)
PORTAL_SET_RE = re.compile(
    r'^[ \t]*(?:xdg\.portal\.)?config\.common\.default[ \t]*=', re.M)
PORTAL_LINE = 'config.common.default = "*";'
if PORTAL_SET_RE.search(src):
    actions.append("xdg.portal: default already set")
else:
    pb = list(PORTAL_BLOCK_RE.finditer(src))
    if len(pb) != 1:
        refuse("expected exactly 1 `xdg.portal = {` block, found %d" % len(pb))
    ep = [e for e in EXTRA_PORTALS_RE.finditer(src) if e.start() > pb[0].start()]
    if len(ep) != 1:
        refuse("expected exactly 1 `extraPortals = [...];` line after "
               "`xdg.portal = {`, found %d" % len(ep))
    e = ep[0]
    ind = e.group("i")
    add = (
        '%s# xdg-desktop-portal 1.17+ no longer falls back to "the first portal that\n'
        '%s# implements the interface" — it warns and asks to be told. "*" IS that old\n'
        '%s# behaviour, written down: use whatever backend is installed, which here is\n'
        '%s# the single gtk portal above. Creates /etc/xdg/xdg-desktop-portal/portals.conf.\n'
        '%s%s\n' % (ind, ind, ind, ind, ind, PORTAL_LINE))
    src = src[:e.end()] + add + src[e.end():]
    expected_delta += len(add)
    actions.append("xdg.portal: added %s" % PORTAL_LINE)

# ── verify the AFTER state BEFORE writing ────────────────────────────────────
problems = []
if SERVERS_RE.search(src):
    problems.append("a top-level `servers = [...];` line survives the migration")
if not SETTINGS_SERVER_RE.search(src):
    problems.append("no `server = [...]` key inside `settings` after the migration")
for dead in ("services.gnome.tracker.enable", "services.gnome.tracker-miners.enable"):
    if dead in src:
        problems.append("`%s` survives" % dead)
for live in ("services.gnome.tinysparql.enable", "services.gnome.localsearch.enable"):
    if src.count(live) != 1:
        problems.append("expected exactly 1 `%s`, found %d" % (live, src.count(live)))
if XORG_XRANDR_RE.search(src):
    problems.append("an `xorg.xrandr` package-list entry survives")
n_portal = len(PORTAL_SET_RE.findall(src))
if n_portal != 1:
    problems.append("expected exactly 1 LIVE `config.common.default = …` line, found %d"
                    % n_portal)
# Nothing outside the five edits may move.
#
# DEFENCE IN DEPTH, and honestly labelled: with the five edits implemented
# correctly this check is redundant with the line-level assertion in
# scripts/tests (`test_nothing_outside_the_five_edits_moves`), so deleting it
# from a CORRECT script changes nothing and it survives a mutation sweep as an
# equivalent mutant. It is REACHABLE, and proven so: mis-accounting the xrandr
# edit by 5 bytes makes it fire with its own message ("the file changed by 352
# bytes, expected 357") and refuse, and the same bug is written out silently
# with this branch removed. It exists for the SIXTH edit somebody adds.
delta = len(src) - len(orig)
if delta != expected_delta:
    problems.append("the file changed by %d bytes, expected %d — something else moved"
                    % (delta, expected_delta))
# Cheap structural sanity: brace/bracket balance is unchanged.
for ch in "{}[]":
    if src.count(ch) != orig.count(ch):
        problems.append("the count of %r changed (%d -> %d)"
                        % (ch, orig.count(ch), src.count(ch)))

if problems:
    print("\nERROR: post-edit verification failed. NOTHING WRITTEN.", file=sys.stderr)
    for p in problems:
        print("  - " + p, file=sys.stderr)
    raise SystemExit(2)

for a in actions:
    print("  " + a)

if src == orig:
    print("  (already fully applied — nothing to write)")
    raise SystemExit(0)

if dry:
    print("  --dry-run: all checks pass, NOTHING WRITTEN (%+d bytes)" % delta)
    raise SystemExit(0)

open(path, "w", encoding="utf-8").write(src)
print("  written (%+d bytes)" % delta)
PYBLOCK

if [[ $rc -ne 0 ]]; then
  if [[ "$DRY_RUN" == "no" ]]; then
    echo
    echo "The config was NOT modified. The backup at ${BACKUP} is identical to it." >&2
  fi
  exit "$rc"
fi

if [[ "$DRY_RUN" == "yes" ]]; then
  echo
  echo "--dry-run: nothing written, no rebuild. Re-run without --dry-run under sudo."
  exit 0
fi

# ── rebuild ──────────────────────────────────────────────────────────────────
LOG="$(mktemp -t nixos-option-renames-XXXXXX.log)"
echo
echo "rebuilding (log: ${LOG}) ..."
rc=0
nixos-rebuild switch 2>&1 | tee "$LOG" || rc=$?
if [[ $rc -ne 0 ]]; then
  echo
  echo "🔴 nixos-rebuild FAILED (rc=${rc}). The system is unchanged; the SOURCE is not." >&2
  echo "   Restore it and re-run the rebuild:" >&2
  echo "       sudo cp -a ${BACKUP} ${CFG}" >&2
  echo "       sudo nixos-rebuild switch" >&2
  exit "$rc"
fi

# ── verify ───────────────────────────────────────────────────────────────────
# Every check below states the EXPECTED VALUE, so a reader can tell a pass from
# a thing that merely printed. "it worked" is not a verification.
echo
echo "════════════════════════════════════════════════════════════════════════"
echo "VERIFY"
echo "════════════════════════════════════════════════════════════════════════"

fail=0
ok()   { printf '  ok   %s\n' "$*"; }
bad()  { printf '  FAIL %s\n' "$*"; fail=$((fail + 1)); }

# 1. exactly ONE evaluation warning, and it is the wineWowPackages one.
#    re-run yourself with:  sudo nixos-rebuild dry-build 2>&1 | grep '^evaluation warning:'
warn_lines="$(grep '^evaluation warning:' "$LOG" || true)"
warn_count="$(printf '%s' "$warn_lines" | grep -c . || true)"
if [[ "$warn_count" == "1" ]]; then
  if printf '%s' "$warn_lines" | grep -q 'wineWowPackages'; then
    ok "1 evaluation warning remains and it is wineWowPackages (expected: exactly 1)"
  else
    bad "1 warning remains but it is NOT wineWowPackages: ${warn_lines}"
  fi
else
  bad "expected exactly 1 evaluation warning, got ${warn_count}:"
  printf '%s\n' "$warn_lines" | sed 's/^/         /'
fi

# 2. dnsmasq still answers for the LAN name.
#    expected:  192.168.50.250
DIG_LAN='dig +short @127.0.0.1 workbench.lan'
# 3. the docker.io pin survived the move into settings.server.
#    expected TTL: TENS of seconds. ~42,000,000 means the LAN router's 487-day
#    record is being served again -> the pin did NOT survive -> ROLL BACK.
DIG_DOCKER='dig @127.0.0.1 registry-1.docker.io A +noall +answer'
if command -v dig >/dev/null 2>&1; then
  lan="$(dig +short @127.0.0.1 workbench.lan 2>/dev/null | head -1 || true)"
  if [[ "$lan" == "192.168.50.250" ]]; then
    ok "workbench.lan -> ${lan}   (expected: 192.168.50.250)"
  elif [[ -n "$lan" ]]; then
    bad "workbench.lan -> ${lan}, expected 192.168.50.250"
  else
    bad "workbench.lan resolved to NOTHING — the settings.address entries did not survive"
  fi

  ttl="$(dig @127.0.0.1 registry-1.docker.io A +noall +answer 2>/dev/null \
         | awk '$4=="A"{print $2; exit}' || true)"
  if [[ -z "$ttl" ]]; then
    bad "no A record for registry-1.docker.io — resolver is not answering"
  elif [[ ! "$ttl" =~ ^[0-9]+$ ]]; then
    # 🔴 One `bad` per PROBLEM. Calling it again for a continuation line would
    # inflate the failure COUNT, and that count is what the exit status and the
    # closing summary are built from.
    bad "could not parse a TTL out of dig's answer (got '${ttl}') — run it yourself:"
    echo "         ${DIG_DOCKER}"
  elif [[ "$ttl" -lt 1000 ]]; then
    ok "registry-1.docker.io TTL=${ttl}s (expected: TENS of seconds — the /docker.io/ pin held)"
  else
    bad "registry-1.docker.io TTL=${ttl}s — that is the LAN router's ~42,000,000s record."
    echo "       THE /docker.io/ PIN DID NOT SURVIVE THE MOVE, and ~half of all docker"
    echo "       pulls will now fail TLS verification. Roll back:"
    echo "           sudo nixos-rebuild switch --rollback"
  fi
else
  echo "  ---- dig is not on PATH; run these two yourself, as zach:"
  echo "         ${DIG_LAN}"
  echo "           expected: 192.168.50.250"
  echo "         ${DIG_DOCKER}"
  echo "           expected: a TTL in the TENS of seconds. ~42,000,000 means the"
  echo "           LAN router's 487-day record is back and the /docker.io/ pin did"
  echo "           NOT survive — roll back with: sudo nixos-rebuild switch --rollback"
fi

# 4. the portals.conf that patch B exists to create.
if [[ -f "$PORTALS_CONF" ]]; then
  if grep -q 'default=\*' "$PORTALS_CONF"; then
    ok "${PORTALS_CONF} contains default=*"
  else
    bad "${PORTALS_CONF} exists but has no 'default=*':"
    sed 's/^/         /' "$PORTALS_CONF"
  fi
else
  bad "${PORTALS_CONF} was not created — patch B did not take effect"
fi

echo
echo "  ---- run these two AS ZACH (they are USER units; root cannot see them) ----"
echo "  systemctl --user list-unit-files | grep -E 'tinysparql|localsearch|tracker'"
echo "    expected: tinysparql-miner-fs-3.service and localsearch-3.service present,"
echo "              and NO tracker-miner-fs-3.service / tracker-extract-3.service."
echo "    🔴 These are session units. If they are missing, log out and back in"
echo "       before concluding the rename broke them — the running session still"
echo "       holds the pre-switch user-unit generation."
echo
echo "  ---- the whole-of-system control -------------------------------------"
echo "  sudo nixos-rebuild dry-build 2>&1 | grep -c '^evaluation warning:'"
echo "    expected: 1   (was 6 before this script)"
echo
echo "  ---- rollback, if any check above did not pass -----------------------"
echo "  sudo nixos-rebuild switch --rollback"
echo "  sudo cp -a ${BACKUP} ${CFG}        # restores the source too"

echo
if [[ $fail -ne 0 ]]; then
  echo "🔴 ${fail} verification check(s) FAILED. Read them above and roll back if"
  echo "   the docker.io TTL check is among them — that one breaks docker pulls."
  exit 1
fi
echo "All automated checks passed. Still run the two USER-unit / dry-build"
echo "commands above — this script cannot see either."
