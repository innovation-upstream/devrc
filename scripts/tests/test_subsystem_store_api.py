"""Tests for scripts/subsystem-store-api/ — the phase-1 HTTP layer, seed and verifier.

WHAT IS BEING PROTECTED
-----------------------
`claudedocs/proposal-subsystem-store-homelab.md` phase 1: build the pod, seed
`/data` from the local store, serve a READ-ONLY API cluster-internally, and prove
the remote digest is byte-identical to the local one. The local store stays
authoritative and untouched.

🔴 THESE ARE INVARIANT GUARDS, NOT REGRESSION TESTS, AND THE DISTINCTION IS
NOT COSMETIC. There is no pre-existing defect here: `server.py` did not exist
before this branch, so every test in this file is trivially red at the base ref
(the module is absent) and that red proves NOTHING — it is a collection error,
not a caught bug. `claude/RULES.md`: "a guard pinning an invariant the bug never
violated is an invariant guard: label it as one, don't count it as regression
coverage." So the meaningful evidence for this file is the MUTATION matrix in
the PR body — each guard broken on purpose, watched to fail with THAT guard's
own error, and reached by a case no earlier check rejects — plus the two
comparators below that are exercised in both directions in-band.

WHAT IS EXERCISED IN BOTH DIRECTIONS, IN-BAND
---------------------------------------------
  * `TestAuthControls` — a valid token accepted AND no-token/wrong-token/
    near-miss-token watched to be rejected. An auth layer never seen to deny is
    not known to be an auth layer.
  * `TestByteIdentityVerifier` — the phase-1 acceptance comparator run against
    identical stores (PASS) and against a store differing by ONE character
    (FAIL, naming the scope). A comparator that always says PASS is
    indistinguishable from one that works.
  * `TestSeedIsNonDestructive` — the tree hasher is shown to CHANGE when the
    source is deliberately modified, before its "unchanged" verdict is believed.

🔴 NO TEST HERE READS THE REAL STORE. `~/.claude/analyze-service-index/` is
client-confidential and not re-derivable by re-running recon, and this repo is
PUBLIC. (Was "has no off-machine backup" — false; daily age-encrypted bundles go
to MinIO. The reason no test reads the live store is confidentiality, not
fragility.) Every
fixture below is synthetic, under `tmp_path`, with names invented for this file
and pairwise-distinct fields so a renderer that surfaced the wrong section
cannot pass by coincidence.

🔴 EXPECTATIONS ARE PINNED LITERALLY, never imported from the module under test.
`UNAUTHORIZED_BODY`, the 43-character token floor, the header names and the
status strings are all spelled again here by hand. Importing them would assert
`x == x` and stay green through a rename that broke every caller.
"""

from __future__ import annotations

from collections.abc import Iterator

import ast
import errno
import hashlib
import http.client
import importlib.util
import json
from testlib import hermetic_git  # noqa: E402
from testlib import mockbin  # noqa: E402
from testlib import store_siting  # noqa: E402
import io
import os
import re
import shutil
import socket
import socketserver
import stat
import tarfile
import secrets
import subprocess
import sys
import textwrap
import threading
import time
import traceback
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# 🔴 ONE bound for every "this should already have happened" wait in this module
# — the localhost HTTP round-trips, `wait_closed`, `await_audit`. These are
# HANG-DETECTORS: they exist so a broken server fails the test instead of hanging
# the suite forever, and their value is not a correctness claim about how fast
# anything must be.
#
# 🔴 ONE DOCUMENTED EXCEPTION, named here because the sentence above was already
# falsified once by a change that did not update it: `running_subprocess`'s
# healthz RETRY probe takes a DEADLINE-RELATIVE bound, not this one. It is a
# poll inside a 20 s budget rather than a detector, so at this value a single
# blocked call would outlast the budget it enforces. Any future exception goes
# in this list or the claim above is false again — which is how two
# `wait_closed` sites kept a stale bound while the comment said otherwise.
#
# It is deliberately NOT used for the raw-socket `settimeout(...)` calls further
# down. Those are DRAIN bounds — the loop terminator for a `recv`-until-quiet
# read — so raising one adds its full value to the suite's runtime instead of
# only to the latency of a genuine failure. Same spelling, opposite meaning; a
# single shared constant across both would be wrong.
#
# Why 60 and not 15: measured 2026-08-29, the devrc Tekton gate was failing
# ~60% of runs REPO-WIDE (6 of 10 in one window, on unrelated branches) with
# `TimeoutError` out of `socket.py` — a localhost round-trip that lost the
# scheduler for >15 s while 12 pipelineruns shared the node and this suite ran
# 637 s under xdist. The test logic was never reached, so the gate reported a
# code failure for a capacity problem. 60 s absorbs a 4x scheduling delay and
# still fails a genuinely hung server well inside the task timeout.
#
# 🔴 This is the SYMPTOM fix. The cause is a 10-minute parallel suite competing
# with a saturated cluster, which belongs to Tekton capacity, not to this file.
#
# 🔴 AND 60 DID NOT HOLD — it recurred 2026-08-31, so do not read the paragraph
# above as closed. What is new is that the contention is now NAMED and can be
# reproduced ON THE DEV HOST in ~70 s; see `scripts/ci-repro/`. Two corrections
# to the framing above, both measured:
#
#   * IT IS DISK LATENCY, NOT CPU. On run `devrc-ci-86zxj` (sha 5de43017) this
#     suite's own classifier printed `MECHANISM = SERVER_BLOCKED_IN_FSYNC …
#     accept loop parked=True`. `server.py:_replace_bytes` fsyncs the file
#     (:2012) and the parent dir (_fsync_dir, :1961) INSIDE the request, before
#     the response is written, and fsync blocks in uninterruptible sleep.
#     devrc-ci is pinned to ONE node (talos-xr6-r7p). 🔴 The contention set is
#     the 7 devrc-ci runs, NOT the 12 overlapping pipelineruns: gitops-validate
#     is pinned to talos-uvh-gtj and the one auditloop run was on
#     talos-deu-s2q. 🔴 And the stalling fsync lands on NEITHER named volume —
#     not `nix-store-cache` (/nix) nor the per-run `source` PVC
#     (/workspace/source), but the step container's EPHEMERAL layer under /tmp,
#     where the gate sets no TMPDIR and mounts nothing.
#     ⚠ "give the gate CPU/memory requests" does NOT fix the latency — requests
#     govern CPU and memory, not IOPS — but it is not useless either: every run
#     is pinned to one node, so non-zero requests are the standard way to make
#     excess runs Pending instead of co-scheduled, i.e. a concurrency cap. Do
#     not read this as "requests are the wrong lever"; read it as "they cap
#     concurrency, they do not speed up fsync". `computeResources: null` is a
#     platform-wide default — EVERY taskrun in that namespace declares none,
#     at every reading; the absolute count drifts — not a devrc oversight.
#   * SEED/ORDERING IS NOT THE MECHANISM — but mind what proves that. The
#     REPRODUCER (`scripts/ci-repro/`) shows fsync latency SUFFICES: delaying a
#     single fsync past this bound reproduces the exact failure, on the
#     identical test and parametrisation the gate reported (control 8 passed /
#     4.63 s; with one stalled fsync, 1 failed). Sufficiency is not necessity —
#     what actually refutes seed/ordering is the CI classifier above, not the
#     reproducer.
#   * ⚠ THE 3 FAILURES ON THAT RUN WERE NOT ONE FLAKE. The third was
#     TestAHungRoundTripSAYSWhichSideBlocked::test_a_stall_in_the_FSYNC_region_
#     is_NAMED, which failed on its assertion ("the server never reached the stall
#     site") against `CLIENT_BOUND` (0.25) — NOT against this constant. Both
#     live in TestAHungRoundTripSAYSWhichSideBlocked; find them by NAME, never
#     by line — this comment block shifts them every time it is edited, which
#     already shipped one wrong citation.
#     So it evidences an fsync exceeding 0.25 s, and the advice below about not
#     raising HANG_TIMEOUT does not address the bound that actually failed
#     there. That 0.25/1.2 pair was the same "bound tighter than the thing it
#     discriminates" shape as commit f4a3d69b.
#     ✅ FIXED — and the pair is GONE rather than retuned. `SERVER_STALL` no
#     longer exists and nothing samples `stalled.is_set()`: the caller WAITS for
#     the stall site to be reached, and the handler is then held by an Event
#     until the report has been taken. Retuning the two numbers was the obvious
#     fix and would have been wrong — ANY fixed pair is a bet on the scheduler,
#     and this one was lost in CI while winning on every dev host. Do not
#     reintroduce a fixed stall duration here; the guard below pins that.
#
# 🔴 Do NOT raise this constant again in response to a recurrence. Read the
# per-hung-call arithmetic directly below — the bound is not the lever, and the
# next raise buys a longer outage, not a greener gate.
#
# 🔴 THE COST IS PER HUNG CALL, NOT PER SUITE — corrected after audit. The
# commit that introduced this said "4x longer to fail … still fits" the 45m gate
# task budget. That is true of ONE hung call. This module has ~208 `fetch(` and
# ~112 `await_audit(` sites, so a BROADLY hung server costs ~320x60s ≈ 5.3h
# serialised where 15 s cost ~80m — and both blow the 45m budget, which is the
# documented state where nothing is posted and the required checks stay
# `pending` forever, clearable only by a fresh push. The bound is right for the
# failure it exists to absorb (one starved round-trip); it is not a defence
# against a server that is down, and nothing here should be read as claiming so.
HANG_TIMEOUT = 60.0

# 🔴 The guard the introducing commit CLAIMED and did not write. Its message said
# "Constant asserted finite and positive (a None/0 would wait forever)" — no such
# assertion existed; that check lived only in a throwaway probe script, and a
# verification that ran once and was never committed is exactly the "one-off
# nobody re-ran" shape this file elsewhere refuses. `None` is the stdlib's
# spelling for "wait forever", so it is the value that silently turns every
# hang-detector in this module into a hang.
assert isinstance(HANG_TIMEOUT, (int, float)) and not isinstance(HANG_TIMEOUT, bool), (
    f"HANG_TIMEOUT must be a number, got {type(HANG_TIMEOUT).__name__} — `None` is "
    f"the stdlib's 'block forever', which disables every hang-detector here")
assert 0 < HANG_TIMEOUT < float("inf"), (
    f"HANG_TIMEOUT={HANG_TIMEOUT!r} must be finite and positive; 0 or a "
    f"non-finite value makes these waits unbounded rather than merely slow")

API_DIR = ROOT / "scripts" / "subsystem-store-api"
SERVER_PATH = API_DIR / "server.py"
SEED_PATH = API_DIR / "seed.sh"
VERIFY_PATH = API_DIR / "verify-byte-identity.sh"
RECALL_PATH = ROOT / "scripts" / "lib" / "subsystem_recall.py"


def _load_server():
    """Import `server.py` by path — its directory name has a hyphen in it."""
    spec = importlib.util.spec_from_file_location("subsystem_store_server", SERVER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


api = _load_server()

# 🔴 THE SAME MODULE OBJECT THE SERVER IMPORTED FROM, not a second load.
# `server.py` puts `scripts/lib` on `sys.path` and imports `subsystem_resolver`,
# so by the line above it is in `sys.modules`; re-loading it by path would give a
# SECOND module whose `_LOADER_ENTRY_ACTIONS` is a different dict, and a mutation
# aimed at the one the loader actually reads would survive against it.
resolver = sys.modules["subsystem_resolver"]
assert resolver.classify_path is api.classify_path, (
    "the server no longer shares the resolver's classifier — two copies of "
    "'what IS this path' is the defect the move was made to prevent"
)

# 🔴 THE SEAM THAT MAKES A POD-SHAPED `host:` LINE REACHABLE IN-PROCESS.
# `subsystem_recall` does `from subsystem_touch import store_host_line`, and
# `store_host_line` calls `store_host()` — a lookup in THIS module's globals, by
# design (`subsystem_touch.store_host`'s docstring: "one injection point makes
# the reader and the writer agree"). So patching it here moves what the
# IN-PROCESS server renders while the local CLI, which `run_verify` starts as a
# SUBPROCESS, keeps this machine's real identity. That asymmetry is precisely
# the workbench-vs-pod shape, and nothing else in this harness produces it.
touch = sys.modules["subsystem_touch"]
assert hasattr(touch, "store_host"), (
    "subsystem_touch no longer exposes `store_host` — the byte-identity "
    "verifier's `host:` canonicalisation is tested through this seam, and a "
    "rename here would silently turn those tests into same-host self-checks"
)


# =============================================================================
# Synthetic fixtures — realistic SHAPES, invented names, pairwise-distinct text.
# =============================================================================

SCOPE = "widget-cfg"
OTHER_SCOPE = "gizmo-notes"
EMPTY_SCOPE = "hollow-area"
BROKEN_SCOPE = "rubble-pile"

# Distinct on purpose: no substring is shared between the three sections, so a
# handler that served `## What it is` instead of `## Pointers` cannot pass.
WHAT_IT_IS = "A durable description that recall must never surface."
POINTER_LINE = "- ops skill `manage-widget` — invoke it for restarts"
NUANCE_LINE = "- 2026-01-02: the readiness probe lies for 40s after a reload."
OTHER_NUANCE = "- 2026-01-03: the sidecar drops its lease during a rollout."

# A token that clears the 43-character floor pinned literally below.
GOOD_TOKEN = "a" * 20 + "B" * 20 + "c" * 8  # 48 chars

# What `store_host()` returns INSIDE THE POD, in shape: the pod name as the
# label, and `machine-id-unreadable` because a container image carries no
# `/etc/machine-id` this reader will accept (`host_identity.MACHINE_ID_UNREADABLE`).
# Synthetic — an invented replicaset/pod suffix, like every other fixture here.
# 🔴 IT MUST SHARE NO PREFIX WITH THIS MACHINE'S OWN IDENTITY, or a
# canonicalisation that matched only a prefix would pass by coincidence.
POD_HOST = "subsystem-store-api-6f4d8c9b7a-qz2wl-machine-id-unreadable"


def _entry(
    service: str,
    scope: str,
    *,
    sensitivity: str | None = "internal",
    nuance: str = NUANCE_LINE,
) -> str:
    lines = ["---", f"service: {service}", f"scope: {scope}"]
    if sensitivity is not None:
        lines.append(f"sensitivity: {sensitivity}")
    lines += [
        "---",
        "",
        "## What it is",
        WHAT_IT_IS,
        "",
        "## Pointers",
        POINTER_LINE,
        "",
        "## Nuance / work-history",
        nuance,
        "",
    ]
    return "\n".join(lines)


# 🔴 THE STORE FIXTURE IS SITED ON tmpfs WHEN ONE IS USABLE, AND THAT IS A FIX FOR
# THE GATE FLAKE, NOT A PERFORMANCE TWEAK.
#
# `server.py:_replace_bytes` fsyncs the FILE and then the parent DIRECTORY *inside
# the request, before the response is written*, and fsync blocks in uninterruptible
# sleep. Under disk contention on the single node `devrc-ci` is pinned to, one such
# fsync can exceed `HANG_TIMEOUT` and the gate reports a code failure for an I/O
# stall — on PRs whose diff cannot reach this file at all. Full mechanism, and the
# on-demand reproducer, in `scripts/ci-repro/README.md`.
#
# Raising `HANG_TIMEOUT` is explicitly banned above and does not address the bound.
# The lever that remains is to remove the DEPENDENCE on disk latency, because these
# tests are about HTTP and store semantics and assert nothing whatsoever about how
# long an fsync takes. On tmpfs there is no backing device to contend for, so the
# stall cannot occur by construction.
#
# Measured 2026-09-01 on this host, `_replace_bytes`'s exact sequence (mkstemp →
# write → fsync file → replace → fsync dir), reporting MAX because HANG_TIMEOUT is
# breached by a single worst-case call and a mean would hide it:
#
#              idle                          under 3 concurrent fsync writers
#   disk    median 6.562ms  MAX 12.431ms     median 11.725ms  MAX 17.843ms
#   tmpfs   median 0.017ms  MAX  0.140ms     median  0.011ms  MAX  0.090ms
#
# Disk latency doubled under a deliberately modest load; tmpfs did not move. That
# load was nowhere near CI's, and no 60s stall was reproduced here — the claim is
# that tmpfs is FLAT under contention, not that this measured the CI event.
#
# 🔴 IT FALLS BACK TO `tmp_path`, so it can never make the gate worse than it is
# today. In the nix build sandbox `TMPDIR` is `/build` on ext2/ext3 while `/dev/shm`
# is tmpfs and writable (probed). But CI builds UNSANDBOXED — its traceback shows
# `/tmp/nix-build-…`, not `/build` — so there `/dev/shm` is the container's own
# mount, which is 64Mi by default and may be absent or read-only. Every one of those
# cases lands on the fallback and simply restores current behaviour.
#
# 🔴 It does NOT weaken `TestAHungRoundTripSAYSWhichSideBlocked`. That class does not
# depend on the real filesystem being slow: it monkeypatches `_fsync_dir` to stall
# deliberately, so it exercises the same code path wherever the store lives.
@pytest.fixture
def store(tmp_path: Path) -> Iterator[Path]:
    """A synthetic store: two populated scopes, one empty, one all-malformed."""
    with store_siting.store_root(tmp_path) as root:
        (root / SCOPE).mkdir(parents=True)
        (root / OTHER_SCOPE).mkdir(parents=True)
        (root / EMPTY_SCOPE).mkdir(parents=True)
        (root / BROKEN_SCOPE).mkdir(parents=True)
        (root / SCOPE / "thing-alpha.md").write_text(_entry("thing-alpha", SCOPE))
        (root / OTHER_SCOPE / "thing-beta.md").write_text(
            _entry("thing-beta", OTHER_SCOPE, nuance=OTHER_NUANCE)
        )
        # No front matter at all: the loader collects it as MALFORMED.
        (root / BROKEN_SCOPE / "thing-gamma.md").write_text("no front matter here\n")
        yield root


# RFC 5737 TEST-NET-3, and three DISTINCT addresses: a test that used one
# address for the client and the same one for the spoofed header could not tell
# "keyed on CF-Connecting-IP" from "keyed on anything at all".
CLIENT_IP = "203.0.113.7"
OTHER_IP = "203.0.113.99"
SPOOF_IP = "198.51.100.4"  # TEST-NET-2 — the value a forged XFF would carry

# The peer allowlist every in-process server below is built with. The harness
# binds on loopback, so loopback IS the "trusted proxy" for these tests — and it
# is spelled here rather than defaulted inside `build_server`, because a default
# there would be the very hole `SUBSYSTEM_STORE_TRUSTED_PROXIES` exists to close.
LOOPBACK_PROXY = "127.0.0.1/32"
# A proxy allowlist that loopback is NOT in. Used to drive the untrusted-peer
# path without needing a second network interface. TEST-NET-1, distinct from
# every client address above.
NOT_LOOPBACK_PROXY = "192.0.2.0/24"


@contextmanager
def running(
    store_root,
    token=GOOD_TOKEN,
    *,
    tokens=None,
    limiter=None,
    trusted_proxies=(LOOPBACK_PROXY,),
    wrap_sink=None,
):
    """Bind a real server on :0 and drive it over a real socket.

    Deliberately not a handler-level unit test: the response CODE, the header
    set and the exact bytes on the wire are what every claim in this file is
    about, and an in-process call to a handler method cannot observe them.

    🔴 THE YIELDED `audit` IS AN `AuditLog`, NOT A BARE LIST, so `await_audit`
    works on it. The response-does-not-imply-the-line race `drain_output`
    documents is NOT a property of the subprocess pipe — it is a property of
    `ThreadingHTTPServer`, and this in-process server has exactly the same one.

    🔴 `wrap_sink` IS THE SEAM THAT MAKES THE ORDERING AND ATOMICITY DEFECTS
    OBSERVABLE WITHOUT A SLEEP. It takes the recording sink and returns the sink
    actually installed, so a test can interpose a GATE (hold the first record and
    watch whether the client sails on) or a deliberately NON-ATOMIC writer (emit
    the record and its terminator as two writes — the shape `print` really has).
    The `AuditLog` is still handed every record either way, so `await_audit` and
    `settle` keep working at those sites. Wrapping here rather than standing up a
    second server helper means such a test inherits this teardown rather than
    open-coding a copy of it.
    """
    audit = AuditLog()
    sink = audit.append if wrap_sink is None else wrap_sink(audit.append)
    httpd = api.build_server(
        host="127.0.0.1",
        port=0,
        store_root=str(store_root),
        tokens=(token,) if tokens is None else tuple(tokens),
        trusted_proxies=tuple(trusted_proxies),
        limiter=limiter,
        audit=sink,
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", audit
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


# 🔴 WHICH SIDE WAS BLOCKED, AND ON WHAT. Ordered innermost-first: the first
# token found scanning a stuck handler's frames from the innermost outward wins,
# so `fsync` reached from inside `_replace_bytes` reports as FSYNC and not as the
# entry lock it is holding on the way in.
#
# 🔴 KNOWN DEFECT, MEASURED 2026-09-01, NOT FIXED HERE — THE SCAN MATCHES THE
# CHECKOUT PATH, BECAUSE `traceback.format_stack` RENDERS EACH FRAME'S FILENAME.
# These are substring tokens matched against the whole rendered stack, so a clone
# or worktree whose PATH contains one of them misclassifies EVERY hang, confidently
# and wrongly — which is worse than no verdict. Reproduced by accident: a worktree
# named `devrc-fsync` (named after the flake being fixed, which is the likely case)
# turned `test_a_stall_on_the_ENTRY_LOCK_reads_DIFFERENTLY` red with
# `MECHANISM = SERVER_BLOCKED_IN_FSYNC`, while the identical tree at
# `devrc-storetmp` passed. `flock`, `_EntryLock` and `_audit_lock` are exposed the
# same way.
# ⚠ The first attempt to CONFIRM that was itself wrong and read as a refutation:
# after `git worktree move`, a stale `__pycache__` kept the old `co_filename`
# baked into the code objects, so the renamed tree still rendered the OLD path and
# still failed. Clearing `__pycache__` (or `PYTHONDONTWRITEBYTECODE=1`) is what
# made the control honest — the documented mtime+size revalidation trap, hit here
# in the wild.
# The fix is to scan the frames' SOURCE LINES rather than their filenames; it is
# deliberately left out of the change that found it, because this classifier has
# its own tests and that is its own edit.
_HUNG_SERVER_RULES: tuple[tuple[str, str], ...] = (
    ("fsync", "SERVER_BLOCKED_IN_FSYNC"),
    ("flock", "SERVER_BLOCKED_ON_ENTRY_LOCK"),
    ("_EntryLock", "SERVER_BLOCKED_ON_ENTRY_LOCK"),
    ("_audit_lock", "SERVER_BLOCKED_IN_AUDIT_SINK"),
    ("self.audit(", "SERVER_BLOCKED_IN_AUDIT_SINK"),
)


def _why_the_server_did_not_answer() -> str:
    """Every live thread's stack, plus a one-line `MECHANISM =` verdict.

    🔴 DIAGNOSTICS, NOT A MITIGATION. Nothing here retries, drains, or moves a
    bound, and the `TimeoutError` is re-raised unchanged — a test that hung
    still fails, exactly as loudly. What this adds is the one fact the bare
    exception cannot carry, and whose absence is why the store-api hang stayed
    open for weeks: **a client-side read timeout is the observable that the
    most mechanisms share, so on its own it identifies none of them.**

    The rivals, every one of which surfaces identically as `TimeoutError` at
    `socket.py:720` (`SocketIO.readinto` -> `recv_into`):

      * **the handler parked in `fsync`.** `server.py:_replace_bytes` issues
        TWO — the file, then the parent directory — INSIDE the request and
        BEFORE the response is written. `fsync` blocks in uninterruptible
        D-state, is bounded by nothing, and burns no CPU, so it is invisible in
        CPU/PSI-cpu metrics. The handler's `timeout = 15` does NOT bound it:
        that is a SOCKET timeout and does not reach a syscall.
      * **the handler parked on the entry `flock`** (also unbounded).
      * **the handler parked in the audit sink** — `_audit_lock` is a CLASS
        attribute and therefore process-global across every server in the
        worker.
      * **the connection was never ACCEPTED** — accept-queue overflow, or the
        `serve_forever` thread simply not scheduled. Distinguished from all of
        the above by there being no handler thread at all.

    The verdict is emitted as a `MECHANISM = ...` line so a CI log can be
    grepped for it without a human reading the stacks, the same shape the
    transport investigation in `claudedocs/handoff-cairn-phase3.md` used.
    """
    frames = sys._current_frames()
    main_ident = threading.main_thread().ident
    handlers: list[tuple[str, list[str]]] = []
    accept_loop_parked = False
    report: list[str] = []

    for thread in threading.enumerate():
        frame = frames.get(thread.ident)
        if frame is None or thread.ident == main_ident:
            continue
        stack = traceback.format_stack(frame)
        report.append(
            f"--- thread {thread.name!r} daemon={thread.daemon} ---\n" + "".join(stack)
        )
        blob = "".join(stack)
        # `process_request_thread` is socketserver's per-connection worker, so
        # its presence is what makes a thread a HANDLER rather than the accept
        # loop. A thread can be both only if `serve_forever` ran inline, which
        # `running()` never does.
        if "process_request_thread" in blob:
            handlers.append((thread.name, stack))
        elif "serve_forever" in blob:
            accept_loop_parked = True

    # 🔴 EVERY handler is classified, not the first one found — and if they
    # DISAGREE the headline says so instead of picking one. `ThreadingHTTPServer`
    # runs daemon handler threads that `shutdown()`/`server_close()` do NOT join,
    # so a thread stuck in an EARLIER test is still alive here and would
    # otherwise be reported as this request's mechanism. That is not
    # hypothetical: it happened while writing the tests for this function, and a
    # confident wrong verdict is worse than none.
    per_handler: list[str] = []
    for name, stack in handlers:
        found = "BLOCKED_ELSEWHERE"
        for line in reversed(stack):          # innermost frame first
            hit = next(
                (m for token, m in _HUNG_SERVER_RULES if token in line), None
            )
            if hit:
                found = hit
                break
        per_handler.append(f"{name}={found}")

    distinct = {entry.split("=", 1)[1] for entry in per_handler}
    if len(distinct) == 1:
        verdict = distinct.pop()
    elif len(distinct) > 1:
        verdict = "AMBIGUOUS(" + ",".join(sorted(distinct)) + ")"
    elif accept_loop_parked:
        # No handler exists at all, so the connection was never accepted. That
        # names the FAMILY — accept-queue overflow or an unscheduled accept loop
        # — and deliberately does not choose between them; the listening
        # socket's own queue depth is what separates those two.
        verdict = "NEVER_ACCEPTED"
    else:
        verdict = "NO_SERVER_THREAD_ALIVE"

    return (
        f"\nMECHANISM = {verdict}   "
        f"(handler threads={len(handlers)} [{' '.join(per_handler) or '-'}], "
        f"accept loop parked={accept_loop_parked})\n"
        + "".join(report)
    )


def _await_no_handler_threads(bound: float = HANG_TIMEOUT) -> int:
    """Block until no `process_request_thread` is alive. Returns what is left.

    🔴 A TEST THAT DELIBERATELY WEDGES A HANDLER MUST DRAIN IT. Handler threads
    are daemons and `running()`'s teardown does not join them, so one left
    parked leaks into every later test in this worker — where
    `_why_the_server_did_not_answer` will see it and report ITS mechanism for
    somebody else's hang. Bounded, and the count is returned rather than
    asserted so the caller decides what a leftover means.
    """
    deadline = time.monotonic() + bound
    while time.monotonic() < deadline:
        alive = [
            t for t in threading.enumerate()
            if "process_request_thread" in t.name
        ]
        if not alive:
            return 0
        time.sleep(0.02)
    return len(
        [t for t in threading.enumerate() if "process_request_thread" in t.name]
    )


def fetch(
    url: str,
    *,
    token: str | None = None,
    method: str = "GET",
    auth_header=None,
    client_ip: str | None = CLIENT_IP,
    extra_headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = HANG_TIMEOUT,
):
    """Return (code, headers, body-bytes) without raising on 4xx/5xx.

    🔴 `CF-Connecting-IP` is sent BY DEFAULT because the server requires it —
    it is the rate limiter's key, and an absent one fails closed. Pass
    `client_ip=None` to exercise exactly that.

    `data` is the REQUEST body, added for the write path. `urllib` frames it
    with a single `Content-Length`, which is the one framing `_consume_body`
    accepts — the hostile framings (chunked, duplicated, negative) have their own
    tests and are put on the wire by `http.client` directly, since `urllib`
    cannot express them.
    """
    req = urllib.request.Request(url, method=method, data=data)
    if auth_header is not None:
        req.add_header("Authorization", auth_header)
    elif token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    if client_ip is not None:
        req.add_header("CF-Connecting-IP", client_ip)
    for key, value in (extra_headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()
    except TimeoutError:
        # 🔴 RE-RAISED UNCHANGED — this is a report, not a recovery. The dump is
        # taken HERE, while the server is still blocked, because by the time the
        # exception reaches the test the `with running(...)` teardown has torn
        # down the very threads whose stacks answer the question.
        #
        # ⚠ `TimeoutError` IS THE READ PHASE, and that is why this clause is
        # narrow rather than an `except OSError`. `urllib` wraps only
        # `h.request()`, so a CONNECT-phase timeout arrives as `URLError` and
        # already names its own phase; letting it past unhandled keeps the two
        # distinguishable in the log instead of collapsing them into one report.
        #
        # 🔴 THE REPORTER MAY NEVER REPLACE THE FAILURE IT DESCRIBES. ~200 call
        # sites share this helper; if `_why_the_server_did_not_answer` ever
        # raised, that exception would propagate INSTEAD of the `TimeoutError`
        # and every hang in the module would report as a defect in the
        # diagnostic. A best-effort report is worth having; a diagnostic that
        # can rewrite the diagnosis is not.
        try:
            report = _why_the_server_did_not_answer()
        except Exception as diag_exc:  # noqa: BLE001 — see above
            report = f"\nMECHANISM = REPORTER_FAILED ({diag_exc!r})\n"
        print(report, file=sys.stderr, flush=True)
        raise


def _raw_request(host: str, path: str, headers: list[tuple[str, str]]) -> int:
    """GET `path` with headers put on the wire VERBATIM, duplicates included.

    `urllib.request.Request.add_header` stores headers in a dict, so it silently
    collapses a repeated header to one — which makes it structurally incapable
    of expressing the "two `CF-Connecting-IP`s" case. `http.client.putheader`
    can.
    """
    conn = http.client.HTTPConnection(host, timeout=HANG_TIMEOUT)
    try:
        conn.putrequest("GET", path, skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", host)
        for key, value in headers:
            conn.putheader(key, value)
        conn.endheaders()
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


def _executable_tokens(path: Path) -> str:
    """`path`'s source with COMMENTS and DOCSTRINGS removed, string literals kept.

    A header name IS a string literal, so a scan that dropped strings could not
    see one; a scan that kept comments would trip over the paragraphs explaining
    why a header is refused. `ast` drops comments by construction and the walk
    below drops docstrings, leaving exactly what executes.
    """
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _comparable(headers: dict) -> tuple:
    """Header set with the two fields that legitimately vary stripped."""
    return tuple(
        sorted((k.lower(), v) for k, v in headers.items() if k.lower() != "date")
    )


AUDIT_PREFIX = "store-api audit "


def _audit_lines(all_lines: "list[str]") -> "list[str]":
    """The audit subset of a stream. ONE copy, shared by both record types.

    `Drained` (a subprocess's stdout) and `AuditLog` (an in-process server's
    sink) differ in where the lines come from and in nothing else that matters
    here; open-coding the filter twice is how the two drift apart.
    """
    return [ln for ln in all_lines if ln.startswith(AUDIT_PREFIX)]


class AuditLog(list):
    """Every line an IN-PROCESS `running()` server has emitted so far.

    🔴 IT IS A REAL `list` BECAUSE FORTY-ODD ASSERTIONS READ IT AS ONE, and it
    carries `Drained`'s read surface because `await_audit` is the one helper in
    this file that knows how to wait for a line. The race is NOT a property of
    the subprocess pipe: it is a property of `ThreadingHTTPServer`, so an
    in-process `fetch()` returning proves exactly as little about the audit line
    as a subprocess one does. Before this class existed the in-process sites had
    no way to wait at all, and the only thing standing between them and a red run
    was `shutdown()`'s 0.5s poll interval — a `sleep` nobody wrote down.
    Measured: with the handler's `_audit` delayed past that interval, the
    teardown "barrier" vanishes entirely.

    🔴 AND THE WAIT IS STILL NEEDED NOW THAT `_audit` RUNS BEFORE `_respond`.
    That ordering makes "the client holds response N" imply "line N is in the
    sink" for the request the client is holding — but the append is to a plain
    `list` from a handler thread nothing joins, and requests that are NOT the one
    being awaited (a `/healthz`, a pipelined second request, a lockout probe) are
    still in flight. `TestTheAuditLineIsWrittenBEFORETheResponse` pins the new
    ordering property directly; these helpers keep waiting rather than leaning on
    it, because a waiter is correct under both.

    🔴 `closed` IS `None`, NOT AN UNSET `Event`, AND THAT IS THE HONEST VALUE.
    A `Drained` reaches EOF when the pipe closes, which is a real "no more lines
    are coming" signal. This stream has none: `ThreadingHTTPServer` runs its
    handlers as DAEMON threads, which `socketserver._Threads.append` refuses to
    track, so `server_close()` joins nothing and a handler can still append
    after teardown returns. Setting a `closed` event there would be a lie that
    turns "the line is a little late" into a hard failure; `await_audit` reads
    the sentinel and simply waits out its deadline instead.
    """

    closed = None

    @property
    def all(self) -> "list[str]":
        return list(self)

    @property
    def audit(self) -> "list[str]":
        return _audit_lines(list(self))

    @property
    def text(self) -> str:
        return "\n".join(self)


class Drained:
    """Everything a running store-api process has printed so far.

    🔴 Keeps the FULL output, not just the audit lines. The audit subset is what
    most assertions want, but at least one caller asserts that a credential
    appears NOWHERE in stdout — a check that silently weakens if it is narrowed
    to the audit lines, since a token leaked on a non-audit line would then pass.
    That caller must `wait_closed()` first: a line printed during SHUTDOWN (a
    SIGTERM handler, an atexit hook) reaches the pipe after the last assertion
    would otherwise have read it, and a credential leaked there must still fail.
    """

    def __init__(self) -> None:
        self.all: list[str] = []
        self.closed = threading.Event()   # set when the pipe reaches EOF

    @property
    def audit(self) -> "list[str]":
        return _audit_lines(self.all)

    @property
    def text(self) -> str:
        """The whole stream, for `x not in out` style assertions."""
        return "\n".join(self.all)

    def wait_closed(self, timeout: float = HANG_TIMEOUT) -> bool:
        """Block until the process's stdout reaches EOF. Returns whether it did.

        Read `text` only AFTER this. Without it the reader thread may still be
        draining, so an assertion over the whole stream is racing the very lines
        it is meant to inspect — the same class of bug as asserting on an audit
        line before it is printed, one layer out. It is the reason
        `drain_output` returns something joinable at all: a helper that cannot
        be waited on just relocates the race into every caller.
        """
        return self.closed.wait(timeout)


def drain_output(proc) -> Drained:
    """Start draining a RUNNING process's stdout; returns the growing record.

    🔴 THE RESPONSE DOES NOT IMPLY EVERY LOG LINE, and every test that reads
    audit output has to be written around that. A handler used to write its
    response and only THEN call `_audit()`, on a ThreadingHTTPServer — so
    `fetch`/`fetch_from` returning meant the response was written, not that the
    handler thread had reached its `print`. `_audit` now runs FIRST (see
    `TestTheAuditLineIsWrittenBEFORETheResponse`), which settles the awaited
    request's own line but says nothing about any OTHER request still in flight
    on this process. A test that calls `proc.terminate()` on one client's return
    is still racing every line it has not waited for.

    Draining also keeps the pipe buffer from becoming a SECOND timing
    dependency. Teardown stays with `running_subprocess`.

    History, and why this is a function rather than a fourth copy: #544 found the
    race, measured it at 3/20 red locally plus two consecutive reds in the nix
    sandbox, and fixed ONE site inline. The other two kept the defect and one of
    them duly failed in CI at 2026-08-23T00:37Z (`devrc-ci-jxf5j`) with
    `IndexError: list index out of range` — an empty list indexed at [-1], on a
    tree whose only change was to an unrelated test. That is the open-coded
    predicate from claude/RULES.md: wrong at N-1 sites, and re-fixed one site at
    a time until it is consolidated.
    """
    out = Drained()

    def _run() -> None:
        try:
            for raw in proc.stdout:                 # ends when the pipe closes
                out.all.append(raw.rstrip("\n"))
        finally:
            out.closed.set()                        # EOF, even if the read raised

    threading.Thread(target=_run, daemon=True).start()
    return out


def await_audit(out: "Drained | AuditLog", n: int, timeout: float = HANG_TIMEOUT) -> "list[str]":
    """Wait for at least `n` audit lines, then return them. RAISES if they never
    arrive.

    🔴 IT TAKES EITHER RECORD, and that is the whole reason there is only one of
    these. `Drained` wraps a subprocess's stdout; `AuditLog` is what an
    in-process `running()` server appends to. The hazard is identical in both —
    `_respond` runs before `_audit` — so a second waiting helper for the second
    shape would be the open-coded predicate `claude/RULES.md` warns about, wrong
    at N-1 sites.

    🔴 It raises rather than returning short, so that `[-1]` on the result is
    always safe. Returning whatever had arrived is what produced the CI failure
    this helper exists to prevent — `IndexError: list index out of range`, an
    empty list indexed at [-1], which names neither the expectation nor the
    actual. Consolidating a footgun into one place does not disarm it; this does.

    🔴 IT GUARANTEES A FLOOR, AND THE CEILING IS NOT WHERE IT LOOKS. This used to
    say "more lines than expected remains the caller's to catch", which is only
    half true and the misleading half. The value returned is a SNAPSHOT taken
    while the process is still running, so a caller's `== 3` against it cannot
    see a fourth record emitted afterwards — during shutdown, for instance.
    Measured: with the server patched to emit one extra audit line at SIGTERM,
    the racy pre-helper code FAILED and the snapshot check PASSES.

    🔴 SO A CALLER THAT MEANS "EXACTLY N, EVER" CALLS `settle()` — NOT THIS.
    This paragraph used to prescribe "re-read `out.audit` after
    `out.wait_closed()`", which is a real recipe for a `Drained` and an
    IMPOSSIBLE one for an `AuditLog`: that class has no `wait_closed` at all and
    its `closed` is `None` on purpose (see `AuditLog`). Nine in-process sites
    were converted to this helper on the strength of that sentence and silently
    lost their ceiling — a SECOND line emitted from a later callback, another
    thread, or after any delay became invisible to them.

    🔴 AND THE TWO HALVES DISAGREE, WHICH IS THE WHOLE POINT — "the ceiling is
    gone" would be an overstatement. Measured over the 19 exact-count items:
    with every `_audit` scheduling one extra line 50 ms or 300 ms later, the
    snapshot assertions were green 0/19 and these `settle` assertions are red
    18/19; with a SYNCHRONOUS second `self.audit(...)` emitted inline, the
    SNAPSHOT assertions were already red 19/19. So the ceiling was never
    destroyed — it was NARROWED to the synchronous case, and `settle` widens it
    back.

    🔴 AND THE ONE DEFERRED SURVIVOR IS A PROPERTY OF THAT SITE'S TEARDOWN, NOT
    OF THE MUTANT. The survivor is the SUBPROCESS site, and the earlier reading
    of it — "the mutant's own timer dies with the SIGTERM'd child, so that is a
    property of the mutant" — was wrong in a way worth correcting rather than
    quietly rewriting: `running_subprocess` `terminate()`s AND `wait()`s on
    block exit, so that site structurally cannot observe ANY record deferred
    past its own teardown — from a mutant, or from a real defect. The blind spot
    belongs to the fixture. What the synchronous mutant shows is only that the
    site's assertion works when the extra record arrives BEFORE teardown.
    `test_the_STDOUT_audit_stream_names_the_matched_fingerprint` keeps the
    `wait_closed()` form because a subprocess pipe really does have an EOF.

    🔴 AND IT GUARANTEES A COUNT, NEVER AN ORDER — the half that cost a red run
    after the count race was closed everywhere. `_audit` used to run AFTER
    `_respond`, so a client could send request N+1 while handler N had still not
    appended: request N's line landed SECOND. `await_audit(audit, 2)` is then
    perfectly satisfied by two lines in the WRONG order, and a caller reading
    `lines[0]` attributes one request's fields to another. MEASURED: with every
    positional site waiting only at the end, `test_a_ROTATION_end_to_end_old_
    still_works_then_stops` failed on `lines[0]` inside a full-file run.

    🔴 THE SERVER-SIDE HALF OF THAT IS NOW FIXED — AND THIS OBLIGATION STAYS.
    `_audit` runs BEFORE `_respond`, so for a client that issues request N+1
    only after response N has come back, the records are in request order by
    construction. Two reasons the rule below does not relax: (a) it is a claim
    about a SEQUENTIAL client, and a site with two requests genuinely in flight
    (`threading.Thread`, a pipelined socket, a `/healthz` alongside an API call)
    gets no ordering promise at all; (b) leaning on it would make every
    positional site in this file a second, uncounted assertion of the ordering
    property — so a regression there would show up as forty confusing failures
    instead of the one guard that exists to state it,
    `TestTheAuditLineIsWrittenBEFORETheResponse`.

    So a POSITIONAL read keeps its second obligation: wait for line N BEFORE
    issuing request N+1, which makes the position a fact rather than an
    assumption. A caller that only aggregates (`len`, `any`, `all`, `sum`,
    `join`) has no such obligation.

    🔴 THE OBLIGATION IS "AT MOST ONE AUDITED REQUEST IS IN FLIGHT PER WAIT",
    NOT "one request per wait" — an earlier draft of this paragraph claimed
    "every positional site in this file interleaves its waits", and that was
    simply false for the three healthz sites (`test_health_is_NOT_audited`,
    `test_health_needs_NO_client_ip_because_the_kubelet_sends_none`,
    `test_healthz_answers_an_untrusted_peer`). Each issues TWO requests, waits
    ONCE and reads `lines[0]`. They are sound anyway, and for a reason worth
    spelling out rather than papering over: `/healthz` is the thing they are
    proving is NOT audited, so only one of the two requests can produce a line
    and `lines[0]` cannot be the wrong one. That argument is STRUCTURAL, not a
    measurement — no mutant can demonstrate the absence of an ordering hazard
    that the request count already rules out. A site that issues two AUDITED
    requests and waits once has no such argument available and must interleave.
    """
    # `closed` is None for an `AuditLog`: that stream has no EOF to short-circuit
    # on, so the loop simply runs to its deadline. See `AuditLog`.
    closed = out.closed
    deadline = time.time() + timeout
    while len(out.audit) < n and time.time() < deadline:
        if closed is not None and closed.is_set() and len(out.audit) < n:
            break                                   # the pipe is done; no more coming
        time.sleep(0.02)
    lines = out.audit
    ended = closed is not None and closed.is_set()
    assert len(lines) >= n, (
        f"expected at least {n} `{AUDIT_PREFIX}` line(s) within {timeout:g}s, got "
        f"{len(lines)}{' (stdout closed early)' if ended else ''}.\n"
        f"full stdout:\n{out.text}")
    return lines


# How long `settle` keeps watching AFTER the server is torn down. It is the
# width of the ceiling, and it is a real cost. Chosen so a second line deferred
# by 300ms is caught with margin, on top of whatever `shutdown()`'s 0.5s poll
# interval happens to contribute — which is 0 to 0.5s and NOT a guarantee, which
# is exactly why the wait is written down here instead of being leaned on.
#
# 🔴 THE COST IS 13.76s, AND IT IS MEASURED INSIDE THE HELPER, NOT BY DIFFING
# TWO SUITE RUNS. An earlier version of this comment claimed "+8.3s (279.2s ->
# 287.5s)", which was the difference of two single whole-file runs — an
# instrument that cannot see this effect: four whole-file runs of the SAME tree
# in one sitting came in at 273.6s, 288.9s, 290.4s and 302.4s, a 28.8s spread
# over a ~14s quantity. That number was also reasoned from a site count that
# missed the x5 parametrization on
# `TestMalformedRequestLinesDoNotCrash.test_it_answers_instead_of_crashing`:
# 15 call SITES, but 19 call EXECUTIONS.
#
# METHOD: a pytest plugin wraps `settle` and sums the helper's OWN wall time
# over a whole-file run. 19 calls — 1 on the free EOF path (a `Drained`) and 18
# on the wall-clock path — totalling 13.76s, against 0.00s for the identical
# run with the grace forced to 0. So 13.76s is attributable to the window, and
# the arithmetic floor agrees: 18 x 0.75 = 13.5s.
SETTLE_GRACE_S = 0.75


def settle(out: "Drained | AuditLog", n: int, grace: float = SETTLE_GRACE_S) -> "list[str]":
    """EXACTLY `n` audit lines, ever — the CEILING, which `await_audit` cannot give.

    🔴 CALL IT AFTER THE `with running(...)` BLOCK, NEVER INSIDE, and
    `test_settle_is_called_AFTER_the_running_block_never_inside` enforces that
    structurally rather than leaving it to this sentence.

    🔴 AND THE REASON IS TEARDOWN, NOT THE POLL. An earlier version of this
    paragraph said that inside the block "the only thing it could observe is the
    same snapshot `await_audit` already returned", and that is not what the code
    does: the grace loop below would still run its 0.75s inside the block and
    would still catch a line deferred by 300ms. What it could NOT catch there is
    the case the ceiling exists for — a line emitted BY teardown, from
    `shutdown()`/`server_close()`/`thread.join()` or from a handler thread that
    only finishes as the block exits. None of that has happened yet on the
    inside, so the window would be spent watching a server that is still up.

    🔴 WHY IT IS A WALL-CLOCK WAIT AND NOT AN EVENT. An `AuditLog` has no EOF to
    wait for: `ThreadingHTTPServer` runs handlers as DAEMON threads, which
    `socketserver._Threads.append` drops on the floor, so `server_close()` joins
    nothing and `AuditLog.closed` is honestly `None`. There is no signal here to
    convert into an `Event` — only elapsed time. That makes this ceiling a
    BOUNDED one, and the bound is `grace` PLUS whatever teardown itself costs
    before the loop starts. MEASURED against a server patched to emit one extra
    audit line per request after a delay, over the 19 exact-count items:
    18/19 red at 300ms, 18/19 red at 1.0s, 0/19 at 1.5s. So the sentence this
    replaces — "a line emitted a full second late is still unobserved" — was
    FALSE, and contradicted `SETTLE_GRACE_S`'s own comment just above it: the
    effective window is roughly 0.75-1.25s, not a flat 0.75s. What is true is
    that the window is finite and this docstring is where its width is written
    down; no assertion in this file claims a line arriving after it is caught.

    🔴 A 0.75s WALL-CLOCK BOUND INSIDE AN ASSERTION IS THE SHAPE THAT PRODUCES
    FLAKES, SO HERE IS WHY THIS ONE CANNOT FAIL FALSELY. The bound is on how
    long a surplus has to ARRIVE, never on how long anything has to FINISH.
    (a) Every call site runs `await_audit(out, n)` first, so `len >= n` already
    holds on entry and the `<= n` assertion can only fire on a genuine surplus —
    it is not racing the arrival of the n records it expects. (b) The sink
    handed to `build_server` is `audit.append` and nothing in this file removes
    an element, so a count that reached n cannot drop back below it. (c) The
    grace deadline and any deferred emitter are on the SAME wall clock, so a
    saturated host stretches both and cannot shrink the window relative to what
    it is watching for. The failure mode this leaves is the honest one — a
    surplus arriving after the window is MISSED, never a green run turned red.

    🔴 A `Drained` DOES HAVE AN EOF, AND THIS TAKES IT. When `out.closed` is a
    real `Event` the grace window is not used at all: the pipe reaching EOF is a
    genuine "no more lines are coming", which is strictly stronger than any
    amount of elapsed time and costs nothing. The wall-clock path below exists
    only for the sink that has no such signal.

    🔴 IT FAILS FAST, AND IT PRINTS THE WHOLE LEDGER RATHER THAN GUESSING WHICH
    LINE IS THE EXTRA. The `<= n` check runs on every poll, so an extra line is
    reported the moment it lands rather than at the end of the window. It used
    to print `lines[n:]` under the heading "Surplus", which quietly assumed the
    records arrive in the order they were requested — the exact assumption
    `await_audit`'s docstring exists to deny ("IT GUARANTEES A COUNT, NEVER AN
    ORDER"). At an interleaved site a deferred record can land BETWEEN two
    genuine ones, and the slice then names a genuine record as the intruder.
    Never a pass/fail difference; purely a message that sends the reader to the
    wrong line, which is the failure mode the fail-specific claim was about.
    """
    if out.closed is not None:
        # 🔴 `wait_closed()`, NOT `out.closed.wait()`. Identical behaviour and a
        # DIFFERENT static shape: `test_no_test_reads_an_AUDIT_LINE_from_a_
        # process_it_just_terminated` counts a bare `.wait` attribute as a killer
        # verb, so the raw `Event` form makes this helper — and transitively
        # every test that calls it — a false offender in that seam guard.
        #
        # 🔴 AND ITS ANSWER IS ASSERTED, NOT DISCARDED. `wait_closed` returns
        # whether EOF was actually reached; on a timeout the discarded form
        # degraded silently into a bare snapshot with no ceiling at all, and
        # then reported "the closed stream holds ..." about a stream that was
        # not closed. That is the docstring's "A `Drained` DOES HAVE AN EOF, AND
        # THIS TAKES IT" claiming a state which did not hold.
        assert out.wait_closed(HANG_TIMEOUT), (
            f"the stream never reached EOF within {HANG_TIMEOUT:g}s, so there is "
            f"no ceiling to check — it holds {len(out.audit)} `{AUDIT_PREFIX}` "
            f"line(s) so far for an expected {n}.\nfull stream:\n{out.text}")
        lines = out.audit
        assert len(lines) == n, (
            f"the closed stream holds {len(lines)} `{AUDIT_PREFIX}` line(s) for "
            f"an expected {n}.\nfull stream:\n{out.text}")
        return lines
    deadline = time.time() + grace
    while True:
        lines = out.audit
        assert len(lines) <= n, (
            f"{len(lines)} `{AUDIT_PREFIX}` line(s) for an expected {n} — a "
            f"record was emitted after the response. The records are NOT "
            f"ordered by request (see `await_audit`), so which of these is the "
            f"extra cannot be read off its position; all of them:\n  "
            + "\n  ".join(lines))
        if time.time() >= deadline:
            break
        time.sleep(0.02)
    assert len(lines) == n, (
        f"expected exactly {n} `{AUDIT_PREFIX}` line(s), got {len(lines)}.\n"
        f"full stream:\n{out.text}")
    return lines


def tree_hash(root: Path) -> str:
    """Content + relative-path digest of a whole tree. Order-stable."""
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(root)).encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


# =============================================================================
# 1. Token loading — four guards, each with its OWN sentence, each REACHABLE.
# =============================================================================


def loaded(token_file, env, **kwargs) -> list[tuple]:
    """`load_tokens` as PLAIN TUPLES — `(token, identity, scopes)` per row.

    🔴 A tuple, not the `TokenRecord` itself, and deliberately: importing the
    dataclass and asserting `== TokenRecord(...)` would re-derive the expected
    value from the implementation under test, and a field renamed on both sides
    would stay green. Spelling the three facts out here means the assertion
    breaks when the SHAPE changes, which is when someone should look.

    `warn=` is swallowed by default so the legacy-mode banner does not spray
    stderr across every guard test; the tests that are ABOUT that banner pass
    their own sink and read it.
    """
    kwargs.setdefault("warn", lambda _line: None)
    return [(r.token, r.identity, r.scopes) for r in api.load_tokens(token_file, env, **kwargs)]


def exc_of(call) -> ValueError:
    """The `ValueError` `call()` raises, so a MESSAGE assertion is one line.

    Only for tests whose subject is the wording; `pytest.raises` stays inline
    wherever the fact under test is that it raised AT ALL, because that is a
    different claim and reads better spelled out.
    """
    with pytest.raises(ValueError) as exc:
        call()
    return exc.value


class TestTokenLoadingGuards:
    """`load_tokens` refuses to serve on a token that is absent, empty or weak.

    🔴 Each case is built so that every EARLIER guard passes: the empty-token
    case uses a file that exists and is readable, and the too-short case uses a
    file that exists, is readable and is non-empty. A test that tripped an
    earlier guard would be green with the guard it names deleted.
    """

    def test_no_source_at_all_names_the_two_ways_to_supply_one(self):
        with pytest.raises(ValueError) as exc:
            api.load_tokens(None, {})
        assert "no token source" in str(exc.value)
        assert "--token-file" in str(exc.value)
        assert "SUBSYSTEM_STORE_TOKEN" in str(exc.value)

    def test_a_missing_file_is_not_confused_with_an_absent_one(self, tmp_path: Path):
        with pytest.raises(ValueError) as exc:
            api.load_tokens(str(tmp_path / "nope"), {})
        assert "token file unreadable" in str(exc.value)

    def test_a_readable_file_of_whitespace_is_rejected_as_EMPTY(self, tmp_path: Path):
        # Guard 1 and 2 both pass here: the source exists and reads fine.
        path = tmp_path / "tok"
        path.write_text("   \n\t\n")
        with pytest.raises(ValueError) as exc:
            api.load_tokens(str(path), {})
        assert "token is empty" in str(exc.value)

    def test_a_short_but_perfectly_valid_file_is_rejected_as_TOO_SHORT(
        self, tmp_path: Path
    ):
        # Guards 1-3 all pass: the file exists, reads, and is non-empty. This is
        # the hand-typed-token case, which is exactly what guard 4 is for.
        path = tmp_path / "tok"
        path.write_text("hunter2\n")
        with pytest.raises(ValueError) as exc:
            api.load_tokens(str(path), {})
        assert "is too short" in str(exc.value)
        # The position is named even when there is only one — a message that
        # said "the token" would have to change shape the day a second appears.
        assert "token on line 1 of 1" in str(exc.value)
        # 43 chars = 256 bits base64url, pinned LITERALLY (§2b).
        assert "43" in str(exc.value)

    def test_the_floor_is_43_characters(self):
        # A literal, not `api.MIN_TOKEN_CHARS` — importing it would assert x == x.
        assert api.MIN_TOKEN_CHARS == 43

    def test_a_token_of_exactly_the_floor_is_accepted(self, tmp_path: Path):
        path = tmp_path / "tok"
        path.write_text("z" * 43 + "\n")
        # 🔴 A BARE ROW IS THE LEGACY RECORD: identity `legacy`, `scopes=None`
        # meaning UNRESTRICTED. Pinned here rather than only in the phase-3
        # section, because this is the shape criterion 10's rollback re-adds and
        # the whole migration rests on it still loading.
        assert loaded(str(path), {}) == [("z" * 43, "legacy", None)]

    def test_env_is_the_FALLBACK_not_the_primary(self, tmp_path: Path):
        # Both sources present: the FILE wins. The agent exec sandbox strips env
        # vars from agent-run commands, so an env token that quietly overrode a
        # mounted secret would make the deployed token unknowable.
        path = tmp_path / "tok"
        path.write_text("f" * 50)
        assert loaded(str(path), {"SUBSYSTEM_STORE_TOKEN": "e" * 50}) == [
            ("f" * 50, "legacy", None)
        ]

    def test_env_is_used_when_no_file_is_named(self):
        assert loaded(None, {"SUBSYSTEM_STORE_TOKEN": "e" * 50}) == [
            ("e" * 50, "legacy", None)
        ]


# =============================================================================
# 2. Auth — the POSITIVE and NEGATIVE controls, reported as a pair.
# =============================================================================


class TestAuthControls:
    """🔴 REPORTED AS A PAIR. A 200 from a valid token says nothing on its own —
    a handler that ignores the header entirely produces exactly that 200. The
    rejections below are what make the acceptance mean something.
    """

    def test_POSITIVE_a_valid_token_gets_a_real_digest(self, store: Path):
        with running(store) as (base, _):
            code, headers, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert code == 200
        assert headers["X-Store-Status"] == "recalled"
        # A NON-ZERO count, watched to move: the pointer line is present.
        assert POINTER_LINE.encode() in body
        assert len(body) > 500

    def test_NEGATIVE_no_authorization_header_at_all(self, store: Path):
        with running(store) as (base, _):
            code, _headers, body = fetch(f"{base}/api/v1/recall/{SCOPE}")
        assert code == 401
        assert body == b"unauthorized\n"

    def test_NEGATIVE_a_wrong_token(self, store: Path):
        with running(store) as (base, _):
            code, _h, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
        assert code == 401
        assert body == b"unauthorized\n"

    def test_NEGATIVE_a_NEAR_MISS_token_of_the_right_length(self, store: Path):
        # One character different, same length — the case a length check or a
        # prefix comparison would wave through.
        near = GOOD_TOKEN[:-1] + ("d" if GOOD_TOKEN[-1] != "d" else "e")
        assert len(near) == len(GOOD_TOKEN) and near != GOOD_TOKEN
        with running(store) as (base, _):
            code, _h, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=near)
        assert code == 401
        assert body == b"unauthorized\n"

    def test_NEGATIVE_a_valid_token_under_the_wrong_scheme(self, store: Path):
        with running(store) as (base, _):
            code, _h, _b = fetch(
                f"{base}/api/v1/recall/{SCOPE}", auth_header=f"Basic {GOOD_TOKEN}"
            )
        assert code == 401

    def test_NEGATIVE_the_token_as_a_bare_header_value(self, store: Path):
        with running(store) as (base, _):
            code, _h, _b = fetch(f"{base}/api/v1/recall/{SCOPE}", auth_header=GOOD_TOKEN)
        assert code == 401

    def test_the_401_carries_a_WWW_Authenticate_challenge(self, store: Path):
        with running(store) as (base, _):
            _c, headers, _b = fetch(f"{base}/api/v1/recall/{SCOPE}")
        assert headers["WWW-Authenticate"].startswith("Bearer ")


class TestUniform401:
    """🔴 AN ERROR THAT DISCRIMINATES IS AN ENUMERATION API (§2b).

    An unauthenticated caller must not be able to learn which scopes exist, which
    refs exist, or which URLs are routes, by reading the differences between
    rejections. So every rejection is byte-identical — body, code AND header set.
    """

    def _reject(self, base: str, path: str):
        return fetch(f"{base}{path}", token="w" * 48)

    def test_bad_token_unknown_scope_and_unknown_ref_are_INDISTINGUISHABLE(
        self, store: Path
    ):
        with running(store) as (base, _):
            known = self._reject(base, f"/api/v1/recall/{SCOPE}")
            unknown_scope = self._reject(base, "/api/v1/recall/no-such-scope-anywhere")
            unknown_ref = self._reject(base, f"/api/v1/recall/{SCOPE}?ref=no-such-ref")
            not_a_route = self._reject(base, "/api/v1/nonsense/whatever")
            not_api = self._reject(base, "/admin")

        responses = [known, unknown_scope, unknown_ref, not_a_route, not_api]
        codes = {r[0] for r in responses}
        bodies = {r[2] for r in responses}
        headers = {_comparable(r[1]) for r in responses}
        assert codes == {401}
        assert bodies == {b"unauthorized\n"}
        # One header shape across all five. `Content-Length` is part of it, so a
        # body that leaked a scope name would show up here even if the assertion
        # above were somehow satisfied.
        assert len(headers) == 1, f"401s differ in headers: {headers}"

    def test_the_401_body_names_no_scope_no_ref_and_no_path(self, store: Path):
        with running(store) as (base, _):
            _c, _h, body = self._reject(base, f"/api/v1/recall/{SCOPE}?ref=thing-alpha")
        text = body.decode()
        assert SCOPE not in text
        assert "thing-alpha" not in text
        assert "scope" not in text.lower()


class TestConstantTimeComparison:
    """§2b: "Constant-time token comparison … a `==` on a secret is a timing
    oracle that a public endpoint makes practically exploitable."

    🔴 STRUCTURAL **AND** BEHAVIOURAL, because neither alone holds. A structural
    check ("it calls compare_digest") type-checks past a call with the wrong
    arguments; a behavioural check (right token in, wrong token out) is passed
    just as well by `==`. Both, or the guard is walkable.
    """

    def test_STRUCTURAL_authorize_delegates_to_hmac_compare_digest(self, monkeypatch):
        seen: list[tuple] = []
        real = api.hmac.compare_digest

        def spy(a, b):
            seen.append((a, b))
            return real(a, b)

        monkeypatch.setattr(api.hmac, "compare_digest", spy)
        api.authorize(f"Bearer {GOOD_TOKEN}", (GOOD_TOKEN,))
        assert len(seen) == 1
        # 🔴 And with the RIGHT arguments, in the right order: presented first,
        # expected second, both as bytes. A spy that only counted calls would be
        # green for `compare_digest(expected, expected)`, which always says yes.
        assert seen[0] == (GOOD_TOKEN.encode(), GOOD_TOKEN.encode())

    def test_BEHAVIOURAL_it_accepts_the_right_token_and_rejects_a_near_miss(self):
        api.authorize(f"Bearer {GOOD_TOKEN}", (GOOD_TOKEN,))  # no raise
        with pytest.raises(api._Rejected):
            api.authorize(f"Bearer {GOOD_TOKEN[:-1]}X", (GOOD_TOKEN,))
        with pytest.raises(api._Rejected):
            api.authorize(None, (GOOD_TOKEN,))

    def test_a_PREFIX_of_the_token_is_rejected(self):
        with pytest.raises(api._Rejected):
            api.authorize(f"Bearer {GOOD_TOKEN[:10]}", (GOOD_TOKEN,))

    def test_the_source_contains_no_equality_test_against_the_token(self):
        # A cheap second reading of the same property. It is NOT the guard —
        # `test_STRUCTURAL_...` is — but a `==` reintroduced during a refactor
        # would leave the spy green if the spy call were also left in place.
        src = SERVER_PATH.read_text()
        assert "== expected" not in src
        assert "expected ==" not in src
        assert "hmac.compare_digest" in src


# =============================================================================
# 3. The health endpoint says NOTHING.
# =============================================================================


class TestHealthSaysNothing:
    """§2b: "Health endpoint stays unauthenticated but says nothing — `200 ok`,
    no version, no scope count, no store revision."
    """

    def test_it_answers_without_a_token(self, store: Path):
        with running(store) as (base, _):
            code, _h, body = fetch(f"{base}/healthz")
        assert code == 200
        assert body == b"ok\n"

    def test_it_reveals_no_scope_count_no_revision_and_no_store_path(self, store: Path):
        with running(store) as (base, _):
            _c, headers, body = fetch(f"{base}/healthz")
        text = body.decode()
        assert SCOPE not in text and str(store) not in text
        assert "X-Store-Status" not in headers
        assert "X-Store-Revision" not in headers

    def test_it_does_not_leak_the_python_version_in_the_Server_header(self, store: Path):
        with running(store) as (base, _):
            _c, headers, _b = fetch(f"{base}/healthz")
        assert headers["Server"] == "subsystem-store"
        assert "Python" not in headers["Server"]


# =============================================================================
# 4. 🔴 THE FOUR STATES. This is the defect class the whole design exists to
#    avoid: an unreachable store rendering as "nothing recorded yet".
# =============================================================================


class TestFourStates:
    """§3: `scope-empty` and `store-unreachable` must NEVER render alike.

    The reader already refuses to conflate them (`load_store` raises rather than
    returning an empty index). This layer's job is not to throw that away by
    catching everything into one cheerful 200.
    """

    def test_recalled_is_200_with_content(self, store: Path):
        with running(store) as (base, _):
            code, headers, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert (code, headers["X-Store-Status"], headers["X-Store-Exit"]) == (
            200,
            "recalled",
            "0",
        )
        assert NUANCE_LINE.encode() in body

    def test_scope_absent_is_200_and_says_NOTHING_RECORDED_YET(self, store: Path):
        with running(store) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/never-heard-of-it", token=GOOD_TOKEN
            )
        assert code == 200
        assert headers["X-Store-Status"] == "scope-absent"
        assert b"NOTHING RECORDED YET" in body

    def test_scope_empty_is_200_and_the_store_WAS_read(self, store: Path):
        with running(store) as (base, _):
            code, headers, _b = fetch(
                f"{base}/api/v1/recall/{EMPTY_SCOPE}", token=GOOD_TOKEN
            )
        assert code == 200
        assert headers["X-Store-Status"] == "scope-empty"
        assert headers["X-Store-Exit"] == "0"

    def test_store_unreachable_is_503_and_NOT_a_200(self, tmp_path: Path):
        # The store root does not exist. Nothing was read, so nothing may be
        # concluded — and a 200 is a claim that the store WAS read.
        with running(tmp_path / "absent") as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{EMPTY_SCOPE}", token=GOOD_TOKEN
            )
        assert code == 503
        assert headers["X-Store-Status"] == "store-unreachable"
        assert headers["X-Store-Exit"] == "3"
        assert b"NOT 'nothing recorded yet'" in body

    def test_scope_empty_and_store_unreachable_SHARE_NOTHING(
        self, store: Path, tmp_path: Path
    ):
        """🔴 THE DISCRIMINATOR, asserted as a difference rather than two facts.

        Two separate assertions elsewhere in this class can both pass while the
        two states still render alike to a caller that reads only one field.
        This one fails if they agree on the code, the status header OR the body.
        """
        with running(store) as (base, _):
            empty = fetch(f"{base}/api/v1/recall/{EMPTY_SCOPE}", token=GOOD_TOKEN)
        with running(tmp_path / "absent") as (base, _):
            gone = fetch(f"{base}/api/v1/recall/{EMPTY_SCOPE}", token=GOOD_TOKEN)

        assert empty[0] != gone[0], "same HTTP status code"
        assert empty[1]["X-Store-Status"] != gone[1]["X-Store-Status"]
        assert empty[2] != gone[2], "same body"
        # And specifically: the unreachable one must NOT be the reassuring text.
        assert b"NOTHING RECORDED YET" not in gone[2]

    def test_an_unreadable_scope_is_a_FIFTH_state_not_folded_into_empty(
        self, store: Path
    ):
        # Every file in the scope is malformed. The store WAS reached (200), but
        # nothing in this scope could be read — exit 3, and the body says so.
        with running(store) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{BROKEN_SCOPE}", token=GOOD_TOKEN
            )
        assert code == 200
        assert headers["X-Store-Status"] == "scope-unreadable"
        assert headers["X-Store-Exit"] == "3"
        assert b"NOTHING RECORDED YET" not in body


# =============================================================================
# 5. The reader's degradations survive the HTTP layer.
# =============================================================================


class TestMalformedDegradationSurvivesHTTP:
    """A scope with one bad entry still serves the good ones AND names the reject.

    Fail-closed here cost the whole scope once already (2 good entries and 1 bad
    one served ZERO); the reader was changed to degrade instead. An HTTP layer
    that turned a partial read into a 500 would undo that silently.
    """

    def test_two_good_entries_and_one_reject_serve_BOTH_and_NAME_the_reject(
        self, store: Path
    ):
        (store / SCOPE / "thing-delta.md").write_text(
            _entry("thing-delta", SCOPE, nuance="- 2026-01-04: a second good entry.")
        )
        (store / SCOPE / "thing-wrecked.md").write_text("not front matter\n")
        with running(store) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}?mode=list", token=GOOD_TOKEN
            )
        text = body.decode()
        assert code == 200
        assert headers["X-Store-Exit"] == "0", "a partial read still served content"
        assert "thing-alpha" in text and "thing-delta" in text
        assert "thing-wrecked" in text
        assert "MALFORMED" in text

    def test_the_pod_LOG_counts_the_real_rejects_not_an_empty_tuple(
        self, store: Path, capfd
    ):
        """`_exit_for` writes the CLI's own one-line summary to stderr, which in
        a pod is the log. It takes the malformed tuple, so handing it an empty
        one would print "all 0 entry files are MALFORMED" — a sentence that is
        both false and reassuring, on the one status that exists to report
        rejects. Reached with the all-malformed scope, since that is the only
        status `_exit_for` prints for at all.
        """
        (store / BROKEN_SCOPE / "thing-shattered.md").write_text("also not front matter\n")
        capfd.readouterr()
        with running(store) as (base, _):
            code, headers, _b = fetch(
                f"{base}/api/v1/recall/{BROKEN_SCOPE}", token=GOOD_TOKEN
            )
        err = capfd.readouterr().err
        assert (code, headers["X-Store-Exit"]) == (200, "3")
        assert "all 2 entry files" in err, err


class TestSensitivityFailSafeSurvivesHTTP:
    """An absent or unknown sensitivity marker folds to `client-confidential`.

    🔴 The fail-safe is the reason it is safe to serve this store at all. A
    rendering path that dropped it would hand an entry to a caller with no mark
    on it, and unmarked reads as unrestricted.
    """

    @pytest.mark.parametrize(
        "marker", [None, "totally-made-up-level", "", "public-ish"]
    )
    def test_an_absent_or_unknown_marker_reads_client_confidential(
        self, store: Path, marker
    ):
        (store / SCOPE / "thing-unmarked.md").write_text(
            _entry("thing-unmarked", SCOPE, sensitivity=marker)
        )
        with running(store) as (base, _):
            code, _h, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}?mode=list", token=GOOD_TOKEN
            )
        assert code == 200
        text = body.decode()
        assert "thing-unmarked" in text
        # The fold is per-entry; find the row and read ITS label, not the
        # caveat's generic sentence, which would be a spelled guard satisfied by
        # prose that has nothing to do with this entry.
        row = next(ln for ln in text.splitlines() if "thing-unmarked" in ln)
        assert "client-confidential" in row


# =============================================================================
# 6. Phase 1 is READ-ONLY, and that is enforced rather than documented.
# =============================================================================


class TestReadOnlyPhase1:
    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_every_write_method_is_405_even_with_a_VALID_token(
        self, store: Path, method: str
    ):
        with running(store) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, method=method
            )
        assert code == 405
        assert headers["Allow"] == "GET, HEAD"
        assert body == b"read-only\n"

    def test_a_full_read_workload_leaves_the_store_BYTE_IDENTICAL(self, store: Path):
        """Behavioural, not a grep for `open(..., "w")`.

        A spelled guard on the source would be satisfied by a write performed
        through any other spelling; hashing the tree is not.
        """
        before = tree_hash(store)
        with running(store) as (base, _):
            for path in (
                f"/api/v1/recall/{SCOPE}",
                f"/api/v1/recall/{SCOPE}?mode=list",
                f"/api/v1/recall/{SCOPE}?mode=full&limit=5",
                f"/api/v1/recall/{SCOPE}?ref=thing-alpha",
                f"/api/v1/recall/{BROKEN_SCOPE}",
                f"/api/v1/search/{SCOPE}?q=readiness+probe",
                "/api/v1/recall/never-heard-of-it",
                "/healthz",
            ):
                fetch(f"{base}{path}", token=GOOD_TOKEN)
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, method="POST")
        assert tree_hash(store) == before

    def test_the_hasher_can_SEE_a_change(self, store: Path):
        """Positive control for the assertion above. A hasher wired to nothing
        reports "unchanged" for a tree that was rewritten wholesale."""
        before = tree_hash(store)
        (store / SCOPE / "thing-alpha.md").write_text(
            _entry("thing-alpha", SCOPE, nuance="- 2026-01-09: moved.")
        )
        assert tree_hash(store) != before


# =============================================================================
# 7. Search, and query parameters that must not silently default.
# =============================================================================


# 🔴 THE PINNED DECISION TABLE, as a named constant so the completeness guard
# and the per-cell guard read the SAME list. Introspecting the parametrize mark
# to get it was clever and wrong; two literals would drift.
#
# THREE contexts now: the `/snapshot` scope-root scan, the `/snapshot` entry
# scan, and — since the entry-kind guard landed — `subsystem_resolver`'s INDEX
# LOADER, which is a different context and therefore a different column.
#
# 🔴 THE LOADER COLUMN IS THE NARROW RULING, CELL BY CELL. Every REFUSE in it
# rests on ONE criterion, applied over three rulings and never widened: this
# loader has never successfully READ that kind — it has only ever raised or
# blocked on one — so refusing it changes no legitimate caller. That covers
# `broken-link` (the Emacs `.#entry.md` lock file, which 503'd the whole store),
# the two shapes of "an `open()` on this never returns" — `other` (the fifo) and
# `link-to-other` (a symlink pointing at one), each measured HANGING the request
# thread — and the two shapes of `IsADirectoryError`, `directory` and
# `link-to-dir`, measured 503ing the whole store off one stray `mkdir`.
#
# It TAKES everything else — most pointedly `link-to-file`, which the loader has
# always read. Copying the `_ENTRY_ACTIONS` column wholesale is the over-broad
# form that was explicitly rejected, and flipping any remaining TAKE here to
# REFUSE is a behaviour change for ordinary callers; every one of those flips is
# a mutant this column kills.
DECISION_TABLE = [
    ("KIND_BROKEN_LINK", "REFUSE", "REFUSE", "REFUSE"),
    # 🔴 CLOSED IN THE SAME ROUND AS `KIND_DIRECTORY`, AND FOR ITS REASON —
    # `read_text` raises `IsADirectoryError` whether the directory is named
    # directly or reached through a link, and that OSError fails closed into a
    # store-wide 503 for every caller.
    ("KIND_LINK_TO_DIR", "REFUSE", "REFUSE", "REFUSE"),
    ("KIND_LINK_TO_FILE", "SKIP", "REFUSE", "TAKE"),
    # 🔴 CLOSED, AND IT WAS THE SAME DEFECT — NOT A LESSER ONE. This cell was
    # `TAKE` for one round, pinned as a named residual because the ruling that
    # created the loader column named `other` and `broken-link` and nothing
    # else. Then it was MEASURED on that tip: a `link-to-fifo.md` symlink in a
    # scope the caller never asked for wedged `GET /api/v1/recall/<other-scope>`
    # for 25s under an UNRESTRICTED legacy token — the request thread gone, on a
    # `replicas: 1` / `strategy: Recreate` service. `open()` blocks identically
    # whether the fifo is reached directly or through a link, so the loader
    # column now refuses BOTH shapes of it. `link-to-file` stays `TAKE` — that
    # is still the upper bound, and still the point of the narrow form.
    ("KIND_LINK_TO_OTHER", "SKIP", "REFUSE", "REFUSE"),
    # 🔴 CLOSED, AND IT WAS NEVER A LESSER DEFECT EITHER. The loader TOOK this
    # cell for three rounds while its own ledger called `chmod 000` "the only
    # residual left" — which was measured false: `store/beta/notes.md` created
    # as a DIRECTORY answered `GET /api/v1/recall/alpha` (a scope the caller
    # never asked about, unrestricted legacy token) with `503 index entry
    # unreadable … IsADirectoryError`, while the same store carrying a dangling
    # `.#lock.md` instead answered 200. One accidental `mkdir <scope>/notes.md`
    # or one rsync/restore artefact took `/recall` and `/search` down for EVERY
    # caller. `read_text` has never once succeeded on a directory, so refusing
    # it changes no legitimate caller — the criterion the narrow ruling itself
    # used — and it makes the loader agree with `/snapshot`, which already
    # refused it.
    ("KIND_DIRECTORY", "TAKE", "REFUSE", "REFUSE"),
    ("KIND_REGULAR_FILE", "SKIP", "TAKE", "TAKE"),
    ("KIND_OTHER", "SKIP", "REFUSE", "REFUSE"),
    # 🔴 "I could not look" must never share a cell with "nothing is there".
    # In the LOADER it is TAKE, so `read_text` raises and the four-state rule's
    # "the store was not fully READ" is preserved — a DIFFERENT fact from "this
    # entry is malformed", which is what REFUSE would report it as. ⚠ This is
    # the cell the `directory` ruling deliberately did NOT sweep up with it:
    # "the lstat failed" is a different premise from "this kind can never be an
    # entry", and only the second justifies a REFUSE.
    ("KIND_INDETERMINATE", "REFUSE", "REFUSE", "TAKE"),
    ("KIND_ABSENT", "SKIP", "SKIP", "TAKE"),
]

# 🔴 THE LOADER'S RESIDUAL SET, PINNED AS A LITERAL — the set of kinds
# `_LOADER_ENTRY_ACTIONS` still maps to TAKE, which is exactly the set of shapes
# whose `read_text` can still fail closed into a store-wide 503.
#
# It is spelled here, by hand, and NOT derived from the table, because the whole
# defect this pins is a LEDGER that drifted: two documents claimed `chmod 000`
# was "the only residual" while the table had four TAKE cells that could raise,
# one of which (`directory`) took the store down for every caller. A derived set
# would have agreed with the table forever and said nothing.
LOADER_RESIDUAL_KINDS = {
    "regular-file",   # chmod 000 -> PermissionError
    "link-to-file",   # a link whose TARGET is chmod 000 -> PermissionError
    "indeterminate",  # the lstat itself failed -> OSError on read
    "absent",         # vanished between glob() and classify -> FileNotFoundError
}

# The LEDGER of every action table that exists, so "all three contexts" is a
# claim something checks rather than a number in a test name.
ALL_ACTION_TABLES = [
    ("subsystem_store_server._ROOT_ACTIONS", api._ROOT_ACTIONS),
    ("subsystem_store_server._ENTRY_ACTIONS", api._ENTRY_ACTIONS),
    ("subsystem_resolver._LOADER_ENTRY_ACTIONS", resolver._LOADER_ENTRY_ACTIONS),
]


class TestClassifierIsTotal:
    """🔴 THE GUARD THAT IS SUPPOSED TO END THE ROUND-N LOOP.

    Four audit rounds found the same defect shape in `_snapshot`, each time in a
    NEW input class that the previous round's sequence of `if`s did not decide:
    symlinked entries, symlinked scope dirs, symlinked non-scopes, then dangling
    links and symlink loops. Every fix added an arm; none made the rule total,
    so the next class fell through the same gap and rendered as `scope-empty —
    nothing recorded` at exit 0.

    These tests pin the classification itself rather than its instances:
      1. every path lands in exactly one kind (totality of `classify_path`);
      2. every kind is mapped explicitly in BOTH contexts (no default);
      3. the fallthrough RAISES, so an unmapped kind is a failure, not a skip.

    Adding a kind therefore breaks (2) until somebody decides, per context,
    whether it is TAKE, SKIP or REFUSE — which is the decision the last four
    rounds each made implicitly, by omission, and got wrong.
    """

    def test_every_kind_is_mapped_in_ALL_THREE_contexts(self):
        for name, actions in ALL_ACTION_TABLES:
            missing = api.ALL_KINDS - set(actions)
            extra = set(actions) - api.ALL_KINDS
            assert not missing, f"{name} does not decide: {sorted(missing)}"
            assert not extra, f"{name} maps unknown kinds: {sorted(extra)}"

    def test_the_TABLE_LEDGER_names_every_action_table_that_exists(self):
        """🔴 A SEAM GUARD, NOT A TOTALITY ONE — the test above is only as wide
        as the list it iterates.

        A fourth context that maps kinds would be structurally invisible to
        every assertion in this class: nothing here reads the modules, so a new
        table simply would not be checked, and "every kind is mapped in all
        three contexts" would keep passing while the fourth defaulted. This
        asserts the LEDGER against what the two modules actually define, so the
        set GROWING or SHRINKING is a failure either way.

        🔴 SELECTED BY WHAT THE DICT CONTAINS, NEVER BY WHAT IT IS CALLED. This
        guard used to match `name.endswith("_ACTIONS")`, which made it a SPELLED
        guard while its docstring claimed a structural one — and the difference
        was measured with paired controls: a fourth table named
        `_FOURTH_TABLE_ACTIONS` was KILLED, the byte-identical table renamed
        `_SNAPSHOT_KIND_POLICY` SURVIVED (19 passed, 0 failed). A new context
        does not have to adopt the old suffix, and the one that does not is
        exactly the one nobody notices.

        So the question asked is what the dict IS, not what it is called: KEYS
        that are kinds, VALUES that are actions. Both halves are load-bearing —
        keys alone also matches `_LOADER_REFUSAL_REASON`, which is kind-keyed
        but maps to English sentences and decides nothing. Naming is then free.
        """
        found = {
            f"{mod.__name__}.{name}"
            for mod in (api, resolver)
            for name, value in vars(mod).items()
            if isinstance(value, dict)
            and set(value) & api.ALL_KINDS
            and set(value.values()) <= {api.SKIP, api.TAKE, api.REFUSE}
        }
        assert found == {name for name, _t in ALL_ACTION_TABLES}, (
            f"the action-table ledger is out of date: {sorted(found)}"
        )

    def test_the_LOADER_RESIDUAL_SET_is_pinned(self):
        """🔴 THE LEDGER THAT KEPT GOING STALE, MADE MACHINE-READABLE.

        `load_index`'s docstring and the API README each asserted, for three
        rounds, that a `chmod 000` regular file was "the only residual left" —
        while the table held FOUR `TAKE` cells whose `read_text` fails closed
        into a store-wide 503, one of them (`directory`) reachable from a single
        accidental `mkdir <scope>/notes.md` and measured taking `/recall` down
        for every caller.

        ⚠ AN INVARIANT GUARD, NOT A REGRESSION TEST — nothing was ever
        structurally wrong with the TABLE this asserts; the defect was in the
        two documents describing it, and the two guards below are the ones that
        pin those. This exists so that whatever is ruled next about a cell has
        to move a literal a reviewer can see, in the same diff.
        """
        taken = {
            k for k, a in resolver._LOADER_ENTRY_ACTIONS.items() if a == api.TAKE
        }
        assert taken == LOADER_RESIDUAL_KINDS, (
            "the loader's TAKE set moved. Every kind here is a shape whose "
            "`read_text` can still 503 the whole store, so this set IS the "
            "residual ledger — update `load_index`'s docstring and "
            f"subsystem-store-api/README.md in the same diff. now={sorted(taken)}"
        )

    @staticmethod
    def _marked_region(
        text: str, label: str, start: str, end: str, min_len: int
    ) -> str:
        """A document region delimited by two literal markers.

        An absent or unterminated marker ASSERTS rather than returning an empty
        slice — an empty region would satisfy every "is not in" below and turn
        the guard using it into the reassuring zero it exists to prevent. So
        does a region that came back implausibly short.
        """
        lo = text.find(start)
        assert lo > 0, f"{label}: no {start} section at all"
        hi = text.find(end, lo + len(start))
        assert hi > lo, f"{label}: the {start} region is not terminated by {end!r}"
        region = text[lo:hi]
        assert len(region) > min_len, (
            f"{label}: the {start} region is suspiciously short ({len(region)}b)"
        )
        return region

    @classmethod
    def _residual_ledger(cls, label: str) -> str:
        """The document's RESIDUAL LEDGER region, by its literal markers."""
        text = (
            resolver.load_index.__doc__
            if "load_index" in label
            else (API_DIR / label).read_text(encoding="utf-8")
        )
        return cls._marked_region(
            text, label, "RESIDUAL LEDGER", "END OF RESIDUAL LEDGER", min_len=200
        )

    @pytest.mark.parametrize(
        "label", ["subsystem_resolver.load_index", "README.md"]
    )
    def test_the_RESIDUAL_LEDGER_names_every_TAKE_kind_and_no_REFUSE_one(
        self, label
    ):
        """🔴 THE GUARD THAT WOULD HAVE CAUGHT THE STALE LEDGER, IN BOTH
        DIRECTIONS AND IN BOTH DOCUMENTS.

        The previous round flipped `link-to-other` to REFUSE and its commit
        message claimed "comments and docstrings updated wherever they still
        recorded the hang as open". The docstring and two test docstrings were;
        the README was NOT, and went on telling operators the hang was "left
        open" for a whole round after it was closed. Nothing could see that,
        because no test read the README.

        So both directions are asserted, and a document that fails either is
        wrong in a way a reader would act on:
          * a kind the table still `TAKE`s and the ledger omits -> the ledger
            reads SHORTER than the hazard actually is;
          * a kind the table now REFUSEs and the ledger still lists -> a closed
            hazard recorded as open, which is what happened.
        And symmetrically for the REFUSED table above the ledger.

        ⚠ THIS PINS TOKENS, NOT PROSE. A backticked kind name is a
        machine-readable claim; the sentences around it are free to be
        rewritten. That is the deliberate trade — pinning the whole normalised
        string would fail on every cosmetic edit and get deleted.
        """
        ledger = self._residual_ledger(label)
        taken = {
            k for k, a in resolver._LOADER_ENTRY_ACTIONS.items() if a == api.TAKE
        }
        refused = {
            k for k, a in resolver._LOADER_ENTRY_ACTIONS.items() if a == api.REFUSE
        }
        assert taken and refused, "the loader table is degenerate"

        for kind in sorted(taken):
            assert f"`{kind}`" in ledger, (
                f"{label}: the residual ledger does not name `{kind}`, which "
                f"the loader still TAKEs — the ledger reads SHORTER than the "
                f"hazard is"
            )
        for kind in sorted(refused):
            assert f"`{kind}`" not in ledger, (
                f"{label}: `{kind}` is still described as a residual, but the "
                f"loader now REFUSEs it — a closed hazard recorded as open is "
                f"the exact drift this guard exists for"
            )

    def test_the_DOCSTRING_REFUSED_SET_names_every_REFUSE_kind_and_no_other(self):
        """🔴 THE HALF THE GUARD BELOW CLAIMED AND DID NOT COVER — a docstring
        whose SENTENCE promised more coverage than its BODY delivered.

        `test_the_README_REFUSED_TABLE_…` used to say "`load_index`'s docstring
        prose is covered by the ledger half above". It was not. The ledger guard
        reads only the span between `RESIDUAL LEDGER` and its END marker, and
        the docstring's REFUSAL sentence lives well above that span — so nothing
        in the tree read it. `resolver.load_index.__doc__` appeared exactly once
        in the whole test suite, inside `_residual_ledger`.

        Measured, on the state this fix replaced: dropping `link-to-other`,
        `directory` and `link-to-dir` from that sentence — leaving the docstring
        asserting a TWO-cell guard against a FIVE-cell table, verbatim the shape
        of the README drift the ledger guard exists for — left the suite at
        **407 passed**.

        So the sentence now names its kinds as backticked TOKENS inside literal
        markers, and this pins the marked span against the table in both
        directions, exactly as the two guards around it do:
          * a kind the loader REFUSEs and the sentence omits -> the docstring
            claims a NARROWER guard than the code has, which is what a reader
            deletes a "redundant" cell on the strength of;
          * a kind the sentence names that the loader TAKEs -> a guard claimed
            where none exists, which is the README failure one document over.

        ⚠ TOKENS, NOT PROSE — same trade as the ledger guard. The explanatory
        clause beside each kind is free to be rewritten; the backticked name is
        the machine-readable claim. And the span is delimited tightly on
        purpose: the paragraph BELOW it names `link-to-other`, `directory` and
        `link-to-dir` while recounting which round each shipped in, so a guard
        scoped to the whole docstring would have passed on all three while the
        refusal sentence itself said nothing about them.
        """
        refused = {
            k for k, a in resolver._LOADER_ENTRY_ACTIONS.items() if a == api.REFUSE
        }
        assert refused, "the loader refuses nothing — the table is degenerate"
        region = self._marked_region(
            resolver.load_index.__doc__ or "",
            "subsystem_resolver.load_index",
            "REFUSED SET",
            "END OF REFUSED SET",
            min_len=120,
        )
        for kind, action in sorted(resolver._LOADER_ENTRY_ACTIONS.items()):
            present = f"`{kind}`" in region
            if action == api.REFUSE:
                assert present, (
                    f"`load_index`'s REFUSED SET does not name `{kind}`, which "
                    f"the loader refuses — the docstring claims a narrower "
                    f"guard than the code has"
                )
            else:
                assert not present, (
                    f"`load_index`'s REFUSED SET names `{kind}`, which the "
                    f"loader does not REFUSE (it is {action!r}) — it claims a "
                    f"guard that is not there"
                )

    def test_the_README_REFUSED_TABLE_names_every_REFUSE_kind_and_no_other(self):
        """The other half of the same seam, and the half an operator reads
        first: the README's refusal table is what tells them which shapes are
        already handled. A kind missing from it reads as an open hazard that is
        closed; a kind listed in it that the loader actually TAKEs reads as a
        guard that does not exist.

        Scoped to the README's refusal TABLE — not to everything above the
        ledger marker, which is what it used to be. `text[:cut]` swept in every
        line of the document before the ledger, so an unrelated future table
        with a `regular-file` row would have failed here with a message
        pointing at the refusal table, which is not where the edit is. The
        docstring's own refusal sentence is pinned by the guard ABOVE, not by
        this one — it is a different document and it says it differently.
        """
        text = (API_DIR / "README.md").read_text(encoding="utf-8")
        # The table's own header line is the anchor, and a blank line ends it —
        # the narrowest span that can be wrong. An absent header ASSERTS rather
        # than yielding a slice: `find` returns -1 on a miss, and `text[-1:]`
        # is a one-character region that satisfies every "is not in" below.
        head = self._marked_region(
            text, "README.md", "| kind | on disk | why it is refused |", "\n\n", min_len=200
        )
        for kind, action in sorted(resolver._LOADER_ENTRY_ACTIONS.items()):
            present = f"| `{kind}` |" in head
            if action == api.REFUSE:
                assert present, (
                    f"README refusal table does not list `{kind}`, which the "
                    f"loader refuses"
                )
            else:
                assert not present, (
                    f"README refusal table lists `{kind}`, which the loader "
                    f"TAKEs — it claims a guard that is not there"
                )

    def test_the_pinned_table_covers_EVERY_kind(self):
        """🔴 Two-way pin on the parametrize list itself.

        `test_the_decision_table_is_pinned`'s docstring says "a silent flip of
        any single cell fails", but nothing asserted its parametrize list
        covered `ALL_KINDS` — so a future kind, mapped correctly in both dicts,
        would have an UNPINNED cell while the docstring claimed otherwise. Same
        idiom as `test_waiting_windows.py`'s `set(KIND_BAND) == set(ALL_KINDS)`.
        """
        pinned = {getattr(api, name) for name, _r, _e, _l in DECISION_TABLE}
        assert pinned == api.ALL_KINDS, (
            f"unpinned cells: {sorted(api.ALL_KINDS - pinned)}"
        )

    def test_an_unstatable_ROOT_child_is_REFUSED_by_the_CLASSIFIER(
        self, store: Path, tmp_path: Path
    ):
        """🔴 The cell this whole commit exists for had only a CONSTANTS pin —
        the shape `TestEntryTableCellsHaveBehaviour`'s own docstring calls
        insufficient ("exactly the kind a future edit updates ALONGSIDE the code
        it was meant to stop"). Three ENTRY cells got behavioural tests; the
        ROOT cell did not.

        🔴 IT ASSERTS THE MESSAGE, NOT THE STATUS, AND THAT IS THE WHOLE POINT.
        With the store root at 0o600 the tar block ALSO fails (on `.seed-stamp`),
        so a test asserting only `503` passes even with this cell flipped to
        SKIP — green for the wrong reason. The classifier's refusal is emitted
        BEFORE the tar block and names the kind, so pinning `indeterminate
        refused` is what discriminates. Verified by mutation, not assumed.
        """
        if os.geteuid() == 0:
            pytest.skip("root ignores directory permissions; unreachable as root")
        store.chmod(0o600)  # readable, NOT searchable -> every child lstat EACCES
        try:
            with running(store) as (base, _):
                code, _h, body = fetch(f"{base}/api/v1/snapshot", token=GOOD_TOKEN)
        finally:
            store.chmod(0o755)
        assert code == 503, body[:300]
        assert b"indeterminate refused" in body, body[:300]

    def test_every_mapped_action_is_a_known_action(self):
        for name, actions in ALL_ACTION_TABLES:
            for kind, action in actions.items():
                assert action in (api.SKIP, api.TAKE, api.REFUSE), (name, kind, action)

    def test_an_unmapped_kind_RAISES_rather_than_defaulting(self):
        """🔴 The fallthrough is the whole mechanism. If `action_for` returned a
        default, adding a kind would silently inherit SKIP — which is exactly
        how a dangling scope link became `scope-empty` at exit 0."""
        with pytest.raises(AssertionError, match="unclassified path kind"):
            api.action_for("a-kind-nobody-mapped", api._ROOT_ACTIONS)

    @pytest.mark.parametrize(
        "kind,expected_root,expected_entry,expected_loader", DECISION_TABLE
    )
    def test_the_decision_table_is_pinned(
        self, kind, expected_root, expected_entry, expected_loader
    ):
        """Pins the table itself, so a silent flip of any single cell fails.

        Every previous round's bug was one cell of this table being wrong or
        absent; asserting the table makes each cell a named, reviewable decision
        instead of an emergent property of statement order.

        ⚠ A CONSTANTS PIN IS NOT A BEHAVIOUR TEST, and this class's own history
        says so — three ENTRY cells needed behavioural tests before they were
        believed. The two loader cells that DO something have them below
        (`TestTheLoaderRefusesHostileEntriesByKind`); this asserts the decision
        is written down, not that it fires.
        """
        k = getattr(api, kind)
        assert api._ROOT_ACTIONS[k] == getattr(api, expected_root)
        assert api._ENTRY_ACTIONS[k] == getattr(api, expected_entry)
        assert resolver._LOADER_ENTRY_ACTIONS[k] == getattr(api, expected_loader)

    def test_every_REFUSED_loader_kind_HAS_a_reason_and_no_other_kind_does(self):
        """🔴 THE SEAM BETWEEN THE TWO DICTS, which nothing else reads together.

        `load_index` looks the reason up by `_LOADER_REFUSAL_REASON[kind]` —
        an unguarded subscript — so flipping a cell to REFUSE without writing
        its sentence turns a hostile entry into a `KeyError` out of the loader:
        a 500 with no `X-Store-Status`, for a shape whose whole fix was to make
        it a NAMED malformed row. That is how the `link-to-other` cell would
        have landed, and reviewing the table alone cannot see it.

        Asserted as SET EQUALITY, not containment, so it fails when the ledger
        GROWS as well as when it shrinks — a reason left behind for a kind that
        went back to TAKE is dead prose claiming a guard that is gone.
        """
        refused = {
            k
            for k, action in resolver._LOADER_ENTRY_ACTIONS.items()
            if action == api.REFUSE
        }
        assert refused == set(resolver._LOADER_REFUSAL_REASON), (
            "the loader's REFUSE cells and its refusal sentences have drifted"
        )
        assert refused == {
            api.KIND_BROKEN_LINK,
            api.KIND_OTHER,
            api.KIND_LINK_TO_OTHER,
            api.KIND_DIRECTORY,
            api.KIND_LINK_TO_DIR,
        }

    def test_classify_returns_the_right_kind_for_each_REAL_path(self, tmp_path: Path):
        """🔴 The table above is only meaningful if `classify_path` actually
        produces these kinds from real filesystem objects. Built with `os.mkfifo`
        and real symlinks — not mocks — because the whole bug class came from
        `pathlib` predicates DEREFERENCING in ways a mock would not reproduce.
        """
        (tmp_path / "plain.md").write_text("x")
        (tmp_path / "adir").mkdir()
        (tmp_path / "to_file").symlink_to(tmp_path / "plain.md")
        (tmp_path / "to_dir").symlink_to(tmp_path / "adir", target_is_directory=True)
        (tmp_path / "dangling").symlink_to(tmp_path / "nope")
        (tmp_path / "loop").symlink_to(tmp_path / "loop")
        os.mkfifo(tmp_path / "afifo")
        (tmp_path / "to_fifo").symlink_to(tmp_path / "afifo")

        assert api.classify_path(tmp_path / "plain.md") == api.KIND_REGULAR_FILE
        assert api.classify_path(tmp_path / "adir") == api.KIND_DIRECTORY
        assert api.classify_path(tmp_path / "to_file") == api.KIND_LINK_TO_FILE
        assert api.classify_path(tmp_path / "to_dir") == api.KIND_LINK_TO_DIR
        # 🔴 The r4 regression lived here: `is_dir()` is False for BOTH of these,
        # so the old code skipped them and the scope read as empty.
        assert api.classify_path(tmp_path / "dangling") == api.KIND_BROKEN_LINK
        assert api.classify_path(tmp_path / "loop") == api.KIND_BROKEN_LINK
        assert api.classify_path(tmp_path / "afifo") == api.KIND_OTHER
        assert api.classify_path(tmp_path / "to_fifo") == api.KIND_LINK_TO_OTHER
        assert api.classify_path(tmp_path / "never-existed") == api.KIND_ABSENT

    def test_an_UNSTATABLE_path_is_INDETERMINATE_not_OTHER(self, tmp_path: Path):
        """🔴 The last cell of the four-round loop.

        Every pathlib predicate returns False when the stat itself fails, so an
        EACCES child fell into KIND_OTHER — the FIFO bucket — and was SKIPPED at
        the root. MEASURED end-to-end before this fix, store root at 0o600
        (readable, not searchable, so readdir works and every lstat gives
        EACCES): snapshot answered 200 / X-Store-Exit: 0 / entries=0, and the
        client printed "scope-empty — nothing recorded". An unreadable store
        rendering as an empty one, which is the defect this client exists for.
        """
        if os.geteuid() == 0:
            pytest.skip("root ignores directory permissions; unreachable as root")
        parent = tmp_path / "locked"
        parent.mkdir()
        (parent / "child.md").write_text("x")
        parent.chmod(0o600)  # readable, NOT searchable -> lstat(child) = EACCES
        try:
            assert api.classify_path(parent / "child.md") == api.KIND_INDETERMINATE
        finally:
            parent.chmod(0o755)

    def test_classify_is_exhaustive_over_a_real_directory(self, tmp_path: Path):
        """Totality, behaviourally: every child of a directory containing one of
        each shape classifies into `ALL_KINDS`, and between them they cover it."""
        (tmp_path / "plain.md").write_text("x")
        (tmp_path / "adir").mkdir()
        (tmp_path / "to_file").symlink_to(tmp_path / "plain.md")
        (tmp_path / "to_dir").symlink_to(tmp_path / "adir", target_is_directory=True)
        (tmp_path / "dangling").symlink_to(tmp_path / "nope")
        os.mkfifo(tmp_path / "afifo")
        (tmp_path / "to_fifo").symlink_to(tmp_path / "afifo")

        seen = {api.classify_path(p) for p in tmp_path.iterdir()}
        # ABSENT and INDETERMINATE cannot be produced by iterating a readable
        # directory — they are the two "the stat failed" answers — so they are
        # covered by their own tests above rather than here. Named, not omitted.
        reachable_by_iteration = api.ALL_KINDS - {api.KIND_ABSENT, api.KIND_INDETERMINATE}
        assert seen <= api.ALL_KINDS, f"produced an unknown kind: {seen - api.ALL_KINDS}"
        assert seen == reachable_by_iteration, (
            f"fixture misses kinds: {reachable_by_iteration - seen}"
        )


class TestSnapshotRoute:
    """Phase 2's cache-fill route: `GET /api/v1/snapshot` ships the entry files.

    🔴 The property under test is NOT "a tar came back". It is that a digest
    rendered from the EXTRACTED copy is byte-identical to one rendered from the
    source — because that is what makes a client's offline answer the same
    answer, and it is the thing a plausible tar gets silently wrong.
    """

    @staticmethod
    def _members(body: bytes) -> dict[str, tarfile.TarInfo]:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r") as tar:
            return {m.name: m for m in tar.getmembers()}

    @staticmethod
    def _extract(body: bytes, dest: Path) -> Path:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r") as tar:
            tar.extractall(dest)
        return dest

    def test_it_ships_every_entry_and_counts_them(self, store: Path):
        with running(store) as (base, _):
            code, headers, body = fetch(f"{base}/api/v1/snapshot", token=GOOD_TOKEN)
        assert code == 200
        assert headers["X-Store-Status"] == "snapshot"
        assert headers["Content-Type"] == "application/gzip"
        members = self._members(body)
        on_disk = {
            f"{p.parent.name}/{p.name}" for p in store.glob("*/*.md") if p.is_file()
        }
        assert on_disk, "fixture bug: the store has no entries to ship"
        assert on_disk <= set(members), f"missing: {on_disk - set(members)}"
        # The server's own count must agree with what it actually wrote.
        assert int(headers["X-Store-Entries"]) == len(on_disk)

    def test_the_archive_is_SMALLER_than_the_payload_it_carries(self, store: Path):
        """The gzip change was made on a measured claim — PAX spends ~2 KB of
        headers on a ~200-byte entry, so an uncompressed tar of 305 small
        entries measured **10.1x** the markdown it carried. Assert the property
        rather than trusting the commit message: with many small entries the
        archive must not exceed the raw bytes.
        """
        for i in range(40):
            (store / SCOPE / f"bulk-{i:03d}.md").write_text(
                _entry(f"bulk-{i:03d}", SCOPE, nuance=f"- 2026-03-01: item {i}.")
            )
        raw = sum(p.stat().st_size for p in store.glob("*/*.md"))
        with running(store) as (base, _):
            code, _h, body = fetch(f"{base}/api/v1/snapshot", token=GOOD_TOKEN)
        # 🔴 THE STATUS, FIRST. This used to be `_c, _h, body` — the code
        # discarded — so a 9-byte error body satisfied `len(body) < raw` and the
        # test passed having measured no archive at all.
        assert code == 200, (code, body)
        assert len(body) < raw, (
            f"archive {len(body)}B vs {raw}B of markdown — compression is off "
            f"or PAX overhead is dominating again"
        )

    def test_a_digest_from_the_EXTRACTED_copy_is_BYTE_IDENTICAL(
        self, store: Path, tmp_path: Path
    ):
        """🔴 The criterion this route exists to satisfy.

        Rendered from the source vs rendered from the extracted copy. The one
        line that legitimately differs is `store: <root>`, for exactly the
        reason `verify-byte-identity.sh` documents — so it is canonicalised on
        BOTH sides and nothing else is.
        """
        with running(store) as (base, _):
            _c, _h, body = fetch(f"{base}/api/v1/snapshot", token=GOOD_TOKEN)
        copy = self._extract(body, tmp_path / "cache")

        def digest(root: Path) -> str:
            report = api.rc.recall(str(root), SCOPE, mode=api.rc.DEFAULT_MODE)
            text = api.rc.render_text(report)
            return re.sub(r"^(\s*store:) .*$", r"\1 X", text, flags=re.M)

        assert digest(copy) == digest(store)

    def test_mtimes_are_PRESERVED_not_normalised(self, store: Path, tmp_path: Path):
        """🔴 Load-bearing, and the reason a "reproducible" tar is WRONG here.

        The reader orders the index newest-first by entry-file mtime. Normalising
        mtimes — the usual reproducibility move — reorders every digest rendered
        from the copy, with no error and nothing missing: it just reads as a
        stale cache.

        The fixture mtimes are pairwise distinct AND deliberately anti-aligned
        with alphabetical order, so a tar that dropped mtimes cannot accidentally
        reproduce the right ordering.
        """
        # This test owns its ordering requirement rather than depending on the
        # shared fixture's shape — which holds ONE entry in this scope, so the
        # ordering claim would have been vacuous against it.
        for name in ("thing-beta", "thing-gamma"):
            (store / SCOPE / f"{name}.md").write_text(
                _entry(name, SCOPE, nuance=f"- 2026-02-01: {name} distinct nuance.")
            )
        entries = sorted(store.glob(f"{SCOPE}/*.md"))
        assert len(entries) >= 3, "fixture bug: need several entries to order"
        # 🔴 FRACTIONAL, AND SHARING ONE WHOLE SECOND. An earlier version of this
        # test used whole-second mtimes a day apart and PASSED against a server
        # that truncated with `int()` — the truncation was invisible because the
        # fixture had no fraction to lose. Entries written in the same second is
        # the NORMAL case (a `/handoff` writes several at once), and there the
        # truncation makes every entry tie, so the reader's ref tie-break
        # silently reorders the index. Fixture values must be able to see the
        # mutation, or a green result is a claim about the fixture.
        base_t = 1_700_000_000
        for i, path in enumerate(reversed(entries)):
            stamp = base_t + 0.1 * (i + 1)  # same second, distinct fractions
            os.utime(path, (stamp, stamp))
        want = {f"{p.parent.name}/{p.name}": p.stat().st_mtime for p in entries}
        assert len({int(v) for v in want.values()}) == 1, (
            "fixture bug: the mtimes must share a whole second, or truncation "
            "is not exercised"
        )

        with running(store) as (base, _):
            _c, _h, body = fetch(f"{base}/api/v1/snapshot", token=GOOD_TOKEN)
        members = self._members(body)
        for name, mtime in want.items():
            assert members[name].mtime == pytest.approx(mtime, abs=1e-4), (
                f"{name}: snapshot lost sub-second mtime precision"
            )

        copy = self._extract(body, tmp_path / "cache")
        for name, mtime in want.items():
            assert (copy / name).stat().st_mtime == pytest.approx(mtime, abs=1e-4)

    def test_the_index_ORDER_survives_a_round_trip(self, store: Path, tmp_path: Path):
        """The behavioural consequence of the mtime test above.

        Preserving mtimes is a means; this is the end. Asserted separately
        because a structural mtime check passes for a tar whose ordering the
        reader still disagrees with — e.g. if the reader's tie-break changed.
        """
        for name in ("thing-beta", "thing-gamma"):
            (store / SCOPE / f"{name}.md").write_text(
                _entry(name, SCOPE, nuance=f"- 2026-02-01: {name} distinct nuance.")
            )
        base_t = 1_700_000_000
        for i, path in enumerate(sorted(store.glob(f"{SCOPE}/*.md"))):
            stamp = base_t + 0.1 * (i + 1)
            os.utime(path, (stamp, stamp))

        with running(store) as (base, _):
            _c, _h, body = fetch(f"{base}/api/v1/snapshot", token=GOOD_TOKEN)
        copy = self._extract(body, tmp_path / "cache")

        def index_order(root: Path) -> list[str]:
            report = api.rc.recall(str(root), SCOPE, mode=api.rc.DEFAULT_MODE)
            return [
                line.split()[0]
                for line in api.rc.render_text(report).splitlines()
                if line.startswith("  ") and line.strip() and "nuance" in line
            ]

        source_order = index_order(store)
        assert len(source_order) >= 3, f"fixture bug: got {source_order}"
        assert index_order(copy) == source_order

    def test_the_mtime_assertion_can_SEE_a_normalised_tar(self, store: Path):
        """Positive control for the test above: build the tar the WRONG way and
        watch the same comparison fail. Without this, a snapshot that happened to
        preserve mtimes and one whose mtimes were never checked look alike."""
        entries = sorted(store.glob(f"{SCOPE}/*.md"))
        os.utime(entries[0], (1_700_000_000, 1_700_000_000))
        want = int(entries[0].stat().st_mtime)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(f"{SCOPE}/{entries[0].name}")
            info.size = entries[0].stat().st_size
            info.mtime = 0  # the mutation: normalised, as a "reproducible" tar would
            with entries[0].open("rb") as fh:
                tar.addfile(info, fh)
        members = self._members(buf.getvalue())
        got = members[f"{SCOPE}/{entries[0].name}"].mtime
        assert got != want, "the control cannot distinguish a normalised tar"

    def test_scope_filter_ships_only_that_scope(self, store: Path):
        with running(store) as (base, _):
            _c, _h, body = fetch(
                f"{base}/api/v1/snapshot?scope={SCOPE}", token=GOOD_TOKEN
            )
        names = [n for n in self._members(body) if n.endswith(".md")]
        assert names, "filtered snapshot shipped nothing"
        assert all(n.startswith(f"{SCOPE}/") for n in names), names

    def test_an_invalid_scope_is_a_400_not_a_traversal(self, store: Path):
        with running(store) as (base, _):
            code, headers, _b = fetch(
                f"{base}/api/v1/snapshot?scope=../../etc", token=GOOD_TOKEN
            )
        assert code == 400
        assert headers["X-Store-Status"] == "bad-request"

    def test_it_requires_a_token(self, store: Path):
        with running(store) as (base, _):
            code, _h, _b = fetch(f"{base}/api/v1/snapshot")
        assert code == 401

    def test_it_ships_no_dot_dirs_and_no_non_markdown(self, store: Path):
        (store / SCOPE / "notes.txt").write_text("not an entry")
        (store / ".git").mkdir(exist_ok=True)
        (store / ".git" / "HEAD").write_text("ref: refs/heads/main")
        with running(store) as (base, _):
            _c, _h, body = fetch(f"{base}/api/v1/snapshot", token=GOOD_TOKEN)
        names = set(self._members(body))
        assert not any(n.startswith(".git") for n in names), names
        assert not any(n.endswith(".txt") for n in names), names

    def test_taking_a_snapshot_leaves_the_store_BYTE_IDENTICAL(self, store: Path):
        """🔴 THE SNAPSHOTS MUST HAVE HAPPENED. Both `fetch` results used to be
        discarded, so the only assertion was `tree_hash == before` — which a
        server that answered 401 to everything satisfies perfectly, having
        touched nothing because it did nothing. "Unchanged" is only a claim
        about the archiver if the archiver ran.
        """
        before = tree_hash(store)
        with running(store) as (base, _):
            whole, _h, whole_body = fetch(
                f"{base}/api/v1/snapshot", token=GOOD_TOKEN
            )
            scoped, _h2, scoped_body = fetch(
                f"{base}/api/v1/snapshot?scope={SCOPE}", token=GOOD_TOKEN
            )
        assert whole == 200, (whole, whole_body)
        assert scoped == 200, (scoped, scoped_body)
        assert tree_hash(store) == before


class TestSearchOverHTTP:
    def test_POSITIVE_a_query_that_must_hit_DOES(self, store: Path):
        with running(store) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/search/{SCOPE}?q=readiness+probe", token=GOOD_TOKEN
            )
        assert code == 200
        assert headers["X-Store-Status"] == "search-hit"
        assert b"readiness probe" in body

    def test_NEGATIVE_a_query_that_must_MISS_reports_no_match_not_a_hit(
        self, store: Path
    ):
        # Reported beside the hit above: a zero from a searcher never seen to
        # return non-zero is indistinguishable from a searcher wired to nothing.
        with running(store) as (base, _):
            code, headers, _b = fetch(
                f"{base}/api/v1/search/{SCOPE}?q=zzqqxx+nonesuch", token=GOOD_TOKEN
            )
        assert code == 200
        assert headers["X-Store-Status"] == "search-no-match"

    def test_a_missing_query_is_a_400_not_an_empty_search(self, store: Path):
        with running(store) as (base, _):
            code, headers, body = fetch(f"{base}/api/v1/search/{SCOPE}", token=GOOD_TOKEN)
        assert code == 400
        assert headers["X-Store-Status"] == "bad-request"
        assert b"q is required" in body


class TestQueryParamsNeverSilentlyDefault:
    """A `?limit=abc` that quietly became the default is a caller believing a
    setting took effect — the class `subsystem_recall.main` rejects flag
    combinations for.
    """

    @pytest.mark.parametrize(
        "query,needle",
        [
            ("limit=abc", b"limit must be an integer"),
            ("page=two", b"page must be an integer"),
            ("mode=sideways", b"mode must be one of"),
            ("limit=0", b"limit must be an int >= 1"),
            ("page=0", b"page must be an int >= 1"),
        ],
    )
    def test_a_bad_parameter_is_a_400_naming_the_parameter(
        self, store: Path, query: str, needle: bytes
    ):
        with running(store) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}?{query}", token=GOOD_TOKEN
            )
        assert code == 400
        assert headers["X-Store-Status"] == "bad-request"
        assert needle in body

    def test_a_GOOD_parameter_still_works(self, store: Path):
        # The controls above are only evidence if the same shape can succeed.
        with running(store) as (base, _):
            code, headers, _b = fetch(
                f"{base}/api/v1/recall/{SCOPE}?limit=3&mode=full", token=GOOD_TOKEN
            )
        assert code == 200
        assert headers["X-Store-Status"] == "recalled"


# =============================================================================
# 8. The revision header — "unknown" rather than a fabricated sha.
# =============================================================================


class TestScopeRevision:
    def _git(self, path: Path, *args: str):
        subprocess.run(
            ["git", "-C", str(path), *args],
            check=True,
            capture_output=True,
            env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "HOME": str(path),
                 **hermetic_git.MAINTENANCE_OFF},
        )

    def test_a_real_scope_repo_yields_its_HEAD_sha(self, store: Path):
        scope_dir = store / SCOPE
        self._git(scope_dir, "init", "-q", "-b", "main")
        self._git(scope_dir, "config", "user.email", "t@example.invalid")
        self._git(scope_dir, "config", "user.name", "T")
        self._git(scope_dir, "add", "thing-alpha.md")
        self._git(scope_dir, "commit", "-qm", "seed")
        head = subprocess.run(
            ["git", "-C", str(scope_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        assert api.scope_revision(store, SCOPE) == head
        with running(store) as (base, _):
            _c, headers, _b = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert headers["X-Store-Revision"] == head

    def test_a_scope_with_no_repo_reports_unknown_NOT_a_made_up_sha(self, store: Path):
        assert api.scope_revision(store, OTHER_SCOPE) == "unknown"

    def test_an_absent_scope_reports_unknown(self, store: Path):
        assert api.scope_revision(store, "never-heard-of-it") == "unknown"

    def test_a_dangling_ref_reports_unknown(self, store: Path):
        git = store / SCOPE / ".git"
        git.mkdir(parents=True)
        (git / "HEAD").write_text("ref: refs/heads/nowhere\n")
        assert api.scope_revision(store, SCOPE) == "unknown"

    def test_a_packed_ref_is_resolved(self, store: Path):
        git = store / SCOPE / ".git"
        git.mkdir(parents=True)
        (git / "HEAD").write_text("ref: refs/heads/main\n")
        (git / "packed-refs").write_text(
            "# pack-refs with: peeled fully-peeled sorted\n"
            "1111111111111111111111111111111111111111 refs/heads/main\n"
        )
        assert api.scope_revision(store, SCOPE) == "1" * 40


# =============================================================================
# 8b. The snapshot stamp — every report dates the COPY it is serving.
#
# 🔴 REGRESSION, NOT AN INVARIANT GUARD. Measured on the live public endpoint
# 2026-08-20, four days after cutover: an authed GET returned 200 with
# `ALL 5 entries in devrc/, none omitted` while the source held 9, and one
# served entry was a 40-day-old copy of a file edited that morning. Every
# existing check passed — reachability, auth, client-IP chain, firewall — because
# none of them compares the served bytes to the source. The defect was that a
# stale snapshot and the live source produce byte-identical-looking answers.
#
# 🔴 WHICH OF THESE ARE REGRESSION TESTS, HONESTLY — 3 of 11, not 11.
# Measured by running this class against `main` at `19756d5`: 11 failed, but
# only THREE failed on BEHAVIOUR. The other eight raise `AttributeError: module
# has no attribute 'snapshot_freshness' / 'SEED_STAMP_NAME'`, which is the
# symbol not existing, not the defect being caught — the same "red at base is a
# collection error and proves nothing" this file's own header records about
# `server.py`. Those eight are INVARIANT GUARDS on the four-state contract; they
# are worth having and they are not evidence.
#
# The three that genuinely bite, each with an assertion failure at base:
#   * test_a_report_that_does_not_date_itself_is_the_regression  (body undated)
#   * test_the_stamp_is_on_search_too_not_only_recall            (body undated)
#   * test_seed_sh_writes_a_stamp_and_puts_it_IN_THE_ARCHIVE     (no stamp)
# =============================================================================


class TestSnapshotStamp:
    def _fresh(self, store: Path):
        return api.snapshot_freshness(store)

    def test_a_report_that_does_not_date_itself_is_the_regression(self, store: Path):
        """The whole point: the body — not just a header — must say SNAPSHOT.

        Asserted on the BODY because the measured failure was an agent reading
        rendered text; a caller that pipes the body never sees a header.
        """
        with running(store) as (base, _):
            _c, _h, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        text = body.decode()
        assert "SNAPSHOT, NOT THE SOURCE" in text
        # It must come BEFORE the completeness claim it qualifies.
        assert text.index("SNAPSHOT, NOT THE SOURCE") < text.index("none omitted")

    def test_the_stamp_is_on_search_too_not_only_recall(self, store: Path):
        """Both routes go through `_serve_report`; pin that they both stamp.

        If a future route stamps only recall, the copy it serves is undated on
        exactly the surface a caller reaches for when an entry seems missing.
        """
        with running(store) as (base, _):
            _c, headers, body = fetch(
                f"{base}/api/v1/search/{SCOPE}?q=alpha", token=GOOD_TOKEN
            )
        assert "SNAPSHOT, NOT THE SOURCE" in body.decode()
        assert "X-Store-Snapshot" in headers

    def test_newest_entry_is_the_newest_mtime_and_MOVES_when_content_changes(
        self, store: Path
    ):
        """A value that cannot move is indistinguishable from a hardcoded string.

        Feeds a timestamp the fixture CANNOT already equal and watches the
        output follow it (RULES.md: the mechanical control for a constant).
        """
        header_before, _ = self._fresh(store)
        target = time.time() + 86_400  # a day ahead: no fixture file can hold it
        os.utime(store / SCOPE / "thing-alpha.md", (target, target))
        header_after, _ = self._fresh(store)

        assert header_before != header_after
        stamp = datetime.fromtimestamp(target, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        assert f"newest={stamp}" in header_after

    def test_entry_files_counts_entries_and_not_the_stamp_file(self, store: Path):
        """The count must be of *.md, or it drifts against seed.sh's own number.

        `.seed-stamp` is not an entry; counting it would make the API and the
        seeder disagree by exactly one and read as a lost file.
        """
        header_before, _ = self._fresh(store)
        assert "entry-files=3" in header_before
        (store / api.SEED_STAMP_NAME).write_text("2026-08-20T00:00:00Z\n")
        header_after, _ = self._fresh(store)
        assert "entry-files=3" in header_after

    def test_a_seeded_stamp_is_read_and_reported(self, store: Path):
        (store / api.SEED_STAMP_NAME).write_text(
            "2026-08-20T17:30:00Z staged_entries=71 host=box\n"
        )
        header, prose = self._fresh(store)
        assert "seeded=2026-08-20T17:30:00Z staged_entries=71 host=box" in header
        assert "UNSTAMPED" not in header

    def test_an_ABSENT_stamp_is_named_never_omitted(self, store: Path):
        """The failure this whole block exists to prevent is a SILENT absence."""
        header, prose = self._fresh(store)
        assert "seeded=UNSTAMPED" in header
        assert "SNAPSHOT, NOT THE SOURCE" in prose

    def test_an_EMPTY_stamp_is_UNREADABLE_and_distinct_from_absent(
        self, store: Path
    ):
        """Two mechanisms, two names — an empty file is not a missing one."""
        (store / api.SEED_STAMP_NAME).write_text("   \n")
        header, _ = self._fresh(store)
        assert "seeded=UNREADABLE" in header
        assert "UNSTAMPED" not in header

    def test_an_EMPTY_store_says_NONE_and_zero_not_a_fabricated_date(
        self, tmp_path: Path
    ):
        """`newest=NONE entry-files=0` must be distinguishable from UNREADABLE."""
        empty = tmp_path / "empty-store"
        empty.mkdir()
        header, _ = self._fresh(empty)
        assert "newest=NONE" in header
        assert "entry-files=0" in header
        assert "UNREADABLE" not in header

    def test_an_UNREADABLE_scope_is_UNREADABLE_not_an_empty_store(
        self, store: Path
    ):
        """🔴 The two zeros that must never be confused.

        A store that cannot be WALKED and a store that is genuinely EMPTY both
        yield "no entries". This file's own header calls that out; the stamp
        would be worthless if it collapsed them.
        """
        if os.geteuid() == 0:
            pytest.skip("root ignores directory permissions; the case is unreachable")
        locked = store / SCOPE
        mode = locked.stat().st_mode
        locked.chmod(0o000)
        try:
            header, _ = self._fresh(store)
        finally:
            locked.chmod(mode)
        assert "UNREADABLE" in header
        assert "newest=NONE" not in header

    def test_the_header_and_the_prose_carry_the_SAME_facts(self, store: Path):
        """One derivation, two renderings — they must not drift apart.

        A header saying `seeded=UNSTAMPED` beside prose implying a known date is
        the shape where a reader believes whichever they happened to read.
        """
        (store / api.SEED_STAMP_NAME).write_text("2026-08-20T17:30:00Z\n")
        header, prose = self._fresh(store)
        for field in ("2026-08-20T17:30:00Z", "entry-files=3"):
            assert field in header
            assert field.replace("entry-files=", "entry-files=") in prose

    def test_seed_sh_writes_a_stamp_and_puts_it_IN_THE_ARCHIVE(
        self, tmp_path: Path, store: Path
    ):
        """🔴 The member list globs `*/` — directories only.

        A top-level stamp file is silently dropped from the tar unless named,
        which would push undated content while reporting OK. Drives the real
        script's stage half, then asserts the stamp is both written and listed
        as a tar member by the script's own source.
        """
        stage = tmp_path / "stage"
        seed = API_DIR / "seed.sh"
        proc = subprocess.run(
            [str(seed), "--store", str(store), "--stage", str(stage)],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        stamp = stage / ".seed-stamp"
        assert stamp.exists(), "seed.sh staged no .seed-stamp"
        assert re.match(
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z staged_entries=\d+ host=",
            stamp.read_text().strip(),
        ), stamp.read_text()
        # The stage half never tars; pin the member list from the source, since
        # this is exactly the line whose omission is silent.
        assert 'members+=(".seed-stamp")' in seed.read_text()


# =============================================================================
# 9. The audit log — §2b: "timestamp, path, token id (not the token), result".
# =============================================================================


class TestAuditLog:
    def test_every_api_request_writes_exactly_one_line(self, store: Path):
        with running(store) as (base, audit):
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
            fetch(f"{base}/api/v1/recall/{SCOPE}")  # rejected
            await_audit(audit, 2)
        # 🔴 `settle`, NOT `assert len(lines) == 2` ON THE SNAPSHOT. The snapshot
        # is taken microseconds after the response, so a THIRD line emitted from
        # a later callback or another thread is invisible to it. See `settle`.
        settle(audit, 2)

    def test_health_is_NOT_audited(self, store: Path):
        # It is unauthenticated and says nothing; logging it would bury the
        # /api/* lines the log exists for under kubelet probe traffic.
        with running(store) as (base, audit):
            fetch(f"{base}/healthz")
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
            await_audit(audit, 1)
        # 🔴 A REASSURING ZERO NEEDS A POSITIVE CONTROL. `assert audit == []`
        # read the live list with nothing to wait for, so it was equally happy
        # with "the probe is not audited" and with "the sink had not appended
        # yet" — and it would have stayed green with `_audit` wired to nothing
        # at all. An audited request is issued after the probe; waiting for ITS
        # line proves the sink works, and the count then says the probe added
        # none. 🔴 THE COUNT IS `settle`'s, NOT THE SNAPSHOT'S — this comment
        # used to close with "(Residual: a probe line arriving after this
        # snapshot is still unobserved)", and that residual is now closed for
        # anything landing inside `SETTLE_GRACE_S` of teardown. Still bounded by
        # that window: a sink with no EOF admits no stronger claim.
        lines = settle(audit, 1)
        assert "/healthz" not in lines[0], lines[0]

    def test_the_line_carries_timestamp_path_result_and_a_token_ID(self, store: Path):
        with running(store) as (base, audit):
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
            line = await_audit(audit, 1)[0]
        assert "ts=2" in line
        assert f"path=/api/v1/recall/{SCOPE}" in line
        assert "result=200" in line
        assert "auth=ok" in line
        assert f"token={api.token_id(GOOD_TOKEN)}" in line

    def test_the_log_NEVER_contains_the_token_itself(self, store: Path):
        with running(store) as (base, audit):
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
            fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
            joined = "\n".join(await_audit(audit, 2))
        assert GOOD_TOKEN not in joined
        assert "w" * 48 not in joined, "a rejected token was echoed into the log"

    def test_a_rejected_request_is_logged_as_a_FAILURE_with_no_token_id(
        self, store: Path
    ):
        with running(store) as (base, audit):
            fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
            line = await_audit(audit, 1)[0]
        assert "auth=fail" in line
        assert "token=-" in line
        assert "result=401" in line

    def test_the_token_id_is_a_DIGEST_not_a_prefix_of_the_token(self):
        tid = api.token_id(GOOD_TOKEN)
        assert len(tid) == 12
        assert tid not in GOOD_TOKEN
        assert api.token_id(GOOD_TOKEN) == api.token_id(GOOD_TOKEN)
        assert api.token_id(GOOD_TOKEN) != api.token_id(GOOD_TOKEN + "x")


# =============================================================================
# 10. The seed — 🔴 the local store is AUTHORITATIVE; every other copy lags it.
# =============================================================================


def run_seed(*args: str, env: "dict[str, str] | None" = None) -> subprocess.CompletedProcess:
    # `bash <script>` rather than the shebang: `/usr/bin/env` does not exist in
    # the nix sandbox that gates merges (see test_runtime_shebangs.py).
    #
    # `env` defaults to None so every existing caller inherits the ambient
    # environment exactly as before; the push tests pass one to put a fake
    # `kubectl` on PATH.
    return subprocess.run(
        ["bash", str(SEED_PATH), *args],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


# 🔴 THE PUSH HALF WAS UNTESTED, AND THAT IS HOW THE COUNT GUARD SHIPPED WRONG.
# seed.sh's own header says "staging is hermetic and testable, pushing needs a
# cluster", so every seed test above stops at `--stage`. The verdict that
# decides whether a push SUCCEEDED therefore lived in the only half nothing
# exercised, and it was wrong for the multi-host case the store has always had.
#
# This fake answers `get pod`, runs the real `tar` extract into a real
# directory, and runs the remote `find` there too — so the guard executes
# against a filesystem the test controls rather than against a mock of itself.
# `$FAKE_DROP` deletes one member AFTER the extract, which is how a staged entry
# that does not land is simulated without pretending the guard ran.
#
# 🔴 POSIX sh, AND `mockbin.write_exec` OWNS THE SHEBANG. The first version of
# this stub wrote `#!/usr/bin/env bash` itself. `/usr/bin/env` does not exist in
# the nix sandbox that gates merges, and the two tiers reported that completely
# differently: the dev host showed ONE tidy failure in `test_runtime_shebangs`,
# while the gating tier showed `5 failed` — the guard PLUS all four tests here,
# which never ran at all (`bad interpreter`, rc 126). The class had zero
# coverage on the only tier that matters, and the dev-host run could not say so.
#
# Resolving the path with `shutil.which` is not enough either: that scanner
# flags a test writing ANY shebang, and its allowlist is explicitly not the way
# to green a new site. So the body is POSIX sh — no arrays, no `[[ ]]`, no
# `${a//…}` — and `write_exec` supplies `#!<sh>`. The `set --` rotate below is
# the POSIX way to rebuild the argument list without an array, and it preserves
# arguments containing spaces, which a string accumulator would not.
FAKE_KUBECTL_BODY = r"""
# 🔴 AN EXPLICIT TOP-LEVEL `:?` GUARD, NOT `set -u`, AND THAT IS A MEASUREMENT.
# The bash original had `set -uo pipefail`; the POSIX rewrite dropped it; round 2
# restored `set -u`. Round 3's sweep showed that restoration SURVIVED its own
# mutant — the only reference to $FAKE_DEST sits inside a `$( … )`, so an unbound
# variable there kills the SUBSHELL while the stub carries on with an empty
# substitution (`s|/data||g`, silently rewriting absolute paths to relative).
# `:?` at top level exits the stub itself, which is what makes the failure
# observable — and therefore testable — by a caller.
: "${FAKE_DEST:?FAKE_DEST must be set - the stub would otherwise rewrite /data to the empty string}"
# Stands in for kubectl in seed.sh's push half. $FAKE_DEST is the pretend /data;
# $FAKE_DROP, if set, removes one member AFTER the extract, which is how "a
# staged entry that did not land" is simulated without faking the guard itself.
if [ "${1:-}" = "-n" ]; then shift 2; fi
sub="${1:-}"
if [ $# -gt 0 ]; then shift; fi
case "$sub" in
  get)
    echo "fake-pod-0"
    ;;
  exec)
    if [ "${1:-}" = "-i" ]; then shift; fi
    if [ $# -gt 0 ]; then shift; fi
    if [ "${1:-}" = "--" ]; then shift; fi
    n=$#
    i=0
    while [ "$i" -lt "$n" ]; do
      a="$1"
      shift
      set -- "$@" "$(printf '%s' "$a" | sed "s|/data|$FAKE_DEST|g")"
      i=$((i + 1))
    done
    "$@"
    rc=$?
    if [ -n "${FAKE_DROP:-}" ] && [ "${1:-}" = "tar" ]; then
      rm -f "$FAKE_DEST/$FAKE_DROP"
    fi
    exit "$rc"
    ;;
  *)
    echo "fake kubectl: unexpected subcommand: $sub" >&2
    exit 64
    ;;
esac
"""


@pytest.fixture
def fake_cluster(tmp_path: Path):
    """`(env, dest)` — an environment whose `kubectl` is the fake above, and the
    directory standing in for the pod's `/data`."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    mockbin.write_exec(bindir / "kubectl", FAKE_KUBECTL_BODY)
    dest = tmp_path / "pod-data"
    dest.mkdir()
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_DEST": str(dest),
    }
    return env, dest


class TestSeedPushVerdict:
    """🔴 The push verdict must answer "did everything I staged land?" — a
    SUBSET check on names — and NOT "does the remote hold exactly as many files
    as the stage", which is only true while one host ever seeds."""

    def _push(self, store: Path, tmp_path: Path, env):
        return run_seed(
            "--store", str(store),
            "--stage", str(tmp_path / "stage"),
            "--push", "ns/app",
            env=env,
        )

    # 🔴 ONE PIN, ONE PLACE, PARAMETERISED BY COUNT. This sentence has been
    # FALSE three times — "hold .md files" asserted contents an unreadable probe
    # could not read; "will NOT ship" was wrong because a symlinked scope DOES
    # ship, as a symlink; and "excluded from the count" is loose because
    # `staged_scopes` counts a symlinked scope and not a dot one. Keyword guards
    # caught none of them: any rewording satisfies `"X" not in stdout`, and two
    # such guards had become unfalsifiable — no code path could emit the string
    # they forbade. When the artifact under test is prose, the normalised WHOLE
    # is the only machine-readable claim, and a deliberate reword must fail here
    # and be re-verified. The count is a parameter so a fixture that gains an
    # excluded scope reports THAT, instead of misdirecting at the wording.
    @staticmethod
    def _assert_note_header(stdout: str, n: int) -> None:
        headers = [l for l in stdout.splitlines() if l.startswith("seed: NOTE")]
        assert len(headers) == 1, f"expected exactly one NOTE header: {headers}"
        assert headers[0] == (
            f"seed: NOTE {n} scope director(ies) contribute NO entries, and are "
            "excluded from the entry count and the verdict:"
        ), (
            "the NOTE header changed. If the COUNT moved, fix the caller; if the "
            "WORDING moved, re-verify it is true of EVERY state that reaches it "
            f"— three earlier wordings were not. Got: {headers[0]!r}"
        )

    @staticmethod
    def _foreign(dest: Path, name: str = "from-the-other-host.md") -> Path:
        """An entry the OTHER host seeded. Its presence is what arms GNU comm's
        order check — without an unpairable line the check never runs, which is
        why an exactly-matching push cannot see a collation bug."""
        d = dest / "a-scope-only-the-laptop-has"
        d.mkdir(exist_ok=True)
        p = d / name
        p.write_text("---\nservice: x\n---\n")
        return p

    def test_POSITIVE_CONTROL_the_fake_cluster_really_receives_the_push(
        self, store: Path, tmp_path: Path, fake_cluster
    ):
        """Reported BESIDE the verdicts below. A guard asserted against a
        cluster that received NOTHING is green for the wrong reason — every
        assertion in this class would hold over an empty directory."""
        env, dest = fake_cluster
        r = self._push(store, tmp_path, env)
        assert r.returncode == 0, r.stderr
        landed = sorted(p.name for p in (dest / SCOPE).glob("*.md"))
        assert landed == ["thing-alpha.md"], (
            f"the fake cluster received {landed!r} — the push did not happen, "
            "so nothing below is evidence about the guard"
        )
        assert "seed: OK" in r.stdout

    def test_entries_from_ANOTHER_HOST_do_not_fail_a_correct_push(
        self, store: Path, tmp_path: Path, fake_cluster
    ):
        """🔴 THE REGRESSION. Red before this change: the old guard compared
        COUNTS, so a pod already holding a second host's entries made a correct
        push exit 7 — after the content had landed. The store is per-host and
        the extract never deletes, so this is the NORMAL state, not an error."""
        env, dest = fake_cluster
        foreign = dest / "a-scope-only-the-laptop-has"
        foreign.mkdir()
        (foreign / "from-the-other-host.md").write_text("---\nservice: x\n---\n")

        r = self._push(store, tmp_path, env)

        assert r.returncode == 0, (
            f"a correct multi-host push must not fail. stderr={r.stderr}"
        )
        assert "NOTE 1 entry file(s)" in r.stdout, r.stdout
        assert (foreign / "from-the-other-host.md").exists(), (
            "the other host's entry was DELETED — the push must add and "
            "overwrite, never remove"
        )

    def test_a_staged_entry_that_does_NOT_land_exits_7_and_NAMES_it(
        self, store: Path, tmp_path: Path, fake_cluster
    ):
        """The guard must still bite, and say WHICH file — a bare count told an
        operator a number and left them to find the gap by hand."""
        env, dest = fake_cluster
        env = {**env, "FAKE_DROP": f"{SCOPE}/thing-alpha.md"}

        r = self._push(store, tmp_path, env)

        assert r.returncode == 7, f"rc={r.returncode} stdout={r.stdout}"
        assert "MISMATCH" in r.stderr
        assert f"{SCOPE}/thing-alpha.md" in r.stderr, (
            "the failure must name the entry that did not land"
        )
        assert not (dest / SCOPE / "thing-alpha.md").exists()

    def test_a_missing_entry_is_caught_even_when_the_COUNTS_MATCH(
        self, store: Path, tmp_path: Path, fake_cluster
    ):
        """🔴 The case the old count guard could not see, and the reason this is
        a STRENGTHENING rather than a relaxation: one staged file absent while a
        foreign file makes the totals agree. Count-equality passes; containment
        does not."""
        env, dest = fake_cluster
        foreign = dest / "a-scope-only-the-laptop-has"
        foreign.mkdir()
        (foreign / "makes-up-the-number.md").write_text("---\nservice: x\n---\n")
        env = {**env, "FAKE_DROP": f"{SCOPE}/thing-alpha.md"}

        r = self._push(store, tmp_path, env)

        m = re.search(r"STAGED scopes=\d+ entries=(\d+)", r.stdout)
        assert m is not None, f"seed.sh printed no STAGED line: {r.stdout}"
        staged = int(m.group(1))
        remote = len(list(dest.glob("*/*.md")))
        assert remote == staged, (
            f"fixture no longer exercises the blind spot: remote={remote} "
            f"staged={staged} — they must be EQUAL for this test to mean anything"
        )
        assert r.returncode == 7, (
            "counts matched, so the old guard would have passed this broken push"
        )
        assert f"{SCOPE}/thing-alpha.md" in r.stderr

    # --- the three dimensions the first fixture was structurally blind to -----
    #
    # 🔴 THE ORIGINAL FIXTURE COULD NOT SEE ANY OF THESE. It was all-lowercase,
    # all hyphen-slug, no top-level `.md`, no dot-directory — so it pinned none
    # of the ways the REAL store differs, and a mutation sweep over it left five
    # survivors. An audit found two live defects in that blind spot. Each test
    # below is named for the dimension, not the symptom.

    def test_a_README_beside_a_lowercase_sibling_survives_a_UTF8_LOCALE(
        self, store: Path, tmp_path: Path, fake_cluster
    ):
        """🔴 REGRESSION. Both lists are sorted `LC_ALL=C`, but GNU `comm`
        compares AND order-checks in the AMBIENT locale — so under en_US.UTF-8 a
        C-sorted list is "not in sorted order", `comm` exits 1, and `set -e`
        kills the script with NO verdict printed at all.

        Needs BOTH: an unpairable line (comm arms the order check only after
        one) and an adjacency where C and en_US disagree. `README.md` beside
        `backblaze.md`/`thing-alpha.md` is that adjacency, and the real store is
        full of it — so the FIRST push after another host seeds would abort."""
        # 🔴 THIS TEST NEITHER SKIPS NOR DEGRADES — IT FAILS IF IT CANNOT RUN,
        # AND THAT TOOK THREE TRIES TO GET RIGHT.
        #
        #   1. It CRASHED the gating tier: no `locale` binary in the nix sandbox,
        #      so `subprocess.run(["locale", …])` raised FileNotFoundError.
        #   2. Made to skip, it went red differently: `run-tests.sh` refuses an
        #      UNPINNED skip, and EXPECTED_SKIPS conditions key on env vars
        #      (`unset:VAR`) while this predicate is locale AVAILABILITY — an
        #      unconditional pin reds the dev host, where the test does run.
        #   3. Made to fall back to C, it passed on BOTH tiers while being
        #      VACUOUS on the gating one: measured with both `comm` calls
        #      unpinned, the sandbox reported ONE failure where the dev host
        #      reported two. The defect was invisible exactly where it counts.
        #
        # So the locale is now supplied to both tiers by `LOCALE_ARCHIVE`,
        # exported in the devShell AND in checks.pytests, and its absence is a
        # HARD FAILURE naming the cause. A degradation that silently weakens a
        # test is worse than a red one that says why.
        # (This comment used to add "`glibc.bin` in `gateTools`" — a requirement
        # the block below deliberately removed. It was left standing four lines
        # above its own correction; see flake.nix for both retractions.)
        # 🔴 AVAILABILITY IS DETECTED BY EXERCISING THE CAPABILITY, NOT BY
        # PROBING FOR A BINARY. An earlier version ran `locale -a` and asserted
        # `en_US.utf8` appeared. That was the wrong question: it needed
        # `pkgs.glibc.bin` added to gateTools purely to answer it, and MEASURED
        # — with `LOCALE_ARCHIVE` set
        # and NO `locale` binary at all, `LC_ALL=en_US.UTF-8 sort` collates
        # correctly. The binary was never load-bearing; `LOCALE_ARCHIVE` is.
        #
        # So the check IS the control: sort one inverting pair under C and under
        # en_US and require the orders to DIFFER. That single assertion answers
        # "is a non-C collation available" and "does this fixture still invert"
        # at once — and it cannot pass while the collation is unavailable, which
        # is exactly the vacuity this test kept falling into.
        forced = "en_US.UTF-8"
        pair = f"{SCOPE}/README.md\n{SCOPE}/backblaze.md\n"

        def _sorted_under(lc: str) -> str:
            return subprocess.run(
                ["sort"], input=pair, capture_output=True, text=True,
                env={**os.environ, "LC_ALL": lc},
            ).stdout

        c_order, loc_order = _sorted_under("C"), _sorted_under(forced)
        assert c_order != loc_order, (
            f"`sort` orders {SCOPE}/README.md and {SCOPE}/backblaze.md the same "
            f"under C and under {forced}, so comm's order check cannot arm and "
            "this test would pass whether or not the collation is pinned.\n"
            "Either the fixture stopped inverting, or — far more likely — this "
            "is a GATE ENVIRONMENT regression rather than a defect in seed.sh: "
            "flake.nix must export LOCALE_ARCHIVE in BOTH the devShell and "
            "checks.pytests, or the tier has only the C locale."
        )

        env, dest = fake_cluster
        # 🔴 THE PAIR MUST ACTUALLY INVERT, AND THE FIRST VERSION OF THIS TEST
        # DID NOT. `README.md` beside `thing-alpha.md` orders the SAME under
        # both collations (C: 'R'<'t'; en_US: 'r'<'t'), so nothing was out of
        # order, `comm` never complained, and the mutant that strips `LC_ALL=C`
        # from the comm calls SURVIVED a fully green run. The inversion needs a
        # lowercase sibling sorting BEFORE `README` in en_US and AFTER it in C —
        # 'b' is 0x62, above 'R' at 0x52, but below 'r' when case is folded.
        (store / SCOPE / "README.md").write_text(_entry("README", SCOPE))
        (store / SCOPE / "backblaze.md").write_text(_entry("backblaze", SCOPE))
        self._foreign(dest)
        env = {**env, "LC_ALL": forced, "LANG": forced}

        r = self._push(store, tmp_path, env)

        assert "not in sorted order" not in r.stderr, (
            f"comm order-checked in the ambient locale (LC_ALL={forced}) over "
            f"C-sorted input: stderr={r.stderr}"
        )
        assert r.returncode == 0, (
            f"rc={r.returncode} under LC_ALL={forced} "
            f"stdout={r.stdout} stderr={r.stderr}"
        )
        assert "seed: OK" in r.stdout
        assert "NOTE 1 entry file(s)" in r.stdout

    # 🔴 A STRUCTURAL `comm`-SPELLING GUARD WAS TRIED HERE AND DELETED, ON
    # EVIDENCE. It regex'd seed.sh for a bare `comm` not prefixed by `LC_ALL=C`.
    # An audit walked it three ways — `/usr/bin/comm -23 …`, `_cmp=comm` then
    # `$_cmp -23 …`, and a second `comm` later on an already-prefixed line — and
    # it ALSO rejected three safe spellings (`env LC_ALL=C comm`, a line
    # continuation, a global `export LC_ALL=C`). It asserted a WORD, and the
    # hazard has other shapes. Supplying the locale to both tiers (flake.nix)
    # lets the behavioural test above assert the STATE instead, everywhere.

    def test_the_two_find_expressions_are_IDENTICAL(self):
        """Local and remote listings must ask ONE question.

        Every clause carries a case: `-mindepth 2` excludes the store's own
        top-level `README.md`; `-maxdepth 2` stops a nested directory becoming a
        phantom entry; `! -path './.*'` matches the tar member list, which is
        `"$STAGE"/*/` without `dotglob`; `-type f` refuses a DIRECTORY named
        `*.md` (the server 503'd on exactly that).

        A mutation sweep found the clauses were pinned on the STAGED side only —
        dropping `! -path './.*'`, widening `-maxdepth`, or dropping `-type f`
        on the REMOTE side all SURVIVED. Comparing the two expressions to each
        other catches an asymmetry in either direction, which per-side
        assertions did not."""
        # 🔴 CAPTURE TO THE END OF THE EXPRESSION, NOT TO `-type f`. The first
        # version stopped at the first `-type f` (non-greedy), so everything
        # AFTER it was invisible: appending `-o -type d` to the remote find
        # SURVIVED a fully green 34-test run, which would have put depth-2
        # DIRECTORIES into `remote_list` and reported them as foreign entries on
        # every push. A guard whose stated purpose is "an asymmetry changes what
        # the comparison MEANS" must see the whole expression.
        #
        # The `cd` target is captured too: `( cd "$1/sub" && find . … )` leaves
        # both expressions textually identical while the sides walk different
        # trees.
        pat = re.compile(
            r"""cd\s+['"]?(?P<root>[^'"\s&]+)['"]?\s*&&\s*find\s+\.\s+"""
            r"""(?P<expr>.+?)\s*(?:\)|")"""
        )
        found = []
        for line in SEED_PATH.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue  # comments quote these expressions when explaining them
            m = pat.search(stripped)
            if m:
                found.append((m.group("root"), " ".join(m.group("expr").split())))
        assert len(found) == 2, (
            f"expected exactly 2 `cd … && find .` entry listings in seed.sh, "
            f"found {len(found)}: {found}"
        )
        (staged_root, staged), (remote_root, remote) = found
        assert staged == remote, (
            "the staged and remote listings no longer ask the same question — "
            "an asymmetry silently changes what the comparison MEANS:\n"
            f"  staged: {staged}\n  remote: {remote}"
        )
        assert staged_root != remote_root, (
            "both listings walk the same root, so one of them is not reading "
            f"what it is supposed to: {staged_root!r} / {remote_root!r}"
        )
        for clause in ("-mindepth 2", "-maxdepth 2", "! -path './.*'", "-type f"):
            assert clause in staged, f"{clause!r} is no longer pinned: {staged}"

    def test_a_dot_scope_does_not_ship_EVEN_WITH_dotglob_SET(
        self, store: Path, tmp_path: Path, fake_cluster
    ):
        """🔴 THE SUITE WAS BLIND TO THIS DIMENSION, WHICH IS HOW THE DEFECT GOT
        IN. Every other test here runs with bash's default options, so a change
        that made the tar member list depend on the AMBIENT shell passed
        everything.

        The member list is `"$STAGE"/*/`, correct only while `dotglob` is off. A
        version of this script restored the caller's `dotglob` before building
        it; under `BASHOPTS=dotglob` a dot-scope then LANDED on the pod while the
        remote listing's `! -path './.*'` still excluded it — shipped, never
        verified, and the NOTE saying "not in the tar member list" was false.
        Asking which dimension the config fixes is what surfaces this class."""
        env, dest = fake_cluster
        hidden = store / ".hidden"
        hidden.mkdir()
        (hidden / "h.md").write_text(_entry("h", ".hidden"))
        env = {**env, "BASHOPTS": "dotglob"}

        r = self._push(store, tmp_path, env)

        assert r.returncode == 0, f"stdout={r.stdout} stderr={r.stderr}"
        # 🔴 POSITIVE CONTROL, AND IT IS FREE. Everything below holds just as
        # well if BASHOPTS were ignored entirely — by a future bash, by
        # `set -o posix`, by a typo in the option name — and this test is the
        # SOLE killer of the caller-restoring shopt regression, so a silent
        # vacuity here re-opens that class with a green suite. The per-scope
        # loop DOES honour the ambient option, so a `.hidden` line appears only
        # when dotglob really armed.
        assert "staged scope .hidden" in r.stdout, (
            "BASHOPTS=dotglob did not arm — this test is not exercising the "
            f"dimension it names, so its pass means nothing. {r.stdout}"
        )
        assert not (dest / ".hidden").exists(), (
            "with dotglob set in the environment the dot-scope SHIPPED — the "
            "member list must not depend on the ambient shell. pod holds: "
            f"{sorted(p.name for p in dest.iterdir())}"
        )

    def test_a_DEPTH_2_DIRECTORY_on_the_pod_is_not_an_entry(
        self, store: Path, tmp_path: Path, fake_cluster
    ):
        """🔴 THE BEHAVIOURAL HALF OF THE TWO-LISTINGS INVARIANT, and the durable
        one. The textual identity guard has now lost this race twice: first by
        capturing only to `-type f`, then — after being 'fixed' — to an escaped
        `\\( … \\)` group, which recreated the same hole and stayed green across
        the whole class. A guard over a shell expression's SPELLING keeps
        finding new shapes to miss; this asserts the STATE instead.

        A depth-2 directory on the pod must not enter `remote_list`. If it does
        (an `-o -type d` on the remote side, a widened `-maxdepth`), it is
        reported as another host's entry on every single push."""
        env, dest = fake_cluster
        # 🔴 THE NAME MATTERS. A directory called `a-subdirectory` is excluded
        # by `-name '*.md'`, NOT by `-type f` — so the plainest mutant (dropping
        # `-type f` from the remote find) stayed invisible, caught only by the
        # textual guard this test was written to replace. Named `*.md`, the only
        # clause that can exclude it is `-type f`. It is also the exact shape the
        # server 503'd on: a DIRECTORY named `*.md`.
        (dest / SCOPE).mkdir(parents=True, exist_ok=True)
        (dest / SCOPE / "a-subdirectory.md").mkdir()

        r = self._push(store, tmp_path, env)

        assert r.returncode == 0, f"stdout={r.stdout} stderr={r.stderr}"
        assert "were not staged by this host" not in r.stdout, (
            "a DIRECTORY on the pod was counted as an entry and reported as "
            f"foreign — the two listings disagree: {r.stdout}"
        )

    def test_a_scope_name_containing_a_BACKSLASH_ESCAPE_counts_correctly(
        self, store: Path, tmp_path: Path, fake_cluster
    ):
        r"""`awk -v p=…` INTERPRETS escape sequences, so a scope literally named
        `a\tb` was searched for as `a<TAB>b` and reported entries=0 beside a
        non-zero total. Passing it through ENVIRON fixes that — and a straight
        revert to `-v` was green across the whole class until this existed."""
        # STAGE-ONLY, deliberately: the count line this pins is printed before
        # the push, and a backslash in a scope name independently breaks the tar
        # member list (`tar: a\tb: Cannot stat`) — a pre-existing defect this
        # round does not touch. Requiring rc 0 would make the test about that
        # instead, and it would never pass.
        weird = store / "a\\tb"
        weird.mkdir()
        (weird / "n.md").write_text(_entry("n", "a-tb"))

        r = run_seed("--store", str(store), "--stage", str(tmp_path / "stage"))

        assert r.returncode == 0, f"stdout={r.stdout} stderr={r.stderr}"
        counts = dict(re.findall(r"staged scope (\S+)\s+entries=(\d+)", r.stdout))
        assert counts.get("a\\tb") == "1", (
            "a scope whose name contains a backslash escape mis-counted: "
            f"{counts} — awk -v would read it as a literal tab. {r.stdout}"
        )

    def test_a_dot_scope_that_is_ALSO_a_symlink_is_announced_ONCE(
        self, store: Path, tmp_path: Path, fake_cluster
    ):
        """The two NOTE arms are `if`/`elif` for a reason: turning the `elif`
        into a second `if` was green across the class while printing one
        directory twice under a header that counts them."""
        env, dest = fake_cluster
        real = tmp_path / "dotsym-target"
        real.mkdir()
        (real / "e.md").write_text(_entry("e", "dotsym"))
        (store / ".dotsym").symlink_to(real)

        r = self._push(store, tmp_path, env)

        assert r.returncode == 0, f"stdout={r.stdout} stderr={r.stderr}"
        named = [l for l in r.stdout.splitlines() if ".dotsym" in l and l.startswith("seed:   ")]
        assert len(named) == 1, f"announced {len(named)} times, want 1: {named}"
        header = [l for l in r.stdout.splitlines() if l.startswith("seed: NOTE")]
        assert "1 scope" in header[0], f"the count must match the list: {header}"

    def test_a_scope_whose_md_is_TOO_DEEP_is_not_announced(
        self, store: Path, tmp_path: Path, fake_cluster
    ):
        """`_md_state` uses `-maxdepth 1` because only depth-1 `*.md` inside a
        scope is shippable at all. Widening it survived the class and would
        announce a scope over files no scope could ever ship."""
        env, dest = fake_cluster
        deep = store / ".deep" / "sub"
        deep.mkdir(parents=True)
        (deep / "x.md").write_text(_entry("x", "deep"))

        r = self._push(store, tmp_path, env)

        assert r.returncode == 0, f"stdout={r.stdout} stderr={r.stderr}"
        assert ".deep" not in "".join(
            l for l in r.stdout.splitlines() if l.startswith("seed:   ")
        ), f"announced a scope whose only .md is too deep to ship: {r.stdout}"

    def test_a_SYMLINKED_scope_is_excluded_and_SAID_so(
        self, store: Path, tmp_path: Path, fake_cluster
    ):
        """🔴 REGRESSION. `staged_entries` walked `"$STAGE"/*/` with a trailing
        slash, which RESOLVES a symlinked scope, while the comparison's `find .`
        does not descend one and `tar` ships the link rather than its target.
        Measured before the fix: `remote_entries=1 staged_entries=2` followed by
        `seed: OK all 2 staged entries are present`, rc 0 — a completeness claim
        over an entry that never landed, and one the OLD count-equality check
        would have caught. Both sides now come from `_shippable_entries`."""
        env, dest = fake_cluster
        real = tmp_path / "outside"
        real.mkdir()
        (real / "e.md").write_text(_entry("e", "symscope"))
        (store / "symscope").symlink_to(real)

        r = self._push(store, tmp_path, env)

        assert r.returncode == 0, f"stdout={r.stdout} stderr={r.stderr}"
        m = re.search(r"remote_entries=(\d+) staged_entries=(\d+)", r.stdout)
        assert m and m.group(1) == m.group(2), (
            "the two counts disagree and the run still succeeded: " + r.stdout
        )
        assert "symscope (symlink" in r.stdout, (
            "a scope that ships NOTHING must be named, not silently dropped: "
            + r.stdout
        )

    def test_the_per_scope_count_is_a_PREFIX_match_not_a_substring(
        self, store: Path, tmp_path: Path, fake_cluster
    ):
        """`entries=N` beside each scope had no coverage in either direction —
        a mutation sweep survived both `index($0,p) > 0` (substring) and the
        whole count hardcoded. With scopes `foo` and `xfoo`, a substring test
        counts `xfoo/n.md` against `foo`, so `foo` reports 2 while holding 1."""
        # 🔴 THE FIXTURE MUST DEFEAT THREE MUTANTS, AND THE FIRST VERSION BEAT
        # ONLY ONE. It used `foo`/`xfoo` with one entry each, so every true
        # count was 1 and the assertions were `== "1"` — hardcoding `n=1`
        # SURVIVED (only `n=0` and `n=2` died), the constant-equals-fixture trap.
        # And `xfoo` extends `foo` on the LEFT, so dropping the `/` separator
        # (`P="$name"`) survived too. Hence: one scope with TWO entries, and a
        # `foo`/`foobar` pair where the separator is what distinguishes them.
        env, dest = fake_cluster
        for name, n in (("foo", 2), ("foobar", 1), ("xfoo", 1)):
            (store / name).mkdir()
            for i in range(n):
                (store / name / f"n{i}.md").write_text(_entry(f"n{i}", name))

        r = self._push(store, tmp_path, env)

        assert r.returncode == 0, f"stdout={r.stdout} stderr={r.stderr}"
        counts = dict(re.findall(r"staged scope (\S+)\s+entries=(\d+)", r.stdout))
        assert counts.get("foo") == "2", (
            f"`foo` holds 2 entries, got {counts.get('foo')!r} — a substring "
            f"match would add xfoo's, a dropped separator would add foobar's. "
            f"{r.stdout}"
        )
        assert counts.get("foobar") == "1", counts
        assert counts.get("xfoo") == "1", counts

    def test_a_symlinked_scope_whose_target_is_UNREADABLE_is_still_announced(
        self, store: Path, tmp_path: Path, fake_cluster
    ):
        """🔴 AN ERROR IS NOT "NO".

        ⚠ Read this with `_md_state` open. The defect below was never the
        `2>/dev/null` — it was discarding `find`'s EXIT STATUS, which is the
        whole discriminator. An earlier version of this docstring blamed the
        three characters; a later one over-corrected and called them
        load-bearing. MEASURED, both false: with the redirect removed, stdout is
        byte-identical and rc is identical over a store with an unreadable
        dot-symlink — the only difference is one extra `Permission denied` line
        on stderr, which nothing consumes. The redirect suppresses an
        EXPECTED-condition diagnostic and nothing more, which is why no test
        catches its removal and why none should.

        The probe briefly treated a failed `find` as "no markdown here",
        which turned an unreadable symlink target from a loud over-report into
        complete silence — no NOTE, rc 0, `seed: OK`, and the operator loses
        that scope's contents with nothing to say so. `_shippable_entries` does
        not descend the symlink either, so nothing else surfaces it."""
        env, dest = fake_cluster
        locked = tmp_path / "locked-target"
        locked.mkdir()
        (locked / "l.md").write_text(_entry("l", "locked"))
        (store / "lockedsym").symlink_to(locked)
        locked.chmod(0o000)
        try:
            r = self._push(store, tmp_path, env)
        finally:
            locked.chmod(0o755)  # so tmp_path cleanup can proceed

        assert r.returncode == 0, (
            f"rc={r.returncode} — measured 0 today; a future change that "
            f"aborted after the NOTE would otherwise stay green. {r.stderr}"
        )
        assert "lockedsym" in "".join(
            l for l in r.stdout.splitlines() if l.startswith("seed:   ")
        ), (
            "an unreadable symlinked scope was silently dropped instead of "
            f"announced: {r.stdout}"
        )
        # 🔴 AND THE WORDING MUST NOT ASSERT WHAT THE PROBE COULD NOT ESTABLISH.
        # Announcing on error fixed the silence; reusing the "holds .md files"
        # wording then claimed markdown in a directory nobody could read — the
        # same unestablished-claim defect, one round later.
        assert "UNREADABLE" in r.stdout, (
            "the unreadable case must say so, not borrow the holds-markdown "
            f"wording: {r.stdout}"
        )
        self._assert_note_header(r.stdout, 1)

    def test_an_UNREADABLE_target_with_NO_markdown_is_not_called_a_holder(
        self, store: Path, tmp_path: Path, fake_cluster
    ):
        """🔴 The case the sibling above CANNOT reach, because its target really
        does hold `l.md` — so it would pass even while the wording lied.

        Here the target holds no markdown at all and is unreadable. Announcing
        it is right; announcing it as a directory that "holds .md files" is a
        claim about bytes nobody could see, and is exactly the defect that the
        fix for the previous round's silence reintroduced."""
        env, dest = fake_cluster
        locked = tmp_path / "empty-locked"
        locked.mkdir()
        (locked / "notes.txt").write_text("no markdown here\n")
        (store / "emptylocked").symlink_to(locked)
        locked.chmod(0o000)
        try:
            r = self._push(store, tmp_path, env)
        finally:
            locked.chmod(0o755)  # so tmp_path cleanup can proceed

        assert r.returncode == 0, f"stdout={r.stdout} stderr={r.stderr}"
        # 🔴 SCOPED TO THE NOTE LINES. `"emptylocked" in r.stdout` was satisfied
        # by the unrelated per-scope line `staged scope emptylocked entries=0`,
        # so the half this test exists for — that it is ANNOUNCED — guarded
        # nothing: reverting `_md_state`'s error state to a plain "no" removed
        # the NOTE entirely and this assertion stayed green.
        note = "\n".join(
            l for l in r.stdout.splitlines() if l.startswith("seed:   ")
        )
        assert "emptylocked" in note, (
            "a scope whose contents could not be read must still be ANNOUNCED, "
            f"not merely mentioned in the per-scope lines: {r.stdout}"
        )
        assert "UNREADABLE" in note, (
            "announced, but not as the state that was actually established: "
            + r.stdout
        )
        self._assert_note_header(r.stdout, 1)

    def test_the_DOT_arm_of_the_unreadable_state_is_reachable_and_covered(
        self, store: Path, tmp_path: Path, fake_cluster
    ):
        """🔴 The `2)` arm of the DOT branch had no coverage — deleting it left
        every test green.

        It is reachable, but not the obvious way: a real unreadable dot
        DIRECTORY never gets here, because `rsync` fails first (rc 23) and
        `set -e` aborts the staging. The path that does reach it is a
        dot-NAMED SYMLINK to an unreadable target — the dot branch wins the
        `if`/`elif`, and the probe cannot read through the link."""
        env, dest = fake_cluster
        locked = tmp_path / "dot-locked-target"
        locked.mkdir()
        (locked / "d.md").write_text(_entry("d", "dotsym"))
        (store / ".dotsym").symlink_to(locked)
        locked.chmod(0o000)
        try:
            r = self._push(store, tmp_path, env)
        finally:
            locked.chmod(0o755)

        assert r.returncode == 0, f"stdout={r.stdout} stderr={r.stderr}"
        note = "\n".join(l for l in r.stdout.splitlines() if l.startswith("seed:   "))
        assert ".dotsym" in note, f"the dot arm did not announce it: {r.stdout}"
        assert "dot-directory — UNREADABLE" in note, (
            "reached the dot arm but not its unreadable state — the wrong "
            f"reason would still read as covered: {note}"
        )
        # 🔴 PIN THE WHOLE HEADER, NOT A KEYWORD. This one sentence has been
        # FALSE twice in two rounds, each time on a different axis: "hold .md
        # files" asserted contents an unreadable probe could not read, and
        # "will NOT ship" was wrong because a symlinked scope DOES ship — as a
        # symlink, which the very next line of output says. Both reverts passed
        # a suite that only checked for absent keywords, because a keyword guard
        # is satisfied by any rewording. When the artifact under test is prose,
        # the normalised whole is the only machine-readable claim. A deliberate
        # reword must fail here and be re-verified; that cost is the point.
        self._assert_note_header(r.stdout, 1)

    def test_the_stub_REFUSES_to_run_with_FAKE_DEST_unset(
        self, store: Path, tmp_path: Path, fake_cluster
    ):
        """Pins the `set -u` the bash→POSIX rewrite dropped and round 2 restored.

        Without it an unset `$FAKE_DEST` makes the stub's `sed` become
        `s|/data||g`, silently rewriting every absolute path to a relative one —
        the stub then 'works' against the wrong directory and every assertion
        built on it is meaningless. That regression was re-introducible with a
        fully green suite until this test existed."""
        env, _ = fake_cluster
        env = {k: v for k, v in env.items() if k != "FAKE_DEST"}

        r = self._push(store, tmp_path, env)

        # 🔴 ASSERT THE GUARD'S OWN MESSAGE, NOT MERELY A NON-ZERO RC. `rc != 0`
        # alone does NOT isolate this: without the guard the stub still fails,
        # later and for an unrelated reason (`tar -C ""`), so the mutant survived
        # a green run. The specific string is what proves THIS guard fired.
        assert r.returncode != 0, (
            f"the stub ran with FAKE_DEST unset instead of erroring. stdout={r.stdout}"
        )
        assert "FAKE_DEST must be set" in r.stderr + r.stdout, (
            "the stub failed, but not because of its own unset-variable guard — "
            "so this test does not pin that guard. "
            f"stderr={r.stderr!r} stdout={r.stdout!r}"
        )

    def test_an_EMPTY_dot_scope_is_not_announced_as_holding_md_files(
        self, store: Path, tmp_path: Path, fake_cluster
    ):
        """Both arms must PROBE before naming a directory — the symlink arm
        used to fire unconditionally, announcing a scope that held no markdown
        at all.

        ⚠ The header no longer says "hold .md files" (it says "contribute NO
        entries"), so announcing an empty excluded scope would no longer be a
        FALSE claim — but it is still noise about a directory the operator
        cannot act on, and the probe is what keeps the list meaningful. Do not
        "restore" the old wording on the strength of this test's name: it was
        removed because it asserted contents an unreadable probe cannot read."""
        env, dest = fake_cluster
        empty_real = tmp_path / "empty-target"
        empty_real.mkdir()
        (store / "emptysym").symlink_to(empty_real)
        (store / ".emptydot").mkdir()

        r = self._push(store, tmp_path, env)

        assert r.returncode == 0, f"stdout={r.stdout} stderr={r.stderr}"
        # Scoped to the NOTE block: both scopes legitimately appear in the
        # per-scope `entries=0` lines, and asserting over the whole of stdout
        # would fail on that correct output.
        note = "\n".join(
            l for l in r.stdout.splitlines()
            if l.startswith("seed: NOTE") or l.startswith("seed:   ")
        )
        assert "emptysym" not in note, (
            "a symlinked scope holding NO .md was announced as holding some:\n"
            + note
        )
        assert ".emptydot" not in note, note

    def test_a_TOP_LEVEL_md_in_the_store_is_not_reported_missing(
        self, store: Path, tmp_path: Path, fake_cluster
    ):
        """The store root really does hold a `README.md`. It is not an entry,
        the tar member list never ships it, and `-mindepth 2` is what keeps it
        out of the comparison — a dimension no earlier fixture had, so dropping
        that flag survived the sweep while breaking every real push."""
        env, dest = fake_cluster
        (store / "README.md").write_text("# the store's own readme\n")

        r = self._push(store, tmp_path, env)

        assert r.returncode == 0, f"stdout={r.stdout} stderr={r.stderr}"
        assert "README.md" not in r.stderr
        assert "seed: OK" in r.stdout

    def test_a_DOT_DIRECTORY_scope_is_not_reported_missing(
        self, store: Path, tmp_path: Path, fake_cluster
    ):
        """`staged_entries` and the tar member list are both built from
        `"$STAGE"/*/`, which without `dotglob` skips dot-directories — so a
        `.hidden/` scope is never archived. A bare `find` would list it as
        staged and then report it missing: "1 of 1 staged entry file(s) did NOT
        land" over something that was never shipped. The compared population has
        to be the PUSHED one."""
        env, dest = fake_cluster
        hidden = store / ".hidden"
        hidden.mkdir()
        (hidden / "b.md").write_text(_entry("b", ".hidden"))

        r = self._push(store, tmp_path, env)

        assert r.returncode == 0, (
            f"a dot-scope is not pushed, so it must not be reported missing. "
            f"stdout={r.stdout} stderr={r.stderr}"
        )
        assert ".hidden" not in r.stderr
        assert "seed: OK" in r.stdout
        # 🔴 Excluding it is right; saying nothing about it just moves the lie
        # from a wrong MISMATCH to a silent omission.
        assert ".hidden (dot-directory" in r.stdout, (
            "the excluded scope must be NAMED, not silently dropped: " + r.stdout
        )
        # 🔴 AND IT MUST ACTUALLY NOT SHIP. The NOTE says "not in the tar member
        # list"; that is only true while `dotglob` is off when the member list is
        # built. Leaving it set SURVIVED the sweep — the entry then lands on the
        # pod while the remote listing still excludes `./.*`, so it is shipped,
        # never verified, and the NOTE's sentence becomes false.
        assert not (dest / ".hidden").exists(), (
            "the dot-scope was SHIPPED despite the NOTE saying it would not be — "
            f"pod holds: {sorted(p.name for p in dest.iterdir())}"
        )


class TestSeedIsNonDestructive:
    def test_the_SOURCE_tree_is_byte_identical_after_a_seed(
        self, store: Path, tmp_path: Path
    ):
        before = tree_hash(store)
        result = run_seed("--store", str(store), "--stage", str(tmp_path / "stage"))
        assert result.returncode == 0, result.stderr
        assert tree_hash(store) == before

    def test_POSITIVE_CONTROL_the_hasher_sees_a_one_character_change(self, store: Path):
        """🔴 Reported BESIDE the verdict above. "unchanged" from a hasher never
        watched to change is indistinguishable from a hasher wired to nothing."""
        before = tree_hash(store)
        path = store / SCOPE / "thing-alpha.md"
        path.write_text(path.read_text().replace("40s", "41s"))
        assert tree_hash(store) != before

    def test_seeding_over_a_populated_stage_removes_only_STAGE_files(
        self, store: Path, tmp_path: Path
    ):
        stage = tmp_path / "stage"
        stage.mkdir()
        (stage / "leftover-from-an-older-run.md").write_text("stale\n")
        before = tree_hash(store)
        assert run_seed("--store", str(store), "--stage", str(stage)).returncode == 0
        assert not (stage / "leftover-from-an-older-run.md").exists()
        assert tree_hash(store) == before

    def test_the_stage_is_a_faithful_copy_APART_FROM_THE_STAMP(
        self, store: Path, tmp_path: Path
    ):
        """The stage mirrors the source byte-for-byte, plus EXACTLY one file.

        🔴 THIS ASSERTION GOT NARROWER, NOT WEAKER, AND THAT IS THE POINT.
        It used to be a bare `tree_hash(stage) == tree_hash(store)`, which
        `.seed-stamp` breaks: the stage is deliberately no longer a pure
        byte-copy, because a copy that cannot say when it was taken is the
        entire defect the stamp exists to fix (`server.snapshot_freshness`, and
        the incident in the README). RULES.md — "when a test documents a
        contract, ask whether the contract is right": the contract changed, so
        the test states the NEW one exactly rather than being loosened to
        "mostly the same", which would have surrendered the property actually
        worth keeping — that nothing ELSE ever appears in the stage.

        So: the extra-path set is pinned to exactly `{.seed-stamp}` (it fails if
        that set GROWS *or* SHRINKS), and with the stamp removed the remaining
        tree is still hashed byte-for-byte against the source.
        """
        stage = tmp_path / "stage"
        assert run_seed("--store", str(store), "--stage", str(stage)).returncode == 0

        stamp = stage / ".seed-stamp"
        assert stamp.exists(), "the stage carries no stamp — seed.sh did not date it"

        staged = {p.relative_to(stage) for p in stage.rglob("*") if p.is_file()}
        source = {p.relative_to(store) for p in store.rglob("*") if p.is_file()}
        assert staged - source == {Path(".seed-stamp")}, (
            f"unexpected extra path(s) in the stage: {staged - source}"
        )
        assert not source - staged, f"the stage is MISSING: {source - staged}"

        stamp.unlink()
        assert tree_hash(stage) == tree_hash(store)

    def test_the_summary_prints_the_COUNT_beside_what_produced_it(
        self, store: Path, tmp_path: Path
    ):
        out = run_seed("--store", str(store), "--stage", str(tmp_path / "stage")).stdout
        assert "seed: STAGED scopes=4 entries=3" in out
        assert f"from={store}" in out

    def test_a_run_without_push_SAYS_it_proved_nothing_about_a_pod(
        self, store: Path, tmp_path: Path
    ):
        out = run_seed("--store", str(store), "--stage", str(tmp_path / "stage")).stdout
        assert "PUSH skipped" in out
        assert "proves nothing about any pod" in out


class TestSeedGuards:
    """Each guard reachable by an input no earlier guard rejects, each with its
    OWN exit code and sentence — so a test cannot pass because a NEIGHBOURING
    guard fired."""

    def test_an_absent_store_root_exits_3_and_says_nothing_was_pushed(
        self, tmp_path: Path
    ):
        r = run_seed("--store", str(tmp_path / "nope"), "--stage", str(tmp_path / "s"))
        assert r.returncode == 3
        assert "store root not found" in r.stderr
        assert "nothing was pushed" in r.stderr
        assert not (tmp_path / "s").exists(), "an absent source must stage NOTHING"

    def test_an_EXISTING_but_scopeless_root_exits_4_not_3(self, tmp_path: Path):
        # Guard 1 passes here — the directory exists. This is the silent-wipe
        # case: staging an empty tree over a populated /data.
        empty = tmp_path / "empty-root"
        empty.mkdir()
        r = run_seed("--store", str(empty), "--stage", str(tmp_path / "s"))
        assert r.returncode == 4
        assert "NO scope directories" in r.stderr
        assert "refusing" in r.stderr

    def test_missing_arguments_exit_2(self, tmp_path: Path):
        assert run_seed("--stage", str(tmp_path / "s")).returncode == 2
        assert run_seed("--store", str(tmp_path)).returncode == 2

    def test_a_bad_push_target_is_rejected_BEFORE_any_kubectl_call(
        self, store: Path, tmp_path: Path
    ):
        r = run_seed(
            "--store", str(store), "--stage", str(tmp_path / "s"), "--push", "no-slash"
        )
        assert r.returncode == 2
        assert "namespace" in r.stderr


class TestSeedNeverAddsARemote:
    """🔴 THE POLICY THIS MIGRATION MUST NOT QUIETLY BECOME.

    The store's README forbids a git remote on any scope, and three tests in
    `test_analyze_service_index_commit.py` enforce it. Replication here happens
    over HTTP, not `git push` — so a seed must leave both the source AND the
    staged copy with zero remotes. This is the behavioural check; the three
    existing tests are untouched by this branch.
    """

    def _remotes(self, path: Path) -> str:
        return subprocess.run(
            ["git", "-C", str(path), "remote"], capture_output=True, text=True
        ).stdout.strip()

    def test_neither_the_source_nor_the_stage_gains_a_remote(
        self, store: Path, tmp_path: Path
    ):
        scope_dir = store / SCOPE
        env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "HOME": str(tmp_path),
               **hermetic_git.MAINTENANCE_OFF}
        for args in (
            ["init", "-q", "-b", "main"],
            ["config", "user.email", "t@example.invalid"],
            ["config", "user.name", "T"],
            ["add", "thing-alpha.md"],
            ["commit", "-qm", "seed"],
        ):
            subprocess.run(
                ["git", "-C", str(scope_dir), *args], check=True, capture_output=True,
                env=env,
            )
        assert self._remotes(scope_dir) == ""

        stage = tmp_path / "stage"
        assert run_seed("--store", str(store), "--stage", str(stage)).returncode == 0

        assert self._remotes(scope_dir) == "", "the SOURCE gained a remote"
        assert self._remotes(stage / SCOPE) == "", "the STAGE gained a remote"

    def test_the_remote_probe_can_SEE_a_remote(self, store: Path, tmp_path: Path):
        """Positive control: an empty `git remote` from a probe that never works
        is indistinguishable from a repo with no remotes."""
        scope_dir = store / OTHER_SCOPE
        env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "HOME": str(tmp_path),
               **hermetic_git.MAINTENANCE_OFF}
        subprocess.run(
            ["git", "-C", str(scope_dir), "init", "-q", "-b", "main"],
            check=True, capture_output=True, env=env,
        )
        subprocess.run(
            ["git", "-C", str(scope_dir), "remote", "add", "origin",
             "https://example.invalid/x.git"],
            check=True, capture_output=True, env=env,
        )
        assert self._remotes(scope_dir) == "origin"


# =============================================================================
# 11. 🔴 THE PHASE-1 ACCEPTANCE COMPARATOR, exercised in BOTH directions.
# =============================================================================


def run_verify(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(VERIFY_PATH), *args], capture_output=True, text=True, timeout=300
    )


# 🔴 PINNED LITERALLY, PASS LINES ONLY. The field names are spelled here by hand
# rather than imported or derived, per this file's header: a rename in the
# script must break these tests, not be absorbed by them. It is anchored on
# `PASS` because the accounting identity is a claim about a run the comparator
# called byte-identical; a FAIL line prints a different field set on purpose.
_EVIDENCE_RE = re.compile(
    r"^PASS scope=\S+ bytes=\d+ "
    r"raw-diff-lines=(?P<raw>\d+) "
    r"store-root-lines=(?P<store_root>\d+) "
    r"host-lines=(?P<host>\d+) "
    r"snapshot-line=(?P<snapshot>\d+) "
    r"snapshot-block-lines=(?P<block>\d+) "
    r"accounted-for=(?P<accounted>\d+) ",
    re.MULTILINE,
)


def _evidence_rows(stdout: str) -> list[dict[str, int]]:
    """Every PASS line's accounting, as ints."""
    return [
        {k: int(v) for k, v in m.groupdict().items()}
        for m in _EVIDENCE_RE.finditer(stdout)
    ]


def _wider_separator(module):
    """A `snapshot_freshness` whose prose ends in a newline.

    🔴 IT MODELS A DIFFERENT IMAGE, NOT A BROKEN ONE, AND THE SKEW IS
    SYNTHETIC. `_serve_report` builds the body as `prose + "\\n\\n" + text`, so
    a prose carrying its own trailing newline yields banner + TWO blank
    separators — a three-line transport annotation instead of this tree's two.

    ⚠ NO DEPLOYED IMAGE HAS BEEN SEEN TO EMIT THAT. This fixture exists because
    the verifier is pointed at a POD, and the pod's `server.py` is whatever was
    last deployed rather than this checkout: hardcoding the block's length is a
    bet on those two being the same version, and nothing in the script can check
    that bet. It is the unobserved case made reachable, not a reproduction of
    one that happened.
    """
    real = module.snapshot_freshness

    def _wrapped(store_root):
        header, prose = real(store_root)
        return header, prose + "\n"

    return _wrapped


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    path = tmp_path / "token"
    path.write_text(GOOD_TOKEN + "\n")
    path.chmod(0o600)
    return path


class TestByteIdentityVerifier:
    def test_POSITIVE_identical_stores_PASS_for_every_scope(
        self, store: Path, token_file: Path
    ):
        with running(store) as (base, _):
            r = run_verify(
                "--store", str(store), "--url", base, "--token-file", str(token_file)
            )
        assert r.returncode == 0, r.stdout + r.stderr
        # Every scope compared, and the count printed BESIDE the verdict.
        assert "verify: scopes=4 pass=4 fail=0" in r.stdout
        for scope in (SCOPE, OTHER_SCOPE, EMPTY_SCOPE, BROKEN_SCOPE):
            assert f"PASS scope={scope}" in r.stdout

    def test_NEGATIVE_a_ONE_CHARACTER_divergence_FAILS_and_names_the_scope(
        self, store: Path, tmp_path: Path, token_file: Path
    ):
        """🔴 The control that makes the PASS above mean anything.

        The served copy differs from the local one by a single character inside
        one entry, in one scope. A comparator that always says PASS, or that
        compares the wrong thing, is green here.
        """
        served = tmp_path / "served"
        subprocess.run(
            ["cp", "-a", str(store), str(served)], check=True, capture_output=True
        )
        path = served / SCOPE / "thing-alpha.md"
        path.write_text(path.read_text().replace("40s", "41s"))

        with running(served) as (base, _):
            r = run_verify(
                "--store", str(store), "--url", base, "--token-file", str(token_file)
            )
        assert r.returncode == 1
        assert f"FAIL scope={SCOPE}" in r.stdout
        # The other scopes are still identical, so the failure is attributed and
        # not a blanket red.
        assert f"PASS scope={OTHER_SCOPE}" in r.stdout
        assert "pass=3 fail=1" in r.stdout

    def test_NEGATIVE_a_MISSING_entry_on_the_remote_FAILS(
        self, store: Path, tmp_path: Path, token_file: Path
    ):
        # A seed that half-copied. Different shape from the mutation above:
        # nothing is wrong with any served byte, there is simply less of it.
        served = tmp_path / "served"
        subprocess.run(
            ["cp", "-a", str(store), str(served)], check=True, capture_output=True
        )
        (served / OTHER_SCOPE / "thing-beta.md").unlink()
        with running(served) as (base, _):
            r = run_verify(
                "--store", str(store), "--url", base, "--token-file", str(token_file)
            )
        assert r.returncode == 1
        assert f"FAIL scope={OTHER_SCOPE}" in r.stdout
        # 🔴 AND it kept going. A failing scope must not abort the sweep: the
        # first version of this script died inside `diff | head` under
        # `set -o pipefail` and reported one FAIL with three scopes silently
        # uncompared, which reads as a narrower problem than it is.
        assert "verify: scopes=4" in r.stdout
        assert "pass=3 fail=1" in r.stdout

    def test_every_permitted_difference_is_ACCOUNTED_FOR_not_merely_small(
        self, store: Path, tmp_path: Path, token_file: Path
    ):
        """The pod serves `/data`; the workbench serves `~/.claude/…`.

        🔴 RENAMED, BECAUSE THE OLD NAME BECAME FALSE. This was
        `test_the_STORE_ROOT_line_is_the_only_permitted_difference`, asserting a
        flat `raw-diff-lines=2 store-root-lines=2`. The snapshot block
        (`server.snapshot_freshness`) is a SECOND legitimate difference — the
        remote dates the copy it serves and the local CLI, reading the
        authoritative store, correctly does not — so "the only permitted
        difference" stopped being true the moment that shipped. RULES.md: "a
        comment is a claim too"; a name is louder than a comment.

        The replacement is STRONGER than a bumped constant. It asserts the raw
        difference is FULLY DECOMPOSED by its named causes:

            raw == store_root_lines + host_lines + snapshot_block_lines

        An unexplained differing line therefore still fails — which a hardcoded
        `raw-diff-lines=4` would not, since it would go on passing if a
        store-root line vanished and some other difference appeared in its
        place.

        🔴 `host_lines` JOINED THE SUM WITH THE `host:` CANONICALISATION, in the
        same commit, and that ordering is the point: a rule that erases a
        difference without a matching count widens the blind spot rather than
        the gate. Here both sides run on this machine, so `host_lines` is 0 and
        the identity is unchanged from the two-cause version — the case where it
        is 2 is `test_a_POD_SHAPED_remote_PASSES_when_only_the_THREE_permitted_
        lines_differ` below, which is where that term is actually exercised.
        `snapshot_block_lines` replaces `2 * snapshot_lines`: the block's LENGTH
        is now measured rather than assumed, so a served image whose separator
        arrangement is not this tree's is still fully accounted for.
        """
        served = tmp_path / "served-elsewhere"
        subprocess.run(
            ["cp", "-a", str(store), str(served)], check=True, capture_output=True
        )
        with running(served) as (base, _):
            r = run_verify(
                "--store", str(store), "--url", base, "--token-file", str(token_file)
            )
        assert r.returncode == 0, r.stdout + r.stderr

        rows = _evidence_rows(r.stdout)
        assert rows, f"the verifier printed no evidence rows:\n{r.stdout}"
        for row in rows:
            assert row["raw"] == row["store_root"] + row["host"] + row["block"], (
                f"unaccounted differing lines: {row}"
            )
            # The script's own arithmetic, read back rather than re-derived —
            # a printed `accounted-for` that disagreed with its own parts would
            # be a reader-facing lie even while the identity above held.
            assert row["accounted"] == row["store_root"] + row["host"] + row["block"], (
                f"the printed accounted-for does not equal its own parts: {row}"
            )
        # …and the causes exercised here are genuinely PRESENT, or the identity
        # above is satisfiable by a run that compared nothing (0 == 0 + 0 + 0).
        assert any(row["snapshot"] == 1 for row in rows), "no snapshot line observed"
        assert any(row["block"] == 2 for row in rows), (
            "no snapshot BLOCK observed — this tree's server emits the banner "
            "plus exactly one blank separator, so a 2 is the expected length"
        )
        assert any(row["store_root"] == 2 for row in rows), "no store-root line observed"
        # Both sides are this machine, so the host line must be IDENTICAL here.
        # A non-zero would mean the harness accidentally diverged the two hosts,
        # which would make the pod-shaped test below prove nothing new.
        assert all(row["host"] == 0 for row in rows), (
            f"the local self-check saw a host-line difference: {rows}"
        )

    def test_a_POD_SHAPED_remote_PASSES_when_only_the_THREE_permitted_lines_differ(
        self, store: Path, tmp_path: Path, token_file: Path, monkeypatch
    ):
        """🔴 THE REGRESSION. This is the run that could not pass at all.

        Every earlier test in this class compares two renders produced ON THE
        SAME MACHINE, so their `host:` lines are byte-identical and the verifier
        never had to canonicalise one. Against the actual pod they are different
        BY CONSTRUCTION — `store_host_line()` names the machine whose disk was
        read, and that is the entire reason it exists — so the comparator was
        structurally unable to return anything but FAIL, on every scope, for a
        store whose content was identical. `claude/RULES.md`: a permanently-red
        gate is worse than no gate.

        Three differences are set up here and NOTHING else:

          * `store:`  — the served copy lives at a different root;
          * `host:`   — the server renders a pod identity, the CLI subprocess
                        this machine's;
          * the SNAPSHOT block — and deliberately NOT in this tree's shape. The
            served prose carries an extra separator, so the block is THREE lines
            rather than two.

        ⚠ THAT THIRD DIFFERENCE IS A SYNTHESISED SKEW, AND ONLY THE FIRST TWO
        ARE PART OF THE REGRESSION. No deployed image has been seen to emit a
        three-line block, and the old anchored two-line delete was correct for
        the arrangement this tree emits — `test_every_permitted_difference_is_
        ACCOUNTED_FOR_not_merely_small` passes on it before and after. The skew
        is here because the verifier compares against whatever image is
        DEPLOYED, not this checkout, so a hardcoded length is an unverifiable
        bet; measuring it also makes the deletion countable in the accounting,
        which a fixed-size delete never was.

        The negative control is the next test, not a comment: the same three
        permitted differences plus ONE mutated character must still FAIL.
        """
        served = tmp_path / "served-elsewhere"
        subprocess.run(
            ["cp", "-a", str(store), str(served)], check=True, capture_output=True
        )
        monkeypatch.setattr(touch, "store_host", lambda: POD_HOST)
        monkeypatch.setattr(api, "snapshot_freshness", _wider_separator(api))

        with running(served) as (base, _):
            r = run_verify(
                "--store", str(store), "--url", base, "--token-file", str(token_file)
            )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "verify: scopes=4 pass=4 fail=0" in r.stdout

        rows = _evidence_rows(r.stdout)
        assert len(rows) == 4, f"expected one evidence row per scope:\n{r.stdout}"
        for row in rows:
            # 🔴 EACH PERMITTED DIFFERENCE PRESENT AND COUNTED. Asserting only
            # the PASS would be satisfied by a canonicalisation that flattened
            # the whole stream.
            assert row["store_root"] == 2, f"no store-root difference: {row}"
            assert row["host"] == 2, f"no host-line difference: {row}"
            assert row["block"] == 3, (
                f"the served banner + TWO blank separators were not measured "
                f"as a 3-line block: {row}"
            )
            assert row["raw"] == row["store_root"] + row["host"] + row["block"], (
                f"unaccounted differing lines: {row}"
            )
            assert row["accounted"] == row["raw"], (
                f"the printed accounted-for does not cover the raw diff: {row}"
            )

    def test_a_POD_SHAPED_remote_STILL_FAILS_on_a_real_content_difference(
        self, store: Path, tmp_path: Path, token_file: Path, monkeypatch
    ):
        """🔴 The control for the test above. Same three permitted differences,
        plus a single changed character inside one entry.

        Without this, "the pod-shaped run passes" is equally true of a verifier
        that stopped comparing anything — which is exactly the failure mode a
        wider canonicalisation introduces, and the reason the new rules are
        counted rather than merely applied.
        """
        served = tmp_path / "served-elsewhere"
        subprocess.run(
            ["cp", "-a", str(store), str(served)], check=True, capture_output=True
        )
        path = served / SCOPE / "thing-alpha.md"
        path.write_text(path.read_text().replace("40s", "41s"))

        monkeypatch.setattr(touch, "store_host", lambda: POD_HOST)
        monkeypatch.setattr(api, "snapshot_freshness", _wider_separator(api))

        with running(served) as (base, _):
            r = run_verify(
                "--store", str(store), "--url", base, "--token-file", str(token_file)
            )
        assert r.returncode == 1, r.stdout + r.stderr
        assert f"FAIL scope={SCOPE}" in r.stdout
        # Attributed, not a blanket red: the other scopes differ by the three
        # permitted lines ONLY and still pass.
        assert f"PASS scope={OTHER_SCOPE}" in r.stdout
        assert "pass=3 fail=1" in r.stdout

    def test_LEADING_BLANKS_WITHOUT_the_banner_are_NOT_stripped(
        self, store: Path, token_file: Path, monkeypatch
    ):
        """🔴 THE NARROWING, EXERCISED — not merely asserted in a comment.

        The snapshot block is measured as a run of banner-or-blank lines at the
        head of the remote stream, and a run that does NOT contain the banner
        must be left alone: blank lines the server put there for some other
        reason are a real difference, and a rule that swallowed them would be
        the "erase a difference it does not claim" failure this whole commit is
        about.

        Same store on both sides, so `store:` and `host:` are identical and the
        ONLY difference is two leading blanks. The verifier must FAIL, and its
        accounting must say `snapshot-block-lines=0` — it stripped nothing, and
        it says so.
        """
        # An image that emits the separator but no banner. `_serve_report` builds
        # `prose + "\n\n" + text`, so an empty prose is exactly two blank lines.
        monkeypatch.setattr(api, "snapshot_freshness", lambda root: ("x", ""))

        with running(store) as (base, _):
            r = run_verify(
                "--store", str(store), "--url", base, "--token-file", str(token_file)
            )
        assert r.returncode == 1, (
            f"two unexplained leading blank lines were swallowed:\n{r.stdout}"
        )
        assert "verify: scopes=4 pass=0 fail=4" in r.stdout
        for line in r.stdout.splitlines():
            if line.startswith("FAIL scope="):
                assert "snapshot-block-lines=0" in line, (
                    f"the block claimed lines it did not earn: {line}"
                )
                assert "raw-diff-lines=2 " in line, (
                    f"expected exactly the two leading blanks to differ: {line}"
                )

    def test_a_server_that_serves_NO_SNAPSHOT_BLOCK_at_all_still_PASSES(
        self, store: Path, token_file: Path
    ):
        """🔴 THE 0.2.0 PATH. The stamp shipped in 0.3.0; this script has to keep
        working against an image that predates it.

        The old rule got that for free — a `sed` address that matches nothing
        deletes nothing. The measured rule does not: it computes a block LENGTH,
        and `sed '1,0d'` is an ERROR, not a no-op, so an empty block has to be
        branched on rather than passed through. That branch is the whole reason
        this test exists; without it the script would exit non-zero on every
        scope against a pre-0.3.0 pod and the failure would read as a content
        difference.

        The stub replays the local CLI's own bytes per scope — a server that
        renders identically and stamps nothing.
        """
        import http.server

        bodies = {}
        for scope in (SCOPE, OTHER_SCOPE, EMPTY_SCOPE, BROKEN_SCOPE):
            out = subprocess.run(
                [sys.executable, str(RECALL_PATH), "--store", str(store),
                 "--scope", scope],
                capture_output=True, timeout=HANG_TIMEOUT,
            )
            # exit 3 is "nothing readable" — a legitimate render, which the
            # verifier itself tolerates. Anything harder is a broken fixture.
            assert out.returncode <= 3, out.stderr.decode()
            bodies[scope] = out.stdout

        class Unstamped(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                body = bodies.get(self.path.rsplit("/", 1)[-1])
                if body is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):  # noqa: D102
                pass

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Unstamped)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            r = run_verify(
                "--store", str(store),
                "--url", f"http://127.0.0.1:{httpd.server_address[1]}",
                "--token-file", str(token_file),
            )
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=10)

        assert r.returncode == 0, r.stdout + r.stderr
        assert "verify: scopes=4 pass=4 fail=0" in r.stdout
        # 🔴 And the accounting says WHY it is green: nothing differed and
        # nothing was canonicalised away. A green with a non-zero block here
        # would mean the strip invented a block to delete.
        rows = _evidence_rows(r.stdout)
        assert len(rows) == 4, f"expected one evidence row per scope:\n{r.stdout}"
        for row in rows:
            assert row == {
                "raw": 0, "store_root": 0, "host": 0,
                "snapshot": 0, "block": 0, "accounted": 0,
            }, f"an unstamped identical render was not a clean zero: {row}"

    def test_an_UNREACHABLE_pod_FAILS_rather_than_comparing_nothing(
        self, store: Path, token_file: Path
    ):
        # Nothing is listening. A comparator that treated an empty body as
        # "identical to an empty local render" would report success here.
        r = run_verify(
            "--store", str(store),
            "--url", "http://127.0.0.1:1",
            "--token-file", str(token_file),
        )
        assert r.returncode == 1
        assert "FAIL" in r.stdout

    def test_a_WRONG_token_FAILS_rather_than_reporting_identity(
        self, store: Path, tmp_path: Path
    ):
        bad = tmp_path / "bad-token"
        bad.write_text("q" * 48 + "\n")
        with running(store) as (base, _):
            r = run_verify(
                "--store", str(store), "--url", base, "--token-file", str(bad)
            )
        assert r.returncode == 1
        assert "remote HTTP 401" in r.stdout

    def test_a_200_with_an_EMPTY_body_FAILS_rather_than_comparing_equal(
        self, store: Path, token_file: Path
    ):
        """🔴 Two empty streams `cmp` equal. That is the shape in which a proxy,
        a misrouted ingress or a half-written response reads as byte-identity.

        The stub answers 200 to everything with a zero-length body — a realistic
        failure, not a textbook one: it is what a Traefik route pointed at the
        wrong service returns.
        """
        import http.server

        class Blank(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):  # noqa: D102
                return

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Blank)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            r = run_verify(
                "--store", str(store),
                "--url", f"http://127.0.0.1:{httpd.server_address[1]}",
                "--token-file", str(token_file),
            )
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=10)
        assert r.returncode == 1, r.stdout
        assert "empty render" in r.stdout
        assert "pass=0 fail=4" in r.stdout

    def test_ZERO_scopes_is_a_FAILURE_not_an_all_clear(
        self, tmp_path: Path, token_file: Path
    ):
        """🔴 The silent zero. A comparison over no scopes passes trivially, and
        `pass=0 fail=0` reads exactly like success."""
        empty = tmp_path / "empty-store"
        empty.mkdir()
        r = run_verify(
            "--store", str(empty),
            "--url", "http://127.0.0.1:1",
            "--token-file", str(token_file),
        )
        assert r.returncode == 4
        assert "nothing was compared" in r.stderr

    def test_missing_inputs_are_refused(self, store: Path, token_file: Path):
        assert run_verify("--url", "http://x", "--token-file", str(token_file)).returncode == 2
        assert run_verify("--store", str(store), "--token-file", str(token_file)).returncode == 2
        assert run_verify("--store", str(store), "--url", "http://x").returncode == 2


# =============================================================================
# 12. The end-to-end shape phase 1 actually ships: seed, then verify.
# =============================================================================


class TestSeedThenVerify:
    def test_a_seeded_copy_serves_byte_identical_digests(
        self, store: Path, tmp_path: Path, token_file: Path
    ):
        """The phase-1 acceptance path in miniature, with the real scripts.

        Not a substitute for running it against the pod — a `--stage` directory
        is not a PVC and this machine is not the cluster — but it proves the two
        scripts compose, which is the seam neither of them owns alone.
        """
        stage = tmp_path / "stage"
        seeded = run_seed("--store", str(store), "--stage", str(stage))
        assert seeded.returncode == 0, seeded.stderr

        with running(stage) as (base, _):
            r = run_verify(
                "--store", str(store), "--url", base, "--token-file", str(token_file)
            )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "pass=4 fail=0" in r.stdout

    def test_a_seed_that_MISSED_a_scope_is_caught_by_the_verifier(
        self, store: Path, tmp_path: Path, token_file: Path
    ):
        # The composition's own negative control: the two scripts agreeing is
        # only evidence if a broken seed makes them disagree.
        stage = tmp_path / "stage"
        assert run_seed("--store", str(store), "--stage", str(stage)).returncode == 0
        subprocess.run(
            ["rm", "-rf", str(stage / OTHER_SCOPE)], check=True, capture_output=True
        )
        with running(stage) as (base, _):
            r = run_verify(
                "--store", str(store), "--url", base, "--token-file", str(token_file)
            )
        assert r.returncode == 1
        assert f"FAIL scope={OTHER_SCOPE}" in r.stdout


# =============================================================================
# 13. Phase scope — what this branch must NOT contain.
# =============================================================================


class TestPhaseOneScope:
    """🔴 Phase 1 is cluster-internal: read-only, no ingress, no write path.

    §4 phase 1.5 is explicit that the IngressRoute is "the last thing to land,
    not the first" — it is the moment the store becomes internet-reachable. This
    guard is structural (it reads the shipped files), so a write endpoint or an
    exposure added here fails a test rather than a review.
    """

    # 🔴 THE VERB LEDGER — the CONVERTED write guard (phase 3, criterion 7).
    #
    # It used to be a `str in SERVER_PATH.read_text()` for the exact line
    # `do_POST = do_PUT = do_PATCH = do_DELETE = _reject_write`, plus four
    # `"def do_POST" not in src` probes. That guard was WALKED IN THE COMMIT THAT
    # CONVERTED IT, and the walk is worth recording because nobody aimed at it:
    # the write path rebound the aliases to `_write` and, in the same edit,
    # QUOTED THE OLD LINE IN THE MODULE DOCSTRING while explaining the change.
    # The substring was still in the file, so all 408 tests stayed green over a
    # server that had just grown two write endpoints. `claude/RULES.md`: a guard
    # on a SPELLING is walkable by re-spelling — here, by writing the spelling
    # somewhere it does not execute.
    #
    # So it is structural now, over the CLASS rather than the source text. It
    # pins the whole `do_*` surface AND what each verb is bound to, in both
    # directions: a new verb (`do_OPTIONS`), a removed one, or an existing one
    # rebound to a different function all fail. Prose cannot satisfy it, because
    # nothing here reads prose.
    # The value is the name of the FUNCTION the verb resolves to. `do_GET` and
    # `do_HEAD` are their own one-line methods (so they name themselves); the
    # four mutating verbs are ALIASES of one function, and `__name__` is what
    # sees through an alias — a `def do_POST(self): …` would name itself and
    # fail, and `do_DELETE = _handle` would name `_handle` and fail.
    VERBS: "dict[str, str]" = {
        "do_GET": "do_GET",
        "do_HEAD": "do_HEAD",
        # Every mutating verb goes through ONE door. The ones with no row in
        # `WRITE_ROUTES` — PATCH and DELETE — reach it and take its 405 tail.
        "do_POST": "_write",
        "do_PUT": "_write",
        "do_PATCH": "_write",
        "do_DELETE": "_write",
    }

    def test_the_verb_ledger_is_the_whole_bound_verb_surface(self):
        bound = {
            name for name in dir(api.StoreRequestHandler) if name.startswith("do_")
        }
        assert bound == set(self.VERBS), (
            f"the handler binds {sorted(bound)} but the ledger says "
            f"{sorted(self.VERBS)} — add the verb to VERBS on purpose, or unbind it"
        )
        for verb, target in sorted(self.VERBS.items()):
            assert getattr(api.StoreRequestHandler, verb).__name__ == target, (
                f"{verb} resolves to "
                f"{getattr(api.StoreRequestHandler, verb).__name__}, not {target}"
            )

    def test_no_verb_is_declared_as_its_own_method(self):
        """The aliasing above is what makes the ledger readable in one place. A
        `def do_POST(self)` would satisfy the identity check only by being the
        thing it names, so the shape is pinned separately."""
        src = SERVER_PATH.read_text()
        for handler in ("def do_POST", "def do_PUT", "def do_PATCH", "def do_DELETE"):
            assert handler not in src

    # 🔴 THE LEDGER. Adding a route means adding it HERE, on purpose, in the
    # same commit. That is the whole point of the guard below.
    ROUTES: tuple[str, ...] = ("recall", "search", "snapshot")

    # …and the same rule for the WRITE table, which is keyed on `(verb, head)`
    # because `POST .../bullets` and `PUT .../<ref>` are different operations on
    # one noun. Adding a row here is adding a public, internet-reachable write
    # endpoint; the arity and the fixed tail are part of the row because
    # `len(parts) == arity` mutated to `>=` once survived 318 tests on the read
    # side, and the tail is the only thing that stops `POST entry/a/b/anything`
    # from dispatching as an append.
    WRITE_ROUTES: "dict[tuple[str, str], tuple[str, int, tuple[str, ...]]]" = {
        ("POST", "entry"): ("_append_bullet", 4, ("bullets",)),
        ("PUT", "entry"): ("_replace_entry", 3, ()),
    }

    def test_the_write_route_ledger_is_the_whole_write_route_set(self):
        assert api.WRITE_ROUTES == self.WRITE_ROUTES, (
            f"the write router dispatches {sorted(api.WRITE_ROUTES)} but the "
            f"ledger says {sorted(self.WRITE_ROUTES)}"
        )

    def test_every_ledgered_write_route_actually_dispatches(self):
        for key, (handler, arity, tail) in api.WRITE_ROUTES.items():
            assert hasattr(api.StoreRequestHandler, handler), f"{key} -> {handler}"
            assert arity >= 1, f"{key} has arity {arity}"
            assert len(tail) < arity, f"{key} tail {tail} is longer than its path"

    def test_the_route_ledger_is_the_whole_route_set(self, store: Path):
        """🔴 REWRITTEN TWICE, and the FIRST rewrite was still a spelled guard.

        v1 was named `test_the_only_routes_are_recall_and_search` and claimed to
        "walk the endpoint list". It probed four hardcoded non-routes, so adding
        `/api/v1/snapshot` left every test in this class green.

        v2 replaced that with `re.findall(r'parts\\[0\\]\\s*==\\s*"([a-z0-9-]+)"')`
        and was described in its PR as "derives the accepted set from the
        router". It does not — it derives the set from ONE SPELLING. An audit
        MEASURED the hole: adding `parts[0] == "raw_dump"` (an underscore, which
        the character class excludes) left all six tests in this class PASSING.
        Single quotes, `parts[0] in (...)`, reversed operands, uppercase and
        dict dispatch walk past it too. Its "positive control" fed it the one
        spelling it catches, so the control could not reveal any of that.

        v3 parses the ROUTER'S AST and collects every string compared against a
        `parts[...]` subscript — `==` in either operand order, and `in` over a
        tuple/list. Spelling is now irrelevant: quotes, case and underscores are
        all the same node to `ast`.

        🔴 WHAT THIS STILL CANNOT SEE, stated rather than implied: a route whose
        name never appears as a literal in a comparison against `parts` — a dict
        or table dispatch (`ROUTES[parts[0]]`), a computed name, or a
        `startswith` prefix match. `test_no_table_dispatch_on_parts` below closes
        the table case; a computed name remains uncovered, and the behavioural
        test cannot cover it either because it cannot guess the name.
        """
        assert set(api.API_ROUTES) == set(self.ROUTES), (
            f"router dispatches {sorted(api.API_ROUTES)} but the ledger says "
            f"{sorted(self.ROUTES)} — add it to ROUTES on purpose, or remove it"
        )

    @pytest.mark.parametrize(
        "path",
        [
            f"/api/v1/recall/{SCOPE}/extra",   # ledgered head, too many parts
            "/api/v1/recall",                  # ledgered head, too few
            "/api/v1/snapshot/anything/at/all",
            "/api/v1/search",
        ],
    )
    def test_a_ledgered_head_with_the_WRONG_arity_404s(self, store: Path, path: str):
        """🔴 The dispatcher's one numeric field had NO test.

        `if len(parts) == arity` mutated to `>=` SURVIVED all 318 tests, and
        that mutant serves `200 recalled` for `/recall/<scope>/extra` and
        `200 snapshot` for `/snapshot/anything/at/all`. The existing
        `test_anything_outside_the_ledger_404s` only probes heads OUTSIDE the
        table, so a ledgered head with the wrong component count was unreachable
        by every guard in this file. Arity is the table's other half.
        """
        with running(store) as (base, _):
            code, headers, _b = fetch(f"{base}{path}", token=GOOD_TOKEN)
        assert code == 404, f"{path} answered {code}"
        assert headers["X-Store-Status"] == "no-route"

    def test_every_ledgered_route_actually_dispatches(self):
        """Structural companion: the table's handlers must EXIST and be bound.

        A table is only as good as its rows — a typo'd handler name would make a
        ledgered route 500 rather than serve, and the equality check above
        cannot see that.
        """
        for name, (handler, arity) in api.API_ROUTES.items():
            assert hasattr(api.StoreRequestHandler, handler), f"{name} -> missing {handler}"
            assert arity >= 1, f"{name} has arity {arity}"

    def test_anything_outside_the_ledger_404s(self, store: Path):
        """Behavioural companion to the structural ledger above.

        The ledger reads the source; this proves the server actually refuses.
        Both are needed: a structural check type-checks past a router that
        accepts a name it never dispatches, and a behavioural sample cannot see
        a route it did not think to name.
        """
        with running(store) as (base, _):
            for path in (
                "/api/v1/entry/x/y/bullets",
                "/api/v1/scopes",
                "/api/v1/sync",
                "/api/v1/",
            ):
                code, headers, _b = fetch(f"{base}{path}", token=GOOD_TOKEN)
                assert code == 404, f"{path} answered {code}"
                assert headers["X-Store-Status"] == "no-route"

    def test_the_router_reads_the_table_rather_than_its_own_spelling(self):
        """🔴 WHY THE SOURCE-PARSING LEDGER IS GONE, recorded so nobody rebuilds it.

        Three versions of this guard read the router as TEXT and each was
        defeated by a re-spelling while the whole suite stayed green:

          v1  four hardcoded non-route probes  -> missed `/snapshot` entirely
          v2  regex `parts\\[0\\] == "([a-z0-9-]+)"` -> missed `"raw_dump"`
              (underscore); its own positive control fed it the one spelling it
              caught, so it could not reveal that
          v3  AST walk over comparisons against `parts` -> missed
              `head = parts[0]; head == "x"` and `parts[0] in NAME`, both one
              ordinary refactor away, and it was file-scoped so a rewrite of an
              unrelated `parts` local (server.py has two) would have produced a
              FALSE failure naming a header value as a route

        v3 and its companion `test_no_table_dispatch_on_parts` are BOTH GONE —
        this docstring described them for a round after they were deleted, which
        is the same "reads as coverage while providing none" failure the guard
        itself exists to prevent. What runs now is the assertion below.

        Each fix made the pattern-matching cleverer, which is the wrong axis.
        The route set is now DATA the dispatcher reads (`API_ROUTES`), so
        "what does the router accept" is answered by reading the router's own
        table instead of guessing how it was written. There is no spelling left
        to miss, and no source text to parse.
        """
        src = SERVER_PATH.read_text()
        assert "API_ROUTES.get(parts[0])" in src, (
            "the dispatcher no longer reads API_ROUTES — the ledger test above "
            "would then be asserting against a table nothing uses"
        )

    def test_the_snapshot_route_added_NO_write_verb(self, store: Path):
        """Phase 2 adds a READ route. The write guard above must be untouched by
        it — stated as its own test so "phase 2 stayed read-only" is a checked
        claim rather than a sentence in a commit message."""
        with running(store) as (base, _):
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                code, headers, body = fetch(
                    f"{base}/api/v1/snapshot", token=GOOD_TOKEN, method=method
                )
                assert code == 405, f"{method} answered {code}"
                assert headers["Allow"] == "GET, HEAD"
                assert body == b"read-only\n"

    def test_the_image_copies_every_module_it_needs(self):
        """🔴 THE DOCKERFILE ENUMERATES ITS `COPY`s, AND THE LIST ROTTED SILENTLY.

        `subsystem_touch` gained `from git_mainline import …` in #677
        (2026-08-21). The Dockerfile's hand-written list was not updated, so
        every image built after that commit contained code that could not
        import — while the RUNNING pod stayed healthy, because its image
        predates the change. The defect was therefore invisible from production
        and invisible from CI, and surfaced only when somebody next rebuilt:
        `ModuleNotFoundError: No module named 'git_mainline'`, caught by
        `build-push.sh`'s own import control at deploy time.

        This computes the TRANSITIVE closure of local `scripts/lib` imports from
        the entrypoints and asserts the Dockerfile covers it, so the next added
        import fails here — in CI, on the commit that adds it — rather than at
        the next deploy, which may be months later and someone else's problem.
        """
        lib = ROOT / "scripts" / "lib"
        dockerfile = (API_DIR / "Dockerfile").read_text()

        def local_imports(path: Path) -> set[str]:
            found = set()
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    found.add(node.module.split(".")[0])
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        found.add(alias.name.split(".")[0])
            return {m for m in found if (lib / f"{m}.py").exists()}

        # Entrypoints: what the server imports directly.
        needed, queue = set(), ["subsystem_recall"]
        while queue:
            mod = queue.pop()
            if mod in needed:
                continue
            needed.add(mod)
            queue.extend(local_imports(lib / f"{mod}.py"))

        # 🔴 TWO LISTS, AND CHECKING ONLY ONE IS A GUARD NARROWER THAN THE
        # HAZARD. The first version of this test checked only the COPY lines —
        # and would have passed while the build still failed, because
        # `Dockerfile.dockerignore` is an ALLOWLIST (`**` then explicit `!`
        # unignores) and an un-listed file never reaches the build context at
        # all. Measured: with the COPY added but the ignore-file untouched,
        # `docker build` fails with `"/scripts/lib/git_mainline.py": not found`.
        # Both lists must cover the closure, so both are asserted.
        ignorefile = (API_DIR / "Dockerfile.dockerignore").read_text()
        copied = set(re.findall(r"COPY scripts/lib/(\w+)\.py", dockerfile))
        unignored = set(re.findall(r"!scripts/lib/(\w+)\.py", ignorefile))

        assert not (needed - copied), (
            f"Dockerfile does not COPY {sorted(needed - copied)} — the image "
            f"would build and then fail to import at runtime."
        )
        assert not (needed - unignored), (
            f"Dockerfile.dockerignore does not un-ignore "
            f"{sorted(needed - unignored)} — it is an allowlist, so the file "
            f"never reaches the build context and COPY fails outright."
        )

    def test_nothing_in_this_directory_writes_to_the_store(self):
        for path in sorted(API_DIR.iterdir()):
            if not path.is_file():
                continue
            text = path.read_text()
            assert "git push" not in text, f"{path.name} reaches for git push"
            assert "remote add" not in text, f"{path.name} configures a git remote"


# =============================================================================
# 14. PHASE 1.5 — the (B-required) hardening.
#
# 🔴 WHAT THESE ARE, HONESTLY. `server.py` EXISTS at the base ref, so unlike
# every section above, some of these are real regressions: a request with no
# `CF-Connecting-IP` and a valid token is served 200 at base and 401 here, and a
# valid token after five failures is served 200 at base and refused here. Those
# two are red at base for the RIGHT reason. The token-SET tests are NOT: they
# call `build_server(tokens=…)` / `load_tokens`, which do not exist at base, so
# their red is an API error and proves nothing. The PR body reports which is
# which, and the mutation matrix is the evidence for the second group.
# =============================================================================


SECOND_TOKEN = "q" * 20 + "R" * 20 + "s" * 8  # 48 chars, disjoint from GOOD_TOKEN
THIRD_TOKEN = "m" * 20 + "N" * 20 + "o" * 8


class TestTokenSetAndOverlapRotation:
    """§2b: "Token rotation must be a one-command operation and must be
    exercised once before cutover." Overlap is what makes it one command: the
    server accepts current AND previous, so no client is ever broken.
    """

    def _write(self, tmp_path: Path, *tokens: str) -> str:
        path = tmp_path / "tokens"
        path.write_text("\n".join(tokens) + "\n")
        return str(path)

    def test_two_tokens_load_as_a_set_IN_FILE_ORDER(self, tmp_path: Path):
        path = self._write(tmp_path, GOOD_TOKEN, SECOND_TOKEN)
        assert [r[0] for r in loaded(path, {})] == [GOOD_TOKEN, SECOND_TOKEN]

    def test_a_duplicated_line_collapses_and_order_is_kept(self, tmp_path: Path):
        path = self._write(tmp_path, SECOND_TOKEN, GOOD_TOKEN, SECOND_TOKEN)
        assert [r[0] for r in loaded(path, {})] == [SECOND_TOKEN, GOOD_TOKEN]

    def test_TWO_LEGACY_ROWS_do_NOT_trip_the_duplicate_identity_guard(
        self, tmp_path: Path
    ):
        """🔴 THE EXEMPTION, EXERCISED — it is what keeps rotation working.

        Both bare rows carry identity `legacy`, so a duplicate-identity check
        written without the exemption refuses the ordinary current+previous
        overlap file: the very shape guards 1-5 exist to support, and the shape
        criterion 10's rollback restores. Distinct tokens, one identity, and it
        must LOAD.
        """
        path = self._write(tmp_path, GOOD_TOKEN, SECOND_TOKEN)
        rows = loaded(path, {})
        assert [r[1] for r in rows] == ["legacy", "legacy"]
        assert [r[2] for r in rows] == [None, None]

    def test_the_cap_is_FOUR(self):
        # Literal, not `api.MAX_TOKENS` — importing it would assert x == x.
        assert api.MAX_TOKENS == 4

    def test_a_FIFTH_token_is_refused_at_startup(self, tmp_path: Path):
        # Every earlier guard passes: the file exists, reads, is non-empty, and
        # every one of the five tokens clears the length floor. Only the cap can
        # reject this input.
        five = [chr(ord("a") + i) * 48 for i in range(5)]
        path = self._write(tmp_path, *five)
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {})
        assert "too many tokens" in str(exc.value)
        assert "5" in str(exc.value)

    def test_FOUR_tokens_are_accepted_the_boundary_is_not_off_by_one(
        self, tmp_path: Path
    ):
        four = [chr(ord("a") + i) * 48 for i in range(4)]
        path = self._write(tmp_path, *four)
        assert [r[0] for r in loaded(path, {})] == four

    def test_the_CAP_counts_CREDENTIALS_not_ROWS(self, tmp_path: Path):
        """🔴 A REGRESSION THIS BRANCH INTRODUCED, MEASURED. Removing the
        pre-parse dedup left guard 4 counting physical rows, so four distinct
        tokens plus ONE verbatim duplicate line answered `too many tokens: 5,
        max 4` — for a file holding four credentials that loaded fine before.

        It also contradicted guard 11, whose own comment calls a duplicated row
        "the rotation shape, and it is legitimate": the file guard 11 accepts is
        the file guard 4 was refusing.

        FIVE rows, FOUR credentials. It must load, and the collapse must leave
        exactly the four.
        """
        four = [chr(ord("a") + i) * 48 for i in range(4)]
        path = self._write(tmp_path, *four, four[0])
        assert [r[0] for r in loaded(path, {})] == four

    def test_FIVE_COPIES_of_ONE_token_is_ONE_credential(self, tmp_path: Path):
        """The far end of the same claim, and the one that made the old wording
        plainly wrong: five identical lines are one credential, and "too many
        tokens: 5" was a sentence about a file with one token in it.
        """
        path = self._write(tmp_path, *([GOOD_TOKEN] * 5))
        assert [r[0] for r in loaded(path, {})] == [GOOD_TOKEN]

    def test_the_CAP_still_counts_the_DISTINCT_ones_and_says_so(
        self, tmp_path: Path
    ):
        """🔴 THE UPPER BOUND: counting credentials must not become counting
        nothing. SEVEN distinct tokens with one of them repeated is still seven
        credentials, and the number in the message is the credential count — not
        the row count (8) it would have been, and not a constant.

        🔴 SEVEN, NOT FIVE, AND THAT IS THE WHOLE POINT OF THE FIXTURE. This
        test used to build five tokens and assert `too many tokens: 5, max 4` —
        five being one past a cap of four, so the printed number could only ever
        BE five, and the other test that reaches this message uses five too. It
        was a fixture that can only produce the constant's own value:
        `claude/RULES.md` calls that shape out by name, and it was measured — a
        mutant hardcoding `5` in place of the credential count SURVIVED the full
        93-test subset with 0 failures.

        Seven separates every number in the sentence: 7 credentials, 8 rows,
        cap 4. A hardcoded 5, a row count, or the cap itself each print
        something this assertion rejects.
        """
        seven = [chr(ord("a") + i) * 48 for i in range(7)]
        path = self._write(tmp_path, *seven, seven[2])
        message = str(exc_of(lambda: api.load_tokens(path, {})))
        assert "too many tokens: 7, max 4" in message, message

    def test_a_SHORT_SECOND_token_names_its_POSITION_and_never_the_token(
        self, tmp_path: Path
    ):
        # 🔴 The reachable case that a "is the token long enough" check written
        # against `raw.strip()` would wave straight through: the FIRST token is
        # fine, so the file passes every earlier guard.
        path = self._write(tmp_path, GOOD_TOKEN, "hunter2")
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {})
        message = str(exc.value)
        assert "token on line 2 of 2 is too short" in message
        assert "hunter2" not in message, "the secret was echoed into the error"
        assert "43" in message

    def test_BOTH_tokens_in_the_set_authorize_over_HTTP(self, store: Path):
        with running(store, tokens=(GOOD_TOKEN, SECOND_TOKEN)) as (base, _):
            for token in (GOOD_TOKEN, SECOND_TOKEN):
                code, _h, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=token)
                assert code == 200, f"{token[:3]}… was refused"
                assert POINTER_LINE.encode() in body

    def test_a_token_OUTSIDE_the_set_is_still_refused(self, store: Path):
        with running(store, tokens=(GOOD_TOKEN, SECOND_TOKEN)) as (base, _):
            code, _h, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=THIRD_TOKEN)
        assert code == 401
        assert body == b"unauthorized\n"

    def test_the_audit_line_names_WHICH_fingerprint_matched(self, store: Path):
        """🔴 THE ONE THING THAT MAKES OVERLAP ROTATION SAFE.

        Without this, "nobody is using the old token any more" is a guess and
        deleting it is a coin flip. A log that named the SERVER's token instead
        of the MATCHED one would be green on a single-token deployment and
        useless on the only deployment shape that needs it.
        """
        with running(store, tokens=(GOOD_TOKEN, SECOND_TOKEN)) as (base, audit):
            # 🔴 The waits are INTERLEAVED because the read below is POSITIONAL:
            # `await_audit` guarantees a count, never an order. See its docstring.
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
            await_audit(audit, 1)
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=SECOND_TOKEN)
            await_audit(audit, 2)
        lines = settle(audit, 2)
        first, second = api.token_id(GOOD_TOKEN), api.token_id(SECOND_TOKEN)
        assert first != second
        assert f"token={first}" in lines[0]
        assert f"token={second}" in lines[1]
        assert "auth=ok" in lines[0] and "auth=ok" in lines[1]
        # And never the credential itself, on either line.
        joined = "\n".join(lines)
        assert GOOD_TOKEN not in joined and SECOND_TOKEN not in joined

    def test_authorize_compares_against_EVERY_token_with_no_early_exit(
        self, monkeypatch
    ):
        """A `break` on the first match would make "which token did you use"
        measurable from outside — during an overlap window that is exactly the
        fact an attacker wants. The FIRST token matches here, so a short-circuit
        would show up as one call instead of three.
        """
        seen: list[tuple] = []
        real = api.hmac.compare_digest

        def spy(a, b):
            seen.append((a, b))
            return real(a, b)

        monkeypatch.setattr(api.hmac, "compare_digest", spy)
        got = api.authorize(
            f"Bearer {GOOD_TOKEN}", (GOOD_TOKEN, SECOND_TOKEN, THIRD_TOKEN)
        )
        # 🔴 The RECORD, and the fingerprint is read off it — `authorize`
        # returns identity and allowlist alongside the match now, so the audit
        # line and the scope filter come from one decision.
        assert got.fingerprint == api.token_id(GOOD_TOKEN)
        assert got.token == GOOD_TOKEN
        assert len(seen) == 3, f"short-circuited after {len(seen)} comparisons"

    def test_authorize_REFUSES_a_bare_string_rather_than_iterating_CHARACTERS(self):
        """🔴 `for token in "abc…"` yields "a", "b", "c". Without this guard, a
        caller who passed one token as a `str` would authorize anybody who
        presented a SINGLE CHARACTER of it — a total auth bypass that no
        functional test with a correct caller would ever surface.
        """
        with pytest.raises(TypeError) as exc:
            api.authorize(f"Bearer {GOOD_TOKEN}", GOOD_TOKEN)
        assert "SEQUENCE" in str(exc.value)
        # And the hazard it describes is real: one character is not the token.
        with pytest.raises(api._Rejected):
            api.authorize(f"Bearer {GOOD_TOKEN[0]}", (GOOD_TOKEN,))

    def test_build_server_refuses_a_bare_string_too(self, store: Path):
        with pytest.raises(TypeError) as exc:
            api.build_server(
                host="127.0.0.1",
                port=0,
                store_root=str(store),
                tokens=GOOD_TOKEN,
                trusted_proxies=(LOOPBACK_PROXY,),
            )
        # 🔴 The MESSAGE, not just the type. At the base ref this call raises
        # `TypeError: unexpected keyword argument 'tokens'` — so a bare
        # `pytest.raises(TypeError)` is GREEN AT BASE for a completely different
        # reason, which is a vacuous guard. Pinning the sentence makes the test
        # a statement about the guard rather than about the signature.
        assert "SEQUENCE" in str(exc.value)

    def test_a_ROTATION_end_to_end_old_still_works_then_stops(self, store: Path):
        """The proposal's pre-cutover requirement, in-band: add the new token,
        watch BOTH work and the fingerprints diverge, then remove the old one
        and watch it be REFUSED. A rotation path never run is not a rotation
        path — and the last step is the one that is usually skipped.
        """
        # Step 1: only the old token exists.
        with running(store, tokens=(GOOD_TOKEN,)) as (base, audit):
            assert fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)[0] == 200
            assert fetch(f"{base}/api/v1/recall/{SCOPE}", token=SECOND_TOKEN)[0] == 401
        # Step 2: OVERLAP — both accepted, and the log tells them apart.
        with running(store, tokens=(SECOND_TOKEN, GOOD_TOKEN)) as (base, audit):
            # 🔴 The waits are INTERLEAVED because the read below is POSITIONAL:
            # `await_audit` guarantees a count, never an order. See its docstring.
            assert fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)[0] == 200
            await_audit(audit, 1)
            assert fetch(f"{base}/api/v1/recall/{SCOPE}", token=SECOND_TOKEN)[0] == 200
            lines = await_audit(audit, 2)
        assert f"token={api.token_id(GOOD_TOKEN)}" in lines[0]
        assert f"token={api.token_id(SECOND_TOKEN)}" in lines[1]
        # Step 3: the old token is REMOVED. 🔴 This is the assertion that makes
        # the whole exercise mean something.
        with running(store, tokens=(SECOND_TOKEN,)) as (base, _):
            assert fetch(f"{base}/api/v1/recall/{SCOPE}", token=SECOND_TOKEN)[0] == 200
            code, _h, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert code == 401
        assert body == b"unauthorized\n"

    def test_the_startup_banner_prints_FINGERPRINTS_never_tokens(
        self, tmp_path: Path, store: Path, capsys, monkeypatch
    ):
        path = self._write(tmp_path, GOOD_TOKEN, SECOND_TOKEN)
        started: dict = {}

        class _Fake:
            def serve_forever(self_inner):
                raise KeyboardInterrupt

            def server_close(self_inner):
                pass

        def fake_build(**kwargs):
            started.update(kwargs)
            return _Fake()

        monkeypatch.setattr(api, "build_server", fake_build)
        monkeypatch.setenv("SUBSYSTEM_STORE_TRUSTED_PROXIES", LOOPBACK_PROXY)
        rc = api.main(["--store", str(store), "--port", "0", "--token-file", path])
        assert rc == 0
        out = capsys.readouterr().out
        assert api.token_id(GOOD_TOKEN) in out
        assert api.token_id(SECOND_TOKEN) in out
        assert GOOD_TOKEN not in out and SECOND_TOKEN not in out
        assert [r.token for r in started["tokens"]] == [GOOD_TOKEN, SECOND_TOKEN]


class TestClientIpIsCloudflareOnly:
    """§0.2: `/api/*` has no edge auth, so the app is the only place a client can
    be identified — and it can only be identified correctly.
    """

    def test_the_audit_line_carries_the_CF_Connecting_IP(self, store: Path):
        with running(store) as (base, audit):
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=CLIENT_IP)
            line = await_audit(audit, 1)[0]
        assert f"ip={CLIENT_IP}" in line

    def test_a_spoofed_X_Forwarded_For_does_NOT_win(self, store: Path):
        """🔴 Both headers present, DIFFERENT values. The CF one must be the one
        that is recorded and keyed on; the forged one must not appear anywhere.
        """
        with running(store) as (base, audit):
            code, _h, _b = fetch(
                f"{base}/api/v1/recall/{SCOPE}",
                token=GOOD_TOKEN,
                client_ip=CLIENT_IP,
                extra_headers={"X-Forwarded-For": SPOOF_IP},
            )
            line = await_audit(audit, 1)[0]
        assert code == 200
        assert f"ip={CLIENT_IP}" in line
        assert SPOOF_IP not in line, "a caller-supplied address was trusted"

    def test_X_Forwarded_For_ALONE_fails_CLOSED(self, store: Path):
        """The header an attacker controls cannot substitute for the one
        Cloudflare overwrites — not even as a fallback.
        """
        with running(store) as (base, audit):
            code, _h, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}",
                token=GOOD_TOKEN,
                client_ip=None,
                extra_headers={"X-Forwarded-For": SPOOF_IP},
            )
            line = await_audit(audit, 1)[0]
        assert code == 401
        assert body == b"unauthorized\n"
        assert "status=no-client-ip" in line
        assert SPOOF_IP not in line

    def test_a_MISSING_CF_Connecting_IP_fails_closed_even_with_a_VALID_token(
        self, store: Path
    ):
        with running(store) as (base, audit):
            code, _h, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=None
            )
            line = await_audit(audit, 1)[0]
        assert code == 401
        assert body == b"unauthorized\n"
        assert "auth=fail" in line and "ip=-" in line

    def test_a_MANGLED_CF_Connecting_IP_fails_closed(self, store: Path):
        for value in ("not-an-ip", "", "203.0.113.7, 198.51.100.4", "999.1.1.1"):
            with running(store) as (base, _):
                code, _h, _b = fetch(
                    f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=value
                )
            assert code == 401, f"{value!r} was accepted as a client address"

    def test_TWO_CF_Connecting_IP_headers_fail_closed(self, store: Path):
        """A proxy that APPENDS rather than overwrites would let a caller smuggle
        a second value past it. Refuse rather than pick one.
        """
        # 🔴 `urllib`'s `add_header` OVERWRITES, so it cannot express this at
        # all — a test written with it would send ONE header and pass with the
        # guard deleted. Raw `putheader` twice is the only way to put two on the
        # wire, and the control below proves the shape reaches the server.
        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            two = _raw_request(
                host,
                f"/api/v1/recall/{SCOPE}",
                [
                    ("Authorization", f"Bearer {GOOD_TOKEN}"),
                    ("CF-Connecting-IP", CLIENT_IP),
                    ("CF-Connecting-IP", SPOOF_IP),
                ],
            )
            # POSITIVE CONTROL on the harness: the same call shape with ONE
            # header must be served, or the 401 above would prove only that
            # `_raw_request` is broken.
            one = _raw_request(
                host,
                f"/api/v1/recall/{SCOPE}",
                [
                    ("Authorization", f"Bearer {GOOD_TOKEN}"),
                    ("CF-Connecting-IP", CLIENT_IP),
                ],
            )
        assert one == 200, "the raw-request harness cannot reach a 200 at all"
        assert two == 401

    def test_unidentified_requests_are_NOT_bucketed_together(self, store: Path):
        """🔴 THE HAZARD THE FAIL-CLOSED EXISTS TO AVOID. Bucketing every
        unidentified caller under one shared key means one abuser locks out
        everybody. Twenty rejected no-IP requests — four times the failure
        budget — must leave an identified client completely unaffected.
        """
        with running(store) as (base, audit):
            for _ in range(20):
                assert fetch(f"{base}/api/v1/recall/{SCOPE}", client_ip=None)[0] == 401
            code, _h, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=CLIENT_IP
            )
            # 🔴 WAIT FOR ALL TWENTY-ONE LINES. `fetch` returning means the
            # RESPONSE was written; `_audit()` runs after it, on a handler
            # thread. See `drain_output` — the hazard is the server's, not the
            # subprocess pipe's, and this site is in-process.
            await_audit(audit, 21)
        assert code == 200, "an unidentifiable caller locked out an identified one"
        assert POINTER_LINE.encode() in body
        lines = settle(audit, 21)
        # 🔴 THE ASSERTION THAT MAKES THIS TEST MEAN ANYTHING, and it was missing.
        # An audit found this test VACUOUS against the very hazard it names:
        # under the mutant `ip = "unknown"` (bucket every unidentified caller
        # under one shared key) the flood locks out `"unknown"` while the final
        # request above uses a DIFFERENT key — so it stayed green. What actually
        # distinguishes fail-closed from a shared bucket is that the twentieth
        # unidentified request is STILL `no-client-ip` and never `locked-out`:
        # nothing was counted, because there was no bucket to count into.
        #
        # 🔴 SELECTED BY IDENTITY, NEVER BY POSITION. This used to slice
        # `audit[:20]`, which assumes the twenty rejections were APPENDED before
        # the twenty-first request's line. They are not ordered: twenty-one
        # handler threads each write their response and then race to append, so
        # the `status=recalled` record can land anywhere in the list. Measured on
        # an unmodified tree under CPU load: 2/50 red; with the handler's
        # `_audit` delayed for `no-client-ip` only, 1/1 — `{'no-client-ip',
        # 'recalled'}`. A row is not yours because it is FIRST.
        unidentified = [ln for ln in lines if " ip=- " in ln]
        assert len(unidentified) == 20, (
            f"expected twenty unidentified records, got {len(unidentified)} — "
            f"an unidentified caller was given an identity:\n" + "\n".join(lines))
        statuses = {line.split("status=")[1].split()[0] for line in unidentified}
        assert statuses == {"no-client-ip"}, statuses
        assert not any("locked-out" in line for line in lines)
        assert not any("lockout-triggered" in line for line in lines)

    def test_the_address_is_NORMALISED_so_one_caller_is_one_bucket(self):
        assert api.client_ip({"CF-Connecting-IP": "::FFFF:203.0.113.7"}) == api.client_ip(
            {"CF-Connecting-IP": "::ffff:203.0.113.7"}
        )
        assert api.client_ip({"CF-Connecting-IP": " 203.0.113.7 "}) == "203.0.113.7"
        assert api.client_ip({}) is None
        assert api.client_ip({"CF-Connecting-IP": "nope"}) is None

    def test_health_needs_NO_client_ip_because_the_kubelet_sends_none(
        self, store: Path
    ):
        with running(store) as (base, audit):
            code, _h, body = fetch(f"{base}/healthz", client_ip=None)
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
            await_audit(audit, 1)
        assert (code, body) == (200, b"ok\n")
        # 🔴 A REASSURING ZERO NEEDS A POSITIVE CONTROL. `assert audit == []`
        # read the live list with nothing to wait for, so it was equally happy
        # with "the probe is not audited" and with "the sink had not appended
        # yet" — and it would have stayed green with `_audit` wired to nothing
        # at all. An audited request is issued after the probe; waiting for ITS
        # line proves the sink works, and the count then says the probe added
        # none. 🔴 THE COUNT IS `settle`'s, NOT THE SNAPSHOT'S — this comment
        # used to close with "(Residual: a probe line arriving after this
        # snapshot is still unobserved)", and that residual is now closed for
        # anything landing inside `SETTLE_GRACE_S` of teardown. Still bounded by
        # that window: a sink with no EOF admits no stronger claim.
        lines = settle(audit, 1)
        assert "/healthz" not in lines[0], lines[0]

    def test_the_source_never_reads_X_Forwarded_For(self):
        """Secondary, not the guard — `test_a_spoofed_X_Forwarded_For_does_NOT_win`
        is. A behavioural test alone would stay green if XFF were consulted only
        when `CF-Connecting-IP` is absent, which this catches directly.

        🔴 It reads CODE, not text. Comments and docstrings in `server.py`
        discuss `X-Forwarded-For` at length — that is the documentation of why
        it is refused — so a substring scan over the file would be red for the
        wrong reason and get "fixed" by deleting the explanation. Tokenising and
        dropping COMMENT/STRING tokens leaves only what actually executes.
        """
        code = _executable_tokens(SERVER_PATH)
        assert "X-Forwarded-For" not in code
        assert "x-forwarded-for" not in code.lower()
        # POSITIVE CONTROL: the tokeniser CAN see a header string in real code —
        # otherwise the assertion above is a fact about the tokeniser.
        assert "CF-Connecting-IP" in code


class TestRateLimiterUnit:
    """Injected clock, so the WINDOW and the LOCKOUT are both watched to expire.

    A limiter tested only in the "locks out" direction is half a guard: one that
    never released would take the whole store down on a typo.
    """

    def _limiter(self, now: list[float], **kwargs):
        return api.RateLimiter(clock=lambda: now[0], **kwargs)

    def test_the_defaults_are_5_per_60s_then_900s(self):
        # Literals (§: 5 failures / minute -> 15-minute lockout), never imported.
        assert api.DEFAULT_MAX_FAILURES == 5
        assert api.DEFAULT_FAILURE_WINDOW_S == 60.0
        assert api.DEFAULT_LOCKOUT_S == 900.0

    def test_four_failures_do_NOT_lock_and_the_fifth_DOES(self):
        now = [1000.0]
        lim = self._limiter(now)
        for i in range(4):
            assert lim.record_failure("a") is False, f"locked after {i + 1}"
            assert lim.locked_out("a") is False
        assert lim.record_failure("a") is True
        assert lim.locked_out("a") is True

    def test_failures_OUTSIDE_the_window_do_not_accumulate(self):
        now = [1000.0]
        lim = self._limiter(now)
        for _ in range(4):
            assert lim.record_failure("a") is False
        now[0] += 61.0  # the four have aged out
        for _ in range(4):
            assert lim.record_failure("a") is False
        assert lim.locked_out("a") is False

    def test_the_lockout_EXPIRES(self):
        now = [1000.0]
        lim = self._limiter(now)
        for _ in range(5):
            lim.record_failure("a")
        assert lim.locked_out("a") is True
        now[0] += 899.0
        assert lim.locked_out("a") is True, "released early"
        now[0] += 2.0
        assert lim.locked_out("a") is False

    def test_a_lockout_is_PER_KEY_not_global(self):
        now = [1000.0]
        lim = self._limiter(now)
        for _ in range(5):
            lim.record_failure("a")
        assert lim.locked_out("a") is True
        assert lim.locked_out("b") is False

    def test_a_success_does_NOT_forgive_the_streak(self):
        """🔴 INVERTED BY AN AUDIT FINDING, and the old behaviour was mine, not
        the spec's. Forgiving a streak on success created two attacks, both
        because the key is an ADDRESS and not an identity: an attacker holding
        ANY accepted token — including the old one overlap rotation keeps live —
        interleaves one success per four guesses and brute-forces forever; and
        an attacker behind the same NAT as a legitimate client is never locked
        out, because the victim's own traffic keeps resetting them.
        """
        now = [1000.0]
        lim = self._limiter(now)
        for _ in range(4):
            lim.record_failure("a")
        lim.record_success("a")
        assert lim.record_failure("a") is True, "a success forgave the streak"
        assert lim.locked_out("a") is True

    def test_the_WINDOW_is_what_forgives_a_streak(self):
        """The forgiveness the inverted test above was reaching for, done by the
        mechanism that cannot be driven by an attacker: four typos age out.
        """
        now = [1000.0]
        lim = self._limiter(now)
        for _ in range(4):
            assert lim.record_failure("a") is False
        now[0] += 61.0
        for _ in range(4):
            assert lim.record_failure("a") is False
        assert lim.locked_out("a") is False

    def test_a_success_does_NOT_clear_a_LIVE_lockout(self):
        now = [1000.0]
        lim = self._limiter(now)
        for _ in range(5):
            lim.record_failure("a")
        lim.record_success("a")
        assert lim.locked_out("a") is True

    def test_eviction_NEVER_releases_a_live_lockout(self):
        """🔴 The failure table is bounded, and a bound is a deletion policy —
        so the question is what it is allowed to delete. Flooding it with more
        distinct keys than it will hold must not buy an attacker their way out
        of a lockout they already earned. Reachable: `MAX_TRACKED_CLIENTS` + 1
        distinct keys, each with one failure, is exactly one eviction pass.
        """
        now = [1000.0]
        lim = self._limiter(now)
        for _ in range(5):
            lim.record_failure("victim")
        assert lim.locked_out("victim") is True
        # Age every subsequent failure past the window so they are all evictable
        # — the eviction path only ever considers stale entries.
        for i in range(api.MAX_TRACKED_CLIENTS + 1):
            now[0] += 0.001
            lim.record_failure(f"flood-{i}")
        now[0] += 61.0
        lim.record_failure("one-more")
        assert lim.locked_out("victim") is True, "a flood released a live lockout"

    def test_the_thresholds_are_tunable(self):
        now = [1000.0]
        lim = self._limiter(now, max_failures=2, window_s=10.0, lockout_s=30.0)
        assert lim.record_failure("a") is False
        assert lim.record_failure("a") is True
        now[0] += 31.0
        assert lim.locked_out("a") is False


class TestLimiterSettings:
    def test_an_empty_env_yields_the_code_defaults(self):
        assert api.limiter_settings({}) == (5, 60.0, 900.0)

    def test_env_overrides_all_three(self):
        env = {
            "SUBSYSTEM_STORE_MAX_FAILURES": "9",
            "SUBSYSTEM_STORE_FAILURE_WINDOW_S": "30",
            "SUBSYSTEM_STORE_LOCKOUT_S": "120",
        }
        assert api.limiter_settings(env) == (9, 30.0, 120.0)

    def test_a_TYPO_raises_rather_than_silently_defaulting(self):
        with pytest.raises(ValueError) as exc:
            api.limiter_settings({"SUBSYSTEM_STORE_MAX_FAILURES": "fve"})
        assert "SUBSYSTEM_STORE_MAX_FAILURES" in str(exc.value)

    def test_a_NON_POSITIVE_value_raises(self):
        # Reachable past the parse guard: "0" parses fine and would disable the
        # limiter — or lock everyone out on request one, depending on the
        # comparison. Neither is a setting anybody meant.
        with pytest.raises(ValueError) as exc:
            api.limiter_settings({"SUBSYSTEM_STORE_LOCKOUT_S": "0"})
        assert "positive" in str(exc.value)

    def test_main_EXITS_78_on_a_bad_limiter_setting(
        self, store: Path, tmp_path: Path, monkeypatch, capsys
    ):
        """🔴 `build_server` IS STUBBED TO RAISE, AND THAT IS THE POINT, NOT
        TIDINESS. Found by the mutation sweep: with the parse guard broken to
        `return default`, `main` sails past the check and reaches
        `serve_forever()` — so this test does not FAIL, it HANGS, forever, and
        every test after it in the run is silently truncated (claude/RULES.md:
        "a known-red slow test eats the suite budget"). A guard whose mutant
        hangs the suite is worse than one with no test at all, because the
        symptom reads as infrastructure. The stub turns that hang into an
        immediate, named failure.
        """

        def _must_not_be_reached(**kwargs):
            raise AssertionError(
                "main() reached build_server on a bad SUBSYSTEM_STORE_MAX_FAILURES "
                "— the limiter setting was accepted instead of exiting 78"
            )

        monkeypatch.setattr(api, "build_server", _must_not_be_reached)
        path = tmp_path / "tok"
        path.write_text(GOOD_TOKEN)
        monkeypatch.setenv("SUBSYSTEM_STORE_MAX_FAILURES", "lots")
        # Set, so the failure under test is the LIMITER setting and not the
        # trusted-proxy one — two guards reaching one rc are indistinguishable.
        monkeypatch.setenv("SUBSYSTEM_STORE_TRUSTED_PROXIES", LOOPBACK_PROXY)
        rc = api.main(["--store", str(store), "--port", "0", "--token-file", str(path)])
        assert rc == 78
        assert "SUBSYSTEM_STORE_MAX_FAILURES" in capsys.readouterr().err


class TestLockoutOverHTTP:
    """The limiter wired into the router — the layer that knows an auth FAILED.

    🔴 A genuine regression against the base ref: at base, a valid token after
    five wrong ones is served a 200.
    """

    def test_five_failures_lock_out_a_VALID_token_from_the_same_client(
        self, store: Path
    ):
        with running(store) as (base, audit):
            # 🔴 The waits are INTERLEAVED because the read below is POSITIONAL:
            # `await_audit` guarantees a count, never an order. See its docstring.
            for k in range(5):
                assert fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)[0] == 401
                await_audit(audit, k + 1)
            code, _h, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
            lines = await_audit(audit, 6)
        assert code == 401, "a locked-out client was served with a valid token"
        assert body == b"unauthorized\n"
        assert "status=lockout-triggered" in lines[4]
        assert "status=locked-out" in lines[5]

    def test_FOUR_failures_do_not_lock_out_the_boundary_is_not_off_by_one(
        self, store: Path
    ):
        with running(store) as (base, _):
            for _ in range(4):
                assert fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)[0] == 401
            code, _h, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert code == 200
        assert POINTER_LINE.encode() in body

    def test_the_lockout_is_PER_CLIENT_not_a_global_kill_switch(self, store: Path):
        """🔴 The failure the `CF-Connecting-IP` keying exists to prevent: one
        abuser must not take the store down for everyone else.
        """
        with running(store) as (base, _):
            for _ in range(6):
                fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48, client_ip=CLIENT_IP)
            locked = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=CLIENT_IP
            )
            other = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=OTHER_IP
            )
        assert locked[0] == 401
        assert other[0] == 200, "an unrelated client was caught in someone else's lockout"

    def test_a_LOCKED_OUT_response_is_BYTE_IDENTICAL_to_an_ordinary_401(
        self, store: Path
    ):
        """The log discriminates; the wire must not. An attacker who could see
        the lockout land would know exactly how to pace a stuffing run.
        """
        with running(store) as (base, audit):
            ordinary = fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
            await_audit(audit, 1)
            for k in range(5):
                fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
                await_audit(audit, k + 2)
            locked = fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
            # 🔴 SEVEN REQUESTS, SEVEN LINES, WAITED FOR. `_respond` runs before
            # `_audit` and `ThreadingHTTPServer` uses DAEMON threads, so `fetch`
            # returning — and the `with` block exiting — prove nothing about the
            # last handler having appended yet. Observed failing exactly that
            # way: `audit[-1]` held the PREVIOUS request's `status=unauthorized`.
            # This is the hazard `await_audit`'s own docstring names, and this
            # was one of the few call sites not using it.
            lines = await_audit(audit, 7)
        assert ordinary[0] == locked[0] == 401
        assert ordinary[2] == locked[2]
        assert _comparable(ordinary[1]) == _comparable(locked[1])
        # …and the audit log DOES tell them apart, or the property is vacuous.
        assert "status=unauthorized" in lines[0]
        assert "status=locked-out" in lines[-1]

    def test_a_SUCCESS_does_NOT_buy_more_GUESSES(self, store: Path):
        """🔴 THE INTERLEAVE ATTACK, over HTTP. An attacker holding one accepted
        token — the old one, during an overlap rotation — must not be able to
        spend it to reset the budget and keep guessing the rest of the set.
        Four wrong, one right, one wrong: the sixth request is the fifth FAILURE
        inside the window, so it locks out.
        """
        with running(store, tokens=(GOOD_TOKEN, SECOND_TOKEN)) as (base, audit):
            # 🔴 The waits are INTERLEAVED because the read below is POSITIONAL:
            # `await_audit` guarantees a count, never an order. See its docstring.
            for k in range(4):
                fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
                await_audit(audit, k + 1)
            assert fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)[0] == 200
            await_audit(audit, 5)
            fetch(f"{base}/api/v1/recall/{SCOPE}", token="x" * 48)
            await_audit(audit, 6)
            code, _h, _b = fetch(f"{base}/api/v1/recall/{SCOPE}", token=SECOND_TOKEN)
            lines = await_audit(audit, 7)
        assert code == 401, "a valid token reset the guessing budget"
        assert "status=lockout-triggered" in lines[5]

    def test_a_WRONG_PATH_does_NOT_lock_out_a_client_holding_the_RIGHT_token(
        self, store: Path
    ):
        """🔴 INVERTED BY A DELTA AUDIT, which measured the previous behaviour
        locking out a legitimate client. Counting path probes AND removing
        success-forgiveness combined into: five ordinary wrong paths — one of
        them `/api/v1`, a missing trailing slash from the real prefix — and a
        client holding the correct token was dead for 15 minutes with nothing
        able to forgive it.

        The specification says five failed AUTHS per minute. A request that
        never reaches the token check is not a failed auth. Volumetric probing
        belongs to the Traefik (10/s) and Cloudflare layers.
        """
        with running(store) as (base, audit):
            for path in ("/favicon.ico", "/", "/robots.txt", "/metrics", "/api/v1"):
                assert fetch(f"{base}{path}", token=GOOD_TOKEN)[0] == 401
            code, _h, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
            lines = await_audit(audit, 6)
        assert code == 200, "a valid client locked itself out on wrong paths"
        assert POINTER_LINE.encode() in body
        # 🔴 WAITED FOR, because `not any(...)` over a list that is still filling
        # is satisfied by an EMPTY one — the racy read makes the negative half of
        # this test pass for the wrong reason.
        assert not any("locked-out" in line for line in lines)
        # …and they are still REFUSED and logged, or this would be a hole.
        assert sum("status=unauthorized" in line for line in lines) == 5

    def test_a_WRONG_TOKEN_still_counts_even_on_a_path_that_does_not_exist(
        self, store: Path
    ):
        """The other half: the exemption above is for the PATH check, not for
        auth. An `/api/v1/...` request with a bad token is a failed auth no
        matter how nonsensical the route.
        """
        with running(store) as (base, _):
            for _ in range(5):
                assert fetch(f"{base}/api/v1/nonsense/x", token="w" * 48)[0] == 401
            code, _h, _b = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert code == 401

    def test_the_health_probe_is_never_rate_limited(self, store: Path):
        with running(store) as (base, _):
            for _ in range(10):
                fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
            code, _h, body = fetch(f"{base}/healthz", client_ip=None)
        assert (code, body) == (200, b"ok\n"), "a lockout took the readiness probe down"

    def test_POST_is_STILL_405_and_not_swallowed_by_the_new_ordering(
        self, store: Path
    ):
        """Phase 3 owns writes. The 405 sits ahead of the client-IP and lockout
        checks, so none of the phase-1.5 plumbing can turn a mutation into a
        read — pinned here because that ordering is now load-bearing.
        """
        with running(store) as (base, audit):
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                code, headers, body = fetch(
                    f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, method=method
                )
                assert code == 405, f"{method} answered {code}"
                assert body == b"read-only\n"
                assert headers["Allow"] == "GET, HEAD"
            # `all(...)` over a partially-filled list is vacuously true, so the
            # count is waited for before it is read.
            await_audit(audit, 4)
        lines = settle(audit, 4)
        assert all("status=method-not-allowed" in line for line in lines)


# =============================================================================
# 15. The REAL entrypoint, driven as a SUBPROCESS.
#
# 🔴 THIS SECTION EXISTS TO BE RED AT THE BASE REF FOR THE RIGHT REASON.
# Everything in section 14 that touches the server calls `build_server(tokens=…)`
# or `load_tokens`, neither of which exists at base — so its red is an
# AttributeError, which is a collection error wearing a failure's clothes and
# proves nothing (the same trap phase 1's header calls out).
#
# The COMMAND LINE, by contrast, is unchanged between the two refs:
# `server.py --store --host --port --token-file` parses identically at base. So
# a test that spawns the real process and drives it over a real socket runs on
# BOTH trees, and its failure at base is a statement about BEHAVIOUR:
#
#   * base serves 200 to a valid token with no `CF-Connecting-IP` at all
#   * base serves 200 to a valid token after five wrong ones from one address
#   * base treats a TWO-LINE token file as ONE 97-character token, so neither
#     line authorises anything — an overlap rotation is impossible
#
# It is also the only test here that reads the audit line off the process's
# STDOUT, which is the stream Loki actually ingests. The in-process `audit`
# callback used everywhere above is a different code path.
# =============================================================================


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


#: 🔴 TWO LOOPBACK ADDRESSES, WHICH IS WHAT MAKES THE REGRESSION MEASURABLE.
#: `127.0.0.0/8` is entirely local on Linux, so a client can BIND `127.0.0.2` as
#: its source and reach a server listening on `127.0.0.1` — and the server sees
#: a genuinely different peer. Without that, every request in a hermetic test
#: comes from the same address, "trusted peer" and "untrusted peer" cannot both
#: appear against ONE running process, and the victim's 401 is identical on both
#: trees. That identical 401 is exactly what a first draft of this file asserted,
#: and it passed at the base ref: a vacuous guard on the one defect that matters.
TRUSTED_PEER = "127.0.0.1"
UNTRUSTED_PEER = "127.0.0.2"


def fetch_from(
    source_ip: str,
    base: str,
    path: str,
    *,
    token: str | None = None,
    client_ip: str | None = CLIENT_IP,
) -> int:
    """GET `path` with the TCP source address bound to `source_ip`. Returns the
    status code.

    `urllib` cannot express a source address; `http.client.HTTPConnection` can.
    """
    host, _, port = base.removeprefix("http://").partition(":")
    conn = http.client.HTTPConnection(
        host, int(port), timeout=HANG_TIMEOUT, source_address=(source_ip, 0)
    )
    try:
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if client_ip is not None:
            headers["CF-Connecting-IP"] = client_ip
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


def _child_env(trusted_proxies: str | None) -> dict[str, str]:
    """The spawned server's environment. `None` REMOVES the variable.

    🔴 It pops rather than skipping the set: `os.environ` is inherited, so a
    developer who happens to export `SUBSYSTEM_STORE_TRUSTED_PROXIES` in their
    shell would otherwise make the "unset" test pass for the wrong reason — and
    on the day it mattered it would be the CI runner's environment deciding.
    """
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    env.pop("SUBSYSTEM_STORE_TRUSTED_PROXIES", None)
    if trusted_proxies is not None:
        env["SUBSYSTEM_STORE_TRUSTED_PROXIES"] = trusted_proxies
    return env


@contextmanager
def running_subprocess(
    store_root: Path,
    token_file: Path,
    *,
    trusted_proxies: str | None = LOOPBACK_PROXY,
    host: str = "127.0.0.1",
):
    """Spawn the REAL `server.py` process and wait for it to answer /healthz.

    `trusted_proxies` goes in as `$SUBSYSTEM_STORE_TRUSTED_PROXIES`. It is a
    string, not a list, so a test can pass a deliberately malformed value; pass
    `None` to leave the variable UNSET, which is how the startup refusal is
    exercised. 🔴 The base ref ignores this variable entirely, which is what
    makes the tests below behavioural rather than AttributeErrors.
    """
    import time

    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            str(SERVER_PATH),
            "--store",
            str(store_root),
            "--host",
            host,
            "--port",
            str(port),
            "--token-file",
            str(token_file),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_child_env(trusted_proxies),
    )
    base = f"http://{host}:{port}"
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            if proc.poll() is not None:
                out, err = proc.communicate()
                raise AssertionError(f"server exited {proc.returncode}: {err or out}")
            try:
                # 🔴 DEADLINE-RELATIVE, not a fixed short bound — this is a RETRY
                # probe inside the 20 s budget, not a hang-detector. At the
                # module default one blocked call outlasts the deadline it sits
                # in, so the loop could never retry and the budget was
                # unenforceable. But a fixed `timeout=2` was the wrong correction
                # and audit measured why: under the very saturation this file's
                # header documents (round-trips losing the scheduler for >15 s)
                # EVERY probe would exceed 2 s, so a server that was merely slow
                # would be reported as one that never came up — reintroducing the
                # capacity-read-as-failure this module exists to stop, on the
                # other side. Measured at 4x oversubscription the worst probe was
                # 0.675 s, i.e. the fixed bound had ~3x headroom where the
                # documented bad case needs ~8x.
                #
                # `deadline - now` keeps BOTH properties: the 20 s budget is
                # enforceable because no single call can outlast it, and a slow
                # probe that still answers inside the budget is counted rather
                # than discarded. The 0.25 s floor keeps the last iteration from
                # degenerating into a zero-timeout call that cannot succeed.
                probe_bound = max(0.25, deadline - time.time())
                if fetch(f"{base}/healthz", client_ip=None,
                         timeout=probe_bound)[0] == 200:
                    break
            except Exception:
                time.sleep(0.1)
        else:
            raise AssertionError("server never became healthy")
        yield base, proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()
            proc.wait(timeout=10)


class TestTheDeployedEntrypoint:
    """🔴 RED AT THE BASE REF BEHAVIOURALLY, not by AttributeError."""

    @pytest.fixture
    def token_file(self, tmp_path: Path) -> Path:
        path = tmp_path / "token"
        path.write_text(GOOD_TOKEN + "\n")
        return path

    @pytest.fixture
    def rotating_token_file(self, tmp_path: Path) -> Path:
        """Two tokens, one per line — the overlap-rotation shape."""
        path = tmp_path / "tokens"
        path.write_text(f"{SECOND_TOKEN}\n{GOOD_TOKEN}\n")
        return path

    def test_POSITIVE_CONTROL_the_spawned_process_serves_a_real_digest(
        self, store: Path, token_file: Path
    ):
        """Before any zero or any 401 below is believed: this call shape CAN
        return a 200 with content from a process spawned exactly this way.
        """
        with running_subprocess(store, token_file) as (base, _proc):
            code, headers, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert code == 200
        assert headers["X-Store-Status"] == "recalled"
        assert POINTER_LINE.encode() in body

    def test_a_valid_token_with_NO_client_ip_is_REFUSED(
        self, store: Path, token_file: Path
    ):
        """Base serves this 200. The store would be reachable by anything that
        held the token, from anywhere, with no address recorded against it.
        """
        with running_subprocess(store, token_file) as (base, _proc):
            code, _h, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=None
            )
        assert code == 401
        assert body == b"unauthorized\n"

    def test_five_wrong_tokens_LOCK_OUT_the_right_one(
        self, store: Path, token_file: Path
    ):
        """Base serves the sixth request 200 — an unlimited online guessing
        budget against the one credential protecting the whole store.
        """
        with running_subprocess(store, token_file) as (base, _proc):
            for _ in range(5):
                assert fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)[0] == 401
            code, _h, _b = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert code == 401, "an unlimited guessing budget survived"

    def test_a_TWO_LINE_token_file_authorises_BOTH_lines(
        self, store: Path, rotating_token_file: Path
    ):
        """Base reads the whole file as ONE 97-character token, so NEITHER line
        works and an overlap rotation cannot be performed at all.
        """
        with running_subprocess(store, rotating_token_file) as (base, _proc):
            for token in (GOOD_TOKEN, SECOND_TOKEN):
                code, _h, _b = fetch(f"{base}/api/v1/recall/{SCOPE}", token=token)
                assert code == 200, f"{token[:3]}… was refused during overlap"
            outside = fetch(f"{base}/api/v1/recall/{SCOPE}", token=THIRD_TOKEN)[0]
        assert outside == 401, "the set accepted a token that is not in it"

    def test_the_STDOUT_audit_stream_names_the_matched_fingerprint(
        self, store: Path, rotating_token_file: Path
    ):
        """🔴 The stream Loki ingests, not the in-process callback — and the
        field the `SubsystemStoreAuthFailSpike` rule keys on.

        Base prints the CONFIGURED token's id on every line, so during an
        overlap it cannot tell you which credential a client actually used,
        which is the one fact that makes retiring the old one safe.
        """
        # Drain and WAIT — a returned response does not imply its audit line.
        # See `drain_output`.
        with running_subprocess(store, rotating_token_file) as (base, proc):
            out = drain_output(proc)
            # 🔴 The waits are INTERLEAVED because the read below is POSITIONAL:
            # `await_audit` guarantees a count, never an order. See its docstring.
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
            await_audit(out, 1)
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=SECOND_TOKEN)
            await_audit(out, 2)
            fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
            lines = await_audit(out, 3)

        assert len(lines) == 3, f"expected 3 audit lines, got {len(lines)}: {out.text}"
        assert f"token={api.token_id(GOOD_TOKEN)}" in lines[0]
        assert f"token={api.token_id(SECOND_TOKEN)}" in lines[1]
        assert api.token_id(GOOD_TOKEN) != api.token_id(SECOND_TOKEN)
        # The failure line: no fingerprint, `auth=fail`, and the client address —
        # the three fields the Loki alert selects on.
        assert "auth=fail" in lines[2] and "token=-" in lines[2]
        assert f"ip={CLIENT_IP}" in lines[2]
        assert "result=401" in lines[2]
        # And never a credential, on any line. 🔴 Asserted against the WHOLE
        # stream (`out.text`), not the audit subset — a token leaked on a
        # non-audit line must still fail this. `wait_closed()` first, so a line
        # printed during SHUTDOWN is inside the stream being asserted on rather
        # than still in flight.
        #
        # 🔴 AND ITS ANSWER IS ASSERTED, exactly as in `settle`. Discarded, a
        # timeout here degrades in silence: the stream has NOT reached EOF, the
        # ceiling below is a snapshot again, and it reports "the closed stream
        # holds ..." about a stream that is not closed. This site kept the
        # discarded form after `settle` was fixed because `_eof_barriers`
        # accepted only `ast.Expr`/`ast.Assign` and FLAGGED the assert — the
        # guard structurally required the defect. That arm now exists.
        # 🔴 THE TIMEOUT IS THE SHARED BOUND, AND THE MESSAGE INTERPOLATES IT.
        # This used to hardcode 15 s in both places, on the reasoning that taking
        # `Drained.wait_closed`'s default would couple this sentence to a
        # constant declared 3800 lines away — "retune that default and the
        # message silently starts lying about how long it waited". Retuning the
        # default is exactly what the HANG_TIMEOUT change did, so that argument
        # inverted: the literal became the thing that lies, and this site (plus
        # `settle`'s) silently kept the old 15 s bound the rest of the file had
        # left behind. Interpolating `{HANG_TIMEOUT:g}` satisfies BOTH concerns —
        # one bound, and a message that cannot disagree with it.
        assert out.wait_closed(HANG_TIMEOUT), (
            f"the stream never reached EOF within {HANG_TIMEOUT:g}s, so the "
            f"ceiling below is a snapshot again and the leak check is racing "
            f"lines still in flight — it holds {len(out.audit)} audit record(s) "
            f"so far for 3 requests.\nfull stream:\n{out.text}")
        assert GOOD_TOKEN not in out.text and SECOND_TOKEN not in out.text
        assert "w" * 48 not in out.text
        # 🔴 AND THE CEILING, AFTER THE STREAM IS CLOSED. `lines` above is a
        # SNAPSHOT taken while the process was still running, so `== 3` on it
        # cannot see a FOURTH record emitted later — during shutdown, say. That
        # gap is real: with the server patched to emit one extra audit line at
        # SIGTERM, the pre-helper code failed and the snapshot check passes.
        # Three requests must produce three records, not "at least three".
        #
        # 🔴 AND YES, THESE TWO LINES HAND-ROLL `settle`'s `Drained` BRANCH —
        # THE ONLY `len(...audit...) ==` LEFT OUTSIDE THE HELPER, AND THE ONLY
        # REASON `_eof_barriers` NEEDS AN EXEMPTION AT ALL. "One rule, one
        # place" says convert it, and it is DELIBERATELY not converted, for two
        # reasons that are worth stating rather than leaving as a smell:
        #   (1) `settle(out, 3)` would leave this function with no raw audit
        #       read, which is what the raw-read guard's positive control
        #       (`assert _raw_audit_reads(permitted)`) and its barrier control
        #       are built on — the file's only LIVE specimen of the shape that
        #       guard classifies. Both would have to fall back to synthetic
        #       sources, i.e. the guard would no longer be exercised against any
        #       real code in the tree it polices.
        #   (2) It would move the denominator every ceiling matrix in this PR is
        #       stated against — 19 exact-count items, 18 of them on `settle`'s
        #       wall-clock path — so the conversion is not a rename, it is a new
        #       mutation sweep. It belongs in a change that carries its own
        #       matrix, not in one that inherits this one's.
        # If (1) is ever solved with a synthetic fixture, convert this.
        #
        # 🔴 "NOT CONVERTED" IS NOT "FINE AS-IS", AND THIS PARAGRAPH USED TO
        # READ AS THOUGH IT WERE. Both reasons above are about the CONVERSION to
        # `settle`, and neither has anything to say about whether the copy is
        # CORRECT. It was not: it carried the discarded `out.wait_closed()` and
        # the "the closed stream holds ..." message verbatim — line for line the
        # pre-fix shape that the `settle` commit repaired everywhere else, in
        # the one place a reader is most likely to copy from. The one-word
        # repair is above; it is independent of the conversion, which stays
        # deferred. A duplicated branch has to be kept in step with its
        # original, not merely justified.
        assert len(out.audit) == 3, (
            f"the closed stream holds {len(out.audit)} audit records for 3 "
            f"requests — an extra one was emitted after the snapshot:\n{out.text}")


# =============================================================================
# 16. THE AUDIT ROUND — one test per finding, each red before its fix.
#
# 🔴 EVERY TEST BELOW EXISTS BECAUSE A REVIEW FOUND A REAL DEFECT IN SECTIONS
# 14-15, NOT BECAUSE OF A STYLE NOTE. Two were critical: an unauthenticated
# caller could FORGE audit lines (defeating the exact property the token-set
# design rests on), and an unread request body could be re-parsed as the next
# request on a keep-alive connection (CL.0 smuggling). A fix made in response to
# a review is a code change like any other, so each one is pinned here.
# =============================================================================


class TestAuditLogCannotBeForged:
    """🔴 CRITICAL. `unquote()` turns `%0a` into a REAL newline and the audit
    record is one f-string. An unauthenticated caller could emit a second,
    syntactically perfect `auth=ok` line naming any fingerprint and any address.

    Why that is not cosmetic: the README's rotation procedure says to delete the
    old token once its fingerprint stops appearing in the log. A caller who can
    keep any fingerprint appearing forever can block rotation indefinitely, and
    one who can forge `auth=fail` at will can drown or fabricate the Loki alert.
    """

    FORGED = (
        "store-api%20audit%20ts=2026-01-01T00:00:00+00:00%20ip=198.51.100.4"
        "%20method=GET%20path=/api/v1/recall/x%20token=deadbeef1234%20auth=ok"
        "%20result=200%20status=recalled"
    )

    def test_a_NEWLINE_in_the_path_cannot_open_a_second_record(self, store: Path):
        with running(store) as (base, audit):
            code, _h, _b = fetch(f"{base}/api/v1/x%0a{self.FORGED}")
            await_audit(audit, 1)
        assert code == 401
        # ONE request, ONE record — the property nothing asserted before.
        lines = settle(audit, 1)
        assert "\n" not in lines[0], "a newline survived into the audit record"
        assert "\r" not in lines[0]
        # 🔴 ASSERT THE PARSED FIELDS, NOT THE SPELLING. The escaped text still
        # CONTAINS the characters `auth=ok` inside the path value — a substring
        # check would be red for a record that is perfectly safe, and would then
        # be "fixed" by scrubbing the path into uselessness. What matters is
        # that a splitter sees one `auth` field and it says `fail`.
        fields = [part for part in lines[0].split() if "=" in part]
        keys = [part.split("=", 1)[0] for part in fields]
        assert keys.count("auth") == 1, f"more than one auth field: {lines[0]}"
        assert keys.count("token") == 1
        parsed = dict(part.split("=", 1) for part in fields)
        assert parsed["auth"] == "fail"
        assert parsed["token"] == "-"

    def test_the_forged_text_cannot_reach_the_log_as_SEPARATE_FIELDS(
        self, store: Path
    ):
        """Escaping newlines alone is not enough — a SPACE also opens a new
        field, so `path=/x auth=ok` would parse as two fields to any splitter.
        """
        with running(store) as (base, audit):
            fetch(f"{base}/api/v1/x%20auth=ok%20token=deadbeef1234")
            line = await_audit(audit, 1)[0]
        # 🔴 COUNT THE FIELDS; DO NOT `dict()` THEM. A round-2 mutation sweep
        # caught this test being vacuous: `dict()` lets the LAST occurrence win,
        # and the genuine `auth=fail` the server appends is always last — so
        # under the mutant that stops escaping spaces (leaving the forged
        # `auth=ok` in the line as its own field) the assertions still passed.
        # What a log consumer sees is a DUPLICATE key, so that is what to assert.
        keys = [part.split("=", 1)[0] for part in line.split() if "=" in part]
        assert keys.count("auth") == 1, f"the record has {keys.count('auth')} auth fields"
        assert keys.count("token") == 1, f"the record has {keys.count('token')} token fields"
        parsed = dict(part.split("=", 1) for part in line.split() if "=" in part)
        assert parsed["auth"] == "fail"
        assert parsed["token"] == "-"

    def test_control_characters_are_neutralised_but_the_path_is_still_LEGIBLE(
        self, store: Path
    ):
        with running(store) as (base, audit):
            fetch(f"{base}/api/v1/recall/{SCOPE}%00%09%1b[31m", token=GOOD_TOKEN)
            line = await_audit(audit, 1)[0]
        assert "\x00" not in line and "\x1b" not in line and "\t" not in line
        # A log that scrubbed everything would be safe and useless.
        assert SCOPE in line

    def test_an_ABSURDLY_long_path_cannot_flood_one_record(self, store: Path):
        with running(store) as (base, audit):
            fetch(f"{base}/api/v1/{'z' * 4000}")
            line = await_audit(audit, 1)[0]
        assert len(line) < 1000, "one request wrote an unbounded log record"
        assert "truncated" in line

    def test_POSITIVE_CONTROL_an_ordinary_path_is_logged_verbatim(self, store: Path):
        """Without this, every assertion above is satisfied by a `_audit` that
        logs nothing at all.
        """
        with running(store) as (base, audit):
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
            line = await_audit(audit, 1)[0]
        assert f"path=/api/v1/recall/{SCOPE}" in line
        assert f"token={api.token_id(GOOD_TOKEN)}" in line


def _smuggling_verdict(responses: "list[bytes]", what: str) -> str:
    """Say WHICH WAY a smuggling count was wrong — 0 and 2 are opposite bugs.

    🔴 The same mis-description devrc#1165 was about, in the sibling family.
    "a GET body was re-parsed as a request" is a sentence about `2`, asserted by
    a predicate that also fails at `0` — and `0` is what an empty read produces.
    A reader that returned nothing would have accused the server of smuggling.
    """
    if not responses:
        return (
            f"NO complete response came back for {what}. The read was EMPTY or "
            "PARTIAL — the server did not answer, or had not answered yet. "
            "NOTHING was re-parsed as a request; this is not a smuggling result "
            "at all, and re-reading it as one is how this flake was misdiagnosed."
        )
    return (
        f"{len(responses)} responses came back for {what} — the body WAS "
        f"re-parsed as a request on the same connection: {responses!r}"
    )


class TestNoRequestSmuggling:
    """🔴 CRITICAL. The server keeps connections alive and never read request
    bodies, so a body was parsed as the NEXT request on the same socket.

    Behind a proxy that pools upstream connections — Traefik does by default —
    that is CL.0 smuggling: a POST body holding a partial request line
    desynchronises the connection and the next VICTIM request completes the
    attacker's line, carrying the victim's `Authorization` header to a scope the
    attacker chose.
    """

    def _raw(self, host: str, payload: bytes, expect: int = 1) -> list[bytes]:
        """Write raw bytes on ONE socket and read every response that comes back.

        🔴 `expect` USED TO BE DECLARED AND NEVER READ, which is worse than not
        having it: the signature advertised "I wait for this many" while the
        body read until a 5 s timeout and returned whatever had arrived, and
        every caller took the default without passing anything. A parameter is
        not a code path. It is now the bound the reader actually waits on.

        Reads through `_raw_exchange` (defined further down, at the desync
        tests) rather than open-coding a second socket loop — that duplicate is
        how this family kept the empty-read race after the other one was fixed.
        Only the SPLIT pieces are returned here, which is the difference that
        justified two helpers in the first place.
        """
        raw, _eof = _raw_exchange(host, payload, expect=expect)
        return _responses(raw)

    def test_a_POST_BODY_is_not_served_as_the_next_request(self, store: Path):
        """The end-to-end property. ⚠ It is defended by BOTH layers, so a sweep
        shows it killed by the connection-close mutant and NOT by the
        no-drain one — the 405 closes the socket either way. The drain's own
        guard is the GET case below, where the response is a 200 and the
        connection legitimately stays open. Recorded because a reader who
        assumed this test covers the drain would delete the other one.
        """
        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            smuggled = (
                f"GET /api/v1/recall/{SCOPE} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"Authorization: Bearer {GOOD_TOKEN}\r\n"
                f"CF-Connecting-IP: {CLIENT_IP}\r\n\r\n"
            ).encode()
            payload = (
                f"POST /api/v1/recall/{SCOPE} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"CF-Connecting-IP: {CLIENT_IP}\r\n"
                f"Content-Length: {len(smuggled)}\r\n\r\n"
            ).encode() + smuggled
            responses = self._raw(host, payload)
        assert len(responses) == 1, _smuggling_verdict(
            responses, "a POST whose BODY held a complete second request line"
        )
        assert POINTER_LINE.encode() not in b"".join(responses)

    def test_a_GET_with_a_body_does_not_desynchronise_the_connection(
        self, store: Path
    ):
        """🔴 THIS is the drain's guard, not the POST case above. `/healthz`
        answers 200, so the connection is deliberately kept alive and the ONLY
        thing standing between the body and the next request is `_drain_body`.
        Confirmed by mutation: removing the drain kills exactly this test.
        """
        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            smuggled = (
                f"GET /api/v1/recall/{SCOPE} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"Authorization: Bearer {GOOD_TOKEN}\r\n"
                f"CF-Connecting-IP: {CLIENT_IP}\r\n\r\n"
            ).encode()
            payload = (
                f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n"
                f"Content-Length: {len(smuggled)}\r\n\r\n"
            ).encode() + smuggled
            responses = self._raw(host, payload)
        assert len(responses) == 1, _smuggling_verdict(
            responses, "a GET with a BODY on a deliberately kept-alive connection"
        )
        assert POINTER_LINE.encode() not in b"".join(responses)

    def test_POSITIVE_CONTROL_the_raw_harness_CAN_see_two_responses(
        self, store: Path
    ):
        """🔴 Otherwise "1 response" is a fact about the socket reader. Two
        genuinely pipelined requests must come back as two.
        """
        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            one = (
                f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n\r\n"
            ).encode()
            responses = self._raw(host, one + one, expect=2)
        assert len(responses) == 2, (
            f"the harness cannot see two responses on one connection: "
            f"{len(responses)} came back ({responses!r}). Every 'exactly 1' "
            "assertion in this class is vacuous until this passes."
        )

    def test_a_rejected_request_does_not_keep_its_connection(self, store: Path):
        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            bad = (
                f"GET /api/v1/recall/{SCOPE} HTTP/1.1\r\nHost: {host}\r\n"
                f"CF-Connecting-IP: {CLIENT_IP}\r\n\r\n"
            ).encode()
            responses = self._raw(host, bad + bad)
        assert len(responses) == 1, _smuggling_verdict(
            responses, "two pipelined requests the FIRST of which is a 401"
        )
        assert b"Connection: close" in responses[0]


class TestWritesAreMeteredLikeEverythingElse:
    """A write attempt used to answer 405 BEFORE the client-IP and lockout
    checks: 31 anonymous POSTs with no token and no `CF-Connecting-IP` produced
    31 audit lines and counted for nothing. That is a free, unauthenticated,
    unbounded channel for drowning the Loki alert this design depends on.
    """

    def test_a_POST_with_NO_client_ip_is_a_401_not_a_405(self, store: Path):
        with running(store) as (base, audit):
            code, _h, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}", method="POST", client_ip=None
            )
            line = await_audit(audit, 1)[0]
        assert code == 401
        assert body == b"unauthorized\n"
        assert "status=no-client-ip" in line

    def test_POST_probing_COUNTS_toward_the_lockout(self, store: Path):
        with running(store) as (base, _):
            for _ in range(5):
                fetch(f"{base}/api/v1/recall/{SCOPE}", method="POST")
            code, _h, _b = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert code == 401, "unauthenticated POSTs were an unmetered channel"

    def test_a_LOCKED_OUT_client_cannot_even_learn_that_writes_are_405(
        self, store: Path
    ):
        with running(store) as (base, _):
            for _ in range(5):
                fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
            code, _h, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, method="POST"
            )
        assert (code, body) == (401, b"unauthorized\n")

    def test_an_IDENTIFIED_caller_still_gets_the_405(self, store: Path):
        """The read-only guarantee is unchanged for anyone who gets that far —
        this is the positive control on the reordering.
        """
        with running(store) as (base, _):
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                code, headers, body = fetch(
                    f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, method=method
                )
                assert code == 405, f"{method} answered {code}"
                assert body == b"read-only\n"
                assert headers["Allow"] == "GET, HEAD"


class TestMalformedTargetsAndUnknownMethods:
    def test_an_absolute_form_target_that_breaks_urlsplit_is_a_401_not_a_CRASH(
        self, store: Path
    ):
        """`GET http://[ HTTP/1.1` raised an unhandled ValueError: no response,
        a killed connection, and a ~20-line traceback per request in the pod log
        — cheaper than any metered path. Absolute-form is mandatory-to-accept.
        """
        import socket

        with running(store) as (base, audit):
            host = base.split("//", 1)[1]
            with socket.create_connection(tuple(host.split(":")[:1]) + (int(host.split(":")[1]),), timeout=10) as sock:
                sock.sendall(
                    f"GET http://[ HTTP/1.1\r\nHost: {host}\r\n"
                    f"CF-Connecting-IP: {CLIENT_IP}\r\n\r\n".encode()
                )
                sock.settimeout(5)
                data = b""
                try:
                    while True:
                        chunk = sock.recv(65536)
                        if not chunk:
                            break
                        data += chunk
                except (TimeoutError, OSError):
                    pass
            lines = await_audit(audit, 1)
        assert data, "the request got no response at all"
        assert b"401" in data.split(b"\r\n")[0], data.split(b"\r\n")[0]
        assert b"unauthorized" in data
        assert any("status=malformed-target" in line for line in lines)

    def test_an_UNKNOWN_method_is_the_same_uniform_401_not_a_501_page(
        self, store: Path
    ):
        import socket

        for verb in ("OPTIONS", "TRACE", "FROBNICATE"):
            with running(store) as (base, _):
                host = base.split("//", 1)[1]
                name, port = host.split(":")
                with socket.create_connection((name, int(port)), timeout=10) as sock:
                    sock.sendall(
                        f"{verb} /api/v1/recall/{SCOPE} HTTP/1.1\r\nHost: {host}\r\n"
                        f"CF-Connecting-IP: {CLIENT_IP}\r\n\r\n".encode()
                    )
                    sock.settimeout(5)
                    data = b""
                    try:
                        while True:
                            chunk = sock.recv(65536)
                            if not chunk:
                                break
                            data += chunk
                    except (TimeoutError, OSError):
                        pass
            assert b"401" in data.split(b"\r\n")[0], f"{verb}: {data[:80]!r}"
            assert b"unauthorized\n" in data
            assert verb.encode() not in data, f"{verb} was echoed back to the caller"

    def test_a_traversal_component_is_refused_before_it_reaches_the_disk(
        self, store: Path
    ):
        with running(store) as (base, _):
            code, headers, _b = fetch(
                f"{base}/api/v1/recall/%2e%2e", token=GOOD_TOKEN
            )
        assert code == 400
        assert headers["X-Store-Status"] == "bad-request"

    def test_a_NORMAL_scope_name_is_still_accepted(self, store: Path):
        """The positive control on that guard — a guard that refused everything
        would pass the traversal test above and break every real caller.
        """
        with running(store) as (base, _):
            assert fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)[0] == 200
            code, _h, _b = fetch(
                f"{base}/api/v1/recall/{SCOPE}?ref=thing-alpha", token=GOOD_TOKEN
            )
        assert code == 200

    def test_a_DOT_in_a_path_component_is_refused_at_all(self, store: Path):
        """🔴 ADDED BECAUSE A MUTANT SURVIVED. Removing `.` from the character
        class left the whole suite green: nothing had a dotted PATH component,
        because refs travel in the query string. So the permissive class was
        never justified by a caller, and the guard is now structural — no dot at
        all, which makes `..` impossible to spell rather than excluded by name.

        Measured before tightening: all 8 scopes in the live store match
        `[A-Za-z0-9_-]+` and 0 contain a dot (counts only — the names are
        client-confidential and this repo is public).
        """
        with running(store) as (base, _):
            for probe in ("with.dot", "..", ".", "a.b.c", "%2e%2e%2f"):
                code, _h, _b = fetch(
                    f"{base}/api/v1/recall/{probe}", token=GOOD_TOKEN
                )
                assert code == 400, f"{probe!r} was accepted as a path component"

    def test_the_ref_QUERY_parameter_may_still_contain_a_dot(self, store: Path):
        """The path is strict; the query string is not, and must not become so —
        that is the distinction the surviving mutant exposed.
        """
        with running(store) as (base, _):
            code, _h, _b = fetch(
                f"{base}/api/v1/recall/{SCOPE}?ref=thing.alpha.v2", token=GOOD_TOKEN
            )
        assert code == 200


class TestRateLimitKeyIsAClientNotAnAddress:
    """An IPv6 /64 is one client's free choice of 2**64 addresses. Keying on the
    full address makes the lockout decorative and is the cheapest way to grow the
    failure table without bound.
    """

    def test_two_addresses_in_ONE_v6_slash_64_are_ONE_bucket(self, store: Path):
        a = "2001:db8:1:2::1"
        b = "2001:db8:1:2:ffff:ffff:ffff:ffff"
        with running(store) as (base, _):
            for _ in range(3):
                fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48, client_ip=a)
            for _ in range(2):
                fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48, client_ip=b)
            code, _h, _b = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=a
            )
        assert code == 401, "an attacker got a fresh bucket by changing host bits"

    def test_a_DIFFERENT_slash_64_is_a_DIFFERENT_bucket(self, store: Path):
        """The negative half: aggregating to /64 must not aggregate the world.
        Without this, `return 0` would pass the test above.
        """
        with running(store) as (base, _):
            for _ in range(6):
                fetch(
                    f"{base}/api/v1/recall/{SCOPE}",
                    token="w" * 48,
                    client_ip="2001:db8:1:2::1",
                )
            code, _h, _b = fetch(
                f"{base}/api/v1/recall/{SCOPE}",
                token=GOOD_TOKEN,
                client_ip="2001:db8:9:9::1",
            )
        assert code == 200, "an unrelated /64 was caught in someone else's lockout"

    def test_a_v4_MAPPED_address_is_the_SAME_bucket_as_its_v4_form(self):
        assert api.client_ip({"CF-Connecting-IP": "::ffff:203.0.113.7"}) == api.client_ip(
            {"CF-Connecting-IP": "203.0.113.7"}
        )

    def test_v4_is_NOT_aggregated(self):
        """A /64 rule misapplied to v4 would collapse whole ISPs into one
        bucket. Two adjacent v4 addresses stay two clients.
        """
        first = api.client_ip({"CF-Connecting-IP": "203.0.113.7"})
        second = api.client_ip({"CF-Connecting-IP": "203.0.113.8"})
        assert first != second


class TestTheTablesAreActuallyBounded:
    """`MAX_TRACKED_CLIENTS` was a bound in name only: it dropped just the
    entries that had already aged out, so INSIDE the window nothing was
    evictable. Measured before the fix: 20,000 tracked against a cap of 4,096.
    """

    def _limiter(self, now: list[float]):
        return api.RateLimiter(clock=lambda: now[0])

    def test_the_failure_table_stays_under_the_cap_with_NON_stale_entries(self):
        now = [1000.0]
        lim = self._limiter(now)
        for i in range(api.MAX_TRACKED_CLIENTS * 2):
            now[0] += 0.0001  # every entry stays well inside the window
            lim.record_failure(f"k{i}")
        assert len(lim._failures) <= api.MAX_TRACKED_CLIENTS, len(lim._failures)

    def test_a_LIVE_lockout_survives_that_flood(self):
        """Bounding must never be a release valve — the whole reason the first
        version only dropped stale entries.
        """
        now = [1000.0]
        lim = self._limiter(now)
        for _ in range(5):
            lim.record_failure("victim")
        assert lim.locked_out("victim") is True
        for i in range(api.MAX_TRACKED_CLIENTS * 2):
            now[0] += 0.0001
            lim.record_failure(f"k{i}")
        assert lim.locked_out("victim") is True

    def test_the_lockout_table_is_bounded_too(self):
        now = [1000.0]
        lim = self._limiter(now)
        for i in range(api.MAX_TRACKED_LOCKOUTS + 500):
            for _ in range(5):
                lim.record_failure(f"lk{i}")
            now[0] += 0.0001
        assert len(lim._locked_until) <= api.MAX_TRACKED_LOCKOUTS

    def test_the_client_CLOSEST_to_a_lockout_is_the_LAST_forgotten(self):
        """Oldest-first eviction, so the flood does not launder the attacker's
        own streak out of the table.
        """
        now = [1000.0]
        lim = self._limiter(now)
        lim.record_failure("early")
        for i in range(api.MAX_TRACKED_CLIENTS * 2):
            now[0] += 0.0001
            lim.record_failure(f"k{i}")
        assert "early" not in lim._failures
        assert f"k{api.MAX_TRACKED_CLIENTS * 2 - 1}" in lim._failures


class TestNonFiniteLimiterSettings:
    """`nan <= 0` is False, so both walked through the "must be positive" guard —
    the exact "misconfiguration that defaults is invisible forever" the function
    exists to prevent, arriving through the one comparison that does not order
    them. Measured before the fix: a nan WINDOW silently disabled the limiter
    entirely; a nan or inf LOCKOUT made it permanent.
    """

    @pytest.mark.parametrize("value", ["nan", "inf", "-inf", "NaN", "Infinity"])
    @pytest.mark.parametrize(
        "name",
        [
            "SUBSYSTEM_STORE_FAILURE_WINDOW_S",
            "SUBSYSTEM_STORE_LOCKOUT_S",
        ],
    )
    def test_a_non_finite_value_is_REFUSED(self, name: str, value: str):
        with pytest.raises(ValueError) as exc:
            api.limiter_settings({name: value})
        assert name in str(exc.value)

    def test_a_FINITE_value_is_still_accepted(self):
        assert api.limiter_settings(
            {"SUBSYSTEM_STORE_LOCKOUT_S": "42.5"}
        ) == (5, 60.0, 42.5)


class TestSlowlorisCannotPinAThreadForever:
    def test_the_handler_declares_a_socket_TIMEOUT(self):
        # `timeout = None` is the stdlib default and means "wait forever".
        # Measured before the fix: 50 half-open connections held 50 threads.
        assert api.StoreRequestHandler.timeout is not None
        assert 0 < api.StoreRequestHandler.timeout <= 60

    def test_a_HALF_OPEN_connection_is_dropped_rather_than_held(self, store: Path):
        """Behavioural, not a constant check: send headers with no terminating
        blank line and watch the server give up on its own.
        """
        import socket
        import time

        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            name, port = host.split(":")
            sock = socket.create_connection((name, int(port)), timeout=10)
            try:
                sock.sendall(b"GET /healthz HTTP/1.1\r\n")  # never finished
                sock.settimeout(api.StoreRequestHandler.timeout + 10)
                started = time.monotonic()
                data = sock.recv(65536)  # returns b"" when the server closes
                elapsed = time.monotonic() - started
            finally:
                sock.close()
        assert data == b"", f"expected a close, got {data[:60]!r}"
        assert elapsed < api.StoreRequestHandler.timeout + 5


# =============================================================================
# 17. THE DELTA AUDIT ROUND — the fixes in section 16 introduced three
# criticals, in the same commit that closed two.
#
# 🔴 THIS IS THE POINT OF RE-AUDITING THE DELTA, and it is not a formality:
# every round in this repo's history has found a real defect in the PRECEDING
# round's fix. Here the `send_error` override written to make unknown verbs
# uniform reopened BOTH defects the rest of that commit closed — an unhandled
# crash on a malformed request line, and a free unmetered channel — one screen
# below the code that exists to prevent them.
# =============================================================================


def _speak(host: str, payload: bytes, *, wait: float = 3.0) -> bytes:
    """Write raw bytes to a socket and read whatever comes back."""
    import socket

    name, port = host.split(":")
    with socket.create_connection((name, int(port)), timeout=10) as sock:
        sock.sendall(payload)
        sock.settimeout(wait)
        data = b""
        try:
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        except (TimeoutError, OSError):
            pass
    return data


class TestMalformedRequestLinesDoNotCrash:
    """🔴 `parse_request` assigns `self.path` only AFTER five of its own
    `send_error` calls, and `path` has no class-level default — so an override
    that read `self.path` raised AttributeError on every malformed request LINE.
    Measured: 6 of 7 probe shapes crashed, no audit record, ~25 lines of
    traceback each, from a six-byte request.
    """

    # Each is a request LINE the stdlib rejects BEFORE assigning self.path.
    SHAPES = [
        # A three-component version: `parse_request` raises on the split.
        # ⚠ THREE components, deliberately not four. A four-component
        # version string is indistinguishable from an IPv4 literal, and
        # `test_no_public_ips` reads it as one — correctly, in a PUBLIC
        # repo. This comment names the shape rather than quoting it, for
        # the same reason: an explanation that quotes the banned value is
        # the banned value.
        b"GET /x HTTP/1.1.1\r\n\r\n",
        b"GET /x HTTP/2.0\r\n\r\n",
        b"GET\r\n\r\n",
        b"POST /x\r\n\r\n",
        b"GET /x y HTTP/1.1\r\n\r\n",
    ]

    @pytest.mark.parametrize("shape", SHAPES, ids=lambda s: s.split(b"\r\n")[0].decode())
    def test_it_answers_instead_of_crashing(self, store: Path, shape: bytes):
        with running(store) as (base, audit):
            data = _speak(base.split("//", 1)[1], shape)
            await_audit(audit, 1)
        assert data, "the request got no response at all"
        assert b"unauthorized\n" in data
        # And it was RECORDED — a crash produces no audit line, which is what
        # made this invisible to every wire-level assertion.
        lines = settle(audit, 1)
        assert "auth=fail" in lines[0]

    def test_the_audit_line_survives_a_missing_request_path(self, store: Path):
        with running(store) as (base, audit):
            _speak(base.split("//", 1)[1], b"GET\r\n\r\n")
            line = await_audit(audit, 1)[0]
        assert "path=-" in line, line
        assert "status=malformed-request" in line
        # 🔴 `peer=-`, THE THIRD VALUE OF THAT FIELD, AND THE ONLY ONE NOTHING
        # ASSERTED. Six bytes is too little to have headers, so `send_error`
        # answers before `_identify_and_meter` ever runs and `_peer_trusted` is
        # still `None`. Found by a mutation sweep with NO `-k` selector:
        # rendering that `None` as `trusted` survived a fully green suite —
        # `peer=trusted|untrusted` were asserted eleven times and `peer=-` zero.
        #
        # The untested direction is the dangerous one. It makes the audit log
        # assert TRUST about a request whose peer was never evaluated, which is
        # the one claim this field exists to let an operator rely on.
        assert "peer=-" in line, line
        assert "peer=trusted" not in line, line

    def test_POSITIVE_CONTROL_a_WELL_FORMED_unknown_verb_still_works(
        self, store: Path
    ):
        """The shape the previous round DID test — the one where `self.path`
        happens to be set, which is exactly why the crash stayed invisible.
        """
        with running(store) as (base, audit):
            data = _speak(
                base.split("//", 1)[1],
                f"FROBNICATE /api/v1/recall/{SCOPE} HTTP/1.1\r\n"
                f"Host: h\r\nCF-Connecting-IP: {CLIENT_IP}\r\n\r\n".encode(),
            )
            await_audit(audit, 1)
        assert b"401" in data.split(b"\r\n")[0]
        settle(audit, 1)


class TestUnknownVerbsAreMeteredToo:
    """The second half of the same regression: the override answered without
    ever metering, so 30 `FROBNICATE`s wrote 30 audit lines and counted for
    nothing — the free channel `_reject_write` had just been reordered to close,
    widened to every verb that is not one of the six with a handler.
    """

    def _verb(self, base: str, verb: str, ip: str | None = CLIENT_IP) -> bytes:
        headers = f"Host: h\r\n" + (f"CF-Connecting-IP: {ip}\r\n" if ip else "")
        return _speak(
            base.split("//", 1)[1],
            f"{verb} /api/v1/recall/{SCOPE} HTTP/1.1\r\n{headers}\r\n".encode(),
        )

    def test_unknown_verb_probing_COUNTS_toward_the_lockout(self, store: Path):
        with running(store) as (base, _):
            for _ in range(5):
                assert b"unauthorized" in self._verb(base, "FROBNICATE")
            code, _h, _b = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert code == 401, "unknown verbs were an unmetered channel"

    def test_an_unknown_verb_with_NO_client_ip_fails_closed(self, store: Path):
        with running(store) as (base, audit):
            data = self._verb(base, "OPTIONS", ip=None)
            line = await_audit(audit, 1)[0]
        assert b"unauthorized" in data
        assert "status=no-client-ip" in line

    def test_a_MALFORMED_request_line_cannot_be_a_keep_alive_channel(
        self, store: Path
    ):
        """It cannot be metered — there are no headers to identify anyone by —
        so it is bounded the only other way: one request per TCP handshake.
        """
        with running(store) as (base, _):
            data = _speak(base.split("//", 1)[1], b"GET\r\n\r\n" + b"GET\r\n\r\n")
        assert data.count(b"unauthorized\n") == 1, "the connection was reused"


class TestSmugglingViaOtherFramings:
    """🔴 The drain understood exactly ONE framing, and a delta audit walked it
    with two others — each producing two responses on one socket with store
    content in the second, on a 200 where the connection legitimately stays open
    so the close-on-non-200 belt does not apply.
    """

    def _smuggled(self, host: str) -> bytes:
        return (
            f"GET /api/v1/recall/{SCOPE} HTTP/1.1\r\nHost: {host}\r\n"
            f"Authorization: Bearer {GOOD_TOKEN}\r\n"
            f"CF-Connecting-IP: {CLIENT_IP}\r\n\r\n"
        ).encode()

    def test_a_CHUNKED_body_cannot_smuggle_a_request(self, store: Path):
        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            body = self._smuggled(host)
            payload = (
                f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n"
                f"Transfer-Encoding: chunked\r\n\r\n"
                f"{len(body):x}\r\n"
            ).encode() + body + b"\r\n0\r\n\r\n"
            data = _speak(host, payload)
        assert data.count(b"HTTP/1.1 ") <= 1, "chunked framing smuggled a request"
        assert POINTER_LINE.encode() not in data

    def test_a_NEGATIVE_content_length_cannot_smuggle_a_request(self, store: Path):
        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            payload = (
                f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n"
                f"Content-Length: -5\r\n\r\n"
            ).encode() + self._smuggled(host)
            data = _speak(host, payload)
        assert data.count(b"HTTP/1.1 ") <= 1, "a negative length smuggled a request"
        assert POINTER_LINE.encode() not in data

    def test_POSITIVE_CONTROL_the_probe_CAN_see_a_second_response(self, store: Path):
        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            one = f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n\r\n".encode()
            data = _speak(host, one + one)
        assert data.count(b"HTTP/1.1 ") == 2, "the probe cannot see two responses"

    def test_a_DRIPPED_body_cannot_hold_a_thread_indefinitely(self, store: Path):
        """`timeout` is per-RECV, so a caller that sends a byte before each
        timeout expires satisfies it forever — measured holding a thread 60s for
        a SIX-byte body. The read loop needed its own total deadline.

        🔴 THE FIRST VERSION OF THIS TEST WAS VACUOUS, and a mutation sweep said
        so: it sent ONE byte and then waited, so the per-recv timeout ended the
        connection at ~15s either way and the assertion (`< deadline + 15`) was
        satisfied with the deadline deleted. A drip test has to actually DRIP —
        fast enough that the per-recv timeout never fires — or it measures the
        socket timeout and calls it the deadline.
        """
        import socket
        import threading
        import time

        assert api.DRAIN_DEADLINE_S <= 30
        interval = api.DRAIN_DEADLINE_S / 4
        assert interval < api.StoreRequestHandler.timeout, (
            "the drip must outpace the per-recv timeout, or this measures that"
        )
        with running(store) as (base, _):
            name, port = base.split("//", 1)[1].split(":")
            sock = socket.create_connection((name, int(port)), timeout=10)
            stop = threading.Event()

            def drip():
                # 200 bytes at `interval` apart would take 50 * DEADLINE if the
                # deadline did not exist.
                for _ in range(200):
                    if stop.wait(interval):
                        return
                    try:
                        sock.sendall(b"x")
                    except OSError:
                        return

            try:
                sock.sendall(
                    f"POST /api/v1/recall/{SCOPE} HTTP/1.1\r\nHost: h\r\n"
                    f"CF-Connecting-IP: {CLIENT_IP}\r\n"
                    f"Content-Length: 200\r\n\r\n".encode()
                )
                started = time.monotonic()
                dripper = threading.Thread(target=drip, daemon=True)
                dripper.start()
                sock.settimeout(api.DRAIN_DEADLINE_S * 6)
                try:
                    sock.recv(65536)
                except (TimeoutError, OSError):
                    pass
                elapsed = time.monotonic() - started
            finally:
                stop.set()
                sock.close()
        # Without the deadline the drip keeps the connection alive far past this.
        assert elapsed < api.DRAIN_DEADLINE_S * 3, (
            f"a dripped body held the thread for {elapsed:.1f}s"
        )


class TestTheLockoutCapDoesNotLIE:
    """At the cap, `record_failure` returned True regardless AND popped the
    streak: the audit log claimed `lockout-triggered` for a client that was not
    locked out, and no state accumulated at all — unlimited brute force for an
    attacker who had first filled the table.

    🔴 The cap is monkeypatched DOWN rather than filled. Filling the real one is
    81,920 calls, which made the suite 45s slower and — worse — forced the
    "a slot frees" case to advance the clock past the lockout, which also aged
    out the attacker's own streak and tested nothing. A small cap reaches the
    same branch with the states actually distinguishable.
    """

    CAP = 8

    def _full(self, now: list[float], monkeypatch, **kwargs):
        monkeypatch.setattr(api, "MAX_TRACKED_LOCKOUTS", self.CAP)
        lim = api.RateLimiter(clock=lambda: now[0], **kwargs)
        for i in range(self.CAP):
            for _ in range(5):
                lim.record_failure(f"filler{i}")
            now[0] += 0.0001
        assert len(lim._locked_until) == self.CAP
        return lim

    def test_at_the_cap_it_reports_FALSE_rather_than_a_lockout_it_did_not_make(
        self, monkeypatch
    ):
        now = [1000.0]
        lim = self._full(now, monkeypatch)
        results = [lim.record_failure("attacker") for _ in range(5)]
        assert results[-1] is False, "it claimed a lockout the table had no room for"
        assert lim.locked_out("attacker") is False

    def test_at_the_cap_the_streak_is_KEPT_so_state_still_accumulates(
        self, monkeypatch
    ):
        """The half that matters for brute force: popping the streak meant every
        five failures started over, so nothing ever accumulated. Keeping it holds
        the client AT the threshold, so the lockout lands the moment a slot frees.

        The lockout is deliberately SHORTER than the window here, so a filler can
        expire while the attacker's streak is still live — the only arrangement
        in which "a slot frees" is observable at all.
        """
        now = [1000.0]
        lim = self._full(now, monkeypatch, lockout_s=5.0, window_s=600.0)
        for _ in range(9):
            lim.record_failure("attacker")
        assert len(lim._failures.get("attacker", [])) >= 5, "the streak was reset"
        now[0] += 6.0  # fillers expire; the attacker's streak is still in-window
        assert lim.record_failure("attacker") is True
        assert lim.locked_out("attacker") is True

    def test_EXPIRED_lockouts_are_released_so_the_table_DRAINS(self, monkeypatch):
        """🔴 The release loop had ZERO coverage — a surviving mutant in the
        previous sweep — and it is the only thing that drains `_locked_until`,
        i.e. the only reason the cap above is not permanent.
        """
        now = [1000.0]
        lim = self._full(now, monkeypatch, lockout_s=5.0, window_s=600.0)
        now[0] += 6.0
        lim.record_failure("anyone")
        assert len(lim._locked_until) < self.CAP, "the table never drained"


class TestEveryAuditFieldIsEscaped:
    def test_the_METHOD_is_escaped_not_just_the_path(self, store: Path):
        """🔴 A surviving mutant with zero coverage: a verb can carry control
        bytes, and the method field went into the record unescaped.
        """
        with running(store) as (base, audit):
            _speak(
                base.split("//", 1)[1],
                f"FROB\x1b[31mNICATE /api/v1/recall/{SCOPE} HTTP/1.1\r\n"
                f"Host: h\r\nCF-Connecting-IP: {CLIENT_IP}\r\n\r\n".encode(),
            )
            lines = await_audit(audit, 1)
        assert lines, "no audit line was written"
        assert "\x1b" not in lines[0], "an escape sequence reached the log"
        assert "\n" not in lines[0]


# =============================================================================
# 18. THE FINAL AUDIT ROUND — a third framing, and the loop nothing looped.
# =============================================================================


class TestDuplicateContentLengthCannotSmuggle:
    """🔴 A WORKING SMUGGLE, found by a final audit, in the function whose own
    comment claimed no framing could walk it.

    `Content-Length: 0` followed by `Content-Length: 154`: a bare `.get()` takes
    the FIRST value, so nothing is drained and the body is served as the next
    request — on a `/healthz` 200, where the connection legitimately stays open
    and the close-on-non-200 belt does not apply.

    The predicate that catches it already existed TWENTY LINES AWAY, in
    `client_ip`, rejecting a duplicated `CF-Connecting-IP` for exactly this
    reason. Open-coded twice, correct at one site and wrong at the other — which
    is the shape a duplicated predicate always takes. It is now one function.
    """

    def _smuggled(self, host: str) -> bytes:
        return (
            f"GET /api/v1/recall/{SCOPE} HTTP/1.1\r\nHost: {host}\r\n"
            f"Authorization: Bearer {GOOD_TOKEN}\r\n"
            f"CF-Connecting-IP: {CLIENT_IP}\r\n\r\n"
        ).encode()

    def test_two_content_length_headers_cannot_smuggle_a_request(self, store: Path):
        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            body = self._smuggled(host)
            payload = (
                f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n"
                f"Content-Length: 0\r\nContent-Length: {len(body)}\r\n\r\n"
            ).encode() + body
            data = _speak(host, payload)
        assert data.count(b"HTTP/1.1 ") <= 1, "a duplicated Content-Length smuggled"
        assert POINTER_LINE.encode() not in data

    def test_the_LARGER_value_first_is_refused_too(self, store: Path):
        """Order must not matter — a guard that only looked at the first value
        would pass the test above by accident if the smaller one came second.
        """
        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            body = self._smuggled(host)
            payload = (
                f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n"
                f"Content-Length: {len(body)}\r\nContent-Length: 0\r\n\r\n"
            ).encode() + body
            data = _speak(host, payload)
        assert data.count(b"HTTP/1.1 ") <= 1
        assert POINTER_LINE.encode() not in data

    def test_the_shared_predicate_is_used_by_BOTH_sites(self):
        """Structural, and deliberately so: the behavioural tests above and the
        duplicate-`CF-Connecting-IP` test elsewhere would both stay green if the
        two sites drifted apart again into two correct-today copies. What is
        being pinned is that there is ONE predicate.
        """
        code = _executable_tokens(SERVER_PATH)
        assert code.count("get_all") <= 2, (
            "header-multiplicity logic is open-coded again; it belongs in "
            "sole_header()"
        )
        assert "sole_header" in code

    def test_sole_header_directly(self):
        assert api.sole_header({"X": "1"}, "X") == "1"
        assert api.sole_header({}, "X") is None


class TestTheDrainLoopActuallyLOOPS:
    """🔴 A GAP THIS BRANCH CREATED. Under the old `read(n)` the drain consumed
    the whole body in one call; `read1` is what made the loop bookkeeping
    load-bearing — and every other smuggling test sends headers and body in ONE
    `sendall`, so the body arrives in one segment, the loop runs once, and the
    accumulator is never exercised. Two mutants survived the whole suite.

    🔴 THE DELIVERY SHAPE DECIDES WHICH DIRECTION IS OBSERVABLE, and a first
    version of these tests missed that. Under-consumption (a broken accumulator)
    is only visible when the body arrives in SEVERAL segments; over-consumption
    (a dropped `min(remaining, …)` clamp) is only visible when the body and the
    NEXT request arrive in ONE segment, because `read1` can only over-read bytes
    that are already buffered. One shape cannot see both.
    """

    def _exchange(self, store: Path, *, segmented: bool, fill: int = 4000) -> bytes:
        import socket
        import time

        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            name, port = host.split(":")
            smuggled = (
                f"GET /api/v1/recall/{SCOPE} HTTP/1.1\r\nHost: {host}\r\n"
                f"Authorization: Bearer {GOOD_TOKEN}\r\n"
                f"CF-Connecting-IP: {CLIENT_IP}\r\n\r\n"
            ).encode()
            body = b"F" * fill + smuggled
            follow = f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n\r\n".encode()
            head = (
                f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n"
                f"Content-Length: {len(body)}\r\n\r\n"
            ).encode()
            with socket.create_connection((name, int(port)), timeout=10) as sock:
                if segmented:
                    # Separate sends with gaps: each `read1` returns only what
                    # has arrived, so the loop MUST iterate and accumulate.
                    sock.sendall(head)
                    time.sleep(0.05)
                    for at in range(0, len(body), 512):
                        sock.sendall(body[at : at + 512])
                        time.sleep(0.01)
                    time.sleep(0.05)
                    sock.sendall(follow)
                else:
                    # One segment: everything is buffered at once, so a `read1`
                    # without the clamp will swallow `follow` too.
                    sock.sendall(head + body + follow)
                sock.settimeout(4)
                data = b""
                try:
                    while True:
                        chunk = sock.recv(65536)
                        if not chunk:
                            break
                        data += chunk
                except (TimeoutError, OSError):
                    pass
        return data

    def test_a_SEGMENTED_body_is_drained_WHOLE(self, store: Path):
        """UNDER-consumption. A broken accumulator stops after the first
        `read1`, leaving the tail to be parsed as the next request — which both
        loses the caller's real next request and can serve the smuggled one.
        """
        data = self._exchange(store, segmented=True)
        assert POINTER_LINE.encode() not in data, "a segmented body leaked store content"
        assert data.count(b"HTTP/1.1 200") == 2, (
            f"expected both /healthz answers, got {data.count(b'HTTP/1.1 200')}: "
            f"{data[:140]!r}"
        )

    def test_a_SINGLE_SEGMENT_body_is_drained_NO_FURTHER(self, store: Path):
        """OVER-consumption, the direction the segmented case structurally
        cannot see. Without the `min(remaining, …)` clamp the drain reads the
        following pipelined request out of the buffer and answers it never —
        fail-safe for smuggling, but it silently breaks keep-alive, and "safe by
        accident in one direction" is not a property to leave untested.
        """
        data = self._exchange(store, segmented=False)
        assert POINTER_LINE.encode() not in data
        assert data.count(b"HTTP/1.1 200") == 2, (
            f"the drain ate the next request: {data.count(b'HTTP/1.1 200')} "
            f"responses, {data[:140]!r}"
        )

    def test_POSITIVE_CONTROL_both_shapes_answer_TWICE_when_correct(
        self, store: Path
    ):
        """Both assertions above are `== 2`, so a server that answered nothing
        would fail them — but a HARNESS that could never see two would fail them
        identically. This pins that the shapes themselves are well-formed.
        """
        for segmented in (True, False):
            data = self._exchange(store, segmented=segmented, fill=16)
            assert data.count(b"ok\n") == 2, f"segmented={segmented}: {data[:140]!r}"


# =============================================================================
# 18. `CF-Connecting-IP` WAS TRUSTED FROM ANY PEER (phase 1.5b)
#
# 🔴 THIS SECTION IS THE ONE PLACE IN THIS FILE WITH REAL REGRESSION COVERAGE,
# and only the `TestTrustedProxyOverTheRealProcess` half is. The distinction
# matters and the file's header explains why: `server.py` did not exist before
# phase 1, so everything else here is red at ITS base for a collection error.
# Phase 1.5b's base ref is different — `server.py` exists, it parses the same
# command line, and it IGNORES `$SUBSYSTEM_STORE_TRUSTED_PROXIES` completely.
# So a test that spawns the real process with that variable set runs on BOTH
# trees and its failure at base is a statement about BEHAVIOUR:
#
#   * base honours a `CF-Connecting-IP` from an untrusted peer, and locks the
#     named third party out in five requests   <- THE DEFECT
#   * base serves 200 to a valid token from an untrusted peer
#   * base STARTS with the variable unset
#
# The in-process classes below are invariant guards. They are labelled as such
# and their evidence is the mutation matrix in the PR body, not their red.
# =============================================================================


class TestTrustedProxyOverTheRealProcess:
    """🔴 RED AT THE BASE REF BEHAVIOURALLY, not by AttributeError."""

    @pytest.fixture
    def token_file(self, tmp_path: Path) -> Path:
        path = tmp_path / "token"
        path.write_text(GOOD_TOKEN + "\n")
        return path

    def test_POSITIVE_CONTROL_a_TRUSTED_peer_still_gets_its_digest(
        self, store: Path, token_file: Path
    ):
        """Before any 401 below is read as "the guard fired": this exact call
        shape, against a process spawned exactly this way, CAN return a 200 with
        store content. Without it a server that refused everything — including
        one that failed to read its store at all — would pass every assertion in
        this class.
        """
        with running_subprocess(store, token_file) as (base, _proc):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=CLIENT_IP
            )
        assert code == 200, body
        assert headers.get("X-Store-Status") == "recalled"
        assert POINTER_LINE.encode() in body

    def test_THE_DEFECT_a_forged_header_from_an_untrusted_peer_locks_out_a_victim(
        self, store: Path, token_file: Path
    ):
        """🔴 THE WHOLE POINT OF THIS BRANCH, and the one test whose red at base
        is the bug rather than the diff.

        ONE running process, TWO peers. `127.0.0.1` is the allowlisted proxy;
        `127.0.0.2` is anything that can address the pod directly. The attacker
        binds `127.0.0.2` and sends five bad tokens while CLAIMING, in the
        header, to be `SPOOF_IP`. Then the legitimate proxy forwards a request
        for that same `SPOOF_IP` client, with a VALID token.

          base: the five are charged to SPOOF_IP -> the victim is 401 for 15 min
          HEAD: the five are charged to 127.0.0.2, the forger's own address
                -> the victim gets its 200

        ⚠ "not one of them reaches the limiter" is what this line used to say,
        and it described the refuse-outright design that was replaced. They DO
        reach it now, and should: five failed auths from one caller is a real
        lockout — of that caller. The property is only ever about WHOSE bucket,
        which is what the sibling
        `test_THE_DEFECT_the_five_forged_attempts_are_CHARGED_TO_THE_FORGER`
        reads out of the audit line.

        🔴 THE FIRST DRAFT OF THIS TEST WAS VACUOUS AND PASSED AT BASE. It ran
        every request from ONE address and asserted the victim saw a 401 — which
        is true on both trees, because the wire deliberately does not
        discriminate. A test of a lockout has to observe the VICTIM getting
        through, and that needs a second peer.
        """
        with running_subprocess(
            store,
            token_file,
            trusted_proxies=f"{TRUSTED_PEER}/32",
            host=TRUSTED_PEER,
        ) as (base, _proc):
            for _ in range(5):
                attempt = fetch_from(
                    UNTRUSTED_PEER,
                    base,
                    f"/api/v1/recall/{SCOPE}",
                    token="w" * 48,
                    client_ip=SPOOF_IP,
                )
                assert attempt == 401, attempt
            victim = fetch_from(
                TRUSTED_PEER,
                base,
                f"/api/v1/recall/{SCOPE}",
                token=GOOD_TOKEN,
                client_ip=SPOOF_IP,
            )
        assert victim == 200, (
            f"the victim got {victim}: five requests from an UNTRUSTED peer, "
            f"forging CF-Connecting-IP, locked out a third party"
        )

    def test_POSITIVE_CONTROL_the_same_five_from_a_TRUSTED_peer_DO_lock_out(
        self, store: Path, token_file: Path
    ):
        """🔴 THE OTHER HALF, and without it the test above is satisfied by a
        server with no lockout at all — including one where the limiter was
        deleted outright. Identical shape, identical count, ONE difference: the
        five bad tokens come from the ALLOWLISTED peer, so they are a real
        client failing auth and the lockout must fire.
        """
        with running_subprocess(
            store,
            token_file,
            trusted_proxies=f"{TRUSTED_PEER}/32",
            host=TRUSTED_PEER,
        ) as (base, _proc):
            for _ in range(5):
                fetch_from(
                    TRUSTED_PEER,
                    base,
                    f"/api/v1/recall/{SCOPE}",
                    token="w" * 48,
                    client_ip=SPOOF_IP,
                )
            victim = fetch_from(
                TRUSTED_PEER,
                base,
                f"/api/v1/recall/{SCOPE}",
                token=GOOD_TOKEN,
                client_ip=SPOOF_IP,
            )
        assert victim == 401, (
            f"got {victim}: five FAILED AUTHS from a real client did not lock "
            f"it out, so the test above proves nothing"
        )

    def test_THE_DEFECT_the_five_forged_attempts_are_CHARGED_TO_THE_FORGER(
        self, store: Path, token_file: Path
    ):
        """The status-code half cannot see WHO was charged. At base the five
        forged attempts are booked against `ip=198.51.100.7` — the victim — and
        the fifth is `status=lockout-triggered`. At HEAD every one is booked
        against the forger's own address with `peer=untrusted`. Read off the
        process's real STDOUT, which is the stream Loki ingests.

        ⚠ THIS IS NOT "the attempts do not reach the limiter" ANY MORE. They do,
        and they should: an untrusted caller failing auth five times is a real
        failed auth and gets a real lockout — of ITSELF. The property is only
        ever about WHOSE bucket.
        """
        # 🔴 WAIT FOR THE AUDIT LINES; DO NOT ASSUME THE RESPONSE IMPLIES THEM.
        # The measurement that found this race (#544): 3/20 red locally and two
        # consecutive reds in the nix sandbox, always `assert 4 == 5` with four
        # identical audit lines. Re-running was the wrong answer — a ~15% flaky
        # gate is the thing that teaches everyone to click through a red run.
        # The mechanism and the reason this is now shared rather than copied are
        # on `drain_output`.
        with running_subprocess(
            store,
            token_file,
            trusted_proxies=f"{TRUSTED_PEER}/32",
            host=TRUSTED_PEER,
        ) as (base, proc):
            out = drain_output(proc)
            for _ in range(5):
                fetch_from(
                    UNTRUSTED_PEER,
                    base,
                    f"/api/v1/recall/{SCOPE}",
                    token="w" * 48,
                    client_ip=SPOOF_IP,
                )
            await_audit(out, 5)
        lines = settle(out, 5)
        # 🔴 THE ASSERTION THAT IS THE WHOLE DEFECT: the forged address never
        # becomes an identity. A fix that recorded the spoofed value but declined
        # to COUNT it would pass every status check and fail this one.
        assert all(f"ip={SPOOF_IP}" not in ln for ln in lines), lines
        assert all(f"ip={UNTRUSTED_PEER}" in ln for ln in lines), lines
        assert all("peer=untrusted" in ln for ln in lines), lines
        # …and the forger locks out ITSELF, which is a rate limiter working.
        #
        # 🔴 READ AS A MULTISET, NEVER AS `lines[-1]`. `await_audit` guarantees
        # the five lines EXIST; it cannot guarantee the ORDER they were appended
        # in, and it never could. The five requests are issued sequentially and
        # the limiter is charged before the response, so the fifth is the one
        # that trips — but each handler writes its response and only then races
        # to `_audit()`, so request 5's record can be appended before request
        # 4's. `lines[-1]` then holds a plain `unauthorized` and the test is red
        # on a tree with nothing wrong with it. The multiset says exactly what
        # the sentence above says — five attempts, one of them the lockout — and
        # says it about all five records instead of one.
        statuses = sorted(ln.split("status=")[1].split()[0] for ln in lines)
        assert statuses == ["lockout-triggered"] + ["unauthorized"] * 4, (
            f"expected four unauthorized and one lockout-triggered, got "
            f"{statuses}:\n" + "\n".join(lines))

    def test_an_untrusted_peer_LOCKING_ITSELF_OUT_does_not_touch_the_victim(
        self, store: Path, token_file: Path
    ):
        """The pair to the test above, and the one that stops "charge the peer"
        from being a euphemism for "do not charge anything". The forger is
        locked out — its own next request is refused — while the client it named
        is served normally through the trusted proxy.
        """
        with running_subprocess(
            store,
            token_file,
            trusted_proxies=f"{TRUSTED_PEER}/32",
            host=TRUSTED_PEER,
        ) as (base, _proc):
            for _ in range(5):
                fetch_from(
                    UNTRUSTED_PEER,
                    base,
                    f"/api/v1/recall/{SCOPE}",
                    token="w" * 48,
                    client_ip=SPOOF_IP,
                )
            forger = fetch_from(
                UNTRUSTED_PEER,
                base,
                f"/api/v1/recall/{SCOPE}",
                token=GOOD_TOKEN,
                client_ip=SPOOF_IP,
            )
            victim = fetch_from(
                TRUSTED_PEER,
                base,
                f"/api/v1/recall/{SCOPE}",
                token=GOOD_TOKEN,
                client_ip=SPOOF_IP,
            )
        assert forger == 401, f"the forger was not locked out: {forger}"
        assert victim == 200, f"the victim was collateral: {victim}"

    def test_a_TRUSTED_peers_HEADER_actually_separates_two_clients(
        self, store: Path, token_file: Path
    ):
        """🔴 THE ONE PROPERTY EVERY OTHER PROCESS-LEVEL TEST HERE IS BLIND TO,
        and a mutation sweep is what showed it: replacing the allowlist `main`
        passes to `build_server` with a hardcoded one that matches NOBODY
        survived the whole class. Every test either drove an untrusted peer (a
        mutant that trusts nobody agrees) or drove one client through a trusted
        peer (bucketing it under the peer instead of its header is invisible
        when there is only one client).

        So: five failures through the trusted proxy on behalf of ONE client,
        then a DIFFERENT client through the SAME proxy. It must be served — the
        header, not the peer, is what separated them. If the proxy's allowlist
        were not in force, both would share the peer's bucket and the second
        client would be collateral.
        """
        with running_subprocess(
            store,
            token_file,
            trusted_proxies=f"{TRUSTED_PEER}/32",
            host=TRUSTED_PEER,
        ) as (base, _proc):
            for _ in range(5):
                fetch_from(
                    TRUSTED_PEER,
                    base,
                    f"/api/v1/recall/{SCOPE}",
                    token="w" * 48,
                    client_ip=SPOOF_IP,
                )
            same_client = fetch_from(
                TRUSTED_PEER,
                base,
                f"/api/v1/recall/{SCOPE}",
                token=GOOD_TOKEN,
                client_ip=SPOOF_IP,
            )
            other_client = fetch_from(
                TRUSTED_PEER,
                base,
                f"/api/v1/recall/{SCOPE}",
                token=GOOD_TOKEN,
                client_ip=OTHER_IP,
            )
        # Both halves, or the assertion is satisfied by a server with no limiter
        # (everything 200) or one that locked the whole proxy out (everything
        # 401).
        assert same_client == 401, f"the guilty client was not locked out: {same_client}"
        assert other_client == 200, (
            f"an innocent client behind the same proxy got {other_client} — the "
            f"bucket was the PEER, not the header"
        )

    def test_a_VALID_token_from_an_untrusted_peer_is_SERVED_but_bucketed_under_the_PEER(
        self, store: Path, token_file: Path
    ):
        """🔴 THIS REPLACES A TEST THAT PINNED THE WRONG BEHAVIOUR. It used to
        assert a 401, which was stricter than the security property needs and
        broke the phase-1 acceptance procedure outright: `verify-byte-identity.sh`
        runs through `kubectl port-forward`, which presents peer `127.0.0.1`
        while the pod allowlists the node's Cilium internal address, so every
        byte-identity run — THE phase-1 criterion — became a 401.

        Serving it is safe because the header it sent is IGNORED: the request is
        booked against the peer, so the caller can only ever spend its own
        budget. Distrust is expressed by disbelieving the caller, not by hanging
        up on them.

        🔴 The replacement is NOT weaker than what it replaces. The property the
        old test was reaching for — a forged header must never name a third
        party — is pinned by
        `test_THE_DEFECT_a_forged_header_from_an_untrusted_peer_locks_out_a_victim`
        and by the `ip=` assertions above, both of which are still RED at base.
        """
        with running_subprocess(
            store,
            token_file,
            trusted_proxies=f"{TRUSTED_PEER}/32",
            host=TRUSTED_PEER,
        ) as (base, proc):
            # 🔴 THIS SITE IS WHY `drain_output` EXISTS. It used to terminate on
            # the client's return and read the corpse's stdout, so a slow handler
            # lost the line and `[...][-1]` raised `IndexError: list index out of
            # range` — an index into an empty list, not a useful assertion.
            # MEASURED 2026-08-23T00:37Z on `devrc-ci-jxf5j`, in the nix sandbox, on a
            # tree whose only change was to an unrelated test, while the same
            # commit passed a local `nix build`.
            out = drain_output(proc)
            code = fetch_from(
                UNTRUSTED_PEER,
                base,
                f"/api/v1/recall/{SCOPE}",
                token=GOOD_TOKEN,
                client_ip=SPOOF_IP,
            )
            # `await_audit` RAISES if the line never arrives, so `[-1]` below
            # cannot be the IndexError this whole change exists to remove.
            lines = await_audit(out, 1)
        assert code == 200, code
        line = lines[-1]
        assert f"ip={UNTRUSTED_PEER}" in line, line
        assert f"ip={SPOOF_IP}" not in line, line
        assert "peer=untrusted" in line, line
        # 🔴 AND IT IS NOT SPELLED AS AN AUTH FAILURE. The earlier shape emitted
        # `status=untrusted-peer auth=fail`, which put every port-forward into
        # the Loki auth-fail alert and trained the operator to ignore it.
        assert "auth=ok" in line, line
        assert "auth=fail" not in line, line
        assert "status=untrusted-peer" not in line, line

    def test_healthz_is_answered_for_an_UNTRUSTED_peer_so_the_kubelet_probe_lives(
        self, store: Path, token_file: Path
    ):
        """🔴 THE FAILURE MODE THAT GETS A SECURITY GUARD DELETED. The kubelet
        probes from the node, sends no `CF-Connecting-IP`, and is not the
        gateway — so if the peer gate ran before `/healthz` the pod would never
        become Ready and the guard would be reverted within the hour.

        `running_subprocess` already waits on `/healthz` to decide the server is
        up, so an ordinary spawn would prove this by accident. This one spawns
        with an allowlist that excludes loopback ENTIRELY and asserts the body.
        """
        with running_subprocess(
            store, token_file, trusted_proxies=NOT_LOOPBACK_PROXY
        ) as (base, _proc):
            code, _headers, body = fetch(f"{base}{'/healthz'}", client_ip=None)
        assert code == 200
        assert body == b"ok\n"

    def test_the_process_REFUSES_TO_START_with_the_variable_unset(
        self, store: Path, token_file: Path
    ):
        """Exit 78, on stderr, naming the variable. At base the process starts
        happily and serves — which is the whole reason this must not default.
        """
        proc = subprocess.run(
            [
                sys.executable,
                str(SERVER_PATH),
                "--store",
                str(store),
                "--host",
                "127.0.0.1",
                "--port",
                str(_free_port()),
                "--token-file",
                str(token_file),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=_child_env(None),
        )
        assert proc.returncode == 78, (proc.returncode, proc.stdout, proc.stderr)
        assert "SUBSYSTEM_STORE_TRUSTED_PROXIES" in proc.stderr
        assert "no trusted proxies" in proc.stderr

    def test_the_process_REFUSES_TO_START_on_a_DEFAULT_ROUTE(
        self, store: Path, token_file: Path
    ):
        """`0.0.0.0/0` is the pre-fix behaviour spelled as configuration, and it
        is the value an operator reaches for at 2am. Requiring the variable to
        be SET does not catch it; only refusing the value does.
        """
        proc = subprocess.run(
            [
                sys.executable,
                str(SERVER_PATH),
                "--store",
                str(store),
                "--host",
                "127.0.0.1",
                "--port",
                str(_free_port()),
                "--token-file",
                str(token_file),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=_child_env("0.0.0.0/0"),
        )
        assert proc.returncode == 78, (proc.returncode, proc.stdout, proc.stderr)
        assert "trusts every peer" in proc.stderr

    def test_the_startup_line_NAMES_the_trusted_proxies(
        self, store: Path, token_file: Path
    ):
        """"Which peers may set the client identity" must be readable out of a
        running pod. It is configuration, not a credential.
        """
        with running_subprocess(
            store, token_file, trusted_proxies=NOT_LOOPBACK_PROXY
        ) as (_base, proc):
            proc.terminate()
            stdout, _err = proc.communicate(timeout=HANG_TIMEOUT)
        assert f"trusted-proxies={NOT_LOOPBACK_PROXY}" in stdout, stdout


# =============================================================================
# THE SEAM GUARDS for the audit-line race that `drain_output`/`await_audit` close.
#
# 🔴 THESE WALK THE AST INTERPROCEDURALLY, AND THAT IS NOT GOLD-PLATING — an
# earlier, single-function version of this guard was walked FIVE ways in an
# adversarial audit, each verified against the real guard with a verbatim racy
# shape as the positive control:
#
#   E1  terminate/communicate moved into a module-level helper   -> passed
#   E2  the prefix read via a different module constant          -> passed
#   E3  proc.send_signal(SIGTERM) instead of terminate           -> passed
#   E4  _c = proc.communicate; _c()   (bound-method alias)       -> passed
#   E5  a racy function merely NAMED `_drain` (an exclusion)     -> passed
#
# E4 is the instructive one: binding the method makes `proc.communicate` an
# `Attribute` inside an `Assign`, never a `Call`, so a walker looking for calls
# never sees it. E5 is worse than a hole — the exclusion list it exploited was
# also DEAD CODE (removing it entirely left the guard green), so it bought
# nothing and granted a permanent bypass. It is gone; the sanctioned helpers are
# not excluded by NAME, they simply never both kill and read.
#
# A guard that reads as coverage while providing little is worse than none,
# because it stops the next person looking.
# =============================================================================

_KILLERS = ("terminate", "kill", "communicate", "send_signal", "wait")

# 🔴 THE ONE SANCTIONED KILLER, AND WHY THIS IS NOT E5's BYPASS IN A NEW COAT.
# `running_subprocess` terminates in its `finally` — that IS the design, and every
# correct call site delegates teardown to it. So its kills must not propagate to
# its callers, or the guard flags exactly the three tests that are RIGHT.
#
# The difference from the exclusion list this replaces: that one skipped functions
# by NAME, so any function called `_drain` inherited a permanent exemption it had
# not earned. This names the context manager whose whole contract is teardown, and
# `test_the_teardown_owner_really_is_one` below FAILS if the named function stops
# being a killer or stops being a context manager — so the entry cannot rot into a
# free pass for something that no longer does the job.
_TEARDOWN_OWNERS = frozenset({"running_subprocess"})


def _module_tree() -> ast.Module:
    return ast.parse(Path(__file__).read_text())


def _audit_prefix_names(tree: ast.Module) -> set[str]:
    """Every module-level name bound to a string that IS the audit prefix.

    Closes E2. Reading the prefix through a second constant is reading the
    prefix; the guard must not care which name you spell it with.
    """
    names = {"AUDIT_PREFIX"}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str) and node.value.value == AUDIT_PREFIX:
                names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


def _direct_kills(fn: ast.AST) -> set[str]:
    """Killer verbs reached directly in this function body.

    Counts a CALL (`proc.terminate()`) and also a bare ATTRIBUTE reference
    (`_c = proc.communicate`) — closing E4, where the alias is never a Call.
    """
    hits: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute) and node.attr in _KILLERS:
            hits.add(node.attr)
    return hits


def _direct_reads(fn: ast.AST, prefix_names: set[str]) -> bool:
    """Does this function body reach the audit records directly?"""
    for node in ast.walk(fn):
        if isinstance(node, ast.Constant) and node.value == AUDIT_PREFIX:
            return True
        if isinstance(node, ast.Name) and node.id in prefix_names:
            return True
        # `.audit` on the drained record, and the helper that returns it
        if isinstance(node, ast.Attribute) and node.attr == "audit":
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "await_audit":
            return True
    return False


def _called_names(fn: ast.AST) -> set[str]:
    return {
        n.func.id for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }


def _functions(tree: ast.Module) -> dict:
    return {
        n.name: n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _transitive(seed: dict, funcs: dict) -> dict:
    """Propagate a per-function property through the call graph to a fixed point.

    Closes E1 and E5: moving the terminate into a helper, or naming the helper
    something the guard used to skip, no longer hides it — the property follows
    the calls rather than the spelling.
    """
    prop = dict(seed)
    for _ in range(len(funcs) + 1):
        changed = False
        for name, node in funcs.items():
            if prop.get(name):
                continue
            if any(prop.get(c) for c in _called_names(node)):
                prop[name] = True
                changed = True
        if not changed:
            break
    return prop


def test_no_test_reads_an_AUDIT_LINE_from_a_process_it_just_terminated():
    """🔴 THE SEAM GUARD. The hazard is a RELATIONSHIP inside one test — reading
    audit records out of a stream while also being the thing that killed the
    process producing them. A client's return proves nothing about the lines it
    did not wait for — `_audit` precedes `_respond` for the request in hand and
    says nothing about any other handler thread — and terminating on it races
    the emission.

    🔴 WHAT IT DELIBERATELY PERMITS:
    `test_the_startup_line_NAMES_the_trusted_proxies` terminates and reads stdout
    too, but reads the STARTUP line — which `running_subprocess` has already
    synchronised on, because a `/healthz` ANSWER requires `serve_forever()` and
    the startup `print(..., flush=True)` runs before it. Verified in `server.py`,
    not assumed. It is permitted by the READ condition (it never touches the
    audit records), not by a name exclusion — so the permission cannot rot into
    a bypass the way E5's exclusion list did.

    🔴 WHAT IT STILL CANNOT SEE, stated so it is not read as more than it is: a
    racy read that never reaches the prefix, `.audit` or `await_audit` — slicing
    stdout positionally, or matching a substring of a record. Killing via a
    non-`_KILLERS` route (`os.kill(proc.pid, ...)`) is also unseen.
    """
    tree = _module_tree()
    funcs = _functions(tree)
    prefix_names = _audit_prefix_names(tree)

    kills_seed = {n: bool(_direct_kills(f)) for n, f in funcs.items()}
    reads_seed = {n: _direct_reads(f, prefix_names) for n, f in funcs.items()}

    # Positive control BEFORE the teardown owner is removed — the detectors must
    # be able to see the real thing, or every result below is a vacuous zero.
    assert reads_seed["await_audit"], "the read detector sees nothing — it is broken"
    assert kills_seed["running_subprocess"], "the kill detector sees nothing — it is broken"

    # Teardown owners neither kill (for propagation) nor pass killing to callers.
    graph = {n: f for n, f in funcs.items() if n not in _TEARDOWN_OWNERS}
    kills = _transitive({n: v for n, v in kills_seed.items() if n in graph}, graph)
    reads = _transitive({n: v for n, v in reads_seed.items() if n in graph}, graph)

    offenders = sorted(
        f"{n} (line {funcs[n].lineno}) kills via "
        f"{sorted(_direct_kills(funcs[n])) or 'a callee'}"
        for n in graph
        if kills.get(n) and reads.get(n) and n != "await_audit"
    )
    assert not offenders, (
        "these functions both terminate the server and read its audit records — "
        "the response does not imply the line was written. Use "
        "`drain_output(proc)` + `await_audit(out, n)` and leave teardown to "
        "`running_subprocess`:\n  " + "\n  ".join(offenders)
    )


def test_the_teardown_owner_really_is_one():
    """🔴 The entry in `_TEARDOWN_OWNERS` is an exemption, and an exemption that
    stops being earned is exactly how the previous version of this guard was
    walked (E5: a racy function merely NAMED `_drain` inherited a skip).

    So the exemption is checked rather than trusted: each named function must
    still (a) exist, (b) actually kill the process, and (c) be a context manager,
    which is what makes "teardown belongs to it" true. If someone empties its
    `finally`, or the name goes stale, this fails instead of silently widening
    the guard's blind spot.
    """
    tree = _module_tree()
    funcs = _functions(tree)

    for name in _TEARDOWN_OWNERS:
        assert name in funcs, f"_TEARDOWN_OWNERS names {name!r}, which does not exist"
        node = funcs[name]
        assert _direct_kills(node), (
            f"{name} is exempted as the teardown owner but no longer kills the "
            "process — the exemption is now a free pass for nothing"
        )
        decorators = {
            d.id if isinstance(d, ast.Name) else getattr(d, "attr", "")
            for d in node.decorator_list
        }
        assert "contextmanager" in decorators, (
            f"{name} is exempted as the teardown owner but is not a context "
            f"manager (decorators: {sorted(decorators)}), so callers are not "
            "actually delegating teardown to it"
        )


def test_every_audit_reading_test_goes_through_the_shared_helper():
    """The anti-vacuity half: the guard above passes trivially if the tests stop
    reading audit records altogether, so this fails when the coverage SHRINKS.

    🔴 IT COUNTS CALL SITES, NOT FUNCTION NAMES. The earlier version counted the
    names of functions containing a call, and an audit showed one site inside a
    nested `def` contributed TWO — so two real sites could satisfy a threshold of
    three. The count is now `drain_output(...)` call expressions.

    🔴 THE THRESHOLD IS EXERCISED BY ITS OWN MUTANT. A sweep that only ever
    deletes the helper drives the count to 0, which kills `>= 1`, `>= 2` and
    `>= 3` identically — so the threshold looks verified while a `>= 1` mutant
    survives and permits two of the three sites to regress. The companion test
    below removes exactly ONE site and requires this to go red.
    """
    assert _drain_output_call_sites() == 3, (
        f"expected exactly 3 `drain_output(...)` call sites, found "
        f"{_drain_output_call_sites()}. A reader was added or deleted; if that is "
        "intended, update this count AND check the guard above still has teeth."
    )


def _drain_output_call_sites(source: "str | None" = None) -> int:
    """`drain_output(...)` CALL expressions — not the functions containing them."""
    tree = ast.parse(source if source is not None else Path(__file__).read_text())
    return sum(
        1 for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "drain_output"
    )


def test_the_call_site_THRESHOLD_is_load_bearing_not_decorative():
    """🔴 The mutant the threshold's own sweep cannot supply.

    Deleting the helper everywhere drives the count to 0 and kills every
    threshold equally. This removes ONE site from a COPY of the source and
    asserts the count actually moves to 2 — the case that separates `>= 3` from
    `>= 1`, and the reason the assertion above is `== 3` rather than a floor.
    """
    src = Path(__file__).read_text()
    assert _drain_output_call_sites(src) == 3, "fixture drift: the real count moved"

    one_removed = src.replace("out = drain_output(proc)", "out = None  # mutant", 1)
    assert one_removed != src, "the mutation did not apply — this test is vacuous"
    assert _drain_output_call_sites(one_removed) == 2, (
        "removing one call site did not move the count, so the threshold cannot "
        "distinguish three readers from two"
    )


# 🔴 THE SAME TWO-PART TREATMENT FOR THE HANG/DRAIN SPLIT, BECAUSE IT WAS PROSE
# ONLY — and the prose was already wrong when it landed. `HANG_TIMEOUT`'s comment
# claims "ONE bound for every … wait in this module", but the commit that wrote
# it left TWO `wait_closed(15.0)` sites overriding the new default, so the file
# said one thing and did another until an audit read it. The rule needs teeth,
# not a better sentence.


# The POSITIONAL INDEX of the timeout argument, per callee. A first draft read
# every positional arg as a bound and flagged 81 false stragglers: `await_audit`
# takes `(out, n, timeout)`, so its literal `n` — the expected line COUNT — looks
# exactly like a literal bound. An over-broad finder is not a strict guard, it is
# a guard nobody can leave green.
_HANG_DETECTOR_BOUND_ARG = {"wait_closed": 0, "await_audit": 2}


def _literal_bound_hang_detectors(source: "str | None" = None) -> "list[tuple[str, int, object]]":
    """Hang-detector waits bound by a NUMERIC LITERAL instead of `HANG_TIMEOUT`.

    AST-based, so the `wait_closed(15.0)` living inside this file's own
    AST-fixture STRING is correctly invisible — it is not a call, and a textual
    grep would report it as a straggler forever.

    Deliberately does NOT look at `settimeout`/`create_connection`: those are
    DRAIN bounds, pinned separately below. Same spelling, opposite meaning.
    """
    src = source if source is not None else Path(__file__).read_text()
    bad = []
    for n in ast.walk(ast.parse(src)):
        if not isinstance(n, ast.Call):
            continue
        name = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
        if name not in _HANG_DETECTOR_BOUND_ARG:
            continue
        idx = _HANG_DETECTOR_BOUND_ARG[name]
        bounds = [n.args[idx]] if len(n.args) > idx else []
        bounds += [kw.value for kw in n.keywords if kw.arg == "timeout"]
        for arg in bounds:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)):
                bad.append((name, n.lineno, arg.value))
    return bad


def test_no_hang_detector_is_still_bound_by_a_LITERAL():
    """🔴 Every hang-detector takes the shared bound, or none at all.

    A literal here is not a style nit: it silently pins one site to the OLD
    value while every other site moves, which is exactly how two `wait_closed`
    calls kept a 15 s bound through a change whose whole purpose was to raise it.
    """
    stragglers = _literal_bound_hang_detectors()
    assert not stragglers, (
        "these hang-detector waits are bound by a literal instead of "
        f"HANG_TIMEOUT: {stragglers}. Pass HANG_TIMEOUT and interpolate it into "
        "the message ({HANG_TIMEOUT:g}) so the sentence cannot disagree with the "
        "bound."
    )


def test_the_literal_bound_DETECTOR_can_actually_see_one():
    """🔴 The positive control. A guard whose finder is broken reports a clean
    zero forever, and "no stragglers" would then mean "the walk matched nothing".
    """
    src = Path(__file__).read_text()
    assert _literal_bound_hang_detectors(src) == [], "fixture drift: a straggler is already present"

    mutant = src.replace("out.wait_closed(HANG_TIMEOUT)", "out.wait_closed(15.0)", 1)
    assert mutant != src, "the mutation did not apply — this test is vacuous"
    found = _literal_bound_hang_detectors(mutant)
    assert len(found) == 1 and found[0][0] == "wait_closed" and found[0][2] == 15.0, (
        f"re-introducing one literal bound did not surface it: {found!r}"
    )


def _drain_bounds(source: "str | None" = None) -> "list[tuple[int, str]]":
    """Every `sock.settimeout(<x>)` DRAIN bound, as (lineno, rendered arg).

    🔴 Returns ALL of them, literal or not. The introducing commit claimed
    "drain bounds ... Verified still at 5 s" off a grep for the literal string
    `settimeout(5)`, which finds 3 — there are EIGHT settimeout call sites, one
    of them `settimeout(4)` and four computed from expressions. That grep could
    not have seen any of those, so the verification was a claim about the
    pattern, not about the file. This walks the AST instead.

    🔴 The count in the previous sentence said NINE, and was itself wrong — the
    third miscounted self-report in this ladder, in the artifact written to stop
    them. It came from counting `grep -n 'settimeout('` OUTPUT LINES, one of
    which is this very docstring mentioning the name. A grep counts MENTIONS; an
    AST walk counts CALLS. Re-derive with `_drain_bounds()` rather than trusting
    any number written here, this one included.
    """
    src = source if source is not None else Path(__file__).read_text()
    return [
        (n.lineno, ast.unparse(n.args[0]))
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Call)
        and getattr(n.func, "attr", None) == "settimeout"
        and n.args
    ]


def test_the_drain_bounds_were_NOT_swept_into_the_hang_bound():
    """🔴 Pins the RELATIONSHIP, not the numbers.

    The obvious tidy-up — one constant for every timeout in this file — is the
    defect: a drain is a `recv`-until-quiet loop terminator, so raising one adds
    its full value to every run of a PASSING suite, where a hang bound only ever
    costs the latency of a real failure.

    Asserting the exact multiset was the first draft and it was wrong twice over:
    it went red on a `settimeout(4)` that is perfectly correct, and it would say
    nothing at all about the five expression-valued sites. What must hold is that
    no drain is bound by the hang constant, and that literal drains stay small.
    """
    bounds = _drain_bounds()
    assert len(bounds) >= 4, (
        f"expected at least 4 settimeout sites, found {len(bounds)} — the finder "
        "is matching less than it used to, so a clean result here means nothing"
    )
    swept = [(ln, a) for ln, a in bounds if "HANG_TIMEOUT" in a]
    assert not swept, (
        f"these DRAIN bounds were bound to the hang constant: {swept}. A drain at "
        f"{HANG_TIMEOUT:g}s adds that to every passing run; it is not a detector."
    )
    # 🔴 `None` FIRST, and as its own arm. It is the stdlib's "block forever",
    # so it is the worst value a drain can take — and it slipped a fully green
    # run of this guard's first draft, because `ast.unparse` renders it as the
    # string "None", which is not `.isdigit()`, so the size arm below never saw
    # it. The module-level assert rejects exactly this value for the hang bound;
    # a drain must not be able to take what the detector refuses.
    blocking = [(ln, a) for ln, a in bounds if a == "None"]
    assert not blocking, (
        f"these drain bounds are `None` — the stdlib's block-forever: {blocking}. "
        "A recv-until-quiet loop with no bound never terminates."
    )
    big = [(ln, a) for ln, a in bounds
           if a.lstrip("-").replace(".", "", 1).isdigit() and float(a) > 10]
    assert not big, (
        f"these literal drain bounds exceed 10s: {big}. A recv-until-quiet "
        "terminator that large is a stall, not a bound."
    )


def test_the_drain_guard_catches_the_sweep_it_exists_to_catch():
    """🔴 The mutant that IS the hazard: someone "tidies up" a drain onto the
    shared constant. Without this, the guard above is a green nobody has watched
    go red — and its own first draft was green for the wrong reason.
    """
    src = Path(__file__).read_text()
    mutant = src.replace("sock.settimeout(5)", "sock.settimeout(HANG_TIMEOUT)", 1)
    assert mutant != src, "the mutation did not apply — this test is vacuous"
    swept = [(ln, a) for ln, a in _drain_bounds(mutant) if "HANG_TIMEOUT" in a]
    assert len(swept) == 1, (
        f"sweeping one drain onto HANG_TIMEOUT was not detected: {swept!r}"
    )

    # 🔴 The mutant that SURVIVED this guard's first draft, kept as a permanent
    # control. `None` renders as a non-numeric string, so the size arm cannot see
    # it — only the dedicated arm can, and without this nobody would notice that
    # arm being deleted.
    blocking_mutant = src.replace("sock.settimeout(4)", "sock.settimeout(None)", 1)
    assert blocking_mutant != src, "the None mutation did not apply — vacuous"
    blocking = [(ln, a) for ln, a in _drain_bounds(blocking_mutant) if a == "None"]
    assert len(blocking) == 1, (
        f"a `settimeout(None)` drain — block forever — was not detected: {blocking!r}"
    )


def _sampled_event_preconditions(source: "str | None" = None) -> "list[tuple[int, str]]":
    """Every `assert <event>.is_set()` — a PRECONDITION that samples a race.

    🔴 THE SHAPE, NOT THE SPELLING. `is_set()` is not banned: `await_audit`
    calls it twice, inside a deadline-bounded polling loop, which is correct and
    must stay legal. What cannot be legal is using it as the TEST OF AN ASSERT,
    because that reads a background thread's progress at one instant and fails
    the build if the thread has not got there yet — a claim about the scheduler
    wearing the words of a claim about the code.

    That is exactly how `TestAHungRoundTripSAYSWhichSideBlocked` failed in CI on
    PRs whose diff could not reach it: within `CLIENT_BOUND` (0.25 s) the server
    had to accept, spawn, parse, authenticate, meter, resolve and read before
    reaching the stall site. Idle dev host: a few ms, so it passed 5/5 even
    pinned to one core under 27 CPU hogs. Contended CI node: not always.

    The fix is `.wait(HANG_TIMEOUT)`, which is strictly stronger — it still
    fails when the site is genuinely never reached (proved by mutation), and it
    stops failing when the site is merely reached late.
    """
    src = source if source is not None else Path(__file__).read_text()
    bad = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Assert):
            continue
        test = node.test
        if (
            isinstance(test, ast.Call)
            and getattr(test.func, "attr", None) == "is_set"
        ):
            bad.append((node.lineno, ast.unparse(test)))
    return bad


def test_no_precondition_SAMPLES_an_event_instead_of_WAITING_for_it():
    """🔴 A precondition that samples a race is a flake with a good error message.

    It blocks every PR exactly as a real defect would, while saying the code is
    wrong — the most expensive possible failure, because the accusation is
    specific and false.
    """
    sampled = _sampled_event_preconditions()
    assert not sampled, (
        f"these assertions SAMPLE an Event instead of waiting on it: {sampled}. "
        "Use `assert ev.wait(HANG_TIMEOUT)` — it still fails when the event is "
        "never set, and stops failing when it is merely set late. See "
        "`_hang_and_report`."
    )


def test_the_sampled_event_DETECTOR_can_actually_see_one():
    """🔴 The positive control. Without it a clean zero above is
    indistinguishable from a walk that matches nothing — and this detector is
    narrow by design (asserts only), so "it found none" is very easy to get for
    the wrong reason.
    """
    src = Path(__file__).read_text()
    assert _sampled_event_preconditions(src) == [], "fixture drift: one is already present"

    # Re-introduce exactly the form that was removed.
    #
    # 🔴 THE ANCHOR IS BUILT BY CONCATENATION, NOT WRITTEN AS ONE LITERAL, and
    # that is load-bearing rather than style. Spelled whole, this string would
    # appear TWICE in the file — once here and once in the code it targets — and
    # `replace(..., 1)` would hit THIS line first. It did: the mutation applied
    # (so `mutant != src` passed), the guard reported zero, and the test failed
    # with an empty list while the real code was untouched. Split like this, the
    # file contains exactly one copy, which the assertion below pins.
    target = "            armed = stalled" + ".wait(HANG_TIMEOUT)"
    assert src.count(target) == 1, (
        f"the anchor is no longer unique ({src.count(target)} copies) — a "
        "count=1 replace would mutate the wrong one and this control would pass "
        "while testing nothing"
    )
    mutant = src.replace(target, "            assert stalled" + ".is_set()", 1)
    assert mutant != src, "the mutation did not apply — this test is vacuous"
    found = _sampled_event_preconditions(mutant)
    assert len(found) == 1 and found[0][1] == "stalled.is_set()", (
        f"re-introducing the sampled precondition did not surface it: {found!r}"
    )

    # 🔴 AND THE NEGATIVE HALF: the legal uses must NOT be flagged, or the guard
    # is unleaveable and someone will delete it. `await_audit` calls `is_set()`
    # in a loop and in an assignment; neither is an assert test.
    assert "closed.is_set()" in src, "fixture drift: await_audit no longer calls is_set"
    assert not [f for f in _sampled_event_preconditions(src) if "closed" in f[1]], (
        "the legal polling-loop use of is_set() was flagged — this guard would "
        "be permanently red and would train people to delete it"
    )


# 🔴 THE SAME TWO-PART TREATMENT FOR `settle`, BECAUSE ITS RULE WAS PROSE ONLY.
# `settle`'s docstring says CALL IT AFTER THE `with running(...)` BLOCK, NEVER
# INSIDE, and 15 sites were converted to it in one commit — with nothing pinning
# either fact. This file's own history says what a prose rule is worth here:
# `await_audit` was documented and FORTY sites ignored it, and the commit that
# fixed that prescribed a recipe (`out.wait_closed()` on an `AuditLog`) that
# cannot exist, so nine more sites followed a rule that could not work.
#
# The failure this closes is silent and is a REVERSION, not a new mistake:
# rewrite any `lines = settle(audit, 1)` back to
# `lines = await_audit(audit, 1); assert len(lines) == 1` and that site's
# ceiling re-narrows to the synchronous case — which is bit-for-bit the defect
# the `settle` commit repaired — while every other guard in this file stays
# green, because that form performs no raw read and so the raw-read ban never
# fires. A count is the cheapest thing that notices.
def _settle_call_sites(source: "str | None" = None) -> int:
    """`settle(...)` CALL expressions — not the functions containing them."""
    tree = ast.parse(source if source is not None else Path(__file__).read_text())
    return sum(
        1 for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "settle"
    )


def _settle_calls_inside_a_with_block(source: "str | None" = None) -> "list[int]":
    """Lines where a `settle(...)` call sits INSIDE a `with` body. The placement
    half of the rule, which no count can see.
    """
    tree = ast.parse(source if source is not None else Path(__file__).read_text())
    found = set()
    for block in ast.walk(tree):
        if not isinstance(block, (ast.With, ast.AsyncWith)):
            continue
        for stmt in block.body:
            for n in ast.walk(stmt):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                        and n.func.id == "settle":
                    found.add(n.lineno)
    return sorted(found)


def test_every_exact_count_site_goes_through_settle():
    """The anti-shrink half: this fails when the ceiling coverage SHRINKS.

    A site that gives up its ceiling — reverting to `await_audit` plus a
    `len(...) == n` on the snapshot — is invisible to every other guard here.
    Pinning the number is what makes that revert a red run instead of a silent
    narrowing back to the synchronous-only ceiling.

    🔴 WHAT IT CANNOT SEE, AND THE NAME OVERCLAIMS. This is a COUNT of
    `settle(...)` call expressions. "Every exact-count site goes through
    `settle`" is a claim about a SET, and a count cannot deliver it — the census
    never enumerates the exact-count sites, so it cannot know whether one of
    them is missing. Two shapes were APPLIED and SURVIVED — measured against
    all five census/placement guards, not reasoned about — and they are named
    here rather than left for the next reader to rediscover:

      (1) HELPER INDIRECTION. Route one site through a one-line wrapper
          (`def _w(o): return settle(o, 4)`, called where the direct call was).
          The `settle(...)` expression still exists, so the count stays 15 and
          this stays green; and because the call AT THE SITE is now spelled
          `_w(...)`, `test_settle_is_called_AFTER_the_running_block_never_inside`
          cannot see it either. MEASURED: indirection alone, 0/5 guards red —
          and the state that actually LOSES the ceiling, indirection plus the
          call moved INSIDE the `with` body where teardown has not run, also
          0/5. The stake is not hypothetical.
      (2) A NEW SITE WRITTEN THE OLD WAY. Add a test whose exact-count assertion
          is spelled `lines = await_audit(audit, n)` + `assert len(lines) == n`.
          It is a snapshot ceiling, it is exactly the defect `settle` exists to
          repair, and the count is unchanged at 15. MEASURED: 0/5 red. What the
          census catches is the REVERSION of an EXISTING site — the failure it
          was built against — never the ARRIVAL of a new one in the old shape,
          despite what the name says.
          🔴 And it is invisible only in the IN-PROCESS shape. A new site that
          also calls `drain_output(...)` IS caught — but by the sibling census
          `test_every_audit_reading_test_goes_through_the_shared_helper` moving
          3 -> 4, i.e. for a reason that has nothing to do with ceilings. Do not
          read that red as coverage: it is a guard dying for the wrong reason,
          and it disappears the moment the new site uses `running(...)`.

    Both gaps are PRECEDENT, not new: `_drain_output_call_sites` and
    `test_every_audit_reading_test_goes_through_the_shared_helper` have the
    identical name-versus-count shape and shipped that way. Closing (1) needs
    the census routed through `_transitive` (which propagates a BOOLEAN through
    the call graph and so can serve the placement guard, but cannot produce a
    count); closing (2) needs a detector for the old spelling. Both are new
    guards with their own mutation matrices, and neither belongs in a change
    that inherits this PR's.
    """
    assert _settle_call_sites() == 21, (
        f"expected exactly 21 `settle(...)` call sites, found "
        f"{_settle_call_sites()}. A ceiling was added or given up; if that is "
        "intended, update this count AND say in the commit which property the "
        "file no longer asserts."
    )


def test_the_settle_call_site_THRESHOLD_is_load_bearing_not_decorative():
    """🔴 The mutant the threshold's own sweep cannot supply, for `settle`.

    Deleting the helper everywhere drives the count to 0 and kills `>= 1`,
    `>= 20` and `== 21` identically. This removes ONE site from a COPY of the
    source and requires the count to actually move to 20 — the case that
    separates a real ceiling census from a floor that twenty-one sites could
    satisfy with one.
    """
    src = Path(__file__).read_text()
    assert _settle_call_sites(src) == 21, "fixture drift: the real count moved"

    # A literal that appears exactly once as real code. (It also appears in this
    # line, later in the file; `count=1` takes the earlier, real occurrence, and
    # the assertions below fail loudly if it ever stops doing so.)
    one_removed = src.replace("lines = settle(audit, 21)",
                              "lines = []  # mutant", 1)
    assert one_removed != src, "the mutation did not apply — this test is vacuous"
    assert _settle_call_sites(one_removed) == 20, (
        "removing one call site did not move the count, so the threshold cannot "
        "distinguish twenty-one ceilings from twenty"
    )


def test_settle_is_called_AFTER_the_running_block_never_inside():
    """The placement half of `settle`'s rule, which the count cannot see.

    Inside the block the grace window watches a server that is still up, so the
    one thing the ceiling exists for — a record emitted BY teardown — cannot
    land where it can be seen. A site moved inside keeps the call, keeps the
    count, and quietly stops asserting the property.
    """
    inside = _settle_calls_inside_a_with_block()
    assert inside == [], (
        "these `settle(...)` calls are INSIDE a `with` body, where teardown has "
        f"not run yet and the ceiling is not being tested: lines {inside}"
    )

    # Positive control: the detector must be able to SEE a misplaced call, or
    # the empty list above is a fact about the walker and nothing else.
    misplaced = textwrap.dedent("""
        def test_x(store):
            with running(store) as (base, audit):
                await_audit(audit, 1)
                lines = settle(audit, 1)
            assert lines
    """)
    flagged = _settle_calls_inside_a_with_block(misplaced)
    assert len(flagged) == 1, (
        "the placement detector cannot see a `settle(...)` inside a `with` body: "
        f"{flagged}"
    )
    assert misplaced.splitlines()[flagged[0] - 1].strip() == "lines = settle(audit, 1)", (
        "the placement detector flagged a line other than the misplaced call, so "
        "its line numbers cannot be trusted to point anywhere"
    )
    correct = textwrap.dedent("""
        def test_x(store):
            with running(store) as (base, audit):
                await_audit(audit, 1)
            lines = settle(audit, 1)
            assert lines
    """)
    assert _settle_calls_inside_a_with_block(correct) == [], (
        "the placement detector flags a correctly-placed call, so its zero above "
        "means nothing"
    )


def _raw_audit_reads(fn: ast.AST) -> "list[tuple[int, str]]":
    """`(line, owner)` per read of the audit records that skips the helper.

    A read is `audit` in a Load context or any `.audit` attribute; the argument
    positions of `await_audit(...)` and `settle(...)` are subtracted, since
    naming the record to hand it to a waiter is not reading it.

    🔴 `owner` IS WHAT MAKES THE BARRIER BELOW CHECKABLE AGAINST THE SAME
    STREAM. `out.audit` is owned by `out`, a bare `audit[0]` by `audit`, and
    anything whose base is not a plain name (`f().audit`) owns `""`, which no
    barrier can match. Without it, `proc_out.wait_closed()` — a barrier on a
    DIFFERENT stream, standing next to a raw read of `out.audit` — bought that
    read a pass. An exemption spelled by waiting on the wrong object is a free
    pass wearing the costume of diligence.
    """
    raw, in_arg = set(), set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id == "audit" \
                and isinstance(node.ctx, ast.Load):
            raw.add((node.lineno, "audit"))
        elif isinstance(node, ast.Attribute) and node.attr == "audit":
            raw.add((node.lineno,
                     node.value.id if isinstance(node.value, ast.Name) else ""))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in ("await_audit", "settle"):
            for arg in node.args:
                for sub in ast.walk(arg):
                    if isinstance(sub, (ast.Name, ast.Attribute)):
                        in_arg.add(sub.lineno)
    return sorted((ln, owner) for ln, owner in raw if ln not in in_arg)


def _eof_barriers(fn: ast.AST) -> "list[tuple[int, str]]":
    """`(line, receiver)` per UNCONDITIONAL wait for that stream's EOF in `fn`.

    🔴 THE POINT IS "UNCONDITIONALLY", AND IT IS WHY THIS IS NOT AN
    `ast.walk(fn)`. The guard below used to accept a `wait_closed` MENTION
    anywhere in the function — unordered, and inside whatever branch. MEASURED:
    a reintroduced raw `audit[0]` was caught, and the SAME raw read alongside a
    dead `if False: audit.wait_closed()` SURVIVED. One unreachable line bought a
    blanket pass, which is precisely the rot the guard's own docstring claimed
    it was immune to.

    🔴 AND CLOSING THAT HOLE FOR `if` LEFT IT OPEN SPELLED AS A `with`. This
    used to descend into `with` bodies, justified by "`with running(...)` is how
    every test in this file is shaped and its body always runs". That sentence
    is false for any context manager that suppresses or diverts, and both
    spellings are one import away: MEASURED against the shipped predicate, a raw
    `out.audit[0]` was EXEMPTED by a barrier sitting inside
    `with contextlib.suppress(AttributeError):` and again by one inside
    `with pytest.raises(AttributeError):` — the same free pass as `if False:`,
    two characters of diff away from it. `return` had the same hole in the time
    axis: a barrier after one is unreachable and was counted anyway.

    So NOTHING nested counts, and nothing after a terminator counts: only the
    function's own top-level statement sequence, up to the first
    `return`/`raise`/`break`/`continue`. A barrier nested in a `with`, an `if`,
    a `try` or a loop is not rejected as hostile — it is simply not counted, and
    the site is asked to hoist it, which for the one permitted site in this file
    is where the barrier already is.

    Only a bare `x.wait_closed()` statement, `y = x.wait_closed()`, or
    `assert x.wait_closed()` qualifies, and `x` must be a plain name so it can
    be matched against the read's owner: a call buried in a lambda, a
    comprehension or an argument list is a mention again, one layer down.

    🔴 `assert x.wait_closed()` IS ACCEPTED, AND LEAVING IT OUT WAS A REAL
    DEFECT, NOT A GAP. `wait_closed` RETURNS whether EOF was reached, and
    discarding that answer is the exact bug `settle` was fixed for: on a timeout
    the discarded form degrades silently into a bare snapshot with no ceiling at
    all, and then reports "the closed stream holds ..." about a stream that was
    not closed. Accepting only `ast.Expr`/`ast.Assign` made the repaired form
    STRUCTURALLY FORBIDDEN at every test site — this helper taught "assert the
    answer" while its own predicate required the discarded spelling — so the
    file's one hand-rolled copy of that branch was pinned at the pre-fix shape
    by the guard that was supposed to be protecting it. MEASURED against the
    shipped predicate before this arm existed: rewriting the permitted site's
    barrier to `assert out.wait_closed()` emptied `_eof_barriers` and FLAGGED
    both of its reads, under the message "these tests index the LIVE audit
    list" — which is not what had happened.

    🔴 AND ONLY WHEN THE CALL IS THE ASSERT'S ENTIRE `test` EXPRESSION.
    `assert not x.wait_closed()` asserts the NEGATION — a stream that did NOT
    reach EOF — and `assert x.wait_closed() is False` says the same thing one
    node further out; accepting either would exempt a raw read behind an
    assertion that the ceiling does not exist. So a `UnaryOp` or a `Compare`
    wrapper is not counted, deliberately: the truthy comparison spellings are
    unused in this file and cannot be separated from their negations without a
    whitelist of operators and literals, and the cost of refusing them is that a
    site writes the plain form. Both spellings are controls below. (`assert`
    also implies `-O` strips the barrier; nothing here runs under `-O`, and a
    stripped `assert` removes the wait rather than the read, which fails loudly.)
    """
    found: "list[tuple[int, str]]" = []
    for stmt in fn.body:
        if isinstance(stmt, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
            break                       # nothing after this statement runs
        if isinstance(stmt, (ast.Expr, ast.Assign)):
            value = stmt.value
        elif isinstance(stmt, ast.Assert):
            value = stmt.test           # `assert x.wait_closed()` — see above
        else:
            value = None
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) \
                and value.func.attr == "wait_closed" \
                and isinstance(value.func.value, ast.Name):
            found.append((value.lineno, value.func.value.id))
    return sorted(found)


def _unguarded_audit_reads(fn: ast.AST) -> "list[int]":
    """Raw audit reads in `fn` that no EOF barrier ON THAT STREAM precedes.

    ONE predicate: the guard below and its own controls both need this answer,
    and an open-coded second copy is how the two drift into disagreeing about
    what the guard means.
    """
    barriers = _eof_barriers(fn)
    return sorted({
        line for line, owner in _raw_audit_reads(fn)
        if not any(b < line and receiver == owner for b, receiver in barriers)
    })


def test_no_test_INDEXES_a_live_audit_list():
    """🔴 THE SWEEP, MADE DURABLE. `await_audit` existed and was documented, and
    FORTY test functions still read the live list directly — the helper's own
    docstring names the hazard and those sites were simply never converted.

    One of them was observed failing: `test_a_LOCKED_OUT_response_is_BYTE_
    IDENTICAL_to_an_ordinary_401` asserted on `audit[-1]` and got the PREVIOUS
    request's `status=unauthorized`. `_respond` ran before `_audit` then, and
    `ThreadingHTTPServer` uses DAEMON threads — which `socketserver._Threads`
    refuses to track — so `server_close()` joins nothing: neither `fetch`
    returning NOR the `with` block exiting proves the last handler has appended.
    The first half of that is fixed (`_audit` is emitted first now); the DAEMON
    half is not, and a site with a second request in flight has no ordering
    promise at all, so the ban stands.

    An unknown number of tests carrying a known race is worse than the one that
    was caught, so the count is pinned here rather than left to the next reader.

    🔴 THE ONE PERMITTED SITE IS PERMITTED STRUCTURALLY, NOT BY NAME.
    `test_the_STDOUT_audit_stream_names_the_matched_fingerprint` re-reads
    `out.audit` deliberately, to assert a CEILING ("exactly 3, ever") that a
    snapshot cannot see — and it is sound only because it calls
    `out.wait_closed()` first, so the stream has a real EOF behind it.

    🔴 AND "STRUCTURALLY" NOW MEANS WHAT IT SAYS — IT DID NOT. This paragraph
    used to end "Delete the `wait_closed()` and this fails; it cannot rot into a
    free pass the way a name exclusion would", and that second clause was FALSE.
    The condition was `any(n.attr == "wait_closed" for n in ast.walk(fn))`: a
    MENTION, anywhere in the function, in any branch, in any order relative to
    the read. MEASURED: a reintroduced raw `audit[0]` was KILLED, and the same
    read plus a dead `if False: audit.wait_closed()` SURVIVED — one unreachable
    line, blanket pass, and the detector was demonstrably sensitive otherwise.
    The escape hatch was pure rot channel: `wait_closed` is not even a method on
    `AuditLog`, so no in-process test could ever have called it honestly.

    🔴 AND THE FIRST REPAIR CLOSED THE HOLE FOR `if` AND LEFT IT OPEN FOR
    `with`. `_eof_barriers` descended into `with` bodies on the claim that such
    a body "always runs"; `contextlib` and `pytest` are both one line away, and
    MEASURED against that shipped predicate a barrier inside
    `with contextlib.suppress(AttributeError):` or `with pytest.raises(...):`
    exempted a raw read exactly as `if False:` had. Two more of the same family
    went with it: a barrier after a `return` (unreachable, counted anyway) and a
    barrier on a DIFFERENT object (`proc_out.wait_closed()` guarding a read of
    `out.audit`). All four are controls below.

    🔴 A NOTE ON WHY THE `if False:` CONTROL IS NOW BOUND TO `out`. Spelled
    against `audit`, the barrier line `audit.wait_closed()` is itself a raw
    `audit` Name-Load read, so the control passed partly because the dead line
    self-flagged — protection by accident, which evaporates the moment a site
    binds its stream to `out` the way `Drained` sites do. The control below is
    written both ways for that reason.

    🔴 AND THE FOURTH ROUND WAS THE OPPOSITE ERROR — THE EXEMPTION WAS TOO
    NARROW, AND THAT IS ALSO A DEFECT. `_eof_barriers` accepted only `ast.Expr`
    and `ast.Assign`, so `assert x.wait_closed()` — the form the `settle` fix
    adopted precisely BECAUSE the discarded answer degrades in silence on a
    timeout — was invisible to it. MEASURED against the shipped predicate:
    rewriting the permitted site's barrier to `assert out.wait_closed()` emptied
    its barrier list and FLAGGED both of its reads as though they were racy,
    under this test's own "these tests index the LIVE audit list" message. So
    the guard did not merely fail to recognise the repaired form, it FORBADE it,
    and the one hand-rolled copy of `settle`'s branch in this file stayed pinned
    at the pre-fix shape for exactly that reason. A guard that requires the
    defect is worse than one that misses it.

    The condition is now `_eof_barriers`: an UNCONDITIONAL `x.wait_closed()` —
    discarded, assigned, or ASSERTED — in the function's OWN top-level statement
    sequence, before any terminator, LEXICALLY BEFORE the read, and on the SAME
    object the read names. Every clause of that sentence has a control below
    that separates it from its negation, so the sentence is machine-checked for
    the shapes enumerated there and is not a claim about code nobody re-read.

    🔴 WHAT IT CANNOT SEE: a read through an alias (`a = audit; a[0]`), a list
    passed into a helper, a differently-named binding, or a barrier whose
    receiver is a plain name that has been REBOUND between the barrier and the
    read. It is a ledger of the shapes that actually bit plus their nearest
    siblings, not a proof that no racy read exists.

    🔴 AND WHAT IT DELIBERATELY REFUSES, which is the other direction and is
    stated separately because it costs a site a rewrite rather than hiding a
    race: `assert not x.wait_closed()` and `assert x.wait_closed() is False`
    assert that EOF was NOT reached, and the truthy comparison spellings cannot
    be told from their negations without a whitelist of operators and literals.
    Only an `assert` whose test IS the call counts. Both refusals are controls
    below, so this is pinned rather than merely intended.
    """
    tree = ast.parse(Path(__file__).read_text())
    offenders = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not fn.name.startswith("test"):
            continue
        unguarded = _unguarded_audit_reads(fn)
        if unguarded:
            offenders.append(f"{fn.name} (line {fn.lineno}) reads at {unguarded}")
    assert not offenders, (
        "these tests index the LIVE audit list: the handler threads are never "
        "joined and a request the client is not holding may not have appended "
        "yet. Use `lines = await_audit(audit, n)` and index `lines`, and "
        "`settle(audit, n)` after the `with` block for an exact count:\n  "
        + "\n  ".join(offenders)
    )

    # Positive control: the detector must be able to SEE a raw read, or the
    # empty offender list above is a fact about the walker and nothing else.
    permitted = next(
        fn for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef)
        and fn.name == "test_the_STDOUT_audit_stream_names_the_matched_fingerprint"
    )
    assert _raw_audit_reads(permitted), (
        "the raw-read detector sees nothing — it is broken, and every zero it "
        "reported above is meaningless"
    )
    assert _eof_barriers(permitted), (
        "the permitted site's `out.wait_closed()` is not being recognised as a "
        "barrier — the guard is passing it for some other reason"
    )

    # 🔴 CONTROLS FOR THE ESCAPE HATCH ITSELF, not just for the read detector.
    # A guard whose exemption can be spelled by a dead line is a free pass. Each
    # shape below separates one clause of "unconditional, before, same object"
    # from its negation; the `if False:` pair is the mutant that SURVIVED the
    # first version of the guard, and the `with` pair is the mutant that
    # survived the SECOND — the same hole respelled, which is why they are
    # enumerated rather than summarised.
    def unguarded(src: str) -> "list[int]":
        return _unguarded_audit_reads(ast.parse(textwrap.dedent(src)).body[0])

    assert unguarded("""
        def test_x(store):
            with running(store) as (base, audit):
                await_audit(audit, 1)
            if False:
                audit.wait_closed()
            assert "x" in audit[0]
    """), "a DEAD `wait_closed()` still buys a raw read a free pass"

    # The same shape bound to `out`. Against `audit` the dead barrier line is
    # ITSELF a raw `audit` read and self-flags, so the control above passes
    # partly by accident; this one has no such help.
    assert unguarded("""
        def test_x(out):
            await_audit(out, 1)
            if False:
                out.wait_closed()
            assert "x" in out.audit[0]
    """), "a DEAD `wait_closed()` on an `out`-bound stream buys a free pass"

    assert unguarded("""
        def test_x(out):
            await_audit(out, 1)
            with contextlib.suppress(AttributeError):
                out.wait_closed()
            assert "x" in out.audit[0]
    """), "a barrier inside a SUPPRESSING `with` was counted as unconditional"

    assert unguarded("""
        def test_x(out):
            await_audit(out, 1)
            with pytest.raises(AttributeError):
                out.wait_closed()
            assert "x" in out.audit[0]
    """), ("a barrier inside `pytest.raises(...)` — which REQUIRES the body to "
           "raise, i.e. NOT to complete — was counted as unconditional")

    assert unguarded("""
        def test_x(out):
            await_audit(out, 1)
            return
            out.wait_closed()
            assert "x" in out.audit[0]
    """), "a barrier after a `return` is unreachable and was counted anyway"

    assert unguarded("""
        def test_x(out, proc_out):
            await_audit(out, 1)
            proc_out.wait_closed()
            assert "x" in out.audit[0]
    """), "a barrier on a DIFFERENT stream exempted a read of `out.audit`"

    assert unguarded("""
        def test_x(out):
            assert "x" in out.audit[0]
            out.wait_closed()
    """), "a `wait_closed()` AFTER the read was accepted as if it preceded it"

    assert not unguarded("""
        def test_x(out):
            await_audit(out, 1)
            out.wait_closed()
            assert "x" in out.audit[0]
    """), "a real, unconditional `wait_closed()` before the read was rejected"

    # 🔴 CONTROLS FOR THE `assert` ARM — the round where the exemption was too
    # NARROW rather than too wide. Both directions, because widening the barrier
    # FORM must not widen the EXEMPTION: the accepted pair below is the whole
    # point of the arm, and everything after it is a shape that must still be
    # flagged with the arm in place.
    assert not unguarded("""
        def test_x(out):
            await_audit(out, 1)
            assert out.wait_closed()
            assert "x" in out.audit[0]
    """), ("`assert x.wait_closed()` — the form `settle` uses, and the only one "
           "that does not discard the answer — was rejected as a barrier")

    # The real site's spelling: an explicit timeout and a failure message.
    assert not unguarded("""
        def test_x(out):
            await_audit(out, 1)
            assert out.wait_closed(15.0), f"no EOF: {out.text}"
            assert "x" in out.audit[0]
    """), "an asserted barrier with a timeout and a message was rejected"

    # Refused on purpose: these assert the NEGATION of the barrier.
    assert unguarded("""
        def test_x(out):
            await_audit(out, 1)
            assert not out.wait_closed()
            assert "x" in out.audit[0]
    """), ("`assert not x.wait_closed()` asserts the stream did NOT reach EOF "
           "and was accepted as a barrier for it")

    assert unguarded("""
        def test_x(out):
            await_audit(out, 1)
            assert out.wait_closed() is False
            assert "x" in out.audit[0]
    """), ("a `Compare` wrapper was counted, so `is False` — the negation one "
           "node further out — buys the read a pass")

    # And the four shapes from the previous round, respelled with the new arm.
    # The nesting/ordering clauses live in the statement walk, which the arm
    # shares; these pin that it did not become a way around them.
    assert unguarded("""
        def test_x(out):
            await_audit(out, 1)
            if False:
                assert out.wait_closed()
            assert "x" in out.audit[0]
    """), "an ASSERTED barrier inside `if False:` was counted as unconditional"

    assert unguarded("""
        def test_x(out):
            await_audit(out, 1)
            with contextlib.suppress(AttributeError):
                assert out.wait_closed()
            assert "x" in out.audit[0]
    """), "an ASSERTED barrier inside a SUPPRESSING `with` was counted"

    assert unguarded("""
        def test_x(out):
            await_audit(out, 1)
            with pytest.raises(AttributeError):
                assert out.wait_closed()
            assert "x" in out.audit[0]
    """), "an ASSERTED barrier inside `pytest.raises(...)` was counted"

    assert unguarded("""
        def test_x(out):
            await_audit(out, 1)
            return
            assert out.wait_closed()
            assert "x" in out.audit[0]
    """), "an ASSERTED barrier after a `return` is unreachable and was counted"

    assert unguarded("""
        def test_x(out, proc_out):
            await_audit(out, 1)
            assert proc_out.wait_closed()
            assert "x" in out.audit[0]
    """), "an ASSERTED barrier on a DIFFERENT stream exempted a read of `out.audit`"

    assert unguarded("""
        def test_x(out):
            assert "x" in out.audit[0]
            assert out.wait_closed()
    """), "an ASSERTED barrier AFTER the read was accepted as if it preceded it"


class TestTrustedProxyAllowlistParsing:
    """Invariant guards on `trusted_network` / `load_trusted_proxies`.

    Each guard below is reachable by an input no earlier guard rejects, which is
    the property a mutation sweep can otherwise not distinguish from a guard
    that never executes.
    """

    # 🔴 THREE GUARDS REACH "no trusted proxies", SO THAT PHRASE CANNOT TELL
    # THEM APART — and asserting it was how two mutants survived. Each test
    # below pins the SENTENCE ITS OWN GUARD emits. Measured: with guard 1
    # weakened to `if raw is None`, a blank value falls through to the
    # empty-result guard and raises a message that still contains
    # "no trusted proxies", so the sweep scored it SURVIVED.
    UNSET_SENTENCE = "set $SUBSYSTEM_STORE_TRUSTED_PROXIES to the address(es)"
    NO_ENTRIES_SENTENCE = "resolved to no entries"

    def test_an_UNSET_variable_raises_rather_than_defaulting(self):
        with pytest.raises(ValueError) as exc:
            api.load_trusted_proxies({})
        assert self.UNSET_SENTENCE in str(exc.value)

    def test_a_BLANK_variable_raises_from_THE_SAME_guard_not_a_later_one(self):
        """Reachable past a presence-only guard 1: the variable IS set, to
        whitespace. And it must be guard 1 that catches it — a fall-through to
        the empty-result guard is a different message and a different bug.
        """
        with pytest.raises(ValueError) as exc:
            api.load_trusted_proxies({"SUBSYSTEM_STORE_TRUSTED_PROXIES": "  \t "})
        assert self.UNSET_SENTENCE in str(exc.value)
        assert self.NO_ENTRIES_SENTENCE not in str(exc.value)

    def test_a_NON_BLANK_value_that_yields_NO_ENTRIES_raises_too(self):
        """🔴 REACHABILITY for the empty-result guard, which nothing else here
        reaches: `","` is not blank, so guard 1 passes it, and the split then
        yields nothing but empty strings. Without this the guard is deletable
        and the sweep says so — measured, it survived.
        """
        with pytest.raises(ValueError) as exc:
            api.load_trusted_proxies({"SUBSYSTEM_STORE_TRUSTED_PROXIES": ",,"})
        assert self.NO_ENTRIES_SENTENCE in str(exc.value)
        assert self.UNSET_SENTENCE not in str(exc.value)

    def test_a_NON_ADDRESS_entry_names_the_offending_item(self):
        with pytest.raises(ValueError) as exc:
            api.load_trusted_proxies(
                {"SUBSYSTEM_STORE_TRUSTED_PROXIES": "192.0.2.1, gateway"}
            )
        message = str(exc.value)
        # 🔴 THE WHOLE COMPUTED PREFIX, PINNED AS ONE STRING — and that is a
        # correction, not style. The first version asserted `"'gateway'" in
        # message`, which SURVIVED a mutant that replaced the `{item!r}` slot
        # with the literal word "an entry": `ip_network`'s own ValueError
        # already contains `'gateway'`, so the assertion was reading the
        # exception's static prose and never the computed slot at all. A guard
        # on WORDS is walkable by a value that spells the same words somewhere
        # else in the sentence.
        assert message.startswith(
            "SUBSYSTEM_STORE_TRUSTED_PROXIES: 'gateway' is not an IP address or CIDR ("
        ), message
        # And the VALID sibling must not be blamed.
        assert "192.0.2.1'" not in message

    def test_a_DEFAULT_ROUTE_is_refused_in_BOTH_families(self):
        """The refusal is by PREFIX LENGTH, so it is one rule rather than a list
        of spellings somebody has to extend. Both families, because a guard
        written against `"0.0.0.0/0"` as a string passes the v4 case and lets
        `::/0` straight through.
        """
        for spelling in ("0.0.0.0/0", "::/0"):
            with pytest.raises(ValueError) as exc:
                api.load_trusted_proxies(
                    {"SUBSYSTEM_STORE_TRUSTED_PROXIES": spelling}
                )
            assert "trusts every peer" in str(exc.value), spelling

    def test_A_WIDE_PREFIX_IS_REFUSED_because_the_slash_zero_guard_reads_ONE_entry(self):
        """🔴 THIS REPLACES A TEST THAT PINNED A WALK AS CORRECT. It used to
        assert `0.0.0.0/1` was ACCEPTED — "the guard is not a ban on CIDRs" —
        which is true and useless, because the two halves of the address space,
        each written as a `/1`, parse clean and together trust every IPv4 peer.
        Refusing only `/0` inspects one entry in isolation and cannot see the
        union.

        The realistic misconfiguration is worse than the contrived one: a pod
        CIDR is exactly the shape an operator reaches for, and it hands the
        client identity to every pod in the cluster — verbatim the attacker in
        this module's threat model.

        ⚠ THE UPPER HALF IS BUILT ARITHMETICALLY, NOT WRITTEN. It is routable
        space, and `test_no_public_ips.py` refuses an IP literal in a PUBLIC
        repo — it caught the first draft of this test and of the comment in
        `server.py`. Widening that allowlist would have been the failure mode,
        not the fix.
        """
        import ipaddress

        upper_half = f"{ipaddress.ip_address(1 << 31)}/1"
        for spelling in (
            f"0.0.0.0/1,{upper_half}",  # the union that covers everything
            "10.244.0.0/16",  # a pod CIDR: every pod in the cluster
            "2001:db8::/48",  # the v6 mirror, which a v4-only floor would miss
        ):
            with pytest.raises(ValueError) as exc:
                api.load_trusted_proxies({"SUBSYSTEM_STORE_TRUSTED_PROXIES": spelling})
            assert "too broad" in str(exc.value), spelling

    def test_THE_FLOOR_ITSELF_IS_ACCEPTED_so_the_guard_is_not_a_ban_on_CIDRs(self):
        """The negative half, at the BOUNDARY rather than somewhere comfortable.
        Without it, `raise` on every network with a prefix would pass the test
        above — and the setting would be unusable for the one deployment shape
        (a proxy subnet) it exists to serve. Measured at both ends of both
        families: the floor passes, one bit wider fails.
        """
        import ipaddress

        assert api.load_trusted_proxies(
            {"SUBSYSTEM_STORE_TRUSTED_PROXIES": "198.51.100.0/24"}
        ) == (ipaddress.ip_network("198.51.100.0/24"),)
        assert api.load_trusted_proxies(
            {"SUBSYSTEM_STORE_TRUSTED_PROXIES": "2001:db8::/64"}
        ) == (ipaddress.ip_network("2001:db8::/64"),)
        for one_bit_wider in ("198.51.100.0/23", "2001:db8::/63"):
            with pytest.raises(ValueError) as exc:
                api.load_trusted_proxies(
                    {"SUBSYSTEM_STORE_TRUSTED_PROXIES": one_bit_wider}
                )
            assert "too broad" in str(exc.value), one_bit_wider

    def test_the_TOO_BROAD_message_is_NOT_the_default_route_message(self):
        """Two guards, two diagnostics, and they must not collapse into one: `/0`
        is "you disabled it", a wide prefix is "you meant a smaller range". A
        test asserting a phrase both emit could not tell them apart — which is
        exactly how two mutants survived an earlier round of this file.
        """
        with pytest.raises(ValueError) as zero:
            api.load_trusted_proxies({"SUBSYSTEM_STORE_TRUSTED_PROXIES": "0.0.0.0/0"})
        with pytest.raises(ValueError) as wide:
            api.load_trusted_proxies({"SUBSYSTEM_STORE_TRUSTED_PROXIES": "10.0.0.0/8"})
        assert "trusts every peer" in str(zero.value)
        assert "too broad" not in str(zero.value)
        assert "too broad" in str(wide.value)
        assert "trusts every peer" not in str(wide.value)
        # And the wide one NAMES the entry and the floor, or the operator cannot
        # act on it.
        assert "'10.0.0.0/8'" in str(wide.value)
        assert "/24" in str(wide.value)

    def test_COMMAS_AND_WHITESPACE_both_separate(self):
        import ipaddress

        assert api.load_trusted_proxies(
            {"SUBSYSTEM_STORE_TRUSTED_PROXIES": "192.0.2.1,198.51.100.0/24  203.0.113.9"}
        ) == (
            ipaddress.ip_network("192.0.2.1/32"),
            ipaddress.ip_network("198.51.100.0/24"),
            ipaddress.ip_network("203.0.113.9/32"),
        )

    def test_a_CIDR_WITH_HOST_BITS_is_taken_as_the_network_it_names(self):
        """`strict=False`, deliberately: refusing `198.51.100.7/24` would push an
        operator towards the `/0` the guard above exists to stop.
        """
        import ipaddress

        assert api.load_trusted_proxies(
            {"SUBSYSTEM_STORE_TRUSTED_PROXIES": "198.51.100.7/24"}
        ) == (ipaddress.ip_network("198.51.100.0/24"),)


class TestPeerAddressNormalisation:
    """`peer_address` turns a socket's `client_address` into an identity, or
    `None` — and `None` means refuse.
    """

    def test_an_IPv4_MAPPED_peer_matches_its_IPv4_allowlist_entry(self):
        """🔴 A dual-stack listener reports a v4 caller as `::ffff:198.51.100.4`.
        Without unwrapping, an allowlist written the obvious way never matches
        and the operator widens it until it does.
        """
        trusted = api.load_trusted_proxies(
            {"SUBSYSTEM_STORE_TRUSTED_PROXIES": "198.51.100.4"}
        )
        peer = api.peer_address(("::ffff:198.51.100.4", 4242, 0, 0))
        assert api.peer_is_trusted(peer, trusted) is True

    def test_it_does_NOT_aggregate_IPv6_to_a_slash_64_the_way_rate_limit_key_does(self):
        """🔴 THE SEAM BETWEEN TWO NORMALISERS. `rate_limit_key` collapses IPv6 to
        its /64 on purpose — an attacker picks freely inside their allocation.
        Reusing it here would trust 2**64 peers the operator never named. Two
        addresses in ONE /64: one allowlisted, the other must NOT be trusted.
        """
        trusted = api.load_trusted_proxies(
            {"SUBSYSTEM_STORE_TRUSTED_PROXIES": "2001:db8:1:2::1"}
        )
        assert api.peer_is_trusted(api.peer_address(("2001:db8:1:2::1", 1, 0, 0)), trusted)
        sibling = api.peer_address(("2001:db8:1:2:ffff:ffff:ffff:ffff", 1, 0, 0))
        assert api.peer_is_trusted(sibling, trusted) is False

    def test_an_IPv6_SCOPE_ID_does_not_make_the_peer_unparseable(self):
        """A link-local peer arrives as `fe80::1%eth0`, which `ip_address`
        refuses. The zone is a local interface name, not part of the identity.
        """
        assert str(api.peer_address(("fe80::1%eth0", 1, 0, 0))) == "fe80::1"

    @pytest.mark.parametrize(
        "client_address",
        [None, (), "127.0.0.1", ("not-an-address", 1), (127, 1), ({"a": 1}, 1)],
    )
    def test_every_shape_that_is_not_an_address_is_None_not_a_crash(self, client_address):
        """This runs on the PRE-AUTH path, where an unhandled exception is a
        cheaper log flood than any request that IS metered — the defect
        `_request_path` already exists to prevent, one screen away.
        """
        assert api.peer_address(client_address) is None

    def test_None_is_never_trusted(self):
        trusted = api.load_trusted_proxies(
            {"SUBSYSTEM_STORE_TRUSTED_PROXIES": "192.0.2.1"}
        )
        assert api.peer_is_trusted(None, trusted) is False

    def test_a_v6_peer_against_a_v4_allowlist_is_False_not_a_TypeError(self):
        """An allowlist holding one family and a peer from the other is an
        ordinary deployment, not an error.

        ⚠ THE NAME RECORDS A CLAIM THAT WAS WRONG. `peer_is_trusted` used to
        carry an explicit version gate, commented "`IPv4Address in IPv6Network`
        raises TypeError". It does not — `ipaddress` compares versions itself
        and returns False (measured on 3.12.13 and 3.14.7). The gate was dead
        code; a mutation sweep found it by surviving its removal. The BEHAVIOUR
        is still worth pinning, so the test stays and the guard is gone.
        """
        trusted = api.load_trusted_proxies(
            {"SUBSYSTEM_STORE_TRUSTED_PROXIES": "192.0.2.0/24"}
        )
        assert api.peer_is_trusted(api.peer_address(("2001:db8::1", 1, 0, 0)), trusted) is False

    def test_a_BARE_STRING_allowlist_is_refused_LOUDLY(self):
        """Iterating a `str` yields characters, so `"192.0.2.1"` as an allowlist
        would refuse every request — a misconfiguration wearing an attack's
        clothes. A type annotation is not a code path; this check is.
        """
        with pytest.raises(TypeError) as exc:
            api.peer_is_trusted(api.peer_address(("192.0.2.1", 1)), "192.0.2.1")
        assert "SEQUENCE" in str(exc.value)


class TestTrustedProxyOverHTTP:
    """The gate as the wire sees it. Invariant guards: `build_server(trusted_
    proxies=…)` does not exist at base, so their red there is an AttributeError.
    """

    def test_a_BAD_TOKEN_from_an_untrusted_peer_is_the_SAME_uniform_401(
        self, store: Path
    ):
        """🔴 The wire must not discriminate. Once a request DOES fail auth, the
        401 an untrusted peer sees must be byte-identical to the one a trusted
        peer sees — otherwise the response is an oracle for "which hop is
        trusted", which is the enumeration surface §2b forbids.
        """
        with running(store, trusted_proxies=(NOT_LOOPBACK_PROXY,)) as (base, _audit):
            untrusted = fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
        with running(store) as (base, _audit):
            trusted = fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
        assert untrusted[0] == trusted[0] == 401
        assert untrusted[2] == trusted[2] == b"unauthorized\n"
        assert _comparable(untrusted[1]) == _comparable(trusted[1])

    def test_the_LOG_ANNOTATES_the_peer_WITHOUT_calling_it_an_auth_failure(
        self, store: Path
    ):
        """🔴 THIS REPLACES A TEST THAT PINNED THE WRONG SHAPE. It asserted
        `status=untrusted-peer` together with `auth=fail` — which meant every
        `kubectl port-forward` landed in the Loki auth-fail alert, and an alert
        that fires on the documented acceptance procedure is one the operator
        learns to ignore.

        Direct-to-pod access must still be greppable, so it is its OWN field.
        The request itself succeeds.
        """
        with running(store, trusted_proxies=(NOT_LOOPBACK_PROXY,)) as (base, audit):
            code, _h, _b = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
            await_audit(audit, 1)
        assert code == 200, code
        lines = settle(audit, 1)
        assert "peer=untrusted" in lines[0], lines[0]
        assert "auth=ok" in lines[0], lines[0]
        assert "result=200" in lines[0], lines[0]
        assert "status=untrusted-peer" not in lines[0], lines[0]

    def test_a_TRUSTED_peer_is_annotated_too_so_the_field_is_not_write_only(
        self, store: Path
    ):
        """The other half. A field that only ever takes one value cannot tell a
        reader that the OTHER case did not occur — `peer=trusted` is what makes
        the absence of `peer=untrusted` mean something.
        """
        with running(store) as (base, audit):
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
            line = await_audit(audit, 1)[0]
        assert "peer=trusted" in line, line
        assert "peer=untrusted" not in line, line

    def test_a_WRITE_verb_from_an_untrusted_peer_is_METERED_under_the_peer(
        self, store: Path
    ):
        """🔴 ONE RULE, BOTH DOORS. `_write` and `_handle` share
        `_identify_and_meter` precisely because a check enforced at one call site
        and not the other is the failure this file keeps finding — writes used to
        skip the client-IP and lockout checks entirely. A write from an untrusted
        peer therefore gets the ordinary 405, annotated, exactly once.
        """
        limiter = api.RateLimiter(max_failures=5, window_s=600.0, lockout_s=600.0)
        with running(
            store, limiter=limiter, trusted_proxies=(NOT_LOOPBACK_PROXY,)
        ) as (base, audit):
            code, _h, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, method="POST"
            )
            await_audit(audit, 1)
        # 🔴 EXACTLY ONE LINE, AND NOTHING CHARGED for a request that AUTHENTICATED
        # — a round-2 correction, not belt and braces. A mutant that mis-handles
        # the identify step's return value answers a SECOND response here and
        # charges the limiter under a `None` key; the GET path hides that on an
        # internal assert. `settle`, not the snapshot: the second line that
        # mutant emits need not be synchronous with the first.
        lines = settle(audit, 1)
        assert code == 405, (code, body)
        assert body == b"read-only\n"
        assert "peer=untrusted" in lines[0], lines[0]
        assert f"ip={TRUSTED_PEER}" in lines[0], lines[0]
        assert limiter._failures == {} and limiter._locked_until == {}

    def test_an_UNKNOWN_VERB_from_an_untrusted_peer_is_METERED_under_the_peer(
        self, store: Path
    ):
        """The third door: `send_error`, which every unhandled method reaches.
        An unknown verb never authenticates, so it IS a charged failure — under
        the peer's own address, which is the whole point.
        """
        limiter = api.RateLimiter(max_failures=5, window_s=600.0, lockout_s=600.0)
        with running(
            store, limiter=limiter, trusted_proxies=(NOT_LOOPBACK_PROXY,)
        ) as (base, audit):
            code, _h, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, method="FROBNICATE"
            )
            await_audit(audit, 1)
        lines = settle(audit, 1)
        assert code == 401, (code, body)
        assert body == b"unauthorized\n"
        assert "peer=untrusted" in lines[0], lines[0]
        assert list(limiter._failures) == [TRUSTED_PEER], limiter._failures

    def test_the_header_is_NOT_READ_AT_ALL_from_an_untrusted_peer(self, store: Path):
        """🔴 THE ONE ORDERING THAT CAN BE WRONG WHILE EVERY TEST ABOVE STAYS
        GREEN. Two requests from the same untrusted peer, one sending a forged
        `CF-Connecting-IP` and one sending none at all, must be booked under the
        SAME bucket — the peer's. If the header were consulted at all, the forged
        request would land somewhere else and the forger would get a free second
        budget by rotating the header.
        """
        with running(store, trusted_proxies=(NOT_LOOPBACK_PROXY,)) as (base, audit):
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=SPOOF_IP)
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=None)
            await_audit(audit, 2)
        lines = settle(audit, 2)
        assert all(f"ip={TRUSTED_PEER}" in ln for ln in lines), lines
        assert all(f"ip={SPOOF_IP}" not in ln for ln in lines), lines
        # …and the absent header is NOT the `no-client-ip` refusal either: that
        # rule applies only where the header IS the identity.
        assert all("status=no-client-ip" not in ln for ln in lines), lines

    def test_an_untrusted_peer_IS_charged_to_ITS_OWN_bucket(self, store: Path):
        """🔴 THIS REPLACES A TEST THAT ASSERTED NOTHING WAS CHARGED. Under the
        old refuse-outright design that was true; under this one it would mean an
        untrusted peer had an unlimited budget, which is worse than the defect
        being fixed. Five failed auths from one untrusted peer must lock out that
        peer — and the bucket must be keyed on the PEER, never on the header.
        """
        limiter = api.RateLimiter(max_failures=5, window_s=600.0, lockout_s=600.0)
        with running(
            store, limiter=limiter, trusted_proxies=(NOT_LOOPBACK_PROXY,)
        ) as (base, audit):
            # 🔴 The waits are INTERLEAVED because the read below is POSITIONAL:
            # `await_audit` guarantees a count, never an order. See its docstring.
            for k in range(5):
                fetch(
                    f"{base}/api/v1/recall/{SCOPE}", token="w" * 48, client_ip=SPOOF_IP
                )
                await_audit(audit, k + 1)
            after = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=SPOOF_IP
            )
            lines = await_audit(audit, 6)
        assert after[0] == 401, "an untrusted peer had an unlimited budget"
        assert list(limiter._locked_until) == [TRUSTED_PEER], limiter._locked_until
        assert SPOOF_IP not in limiter._locked_until, limiter._locked_until
        assert "status=lockout-triggered" in lines[4], lines[4]

    def test_healthz_answers_an_untrusted_peer(self, store: Path):
        with running(store, trusted_proxies=(NOT_LOOPBACK_PROXY,)) as (base, audit):
            code, _h, body = fetch(f"{base}/healthz", client_ip=None)
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
            await_audit(audit, 1)
        assert (code, body) == (200, b"ok\n")
        # 🔴 A REASSURING ZERO NEEDS A POSITIVE CONTROL. `assert audit == []`
        # read the live list with nothing to wait for, so it was equally happy
        # with "the probe is not audited" and with "the sink had not appended
        # yet" — and it would have stayed green with `_audit` wired to nothing
        # at all. An audited request is issued after the probe; waiting for ITS
        # line proves the sink works, and the count then says the probe added
        # none. 🔴 THE COUNT IS `settle`'s, NOT THE SNAPSHOT'S — this comment
        # used to close with "(Residual: a probe line arriving after this
        # snapshot is still unobserved)", and that residual is now closed for
        # anything landing inside `SETTLE_GRACE_S` of teardown. Still bounded by
        # that window: a sink with no EOF admits no stronger claim.
        lines = settle(audit, 1)
        assert "/healthz" not in lines[0], lines[0]

    def test_a_CIDR_entry_admits_a_peer_INSIDE_it(self, store: Path):
        """POSITIVE CONTROL for the CIDR arm. Every other test in this class
        uses a /32-equivalent, so `return False` for any prefixed network would
        pass all of them.
        """
        # `/24`, not the `/8` this used to say: a /8 is now refused by the
        # prefix floor, and a test that quietly relied on it would have turned
        # into a fact about the floor rather than about the CIDR arm.
        with running(store, trusted_proxies=("127.0.0.0/24",)) as (base, audit):
            code, _h, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
            line = await_audit(audit, 1)[0]
        assert code == 200, (code, body)
        assert "peer=trusted" in line, line

    def test_build_server_REFUSES_an_empty_allowlist(self, store: Path):
        with pytest.raises(ValueError) as exc:
            api.build_server(
                host="127.0.0.1",
                port=0,
                store_root=str(store),
                tokens=(GOOD_TOKEN,),
                trusted_proxies=(),
            )
        assert "empty" in str(exc.value)

    def test_build_server_REFUSES_a_bare_string_allowlist(self, store: Path):
        with pytest.raises(TypeError) as exc:
            api.build_server(
                host="127.0.0.1",
                port=0,
                store_root=str(store),
                tokens=(GOOD_TOKEN,),
                trusted_proxies=LOOPBACK_PROXY,
            )
        assert "SEQUENCE" in str(exc.value)

    def test_build_server_REFUSES_a_default_route_through_the_PROGRAMMATIC_door(
        self, store: Path
    ):
        """🔴 The `/0` refusal lives in `trusted_network`, which BOTH doors go
        through. A guard placed only in the env parser would be walked by any
        caller constructing the server directly — and this file's own harness is
        such a caller.
        """
        with pytest.raises(ValueError) as exc:
            api.build_server(
                host="127.0.0.1",
                port=0,
                store_root=str(store),
                tokens=(GOOD_TOKEN,),
                trusted_proxies=("0.0.0.0/0",),
            )
        assert "trusts every peer" in str(exc.value)

    def test_the_HANDLER_CLASS_DEFAULT_trusts_nobody(self):
        """`build_server` refuses an empty allowlist, so the class attribute is
        not reachable through it — which is exactly why it is pinned here. A
        subclass that never went through `build_server` must fail CLOSED, and
        `()` read as "unset, allow all" is the shape that mistake takes.
        """
        assert api.StoreRequestHandler.trusted_proxies == ()
        assert (
            api.peer_is_trusted(
                api.peer_address(("127.0.0.1", 1)),
                api.StoreRequestHandler.trusted_proxies,
            )
            is False
        )
        # …and "trusts nobody" now means "believes nobody's header", so the
        # bucket falls back to the peer rather than to the forged value.
        assert api.resolve_client(
            {"CF-Connecting-IP": SPOOF_IP},
            ("127.0.0.1", 1),
            api.StoreRequestHandler.trusted_proxies,
        ) == ("127.0.0.1", False)


class TestResolveClientIsTheWholeRule:
    """`resolve_client` is the one place the trusted/untrusted decision is made.
    Extracted as a pure function precisely so the branches a real TCP socket
    cannot produce are reachable here.
    """

    TRUSTED = ("198.51.100.9",)

    def _trusted(self):
        return api.load_trusted_proxies(
            {"SUBSYSTEM_STORE_TRUSTED_PROXIES": self.TRUSTED[0]}
        )

    def test_a_TRUSTED_peer_is_bucketed_on_the_HEADER(self):
        assert api.resolve_client(
            {"CF-Connecting-IP": CLIENT_IP}, (self.TRUSTED[0], 9), self._trusted()
        ) == (CLIENT_IP, True)

    def test_an_UNTRUSTED_peer_is_bucketed_on_ITSELF_and_the_header_is_ignored(self):
        """The two halves of one assertion: the answer IS the peer, and it is
        NOT the header. Either alone is satisfied by a bug — returning `None`
        satisfies "not the header", and echoing the header back satisfies
        neither but would pass a test that only checked the flag.
        """
        key, trusted = api.resolve_client(
            {"CF-Connecting-IP": SPOOF_IP}, (OTHER_IP, 9), self._trusted()
        )
        assert key == OTHER_IP
        assert key != SPOOF_IP
        assert trusted is False

    def test_a_TRUSTED_peer_with_NO_header_still_FAILS_CLOSED(self):
        """The fail-closed rule survives the redesign: where the header IS the
        identity, its absence is still a refusal, not a fallback to the peer.
        Otherwise a trusted proxy that stopped setting the header would silently
        bucket the whole internet under one key.
        """
        assert api.resolve_client({}, (self.TRUSTED[0], 9), self._trusted()) == (
            None,
            True,
        )

    def test_a_PEER_THAT_IS_NOT_AN_ADDRESS_refuses_rather_than_crashing(self):
        """🔴 REACHABLE ONLY HERE. A real TCP socket always yields an address, so
        this branch cannot be driven over the wire — which is the argument for
        extracting the function rather than leaving the logic inline where the
        branch would be untestable and therefore unverified.
        """
        for bogus in (None, (), ("not-an-address", 9), (127, 9)):
            assert api.resolve_client(
                {"CF-Connecting-IP": SPOOF_IP}, bogus, self._trusted()
            ) == (None, False), bogus

    def test_an_UNTRUSTED_v6_peer_is_aggregated_to_its_slash_64(self):
        """Both branches normalise through `rate_limit_key`, or the peer branch
        would hand an IPv6 caller 2**64 free buckets — the exact hazard
        `rate_limit_key` exists for, reintroduced through the new door.
        """
        key, trusted = api.resolve_client(
            {}, ("2001:db8:1:2:ffff:ffff:ffff:ffff", 9, 0, 0), self._trusted()
        )
        assert key == "2001:db8:1:2::/64"
        assert trusted is False


# =============================================================================
# 16. PHASE 3, CRITERIA 1-3 — two-token authorization on the READ path.
#
# 🔴 WHICH OF THESE ARE REGRESSION TESTS, HONESTLY. `server.py` and the absent
# path both EXIST at the base ref, and the leak is real there, so the tests in
# `TestEnumerationChannelsAreClosed` and `TestRefusedIsIndistinguishableFromAbsent`
# are genuine regressions: they go red at base for the RIGHT reason (the body
# names scopes the caller may not see) rather than by API error. The GUARD tests
# in `TestScopedTokenRowGuards` are NOT: they call a parser that does not accept
# a three-field row at base, so their red is a shape error and proves nothing —
# the mutation matrix in the PR body is their evidence.
#
# 🔴 AND THE WRITE PATH IS NOT HERE. Criteria 4-10 add no verb in this branch;
# `TestPhaseOneScope.test_the_server_declares_no_write_handler` is untouched and
# still the thing that has to be broken on purpose when the write path lands.
# =============================================================================


# Pairwise-distinct, and distinct from every scope constant already in this
# file AND from every literal any assertion below names. Invented for this
# section so a renderer that surfaced the wrong scope cannot pass by
# coincidence.
ALLOW_SCOPE = "kelp-forest"     # zach may read it
DENY_SCOPE = "quartz-mine"      # dana may read it; zach may not
THIRD_SCOPE = "lantern-bay"     # nobody in these tests may read it
PHANTOM_SCOPE = "never-quarried"  # never exists on disk, at any point

# One distinctive sentence per scope, sharing no substring, so "did content from
# a scope I cannot see reach me" is answerable by a single `in` on the body.
KELP_NUANCE = "- 2026-03-04: the tide gauge drifts 3cm after a spring flood."
QUARTZ_NUANCE = "- 2026-03-05: the drill head overheats past 900 revolutions."
LANTERN_NUANCE = "- 2026-03-06: the beacon lamp browns out on a westerly gale."

ZACH_TOKEN = "k" * 20 + "L" * 20 + "m" * 8   # 48 chars
DANA_TOKEN = "p" * 20 + "Q" * 20 + "r" * 8   # 48 chars, disjoint
# 🔴 TOKEN-LENGTH AND ALL-LOWERCASE, so it PASSES the identity charset check and
# can be rejected ONLY by the length cap. That is what lets the "a token can
# never be an identity" test measure the arithmetic it claims to measure rather
# than an incidental uppercase letter. `secrets.token_urlsafe` really can emit
# an all-lowercase string, so this is a shape, not a contrivance.
LOWER_TOKEN = "s" * 24 + "t" * 24            # 48 chars, [a-z] only

# The identity class, spelled again here BY HAND. Importing
# `api.IDENTITY_COMPONENT` would make the fixture's precondition and the code
# under test the same expression, so a wrong class would satisfy both.
IDENTITY_CHARSET = re.compile(r"[a-z0-9][a-z0-9-]*")

# A fixed instant, applied to every entry file, so two stores built from
# different scope NAMES still date identically. `snapshot_freshness` reports the
# newest entry mtime and the entry-file COUNT store-wide, and both are shared
# across an allowlist — see the module docstring's residual-leak note. Holding
# them constant is what lets the byte-identity claim below be about scope
# EXISTENCE and nothing else.
FIXED_MTIME = 1_767_225_600.0  # 2026-01-01T00:00:00Z, an arbitrary round instant


def _scoped_record(token: str, identity: str, *scopes: str):
    return api.TokenRecord(token=token, identity=identity, scopes=tuple(scopes))


ZACH = _scoped_record(ZACH_TOKEN, "zach", ALLOW_SCOPE)
DANA = _scoped_record(DANA_TOKEN, "dana", DENY_SCOPE)


def _build_store(root: Path, scopes: "dict[str, str]", *, malformed: str = "") -> Path:
    """A store holding one entry per named scope, every mtime pinned.

    `scopes` maps scope name -> the nuance line that scope's single entry
    carries. `malformed` optionally names a scope that also gets a front-matter-
    less file, which is what puts a row on `index.malformed`.
    """
    root.mkdir(parents=True, exist_ok=True)
    for scope, nuance in scopes.items():
        (root / scope).mkdir(parents=True, exist_ok=True)
        entry = root / scope / f"{scope}-entry.md"
        entry.write_text(_entry(f"{scope}-entry", scope, nuance=nuance))
    if malformed:
        (root / malformed / "broken-shard.md").write_text("no front matter here\n")
    for path in sorted(root.rglob("*.md")):
        os.utime(path, (FIXED_MTIME, FIXED_MTIME))
    return root


@pytest.fixture
def scoped_store(tmp_path: Path) -> Iterator[Path]:
    """Three populated scopes, and the malformed row lives in a DENIED one.

    The malformed placement is the point: `malformed_elsewhere` is rendered on
    EVERY status, so a reject sitting in a scope the caller cannot name is the
    channel that leaks without any miss ever happening.

    🔴 SITED off the contended disk like `store` above, and it matters MORE than
    that one: this fixture feeds ~110 `running(scoped_store, …)` sites, i.e. most
    of the in-request-fsync population in this file, including the whole
    concurrent-append and If-Match/revision blocks. It was missed when #1211 sited
    `store` alone — the same "fixed one call site of N" error that PR was written
    to correct, repeated one level down inside the file it had just fixed.
    """
    with store_siting.store_root(tmp_path) as root:
        yield _build_store(
            root,
            {
                ALLOW_SCOPE: KELP_NUANCE,
                DENY_SCOPE: QUARTZ_NUANCE,
                THIRD_SCOPE: LANTERN_NUANCE,
            },
            malformed=DENY_SCOPE,
        )


class TestScopedTokenRowGuards:
    """🔴 SIX NEW GUARDS, AND EACH INPUT PASSES EVERY EARLIER ONE.

    Guards 1-5 (no source / unreadable / empty / too many / too short) are
    unchanged and covered by `TestTokenLoadingGuards` and
    `TestTokenSetAndOverlapRotation` above. Every file below therefore uses
    tokens of 48 characters and at most four rows, so the only guard that can
    reject it is the one the test names — a test that went red because a
    DIFFERENT guard fired would be green with the guard it names deleted, which
    is the failure mode this class is shaped against.

    Each assertion pins the guard's OWN sentence, not merely `ValueError`.
    """

    def _write(self, tmp_path: Path, *rows: str) -> str:
        path = tmp_path / "tokens"
        path.write_text("\n".join(rows) + "\n")
        return str(path)

    def test_GUARD_6_a_row_with_two_fields_is_MALFORMED_not_two_tokens(
        self, tmp_path: Path
    ):
        """🔴 THE FORMAT CHANGE, MADE LOUD. Under the old whole-file `.split()`
        this line was TWO credentials. Under the row format it is one row with a
        field count that means nothing, and the process refuses to start rather
        than pick a reading.
        """
        path = self._write(tmp_path, f"{ZACH_TOKEN} zach")
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        assert "malformed token row on line 1 of 1" in str(exc.value)
        assert "2 fields" in str(exc.value)
        # And never the credential itself, on the one file whose whole content
        # is credentials.
        assert ZACH_TOKEN not in str(exc.value)

    def test_GUARD_6_is_reached_by_a_FOUR_field_row_too(self, tmp_path: Path):
        # The other side of the `not in (1, 3)` boundary. A guard tested only
        # from below is a guard tested on one side of its condition.
        path = self._write(tmp_path, f"{ZACH_TOKEN} zach {ALLOW_SCOPE} extra")
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        assert "malformed token row on line 1 of 1" in str(exc.value)
        assert "4 fields" in str(exc.value)
        # 🔴 THE NEGATIVE HALF OF THE TYPO HINT, and it is what stops the hint
        # becoming noise. There is no comma anywhere in this row, so a
        # comma-spacing explanation would be a wrong guess printed with the same
        # confidence as the count. A hint that fires unconditionally is a hint
        # nobody reads.
        assert "NO SPACES" not in str(exc.value)

    def test_GUARD_6_a_SPACE_AFTER_A_COMMA_is_told_what_it_actually_did(
        self, tmp_path: Path
    ):
        """🔴 FAIL-CLOSED IS NOT THE SAME AS DIAGNOSTIC. `<tok> zach a, b` is a
        four-field row because the space after the comma split the scope list,
        and the row is correctly refused — but "4 fields, expected 1 or 3" sends
        the operator to count fields on a line that looks like it has three.

        The refusal is unchanged: same guard, same prefix, same count. Only the
        sentence after it is new, and it is conditional on a comma actually
        being present past the identity, so it cannot be printed as a guess.
        """
        path = self._write(
            tmp_path, f"{ZACH_TOKEN} zach {ALLOW_SCOPE}, {DENY_SCOPE}"
        )
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        message = str(exc.value)
        # The guard is NOT weakened — it still refuses, still by field count.
        assert "malformed token row on line 1 of 1" in message
        assert "4 fields" in message
        # …and now says what to do about it.
        assert "NO SPACES" in message
        assert "`alpha,beta`, not `alpha, beta`" in message
        assert ZACH_TOKEN not in message

    def test_GUARD_7_an_identity_outside_the_charset_is_refused(self, tmp_path: Path):
        # Passes guard 6: three fields. Fails only on the identity's spelling.
        path = self._write(tmp_path, f"{ZACH_TOKEN} Za_ch {ALLOW_SCOPE}")
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        assert "invalid identity in token row on line 1 of 1" in str(exc.value)
        assert "Za_ch" in str(exc.value)

    def test_GUARD_7_a_TOKEN_can_never_be_read_as_an_identity(self, tmp_path: Path):
        """🔴 THE STRUCTURAL HALF OF THE FORMAT CHANGE, AND THE FIXTURE HAS TO
        REACH IT. Three tokens on one line used to be three credentials; now it
        is a three-field row, and what stops the second one being read as an
        identity is ARITHMETIC — the cap is below the token floor, 48 > 32.

        So the identity here is `LOWER_TOKEN`, which is token-SHAPED and passes
        the charset check outright: only the LENGTH cap can reject it. An
        earlier version used a token containing uppercase, which meant the
        charset half did all the work and the length cap — the half the
        docstring is about — was never exercised at all.
        """
        path = self._write(tmp_path, f"{ZACH_TOKEN} {LOWER_TOKEN} {ALLOW_SCOPE}")
        assert IDENTITY_CHARSET.fullmatch(LOWER_TOKEN), (
            "the fixture must pass the CHARSET check, or this test measures the "
            "charset half and not the length cap it claims to"
        )
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        assert "invalid identity in token row on line 1 of 1" in str(exc.value)
        # 32 and 43, pinned LITERALLY — the cap must stay under the floor or
        # this whole property evaporates silently.
        assert api.MAX_IDENTITY_CHARS == 32
        assert api.MIN_TOKEN_CHARS == 43
        assert api.MAX_IDENTITY_CHARS < api.MIN_TOKEN_CHARS
        assert len(LOWER_TOKEN) > api.MAX_IDENTITY_CHARS

    def test_GUARD_8_a_mapped_row_may_not_claim_the_legacy_identity(
        self, tmp_path: Path
    ):
        # Passes 6 (three fields) and 7 (`legacy` is a well-formed identity).
        # Only the reservation can reject it.
        path = self._write(tmp_path, f"{ZACH_TOKEN} legacy {ALLOW_SCOPE}")
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        assert "reserved identity in token row on line 1 of 1" in str(exc.value)
        assert "'legacy'" in str(exc.value)

    def test_GUARD_9_an_explicitly_empty_allowlist_is_refused(self, tmp_path: Path):
        # Three fields, a valid non-reserved identity, and a third field holding
        # no scope name at all. Every earlier guard passes.
        path = self._write(tmp_path, f"{ZACH_TOKEN} zach ,")
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        assert "empty scope allowlist in token row on line 1 of 1" in str(exc.value)
        assert "'zach'" in str(exc.value)

    def test_GUARD_10_a_scope_no_URL_could_name_is_refused(self, tmp_path: Path):
        # A dot is outside the path-component class, so `kelp.forest` could
        # never be requested — an allowlist entry that would sit inert forever.
        # Passes 9: the list is non-empty.
        path = self._write(tmp_path, f"{ZACH_TOKEN} zach kelp.forest")
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        assert "invalid scope in token row on line 1 of 1" in str(exc.value)
        assert "kelp.forest" in str(exc.value)

    def test_GUARD_10_also_catches_an_EMPTY_entry_inside_a_real_list(
        self, tmp_path: Path
    ):
        """The reachable case guard 9 cannot see: the list is not empty, so
        `any(...)` is satisfied, and one entry still names nothing.
        """
        path = self._write(
            tmp_path, f"{ZACH_TOKEN} zach {ALLOW_SCOPE},,{DENY_SCOPE}"
        )
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        assert "invalid scope in token row on line 1 of 1" in str(exc.value)

    def test_GUARD_10_also_catches_an_entry_that_FOLDS_AWAY_to_nothing(
        self, tmp_path: Path
    ):
        """🔴 THE HALF THE CHARACTER CLASS CANNOT SEE, and the reason the guard
        checks the FOLDED value rather than the typed one.

        `-` and `___` are inside `[A-Za-z0-9_-]+`, so they are perfectly namable
        in a URL — and `normalize_ref` folds both to the EMPTY STRING, which
        matches no index key. Such an entry is a grant that reads as working and
        does nothing, which is exactly what this guard's sentence promises to
        prevent. A guard narrower than its own description is worse than none,
        because it stops anyone looking.
        """
        for typo in ("-", "___", "--"):
            path = self._write(tmp_path, f"{ZACH_TOKEN} zach {typo}")
            with pytest.raises(ValueError) as exc:
                api.load_tokens(path, {}, warn=lambda _l: None)
            assert "invalid scope in token row on line 1 of 1" in str(exc.value), typo
            assert "folds away" in str(exc.value), typo

    def test_GUARD_11_the_MIGRATION_SHAPE_is_refused_not_silently_collapsed(
        self, tmp_path: Path
    ):
        """🔴 THE FAIL-OPEN THIS GUARD EXISTS FOR, AND IT WAS LIVE.

        "Scope a credential its holder already has" is the migration's own first
        step, and the natural way to write it is to leave the bare line and add a
        mapped one below. The loader used to drop the second row BEFORE parsing
        it — keyed on the token, order preserved — so this file loaded as ONE
        record, `identity='legacy' scopes=None`: UNRESTRICTED. No error. The
        mapped row did not exist, and the only signal was a banner reading
        "1 of 1 token rows are bare" over a two-line file.

        The two authorities are named in the message because the whole content
        of the complaint is that they DISAGREE — and neither an identity nor a
        scope name is a credential, so naming them keeps guard 5's property.
        """
        path = self._write(
            tmp_path, ZACH_TOKEN, f"{ZACH_TOKEN} zach {ALLOW_SCOPE}"
        )
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        message = str(exc.value)
        assert "duplicate token on lines 1 and 2" in message
        # Both authorities, spelled out — the unrestricted one by name, so an
        # operator reading the pod log can see WHICH reading they nearly got.
        assert "legacy (UNRESTRICTED)" in message
        assert f"zach ({ALLOW_SCOPE})" in message
        # …and never the credential itself, on the one file whose whole content
        # is credentials.
        assert ZACH_TOKEN not in message

    def test_GUARD_11_a_DUPLICATE_TOKEN_ROW_no_longer_bypasses_guards_6_to_10(
        self, tmp_path: Path
    ):
        """🔴 THE SECOND HALF OF THE SAME DEFECT: a dropped row was never
        VALIDATED either.

        This second row carries an invalid identity AND an invalid scope. Under
        the pre-fix loader the whole row vanished before guard 6 ran and the file
        loaded clean as `zach/{ALLOW_SCOPE}`. Every row must reach the ladder, so
        the row's OWN first failure — guard 7 — is what must fire, and the
        assertion names guard 7's sentence rather than merely `ValueError`: a
        test satisfied by any raise would be green with guard 7 deleted.
        """
        path = self._write(
            tmp_path,
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
            f"{ZACH_TOKEN} Za_CH_BAD !!!!",
        )
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        message = str(exc.value)
        assert "invalid identity in token row on line 2 of 2" in message
        assert "Za_CH_BAD" in message

    def test_GUARD_11_collapses_ONE_GRANT_SPELLED_TWO_WAYS(self, tmp_path: Path):
        """🔴 THE OTHER DIRECTION — OVER-REFUSING IS ALSO A FAILURE, and this is
        the case a purely TEXTUAL collapse gets wrong.

        `Kelp_Forest` and `kelp-forest` are the same grant: the parser folds both
        to one scope. The rows are not identical as text and ARE identical as
        records, and it is the record that decides. A guard comparing raw lines
        would refuse a file that says one unambiguous thing.
        """
        path = self._write(
            tmp_path,
            f"{ZACH_TOKEN} zach Kelp_Forest",
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
        )
        assert loaded(path, {}) == [(ZACH_TOKEN, "zach", (ALLOW_SCOPE,))]

    def test_GUARD_11_fires_even_when_the_IDENTITY_agrees(self, tmp_path: Path):
        """One token, one identity, two DIFFERENT allowlists. Guard 12 would also
        see this pair — and would tell the operator to invent `zach-prev`, which
        is the wrong advice for what is one credential written twice. Guard 11
        runs first precisely so the more specific complaint wins.
        """
        path = self._write(
            tmp_path,
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
            f"{ZACH_TOKEN} zach {DENY_SCOPE}",
        )
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        assert "duplicate token on lines 1 and 2" in str(exc.value)
        assert "duplicate identity" not in str(exc.value)

    def test_GUARD_11_runs_BEFORE_12_so_an_IDENTICAL_MAPPED_ROW_still_loads(
        self, tmp_path: Path
    ):
        """🔴 THE ORDER IS LOAD-BEARING, AND THIS IS WHAT BREAKS IF IT INVERTS.

        One mapped row pasted twice, verbatim. Guard 11 collapses it to a single
        record; if guard 12 ran first it would see two rows claiming `zach` and
        refuse the file. Nothing about this input is ambiguous, so it must load.
        """
        path = self._write(
            tmp_path,
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
        )
        assert loaded(path, {}) == [(ZACH_TOKEN, "zach", (ALLOW_SCOPE,))]

    def test_GUARD_12_two_rows_naming_ONE_identity_are_refused(self, tmp_path: Path):
        # Two rows, both well-formed, both with real allowlists, and two DISTINCT
        # tokens so guard 11 cannot be what fires — every earlier guard passes on
        # every row. Only the cross-row identity check can see this.
        path = self._write(
            tmp_path,
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
            f"{DANA_TOKEN} zach {DENY_SCOPE}",
        )
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        assert "duplicate identity 'zach'" in str(exc.value)
        assert "on lines 1 and 2" in str(exc.value)

    def test_GUARD_12_names_the_PHYSICAL_rows_across_a_collapse(
        self, tmp_path: Path
    ):
        """The index an operator is told to look at must be the LINE they can
        see. Row 2 collapses into row 1, so the clash is between rows 1 and 3 —
        a position in the post-collapse list would call it "rows 1 and 2" and
        send them to the wrong line.
        """
        path = self._write(
            tmp_path,
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
            f"{DANA_TOKEN} zach {DENY_SCOPE}",
        )
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        assert "token rows on lines 1 and 3 both claim it" in str(exc.value)

    def test_GUARD_12_names_the_PHYSICAL_LINE_when_A_BLANK_LINE_SHIFTS_IT(
        self, tmp_path: Path
    ):
        """🔴 THE FIXTURE ABOVE HAS NO BLANK LINE, SO IT PINS THE COLLAPSE
        DIMENSION AND NOTHING ELSE — while its name and the comment beside the
        code both claim PHYSICAL LINES. On a file with no blank line the ordinal
        over non-blank rows and the line number are the same number, so it reads
        as coverage for a claim it cannot see.

        This is that claim, reproduced: rows on lines 2, 4 and 6. Lines 2 and 4
        are identical and collapse; the identity clash is between lines 2 and 6.
        Counting non-blank rows says "1 and 3" — a real message this code emitted
        — and sends the operator to two lines that hold nothing.
        """
        path = self._write(
            tmp_path,
            "",
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
            "",
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
            "",
            f"{DANA_TOKEN} zach {DENY_SCOPE}",
        )
        message = str(exc_of(lambda: api.load_tokens(path, {}, warn=lambda _l: None)))
        assert "token rows on lines 2 and 6 both claim it" in message
        assert "1 and 3" not in message, (
            "the ordinal over non-blank rows leaked into the message"
        )

    def test_GUARD_11_names_the_PHYSICAL_LINE_when_A_BLANK_LINE_SHIFTS_IT(
        self, tmp_path: Path
    ):
        """The same claim for guard 11 — "every guard's message must use them
        consistently" is only true if every guard is measured. Rows on lines 2
        and 4, disagreeing, so guard 11 fires rather than 12.
        """
        path = self._write(
            tmp_path,
            "",
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
            "",
            f"{ZACH_TOKEN} zach {DENY_SCOPE}",
        )
        message = str(exc_of(lambda: api.load_tokens(path, {}, warn=lambda _l: None)))
        assert "duplicate token on lines 2 and 4" in message
        assert "1 and 2" not in message

    @pytest.mark.parametrize(
        "row,sentence",
        [
            ("hunter2", "token on line 4 of 4 is too short"),
            # Two TOKEN-LENGTH fields, so guard 5 passes and guard 6 is what
            # fires — `aaa bbb` would have been caught by the length floor first
            # and this row would have measured guard 5 twice.
            (f"{ZACH_TOKEN} {LOWER_TOKEN}", "malformed token row on line 4 of 4"),
            (f"{ZACH_TOKEN} Za_ch {ALLOW_SCOPE}", "invalid identity in token row on line 4 of 4"),
            (f"{ZACH_TOKEN} legacy {ALLOW_SCOPE}", "reserved identity in token row on line 4 of 4"),
            (f"{ZACH_TOKEN} zach ,", "empty scope allowlist in token row on line 4 of 4"),
            (f"{ZACH_TOKEN} zach ___", "invalid scope in token row on line 4 of 4"),
        ],
    )
    def test_GUARDS_5_to_10_ALL_name_the_PHYSICAL_LINE(
        self, tmp_path: Path, row: str, sentence: str
    ):
        """🔴 EVERY GUARD IN THE LADDER, NOT THE ONE THAT WAS CONVENIENT. The
        index was wrong in ALL of them — it is one loop — so a fix measured at
        one site is a fix that can be half-applied at the others and still look
        green. The bad row sits on physical line 4 behind two blank lines and a
        good row, so an ordinal over non-blank rows would say "2".
        """
        path = self._write(tmp_path, "", f"{DANA_TOKEN} dana {DENY_SCOPE}", "", row)
        message = str(exc_of(lambda: api.load_tokens(path, {}, warn=lambda _l: None)))
        assert sentence in message, message
        assert "of 2" not in message, (
            "`total` is still a count of non-blank rows, so 'line 4 of 2' or "
            f"'line 2 of 2' can be printed for a 4-line file: {message}"
        )

    def test_GUARD_11_collapses_a_scope_list_written_in_A_DIFFERENT_ORDER(
        self, tmp_path: Path
    ):
        """🔴 ORDER IS A SPELLING, NOT A DISAGREEMENT, and it was measured being
        refused: `<tok> zach alpha,beta` and `<tok> zach beta,alpha` answered
        "two different authorities — zach (alpha,beta) and zach (beta,alpha)".
        Both grant the same SET, so there IS a defined answer and guard 11 may
        not claim there is none. Guard 11's own comment already promised this
        ("rows that merely SPELL one grant differently ... are recognised as the
        same grant") while the code delivered it for case-folding only.

        The FIRST row's spelling is what survives, which is the same rule the
        rest of the collapse follows.
        """
        path = self._write(
            tmp_path,
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE},{DENY_SCOPE}",
            f"{ZACH_TOKEN} zach {DENY_SCOPE},{ALLOW_SCOPE}",
        )
        assert loaded(path, {}) == [
            (ZACH_TOKEN, "zach", (ALLOW_SCOPE, DENY_SCOPE))
        ]

    def test_GUARD_11_a_GENUINE_disagreement_is_STILL_refused(
        self, tmp_path: Path
    ):
        """🔴 THE UPPER BOUND ON THE FIX ABOVE. Comparing SETS must not become
        comparing nothing: `alpha,beta` and `alpha` are two different grants and
        one is strictly wider, which is the fail-open direction. Same shape as
        the collapse above — one token, one identity, two scope lists — so a
        mutant that returned a constant key, or dropped `scopes` from the key
        entirely, passes the test above and dies here.
        """
        path = self._write(
            tmp_path,
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE},{DENY_SCOPE}",
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
        )
        message = str(exc_of(lambda: api.load_tokens(path, {}, warn=lambda _l: None)))
        assert "duplicate token on lines 1 and 2" in message
        assert f"zach ({ALLOW_SCOPE},{DENY_SCOPE})" in message
        assert f"zach ({ALLOW_SCOPE})" in message

    def test_GUARD_11_a_BARE_row_never_collapses_into_a_MAPPED_one(
        self, tmp_path: Path
    ):
        """A bare row and a mapped row on ONE token are two authorities, and the
        refusal names the unrestricted one so the operator can see which reading
        they nearly got.

        ⚠ WHAT THIS DOES **NOT** PIN, said because the obvious reading of the
        name is wrong: it is not a test of `_authority_key`'s `None`-vs-empty
        asymmetry. The two rows differ in IDENTITY (`legacy` vs `zach`), so the
        identity component alone decides, and a mutant folding `None` into the
        empty frozenset SURVIVES this — measured. See `_authority_key`'s own
        note for why that mutant is unreachable rather than uncaught.
        """
        path = self._write(
            tmp_path, ZACH_TOKEN, f"{ZACH_TOKEN} zach {ALLOW_SCOPE}"
        )
        message = str(exc_of(lambda: api.load_tokens(path, {}, warn=lambda _l: None)))
        assert "duplicate token on lines 1 and 2" in message
        assert "legacy (UNRESTRICTED)" in message

    def test_a_WELL_FORMED_mapped_file_loads_with_its_allowlist(self, tmp_path: Path):
        """The positive control for all six guards above. Without it, a parser
        that rejected EVERYTHING would pass every test in this class.
        """
        path = self._write(
            tmp_path,
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE},{DENY_SCOPE}",
            f"{DANA_TOKEN} dana {DENY_SCOPE}",
        )
        assert loaded(path, {}) == [
            (ZACH_TOKEN, "zach", (ALLOW_SCOPE, DENY_SCOPE)),
            (DANA_TOKEN, "dana", (DENY_SCOPE,)),
        ]

    def test_an_allowlist_entry_is_FOLDED_the_way_the_reader_folds_a_scope(
        self, tmp_path: Path
    ):
        """🔴 An allowlist entry and the index key it must match cannot be
        allowed to disagree about case or `_` vs `-`: an entry that never
        matched would be a silently inert grant, which reads as a working one.
        """
        path = self._write(tmp_path, f"{ZACH_TOKEN} zach Kelp_Forest")
        assert loaded(path, {}) == [(ZACH_TOKEN, "zach", (ALLOW_SCOPE,))]


class TestLegacyRowsSurviveTheMigration:
    """🔴 CRITERION 10's REQUIREMENT, WHICH IS WHY THIS IS NOT A PREFERENCE.

    The old shared token has to keep working while clients move onto mapped
    rows, and the rollback is putting that one line back — a rollback that
    needed a code change would not be one.
    """

    def _write(self, tmp_path: Path, *rows: str) -> str:
        path = tmp_path / "tokens"
        path.write_text("\n".join(rows) + "\n")
        return str(path)

    def test_a_MIXED_file_of_legacy_and_mapped_rows_loads(self, tmp_path: Path):
        path = self._write(
            tmp_path,
            GOOD_TOKEN,                            # legacy, unrestricted
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",    # mapped
        )
        assert loaded(path, {}) == [
            (GOOD_TOKEN, "legacy", None),
            (ZACH_TOKEN, "zach", (ALLOW_SCOPE,)),
        ]

    def test_a_LEGACY_row_reads_EVERY_scope_over_HTTP(self, scoped_store: Path):
        """The unrestricted half, exercised rather than asserted from the shape:
        a bare token is served content from a scope no mapped row names.
        """
        with running(scoped_store, tokens=(GOOD_TOKEN,)) as (base, _):
            for scope, nuance in (
                (ALLOW_SCOPE, KELP_NUANCE),
                (DENY_SCOPE, QUARTZ_NUANCE),
                (THIRD_SCOPE, LANTERN_NUANCE),
            ):
                code, _h, body = fetch(f"{base}/api/v1/recall/{scope}", token=GOOD_TOKEN)
                assert code == 200, scope
                assert nuance.encode() in body, scope

    def test_the_startup_warning_NAMES_legacy_mode_and_its_fingerprints(
        self, tmp_path: Path
    ):
        """🔴 A one-line, loud, greppable statement that the store is running
        with an unrestricted credential. Without it "the migration is finished"
        is a guess, which is the same failure the `token=` fingerprint exists to
        stop for rotation.
        """
        path = self._write(
            tmp_path, GOOD_TOKEN, f"{ZACH_TOKEN} zach {ALLOW_SCOPE}"
        )
        warnings: list[str] = []
        api.load_tokens(path, {}, warn=warnings.append)
        assert len(warnings) == 1, warnings
        line = warnings[0]
        assert "LEGACY MODE" in line
        assert "1 of 2" in line
        assert api.token_id(GOOD_TOKEN) in line
        # NEGATIVE CONTROL on the same line: the mapped row is not legacy, so
        # its fingerprint must NOT be named as unrestricted.
        assert api.token_id(ZACH_TOKEN) not in line
        # …and never a credential.
        assert GOOD_TOKEN not in line and ZACH_TOKEN not in line

    def test_NO_warning_when_EVERY_row_is_mapped(self, tmp_path: Path):
        """🔴 THE POSITIVE CONTROL'S PARTNER. A warner that fires unconditionally
        would pass the test above and teach the operator to ignore the line.
        """
        path = self._write(
            tmp_path,
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
            f"{DANA_TOKEN} dana {DENY_SCOPE}",
        )
        warnings: list[str] = []
        api.load_tokens(path, {}, warn=warnings.append)
        assert warnings == []


class TestEnumerationChannelsAreClosed:
    """🔴 FOUR CHANNELS, MEASURED ON THE DEPLOYED POD, EACH WITH ITS OWN TEST.

    A per-route "is this scope yours" check would close NONE of the first three:
    they all fire on requests for a scope the caller IS allowed, or on a request
    that names no scope at all. Every test here drives `zach`, whose allowlist
    is `ALLOW_SCOPE` alone, and asserts on the names and CONTENT of the two
    scopes he may not see.
    """

    def test_CHANNEL_1_known_scopes_names_only_the_callers_own(
        self, scoped_store: Path
    ):
        """`scope-absent` renders "scopes the store does hold: …". At base that
        sentence enumerated the whole store to anybody holding any token.
        """
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{PHANTOM_SCOPE}", token=ZACH_TOKEN
            )
        assert code == 200
        assert headers["X-Store-Status"] == "scope-absent"
        text = body.decode()
        assert ALLOW_SCOPE in text, "the caller's own scope vanished — over-filtered"
        assert DENY_SCOPE not in text
        assert THIRD_SCOPE not in text

    def test_CHANNEL_2_malformed_elsewhere_names_no_denied_scope(
        self, scoped_store: Path
    ):
        """🔴 THE CHANNEL THAT FIRES ON A SUCCESSFUL READ. The "(+N further
        malformed entries in OTHER scopes …)" block is rendered on EVERY status,
        so this leaks on a perfectly ordinary 200 for a scope the caller owns —
        no miss, nothing refused, nothing to notice.
        """
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=ZACH_TOKEN
            )
        assert code == 200
        assert headers["X-Store-Status"] == "recalled"
        text = body.decode()
        assert KELP_NUANCE in text, "the caller's own content vanished"
        assert DENY_SCOPE not in text
        # ⚠ `assert "broken-shard" not in text` WAS HERE AND HAS BEEN DELETED AS
        # VACUOUS, not moved. `render_malformed` emits `elsewhere` as a COUNT
        # with its scopes named and DELIBERATELY never a filename ("naming the
        # scopes makes it actionable without putting another scope's filenames,
        # which are client-identifying, on this screen"). So no filename from
        # another scope is rendered at base OR at HEAD, for ANY token — the
        # assertion could not have failed and was reading as coverage while
        # providing none. The line above is the real guard for this channel; the
        # filename half is pinned where it CAN fail, in the positive control
        # below, which drives the reading that does render the block.
    def test_CHANNEL_2_POSITIVE_CONTROL_a_legacy_token_DOES_see_it(
        self, scoped_store: Path
    ):
        """🔴 WITHOUT THIS THE TEST ABOVE IS A ZERO FROM A CHECK THAT MIGHT SEE
        NOTHING. The fixture must actually PRODUCE a `malformed_elsewhere` block
        naming the denied scope, or "the name is absent" is satisfied by a
        renderer that never emits the block at all.
        """
        with running(scoped_store, tokens=(GOOD_TOKEN,)) as (base, _):
            _c, _h, body = fetch(
                f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=GOOD_TOKEN
            )
        text = body.decode()
        assert DENY_SCOPE in text, (
            "the fixture produced no cross-scope malformed block, so the "
            "negative assertion above proves nothing"
        )
        # 🔴 AND THE BLOCK IS A COUNT, NOT A ROW — pinned HERE because this is
        # the only reading in which the block is rendered at all, so it is the
        # only place the claim can fail. `render_malformed`'s contract is that a
        # scope OUTSIDE the one being recalled contributes its name and a number
        # and never a filename, because filenames are client-identifying. An
        # unrestricted caller is the widest reading there is: if `broken-shard`
        # is absent from THIS body, no narrower caller can see it either.
        assert "broken-shard" not in text
        assert "1 further malformed entry in OTHER scopes" in text

    def test_CHANNEL_3_all_scopes_search_narrows_to_the_callers_own(
        self, scoped_store: Path
    ):
        """🔴 THE ONE A PER-SCOPE CHECK STRUCTURALLY CANNOT COVER. `?all_scopes=1`
        NAMES NO SCOPE, so there is nothing for such a check to refuse — it
        searches the CONTENT of every scope in the store.
        """
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, _h, body = fetch(
                f"{base}/api/v1/search/{ALLOW_SCOPE}?q=drill+head+overheats"
                f"&all_scopes=1",
                token=ZACH_TOKEN,
            )
        assert code == 200
        text = body.decode()
        assert QUARTZ_NUANCE not in text
        assert DENY_SCOPE not in text
        assert THIRD_SCOPE not in text
        # 🔴 NARROWED IS NOT EMPTIED, AND WITHOUT THESE THREE THE TEST ABOVE IS
        # SATISFIED BY A SEARCH THAT LOOKED AT NOTHING. Measured: with
        # `scopes_searched` forced to `()` the body renders "searched 0 entries
        # in (none)" — which contains no denied scope name and no denied content,
        # so every assertion above passes while the feature is entirely inert.
        #
        # The caller's OWN scope must be named, the placeholder must be absent,
        # and the COUNT must have moved off zero. Three independent facts,
        # because a renderer could satisfy any one of them by accident.
        assert ALLOW_SCOPE in text, "the caller's own scope was not searched"
        assert "(none)" not in text
        assert "searched 1 entry" in text

    def test_CHANNEL_3_POSITIVE_CONTROL_a_legacy_token_DOES_find_it(
        self, scoped_store: Path
    ):
        """The query has to be one that HITS, or the narrowed search is
        indistinguishable from a query nothing matches.
        """
        with running(scoped_store, tokens=(GOOD_TOKEN,)) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/search/{ALLOW_SCOPE}?q=drill+head+overheats"
                f"&all_scopes=1",
                token=GOOD_TOKEN,
            )
        assert code == 200
        assert headers["X-Store-Status"] == "search-hit"
        assert QUARTZ_NUANCE in body.decode()

    def test_CHANNEL_4_the_snapshot_tar_carries_only_allowed_members(
        self, scoped_store: Path
    ):
        """🔴 ASSERTED OVER EXTRACTED MEMBER NAMES, NOT A STATUS CODE. This route
        never builds an index, so the filter that closes channels 1-3 does not
        reach it; a 200 here says nothing about what is inside the archive.
        """
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, body = fetch(f"{base}/api/v1/snapshot", token=ZACH_TOKEN)
        assert code == 200
        with tarfile.open(fileobj=io.BytesIO(body), mode="r") as tar:
            names = sorted(tar.getnames())
        assert names == [f"{ALLOW_SCOPE}/{ALLOW_SCOPE}-entry.md"], names
        # The server's own count must describe the same filtered set, or
        # `cairn::install_snapshot`'s mismatch check refuses every scoped pull.
        assert headers["X-Store-Entries"] == "1"

    def test_CHANNEL_4_POSITIVE_CONTROL_a_legacy_token_gets_every_member(
        self, scoped_store: Path
    ):
        with running(scoped_store, tokens=(GOOD_TOKEN,)) as (base, _):
            code, headers, body = fetch(f"{base}/api/v1/snapshot", token=GOOD_TOKEN)
        assert code == 200
        with tarfile.open(fileobj=io.BytesIO(body), mode="r") as tar:
            names = sorted(tar.getnames())
        assert names == sorted(
            [f"{s}/{s}-entry.md" for s in (ALLOW_SCOPE, DENY_SCOPE, THIRD_SCOPE)]
            + [f"{DENY_SCOPE}/broken-shard.md"]
        ), names
        assert headers["X-Store-Entries"] == "4"

    def test_a_scope_FILTERED_snapshot_of_a_denied_scope_ships_nothing(
        self, scoped_store: Path
    ):
        """`?scope=` reaches the filesystem directly, so it is its own door into
        the same store — and it must answer for a denied scope exactly what it
        answers for one that never existed.

        🔴 COMPARED AS BYTES AND AS A HEADER SET, the way
        `TestRefusedIsIndistinguishableFromAbsent` compares its pair. This test
        used to check three NAMED facts — the code, one header and the member
        list — which is a claim about the three things somebody thought of. The
        docstring promises "exactly what it answers", and exactly is a byte
        comparison: a fourth `X-Store-*` header, or a differing tar footer, would
        discriminate a refused scope from an absent one while every named
        assertion stayed green.
        """
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            denied = fetch(
                f"{base}/api/v1/snapshot?scope={DENY_SCOPE}", token=ZACH_TOKEN
            )
            phantom = fetch(
                f"{base}/api/v1/snapshot?scope={PHANTOM_SCOPE}", token=ZACH_TOKEN
            )

        # 🔴 THE WHOLE RESPONSE, VIA `_comparable` — every header but `Date`,
        # not just the `X-Store-*` subset. A local helper here narrowed the
        # comparison to the family somebody expected the leak to be in, which is
        # a guard narrower than its own docstring: `ETag`, `Content-Length` and
        # anything a future header carries would discriminate a refused scope
        # from an absent one with every named assertion still green.
        assert denied[0] == phantom[0] == 200
        assert _comparable(denied[1]) == _comparable(phantom[1]), (
            f"headers differ:\n denied ={_comparable(denied[1])}\n"
            f" phantom={_comparable(phantom[1])}"
        )
        assert denied[2] == phantom[2], (
            "the tar BYTES differ — a refused scope is distinguishable from an "
            f"absent one:\n denied ={denied[2]!r}\n phantom={phantom[2]!r}"
        )
        # 🔴 AND THE SHARED ANSWER IS THE EMPTY ARCHIVE, not two identical
        # errors: byte-identity between two 503s would satisfy everything above
        # while serving nothing. Same reasoning as the recall/search pair.
        assert denied[1]["X-Store-Entries"] == "0"
        for body in (denied[2], phantom[2]):
            with tarfile.open(fileobj=io.BytesIO(body), mode="r") as tar:
                assert tar.getnames() == []

    def test_TWO_TOKENS_ON_ONE_SERVER_each_see_only_their_own(
        self, scoped_store: Path
    ):
        """🔴 THE SEAM, NOT THE COMPONENT. Both records are configured on ONE
        server, so this fails if the allowlist is resolved from anything other
        than the record that authenticated THIS request — a module-level cache,
        the first configured row, or a value left on the handler by the previous
        request on a keep-alive connection.
        """
        with running(scoped_store, tokens=(ZACH, DANA)) as (base, audit):
            # 🔴 The waits are INTERLEAVED because the read below is POSITIONAL:
            # `await_audit` guarantees a count, never an order. See its docstring.
            zach_own = fetch(f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=ZACH_TOKEN)
            await_audit(audit, 1)
            zach_other = fetch(f"{base}/api/v1/recall/{DENY_SCOPE}", token=ZACH_TOKEN)
            await_audit(audit, 2)
            dana_own = fetch(f"{base}/api/v1/recall/{DENY_SCOPE}", token=DANA_TOKEN)
            await_audit(audit, 3)
            dana_other = fetch(f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=DANA_TOKEN)
            lines = await_audit(audit, 4)

        assert zach_own[1]["X-Store-Status"] == "recalled"
        assert KELP_NUANCE.encode() in zach_own[2]
        assert dana_own[1]["X-Store-Status"] == "recalled"
        assert QUARTZ_NUANCE.encode() in dana_own[2]
        # …and each is told the OTHER's scope does not exist.
        assert zach_other[1]["X-Store-Status"] == "scope-absent"
        assert dana_other[1]["X-Store-Status"] == "scope-absent"
        assert QUARTZ_NUANCE.encode() not in zach_other[2]
        assert KELP_NUANCE.encode() not in dana_other[2]
        # The audit line says WHOSE request each was, which is the only record
        # that can answer "who read what" after the fact.
        assert "identity=zach" in lines[0] and "identity=zach" in lines[1]
        assert "identity=dana" in lines[2] and "identity=dana" in lines[3]

    def test_the_audit_line_still_carries_the_FINGERPRINT_not_only_the_identity(
        self, scoped_store: Path
    ):
        """🔴 `identity=` is ADDITIVE. Overlap rotation is checkable only through
        `token=`: two rows can hold one holder's current and previous credential,
        and the identity cannot tell them apart.
        """
        with running(scoped_store, tokens=(ZACH,)) as (base, audit):
            fetch(f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=ZACH_TOKEN)
            line = await_audit(audit, 1)[0]
        assert f"token={api.token_id(ZACH_TOKEN)}" in line
        assert "identity=zach" in line
        assert "auth=ok" in line
        assert ZACH_TOKEN not in line

    def test_a_REJECTED_request_names_no_identity(self, scoped_store: Path):
        with running(scoped_store, tokens=(ZACH,)) as (base, audit):
            fetch(f"{base}/api/v1/recall/{ALLOW_SCOPE}", token="w" * 48)
            line = await_audit(audit, 1)[0]
        assert "identity=-" in line
        assert "auth=fail" in line


class TestRefusedIsIndistinguishableFromAbsent:
    """🔴 CRITERION 3, PROVEN RATHER THAN ASSUMED — and the comparison is built
    so that scope EXISTENCE is the only thing that varies.

    Two responses to `recall/<DENY_SCOPE>` from the SAME token at the SAME store
    path: once when that scope holds an entry, once when it never existed. The
    entry-file COUNT and the newest mtime are held constant across the two (the
    scope is rebuilt under a different name), because `X-Store-Snapshot` is
    store-wide and would otherwise differ for a reason that is not the one under
    test. See the module docstring's residual-leak note.
    """

    def _phases(self, tmp_path: Path):
        """Yields a builder for phase A (denied scope present) and phase B (it
        never existed), both at ONE path so `  store: <root>` cannot differ."""
        root = tmp_path / "store"

        def present():
            if root.exists():
                shutil.rmtree(root)
            return _build_store(
                root, {ALLOW_SCOPE: KELP_NUANCE, DENY_SCOPE: QUARTZ_NUANCE}
            )

        def absent():
            shutil.rmtree(root)
            # Same file COUNT, same mtimes, different scope name — so the only
            # fact that moved is whether DENY_SCOPE is on disk.
            return _build_store(
                root, {ALLOW_SCOPE: KELP_NUANCE, THIRD_SCOPE: LANTERN_NUANCE}
            )

        return root, present, absent

    def _ask(self, root: Path, token, path: str):
        with running(root, tokens=(token,)) as (base, _):
            code, headers, body = fetch(
                f"{base}{path}",
                token=token.token if hasattr(token, "token") else token,
            )
        # 🔴 THE WHOLE RESPONSE, VIA `_comparable`. This narrowed to the
        # `X-Store-*` family, which is a guard narrower than its own docstring:
        # `ETag`, `Content-Length` or any header added later would discriminate a
        # refused scope from an absent one with every assertion still green.
        return code, _comparable(headers), body

    def test_RECALL_a_refused_scope_is_BYTE_IDENTICAL_to_one_that_never_existed(
        self, tmp_path: Path
    ):
        root, present, absent = self._phases(tmp_path)
        present()
        refused = self._ask(root, ZACH, f"/api/v1/recall/{DENY_SCOPE}")
        absent()
        never = self._ask(root, ZACH, f"/api/v1/recall/{DENY_SCOPE}")

        assert refused[0] == never[0] == 200
        assert refused[1] == never[1], (
            f"X-Store-* headers differ:\n refused={refused[1]}\n absent ={never[1]}"
        )
        assert refused[2] == never[2], (
            "response bodies differ — a refused scope is distinguishable from an "
            "absent one:\n"
            f"refused: {refused[2]!r}\nabsent : {never[2]!r}"
        )
        # 🔴 And the shared answer is the ABSENT report, not two identical
        # errors: byte-identity between two 401s or two 503s would satisfy
        # everything above while serving nothing.
        assert dict(refused[1])["x-store-status"] == "scope-absent"
        assert b"NOTHING RECORDED YET" in refused[2].upper()
        assert QUARTZ_NUANCE.encode() not in refused[2]

    SEARCH_QUERY = f"/api/v1/search/{DENY_SCOPE}?q=drill+head+overheats"

    def test_SEARCH_a_refused_scope_is_BYTE_IDENTICAL_to_one_that_never_existed(
        self, tmp_path: Path
    ):
        root, present, absent = self._phases(tmp_path)
        present()
        refused = self._ask(root, ZACH, self.SEARCH_QUERY)
        absent()
        never = self._ask(root, ZACH, self.SEARCH_QUERY)

        assert refused[0] == never[0] == 200
        assert refused[1] == never[1], f"{refused[1]} != {never[1]}"
        assert refused[2] == never[2]
        assert dict(refused[1])["x-store-status"] == "scope-absent"

    def test_POSITIVE_CONTROL_the_RECALL_comparison_CAN_see_the_difference(
        self, tmp_path: Path
    ):
        """🔴 WITHOUT THIS, THE RECALL TEST ABOVE IS SATISFIED BY A SERVER THAT
        ANSWERS THE SAME BYTES TO EVERYTHING.

        The same two phases, driven by an UNRESTRICTED legacy token: present ->
        `recalled` with the entry's content, absent -> `scope-absent`. If this
        pair did not differ, the equality above would be measuring the harness
        rather than the fix.
        """
        root, present, absent = self._phases(tmp_path)
        present()
        seen = self._ask(root, GOOD_TOKEN, f"/api/v1/recall/{DENY_SCOPE}")
        absent()
        gone = self._ask(root, GOOD_TOKEN, f"/api/v1/recall/{DENY_SCOPE}")

        assert dict(seen[1])["x-store-status"] == "recalled"
        assert dict(gone[1])["x-store-status"] == "scope-absent"
        assert seen[2] != gone[2]
        assert QUARTZ_NUANCE.encode() in seen[2]

    def test_POSITIVE_CONTROL_the_SEARCH_comparison_CAN_see_the_difference(
        self, tmp_path: Path
    ):
        """🔴 THE SEARCH PATH HAD NO POSITIVE CONTROL AT ALL, and the recall one
        does not cover it: they are different routes, different renderers and
        different statuses.

        An equality between two responses is only evidence if the pair CAN
        differ. This server fail-closes to an EMPTY body without
        `SUBSYSTEM_STORE_TRUSTED_PROXIES` and a `CF-Connecting-IP` header, and
        two empty bodies compare identical — so "byte-identical" is exactly the
        assertion a broken harness satisfies best. Same two phases, same query,
        an UNRESTRICTED token: present -> `search-hit` carrying the matched
        nuance, absent -> `scope-absent`. Both non-empty, and different.
        """
        root, present, absent = self._phases(tmp_path)
        present()
        found = self._ask(root, GOOD_TOKEN, self.SEARCH_QUERY)
        absent()
        gone = self._ask(root, GOOD_TOKEN, self.SEARCH_QUERY)

        assert found[0] == gone[0] == 200
        assert dict(found[1])["x-store-status"] == "search-hit"
        assert dict(gone[1])["x-store-status"] == "scope-absent"
        assert found[2] != gone[2]
        assert QUARTZ_NUANCE.encode() in found[2]
        # …and neither body is the empty string, which is what a fail-closed
        # server returns and what would make the equality above vacuous.
        assert found[2] and gone[2]


class TestScopeRevisionIsGatedByConstruction:
    """🔴 THE ONE HEADER THE INDEX FILTER CANNOT REACH.

    `X-Store-Revision` is read off `<store>/<scope>/.git/HEAD`, a path the index
    knows nothing about. Today no scope in the served copy is a git repo, so it
    answers "unknown" for everything and the leak is LATENT — which is exactly
    the state in which a guard gets skipped. So the fixture MAKES a denied scope
    a real repo and asserts the header still cannot tell it from an absent one.
    """

    def _git(self, path: Path, *args: str):
        subprocess.run(
            ["git", "-C", str(path), *args],
            check=True,
            capture_output=True,
            env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "HOME": str(path),
                 **hermetic_git.MAINTENANCE_OFF},
        )

    def _repo(self, store: Path, scope: str) -> str:
        scope_dir = store / scope
        self._git(scope_dir, "init", "-q", "-b", "main")
        self._git(scope_dir, "config", "user.email", "t@example.invalid")
        self._git(scope_dir, "config", "user.name", "T")
        self._git(scope_dir, "add", f"{scope}-entry.md")
        self._git(scope_dir, "commit", "-qm", "seed")
        return subprocess.run(
            ["git", "-C", str(scope_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def test_POSITIVE_CONTROL_the_fixture_really_is_a_repo_and_the_header_shows_it(
        self, scoped_store: Path
    ):
        """A "the header said unknown" assertion is worthless against a fixture
        that could never have produced a sha. This proves it could.
        """
        head = self._repo(scoped_store, DENY_SCOPE)
        assert len(head) == 40
        assert api.scope_revision(scoped_store, DENY_SCOPE) == head
        with running(scoped_store, tokens=(GOOD_TOKEN,)) as (base, _):
            _c, headers, _b = fetch(
                f"{base}/api/v1/recall/{DENY_SCOPE}", token=GOOD_TOKEN
            )
        assert headers["X-Store-Revision"] == head

    def test_a_DENIED_scopes_revision_is_unknown_even_though_it_HAS_one(
        self, scoped_store: Path
    ):
        head = self._repo(scoped_store, DENY_SCOPE)
        assert api.scope_revision(
            scoped_store, DENY_SCOPE, visible_scopes=(ALLOW_SCOPE,)
        ) == "unknown"
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            _c, denied, _b = fetch(
                f"{base}/api/v1/recall/{DENY_SCOPE}", token=ZACH_TOKEN
            )
            _c2, phantom, _b2 = fetch(
                f"{base}/api/v1/recall/{PHANTOM_SCOPE}", token=ZACH_TOKEN
            )
        assert denied["X-Store-Revision"] == "unknown"
        assert denied["X-Store-Revision"] == phantom["X-Store-Revision"]
        assert head not in denied["X-Store-Revision"]

    def test_the_callers_OWN_scope_still_reports_its_sha(self, scoped_store: Path):
        """🔴 OVER-FILTERING IS ALSO A FAILURE. A gate that answered "unknown"
        for everything would pass the test above and silently delete the
        determinism guarantee `scope@sha` exists for.
        """
        head = self._repo(scoped_store, ALLOW_SCOPE)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            _c, headers, _b = fetch(
                f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=ZACH_TOKEN
            )
        assert headers["X-Store-Revision"] == head

    def test_visible_scopes_None_is_UNRESTRICTED_matching_every_other_seam(
        self, scoped_store: Path
    ):
        head = self._repo(scoped_store, DENY_SCOPE)
        assert api.scope_revision(scoped_store, DENY_SCOPE, visible_scopes=None) == head
        # …and an EMPTY sequence is the opposite, not a synonym for None.
        assert api.scope_revision(
            scoped_store, DENY_SCOPE, visible_scopes=()
        ) == "unknown"


class TestTheReaderNarrowingItself:
    """`load_store`'s `visible_scopes`, unit-level — the one site both readers
    take their index from, so this is where over- and under-filtering show up
    without an HTTP layer in the way.
    """

    def test_None_is_unrestricted_and_an_EMPTY_SEQUENCE_is_the_opposite(
        self, scoped_store: Path
    ):
        """🔴 THE ASYMMETRY, PINNED. `None` and `()` are both falsy, so a guard
        written `if visible_scopes:` would treat an empty allowlist as
        unrestricted — a total bypass that every functional test with a
        populated allowlist would pass.
        """
        _s, wide = api.rc.load_store(scoped_store, verb="recalled")
        assert set(wide.scopes) == {ALLOW_SCOPE, DENY_SCOPE, THIRD_SCOPE}
        _s, none_visible = api.rc.load_store(
            scoped_store, verb="recalled", visible_scopes=()
        )
        assert none_visible.scopes == ()
        assert len(none_visible) == 0
        assert none_visible.malformed == ()

    def test_the_MALFORMED_tuple_is_narrowed_beside_by_scope(
        self, scoped_store: Path
    ):
        """Both public fields, or `malformed_outside` still names a denied scope
        while `scopes` does not — the half-fix that reads as a whole one.
        """
        _s, wide = api.rc.load_store(scoped_store, verb="recalled")
        assert [m.scope for m in wide.malformed] == [DENY_SCOPE]
        _s, narrow = api.rc.load_store(
            scoped_store, verb="recalled", visible_scopes=(ALLOW_SCOPE,)
        )
        assert narrow.scopes == (ALLOW_SCOPE,)
        assert narrow.malformed == ()
        assert narrow.malformed_outside((ALLOW_SCOPE,)) == ()

    def test_an_allowlist_entry_is_normalised_against_the_index_key(
        self, scoped_store: Path
    ):
        _s, narrow = api.rc.load_store(
            scoped_store, verb="recalled", visible_scopes=("Kelp_Forest",)
        )
        assert narrow.scopes == (ALLOW_SCOPE,)

    def test_a_MISSING_store_still_RAISES_rather_than_narrowing_to_empty(
        self, tmp_path: Path
    ):
        """🔴 THE FOUR-STATE RULE SURVIVES THE FILTER. `store-unreachable` and
        "you may see nothing" both produce an empty index; only the first may be
        a raise, and collapsing them is the exact conflation this whole module
        exists to prevent. A filter applied BEFORE the load would have done it.

        ⚠ RENAMED. This used to be called `test_an_UNREADABLE_store_still_RAISES…`
        and passes a store root that does not EXIST — a different condition, a
        different error type and a different line of `load_store`. The
        genuinely-unreadable path is `TestUnreadableEntriesInDeniedScopes` below,
        which is where the interesting behaviour is.
        """
        with pytest.raises(api.rc.StoreMissingError):
            api.rc.load_store(
                tmp_path / "absent", verb="recalled", visible_scopes=(ALLOW_SCOPE,)
            )

    def test_recall_and_search_BOTH_take_the_narrowing(self, scoped_store: Path):
        """One kwarg, two callers — and a threading bug that reached only one of
        them would leave `/search` wide open while `/recall` looked fixed.
        """
        rep = api.rc.recall(
            scoped_store, DENY_SCOPE, visible_scopes=(ALLOW_SCOPE,)
        )
        assert rep.status == "scope-absent"
        assert rep.known_scopes == (ALLOW_SCOPE,)
        sea = api.rc.search(
            scoped_store, ALLOW_SCOPE, "drill head overheats",
            all_scopes=True, visible_scopes=(ALLOW_SCOPE,),
        )
        assert sea.scopes_searched == (ALLOW_SCOPE,)
        assert sea.known_scopes == (ALLOW_SCOPE,)
        assert sea.total_hits == 0

    def test_the_CLI_DEFAULT_is_still_unrestricted(self, scoped_store: Path):
        """The local reader must not have acquired an allowlist by accident:
        `cairn` and `/resume` call these with no such argument and read the whole
        store, and a default of `()` here would empty every local recall.
        """
        assert api.rc.recall(scoped_store, DENY_SCOPE).status == "recalled"
        assert set(api.rc.recall(scoped_store, ALLOW_SCOPE).known_scopes) == {
            ALLOW_SCOPE, DENY_SCOPE, THIRD_SCOPE
        }


# The name in the 503 body an unreadable entry used to produce. Distinct from
# every other literal in this file, so "did this filename reach the caller" is a
# single `in` and cannot be satisfied by coincidence.
LOCKED_ENTRY = "sealed-adit.md"
# An Emacs lock file: a DANGLING symlink whose name starts with a dot and ends
# `.md`. `Path.glob("*.md")` matches a leading dot — measured, not assumed, and
# pinned by a test below — so it IS a candidate entry, and this exact shape has
# been observed 503ing `/api/v1/recall/<scope>` in practice.
EMACS_LOCK = ".#sealed-adit.md"


def _make_unreadable(
    store: Path, scope: str, kind: str, *, outside: Path | None = None
) -> Path:
    """Put ONE hostile candidate entry into `<store>/<scope>/`.

    `kind` is `perm` (a mode-000 regular file), `emacs` (the dangling lock-file
    symlink), `fifo` (a named pipe, which blocks `open()` until somebody
    writes), `dir` (a DIRECTORY named `*.md` — one stray `mkdir`, or an
    rsync/restore artefact) or `linkdir` (a symlink pointing at one, which needs
    `outside` to hold the real directory). All five are real shapes seen on a
    real store, not contrivances.
    """
    target = store / scope / (EMACS_LOCK if kind == "emacs" else LOCKED_ENTRY)
    if kind == "perm":
        target.write_text(_entry("sealed-adit", scope, nuance="- sealed."))
        os.chmod(target, 0o000)
    elif kind == "emacs":
        os.symlink("zach@host.4242:1767225600", target)
    elif kind == "fifo":
        os.mkfifo(target)
    elif kind == "dir":
        target.mkdir()
    elif kind == "linkdir":
        assert outside is not None, "`linkdir` needs somewhere to put the target"
        real = outside / "a-real-directory"
        real.mkdir(parents=True, exist_ok=True)
        os.symlink(real, target)
    else:  # pragma: no cover - a typo in a test argument is a test bug
        raise AssertionError(kind)
    return target


def under_deadline(source: str, seconds: float, *args: str):
    """Run `source` in a CHILD process. The completed process, or `None` meaning
    it blew the wall-clock deadline.

    🔴 EVERY TEST THAT READS A STORE HOLDING A FIFO MUST GO THROUGH HERE, AND
    THE REASON IS THAT THE ALTERNATIVE HAS NO FAILURE MODE. `read_text` on a
    fifo blocks forever: in-process there is no exception and no value to assert
    on, so the test does not fail — the RUNNER wedges. Measured on this file
    with `_LOADER_ENTRY_ACTIONS[KIND_OTHER]` reverted to `TAKE`:
    `test_the_REFUSED_entries_are_SURFACED_not_silently_dropped` ran **>600s**
    with no error and no failure (isolated, `rc=124` at 60s). No
    `pytest-timeout` plugin is installed — only `xdist` — so nothing above the
    test would have cut it off either.

    A test that hangs instead of failing is worse than no test: CI stops, and
    nobody gets a red to act on. `claude/RULES.md`: "a permanently-red gate is
    worse than no gate" — a permanently-HUNG one is worse still, because it does
    not even report.

    ⚠ ONE SPELLING, DELIBERATELY. This was a nested closure in two separate
    tests before, and a third test simply did without; the predicate-at-N-sites
    shape is how the third one came to be missing.
    """
    try:
        return subprocess.run(
            [sys.executable, "-c", source, *args],
            capture_output=True,
            text=True,
            timeout=seconds,
        )
    except subprocess.TimeoutExpired:
        return None


def _load_store_probe(store: Path, *, expr: str = "None") -> str:
    """A child-process program that loads `store` and prints what it found.

    `expr` is evaluated in the child to produce `visible_scopes`, so the scoped
    and unrestricted arms share one program instead of two that could drift.
    """
    return (
        "import sys;"
        f"sys.path.insert(0, {str(RECALL_PATH.parent)!r});"
        "import subsystem_recall as rc;"
        f"vs = {expr};"
        f"_s, i = rc.load_store({str(store)!r}, verb='recalled', visible_scopes=vs);"
        "print('SCOPES=' + ','.join(i.scopes));"
        "print('MALFORMED=' + ','.join(m.label for m in i.malformed));"
        "print('REASONS=' + '|'.join(m.reason for m in i.malformed));"
        "print('PERSCOPE=' + ','.join("
        "    '%s:%d' % (s, len(i.entries(s))) for s in i.scopes))"
    )


def _load_index_raise_probe(store: Path) -> str:
    """A child-process program that loads `store` under `RAISE` and prints the
    exception class, its `source` and its message — the facts the in-process
    `pytest.raises` used to assert, in a form a wall-clock deadline can bound.
    """
    return (
        "import sys;"
        f"sys.path.insert(0, {str(RECALL_PATH.parent)!r});"
        "import subsystem_recall as rc;"
        "\ntry:\n"
        f"    rc.load_index({str(store)!r})\n"
        "except BaseException as exc:\n"
        "    print('CLASS=' + type(exc).__name__)\n"
        "    print('SOURCE=' + str(getattr(exc, 'source', None)))\n"
        "    print('MESSAGE=' + str(exc).replace(chr(10), ' '))\n"
        "else:\n"
        "    print('CLASS=NOTHING-RAISED')\n"
    )


def _probe_field(stdout: str, key: str) -> str:
    """The one `KEY=value` line a probe printed, or an assertion naming what it
    printed instead — never a silent empty string, which would satisfy most of
    the comparisons it feeds.
    """
    for line in stdout.splitlines():
        if line.startswith(f"{key}="):
            return line[len(key) + 1 :]
    raise AssertionError(f"the probe printed no {key}= line: {stdout[:600]!r}")


class TestTheLoaderItselfTakesTheAllowlist:
    """🔴 `load_index` DIRECTLY — the surface `load_store` hides.

    `load_store` narrows its RESULT as well, so through that door three separate
    mutations of the loader's own filter are invisible: an empty allowlist read
    as unrestricted, a denied scope's NAME registered before the skip, and a
    directory name compared unfolded. Each was measured SURVIVING a sweep that
    only drove `load_store`, and each is a real defect for the OTHER callers of
    this function — `subsystem_touch`, and anything that hands the result
    somewhere the post-filter is not.

    The seam guard is the pair: these tests plus the `load_store` ones above.
    Neither half alone pins the loader.
    """

    def _store(self, tmp_path: Path, *scope_dirs: str) -> Path:
        """A store whose scope DIRECTORY NAMES are exactly as given.

        Spelled by hand rather than through `_build_store`, because one test
        below needs a directory whose name does NOT equal its own folded form
        and that fixture is the whole point of it.
        """
        root = tmp_path / "store"
        for name in scope_dirs:
            (root / name).mkdir(parents=True)
            (root / name / f"{name}-entry.md").write_text(
                _entry(f"{name}-entry", name, nuance=f"- 2026-03-04: {name} drifts.")
            )
        return root

    def test_POSITIVE_CONTROL_no_allowlist_loads_every_scope(self, tmp_path: Path):
        """Without this the three tests below are satisfied by a loader that
        returns an empty index for everything.
        """
        root = self._store(tmp_path, ALLOW_SCOPE, DENY_SCOPE)
        index = api.rc.load_index(root, on_malformed="collect")
        assert set(index.scopes) == {ALLOW_SCOPE, DENY_SCOPE}

    def test_an_EMPTY_allowlist_registers_NO_scope_not_EVERY_scope(
        self, tmp_path: Path
    ):
        """🔴 THE ASYMMETRY, PINNED AT THE LOADER TOO. `None` and `()` are both
        falsy, so a filter written `if allowed and …` treats "you may see
        nothing" as "you may see everything" — a total bypass, and one that every
        test with a POPULATED allowlist passes.

        `load_store` re-narrows its result, which is why this mutation survives
        entirely when measured through that door. Here there is nothing in the
        way.
        """
        root = self._store(tmp_path, ALLOW_SCOPE, DENY_SCOPE)
        index = api.rc.load_index(root, on_malformed="collect", visible_scopes=())
        assert index.scopes == ()
        assert len(index) == 0

    def test_a_denied_scopes_NAME_is_not_registered_either(self, tmp_path: Path):
        """🔴 SKIPPING THE READ IS NOT SKIPPING THE SCOPE. A filter placed one
        line too late still appends the directory name to `extra_scopes`, so the
        denied scope arrives on `index.scopes` — the `known_scopes` enumeration
        channel — having never been opened. It reads as fixed and leaks the one
        fact the channel was about: that the scope EXISTS.

        Invisible through `load_store`, which drops the key again on the way out.
        """
        root = self._store(tmp_path, ALLOW_SCOPE, DENY_SCOPE)
        index = api.rc.load_index(
            root, on_malformed="collect", visible_scopes=(ALLOW_SCOPE,)
        )
        assert index.scopes == (ALLOW_SCOPE,)

    def test_the_DIRECTORY_NAME_is_FOLDED_before_it_is_compared(
        self, tmp_path: Path
    ):
        """🔴 OVER-FILTERING, AND THE FIXTURE HAS TO REACH IT. Every other store
        in this file has directory names that are already their own folded form,
        so a filter comparing the RAW `scope_dir.name` behaves identically and
        the mutation survives. Here the directory really is spelled
        `Kelp_Forest`: the index key it produces is `kelp-forest`, so an
        allowlist naming `kelp-forest` must still match it, and an unfolded
        comparison silently empties the caller's OWN scope.
        """
        raw_dir = "Kelp_Forest"
        assert api.rc.normalize_ref(raw_dir) == ALLOW_SCOPE != raw_dir, (
            "the fixture directory must NOT already equal its folded form, or "
            "this test measures nothing"
        )
        root = self._store(tmp_path, raw_dir, DENY_SCOPE)
        index = api.rc.load_index(
            root, on_malformed="collect", visible_scopes=(ALLOW_SCOPE,)
        )
        assert index.scopes == (ALLOW_SCOPE,)
        assert len(index) == 1


class TestUnreadableEntriesInDeniedScopes:
    """🔴 THE INDEX LOADER USED TO OPEN EVERY FILE IN THE STORE BEFORE THE
    ALLOWLIST WAS APPLIED, AND SCOPED CALLERS MADE THAT THREE DEFECTS AT ONCE.

    `load_store` narrowed the index it got BACK; `load_index` had already walked
    and read the whole store to build it. So one unreadable entry in a scope a
    caller may not see:

      * put that file's FULL PATH — and therefore the denied scope's name — into
        a 503 body that caller could read. A scoped reader had no other way to
        learn the name existed, which is the exact enumeration surface the rest
        of this section closes;
      * broke `/recall` and `/search` for EVERY caller, including the ones whose
        own scopes were perfectly readable;
      * and for a FIFO named `*.md`, blocked the request thread on `open()`
        indefinitely. `/snapshot` already refused that by kind
        (`_ENTRY_ACTIONS[KIND_OTHER] == REFUSE`); the index path did not.

    Fixed in TWO steps, and the split matters because they cover different
    callers:

      1. `visible_scopes` pushed DOWN into `load_index`, so a denied scope dir
         is never descended into at all. Protects a SCOPED caller only.
      2. an entry-KIND check before `open()`
         (`subsystem_resolver._LOADER_ENTRY_ACTIONS`), which protects EVERY
         caller including the unrestricted bare legacy token the pod runs.

    🔴 AND THE LIMIT OF (2) IS TESTED TOO, not just stated: it is NARROW. A
    `chmod 000` regular file still raises for an unrestricted caller —
    `test_an_UNREADABLE_REGULAR_FILE_still_RAISES_and_THAT_is_the_residual` is
    the honest record of ONE thing that did not close. ⚠ It is not the only one
    and must not be read as such: the full list is the module-level
    `LOADER_RESIDUAL_KINDS`, pinned against the table by
    `test_the_LOADER_RESIDUAL_SET_is_pinned` and against both documents that
    describe it by the two `RESIDUAL LEDGER` guards beside it.
    """

    def _store(self, tmp_path: Path, kind: str) -> Path:
        store = _build_store(
            tmp_path / "store",
            {ALLOW_SCOPE: KELP_NUANCE, DENY_SCOPE: QUARTZ_NUANCE},
        )
        _make_unreadable(store, DENY_SCOPE, kind)
        return store

    def test_the_FIXTURE_really_is_a_candidate_entry_glob_matches_a_dotfile(
        self, tmp_path: Path
    ):
        """🔴 THE POSITIVE CONTROL FOR THE EMACS SHAPE. If `glob("*.md")` did not
        match a leading dot, `.#sealed-adit.md` would never be opened, every
        assertion about it would be vacuous, and the whole case would be a story
        about a file the loader never sees.
        """
        d = tmp_path / "scope"
        d.mkdir()
        os.symlink("dangling", d / EMACS_LOCK)
        assert [p.name for p in d.glob("*.md")] == [EMACS_LOCK]

    @pytest.mark.parametrize("kind", ["perm", "emacs"])
    def test_a_SCOPED_caller_is_unaffected_by_an_unreadable_DENIED_entry(
        self, tmp_path: Path, kind: str
    ):
        store = self._store(tmp_path, kind)
        _s, index = api.rc.load_store(
            store, verb="recalled", visible_scopes=(ALLOW_SCOPE,)
        )
        assert index.scopes == (ALLOW_SCOPE,)
        assert index.malformed == ()

    def test_POSITIVE_CONTROL_the_same_file_in_the_CALLERS_OWN_scope_still_RAISES(
        self, tmp_path: Path
    ):
        """🔴 WITHOUT THIS THE TEST ABOVE IS SATISFIED BY A FIXTURE THAT CREATED
        NOTHING UNREADABLE. It also pins the half that must NOT change: the
        four-state rule. "I could not read your store" and "you may see nothing"
        both produce an empty index, and only the first may raise.

        ⚠ `perm` ONLY, and the parametrize over `emacs` that used to be here was
        DELETED rather than left to fail: the entry-kind guard now refuses a
        broken link before `open()`, so that shape no longer raises for ANY
        caller. Its own-scope behaviour is asserted below, as a collected
        malformed row.
        """
        store = self._store(tmp_path, "perm")
        with pytest.raises(api.rc.EntryUnreadableError):
            api.rc.load_store(store, verb="recalled", visible_scopes=(DENY_SCOPE,))

    def test_the_BROKEN_LINK_in_the_CALLERS_OWN_scope_is_REPORTED_not_fatal(
        self, tmp_path: Path
    ):
        """The other side of the guard: refusing an entry must not silently
        empty the scope that holds it. The caller who OWNS the hostile file
        still gets their good entry, and still gets told about the bad one.
        """
        store = self._store(tmp_path, "emacs")
        _s, index = api.rc.load_store(
            store, verb="recalled", visible_scopes=(DENY_SCOPE,)
        )
        assert index.scopes == (DENY_SCOPE,)
        assert len(index) == 1
        assert [m.label for m in index.malformed] == [f"{DENY_SCOPE}/{EMACS_LOCK}"]

    def test_the_UNRESTRICTED_reading_of_a_BROKEN_LINK_no_longer_DIES(
        self, tmp_path: Path
    ):
        """🔴 THE INVERSION. This test used to be
        `test_the_UNRESTRICTED_reading_is_UNCHANGED_and_that_is_the_residual`
        and asserted `pytest.raises(EntryUnreadableError)` — the honest record
        of a residual the allowlist pushdown could not reach. The residual was
        CLOSED by the entry-kind guard, so the same input must now assert the
        opposite, and the name has to say which.

        `visible_scopes=None` still skips no SCOPE — that half is unchanged and
        is why this matters: the pod runs a bare legacy token, which is
        unrestricted. What changed is that the candidate's KIND is decided
        before `open()`, so the Emacs lock file costs its own entry and nothing
        else.
        """
        store = self._store(tmp_path, "emacs")
        _s, index = api.rc.load_store(store, verb="recalled")
        # The OTHER scope's content survived, which is the DoS half.
        assert set(index.scopes) == {ALLOW_SCOPE, DENY_SCOPE}
        assert len(index) == 2, "a good entry was dropped along with the bad one"
        # …and the bad entry is REPORTED, not silently skipped: a dropped entry
        # is indistinguishable from one nobody ever wrote, which is the exact
        # conflation this store guards against everywhere else.
        assert [m.label for m in index.malformed] == [f"{DENY_SCOPE}/{EMACS_LOCK}"]
        assert "broken symlink" in index.malformed[0].reason

    def test_an_UNREADABLE_REGULAR_FILE_still_RAISES_and_THAT_is_the_residual(
        self, tmp_path: Path
    ):
        """🔴 THE HALF THAT IS **NOT** CLOSED, kept as its own named test rather
        than left implied by the parametrize list this used to share.

        A `chmod 000` regular file classifies as `regular-file`, which the
        loader TAKES — deliberately. `read_text` then raises, and an OSError
        fails closed in both policies: "the store was not fully READ" is a
        different fact from "this entry is malformed", and only the second has
        an honest degraded form. So for THIS shape an unrestricted caller is
        exactly as exposed as before: 503, and the path in the body
        (`test_POSITIVE_CONTROL_a_LEGACY_token_DOES_still_get_the_503`).

        It is also the negative control for the test above: if the kind guard
        were quietly widened to refuse everything it could not read, this would
        go green-by-collapse and the four-state rule would be gone.
        """
        store = self._store(tmp_path, "perm")
        with pytest.raises(api.rc.EntryUnreadableError):
            api.rc.load_store(store, verb="recalled")

    def test_the_503_body_NAMED_the_denied_scope_and_its_PATH_over_HTTP(
        self, tmp_path: Path
    ):
        """🔴 THE DISCLOSURE ITSELF, DRIVEN THROUGH THE SERVER — the layer where
        it was a leak rather than an exception type.

        `zach` may see `ALLOW_SCOPE` only. He asks for his OWN scope, which is
        readable, and used to be answered `503 index entry unreadable: under …
        (PermissionError: … '<store>/quartz-mine/sealed-adit.md')`. Both halves
        matter: the status is a denial of service he did not cause, and the body
        names a scope and a filename he is not allowed to know exist.
        """
        store = self._store(tmp_path, "perm")
        with running(store, tokens=(ZACH,)) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=ZACH_TOKEN
            )
        text = body.decode()
        assert code == 200, f"{code}: {text[:400]}"
        assert headers["X-Store-Status"] == "recalled"
        assert KELP_NUANCE in text, "the caller's own content vanished"
        assert DENY_SCOPE not in text
        assert LOCKED_ENTRY not in text
        # …and therefore not the hostile file's path either. Spelled out because
        # the PATH is what the 503 carried and the two assertions above are only
        # its components.
        #
        # 🔴 NEITHER `"unreadable" not in text` NOR `str(store) not in text` WOULD
        # DO, and both were tried: the caveat block explains what
        # `unstamped/unreadable/none` mean on EVERY report, and the store ROOT is
        # printed on every report as `  store: <root>`. Both are present on a
        # perfectly healthy 200, so both would be red for a reason that has
        # nothing to do with this defect. What leaked was the scope name and the
        # filename UNDER the root, which is what is asserted.
        assert str(store / DENY_SCOPE / LOCKED_ENTRY) not in text

    def test_POSITIVE_CONTROL_a_LEGACY_token_DOES_still_get_the_503(
        self, tmp_path: Path
    ):
        """🔴 THE FIXTURE MUST ACTUALLY PRODUCE AN UNREADABLE ENTRY. Without this
        the assertions above are a zero from a check that might see nothing — a
        `chmod 000` that silently failed (running as root, an exotic filesystem)
        would leave every one of them green against a perfectly healthy store.

        It is also the honest record of the residual: unrestricted callers still
        get the 503, and still get the path in it.
        """
        store = self._store(tmp_path, "perm")
        with running(store, tokens=(GOOD_TOKEN,)) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=GOOD_TOKEN
            )
        assert code == 503, f"the fixture is readable after all: {code}"
        assert headers["X-Store-Status"] == "store-unreachable"
        text = body.decode()
        assert LOCKED_ENTRY in text and DENY_SCOPE in text

    def test_a_FIFO_named_md_in_a_DENIED_scope_no_longer_HANGS_the_reader(
        self, tmp_path: Path
    ):
        """🔴 THE MOST SERIOUS OF THE THREE, AND THE ONE A NORMAL TEST CANNOT
        ASSERT: a wedged thread produces no exception and no value, so there is
        nothing to `pytest.raises` on. It is measured in a CHILD PROCESS under a
        wall-clock deadline, because a test that can hang is a suite that can
        hang.

        On a `replicas: 1` Deployment an `open()` that never returns is worse
        than the 503: the worker is gone and the next request queues behind it.

        🔴 THE UNRESTRICTED ARM IS AN INVERSION. It used to be the NEGATIVE
        CONTROL — "the unrestricted load of the SAME store must still hang", the
        residual the allowlist pushdown could not reach. The entry-kind guard
        closed it, so the same probe must now COMPLETE, load both scopes, and
        report the FIFO as a malformed entry. A control that asserts a hazard is
        still live cannot survive the hazard being fixed; it has to be re-aimed
        or it becomes a test pinning the bug.

        What keeps the timeout meaningful instead is
        `test_a_SYMLINK_to_a_FIFO_no_longer_HANGS_and_the_DEADLINE_still_SEES_one`
        below. ⚠ That test's control had to be re-aimed too: it used to point at
        `link-to-other`, a kind the loader still TAKES — and it does not any
        more, because that shape was measured wedging a live request thread for
        25s. No remaining TAKE cell blocks, so the control is now a bare
        `open()` of the fifo itself, which is the syscall in question rather
        than a proxy for it.
        """
        store = self._store(tmp_path, "fifo")
        probe = _load_store_probe(
            store, expr=f"None if sys.argv[1] == 'unrestricted' else ({ALLOW_SCOPE!r},)"
        )

        def run(arg: str, deadline: float):
            return under_deadline(probe, deadline, arg)

        scoped = run("scoped", 30.0)
        assert scoped is not None, (
            "the scoped reader HUNG on a FIFO in a scope it may not see — the "
            "allowlist is not reaching `load_index`"
        )
        assert scoped.returncode == 0, scoped.stderr[-600:]
        assert f"SCOPES={ALLOW_SCOPE}\n" in scoped.stdout, scoped.stdout
        # The denied scope was never descended into, so its FIFO is not even
        # reported — step 1 still does its own job.
        assert "MALFORMED=\n" in scoped.stdout, scoped.stdout

        unrestricted = run("unrestricted", 15.0)
        assert unrestricted is not None, (
            "the UNRESTRICTED reader HUNG on the FIFO — the entry-kind guard is "
            "not reaching `load_index`, and on a `replicas: 1` Deployment that "
            "is a worker that never comes back"
        )
        assert unrestricted.returncode == 0, unrestricted.stderr[-600:]
        assert f"SCOPES={ALLOW_SCOPE},{DENY_SCOPE}\n" in unrestricted.stdout
        assert f"MALFORMED={DENY_SCOPE}/{LOCKED_ENTRY}\n" in unrestricted.stdout

    def test_a_SYMLINK_to_a_FIFO_no_longer_HANGS_and_the_DEADLINE_still_SEES_one(
        self, tmp_path: Path
    ):
        """🔴 THE SECOND INVERSION, AND THE PROBE'S OWN POSITIVE CONTROL, in one
        test — because the two have to move together.

        This test used to be
        `test_a_TAKEN_kind_still_HANGS_which_is_why_the_REFUSE_cells_exist`, and
        asserted that a symlink POINTING AT a fifo blocks the reader forever. It
        was the honest record of the `link-to-other` residual the narrow ruling
        left open, and simultaneously the positive control proving the deadline
        machinery can observe a hang at all.

        The residual was then MEASURED rather than reasoned about: on the tip
        that carried it, an unrestricted (bare legacy) `GET
        /api/v1/recall/<scope>` against a store holding one `link-to-fifo.md` in
        a DIFFERENT scope wedged for 25s and the request thread never came back,
        while `/healthz` answered 200 throughout — so the process was up and the
        worker was gone. `open()` blocks the same whether the fifo is reached
        directly or through a link. The cell is now REFUSE, so the same input
        must assert the opposite: complete, load both scopes, and REPORT the
        link as a malformed entry.

        🔴 AND THAT LEAVES A HOLE THIS TEST MUST FILL ITSELF. Every timing
        assertion in the class is now "it did NOT hang", which is the reassuring
        zero `claude/RULES.md` calls indistinguishable from a harness wired to
        nothing — and no kind the loader still TAKES blocks, so the old control
        cannot simply be re-aimed at another cell. The control below therefore
        drives the SAME deadline machinery at a bare `open()` of the SAME fifo,
        which must blow it. A fixture that made no real fifo, or a subprocess
        runner that never blocks, fails there and takes the vacuous green with
        it.
        """
        store = _build_store(
            tmp_path / "store",
            {ALLOW_SCOPE: KELP_NUANCE, DENY_SCOPE: QUARTZ_NUANCE},
        )
        real_fifo = tmp_path / "a-real-fifo"
        os.mkfifo(real_fifo)
        link = store / DENY_SCOPE / LOCKED_ENTRY
        os.symlink(real_fifo, link)
        assert api.classify_path(link) == api.KIND_LINK_TO_OTHER, (
            "the fixture is not the kind this test is about"
        )

        # 🔴 THE POSITIVE CONTROL FIRST, so a runner that cannot observe a block
        # fails before the assertion that depends on it means anything. A bare
        # `open()` of the fifo — no store, no loader — is the syscall the guard
        # exists to keep the request thread out of.
        blocked = under_deadline(
            f"open({str(real_fifo)!r}); print('OPENED')", 10.0
        )
        assert blocked is None, (
            "a bare `open()` of the fifo RETURNED, so this fixture is not a "
            "blocking fifo and the deadline below measures nothing — every "
            "'it did not hang' assertion in this class would be vacuous. "
            f"stdout={None if blocked is None else blocked.stdout!r}"
        )

        probe = (
            "import sys;"
            f"sys.path.insert(0, {str(RECALL_PATH.parent)!r});"
            "import subsystem_recall as rc;"
            f"_s, i = rc.load_store({str(store)!r}, verb='recalled');"
            "print('SCOPES=' + ','.join(i.scopes));"
            "print('MALFORMED=' + ','.join(m.label for m in i.malformed))"
        )
        done = under_deadline(probe, 30.0)
        assert done is not None, (
            "an UNRESTRICTED read of a store holding a SYMLINK-to-FIFO entry "
            "HUNG — `_LOADER_ENTRY_ACTIONS[KIND_LINK_TO_OTHER]` is not REFUSE, "
            "or the guard is not reaching `load_index`. On a `replicas: 1` "
            "Deployment that is a worker that never comes back"
        )
        assert done.returncode == 0, done.stderr[-600:]
        assert f"SCOPES={ALLOW_SCOPE},{DENY_SCOPE}\n" in done.stdout, done.stdout
        # REPORTED, not skipped — a dropped entry is indistinguishable from one
        # nobody ever wrote, which is the conflation this store exists to avoid.
        assert f"MALFORMED={DENY_SCOPE}/{LOCKED_ENTRY}\n" in done.stdout, done.stdout


class TestTheLoaderRefusesHostileEntriesByKind:
    """🔴 THE NARROW ENTRY-KIND GUARD — what it closes, and what it must not.

    The allowlist pushdown protects a SCOPED caller. `visible_scopes=None` skips
    nothing, and the live pod runs a BARE LEGACY token, which is unrestricted —
    so on the deployed configuration a single `.#entry.md` lock file 503'd every
    recall and a single FIFO wedged the worker. That is the configuration these
    tests drive.

    🔴 AND THE GUARD IS NARROW BY DECISION, NOT BY ACCIDENT. Mirroring
    `/snapshot`'s `_ENTRY_ACTIONS` wholesale also refuses a symlink to a regular
    file and an unstat-able path — which this loader READS and honestly fails on
    respectively, so the broad form is still a behaviour change for every local
    CLI caller. `test_a_SYMLINKED_entry_is_STILL_READ…` is the test that kills
    the broad form; without it "refuse hostile kinds" has no upper bound.

    ⚠ NARROW IS NOT FROZEN. The guard has FIVE cells, not the two it shipped
    with, added over three rulings and each on the SAME criterion — this loader
    has never successfully read that kind, so refusing it changes no legitimate
    caller. `link-to-other` (a symlink pointing at a fifo/socket/device) was
    left TAKE for one round as a named residual, then measured wedging an
    unrestricted `/recall` for 25s. `directory` and `link-to-dir` were TAKE for
    three, while the ledger called `chmod 000` "the only residual left" — then
    measured 503ing the whole store on an `IsADirectoryError`, off one stray
    `mkdir <scope>/notes.md`. Every one of those widenings is still inside the
    upper bound, and `link-to-file`, the cell the narrow ruling was actually
    about, is untouched.

    🔴 THE UPPER BOUND IS NOW ALSO A LEDGER SOMETHING READS.
    `TestClassifierIsTotal.test_the_LOADER_RESIDUAL_SET_is_pinned` and the two
    `RESIDUAL LEDGER` guards beside it assert the surviving TAKE set against
    both documents that describe it, in both directions — because the drift that
    let `directory` sit open for three rounds was a DOCUMENT going stale, not a
    table being wrong, and no test read the documents.
    """

    def _hostile(self, tmp_path: Path) -> Path:
        """One store, BOTH refused shapes, in a scope that is not the one asked
        for — the arrangement the operator reproduced: unrestricted token, a
        dangling `.#lock.md` in `bravo`, and a recall for `alpha`.

        🔴 THIS FIXTURE PLANTS A REAL FIFO, SO NOTHING MAY READ IT IN-PROCESS.
        Three tests in this class used to: `load_store` / `load_index` directly,
        and an in-process server. With `_LOADER_ENTRY_ACTIONS[KIND_OTHER]`
        reverted to `TAKE` the first of them ran >600s with no error and no
        failure — the guard's own regression test WEDGED the suite instead of
        failing it, and with no `pytest-timeout` plugin loaded nothing would have
        cut it off. Every read of this store now goes through `under_deadline`,
        which turns that hang back into a red.
        """
        store = _build_store(
            tmp_path / "store",
            {ALLOW_SCOPE: KELP_NUANCE, DENY_SCOPE: QUARTZ_NUANCE},
        )
        _make_unreadable(store, DENY_SCOPE, "emacs")
        _make_unreadable(store, DENY_SCOPE, "fifo")
        return store

    def test_an_UNRESTRICTED_recall_of_ANOTHER_scope_is_200_not_503(
        self, tmp_path: Path
    ):
        """🔴 THE MEASURED SYMPTOM, DRIVEN THROUGH THE SERVER ON THE LIVE
        CREDENTIAL SHAPE. Before the guard this exact request answered `503
        index entry unreadable … '<store>/quartz-mine/.#sealed-adit.md'`.

        `GOOD_TOKEN` is a BARE row, so the record is `legacy`/unrestricted —
        the pod's own configuration, not a scoped token that would be protected
        by the allowlist pushdown instead.

        🔴 THE SERVER IS A REAL SPAWNED PROCESS, NOT AN IN-PROCESS ONE, AND
        THAT IS A TEST-SAFETY REQUIREMENT RATHER THAN A FIDELITY PREFERENCE.
        This fixture plants a FIFO; a wedged handler thread inside the pytest
        process cannot be reclaimed, and `running()`'s teardown would leave it
        parked for the rest of the run. `running_subprocess` puts the load in a
        child that is `terminate()`d in its own `finally`, and turns a wedge into
        a socket timeout — which this test converts into a NAMED failure below,
        because "the worker never came back" is the whole claim.
        """
        store = self._hostile(tmp_path)
        token_file = tmp_path / "token"
        token_file.write_text(GOOD_TOKEN + "\n")
        with running_subprocess(store, token_file) as (base, _proc):
            try:
                code, headers, body = fetch(
                    f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=GOOD_TOKEN
                )
            except (urllib.error.URLError, TimeoutError) as exc:  # no response
                raise AssertionError(
                    "the request thread WEDGED on the hostile store — a REFUSE "
                    "cell is not reaching `load_index`. On a `replicas: 1` "
                    f"Deployment that is a worker that never comes back ({exc})"
                ) from None
        text = body.decode()
        assert code == 200, f"{code}: {text[:400]}"
        assert headers["X-Store-Status"] == "recalled"
        assert KELP_NUANCE in text, "the caller's own content vanished"

    def test_the_REFUSED_entries_are_SURFACED_not_silently_dropped(
        self, tmp_path: Path
    ):
        """🔴 A SKIP RENDERS AS "NOTHING RECORDED", which is the conflation this
        whole store exists to avoid — so the 200 above is only correct if the
        two refused files are still ACCOUNTED FOR.

        Asserted on the index rather than on the rendered body, because the body
        deliberately reports cross-scope defects as a COUNT with scopes named and
        never a filename; the per-file facts live here.

        🔴 IN A CHILD PROCESS, BECAUSE THIS IS THE TEST THAT WEDGED. Measured:
        reverting `_LOADER_ENTRY_ACTIONS[KIND_OTHER]` to `TAKE` made this exact
        test run >600s with no error and no failure — it called `load_store` on a
        store holding a real fifo, in-process, and `read_text` never returned.
        The deadline is what converts that back into a `None`, i.e. a red.
        """
        store = self._hostile(tmp_path)
        done = under_deadline(_load_store_probe(store), 30.0)
        assert done is not None, (
            "an UNRESTRICTED `load_store` of the hostile store HUNG — a "
            "REFUSE cell is not reaching `load_index`"
        )
        assert done.returncode == 0, done.stderr[-800:]
        labels = sorted(
            _probe_field(done.stdout, "MALFORMED").split(",")
        )
        assert labels == sorted(
            [f"{DENY_SCOPE}/{EMACS_LOCK}", f"{DENY_SCOPE}/{LOCKED_ENTRY}"]
        ), done.stdout
        reasons = _probe_field(done.stdout, "REASONS")
        assert "broken symlink" in reasons, reasons
        assert "not a regular file" in reasons, reasons
        # …and the good entry in that same scope is still served.
        assert f"{DENY_SCOPE}:1" in _probe_field(done.stdout, "PERSCOPE"), done.stdout

    def _recall_over_http(self, tmp_path: Path, name: str, kind: str):
        """Build a store carrying ONE hostile `kind`, then recall a DIFFERENT
        scope over the wire on the pod's own credential shape (a bare legacy
        row, i.e. unrestricted). Returns `(code, headers, text)`.
        """
        root = tmp_path / name
        store = _build_store(
            root / "store",
            {ALLOW_SCOPE: KELP_NUANCE, DENY_SCOPE: QUARTZ_NUANCE},
        )
        _make_unreadable(store, DENY_SCOPE, kind, outside=root / "outside")
        with running(store, tokens=(GOOD_TOKEN,)) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=GOOD_TOKEN
            )
        return code, headers, body.decode()

    @pytest.mark.parametrize("kind", ["dir", "linkdir"])
    def test_a_DIRECTORY_named_md_no_longer_503s_the_WHOLE_store(
        self, tmp_path: Path, kind: str
    ):
        """🔴 THE MEASURED SYMPTOM, AND THE ONE THE LEDGER DENIED EXISTED.

        For three rounds `load_index`'s docstring and the API README both said a
        `chmod 000` regular file was "the only residual left". Measured on that
        tip, with a paired control:

            store/beta/notes.md created as a DIRECTORY
              GET /api/v1/recall/alpha, unrestricted legacy token
              -> 503 "index entry unreadable: … (IsADirectoryError: …)"
            CONTROL, same shape but a dangling `.#lock.md`
              -> 200

        So one accidental `mkdir <scope>/notes.md`, or one rsync or restore
        artefact, took `/recall` AND `/search` down for EVERY caller — including
        callers whose own scopes were untouched, and for a scope this request
        never even asked about. A symlink to a directory behaved identically,
        which is why both are parametrized here rather than one standing in for
        the other.

        🔴 THE POSITIVE CONTROL IS IN THIS TEST, NOT NEXT TO IT. Every assertion
        here is "it did NOT 503", which is the reassuring zero that a harness
        wired to nothing also produces. The `chmod 000` arm at the end drives the
        SAME helper at the one shape that is still, deliberately, a residual —
        so a fixture that plants nothing, or a `running()` that never reaches the
        loader, fails there before the 200 above means anything.
        """
        code, headers, text = self._recall_over_http(tmp_path, f"probe-{kind}", kind)
        assert code == 200, f"{code}: {text[:400]}"
        assert headers["X-Store-Status"] == "recalled"
        assert KELP_NUANCE in text, "the caller's own content vanished"

        code, headers, text = self._recall_over_http(
            tmp_path, f"control-{kind}", "perm"
        )
        assert code == 503, (
            "an unreadable REGULAR file did NOT 503 — this harness cannot "
            f"observe the failure the assertions above deny, so they are "
            f"vacuous. got {code}: {text[:300]}"
        )
        assert headers["X-Store-Status"] == "store-unreachable"

    @pytest.mark.parametrize("kind", ["dir", "linkdir"])
    def test_the_REFUSED_DIRECTORY_is_a_NAMED_row_not_a_silent_skip(
        self, tmp_path: Path, kind: str
    ):
        """A 200 is only correct if the refused path is still ACCOUNTED FOR — a
        dropped entry is indistinguishable from one nobody ever wrote, which is
        the conflation this whole store exists to avoid.

        The REASON is asserted too, and the two kinds carry DIFFERENT sentences,
        so a refusal filed under the wrong cell's reason fails here rather than
        passing on the word "directory" appearing anywhere at all.
        """
        root = tmp_path / kind
        store = _build_store(
            root / "store",
            {ALLOW_SCOPE: KELP_NUANCE, DENY_SCOPE: QUARTZ_NUANCE},
        )
        planted = _make_unreadable(
            store, DENY_SCOPE, kind, outside=root / "outside"
        )
        expected_kind = api.KIND_DIRECTORY if kind == "dir" else api.KIND_LINK_TO_DIR
        assert api.classify_path(planted) == expected_kind, (
            "the fixture is not the kind this test is about"
        )

        _s, index = api.rc.load_store(store, verb="recalled")
        assert [m.label for m in index.malformed] == [f"{DENY_SCOPE}/{LOCKED_ENTRY}"]
        assert index.malformed[0].reason == resolver._LOADER_REFUSAL_REASON[
            expected_kind
        ], "the refusal was filed under another cell's sentence"
        # …and the good entry in that same scope is still served.
        assert len(index.entries(DENY_SCOPE)) == 1

    def test_a_SYMLINKED_entry_is_STILL_READ_the_guard_is_NOT_the_broad_one(
        self, tmp_path: Path
    ):
        """🔴 THE UPPER BOUND ON THE GUARD, AND THE MUTANT IT EXISTS TO KILL.

        `_ENTRY_ACTIONS[KIND_LINK_TO_FILE]` is REFUSE, because `/snapshot` will
        not follow a link out of the store. The LOADER has always read one, and
        a `<scope>/<slug>.md -> ../shared/<slug>.md` symlink is an ordinary way
        to keep one entry in two places. Copying `/snapshot`'s column here is the
        over-broad form that was rejected on this PR; flipping that one cell to
        REFUSE turns this entry into a malformed row and fails here.

        The entry's CONTENT is asserted, not merely its presence: a guard that
        refused it would still leave the scope registered.
        """
        store = _build_store(tmp_path / "store", {ALLOW_SCOPE: KELP_NUANCE})
        real = tmp_path / "outside" / "linked-entry.md"
        real.parent.mkdir()
        real.write_text(
            _entry("linked-entry", ALLOW_SCOPE, nuance="- 2026-03-06: via a link.")
        )
        link = store / ALLOW_SCOPE / "linked-entry.md"
        os.symlink(real, link)
        assert api.classify_path(link) == api.KIND_LINK_TO_FILE, (
            "the fixture is not a symlink-to-regular-file, so this measures "
            "nothing about the cell it names"
        )

        _s, index = api.rc.load_store(store, verb="recalled")
        assert index.malformed == (), (
            "a symlinked entry was refused — the guard has been widened to the "
            "broad `_ENTRY_ACTIONS` form the narrow ruling rejected"
        )
        assert sorted(e.slug for e in index.entries(ALLOW_SCOPE)) == sorted(
            ["linked-entry", f"{ALLOW_SCOPE}-entry"]
        )

    def test_under_RAISE_a_refused_entry_RAISES_the_same_class_as_any_other(
        self, tmp_path: Path
    ):
        """🔴 THE POLICY IS `on_malformed`'s, NOT THE GUARD'S. The WRITER's probe
        loads with `RAISE` precisely because it must not modify a store it read
        only part of, and that is as true of a fifo as of a wrapped `aliases:`
        line. A guard that collected unconditionally would silently hand the
        writer a partial index.

        🔴 UNDER THE DEADLINE TOO. Whether the fifo is even REACHED here depends
        on `sorted()` order inside the scope directory — so "it raised before the
        fifo, therefore it cannot hang" is a property of two filenames, not of
        the code. That is not a guarantee worth resting the suite's liveness on.
        """
        store = self._hostile(tmp_path)
        done = under_deadline(_load_index_raise_probe(store), 30.0)
        assert done is not None, (
            "`load_index` under RAISE HUNG on the hostile store — a REFUSE cell "
            "is not reaching the loader"
        )
        assert done.returncode == 0, done.stderr[-800:]
        assert _probe_field(done.stdout, "CLASS") == "MalformedEntryError", done.stdout
        assert "malformed index entry" in _probe_field(done.stdout, "MESSAGE")
        assert _probe_field(done.stdout, "SOURCE") in (EMACS_LOCK, LOCKED_ENTRY)

    def test_a_BOGUS_policy_is_still_a_ValueError_not_a_refusal(
        self, tmp_path: Path
    ):
        """The guard branches on `on_malformed` BEFORE `build_index` validates
        it, so the predicate is shared (`_check_on_malformed`) rather than
        spelled twice. Spelled twice, a bogus policy on a hostile store would be
        answered with a complaint about the first fifo instead of about the
        policy — a message that sends the operator to the wrong file.

        ⚠ THE ONE TEST IN THIS CLASS THAT STAYS IN-PROCESS ON THE HOSTILE STORE,
        and it is safe for a STRUCTURAL reason, not a lucky one:
        `_check_on_malformed` is the FIRST statement of `load_index`, so this
        call raises before `iterdir()` — no candidate is ever classified, let
        alone opened. If that ordering ever changes, this test becomes a hang and
        must move to `under_deadline` with the others.
        """
        store = self._hostile(tmp_path)
        with pytest.raises(ValueError, match="on_malformed must be one of"):
            api.rc.load_index(store, on_malformed="collct")

    def test_the_REFUSED_row_is_filed_under_the_FOLDED_scope(self, tmp_path: Path):
        """🔴 THE SCOPE ON A `MalformedEntry` IS THE NORMALIZED ONE — that is
        `MalformedEntry`'s own contract, and `malformed_in` compares against
        `normalize_ref(scope)`. A refusal filed under the RAW directory name
        matches no scope, so it vanishes from `malformed_in` and surfaces only
        through the store-wide count: reported, but not against the scope that
        holds it, which is where the operator will look.

        The fixture directory really is spelled `Kelp_Forest`. Every other store
        in this file is already its own folded form, so an unfolded mutant
        survives them all.
        """
        raw_dir = "Kelp_Forest"
        assert api.rc.normalize_ref(raw_dir) == ALLOW_SCOPE != raw_dir
        store = _build_store(tmp_path / "store", {raw_dir: KELP_NUANCE})
        _make_unreadable(store, raw_dir, "emacs")

        _s, index = api.rc.load_store(store, verb="recalled")
        assert [m.label for m in index.malformed_in(ALLOW_SCOPE)] == [
            f"{ALLOW_SCOPE}/{EMACS_LOCK}"
        ]
        assert index.malformed_outside([ALLOW_SCOPE]) == ()

    def test_a_CLEAN_store_is_UNCHANGED_by_the_guard(self, tmp_path: Path):
        """The positive control. Every assertion above is about a hostile store;
        without this, a loader that refused EVERY candidate would satisfy the
        `.malformed` ones and only fail on content nobody asserted.
        """
        store = _build_store(
            tmp_path / "store",
            {ALLOW_SCOPE: KELP_NUANCE, DENY_SCOPE: QUARTZ_NUANCE},
        )
        _s, index = api.rc.load_store(store, verb="recalled")
        assert index.malformed == ()
        assert len(index) == 2
        assert set(index.scopes) == {ALLOW_SCOPE, DENY_SCOPE}


class TestScopeFilteringIsNotAWriteVerb:
    """🔴 CRITERIA 4-10 ARE NOT IN THIS BRANCH, AND THAT IS ASSERTED, NOT SAID.

    `TestPhaseOneScope` already pins the write guard and the route ledger; this
    is the same claim restated for the change that landed here, so a future
    branch that adds a verb "while it is touching auth anyway" fails a test in
    the section that added scoping.
    """

    def test_the_route_set_did_NOT_grow(self):
        assert set(api.API_ROUTES) == {"recall", "search", "snapshot"}

    def test_every_write_verb_is_STILL_405_on_EVERY_ROUTE_with_a_SCOPED_token(
        self, scoped_store: Path
    ):
        """🔴 EVERY ROUTE, ENUMERATED FROM `API_ROUTES` ITSELF — not `/recall`
        alone, which is what this used to probe.

        The two guards in this class were jointly walkable. A `do_POST` added on
        `/snapshot` passed BOTH: the route SET is unchanged, so
        `test_the_route_set_did_NOT_grow` is green, and the only path this test
        exercised was `/recall`. Driving the table means a route added to
        `API_ROUTES` is automatically probed, and a verb added to one existing
        route is caught by the route it was added to.
        """
        # A path per route, so the request reaches the route rather than the
        # `bad request: invalid path component` branch. Keyed on the ledger, and
        # asserted TOTAL below, so a new route cannot be silently unprobed.
        paths = {
            "recall": f"/api/v1/recall/{ALLOW_SCOPE}",
            "search": f"/api/v1/search/{ALLOW_SCOPE}?q=tide",
            "snapshot": "/api/v1/snapshot",
        }
        assert set(paths) == set(api.API_ROUTES), (
            "a route exists that this test has no path for, so it would go "
            f"unprobed: {sorted(set(api.API_ROUTES) ^ set(paths))}"
        )
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            for route, path in sorted(paths.items()):
                for method in ("POST", "PUT", "PATCH", "DELETE"):
                    code, _h, body = fetch(
                        f"{base}{path}", token=ZACH_TOKEN, method=method
                    )
                    assert code == 405, f"{method} {route} answered {code}"
                    assert body == b"read-only\n", f"{method} {route}: {body!r}"

    def test_a_full_scoped_read_workload_leaves_the_store_BYTE_IDENTICAL(
        self, scoped_store: Path
    ):
        before = tree_hash(scoped_store)
        with running(scoped_store, tokens=(ZACH, DANA)) as (base, _):
            for token in (ZACH_TOKEN, DANA_TOKEN):
                for path in (
                    f"/api/v1/recall/{ALLOW_SCOPE}",
                    f"/api/v1/recall/{DENY_SCOPE}",
                    f"/api/v1/recall/{PHANTOM_SCOPE}",
                    f"/api/v1/search/{ALLOW_SCOPE}?q=tide&all_scopes=1",
                    "/api/v1/snapshot",
                ):
                    fetch(f"{base}{path}", token=token)
        assert tree_hash(scoped_store) == before


# =============================================================================
# 19. PHASE 3, CRITERIA 4-7 — THE WRITE PATH.
#
# 🔴 WHAT IS RED AT THE BASE REF AND WHAT IS NOT, STATED HERE RATHER THAN LEFT
# TO BE ASSUMED, because "a test never seen fail proves nothing".
#
#   * Every BEHAVIOURAL test below is red at `origin/main`: the routes do not
#     exist there, so `POST /api/v1/entry/…` answers `405 read-only` and each
#     assertion fails on a real behaviour difference rather than on an import
#     error. That is genuine regression coverage.
#   * The UNIT tests (`content_hash`, `bullet_content`, `nuance_insert_index`,
#     `entry_revision`, `append_bullet`, `replace_entry`) reference names that do
#     not exist at base, so their red is an `AttributeError` and proves nothing
#     about behaviour. They are pinned by MUTATION instead, and the PR body
#     reports which mutant each one kills.
#   * `TestPhaseOneScope.test_the_verb_ledger_…` is likewise an ATTRIBUTE error
#     at base (`_write` does not exist), so it is an invariant guard plus its
#     mutants, not regression coverage.
# =============================================================================


# Pairwise-distinct, sharing no substring with each other or with the three
# scope nuance lines above — so an assertion that a bullet landed cannot be
# satisfied by a renderer that surfaced a different one, and a mutant that
# hardcodes any single literal is visible.
BULLET_A = "the salinity probe reads high after a squall"
BULLET_B = "the winch motor stalls on a neap tide"
BULLET_C = "the buoy transmitter drops every third packet"
BULLET_OPEN = "OPEN: the mooring shackle wants replacing before winter"

# Distinct from each other and from every identity, so "the session was
# recorded" and "the actor was recorded" cannot pass by reading one field twice.
SESSION_A = "sess-7f3a2b"
SESSION_B = "sess-91cd40"

# 🔴 THE NAME A HOSTILE CLIENT PUTS IN THE BODY. It is not any identity in this
# file, so a server that wrote the body's `actor` through would spell something
# no token could ever produce.
FORGED_ACTOR = "mallory"


def entry_ref(scope: str) -> str:
    """`_build_store` names each scope's single entry `<scope>-entry`."""
    return f"{scope}-entry"


def entry_file(root: Path, scope: str) -> Path:
    return root / scope / f"{entry_ref(scope)}.md"


def bullets_url(base: str, scope: str, ref: str | None = None) -> str:
    return f"{base}/api/v1/entry/{scope}/{ref or entry_ref(scope)}/bullets"


def entry_url(base: str, scope: str, ref: str | None = None) -> str:
    return f"{base}/api/v1/entry/{scope}/{ref or entry_ref(scope)}"


def post_bullet(base: str, token: str, scope: str, *, ref: str | None = None, **payload):
    """POST one append. The payload is passed through VERBATIM so a test can send
    a field the server must ignore, or omit one it must require."""
    return fetch(
        bullets_url(base, scope, ref),
        token=token,
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
    )


def nuance_of(path: Path) -> str:
    """The `## Nuance / work-history` body of an entry file, via the resolver's
    own parser — so "the bullet landed in the right SECTION" is answered the way
    every reader answers it, not by an `in` over the whole file."""
    return resolver.extract_sections(
        path.read_text(encoding="utf-8"), (resolver.NUANCE_HEADING,)
    ).get(resolver.NUANCE_HEADING, "")


def nuance_bytes_of(data: bytes) -> str:
    """`nuance_of`, for an entry whose bytes are NOT valid UTF-8.

    `Path.read_text` decodes `strict` and raises on exactly the files the
    non-UTF-8 section is about, so a test there cannot use `nuance_of` at all.

    `surrogateescape` is spelled HERE BY HAND rather than imported from the
    module under test: reading the file with whatever handler the server happens
    to use would assert `x == x`, and this helper is used to state what is on
    disk, which is a claim about BYTES and not about the server's opinion of
    them. `TestTheEntryTextCodecIsONERuleInONEPlace` pins the server's side
    separately.
    """
    return resolver.extract_sections(
        data.decode("utf-8", errors="surrogateescape"), (resolver.NUANCE_HEADING,)
    ).get(resolver.NUANCE_HEADING, "")


def store_headers(headers: dict) -> tuple:
    """The `X-Store-*` family only.

    🔴 NOT FOR A LEAK-PROPERTY COMPARISON — use `_comparable`, which keeps
    every header but `Date`. Every "these two answers are byte-identical" test in
    this file used to narrow to this family, which is a guard narrower than its
    own name: `ETag`, `Content-Length` or any header added later would
    discriminate a refused target from an absent one while the assertion stayed
    green. Kept for the places that genuinely mean "the store's own status
    fields", not for indistinguishability.
    """
    return tuple(
        sorted((k, v) for k, v in headers.items() if k.lower().startswith("x-store"))
    )


@contextmanager
def forced_interleave(gate_s: float = 2.0):
    """Force TWO writers to overlap inside the critical section, deterministically.

    🔴 WHY A HOOK AND NOT TWO THREADS AND HOPE. The defect this guards against —
    a read-modify-write with no mutual exclusion — only shows up when the two
    writers' windows actually overlap, and on a fast machine they usually do not.
    A test that passes because the race did not happen is worse than no test: it
    reads as coverage of the ONE criterion that protects against content loss.

    The hook runs inside `append_bullet`/`replace_entry`, after the read and
    before the write, which is exactly the window a missing lock leaves open.
    The FIRST caller to reach it parks until `release` is set or `gate_s`
    elapses; every later caller passes straight through. So:

      * with the lock, the second writer cannot even reach the hook — it is
        blocked on `flock` — so the first parks for `gate_s`, writes, releases,
        and the second then does its own complete read-modify-write. Both
        bullets survive, and the test costs `gate_s`.
      * without the lock, the second writer reads the SAME original bytes the
        first is holding, writes, and finishes; the first then writes its own
        version over the top and the second bullet is gone. Every time.

    `test_the_interleave_harness_CAN_LOSE_an_append` is the negative control for
    exactly that second bullet, run against a no-op lock.
    """
    state = {"n": 0}
    counter_lock = threading.Lock()
    first_in = threading.Event()
    release = threading.Event()

    def hook() -> None:
        with counter_lock:
            index = state["n"]
            state["n"] += 1
        if index == 0:
            first_in.set()
            release.wait(timeout=gate_s)

    original = api._WRITE_INTERLEAVE
    api._WRITE_INTERLEAVE = hook
    try:
        yield first_in, release
    finally:
        api._WRITE_INTERLEAVE = original


@contextmanager
def no_entry_lock():
    """Replace `_EntryLock` with a lock that locks nothing. The MUTATION, run as
    a control: every concurrency assertion in this file must fail under it."""

    class _NoLock:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

    original = api._EntryLock
    api._EntryLock = _NoLock
    try:
        yield
    finally:
        api._EntryLock = original


class TestTheActorComesFromTheTOKEN:
    """🔴 CRITERION 4. The single most important property on the write path: a
    client-supplied actor lets any token-holder attribute a bullet to somebody
    else, and an attribution nobody can trust is worse than none.
    """

    @pytest.mark.parametrize(
        "record,token,scope,identity",
        [
            (ZACH, ZACH_TOKEN, ALLOW_SCOPE, "zach"),
            (DANA, DANA_TOKEN, DENY_SCOPE, "dana"),
        ],
    )
    def test_a_FORGED_actor_in_the_body_is_DISCARDED(
        self, scoped_store: Path, record, token: str, scope: str, identity: str
    ):
        """🔴 TWO IDENTITIES, NOT ONE, AND THAT IS THE POINT. A single-identity
        version of this test is passed by `actor = "zach"` hardcoded in the
        renderer. Two callers writing to two scopes cannot be.
        """
        path = entry_file(scoped_store, scope)
        with running(scoped_store, tokens=(record,)) as (base, _):
            code, headers, _b = post_bullet(
                base, token, scope, text=BULLET_A, session=SESSION_A,
                actor=FORGED_ACTOR,
            )
        assert code == 200, (code, headers)
        assert headers["X-Store-Status"] == "appended"
        text = path.read_text()
        assert f"[cairn: {identity}/{SESSION_A}]" in text, text
        assert FORGED_ACTOR not in text, (
            "the body's `actor` reached the file — any token-holder could then "
            "attribute a bullet to anybody"
        )

    def test_the_FORGED_name_is_one_the_server_COULD_have_written(self):
        """Positive control for the assertion above: `mallory` passes the
        identity charset, so its absence from the file is evidence that it was
        DISCARDED rather than evidence that it could never have been rendered."""
        assert IDENTITY_CHARSET.fullmatch(FORGED_ACTOR)
        assert len(FORGED_ACTOR) <= api.MAX_IDENTITY_CHARS

    def test_the_SESSION_is_recorded_and_it_is_the_one_that_was_SENT(
        self, scoped_store: Path
    ):
        path = entry_file(scoped_store, ALLOW_SCOPE)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            post_bullet(base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_A, session=SESSION_A)
            post_bullet(base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_B, session=SESSION_B)
        text = path.read_text()
        # Both sessions, each on its OWN bullet — a server that stamped the last
        # session onto every bullet would satisfy a one-append test.
        assert f"{BULLET_A} [cairn: zach/{SESSION_A}]" in text, text
        assert f"{BULLET_B} [cairn: zach/{SESSION_B}]" in text, text

    def test_the_bullet_lands_in_the_NUANCE_section_and_nowhere_else(
        self, scoped_store: Path
    ):
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_text()
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            post_bullet(base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_A, session=SESSION_A)
        after = path.read_text()
        assert BULLET_A in nuance_of(path)
        # …and the other two sections are untouched, byte for byte.
        for heading in (resolver.WHAT_HEADING, resolver.POINTERS_HEADING):
            was = resolver.extract_sections(before, (heading,))
            now = resolver.extract_sections(after, (heading,))
            assert was == now, f"{heading} changed"

    def test_the_new_bullet_is_FIRST_which_is_the_store_convention(
        self, scoped_store: Path
    ):
        path = entry_file(scoped_store, ALLOW_SCOPE)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            post_bullet(base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_A, session=SESSION_A)
        bullets = resolver.parse_journal_bullets(nuance_of(path))
        assert len(bullets) == 2
        assert BULLET_A in bullets[0].lines[0]
        assert KELP_NUANCE.lstrip("- ") in bullets[1].lines[0]

    def test_an_appended_OPEN_MARKER_STILL_PARSES(self, scoped_store: Path):
        """🔴 THIS IS WHY THE ATTRIBUTION IS A SUFFIX.

        The store's bullet grammar is a PREFIX grammar anchored at position 0:
        `- [YYYY-MM-DD: ]OPEN:` with an exact terminator. Writing the actor
        between the date and the text — `- 2026-08-27 (zach): OPEN: …`, the
        obvious rendering — parses as NO MARKER at all, which is precisely the
        near-miss class the reader exists to report: the `🔴 1 OPEN` badge
        silently stops rendering and a vanished badge looks like success.

        So the marker is asserted through the READER'S OWN parser, not by an `in`
        over the line.
        """
        path = entry_file(scoped_store, ALLOW_SCOPE)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, _h, _b = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_OPEN, session=SESSION_A
            )
        assert code == 200
        first = resolver.parse_journal_bullets(nuance_of(path))[0]
        assert first.openness == resolver.OPENNESS_OPEN, first.lines
        assert first.date is not None, "the date prefix stopped parsing too"

    def test_the_audit_line_names_the_identity_and_the_append(
        self, scoped_store: Path
    ):
        with running(scoped_store, tokens=(ZACH,)) as (base, audit):
            post_bullet(base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_A, session=SESSION_A)
            line = await_audit(audit, 1)[0]
        assert "identity=zach" in line, line
        assert "method=POST" in line, line
        assert "status=appended" in line, line
        assert "result=200" in line, line
        assert ZACH_TOKEN not in line


class TestALegacyTokenCannotWrite:
    """🔴 A BARE (UNMAPPED) TOKEN HAS NO IDENTITY, SO IT HAS NO ACTOR.

    Criterion 4 says every appended bullet records an actor and a session. A
    legacy row's identity is the constant `legacy`, which names no holder — so
    the guarantee cannot be met and the write is refused with its OWN error.
    That makes criterion 10's credential retirement a PREREQUISITE for writes
    rather than an afterthought.
    """

    def test_a_legacy_token_is_REFUSED_and_the_entry_is_UNCHANGED(
        self, scoped_store: Path
    ):
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_bytes()
        with running(scoped_store, token=GOOD_TOKEN) as (base, _):
            code, headers, body = post_bullet(
                base, GOOD_TOKEN, ALLOW_SCOPE, text=BULLET_A, session=SESSION_A
            )
        assert code == 403, (code, body)
        assert headers["X-Store-Status"] == "legacy-cannot-write"
        assert path.read_bytes() == before

    def test_the_refusal_is_its_OWN_error_not_the_uniform_401_or_the_404(
        self, scoped_store: Path
    ):
        """It must be distinguishable BY THE HOLDER, or the migration is
        undiagnosable: an operator seeing `unauthorized` would rotate a token
        that is working exactly as configured."""
        with running(scoped_store, token=GOOD_TOKEN) as (base, _):
            code, _h, body = post_bullet(
                base, GOOD_TOKEN, ALLOW_SCOPE, text=BULLET_A, session=SESSION_A
            )
        assert code == 403
        assert body != UNAUTHORIZED_BODY_LITERAL
        assert b"no identity" in body, body

    def test_a_legacy_token_can_still_READ(self, scoped_store: Path):
        """The control. The refusal is on WRITES; a legacy row is still
        unrestricted for reads and rolling back to one must stay possible."""
        with running(scoped_store, token=GOOD_TOKEN) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{DENY_SCOPE}", token=GOOD_TOKEN
            )
        assert code == 200
        assert headers["X-Store-Status"] == "recalled"
        assert QUARTZ_NUANCE.encode() in body

    def test_the_403_names_no_scope_so_it_is_not_an_ORACLE(self, scoped_store: Path):
        """A legacy row is UNRESTRICTED, so this answer must not vary with
        whether the scope or the ref exists — otherwise the one credential that
        cannot write would be the one that can enumerate."""
        with running(scoped_store, token=GOOD_TOKEN) as (base, _):
            real = post_bullet(
                base, GOOD_TOKEN, ALLOW_SCOPE, text=BULLET_A, session=SESSION_A
            )
            phantom = post_bullet(
                base, GOOD_TOKEN, PHANTOM_SCOPE, ref="never-carved",
                text=BULLET_A, session=SESSION_A,
            )
        assert real[0] == phantom[0] == 403
        assert _comparable(real[1]) == _comparable(phantom[1])
        assert real[2] == phantom[2]
        assert real[2], "both bodies are empty — the comparison would be vacuous"


UNAUTHORIZED_BODY_LITERAL = b"unauthorized\n"


class TestWritesGoThroughTheSAMEDoorAsReads:
    """🔴 NO SECOND, WEAKER AUTH PATH. Same `_identify_and_meter`, same
    `authorize`, same uniform 401, same lockout — because a rule enforced at one
    call site and not the other is the failure this file keeps finding.
    """

    def test_a_write_with_NO_client_ip_is_the_uniform_401(self, scoped_store: Path):
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_bytes()
        with running(scoped_store, tokens=(ZACH,)) as (base, audit):
            code, _h, body = fetch(
                bullets_url(base, ALLOW_SCOPE),
                token=ZACH_TOKEN,
                method="POST",
                client_ip=None,
                data=json.dumps({"text": BULLET_A, "session": SESSION_A}).encode(),
            )
            line = await_audit(audit, 1)[0]
        assert (code, body) == (401, UNAUTHORIZED_BODY_LITERAL)
        assert "status=no-client-ip" in line
        assert path.read_bytes() == before

    def test_a_write_with_a_WRONG_token_is_the_uniform_401_and_is_CHARGED(
        self, scoped_store: Path
    ):
        limiter = api.RateLimiter(max_failures=5, window_s=600.0, lockout_s=600.0)
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_bytes()
        with running(scoped_store, tokens=(ZACH,), limiter=limiter) as (base, _):
            code, _h, body = post_bullet(
                base, "w" * 48, ALLOW_SCOPE, text=BULLET_A, session=SESSION_A
            )
        assert (code, body) == (401, UNAUTHORIZED_BODY_LITERAL)
        assert limiter._failures, "a failed write auth was not charged"
        assert path.read_bytes() == before

    def test_a_LOCKED_OUT_client_cannot_write(self, scoped_store: Path):
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_bytes()
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            for _ in range(5):
                post_bullet(
                    base, "w" * 48, ALLOW_SCOPE, text=BULLET_A, session=SESSION_A
                )
            code, _h, body = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_A, session=SESSION_A
            )
        assert (code, body) == (401, UNAUTHORIZED_BODY_LITERAL)
        assert path.read_bytes() == before

    def test_a_write_to_a_route_with_no_write_row_is_STILL_405(
        self, scoped_store: Path
    ):
        """The converted guard, behaviourally: adding two write routes must not
        have widened any OTHER path."""
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            for path in (
                f"/api/v1/recall/{ALLOW_SCOPE}",
                f"/api/v1/search/{ALLOW_SCOPE}?q=tide",
                "/api/v1/snapshot",
            ):
                for method in ("POST", "PUT", "PATCH", "DELETE"):
                    code, headers, body = fetch(
                        f"{base}{path}", token=ZACH_TOKEN, method=method, data=b"{}"
                    )
                    assert code == 405, f"{method} {path} answered {code}"
                    assert body == b"read-only\n"
                    assert headers["Allow"] == "GET, HEAD"

    @pytest.mark.parametrize("method", ["PATCH", "DELETE"])
    def test_PATCH_and_DELETE_have_no_write_row_even_on_the_ENTRY_path(
        self, scoped_store: Path, method: str
    ):
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_bytes()
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, _h, body = fetch(
                bullets_url(base, ALLOW_SCOPE), token=ZACH_TOKEN, method=method,
                data=json.dumps({"text": BULLET_A, "session": SESSION_A}).encode(),
            )
        assert (code, body) == (405, b"read-only\n")
        assert path.read_bytes() == before

    def test_a_PUT_at_the_BULLETS_path_does_not_dispatch(self, scoped_store: Path):
        """The tail (`bullets`) and the arity are part of the row. A PUT there is
        four components against a row that declares three, so it matches nothing."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_bytes()
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, _h, _b = fetch(
                bullets_url(base, ALLOW_SCOPE), token=ZACH_TOKEN, method="PUT",
                data=before, extra_headers={"If-Match": api.entry_revision(before)},
            )
        assert code == 405
        assert path.read_bytes() == before

    def test_a_POST_with_the_WRONG_TAIL_does_not_dispatch(self, scoped_store: Path):
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_bytes()
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, _h, _b = fetch(
                f"{base}/api/v1/entry/{ALLOW_SCOPE}/{entry_ref(ALLOW_SCOPE)}/pointers",
                token=ZACH_TOKEN, method="POST",
                data=json.dumps({"text": BULLET_A, "session": SESSION_A}).encode(),
            )
        assert code == 405
        assert path.read_bytes() == before

    def test_a_TRAVERSING_path_component_is_refused_before_it_reaches_the_disk(
        self, scoped_store: Path
    ):
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, _b = fetch(
                f"{base}/api/v1/entry/%2e%2e/{entry_ref(ALLOW_SCOPE)}/bullets",
                token=ZACH_TOKEN, method="POST",
                data=json.dumps({"text": BULLET_A, "session": SESSION_A}).encode(),
            )
        assert code == 400
        assert headers["X-Store-Status"] == "bad-request"


class TestARefusedWriteIsIndistinguishableFromAnAbsentOne:
    """🔴 CRITERION 3's ENUMERATION PROPERTY, APPLIED TO WRITES.

    The read path closed this at the INDEX rather than with a per-route "is this
    scope yours" check, and the write path reuses that same narrowing — so a
    scope outside the caller's allowlist is not merely refused, it is not IN the
    index the writer resolves against, and `UnknownScopeError` is raised for the
    identical reason it is raised for a scope that never existed.

    An error that discriminates is an enumeration API on a write verb exactly as
    on a read one, and building a NEW oracle on the write side would undo what
    criteria 1-3 closed.
    """

    def _phases(self, tmp_path: Path):
        root = tmp_path / "store"

        def present():
            if root.exists():
                shutil.rmtree(root)
            return _build_store(
                root, {ALLOW_SCOPE: KELP_NUANCE, DENY_SCOPE: QUARTZ_NUANCE}
            )

        def absent():
            shutil.rmtree(root)
            return _build_store(
                root, {ALLOW_SCOPE: KELP_NUANCE, THIRD_SCOPE: LANTERN_NUANCE}
            )

        return root, present, absent

    def _post(self, root: Path, record, scope: str):
        with running(root, tokens=(record,)) as (base, _):
            code, headers, body = post_bullet(
                base, record.token, scope, text=BULLET_C, session=SESSION_B
            )
        return code, _comparable(headers), body

    def test_APPEND_to_a_refused_scope_is_BYTE_IDENTICAL_to_one_that_never_existed(
        self, tmp_path: Path
    ):
        root, present, absent = self._phases(tmp_path)
        present()
        refused = self._post(root, ZACH, DENY_SCOPE)
        absent()
        never = self._post(root, ZACH, DENY_SCOPE)

        assert refused[0] == never[0] == 404
        assert refused[1] == never[1], (
            f"X-Store-* headers differ:\n refused={refused[1]}\n absent ={never[1]}"
        )
        assert refused[2] == never[2], (
            "response bodies differ — a refused write target is distinguishable "
            f"from an absent one:\nrefused: {refused[2]!r}\nabsent : {never[2]!r}"
        )
        assert refused[2], "both bodies are empty — the equality would be vacuous"

    def test_POSITIVE_CONTROL_the_APPEND_comparison_CAN_see_the_difference(
        self, tmp_path: Path
    ):
        """🔴 WITHOUT THIS, THE EQUALITY ABOVE IS SATISFIED BY A SERVER THAT
        ANSWERS 404 TO EVERYTHING — and by a fail-closed one that answers two
        empty bodies. Same two phases, same path, a token that MAY write that
        scope: present -> `appended`, absent -> `not-found`. Both non-empty, and
        different.
        """
        root, present, absent = self._phases(tmp_path)
        present()
        wrote = self._post(root, DANA, DENY_SCOPE)
        absent()
        gone = self._post(root, DANA, DENY_SCOPE)

        assert wrote[0] == 200 and gone[0] == 404
        # `_comparable` lower-cases the header names it sorts on.
        assert dict(wrote[1])["x-store-status"] == "appended"
        assert dict(gone[1])["x-store-status"] == "not-found"
        assert wrote[2] != gone[2]
        assert wrote[2] and gone[2]
        assert BULLET_C.encode() in wrote[2]

    def test_a_refused_write_leaves_the_DENIED_scope_BYTE_IDENTICAL(
        self, scoped_store: Path
    ):
        """The refusal is not merely a status: nothing on disk moved. Hashed over
        the WHOLE tree, so a lock file, a temp file or a stray write anywhere
        would be caught, not just a change to the entry this test names."""
        before = tree_hash(scoped_store)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            for scope, ref in (
                (DENY_SCOPE, entry_ref(DENY_SCOPE)),
                (PHANTOM_SCOPE, "never-carved"),
                (THIRD_SCOPE, entry_ref(THIRD_SCOPE)),
                (ALLOW_SCOPE, "no-such-entry"),
            ):
                code, _h, _b = post_bullet(
                    base, ZACH_TOKEN, scope, ref=ref, text=BULLET_A, session=SESSION_A
                )
                assert code == 404, (scope, ref, code)
        assert tree_hash(scoped_store) == before

    def test_an_UNKNOWN_REF_in_an_ALLOWED_scope_is_the_SAME_404(
        self, scoped_store: Path
    ):
        """A ref that resolves to nothing answers the identical bytes a refused
        scope does. Four ways to fail to resolve, ONE answer — so there is no
        residual channel to probe on either axis."""
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            missing_ref = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, ref="no-such-entry",
                text=BULLET_A, session=SESSION_A,
            )
            refused_scope = post_bullet(
                base, ZACH_TOKEN, DENY_SCOPE, text=BULLET_A, session=SESSION_A
            )
        assert missing_ref[0] == refused_scope[0] == 404
        assert _comparable(missing_ref[1]) == _comparable(refused_scope[1])
        assert missing_ref[2] == refused_scope[2]
        # 🔴 THE NON-EMPTY GUARD ITS THREE SIBLINGS CARRY AND THIS ONE DID NOT.
        # `b"" == b""` satisfies the equality above, so a server that answered
        # two empty bodies would pass an indistinguishability test while telling
        # the caller nothing at all.
        assert missing_ref[2]

    def test_PUT_to_a_refused_scope_is_BYTE_IDENTICAL_to_one_that_never_existed(
        self, tmp_path: Path
    ):
        root, present, absent = self._phases(tmp_path)

        def put(record):
            with running(root, tokens=(record,)) as (base, _):
                code, headers, body = fetch(
                    entry_url(base, DENY_SCOPE), token=record.token, method="PUT",
                    data=b"---\nservice: x\n---\n",
                    extra_headers={"If-Match": "0" * 16},
                )
            return code, _comparable(headers), body

        present()
        refused = put(ZACH)
        absent()
        never = put(ZACH)
        assert refused[0] == never[0] == 404
        assert refused[1] == never[1]
        assert refused[2] == never[2]
        assert refused[2]

        # 🔴 THE POSITIVE CONTROL, IN THE SAME TEST because the pair is the
        # evidence: a holder who MAY see that scope gets a 412 (a real
        # precondition answer about a real file), not the 404 above.
        present()
        allowed = put(DANA)
        assert allowed[0] == 412, allowed
        assert allowed[:2] != refused[:2]


class TestAppendIsCommutativeAndIdempotent:
    """🔴 CRITERION 5, AND THE SHIP GATE. The store is not re-derivable — it
    records gotchas, retracted theories and measurements that were true at a
    moment — so a lost append is lost forever. This is the one defect class here
    that destroys CONTENT rather than availability.
    """

    def test_two_CONCURRENT_appends_of_DIFFERENT_bullets_BOTH_survive(
        self, scoped_store: Path
    ):
        path = entry_file(scoped_store, ALLOW_SCOPE)
        results: "dict[str, tuple]" = {}
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            with forced_interleave() as (first_in, release):

                def writer(name: str, text: str, session: str) -> None:
                    results[name] = post_bullet(
                        base, ZACH_TOKEN, ALLOW_SCOPE, text=text, session=session
                    )

                first = threading.Thread(
                    target=writer, args=("a", BULLET_A, SESSION_A), daemon=True
                )
                first.start()
                assert first_in.wait(timeout=20), (
                    "the first writer never reached the interleave point — the "
                    "harness is not observing the critical section at all"
                )
                second = threading.Thread(
                    target=writer, args=("b", BULLET_B, SESSION_B), daemon=True
                )
                second.start()
                second.join(timeout=60)
                release.set()
                first.join(timeout=60)
                assert not first.is_alive() and not second.is_alive()

        assert results["a"][0] == results["b"][0] == 200, results
        text = path.read_text()
        assert BULLET_A in text, "the FIRST writer's bullet was lost"
        assert BULLET_B in text, "the SECOND writer's bullet was lost"
        bullets = resolver.parse_journal_bullets(nuance_of(path))
        assert len(bullets) == 3, [b.lines[0] for b in bullets]

    def test_the_interleave_harness_CAN_LOSE_an_append(self, scoped_store: Path):
        """🔴 THE NEGATIVE CONTROL FOR THE TEST ABOVE, and it is the mutation run
        as a test: with `_EntryLock` replaced by a lock that locks nothing, the
        identical scenario MUST lose a bullet. Without this, "both survived"
        cannot be told apart from a harness whose two writers never overlapped.

        🔴 THE TWO CODES ARE ASSERTED FIRST, AND THAT IS THE WHOLE POINT OF THE
        CONTROL. Without them "BULLET_B is not in the file" is satisfied by a
        second writer that was REFUSED and never wrote at all — a file nothing
        wrote to is trivially missing the bullet. Proven vacuous: injecting a
        server-side refusal into the write dispatch made this test pass while
        the well-shaped `test_an_exception_in_a_handler_is_a_500_with_an_AUDIT_LINE`
        failed under the identical injection. Its PUT twin
        (`test_the_CONCURRENT_PUT_harness_CAN_see_a_lost_update`) had the
        `[200, 200]` assertion from the start; this one did not.

        ⚠ THE CONCURRENCY PROPERTY ITSELF WAS NEVER UNSUPPORTED — `LOCK_EX` ->
        `LOCK_SH` has been watched to red the test above on "the SECOND writer's
        bullet was lost". What was unsupported was the IN-BAND control.
        """
        path = entry_file(scoped_store, ALLOW_SCOPE)
        results: "dict[str, tuple]" = {}
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            with no_entry_lock():
                with forced_interleave() as (first_in, release):

                    def writer(name: str, text: str, session: str) -> None:
                        results[name] = post_bullet(
                            base, ZACH_TOKEN, ALLOW_SCOPE, text=text, session=session
                        )

                    first = threading.Thread(
                        target=writer, args=("a", BULLET_A, SESSION_A), daemon=True
                    )
                    first.start()
                    assert first_in.wait(timeout=20)
                    second = threading.Thread(
                        target=writer, args=("b", BULLET_B, SESSION_B), daemon=True
                    )
                    second.start()
                    second.join(timeout=60)
                    release.set()
                    first.join(timeout=60)

        assert sorted(r[0] for r in results.values()) == [200, 200], (
            "a writer was REFUSED, so 'the second bullet is missing' is a fact "
            "about the refusal and not about the lock — this control proves "
            f"nothing in that state: {results}"
        )
        text = path.read_text()
        assert BULLET_A in text, "the surviving writer is the wrong one"
        assert BULLET_B not in text, (
            "the unlocked read-modify-write did NOT lose the second append — the "
            "harness cannot see the defect it claims to guard against, so the "
            "green test above is evidence of nothing"
        )

    def test_EIGHT_concurrent_appends_all_survive(self, scoped_store: Path):
        """No hook, real overlap, eight racers. A supplement to the forced
        interleave rather than a replacement: it cannot be relied on to catch a
        missing lock (some runs will serialise anyway) but it exercises the lock
        under genuine contention, which the two-writer version does not.

        🔴 THE FAILURE NAMES ITS OWN MECHANISM, BECAUSE ONE OCCURRENCE IS ALL
        YOU GET. This failed once in a saturated baseline run and then passed
        15/15 (3 immediate re-runs, then 12 on a quiet host) — so the
        discriminator that mattered was never captured, and the two candidate
        mechanisms call for OPPOSITE actions:

            `'…was lost'` with every code 200  -> an append was lost WITH THE
              LOCK IN PLACE. A DEFECT. Do not re-run; the serialisation is wrong.
            a short `codes` list, a `BrokenBarrierError`, or a live thread after
              `join` -> the WALL-CLOCK BOUND (`barrier.wait(timeout=30)`,
              `t.join(timeout=60)`) under host saturation. Not about the lock.

        The old assertions could not tell them apart: `codes == [200]*8` reds
        identically for "a writer was refused" and "a writer never got past the
        barrier", and `f"{text!r} was lost"` fires without ever saying whether
        the writer that owned that text actually completed. So every arm below
        is now labelled with its MECHANISM and carries the per-writer phase and
        elapsed time — `claude/RULES.md` separates load from a real assertion by
        WHOSE time moved, and that evidence has to be IN the failure output or
        the next single occurrence is as unresolvable as this one was.

        🔴 THE TIMEOUTS ARE DELIBERATELY NOT WIDENED. Widening them converts the
        load case into a pass and leaves the defect case looking identical —
        which hides exactly the mechanism this instrumentation exists to name.
        """
        path = entry_file(scoped_store, ALLOW_SCOPE)
        texts = [f"{BULLET_C} on run {n}" for n in range(8)]
        barrier = threading.Barrier(len(texts))
        outcomes: "dict[int, dict]" = {}
        lock = threading.Lock()
        started = time.time()
        with running(scoped_store, tokens=(ZACH,)) as (base, _):

            def writer(n: int, text: str) -> None:
                record = {
                    "n": n, "phase": "never-started", "code": None,
                    "error": None, "elapsed": None,
                }
                with lock:
                    outcomes[n] = record
                mark = time.time()
                try:
                    barrier.wait(timeout=30)
                except threading.BrokenBarrierError as exc:
                    record["phase"] = "barrier-broken"
                    record["error"] = f"{type(exc).__name__}: {exc}"
                    record["elapsed"] = round(time.time() - mark, 2)
                    return
                record["phase"] = "posting"
                mark = time.time()
                try:
                    code, _h, _b = post_bullet(
                        base, ZACH_TOKEN, ALLOW_SCOPE, text=text, session=SESSION_A
                    )
                except Exception as exc:  # noqa: BLE001 — recorded, then reported
                    record["phase"] = "post-raised"
                    record["error"] = f"{type(exc).__name__}: {exc}"
                    record["elapsed"] = round(time.time() - mark, 2)
                    return
                record["phase"] = "posted"
                record["code"] = code
                record["elapsed"] = round(time.time() - mark, 2)

            threads = [
                threading.Thread(target=writer, args=(n, t), daemon=True)
                for n, t in enumerate(texts)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)
            alive = sorted(n for n, t in enumerate(threads) if t.is_alive())
            wall = round(time.time() - started, 2)

        rows = sorted(outcomes.values(), key=lambda r: r["n"])
        report = (
            f"\n  wall={wall}s for {len(texts)} racers"
            f" (barrier timeout 30s, join timeout 60s)\n  "
            + "\n  ".join(
                f"#{r['n']} phase={r['phase']} code={r['code']} "
                f"elapsed={r['elapsed']}s err={r['error']}" for r in rows
            )
        )

        assert len(outcomes) == len(texts), (
            "MECHANISM = HARNESS. A writer thread never recorded an outcome at "
            "all, so the classification below cannot be trusted." + report
        )
        assert not alive, (
            f"MECHANISM = WALL-CLOCK BOUND (join). Writers {alive} were still "
            "running after join(timeout=60). The host was saturated; this says "
            "nothing about the entry lock. Re-run on a quiet host — do NOT widen "
            "the timeout." + report
        )
        broken = [r["n"] for r in rows if r["phase"] == "barrier-broken"]
        assert not broken, (
            f"MECHANISM = WALL-CLOCK BOUND (barrier). Writers {broken} never got "
            "past barrier.wait(timeout=30), so they never POSTed and no append "
            "was lost. Saturation, not the lock." + report
        )
        raised = [r["n"] for r in rows if r["phase"] == "post-raised"]
        assert not raised, (
            f"MECHANISM = TRANSPORT. The POST from writers {raised} raised "
            "instead of answering. Read the per-writer error: a connection error "
            "is not a lost append." + report
        )
        assert [r["code"] for r in rows] == [200] * len(texts), (
            "MECHANISM = REFUSED. Every writer answered, but not all with 200 — "
            "so a 'was lost' claim would be a fact about the refusal and not "
            "about the lock." + report
        )
        stored = path.read_text()
        lost = [t for t in texts if t not in stored]
        assert not lost, (
            "🔴 MECHANISM = LOST APPEND, WITH THE LOCK IN PLACE. All "
            f"{len(texts)} writers answered 200 and {len(lost)} bullet(s) are "
            f"absent from the entry: {lost!r}. This is the DEFECT case — the "
            "read-modify-write is not serialised. Do NOT re-run and move on."
            + report
        )
        bullets = resolver.parse_journal_bullets(nuance_of(path))
        assert len(bullets) == len(texts) + 1, (
            "🔴 MECHANISM = LOST OR DUPLICATED BULLET. Every text is present but "
            f"the journal holds {len(bullets)} bullets for {len(texts)} appends "
            "plus the fixture's one — a splice landed in the wrong place."
            + report
        )

    def test_re_POSTing_the_SAME_bullet_leaves_the_entry_BYTE_IDENTICAL(
        self, scoped_store: Path
    ):
        """🔴 ASSERTED ON BYTES, NOT ON A STATUS CODE. A server that appended a
        second identical bullet and answered 200 satisfies every status-based
        check, and the duplicate is then in the store forever."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            first = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_A, session=SESSION_A
            )
            after_first = path.read_bytes()
            second = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_A, session=SESSION_A
            )
        assert first[1]["X-Store-Status"] == "appended"
        assert second[1]["X-Store-Status"] == "duplicate"
        assert path.read_bytes() == after_first, "the re-POST changed the file"

    def test_the_duplicate_check_is_on_CONTENT_not_on_the_WHOLE_LINE(
        self, scoped_store: Path
    ):
        """Same text, DIFFERENT session — still a duplicate. A retry from a new
        agent run is the same observation, and hashing the rendered line would
        make every retry a new bullet."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            # The seeding append's verdict is ASSERTED, not discarded: if it
            # failed, `after_first` would be the untouched fixture and the
            # "unchanged" assertion below would hold for the wrong reason.
            seed, seed_h, seed_b = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_A, session=SESSION_A
            )
            assert (seed, seed_h["X-Store-Status"]) == (200, "appended"), seed_b
            after_first = path.read_bytes()
            code, headers, _b = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_A, session=SESSION_B
            )
        assert code == 200
        assert headers["X-Store-Status"] == "duplicate"
        assert path.read_bytes() == after_first

    def test_a_DIFFERENT_bullet_from_the_SAME_session_IS_appended(
        self, scoped_store: Path
    ):
        """Positive control for both duplicate tests: the no-op is decided by the
        CONTENT, so a different observation from the same session must land."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            # Same reason as above: a seeding append whose verdict is not read
            # turns "two appends" into "one append" without any assertion moving.
            seed, seed_h, seed_b = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_A, session=SESSION_A
            )
            assert (seed, seed_h["X-Store-Status"]) == (200, "appended"), seed_b
            after_first = path.read_bytes()
            code, headers, _b = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_B, session=SESSION_A
            )
        assert code == 200
        assert headers["X-Store-Status"] == "appended"
        assert path.read_bytes() != after_first
        assert BULLET_B in path.read_text()

    def test_a_bullet_ALREADY_in_the_entry_by_hand_is_a_duplicate_too(
        self, scoped_store: Path
    ):
        """The hash is taken over the CONTENT of the bullets already there, with
        the date opener and any attribution trailer stripped — so an append that
        repeats a hand-written line is recognised, not duplicated."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_bytes()
        # KELP_NUANCE is `- 2026-03-04: <prose>` — send exactly its prose.
        prose = KELP_NUANCE.split(": ", 1)[1]
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, _b = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=prose, session=SESSION_A
            )
        assert code == 200
        assert headers["X-Store-Status"] == "duplicate"
        assert path.read_bytes() == before

    def test_a_CONCURRENT_duplicate_still_writes_NOTHING(self, scoped_store: Path):
        """The idempotency check runs INSIDE the lock. Outside it, two identical
        POSTs racing would both read "not present" and both append."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        statuses: "list[str]" = []
        lock = threading.Lock()
        barrier = threading.Barrier(4)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):

            def writer() -> None:
                barrier.wait(timeout=30)
                _c, headers, _b = post_bullet(
                    base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_A, session=SESSION_A
                )
                with lock:
                    statuses.append(headers["X-Store-Status"])

            threads = [threading.Thread(target=writer, daemon=True) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)
                assert not t.is_alive()
        assert sorted(statuses) == ["appended", "duplicate", "duplicate", "duplicate"], (
            statuses
        )
        assert len(resolver.parse_journal_bullets(nuance_of(path))) == 2


class TestIfMatchIsRequiredAndChecked:
    """🔴 CRITERION 6. A whole-file PUT is the only primitive here that DESTROYS
    content rather than adding to it, so the precondition is the thing standing
    between a stale client and a lost update — and every refusal is asserted on
    the BYTES on disk, not on a status code.
    """

    def _replacement(self, scope: str) -> bytes:
        return _entry(entry_ref(scope), scope, nuance=f"- 2026-04-09: {BULLET_C}").encode()

    def _put(self, base: str, scope: str, data: bytes, if_match: str | None):
        headers = {} if if_match is None else {"If-Match": if_match}
        return fetch(
            entry_url(base, scope), token=ZACH_TOKEN, method="PUT", data=data,
            extra_headers=headers,
        )

    def test_the_CURRENT_revision_REPLACES_the_file(self, scoped_store: Path):
        """The positive control, and it comes first: every refusal below is only
        evidence if the same request WITH the right revision succeeds."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        data = self._replacement(ALLOW_SCOPE)
        revision = api.entry_revision(path.read_bytes())
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, _b = self._put(base, ALLOW_SCOPE, data, revision)
        assert code == 200, (code, headers)
        assert headers["X-Store-Status"] == "replaced"
        assert path.read_bytes() == data
        assert headers["ETag"] == f'"{api.entry_revision(data)}"'
        assert KELP_NUANCE not in path.read_text()

    def test_a_STALE_revision_is_412_and_the_file_is_UNCHANGED(
        self, scoped_store: Path
    ):
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_bytes()
        stale = api.entry_revision(b"whatever this store used to hold")
        assert stale != api.entry_revision(before)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, body = self._put(
                base, ALLOW_SCOPE, self._replacement(ALLOW_SCOPE), stale
            )
        assert code == 412, (code, body)
        assert headers["X-Store-Status"] == "precondition-failed"
        assert path.read_bytes() == before, "a stale PUT overwrote the entry"

    def test_the_412_carries_the_CURRENT_revision_so_a_retry_can_SUCCEED(
        self, scoped_store: Path
    ):
        """A client told only "no" cannot retry, and a client that cannot retry
        re-sends without the precondition. So the refusal names the revision, and
        the retry is exercised rather than assumed."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        data = self._replacement(ALLOW_SCOPE)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            _c, refused, _b = self._put(base, ALLOW_SCOPE, data, "f" * 16)
            current = refused["ETag"].strip('"')
            code, _h, _b2 = self._put(base, ALLOW_SCOPE, data, current)
        assert current == api.entry_revision(
            _entry(entry_ref(ALLOW_SCOPE), ALLOW_SCOPE, nuance=KELP_NUANCE).encode()
        )
        assert code == 200
        assert path.read_bytes() == data

    def test_a_MISSING_If_Match_is_428_and_the_file_is_UNCHANGED(
        self, scoped_store: Path
    ):
        """🔴 REQUIRED, NOT OPTIONAL. An optional precondition is no precondition:
        the caller that most needs it — a retry after a timeout, on a store two
        agents share — is exactly the one that would omit it."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_bytes()
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, _b = self._put(
                base, ALLOW_SCOPE, self._replacement(ALLOW_SCOPE), None
            )
        assert code == 428, code
        assert headers["X-Store-Status"] == "precondition-required"
        assert path.read_bytes() == before

    def test_If_Match_STAR_is_REFUSED_and_the_file_is_UNCHANGED(
        self, scoped_store: Path
    ):
        """`*` means "any current representation" — the one value that turns the
        guard off while looking like it is on."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_bytes()
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, _b = self._put(
                base, ALLOW_SCOPE, self._replacement(ALLOW_SCOPE), "*"
            )
        assert code == 400, code
        assert headers["X-Store-Status"] == "bad-request"
        assert path.read_bytes() == before

    @pytest.mark.parametrize("wrap", ['{rev}', '"{rev}"', 'W/"{rev}"'])
    def test_a_QUOTED_and_a_BARE_revision_name_the_SAME_revision(
        self, scoped_store: Path, wrap: str
    ):
        """HTTP spells an entity-tag quoted; a shell client will send it bare.
        Refusing one of the two would be a precondition that fails for a reason
        the caller cannot see."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        data = self._replacement(ALLOW_SCOPE)
        revision = api.entry_revision(path.read_bytes())
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, _h, _b = self._put(
                base, ALLOW_SCOPE, data, wrap.format(rev=revision)
            )
        assert code == 200, (wrap, code)
        assert path.read_bytes() == data

    def test_a_PUT_that_would_MALFORM_the_entry_is_refused_and_the_file_is_UNCHANGED(
        self, scoped_store: Path
    ):
        """🔴 THE WRITE PATH MAY NOT CREATE THE `MALFORMED` STATE THE READ PATH
        HAS SO MUCH MACHINERY FOR. The check is the index loader's OWN mapping,
        not a second opinion about what an entry is."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_bytes()
        revision = api.entry_revision(before)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, _b = self._put(
                base, ALLOW_SCOPE, b"no front matter here\n", revision
            )
        assert code == 422, code
        assert headers["X-Store-Status"] == "entry-shape"
        assert path.read_bytes() == before

    def test_the_MALFORM_guard_is_the_LOADER_and_a_VALID_entry_still_lands(
        self, scoped_store: Path
    ):
        """Positive control: the refusal above is about the CONTENT, not about
        PUT refusing everything."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        data = self._replacement(ALLOW_SCOPE)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, _h, _b = self._put(
                base, ALLOW_SCOPE, data, api.entry_revision(path.read_bytes())
            )
        assert code == 200
        # …and the reader agrees it is an entry, which is the claim the guard
        # actually makes.
        index = resolver.load_index(
            scoped_store, on_malformed=resolver.ON_MALFORMED_COLLECT
        )
        assert not index.malformed_in(ALLOW_SCOPE)

    def test_two_CONCURRENT_PUTs_on_ONE_revision_land_exactly_ONE(
        self, scoped_store: Path
    ):
        """🔴 THE LOST-UPDATE CASE, AND THE PRECONDITION IS CHECKED UNDER THE SAME
        LOCK THE WRITE HAPPENS UNDER. Outside it, both callers read revision R,
        both pass, and the second silently overwrites the first — which is the
        exact update the precondition exists to refuse.
        """
        path = entry_file(scoped_store, ALLOW_SCOPE)
        revision = api.entry_revision(path.read_bytes())
        first_data = self._replacement(ALLOW_SCOPE)
        second_data = _entry(
            entry_ref(ALLOW_SCOPE), ALLOW_SCOPE, nuance=f"- 2026-04-10: {BULLET_B}"
        ).encode()
        codes: "dict[str, int]" = {}
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            with forced_interleave() as (first_in, release):

                def writer(name: str, data: bytes) -> None:
                    codes[name] = self._put(base, ALLOW_SCOPE, data, revision)[0]

                first = threading.Thread(
                    target=writer, args=("a", first_data), daemon=True
                )
                first.start()
                assert first_in.wait(timeout=20)
                second = threading.Thread(
                    target=writer, args=("b", second_data), daemon=True
                )
                second.start()
                second.join(timeout=60)
                release.set()
                first.join(timeout=60)
                assert not first.is_alive() and not second.is_alive()
        assert sorted(codes.values()) == [200, 412], codes
        assert path.read_bytes() == first_data
        assert BULLET_B.encode() not in path.read_bytes()

    def test_the_CONCURRENT_PUT_harness_CAN_see_a_lost_update(
        self, scoped_store: Path
    ):
        """The negative control for the test above: with the lock removed, BOTH
        PUTs pass the precondition and the first writer's content is gone."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        revision = api.entry_revision(path.read_bytes())
        first_data = self._replacement(ALLOW_SCOPE)
        second_data = _entry(
            entry_ref(ALLOW_SCOPE), ALLOW_SCOPE, nuance=f"- 2026-04-10: {BULLET_B}"
        ).encode()
        codes: "dict[str, int]" = {}
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            with no_entry_lock():
                with forced_interleave() as (first_in, release):

                    def writer(name: str, data: bytes) -> None:
                        codes[name] = self._put(base, ALLOW_SCOPE, data, revision)[0]

                    first = threading.Thread(
                        target=writer, args=("a", first_data), daemon=True
                    )
                    first.start()
                    assert first_in.wait(timeout=20)
                    second = threading.Thread(
                        target=writer, args=("b", second_data), daemon=True
                    )
                    second.start()
                    second.join(timeout=60)
                    release.set()
                    first.join(timeout=60)
        assert sorted(codes.values()) == [200, 200], (
            "the unlocked PUT pair did NOT both pass the precondition — the "
            f"harness cannot see a lost update: {codes}"
        )
        assert path.read_bytes() == first_data


class TestTheAppendRequestIsValidated:
    """Every clause of `_bullet_request_problem` is reachable by an input every
    earlier clause accepts, and each is asserted on its OWN sentence — a test
    that went red because a DIFFERENT clause fired would be green with the clause
    it names deleted."""

    def _post_raw(self, base: str, body: bytes):
        return fetch(
            bullets_url(base, ALLOW_SCOPE), token=ZACH_TOKEN, method="POST", data=body
        )

    @pytest.mark.parametrize(
        "body,fragment",
        [
            (b"not json at all", b"must be JSON"),
            (b'"a string"', b"must be a JSON object"),
            (b'{"session": "sess-7f3a2b"}', b"`text` is required"),
            (b'{"text": "   ", "session": "sess-7f3a2b"}', b"`text` is required"),
            (b'{"text": "a\\nb", "session": "sess-7f3a2b"}', b"must be ONE line"),
            (b'{"text": "- a", "session": "sess-7f3a2b"}', b"must not open a markdown"),
            (b'{"text": "a real observation"}', b"`session` is required"),
            (b'{"text": "a real observation", "session": "has spaces"}',
             b"`session` is required"),
        ],
    )
    def test_a_MALFORMED_append_is_400_and_writes_NOTHING(
        self, scoped_store: Path, body: bytes, fragment: bytes
    ):
        before = tree_hash(scoped_store)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, resp = self._post_raw(base, body)
        assert code == 400, (body, code, resp)
        assert headers["X-Store-Status"] == "bad-request"
        assert fragment in resp, resp
        assert tree_hash(scoped_store) == before

    def test_an_OVERLONG_text_is_refused(self, scoped_store: Path):
        before = tree_hash(scoped_store)
        payload = {"text": "x" * (api.BULLET_TEXT_MAX + 1), "session": SESSION_A}
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, _h, resp = self._post_raw(base, json.dumps(payload).encode())
        assert code == 400
        assert b"characters, max" in resp
        assert tree_hash(scoped_store) == before

    def test_a_text_at_the_LIMIT_is_accepted(self, scoped_store: Path):
        """The other side of the boundary. A cap tested only from above is a cap
        tested on one side of its condition."""
        payload = {"text": "x" * api.BULLET_TEXT_MAX, "session": SESSION_A}
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, _b = self._post_raw(base, json.dumps(payload).encode())
        assert code == 200, code
        assert headers["X-Store-Status"] == "appended"

    def test_a_body_with_NO_Content_Length_writes_nothing(self, scoped_store: Path):
        """A write needs a body, and `_consume_body` reports "no body declared"
        distinctly from "framing refused" precisely so this can be answered."""
        before = tree_hash(scoped_store)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, _h, resp = fetch(
                bullets_url(base, ALLOW_SCOPE), token=ZACH_TOKEN, method="POST"
            )
        assert code == 400, (code, resp)
        assert tree_hash(scoped_store) == before


class TestTheWritePrimitives:
    """Unit-level, and pinned by MUTATION rather than by a red-at-base run: these
    names do not exist at the base ref, so their red there is an `AttributeError`
    and proves nothing about behaviour."""

    def test_a_revision_is_a_function_of_the_BYTES(self):
        assert api.entry_revision(b"alpha") == api.entry_revision(b"alpha")
        assert api.entry_revision(b"alpha") != api.entry_revision(b"alphb")
        assert len(api.entry_revision(b"alpha")) == api.CONTENT_HASH_CHARS

    def test_a_content_hash_ignores_WRAPPING_and_nothing_else(self):
        assert api.content_hash(BULLET_A) == api.content_hash(
            "  " + BULLET_A.replace(" ", "\t ") + "  "
        )
        assert api.content_hash(BULLET_A) != api.content_hash(BULLET_B)
        # …and it is not a constant: three distinct texts, three distinct hashes.
        assert len({api.content_hash(t) for t in (BULLET_A, BULLET_B, BULLET_C)}) == 3

    def test_a_rendered_bullet_round_trips_through_bullet_content(self):
        line = api.render_bullet(
            BULLET_B, actor="dana", session=SESSION_B, today="2026-04-11"
        )
        assert api.bullet_content([line]) == BULLET_B
        assert api.content_hash(api.bullet_content([line])) == api.content_hash(BULLET_B)

    def test_bullet_content_strips_an_opener_WITHOUT_a_trailer(self):
        assert api.bullet_content(["- 2026-04-11: " + BULLET_C]) == BULLET_C
        assert api.bullet_content(["- " + BULLET_C]) == BULLET_C
        # A trailer that is not THIS writer's trailer is content, not attribution.
        assert api.bullet_content([f"- {BULLET_C} [seen: dana]"]) == (
            f"{BULLET_C} [seen: dana]"
        )

    def test_bullet_content_joins_a_MULTI_LINE_bullet(self):
        assert api.bullet_content(["- 2026-04-11: " + BULLET_A, "  " + BULLET_B]) == (
            f"{BULLET_A} {BULLET_B}"
        )

    def test_the_insert_point_is_UNDER_the_nuance_heading(self):
        lines = _entry("x", "y").splitlines()
        index = api.nuance_insert_index(lines)
        assert index is not None
        assert lines[index - 1] == resolver.NUANCE_HEADING

    def test_there_is_NO_insert_point_without_the_heading(self):
        lines = _entry("x", "y").splitlines()
        lines = [ln for ln in lines if ln != resolver.NUANCE_HEADING]
        assert api.nuance_insert_index(lines) is None

    def test_a_heading_INSIDE_a_fence_is_not_the_heading(self):
        """The same rule `_heading_blocks` applies — imported rather than
        re-spelled, so the writer cannot come to disagree with the reader about
        where a section starts."""
        lines = [
            "```",
            resolver.NUANCE_HEADING,
            "```",
            resolver.NUANCE_HEADING,
            "- real",
        ]
        assert api.nuance_insert_index(lines) == 4

    def test_an_entry_with_no_NUANCE_heading_is_422_not_a_reshaped_file(
        self, tmp_path: Path
    ):
        root = _build_store(tmp_path / "store", {ALLOW_SCOPE: KELP_NUANCE})
        path = entry_file(root, ALLOW_SCOPE)
        text = path.read_text().replace(resolver.NUANCE_HEADING, "## Notes")
        path.write_text(text)
        before = path.read_bytes()
        with running(root, tokens=(ZACH,)) as (base, _):
            code, headers, _b = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_A, session=SESSION_A
            )
        assert code == 422, code
        assert headers["X-Store-Status"] == "entry-shape"
        assert path.read_bytes() == before

    def test_a_FAILED_write_leaves_the_entry_and_no_temp_file(
        self, tmp_path: Path, monkeypatch
    ):
        """🔴 `os.replace`, NOT `open(path, "w")`. A truncate-then-write leaves a
        window in which a concurrent reader sees an EMPTY or half-written entry
        and serves it as a complete one — the silent under-report this whole
        module is built against, produced by the WRITER.

        Driven by making the final `os.replace` fail: a truncating writer has
        already destroyed the entry by this point, `os.replace` has not, and the
        temp file must not be left behind either (it is invisible to every
        walker, so nothing would ever report or clean it up).
        """
        root = _build_store(tmp_path / "store", {ALLOW_SCOPE: KELP_NUANCE})
        path = entry_file(root, ALLOW_SCOPE)
        before = path.read_bytes()

        def boom(_src, _dst):
            raise OSError("no space left on device")

        monkeypatch.setattr(os, "replace", boom)
        # 🔴 `match=`, NOT A BARE `OSError`. `append_bullet` opens, writes and
        # fsyncs before it replaces, and any of those raising is also an
        # `OSError` — so a bare `raises` would be green for a failure that
        # happened BEFORE the window this test is about, which is the one state
        # where "the entry is unchanged" proves nothing.
        with pytest.raises(OSError, match="no space left"):
            api.append_bullet(
                path, text=BULLET_A, actor="zach", session=SESSION_A,
                today="2026-04-11",
            )
        monkeypatch.undo()
        assert path.read_bytes() == before
        assert not list(path.parent.glob(".cairn-*.tmp")), "a temp file was orphaned"

    def test_the_LOCK_FILE_is_invisible_to_every_walker(self, scoped_store: Path):
        """It has a leading dot AND no `.md` suffix, so all three of the store's
        walkers skip it twice over. Asserted through the walkers themselves."""
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            post_bullet(base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_A, session=SESSION_A)
            lock = scoped_store / ALLOW_SCOPE / f".{entry_ref(ALLOW_SCOPE)}.md.lock"
            assert lock.exists(), "the lock file was never created"
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=ZACH_TOKEN
            )
            _c, snap_headers, tar_bytes = fetch(
                f"{base}/api/v1/snapshot", token=ZACH_TOKEN
            )
        assert code == 200 and headers["X-Store-Status"] == "recalled"
        assert lock.name.encode() not in body
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as tar:
            names = tar.getnames()
        assert not any(n.endswith(".lock") for n in names), names
        # `entry-files=` counts `.md` only, so the lock must not move it. The
        # expected number is counted off DISK, not copied from the fixture.
        md_on_disk = len(list(scoped_store.rglob("*.md")))
        assert f"entry-files={md_on_disk}" in snap_headers["X-Store-Snapshot"], (
            snap_headers,
            md_on_disk,
        )


# =============================================================================
# AUDIT ROUND 2 — the write path's own defects. Every class below names a
# MEASURED behaviour, not a hypothetical: the fixtures are the ones the auditor
# reproduced with, and each test's red-at-`b32db213` state is recorded in the PR.
# =============================================================================


# 🔴 A FIXTURE BUILT OUT OF THE THREE THINGS THE OLD WRITER DESTROYED, in one
# file, because they were destroyed by ONE operation and a test that separated
# them would let two of the three regress unseen:
#
#   * `\xe9` — a latin-1 byte. Not valid UTF-8 anywhere. It appears TWICE, see
#     below.
#   * a `\r\n` line ending, twice.
#   * NO trailing newline.
#
# 🔴 THE BYTE IS IN **BOTH** SECTIONS, AND THAT PLACEMENT IS THE WHOLE POINT.
# It used to sit only in `## What it is`, and that made every assertion in this
# section STRUCTURALLY UNABLE to reach the branch that later broke: the append's
# dedupe hashes `bullet_content(...)` over the NUANCE BULLETS ONLY, so a hostile
# byte parked in another section is never fed to `content_hash` and a writer that
# crashes on one stayed green here. With it in both places neither location is
# load-bearing — the byte survives an append wherever it is, and the hashing path
# is exercised whether or not anyone remembers that it is the interesting one.
#
# The bytes are spelled out rather than built from `_entry()` because every one
# of them is load-bearing here, and `_entry()` can only emit LF and always ends
# with a newline.
_HEADING_BYTES = b"## Nuance / work-history\n"
LOSSY_ENTRY = (
    b"---\n"
    b"service: reef-buoy\n"
    b"scope: kelp-forest\n"
    b"sensitivity: internal\n"
    b"---\n"
    b"\n"
    b"## What it is\r\n"
    b"the tender's caf\xe9 log was written latin-1 and nobody re-encoded it\r\n"
    b"\n"
    + _HEADING_BYTES
    + b"- 2026-05-01: the anchor winch was re-greased at the caf\xe9 and left untested"
)

# Distinct from BULLET_A/B/C, from every nuance line, and from every literal any
# assertion in this section names.
BULLET_D = "the fog horn compressor cycles twice on a cold start"
BULLET_E = "the deck light dims whenever the davit is powered"
BULLET_F = "the tide predictor disagrees with the gauge on a neap"


class TestAnAppendDoesNotREWRITETheFile:
    """🔴 AN ORDINARY APPEND SILENTLY REWROTE THE WHOLE FILE, LOSSILY.

    `append_bullet` decoded with `errors="replace"`, `splitlines()`, and wrote
    back `"\\n".join(...) + "\\n"` — so one append to ONE line re-emitted every
    other line through a lossy round trip. Measured, all three at `200 appended`
    with no error:

      * a latin-1 `0xe9` on an untouched line became `U+FFFD`, permanently —
        while `replace_entry` decodes the same bytes `errors="strict"` and
        answers **422**. The two write primitives disagreed about what a valid
        entry is, and the LOSSY one was the primitive advertised as additive.
      * `\\r\\n` became `\\n`.
      * a file with no trailing newline gained one.

    Each also changes the entry revision, so every other client's cached
    `If-Match` is invalidated for a change nobody asked for.
    """

    def _entry_path(self, tmp_path: Path) -> Path:
        root = tmp_path / "store" / ALLOW_SCOPE
        root.mkdir(parents=True)
        path = root / f"{entry_ref(ALLOW_SCOPE)}.md"
        path.write_bytes(LOSSY_ENTRY)
        return path

    def test_every_byte_OUTSIDE_the_inserted_line_is_IDENTICAL(self, tmp_path: Path):
        """🔴 THE WHOLE CLAIM, PINNED ON BYTES. The expected file is spelled here
        as `prefix + <the one new line> + suffix` over the ORIGINAL bytes, so a
        writer that changed anything at all — an encoding, a line ending, a
        trailing newline — fails on the equality rather than on a property
        somebody remembered to check."""
        path = self._entry_path(tmp_path)
        head_end = LOSSY_ENTRY.index(_HEADING_BYTES) + len(_HEADING_BYTES)

        status, line, _rev = api.append_bullet(
            path, text=BULLET_D, actor="zach", session=SESSION_A, today="2026-05-02"
        )
        after = path.read_bytes()

        assert status == "appended"
        assert after == (
            LOSSY_ENTRY[:head_end]
            + (line + "\n").encode("utf-8")
            + LOSSY_ENTRY[head_end:]
        ), after

    def test_the_hostile_byte_is_INSIDE_A_NUANCE_BULLET_which_is_what_is_hashed(self):
        """🔴 THE ANTI-VACUITY CONTROL FOR EVERY ASSERTION BELOW, and the exact
        hole that let a 500 pass here for a whole audit round.

        `append_bullet` feeds `bullet_content(existing.lines)` — the NUANCE
        BULLETS and nothing else — to `content_hash`. A fixture whose only
        undecodable byte sat in `## What it is` therefore never put one through
        the hashing path, so "a non-UTF-8 byte survives an append" was answered
        by a writer that cannot append to such an entry at all.

        Asked of the reader's OWN section parser rather than by an `in` over the
        whole file, so a byte that drifts back out of the nuance block fails
        here instead of quietly re-vacuating the section.
        """
        assert b"\xe9" in LOSSY_ENTRY and b"\xef\xbf\xbd" not in LOSSY_ENTRY
        assert LOSSY_ENTRY.count(b"\xe9") == 2, "the fixture stopped covering BOTH"
        nuance = nuance_bytes_of(LOSSY_ENTRY)
        assert "\udce9" in nuance, nuance
        what = resolver.extract_sections(
            LOSSY_ENTRY.decode("utf-8", errors="surrogateescape"),
            (resolver.WHAT_HEADING,),
        )[resolver.WHAT_HEADING]
        assert "\udce9" in what, what

    def test_a_NON_UTF8_byte_on_an_untouched_line_SURVIVES(self, tmp_path: Path):
        """Named separately from the equality above because this is the one that
        DESTROYS content the store cannot re-derive, and because `U+FFFD` is the
        specific corpse to look for."""
        path = self._entry_path(tmp_path)
        assert b"\xe9" in LOSSY_ENTRY and b"\xef\xbf\xbd" not in LOSSY_ENTRY

        status, line, _rev = api.append_bullet(
            path, text=BULLET_E, actor="zach", session=SESSION_A, today="2026-05-02"
        )
        after = path.read_bytes()

        # 🔴 THE POSITIVE CONTROL, AND IT COMES FIRST. "the bytes are unchanged"
        # is trivially true of a file NOTHING WROTE TO — a writer that raised, or
        # refused, or returned `duplicate`, satisfies the preservation assertions
        # below completely. So the append is proved to have LANDED before its
        # non-destructiveness is claimed.
        assert status == "appended", status
        assert line.encode("utf-8") in after, "the new bullet never reached the file"

        assert b"\xe9" in after, "the latin-1 byte was destroyed by an append"
        assert b"\xef\xbf\xbd" not in after, (
            "the append replaced an undecodable byte with U+FFFD"
        )

    def test_CRLF_line_endings_are_NOT_normalised(self, tmp_path: Path):
        path = self._entry_path(tmp_path)
        before_crlf = LOSSY_ENTRY.count(b"\r\n")
        assert before_crlf == 2, "the fixture stopped exercising CRLF"

        status, line, _rev = api.append_bullet(
            path, text=BULLET_F, actor="zach", session=SESSION_A, today="2026-05-02"
        )
        after = path.read_bytes()

        # Positive control, same reason as above: an unwritten file has exactly
        # the CRLF count it started with.
        assert status == "appended", status
        assert line.encode("utf-8") in after, "the new bullet never reached the file"

        assert after.count(b"\r\n") == before_crlf

    def test_a_file_with_NO_trailing_newline_does_not_gain_one(self, tmp_path: Path):
        path = self._entry_path(tmp_path)
        assert not LOSSY_ENTRY.endswith(b"\n")

        status, line, _rev = api.append_bullet(
            path, text=BULLET_D, actor="zach", session=SESSION_A, today="2026-05-02"
        )
        after = path.read_bytes()

        # Positive control, same reason as above: a file nobody wrote to has
        # exactly the trailing newline it started without.
        assert status == "appended", status
        assert line.encode("utf-8") in after, "the new bullet never reached the file"

        assert not after.endswith(b"\n"), (
            "the append added a trailing newline to a file that had none"
        )

    def test_the_bullet_INHERITS_the_headings_own_line_ending(self, tmp_path: Path):
        """A CRLF entry must not gain an LF-terminated line in the middle of it.
        The terminator is taken from the heading the bullet is inserted under,
        never assumed."""
        root = tmp_path / "store" / ALLOW_SCOPE
        root.mkdir(parents=True)
        path = root / f"{entry_ref(ALLOW_SCOPE)}.md"
        original = (
            b"---\r\nservice: crlf-only\r\nscope: kelp-forest\r\n---\r\n\r\n"
            b"## Nuance / work-history\r\n"
            b"- 2026-05-01: the bilge alarm chirps once at power-up\r\n"
        )
        path.write_bytes(original)

        _s, line, _r = api.append_bullet(
            path, text=BULLET_F, actor="zach", session=SESSION_A, today="2026-05-02"
        )
        after = path.read_bytes()

        assert (line + "\r\n").encode("utf-8") in after
        assert b"\n" not in after.replace(b"\r\n", b""), (
            "a bare LF was introduced into a CRLF-only file"
        )

    def test_a_NO_trailing_newline_entry_whose_HEADING_is_the_LAST_line(
        self, tmp_path: Path
    ):
        """The boundary the terminator rule turns on: there is no line ending to
        inherit, so one is introduced BEFORE the bullet and the file still does
        not end in a newline."""
        root = tmp_path / "store" / ALLOW_SCOPE
        root.mkdir(parents=True)
        path = root / f"{entry_ref(ALLOW_SCOPE)}.md"
        original = (
            b"---\nservice: bare-tail\nscope: kelp-forest\n---\n\n"
            b"## Nuance / work-history"
        )
        path.write_bytes(original)

        _s, line, _r = api.append_bullet(
            path, text=BULLET_E, actor="zach", session=SESSION_A, today="2026-05-02"
        )
        after = path.read_bytes()

        assert after == original + b"\n" + line.encode("utf-8")
        assert not after.endswith(b"\n")


# The one substring of `KELP_NUANCE` the hostile byte is spliced next to. Named
# so the fixture builder can assert it occurs EXACTLY ONCE store-wide — a
# `bytes.replace` that hit a second site would put the byte somewhere no
# assertion below describes.
_NUANCE_ANCHOR = b"spring flood"


def lossy_scoped_entry(root: Path) -> Path:
    """The ALLOW_SCOPE entry of a `scoped_store`, with ONE latin-1 `0xe9` spliced
    into its NUANCE BULLET — and nowhere else.

    🔴 BUILT BY MUTATING `_build_store`'s OWN OUTPUT rather than spelled by hand.
    The entry has to survive `rc.load_store` for the route to reach the writer at
    all: a hand-written file that the index loader classified as MALFORMED would
    answer `404 ref-unknown`, and every assertion about the write path below
    would then be measuring a 404 while reading as coverage of an append.
    """
    path = entry_file(root, ALLOW_SCOPE)
    original = path.read_bytes()
    assert original.count(_NUANCE_ANCHOR) == 1, original
    assert nuance_bytes_of(original).count(_NUANCE_ANCHOR.decode()) == 1
    hostile = original.replace(_NUANCE_ANCHOR, b"caf\xe9 spring flood")
    assert hostile != original and hostile.count(b"\xe9") == 1
    path.write_bytes(hostile)
    os.utime(path, (FIXED_MTIME, FIXED_MTIME))
    return path


class TestAnEntryWithANonUTF8ByteInABulletIsSTILLAPPENDABLE:
    """🔴 THE FIX FOR THE LOSSY REWRITE MADE ONE LEGACY BYTE PERMANENTLY
    UNAPPENDABLE — over HTTP, on the real route, `500 internal-error`.

    The append decodes the whole file `errors="surrogateescape"`, which is what
    makes the round trip a bijection; that decode yields LONE SURROGATES for
    every byte that is not valid UTF-8. `content_hash` then re-encoded the
    dedupe text with PLAIN `"utf-8"`, which refuses to encode a surrogate:

        server.py:2011  if content_hash(bullet_content(existing.lines)) == wanted
        server.py:1743  " ".join(text.split()).encode("utf-8")
        UnicodeEncodeError: 'utf-8' codec can't encode character '\\udce9'

    Measured end to end over HTTP and isolated by property, at the ref that
    introduced it:

        plain             -> 200 appended
        latin-1 in bullet -> 500 internal-error      <- the regression
        crlf-only         -> 200 appended
        no trailing \\n    -> 200 appended

    🔴 AND WHY IT IS NOT MERELY COSMETIC. The old writer corrupted the byte
    silently; this refused the append entirely, so ONE legacy byte in ONE bullet
    made that entry unappendable FOREVER — an availability regression on a store
    whose entire purpose is accumulating notes, and on exactly the entry shape
    the fix was written to protect.

    🔴 THE CLASS ABOVE COULD NOT SEE IT, TWICE OVER, and both holes are closed
    rather than worked around:

      1. Its fixture put the byte in `## What it is`, a section
         `bullet_content` never reads — so the crashing branch was unreachable.
         `LOSSY_ENTRY` now carries the byte in BOTH sections.
      2. It called `api.append_bullet` DIRECTLY. That proves the library
         function preserves bytes while the ROUTE still 500s: the isolation
         seam, two surfaces each verified alone and broken together. Every test
         here goes over a real socket.

    🔴 AND EVERY ONE OF THEM PROVES THE WRITE LANDED BEFORE CLAIMING THE OLD
    BYTES SURVIVED. "the pre-existing bytes are unchanged" is trivially true of
    a file NOTHING WROTE TO, which is precisely how the earlier assertion passed
    over a 500.
    """

    def test_PAIRED_CONTROL_the_same_POST_against_an_ASCII_entry_is_200(
        self, scoped_store: Path
    ):
        """🔴 Without this, every 200 below is satisfied by a store that would
        answer 200 for any reason at all, and every 500 by a broken harness. Same
        store, same token, same call shape, same text — the ONLY difference in
        the test that follows is one byte inside one bullet."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        with running(scoped_store, tokens=(ZACH,)) as (base, _audit):
            code, headers, body = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_D, session=SESSION_A
            )
        assert code == 200, (code, body)
        assert headers["X-Store-Status"] == "appended"
        assert BULLET_D.encode("utf-8") in path.read_bytes()

    def test_the_append_SUCCEEDS_over_the_REAL_ROUTE(self, scoped_store: Path):
        """🔴 THE REGRESSION, ON THE WIRE. `500` at the ref this fix lands on."""
        path = lossy_scoped_entry(scoped_store)
        before = path.read_bytes()

        with running(scoped_store, tokens=(ZACH,)) as (base, _audit):
            code, headers, body = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_D, session=SESSION_A
            )
        after = path.read_bytes()

        assert code == 200, (code, headers, body)
        assert headers["X-Store-Status"] == "appended"
        # 🔴 THE POSITIVE CONTROL: the write actually LANDED. Asked of the
        # reader's own section parser, so "the bullet is on disk" cannot be
        # satisfied by prose that landed outside the nuance block.
        assert after != before, "the route answered 200 and wrote nothing"
        assert BULLET_D in nuance_bytes_of(after), nuance_bytes_of(after)
        assert f"[cairn: zach/{SESSION_A}]" in nuance_bytes_of(after)

    def test_the_PRE_EXISTING_undecodable_byte_survives_the_append(
        self, scoped_store: Path
    ):
        path = lossy_scoped_entry(scoped_store)
        assert b"\xe9" in path.read_bytes()

        with running(scoped_store, tokens=(ZACH,)) as (base, _audit):
            code, headers, body = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_E, session=SESSION_A
            )
        after = path.read_bytes()

        # Positive control first — see the class docstring.
        assert code == 200, (code, headers, body)
        assert BULLET_E in nuance_bytes_of(after)

        assert b"\xe9" in after, "the latin-1 byte was destroyed by an append"
        assert b"\xef\xbf\xbd" not in after, (
            "the append replaced an undecodable byte with U+FFFD"
        )

    def test_the_RESPONSE_BODY_carries_the_rendered_bullet(self, scoped_store: Path):
        """The append's response body is `(line + "\\n").encode(...)`, so a
        surrogate reaching `line` would 500 AFTER the file was already written —
        the worst shape of all, a durable write reported as a server error."""
        path = lossy_scoped_entry(scoped_store)
        with running(scoped_store, tokens=(ZACH,)) as (base, _audit):
            code, _h, body = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_F, session=SESSION_A
            )
        assert code == 200, (code, body)
        assert BULLET_F.encode("utf-8") in body, body
        assert body.decode("utf-8").strip() in nuance_bytes_of(path.read_bytes())

    def test_the_audit_line_says_APPENDED_not_internal_error(
        self, scoped_store: Path
    ):
        """🔴 The dispatch backstop is what turned this from a dropped connection
        into an answered `500 internal-error` WITH an audit line — which is how
        the regression was found at all. The line is asserted to say `appended`
        so that a future crash caught by that same backstop cannot pass here."""
        lossy_scoped_entry(scoped_store)
        with running(scoped_store, tokens=(ZACH,)) as (base, audit):
            post_bullet(base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_D, session=SESSION_A)
            line = await_audit(audit, 1)[0]
        assert "status=appended" in line, line
        assert "result=200" in line, line
        assert "internal-error" not in line, line

    def test_a_RE_POST_of_the_SAME_text_is_a_DUPLICATE_and_writes_NOTHING(
        self, scoped_store: Path
    ):
        """🔴 THE OTHER DIRECTION OF THE SAME HASH. Idempotency is decided by
        `content_hash` over the stored bullets — the very call that raised — so
        an entry carrying an undecodable byte must still be able to RECOGNISE a
        repeat, not merely to accept a new one. A fix that made `content_hash`
        return a constant would pass the append tests above and fail here.
        """
        path = lossy_scoped_entry(scoped_store)
        with running(scoped_store, tokens=(ZACH,)) as (base, _audit):
            first, h1, b1 = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_D, session=SESSION_A
            )
            after_first = path.read_bytes()
            second, h2, b2 = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_D, session=SESSION_B
            )
        assert (first, h1["X-Store-Status"]) == (200, "appended"), (first, b1)
        assert (second, h2["X-Store-Status"]) == (200, "duplicate"), (second, b2)
        assert path.read_bytes() == after_first, "the duplicate wrote to the file"
        # …and the PRE-EXISTING hostile bullet is still not confused with it: a
        # `content_hash` that collapsed every surrogate-bearing bullet to one
        # value would have answered `duplicate` on the FIRST post.
        assert b"\xe9" in after_first

    def test_the_hostile_bullet_is_NOT_hash_equal_to_its_REPAIRED_spelling(
        self, scoped_store: Path
    ):
        """🔴 THE BIJECTION, STATED AS BEHAVIOUR. `surrogateescape` must map the
        raw `0xe9` back to `0xe9` — not to `é` (U+00E9, `0xc3 0xa9`) and not to
        `U+FFFD`. If it did, a client POSTing the correctly-encoded spelling of a
        legacy bullet would be told `duplicate` and its correction silently
        dropped; here it is a genuinely new bullet and it lands.
        """
        path = lossy_scoped_entry(scoped_store)
        bullets = resolver.parse_journal_bullets(nuance_bytes_of(path.read_bytes()))
        stored = api.bullet_content(bullets[0].lines)
        assert "\udce9" in stored, stored
        repaired = stored.replace("\udce9", "é")
        assert "\udce9" not in repaired and "é" in repaired

        with running(scoped_store, tokens=(ZACH,)) as (base, _audit):
            code, headers, body = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=repaired, session=SESSION_A
            )
        assert code == 200, (code, body)
        assert headers["X-Store-Status"] == "appended", body
        after = path.read_bytes()
        assert b"caf\xc3\xa9" in after, "the correctly-encoded spelling did not land"
        assert b"caf\xe9" in after, "the legacy byte was overwritten by the repair"


class TestTheEntryTextCodecIsONERuleInONEPlace:
    """🔴 THE DECODE AND THE ENCODE MUST AGREE, EVERYWHERE, and the regression
    above was exactly one site where they did not.

    `append_bullet` decodes the entry `surrogateescape` and then re-encoded at
    three separate sites — the splice offset (`surrogateescape`), the inserted
    line (plain), and the idempotency hash (plain). Three call sites deciding one
    thing is the predicate-at-N-sites shape: it was wrong at one of them, and the
    disagreement was inaudible until a byte reached it. There is now ONE encoder
    and ONE decoder and every site calls them.
    """

    # Pairwise distinct, and each a DIFFERENT reason to be undecodable: a lone
    # continuation byte, a truncated 2-byte lead, a UTF-16 BOM, a valid
    # multi-byte character (which must survive unchanged), and a mix with a CRLF.
    HOSTILE = [
        b"",
        b"caf\xe9 log",
        b"\x80",
        b"\xc3",
        b"\xff\xfe",
        "café log".encode("utf-8"),
        b"a\xe9b\r\nc\xf0d",
    ]

    @pytest.mark.parametrize("raw", HOSTILE)
    def test_the_round_trip_is_a_BIJECTION_on_BYTES(self, raw: bytes):
        assert api.encode_entry_text(api.decode_entry_text(raw)) == raw

    def test_content_hash_hashes_the_ORIGINAL_BYTES(self):
        """🔴 PINNED TO A DIGEST COMPUTED HERE FROM THE RAW BYTES, never from the
        function under test. A `content_hash` that encoded `"utf-8"` raises on
        this input; one that encoded `errors="replace"` returns the digest of
        `U+FFFD` and fails on the value.
        """
        raw = b"the tender's caf\xe9 log"
        expected = hashlib.sha256(raw).hexdigest()[:16]
        assert api.content_hash(raw.decode("utf-8", "surrogateescape")) == expected
        assert len(expected) == len(api.content_hash("anything at all"))

    def test_a_CLEAN_string_hashes_IDENTICALLY_to_before(self):
        """The fix must not move the hash of any bullet already in the corpus —
        that would re-open every entry's idempotency and re-write the world. The
        digest is spelled from plain UTF-8 bytes, which is what the old code
        computed."""
        text = "the tide gauge drifts 3cm after a spring flood."
        assert api.content_hash(text) == (
            hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        )

    def test_NEGATIVE_CONTROL_a_surrogate_is_UNENCODABLE_by_plain_utf8(self):
        """Proof that the parametrized bijection above is testing something: the
        SAME string, through the encoder the code used to use, raises."""
        text = b"caf\xe9".decode("utf-8", "surrogateescape")
        with pytest.raises(UnicodeEncodeError):
            text.encode("utf-8")


class TestAPUTOfUndecodableBytesIs422NotA500:
    """🔴 THE SIBLING WRITE PRIMITIVE, CHECKED FOR THE SAME MISMATCH.

    `replace_entry` decodes the PUT body `errors="strict"` — deliberately, and
    that is NOT the append's handler. A PUT is the one primitive that can
    DESTROY content, so bytes the reader could not parse are refused rather than
    written, and the refusal is the documented `422 unprocessable`.

    The two handlers disagreeing is the whole defect class above, so the
    difference is pinned as INTENDED behaviour rather than left to be rediscovered
    as a bug: append round-trips any bytes, PUT refuses the ones it cannot read.
    """

    def _if_match(self, path: Path) -> dict[str, str]:
        return {"If-Match": f'"{api.entry_revision(path.read_bytes())}"'}

    def test_a_PUT_of_a_body_with_a_non_UTF8_byte_is_422(self, scoped_store: Path):
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_bytes()
        body = before.replace(_NUANCE_ANCHOR, b"caf\xe9 spring flood")
        assert body != before

        with running(scoped_store, tokens=(ZACH,)) as (base, _audit):
            code, headers, resp = fetch(
                entry_url(base, ALLOW_SCOPE),
                token=ZACH_TOKEN,
                method="PUT",
                data=body,
                extra_headers=self._if_match(path),
            )
        assert code == 422, (code, resp)
        assert headers["X-Store-Status"] == "entry-shape"
        assert path.read_bytes() == before, "a refused PUT wrote to the file"

    def test_POSITIVE_CONTROL_the_SAME_edit_correctly_encoded_is_200(
        self, scoped_store: Path
    ):
        """🔴 Without this the 422 above is satisfied by a PUT route that refuses
        EVERYTHING. Same target, same precondition, same edit — spelled in
        well-formed UTF-8."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_bytes()
        body = before.replace(_NUANCE_ANCHOR, "café spring flood".encode("utf-8"))
        assert body != before

        with running(scoped_store, tokens=(ZACH,)) as (base, _audit):
            code, headers, resp = fetch(
                entry_url(base, ALLOW_SCOPE),
                token=ZACH_TOKEN,
                method="PUT",
                data=body,
                extra_headers=self._if_match(path),
            )
        assert code == 200, (code, resp)
        assert headers["X-Store-Status"] == "replaced"
        assert path.read_bytes() == body


class TestTextIsValidatedAgainstEVERYLineBreak:
    """🔴 `str.splitlines()` SPLITS ON TEN CHARACTERS AND THE VALIDATOR CHECKED
    TWO. `if "\\n" in text or "\\r" in text` is a membership test on two
    characters standing in for a predicate about ten.

    MEASURED with a paired control — the same payload twice, once with a plain
    `" - "` separator and once with a literal `U+2028`. The control stayed ONE
    line; the probe became TWO stored bullets, the first carrying the caller's
    prose with **no attribution trailer at all** and the second an `OPEN:`-marked
    bullet whose leading `[cairn: …]` an operator reads as a DIFFERENT person's
    attribution. One `200 appended` by `zach`; one forged-looking record.

    Every one of the ten is exercised, not a sample: the defect was precisely
    that a hand-picked subset stood in for the class.
    """

    # Spelled out here by hand rather than imported from `api.LINE_BREAK_CHARS`,
    # which would assert `x == x` and stay green if the module's own set shrank.
    ALL_BREAKS = [
        "\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", " ", " ",
    ]

    def test_the_set_this_class_exercises_is_the_set_splitlines_ACTUALLY_splits_on(
        self,
    ):
        """The list above is only evidence if it is complete. Asked of
        `str.splitlines()` itself, over the whole BMP-and-then-some, so a
        character nobody thought of fails this rather than slipping through every
        parametrized case below."""
        found = [
            chr(cp)
            for cp in range(0x11000)
            if len(f"a{chr(cp)}b".splitlines()) > 1
        ]
        assert sorted(found) == sorted(self.ALL_BREAKS), found

    @pytest.mark.parametrize("break_char", ALL_BREAKS)
    def test_a_LINE_BREAK_in_text_is_400_and_writes_NOTHING(
        self, scoped_store: Path, break_char: str
    ):
        """⚠ 8 of these 10 are pinned on the MESSAGE, not on this clause: the
        eight `Cc` characters are ALSO refused by `_FORBIDDEN_CATEGORIES`, so
        deleting the line-break predicate leaves them 400 with a different
        sentence. `must be ONE line` is what keeps them honest — it pins WHICH
        clause wins, a deliberate UX ordering — but the coverage that only this
        clause can provide is `U+2028`/`U+2029` (`Zl`/`Zp`, not in
        `_FORBIDDEN_CATEGORIES`), and it is in this same parametrization.
        Redundancy, accepted and now stated rather than left invisible.
        """
        before = tree_hash(scoped_store)
        text = f"{BULLET_D}{break_char}OPEN: {BULLET_E}"
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, body = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=text, session=SESSION_A
            )
        assert code == 400, (hex(ord(break_char)), code, body)
        assert headers["X-Store-Status"] == "bad-request"
        # 🔴 THE VALIDATOR'S OWN SENTENCE. A test satisfied by any 400 would be
        # green with this clause deleted and a different clause firing.
        assert b"must be ONE line" in body, body
        assert tree_hash(scoped_store) == before

    def test_PAIRED_CONTROL_the_SAME_payload_with_a_plain_separator_is_ONE_line(
        self, scoped_store: Path
    ):
        """🔴 WITHOUT THIS THE PARAMETRIZED REFUSAL ABOVE IS SATISFIED BY A SERVER
        THAT 400s EVERY APPEND. Same prose, same shape, an ordinary `" - "` where
        the break was: accepted, and exactly ONE bullet appears."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before_lines = len(path.read_text().splitlines())
        text = f"{BULLET_D} - OPEN: {BULLET_E}"
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, _b = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=text, session=SESSION_A
            )
        assert code == 200, code
        assert headers["X-Store-Status"] == "appended"
        stored = path.read_text()
        assert len(stored.splitlines()) == before_lines + 1, stored
        assert f"[cairn: zach/{SESSION_A}]" in stored

    @pytest.mark.parametrize("break_char", ALL_BREAKS)
    def test_a_LEADING_or_TRAILING_line_break_is_refused_too(
        self, scoped_store: Path, break_char: str
    ):
        """`"a\\n".splitlines()` is ONE element, so the count predicate alone
        cannot see a break at either end — and a bullet that opens or closes with
        an empty line is the same content-attached-to-the-wrong-bullet defect one
        line over."""
        before = tree_hash(scoped_store)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            for text in (f"{break_char}{BULLET_F}", f"{BULLET_F}{break_char}"):
                code, _h, body = post_bullet(
                    base, ZACH_TOKEN, ALLOW_SCOPE, text=text, session=SESSION_A
                )
                assert code == 400, (hex(ord(break_char)), repr(text), code)
                assert b"must be ONE line" in body, body
        assert tree_hash(scoped_store) == before


class TestTextRejectsControlAndFormattingCharacters:
    """🔴 EACH OF THESE WAS MEASURED LANDING IN THE CURATED FILE AT `200
    appended`. None of them is a line break, so the widened line-break predicate
    does not cover them and a second clause is required — in the SAME validator,
    because a character rule at two sites is a character rule that disagrees with
    itself.
    """

    HOSTILE = [
        ("\x00", "makes git and grep read the entry as BINARY"),
        ("\x1b", "an ANSI escape rewrites the reader's terminal"),
        ("\t", "a tab is a control character and a bullet is one line of prose"),
        ("‮", "a bidi override reorders what an operator reads"),
        ("⁦", "a bidi isolate does the same, in a newer shape"),
        ("​", "zero width: invisible AND it defeats idempotency"),
        ("‍", "a joiner is invisible between two visible characters"),
        ("­", "a soft hyphen renders as nothing, or as a hyphen"),
        ("﻿", "a BOM in mid-line is invisible everywhere"),
    ]

    @pytest.mark.parametrize("char,why", HOSTILE)
    def test_a_HOSTILE_character_is_400_and_writes_NOTHING(
        self, scoped_store: Path, char: str, why: str
    ):
        before = tree_hash(scoped_store)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, body = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE,
                text=f"{BULLET_E}{char}{BULLET_F}", session=SESSION_A,
            )
        assert code == 400, (why, code, body)
        assert headers["X-Store-Status"] == "bad-request"
        # 🔴 The clause's OWN sentence, naming the CODE POINT it refused — so a
        # green here cannot be a different clause firing, and the message is
        # actionable for a character the caller cannot see.
        assert f"U+{ord(char):04X}".encode() in body, (why, body)
        assert tree_hash(scoped_store) == before

    def test_ORDINARY_non_ASCII_prose_is_still_ACCEPTED(self, scoped_store: Path):
        """🔴 THE POSITIVE CONTROL. A rule that refused all non-ASCII would pass
        every case above and quietly make the store English-only."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        text = "the café gauge reads 3 °C low — naïve calibration, 5 µs skew"
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, _b = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=text, session=SESSION_A
            )
        assert code == 200, code
        assert headers["X-Store-Status"] == "appended"
        assert text in path.read_text(encoding="utf-8")

    def test_two_VISUALLY_IDENTICAL_bullets_cannot_both_land(
        self, scoped_store: Path
    ):
        """The idempotency half of the zero-width finding: `A\\u200bB` and `AB`
        render identically, so accepting the first would let a retry
        double-record something an operator cannot tell apart on screen."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            first = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_D, session=SESSION_A
            )
            after_first = path.read_bytes()
            second = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE,
                text=BULLET_D.replace(" ", "​ ", 1), session=SESSION_A,
            )
        assert first[0] == 200 and first[1]["X-Store-Status"] == "appended"
        assert second[0] == 400, second
        assert path.read_bytes() == after_first


class TestADeepJSONBodyIsAnswered:
    """🔴 `RecursionError` IS NOT A `ValueError`, AND `json.loads` RAISES IT.

    The handler caught `(UnicodeDecodeError, ValueError)`, so a 400 KB body of
    `[[[[…]]]]` escaped the handler entirely: the connection was dropped with no
    response, no `X-Store-Status` and — the part that matters — **no audit line**,
    on a request that had already been metered and authenticated.
    """

    BODY = b"[" * 200_000 + b"]" * 200_000

    def test_the_deep_body_is_a_400_and_the_AUDIT_LINE_IS_WRITTEN(
        self, scoped_store: Path
    ):
        before = tree_hash(scoped_store)
        assert len(self.BODY) < api.MAX_DRAIN_BYTES, "the body must reach the parser"
        with running(scoped_store, tokens=(ZACH,)) as (base, audit):
            code, headers, body = fetch(
                bullets_url(base, ALLOW_SCOPE), token=ZACH_TOKEN, method="POST",
                data=self.BODY,
            )
            lines = await_audit(audit, 1)
        assert code == 400, (code, body)
        assert headers["X-Store-Status"] == "bad-request"
        assert b"must be JSON" in body, body
        assert any("result=400" in ln and "status=bad-request" in ln for ln in lines), lines
        assert tree_hash(scoped_store) == before

    def test_POSITIVE_CONTROL_a_SHALLOW_body_of_the_same_shape_still_parses(
        self, scoped_store: Path
    ):
        """The refusal above must be about the DEPTH, not about brackets or about
        a large body. A nested list the parser can handle is still refused — as
        `the body must be a JSON object` — which is a DIFFERENT clause, and that
        difference is the evidence."""
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, _h, body = fetch(
                bullets_url(base, ALLOW_SCOPE), token=ZACH_TOKEN, method="POST",
                data=b"[" * 50 + b"]" * 50,
            )
        assert code == 400
        assert b"must be a JSON object" in body, body
        assert b"must be JSON (" not in body, body


class TestTheDedupeScopeIsTheINSERTIONScope:
    """🔴 A `duplicate` VERDICT DECIDED BY A SECTION THE WRITER WOULD NEVER TOUCH.

    `nuance_insert_index` takes the FIRST `## Nuance / work-history` heading; the
    duplicate check read `rc.extract_sections`, which CONCATENATES every block
    sharing a heading. So a genuinely new bullet that happened to match one in
    the SECOND section answered `200 duplicate` and wrote nothing — content loss
    in the direction this module's own docstring calls the one that matters, and
    silent, because the response says the observation is already recorded.
    """

    FIRST_PROSE = "the mooring pennant chafes against the fairlead"
    SECOND_PROSE = "the stern gland weeps a drop a minute under way"

    def _twin_heading_entry(self, tmp_path: Path) -> Path:
        root = tmp_path / "store" / ALLOW_SCOPE
        root.mkdir(parents=True)
        path = root / f"{entry_ref(ALLOW_SCOPE)}.md"
        path.write_text(
            "\n".join(
                [
                    "---",
                    f"service: {entry_ref(ALLOW_SCOPE)}",
                    f"scope: {ALLOW_SCOPE}",
                    "sensitivity: internal",
                    "---",
                    "",
                    "## Nuance / work-history",
                    f"- 2026-05-03: {self.FIRST_PROSE}",
                    "",
                    "## Pointers",
                    POINTER_LINE,
                    "",
                    "## Nuance / work-history",
                    f"- 2026-05-04: {self.SECOND_PROSE}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return path

    def test_a_bullet_matching_the_SECOND_section_is_APPENDED_not_swallowed(
        self, tmp_path: Path
    ):
        path = self._twin_heading_entry(tmp_path)
        before = path.read_bytes()

        status, line, _rev = api.append_bullet(
            path, text=self.SECOND_PROSE, actor="zach", session=SESSION_A,
            today="2026-05-05",
        )

        assert status == "appended", (
            "a bullet matching only the SECOND nuance section was answered "
            "`duplicate` — the writer refused to record an observation on the "
            "authority of a section it would never have inserted into"
        )
        assert path.read_bytes() != before
        after = path.read_text(encoding="utf-8")
        heading = resolver.NUANCE_HEADING
        # It landed under the FIRST heading — asserted by position in the raw
        # text, not by re-asking the function under test where that is.
        first_body = after.split(heading, 2)[1]
        assert first_body.splitlines()[1] == line, after

    def test_a_bullet_matching_the_FIRST_section_is_STILL_a_duplicate(
        self, tmp_path: Path
    ):
        """🔴 THE POSITIVE CONTROL, and the reason the fix is a NARROWING rather
        than a removal: within the section the writer actually inserts into,
        idempotency is unchanged and not one byte is written."""
        path = self._twin_heading_entry(tmp_path)
        before = path.read_bytes()

        status, _line, _rev = api.append_bullet(
            path, text=self.FIRST_PROSE, actor="zach", session=SESSION_A,
            today="2026-05-05",
        )

        assert status == "duplicate"
        assert path.read_bytes() == before

    def test_the_section_body_STOPS_at_the_next_heading(self, tmp_path: Path):
        """The narrowing is the section boundary itself, so it is asserted
        directly: `## Pointers` sits between the two nuance blocks and its
        content belongs to neither."""
        path = self._twin_heading_entry(tmp_path)
        lines = path.read_text(encoding="utf-8").splitlines()

        block = api.nuance_block(lines)

        assert block is not None
        index, body = block
        assert lines[index - 1] == resolver.NUANCE_HEADING
        assert self.FIRST_PROSE in body
        assert self.SECOND_PROSE not in body, body
        assert POINTER_LINE not in body, body


class TestTheRENAMEIsFSYNCedToo:
    """🔴 DURABILITY WAS ENTIRELY UNPINNED: a mutant deleting `os.fsync(fh)`
    survived all 479 tests, and there was no directory fsync at all.

    `fsync` on the FILE persists the bytes; the RENAME lives in the parent
    DIRECTORY and has its own writeback, so a node that lost power after
    `os.replace` returned could come back with the old name still pointing at the
    old inode — the append gone, the client told `200 appended`. Atomicity is
    what a concurrent READER sees; durability is what survives a crash, and
    `os.replace` gives only the first for free.
    """

    def _fsynced_kinds(self, monkeypatch, work) -> "list[bool]":
        """`[is_a_directory, …]` for every fd `os.fsync` was called on."""
        kinds: "list[bool]" = []
        real = os.fsync

        def spy(fd):
            kinds.append(stat.S_ISDIR(os.fstat(fd).st_mode))
            return real(fd)

        monkeypatch.setattr(os, "fsync", spy)
        try:
            work()
        finally:
            monkeypatch.undo()
        return kinds

    def test_an_append_fsyncs_BOTH_the_file_and_its_DIRECTORY(
        self, tmp_path: Path, monkeypatch
    ):
        """🔴 ONE ASSERTION PER FSYNC, so deleting EITHER one goes red — a single
        "fsync was called" check is green with the directory one removed, which
        is exactly the mutant that survived."""
        root = _build_store(tmp_path / "store", {ALLOW_SCOPE: KELP_NUANCE})
        path = entry_file(root, ALLOW_SCOPE)

        kinds = self._fsynced_kinds(
            monkeypatch,
            lambda: api.append_bullet(
                path, text=BULLET_D, actor="zach", session=SESSION_A,
                today="2026-05-06",
            ),
        )

        assert False in kinds, "the entry file itself was never fsynced"
        assert True in kinds, (
            "the parent DIRECTORY was never fsynced, so the rename that made the "
            "append visible is not durable across a crash"
        )

    def test_a_PUT_fsyncs_BOTH_as_well(self, tmp_path: Path, monkeypatch):
        """Both write primitives go through `_replace_bytes`, and the test says so
        rather than assuming it: a second copy of the write would be the
        predicate-at-two-sites shape this module keeps finding."""
        root = _build_store(tmp_path / "store", {ALLOW_SCOPE: KELP_NUANCE})
        path = entry_file(root, ALLOW_SCOPE)
        data = _entry(entry_ref(ALLOW_SCOPE), ALLOW_SCOPE, nuance=f"- 2026-05-06: {BULLET_E}").encode()
        revision = api.entry_revision(path.read_bytes())

        kinds = self._fsynced_kinds(
            monkeypatch,
            lambda: api.replace_entry(
                path, data=data, if_match=[revision], scope=ALLOW_SCOPE,
                filename=path.name,
            ),
        )

        assert False in kinds and True in kinds, kinds

    def test_an_UNFSYNCABLE_directory_does_not_fail_the_write(
        self, tmp_path: Path, monkeypatch
    ):
        """Best-effort is a decision, so it is pinned: a filesystem that refuses a
        directory fd must not turn a completed append into a 503. Trading a rare
        durability gap for a certain availability one is the wrong trade, and an
        unasserted `try/except` is the shape that silently becomes the right one
        for the wrong reason."""
        root = _build_store(tmp_path / "store", {ALLOW_SCOPE: KELP_NUANCE})
        path = entry_file(root, ALLOW_SCOPE)
        real_open = os.open

        def refuse_dirs(target, flags, *args, **kwargs):
            if Path(target).is_dir():
                raise OSError(13, "permission denied")
            return real_open(target, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", refuse_dirs)
        status, line, _rev = api.append_bullet(
            path, text=BULLET_F, actor="zach", session=SESSION_A, today="2026-05-06",
        )
        monkeypatch.undo()

        assert status == "appended"
        assert line in path.read_text(encoding="utf-8")


class TestIfMatchIsRead_AS_A_LIST:
    """🔴 RFC 9110 §13.1.1 SAYS `If-Match` IS A LIST, AND THIS READ IT AS ONE
    STRING. `If-Match: "stale", "<correct>"` — the header any conformant client
    builds from more than one candidate revision — compared the WHOLE value
    against a 16-character hash and answered **412 forever**. Uppercase hex did
    the same. Fail-closed, and still a precondition no conformant client could
    ever satisfy; a client that cannot succeed re-sends without one.
    """

    def _replacement(self) -> bytes:
        return _entry(
            entry_ref(ALLOW_SCOPE), ALLOW_SCOPE, nuance=f"- 2026-05-07: {BULLET_D}"
        ).encode()

    def _put(self, base: str, if_match: str):
        return fetch(
            entry_url(base, ALLOW_SCOPE), token=ZACH_TOKEN, method="PUT",
            data=self._replacement(), extra_headers={"If-Match": if_match},
        )

    def test_a_LIST_containing_the_CURRENT_revision_SUCCEEDS(self, scoped_store: Path):
        path = entry_file(scoped_store, ALLOW_SCOPE)
        current = api.entry_revision(path.read_bytes())
        stale = api.entry_revision(b"a revision this store never held")
        assert stale != current
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, body = self._put(base, f'"{stale}", "{current}"')
        assert code == 200, (code, body)
        assert headers["X-Store-Status"] == "replaced"
        assert path.read_bytes() == self._replacement()

    def test_a_LIST_of_ONLY_stale_revisions_is_still_412_and_UNCHANGED(
        self, scoped_store: Path
    ):
        """🔴 THE NEGATIVE HALF, AND IT IS THE ONE THAT MATTERS: widening the
        parser must not widen what SATISFIES the precondition. Two wrong tags are
        still wrong."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_bytes()
        one = api.entry_revision(b"neither of these")
        two = api.entry_revision(b"is the current revision")
        assert one != two
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, _b = self._put(base, f'"{one}", "{two}"')
        assert code == 412, code
        assert headers["X-Store-Status"] == "precondition-failed"
        assert path.read_bytes() == before

    def test_UPPERCASE_hex_names_the_SAME_revision(self, scoped_store: Path):
        """`hexdigest()` is lower-case; hex is not. A client that upper-cased its
        ETag got a 412 it could not diagnose."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        current = api.entry_revision(path.read_bytes())
        assert current != current.upper(), "the fixture revision has no hex letters"
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, _h, _b = self._put(base, f'"{current.upper()}"')
        assert code == 200, code
        assert path.read_bytes() == self._replacement()

    def test_a_LIST_containing_STAR_is_still_REFUSED(self, scoped_store: Path):
        """`*` turns the guard off while looking like it is on, and hiding it in
        a list must not smuggle it past the refusal."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_bytes()
        current = api.entry_revision(before)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, body = self._put(base, f'"{current}", *')
        assert code == 400, (code, body)
        assert headers["X-Store-Status"] == "bad-request"
        assert path.read_bytes() == before

    @pytest.mark.parametrize("shape", ["prefix", "superstring"])
    def test_the_precondition_is_EQUALITY_not_CONTAINMENT(
        self, scoped_store: Path, shape: str
    ):
        """🔴 THE MUTANT A 16-HEX FIXTURE STRUCTURALLY CANNOT SEE. Weakening
        `current not in tags` to a substring test (`tag in current or current in
        tag`) survives every other case in this file, because two DISTINCT full
        revisions are never substrings of one another — so nothing reached the
        weakened branch. A truncated ETag would then satisfy the precondition and
        a lost update would go straight through.

        Both directions, because the two containment mutants are different
        mutants: a PREFIX of the revision, and a string CONTAINING it.
        """
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_bytes()
        current = api.entry_revision(before)
        tag = current[:8] if shape == "prefix" else f"00{current}00"
        assert tag != current
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, _b = self._put(base, f'"{tag}"')
        assert code == 412, (shape, code)
        assert headers["X-Store-Status"] == "precondition-failed"
        assert path.read_bytes() == before

    def test_an_If_Match_naming_NO_entity_tag_is_refused(self, scoped_store: Path):
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_bytes()
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, body = self._put(base, " , ")
        assert code == 400, (code, body)
        assert headers["X-Store-Status"] == "bad-request"
        assert b"names no entity-tag" in body, body
        assert path.read_bytes() == before

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ('"abc"', ["abc"]),
            ('W/"abc"', ["abc"]),
            ('"abc", "def"', ["abc", "def"]),
            ('"ABC" ,  W/"DeF"', ["abc", "def"]),
            ("bare", ["bare"]),
            ("", []),
            (" , ", []),
            ("*", ["*"]),
        ],
    )
    def test_the_parser_itself(self, raw: str, expected: "list[str]"):
        assert api.parse_if_match(raw) == expected


class TestPUTDoesNotEnforceAttribution:
    """⚠ AN ACCEPTED LIMIT, PINNED SO IT CANNOT DRIFT INTO BEING ASSUMED. THIS IS
    NOT A SECURITY GUARANTEE AND NOTHING HERE SHOULD BE READ AS ONE.

    Criterion 4's "every appended bullet records actor and session" is a claim
    about `POST /bullets`, where the actor is a keyword no request body can
    populate. A PUT writes the caller's bytes VERBATIM — a forged
    `[cairn: <somebody else>/…]` trailer included — and this server does not
    check it.

    Enforcement was considered and DECLINED: PUT exists for the whole-file
    rewrites the store needs (editing `## Pointers`, turning `OPEN:` into
    `RESOLVED <sha>:`), and per-bullet enforcement would have to diff the old
    bullet set against the new one to tell a legitimate rewrite from a forgery,
    refusing real edits whenever that diff was wrong. The holder of a
    PUT-capable token is already trusted with the whole file's contents. What is
    NOT acceptable is claiming otherwise, which is why the claim is scoped to
    POST in the README, the module docstring and `render_bullet`.
    """

    FORGED_BULLET = (
        f"- 2026-05-08: OPEN: {BULLET_F} [cairn: {DANA.identity}/{SESSION_B}]"
    )

    def test_a_PUT_writes_a_FORGED_attribution_trailer_VERBATIM(
        self, scoped_store: Path
    ):
        path = entry_file(scoped_store, ALLOW_SCOPE)
        data = _entry(
            entry_ref(ALLOW_SCOPE), ALLOW_SCOPE, nuance=self.FORGED_BULLET
        ).encode()
        revision = api.entry_revision(path.read_bytes())
        assert ZACH.identity != DANA.identity, "the forgery must name another holder"

        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, _b = fetch(
                entry_url(base, ALLOW_SCOPE), token=ZACH_TOKEN, method="PUT",
                data=data, extra_headers={"If-Match": revision},
            )

        assert code == 200, code
        assert headers["X-Store-Status"] == "replaced"
        # 🔴 The bytes are on disk exactly as `zach` sent them, naming `dana`.
        assert path.read_bytes() == data
        assert self.FORGED_BULLET in path.read_text(encoding="utf-8")

    def test_the_SAME_forgery_through_POST_is_ATTRIBUTED_TO_THE_TOKEN(
        self, scoped_store: Path
    ):
        """🔴 THE BOUNDARY, IN ONE PAIR. The identical trailer sent through the
        POST route does NOT become an attribution: `render_bullet` appends the
        token's own, so the forged text is demoted to content and the bullet
        still records who wrote it. That contrast is the whole reason the claim
        can be scoped to POST rather than dropped."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        text = f"OPEN: {BULLET_F} [cairn: {DANA.identity}/{SESSION_B}]"

        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, _b = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=text, session=SESSION_A
            )

        assert code == 200 and headers["X-Store-Status"] == "appended"
        stored = path.read_text(encoding="utf-8")
        written = [ln for ln in stored.splitlines() if BULLET_F in ln]
        assert len(written) == 1, stored
        assert written[0].endswith(f"[cairn: {ZACH.identity}/{SESSION_A}]"), written


class TestTheFramingRefusalIsREACHED:
    """The `if not framed:` branch was dead code as far as the suite could see —
    a mutant making it inert survived 479 tests. It is EQUIVALENT today
    (`framed=False` implies `body=b""` by construction, and both write handlers
    then refuse an empty body downstream), and that is accepted. These guards
    stop the redundancy from being incidental: one pins the ANSWER a refused
    framing gets, the other pins the INVARIANT the equivalence rests on.
    """

    def test_a_CHUNKED_PUT_with_a_CORRECT_If_Match_is_400_and_UNCHANGED(
        self, scoped_store: Path
    ):
        """🔴 EVERY OTHER GUARD IS SATISFIED — a valid token, a resolvable target
        and the CURRENT revision — so the only thing that can refuse this request
        is the framing check. A test that sent a stale revision would be green
        with the branch deleted."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_bytes()
        revision = api.entry_revision(before)
        body = _entry(
            entry_ref(ALLOW_SCOPE), ALLOW_SCOPE, nuance=f"- 2026-05-09: {BULLET_E}"
        ).encode()
        with running(scoped_store, tokens=(ZACH,)) as (base, audit):
            host = base.split("//", 1)[1]
            payload = (
                f"PUT /api/v1/entry/{ALLOW_SCOPE}/{entry_ref(ALLOW_SCOPE)} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"Authorization: Bearer {ZACH_TOKEN}\r\n"
                f"CF-Connecting-IP: {CLIENT_IP}\r\n"
                f"If-Match: \"{revision}\"\r\n"
                f"Transfer-Encoding: chunked\r\n\r\n"
                f"{len(body):x}\r\n"
            ).encode() + body + b"\r\n0\r\n\r\n"
            data = _speak(host, payload)
            await_audit(audit, 1)
        assert b"HTTP/1.1 400" in data, data[:200]
        assert b"X-Store-Status: bad-request" in data, data[:400]
        assert b"unreadable request body" in data, data[:400]
        assert path.read_bytes() == before

    @pytest.mark.parametrize(
        "raw_headers",
        [
            b"Transfer-Encoding: chunked\r\n",
            b"Content-Length: 5\r\nContent-Length: 9\r\n",
            b"Content-Length: -5\r\n",
            b"Content-Length: not-a-number\r\n",
            b"Content-Length: 99999999\r\n",
        ],
    )
    def test_INVARIANT_a_refused_framing_ALWAYS_yields_an_EMPTY_body(
        self, raw_headers: bytes
    ):
        """🔴 THE INVARIANT THE EQUIVALENCE RESTS ON, ASSERTED DIRECTLY. If a
        refusal could ever return bytes, the `if not framed:` branch would stop
        being redundant and its absence would be a real defect — so the thing to
        pin is not the branch but the property that makes it safe."""
        message = http.client.parse_headers(io.BytesIO(raw_headers + b"\r\n"))

        class _Stub:
            close_connection = False
            headers = message
            rfile = io.BytesIO(b"hello there, this should never be read")

        framed, body = api.StoreRequestHandler._consume_body(_Stub(), keep=True)

        assert framed is False, raw_headers
        assert body == b"", (raw_headers, body)

    def test_POSITIVE_CONTROL_an_ACCEPTED_framing_DOES_return_the_body(self):
        """Without this, the invariant above is satisfied by a `_consume_body`
        that returns an empty body for everything."""
        message = http.client.parse_headers(io.BytesIO(b"Content-Length: 5\r\n\r\n"))

        class _Stub:
            close_connection = False
            headers = message
            rfile = io.BytesIO(b"hello there")

        framed, body = api.StoreRequestHandler._consume_body(_Stub(), keep=True)

        assert framed is True
        assert body == b"hello"


class TestTheHandlerArgumentCountComesFromTheTABLE:
    """🔴 `middle = parts[1 : len(parts) - tail_len]` SIZED A HANDLER CALL FROM
    THE REQUEST. `len(parts)` is attacker-controlled, and the expression was
    correct only because `_write_route`'s `len(parts) != arity` check rejected
    the mismatch three lines earlier. Relaxing that check proved the coupling:
    `PUT /api/v1/entry/<scope>/<ref>/bullets` (4 parts) matched the PUT row
    (arity 3) and passed FIVE arguments into a four-parameter method — unhandled
    `TypeError`, connection dropped, no response, no `X-Store-Status`, no audit
    line.

    The slice now comes from `WRITE_ROUTES`' own `arity`, so a wrong argument
    count is structurally impossible however the arity check behaves.
    """

    def test_a_PUT_at_the_BULLETS_path_is_a_405_and_writes_NOTHING(
        self, scoped_store: Path
    ):
        """🔴 ASSERTED ON THE STATUS CODE, NOT ON "something went wrong". The
        mutant that motivated this fix was previously "killed" only because
        `fetch` itself raised `RemoteDisconnected` — a transport error that would
        be equally red for any downstream crash and proves nothing about the
        arity guard. A real 405 read off the wire is the guard's own answer."""
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_bytes()
        revision = api.entry_revision(before)
        with running(scoped_store, tokens=(ZACH,)) as (base, audit):
            code, headers, body = fetch(
                bullets_url(base, ALLOW_SCOPE), token=ZACH_TOKEN, method="PUT",
                data=b"---\nservice: x\nscope: y\n---\n",
                extra_headers={"If-Match": revision},
            )
            lines = await_audit(audit, 1)
        assert code == 405, (code, body)
        assert headers["Allow"] == "GET, HEAD"
        assert body == b"read-only\n", body
        assert any("result=405" in ln and "status=method-not-allowed" in ln for ln in lines), lines
        assert path.read_bytes() == before

    def test_the_route_table_and_every_handler_signature_AGREE(self):
        """🔴 A LEDGER OVER THE SEAM, not over one side of it. The dispatcher
        passes `arity - tail_len - 1` path components plus `body` and `path`; a
        row whose arity disagrees with its handler's signature is a `TypeError`
        at request time on an internet-reachable route, and nothing else in this
        file would see it. Fails when a row is added, when a handler grows a
        parameter, and when either is removed."""
        import inspect

        for (verb, name), (handler_name, arity, tail) in api.WRITE_ROUTES.items():
            handler = getattr(api.StoreRequestHandler, handler_name)
            params = [
                p
                for p in inspect.signature(handler).parameters
                if p != "self"
            ]
            expected = (arity - len(tail) - 1) + 2  # path components + body + path
            assert len(params) == expected, (
                f"{verb} {name} -> {handler_name}: the table would pass "
                f"{expected} arguments to a {len(params)}-parameter handler"
            )
            assert params[-2:] == ["body", "path"], (verb, name, params)


class TestAnUnhandledHandlerErrorIsSTILLAudited:
    """🔴 A METERED REQUEST MUST NEVER VANISH FROM THE AUDIT TRAIL.

    Two defects in this round reached an unhandled-exception path (a
    `RecursionError` out of `json.loads`, a `TypeError` from the argument slice),
    and each dropped the connection with no response, no `X-Store-Status` and no
    audit line — the mirror image of the unmetered-405 channel `_write`'s
    docstring says it closed, and reachable by any token holder.

    🔴 THIS IS A BACKSTOP FOR THE UNKNOWN NEXT CASE, NOT A SUBSTITUTE FOR EITHER
    FIX. Both are fixed at their own site and have their own tests above.
    """

    def test_an_exception_in_a_handler_is_a_500_with_an_AUDIT_LINE(
        self, scoped_store: Path, monkeypatch
    ):
        def boom(*_a, **_k):
            raise ZeroDivisionError(
                f"/data/{DENY_SCOPE}/secret-path-{SESSION_B} blew up"
            )

        monkeypatch.setattr(api, "append_bullet", boom)
        before = tree_hash(scoped_store)
        with running(scoped_store, tokens=(ZACH,)) as (base, audit):
            code, headers, body = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_D, session=SESSION_A
            )
            lines = await_audit(audit, 1)
        monkeypatch.undo()

        assert code == 500, (code, body)
        assert headers["X-Store-Status"] == "internal-error"
        assert body == b"internal error\n", body
        assert any("result=500" in ln and "status=internal-error" in ln for ln in lines), lines
        assert tree_hash(scoped_store) == before

    def test_the_500_body_carries_NO_exception_detail(
        self, scoped_store: Path, monkeypatch
    ):
        """🔴 A BACKSTOP THAT ECHOED THE EXCEPTION WOULD OPEN A LEAK CHANNEL WHILE
        CLOSING AN AUDIT ONE. The raised message names a scope the caller may not
        see, a path and a session id; none of them may reach the wire.

        🔴 THE ARRIVAL ASSERTIONS COME FIRST, BECAUSE WITHOUT THEM THIS TEST WAS
        VACUOUS. "the body does not contain the secret" is satisfied by ANY
        answer the patched `append_bullet` never reached — a 400, a 403, a 404 —
        and a server-side refusal injected before the dispatch made it pass while
        the sibling above failed. A leak test must first prove it produced the
        very answer that could leak.
        """
        secret = f"/data/{DENY_SCOPE}/{THIRD_SCOPE}-{SESSION_B}"

        def boom(*_a, **_k):
            raise ZeroDivisionError(secret)

        monkeypatch.setattr(api, "append_bullet", boom)
        with running(scoped_store, tokens=(ZACH,)) as (base, audit):
            code, headers, body = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_E, session=SESSION_A
            )
            lines = await_audit(audit, 1)
        monkeypatch.undo()

        assert code == 500, (code, body)
        assert headers["X-Store-Status"] == "internal-error"
        assert body == b"internal error\n", body
        assert any(
            "result=500" in ln and "status=internal-error" in ln for ln in lines
        ), lines
        for leaked in (secret, DENY_SCOPE, THIRD_SCOPE, "ZeroDivisionError", "Traceback"):
            assert leaked.encode() not in body, (leaked, body)
        assert tuple(sorted(k.lower() for k in headers)) == tuple(
            sorted(
                ("cache-control", "connection", "content-length", "content-type",
                 "date", "server", "x-store-status")
            )
        ), sorted(headers)


class TestTheWriteRoutesDoNotCarryTheREADHeaders:
    """🔴 A README SENTENCE IS A CLAIM LIKE ANY OTHER, and this one was false from
    the moment the write path landed: "Every `/api/*` response carries
    `X-Store-Status`, `X-Store-Exit` …, `X-Store-Revision` … and
    `X-Store-Snapshot`". The write routes carry neither `X-Store-Revision` nor
    `X-Store-Snapshot`, and `X-Store-Exit` only on a 503.

    Pinned on BEHAVIOUR — the header set a write answer actually carries — and
    then on the corrected sentence, so the doc cannot drift back.
    """

    def test_a_successful_APPEND_carries_no_read_headers(self, scoped_store: Path):
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, _b = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_F, session=SESSION_A
            )
        assert code == 200
        lowered = {k.lower() for k in headers}
        assert "x-store-status" in lowered and "etag" in lowered
        assert "x-store-revision" not in lowered, sorted(headers)
        assert "x-store-snapshot" not in lowered, sorted(headers)
        assert "x-store-exit" not in lowered, sorted(headers)

    def test_a_successful_RECALL_DOES_carry_them(self, scoped_store: Path):
        """The positive control: the claim is true of the READ routes, which is
        why the fix is a qualification rather than a deletion."""
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, _b = fetch(
                f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=ZACH_TOKEN
            )
        assert code == 200
        lowered = {k.lower() for k in headers}
        for name in ("x-store-status", "x-store-exit", "x-store-revision",
                     "x-store-snapshot"):
            assert name in lowered, (name, sorted(headers))

    def test_the_README_no_longer_makes_the_UNQUALIFIED_claim(self):
        """🔴 PINNED ON THE WHOLE NORMALISED SENTENCE, not on a keyword. A guard
        that grepped for `X-Store-Revision` would be walked by any reword; a
        cosmetic edit failing this test is the price of a machine-readable claim.
        """
        text = " ".join((API_DIR / "README.md").read_text().split())
        assert (
            "Every `/api/*` response carries `X-Store-Status`, `X-Store-Exit` "
            "(the CLI's own exit code, from the CLI's own `_exit_for`), "
            "`X-Store-Revision` (the scope's git HEAD, or `unknown` — never a "
            "fabricated sha) and `X-Store-Snapshot`."
        ) not in text, "the README still claims all four headers on EVERY /api/* route"
        assert (
            "⚠ **The WRITE routes carry NEITHER `X-Store-Revision` NOR "
            "`X-Store-Snapshot`, and `X-Store-Exit` only on a `503`.**"
        ) in text

    def test_the_README_scopes_the_attribution_claim_to_POST(self):
        """The other false-by-scope claim from this round. Same whole-sentence
        rule, for the same reason."""
        text = " ".join((API_DIR / "README.md").read_text().split())
        assert (
            "🔴 **The ACTOR is derived from the token, never from the body.**"
        ) not in text, "the README still makes the attribution claim server-wide"
        assert (
            "🔴 **On this POST route the ACTOR is derived from the token, never "
            "from the body.**"
        ) in text
        assert (
            "⚠ **PUT DOES NOT ENFORCE ATTRIBUTION, AND THE POST GUARANTEE ABOVE "
            "DOES NOT EXTEND TO IT.**"
        ) in text


# =============================================================================
# The backstop must not become the desync it was added on top of.
# =============================================================================

# Distinct from every other bullet in this file, so "the write landed" cannot be
# satisfied by a line some earlier test wrote.
BULLET_BACKSTOP = "the anchor windlass trips its breaker on a cold morning"
BULLET_AFTER_PUT = "the chart plotter loses its fix under the bridge"


# 🔴 HOW LONG THE RAW READER LINGERS *AFTER* THE RESPONSES IT WAS TOLD TO EXPECT
# HAVE ARRIVED, watching for an extra one. This is a DRAIN bound in the sense
# `test_the_drain_bounds_were_NOT_swept_into_the_hang_bound` means it: it is paid
# in full by every PASSING run, so it stays small and must never be swept onto
# `HANG_TIMEOUT`. It is NOT the bound that decides whether the answer arrived —
# that is the completion wait below, and conflating the two is the defect this
# constant was extracted to make impossible to re-introduce.
RAW_SETTLE_S = 3.0

# Poll granularity for the completion wait. Deliberately small: every `settimeout`
# in this file stays a drain-sized number, and the OVERALL deadline is enforced by
# the loop re-checking the clock instead of by one long `settimeout`. Written this
# way on purpose — a single `sock.settimeout(HANG_TIMEOUT)` would be the exact
# sweep the drain guard forbids, and would also make a passing run pay 60 s.
RAW_POLL_S = 0.5


def _complete_responses(raw: bytes) -> int:
    """How many FULLY-FRAMED responses `raw` holds.

    🔴 THE POINT IS TO TELL "NOT YET" FROM "NEVER". A byte count cannot: a
    response that is half-written and a response that will never come look
    identical from the reader's side, and the whole flake below is what happens
    when a reader treats the first as the second.

    Framing is exact rather than heuristic because `server.py:_respond` is the
    ONLY thing that writes a response and it ALWAYS sends `Content-Length`
    (`/healthz` and the uniform 401 included — `send_error` is overridden to go
    through `_respond` too). A response whose headers are incomplete, or whose
    body has not all arrived, counts as NOT complete; so does one with no
    `Content-Length`, since an unframeable response cannot be known to have
    ended. ⚠ Assumes no HEAD on a raw socket, which no caller in this file does:
    a HEAD reply advertises a length it never sends, and would read as forever
    incomplete.
    """
    n, pos = 0, 0
    while True:
        head_end = raw.find(b"\r\n\r\n", pos)
        if head_end < 0:
            return n
        head = raw[pos:head_end]
        match = re.search(rb"\r\ncontent-length:[ \t]*(\d+)", head, re.I)
        if match is None:
            return n
        end = head_end + 4 + int(match.group(1))
        if len(raw) < end:
            return n
        n += 1
        pos = end


def _raw_exchange(
    host: str, payload: bytes, *, expect: int = 1, settle: float = RAW_SETTLE_S
):
    """Send `payload` on ONE socket; return `(every byte that came back, saw_eof)`.

    🔴 THE RAW SOCKET IS THE ONLY INSTRUMENT THAT CAN SEE THIS DEFECT. `urllib`
    and `http.client` parse ONE response and leave whatever follows sitting in
    the buffer, so a SECOND complete response on the same connection — the thing
    these tests are about — is structurally invisible to them: the client returns
    a perfectly good `200` and the trailing `500` goes to whoever gets the
    pooled connection next. `saw_eof` separates "the server closed" from "the
    reader gave up waiting", which is the other half a parsed client hides.

    Deliberately NOT `TestNoRequestSmuggling._raw`: that one returns only the
    split pieces, and the assertion below needs the UNSPLIT bytes to say
    "nothing trailed the first response". (That helper now delegates HERE for
    its reading, so there is one reader rather than two that drift.)

    🔴 TWO BOUNDS, AND COLLAPSING THEM INTO ONE IS THE FLAKE (devrc#1165).
    This helper used to `settimeout(3.0)` and read until that expired, treating
    the timeout as "the server has finished talking". Under the disk contention
    PR #1181 measured, a response that takes longer than 3 s to start is
    ordinary — so the reader returned an EMPTY buffer, no exception was raised
    anywhere, and the failure surfaced further down as `len(answers) == 0`
    against a message about a SECOND response. Four CI runs were read as a
    response-desync defect when the server had simply not answered yet.

      * PHASE 1 — WAIT FOR AN ANSWER. Read until `expect` responses are fully
        framed, or EOF, bounded by `HANG_TIMEOUT`. Costs nothing on a healthy
        run (it stops the instant the last byte of the response lands) and
        raises a message that says HANG when it does give up.
      * PHASE 2 — THEN LINGER `settle`, which is what actually detects the extra
        response these tests exist to catch. Unchanged in value and meaning.

    So a slow server is now slow rather than silent, and only a server that
    never answers fails — with a sentence naming that.
    """
    import socket

    name, port = host.split(":")
    buf = b""
    saw_eof = False
    with socket.create_connection((name, int(port)), timeout=10) as sock:
        # 🔴 SEND UNDER THE CONNECT TIMEOUT, not under the poll slice. Setting
        # the poll bound first would put a 0.5 s deadline on `sendall`, which is
        # a send bound nobody asked for and a new flake on a slow write.
        sock.sendall(payload)
        sock.settimeout(RAW_POLL_S)

        deadline = time.monotonic() + HANG_TIMEOUT
        while not saw_eof and _complete_responses(buf) < expect:
            if time.monotonic() >= deadline:
                raise AssertionError(
                    f"NO complete response within {HANG_TIMEOUT:g}s: expected "
                    f"{expect}, framed {_complete_responses(buf)} from "
                    f"{len(buf)} bytes: {buf!r}. THE SERVER DID NOT ANSWER IN "
                    "TIME — this is a hang or an I/O stall on the server side, "
                    "NOT a wrong number of responses. Do not read it as a "
                    "desync, and do not raise this bound: see HANG_TIMEOUT."
                )
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                continue  # poll slice expired; the deadline above is the bound
            except OSError:
                break
            if not chunk:
                saw_eof = True
                break
            buf += chunk

        # 🔴 The extra-response watch. It runs even once `expect` are in hand —
        # that is the entire point, and shortening it blunts every desync
        # assertion below without failing any of them.
        linger_until = time.monotonic() + settle
        while not saw_eof and time.monotonic() < linger_until:
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                continue
            except OSError:
                break
            if not chunk:
                saw_eof = True
                break
            buf += chunk
    return buf, saw_eof


def _responses(raw: bytes) -> "list[bytes]":
    """The status lines + everything after them, one per response on the wire."""
    return raw.split(b"HTTP/1.1 ")[1:]


def _one_response(raw: bytes, what: str, *, saw_eof: "bool | None" = None) -> "list[bytes]":
    """Assert `raw` holds EXACTLY ONE response, saying WHICH WAY it was wrong.

    🔴 THE MESSAGE MUST DISCRIMINATE 0 FROM 2, BECAUSE THOSE ARE OPPOSITE BUGS
    WITH OPPOSITE FIXES (devrc#1165). Every site here used to be spelled

        assert len(answers) == 1, "a SECOND complete response followed ..."

    which is a sentence about `> 1` attached to a predicate that also fails at
    `0`. In CI it failed at `0` — `raw` was `b''` — and reported a response
    desync that had not happened. Four runs, three sessions and two wrong
    diagnoses came out of that one mis-description, so the count is now reported
    by the branch that actually fired rather than by whichever case the author
    had in mind.

    `what` names the request under test, so the sentence identifies the site
    without the reader having to resolve a line number that shifts whenever a
    comment above it is edited.
    """
    answers = _responses(raw)
    if len(answers) == 1:
        return answers
    eof = "" if saw_eof is None else f" (saw_eof={saw_eof})"
    if not answers:
        raise AssertionError(
            f"NO complete response came back for {what}{eof}: {raw!r}. The read "
            "was EMPTY or PARTIAL — either the request vanished without an "
            "answer, or the server had not answered yet and the reader stopped "
            "waiting. THIS IS NOT A SECOND RESPONSE and is not a desync; a "
            "trailing-response bug reports as 2 below, never as 0."
        )
    raise AssertionError(
        f"{len(answers)} responses came back for {what}{eof} — a SECOND "
        f"complete response followed the first on ONE connection, which a "
        f"pooling proxy hands to the next client: {raw!r}"
    )


def _request(host: str, method: str, target: str, body: bytes | None = None) -> bytes:
    head = (
        f"{method} {target} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"Authorization: Bearer {ZACH_TOKEN}\r\n"
        f"CF-Connecting-IP: {CLIENT_IP}\r\n"
    )
    if body is None:
        return (head + "\r\n").encode()
    return (head + f"Content-Length: {len(body)}\r\n\r\n").encode() + body


class TestTheRawReaderWaitsForTheAnswerItWasPromised:
    """🔴 THE REGRESSION GUARD FOR devrc#1165 — RED AT THE PARENT COMMIT.

    Unlike almost everything else in this file, these are NOT invariant guards.
    There was a real defect: `_raw_exchange` read with a single 3 s
    `settimeout` and treated its expiry as "the server has finished talking",
    so a server slower than 3 s returned an EMPTY buffer with no exception
    anywhere. `tekton/devrc-pytests` failed four times on it, on three
    different test names, and every report named a SECOND response that did not
    exist.

    Each test below fails at the parent commit, and the matrix is in the PR
    body. They use a stub TCP server rather than the real one: the property is
    "the reader waits for a slow answer", which does not depend on what is
    slow, and pinning it to `server.py`'s fsync would re-pin it to the one
    cause that happened to be observed.
    """

    @staticmethod
    @contextmanager
    def _slow_server(delay: float, reply: bytes, *, repeat: int = 1):
        """A socket that accepts, waits `delay`, then writes `reply` `repeat` times."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)

        def serve():
            try:
                conn, _addr = srv.accept()
            except OSError:
                return
            with conn:
                conn.recv(65536)
                time.sleep(delay)
                try:
                    conn.sendall(reply * repeat)
                except OSError:
                    # The client gave up and closed — exactly the BrokenPipeError
                    # the CI failures printed alongside the empty read.
                    return
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield f"127.0.0.1:{srv.getsockname()[1]}"
        finally:
            srv.close()
            thread.join(timeout=10)

    # A complete, correctly framed response — the thing the reader must wait for.
    _REPLY = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nCache-Control: no-store\r\n\r\nok"

    def test_a_response_SLOWER_than_the_settle_bound_is_still_READ(self):
        """🔴 THE DEFECT ITSELF. `RAW_SETTLE_S` is 3.0; this server takes longer,
        which under CI disk contention is ordinary rather than exotic. Before the
        fix this returned `b''` and the caller reported a phantom second response.
        """
        slow = RAW_SETTLE_S + 1.5
        assert slow > RAW_SETTLE_S, "fixture drift: the delay must exceed the drain"
        with self._slow_server(slow, self._REPLY) as host:
            raw, _eof = _raw_exchange(host, b"GET / HTTP/1.1\r\n\r\n", settle=0.2)
        assert _complete_responses(raw) == 1, (
            f"the reader gave up before a {slow:g}s answer arrived: {raw!r}"
        )
        assert _one_response(raw, "a deliberately slow stub server")[0].startswith(b"200 ")

    def test_the_reader_still_sees_a_SECOND_response_after_waiting(self):
        """🔴 THE OTHER DIRECTION, and the reason the fix is not just a longer
        timeout. Waiting for `expect` responses must not stop the reader
        noticing an EXTRA one — that detection is the entire point of these
        tests, and a fix that traded it away would pass every assertion above
        while silently disarming the class.
        """
        with self._slow_server(RAW_SETTLE_S + 1.5, self._REPLY, repeat=2) as host:
            raw, _eof = _raw_exchange(host, b"GET / HTTP/1.1\r\n\r\n")
        assert _complete_responses(raw) == 2, (
            f"the trailing response was not observed after the wait: {raw!r}"
        )

    def test_a_server_that_NEVER_answers_says_HANG_not_response_count(self):
        """🔴 The bound still exists; only its MESSAGE changed. A reader that
        waited forever would swap a wrong diagnosis for a hung gate.
        """
        # 🔴 HOLDS THE CONNECTION OPEN AND SENDS NOTHING. An earlier draft passed
        # `delay=0` with an empty reply, which CLOSED the socket immediately —
        # the reader saw EOF, returned cleanly and the test failed DID-NOT-RAISE.
        # A stub that hangs up is not a stub that hangs, and only the second one
        # reaches the deadline arm.
        with self._slow_server(2.0, b"") as host:
            with pytest.raises(AssertionError) as excinfo:
                _raw_exchange_with_bound(host, bound=0.5)
        message = str(excinfo.value)
        assert "NO complete response within" in message, message
        assert "DID NOT ANSWER IN TIME" in message, message
        # 🔴 The mis-description that started all this must not come back by
        # another route: a hang is never reported as a response-count defect.
        assert "SECOND complete response" not in message, message

    def test_an_EMPTY_read_is_reported_as_EMPTY_and_never_as_a_SECOND_response(self):
        """🔴 THE ASSERTION-SHAPE FIX, pinned as a normalised STRING rather than
        by keyword. The old sentence was reachable at `len == 0`, and a guard
        that only greps for a word is walkable by rewording — so this asserts
        what the reader is actually told.
        """
        with pytest.raises(AssertionError) as empty:
            _one_response(b"", "the CI case", saw_eof=False)
        said = str(empty.value)
        assert "NO complete response came back for the CI case" in said, said
        assert "THIS IS NOT A SECOND RESPONSE" in said, said
        assert "saw_eof=False" in said, said

        # And the >=2 case still says the thing it was always meant to say.
        two = self._REPLY * 2
        with pytest.raises(AssertionError) as extra:
            _one_response(two, "the desync case")
        also = str(extra.value)
        assert "2 responses came back for the desync case" in also, also
        assert "SECOND complete response followed the first" in also, also

    def test_the_FRAMING_parser_tells_partial_from_complete(self):
        """🔴 The positive/negative control for `_complete_responses`. Without
        it, "the reader waited for a complete response" is a claim about a
        function nobody has watched distinguish anything.
        """
        assert _complete_responses(b"") == 0
        assert _complete_responses(self._REPLY) == 1
        assert _complete_responses(self._REPLY * 3) == 3
        # Headers complete, body one byte short — the case a byte-count check
        # cannot see and the whole reason framing is parsed rather than sniffed.
        assert _complete_responses(self._REPLY[:-1]) == 0
        # Headers themselves truncated.
        assert _complete_responses(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n") == 0
        # A first complete response followed by a partial second counts as ONE,
        # so a desync assertion cannot be tripped by a half-arrived trailer.
        assert _complete_responses(self._REPLY + self._REPLY[:-1]) == 1


def _raw_exchange_with_bound(host: str, *, bound: float):
    """`_raw_exchange` with its completion deadline shortened, for the hang test.

    🔴 A SEPARATE ENTRY POINT rather than a parameter on `_raw_exchange` itself:
    a `deadline=` argument would be one refactor away from a call site pinning
    its own bound, which is exactly the drift
    `test_no_hang_detector_is_still_bound_by_a_LITERAL` exists to stop. Nothing
    in the suite proper reaches this; it exists so the hang MESSAGE can be
    asserted without spending `HANG_TIMEOUT` seconds to see it.
    """
    global HANG_TIMEOUT
    previous = HANG_TIMEOUT
    HANG_TIMEOUT = bound
    try:
        return _raw_exchange(host, b"GET / HTTP/1.1\r\n\r\n", settle=0.2)
    finally:
        HANG_TIMEOUT = previous


class TestTheBackstopNeverSendsASecondResponse:
    """🔴 THE GUARD ADDED TO CLOSE THE AUDIT HOLE REOPENED THE DESYNC HOLE.

    `_append_bullet` used to call `_respond(200, …)` and THEN `_audit(…)`, and
    both sat inside the dispatch backstop's `try`. Anything raising after that
    `_respond` — a broken audit sink, a full disk on stderr, or simply the next
    statement somebody adds below it — made the backstop call `_respond(500, …)`
    on a connection that had already sent a complete `200`. `_respond` has no
    already-sent guard and only a non-200 sets `close_connection`, so the `200`
    had already advertised the socket as reusable and a pooling proxy hands the
    trailing `500` to the NEXT client on it. That is the response-desync class
    `_drain_body`'s own docstring exists to prevent.

    ⚠ HONEST REACHABILITY, AND IT IS NOW THINNER THAN IT WAS — WHICH IS A REASON
    TO KEEP THESE TESTS, NOT TO DELETE THEM. The already-responded arm is
    induced here by wrapping the handler. It used to have a production route
    (`_audit` ran after `_respond`, so a raising sink reached it); with the audit
    emitted FIRST there is no statement after a completed `_respond` at any call
    site, so nothing in production reaches this arm today. That is precisely the
    state the fix is a flag inside `_respond` rather than a rule about where code
    may go: "no statement will ever be added after a `_respond`" is a promise,
    and the next person to add one gets the guard instead of the desync.
    """

    def test_an_exception_AFTER_the_response_sends_NO_second_response(
        self, scoped_store: Path, monkeypatch
    ):
        real = api.StoreRequestHandler._append_bullet

        def then_raise(self, *args, **kwargs):
            real(self, *args, **kwargs)
            raise ZeroDivisionError(f"/data/{DENY_SCOPE}/{SESSION_B} after the 200")

        monkeypatch.setattr(api.StoreRequestHandler, "_append_bullet", then_raise)
        path = entry_file(scoped_store, ALLOW_SCOPE)
        body = json.dumps({"text": BULLET_BACKSTOP, "session": SESSION_A}).encode()
        with running(scoped_store, tokens=(ZACH,)) as (base, audit):
            host = base.split("//", 1)[1]
            raw, saw_eof = _raw_exchange(
                host,
                _request(
                    host, "POST",
                    f"/api/v1/entry/{ALLOW_SCOPE}/{entry_ref(ALLOW_SCOPE)}/bullets",
                    body,
                ),
            )
            lines = await_audit(audit, 2)
        monkeypatch.undo()

        # 🔴 THE WRITE LANDED. Without this the test is the vacuous shape this
        # round has produced three times: a file nothing wrote to trivially has
        # no second response either. The 200 is a real 200 about a real append.
        assert BULLET_BACKSTOP in nuance_of(path), path.read_text()
        answers = _one_response(
            raw, "a POST whose handler raised AFTER its 200", saw_eof=saw_eof
        )
        assert answers[0].startswith(b"200 "), raw
        assert b"internal error" not in raw, raw
        assert b"X-Store-Status: internal-error" not in raw, raw
        # The connection must not be reused after a request that ended in an
        # unknown state, so the server closes it rather than leaving it pooled.
        assert saw_eof, "the server left the desynchronised connection open"
        # And the request still did not vanish from the audit trail: the 200 for
        # the append that landed, and the 500 the backstop could not send.
        assert any("result=200" in ln and "status=appended" in ln for ln in lines), lines
        assert any(
            "result=500" in ln and "status=internal-error-after-response" in ln
            for ln in lines
        ), lines

    def test_POSITIVE_CONTROL_the_raw_reader_CAN_see_a_SECOND_response(
        self, scoped_store: Path
    ):
        """🔴 Otherwise "exactly one response" is a fact about the reader, not
        about the server. Two genuinely pipelined requests must come back as
        two, read by the SAME helper and split by the SAME function."""
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            host = base.split("//", 1)[1]
            one = f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n\r\n".encode()
            raw, _eof = _raw_exchange(host, one + one, expect=2)
        # 🔴 Spelled as an exact count, NOT `>= 2`: this control is the reason
        # "exactly one" above is a claim about the SERVER rather than about the
        # reader, and a floor would keep saying so if the reader started
        # duplicating. It is also the site that would go red first if the
        # completion wait ever stopped waiting — `expect=2` makes the reader
        # hold out for BOTH pipelined answers instead of whatever the drain
        # happened to catch.
        assert len(_responses(raw)) == 2, (
            f"the reader cannot see two responses on one connection: {raw!r}. "
            "Every 'exactly one response' assertion in this class is worthless "
            "until this passes — a reader that can only ever see one satisfies "
            "them all."
        )

    def test_an_exception_BEFORE_the_response_still_yields_ONE_500(
        self, scoped_store: Path, monkeypatch
    ):
        """The other arm, on the wire rather than through a parsed client: when
        nothing has been sent yet the backstop still answers, exactly once."""

        def boom(*_a, **_k):
            raise ZeroDivisionError(f"/data/{DENY_SCOPE}/{SESSION_B} before the 200")

        monkeypatch.setattr(api.StoreRequestHandler, "_append_bullet", boom)
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_bytes()
        body = json.dumps({"text": BULLET_BACKSTOP, "session": SESSION_B}).encode()
        with running(scoped_store, tokens=(ZACH,)) as (base, audit):
            host = base.split("//", 1)[1]
            raw, _eof = _raw_exchange(
                host,
                _request(
                    host, "POST",
                    f"/api/v1/entry/{ALLOW_SCOPE}/{entry_ref(ALLOW_SCOPE)}/bullets",
                    body,
                ),
            )
            lines = await_audit(audit, 1)
        monkeypatch.undo()

        answers = _one_response(raw, "a POST whose handler raised BEFORE any response")
        assert answers[0].startswith(b"500 "), raw
        assert b"internal error\n" in raw, raw
        assert any("result=500" in ln and "status=internal-error" in ln for ln in lines), lines
        assert path.read_bytes() == before

    def test_a_PUT_that_raises_AFTER_its_response_is_the_SAME_one_answer(
        self, scoped_store: Path, monkeypatch
    ):
        """🔴 THE RULE LIVES IN `_respond`, NOT IN ONE HANDLER — so the second
        write route inherits it without being told. If the flag had been set at
        the `_append_bullet` call site instead, this would still send two."""
        real = api.StoreRequestHandler._replace_entry

        def then_raise(self, *args, **kwargs):
            real(self, *args, **kwargs)
            raise ZeroDivisionError("after the PUT response")

        monkeypatch.setattr(api.StoreRequestHandler, "_replace_entry", then_raise)
        path = entry_file(scoped_store, ALLOW_SCOPE)
        revision = api.entry_revision(path.read_bytes())
        replacement = _entry(
            entry_ref(ALLOW_SCOPE), ALLOW_SCOPE,
            nuance=f"- 2026-04-12: {BULLET_AFTER_PUT}",
        ).encode()
        with running(scoped_store, tokens=(ZACH,)) as (base, audit):
            host = base.split("//", 1)[1]
            head = (
                f"PUT /api/v1/entry/{ALLOW_SCOPE}/{entry_ref(ALLOW_SCOPE)} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"Authorization: Bearer {ZACH_TOKEN}\r\n"
                f"CF-Connecting-IP: {CLIENT_IP}\r\n"
                f"If-Match: {revision}\r\n"
                f"Content-Length: {len(replacement)}\r\n\r\n"
            ).encode()
            raw, _eof = _raw_exchange(host, head + replacement)
            lines = await_audit(audit, 2)
        monkeypatch.undo()

        # The replace LANDED — the assertion that stops this being vacuous.
        assert path.read_bytes() == replacement
        answers = _one_response(raw, "a PUT whose handler raised AFTER its 200")
        assert answers[0].startswith(b"200 "), raw
        assert any(
            "result=500" in ln and "status=internal-error-after-response" in ln
            for ln in lines
        ), lines


class TestTheREADDispatchIsBackstoppedToo:
    """🔴 THE WRITE DISPATCH GOT A BACKSTOP; THE READ DISPATCH DID NOT, AND READS
    ARE THE BUSIER PATH.

    Measured before the fix: an exception inside `_recall` produced a
    `RemoteDisconnected` at the client, NO response on the wire and ZERO audit
    lines — on a request that had already been metered, authenticated and
    authorised. The write backstop's own justification ("a metered request must
    never vanish from the audit trail") says nothing about the verb, so neither
    does the guard.
    """

    def test_an_exception_in_a_READ_handler_is_a_500_with_an_AUDIT_LINE(
        self, scoped_store: Path, monkeypatch
    ):
        secret = f"/data/{DENY_SCOPE}/{THIRD_SCOPE}-{SESSION_B}"

        def boom(*_a, **_k):
            raise ZeroDivisionError(secret)

        monkeypatch.setattr(api.StoreRequestHandler, "_recall", boom)
        with running(scoped_store, tokens=(ZACH,)) as (base, audit):
            host = base.split("//", 1)[1]
            raw, _eof = _raw_exchange(
                host, _request(host, "GET", f"/api/v1/recall/{ALLOW_SCOPE}")
            )
            answers = _one_response(raw, "a GET whose read handler raised")
            assert answers[0].startswith(b"500 "), raw
            lines = await_audit(audit, 1)
        monkeypatch.undo()

        assert b"internal error\n" in raw, raw
        assert b"X-Store-Status: internal-error" in raw, raw
        assert any("result=500" in ln and "status=internal-error" in ln for ln in lines), lines
        # The same leak rule the write backstop carries: an exception string
        # names paths, scopes and sessions the caller may not see.
        for leaked in (secret, DENY_SCOPE, THIRD_SCOPE, "ZeroDivisionError", "Traceback"):
            assert leaked.encode() not in raw, (leaked, raw)

    def test_an_exception_in_the_SNAPSHOT_handler_is_answered_too(
        self, scoped_store: Path, monkeypatch
    ):
        """A second read route, so the guard is pinned on the DISPATCH rather
        than on one handler's name."""

        def boom(*_a, **_k):
            raise ZeroDivisionError("snapshot blew up")

        monkeypatch.setattr(api.StoreRequestHandler, "_snapshot", boom)
        with running(scoped_store, tokens=(ZACH,)) as (base, audit):
            host = base.split("//", 1)[1]
            raw, _eof = _raw_exchange(host, _request(host, "GET", "/api/v1/snapshot"))
            answers = _one_response(raw, "a GET whose snapshot handler raised")
            assert answers[0].startswith(b"500 "), raw
            lines = await_audit(audit, 1)
        monkeypatch.undo()
        assert any("result=500" in ln and "status=internal-error" in ln for ln in lines), lines

    def test_an_exception_AFTER_a_READ_response_sends_NO_second_one(
        self, scoped_store: Path, monkeypatch
    ):
        """The already-sent rule is the SHARED one — the read backstop gets it
        from `_backstop`, not from a second copy that could drift."""
        real = api.StoreRequestHandler._recall

        def then_raise(self, *args, **kwargs):
            real(self, *args, **kwargs)
            raise ZeroDivisionError("after the recall response")

        monkeypatch.setattr(api.StoreRequestHandler, "_recall", then_raise)
        with running(scoped_store, tokens=(ZACH,)) as (base, audit):
            host = base.split("//", 1)[1]
            raw, saw_eof = _raw_exchange(
                host, _request(host, "GET", f"/api/v1/recall/{ALLOW_SCOPE}")
            )
            lines = await_audit(audit, 2)
        monkeypatch.undo()

        answers = _one_response(
            raw, "a GET whose recall handler raised AFTER its 200", saw_eof=saw_eof
        )
        assert answers[0].startswith(b"200 "), raw
        # The recall really was SERVED — the anti-vacuity half. An answer that
        # never rendered the digest would satisfy "exactly one response" too.
        assert KELP_NUANCE.encode() in raw, raw
        assert b"internal error" not in raw, raw
        assert saw_eof, "the desynchronised connection was left open"
        assert any(
            "result=500" in ln and "status=internal-error-after-response" in ln
            for ln in lines
        ), lines


BULLET_SINKLESS = "the gangway sensor sticks when the pontoon ices over"


class _RaisingTraceback:
    """A stand-in for the `traceback` MODULE whose `print_exc` is what is broken.

    🔴 SUBSTITUTED FOR `api.traceback`, NOT FOR `traceback.print_exc` ITSELF.
    Patching the real module's attribute would break every other importer in the
    interpreter — pytest's own reporting included — so the swap is scoped to the
    one module whose behaviour is under test.

    ⚠ HONEST REACHABILITY, stated because it is the weakest part of these two
    tests: a broken stderr is induced AT `print_exc`, not by closing fd 2 or
    filling a disk. For the question these tests ask — does a raising log write
    decide whether the request gets answered — the two are the same
    control-flow event, and this one is deterministic. `EPIPE` rather than a
    bare `Exception` because a closed stderr pipe is what actually happens to a
    pod whose log collector went away, and a guard tuned to a textbook fixture
    is a guard that has not met production.
    """

    def __init__(self) -> None:
        self.calls = 0

    def print_exc(self, *_args, **_kwargs):
        self.calls += 1
        raise OSError(errno.EPIPE, "stderr is a closed pipe")


class TestTheBackstopSurvivesITSOWNLogSink:
    """🔴 `_backstop`'s FIRST STATEMENT WAS AN UNGUARDED `traceback.print_exc`,
    AND THE `except` AROUND ITS `_audit` CALL PRINTED AGAIN, ALSO UNGUARDED.

    So the one function whose entire job is "a metered request must never vanish"
    vanished the request itself whenever the LOG SINK was the broken thing —
    exactly the case its own docstring reasons about. MEASURED before the fix,
    handler exception plus a raising `print_exc`: `RemoteDisconnected` at the
    client, ZERO audit lines.

    NOT a regression: the pre-`ea3d0a16` backstop had the same bare print. What
    changed is that the docstring grew a paragraph about the broken-sink case and
    guarded only half of it — the "reads as coverage while providing none" shape,
    which is worse than no claim because it stops the next reader looking.
    """

    def test_a_handler_exception_with_a_RAISING_print_exc_STILL_answers_and_audits(
        self, scoped_store: Path, monkeypatch
    ):
        """The FIRST print. Nothing has been sent yet, so the backstop owes a
        500 and an audit line — and a raising stderr must not cost either."""
        broken = _RaisingTraceback()

        def boom(*_a, **_k):
            raise ZeroDivisionError(f"/data/{DENY_SCOPE}/{SESSION_B} before the 200")

        monkeypatch.setattr(api.StoreRequestHandler, "_append_bullet", boom)
        monkeypatch.setattr(api, "traceback", broken)
        path = entry_file(scoped_store, ALLOW_SCOPE)
        before = path.read_bytes()
        body = json.dumps({"text": BULLET_SINKLESS, "session": SESSION_B}).encode()
        with running(scoped_store, tokens=(ZACH,)) as (base, audit):
            host = base.split("//", 1)[1]
            raw, _eof = _raw_exchange(
                host,
                _request(
                    host, "POST",
                    f"/api/v1/entry/{ALLOW_SCOPE}/{entry_ref(ALLOW_SCOPE)}/bullets",
                    body,
                ),
            )
            answers = _one_response(
                raw,
                "a POST whose backstop had to log through a RAISING print_exc "
                "(the request must not vanish with the log write)",
            )
            assert answers[0].startswith(b"500 "), raw
            lines = await_audit(audit, 1)
        monkeypatch.undo()

        # The fixture's own positive control: a `print_exc` that was never
        # called cannot have been the thing under test.
        assert broken.calls >= 1, "the broken sink was never reached"
        assert b"internal error\n" in raw, raw
        assert any(
            "result=500" in ln and "status=internal-error" in ln for ln in lines
        ), lines
        assert path.read_bytes() == before
        for leaked in (DENY_SCOPE, "ZeroDivisionError", "closed pipe", "Traceback"):
            assert leaked.encode() not in raw, (leaked, raw)

    def test_a_RAISING_AUDIT_SINK_AND_a_RAISING_print_exc_still_end_the_request(
        self, scoped_store: Path, monkeypatch, capfd
    ):
        """The SECOND print, on the route the docstring calls the only
        production-reachable one: the AUDIT SINK is what raised. The backstop
        owes an answer and a log entry — and when the log is what is broken it
        must end the request cleanly anyway.

        🔴 THE OBSERVABLE IS STDERR, NOT THE WIRE, and that is not a weaker
        claim — it is the claim. An answer goes out either way, so with the
        second print unguarded the exception escapes `do_POST`, past
        `handle_one_request`, into `socketserver.BaseServer.handle_error`, whose
        banner replaces the operator's traceback. That banner appearing is the
        failure.

        🔴 THE ANSWER MOVED FROM `200` TO `500`, AND THAT IS THE PRICE OF
        AUDIT-BEFORE-RESPOND, WRITTEN DOWN RATHER THAN DISCOVERED LATER. This
        test asserted `200` while `_audit` ran AFTER `_respond`: the append
        answered, the sink then raised, and the caller was served a request that
        had no record. Now the sink raises BEFORE any byte is written, so
        `_responded` is False and the backstop answers `500` — the request is
        REFUSED rather than silently unrecorded.

        ⚠ AND THE MUTATION STILL LANDED, which is the uncomfortable half and is
        asserted below rather than glossed: the append is complete before the
        outcome is known, so a broken sink produces a `500` over a write that
        happened. That is the fail-closed direction for an audit trail — the
        operator sees a failure instead of nothing — but it is not free, and a
        client that retries on `500` will append twice.
        """
        broken = _RaisingTraceback()

        def no_sink(_self, *_a, **_k):
            raise OSError(errno.EPIPE, "the audit sink is gone")

        monkeypatch.setattr(api.StoreRequestHandler, "_audit", no_sink)
        monkeypatch.setattr(api, "traceback", broken)
        path = entry_file(scoped_store, ALLOW_SCOPE)
        body = json.dumps({"text": BULLET_SINKLESS, "session": SESSION_A}).encode()
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            host = base.split("//", 1)[1]
            raw, saw_eof = _raw_exchange(
                host,
                _request(
                    host, "POST",
                    f"/api/v1/entry/{ALLOW_SCOPE}/{entry_ref(ALLOW_SCOPE)}/bullets",
                    body,
                ),
            )
        monkeypatch.undo()
        captured = capfd.readouterr()

        # 🔴 THE WRITE LANDED. Without this every assertion below is satisfied by
        # a server that refused the append and therefore had nothing to audit —
        # and it is also the half that makes the `500` below a TRADE rather than
        # a clean refusal. See the docstring.
        assert BULLET_SINKLESS in nuance_of(path), path.read_text()
        answers = _one_response(
            raw,
            "a POST whose audit sink was gone (EPIPE) and whose print_exc raised",
            saw_eof=saw_eof,
        )
        assert answers[0].startswith(b"500 "), (
            "a request whose audit record could not be written was SERVED. With "
            "`_audit` before `_respond` the sink raises before any byte is on "
            "the wire, so the backstop owes a 500 — an unrecordable request is "
            f"refused, not answered: {raw!r}"
        )
        assert saw_eof, "the connection was left open after an unknown-state request"
        # BOTH prints were attempted — the handler's exception and the audit
        # failure. `>= 1` would be satisfied without ever reaching the second
        # site, which is the one this test exists for.
        assert broken.calls == 2, broken.calls
        assert "Exception occurred during processing of request" not in captured.err, (
            "the backstop's second log write escaped `do_POST`: socketserver's "
            f"banner replaced the operator's traceback:\n{captured.err}"
        )

    def test_NO_stderr_write_in_the_REQUEST_HANDLER_can_raise(self):
        """🔴 THE LEDGER, BECAUSE THE PER-SITE FIX HAS NOW BEEN NEEDED THREE
        TIMES. `ea3d0a16` guarded the `_audit` call and left both of
        `_backstop`'s prints bare; `aa31d431` routed those two through
        `_print_exc_quietly` and left `_handle`'s `except UnicodeError:` arm bare
        on the reasoning that `_backstop` would catch it — which it cannot,
        because they are SIBLING `except` arms of one `try`.

        Each round fixed the site it had measured and left the next one. So this
        pins the RELATIONSHIP instead: inside `StoreRequestHandler`, the ONLY
        route to stderr is `_print_exc_quietly`. A new bare `print_exc` — or a
        `print(..., file=sys.stderr)` — anywhere in a request-handling method
        fails here, at the edit, rather than in production on the one request
        whose log sink is broken.

        🔴 SCOPED TO THE HANDLER CLASS, NOT THE FILE. `main()` and the
        trusted-proxy warner also write stderr; a raise there is a STARTUP
        failure, which is loud and correct. Only a write on the request path can
        silently trade a caller's response for a log line.

        🔴 WHAT IT CANNOT SEE, stated so it is not read as more than it is: a
        write reached through an alias (`_p = traceback.print_exc`), through
        `logging`, through `sys.stderr.write`, or from a module-level helper the
        handler calls. It is a ledger of the shape that has actually bitten three
        times, not a proof that no stderr write exists.
        """
        tree = ast.parse(SERVER_PATH.read_text())
        handler = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "StoreRequestHandler"
        )
        offenders = []
        for node in ast.walk(handler):
            if isinstance(node, ast.Attribute) and node.attr == "print_exc":
                offenders.append(f"traceback.print_exc at line {node.lineno}")
            elif isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg != "file":
                        continue
                    if isinstance(kw.value, ast.Attribute) and kw.value.attr == "stderr":
                        offenders.append(f"file=sys.stderr at line {kw.value.lineno}")
        assert not offenders, (
            "a stderr write on the REQUEST PATH that can itself raise — a broken "
            "log sink then decides whether the caller gets an answer. Route it "
            "through `_print_exc_quietly()`:\n  " + "\n  ".join(offenders)
        )
        # Positive control: the detector must be able to SEE the shape it bans,
        # or the zero above is a fact about the walker and not about the handler.
        #
        # 🔴 NON-EMPTY, NOT `== 1`. The upper bound this used to carry BANNED
        # what the docstring above explicitly blesses: a `traceback.print_exc`
        # inside `main()` is a startup failure, loud and correct, and adding one
        # failed this control with "`traceback.print_exc` is spelled at 2 sites"
        # — a message naming a problem that is not the problem, in a test whose
        # subject is the handler class. The control's job is only to prove the
        # walker can see the shape; the file-wide count is not its business.
        seen = [
            n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and n.attr == "print_exc"
        ]
        assert seen, "the print_exc detector sees nothing — it is broken"


class TestAnUndecodableEntryNameIsNotTheCallersFault:
    """🔴 A BADLY-NAMED FILE ANSWERED `400 bad request`, BLAMING THE CALLER FOR A
    STORE-SIDE PROBLEM — and echoed the codec's internal message while doing it.

    `UnicodeEncodeError` is a `ValueError`, so one legacy byte in a filename
    (`café.md` written under a non-UTF-8 locale) fell through `_handle`'s
    caller-error clause: `400 bad request: 'utf-8' codec can't encode character
    '\\udce9' in position 1906: surrogates not allowed`. The caller sent nothing
    wrong and can do nothing about it; the four-state doctrine's answer for "I
    could not look" is a 503.

    Pre-existing, not introduced by the write path — fixed here because it is the
    same consolidation rule this round is enforcing elsewhere.
    """

    def _with_bad_name(self, root: Path) -> str:
        """Drop a file whose NAME holds a byte no UTF-8 decode can round-trip."""
        name = b"caf\xe9.md".decode("utf-8", "surrogateescape")
        target = os.path.join(str(root / ALLOW_SCOPE), name)
        with open(target, "w", encoding="utf-8", errors="surrogateescape") as handle:
            handle.write(_entry("cafe-entry", ALLOW_SCOPE, nuance=KELP_NUANCE))
        return name

    def test_a_RECALL_over_a_badly_named_file_is_a_503_not_a_400(
        self, scoped_store: Path
    ):
        self._with_bad_name(scoped_store)
        with running(scoped_store, tokens=(ZACH,)) as (base, audit):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=ZACH_TOKEN
            )
            lines = await_audit(audit, 1)
        assert code == 503, (code, body)
        assert headers["X-Store-Status"] == "store-unreachable"
        assert headers["X-Store-Exit"] == "3"
        assert any(
            "result=503" in ln and "status=store-unreachable" in ln for ln in lines
        ), lines

    def test_a_RAISING_print_exc_in_THIS_arm_STILL_answers_and_audits(
        self, scoped_store: Path, monkeypatch
    ):
        """🔴 SIBLING `except` ARMS DO NOT CATCH EACH OTHER, AND THAT IS WHY THE
        DEFERRAL WAS WRONG. This arm's `traceback.print_exc` was left bare on the
        reasoning that it raises BEFORE its `_respond`, so `_handle`'s backstop
        would still answer the request. `_backstop` is called from the
        `except Exception:` arm of the SAME `try` as this `except UnicodeError:`
        — a sibling — so an exception raised in here leaves `_handle` entirely
        and reaches nothing.

        MEASURED with the badly-named file above, before the fix:

            sink alive  : 503 store-unreachable, 1 audit line
            sink broken : RemoteDisconnected,    0 audit lines

        Identical outcome, on the identical mechanism, as the `_backstop` bug
        `aa31d431` fixed — which is why the answer is the same helper.

        The observable is the RAW WIRE, not `fetch`: a `RemoteDisconnected` from
        a parsed client is a transport error, and a test that dies on one is a
        test whose own assertion never ran. `_responses(raw)` lets the failure be
        "no response came back", stated by this test.
        """
        broken = _RaisingTraceback()
        self._with_bad_name(scoped_store)
        monkeypatch.setattr(api, "traceback", broken)
        with running(scoped_store, tokens=(ZACH,)) as (base, audit):
            host = base.split("//", 1)[1]
            raw, _eof = _raw_exchange(
                host, _request(host, "GET", f"/api/v1/recall/{ALLOW_SCOPE}")
            )
            answers = _one_response(
                raw,
                "a GET on an unreadable store whose own log write raised, past a "
                "SIBLING backstop that never sees it",
            )
            assert answers[0].startswith(b"503 "), raw
            lines = await_audit(audit, 1)
        monkeypatch.undo()

        # The fixture's own positive control: a `print_exc` that was never called
        # cannot have been the thing under test.
        assert broken.calls >= 1, "the broken sink was never reached"
        assert any(
            "result=503" in ln and "status=store-unreachable" in ln for ln in lines
        ), lines
        # The constant sentence still, and still no codec internals on the wire.
        assert b"store unreadable: an entry name or body is not valid UTF-8\n" in raw
        for leaked in (b"codec", b"udce9", b"surrogates", b"closed pipe", b"Traceback"):
            assert leaked not in raw, (leaked, raw)

    def test_the_503_body_does_NOT_echo_the_CODEC_message(self, scoped_store: Path):
        """The message names the offending code point and its byte position —
        facts about a file the caller may not be allowed to know exists, handed
        out in an error the caller can trigger at will."""
        self._with_bad_name(scoped_store)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            _code, _headers, body = fetch(
                f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=ZACH_TOKEN
            )
        for leaked in (b"codec", b"udce9", b"surrogates", b"position", b"bad request"):
            assert leaked not in body.lower(), (leaked, body)
        assert body == b"store unreadable: an entry name or body is not valid UTF-8\n"

    def test_POSITIVE_CONTROL_a_REAL_caller_error_is_STILL_a_400(
        self, scoped_store: Path
    ):
        """🔴 Otherwise the fix above is satisfied by answering 503 to
        everything. `?limit=banana` is the caller's mistake and stays one."""
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{ALLOW_SCOPE}?limit=banana", token=ZACH_TOKEN
            )
        assert code == 400, (code, body)
        assert headers["X-Store-Status"] == "bad-request"
        assert b"bad request" in body


class TestOnlyAWriteROUTERetainsItsBody:
    """🟡 F6 — the pre-auth body buffer. `_consume_body(keep=True)` ran before
    `_identify_and_meter`, so every write-verb request retained up to
    `MAX_DRAIN_BYTES` in memory — including unauthenticated ones, and including
    verbs and paths whose only possible answer is the 405 tail.

    `_write_route` needs `self.command` and `path`, both known before the body is
    read, so `keep=` can be the route. The DRAIN is unchanged and unconditional:
    that is what closes the desync, and it is what the smuggling tests pin.
    """

    def _record_keeps(self, monkeypatch) -> "list[bool]":
        keeps: "list[bool]" = []
        real = api.StoreRequestHandler._consume_body

        def spy(self, *, keep: bool):
            keeps.append(keep)
            return real(self, keep=keep)

        monkeypatch.setattr(api.StoreRequestHandler, "_consume_body", spy)
        return keeps

    def test_a_POST_at_a_READ_route_keeps_NOTHING(
        self, scoped_store: Path, monkeypatch
    ):
        keeps = self._record_keeps(monkeypatch)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, _h, body = fetch(
                f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=ZACH_TOKEN,
                method="POST", data=b"x" * 4096,
            )
        monkeypatch.undo()
        assert code == 405, (code, body)
        assert keeps == [False], keeps

    def test_an_UNAUTHENTICATED_POST_at_a_READ_route_keeps_NOTHING(
        self, scoped_store: Path, monkeypatch
    ):
        """The case that motivated the fix: no credential, no route, a megabyte
        held anyway."""
        keeps = self._record_keeps(monkeypatch)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, _h, _b = fetch(
                f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=None,
                method="POST", data=b"x" * 4096,
            )
        monkeypatch.undo()
        assert code == 401, code
        assert keeps == [False], keeps

    def test_POSITIVE_CONTROL_a_real_WRITE_route_DOES_keep_its_body(
        self, scoped_store: Path, monkeypatch
    ):
        """🔴 Otherwise `keep == False` everywhere is satisfied by a server that
        can no longer write at all. The append must still LAND."""
        keeps = self._record_keeps(monkeypatch)
        path = entry_file(scoped_store, ALLOW_SCOPE)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, body = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_AFTER_PUT,
                session=SESSION_A,
            )
        monkeypatch.undo()
        assert code == 200, (code, body)
        assert headers["X-Store-Status"] == "appended"
        assert BULLET_AFTER_PUT in nuance_of(path), path.read_text()
        assert keeps == [True], keeps


class TestTheSurrogateNoteNamesTheRealGuard:
    """🟡 The `encode_entry_text` note on the append response used to justify
    itself with a FALSE premise: that the request body "is decoded `strict`, so
    it holds no surrogates". The conclusion holds; the reason did not, and a
    future reader reasoning from it would have deleted the clause that actually
    does the work.
    """

    def test_a_JSON_escape_DOES_produce_a_lone_surrogate(self):
        """🔴 THE MEASUREMENT THAT MAKES THE OLD REASON FALSE. The escape is
        plain ASCII on the wire, so no decode handler on the raw bytes has any
        bearing on it — `json` expands it afterwards."""
        import unicodedata

        raw = b'"\\ud800"'
        text = json.loads(raw.decode("utf-8"))  # the STRICT decode the note names
        assert len(text) == 1 and ord(text[0]) == 0xD800
        assert unicodedata.category(text[0]) == "Cs"
        with pytest.raises(UnicodeEncodeError):
            text.encode("utf-8")

    def test_the_Cs_CATEGORY_is_what_actually_refuses_it(self, scoped_store: Path):
        """So `Cs` in `_FORBIDDEN_CATEGORIES` is LOAD-BEARING, not defensive."""
        assert "Cs" in api._FORBIDDEN_CATEGORIES
        before = tree_hash(scoped_store)
        surrogate = json.loads('"\\ud800"')
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, body = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE,
                text=f"a bullet with {surrogate} in it",
                session=SESSION_A,
            )
        assert code == 400, (code, body)
        assert headers["X-Store-Status"] == "bad-request"
        assert b"U+D800 (Cs)" in body, body
        assert tree_hash(scoped_store) == before

    def test_the_note_no_longer_gives_the_FALSE_reason(self):
        """🔴 PINNED ON THE WHOLE NORMALISED SENTENCE. A guard that grepped for
        `strict` would be walked by any reword; the claim is machine-readable
        because the string is.

        The leading `#` of each comment line is stripped BEFORE normalising —
        otherwise every sentence that wraps carries a `#` into the middle of
        itself and no multi-line claim in this file could ever be pinned.
        """
        text = " ".join(
            " ".join(
                line.strip().lstrip("#").strip()
                for line in SERVER_PATH.read_text().splitlines()
            ).split()
        )
        assert (
            "A stored bullet holding an undecodable byte cannot hash-equal any "
            "body a client can send (the body is decoded `strict`, so it holds "
            "no surrogates, and the byte survives `bullet_content` into the "
            "hash)."
        ) not in text, "the response-encode note still gives the false reason"
        assert (
            "the reason is 🔴 `Cs` IN `_FORBIDDEN_CATEGORIES` — WHICH IS "
            "THEREFORE LOAD-BEARING HERE, NOT MERELY DEFENSIVE, AND MUST NOT BE "
            "REMOVED ON THE STRENGTH OF THIS COMMENT."
        ) in text, "the corrected reason is not in the file"


# =============================================================================
# THE AUDIT LINE IS WRITTEN *BEFORE* THE RESPONSE, AND THE SINK IS SERIALISED.
#
# 🔴 TWO DEFECTS, ONE CALL-SITE SHAPE. Every handler in `server.py` used to
# `_respond(...)` and only then `_audit(...)`, against a `ThreadingHTTPServer`
# whose default sink is a bare unlocked `print`.
#
#   ORDERING     A SEQUENTIAL client's request N+1 is accepted on a NEW handler
#                thread the moment response N is on the wire, so handler N+1
#                could reach the sink before handler N did and the stream came
#                out in the wrong order. Seen once in the nix sandbox tier:
#                `test_the_audit_line_names_WHICH_fingerprint_matched` failed on
#                `token=<A> in audit[0]` with the two records SWAPPED, then
#                passed on re-run and in ~20 runs after. Rare, and real.
#   ATOMICITY    `print(line, flush=True)` is TWO writes on one stream — the
#                record, then the terminator — so two overlapping handlers can
#                emit `<A><B>\n\n`: one line carrying two requests' fields and
#                one carrying none.
#
# 🔴 AND THEY NEED DIFFERENT FIXES, WHICH IS WHY THEY GET DIFFERENT TESTS.
# Ordering alone leaves genuinely concurrent callers interleaving; a lock alone
# leaves a sequential client's records out of order. Each test below is red
# under exactly one of the two mutants — see the PR's matrix.
#
# 🔴 NOTHING HERE WAITS ON A SLEEP TO MAKE ITS VERDICT. The observable is forced
# by the injected sink (`running(..., wrap_sink=...)`): a GATE that holds the
# first record so the client's own progress becomes the measurement, and a
# deliberately NON-ATOMIC writer that cannot help but interleave if it is
# allowed to run twice at once. The two bounded windows below are windows for a
# FAILURE to appear in, never a wait for success to finish — the same shape
# `settle` uses, and for the same reason.
# =============================================================================

# How long to keep watching for the client to sail past a HELD audit record.
# It is the width of the ordering claim: with the record held, the response must
# not have been written, so the client cannot have started its next request. Not
# a wait for anything to succeed — the gate is released immediately afterwards,
# whatever we saw. A saturated host stretches the client and this window on the
# SAME clock, so it cannot turn a green run red; it can only miss a violation.
_ORDER_PROBE_S = 1.0

# How long the non-atomic sink holds its record open, waiting for a second
# writer to interleave with it. With the sink serialised no second writer can
# ever arrive, so this window is spent in full, once, by one test.
_SPLIT_WINDOW_S = 0.5

# 🔴 A TOKEN THIS SECTION OWNS, AND ITS FINGERPRINT WRITTEN OUT BY HAND. The
# whole-line pin below must not compute its expectation with `api.token_id` —
# that asserts `x == x` and stays green through a change to the digest that
# every operator's grep depends on. sha256("p"*48).hexdigest()[:12].
PINNED_TOKEN = "p" * 48
PINNED_TOKEN_ID = "d64bf41c3707"

# 🔴 THE WHOLE RECORD, FIELD FOR FIELD, NOT A KEYWORD. A guard spelled as
# `"token=" in line` passes on a line whose fields are in the wrong order, on a
# line that lost `identity=`, and on two records fused into one — which is
# precisely the atomicity defect. `fullmatch` or nothing.
_AUDIT_LINE_RE = re.compile(
    r"store-api audit "
    r"ts=\S+ ip=\S+ peer=(?:-|trusted|untrusted) method=\S+ path=\S+ "
    r"token=\S+ identity=\S+ auth=(?:ok|fail) result=\d+ status=\S+"
)


def _poll_until(predicate, timeout: float) -> bool:
    """Spin until `predicate()` is true or `timeout` elapses. Says which.

    🔴 A POLL RATHER THAN `Event.wait(t)`, AND THE REASON IS A GUARD IN THIS
    FILE RATHER THAN A PREFERENCE. `_KILLERS` counts a bare `.wait` ATTRIBUTE as
    terminating a process, so a test that waits on an `Event` and also reads
    audit records is a false offender in
    `test_no_test_reads_an_AUDIT_LINE_from_a_process_it_just_terminated` — the
    same collision that made `settle` spell its barrier `wait_closed()` instead
    of `closed.wait()`. Polling is the identical synchronisation in a shape that
    guard can tell apart from a kill.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class _Rendezvous:
    """A meeting point for `parties` threads that TIMES OUT instead of blocking.

    Deliberately not `threading.Barrier`: its `wait()` carries the verb
    `_KILLERS` reads as a process kill (see `_poll_until`), and a broken barrier
    RAISES where every caller here wants a boolean. It is also sticky on purpose
    — once enough threads have arrived it stays satisfied — so a late arrival
    returns immediately rather than hanging on a second generation.
    """

    def __init__(self, parties: int = 2) -> None:
        self.parties = parties
        self.arrived = 0
        self._lock = threading.Lock()

    def arrive(self, timeout: float) -> bool:
        """Count this thread in, then wait for the rest. `False` = timed out."""
        with self._lock:
            self.arrived += 1
        return _poll_until(lambda: self.arrived >= self.parties, timeout)


class _GatedSink:
    """Holds the FIRST record handed to it until the test lets go.

    🔴 THIS IS THE WHOLE INSTRUMENT FOR THE ORDERING CLAIM, AND IT WORKS BY
    MAKING THE CLIENT THE MEASUREMENT. With the record held:

      audit-before-respond -> response 1 is not on the wire, so a sequential
                              client is still blocked inside its FIRST request
      respond-before-audit -> response 1 went out before the sink was entered,
                              so the client has already issued its SECOND

    "Which request has the client started" is a fact about the client, observed
    from outside the server, and it does not depend on how fast anything runs.
    """

    def __init__(self) -> None:
        self.reached = _Rendezvous(parties=1)   # set when the first record is held
        self.released = False
        self.seen = 0
        self._lock = threading.Lock()

    def release(self) -> None:
        self.released = True

    def __call__(self, inner):
        def sink(line: str) -> None:
            with self._lock:
                self.seen += 1
                first = self.seen == 1
            if first:
                self.reached.arrive(0.0)
                # A generous ceiling: the test releases explicitly, in a
                # `finally`, so this bound is only reached if the test itself
                # died — in which case hanging the server thread would turn one
                # failure into a suite that never finishes.
                _poll_until(lambda: self.released, 30.0)
            inner(line)

        return sink


class _SplittingSink:
    """A sink whose write is NON-ATOMIC in exactly the way `print` is.

    `print(line, flush=True)` writes the record and the terminator separately.
    This reproduces that split and FORCES the hazard rather than hoping for it:
    between the two writes it waits for a SECOND writer to arrive, so any sink
    that admits two threads at once must produce `<A><B>\\n\\n`. When the sink is
    serialised the second writer can never arrive, the window is spent, and the
    two records come out whole and terminated.

    `pieces` is an ordinary list appended without a lock ON PURPOSE — the append
    ORDER is the measurement, and serialising it here would destroy the very
    thing under test. `list.append` is atomic, so the list itself is safe.
    """

    def __init__(self, window: float) -> None:
        self.pieces: "list[str]" = []
        self.window = window
        self.mid = _Rendezvous(parties=2)

    @property
    def stream(self) -> str:
        """Everything the sink wrote, in the order it wrote it."""
        return "".join(self.pieces)

    def __call__(self, inner):
        def sink(line: str) -> None:
            self.pieces.append(line)
            self.mid.arrive(self.window)
            self.pieces.append("\n")
            inner(line)

        return sink


def _tee(records: "list[str]"):
    """A `wrap_sink` that copies every record into `records` and passes it on.

    The copy is what a test may read while the server is RUNNING: it is the
    test's own list, so the ban on indexing the live `AuditLog`
    (`test_no_test_INDEXES_a_live_audit_list`) is not being walked around — that
    ban is about reading a shared record with no waiter, and these sites are
    asserting a per-request ordering property the waiter would hide.
    """

    def wrap(inner):
        def sink(line: str) -> None:
            records.append(line)
            inner(line)

        return sink

    return wrap


def _without_ts(line: str) -> str:
    """The record with its one genuinely varying field replaced by a marker."""
    return re.sub(r"ts=\S+", "ts=<TS>", line, count=1)


class TestTheAuditLineIsWrittenBEFORETheResponse:
    """§ the record is a PRECONDITION of the answer, not a footnote to it."""

    def test_HOLDING_the_first_record_STOPS_the_client_reaching_the_second(
        self, store: Path
    ):
        """🔴 THE DETERMINISTIC REPRODUCTION. No sleep decides this verdict.

        The sink holds request A's record. Two mutually exclusive states follow,
        and the client tells us which one we are in: if the response is written
        before the record, A has already come back and the client has issued B;
        if the record is written first, the client is still inside A.

        MEASURED at the base ref (`_audit` after `_respond`): `reached` came back
        `['A', 'B']` and the records came back in the order B, A. Both
        assertions below are red there, on their own messages.
        """
        gate = _GatedSink()
        reached: "list[str]" = []

        def client(base: str) -> None:
            for name, scope in (("A", SCOPE), ("B", OTHER_SCOPE)):
                # Recorded BEFORE the request, so this list means "has issued",
                # never "has completed" — the client blocking inside A must not
                # be readable as the same state as never having started it.
                reached.append(name)
                fetch(f"{base}/api/v1/recall/{scope}", token=GOOD_TOKEN)

        with running(store, wrap_sink=gate) as (base, audit):
            worker = threading.Thread(target=client, args=(base,), daemon=True)
            worker.start()
            try:
                assert gate.reached.arrive(15.0), (
                    "the sink was never handed a record — the gate cannot have "
                    "measured anything"
                )
                # A window for the VIOLATION to appear in. It is released
                # immediately after, whatever we saw; nothing here waits for a
                # success to finish.
                _poll_until(lambda: len(reached) >= 2, _ORDER_PROBE_S)
                seen = list(reached)
            finally:
                gate.release()
            worker.join(timeout=30)
            assert not worker.is_alive(), "the client never finished"
            await_audit(audit, 2)

        assert seen == ["A"], (
            "the client issued its SECOND request while the FIRST request's "
            "audit record was still being held — so the response was written "
            f"before the record. Requests issued: {seen}"
        )
        # 🔴 THE ORDER, ASSERTED RATHER THAN ASSUMED — and this is the one site
        # in the file entitled to read positions without interleaving its waits,
        # because the ordering IS its subject. See `await_audit`.
        lines = settle(audit, 2)
        assert f"path=/api/v1/recall/{SCOPE}" in lines[0], lines
        assert f"path=/api/v1/recall/{OTHER_SCOPE}" in lines[1], lines

    def test_EVERY_sequential_response_arrives_WITH_its_record_already_written(
        self, store: Path
    ):
        """The same property read at SIX points instead of one, per request.

        ⚠ HONEST LABELLING: at the base ref this is a RACE, not a certain red —
        the handler reaches its `print` microseconds after the client returns,
        so it fails only sometimes. It is here because the property it states is
        the one an operator relies on ("the response implies the record"), and
        stating it once per request is what makes the claim cover more than a
        single hand-built scenario. The DETERMINISTIC guard is the gate above.
        """
        records: "list[str]" = []
        wanted = [SCOPE, OTHER_SCOPE, SCOPE, OTHER_SCOPE, SCOPE, OTHER_SCOPE]
        with running(store, wrap_sink=_tee(records)) as (base, audit):
            for issued, scope in enumerate(wanted, start=1):
                code, _headers, _body = fetch(
                    f"{base}/api/v1/recall/{scope}", token=GOOD_TOKEN
                )
                assert code == 200, (scope, code)
                snapshot = list(records)
                assert len(snapshot) == issued, (
                    f"request {issued} (/{scope}) came back with "
                    f"{len(snapshot)} record(s) written, expected {issued} — "
                    "the response does not imply the record"
                )
                assert f"path=/api/v1/recall/{scope}" in snapshot[-1], (
                    f"request {issued} was for /{scope} but the newest record "
                    f"is {snapshot[-1]!r} — the records are out of order"
                )
            await_audit(audit, len(wanted))
        settle(audit, len(wanted))

    def test_the_WHOLE_normalised_record_is_pinned_field_for_field(
        self, store: Path
    ):
        """🔴 PINNED AS ONE STRING, BECAUSE EVERY FIELD IS LOAD-BEARING.

        `token=` is what makes an overlap rotation checkable, `identity=` is
        which allowlist applied, `peer=` is how `ip=` must be read, and their
        ORDER is what the operator's grep and the Loki parser are written
        against. A test spelled as four `in` checks is walked by a reorder, by a
        dropped field, and by two records fused into one line.

        The expectation is a literal this test owns — the fingerprint included
        (see `PINNED_TOKEN_ID`) — never a value computed from `server.py`.
        """
        with running(store, token=PINNED_TOKEN) as (base, audit):
            code, _headers, _body = fetch(
                f"{base}/api/v1/recall/{SCOPE}",
                token=PINNED_TOKEN,
                client_ip=CLIENT_IP,
            )
            line = await_audit(audit, 1)[0]
        assert code == 200, code
        assert _without_ts(line) == (
            "store-api audit ts=<TS> "
            f"ip={CLIENT_IP} peer=trusted method=GET "
            f"path=/api/v1/recall/{SCOPE} "
            f"token={PINNED_TOKEN_ID} identity=legacy auth=ok "
            "result=200 status=recalled"
        ), line
        # The normaliser must actually have removed something, or the pin above
        # is over a string that never varied and says nothing about `ts=`.
        assert _without_ts(line) != line, line
        assert re.search(r"ts=\d{4}-\d{2}-\d{2}T", line), line


class TestTwoConcurrentRequestsCannotINTERLEAVETheirRecords:
    """§ ordering fixes a SEQUENTIAL client; only a lock fixes overlapping ones.

    🔴 THE HAZARD IS IN THE SINK, NOT IN THE HANDLER. `ThreadingHTTPServer` runs
    a handler per connection and the shipped sink is `print(line, flush=True)`,
    which is two writes on one `TextIOWrapper`. The GIL orders the individual
    writes and says nothing about which pair they belong to, so the failure mode
    is a stream in which one line holds two requests' fields — `token=` from one
    caller beside `path=` from another, in the record the rotation procedure and
    the auth-fail alert both parse.
    """

    def test_two_CONCURRENT_records_come_out_WHOLE_and_separately_terminated(
        self, store: Path, monkeypatch
    ):
        """🔴 THE INTERLEAVING IS FORCED, NOT AWAITED.

        Two things are arranged, and both are needed:

          THE DOOR   `_audit` is wrapped so both handler threads are parked
                     immediately BEFORE the serialised region and released
                     together. Without it, thread A could simply finish before
                     thread B was scheduled and the unlocked sink would look
                     serialised — a mutant surviving on timing.
          THE SPLIT  the sink writes the record, waits for a second writer, then
                     writes the terminator. If two threads are ever inside it at
                     once, B's record MUST land between A's two writes.

        So `<A>\\n<B>\\n` is only reachable when the sink is serialised, and
        `<A><B>\\n\\n` is the certain outcome when it is not. MEASURED with the
        lock removed: one line holding both records and one empty line, failing
        on the whole-line regex below.
        """
        split = _SplittingSink(window=_SPLIT_WINDOW_S)
        door = _Rendezvous(parties=2)
        real_audit = api.StoreRequestHandler._audit

        def at_the_door(self, *args, **kwargs):
            # OUTSIDE the serialised region on purpose: this parks both threads
            # at the entrance, it does not hold them apart.
            door.arrive(15.0)
            return real_audit(self, *args, **kwargs)

        monkeypatch.setattr(api.StoreRequestHandler, "_audit", at_the_door)
        with running(store, wrap_sink=split) as (base, audit):
            workers = [
                threading.Thread(
                    target=fetch,
                    args=(f"{base}/api/v1/recall/{scope}",),
                    kwargs={"token": GOOD_TOKEN},
                    daemon=True,
                )
                for scope in (SCOPE, OTHER_SCOPE)
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=30)
            assert not any(worker.is_alive() for worker in workers), (
                "a client never came back — the door never opened"
            )
            await_audit(audit, 2)
        monkeypatch.undo()

        # The positive control for the instrument itself: a door nobody reached
        # would make every assertion below a statement about a sink that was
        # only ever entered once.
        assert door.arrived == 2, (
            f"{door.arrived} handler thread(s) reached the sink — the two "
            "requests were not concurrent, so nothing was measured"
        )
        stream = split.stream
        assert stream.endswith("\n"), repr(stream)
        emitted = stream.split("\n")[:-1]
        assert len(emitted) == 2, (
            f"the stream holds {len(emitted)} line(s) for two requests: "
            f"{stream!r}"
        )
        for line in emitted:
            assert line.count(AUDIT_PREFIX) == 1, (
                f"{line.count(AUDIT_PREFIX)} records were fused into one line — "
                f"two writers interleaved: {line!r}"
            )
            assert _AUDIT_LINE_RE.fullmatch(line), (
                f"not a whole, well-formed audit record: {line!r}"
            )
        paths = sorted(re.search(r"path=(\S+)", line).group(1) for line in emitted)
        assert paths == sorted(
            [f"/api/v1/recall/{SCOPE}", f"/api/v1/recall/{OTHER_SCOPE}"]
        ), paths

    def test_the_LOCK_is_ONE_object_every_handler_class_shares(self, store: Path):
        """🔴 A PER-INSTANCE LOCK WOULD SERIALISE NOTHING, AND WOULD LOOK FINE.

        `ThreadingHTTPServer` builds a NEW handler instance per connection, so a
        lock created in `__init__` is a fresh lock per caller — never contended,
        never wrong-looking, and completely inert. The thing being serialised is
        one process's stdout, so the lock has to outlive the instance and the
        server: `build_server`'s `_Handler` subclass must share the base class's.
        """
        first = api.build_server(
            host="127.0.0.1", port=0, store_root=str(store),
            tokens=(GOOD_TOKEN,), trusted_proxies=(LOOPBACK_PROXY,),
        )
        second = api.build_server(
            host="127.0.0.1", port=0, store_root=str(store),
            tokens=(GOOD_TOKEN,), trusted_proxies=(LOOPBACK_PROXY,),
        )
        try:
            shared = api.StoreRequestHandler._audit_lock
            assert first.RequestHandlerClass._audit_lock is shared
            assert second.RequestHandlerClass._audit_lock is shared, (
                "two servers in one process hold different audit locks, so "
                "their records can still interleave on one stdout"
            )
        finally:
            first.server_close()
            second.server_close()


class TestNoRequestEverAuditsTwiceAndNoneVanishes:
    """§ the record count per request, across the outcomes and the backstop."""

    def test_FOUR_different_outcomes_produce_EXACTLY_four_records(
        self, store: Path
    ):
        """One line per request at four DIFFERENT call sites — a 200, a 401, a
        404 and a 405 — so "the reorder duplicated or dropped a record" is
        checked where the reorder happened rather than on one happy path.

        `settle`, not a snapshot: the claim is a CEILING.
        """
        with running(store) as (base, audit):
            ok, _h, _b = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
            await_audit(audit, 1)
            denied, _h, _b = fetch(f"{base}/api/v1/recall/{SCOPE}")
            await_audit(audit, 2)
            missing, _h, _b = fetch(f"{base}/api/v1/nowhere", token=GOOD_TOKEN)
            await_audit(audit, 3)
            refused, _h, _b = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, method="POST"
            )
            await_audit(audit, 4)
        assert (ok, denied, missing, refused) == (200, 401, 404, 405)
        lines = settle(audit, 4)
        assert [int(re.search(r"result=(\d+)", ln).group(1)) for ln in lines] == [
            200, 401, 404, 405
        ], lines

    def test_healthz_adds_NOTHING_even_while_an_audited_request_is_in_flight(
        self, store: Path
    ):
        """🔴 `/healthz` IS DELIBERATELY UNAUDITED, AND THE ZERO NEEDS A CONTROL.

        Sequential probes are already pinned by `test_health_is_NOT_audited`.
        This asks the question the reorder actually changes: the probes run
        CONCURRENTLY with an audited request, i.e. through the same serialised
        sink, where a `/healthz` that had acquired a record would be visible as
        a fifth line. The audited request is the positive control — its record
        proves the sink was wired to something.
        """
        with running(store) as (base, audit):
            probes = [
                threading.Thread(
                    target=fetch, args=(f"{base}/healthz",), daemon=True
                )
                for _ in range(4)
            ]
            for probe in probes:
                probe.start()
            code, _headers, _body = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN
            )
            for probe in probes:
                probe.join(timeout=30)
            assert not any(probe.is_alive() for probe in probes)
            await_audit(audit, 1)
        assert code == 200, code
        lines = settle(audit, 1)
        assert "/healthz" not in lines[0], lines[0]

    def test_a_handler_that_DIES_before_its_response_has_ALREADY_logged_it(
        self, store: Path, monkeypatch
    ):
        """🔴 THE PAYOFF OF AUDITING FIRST, AND IT IS RED AT THE BASE REF.

        `_respond` is made to raise on its FIRST call only — so the backstop's
        own `500` still goes out, and the induced failure sits exactly in the
        window between the decision and the wire.

          base ref (respond first)  ONE record: `result=500
                                    status=internal-error`. What the request
                                    actually DID is lost — the outcome was never
                                    written.
          here    (audit first)     TWO: the outcome (`result=200
                                    status=recalled`) already in the stream, and
                                    the backstop's 500 beside it.

        A duplicate beats a hole: the operator can see that the request was
        served AND that answering it failed. `settle` pins the ceiling at two, so
        "audit first" cannot quietly become "audit twice on every request".

        🔴 THE OWN-COPY (`_tee`) IS WHY THIS DIES ON ITS OWN MESSAGE. Waiting for
        two records first would make the KILLING assertion `await_audit`'s
        ("expected at least 2, got 1") — a true statement that names a count
        instead of naming the missing outcome. The copy is read before any
        waiter, so the failure says which record was lost.
        """
        calls = {"n": 0}
        records: "list[str]" = []
        real_respond = api.StoreRequestHandler._respond

        def flaky(self, code, body, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ZeroDivisionError("between the record and the wire")
            return real_respond(self, code, body, **kwargs)

        monkeypatch.setattr(api.StoreRequestHandler, "_respond", flaky)
        with running(store, wrap_sink=_tee(records)) as (base, audit):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN
            )
            await_audit(audit, 1)
        monkeypatch.undo()

        assert code == 500, (code, body)
        assert headers["X-Store-Status"] == "internal-error"
        assert any("result=200 status=recalled" in ln for ln in records), (
            "the outcome the handler had already decided was NEVER WRITTEN — the "
            "record died with the response it was waiting for. The trail holds "
            f"only: {records}"
        )
        # And it really was the injected failure, not a store that answered 500
        # on its own: `_respond` was reached twice, the handler's and the
        # backstop's.
        assert calls["n"] == 2, calls
        lines = settle(audit, 2)
        assert "result=200 status=recalled" in lines[0], lines
        assert "result=500 status=internal-error" in lines[1], lines

    def test_an_exception_AFTER_a_completed_response_audits_EXACTLY_TWICE(
        self, store: Path, monkeypatch
    ):
        """The other arm, with the CEILING the existing coverage of it lacks.

        `TestTheREADDispatchIsBackstoppedToo` asserts both records are present;
        it cannot see a THIRD. The already-responded arm is now reachable only
        by a statement failing after a completed `_respond` — which is what this
        wrapper is — so pinning its count is the only way "the reorder did not
        add a record here" stays a checked claim.
        """
        real_recall = api.StoreRequestHandler._recall

        def then_raise(self, *args, **kwargs):
            real_recall(self, *args, **kwargs)
            raise ZeroDivisionError("after the recall response")

        monkeypatch.setattr(api.StoreRequestHandler, "_recall", then_raise)
        with running(store) as (base, audit):
            code, _headers, _body = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN
            )
            await_audit(audit, 2)
        monkeypatch.undo()

        assert code == 200, code
        lines = settle(audit, 2)
        assert "result=200 status=recalled" in lines[0], lines
        assert "result=500 status=internal-error-after-response" in lines[1], lines


# =============================================================================
# THE LEDGERS — the two structural rules, pinned where they are written.
# =============================================================================

# 🔴 `_backstop` IS THE ONE EXEMPTION, AND IT IS EARNED, NOT GRANTED. The status
# it records (`internal-error` vs `internal-error-after-response`) is not
# knowable until `_responded` has been read, and on the already-responded arm the
# bytes went out before the function was even called. Every other site in the
# class decides its outcome first and can therefore log it first.
_AUDITS_AFTER_RESPONDING = frozenset({"_backstop"})

# The number of `_audit(...)` calls that sit immediately in front of the
# `_respond(...)`/`_unauthorized()` they describe. A census, so DELETING a call
# site is as loud as reordering one — a route that stops auditing is the failure
# this whole section is about, and an offender list of zero cannot see it.
_AUDIT_BEFORE_RESPOND_PAIRS = 33


def _respond_names() -> "frozenset[str]":
    """The methods that put bytes on the wire. `_unauthorized` is a `_respond`."""
    return frozenset({"_respond", "_unauthorized"})


def _self_call(stmt: ast.AST) -> "str | None":
    """`self.<name>(...)` as a bare statement -> `<name>`; anything else None."""
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return None
    func = stmt.value.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) \
            and func.value.id == "self":
        return func.attr
    return None


def _audit_order(source: "str | None" = None) -> "tuple[int, list[str]]":
    """`(pairs, offenders)` for the audit-before-respond rule in `server.py`.

    An offender is an `_audit(...)` that appears AFTER a `_respond(...)` or
    `_unauthorized()` in the same statement sequence — the shape both defects
    came from. A pair is an `_audit(...)` immediately in front of one.

    Scoped to `StoreRequestHandler` and to each method's own statement
    sequences, so a helper elsewhere in the file cannot make the count drift.
    """
    tree = ast.parse(source if source is not None else SERVER_PATH.read_text())
    handler = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "StoreRequestHandler"
    )
    responders = _respond_names()
    pairs, offenders = 0, []
    for method in handler.body:
        if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if method.name in _AUDITS_AFTER_RESPONDING:
            continue
        for node in ast.walk(method):
            for field in ("body", "orelse", "finalbody"):
                seq = getattr(node, field, None)
                if not isinstance(seq, list):
                    continue
                responded = None
                for index, stmt in enumerate(seq):
                    name = _self_call(stmt)
                    if name in responders and responded is None:
                        responded = stmt.lineno
                    elif name == "_audit":
                        if responded is not None:
                            offenders.append(
                                f"{method.name}: _audit at line {stmt.lineno} "
                                f"follows a response at line {responded}"
                            )
                        following = seq[index + 1] if index + 1 < len(seq) else None
                        if following is not None and _self_call(following) in responders:
                            pairs += 1
    return pairs, sorted(offenders)


def test_every_audit_call_site_PRECEDES_its_response():
    """🔴 THE RULE, PINNED WHERE IT IS WRITTEN RATHER THAN ONLY WHERE IT SHOWS.

    The behavioural guards above catch the reorder on the routes they exercise.
    This catches it at the EDIT, on all 33 sites at once, including the ones no
    test drives — and it catches the far likelier regression, which is not
    somebody reverting the fix but somebody adding the 34th call site by copying
    the shape of a neighbour they happened to read before this change.

    🔴 WHAT IT CANNOT SEE, so it is not read as more than it is: an `_audit`
    reached through a helper (`self._log_it()`), a response written by calling
    `self.wfile.write` directly, and any ordering between two DIFFERENT
    statement sequences (an `_audit` in an `else:` after a `_respond` in the
    `if:` is not a violation and is not counted as one).
    """
    pairs, offenders = _audit_order()
    assert not offenders, (
        "these sites put the response on the wire before the record. A "
        "sequential client's next request then races the record it precedes, "
        "and a crash in between loses the outcome entirely — audit first, then "
        "respond (see `StoreRequestHandler._audit`):\n  " + "\n  ".join(offenders)
    )
    assert pairs == _AUDIT_BEFORE_RESPOND_PAIRS, (
        f"expected {_AUDIT_BEFORE_RESPOND_PAIRS} `_audit(...)` calls sitting "
        f"immediately in front of a response, found {pairs}. A route was added "
        "or stopped auditing; if that is intended, update the census AND say in "
        "the commit which requests no longer appear in the audit trail."
    )


def test_the_audit_ORDER_detector_can_SEE_the_defect_it_bans():
    """🔴 THE POSITIVE CONTROL, BUILT FROM THE REAL FILE.

    An offender list of zero is a fact about the walker until the walker has
    been shown the shape. The mutation is applied to a COPY of `server.py` — the
    405 pair, spelled exactly as it is in the source — and both halves of the
    verdict must move: one offender named, and the pair census down by one.
    A control built from a synthetic two-line fixture would prove neither, since
    the real file's shape (a method body inside a class inside a module, with
    the pair nested in an `if`) is the thing being parsed.
    """
    src = SERVER_PATH.read_text()
    correct = (
        '        self._audit(path, 405, "method-not-allowed")\n'
        '        self._respond(405, b"read-only\\n", headers={"Allow": "GET, HEAD"})\n'
    )
    assert src.count(correct) == 1, (
        "fixture drift: the 405 pair is no longer spelled the way this control "
        "expects, so the mutation below would not apply"
    )
    swapped = src.replace(
        correct,
        '        self._respond(405, b"read-only\\n", headers={"Allow": "GET, HEAD"})\n'
        '        self._audit(path, 405, "method-not-allowed")\n',
        1,
    )
    assert swapped != src, "the mutation did not apply — this control is vacuous"

    # 🔴 THE LINE NUMBERS ARE COMPUTED FROM THE MUTATED TEXT THIS TEST OWNS, and
    # the offender must name BOTH. An earlier draft accepted
    # `"_audit at line" in offenders[0]`, which every offender string contains by
    # construction — a disjunction that could not fail, sitting inside the one
    # test whose whole job is to prove the detector points somewhere real. A
    # detector that flags the right COUNT at the wrong PLACE sends the next
    # reader to a site that is fine.
    audit_line = 1 + swapped.splitlines().index(
        '        self._audit(path, 405, "method-not-allowed")'
    )
    respond_line = audit_line - 1

    pairs, offenders = _audit_order(swapped)
    assert len(offenders) == 1, (
        f"the detector saw {len(offenders)} offender(s) in a source with "
        f"exactly one reordered site: {offenders}"
    )
    assert (
        f"_audit at line {audit_line} follows a response at line {respond_line}"
        in offenders[0]
    ), (offenders, audit_line, respond_line)
    assert pairs == _AUDIT_BEFORE_RESPOND_PAIRS - 1, (
        f"the pair census did not move when a pair was broken: {pairs}"
    )


def _sink_calls_outside_the_lock(source: "str | None" = None) -> "list[int]":
    """Lines in `_audit` where the SINK is called from outside `_audit_lock`."""
    tree = ast.parse(source if source is not None else SERVER_PATH.read_text())
    audit = next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_audit"
    )
    guarded: "list[tuple[int, int]]" = []
    for node in ast.walk(audit):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            expr = item.context_expr
            if isinstance(expr, ast.Attribute) and expr.attr == "_audit_lock" \
                    and isinstance(expr.value, ast.Name) and expr.value.id == "self":
                guarded.append((node.body[0].lineno, node.body[-1].end_lineno))
    loose = []
    for node in ast.walk(audit):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "audit"
                and isinstance(func.value, ast.Name) and func.value.id == "self"):
            continue
        if not any(start <= node.lineno <= end for start, end in guarded):
            loose.append(node.lineno)
    return sorted(loose)


def test_the_SINK_call_is_inside_the_lock_not_beside_it():
    """🔴 A LOCK HELD AROUND THE WRONG THING IS A LOCK THAT SERIALISES NOTHING.

    The behavioural guard is
    `test_two_CONCURRENT_records_come_out_WHOLE_and_separately_terminated`; this
    is the cheap ledger beside it, because the way this regresses is not a
    deletion (which that test catches loudly) but a refactor that keeps the
    `with` and moves the sink call out from under it — leaving a lock, a
    comment, and no serialisation.
    """
    loose = _sink_calls_outside_the_lock()
    assert loose == [], (
        "the audit SINK is called from outside `self._audit_lock` at lines "
        f"{loose} — two concurrent handlers can interleave their writes"
    )

    # 🔴 THE CONTROL'S MUTATION IS STRUCTURAL, NOT A FIXED TWO-LINE STRING, AND
    # THAT IS A FIX RATHER THAN A FLOURISH. It used to `str.replace` the exact
    # text `with self._audit_lock:\n    self.audit(line)\n` with the bare call.
    # MEASURED against an unrelated mutant that added a second statement inside
    # the block: the two-line pattern still matched, the replacement orphaned the
    # extra statement at the old indent, and this test died inside `ast.parse`
    # with `IndentationError` — an exception, from a control whose whole job is
    # to produce a specific ASSERTION. Lifting the block by its own AST span
    # keeps the mutant syntactically valid whatever the body holds.
    src = SERVER_PATH.read_text()
    lines = src.splitlines(keepends=True)
    # 🔴 `audit_fn`, NOT `audit`. This binds an AST FunctionDef, but
    # `_raw_audit_reads` flags the NAME — deliberately, since it cannot tell an
    # audit-record read from anything else spelled that way — so a local called
    # `audit` here makes this a FALSE OFFENDER in
    # `test_no_test_INDEXES_a_live_audit_list`. MEASURED: it did, in BOTH gate
    # tiers, on that test. What missed it was checking the edit with a `-k`
    # filter that excluded the seam guard — a subset run is a claim about the
    # subset. Renamed rather than widening the guard: the guard is right that a
    # `test*` function should not carry a bare `audit` Load.
    audit_fn = next(
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_audit"
    )
    blocks = [
        node for node in ast.walk(audit_fn)
        if isinstance(node, (ast.With, ast.AsyncWith))
        and any(
            isinstance(item.context_expr, ast.Attribute)
            and item.context_expr.attr == "_audit_lock"
            for item in node.items
        )
    ]
    assert len(blocks) == 1, (
        f"fixture drift: `_audit` holds {len(blocks)} `_audit_lock` block(s), so "
        "the control does not know which one to lift"
    )
    block = blocks[0]
    body = lines[block.body[0].lineno - 1:block.body[-1].end_lineno]
    unlocked = "".join(
        lines[:block.lineno - 1]
        + [ln[4:] if ln.startswith("    ") else ln for ln in body]
        + lines[block.body[-1].end_lineno:]
    )
    assert unlocked != src, "the mutation did not apply — this control is vacuous"
    ast.parse(unlocked)  # the mutant must be a SOURCE FILE, not a syntax error
    assert _sink_calls_outside_the_lock(unlocked), (
        "the detector cannot see a sink call outside the lock, so its empty "
        "list above is a fact about the walker and nothing else"
    )


class TestTheListenBacklogIsDeepEnoughForThisServersOwnConcurrency:
    """#1030. `socketserver.TCPServer.request_queue_size` defaults to **5**,
    against a server whose own suite fires 8 simultaneous appends. Overflowing
    the accept queue does not refuse cleanly: the client retries the SYN and
    then sees `ConnectionResetError: [Errno 104]`, which reads as a transport
    flake correlated with host load.

    🔴 THIS IS MEASURED AGAINST THE SOCKET, NOT THE ATTRIBUTE, and that is the
    whole point of the arm. `TCPServer.__init__` calls `server_activate()` ->
    `listen(self.request_queue_size)` before it returns, so a fix that assigns
    to the INSTANCE changes the attribute and leaves the kernel queue at 5. An
    `assert server.request_queue_size == 128` would pass for that broken fix —
    it is a claim about a name, and the defect lives in the socket. So the queue
    is FILLED and the landings are COUNTED.

    Deterministic by construction: nothing ever accepts, so there is no race to
    lose and no scheduling luck involved. MEASURED on a bare socket — backlog 5
    admits 6 of 24 (the kernel's documented backlog+1) and backlog 128 admits
    24 of 24.
    """

    # Comfortably past the default 5 and comfortably under LISTEN_BACKLOG, and
    # deliberately not a multiple of either: a bound that sits exactly on a
    # boundary cannot tell a fix from an off-by-one.
    ATTEMPTS = 24
    CONNECT_TIMEOUT = 0.4

    def _fill(self, port: int) -> int:
        """Connections that LAND while nothing is accepting."""
        landed, held = 0, []
        try:
            for _ in range(self.ATTEMPTS):
                sock = socket.socket()
                sock.settimeout(self.CONNECT_TIMEOUT)
                try:
                    sock.connect(("127.0.0.1", port))
                except OSError:
                    sock.close()
                    continue
                landed += 1
                held.append(sock)
        finally:
            for sock in held:
                sock.close()
        return landed

    def test_the_SOCKET_build_server_returns_queues_more_than_the_default_five(
        self, scoped_store: Path
    ):
        """RED at `request_queue_size = 5`: only 6 of 24 land."""
        httpd = api.build_server(
            host="127.0.0.1", port=0, store_root=str(scoped_store),
            tokens=(ZACH,), trusted_proxies=(LOOPBACK_PROXY,),
            limiter=None, audit=None,
        )
        # 🔴 `serve_forever` is NEVER started. An accepting server would drain
        # the queue and every attempt would land whatever the backlog is, which
        # is the vacuous-green version of this test.
        try:
            landed = self._fill(httpd.server_address[1])
        finally:
            httpd.server_close()
        assert landed == self.ATTEMPTS, (
            f"only {landed} of {self.ATTEMPTS} connections could be QUEUED "
            "against the server `build_server` returns, so its accept queue is "
            "shallower than this server's own concurrency. That is #1030: the "
            "excess connects retry their SYN and then surface as "
            "ConnectionResetError, which presents as a load-correlated flake "
            "rather than a refusal. Note this counts LANDINGS on the socket — "
            "if you 'fixed' it by assigning request_queue_size to the instance, "
            "the attribute moved and the socket did not, because "
            "TCPServer.__init__ has already called listen()."
        )

    def test_the_default_five_would_still_FAIL_this_probe(self):
        """The positive control for the arm above, and it is not optional.

        Without it, `landed == ATTEMPTS` is unfalsifiable: a probe that can
        never fail reports a full queue for any backlog at all, and the guard
        would stay green if `LISTEN_BACKLOG` were reverted to 5 tomorrow. This
        pins that the probe DISCRIMINATES, using the stdlib default explicitly
        rather than a number copied from the fix.
        """
        srv = socketserver.TCPServer.__new__(socketserver.TCPServer)
        sock = socket.socket()
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", 0))
            sock.listen(socketserver.TCPServer.request_queue_size)
            landed = self._fill(sock.getsockname()[1])
        finally:
            sock.close()
            del srv
        assert landed < self.ATTEMPTS, (
            f"the probe queued all {self.ATTEMPTS} connections against a socket "
            f"listening at the STDLIB DEFAULT backlog of "
            f"{socketserver.TCPServer.request_queue_size} — so it cannot "
            "distinguish a deep queue from a shallow one, and the sibling "
            "test's pass means nothing."
        )

    def test_build_server_sets_the_backlog_as_a_CLASS_attribute(
        self, scoped_store: Path
    ):
        """The mechanism, pinned separately from the effect.

        The effect test above is the one that matters, but it cannot say WHY it
        passes — a host whose kernel silently widened the queue would green it.
        This asserts the class the server is an instance of carries the value,
        which is the only assignment that reaches `server_activate()`.
        """
        httpd = api.build_server(
            host="127.0.0.1", port=0, store_root=str(scoped_store),
            tokens=(ZACH,), trusted_proxies=(LOOPBACK_PROXY,),
            limiter=None, audit=None,
        )
        try:
            assert type(httpd).request_queue_size == api.LISTEN_BACKLOG, (
                "the server's CLASS does not carry LISTEN_BACKLOG, so "
                "TCPServer.__init__ listened at whatever it inherited"
            )
            assert api.LISTEN_BACKLOG > socketserver.TCPServer.request_queue_size, (
                f"LISTEN_BACKLOG ({api.LISTEN_BACKLOG}) is not actually deeper "
                f"than the stdlib default "
                f"({socketserver.TCPServer.request_queue_size}) it exists to "
                "replace"
            )
        finally:
            httpd.server_close()


class TestAHungRoundTripSAYSWhichSideBlocked:
    """🔴 THE INSTRUMENT, NOT A FIX — and the distinction is the whole point.

    `test_a_FORGED_actor_in_the_body_is_DISCARDED` failed in CI (`devrc-ci-ddrxx`,
    revision `857fc3f5`) with a bare `TimeoutError` out of `socket.py:720` and
    nothing else. Nothing in that traceback says whether the server was blocked,
    and if so on what — which is why the investigation in
    `claudedocs/handoff-cairn-phase3.md` ran for weeks against an observable that
    every candidate mechanism produces identically.

    These tests do NOT make the flake reproduce, do NOT retry and do NOT move a
    bound. They pin that `_why_the_server_did_not_answer` can TELL THE RIVALS
    APART, because a classifier that answered `SERVER_BLOCKED_IN_FSYNC` to every
    hang would be worse than no classifier: it would end the investigation with
    a confident wrong answer.

    ⚠ LABELLED HONESTLY: these are INVARIANT GUARDS on the reporter, not
    regression coverage for the flake. The flake has never been made to
    reproduce, and no test here claims otherwise. Each arm's own evidence is the
    mutation matrix in the PR body — the arm's rule deleted from
    `_HUNG_SERVER_RULES` and the test watched to fail with its own message.
    """

    # 🔴 THE CLIENT BOUND, AND NOTHING ELSE. There is deliberately no
    # `SERVER_STALL` any more — see `_hang_and_report`. The stall is now held
    # open by an Event until the report has been taken, so its duration is
    # DECIDED by this test rather than guessed in advance, and the pair of
    # bounds that had to be "far apart" no longer has to be anything.
    CLIENT_BOUND = 0.25

    def _hang_and_report(self, store, monkeypatch, where: str) -> str:
        """Drive one POST into a server deliberately stuck at `where`.

        Returns the reporter's own text, captured by calling it at the moment
        the client gives up — the same instant `fetch` calls it in anger.
        """
        # 🔴 Every arm starts from a clean thread table. Without this, a handler
        # wedged by the PREVIOUS arm is still alive and the verdict below is
        # about that one — the exact mis-attribution these tests exist to
        # prevent, and the reason the headline reports AMBIGUOUS on disagreement.
        assert _await_no_handler_threads(HANG_TIMEOUT) == 0, (
            "a handler thread from an earlier test is still parked, so this "
            "arm's verdict would not be about this arm's request"
        )
        stalled = threading.Event()
        released = threading.Event()

        def _stall(*_a, **_k):
            stalled.set()
            # 🔴 HELD UNTIL THE REPORT HAS BEEN TAKEN, not for a fixed span.
            # The old form slept `SERVER_STALL` (1.2 s) and the caller sampled
            # `stalled.is_set()` the instant the client gave up at
            # `CLIENT_BOUND` (0.25 s) — two independent races against one
            # unsynchronised handler:
            #   (a) ARMING: within 250 ms the server had to accept, spawn a
            #       thread, parse, authenticate, meter, resolve and read before
            #       reaching this line. Idle dev host: a few ms. Contended CI
            #       node: not always — and the guard then blamed the CODE for a
            #       SCHEDULING outcome.
            #   (b) REPORTING: if the stack walk took longer than the remaining
            #       sleep, the handler unblocked mid-report and the verdict was
            #       about a server that was no longer stuck.
            # Both disappear once the two sides synchronise instead of racing:
            # the caller WAITS for this line to be reached, and this line waits
            # for the caller to be done. Bounded so a defect cannot park a
            # handler forever and wedge the drain below.
            released.wait(HANG_TIMEOUT)

        if where == "fsync":
            # 🔴 `_fsync_dir`, not `os.fsync`: patching the stdlib's `os.fsync`
            # is process-global and would stall any OTHER thread that happened
            # to fsync during this test. This module-level function is reached
            # only from `_replace_bytes`, so the blast radius is exactly the
            # request under test.
            monkeypatch.setattr(api, "_fsync_dir", _stall)
        elif where == "entry-lock":
            class _StallingLock:
                def __init__(self, _path):
                    pass

                def __enter__(self):
                    _stall()
                    return self

                def __exit__(self, *_exc):
                    return None

            monkeypatch.setattr(api, "_EntryLock", _StallingLock)
        else:                                    # pragma: no cover - typo guard
            raise AssertionError(f"unknown stall site {where!r}")

        with running(store, tokens=(ZACH,)) as (base, _audit):
            # 🔴 `fetch` DIRECTLY, not `post_bullet`. `post_bullet` forwards
            # `**payload` into the JSON BODY, so a `timeout=` passed to it is
            # silently sent to the server as a field instead of bounding the
            # client — the request then waits the full HANG_TIMEOUT, the stall
            # elapses, and the test passes while measuring nothing. Cost one
            # debugging round; do not "simplify" this back.
            with pytest.raises(TimeoutError):
                fetch(
                    bullets_url(base, ALLOW_SCOPE),
                    token=ZACH_TOKEN,
                    method="POST",
                    timeout=self.CLIENT_BOUND,
                    data=json.dumps(
                        {"text": BULLET_A, "session": SESSION_A}
                    ).encode("utf-8"),
                )
            # 🔴 WAIT FOR THE ARMING — DO NOT SAMPLE IT. `stalled.is_set()` here
            # was an instantaneous read of a race the server had no obligation
            # to have won yet, so a slow-to-schedule handler failed the guard
            # with "the server never reached the stall site" while the code was
            # fine. Waiting cannot mask a real regression: if the stall site is
            # genuinely never reached — the fix stops calling `_fsync_dir`, or
            # the request is rejected before the write path — this returns False
            # and the assertion below still fires, just on evidence instead of
            # on timing. It costs a PASSING run nothing: the event is normally
            # already set by the time the client has given up.
            armed = stalled.wait(HANG_TIMEOUT)
            try:
                assert armed, (
                    f"the server never reached the {where!r} stall site within "
                    f"{HANG_TIMEOUT:g}s, so the hang under test was NOT the one "
                    "this test set up — the report would be about some other "
                    "mechanism. This is now a WAITED verdict, not a sampled "
                    "one, so it is a claim about the SERVER rather than about "
                    "scheduling: the handler never got to the stall site at all."
                )
                return _why_the_server_did_not_answer()
            finally:
                # 🔴 RELEASE FIRST, THEN DRAIN — and in `finally`, so a failed
                # assertion above cannot leave the handler parked for the full
                # bound and strand the next arm's precondition.
                released.set()
                # Drain OUR wedged handler before handing back, so the leak
                # this arm created cannot be inherited by the next one.
                _await_no_handler_threads(HANG_TIMEOUT)

    def test_a_stall_in_the_FSYNC_region_is_NAMED(self, scoped_store, monkeypatch):
        """`_replace_bytes` fsyncs the file AND the parent directory inside the
        request, before the response is written. Both are unbounded: the
        handler's `timeout = 15` is a SOCKET timeout and does not reach a
        syscall."""
        report = self._hang_and_report(scoped_store, monkeypatch, "fsync")
        assert "MECHANISM = SERVER_BLOCKED_IN_FSYNC" in report, report
        assert "_fsync_dir" in report, (
            "the verdict was right but the stacks do not name the blocking "
            "frame, so a reader still cannot check the verdict"
        )

    def test_a_stall_on_the_ENTRY_LOCK_reads_DIFFERENTLY(
        self, scoped_store, monkeypatch
    ):
        """🔴 THE DISCRIMINATION CONTROL. A reporter that said FSYNC to every
        hang would pass the test above and be worthless. This one hangs the
        server somewhere ELSE and requires the verdict to MOVE."""
        report = self._hang_and_report(scoped_store, monkeypatch, "entry-lock")
        assert "MECHANISM = SERVER_BLOCKED_ON_ENTRY_LOCK" in report, report
        assert "SERVER_BLOCKED_IN_FSYNC" not in report, (
            "a hang that is not in fsync was reported as fsync — the verdict "
            "is a constant, not a measurement"
        )

    def test_a_request_that_is_NEVER_ACCEPTED_reads_as_NEVER_ACCEPTED(
        self, scoped_store
    ):
        """The rival family the backlog work was about: no handler thread exists
        at all, because the connection was never accepted.

        Modelled with a real parked `serve_forever` (from `running`) plus a
        second socket that is listening and never accepted — which is exactly
        the state an accept-queue overflow leaves the client in.
        """
        assert _await_no_handler_threads(HANG_TIMEOUT) == 0, (
            "a handler thread from an earlier test is still parked — this arm "
            "asserts there is NO handler, so a leftover would fail it for the "
            "wrong reason"
        )
        with running(scoped_store, tokens=(ZACH,)) as (_base, _audit):
            with socket.socket() as never:
                never.bind(("127.0.0.1", 0))
                never.listen(1)
                url = f"http://127.0.0.1:{never.getsockname()[1]}/api/v1/status"
                with pytest.raises(TimeoutError):
                    fetch(url, token=ZACH_TOKEN, timeout=self.CLIENT_BOUND)
                report = _why_the_server_did_not_answer()
        assert "MECHANISM = NEVER_ACCEPTED" in report, report
        assert "handler threads=0" in report, report

    def test_the_reporter_is_a_REPORT_and_changes_no_OUTCOME(self, scoped_store):
        """🔴 The property that makes this safe to add to a helper 200+ call
        sites share: a healthy round-trip is untouched, and a hung one still
        RAISES. `fetch` swallowing the timeout to print a nice message would be
        the suppression this investigation is explicitly forbidden to ship."""
        with running(scoped_store, tokens=(ZACH,)) as (base, _audit):
            code, headers, _b = post_bullet(
                base, ZACH_TOKEN, ALLOW_SCOPE, text=BULLET_B, session=SESSION_B,
            )
        assert code == 200, (code, headers)
        assert headers["X-Store-Status"] == "appended"


class TestTheStoreIsSitedOffTheContendedDisk:
    """The gate flake is fsync latency under disk contention; tmpfs removes it.

    🔴 These pin the SITING, which is the only thing that makes the fix real. A
    fixture that silently fell back to disk everywhere would leave the suite exactly
    as flaky while every test still passed — the change would be inert and
    indistinguishable from a working one, which is the failure mode this class
    exists to make impossible.
    """

    def test_the_fstype_is_resolved_by_LONGEST_mount_point_not_by_prefix(self):
        # /dev/shm is tmpfs while /dev is devtmpfs, and both are prefixes of a
        # path under the former. A first-match-wins scan reports devtmpfs and the
        # store then silently lands on the wrong filesystem.
        if store_siting.mount_fstype(Path("/dev")) is None:
            pytest.skip("no /proc/mounts on this platform")
        assert store_siting.mount_fstype(Path("/dev/shm")) == "tmpfs", (
            "/dev/shm must resolve to its OWN mount, not to /dev's"
        )

    def test_a_disk_path_is_NOT_reported_as_tmpfs(self):
        # The negative control. Without it, a helper hardcoded to return "tmpfs"
        # would satisfy every other assertion here.
        fstype = store_siting.mount_fstype(Path("/"))
        assert fstype is not None and fstype != "tmpfs", (
            f"root filesystem reported as {fstype!r} — if this box really does run "
            "root on tmpfs the siting logic is untestable here, not wrong"
        )

    def test_the_candidate_is_rejected_when_it_is_NOT_tmpfs(
        self, tmp_path: Path, monkeypatch
    ):
        # A directory that exists and is writable but is disk-backed must be
        # refused: the guard is the FILESYSTEM TYPE, never the path's spelling.
        # This is the case that makes `/dev/shm` being conventionally-tmpfs safe
        # to rely on — we do not rely on it.
        monkeypatch.setenv("DEVRC_TEST_TMPFS", str(tmp_path))
        got = store_siting.tmpfs_dir()
        assert got != tmp_path, (
            "a disk-backed directory was accepted as tmpfs — the type check is "
            "not doing the work its docstring claims"
        )

    def test_an_ABSENT_candidate_falls_back_rather_than_raising(
        self, tmp_path: Path, monkeypatch
    ):
        # The CI sandbox may have no usable tmpfs at all. That must degrade to
        # current behaviour, never fail the suite.
        missing = tmp_path / "definitely-not-here"
        monkeypatch.setenv("DEVRC_TEST_TMPFS", str(missing))
        got = store_siting.tmpfs_dir()
        assert got is None or store_siting.mount_fstype(got) == "tmpfs"

    def test_the_store_fixture_ACTUALLY_lands_on_tmpfs_when_one_exists(
        self, store: Path
    ):
        # 🔴 The positive control for the whole change. If `store_siting.tmpfs_dir()` finds a
        # tmpfs, the store MUST be on it — otherwise the fixture fell back and the
        # fix is inert while this file stays green.
        available = store_siting.tmpfs_dir()
        if available is None:
            pytest.skip(
                # 🔴 `tmpfs_dir()` returning None gained a SECOND cause when the
                # free-space floor landed, and this message named only the first.
                # In the environment that matters this skip is the only signal,
                # so the whole fix going inert must not read as "no tmpfs here".
                "no USABLE tmpfs: absent, not tmpfs, under _MIN_FREE_BYTES "
                "free, or unwritable. The fallback path is exercised. Check "
                "WHICH cause applies before reading this as a bare absence."
            )
        assert store_siting.mount_fstype(store) == "tmpfs", (
            f"store landed on {store_siting.mount_fstype(store)!r} while a tmpfs at "
            f"{available} was available — the fix is inert"
        )

    def test_the_store_fixture_is_still_a_correct_store_wherever_it_lands(
        self, store: Path
    ):
        # Siting must not change CONTENT. Cheap, and it is what stops the tmpfs
        # branch quietly producing a different fixture from the disk branch.
        assert (store / SCOPE / "thing-alpha.md").is_file()
        assert (store / OTHER_SCOPE / "thing-beta.md").is_file()
        assert (store / EMPTY_SCOPE).is_dir()
        assert (store / BROKEN_SCOPE / "thing-gamma.md").read_text().startswith(
            "no front matter"
        )


class TestTheSitingRULESThemselvesArePinned:
    """🔴 THE THREE FIXES OF ROUND 2 ARE PINNED HERE — NOT every guard in the module.

    An earlier header read "EVERY FIX IN THIS MODULE HAS A TEST" and that is
    FALSE: dropping the write probe, the `is_dir()` check, or the tmpfs `rmtree`
    cleanup each SURVIVES this suite (99 passed). The scoped sentence below was
    accurate; the header was what a reader took away, which is the half that
    stops anyone looking.

    Audit round 2 ran a battery over the 105 tests that touch `store_siting` and
    found `>=`→`>`, dropping the free-space floor, and dropping the `mkdtemp`
    try/except ALL survived — 105 passed each time — while a positive control
    (fstype check always true) was correctly KILLED. So the selected set could go
    red on a defect in this module; these three simply were not pinned by anything.

    They are the fixes most likely to be "simplified" back: `>=` reads like a typo
    against a docstring that says "LONGEST matching mount point", and the floor
    reads redundant beside the write probe. Each mutation lands the store back on
    the contended disk with a fully green suite — the exact defect this module
    exists to remove, arriving through the check meant to prevent it, and showing up
    in CI as an unattributable flake on somebody else's PR.
    """

    def _mounts(self, tmp_path: Path, body: str) -> str:
        table = tmp_path / "mounts"
        table.write_text(body)
        return str(table)

    def test_a_SHADOWED_mount_point_reports_the_LAST_entry_not_the_first(
        self, tmp_path: Path, monkeypatch
    ):
        # /proc/mounts is LAST-WINS: when two mounts share a path the later line is
        # the live filesystem. `depth > best[0]` kept the FIRST at equal depth, so a
        # disk bind over a tmpfs reported `tmpfs` and the store landed on disk.
        # This is the mutation that survived round 2; it dies here.
        monkeypatch.setattr(
            store_siting,
            "_MOUNTS_PATH",
            self._mounts(
                tmp_path,
                "tmpfs /dev/shm tmpfs rw 0 0\n"
                "/dev/sda1 /dev/shm ext4 rw 0 0\n",
            ),
        )
        assert store_siting.mount_fstype(Path("/dev/shm")) == "ext4", (
            "the shadowing (last) entry must win — with `>` this returns 'tmpfs' "
            "and a disk-backed bind is silently accepted as tmpfs"
        )

    def test_the_MIRROR_shadow_also_resolves_to_the_last_entry(
        self, tmp_path: Path, monkeypatch
    ):
        # The opposite order must also follow last-wins, or the rule is just a
        # coincidence that happens to favour one spelling.
        monkeypatch.setattr(
            store_siting,
            "_MOUNTS_PATH",
            self._mounts(
                tmp_path,
                "/dev/sda1 /dev/shm ext4 rw 0 0\ntmpfs /dev/shm tmpfs rw 0 0\n",
            ),
        )
        assert store_siting.mount_fstype(Path("/dev/shm")) == "tmpfs"

    def test_a_genuinely_NESTED_mount_still_resolves_to_the_deepest(
        self, tmp_path: Path, monkeypatch
    ):
        # The `>=` change must not break real nesting: distinct paths at different
        # depths still resolve to the longest match, not to whatever came last.
        monkeypatch.setattr(
            store_siting,
            "_MOUNTS_PATH",
            self._mounts(
                tmp_path,
                # 🔴 `/` LAST, deliberately. Ordered shallowest-first this
                # fixture cannot tell "longest match" from "last match" —
                # both rules give the same answer on every line, so a mutant
                # that drops the depth comparison entirely SURVIVES here. It
                # was killed only by the round-1 test that reads the real
                # /proc/mounts, where `/` happens to come after /dev/shm on
                # this host — and in the nix build sandbox the order is
                # reversed, so the depth rule was unpinned in the tier the
                # merge is gated on.
                "devtmpfs /dev devtmpfs rw 0 0\n"
                "tmpfs /dev/shm tmpfs rw 0 0\n"
                "/dev/sda1 /home ext4 rw 0 0\n"
                "/dev/sda1 / ext4 rw 0 0\n",
            ),
        )
        assert store_siting.mount_fstype(Path("/dev/shm/inner/deeper")) == "tmpfs"
        assert store_siting.mount_fstype(Path("/dev/null")) == "devtmpfs"
        assert store_siting.mount_fstype(Path("/etc/passwd")) == "ext4"

    def test_a_tmpfs_UNDER_the_free_space_floor_is_REFUSED(self, monkeypatch):
        # A five-byte probe passes on a tmpfs with five bytes free; the caller's
        # real writes then raise ENOSPC, turning a test that would have PASSED on
        # disk into an error. Driving the floor above any real filesystem is the
        # hermetic equivalent of filling one.
        if store_siting.tmpfs_dir() is None:
            pytest.skip("no usable tmpfs here, so the floor cannot be exercised")
        monkeypatch.setattr(store_siting, "_MIN_FREE_BYTES", 1 << 60)
        assert store_siting.tmpfs_dir() is None, (
            "a tmpfs with less than _MIN_FREE_BYTES free must be refused; without "
            "the floor this returns the path and the caller dies on ENOSPC"
        )

    def test_the_floor_is_not_so_high_that_it_refuses_EVERY_tmpfs(self, monkeypatch):
        # The mirror. A floor that rejects everything is indistinguishable from
        # having no tmpfs at all — the fix would go inert with the suite green.
        if store_siting.mount_fstype(Path("/dev/shm")) != "tmpfs":
            pytest.skip("no tmpfs at /dev/shm on this host")
        monkeypatch.setattr(store_siting, "_MIN_FREE_BYTES", 0)
        assert store_siting.tmpfs_dir() is not None

    def test_mkdtemp_REFUSING_falls_back_instead_of_raising(
        self, tmp_path: Path, monkeypatch
    ):
        # mkdtemp sat outside the try, so its OSError propagated rather than
        # falling back — the candidate can fill or go read-only between the probe
        # and the call. Dropping the try/except survived round 2's battery.
        if store_siting.tmpfs_dir() is None:
            pytest.skip("no usable tmpfs here, so the mkdtemp path is not taken")

        def raiser(*a, **k):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(store_siting.tempfile, "mkdtemp", raiser)
        with store_siting.store_root(tmp_path) as root:
            assert tmp_path in root.parents, (
                f"expected the tmp_path fallback, got {root} — without the "
                "try/except this raises OSError instead"
            )

    def test_the_fallback_honours_a_custom_store_NAME(self, tmp_path: Path, monkeypatch):
        # test_cairn_cli.py passes name="src"; a fallback that hardcoded "store"
        # would hand it the wrong directory only on hosts without a tmpfs.
        def raiser(*a, **k):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(store_siting.tempfile, "mkdtemp", raiser)
        with store_siting.store_root(tmp_path, "src") as root:
            assert root.name == "src"

    def test_scoped_store_is_ALSO_sited_off_the_disk(self, scoped_store: Path):
        # The fixture round 2 fixed had no positive control of its own — only the
        # ratchet, and only for one spelling. It feeds ~110 `running(...)` sites,
        # i.e. most of the in-request-fsync population in this file.
        available = store_siting.tmpfs_dir()
        if available is None:
            pytest.skip(
                # 🔴 `tmpfs_dir()` returning None gained a SECOND cause when the
                # free-space floor landed, and this message named only the first.
                # In the environment that matters this skip is the only signal,
                # so the whole fix going inert must not read as "no tmpfs here".
                "no USABLE tmpfs: absent, not tmpfs, under _MIN_FREE_BYTES "
                "free, or unwritable. The fallback path is exercised. Check "
                "WHICH cause applies before reading this as a bare absence."
            )
        assert store_siting.mount_fstype(scoped_store) == "tmpfs", (
            f"scoped_store landed on {store_siting.mount_fstype(scoped_store)!r} "
            f"while a tmpfs at {available} was available"
        )

    def test_the_floor_CONSTANT_clears_the_largest_store_this_suite_builds(self):
        """🔴 ASSERTS THE CONSTANT, NOT A CALL — because the call-based guards CANNOT
        see a bad value, and a bad value shipped.

        `_MIN_FREE_BYTES` was lowered to 1 MiB for one commit while the largest store
        this suite builds page-allocates 1.199 MiB, opening an ENOSPC window at
        1 MiB <= free < ~1.3 MiB. The whole battery stayed green, because:

          * `test_the_floor_is_not_so_high...` monkeypatches `_MIN_FREE_BYTES` to 0
            before measuring — it overrides the exact variable it claims to bound, so
            no value of that variable can ever fail it;
          * `test_a_tmpfs_UNDER_the_free_space_floor_is_REFUSED` skips when
            `tmpfs_dir()` is None, i.e. its assertion is unreachable precisely when
            the fix has gone inert.

        Measured: setting the floor to `1 << 60` (every store falls back to the
        contended disk, the fix fully inert) gave 95 passed, 4 skipped, rc 0.

        This test takes no fixture, monkeypatches nothing and cannot skip, so it holds
        in the sandbox tier the merge is gated on.
        """
        assert store_siting._MIN_FREE_BYTES > store_siting._LARGEST_STORE_BYTES, (
            f"_MIN_FREE_BYTES ({store_siting._MIN_FREE_BYTES:,}) must exceed the "
            f"largest store this suite builds ({store_siting._LARGEST_STORE_BYTES:,} "
            "page-allocated bytes) or a mount that passes the check can still run out "
            "mid-test. Apparent file sizes understate tmpfs cost ~23x — measure "
            "page allocation, not st_size."
        )
        # And an upper bound, because too HIGH silently disables the whole module.
        # Generous: this only has to catch an order-of-magnitude mistake, not tune it.
        assert store_siting._MIN_FREE_BYTES <= 64 * 1024 * 1024, (
            f"_MIN_FREE_BYTES ({store_siting._MIN_FREE_BYTES:,}) exceeds a container's "
            "default 64Mi /dev/shm, so every store would fall back to disk and this "
            "module would be inert with the suite green"
        )

    def test_the_string_literal_half_of_the_membership_scan_is_LOAD_BEARING(
        self, tmp_path: Path
    ):
        """A store server stood up inside a `sys.executable -c` script.

        The AST scan cannot see a call inside a string, and seven test files here
        already drive that shape. Disabling the Constant half left the ledger suite
        at 99 passed — the widening reverted green, so nothing pinned it.
        """
        import test_store_siting_ledger as led

        probe = tmp_path / "test_probe_subprocess_server.py"
        probe.write_text(
            "import textwrap\n"
            'SCRIPT = textwrap.dedent("""\n'
            "    build_server(store_root=arg)\n"
            '""")\n'
        )
        assert led._calls_build_server(probe), (
            "a build_server( call inside a string literal must count as standing up "
            "the server — otherwise a subprocess-driven test is silently dropped from "
            "the ledger, which is worse than the comment false positive this replaced"
        )

    def test_a_hash_COMMENT_mentioning_build_server_is_still_not_a_call(
        self, tmp_path: Path
    ):
        """The mirror. Widening for strings must not undo the false-positive fix."""
        import test_store_siting_ledger as led

        probe = tmp_path / "test_probe_comment_only.py"
        probe.write_text(
            "# this file deliberately never calls build_server(...) itself\n"
            "def test_x():\n    assert True\n"
        )
        assert not led._calls_build_server(probe)
