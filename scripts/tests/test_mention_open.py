"""Tests for `scripts/mention-open.py` — the Alacritty hint handler.

Scope: URL CONSTRUCTION and the resolution decision. Nothing here launches a
browser, a picker or a notification — every impure edge (`xdg-open`, the
picker terminal,
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

import ast
import importlib.util
import json
import os
import re
import select
import shutil
import stat
import subprocess
import sys
import threading
import time
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
        "https://github.com/civitai/talos-infra/pull/1065"]


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
        ("github", "https://github.com/civitai/talos-infra/pull/370"),
    ]


def test_a_measured_repo_mapping_resolves_the_short_form():
    _span, cands = MO.resolve("talos-infra#1065",
                              repos={"talos-infra": "civitai/talos-infra"})
    assert [c["url"] for c in cands] == [
        "https://github.com/civitai/talos-infra/pull/1065"]


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
    assert rows[1].endswith("https://github.com/civitai/talos-infra/pull/370")


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
    assert r.stdout.strip() == "https://github.com/civitai/talos-infra/pull/1065"

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
        "https://github.com/civitai/talos-infra/pull/370",
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
# 🔴 NO TEST MAY READ THE OPERATOR'S REAL `known_repos.json`
#
# `KNOWN_REPOS_PATH` is a module CONSTANT pointing at
# `~/.config/mention-open/known_repos.json` — the file whose committed ancestor
# disclosed 232 private repository names into this PUBLIC repo. Every route into
# it (`load_known_repos`, `universe_reason`, `mapping_age_days`) resolves that
# constant AT CALL TIME, so redirecting it here closes the whole class rather
# than one test at a time.
#
# 🔴 THIS IS A MEASURED HOLE, NOT A PRECAUTION. Before this fixture, NINE tests
# in this file touched the real file — seven `stat()`ing it for the mapping age
# and two `read_text()`ing it — which is why they asserted different things on
# the dev host (a real 369-row mapping) than in the nix sandbox tier (an empty
# HOME, so no file at all). The disclosure guards below are the sharp end: a
# mutant that leaks `load_known_repos()` would, unredirected, leak the
# OPERATOR'S OWN private names while the guard checked for `FAKE_UNIVERSE`'s
# synthetic ones — a leak the tripwire is structurally unable to see.
#
# The redirect writes `FAKE_UNIVERSE` itself, FRESH, so a leaking mutant leaks
# exactly the names the guards assert on. A test needing another mapping state —
# absent, unreadable, stale, holding one row — re-patches the constant, and
# every one that does still does.
#
# 🔴 IT TAKES TWO REDIRECTS, BECAUSE HALF THIS FILE RUNS IN A SUBPROCESS.
# `monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", p)` rebinds an attribute on THIS
# process's module object. `_run()` spawns `mention-open.py` as a REAL child,
# which imports itself from scratch and resolves the constant from
# `MENTION_OPEN_KNOWN_REPOS`/`XDG_CONFIG_HOME` at its own import time — the
# setattr is invisible to it. MEASURED with inotify at the PR head: all four
# mention suites made ZERO opens of the operator's real mapping, but that was an
# accident of which flags the four `_run` tests happened to pass — take
# `--no-discovery` off one existing test and the child made 2 OPEN + 2 ACCESS on
# the real 369-row file while the suite reported 138 passed and the in-process
# control below stayed green, because it inspects an attribute the child never
# consults. The `setenv` is what a subprocess actually inherits, and
# `test_the_autouse_redirect_REACHES_A_SUBPROCESS` is what can see it missing.
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _mapping_is_never_the_operators(monkeypatch, tmp_path):
    p = tmp_path / "autouse-known-repos.json"
    # `FAKE_UNIVERSE` is defined further down; module globals resolve at call
    # time, so the ordering is not a problem and the constant stays beside the
    # comment block that explains it.
    p.write_text(json.dumps(FAKE_UNIVERSE))
    when = time.time() - 1.0 * 86400  # fresh: inside STALE_MAPPING_DAYS
    os.utime(p, (when, when))
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", p)
    # 🔴 THE SUBPROCESS HALF. `mention-open.py`'s `KNOWN_REPOS_PATH` already
    # reads this variable, and a child inherits the environment — so this closes
    # the class for every `_run()` test, present and future, rather than
    # depending on each one remembering to pass `--no-discovery`.
    #
    # ⚠ NAMED, NOT LINE-NUMBERED. This comment used to cite `mention-open.py:135`
    # and the line had already moved to 141 — worse, 135 is now `WORKSPACE`, a
    # DIFFERENT variable, so the reference pointed at the wrong hazard while
    # reading as precise. A constant's name survives an edit above it.
    monkeypatch.setenv("MENTION_OPEN_KNOWN_REPOS", str(p))

    # 🔴 THE SECOND HOST-STATE FILE GETS BOTH REDIRECTS TOO. `known_universe.json`
    # names private repositories exactly as the mapping does, and it feeds the
    # PICKER — the one surface those names are allowed to reach. Redirecting only
    # the mapping would leave every disclosure guard in this file asserting on
    # `FAKE_UNIVERSE`'s synthetic names while the code under test read the
    # operator's real 388-row universe: the guard would be structurally unable
    # to see the leak it is named for. Same two mechanisms, same reason — the
    # `setattr` for in-process reads, the `setenv` for `_run()`'s real child.
    # ⚠ AND IT IS DELIBERATELY NOT WRITTEN. The mapping redirect writes
    # `FAKE_UNIVERSE` so a leaking mutant leaks synthetic names; this one points
    # at a path that does not exist, so `load_known_universe()` returns [] and
    # the universe a test sees is exactly the one its own mapping state implies.
    # Writing a default here was tried and it silently CHANGED NINE EXISTING
    # TESTS: cases built to assert an empty universe — an unreadable mapping, a
    # mapping holding no usable rows — started getting a picker from the file
    # instead of the refusal they were written to pin, so they failed for a
    # reason that had nothing to do with what they test. A redirect must relocate
    # a read, never invent content the test did not ask for. Tests that DO want
    # a universe write one, or patch `load_known_universe`.
    u = tmp_path / "autouse-known-universe.json"
    monkeypatch.setattr(MO, "KNOWN_UNIVERSE_PATH", u)
    monkeypatch.setenv("MENTION_OPEN_KNOWN_UNIVERSE", str(u))

    # 🔴 AND THE WORKSPACE, WHICH WAS THE REMAINING HOLE IN THIS FIXTURE. The
    # two redirects above stop a child reading the operator's FILES; nothing
    # stopped it reading their DISK. `discover_repos()` fans out `git remote
    # get-url` over `DEVRC_WORKSPACE`, which every `_run()` test inherited from
    # the real environment — so the next such test written with digits and
    # without `--no-discovery` walks the operator's real `~/workspace` and reads
    # back real repositories, exactly as the mapping hole did. It was closed
    # per-test; a per-test guard is one forgotten flag away from being no guard.
    monkeypatch.setenv("DEVRC_WORKSPACE", str(tmp_path / "no-checkouts-here"))
    return p


def test_the_autouse_redirect_is_IN_FORCE(tmp_path):
    """The positive control for the fixture above — the IN-PROCESS half only.
    Without it, a redirect that silently stopped applying — a renamed constant,
    a fixture shadowed by a later definition — would leave every in-process test
    in this file reading the operator's real mapping again, and nothing would
    say so.

    ⚠ IT IS BLIND TO THE SUBPROCESS HALF, AND SAYING SO IS THE POINT: it reads a
    module attribute a child process never consults, so it stayed green through
    the whole gap the test below now covers."""
    assert MO.KNOWN_REPOS_PATH.name == "autouse-known-repos.json", (
        f"the autouse redirect is not in force: {MO.KNOWN_REPOS_PATH}")
    assert MO.KNOWN_REPOS_PATH.is_relative_to(tmp_path), MO.KNOWN_REPOS_PATH
    assert MO.KNOWN_REPOS_PATH != Path.home() / ".config/mention-open/known_repos.json"
    # And it really is loadable — an unreadable redirect would make every
    # mapping-state assertion below pass for the wrong reason.
    assert MO.load_known_repos() == FAKE_UNIVERSE


# What a child prints: the path IT resolved, from its own import, with no help
# from this process. `argv[1]` is the handler; the filename has a hyphen so it
# cannot simply be imported by name.
_CHILD_REPORTS_ITS_MAPPING_PATH = (
    "import importlib.util,sys;"
    "spec=importlib.util.spec_from_file_location('mo', sys.argv[1]);"
    "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
    "print(m.KNOWN_REPOS_PATH);print(sorted(m.load_known_repos()))"
)


def test_the_autouse_redirect_REACHES_A_SUBPROCESS(tmp_path):
    """🔴 THE HALF THE CONTROL ABOVE STRUCTURALLY CANNOT SEE, and the reason the
    gap survived a round of review: a control that cannot observe the failure it
    names is not a control.

    Four tests in this file drive the handler through `_run()` — a real child
    process. It re-resolves `KNOWN_REPOS_PATH` at ITS import, so the
    `monkeypatch.setattr` never reached it; those four passed only because each
    happened to carry `--no-discovery` (under which the mapping is never read)
    or no digits. Nothing enforced that, and dropping the flag from one of them
    put 2 OPEN + 2 ACCESS on the operator's real 369-row mapping — measured with
    inotify, positive control fired at 1 — with the suite reporting 138 passed.

    This asserts what the CHILD resolved, so deleting the `setenv` from the
    fixture makes it red rather than leaving the hole invisible."""
    r = subprocess.run([sys.executable, "-c", _CHILD_REPORTS_ITS_MAPPING_PATH,
                        str(HANDLER)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    resolved, loaded = r.stdout.splitlines()[:2]
    child_path = Path(resolved.strip())
    real = Path.home() / ".config/mention-open/known_repos.json"
    assert child_path != real, (
        f"a CHILD of this suite resolves the operator's real mapping: {child_path}")
    assert child_path.is_relative_to(tmp_path), child_path
    assert child_path == MO.KNOWN_REPOS_PATH, (child_path, MO.KNOWN_REPOS_PATH)
    # POSITIVE CONTROL — the child did not merely resolve a path, it READ the
    # fake mapping. A redirect pointing somewhere unreadable would satisfy every
    # absence above while the child fell back to nothing.
    assert loaded.strip() == str(sorted(FAKE_UNIVERSE)), loaded


def test_a_DISCOVERING_subprocess_resolves_through_the_FAKE_mapping(tmp_path,
                                                                    monkeypatch):
    """🔴 THE BEHAVIOURAL HALF, on the exact flag combination that was unguarded.
    Every other `_run()` test passes `--no-discovery`, under which the mapping is
    never read at all — so none of them could have caught the redirect not
    reaching a child. This one DISCOVERS, and answers from a name that exists
    only in `FAKE_UNIVERSE`.

    🔴 TWO REAL EDGES ARE CLOSED BY ARGUMENT RATHER THAN BY STUB, because a
    child process cannot be monkeypatched and this is the only test that lets
    one run the discovery pass:

      * `DEVRC_WORKSPACE` -> an empty directory, so the `git remote` fan-out
        walks nothing. A local checkout WINS over the mapping, so this host's
        real `~/workspace` must not be able to decide a test's answer either
        way — and it also keeps the test off ~100 concurrent agent worktrees.
      * `--default-repo` -> set, which short-circuits `tmux_pane_repo()` in
        `main()` (`args.default_repo or tmux_pane_repo()`). Without it the child
        runs a REAL `tmux display-message` against the operator's live server —
        a side effect in a suite that is supposed to have none, next door to
        tests that drive real tmux sockets. The value is irrelevant to the
        assertion: `loamfield#12` names its repo, so it resolves `mapped`, one
        rung above `default`.
    """
    monkeypatch.setenv("DEVRC_WORKSPACE", str(tmp_path / "no-checkouts-here"))
    r = _run("--print", "--default-repo", "unused/by-this-shape", "loamfield#12")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "https://github.com/gardenersguild/trowelcast/pull/12", (
        "the child did not resolve through the redirected mapping — it either "
        f"read the operator's real one or nothing at all: {r.stdout!r} {r.stderr!r}")


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
    # `mesg=""` mirrors the real signature: `main()` hands the picker a note to
    # show above the list when the universe is the answer. A stub that took only
    # `candidates` would raise TypeError on that path — which is a kill, but for
    # the wrong reason, and it would hide whatever the note actually said.
    monkeypatch.setattr(
        MO, "pick",
        lambda c, mesg="": calls.append(("pick", len(c))) or c[0]["url"])
    monkeypatch.setattr(MO, "notify", lambda *a, **k: calls.append(("notify", a[0])))
    return calls


def test_an_unambiguous_click_opens_without_paying_for_discovery(spy):
    """Discovery is a `git remote` fan-out plus a tmux round-trip. A click whose
    text already carries `owner/repo` must not pay for it — that latency lands
    on the operator every single time."""
    assert MO.main(["civitai/talos-infra#1065"]) == 0
    assert spy == [("open", "https://github.com/civitai/talos-infra/pull/1065")]


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
    monkeypatch.setattr(MO, "pick", lambda c, mesg="": "")
    assert MO.main(["#370"]) == 0
    assert not [c for c in spy if isinstance(c, tuple) and c[0] == "open"]


def test_an_UNEXPECTED_exception_is_REPORTED_rather_than_vanishing(monkeypatch):
    """🔴 THE DETACHED-PROCESS SILENT CLICK. Alacritty spawns this handler with
    no terminal, so an uncaught exception writes a traceback to a stderr nobody
    will ever read and the operator sees a click that did nothing — the exact
    shape `notify()` exists to prevent, arriving one level above it.

    The case that is not hypothetical: `ThreadPoolExecutor` raises
    `RuntimeError: can't start new thread` when the box is at its thread limit,
    and this host routinely runs 100+ concurrent agent worktrees.

    The message must carry the exception TYPE and TEXT — "something went wrong"
    would be the silent zero with a face on it."""
    notices = []
    monkeypatch.setattr(MO, "notify", lambda *a, **k: notices.append(a))

    def boom(argv=None):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(MO, "main", boom)
    assert MO.guarded_main(["#370"]) == 1
    assert notices, (
        "an exception escaped the handler: a DETACHED click then does nothing "
        "at all — no toast, no readable stderr")
    assert "RuntimeError" in notices[-1][1], notices[-1]
    assert "can't start new thread" in notices[-1][1], notices[-1]


def test_the_guard_does_not_swallow_a_NORMAL_run(spy):
    """The negative control: `guarded_main` must be transparent when nothing
    goes wrong, or every assertion above would hold for a wrapper that always
    reported a failure."""
    assert MO.guarded_main(["civitai/talos-infra#1065"]) == 0
    assert spy == [("open", "https://github.com/civitai/talos-infra/pull/1065")]


def test_the_ENTRY_POINT_is_the_guarded_one():
    """🔴 A GUARD NOTHING CALLS IS NOT A GUARD. `guarded_main` is only reached if
    `__main__` actually uses it; leaving `raise SystemExit(main())` in place
    would keep every test above green while production kept vanishing. Read from
    the SOURCE, because the `__main__` block never executes under pytest."""
    src = HANDLER.read_text()
    assert re.search(r'if __name__ == "__main__":\s*\n\s*raise SystemExit\('
                     r"guarded_main\(\)\)", src), (
        "scripts/mention-open.py's __main__ block does not call guarded_main() "
        "— an unexpected exception is a silent click again")


def test_a_short_form_repo_is_resolved_from_the_discovered_checkouts(spy):
    assert MO.main(["talos-infra#1065"]) == 0
    assert "discover" in spy
    assert spy[-1] == ("open", "https://github.com/civitai/talos-infra/pull/1065")


def test_text_the_SCANNER_REFUSED_offers_the_picker_instead_of_refusing(spy):
    """🔴 THE REVERSAL THIS CHANGE EXISTS FOR. Text the strict scanner rejects
    used to produce the toast `no mention in the clicked text`, which reads as
    the handler being broken when it is the guard working correctly — and a
    guard that reads as a bug is one the next maintainer deletes.

    `##370` is such a text: `BARE_RE`'s left guard refuses a `#` run, so
    `resolve()` returns no span, while `offer_number` still recovers `370`.

    ⚠ NOT `#282828`, AND THE CHANGE OF FIXTURE IS THE POINT. A SIX-digit number
    now gets a named toast rather than a picker — see
    `test_a_SIX_DIGIT_click_gets_a_NAMED_toast_not_a_wall_of_repos`. This test is
    about the `span is None` arm of the measurement pass, which the six-digit
    branch no longer exercises, so it needs a non-six-digit refusal to stay a
    guard rather than a duplicate."""
    # 🔴 THE MESSAGE IS ON THE EXIT CODE, which is what a reverted measurement
    # pass turns to 1 — the picker assertion below never evaluates in that case.
    assert MO.main(["##370"]) == 0, (
        "a scanner-refused click DEAD-ENDED instead of offering the picker")
    assert ("pick", 1) in spy, (
        f"a scanner-refused click DEAD-ENDED instead of offering the "
        f"picker: {spy}")
    assert not [c for c in spy if isinstance(c, tuple) and c[0] == "notify"], (
        "a scanner-refused click DEAD-ENDED instead of offering the picker")


def test_the_span_is_None_fixture_really_IS_refused_by_the_scanner():
    """POSITIVE CONTROL for the fixture above, on the scanner rather than on
    `main()`. If `##370` ever started resolving, the test above would pass
    through the ordinary bare-`#N` path and stop covering the `span is None`
    arm entirely — green, and testing something else."""
    assert MO.resolve("##370") == (None, [])
    assert MO.offer_number("##370") == "370"


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

# Every command the resolution path is allowed to spawn, as its VERB PATH — see
# `_ledger_key`.
#
# 🔴 THE SUBCOMMAND IS PART OF THE LEDGER, NOT DECORATION. `git` alone would
# admit `git ls-remote` and `git fetch`, which are network calls wearing the name
# of a local tool — the exact substitution a ban on `gh` invites.
#
# 🔴 AND TWO WORDS WERE NOT ENOUGH, WHICH IS A MEASUREMENT AND NOT A THEORY. The
# ledger used to be `(argv[0], argv[1])`, so `("git", "remote")` admitted
# `git remote update` — which runs `git fetch` against EVERY remote, i.e. the
# network call this whole section exists to keep out, spelled with the same two
# words as the local `git remote get-url`. Measured on this branch: adding
# `_git(["remote", "update"], cwd=…)` to `repo_of_checkout` left all 352 tests
# green, while `git fetch --all` and `curl` both died. The docstring above
# already claimed the subcommand was in the ledger BECAUSE `git` alone admits a
# fetch; the key was one level short of the claim.
RESOLUTION_PATH_COMMANDS = {
    ("tmux", "display-message"),      # which pane the operator was last in
    ("git", "rev-parse"),             # that pane's checkout root
    ("git", "remote", "get-url"),     # reads .git/config — NOT `remote update`
}


def _ledger_key(argv: list[str]) -> tuple[str, ...]:
    """A command's VERB PATH: `argv[0]`, `argv[1]`, and `argv[2]` when that third
    word is a bare word rather than an option.

    The third word is what separates `git remote get-url` (a local config read)
    from `git remote update` (a fetch per remote). It is dropped when it starts
    with `-`, so `tmux display-message -p -F …` still keys on its subcommand
    rather than dragging a flag or a format string into the ledger.
    """
    key = tuple(argv[:2])
    third = argv[2] if len(argv) > 2 else ""
    return key + ((third,) if third and not third.startswith("-") else ())


def _assert_only_local_commands(seen: list[list[str]]) -> None:
    """Fail unless `seen` is exactly the ledger above.

    🔴 BOTH DIRECTIONS. A GROWN set is a new command nobody vetted — a network
    call, most likely. A SHRUNK set means the path under test never ran the
    measurement at all, which would make every absence below vacuous.
    """
    got = {_ledger_key(c) for c in seen if len(c) >= 2}
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


def test_the_ledger_rejects_git_remote_UPDATE_which_is_a_fetch():
    """🔴 THE SUBSTITUTION A TWO-WORD LEDGER ADMITTED, kept as its own control.

    `git remote update` runs `git fetch` for every configured remote. Under the
    old `(argv[0], argv[1])` key it was INDISTINGUISHABLE from
    `git remote get-url origin`, so it passed — verified by adding it to
    `repo_of_checkout` and watching all 352 tests stay green. This is the case
    that must go red, and it is spelled out separately from the `gh`/`curl` row
    above because those two never passed: a control built only from commands the
    old key already caught proves nothing about the change that closed this."""
    local = [["tmux", "display-message", "-p", "-F", "#{pane_current_path}"],
             ["git", "rev-parse", "--show-toplevel"],
             ["git", "remote", "get-url", "origin"]]
    # NEGATIVE CONTROL FIRST: the REAL argv must still pass, or the assertions
    # below would be satisfied by a key that rejects everything.
    _assert_only_local_commands(local)
    for network in (["git", "remote", "update"],
                    ["git", "fetch", "--all"],
                    ["git", "ls-remote", "https://github.com/x/y"],
                    ["curl", "-s", "https://api.github.com/search/repositories"]):
        with pytest.raises(AssertionError, match="ledger MOVED"):
            _assert_only_local_commands(local + [network])


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
    monkeypatch.setattr(MO, "pick", lambda c, mesg="": "")
    monkeypatch.setattr(MO, "notify", lambda *a, **k: None)

    assert MO.main(["zzznosuchrepo#12"]) == 0
    _assert_only_local_commands(seen)
    # POSITIVE CONTROL: the fan-out really ran over the real workspace, so the
    # ledger above is a fact about a path that did the work, not about a
    # short-circuit. One checkout ⇒ one `git remote` call.
    assert [c for c in seen if c[:2] == ["git", "remote"]], seen


# Every executable the module may ever spawn, from ANY function. Wider than
# `RESOLUTION_PATH_COMMANDS` because it also covers the ACTION half — opening a
# browser, raising the picker terminal, sending a notification — which no
# resolution runs.
SPAWNABLE_EXECUTABLES = {"git", "tmux", "alacritty", "xdg-open", "notify-send"}


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


def _shell_child_commands(source: str) -> set[str]:
    """The binaries a spawn's `-c` SCRIPT runs — the first word of every string
    constant that follows a literal `"-c"` inside a spawn's argv list.

    🔴 IT EXISTS BECAUSE argv[0] IS NOT THE WHOLE ANSWER ANY MORE. The picker is
    `alacritty -e /bin/sh -c 'fzf …'`: argv[0] is the terminal, and the binary
    that actually has to be on PATH is one level down. Reading the first word
    (rather than grepping the file for `fzf`) keeps this a claim about a CALL
    SITE — a comment mentioning fzf cannot satisfy it, and moving the command
    out of the script fails it.

    Absolute paths are skipped: `/bin/sh` is not resolved through PATH, so it
    needs no package.
    """
    out: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("run", "Popen") or not node.args:
            continue
        argv = node.args[0]
        if not isinstance(argv, (ast.List, ast.Tuple)):
            continue
        elts = list(argv.elts)
        for i, elt in enumerate(elts[:-1]):
            if not (isinstance(elt, ast.Constant) and elt.value == "-c"):
                continue
            script = elts[i + 1]
            # The script may be a NAME (a module constant, as `PICKER_SH` is),
            # so resolve one level of module-level assignment.
            text = None
            if isinstance(script, ast.Constant) and isinstance(script.value, str):
                text = script.value
            elif isinstance(script, ast.Name):
                text = _module_str_constant(source, script.id)
            if not text:
                out.add("<computed>")
                continue
            word = text.split()[0] if text.split() else ""
            if word and not word.startswith("/"):
                out.add(word)
    return out


def _module_str_constant(source: str, name: str) -> str | None:
    """The value of a module-level `NAME = "…"` (implicit-concatenation
    included), or None. Deliberately NOT `getattr(MO, name)`: reading it off the
    imported module would make this reader agree with a runtime value the AST
    cannot see, which is the loophole the AST is here to close."""
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name) and tgt.id == name:
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, SyntaxError, TypeError):  # pragma: no cover
                    return None
                return value if isinstance(value, str) else None
    return None


def test_the_shell_child_reader_can_actually_fire():
    """POSITIVE **AND** NEGATIVE CONTROL on the second reader, before its
    verdict is believed — a reader that matched nothing would report a clean
    empty set forever, and `test_the_alacritty_wrapper_PATH_…` would then stop
    covering fzf with no visible change."""
    assert _shell_child_commands(
        "import subprocess\n"
        "subprocess.Popen(['alacritty', '-e', '/bin/sh', '-c', 'fzf --x <\"$1\"'])\n"
    ) == {"fzf"}
    # Through a module constant, which is how the handler actually spells it.
    assert _shell_child_commands(
        "import subprocess\n"
        "S = 'fzf --tiebreak=end'\n"
        "subprocess.Popen(['alacritty', '-e', '/bin/sh', '-c', S])\n"
    ) == {"fzf"}
    # A spawn with no `-c` contributes nothing…
    assert _shell_child_commands(
        "import subprocess\nsubprocess.run(['git', 'rev-parse'])\n") == set()
    # …and a script it cannot read is reported, never silently dropped.
    assert _shell_child_commands(
        "import subprocess\n"
        "subprocess.Popen(['sh', '-c', build()])\n") == {"<computed>"}


def test_the_spawned_executable_LEDGER_can_actually_fire():
    """POSITIVE CONTROL on the reader, before its verdict is believed. An AST
    walk that matched nothing would report a clean empty set forever."""
    found = _spawned_executables(
        "import subprocess\n"
        "subprocess.run(['gh', 'api', 'search/repositories'])\n")
    assert found == {"gh"}, found
    assert _spawned_executables("import subprocess\nx = 1\n") == set()


def test_the_alacritty_wrapper_PATH_covers_every_executable_the_handler_spawns():
    """🔴 A SEAM BETWEEN TWO FILES IN TWO LANGUAGES, OWNED BY NEITHER.
    `nix/programs/alacritty/default.nix` pins a PATH for the hint wrapper because
    Alacritty spawns it with the display manager's environment; `mention-open.py`
    decides what it spawns. They agree only by coincidence, and BOTH failure
    directions are silent:

      * a spawn with no package — `FileNotFoundError` is caught as `OSError` and
        answered with "", so the feature is INERT in production with a fully
        green suite. That is exactly how the deleted `gh` search would have
        behaved, and its own comment said so.
      * a package with no spawn — dead weight in the wrapper's closure, and a
        comment justifying a call site that no longer exists. `pkgs.gh` was
        precisely this: its comment named "PASS 3", the branch DELETED that pass
        and renumbered the rest, so a reader following the comment landed on the
        fuzzy universe, which spawns nothing.

    ⚠ `rofi` USED TO BE THE ONE DELIBERATE OMISSION — a system package invoked
    bare, kept off the list so a second copy's theme could not drift from the
    launcher's. The handler no longer spawns it; the picker is `fzf` inside a
    float `alacritty`, and both are pinned rather than omitted.

    🔴 AND `fzf` IS THE CASE THIS TEST HAD TO GROW A SECOND READER FOR. It is
    spawned by the picker's `/bin/sh -c` line, not by the handler, so an argv[0]
    scan cannot see it — and a missing `fzf` fails exactly the way the wrapper
    exists to prevent: `sh` prints `not found` into a terminal that then closes,
    and the click is a silent dead end with the whole suite green.
    `_shell_child_commands` reads the FIRST WORD of the `-c` script out of the
    SYNTAX TREE, so this is pinned to a call site rather than to a comment.
    """
    # executable -> the nix attribute that must provide it.
    PROVIDER = {"git": "pkgs.git", "tmux": "pkgs.tmux",
                "xdg-open": "pkgs.xdg-utils", "notify-send": "pkgs.libnotify",
                "alacritty": "pkgs.alacritty", "fzf": "pkgs.fzf"}
    # python312 is the interpreter the wrapper `exec`s by store path.
    INTERPRETER = {"pkgs.python312"}

    nix_src = (ROOT / "nix" / "programs" / "alacritty" / "default.nix").read_text()
    m = re.search(r"makeBinPath\s*\[(.*?)\]", nix_src, re.S)
    assert m, "the mentionOpen wrapper no longer calls lib.makeBinPath"
    # Comments in that block are PROSE ABOUT the packages — `pkgs.gh` was named
    # in one for months. Reading them as config is the mistake this strips.
    body = re.sub(r"#[^\n]*", "", m.group(1))
    listed = set(re.findall(r"pkgs\.[A-Za-z0-9_-]+", body))
    assert listed, "positive control: the wrapper DOES pin a PATH"

    src = HANDLER.read_text()
    spawned = _spawned_executables(src) | _shell_child_commands(src)
    assert spawned, "positive control: the module DOES spawn things"
    assert "fzf" in spawned, (
        "positive control on the SECOND reader: the picker's `sh -c` line must "
        "still be visible to _shell_child_commands, or this test silently stops "
        "covering the binary the picker cannot run without")
    needed = {PROVIDER[e] for e in spawned if e in PROVIDER}
    unknown = spawned - set(PROVIDER)
    assert not unknown, (
        f"mention-open.py spawns {sorted(unknown)}, which this test cannot map "
        f"to a nix package — add it to PROVIDER and to the wrapper's PATH")
    assert needed <= listed, (
        f"the wrapper's PATH is MISSING {sorted(needed - listed)}: the handler "
        f"spawns it, FileNotFoundError is caught as OSError, and the feature is "
        f"inert in production with a green suite")
    assert listed <= needed | INTERPRETER, (
        f"the wrapper's PATH carries {sorted(listed - needed - INTERPRETER)}, "
        f"which nothing in mention-open.py spawns — dead weight in the closure, "
        f"and a comment justifying a call site that no longer exists")


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
    assert opened == ["https://github.com/gardenersguild/plotwidget/pull/42"]


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
    """`github.com/a/b/c/pull/12` 404s while looking authoritative — the same
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


# --------------------------------------------------------------------------- #
# 🔴 THE 16-THREAD FAN-OUT
#
# `discover_repos` runs one `git remote get-url` per checkout across a thread
# pool and re-pairs the results with the checkouts POSITIONALLY. Every other
# fixture in this file holds exactly ONE checkout, so none of them can see a
# pairing bug at all: with one entry, `zip(entries, results)` and
# `zip(entries, reversed(results))` are the same function.
#
# Two mutants survived the whole 352-test suite before these tests existed:
#   * `pool.map(repo_of_checkout, entries[::-1])` — every checkout takes its
#     NEIGHBOUR's owner. On the real host that maps `~/workspace/devrc` to
#     another organisation's repository, so `devrc#1291` opens issue 1291
#     somewhere unrelated: the confident wrong page.
#   * dropping `if full:` — a child that failed or timed out answers "", and
#     that "" is written OVER a good row the generated mapping supplied,
#     silently un-resolving a name the host could answer a moment earlier.
#
# 🔴 FOUR CHECKOUTS, PAIRWISE-DISTINCT OWNERS AND REPOS, ALL SYNTHETIC. Four
# rather than three because a 3-element reversal leaves the MIDDLE element on
# itself; four has no fixed point. Distinct from `FAKE_UNIVERSE` and from every
# constant these assertions name, so a mutant that transposes or hardcodes
# cannot land on an expected value by accident.
# --------------------------------------------------------------------------- #
FAN_OUT_CHECKOUTS = {
    "brambleway": "oxbowlabs/brambleway",
    "cinderfell": "pitchpine/cinderfell",
    "duskharrow": "quarrymill/duskharrow",
    "quillmarsh": "northgate/quillmarsh",
}


def _workspace_of(tmp_path, names) -> Path:
    ws = tmp_path / "workspace"
    for name in names:
        (ws / name / ".git").mkdir(parents=True)
    return ws


def test_the_fan_out_pairs_every_checkout_with_its_OWN_owner(tmp_path, monkeypatch):
    """🔴 THE TRANSPOSITION GUARD. Every checkout must be paired with the owner
    measured FOR IT, not with a neighbour's."""
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", tmp_path / "absent.json")
    ws = _workspace_of(tmp_path, FAN_OUT_CHECKOUTS)
    monkeypatch.setattr(MO, "repo_of_checkout",
                        lambda path: FAN_OUT_CHECKOUTS[Path(path).name])
    got = MO.discover_repos(ws)
    # POSITIVE CONTROL: the fan-out really ran over four checkouts, so the
    # equality below is a fact about work that happened.
    assert len(got) == 4, got
    assert got == dict(FAN_OUT_CHECKOUTS), (
        f"the fan-out paired a checkout with a NEIGHBOUR's owner: {got}")


def test_the_fan_out_DEGRADES_to_serial_rather_than_vanishing(tmp_path, monkeypatch):
    """🔴 `RuntimeError: can't start new thread` IS NOT HYPOTHETICAL ON THIS BOX
    — it routinely runs 100+ concurrent agent worktrees. It used to propagate
    out of `main()` into a DETACHED process: no toast, no readable stderr, a
    click that did nothing.

    The serial path must produce the SAME pairing, not merely "an answer" — a
    fallback that transposes is the confident wrong page again, reached only
    under load, where nobody is watching."""
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", tmp_path / "absent.json")
    ws = _workspace_of(tmp_path, FAN_OUT_CHECKOUTS)
    monkeypatch.setattr(MO, "repo_of_checkout",
                        lambda path: FAN_OUT_CHECKOUTS[Path(path).name])

    def cannot_start_threads(paths):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(MO, "_fan_out", cannot_start_threads)
    got = MO.discover_repos(ws)
    assert got == dict(FAN_OUT_CHECKOUTS), (
        f"discovery did not DEGRADE to a serial fan-out: {got}")


def test_an_UNREADABLE_checkout_does_not_ERASE_the_mappings_answer(
        tmp_path, monkeypatch):
    """🔴 `if full:` IS A GUARD. `repo_of_checkout` answers "" for a checkout
    whose `git remote` failed or timed out. Writing that "" into the result
    DELETES the row the generated mapping already supplied for the same name —
    a name that resolved a moment ago silently stops resolving, and the operator
    sees a picker where they used to see a page.

    Both directions are asserted: the mapping's row must SURVIVE, and a
    checkout the mapping never knew about must be ABSENT rather than present
    with an empty value (an empty value builds `https://github.com//pull/12`).
    """
    p = tmp_path / "known_repos.json"
    p.write_text(json.dumps({"quillmarsh": "northgate/quillmarsh"}))
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", p)
    ws = _workspace_of(tmp_path, ["brambleway", "cinderfell", "quillmarsh"])
    unreadable = {"quillmarsh", "cinderfell"}
    monkeypatch.setattr(
        MO, "repo_of_checkout",
        lambda path: "" if Path(path).name in unreadable
        else FAN_OUT_CHECKOUTS[Path(path).name])
    got = MO.discover_repos(ws)
    # POSITIVE CONTROL: the readable checkout DID land, so the two claims below
    # are about a run that measured something.
    assert got.get("brambleway") == "oxbowlabs/brambleway", got
    assert got.get("quillmarsh") == "northgate/quillmarsh", (
        f"an unreadable checkout ERASED the mapping's answer: {got}")
    assert "cinderfell" not in got, (
        f"an unreadable checkout ERASED the mapping's answer by adding an "
        f"empty row: {got}")


# The picker's size, and the empty results that must name their cause
# --------------------------------------------------------------------------- #
def test_a_WALL_of_repos_reaches_the_picker_because_it_can_be_TYPED_at(
        spy, monkeypatch):
    """🔴 THE REVERSAL, NOW OVER THE LOCAL UNIVERSE. This used to refuse above 8
    candidates, on the reasoning that "a 100-row list of URLs differing only by
    owner is not a choice, it is a wall". That is true of a list you can only
    SCROLL and false of one you can TYPE AT, and `pick()` runs fzf, which
    matches fuzzily. So the wall is a narrowing, and refusing would remove the
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


# --------------------------------------------------------------------------- #
# THE PICKER'S ARGV AND ITS TRANSPORT
#
# 🔴 NOTHING BELOW RAISES A WINDOW. `subprocess.Popen` is replaced by a FAKE
# TERMINAL — a thread that opens the same two FIFOs a real `alacritty -e sh -c
# fzf …` would — so the transport is exercised end to end and the operator's
# screen is never touched. `alacritty` is ALSO in `nolaunch.HOST_LAUNCHERS`, so
# even a test that forgot to patch reaches a recording stub rather than a real
# terminal.
# --------------------------------------------------------------------------- #
ONE_CANDIDATE = [{"platform": "github", "id": "7",
                  "url": "https://github.com/gardenersguild/trowelcast/pull/7"}]


class _FakeTerminal:
    """Stands in for the terminal `pick()` spawns, doing what it really does.

    A plain `lambda: None` Popen is NOT usable here and that is the point: with
    nobody on the other end of the rows FIFO, `os.open(..., O_WRONLY|
    O_NONBLOCK)` raises ENXIO forever and `run_picker` would spin to its
    deadline. Reading the pipe is the only faithful fake — which makes every
    test below an exercise of the real transport rather than of an argv builder.

    🔴 IT OPENS BOTH FIFOs BEFORE READING EITHER, BECAUSE `/bin/sh` DOES.
    `<"$1" >"$2"` are applied by the shell BEFORE fzf is exec'd, so the child is
    holding an open read end of the rows FIFO and blocking on the write end of
    the choice FIFO while nothing is draining the rows. A fake that read the
    rows first would be a friendlier peer than the real one and would hide the
    deadlock `test_a_payload_LARGER_than_a_pipe_buffer_still_reaches_the_picker`
    exists for.
    """

    def __init__(self, choose: str | None = None, *, answer_early: bool = False):
        self.choose = choose
        # 🔴 `answer_early` MODELS WHAT fzf ACTUALLY DOES, and without it this
        # fake is a FRIENDLIER PEER THAN THE REAL ONE. fzf answers the moment the
        # operator presses a key — it does NOT wait to drain the list — and then
        # closes its read end, so the writer's next `os.write` gets EPIPE. The
        # default here reads the whole FIFO first, which is why every transport
        # test below was blind to `BrokenPipeError` until this parameter existed.
        self.answer_early = answer_early
        self.argv: list[str] = []
        self.payload = ""
        self._thread = None
        self._done = threading.Event()

    def popen(self, argv, **kwargs):
        self.argv = list(argv)
        rows_fifo, choice_fifo = argv[-3], argv[-2]

        def serve():
            # The shell's own order: both redirections, THEN the program.
            rfh = open(rows_fifo, "r", encoding="utf-8")
            wfh = open(choice_fifo, "w", encoding="utf-8")
            with wfh:
                if self.answer_early:
                    # Answer, then hang up WITHOUT draining — the real thing.
                    if self.choose is not None:
                        wfh.write(self.choose + "\n")
                        wfh.flush()
                    self.payload = rfh.read(4096)
                    rfh.close()
                else:
                    with rfh:
                        self.payload = rfh.read()
                        if self.choose is not None:
                            wfh.write(self.choose + "\n")
            self._done.set()

        self._thread = threading.Thread(target=serve, daemon=True)
        self._thread.start()
        return self

    # --- the `subprocess.Popen` surface `run_picker` uses ------------------ #
    def poll(self):
        return 0 if self._done.is_set() else None

    def terminate(self):  # pragma: no cover — only on the timeout path
        self._done.set()

    @property
    def rows(self) -> list[str]:
        return [r for r in self.payload.split("\n") if r]

    def sh_script(self) -> str:
        return self.argv[self.argv.index("-c") + 1]


def _drive_picker(monkeypatch, candidates=None, mesg="", choose=None,
                  answer_early=False):
    term = _FakeTerminal(choose, answer_early=answer_early)
    monkeypatch.setattr(MO.subprocess, "Popen", term.popen)
    url = MO.pick(candidates if candidates is not None else ONE_CANDIDATE,
                  mesg=mesg)
    return term, url


def test_the_picker_spawns_a_TERMINAL_running_fzf(monkeypatch):
    """🔴 THE SEAM BETWEEN THE DECISION AND THE TOOL. Every other test here
    stubs `pick`, so none of them notices if the picker stops being spawned at
    all. This is the only family that reads the argv `pick` actually builds."""
    term, _url = _drive_picker(monkeypatch)
    assert term.argv[0] == "alacritty"
    # `-e` must be LAST before the command, or alacritty eats the rest as its
    # own options and the picker never runs.
    assert term.argv[term.argv.index("-e") + 1] == "/bin/sh"
    assert term.sh_script().split()[0] == "fzf", (
        "the wrapper's PATH pin is derived from this first word — see "
        "test_the_alacritty_wrapper_PATH_covers_every_executable_the_handler_"
        "spawns")


def test_the_picker_ranks_by_TIEBREAK_END_which_is_the_whole_reason_for_fzf(
        monkeypatch):
    """🔴 THE FLAG THE SWAP EXISTS FOR, and the one whose loss is silent.

    rofi ranked by score and then by ROW LENGTH. In `owner/repo` every row of
    one owner matches at the same offset, so the scores tie and the LONGEST
    row loses — MEASURED on the real 392-row universe, query `civitai`:
    `civitai/civitai` 8th of 230, under seven shorter rows containing the
    token once. `--tiebreak=end` prefers the match nearest the END of the line,
    and the suffix after the repo is a constant, so that is exactly "the repo
    name rather than the owner": same corpus, same query, rank 1 of 230.

    Dropping this flag leaves a picker that still WORKS — it just goes back to
    ranking the wanted repo eighth — which is why it is asserted rather than
    left to a reviewer."""
    term, _url = _drive_picker(monkeypatch)
    assert "--tiebreak=end" in term.sh_script(), (
        "without --tiebreak=end fzf falls back to its LENGTH tiebreak, which "
        "is the exact defect rofi could not be talked out of")


@pytest.mark.parametrize("flag,why", [
    ("--exact", "MEASURED not to fix the tie (rank 27 with it and 27 without, "
                "same corpus) while narrowing the match set from 56 rows to 21 "
                "on a 6-char prefix — the fuzzy narrowing is the whole reason a "
                "392-row universe is usable"),
    ("--select-1", "auto-accepts a single match, which is precisely the "
                   "one-row GUESSED-repo picker that #1336 made unconfirmable"),
    ("--print-query", "makes fzf hand back text the operator TYPED as if it "
                      "were a selection — `row_to_url` matches nothing, so it "
                      "is dismissal-shaped by accident. This is what rofi "
                      "needed `-no-custom` for"),
    ("--no-sort", "turns off ranking entirely, which is the pre-#1373 bug"),
])
def test_the_picker_does_NOT_pass(flag, why, monkeypatch):
    """Four flags REJECTED, each with its measurement. Asserted on the argv
    rather than left in a comment: every one of them is a plausible-looking
    addition whose damage is invisible in a green suite."""
    term, _url = _drive_picker(monkeypatch)
    assert flag not in term.sh_script(), f"{flag}: {why}"


def test_the_picker_argv_stays_a_LIST_LITERAL_so_the_ledger_can_read_it():
    """🔴 The AST ledger of spawnable executables reads `run_picker`'s argv as a
    list literal whose first element is the constant `"alacritty"`. Building it
    from a variable reports `<computed>` and reddens the no-network guard for a
    reason that has nothing to do with what is spawned — the module already
    carries that warning in a comment, and this is the assertion behind it.

    Kept beside the flag tests because the tempting way to add a conditional
    flag is exactly the refactor that breaks it."""
    tree = ast.parse(HANDLER.read_text())
    found = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and node.args):
            continue
        name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if name != "Popen":
            continue
        first = node.args[0]
        if (isinstance(first, ast.List) and first.elts
                and isinstance(first.elts[0], ast.Constant)
                and first.elts[0].value == "alacritty"):
            found = True
    assert found, (
        "no `subprocess.Popen([\"alacritty\", …])` list literal found in the "
        "handler")


# --------------------------------------------------------------------------- #
# 🔴 DISCLOSURE — THE NEW SURFACE A TERMINAL BRINGS
#
# The rows name PRIVATE repositories. Under rofi they went to `input=`, a pipe
# nothing else could read. A terminal emulator does NOT proxy stdin, so the
# transport had to change, and every alternative to a FIFO puts them somewhere
# a second process can read: argv is world-readable in `/proc`, an env var
# likewise, a temp file lands them on disk. These pin the choice.
# --------------------------------------------------------------------------- #
def test_NO_ROW_reaches_the_terminal_ARGV(monkeypatch):
    """🔴 argv IS A SINK `_every_sink` CANNOT SEE. `/proc/<pid>/cmdline` is
    readable for the whole lifetime of the picker, and a `--header` or an
    inlined row list would put private repository names there.

    The positive control comes first: the rows really did reach the picker, so
    their absence from argv is a routing decision and not an empty fixture."""
    cands = MO.universe_candidates("12", sorted(FAKE_UNIVERSE.values()))
    term, _url = _drive_picker(monkeypatch, cands, mesg="pick a repository")
    assert term.rows[int(term.argv[-1]):] == MO.picker_rows(cands), (
        "positive control: the picker got the real rows")
    blob = " ".join(term.argv)
    _no_universe_token_anywhere(blob, "PICKER ARGV DISCLOSURE")


def test_the_rows_go_down_a_FIFO_that_does_not_outlive_the_pick(monkeypatch):
    """🔴 NOTHING IS LEFT ON DISK. A FIFO holds no content at rest — the bytes
    live in a kernel pipe buffer between two processes — and the 0700 directory
    holding the pair is removed in a `finally`.

    Both halves are asserted: the paths really were FIFOs while the picker ran
    (so this is not silently testing regular files), and the directory is gone
    afterwards."""
    kinds = {}

    class Watcher(_FakeTerminal):
        def popen(self, argv, **kwargs):
            kinds["rows"] = stat.S_ISFIFO(os.stat(argv[-3]).st_mode)
            kinds["choice"] = stat.S_ISFIFO(os.stat(argv[-2]).st_mode)
            kinds["dir"] = os.path.dirname(argv[-3])
            kinds["mode"] = stat.S_IMODE(os.stat(kinds["dir"]).st_mode)
            return super().popen(argv, **kwargs)

    term = Watcher(None)
    monkeypatch.setattr(MO.subprocess, "Popen", term.popen)
    MO.pick(ONE_CANDIDATE)
    assert kinds["rows"] and kinds["choice"], "the transport is not a FIFO pair"
    assert kinds["mode"] == 0o700, f"the picker's tmpdir is {kinds['mode']:o}"
    assert not os.path.exists(kinds["dir"]), (
        f"{kinds['dir']} outlived the pick — a private row list left on disk")


# --------------------------------------------------------------------------- #
# THE NOTE — rofi's `-mesg`, now fzf's `--header-lines`
# --------------------------------------------------------------------------- #
def test_the_note_is_prepended_to_the_INPUT_and_counted_exactly(monkeypatch):
    """🔴 THE COUNT IS THE HAZARD, AND IT ONLY BITES IN ONE DIRECTION. A
    `--header-lines` value too LOW leaves a note line selectable, which
    `row_to_url` maps to nothing; too HIGH it swallows the FIRST REAL ROW — the
    clawgate task on a bare `#N`. This asserts the count equals the number of
    note lines actually prepended, and that every candidate row survived."""
    note = ("nothing here knows widget#12 — pick a repository, or dismiss. "
            "3 offered · mapping generated 2026-09-01 (8d ago) · refresh with "
            "scripts/regen-known-repos.py")
    cands = MO.universe_candidates("12", sorted(FAKE_UNIVERSE.values()))
    term, _url = _drive_picker(monkeypatch, cands, mesg=note)
    n = int(term.argv[-1])
    assert n == len(MO.picker_header(note)) >= 2, (
        "the note must be WRAPPED, not truncated by the terminal's width")
    assert term.rows[n:] == MO.picker_rows(cands), (
        "the header count does not line up with the rows — a too-high count "
        "eats the first candidate")
    assert " ".join(term.rows[:n]).split() == note.split(), (
        "the note reached the picker with its words changed")


def test_the_note_is_NOT_pango_escaped_because_fzf_renders_plain_text(monkeypatch):
    """rofi's `-mesg` was PANGO markup, so `&`/`<`/`>` had to be escaped or the
    line vanished. fzf's header is plain text: leaving that escaping in would
    show the operator a literal `&amp;` in text THEY typed."""
    term, _url = _drive_picker(monkeypatch, mesg="a & b <c> d")
    assert "&amp;" not in term.payload and "&lt;" not in term.payload
    assert "a & b <c> d" in term.payload


def test_no_note_means_no_header_lines(monkeypatch):
    """The `mesg=""` default must produce a count of 0, not an empty line that
    eats the first row."""
    term, _url = _drive_picker(monkeypatch)
    assert term.argv[-1] == "0"
    assert term.rows == MO.picker_rows(ONE_CANDIDATE)


# --------------------------------------------------------------------------- #
# THE ROUND TRIP
# --------------------------------------------------------------------------- #
def test_a_selection_comes_back_as_its_URL(monkeypatch):
    """The positive control for every "returns nothing" assertion below: the
    transport really can carry a choice back."""
    row = MO.picker_rows(ONE_CANDIDATE)[0]
    _term, url = _drive_picker(monkeypatch, choose=row)
    assert url == ONE_CANDIDATE[0]["url"]


def test_a_DISMISSAL_opens_nothing(monkeypatch):
    """fzf exits without writing when the operator presses Esc. That must be ""
    — never a guess at row 1."""
    _term, url = _drive_picker(monkeypatch, choose=None)
    assert url == ""


def test_TYPED_FREE_TEXT_can_never_come_back_as_a_selection(monkeypatch):
    """🔴 `-no-custom`'S REPLACEMENT, ASSERTED AT THE BOUNDARY RATHER THAN ON A
    FLAG. fzf structurally cannot return the query (that would need
    `--print-query`, whose absence is pinned above) — but the property that
    matters is what `pick` does with a row it did not offer. Feed it one and it
    must open NOTHING, exactly like a dismissal."""
    _term, url = _drive_picker(monkeypatch, choose="kubectl-neat")
    assert url == ""


def test_a_payload_LARGER_than_a_pipe_buffer_still_reaches_the_picker(
        monkeypatch):
    """🔴 A DEADLOCK THE CURRENT UNIVERSE IS TOO SMALL TO HIT, WHICH IS EXACTLY
    WHY IT IS PINNED AT A SIZE IT DOES.

    `/bin/sh` applies `<"$1" >"$2"` BEFORE exec'ing fzf, and opening a FIFO for
    writing blocks until a reader exists. So if `run_picker` opened the rows
    FIFO and started writing before opening the choice FIFO for reading, the
    child would be parked in `open("$2")` with nothing draining `$1`. Everything
    works while the payload fits in one 64 KiB pipe buffer — today's ~392 rows
    are ~27 KB — and the day it does not, the click hangs to the 120s timeout.

    3,000 rows (~172 KB, measured) is deliberately several buffers past the
    boundary rather than one byte over it: at 65,537 bytes this would still pass
    on a kernel with a larger pipe buffer, and the point is the ORDER, not the
    exact size. The wall-clock bound is what turns a hang into a failure."""
    cands = [{"platform": "github", "id": "12",
              "url": f"https://github.com/owner{i}/repo{i}/pull/12"}
             for i in range(3000)]
    payload_size = len("\n".join(MO.picker_rows(cands)))
    assert payload_size > 150_000, (
        f"the fixture no longer overshoots a pipe buffer: {payload_size}")
    started = time.monotonic()
    term, url = _drive_picker(monkeypatch, cands, choose=MO.picker_rows(cands)[-1])
    assert time.monotonic() - started < 30, (
        "the picker DEADLOCKED on a payload larger than a pipe buffer")
    assert term.rows == MO.picker_rows(cands), "the rows were truncated"
    assert url == cands[-1]["url"]


def test_a_terminal_that_never_starts_returns_EMPTY_rather_than_hanging(
        monkeypatch):
    """🔴 THE ENXIO TRAP. A FIFO opened for writing fails with ENXIO until a
    READER appears, so a blocking open against a terminal that died — a missing
    binary, a stub, a crash — would hang for the whole 120s timeout on the
    operator's click. `run_picker` polls and watches the child instead.

    Measured rather than asserted structurally: this test would take two
    minutes if the poll regressed, and the wall-clock bound is what says so."""
    class DeadTerminal:
        def poll(self):
            return 1

        def terminate(self):  # pragma: no cover — poll() already reports exit
            pass

    monkeypatch.setattr(MO.subprocess, "Popen", lambda *a, **k: DeadTerminal())
    # `notify` is stubbed because THIS test is about the wall clock, not the
    # toast — the real one would reach the patched `Popen` and blow up on a fake
    # that is not a context manager. The toast itself is
    # `test_a_terminal_that_DIED_before_showing_anything_says_so`.
    monkeypatch.setattr(MO, "notify", lambda *a, **k: None)
    started = time.monotonic()
    assert MO.pick(ONE_CANDIDATE) == ""
    assert time.monotonic() - started < 5, (
        "pick() waited on a terminal that had already exited")


def test_a_terminal_that_cannot_be_SPAWNED_says_so(monkeypatch):
    """`alacritty` missing from the wrapper's PATH is a FileNotFoundError, which
    is an OSError — silently answered with "" everywhere else in this file. The
    picker is the one place that must TOAST, because the alternative is a click
    that does nothing at all."""
    said = []

    def boom(*a, **k):
        raise FileNotFoundError("alacritty")

    monkeypatch.setattr(MO.subprocess, "Popen", boom)
    monkeypatch.setattr(MO, "notify", lambda s, b="": said.append((s, b)))
    assert MO.pick(ONE_CANDIDATE) == ""
    assert said and "picker" in said[0][0], said


# --------------------------------------------------------------------------- #
# 🔴 THE ONE PROPERTY NO ARGV ASSERTION CAN REACH: WHAT REAL fzf DOES
#
# Everything above is a claim about the command line. These two run the REAL
# binary, headlessly, with `--filter` — fzf's own non-interactive ranking mode —
# because the two properties that matter most are properties of fzf's ALGORITHM
# and would survive any argv check unchanged.
# --------------------------------------------------------------------------- #
def _require_fzf() -> None:
    """🔴 NOT a `skipif`, and that was MEASURED rather than chosen on principle.

    These four were `@pytest.mark.skipif(shutil.which("fzf") is None)` first. On
    the dev host fzf is on PATH and all four ran; in the nix check sandbox — the
    tier the merge is actually gated on — all four SKIPPED, and `run-tests.sh`
    failed the derivation on the unpinned skips. Which is the guard doing its
    job: the authoritative tier had zero coverage of the single property this
    whole change exists for, and a `skipif` is what made that silent.

    So fzf is declared in `run-tests.sh` REQUIRED_TOOLS and in flake.nix
    `gateTools` (both, spelled differently — binary vs package), and its absence
    is an environment fault to fix there, never a skip here.
    """
    assert shutil.which("fzf") is not None, (
        "fzf is not on PATH. The mention picker IS fzf, so this is a broken "
        "environment, not a reason to skip: add it to REQUIRED_TOOLS in "
        "scripts/run-tests.sh and pkgs.fzf to gateTools in flake.nix, or enter "
        "the repo's dev shell (`nix develop`), which carries both.")


def test_REAL_fzf_ranks_the_eponymous_repo_FIRST_under_tiebreak_end():
    """🔴 THE MEASUREMENT THE SWAP IS FOR, RE-RUN AS A TEST, on a corpus of the
    SAME SHAPE as the operator's (an owner whose name is also one of its repos,
    with shorter siblings). Synthetic names on purpose — this repo is public and
    the real universe is private.

    Both directions are asserted. Without the flag the wanted row is buried,
    which is the NEGATIVE CONTROL: an assertion that only checked "rank 1 with
    the flag" would pass against an fzf that ranked it first regardless, and
    would then not be measuring the flag at all."""
    _require_fzf()
    rows, target = _eponymous_corpus()
    plain = _fzf_rank(rows, "nimbusworks", target)
    tiebreak = _fzf_rank(rows, "nimbusworks", target, "--tiebreak=end")
    assert plain > 5, (
        f"NEGATIVE CONTROL: default fzf put the wanted row at {plain}; if that "
        "is already 1 this corpus no longer reproduces the defect and the "
        "assertion below proves nothing")
    assert tiebreak == 1, f"--tiebreak=end ranked it {tiebreak}, not 1"


def test_REAL_fzf_does_not_reorder_rows_when_the_query_is_EMPTY():
    """🔴 THE CLAWGATE ROW MUST STAY FIRST. A bare `#N` offers the clawgate task
    and then the pane's repo, and several tests pin that order — but they all
    stub `pick`, so none of them would notice a picker that re-sorted before the
    operator typed anything. Ranking applies to a SCORED match set; an empty
    query scores nothing, so input order survives. Asserted against the real
    binary because it is a claim about fzf, not about our argv."""
    _require_fzf()
    rows, _target = _eponymous_corpus()
    out = subprocess.run(["fzf", "--filter", "", "--tiebreak=end"],
                         input="\n".join(rows), capture_output=True, text=True)
    got = [r for r in out.stdout.split("\n") if r]
    assert got == rows, "fzf reordered an unfiltered list"


def _eponymous_corpus() -> tuple[list[str], str]:
    """~200 picker rows in the exact shape `picker_rows()` builds, reproducing
    the measured defect: `nimbusworks/nimbusworks` alongside sibling repos whose
    names are SHORTER than the owner's, so a LENGTH tiebreak buries it."""
    owner = "nimbusworks"
    repos = [f"{owner}/{owner}"]
    repos += [f"{owner}/{r}" for r in
              ("api", "web", "cli", "sdk", "ops", "ui", "db", "docs", "auth",
               "jobs", "charts", "runner", "gateway", "console")]
    others = ("greenfielded", "hollowpoint", "quartzline", "sablefen",
              "umbralabs", "verdantco", "wickerbay", "xenolith", "yarrowsoft",
              "zephyrgate", "acrepitch", "brambleway")
    names = ("atlas", "beacon", "cascade", "dossier", "eyrie", "fathom",
             "girder", "harbour", "inkwell", "jetty", "kiln", "lantern",
             "mortar", "nectar", "oxbow")
    repos += [f"{o}/{n}" for o in others for n in names]
    repos = sorted(set(repos), key=str.lower)
    return ([f"github 1234 — https://github.com/{r}/pull/1234" for r in repos],
            f"{owner}/{owner}")


def _fzf_rank(rows: list[str], query: str, target: str, *flags) -> int:
    """The 1-based rank `target` gets for `query`, via fzf's own `--filter`."""
    out = subprocess.run(["fzf", "--filter", query, *flags],
                         input="\n".join(rows), capture_output=True, text=True)
    ranked = [r for r in out.stdout.split("\n") if r]
    assert ranked, "positive control: fzf matched nothing at all"
    for i, row in enumerate(ranked, 1):
        if f"/{target}/pull/" in row:
            return i
    raise AssertionError(f"{target} did not survive the filter {query!r}")


# --------------------------------------------------------------------------- #
# 🔴 THE INTERACTIVE INSTRUMENT — `--filter` IS A PROXY, AND IT HAD TO BE CHECKED
#
# The two tests above use `fzf --filter`, fzf's own non-interactive ranking
# mode. That is a claim about `--filter`, not about the picker the operator
# actually types into, and the difference is exactly the shape RULES.md warns
# about: one measurement, quoted at a scope wider than it was taken.
#
# MEASURED 2026-09-09 while building this: an end-to-end run under Xvfb picked
# the WRONG row and read as "interactive fzf ranks differently from --filter".
# It does not — that run had read the working tree while the mutation battery
# was rewriting it in place, so it was running mutant K49 (`--tiebreak=length`).
# A pty-driven interactive fzf agrees with `--filter` on every variant tried:
# typed character by character, pasted in one write, and `--query`.
#
# These two run the REAL binary interactively, under a pty, with NO window and
# NO X server. `--query` is pre-filled rather than typed, so the only timing
# dependency left is "fzf renders its prompt", which is waited for rather than
# slept through — and a run that never gets there FAILS with the terminal output
# it did see, because a skip here would be the vacuous green one level up.
# --------------------------------------------------------------------------- #
_PTY_PROMPT = "PICKERREADY>"


def _picker_flags() -> list[str]:
    """The picker's OWN fzf flags, read out of `PICKER_SH`.

    🔴 DERIVED, NEVER RE-SPELLED. A pty test carrying its own `--tiebreak=end`
    would be a fact about fzf and not about this handler: drop the flag from
    `PICKER_SH` and such a test stays green while the picker ranks the wanted
    repository 27th. Taking the flags from the string that is actually spawned
    makes these two guards of the handler.

    Everything the shell owns is dropped: the command word, the redirections,
    and any flag whose value is a positional (`--header-lines="$3"` — there is
    no shell here to expand it)."""
    import shlex  # noqa: PLC0415
    words = shlex.split(MO.PICKER_SH)
    assert words and words[0] == "fzf", words
    flags = [w for w in words[1:]
             if not w.startswith(("<", ">")) and "$" not in w]
    assert "--tiebreak=end" in flags, (
        f"positive control on the reader: it lost the picker's flags: {flags}")
    return flags


def _fzf_interactive_first_row(rows: list[str], query: str) -> str:
    """The row a REAL interactive fzf selects on Enter, driven through a pty."""
    import fcntl     # noqa: PLC0415 — only this pair needs them
    import pty       # noqa: PLC0415
    import struct    # noqa: PLC0415
    import termios   # noqa: PLC0415

    # The prompt override comes LAST so it wins over the picker's own — fzf
    # takes the final occurrence — giving a readiness marker that does not move
    # when the handler's prompt does.
    flags = [*_picker_flags(), f"--prompt={_PTY_PROMPT} ", f"--query={query}"]
    r_in, w_in = os.pipe()       # the candidate list
    r_out, w_out = os.pipe()     # the selection
    pid, master = pty.fork()
    if pid == 0:                 # pragma: no cover — the child never returns
        os.dup2(r_in, 0)
        os.dup2(w_out, 1)
        os.environ["TERM"] = "xterm-256color"
        os.execvp("fzf", ["fzf", *flags])
    os.close(r_in)
    os.close(w_out)
    # 🔴 `pty.fork()` LEAVES THE TERMINAL 0x0, AND fzf THEN DRAWS NOTHING. It
    # still WORKS — it filters, and Enter still returns the right row — so a
    # test that only checked the return value would pass while measuring a
    # picker that rendered no prompt to wait for. Setting a size is what makes
    # the readiness signal below exist at all.
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    with os.fdopen(w_in, "wb") as fh:
        fh.write(("\n".join(rows) + "\n").encode())

    def _drain(fd, until, deadline):
        seen = b""
        while time.monotonic() < deadline:
            r, _w, _x = select.select([fd], [], [], 0.2)
            if not r:
                continue
            try:
                chunk = os.read(fd, 65536)
            except OSError:       # pragma: no cover — pty hangup
                break
            if not chunk:
                break
            seen += chunk
            if until(seen):
                break
        return seen

    try:
        drawn = _drain(master, lambda b: _PTY_PROMPT.encode() in b,
                       time.monotonic() + 20)
        assert _PTY_PROMPT.encode() in drawn, (
            "fzf never drew its prompt in 20s — this test measured nothing. "
            f"terminal saw: {drawn[-400:]!r}")
        os.write(master, b"\r")
        got = _drain(r_out, lambda b: b"\n" in b, time.monotonic() + 20)
    finally:
        os.close(r_out)
        os.close(master)
        try:
            os.waitpid(pid, 0)
        except (ChildProcessError, OSError):  # pragma: no cover
            pass
    assert b"\n" in got, (
        f"interactive fzf returned nothing for query {query!r} — the "
        f"measurement did not happen, so neither did the assertion")
    return got.decode("utf-8", "replace").split("\n")[0]


def test_REAL_INTERACTIVE_fzf_opens_on_the_FIRST_INPUT_ROW_with_no_query():
    """🔴 THE CLAWGATE ROW, AT THE INSTRUMENT THE OPERATOR ACTUALLY USES. A bare
    `#N` offers the clawgate task first and the pane's repo second, and several
    tests pin that order — every one of them by stubbing `pick`. This is the one
    that presses Enter on a real fzf."""
    _require_fzf()
    rows, _target = _eponymous_corpus()
    assert _fzf_interactive_first_row(rows, "") == rows[0]


def test_REAL_INTERACTIVE_fzf_puts_the_eponymous_repo_under_the_cursor():
    """The ranking claim at the same instrument: with the owner's name typed,
    Enter must open `owner/owner` and not the shortest sibling."""
    _require_fzf()
    rows, target = _eponymous_corpus()
    got = _fzf_interactive_first_row(rows, "nimbusworks")
    assert f"/{target}/pull/" in got, got



def test_the_picker_matches_CASE_INSENSITIVELY(monkeypatch):
    """🔴 `-i`, AND ITS ABSENCE WAS SILENT FOR A WHOLE ROUND. fzf's default is
    SMART-CASE: a query with any uppercase letter becomes case-SENSITIVE. rofi's
    `-i` was unconditional, so dropping it changed behaviour with no test to say
    so — the old suite asserted `-matching fuzzy` and `-no-custom` and never the
    `-i` beside them, which is exactly how it was lost.

    The consequence is the one this handler exists to prevent: an empty list is
    indistinguishable from a dismissal inside `pick()`, so an operator who types
    a capital letter gets SILENCE. `test_REAL_fzf_is_CASE_INSENSITIVE_only_with_
    the_flag` measures the behaviour; this pins the flag reaching the picker."""
    term, _url = _drive_picker(monkeypatch)
    assert " -i " in f" {term.sh_script()} ", (
        "the picker lost `-i`: fzf is SMART-CASE, so a query containing an "
        "uppercase letter matches nothing and the operator sees an empty list "
        "they cannot tell from a dismissal")


def test_REAL_fzf_is_CASE_INSENSITIVE_only_with_the_flag():
    """🔴 THE BEHAVIOUR BEHIND THE FLAG, at the real binary, with BOTH
    directions — an assertion that only checked "the flag matches" would pass
    against an fzf that was case-insensitive anyway, and would not be measuring
    the flag at all.

    Measured here: `NimbusWorks` → 0 rows without, 41 with. Ranking is
    unaffected (the eponymous repo is 1 either way), which is why adding it
    costs nothing."""
    _require_fzf()
    rows, target = _eponymous_corpus()
    mixed = "NimbusWorks"

    def matched(*flags):
        out = subprocess.run(["fzf", "--filter", mixed, "--tiebreak=end", *flags],
                             input="\n".join(rows), capture_output=True, text=True)
        return [r for r in out.stdout.split("\n") if r]

    assert matched() == [], (
        "NEGATIVE CONTROL: fzf already matched a mixed-case query without `-i`, "
        "so this corpus cannot measure the flag")
    with_i = matched("-i")
    assert with_i, "`-i` matched nothing — the flag is not doing what this pins"
    # …and the flag rescues the ranking too, not merely the membership.
    assert f"/{target}/pull/" in with_i[0], with_i[0]


# --------------------------------------------------------------------------- #
# 🔴 THE SHELL SCRIPT IS A SURFACE ROFI DID NOT HAVE
# --------------------------------------------------------------------------- #
# `_shell_child_commands` learns that `fzf` must be on the wrapper's PATH by
# reading the FIRST WORD of the `-c` script. That is all it can see — so
# `fzf … <"$1" | tee /tmp/picker.log >"$2"` or `notify-send "$(cat "$1")"; fzf …`
# would write the PRIVATE rows to disk, or into a toast argv, with every ledger
# in this file still green. Under rofi there was no shell at all; this PR is what
# created the hole, so the shape is pinned rather than the first word alone.
# --------------------------------------------------------------------------- #
EXPECTED_PICKER_SH = (
    'fzf -i --tiebreak=end --layout=reverse --info=inline '
    '--prompt="mention > " --pointer=">" --color=16 '
    '--header-lines="$3" <"$1" >"$2"'
)


def test_the_picker_SHELL_SCRIPT_stays_the_shape_it_is_pinned_to():
    """🔴 THE WHOLE NORMALISED STRING, not a keyword search. A guard on WORDS is
    walkable by REWORDING, and this artifact IS a shell script — the thing that
    matters is what the SHELL will do with it, which no per-flag assertion can
    answer. Rewording is expected to fail this; the diff is the review."""
    assert MO.PICKER_SH == EXPECTED_PICKER_SH, (
        "PICKER_SH changed shape. That is a SHELL SCRIPT running with the "
        "private repository rows on its stdin, so the change needs reading, not "
        "a test edit. If it is right, paste this in:\n\n"
        f"EXPECTED_PICKER_SH = {MO.PICKER_SH!r}\n")


@pytest.mark.parametrize("metachar,what", [
    (";", "a second command"),
    ("|", "a pipe — `| tee /tmp/x` writes the private rows to disk"),
    ("&", "backgrounding, or `&&` chaining"),
    ("`", "a backtick substitution"),
    ("$(", "a command substitution — `notify-send \"$(cat \"$1\")\"` puts every "
           "row in a toast argv"),
    (">>", "an APPEND, which would accumulate rows across clicks"),
])
def test_the_picker_shell_script_carries_no(metachar, what):
    """The exact-string pin above already forbids these — this says WHY in the
    failure message, and keeps saying it if someone relaxes that pin to a
    substring check. Two guards on one property, on purpose: the hazard here is
    disclosure, and the exact-string pin is the kind a future edit "fixes"."""
    assert metachar not in MO.PICKER_SH, f"PICKER_SH gained {metachar!r}: {what}"


def test_the_picker_shell_script_has_EXACTLY_the_two_redirections():
    """Both, and no more: stdin from the rows FIFO, stdout to the choice FIFO.
    A third redirection is a third destination for the rows.

    ⚠ SPLIT WITH THE SHELL'S OWN QUOTING RULES, NOT COUNTED AS CHARACTERS — the
    first version of this did the latter and was WRONG: `>` also appears inside
    `--prompt="mention > "` and `--pointer=">"`, so a raw `count(">")` is 3 and
    says nothing about where the rows go. `shlex` is the same lexer
    `_picker_flags()` uses, so a `>` that is quoted is data and a `>` that is
    not is a destination."""
    import shlex
    tokens = shlex.split(MO.PICKER_SH)
    redirs = [t for t in tokens if t.startswith(("<", ">"))]
    assert redirs == ["<$1", ">$2"], (
        f"PICKER_SH's redirections are {redirs}, expected exactly the rows FIFO "
        f"in and the choice FIFO out: {MO.PICKER_SH}")


def test_the_shell_script_pin_can_actually_FIRE():
    """POSITIVE CONTROL on the three guards above, because a pin nobody has
    watched reject anything is indistinguishable from one wired to nothing.

    🔴 AND IT RECORDS WHICH GUARD CATCHES WHAT. The leaky mutant is the real
    hazard — a pipe that copies every private row to a file — and it passes the
    REDIRECTION check (still one `<$1`, one `>$2`). Only the exact-string pin
    and the metacharacter check see it. That is the argument for keeping all
    three rather than "simplifying" to the tidiest one."""
    import shlex
    leaky = 'fzf -i <"$1" | tee /tmp/picker.log >"$2"'
    assert leaky != EXPECTED_PICKER_SH, "the exact-string pin catches it"
    assert "|" in leaky, "the metacharacter check catches it"
    redirs = [t for t in shlex.split(leaky) if t.startswith(("<", ">"))]
    assert redirs == ["<$1", ">$2"], (
        f"the redirection check does NOT catch this mutant ({redirs}) — which "
        f"is why it is not the only guard")


def test_the_picker_terminal_does_NOT_copy_a_selection_to_the_CLIPBOARD(
        monkeypatch):
    """🔴 A SINK ROFI NEVER HAD, AND ONE THE MODULE'S OWN ENUMERATION OMITS.
    The picker inherits `~/.config/alacritty/alacritty.toml`, where this repo
    sets `selection.save_to_clipboard = true` — so a drag across the list would
    put PRIVATE repository names in the X CLIPBOARD, which OUTLIVES the pick and
    is readable by every X client. fzf's mouse reporting makes a plain drag
    fzf's rather than alacritty's, which is mitigation and not a guarantee."""
    term, _url = _drive_picker(monkeypatch)
    assert "selection.save_to_clipboard=false" in term.argv, (
        "the picker terminal no longer disables clipboard-on-select: a "
        "drag-selection would copy private repository names into the X "
        "clipboard, where they outlive the pick")
    # It must be an alacritty `-o` OVERRIDE, not a bare word somewhere.
    i = term.argv.index("selection.save_to_clipboard=false")
    assert term.argv[i - 1] == "-o", term.argv


def test_a_selection_made_BEFORE_the_rows_are_drained_is_NOT_thrown_away(
        monkeypatch):
    """🔴 THE OPERATOR'S ANSWER, DISCARDED, PLUS A FALSE ERROR TOAST.

    fzf answers on the first keypress; it does not wait to drain the list. It
    then closes its read end, so the next `os.write` raises `BrokenPipeError` —
    an `OSError`, which used to propagate to `pick()`'s handler, fire "could not
    show the mention picker", and return "" while the chosen row was ALREADY
    sitting in the choice FIFO.

    MEASURED: invisible at 392 rows (~21 KB) and broken at 1,200 (~67 KB) and
    3,000 (~172 KB). The boundary is the 64 KiB pipe buffer — below it the whole
    payload lands in one write before fzf can answer — which is the SAME
    threshold the open-order deadlock fix exists for.

    🔴 AND THE FAKE HAD TO CHANGE TO SEE IT. `_FakeTerminal` drained the whole
    FIFO before answering, i.e. it was a friendlier peer than fzf, so every
    transport test here was structurally blind to this. `answer_early=True` is
    what makes the case reachable at all."""
    cands = [{"platform": "github", "id": "12",
              "url": f"https://github.com/owner{i}/repo{i}/pull/12"}
             for i in range(3000)]
    payload = len("\n".join(MO.picker_rows(cands)))
    assert payload > 150_000, f"fixture no longer overshoots a pipe buffer: {payload}"
    chosen_row = MO.picker_rows(cands)[7]

    said = []
    monkeypatch.setattr(MO, "notify", lambda s, b="": said.append((s, b)))
    _term, url = _drive_picker(monkeypatch, cands, choose=chosen_row,
                               answer_early=True)
    assert url == cands[7]["url"], (
        "the selection was thrown away when fzf hung up before the payload was "
        "fully written")
    assert not said, f"a false error toast fired on a normal early answer: {said}"


def test_the_early_answer_fixture_is_not_vacuous(monkeypatch):
    """POSITIVE CONTROL on the fixture: at a SMALL payload the same early-answer
    peer must also work. If this went red the test above would be measuring a
    broken fake rather than the code."""
    row = MO.picker_rows(ONE_CANDIDATE)[0]
    _term, url = _drive_picker(monkeypatch, choose=row, answer_early=True)
    assert url == ONE_CANDIDATE[0]["url"]


# --------------------------------------------------------------------------- #
# 🔴 THREE SILENCES THAT USED TO LOOK ALIKE
# --------------------------------------------------------------------------- #
def test_a_TIMEOUT_still_toasts_the_way_rofi_did(monkeypatch):
    """🔴 AN UNDECLARED BEHAVIOUR CHANGE, RESTORED. rofi's `timeout=120` raised
    `TimeoutExpired`, which the old `except` turned into a toast. The first
    version of this rewrite returned "" and said nothing, and the PR body
    claimed "same behaviour as before" — it was not.

    A dismissal stays silent; a timeout does not. They are different events and
    only one of them means the operator decided something."""
    class Wedged:
        def poll(self):
            return None      # alive, and never answers

        def terminate(self):
            pass

    monkeypatch.setattr(MO, "PICKER_TIMEOUT", 0.3)
    monkeypatch.setattr(MO.subprocess, "Popen", lambda *a, **k: Wedged())
    said = []
    monkeypatch.setattr(MO, "notify", lambda s, b="": said.append((s, b)))
    assert MO.pick(ONE_CANDIDATE) == ""
    # 🔴 THE TOKEN IS IN THE MESSAGE, NOT ONLY IN THE SOURCE LINE. The mutation
    # battery scores a row by grepping pytest's `E ` lines for the phrase the
    # row names, and `assert said and "timed out" in said[0][0]` short-circuits
    # on an empty list — so the mutant died but was scored KILLED-WRONG-REASON,
    # which is a scoring blind spot, not a passing guard.
    assert said, ("the picker timed out and toasted NOTHING — an abandonment "
                  "and a dismissal must not look the same")
    assert "timed out" in said[0][0], said


def test_a_terminal_that_DIED_before_showing_anything_says_so(monkeypatch):
    """🔴 A CLICK THAT DID NOTHING, WITH NO EXPLANATION — the dead end this
    whole handler replaced. rofi had no analogue (a missing binary is a
    `FileNotFoundError` at spawn), but a terminal that starts and whose `fzf` is
    missing from the wrapper's PATH exits instantly, and that used to be
    swallowed as a dismissal."""
    class DeadTerminal:
        def poll(self):
            return 1

        def terminate(self):  # pragma: no cover — poll() already reports exit
            pass

    monkeypatch.setattr(MO.subprocess, "Popen", lambda *a, **k: DeadTerminal())
    said = []
    monkeypatch.setattr(MO, "notify", lambda s, b="": said.append((s, b)))
    assert MO.pick(ONE_CANDIDATE) == ""
    # Same short-circuit hazard as the timeout test above — see its comment.
    assert said, ("the picker could not open and said nothing — the click "
                  "produced no window and no explanation")
    assert "could not open" in said[0][0], said


def test_a_DISMISSAL_is_still_SILENT(monkeypatch):
    """🔴 THE HALF THAT MUST NOT REGRESS while the two above were added. A toast
    after a dismissal fires after EVERY dismissal, which is the noise the note
    above the list exists to avoid making."""
    said = []
    monkeypatch.setattr(MO, "notify", lambda s, b="": said.append((s, b)))
    _term, url = _drive_picker(monkeypatch, choose=None)
    assert url == ""
    assert not said, f"a dismissal toasted: {said}"


def test_no_toast_body_can_name_a_REPOSITORY(monkeypatch, universe):
    """🔴 THE NEW TOASTS ARE A NEW SINK, so they go through the same guard as
    every other one. `notify()` prints to stderr AND to `notify-send` argv."""
    for popen in (lambda *a, **k: _Dead(), lambda *a, **k: _Wedged()):
        said = []
        monkeypatch.setattr(MO, "notify", lambda s, b="": said.append(f"{s} {b}"))
        monkeypatch.setattr(MO, "PICKER_TIMEOUT", 0.3)
        monkeypatch.setattr(MO.subprocess, "Popen", popen)
        cands = MO.universe_candidates("12", sorted(FAKE_UNIVERSE.values()))
        assert MO.pick(cands, mesg="nothing here knows widget#12") == ""
        assert said, "positive control: a toast really fired"
        _no_universe_token_anywhere(" ".join(said), "PICKER TOAST DISCLOSURE")


class _Dead:
    def poll(self):
        return 1

    def terminate(self):  # pragma: no cover
        pass


class _Wedged:
    def poll(self):
        return None

    def terminate(self):
        pass


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


# --------------------------------------------------------------------------- #
# 🔴 THE MAPPING IS HAND-REGENERATED AND NOTHING CONVERGES IT
#
# `scripts/regen-known-repos.py` is run by hand: no timer in `nix/` references
# it. So a repository created since the last run is invisible to EVERY
# resolution path, and the only symptom is a picker appearing where a page used
# to open — which reads as "the picker is noisy", not as "my mapping is old".
# The age is therefore measured and surfaced. These pin that it is measured at
# TWO points rather than at one, because a threshold checked on one side of
# itself cannot tell a working guard from a constant.
# --------------------------------------------------------------------------- #
def _mapping_aged(tmp_path, days: float) -> Path:
    """A mapping of a chosen age holding `FAKE_UNIVERSE` — the SAME rows the
    disclosure guards assert are absent. It used to hold one unrelated row, which
    made those guards' KEY half vacuous on every path that re-reads the file:
    `refuse()` calls `load_known_repos()` afresh, so a mutant leaking it leaked
    names no assertion was looking for."""
    p = tmp_path / "known_repos.json"
    p.write_text(json.dumps(FAKE_UNIVERSE))
    when = time.time() - days * 86400
    os.utime(p, (when, when))
    return p


@pytest.mark.parametrize("days,stale", [
    (0.0, False),                            # just regenerated
    (MO.STALE_MAPPING_DAYS - 1, False),      # inside the window
    (MO.STALE_MAPPING_DAYS + 1, True),       # just outside it
    (90.0, True),                            # long gone
])
def test_the_mapping_age_is_measured_at_FOUR_points(tmp_path, days, stale):
    """Both sides of the threshold AND a middle. The note must name a COUNT and
    the regenerator, and must never name a row."""
    p = _mapping_aged(tmp_path, days)
    measured = MO.mapping_age_days(p)
    assert measured is not None and abs(measured - days) < 0.01, measured
    note = MO.staleness_note(p)
    assert bool(note) is stale, f"{days}d -> {note!r}"
    if stale:
        assert "regen-known-repos.py" in note, note
        _no_universe_token_anywhere(note, "STALENESS-NOTE DISCLOSURE")


def test_an_ABSENT_mapping_has_an_UNKNOWN_age_not_a_fresh_one(tmp_path):
    """🔴 None MEANS UNMEASURED. Answering "0 days" for a file that does not
    exist would report the staleness signal as clean on exactly the host it
    cannot measure — the silent zero this module is written against. The absent
    case has its OWN reason (`universe_reason`), so the staleness note stays
    empty rather than inventing a second one."""
    assert MO.mapping_age_days(tmp_path / "nothing-here.json") is None
    assert MO.staleness_note(tmp_path / "nothing-here.json") == ""


def test_a_STALE_mapping_is_NAMED_in_the_refusal_beside_the_primary_cause(
        spy, monkeypatch, tmp_path):
    """🔴 APPENDED, NOT SUBSTITUTED — the same rule the advice follows. "no
    repository owner is known for it" is the primary fact and staleness is the
    ACTIONABLE addition; reporting only one of them is how the operator ends up
    regenerating nothing, or regenerating for the wrong reason."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: dict(FAKE_UNIVERSE))
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH",
                        _mapping_aged(tmp_path, MO.STALE_MAPPING_DAYS + 30))
    notices = []
    monkeypatch.setattr(MO, "notify", lambda *a, **k: notices.append(a))
    # `--print` bars the picker, which is what routes this to a refusal at all.
    assert MO.main(["--print", "zzznosuchrepo#77"]) == 1
    body = notices[-1][1]
    assert "days old" in body, f"the refusal never named the mapping's age: {body}"
    assert "regen-known-repos.py" in body, body
    assert "owner/repo#N" in body, "the actionable advice was dropped"


def _shadowed_mapping_host(monkeypatch, tmp_path, days: float):
    """The one state in which a NON-`--print` refusal can name the mapping's age,
    built end-to-end rather than by stubbing the branch's own inputs.

    🔴 WHY IT LOOKS ABSURD AND IS NOT. A round-2 audit read the branch as dead
    code: `universe_reason()` returns "" only when `clean_repo_map` kept a row,
    `repo_universe` filters on the SAME regex, and `discover_repos` only ADDS —
    so a non-empty mapping was said to guarantee a non-empty universe and hence a
    picker. The missing step is that `discover_repos` also OVERWRITES, and
    `parse_owner_repo` is LOOSER than `OWNER_REPO_VALUE_RE`: it asks only for two
    `/`-separated segments, so `https://github.com/-acme/widget.git` yields the
    non-empty `-acme/widget`, which the value regex rejects. A checkout whose
    DIRECTORY NAME shadows the mapping's key therefore replaces a valid row with
    an invalid one — reason "", universe empty, refusal reached.
    """
    mapping = tmp_path / "shadowed.json"
    mapping.write_text(json.dumps({"widget": "acme/widget"}))
    when = time.time() - days * 86400
    os.utime(mapping, (when, when))
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", mapping)
    ws = tmp_path / "workspace"
    (ws / "widget" / ".git").mkdir(parents=True)
    monkeypatch.setattr(MO, "WORKSPACE", ws)
    # Two path segments, so `parse_owner_repo` accepts it; a leading `-`, so
    # `OWNER_REPO_VALUE_RE` does not.
    monkeypatch.setattr(MO, "repo_of_checkout", lambda p: "-acme/widget")
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "")
    return mapping


def test_the_SHADOWED_mapping_state_is_the_one_the_branch_needs(monkeypatch,
                                                                tmp_path):
    """The premise, asserted separately from the behaviour. If any of these three
    stopped holding, the test below would still pass — for the wrong reason, or
    by never reaching the branch at all."""
    _shadowed_mapping_host(monkeypatch, tmp_path, MO.STALE_MAPPING_DAYS + 30)
    assert MO.parse_owner_repo("https://github.com/-acme/widget.git") == "-acme/widget"
    assert MO.load_known_repos() == {"widget": "acme/widget"}
    assert MO.universe_reason() == "", (
        "the mapping must PARSE and hold a row, or `reason` wins and the "
        "staleness branch is not the code under test")
    assert MO.repo_universe(MO.discover_repos()) == [], (
        "the universe must be EMPTY, or the picker is shown and no refusal "
        "happens at all")


def test_a_NON_print_refusal_CAN_still_name_the_mapping_AGE(monkeypatch,
                                                            tmp_path):
    """🔴 THE REGRESSION TEST FOR A BRANCH AN AUDIT CALLED UNREACHABLE. Deleting
    it left the whole suite green — 264/264 — because the only refusal test that
    named an age used `--print`, which takes the OTHER arm. Watched to fail with
    the branch removed; see `refuse()` for the reachability argument.

    Not `--print`: this is the interactive path, refusing because there is
    nothing to put in a picker."""
    _shadowed_mapping_host(monkeypatch, tmp_path, MO.STALE_MAPPING_DAYS + 393)
    notices = []
    monkeypatch.setattr(MO, "notify", lambda *a, **k: notices.append(a))
    monkeypatch.setattr(MO, "pick", lambda *a, **k: pytest.fail(
        "a picker was raised — the universe was not empty and this test is "
        "no longer exercising the refusal path"))
    assert MO.main(["zzznosuchrepo#77"]) == 1
    body = notices[-1][1]
    # The PRIMARY fact, un-substituted.
    assert "no repository owner is known for it" in body, body
    # The ADDITIVE one — the branch under test.
    assert "days old" in body, (
        f"a non-`--print` refusal on a host whose mapping is 400 days old "
        f"never named the age: {body}")
    assert "regen-known-repos.py" in body, body
    # And the advice is still appended, not replaced.
    assert "owner/repo#N" in body, body


def test_a_NON_print_refusal_on_a_FRESH_mapping_adds_no_age(monkeypatch,
                                                            tmp_path):
    """The negative control for the test above, on the SAME state. Without it a
    `staleness_note` that complained unconditionally would satisfy every
    assertion there, and the branch would be pinned as noise rather than as a
    signal."""
    _shadowed_mapping_host(monkeypatch, tmp_path, 0.0)
    notices = []
    monkeypatch.setattr(MO, "notify", lambda *a, **k: notices.append(a))
    assert MO.main(["zzznosuchrepo#77"]) == 1
    body = notices[-1][1]
    assert "no repository owner is known for it" in body, body
    assert "days old" not in body, body


def test_a_FRESH_mapping_adds_NO_staleness_noise_to_the_refusal(
        spy, monkeypatch, tmp_path):
    """The negative control for the test above. Without it, a `staleness_note`
    that complained unconditionally would satisfy every assertion there."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: dict(FAKE_UNIVERSE))
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", _mapping_aged(tmp_path, 0.0))
    notices = []
    monkeypatch.setattr(MO, "notify", lambda *a, **k: notices.append(a))
    assert MO.main(["--print", "zzznosuchrepo#77"]) == 1
    assert "days old" not in notices[-1][1], notices[-1]


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
    "acme/widget/",      # trailing slash -> .../acme/widget//pull/12
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
# from it may ever be written to a fixture, a log or a spool.
#
# 🔴 THE KEYS ARE NOT SUBSTRINGS OF THE VALUES, AND THAT IS THE WHOLE FIXTURE.
# A mapping is `{checkout directory name: "owner/repo"}` and the two halves are
# genuinely independent — `regen-known-repos.py` handles `vendored-plotwidget ->
# rivalorg/plotwidget` on purpose. This fixture used to spell them
# `trowelcast -> gardenersguild/trowelcast`, so every disclosure guard could
# iterate `.values()` and LOOK total while being blind to a KEY leak: asserting
# `"gardenersguild/trowelcast" not in output` says nothing about an output
# containing only `trowelcast`. A round-2 audit's mutant — a "did you mean?"
# line carrying `", ".join(sorted(load_known_repos()))`, i.e. every private repo
# NAME — reached `notify()` (stderr AND `notify-send`) and survived all 127
# tests. Keys, values and owners are now pairwise distinct, and distinct from
# every constant these assertions name (`kubectl-neat`, `zzznosuchrepo`,
# `regen-known-repos.py`, `offered`), so no guard can pass by coincidence.
#
# 🔴 NOTHING HERE RAISES A WINDOW. `pick` is stubbed by the `spy` fixture, or
# `subprocess.run` is replaced. Raising a window takes the operator's screen.
# --------------------------------------------------------------------------- #
FAKE_UNIVERSE = {
    "loamfield": "gardenersguild/trowelcast",
    "pegboard": "hobbyist/plotwidget",
    "quarryside": "rivalorg/spadeworks",
}

# Keys, owners and full names, as one flat set. THE thing no sink may name.
FAKE_UNIVERSE_TOKENS = (
    set(FAKE_UNIVERSE)
    | set(FAKE_UNIVERSE.values())
    | {v.split("/")[0] for v in FAKE_UNIVERSE.values()}
    | {v.split("/")[1] for v in FAKE_UNIVERSE.values()}
)


def test_the_fixtures_KEYS_and_VALUES_are_pairwise_distinct():
    """🔴 THE GUARD ON THE GUARDS. Every disclosure assertion below is only as
    wide as this fixture: if a key were a substring of its value, a `.values()`
    check would appear to cover the key half and a key-only leak would walk
    straight through. TWELVE distinct tokens over three rows — key, full
    `owner/repo`, bare owner, bare repo-half — is what makes
    `FAKE_UNIVERSE_TOKENS` a real ledger rather than three names spelled twice.
    (Nine of the twelve carry no slash; that count belongs to the substring
    sweep below, not to the ledger, and this docstring used to attach it to the
    wrong object.)
    """
    assert len(FAKE_UNIVERSE_TOKENS) == 4 * len(FAKE_UNIVERSE), FAKE_UNIVERSE_TOKENS
    for a in FAKE_UNIVERSE_TOKENS:
        for b in FAKE_UNIVERSE_TOKENS:
            if a != b and "/" not in a and "/" not in b:
                assert a not in b, (
                    f"{a!r} is a substring of {b!r} — a leak of the first would "
                    f"be indistinguishable from a leak of the second")


def _no_universe_token_anywhere(everywhere: str, label: str) -> None:
    """🔴 KEYS *AND* VALUES *AND* OWNERS. The guards this replaces iterated
    `FAKE_UNIVERSE.values()` alone, which pins one of the three spellings a leak
    can take. `load_known_repos()` returns a dict, so the single most natural
    "did you mean?" mutant — `", ".join(sorted(load_known_repos()))` — leaks the
    KEYS, and nothing in this file could see it."""
    for token in sorted(FAKE_UNIVERSE_TOKENS):
        assert token not in everywhere, f"{label}: {token}"


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
        "https://github.com/gardenersguild/trowelcast/pull/77",
        "https://github.com/rivalorg/spadeworks/pull/77"]
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


@pytest.mark.parametrize("text", ["audit-pr 1291", "/audit-pr 1291"])
def test_an_audit_pr_REFERENCE_resolves_through_the_PANE_repo(spy, text):
    """🔴 THE CLICK PATH FOR THE ONE WORDY SHAPE THAT IS CLICKABLE. `audit-pr N`
    names a number and NO repository, so it resolves exactly the way a bare `#N`
    does: the tmux pane's repo when there is one. It is the same ladder every
    other no-owner reference walks.

    🔴 AND IT IS OFFERED, NOT OPENED. This test used to assert an `open` — one
    candidate, so `main()`'s shortcut fired — which made this the FIRST shape in
    the handler that could open a page on a repository guessed from the pane
    alone. `repo_source == "default"` now suppresses the shortcut, so the pane's
    repo arrives as a row to confirm. See
    `test_a_GUESSED_repo_is_never_auto_opened_whatever_the_shape` for the rule
    stated where it is decided."""
    assert MO.main([text]) == 0
    # It really did walk the measurement pass — the URL came from the pane, not
    # from something the text carried. ONE row, and the `spy` picker selects it,
    # so the open still happens — after a keystroke, which is the whole
    # difference. The exact SEQUENCE is asserted, not a set: the pane must be
    # measured before the picker is raised, and the picker before the open.
    assert spy == ["tmux", "discover", ("pick", 1),
                   ("open", "https://github.com/civitai/talos-infra/pull/1291")], spy


# --------------------------------------------------------------------------- #
# 🔴 A GUESSED REPOSITORY IS OFFERED, NEVER OPENED
#
# `repo_source == "default"` is the bottom rung of `mention_scan`'s attribution
# ladder: the text named no repository and the answer came from `tmux_pane_repo`
# — which answers for the most recently ACTIVE tmux client, not necessarily the
# pane whose text was clicked. Every rung above it is evidence ABOUT the
# reference; this one is evidence about the WINDOW.
#
# MEASURED with the pane forced to `WRONGORG/wrongrepo`, base vs merged:
#
#   text             base                 merged (before this rule)
#   audit-pr 1291    REFUSE               OPEN-DIRECTLY WRONGORG/wrongrepo#1291
#   #1291            picker (2 rows)      picker (2 rows)
#
# The bare `#N` was protected only INCIDENTALLY — it has a clawgate sibling, so
# it was two candidates. Nothing suppressed the shortcut for a `default`-sourced
# row, so the next single-candidate shape would have inherited the defect.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", ["audit-pr 1291", "/audit-pr 1291", "#1291"])
def test_a_GUESSED_repo_is_never_auto_opened_whatever_the_shape(monkeypatch, text):
    """🔴 THE RULE, PINNED AT THE `repo_source` LEVEL RATHER THAN ON `audit-pr`.

    The pane names a repository the text never mentions. Nothing may open
    without a selection — for the wordy shape that has ONE candidate, for the
    bare `#N` that has two, and for whatever is added next."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: dict(FAKE_UNIVERSE))
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "wrongorg/wrongrepo")
    monkeypatch.setattr(MO, "open_url",
                        lambda url: pytest.fail(f"AUTO-OPENED a guessed repo: {url}"))
    seen = {}
    monkeypatch.setattr(MO, "pick",
                        lambda c, mesg="": seen.update(
                            urls=[x["url"] for x in c], mesg=mesg) or "")
    assert MO.main([text]) == 0
    # POSITIVE CONTROL — the pane's repo really WAS offered. A run that resolved
    # nothing would satisfy "did not auto-open" while proving nothing.
    assert "https://github.com/wrongorg/wrongrepo/pull/1291" in seen["urls"], seen


def test_the_picker_for_a_guessed_repo_SAYS_WHY(monkeypatch):
    """A picker whose top row was GUESSED, with no explanation, reads as a broken
    handler asking the operator to confirm the obvious. The picker cannot report a
    reason after a dismissal (see `pick`), so the reason goes above the choice.

    ⚠ THIS TEST USED TO ASSERT `n == 1` AND WAS RENAMED FROM
    `test_the_one_row_picker_...`. That single row was the defect the operator
    reported from the real click path on 2026-09-07 — the guess could not be
    overridden — so the row COUNT it pinned is superseded. What survives
    unchanged is the claim its name makes and the disclosure rule, both of which
    matter more than the count: the note must explain itself, and it must not
    name a repository.

    The count is now pinned by
    `test_a_GUESSED_repo_is_offered_WITH_the_whole_universe_beneath_it`, which
    is the regression for the report."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: dict(FAKE_UNIVERSE))
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "wrongorg/wrongrepo")
    seen = {}
    monkeypatch.setattr(MO, "pick",
                        lambda c, mesg="": seen.update(n=len(c), mesg=mesg) or "")
    assert MO.main(["audit-pr 1291"]) == 0
    assert seen["n"] > 1, ("the guess must not be the ONLY option — that is the "
                           "reported defect, not the design")
    assert "audit-pr 1291" in seen["mesg"], seen
    assert "tmux pane" in seen["mesg"], seen
    _no_universe_token_anywhere(seen["mesg"], "GUESSED-NOTE DISCLOSURE")


def test_the_bare_hash_N_picker_SAYS_the_github_row_is_a_guess(monkeypatch):
    """🔴 SUPERSEDES `test_the_note_is_NOT_attached_to_the_ordinary_bare_hash_N_
    picker`, WHICH PINNED `mesg == ""` AND `2` ROWS ON PURPOSE.

    That test's claim was that a bare `#N` with a pane repo is not a dead end,
    so a note above it would be "a line of apology" taxing every click. Half of
    it survives — the two measured rows are still FIRST, so the common case is
    still one Enter — and half is OVERRULED: the OPERATOR asked for the universe
    here on 2026-09-08 ("fix the bare-#N and any other cases left unfixed"),
    because when the pane guess is wrong the right repo was unreachable. Once
    several hundred rows are in the list, an unexplained picker is the bug and
    the note is what stops it reading as one.

    ⚠ THE NOTE MUST NAME THE ROW, NOT "THE FIRST ROW". The guess is at row 2
    here — the clawgate task is above it — and #1380's wording said FIRST
    unconditionally, which was true only of the `audit-pr` shape."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: dict(FAKE_UNIVERSE))
    monkeypatch.setattr(MO, "load_known_universe", lambda *a, **k: [])
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "wrongorg/wrongrepo")
    seen = {}
    monkeypatch.setattr(
        MO, "pick",
        lambda c, mesg="": seen.update(rows=list(c), mesg=mesg) or "")
    assert MO.main(["#1291"]) == 0
    urls = [c["url"] for c in seen["rows"]]
    # clawgate, the pane guess, then the three FAKE_UNIVERSE repos.
    assert len(urls) == 5, urls
    assert urls[0] == "https://clawgate.zacx.dev/tasks/1291", urls
    assert "wrongorg/wrongrepo" in urls[1], urls
    assert "Row 2" in seen["mesg"], seen["mesg"]
    assert "3 rows below it" in seen["mesg"], seen["mesg"]
    assert "guess" in seen["mesg"].lower(), seen["mesg"]
    assert "nothing here knows" not in seen["mesg"], seen["mesg"]
    _no_universe_token_anywhere(seen["mesg"], "BARE-HASH-N GUESS NOTE")


@pytest.mark.parametrize("text,expected", [
    # `mapped` — the mapping resolved the owner for a repo the TEXT named.
    ("loamfield#12", "https://github.com/gardenersguild/trowelcast/pull/12"),
    # `explicit` — the operator wrote the owner out.
    ("civitai/talos-infra#1065",
     "https://github.com/civitai/talos-infra/pull/1065"),
])
def test_a_repo_the_TEXT_named_still_opens_with_no_picker(monkeypatch, text,
                                                          expected):
    """🔴 THE NEGATIVE CONTROL FOR THE SUPPRESSION ITSELF. A rule that turned
    EVERY single candidate into a picker would satisfy every assertion above
    and put a keystroke in front of the two shapes that carry their own
    evidence. `mapped` and `explicit` are rungs above `default` and must still
    open directly."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: dict(FAKE_UNIVERSE))
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "wrongorg/wrongrepo")
    monkeypatch.setattr(MO, "pick",
                        lambda c, mesg="": pytest.fail(f"asked about {text}"))
    opened = []
    monkeypatch.setattr(MO, "open_url", lambda url: opened.append(url) or 0)
    assert MO.main([text]) == 0
    assert opened == [expected], opened


def test_the_suppression_reads_mention_scans_OWN_source_constant():
    """One rule, one place: `main()` branches on the ladder's weakest rung, and
    a hand-spelled `"default"` here would silently stop firing the day
    `mention_scan` renamed the constant — with every suite green, because the
    branch would simply never be taken."""
    import mention_scan as MS  # noqa: PLC0415 — see `_load_handler`'s sys.path

    assert MO.SOURCE_DEFAULT is MS.SOURCE_DEFAULT
    assert MO.SOURCE_DEFAULT == "default"


@pytest.mark.parametrize("text", ["audit-pr 1291", "/audit-pr 1291"])
def test_an_audit_pr_reference_with_NO_pane_repo_offers_the_PICKER(
        universe, monkeypatch, text):
    """The other half of the ladder. With nothing to attribute it to, the
    reference becomes a CHOICE over the local universe — never a guess, and never
    the dead-end toast."""
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "")
    monkeypatch.setattr(MO, "open_url", lambda url: pytest.fail(f"opened {url}"))
    seen = {}
    monkeypatch.setattr(MO, "pick",
                        lambda c, mesg="": seen.update(rows=len(c), mesg=mesg) or "")
    assert MO.main([text]) == 0
    assert seen["rows"] == 3, seen
    assert seen["mesg"], "the universe picker was raised with no explanation"


def test_an_OVER_LONG_audit_pr_number_is_REFUSED_not_truncated(spy):
    """🔴 THE `{1,6}` CONTRACT AT THE HANDLER END. The alacritty hint underlines
    six digits so the whole run arrives here; the strict scanner's `\\d{1,5}`
    plus its trailing-digit guard then refuses it. If this ever opened PR 12345
    the loose-hint/strict-handler contract would be broken in the direction that
    opens a confident wrong page."""
    assert MO.main(["audit-pr 123456"]) == 1
    assert not [c for c in spy if isinstance(c, tuple) and c[0] == "open"], spy


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

    A mapping hit is evidence about the name; a universe row is only an option.

    🔴 THIS TEST NOW CARRIES THE WHOLE `offered_universe` GUARD. It used to share
    it with a six-digit case, and six digits no longer reaches the picker at all
    — so if this one stopped exercising the one-entry shortcut, nothing would.
    The message says what a failure MEANS rather than what was asserted, because
    a mutation battery reads the `E ` line to decide whether the row it named is
    the row that fired."""
    monkeypatch.setattr(MO, "discover_repos",
                        lambda *a, **k: {"spadeworks": "rivalorg/spadeworks"})
    assert MO.main(["zzznosuchrepo#77"]) == 0
    assert ("pick", 1) in spy, (
        f"a universe row was AUTO-OPENED, bypassing the picker: {spy}")
    # The claim is ORDER, not absence: `spy`'s `pick` answers with row 0, so an
    # `open` AFTER a pick is what a selection legitimately does. What must never
    # happen is an open with no pick in front of it.
    kinds = [c[0] for c in spy if isinstance(c, tuple)]
    assert kinds.index("pick") < kinds.index("open"), (
        "a universe row was AUTO-OPENED, bypassing the picker")


def test_dismissing_the_universe_picker_opens_NOTHING(spy, universe, monkeypatch):
    """`pick()`'s contract, preserved exactly: a dismissal is not an error and it
    must not open anything. Asserted on the OPENER, not only on the exit code —
    an exit code cannot tell you a browser was launched."""
    monkeypatch.setattr(MO, "pick", lambda c, mesg="": "")
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
    monkeypatch.setattr(MO, "pick", lambda c, mesg="": picks.append(len(c)) or "")
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
                                                     tmp_path, flag, expected):
    """🔴 THE THIRD WAY THE PICKER CANNOT BE SHOWN, and it is not an empty
    universe — the universe here is fine and non-empty. A refusal reporting "no
    repo mapping on this host" would send the operator to run
    `regen-known-repos.py` over a mapping that was never the problem.

    Both flags are named in the body because they are the operator's own lever:
    drop the flag and the picker appears.

    🔴 THE MAPPING IS PATCHED TO A HEALTHY TMP FILE, and that is not tidiness.
    `refuse()` reads `KNOWN_REPOS_PATH`; without this the test would consult the
    OPERATOR'S REAL mapping, so it asserted one thing on the dev host (file
    present) and another in the nix sandbox tier (HOME is empty, so the file is
    absent) — the config-blind shape, green for different reasons in the two
    tiers. The healthy-mapping case is what this test is about; the ABSENT one
    is the test below, which is the case that was reporting the wrong cause."""
    p = tmp_path / "known_repos.json"
    p.write_text(json.dumps({"plotwidget": "hobbyist/plotwidget"}))
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", p)
    notices = []
    monkeypatch.setattr(MO, "notify", lambda *a, **k: notices.append(a))
    assert MO.main([flag, "zzznosuchrepo#12"]) == 1
    assert expected in notices[-1][1], notices[-1]
    assert "regen-known-repos" not in notices[-1][1], (
        "blamed the mapping for a refusal the FLAG caused")


def test_print_mode_on_a_host_with_NO_mapping_names_the_ACTIONABLE_cause(
        monkeypatch, universe, tmp_path):
    """🔴 BOTH CAUSES ARE TRUE; ONLY ONE IS ACTIONABLE, AND IT WAS THE ONE BEING
    DROPPED. `--print zzz#12` on a host with no mapping at all used to answer
    "--print cannot show the repository picker" and stop — because the flag was
    tested BEFORE `universe_reason()`, not because the flag was the better
    answer. The operator cannot act on "--print is non-interactive"; they can act
    on "run scripts/regen-known-repos.py". A handler whose stated discipline is
    "say WHICH empty this is" reported the un-actionable half.

    Both are asserted, and the flag half is asserted FIRST so this cannot pass by
    having quietly stopped naming the flag."""
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", tmp_path / "nothing-here.json")
    notices = []
    monkeypatch.setattr(MO, "notify", lambda *a, **k: notices.append(a))
    assert MO.main(["--print", "zzznosuchrepo#12"]) == 1
    body = notices[-1][1]
    assert "there is nobody to ask" in body, body
    assert "has no repo mapping" in body, (
        f"--print blamed the FLAG and never named the mapping, which is the "
        f"only cause the operator can act on: {body}")
    assert "regen-known-repos.py" in body, body


def test_no_discovery_still_does_NOT_blame_the_mapping_it_never_read(
        monkeypatch, universe, tmp_path):
    """The mirror of the test above, and the reason the two flags are handled
    differently rather than uniformly. `--no-discovery` means the mapping is
    never opened, so its state is not a cause of anything — naming it would send
    the operator to regenerate a file that was not involved. `--print` DOES read
    it (PASS 2 still runs), which is why it names both."""
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", tmp_path / "nothing-here.json")
    notices = []
    monkeypatch.setattr(MO, "notify", lambda *a, **k: notices.append(a))
    assert MO.main(["--no-discovery", "zzznosuchrepo#12"]) == 1
    body = notices[-1][1]
    assert "resolves only what the text itself carries" in body, body
    assert "regen-known-repos" not in body, (
        f"blamed a mapping --no-discovery never read: {body}")


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
        "https://github.com/gardenersguild/trowelcast/pull/1065")


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


def test_a_SIX_DIGIT_click_gets_a_NAMED_toast_not_a_wall_of_repos(spy, universe,
                                                                  monkeypatch):
    """🔴 THE OPERATOR DECISION, RECORDED. `mention_scan`'s `_NUM` is `\\d{1,5}`
    because nothing on this host — no clawgate task, no GitHub issue in any repo
    the operator touches — reaches six digits; devrc itself is in the 1300s. So a
    six-digit `#N` is ALWAYS a false positive, and every row a universe picker
    could offer for it names an issue no repository has: several hundred URLs
    that all 404 is not a choice, it is a wall with no door.

    `#282828` is the operator's own gruvbox background literal, written in the
    very file that configures this hint. The right answer is "that is a colour",
    said once.

    🔴 IT IS A TOAST, NOT A SILENCE. The dead end this branch replaced was the
    complaint that started the whole change; answering with nothing would be that
    dead end again. So the notification must NAME the number and say what it
    looks like — asserted on the BODY, not merely on the exit code."""
    notices = []
    monkeypatch.setattr(MO, "notify", lambda *a, **k: notices.append(a))
    # 🔴 THE MESSAGE IS ON THE EXIT CODE, and that placement is not cosmetic.
    # When the six-digit branch is removed the picker fires and `main()` returns
    # 0, so THIS is the assertion that goes red first — the picker assertion
    # below never evaluates, and a battery matching on its phrase would report
    # `KILLED-WRONG-REASON` for a row that killed exactly the right test.
    assert MO.main(['background = "#282828";']) == 1, (
        "a six-digit click raised the repository picker for a colour literal")
    assert not [c for c in spy if isinstance(c, tuple) and c[0] == "pick"], (
        f"a six-digit click raised the repository picker for a colour "
        f"literal: {spy}")
    assert not [c for c in spy if isinstance(c, tuple) and c[0] == "open"]
    assert notices, "a six-digit click said NOTHING — the old dead end is back"
    body = notices[-1][1]
    assert "282828" in body and "colour literal" in body, notices[-1]


def test_a_six_digit_literal_BESIDE_a_real_reference_does_not_suppress_it(spy):
    """🔴 WHY `colour_literal_offer` ASKS FOR `span is None` FIRST.

    `offer_number` scans the WHOLE text for the first `#N`, and in
    `background = "#282828"; fixed in talos-infra#1065` the first one is the
    COLOUR — while the scanner's span is the reference. Classifying on the
    number alone would call this click a colour literal, skip discovery, and
    refuse a reference that resolves perfectly well.

    A number the scanner ACCEPTED is a reference by definition, whatever else
    the text contains; the six-digit rule may only speak for text the scanner
    refused outright. This is a command-line surface rather than a click one —
    Alacritty hands over the matched substring, not the whole line — but it is
    the guard that keeps the two classifications from being confused, and it
    survived the whole suite before this test existed."""
    assert MO.main(['background = "#282828"; fixed in talos-infra#1065']) == 0, (
        "a colour literal BESIDE a real reference suppressed the reference")
    assert spy[-1] == ("open", "https://github.com/civitai/talos-infra/pull/1065"), (
        f"a colour literal BESIDE a real reference suppressed the "
        f"reference: {spy}")


def test_a_SIX_DIGIT_click_does_not_pay_for_the_DISCOVERY_fan_out(spy):
    """The universe it will not show costs a `git remote` fan-out plus a tmux
    round-trip to build. A click that already has its answer must not pay for
    one — the same latency argument that keeps `owner/repo#N` out of PASS 2,
    applied at the other end of the path."""
    assert MO.main(["#282828"]) == 1
    assert "discover" not in spy, spy
    assert "tmux" not in spy, spy


def test_a_FIVE_digit_number_still_reaches_the_picker(spy, universe, monkeypatch):
    """🔴 THE OTHER HALF OF THE DECISION, AND THE CONTROL FOR IT. Only SIX digits
    is special-cased; every other unresolvable shape still becomes a choice. A
    branch measured at one point cannot tell "six digits is refused" from "the
    picker is gone", so the bound is measured on both sides of itself.

    Five digits is the widest the scanner ACCEPTS, so this is also the boundary
    case: `#28282` resolves to a clawgate task, is ambiguous, and gets the
    universe appended below it."""
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "")
    assert MO.main(["#28282"]) == 0
    assert ("pick", 4) in spy, spy


# --------------------------------------------------------------------------- #
# 🔴 A DISMISSED PICKER CANNOT BE READ, SO THE DIAGNOSIS GOES ABOVE IT
#
# MEASURED: `kubectl-neat#1` resolved before this branch and now exits 1.
# Interactively the operator gets the fuzzy picker, types the name, fzf
# (`-no-custom`) matches nothing, presses Escape — and `pick()` returns "" for
# BOTH that and a genuine "I changed my mind". The picker reports them identically;
# there is no exit code, no stdout, nothing that separates them. So a toast
# fired after a dismissal would fire after EVERY dismissal, which is noise the
# handler must not make.
#
# The honest place for "this host has no repository called kubectl-neat" is
# therefore BEFORE the choice, on the one surface the universe may already
# reach: a `-mesg` line above the list.
# --------------------------------------------------------------------------- #
def test_the_universe_picker_EXPLAINS_ITSELF_above_the_list(universe, monkeypatch,
                                                            tmp_path):
    """It must name the repo the operator CLICKED — which they already typed —
    plus how many rows are offered, when the mapping was generated, and the
    remedy. Never a row from the universe."""
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", _mapping_aged(tmp_path, 3.0))
    monkeypatch.setattr(MO, "open_url", lambda url: 0)
    seen = {}

    def note_pick(cands, mesg=""):
        seen["mesg"] = mesg
        seen["rows"] = len(cands)
        return ""

    monkeypatch.setattr(MO, "pick", note_pick)
    assert MO.main(["kubectl-neat#1"]) == 0
    # POSITIVE CONTROL: the picker really was raised over the real universe.
    assert seen["rows"] == 3, seen
    mesg = seen["mesg"]
    assert mesg, (
        "the universe picker was raised with NO explanation — a dismissal and "
        "a no-match are then indistinguishable to the operator too")
    assert "kubectl-neat#1" in mesg, mesg
    assert "3 offered" in mesg, mesg
    assert "regen-known-repos.py" in mesg, mesg
    assert "3d ago" in mesg, mesg


def test_the_picker_NOTE_never_names_a_universe_row(universe, monkeypatch,
                                                    tmp_path):
    """🔴 THE NOTE IS THE ONE NEW STRING BUILT WHILE THE WHOLE UNIVERSE IS IN
    HAND, so it is exactly where "did you mean one of these?" would be written
    next. The rows themselves may go to the picker; the note may say only what the
    operator already knows plus two numbers.

    ⚠ IT USED TO CHECK THE VALUE AND THE OWNER AND NOT THE KEY — two spellings
    of three, on a note built beside a dict whose KEYS are the repo names. Every
    token is checked now."""
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", _mapping_aged(tmp_path, 1.0))
    seen = {}
    monkeypatch.setattr(MO, "pick",
                        lambda c, mesg="": seen.update(mesg=mesg) or "")
    assert MO.main(["kubectl-neat#1"]) == 0
    # POSITIVE CONTROL — a note was really built, over a real universe.
    assert "3 offered" in seen["mesg"], seen
    assert MO.load_known_repos() == FAKE_UNIVERSE
    _no_universe_token_anywhere(seen["mesg"], "PICKER-NOTE DISCLOSURE")


def test_the_ORDINARY_picker_gets_no_note(spy, monkeypatch):
    """A picker whose every row is EVIDENCE is not a dead end and needs no
    explanation.

    🔴 ITS SCOPE NARROWED ON 2026-09-08 AND THE DOCSTRING IS REWRITTEN RATHER
    THAN LEFT TO ROT. It used to claim the bare `#N` picker never carries a
    note. That is no longer true on a real host: the universe is now appended
    beneath the guess, and a several-hundred-row picker with no explanation is
    the bug, not the note. What this pins now is the DEGENERATE host the `spy`
    fixture happens to build — its whole universe is the pane's own repo, so the
    dedupe appends nothing, there is nothing to search, and the two measured
    rows still speak for themselves. A comment claiming the wider coverage would
    read as a guard on the common path while guarding only this corner."""
    seen = {}
    monkeypatch.setattr(
        MO, "pick",
        lambda c, mesg="": seen.update(mesg=mesg) or c[0]["url"])
    assert MO.main(["#370"]) == 0
    assert seen["mesg"] == "", seen


def test_the_NOTE_survives_all_the_way_from_main_to_the_picker(
        monkeypatch, universe, tmp_path):
    """🔴 THE WHOLE SEAM IN ONE RUN — `main()` computes the note, `pick()`
    wraps it, and the transport carries it. Every OTHER test on this path stubs
    `pick`, so none of them would notice a note computed and then dropped on the
    floor; the three tests beside `_drive_picker` cover the second half but
    start from a note handed in by hand.

    ⚠ THE ESCAPING TEST THIS REPLACES IS GONE ON PURPOSE. It asserted
    `widget<1> & co` arrived as `widget&lt;1&gt; &amp; co`, because rofi's
    `-mesg` rendered PANGO. fzf's header is plain text, so that expectation is
    now exactly backwards — see
    `test_the_note_is_NOT_pango_escaped_because_fzf_renders_plain_text`."""
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", _mapping_aged(tmp_path, 1.0))
    term = _FakeTerminal(None)
    monkeypatch.setattr(MO.subprocess, "Popen", term.popen)
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "")
    assert MO.main(["zzznosuchrepo#12"]) == 0
    n = int(term.argv[-1])
    header = " ".join(term.rows[:n])
    # POSITIVE CONTROL: rows really were offered under it.
    assert term.rows[n:], "the picker got a header and no candidates"
    assert "zzznosuchrepo#12" in header, header
    assert "3 offered" in header, header
    # …and the header is STILL not a place a repository name may appear.
    _no_universe_token_anywhere(header, "END-TO-END HEADER DISCLOSURE")


def test_a_bare_hash_N_that_the_PANE_attributes_gets_the_universe_BELOW_it(
        spy, universe):
    """🔴 SUPERSEDES `test_a_bare_hash_N_that_the_PANE_already_attributes_does_
    NOT_get_the_universe`, WHOSE ASSERTION WAS `("pick", 2) in spy`.

    Its argument — "the universe is the LAST resort; a measured pane repo is
    evidence, and burying it under 300 options would be a regression dressed as
    a feature" — is a real prior decision, taken in #1380 and written down
    there. The OPERATOR overruled it on 2026-09-08, asking explicitly for "the
    bare-#N and any other cases left unfixed": a pane repo is evidence about the
    WINDOW, not about the reference, and when it is wrong the two-row picker
    offered no way to say so. Same defect as `audit-pr N`, one rung along.

    🔴 WHAT SURVIVES OF THE OLD CLAIM IS PINNED HERE, NOT DROPPED. The two
    MEASURED rows stay first, in their old order — clawgate, then the pane's
    GitHub repo — so the common case is still one Enter and the picker still opens on
    the clawgate row. The universe is strictly APPENDED."""
    assert MO.main(["#370"]) == 0
    # clawgate + the pane repo + the three FAKE_UNIVERSE repos, deduped.
    assert ("pick", 5) in spy, spy
    assert spy[-1] == ("open", "https://clawgate.zacx.dev/tasks/370"), (
        "the clawgate row must still be the first, default-selected one")


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


# --------------------------------------------------------------------------- #
# 🔴 A REFUSAL THAT SHOWS NOTHING IS WORSE THAN THE REFUSAL IT REPLACED
# --------------------------------------------------------------------------- #
def test_notify_send_is_given_a_double_dash_before_the_MESSAGE(real_notify):
    """🔴 MEASURED WITH notify-send 0.8.8, not reasoned about. Several refusal
    bodies begin with a flag NAME — "--print cannot show the repository picker",
    "--no-discovery resolves only what the text itself carries" — because naming
    the operator's own lever is the point. `notify-send` uses GNU getopt, which
    PERMUTES, so it parses that body as an OPTION wherever it sits:

        $ notify-send -a mention-open S "--no-discovery resolves only …"
        Unknown option --no-discovery resolves only …   ; exit 1, NO TOAST
        $ notify-send -a mention-open -- S "--no-discovery resolves only …"
        ; exit 0, one toast

    And `notify()` runs with `check=False`, so that exit 1 is swallowed: the
    handler reports the refusal to a stderr nobody reads and shows nothing at
    all. The `--` is what stops it."""
    MO.notify("cannot resolve zzz#12",
              "--print cannot show the repository picker — there is nobody to ask")
    argv = real_notify[-1]
    assert argv[0] == "notify-send", argv
    assert "--" in argv, (
        f"notify-send got a body starting with `--` and NO `--` terminator, so "
        f"it exits 1 and shows NOTHING: {argv}")
    assert argv.index("--") < argv.index("cannot resolve zzz#12"), argv
    assert argv[-1].startswith("--print"), argv


def test_every_flag_refusal_the_handler_can_produce_survives_notify_send(
        universe, monkeypatch, real_notify, tmp_path):
    """The seam, not the unit: it is `main()`'s refusal bodies that begin with
    `--`, and a `--` added to `notify()` is only useful if those bodies still
    reach it. Both flags are driven through the REAL `notify`."""
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", tmp_path / "nothing-here.json")
    for flag in ("--no-discovery", "--print"):
        real_notify.clear()
        assert MO.main([flag, "zzznosuchrepo#12"]) == 1
        argv = real_notify[-1]
        # POSITIVE CONTROL: this really is a body that starts with a flag name.
        assert argv[-1].startswith("--"), argv
        assert "--" in argv[:-1], (
            f"{flag}: the refusal body starts with a flag name and notify-send "
            f"got no `--`, so the toast never appears: {argv}")


# The universe rows may reach the operator's picker window and NOWHERE else, so
# every sink the handler can write to is enumerated here rather than left to
# whichever one a test happened to think of. A sink missing from this list is a
# hole; adding one to the module means adding it here.
#
# 🔴 THE THREE SINKS ARE STDOUT, STDERR AND THE `notify-send` ARGV — and that is
# the WHOLE list, because those are the only places `mention-open.py` writes.
# It has no log file and no spool: it is a detached one-shot click handler. The
# guard below used to be named `..._a_LOG_a_SPOOL_or_stderr`, which promised
# coverage of two sinks that do not exist and understated the one that does (a
# desktop toast is more public than either). A guard whose NAME is wider than
# its body reads as coverage while providing none, which is worse than none —
# it stops anyone looking. The tailer is the module with a spool, and its own
# disclosure guards are in `test_session_tailer.py`.
def _every_sink(capsys, notify_argv: list[list[str]]) -> str:
    captured = capsys.readouterr()
    return "\n".join([captured.out, captured.err,
                      *(" ".join(a) for a in notify_argv)])


def test_the_universe_never_reaches_STDOUT_STDERR_or_a_desktop_TOAST(
        spy, universe, monkeypatch, capsys, real_notify):
    """🔴 THE DISCLOSURE GUARD FOR PASS 4. The real universe names private
    repositories; the ONLY place it may go is the operator's picker window. This
    asserts the rows exist (a positive control — a test that only checked for
    absence would pass against a picker wired to nothing) and that none of them
    reached stdout, stderr or a desktop notification."""
    seen = {}

    def spy_pick(cands, mesg=""):
        seen["rows"] = MO.picker_rows(cands)
        seen["mesg"] = mesg
        return ""

    monkeypatch.setattr(MO, "pick", spy_pick)
    assert MO.main(["zzznosuchrepo#12"]) == 0
    assert len(seen["rows"]) == 3, "positive control: the picker got real rows"
    # POSITIVE CONTROL 2 — the mapping the handler could reach really held these
    # names, so their absence below is a decision and not an empty fixture. This
    # is what makes a KEY leak visible: the redirected mapping holds the same
    # rows the stubbed `discover_repos` does.
    assert MO.load_known_repos() == FAKE_UNIVERSE
    everywhere = _every_sink(capsys, real_notify)
    _no_universe_token_anywhere(everywhere, "PICKER-PATH DISCLOSURE")


def test_the_REFUSAL_path_names_the_clicked_text_and_never_the_universe(
        universe, monkeypatch, capsys, real_notify, tmp_path):
    """🔴 THE SECOND HALF OF THE SAME GUARD, on the path PASS 4 never reaches.

    `--print` skips PASS 4 entirely, so the handler refuses — but `discovered`
    was already populated by PASS 2 and holds the whole universe. That refusal
    goes through `notify()`, which prints to stderr AND to `notify-send`. Adding
    the universe to that body — "no repo by that name; did you mean one of
    these?" is a natural-looking improvement — leaked every private name, with
    the previous version of this file green.

    The positive controls come FIRST: the refusal really happened, and the
    handler really held the universe at that moment. Without them a stubbed-out
    run that refused for some other reason would satisfy every absence below.

    ⚠ THE MAPPING PATH IS PATCHED so the refusal's reason is a property of this
    fixture rather than of the machine: `refuse()` reads `KNOWN_REPOS_PATH`, and
    the nix sandbox tier runs under an empty HOME, so an unpatched run reports a
    different cause there than on the dev host."""
    monkeypatch.setattr(MO, "KNOWN_REPOS_PATH", _mapping_aged(tmp_path, 1.0))
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "")
    assert MO.main(["--print", "zzznosuchrepo#77"]) == 1
    everywhere = _every_sink(capsys, real_notify)
    # POSITIVE CONTROL 1 — this IS the refusal path, and it named the click.
    assert "cannot resolve zzznosuchrepo#77" in everywhere
    # POSITIVE CONTROL 2 — the universe was in hand and NOT empty at that point,
    # so its absence below is a decision rather than an accident of the fixture.
    assert MO.repo_universe(dict(FAKE_UNIVERSE)) == sorted(FAKE_UNIVERSE.values())
    # POSITIVE CONTROL 3 — and the MAPPING `refuse()` itself re-reads holds the
    # same rows. Without this the KEY half of the guard below is vacuous: the
    # `load_known_repos()` a "did you mean?" mutant would call must return the
    # names the assertion looks for, or it leaks something the guard cannot see.
    assert MO.load_known_repos() == FAKE_UNIVERSE
    _no_universe_token_anywhere(everywhere, "REFUSAL-PATH DISCLOSURE")


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


# --------------------------------------------------------------------------- #
# 🔴 THE PICKER UNIVERSE IS A SEPARATE CORPUS FROM THE RESOLUTION MAPPING
#
# `repo_universe()` used to read `discover_repos().values()`, which silently
# inherited both of the mapping's filters. The mapping answers "what does the
# bare name `foo` mean?", so it MUST drop an issues-disabled repo and MUST drop
# a bare name two owners share. A picker answers "which repo do you want?", its
# rows are fully-qualified, and nothing opens without a selection — so neither
# filter buys anything there and both cost coverage.
#
# MEASURED on the operator's host 2026-09-07: `gh api user/repos` returned 388
# repos; the picker offered 339. 53 repos the operator owns or collaborates on
# could not be reached by typing at the picker at all.
# --------------------------------------------------------------------------- #
UNIVERSE_ONLY = "rivalorg/spadeworks-archived"


def test_repo_universe_offers_a_repo_that_is_ONLY_in_the_universe_file():
    """🔴 THE HEADLINE REGRESSION. Red on pre-change code, where `repo_universe`
    took one argument and read the mapping alone.

    `UNIVERSE_ONLY` stands for the 53 real repos the mapping's filters removed —
    issues disabled, or a bare name two owners share. It is deliberately NOT a
    value of `FAKE_UNIVERSE`, so it can only arrive through the new source."""
    out = MO.repo_universe(dict(FAKE_UNIVERSE), [UNIVERSE_ONLY])
    assert UNIVERSE_ONLY in out, out
    # POSITIVE CONTROL — the mapping half did not stop working in the process.
    for full in set(FAKE_UNIVERSE.values()):
        assert full in out, (full, out)


def test_the_union_is_STRICTLY_WIDENING_neither_source_can_remove_a_row():
    """Ordering and precedence are not concepts here: this is a list of things
    to offer, not a mapping from a name to one answer. Pinned in BOTH directions
    because a `dict.update`-shaped implementation would let the second source
    silently drop rows the first contributed and still pass a one-way check."""
    mapped = MO.repo_universe(dict(FAKE_UNIVERSE), [])
    filed = MO.repo_universe({}, [UNIVERSE_ONLY])
    both = MO.repo_universe(dict(FAKE_UNIVERSE), [UNIVERSE_ONLY])
    assert set(both) == set(mapped) | set(filed), (both, mapped, filed)
    assert set(both) > set(mapped), "the file must ADD to the mapping"
    assert set(both) > set(filed), "the mapping must ADD to the file"


def test_the_universe_dedupes_case_insensitively_and_keeps_ONE_row():
    """`acme/Widget` and `acme/widget` are ONE repository on GitHub — GitHub repo
    names are case-insensitive, which is why the mapping writes two spellings of
    every key. Two identical-looking rows in the picker is a worse picker, and the
    operator cannot tell which one is 'right' because neither is."""
    out = MO.repo_universe({"w": "acme/Widget"}, ["acme/widget"])
    assert out == ["acme/Widget"], (
        "the on-disk spelling wins and the duplicate is dropped")


def test_the_universe_file_cannot_INVENT_a_row_that_is_not_owner_slash_repo():
    """Same rule as the mapping's `clean_repo_map`, same reason: a row that is
    not exactly `owner/repo` builds a URL that 404s while looking authoritative.
    A picker row is worse than a mapping row here — the operator SELECTED it, so
    a 404 reads as the handler being broken rather than the reference being bad."""
    junk = ["acme/widget/", "acme//widget", "noslash", "", "a/b\nc/d",
            "  acme/widget  ", 12, None, ["acme/widget"]]
    assert MO.repo_universe({}, junk) == []
    # POSITIVE CONTROL: the same call with a GOOD row is non-empty, so the empty
    # above is the filter working and not the function failing to read anything.
    assert MO.repo_universe({}, junk + ["acme/widget"]) == ["acme/widget"]


@pytest.mark.parametrize("body,why", [
    (None, "absent"),
    ("", "empty"),
    ("{not json", "malformed"),
    ('{"loamfield": "gardenersguild/trowelcast"}', "a DICT — the mapping's shape"),
    ('"gardenersguild/trowelcast"', "a bare string"),
    ("42", "a number"),
])
def test_load_known_universe_answers_EVERY_failure_with_an_empty_list(
        tmp_path, body, why):
    """🔴 IDENTICAL POSTURE TO `load_known_repos`, AND FOR THE IDENTICAL REASON:
    this runs on a detached click handler with nowhere to print a traceback.

    ⚠ THE DICT CASE IS NOT HYPOTHETICAL. `known_repos.json` sits in the same
    directory and IS a dict; a `--path`/`--universe-path` mix-up at generation
    time writes one where the other belongs. Without the `isinstance(raw, list)`
    check, iterating a dict yields its KEYS — so the picker would silently offer
    bare repo names that are not `owner/repo` and build 404 URLs from them."""
    p = tmp_path / "universe.json"
    if body is not None:
        p.write_text(body)
    assert MO.load_known_universe(p) == [], why


def test_load_known_universe_reads_a_GOOD_file(tmp_path):
    """The positive control for the parametrisation above. Six ways of returning
    `[]` prove nothing about the reader unless it can also return rows — a
    function hardcoded to `return []` passes every case above."""
    p = tmp_path / "universe.json"
    p.write_text(json.dumps(["acme/widget", "not a repo", "acme/gadget"]))
    assert MO.load_known_universe(p) == ["acme/widget", "acme/gadget"], (
        "good rows kept IN FILE ORDER, the malformed one dropped")


def test_the_universe_path_is_resolved_at_CALL_time_not_bound_as_a_default():
    """🔴 THE DEFECT THAT MADE `load_known_repos`' OWN OVERRIDE TEST INERT, and
    it is a CLASS, not one site: a `path: Path = KNOWN_UNIVERSE_PATH` default is
    evaluated at IMPORT, so every test that patches the module constant passes
    while observing nothing. Measured on the mapping half: deleting the entire
    `load_known_repos()` call from `discover_repos` left the suite green."""
    import inspect
    sig = inspect.signature(MO.load_known_universe)
    assert sig.parameters["path"].default is None, (
        "the default must be None and resolved inside the body")


def test_the_picker_actually_OFFERS_a_universe_only_repo_end_to_end(monkeypatch):
    """🔴 THE BEHAVIOURAL HALF. Every assertion above is about `repo_universe` in
    isolation; this drives `main()` and reads what reached `pick`, so a correct
    universe that `main()` never passes to the picker still fails.

    That seam is exactly where this feature could be inert: `main()` built the
    universe from `repo_universe(discovered)` with no second argument, and a
    version of this change that widened the function but not the call site would
    pass every unit test in this section."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: dict(FAKE_UNIVERSE))
    monkeypatch.setattr(MO, "load_known_universe", lambda *a, **k: [UNIVERSE_ONLY])
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "")
    seen = {}
    monkeypatch.setattr(MO, "pick",
                        lambda c, mesg="": seen.update(rows=list(c), mesg=mesg) or "")
    assert MO.main(["nosuchrepo#77"]) == 0
    urls = [c["url"] for c in seen["rows"]]
    assert any(UNIVERSE_ONLY in u for u in urls), (
        f"the universe-only repo never reached the picker: {urls}")
    assert all(u.endswith("/77") for u in urls), urls


def test_the_universe_file_is_NOT_read_on_a_click_that_resolves(monkeypatch):
    """🔴 A LATENCY GUARD, AND THE REASON THE CALL IS INSIDE A CONDITIONAL
    EXPRESSION. The whole subsystem exists because a click took 4.3s; the fix
    was deleting a lookup, and re-adding an unconditional `stat` + `read` +
    `json.loads` on the fast path would be the same mistake in miniature.

    `owner/repo#N` is answered by the text alone and must never touch the file."""
    reads = []
    monkeypatch.setattr(MO, "load_known_universe",
                        lambda *a, **k: reads.append(1) or [])
    monkeypatch.setattr(MO, "open_url", lambda url: reads.append(url) or 0)
    assert MO.main(["--no-discovery", "civitai/talos-infra#1065"]) == 0
    assert reads == ["https://github.com/civitai/talos-infra/pull/1065"], (
        f"expected exactly one open and no universe read, got {reads}")


def test_the_staleness_note_names_the_UNIT_not_only_the_generator(tmp_path):
    """🔴 THE TEXT IS A CLAIM AND THE CLAIM CHANGED. It used to say "nothing
    regenerates it", which was true and is now false: a daily user unit does.
    Left alone, it would send the operator to re-run a generator by hand while
    the actual fault — a failing unit, an expired token, a host that was off —
    stayed invisible. A stale mapping past seven days now means SEVERAL runs did
    not land, which is a different diagnosis and needs a different sentence."""
    p = tmp_path / "known_repos.json"
    p.write_text(json.dumps(FAKE_UNIVERSE))
    old = time.time() - (MO.STALE_MAPPING_DAYS + 3) * 86400
    os.utime(p, (old, old))
    note = MO.staleness_note(p)
    assert note, "positive control: a file this old MUST produce a note"
    assert "mention-known-repos-refresh" in note, note
    assert "nothing regenerates it" not in note, note
    # And it still says nothing about a ROW — the disclosure rule is unchanged.
    _no_universe_token_anywhere(note, "STALENESS NOTE DISCLOSURE")


def test_the_universe_reaches_NO_sink_but_the_PICKER(monkeypatch, capsys):
    """🔴 THE DISCLOSURE GUARD FOR THE NEW CORPUS. `known_universe.json` names
    private repositories — MORE of them than the mapping does, because it is
    deliberately unfiltered — and it is read on the picker path. The existing
    guards assert on `FAKE_UNIVERSE`'s tokens; this one adds the universe-only
    row, which no mapping-derived guard can see."""
    notify_argv = []
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: dict(FAKE_UNIVERSE))
    monkeypatch.setattr(MO, "load_known_universe", lambda *a, **k: [UNIVERSE_ONLY])
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "")
    monkeypatch.setattr(MO, "notify",
                        lambda *a: notify_argv.append(["notify-send", *a]))
    monkeypatch.setattr(MO, "pick", lambda c, mesg="": "")   # dismissed
    MO.main(["nosuchrepo#77"])
    blob = _every_sink(capsys, notify_argv)
    for token in (UNIVERSE_ONLY, UNIVERSE_ONLY.split("/")[0],
                  UNIVERSE_ONLY.split("/")[1]):
        assert token not in blob, (
            f"a universe row reached a sink that is not the picker: {token!r}")


# --------------------------------------------------------------------------- #
# 🔴 THE FILE-WIDE SPAWN PIN — required by this handler's entry in
# `test_no_real_launchers.py::ACKNOWLEDGED_UNSTUBBED`.
#
# `mention-open.py` names `systemctl` in ONE operator-facing string (the
# staleness note tells you which unit to check). `launcher_scan.hazard_hits` is
# a TEXTUAL scan — deliberately, since erring toward reporting is right for a
# scan whose failure mode is a missed launch — so that mention registers this
# file as "reaching systemctl" and it had to be acknowledged.
#
# 🔴 AN ACKNOWLEDGEMENT BLINDS THE GUARD IT IS FILED UNDER, AND THAT WAS
# MEASURED ON THIS EXACT TABLE: `tmux-reply-agent`'s entry claimed by grep that
# no call site existed, and injecting a real `subprocess.run(["systemctl", …])`
# into it left that whole suite green — the acknowledgement had absorbed the
# thing the guard exists to catch. Its remedy was an AST pin, and this is the
# same remedy for the same reason.
#
# ⚠ THE EXISTING LEDGER DOES NOT COVER THIS, WHICH IS WHY A SECOND TEST EXISTS.
# `test_the_resolution_path_spawns_ONLY_these_local_commands` records what the
# RESOLUTION PATH actually spawns at runtime — a stronger claim on the path it
# drives, and blind everywhere else: a `systemctl` call added to `notify()`, or
# to any branch that run does not reach, would not appear in its ledger. This
# one reads the whole FILE.
# --------------------------------------------------------------------------- #
_SPAWN_FUNCS = {"run", "Popen", "call", "check_output", "check_call", "system",
                "execv", "execvp", "execve", "spawnv", "spawnvp"}

# Every argv[0] this handler is allowed to spawn, and why each is here:
#   git         — reading a checkout's remote (discovery)
#   tmux        — asking the pane for its repo
#   notify-send — the refusal toast
#   xdg-open    — opening the chosen URL
#   alacritty   — the float terminal the fzf picker runs in (was `rofi` until
#                 2026-09-09; see `PICKER_SH` for the ranking measurement that
#                 forced the swap). ⚠ `fzf` itself is NOT here and must not be:
#                 this set is argv[0] literals, and fzf is one level down, in
#                 the `-c` script. `_shell_child_commands` is the reader that
#                 sees it, and `test_the_alacritty_wrapper_PATH_covers_every_
#                 executable_the_handler_spawns` is where it is pinned.
EXPECTED_ARGV0 = {"git", "tmux", "notify-send", "xdg-open", "alacritty"}


def _spawn_argv0_literals(path: Path) -> set[str]:
    """Every literal argv[0] in a spawn-shaped call, read from the SYNTAX TREE.

    A non-literal argv[0] is reported as `<computed>` rather than skipped: a
    spawn whose command comes from a variable is exactly how a ledger keyed on
    literals gets walked past, so it must fail this test loudly instead of
    vanishing from the set. `pick()` already carries a comment requiring its
    terminal argv to stay a list literal for this reason.
    """
    tree = ast.parse(path.read_text())
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if name not in _SPAWN_FUNCS:
            continue
        first = node.args[0]
        if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
            head = first.elts[0]
            found.add(head.value if isinstance(head, ast.Constant)
                      else "<computed>")
        else:
            found.add("<not-a-list>")
    return found


def test_mention_open_SPAWNS_these_argv0_AND_NOTHING_ELSE():
    """🔴 GROWS-OR-SHRINKS, both directions asserted.

    A NEW binary appearing here is the hazard the `ACKNOWLEDGED_UNSTUBBED`
    entry would otherwise hide. A binary DISAPPEARING matters too: it means a
    capability was removed and the acknowledgement's justification — which
    names this set — has silently stopped describing the file.
    """
    assert _spawn_argv0_literals(HANDLER) == EXPECTED_ARGV0


def test_systemctl_is_MENTIONED_but_never_SPAWNED():
    """🔴 THE CLAIM THE ACKNOWLEDGEMENT ACTUALLY MAKES, asserted directly rather
    than left to the reader of a prose justification.

    Both halves are pinned. The mention must EXIST — delete the staleness note's
    command and this test says so, because a justification describing a file
    that no longer mentions the name is a stale entry in that table. And it must
    remain a mention only."""
    text = HANDLER.read_text()
    assert re.search(r"(?<![\w-])systemctl(?![\w-])", text), (
        "the acknowledgement in test_no_real_launchers.py exists BECAUSE this "
        "file names systemctl; if that is gone, remove the acknowledgement too")
    assert "systemctl" not in _spawn_argv0_literals(HANDLER), (
        "mention-open.py now SPAWNS systemctl — the ACKNOWLEDGED_UNSTUBBED "
        "entry covering it is an unreachability claim and is now FALSE")


# --------------------------------------------------------------------------- #
# 🔴 A GUESSED REPO MUST BE OVERRIDABLE — REPORTED FROM THE REAL CLICK PATH
#
# 2026-09-07, operator, clicking `audit-pr 1291` in Alacritty: a ONE-ROW picker
# holding the tmux pane's repo, above the note "audit-pr 1291 names no
# repository". Their words: "in this case the guess is right, but in practice
# it's not."
#
# That is the whole defect. Suppressing the auto-open (#1336) was correct — a
# `default`-sourced repo is evidence about the WINDOW, not about the reference,
# so it must never open unconfirmed. But having declined to act on the guess,
# the handler offered nothing else: confirm the wrong repo, or dismiss and type
# the URL by hand. The universe was already built and already the answer
# everywhere else a repository cannot be named; this arm just never reached it.
# --------------------------------------------------------------------------- #
def _guessed_picker(monkeypatch, *, universe, pane="wrongorg/wrongrepo",
                    mapping=FAKE_UNIVERSE, text="audit-pr 1291"):
    """Drive `main()` down the guessed-repo path and return what `pick` saw.

    ⚠ `mapping` IS A PARAMETER BECAUSE `universe=[]` DOES NOT EMPTY THE UNIVERSE.
    `repo_universe()` unions the generated file with `discover_repos()`, so a
    caller that clears only the file still gets every mapped repo — my own
    empty-universe test asserted 1 row and got 4 for exactly that reason. The
    empty case needs BOTH sources cleared, and that is worth a parameter rather
    than a comment, because the trap is silent in the other direction too.

    ⚠ `text` IS A PARAMETER BECAUSE THE DEFECT HAS TWO SHAPES, and they differ
    in exactly the way that broke the note: `audit-pr N` offers the guess at row
    1, a bare `#N` offers the clawgate task first and the guess at row 2. A
    helper hard-wired to one of them is how the second went unfixed."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: dict(mapping))
    monkeypatch.setattr(MO, "load_known_universe", lambda *a, **k: list(universe))
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: pane)
    seen = {}
    monkeypatch.setattr(MO, "pick",
                        lambda c, mesg="": seen.update(rows=list(c), mesg=mesg) or "")
    assert MO.main([text]) == 0
    return seen


def test_a_GUESSED_repo_is_offered_WITH_the_whole_universe_beneath_it(monkeypatch):
    """🔴 THE REGRESSION FOR THE REPORTED SYMPTOM. Red before this change: the
    picker held exactly ONE row.

    The guess must still be FIRST — it is the most likely answer and stays one
    Enter away — and everything else must be reachable by typing."""
    seen = _guessed_picker(monkeypatch, universe=[UNIVERSE_ONLY, "acme/widget"])
    urls = [c["url"] for c in seen["rows"]]
    assert len(urls) > 1, f"a guess with no alternatives is a dead end: {urls}"
    assert "wrongorg/wrongrepo" in urls[0], (
        f"the guess must stay FIRST — one Enter for the common case: {urls[0]}")
    assert any(UNIVERSE_ONLY in u for u in urls), (
        f"the universe never reached the guessed picker: {urls}")
    assert all(u.endswith("/1291") for u in urls), urls


def test_the_guessed_row_is_NOT_offered_twice(monkeypatch):
    """The pane's repo is usually in the universe too. Two identical rows read
    as a rendering bug rather than as a recommendation, and the operator cannot
    tell which one is the 'real' one — because neither is."""
    seen = _guessed_picker(monkeypatch,
                           universe=["wrongorg/wrongrepo", "acme/widget"])
    urls = [c["url"] for c in seen["rows"]]
    assert len(urls) == len(set(urls)), f"duplicate rows: {urls}"
    assert sum("wrongorg/wrongrepo" in u for u in urls) == 1, urls


def test_a_guessed_picker_is_NOT_described_as_nothing_here_knows(monkeypatch):
    """🔴 THE ORDER GUARD, and it pins a defect no behavioural test can see.

    After the fix BOTH `guessed` and `offered_universe` are true on this path,
    so the note is chosen by the ORDER of two branches. `universe_note` opens
    "nothing here knows X" — which is false here: the first row is a
    recommendation the handler is explicitly asking about. Swap the branches and
    the picker still shows the right rows in the right order, so only the words
    are wrong, and only this test says so."""
    seen = _guessed_picker(monkeypatch, universe=[UNIVERSE_ONLY])
    assert "nothing here knows" not in seen["mesg"], seen["mesg"]
    assert "guess" in seen["mesg"].lower(), seen["mesg"]
    assert "tmux pane" in seen["mesg"], seen["mesg"]
    # …and it must say the rest are SEARCHABLE, because "confirm or dismiss" —
    # the old wording — is now false: neither is what the operator should do.
    assert "search" in seen["mesg"].lower(), seen["mesg"]
    _no_universe_token_anywhere(seen["mesg"], "GUESSED-NOTE DISCLOSURE")


def test_the_ONE_ROW_wording_survives_for_an_EMPTY_universe(monkeypatch):
    """⚠ The narrow wording is kept, not deleted: with no mapping file (or an
    unreadable one) the universe is empty and a lone guessed row is still
    reachable. "Confirm, or dismiss" is the correct instruction THERE, and
    telling the operator to "type to search" over one row would be nonsense."""
    seen = _guessed_picker(monkeypatch, universe=[], mapping={})
    assert len(seen["rows"]) == 1, seen["rows"]
    assert "Confirm, or dismiss" in seen["mesg"], seen["mesg"]
    assert "search" not in seen["mesg"].lower(), seen["mesg"]


def test_the_guessed_repo_is_still_NEVER_opened_unconfirmed(monkeypatch):
    """🔴 THE SAFETY PROPERTY #1336 ADDED, RE-ASSERTED HERE because this change
    touches the branch that enforces it. Widening the offer must not weaken the
    rule: a repo measured from the tmux pane is evidence about the WINDOW, and
    opening it unconfirmed is the confident-wrong-page failure the whole handler
    is anchored against. `open_url` must not be reached without a selection."""
    opened = []
    monkeypatch.setattr(MO, "open_url", lambda url: opened.append(url) or 0)
    _guessed_picker(monkeypatch, universe=[UNIVERSE_ONLY])
    assert opened == [], f"a guessed repo was opened without a selection: {opened}"


def test_an_EXPLICIT_owner_still_opens_directly_and_gets_NO_picker(monkeypatch):
    """🔴 THE NEGATIVE CONTROL FOR THE WHOLE CHANGE. A rule that appended the
    universe to every candidate would satisfy every assertion above while
    putting a 392-row picker in front of `owner/repo#N`, which carries its own
    evidence and must still open with zero keystrokes."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: dict(FAKE_UNIVERSE))
    monkeypatch.setattr(MO, "load_known_universe", lambda *a, **k: [UNIVERSE_ONLY])
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "wrongorg/wrongrepo")
    picked, opened = [], []
    monkeypatch.setattr(MO, "pick", lambda c, mesg="": picked.append(c) or "")
    monkeypatch.setattr(MO, "open_url", lambda url: opened.append(url) or 0)
    assert MO.main(["civitai/talos-infra#1065"]) == 0
    assert picked == [], "an explicit owner must not raise a picker"
    assert opened == ["https://github.com/civitai/talos-infra/pull/1065"], opened


# --------------------------------------------------------------------------- #
# 🔴 THE SAME DEFECT ONE RUNG ALONG — THE BARE `#N` THE PANE ATTRIBUTES
#
# 2026-09-08, operator: "fix the bare-#N and any other cases left unfixed".
#
# #1380 closed the shape where the guess was ALONE (`audit-pr N`) and wrote
# down, in the handler and in
# `test_a_bare_hash_N_that_the_PANE_already_attributes_does_NOT_get_the_universe`,
# that it was deliberately leaving the two-row shape alone: "burying two good
# rows under several hundred is a regression dressed as a feature", and the
# trade belonged to the operator. The operator has now made it. The rows that
# were MEASURED stay on top — that half of the old claim is still pinned, in the
# superseding test above and in the ordering test below — and the universe goes
# underneath.
#
# Everything here drives `main()` rather than a helper, because a correct helper
# `main()` never calls is the failure mode this file has already had.
# --------------------------------------------------------------------------- #
def test_a_bare_hash_N_offers_the_universe_UNDER_the_two_measured_rows(
        monkeypatch):
    """🔴 THE REGRESSION FOR THE 2026-09-08 REPORT. Red before this change: the
    picker held exactly TWO rows and the pane's repo was the only GitHub one, so
    a wrong pane guess left the right repository unreachable."""
    seen = _guessed_picker(monkeypatch, text="#1291",
                           universe=[UNIVERSE_ONLY, "acme/widget"])
    urls = [c["url"] for c in seen["rows"]]
    assert len(urls) > 2, f"the two-row picker is unchanged: {urls}"
    assert any(UNIVERSE_ONLY in u for u in urls), (
        f"the universe never reached the bare-#N picker: {urls}")
    assert any("acme/widget" in u for u in urls), urls
    assert all(u.endswith("/1291") for u in urls), urls


def test_the_bare_hash_N_ORDER_is_clawgate_then_the_guess_then_the_universe(
        monkeypatch):
    """🔴 THE HALF OF THE SUPERSEDED TEST'S CLAIM THAT SURVIVES, PINNED AS AN
    ORDERING RATHER THAN AS A ROW COUNT.

    The common case must stay one or two keystrokes: the picker opens on row 1, so the
    clawgate task is still one Enter and the pane's repo is one arrow key away.
    Every universe row must sit BELOW both.

    ⚠ THE ROW-COUNT FLOOR IS WHAT MAKES THIS A REGRESSION TEST RATHER THAN AN
    INVARIANT GUARD. Without it the two positional assertions are satisfied by
    the PRE-CHANGE two-row picker — there is simply nothing at index 2 to be
    out of order — so it would have been green at base while claiming to pin the
    ordering the change introduces."""
    seen = _guessed_picker(monkeypatch, text="#1291",
                           universe=[UNIVERSE_ONLY, "acme/widget"])
    urls = [c["url"] for c in seen["rows"]]
    assert len(urls) == 7, urls
    assert urls[0] == "https://clawgate.zacx.dev/tasks/1291", (
        f"the MEASURED rows must stay on top: {urls}")
    assert urls[1] == "https://github.com/wrongorg/wrongrepo/pull/1291", (
        f"the MEASURED rows must stay on top: {urls}")
    assert not any(UNIVERSE_ONLY in u or "acme/widget" in u for u in urls[:2]), urls
    assert UNIVERSE_ONLY in "\n".join(urls[2:]), urls


def test_the_bare_hash_N_note_names_the_GUESSED_ROWS_POSITION(monkeypatch):
    """🔴 THE WORDING DEFECT WIDENING THE ARM CREATES, AND THE REASON
    `guessed_note` GREW A `rank`. #1380's multi-row wording opened "The FIRST
    row is a guess from the tmux pane" — true of `audit-pr N`, FALSE here, where
    row 1 is the clawgate task and the guess is row 2. Pointing the operator at
    the wrong row is worse than saying nothing: they would distrust the clawgate
    task and trust the guess.

    Pinned as the WHOLE normalised string, because a guard on the words `guess`
    and `tmux pane` is walkable by a reword that still names the wrong row."""
    seen = _guessed_picker(monkeypatch, text="#1291",
                           universe=[UNIVERSE_ONLY, "acme/widget"])
    assert seen["mesg"] == (
        "#1291 names no repository. Row 2 is a guess from the tmux pane, which "
        "may not be the pane you clicked in — the 5 rows below it are every "
        "repository this host knows. Type to search, or dismiss."), seen["mesg"]
    _no_universe_token_anywhere(seen["mesg"], "BARE-#N GUESS-NOTE DISCLOSURE")


def test_the_audit_pr_note_still_names_ROW_1(monkeypatch):
    """The other half of the same pin — the shape #1380 fixed must not have its
    row number silently shifted by the `rank` parameter. Its guess is row 1."""
    seen = _guessed_picker(monkeypatch, universe=[UNIVERSE_ONLY, "acme/widget"])
    assert seen["mesg"] == (
        "audit-pr 1291 names no repository. Row 1 is a guess from the tmux "
        "pane, which may not be the pane you clicked in — the 5 rows below it "
        "are every repository this host knows. Type to search, or dismiss."), (
            seen["mesg"])


def test_the_bare_hash_N_guess_is_NEVER_opened_unconfirmed(monkeypatch):
    """🔴 THE SAFETY PROPERTY, RE-ASSERTED FOR THIS SHAPE. Widening the offer
    must not weaken the rule. `open_url` is reachable here only through a
    selection, and there is none.

    ⚠ INVARIANT GUARD, NOT A REGRESSION TEST — green at base 18bc1500 too,
    because #1336 already suppressed the auto-open for this rung. It is here to
    catch the widening WEAKENING it, which is a different failure from the one
    being fixed."""
    opened = []
    monkeypatch.setattr(MO, "open_url", lambda url: opened.append(url) or 0)
    _guessed_picker(monkeypatch, text="#1291", universe=[UNIVERSE_ONLY])
    assert opened == [], f"a guessed repo was opened without a selection: {opened}"


def test_a_bare_hash_N_whose_universe_ADDS_NOTHING_keeps_the_two_row_picker(
        monkeypatch):
    """⚠ THE DEGENERATE HOST, AND THE REASON THE APPEND IS GUARDED ON `extra`
    RATHER THAN ON `universe`. When the only repository this host knows IS the
    pane's, the dedupe leaves nothing to append — so there is nothing to
    explain, and a note claiming "the N rows below it" would name rows that are
    not in the list. The picker stays two rows and stays silent.

    ⚠ INVARIANT GUARD — green at base 18bc1500 too. It pins the boundary the
    widening must NOT cross, not behaviour the widening created."""
    seen = _guessed_picker(monkeypatch, text="#1291",
                           universe=["wrongorg/wrongrepo"],
                           mapping={"wr": "wrongorg/wrongrepo"})
    urls = [c["url"] for c in seen["rows"]]
    assert len(urls) == 2, urls
    assert seen["mesg"] == "", seen["mesg"]


def test_the_guessed_row_is_not_offered_twice_on_the_bare_hash_N_path_either(
        monkeypatch):
    """The dedupe is at the `main()` level rather than inside the `audit-pr`
    arm, so it covers this shape too. A pane repo that is ALSO in the universe
    appears exactly once.

    ⚠ THE COUNT IS ASSERTED FIRST FOR THE SAME REASON AS THE ORDERING TEST: a
    two-row picker has no duplicates either, so without it this would be green
    at base. Three mapped repos + two universe rows = five distinct
    repositories, one of which IS the pane's, so the append contributes four
    beneath the two measured rows."""
    seen = _guessed_picker(monkeypatch, text="#1291",
                           universe=["wrongorg/wrongrepo", "acme/widget"])
    urls = [c["url"] for c in seen["rows"]]
    assert len(urls) == 6, urls
    assert len(urls) == len(set(urls)), f"duplicate rows: {urls}"
    assert sum("wrongorg/wrongrepo" in u for u in urls) == 1, urls


def test_the_DEFAULT_REPO_FLAG_rides_the_same_rung_as_the_pane(monkeypatch):
    """🔴 `--default-repo` IS THE SAME LADDER RUNG AS `tmux_pane_repo()`, and the
    predicate is `repo_source == default` rather than "the pane answered". With
    the pane silent, the flag alone must still produce an overridable offer —
    otherwise a caller could re-open the exact hole this change closes by
    handing the guess in from outside."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: dict(FAKE_UNIVERSE))
    monkeypatch.setattr(MO, "load_known_universe", lambda *a, **k: [UNIVERSE_ONLY])
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "")
    seen = {}
    monkeypatch.setattr(
        MO, "pick",
        lambda c, mesg="": seen.update(rows=list(c), mesg=mesg) or "")
    assert MO.main(["--default-repo", "wrongorg/wrongrepo", "#1291"]) == 0
    urls = [c["url"] for c in seen["rows"]]
    assert urls[0] == "https://clawgate.zacx.dev/tasks/1291", urls
    assert urls[1] == "https://github.com/wrongorg/wrongrepo/pull/1291", urls
    assert any(UNIVERSE_ONLY in u for u in urls), urls
    assert "Row 2" in seen["mesg"], seen["mesg"]


def test_a_bare_hash_N_with_NO_pane_repo_is_UNCHANGED_by_this_widening(
        monkeypatch):
    """🔴 THE UNTOUCHED NEIGHBOUR. Nothing attributed this `#N`, so there is no
    guess to override and PASS 3's own arm — not the guessed one — appends the
    universe. It must still do so, and it must still carry NO note: the rows are
    a real clawgate task plus a list, with nothing being recommended.

    ⚠ INVARIANT GUARD — green at base 18bc1500 too, by design: "unchanged" is
    the whole claim."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: dict(FAKE_UNIVERSE))
    monkeypatch.setattr(MO, "load_known_universe", lambda *a, **k: [])
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "")
    seen = {}
    monkeypatch.setattr(
        MO, "pick",
        lambda c, mesg="": seen.update(rows=list(c), mesg=mesg) or "")
    assert MO.main(["#1291"]) == 0
    urls = [c["url"] for c in seen["rows"]]
    assert urls[0] == "https://clawgate.zacx.dev/tasks/1291", urls
    assert len(urls) == 4, urls
    assert seen["mesg"] == "", seen["mesg"]


@pytest.mark.parametrize("text,expected", [
    # `mapped` — the operator named the repo, the mapping supplied the owner.
    # DELIBERATELY NOT WIDENED, and the reasoning is in the PR body: the TEXT is
    # evidence about the REFERENCE, `regen-known-repos.py` drops a bare name two
    # owners share rather than picking one (so a `mapped` hit is unique on this
    # host by construction), and the mapping's AGE already reaches the operator
    # through `staleness_note`. Putting a several-hundred-row picker in front of
    # every `repo#N` would tax the shape that carries its own evidence.
    ("loamfield#12", "https://github.com/gardenersguild/trowelcast/pull/12"),
    # `explicit` — the strongest rung there is.
    ("civitai/talos-infra#1065",
     "https://github.com/civitai/talos-infra/pull/1065"),
])
def test_the_TEXTS_OWN_evidence_still_opens_with_ZERO_keystrokes(monkeypatch,
                                                                text, expected):
    """🔴 THE NEGATIVE CONTROL FOR THE WIDENING. A rule that appended the
    universe to every candidate — or that read `guessed` as "the text named no
    OWNER" rather than "the ladder answered from `default`" — would satisfy
    every assertion above while putting a picker in front of the two shapes that
    already know the answer. Both must still open directly, and a WRONG pane
    repo is loaded so a guess leaking into these paths is visible.

    ⚠ INVARIANT GUARD / NEGATIVE CONTROL — green at base 18bc1500 too."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: dict(FAKE_UNIVERSE))
    monkeypatch.setattr(MO, "load_known_universe", lambda *a, **k: [UNIVERSE_ONLY])
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "wrongorg/wrongrepo")
    monkeypatch.setattr(MO, "pick",
                        lambda c, mesg="": pytest.fail(f"asked about {text}"))
    opened = []
    monkeypatch.setattr(MO, "open_url", lambda url: opened.append(url) or 0)
    assert MO.main([text]) == 0
    assert opened == [expected], opened


def test_a_SIX_DIGIT_click_is_STILL_a_toast_with_a_pane_repo_loaded(monkeypatch):
    """🔴 THE OTHER NEGATIVE CONTROL. `#282828` is a colour literal; every row a
    picker could offer names an issue no repository has. `colour` bars the
    universe before any of this, and a pane repo must not sneak it back in — the
    scanner refuses the text, so `span is None`, so `guessed` is False.

    ⚠ INVARIANT GUARD / NEGATIVE CONTROL — green at base 18bc1500 too."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: dict(FAKE_UNIVERSE))
    monkeypatch.setattr(MO, "load_known_universe", lambda *a, **k: [UNIVERSE_ONLY])
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "wrongorg/wrongrepo")
    monkeypatch.setattr(MO, "pick",
                        lambda c, mesg="": pytest.fail("raised a picker"))
    notices = []
    monkeypatch.setattr(MO, "notify", lambda *a, **k: notices.append(tuple(a)))
    assert MO.main(["#282828"]) == 1
    assert notices, "no toast at all"
    body = " ".join(notices[-1])
    assert "colour literal" in body, body
    _no_universe_token_anywhere(body, "COLOUR-TOAST DISCLOSURE")


def test_PRINT_mode_on_a_bare_hash_N_lists_the_CANDIDATES_not_the_universe(
        monkeypatch, capsys):
    """🔴 THE DISCLOSURE CONTROL FOR THE WIDENED ARM. `--print` returns before
    the guessed branch AND `may_offer_universe` is false under it, so the
    universe is doubly barred — but widening an arm is exactly the kind of
    change that reaches a path by accident, and the leak here would be private
    repository names on stdout.

    ⚠ INVARIANT GUARD / NEGATIVE CONTROL — green at base 18bc1500 too."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: dict(FAKE_UNIVERSE))
    monkeypatch.setattr(MO, "load_known_universe", lambda *a, **k: [UNIVERSE_ONLY])
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "wrongorg/wrongrepo")
    monkeypatch.setattr(MO, "pick",
                        lambda c, mesg="": pytest.fail("--print raised a picker"))
    assert MO.main(["--print", "#1291"]) == 0
    out = capsys.readouterr().out
    # POSITIVE CONTROL: it really did resolve and print something.
    assert "https://clawgate.zacx.dev/tasks/1291" in out, out
    assert len(out.strip().splitlines()) == 2, out
    assert UNIVERSE_ONLY not in out, out
    _no_universe_token_anywhere(out, "PRINT-MODE DISCLOSURE")


def test_the_universe_reaches_THE_PICKER_AND_NO_OTHER_SINK_on_the_bare_hash_N_path(
        monkeypatch, capsys, real_notify):
    """🔴 THE WIDENED ARM'S DISCLOSURE GUARD, through the REAL `notify()`.
    `_every_sink` folds stdout, stderr and every `notify-send` argv into one
    string; a universe row may appear in the picker rows and NOWHERE in it.

    POSITIVE CONTROL: the same run's picker rows are asserted to CONTAIN a
    universe token, so a version of this test that scanned an empty universe
    could not pass by finding nothing."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: dict(FAKE_UNIVERSE))
    monkeypatch.setattr(MO, "load_known_universe", lambda *a, **k: [UNIVERSE_ONLY])
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "wrongorg/wrongrepo")
    seen = {}
    monkeypatch.setattr(
        MO, "pick",
        lambda c, mesg="": seen.update(rows=MO.picker_rows(c), mesg=mesg) or "")
    assert MO.main(["#1291"]) == 0
    rows = "\n".join(seen["rows"])
    assert UNIVERSE_ONLY in rows, (
        f"POSITIVE CONTROL FAILED — no universe row was offered at all: {rows}")
    assert any(t in rows for t in FAKE_UNIVERSE_TOKENS), rows
    blob = _every_sink(capsys, real_notify)
    _no_universe_token_anywhere(blob, "BARE-#N EVERY SINK")
    for token in (UNIVERSE_ONLY, UNIVERSE_ONLY.split("/")[0],
                  UNIVERSE_ONLY.split("/")[1]):
        assert token not in blob, (
            f"a universe row reached a sink that is not the picker: {token!r}")
