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
docker run --rm "$IMAGE" python3 -c \
  'import sys; sys.path.insert(0, "/app/scripts/lib"); import subsystem_recall as r; print("==> control: subsystem_recall imported,", len(r.RECALL_MODES), "modes")'

if [[ $PUSH -eq 0 ]]; then
  echo "==> --no-push: built only. NOTHING was pushed."
  exit 0
fi

docker push "$IMAGE"
docker inspect --format '{{index .RepoDigests 0}}' "$IMAGE" 2>/dev/null || true
echo "==> pushed $IMAGE"
