"""Tests for `scripts/mention-open.py` — the Alacritty hint handler.

Scope: URL CONSTRUCTION and the resolution decision. Nothing here launches a
browser, a picker or a notification — every impure edge (`xdg-open`, `rofi`,
`notify-send`, `git`, `tmux`) is left untouched, and the one subprocess test
runs the handler with `--print --no-discovery`, which by construction spawns
nothing.

The two things worth pinning:

  1. THE ALACRITTY CONTRACT. Alacritty appends the matched text as the LAST
     argument, after any configured `args`. Reading `sys.argv[1]` instead works
     by accident today (there are no args) and breaks silently the day one is
     added, so the last-arg behaviour is tested through a real invocation.

  2. NOTHING IS GUESSED. A GitHub reference with no known owner must resolve to
     NO url, and the handler must refuse rather than open something plausible.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
HANDLER = ROOT / "scripts" / "mention-open.py"

_spec = importlib.util.spec_from_file_location("mention_open_under_test", HANDLER)
MO = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MO)


# --------------------------------------------------------------------------- #
# Remote URL -> owner/repo
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("remote,expected", [
    ("git@github.com:innovation-upstream/devrc.git", "innovation-upstream/devrc"),
    ("git@github.com:innovation-upstream/devrc", "innovation-upstream/devrc"),
    ("ssh://git@github.com/civitai/talos-infra.git", "civitai/talos-infra"),
    ("https://github.com/civitai/talos-infra.git", "civitai/talos-infra"),
    ("https://github.com/civitai/talos-infra", "civitai/talos-infra"),
    ("https://github.com/civitai/talos-infra/", "civitai/talos-infra"),
])
def test_parse_owner_repo(remote, expected):
    assert MO.parse_owner_repo(remote) == expected


@pytest.mark.parametrize("remote", [
    "",
    "   ",
    "not a url",
    # A one-segment or three-segment path would build a URL that 404s while
    # looking authoritative — refuse instead.
    "https://example.com/only-one",
    "https://example.com/a/b/c",
    "/home/zach/workspace/devrc",
])
def test_parse_owner_repo_refuses_what_it_cannot_read(remote):
    assert MO.parse_owner_repo(remote) == ""


def test_parse_owner_repo_handles_a_none():
    assert MO.parse_owner_repo(None) == ""


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #
def test_an_explicit_owner_repo_resolves_to_one_openable_candidate():
    span, cands = MO.resolve("civitai/talos-infra#1065")
    assert span["platform"] == "github"
    assert [c["url"] for c in cands] == [
        "https://github.com/civitai/talos-infra/issues/1065"]


def test_a_clickup_id_resolves_to_one_openable_candidate():
    _span, cands = MO.resolve("868abc123")
    assert [c["url"] for c in cands] == ["https://app.clickup.com/t/868abc123"]


def test_a_bare_number_with_no_repo_context_offers_only_clawgate():
    """The GitHub candidate has no URL without a repo, so it is not openable and
    must not appear. One openable candidate means the click opens directly — no
    picker for a choice that does not exist."""
    _span, cands = MO.resolve("#370")
    assert [(c["platform"], c["url"]) for c in cands] == [
        ("clawgate", "https://clawgate.zacx.dev/tasks/370")]


def test_the_clawgate_candidate_opens_a_page_not_a_fragment():
    """🔴 Regression guard against a partial revert to `…/tasks#task-<n>`. The
    fragment form only resolved for a card the board had already rendered; the
    details page at `/tasks/{id}` has no such precondition, so a `#` reappearing
    anywhere in the openable URL is a defect, not a cosmetic difference. Pinned
    as an ABSENCE because `…/tasks/370#task-370` would satisfy a substring or
    `endswith` check on the new form."""
    _span, (clawgate,) = MO.resolve("#370")
    assert clawgate["platform"] == "clawgate"
    assert "#" not in clawgate["url"], clawgate["url"]
    assert clawgate["url"].endswith("/tasks/370")


def test_a_bare_number_with_a_repo_context_offers_both():
    _span, cands = MO.resolve("#370", default_repo="civitai/talos-infra")
    assert [(c["platform"], c["url"]) for c in cands] == [
        ("clawgate", "https://clawgate.zacx.dev/tasks/370"),
        ("github", "https://github.com/civitai/talos-infra/issues/370"),
    ]


def test_a_measured_repo_mapping_resolves_the_short_form():
    _span, cands = MO.resolve("talos-infra#1065",
                              repos={"talos-infra": "civitai/talos-infra"})
    assert [c["url"] for c in cands] == [
        "https://github.com/civitai/talos-infra/issues/1065"]


def test_an_unknown_short_form_repo_yields_NO_candidate_rather_than_a_guess():
    """🔴 The no-guessing rule at the click. A default org here would open a real
    but unrelated issue, which is worse than opening nothing."""
    span, cands = MO.resolve("some-unknown-repo#12")
    assert span is not None and span["platform"] == "github"
    assert cands == []


def test_text_that_is_not_a_mention_resolves_to_nothing():
    """This is how `#282828` arrives: the Alacritty regex is deliberately loose
    (Rust has no lookaround), and the handler is the strict authority."""
    assert MO.resolve('background = "#282828";') == (None, [])
    assert MO.resolve("#282828") == (None, [])


def test_surrounding_debris_does_not_prevent_resolution():
    """Alacritty's post-processing is off for this hint and its regex can carry
    adjacent characters, so the handler finds the mention inside what it is
    given."""
    _span, cands = MO.resolve("(#370)")
    assert [c["platform"] for c in cands] == ["clawgate"]


# --------------------------------------------------------------------------- #
# Picker plumbing
# --------------------------------------------------------------------------- #
def test_picker_rows_show_the_platform_and_the_url():
    _span, cands = MO.resolve("#370", default_repo="civitai/talos-infra")
    rows = MO.picker_rows(cands)
    assert len(rows) == 2
    assert rows[0].startswith("clawgate task 370 ")
    assert rows[0].endswith("https://clawgate.zacx.dev/tasks/370")
    assert rows[1].endswith("https://github.com/civitai/talos-infra/issues/370")


def test_a_row_maps_back_to_its_own_url():
    _span, cands = MO.resolve("#370", default_repo="civitai/talos-infra")
    rows = MO.picker_rows(cands)
    for row, cand in zip(rows, cands):
        assert MO.row_to_url(row, cands) == cand["url"]


def test_a_dismissed_picker_maps_to_no_url():
    _span, cands = MO.resolve("#370", default_repo="civitai/talos-infra")
    assert MO.row_to_url("", cands) == ""
    assert MO.row_to_url("something else entirely", cands) == ""


# --------------------------------------------------------------------------- #
# 🔴 The Alacritty argv contract
# --------------------------------------------------------------------------- #
def _run(*args):
    return subprocess.run([sys.executable, str(HANDLER), *args],
                          capture_output=True, text=True, timeout=60)


def test_the_matched_text_is_read_from_the_LAST_argument():
    """Alacritty appends the match AFTER any configured `args`. Reading argv[1]
    would take a configured argument as the mention and is indistinguishable
    from correct behaviour until the day an arg is added."""
    r = _run("--print", "--no-discovery", "civitai/talos-infra#1065")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "https://github.com/civitai/talos-infra/issues/1065"

    # Same match, now preceded by extra arguments — the answer must not move.
    r2 = _run("--print", "--no-discovery", "--default-repo", "civitai/talos-infra",
              "civitai/talos-infra#1065")
    assert r2.returncode == 0, r2.stderr
    assert r2.stdout == r.stdout


def test_an_ambiguous_click_prints_every_candidate():
    r = _run("--print", "--no-discovery", "--default-repo", "civitai/talos-infra", "#370")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == [
        "https://clawgate.zacx.dev/tasks/370",
        "https://github.com/civitai/talos-infra/issues/370",
    ]


def test_a_non_mention_exits_non_zero_and_says_so():
    """🔴 A handler that fails SILENTLY is indistinguishable from one that was
    never wired up. The refusal is announced on stderr even when no desktop
    notification is available."""
    r = _run("--print", "--no-discovery", "#282828")
    assert r.returncode == 1
    assert "no mention" in r.stderr


def test_an_unresolvable_owner_exits_non_zero_and_explains():
    r = _run("--print", "--no-discovery", "some-unknown-repo#12")
    assert r.returncode == 1
    assert "cannot resolve" in r.stderr
    assert "owner" in r.stderr


# --------------------------------------------------------------------------- #
# main() — the decision, with every impure edge stubbed
# --------------------------------------------------------------------------- #
@pytest.fixture
def spy(monkeypatch):
    """Record what main() would have done, without doing any of it."""
    calls: list = []
    monkeypatch.setattr(MO, "discover_repos",
                        lambda *a, **k: calls.append("discover") or
                        {"talos-infra": "civitai/talos-infra"})
    monkeypatch.setattr(MO, "tmux_pane_repo",
                        lambda: calls.append("tmux") or "civitai/talos-infra")
    monkeypatch.setattr(MO, "open_url", lambda url: calls.append(("open", url)) or 0)
    monkeypatch.setattr(MO, "pick", lambda c: calls.append(("pick", len(c))) or c[0]["url"])
    monkeypatch.setattr(MO, "notify", lambda *a, **k: calls.append(("notify", a[0])))
    return calls


def test_an_unambiguous_click_opens_without_paying_for_discovery(spy):
    """Discovery is a `git remote` fan-out plus a tmux round-trip. A click whose
    text already carries `owner/repo` must not pay for it — that latency lands
    on the operator every single time."""
    assert MO.main(["civitai/talos-infra#1065"]) == 0
    assert spy == [("open", "https://github.com/civitai/talos-infra/issues/1065")]


def test_a_clickup_click_also_skips_discovery(spy):
    assert MO.main(["868abc123"]) == 0
    assert spy == [("open", "https://app.clickup.com/t/868abc123")]


def test_an_ambiguous_click_measures_then_shows_the_picker(spy):
    """A bare `#N` is the case discovery exists for: without a repo there is
    only a clawgate candidate, and the operator never gets the choice."""
    assert MO.main(["#370"]) == 0
    assert spy[0] == "tmux"
    assert "discover" in spy
    assert ("pick", 2) in spy
    assert spy[-1] == ("open", "https://clawgate.zacx.dev/tasks/370")


def test_a_multi_digit_clawgate_id_round_trips_to_the_click(spy):
    """A five-digit id must arrive at `open` INTACT — the widest id the bare-`#`
    pattern admits (`_NUM = \\d{1,5}`), and distinct from every other id in this
    file, so a mutant that truncates the id or reuses a neighbouring value cannot
    land on the expected string by accident."""
    assert MO.main(["#10593"]) == 0
    assert spy[-1] == ("open", "https://clawgate.zacx.dev/tasks/10593")


def test_a_dismissed_picker_opens_nothing_and_is_not_an_error(spy, monkeypatch):
    monkeypatch.setattr(MO, "pick", lambda c: "")
    assert MO.main(["#370"]) == 0
    assert not [c for c in spy if isinstance(c, tuple) and c[0] == "open"]


def test_a_short_form_repo_is_resolved_from_the_discovered_checkouts(spy):
    assert MO.main(["talos-infra#1065"]) == 0
    assert "discover" in spy
    assert spy[-1] == ("open", "https://github.com/civitai/talos-infra/issues/1065")


def test_a_non_mention_notifies_and_opens_nothing(spy):
    assert MO.main(['background = "#282828";']) == 1
    assert spy == [("notify", "no mention in the clicked text")]


# --------------------------------------------------------------------------- #
# The generated mapping — an OPTIONAL accelerator that must never break a click
# --------------------------------------------------------------------------- #
def test_an_absent_mapping_file_is_an_empty_mapping_not_an_error(tmp_path):
    """🔴 The mapping is optional by construction: `owner/repo#N`, a local
    checkout and PASS 3 all resolve without it. An earlier version imported a
    generated module at the TOP of the handler, so a checkout predating that
    file killed EVERY click — including the ones needing no mapping at all —
    with a traceback to a detached process's stderr that nobody ever sees."""
    assert MO.load_known_repos(tmp_path / "nothing-here.json") == {}


@pytest.mark.parametrize("body", [
    "",                                   # empty file
    "{not json at all",                   # truncated write
    '["a", "b"]',                         # right syntax, wrong shape
    '{"repo": 12}',                       # value not a string
    '{"repo": "no-slash"}',               # value not owner/repo
])
def test_every_malformed_mapping_degrades_to_empty(tmp_path, body):
    p = tmp_path / "known_repos.json"
    p.write_text(body)
    assert MO.load_known_repos(p) == {}


def test_a_valid_mapping_is_read_and_bad_rows_are_dropped_individually(tmp_path):
    """One bad row must not discard the good ones — the file is generated, and
    a single odd entry is not a reason to lose 400 working ones."""
    p = tmp_path / "known_repos.json"
    p.write_text('{"trowelcast": "gardenersguild/trowelcast", "bad": "nope", '
                 '"sledgehorn": "gardenersguild/sledgehorn"}')
    assert MO.load_known_repos(p) == {
        "trowelcast": "gardenersguild/trowelcast",
        "sledgehorn": "gardenersguild/sledgehorn"}


def test_a_local_checkout_overrides_the_mapping(tmp_path, monkeypatch):
    """The checkout is a measurement of this disk; the mapping is a snapshot
    that can predate a transfer or a rename."""
    p = tmp_path / "known_repos.json"
    p.write_text('{"trowelcast": "stale-owner/trowelcast"}')
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", p)
    ws = tmp_path / "workspace"
    (ws / "trowelcast" / ".git").mkdir(parents=True)
    monkeypatch.setattr(MO, "repo_of_checkout", lambda path: "gardenersguild/trowelcast")
    assert MO.discover_repos(ws)["trowelcast"] == "gardenersguild/trowelcast"


# --------------------------------------------------------------------------- #
# PASS 3 — the GitHub API fallback
#
# 🔴 An earlier version of this path shipped INERT: its jq program compared
# `.name` against the LITERAL text `"$name"` (a Python string with no f-prefix),
# selecting nothing for any input, while every test that stubbed `gh` passed.
# Nothing here mocks the selection — the fake returns the rows `gh` would print
# and the assertions pin WHICH of them the resolver takes.
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _clear_api_cache():
    """The cache is module-global and would carry an answer between tests."""
    MO._GITHUB_API_CACHE.clear()
    yield
    MO._GITHUB_API_CACHE.clear()


# Bound BEFORE any test patches the module attribute, so a test that needs a
# REAL subprocess while `gh` is faked still gets one.
_REAL_RUN = subprocess.run


def _fake_gh(rows, *, returncode=0):
    """Stand in for `gh api search/repositories`, recording the argv it got.
    Rows are RELEVANCE-ordered as the endpoint returns them — the exact-name
    matches are deliberately NOT first."""
    seen: dict = {}

    def run(cmd, **kwargs):
        seen["cmd"] = list(cmd)
        return types.SimpleNamespace(
            returncode=returncode, stdout="".join(f"{r}\n" for r in rows), stderr="")

    return run, seen


# Fixture rows share no substring with any assertion constant, and the two
# EXACT matches sit at positions 2 and 4 — so a mutant that takes the first row,
# the last row, or matches on a prefix cannot land on the expected value.
_SEARCH_ROWS = [
    "hobbyist/trowelcast-examples",
    "gardenersguild/trowelcast",
    "someoneelse/trowelcast-mirror",
    "rivalorg/trowelcast",
    "thirdparty/trowelcast-fork",
]


def test_the_api_fallback_returns_EVERY_exact_name_match(monkeypatch):
    """🔴 Relevance order is not name order, and an exact-name hit is not an
    unambiguous one. Returning the first row silently picks a stranger's repo."""
    run, seen = _fake_gh(_SEARCH_ROWS)
    monkeypatch.setattr(MO.subprocess, "run", run)
    assert MO.gh_api_repo_search("trowelcast") == ({
        "gardenersguild/trowelcast": "gardenersguild/trowelcast",
        "rivalorg/trowelcast": "rivalorg/trowelcast"}, "")
    assert seen["cmd"][:3] == ["gh", "api", "search/repositories"]


def test_the_api_fallback_matches_a_name_case_insensitively(monkeypatch):
    run, _ = _fake_gh(["hobbyist/TrowelCast-ui", "gardenersguild/TrowelCast"])
    monkeypatch.setattr(MO.subprocess, "run", run)
    assert MO.gh_api_repo_search("trowelcast") == (
        {"gardenersguild/TrowelCast": "gardenersguild/TrowelCast"}, "")


def test_the_api_fallback_returns_NOTHING_when_no_row_names_the_repo(monkeypatch):
    """🔴 NOTHING IS GUESSED. Only near-misses ⇒ no candidate at all."""
    run, _ = _fake_gh(["hobbyist/trowelcast-examples", "thirdparty/trowelcast-fork"])
    monkeypatch.setattr(MO.subprocess, "run", run)
    # 🔴 The reason is EMPTY here: the search RAN and matched nothing. That is a
    # different fact from a search that could not run, and the refusal differs.
    assert MO.gh_api_repo_search("trowelcast") == ({}, "")


def test_the_api_fallback_returns_nothing_when_gh_fails(monkeypatch):
    """A non-zero `gh` (no auth, rate limit, no network, BINARY ABSENT) prints
    its error on stderr; treating stdout as an answer would resolve to garbage.

    ⚠ INVARIANT GUARD, not regression coverage — green on the pre-fix code too."""
    run, _ = _fake_gh(_SEARCH_ROWS, returncode=1)
    monkeypatch.setattr(MO.subprocess, "run", run)
    matches, why = MO.gh_api_repo_search("trowelcast")
    assert matches == {}
    assert "gh exited 1" in why


def test_the_jq_program_is_a_CONSTANT_that_cannot_do_the_selection(monkeypatch):
    """🔴 THE SEAM. Every test above stubs `gh`, so a filter that selects
    nothing in production still passes them — precisely how the original bug
    survived. `gh api --jq` accepts no `--arg`, so a name in the filter can only
    be a string literal: assert the program carries neither the searched name
    nor a `$`, i.e. that jq CANNOT be where the match happens."""
    run, seen = _fake_gh(_SEARCH_ROWS)
    monkeypatch.setattr(MO.subprocess, "run", run)
    MO.gh_api_repo_search("trowelcast")
    jq_program = seen["cmd"][seen["cmd"].index("--jq") + 1]
    assert "$" not in jq_program
    assert "trowelcast" not in jq_program


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not on PATH")
def test_the_jq_program_really_yields_the_rows_the_resolver_filters(monkeypatch):
    """The other half of the seam: run the EMITTED program through the REAL jq
    against a realistic payload. The inert original returns zero lines here."""
    run, seen = _fake_gh(_SEARCH_ROWS)
    monkeypatch.setattr(MO.subprocess, "run", run)
    MO.gh_api_repo_search("trowelcast")
    jq_program = seen["cmd"][seen["cmd"].index("--jq") + 1]

    payload = json.dumps({"items": [
        {"name": full.split("/")[1], "full_name": full} for full in _SEARCH_ROWS]})
    # 🔴 _REAL_RUN, not subprocess.run: `MO.subprocess` IS the shared module, so
    # the fake above is installed globally for the duration of the test. Calling
    # subprocess.run here reaches the FAKE and this test passes on any filter
    # whatsoever — measured, it passed against the inert original.
    r = _REAL_RUN(["jq", "-r", jq_program], input=payload,
                  capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "gardenersguild/trowelcast" in r.stdout.splitlines()


def test_one_exact_match_opens_directly(spy, monkeypatch):
    run, _ = _fake_gh(["hobbyist/trowelcast-examples", "gardenersguild/trowelcast"])
    monkeypatch.setattr(MO.subprocess, "run", run)
    assert MO.main(["trowelcast#77"]) == 0
    assert ("pick", 2) not in spy
    assert spy[-1] == ("open", "https://github.com/gardenersguild/trowelcast/issues/77")


def test_TWO_owners_with_the_same_repo_name_go_to_the_PICKER(spy, monkeypatch):
    """🔴 THE NAMESAKE RULE. `dashboard`, `cli`, `api` exist under dozens of
    owners; measured on the first version, `dashboard#12` silently opened a
    retired Kubernetes repo. Several owners is a CHOICE, not a ranking."""
    run, _ = _fake_gh(_SEARCH_ROWS)
    monkeypatch.setattr(MO.subprocess, "run", run)
    assert MO.main(["trowelcast#77"]) == 0
    assert ("pick", 2) in spy, spy
    urls = MO.picker_rows([
        {"platform": "github", "id": "77",
         "url": "https://github.com/gardenersguild/trowelcast/issues/77"},
        {"platform": "github", "id": "77",
         "url": "https://github.com/rivalorg/trowelcast/issues/77"}])
    assert "gardenersguild" in urls[0] and "rivalorg" in urls[1]


def test_an_unknown_repo_with_no_exact_match_still_refuses(spy, monkeypatch):
    """PASS 3 must not turn an honest refusal into a confident wrong page."""
    run, _ = _fake_gh(["hobbyist/trowelcast-examples"])
    monkeypatch.setattr(MO.subprocess, "run", run)
    assert MO.main(["trowelcast#77"]) == 1
    assert spy[-1] == ("notify", "cannot resolve trowelcast#77")


def test_PASS_3_never_second_guesses_an_owner_the_text_already_stated(monkeypatch):
    """🔴 An explicit `owner/repo` is source 1 — the strongest there is. PASS 3's
    subject pattern must not admit it, or a search by name could REPLACE a
    stated owner with a guessed one."""
    assert MO._PASS3_REPO_RE.match("trowelcast#77")
    assert not MO._PASS3_REPO_RE.match("gardenersguild/trowelcast#77")
    assert not MO._PASS3_REPO_RE.match("#77")
    assert not MO._PASS3_REPO_RE.match("see trowelcast#77 today")


def test_a_bare_number_never_reaches_the_api(spy, monkeypatch):
    """A bare `#N` names no repo, so there is nothing to search for — and a
    search on a number would return arbitrary repos."""
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(MO.subprocess, "run", run)
    MO.main(["#370"])
    assert not [c for c in calls if c[:1] == ["gh"]]


# --------------------------------------------------------------------------- #
# 🔴 THE SEAM: the mapping actually reaching a resolution
#
# Every test above this point stubs `discover_repos` or `gh`, so NONE of them
# notices if the mapping is never loaded at all. Measured: deleting the
# `load_known_repos()` call from `discover_repos` left the entire file green,
# and the override test below was inert because the loader bound its default
# path at IMPORT — so patching the module attribute changed nothing.
# --------------------------------------------------------------------------- #
def _write_mapping(tmp_path, mapping):
    p = tmp_path / "known_repos.json"
    p.write_text(json.dumps(mapping))
    return p


def test_a_mapping_ONLY_name_resolves_end_to_end_through_main(tmp_path, monkeypatch):
    """🔴 THE MUTATION THIS PINS: drop `load_known_repos()` from
    `discover_repos` and this is the test that goes red. The name exists in no
    checkout and `gh` is not reachable, so the mapping is the ONLY thing that
    can produce this URL."""
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH",
                        _write_mapping(tmp_path, {"plotwidget": "gardenersguild/plotwidget"}))
    monkeypatch.setattr(MO, "WORKSPACE", tmp_path / "no-such-workspace")
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "")
    monkeypatch.setattr(MO, "gh_api_repo_search", lambda name: ({}, "gh disabled for this test"))
    opened = []
    monkeypatch.setattr(MO, "open_url", lambda url: opened.append(url) or 0)
    assert MO.main(["plotwidget#42"]) == 0
    assert opened == ["https://github.com/gardenersguild/plotwidget/issues/42"]


def test_the_loader_reads_the_path_at_CALL_time_not_at_import(tmp_path, monkeypatch):
    """A `path: Path = KNOWN_REPOS_PATH` default is evaluated once, at import,
    so `monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", …)` would be inert and every
    test using it would pass for the wrong reason."""
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH",
                        _write_mapping(tmp_path, {"plotwidget": "gardenersguild/plotwidget"}))
    assert MO.load_known_repos() == {"plotwidget": "gardenersguild/plotwidget"}


def test_a_local_checkout_beats_the_mapping_for_the_same_name(tmp_path, monkeypatch):
    """The precedence the module docstring asserts, exercised through
    `discover_repos` rather than asserted in prose."""
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH",
                        _write_mapping(tmp_path, {"plotwidget": "staleowner/plotwidget"}))
    ws = tmp_path / "workspace"
    (ws / "plotwidget" / ".git").mkdir(parents=True)
    monkeypatch.setattr(MO, "repo_of_checkout", lambda path: "gardenersguild/plotwidget")
    out = MO.discover_repos(ws)
    assert out["plotwidget"] == "gardenersguild/plotwidget"


def test_a_three_segment_value_is_refused_rather_than_404ing(tmp_path, monkeypatch):
    """`github.com/a/b/c/issues/12` 404s while looking authoritative — the same
    rule `parse_owner_repo` enforces for a git remote."""
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", _write_mapping(
        tmp_path, {"good": "gardenersguild/good", "bad": "gardenersguild/team/bad"}))
    assert MO.load_known_repos() == {"good": "gardenersguild/good"}


def test_the_reader_and_the_writer_agree_on_ONE_path(tmp_path, monkeypatch):
    """🔴 TWO INDEPENDENT COPIES OF ONE RULE. The generator computes its default
    output path and the handler computes where it reads from; they agree only by
    coincidence. Move either and the generator reports success, every `repo#N`
    silently behaves as if no mapping existed, and BOTH files' suites stay
    green — nothing else in either one compares them."""
    monkeypatch.delenv("MENTION_OPEN_KNOWN_REPOS", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    spec = importlib.util.spec_from_file_location(
        "regen_for_seam", ROOT / "scripts" / "regen-known-repos.py")
    RG = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(RG)
    reader = Path(os.environ.get("MENTION_OPEN_KNOWN_REPOS")
                  or Path(os.environ["XDG_CONFIG_HOME"]) / "mention-open" / "known_repos.json")
    assert RG.DEFAULT_PATH == reader, (
        f"the generator writes {RG.DEFAULT_PATH} and the handler reads {reader}")


# --------------------------------------------------------------------------- #
# PASS 3 — an unusable picker, and an empty result that names its cause
# --------------------------------------------------------------------------- #
def test_too_many_namesakes_REFUSES_instead_of_showing_a_wall(spy, monkeypatch):
    """Measured: `dashboard` and `cli` each return a full page of EXACT matches.
    A 100-row list of URLs differing only by owner is not a choice."""
    rows = [f"owner{i}/trowelcast" for i in range(MO.PASS3_MAX_CHOICES + 1)]
    run, _ = _fake_gh(rows)
    monkeypatch.setattr(MO.subprocess, "run", run)
    assert MO.main(["trowelcast#77"]) == 1
    assert spy[-1] == ("notify", "cannot resolve trowelcast#77")
    assert not [c for c in spy if isinstance(c, tuple) and c[0] == "open"]


def test_exactly_the_cap_still_offers_the_picker(spy, monkeypatch):
    """The boundary, from the other side — a cap that also swallowed the
    legitimate case would be a refusal dressed as a limit."""
    rows = [f"owner{i}/trowelcast" for i in range(MO.PASS3_MAX_CHOICES)]
    run, _ = _fake_gh(rows)
    monkeypatch.setattr(MO.subprocess, "run", run)
    assert MO.main(["trowelcast#77"]) == 0
    assert ("pick", MO.PASS3_MAX_CHOICES) in spy


def test_a_search_that_COULD_NOT_RUN_says_so_instead_of_denying_the_repo(spy, monkeypatch):
    """🔴 An empty result cannot distinguish `gh` missing from no such repo, and
    the two need opposite next moves. The refusal must not assert the stronger
    claim."""
    def boom(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "gh")

    monkeypatch.setattr(MO.subprocess, "run", boom)
    notices = []
    monkeypatch.setattr(MO, "notify", lambda *a, **k: notices.append(a))
    assert MO.main(["trowelcast#77"]) == 1
    assert "gh is not on PATH" in notices[-1][1]
    assert "not a claim that no such repo exists" in notices[-1][1]


def test_a_search_that_RAN_and_matched_nothing_keeps_the_ordinary_refusal(spy, monkeypatch):
    run, _ = _fake_gh(["hobbyist/trowelcast-examples"])
    monkeypatch.setattr(MO.subprocess, "run", run)
    notices = []
    monkeypatch.setattr(MO, "notify", lambda *a, **k: notices.append(a))
    assert MO.main(["trowelcast#77"]) == 1
    assert "owner/repo#N" in notices[-1][1]
    assert "not a claim" not in notices[-1][1]
