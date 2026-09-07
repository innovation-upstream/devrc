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

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
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
    assert r.stdout.strip() == "https://github.com/gardenersguild/trowelcast/issues/12", (
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
    assert spy == [("open", "https://github.com/civitai/talos-infra/issues/1065")]


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
    assert spy[-1] == ("open", "https://github.com/civitai/talos-infra/issues/1065")


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

    `rofi` is the one deliberate omission and it is enumerated, not inferred:
    it is a SYSTEM package here (`nix/i3/config.nix` invokes it bare), and
    pulling `pkgs.rofi` in would install a second copy whose theme drifts from
    the launcher's.
    """
    # executable -> the nix attribute that must provide it.
    PROVIDER = {"git": "pkgs.git", "tmux": "pkgs.tmux",
                "xdg-open": "pkgs.xdg-utils", "notify-send": "pkgs.libnotify"}
    # Not spawned by name from PATH: rofi is the system copy on purpose, and
    # python312 is the interpreter the wrapper `exec`s by store path.
    NOT_FROM_THE_WRAPPER_PATH = {"rofi"}
    INTERPRETER = {"pkgs.python312"}

    nix_src = (ROOT / "nix" / "programs" / "alacritty" / "default.nix").read_text()
    m = re.search(r"makeBinPath\s*\[(.*?)\]", nix_src, re.S)
    assert m, "the mentionOpen wrapper no longer calls lib.makeBinPath"
    # Comments in that block are PROSE ABOUT the packages — `pkgs.gh` was named
    # in one for months. Reading them as config is the mistake this strips.
    body = re.sub(r"#[^\n]*", "", m.group(1))
    listed = set(re.findall(r"pkgs\.[A-Za-z0-9_-]+", body))
    assert listed, "positive control: the wrapper DOES pin a PATH"

    spawned = _spawned_executables(HANDLER.read_text())
    assert spawned, "positive control: the module DOES spawn things"
    needed = {PROVIDER[e] for e in spawned - NOT_FROM_THE_WRAPPER_PATH
              if e in PROVIDER}
    unknown = spawned - NOT_FROM_THE_WRAPPER_PATH - set(PROVIDER)
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
    with an empty value (an empty value builds `https://github.com//issues/12`).
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
# 🔴 NOTHING HERE LAUNCHES ROFI. `pick` is stubbed by the `spy` fixture, or
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
                   ("open", "https://github.com/civitai/talos-infra/issues/1291")], spy


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
    assert "https://github.com/wrongorg/wrongrepo/issues/1291" in seen["urls"], seen


def test_the_one_row_picker_for_a_guessed_repo_SAYS_WHY(monkeypatch):
    """A single-row picker with no explanation reads as a broken handler asking
    the operator to confirm the obvious. rofi cannot report a reason after a
    dismissal (see `pick`), so the reason goes above the choice.

    🔴 AND THE NOTE NAMES THE CLICKED TEXT, NEVER A REPOSITORY OR THE MAPPING —
    the ROW already carries the repo, and `_every_sink`'s disclosure guards
    cannot see a string that goes to rofi."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: dict(FAKE_UNIVERSE))
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "wrongorg/wrongrepo")
    seen = {}
    monkeypatch.setattr(MO, "pick",
                        lambda c, mesg="": seen.update(n=len(c), mesg=mesg) or "")
    assert MO.main(["audit-pr 1291"]) == 0
    assert seen["n"] == 1, seen
    assert "audit-pr 1291" in seen["mesg"], seen
    assert "tmux pane" in seen["mesg"], seen
    _no_universe_token_anywhere(seen["mesg"], "GUESSED-NOTE DISCLOSURE")


def test_the_note_is_NOT_attached_to_the_ordinary_bare_hash_N_picker(monkeypatch):
    """🔴 THE NEGATIVE CONTROL FOR THE NOTE, and it guards the most common
    interaction in this handler. A bare `#N` with a pane repo has been a
    two-row picker since before any of this; putting a line of apology above it
    would tax every single click. The note rides ONLY on the one-row picker the
    suppression created."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: dict(FAKE_UNIVERSE))
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "wrongorg/wrongrepo")
    seen = {}
    monkeypatch.setattr(MO, "pick",
                        lambda c, mesg="": seen.update(n=len(c), mesg=mesg) or "")
    assert MO.main(["#1291"]) == 0
    assert seen["n"] == 2, seen
    assert seen["mesg"] == "", seen


@pytest.mark.parametrize("text,expected", [
    # `mapped` — the mapping resolved the owner for a repo the TEXT named.
    ("loamfield#12", "https://github.com/gardenersguild/trowelcast/issues/12"),
    # `explicit` — the operator wrote the owner out.
    ("civitai/talos-infra#1065",
     "https://github.com/civitai/talos-infra/issues/1065"),
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
    assert spy[-1] == ("open", "https://github.com/civitai/talos-infra/issues/1065"), (
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
# Interactively the operator gets the fuzzy picker, types the name, rofi
# (`-no-custom`) matches nothing, presses Escape — and `pick()` returns "" for
# BOTH that and a genuine "I changed my mind". rofi reports them identically;
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
    next. The rows themselves may go to rofi; the note may say only what the
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
    """The bare `#N` picker — clawgate plus a pane-attributed GitHub row — is
    not a dead end. Putting a line of apology above the single most common
    interaction in this handler would be a regression dressed as a diagnosis."""
    seen = {}
    monkeypatch.setattr(
        MO, "pick",
        lambda c, mesg="": seen.update(mesg=mesg) or c[0]["url"])
    assert MO.main(["#370"]) == 0
    assert seen["mesg"] == "", seen


def test_the_picker_passes_its_NOTE_to_rofi_as_mesg(monkeypatch):
    """🔴 THE SEAM BETWEEN THE DECISION AND THE TOOL, again. Every test above
    stubs `pick`, so none of them notices if the note is computed and then
    dropped on the floor. This is the only one that reads the argv rofi is
    actually handed.

    NOTHING IS LAUNCHED: `subprocess.run` is replaced."""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = list(cmd)
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(MO.subprocess, "run", fake_run)
    cands = [{"platform": "github", "id": "7",
              "url": "https://github.com/gardenersguild/trowelcast/issues/7"}]
    MO.pick(cands, mesg="nothing here knows widget<1> & co")
    assert "-mesg" in seen["cmd"], seen["cmd"]
    body = seen["cmd"][seen["cmd"].index("-mesg") + 1]
    # `-mesg` is rendered as PANGO MARKUP, so the three significant characters
    # must arrive escaped or a stray `<` swallows the rest of the line.
    assert body == "nothing here knows widget&lt;1&gt; &amp; co", body

    # NEGATIVE CONTROL: no note, no flag. A `-mesg` with an empty argument
    # renders an empty band above the list on every ordinary picker.
    MO.pick(cands)
    assert "-mesg" not in seen["cmd"], seen["cmd"]


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


# The universe rows may reach the operator's rofi window and NOWHERE else, so
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
    repositories; the ONLY place it may go is the operator's rofi window. This
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
    every key. Two identical-looking rows in rofi is a worse picker, and the
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
    assert reads == ["https://github.com/civitai/talos-infra/issues/1065"], (
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


def test_the_universe_reaches_NO_sink_but_rofi(monkeypatch, capsys):
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
            f"a universe row reached a sink that is not rofi: {token!r}")


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
#   rofi        — the picker
EXPECTED_ARGV0 = {"git", "tmux", "notify-send", "xdg-open", "rofi"}


def _spawn_argv0_literals(path: Path) -> set[str]:
    """Every literal argv[0] in a spawn-shaped call, read from the SYNTAX TREE.

    A non-literal argv[0] is reported as `<computed>` rather than skipped: a
    spawn whose command comes from a variable is exactly how a ledger keyed on
    literals gets walked past, so it must fail this test loudly instead of
    vanishing from the set. `pick()` already carries a comment requiring its
    rofi argv to stay a list literal for this reason.
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
