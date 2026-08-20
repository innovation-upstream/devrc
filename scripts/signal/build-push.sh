#!/usr/bin/env bash
# Build + push the Signal consumer image to Harbor.
#
# The image is built from THIS repo (see the Dockerfile's header for why), with
# the repo root as the build context.
#
# 🔴 THE TAG IS AN ARGUMENT AND HAS NO DEFAULT. A `:latest` default is how a
# mutable tag gets clobbered by a concurrent build and a pod silently restarts
# on somebody else's code. homelab-talos pins an immutable DIGEST.
#
#   build-push.sh 0.1.0            # build, run the controls, push, print the digest
#   build-push.sh 0.1.0 --no-push  # build + controls only, push nothing
#
# Every control below refuses the PUSH, not the build: a broken image that was
# never pushed is a local problem; one that reached the registry is a deploy.

set -euo pipefail

VERSION="${1:?usage: build-push.sh <version> [--no-push]}"
shift || true
PUSH=1
[[ "${1:-}" == "--no-push" ]] && PUSH=0

REGISTRY="harbor.homelab.lan"
IMAGE="$REGISTRY/library/signal-consumer:$VERSION"
# CDPATH= : a set CDPATH makes `cd` ECHO its destination, which would be
# captured into ROOT alongside the real path. Measured in the subsystem-store
# precedent, not theorised.
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "==> building $IMAGE from $ROOT"
docker build -f "$ROOT/scripts/signal/Dockerfile" -t "$IMAGE" "$ROOT"

# --------------------------------------------------------------------------- #
# CONTROL 1 — THE POSITIVE ONE, AND THE REASON THIS SCRIPT EXISTS.
#
# 🔴 A MISSING DEPENDENCY IN THIS SERVICE IS NOT A CRASH, IT IS A ZOMBIE.
# `consumer.run()` treats any exception out of the stream factory as a dropped
# stream and reconnects with backoff — so an image built without
# `websocket-client` comes up healthy, logs "reconnecting" forever, ingests zero
# rows, and reports no error anywhere. Hours were lost to exactly that. The
# failure must therefore happen HERE, at build time, loudly.
#
# The list is DERIVED, never restated: the script walks the AST of the modules
# that are actually in the image and imports every non-stdlib, non-local name it
# finds, then imports the local modules themselves. A dependency added to a new
# `import` line is covered without anyone remembering to update this file.
# (requirements.txt is pinned against the same derivation, from the other
# direction, by scripts/signal/tests/test_image_deps.py.)
# --------------------------------------------------------------------------- #
# 🔴 `-i` IS LOad-BEARING, NOT STYLE. Without it `docker run` does not attach
# stdin, `python3 -` reads EOF, executes an EMPTY program, and exits 0. The
# control then "passes" having imported nothing — measured here on the first
# run of this script, which is exactly why it prints what it imported and why
# the caller below asserts that the output ARRIVED.
echo "==> control 1/3: importing every derived runtime dependency INSIDE the image"
control_out=$(docker run --rm -i --entrypoint python3 "$IMAGE" - <<'PYCONTROL'
import ast, importlib, pathlib, sys

APP = pathlib.Path("/app/scripts/signal")
sys.path.insert(0, str(APP))

modules = sorted(p for p in APP.glob("*.py"))
if len(modules) < 4:
    raise SystemExit(
        f"CONTROL BROKEN: found {len(modules)} module(s) under {APP}; the AST walk "
        "would have nothing to derive from and this control would pass vacuously")

local = {p.stem for p in modules}
third = set()
for path in modules:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module] if (node.level == 0 and node.module) else []
        else:
            continue
        for name in names:
            root = name.split(".")[0]
            if root not in sys.stdlib_module_names and root not in local:
                third.add(root)

if not third:
    raise SystemExit(
        "CONTROL BROKEN: the AST walk derived ZERO third-party imports. That is "
        "not possible for these modules (psycopg2/minio/requests/websocket are "
        "all imported) — the walker is broken, so a green here would mean nothing")

print(f"    derived {len(third)} third-party import(s) from {len(modules)} module(s)")
for name in sorted(third):
    mod = importlib.import_module(name)
    version = getattr(mod, "__version__", None) or getattr(mod, "version", "")
    print(f"    OK  dependency  {name:<12} {version}")

for name in sorted(local):
    importlib.import_module(name)
    print(f"    OK  own module  {name}")

print(f"CONTROL-1-PASSED: {len(third)} dependencies + {len(local)} own modules imported")
PYCONTROL
)
printf '%s\n' "$control_out"

# The output-arrived assertion. `set -e` already catches a non-zero exit, but the
# failure this guards is the one that exits ZERO: a control that ran no code at
# all. Anchored on a token the script only prints after every import succeeded.
if ! printf '%s' "$control_out" | grep -q '^CONTROL-1-PASSED:'; then
  echo "build-push: REFUSING TO PUSH — control 1 produced no completion line." >&2
  echo "            It exited 0 having imported NOTHING (an unattached stdin does" >&2
  echo "            exactly this). A silent pass here is the failure mode the whole" >&2
  echo "            control exists to catch, so it is treated as one." >&2
  exit 1
fi
echo "==> control 1/3 PASSED"

# --------------------------------------------------------------------------- #
# CONTROL 2 — THE NEGATIVE ONE. devrc is a PUBLIC repo and a working tree
# routinely holds scratch files, `.envrc`, a half-written secret. The Dockerfile
# copies by name, but "the Dockerfile is correct" is a claim about the
# Dockerfile; this is the measurement.
#
# The assertion is EXACT SET EQUALITY, not a blocklist of forbidden names: a
# blocklist can only catch the leaks somebody imagined. Anything under /app that
# is not one of the five files we put there fails the push.
#
# Note the scope this control does NOT have: it walks /app only, so it says
# nothing about the base image's own contents. That is deliberate — the base is
# `python:3.12-slim` by digest-of-tag and its contents are upstream's problem,
# not a leak surface from this working tree.
# --------------------------------------------------------------------------- #
echo "==> control 2/3: /app must hold EXACTLY the files the Dockerfile names"
# 🔴 THE EXPECTED LIST IS DERIVED FROM THE DOCKERFILE, NOT RETYPED HERE. A third
# hand-written copy of the file set would be one more ledger to drift, and the
# chain that makes this meaningful is already closed WITHOUT it:
#
#   scripts/signal/*.py  ==  Dockerfile COPY list  ==  Dockerfile.dockerignore
#        pinned three ways by tests/test_image_deps.py, on every gate run
#   Dockerfile COPY list  ==  what is actually in the built image
#        pinned right here, on every build
#
# so "the directory equals the image" follows, and no statement of it is
# written twice. The leak this catches is content that entered the image
# WITHOUT a COPY naming it — a base-image surprise under /app, a cache artifact,
# or a `COPY . .` added in a hurry (which names a directory, so every file it
# drags in shows up as unexpected here).
#
# LC_ALL=C on BOTH sides. The host sorts under the caller's locale and the
# container under its own; en_US folds the leading underscore of `_minio.py`
# differently from C, so two identical file sets compared as a diff of
# differently-sorted lists reports four spurious changes. Measured, not
# theorised — the first run of this script did exactly that.
EXPECTED_FILES=$(
  awk '/^COPY[[:space:]]/ {
         src = $2; dst = $3
         n = split(src, parts, "/")
         # A COPY whose destination ends in "/" drops the source basename there.
         printf "%s%s\n", dst, (dst ~ /\/$/ ? parts[n] : "")
       }' "$ROOT/scripts/signal/Dockerfile" | LC_ALL=C sort
)
if [[ $(printf '%s\n' "$EXPECTED_FILES" | grep -c .) -lt 5 ]]; then
  echo "build-push: CONTROL BROKEN — parsed fewer than 5 COPY destinations out of" >&2
  echo "            the Dockerfile. The expected list is derived from it, so an" >&2
  echo "            empty or short parse would compare the image against nothing." >&2
  exit 1
fi
ACTUAL_FILES=$(docker run --rm --entrypoint sh "$IMAGE" -c 'find /app -type f | LC_ALL=C sort')

# 🔴 The comparator gets its own controls FIRST. A `diff` that always reports
# "same" would make the check below pass with a leaked .envrc in the image, and
# a `diff` that always reports "different" would fail an honest build — both are
# a broken instrument, and neither is visible from the result alone. So: prove
# it says SAME for identical input, and DIFFERENT for input perturbed by one
# line, before its verdict on the real thing is worth reading.
if ! diff <(printf '%s\n' "$EXPECTED_FILES") <(printf '%s\n' "$EXPECTED_FILES") >/dev/null; then
  echo "build-push: CONTROL BROKEN — the comparator reports a difference between a list and itself." >&2
  exit 1
fi
if diff <(printf '%s\n' "$EXPECTED_FILES") <(printf '%s\n/app/LEAKED\n' "$EXPECTED_FILES") >/dev/null; then
  echo "build-push: CONTROL BROKEN — the comparator did NOT notice an extra file. It cannot see a leak." >&2
  exit 1
fi
echo "    comparator controls OK (identical => same, one extra line => different)"

if [[ -z "$ACTUAL_FILES" ]]; then
  echo "build-push: REFUSING TO PUSH — /app is EMPTY in the image. A zero-leak" >&2
  echo "            result from an empty tree is the vacuous pass, not the all-clear." >&2
  exit 1
fi

if ! diff <(printf '%s\n' "$EXPECTED_FILES") <(printf '%s\n' "$ACTUAL_FILES"); then
  echo "build-push: REFUSING TO PUSH — /app does not match the Dockerfile's COPY list." >&2
  echo "            '<' = expected and missing, '>' = present and UNINTENDED (a leak)." >&2
  exit 1
fi
echo "==> control 2/3 PASSED: /app holds exactly $(printf '%s\n' "$ACTUAL_FILES" | wc -l) expected files, nothing else"

# --------------------------------------------------------------------------- #
# CONTROL 3 — the image's CMD points at the CLI, and that CLI is the one this
# service is supposed to carry.
#
# Two halves, because either alone is walkable: the CMD is read STRUCTURALLY out
# of the image config (a `docker run` would override it, so running the CLI
# proves nothing about what the pod will execute), and the subcommand set is
# compared as an EXACT SET against argparse's own `{a,b,c}` choices line — not
# by grepping for the word "run", which the help TEXT spells in a dozen places
# and which would pass with the subcommand deleted.
#
# It is NOT a claim that ingest works. Nothing in this script connects to
# Signal, Postgres or MinIO, and no message has been ingested by anything it
# builds.
# --------------------------------------------------------------------------- #
echo "==> control 3/3: CMD points at the CLI, and the CLI has the expected subcommands"
cmd=$(docker inspect --format '{{join .Config.Cmd " "}}' "$IMAGE")
want_cmd="python3 /app/scripts/signal/consumer.py run"
if [[ "$cmd" != "$want_cmd" ]]; then
  echo "build-push: REFUSING TO PUSH — image CMD is '$cmd', expected '$want_cmd'." >&2
  exit 1
fi

# argparse renders the subparser choices as a single `{a,b,c,…}` token. Pulling
# that token out and comparing SETS means a renamed or dropped subcommand fails
# here, and an ADDED one fails here too — which is the point: a new operator
# subcommand is a decision about what this pod can be told to do.
choices=$(docker run --rm --entrypoint python3 "$IMAGE" \
            /app/scripts/signal/consumer.py --help \
          | tr ' ' '\n' | grep -o '{[a-z,]*}' | head -1 | tr -d '{}' | tr ',' '\n' | sort | tr '\n' ' ')
# `health` added 2026-08-18 with the liveness heartbeat (#540). Acknowledged
# deliberately, as this control demands: it is READ-ONLY — it reads the local
# heartbeat file (or, with --from-db, the health row) and exits 0/1. It
# transmits nothing, writes nothing, and is what the k8s liveness probe runs.
#
# `mute`/`unmute`/`muted` added 2026-08-19 with the group mute list. Acknowledged
# deliberately, as this control demands. They write to ONE table
# (`signal.excluded_groups`) that nothing else reads except the read predicate,
# transmit nothing, and — the property that made this the chosen design — DELETE
# nothing: `unmute` restores the conversation in full, because the messages were
# never removed. `muted` is read-only.
want_choices="approve conversations draft drafts health mute muted reconcile run search send unmute "
if [[ "$choices" != "$want_choices" ]]; then
  echo "build-push: REFUSING TO PUSH — subcommand set is '$choices'," >&2
  echo "            expected '$want_choices'. Empty means the parse found no {…}" >&2
  echo "            token at all, i.e. this control observed nothing." >&2
  exit 1
fi
echo "==> control 3/3 PASSED: CMD='$cmd'; subcommands = $choices"

if [[ $PUSH -eq 0 ]]; then
  echo "==> --no-push: built and controlled only. NOTHING was pushed."
  exit 0
fi

docker push "$IMAGE"
echo "==> pushed $IMAGE"
echo "==> DIGEST (this is what homelab-talos pins, NOT the tag):"
docker inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$IMAGE"
