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
import subprocess
import sys
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
        ("clawgate", "https://clawgate.zacx.dev/tasks#task-370")]


def test_a_bare_number_with_a_repo_context_offers_both():
    _span, cands = MO.resolve("#370", default_repo="civitai/talos-infra")
    assert [(c["platform"], c["url"]) for c in cands] == [
        ("clawgate", "https://clawgate.zacx.dev/tasks#task-370"),
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
    assert rows[0].endswith("https://clawgate.zacx.dev/tasks#task-370")
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
        "https://clawgate.zacx.dev/tasks#task-370",
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
    assert spy[-1] == ("open", "https://clawgate.zacx.dev/tasks#task-370")


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
