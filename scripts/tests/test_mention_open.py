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
# PASS 3 — the GitHub API fallback
#
# 🔴 This path shipped INERT. Its jq program compared `.name` against the
# LITERAL text `"$name"` (a Python string with no f-prefix), so it selected
# nothing for any input, and the only symptom was the same "cannot resolve"
# notification an unknown repo produces anyway. Nothing here mocks the
# selection: the fake returns the rows `gh` would print and the assertions
# pin WHICH row the resolver picks.
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

    The rows are RELEVANCE-ordered, exactly as the search endpoint returns
    them — the exact-name match is deliberately NOT first."""
    seen: dict = {}

    def run(cmd, **kwargs):
        seen["cmd"] = list(cmd)
        return types.SimpleNamespace(
            returncode=returncode, stdout="".join(f"{r}\n" for r in rows), stderr="")

    return run, seen


# Fixture rows share no substring with any assertion constant: the owner that
# must WIN (`gardenersguild`) appears in no other row, and every near-miss name
# differs from the searched name by a suffix, so a mutant that returns the first
# row, or that matches on a prefix, cannot land on the expected value.
_SEARCH_ROWS = [
    "hobbyist/trowelcast-examples",
    "someoneelse/trowelcast-mirror",
    "gardenersguild/trowelcast",
    "thirdparty/trowelcast-fork",
]


def test_the_api_fallback_takes_the_EXACT_name_match_not_the_first_row(monkeypatch):
    """🔴 Relevance order is not name order. Taking `splitlines()[0]` opens a
    stranger's repo under the operator's own repo name."""
    run, seen = _fake_gh(_SEARCH_ROWS)
    monkeypatch.setattr(MO.subprocess, "run", run)
    assert MO._gh_api_repo_search("trowelcast") == {"trowelcast": "gardenersguild/trowelcast"}
    assert seen["cmd"][:3] == ["gh", "api", "search/repositories"]


def test_the_api_fallback_matches_a_name_case_insensitively(monkeypatch):
    """GitHub repo names are case-insensitive; the static mapping carries both
    spellings for exactly this reason and the fallback must not regress it."""
    run, _ = _fake_gh(["hobbyist/TrowelCast-ui", "gardenersguild/TrowelCast"])
    monkeypatch.setattr(MO.subprocess, "run", run)
    assert MO._gh_api_repo_search("trowelcast") == {"trowelcast": "gardenersguild/TrowelCast"}


def test_the_api_fallback_returns_NOTHING_when_no_row_names_the_repo(monkeypatch):
    """🔴 NOTHING IS GUESSED (the module docstring's rule). A search that finds
    only near-misses must resolve to no candidate at all — not to the closest
    one."""
    run, _ = _fake_gh(["hobbyist/trowelcast-examples", "thirdparty/trowelcast-fork"])
    monkeypatch.setattr(MO.subprocess, "run", run)
    assert MO._gh_api_repo_search("trowelcast") == {}


def test_the_api_fallback_returns_nothing_when_gh_fails(monkeypatch):
    """A non-zero `gh` (no auth, rate limit, no network) prints its error on
    stderr; treating stdout as an answer anyway would resolve to garbage.

    ⚠ INVARIANT GUARD, not regression coverage — this is the one test in this
    section that is GREEN on the pre-fix code (which checked `returncode == 0`
    too). It pins the behaviour across the rewrite; it never caught the bug."""
    run, _ = _fake_gh(_SEARCH_ROWS, returncode=1)
    monkeypatch.setattr(MO.subprocess, "run", run)
    assert MO._gh_api_repo_search("trowelcast") == {}


def test_the_jq_program_is_a_CONSTANT_that_cannot_do_the_selection(monkeypatch):
    """🔴 THE SEAM. Every test above stubs `gh`, so a filter that selects
    nothing in production still passes them — which is precisely how the
    original bug survived. `gh api --jq` accepts no `--arg`, so a name in the
    filter can only be a string literal: assert the program carries neither the
    searched name nor a `$`, i.e. that jq CANNOT be where the match happens."""
    run, seen = _fake_gh(_SEARCH_ROWS)
    monkeypatch.setattr(MO.subprocess, "run", run)
    MO._gh_api_repo_search("trowelcast")
    jq_program = seen["cmd"][seen["cmd"].index("--jq") + 1]
    assert "$" not in jq_program
    assert "trowelcast" not in jq_program


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not on PATH")
def test_the_jq_program_really_yields_the_rows_the_resolver_filters(monkeypatch):
    """The other half of the seam: run the EMITTED program through the REAL jq
    against a realistic search payload, and check the exact-name repo is among
    the lines Python gets. The original filter passes every test above and
    returns zero lines here."""
    run, seen = _fake_gh(_SEARCH_ROWS)
    monkeypatch.setattr(MO.subprocess, "run", run)
    MO._gh_api_repo_search("trowelcast")
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


def test_an_unknown_repo_falls_back_to_the_api_and_opens_the_match(spy, monkeypatch):
    """End-to-end through main(): discovery knows only `talos-infra`, so a
    click on a repo it has never heard of must reach PASS 3 and open the URL
    the search resolved."""
    run, _ = _fake_gh(_SEARCH_ROWS)
    monkeypatch.setattr(MO.subprocess, "run", run)
    assert MO.main(["trowelcast#77"]) == 0
    assert spy[-1] == ("open", "https://github.com/gardenersguild/trowelcast/issues/77")


def test_an_unknown_repo_with_no_exact_match_still_refuses(spy, monkeypatch):
    """PASS 3 must not turn the honest refusal into a confident wrong page."""
    run, _ = _fake_gh(["hobbyist/trowelcast-examples"])
    monkeypatch.setattr(MO.subprocess, "run", run)
    assert MO.main(["trowelcast#77"]) == 1
    assert spy[-1] == ("notify", "cannot resolve trowelcast#77")
