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
    # complete / dismissed / unknown / missing must NOT block a dispatch (fail-open)
    assert tasks.is_open(_task(status="complete")) is False
    assert tasks.is_open(_task(status="dismissed")) is False
    assert tasks.is_open(_task(status="")) is False
    assert tasks.is_open({}) is False
    assert tasks.is_open(None) is False


def test_open_task_count_counts_only_live_work():
    views = [tasks.task_view(_task(1, status="open")),
             tasks.task_view(_task(2, status="in_progress")),
             tasks.task_view(_task(3, status="complete")),
             tasks.task_view(_task(4, status="dismissed"))]
    assert tasks.open_task_count(views) == 2
    assert tasks.open_task_count([]) == 0
    assert tasks.open_task_count(None) == 0


# --- fetch: the degradation matrix ----------------------------------------- #
def _getter(body):
    def get(url, token, *, timeout=None):
        get.calls.append((url, token, timeout))
        return body
    get.calls = []
    return get


def test_fetch_tasks_happy_path_hits_api_tasks_with_the_bearer_token():
    get = _getter(json.dumps([_task(1, ["initiative:a"])]))
    got = tasks.fetch_tasks(creds=CREDS, env={}, getter=get)
    assert [t["id"] for t in got] == [1]
    url, token, timeout = get.calls[0]
    assert url == "http://cg.test:30302/api/tasks"
    assert token == "tok"
    assert timeout == tasks.FETCH_TIMEOUT and tasks.FETCH_TIMEOUT <= 5   # SHORT, on the render path


def test_fetch_tasks_missing_creds_yields_empty_without_calling_out(capsys):
    get = _getter("[]")
    assert tasks.fetch_tasks(creds={}, env={}, getter=get) == []
    assert get.calls == []                       # never even attempted a request
    assert "CLAWGATE_HOOK_TOKEN" in capsys.readouterr().err


def test_fetch_tasks_connection_error_yields_empty():
    def boom(url, token, *, timeout=None):
        raise OSError("Connection refused")
    assert tasks.fetch_tasks(creds=CREDS, env={}, getter=boom) == []


def test_fetch_tasks_non_200_yields_empty():
    def http_err(url, token, *, timeout=None):
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

    def fetch(*, creds=None, env=None, timeout=None):
        calls.append(creds)
        return [_task(1, ["initiative:alpha"]), _task(2, ["initiative:alpha"]), _task(3)]

    got = tasks.linked_tasks_map(creds=CREDS, env={}, fetcher=fetch)
    assert len(calls) == 1                                # ONE read, not one per card
    assert list(got) == ["alpha"]
    assert [t["id"] for t in got["alpha"]] == [1, 2]
    assert got["alpha"][0]["url"] == "http://cg.test:30302/tasks#task-1"


def test_linked_tasks_map_returns_empty_map_on_any_failure():
    def boom(*, creds=None, env=None, timeout=None):
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
