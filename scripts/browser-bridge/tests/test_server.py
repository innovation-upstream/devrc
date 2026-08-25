"""Tests for the browser-bridge rendezvous server.

Fully HEADLESS: no Brave, no network beyond loopback. The extension is
simulated in-process by a `FakeExtension` thread that long-polls `/poll` (with
its instance identity headers), "executes" the op (echo), and POSTs to
`/result` (echoing its instanceId) — exercising the real HTTP round-trip, the
request↔reply id correlation, AND the multi-instance registry/routing.

Run: nix-shell -p python312Packages.pytest --run "pytest scripts/browser-bridge/tests"
"""
import base64
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server as S  # noqa: E402

TOKEN = "test-token-abc123"
EXT_DIR = Path(__file__).resolve().parent.parent / "extension"

# Capture the REAL i3_available at import (the _disable_i3 autouse fixture stubs
# the module attribute to False for hermeticity; this reference lets the
# gating-logic test still exercise the genuine implementation).
_REAL_I3_AVAILABLE = S.i3_available

# A manifest version pinned for the staleness tests. `manifest_version()` prefers
# the DEPLOYED extension at ~/.local/share/browser-bridge-ext/, so any test that
# asserts on a stale/fresh verdict must pin it — otherwise the suite's result
# depends on the operator's live deploy state (whether they have switched, and to
# which build). Deliberately not a real version so a leak is obvious.
PINNED_VERSION = "9.9.9-pinned"


@pytest.fixture
def pinned_manifest(tmp_path, monkeypatch):
    """Pin manifest_version() to PINNED_VERSION, hermetically: a tmp deployed
    manifest + a non-existent repo fallback. Never reads host state."""
    deployed = tmp_path / "pinned-manifest.json"
    deployed.write_text(json.dumps({"version": PINNED_VERSION}),
                        encoding="utf-8")
    monkeypatch.setattr(S, "_DEPLOYED_EXT_MANIFEST", deployed)
    monkeypatch.setattr(S, "_EXT_MANIFEST_PATH", tmp_path / "no-repo-manifest.json")
    return PINNED_VERSION


# The build marker the staleness tests pin. Same hermeticity argument as
# PINNED_VERSION: build_marker() prefers the DEPLOYED extension tree, so a test
# that asserts a verdict must pin it or it reads the operator's live deploy
# state. Deliberately not a real marker so a leak is obvious.
PINNED_BUILD = "deadbeefcafef00d"


@pytest.fixture
def pinned_build(tmp_path, monkeypatch):
    """Pin build_marker() to PINNED_BUILD, hermetically: a tmp deployed
    build_id.js + a non-existent repo fallback. Never reads host state."""
    deployed = tmp_path / "pinned-build_id.js"
    deployed.write_text(f'export const BUILD_MARKER = "{PINNED_BUILD}";\n',
                        encoding="utf-8")
    monkeypatch.setattr(S, "_DEPLOYED_EXT_BUILD_ID", deployed)
    monkeypatch.setattr(S, "_EXT_BUILD_ID_PATH", tmp_path / "no-repo-build_id.js")
    return PINNED_BUILD

# The in-repo activity spool emitter (single source of truth for the v1 line
# format). scripts/browser-bridge/tests/ -> parent.parent.parent == scripts/.
SPOOL_EMIT_PY = (Path(__file__).resolve().parent.parent.parent
                 / "collector" / "keylog" / "spool_emit.py")


# --------------------------------------------------------------------------- #
# Telemetry (browser-bridge -> activity spool) test scaffolding
# --------------------------------------------------------------------------- #
def _parse_spool_line(line: str) -> dict:
    """Decode ONE v1 spool line into a field dict (base64 keys decoded)."""
    parts = line.rstrip("\n").split("\t")
    assert parts and parts[0] == "v1", f"not a v1 line: {line!r}"
    out = {}
    for kv in parts[1:]:
        key, _, val = kv.partition("=")
        if key.startswith("b64:"):
            out[key[4:]] = base64.b64decode(val).decode("utf-8")
        else:
            out[key] = val
    return out


def _log_file(spool_dir) -> Path:
    return Path(spool_dir) / "current.log"


def _read_events(spool_dir) -> list:
    f = _log_file(spool_dir)
    if not f.exists():
        return []
    return [_parse_spool_line(ln) for ln in f.read_text().splitlines()
            if ln.strip()]


def _wait_events(spool_dir, n=1, timeout=10.0, until=None) -> list:
    """Poll the spool until `until(evs)` holds, or >=n events have landed.

    The emit runs off the critical path, after the HTTP response, so it lands
    slightly after /cmd returns — hence the poll rather than a bare read.

    🔴 THE FLAKE THIS WAS BUILT FOR WAS NOT A WAIT PROBLEM AT ALL — DO NOT REACH
    FOR THE DEADLINE. The throttling test that failed on 3 of the 29 `devrc-ci`
    runs after 2026-08-24 was losing its `throttled` row to a DATA RACE in
    server.py's lazy emitter load: two /cmd handler threads reached
    `_load_spool_emit` together, the flag was published before the module, and
    the loser was handed None and dropped its event. DROPPED, not delayed — no
    deadline could ever have recovered it, and raising this one 3s -> 10s did
    not. Fixed at the root (`_spool_emit_lock`, and the ordering the flag is
    published in); pinned by
    `test_a_second_command_emitting_during_the_emitter_load_still_spools`.

    So `until=` and the loud timeout below are still worth having, but for the
    ORDINARY reason — a count is a proxy for "the event I want landed", and the
    proxy is only exact when any N will do.

    DERIVED BY AST OVER THIS FILE, not by grep, and the difference was a real
    error: a line-oriented regex for the count reads the POSITIONAL form and
    misses the KEYWORD one, so `_wait_events(tmp_path, n=2, …)` was silently
    filed under n=1 while its sibling on the next line (`until=lambda evs:
    False`) was filed under `until=` — one function, one test, two buckets, and
    a total that still added to 56. Rule used, stated because it is a judgement
    call: bucket by what the call ASKS FOR, and **`_wait_events`' own control
    tests are classified like every other site — no self-test exemption**, since
    exempting one of a pair and not the other is what produced the wrong number.

        53 total  =  39 n=1  +  5 until=  +  9 n>=2   (+ 7 op-selected)

    Re-derived by the same AST rule after #807 merged, because the merge
    moved every bucket and a stale count is what the rule above exists to
    prevent. The 7 op-selected calls are `_wait_ops`/`_wait_payload`.

    n=1 after a single command is exact, not a proxy. Of the 9 n>=2, one is this
    harness's own negative control below (it asserts the timeout fires; no real
    events are involved). All 8 remaining real waits are order-safe — either
    they wait for the earlier event before issuing the next request, so file
    order is pinned structurally, or they assert something order-independent.

    (Both numbers moved by one: the single order-DEPENDENT site this paragraph
    used to name — the `absent, empty = _wait_events(spool_dir, 2)` unpack — is
    the one #807 migrated, so it is no longer an n>=2 `_wait_events` call and no
    longer the exception. Re-checked, not assumed.)

    Pass `until=` whenever you are waiting for a SPECIFIC event.

    🔴 AND THERE IS A POSITIONAL HALF, added by #807: even when the row you want
    DOES land, `[i]` assumes every row in the spool is yours. It is not —
    `ACTIVITY_SPOOL_DIR` is process-global and re-pointed per test, so a thread
    still alive from an EARLIER test emits into the CURRENT test's spool. Seen in
    CI as `assert 'getHtml' == 'frames'` and `assert 'getHtml' == 'type'`. Use
    `_wait_ops` / `_wait_payload` below to select by op rather than by position.
    The `absent, empty` unpack named above as the order-dependent exception is
    now `_wait_ops(spool_dir, "tabs", 2)`, which keeps the order and drops
    foreign rows.

    🔴 AND A TIMEOUT IS NOW LOUD. This used to return a SHORT list silently, so
    every caller's next line — `[0]`, or a filter — failed with a message about
    the assertion rather than about the wait. Nothing in this module treats a
    short read as valid (checked before changing it), so a miss is always a bug
    or a race, and it now says which.

    The deadline is 10s, up from 3s: CI is far slower than a workstation (that
    suite ran 244s there against ~35s locally), and 3s was tuned on the fast
    machine. It is a ceiling, not a sleep — a passing wait still returns as soon
    as the condition holds.
    """
    deadline = time.time() + timeout
    evs = []
    while time.time() < deadline:
        evs = _read_events(spool_dir)
        if until(evs) if until else len(evs) >= n:
            return evs
        time.sleep(0.02)
    evs = _read_events(spool_dir)
    if until is not None:
        assert until(evs), (
            f"spool never satisfied the wait condition in {timeout}s; "
            f"got {len(evs)} event(s): {evs}"
        )
    else:
        assert len(evs) >= n, (
            f"spool never reached {n} event(s) in {timeout}s; "
            f"got {len(evs)}: {evs}"
        )
    return evs


def _payload_op(e) -> str | None:
    """The `op` inside a spooled event's payload, or None if it has no readable
    one.

    Total by construction — a row written by something other than the bridge
    must not raise here, because the whole point of the helpers below is to walk
    PAST such a row rather than trip on it.

    🔴 `AttributeError` IS IN THE TUPLE ON PURPOSE, and the #807 audit is why:
    an earlier version claimed to be total and was not. A payload that is valid
    JSON but not an OBJECT — `null`, `123`, `"str"`, `[1,2]` — decodes fine and
    then has no `.get`, so all four raised. Only malformed JSON, a missing
    `payload` key and a `None` payload were actually covered. A non-bridge
    writer emitting a scalar payload is exactly the named case, so the claim and
    the code disagreed precisely where it mattered.
    """
    try:
        return json.loads(e["payload"]).get("op")
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _wait_ops(spool_dir, op, n=1, **kw) -> list:
    """The first `n` spooled events whose payload op is `op`, waiting for them.

    🔴 USE THIS INSTEAD OF `_wait_events(spool_dir, n)[i]` WHENEVER YOU WANT A
    SPECIFIC OP — indexing by POSITION assumes every row in the spool is yours,
    and that assumption is false.

    MEASURED 2026-08-24. `ACTIVITY_SPOOL_DIR` is a process-global env var that
    `conftest._isolate_activity_spool` re-points per test with
    `monkeypatch.setenv`. A thread still alive from an EARLIER test therefore
    emits into the CURRENT test's spool — so `[0]` is whichever row landed
    first, not whichever row this test caused. Seen in CI as
    `assert 'getHtml' == 'frames'` and `assert 'getHtml' == 'type'`. The visible
    artifact was a pair of `{"event":"cmd_timeout","op":"getHtml"}` lines in the
    failing test's captured STDERR — those are the server's structured log, NOT
    spool rows; do not grep the spool for that string. The corresponding spool
    payload reads `{"op":"getHtml", …, "outcome":"timeout"}`. The stderr is
    still good evidence because capture is per-test-phase, so a neighbour's
    timeout demonstrably fired inside the failing test's window. Neither PR could reach browser-bridge
    — one changed `scripts/run-tests.sh`, the other changed only a `.md`.

    This is the positional sibling of the COUNT problem `_wait_events`'s own
    docstring describes: that one waits for the wrong NUMBER, this one reads the
    wrong ROW. `until=` fixes both, and this wraps the idiom so each call site
    does not re-derive it.

    🔴 WHAT THIS DOES **NOT** COVER — it narrows the class, it does not close it.
    Discrimination is on `op` ALONE, so a neighbour emitting the SAME op is
    still selected. The exposed shape is a caller asking for N rows of a common
    op where their ORDER carries the signal — `_wait_ops(…, "tabs", 2)` below is
    the one such site. Measured for the #807 audit: no current test leaves a
    `tabs` command in flight to time out (the two candidates call
    `registry.submit` directly and never reach `emit_cmd_event`), so today's
    residual is far smaller than the `getHtml`-timeout shape that caused the
    incident. If that ever changes, the rows already carry `session` and a
    payload `sess_src`, which would discriminate further.
    """
    def _seen(evs):
        return len([e for e in evs if _payload_op(e) == op]) >= n
    evs = _wait_events(spool_dir, until=_seen, **kw)
    return [e for e in evs if _payload_op(e) == op][:n]


def _wait_payload(spool_dir, op, **kw) -> dict:
    """The decoded payload of the first spooled event whose op is `op`."""
    return json.loads(_wait_ops(spool_dir, op, 1, **kw)[0]["payload"])


def test_a_neighbours_late_row_does_not_become_this_tests_event(telemetry):
    """🔴 REGRESSION for the CI flake that blocked two unrelated PRs.

    `ACTIVITY_SPOOL_DIR` is a process-global env var re-pointed per test, so a
    thread still alive from an EARLIER test emits into THIS test's spool. Then
    `_wait_events(spool_dir, 1)[0]` returns the neighbour's row and the test
    asserts against someone else's op.

    Observed twice in CI, on diffs that cannot reach browser-bridge:
    `assert 'getHtml' == 'frames'` (#773, a change to `scripts/run-tests.sh`)
    and `assert 'getHtml' == 'type'` (#770, a change to one `.md` file).

    The foreign row is planted directly rather than raced into place: the defect
    is "a row this test did not cause is sitting in the spool", and how it got
    there is the neighbour's business. Planting it makes the test deterministic
    instead of load-dependent — the whole complaint about the original.
    """
    spool_dir = telemetry
    spool_dir.mkdir(parents=True, exist_ok=True)
    # A neighbour's late `cmd_timeout`, written as a v1 spool line. The format
    # lives in `_parse_spool_line` above. Drift breaks this LOUDLY either way,
    # but be precise about which guard catches what: a VERSION-TAG change trips
    # that reader's `v1` assert, while a same-version KEY RENAME is caught by
    # this test's own control below (verified by simulating both).
    foreign_payload = json.dumps({"op": "getHtml", "outcome": "timeout"})
    _log_file(spool_dir).write_text("\t".join([
        "v1", "source=browser-bridge", "kind=cmd",
        "b64:payload=" + base64.b64encode(foreign_payload.encode()).decode(),
    ]) + "\n")

    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {"url": "https://civitai.com/",
                                                 "frames": []})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _req(srv, "POST", "/cmd", {"op": "frames"})[0] == 200

        # THE FIX: selected by op, so the neighbour is walked past.
        assert _wait_payload(spool_dir, "frames")["op"] == "frames"

        # 🔴 THE CONTROL, in the same test so it cannot rot separately: the OLD
        # idiom really does pick the wrong row here. Without this the assertion
        # above would pass just as happily if the spool held only our own event,
        # and the test would be pinning nothing.
        evs = _wait_events(spool_dir, 1)
        assert _payload_op(evs[0]) == "getHtml", (
            "the planted neighbour row is not at position 0, so this test is "
            "not reproducing the flake it claims to cover")
        assert len(evs) >= 2, (
            "this test's own event never landed — the control is measuring an "
            "empty spool, not a contested one")
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_wait_events_reports_a_timeout_instead_of_returning_short(tmp_path):
    """🔴 NEGATIVE CONTROL on the harness. `_wait_events` used to return a SHORT
    list silently on timeout, so the caller's next line failed with a message
    about its own assertion rather than about the wait — which is how a CI race
    read as `assert 0 == 1` in a rate-limiting test.

    Uses a tiny timeout so the control costs no wall clock."""
    with pytest.raises(AssertionError, match=r"never reached 2 event\(s\)"):
        _wait_events(tmp_path, n=2, timeout=0.05)

    with pytest.raises(AssertionError, match=r"never satisfied the wait condition"):
        _wait_events(tmp_path, timeout=0.05, until=lambda evs: False)


def test_wait_events_returns_as_soon_as_the_condition_holds(tmp_path):
    """POSITIVE CONTROL: the raised deadline is a CEILING, not a sleep. Without
    this, `timeout=10.0` could be silently turning every wait into a 10s pause
    and the suite would still be green — just 50x slower."""
    started = time.time()
    got = _wait_events(tmp_path, timeout=30.0, until=lambda evs: True)
    assert got == []
    assert time.time() - started < 1.0, "the wait slept instead of returning early"


# NOTE: `_isolate_activity_spool` — the autouse fixture that points
# ACTIVITY_SPOOL_DIR at a per-test tmp dir — now lives in `conftest.py`, one
# directory-wide definition instead of this module-scoped copy. It was scoped to
# this file only while its docstring claimed it covered EVERY test, so the other
# eight modules in this directory ran unisolated (one of them, test_site_notes,
# genuinely writing rows into the production activity pipeline).
# `telemetry` below depends on it having run — it returns the very path that
# fixture pointed ACTIVITY_SPOOL_DIR at. `_disable_i3` does not; it is unrelated.


@pytest.fixture(autouse=True)
def _disable_i3(monkeypatch):
    """HERMETIC by default: neutralise host-side i3 foregrounding so no test
    accidentally fires a real `i3-msg` (the suite runs on Zach's graphical
    workbench where DISPLAY is set + i3-msg is on PATH). Tests that exercise the
    i3 path re-enable it explicitly (monkeypatch S.i3_available → True + a fake
    subprocess.run). With it False, `activate` reports i3:"skipped"."""
    monkeypatch.setattr(S, "i3_available", lambda: False)


@pytest.fixture
def telemetry(tmp_path, monkeypatch):
    """Pin the bridge telemetry emitter at the in-repo spool_emit (hermetic — no
    dependency on the host's ~/workspace checkout) and return the temp spool dir
    that `_isolate_activity_spool` pointed ACTIVITY_SPOOL_DIR at."""
    monkeypatch.setattr(S, "_SPOOL_EMIT_PATH", SPOOL_EMIT_PY)
    monkeypatch.setattr(S, "_spool_emit_mod", None)
    monkeypatch.setattr(S, "_spool_emit_tried", False)
    return tmp_path / "activity-spool"


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def _req(srv, method, path, body=None, token=TOKEN, host="127.0.0.1",
         headers=None):
    port = srv.server_address[1]
    hdrs = {}
    if token is not None:
        hdrs["Authorization"] = f"Bearer {token}"
    if host is not None:
        hdrs["Host"] = host
    if headers:
        hdrs.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, headers=hdrs,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw else None)


def _serve(cmd_timeout=5.0, poll_timeout=5.0, registry=None, ping_timeout=None):
    registry = registry if registry is not None else S.Registry()
    handler = S.make_handler(registry, TOKEN, cmd_timeout, poll_timeout,
                             ping_timeout)
    srv = S.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, registry


class FakeExtension(threading.Thread):
    """Simulated extension: long-polls /poll (with its instance identity) and
    echoes the op back via /result (echoing its instanceId)."""

    def __init__(self, srv, instance_id="fake-1", label="", executor=None,
                 swallow=False, active_url=None, active_title=None,
                 ext_version=None, ext_id=None, ext_build=None):
        super().__init__(daemon=True)
        self.srv = srv
        self.instance_id = instance_id
        self.label = label
        self.executor = executor or (lambda cmd: {"echo": cmd.get("op")})
        self.swallow = swallow  # pick up a command but never answer (→ timeout)
        self.active_url = active_url
        self.active_title = active_title
        self.ext_version = ext_version  # reported via X-Bridge-Ext-Version (or None)
        self.ext_id = ext_id            # reported via X-Bridge-Ext-Id (or None)
        self.ext_build = ext_build      # reported via X-Bridge-Ext-Build (or None)
        self.dispatched = []    # every command this fake picked up (for assertions)
        self._stopev = threading.Event()

    def _poll_headers(self):
        h = {S.HDR_INSTANCE_ID: self.instance_id}
        if self.label:
            h[S.HDR_LABEL] = urllib.parse.quote(self.label)
        if self.active_url:
            h[S.HDR_ACTIVE_URL] = urllib.parse.quote(self.active_url)
        if self.active_title:
            h[S.HDR_ACTIVE_TITLE] = urllib.parse.quote(self.active_title)
        if self.ext_version:
            h[S.HDR_EXT_VERSION] = urllib.parse.quote(self.ext_version)
        if self.ext_id:
            h[S.HDR_EXT_ID] = urllib.parse.quote(self.ext_id)
        if self.ext_build:
            h[S.HDR_EXT_BUILD] = urllib.parse.quote(self.ext_build)
        return h

    def run(self):
        while not self._stopev.is_set():
            try:
                status, cmd = _req(self.srv, "GET", "/poll",
                                   headers=self._poll_headers())
            except Exception:
                if self._stopev.is_set():
                    return
                continue
            if status == 204 or cmd is None:
                continue
            self.dispatched.append(cmd)
            if self.swallow:
                continue
            data = self.executor(cmd)
            # An executor may model an op-level FAILURE (the op threw in the page
            # / the target tab is gone) by returning {"__error__": "<msg>"} — the
            # fake then emits the extension's ok:false error envelope instead of a
            # success envelope. A plain dict stays a success `data` payload.
            if isinstance(data, dict) and "__error__" in data:
                envelope = {"id": cmd["id"], "ok": False,
                            "error": data["__error__"],
                            "instanceId": self.instance_id}
            else:
                envelope = {"id": cmd["id"], "ok": True, "data": data,
                            "instanceId": self.instance_id}
            try:
                _req(self.srv, "POST", "/result", envelope)
            except Exception:
                pass

    def stop(self):
        self._stopev.set()


# 🔴 _wait_connected POLLS /instances, NOT /health — and that is load-bearing.
#
# /health now emits ONE telemetry event per call (the orientation ops used to be
# invisible in activity.events; see _emit_diag_event in server.py). This helper
# calls it in a tight loop, so polling /health here injected 1..N spurious events
# into the spool of EVERY test that waits for a connection — which is most of the
# telemetry suite, and it broke 14 of them by making `len(evs) == 1` false.
#
# The fix is to make the HARNESS silent rather than to loosen those assertions:
# /instances reports the same live-instance snapshot (`count` is len() of exactly
# the list /health's `extension_connected` is the bool() of) and deliberately does
# NOT emit — it is not an operator-facing orientation op. Keep it that way; if you
# point this back at /health, the exact-count telemetry assertions go red again.
#
# `body["count"]`, NOT `body.get("count", 0)`: a missing key must RAISE, not read
# as "nothing connected". With a default, a shape change on /instances turns
# `want=False` into an instantly vacuous pass — the helper would report success
# without ever observing the server. The old /health helper used `body[...]` for
# exactly this reason; the move to /instances must not quietly weaken it.
def _wait_connected(srv, want=True, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, body = _req(srv, "GET", "/instances")
        if status == 200 and bool(body["count"]) == want:
            return True
        time.sleep(0.02)
    return False


# The SILENT (no-telemetry) counterpart of _wait_instances, for tests that only
# need "N instances are up" and must not have /health events in their spool.
def _wait_count(srv, n, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, body = _req(srv, "GET", "/instances")
        if status == 200 and body["count"] >= n:   # see _wait_connected on `[...]`
            return body
        time.sleep(0.02)
    return None


# _wait_instances stays on /health BECAUSE its callers read health-only fields
# (`extension_connected`, `extension_version_current`, per-instance
# `extension_stale`), which /instances does not compute. It therefore DOES emit —
# so a telemetry test must use _wait_connected, or count only the ops it cares
# about.
def _wait_instances(srv, n, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, body = _req(srv, "GET", "/health")
        if status == 200 and body.get("count", 0) >= n:
            return body
        time.sleep(0.02)
    return None


# --------------------------------------------------------------------------- #
# Token generation + perms
# --------------------------------------------------------------------------- #
def test_token_created_with_0600(tmp_path):
    p = tmp_path / "sub" / "token"
    tok = S.load_or_create_token(p)
    assert tok and len(tok) >= 32
    mode = stat.S_IMODE(os.stat(p).st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_token_stable_across_calls(tmp_path):
    p = tmp_path / "token"
    a = S.load_or_create_token(p)
    b = S.load_or_create_token(p)
    assert a == b


def test_empty_token_file_regenerated(tmp_path):
    p = tmp_path / "token"
    p.write_text("   \n")
    tok = S.load_or_create_token(p)
    assert tok.strip()
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


# --------------------------------------------------------------------------- #
# Auth + host allowlist
# --------------------------------------------------------------------------- #
def test_missing_token_401():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "GET", "/health", token=None)
        assert status == 401
        assert body["error"] == "unauthorized"
    finally:
        srv.shutdown(); srv.server_close()


def test_wrong_token_401():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "GET", "/health", token="nope")
        assert status == 401
    finally:
        srv.shutdown(); srv.server_close()


def test_bad_host_403():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "GET", "/health", host="evil.example.com")
        assert status == 403
        assert body["error"] == "bad_host"
    finally:
        srv.shutdown(); srv.server_close()


def test_host_with_port_allowed():
    srv, _ = _serve()
    try:
        status, _ = _req(srv, "GET", "/health", host="localhost:8788")
        assert status == 200
    finally:
        srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Command-delivery channel auth: /poll (extension long-poll) and /result must
# enforce the SAME host + bearer gate as /health and /cmd — INCLUDING when the
# new instance-scoped params are present. These are the two endpoints that
# actually move commands, so they get direct coverage. The guard runs before any
# polling/body work, so a rejected request returns immediately.
# --------------------------------------------------------------------------- #
def _poll_hdrs(instance_id="fake-1", label=""):
    h = {S.HDR_INSTANCE_ID: instance_id}
    if label:
        h[S.HDR_LABEL] = urllib.parse.quote(label)
    return h


def test_poll_missing_token_401():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "GET", "/poll", token=None,
                            headers=_poll_hdrs())
        assert status == 401
        assert body["error"] == "unauthorized"
    finally:
        srv.shutdown(); srv.server_close()


def test_poll_wrong_token_401():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "GET", "/poll", token="nope",
                            headers=_poll_hdrs())
        assert status == 401
        assert body["error"] == "unauthorized"
    finally:
        srv.shutdown(); srv.server_close()


def test_poll_bad_host_403():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "GET", "/poll", host="evil.example.com",
                            headers=_poll_hdrs())
        assert status == 403
        assert body["error"] == "bad_host"
    finally:
        srv.shutdown(); srv.server_close()


def test_result_missing_token_401():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "POST", "/result",
                            {"id": "x", "ok": True, "data": {},
                             "instanceId": "fake-1"}, token=None)
        assert status == 401
        assert body["error"] == "unauthorized"
    finally:
        srv.shutdown(); srv.server_close()


def test_result_wrong_token_401():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "POST", "/result",
                            {"id": "x", "ok": True, "data": {},
                             "instanceId": "fake-1"}, token="nope")
        assert status == 401
        assert body["error"] == "unauthorized"
    finally:
        srv.shutdown(); srv.server_close()


def test_result_bad_host_403():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "POST", "/result",
                            {"id": "x", "ok": True, "data": {},
                             "instanceId": "fake-1"}, host="evil.example.com")
        assert status == 403
        assert body["error"] == "bad_host"
    finally:
        srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# /health connection state (now per-instance)
# --------------------------------------------------------------------------- #
def test_health_no_extension():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "GET", "/health")
        assert status == 200
        assert body["ok"] is True
        assert body["extension_connected"] is False
        assert body["count"] == 0
        assert body["instances"] == []
    finally:
        srv.shutdown(); srv.server_close()


def test_health_reflects_connected_extension():
    srv, _ = _serve(poll_timeout=5.0)
    ext = FakeExtension(srv, instance_id="a", label="alpha")
    ext.start()
    try:
        body = _wait_instances(srv, 1)
        assert body is not None, "extension never showed connected"
        assert body["extension_connected"] is True
        assert body["count"] == 1
        keys = {i["key"] for i in body["instances"]}
        assert "alpha" in keys
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_health_per_instance_two():
    srv, _ = _serve(poll_timeout=5.0)
    a = FakeExtension(srv, instance_id="a", label="alpha",
                      active_url="https://a.test", active_title="A")
    b = FakeExtension(srv, instance_id="b", label="beta")
    a.start(); b.start()
    try:
        body = _wait_instances(srv, 2)
        assert body is not None
        assert body["count"] == 2
        by_key = {i["key"]: i for i in body["instances"]}
        assert set(by_key) == {"alpha", "beta"}
        assert by_key["alpha"]["instanceId"] == "a"
        assert by_key["alpha"]["activeTab"]["url"] == "https://a.test"
        assert by_key["beta"]["label"] == "beta"
    finally:
        a.stop(); b.stop(); srv.shutdown(); srv.server_close()


def test_instances_endpoint():
    srv, _ = _serve(poll_timeout=5.0)
    a = FakeExtension(srv, instance_id="a", label="alpha")
    a.start()
    try:
        assert _wait_instances(srv, 1) is not None
        status, body = _req(srv, "GET", "/instances")
        assert status == 200
        assert body["count"] == 1
        assert body["instances"][0]["key"] == "alpha"
    finally:
        a.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# /cmd round-trip (single instance, back-compat: no target needed)
# --------------------------------------------------------------------------- #
def test_cmd_roundtrip_getHtml():
    srv, _ = _serve()

    def executor(cmd):
        return {"html": "<html><body>hi</body></html>", "url": "https://x.test"}

    ext = FakeExtension(srv, executor=executor)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        status, body = _req(srv, "POST", "/cmd", {"op": "getHtml"})
        assert status == 200
        assert body["ok"] is True
        assert body["result"]["data"]["html"] == "<html><body>hi</body></html>"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_cmd_single_instance_no_target_ok():
    """Exactly one connected instance → a command with NO target routes to it."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=lambda c: {"who": "only"})
    ext.start()
    try:
        assert _wait_instances(srv, 1) is not None
        status, body = _req(srv, "POST", "/cmd", {"op": "tabs"})
        assert status == 200
        assert body["result"]["data"]["who"] == "only"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_cmd_nav_requires_url():
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        status, body = _req(srv, "POST", "/cmd", {"op": "nav"})
        assert status == 400
        assert body["error"] == "missing_field:url"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_cmd_unknown_op_400():
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        status, body = _req(srv, "POST", "/cmd", {"op": "rm_rf"})
        assert status == 400
        assert body["error"] == "unknown_op"
        assert body["op"] == "rm_rf"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_cmd_no_extension_503():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "POST", "/cmd", {"op": "getHtml"})
        assert status == 503
        assert body["error"] == "extension_not_connected"
    finally:
        srv.shutdown(); srv.server_close()


def test_cmd_timeout_504():
    # Extension connects + picks up the command but never answers → 504.
    srv, _ = _serve(cmd_timeout=0.5, poll_timeout=5.0)
    ext = FakeExtension(srv, swallow=True)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        status, body = _req(srv, "POST", "/cmd", {"op": "getHtml"})
        assert status == 504
        assert body["error"] == "timeout"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_bad_json_body_400():
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        port = srv.server_address[1]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/cmd", data=b"not-json{{",
            headers={"Authorization": f"Bearer {TOKEN}", "Host": "127.0.0.1",
                     "Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "expected 400"
        except urllib.error.HTTPError as e:
            assert e.code == 400
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_result_unknown_id_reported():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "POST", "/result",
                            {"id": "deadbeef", "ok": True, "data": {}})
        assert status == 200
        assert body["ok"] is False
        assert body["error"] == "unknown_id"
    finally:
        srv.shutdown(); srv.server_close()


def test_unknown_path_404():
    srv, _ = _serve()
    try:
        status, _ = _req(srv, "GET", "/nope")
        assert status == 404
    finally:
        srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Multi-instance routing + targeting (over real HTTP)
# --------------------------------------------------------------------------- #
def test_cmd_routes_to_target_instance():
    """With two instances connected, --instance/target routes to the named one."""
    srv, _ = _serve(poll_timeout=5.0)
    a = FakeExtension(srv, instance_id="a", label="alpha",
                      executor=lambda c: {"who": "alpha"})
    b = FakeExtension(srv, instance_id="b", label="beta",
                      executor=lambda c: {"who": "beta"})
    a.start(); b.start()
    try:
        assert _wait_instances(srv, 2) is not None
        st, body = _req(srv, "POST", "/cmd", {"op": "tabs", "target": "alpha"})
        assert st == 200 and body["result"]["data"]["who"] == "alpha"
        st, body = _req(srv, "POST", "/cmd", {"op": "tabs", "target": "beta"})
        assert st == 200 and body["result"]["data"]["who"] == "beta"
        # Target by auto-id also works.
        st, body = _req(srv, "POST", "/cmd", {"op": "tabs", "target": "a"})
        assert st == 200 and body["result"]["data"]["who"] == "alpha"
    finally:
        a.stop(); b.stop(); srv.shutdown(); srv.server_close()


def test_cmd_ambiguous_no_target_409():
    """Two instances + no target → 409 ambiguous_instance, never a silent pick."""
    srv, _ = _serve(poll_timeout=5.0)
    a = FakeExtension(srv, instance_id="a", label="alpha")
    b = FakeExtension(srv, instance_id="b", label="beta")
    a.start(); b.start()
    try:
        assert _wait_instances(srv, 2) is not None
        st, body = _req(srv, "POST", "/cmd", {"op": "tabs"})
        assert st == 409
        assert body["error"] == "ambiguous_instance"
        keys = {i["key"] for i in body["instances"]}
        assert keys == {"alpha", "beta"}
    finally:
        a.stop(); b.stop(); srv.shutdown(); srv.server_close()


def test_cmd_unknown_target_404():
    srv, _ = _serve(poll_timeout=5.0)
    a = FakeExtension(srv, instance_id="a", label="alpha")
    a.start()
    try:
        assert _wait_instances(srv, 1) is not None
        st, body = _req(srv, "POST", "/cmd", {"op": "tabs", "target": "ghost"})
        assert st == 404
        assert body["error"] == "unknown_instance"
        assert body["target"] == "ghost"
    finally:
        a.stop(); srv.shutdown(); srv.server_close()


def test_poll_superseded_returns_409_not_204():
    """A blocked /poll whose connection is displaced by a NEWER connection sharing
    its routing key returns the DISTINCT `409 superseded` signal — never the idle
    `204`. This is what lets the extension back off instead of hot re-polling
    (the mutual-supersede livelock fix). Still bearer+Host guarded: this path is
    only reachable AFTER _guard, and the auth/host tests above cover /poll."""
    srv, _ = _serve(poll_timeout=5.0)
    res = {}

    def poll(iid, key):
        res[iid] = _req(srv, "GET", "/poll", headers=_poll_hdrs(iid, key))

    ta = threading.Thread(target=poll, args=("old", "dup"), daemon=True)
    ta.start()
    try:
        # "old" is registered + blocked in its long-poll.
        assert _wait_instances(srv, 1) is not None
        # "new" (different auto-id, same key) supersedes it.
        tb = threading.Thread(target=poll, args=("new", "dup"), daemon=True)
        tb.start()
        ta.join(3)
        assert "old" in res, "superseded poll never returned"
        status, body = res["old"]
        assert status == 409, f"expected 409 superseded, got {status}"
        assert body["error"] == "superseded"
    finally:
        srv.shutdown(); srv.server_close()


def test_supersede_logged_once_per_displacement_not_per_poll(monkeypatch):
    """The supersede is logged ONCE, at the displacement site — the superseded
    connection's own returning poll must NOT log (a backing-off loser can't flood
    journald). Deterministic proof of the no-churn contract (the extension's own
    back-off is only manually verifiable; the server side is unit-tested here)."""
    reg = S.Registry()
    events = []
    monkeypatch.setattr(S, "log", lambda ev, **k: events.append((ev, k)))

    got = {}

    def poll_old():
        got["old"] = reg.poll("old", "dup", 2.0)

    t = threading.Thread(target=poll_old, daemon=True)
    t.start()
    # Wait until "old" is registered AND actively blocked in its poll.
    deadline = time.time() + 3
    while time.time() < deadline:
        with reg._cond:
            inst = reg._instances.get("dup")
            if inst is not None and inst.active_polls > 0:
                break
        time.sleep(0.01)
    # A NEW connection claims the key → supersede "old" (ONE displacement event).
    reg.poll("new", "dup", 0.05)
    t.join(2)

    # The superseded connection got the DISTINCT sentinel, not an idle None.
    assert got["old"] is S.SUPERSEDED
    supersede_logs = [e for e in events if e[0] == "supersede"]
    assert len(supersede_logs) == 1, \
        f"expected exactly ONE supersede log, got {len(supersede_logs)}: {supersede_logs}"


def test_cmd_same_label_supersede_routes_to_newest():
    """A NEW connection with the SAME label supersedes the old one at that key
    (they share a routing key); the old connection is dropped and a command
    routes to the survivor.

    The superseded connection is modelled as *dropped/closed* (it stops polling)
    — labels are required to be unique per host, so two connections concurrently
    contending for one key is a misconfiguration, not steady state.
    """
    srv, _ = _serve(poll_timeout=0.3)
    old = FakeExtension(srv, instance_id="old", label="dup",
                        executor=lambda c: {"who": "old"})
    old.start()
    try:
        assert _wait_instances(srv, 1) is not None
        # Old connection is dropped (stops polling) but is still within the stale
        # window, so it is still the registered holder of key "dup".
        old.stop(); old.join(timeout=2)

        new = FakeExtension(srv, instance_id="new", label="dup",
                            executor=lambda c: {"who": "new"})
        new.start()
        # New's first poll supersedes old; the sole live holder of "dup" is now new.
        deadline = time.time() + 3
        while time.time() < deadline:
            st, body = _req(srv, "GET", "/health")
            if st == 200 and body["count"] == 1 and \
                    body["instances"][0]["instanceId"] == "new":
                break
            time.sleep(0.02)
        assert body["count"] == 1
        assert body["instances"][0]["instanceId"] == "new"

        st, body = _req(srv, "POST", "/cmd", {"op": "tabs"})
        assert st == 200 and body["result"]["data"]["who"] == "new"
        new.stop()
    finally:
        old.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Back-compat: a legacy extension that polls WITHOUT an instance id
# --------------------------------------------------------------------------- #
def test_legacy_no_handshake_single_instance():
    """A poll with no X-Bridge-Instance-Id registers as one synthetic instance
    and a no-target /cmd + a no-instanceId /result still round-trip."""
    srv, _ = _serve(poll_timeout=5.0)

    class LegacyExt(FakeExtension):
        def _poll_headers(self):
            return {}  # no identity headers at all

        def run(self):
            while not self._stopev.is_set():
                try:
                    status, cmd = _req(self.srv, "GET", "/poll")
                except Exception:
                    if self._stopev.is_set():
                        return
                    continue
                if status == 204 or cmd is None:
                    continue
                # NOTE: no instanceId in the result body (old wire shape).
                _req(self.srv, "POST", "/result",
                     {"id": cmd["id"], "ok": True, "data": {"who": "legacy"}})

    ext = LegacyExt(srv)
    ext.start()
    try:
        body = _wait_instances(srv, 1)
        assert body is not None
        assert body["instances"][0]["key"] == S.LEGACY_INSTANCE_ID
        st, r = _req(srv, "POST", "/cmd", {"op": "tabs"})
        assert st == 200 and r["result"]["data"]["who"] == "legacy"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Registry core (no HTTP): routing, independence, supersede, key resolution
# --------------------------------------------------------------------------- #
def test_registry_independent_queues_no_cross_delivery():
    """Command for instance A is never delivered to B's poll; each gets its own
    queue and its own reply."""
    reg = S.Registry()
    picked_a, picked_b = [], []
    stop = threading.Event()

    def poller(iid, label, bucket):
        while not stop.is_set():
            cmd = reg.poll(iid, label, 0.1)
            if cmd is not None:
                bucket.append(cmd)
                reg.deliver_result(cmd["id"],
                                   {"id": cmd["id"], "ok": True,
                                    "data": {"who": label}}, instance_id=iid)

    ta = threading.Thread(target=poller, args=("a", "alpha", picked_a),
                          daemon=True)
    tb = threading.Thread(target=poller, args=("b", "beta", picked_b),
                          daemon=True)
    ta.start(); tb.start()
    # Wait until both are registered + live.
    deadline = time.time() + 3
    while time.time() < deadline and len(reg.snapshot()) < 2:
        time.sleep(0.01)
    assert len(reg.snapshot()) == 2

    r = reg.submit({"op": "tabs"}, timeout=3.0, target="alpha")
    assert r["data"]["who"] == "alpha"
    assert len(picked_b) == 0, "B must never see A's command"
    assert len(picked_a) == 1
    stop.set()


def test_registry_key_is_label_when_set_else_autoid():
    reg = S.Registry()
    stop = threading.Event()

    def poller(iid, label):
        while not stop.is_set():
            reg.poll(iid, label, 0.05)

    # Labelled instance → key is the label.
    t1 = threading.Thread(target=poller, args=("uuid-1", "work"), daemon=True)
    # Unlabelled instance → key is the auto-id.
    t2 = threading.Thread(target=poller, args=("uuid-2", ""), daemon=True)
    t1.start(); t2.start()
    deadline = time.time() + 3
    while time.time() < deadline and len(reg.snapshot()) < 2:
        time.sleep(0.01)
    keys = {i["key"] for i in reg.snapshot()}
    assert "work" in keys       # label wins
    assert "uuid-2" in keys     # falls back to auto-id
    stop.set()


def test_registry_supersede_inflight_resolves_error_no_leak():
    """Same routing key reconnects with a NEW auto-id → the old connection is
    dropped, its in-flight submit resolves to BridgeSuperseded (no orphaned
    waiter), and a subsequent command routes to the newcomer."""
    reg = S.Registry()
    picked = []

    def poller_old():
        cmd = reg.poll("old", "dup", 2.0)
        if cmd is not None:
            picked.append(cmd)   # picked up but NEVER delivered (in-flight)

    threading.Thread(target=poller_old, daemon=True).start()

    res = {}

    def submit():
        try:
            res["v"] = reg.submit({"op": "getHtml"}, timeout=3.0, target="dup")
        except Exception as e:  # noqa: BLE001
            res["err"] = type(e).__name__

    ts = threading.Thread(target=submit, daemon=True)
    ts.start()

    # Wait until "old" picked the command (now in-flight).
    deadline = time.time() + 3
    while not picked and time.time() < deadline:
        time.sleep(0.01)
    assert picked, "old connection never picked up the command"

    # A NEW connection registers under the same key → supersede "old".
    reg.poll("new", "dup", 0.05)

    ts.join(3)
    assert res.get("err") == "BridgeSuperseded"

    # No orphaned waiter / leak: the old instance is gone; the survivor is "new".
    with reg._cond:
        assert "old" not in reg._instances
        inst = reg._instances["dup"]
        assert inst.instance_id == "new"
        assert inst.waiters == set()

    # A subsequent command routes to the newcomer.
    def deliverer():
        cmd = reg.poll("new", "dup", 2.0)
        if cmd is not None:
            reg.deliver_result(cmd["id"], {"id": cmd["id"], "ok": True,
                                           "data": {"who": "new"}},
                               instance_id="new")
    threading.Thread(target=deliverer, daemon=True).start()
    r = reg.submit({"op": "tabs"}, timeout=3.0, target="dup")
    assert r["data"]["who"] == "new"


def test_registry_id_correlation_out_of_order():
    """Two concurrent submits to the SAME instance get their OWN replies even
    when the extension answers them in reverse order.

    They target DIFFERENT tabs so both are concurrently in-flight — commands to
    the SAME tab now serialize FIFO (per-tab isolation), so only distinct tabs
    can be simultaneously outstanding. The transport's cid correlation is what's
    under test here and is unchanged."""
    reg = S.Registry()
    poll_done = threading.Event()
    picked = []

    def poller():
        while not poll_done.is_set():
            cmd = reg.poll("solo", "one", 0.2)
            if cmd is not None:
                picked.append(cmd)

    threading.Thread(target=poller, daemon=True).start()

    results = {}

    def submit(tag):
        # Distinct tabs (1 for A, 2 for B) → independent tab-FIFO turnstiles.
        results[tag] = reg.submit({"op": "eval", "tag": tag}, timeout=3.0,
                                  tab=(1 if tag == "A" else 2))

    s1 = threading.Thread(target=submit, args=("A",), daemon=True)
    s2 = threading.Thread(target=submit, args=("B",), daemon=True)
    s1.start(); s2.start()

    deadline = time.time() + 3
    while len(picked) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert len(picked) == 2

    for cmd in reversed(picked):
        reg.deliver_result(cmd["id"], {"id": cmd["id"], "ok": True,
                                       "data": {"tag": cmd["tag"]}},
                           instance_id="solo")

    s1.join(3); s2.join(3)
    poll_done.set()
    assert results["A"]["data"]["tag"] == "A"
    assert results["B"]["data"]["tag"] == "B"


def test_registry_submit_no_extension_raises():
    reg = S.Registry()
    with pytest.raises(S.NoExtension):
        reg.submit({"op": "getHtml"}, timeout=0.2)


def test_registry_deliver_unknown_id_false():
    reg = S.Registry()
    assert reg.deliver_result("nope", {"x": 1}) is False


def test_validate_command_contract():
    # The op set is the shared contract with extension/protocol.js.
    #
    # NOTE: equality between THIS list and protocol.js's is pinned separately and
    # structurally by test_ping_op_set_mirrors_the_extension_protocol_js, which
    # parses both real files. What this literal adds is a deliberate speed bump:
    # adding an op has to be acknowledged in a second place. That is a weaker
    # signal than the drift test — consider dropping it if it keeps costing a red
    # `main` — but it is a live check today, so it gets updated, not deleted.
    assert set(S.ALLOWED_OPS) == {"getHtml", "text", "eval", "tabs", "nav",
                                  "screenshot", "open", "close",
                                  "frames", "click", "type", "key", "wake",
                                  "activate", "upload", "ping", "emulate",
                                  "context"}
    # `upload` is a dispatched, tab-scoped CDP op requiring selector + path.
    assert S.validate_command({"op": "upload", "selector": "#f",
                               "path": "/tmp/x"}) == ("upload", None)
    assert S.validate_command({"op": "upload", "path": "/tmp/x"})[1] \
        == "missing_field:selector"
    assert S.validate_command({"op": "upload", "selector": "#f"})[1] \
        == "missing_field:path"
    assert "upload" in S.TAB_SCOPED_OPS
    # `text` is a dispatched, tab-scoped, cheap-read op with NO required field.
    assert S.validate_command({"op": "text"}) == ("text", None)
    assert "text" in S.TAB_SCOPED_OPS
    # `activate` is a dispatched, tab-scoped op with NO required field (the server
    # injects the tabId; its optional waitMs is a passthrough, not a routing hint).
    assert S.validate_command({"op": "activate"}) == ("activate", None)
    assert S.validate_command({"op": "activate", "waitMs": 1000}) == ("activate", None)
    assert "activate" in S.TAB_SCOPED_OPS
    # `wake` is a dispatched, tab-scoped CDP op with NO required field; its
    # optional waitMs (the un-throttle settle) is a passthrough, not a routing hint.
    assert S.validate_command({"op": "wake"}) == ("wake", None)
    assert S.validate_command({"op": "wake", "waitMs": 500}) == ("wake", None)
    assert "wake" in S.TAB_SCOPED_OPS
    assert S.validate_command({"op": "tabs"}) == ("tabs", None)
    assert S.validate_command({"op": "eval", "js": "1"})[0] == "eval"
    assert S.validate_command({"op": "eval"})[1] == "missing_field:js"
    assert S.validate_command({"op": "bogus"})[1] == "unknown_op"
    assert S.validate_command("nope")[1] == "body_not_object"
    # open/close (dispatched) and release (server-side) are all accepted ops.
    assert S.validate_command({"op": "open"}) == ("open", None)
    assert S.validate_command({"op": "close"}) == ("close", None)
    assert S.validate_command({"op": "release"}) == ("release", None)
    assert "release" in S.SERVER_OPS and "release" not in S.ALLOWED_OPS


# --------------------------------------------------------------------------- #
# `text` op (B1 cheap read): dispatched + tab-scoped exactly like getHtml, its
# selector/maxBytes pass through to the extension, and its telemetry stays
# metadata-only (the page text is NEVER emitted). Headless — the FakeExtension
# echoes; the real innerText/normalization is unit-tested in protocol.test.mjs.
# --------------------------------------------------------------------------- #
def test_text_routes_to_owned_session_tab():
    """A session that `open`ed has its `text` op routed to ITS owned tabId — i.e.
    `text` is tab-scoped just like getHtml/eval/nav."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        st, body = _cmd(srv, {"op": "text"}, session="A")
        assert st == 200
        assert body["result"]["data"]["tabId"] == 101, \
            "text must route to the session's owned tab (tab-scoped)"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_text_passes_selector_and_maxbytes_to_extension():
    """selector + maxBytes are forwarded verbatim to the extension command (they
    are not skill-side routing hints like target/tab)."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=lambda c: {"url": "https://x.test",
                                            "title": "X", "text": "hello"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd",
                     {"op": "text", "selector": "main", "maxBytes": 1024})
        assert st == 200
        text_cmds = [c for c in ext.dispatched if c["op"] == "text"]
        assert len(text_cmds) == 1
        assert text_cmds[0]["selector"] == "main"
        assert text_cmds[0]["maxBytes"] == 1024
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_text_telemetry_is_metadata_only(telemetry):
    """PRIVACY: a `text` result carries page text, but telemetry emits ONLY the
    op + the bare domain — never the extracted text content."""
    spool_dir = telemetry
    srv, _ = _serve()
    secret_text = "SECRET_PAGE_TEXT_deadbeef the quick brown fox"
    ext = FakeExtension(
        srv, executor=lambda c: {"url": "https://news.ycombinator.com/",
                                 "title": "HN", "text": secret_text})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "text"})
        assert st == 200
        assert body["result"]["data"]["text"] == secret_text   # round-trip sanity
        e = _wait_events(spool_dir, 1)[0]
        p = json.loads(e["payload"])
        assert p["op"] == "text"
        assert p["outcome"] == "ok"
        assert p["domain"] == "news.ycombinator.com"
        assert e["text"] == "news.ycombinator.com"              # bare domain only
        raw = _log_file(spool_dir).read_text()
        assert "SECRET_PAGE_TEXT" not in raw, "text content leaked into telemetry"
        assert "quick brown fox" not in raw
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Icon sanity — the manifest references all four sizes and each PNG's real IHDR
# width/height matches its declared size. Stdlib only (struct — no PIL).
# --------------------------------------------------------------------------- #
def _png_size(path):
    """Return (width, height) from a PNG's IHDR using stdlib struct only."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"not a PNG: {path}"
    # 8-byte signature, then IHDR chunk: 4 len + 4 "IHDR" + 4 width + 4 height.
    assert data[12:16] == b"IHDR", f"IHDR not first chunk: {path}"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def test_manifest_icons_exist_and_match_declared_size():
    manifest = json.loads((EXT_DIR / "manifest.json").read_text())
    icons = manifest.get("icons")
    assert icons, "manifest.json is missing an 'icons' map"
    assert set(icons) == {"16", "32", "48", "128"}, \
        "manifest icons must declare 16/32/48/128"
    for size, rel in icons.items():
        path = EXT_DIR / rel
        assert path.exists(), f"declared icon missing on disk: {rel}"
        w, h = _png_size(path)
        assert (w, h) == (int(size), int(size)), \
            f"{rel}: declared {size} but PNG is {w}x{h}"
    # The MV3 toolbar action icon references the same four sizes.
    action_icon = manifest.get("action", {}).get("default_icon", {})
    assert set(action_icon) == {"16", "32", "48", "128"}


def test_svg_source_present():
    assert (EXT_DIR / "icons" / "icon.svg").exists(), \
        "the SVG icon source must be committed alongside the PNGs"


# --------------------------------------------------------------------------- #
# Activity telemetry: every handled command emits ONE metadata-only,
# best-effort event into the activity spool (source=browser-bridge, kind=cmd).
# All headless — writes to a temp spool dir, never ClickHouse/network/Brave.
# --------------------------------------------------------------------------- #
def test_domain_from_result_bare_host_only():
    """Only a bare hostname is ever derived — never a path/query/port, and a
    screenshot data: URL yields nothing."""
    d = S._domain_from_result
    assert d({"data": {"url": "https://mail.google.com/mail/u/0?tok=SECRET"}}) \
        == "mail.google.com"
    assert d({"url": "https://x.test:8443/p?q=1"}) == "x.test"
    assert d({"data": {"url": "data:image/png;base64,AAAABBBB"}}) == ""
    assert d({"data": {"dataUrl": "data:image/png;base64,AAAA"}}) == ""
    assert d({"data": {"tabs": []}}) == ""
    assert d({}) == ""
    assert d(None) == ""
    assert d("not-a-dict") == ""


def test_cmd_ok_emits_one_metadata_event(telemetry):
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(
        srv, executor=lambda c: {"url": "https://mail.google.com/mail/u/0?t=rm",
                                 "html": "<html>hi</html>"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml"})
        assert st == 200
        evs = _wait_events(spool_dir, 1)
        assert len(evs) == 1, f"expected exactly one event, got {evs}"
        e = evs[0]
        assert e["source"] == "browser-bridge"
        assert e["kind"] == "cmd"
        assert e["exit_code"] == "0"
        assert int(e["duration_ms"]) >= 0        # latency present
        assert e["text"] == "mail.google.com"    # text = bare domain
        p = json.loads(e["payload"])
        # Exact equality on purpose: metadata-only is asserted POSITIVELY and
        # NEGATIVELY — nothing may creep into this payload unnoticed. `sess_src`
        # is here because every /cmd row now states which tier its session id
        # came from; this request sends NO X-Session-Id, so the honest answer is
        # `unknown` and no `session` key is written (asserted below).
        assert p == {"op": "getHtml", "key": "", "outcome": "ok",
                     "domain": "mail.google.com", "sess_src": "unknown"}
        assert "session" not in e, e
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_cmd_ok_no_url_uses_op_as_text_and_omits_domain(telemetry):
    """A result with no url (e.g. tabs) → text falls back to the op, and the
    payload carries no domain key."""
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {"tabs": [{"id": 1}]})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd", {"op": "tabs"})
        assert st == 200
        e = _wait_events(spool_dir, 1)[0]
        assert e["text"] == "tabs"
        p = json.loads(e["payload"])
        assert p["op"] == "tabs" and p["outcome"] == "ok"
        assert "domain" not in p
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_cmd_emits_routing_key_from_target(telemetry):
    """The instance routing key (the skill's --instance target) is recorded."""
    spool_dir = telemetry
    srv, _ = _serve(poll_timeout=5.0)
    a = FakeExtension(srv, instance_id="a", label="alpha",
                      executor=lambda c: {"who": "a"})
    b = FakeExtension(srv, instance_id="b", label="beta",
                      executor=lambda c: {"who": "b"})
    a.start(); b.start()
    try:
        assert _wait_count(srv, 2) is not None   # /instances: emits nothing
        st, _ = _req(srv, "POST", "/cmd", {"op": "tabs", "target": "alpha"})
        assert st == 200
        e = _wait_events(spool_dir, 1)[0]
        p = json.loads(e["payload"])
        assert p["key"] == "alpha"
        assert p["outcome"] == "ok"
    finally:
        a.stop(); b.stop(); srv.shutdown(); srv.server_close()


def test_cmd_no_extension_emits_error_outcome(telemetry):
    """An error dispatch (no extension) still emits, with a nonzero exit_code and
    the error outcome — no domain (no result)."""
    spool_dir = telemetry
    srv, _ = _serve()
    try:
        st, _ = _req(srv, "POST", "/cmd", {"op": "tabs"})
        assert st == 503
        e = _wait_events(spool_dir, 1)[0]
        assert e["exit_code"] == "1"
        p = json.loads(e["payload"])
        assert p["op"] == "tabs"
        assert p["outcome"] == "no_extension"
        assert "domain" not in p
    finally:
        srv.shutdown(); srv.server_close()


def test_cmd_ambiguous_emits_ambiguous_outcome(telemetry):
    """An ambiguous dispatch (>1 instance, no target) emits outcome=ambiguous."""
    spool_dir = telemetry
    srv, _ = _serve(poll_timeout=5.0)
    a = FakeExtension(srv, instance_id="a", label="alpha")
    b = FakeExtension(srv, instance_id="b", label="beta")
    a.start(); b.start()
    try:
        assert _wait_count(srv, 2) is not None   # /instances: emits nothing
        st, _ = _req(srv, "POST", "/cmd", {"op": "tabs"})
        assert st == 409
        e = _wait_events(spool_dir, 1)[0]
        assert e["exit_code"] == "1"
        p = json.loads(e["payload"])
        assert p["outcome"] == "ambiguous"
        assert p["op"] == "tabs"
    finally:
        a.stop(); b.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# S2 — `ping` gets its OWN short deadline (2026-08-02 usage audit, F7)
#
# `ping` is the documented FIRST thing you run when you suspect the loaded
# extension is stale. Measured over a 2-day window: 34 pings, 13 failures (38.2%,
# the worst rate of any op), of which 6 timed out at EXACTLY 20,000 ms — the
# generic CMD_TIMEOUT. The 21 healthy ones averaged 3–4 ms. A diagnostic that
# takes 20 s to say "no" is one an operator learns to skip.
#
# These tests are WALL-CLOCK tests, so they use a deliberately wide margin: the
# claim is "ping does not wait out cmd_timeout", not "ping takes exactly 2.0 s".
# --------------------------------------------------------------------------- #
def test_ping_does_not_wait_out_cmd_timeout(monkeypatch):
    """REGRESSION. Pre-change `ping` used cmd_timeout like every other op, so
    against a wedged extension it burned the full budget before answering."""
    monkeypatch.delenv("BROWSER_BRIDGE_PING_TIMEOUT", raising=False)
    # The instance is IDLE (this ping is the only command), so nothing can be
    # ahead of it and a stalled reply IS the wedge → the fast deadline applies.
    srv, _ = _serve(cmd_timeout=25.0, poll_timeout=5.0)
    ext = FakeExtension(srv, swallow=True)       # picks up, never answers
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        t0 = time.monotonic()
        st, body = _req(srv, "POST", "/cmd", {"op": "ping"})
        elapsed = time.monotonic() - t0
        assert st == 504 and body["error"] == "timeout"
        # Red pre-change: this was ~25s (the full cmd_timeout).
        assert elapsed < 5.0, f"ping waited {elapsed:.1f}s — cmd_timeout, not its own"
        # ...and it did actually WAIT its own deadline rather than failing instantly
        # for some unrelated reason (which would make the bound above vacuous).
        assert elapsed >= 1.5, f"ping returned in {elapsed:.2f}s — it never waited"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_the_short_deadline_applies_to_ping_ONLY(monkeypatch):
    """NEGATIVE CONTROL for the test above. Without this, a change that shortened
    EVERY op's timeout would pass it — and that would be a real regression (a 2s
    cap on `nav`/`eval` would break legitimate slow pages).

    Same server, same wedged extension, a non-ping op: it must still wait out the
    full cmd_timeout.

    🔴 BOUNDED ON BOTH SIDES; each bound catches a DIFFERENT mutation, and both
    have caught one for real:
      * LOWER — `fast_timeout` passed for every op: an idle instance would give
        getHtml the 2s fast deadline, so it would return in ~2s, not ~5s.
      * UPPER — the derived busy-budget applied to every op: getHtml could then
        run far past its own cmd_timeout.
    A single bound is not enough. An earlier revision had only the lower one and
    silently stopped discriminating when PING_TIMEOUT_DEFAULT briefly rose above
    this test's cmd_timeout — the leak mutant passed `10 >= 3.5`.
    """
    monkeypatch.delenv("BROWSER_BRIDGE_PING_TIMEOUT", raising=False)
    assert S.PING_TIMEOUT_DEFAULT < 5.0, (
        "this control needs the fast deadline to be clearly below cmd_timeout")
    srv, _ = _serve(cmd_timeout=5.0, poll_timeout=5.0)
    ext = FakeExtension(srv, swallow=True)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        t0 = time.monotonic()
        st, _ = _req(srv, "POST", "/cmd", {"op": "getHtml"})
        elapsed = time.monotonic() - t0
        assert st == 504
        assert elapsed >= 4.0, (
            f"getHtml returned in {elapsed:.2f}s — the fast ping deadline leaked "
            "onto every op (cmd_timeout was 5s)")
        assert elapsed < 9.0, (
            f"getHtml waited {elapsed:.2f}s — something gave a non-ping op a "
            "budget beyond its own cmd_timeout")
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_ping_survives_a_BUSY_but_perfectly_healthy_extension(monkeypatch):
    """🔴 REGRESSION (adversarial audit of PR #278). The FIRST cut of this feature
    used a 2s deadline and had NO test with a BUSY extension — only a wedged one
    and an idle one. That is the whole gap: the extension's poll loop is strictly
    SERIAL (service_worker.js:1646-1652 — `await execute()` → `await postResult()`
    → next `pollOnce()`), so `ping` skips the per-tab FIFO but still cannot be
    DEQUEUED until whatever is already running finishes.

    Consequence of the 2s cut, measured: two sessions drive one Brave profile;
    agent B runs `browser ping` (the skill's documented FIRST action) while agent A
    is mid-`nav` on a heavy page. B got a 504 and the message "is Brave focused /
    responsive?", whose documented remedy is a FULL Brave restart of the operator's
    live session. A healthy profile reported as dead.

    This models exactly that: a legitimate BUSY_S-second op in flight, then a ping.

    The elapsed LOWER bound is what stops this being vacuous — without it the test
    passes against an extension that was never actually busy, which is the mistake
    the original S2 tests made.
    """
    BUSY_S = 6.0
    monkeypatch.delenv("BROWSER_BRIDGE_PING_TIMEOUT", raising=False)

    def executor(cmd):
        if cmd.get("op") == "getHtml":
            time.sleep(BUSY_S)          # a legitimate slow op, NOT a wedge
            return {"html": "<html></html>"}
        return {"pong": True, "extensionVersion": "0.7.0"}

    srv, _ = _serve(cmd_timeout=25.0, poll_timeout=5.0)
    ext = FakeExtension(srv, executor=executor)
    ext.start()
    slow = {}
    try:
        assert _wait_connected(srv, want=True)

        # Agent A: the slow-but-healthy op. Exceptions are captured rather than
        # left to surface in a daemon thread (pytest would attribute them to some
        # unrelated later test — see the wedge test below).
        def _slow():
            try:
                slow.update(zip(("st", "body"),
                                _req(srv, "POST", "/cmd", {"op": "getHtml"})))
            except Exception as exc:           # noqa: BLE001
                slow["exc"] = exc

        t = threading.Thread(target=_slow, daemon=True)
        t.start()

        # Wait until the extension has actually PICKED IT UP — being busy is the
        # precondition under test, so it must be established, not assumed.
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if any(c.get("op") == "getHtml" for c in ext.dispatched):
                break
            time.sleep(0.02)
        else:
            pytest.fail("the fake extension never dequeued the slow op")

        # Agent B: the diagnostic, issued against a busy-but-healthy extension.
        t0 = time.monotonic()
        st, body = _req(srv, "POST", "/cmd", {"op": "ping"})
        elapsed = time.monotonic() - t0

        assert st == 200, (
            f"a BUSY but healthy extension was reported dead after {elapsed:.2f}s "
            "— this is the false negative that sends an operator to restart Brave")
        assert body["result"]["data"]["pong"] is True
        # NOT vacuous: it genuinely queued behind the slow op.
        assert elapsed >= BUSY_S * 0.5, (
            f"ping answered in {elapsed:.2f}s — the extension was not actually "
            "busy, so this proves nothing")
        t.join(timeout=10)
        assert slow.get("st") == 200, "the slow op must also have completed fine"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_ping_fast_fails_even_while_BUSY_once_the_work_has_blown_its_budget():
    """🔴 THE PAYOFF of the structural gate, and the case a tuned constant could
    NEVER express: the instance is busy AND wedged.

    A fixed deadline has to choose one failure. Too short → a busy healthy profile
    reads as dead. Too long → a genuinely wedged one takes the full cmd_timeout to
    say so. Deriving the deadline from the age of the outstanding work does both:
    while the in-flight command is inside EXEC_OP_BUDGET_S the ping waits, and the
    moment that budget is exhausted the extension is provably not healthy (its own
    `execute()` self-bound says so), and the ping fails fast.

    🔴 THE FIXTURE IS CALIBRATED, and that is load-bearing — an earlier version was
    INSENSITIVE to the `- age` term it advertises. It used EXEC=0.2 with the default
    2s grace, so the no-age budget was 0.2+2.0 = 2.2s, comfortably under its own
    `elapsed < 5.0` bound: deleting `- age` from the formula left this GREEN and only
    the unit test caught it. Now EXEC=3.0 / GRACE=0.5 / age≈4s, so:
        with `- age`:    3.0 + 0.5 - 4.0  = -0.5  -> clamps to the 0.3s fast floor
        without `- age`: 3.0 + 0.5        =  3.5s
    and the bound sits between them.

    cmd_timeout is also kept BELOW `_req`'s 10s urlopen timeout. It was 25s, so the
    `return timeout` mutant died on a transport TimeoutError instead of this test's
    own assertion — red for a NEIGHBOURING guard's reason, which proves nothing
    about this one.
    """
    # ping_timeout is passed EXPLICITLY: make_handler resolves it once at
    # construction, so patching the env (or PING_TIMEOUT_DEFAULT) at request time
    # would be inert and the test would silently measure the 2s default instead.
    srv, _ = _serve(cmd_timeout=8.0, poll_timeout=5.0,   # < the 10s urlopen bound
                    ping_timeout=0.3)
    ext = FakeExtension(srv, swallow=True)     # takes the op, never answers
    ext.start()

    # The wedging request outlives the assertions and dies when the server closes.
    # SWALLOW its exception explicitly: an unhandled exception in a daemon thread
    # is reported by pytest against whatever test happens to be running when it
    # surfaces — it showed up on `test_release_drops_ownership_without_dispatch`,
    # which has nothing to do with this. A test must not poison its neighbours.
    wedger = {}

    def _wedge():
        try:
            wedger["st"] = _req(srv, "POST", "/cmd", {"op": "getHtml"})[0]
        except Exception as exc:               # noqa: BLE001 — teardown teardown
            wedger["exc"] = exc

    try:
        assert _wait_connected(srv, want=True)
        # Agent A's op goes out and wedges.
        t = threading.Thread(target=_wedge, daemon=True)
        t.start()
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if any(c.get("op") == "getHtml" for c in ext.dispatched):
                break
            time.sleep(0.02)
        else:
            pytest.fail("the fake extension never dequeued the wedging op")

        # Let the (shrunken) budget lapse, so the in-flight work is now OVERDUE.
        # age ends up ~4s: 3.0 + 0.5 - 4.0 < 0, so the budget is negative WITH the
        # age term and +3.5s without it.
        time.sleep(4.0)
        with mock.patch.object(S, "EXEC_OP_BUDGET_S", 3.0), \
                mock.patch.object(S, "WEDGE_GRACE_S", 0.5):
            t0 = time.monotonic()
            st, _ = _req(srv, "POST", "/cmd", {"op": "ping"})
            elapsed = time.monotonic() - t0
        assert st == 504
        # Fast, NOT cmd_timeout: the budget went negative, so it clamps to the
        # fast floor. 1.5 sits between 0.3 (correct) and 3.5 (no `- age` term).
        assert elapsed < 1.5, (
            f"ping waited {elapsed:.2f}s against a wedged instance — the gate did "
            "not subtract how long the in-flight work had already been running")
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()
        t.join(timeout=30)                     # let it finish before pytest moves on


def test_ping_still_waits_after_the_SUBMITTER_gave_up_on_a_running_command():
    """🔴 REGRESSION (round-3 audit). The submitter abandoning a command does NOT
    free the extension: its serial loop is still executing that command for up to
    EXEC_OP_BUDGET_MS (18s) + RESULT_BUDGET_MS (10s) = 28s, which EXCEEDS the 20s
    default cmd_timeout. Dropping the `inflight` entry on BridgeTimeout therefore
    opened a window where the instance looks IDLE while it is provably still busy,
    and the next ping fast-failed at 2s against a perfectly healthy extension.

    In that window a0a0021 was WORSE than both main (20s flat) and the round-2
    constant (10s flat) — under either of those the ping would have waited it out.

    Shape: cmd_timeout 5s, a command that takes 8s. The submitter gives up at 5s;
    the extension is still executing until 8s; the ping is issued at ~5s and must
    still be alive at 8s.
    """
    srv, _ = _serve(cmd_timeout=5.0, poll_timeout=5.0)

    def executor(cmd):
        if cmd.get("op") == "getHtml":
            time.sleep(8.0)                 # outlives the submitter's 5s deadline
            return {"html": "<html></html>"}
        return {"pong": True}

    ext = FakeExtension(srv, executor=executor)
    ext.start()
    abandoned = {}

    def _abandon():
        try:
            abandoned["st"] = _req(srv, "POST", "/cmd", {"op": "getHtml"})[0]
        except Exception as exc:            # noqa: BLE001
            abandoned["exc"] = exc

    t = threading.Thread(target=_abandon, daemon=True)
    try:
        assert _wait_connected(srv, want=True)
        t.start()
        # Wait for the extension to actually START it, then for the submitter to
        # give up. Both preconditions are established, not assumed.
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if any(c.get("op") == "getHtml" for c in ext.dispatched):
                break
            time.sleep(0.02)
        else:
            pytest.fail("the fake extension never dequeued the long op")
        t.join(timeout=15)
        assert abandoned.get("st") == 504, "precondition: the submitter must give up"

        # The extension is STILL executing here. The ping must not read this as idle.
        t0 = time.monotonic()
        st, body = _req(srv, "POST", "/cmd", {"op": "ping"})
        elapsed = time.monotonic() - t0
        assert st == 200, (
            f"ping was told the extension is dead after {elapsed:.2f}s, while it "
            "was still executing an abandoned command")
        assert body["result"]["data"]["pong"] is True
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()
        t.join(timeout=15)


def test_effective_timeout_gate_unit(monkeypatch):
    """The gate's arithmetic, directly — the threaded tests above cover the two
    endpoints, this covers the boundary and both clamps (one measurement is not a
    general claim).
    """
    clock = {"t": 1000.0}
    reg = S.Registry(clock=lambda: clock["t"])
    inst = S.Instance("k", "i", "", clock["t"])
    g = lambda t, f: reg._effective_timeout_locked(inst, t, f)   # noqa: E731

    # 1. Not a fast-fail op at all -> untouched, whatever is in flight.
    inst.inflight = {"a": 1000.0}
    assert g(20.0, None) == 20.0

    # 2. Idle -> the fast deadline.
    inst.inflight = {}
    assert g(20.0, 2.0) == 2.0

    # 3. Busy, work just started -> a full budget, CLAMPED to cmd_timeout so this
    #    can never be slower than the behaviour that predates the fast ping.
    monkeypatch.setattr(S, "EXEC_OP_BUDGET_S", 18.0)
    monkeypatch.setattr(S, "WEDGE_GRACE_S", 2.0)
    inst.inflight = {"a": 1000.0}
    assert g(20.0, 2.0) == 20.0
    assert g(5.0, 2.0) == 5.0, "must never exceed cmd_timeout"

    # 4. Busy, partway through -> the REMAINING budget.
    clock["t"] = 1012.0                     # 12s elapsed of an 18+2 budget
    assert g(20.0, 2.0) == pytest.approx(8.0)

    # 5. Overdue -> negative budget, clamped UP to the fast floor (never 0/instant).
    clock["t"] = 1030.0
    assert g(20.0, 2.0) == 2.0

    # 6. TWO commands in flight drain serially, so the budget scales with depth —
    #    otherwise a queue of legitimate work would false-negative.
    clock["t"] = 1012.0
    inst.inflight = {"a": 1000.0, "b": 1005.0}
    assert g(60.0, 2.0) == pytest.approx(2 * 18.0 + 2.0 - 12.0)

    # 7. 🟢-D: fast_timeout ABOVE cmd_timeout must NOT escape the ceiling. Both are
    #    operator-settable and nothing validates the relation; before the inner
    #    clamp this returned 60.0 for fast=60 / cmd=20, contradicting the
    #    "never above cmd_timeout" claim in the docstring.
    inst.inflight = {}
    assert g(20.0, 60.0) == 20.0, "idle: fast_timeout must be capped at cmd_timeout"
    inst.inflight = {"a": 1000.0}
    clock["t"] = 1030.0                     # overdue -> lower clamp is what applies
    assert g(20.0, 60.0) == 20.0, "busy: the lower clamp must not lift it past cmd"


def test_exec_budget_matches_the_extension():
    """EXEC_OP_BUDGET_S mirrors EXEC_OP_BUDGET_MS across a language boundary, so
    nothing can enforce it at runtime. Assert it here: if someone retunes the
    extension's self-bound, the gate's arithmetic silently stops matching reality.
    """
    proto = (Path(__file__).resolve().parent.parent
             / "extension" / "protocol.js").read_text(encoding="utf-8")
    m = re.search(r"export const EXEC_OP_BUDGET_MS\s*=\s*(\d+)", proto)
    assert m, "could not find EXEC_OP_BUDGET_MS in protocol.js — retarget this test"
    assert float(m.group(1)) / 1000.0 == S.EXEC_OP_BUDGET_S, (
        f"protocol.js says {m.group(1)}ms, server.py says "
        f"{S.EXEC_OP_BUDGET_S}s — the busy/wedged gate is now wrong")


def test_inflight_release_distinguishes_queued_from_running_on_abandon():
    """`inflight` bookkeeping across the exits that behave DIFFERENTLY. A stranded
    entry makes an instance look permanently busy so `ping` can never fast-fail on
    it again; releasing too eagerly makes a busy instance look idle (the bug this
    round fixed). Both directions are wrong, so both are pinned.

    An earlier version of this test claimed three exits, had two blocks, and both
    described the same case (timeout-after-dequeue). These are genuinely distinct:

      1. SUCCESS                     -> released at once.
      2. abandoned while STILL QUEUED -> released at once: it never ran and never
         will, so the instance really is that much less busy.
      3. abandoned while RUNNING      -> KEPT. The extension is still executing it;
         releasing here is exactly what made a busy instance read as idle.
      4. ...and the kept entry is released when the late result finally arrives,
         rather than lingering until the staleness window expires.
    """
    # 1. SUCCESS
    srv, reg = _serve(cmd_timeout=1.0, poll_timeout=5.0)
    ext = FakeExtension(srv, executor=lambda c: {"ok": 1})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _req(srv, "POST", "/cmd", {"op": "tabs"})[0] == 200
        insts = list(reg._instances.values())
        assert insts and all(not i.inflight for i in insts), "leaked after success"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()

    # 2. ABANDONED WHILE STILL QUEUED — nothing is polling, so the command never
    #    leaves the outbox. The instance stays resolvable (CONNECT_STALE_S).
    #    A SHORT poll_timeout matters: stop() only sets a flag, so the thread must
    #    be allowed to finish its in-flight long poll and exit before we submit —
    #    otherwise it dequeues the command and this becomes case 3 by accident.
    srv, reg = _serve(cmd_timeout=1.0, poll_timeout=0.5)
    ext = FakeExtension(srv, swallow=True)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        ext.stop()
        ext.join(timeout=10)
        assert not ext.is_alive(), "the fake must be fully stopped, not just flagged"
        before = len(ext.dispatched)
        assert _req(srv, "POST", "/cmd", {"op": "getHtml"})[0] == 504
        assert len(ext.dispatched) == before, "precondition: it must NOT be dequeued"
        insts = list(reg._instances.values())
        assert insts and all(not i.inflight for i in insts), (
            "a command that never left the outbox must not keep the instance busy")
    finally:
        srv.shutdown(); srv.server_close()

    # 3 + 4. ABANDONED WHILE RUNNING, then the late result lands.
    gate = threading.Event()

    def executor(cmd):
        gate.wait(timeout=10)
        return {"ok": 1}

    srv, reg = _serve(cmd_timeout=1.0, poll_timeout=5.0)
    ext = FakeExtension(srv, executor=executor)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _req(srv, "POST", "/cmd", {"op": "getHtml"})[0] == 504
        insts = list(reg._instances.values())
        assert insts and any(i.inflight for i in insts), (
            "the extension is STILL executing it — the entry must survive")
        gate.set()                            # let the late result arrive
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if all(not i.inflight for i in reg._instances.values()):
                break
            time.sleep(0.02)
        assert all(not i.inflight for i in reg._instances.values()), (
            "the late result must release the kept entry, not leave it to expire")
        # ...and `last_dispatch` must stop naming it. Its contract is "the command
        # it never answered" (surfaced by health/whoami); after a late result it
        # DID answer, so leaving it set reports a phantom unanswered op forever.
        assert all((i.last_dispatch or {}).get("id") is None
                   for i in reg._instances.values()), (
            "last_dispatch still names a command that has now been answered")
    finally:
        gate.set(); ext.stop(); srv.shutdown(); srv.server_close()


def test_a_kept_inflight_entry_expires_if_the_result_never_arrives():
    """The memory/liveness bound on case 3 above. If the abandoned command's result
    NEVER comes, the entry must stop counting after INFLIGHT_STALE_S — otherwise
    one wedged command would make `ping` slow on that instance forever, which is
    the failure the whole fast path exists to avoid.

    Measured at THREE points, because the two thresholds are different and it
    would be easy to conflate them:
      * age <  EXEC+GRACE      -> the extension may still be working: derived
                                  deadline, entry retained.
      * EXEC+GRACE < age < STALE -> budget has gone negative (the extension blew
                                  its own self-bound, so it IS wedged): the ping is
                                  fast again, but the entry is still RETAINED.
      * age >  STALE           -> pruned.

    ⚠ SCOPE: all three points are measured at **N=1, no live submitter** (a single
    ABANDONED entry). "The staleness window bounds memory, not the verdict" is TRUE
    ONLY AT N=1 — it holds here because a lone entry's budget is already negative
    by 20s, so pruning at 28s is verdict-neutral BY CONSTRUCTION. At N>=2 the prune
    does change the verdict (3 entries at age 29s: 27.0s -> 2.0s), which is exactly
    the bug `test_stale_prune_never_evicts_an_entry_whose_submitter_is_STILL_WAITING`
    covers. Do not generalise this test's conclusion past N=1.
    """
    clock = {"t": 1000.0}
    reg = S.Registry(clock=lambda: clock["t"])
    inst = S.Instance("k", "i", "", clock["t"])
    inst.inflight = {"abandoned": 1000.0}
    assert S.INFLIGHT_STALE_S > S.EXEC_OP_BUDGET_S + S.WEDGE_GRACE_S, (
        "this test needs the two thresholds to be distinct")

    # 1. Could still be running -> derived deadline, retained.
    clock["t"] = 1000.0 + 5.0
    assert reg._effective_timeout_locked(inst, 20.0, 2.0) > 2.0
    assert inst.inflight, "must not be pruned while it could still be running"

    # 2. Past its own budget but inside the staleness window -> fast, still retained.
    clock["t"] = 1000.0 + S.EXEC_OP_BUDGET_S + S.WEDGE_GRACE_S + 1.0
    assert reg._effective_timeout_locked(inst, 20.0, 2.0) == 2.0
    assert inst.inflight, "the staleness window is about memory, not the verdict"

    # 3. Past the staleness window -> pruned.
    clock["t"] = 1000.0 + S.INFLIGHT_STALE_S + 1.0
    assert reg._effective_timeout_locked(inst, 20.0, 2.0) == 2.0
    assert not inst.inflight, "a stale entry must be pruned, not just ignored"


def test_waiters_is_exactly_the_live_submitter_set():
    """The prune now READS `waiters` as "is a submitter still blocked on this?", so
    that property has to be pinned rather than assumed — it is the load-bearing
    premise of the fix below, and it lives in a different function.

    Checked on the exits that are reachable from outside: success and timeout.
    (The `finally`'s extra discard is a defensive net for an unexpected raise
    INSIDE the wait loop, which no test can reach from here — it is an invariant
    guard, and is labelled as such in the source rather than claimed as covered.)
    """
    srv, reg = _serve(cmd_timeout=1.0, poll_timeout=5.0)
    ext = FakeExtension(srv, executor=lambda c: {"ok": 1})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _req(srv, "POST", "/cmd", {"op": "tabs"})[0] == 200
        assert all(not i.waiters for i in reg._instances.values()), (
            "a completed submit must leave no waiter")
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()

    srv, reg = _serve(cmd_timeout=1.0, poll_timeout=5.0)
    ext = FakeExtension(srv, swallow=True)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _req(srv, "POST", "/cmd", {"op": "getHtml"})[0] == 504
        assert all(not i.waiters for i in reg._instances.values()), (
            "a timed-out submit must leave no waiter — otherwise its inflight "
            "entry would be exempt from the staleness prune forever")
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_stale_prune_never_evicts_an_entry_whose_submitter_is_STILL_WAITING():
    """🔴 REGRESSION (round-4 audit). INFLIGHT_STALE_S is measured from ENQUEUE,
    but a queued command's 18s execution budget does not start until the serial
    extension DEQUEUES it. So "past this point a healthy extension has certainly
    answered" is false exactly when N>=2 — which is the case the
    `len(inst.inflight) * EXEC_OP_BUDGET_S` term exists to model.

    The scenario is the remediation the same commit documented: an operator reads
    "raise CMD_TIMEOUT if that is your workload", sets 60s, and submits 3 ops. The
    extension legitimately works until t~54s. At t=29s all three entries aged past
    STALE(28) and were pruned WHILE THEIR SUBMITTERS WERE STILL BLOCKED, the
    instance read idle, and ping fast-failed at 2s. Worse than a0a0021 in that
    window, and it falsified the advice.

    Fix: an entry may only be pruned once NO live submitter awaits it. `waiters` is
    exactly that set (added at enqueue, discarded on all three loop exits, and
    discarded again in the finally so an unexpected raise cannot strand one).

    NEGATIVE CONTROL is the last block: an ABANDONED entry (no waiter) must still
    be pruned, or this "fix" would just disable the memory bound.
    """
    clock = {"t": 1000.0}
    reg = S.Registry(clock=lambda: clock["t"])
    inst = S.Instance("k", "i", "", clock["t"])

    # N=3, cmd_timeout 60 — every submitter still blocked.
    inst.inflight = {"a": 1000.0, "b": 1000.0, "c": 1000.0}
    inst.waiters = {"a", "b", "c"}
    clock["t"] = 1000.0 + 27.0                      # inside STALE: fine before too
    assert reg._effective_timeout_locked(inst, 60.0, 2.0) == pytest.approx(29.0)
    clock["t"] = 1000.0 + 29.0                      # PAST STALE — the bug window
    assert reg._effective_timeout_locked(inst, 60.0, 2.0) == pytest.approx(27.0), (
        "three live submitters were pruned as stale, so the instance read idle "
        "while the extension was legitimately working")
    assert len(inst.inflight) == 3, "a live submitter's entry must not be evicted"

    # N=2, cmd_timeout 20 — the default-ish shape.
    inst.inflight = {"a": 1000.0, "b": 1000.0}
    inst.waiters = {"a", "b"}
    clock["t"] = 1000.0 + 27.0
    assert reg._effective_timeout_locked(inst, 20.0, 2.0) == pytest.approx(11.0)
    clock["t"] = 1000.0 + 29.0
    assert reg._effective_timeout_locked(inst, 20.0, 2.0) == pytest.approx(9.0)
    assert len(inst.inflight) == 2

    # NEGATIVE CONTROL: no live submitter -> the staleness bound still applies.
    inst.inflight = {"abandoned": 1000.0}
    inst.waiters = set()
    clock["t"] = 1000.0 + 29.0
    assert reg._effective_timeout_locked(inst, 20.0, 2.0) == 2.0
    assert not inst.inflight, "an ABANDONED entry must still be pruned"


def test_result_budget_matches_the_extension():
    """RESULT_BUDGET_S mirrors RESULT_BUDGET_MS across a language boundary — the
    same drift hazard as EXEC_OP_BUDGET_S, and it feeds INFLIGHT_STALE_S."""
    proto = (Path(__file__).resolve().parent.parent
             / "extension" / "protocol.js").read_text(encoding="utf-8")
    m = re.search(r"export const RESULT_BUDGET_MS\s*=\s*(\d+)", proto)
    assert m, "could not find RESULT_BUDGET_MS in protocol.js — retarget this test"
    assert float(m.group(1)) / 1000.0 == S.RESULT_BUDGET_S, (
        f"protocol.js says {m.group(1)}ms, server.py says {S.RESULT_BUDGET_S}s")


def test_a_healthy_ping_is_unaffected_and_answers_in_milliseconds(monkeypatch):
    """NEGATIVE CONTROL #2: the cap must never truncate the HEALTHY path. 21
    measured healthy pings averaged 3–4 ms, so the 10 s deadline is ~2500×
    headroom — assert the fast path still returns 200, not a timeout."""
    monkeypatch.delenv("BROWSER_BRIDGE_PING_TIMEOUT", raising=False)
    srv, _ = _serve(cmd_timeout=25.0, poll_timeout=5.0)
    ext = FakeExtension(srv, executor=lambda c: {"pong": True})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        t0 = time.monotonic()
        st, body = _req(srv, "POST", "/cmd", {"op": "ping"})
        elapsed = time.monotonic() - t0
        assert st == 200 and body["ok"] is True
        assert elapsed < 1.0, f"a healthy ping took {elapsed:.2f}s"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_ping_timeout_is_env_overridable_and_survives_a_malformed_value(monkeypatch):
    """The knob is BROWSER_BRIDGE_PING_TIMEOUT, not a bare literal — and a typo in
    it must not take the bridge down at startup (a ValueError from float() would),
    so an unparseable value falls back to the default."""
    monkeypatch.setenv("BROWSER_BRIDGE_PING_TIMEOUT", "0.3")
    assert S._env_float("BROWSER_BRIDGE_PING_TIMEOUT",
                        S.PING_TIMEOUT_DEFAULT) == 0.3
    srv, _ = _serve(cmd_timeout=8.0, poll_timeout=5.0)   # ping_timeout=None → env
    ext = FakeExtension(srv, swallow=True)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        t0 = time.monotonic()
        st, _ = _req(srv, "POST", "/cmd", {"op": "ping"})
        elapsed = time.monotonic() - t0
        assert st == 504
        assert elapsed < 1.5, f"the env override was ignored ({elapsed:.2f}s)"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()

    monkeypatch.setenv("BROWSER_BRIDGE_PING_TIMEOUT", "not-a-number")
    assert S._env_float("BROWSER_BRIDGE_PING_TIMEOUT",
                        S.PING_TIMEOUT_DEFAULT) == S.PING_TIMEOUT_DEFAULT
    monkeypatch.delenv("BROWSER_BRIDGE_PING_TIMEOUT")
    assert S._env_float("BROWSER_BRIDGE_PING_TIMEOUT",
                        S.PING_TIMEOUT_DEFAULT) == S.PING_TIMEOUT_DEFAULT


def test_cmd_timeout_emits_timeout_outcome(telemetry):
    spool_dir = telemetry
    srv, _ = _serve(cmd_timeout=0.5, poll_timeout=5.0)
    ext = FakeExtension(srv, swallow=True)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd", {"op": "getHtml"})
        assert st == 504
        e = _wait_events(spool_dir, 1)[0]
        assert e["exit_code"] == "1"
        assert json.loads(e["payload"])["outcome"] == "timeout"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_emitted_event_is_metadata_only_no_page_content(telemetry):
    """PRIVACY: even when the command carries an eval source marker AND the
    result carries HTML, an eval return value, a screenshot data-URL, and a full
    URL with a secret query, NONE of that appears in the emitted event — only
    op/key/outcome/duration + the BARE domain."""
    spool_dir = telemetry
    srv, _ = _serve()
    marker_js = "SECRET_EVAL_MARKER_deadbeef()"
    secret_data_url = "data:image/png;base64,SCREENSHOTSECRETBYTESxyz=="
    secret_html = "<html>SECRET_HTML_BODY_TOKEN</html>"
    secret_query = "SUPERSECRETQUERY"

    def executor(cmd):
        # The extension echoes a rich result incl. sensitive fields + a full URL
        # with a query string. The command itself carried the eval marker.
        return {"url": f"https://mail.google.com/mail/u/0?token={secret_query}",
                "value": "EVAL_RETURN_SECRET", "html": secret_html,
                "dataUrl": secret_data_url}

    ext = FakeExtension(srv, executor=executor)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "eval", "js": marker_js})
        assert st == 200
        # Sanity: the sensitive material genuinely WAS in the round-trip.
        assert body["result"]["data"]["value"] == "EVAL_RETURN_SECRET"
        evs = _wait_events(spool_dir, 1)
        assert len(evs) == 1
        e = evs[0]
        # The fully-decoded event (every field incl. the b64 payload) leaks none.
        decoded_blob = "\t".join(f"{k}={v}" for k, v in e.items())
        # The RAW spool bytes too — defeats any base64-hidden copy.
        raw = _log_file(spool_dir).read_text()
        for secret in (marker_js, "SECRET_EVAL_MARKER", "EVAL_RETURN_SECRET",
                       secret_html, "SECRET_HTML_BODY_TOKEN", secret_data_url,
                       "SCREENSHOTSECRETBYTES", secret_query, "token="):
            assert secret not in decoded_blob, f"leaked in event: {secret}"
            assert secret not in raw, f"leaked in raw spool: {secret}"
        # What IS recorded: op + the bare domain, nothing else sensitive.
        p = json.loads(e["payload"])
        assert p["op"] == "eval"
        assert p["outcome"] == "ok"
        assert p["domain"] == "mail.google.com"   # host only — no path/query
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_cmd_succeeds_when_emitter_raises(monkeypatch):
    """BEST-EFFORT: if the emitter itself raises, the command STILL returns its
    normal successful result — no 500, no exception into the handler."""
    class Boom:
        def emit(self, *a, **k):
            raise RuntimeError("spool exploded")

    monkeypatch.setattr(S, "_load_spool_emit", lambda: Boom())
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {"url": "https://x.test"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml"})
        assert st == 200
        assert body["ok"] is True
        assert body["result"]["data"]["url"] == "https://x.test"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_cmd_succeeds_when_spool_unwritable(telemetry, monkeypatch, tmp_path):
    """BEST-EFFORT: an unwritable spool path never breaks a command."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")  # a regular file where a dir is expected
    monkeypatch.setenv("ACTIVITY_SPOOL_DIR", str(blocker / "spool"))
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {"url": "https://x.test"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml"})
        assert st == 200
        assert body["ok"] is True
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_instances_and_poll_still_do_not_emit(telemetry):
    """/instances and the extension's /poll are NOISE — still no events.

    This is the half of the old `test_health_and_instances_do_not_emit` that
    survives: /poll fires continuously (a long-poll per instance, forever) and
    /instances is a machine-facing list, so emitting for either would swamp
    activity.events with traffic no human initiated. Only the OPERATOR-facing
    orientation ops (/whoami, /health) emit — see the two tests below.
    """
    spool_dir = telemetry
    srv, _ = _serve(poll_timeout=5.0)
    ext = FakeExtension(srv, instance_id="a", label="alpha")
    ext.start()
    try:
        assert _wait_count(srv, 1) is not None   # exercises /poll repeatedly
        _req(srv, "GET", "/instances")
        time.sleep(0.2)  # give any erroneous emit a chance to land
        assert _read_events(spool_dir) == []
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


@pytest.mark.parametrize("path,op", [("/whoami", "whoami"), ("/health", "health")])
def test_orientation_ops_emit_exactly_one_metadata_only_event(telemetry, path, op):
    """REGRESSION (2026-08-02 usage audit, F8). `whoami` (38 calls) and `health`
    (15) appeared NOWHERE in activity.events — 53 invocations invisible to the only
    structured source, and they are the ops the skill tells you to run FIRST.

    The count is asserted as a DELTA (0 before → exactly 1 after), not as "there is
    a row": a post-hoc existence check would pass on any pre-existing event, and
    this suite's own connection-wait helper used to inject /health events.

    Metadata-only is asserted positively AND negatively: the payload must be
    exactly {op,key,outcome} — in particular NO `domain`, because these endpoints
    are global (they describe every connected profile at once) and per-profile
    domains would widen the privacy contract at server.py's PRIVACY CONTRACT.
    """
    spool_dir = telemetry
    srv, _ = _serve(poll_timeout=5.0)
    ext = FakeExtension(srv, instance_id="a", label="alpha")
    ext.start()
    try:
        assert _wait_count(srv, 1) is not None      # silent: /instances
        assert _read_events(spool_dir) == [], "the negative control: 0 before"

        st, body = _req(srv, "GET", path)
        assert st == 200 and body["ok"] is True

        evs = _wait_events(spool_dir, 1)
        assert len(evs) == 1, f"expected exactly one event, got {evs}"
        e = evs[0]
        assert e["source"] == "browser-bridge" and e["kind"] == "cmd"
        assert e["exit_code"] == "0"
        assert e["text"] == op                      # no domain → text is the op
        p = json.loads(e["payload"])
        # Exact equality still, so nothing can creep in unnoticed. `sess_src` is
        # here because these are OPERATOR calls and are now attributed like any
        # other command (see test_only_the_heartbeat_is_server_originated); this
        # request sends no X-Session-Id, so the honest tier is `unknown`.
        assert p == {"op": op, "key": "", "outcome": "ok",
                     "sess_src": "unknown"}, p
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_orientation_emit_never_breaks_the_response(telemetry, monkeypatch):
    """BEST-EFFORT contract: a broken emitter must not fail /whoami or /health.

    Mutation-proof for the try/except in emit_cmd_event as reached from the NEW
    call site: force the emitter to raise and assert both endpoints still 200.
    """
    class _Boom:
        @staticmethod
        def emit(_rec):
            raise RuntimeError("spool exploded")

    monkeypatch.setattr(S, "_load_spool_emit", lambda: _Boom)
    srv, _ = _serve()
    try:
        for path in ("/whoami", "/health"):
            st, body = _req(srv, "GET", path)
            assert st == 200 and body["ok"] is True, path
    finally:
        srv.shutdown(); srv.server_close()


def test_emit_cmd_event_noop_when_emitter_missing(monkeypatch):
    """emit_cmd_event is a silent no-op (never raises) when the emitter can't be
    loaded — the collector not being checked out just disables telemetry."""
    monkeypatch.setattr(S, "_load_spool_emit", lambda: None)
    S.emit_cmd_event(op="tabs", key="", outcome="ok", duration_ms=1)


def test_load_spool_emit_missing_path_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "_SPOOL_EMIT_PATH", tmp_path / "nope.py")
    monkeypatch.setattr(S, "_spool_emit_mod", None)
    monkeypatch.setattr(S, "_spool_emit_tried", False)
    assert S._load_spool_emit() is None
    # 🔴 "TRIED" MEANS TRIED, NOT "SUCCEEDED". The flag must be set even on the
    # failure path — see the behavioural guard below for what goes wrong if a
    # later edit tucks it into the success branch.
    assert S._spool_emit_tried is True, (
        "a FAILED import left `_spool_emit_tried` False, so the next emit will "
        "retry it — under `_spool_emit_lock`, on every request")


def test_a_failed_emitter_import_is_never_retried(monkeypatch, tmp_path):
    """🔴 THE FAILURE PATH LATCHES — and without this nothing said so.

    `_spool_emit_tried` is set unconditionally, so an emitter that cannot be
    imported disables telemetry once and stays disabled. That is the documented
    supported configuration: "collector not checked out -> telemetry simply off".

    UNPINNED UNTIL NOW, and measured: the mutant `_spool_emit_tried = mod is not
    None` — tucking the flag into the success branch — SURVIVED all 785 tests in
    this directory. Under it every single emit re-attempts a failing import, and
    since this PR each of those attempts serializes on `_spool_emit_lock`. That
    makes a lock added to stop dropped events strictly WORSE than no lock, on a
    configuration the code says it supports, with no test to say so.

    Pinned BEHAVIOURALLY — the number of import ATTEMPTS — not by reading the
    flag, because the flag is the proxy and the retry is the harm.
    """
    attempts = tmp_path / "import-attempts"
    emitter = tmp_path / "broken_spool_emit.py"
    emitter.write_text(
        f"with open({str(attempts)!r}, 'a') as _f:\n"
        "    _f.write('x')\n"
        "raise RuntimeError('emitter is broken')\n",
        encoding="utf-8")
    monkeypatch.setattr(S, "_SPOOL_EMIT_PATH", emitter)
    monkeypatch.setattr(S, "_spool_emit_mod", None)
    monkeypatch.setattr(S, "_spool_emit_tried", False)

    assert S._load_spool_emit() is None
    assert attempts.read_text() == "x", "positive control: the import never ran"
    for _ in range(3):
        assert S._load_spool_emit() is None
    assert attempts.read_text() == "x", (
        f"the broken emitter was imported {len(attempts.read_text())} times — a "
        "failed import must latch, not retry on every emit")


# --------------------------------------------------------------------------- #
# The session JOIN KEY on browser-bridge telemetry rows.
#
# THE BUG (measured 2026-08-18): every `source='browser-bridge'` row had an EMPTY
# `session` column — 0 of 6,937 over 14 days — while claude/opencode/keys/tmux/
# zsh filled it 100%. The value already reached the server (X-Session-Id, used
# for tab-ownership routing) and was simply never handed to emit_cmd_event, so
# "which agent session made this browser call" was unanswerable from the one
# structured source and had to be answered by scanning 1.5M transcript records.
#
# THE TWO HAZARDS THE FIX MUST NOT CREATE:
#   1. TIER. The id is TAGGED with the fallback tier that produced it, and only
#      `claude:` is a join key. `tmux:%3` is stable across many unrelated
#      sessions in one pane, so recording it would silently merge them into one
#      apparent session — worse than the empty column.
#   2. NESTING. `browser agent` forwards its INVOKER's id, so a nested run's
#      commands arrive wearing the operator's own `claude:` tag. Attributed
#      naively, one `browser agent` call becomes N calls credited to the
#      operator's session — fabricated rows in the `session` JOIN column. (NOT,
#      as an earlier draft said, "the column adoption-scan reads": adoption-scan
#      never selects `session` for this source, and the deadman reads only row
#      existence. The harm is latent — it corrupts the first consumer that does
#      read it.) The nested tool declares X-Session-Origin, and the
#      forwarded id is then recorded as the causal PARENT, not the actor.
#
# FIXTURE DISCIPLINE: every id below is pairwise distinct AND distinct from every
# constant the assertions name ("claude", "tmux", "unknown", "browser-agent", …),
# so a mutant that hardcodes any of those literals cannot survive.
# --------------------------------------------------------------------------- #
JOINABLE_UUID = "6f1c9a20-1111-4bbb-8ccc-000000000001"
JOINABLE_ID = "claude:" + JOINABLE_UUID
NESTED_UUID = "8ab3d704-2222-4eee-9fff-000000000002"
NESTED_ID = "claude:" + NESTED_UUID
# A Claude session uuid that LEAKED into an opencode tool shell. It arrives
# `claude:`-tagged and is INDISTINGUISHABLE from a direct call by inspection --
# the id is genuinely the outer session's. Only the origin header separates them,
# which is exactly why the fix is a header and not a tier. Distinct from both
# uuids above so a mutant reusing either cannot satisfy its tests.
LEAKED_UUID = "c05e17b6-3333-4aaa-8ddd-000000000003"
LEAKED_ID = "claude:" + LEAKED_UUID
# The complete set of origin tokens any caller may declare. Pinned as a LEDGER:
# both mean "issued by something nested under origin_session", and both must
# suppress the session key identically. A third one added without a test here
# fails the ledger rather than silently getting a different behaviour.
ORIGIN_TOKENS = ("browser-agent", "opencode-inherited")
# An opencode session id, exported into the bash tool by
# scripts/opencode/plugin/session-env.js's `shell.env` hook. It JOINS for the
# same reason the claude one does: `source='opencode'` rows in activity.events
# store exactly this string (activity-plugin.js emits `session: input.sessionID`
# from `tool.execute.after`, the same id `shell.env` receives). Deliberately NOT
# uuid-shaped — the tag is what makes it joinable, never the form, and a
# differently-shaped id is the fixture that would catch a shape check sneaking in.
OC_SESSION = "ses_5d9e2b71a4"
OC_ID = "opencode:" + OC_SESSION
# tier tag -> (wire id, the bare id behind it). Mirrors derive_session_id's tags.
TIER_IDS = {
    "claude": (JOINABLE_ID, JOINABLE_UUID),
    "opencode": (OC_ID, OC_SESSION),
    "tmux": ("tmux:%77", "%77"),
    "sid": ("sid:424242:99887766", "424242:99887766"),
    "ppid": ("ppid:31337:deadbeefcafef00d", "31337:deadbeefcafef00d"),
    "synthetic": ("synthetic:" + JOINABLE_ID + "+recreate-close",
                  JOINABLE_ID + "+recreate-close"),
}


def _cmd_sess(srv, body, sid=None, origin=None):
    """POST /cmd with explicit session headers; None omits the header entirely."""
    hdrs = {}
    if sid is not None:
        hdrs[S.HDR_SESSION_ID] = sid
    if origin is not None:
        hdrs[S.HDR_SESSION_ORIGIN] = origin
    return _req(srv, "POST", "/cmd", body, headers=hdrs or None)


# The tiers that MAY fill the join column, as a LITERAL contract — never read off
# `S.SESSION_JOINABLE_TIERS`, which would make every row below assert whatever
# the implementation currently believes. Whether the server's set equals this one
# is a separate question, pinned in test_browser_session_id.py against tags
# PARSED from the CLI.
JOINABLE_TIERS_EXPECTED = {"claude", "opencode"}


@pytest.mark.parametrize("tier", sorted(TIER_IDS))
def test_session_column_is_filled_for_the_joinable_tiers_only(telemetry, tier):
    """Each tier reports its own `sess_src`; only a JOINABLE tier fills `session`.

    Both halves matter and are asserted together per tier:
      * `sess_src` is always present, so a row is self-describing about WHY it
        does or does not carry a key;
      * `session` is the BARE id for the joinable tier and ABSENT for every other
        — asserted as "the key is not in the record", not "it is empty", because
        an empty column and a merged-sessions column are the two outcomes this
        test exists to keep apart.
    """
    spool_dir = telemetry
    wire_id, bare = TIER_IDS[tier]
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only")
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _read_events(spool_dir) == [], "the negative control: 0 before"
        st, body = _cmd_sess(srv, {"op": "tabs"}, sid=wire_id)
        assert st == 200 and body["ok"] is True

        evs = _wait_events(spool_dir, 1)
        assert len(evs) == 1, evs
        e = evs[0]
        assert e["source"] == "browser-bridge"
        # The USAGE signal (adoption-scan.py) is untouched by this change.
        assert e["kind"] == "cmd"
        p = json.loads(e["payload"])
        assert p["sess_src"] == tier, p
        if tier in JOINABLE_TIERS_EXPECTED:
            assert e.get("session") == bare, e
        else:
            assert "session" not in e, (
                f"tier {tier!r} must NOT produce a join key; got {e.get('session')!r}")
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_the_session_column_carries_the_bare_id_not_the_wire_tag(telemetry):
    """`session` must equal what `source='claude'` rows store — the BARE uuid.

    The CLI tags its routing id `claude:<uuid>` so tiers cannot collide on the
    wire. Storing that tag in the COLUMN would make browser-bridge the one source
    needing a replaceOne() at every join site, and a forgotten one returns zero
    rows — which reads as a valid "no sessions matched" answer.
    """
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _cmd_sess(srv, {"op": "tabs"}, sid=JOINABLE_ID)[0] == 200
        e = _wait_events(spool_dir, 1)[0]
        assert e["session"] == JOINABLE_UUID
        assert ":" not in e["session"], e["session"]
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_only_the_first_colon_splits_a_multi_colon_id(telemetry):
    """`sid:` and `ppid:` ids contain further colons. Splitting on the LAST one
    (or on every one) would report a tier of `424242` and mangle the id — this
    pins that the tag is the FIRST field and the remainder is kept whole."""
    spool_dir = telemetry
    wire_id, bare = TIER_IDS["sid"]
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _cmd_sess(srv, {"op": "tabs"}, sid=wire_id)[0] == 200
        p = json.loads(_wait_events(spool_dir, 1)[0]["payload"])
        assert p["sess_src"] == "sid"
        assert S._split_session_id(wire_id) == ("sid", bare)
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


@pytest.mark.parametrize("untagged", ["", None])
def test_an_absent_or_empty_id_fails_closed(telemetry, untagged):
    """No id at all: the event still lands (usage is still usage), reports the
    unknown tier, and carries no join key."""
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _cmd_sess(srv, {"op": "tabs"}, sid=untagged)[0] == 200
        e = _wait_events(spool_dir, 1)[0]
        assert e["kind"] == "cmd"
        assert json.loads(e["payload"])["sess_src"] == "unknown"
        assert "session" not in e, e
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_an_untagged_id_is_never_promoted_to_a_join_key(telemetry):
    """🔴 THE FAIL-CLOSED CASE THAT LOOKS MOST LIKE A JOIN KEY. A bare uuid with
    no tier tag is exactly the value it would be tempting to accept — and a
    version-skewed or hand-rolled caller can send anything (the opencode tool's
    own default is the literal "browser-agent"). Provenance is stated or it is
    unknown; it is never inferred from the value's FORM."""
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _cmd_sess(srv, {"op": "tabs"}, sid=JOINABLE_UUID)[0] == 200
        e = _wait_events(spool_dir, 1)[0]
        p = json.loads(e["payload"])
        assert p["sess_src"] == "unknown", p
        assert p["sess_src"] not in TIER_IDS, "must not spell a real tier"
        assert "session" not in e, e
        # And the id itself is nowhere on the row, in any field.
        assert JOINABLE_UUID not in _log_file(spool_dir).read_text()
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


@pytest.mark.parametrize("bogus_tag", [
    "futuretier",      # a tier that does not exist yet
    "CLAUDE",          # the joinable tag, wrong case
    "claud",           # a near-miss
    "['claude",        # the shape a mangled caller actually produced
])
def test_a_tag_outside_the_vocabulary_is_normalised_not_recorded(telemetry, bogus_tag):
    """🔴 `sess_src` IS A CLOSED SET, NOT FREE TEXT. It arrives on a
    caller-supplied header, so recording it verbatim makes it an
    unbounded-cardinality column: every string below was measured landing in it
    unchanged, and four of the five are near-misses of the joinable tag that a
    reader skimming a GROUP BY would misread as the real thing.

    Anything outside SESSION_TIER_TAGS collapses to `unknown` and carries NO id.
    A genuinely new tier needs a CLI change, which the two-way vocabulary pin in
    test_browser_session_id.py already forces someone to declare.
    """
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _cmd_sess(srv, {"op": "tabs"},
                         sid=bogus_tag + ":" + JOINABLE_UUID)[0] == 200
        e = _wait_events(spool_dir, 1)[0]
        p = json.loads(e["payload"])
        assert p["sess_src"] == "unknown", p
        assert "session" not in e, e
        # Neither half of an unrecognised id may survive anywhere on the row.
        assert JOINABLE_UUID not in _log_file(spool_dir).read_text()
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


@pytest.mark.parametrize("padded", [" claude", "claude ", "\tclaude"])
def test_a_whitespace_padded_tag_is_rejected_at_the_unit(padded):
    """Unit-level because these CANNOT travel: an HTTP header value is delimited
    by the colon-space, so `X-Session-Id:  claude:<uuid>` arrives as
    `claude:<uuid>` with the padding already gone -- measured, and it is why this
    case is not in the HTTP table above. The validator is still what decides, and
    a padded tag must not slip through if a future caller reaches the emitter by
    another route.

    Paired with its own control so a validator that rejected EVERYTHING would be
    caught rather than looking correct."""
    assert S._split_session_id(padded + ":" + JOINABLE_UUID) == ("unknown", "")
    assert S._split_session_id(JOINABLE_ID) == ("claude", JOINABLE_UUID)


def test_the_tier_vocabulary_holds_its_internal_invariants():
    """The SELF-CONSISTENCY half only. Whether the set matches the CLI is pinned
    in test_browser_session_id.py::
    test_the_server_validation_set_equals_the_tags_parsed_from_the_cli, which
    compares it against tags PARSED from the CLI source.

    🔴 The literal that used to live here has been DELETED, deliberately. It read
    as a cross-file pin and was not one: retyping the CLI's tags beside the
    server's meant a change touching both real files sailed through, and a delta
    audit measured exactly that (grow the CLI + its own ledger, leave the server
    alone -> 400 passed, SURVIVED). A literal that can be updated in lockstep
    with the thing it checks is worse than no check, because it reads as one."""
    assert set(S.SESSION_JOINABLE_TIERS) <= set(S.SESSION_TIER_TAGS), (
        "a joinable tier that is not in the vocabulary can never be reached — "
        "_split_session_id normalises it to unknown before the gate is asked")
    assert S.SESSION_SRC_UNKNOWN not in S.SESSION_TIER_TAGS, (
        "the fallback marker must not also be a real tier -- an unparseable id "
        "and a genuine tier would become indistinguishable")
    assert S.SESSION_SRC_UNKNOWN not in S.SESSION_JOINABLE_TIERS


# --- nesting: the forwarded id is the causal PARENT, not the actor --------- #
def test_a_nested_run_records_the_forwarded_id_as_origin_not_as_session(telemetry):
    """`browser agent` forwards its INVOKER's `claude:` id. Attributed naively,
    one agent call becomes N calls credited to the operator's own session —
    fabricated rows in the `session` JOIN column (~11% of bridge commands over
    14d). Not "the column adoption-scan reads" -- it does not read this one; the
    harm is latent, landing on the first consumer that does. The origin declaration moves it to `origin_session`, where nothing
    can mistake the parent for the actor.

    The id is DISTINCT from the ordinary-request fixture below, so a mutant that
    wrote whichever id it had lying around cannot satisfy both.
    """
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _cmd_sess(srv, {"op": "tabs"}, sid=NESTED_ID,
                         origin="browser-agent")[0] == 200
        e = _wait_events(spool_dir, 1)[0]
        p = json.loads(e["payload"])
        assert "session" not in e, f"a nested run must not claim the session: {e}"
        assert p["origin"] == "browser-agent", p
        assert p["origin_session"] == NESTED_UUID, p
        assert p["sess_src"] == "claude", p   # the tier of the forwarded id
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_an_ordinary_request_sets_session_and_declares_no_origin(telemetry):
    """The other side of the nesting fork, on the SAME request shape: a direct
    call fills `session` and adds NO origin fields. Without this pair, a change
    that dropped attribution entirely would still satisfy the nested test."""
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _cmd_sess(srv, {"op": "tabs"}, sid=JOINABLE_ID)[0] == 200
        e = _wait_events(spool_dir, 1)[0]
        p = json.loads(e["payload"])
        assert e["session"] == JOINABLE_UUID, e
        assert "origin" not in p, p
        assert "origin_session" not in p, p
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_an_origin_wins_over_the_joinable_tier_on_every_emit_site(telemetry):
    """The three /cmd emit call sites are three places to forget the fork. All
    three are exercised with a joinable tier PLUS an origin; none may write a
    session key."""
    spool_dir = telemetry
    # 1. the `release` short-circuit (returns before submit).
    srv, _ = _serve()
    try:
        assert _cmd_sess(srv, {"op": "release"}, sid=NESTED_ID,
                         origin="browser-agent")[0] == 200
        e = _wait_events(spool_dir, 1)[0]
        assert "session" not in e, e
        assert json.loads(e["payload"])["origin_session"] == NESTED_UUID
    finally:
        srv.shutdown(); srv.server_close()

    # 2. the throttle path, and 3. the terminal emit.
    reg = S.Registry(rate_per_sec=0.001, burst=1, max_queue=1000)
    srv, _ = _serve(registry=reg)
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _cmd_sess(srv, {"op": "tabs"}, sid=NESTED_ID,
                         origin="browser-agent")[0] == 200          # terminal
        assert _cmd_sess(srv, {"op": "tabs"}, sid=NESTED_ID,
                         origin="browser-agent")[0] == 429          # throttled
        evs = _wait_events(spool_dir, 3)[1:]
        assert len(evs) == 2, evs
        for e in evs:
            assert "session" not in e, e
            assert json.loads(e["payload"])["origin"] == "browser-agent"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_the_release_short_circuit_attributes_an_ordinary_session(telemetry):
    """`release` returns BEFORE the submit path, from its own emit call site. A
    second call site is a second place to forget the attribution — pin it."""
    spool_dir = telemetry
    srv, _ = _serve()
    try:
        st, body = _cmd_sess(srv, {"op": "release"}, sid=JOINABLE_ID)
        assert st == 200 and body["ok"] is True
        e = _wait_events(spool_dir, 1)[0]
        assert json.loads(e["payload"])["sess_src"] == "claude"
        assert e["session"] == JOINABLE_UUID
    finally:
        srv.shutdown(); srv.server_close()


def test_the_throttle_path_carries_both_the_hash_and_the_join_key(telemetry):
    """The throttle emit is the THIRD call site. `sess` (the coarse hash) is KEPT
    on purpose beside the new `session` column: the column is filled for the
    joinable tier and non-nested calls only, and a flood from a tmux/sid/unknown
    tier or a nested agent run is exactly the case where you still need some
    stable handle to tell one flooder from two."""
    spool_dir = telemetry
    reg = S.Registry(rate_per_sec=0.001, burst=1, max_queue=1000)
    srv, _ = _serve(registry=reg)
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _cmd_sess(srv, {"op": "tabs"}, sid=JOINABLE_ID)[0] == 200
        assert _cmd_sess(srv, {"op": "tabs"}, sid=JOINABLE_ID)[0] == 429
        # `until=` not `n=2` — the count is a proxy for "the throttled event
        # landed". 🔴 But the CI failure this test kept producing was NOT a slow
        # wait: the row was being DROPPED by the emitter-load race (see
        # `_wait_events` and server.py's `_spool_emit_lock`). Do not widen the
        # deadline if this reds again — read the spool for what is MISSING.
        def _has_throttle(evs):
            return any(json.loads(e["payload"]).get("outcome") == "throttled"
                       for e in evs)

        evs = _wait_events(spool_dir, until=_has_throttle)
        thr = [e for e in evs
               if json.loads(e["payload"]).get("outcome") == "throttled"]
        assert len(thr) == 1, evs
        p = json.loads(thr[0]["payload"])
        assert p["sess"] == hashlib.sha256(JOINABLE_ID.encode()).hexdigest()[:8]
        assert p["sess_src"] == "claude"
        assert thr[0]["session"] == JOINABLE_UUID
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# The lazy emitter load is CONCURRENT — see server.py's `_spool_emit_lock`.
# --------------------------------------------------------------------------- #
def _gated_emitter(tmp_path):
    """A copy of the REAL spool emitter whose IMPORT blocks until released.

    Returns `(path, release, counts)`. The gate is APPENDED to the genuine
    `spool_emit.py` source — so `emit` behaves identically, and so the real file's
    `from __future__` line keeps its mandatory first-statement position. It
    records one character per import in `counts` and then blocks. That turns
    "another caller arrives while the emitter is still loading" from a scheduling
    accident into an ORDER THE TEST CHOOSES, which is the only way to pin this
    without a wall-clock dependency.

    The wait is capped so a regression can never hang the suite: it gives up and
    lets the import finish, and the assertion (not a timeout) is what reports.
    """
    go = tmp_path / "emitter-go"
    counts = tmp_path / "emitter-imports"
    src = SPOOL_EMIT_PY.read_text(encoding="utf-8") + (
        "\n\n# --- test gate (appended by _gated_emitter) ---\n"
        "import time as _t\n"
        f"with open({str(counts)!r}, 'a') as _f:\n"
        "    _f.write('x')\n"
        f"_gate = Path({str(go)!r})\n"
        "_deadline = _t.time() + 30\n"
        "while not _gate.exists() and _t.time() < _deadline:\n"
        "    _t.sleep(0.005)\n"
    )
    path = tmp_path / "gated_spool_emit.py"
    path.write_text(src, encoding="utf-8")
    return path, (lambda: go.write_text("go", encoding="utf-8")), counts


def test_the_emitter_load_publishes_the_module_before_it_claims_to_have_tried(
        telemetry, tmp_path, monkeypatch):
    """🔴 THE ORDERING CONTRACT, and the root cause of the #1 CI failure.

    `_load_spool_emit`'s unlocked fast path is `if _spool_emit_tried: return
    _spool_emit_mod`. It used to set `_spool_emit_tried = True` BEFORE running the
    import, so for the whole duration of that import a second caller read
    "already tried" and was handed a still-`None` module — and `emit_cmd_event`
    returned at its `if se is None` guard. The event was DROPPED, permanently.
    Not delayed: no deadline could ever have recovered it.

    That is not a rare interleave. Every /cmd emits AFTER its response is sent,
    so request N's load overlaps request N+1's handler by construction. A probe
    of two threads reaching the cold function together lost the event in 35 of 40
    trials at zero stagger and 1 of 40 at a 0.5ms stagger (24-core workbench,
    load average ~42-49); 0 of 40 at every point after the fix. The full paired
    numbers are in server.py beside `_spool_emit_lock`.

    Pinned as STATE, not as a word: while the import is provably in flight,
    `_spool_emit_tried` must still be False.
    """
    emitter, release, counts = _gated_emitter(tmp_path)
    monkeypatch.setattr(S, "_SPOOL_EMIT_PATH", emitter)
    monkeypatch.setattr(S, "_spool_emit_mod", None)
    monkeypatch.setattr(S, "_spool_emit_tried", False)

    got = {}
    t = threading.Thread(target=lambda: got.update(mod=S._load_spool_emit()))
    t.start()
    try:
        # Deterministic handshake: the counts file is written by the gate at the
        # END of the module body, immediately before it blocks — so its existence
        # proves the loader is inside exec_module and has NOT returned.
        assert _wait_until(counts.exists, timeout=30), (
            "the gated import never started")
        assert S._spool_emit_tried is False, (
            "`_spool_emit_tried` was published while `_spool_emit_mod` is still "
            f"{S._spool_emit_mod!r} — a concurrent caller reading the fast path "
            "here is handed None and silently drops its event")
    finally:
        release()
        t.join(timeout=30)
    assert not t.is_alive()
    assert got["mod"] is not None
    assert S._spool_emit_tried is True and S._spool_emit_mod is got["mod"]


def test_a_second_command_emitting_during_the_emitter_load_still_spools(
        telemetry, tmp_path, monkeypatch):
    """🔴 THE CI FAILURE ITSELF, made deterministic.

    `test_the_throttle_path_carries_both_the_hash_and_the_join_key` failed on 3
    of the 29 `devrc-ci` runs after 2026-08-24 — the single most frequent red in
    that window — always the same way: the `throttled` row absent from the spool
    while the server's captured stderr proved it HAD throttled. The two /cmd
    handler threads were racing the lazy emitter load, and the loser's event was
    dropped.

    Here the race is SCHEDULED rather than hoped for: the emitter's import blocks,
    and the second command is not issued until the first command's load is
    provably in flight and the second's emit is provably inside the loader. So
    this is red at the pre-fix ordering EVERY run, not one in ten.

    Note what is NOT weakened: the wait is the same `until=_has_throttle`, and the
    row must still carry its payload. A fix that made this pass by asserting less
    would be worse than the flake.
    """
    spool_dir = telemetry
    emitter, release, counts = _gated_emitter(tmp_path)
    monkeypatch.setattr(S, "_SPOOL_EMIT_PATH", emitter)
    monkeypatch.setattr(S, "_spool_emit_mod", None)
    monkeypatch.setattr(S, "_spool_emit_tried", False)

    real_load = S._load_spool_emit
    entered = threading.Semaphore(0)

    def _counting_load():
        # Recorded BEFORE the call so "the second emitter has reached the loader"
        # is observable from the test thread. The RESULT still comes from the
        # real function — this wrapper orchestrates, it never answers.
        entered.release()
        return real_load()

    monkeypatch.setattr(S, "_load_spool_emit", _counting_load)

    reg = S.Registry(rate_per_sec=0.001, burst=1, max_queue=1000)
    srv, _ = _serve(registry=reg)
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _cmd_sess(srv, {"op": "tabs"}, sid=JOINABLE_ID)[0] == 200
        # The ok-command's emit is the one that performs the load. Wait until it
        # is inside the (blocked) import before provoking the throttle.
        assert entered.acquire(timeout=30), "the first emit never reached the loader"
        assert _wait_until(counts.exists, timeout=30), (
            "the gated import never started")

        assert _cmd_sess(srv, {"op": "tabs"}, sid=JOINABLE_ID)[0] == 429
        assert entered.acquire(timeout=30), "the throttled emit never reached the loader"
    finally:
        release()

    def _has_throttle(evs):
        return any(json.loads(e["payload"]).get("outcome") == "throttled"
                   for e in evs)

    try:
        evs = _wait_events(spool_dir, until=_has_throttle)
        thr = [e for e in evs
               if json.loads(e["payload"]).get("outcome") == "throttled"]
        assert len(thr) == 1, evs
        p = json.loads(thr[0]["payload"])
        assert p["sess"] == hashlib.sha256(JOINABLE_ID.encode()).hexdigest()[:8]
        assert thr[0]["session"] == JOINABLE_UUID
        # ONCE-ONLY under a race is the lock's own job — the ordering alone would
        # let the second caller redo the import.
        assert counts.read_text() == "x", (
            f"the emitter was imported {len(counts.read_text())} times under a "
            "concurrent first load; `_spool_emit_lock` is not holding")
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_a_raising_emitter_still_lets_the_command_succeed(telemetry, monkeypatch):
    """BEST-EFFORT CONTRACT, re-proved through the NEW code path: the attribution
    branch runs inside emit_cmd_event's swallowing try, so an emitter that
    explodes while attributing must still leave /cmd at 200.

    Four header shapes are exercised — joinable, non-joinable, nested, and none
    at all — because a guard that only holds on the path you thought about is not
    a guard."""
    class _Boom:
        @staticmethod
        def emit(_rec):
            raise RuntimeError("spool exploded")

    monkeypatch.setattr(S, "_load_spool_emit", lambda: _Boom)
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        for sid, origin in ((JOINABLE_ID, None), (TIER_IDS["tmux"][0], None),
                            (NESTED_ID, "browser-agent"), (None, None)):
            st, body = _cmd_sess(srv, {"op": "tabs"}, sid=sid, origin=origin)
            assert st == 200 and body["ok"] is True, (sid, origin)
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_an_oversized_id_is_dropped_whole_never_truncated(telemetry):
    """A truncated join key is a WRONG join key — it silently attributes a call to
    a different session. So an id past the sanity bound is dropped ENTIRELY.

    The paired control is the point: the SAME request shape with a well-formed id
    DOES produce a key, so this cannot pass by the emitter being wired to nothing.
    """
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        # Positive control first: a good id on this exact path yields a key.
        assert _cmd_sess(srv, {"op": "tabs"}, sid=JOINABLE_ID)[0] == 200
        good = _wait_events(spool_dir, 1)[0]
        assert good["session"] == JOINABLE_UUID, "positive control: no key emitted"

        over = "claude:" + ("x" * (S.MAX_SESSION_FIELD + 1))
        assert _cmd_sess(srv, {"op": "tabs"}, sid=over)[0] == 200
        evs = _wait_events(spool_dir, 2)
        assert "session" not in evs[1], evs[1]
        assert json.loads(evs[1]["payload"])["sess_src"] == "unknown", evs[1]
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


@pytest.mark.parametrize("bad", ["has\ttab", "has\nnewline", "has\x00nul",
                                 "has\x7fdel"])
def test_a_control_character_id_is_dropped_at_the_unit(bad):
    """Unit-level because these cannot travel: http.client refuses a header value
    containing a newline, so the wire cannot deliver one. The sanitiser is still
    what decides, and a control character in a column other tools compare with
    `=` is an unreadable handle — drop it whole.

    Paired with its own control, so a sanitiser that rejected EVERYTHING would be
    caught rather than looking correct."""
    assert S._clean_session_field(bad) == ""
    assert S._split_session_id("claude:" + bad) == ("unknown", "")
    assert S._split_session_id(JOINABLE_ID) == ("claude", JOINABLE_UUID)  # control


def test_extra_cannot_overwrite_the_attribution(telemetry):
    """The attribution fields are written AFTER the `extra` merge, so an internal
    call site cannot smuggle a tier or an origin the headers did not send.
    Unit-level: `extra` is not caller-reachable today, and this pins that it stays
    uncontestable if it ever becomes so."""
    spool_dir = telemetry
    S.emit_cmd_event(op="tabs", key="", outcome="ok", duration_ms=1,
                     extra={"sess_src": "claude", "origin": "spoofed"},
                     session_id=TIER_IDS["tmux"][0], attribute_session=True)
    e = _read_events(spool_dir)[0]
    p = json.loads(e["payload"])
    assert p["sess_src"] == "tmux", p
    assert "origin" not in p, p
    assert "session" not in e, e


# --------------------------------------------------------------------------- #
# The opencode leak, server side.
#
# `CLAUDE_CODE_SESSION_ID` survives into opencode's tool shells (opencode sets
# OPENCODE=1 in a yargs top-level `.middleware()` and hands its tools
# `{...process.env}` -- confirmed in the PINNED build, see PINNED_VERSION in
# scripts/tests/test_opencode_engine.py; a live env dump showed the outer Claude
# id still present). So a plain `opencode run …` whose bash tool shells out to
# `browser` sends a genuinely `claude:`-tagged id that names an ANCESTOR.
#
# 🔴 THE ID IS INDISTINGUISHABLE FROM A DIRECT CALL. There is nothing in it to
# branch on -- it IS the outer session's id, correctly tagged. That is precisely
# why the fix is the ORIGIN HEADER and not a new tier: the server cannot tell
# these apart by inspection, so the caller has to say so. It is the same question
# `browser agent` already answers the same way, and one question gets one
# mechanism.
#
# 🔴 HONEST LABEL: everything in this section is an INVARIANT GUARD, green at
# BOTH f47be59 (which introduced the origin path) and 84bf324. The server needed
# NO change for the opencode leak -- the origin path already did the right thing,
# and reusing it rather than adding a parallel one is the entire point of this
# round. These tests pin that BOTH origin tokens behave identically and that a
# leaked-but-genuinely-claude-tagged id composes with the tier gate; they are not
# regression coverage for a server bug. They ARE red against origin/main
# (da33356), where the origin header does not exist. Reachability is proved by
# mutants N7 (origin never detected) and N8 (the token collapsed to a constant).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("token", ORIGIN_TOKENS)
def test_every_origin_token_suppresses_the_session_key(telemetry, token):
    """LEDGER + BEHAVIOUR. Both declared origins mean the same thing -- "issued by
    something nested under this id" -- so both must suppress `session` and record
    the id as the causal parent instead.

    Parametrized over the ledger rather than written once per token: a third
    token added to ORIGIN_TOKENS without server support fails here, and a token
    that got special-cased into filling `session` fails here too.

    The id is `claude:`-tagged on purpose. A non-joinable tier would suppress
    `session` on its own, so the test would pass with the origin logic deleted --
    the joinable tier is the only fixture that can distinguish the two.
    """
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _cmd_sess(srv, {"op": "tabs"}, sid=LEAKED_ID, origin=token)[0] == 200
        e = _wait_events(spool_dir, 1)[0]
        p = json.loads(e["payload"])
        assert "session" not in e, f"origin {token!r} must claim no session: {e}"
        assert p["origin"] == token, p
        assert p["origin_session"] == LEAKED_UUID, p
        assert ":" not in p["origin_session"], p
        # The tier is still reported honestly -- the id really is claude-tagged.
        assert p["sess_src"] == "claude", p
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_the_same_id_fills_the_session_when_no_origin_is_declared(telemetry):
    """🔴 THE CONTROL FOR THE WHOLE MECHANISM, and the reason the fixture above is
    `claude:`-tagged. The EXACT SAME id on the EXACT SAME path, differing only in
    whether the origin header is present, must fill `session`.

    Without this pair, a server that had simply stopped writing `session`
    altogether would satisfy every origin test in this file.
    """
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _cmd_sess(srv, {"op": "tabs"}, sid=LEAKED_ID)[0] == 200
        e = _wait_events(spool_dir, 1)[0]
        assert e["session"] == LEAKED_UUID, e
        assert "origin" not in json.loads(e["payload"])
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_the_two_origin_tokens_are_distinct_and_recorded_verbatim(telemetry):
    """`origin` is a two-value enum and its VALUE carries the diagnosis: which
    nesting mechanism produced the row. Recording one token under the other's
    name would make the two populations unseparable in the column -- the same
    class of harm as the session key itself.

    Asserted as distinctness plus verbatim round-trip, so a mutant that collapsed
    them to a single constant dies here rather than in a test that only checks
    `origin` is truthy."""
    spool_dir = telemetry
    assert len(set(ORIGIN_TOKENS)) == len(ORIGIN_TOKENS), ORIGIN_TOKENS
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        for token in ORIGIN_TOKENS:
            assert _cmd_sess(srv, {"op": "tabs"}, sid=LEAKED_ID,
                             origin=token)[0] == 200
        evs = _wait_events(spool_dir, len(ORIGIN_TOKENS))
        seen = [json.loads(e["payload"])["origin"] for e in evs]
        assert seen == list(ORIGIN_TOKENS), seen
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# The ORIGIN path, hardened. Three findings from the blind audit of #549, all one
# shape: the origin path was more permissive than the id path beside it.
#
#   A1. The origin sanitiser failed OPEN. `if origin:` on the CLEANED value meant
#       an oversized or control-char origin cleaned to "" and fell through to the
#       `elif`, writing `session` WITH THE PARENT'S ID -- the exact fabrication
#       the mechanism exists to prevent. Measured: a 201-char origin produced
#       session='uuid-w'.
#   A2. `origin` accepted anything. `totally-made-up`, `false`, `   ` were all
#       recorded verbatim and all suppressed `session`. The "two-value enum" was
#       documentation, not a contract.
#   A3. `origin_session` bypassed the tier gate, and is REACHABLE: browser-agent
#       forwards whatever --print-session-id produced and the opencode tool
#       declares its origin unconditionally, so `tmux:%3` genuinely arrived and
#       was recorded as `origin_session='%3'`.
#
# The fix is one rule: branch on header PRESENCE, validate the value against the
# ledger, and put `origin_session` behind the SAME tier gate as `session`.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad_origin,label", [
    ("x" * (S.MAX_SESSION_FIELD + 1), "oversized"),
    ("", "empty"),
    ("   ", "whitespace"),
    ("totally-made-up", "unknown token"),
    ("false", "a word that looks like a negative"),
    ("unknown", "collides with the tier fallback marker"),
    ("BROWSER-AGENT", "a real token, wrong case"),
])
def test_an_unreadable_origin_still_suppresses_the_session(telemetry, bad_origin,
                                                           label):
    """🔴 THE A1/A2 REGRESSION. A present-but-unreadable origin must STILL
    suppress.

    The id is `claude:`-tagged, so if suppression were keyed off the cleaned
    value's truthiness -- as it was -- every row here would carry the PARENT's
    uuid in `session`. Fail closed: a caller that tried to disclaim authorship and
    could not be understood loses attribution rather than fabricating it.

    `unknown` and `BROWSER-AGENT` are in the table on purpose: the first collides
    with the tier fallback marker, the second is a real token in the wrong case --
    both are near-misses a `value in LEDGER` check must still reject.

    HONEST NOTE ON THE TABLE: over HTTP the `whitespace` row is NOT a distinct
    case from `empty`. A header value is delimited by the colon-space and trailing
    whitespace is stripped, so `X-Session-Origin:    ` arrives as `""`. Measured
    via mutant P6, which kills both rows identically. The row is kept because it
    documents what a caller writing whitespace actually gets, not because it
    exercises a separate branch.
    """
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _cmd_sess(srv, {"op": "tabs"}, sid=LEAKED_ID,
                         origin=bad_origin)[0] == 200
        e = _wait_events(spool_dir, 1)[0]
        p = json.loads(e["payload"])
        assert "session" not in e, (
            f"{label}: a present origin must suppress the key; "
            f"got {e.get('session')!r}")
        assert p["origin"] == "invalid", p
        assert p["origin"] not in ORIGIN_TOKENS, p
        # The parent id is still recorded -- suppression is not amnesia.
        assert p["origin_session"] == LEAKED_UUID, p
        # And no session field reached the line at all, under any name.
        assert "b64:session=" not in _log_file(spool_dir).read_text()
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_the_invalid_marker_is_distinguishable_from_every_real_token():
    """The marker exists so the failure is VISIBLE in the data rather than silent.
    It must not collide with a real token, or a malformed declaration would be
    indistinguishable from a working one when someone groups by `origin`."""
    assert S.SESSION_ORIGIN_INVALID == "invalid"
    assert S.SESSION_ORIGIN_INVALID not in S.ORIGIN_TOKENS


def test_the_origin_ledger_is_the_same_set_the_tests_pin():
    """SEAM. The server's ledger and this suite's expectation are two lists that
    nothing forces to agree; pin them, or a token added to one alone changes
    behaviour with a green suite."""
    assert tuple(S.ORIGIN_TOKENS) == ORIGIN_TOKENS, S.ORIGIN_TOKENS


@pytest.mark.parametrize("tier", ["tmux", "sid", "ppid", "synthetic"])
def test_origin_session_passes_the_same_tier_gate_as_session(telemetry, tier):
    """🔴 THE A3 REGRESSION, and it is REACHABLE rather than theoretical.

    browser-agent forwards whatever `--print-session-id` produced -- its own
    opencode-tool test exercises `BROWSER_AGENT_SESSION_ID: "tmux:%41"` -- and the
    tool declares the origin unconditionally. So a non-joinable parent id really
    does arrive here. The tier gate's own rationale applies verbatim: a pane id is
    stable across many unrelated sessions, so a reader grouping by
    `origin_session` would merge them exactly as they would on `session`.

    The tier is still recorded, so the suppressed population stays measurable --
    that is the difference between a gate and a silent drop.
    """
    spool_dir = telemetry
    wire_id, bare = TIER_IDS[tier]
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _cmd_sess(srv, {"op": "tabs"}, sid=wire_id,
                         origin="browser-agent")[0] == 200
        e = _wait_events(spool_dir, 1)[0]
        p = json.loads(e["payload"])
        assert "session" not in e, e
        assert "origin_session" not in p, (
            f"tier {tier!r} is not joinable and must not be recorded as a parent "
            f"key; got {p.get('origin_session')!r}")
        assert p["origin"] == "browser-agent", p
        assert p["sess_src"] == tier, p        # measurable, not silent
        # The bare id must not have leaked onto the row under any other name.
        assert bare not in _log_file(spool_dir).read_text()
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


@pytest.mark.parametrize("tier", sorted(JOINABLE_TIERS_EXPECTED))
def test_both_join_sites_answer_the_same_way_for_every_joinable_tier(telemetry, tier):
    """🔴 THE CONSOLIDATION GUARD. Joinability is asked at TWO places — `session`
    (no origin) and `origin_session` (origin declared) — and they were open-coded
    as the same comparison twice. Widening the joinable set from one tag to
    several is exactly the edit that lands on one copy and not the other, and the
    result is an id that is attributable in one field and silently dropped in the
    neighbouring one.

    Driven by the LEDGER, not by the server's own tuple, and it runs both arms
    for each tier IN ONE TEST, so a mutant that widens only `session` (or only
    `origin_session`) dies on whichever arm it did not touch. Distinct ids per
    tier, so nothing can pass by echoing a constant.

    BASELINES DIFFER PER ROW: `[opencode]` is RED at origin/main; `[claude]` is
    an INVARIANT GUARD there (base already answers both sites the same way for
    that one tier) and is what makes the widening measurable rather than assumed.
    """
    spool_dir = telemetry
    wire_id, bare = TIER_IDS[tier]
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        # ARM 1 — no origin: the actor is this session, so `session` is filled.
        assert _cmd_sess(srv, {"op": "tabs"}, sid=wire_id)[0] == 200
        direct = _wait_events(spool_dir, 1)[0]
        assert direct.get("session") == bare, direct
        assert "origin_session" not in json.loads(direct["payload"]), direct

        # ARM 2 — origin declared: the same id is now a causal PARENT, so it
        # moves to `origin_session` and `session` stays empty.
        assert _cmd_sess(srv, {"op": "tabs"}, sid=wire_id,
                         origin="browser-agent")[0] == 200
        nested = _wait_events(spool_dir, 2)[1]
        assert "session" not in nested, nested
        p = json.loads(nested["payload"])
        assert p["origin_session"] == bare, p
        assert p["origin"] == "browser-agent", p
        assert p["sess_src"] == tier, p
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


@pytest.mark.parametrize("tier,bare,want", [
    ("claude", JOINABLE_UUID, True),
    ("opencode", OC_SESSION, True),
    ("tmux", "%77", False),
    ("sid", "424242:99887766", False),
    ("ppid", "31337:deadbeefcafef00d", False),
    ("synthetic", "whatever-the-cli-made-up", False),
    ("unknown", "", False),
    # A joinable TAG with an empty bare half is not a key. Both halves are
    # required, and this is the row that keeps the `and bare` conjunct alive.
    ("claude", "", False),
    ("opencode", "", False),
])
def test_the_joinability_predicate_at_the_unit(tier, bare, want):
    """The predicate both emit sites go through, exercised directly so the
    boundary rows (empty bare half; every non-joinable tier) are cheap to keep.
    Paired True/False rows so a predicate stuck at either constant dies."""
    assert S._is_joinable(tier, bare) is want


def test_a_joinable_parent_is_still_recorded(telemetry):
    """The control for the gate above: the joinable tier still yields a parent
    key. Without this, deleting `origin_session` entirely would pass every test
    in this section."""
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _cmd_sess(srv, {"op": "tabs"}, sid=LEAKED_ID,
                         origin="browser-agent")[0] == 200
        p = json.loads(_wait_events(spool_dir, 1)[0]["payload"])
        assert p["origin_session"] == LEAKED_UUID, p
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_an_absent_origin_header_is_not_the_same_as_an_empty_one(telemetry):
    """PRESENCE vs VALUE, asserted as the pair that defines the rule.

    Absent -> attribute normally (the ordinary case; regressing it would re-empty
    the column this PR exists to fill). Present but empty -> suppress. They differ
    ONLY in whether the header is on the request, which is exactly what
    `session_origin is None` tests and what `or None` in the handler destroyed.
    """
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _cmd_sess(srv, {"op": "tabs"}, sid=LEAKED_ID)[0] == 200
        assert _cmd_sess(srv, {"op": "tabs"}, sid=LEAKED_ID, origin="")[0] == 200
        # Both rows are `tabs`, so ORDER between them is the signal and must be
        # preserved — but a neighbour's row must not be counted as one of them.
        absent, empty = _wait_ops(spool_dir, "tabs", 2)
        assert absent["session"] == LEAKED_UUID, absent
        assert "origin" not in json.loads(absent["payload"])
        assert "session" not in empty, empty
        assert json.loads(empty["payload"])["origin"] == "invalid"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_only_the_heartbeat_is_server_originated(telemetry):
    """🔴 CATEGORY CORRECTION. This test used to assert that the heartbeat AND the
    /whoami+/health diagnostics "have no caller session to attribute". That was
    true of the heartbeat and FALSE of the other two, and the wrong half was
    load-bearing: `browser whoami` / `browser health` are subcommands a person
    runs, and the CLI sends its ordinary session headers on them because `_curl`
    is one code path. 125 rows / 2.0% of `kind='cmd'` over 14d were being
    de-attributed on a false premise, while the SAME `whoami` reached via POST
    /cmd was attributed -- one operation, two outcomes.

    So the category now contains exactly one member. The heartbeat is emitted by a
    timer with no request behind it; a `sess_src` on it would be a claim about a
    caller that does not exist.
    """
    spool_dir = telemetry
    S.emit_heartbeat_event(S.Registry())
    # Selected by op, then cross-checked against `kind` — selection is on the
    # PAYLOAD op, so this assertion is not tautological.
    e = _wait_ops(spool_dir, "heartbeat", 1)[0]
    assert e["kind"] == "heartbeat"
    assert "session" not in e, e
    p = json.loads(e["payload"])
    assert "sess_src" not in p and "origin" not in p, e


def test_the_diagnostic_gets_are_attributed_like_any_operator_command(telemetry):
    """The other side of that correction: /whoami and /health are operator calls,
    so they attribute exactly like a POST /cmd.

    Asserted through the REAL HTTP path with real headers rather than by calling
    the emitter directly -- the bug was that the handler never passed the headers
    it already had, which a direct-call test cannot see.
    """
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        for path in ("/whoami", "/health"):
            st, _b = _req(srv, "GET", path,
                          headers={S.HDR_SESSION_ID: JOINABLE_ID})
            assert st == 200, path
        evs = _wait_events(spool_dir, 2)
        assert len(evs) == 2, evs
        for e in evs:
            assert e["session"] == JOINABLE_UUID, e
            assert json.loads(e["payload"])["sess_src"] == "claude", e
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_a_diagnostic_get_from_a_nested_run_is_not_credited(telemetry):
    """And they honour the origin header too -- otherwise `browser health` from
    inside an opencode session would be the one command that still credited the
    inherited id, which is the whole class this PR closes."""
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _b = _req(srv, "GET", "/whoami",
                      headers={S.HDR_SESSION_ID: LEAKED_ID,
                               S.HDR_SESSION_ORIGIN: "opencode-inherited"})
        assert st == 200
        e = _wait_events(spool_dir, 1)[0]
        p = json.loads(e["payload"])
        assert "session" not in e, e
        assert p["origin"] == "opencode-inherited", p
        assert p["origin_session"] == LEAKED_UUID, p
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Per-session tab isolation (the concurrent-session clobber fix)
#
# The clobber was SEMANTIC: every op targeted the ONE shared active tab, so two
# sessions' multi-step workflows interleaved on it. Fix: a session `open`s its own
# tab; the server records `(instance_key, session_id) -> tabId` and routes that
# session's tab-scoped ops to ITS tab. These are all headless — an in-process
# FakeExtension that ECHOES the dispatched tabId models "which tab did the op hit".
# --------------------------------------------------------------------------- #
def _wait_until(pred, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.005)
    return pred()


def _tab_echo_exec(open_ids):
    """Executor: `open` returns the next id from `open_ids`; every other op echoes
    back the tabId the SERVER injected (None → active-tab fallback) + the op."""
    it = iter(open_ids)

    def _exec(cmd):
        if cmd["op"] == "open":
            return {"tabId": next(it), "url": cmd.get("url")}
        if cmd["op"] == "close":
            return {"closed": cmd.get("tabId")}
        return {"tabId": cmd.get("tabId"), "op": cmd["op"]}
    return _exec


def _cmd(srv, body, session=None):
    hdrs = {S.HDR_SESSION_ID: session} if session is not None else None
    return _req(srv, "POST", "/cmd", body, headers=hdrs)


# --- ownership: open records (instance,session)->tabId --------------------- #
def test_open_records_ownership_and_returns_tabid():
    srv, reg = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _cmd(srv, {"op": "open", "url": "https://a.test"}, session="A")
        assert st == 200
        assert body["result"]["data"]["tabId"] == 101
        assert reg.owners_snapshot() == {("only", "A"): 101}
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_two_sessions_get_distinct_ownership():
    srv, reg = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101, 202]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _cmd(srv, {"op": "open"}, session="A")[1]["result"]["data"]["tabId"] == 101
        assert _cmd(srv, {"op": "open"}, session="B")[1]["result"]["data"]["tabId"] == 202
        assert reg.owners_snapshot() == {("only", "A"): 101, ("only", "B"): 202}
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --- routing: a session's ops carry ITS owned tabId ------------------------ #
def test_owned_session_ops_route_to_its_tab():
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        for op in ("getHtml", "eval", "nav"):
            b = {"op": op}
            if op == "eval":
                b["js"] = "1"
            if op == "nav":
                b["url"] = "https://x.test"
            st, body = _cmd(srv, b, session="A")
            assert st == 200, (op, body)
            assert body["result"]["data"]["tabId"] == 101, \
                f"{op} must route to A's owned tab 101"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_no_owned_tab_falls_back_to_active():
    """A session that never `open`ed → no tabId injected (extension uses the
    active tab). This preserves the one-shot 'read the tab I have open' path."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=lambda c: {"hasTab": ("tabId" in c), "op": c["op"]})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _cmd(srv, {"op": "getHtml"}, session="Z")   # session, but no open
        assert st == 200
        assert body["result"]["data"]["hasTab"] is False
        # And the dispatched command genuinely carried no tabId.
        assert all("tabId" not in c for c in ext.dispatched)
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_explicit_tab_override():
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        # No open at all, but --tab 77 forces the target.
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml", "tab": 77},
                        headers={S.HDR_SESSION_ID: "A"})
        assert st == 200
        assert body["result"]["data"]["tabId"] == 77
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --- `activate` op: tab-scoped foreground, waitMs passthrough, telemetry ---- #
def test_activate_routes_to_owned_session_tab():
    """`activate` is tab-scoped: a session that `open`ed has its activate routed
    to ITS owned tabId (own-tab enforcement — it can only ever foreground the tab
    the session owns, never an arbitrary one)."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        st, body = _cmd(srv, {"op": "activate"}, session="A")
        assert st == 200
        assert body["result"]["data"]["tabId"] == 101, \
            "activate must route to the session's owned tab (tab-scoped, own-tab enforced)"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_activate_explicit_tab_override():
    """`--tab <id>` forces the activate target even with no owned tab."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "activate", "tab": 88},
                        headers={S.HDR_SESSION_ID: "A"})
        assert st == 200
        assert body["result"]["data"]["tabId"] == 88
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_activate_forwards_waitms_to_extension():
    """`waitMs` is a passthrough command field (like text's selector/maxBytes) —
    forwarded verbatim to the extension, NOT stripped like target/tab."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=lambda c: {"tabId": c.get("tabId"),
                                            "url": "https://x.test", "status": "complete"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd", {"op": "activate", "waitMs": 1500})
        assert st == 200
        acts = [c for c in ext.dispatched if c["op"] == "activate"]
        assert len(acts) == 1
        assert acts[0]["waitMs"] == 1500
        # Routing hints never leak into the dispatched command.
        assert "target" not in acts[0] and "tab" not in acts[0]
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_activate_telemetry_is_metadata_only(telemetry):
    """PRIVACY: an activate event emits ONLY op + the bare domain — never page
    content (activate returns no page content anyway; assert the metadata shape)."""
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(
        srv, executor=lambda c: {"tabId": 5, "windowId": 1, "active": True,
                                 "status": "complete",
                                 "url": "https://model-benchmarking.example.test/run"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd", {"op": "activate"})
        assert st == 200
        e = _wait_events(spool_dir, 1)[0]
        p = json.loads(e["payload"])
        assert p["op"] == "activate"
        assert p["outcome"] == "ok"
        assert p["domain"] == "model-benchmarking.example.test"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --- `activate` host-side i3 foregrounding (untrusted-title-safe) ---------- #
class _FakeProc:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.stdout = b""
        self.stderr = b""


def _enable_i3(monkeypatch, returncode=0, raise_exc=None):
    """Turn the i3 path ON and stub subprocess.run to capture the argv (never a
    real i3-msg). Returns the list the fake appends (argv, kwargs) to per call."""
    calls = []
    monkeypatch.setattr(S, "i3_available", lambda: True)
    # Deterministic resolved i3-msg path (used as argv[0]) regardless of host.
    monkeypatch.setattr(S, "_resolve_i3_msg",
                        lambda: _FAKE_I3_MSG)

    def _fake_run(argv, **kw):
        calls.append((argv, kw))
        if raise_exc is not None:
            raise raise_exc
        return _FakeProc(returncode)

    monkeypatch.setattr(S.subprocess, "run", _fake_run)
    return calls


# The absolute i3-msg path the _enable_i3 helper makes _resolve_i3_msg return, so
# argv[0] assertions are host-independent (matches _I3_MSG_FALLBACKS[0]).
_FAKE_I3_MSG = "/run/current-system/sw/bin/i3-msg"


# --- a FAKE of the `i3-msg` boundary --------------------------------------- #
# 🔴 NEVER a real i3. Every test below stubs subprocess.run, so the suite cannot
# switch a workspace, raise a window or otherwise take the operator's screen.
#
# 🔴 THE FAKE'S ONE LOAD-BEARING FIDELITY REQUIREMENT: real `i3-msg` exits 0 and
# replies `[{"success":true}]` for a command whose criteria matched ZERO windows —
# byte-identical to a real raise. That is the entire defect. A fake that returned
# nonzero on a miss would make the buggy implementation look correct and every
# test here would be vacuous, so the fake reproduces the lie faithfully and
# test_fake_i3_mirrors_real_i3_success_on_zero_match pins that it does.
_I3_SUCCESS_REPLY = b'[{"success":true}]'
_TITLE_CRITERIA_RE = re.compile(r'^\[class="([^"]*)" title="(.*)"\] focus$')
_ID_CRITERIA_RE = re.compile(r'^\[id="(\d+)"\] focus$')


class _FakeI3:
    """An in-memory i3 that answers `-t get_tree` and `… focus`.

    windows: list of (x11_window_id, wm_class, wm_name). `focused` is the id of
    the currently focused window (None = none). Understands BOTH command shapes
    so one test file can be run against the pre-fix implementation (which focuses
    by a `title="…"` criteria) and the fixed one (which focuses by `id="…"`)."""

    def __init__(self, windows=(), focused=None):
        self.windows = [list(w) for w in windows]
        self.focused = focused
        self.calls = []            # every argv, in order
        self.tree_rc = 0
        self.tree_stdout = None    # not None → return this instead of a tree
        self.focus_rc = 0
        # False → focus is ACCEPTED (rc 0, success:true) but changes nothing.
        # This is not hypothetical: an id that vanished between the tree read and
        # the focus answers exactly this way.
        self.focus_effective = True
        self.on_call = None        # hook(fake, call_index) run BEFORE each reply

    # -- test-facing helpers ------------------------------------------------ #
    def set_title(self, window_id, name):
        for w in self.windows:
            if w[0] == window_id:
                w[2] = name

    def _is_tree(self, argv):
        return argv[1:] == ["-t", "get_tree"]

    @property
    def tree_calls(self):
        return [a for a in self.calls if self._is_tree(a)]

    @property
    def focus_calls(self):
        return [a for a in self.calls if not self._is_tree(a)]

    # -- the i3 model ------------------------------------------------------- #
    def tree(self):
        nodes = [{"type": "con", "window": wid, "name": name,
                  "window_properties": {"class": cls, "instance": "brave-browser"},
                  "focused": wid == self.focused,
                  "nodes": [], "floating_nodes": []}
                 for wid, cls, name in self.windows]
        return {"type": "root", "name": "root", "window": None, "focused": False,
                "floating_nodes": [],
                "nodes": [{"type": "workspace", "name": "1", "window": None,
                           "focused": False, "floating_nodes": [],
                           "nodes": nodes}]}

    def _focus(self, window_id):
        if not self.focus_effective:
            return
        if any(w[0] == window_id for w in self.windows):
            self.focused = window_id

    def run(self, argv, **kw):
        argv = list(argv)
        self.calls.append(argv)
        if self.on_call is not None:
            self.on_call(self, len(self.calls) - 1)
        if self._is_tree(argv):
            if self.tree_stdout is not None:
                return _FakeProcOut(self.tree_rc, self.tree_stdout)
            return _FakeProcOut(self.tree_rc,
                                json.dumps(self.tree()).encode("utf-8"))
        cmd = argv[1] if len(argv) > 1 else ""
        m = _ID_CRITERIA_RE.match(cmd)
        if m:
            self._focus(int(m.group(1)))
        else:
            m = _TITLE_CRITERIA_RE.match(cmd)
            if m:
                cls, pat = m.group(1), m.group(2)
                try:
                    rx = re.compile(pat)
                except re.error:
                    rx = None
                if rx is not None:
                    for wid, wcls, name in self.windows:
                        if wcls == cls and rx.search(name):
                            self._focus(wid)
                            break
        # 🔴 UNCONDITIONAL success — matched or not. This is what real i3 does.
        return _FakeProcOut(self.focus_rc, _I3_SUCCESS_REPLY)


class _FakeProcOut:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = b""


def _enable_fake_i3(monkeypatch, windows=(), focused=None, match_wait=0.0):
    """Turn the i3 path ON, routed at a _FakeI3. `match_wait` 0 = one tree read
    (keeps the suite fast); the settle-race tests raise it deliberately."""
    fake = _FakeI3(windows=windows, focused=focused)
    monkeypatch.setattr(S, "i3_available", lambda: True)
    monkeypatch.setattr(S, "_resolve_i3_msg", lambda: _FAKE_I3_MSG)
    monkeypatch.setattr(S, "I3_MATCH_WAIT", match_wait, raising=False)
    monkeypatch.setattr(S, "I3_MATCH_POLL", 0.01, raising=False)
    monkeypatch.setattr(S.subprocess, "run",
                        lambda argv, **kw: fake.run(argv, **kw))
    return fake


# The live pair measured 2026-08-19, reproduced as a fixture: `activate` is
# answered by Chrome with the NEW tab title while the Brave X11 window's WM_NAME
# still advertises the OLD one.
_NEW_TITLE = "Model Benchmarking"
_OLD_TITLE = "New Tab"
_BRAVE = "Brave-browser"


def _activate_with_fake_i3(fake_windows, focused=None, match_wait=0.0,
                           title=_NEW_TITLE, monkeypatch=None, mutate=None):
    """Drive ONE real `activate` through the HTTP surface against a fake i3.
    Returns (response_body, fake)."""
    fake = _enable_fake_i3(monkeypatch, windows=fake_windows, focused=focused,
                           match_wait=match_wait)
    if mutate is not None:
        mutate(fake)
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "windowId": 1, "active": True, "status": "complete",
        "url": "https://model-benchmarking.example.test/run", "title": title})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "activate", "focus": True})
        assert st == 200
        return body, fake
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


_HOSTILE_TITLE = (
    'evil"] exec xterm [title="pwn'      # try to close value → i3 `exec`
    + '; rm -rf ~ | cat `id` $(whoami)'  # shell metachars (irrelevant, no shell)
    + '\n\r\tmore .*+?[]{}^$\\ '          # control chars + regex metachars
    + "A" * 500                             # length bomb
)


def test_i3_title_pattern_hostile_title_is_inert():
    """KEY SECURITY TEST (was: the criteria-breakout test). A hostile,
    page-controlled title is reduced to a LITERAL regex — every metacharacter
    re.escape'd, every i3-structural char stripped, length capped. It is matched
    in-process against i3's get_tree reply, so a `"`/`]` has nothing to break out
    of; the escaping also means the compiled pattern is a literal (no ReDoS)."""
    pat = S.i3_title_pattern(_HOSTILE_TITLE)
    assert isinstance(pat, str) and pat
    assert '"' not in pat and "\n" not in pat and "\r" not in pat and "\t" not in pat
    # A LITERAL: the compiled pattern matches its own unescaped text and nothing
    # a metacharacter would have let through.
    lit = S._sanitize_i3_title(_HOSTILE_TITLE)
    assert re.compile(pat).search(lit)
    assert not re.compile(pat).search("totally unrelated window title")
    # Length is capped (the 500-char bomb cannot bloat the pattern unbounded).
    assert len(lit) <= S.I3_TITLE_MAX


def test_i3_title_pattern_escapes_regex_metacharacters():
    """A plain title with regex metacharacters is re.escape'd so it matches
    literally (never alters the match)."""
    assert S.i3_title_pattern("Model Benchmarking") == re.escape("Model Benchmarking")


def test_i3_title_pattern_empty_title_returns_none():
    """No usable title (empty / only structural+control chars) → None → skip."""
    assert S.i3_title_pattern("") is None
    assert S.i3_title_pattern(None) is None
    assert S.i3_title_pattern('"[]\\\n\t ') is None


def test_activate_invokes_i3_msg_on_success(monkeypatch):
    """On a successful activate the server talks to i3 with ARGV LISTS
    (shell=False), reads the tree, focuses the matched window, and reports
    i3:"applied" / i3_detail:"focused"."""
    body, fake = _activate_with_fake_i3(
        [(4242, _BRAVE, _NEW_TITLE)], focused=None, monkeypatch=monkeypatch)
    assert body["result"]["data"]["i3"] == "applied"
    assert body["result"]["data"]["i3_detail"] == "focused"
    # argv[0] is the RESOLVED ABSOLUTE i3-msg path (not the bare name) so the
    # call works even under the minimal systemd --user service PATH.
    assert all(a[0] == _FAKE_I3_MSG for a in fake.calls), fake.calls
    assert fake.tree_calls, "the raise must be decided from a get_tree read"
    assert fake.focus_calls == [[_FAKE_I3_MSG, '[id="4242"] focus']]
    assert fake.focused == 4242


def test_activate_i3_calls_are_shell_false_and_bounded(monkeypatch):
    """Every i3-msg invocation is an argv LIST with shell=False and a timeout."""
    seen = []
    fake = _FakeI3(windows=[(7, _BRAVE, _NEW_TITLE)])
    monkeypatch.setattr(S, "i3_available", lambda: True)
    monkeypatch.setattr(S, "_resolve_i3_msg", lambda: _FAKE_I3_MSG)

    def _run(argv, **kw):
        seen.append((argv, kw))
        return fake.run(argv, **kw)

    monkeypatch.setattr(S.subprocess, "run", _run)
    assert S.i3_foreground(_NEW_TITLE, match_wait=0) == ("applied", "focused")
    assert seen, "positive control: at least one i3-msg call was made"
    for argv, kw in seen:
        assert isinstance(argv, list)          # NEVER a shell string
        assert kw.get("shell", False) is False
        assert kw.get("timeout")               # bounded


def test_i3_state_and_detail_constants_match_the_literals_the_matrix_asserts():
    """The matrix compares against LITERALS (so it stays behavioural at a pre-fix
    baseline). That only stays honest while the constants agree with them."""
    assert S.I3_SKIPPED == "skipped"
    assert S.I3_WITHHELD == "withheld"
    assert S.I3_DETAIL_UNAVAILABLE == "unavailable"
    assert S.I3_DETAIL_NOT_REQUESTED == "not_requested"


def test_call_site_unavailable_detail_matches_i3_foreground(monkeypatch):
    """🔴 SEAM GUARD. The REFUSED path reports "i3 is not here" WITHOUT going
    through i3_foreground (calling it would cost a get_tree round trip we must
    not make when no raise was asked for). So the same fact is produced in two
    places and can drift. Pin them to each other by ASKING i3_foreground.

    This is the ledger-style check `claude/RULES.md` asks for at a seam: it fails
    if either side renames its detail, not merely if the call site does."""
    monkeypatch.setattr(S, "i3_available", lambda: False)
    state, detail = S.i3_foreground("anything")
    assert state == S.I3_SKIPPED
    assert detail == S.I3_DETAIL_UNAVAILABLE, (
        f"i3_foreground says {detail!r} when i3 is absent, but the /cmd refused "
        f"path reports {S.I3_DETAIL_UNAVAILABLE!r}; the two have drifted"
    )


# --- the (consent x availability x match) matrix, re-derived against #557 ---- #
# WHAT CHANGED WHEN #557 LANDED. `applied` is now EARNED: i3-msg exits 0 even for
# a criteria that matched NOTHING, so the server confirms via `get_tree` that a
# window exists and ended up focused. Two consequences for this matrix:
#
#   * the cells' MEANING is unchanged for skipped/withheld — they changed only
#     their FIXTURE (a bare `subprocess.run`→rc0 stub can no longer produce
#     `applied`, because rc 0 with empty stdout is now `failed`/`tree_unreadable`);
#   * the consented x available cell SPLIT IN TWO. "i3 is there and a window
#     matches" → applied/focused; "i3 is there and nothing matches" →
#     failed/no_match, a state that did not exist before and that used to be
#     reported as `applied`. That new cell is included below.
#
# Every cell also asserts `data["i3"]` is a STRING. #557 changed i3_foreground to
# return a (state, detail) TUPLE, so a call site that forgets to unpack puts a
# tuple in the JSON — silently wrong rather than a crash. That is the single
# highest-value regression guard in this block.
@pytest.mark.parametrize(
    "i3_up,consent,window_matches,want_state,want_detail,want_note", [
        (True,  True,  True,  "applied",  "focused",      False),
        (True,  True,  False, "failed",   "no_match",     False),
        (True,  False, None,  "withheld", "not_requested", True),
        (False, True,  None,  "skipped",  "unavailable",  False),
        (False, False, None,  "skipped",  "unavailable",  False),
    ],
    ids=["i3+consent+match", "i3+consent+nomatch", "i3+refused",
         "noi3+consent", "noi3+refused"])
def test_activate_i3_state_matrix(monkeypatch, i3_up, consent, window_matches,
                                  want_state, want_detail, want_note):
    """All (availability x consent x match) cells, so ORDER and SHAPE are pinned.

    `want_note` is asserted BOTH ways: the withheld note must appear exactly
    where its `--focus` advice is actionable, and nowhere else.
    """
    fake = None
    if i3_up:
        windows = [(77, _BRAVE, _NEW_TITLE if window_matches else "Something Else")]
        fake = _enable_fake_i3(monkeypatch, windows=windows, focused=None)
    else:
        # autouse _disable_i3 already forces i3_available False; make ANY
        # subprocess a hard failure so "we never shelled out" is proven, not
        # inferred from the reported state.
        def _boom(*a, **k):
            raise AssertionError("i3-msg must NOT run when i3 is unavailable")
        monkeypatch.setattr(S.subprocess, "run", _boom)

    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "windowId": 1, "active": True, "status": "complete",
        "url": "https://model-benchmarking.example.test/run",
        "title": _NEW_TITLE})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        cmd = {"op": "activate"}
        if consent:
            cmd["focus"] = True
        st, body = _req(srv, "POST", "/cmd", cmd)
        assert st == 200
        data = body["result"]["data"]

        # 🔴 TUPLE GUARD (#557 made i3_foreground return a pair).
        assert isinstance(data["i3"], str), (
            f"data['i3'] is {type(data['i3']).__name__} {data['i3']!r} — a call "
            "site forgot to unpack i3_foreground's (state, detail) pair"
        )
        assert isinstance(data.get("i3_detail", ""), str)

        assert data["i3"] == want_state, (
            f"i3_up={i3_up} consent={consent} match={window_matches} -> "
            f"i3={data['i3']!r}, want {want_state!r}"
        )
        assert data.get("i3_detail") == want_detail, (
            f"i3_up={i3_up} consent={consent} match={window_matches} -> "
            f"i3_detail={data.get('i3_detail')!r}, want {want_detail!r}"
        )
        assert ("note" in data) is want_note, (
            f"note present={'note' in data}, want {want_note}"
        )
        # The Chrome-side result survives in every cell — this gates the WM step
        # only, never the op.
        assert data["tabId"] == 5

        # 🔴 A REFUSED raise must not even LOOK at i3: no get_tree, no focus.
        if fake is not None and not consent:
            assert fake.calls == [], (
                f"a refused activate talked to i3 anyway: {fake.calls}"
            )
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_refused_activate_makes_no_i3_round_trip_at_all(monkeypatch):
    """🔴 The consent gate must sit AHEAD of #557's confirmation step.

    #557 made a consented activate do a `get_tree` read (and a re-read to confirm
    focus). None of that may happen for a raise nobody asked for — not because it
    would move focus (a tree read does not), but because the gate's promise is
    that a refusal touches the WM path not at all. Asserted on the fake's own call
    log rather than on the reported state, since a state string cannot tell you
    whether a subprocess ran."""
    fake = _enable_fake_i3(monkeypatch,
                           windows=[(77, _BRAVE, _NEW_TITLE)], focused=None)
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "windowId": 1, "active": True, "status": "complete",
        "url": "https://model-benchmarking.example.test/run",
        "title": _NEW_TITLE})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "activate"})
        assert st == 200
        # LITERAL, not S.I3_WITHHELD: at a baseline without the gate the constant
        # does not exist, and an AttributeError here would be red for the wrong
        # reason — it would prove nothing about whether a round trip happened.
        assert body["result"]["data"]["i3"] == "withheld"
        assert fake.tree_calls == [], f"get_tree ran for a refused raise: {fake.tree_calls}"
        assert fake.focus_calls == [], f"focus ran for a refused raise: {fake.focus_calls}"
        # …and the window really was left alone.
        assert fake.focused is None
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_non_consented_activate_on_a_non_i3_host_says_skipped_not_withheld(monkeypatch):
    """🔴 Unavailability must beat non-consent.

    Compared against the LITERAL "skipped", not S.I3_SKIPPED: at the pre-fix
    commit the constant did not exist, so referencing it made this red with an
    AttributeError — red for the wrong reason, which proves nothing about
    behaviour. Drift is pinned separately, above."""
    def _boom(*a, **k):
        raise AssertionError("i3-msg must NOT run when i3 is unavailable")
    monkeypatch.setattr(S.subprocess, "run", _boom)
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "windowId": 1, "active": True, "status": "complete",
        "url": "https://model-benchmarking.example.test/run",
        "title": _NEW_TITLE})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "activate"})
        assert st == 200
        data = body["result"]["data"]
        assert data["i3"] == "skipped", (
            f"a non-consented activate on a host with NO i3 reported "
            f"{data['i3']!r}; it must report 'skipped' — there is no raise to "
            "withhold, and 'withheld' invites a pointless --focus retry"
        )
        assert "note" not in data, (
            "the withheld note (which tells the caller to pass --focus) was "
            f"attached on a host that cannot raise at all: {data.get('note')!r}"
        )
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_activate_i3_skipped_when_unavailable(monkeypatch):
    """i3-msg unavailable (headless/non-i3) → SKIPPED gracefully; the activate
    still returns the Chrome-side result and NO subprocess is spawned."""
    # _disable_i3 (autouse) already forces i3_available False; also make any
    # subprocess.run a hard failure so a regression that skips the gate is caught.
    def _boom(*a, **k):
        raise AssertionError("i3-msg must NOT run when i3 is unavailable")
    monkeypatch.setattr(S.subprocess, "run", _boom)
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "active": True, "status": "complete",
        "url": "https://x.test", "title": "Anything"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "activate", "focus": True})
        assert st == 200
        assert body["result"]["data"]["i3"] == "skipped"
        assert body["result"]["data"]["i3_detail"] == "unavailable"
        assert body["result"]["data"]["tabId"] == 5  # Chrome-side result intact
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_activate_i3_skipped_when_no_title(monkeypatch):
    """i3 available but the tab has no usable title → SKIPPED (no criteria to
    match); no subprocess is spawned."""
    calls = _enable_i3(monkeypatch, returncode=0)
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "active": True, "status": "complete", "url": "https://x.test"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "activate", "focus": True})
        assert st == 200
        assert body["result"]["data"]["i3"] == "skipped"
        assert body["result"]["data"]["i3_detail"] == "no_title"
        assert calls == []
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_activate_i3_failed_on_nonzero(monkeypatch):
    """i3-msg exits nonzero → non-fatal FAILED; the Chrome-side result still
    returns."""
    _enable_i3(monkeypatch, returncode=1)
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "active": True, "status": "complete",
        "url": "https://x.test", "title": "Some Tab"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "activate", "focus": True})
        assert st == 200
        assert body["result"]["data"]["i3"] == "failed"
        assert body["result"]["data"]["i3_detail"] == "tree_unreadable"
        assert body["result"]["data"]["tabId"] == 5
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_activate_i3_failed_on_timeout(monkeypatch):
    """i3-msg timing out → non-fatal FAILED (the call is timeout-bounded)."""
    _enable_i3(monkeypatch,
               raise_exc=subprocess.TimeoutExpired(cmd="i3-msg", timeout=1.5))
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "active": True, "status": "complete",
        "url": "https://x.test", "title": "Some Tab"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "activate", "focus": True})
        assert st == 200
        assert body["result"]["data"]["i3"] == "failed"
        assert body["result"]["data"]["i3_detail"] == "tree_unreadable"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# ======================================================================== #
# 🔴 `i3: applied` MUST MEAN A WINDOW WAS RAISED
#
# Live pair, 2026-08-19: `activate` #1 (right after `open`) reported
# i3:"applied" while raising NOTHING — the tab stayed `hidden` — because the
# Brave window's WM_NAME still held the OLD title, the `title="…"` criteria
# matched zero windows, and `i3-msg` exits 0 with `[{"success":true}]` anyway.
# `activate` #2, once the title had settled, reported the SAME "applied" and
# really did raise it. One signal, two opposite realities.
#
# Downstream that is the dominant live cause of a capture tool exiting 11 "the
# app never booted": it never booted because nothing was ever raised, and the
# only status said everything was fine.
# ======================================================================== #

def test_fake_i3_mirrors_real_i3_success_on_zero_match():
    """INSTRUMENT VALIDATION — pin the fake's fidelity to the ONE real i3
    behaviour every test below depends on: a `focus` whose criteria matched ZERO
    windows still exits 0 with `[{"success":true}]`.

    If this ever stops holding, the fake has become kinder than i3 and every
    zero-match test below would pass against the BUGGY implementation."""
    fake = _FakeI3(windows=[(1, _BRAVE, _OLD_TITLE)])
    miss = fake.run([_FAKE_I3_MSG,
                     '[class="%s" title="%s"] focus' % (_BRAVE, re.escape(_NEW_TITLE))])
    assert miss.returncode == 0                     # ← the trap, faithfully
    assert miss.stdout == b'[{"success":true}]'
    assert fake.focused is None                     # …and nothing was raised
    # POSITIVE CONTROL: the same reply shape for a criteria that DOES match, so
    # the assertion above is about the MATCH, not about the fake refusing to work.
    hit = fake.run([_FAKE_I3_MSG,
                    '[class="%s" title="%s"] focus' % (_BRAVE, re.escape(_OLD_TITLE))])
    assert hit.returncode == 0 and hit.stdout == b'[{"success":true}]'
    assert fake.focused == 1                        # this one really raised


def test_activate_i3_zero_match_is_never_reported_as_applied(monkeypatch):
    """🔴 THE REGRESSION TEST. RED on the pre-fix implementation.

    The exact live race: Chrome answers `activate` with the NEW title while the
    only Brave window still advertises the OLD one. i3 matches nothing and exits
    0 — so the pre-fix code reported i3:"applied" with the tab still hidden.
    `applied` must be unreachable here."""
    body, fake = _activate_with_fake_i3(
        [(4242, _BRAVE, _OLD_TITLE)], monkeypatch=monkeypatch)
    data = body["result"]["data"]
    assert data["i3"] != "applied", (
        "zero windows matched — `applied` is a lie the caller acts on")
    assert data["i3"] == "failed"
    assert data["i3_detail"] == "no_match"
    # Ground truth from the fake: nothing was ever focused.
    assert fake.focused is None
    # The Chrome-side result is untouched (the i3 step is non-fatal metadata).
    assert data["tabId"] == 5


def test_activate_i3_real_match_is_reported_as_applied(monkeypatch):
    """POSITIVE CONTROL for the test above — the SAME fixture with the SAME code
    path, differing only in the window's title. Reported pair:
      window title OLD (no match) → i3="failed"  / detail="no_match"
      window title NEW (a match)  → i3="applied" / detail="focused"
    Without this, "never applied" could be satisfied by a check wired to nothing."""
    body, fake = _activate_with_fake_i3(
        [(4242, _BRAVE, _NEW_TITLE)], monkeypatch=monkeypatch)
    data = body["result"]["data"]
    assert data["i3"] == "applied"
    assert data["i3_detail"] == "focused"
    assert fake.focused == 4242          # ground truth: it really was raised


def test_activate_i3_zero_match_issues_no_focus_command(monkeypatch):
    """A raise that cannot match must not fire a focus at all — and the ZERO is
    reported WITH its positive control (0 focus calls on a miss, 1 on a hit), so
    a harness wired to nothing cannot masquerade as the finding."""
    miss_body, miss = _activate_with_fake_i3(
        [(1, _BRAVE, _OLD_TITLE)], monkeypatch=monkeypatch)
    hit_body, hit = _activate_with_fake_i3(
        [(1, _BRAVE, _NEW_TITLE)], monkeypatch=monkeypatch)
    assert len(miss.focus_calls) == 0, miss.focus_calls
    assert len(hit.focus_calls) == 1, hit.focus_calls   # ← the positive control
    assert miss_body["result"]["data"]["i3"] == "failed"
    assert hit_body["result"]["data"]["i3"] == "applied"
    # Both arms DID talk to i3 — the miss is a real read, not a skipped step.
    assert miss.tree_calls and hit.tree_calls


def test_activate_i3_focus_is_keyed_on_stable_window_id(monkeypatch):
    """The focus is keyed on the X11 window ID from i3's own reply, not on the
    racy title — the id cannot change underneath us while WM_NAME settles."""
    body, fake = _activate_with_fake_i3(
        [(9, _BRAVE, "Some Other Tab"), (77, _BRAVE, _NEW_TITLE)],
        monkeypatch=monkeypatch)
    assert body["result"]["data"]["i3"] == "applied"
    assert fake.focus_calls == [[_FAKE_I3_MSG, '[id="77"] focus']]
    assert fake.focused == 77            # the RIGHT window, not the first one


def test_activate_i3_untrusted_title_never_reaches_any_argv(monkeypatch):
    """SECURITY: with the title out of the criteria entirely, no fragment of the
    page-controlled title appears in ANY i3-msg argv. The window is named with
    the sanitized title so the match SUCCEEDS — the positive control that proves
    this assertion is made over a run that really did find and focus a window."""
    lit = S._sanitize_i3_title(_HOSTILE_TITLE)
    body, fake = _activate_with_fake_i3(
        [(31337, _BRAVE, "prefix " + lit + " suffix")],
        title=_HOSTILE_TITLE, monkeypatch=monkeypatch)
    assert body["result"]["data"]["i3"] == "applied"      # ← positive control
    assert fake.focused == 31337
    flat = " ".join(" ".join(a) for a in fake.calls)
    for probe in ("exec", "xterm", "rm -rf", "whoami", "pwn", "AAAA"):
        assert probe not in flat, (probe, flat)
    assert fake.focus_calls == [[_FAKE_I3_MSG, '[id="31337"] focus']]


def test_activate_i3_focus_accepted_but_not_focused_is_failed(monkeypatch):
    """VERIFY step: i3 accepting the focus (rc 0, success:true) is not proof. A
    window that does not end up focused → failed/not_focused, never applied.

    Reported pair — same window, same command, only the effect differs:
      focus takes effect     → applied / focused
      focus is a no-op       → failed  / not_focused"""
    eff_body, eff = _activate_with_fake_i3(
        [(5, _BRAVE, _NEW_TITLE)], monkeypatch=monkeypatch)
    noop_body, noop = _activate_with_fake_i3(
        [(5, _BRAVE, _NEW_TITLE)], monkeypatch=monkeypatch,
        mutate=lambda f: setattr(f, "focus_effective", False))
    assert eff_body["result"]["data"]["i3"] == "applied"          # control
    assert noop_body["result"]["data"]["i3"] == "failed"
    assert noop_body["result"]["data"]["i3_detail"] == "not_focused"
    # Both arms issued the focus and got rc 0 — the difference is only the EFFECT.
    assert len(eff.focus_calls) == 1 and len(noop.focus_calls) == 1
    assert eff.focused == 5 and noop.focused is None


def test_activate_i3_focus_nonzero_is_failed(monkeypatch):
    """The focus command itself erroring → failed/focus_error (not applied)."""
    body, fake = _activate_with_fake_i3(
        [(5, _BRAVE, _NEW_TITLE)], monkeypatch=monkeypatch,
        mutate=lambda f: setattr(f, "focus_rc", 2))
    assert body["result"]["data"]["i3"] == "failed"
    assert body["result"]["data"]["i3_detail"] == "focus_error"
    assert len(fake.focus_calls) == 1     # positive control: it WAS attempted


def test_activate_i3_unparseable_tree_is_failed(monkeypatch):
    """A get_tree reply that is not JSON → failed/tree_unreadable, and NO focus
    is attempted (we cannot know what would match). Positive control: the same
    fixture with a parseable tree focuses exactly once."""
    bad_body, bad = _activate_with_fake_i3(
        [(5, _BRAVE, _NEW_TITLE)], monkeypatch=monkeypatch,
        mutate=lambda f: setattr(f, "tree_stdout", b"not json at all"))
    good_body, good = _activate_with_fake_i3(
        [(5, _BRAVE, _NEW_TITLE)], monkeypatch=monkeypatch)
    assert bad_body["result"]["data"]["i3"] == "failed"
    assert bad_body["result"]["data"]["i3_detail"] == "tree_unreadable"
    assert len(bad.focus_calls) == 0
    assert good_body["result"]["data"]["i3"] == "applied"     # ← control
    assert len(good.focus_calls) == 1


def test_activate_i3_ignores_non_brave_windows(monkeypatch):
    """A same-titled window of ANOTHER application must not be raised or counted
    as a match — the class constraint survived the move out of the criteria."""
    body, fake = _activate_with_fake_i3(
        [(8, "Alacritty", _NEW_TITLE)], monkeypatch=monkeypatch)
    assert body["result"]["data"]["i3"] == "failed"
    assert body["result"]["data"]["i3_detail"] == "no_match"
    assert fake.focused is None
    assert len(fake.focus_calls) == 0
    # POSITIVE CONTROL: identical title, class Brave → matched and raised.
    body2, fake2 = _activate_with_fake_i3(
        [(8, _BRAVE, _NEW_TITLE)], monkeypatch=monkeypatch)
    assert body2["result"]["data"]["i3"] == "applied" and fake2.focused == 8


def test_i3_foreground_waits_out_the_title_settling_race(monkeypatch):
    """THE OTHER HALF OF THE LIVE PAIR: a bounded re-read of the tree turns the
    first post-`open` activate — the one that used to match nothing — into a real
    raise once WM_NAME catches up.

    Reported pair:
      title settles on the 3rd tree read → applied / focused, >1 tree read
      title never settles                → failed  / no_match, >1 tree read
    (the second arm is the control proving the loop actually re-read and did not
    simply succeed on its first look)."""
    monkeypatch.setattr(S, "i3_available", lambda: True)
    monkeypatch.setattr(S, "_resolve_i3_msg", lambda: _FAKE_I3_MSG)
    monkeypatch.setattr(S, "I3_MATCH_POLL", 0.001)

    settling = _FakeI3(windows=[(4242, _BRAVE, _OLD_TITLE)])

    def _settle(fake, idx):
        if len(fake.tree_calls) >= 2:      # this call is the 3rd tree read
            fake.set_title(4242, _NEW_TITLE)

    settling.on_call = _settle
    monkeypatch.setattr(S.subprocess, "run",
                        lambda argv, **kw: settling.run(argv, **kw))
    assert S.i3_foreground(_NEW_TITLE, match_wait=1.0) == ("applied", "focused")
    assert len(settling.tree_calls) > 1, "it must have re-read the tree"
    assert settling.focused == 4242

    stuck = _FakeI3(windows=[(4242, _BRAVE, _OLD_TITLE)])
    monkeypatch.setattr(S.subprocess, "run",
                        lambda argv, **kw: stuck.run(argv, **kw))
    assert S.i3_foreground(_NEW_TITLE, match_wait=0.05) == ("failed", "no_match")
    assert len(stuck.tree_calls) > 1, "the wait must have polled, not given up"
    assert stuck.focused is None


def test_i3_foreground_state_vocabulary_is_closed(monkeypatch):
    """LEDGER over `i3_foreground`'s OWN RETURNS: exactly {applied, skipped,
    failed}, and "applied" pairs with exactly one detail.

    🔴 SCOPE, corrected. This used to claim it guarded `result.data.i3` — "the
    three values consumers branch on". That was false the moment the consent gate
    added a FOURTH value: `withheld` is produced at the /cmd CALL SITE and never
    passes through this function, so this enumeration cannot observe it and stayed
    green while the field it claimed to close gained a value. A ledger that names a
    RELATIONSHIP but pins a COMPONENT is the failure `claude/RULES.md` describes.

    The field-level ledger it pretended to be now exists separately, and covers
    both producers — see test_data_i3_value_set_is_a_closed_ledger."""
    monkeypatch.setattr(S, "i3_available", lambda: True)
    monkeypatch.setattr(S, "_resolve_i3_msg", lambda: _FAKE_I3_MSG)
    monkeypatch.setattr(S, "I3_MATCH_POLL", 0.001)

    def _outcome(windows, **attrs):
        fake = _FakeI3(windows=windows)
        for k, v in attrs.items():
            setattr(fake, k, v)
        monkeypatch.setattr(S.subprocess, "run",
                            lambda argv, **kw: fake.run(argv, **kw))
        return S.i3_foreground(_NEW_TITLE, match_wait=0)

    seen = {
        _outcome([(1, _BRAVE, _NEW_TITLE)]),
        _outcome([(1, _BRAVE, _OLD_TITLE)]),
        _outcome([(1, _BRAVE, _NEW_TITLE)], focus_effective=False),
        _outcome([(1, _BRAVE, _NEW_TITLE)], focus_rc=3),
        _outcome([(1, _BRAVE, _NEW_TITLE)], tree_rc=1),
        _outcome([(1, _BRAVE, _NEW_TITLE)], tree_stdout=b"{"),
    }
    monkeypatch.setattr(S, "i3_available", lambda: False)
    seen.add(S.i3_foreground(_NEW_TITLE, match_wait=0))
    monkeypatch.setattr(S, "i3_available", lambda: True)
    seen.add(S.i3_foreground("", match_wait=0))

    # Positive control: the enumeration really did exercise every branch.
    assert len(seen) >= 7, seen
    assert {s for s, _ in seen} == {"applied", "skipped", "failed"}, seen
    assert {d for s, d in seen if s == "applied"} == {"focused"}, seen
    assert all(d for _, d in seen), "every outcome carries a reason"


# The COMPLETE value set of `result.data.i3`, across BOTH producers: whatever
# `i3_foreground` returns, plus whatever the /cmd activate branch assigns without
# calling it. Adding a value to either side without updating this line fails.
DATA_I3_VALUES = {"applied", "skipped", "failed", "withheld"}


def _call_site_state_values():
    """Every value the /cmd activate branch can put in `state`, read STRUCTURALLY
    from server.py's AST — not from running it.

    Structural on purpose: a behavioural sweep can only see states some fixture
    happens to produce, so a fifth value added on a branch no test exercises would
    sail past it. Parsing the assignments sees it whether or not it is reachable,
    which is the half a ledger has to have to fail when the set GROWS.
    """
    import ast
    import pathlib as _pl
    tree = ast.parse(_pl.Path(S.__file__).read_text())
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if not (isinstance(tgt, ast.Tuple) and len(tgt.elts) == 2):
                continue
            names = [e.id for e in tgt.elts if isinstance(e, ast.Name)]
            if names[:1] != ["state"]:
                continue
            val = node.value
            if isinstance(val, ast.Tuple) and val.elts:
                first = val.elts[0]
                if isinstance(first, ast.Constant):
                    out.add(first.value)
                elif isinstance(first, ast.Name):
                    out.add(getattr(S, first.id))
            # `state, detail = i3_foreground(...)` contributes that function's
            # returns, which the sibling ledger above pins.
    return out


def _i3_foreground_return_values():
    """The state half of every literal `return` in i3_foreground, from the AST."""
    import ast
    import pathlib as _pl
    tree = ast.parse(_pl.Path(S.__file__).read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "i3_foreground")
    out = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple):
            first = node.value.elts[0]
            if isinstance(first, ast.Constant):
                out.add(first.value)
            elif isinstance(first, ast.Name):
                out.add(getattr(S, first.id))
    return out


def test_data_i3_value_set_is_a_closed_ledger():
    """🔴 THE FIELD-LEVEL LEDGER: `result.data.i3` takes EXACTLY DATA_I3_VALUES.

    Fails when the set GROWS *or* SHRINKS, and covers BOTH producers — the
    `i3_foreground` returns and the call-site assignments that bypass it. The
    predecessor of this test enumerated only the former while claiming the latter,
    which is how a fourth value was added to this field with the suite green.

    Structural rather than behavioural on the GROW side by design: a fifth value
    added on a branch nothing exercises is exactly the case a driven sweep cannot
    see, and exactly the case a ledger must catch.
    """
    produced = _i3_foreground_return_values() | _call_site_state_values()
    assert produced == DATA_I3_VALUES, (
        "the set of values `result.data.i3` can take has changed.\n"
        f"  produced by the code: {sorted(produced)}\n"
        f"  declared here:        {sorted(DATA_I3_VALUES)}\n"
        f"  added:   {sorted(produced - DATA_I3_VALUES)}\n"
        f"  removed: {sorted(DATA_I3_VALUES - produced)}\n"
        "If this is a deliberate contract change, update DATA_I3_VALUES *and* the "
        "docs that publish the vocabulary (README op table, browser CLI usage "
        "block, _annotate_i3's docstring) in the SAME commit."
    )


def test_data_i3_ledger_is_not_vacuous():
    """POSITIVE CONTROL for the ledger above: both halves must actually find
    something. A parser that silently matched nothing would make the ledger a
    tautology (empty == empty is false here, but a HALF that returns empty would
    still let the other half carry it and hide a whole producer)."""
    fg = _i3_foreground_return_values()
    cs = _call_site_state_values()
    assert fg, "the i3_foreground return parser found no states — it is wired to nothing"
    assert cs, "the call-site parser found no states — it is wired to nothing"
    # The call site must contribute at least the value i3_foreground CANNOT.
    assert "withheld" in cs, cs
    assert "withheld" not in fg, fg


def test_activate_i3_telemetry_stays_metadata_only(telemetry, monkeypatch):
    """Even when i3 focusing is APPLIED (focus:true — the consented path, which
    is the only one that reaches i3_foreground at all), the activate telemetry
    event carries NO page title — only op / outcome / bare domain (the title can
    hold page content; the i3 step must not leak it into activity.events)."""
    spool_dir = telemetry
    _enable_fake_i3(monkeypatch,
                    windows=[(5, _BRAVE, "SECRET PAGE CONTENT IN TITLE")])
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "active": True, "status": "complete",
        "url": "https://model-benchmarking.example.test/run",
        "title": "SECRET PAGE CONTENT IN TITLE"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd",
                        {"op": "activate", "focus": True})
        assert st == 200
        # POSITIVE CONTROL: the i3 step really RAN and raised the window, so the
        # "no title in telemetry" assertion below is made over a live i3 path —
        # a skipped/failed i3 step would leak nothing for trivial reasons.
        assert body["result"]["data"]["i3"] == "applied"
        e = _wait_events(spool_dir, 1)[0]
        p = json.loads(e["payload"])
        assert p["op"] == "activate" and p["outcome"] == "ok"
        assert p["domain"] == "model-benchmarking.example.test"
        # No title anywhere in the emitted event.
        assert "SECRET" not in json.dumps(e)
        assert "title" not in p
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --- the focus-steal CONSENT gate (regression, #focus-steal) --------------- #
# WHY THESE EXIST — the measurement, not a hunch. Correlating 55,003
# `browser-bridge` cmd events in activity.events against `i3` window-focus
# events (2026-07-29 .. 2026-08-18): `activate` is the ONLY op with a causal
# signature — 111/166 calls (66.9%) have a Brave window-focus event within
# +/-1s, against 1.7-7.3% for every other op, and only 5/166 land in the 1-5s
# band (a WM raise is immediate; a human context-switch is not). `screenshot`
# was the leading rival hypothesis (captureVisibleTab only grabs the focused
# window) and is FLAT: 7.3% at +/-1s vs 8.5% across the 1-5s bands, n=531.
#
# Before this gate, `i3_foreground()` ran on EVERY successful activate. The two
# prior mitigations were a PROSE nudge (HIDDEN_TAB_NOTE steering to `wake`) and
# an op-allowlist in ONE caller (the sandboxed browser-agent tool) — so every
# other caller of the `browser` CLI walked straight past both, which is what the
# 166 activates are. The rule now lives in the single place the screen is
# actually taken.
#
# Each test below is RED at the pre-fix commit (i3_foreground was
# unconditional); none of them is an invariant guard.
def test_activate_withholds_the_i3_raise_without_explicit_consent(monkeypatch):
    """🔴 THE REGRESSION. An activate that does NOT carry focus:true must never
    reach i3-msg: no subprocess call at all, and the result says so.

    This is the measured focus steal, closed at its source. The assertion is on
    the SUBPROCESS CALL LIST, not only on the reported state — a state string is
    something a future refactor can set while still shelling out, and the thing
    that actually takes the operator's screen is the exec."""
    calls = _enable_i3(monkeypatch, returncode=0)
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "windowId": 1, "active": True, "status": "complete",
        "url": "https://model-benchmarking.example.test/run",
        "title": "Model Benchmarking"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "activate"})
        assert st == 200
        assert calls == [], (
            "activate without focus:true shelled out to i3-msg — the operator's "
            f"screen was taken without consent (argv: {calls})"
        )
        assert body["result"]["data"]["i3"] == "withheld"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_activate_still_activates_the_tab_when_the_raise_is_withheld(monkeypatch):
    """INVARIANT GUARD (green at origin/main — NOT regression coverage).

    Withholding the RAISE must not break the OP. The Chrome-side tab activation
    still happens (it is a no-op for real visibility under i3 and takes
    nothing), so `activate` keeps working as a tab-state change — this is a gate
    on the WM step alone, not a removal of the op. Base passes it because base
    also activates the tab; its job is to stop a future "fix" from turning the
    gate into a removal."""
    _enable_i3(monkeypatch, returncode=0)
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "windowId": 1, "active": True, "status": "complete",
        "url": "https://model-benchmarking.example.test/run",
        "title": "Model Benchmarking"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "activate"})
        assert st == 200
        data = body["result"]["data"]
        assert data["active"] is True and data["tabId"] == 5
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_withheld_activate_names_wake_before_the_focus_override(monkeypatch):
    """The note an agent READS is what it learns, so pin the whole normalised
    string, not a keyword an unrelated sentence could spell.

    Load-bearing ORDER: the non-intrusive remedy (`browser wake`) must appear
    BEFORE the `--focus` override, or the note teaches the escape hatch as the
    headline and the gate decays into a speed bump — which is exactly how the
    prose-only mitigation in protocol.js's HIDDEN_TAB_NOTE half-failed."""
    note = S.I3_WITHHELD_NOTE
    assert "browser wake" in note
    assert "--focus" in note
    assert note.index("browser wake") < note.index("--focus"), (
        "the note offers --focus before it offers `browser wake`; the "
        "non-intrusive remedy must lead"
    )
    # The whole string, normalised — a reword has to come here and be read.
    assert " ".join(note.split()) == (
        "the tab is now the active tab of its window, but the Brave WINDOW was "
        "NOT raised: taking the operator's screen needs explicit consent. If "
        "you only needed the page to render, use 'browser wake' (un-throttles "
        "via CDP, moves no focus). Pass --focus only if something genuinely "
        "needs the real foreground."
    )


def test_withheld_activate_carries_the_note_and_applied_does_not(monkeypatch):
    """The note rides on the WITHHELD result so the caller is told what did not
    happen and what to do instead — and is ABSENT when the raise was applied
    (a note on a successful raise would be noise the agent learns to ignore).

    FIXTURE CHANGED BY #557, meaning unchanged. `applied` is now EARNED via a
    `get_tree` confirmation, so the old `_enable_i3` stub (bare subprocess.run →
    rc 0, empty stdout) can no longer reach it — it now parses as an unreadable
    tree and yields failed/tree_unreadable. The assertion this test exists to
    make is identical; only the i3 it is told to imagine is real now."""
    fake = _enable_fake_i3(monkeypatch,
                           windows=[(77, _BRAVE, _NEW_TITLE)], focused=None)
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "windowId": 1, "active": True, "status": "complete",
        "url": "https://model-benchmarking.example.test/run",
        "title": _NEW_TITLE})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _st, withheld = _req(srv, "POST", "/cmd", {"op": "activate"})
        assert withheld["result"]["data"]["i3"] == S.I3_WITHHELD
        assert withheld["result"]["data"]["note"] == S.I3_WITHHELD_NOTE
        # POSITIVE CONTROL for the "absent when applied" half: the second call
        # must genuinely reach `applied`, or "no note" would hold for the boring
        # reason that the raise failed.
        _st, applied = _req(srv, "POST", "/cmd",
                            {"op": "activate", "focus": True})
        assert applied["result"]["data"]["i3"] == "applied", (
            f"expected a real raise; got {applied['result']['data']['i3']!r}/"
            f"{applied['result']['data'].get('i3_detail')!r}"
        )
        assert "note" not in applied["result"]["data"]
        assert fake.focused == 77
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_activate_telemetry_records_the_consent_decision(telemetry, monkeypatch):
    """`payload.focus` distinguishes a withheld activate from a consented one.

    This is what makes the fix FALSIFIABLE with the same instrument that found
    the bug. The focus-steal rate was measured per-op out of activity.events;
    without this field a post-deploy re-run of that query cannot separate the
    two cases, so "the steals stopped" would be a claim with no data behind it.

    It is a bare boolean — no page content — so the PRIVACY contract is
    unchanged, and `kind` stays "cmd" so the adoption-scan usage signal is
    unchanged (both asserted here, because a telemetry addition is exactly where
    those two contracts get broken by accident)."""
    spool_dir = telemetry
    _enable_i3(monkeypatch, returncode=0)
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "windowId": 1, "active": True, "status": "complete",
        "url": "https://model-benchmarking.example.test/run",
        "title": "Model Benchmarking"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd", {"op": "activate"})
        assert st == 200
        e = _wait_events(spool_dir, 1)[0]
        p = json.loads(e["payload"])
        assert p["op"] == "activate" and p["focus"] is False
        assert e["kind"] == "cmd", "the usage signal must not change"

        st, _ = _req(srv, "POST", "/cmd", {"op": "activate", "focus": True})
        assert st == 200
        e2 = _wait_events(spool_dir, 2)[1]
        p2 = json.loads(e2["payload"])
        assert p2["op"] == "activate" and p2["focus"] is True
        # No page content rode along with the new field.
        assert "title" not in p2 and "Model Benchmarking" not in json.dumps(e2)
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_focus_field_never_reaches_the_extension(monkeypatch):
    """`focus` is a SERVER-side decision: it must be popped like target/tab and
    never dispatched.

    Two reasons this is pinned. (1) The extension's validateCommand is permissive
    about unknown fields today, so a leak would be silent — and a future strict
    validator would turn it into a hard failure of the one op this gate governs.
    (2) It is what lets this fix deploy with NO extension rebuild and NO Brave
    restart: the wire contract the extension sees is byte-identical."""
    _enable_i3(monkeypatch, returncode=0)
    srv, _ = _serve()
    seen = []

    def _exec(c):
        seen.append(dict(c))
        return {"tabId": 5, "windowId": 1, "active": True, "status": "complete",
                "url": "https://model-benchmarking.example.test/run",
                "title": "Model Benchmarking"}

    ext = FakeExtension(srv, executor=_exec)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd", {"op": "activate", "focus": True})
        assert st == 200
        acts = [c for c in seen if c.get("op") == "activate"]
        assert acts, "the activate never reached the fake extension"
        assert all("focus" not in c for c in acts), (
            f"the server forwarded the `focus` consent flag to the extension: {acts}"
        )
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_focus_requested_accepts_only_a_literal_json_true():
    """Consent is a literal `true`, never Python truthiness.

    The dangerous direction is a STRING: a caller that builds the body by shell
    interpolation can easily send "false", and `bool("false")` is True — which
    would read a refusal as consent and hand back exactly the bug this gate
    closes. Pinned pairwise-distinct: every value below is a different shape,
    and the two that differ ONLY by type ("true" vs True) sit next to each other
    so a truthiness mutant cannot pass by coincidence."""
    assert S.focus_requested({"op": "activate", "focus": True}) is True
    # Everything else is a refusal.
    for value in ("true", "false", "1", 1, 0, "", [], {}, None, "yes"):
        assert S.focus_requested({"op": "activate", "focus": value}) is False, (
            f"focus={value!r} ({type(value).__name__}) was read as consent"
        )
    assert S.focus_requested({"op": "activate"}) is False   # absent → refuse
    assert S.focus_requested(None) is False                 # not a dict → refuse


def test_every_other_op_is_untouched_by_the_gate(monkeypatch):
    """SCOPE GUARD / INVARIANT GUARD (green at origin/main — NOT regression
    coverage). The gate must bind `activate` and nothing else: no other op has
    ever reached i3_foreground, and a mutant that widened or moved the condition
    would show up as a behaviour change here.

    `wake` is the op that matters most — it is the sanctioned non-intrusive
    remedy, so if it ever started shelling out to i3-msg the whole fix is moot.
    Sending focus:true on these ops must ALSO change nothing (it is meaningless
    outside activate), which is what the second pass pins."""
    calls = _enable_i3(monkeypatch, returncode=0)
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "windowId": 1, "active": True, "status": "complete",
        "url": "https://model-benchmarking.example.test/run",
        "title": "Model Benchmarking", "value": "x", "text": "x", "html": "x"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        for op in ("wake", "screenshot", "text", "getHtml", "tabs"):
            for extra in ({}, {"focus": True}):
                st, _ = _req(srv, "POST", "/cmd", dict(op=op, **extra))
                assert st == 200, f"{op} {extra} returned {st}"
        assert calls == [], (
            f"a non-activate op reached i3-msg — the gate is on the wrong "
            f"condition (argv: {calls})"
        )
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_i3_available_requires_display_and_i3msg(monkeypatch):
    """i3_available gates on BOTH a graphical session (DISPLAY) and a resolvable
    i3-msg — either missing → False (skip). Exercises the REAL implementation
    (the autouse fixture stubs the module attribute for hermeticity)."""
    monkeypatch.setattr(S.shutil, "which",
                        lambda n: "/run/current-system/sw/bin/i3-msg")
    monkeypatch.delenv("DISPLAY", raising=False)
    assert _REAL_I3_AVAILABLE() is False          # no DISPLAY → False
    monkeypatch.setenv("DISPLAY", ":0")
    assert _REAL_I3_AVAILABLE() is True           # DISPLAY + i3-msg → True
    # i3-msg NOT on PATH *and* no absolute fallback exists → False. Both the
    # which() lookup AND the absolute-fallback probe must miss (else the resolver
    # finds a real /run/current-system/... i3-msg on the graphical test host).
    monkeypatch.setattr(S.shutil, "which", lambda n: None)
    monkeypatch.setattr(S.os.path, "exists", lambda p: False)
    assert _REAL_I3_AVAILABLE() is False          # i3-msg absent → False


def test_resolve_i3_msg_prefers_which(monkeypatch):
    """_resolve_i3_msg returns the PATH-resolved i3-msg when one is on PATH."""
    monkeypatch.setattr(S.shutil, "which", lambda n: "/usr/bin/i3-msg")
    assert S._resolve_i3_msg() == "/usr/bin/i3-msg"


def test_resolve_i3_msg_falls_back_to_absolute_path(monkeypatch):
    """THE FIX: i3-msg is NOT on PATH (the minimal systemd --user service PATH),
    but a well-known absolute i3-msg EXISTS and is executable → resolve to it."""
    monkeypatch.setattr(S.shutil, "which", lambda n: None)
    fallback = S._I3_MSG_FALLBACKS[0]
    monkeypatch.setattr(S.os.path, "exists", lambda p: p == fallback)
    monkeypatch.setattr(S.os, "access", lambda p, mode: p == fallback)
    assert S._resolve_i3_msg() == fallback


def test_resolve_i3_msg_none_when_nothing_found(monkeypatch):
    """Nothing on PATH and no absolute fallback exists → None (→ i3 skipped)."""
    monkeypatch.setattr(S.shutil, "which", lambda n: None)
    monkeypatch.setattr(S.os.path, "exists", lambda p: False)
    assert S._resolve_i3_msg() is None


def test_i3_available_uses_absolute_fallback_off_path(monkeypatch):
    """i3_available is TRUE when DISPLAY is set and i3-msg resolves ONLY via the
    absolute fallback (which() misses) — the exact in-service condition the bug
    hit. This is what un-breaks `activate` under the minimal service PATH."""
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(S.shutil, "which", lambda n: None)
    fallback = S._I3_MSG_FALLBACKS[0]
    monkeypatch.setattr(S.os.path, "exists", lambda p: p == fallback)
    monkeypatch.setattr(S.os, "access", lambda p, mode: p == fallback)
    assert _REAL_I3_AVAILABLE() is True
    # DISPLAY absent → skipped regardless of a resolvable i3-msg.
    monkeypatch.delenv("DISPLAY", raising=False)
    assert _REAL_I3_AVAILABLE() is False


# --- THE BUG: two sessions interleave without clobbering ------------------- #
def test_two_sessions_interleaved_never_clobber():
    """Session A and B each own a tab; interleaved nav+read ops each dispatch
    against their OWN tabId — the clobber (A reads B's page) cannot happen."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101, 202]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open", "url": "https://a.test"}, session="A")
        _cmd(srv, {"op": "open", "url": "https://b.test"}, session="B")
        # Interleave: A nav, B nav, A read, B read.
        assert _cmd(srv, {"op": "nav", "url": "https://a2.test"}, session="A")[1]["result"]["data"]["tabId"] == 101
        assert _cmd(srv, {"op": "nav", "url": "https://b2.test"}, session="B")[1]["result"]["data"]["tabId"] == 202
        assert _cmd(srv, {"op": "getHtml"}, session="A")[1]["result"]["data"]["tabId"] == 101
        assert _cmd(srv, {"op": "getHtml"}, session="B")[1]["result"]["data"]["tabId"] == 202
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --- close / release ------------------------------------------------------- #
def test_close_drops_ownership_and_dispatches_remove():
    srv, reg = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        assert reg.owners_snapshot() == {("only", "A"): 101}
        st, body = _cmd(srv, {"op": "close"}, session="A")
        assert st == 200
        assert body["result"]["data"]["closed"] == 101
        assert reg.owners_snapshot() == {}
        # A close-shaped command (op=close, tabId=owned) was dispatched.
        close_cmds = [c for c in ext.dispatched if c["op"] == "close"]
        assert len(close_cmds) == 1 and close_cmds[0]["tabId"] == 101
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_close_without_owned_tab_409_no_owned_tab():
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only")
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _cmd(srv, {"op": "close"}, session="A")   # never opened
        assert st == 409
        assert body["error"] == "no_owned_tab"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_release_drops_ownership_without_dispatch():
    srv, reg = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        assert reg.owners_snapshot() == {("only", "A"): 101}
        st, body = _cmd(srv, {"op": "release"}, session="A")
        assert st == 200
        assert body["result"]["data"]["released"] == 1
        assert reg.owners_snapshot() == {}
        # release is server-side: the extension NEVER saw a release/close command.
        assert all(c["op"] not in ("release", "close") for c in ext.dispatched)
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_tabs_annotates_owned_tab_id():
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        st, body = _cmd(srv, {"op": "tabs"}, session="A")
        assert st == 200
        assert body["result"]["data"]["ownedTabId"] == 101
        # A different session owns nothing here → None.
        st, body = _cmd(srv, {"op": "tabs"}, session="B")
        assert body["result"]["data"]["ownedTabId"] is None
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --- backward compatibility ------------------------------------------------ #
def test_backward_compat_no_session_no_open_unchanged():
    """A SINGLE session with NO session header and NO open behaves EXACTLY as
    before: active-tab dispatch, no tabId injected."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=lambda c: {"hasTab": ("tabId" in c)})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml"})   # no X-Session-Id
        assert st == 200
        assert body["result"]["data"]["hasTab"] is False
        assert all("tabId" not in c for c in ext.dispatched)
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --- security: the session id is routing-only, never trusted for auth ------ #
def test_cmd_with_session_still_enforces_auth_and_host():
    srv, _ = _serve()
    try:
        # Wrong bearer + a session header → still 401 (session never grants auth).
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml"}, token="nope",
                        headers={S.HDR_SESSION_ID: "A"})
        assert st == 401 and body["error"] == "unauthorized"
        # Bad Host + a session header → still 403.
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml"},
                        host="evil.example.com",
                        headers={S.HDR_SESSION_ID: "A"})
        assert st == 403 and body["error"] == "bad_host"
    finally:
        srv.shutdown(); srv.server_close()


# --- per-tab FIFO serialization (Registry-level, deterministic) ------------ #
def _register_live(reg, iid="solo", key="one"):
    """Register a live instance via one idle poll (leaves last_poll recent so it
    counts live within CONNECT_STALE_S) and return its Instance."""
    reg.poll(iid, key, 0.01)
    with reg._cond:
        return reg._instances[key]


def test_same_tab_commands_serialize_fifo():
    """Two commands targeting the SAME tab are granted in arrival order: the
    second does not even enqueue until the first completes."""
    reg = S.Registry()
    inst = _register_live(reg)

    r1 = {}

    def sub1():
        r1["v"] = reg.submit({"op": "getHtml"}, 5.0, session_id="s1", tab=5)

    threading.Thread(target=sub1, daemon=True).start()
    assert _wait_until(lambda: len(inst.waiters) == 1), "cmd1 never enqueued"
    cid1 = next(iter(inst.waiters))

    # A second command to the SAME tab (5) must be FIFO-gated (not enqueued yet).
    threading.Thread(
        target=lambda: reg.submit({"op": "getHtml"}, 5.0, session_id="s2", tab=5),
        daemon=True).start()
    time.sleep(0.15)
    assert inst.waiters == {cid1}, "second same-tab command must NOT enqueue while cmd1 is in-flight"

    # Complete cmd1 → its turn is released → cmd2 enqueues.
    reg.deliver_result(cid1, {"id": cid1, "ok": True, "data": {}},
                       instance_id="solo")
    assert _wait_until(lambda: inst.waiters and next(iter(inst.waiters)) != cid1), \
        "cmd2 must enqueue once cmd1 completed"
    cid2 = next(iter(inst.waiters))
    reg.deliver_result(cid2, {"id": cid2, "ok": True, "data": {}},
                       instance_id="solo")


def test_different_tab_commands_do_not_block():
    """A command to a DIFFERENT tab enqueues immediately, even while another tab's
    command is in flight (no false serialization across tabs)."""
    reg = S.Registry()
    inst = _register_live(reg)

    def sub1():
        reg.submit({"op": "getHtml"}, 5.0, session_id="s1", tab=5)

    threading.Thread(target=sub1, daemon=True).start()
    assert _wait_until(lambda: len(inst.waiters) == 1)
    cid1 = next(iter(inst.waiters))

    # Different tab (6) — must enqueue right away (→ 2 waiters), not wait for cid1.
    threading.Thread(
        target=lambda: reg.submit({"op": "getHtml"}, 5.0, session_id="s2", tab=6),
        daemon=True).start()
    assert _wait_until(lambda: len(inst.waiters) == 2), \
        "a different-tab command must not be blocked by cmd1"
    for cid in list(inst.waiters):
        reg.deliver_result(cid, {"id": cid, "ok": True, "data": {}},
                           instance_id="solo")


def test_fifo_queued_command_respects_cmd_timeout():
    """A command queued behind an in-flight one on the same tab still times out
    at cmd_timeout (a queued command can never block forever)."""
    reg = S.Registry()
    inst = _register_live(reg)

    def sub1():
        # Never delivered → holds the tab turn until ITS own timeout.
        try:
            reg.submit({"op": "getHtml"}, 5.0, session_id="s1", tab=5)
        except S.BridgeTimeout:
            pass

    threading.Thread(target=sub1, daemon=True).start()
    assert _wait_until(lambda: len(inst.waiters) == 1)

    r2 = {}

    def sub2():
        try:
            reg.submit({"op": "getHtml"}, 0.3, session_id="s2", tab=5)
        except Exception as e:  # noqa: BLE001
            r2["err"] = type(e).__name__

    t2 = threading.Thread(target=sub2, daemon=True)
    t2.start()
    t2.join(3)
    assert r2.get("err") == "BridgeTimeout", \
        "a FIFO-queued command must still honour cmd_timeout"


# --- release_session: INSTANCE-SCOPED when a target is given --------------- #
# Ownership is keyed (instance, session), so ONE session legitimately owns a tab
# on EVERY connected Brave profile — the normal case on these hosts, where two
# profiles are connected at once. `release` used to delete every one of them.
#
# That is right for "this session is done" and WRONG for `emulate --reset
# --recreate`, whose whole job is fixing ONE tab on ONE profile: it silently
# orphaned the other profile's owned tab, after which a later bare op there fell
# back to the OPERATOR'S ACTIVE TAB — the exact blast radius the ownership map
# exists to prevent.
#
# RED-BEFORE-GREEN: on origin/main `release_session` takes no `target` at all, so
# the first test below raises TypeError and the second's assertion fails.
def _seed_owner(reg, inst_key, session, tab_id):
    with reg._cond:
        reg._owners[(inst_key, session)] = {"tab_id": tab_id,
                                            "last_seen": reg._clock()}


def test_release_session_scoped_to_target_leaves_other_instances_alone():
    reg = S.Registry()
    _seed_owner(reg, "work", "S", 101)
    _seed_owner(reg, "personal", "S", 202)
    _seed_owner(reg, "work", "OTHER", 303)
    assert reg.release_session("S", target="work") == 1
    assert reg.owners_snapshot() == {("personal", "S"): 202, ("work", "OTHER"): 303}, \
        "a scoped release must touch ONLY (target, session)"


def test_release_session_unscoped_still_drops_every_instance():
    """The other half of the contract — a bare `browser release` means 'this
    session is finished', and must keep clearing every profile."""
    reg = S.Registry()
    _seed_owner(reg, "work", "S", 101)
    _seed_owner(reg, "personal", "S", 202)
    assert reg.release_session("S") == 2
    assert reg.owners_snapshot() == {}


def test_release_session_resolves_a_target_by_instance_id_too():
    """`--instance` accepts a routing key OR an instanceId (_find_locked matches
    both), so the scope must resolve the same way — otherwise a caller who used
    the id gets a release that matches nothing and silently keeps ownership."""
    reg = S.Registry()
    inst = _register_live(reg, iid="brave-abc", key="work")
    assert inst.instance_id == "brave-abc"
    _seed_owner(reg, "work", "S", 101)
    _seed_owner(reg, "personal", "S", 202)
    assert reg.release_session("S", target="brave-abc") == 1
    assert reg.owners_snapshot() == {("personal", "S"): 202}


def test_release_session_unknown_target_releases_nothing():
    """FAIL CLOSED. A target that resolves to no instance and matches no owner key
    must NOT fall back to the unscoped sweep — silently widening the blast radius
    when the caller asked to narrow it is the whole bug this argument fixes."""
    reg = S.Registry()
    _seed_owner(reg, "work", "S", 101)
    _seed_owner(reg, "personal", "S", 202)
    assert reg.release_session("S", target="nope") == 0
    assert reg.owners_snapshot() == {("work", "S"): 101, ("personal", "S"): 202}


# --- TTL reclaim: released, NOT closed ------------------------------------- #
def test_owner_ttl_reclaims_without_closing(monkeypatch):
    """Ownership idle past the TTL is RELEASED (mapping dropped) using an injected
    clock — never a real Date.now(). Reclaim must NOT dispatch a close."""
    clock = [1000.0]
    reg = S.Registry(clock=lambda: clock[0], owner_ttl=10.0)
    with reg._cond:
        reg._owners[("k", "s")] = {"tab_id": 42, "last_seen": clock[0]}
    assert reg.owners_snapshot() == {("k", "s"): 42}

    events = []
    monkeypatch.setattr(S, "log", lambda ev, **kw: events.append(ev))
    clock[0] += 11.0                      # advance past the idle TTL
    assert reg.owners_snapshot() == {}    # reclaimed on the next touch
    assert "owner_reclaim" in events


def test_owner_ttl_reclaim_falls_back_to_active_not_close():
    """After a session's ownership expires, its next op routes with NO injected
    tabId (active-tab fallback) and NO close is ever dispatched — the real tab is
    left open. Registry-level with an injected clock (no real Date.now())."""
    clock = [1000.0]
    reg = S.Registry(clock=lambda: clock[0], owner_ttl=10.0)

    dispatched = []
    stop = threading.Event()

    def poller():
        while not stop.is_set():
            cmd = reg.poll("solo", "one", 0.05)
            if cmd is not None and cmd is not S.SUPERSEDED:
                dispatched.append(cmd)
                reg.deliver_result(cmd["id"], {"id": cmd["id"], "ok": True,
                                               "data": {}}, instance_id="solo")

    threading.Thread(target=poller, daemon=True).start()
    try:
        assert _wait_until(lambda: len(reg.snapshot()) == 1)
        # Model an owned tab, then let it go idle past the TTL.
        with reg._cond:
            reg._owners[("one", "S")] = {"tab_id": 55, "last_seen": clock[0]}
        clock[0] += 11.0                  # past owner TTL (10), within stale (40)
        reg.submit({"op": "getHtml"}, 3.0, session_id="S")   # no tab arg
        assert reg.owners_snapshot() == {}                    # ownership reclaimed
        assert dispatched, "the op was never dispatched"
        assert all("tabId" not in c for c in dispatched)      # active-tab fallback
        assert all(c["op"] != "close" for c in dispatched)    # tab NOT closed
    finally:
        stop.set()


# --------------------------------------------------------------------------- #
# Fix 1 — self-heal when an owned tab is gone
#
# When the user manually closes an owned BACKGROUND tab, the next tab-scoped op
# dispatches the stale tabId → the extension returns `owned_tab_gone` (ok:false).
# The session must DROP its ownership so the next command self-heals to the
# active-tab fallback (instead of staying wedged to the dead tab for OWNER_TTL),
# and `close` must clear the mapping even when the remove reports the tab gone.
# --------------------------------------------------------------------------- #
def _tab_gone_exec(open_ids, gone):
    """Executor: `open` hands out the next id; a tab-scoped op whose injected
    tabId is in the mutable `gone` set returns an `owned_tab_gone` error envelope;
    otherwise it echoes the injected tabId (None → active-tab fallback)."""
    it = iter(open_ids)

    def _exec(cmd):
        if cmd["op"] == "open":
            return {"tabId": next(it), "url": cmd.get("url")}
        tid = cmd.get("tabId")
        if tid is not None and tid in gone:
            return {"__error__": "owned_tab_gone"}
        if cmd["op"] == "close":
            return {"closed": tid}
        return {"tabId": tid, "op": cmd["op"]}
    return _exec


def test_is_tab_gone_helper():
    assert S._is_tab_gone("owned_tab_gone") is True
    assert S._is_tab_gone("No tab with id: 42.") is True   # chrome's raw message
    assert S._is_tab_gone("something_else") is False
    assert S._is_tab_gone(None) is False
    assert S._is_tab_gone(123) is False


def test_owned_tab_gone_drops_ownership_and_self_heals():
    """A tab-scoped op that fails with `owned_tab_gone` for the session's OWNED
    tab drops the mapping; the NEXT op falls back to the active tab (no tabId)."""
    srv, reg = _serve()
    gone = set()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_gone_exec([101], gone))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        assert reg.owners_snapshot() == {("only", "A"): 101}
        # The user closes tab 101 out-of-band.
        gone.add(101)
        st, body = _cmd(srv, {"op": "getHtml"}, session="A")
        assert st == 200                          # HTTP ok; op-level failure
        assert body["result"]["ok"] is False
        assert body["result"]["error"] == "owned_tab_gone"
        assert reg.owners_snapshot() == {}        # ownership self-healed (dropped)
        # Next op now falls back to the active tab (no tabId injected).
        st, body = _cmd(srv, {"op": "getHtml"}, session="A")
        assert st == 200 and body["result"]["ok"] is True
        assert body["result"]["data"]["tabId"] is None
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_close_drops_ownership_even_when_tab_already_gone():
    """`close` clears the mapping UNCONDITIONALLY — even when the extension
    reports the tab already gone (remove → ok:false). Without this, a tab the
    user closed out-of-band could never be cleared and wedged the session."""
    srv, reg = _serve()
    gone = set()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_gone_exec([101], gone))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        assert reg.owners_snapshot() == {("only", "A"): 101}
        gone.add(101)                              # closed out-of-band
        st, body = _cmd(srv, {"op": "close"}, session="A")
        assert st == 200
        assert body["result"]["ok"] is False       # remove reported it gone
        assert reg.owners_snapshot() == {}          # mapping cleared regardless
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_explicit_tab_gone_does_not_evict_owned_mapping():
    """An explicit --tab to a DIFFERENT (gone) tab must NOT evict the session's
    healthy owned mapping — only the session's OWN owned tab going gone self-heals."""
    srv, reg = _serve()
    gone = {999}
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_gone_exec([101], gone))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")      # owns 101
        # Target an unrelated, already-gone tab 999 via --tab.
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml", "tab": 999},
                        headers={S.HDR_SESSION_ID: "A"})
        assert st == 200 and body["result"]["ok"] is False
        assert body["result"]["error"] == "owned_tab_gone"
        # The session's own tab (101) is untouched.
        assert reg.owners_snapshot() == {("only", "A"): 101}
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Fix 2 — a double `open` must not orphan a real tab
#
# A second `open` from a session used to overwrite its ownership with a brand-new
# tabId, orphaning the FIRST real tab (no ownership → never closed → leaked). The
# server now passes the owned tabId as `reuseTabId`; the extension returns the
# SAME tab when it is still live (idempotent), and only opens fresh when gone.
# --------------------------------------------------------------------------- #
def _open_reuse_exec(new_ids, live):
    """Model the SW `open` reuse logic: honour `reuseTabId` when that tab is still
    in the mutable `live` set (idempotent), else create the next new id. `close`
    removes from `live`; other ops echo their injected tabId."""
    it = iter(new_ids)

    def _exec(cmd):
        if cmd["op"] == "open":
            reuse = cmd.get("reuseTabId")
            if reuse is not None and reuse in live:
                return {"tabId": reuse, "url": cmd.get("url"), "reused": True}
            tid = next(it)
            live.add(tid)
            return {"tabId": tid, "url": cmd.get("url")}
        if cmd["op"] == "close":
            live.discard(cmd.get("tabId"))
            return {"closed": cmd.get("tabId")}
        return {"tabId": cmd.get("tabId"), "op": cmd["op"]}
    return _exec


def test_double_open_is_idempotent_no_orphan():
    """A second `open` returns the EXISTING owned tab (reused) — no second real
    tab is created, so the first tab is never orphaned/leaked."""
    srv, reg = _serve()
    live = set()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_open_reuse_exec([101, 202], live))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _cmd(srv, {"op": "open"}, session="A")
        assert body["result"]["data"]["tabId"] == 101
        assert live == {101}
        # Second open by the SAME session → the server injects reuseTabId=101 and
        # the extension returns the SAME live tab (no new 202 tab created).
        st, body = _cmd(srv, {"op": "open"}, session="A")
        assert st == 200
        assert body["result"]["data"]["tabId"] == 101
        assert body["result"]["data"].get("reused") is True
        assert live == {101}, "a second real tab was created → orphaned/leaked"
        assert reg.owners_snapshot() == {("only", "A"): 101}
        # The server injected the reuse hint on the second open.
        opens = [c for c in ext.dispatched if c["op"] == "open"]
        assert opens[1].get("reuseTabId") == 101
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_open_after_owned_tab_gone_opens_fresh():
    """If the previously-owned tab is GONE, `open` creates a fresh tab and the
    ownership updates to it (the stale mapping is replaced, not reused)."""
    srv, reg = _serve()
    live = set()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_open_reuse_exec([101, 202], live))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")       # owns 101
        live.discard(101)                            # user closed it out-of-band
        st, body = _cmd(srv, {"op": "open"}, session="A")
        assert st == 200
        assert body["result"]["data"]["tabId"] == 202    # fresh tab
        assert body["result"]["data"].get("reused") is None
        assert reg.owners_snapshot() == {("only", "A"): 202}
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Fix 3 — a malformed `tab` from a raw API caller is a clean 400, never a 500
# --------------------------------------------------------------------------- #
def test_coerce_tab_helper():
    assert S._coerce_tab(5) == (5, None)
    assert S._coerce_tab(0) == (0, None)
    assert S._coerce_tab("77") == (77, None)
    assert S._coerce_tab([1, 2]) == (None, "bad_tab")
    assert S._coerce_tab({"x": 1}) == (None, "bad_tab")
    assert S._coerce_tab(True) == (None, "bad_tab")      # bool is not a tab id
    assert S._coerce_tab(1.5) == (None, "bad_tab")
    assert S._coerce_tab(-3) == (None, "bad_tab")
    assert S._coerce_tab("x") == (None, "bad_tab")


def test_malformed_tab_returns_400_bad_tab_not_500():
    """A raw token-holder POSTing an unhashable `tab` (list/dict) gets a clean
    400 bad_tab — never an uncaught TypeError → 500."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only")
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        for bad in ([1, 2], {"x": 1}, True, 1.5, "nope"):
            st, body = _req(srv, "POST", "/cmd", {"op": "getHtml", "tab": bad})
            assert st == 400, f"tab={bad!r} → {st}"
            assert body["error"] == "bad_tab"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_numeric_string_tab_is_coerced_and_routes():
    """A valid numeric-string `tab` is coerced to an int and routes normally."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=lambda c: {"tabId": c.get("tabId")})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml", "tab": "5"},
                        headers={S.HDR_SESSION_ID: "A"})
        assert st == 200
        assert body["result"]["data"]["tabId"] == 5
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Fix 4 — explicit --tab overrides owned-tab routing (the subagent escape hatch)
#
# Sibling subagents of one parent share a session id (CLAUDE_CODE_SESSION_ID +
# $TMUX_PANE are inherited, and no subagent-unique env var exists), so they would
# own the SAME tab. The robust isolation is explicit tab handles: each driver
# `open`s and then threads its OWN --tab on every op. This asserts --tab fully
# overrides the session-ownership mapping so two same-session drivers never collide.
# --------------------------------------------------------------------------- #
def test_explicit_tab_overrides_owned_mapping():
    srv, reg = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")          # session A owns tab 101
        # An explicit --tab 999 must target 999, NOT the owned 101.
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml", "tab": 999},
                        headers={S.HDR_SESSION_ID: "A"})
        assert st == 200
        assert body["result"]["data"]["tabId"] == 999, \
            "--tab must override the session's owned-tab routing"
        # The override does not disturb the owned mapping.
        assert reg.owners_snapshot() == {("only", "A"): 101}
        # And with no --tab the same session still routes to its owned tab.
        st, body = _cmd(srv, {"op": "getHtml"}, session="A")
        assert body["result"]["data"]["tabId"] == 101
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Fix 5 — per-instance concurrency backstop (rate limit + queue-depth cap)
#
# Motivation: an audit found a 44,061-event storm (43,991 evals in one hour,
# ~13/sec sustained) that saturated the SINGLE serial extension connection with
# NO backpressure (latency 10ms→5.5s). The server now enforces, PER-INSTANCE, a
# token-bucket rate limit + a pending-command depth cap; an over-limit /cmd is
# rejected with HTTP 429 (caller-visible backpressure). The admission decision
# (`_admit_locked`) is unit-tested directly with an INJECTED clock (scripts forbid
# real-time nondeterminism), plus HTTP round-trips prove the 429 + telemetry.
# --------------------------------------------------------------------------- #
def _mk_instance(reg, key="k", burst=0.0, now=0.0):
    """A bare Instance for direct _admit_locked unit tests (no HTTP/registry)."""
    return S.Instance(key, key, "", now, burst=burst)


def _instance_pending(reg):
    with reg._cond:
        return sum(i.pending for i in reg._instances.values())


# --- token bucket: burst passes, then rate_limited (frozen clock) ---------- #
def test_admit_token_bucket_burst_then_rate_limited():
    """Within `burst`, admits pass; the next over the empty bucket → rate_limited.
    Frozen clock ⇒ no refill ⇒ deterministic."""
    clock = [100.0]
    reg = S.Registry(clock=lambda: clock[0], rate_per_sec=5.0, burst=3,
                     max_queue=1000)
    inst = _mk_instance(reg, burst=3, now=clock[0])
    with reg._cond:
        for i in range(3):
            assert reg._admit_locked(inst) is None, f"burst slot {i} must admit"
            inst.pending += 1              # caller (submit) claims the slot
        verdict = reg._admit_locked(inst)
    assert verdict is not None
    reason, retry_after = verdict
    assert reason == "rate_limited"
    assert retry_after > 0                 # a positive Retry-After-style hint
    assert inst.pending == 3               # ONLY the 3 admitted bumped pending


# --- token bucket refills over (fake) time → dispatches resume ------------- #
def test_admit_token_bucket_refills_over_time():
    clock = [100.0]
    reg = S.Registry(clock=lambda: clock[0], rate_per_sec=5.0, burst=2,
                     max_queue=1000)
    inst = _mk_instance(reg, burst=2, now=clock[0])
    with reg._cond:
        assert reg._admit_locked(inst) is None
        assert reg._admit_locked(inst) is None
        assert reg._admit_locked(inst)[0] == "rate_limited"   # bucket empty
        clock[0] += 0.2                                       # +1 token @5/sec
        assert reg._admit_locked(inst) is None                # resumed
        assert reg._admit_locked(inst)[0] == "rate_limited"   # empty again
        clock[0] += 100.0                                     # long idle
        # Refill is CAPPED at `burst` (2) — no unbounded accrual.
        assert reg._admit_locked(inst) is None
        assert reg._admit_locked(inst) is None
        assert reg._admit_locked(inst)[0] == "rate_limited"


# --- queue-depth cap: fill to MAX_QUEUE → queue_full; drain resumes -------- #
def test_admit_queue_depth_cap():
    """Rate limit disabled so ONLY the depth cap is exercised."""
    reg = S.Registry(rate_per_sec=0, max_queue=2)
    inst = _mk_instance(reg, burst=0)
    with reg._cond:
        # The caller (submit) bumps pending on each admit; the depth cap reads it.
        assert reg._admit_locked(inst) is None    # pending 1
        inst.pending += 1
        assert reg._admit_locked(inst) is None    # pending 2
        inst.pending += 1
        verdict = reg._admit_locked(inst)         # 2 >= cap 2 → queue_full
        assert verdict is not None and verdict[0] == "queue_full"
        assert inst.pending == 2                  # a rejection does NOT bump pending
        inst.pending -= 1                         # a command completes (drains)
        assert reg._admit_locked(inst) is None    # slot freed → resumes


# --- strict per-instance isolation ----------------------------------------- #
def test_admit_is_strictly_per_instance():
    """Throttling instance A must NOT throttle instance B — independent buckets
    AND independent pending."""
    reg = S.Registry(rate_per_sec=5.0, burst=1, max_queue=1)
    a = _mk_instance(reg, key="a", burst=1)
    b = _mk_instance(reg, key="b", burst=1)
    with reg._cond:
        assert reg._admit_locked(a) is None                       # a: token+slot
        a.pending += 1                                            # caller claims slot
        assert reg._admit_locked(a)[0] in ("rate_limited", "queue_full")
        assert reg._admit_locked(b) is None                       # b unaffected
        b.pending += 1
        assert a.pending == 1 and b.pending == 1


# --- disable path (rate=0 AND max_queue=0) never throttles ----------------- #
def test_admit_disabled_never_throttles():
    reg = S.Registry(rate_per_sec=0, max_queue=0)
    inst = _mk_instance(reg, burst=0)
    with reg._cond:
        for _ in range(1000):
            assert reg._admit_locked(inst) is None
            inst.pending += 1                     # caller (submit) claims the slot
    assert inst.pending == 1000


# --- a rejected submit leaves NO turnstile/waiter residue (no deadlock) ---- #
def test_rate_limited_submit_leaves_no_turnstile_residue():
    """A rejected submit raises RateLimited IMMEDIATELY and leaves no residue —
    no tab-queue ticket, no waiter, pending balanced — so it can neither wedge
    the FIFO turnstile nor leak a queue slot."""
    reg = S.Registry(rate_per_sec=0.001, burst=1, max_queue=1000)  # ~no refill
    inst = _register_live(reg)
    stop = threading.Event()

    def deliverer():
        while not stop.is_set():
            cmd = reg.poll("solo", "one", 0.05)
            if cmd is not None and cmd is not S.SUPERSEDED:
                reg.deliver_result(cmd["id"], {"id": cmd["id"], "ok": True,
                                               "data": {}}, instance_id="solo")

    threading.Thread(target=deliverer, daemon=True).start()
    try:
        reg.submit({"op": "getHtml"}, 3.0, session_id="s1", tab=5)  # spends token
        with pytest.raises(S.RateLimited) as ei:
            reg.submit({"op": "getHtml"}, 3.0, session_id="s2", tab=5)
        assert ei.value.reason == "rate_limited"
        assert ei.value.key == "one"
        with reg._cond:
            assert inst.waiters == set()      # no leaked waiter
            assert reg._tab_queues == {}      # no leaked turnstile ticket
            assert inst.pending == 0          # admitted slot released
    finally:
        stop.set()


# --- Fix 1: BURST<1 with rate>0 must NOT silently brick the instance -------- #
def test_burst_below_one_with_rate_does_not_lock_out():
    """A sub-1 burst while RATE_PER_SEC>0 used to permanently brick the instance:
    rl_tokens started at 0 and the refill cap was min(0,…)=0, so `rl_tokens<1.0`
    was ALWAYS true → EVERY /cmd returned rate_limited forever (silent lockout).
    The burst is now clamped to a floor of 1 so a normal command is admitted."""
    for bad_burst in (0, 0.5):
        reg = S.Registry(rate_per_sec=5.0, burst=bad_burst)
        assert reg._burst == 1.0                  # clamped up to the sane floor
        inst = _register_live(reg)                # created with the clamped burst
        with reg._cond:
            # First admit must PASS (full bucket of 1) — not rate_limited forever.
            assert reg._admit_locked(inst) is None
    # RATE_PER_SEC=0 (unlimited) is the real disable path — honoured regardless
    # of burst, so a 0 burst there is left untouched (no spurious clamp/log).
    reg0 = S.Registry(rate_per_sec=0, burst=0)
    assert reg0._burst == 0.0


# --- Fix 2: a post-admission raise must not leak the pending slot ----------- #
def test_pending_slot_released_on_post_admission_raise(monkeypatch):
    """The pending increment lives INSIDE the try whose finally releases it, so
    a raise AFTER admission but BEFORE the turnstile completes cannot strand a
    `pending` slot (which would eventually wedge the depth cap into a permanent
    queue_full). Force such a raise mid-submit (token_hex blows up) and assert
    the balance returns to 0 and no turnstile ticket leaks."""
    reg = S.Registry(rate_per_sec=0, max_queue=1000)   # admit always succeeds
    inst = _register_live(reg)

    def _boom(*a, **k):
        raise RuntimeError("forced post-admission failure")
    # token_hex is called inside submit's try, AFTER pending is bumped.
    monkeypatch.setattr(S.secrets, "token_hex", _boom)

    with pytest.raises(RuntimeError):
        reg.submit({"op": "getHtml"}, 3.0)   # admitted, then raises past the bump
    with reg._cond:
        assert inst.pending == 0             # released by the finally, no leak
        assert reg._tab_queues == {}         # no leaked turnstile ticket either


# --- HTTP: burst over the bucket → 429 rate_limited + throttle telemetry --- #
def test_cmd_rate_limited_returns_429_and_emits_throttle(telemetry):
    """Over-burst /cmd → HTTP 429 rate_limited (with a retry_after hint), a
    DISTINCT throttle telemetry event that is metadata-only and carries a COARSE
    session hash (never the raw id), and the server stays responsive (no wedge)."""
    spool_dir = telemetry
    reg = S.Registry(rate_per_sec=0.001, burst=2, max_queue=1000)  # ~no refill
    srv, _ = _serve(registry=reg)
    ext = FakeExtension(srv, executor=lambda c: {"url": "https://x.test"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _req(srv, "POST", "/cmd", {"op": "getHtml"})[0] == 200   # burst 1
        assert _req(srv, "POST", "/cmd", {"op": "getHtml"})[0] == 200   # burst 2
        st, body = _cmd(srv, {"op": "getHtml"}, session="floodsession")  # over
        assert st == 429
        assert body["error"] == "rate_limited"
        assert body["retry_after"] > 0
        # 3 events: 2 ok + 1 throttled.
        evs = _wait_events(spool_dir, 3)
        throttled = [e for e in evs
                     if json.loads(e["payload"]).get("outcome") == "throttled"]
        assert len(throttled) == 1, f"expected one throttle event, got {evs}"
        p = json.loads(throttled[0]["payload"])
        assert p["op"] == "getHtml"
        assert p["reason"] == "rate_limited"
        # Attribution is a COARSE, non-reversible hash of X-Session-Id — NOT raw.
        assert p["sess"] == hashlib.sha256(b"floodsession").hexdigest()[:8]
        raw = _log_file(spool_dir).read_text()
        # This id carries no tier TAG, so it is `unknown` and no join key may be
        # written — the raw id appears nowhere. (Since the session-key fix a
        # `claude:`-tagged id DOES reach the `session` column, deliberately; see
        # the join-key section below. The invariant here is the fail-closed one,
        # not "never".)
        assert "floodsession" not in raw
        assert p["sess_src"] == "unknown"
        # No deadlock: the server still answers after shedding load.
        assert _req(srv, "GET", "/health")[0] == 200
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --- HTTP: pending at MAX_QUEUE → next /cmd is 429 queue_full (immediate) --- #
def test_cmd_queue_full_returns_429():
    """Two stuck (un-answered) in-flight commands fill an instance's pending to
    MAX_QUEUE; the next /cmd is rejected FAST with 429 queue_full (it does not
    block behind the queue). Rate disabled so ONLY the cap fires."""
    reg = S.Registry(rate_per_sec=0, max_queue=2)
    srv, _ = _serve(cmd_timeout=5.0, poll_timeout=5.0, registry=reg)
    ext = FakeExtension(srv, swallow=True)     # picks up but never answers
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        for t in (1, 2):     # different tabs → both admitted + pending
            threading.Thread(
                target=lambda tt=t: _req(srv, "POST", "/cmd",
                                         {"op": "getHtml", "tab": tt}),
                daemon=True).start()
        assert _wait_until(lambda: _instance_pending(reg) >= 2), \
            "two in-flight commands never filled the queue"
        t0 = time.time()
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml", "tab": 3})
        assert st == 429 and body["error"] == "queue_full"
        assert time.time() - t0 < 2.0, "queue_full must reject immediately"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --- default knobs never throttle a legitimate small burst ----------------- #
def test_default_knobs_do_not_throttle_normal_use():
    """With PRODUCTION defaults, a normal workflow (open + a handful of ops) is
    NEVER throttled — the storm-only guarantee for legit use."""
    reg = S.Registry()   # default rate=5/s, burst=20, max_queue=32
    srv, _ = _serve(registry=reg)
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        assert _cmd(srv, {"op": "open"}, session="A")[0] == 200
        for _ in range(10):            # 10 rapid ops, well under burst 20
            st, _b = _cmd(srv, {"op": "getHtml"}, session="A")
            assert st == 200, "a normal small burst must never be throttled"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Skill side: a 429 from /cmd makes the `browser` CLI print a back-off message
# and exit non-zero (the runaway-loop backpressure signal). Runs the REAL bash
# entrypoint against an in-process fake server that always answers /cmd with 429.
# Skipped where curl is unavailable (the CLI shells out to curl).
# --------------------------------------------------------------------------- #
BROWSER_BIN = Path(__file__).resolve().parent.parent / "browser"

# 🔴 A TEST'S SAFETY-NET TIMEOUT MUST NOT BE TIGHTER THAN THE BOUND OF THE THING IT
# INVOKES. The CLI bounds its own HTTP call at `curl -m 60`; every CLI-spawning test
# here used `timeout=30`, i.e. the test's net always fired FIRST. A stall therefore
# surfaced as an opaque subprocess.TimeoutExpired instead of the CLI's own
# attributable error — and, being wall-clock, it flaked under CI load: measured
# 2026-08-25, `test_browser_cli_backs_off_on_429` failed exactly this way in the
# devrc-pytests gate while the SAME nix derivation
# (9cvfsmpjq6ip5yiv51mk8mf1f9zazpdz) built green locally. All ten sites shared the
# defect. 90 > 60 leaves the CLI's own bound to govern, so a stall now reports what
# the CLI says rather than that the test ran out of patience.
# Pinned by `test_cli_subprocess_timeouts_outrank_the_cli_own_curl_bound`.
CLI_TIMEOUT_S = 90


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
# --------------------------------------------------------------------------- #
# 🔴 THE ORDERING BETWEEN A TEST'S SAFETY NET AND THE CLI'S OWN BOUND.
#
# This is the guard for CLI_TIMEOUT_S. It reads the REAL bound out of the CLI
# rather than restating it, so raising `curl -m` without revisiting these tests
# fails HERE instead of becoming ten wall-clock flakes nobody can attribute.
#
# It also refuses a LITERAL timeout at a CLI-spawning site: the defect was not one
# bad number, it was ten copies of one: a predicate duplicated across N call sites
# is wrong at N-1 of them. A new site must use the constant.
# --------------------------------------------------------------------------- #
def test_cli_subprocess_timeouts_outrank_the_cli_own_curl_bound():
    cli = open(str(BROWSER_BIN)).read()
    caps = re.findall(r"(?:^|\s)-m\s+(\d+)", cli)
    assert caps, "could not find `curl -m <n>` in the CLI — retarget this test"
    curl_max = max(int(c) for c in caps)
    assert CLI_TIMEOUT_S > curl_max, (
        f"CLI_TIMEOUT_S ({CLI_TIMEOUT_S}s) must EXCEED the CLI's own curl bound "
        f"({curl_max}s), or a stall fires the test's net first and reports an opaque "
        f"TimeoutExpired instead of the CLI's own error")

    src = open(__file__).read()
    literal = re.findall(
        r"subprocess\.run\(\s*\[str\(BROWSER_BIN\)[^)]*?timeout=(\d+)", src, re.S)
    assert not literal, (
        f"{len(literal)} CLI-spawning site(s) use a LITERAL timeout {literal} instead "
        f"of CLI_TIMEOUT_S — one rule, one place, or the ordering rots at the copy "
        f"someone forgets")


def test_browser_cli_backs_off_on_429(tmp_path):
    class _H(S.BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: A003
            pass

        def do_POST(self):
            ln = int(self.headers.get("Content-Length") or 0)
            if ln:
                self.rfile.read(ln)
            payload = json.dumps({"ok": False, "error": "rate_limited",
                                  "retry_after": 1.5}).encode("utf-8")
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    srv = S.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tokf = tmp_path / "token"
    tokf.write_text("smoke-token\n")
    env = dict(os.environ)
    env.update(BROWSER_BRIDGE_HOST="127.0.0.1",
               BROWSER_BRIDGE_PORT=str(port),
               BROWSER_BRIDGE_TOKEN_FILE=str(tokf))
    try:
        r = subprocess.run([str(BROWSER_BIN), "eval", "1+1"], env=env,
                           capture_output=True, text=True, timeout=CLI_TIMEOUT_S)
        assert r.returncode != 0, "a 429 must make the CLI exit non-zero"
        low = r.stderr.lower()
        assert "rate-limited" in low or "back off" in low, \
            f"expected a back-off message on stderr, got: {r.stderr!r}"
    finally:
        srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Skill side: `browser text [selector] [--max-bytes N]` builds the right /cmd
# body (op=text + selector + maxBytes). Runs the REAL bash entrypoint against an
# in-process fake server that CAPTURES the posted body and returns a canned
# result. Skipped where curl is unavailable.
# --------------------------------------------------------------------------- #
class _CaptureCmdServer:
    """A minimal /cmd server that records the last posted body and answers with a
    canned success envelope (so the CLI's `text` subcommand exits 0)."""

    def __init__(self):
        self.bodies = []

    def handler(self):
        outer = self

        class _H(S.BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: A003
                pass

            def do_POST(self):
                ln = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(ln) if ln else b"{}"
                try:
                    outer.bodies.append(json.loads(raw))
                except ValueError:
                    outer.bodies.append(None)
                payload = json.dumps({"ok": True, "result": {
                    "id": "x", "ok": True,
                    "data": {"url": "https://x.test", "title": "X",
                             "text": "hello world"}}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        return _H


def _run_browser(args, tmp_path):
    srv_state = _CaptureCmdServer()
    srv = S.ThreadingHTTPServer(("127.0.0.1", 0), srv_state.handler())
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tokf = tmp_path / "token"
    tokf.write_text("smoke-token\n")
    env = dict(os.environ)
    env.update(BROWSER_BRIDGE_HOST="127.0.0.1", BROWSER_BRIDGE_PORT=str(port),
               BROWSER_BRIDGE_TOKEN_FILE=str(tokf))
    try:
        r = subprocess.run([str(BROWSER_BIN), *args], env=env,
                           capture_output=True, text=True, timeout=CLI_TIMEOUT_S)
        return r, srv_state.bodies
    finally:
        srv.shutdown(); srv.server_close()


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_text_default_maxbytes_no_selector(tmp_path):
    r, bodies = _run_browser(["text"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert bodies, "no /cmd body captured"
    b = bodies[-1]
    assert b["op"] == "text"
    assert b["maxBytes"] == 32768          # the documented default cap
    assert "selector" not in b             # no selector given → field omitted


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_text_selector_and_maxbytes(tmp_path):
    r, bodies = _run_browser(["text", "main.content", "--max-bytes", "500"],
                             tmp_path)
    assert r.returncode == 0, r.stderr
    b = bodies[-1]
    assert b["op"] == "text"
    assert b["selector"] == "main.content"
    assert b["maxBytes"] == 500


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_text_rejects_bad_maxbytes(tmp_path):
    r, _ = _run_browser(["text", "--max-bytes", "not-a-number"], tmp_path)
    assert r.returncode != 0
    assert "max-bytes" in r.stderr.lower()


# --------------------------------------------------------------------------- #
# CDP (chrome.debugger) ops: frames / click / type / key + --frame reads.
# The server stays op-agnostic about CDP mechanics — it validates the op set,
# tab-scopes + routes to the owned/target tab, and forwards the typed params
# (frame/selector/text/key) verbatim to the extension. These assert exactly that,
# plus the metadata-only telemetry + rate-limit coverage for the new ops. The
# real chrome.debugger attach/detach behaviour is unit-tested in protocol.js /
# cdp_protocol.test.mjs and verified manually against live Brave (see the PR body).
# --------------------------------------------------------------------------- #
def test_cdp_ops_registered_in_contract():
    """frames/click/type/key are dispatchable + tab-scoped, with required fields."""
    for op in ("frames", "click", "type", "key"):
        assert op in S.ALLOWED_OPS, f"{op} must be an allowed op"
        assert op in S.TAB_SCOPED_OPS, f"{op} must be tab-scoped (acts on one tab)"
    assert S.REQUIRED_FIELDS["click"] == ("selector",)
    assert S.REQUIRED_FIELDS["type"] == ("text",)
    assert S.REQUIRED_FIELDS["key"] == ("key",)
    # frames takes no skill-supplied field.
    assert "frames" not in S.REQUIRED_FIELDS


def test_click_requires_selector_400():
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "click"})
        assert st == 400
        assert body["error"] == "missing_field:selector"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_type_requires_text_400():
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "type"})
        assert st == 400
        assert body["error"] == "missing_field:text"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_key_requires_key_400():
    srv, _ = _serve()
    ext = FakeExtension(srv)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "key"})
        assert st == 400
        assert body["error"] == "missing_field:key"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_cdp_ops_route_to_owned_session_tab():
    """frames/click/type/key are tab-scoped → they route to the session's owned tab
    (the CDP attach on the extension side is thereby confined to the owned tab)."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        for body in ({"op": "frames"}, {"op": "click", "selector": "#go"},
                     {"op": "type", "text": "hi"}, {"op": "key", "key": "Enter"}):
            st, resp = _cmd(srv, body, session="A")
            assert st == 200, (body, resp)
            assert resp["result"]["data"]["tabId"] == 101, \
                f"{body['op']} must route to A's owned tab 101"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_cdp_params_forwarded_verbatim():
    """frame/selector/text/key are forwarded to the extension command untouched
    (they are typed op params, not skill-side routing hints like target/tab)."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=lambda c: {"url": "https://x.test", "ok": True})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _req(srv, "POST", "/cmd",
             {"op": "click", "selector": "#run", "frame": "model-benchmarking"})
        _req(srv, "POST", "/cmd", {"op": "type", "text": "a prompt", "selector": "#p"})
        _req(srv, "POST", "/cmd", {"op": "key", "key": "Enter"})
        _req(srv, "POST", "/cmd", {"op": "text", "frame": "F1"})
        by_op = {c["op"]: c for c in ext.dispatched}
        assert by_op["click"]["selector"] == "#run"
        assert by_op["click"]["frame"] == "model-benchmarking"
        assert by_op["type"]["text"] == "a prompt"
        assert by_op["type"]["selector"] == "#p"
        assert by_op["key"]["key"] == "Enter"
        assert by_op["text"]["frame"] == "F1"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_frames_telemetry_metadata_only(telemetry):
    """PRIVACY: a `frames` result lists frame URLs (incl. cross-origin ones), but
    telemetry emits ONLY the op + the bare TOP-LEVEL domain — never the frame URLs."""
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "url": "https://civitai.com/",
        "frames": [
            {"frameId": "MAIN", "url": "https://civitai.com/", "name": ""},
            {"frameId": "F1", "name": "bench",
             "url": "https://model-benchmarking.example.test/secret-app-path?token=abc"},
        ]})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "frames"})
        assert st == 200
        # round-trip sanity: the caller DOES get the frame list back.
        assert body["result"]["data"]["frames"][1]["frameId"] == "F1"
        # Selected by op, not by position: a neighbour's late row can sit at [0].
        p = _wait_payload(spool_dir, "frames")
        assert p["outcome"] == "ok"
        assert p["domain"] == "civitai.com"       # bare TOP-LEVEL domain only
        raw = _log_file(spool_dir).read_text()
        assert "model-benchmarking" not in raw, "a frame URL leaked into telemetry"
        assert "secret-app-path" not in raw
        assert "token=abc" not in raw
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_type_telemetry_no_typed_text(telemetry):
    """PRIVACY: `type` telemetry never carries the typed text — only op/domain."""
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {"url": "https://civitai.com/",
                                                 "typed": len(c.get("text", ""))})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd",
                     {"op": "type", "text": "SECRET_PROMPT_cafef00d"})
        assert st == 200
        # Selected by op, not by position: a neighbour's late row can sit at [0].
        # No `== "type"` assertion here — selecting on op then asserting it is
        # tautological; `_wait_ops` already raises if no `type` row lands.
        _wait_payload(spool_dir, "type")
        raw = _log_file(spool_dir).read_text()
        assert "SECRET_PROMPT" not in raw, "typed text leaked into telemetry"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_cdp_op_counts_against_rate_limit():
    """A CDP op (frames) is a normal dispatch → it spends a token-bucket slot, so a
    sustained CDP flood is throttled with 429 exactly like eval/text (#178)."""
    reg = S.Registry(rate_per_sec=1.0, burst=2.0)
    srv, _ = _serve(registry=reg)
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=lambda c: {"url": "https://x.test", "frames": []})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        codes = [_req(srv, "POST", "/cmd", {"op": "frames"})[0] for _ in range(6)]
        assert 429 in codes, f"a CDP-op flood must eventually 429; got {codes}"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_frames(tmp_path):
    r, bodies = _run_browser(["frames"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert bodies[-1]["op"] == "frames"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_click_and_frame_flag(tmp_path):
    r, bodies = _run_browser(["--frame", "model-benchmarking", "click", "#run"],
                             tmp_path)
    assert r.returncode == 0, r.stderr
    b = bodies[-1]
    assert b["op"] == "click"
    assert b["selector"] == "#run"
    assert b["frame"] == "model-benchmarking"   # global --frame threaded into body


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_type_with_selector(tmp_path):
    r, bodies = _run_browser(["type", "hello there", "--selector", "#prompt"],
                             tmp_path)
    assert r.returncode == 0, r.stderr
    b = bodies[-1]
    assert b["op"] == "type"
    assert b["text"] == "hello there"
    assert b["selector"] == "#prompt"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_key(tmp_path):
    r, bodies = _run_browser(["key", "Enter"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert bodies[-1]["op"] == "key"
    assert bodies[-1]["key"] == "Enter"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_click_requires_selector(tmp_path):
    r, _ = _run_browser(["click"], tmp_path)
    assert r.returncode != 0
    assert "selector" in r.stderr.lower()


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_fullpage_flag(tmp_path):
    # --fullpage is threaded into the command body; a path arg still works too.
    # BEHAVIOUR CHANGE: `screenshot` with no path no longer pretty-prints the
    # response — it now DECODES the data URL and writes a .png (the token fix), so
    # this capture server's dataUrl-less payload would legitimately error. The
    # `--data-url` escape hatch keeps the old pretty-print path, which is what this
    # test needs to assert the flag threading. The new default (file-writing) path
    # is covered by test_browser_cli_screenshot_fullpage_still_threaded_with_file_output.
    r, bodies = _run_browser(["screenshot", "--fullpage", "--data-url"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert bodies[-1]["op"] == "screenshot"
    assert bodies[-1]["fullpage"] is True


# =========================================================================== #
# whoami — read-only host identity + bridge diagnostics (GET /whoami)
# --------------------------------------------------------------------------- #
# whoami reports which HOST (laptop/workbench) + which browser profiles/instances
# + bridge diagnostics, bearer + Host guarded exactly like /health, NOT rate-
# limited, and metadata-only (active-tab DOMAIN, never the full URL — #173).
# =========================================================================== #

# --- host resolution (fully injectable → unit-testable) -------------------- #
def test_resolve_host_from_activity_host_env():
    r = S.resolve_host(env={"ACTIVITY_HOST": "laptop"}, ips=[])
    assert r == {"label": "laptop", "source": "activity_host_env", "ips": []}
    r2 = S.resolve_host(env={"ACTIVITY_HOST": "WorkBench"}, ips=[])
    assert r2["label"] == "workbench" and r2["source"] == "activity_host_env"


def test_resolve_host_ignores_bogus_activity_host_env():
    # A junk ACTIVITY_HOST is not a valid label → fall through to the next signal
    # (collector file pinned absent so this is hermetic on a real host).
    r = S.resolve_host(env={"ACTIVITY_HOST": "nixos"},
                       collector_env_path="/nonexistent",
                       ips=["192.168.50.155"])
    assert r["label"] == "laptop" and r["source"] == "ip"


def test_resolve_host_from_collector_file(tmp_path):
    f = tmp_path / "env"
    f.write_text('FOO=1\nACTIVITY_HOST="workbench"\nBAR=2\n', encoding="utf-8")
    r = S.resolve_host(env={}, collector_env_path=f, ips=["10.0.0.9"])
    assert r == {"label": "workbench", "source": "activity_collector_file",
                 "ips": ["10.0.0.9"]}


def test_resolve_host_env_beats_file(tmp_path):
    f = tmp_path / "env"
    f.write_text("ACTIVITY_HOST=workbench\n", encoding="utf-8")
    r = S.resolve_host(env={"ACTIVITY_HOST": "laptop"}, collector_env_path=f,
                       ips=[])
    assert r["label"] == "laptop" and r["source"] == "activity_host_env"


def test_resolve_host_missing_file_falls_through(tmp_path):
    missing = tmp_path / "nope" / "env"
    r = S.resolve_host(env={}, collector_env_path=missing,
                       ips=["192.168.50.250"])
    assert r["label"] == "workbench" and r["source"] == "ip"


def test_resolve_host_from_ip_primary_and_secondary():
    assert S.resolve_host(env={}, collector_env_path="/nonexistent",
                          ips=["192.168.50.250"])["label"] == "workbench"
    assert S.resolve_host(env={}, collector_env_path="/nonexistent",
                          ips=["192.168.50.155"])["label"] == "laptop"
    # nebula 10.42.0.x fallbacks also resolve.
    assert S.resolve_host(env={}, collector_env_path="/nonexistent",
                          ips=["10.42.0.30"])["label"] == "workbench"
    assert S.resolve_host(env={}, collector_env_path="/nonexistent",
                          ips=["10.42.0.100"])["label"] == "laptop"


def test_resolve_host_unknown_when_nothing_matches():
    r = S.resolve_host(env={}, collector_env_path="/nonexistent",
                       ips=["8.8.8.8", "203.0.113.5"])
    assert r == {"label": "unknown", "source": "unknown",
                 "ips": ["8.8.8.8", "203.0.113.5"]}


def test_host_from_ips_precedence_mirrors_ship():
    # Both hosts' primaries present → workbench wins (ship.sh detect_role order).
    assert S._host_from_ips(["192.168.50.155", "192.168.50.250"]) == "workbench"
    # A primary beats a secondary of the OTHER host.
    assert S._host_from_ips(["10.42.0.100", "192.168.50.250"]) == "workbench"
    assert S._host_from_ips([]) == ""


def test_parse_activity_host_variants():
    assert S._parse_activity_host("ACTIVITY_HOST=laptop") == "laptop"
    assert S._parse_activity_host("ACTIVITY_HOST='workbench'") == "workbench"
    assert S._parse_activity_host('X=1\nACTIVITY_HOST="laptop"\n') == "laptop"
    assert S._parse_activity_host("NOPE=1") == ""


# --- git short-HEAD + manifest_version (best-effort scalars) --------------- #
def test_git_short_head_none_on_non_repo(tmp_path):
    # A directory that is not a git repo → None (best-effort, never fatal).
    assert S.git_short_head(repo=tmp_path) is None


def test_git_short_head_none_on_missing_dir(tmp_path):
    assert S.git_short_head(repo=tmp_path / "does-not-exist") is None


# --------------------------------------------------------------------------- #
# 🔴 WHERE THE EXPECTED EXTENSION VERSION COMES FROM — read this before "fixing"
# a failure here by editing a number.
#
# The bump is the operator's only falsifiable "is the new build loaded?" signal,
# so it must be a CONSCIOUS, DECLARED act. It used to be declared as a LITERAL in
# this file, twice, buried inside two test bodies. That went stale on a version
# bump TWICE — 0.7.0 and 0.7.3 — each time leaving `main` RED on exactly these
# tests, and each time the fix was "update the number", which regenerates the bug
# at the next bump. It is not a literal any more.
#
# The declaration now lives in extension/README.md's build-discriminator
# changelog: a `> **X.Y.Z — …**` block saying what that build is and HOW TO TELL
# IT APART from the previous one. That file is the right home because it is what
# an operator actually reads to answer "which build is loaded?", and because it
# is NOT derivable from manifest.json — so asserting manifest == declaration is a
# real two-source agreement, not the manifest compared to itself.
#
# WHAT THIS BUYS, precisely:
#   * bump manifest.json and write the changelog block  -> green, NO test edit,
#     so it can never go stale again;
#   * bump manifest.json and write nothing              -> RED, naming the block
#     you have to write. That is the case both stale bumps were.
# The conscious act is preserved — it is just relocated to the artefact that has
# to exist anyway, instead of a number no one thinks to grep for.
#
# Version history: 0.7.3 the corrected `emulate --reset` note; 0.7.1 `text
# --annotated` inside `--frame`; 0.7.0 the `context` op + enriched read
# envelopes; 0.6.0 the `documentPredatesEmulation` hint; 0.5.0 the `emulate` op;
# 0.4.0 the poll-loop no-wedge change. (0.7.2 was a branch build, never on main.)
# --------------------------------------------------------------------------- #
_EXT_CHANGELOG_BLOCK = re.compile(r"^> \*\*(\d+\.\d+\.\d+) —", re.MULTILINE)


def declared_ext_versions(readme_text=None):
    """Every version DECLARED in extension/README.md's changelog, newest first.

    Raises rather than returning [] on a parse miss: a silently-empty result
    would make every caller below vacuous, which is the exact harness failure
    mode these tests exist to avoid.
    """
    if readme_text is None:
        readme_text = (EXT_DIR / "README.md").read_text(encoding="utf-8")
    found = _EXT_CHANGELOG_BLOCK.findall(readme_text)
    if not found:
        raise AssertionError(
            "HARNESS: parsed ZERO `> **X.Y.Z — …**` changelog blocks out of "
            "extension/README.md. The block format changed — fix this parser, "
            "do NOT weaken it, and do not read the empty result as 'nothing to "
            "check'."
        )
    return sorted(found, key=lambda v: tuple(int(p) for p in v.split(".")),
                  reverse=True)


def test_declared_ext_versions_parser_is_wired_to_something():
    """HARNESS SELF-CHECK — the positive + negative controls for the parser every
    assertion below depends on. Without these, a regex that matched nothing would
    make the version tests report success while testing NOTHING."""
    # POSITIVE control: it must find more than one block, including a known one
    # from a build that is definitely documented (0.7.1, `text --annotated` in
    # `--frame`). A parser wired to nothing cannot produce this.
    declared = declared_ext_versions()
    assert len(declared) >= 2, declared
    assert "0.7.1" in declared, declared
    # NEGATIVE control: fed a document with no blocks, it must FAIL LOUDLY.
    with pytest.raises(AssertionError, match="parsed ZERO"):
        declared_ext_versions("# a readme with no changelog blocks at all\n")
    # …and it must not be fooled by prose that merely mentions a version.
    assert declared_ext_versions("> **9.9.9 — x**\nsee 1.2.3 for details\n") \
        == ["9.9.9"]


def test_manifest_version_matches_the_declared_build():
    """manifest.json's version must be DECLARED in extension/README.md.

    This is the gate that replaces the twice-stale literal. It fails when a bump
    lands with no changelog block — which is a bump nobody can identify in the
    field, and is what both stale bumps looked like.
    """
    v = S.manifest_version(path=EXT_DIR / "manifest.json")
    declared = declared_ext_versions()
    assert isinstance(v, str) and v, "manifest.json must carry a version"
    assert v == declared[0], (
        f"extension/manifest.json is {v} but the newest version DECLARED in "
        f"extension/README.md is {declared[0]}. A bump needs BOTH, in one "
        f"commit: the manifest version, and a `> **{v} — …**` block saying what "
        f"discriminates this build from the last (a new op, a new `ping` field, "
        f"or a specific string it emits). Do not 'fix' this by editing a number "
        f"in the tests — there is no longer one to edit."
    )


# The single expected-version handle the rest of this file uses. Derived, on
# purpose (see the header): every site below is now automatically correct after a
# bump, and the ONE place that can disagree with the manifest is the README
# declaration, which the test above gates.
PINNED_EXT_VERSION = declared_ext_versions()[0]


# --------------------------------------------------------------------------- #
# 🔴 THE BUILD-MARKER DRIFT GATE (#324) — read this before "fixing" a failure
# here by editing a hex string.
#
# extension/build_id.js is GENERATED. Its `BUILD_MARKER` literal is a digest over
# the extension source, and it is the value `extension_stale` is computed from —
# so a marker that silently goes stale reintroduces the bug this whole change
# exists to fix: a profile running old code would report a marker matching the
# new deployed source and read `extension_stale: false`.
#
# The derivation lives in gen-build-marker.py and is IMPORTED here rather than
# reimplemented — a second copy of the rule is how a gate like this drifts into
# agreeing with itself. To fix a red:
#
#     python3 scripts/browser-bridge/gen-build-marker.py
#
# and commit the regenerated build_id.js in the SAME commit as the source change.
# --------------------------------------------------------------------------- #
def _load_gen_build_marker():
    """Import gen-build-marker.py by path (the filename has dashes, so it is not
    importable by name)."""
    import importlib.util
    path = Path(__file__).resolve().parent.parent / "gen-build-marker.py"
    spec = importlib.util.spec_from_file_location("gen_build_marker", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GEN = _load_gen_build_marker()
COMMITTED_BUILD_MARKER = GEN.read_marker(EXT_DIR / "build_id.js")


def test_build_marker_generator_is_wired_to_something(tmp_path):
    """HARNESS SELF-CHECK for the drift gate below — its positive and negative
    controls. Without these, a generator that hashed nothing (or hashed a
    constant) would make the gate report success while testing NOTHING.

    POSITIVE control: change an extension source file and the computed marker
    MUST move. That is the exact failure the gate is supposed to catch, so if
    this cannot move the number the gate can only ever pass."""
    src = tmp_path / "ext"
    src.mkdir()
    shutil.copy(EXT_DIR / "manifest.json", src / "manifest.json")
    for js in EXT_DIR.glob("*.js"):
        shutil.copy(js, src / js.name)
    before = GEN.compute_marker(src)
    assert before == GEN.compute_marker(src), "must be deterministic"

    # (1) touching a hashed source file moves the marker
    sw = src / "service_worker.js"
    sw.write_text(sw.read_text(encoding="utf-8") + "\n// positive control\n",
                  encoding="utf-8")
    after = GEN.compute_marker(src)
    assert after != before, (before, after)

    # (2) so does a manifest bump
    src2 = tmp_path / "ext2"
    shutil.copytree(src, src2)
    (src2 / "manifest.json").write_text('{"version": "0.0.0"}', encoding="utf-8")
    assert GEN.compute_marker(src2) != GEN.compute_marker(src)

    # (3) build_id.js is EXCLUDED (no self-reference): rewriting it must NOT move
    #     the marker, or regeneration could never converge.
    (src / "build_id.js").write_text('export const BUILD_MARKER = "ffff";\n',
                                     encoding="utf-8")
    assert GEN.compute_marker(src) == after
    names = [p.name for p in GEN.marker_inputs(src)]
    assert "build_id.js" not in names, names
    assert "service_worker.js" in names and "protocol.js" in names, names
    assert "manifest.json" in names, names

    # NEGATIVE control: fed a directory with nothing to hash, it must FAIL
    # LOUDLY rather than returning a digest of the empty string.
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(AssertionError, match="hashed ZERO files"):
        GEN.compute_marker(empty)


def test_committed_build_marker_matches_the_extension_source():
    """THE GATE. extension/build_id.js must be the digest of the extension
    source it ships with. Any extension change that forgets to regenerate it
    fails here, naming the command to run."""
    want = GEN.compute_marker(EXT_DIR)
    assert COMMITTED_BUILD_MARKER == want, (
        f"extension/build_id.js declares BUILD_MARKER "
        f"{COMMITTED_BUILD_MARKER!r} but the extension source hashes to "
        f"{want!r}. The marker is what `extension_stale` compares, so a stale "
        f"one lets a profile running OLD code report `false`. Regenerate it in "
        f"the SAME commit as the source change:\n\n    {GEN.REGEN_CMD}\n\n"
        f"Do NOT 'fix' this by editing the hex string by hand.")


def test_build_marker_check_mode_agrees_with_the_gate():
    """`gen-build-marker.py --check` is what a human runs; it must give the same
    verdict as the CI gate above (a check mode that could disagree would be a
    second source of truth about what is current)."""
    assert GEN.main(["--check"]) == 0


def test_service_worker_imports_the_marker_as_a_literal():
    """🔴 The marker must travel with the CODE, and that property comes entirely
    from it being a static import of a literal. Anything the worker reads at
    RUNTIME (getManifest, fetch(getURL(...)), chrome.storage) reproduces the
    exact bug #324 documents, because a stale worker reads the NEW file.

    So: build_id.js must declare a literal and reach for no runtime read, and
    service_worker.js must import it statically and report it from `ping`."""
    body = (EXT_DIR / "build_id.js").read_text(encoding="utf-8")
    assert re.search(r'export const BUILD_MARKER = "[0-9a-f]+";', body), body
    code = [ln for ln in body.splitlines() if not ln.lstrip().startswith("//")]
    code = "\n".join(code)
    assert "fetch(" not in code and "getManifest" not in code, code
    sw = (EXT_DIR / "service_worker.js").read_text(encoding="utf-8")
    assert 'import { BUILD_MARKER } from "./build_id.js";' in sw
    # ping must report it — the discriminator an older build cannot fake.
    assert "buildMarker: buildMarker()" in sw


def test_manifest_version_reads_current():
    v = S.manifest_version(path=EXT_DIR / "manifest.json")
    assert isinstance(v, str) and v == PINNED_EXT_VERSION


def test_manifest_version_prefers_the_deployed_copy_over_the_repo(tmp_path,
                                                                 monkeypatch):
    """The version the server reports as EXPECTED is the one Brave actually
    loads — the deployed ~/.local/share/browser-bridge-ext/ copy — not the repo
    working tree (which any concurrent session's checkout can change)."""
    deployed = tmp_path / "deployed.json"
    deployed.write_text('{"version": "9.9.9"}', encoding="utf-8")
    monkeypatch.setattr(S, "_DEPLOYED_EXT_MANIFEST", deployed)
    monkeypatch.setattr(S, "_EXT_MANIFEST_PATH", EXT_DIR / "manifest.json")
    assert S.manifest_version() == "9.9.9"


def test_manifest_version_falls_back_to_repo_when_not_deployed(tmp_path,
                                                               monkeypatch):
    """Backwards-safe: a host that has not switched yet (no deployed copy) keeps
    reporting the repo manifest instead of going null."""
    monkeypatch.setattr(S, "_DEPLOYED_EXT_MANIFEST", tmp_path / "absent.json")
    monkeypatch.setattr(S, "_EXT_MANIFEST_PATH", EXT_DIR / "manifest.json")
    assert S.manifest_version() == PINNED_EXT_VERSION


def test_manifest_version_none_when_neither_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_DEPLOYED_EXT_MANIFEST", tmp_path / "a.json")
    monkeypatch.setattr(S, "_EXT_MANIFEST_PATH", tmp_path / "b.json")
    assert S.manifest_version() is None


# --------------------------------------------------------------------------- #
# 🔴 THE BUILD MARKER (#324) — where `extension_stale` gets its answer from.
#
# It is NOT the version. `extension_version` is chrome.runtime.getManifest()
# .version (read from the on-disk manifest at call time) and `extension_id` is
# derived from the load PATH, so both describe the DIRECTORY, not the running
# code. MEASURED 2026-08-04: two Brave profiles loading the SAME directory
# reported an identical id, an identical 0.7.3 and `extension_stale: false`
# while one ran `main` and the other an unmerged 0.7.2 build whose source was on
# no disk. A version-shaped signal cannot separate those rows.
#
# The marker is a generated LITERAL in extension/build_id.js that
# service_worker.js imports, so it is frozen into the loaded module graph and
# travels with the code. The server reads the expected value out of the DEPLOYED
# build_id.js (build_marker()) and compares.
#
# The verdict FAILS CLOSED: either side missing a marker -> None. `False` is
# only ever reachable with two present, equal markers.
# --------------------------------------------------------------------------- #
def test_build_marker_reads_the_literal(tmp_path):
    f = tmp_path / "build_id.js"
    f.write_text('export const BUILD_MARKER = "abc123def456abcd";\n',
                 encoding="utf-8")
    assert S.build_marker(path=f) == "abc123def456abcd"


def test_build_marker_none_on_missing_unreadable_or_absent_literal(tmp_path):
    assert S.build_marker(path=tmp_path / "nope.js") is None
    bad = tmp_path / "build_id.js"
    bad.write_text("// a file with no marker in it at all\n", encoding="utf-8")
    assert S.build_marker(path=bad) is None


def test_build_marker_prefers_the_deployed_copy_over_the_repo(tmp_path,
                                                              monkeypatch):
    """The marker the server calls EXPECTED is the one in the tree Brave loads —
    the deployed copy — not the repo working tree, which any concurrent
    session's checkout can change under a live verification."""
    deployed = tmp_path / "deployed_build_id.js"
    deployed.write_text('export const BUILD_MARKER = "0000deb10ded0000";\n',
                        encoding="utf-8")
    monkeypatch.setattr(S, "_DEPLOYED_EXT_BUILD_ID", deployed)
    monkeypatch.setattr(S, "_EXT_BUILD_ID_PATH", EXT_DIR / "build_id.js")
    assert S.build_marker() == "0000deb10ded0000"


def test_build_marker_falls_back_to_the_repo_when_not_deployed(tmp_path,
                                                               monkeypatch):
    monkeypatch.setattr(S, "_DEPLOYED_EXT_BUILD_ID", tmp_path / "absent.js")
    monkeypatch.setattr(S, "_EXT_BUILD_ID_PATH", EXT_DIR / "build_id.js")
    assert S.build_marker() == COMMITTED_BUILD_MARKER


def test_build_marker_none_when_neither_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_DEPLOYED_EXT_BUILD_ID", tmp_path / "a.js")
    monkeypatch.setattr(S, "_EXT_BUILD_ID_PATH", tmp_path / "b.js")
    assert S.build_marker() is None


# --- annotate_staleness: the explicit loaded-vs-expected verdict ------------ #
def test_annotate_staleness_flags_a_marker_mismatch_true():
    """A profile running code whose marker differs from the deployed source's is
    STALE — regardless of what version it reports. This is the #324 case: the
    version agreed and the code did not."""
    insts = [{"key": "personal", "extension_version": "0.7.3",
              "extension_build": "0000000000000002"}]
    S.annotate_staleness(insts, expected="0.7.3",
                         expected_build="0000000000000001")
    assert insts[0]["extension_stale"] is True
    assert insts[0]["extension_build_expected"] == "0000000000000001"
    assert insts[0]["extension_version_expected"] == "0.7.3"


def test_annotate_staleness_flags_a_marker_match_false():
    insts = [{"key": "work", "extension_version": "0.3.0",
              "extension_build": "0000000000000001"}]
    S.annotate_staleness(insts, expected="0.3.0",
                         expected_build="0000000000000001")
    assert insts[0]["extension_stale"] is False


def test_annotate_staleness_fails_closed_when_a_marker_is_missing():
    """🔴 FAIL CLOSED. A missing marker on EITHER side is undecidable — null,
    never False. `false` must mean "verified current", so a build predating the
    marker, or an unreadable/undeployed source tree, cannot produce one. Note
    the version MATCHES in every case below — deliberately, and that is the
    whole scope of this test: under the old version-based rule each of these
    returned False, which is exactly the affirmative all-clear #324 was filed
    about. A marker-missing case whose versions DISAGREE is decidable (True)
    and lives in test_annotate_staleness_version_mismatch_decides_true_*."""
    # (a) the instance reports no marker (a build predating #324)
    insts = [{"key": "a", "extension_version": "0.3.0",
              "extension_build": None},
             {"key": "b", "extension_version": "0.3.0",
              "extension_build": ""},
             {"key": "c", "extension_version": "0.3.0"}]     # key absent entirely
    S.annotate_staleness(insts, expected="0.3.0",
                         expected_build="0000000000000001")
    assert [i["extension_stale"] for i in insts] == [None, None, None]

    # (b) the SERVER cannot read a marker (no deployed/repo build_id.js)
    for missing in (None, ""):
        insts2 = [{"key": "d", "extension_version": "0.3.0",
                   "extension_build": "0000000000000001"}]
        S.annotate_staleness(insts2, expected="0.3.0", expected_build=missing)
        assert insts2[0]["extension_stale"] is None, missing

    # (c) the default (no expected_build passed at all) is also undecidable —
    #     a caller that forgets to pass it cannot accidentally get a False.
    insts3 = [{"key": "e", "extension_version": "0.3.0",
               "extension_build": "0000000000000001"}]
    S.annotate_staleness(insts3, expected="0.3.0")
    assert insts3[0]["extension_stale"] is None


def test_annotate_staleness_a_version_match_alone_never_yields_false():
    """The regression this PR exists to prevent: two profiles reporting the same
    version as the deployed manifest must NOT read `false` on the strength of
    that alone. Without markers the honest answer is null."""
    insts = [{"key": "personal", "extension_version": "0.7.3"},
             {"key": "work", "extension_version": "0.7.3"}]
    S.annotate_staleness(insts, expected="0.7.3", expected_build=None)
    assert [i["extension_stale"] for i in insts] == [None, None]
    assert not any(i["extension_stale"] is False for i in insts)


def test_annotate_staleness_marker_match_with_version_mismatch_is_stale():
    """Markers equal but versions not: the deployed manifest moved without the
    marker moving (or a header was spoofed). Not a verified-current state."""
    insts = [{"key": "a", "extension_version": "0.7.1",
              "extension_build": "0000000000000001"}]
    S.annotate_staleness(insts, expected="0.7.3",
                         expected_build="0000000000000001")
    assert insts[0]["extension_stale"] is True


@pytest.mark.parametrize("loaded_build,expected_build", [
    (None, "73f5438f18f395d2"),   # instance reports no marker (the LIVE case)
    ("a1b2c3d4e5f60718", None),   # server cannot read a marker
    (None, None),                 # neither side has one
    ("", "73f5438f18f395d2"),     # empty string is "missing" too
    ("a1b2c3d4e5f60718", ""),
])
def test_annotate_staleness_version_mismatch_decides_true_without_markers(
        loaded_build, expected_build):
    """🔴 THE ASYMMETRY. A version MATCH proves nothing (two profiles loading one
    directory report one version while running different code — #324), but a
    version MISMATCH is positive proof the loaded code is not the deployed code.
    That direction was never in doubt, so a missing marker must not discard it.

    The first parameter set is the LIVE observation from the workbench on
    2026-08-04 that motivated this: profile "personal - other" reported
    extension_version 0.7.1 / extension_build null against an expected 0.8.1 /
    73f5438f18f395d2, and the verdict came back null."""
    insts = [{"key": "personal - other", "extension_version": "0.7.1",
              "extension_build": loaded_build}]
    S.annotate_staleness(insts, expected="0.8.1",
                         expected_build=expected_build)
    assert insts[0]["extension_stale"] is True
    assert insts[0]["extension_version_expected"] == "0.8.1"
    assert insts[0]["extension_build_expected"] == expected_build


@pytest.mark.parametrize("loaded_build,expected_build", [
    (None, "73f5438f18f395d2"),
    ("a1b2c3d4e5f60718", None),
    (None, None),
])
def test_annotate_staleness_missing_marker_version_agreement_stays_none(
        loaded_build, expected_build):
    """A marker is missing and the versions AGREE: still undecidable. Agreement
    is exactly the signal #324 proved worthless, so it may not upgrade the
    verdict in either direction."""
    insts = [{"key": "work", "extension_version": "0.9.2",
              "extension_build": loaded_build}]
    S.annotate_staleness(insts, expected="0.9.2",
                         expected_build=expected_build)
    assert insts[0]["extension_stale"] is None


@pytest.mark.parametrize("loaded_version,expected_version", [
    (None, "0.8.1"),    # instance reported no version
    ("0.7.1", None),    # server could not read a manifest version
    (None, None),
    ("", "0.8.1"),      # empty string is "unknown", not a mismatch
    ("0.7.1", ""),
])
def test_annotate_staleness_missing_marker_and_unknown_version_stays_none(
        loaded_version, expected_version):
    """With a marker missing, a verdict may only come from two KNOWN versions.
    An absent version is not evidence of disagreement."""
    insts = [{"key": "other", "extension_version": loaded_version,
              "extension_build": None}]
    S.annotate_staleness(insts, expected=expected_version,
                         expected_build="73f5438f18f395d2")
    assert insts[0]["extension_stale"] is None


def test_annotate_staleness_false_stays_strictly_marker_backed():
    """🔴 THE INVARIANT. `False` is the affirmative all-clear; only two present,
    identical markers may produce one. Sweep every marker-missing shape — with
    versions agreeing, disagreeing, and unknown — and assert not one yields
    False. Only True becomes newly reachable from versions alone."""
    versions = [("0.7.1", "0.8.1"), ("0.9.2", "0.9.2"), (None, "0.8.1"),
                ("0.7.1", None), (None, None), ("", "0.8.1"), ("0.7.1", "")]
    markers = [(None, "73f5438f18f395d2"), ("a1b2c3d4e5f60718", None),
               (None, None), ("", "73f5438f18f395d2"),
               ("a1b2c3d4e5f60718", "")]
    for loaded_v, expected_v in versions:
        for loaded_b, expected_b in markers:
            insts = [{"key": "k", "extension_version": loaded_v,
                      "extension_build": loaded_b}]
            S.annotate_staleness(insts, expected=expected_v,
                                 expected_build=expected_b)
            assert insts[0]["extension_stale"] is not False, (
                loaded_v, expected_v, loaded_b, expected_b)


def test_manifest_version_none_on_missing(tmp_path):
    assert S.manifest_version(path=tmp_path / "nope.json") is None


def test_manifest_version_none_on_malformed(tmp_path):
    bad = tmp_path / "manifest.json"
    bad.write_text("{ not json", encoding="utf-8")
    assert S.manifest_version(path=bad) is None


# --- GET /whoami: shape, guard, zero-instances ----------------------------- #
def test_whoami_shape_zero_instances():
    srv, _ = _serve()
    try:
        status, body = _req(srv, "GET", "/whoami")
        assert status == 200
        assert body["ok"] is True
        # host block
        host = body["host"]
        assert host["label"] in ("laptop", "workbench", "unknown")
        assert host["source"] in ("activity_host_env",
                                  "activity_collector_file", "ip", "unknown")
        assert isinstance(host["ips"], list)
        # bridge block
        br = body["bridge"]
        assert br["endpoint"].startswith("http://127.0.0.1:")
        assert isinstance(br["port"], int)
        assert br["server_version"]["version"] == S.SERVER_VERSION
        assert "git" in br["server_version"]  # str or None, present either way
        assert br["connected"] == 0
        assert br["rate_limit"] == {"per_sec": S.RATE_PER_SEC,
                                    "burst": S.BURST, "max_queue": S.MAX_QUEUE}
        assert "extension_version_current" in br
        # instances empty when nothing connected — host+bridge still reported.
        assert body["instances"] == []
    finally:
        srv.shutdown(); srv.server_close()


def test_whoami_missing_token_401():
    srv, _ = _serve()
    try:
        status, _ = _req(srv, "GET", "/whoami", token=None)
        assert status == 401
    finally:
        srv.shutdown(); srv.server_close()


def test_whoami_wrong_token_401():
    srv, _ = _serve()
    try:
        status, _ = _req(srv, "GET", "/whoami", token="nope")
        assert status == 401
    finally:
        srv.shutdown(); srv.server_close()


def test_whoami_bad_host_403():
    srv, _ = _serve()
    try:
        status, _ = _req(srv, "GET", "/whoami", host="evil.example.com")
        assert status == 403
    finally:
        srv.shutdown(); srv.server_close()


def test_whoami_rate_limit_reflects_registry():
    reg = S.Registry(rate_per_sec=7, burst=13, max_queue=99)
    srv, _ = _serve(registry=reg)
    try:
        status, body = _req(srv, "GET", "/whoami")
        assert status == 200
        assert body["bridge"]["rate_limit"] == {"per_sec": 7.0, "burst": 13.0,
                                                 "max_queue": 99}
    finally:
        srv.shutdown(); srv.server_close()


def test_whoami_git_head_null_still_200(monkeypatch):
    monkeypatch.setattr(S, "git_short_head", lambda *a, **k: None)
    srv, _ = _serve()
    try:
        status, body = _req(srv, "GET", "/whoami")
        assert status == 200
        assert body["bridge"]["server_version"]["git"] is None
    finally:
        srv.shutdown(); srv.server_close()


# --- GET /whoami: instances (metadata-only, ext_version enrichment) -------- #
def test_whoami_reports_connected_instance_metadata_only():
    srv, _ = _serve()
    fake = FakeExtension(srv, instance_id="uuid-a", label="work",
                         active_url="https://example.com/some/path?q=1",
                         active_title="Example",
                         ext_version="0.1.0")
    fake.start()
    try:
        assert _wait_instances(srv, 1) is not None
        status, body = _req(srv, "GET", "/whoami")
        assert status == 200
        assert body["bridge"]["connected"] == 1
        insts = body["instances"]
        assert len(insts) == 1
        i = insts[0]
        assert i["key"] == "work"        # label is the routing key
        assert i["label"] == "work"
        assert i["instanceId"] == "uuid-a"
        # metadata-only: the DOMAIN, never the full URL/path/query (#173).
        assert i["activeTabDomain"] == "example.com"
        assert "some/path" not in json.dumps(i)
        assert i["extension_version"] == "0.1.0"
    finally:
        fake.stop(); srv.shutdown(); srv.server_close()


def test_whoami_extension_version_null_when_unreported():
    srv, _ = _serve()
    fake = FakeExtension(srv, instance_id="uuid-b", label="lab",
                         active_url="https://noext.test/")
    fake.start()
    try:
        assert _wait_instances(srv, 1) is not None
        status, body = _req(srv, "GET", "/whoami")
        assert status == 200
        i = body["instances"][0]
        assert i["extension_version"] is None
        assert i["activeTabDomain"] == "noext.test"
    finally:
        fake.stop(); srv.shutdown(); srv.server_close()


# --- CLI: `browser whoami` GETs /whoami and pretty-prints ------------------ #
class _WhoamiServer:
    """A minimal GET /whoami server returning a canned identity object."""

    def handler(self):
        class _H(S.BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: A003
                pass

            def do_GET(self):
                payload = json.dumps({
                    "ok": True,
                    "host": {"label": "workbench", "source": "ip",
                             "ips": ["192.168.50.250"]},
                    "bridge": {"endpoint": "http://127.0.0.1:8788", "port": 8788,
                               "server_version": {"version": "x", "git": None},
                               "connected": 0,
                               "rate_limit": {"per_sec": 5.0, "burst": 20.0,
                                              "max_queue": 32},
                               "extension_version_current": "0.1.0"},
                    "instances": []}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        return _H


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_whoami(tmp_path):
    srv = S.ThreadingHTTPServer(("127.0.0.1", 0), _WhoamiServer().handler())
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tokf = tmp_path / "token"
    tokf.write_text("smoke-token\n")
    env = dict(os.environ)
    env.update(BROWSER_BRIDGE_HOST="127.0.0.1", BROWSER_BRIDGE_PORT=str(port),
               BROWSER_BRIDGE_TOKEN_FILE=str(tokf))
    try:
        r = subprocess.run([str(BROWSER_BIN), "whoami"], env=env,
                           capture_output=True, text=True, timeout=CLI_TIMEOUT_S)
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout)
        assert out["host"]["label"] == "workbench"
        assert out["bridge"]["extension_version_current"] == "0.1.0"
    finally:
        srv.shutdown(); srv.server_close()


# =========================================================================== #
# Gap 1 — `upload` op: audit-logged (op + target domain + the file PATH)
# =========================================================================== #
def test_upload_dispatches_and_audit_event_has_op_domain_path(telemetry):
    """An upload dispatches to the extension (tab-scoped like the other CDP ops)
    AND emits an AUDIT telemetry event carrying op + target domain + the file
    PATH (local metadata — never file content). The path is required so this
    exfil-capable action is traceable."""
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "ok": True, "selector": c.get("selector"),
        "url": "https://civitai.com/apps/run/model-benchmarking",
        "files": ["render.png"]})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {
            "op": "upload", "selector": "#file",
            "path": "/home/zach/pics/render.png"})
        assert st == 200
        # The path reached the extension (dispatched op), the basename came back.
        assert ext.dispatched[-1]["path"] == "/home/zach/pics/render.png"
        assert body["result"]["data"]["files"] == ["render.png"]
        e = _wait_events(spool_dir, 1)[0]
        assert e["exit_code"] == "0"
        p = json.loads(e["payload"])
        assert p["op"] == "upload"
        assert p["outcome"] == "ok"
        assert p["domain"] == "civitai.com"       # target domain (from the result url)
        assert p["path"] == "/home/zach/pics/render.png"  # AUDIT: the file path
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_upload_is_tab_scoped_and_required_fields_enforced():
    """`upload` is a tab-scoped op requiring selector + path (server-side 400)."""
    assert "upload" in S.TAB_SCOPED_OPS
    assert "upload" in S.ALLOWED_OPS
    srv, _ = _serve()
    try:
        st, body = _req(srv, "POST", "/cmd", {"op": "upload", "path": "/x"})
        assert st == 400 and body["error"] == "missing_field:selector"
        st, body = _req(srv, "POST", "/cmd", {"op": "upload", "selector": "#f"})
        assert st == 400 and body["error"] == "missing_field:path"
    finally:
        srv.shutdown(); srv.server_close()


# --- CLI: path validation happens BEFORE any dispatch ---------------------- #
@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_upload_rejects_missing_and_nonfile_path(tmp_path):
    """`browser upload` validates the path is a readable regular file and resolves
    it to an ABSOLUTE path BEFORE any /cmd dispatch — a missing/non-file path is a
    clear error and NEVER reaches the bridge."""
    srv_state = _CaptureCmdServer()
    srv = S.ThreadingHTTPServer(("127.0.0.1", 0), srv_state.handler())
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tokf = tmp_path / "token"; tokf.write_text("smoke-token\n")
    env = dict(os.environ)
    env.update(BROWSER_BRIDGE_HOST="127.0.0.1", BROWSER_BRIDGE_PORT=str(port),
               BROWSER_BRIDGE_TOKEN_FILE=str(tokf))

    def run(*args):
        return subprocess.run([str(BROWSER_BIN), *args], env=env,
                              capture_output=True, text=True, timeout=CLI_TIMEOUT_S)
    try:
        # Nonexistent path → refused before dispatch.
        r = run("upload", "#f", str(tmp_path / "nope.png"))
        assert r.returncode != 0 and "no such file" in r.stderr.lower()
        # A directory (not a regular file) → refused.
        r = run("upload", "#f", str(tmp_path))
        assert r.returncode != 0 and "not a regular file" in r.stderr.lower()
        assert srv_state.bodies == [], "no upload reached the bridge for a bad path"
        # A real file → dispatched with an ABSOLUTE path + selector.
        f = tmp_path / "pic.png"; f.write_bytes(b"\x89PNG")
        r = run("upload", "#file", str(f))
        assert r.returncode == 0, r.stderr
        b = srv_state.bodies[-1]
        assert b["op"] == "upload"
        assert b["selector"] == "#file"
        assert b["path"] == os.path.realpath(str(f))  # resolved to an ABSOLUTE path
    finally:
        srv.shutdown(); srv.server_close()


# =========================================================================== #
# Gap 2 — hidden-tab self-announce: the CLI prints the note to STDERR (exit 0)
# =========================================================================== #
class _CannedCmdServer:
    """A /cmd server that answers with a caller-supplied result envelope `data`."""

    def __init__(self, data):
        self.data = data

    def handler(self):
        outer = self

        class _H(S.BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: A003
                pass

            def do_POST(self):
                ln = int(self.headers.get("Content-Length") or 0)
                if ln:
                    self.rfile.read(ln)
                payload = json.dumps({"ok": True, "result": {
                    "id": "x", "ok": True, "data": outer.data}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        return _H


def _run_browser_against(data, args, tmp_path):
    srv = S.ThreadingHTTPServer(("127.0.0.1", 0), _CannedCmdServer(data).handler())
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tokf = tmp_path / "token"; tokf.write_text("smoke-token\n")
    env = dict(os.environ)
    env.update(BROWSER_BRIDGE_HOST="127.0.0.1", BROWSER_BRIDGE_PORT=str(port),
               BROWSER_BRIDGE_TOKEN_FILE=str(tokf))
    try:
        return subprocess.run([str(BROWSER_BIN), *args], env=env,
                              capture_output=True, text=True, timeout=CLI_TIMEOUT_S)
    finally:
        srv.shutdown(); srv.server_close()


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_hidden_tab_note_to_stderr_exit0(tmp_path):
    """A read whose result flags the tab hidden prints the note to STDERR but
    exits 0 (a warning, not an error) and leaves the JSON on stdout intact."""
    note = "tab is hidden — background tabs are throttled; run 'browser activate'"
    data = {"url": "https://x.test", "title": "X", "html": "<html></html>",
            "visibilityState": "hidden", "hidden": True, "note": note}
    r = _run_browser_against(data, ["html"], tmp_path)
    assert r.returncode == 0, "a hidden-tab warning must NOT fail the command"
    assert note in r.stderr, f"the note must be on stderr, got: {r.stderr!r}"
    # The JSON result is still on stdout, unbroken.
    out = json.loads(r.stdout)
    assert out["result"]["data"]["hidden"] is True


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_visible_tab_no_stderr_warning(tmp_path):
    """A read on a VISIBLE tab prints NO hidden-tab warning to stderr."""
    data = {"url": "https://x.test", "title": "X", "html": "<html></html>",
            "visibilityState": "visible"}
    r = _run_browser_against(data, ["html"], tmp_path)
    assert r.returncode == 0
    assert "hidden" not in r.stderr.lower()
    assert "throttled" not in r.stderr.lower()


# =========================================================================== #
# Gap 3 — stale extension: version in /health + unknown_op → reload/restart map
# =========================================================================== #
def test_health_includes_extension_version_and_current():
    """/health surfaces each instance's reported extension_version AND the bridge-
    level extension_version_current (the manifest version the server reads)."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="a", label="alpha", ext_version="0.1.0")
    ext.start()
    try:
        body = _wait_instances(srv, 1)
        assert body is not None
        # /health surfaces exactly what the server reads from the repo manifest.
        assert body["extension_version_current"] == S.manifest_version()
        inst = body["instances"][0]
        assert inst["extension_version"] == "0.1.0"   # what THIS instance reported
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_health_extension_version_null_when_unreported():
    """An instance whose extension predates version reporting → null (no crash)."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="b", label="beta")  # no ext_version
    ext.start()
    try:
        body = _wait_instances(srv, 1)
        assert body is not None
        assert body["instances"][0]["extension_version"] is None
        assert "extension_version_current" in body
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_health_no_extension_still_reports_current_version():
    srv, _ = _serve()
    try:
        st, body = _req(srv, "GET", "/health")
        assert st == 200
        assert "extension_version_current" in body
        assert body["instances"] == []
    finally:
        srv.shutdown(); srv.server_close()


def test_health_carries_an_explicit_stale_verdict_per_instance(
        pinned_manifest, pinned_build):
    """The point of the change: a yes/no, not two strings to eyeball. An
    instance whose reported BUILD MARKER differs from the deployed source's is
    STALE=True — note it reports the EXPECTED VERSION here, which is the #324
    case exactly: the version agreed and the code did not."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="a", label="alpha",
                        ext_version=PINNED_VERSION,
                        ext_build="0000000000000bad")
    ext.start()
    try:
        body = _wait_instances(srv, 1)
        assert body is not None
        inst = body["instances"][0]
        assert inst["extension_version_expected"] == PINNED_VERSION
        assert inst["extension_build_expected"] == PINNED_BUILD
        assert inst["extension_build"] == "0000000000000bad"
        assert inst["extension_stale"] is True
        assert body["extension_build_current"] == PINNED_BUILD
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_health_stale_verdict_is_false_when_the_loaded_build_matches(
        pinned_manifest, pinned_build):
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="a", label="alpha",
                        ext_version=PINNED_VERSION, ext_build=PINNED_BUILD)
    ext.start()
    try:
        body = _wait_instances(srv, 1)
        assert body is not None
        assert body["instances"][0]["extension_stale"] is False
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_health_stale_verdict_is_null_when_only_the_version_matches(
        pinned_manifest, pinned_build):
    """🔴 FAIL CLOSED end-to-end: an instance that reports the EXPECTED VERSION
    but NO marker reads null, not false. Under the old rule this exact instance
    read `false` — the affirmative all-clear #324 was filed about."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="a", label="alpha",
                        ext_version=PINNED_VERSION)   # no ext_build
    ext.start()
    try:
        body = _wait_instances(srv, 1)
        assert body is not None
        inst = body["instances"][0]
        assert inst["extension_version"] == PINNED_VERSION
        assert inst["extension_build"] is None
        assert inst["extension_stale"] is None
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_whoami_carries_the_stale_verdict_per_instance(pinned_manifest,
                                                       pinned_build):
    srv, _ = _serve()
    # Reports the EXPECTED version on purpose, so the True can ONLY have come
    # from the marker comparison. (With a mismatching version this test passes
    # even against a build that ignores the marker entirely — measured while
    # mutation-testing this PR.)
    ext = FakeExtension(srv, instance_id="a", label="alpha",
                        ext_version=PINNED_VERSION, ext_build="0000000000000bad")
    ext.start()
    try:
        _wait_instances(srv, 1)
        st, body = _req(srv, "GET", "/whoami")
        assert st == 200
        inst = body["instances"][0]
        assert inst["extension_stale"] is True
        assert inst["extension_version_expected"] == PINNED_VERSION
        assert inst["extension_build_expected"] == PINNED_BUILD
        assert body["bridge"]["extension_version_current"] == PINNED_VERSION
        assert body["bridge"]["extension_build_current"] == PINNED_BUILD
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_whoami_stale_verdict_is_null_for_an_unreporting_extension(
        pinned_build):
    """A build that predates the marker is UNDECIDABLE, never "fine"."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="a", label="alpha")  # no ext_build
    ext.start()
    try:
        _wait_instances(srv, 1)
        st, body = _req(srv, "GET", "/whoami")
        assert st == 200
        assert body["instances"][0]["extension_stale"] is None
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_stale_verdict_is_null_when_the_server_cannot_read_a_marker(
        pinned_manifest, tmp_path, monkeypatch):
    """The other half of fail-closed: an unreadable/undeployed extension source
    tree makes the verdict undecidable even for an instance that DOES report a
    marker and the expected version."""
    monkeypatch.setattr(S, "_DEPLOYED_EXT_BUILD_ID", tmp_path / "absent-a.js")
    monkeypatch.setattr(S, "_EXT_BUILD_ID_PATH", tmp_path / "absent-b.js")
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="a", label="alpha",
                        ext_version=PINNED_VERSION, ext_build="abcabcabcabcabca")
    ext.start()
    try:
        body = _wait_instances(srv, 1)
        assert body is not None
        assert body["extension_build_current"] is None
        assert body["instances"][0]["extension_stale"] is None
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_two_profiles_one_directory_are_distinguishable(pinned_manifest,
                                                        pinned_build):
    """🔴 THE MEASURED #324 CASE, reproduced. Two instances loaded from the SAME
    directory: identical extension_id (it is path-derived, so it MUST be) and
    identical reported version — but different running code. The old verdict
    said `false` for both. The marker separates them."""
    srv, _ = _serve()
    same_id = "bgbkamdlkdleahpgdgmjipjbgmepgenk"
    personal = FakeExtension(srv, instance_id="p", label="personal",
                             ext_version=PINNED_VERSION, ext_id=same_id,
                             ext_build="0000000000000bad")   # unmerged build
    work = FakeExtension(srv, instance_id="w", label="work",
                         ext_version=PINNED_VERSION, ext_id=same_id,
                         ext_build=PINNED_BUILD)             # main
    personal.start(); work.start()
    try:
        body = _wait_instances(srv, 2)
        assert body is not None
        by_key = {i["key"]: i for i in body["instances"]}
        assert by_key["personal"]["extension_id"] == \
               by_key["work"]["extension_id"] == same_id
        assert by_key["personal"]["extension_version"] == \
               by_key["work"]["extension_version"] == PINNED_VERSION
        assert by_key["personal"]["extension_stale"] is True
        assert by_key["work"]["extension_stale"] is False
    finally:
        personal.stop(); work.stop(); srv.shutdown(); srv.server_close()


# --- extension_id: WHICH DIRECTORY Brave loaded ---------------------------- #
# The version fields cannot answer this — a repo-path 0.3.1 and a deployed-path
# 0.3.1 are identical on every version field. chrome.runtime.id is path-derived,
# so it is the only signal that can confirm the migration off the git-mutable
# path took. (The path→id derivation is now MEASURED — sha256(abs path), first 32
# hex chars, nibbles mapped a-p; see extension/README.md. The server still only
# REPORTS the id and never computes an expected one: that is a deliberate
# separate decision, because an unknown path — a hand-loaded third profile, or a
# rollback to the repo path — must degrade to null, never to a false "wrong
# directory" alarm.)
def test_health_and_whoami_surface_the_reported_extension_id():
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="a", label="alpha",
                        ext_version="0.3.1", ext_id="abcdefghijklmnop")
    ext.start()
    try:
        body = _wait_instances(srv, 1)
        assert body is not None
        assert body["instances"][0]["extension_id"] == "abcdefghijklmnop"
        st, who = _req(srv, "GET", "/whoami")
        assert st == 200
        assert who["instances"][0]["extension_id"] == "abcdefghijklmnop"
        # The deploy dir Brave SHOULD be pointed at, for the operator to read
        # next to the id. NOT an expected id — the server never guesses one.
        assert who["bridge"]["extension_dir_expected"].endswith(
            "browser-bridge-ext")
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_extension_id_is_null_for_a_build_that_does_not_report_it():
    """Backwards-safe: the currently-loaded 0.2.0 build sends no id header."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="a", label="alpha", ext_version="0.2.0")
    ext.start()
    try:
        body = _wait_instances(srv, 1)
        assert body is not None
        assert body["instances"][0]["extension_id"] is None
        st, who = _req(srv, "GET", "/whoami")
        assert who["instances"][0]["extension_id"] is None
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_identity_headers_are_bounded_server_side():
    """A hostile/buggy extension must not be able to push an unbounded string
    into every /health and /whoami response. protocol.js's cap is client-side
    only, so the SERVER truncates the version + id it accepts."""
    srv, _ = _serve()
    huge = "x" * 5000
    ext = FakeExtension(srv, instance_id="a", label="alpha",
                        ext_version=huge, ext_id=huge)
    ext.start()
    try:
        body = _wait_instances(srv, 1)
        assert body is not None
        inst = body["instances"][0]
        assert len(inst["extension_version"]) == S.MAX_IDENTITY_CHARS
        assert len(inst["extension_id"]) == S.MAX_IDENTITY_CHARS
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_every_poll_supplied_string_is_bounded_server_side():
    """The same rationale applies to EVERY /poll string, not just the two new
    ones: label and the active-tab url/title are echoed into /health, /instances
    and /whoami too, and protocol.js's cap binds only an honest extension."""
    srv, _ = _serve()
    huge = "y" * 6000
    ext = FakeExtension(srv, instance_id=huge, label=huge,
                        active_url="https://e.test/" + huge,
                        active_title=huge)
    ext.start()
    try:
        body = _wait_instances(srv, 1)
        assert body is not None
        inst = body["instances"][0]
        assert len(inst["label"]) == S.MAX_IDENTITY_CHARS
        assert len(inst["instanceId"]) == S.MAX_IDENTITY_CHARS
        assert len(inst["activeTab"]["url"]) == S.MAX_ACTIVE_TAB_CHARS
        assert len(inst["activeTab"]["title"]) == S.MAX_ACTIVE_TAB_CHARS
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_a_later_poll_without_the_id_header_does_not_wipe_a_known_id():
    """Same rule the version field already follows: only overwrite a KNOWN
    value, so a legacy/partial poll cannot erase what an earlier poll reported."""
    reg = S.Registry()
    reg.poll("a", "alpha", 0.0, extension_version="0.3.1",
             extension_id="abcdefghijklmnop")
    reg.poll("a", "alpha", 0.0)          # no headers at all
    assert reg.snapshot()[0]["extension_id"] == "abcdefghijklmnop"
    assert reg.snapshot()[0]["extension_version"] == "0.3.1"


# --- `ping`: the extension build-freshness op ------------------------------ #
def test_ping_is_an_allowed_op_and_is_not_tab_scoped():
    """`ping` must reach the extension (it is the freshness tell) but must NOT
    contend for a tab — it touches no page, so it can be probed with no owned
    tab and cannot be serialized behind another session's in-flight op."""
    assert "ping" in S.ALLOWED_OPS
    assert "ping" not in S.TAB_SCOPED_OPS
    assert "ping" not in S.SERVER_OPS      # it is DISPATCHED, not answered locally
    assert S.validate_command({"op": "ping"}) == ("ping", None)


def test_ping_op_set_mirrors_the_extension_protocol_js():
    """server.py ALLOWED_OPS and extension/protocol.js ALLOWED_OPS are one
    contract — a drift here is how an op silently becomes undispatchable."""
    js = (EXT_DIR / "protocol.js").read_text(encoding="utf-8")
    block = re.search(r"export const ALLOWED_OPS = \[(.*?)\];", js, re.S)
    assert block is not None
    js_ops = set(re.findall(r'"([a-zA-Z]+)"', block.group(1)))
    assert js_ops == set(S.ALLOWED_OPS)


def test_ping_round_trips_to_the_extension_with_no_owned_tab():
    """End-to-end over the real HTTP path: a session that owns no tab can still
    probe which build is loaded (the FakeExtension echoes the op)."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="a", label="alpha", ext_version="0.3.0")
    ext.start()
    try:
        _wait_instances(srv, 1)
        st, body = _req(srv, "POST", "/cmd", {"op": "ping"},
                        headers={"X-Session-Id": "sess-ping"})
        assert st == 200, body
        assert body["ok"] is True
        assert body["result"]["ok"] is True
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_unknown_op_maps_to_stale_extension_message(tmp_path):
    """A server-side op-level `unknown_op` for a DISPATCHED op (the extension is
    older than this CLI) maps to a clear reload/restart-Brave message + non-zero
    exit (the ↻-is-unreliable insight: the long-poll keeps the old SW alive)."""
    data_env = {"ok": False, "error": "unknown_op"}   # extension returned unknown_op

    class _H(S.BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: A003
            pass

        def do_POST(self):
            ln = int(self.headers.get("Content-Length") or 0)
            if ln:
                self.rfile.read(ln)
            payload = json.dumps({"ok": True, "result": {
                "id": "x", **data_env}}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    srv = S.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tokf = tmp_path / "token"; tokf.write_text("smoke-token\n")
    env = dict(os.environ)
    env.update(BROWSER_BRIDGE_HOST="127.0.0.1", BROWSER_BRIDGE_PORT=str(port),
               BROWSER_BRIDGE_TOKEN_FILE=str(tokf))
    try:
        r = subprocess.run([str(BROWSER_BIN), "upload", "#f", str(tokf)],
                           env=env, capture_output=True, text=True, timeout=30)
        assert r.returncode != 0, "a stale-extension unknown_op must exit non-zero"
        low = r.stderr.lower()
        assert "unknown_op" in low
        assert "older than this cli" in low
        assert "restart brave" in low, f"expected restart-Brave guidance, got: {r.stderr!r}"
    finally:
        srv.shutdown(); srv.server_close()


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_normal_op_result_unaffected_by_unknown_op_mapping(tmp_path):
    """A NORMAL successful op result is unaffected by the unknown_op mapping."""
    data = {"url": "https://x.test", "title": "X", "html": "<html>ok</html>"}
    r = _run_browser_against(data, ["html"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "older than this cli" not in r.stderr.lower()
    out = json.loads(r.stdout)
    assert out["result"]["data"]["html"] == "<html>ok</html>"


# =========================================================================== #
# CLI token fixes: screenshot writes a FILE (never dumps base64 to stdout),
# `html` gets the same --max-bytes cap as `text`, and `js` aliases `eval`.
# All three are CLI-surface only (no server.py / extension change), so they are
# exercised against the REAL bash entrypoint + a canned in-process /cmd server.
# =========================================================================== #

# A minimal, VALID 1x1 PNG — so the CLI's base64 decode + file write is a real
# round-trip (the assertions check the PNG magic bytes on disk).
_PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8"
            "AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC")
_PNG_BYTES = base64.b64decode(_PNG_B64)
_PNG_DATA_URL = "data:image/png;base64," + _PNG_B64


class _CannedRecordingCmdServer:
    """`_CannedCmdServer` + it RECORDS each posted /cmd body, so one test can
    assert BOTH the request shape (op/flags on the wire) and the CLI's rendering
    of the canned result."""

    def __init__(self, data):
        self.data = data
        self.bodies = []

    def handler(self):
        outer = self

        class _H(S.BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: A003
                pass

            def do_POST(self):
                ln = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(ln) if ln else b"{}"
                try:
                    outer.bodies.append(json.loads(raw))
                except ValueError:
                    outer.bodies.append(None)
                payload = json.dumps({"ok": True, "result": {
                    "id": "x", "ok": True, "data": outer.data}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        return _H


def _run_browser_canned(data, args, tmp_path, env_extra=None):
    """Run the real `browser` CLI against a canned-result server that ALSO
    records the posted bodies. Returns (CompletedProcess, bodies)."""
    state = _CannedRecordingCmdServer(data)
    srv = S.ThreadingHTTPServer(("127.0.0.1", 0), state.handler())
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tokf = tmp_path / "token"
    tokf.write_text("smoke-token\n")
    env = dict(os.environ)
    env.update(BROWSER_BRIDGE_HOST="127.0.0.1", BROWSER_BRIDGE_PORT=str(port),
               BROWSER_BRIDGE_TOKEN_FILE=str(tokf))
    if env_extra:
        env.update(env_extra)
    try:
        r = subprocess.run([str(BROWSER_BIN), *args], env=env,
                           capture_output=True, text=True, timeout=CLI_TIMEOUT_S)
        return r, state.bodies
    finally:
        srv.shutdown(); srv.server_close()


def _shot_data(**extra):
    d = {"url": "https://x.test", "dataUrl": _PNG_DATA_URL, "via": "cdp"}
    d.update(extra)
    return d


# --- screenshot never dumps base64 to stdout -------------------------------- #

@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_no_path_writes_temp_file_and_prints_path(tmp_path):
    """No path → the PNG lands in TMPDIR and stdout is a compact JSON result
    carrying the PATH + byte size, NOT the base64 payload."""
    tmpdir = tmp_path / "shots"
    tmpdir.mkdir()
    r, _ = _run_browser_canned(_shot_data(), ["screenshot"], tmp_path,
                               env_extra={"TMPDIR": str(tmpdir)})
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is True
    p = Path(out["path"])
    assert p.is_file(), f"no png written at {p}"
    assert p.parent == tmpdir, "the temp png must honour TMPDIR"
    assert p.name.startswith("browser-screenshot-") and p.suffix == ".png"
    # A real PNG round-tripped through base64.
    assert p.read_bytes() == _PNG_BYTES
    assert p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert out["bytes"] == len(_PNG_BYTES)
    # Useful metadata carried through from the result.
    assert out["url"] == "https://x.test"
    assert out["via"] == "cdp"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_no_path_stdout_has_no_base64_payload(tmp_path):
    """THE fix: the 133K–890K-token base64 blob must never reach stdout/stderr."""
    tmpdir = tmp_path / "shots"
    tmpdir.mkdir()
    r, _ = _run_browser_canned(_shot_data(), ["screenshot"], tmp_path,
                               env_extra={"TMPDIR": str(tmpdir)})
    assert r.returncode == 0, r.stderr
    assert "data:image/png;base64," not in r.stdout
    assert _PNG_B64 not in r.stdout
    # Not even a FRAGMENT of the payload leaks (stdout or stderr).
    assert _PNG_B64[:24] not in r.stdout
    assert _PNG_B64[:24] not in r.stderr
    # Stdout stays SMALL (a compact JSON line), not blob-sized.
    assert len(r.stdout) < 600, f"stdout unexpectedly large: {len(r.stdout)}"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_explicit_path_prints_path_then_a_read_hint(tmp_path):
    """REGRESSION (2026-08-02 usage audit, F4). 7 of 63 explicit-path captures were
    never Read back — exit 0 plus a path on stdout looks like the job is done, and
    an image nobody Reads is pure waste. One short `#`-prefixed hint now follows.

    LINE 1 IS STILL THE BARE PATH — that is the back-compat half this used to pin
    (`stdout.strip() == path`), and the hint is deliberately on line 2 and comment-
    prefixed so it can never be mistaken for a second path.
    """
    dest = tmp_path / "shot.png"
    r, _ = _run_browser_canned(_shot_data(), ["screenshot", str(dest)], tmp_path)
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert lines[0] == str(dest), "line 1 must stay the bare path"
    assert lines[1] == "# Read %s to view it." % dest
    assert len(lines) == 2
    assert dest.read_bytes() == _PNG_BYTES
    assert _PNG_B64[:24] not in r.stdout


def test_browser_cli_screenshot_read_hint_not_printed_where_it_would_be_wrong(tmp_path):
    """NEGATIVE CONTROL for the hint above — without it, the test passes with the
    string printed UNCONDITIONALLY.

    Two forms must NOT carry it: `--data-url` (no file is written at all, so
    "Read it" is false), and the temp-file form (whose JSON `note` already says
    the same thing — a second copy would be per-call bytes for nothing).
    """
    r, _ = _run_browser_canned(_shot_data(), ["screenshot", "--data-url"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "# Read " not in r.stdout

    r, _ = _run_browser_canned(_shot_data(), ["screenshot"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "# Read " not in r.stdout
    assert json.loads(r.stdout)["path"].endswith(".png")   # still the JSON shape


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_data_url_escape_hatch(tmp_path):
    """`--data-url` restores the old behaviour: the full data URL on stdout."""
    r, _ = _run_browser_canned(_shot_data(), ["screenshot", "--data-url"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert _PNG_DATA_URL in r.stdout.replace("\n", "")


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_data_url_with_path_is_rejected(tmp_path):
    """--data-url and an explicit path are mutually exclusive (no silent winner)."""
    r, _ = _run_browser_canned(_shot_data(),
                               ["screenshot", "--data-url", str(tmp_path / "s.png")],
                               tmp_path)
    assert r.returncode != 0
    assert "mutually exclusive" in r.stderr.lower()


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_fullpage_still_threaded_with_file_output(tmp_path):
    """--fullpage still reaches the wire on the new (file-writing) default path."""
    tmpdir = tmp_path / "shots"
    tmpdir.mkdir()
    r, bodies = _run_browser_canned(_shot_data(), ["screenshot", "--fullpage"],
                                    tmp_path, env_extra={"TMPDIR": str(tmpdir)})
    assert r.returncode == 0, r.stderr
    assert bodies[-1]["op"] == "screenshot"
    assert bodies[-1]["fullpage"] is True
    assert Path(json.loads(r.stdout)["path"]).is_file()


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_missing_image_data_still_errors(tmp_path):
    """A result with no dataUrl is a clear error, not a silent empty file."""
    r, _ = _run_browser_canned({"url": "https://x.test"}, ["screenshot"], tmp_path)
    assert r.returncode != 0
    assert "missing image data" in r.stderr.lower()


# --- screenshot: privacy of the temp file (mode / validation / retention) --- #

@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_temp_file_is_mode_0600(tmp_path):
    """PRIVACY: a screenshot is a pixel-perfect image of an AUTHENTICATED view.
    The temp PNG must be owner-only (tempfile.mkstemp), NOT umask-derived 0644 —
    /tmp is world-readable and shared with every UID on the box."""
    tmpdir = tmp_path / "shots"
    tmpdir.mkdir()
    r, _ = _run_browser_canned(_shot_data(), ["screenshot"], tmp_path,
                               env_extra={"TMPDIR": str(tmpdir)})
    assert r.returncode == 0, r.stderr
    p = Path(json.loads(r.stdout)["path"])
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600, f"temp screenshot is {oct(mode)}, must be 0o600"


@pytest.mark.parametrize("payload,why", [
    ("data:image/png;base64,%%%%", "junk that b64decode would silently drop"),
    ("data:image/png;base64,", "an empty payload"),
    ("data:image/png;base64,notbase64!!!", "an outright invalid alphabet"),
])
@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_malformed_base64_errors_and_writes_nothing(
        payload, why, tmp_path):
    """A malformed payload must EXIT NON-ZERO with a clear message and leave NO
    file. Without validate=True, b64decode ignores non-alphabet chars → these
    would decode to b"" and be written as a 0-byte "successful" .png that an
    agent then Reads as if it were a screenshot (and the last case would raise an
    uncaught binascii.Error → a raw traceback)."""
    tmpdir = tmp_path / "shots"
    tmpdir.mkdir()
    r, _ = _run_browser_canned(_shot_data(dataUrl=payload), ["screenshot"],
                               tmp_path, env_extra={"TMPDIR": str(tmpdir)})
    assert r.returncode != 0, f"{why} must not report success"
    assert "not valid base64" in r.stderr or "not a PNG" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, "must be a clean message, not a traceback"
    assert list(tmpdir.iterdir()) == [], "no file (not even a 0-byte one) may remain"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_non_png_payload_is_rejected(tmp_path):
    """Valid base64 that isn't a PNG is refused before anything is written."""
    tmpdir = tmp_path / "shots"
    tmpdir.mkdir()
    not_png = base64.b64encode(b"GIF89a not really a png").decode()
    r, _ = _run_browser_canned(
        _shot_data(dataUrl="data:image/png;base64," + not_png),
        ["screenshot"], tmp_path, env_extra={"TMPDIR": str(tmpdir)})
    assert r.returncode != 0
    assert "not a PNG" in r.stderr
    assert list(tmpdir.iterdir()) == []


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_explicit_path_also_validates(tmp_path):
    """The same validation guards an EXPLICIT path — no 0-byte png there either."""
    dest = tmp_path / "shot.png"
    r, _ = _run_browser_canned(_shot_data(dataUrl="data:image/png;base64,%%%%"),
                               ["screenshot", str(dest)], tmp_path)
    assert r.returncode != 0
    assert not dest.exists(), "a malformed payload must not create the target file"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_prunes_aged_temp_captures_only(tmp_path):
    """Retention: temp captures older than 24h are auto-pruned on the next
    screenshot. Strictly prefix-scoped — a fresh capture and an unrelated file
    are left alone."""
    tmpdir = tmp_path / "shots"
    tmpdir.mkdir()
    old = tmpdir / "browser-screenshot-old.png"
    fresh = tmpdir / "browser-screenshot-fresh.png"
    other = tmpdir / "someone-elses-file.png"
    unrelated_suffix = tmpdir / "browser-screenshot-notes.txt"
    for f in (old, fresh, other, unrelated_suffix):
        f.write_bytes(b"x")
    aged = time.time() - (25 * 3600)         # older than the 24h retention
    os.utime(old, (aged, aged))
    os.utime(other, (aged, aged))            # aged BUT not ours → must survive
    os.utime(unrelated_suffix, (aged, aged))  # right prefix, wrong suffix → survives

    r, _ = _run_browser_canned(_shot_data(), ["screenshot"], tmp_path,
                               env_extra={"TMPDIR": str(tmpdir)})
    assert r.returncode == 0, r.stderr
    assert not old.exists(), "an aged browser-screenshot-*.png must be pruned"
    assert fresh.exists(), "a fresh capture must NOT be pruned"
    assert other.exists(), "pruning must be scoped to our own prefix"
    assert unrelated_suffix.exists(), "pruning must be scoped to .png"
    # The new capture is there too.
    assert Path(json.loads(r.stdout)["path"]).is_file()


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_prune_never_follows_a_symlink(tmp_path):
    """An aged SYMLINK matching the prefix is unlinked as the symlink itself; the
    file it points at is never touched (lstat + a regular-file check)."""
    tmpdir = tmp_path / "shots"
    tmpdir.mkdir()
    victim = tmp_path / "precious.png"
    victim.write_bytes(b"do not delete me")
    link = tmpdir / "browser-screenshot-link.png"
    link.symlink_to(victim)
    aged = time.time() - (25 * 3600)
    os.utime(link, (aged, aged), follow_symlinks=False)

    r, _ = _run_browser_canned(_shot_data(), ["screenshot"], tmp_path,
                               env_extra={"TMPDIR": str(tmpdir)})
    assert r.returncode == 0, r.stderr
    assert victim.read_bytes() == b"do not delete me", "the target must be untouched"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_screenshot_prune_is_best_effort_and_regular_files_only(tmp_path):
    """Pruning must never fail the command and must only ever unlink a REGULAR
    file: an aged DIRECTORY whose name matches the capture pattern is skipped
    (not rmdir'd, no error), and the screenshot still succeeds."""
    tmpdir = tmp_path / "shots"
    tmpdir.mkdir()
    decoy = tmpdir / "browser-screenshot-adir.png"
    decoy.mkdir()
    (decoy / "keep").write_bytes(b"keep")
    aged = time.time() - (25 * 3600)
    os.utime(decoy, (aged, aged))

    r, _ = _run_browser_canned(_shot_data(), ["screenshot"], tmp_path,
                               env_extra={"TMPDIR": str(tmpdir)})
    assert r.returncode == 0, r.stderr
    assert decoy.is_dir() and (decoy / "keep").exists()
    assert "Traceback" not in r.stderr
    assert Path(json.loads(r.stdout)["path"]).is_file()


# --- `html` gets the same --max-bytes cap as `text` ------------------------- #

@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_html_default_cap_truncates_with_note(tmp_path):
    """A >32 KB outerHTML is capped at the documented 32768-byte default and the
    truncation is ANNOUNCED (the `text` convention: a note + a `truncated` count)."""
    html = "<p>" + ("x" * 50000) + "</p>"
    r, bodies = _run_browser_canned(
        {"url": "https://x.test", "title": "X", "html": html}, ["html"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert bodies[-1]["op"] == "getHtml"
    assert bodies[-1]["maxBytes"] == 32768          # same default as `text`
    data = json.loads(r.stdout)["result"]["data"]
    assert data["truncated"] == len(html.encode()) - 32768
    assert data["html"].endswith("…[truncated %d bytes]" % data["truncated"])
    kept = data["html"].split("\n…[truncated")[0]
    assert len(kept.encode("utf-8")) == 32768
    assert kept == html[:32768]                     # a PREFIX of the original
    # And the whole payload is now bounded (32 KB + the note), not 50 KB.
    assert len(r.stdout) < 34000


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_html_explicit_max_bytes_honoured(tmp_path):
    html = "<p>" + ("y" * 5000) + "</p>"
    r, bodies = _run_browser_canned(
        {"url": "https://x.test", "title": "X", "html": html},
        ["html", "--max-bytes", "100"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert bodies[-1]["maxBytes"] == 100
    data = json.loads(r.stdout)["result"]["data"]
    kept = data["html"].split("\n…[truncated")[0]
    assert len(kept.encode("utf-8")) == 100
    assert data["truncated"] == len(html.encode()) - 100


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_html_max_bytes_zero_is_truly_uncapped(tmp_path):
    """`--max-bytes 0` is a real escape hatch — the FULL html, no note."""
    html = "<p>" + ("z" * 60000) + "</p>"
    r, bodies = _run_browser_canned(
        {"url": "https://x.test", "title": "X", "html": html},
        ["html", "--max-bytes=0"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert bodies[-1]["maxBytes"] == 0
    data = json.loads(r.stdout)["result"]["data"]
    assert data["html"] == html
    assert "truncated" not in data


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_html_under_cap_is_byte_identical_no_regression(tmp_path):
    """Under the cap the response is passed through UNTOUCHED — no `truncated`
    field, no note, no reserialization: an ordinary `html` read is unchanged."""
    html = "<html><body>ünïcøde ok</body></html>"
    data = {"url": "https://x.test", "title": "X", "html": html}
    r, _ = _run_browser_canned(data, ["html"], tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)["result"]["data"]
    assert out == data, "an under-cap read must be byte-for-byte the old result"
    assert "truncated" not in r.stdout


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_html_truncation_never_splits_a_multibyte_char(tmp_path):
    """The cut lands on a UTF-8 boundary (normalizeText's guarantee) — the JSON
    still parses and the kept text is valid unicode."""
    html = "é" * 100                     # 2 bytes each → an odd cap splits a char
    r, _ = _run_browser_canned({"url": "https://x.test", "title": "X", "html": html},
                               ["html", "--max-bytes", "51"], tmp_path)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)["result"]["data"]
    kept = data["html"].split("\n…[truncated")[0]
    assert kept == "é" * 25              # 50 bytes — the 51st (partial) byte is dropped
    assert data["truncated"] == 200 - 50


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_html_rejects_bad_maxbytes_and_positional(tmp_path):
    r, _ = _run_browser_canned({"html": "<p/>"}, ["html", "--max-bytes", "nope"],
                               tmp_path)
    assert r.returncode != 0
    assert "max-bytes" in r.stderr.lower()
    r, _ = _run_browser_canned({"html": "<p/>"}, ["html", "main"], tmp_path)
    assert r.returncode != 0
    assert "positional" in r.stderr.lower()


# --- `js` is a first-class alias for `eval` --------------------------------- #

@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_js_and_eval_build_an_identical_request(tmp_path):
    """`js` and `eval` post the SAME body — and the WIRE op is `eval` for BOTH
    (the extension only knows `eval`; `js` is a CLI-surface alias only)."""
    canned = {"url": "https://x.test", "value": 2}
    r_js, b_js = _run_browser_canned(canned, ["js", "1+1"], tmp_path)
    r_ev, b_ev = _run_browser_canned(canned, ["eval", "1+1"], tmp_path)
    assert r_js.returncode == 0, r_js.stderr
    assert r_ev.returncode == 0, r_ev.stderr
    assert b_js[-1]["op"] == "eval", "the wire op for `js` MUST stay `eval`"
    assert b_ev[-1]["op"] == "eval"
    assert b_js[-1] == b_ev[-1]
    assert b_js[-1]["js"] == "1+1"
    assert json.loads(r_js.stdout) == json.loads(r_ev.stdout)


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_js_alias_honours_frame_tab_instance(tmp_path):
    """The global flags work identically through the alias."""
    canned = {"url": "https://x.test", "value": 1}
    r, bodies = _run_browser_canned(
        canned,
        ["--frame", "42", "--tab", "77", "--instance", "work", "js", "document.title"],
        tmp_path)
    assert r.returncode == 0, r.stderr
    b = bodies[-1]
    assert b["op"] == "eval"          # still `eval` on the wire
    assert b["js"] == "document.title"
    assert b["frame"] == "42"
    assert b["tab"] == 77
    assert b["target"] == "work"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not on PATH")
def test_browser_cli_js_requires_an_expression(tmp_path):
    r, _ = _run_browser_canned({"value": 1}, ["js"], tmp_path)
    assert r.returncode != 0
    assert "usage: browser js" in r.stderr


def test_browser_cli_help_lists_js_alias_and_screenshot_data_url():
    """`browser --help` (the header comment) documents the new surface, and the
    unknown-subcommand error lists `js` too."""
    r = subprocess.run([str(BROWSER_BIN), "--help"], capture_output=True,
                       text=True, timeout=CLI_TIMEOUT_S)
    assert r.returncode == 0, r.stderr
    assert "browser js '<js>'" in r.stdout
    assert "--data-url" in r.stdout
    assert "--max-bytes" in r.stdout
    r2 = subprocess.run([str(BROWSER_BIN), "bogus-op"], capture_output=True,
                        text=True, timeout=CLI_TIMEOUT_S)
    assert r2.returncode != 0
    assert " js " in r2.stderr and " eval " in r2.stderr


# --------------------------------------------------------------------------- #
# `wake` op: tab-scoped, waitMs passthrough, and — the point of the whole change
# — it NEVER triggers the host-side i3 foregrounding that steals the operator's
# screen. `activate` remains the only op that does.
# --------------------------------------------------------------------------- #
def test_wake_routes_to_owned_session_tab():
    """`wake` is tab-scoped: a session that `open`ed has its wake routed to ITS
    owned tabId, so it can only ever un-throttle the tab the session owns."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        st, body = _cmd(srv, {"op": "wake"}, session="A")
        assert st == 200
        assert body["result"]["data"]["tabId"] == 101, \
            "wake must route to the session's owned tab (own-tab enforced)"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_wake_explicit_tab_override():
    """`--tab <id>` forces the wake target even with no owned tab."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "wake", "tab": 88},
                        headers={S.HDR_SESSION_ID: "A"})
        assert st == 200
        assert body["result"]["data"]["tabId"] == 88
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_wake_forwards_waitms_to_extension():
    """`waitMs` (the un-throttle settle) is a passthrough command field."""
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {"tabId": 5, "woke": True})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd", {"op": "wake", "waitMs": 500})
        assert st == 200
        wakes = [c for c in ext.dispatched if c["op"] == "wake"]
        assert wakes and wakes[0].get("waitMs") == 500
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_wake_NEVER_invokes_i3_msg(monkeypatch):
    """THE REGRESSION GUARD FOR THIS WHOLE CHANGE. The host-side i3 foregrounding
    is what actually yanks the operator's screen, and it is keyed on
    op == "activate" alone. Enable i3 (so a leak WOULD be observable), then assert
    a successful `wake` spawns NO i3-msg and adds no `i3` field to the result."""
    calls = _enable_i3(monkeypatch, returncode=0)
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "woke": True, "visibilityState": "visible",
        "url": "https://model-benchmarking.example.test/run",
        "title": "Model Benchmarking"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "wake"})
        assert st == 200
        assert body["result"]["data"]["woke"] is True
        assert calls == [], "wake must NEVER shell out to i3-msg (that is focus theft)"
        assert "i3" not in body["result"]["data"], \
            "wake does no host-side foregrounding, so it must not claim an i3 state"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_wake_telemetry_is_metadata_only(telemetry):
    """PRIVACY: a wake event emits ONLY op + the bare domain — never page content
    and never a full URL with path/query."""
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {
        "tabId": 5, "woke": True,
        "url": "https://model-benchmarking.example.test/run?q=secret"})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd", {"op": "wake"})
        assert st == 200
        e = _wait_events(spool_dir, 1)[0]
        p = json.loads(e["payload"])
        assert p["op"] == "wake"
        assert p["outcome"] == "ok"
        assert p["domain"] == "model-benchmarking.example.test"
        assert "secret" not in json.dumps(e), "no full URL / query ever in telemetry"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Silent-drop honesty + detector (2026-07-31 diagnosis)
#
# `extension_connected` is a bare OR across live instances, so one healthy Brave
# profile reported the bridge as UP for as long as it lived while a named
# instance (`work`) had silently dropped. The field cannot be redefined without
# breaking callers that legitimately ask "is anything up", so the truth is
# carried alongside it: `known_instances` (every routing key seen this process
# lifetime, each with `connected`) and `missing`.
#
# Alongside that, `instance_lost` gives the drop a journal trace naming the last
# command the instance never answered — the operator's second drop produced no
# evidence at all because nothing was dispatched while it was down.
# --------------------------------------------------------------------------- #
def _drop_registry():
    """A registry on a controllable clock, with `work` and `personal` polled in
    once so both are KNOWN. Returns (reg, clock-list)."""
    clock = [1000.0]
    reg = S.Registry(clock=lambda: clock[0])
    with reg._cond:  # noqa: SLF001 — white-box on purpose: no HTTP needed
        reg._register_locked("id-work", "work")          # noqa: SLF001
        reg._register_locked("id-personal", "personal")  # noqa: SLF001
    return reg, clock


def test_known_instances_reports_a_dropped_instance_as_not_connected():
    reg, clock = _drop_registry()
    known, missing = reg.known_snapshot()
    assert {k["key"] for k in known} == {"work", "personal"}
    assert all(k["connected"] for k in known)
    assert missing == []
    # `work` stops polling; `personal` keeps polling.
    clock[0] += S.CONNECT_STALE_S + 1
    with reg._cond:  # noqa: SLF001
        reg._instances["personal"].last_poll = clock[0]  # noqa: SLF001
    known, missing = reg.known_snapshot()
    by_key = {k["key"]: k for k in known}
    assert by_key["work"]["connected"] is False
    assert by_key["personal"]["connected"] is True
    assert [m["key"] for m in missing] == ["work"]


def test_a_long_gone_instance_is_forgotten_so_health_does_not_nag_forever():
    """Without a forget path the registry remembers every key for its whole
    process lifetime, so an operator who normally runs ONE profile would see a
    permanent `other: DISCONNECTED` line — a warning that is always on is a
    warning nobody reads."""
    reg, clock = _drop_registry()
    clock[0] += S.CONNECT_STALE_S + 1
    _known, missing = reg.known_snapshot()
    assert {m["key"] for m in missing} == {"work", "personal"}
    clock[0] += S.KNOWN_FORGET_S            # both now long past the cutoff
    known, missing = reg.known_snapshot()
    assert known == [] and missing == []


def test_INVARIANT_GUARD_a_freshly_polled_instance_is_not_aged_out():
    """⚠ INVARIANT GUARD, **not** coverage of the live-exemption clause.

    This pins only "a recent `last_poll` beats the age-out", i.e. that the cutoff
    keys off staleness rather than uptime. It CANNOT fail if the
    `id(inst) not in live_ids` term is deleted from the age-out, because
    `now - last_poll == 0` already makes the predicate false on its own. The
    clause itself is pinned by
    test_forget_exempts_an_instance_live_via_an_in_flight_poll below.

    Kept because it is the case an operator actually has (a profile polling for a
    week must never be reported as gone), and it is cheap."""
    reg, clock = _drop_registry()
    clock[0] += S.KNOWN_FORGET_S * 3
    with reg._cond:  # noqa: SLF001
        for inst in reg._instances.values():  # noqa: SLF001
            inst.last_poll = clock[0]         # still polling
    known, missing = reg.known_snapshot()
    assert {k["key"] for k in known} == {"work", "personal"}
    assert missing == []


def test_forget_exempts_an_instance_live_via_an_in_flight_poll():
    """The live-exemption term in the age-out, pinned for real.

    `_live_instances_locked` calls an instance alive on EITHER a fresh
    `last_poll` OR `active_polls > 0`. Only the second disjunct can be true while
    `last_poll` is ancient, so that is the state this constructs: a stale
    timestamp AND a poll thread in flight. Such an instance must NOT be forgotten
    — forgetting a connection that is mid-request is the one thing the age-out
    must never do.

    ⚠ Honest scope: this state is believed UNREACHABLE in production. `poll()`
    stamps `last_poll` at entry and again in its `finally`, so an in-flight poll's
    timestamp can be at most `poll_timeout` old — never past KNOWN_FORGET_S. The
    exemption is defensive. This test exists so the clause cannot be deleted as
    'dead' without a red test, since the reasoning that makes it dead lives in a
    different method."""
    clock = [1000.0]
    reg = S.Registry(clock=lambda: clock[0])
    with reg._cond:  # noqa: SLF001
        inst = reg._register_locked("id-work", "work")  # noqa: SLF001
        inst.active_polls = 1                 # a poll thread is parked in _cond.wait
    clock[0] += S.KNOWN_FORGET_S * 2          # ...with an ancient last_poll
    known, missing = reg.known_snapshot()
    assert [k["key"] for k in known] == ["work"], \
        "an instance with a poll IN FLIGHT must never be aged out"
    assert known[0]["connected"] is True
    assert missing == []


# --- `browser health`'s DISCONNECTED line (the only operator-visible surface) - #
def _serve_canned_health(payload):
    """A stub bridge that answers /health with `payload` verbatim, so the CLI's
    rendering can be driven against server shapes the real Registry cannot
    produce (an OLD server with no `known_instances`, a malformed entry)."""
    class H(S.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # silence
            pass

    srv = S.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _run_health(srv, tmp_path):
    tokf = tmp_path / "token"
    tokf.write_text("health-token\n")
    env = dict(os.environ)
    env.update(BROWSER_BRIDGE_HOST="127.0.0.1",
               BROWSER_BRIDGE_PORT=str(srv.server_address[1]),
               BROWSER_BRIDGE_TOKEN_FILE=str(tokf))
    return subprocess.run([str(BROWSER_BIN), "health"], env=env,
                          capture_output=True, text=True, timeout=CLI_TIMEOUT_S)


def test_browser_health_prints_one_DISCONNECTED_line_and_keeps_stdout_json(tmp_path):
    """The whole point of this PR, exercised through the REAL CLI: a dropped
    named instance is glanceable, and stdout stays machine-parseable."""
    srv = _serve_canned_health({
        "ok": True, "extension_connected": True, "count": 1, "instances": [],
        "known_instances": [
            {"key": "personal", "connected": True, "last_seen": "2026-07-31T22:00:00Z",
             "last_unanswered_op": None},
            {"key": "work", "connected": False, "last_seen": "2026-07-31T18:02:34Z",
             "last_unanswered_op": "frames"},
        ],
        "missing": [{"key": "work"}]})
    try:
        r = _run_health(srv, tmp_path)
        assert r.returncode == 0, r.stderr
        json.loads(r.stdout)                    # stdout is UNBROKEN JSON
        lines = [x for x in r.stderr.splitlines() if x.strip()]
        assert lines == [
            "browser: work: DISCONNECTED (last seen 2026-07-31T18:02:34Z, "
            "last unanswered op: frames)"], r.stderr
    finally:
        srv.shutdown(); srv.server_close()


def test_browser_health_is_SILENT_when_every_known_instance_is_connected(tmp_path):
    srv = _serve_canned_health({
        "ok": True, "extension_connected": True, "count": 1, "instances": [],
        "known_instances": [{"key": "work", "connected": True,
                             "last_seen": "2026-07-31T22:00:00Z"}],
        "missing": []})
    try:
        r = _run_health(srv, tmp_path)
        assert r.returncode == 0 and r.stderr.strip() == "", r.stderr
    finally:
        srv.shutdown(); srv.server_close()


def test_browser_health_degrades_silently_against_an_OLD_server(tmp_path):
    """`browser` is deployed as a symlink to the working tree, so it goes live on
    `git pull` — BEFORE the switch that restarts server.py. It therefore WILL run
    against a server with no `known_instances`, and must stay silent and rc 0."""
    srv = _serve_canned_health({"ok": True, "extension_connected": True,
                                "count": 1, "instances": [{"key": "work"}]})
    try:
        r = _run_health(srv, tmp_path)
        assert r.returncode == 0 and r.stderr.strip() == "", r.stderr
        json.loads(r.stdout)
    finally:
        srv.shutdown(); srv.server_close()


def test_browser_health_survives_a_malformed_known_instances(tmp_path):
    """render_missing is the LAST command of the `health` arm, so it owns the
    exit code — a traceback would turn a working probe into rc 1. The try must
    wrap the whole walk, not just the JSON parse."""
    srv = _serve_canned_health({"ok": True, "extension_connected": True,
                                "count": 0, "instances": [],
                                "known_instances": ["not-a-dict", 7, None]})
    try:
        r = _run_health(srv, tmp_path)
        assert r.returncode == 0, r.stderr
        assert "Traceback" not in r.stderr
    finally:
        srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# S3 — the routing-failure messages (2026-08-02 usage audit, F6)
#
# `unknown_instance` was the TOP error at 52 occurrences, and 48 of them used the
# CORRECT label (`work`) — 35 inside a single hour, with `eval` re-issued 37 times.
# A Brave profile had dropped its long-poll; the message ("no connected instance
# matches --instance 'work'") reads as "you typed the wrong label", so agents kept
# retrying a name that was right. `no_extension` has the identical shape (16 of 19
# in one hour).
#
# The distinguishing fact was already on the wire — `known_instances` — it just was
# not being read. These tests pin BOTH branches, because a single-case test passes
# with the branch hard-wired to one string.
# --------------------------------------------------------------------------- #
def _serve_canned_cmd(status, payload):
    """A stub bridge whose /cmd always answers `status` with `payload` verbatim, so
    the CLI's error rendering can be driven against exact server bodies."""
    class H(S.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # noqa: A003
            pass

    srv = S.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _run_browser_routing(srv, args, tmp_path):
    tokf = tmp_path / "token"
    tokf.write_text("routing-token\n")
    env = dict(os.environ)
    env.update(BROWSER_BRIDGE_HOST="127.0.0.1",
               BROWSER_BRIDGE_PORT=str(srv.server_address[1]),
               BROWSER_BRIDGE_TOKEN_FILE=str(tokf))
    return subprocess.run([str(BROWSER_BIN), *args], env=env,
                          capture_output=True, text=True, timeout=CLI_TIMEOUT_S)


_UNKNOWN_404 = {
    "ok": False, "error": "unknown_instance", "target": "work",
    "instances": [{"key": "personal", "label": "personal"}],
    "known_instances": [
        {"key": "personal", "connected": True, "last_seen": "2026-08-02T04:00:00Z"},
        {"key": "work", "connected": False, "last_seen": "2026-08-02T02:11:09Z",
         "last_seen_age_s": 6531.0, "last_unanswered_op": "eval"},
    ],
}


def test_unknown_instance_KNOWN_but_disconnected_says_stop_retrying(tmp_path):
    """REGRESSION. The label is CORRECT and the profile is gone — the message must
    say so and must tell the operator NOT to retry. 37 blind retries is the
    measured symptom of a message that never said that."""
    srv = _serve_canned_cmd(404, _UNKNOWN_404)
    try:
        r = _run_browser_routing(srv, ["--instance", "work", "tabs"], tmp_path)
        assert r.returncode != 0
        err = r.stderr
        assert "KNOWN but NOT CONNECTED" in err, err
        assert "the label is correct" in err, err
        assert "FULLY RESTART Brave" in err, err
        assert "DO NOT RETRY" in err, err
        assert "2026-08-02T02:11:09Z" in err, "name WHEN it went away"
        # `last_unanswered_op` is the evidence server.py calls "the single fact that
        # turns the next silent drop from inference into evidence" — it says the
        # profile died MID-`eval` rather than going quiet while idle. render_missing
        # always printed it; the rewritten advice must not have dropped it.
        assert "last unanswered op: eval" in err, err
        # ...and it must NOT tell the operator their label was wrong.
        assert "is UNKNOWN" not in err, err
    finally:
        srv.shutdown(); srv.server_close()


def test_unknown_instance_NEVER_SEEN_key_says_wrong_label(tmp_path):
    """NEGATIVE CONTROL for the test above. Without it, the branch could be
    hard-wired to the disconnected wording and both tests would still be green for
    the wrong reason. A genuinely bogus label must get the OPPOSITE message —
    "wrong label", plus the keys that do exist, and no restart advice."""
    srv = _serve_canned_cmd(404, {**_UNKNOWN_404, "target": "nosuchlabel"})
    try:
        r = _run_browser_routing(srv, ["--instance", "nosuchlabel", "tabs"],
                                 tmp_path)
        assert r.returncode != 0
        err = r.stderr
        assert "is UNKNOWN" in err, err
        assert "WRONG LABEL" in err, err
        assert "Keys this server HAS seen" in err and "personal" in err, err
        # The wrong-label branch ALSO keeps render_missing's drop evidence: a typo
        # and a dead profile are frequently the SAME incident, and this branch is
        # where that used to become invisible.
        assert "last seen 2026-08-02T02:11:09Z" in err, err
        assert "last unanswered op: eval" in err, err
        # The two branches must be mutually exclusive.
        assert "KNOWN but NOT CONNECTED" not in err, err
        assert "DO NOT RETRY" not in err, err
    finally:
        srv.shutdown(); srv.server_close()


def test_no_extension_distinguishes_dropped_from_never_wired_up(tmp_path):
    """`no_extension` (16 of 19 in one hour) has the same shape, so it gets the
    same split: a profile that HAS been seen and is now gone means "restart Brave,
    stop retrying"; nothing ever seen means "load the extension"."""
    dropped = _serve_canned_cmd(503, {
        "ok": False, "error": "extension_not_connected",
        "known_instances": [
            {"key": "work", "connected": False,
             "last_seen": "2026-08-02T02:11:09Z", "last_seen_age_s": 6531.0,
             "last_unanswered_op": "screenshot"}]})
    try:
        r = _run_browser_routing(dropped, ["tabs"], tmp_path)
        assert r.returncode != 0
        err = r.stderr
        assert "HAS seen" in err and "work" in err, err
        assert "last unanswered op: screenshot" in err, err
        assert "FULLY RESTART Brave" in err and "DO NOT RETRY" in err, err
        assert "has EVER connected" not in err, err
    finally:
        dropped.shutdown(); dropped.server_close()

    virgin = _serve_canned_cmd(503, {"ok": False,
                                     "error": "extension_not_connected",
                                     "known_instances": []})
    try:
        r = _run_browser_routing(virgin, ["tabs"], tmp_path)
        assert r.returncode != 0
        err = r.stderr
        assert "has EVER connected" in err, err
        assert "load the browser-bridge extension" in err, err
        assert "DO NOT RETRY" not in err, err
    finally:
        virgin.shutdown(); virgin.server_close()


def test_routing_failure_explainer_degrades_against_an_OLD_server(tmp_path):
    """`browser` is a symlink to the working tree, so it goes live on `git pull` —
    BEFORE the switch that restarts server.py. It WILL run against a server with no
    `known_instances`, and must still exit non-zero with readable advice and no
    traceback (the same degradation contract render_missing has)."""
    for status, payload, args in (
            (404, {"ok": False, "error": "unknown_instance", "target": "work"},
             ["--instance", "work", "tabs"]),
            (503, {"ok": False, "error": "extension_not_connected"}, ["tabs"]),
            (404, {"ok": False, "error": "unknown_instance",
                   "known_instances": ["not-a-dict", 7, None]},
             ["--instance", "work", "tabs"])):
        srv = _serve_canned_cmd(status, payload)
        try:
            r = _run_browser_routing(srv, args, tmp_path)
            assert r.returncode != 0, payload
            assert "Traceback" not in r.stderr, r.stderr
            assert r.stderr.strip(), "must still say something"
        finally:
            srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# S6 — doc pointers inside the CLI must RESOLVE
# --------------------------------------------------------------------------- #
def test_every_skill_md_heading_the_cli_points_at_actually_exists():
    """`browser:582` and `browser:588` pointed at "SKILL.md → Concurrency" — a
    heading that has never existed (SKILL.md has 7 `##` headings and none is it;
    the content is bold text under "This is the user's LIVE session"). A pointer
    that does not resolve sends a reader hunting, and nothing was checking.

    HARNESS NEGATIVE CONTROL is inline below: the extractor is first run against a
    string containing a KNOWN-BAD pointer and must report it. Without that, an
    extractor that silently matches nothing would report a green that means
    nothing.
    """
    import re

    def bad_pointers(text, headings):
        """Every `SKILL.md -> <name>` / `SKILL.md → <name>` in `text` whose <name>
        does not resolve to a heading. A pointer may name a heading's PREFIX (the
        headings carry trailing "— triage"-style qualifiers that nobody types), but
        it must resolve to at least one — "Concurrency" resolves to none, which is
        the whole defect."""
        out = []
        for m in re.finditer(
                r'SKILL\.md\s*(?:->|→)\s*"?([^"\n(]+?)"?\s*(?:$|[)\.,]|→)',
                text, re.M):
            name = m.group(1).strip()
            if name and not any(h == name or h.startswith(name + " ")
                                for h in headings):
                out.append(name)
        return out

    skill = (BROWSER_BIN.parent / "SKILL.md").read_text(encoding="utf-8")
    headings = {ln.lstrip("#").strip()
                for ln in skill.splitlines() if ln.startswith("#")}
    assert len(headings) >= 5, "SKILL.md heading extraction looks broken"

    # NEGATIVE CONTROL: a pointer at a heading that is not there MUST be reported.
    assert bad_pointers('see SKILL.md → Concurrency.', headings) == ["Concurrency"]
    # ...and a pointer at a REAL heading must not be.
    assert bad_pointers('see SKILL.md -> "Ops".', headings) == []

    cli = BROWSER_BIN.read_text(encoding="utf-8")
    assert bad_pointers(cli, headings) == [], (
        "the CLI points at SKILL.md headings that do not exist")


def test_poll_timeout_at_or_above_the_extension_poll_budget_warns(capsys):
    """From extension 0.4.0 on, the extension aborts its own /poll at
    POLL_BUDGET_MS (40s); raising the server's poll_timeout to/past that makes
    every poll abort client-side and the extension backoff-spin. Nothing can
    enforce it across the two processes, so it must at least be said out loud.

    The warning MUST carry its version scope: the extension this server currently
    ships against (0.3.1) does not bound the poll fetch at all, so an unscoped
    message would describe behaviour that does not exist yet to an operator who
    is already debugging."""
    capsys.readouterr()
    S._warn_poll_timeout_vs_extension_budget(25.0)   # noqa: SLF001 — the default
    assert capsys.readouterr().err == "", "the default must be silent"
    S._warn_poll_timeout_vs_extension_budget(45.0)   # noqa: SLF001
    lines = [json.loads(x) for x in capsys.readouterr().err.splitlines() if x]
    assert [x["event"] for x in lines] == ["config_warning"]
    assert lines[0]["reason"] == "poll_timeout_exceeds_extension_poll_budget"
    assert lines[0]["applies_to_extension"] == "0.4.0+"
    assert "0.4.0" in lines[0]["detail"], \
        "the claim must name the extension version it applies to"


def test_extension_connected_still_true_but_known_instances_tells_the_truth():
    """REGRESSION for the exact reported dishonesty: with two profiles wired up
    and one dropped, the boolean stays true (by design, for its existing
    callers) — and the drop is now visible in the SAME payload."""
    reg, clock = _drop_registry()
    clock[0] += S.CONNECT_STALE_S + 1
    with reg._cond:  # noqa: SLF001
        reg._instances["personal"].last_poll = clock[0]  # noqa: SLF001
    assert reg.connected is True, \
        "extension_connected must NOT be silently redefined — callers depend on it"
    _known, missing = reg.known_snapshot()
    assert [m["key"] for m in missing] == ["work"], \
        "the dropped instance must be nameable from the same payload"


def test_health_payload_carries_known_instances_and_missing():
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="fake-1", label="work")
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "GET", "/health")
        assert st == 200
        assert [k["key"] for k in body["known_instances"]] == ["work"]
        assert body["known_instances"][0]["connected"] is True
        assert body["known_instances"][0]["last_seen"].endswith("Z")
        assert body["missing"] == []
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_instance_lost_is_logged_once_and_names_the_unanswered_command(capsys):
    """The detector. A drop must leave a journal trace naming the op the
    instance never came back from — the fact that would have identified `frames`
    on 2026-07-29 without any code reading."""
    clock = [1000.0]
    reg = S.Registry(clock=lambda: clock[0])
    with reg._cond:  # noqa: SLF001
        inst = reg._register_locked("id-work", "work")  # noqa: SLF001
        # Model a dispatched-but-never-answered command (what `submit` records).
        inst.last_dispatch = {"id": "abc123", "op": "frames", "at": 0.0}
    capsys.readouterr()                       # drop registration noise
    clock[0] += S.CONNECT_STALE_S + 5
    reg.snapshot()
    reg.snapshot()                            # a second probe must NOT re-log
    lines = [json.loads(x) for x in capsys.readouterr().err.splitlines() if x]
    lost = [x for x in lines if x["event"] == "instance_lost"]
    assert len(lost) == 1, f"edge-triggered, not per-probe: {lines}"
    assert lost[0]["key"] == "work"
    assert lost[0]["last_op"] == "frames"
    assert lost[0]["last_id"] == "abc123"
    assert lost[0]["stale_s"] >= S.CONNECT_STALE_S


def test_instance_connected_is_logged_when_a_dropped_instance_returns(capsys):
    clock = [1000.0]
    reg = S.Registry(clock=lambda: clock[0])
    with reg._cond:  # noqa: SLF001
        reg._register_locked("id-work", "work")  # noqa: SLF001
    clock[0] += S.CONNECT_STALE_S + 5
    reg.snapshot()                            # → instance_lost
    with reg._cond:  # noqa: SLF001
        reg._instances["work"].last_poll = clock[0]  # noqa: SLF001
    capsys.readouterr()
    reg.snapshot()                            # → instance_connected
    lines = [json.loads(x) for x in capsys.readouterr().err.splitlines() if x]
    assert [x["event"] for x in lines if x["event"].startswith("instance_")] \
        == ["instance_connected"]


def test_last_dispatch_is_cleared_once_the_command_is_answered():
    """A healthy instance must not report a phantom `last_unanswered_op` — that
    would make every future drop report a stale, misleading op name."""
    srv, reg = _serve()
    ext = FakeExtension(srv, instance_id="fake-1", label="work")
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd", {"op": "tabs"})
        assert st == 200
        known, _missing = reg.known_snapshot()
        assert known[0]["last_unanswered_op"] is None
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_last_unanswered_op_survives_a_swallowed_command():
    """The wedge signature: the extension picked the command up and never
    answered. That op name is exactly what the detector must retain."""
    srv, reg = _serve(cmd_timeout=0.3)
    ext = FakeExtension(srv, instance_id="fake-1", label="work", swallow=True)
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, _ = _req(srv, "POST", "/cmd", {"op": "frames"})
        assert st == 504
        known, _missing = reg.known_snapshot()
        assert known[0]["last_unanswered_op"] == "frames"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --- fail FAST on an unresolvable target (never wait out cmd_timeout) -------- #
def test_unknown_instance_fails_fast_not_after_cmd_timeout():
    """MEASURED, not asserted-by-construction: a mistyped/unknown --instance must
    be rejected in well under the 20s cmd_timeout. `cmd_timeout` here is 5s, so a
    resolution error that took the timeout path would be unmissable."""
    srv, _ = _serve(cmd_timeout=5.0)
    ext = FakeExtension(srv, instance_id="fake-1", label="work")
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        t0 = time.monotonic()
        st, body = _req(srv, "POST", "/cmd",
                        {"op": "tabs", "target": "nosuchlabel"})
        elapsed = time.monotonic() - t0
        assert st == 404 and body["error"] == "unknown_instance"
        assert elapsed < 1.0, f"took {elapsed:.2f}s — it went down a waiting path"
        # And it names what IS known, so a typo and a silent drop are separable.
        assert [k["key"] for k in body["known_instances"]] == ["work"]
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_no_extension_fails_fast_and_names_the_instance_that_dropped():
    """A profile that HAS dropped (no in-flight poll, last_poll older than
    CONNECT_STALE_S) must fail fast and be NAMED.

    ⚠ Scope of this claim, measured: this is the settled state AFTER the
    transition window. While an instance still has a poll thread in flight the
    server correctly considers it live and the command waits out `cmd_timeout` —
    that bounded (≤ poll_timeout + CONNECT_STALE_S) window is unchanged here and
    is not something a resolution-time check can remove.
    """
    clock = [1000.0]
    reg = S.Registry(clock=lambda: clock[0])
    with reg._cond:  # noqa: SLF001
        reg._register_locked("fake-1", "work")  # noqa: SLF001
    clock[0] += S.CONNECT_STALE_S + 1          # it stopped polling; nothing in flight
    srv, _ = _serve(cmd_timeout=5.0, registry=reg)
    try:
        t0 = time.monotonic()
        st, body = _req(srv, "POST", "/cmd", {"op": "tabs", "target": "work"})
        elapsed = time.monotonic() - t0
        assert st == 503 and body["error"] == "extension_not_connected"
        assert elapsed < 1.0, f"took {elapsed:.2f}s — it went down a waiting path"
        assert [k["key"] for k in body["known_instances"]] == ["work"]
        assert body["known_instances"][0]["connected"] is False
    finally:
        srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# `emulate`: device emulation — the OWNED-TAB-ONLY blast-radius gate
#
# The extension-side behaviour (sticky re-application, the screenshot fast-path
# refusal, touch clicks) is covered by tests/emulation.test.mjs. What lives HERE is
# the half only the server knows: which tab a session may emulate at all.
# --------------------------------------------------------------------------- #
def test_emulate_is_a_known_tab_scoped_op():
    assert "emulate" in S.ALLOWED_OPS
    assert "emulate" in S.TAB_SCOPED_OPS, \
        "emulate acts on ONE tab, so it must serialize per-tab like every other"
    assert "emulate" in S.OWNED_TAB_ONLY_OPS


def test_emulate_refused_when_the_session_owns_no_tab():
    """No `open` -> `not_owned_tab`, NOT the active-tab fallback every other
    tab-scoped op gets. The fallback is safe for a READ; for `emulate` it would
    resize the tab the OPERATOR is looking at."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _cmd(srv, {"op": "emulate", "device": "iphone-15"}, session="A")
        assert st == 409
        assert body["error"] == "not_owned_tab"
        # It never reached the browser — nothing was emulated.
        assert all(c["op"] != "emulate" for c in ext.dispatched)
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_emulate_refused_when_tab_override_points_at_someone_elses_tab():
    """THE reach-around: the session DOES own a tab, but `--tab` names another.
    Without this check an agent could emulate any tab id it can guess, including
    the operator's."""
    srv, reg = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        assert reg.owners_snapshot() == {("only", "A"): 101}
        st, body = _req(srv, "POST", "/cmd",
                        {"op": "emulate", "device": "iphone-15", "tab": 999},
                        headers={S.HDR_SESSION_ID: "A"})
        assert st == 409
        assert body["error"] == "not_owned_tab"
        assert all(c["op"] != "emulate" for c in ext.dispatched)
        # A plain read with the same override is STILL allowed — the gate is
        # scoped to OWNED_TAB_ONLY_OPS, it did not tighten everything.
        st, body = _req(srv, "POST", "/cmd", {"op": "getHtml", "tab": 999},
                        headers={S.HDR_SESSION_ID: "A"})
        assert st == 200 and body["result"]["data"]["tabId"] == 999
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_emulate_refused_for_a_session_with_no_session_id_at_all():
    """No X-Session-Id -> owns nothing -> refused. A raw token-holder cannot skip
    the gate by simply omitting the routing header."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        st, body = _req(srv, "POST", "/cmd", {"op": "emulate", "device": "iphone-15"})
        assert st == 409
        assert body["error"] == "not_owned_tab"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_emulate_on_the_owned_tab_is_dispatched_with_that_tabId():
    srv, reg = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        st, body = _cmd(srv, {"op": "emulate", "device": "iphone-15"}, session="A")
        assert st == 200
        assert body["result"]["data"]["tabId"] == 101
        emu = [c for c in ext.dispatched if c["op"] == "emulate"]
        assert len(emu) == 1
        assert emu[0]["tabId"] == 101
        # The device field is forwarded verbatim — the extension is the single
        # authority on validating it (one rule, one place).
        assert emu[0]["device"] == "iphone-15"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_emulate_with_an_explicit_tab_equal_to_the_owned_tab_is_allowed():
    """`--tab <my own tab>` is the same tab, so it passes. The gate is 'not
    yours', not 'never use --tab'."""
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        st, body = _req(srv, "POST", "/cmd",
                        {"op": "emulate", "device": "iphone-15", "tab": 101},
                        headers={S.HDR_SESSION_ID: "A"})
        assert st == 200
        assert body["result"]["data"]["tabId"] == 101
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_emulate_telemetry_is_metadata_only(telemetry):
    """device/viewport are fine; the UA string and the geolocation COORDINATES
    are not. An emulated lat/lon is a place the operator chose to pretend to be
    — its presence is worth recording, its value is not the bridge's business."""
    spool_dir = telemetry
    srv, _ = _serve()
    ext = FakeExtension(srv, instance_id="solo", label="only",
                        executor=_tab_echo_exec([101]))
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        _cmd(srv, {"op": "open"}, session="A")
        _wait_events(spool_dir, 1)
        st, _ = _cmd(srv, {"op": "emulate", "device": "pixel-8",
                           "width": 412, "height": 915, "mobile": True,
                           "touch": True,
                           "ua": "Mozilla/5.0 (Linux; Android 14; SECRETMODEL)",
                           "geo": {"latitude": 51.5074, "longitude": -0.1278}},
                      session="A")
        assert st == 200
        evs = _wait_events(spool_dir, 2)
        e = [x for x in evs if json.loads(x["payload"])["op"] == "emulate"][0]
        p = json.loads(e["payload"])
        assert p["emu_device"] == "pixel-8"
        assert p["emu_width"] == 412 and p["emu_height"] == 915
        assert p["emu_mobile"] is True and p["emu_touch"] is True
        assert p["emu_geo"] is True, "the PRESENCE of a geo override is recorded"
        blob = json.dumps(p)
        assert "SECRETMODEL" not in blob, "the UA string must never be emitted"
        assert "51.5074" not in blob and "-0.1278" not in blob, \
            "emulated coordinates must never be emitted"
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


def test_emulate_reset_telemetry_says_reset_and_nothing_else():
    assert S._emulate_extra({"reset": True}) == {"emu_reset": True}  # noqa: SLF001
    # A raw (preset-less) emulation is labelled "raw", never left absent.
    out = S._emulate_extra({"width": 390, "height": 844})            # noqa: SLF001
    assert out["emu_device"] == "raw"
    assert out["emu_geo"] is False
    # A non-dict body cannot raise (telemetry is best-effort by contract).
    assert S._emulate_extra(None) == {}                              # noqa: SLF001


def test_not_owned_tab_is_distinct_from_no_owned_tab():
    """Two different refusals with two different remedies. Collapsing them would
    tell an operator to run `browser open` when the real problem is that they
    pointed --tab at their own window."""
    assert S.NotOwnedTab is not S.NoOwnedTab
    assert not issubclass(S.NotOwnedTab, S.NoOwnedTab)
    assert not issubclass(S.NoOwnedTab, S.NotOwnedTab)


def test_poll_budget_warning_still_claims_extension_0_4_0_plus():
    """#248's `applies_to_extension: "0.4.0+"` claim must stay accurate after the
    manifest bump to 0.5.0 — 0.5.0 IS 0.4.0+, so the claim holds and the string
    must NOT have been 'helpfully' bumped along with the manifest."""
    manifest = json.loads(
        (Path(__file__).resolve().parents[1] / "extension" / "manifest.json")
        .read_text())
    major, minor, _ = (int(x) for x in manifest["version"].split("."))
    assert (major, minor) >= (0, 4), \
        f"manifest {manifest['version']} no longer satisfies 0.4.0+"


# --------------------------------------------------------------------------- #
# Liveness heartbeat
#
# WHY: browser-bridge only ever emitted on a handled COMMAND, so the source had
# no cadence and its silence was unbounded — which made the activity deadman
# check (scripts/collector/deadman.py) structurally unable to tell "the operator
# has not run a browser task" from "the bridge is down". Measured 2026-08-11:
# laptop/browser-bridge silent 30.9 ACTIVE hours, 2.2x its own worst 14-day lull,
# flagged DEAD while the unit was healthy and simply unused.
#
# The tests below pin the three things that make the heartbeat a fix rather than
# a code change: it EMITS on a cadence, it is NOT counted as operator usage, and
# it cannot take the server down.
# --------------------------------------------------------------------------- #
class _FakeRegistry:
    """Minimal Registry stand-in: just the two attributes the heartbeat reads."""

    def __init__(self, connected=True, instances=1, boom=False):
        self._connected = connected
        self._instances = instances
        self._boom = boom

    @property
    def connected(self):
        if self._boom:
            raise RuntimeError("registry exploded")
        return self._connected

    def snapshot(self):
        if self._boom:
            raise RuntimeError("registry exploded")
        return [{"key": "k%d" % i} for i in range(self._instances)]


def test_heartbeat_emits_a_metadata_only_liveness_event(telemetry):
    """The row the deadman needs: source=browser-bridge, a cadence kind, and a
    payload that says whether the EXTENSION half is connected."""
    spool_dir = telemetry
    assert _read_events(spool_dir) == [], "negative control: 0 before"

    S.emit_heartbeat_event(_FakeRegistry(connected=True, instances=2))

    evs = _wait_events(spool_dir, 1)
    assert len(evs) == 1, f"expected exactly one event, got {evs}"
    e = evs[0]
    assert e["source"] == "browser-bridge"
    assert e["kind"] == "heartbeat"
    assert e["text"] == "heartbeat"
    assert e["exit_code"] == "0"
    p = json.loads(e["payload"])
    assert p == {"op": "heartbeat", "key": "", "outcome": "ok",
                 "connected": True, "instances": 2}, p


def test_heartbeat_reports_a_disconnected_extension(telemetry):
    """The positive control for the field that carries the information: a bridge
    whose extension half is gone must report connected=False, or the heartbeat
    degenerates into 'the process is up' and hides the failure that actually
    happens (a silent extension drop)."""
    spool_dir = telemetry
    S.emit_heartbeat_event(_FakeRegistry(connected=False, instances=0))
    e = _wait_events(spool_dir, 1)[0]
    p = json.loads(e["payload"])
    assert p["connected"] is False and p["instances"] == 0, p


def test_heartbeat_is_not_counted_as_operator_usage(telemetry):
    """🔴 SEAM GUARD — server.py's emit vs adoption-scan.py's usage query.

    Neither module can see the other, and each is 'correct' alone: adoption-scan
    counts source='browser-bridge' AND kind='cmd' to answer "is the browser skill
    actually used". A heartbeat emitted as kind='cmd' would add ~96 machine rows
    a day and report a skill nobody touched as heavily used.

    So this asserts the RELATIONSHIP, against the real spec adoption-scan ships
    with — not a copy of the string. It fails if either side moves.
    """
    import importlib.util
    scan_py = (Path(__file__).resolve().parents[2]
               / "session-analysis" / "adoption-scan.py")
    spec = importlib.util.spec_from_file_location("adoption_scan_seam", scan_py)
    A = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(A)

    specs = [s for s in A.REGISTRY if s.get("source") == "browser-bridge"]
    assert specs, "adoption-scan no longer tracks browser-bridge — re-check this seam"
    counted_kinds = {s.get("kind") for s in specs}
    assert counted_kinds == {"cmd"}, \
        f"adoption-scan's browser-bridge kinds changed to {counted_kinds}"

    spool_dir = telemetry
    S.emit_heartbeat_event(_FakeRegistry())
    # 🔴 SELECTED BY OP, and this site is why the #807 audit called it urgent.
    # With positional indexing a neighbour's `kind="cmd"` row at [0] failed this
    # with "the heartbeat is being counted as operator usage by adoption-scan" —
    # a confident, FALSE diagnosis about a seam that is fine. That is strictly
    # worse than the failure that prompted the fix (`'getHtml' == 'frames'`),
    # which at least names its own confusion.
    e = _wait_ops(spool_dir, "heartbeat", 1)[0]
    assert e["kind"] not in counted_kinds, \
        ("the heartbeat is being counted as operator usage by adoption-scan — "
         f"emitted kind={e['kind']!r}, counted={counted_kinds}")


def test_heartbeat_repeats_on_the_interval(telemetry):
    """The cadence itself: the loop keeps emitting until stopped."""
    calls = []
    stop = threading.Event()

    def _emit(reg):
        calls.append(time.monotonic())
        if len(calls) >= 3:
            stop.set()

    S.run_heartbeat(_FakeRegistry(), 0.01, stop, emit=_emit)
    assert len(calls) >= 3, calls


def test_heartbeat_emits_BEFORE_its_first_sleep():
    """A restart must reset the deadman's silence promptly, so the FIRST emit
    happens before the first sleep — not one interval later.

    🔴 The interval here is deliberately LONG relative to the assertion window.
    An earlier version of this test used 0.01s and a <0.05s bound, which PASSED
    against a mutant that slept first — at a 10ms interval the two orderings are
    indistinguishable. The gap between interval and bound IS the guard: emit-first
    returns in microseconds, sleep-first cannot return in under `interval`.
    """
    interval = 2.0
    bound = 0.5
    assert interval > 2 * bound, "the mutant must not fit inside the bound"
    calls = []
    stop = threading.Event()

    def _emit(reg):
        calls.append(time.monotonic())
        stop.set()          # one emit is all this test needs

    t0 = time.monotonic()
    S.run_heartbeat(_FakeRegistry(), interval, stop, emit=_emit)
    elapsed = time.monotonic() - t0
    assert len(calls) == 1, calls
    assert elapsed < bound, \
        (f"first heartbeat took {elapsed:.2f}s with a {interval}s interval — "
         "the loop is sleeping before its first emit")


def test_run_heartbeat_survives_an_emitter_that_raises():
    """MUTATION-PROOF for the loop's try/except: one bad emit must not kill the
    thread, or a transient spool problem silently ends the cadence forever and
    the deadman then reports the bridge dead for the rest of its uptime."""
    calls = []
    stop = threading.Event()

    def _emit(reg):
        calls.append(1)
        if len(calls) >= 3:
            stop.set()
        raise RuntimeError("spool exploded")

    S.run_heartbeat(_FakeRegistry(), 0.01, stop, emit=_emit)  # must not raise
    assert len(calls) >= 3, f"loop died after {len(calls)} emit(s)"


def test_emit_heartbeat_survives_a_broken_registry(telemetry):
    """A registry that raises must still produce a row: the event's PURPOSE is to
    prove the process is alive, so degrading to connected=False beats emitting
    nothing (which would read as the process being dead)."""
    spool_dir = telemetry
    S.emit_heartbeat_event(_FakeRegistry(boom=True))
    e = _wait_events(spool_dir, 1)[0]
    p = json.loads(e["payload"])
    assert e["kind"] == "heartbeat"
    assert p["connected"] is False and p["instances"] == 0, p


def test_start_heartbeat_disabled_at_zero_interval():
    """The documented escape hatch: BROWSER_BRIDGE_HEARTBEAT_S=0 starts nothing."""
    t, stop = S.start_heartbeat(_FakeRegistry(), 0)
    assert t is None and stop is None


def test_start_heartbeat_runs_and_stops():
    """The thread is real, is a daemon (a stuck heartbeat must never hold up
    shutdown), and stops promptly when the stop event is set."""
    t, stop = S.start_heartbeat(_FakeRegistry(), 0.05)
    try:
        assert t is not None and t.daemon is True
        assert t.is_alive()
    finally:
        stop.set()
        t.join(timeout=2.0)
    assert not t.is_alive(), "heartbeat thread did not stop"


def test_heartbeat_tracks_registry_liveness_across_the_stale_boundary(telemetry):
    """Structural: the heartbeat must read the SAME liveness definition /health
    reports (Registry.connected), not a second one that can disagree.

    Asserting only the connected=True side is a SPELLED guard, not a structural
    one: a divergent definition that ignores staleness (e.g. `_instances or
    connected`) satisfies it and survives. So this drives a REAL Registry across
    the CONNECT_STALE_S boundary with an injected clock and pins BOTH sides —
    the transition is the part a second definition cannot fake.
    """
    spool_dir = telemetry
    clock = [1000.0]
    reg = S.Registry(clock=lambda: clock[0])
    # wait_timeout=0, NOT the usual _register_live(0.01): poll() derives its
    # deadline from the INJECTED clock, so under a frozen clock any positive
    # timeout spins forever. Zero registers the instance and returns immediately.
    assert reg.poll("hb-1", "hb", 0) is None

    S.emit_heartbeat_event(reg)
    p = json.loads(_wait_events(spool_dir, 1)[0]["payload"])
    assert p["connected"] is True and p["instances"] == 1, p
    assert reg.connected is True, "fixture precondition"

    clock[0] += S.CONNECT_STALE_S + 1        # the instance goes stale
    assert reg.connected is False, "fixture precondition"
    S.emit_heartbeat_event(reg)
    p2 = json.loads(_wait_events(spool_dir, 2)[1]["payload"])
    assert p2["connected"] is False and p2["instances"] == 0, p2


def test_heartbeat_interval_keeps_the_deadman_budget_on_the_floor(telemetry):
    """🔴 SEAM GUARD — the emitter's interval vs the consumer's bucket size.

    HEARTBEAT_INTERVAL_S is the one load-bearing number in this feature, and
    nothing in server.py can tell whether it is still small enough to do its job:
    that is decided by deadman.py's BUCKET_SECONDS and FLOOR_BUCKETS, in another
    tree. Left unpinned, `900` -> `90000` is a silent no-op — the heartbeat keeps
    emitting, every test stays green, and the source goes back to being
    undetectable.

    So this imports the real consumer and asserts the RELATIONSHIP: the cadence
    must be frequent enough that a live bridge's worst gap stays far under the
    budget floor. It also pins the >0 default, because 0 disables the feature
    entirely.
    """
    import importlib.util
    dm_py = (Path(__file__).resolve().parents[2] / "collector" / "deadman.py")
    spec = importlib.util.spec_from_file_location("deadman_seam", dm_py)
    DM = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(DM)

    assert S.HEARTBEAT_INTERVAL_S > 0, \
        "the default must not disable the feature"
    buckets_per_beat = S.HEARTBEAT_INTERVAL_S / DM.BUCKET_SECONDS
    assert buckets_per_beat <= DM.FLOOR_BUCKETS / 4, (
        f"heartbeat every {buckets_per_beat:.1f} deadman buckets is too sparse "
        f"against a {DM.FLOOR_BUCKETS}-bucket floor — the budget will no longer "
        "sit on the floor and the source becomes undetectable again")

    # And the pair is actually declared machine-cadence downstream, or the
    # heartbeat inflates every other source's active time (see MACHINE_CADENCE).
    assert ("browser-bridge", "heartbeat") in DM.MACHINE_CADENCE, \
        "deadman no longer treats the heartbeat as machine-cadence"


def test_main_actually_starts_and_stops_the_heartbeat(monkeypatch, tmp_path):
    """🔴 The wiring, not just the parts. Every other test here exercises
    run_heartbeat/start_heartbeat directly, so `start_heartbeat(registry)` could
    be deleted from main() and the whole suite stays green while the feature is
    completely inert on both hosts.

    Drives the real main() with a stub server whose serve_forever returns, and
    asserts the thread was started with the real registry AND stopped on the way
    out (a leaked heartbeat would keep emitting after a shutdown).
    """
    started, stopped, served = [], [], []

    class _StubServer:
        def serve_forever(self):
            return None

        def server_close(self):
            return None

    class _StubStop:
        def set(self):
            stopped.append(True)

    def _fake_build(host, port, registry, *a, **kw):
        served.append(registry)      # capture WHICH registry the server got
        return _StubServer()

    def _fake_start(registry, interval=None):
        started.append(registry)
        return object(), _StubStop()

    monkeypatch.setenv("BROWSER_BRIDGE_TOKEN_FILE", str(tmp_path / "token"))
    monkeypatch.setattr(S, "build_server", _fake_build)
    monkeypatch.setattr(S, "start_heartbeat", _fake_start)

    assert S.main([]) == 0
    assert len(started) == 1, "main() did not start the heartbeat"
    # IDENTITY, not isinstance: `start_heartbeat(Registry())` — a fresh registry
    # instead of the server's — passes a type check while making the heartbeat
    # report connected=False / instances=0 forever.
    assert started[0] is served[0], \
        "the heartbeat was given a different Registry than the server's"
    assert stopped == [True], "main() did not stop the heartbeat on shutdown"
