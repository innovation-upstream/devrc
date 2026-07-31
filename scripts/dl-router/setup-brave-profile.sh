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
#   * refuses to run while a browser is USING THIS user-data-dir (it rewrites
#     Preferences on exit and would clobber the edit). An instance on some
#     OTHER --user-data-dir -- headless automation on a throwaway profile, say
#     -- shares the binary but not this file, and does not count;
#   * backs up Preferences with a timestamp before touching it;
#   * lists the profile display names first, so the right directory is chosen;
#   * --list and --dry-run write nothing, so they are not gated at all.
#
# Nothing here is committed with a real path: the library root comes from
# ~/.config/dl-router/config.toml (or --root).
set -euo pipefail

DEFAULT_BRAVE_DIR="$HOME/.config/BraveSoftware/Brave-Browser"
BRAVE_DIR="${BRAVE_DIR:-$DEFAULT_BRAVE_DIR}"
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
  --dry-run         show what would change; write nothing (writes nothing by
                    construction, so it is not gated on the browser: a live
                    instance is reported as a warning instead of a refusal)

Environment:
  BRAVE_DIR                          browser user-data-dir holding the profile
  DL_ROUTER_CONFIG                   config.toml to read library_root from
  DL_ROUTER_ASSUME_BROWSER_CLOSED=1  proceed even when this script cannot
                                     determine whether anything is using this
                                     user-data-dir (no readable process table).
                                     It does NOT override a browser that WAS
                                     positively detected -- quit that one
                                     instead. Only use it with the browser
                                     genuinely closed: it rewrites Preferences
                                     on exit and would silently revert the
                                     change.
  DL_ROUTER_PROC_DIR                 procfs mount point (default /proc); the
                                     "is anything using this profile?" check
                                     reads it.

Run --list first, pick the profile whose display name is the one you download
with, then re-run with --profile.
USAGE
}

# `shift 2` with only one argument left is a fatal shift error under `set -e`,
# which surfaces as an unexplained non-zero exit rather than a usage message.
need_value() {
  if [ "$2" -lt 2 ]; then
    echo "$1 requires a value" >&2
    usage >&2
    exit 2
  fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    --list) LIST=1; shift ;;
    --profile) need_value "$1" "$#"; PROFILE="$2"; shift 2 ;;
    --root) need_value "$1" "$#"; ROOT="$2"; shift 2 ;;
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

# --- refuse while something is using THIS user-data-dir --------------------- #
# THE BUG THIS GUARD USED TO HAVE: it asked `pgrep -x brave`, i.e. "does a
# process with that BINARY NAME exist", and called that "the browser is
# running". On a host that also drives headless Brave for automation
# (Playwright/chromedp, each on its own `--user-data-dir=/tmp/...`) that is a
# false positive essentially always: the real browser is closed, nothing holds
# this profile, and the one-time setup step is simply unrunnable. The
# documented escape hatch did not help either -- it only covered "pgrep could
# not answer", and here pgrep answered, loudly and wrongly.
#
# What actually matters is not the binary name but the FILE: is any live
# process using the user-data-dir that contains the profile we are about to
# patch? Three signals, most reliable first (see the embedded checker):
#   1. a process holding an open fd under the user-data-dir -- kernel truth,
#      no parsing, and it catches non-Brave holders too;
#   2. a browser MAIN process (no --type=, i.e. not a renderer/zygote child)
#      whose --user-data-dir resolves here -- or that has no --user-data-dir at
#      all, which means it is on the default one;
#   3. SingletonLock, a "<host>-<pid>" symlink -- but only when that pid is
#      still alive. A stale lock from a crash or an unclean exit must not block
#      the script forever.
#
# Fail closed is preserved: if the checker cannot get a usable process table it
# refuses, and only DL_ROUTER_ASSUME_BROWSER_CLOSED=1 overrides THAT. It does
# not override a positively identified instance -- there is nothing to guess
# about, and the message names the pid to quit.
GUARD_RC=0
GUARD_MSG="$(
  python3 - "$BRAVE_DIR" "$PROFILE" "$DEFAULT_BRAVE_DIR" <<'PY' 2>&1
import os
import re
import sys

BUSY, UNKNOWN = 3, 4
udd_arg, profile, default_arg = sys.argv[1], sys.argv[2], sys.argv[3]
proc_root = os.environ.get("DL_ROUTER_PROC_DIR") or "/proc"


def norm(path):
    try:
        return os.path.realpath(path)
    except OSError:
        return os.path.abspath(path)


udd = norm(udd_arg)
inside = udd.rstrip("/") + "/"
udd_is_default = udd == norm(default_arg)

# Packaging calls it brave, brave-browser, brave-bin, .brave-wrapped (Nix), ...
# The name is only ever used to decide whether a process is ELIGIBLE for the
# cmdline check -- never on its own to conclude that this profile is in use.
BRAVE_NAME = re.compile(r"^\.?brave", re.IGNORECASE)


def read_text(pid, name):
    try:
        with open(os.path.join(proc_root, pid, name), "rb") as fh:
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return None


def link(pid, name):
    try:
        return os.readlink(os.path.join(proc_root, pid, name))
    except OSError:
        return ""


def first_token(cmdline):
    # Chromium REWRITES its own argv to set the process title, which collapses
    # the NUL separators into spaces -- on a live browser every process reads
    # back as one space-joined blob, not a NUL-separated vector. Handle both.
    return re.split(r"[\0 ]", cmdline, maxsplit=1)[0]


def names_of(pid, cmdline):
    out = [read_text(pid, "comm") or "", os.path.basename(link(pid, "exe"))]
    if cmdline:
        out.append(os.path.basename(first_token(cmdline)))
    return [n.strip() for n in out if n.strip()]


def flag_value(cmdline, flag):
    m = re.search(r"(?:^|[\0 ])" + re.escape(flag) + r"[= ]", cmdline)
    if not m:
        return None
    # The value ends at the next NUL (a real argv) or at the next " --flag" (an
    # argv the process rewrote into one blob).
    return re.split(r"\0| --", cmdline[m.end():], maxsplit=1)[0].strip()


def has_flag(cmdline, flag):
    return re.search(r"(?:^|[\0 ])" + re.escape(flag) + r"[= ]", cmdline) is not None


def fd_under_udd(pid):
    fd_dir = os.path.join(proc_root, pid, "fd")
    try:
        entries = os.listdir(fd_dir)
    except OSError:
        return None
    for fd in entries:
        try:
            target = os.readlink(os.path.join(fd_dir, fd))
        except OSError:
            continue
        target = target.split(" (deleted)")[0]
        if target == udd or target.startswith(inside):
            return target
    return None


def live_pids():
    try:
        found = sorted((e for e in os.listdir(proc_root) if e.isdigit()), key=int)
    except OSError:
        return None
    # A real process table always has pid 1. An empty (or simply wrong)
    # directory is not evidence that nothing is running.
    return found or None


def lock_pid():
    try:
        target = os.readlink(os.path.join(udd, "SingletonLock"))
    except OSError:
        return None
    m = re.search(r"-(\d+)$", target)
    return m.group(1) if m else None


def describe(pid, cmdline):
    names = names_of(pid, cmdline)
    return "pid %s (%s)" % (pid, names[0] if names else "unknown")


def verdict():
    """('clear'|'busy'|'unknown', detail)."""
    pids = live_pids()
    held_by = lock_pid()

    if pids is None:
        # No process table. The lock is the only signal left, and it can only
        # prove BUSY, never free.
        if held_by is not None:
            try:
                os.kill(int(held_by), 0)
                alive = True
            except ProcessLookupError:
                alive = False
            except (OSError, ValueError):
                alive = True   # cannot signal it, so cannot rule it out
            if alive:
                return "busy", ("pid %s" % held_by,
                                "holds this profile's SingletonLock")
        return "unknown", ("no readable process table at %s -- cannot tell "
                           "whether anything is using this profile" % proc_root)

    for pid in pids:
        cmdline = read_text(pid, "cmdline")
        if cmdline is None:
            continue           # gone, or not ours to inspect
        open_file = fd_under_udd(pid)
        if open_file:
            return "busy", (describe(pid, cmdline),
                            "has %s open" % open_file)
        if not any(BRAVE_NAME.match(n) for n in names_of(pid, cmdline)):
            continue
        if has_flag(cmdline, "--type"):
            continue           # a renderer/zygote/gpu child, not an instance
        value = flag_value(cmdline, "--user-data-dir")
        if value is not None:
            if not os.path.isabs(value):
                cwd = link(pid, "cwd")
                value = os.path.join(cwd, value) if cwd else value
            if norm(value) == udd:
                return "busy", (describe(pid, cmdline),
                                "was started with --user-data-dir=%s" % udd)
        elif udd_is_default:
            return "busy", (describe(pid, cmdline),
                            "has no --user-data-dir, so it is on the default "
                            "one, which is this one")

    if held_by is not None and held_by in pids and read_text(held_by, "cmdline") is None:
        # Alive, holds the lock, and opaque to us (another user, or hidepid).
        # Every other case of a live lock holder was decided above.
        return "busy", ("pid %s" % held_by,
                        "holds this profile's SingletonLock and cannot be "
                        "inspected from here")

    return "clear", None


status, detail = verdict()
if status == "clear":
    raise SystemExit(0)
if status == "busy":
    who, why = detail
    sys.stderr.write(
        "A browser is using the profile directory this script would patch:\n"
        "  blocked by:      %s\n"
        "                   %s\n"
        "  profile:         %s\n"
        "  user-data-dir:   %s\n"
        "Quit THAT instance completely and re-run -- it rewrites Preferences\n"
        "on exit, which would silently revert this change. Instances on a\n"
        "different --user-data-dir (headless automation, throwaway profiles)\n"
        "are ignored by this check and are safe to leave running.\n"
        % (who, why, profile, udd))
    raise SystemExit(BUSY)
sys.stderr.write("Could not determine whether a browser is using this profile "
                 "directory:\n  %s\n" % detail)
raise SystemExit(UNKNOWN)
PY
)" || GUARD_RC=$?

if [ -n "$GUARD_MSG" ]; then
  printf '%s\n' "$GUARD_MSG" >&2
fi
if [ "$GUARD_RC" -ne 0 ] && [ "$DRY_RUN" -eq 1 ]; then
  echo "(--dry-run writes nothing, so that is a warning, not a refusal.)" >&2
elif [ "$GUARD_RC" -eq 3 ]; then
  exit 3
elif [ "$GUARD_RC" -ne 0 ]; then
  # 4 = the check ran and could not answer. Anything else = the check itself
  # broke (no python3, a traceback, ...). Both are "no answer", and no answer
  # fails closed -- that is what DL_ROUTER_ASSUME_BROWSER_CLOSED=1 is for.
  if [ "${DL_ROUTER_ASSUME_BROWSER_CLOSED:-0}" != "1" ]; then
    echo "Refusing rather than guessing: patching Preferences under a live" >&2
    echo "browser is silently reverted on exit. Quit the browser and re-run" >&2
    echo "with DL_ROUTER_ASSUME_BROWSER_CLOSED=1 to override." >&2
    exit 3
  fi
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
