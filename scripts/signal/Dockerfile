# The Signal consumer pod — `consumer.py run`, the long-lived ingest daemon.
#
# 🔴 BUILT FROM devrc, NOT VENDORED INTO homelab-talos. Same split as
# `scripts/subsystem-store-api/Dockerfile` (read its header for the full
# argument) and as `clusters/homelab/apps/mailbox` uses for mail-receiver:
# the code lives HERE, gated by this repo's own suite (387 tests under
# `scripts/signal/tests/`), and homelab-talos holds MANIFESTS ONLY plus a
# pinned image digest. A vendored copy would pass its own tests while drifting
# from the CLI the workbench runs — the exact local/remote disagreement the
# design exists to make impossible.
#
# Build context is the REPO ROOT (so the paths below read the same as they do
# in a git URL), hence:
#     docker build -f scripts/signal/Dockerfile -t <tag> .
# `build-push.sh` next to this file does that, runs the controls, and pushes.
#
# 🔴 COPY BY NAME, NEVER `COPY . .`. devrc is a PUBLIC repo and a working tree
# routinely holds an un-gitignored scratch file; a `COPY . .` bakes whatever
# happened to be sitting there into a layer and pushes it to a registry.
# `build-push.sh` asserts the resulting /app file set EXACTLY, both directions.

FROM python:3.12-slim

# --------------------------------------------------------------------------- #
# 🔴 THE DEPENDENCIES ARE INSTALLED FROM requirements.txt, AND THAT FILE IS THE
# ONLY LIST. Do not add a second hand-written `pip install a b c` here: the two
# lists drift, and this service's failure mode for a missing dependency is not a
# crash. `run()` treats any exception out of the stream factory as a dropped
# stream and reconnects, so an absent `websocket-client` is an INFINITE
# "reconnecting" loop with zero rows ingested and nothing in the log that says
# why. `scripts/signal/tests/test_image_deps.py` pins requirements.txt against
# the modules' ACTUAL imports (derived by walking the AST, not restated), and
# `build-push.sh` imports every one of them inside the built image before it is
# allowed to push.
#
# Deliberately UNPINNED versions, like requirements.txt itself. The pin that
# matters is the image digest homelab-talos deploys — a rebuilt `0.1.0` that
# picked up a new psycopg2 is a DIFFERENT digest and therefore a different
# deploy decision, which is the property we actually want.
# --------------------------------------------------------------------------- #
COPY scripts/signal/requirements.txt /app/scripts/signal/requirements.txt
RUN pip install --no-cache-dir -r /app/scripts/signal/requirements.txt

# Every .py under scripts/signal/ that is not a test. Named individually so a
# stray file cannot ride along. If you add a module here, add it to this list —
# test_image_deps.py::test_dockerfile_copies_every_runtime_module fails
# otherwise, because "the image silently lacks the new module" is a runtime
# ImportError in a reconnect loop, not a build error.
COPY scripts/signal/consumer.py    /app/scripts/signal/
COPY scripts/signal/_signal_db.py  /app/scripts/signal/
COPY scripts/signal/_minio.py      /app/scripts/signal/
COPY scripts/signal/clawgate.py    /app/scripts/signal/
COPY scripts/signal/_mentions.py   /app/scripts/signal/

# --------------------------------------------------------------------------- #
# 🔴 SIGNAL_PG_DIRECT=1 IS A PROPERTY OF THE IMAGE, NOT A DEPLOYMENT CHOICE.
# `_signal_db._direct_target()` returns None unless SIGNAL_PG_HOST or
# SIGNAL_PG_DIRECT is set, and None means "bridge with `kubectl port-forward`" —
# the off-cluster default that the workbench CLI uses. There is no kubectl in
# this image and there must not be one, so without this the pod would
# `FileNotFoundError` on connect. `_minio.MinioSignal` has the identical
# fork keyed on MINIO_ARCHIVE_ENDPOINT, which the Deployment sets (an endpoint
# is a cluster fact, not an image fact, so that one is NOT defaulted here).
#
# PYTHONUNBUFFERED=1: without it a crashed pod's last words sit in a 4 KiB
# stdio buffer that is never flushed, and `kubectl logs` shows nothing.
# HOME=/home/nonroot: a numeric-UID container has no passwd entry, and any
# `Path.home()` on an import path would raise before anything could say so.
# --------------------------------------------------------------------------- #
ENV HOME=/home/nonroot \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SIGNAL_PG_DIRECT=1

RUN mkdir -p /home/nonroot && chown -R 65532:65532 /home/nonroot

# 65532 matches the subsystem-store-api and clawgate precedents. This pod holds
# no volume, so there is no fsGroup story to get right — it only needs to not
# be root.
USER 65532:65532

WORKDIR /app/scripts/signal

# `run` is the only subcommand a Deployment should ever take. Every other
# subcommand (draft/approve/send/reconcile) is operator-driven and runs from the
# workbench CLI, where the approval token lives — `_signal_db.APPROVAL_TOKEN_ENV`
# is deliberately ABSENT from this pod's environment.
CMD ["python3", "/app/scripts/signal/consumer.py", "run"]
