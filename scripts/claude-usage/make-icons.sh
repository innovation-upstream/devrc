#!/usr/bin/env bash
# Regenerate the extension PNG icons from icons/icon.svg.
#
# The PNGs are COMMITTED, not built at switch time: nix copies the extension
# tree verbatim and Brave loads it unpacked, so there is no build step between
# the repo and the browser. Run this after editing the SVG and `git add` all
# four files -- the flake ships only TRACKED files, so an untracked PNG is
# silently omitted from the deployed tree with no error anywhere.
#
# rsvg-convert (librsvg) rather than ImageMagick: IM delegates SVG rendering to
# whatever is installed and silently rasterizes at the wrong size or drops the
# rounded-rect when it falls back to its internal MSVG parser. librsvg is the
# renderer Firefox uses; it is deterministic here.
#
# 🔴 THE VERIFY STEP IS THE POINT, not decoration. The first version of this
# script printed `stat -c%s` for each target after calling rsvg-convert. When
# rsvg-convert failed (an illegal `--` inside the SVG's XML comment), the PNGs
# it had NOT written were still on disk from the previous run, so the script
# printed three reassuring byte counts and exited 0 having rendered nothing.
# A size is evidence a file EXISTS, never that THIS run wrote it. So: delete
# the targets first, then require each one back, and re-read its pixel
# dimensions rather than trusting the exit code.
set -euo pipefail

cd "$(dirname "$0")/extension/icons"

SIZES="16 48 128"

render() {
  rc=0
  for s in $SIZES; do
    rm -f "icon-$s.png"                       # a stale file must not be mistaken for output
    rsvg-convert -w "$s" -h "$s" icon.svg -o "icon-$s.png" || rc=1
    if [ ! -s "icon-$s.png" ]; then
      echo "  FAILED: icon-$s.png was not written" >&2
      rc=1
      continue
    fi
    # Read the dimensions back out of the PNG rather than trusting the flag we
    # passed in: a renderer that ignores -w/-h fails this, silently otherwise.
    got=$(file -b "icon-$s.png" | grep -oE '[0-9]+ x [0-9]+' | head -1)
    if [ "$got" != "$s x $s" ]; then
      echo "  FAILED: icon-$s.png is '$got', expected '$s x $s'" >&2
      rc=1
      continue
    fi
    echo "  icon-$s.png  $(stat -c%s "icon-$s.png") bytes  ($got)"
  done
  return $rc
}

if command -v rsvg-convert >/dev/null 2>&1; then
  render
else
  # NixOS: no apt/dnf. Re-exec the render inside a shell that HAS librsvg
  # rather than failing with "command not found".
  echo "rsvg-convert not on PATH - running under nix-shell -p librsvg"
  exec nix-shell -p librsvg --run "set -eu; SIZES='$SIZES'; $(declare -f render); render"
fi
