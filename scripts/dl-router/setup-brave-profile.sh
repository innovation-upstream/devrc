#!/usr/bin/env bash
# setup-brave-profile.sh — one-time Brave profile change for dl-router.
#
# The extension routes a download by returning "<dir>/<name>" from
# downloads.onDeterminingFilename. Chrome resolves that RELATIVE TO THE DOWNLOAD
# ROOT and will not let it escape — no "..", no absolute paths. So the profile's
# download directory has to BE the library root, and the Save-As prompt has to be
# off, or every download stops at a picker before the extension ever sees it.
#
# This script sets, for ONE profile:
#     download.default_directory  = <library root>
#     savefile.default_directory  = <library root>
#     download.prompt_for_download = false
#
# Safety:
#   * refuses to run while Brave is running (Brave rewrites Preferences on exit
#     and would clobber the edit);
#   * backs up Preferences with a timestamp before touching it;
#   * lists the profile display names first, so the right directory is chosen;
#   * --dry-run prints the resulting diff without writing.
#
# Nothing here is committed with a real path: the library root comes from
# ~/.config/dl-router/config.toml (or --root).
set -euo pipefail

BRAVE_DIR="${BRAVE_DIR:-$HOME/.config/BraveSoftware/Brave-Browser}"
CONFIG="${DL_ROUTER_CONFIG:-$HOME/.config/dl-router/config.toml}"
PROFILE=""
ROOT=""
DRY_RUN=0

usage() {
  cat <<'USAGE'
usage: setup-brave-profile.sh [--list] [--profile <dir>] [--root <path>] [--dry-run]

  --list            show each profile directory and its display name, then exit
  --profile <dir>   profile DIRECTORY name ("Default", "Profile 2", ...)
  --root <path>     library root (default: library_root from config.toml)
  --dry-run         show what would change; write nothing

Run --list first, pick the profile whose display name is the one you download
with, then re-run with --profile.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --list) LIST=1; shift ;;
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --root) ROOT="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ ! -d "$BRAVE_DIR" ]; then
  echo "Brave profile directory not found: $BRAVE_DIR" >&2
  exit 1
fi

# --- list profiles --------------------------------------------------------- #
if [ "${LIST:-0}" = "1" ]; then
  python3 - "$BRAVE_DIR" <<'PY'
import json, sys, pathlib
brave = pathlib.Path(sys.argv[1])
state = brave / "Local State"
names = {}
if state.exists():
    try:
        info = json.loads(state.read_text(encoding="utf-8"))
        names = {k: (v or {}).get("name", "")
                 for k, v in (info.get("profile", {}).get("info_cache", {}) or {}).items()}
    except (ValueError, OSError) as exc:
        print(f"warning: cannot read Local State ({exc})", file=sys.stderr)
rows = []
for child in sorted(brave.iterdir()):
    if not (child / "Preferences").exists():
        continue
    prefs = {}
    try:
        prefs = json.loads((child / "Preferences").read_text(encoding="utf-8"))
    except (ValueError, OSError):
        pass
    dl = (prefs.get("download") or {})
    rows.append((child.name, names.get(child.name, "?"),
                 dl.get("default_directory", ""),
                 dl.get("prompt_for_download", None)))
width = max((len(r[0]) for r in rows), default=8)
print(f"{'PROFILE DIR'.ljust(width)}  DISPLAY NAME")
for name, display, dl_dir, prompt in rows:
    print(f"{name.ljust(width)}  {display}")
    print(f"{' '.ljust(width)}    download.default_directory  = {dl_dir or '(unset)'}")
    print(f"{' '.ljust(width)}    download.prompt_for_download = {prompt}")
PY
  exit 0
fi

# --- resolve the library root ---------------------------------------------- #
if [ -z "$ROOT" ]; then
  ROOT="$(python3 - "$CONFIG" <<'PY'
import sys, pathlib, tomllib
p = pathlib.Path(sys.argv[1])
if not p.exists():
    print("", end="")
    raise SystemExit(0)
try:
    data = tomllib.load(open(p, "rb"))
except Exception:
    print("", end="")
    raise SystemExit(0)
print(str(data.get("library_root", "")).strip(), end="")
PY
)"
fi

if [ -z "$ROOT" ]; then
  echo "no library root: pass --root <path> or set library_root in $CONFIG" >&2
  exit 2
fi
if [ ! -d "$ROOT" ]; then
  echo "library root is not a directory: $ROOT" >&2
  exit 2
fi
if [ -z "$PROFILE" ]; then
  echo "no profile selected. Run with --list, then pass --profile '<dir>'." >&2
  exit 2
fi

PREFS="$BRAVE_DIR/$PROFILE/Preferences"
if [ ! -f "$PREFS" ]; then
  echo "no Preferences file for profile '$PROFILE' ($PREFS)" >&2
  exit 2
fi

# --- refuse while Brave is running ----------------------------------------- #
# Match the real binary paths, not a bare name: `pgrep -f brave` would also
# match this script's own command line.
if pgrep -x brave >/dev/null 2>&1 || pgrep -x brave-browser >/dev/null 2>&1 \
   || pgrep -x "Brave-Browser" >/dev/null 2>&1; then
  echo "Brave is running. Quit it completely (it rewrites Preferences on exit," >&2
  echo "which would silently revert this change), then re-run." >&2
  exit 3
fi

# --- back up + patch -------------------------------------------------------- #
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$PREFS.dl-router-backup-$STAMP"
if [ "$DRY_RUN" -eq 0 ]; then
  cp -p "$PREFS" "$BACKUP"
  echo "backup: $BACKUP"
fi

python3 - "$PREFS" "$ROOT" "$DRY_RUN" <<'PY'
import json, sys, pathlib, os, tempfile
prefs_path, root, dry = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3] == "1"
data = json.loads(prefs_path.read_text(encoding="utf-8"))
dl = data.setdefault("download", {})
sf = data.setdefault("savefile", {})
before = {"download.default_directory": dl.get("default_directory"),
          "savefile.default_directory": sf.get("default_directory"),
          "download.prompt_for_download": dl.get("prompt_for_download")}
dl["default_directory"] = root
dl["prompt_for_download"] = False
sf["default_directory"] = root
after = {"download.default_directory": root,
         "savefile.default_directory": root,
         "download.prompt_for_download": False}
for key in after:
    mark = " " if before[key] == after[key] else "*"
    print(f"{mark} {key}: {before[key]!r} -> {after[key]!r}")
if dry:
    print("dry run — nothing written")
    raise SystemExit(0)
# Atomic replace so a crash mid-write cannot leave a truncated Preferences.
fd, tmp = tempfile.mkstemp(dir=str(prefs_path.parent), prefix=".prefs-")
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    json.dump(data, fh, separators=(",", ":"), ensure_ascii=False)
os.replace(tmp, prefs_path)
print(f"written: {prefs_path}")
PY

if [ "$DRY_RUN" -eq 0 ]; then
  cat <<EOF

Done. Next:
  1. Start Brave and open brave://extensions in the '$PROFILE' profile.
  2. Enable Developer mode -> Load unpacked -> select:
       $(cd "$(dirname "$0")" && pwd)/extension
  3. Open the extension's Options page, paste the token from \`dl-route token\`,
     confirm the port, and tick "Enable routing in this profile".
  4. Verify: \`dl-route status\` shows the sidecar up, then download one file.

To revert: cp "$BACKUP" "$PREFS"   (with Brave closed)
EOF
fi
