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
import errno
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

    # 🔴 THE THIRD AND FOURTH HOST-STATE FILES, AND THE FOURTH IS A *WRITE*.
    # `known_ranges.json` is read exactly as the two above are, so it gets the
    # same pair of redirects for the same measured reason. `picks.jsonl` is
    # different in kind and strictly sharper: `record_pick` CREATES it, so an
    # unredirected test running `main()` to a selection would APPEND the
    # operator's real `~/.config/mention-open/picks.jsonl` — the first time any
    # test in this file could modify host state outside `tmp_path`. Every
    # previous hole in this fixture was a test READING the operator's data; this
    # one would write to it.
    #
    # ⚠ NEITHER IS WRITTEN, for the reason the universe redirect states at
    # length: a redirect relocates a read, it must not invent content the test
    # did not ask for. An absent ranges file means "every repo is UNKNOWN",
    # which is the cold-start state and the correct default for a test that says
    # nothing about ordering; an absent pick log means no learned preference.
    # Tests that want either write one.
    ranges = tmp_path / "autouse-known-ranges.json"
    monkeypatch.setattr(MO, "KNOWN_RANGES_PATH", ranges)
    monkeypatch.setenv("MENTION_OPEN_KNOWN_RANGES", str(ranges))
    picks = tmp_path / "autouse-picks.jsonl"
    monkeypatch.setattr(MO, "PICKS_PATH", picks)
    monkeypatch.setenv("MENTION_OPEN_PICKS", str(picks))
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
    "print(m.KNOWN_REPOS_PATH);print(sorted(m.load_known_repos()));"
    "print(m.KNOWN_RANGES_PATH);print(m.PICKS_PATH)"
)


# 🔴 EVERY HOST-STATE PATH THIS HANDLER TOUCHES, PINNED TWO-WAY BY
# `test_the_HOST_STATE_path_ledger_is_pinned_two_way`. The autouse fixture above
# has now been extended THREE times — once per new file — and each time the hole
# existed silently until somebody happened to look. A ledger makes the next
# addition fail the suite instead: a constant added without a redirect, or a
# redirect left behind after a constant is deleted, both go red.
HOST_STATE_CONSTANTS = {
    "KNOWN_REPOS_PATH": "MENTION_OPEN_KNOWN_REPOS",
    "KNOWN_UNIVERSE_PATH": "MENTION_OPEN_KNOWN_UNIVERSE",
    "KNOWN_RANGES_PATH": "MENTION_OPEN_KNOWN_RANGES",
    "PICKS_PATH": "MENTION_OPEN_PICKS",
}


def _host_state_constants(source: str) -> set[str]:
    """Every module-level constant in `source` that points at a file under the
    mention-open config directory.

    🔴 SPELLINGS THIS HAS MISSED SILENTLY, each measured against a constant
    planted in the real handler source:

      * `X: Path = …`   — `ast.AnnAssign`, not `ast.Assign`;
      * `X, Y = …, …`   — a tuple target, not an `ast.Name`;
      * an assignment inside a module-level `if`/`try`/`with`/`for`/`while` —
        `tree.body` only sees the top level;
      * a lowercase name — `isupper()` is itself a spelling pin, which is the
        very thing this function was rewritten to stop relying on;
      * 🔴 `SPOOL_PATH = PICKS_PATH.parent / "spool.jsonl"` — a constant DERIVED
        from an already-ledgered one, whose own source segment never says
        `mention-open`. A round-3 audit called this the most LIKELY spelling of
        the lot, and it is the worst: because `HOST_STATE_CONSTANTS` is
        hand-maintained, both sides of the two-way pin lose the entry together
        and the ledger test stays GREEN.

    A missed spelling means an unredirected host-state file: a test reads — or,
    since `record_pick`, WRITES — the operator's own 0600 data. So the walk is
    over `Assign` AND `AnnAssign`, recursively through every module-level block
    (never into a function or class, where a local of the same name is not a
    module constant), over flattened tuple/list targets, with no constraint on
    the name's case — and it is resolved to a FIXPOINT, so a constant built from
    a known one is found however many hops away it is.

    ⚠ THE TEST FOR *THIS* FUNCTION IS `test_the_host_state_DISCOVERY_sees_every_
    spelling`, which plants each in turn. A discovery nobody has watched find
    something is not a discovery.
    """
    tree = ast.parse(source)

    def statements(body):
        for node in body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                yield node
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For,
                                   ast.While)):
                yield from statements(node.body)
                yield from statements(getattr(node, "orelse", []))
                yield from statements(getattr(node, "finalbody", []))
                for handler in getattr(node, "handlers", []):
                    yield from statements(handler.body)

    def targets(node):
        raw = ([node.target] if isinstance(node, ast.AnnAssign)
               else list(node.targets))
        while raw:
            tgt = raw.pop()
            if isinstance(tgt, (ast.Tuple, ast.List)):
                raw.extend(tgt.elts)
            elif isinstance(tgt, ast.Name):
                yield tgt.id

    def builds_a_path(node) -> bool:
        # `PICKER_CLASS = "float,mention-open"` is the i3 window class and names
        # the directory only by coincidence of spelling — a bare string constant
        # is not a file to redirect. The two shapes that ARE are a `Path(...)`
        # call and a `/` join.
        return any(
            (isinstance(n, ast.Call)
             and (getattr(n.func, "id", None) or getattr(n.func, "attr", None))
             == "Path")
            or (isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div))
            for n in ast.walk(node.value))

    assignments = [n for n in statements(tree.body) if n.value is not None]
    found: set[str] = set()
    # Seed: the assignments that NAME the directory themselves.
    for node in assignments:
        segment = ast.get_source_segment(source, node) or ""
        if "mention-open" in segment and builds_a_path(node):
            found.update(targets(node))
    # 🔴 THEN TO A FIXPOINT — anything built from something already found is a
    # host-state path too, however many hops away. Without this,
    # `SPOOL_PATH = PICKS_PATH.parent / "spool.jsonl"` is invisible, and it is
    # the most natural way to add the next one.
    #
    # 🔴 AND THE FIXPOINT PASS DOES *NOT* REQUIRE `builds_a_path`, which the
    # first version did. A round-4 audit measured the cost: `SPOOL_PATH =
    # PICKS_PATH.with_name("spool.jsonl")` was MISSED, because `.with_name` is
    # neither a `Path(` call nor a `/` — and it is exactly as idiomatic as
    # `.parent /`. Referencing a known host-state path IS the signal here; the
    # path-shape test is only needed for the SEED, where the evidence is a
    # coincidence of spelling (`PICKER_CLASS = "float,mention-open"`).
    #
    # ⚠ IT OVER-MATCHES IN THE SAFE DIRECTION. `RATIO = len(str(PICKS_PATH)) / 2`
    # would be pulled in — and that fails LOUD, as a ledger mismatch somebody
    # reads, rather than silently dropping a file nobody redirects.
    while True:
        grew = False
        for node in assignments:
            names = set(targets(node))
            if names <= found:
                continue
            refs = {n.id for n in ast.walk(node.value)
                    if isinstance(n, ast.Name)}
            if refs & found:
                found |= names
                grew = True
        if not grew:
            return found


@pytest.mark.parametrize("planted,name", [
    ('SPOOL_PATH = Path.home() / ".config" / "mention-open" / "spool.jsonl"',
     "SPOOL_PATH"),
    ('SPOOL_PATH: Path = Path.home() / ".config" / "mention-open" / "s.jsonl"',
     "SPOOL_PATH"),
    ('SPOOL_PATH, _OTHER = Path("/x/mention-open/s.jsonl"), 1', "SPOOL_PATH"),
    ('if True:\n    SPOOL_PATH = Path("/x/mention-open/s.jsonl")', "SPOOL_PATH"),
    ('try:\n    SPOOL_PATH = Path("/x/mention-open/s.jsonl")\nexcept OSError:\n'
     '    SPOOL_PATH = Path("/y/mention-open/s.jsonl")', "SPOOL_PATH"),
    ('try:\n    _X = 1\nexcept OSError:\n'
     '    SPOOL_PATH = Path("/y/mention-open/s.jsonl")', "SPOOL_PATH"),
    ('with open("/dev/null") as _f:\n'
     '    SPOOL_PATH = Path("/x/mention-open/s.jsonl")', "SPOOL_PATH"),
    ('for _i in (1,):\n    SPOOL_PATH = Path("/x/mention-open/s.jsonl")',
     "SPOOL_PATH"),
    ('spool_path = Path("/x/mention-open/s.jsonl")', "spool_path"),
    # 🔴 THE DERIVED SIBLING — its own source segment never says
    # `mention-open`, and a round-3 audit called it the most LIKELY next
    # spelling. It is also the worst kind of miss: the ledger is
    # hand-maintained, so both sides lose the entry together and the two-way
    # pin stays GREEN.
    ('SPOOL_PATH = PICKS_PATH.parent / "spool.jsonl"', "SPOOL_PATH"),
    # …and two hops, to prove it is a fixpoint rather than one lookahead.
    ('_SPOOL_DIR = PICKS_PATH.parent / "spool"\n'
     'SPOOL_PATH = _SPOOL_DIR / "today.jsonl"', "SPOOL_PATH"),
    # 🔴 `.with_name` — MEASURED MISSED by the fixpoint's first version, which
    # also demanded a `Path(` call or a `/` in the derived node. It is exactly
    # as idiomatic as `.parent /`, and the reference to a known host-state path
    # is the signal; the path-shape test belongs to the SEED only.
    ('SPOOL_PATH = PICKS_PATH.with_name("spool.jsonl")', "SPOOL_PATH"),
    ('SPOOL_PATH = PICKS_PATH.with_suffix(".bak")', "SPOOL_PATH"),
])
def test_the_host_state_DISCOVERY_sees_every_spelling(planted, name):
    """🔴 THE POSITIVE CONTROL ON THE LEDGER'S DISCOVERY, and every one of these
    was measured SILENTLY MISSED by some version of it — the first five by the
    original `ast` walk, the derived-sibling pair by the round-2 rewrite that
    replaced it. A ledger is only as two-way as the thing that enumerates the
    left-hand side: a spelling it cannot see is an unredirected host-state file,
    which since `record_pick` means a test WRITING the operator's own data.

    ⚠ The `try`/`except` case is planted TWICE on purpose — once in the body and
    once in the HANDLER — because a walk that recursed into `body` alone would
    satisfy the first and miss the second, and the first version's claim to
    cover handlers was never exercised."""
    source = HANDLER.read_text() + "\n\n" + planted + "\n"
    assert name in _host_state_constants(source), (
        f"the discovery is blind to this spelling:\n{planted}")


def test_the_host_state_discovery_does_NOT_fire_on_a_non_path():
    """The negative control: a discovery that flagged everything would satisfy
    all six cases above and be useless. `PICKER_CLASS` names the directory and
    is an i3 window class, not a file."""
    found = _host_state_constants(HANDLER.read_text())
    assert "PICKER_CLASS" not in found, found
    assert found, "POSITIVE CONTROL: the discovery found nothing at all"


def test_the_HOST_STATE_path_ledger_is_pinned_two_way(tmp_path):
    """🔴 GROWS-OR-SHRINKS, AND IT COVERS BOTH REDIRECT MECHANISMS.

    The autouse fixture needs a `setattr` (for in-process reads) AND a `setenv`
    (for the child `_run()` spawns) per file, and the history of this file is
    that one of the two gets forgotten: the universe arm was added a release
    after the mapping's, the ranges and picks arms a release after that, and
    each gap was invisible until measured. This asserts, for every host-state
    constant the handler declares:

      * it is redirected INTO `tmp_path` — so no test reads or writes the
        operator's real data;
      * its env override names the variable the ledger says it does, and that
        variable resolves to the same place — so the subprocess half is live.

    🔴 AND IT IS TWO-WAY AGAINST THE MODULE. A new `*_PATH` constant under
    `~/.config/mention-open/` that nobody added to this ledger fails here, which
    is the only thing that makes the next one impossible to forget.
    """
    # 🔴 ASKED OF THE SOURCE, NOT OF THE MODULE OBJECT. The autouse fixture has
    # already rebound every one of these to a `tmp_path` file, so a check that
    # inspected `dir(MO)` for paths under `mention-open/` would find NOTHING and
    # pass vacuously — a ledger that is two-way against an empty set is not
    # two-way. The source is also where a new constant actually gets added.
    # 🔴 KEYED ON THE DIRECTORY, NOT ON A SPELLING — and the first version was
    # keyed on a spelling. It required the literal `Path(` and a name ending
    # `PATH`/`PICKS`, which an audit MEASURED walkable: the sibling
    # `regen-known-repos.py` spells all three of ITS host-state paths as
    # `X = _CONFIG_DIR / "y.json"`, so the next author following the
    # neighbouring file's style adds an unredirected host-state file and this
    # ledger stays green. A guard on WORDS is walkable by REWORDING.
    #
    # What is asked instead is the thing that matters: does this module-level
    # assignment name the mention-open config directory at all? That catches
    # `Path(...)`, `_CONFIG_DIR / …`, an f-string, and anything else that has to
    # mention the directory to point at it.
    #
    # ⚠ `ast`, NOT A REGEX — and the regex was tried. A pattern that had to span
    # a multi-line assignment backtracked badly enough to hang the run; walking
    # the assignment nodes and reading each one's own source segment asks the
    # same question structurally, and cannot be fooled by a `mention-open`
    # mention inside a nested function or a docstring.
    in_source = _host_state_constants(HANDLER.read_text())
    assert in_source == set(HOST_STATE_CONSTANTS), (
        f"the host-state path ledger MOVED: the handler declares {sorted(in_source)}, "
        f"the ledger names {sorted(HOST_STATE_CONSTANTS)}. Every one of these "
        f"points at a 0600 file naming PRIVATE repositories — add it to the "
        f"autouse redirect AND to this ledger, or tests will read (or write) "
        f"the operator's own data.")
    for const, env in HOST_STATE_CONSTANTS.items():
        got = getattr(MO, const)
        assert got.is_relative_to(tmp_path), (
            f"{const} is NOT redirected — it resolves to {got}, which is the "
            f"operator's own host state")
        assert os.environ.get(env) == str(got), (
            f"{const}'s env door {env} is {os.environ.get(env)!r}, not {str(got)!r} "
            f"— the SUBPROCESS half of the redirect is missing, which is "
            f"exactly the gap that let a child read the real 369-row mapping")


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
    resolved, loaded, child_ranges, child_picks = r.stdout.splitlines()[:4]
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
    # 🔴 THE SAME CLAIM FOR THE TWO NEWER FILES, AND `picks.jsonl` IS THE ONE
    # THAT MATTERS MOST HERE: a child resolving the real path would APPEND to
    # the operator's own pick log the first time a `_run()` test reached a
    # selection. Reading their data was the old hazard; writing it is a new one.
    for line, const in ((child_ranges, MO.KNOWN_RANGES_PATH),
                        (child_picks, MO.PICKS_PATH)):
        got = Path(line.strip())
        assert got.is_relative_to(tmp_path), (
            f"a CHILD of this suite resolves the operator's real host state: {got}")
        assert got == const, (got, const)


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

    🔴 THE ORDERING RUNS TOO, AND IT HAD TO BE MADE TO. The autouse redirect
    points the range table at a path that does NOT exist, so without the file
    written below `load_known_ranges` returns {} and `_ordered_universe` takes
    its early `ORDER_NO_TABLE` return — which means the pick log is never read
    and the sort never runs. This guard would then have been asserting "the
    ordering spawns nothing" about an ordering that never happened, which is
    the vacuous-green shape the whole file is written against. With a FRESH
    table and a real pick log present, every leg of the ordering executes
    inside the recording window.
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
    # A FRESH range table and a real pick log, so the ordering is ACTIVE — see
    # the docstring. `universe` must hold the repo the checkout supplies, or
    # there are no rows to order.
    ranges = tmp_path / "known_ranges.json"
    ranges.write_text(json.dumps({"rivalorg/spadeworks": 99,
                                  "hobbyist/plotwidget": 0}))
    monkeypatch.setattr(MO, "KNOWN_RANGES_PATH", ranges)
    picks = tmp_path / "picks.jsonl"
    picks.write_text(json.dumps({"t": time.time(), "repo": "rivalorg/spadeworks",
                                 "n": 11}) + "\n")
    monkeypatch.setattr(MO, "PICKS_PATH", picks)

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

    ordered: list[str] = []
    real_ordering = MO._ordered_universe
    monkeypatch.setattr(
        MO, "_ordered_universe",
        lambda rows, num: ordered.append(num) or real_ordering(rows, num))

    assert MO.main(["zzznosuchrepo#12"]) == 0
    _assert_only_local_commands(seen)
    # POSITIVE CONTROL: the fan-out really ran over the real workspace, so the
    # ledger above is a fact about a path that did the work, not about a
    # short-circuit. One checkout ⇒ one `git remote` call.
    assert [c for c in seen if c[:2] == ["git", "remote"]], seen
    # 🔴 SECOND POSITIVE CONTROL — the ORDERING ran, and ran ACTIVE. Without
    # this the ledger is a claim about a code path the test never entered: an
    # absent range table short-circuits `_ordered_universe` before it reads the
    # pick log or sorts anything. See the docstring.
    assert ordered == ["12"], ordered
    rows, state, _counts, _age = real_ordering(
        MO.repo_universe(MO.discover_repos(), []), "12")
    assert state == MO.ORDER_APPLIED, (
        f"the ordering DEGRADED to {state!r}, so the ledger above says nothing "
        f"about an active ordering — the range table is missing or stale")
    assert rows, "there were no universe rows to order"


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

    Measured on THIS corpus (195 rows): `NimbusWorks` → **0** matched without
    `-i`, **15** with. Ranking is unaffected — measured on the LOWERCASE query,
    the only one where both sides have a ranking to compare: `nimbusworks` → 15
    matched and the eponymous repo 1st, either way. An earlier docstring said
    "0 rows without, 41 with … the eponymous repo is 1 either way", which was
    both wrong (41 came from a scratch corpus, and 392 is the real universe's
    row count) and self-contradictory: rank is undefined on an empty set."""
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
    not is a destination.

    🔴 FD-PREFIXED REDIRECTIONS COUNT TOO, and the first version of this missed
    them: `shlex.split` yields `2>/tmp/leak` and `1>/tmp/leak` as single tokens
    starting with a DIGIT, so a guard keyed on `startswith(("<", ">"))` passed
    both while this docstring said "both, and no more". No live hazard — the
    exact-string pin above catches them — but a guard whose description is wider
    than its body reads as coverage while providing none."""
    assert _redirections(MO.PICKER_SH) == ["<$1", ">$2"], (
        f"PICKER_SH's redirections are {_redirections(MO.PICKER_SH)}, expected "
        f"exactly the rows FIFO in and the choice FIFO out: {MO.PICKER_SH}")


def _redirections(script: str) -> list[str]:
    """Every redirection token in a `sh -c` script, in order.

    🔴 ONE FUNCTION, CALLED BY BOTH THE GUARD AND ITS CONTROL — and that is a
    round-3 audit finding, not a style preference. The control below used to
    carry its OWN inline copy of this regex, so it certified a COPY rather than
    the instrument: MEASURED by the auditor — narrowing the guard straight back
    to the round-2 defect (`t.startswith(("<", ">"))`) and leaving the control
    untouched left the whole file GREEN at 232 passed, including the very
    control written to prevent that regression.

    ⚠ WHAT THIS COVERS, STATED AT THE WIDTH IT WAS MEASURED. `shlex` splits a
    redirection into one token, so the shapes are recognised by their prefix:
    plain `<`/`>`/`>>`, an fd-prefixed `2>`/`1>`, and `&>`. 🔴 IT IS NOT "every
    shape sh accepts" — the previous comment said that and was wrong. MEASURED
    against this `/bin/sh` (bash 5.3p15): `&>` IS honoured (it wrote the leak
    file), so it is included; `{fd}>/tmp/leak` is NOT honoured by this shell —
    bash passed it through as a literal argument — so it is knowingly left out
    rather than claimed. A shell that honoured it would need this widened.
    """
    import re as _re
    import shlex
    return [t for t in shlex.split(script) if _re.match(r"^(\d*|&)[<>]", t)]


@pytest.mark.parametrize("leak,shape", [
    ('fzf -i <"$1" >"$2" 2>/tmp/leak', "fd-prefixed stderr"),
    ('fzf -i <"$1" >"$2" 1>/tmp/leak', "fd-prefixed stdout"),
    ('fzf -i <"$1" >"$2" &>/tmp/leak', "`&>`, which THIS /bin/sh honours"),
    ('fzf -i <"$1" >>"$2"', "an APPEND instead of a truncate"),
])
def test_the_redirection_reader_SEES(leak, shape):
    """🔴 THE CONTROL DRIVES `_redirections` ITSELF. Every one of these sends the
    private rows somewhere new (or accumulates them) while keeping `<$1` and
    `>$2` intact, so the redirection LIST is the only thing that can notice —
    and narrowing the reader must turn this red, which is the property the
    previous version of this test did not have."""
    got = _redirections(leak)
    assert got != ["<$1", ">$2"], (
        f"the redirection reader cannot see {shape} in {leak!r} — it yields "
        f"{got}, which is indistinguishable from the real script")


def test_the_redirection_reader_accepts_the_REAL_script():
    """NEGATIVE CONTROL for the parametrised set above: a reader that flagged
    everything would satisfy all four and be useless. The genuine script must
    come back as exactly the two expected redirections."""
    assert _redirections(MO.PICKER_SH) == ["<$1", ">$2"]


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



# --------------------------------------------------------------------------- #
# 🔴 ROUND 2: THE TIMEOUT GUARD COVERED THE WRONG ARM
#
# `run_picker` can report `PICKED_TIMEOUT` from TWO places, and they are
# different failures:
#
#   * the ENXIO loop — the terminal never opened the rows FIFO at all;
#   * the READ loop — the picker is OPEN, the rows were delivered, and nobody
#     ever answered.
#
# The second one IS rofi's `timeout=120`: "the picker is up and the operator
# walked away". Round 1's finding was that this toast had been LOST; the guard
# added to stop it being lost again used a peer that never opens the FIFOs, so
# it only ever exercised the FIRST arm. MEASURED by the round-2 audit: mutating
# the read loop's `outcome = PICKED_TIMEOUT` to `PICKED_DISMISSED` SURVIVED the
# whole suite (225 passed).
# --------------------------------------------------------------------------- #
class _OpensThenWaits(_FakeTerminal):
    """A peer that behaves like a REAL picker nobody answers.

    It performs the shell's own redirections, drains the rows — so the operator
    is looking at a full list — and then simply never writes a selection and
    never exits. That is the ONLY shape that reaches the read loop's deadline.
    """

    def popen(self, argv, **kwargs):
        self.argv = list(argv)
        rows_fifo, choice_fifo = argv[-3], argv[-2]

        def serve():
            rfh = open(rows_fifo, "r", encoding="utf-8")
            wfh = open(choice_fifo, "w", encoding="utf-8")
            with rfh, wfh:
                self.payload = rfh.read()
                self._shown.set()
                # …and now wait, holding both ends, answering nothing.
                self._release.wait(timeout=30)

        self._shown = threading.Event()
        self._release = threading.Event()
        self._thread = threading.Thread(target=serve, daemon=True)
        self._thread.start()
        return self

    def poll(self):
        return None          # ALIVE the whole time — this is the distinction

    def terminate(self):
        self._release.set()


def test_a_timeout_with_the_LIST_ON_SCREEN_toasts(monkeypatch):
    """🔴 THE ARM ROFI'S `timeout=` ACTUALLY WAS, pinned at last.

    The sibling test drives the ENXIO arm (a terminal that never opens the
    FIFOs). This one drives the arm that matters in production: the rows were
    delivered, the picker is on screen, and the operator walked away.

    The positive control is what separates the two — this asserts the payload
    REACHED the peer, so a regression that made the terminal fail early would
    fail here rather than quietly re-testing the other arm."""
    monkeypatch.setattr(MO, "PICKER_TIMEOUT", 0.6)
    term = _OpensThenWaits(None)
    monkeypatch.setattr(MO.subprocess, "Popen", term.popen)
    said = []
    monkeypatch.setattr(MO, "notify", lambda s, b="": said.append((s, b)))
    try:
        url = MO.pick(ONE_CANDIDATE)
    finally:
        term.terminate()
    # POSITIVE CONTROL: the list really was delivered, so this is the
    # picker-is-open arm and not the terminal-never-started one.
    assert term._shown.is_set(), (
        "the peer never received the rows — this exercised the ENXIO arm again, "
        "which is the very substitution this test exists to stop")
    assert term.rows == MO.picker_rows(ONE_CANDIDATE), term.rows
    assert url == ""
    assert said, ("the picker timed out with the list on screen and toasted "
                  "NOTHING — that is rofi's `timeout=120` case, lost again")
    assert "timed out" in said[0][0], said


def test_the_two_TIMEOUT_arms_are_different_code_paths():
    """🔴 STRUCTURAL, so the pair above cannot collapse into one. `run_picker`
    must keep BOTH deadline checks — the ENXIO loop's and the read loop's.
    Deleting either leaves a hang or a silent dismissal, and a single test can
    only ever cover one of them."""
    src = HANDLER.read_text()
    body = src[src.index("def run_picker("):src.index("def pick(")]
    assert body.count("PICKED_TIMEOUT") == 2, (
        f"run_picker names PICKED_TIMEOUT {body.count('PICKED_TIMEOUT')}x — "
        "expected 2: once when the terminal never opened the rows FIFO, once "
        "when the list was on screen and nobody answered")


# --------------------------------------------------------------------------- #
# 🔴 ROUND 2: A MISSING `fzf` IS NOT `never-shown`
# --------------------------------------------------------------------------- #
def test_a_missing_fzf_is_caught_BEFORE_a_window_is_raised(monkeypatch):
    """🔴 THE PROSE THIS REPLACES WAS DISPROVED BY ITS OWN FILE. Four places
    said `never-shown` is "what a missing fzf on the wrapper's PATH looks
    like". It is not: `/bin/sh` applies `<"$1" >"$2"` BEFORE exec'ing — the very
    fact `run_picker`'s open-order fix rests on — so a missing fzf gives the
    rows FIFO a reader and the run ends as a SILENT dismissal.

    MEASURED: `/bin/sh -c 'zzznosuchbinary <"$1" >"$2"'` exits 127 with the
    redirections applied. And `proc.returncode` cannot rescue it: alacritty
    0.17.0 exits **0** whether its `-e` command exits 0 or 127, so the 127 from
    a shell that DID run is invisible in the terminal's status. (An earlier
    docstring added "or does not exist at all" — FALSE, measured under Xvfb:
    `-e zzznosuchbinary` exits 1. The conclusion is unchanged, because `-e` here
    is always the existing `/bin/sh`.)

    So it is caught BEFORE the spawn, which is better than any toast after it:
    no window is raised at all."""
    spawned = []
    # `pick()` imports `shutil` locally, which binds the SAME module object this
    # file imported — so patching it here is patching what the handler calls.
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    monkeypatch.setattr(MO.subprocess, "Popen",
                        lambda *a, **k: spawned.append(a) or _Dead())
    said = []
    monkeypatch.setattr(MO, "notify", lambda s, b="": said.append((s, b)))
    assert MO.pick(ONE_CANDIDATE) == ""
    assert not spawned, "a window was raised for a picker that cannot run"
    assert said, "a missing fzf said nothing — the silent dead click"
    assert "fzf" in said[0][1], said


def test_the_missing_fzf_preflight_does_not_fire_when_fzf_IS_present(
        monkeypatch):
    """NEGATIVE CONTROL for the test above: with a real fzf on PATH the
    pre-flight must be invisible, or it would break every ordinary pick."""
    _require_fzf()
    row = MO.picker_rows(ONE_CANDIDATE)[0]
    said = []
    monkeypatch.setattr(MO, "notify", lambda s, b="": said.append((s, b)))
    _term, url = _drive_picker(monkeypatch, choose=row)
    assert url == ONE_CANDIDATE[0]["url"]
    assert not said, said


def test_the_NEVER_SHOWN_toast_does_not_blame_a_cause_it_cannot_HAVE(
        monkeypatch):
    """🔴 A TOAST BODY IS A CLAIM, and this one named two causes that cannot
    reach it — a missing alacritty raises `FileNotFoundError` at `Popen` and
    takes the other branch, and a missing fzf is pre-flighted. Sending an
    operator to check a PATH that is fine is worse than saying less."""
    monkeypatch.setattr(MO.subprocess, "Popen", lambda *a, **k: _Dead())
    said = []
    monkeypatch.setattr(MO, "notify", lambda s, b="": said.append((s, b)))
    assert MO.pick(ONE_CANDIDATE) == ""
    body = said[0][1]
    assert "fzf" not in body, (
        f"the never-shown toast still blames fzf, which cannot produce it: {body}")
    assert "PATH" not in body, (
        f"the never-shown toast still blames the wrapper's PATH: {body}")
    assert "DISPLAY" in body, body


def test_the_missing_fzf_toast_names_a_SWITCH_not_a_file_to_edit(monkeypatch):
    """🔴 A REMEDY THAT IS ALREADY APPLIED IS NOT A REMEDY. This body used to say
    "add pkgs.fzf to the hint wrapper in nix/programs/alacritty/default.nix" —
    but that entry is enforced by
    `test_the_alacritty_wrapper_PATH_covers_every_executable_the_handler_spawns`,
    so in ANY tree that passes this suite it is already there and the operator is
    sent to edit a correct file.

    What can actually raise it: a deployed wrapper GENERATION predating the entry
    (this repo's merged-≠-deployed trap), a GC'd store path, or the script run
    outside the wrapper. All three are a `home-manager switch`, none an edit.

    Same operator consequence as round 2's finding A — a diagnosis pointing
    somewhere fine, in a module whose thesis is that an undiagnosed silence is
    the bug."""
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    monkeypatch.setattr(MO.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("a window was raised"))
    said = []
    monkeypatch.setattr(MO, "notify", lambda s, b="": said.append((s, b)))
    assert MO.pick(ONE_CANDIDATE) == ""
    assert said, "the missing-fzf pre-flight said nothing"
    body = said[0][1]
    assert "DEPLOY gap" in body and "home-manager switch" in body, (
        f"the missing-fzf toast must name the DEPLOY gap and the switch that "
        f"closes it, not a file to edit: {body!r}")
    assert "default.nix" not in body, (
        f"the toast sends the operator to edit a file whose pkgs.fzf entry is "
        f"test-enforced and therefore already correct: {body!r}")


def test_the_UNKNOWN_OUTCOME_toast_body_is_FIXED_never_interpolated(monkeypatch):
    """🔴 THE ONE LINE THAT COULD CARRY AN ARBITRARY STRING INTO A FORBIDDEN
    SURFACE. The module docstring says the universe may reach the picker window
    and NOWHERE else — "never to a notification body" — and this catch-all used
    to interpolate `str(outcome)`.

    Not a live leak today (the four outcomes are literals and the branch is
    unreachable from `run_picker`), which is exactly why it needs a test: the
    day an outcome is built from data, the leak would be silent. Driven by
    stubbing `run_picker`, because nothing else can reach the branch."""
    monkeypatch.setattr(MO, "run_picker",
                        lambda *_a, **_k: ("", "outcome-carrying-SECRETREPO"))
    said = []
    monkeypatch.setattr(MO, "notify", lambda s, b="": said.append((s, b)))
    assert MO.pick(ONE_CANDIDATE) == ""
    assert said, "an unknown outcome was dropped silently — round 1's defect"
    summary, body = said[0]
    assert "unknown outcome" in summary, said
    assert "SECRETREPO" not in body and "SECRETREPO" not in summary, (
        f"the unknown-outcome toast interpolates its outcome into a "
        f"notification body, which the module docstring forbids: {said!r}")


def test_every_KNOWN_outcome_is_reachable_through_pick(monkeypatch):
    """NEGATIVE CONTROL for the test above: the catch-all must fire ONLY for an
    outcome outside the ledger. A version that toasted on every outcome would
    satisfy that test and break all four real paths."""
    for outcome in MO.PICKED_OUTCOMES:
        said = []
        monkeypatch.setattr(MO, "run_picker", lambda *_a, **_k: ("", outcome))
        monkeypatch.setattr(MO, "notify", lambda s, b="": said.append((s, b)))
        MO.pick(ONE_CANDIDATE)
        assert not any("unknown outcome" in s for s, _b in said), (
            f"{outcome!r} is in PICKED_OUTCOMES but took the catch-all: {said}")


def test_the_PICKED_outcome_set_is_pinned_and_every_one_is_HANDLED():
    """🔴 TWO-WAY, and the reason is round 1's own finding. `pick()` branches on
    these constants and its `else` used to be silence, so a fifth outcome added
    later would be dropped without a sound — which is exactly how the timeout
    toast went missing in the first place.

    Both directions: the ledger names every `PICKED_*` constant the module
    defines, and `pick()` accounts for every member of the ledger."""
    defined = {n for n in dir(MO) if n.startswith("PICKED_")
               and isinstance(getattr(MO, n), str)}
    assert defined == {"PICKED_SELECTED", "PICKED_DISMISSED", "PICKED_TIMEOUT",
                       "PICKED_NEVER_SHOWN"}, sorted(defined)
    assert set(MO.PICKED_OUTCOMES) == {getattr(MO, n) for n in defined}, (
        "PICKED_OUTCOMES and the PICKED_* constants disagree")
    # …and `pick()` must not silently drop one.
    src = HANDLER.read_text()
    body = src[src.index("def pick("):]
    assert "not in PICKED_OUTCOMES" in body, (
        "pick() no longer has a catch-all for an outcome nobody taught it "
        "about — that is round 1's lost-toast defect one layer up")


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

# 🔴 THE PANE-GUESSED REPO IS A SECOND NAME THE NOTES MUST NOT CARRY, AND IT IS
# NOT IN THE UNIVERSE. That is the point of it — a guess comes from the tmux
# pane, not from the mapping — and it is exactly why
# `_no_universe_token_anywhere` is STRUCTURALLY BLIND to a leak of it: every
# token that check knows comes from `FAKE_UNIVERSE`, which by construction does
# not contain this. So a mutant that spliced the guessed repo into the note
# ("Row 1 is a guess from the tmux pane (wrongorg/wrongrepo)" — a natural-looking
# improvement) passed every assertion in this file, under docstrings that said
# the note "must not name a repository".
PANE_GUESS = "wrongorg/wrongrepo"
PANE_GUESS_TOKENS = {
    PANE_GUESS,
    PANE_GUESS.split("/")[0],
    PANE_GUESS.split("/")[1],
}


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


def test_the_pane_guess_is_OUTSIDE_the_universe_and_spelled_distinctly():
    """🔴 THE GUARD ON THE SECOND LEDGER, and it pins the very property that
    makes the second ledger necessary.

    If `PANE_GUESS` were in `FAKE_UNIVERSE`, `_no_universe_token_anywhere` would
    already cover it and `_no_guessed_repo_token_anywhere` would be decoration
    that no mutation could kill. It is out, deliberately — and the two token sets
    must stay disjoint, or a leak of one becomes indistinguishable from a leak of
    the other.
    """
    assert PANE_GUESS not in FAKE_UNIVERSE.values(), PANE_GUESS
    assert PANE_GUESS not in FAKE_UNIVERSE, PANE_GUESS
    assert not (PANE_GUESS_TOKENS & FAKE_UNIVERSE_TOKENS), (
        PANE_GUESS_TOKENS & FAKE_UNIVERSE_TOKENS)
    assert len(PANE_GUESS_TOKENS) == 3, PANE_GUESS_TOKENS
    for a in PANE_GUESS_TOKENS:
        for b in PANE_GUESS_TOKENS:
            if a != b and "/" not in a and "/" not in b:
                assert a not in b, (
                    f"{a!r} is a substring of {b!r} — a leak of the first would "
                    f"be indistinguishable from a leak of the second")
    # ...and no token may be a word the note legitimately uses, or the guard
    # would be red on correct output rather than on a leak.
    for token in PANE_GUESS_TOKENS:
        assert token not in MO.guessed_note("audit-pr 1291", below=3, rank=1)
        assert token not in MO.guessed_note("audit-pr 1291")


def _no_guessed_repo_token_anywhere(everywhere: str, label: str) -> None:
    """🔴 THE NOTE MUST NOT NAME THE GUESSED REPOSITORY EITHER.

    `guessed_note`'s own docstring says it "names the clicked text and two
    counts, NOTHING ELSE — never the repository", and until 2026-09-09 the only
    check behind that sentence was `_no_universe_token_anywhere`, which cannot
    see this name at all (see `PANE_GUESS`). A guard reading as coverage while
    providing none is worse than none: it stops anyone looking.

    The disclosure argument is the same one as for the universe. The candidate
    ROW already shows the repository — that is what the operator is being asked
    about — so the note repeating it buys nothing and adds a second place a name
    can escape from. It is also the wrong claim: the note explains why a choice
    is being offered, and the pane's repo is not part of that explanation.
    """
    for token in sorted(PANE_GUESS_TOKENS):
        assert token not in everywhere, f"{label}: {token}"


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
    is the regression for the report.

    🔴 "IT MUST NOT NAME A REPOSITORY" IS NOW ACTUALLY CHECKED. The body used to
    run `_no_universe_token_anywhere` alone, which knows only the tokens in
    `FAKE_UNIVERSE` — and the repository this test is ABOUT is the pane guess,
    which is deliberately NOT in it. So the sentence covered every repository
    except the one at issue, and a mutant leaking the guess into the note passed
    under a docstring claiming otherwise. Both ledgers are asserted now; see
    `_no_guessed_repo_token_anywhere`."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: dict(FAKE_UNIVERSE))
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: PANE_GUESS)
    seen = {}
    monkeypatch.setattr(MO, "pick",
                        lambda c, mesg="": seen.update(n=len(c), mesg=mesg) or "")
    assert MO.main(["audit-pr 1291"]) == 0
    assert seen["n"] > 1, ("the guess must not be the ONLY option — that is the "
                           "reported defect, not the design")
    assert "audit-pr 1291" in seen["mesg"], seen
    assert "tmux pane" in seen["mesg"], seen
    _no_universe_token_anywhere(seen["mesg"], "GUESSED-NOTE DISCLOSURE")
    _no_guessed_repo_token_anywhere(seen["mesg"], "GUESSED-NOTE DISCLOSURE")


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
    _no_guessed_repo_token_anywhere(seen["mesg"], "BARE-HASH-N GUESS NOTE")


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
    # noqa: PLC0415 — deliberately local, and it binds the SAME module object
    # the handler imported: `exec_module(MO)` at the top of this file already put
    # `scripts/collector` on `sys.path` and left `mention_scan` in `sys.modules`.
    # (This comment used to point at `_load_handler`, a function that does not
    # exist in this file and, by `git log -S`, never has — the loader has always
    # been the module-level block beside `MO`.)
    import mention_scan as MS  # noqa: PLC0415

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



def test_the_GUESSED_note_reaches_the_picker_and_no_other_surface(
        monkeypatch, capsys, real_notify):
    """🔴 THE SEAM BETWEEN #1457 AND THE fzf SWAP, AND A CLEAN `git merge` LEFT
    IT OPEN. Two changes landed on this file at once. #1457 added
    `_no_guessed_repo_token_anywhere` because `_no_universe_token_anywhere`
    knows `FAKE_UNIVERSE` and the pane guess is deliberately NOT in it — so the
    universe guard covered every repository except the one at issue. This branch
    replaced rofi with a terminal, adding surfaces rofi never had: the child's
    ARGV and a FIFO directory.

    The merge was textually clean and semantically short: every new sink guard
    on this branch used the UNIVERSE token set alone, which is exactly the
    blindness #1457 existed to close — so the guessed repo could have reached a
    terminal argv with the whole suite green. This test is the join.

    It asserts the SPLIT, not just absence: the guess MUST appear in the
    candidate rows — offering it is the entire feature — and must appear nowhere
    else at all."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: dict(FAKE_UNIVERSE))
    monkeypatch.setattr(MO, "load_known_universe", lambda *a, **k: [])
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: PANE_GUESS)
    term = _FakeTerminal(None)
    monkeypatch.setattr(MO.subprocess, "Popen", term.popen)

    assert MO.main(["audit-pr 1291"]) == 0

    n = int(term.argv[-1])
    header = " ".join(term.rows[:n])
    rows = term.rows[n:]

    # POSITIVE CONTROLS FIRST — three of them, because each absence below is
    # vacuous without the matching presence.
    assert rows, "the picker got no candidate rows at all"
    assert any(PANE_GUESS in r for r in rows), (
        f"the guessed repo is NOT among the offered rows — offering it is the "
        f"whole feature, so every absence asserted below would be vacuous: {rows}")
    assert "audit-pr 1291" in header, f"the note never reached the picker: {header}"

    # 1. THE NOTE names the click and counts, never either repository.
    _no_universe_token_anywhere(header, "GUESSED E2E HEADER")
    _no_guessed_repo_token_anywhere(header, "GUESSED E2E HEADER")

    # 2. THE TERMINAL'S ARGV — the surface rofi did not have. It may carry flags
    #    and the two FIFO paths and nothing else.
    argv = " ".join(term.argv)
    _no_universe_token_anywhere(argv, "GUESSED E2E TERMINAL ARGV")
    _no_guessed_repo_token_anywhere(argv, "GUESSED E2E TERMINAL ARGV")

    # 3. AND THE PUBLISHED SINKS, through the real `notify`.
    blob = _every_sink(capsys, real_notify)
    _no_universe_token_anywhere(blob, "GUESSED E2E EVERY SINK")
    _no_guessed_repo_token_anywhere(blob, "GUESSED E2E EVERY SINK")


def test_the_guessed_seam_guard_can_actually_FIRE():
    """POSITIVE CONTROL on the pair of token guards the test above leans on —
    both must reject the thing they are named for, or six absences prove
    nothing. `_no_universe_token_anywhere` is already controlled elsewhere; this
    pins that the GUESS guard is not wired to an empty set, which is the
    specific way #1457's finding could regress."""
    assert PANE_GUESS_TOKENS, "the guess token set is EMPTY — every check passes"
    # 🔴 THE BLINDNESS ITSELF, PINNED — this is why both guards are needed at
    # every site and why the clean merge was not a safe one. The UNIVERSE guard
    # does NOT fire on a leaked pane guess: it knows `FAKE_UNIVERSE`, and the
    # guess is deliberately outside it. Anyone tempted to "simplify" the pair
    # back to one call reads this first.
    _no_universe_token_anywhere(f"leaked {PANE_GUESS}", "BLINDNESS CONTROL")
    with pytest.raises(AssertionError):
        _no_guessed_repo_token_anywhere(f"leaked {PANE_GUESS}", "CONTROL")
    with pytest.raises(AssertionError):
        _no_guessed_repo_token_anywhere(
            f"leaked {PANE_GUESS.split('/')[1]}", "CONTROL")
    # …and it must NOT fire on an innocent string, or it would red everything.
    _no_guessed_repo_token_anywhere("github 12 — clawgate task", "CONTROL")


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


# The universe rows may reach the operator's own PICKER WINDOW and nowhere else,
# so every sink the handler can write to is enumerated here rather than left to
# whichever one a test happened to think of. A sink missing from this list is a
# hole; adding one to the module means adding it here.
#
# 🔴 THE THREE SINKS THIS FOLDS ARE STDOUT, STDERR AND THE `notify-send` ARGV,
# AND THAT IS NOT EVERY PLACE THE MODULE WRITES. There is a FOURTH destination —
# the interactive picker: its rows, and the `mesg` line above them. This helper
# cannot see it and never will, because the picker is reached through `pick()`,
# which every test here replaces with a spy.
#
# 🔴 THAT EXCLUSION IS THE DESIGN, NOT A GAP. The picker is the ONE surface the
# universe is allowed to reach — offering those rows is the entire feature —
# so folding it in would make every disclosure assertion below red on correct
# behaviour. The distinction is not "three sinks vs four": it is
# PUBLISHED-vs-PRIVATE. stdout, stderr and a desktop toast outlive the click and
# can be read by something other than the person who clicked; the picker is a
# transient window on the operator's own screen, raised by this click, gone the
# moment they choose or dismiss. The property is about the SINK, not about which
# program draws it — swapping the picker implementation changes nothing here.
#
# ⚠ THAT SENTENCE SAID "drawn by this process" AND THE SWAP IT ANTICIPATED HAS
# NOW HAPPENED, so the wording is corrected rather than left to rot: the picker
# is fzf inside a float terminal, i.e. a CHILD process, not a call inside this
# one. The published-vs-private property is untouched — a transient window on
# the operator's own screen either way — but "this process" is no longer true,
# and a child process brings two surfaces rofi did not have. Both are guarded,
# and NEITHER is folded in here: the rows and `mesg` travel a FIFO pair in a
# 0700 dir (`test_the_rows_go_down_a_FIFO_that_does_not_outlive_the_pick`), and
# the terminal's argv carries only flags and those two paths
# (`test_NO_ROW_reaches_the_terminal_ARGV`,
# `test_the_GUESSED_note_reaches_the_picker_and_no_other_surface`).
#
# 🔴 AND "MAY SHOW THE UNIVERSE" IS NOT "MAY SHOW ANYTHING". The picker's own
# `mesg` is guarded separately, at each site that builds one, by
# `_no_universe_token_anywhere` and `_no_guessed_repo_token_anywhere` — see
# `guessed_note`'s docstring in the handler, which says outright that this
# helper "would not see this one".
#
# ⚠ THE PARAGRAPH ABOVE REPLACES ONE THAT READ "that is the WHOLE list, because
# those are the only places `mention-open.py` writes". That was false in the
# same shape it was written to warn about: the handler's own module docstring
# and `guessed_note` both name the picker, and a reader who believed the
# sentence would conclude a `mesg` leak was already covered here. It also still
# holds for what it WAS about — this module has no log file and no spool (the
# tailer is the module with a spool; its disclosure guards live in
# `test_session_tailer.py`) — and that half is kept.
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
    _no_guessed_repo_token_anywhere(seen["mesg"], "GUESSED-NOTE DISCLOSURE")


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
    and `tmux pane` is walkable by a reword that still names the wrong row.

    ⚠ THE ORDERING CLAUSE IS NOW PART OF THAT STRING, and moving this pin was a
    DELIBERATE act rather than a test edit made to get green. Every picker
    carrying universe rows says how those rows are ordered — or that they are
    not — because an ordered list and an unordered one look identical and only
    one is worth trusting (see `ordering_note`). This fixture has no range
    table, which is the cold-start state, so the clause is the quiet `no-table`
    wording."""
    seen = _guessed_picker(monkeypatch, text="#1291",
                           universe=[UNIVERSE_ONLY, "acme/widget"])
    assert seen["mesg"] == (
        "#1291 names no repository. Row 2 is a guess from the tmux pane, which "
        "may not be the pane you clicked in — the 5 rows below it are every "
        "repository this host knows. Type to search, or dismiss. · rows "
        "unordered — no reference-range table on this host yet "
        "(scripts/regen-known-repos.py builds one)"), seen["mesg"]
    _no_universe_token_anywhere(seen["mesg"], "BARE-#N GUESS-NOTE DISCLOSURE")


def test_the_audit_pr_note_still_names_ROW_1(monkeypatch):
    """The other half of the same pin — the shape #1380 fixed must not have its
    row number silently shifted by the `rank` parameter. Its guess is row 1.

    ⚠ Carries the same deliberately-added ordering clause as its sibling above;
    see that test's note for why moving a whole-string pin was the right move
    here rather than a test edit to get green."""
    seen = _guessed_picker(monkeypatch, universe=[UNIVERSE_ONLY, "acme/widget"])
    assert seen["mesg"] == (
        "audit-pr 1291 names no repository. Row 1 is a guess from the tmux "
        "pane, which may not be the pane you clicked in — the 5 rows below it "
        "are every repository this host knows. Type to search, or dismiss. · "
        "rows unordered — no reference-range table on this host yet "
        "(scripts/regen-known-repos.py builds one)"), seen["mesg"]


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
    universe. It must still do so, and it must still RECOMMEND nothing: the rows
    are a real clawgate task plus a list.

    ⚠ THE NOTE IS NO LONGER EMPTY, AND THAT CHANGE IS DELIBERATE. It used to
    assert `mesg == ""` on the reasoning that a picker of pure evidence needs no
    explanation — which is still true of RECOMMENDATIONS and was never true of
    ORDER. This is the shape that puts several hundred universe rows under the
    clawgate task, so it is precisely where the operator needs to know whether
    those rows are ranked (see `ordering_note`); asserting an empty string here
    would have made the commonest path the one place the ordering is silent.

    What is pinned instead is the narrower true claim: the note says only how
    the rows are ORDERED, and still recommends no repository. Both token guards,
    because `_no_universe_token_anywhere` is blind to the pane-guessed repo."""
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
    assert seen["mesg"] == MO.ordering_note("1291", MO.ORDER_NO_TABLE), seen["mesg"]
    for wrong in ("guess", "names no repository", "nothing here knows"):
        assert wrong not in seen["mesg"], (
            f"a picker of pure evidence is being described as a dead end or a "
            f"recommendation ({wrong!r}): {seen['mesg']}")
    _no_universe_token_anywhere(seen["mesg"], "BARE-#N ORDERING NOTE")
    for token in PANE_GUESS_TOKENS:
        assert token not in seen["mesg"], seen["mesg"]


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


# --------------------------------------------------------------------------- #
# 🔴 ORDERING THE PICKER — TIER A (plausibility) AND TIER B (the operator's own
# picks). See `order_universe` in the handler for the design; what these pin is
# the two properties that make it SAFE rather than merely useful:
#
#   * it is a PERMUTATION. Nothing is dropped, whatever the table says, because
#     a row that is not in the list cannot be typed at and a day-old snapshot is
#     wrong often enough to matter.
#   * TIER B CANNOT CROSS A TIER A CLASS. A learned preference is evidence about
#     the OPERATOR; a class is evidence about the REPOSITORY. No number of past
#     picks makes a repo with zero references able to answer `#1291`.
#
# THE MEASUREMENT THESE EXIST FOR, recorded once here rather than in each test:
# a 120-repo sample of this host's real universe found **54 (45%) with no issues
# and no pull requests at all**, 40 topping out at 1-9, 19 at 10-99, 5 at
# 100-999 and 2 at 1000+. So a high number is strongly discriminative and a low
# one is weak — and nearly half the list can satisfy NO numeric click at all,
# which is the bigger and cheaper half of the win.
# `_measured_shape_corpus` reproduces that distribution at ~390 rows, the real
# universe's size, with SYNTHETIC names: this repo is public.
# --------------------------------------------------------------------------- #
def test_the_plausibility_CLASS_set_is_pinned_and_ORDERED():
    """🔴 TWO-WAY, AND THE ORDER IS THE BEHAVIOUR. `order_universe` sorts on
    these integers, so they are not labels — a fifth class added later would
    take a rank nobody chose, and renumbering them silently reorders the picker.
    Same argument as the `PICKED_OUTCOMES` ledger above, one concept along."""
    assert MO.PLAUSIBILITY_CLASSES == (
        MO.CLASS_PLAUSIBLE, MO.CLASS_BELOW, MO.CLASS_UNKNOWN,
        MO.CLASS_IMPOSSIBLE)
    assert list(MO.PLAUSIBILITY_CLASSES) == sorted(MO.PLAUSIBILITY_CLASSES), (
        "the classes are SORT KEYS — they must be in ascending rank order")
    assert len(set(MO.PLAUSIBILITY_CLASSES)) == 4, "two classes share a rank"
    # The ranks themselves, spelled out: an assertion on the tuple alone would
    # survive all four being renumbered together.
    assert (MO.CLASS_PLAUSIBLE < MO.CLASS_BELOW < MO.CLASS_UNKNOWN
            < MO.CLASS_IMPOSSIBLE)


@pytest.mark.parametrize("num,max_ref,expected", [
    ("1291", 1300, MO.CLASS_PLAUSIBLE),   # head is past N
    ("1291", 1291, MO.CLASS_PLAUSIBLE),   # exactly N — the boundary, INCLUDED
    ("1291", 1290, MO.CLASS_BELOW),       # one short
    ("1291", 4, MO.CLASS_BELOW),
    ("1291", 0, MO.CLASS_IMPOSSIBLE),     # no issues and no PRs at all
    ("1291", None, MO.CLASS_UNKNOWN),     # not in the table
    ("1", 1, MO.CLASS_PLAUSIBLE),
    ("1", 0, MO.CLASS_IMPOSSIBLE),
])
def test_plausibility_class_at_the_BOUNDARY_and_either_side(num, max_ref, expected):
    """Pinned AT the boundary and at a middle value on both sides, because
    `>=` vs `>` is the one mutation here a single-point test cannot see:
    `max_ref == N` is the repo whose newest reference IS the clicked one, which
    is the most plausible row there is."""
    assert MO.plausibility_class(num, max_ref) == expected


def test_UNKNOWN_and_IMPOSSIBLE_are_DIFFERENT_and_UNKNOWN_ranks_HIGHER():
    """🔴 THE DISTINCTION THE WHOLE SCHEME RESTS ON, asserted rather than
    described. `0` is the strongest signal available — it is the only value that
    rules a repository out — so collapsing "I could not ask" into it would rank
    a perfectly good repository last on no evidence.

    `regen-known-repos.py` keeps the two apart on the WRITE side by OMITTING an
    unanswered repo; this is the read side of the same contract."""
    assert MO.plausibility_class("12", None) != MO.plausibility_class("12", 0)
    assert MO.CLASS_UNKNOWN < MO.CLASS_IMPOSSIBLE, (
        "an unmeasured repo must rank ABOVE one measured to have no references "
        "— it may be anything, while the other is known to be nothing")


def test_a_NON_NUMERIC_subject_is_UNKNOWN_rather_than_guessed():
    """There is no ordering to apply when the clicked subject is not a number,
    and inventing one would be worse than leaving the rows alone."""
    assert MO.plausibility_class("", 40) == MO.CLASS_UNKNOWN
    assert MO.plausibility_class("abc", 40) == MO.CLASS_UNKNOWN


# --------------------------------------------------------------------------- #
# order_universe — the sort
# --------------------------------------------------------------------------- #
_ORDER_UNIVERSE = ["acme/alpha", "acme/beta", "bravo/gamma", "delta/epsilon",
                   "echo/zeta"]
_ORDER_RANGES = {"acme/alpha": 5, "acme/beta": 0, "bravo/gamma": 2000,
                 "delta/epsilon": 1300}


def test_ordering_is_a_PERMUTATION_and_never_a_filter():
    """🔴 THE SAFETY PROPERTY. A row that is not in the list cannot be typed at,
    so dropping one on a day-old snapshot is unrecoverable from inside the
    picker — the operator's only move is to dismiss and type a URL by hand,
    which is the dead end this handler exists to remove. Ranking it last costs
    a scroll.

    Asserted as a MULTISET equality rather than a length, so a mutant that
    dropped one row and duplicated another would still fail."""
    for num in ("1291", "12", "1", "99999"):
        got = MO.order_universe(_ORDER_UNIVERSE, num, _ORDER_RANGES)
        assert sorted(got) == sorted(_ORDER_UNIVERSE), (
            f"ordering for #{num} was not a permutation: {got}")


def test_an_IMPOSSIBLE_repo_is_RANKED_LAST_and_never_DROPPED():
    """The class it would be most tempting to hide — 45% of the real universe —
    is present, and it is at the bottom."""
    got = MO.order_universe(_ORDER_UNIVERSE, "1291", _ORDER_RANGES)
    assert "acme/beta" in got, "the IMPOSSIBLE row was FILTERED, not ranked"
    assert got[-1] == "acme/beta", got


def test_a_HIGH_number_puts_the_repos_that_could_have_it_ON_TOP():
    """The headline behaviour: `#1291` is plausible for the two repos whose
    heads are past it, and the NEARER head comes first."""
    got = MO.order_universe(_ORDER_UNIVERSE, "1291", _ORDER_RANGES)
    assert got[:2] == ["delta/epsilon", "bravo/gamma"], got


def test_within_PLAUSIBLE_the_NEARER_head_wins():
    """`max_ref - N` ascending: a repository whose newest reference is just past
    `N` is a better fit than one that passed it a thousand references ago.

    🔴 THE NEGATIVE CONTROL IS THE ALPHABET, and it is asserted. `bravo/gamma`
    sorts BEFORE `delta/epsilon` by name, so a mutant that dropped the distance
    term would put them the other way round — which is what makes this test
    about the distance rather than about `sorted` being stable."""
    got = MO.order_universe(_ORDER_UNIVERSE, "1291", _ORDER_RANGES)
    assert got.index("delta/epsilon") < got.index("bravo/gamma"), got
    assert "bravo/gamma" < "delta/epsilon", (
        "the fixture no longer reproduces the alphabet/distance disagreement — "
        "this test would now pass under a mutant that ignored the distance")


def test_the_DISTANCE_term_is_INERT_outside_PLAUSIBLE():
    """🔴 THE CONDITION THE SORT KEY'S COMMENT CALLS LOAD-BEARING, PINNED — it
    had NO test, and an adversarial audit measured the mutant (drop
    `klass == CLASS_PLAUSIBLE`, keep `max_ref - target`) SURVIVING every
    ordering fixture in this file.

    Outside PLAUSIBLE the distance is NEGATIVE, so leaving it live sorts BELOW
    rows WORST-FIRST: for `#1291`, a repo whose head is `#5` (d = -1286) would
    outrank one whose head is `#1290` (d = -1). Backwards, and invisible to
    every other test here because no fixture had TWO rows in the same
    non-PLAUSIBLE class with different `max_ref` — which is exactly the gap.

    🔴 THE FIXTURE IS BUILT SO ALL THREE CANDIDATE ORDERS DISAGREE — and the
    first version of this docstring CLAIMED that while the fixture did not have
    it. A round-2 audit measured `acme/near, acme/far, acme/mid`: alphabetical
    came out `far, mid, near` and the mutant came out `far, mid, near` too,
    identical. The test still killed the mutant it names, but it could not have
    separated that mutant from the ALPHABETISING one `order_universe`'s own
    docstring warns about. The names below are prefixed so the three genuinely
    differ, and the three-way disagreement is now ASSERTED rather than asserted
    about."""
    universe = ["b/near", "a/far", "c/mid"]            # deliberately NOT sorted
    ranges = {"b/near": 1290, "a/far": 5, "c/mid": 400}
    got = MO.order_universe(universe, "1291", ranges)
    assert all(MO.plausibility_class("1291", ranges[r]) == MO.CLASS_BELOW
               for r in universe), "POSITIVE CONTROL: these are not all BELOW"
    assert got == universe, (
        f"BELOW rows were reordered by the distance term, which is negative "
        f"there and therefore ranks them WORST-FIRST: {got}")
    # 🔴 THE THREE ORDERS, ASSERTED PAIRWISE DISTINCT. Without this the test
    # passes against an alphabetising sort whenever alphabetical happens to
    # equal the incoming order — which is exactly how the previous fixture's
    # claim became false without anything going red.
    alphabetical = sorted(universe, key=str.lower)
    mutant = sorted(universe, key=lambda r: ranges[r] - 1291)
    assert len({tuple(universe), tuple(alphabetical), tuple(mutant)}) == 3, (
        f"the fixture's three candidate orders are not pairwise distinct — "
        f"incoming={universe} alphabetical={alphabetical} mutant={mutant}")


def test_a_LOW_number_reorders_the_SAME_rows_DIFFERENTLY():
    """🔴 THE ORDER IS A FUNCTION OF THE CLICKED NUMBER, not a fixed ranking.
    `#3` is plausible for every repo with any references at all, and the one
    whose head is nearest 3 leads — a different repo from `#1291`'s winner.

    A version of this feature that merely sorted by `max_ref` DESCENDING would
    pass every other test in this block and fail this one."""
    high = MO.order_universe(_ORDER_UNIVERSE, "1291", _ORDER_RANGES)
    low = MO.order_universe(_ORDER_UNIVERSE, "3", _ORDER_RANGES)
    assert low[0] == "acme/alpha", low
    assert high[0] == "delta/epsilon", high
    assert low != high, "the order did not move between a low and a high number"


def test_COLD_START_leaves_the_incoming_order_EXACTLY_as_it_was():
    """🔴 THE REQUIREMENT THAT NOTHING REGRESSES ON A HOST WITH NO TABLE. No
    ranges and no picks must reproduce today's picker exactly, and it is met by
    CONSTRUCTION rather than by a branch: every key becomes
    `(UNKNOWN, 0.0, 0, name)` and `sorted` is stable.

    Asserted on a DELIBERATELY UNSORTED input, so a mutant that alphabetised
    instead of preserving order goes red. The real incoming list is already
    sorted by `repo_universe`, which would have made such a mutant invisible."""
    shuffled = ["zulu/one", "alpha/two", "mike/three", "bravo/four"]
    for scores in ({}, None):
        got = (MO.order_universe(shuffled, "1291", {}, scores) if scores
               is not None else MO.order_universe(shuffled, "1291", {}))
        assert got == shuffled, (
            f"COLD START REGRESSION: with no ranges and no picks the "
            f"incoming order must survive untouched, and it did not — "
            f"{got} != {shuffled}")


# --------------------------------------------------------------------------- #
# TIER B — the operator's own picks
# --------------------------------------------------------------------------- #
_T0 = 1_757_000_000.0


def test_TIER_B_orders_WITHIN_a_class():
    """A repo the operator has picked before comes first AMONG ITS EQUALS."""
    universe = ["acme/alpha", "acme/beta", "acme/gamma"]
    ranges = {"acme/alpha": 2000, "acme/beta": 2000, "acme/gamma": 2000}
    picks = [{"t": _T0 - 86400, "repo": "acme/gamma", "n": 1290}]
    scores = MO.pick_scores(picks, "1291", now=_T0)
    got = MO.order_universe(universe, "1291", ranges, scores)
    assert got[0] == "acme/gamma", (
        f"a repo picked yesterday at #1290 did not lead its own class: {got}")
    # NEGATIVE CONTROL: without the picks it is LAST, by the alphabet — so this
    # test cannot pass against a sort that ignores the score.
    assert MO.order_universe(universe, "1291", ranges)[0] == "acme/alpha"


def test_TIER_B_can_NEVER_promote_across_a_TIER_A_class():
    """🔴 THE INVARIANT. A learned preference is evidence about the OPERATOR; a
    Tier A class is evidence about the REPOSITORY. No amount of past picking
    makes a repo with zero references able to answer `#1291`, so a score must
    never lift a row past a class boundary.

    Built to make the mutant unmissable: `acme/impossible` is picked FIFTY
    times, a minute ago, at EXACTLY the clicked number — the strongest Tier B
    signal the scheme can produce — against one unpicked PLAUSIBLE row. If the
    score were ever compared before the class, it would lead."""
    universe = ["acme/plausible", "acme/impossible"]
    ranges = {"acme/plausible": 1300, "acme/impossible": 0}
    picks = [{"t": _T0 - 60, "repo": "acme/impossible", "n": 1291}
             for _ in range(50)]
    scores = MO.pick_scores(picks, "1291", now=_T0)
    assert scores["acme/impossible"] > 90, (
        f"POSITIVE CONTROL FAILED — the fixture's score is only "
        f"{scores.get('acme/impossible')}, so this test could pass under a "
        f"mutant that DOES compare scores before classes")
    got = MO.order_universe(universe, "1291", ranges, scores)
    assert got == ["acme/plausible", "acme/impossible"], (
        f"a learned preference promoted an IMPOSSIBLE repository: {got}")


def test_pick_scores_rewards_FREQUENCY_RECENCY_and_PROXIMITY_separately():
    """Each of the three terms moves the score ON ITS OWN, so a mutant that
    drops one is visible. Measured against a one-pick baseline rather than an
    absolute number, because the constants are not the contract."""
    base = MO.pick_scores([{"t": _T0 - 86400, "repo": "o/r", "n": 1291}],
                          "1291", now=_T0)["o/r"]
    twice = MO.pick_scores([{"t": _T0 - 86400, "repo": "o/r", "n": 1291}] * 2,
                           "1291", now=_T0)["o/r"]
    older = MO.pick_scores([{"t": _T0 - 86400 * 60, "repo": "o/r", "n": 1291}],
                           "1291", now=_T0)["o/r"]
    distant = MO.pick_scores([{"t": _T0 - 86400, "repo": "o/r", "n": 3}],
                             "1291", now=_T0)["o/r"]
    assert twice > base, "FREQUENCY: two picks did not outscore one"
    assert base > older, "RECENCY: a 60-day-old pick did not decay"
    assert base > distant, "PROXIMITY: a nearby number was not preferred"
    assert distant > 0, (
        "a distant number must still count as a pick — discarding it would "
        "throw away that row's frequency and recency evidence with it")


def test_a_repo_with_NO_picks_scores_nothing():
    """The cold-start half of Tier B: no log, no scores, no reordering."""
    assert MO.pick_scores([], "1291") == {}


def test_pick_scores_is_CASE_FOLDED_like_every_other_key_in_this_module():
    """GitHub names are case-insensitive and `repo_universe` preserves whatever
    casing the API returned — so a score keyed on the spelling in the LOG would
    miss the row it is about. The lesson `build_mapping` pays for at length."""
    scores = MO.pick_scores(
        [{"t": _T0, "repo": "Acme/Widget", "n": 12}], "12", now=_T0)
    assert "acme/widget" in scores, scores
    got = MO.order_universe(["acme/other", "Acme/Widget"], "12",
                            {"acme/other": 99, "acme/widget": 99}, scores)
    assert got[0] == "Acme/Widget", got


# --------------------------------------------------------------------------- #
# load_known_ranges / load_picks / record_pick — the I/O, every failure quiet
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("content,why", [
    (None, "absent"),
    ("", "empty"),
    ("{", "truncated mid-write"),
    ("[1, 2, 3]", "a LIST — the universe file handed to the wrong reader"),
    ('"a string"', "a bare JSON scalar"),
    ('{"not-a-repo": 5}', "a key that is not owner/repo"),
    ('{"o/r": "40"}', "a STRING value where a number belongs"),
    ('{"o/r": true}', "a bool, which is an int SUBCLASS in Python"),
    ('{"o/r": -1}', "a negative number"),
])
def test_every_RANGE_TABLE_failure_is_an_empty_dict(tmp_path, content, why):
    """🔴 A CORRUPTED TABLE MUST COST A WORSE ORDER, NEVER A DEAD CLICK. This
    runs on a detached handler with nowhere to print a traceback, and `{}` means
    "every repo is UNKNOWN", which is exactly the pre-change ordering.

    ⚠ `true` IS IN THE LIST ON PURPOSE: `isinstance(True, int)` is True in
    Python, so a value check written as `isinstance(v, int)` alone admits it as
    the number 1 — filing a repository as BELOW on a boolean."""
    p = tmp_path / "known_ranges.json"
    if content is not None:
        p.write_text(content)
    assert MO.load_known_ranges(p) == {}, why


def test_the_range_table_is_read_CASE_FOLDED(tmp_path):
    """Keys arrive lowercased from the generator and `order_universe` looks up
    `row.lower()` — but an older or hand-written file may not be, so the reader
    folds too rather than trusting the writer."""
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"Acme/Widget": 40}))
    assert MO.load_known_ranges(p) == {"acme/widget": 40}


def test_load_known_ranges_resolves_its_PATH_at_CALL_time(monkeypatch, tmp_path):
    """🔴 THE CLASS DEFECT THIS MODULE HAS PAID FOR TWICE. A `path: Path =
    KNOWN_RANGES_PATH` default binds at IMPORT, so a test patching the module
    attribute changes nothing and passes for the wrong reason — which is how
    `load_known_repos`' own override test was measured INERT, with the whole
    suite green after its call site was deleted."""
    p = tmp_path / "elsewhere.json"
    p.write_text(json.dumps({"o/r": 7}))
    monkeypatch.setattr(MO, "KNOWN_RANGES_PATH", p)
    assert MO.load_known_ranges() == {"o/r": 7}


def test_load_picks_SKIPS_a_torn_line_rather_than_losing_the_FILE(tmp_path):
    """🔴 THE LOG IS APPEND-ONLY AND ITS WRITER CAN BE KILLED MID-WRITE. One
    truncated last line must not cost the whole history — which is exactly what
    a whole-file `json.loads` would do."""
    p = tmp_path / "picks.jsonl"
    p.write_text(
        json.dumps({"t": _T0, "repo": "o/good", "n": 12}) + "\n"
        + '{"t": 1, "repo": "o/tor\n'          # torn
        + "not json at all\n"
        + json.dumps({"t": _T0, "repo": "o/also-good", "n": 13}) + "\n")
    got = [r["repo"] for r in MO.load_picks(p, now=_T0)]
    assert got == ["o/good", "o/also-good"], got


@pytest.mark.parametrize("row,why", [
    ({"t": _T0, "repo": "o/r"}, "no number"),
    ({"t": _T0, "n": 12}, "no repo"),
    ({"t": _T0, "repo": "not-a-repo", "n": 12}, "repo is not owner/repo"),
    ({"t": _T0, "repo": "o/r", "n": "12"}, "the number is a string"),
    ({"t": _T0, "repo": "o/r", "n": True}, "the number is a bool"),
    ({"t": _T0, "repo": "o/r", "n": 0}, "a zero reference number"),
    ({"t": "yesterday", "repo": "o/r", "n": 12}, "the timestamp is a string"),
    ([1, 2, 3], "a list where an object belongs"),
])
def test_a_MALFORMED_pick_row_is_SKIPPED(tmp_path, row, why):
    """POSITIVE CONTROL IN THE SAME READ: a good row sits beside each bad one
    and must survive, so a reader that discarded everything fails this rather
    than passing it."""
    p = tmp_path / "picks.jsonl"
    p.write_text(json.dumps(row) + "\n"
                 + json.dumps({"t": _T0, "repo": "o/keeper", "n": 5}) + "\n")
    assert [r["repo"] for r in MO.load_picks(p, now=_T0)] == ["o/keeper"], why


def test_a_pick_log_that_is_NOT_UTF8_is_an_empty_list_not_a_DEAD_CLICK(tmp_path):
    """🔴 THE ONE THAT WAS A LIVE DEFECT, AND IT IS A ONE-WORD ASYMMETRY.
    `Path.read_text()` DECODES, and a decode failure raises
    `UnicodeDecodeError` — a `ValueError`, NOT an `OSError`. Caught only by
    `guarded_main`, the click produced "mention-open failed: UnicodeDecodeError"
    and NO PICKER, while `load_picks`' own docstring said "EVERY failure is []"
    and its sibling `load_known_ranges` already caught both.

    Driven through `guarded_main` rather than `load_picks` alone, because the
    claim is about the CLICK: the operator must still get a picker."""
    p = tmp_path / "picks.jsonl"
    p.write_bytes(json.dumps({"t": _T0, "repo": "o/r", "n": 5}).encode()
                  + b"\n\xff\xfe not utf-8 at all\n")
    assert MO.load_picks(p, now=_T0) == [], (
        "a pick log that is NOT UTF-8 must read as an empty list — a "
        "UnicodeDecodeError escaping here is a DEAD CLICK")


def test_a_NON_UTF8_pick_log_still_shows_the_PICKER(monkeypatch, tmp_path):
    """The behavioural half of the test above — the guard is about the click,
    not about a return value. A corrupted accelerator must cost the operator a
    worse ORDER, never a dead end."""
    _ranges_on_disk(monkeypatch, tmp_path, {"acme/one": 9000})
    bad = tmp_path / "picks-bad.jsonl"
    bad.write_bytes(b"\xff\xfe\x00not utf-8\n")
    monkeypatch.setattr(MO, "PICKS_PATH", bad)
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: {})
    monkeypatch.setattr(MO, "load_known_universe", lambda *a, **k: ["acme/one"])
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "")
    shown = {}
    monkeypatch.setattr(MO, "pick",
                        lambda c, mesg="": shown.update(n=len(c)) or "")
    said: list = []
    monkeypatch.setattr(MO, "notify", lambda *a, **k: said.append(a))
    assert MO.guarded_main(["#1291"]) == 0
    assert shown.get("n"), f"the click showed NO picker: {said}"
    assert not said, f"the click reported a failure: {said}"


def test_load_picks_caps_by_AGE_and_by_COUNT(tmp_path):
    """Both caps, closing different ways for old data to mislead: a repository
    that was the answer three months ago may not exist now, and an unbounded
    file would be read in full on the picker path.

    ⚠ THE COUNT CAP IS APPLIED TO THE *TAIL*. `record_pick` APPENDS, so the file
    is chronological with the newest line LAST — this fixture is written the
    same way, because a fixture in the other order would have made the cap look
    correct while it kept the OLDEST rows. Taking the first N would freeze the
    preference at whatever the operator liked when the file was new."""
    p = tmp_path / "picks.jsonl"
    total = MO.PICKS_MAX_ROWS + 50
    old = _T0 - (MO.PICKS_MAX_AGE_DAYS + 5) * 86400
    rows = [{"t": old, "repo": "o/ancient", "n": 1}]
    # Oldest first, newest last — exactly what an append-only log looks like.
    rows += [{"t": _T0 - (total - i), "repo": f"o/r{i}", "n": i + 1}
             for i in range(total)]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    got = MO.load_picks(p, now=_T0)
    assert len(got) <= MO.PICKS_MAX_ROWS, len(got)
    assert not any(r["repo"] == "o/ancient" for r in got), (
        "a pick older than PICKS_MAX_AGE_DAYS survived the age cap")
    # …and the rows that DID survive are the NEWEST, not the oldest.
    assert got[-1]["repo"] == f"o/r{total - 1}", got[-1]
    assert not any(r["repo"] == "o/r0" for r in got), (
        "the OLDEST rows survived the count cap — it is being applied to the "
        "head of the file rather than the tail")


def test_record_pick_APPENDS_and_the_file_is_0600(tmp_path):
    """🔴 IT NAMES PRIVATE REPOSITORIES, so the mode is asserted."""
    p = tmp_path / "sub" / "picks.jsonl"
    assert MO.record_pick("acme/widget", "1291", p, now=_T0)
    assert MO.record_pick("acme/other", "12", p, now=_T0 + 1)
    rows = [json.loads(ln) for ln in p.read_text().splitlines()]
    assert [r["repo"] for r in rows] == ["acme/widget", "acme/other"]
    assert rows[0]["n"] == 1291 and isinstance(rows[0]["n"], int)
    assert oct(os.stat(p).st_mode)[-3:] == "600", oct(os.stat(p).st_mode)
    assert oct(os.stat(p.parent).st_mode)[-3:] == "700"


def test_the_pick_log_is_COMPACTED_so_it_cannot_grow_without_bound(
        tmp_path, monkeypatch):
    """🔴 NOTHING ELSE TRIMS IT. `record_pick` only appends and the age cap is
    applied on READ, so without this a 0600 file naming private repositories
    accumulates forever — and `PICKS_MAX_ROWS`' comment claimed the count cap
    prevented that, which an audit measured false: `load_picks` reads the whole
    file and only then slices.

    Asserted in BOTH directions, because a compaction that fired on every write
    would be a write amplification and one that never fired would be no guard:
    below the threshold the file is untouched, above it the TAIL survives."""
    made = _watch_compaction_tmps(monkeypatch)
    p = tmp_path / "picks.jsonl"
    # One short, so the append lands the file EXACTLY on the threshold.
    rows = [{"t": _T0 - (5000 - i), "repo": f"o/r{i}", "n": i + 1}
            for i in range(MO.PICKS_COMPACT_AT - 1)]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    # AT the threshold: untouched. (`<=` vs `<` is the mutation a one-sided
    # test cannot see, which is why this arm exists at all.)
    before = p.read_text()
    assert MO.record_pick("o/newest", "42", p, now=_T0)
    assert len(p.read_text().splitlines()) == MO.PICKS_COMPACT_AT, (
        "the log was trimmed AT the threshold rather than past it")
    assert p.read_text().startswith(before), "existing rows were rewritten early"

    # PAST it: trimmed to the most recent PICKS_MAX_ROWS, newest kept.
    assert MO.record_pick("o/newer-still", "43", p, now=_T0 + 1)
    kept = [json.loads(ln) for ln in p.read_text().splitlines()]
    assert len(kept) == MO.PICKS_MAX_ROWS, len(kept)
    assert kept[-1]["repo"] == "o/newer-still", kept[-1]
    assert not any(r["repo"] == "o/r0" for r in kept), (
        "compaction kept the HEAD of the file — it must keep the tail")
    assert oct(os.stat(p).st_mode)[-3:] == "600", "compaction widened the mode"
    # …and nothing was left behind — see `_watch_compaction_tmps` for why this
    # observes `mkstemp` rather than listing the directory, and why asserting on
    # a GUESSED tmp name (the old `picks.jsonl.tmp`) could never have failed.
    assert made, "POSITIVE CONTROL: no compaction tmp was ever created"
    assert [t for t in made if t.exists()] == [], (
        f"a compaction tmp outlived the call: {[str(t) for t in made]}")


def _watch_compaction_tmps(monkeypatch):
    """A list that collects every tmp path compaction creates.

    🔴 NOT A DIRECTORY LISTING, AND THAT IS FORCED RATHER THAN CHOSEN. The tmp
    name comes from `mkstemp` and is unguessable, so the obvious check is to
    list the directory — but the glob-site ledger in `test_transcript_search.py`
    enumerates every listing in a scope whose source mentions the log's
    extension, and MEASURED: an inline listing in the caller registered as a new
    walk site, and so did a helper that merely explained itself using the
    literal. Rather than widen a repo-wide disclosure ledger for a two-line test
    assertion, this observes `mkstemp`'s real return value — which is a tighter
    check anyway: it names the exact files the code under test created, instead
    of inferring them from what is left lying around.
    """
    import tempfile as _tempfile
    made: list[Path] = []
    real = _tempfile.mkstemp

    def spy(*a, **k):
        fd, name = real(*a, **k)
        made.append(Path(name))
        return fd, name

    monkeypatch.setattr(_tempfile, "mkstemp", spy)
    # ⚠ A PLAIN FUNCTION, NOT A CONTEXT MANAGER. It was a
    # `@contextlib.contextmanager` entered through a bare `ExitStack()`
    # that nothing ever closed — so the generator never resumed past its
    # `yield` and teardown happened only because `monkeypatch` undid the
    # setattr anyway. `monkeypatch` owning the teardown is the whole
    # reason no context manager is needed.
    return made


def _lock_holder(path: Path, seconds: float):
    """A REAL second process holding `LOCK_EX` on `path`. Returns once held."""
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import fcntl,os,sys,time;"
         "fd=os.open(sys.argv[1], os.O_RDONLY);"
         "fcntl.flock(fd, fcntl.LOCK_EX);"
         "print('held', flush=True); time.sleep(float(sys.argv[2]))",
         str(path), str(seconds)],
        stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "held", "the holder never ran"
    return proc


def test_the_APPEND_takes_the_lock_too(tmp_path):
    """🔴 THE RACE THE LOCK EXISTS FOR, AND THE FIRST VERSION DID NOT CLOSE IT.
    Round 2 locked only `_compact_picks`, so it excluded two COMPACTIONS from
    each other and left APPEND-vs-compaction — the race that was actually
    measured — wide open, under a docstring claiming it was fixed. A round-3
    audit caught that, and I reproduced it: `record_pick` returned True and its
    row landed on disk while a second process held `LOCK_EX`.

    So the append takes the lock as well.

    🔴 A DIFFERENTIAL MEASUREMENT, NOT AN ABSOLUTE THRESHOLD — and that is a
    flake this test already had. Its first version asserted `waited > 20ms`,
    which is inside the noise of a box running dozens of concurrent suites: the
    mutant that removes the lock entirely SURVIVED one battery run and was
    KILLED by the next. So the wait is compared against an UNLOCKED append
    timed in the SAME run, and against the budget itself — both of which move
    with the load rather than against it."""
    log = tmp_path / "picks.jsonl"
    log.write_text(json.dumps({"t": _T0, "repo": "o/seed", "n": 1}) + "\n")

    # BASELINE: the same append with nobody holding the lock.
    start = time.monotonic()
    assert MO.record_pick("o/baseline", "6", log) is True
    baseline = time.monotonic() - start

    holder = _lock_holder(log, 3)
    try:
        start = time.monotonic()
        assert MO.record_pick("o/waited", "7", log) is True
        waited = time.monotonic() - start
    finally:
        holder.terminate()
        holder.wait(timeout=10)
        holder.stdout.close()
    assert waited >= MO.PICKS_LOCK_WAIT_S * 0.7, (
        f"the append returned in {waited*1000:.0f} ms against a HELD lock, "
        f"well under its {MO.PICKS_LOCK_WAIT_S*1000:.0f} ms budget — it is not "
        f"taking the lock at all (unlocked baseline was "
        f"{baseline*1000:.0f} ms)")
    assert waited > baseline * 3, (
        f"the held append ({waited*1000:.0f} ms) is not materially slower than "
        f"the unlocked one ({baseline*1000:.0f} ms)")
    assert "o/waited" in log.read_text()


def test_a_filesystem_that_CANNOT_LOCK_does_not_burn_the_budget(tmp_path,
                                                                monkeypatch):
    """🔴 THE errno FILTER, WHICH SHIPPED WITH NO TEST AND NO BATTERY ROW — a
    round-5 audit deleted the three-line guard outright and the suite reported
    356 passed, 0 failed.

    Retrying on EVERY `OSError` treats a PERMANENT refusal as contention: a
    filesystem with no lock daemon (NFS without `rpc.statd`, some container
    overlays) answers `ENOLCK`/`EINVAL` and never stops answering it, so every
    single click burns the whole `PICKS_LOCK_WAIT_S` budget waiting for a lock
    it can never have. MEASURED before the filter: 204 ms per `record_pick`;
    after: 0.1 ms, one `flock` attempt.

    Differential, like its sibling lock tests — an absolute millisecond
    threshold is inside the noise of a loaded box."""
    import fcntl
    calls: list = []

    def refuses(fd, flags):
        calls.append(flags)
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(fcntl, "flock", refuses)
    log = tmp_path / "picks.jsonl"
    start = time.monotonic()
    assert MO.record_pick("acme/widget", "12", log) is True, (
        "an unlockable filesystem dropped the pick")
    spent = time.monotonic() - start
    assert calls, "POSITIVE CONTROL: flock was never attempted at all"
    assert len(calls) == 1, (
        f"a PERMANENT lock refusal was retried {len(calls)} times — the errno "
        f"filter is gone, and every click now burns the whole budget")
    assert spent < MO.PICKS_LOCK_WAIT_S * 0.5, (
        f"an unlockable filesystem cost {spent*1000:.0f} ms of the "
        f"{MO.PICKS_LOCK_WAIT_S*1000:.0f} ms budget")
    assert "acme/widget" in log.read_text(), "the row did not land"


def test_a_CONTENTION_errno_IS_still_retried(tmp_path, monkeypatch):
    """The negative control on the filter: narrowing it must not turn a real
    contention answer into an immediate give-up, or the lock stops excluding
    anything. `EWOULDBLOCK` is what a held `LOCK_NB` actually returns."""
    import fcntl
    calls: list = []

    def busy(fd, flags):
        calls.append(flags)
        raise OSError(errno.EWOULDBLOCK, "Resource temporarily unavailable")

    monkeypatch.setattr(fcntl, "flock", busy)
    log = tmp_path / "picks.jsonl"
    assert MO.record_pick("acme/widget", "12", log) is True
    assert len(calls) > 1, (
        f"a CONTENTION errno was not retried ({len(calls)} attempt) — the "
        f"filter is too narrow and the budget buys nothing")


def test_the_APPENDS_wait_is_BOUNDED_and_the_row_still_lands(tmp_path):
    """🔴 A DETACHED CLICK HANDLER MUST NOT BE ABLE TO HANG ON A LOCK, and the
    textbook answer — a blocking `LOCK_EX` — is exactly what would. It is not
    hypothetical: a blocking version measurably DEADLOCKED a probe of this
    function within one process, and in production a wedged holder would hang
    the click invisibly and forever, costing the operator the OPEN to save one
    learning row.

    So the wait is a BUDGET. Against a hold far longer than it, the append gives
    up and writes anyway — bounded, and the pick is never lost to the lock."""
    log = tmp_path / "picks.jsonl"
    log.write_text(json.dumps({"t": _T0, "repo": "o/seed", "n": 1}) + "\n")
    holder = _lock_holder(log, 30)
    try:
        start = time.monotonic()
        assert MO.record_pick("o/gave-up", "8", log) is True
        waited = time.monotonic() - start
    finally:
        holder.terminate()
        holder.wait(timeout=10)
        holder.stdout.close()
    assert waited < MO.PICKS_LOCK_WAIT_S * 5, (
        f"the append waited {waited:.2f}s against a 30s hold — the budget is "
        f"not bounding it, and a detached click can hang")
    assert "o/gave-up" in log.read_text(), (
        "the append gave up on the lock AND dropped the pick — the budget must "
        "cost exclusion, never the row")


def test_two_compactions_do_not_DELETE_each_others_tmp(tmp_path):
    """🔴 A BUG MY OWN ROUND-2 FIX INTRODUCED, found by tracing a probe that
    unexpectedly did NOT reproduce the race it was written for.

    The tmp was named `picks.jsonl.<pid>.tmp`, which collides on RE-ENTRY within
    one process. A nested `_compact_picks` skipped on the lock, as designed —
    and its `finally` then UNLINKED THE OUTER CALL'S TMP, so the outer
    `os.replace` raised `FileNotFoundError` into the swallowing handler and the
    compaction silently did nothing at all. Measured: the log stayed at 1011
    rows where it should have been trimmed to 500.

    `mkstemp` gives a name no other call can guess, and the cleanup only removes
    a tmp still present after a FAILED replace."""
    log = tmp_path / "picks.jsonl"
    rows = [{"t": _T0 + i, "repo": f"o/r{i}", "n": i + 1}
            for i in range(MO.PICKS_COMPACT_AT + 10)]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    real_replace = os.replace
    nested: list = []

    def replace_with_a_nested_compaction(a, b, *args, **kw):
        # Exactly the shape that destroyed the outer tmp: a second call runs
        # inside the first one's read->replace window.
        nested.append(MO._compact_picks(log))
        return real_replace(a, b, *args, **kw)

    monkey = MO.os.replace
    MO.os.replace = replace_with_a_nested_compaction
    try:
        MO._compact_picks(log)
    finally:
        MO.os.replace = monkey
    assert nested, "POSITIVE CONTROL: the nested compaction never ran"
    assert len(log.read_text().splitlines()) == MO.PICKS_MAX_ROWS, (
        "the OUTER compaction silently did nothing — a nested call deleted its "
        "tmp out from under it")


def test_a_row_appended_DURING_a_compaction_is_CARRIED_OVER(tmp_path,
                                                            monkeypatch):
    """🔴 THE RACE ITSELF, AND I CLAIMED IT CLOSED TWICE BEFORE IT WAS.

    Round 2 locked only `_compact_picks` (excludes compactions from each other,
    not the append). Round 3 shared the lock with the append — but that is a
    BUDGET, so a compaction slower than `PICKS_LOCK_WAIT_S` still had the
    appender write UNLOCKED onto the file about to be replaced; round 4 measured
    `record_pick` returning True, the row on disk, and gone after the replace.
    What closes it is the CARRY-OVER: everything past the byte offset the
    compaction read is copied into the tmp before the replace.

    ⚠ THE FIRST PROBE FOR THIS COULD NOT SEE THE FIX, and that is why this test
    drives the REAL function. It ran a hand-written "compactor" in a second
    process, so it tested a fake and stayed red no matter what `_compact_picks`
    did. Here the append is injected at `mkstemp`, which is exactly where a
    budget-expired appender lands: after the initial read, before the carry-over
    read.

    ⚠ IT COVERS ONE POINT IN THAT WINDOW, NOT THE WINDOW. An earlier wording said
    "exactly where a budget-expired appender lands", which is wider than this
    test. The complement — a `write(2)` landing AFTER the carry-over read — is a
    measured RESIDUAL and is documented on `_compact_picks`, not fixed here."""
    log = tmp_path / "picks.jsonl"
    log.write_text("\n".join(
        json.dumps({"t": _T0 + i, "repo": f"o/r{i}", "n": i + 1})
        for i in range(MO.PICKS_COMPACT_AT + 10)) + "\n")

    import tempfile as _tempfile
    real_mkstemp = _tempfile.mkstemp
    fired: list = []

    def inject(*a, **k):
        # An UNLOCKED append, exactly as a budget-expired `record_pick` makes.
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": _T0 + 9999, "repo": "o/late",
                                 "n": 4242}) + "\n")
        fired.append(1)
        return real_mkstemp(*a, **k)

    monkeypatch.setattr(_tempfile, "mkstemp", inject)
    MO._compact_picks(log)
    assert fired, "POSITIVE CONTROL: the compaction never reached mkstemp"
    rows = [json.loads(ln) for ln in log.read_text().splitlines()]
    assert any(r["repo"] == "o/late" for r in rows), (
        "a row appended during the compaction window was DESTROYED by the "
        "replace — the carry-over is not covering it")
    # …and it is at the END, where an append belongs.
    assert rows[-1]["repo"] == "o/late", rows[-1]
    # …and the compaction still did its job.
    assert len(rows) == MO.PICKS_MAX_ROWS + 1, len(rows)


def test_the_MEASURED_orderings_of_an_append_vs_a_compaction(tmp_path):
    """🔴 THE RESIDUAL TABLE IN `_compact_picks`, MACHINE-CHECKED. FOUR
    successive wordings of that paragraph were wrong, in BOTH directions, so the
    orderings are asserted rather than described:

        fd open before the carry-over read, write before it  -> survives
        fd open before `os.replace`, write after the read    -> LOST WHOLE
        fd opened AFTER `os.replace`                         -> survives

    🔴 THE THIRD ROW IS THE ONE THAT KEEPS BEING GOT WRONG, and it is why this
    test exists rather than a sentence. The wording before it said the loser was
    "any write(2) after the carry-over read" — but an fd opened after the
    replace writes to the NEW inode and survives however late. The discriminator
    is WHEN THE FD WAS OPENED.

    ⚠ WHAT IS PINNED IS THE TABLE, NOT A PROMISE. The losing row costs one
    learning row and no click, and `load_picks` must stay readable throughout —
    the property that actually matters, asserted last."""
    row = json.dumps({"t": _T0 + 9999, "repo": "o/late", "n": 4242}) + "\n"

    def seeded():
        p = tmp_path / f"picks{len(list(tmp_path.iterdir()))}.jsonl"
        p.write_text("\n".join(
            json.dumps({"t": _T0 + i, "repo": f"o/r{i}", "n": i + 1})
            for i in range(MO.PICKS_COMPACT_AT + 10)) + "\n")
        return p

    # (1) BEFORE the carry-over read — via an fd opened and written up front.
    log = seeded()
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(row)
    MO._compact_picks(log)
    assert "o/late" in log.read_text(), (
        "a write that COMPLETED before the carry-over read was lost — the "
        "carry-over is not working")

    # (2) ENTIRELY AFTER the replace, through an fd opened beforehand. Nothing
    # interleaves; this is the shape a budget-expired record_pick produces.
    log = seeded()
    fh = open(log, "a", encoding="utf-8")
    MO._compact_picks(log)
    fh.write(row)
    fh.close()
    assert "o/late" not in log.read_text(), (
        "the table says this ordering LOSES the row; it survived, so the "
        "residual paragraph is now wrong in the other direction")

    # (3) 🔴 THE ROW THE PARAGRAPH KEPT GETTING WRONG: an fd opened AFTER the
    # replace writes to the NEW inode and SURVIVES, however late it is. Without
    # this case the losing condition reads as "any write after the carry-over
    # read", which is what four wordings said and which is false.
    log = seeded()
    MO._compact_picks(log)
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(row)
    assert "o/late" in log.read_text(), (
        "an fd opened AFTER the replace lost its row — then the losing "
        "condition really is 'any write after the read' and the table is wrong")

    # 🔴 THE PROPERTY THAT ACTUALLY MATTERS, in every ordering: the reader
    # survives. A lost row costs the learning and nothing else.
    for p in (log,):
        assert isinstance(MO.load_picks(p), list)

    # ⚠ AND `record_pick` CANNOT STRADDLE THE READ, which is why the loss is
    # always WHOLE rather than a torn line for THIS writer: it writes one row
    # through one buffered `write` and flushes at close. A straddling writer
    # would tear — measured with a hand-rolled two-`write(2)` append — so the
    # claim is about the writer, not about the filesystem.
    probe = seeded()
    real_write, calls = os.write, []
    os.write = lambda fd, b, *a, **k: (calls.append(len(b)),
                                       real_write(fd, b))[1]
    try:
        MO.record_pick("gardenersguild/trowelcast", "1291", probe)
    finally:
        os.write = real_write
    assert len(calls) <= 1, (
        f"record_pick made {len(calls)} write(2) calls for one row — it can "
        f"now STRADDLE a carry-over read, so the loss is no longer always "
        f"whole and `_compact_picks`' table needs a torn row")


def test_compaction_SKIPS_while_another_writer_holds_the_lock(tmp_path,
                                                              monkeypatch):
    """🔴 ONE CLICK IS ONE PROCESS, SO TWO CLICKS ARE TWO WRITERS — and without
    the lock a row appended between the read and the `replace` is silently LOST.
    A round-2 audit measured exactly that ("concurrent row survived? False").

    The lock is `LOCK_NB` and SKIPS rather than waits, because blocking here
    would cost the click while losing a row only costs a little learning. This
    drives a REAL second process holding a REAL flock, because the claim is
    about two processes and an in-process fake could not make it.

    ⚠ The tmp name is unguessable (`mkstemp`), so two compactions cannot share
    one buffer even on a filesystem that ignores the advisory lock. That belt is
    asserted at the end: nothing is left behind."""
    made = _watch_compaction_tmps(monkeypatch)
    log = tmp_path / "picks.jsonl"
    rows = [{"t": _T0 + i, "repo": f"o/r{i}", "n": i + 1}
            for i in range(MO.PICKS_COMPACT_AT + 10)]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import fcntl,os,sys,time;"
         "fd=os.open(sys.argv[1], os.O_RDONLY);"
         "fcntl.flock(fd, fcntl.LOCK_EX);"
         "print('held', flush=True); time.sleep(6)", str(log)],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held", "the holder never ran"
        before = len(log.read_text().splitlines())
        MO._compact_picks(log)
        assert len(log.read_text().splitlines()) == before, (
            "compaction ran while another writer held the lock — a row "
            "appended in that window is silently lost")
    finally:
        holder.terminate()
        holder.wait(timeout=10)
        if holder.stdout:
            holder.stdout.close()
    # POSITIVE CONTROL: with the lock free it DOES compact, so the assertion
    # above is about the lock and not about compaction being broken.
    MO._compact_picks(log)
    assert len(log.read_text().splitlines()) == MO.PICKS_MAX_ROWS
    # ⚠ THE EXACT TMP PATH, NOT A GLOB OR A LISTING. Two reasons: the autouse
    # redirect writes its own fixture files into this same `tmp_path`, so a bare
    # listing is not a statement about compaction at all; and a `*.jsonl` glob
    # here registers as a new walk site against
    # `test_transcript_search.py::test_the_jsonl_glob_site_ledger_is_pinned_two_way`
    # — measured, it went red.
    # ⚠ OBSERVED, NOT GUESSED. The tmp used to be `picks.jsonl.<pid>.tmp` and is
    # now an unguessable `mkstemp` name, so an assertion naming either exact
    # form is one that can never fail. See `_watch_compaction_tmps`.
    assert made, "POSITIVE CONTROL: no compaction tmp was ever created"
    assert [t for t in made if t.exists()] == [], (
        f"a tmp outlived the compaction: {[str(t) for t in made]}")


def test_a_RAISING_compaction_neither_LOSES_the_pick_nor_ESCAPES(tmp_path,
                                                                  monkeypatch):
    """🔴 THREE CLAIMS, AND THE FIRST VERSION OF THIS TEST PINNED THE OPPOSITE
    OF ONE OF THEM. It wrapped the call in `pytest.raises(AssertionError)` —
    asserting that `record_pick` PROPAGATES — while `record_pick`'s own
    docstring says "IT CAN NEVER RAISE AND IT CAN NEVER BLOCK THE OPEN". A
    round-2 audit caught it: a test made to pass for a new wrong reason, and the
    escape it pinned would have skipped `open_url` entirely, turning the click
    into "mention-open failed" with no browser.

    So: the row is DURABLE before compaction runs, the raise does NOT escape,
    and the return is still True — a compaction failure costs a large file, not
    the operator's pick."""
    p = tmp_path / "picks.jsonl"
    rows = [{"t": _T0, "repo": f"o/r{i}", "n": i + 1}
            for i in range(MO.PICKS_COMPACT_AT + 5)]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    fired: list = []
    monkeypatch.setattr(
        MO, "_compact_picks",
        lambda *a, **k: fired.append(1) or (_ for _ in ()).throw(
            RuntimeError("compaction exploded")))
    assert MO.record_pick("o/kept", "9", p, now=_T0) is True, (
        "a failed compaction flipped the return to False — the pick WAS "
        "recorded")
    assert fired, "POSITIVE CONTROL: compaction was never attempted"
    assert json.loads(p.read_text().splitlines()[-1])["repo"] == "o/kept", (
        "the row was not durable before compaction ran")


def test_a_RAISING_compaction_does_not_cost_the_OPEN(monkeypatch):
    """The behavioural half of the test above, at the call site that matters:
    `record_pick` runs immediately before `return open_url(url)`."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: {})
    monkeypatch.setattr(MO, "load_known_universe", lambda *a, **k: ["acme/one"])
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "")
    monkeypatch.setattr(MO, "_compact_picks",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("compaction exploded")))
    opened: list = []
    said: list = []
    monkeypatch.setattr(MO, "open_url", lambda url: opened.append(url) or 0)
    monkeypatch.setattr(MO, "notify", lambda *a, **k: said.append(a))
    monkeypatch.setattr(MO, "pick",
                        lambda c, mesg="": next(x["url"] for x in c
                                                if "acme/one" in x["url"]))
    assert MO.guarded_main(["#1291"]) == 0
    assert opened, f"the click opened NOTHING because compaction raised: {said}"
    assert not said, f"the click reported a failure: {said}"


def test_record_pick_NARROWS_a_pre_existing_parent_that_others_can_read(tmp_path):
    """🔴 THE HALF THAT WAS ONLY CLAIMED. `Path.mkdir(mode=…, exist_ok=True)`
    applies the mode ONLY at creation, so a pre-existing
    `~/.config/mention-open/` keeps whatever mode it has — while the docstring
    paired the parent with the file mode, which IS re-applied every write. The
    original test created its parent fresh and so could not see this."""
    d = tmp_path / "preexisting"
    d.mkdir()
    os.chmod(d, 0o755)
    assert MO.record_pick("acme/widget", "12", d / "picks.jsonl")
    assert oct(os.stat(d).st_mode)[-3:] == "700", (
        "a pre-existing group/world-readable config dir was left that way, and "
        "it holds files naming PRIVATE repositories")


def test_record_pick_does_NOT_WIDEN_a_parent_the_operator_narrowed(tmp_path):
    """🔴 THE OTHER DIRECTION, AND IT IS WHY THE FIX IS "STRIP GROUP/OTHER"
    RATHER THAN "SET 0700". A flat `chmod(0o700)` closes the hole above and
    opens a worse one: it re-grants owner-write to a directory made read-only on
    purpose. Measured — it made
    `test_record_pick_CANNOT_RAISE_when_the_log_is_UNWRITABLE` pass by removing
    the condition that test exists to exercise.

    Owner bits must survive untouched; only group and other are stripped."""
    d = tmp_path / "narrowed"
    d.mkdir()
    os.chmod(d, 0o500)                    # r-x------ : owner cannot write
    try:
        MO.record_pick("acme/widget", "12", d / "picks.jsonl")
        assert oct(os.stat(d).st_mode)[-3:] == "500", (
            "record_pick WIDENED a directory the operator had narrowed")
    finally:
        os.chmod(d, 0o700)
    # …and a mode with no group/other bits at all is left completely alone.
    e = tmp_path / "already-fine"
    e.mkdir()
    os.chmod(e, 0o700)
    assert MO.record_pick("acme/widget", "12", e / "picks.jsonl")
    assert oct(os.stat(e).st_mode)[-3:] == "700"


def _narrow_would_set(module, start: int) -> int | None:
    """What `module.narrow_dir` would chmod a directory of mode `start` TO —
    with `os.stat`/`os.chmod` stubbed, so no privileged bit has to be settable.

    🔴 STUBBED ON PURPOSE, AND THE REASON IS A TWO-TIER DIFFERENCE THAT COST A
    RED GATE. The first version of the setuid/setgid/sticky test called
    `os.chmod(d, 0o2755)` for real. That works on the dev host and raises
    `PermissionError: Operation not permitted` in the `nix build` sandbox, where
    the build user may not set those bits — so the dev-host tier was green and
    the authoritative tier failed, which is exactly the blindness `CLAUDE.md`
    describes. A `skip` would have been worse: it is an UNPINNED skip, and it
    would have made the sandbox tier silently stop testing this at all.

    The property under test is mask ARITHMETIC (`& 0o7777` vs `& 0o777`), which
    needs no privilege to check.
    """
    got: list[int] = []
    real_stat, real_chmod = module.os.stat, module.os.chmod

    class _St:
        st_mode = start

    module.os.stat = lambda *a, **k: _St()
    module.os.chmod = lambda p, m, *a, **k: got.append(m)
    try:
        module.narrow_dir(Path("/nonexistent-does-not-matter"))
    finally:
        module.os.stat, module.os.chmod = real_stat, real_chmod
    return got[0] if got else None


@pytest.mark.parametrize("start,keeps", [
    (0o2755, 0o2000),   # setgid — common on shared dirs, and not ours to clear
    (0o1777, 0o1000),   # sticky
    (0o4755, 0o4000),   # setuid
])
def test_narrowing_a_parent_PRESERVES_setuid_setgid_and_sticky(start, keeps):
    """🔴 THE BITS A `& 0o777` MASK SILENTLY DESTROYS. A round-2 audit measured
    a `0o2755` parent coming back `0o0700` and a `0o1777` one likewise: the
    narrowing read only the low nine bits, so re-writing them cleared everything
    above. Those bits are not ours to clear — the rule is "nobody ELSE may read
    this", which is a statement about group and other alone.

    ⚠ ASSERTED ON THE FULL MODE, because the sibling test above compares
    `oct(...)[-3:]` and is therefore structurally blind to exactly this — which
    is why it did not catch it. And driven through `_narrow_would_set`, which
    explains why this cannot use a real `chmod`."""
    mode = _narrow_would_set(MO, start)
    assert mode is not None, f"narrow_dir did not chmod {start:o} at all"
    assert mode & 0o077 == 0, f"group/other survived: {mode:o}"
    assert mode & 0o7000 == keeps, (
        f"narrowing destroyed a non-permission bit: {start:o} -> {mode:o}")
    assert mode & 0o700 == start & 0o700, (
        f"owner bits changed: {start:o} -> {mode:o}")


def test_narrowing_a_parent_that_is_ALREADY_private_does_not_chmod_at_all():
    """The negative control on the stub above: a mode with no group/other bits
    needs no call, so `narrow_dir` must make none. Without this, a mutant that
    chmod-ed unconditionally would satisfy every assertion in the sibling
    test."""
    assert _narrow_would_set(MO, 0o700) is None
    assert _narrow_would_set(MO, 0o500) is None
    assert _narrow_would_set(MO, 0o2700) is None


def test_a_parent_this_tool_CANNOT_chmod_does_not_cost_the_PICK(tmp_path,
                                                                monkeypatch):
    """🔴 A REGRESSION THE NARROWING ITSELF INTRODUCED, and it is reachable
    through a redirected `MENTION_OPEN_PICKS`. The chmod started out inside the
    same `try` as the append, so an `EPERM` on a directory this tool does not
    own — `/tmp`, say — propagated into `record_pick`'s handler and DROPPED THE
    PICK, on a path that worked before the narrowing existed.

    The write must still land; the narrowing is defence in depth, not a
    precondition."""
    d = tmp_path / "not-ours"
    d.mkdir()
    os.chmod(d, 0o755)
    monkeypatch.setattr(MO.os, "chmod", _raise_eperm(MO.os.chmod, d))
    assert MO.record_pick("acme/widget", "12", d / "picks.jsonl") is True, (
        "an un-chmod-able parent dropped the pick")
    assert (d / "picks.jsonl").exists()


def _raise_eperm(real, only_for: Path):
    """`os.chmod` that refuses for one directory and behaves for everything
    else — so the file's own 0600 still happens and the test is about the
    PARENT."""
    def chmod(p, mode, *a, **k):
        if str(p) == str(only_for):
            raise PermissionError("not ours to chmod")
        return real(p, mode, *a, **k)
    return chmod


def test_record_pick_RE_APPLIES_the_mode_to_a_file_it_did_NOT_create(tmp_path):
    """🔴 THE CASE `open(..., "a")` SILENTLY LEAVES OPEN. A log created by an
    older build, a hand-run, or a permissive umask keeps its original mode
    FOREVER unless the mode is re-applied on every write."""
    p = tmp_path / "picks.jsonl"
    p.write_text("")
    os.chmod(p, 0o644)
    assert MO.record_pick("acme/widget", "12", p)
    assert oct(os.stat(p).st_mode)[-3:] == "600", (
        "a pre-existing world-readable pick log was left world-readable")


@pytest.mark.parametrize("repo,num", [
    ("", "12"), ("not-a-repo", "12"), ("a/b/c", "12"), (None, "12"),
    ("o/r", ""), ("o/r", "abc"), ("o/r", "0"), ("o/r", None),
])
def test_record_pick_REFUSES_what_the_READER_would_discard(tmp_path, repo, num):
    """A row the reader throws away is not worth writing, and a log full of them
    would push real history past `PICKS_MAX_ROWS`."""
    p = tmp_path / "picks.jsonl"
    assert MO.record_pick(repo, num, p) is False
    assert not p.exists(), p.read_text() if p.exists() else ""


def test_record_pick_CANNOT_RAISE_when_the_log_is_UNWRITABLE(tmp_path):
    """🔴 A FULL DISK MUST COST THE LEARNING, NOT THE CLICK. This runs after the
    operator has already chosen a URL; every OSError is a quiet False."""
    d = tmp_path / "readonly"
    d.mkdir()
    os.chmod(d, 0o500)
    try:
        assert MO.record_pick("acme/widget", "12", d / "picks.jsonl") is False
    finally:
        os.chmod(d, 0o700)


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/acme/widget/issues/12", "acme/widget"),
    ("https://github.com/acme/widget/pull/1291", "acme/widget"),
    ("http://github.com/acme/widget/pull/1", "acme/widget"),
    ("https://www.github.com/acme/widget/pull/1", "acme/widget"),
    # NOT a repository — these must never be recorded as one.
    ("https://clawgate.zacx.dev/tasks/370", ""),
    ("https://app.clickup.com/t/868abc123", ""),
    ("https://github.com/acme", ""),
    ("https://example.com/acme/widget/issues/1", ""),
    ("", ""),
    (None, ""),
])
def test_repo_of_github_url(url, expected):
    assert MO.repo_of_github_url(url) == expected


# --------------------------------------------------------------------------- #
# ordering_state / ordering_note — the DEADMAN, and what the operator sees
# --------------------------------------------------------------------------- #
def test_a_STALE_range_table_is_IGNORED_rather_than_TRUSTED():
    """🔴 THE DEADMAN. Past `STALE_MAPPING_DAYS` the daily refresh has missed
    SEVERAL runs, and the two misclassifications that follow point in OPPOSITE
    directions — a repo that has advanced past `N` is filed BELOW, and a repo
    that gained its first reference is filed IMPOSSIBLE. Ordering on that is
    confidently wrong; ordering on nothing is merely what shipped before.

    Measured AT the boundary and either side, because `>=` vs `>` is the
    mutation a single-point test cannot see."""
    ranges = {"o/r": 40}
    for age, want in ((1.0, MO.ORDER_APPLIED),
                      (MO.STALE_MAPPING_DAYS - 0.1, MO.ORDER_APPLIED),
                      (MO.STALE_MAPPING_DAYS, MO.ORDER_STALE),
                      (MO.STALE_MAPPING_DAYS + 5, MO.ORDER_STALE)):
        assert MO.ordering_state(ranges, age) == want, (
            f"a STALE table is being TRUSTED (or a fresh one distrusted): "
            f"age {age} gave {MO.ordering_state(ranges, age)!r}, want "
            f"{want!r}")


def test_an_ABSENT_table_is_no_table_and_an_UNDATEABLE_one_is_STALE():
    """Three states, three different next moves for the operator: run the
    generator, look at the failing unit, or nothing at all. Collapsing them into
    a boolean is the silent-zero shape this handler is written against."""
    assert MO.ordering_state({}, 1.0) == MO.ORDER_NO_TABLE
    assert MO.ordering_state({}, None) == MO.ORDER_NO_TABLE
    # A table that EXISTS but cannot be dated is STALE, not missing — sending
    # the operator to run a generator would not help when the file is there.
    assert MO.ordering_state({"o/r": 1}, None) == MO.ORDER_STALE


def test_the_ORDER_STATE_set_is_pinned_and_EVERY_ONE_has_a_note():
    """🔴 TWO-WAY, AND IT CHECKS THE CONSUMER RATHER THAN THE LEDGER. A fourth
    state added later with no wording would fall through `ordering_note`'s final
    `return ""` and the operator would see an unordered picker described as
    nothing at all — the silent `else` this file has already been bitten by
    once, in `pick()`'s outcome handling."""
    assert MO.ORDER_STATES == (MO.ORDER_APPLIED, MO.ORDER_NO_TABLE,
                               MO.ORDER_STALE)
    assert len(set(MO.ORDER_STATES)) == 3
    for state in MO.ORDER_STATES:
        assert MO.ordering_note("1291", state, 2, 54, age=9.0), (
            f"the state {state!r} produces no wording at all")
    assert MO.ordering_note("1291", "a-state-nobody-defined") == ""


def test_the_ordering_note_SAYS_WHICH_STATE_and_names_NO_REPOSITORY():
    """🔴 THE OPERATOR MUST BE ABLE TO TELL AN ORDERED LIST FROM AN UNORDERED
    ONE — they look identical and only one is worth trusting.

    And the note reaches the picker header, so it obeys the same rule as
    `universe_note` and `guessed_note`: a clicked number and two counts, NEVER a
    row. BOTH token guards, because `_no_universe_token_anywhere` is
    structurally blind to the pane-guessed repo."""
    applied = MO.ordering_note("1291", MO.ORDER_APPLIED, 2, 54)
    assert "1291" in applied and "2" in applied and "54" in applied
    stale = MO.ordering_note("1291", MO.ORDER_STALE, age=9.0)
    assert "unordered" in stale and "9 days" in stale
    assert "mention-known-repos-refresh" in stale, (
        "the stale wording must name the UNIT — that is the thing to look at, "
        "and a stale table is not fixed by editing a file")
    missing = MO.ordering_note("1291", MO.ORDER_NO_TABLE)
    assert "unordered" in missing and "regen-known-repos.py" in missing
    # 🔴 THE QUIET/LOUD SPLIT IS ASSERTED, NOT JUST DESCRIBED. `no-table` fires
    # on EVERY picker until the generator has run once — and forever on a host
    # where `gh` is not configured, which is a legitimate state — so it must
    # stay the shorter of the two and must NOT send the operator to a unit that
    # is working fine.
    assert len(missing) < len(stale), (missing, stale)
    assert "systemctl" not in missing, (
        "the no-table wording points at a UNIT, which on a host that has simply "
        "never run the generator is a diagnosis pointing somewhere already fine")
    for note in (applied, stale, missing):
        _no_universe_token_anywhere(note, "ORDERING NOTE")
        for token in PANE_GUESS_TOKENS:
            assert token not in note, f"the ordering note named {token!r}"


# --------------------------------------------------------------------------- #
# END TO END through main()
# --------------------------------------------------------------------------- #
def _ranges_on_disk(monkeypatch, tmp_path, table, age_days=1.0):
    """Write a real range table and point the handler at it, with a real mtime —
    the staleness arm reads `st_mtime`, so a stubbed loader could not reach it."""
    p = tmp_path / "ranges-for-this-test.json"
    p.write_text(json.dumps(table))
    when = time.time() - age_days * 86400
    os.utime(p, (when, when))
    monkeypatch.setattr(MO, "KNOWN_RANGES_PATH", p)
    return p


def test_main_ORDERS_the_universe_rows_and_the_CLAWGATE_row_STAYS_FIRST(
        monkeypatch, tmp_path):
    """🔴 THE INVARIANT THE WHOLE CHANGE IS SCOPED BY: the ordering applies to
    the universe rows BENEATH the measured ones, never to the whole list. A bare
    `#N` still opens on the clawgate task, one Enter away.

    Driven through `main()` rather than through `order_universe`, because the
    ordering could be perfectly right and still be applied to the wrong slice."""
    universe = ["acme/impossible", "acme/near", "acme/far", "acme/unknown"]
    _ranges_on_disk(monkeypatch, tmp_path,
                    {"acme/impossible": 0, "acme/near": 1300,
                     "acme/far": 99000})
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: {})
    monkeypatch.setattr(MO, "load_known_universe", lambda *a, **k: universe)
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "")
    seen = {}
    monkeypatch.setattr(MO, "pick",
                        lambda c, mesg="": seen.update(cands=c, mesg=mesg) or "")
    assert MO.main(["#1291"]) == 0
    rows = seen["cands"]
    assert rows[0]["platform"] == "clawgate", (
        f"the clawgate row is no longer FIRST: {MO.picker_rows(rows)[:2]}")
    repos = [MO.repo_of_github_url(c["url"]) for c in rows[1:]]
    assert repos == ["acme/near", "acme/far", "acme/unknown",
                     "acme/impossible"], repos


def test_main_says_the_rows_are_ORDERED_in_the_PICKER_HEADER(monkeypatch,
                                                             tmp_path):
    """The header is the only surface that can say so — see `ordering_note`.

    ⚠ THIS SHAPE HAS NO PRE-EXISTING NOTE, so the clause is the whole header —
    the bare-`#N` picker is rows of real evidence plus a list and has always
    carried `mesg == ""`. That the clause is APPENDED rather than SUBSTITUTED
    where a note DOES exist is pinned separately, by the two whole-string
    `guessed_note` tests above; asserting it here would have been asserting it
    of a string that never had a second half."""
    _ranges_on_disk(monkeypatch, tmp_path, {"acme/yes": 9000, "acme/no": 0})
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: {})
    monkeypatch.setattr(MO, "load_known_universe",
                        lambda *a, **k: ["acme/yes", "acme/no"])
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "")
    seen = {}
    monkeypatch.setattr(MO, "pick",
                        lambda c, mesg="": seen.update(mesg=mesg) or "")
    assert MO.main(["#1291"]) == 0
    assert "ordered by plausibility for #1291" in seen["mesg"], seen["mesg"]
    assert "1 could have it" in seen["mesg"], seen["mesg"]
    assert "1 have no references" in seen["mesg"], seen["mesg"]
    _no_universe_token_anywhere(seen["mesg"], "ORDERED HEADER")


def test_main_says_the_rows_are_UNORDERED_when_the_table_is_STALE(monkeypatch,
                                                                  tmp_path):
    """🔴 IT DEGRADES *AND SAYS SO*. A silently-unordered picker would let the
    operator keep trusting a top-of-list habit the host has stopped earning.

    🔴 THE FIXTURE'S NAMES ARE CHOSEN SO THE TWO ORDERS DISAGREE, and that is
    the whole reason this test can see anything. Its first version used
    `zulu/one` / `alpha/two` / `mike/three`, whose ALPHABETICAL order happens to
    equal their PLAUSIBILITY order for `#1291` — so a mutant that ordered a
    stale table anyway produced a byte-identical list and SURVIVED a green run.
    Measured in this change's own mutation sweep (M9). The names below put the
    plausible repo in the middle of the alphabet and the impossible one first."""
    universe = ["alpha/zeroref", "mike/highhead", "zulu/unmeasured"]
    _ranges_on_disk(monkeypatch, tmp_path,
                    {"alpha/zeroref": 0, "mike/highhead": 9000},
                    age_days=MO.STALE_MAPPING_DAYS + 3)
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: {})
    monkeypatch.setattr(MO, "load_known_universe", lambda *a, **k: universe)
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "")
    seen = {}
    monkeypatch.setattr(MO, "pick",
                        lambda c, mesg="": seen.update(cands=c, mesg=mesg) or "")
    assert MO.main(["#1291"]) == 0
    assert "rows unordered" in seen["mesg"], seen["mesg"]
    assert "mention-known-repos-refresh" in seen["mesg"], seen["mesg"]
    # …and the rows really ARE in the pre-change order. `repo_universe` sorts
    # case-insensitively, so an ordering applied anyway would have led with
    # `mike/highhead` (9000 >= 1291) and ended on `alpha/zeroref` (0 refs).
    repos = [MO.repo_of_github_url(c["url"]) for c in seen["cands"]
             if MO.repo_of_github_url(c["url"])]
    assert repos == sorted(universe, key=str.lower), repos
    # POSITIVE CONTROL ON THE FIXTURE: the two orders must genuinely disagree,
    # or the assertion above is satisfied by a coincidence rather than by the
    # degrade. This is the check whose absence let mutant M9 survive.
    ordered = MO.order_universe(universe, "1291",
                                {"alpha/zeroref": 0, "mike/highhead": 9000})
    assert ordered != sorted(universe, key=str.lower), (
        f"the fixture's alphabetical order EQUALS its plausibility order "
        f"({ordered}) — this test cannot see a stale table being ordered")


def test_main_RECORDS_the_repository_the_operator_PICKED(monkeypatch):
    """Tier B's write side, end to end: the row the operator selected becomes a
    line in the log, and the log is the one the autouse redirect points at."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: {})
    monkeypatch.setattr(MO, "load_known_universe",
                        lambda *a, **k: ["acme/chosen", "acme/other"])
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "")
    monkeypatch.setattr(MO, "open_url", lambda url: 0)
    monkeypatch.setattr(MO, "pick",
                        lambda c, mesg="": next(x["url"] for x in c
                                                if "acme/chosen" in x["url"]))
    assert MO.main(["#1291"]) == 0
    rows = [json.loads(ln) for ln in MO.PICKS_PATH.read_text().splitlines()]
    assert [(r["repo"], r["n"]) for r in rows] == [("acme/chosen", 1291)], rows


def test_a_DISMISSED_picker_records_NOTHING(monkeypatch):
    """A dismissal opens nothing and must teach nothing — otherwise the log
    would fill with repositories the operator explicitly declined."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: {})
    monkeypatch.setattr(MO, "load_known_universe",
                        lambda *a, **k: ["acme/one", "acme/two"])
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "")
    monkeypatch.setattr(MO, "pick", lambda c, mesg="": "")
    assert MO.main(["#1291"]) == 0
    assert not MO.PICKS_PATH.exists(), MO.PICKS_PATH.read_text()


def test_a_CLAWGATE_pick_is_NOT_recorded_as_a_repository(monkeypatch):
    """`repo_of_github_url` is what keeps a task URL out of the repository log —
    a clawgate row has an id, not an owner."""
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: {})
    monkeypatch.setattr(MO, "load_known_universe", lambda *a, **k: ["acme/one"])
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: "")
    monkeypatch.setattr(MO, "open_url", lambda url: 0)
    monkeypatch.setattr(MO, "pick",
                        lambda c, mesg="": next(x["url"] for x in c
                                                if x["platform"] == "clawgate"))
    assert MO.main(["#1291"]) == 0
    assert not MO.PICKS_PATH.exists(), MO.PICKS_PATH.read_text()


def test_an_EXPLICIT_click_pays_NOTHING_for_the_ORDERING(monkeypatch, tmp_path):
    """🔴 THE FAST PATH MUST NOT PAY FOR A LIST IT DISCARDS, AND THE FIRST
    VERSION OF THIS CHANGE DID — found by a latency probe, not by review, under
    a comment that CLAIMED the opposite.

    The trap is that `may_offer_universe and num` is TRUE for an explicit
    `owner/repo#N`: that click carries a number and bars no picker, it simply
    never reaches an arm that shows one. So "inside the conditional" is not the
    same as "only on the picker path", and the ordering reads landed on the
    commonest, fastest shape in the handler. MEASURED at 392 rows and a 400-row
    pick log: a median 1.94 ms (0.26 ranges + 1.09 picks + 0.20 sort) against a
    ~30 ms click.

    Counted rather than timed: a 2 ms claim would drown in this box's load,
    while "did it open the file" is exact."""
    _ranges_on_disk(monkeypatch, tmp_path, {"acme/onlyone": 9000})
    (tmp_path / "picks-for-this-test.jsonl").write_text(
        json.dumps({"t": time.time(), "repo": "acme/onlyone", "n": 1}) + "\n")
    monkeypatch.setattr(MO, "PICKS_PATH", tmp_path / "picks-for-this-test.jsonl")
    reads: list[str] = []
    monkeypatch.setattr(MO, "load_known_ranges",
                        lambda *a, **k: reads.append("ranges") or {})
    monkeypatch.setattr(MO, "load_picks", lambda *a, **k: reads.append("picks") or [])
    monkeypatch.setattr(MO, "ranges_age_days",
                        lambda *a, **k: reads.append("age") or 1.0)
    monkeypatch.setattr(MO, "open_url", lambda url: 0)
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: {})
    monkeypatch.setattr(MO, "load_known_universe",
                        lambda *a, **k: ["acme/onlyone"])

    assert MO.main(["civitai/talos-infra#1065"]) == 0
    assert reads == [], (
        f"an explicit owner/repo#N click paid for the ordering it never uses: "
        f"{reads}")

    # 🔴 POSITIVE CONTROL ON THE COUNTER, in the same test. An assertion that
    # only ever checks for an EMPTY list is indistinguishable from one watching
    # a variable nothing writes to — so the very next call, on a shape that DOES
    # show a picker, must move the number.
    monkeypatch.setattr(MO, "pick", lambda c, mesg="": "")
    assert MO.main(["#1291"]) == 0
    assert "ranges" in reads, (
        f"POSITIVE CONTROL FAILED — the picker path did not read the range "
        f"table either, so the assertion above proves nothing: {reads}")


def test_the_ordered_universe_is_computed_AT_MOST_ONCE_per_click(monkeypatch,
                                                                 tmp_path):
    """⚠ AN INVARIANT GUARD, NOT A REGRESSION TEST, AND IT IS LABELLED ONE
    BECAUSE A MUTATION SWEEP PROVED IT CANNOT FAIL TODAY.

    It was written believing the guessed arm asks for the rows a SECOND time,
    so that `universe_rows`' memo was load-bearing. It is not: the three call
    sites are mutually exclusive (dead end 1 and 2 are an `if`/`elif`; dead
    end 1 sets `offered_universe`, which clears `guessed_offer`; dead end 2
    requires NO GitHub candidate while `guessed` requires one). A mutant
    defeating the memo SURVIVED this test and the whole suite — recorded here
    rather than quietly deleted, because "I could not kill it" is a fact about
    the guard that the next reader needs.

    What it still buys: if a fourth call site is ever added, this goes red
    rather than the click silently paying twice. It counts one click's calls,
    with a positive control that the guessed arm ran at all — so it cannot pass
    by the ordering never happening."""
    _ranges_on_disk(monkeypatch, tmp_path, {"acme/one": 9000, "acme/two": 0})
    calls: list[int] = []
    real = MO._ordered_universe
    monkeypatch.setattr(MO, "_ordered_universe",
                        lambda rows, num: calls.append(1) or real(rows, num))
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: {})
    monkeypatch.setattr(MO, "load_known_universe",
                        lambda *a, **k: ["acme/one", "acme/two"])
    monkeypatch.setattr(MO, "tmux_pane_repo", lambda: PANE_GUESS)
    seen = {}
    monkeypatch.setattr(MO, "pick",
                        lambda c, mesg="": seen.update(n=len(c)) or "")
    assert MO.main(["#1291"]) == 0
    # POSITIVE CONTROL: the guessed arm really did run, or "at most once" would
    # be satisfied by "never".
    assert seen["n"] >= 3, seen
    assert calls == [1], (
        f"_ordered_universe ran {len(calls)} times for one click — the memo is "
        f"not holding")


def test_a_PLAUSIBLE_universe_row_that_is_the_ONLY_one_is_still_NOT_auto_opened(
        spy, monkeypatch, tmp_path):
    """🔴 ORDER ONLY, NEVER AUTO-OPEN — the invariant this change was most
    likely to break by accident. Making a row LOOK like the obvious answer must
    not turn it into one: `offered_universe` still suppresses the "exactly one
    candidate -> just open it" shortcut, and the ordering has no opinion about
    that at all.

    Built as the sharpest case available: a host whose universe holds ONE
    repository, whose range table calls that repository PLAUSIBLE for the
    clicked number, reached through a shape that produces no clawgate sibling."""
    _ranges_on_disk(monkeypatch, tmp_path, {"acme/onlyone": 9000})
    monkeypatch.setattr(MO, "discover_repos", lambda *a, **k: {})
    monkeypatch.setattr(MO, "load_known_universe",
                        lambda *a, **k: ["acme/onlyone"])
    assert MO.main(["trowelcast#1291"]) == 0
    assert ("pick", 1) in spy, (
        f"a single PLAUSIBLE universe row was auto-opened instead of OFFERED: "
        f"{spy}")


# --------------------------------------------------------------------------- #
# 🔴 THE MEASURED-SHAPE CORPUS — ordering at the REAL universe's size and
# distribution, not on a four-row toy.
# --------------------------------------------------------------------------- #
def _measured_shape_corpus(seed: int = 20260911):
    """(~390 rows, range table) shaped like the operator's real universe.

    THE DISTRIBUTION IS THE MEASUREMENT, scaled from the 120-repo sample named
    at the top of this block: 45% with NO references, then 1-9, 10-99, 100-999
    and 1000+ in the measured proportions. Names are SYNTHETIC — this repo is
    PUBLIC and the real universe is private."""
    import random  # noqa: PLC0415 — only this corpus needs it
    rng = random.Random(seed)
    owners = ["gardenersguild", "hobbyist", "rivalorg", "nimbusworks",
              "greenfielded", "quartzline", "sablefen", "umbralabs"]
    stems = ["trowelcast", "spadeworks", "plotwidget", "sledgehorn",
             "ploughshare", "atlas", "beacon", "cascade", "dossier", "eyrie",
             "fathom", "girder", "harbour", "inkwell", "jetty", "kiln",
             "lantern", "mortar", "nectar", "oxbow", "pergola", "quill"]
    repos = sorted({f"{o}/{s}{i}" for i in range(3) for o in owners
                    for s in stems})[:390]
    table = {}
    for r in repos:
        roll = rng.random()
        if roll < 0.45:
            table[r.lower()] = 0
        elif roll < 0.78:
            table[r.lower()] = rng.randint(1, 9)
        elif roll < 0.94:
            table[r.lower()] = rng.randint(10, 99)
        elif roll < 0.98:
            table[r.lower()] = rng.randint(100, 999)
        else:
            table[r.lower()] = rng.randint(1000, 3000)
    return repos, table


def test_the_measured_corpus_really_HAS_the_measured_shape():
    """🔴 THE GUARD ON THE FIXTURE. Both tests below assert against PROPORTIONS
    of this corpus; if a reseed or a refactor flattened the distribution they
    would keep passing while measuring something else entirely."""
    repos, table = _measured_shape_corpus()
    assert 350 <= len(repos) <= 420, len(repos)
    zero = sum(1 for v in table.values() if v == 0)
    high = sum(1 for v in table.values() if v >= 1000)
    assert 0.38 <= zero / len(repos) <= 0.52, zero / len(repos)
    assert 1 <= high <= 25, high


def test_at_the_REAL_SIZE_a_HIGH_number_puts_the_answer_near_the_TOP():
    """🔴 THE END-TO-END MEASUREMENT, on ~390 rows with the measured
    distribution. A high number is strongly discriminative: only the handful of
    repositories that have reached it are plausible, so the wanted row lands in
    the first few rather than somewhere in the alphabet.

    THE NEGATIVE CONTROL IS ASSERTED IN THE SAME RUN: the same repository under
    today's alphabetical ordering sits far down the list, or this test would be
    measuring nothing."""
    repos, table = _measured_shape_corpus()
    wanted = repos[len(repos) // 2]
    table[wanted.lower()] = 1300          # a head just past the clicked number
    ordered = MO.order_universe(repos, "1291", table)
    rank = ordered.index(wanted) + 1
    plausible = sum(1 for r in repos if table[r.lower()] >= 1291)
    baseline = repos.index(wanted) + 1
    assert rank <= plausible, (
        f"the wanted repo ranked {rank} of {len(repos)} but only {plausible} "
        f"rows are plausible for #1291 — the ordering is not doing its job")
    assert rank <= 5, f"ranked {rank} of {len(repos)}"
    assert baseline > 20 * rank, (
        f"NEGATIVE CONTROL: the pre-change alphabetical rank was only "
        f"{baseline}, so this corpus does not demonstrate an improvement "
        f"(ordered rank {rank})")


def test_at_the_REAL_SIZE_a_LOW_number_still_RANKS_OUT_the_IMPOSSIBLE():
    """🔴 THE WEAK CASE, AND IT IS STILL WORTH IT. `#12` is plausible for most
    repositories that have any references at all, so proximity buys little — but
    45% of the list can NEVER satisfy it, and those drop below every row that
    can. That is the bigger and cheaper half of the win.

    Asserted as a PARTITION rather than on one row's rank, because for a low
    number there is no single right answer to rank first."""
    repos, table = _measured_shape_corpus()
    ordered = MO.order_universe(repos, "12", table)
    impossible = [r for r in repos if table[r.lower()] == 0]
    assert len(impossible) > len(repos) // 3, (
        f"POSITIVE CONTROL: only {len(impossible)} of {len(repos)} rows are "
        f"impossible — the corpus no longer has the measured shape")
    assert sorted(ordered[-len(impossible):]) == sorted(impossible), (
        "the impossible rows are not exactly the TAIL of the list")
    assert sorted(ordered) == sorted(repos), "rows were dropped"


def test_REAL_fzf_lets_our_PRECOMPUTED_ORDER_decide_a_TIE():
    """🔴 THE MECHANISM THE WHOLE ORDERING RESTS ON, ASSERTED AGAINST THE REAL
    BINARY. `order_universe` computes a rank and hands it to fzf as INPUT ORDER;
    that only means anything if fzf falls back to input order when its score and
    tiebreak criteria tie. If it did not, our ordering would be silently
    discarded the moment the operator typed a character.

    ⚠ AND THIS IS WHY `--tiebreak=end,index` IS *NOT* SET. Adding `,index` was
    the obvious way to make the property explicit, and it is a NO-OP: MEASURED
    2026-09-11 over 520 (corpus, query) pairs, `--tiebreak=end` and
    `--tiebreak=end,index` produced byte-identical output in **520 of 520**,
    while the positive control `--tiebreak=length` differed in **438**. fzf
    appends `index` implicitly (confirmed separately on a corpus built to TIE
    under `end`, where every criterion returned input order). The property is
    already ours; changing `PICKER_SH` — pinned by a whole-string test, a
    metacharacter ban and a redirection ledger — would have been churn on a
    disclosure-relevant constant for zero behaviour change.

    The flags come from `_picker_flags()` and are never re-spelled here: a test
    carrying its own would be a fact about fzf rather than about this handler."""
    _require_fzf()
    # Equal-length rows, so the query matches at an IDENTICAL offset from the
    # end of every one: nothing but input order can separate them.
    owners = ["zzzz", "mmmm", "aaaa", "qqqq", "bbbb", "yyyy"]
    rows = [f"github 1291 — https://github.com/{o}/api/pull/1291" for o in owners]
    assert len({len(r) for r in rows}) == 1, "the rows are not equal length"
    got = _fzf_filter(rows, "api")
    assert got == rows, (
        f"fzf did NOT break the tie by input order — our pre-computed ordering "
        f"is being discarded:\n{got}")
    # 🔴 THE CONTROL THAT MAKES THIS A MEASUREMENT RATHER THAN A COINCIDENCE:
    # reverse the INPUT and the OUTPUT must reverse with it. Without this, a
    # corpus fzf happened to emit in that order for some other reason would
    # satisfy the assertion above.
    assert _fzf_filter(rows[::-1], "api") == rows[::-1], (
        "reversing the input did not reverse the output — the rows are NOT "
        "being separated by input order, so this test proves nothing")


def _fzf_filter(rows: list[str], query: str) -> list[str]:
    """`fzf --filter` under the PICKER'S OWN flags — see `_picker_flags`."""
    out = subprocess.run(["fzf", "--filter", query, *_picker_flags()],
                         input="\n".join(rows), capture_output=True, text=True)
    return [r for r in out.stdout.split("\n") if r]
