"""Unit tests for the clawgate DISPATCH module (scripts/initiatives/dispatch.py).

Hermetic: no network, no real git, no clawgate. `post_task`/`_git_remote` are injected
(`_post`, `_run`) so nothing shells out or hits the network; `dispatch_initiative` takes an
injectable `poster`. Mirrors the robustness contract of repo-cos/clawgate.py (best-effort,
never raises, returns id|None) — these tests pin that contract for the initiative variant.
"""
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dispatch  # noqa: E402


def _view(**over):
    # `repo` is a FIXTURE path (not a real checkout): `dispatch_initiative`'s internal
    # `resolve_repo_fullname` runs `git -C <repo>` which fails fast → "" (no real remote read),
    # keeping these tests hermetic. `directory`/body use `repo_name` (a view field), not git.
    v = {
        "slug": "emerging-thing", "repo": "/repo/does-not-exist-devrc", "repo_name": "devrc",
        "title": "Emerging thing", "momentum": "active", "age": "2h",
        "status": "", "next_step": "", "open_prs": [], "open_investigations": [],
        "face_message": None, "current_doc": "",
    }
    v.update(over)
    return v


# --- load_creds ------------------------------------------------------------- #
def test_load_creds_parses_keyvalue(tmp_path):
    p = tmp_path / "clawgate.env"
    p.write_text('# comment\nCLAWGATE_API_URL="https://cg.example"\n'
                 "CLAWGATE_HOOK_TOKEN=tok123\n\nJUNK\n")
    creds = dispatch.load_creds(p)
    assert creds["CLAWGATE_API_URL"] == "https://cg.example"
    assert creds["CLAWGATE_HOOK_TOKEN"] == "tok123"


def test_load_creds_missing_file_is_empty(tmp_path):
    assert dispatch.load_creds(tmp_path / "nope.env") == {}


# --- _parse_github_fullname ------------------------------------------------- #
def test_parse_github_fullname_variants():
    assert dispatch._parse_github_fullname("git@github.com:Owner/repo.git") == "Owner/repo"
    assert dispatch._parse_github_fullname("https://github.com/Owner/repo.git") == "Owner/repo"
    assert dispatch._parse_github_fullname("https://github.com/Owner/repo") == "Owner/repo"
    assert dispatch._parse_github_fullname("ssh://git@github.com/Owner/repo.git") == "Owner/repo"


def test_parse_github_fullname_rejects_non_github():
    assert dispatch._parse_github_fullname("https://gitlab.com/o/n.git") == ""
    assert dispatch._parse_github_fullname("") == ""
    assert dispatch._parse_github_fullname("git@github.com:only-one-part") == ""


# --- resolve_repo_fullname (injected git) ----------------------------------- #
def test_resolve_repo_fullname_from_injected_remote():
    calls = []

    def fake_run(path):
        calls.append(path)
        return "git@github.com:zach/devrc.git"

    assert dispatch.resolve_repo_fullname("/home/zach/workspace/devrc", _run=fake_run) == "zach/devrc"
    assert calls == ["/home/zach/workspace/devrc"]  # runs git on the path directly


def test_resolve_repo_fullname_empty_path():
    assert dispatch.resolve_repo_fullname("", _run=lambda p: "x") == ""


def test_resolve_repo_fullname_unresolvable_remote_is_empty():
    assert dispatch.resolve_repo_fullname("/repo/x", _run=lambda p: "") == ""
    assert dispatch.resolve_repo_fullname("/repo/x", _run=lambda p: "not-a-remote") == ""


def test_resolve_repo_fullname_never_raises():
    def boom(path):
        raise RuntimeError("git blew up")

    assert dispatch.resolve_repo_fullname("/repo/x", _run=boom) == ""


# --- build_task_title ------------------------------------------------------- #
def test_build_task_title_repo_and_slug():
    assert dispatch.build_task_title(_view()) == "devrc · emerging-thing"


def test_build_task_title_falls_back_to_slug_then_generic():
    assert dispatch.build_task_title({"slug": "solo"}) == "solo"
    assert dispatch.build_task_title({}) == "initiative next step"


def test_build_task_title_trimmed_to_80():
    t = dispatch.build_task_title({"repo_name": "r", "slug": "s" * 200})
    assert len(t) <= 80


# --- build_task_body -------------------------------------------------------- #
def test_build_task_body_contains_recommendation_and_evidence():
    v = _view(momentum="stalled", age="9d", status="mid-cutover",
              face_message={"text": "explore the router"},
              open_investigations=["does the overlay hold?"],
              open_prs=[{"number": 138, "title": "feat: viewer"}],
              current_doc="/home/zach/workspace/devrc/claudedocs/handoff-x.md")
    rec = {"text": "Review/land open PR #138 feat: viewer", "basis": "open-pr"}
    body = dispatch.build_task_body(v, rec)
    assert "📌 initiatives · next step" in body
    assert "**Review/land open PR #138 feat: viewer**" in body
    assert "inferred" in body                      # non-handoff basis noted as inferred
    assert "devrc" in body                         # repo
    assert "mid-cutover" in body                   # status
    assert "explore the router" in body            # last prompt
    assert "does the overlay hold?" in body        # investigation
    assert "#138 feat: viewer" in body             # open PR line
    assert "emerging-thing" in body                # source line slug
    assert "handoff-x.md" in body                  # source doc path


def test_build_task_body_handoff_basis_labelled_parsed():
    body = dispatch.build_task_body(_view(next_step="wire it"),
                                    {"text": "wire it", "basis": "handoff"})
    assert "parsed next-step" in body


def test_build_task_body_no_crash_on_missing_fields():
    body = dispatch.build_task_body({}, None)
    assert "No grounded next step" in body


# --- build_task_body: Phase-3 STATE-AWARE lead (resume framing) -------------- #
def test_task_lead_is_state_aware():
    # stalled → RESUME lead carrying the real age; slowing → cooling RESUME lead; else generic.
    assert dispatch._task_lead(_view(state="stalled", age="9d")) == \
        "**📌 initiatives · RESUME** — this stalled 9d initiative:"
    assert dispatch._task_lead(_view(state="slowing", age="3d")) == \
        "**📌 initiatives · RESUME** — this cooling initiative:"
    assert dispatch._task_lead(_view(state="active")) == "**📌 initiatives · next step**"
    assert dispatch._task_lead(_view(state="needs_you")) == "**📌 initiatives · next step**"
    # stalled with no age still resolves to a clean lead (no dangling number).
    assert dispatch._task_lead(_view(state="stalled", age="")) == \
        "**📌 initiatives · RESUME** — this stalled initiative:"


def test_build_task_body_stalled_leads_with_resume_and_keeps_evidence():
    v = _view(state="stalled", momentum="stalled", age="9d", status="mid-cutover",
              open_prs=[{"number": 138, "title": "feat: viewer"}],
              current_doc="/home/zach/workspace/devrc/claudedocs/handoff-x.md")
    rec = {"text": "Review/land open PR #138 feat: viewer", "basis": "open-pr"}
    body = dispatch.build_task_body(v, rec)
    # RESUME lead (with age) INSTEAD of the generic heading …
    assert body.startswith("**📌 initiatives · RESUME** — this stalled 9d initiative:")
    assert "📌 initiatives · next step" not in body
    # … but every grounded field is still present, unchanged.
    assert "**Review/land open PR #138 feat: viewer**" in body   # the recommendation
    assert "mid-cutover" in body                                  # status
    assert "#138 feat: viewer" in body                           # open PR line
    assert "emerging-thing" in body                              # source slug
    assert "handoff-x.md" in body                               # source doc


def test_build_task_body_slowing_leads_with_cooling_resume():
    v = _view(state="slowing", momentum="slowing", age="3d")
    body = dispatch.build_task_body(v, {"text": "pick the loop back up", "basis": "focus"})
    assert body.startswith("**📌 initiatives · RESUME** — this cooling initiative:")
    assert "**pick the loop back up**" in body
    assert "📌 initiatives · next step" not in body


def test_build_task_body_active_and_needs_you_keep_generic_lead():
    for st in ("active", "needs_you"):
        body = dispatch.build_task_body(_view(state=st, age="2h"),
                                        {"text": "wire it", "basis": "handoff"})
        assert body.startswith("**📌 initiatives · next step**")
        assert "RESUME" not in body
        assert "**wire it**" in body


def test_build_task_body_state_aware_never_raises_on_missing_fields():
    # a stalled view with NO age / no rec still renders a clean RESUME card (no crash, no invention).
    body = dispatch.build_task_body({"state": "stalled", "slug": "x"}, None)
    assert body.startswith("**📌 initiatives · RESUME** — this stalled initiative:")
    assert "No grounded next step" in body


# --- _post / post_task (injected network) ----------------------------------- #
_CREDS = {"CLAWGATE_API_URL": "https://cg.example", "CLAWGATE_HOOK_TOKEN": "tok"}


def test_post_task_happy_path_payload_and_id():
    captured = {}

    def fake_post(url, payload, token, **kw):
        captured["url"] = url
        captured["payload"] = payload
        captured["token"] = token
        return '{"id": 42}'

    tid = dispatch.post_task("devrc · x", "body text", repo="zach/devrc",
                             creds=_CREDS, _post=fake_post)
    assert tid == 42
    assert captured["url"] == "https://cg.example/api/tasks"
    assert captured["token"] == "tok"
    assert captured["payload"]["directory"] == "devrc · x"
    assert captured["payload"]["body"] == "body text"
    assert captured["payload"]["repo"] == "zach/devrc"


def test_post_task_omits_repo_and_model_when_empty():
    captured = {}

    def fake_post(url, payload, token, **kw):
        captured["payload"] = payload
        return '{"id": 1}'

    dispatch.post_task("d", "b", creds=_CREDS, _post=fake_post)
    assert set(captured["payload"]) == {"directory", "body"}  # no repo, no model


def test_post_task_adds_model_only_when_nonempty():
    captured = {}

    def fake_post(url, payload, token, **kw):
        captured["payload"] = payload
        return '{"id": 1}'

    dispatch.post_task("d", "b", model="deepseek", creds=_CREDS, _post=fake_post)
    assert captured["payload"]["model"] == "deepseek"


def test_post_task_no_creds_returns_none():
    assert dispatch.post_task("d", "b", creds={}) is None


def test_post_task_httperror_returns_none():
    def boom(url, payload, token, **kw):
        raise urllib.error.HTTPError(url, 500, "err", {}, None)

    assert dispatch.post_task("d", "b", creds=_CREDS, _post=boom) is None


def test_post_task_generic_error_returns_none():
    def boom(url, payload, token, **kw):
        raise ConnectionError("unreachable")

    assert dispatch.post_task("d", "b", creds=_CREDS, _post=boom) is None


def test_post_task_bad_id_returns_none():
    assert dispatch.post_task("d", "b", creds=_CREDS, _post=lambda *a, **k: '{"nope": 1}') is None
    # a bool must not count as an int id
    assert dispatch.post_task("d", "b", creds=_CREDS, _post=lambda *a, **k: '{"id": true}') is None


def test_post_task_unparseable_response_returns_none():
    assert dispatch.post_task("d", "b", creds=_CREDS, _post=lambda *a, **k: "not json") is None


# --- dispatch_initiative (injected poster) ---------------------------------- #
def test_dispatch_initiative_happy_path():
    seen = {}

    def poster(directory, body, *, repo="", tags=None, creds=None):
        seen["directory"] = directory
        seen["body"] = body
        seen["repo"] = repo
        seen["tags"] = tags
        return 99

    v = _view(next_step="wire the unit")
    res = dispatch.dispatch_initiative(v, poster=poster)
    assert res == {"ok": True, "task_id": 99, "error": None}
    assert seen["directory"] == "devrc · emerging-thing"
    assert "wire the unit" in seen["body"]
    # The tag is what makes the created Task JOIN back to this card (and arm the guard).
    assert seen["tags"] == ["initiative:emerging-thing"]


def test_dispatch_initiative_no_grounded_step_is_error():
    # An empty view has no grounded next step → ok:False, and the poster is NEVER called.
    called = {"n": 0}

    def poster(*a, **k):
        called["n"] += 1
        return 1

    res = dispatch.dispatch_initiative(_view(), poster=poster)
    assert res["ok"] is False
    assert res["task_id"] is None
    assert "no grounded next step" in res["error"]
    assert called["n"] == 0


def test_dispatch_initiative_poster_returns_none_is_failure():
    res = dispatch.dispatch_initiative(_view(next_step="x"), poster=lambda *a, **k: None)
    assert res["ok"] is False
    assert res["task_id"] is None
    assert res["error"]


def test_dispatch_initiative_never_raises():
    def boom(*a, **k):
        raise RuntimeError("poster exploded")

    res = dispatch.dispatch_initiative(_view(next_step="x"), poster=boom)
    assert res["ok"] is False and res["task_id"] is None
    assert "RuntimeError" in res["error"]


# --- 🔴 TAGGING: the board's own dispatch must arm its own guard ------------- #
# Before this, `dispatch.py` had NO `tags` anywhere: the board minted UNtagged clawgate Tasks,
# which therefore never joined back to a card and never armed the duplicate-dispatch guard —
# so tapping ⤴ dispatch twice on the same card (the exact duplicate the feature is named for)
# was silent. Only repo-cos's weekly approve path emitted tagged tasks.
def test_build_tags_emits_the_ledger_slug_VERBATIM():
    assert dispatch.build_tags(_view(slug="remix-platform")) == ["initiative:remix-platform"]
    # the join in tasks.py is EXACT string equality, so the tag value must equal the slug
    tag = dispatch.build_tags(_view(slug="initiatives-task-links"))[0]
    assert tag == "initiative:initiatives-task-links"
    assert tag.split(":", 1)[1] == "initiatives-task-links"


def test_build_tags_emits_NOTHING_rather_than_a_rewritten_slug(capsys):
    """A normalized-but-rewritten slug would join to NOTHING while looking like it worked —
    strictly worse than no tag. Exact-or-nothing, the same guarantee repo-cos gives."""
    for bad in ("Foo Bar", "UPPER", "has:two:colons", "spaced out", "emoji-🚀", "x" * 80):
        assert dispatch.build_tags(_view(slug=bad)) == [], bad
    assert dispatch.build_tags(_view(slug="")) == []
    assert dispatch.build_tags({}) == []
    assert dispatch.build_tags(None) == []
    assert "untagged" in capsys.readouterr().err


def test_normalize_tag_matches_the_repo_cos_grammar():
    assert dispatch.normalize_tag("initiative:alpha") == "initiative:alpha"
    assert dispatch.normalize_tag("Initiative:Alpha") == "initiative:alpha"   # lowercased
    assert dispatch.normalize_tag("a:b:c") is None                            # one ':' only
    assert dispatch.normalize_tag("initiative:") is None                      # empty side
    assert dispatch.normalize_tag("initiative:a b") == "initiative:a-b"       # ws → '-'
    assert dispatch.normalize_tag("x" * (dispatch.TAG_MAX_LEN + 1)) is None
    assert dispatch.normalize_tag("") is None and dispatch.normalize_tag(None) is None
    assert dispatch.normalize_tags(["b", "a", "a", "BAD:TAG:X"]) == ["a", "b"]


def test_post_task_sends_tags_only_when_nonempty():
    captured = {}

    def fake_post(url, payload, token, **kw):
        captured["payload"] = dict(payload)
        return '{"id": 1}'

    dispatch.post_task("d", "b", creds=_CREDS, _post=fake_post)
    assert "tags" not in captured["payload"]              # bare call = the old 2-key payload
    dispatch.post_task("d", "b", tags=["initiative:alpha"], creds=_CREDS, _post=fake_post)
    assert captured["payload"]["tags"] == ["initiative:alpha"]


def test_post_task_retries_untagged_exactly_once_on_a_tag_400():
    """FAIL-OPEN: 400 is clawgate's tag-grammar rejection, so a tagged post that 400s degrades
    to an UNTAGGED task — never to no task. A tag must never cost a dispatch."""
    seen = []

    def flaky(url, payload, token, **kw):
        seen.append(dict(payload))
        if "tags" in payload:
            raise urllib.error.HTTPError(url, 400, "bad tag", {}, None)
        return '{"id": 7}'

    assert dispatch.post_task("d", "b", tags=["initiative:alpha"],
                              creds=_CREDS, _post=flaky) == 7
    assert len(seen) == 2 and "tags" in seen[0] and "tags" not in seen[1]


def test_post_task_does_NOT_retry_on_any_other_status():
    # 408 in particular: the origin may already have created the Task, so a retry double-posts.
    for code in (401, 403, 404, 408, 429, 500):
        seen = []

        def boom(url, payload, token, _code=code, **kw):
            seen.append(payload)
            raise urllib.error.HTTPError(url, _code, "err", {}, None)

        assert dispatch.post_task("d", "b", tags=["initiative:alpha"],
                                  creds=_CREDS, _post=boom) is None
        assert len(seen) == 1, code


def test_dispatch_of_an_untaggable_slug_still_dispatches_untagged():
    seen = {}

    def poster(directory, body, *, repo="", tags=None, creds=None):
        seen["tags"] = tags
        return 5

    res = dispatch.dispatch_initiative(
        _view(slug="Not A Valid Slug", next_step="do it"), poster=poster)
    assert res["ok"] is True and res["task_id"] == 5      # FAIL-OPEN: the dispatch happens
    assert seen["tags"] == []                             # …just untagged


def test_dispatched_task_joins_back_and_ARMS_the_guard_end_to_end():
    """The loop this closes: dispatch → the created clawgate Task carries `initiative:<slug>`
    → `tasks.py` joins it to that same card → `open_task_count` > 0 → the guard arms."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "tasks_for_dispatch_test", Path(dispatch.__file__).resolve().parent / "tasks.py")
    tasks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tasks)

    posted = {}

    def poster(directory, body, *, repo="", tags=None, creds=None):
        posted.update({"directory": directory, "tags": tags})
        return 4242

    view = _view(slug="initiative-task-links", next_step="close the audit findings")
    res = dispatch.dispatch_initiative(view, poster=poster)
    assert res["ok"] is True

    # what clawgate would then return from GET /api/tasks for that created task
    created = {"id": res["task_id"], "directory": posted["directory"],
               "status": "open", "tags": posted["tags"]}
    linked = tasks.group_by_slug([created], "http://cg.test:30302")
    assert list(linked) == ["initiative-task-links"] == [view["slug"]]
    assert tasks.open_task_count(linked[view["slug"]]) == 1      # → the guard arms
    assert linked[view["slug"]][0]["url"] == "http://cg.test:30302/tasks#task-4242"
