# The subsystem-store API pod. Phase 1.
#
# 🔴 BUILT FROM devrc, NOT FROM homelab-talos, AND THAT IS THE WHOLE POINT.
# The proposal's premise is "the pod is the existing code, unmodified"
# (claudedocs/proposal-subsystem-store-homelab.md §1). `subsystem_recall.py`
# and its two dependencies live HERE, are gated by this repo's own suite
# (`scripts/tests/test_subsystem_{recall,touch,resolver}.py`, thousands of
# assertions), and are what the workbench CLI runs. Vendoring a copy into
# homelab-talos would create a second tree that passes its own tests while
# drifting from the CLI — which is exactly the disagreement between local and
# remote output this design exists to make impossible.
#
# homelab-talos therefore holds MANIFESTS ONLY, plus a pinned image tag — the
# same split `clusters/homelab/apps/mailbox` already uses for mail-receiver.
#
# Build context is the REPO ROOT (the modules are under scripts/lib), so:
#     docker build -f scripts/subsystem-store-api/Dockerfile -t <tag> .
# `build-push.sh` next to this file does that and nothing else.
#
# 🔴 THE IMAGE CONTAINS CODE, NEVER STORE CONTENT. Every .py file is copied BY
# NAME — no `COPY . .`, so a stray entry file in a working tree cannot be baked
# into a layer and pushed to a registry. The store arrives at runtime, on a PVC,
# via `seed.sh`.
#
# 🔴 DO NOT TRIM A `COPY` BY EYE, AND DO NOT COUNT THEM HERE. This comment used
# to say "only four .py files" and was wrong; a first correction replaced the
# number with a different number, which is the same artefact one module later.
# There is no count in this file any more, on purpose. The authority is
# `test_subsystem_store_api.py::test_the_image_copies_every_module_it_needs`,
# which computes the TRANSITIVE closure of local `scripts/lib` imports from
# `server.py`'s entrypoint and asserts BOTH this file and the (allowlist-shaped)
# `Dockerfile.dockerignore` cover it. Add an import, run that test.

# 🔴 THE READER MODULES NOW COME FROM THE PINNED `cairn` PACKAGE, NOT FROM
# `scripts/lib/`. devrc deleted its forked copies when it consolidated onto the
# flake pin, so there is nothing at `scripts/lib/subsystem_recall.py` to COPY.
# `build-push.sh` STAGES the pinned `lib/` into `.cairn-lib/` beside this file
# (a gitignored build-context directory, written by name, never `cp -r` of a
# working tree) and the COPY below takes it from there. The premise in this
# file's header is unchanged and is the reason it is done this way: the pod must
# run the SAME reader the CLI runs, and after the consolidation that reader is
# the pinned one. A copy kept in `scripts/lib/` purely for the image would be
# the second drifting tree this design exists to prevent.
#
# 🔴 `subsystem_touch.py` AND `git_mainline.py` ARE NO LONGER COPIED, and that
# is a consequence rather than a trim: devrc's reader used to import its shared
# vocabulary `from subsystem_touch`, while the pinned reader imports it from
# `entry_shape`. The transitive closure from `server.py` is re-derived by
# `test_subsystem_store_api.py::test_the_image_copies_every_module_it_needs`
# across BOTH source trees — do not restore either by eye.
FROM python:3.12-slim

# Stdlib only: the reader imports argparse/difflib/json/re/sys/pathlib and its
# own siblings, and shells out to nothing. There is no requirements.txt to
# install and there must not be one.
#
# devrc's own module — the seam that puts the pinned lib on `sys.path`.
COPY scripts/lib/cairn_pin.py /app/scripts/lib/
# The pinned reader and its siblings, staged by build-push.sh. Named
# individually for the same reason every other COPY here is: no `COPY . .`.
COPY scripts/subsystem-store-api/.cairn-lib/subsystem_recall.py     /app/cairn-lib/
COPY scripts/subsystem-store-api/.cairn-lib/subsystem_read_store.py /app/cairn-lib/
COPY scripts/subsystem-store-api/.cairn-lib/subsystem_resolver.py   /app/cairn-lib/
COPY scripts/subsystem-store-api/.cairn-lib/entry_shape.py          /app/cairn-lib/
COPY scripts/subsystem-store-api/.cairn-lib/host_identity.py        /app/cairn-lib/
COPY scripts/subsystem-store-api/server.py /app/scripts/subsystem-store-api/

# `subsystem_touch` evaluates `DEFAULT_STORE_ROOT = Path.home() / ".claude" / …`
# at IMPORT time, and `Path.home()` raises when it can resolve neither $HOME nor
# a passwd entry — which is the ordinary state of a numeric-UID container. The
# value is never used here (every call passes `--store`), but the import would
# fail before anything could say so.
# 🔴 `CAIRN_LIB` IS RESOLUTION ROUTE 1 AND THE ONLY ONE AVAILABLE HERE. The
# container carries no `cairn` on PATH and no home-manager generation, so
# `cairn_pin`'s PATH-derived route cannot answer; without this the server would
# refuse at import with the pin's own "not found" message. That refusal is the
# correct behaviour — it is why there is no silent fallback — and this is how the
# pod satisfies it.
ENV CAIRN_LIB=/app/cairn-lib \
    HOME=/home/nonroot \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SUBSYSTEM_STORE_ROOT=/data \
    SUBSYSTEM_STORE_PORT=8102 \
    SUBSYSTEM_STORE_TOKEN_FILE=/run/secrets/subsystem-store/token

RUN mkdir -p /home/nonroot /data && chown -R 65532:65532 /home/nonroot /data

# 65532 matches the clawgate precedent's runAsUser, so the same fsGroup story
# applies to the PVC.
USER 65532:65532

EXPOSE 8102

CMD ["python3", "/app/scripts/subsystem-store-api/server.py"]
