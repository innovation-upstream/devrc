#!/usr/bin/env bash
# Build + push the subsystem-store API image to Harbor. Phase 1.
#
# The image is built from THIS repo (see the Dockerfile's header for why), with
# the repo root as the build context — the modules live under scripts/lib.
#
# 🔴 THE TAG IS AN ARGUMENT AND HAS NO DEFAULT. A `:latest` default is how a
# mutable tag gets clobbered by a concurrent build and a pod silently restarts
# on somebody else's code. homelab-talos pins an immutable version.
#
#   build-push.sh 0.1.0            # build, push, print the digest
#   build-push.sh 0.1.0 --no-push  # build only

set -euo pipefail

VERSION="${1:?usage: build-push.sh <version> [--no-push]}"
shift || true
PUSH=1
[[ "${1:-}" == "--no-push" ]] && PUSH=0

REGISTRY="harbor.homelab.lan"
IMAGE="$REGISTRY/library/subsystem-store-api:$VERSION"
# CDPATH= : a set CDPATH makes `cd` ECHO its destination, which would be
# captured into ROOT alongside the real path. Measured here, not theorised.
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"

# 🔴 STAGE THE PINNED READER INTO THE BUILD CONTEXT. The image runs the SAME
# reader the CLI runs, and since devrc consolidated onto the `cairn` flake pin
# that reader lives in `/nix/store`, which a docker build context cannot reach.
# So it is copied in BY NAME (never `cp -r` of a directory — the Dockerfile's own
# "no COPY . ." rule, one level up) into a gitignored staging dir, and the image
# points `CAIRN_LIB` at it.
#
# 🔴 THE STAGING DIR IS EMPTIED FIRST. A module dropped from the closure by a pin
# bump would otherwise linger here and keep being COPYed, which is how an image
# carries code no current source produces.
CAIRN_LIB="$(python3 "$ROOT/scripts/lib/cairn_pin.py")"
STAGE="$ROOT/scripts/subsystem-store-api/.cairn-lib"
rm -rf "$STAGE"
mkdir -p "$STAGE"
for m in subsystem_recall subsystem_read_store subsystem_resolver entry_shape host_identity; do
  src="$CAIRN_LIB/$m.py"
  [[ -f "$src" ]] || { echo "build-push: the pinned client has no $m.py at $src" >&2; exit 1; }
  cp -- "$src" "$STAGE/$m.py"
done
staged=$(ls -1 "$STAGE" | wc -l)
echo "==> staged $staged pinned module(s) from $CAIRN_LIB"
[[ "$staged" == "5" ]] || { echo "build-push: expected 5 staged modules, got $staged" >&2; exit 1; }

echo "==> building $IMAGE from $ROOT"
docker build -f "$ROOT/scripts/subsystem-store-api/Dockerfile" -t "$IMAGE" "$ROOT"

# 🔴 A CONTROL, NOT A COURTESY. The image must contain the code and NOT the
# store: this repo is public and the store is client-confidential, so a layer
# that picked up a stray entry file would be pushed to a registry. `/data` must
# be EMPTY in the image — the store arrives at runtime on a PVC.
leaked=$(docker run --rm --entrypoint sh "$IMAGE" -c 'ls -A /data | wc -l')
if [[ "$leaked" != "0" ]]; then
  echo "build-push: REFUSING TO PUSH — /data in the image is not empty ($leaked entries)." >&2
  exit 1
fi
echo "==> control: /data in the image holds $leaked files (must be 0) — OK"

# And the positive half: the code IS there and imports. A zero above from an
# image with no filesystem at all would look identical.
# 🔴 THE IMPORT CONTROL GOES THROUGH `cairn_pin`, NOT A BARE `sys.path` POKE.
# That is the mechanism the server itself uses, so this control now also proves
# `CAIRN_LIB` is set correctly in the image — the single point where a staging
# mistake would otherwise surface at pod start instead of at build time.
docker run --rm "$IMAGE" python3 -c \
  'import sys; sys.path.insert(0, "/app/scripts/lib"); import cairn_pin; print("==> control: pin resolves to", cairn_pin.ensure()); import subsystem_recall as r; print("==> control: subsystem_recall imported,", len(r.RECALL_MODES), "modes")'

if [[ $PUSH -eq 0 ]]; then
  echo "==> --no-push: built only. NOTHING was pushed."
  exit 0
fi

docker push "$IMAGE"
docker inspect --format '{{index .RepoDigests 0}}' "$IMAGE" 2>/dev/null || true
echo "==> pushed $IMAGE"
