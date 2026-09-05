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

# 🔴 THE SAME MODULE OBJECT THE HANDLER IMPORTED, not a second reading of the
# file. `mention-open.py` puts `scripts/collector` on `sys.path` and does
# `from mention_scan import …`, so by the line above `mention_scan` is already in
# `sys.modules`; importing it here binds THAT object. A separately-`exec_module`d
# copy would let the profile-split assertions below compare two independent
# readings and agree while the handler used a third.
import mention_scan as MS  # noqa: E402


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
    notification is available.

    ⚠ `--no-discovery` is what makes this a refusal at all. Without it a
    six-digit number now OPENS THE PICKER (see the six-digit tests below); the
    flag bars the picker by contract, so the named refusal is what remains."""
    r = _run("--print", "--no-discovery", "#282828")
    assert r.returncode == 1
    assert "cannot resolve" in r.stderr
    assert "resolves only what the text itself carries" in r.stderr


def test_text_with_no_NUMBER_at_all_still_gets_the_original_refusal():
    """The one place `no mention in the clicked text` survives verbatim: there is
    no number, so there is nothing a picker could even offer.

    ⚠ UNREACHABLE FROM A CLICK. The Alacritty hint regex requires `#[0-9]{1,6}`
    or a ClickUp id, so nothing digitless is ever underlined — this is the
    command line only."""
    r = _run("--print", "background = teal;")
    assert r.returncode == 1
    assert "no mention in the clicked text" in r.stderr


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


def test_a_non_mention_OFFERS_THE_PICKER_instead_of_refusing(spy):
    """🔴 THE REVERSAL THIS CHANGE EXISTS FOR. `#282828` used to produce the
    toast `no mention in the clicked text`, which reads as the handler being
    broken when it is the guard working correctly — and a guard that reads as a
    bug is one the next maintainer deletes.

    The picker is now offered instead. Nothing is opened without a selection, and
    the number never becomes a reference: see
    `test_a_SIX_DIGIT_number_is_OFFERED_but_NEVER_auto_opened`."""
    # 🔴 THE MESSAGE IS ON THE EXIT CODE, which is what a reverted measurement
    # pass turns to 1 — the picker assertion below never evaluates in that case.
    assert MO.main(['background = "#282828";']) == 0, (
        "a six-digit click DEAD-ENDED instead of offering the picker")
    assert ("pick", 1) in spy, (
        f"a six-digit click DEAD-ENDED instead of offering the picker: {spy}")
    assert not [c for c in spy if isinstance(c, tuple) and c[0] == "notify"], (
        "a six-digit click DEAD-ENDED instead of offering the picker")


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
# 🔴 THE CLICK PATH MAKES NO NETWORK CALL
#
# A GitHub-WIDE namesake search used to live between the local sources and the
# picker: `gh api search/repositories`, consulted for any `repo#N` the mapping
# and the checkouts could not name. MEASURED on the deployed copy, `dashboard#12`
# cost 4.3s through `--print` and ~10s through the real hint path at 6% CPU —
# all of it network wait — and returned `vzhong/dashboard`, `yorkie-team/
# dashboard`, `zce/dashboard`: strangers' repositories the operator would never
# pick. It searched a corpus that structurally could not hold the answer.
#
# It is DELETED. These tests exist so it cannot come back by accident, and so no
# OTHER network call can take its place: the check is a LEDGER of every command
# the resolution path may spawn, which fails when the set GROWS or SHRINKS, not a
# ban on the single word `gh` that any other client would walk straight past.
# --------------------------------------------------------------------------- #

# Every command the resolution path is allowed to spawn, as (argv[0], argv[1]).
#
# 🔴 THE SUBCOMMAND IS PART OF THE LEDGER, NOT DECORATION. `git` alone would
# admit `git ls-remote` and `git fetch`, which are network calls wearing the name
# of a local tool — the exact substitution a ban on `gh` invites. Both entries
# here are local-only reads.
RESOLUTION_PATH_COMMANDS = {
    ("tmux", "display-message"),   # which pane the operator was last in
    ("git", "rev-parse"),          # that pane's checkout root
    ("git", "remote"),             # `remote get-url origin` — reads .git/config
}


def _assert_only_local_commands(seen: list[list[str]]) -> None:
    """Fail unless `seen` is exactly the ledger above.

    🔴 BOTH DIRECTIONS. A GROWN set is a new command nobody vetted — a network
    call, most likely. A SHRUNK set means the path under test never ran the
    measurement at all, which would make every absence below vacuous.
    """
    got = {(c[0], c[1]) for c in seen if len(c) >= 2}
    assert got == RESOLUTION_PATH_COMMANDS, (
        f"the resolution path's command ledger MOVED: {sorted(got)}")


def test_the_no_network_assertion_can_actually_FIRE():
    """🔴 POSITIVE CONTROL ON THE ASSERTION ITSELF, not on the code it guards.
    `_assert_only_local_commands` is the whole instrument; until it has been
    watched to reject something, a green run from it is indistinguishable from a
    check wired to nothing. Feed it the exact argv the deleted search used."""
    with pytest.raises(AssertionError, match="ledger MOVED"):
        _assert_only_local_commands([
            ["tmux", "display-message", "-p"],
            ["git", "rev-parse", "--show-toplevel"],
            ["git", "remote", "get-url", "origin"],
            ["gh", "api", "search/repositories", "-f", "q=dashboard in:name"],
        ])
    # …and in the other direction: a path that measured nothing.
    with pytest.raises(AssertionError, match="ledger MOVED"):
        _assert_only_local_commands([])


def test_the_resolution_path_spawns_ONLY_these_local_commands(tmp_path, monkeypatch):
    """🔴 THE GUARD. Drives the FULL unresolvable path — the one that used to
    reach the network — against a real mapping file and a real workspace, with
    `subprocess.run` recording instead of executing, and asserts the ledger.

    Nothing here stubs `discover_repos` or `tmux_pane_repo`: those ARE the
    subject. A fixture that replaced them would leave this test green against a
    handler that phoned home from either one.
    """
    # A real mapping and a real checkout, so both measurement legs do work.
    mapping = tmp_path / "known_repos.json"
    mapping.write_text(json.dumps({"plotwidget": "hobbyist/plotwidget"}))
    ws = tmp_path / "workspace"
    (ws / "spadeworks" / ".git").mkdir(parents=True)
    pane = tmp_path / "pane"
    pane.mkdir()
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", mapping)
    monkeypatch.setattr(MO, "WORKSPACE", ws)

    seen: list[list[str]] = []

    def record(cmd, **kwargs):
        seen.append(list(cmd))
        # Answer each leg plausibly so the NEXT one runs. A fake that returns
        # failure would truncate the path and shrink the ledger silently.
        if cmd[:2] == ["tmux", "display-message"]:
            out = str(pane)
        elif cmd[:2] == ["git", "rev-parse"]:
            out = str(pane)
        else:
            out = "git@github.com:rivalorg/spadeworks.git"
        return types.SimpleNamespace(returncode=0, stdout=out + "\n", stderr="")

    def no_launch(argv, *a, **k):
        raise AssertionError(f"nothing may be launched: {argv!r}")

    monkeypatch.setattr(MO.subprocess, "run", record)
    monkeypatch.setattr(MO.subprocess, "Popen", no_launch)
    monkeypatch.setattr(MO, "pick", lambda c: "")
    monkeypatch.setattr(MO, "notify", lambda *a, **k: None)

    assert MO.main(["zzznosuchrepo#12"]) == 0
    _assert_only_local_commands(seen)
    # POSITIVE CONTROL: the fan-out really ran over the real workspace, so the
    # ledger above is a fact about a path that did the work, not about a
    # short-circuit. One checkout ⇒ one `git remote` call.
    assert [c for c in seen if c[:2] == ["git", "remote"]], seen


# Every executable the module may ever spawn, from ANY function. Wider than
# `RESOLUTION_PATH_COMMANDS` because it also covers the ACTION half — opening a
# browser, raising rofi, sending a notification — which no resolution runs.
SPAWNABLE_EXECUTABLES = {"git", "tmux", "rofi", "xdg-open", "notify-send"}


def _spawned_executables(source: str) -> set[str]:
    """argv[0] of every `subprocess.run`/`Popen` call in `source`, read from the
    AST.

    🔴 AST, NOT grep. The module's docstring NAMES `gh api search/repositories`
    at length — that paragraph is the record of why the call was deleted, and it
    is the single most valuable thing in the file for the next maintainer. A
    substring check would either fail on that prose or force it to be softened
    into uselessness, which is how a hazard note gets deleted to make a test
    pass. The AST sees calls; it cannot see comments or docstrings at all.
    """
    import ast
    out: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("run", "Popen") or not node.args:
            continue
        argv = node.args[0]
        if isinstance(argv, (ast.List, ast.Tuple)) and argv.elts:
            head = argv.elts[0]
            out.add(head.value if isinstance(head, ast.Constant) else "<computed>")
        else:
            out.add("<computed>")
    return out


def test_the_spawned_executable_LEDGER_can_actually_fire():
    """POSITIVE CONTROL on the reader, before its verdict is believed. An AST
    walk that matched nothing would report a clean empty set forever."""
    found = _spawned_executables(
        "import subprocess\n"
        "subprocess.run(['gh', 'api', 'search/repositories'])\n")
    assert found == {"gh"}, found
    assert _spawned_executables("import subprocess\nx = 1\n") == set()


def test_the_module_no_longer_carries_a_repository_SEARCH_at_all():
    """🔴 STRUCTURAL, not behavioural — and deliberately both. The ledger test
    above proves no search RAN on one path; this proves there is no search to run
    on ANY path, including ones no test drives.

    It is a ledger rather than a ban on the word `gh`, for the reason
    `RESOLUTION_PATH_COMMANDS` is: a `curl`, a `python -c`, or a second `gh`
    subcommand would all walk straight past a check that only knows one name.
    """
    assert not hasattr(MO, "gh_api_repo_search")
    found = _spawned_executables(HANDLER.read_text())
    assert found, "positive control: the module DOES spawn things"
    assert found <= SPAWNABLE_EXECUTABLES, (
        f"mention-open.py spawns something new: {sorted(found - SPAWNABLE_EXECUTABLES)}")


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
    checkout, and there is no network fallback left, so the mapping is the ONLY
    thing that can produce this URL."""
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH",
                        _write_mapping(tmp_path, {"plotwidget": "gardenersguild/plotwidget"}))
    monkeypatch.setattr(MO, "WORKSPACE", tmp_path / "no-such-workspace")
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "")
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


# The picker's size, and the empty results that must name their cause
# --------------------------------------------------------------------------- #
def test_a_WALL_of_repos_reaches_the_picker_because_it_can_be_TYPED_at(
        spy, monkeypatch):
    """🔴 THE REVERSAL, NOW OVER THE LOCAL UNIVERSE. This used to refuse above 8
    candidates, on the reasoning that "a 100-row list of URLs differing only by
    owner is not a choice, it is a wall". That is true of a list you can only
    SCROLL and false of one you can TYPE AT, and `pick()` runs rofi with
    `-matching fuzzy`. So the wall is a narrowing, and refusing would remove the
    operator's ability to choose — which matters far more now that the universe
    is the operator's own 369-repo mapping rather than a page of search results.

    17 rows, not 9: the old cap was 8, so a fixture of 9 would sit one step past
    a boundary that no longer exists and could not tell a re-added cap of 16
    from no cap at all."""
    monkeypatch.setattr(MO, "discover_repos",
                        lambda *a, **k: {f"n{i}": f"owner{i}/trowelcast"
                                         for i in range(17)})
    assert MO.main(["zzznosuchrepo#77"]) == 0
    assert ("pick", 17) in spy, spy
    assert not [c for c in spy if isinstance(c, tuple) and c[0] == "notify"]


def test_eight_repos_still_offer_the_picker(spy, monkeypatch):
    """The case that worked under the old cap must keep working under no cap —
    a reversal that broke the legitimate side would be the same outage wearing
    the opposite hat."""
    monkeypatch.setattr(MO, "discover_repos",
                        lambda *a, **k: {f"n{i}": f"owner{i}/trowelcast"
                                         for i in range(8)})
    assert MO.main(["zzznosuchrepo#77"]) == 0
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


@pytest.mark.parametrize("state,write,expected", [
    ("absent", None, "has no repo mapping"),
    ("unreadable", "not json at all", "could not be read"),
    ("no usable rows", json.dumps({"widget": "acme"}), "holds no usable rows"),
])
def test_universe_reason_names_WHICH_empty_it_is(tmp_path, state, write, expected):
    """🔴 THREE STATES, ONE EMPTY DICT, THREE DIFFERENT NEXT MOVES. `{}` is what
    `load_known_repos` returns for all of them by design, so the reason cannot be
    recovered from its value — and reporting one sentence for all three is the
    same silent zero as the deleted search's empty result, which could not tell
    "no such repo" from "gh is missing".

    The third row is a mapping that PARSED and yielded nothing usable
    (`"acme"` is one segment, so `clean_repo_map` drops it) — a `gh auth`
    problem, not a mention-open one, and structurally invisible to a check that
    only asks whether the file exists."""
    p = tmp_path / "known_repos.json"
    if write is not None:
        p.write_text(write)
    reason = MO.universe_reason(p)
    assert expected in reason, f"{state}: {reason!r}"
    assert "regen-known-repos.py" in reason, "the reason names no next move"


def test_universe_reason_is_EMPTY_when_the_mapping_is_fine(tmp_path):
    """The positive control for the three rows above: a readable mapping with a
    usable row must produce NO reason, or every one of them would pass against a
    function that returns a complaint unconditionally."""
    p = tmp_path / "known_repos.json"
    p.write_text(json.dumps({"plotwidget": "hobbyist/plotwidget"}))
    assert MO.universe_reason(p) == ""


def test_universe_reason_never_names_a_ROW(tmp_path):
    """🔴 IT IS A NOTIFY BODY, AND `notify()` WRITES TO stderr AND notify-send.
    "the mapping holds only hobbyist/plotwidget" is a natural-looking
    improvement that discloses a private repository name."""
    p = tmp_path / "known_repos.json"
    p.write_text(json.dumps({"widget": "acme"}))
    reason = MO.universe_reason(p)
    assert "acme" not in reason and "widget" not in reason, reason


def test_the_refusal_keeps_the_ADVICE_when_it_also_names_a_cause(spy, monkeypatch,
                                                                 tmp_path):
    """🔴 The case that names a cause is exactly the case where writing
    `owner/repo#N` is the workaround — so naming the cause must ADD to the
    advice, never replace it. It replaced it once."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: {})
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", tmp_path / "nothing-here.json")
    notices = []
    monkeypatch.setattr(MO, "notify", lambda *a, **k: notices.append(a))
    assert MO.main(["zzznosuchrepo#77"]) == 1
    body = notices[-1][1]
    assert "has no repo mapping" in body
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


# --------------------------------------------------------------------------- #
# PASS 3 — THE FUZZY LOCAL UNIVERSE
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
    """A three-entry synthetic universe — so anything that resolves below did so
    through the local universe and nothing else."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: dict(FAKE_UNIVERSE))
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

    A mapping hit is evidence about the name; a universe row is only an option."""
    monkeypatch.setattr(MO, "discover_repos",
                        lambda *a, **k: {"spadeworks": "rivalorg/spadeworks"})
    assert MO.main(["zzznosuchrepo#77"]) == 0
    assert ("pick", 1) in spy, spy


def test_dismissing_the_universe_picker_opens_NOTHING(spy, universe, monkeypatch):
    """`pick()`'s contract, preserved exactly: a dismissal is not an error and it
    must not open anything. Asserted on the OPENER, not only on the exit code —
    an exit code cannot tell you a browser was launched."""
    monkeypatch.setattr(MO, "pick", lambda c: "")
    assert MO.main(["zzznosuchrepo#12"]) == 0
    assert not [c for c in spy if isinstance(c, tuple) and c[0] == "open"]


def test_an_EMPTY_universe_degrades_to_the_NAMED_reason_refusal(spy, monkeypatch,
                                                                tmp_path):
    """🔴 NOT A SILENT EMPTY PICKER. A universe that cannot be read must fall
    back to the refusal that says WHICH empty this is — "no mapping on this host"
    and "the mapping is corrupt" need opposite next moves."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: {})
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", tmp_path / "nothing-here.json")
    notices = []
    monkeypatch.setattr(MO, "notify", lambda *a, **k: notices.append(a))
    assert MO.main(["zzznosuchrepo#12"]) == 1
    assert "has no repo mapping" in notices[-1][1]
    assert not [c for c in spy if isinstance(c, tuple) and c[0] == "pick"]
    # 🔴 EXACTLY ONE NOTIFICATION. The refusal already names the cause, so the
    # universe pass must not ALSO announce something on the way past — and this
    # is the assertion that makes the `and universe` guard load-bearing rather
    # than decorative. Measured: without it, dropping `and universe` SURVIVED the
    # whole file.
    assert len(notices) == 1, notices


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
    picks, opens, notices = [], [], []
    monkeypatch.setattr(MO, "pick", lambda c: picks.append(len(c)) or "")
    monkeypatch.setattr(MO, "open_url", lambda url: opens.append(url) or 0)
    monkeypatch.setattr(MO, "notify", lambda *a, **k: notices.append(a))
    assert MO.main(["zzznosuchrepo#12"]) == 1
    assert notices[-1][0] == "cannot resolve zzznosuchrepo#12"
    # 🔴 THE REASON DISTINGUISHES CORRUPT FROM ABSENT. Both produce `{}`, and the
    # test above covers absent — asserting only "cannot resolve" here would pass
    # against a handler that reported the wrong one.
    assert "could not be read" in notices[-1][1], notices[-1]
    assert picks == [] and opens == []


def test_no_discovery_still_means_resolve_only_what_the_TEXT_carries(spy, universe):
    """🔴 The flag's meaning is unchanged by the universe pass. A host-wide
    mapping is not something the clicked text carries."""
    assert MO.main(["--no-discovery", "zzznosuchrepo#12"]) == 1
    assert not [c for c in spy if isinstance(c, tuple) and c[0] == "pick"]
    assert [c for c in spy if isinstance(c, tuple) and c[0] == "notify"]
    assert not [c for c in spy if isinstance(c, tuple) and c[0] == "open"]


@pytest.mark.parametrize("flag,expected", [
    ("--no-discovery", "resolves only what the text itself carries"),
    ("--print", "there is nobody to ask"),
])
def test_a_flag_that_BARS_the_picker_says_so_by_NAME(monkeypatch, universe,
                                                     flag, expected):
    """🔴 THE THIRD WAY THE PICKER CANNOT BE SHOWN, and it is not an empty
    universe — the universe here is fine and non-empty. A refusal reporting "no
    repo mapping on this host" would send the operator to run
    `regen-known-repos.py` over a mapping that was never the problem.

    Both flags are named in the body because they are the operator's own lever:
    drop the flag and the picker appears."""
    notices = []
    monkeypatch.setattr(MO, "notify", lambda *a, **k: notices.append(a))
    assert MO.main([flag, "zzznosuchrepo#12"]) == 1
    assert expected in notices[-1][1], notices[-1]
    assert "regen-known-repos" not in notices[-1][1], (
        "blamed the mapping for a refusal the FLAG caused")


def test_print_mode_never_invokes_the_picker_or_the_universe(spy, universe, capsys):
    """🔴 `--print` exists so a script can read the resolved URL. Answering it
    with several hundred is not an answer, and printing them would ALSO write
    private repository names to stdout."""
    assert MO.main(["--print", "zzznosuchrepo#12"]) == 1
    assert not [c for c in spy if isinstance(c, tuple) and c[0] == "pick"], (
        "--print raised the interactive picker")
    out = capsys.readouterr().out
    assert "trowelcast" not in out and "spadeworks" not in out, (
        "the UNIVERSE reached --print's stdout")


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


# --------------------------------------------------------------------------- #
# 🔴 THE SIX-DIGIT NUMBER: OFFERED, NEVER AUTO-OPENED
#
# `mention_scan._NUM` is `\d{1,5}` so a hex colour can never become a reference.
# That guard is UNCHANGED and still decides what may be OPENED. What changed is
# what may be SHOWN: the same click now opens a dismissible picker instead of a
# toast that read like a failure. The two must stay distinguishable in the code,
# and these tests are what keeps them so.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,expected", [
    ("#282828", "282828"),                  # the hex colour that started this
    ('background = "#282828";', "282828"),  # …as it actually arrives from a click
    ("#7", "7"),
    ("#1234567", "123456"),                 # bounded at 6, like the hint regex
    ("no number here", ""),
    ("", ""),
])
def test_offer_number_recovers_a_number_the_SCANNER_refused(text, expected):
    assert MO.offer_number(text) == expected


def test_offer_number_is_NOT_a_resolution(spy):
    """🔴 THE DISTINCTION, ASSERTED. `offer_number` must not make the scanner
    accept anything: `resolve()` still sees NO mention in a six-digit colour, so
    the number cannot arrive as a span, a candidate, or an auto-open."""
    assert MO.offer_number("#282828") == "282828"
    assert MO.resolve("#282828") == (None, [])


def test_a_SIX_DIGIT_number_is_OFFERED_but_NEVER_auto_opened(spy, monkeypatch):
    """🔴 THE EXACT HAZARD `_NUM = \\d{1,5}` EXISTS FOR, on the new path. With a
    ONE-entry universe the "exactly one candidate → just open it" shortcut is
    live, and firing it here would open issue 282828 of an unrelated repository
    from a click on a colour literal — a confident wrong page produced by the
    very change meant to be friendlier.

    A one-entry universe is the fixture on purpose: any larger one reaches the
    picker through the plural branch and could not see this regression at all."""
    monkeypatch.setattr(MO, "discover_repos",
                        lambda *a, **k: {"spadeworks": "rivalorg/spadeworks"})
    assert MO.main(["#282828"]) == 0
    # 🔴 THE CLAIM IS ORDER, NOT ABSENCE. `spy`'s `pick` answers with row 0, so
    # an `open` here is legitimate — it is what a SELECTION does. What must never
    # happen is an open with no pick in front of it, which is the auto-open
    # shortcut firing. Asserting "no open at all" would instead pass against a
    # handler that had stopped working entirely.
    kinds = [c[0] for c in spy if isinstance(c, tuple)]
    # 🔴 THE MESSAGE IS ON THIS ASSERTION, NOT THE ORDERING ONE BELOW. When the
    # auto-open shortcut fires there is no "pick" at all, so THIS is the line
    # that goes red — and a mutation battery reports a kill for the wrong reason
    # when the phrase it looks for sits on an assertion that never evaluates.
    assert "pick" in kinds, (
        f"a number the SCANNER REFUSED was auto-opened, bypassing the "
        f"picker: {spy}")
    assert kinds.index("pick") < kinds.index("open"), (
        "a number the SCANNER REFUSED was auto-opened, bypassing the picker")


def test_dismissing_the_SIX_DIGIT_picker_opens_nothing(spy, universe, monkeypatch):
    monkeypatch.setattr(MO, "pick", lambda c: "")
    assert MO.main(["#282828"]) == 0
    assert not [c for c in spy if isinstance(c, tuple) and c[0] == "open"]


def test_a_SELECTED_six_digit_row_opens_the_repo_the_operator_CHOSE(
        spy, universe, monkeypatch):
    """The other half: offering it must actually work. A picker that cannot open
    what was selected is the refusal again, wearing a window.

    `hobbyist/plotwidget` is the SECOND row of the sorted universe, not the
    first — so a mutant that ignores the selection and takes row 0 cannot land on
    this URL by accident."""
    monkeypatch.setattr(
        MO, "pick",
        lambda c: "https://github.com/hobbyist/plotwidget/issues/282828")
    assert MO.main(["#282828"]) == 0
    assert spy[-1] == (
        "open", "https://github.com/hobbyist/plotwidget/issues/282828")


def test_a_bare_hash_N_that_the_PANE_already_attributes_does_NOT_get_the_universe(
        spy, universe):
    """The universe is the LAST resort. A measured pane repo is evidence, and
    burying it under 300 options would be a regression dressed as a feature."""
    assert MO.main(["#370"]) == 0
    assert ("pick", 2) in spy, spy


@pytest.fixture
def real_notify(monkeypatch):
    """Let the REAL `MO.notify` run, capturing what it hands `notify-send`.

    🔴 THIS FIXTURE IS THE FIX FOR A GUARD THAT COULD NOT SEE ITS OWN SUBJECT.
    The disclosure test below used to stub `MO.notify` to a no-op — and `notify`
    is the ONLY function in the module that writes to stderr or to a desktop
    notification. So "none of them reached stdout or stderr" was a claim about a
    path the test had removed: adding the universe to any refusal's `notify` body
    survived the whole suite. Stubbing `subprocess.run` instead keeps the real
    formatting, the real `print(..., file=sys.stderr)` and the real argv, while
    still launching nothing.
    """
    sent: list[list[str]] = []

    def fake_run(argv, *a, **k):
        sent.append(list(argv))
        raise AssertionError(
            f"no subprocess may launch from these tests: {argv!r}")

    def capture(argv, *a, **k):
        sent.append(list(argv))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(MO.subprocess, "run", capture)
    monkeypatch.setattr(MO.subprocess, "Popen", fake_run)
    return sent


# The universe rows may reach the operator's rofi window and NOWHERE else, so
# every sink the handler can write to is enumerated here rather than left to
# whichever one a test happened to think of. A sink missing from this list is a
# hole; adding one to the module means adding it here.
def _every_sink(capsys, notify_argv: list[list[str]]) -> str:
    captured = capsys.readouterr()
    return "\n".join([captured.out, captured.err,
                      *(" ".join(a) for a in notify_argv)])


def test_the_universe_never_reaches_a_LOG_a_SPOOL_or_stderr(spy, universe,
                                                            monkeypatch, capsys,
                                                            real_notify):
    """🔴 THE DISCLOSURE GUARD FOR PASS 4. The real universe names private
    repositories; the ONLY place it may go is the operator's rofi window. This
    asserts the rows exist (a positive control — a test that only checked for
    absence would pass against a picker wired to nothing) and that none of them
    reached stdout, stderr or a desktop notification."""
    seen = {}

    def spy_pick(cands):
        seen["rows"] = MO.picker_rows(cands)
        return ""

    monkeypatch.setattr(MO, "pick", spy_pick)
    assert MO.main(["zzznosuchrepo#12"]) == 0
    assert len(seen["rows"]) == 3, "positive control: the picker got real rows"
    everywhere = _every_sink(capsys, real_notify)
    for name in FAKE_UNIVERSE.values():
        assert name not in everywhere, f"PICKER-PATH DISCLOSURE: {name}"


def test_the_REFUSAL_path_names_the_clicked_text_and_never_the_universe(
        universe, monkeypatch, capsys, real_notify):
    """🔴 THE SECOND HALF OF THE SAME GUARD, on the path PASS 4 never reaches.

    `--print` skips PASS 4 entirely, so the handler refuses — but `discovered`
    was already populated by PASS 2 and holds the whole universe. That refusal
    goes through `notify()`, which prints to stderr AND to `notify-send`. Adding
    the universe to that body — "no repo by that name; did you mean one of
    these?" is a natural-looking improvement — leaked every private name, with
    the previous version of this file green.

    The positive controls come FIRST: the refusal really happened, and the
    handler really held the universe at that moment. Without them a stubbed-out
    run that refused for some other reason would satisfy every absence below."""
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "")
    assert MO.main(["--print", "zzznosuchrepo#77"]) == 1
    everywhere = _every_sink(capsys, real_notify)
    # POSITIVE CONTROL 1 — this IS the refusal path, and it named the click.
    assert "cannot resolve zzznosuchrepo#77" in everywhere
    # POSITIVE CONTROL 2 — the universe was in hand and NOT empty at that point,
    # so its absence below is a decision rather than an accident of the fixture.
    assert MO.repo_universe(dict(FAKE_UNIVERSE)) == sorted(FAKE_UNIVERSE.values())
    for name in FAKE_UNIVERSE.values():
        assert name not in everywhere, f"REFUSAL-PATH DISCLOSURE: {name}"


def test_print_mode_REFUSES_an_unresolvable_reference_rather_than_listing_repos(
        monkeypatch, universe, capsys):
    """🔴 A DECISION, RECORDED, AND IT IS A BEHAVIOUR CHANGE. Deleting the
    GitHub-wide search changed `--print` too: `dashboard#12` used to exit 0 and
    print one URL per namesake, and now exits 1 with a named reason.

    That is intended, and it is a correction rather than a loss. Those namesakes
    were `vzhong/dashboard`, `yorkie-team/dashboard`, `zce/dashboard` — an exit 0
    carrying strangers' repositories is a WRONG answer dressed as an answer, and
    a consumer parsing it got a plausible URL into somebody else's issue tracker.

    `--print` keeps its contract for real ambiguity — several candidates for a
    reference the text or the host actually attributes still print, see
    `test_an_ambiguous_click_prints_every_candidate`. What it will not do is ASK,
    because it is non-interactive: the universe is a question, not an answer, and
    printing 369 URLs is not one either. A consumer wanting one answer writes
    `owner/repo#N`."""
    # 🔴 THE MESSAGE IS ON THE EXIT CODE. If `--print` is allowed to offer the
    # universe it exits 0 with URLs, so this is the line that goes red first and
    # the stdout assertion below never runs.
    assert MO.main(["--print", "zzznosuchrepo#12"]) == 1, (
        "a refusal must print no URL at all — --print offered the universe")
    assert capsys.readouterr().out == "", "a refusal must print no URL at all"


# --------------------------------------------------------------------------- #
# 🔴 THE PROFILE SPLIT, AT THE CONSUMER
#
# `mention_scan`'s default profile is `terminal`, and this handler's single
# `scan_mention_spans` call passes no `profile=` — that omission is the entire
# reason the wider telemetry surface never becomes clickable. It was pinned at
# the module and NOWHERE here: an independent mutation sweep added
# `profile="telemetry"` to `resolve()` and the whole suite stayed green.
# --------------------------------------------------------------------------- #
def test_every_TELEMETRY_only_shape_is_invisible_to_the_click_handler():
    """Driven off `PATTERN_LEDGER`, not a hand-written list, so a telemetry-only
    pattern added later is covered without anyone remembering this file.

    Each sample carries its own positive control: the SAME text must produce a
    span at the telemetry profile. Without that half, a sample the scanner had
    stopped matching entirely would pass as "invisible to the click"."""
    telemetry_only = {
        name: pat for name, pat in MS.PATTERN_LEDGER.items()
        if pat.role == "detect" and MS.PROFILE_TERMINAL not in pat.profiles}
    assert telemetry_only, "positive control: there ARE telemetry-only patterns"
    for name, pat in sorted(telemetry_only.items()):
        assert MS.scan_mention_spans(pat.sample, profile=MS.PROFILE_TELEMETRY), (
            f"{name}: the ledger sample must match at the telemetry profile")
        assert MO.resolve(pat.sample) == (None, []), (
            f"{name}: a telemetry-only shape reached the CLICK surface — "
            f"resolve() is scanning at the wrong profile")
