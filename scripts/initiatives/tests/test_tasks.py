"""Unit tests for `tasks.py` — the CONSUMER of repo-cos's `initiative:<slug>` clawgate tags.

Offline: no network, no clawgate, no creds file. The HTTP read is exercised through the
injected `getter`, so every degradation path (connection error, non-200, malformed JSON,
missing creds, a rolled-back clawgate with no `tags` field) is asserted deterministically.
Mirrors test_dispatch.py's discipline — this module's whole contract is BEST-EFFORT +
NEVER-RAISES, so the tests are mostly about what it returns when things are broken."""
import json
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tasks  # noqa: E402


def _task(tid=1, tags=None, status="open", directory="d", title="", **over):
    t = {"id": tid, "directory": directory, "title": title, "status": status}
    if tags is not None:
        t["tags"] = tags
    t.update(over)
    return t


CREDS = {"CLAWGATE_API_URL": "http://cg.test:30302", "CLAWGATE_HOOK_TOKEN": "tok"}


# --- base URL config -------------------------------------------------------- #
def test_base_url_defaults_to_the_lan_nodeport():
    assert tasks.clawgate_base_url({}, {}) == "http://192.168.50.250:30302"
    assert tasks.DEFAULT_CLAWGATE_URL == "http://192.168.50.250:30302"


def test_base_url_env_overrides_creds_and_default():
    env = {tasks.CLAWGATE_URL_ENV: "http://elsewhere:9/"}
    assert tasks.clawgate_base_url(CREDS, env) == "http://elsewhere:9"
    # creds fill in when the env knob is absent/blank (reader + writer agree by default)
    assert tasks.clawgate_base_url(CREDS, {}) == "http://cg.test:30302"
    assert tasks.clawgate_base_url(CREDS, {tasks.CLAWGATE_URL_ENV: "  "}) == "http://cg.test:30302"


def test_base_url_never_raises_on_garbage():
    assert tasks.clawgate_base_url(None, None) is not None
    assert tasks.clawgate_base_url("not-a-dict", {}) == tasks.DEFAULT_CLAWGATE_URL


# --- tag parsing: EXACT match only ------------------------------------------ #
def test_initiative_slugs_extracts_the_reserved_namespace_only():
    assert tasks.initiative_slugs(_task(tags=["initiative:remix-platform"])) == ["remix-platform"]
    # a task with SEVERAL tags → only the initiative: ones, order preserved, de-duped
    assert tasks.initiative_slugs(_task(tags=[
        "runbook:deploy", "initiative:alpha", "civitai:frontend", "initiative:beta",
        "initiative:alpha"])) == ["alpha", "beta"]


def test_initiative_slugs_no_tag_or_no_tags_field_yields_nothing():
    assert tasks.initiative_slugs(_task(tags=["runbook:deploy", "gate:needs-decision"])) == []
    assert tasks.initiative_slugs(_task(tags=[])) == []
    # a ROLLED-BACK clawgate has no `tags` key at all → [] (not a KeyError)
    assert tasks.initiative_slugs(_task()) == []
    # and a non-list / non-string / empty-value tag is skipped, never raised on
    assert tasks.initiative_slugs(_task(tags="initiative:x")) == []
    assert tasks.initiative_slugs(_task(tags=[None, 7, "initiative:"])) == []
    assert tasks.initiative_slugs(None) == []


def test_initiative_slugs_is_case_sensitive_and_not_a_prefix_match():
    # EXACT match only: the tag value is the key, verbatim. `initiative:foo` must never
    # satisfy `Foo` or `foo-bar` — the join is a dict lookup on this exact string.
    assert tasks.initiative_slugs(_task(tags=["initiative:foo"])) == ["foo"]
    assert tasks.initiative_slugs(_task(tags=["initiative:Foo"])) == ["Foo"]
    assert "foo" not in tasks.group_by_slug([_task(tags=["initiative:Foo"])])
    assert "Foo" not in tasks.group_by_slug([_task(tags=["initiative:foo"])])
    assert "foo" not in tasks.group_by_slug([_task(tags=["initiative:foo-bar"])])
    assert "foo-bar" not in tasks.group_by_slug([_task(tags=["initiative:foo"])])
    # a differently-cased NAMESPACE isn't the reserved one either
    assert tasks.initiative_slugs(_task(tags=["Initiative:foo"])) == []


# --- grouping --------------------------------------------------------------- #
def test_group_by_slug_builds_the_slug_to_tasks_map():
    got = tasks.group_by_slug([
        _task(1, ["initiative:alpha"], directory="one"),
        _task(2, ["initiative:beta"], directory="two"),
    ])
    assert sorted(got) == ["alpha", "beta"]
    assert [t["id"] for t in got["alpha"]] == [1]
    assert got["alpha"][0]["title"] == "one"


def test_group_by_slug_two_tasks_on_one_initiative_accumulate():
    got = tasks.group_by_slug([
        _task(1, ["initiative:alpha"]), _task(2, ["initiative:alpha"]),
    ])
    assert list(got) == ["alpha"]
    assert [t["id"] for t in got["alpha"]] == [1, 2]   # input order preserved


def test_group_by_slug_multi_tagged_task_lands_under_each_slug():
    got = tasks.group_by_slug([_task(9, ["initiative:alpha", "initiative:beta", "urgent"])])
    assert [t["id"] for t in got["alpha"]] == [9]
    assert [t["id"] for t in got["beta"]] == [9]


def test_group_by_slug_untagged_task_lands_nowhere():
    assert tasks.group_by_slug([_task(1), _task(2, ["runbook:x"])]) == {}


def test_group_by_slug_unknown_slug_is_just_an_unused_key():
    # a tag naming an initiative that has no card is harmless — the viewer looks cards UP in
    # this map, never the reverse, so the entry is simply never read.
    got = tasks.group_by_slug([_task(1, ["initiative:no-such-card"])])
    assert list(got) == ["no-such-card"]
    assert got.get("initiatives-viewer") is None


def test_group_by_slug_survives_garbage_input():
    assert tasks.group_by_slug(None) == {}
    assert tasks.group_by_slug("nope") == {}
    assert tasks.group_by_slug([None, 3, _task(1, ["initiative:a"])]) == {
        "a": [tasks.task_view(_task(1, ["initiative:a"]))]}


# --- task view -------------------------------------------------------------- #
def test_task_view_shape_and_clawgate_deep_link():
    v = tasks.task_view(_task(42, ["initiative:a"], status="in_progress", directory="do the thing"),
                        "http://cg.test:30302/")
    assert v == {"id": 42, "title": "do the thing", "status": "in_progress", "open": True,
                 "url": "http://cg.test:30302/tasks#task-42"}


def test_task_view_prefers_title_over_directory_when_present():
    v = tasks.task_view(_task(1, title="real title", directory="label"))
    assert v["title"] == "real title"
    # …and falls back to `directory`, which is how every current producer smuggles it (§9)
    assert tasks.task_view(_task(1, directory="label"))["title"] == "label"


def test_task_view_no_base_or_no_id_yields_no_url():
    assert tasks.task_view(_task(1))["url"] == ""
    assert tasks.task_view({"status": "open"}, "http://cg.test")["url"] == ""
    assert tasks.task_view({"id": True}, "http://cg.test")["id"] is None   # bool is not an id


# --- open/closed statuses (the dispatch guard's input) ---------------------- #
def test_is_open_is_a_closed_set_of_live_statuses():
    assert tasks.is_open(_task(status="open")) is True
    assert tasks.is_open(_task(status="in_progress")) is True
    assert tasks.is_open(_task(status="ready_for_review")) is True
    # complete / a future-renamed status / unknown / missing must NOT block a dispatch.
    # clawgate's vocabulary is exactly {open, in_progress, ready_for_review, complete}
    # (internal/notes/notes.go), so `retired_by_a_later_clawgate` stands in for "a status
    # this code has never heard of" — OPEN_STATUSES is an allow-list, so it fails OPEN.
    assert tasks.is_open(_task(status="complete")) is False
    assert tasks.is_open(_task(status="retired_by_a_later_clawgate")) is False
    assert tasks.is_open(_task(status="")) is False
    assert tasks.is_open({}) is False
    assert tasks.is_open(None) is False


def test_open_task_count_counts_only_live_work():
    views = [tasks.task_view(_task(1, status="open")),
             tasks.task_view(_task(2, status="in_progress")),
             tasks.task_view(_task(3, status="complete")),
             tasks.task_view(_task(4, status="retired_by_a_later_clawgate"))]
    assert tasks.open_task_count(views) == 2
    assert tasks.open_task_count([]) == 0
    assert tasks.open_task_count(None) == 0


# --- fetch: the degradation matrix ----------------------------------------- #
def _getter(body):
    def get(url, token, *, deadline=None):
        get.calls.append((url, token, deadline))
        return body
    get.calls = []
    return get


def test_fetch_tasks_happy_path_hits_api_tasks_with_the_bearer_token():
    get = _getter(json.dumps([_task(1, ["initiative:a"])]))
    got = tasks.fetch_tasks(creds=CREDS, env={}, getter=get)
    assert [t["id"] for t in got] == [1]
    url, token, deadline = get.calls[0]
    assert url == "http://cg.test:30302/api/tasks"
    assert token == "tok"
    # SHORT wall-clock deadline — this is a decoration on the render path, not core data
    assert deadline == tasks.FETCH_DEADLINE and tasks.FETCH_DEADLINE <= 5


def test_fetch_tasks_missing_creds_yields_empty_without_calling_out(capsys):
    get = _getter("[]")
    assert tasks.fetch_tasks(creds={}, env={}, getter=get) == []
    assert get.calls == []                       # never even attempted a request
    assert "CLAWGATE_HOOK_TOKEN" in capsys.readouterr().err


def test_fetch_tasks_connection_error_yields_empty():
    def boom(url, token, *, deadline=None):
        raise OSError("Connection refused")
    assert tasks.fetch_tasks(creds=CREDS, env={}, getter=boom) == []


def test_fetch_tasks_non_200_yields_empty():
    def http_err(url, token, *, deadline=None):
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)
    assert tasks.fetch_tasks(creds=CREDS, env={}, getter=http_err) == []


def test_fetch_tasks_malformed_json_yields_empty():
    assert tasks.fetch_tasks(creds=CREDS, env={}, getter=_getter("{not json")) == []
    # a well-formed but non-array body is refused too (never a crash)
    assert tasks.fetch_tasks(creds=CREDS, env={}, getter=_getter('{"error":"nope"}')) == []


def test_fetch_tasks_skips_non_dict_entries():
    got = tasks.fetch_tasks(creds=CREDS, env={}, getter=_getter('[1, null, {"id": 5}]'))
    assert got == [{"id": 5}]


# --- orchestrator ----------------------------------------------------------- #
def test_linked_tasks_map_fetches_once_then_groups():
    calls = []

    def fetch(*, creds=None, env=None, deadline=None):
        calls.append(creds)
        return [_task(1, ["initiative:alpha"]), _task(2, ["initiative:alpha"]), _task(3)]

    got = tasks.linked_tasks_map(creds=CREDS, env={}, fetcher=fetch)
    assert len(calls) == 1                                # ONE read, not one per card
    assert list(got) == ["alpha"]
    assert [t["id"] for t in got["alpha"]] == [1, 2]
    assert got["alpha"][0]["url"] == "http://cg.test:30302/tasks#task-1"


def test_linked_tasks_map_returns_empty_map_on_any_failure():
    def boom(*, creds=None, env=None, deadline=None):
        raise RuntimeError("clawgate exploded")
    assert tasks.linked_tasks_map(creds=CREDS, env={}, fetcher=boom) == {}
    # …and a fetcher that merely returns nothing (unreachable clawgate) is an empty map too
    assert tasks.linked_tasks_map(
        creds=CREDS, env={}, fetcher=lambda **kw: []) == {}


def test_linked_tasks_map_no_tags_field_anywhere_is_an_empty_map():
    # a rolled-back clawgate omits `tags` on every task → nothing joins, nothing breaks
    assert tasks.linked_tasks_map(
        creds=CREDS, env={},
        fetcher=lambda **kw: [_task(1), _task(2), _task(3)]) == {}


# --- ok flag: a FAILED read is distinguishable from an empty queue ----------- #
def test_fetch_tasks_result_separates_failure_from_an_empty_queue():
    # clawgate answered with an empty queue → SUCCESS (nothing to serve stale).
    assert tasks.fetch_tasks_result(creds=CREDS, env={}, getter=_getter("[]")) == (True, [])
    # every failure shape is ok=False — which is what lets the cache serve the last good map.
    def boom(url, token, *, deadline=None):
        raise OSError("Connection refused")
    assert tasks.fetch_tasks_result(creds=CREDS, env={}, getter=boom) == (False, [])
    assert tasks.fetch_tasks_result(creds=CREDS, env={}, getter=_getter("{not json")) == (False, [])
    assert tasks.fetch_tasks_result(creds={}, env={}, getter=_getter("[]")) == (False, [])


# --- 🔴 THE WALL-CLOCK DEADLINE, against REAL sockets ------------------------ #
# The bug this replaces shipped because the only latency test was "connection refused is
# fast" (+0.010s). A refused connection exercises NONE of the failure modes that matter:
# `urlopen(timeout=)` is per-socket-OPERATION, so a peer that accepts and never replies cost
# the full timeout on EVERY render (+4.005s on a 0.80s baseline), and a peer that dribbles a
# byte just inside the timeout was UNBOUNDED (+130s measured — and it still succeeded).
# These two tests stand up real sockets that behave exactly that way.
def _serve(handler):
    """Start a one-connection TCP server on an ephemeral port; returns (url, stop)."""
    import socket, threading
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def loop():
        srv.settimeout(0.25)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                continue
            threading.Thread(target=handler, args=(conn, stop), daemon=True).start()
        try:
            srv.close()
        except OSError:
            pass

    th = threading.Thread(target=loop, daemon=True)
    th.start()
    return f"http://127.0.0.1:{port}", stop.set


def _blackhole(conn, stop):
    """Accept the connection, read the request, and then say NOTHING, ever."""
    try:
        conn.recv(4096)
        while not stop.is_set():
            stop.wait(0.1)
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _slow_drip(conn, stop):
    """Reply one byte at a time, slowly — each individual send is well inside any per-socket
    timeout, so this is the shape that defeated `urlopen(timeout=)` entirely."""
    try:
        conn.recv(4096)
        body = json.dumps([_task(1, ["initiative:alpha"])])
        raw = (f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n"
               f"Content-Type: application/json\r\n\r\n{body}").encode()
        for byte in raw:
            if stop.is_set():
                return
            try:
                conn.sendall(bytes([byte]))
            except OSError:
                return
            stop.wait(0.25)          # 1 byte / 250ms — always inside the socket timeout
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _timed_fetch(url, *, deadline):
    import time
    creds = {"CLAWGATE_API_URL": url, "CLAWGATE_HOOK_TOKEN": "tok"}
    t0 = time.monotonic()
    got = tasks.fetch_tasks(creds=creds, env={}, deadline=deadline)
    return got, time.monotonic() - t0


def test_blackholed_clawgate_costs_at_most_the_deadline():
    url, stop = _serve(_blackhole)
    try:
        got, elapsed = _timed_fetch(url, deadline=1.0)
    finally:
        stop()
    assert got == []                        # degrades to "no linked tasks"
    assert elapsed < 2.0, f"blackhole added {elapsed:.3f}s for a 1.0s deadline"


def test_slow_drip_clawgate_costs_at_most_the_deadline():
    # 1 byte / 250ms over a ~120-byte response is ~30s of dribble; every individual read is
    # inside the socket timeout, so ONLY a wall-clock deadline can bound this.
    url, stop = _serve(_slow_drip)
    try:
        got, elapsed = _timed_fetch(url, deadline=1.0)
    finally:
        stop()
    assert got == []
    assert elapsed < 2.0, f"slow-drip added {elapsed:.3f}s for a 1.0s deadline"


def test_run_with_deadline_bounds_any_callable_and_reraises():
    import time
    t0 = time.monotonic()
    try:
        tasks._run_with_deadline(lambda: time.sleep(30), 0.3)
    except TimeoutError:
        pass
    else:
        raise AssertionError("a 30s callable must not satisfy a 0.3s deadline")
    assert time.monotonic() - t0 < 1.5
    # a fast callable returns its value; a raising one re-raises verbatim
    assert tasks._run_with_deadline(lambda: 42, 5) == 42
    try:
        tasks._run_with_deadline(lambda: (_ for _ in ()).throw(ValueError("nope")), 5)
    except ValueError as exc:
        assert "nope" in str(exc)
    else:
        raise AssertionError("the worker's exception must reach the caller")


# --- response SIZE CAP ------------------------------------------------------- #
def test_oversized_response_is_refused_rather_than_buffered():
    # `Notes.List` has no LIMIT and rows carry full task bodies, so the payload grows with
    # retained history. A body past the cap is a failed read, not an unbounded read.
    huge = "[" + ",".join('{"id": %d}' % i for i in range(4000)) + "]"
    assert len(huge) > 1000

    def big(url, token, *, deadline=None):
        raise OSError(f"response exceeded the {tasks.MAX_RESPONSE_BYTES}-byte cap")

    assert tasks.fetch_tasks_result(creds=CREDS, env={}, getter=big) == (False, [])
    assert tasks.MAX_RESPONSE_BYTES <= (4 << 20)     # bounded, not "some big number"


def test_real_get_caps_the_response_body():
    def flood(conn, stop):
        try:
            conn.recv(4096)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n")
            blob = b"x" * 65536
            while not stop.is_set():
                try:
                    conn.sendall(blob)
                except OSError:
                    return
        finally:
            try:
                conn.close()
            except OSError:
                pass

    url, stop = _serve(flood)
    try:
        try:
            tasks._get(f"{url}/api/tasks", "tok", deadline=5, max_bytes=4096)
        except OSError as exc:
            assert "cap" in str(exc)
        else:
            raise AssertionError("an unbounded body must not be buffered whole")
    finally:
        stop()


# --- the linked-task CACHE: TTL, serve-stale, single-flight ------------------ #
class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_cache_serves_within_its_ttl_without_refetching():
    clock = _Clock()
    cache = tasks.LinkedTaskCache(ttl=30.0, now=clock)
    calls = []

    def loader():
        calls.append(1)
        return True, {"alpha": [{"id": 1}]}

    assert cache.get(loader) == {"alpha": [{"id": 1}]}
    clock.t += 29.0
    assert cache.get(loader) == {"alpha": [{"id": 1}]}
    assert len(calls) == 1                    # still inside the TTL → no second clawgate read
    clock.t += 2.0
    cache.get(loader)
    assert len(calls) == 2                    # past it → exactly one refresh


def test_cache_serves_the_LAST_GOOD_map_when_a_refresh_fails(capsys):
    # 🔴 the whole point: a transient blip must not make linked tasks — and with them the
    # dispatch guard — flicker away. A failure keeps the previous map.
    clock = _Clock()
    cache = tasks.LinkedTaskCache(ttl=10.0, now=clock)
    good = {"alpha": [{"id": 1, "open": True}]}
    state = {"ok": True}

    def loader():
        return (True, good) if state["ok"] else (False, {})

    assert cache.get(loader) == good
    state["ok"] = False
    clock.t += 11.0
    assert cache.get(loader) == good          # STALE, not empty
    assert "stale" in capsys.readouterr().err
    # and it recovers when clawgate does
    state["ok"] = True
    clock.t += 11.0
    assert cache.get(loader) == good


def test_cache_backs_off_after_a_failure_instead_of_retrying_every_call():
    clock = _Clock()
    cache = tasks.LinkedTaskCache(ttl=10.0, now=clock)
    calls = []

    def loader():
        calls.append(1)
        return False, {}

    assert cache.get(loader) == {}            # never succeeded → empty, exactly today's board
    assert cache.get(loader) == {}
    assert cache.get(loader) == {}
    assert len(calls) == 1                    # a hung clawgate is retried once per TTL, not per request


def test_cache_never_raises_when_the_loader_explodes():
    cache = tasks.LinkedTaskCache(ttl=10.0, now=_Clock())

    def boom():
        raise RuntimeError("clawgate exploded")

    assert cache.get(boom) == {}


def test_cache_is_single_flight_so_a_slow_read_never_queues_callers():
    # A concurrent caller must get the current value IMMEDIATELY rather than blocking behind a
    # slow clawgate — the other half of moving the read out of DataProvider._lock.
    import threading, time
    cache = tasks.LinkedTaskCache(ttl=0.0)          # always "stale" → always tries to refresh
    entered = threading.Event()
    release = threading.Event()

    def slow():
        entered.set()
        release.wait(5)
        return True, {"alpha": []}

    th = threading.Thread(target=lambda: cache.get(slow), daemon=True)
    th.start()
    assert entered.wait(5)
    t0 = time.monotonic()
    assert cache.get(lambda: (True, {"never": []})) == {}   # returns at once with what it has
    assert time.monotonic() - t0 < 0.5
    release.set()
    th.join(5)


def test_cached_linked_tasks_map_uses_the_cache_and_never_raises():
    cache = tasks.LinkedTaskCache(ttl=60.0, now=_Clock())
    calls = []

    def fetcher(*, creds=None, env=None, deadline=None):
        calls.append(1)
        return True, [_task(1, ["initiative:alpha"])]

    got = tasks.cached_linked_tasks_map(creds=CREDS, env={}, fetcher=fetcher, cache=cache)
    assert list(got) == ["alpha"]
    tasks.cached_linked_tasks_map(creds=CREDS, env={}, fetcher=fetcher, cache=cache)
    assert len(calls) == 1                    # second render served from the cache

    def boom(*, creds=None, env=None, deadline=None):
        raise RuntimeError("nope")

    cache.invalidate()
    assert tasks.cached_linked_tasks_map(creds=CREDS, env={}, fetcher=boom, cache=cache) == {}
