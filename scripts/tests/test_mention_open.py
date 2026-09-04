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


def test_an_unknown_repo_with_no_exact_match_still_refuses_WHEN_THE_UNIVERSE_IS_EMPTY(
        spy, monkeypatch):
    """PASS 3 must not turn an honest refusal into a confident wrong page.

    The refusal is now the LAST resort rather than the only one — it survives
    exactly when PASS 4 has nothing to offer, which is what an empty
    `discover_repos()` produces."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: {})
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
    silently behaves as if no mapping existed, and BOTH files' suites stay green.

    🔴 BOTH MODULES ARE RE-IMPORTED under one environment and their CONSTANTS
    compared. An earlier version re-implemented the reader's expression inline
    here — which pinned the generator's side only: mutating the HANDLER's
    filename to `repos.json` SURVIVED the whole suite, in the direction that
    actually breaks resolution.
    """
    monkeypatch.delenv("MENTION_OPEN_KNOWN_REPOS", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    def _fresh(name, rel):
        spec = importlib.util.spec_from_file_location(name, ROOT / rel)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    writer = _fresh("regen_for_seam", "scripts/regen-known-repos.py")
    reader = _fresh("mention_open_for_seam", "scripts/mention-open.py")
    assert writer.DEFAULT_PATH == reader.KNOWN_REPOS_PATH, (
        f"the generator writes {writer.DEFAULT_PATH} but the handler reads "
        f"{reader.KNOWN_REPOS_PATH} — the mapping would be generated into a "
        f"file nothing ever loads, with both suites green")

    # 🔴 THREE PARTIES NOW, NOT TWO. `scripts/collector/claude/session-tailer.py`
    # reads the same mapping to ATTRIBUTE a bare `#N`, and it computes the path
    # with its own copy of the expression. A tailer pointed at a file nothing
    # writes attributes nothing — and looks exactly like a host with no mapping,
    # which is a supported state, so no counter goes red.
    # The tailer imports its siblings (`_shared`, `tailer`) by bare name, so its
    # own directory has to be importable — it normally is, because the deployed
    # copy is executed from there.
    for extra in ("scripts/collector", "scripts/collector/claude"):
        monkeypatch.syspath_prepend(str(ROOT / extra))
    tailer = _fresh("session_tailer_for_seam",
                    "scripts/collector/claude/session-tailer.py")
    assert tailer.mention_repos_path() == reader.KNOWN_REPOS_PATH, (
        f"the tailer reads {tailer.mention_repos_path()} but the handler reads "
        f"{reader.KNOWN_REPOS_PATH} — telemetry attribution would be silently "
        f"dead while every suite stays green")


def test_the_workspace_is_resolved_at_CALL_time_too(tmp_path, monkeypatch):
    """🔴 THE CLASS, NOT THE INSTANCE. `discover_repos(workspace=WORKSPACE)`
    bound its default at import exactly as `load_known_repos` did, so patching
    `MO.WORKSPACE` was inert — measured: a test believing it had an empty
    workspace ran 91 real `git remote` subprocesses against the real
    `~/workspace` and read back 79 real repositories.

    🔴 THIS ASSERTS A CHECKOUT IS FOUND, NOT THAT NONE IS. An earlier version
    patched `WORKSPACE` to an EMPTY directory and asserted `== {}` — which is
    exactly what the import-bound default ALSO produces in the SANDBOX tier,
    where HOME is a fresh empty dir (`flake.nix` exports `HOME=$TMPDIR/home`).
    Measured: that mutant died on the dev host and SURVIVED under an empty
    HOME — the guard for this defect was inert in the tier the merge is gated
    on, and green either way."""
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", tmp_path / "absent.json")
    ws = tmp_path / "elsewhere"
    (ws / "plotwidget" / ".git").mkdir(parents=True)
    monkeypatch.setattr(MO, "WORKSPACE", ws)
    monkeypatch.setattr(MO, "repo_of_checkout", lambda path: "gardenersguild/plotwidget")
    # No argument: the default must be read NOW, from the patched attribute.
    assert MO.discover_repos() == {"plotwidget": "gardenersguild/plotwidget"}


# PASS 3 — an unusable picker, and an empty result that names its cause
# --------------------------------------------------------------------------- #
def test_a_WALL_of_namesakes_now_reaches_the_picker_because_it_can_be_TYPED_at(
        spy, monkeypatch):
    """🔴 THE REVERSAL. This used to refuse above 8 namesakes, on the reasoning
    that "a 100-row list of URLs differing only by owner is not a choice, it is
    a wall". That is true of a list you can only SCROLL and false of one you can
    TYPE AT, and `pick()` now runs rofi with `-matching fuzzy`. So the wall is a
    narrowing, and refusing would remove the operator's ability to choose.

    17 rows, not 9: the old cap was 8, so a fixture of 9 would sit one step past
    a boundary that no longer exists and could not tell a re-added cap of 16
    from no cap at all."""
    rows = [f"owner{i}/trowelcast" for i in range(17)]
    run, _ = _fake_gh(rows)
    monkeypatch.setattr(MO.subprocess, "run", run)
    assert MO.main(["trowelcast#77"]) == 0
    assert ("pick", 17) in spy, spy
    assert not [c for c in spy if isinstance(c, tuple) and c[0] == "notify"]


def test_eight_namesakes_still_offer_the_picker(spy, monkeypatch):
    """The case that worked under the old cap must keep working under no cap —
    a reversal that broke the legitimate side would be the same outage wearing
    the opposite hat."""
    rows = [f"owner{i}/trowelcast" for i in range(8)]
    run, _ = _fake_gh(rows)
    monkeypatch.setattr(MO.subprocess, "run", run)
    assert MO.main(["trowelcast#77"]) == 0
    assert ("pick", 8) in spy


def test_the_picker_asks_rofi_for_FUZZY_matching(monkeypatch):
    """🔴 THE SEAM BETWEEN THE DECISION AND THE TOOL. Every other test here
    stubs `pick`, so none of them notices if the flag that makes a long list
    usable is missing — and dropping it silently restores the wall the cap used
    to guard against, with the whole suite green. This is the only test that
    reads the argv `pick` actually builds.

    NOTHING IS LAUNCHED: `subprocess.run` is replaced, so no window is ever
    raised. Raising a window takes the operator's screen."""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["input"] = kwargs.get("input", "")
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(MO.subprocess, "run", fake_run)
    MO.pick([{"platform": "github", "id": "7",
              "url": "https://github.com/gardenersguild/trowelcast/issues/7"}])
    assert seen["cmd"][0] == "rofi"
    assert "-matching" in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("-matching") + 1] == "fuzzy"
    # `-no-custom` stops rofi handing back typed free text as a selection.
    assert "-no-custom" in seen["cmd"]


def test_a_search_that_COULD_NOT_RUN_says_so_instead_of_denying_the_repo(spy, monkeypatch):
    """🔴 An empty result cannot distinguish `gh` missing from no such repo, and
    the two need opposite next moves. The refusal must not assert the stronger
    claim. With an EMPTY universe there is nothing to offer, so this is still the
    refusal path exactly as it was."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: {})

    def boom(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "gh")

    monkeypatch.setattr(MO.subprocess, "run", boom)
    notices = []
    monkeypatch.setattr(MO, "notify", lambda *a, **k: notices.append(a))
    assert MO.main(["trowelcast#77"]) == 1
    assert "gh is not on PATH" in notices[-1][1]
    assert "not a claim that no such repo exists" in notices[-1][1]


def test_the_picker_does_not_SWALLOW_the_reason_the_search_could_not_run(
        spy, monkeypatch):
    """🔴 THE NEW SHAPE OF AN OLD RULE. PASS 4 turns a refusal into a choice, and
    the easiest way to write that is to drop the refusal entirely — which would
    silently discard "gh is not on PATH", a fact about the operator's TOOLING
    that no picker can express. The cause is announced AND the choice is offered:
    they answer different questions."""
    def boom(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "gh")

    monkeypatch.setattr(MO.subprocess, "run", boom)
    notices = []
    monkeypatch.setattr(MO, "notify", lambda *a, **k: notices.append(a))
    assert MO.main(["trowelcast#77"]) == 0
    assert ("pick", 1) in spy, spy
    assert any("gh is not on PATH" in n[1] for n in notices), notices


def test_a_search_that_RAN_and_matched_nothing_keeps_the_ordinary_refusal(spy, monkeypatch):
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: {})
    run, _ = _fake_gh(["hobbyist/trowelcast-examples"])
    monkeypatch.setattr(MO.subprocess, "run", run)
    notices = []
    monkeypatch.setattr(MO, "notify", lambda *a, **k: notices.append(a))
    assert MO.main(["trowelcast#77"]) == 1
    assert "owner/repo#N" in notices[-1][1]
    assert "not a claim" not in notices[-1][1]


def test_the_refusal_keeps_the_ADVICE_when_it_also_names_a_cause(spy, monkeypatch):
    """🔴 The case where the search could not run is exactly the case where
    writing `owner/repo#N` is the workaround — so naming the cause must ADD to
    the advice, never replace it. It replaced it once."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: {})

    def boom(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "gh")

    monkeypatch.setattr(MO.subprocess, "run", boom)
    notices = []
    monkeypatch.setattr(MO, "notify", lambda *a, **k: notices.append(a))
    assert MO.main(["trowelcast#77"]) == 1
    body = notices[-1][1]
    assert "gh is not on PATH" in body
    assert "owner/repo#N" in body, "the actionable advice was dropped"


@pytest.mark.parametrize("value", [
    "acme/widget/",      # trailing slash -> .../acme/widget//issues/12
    "acme//widget",      # empty middle segment
    "acme/widget ",      # trailing space inside the URL path
    "acme/wid\nget",     # embedded newline
    "acme/widget\n",     # TRAILING newline — `$` matches before it, `\\Z` does not
    "/acme/widget",      # leading slash
    "acme",              # one segment
])
def test_a_value_that_is_not_EXACTLY_owner_slash_repo_is_refused(tmp_path, value):
    """Counting non-empty segments accepted every one of these, and each builds
    a URL that 404s while looking authoritative."""
    p = tmp_path / "known_repos.json"
    p.write_text(json.dumps({"widget": value}))
    assert MO.load_known_repos(p) == {}


def test_the_search_asks_for_a_FULL_page(monkeypatch):
    """`per_page` defaults to 30. Removing it shrinks what the exact-name filter
    can even see — and it is invisible to every other test here, because the
    fake returns whatever rows the test hands it regardless of the request.
    Measured: dropping `per_page=100` SURVIVED the whole suite."""
    run, seen = _fake_gh(_SEARCH_ROWS)
    monkeypatch.setattr(MO.subprocess, "run", run)
    MO.gh_api_repo_search("trowelcast")
    assert "per_page=100" in seen["cmd"], seen["cmd"]


# --------------------------------------------------------------------------- #
# PASS 4 — THE FUZZY UNIVERSE
#
# 🔴 EVERY NAME BELOW IS SYNTHETIC. The real universe is built from
# `known_repos.json`, the file whose committed ancestor disclosed 232 PRIVATE
# repositories into this PUBLIC repo. No test may read that file, and no row
# from it may ever be written to a fixture, a log or a spool. Values are
# pairwise distinct and distinct from every constant these assertions name.
#
# 🔴 NOTHING HERE LAUNCHES ROFI. `pick` is stubbed by the `spy` fixture, or
# `subprocess.run` is replaced. Raising a window takes the operator's screen.
# --------------------------------------------------------------------------- #
FAKE_UNIVERSE = {
    "trowelcast": "gardenersguild/trowelcast",
    "plotwidget": "hobbyist/plotwidget",
    "spadeworks": "rivalorg/spadeworks",
}


@pytest.fixture
def universe(monkeypatch):
    """A three-entry synthetic universe, and NO gh — so anything that resolves
    below did so through PASS 4 and nothing else."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: dict(FAKE_UNIVERSE))
    # ("", not a reason): the search RAN and matched nothing. A reason here would
    # make every test below also exercise the "could not run" notice, which has
    # its own test — a fixture that bundles two conditions cannot tell you which
    # one an assertion is about.
    monkeypatch.setattr(MO, "gh_api_repo_search", lambda name: ({}, ""))
    return FAKE_UNIVERSE


def test_repo_universe_is_the_sorted_distinct_owner_repos():
    assert MO.repo_universe(FAKE_UNIVERSE) == [
        "gardenersguild/trowelcast", "hobbyist/plotwidget", "rivalorg/spadeworks"]


def test_repo_universe_drops_a_value_that_would_404_while_looking_authoritative():
    assert MO.repo_universe({"a": "acme/widget/", "b": "acme", "c": 3,
                             "d": "hobbyist/plotwidget"}) == ["hobbyist/plotwidget"]


def test_repo_universe_is_total_on_an_absent_mapping():
    assert MO.repo_universe(None) == []
    assert MO.repo_universe({}) == []


def test_universe_candidates_build_one_openable_row_per_repo():
    cands = MO.universe_candidates("77", ["gardenersguild/trowelcast",
                                          "rivalorg/spadeworks"])
    assert [c["url"] for c in cands] == [
        "https://github.com/gardenersguild/trowelcast/issues/77",
        "https://github.com/rivalorg/spadeworks/issues/77"]
    assert {c["platform"] for c in cands} == {"github"}
    assert {c["id"] for c in cands} == {"77"}


def test_an_UNRESOLVABLE_repo_now_reaches_the_fuzzy_picker(spy, universe):
    """🔴 DEAD END 2. `talos-inf#12` used to refuse; now the operator types four
    characters into a fuzzy picker. This is the extension of "several matches
    means a picker", not a relaxation of "nothing is guessed" — nothing opens
    without a selection."""
    assert MO.main(["zzznosuchrepo#12"]) == 0
    assert ("pick", 3) in spy, spy
    assert not [c for c in spy if isinstance(c, tuple) and c[0] == "notify"]


def test_the_picker_rows_carry_the_REPO_so_fuzzy_typing_can_narrow():
    """A picker whose rows do not contain the repo name cannot be typed at —
    `-matching fuzzy` matches on the row TEXT."""
    rows = MO.picker_rows(MO.universe_candidates("12", MO.repo_universe(FAKE_UNIVERSE)))
    assert any("trowelcast" in r for r in rows)
    assert any("spadeworks" in r for r in rows)
    assert len(rows) == 3


def test_a_ONE_ENTRY_universe_is_still_a_CHOICE_and_never_auto_opens(
        spy, monkeypatch):
    """🔴 THE DEFECT THIS PINS, found during development: with exactly one repo
    in the universe the "one candidate → just open it" shortcut fired and
    `zzznosuchrepo#77` opened issue 77 in a completely unrelated repository — a
    confident wrong page, the exact failure this handler exists to prevent.

    A search HIT is evidence about the name; a universe row is only an option."""
    monkeypatch.setattr(MO, "discover_repos",
                        lambda *a, **k: {"spadeworks": "rivalorg/spadeworks"})
    monkeypatch.setattr(MO, "gh_api_repo_search", lambda name: ({}, ""))
    assert MO.main(["zzznosuchrepo#77"]) == 0
    assert ("pick", 1) in spy, spy


def test_dismissing_the_universe_picker_opens_NOTHING(spy, universe, monkeypatch):
    """`pick()`'s contract, preserved exactly: a dismissal is not an error and it
    must not open anything. Asserted on the OPENER, not only on the exit code —
    an exit code cannot tell you a browser was launched."""
    monkeypatch.setattr(MO, "pick", lambda c: "")
    assert MO.main(["zzznosuchrepo#12"]) == 0
    assert not [c for c in spy if isinstance(c, tuple) and c[0] == "open"]


def test_an_EMPTY_universe_degrades_to_the_NAMED_reason_refusal(spy, monkeypatch):
    """🔴 NOT A SILENT EMPTY PICKER. A universe that cannot be read must fall
    back to the refusal that says WHICH empty this is — "gh is not on PATH" and
    "no repo by that name" need opposite next moves."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: {})
    notices = []
    monkeypatch.setattr(MO, "notify", lambda *a, **k: notices.append(a))

    def boom(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "gh")

    monkeypatch.setattr(MO.subprocess, "run", boom)
    assert MO.main(["zzznosuchrepo#12"]) == 1
    assert "gh is not on PATH" in notices[-1][1]
    assert not [c for c in spy if isinstance(c, tuple) and c[0] == "pick"]


def test_an_UNREADABLE_mapping_degrades_the_same_way(tmp_path, monkeypatch):
    """The universe is built from a file. An unreadable one is an empty one, and
    an empty one is the refusal above — never a picker with no rows.

    🔴 NO `spy` FIXTURE HERE, deliberately: `spy` stubs `discover_repos`, which
    is the very code path whose file-reading this test is about. Using it would
    make the test green against a handler that never opens the file at all."""
    bad = tmp_path / "known_repos.json"
    bad.write_text("not json at all")
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", bad)
    monkeypatch.setattr(MO, "WORKSPACE", tmp_path / "no-such-workspace")
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "")
    monkeypatch.setattr(MO, "gh_api_repo_search", lambda name: ({}, ""))
    picks, opens, notices = [], [], []
    monkeypatch.setattr(MO, "pick", lambda c: picks.append(len(c)) or "")
    monkeypatch.setattr(MO, "open_url", lambda url: opens.append(url) or 0)
    monkeypatch.setattr(MO, "notify", lambda *a, **k: notices.append(a))
    assert MO.main(["zzznosuchrepo#12"]) == 1
    assert notices[-1][0] == "cannot resolve zzznosuchrepo#12"
    assert picks == [] and opens == []


def test_no_discovery_still_means_resolve_only_what_the_TEXT_carries(spy, universe):
    """🔴 The flag's meaning is unchanged by PASS 4. A host-wide mapping is not
    something the clicked text carries."""
    assert MO.main(["--no-discovery", "zzznosuchrepo#12"]) == 1
    assert not [c for c in spy if isinstance(c, tuple) and c[0] == "pick"]
    assert [c for c in spy if isinstance(c, tuple) and c[0] == "notify"]
    assert not [c for c in spy if isinstance(c, tuple) and c[0] == "open"]


def test_print_mode_never_invokes_the_picker_or_the_universe(spy, universe, capsys):
    """🔴 `--print` exists so a script can read the resolved URL. Answering it
    with several hundred is not an answer, and printing them would ALSO write
    private repository names to stdout."""
    assert MO.main(["--print", "zzznosuchrepo#12"]) == 1
    assert not [c for c in spy if isinstance(c, tuple) and c[0] == "pick"]
    out = capsys.readouterr().out
    assert "trowelcast" not in out and "spadeworks" not in out


def test_print_mode_still_prints_a_resolvable_url(spy, universe, capsys):
    assert MO.main(["--print", "gardenersguild/trowelcast#1065"]) == 0
    assert capsys.readouterr().out.strip() == (
        "https://github.com/gardenersguild/trowelcast/issues/1065")


def test_a_bare_hash_N_with_NO_repo_context_offers_the_universe_BELOW_clawgate(
        spy, universe, monkeypatch):
    """🔴 DEAD END 3, with the common case protected. The clawgate candidate
    stays FIRST, so the 92%-of-mentions bare `#N` is still one Enter away; the
    universe is appended as the way to say "no, GitHub, this repository"."""
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "")
    assert MO.main(["#370"]) == 0
    assert ("pick", 4) in spy, spy
    assert spy[-1] == ("open", "https://clawgate.zacx.dev/tasks/370"), (
        "the clawgate row must still be the first, default-selected one")


def test_a_bare_hash_N_that_the_PANE_already_attributes_does_NOT_get_the_universe(
        spy, universe):
    """The universe is the LAST resort. A measured pane repo is evidence, and
    burying it under 300 options would be a regression dressed as a feature."""
    assert MO.main(["#370"]) == 0
    assert ("pick", 2) in spy, spy


def test_the_universe_never_reaches_a_LOG_a_SPOOL_or_stderr(spy, universe,
                                                            monkeypatch, capsys):
    """🔴 THE DISCLOSURE GUARD FOR PASS 4. The real universe names private
    repositories; the ONLY place it may go is the operator's rofi window. This
    asserts the rows exist (a positive control — a test that only checked for
    absence would pass against a picker wired to nothing) and that none of them
    reached stdout or stderr."""
    seen = {}

    def spy_pick(cands):
        seen["rows"] = MO.picker_rows(cands)
        return ""

    monkeypatch.setattr(MO, "pick", spy_pick)
    monkeypatch.setattr(MO, "notify", lambda *a, **k: None)
    assert MO.main(["zzznosuchrepo#12"]) == 0
    assert len(seen["rows"]) == 3, "positive control: the picker got real rows"
    captured = capsys.readouterr()
    for name in FAKE_UNIVERSE.values():
        assert name not in captured.out, name
        assert name not in captured.err, name
