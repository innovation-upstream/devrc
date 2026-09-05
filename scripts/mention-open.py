#!/usr/bin/env python3
"""mention-open — Alacritty hint handler for clawgate / GitHub / ClickUp mentions.

Alacritty runs a hint's `command` with the matched text appended as the LAST
argument (after any configured `args`). That is the whole contract, and it is why
`sys.argv[-1]` — not `sys.argv[1]` — is the text.

    hints.enabled = [{ command = ".../mention-open.py", regex = "…", … }]

The matched text is re-scanned here with `scripts/collector/mention_scan.py`, the
SAME module the telemetry emitter uses, so what the terminal underlines and what
gets opened can never disagree. The Alacritty regex is deliberately LOOSER than
the scanner (Rust's regex crate has no lookaround, so the trailing-digit guard
that rejects `#282828` cannot be expressed there) — this script is the strict
authority, and a match the scanner rejects opens nothing.

RESOLUTION
----------
  1 openable candidate   -> xdg-open it.
  2+ (a bare `#N`)       -> rofi picker, one row per platform, showing the URL.
  0                      -> the FUZZY repo picker over the LOCAL universe, and
                            only if that cannot be shown, a notification saying
                            WHICH empty this is.

🔴 THE CLICK PATH MAKES NO NETWORK CALL. This is a hard property, not a target,
and it is pinned by `test_the_resolution_path_spawns_ONLY_these_local_commands`.

🔴 THE CANDIDATE UNIVERSE IS THE REPOS THE OPERATOR CONTRIBUTES TO — NEVER ALL
OF GITHUB, AND A GITHUB-WIDE NAMESAKE SEARCH USED TO LIVE HERE. `gh api
search/repositories` was consulted for a `repo#N` the local sources could not
name. MEASURED on the deployed copy: `dashboard#12` took **4.3s through
`--print` and ~10s through the real hint path, at 6% CPU** — essentially all of
it network wait — and what came back was `vzhong/dashboard`,
`yorkie-team/dashboard`, `zce/dashboard`: strangers' repositories, none of which
the operator would ever pick. It was not slow AND useful; it was slow BECAUSE it
searched a corpus that structurally cannot contain the answer. The same click
with discovery disabled returned in 0.046s.

So it is DELETED, not deferred or cached. The correct universe was local the
whole time: `~/.config/mention-open/known_repos.json` is built by
`scripts/regen-known-repos.py` from `gh api user/repos --paginate` — owner,
collaborator and organization_member — plus the local checkouts. That IS "the
repos the operator contributes to", it is already on disk, and reading it costs
under a millisecond.

🔴 NOTHING IS GUESSED. A GitHub reference needs an owner, and an owner that is
wrong points confidently at a real-but-unrelated issue. An owner is accepted
only from a source that names THIS repo unambiguously, in this order:

  1. an explicit `owner/repo` in the clicked text itself — the strongest signal
     there is, and the only one that costs no I/O at all;
  2. an exact hit in `~/.config/mention-open/known_repos.json`. It is per-host
     and OUTSIDE every checkout — it names private repositories, and this repo
     is public;
  3. the git remote of a real local checkout under `~/workspace` (MEASURED, and
     it OVERRIDES 2 — the checkout is authoritative about its own owner);
  4. the tmux pane the operator was most recently in.

🔴 AND WHEN NONE OF THEM ANSWERS, THE OPERATOR CHOOSES — THE HANDLER DOES NOT
REFUSE. Every repository this host knows about goes into a rofi picker with
`-matching fuzzy`, so `talos-inf#12` is four keystrokes from `talos-infra`, and
`#282828` — which the strict scanner rejects, and which used to produce the
toast `no mention in the clicked text` — opens a picker instead. That toast read
as a failure when it was the guard working, and a guard that reads as a bug gets
deleted by the next maintainer.

🔴 THIS IS NOT A RELAXATION OF THE NO-GUESSING RULE, IT IS ITS EXTENSION. A
picker is a CHOICE the operator makes; a guess is one this script makes for
them. Nothing below ever opens a URL the operator did not select, and dismissing
the picker still opens NOTHING. In particular a six-digit number is OFFERED and
never AUTO-OPENED — see `offer_number` and `offered_universe`.

⚠ IT DEGRADES, IT DOES NOT DISAPPEAR. A toast is correct in exactly one case:
the picker cannot be shown. That is the universe being missing, unreadable or
empty, or `--print`/`--no-discovery` barring it by contract — and the toast
still names WHICH of those it is, because each needs a different next move. A
silent empty picker would be the same silent zero one layer up.

🔴 THE UNIVERSE NAMES PRIVATE REPOSITORIES. It is built from
`known_repos.json`, the file whose committed ancestor disclosed 232 private
repos into this PUBLIC repository. It may go to the operator's own screen and
NOWHERE ELSE: never to a log, never to activity.events, never to a test fixture,
never to stderr. `notify()` prints, so the refusal paths below name only the
clicked text — never a row from the universe.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# 🔴 `concurrent.futures` IS IMPORTED LAZILY, INSIDE `discover_repos`, AND THAT
# IS MEASURED RATHER THAN STYLISTIC. At the top of the file it costs ~5ms of
# interpreter startup — it drags in `threading`, `queue` and `weakref` — and that
# 5ms lands on EVERY click, including `owner/repo#N` and a bare `#N`, which are
# the common cases and never reach the fan-out at all. Measured here, interleaved
# against the pre-change file: 29.6ms -> 35.1ms for an explicit `owner/repo#N`
# with the import at the top, and back to 30.0ms with it moved. A 5ms tax on the
# fast path to save nothing on the slow one is a straight loss.

sys.path.insert(0, str(Path(__file__).resolve().parent / "collector"))

from mention_scan import (  # noqa: E402
    GITHUB_ISSUE_URL,
    PLATFORM_CLAWGATE,
    PLATFORM_CLICKUP,
    PLATFORM_GITHUB,
    OWNER_REPO_VALUE_RE as _OWNER_REPO_RE,
    clean_repo_map,
    scan_mention_spans,
)

# Where local checkouts live. Only used to READ git remotes — the mapping it
# produces is measured, never invented.
WORKSPACE = Path(os.environ.get("DEVRC_WORKSPACE", Path.home() / "workspace"))

# The generated mapping. 🔴 OUTSIDE every checkout, on purpose: it names PRIVATE
# repositories and this repo is PUBLIC. `scripts/regen-known-repos.py` writes it;
# nothing breaks when it is absent. See that script's docstring for the incident.
KNOWN_REPOS_PATH = Path(
    os.environ.get("MENTION_OPEN_KNOWN_REPOS")
    or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    / "mention-open" / "known_repos.json")

# rofi theme — the same one nix/i3/config.nix already uses for the app launcher,
# so the picker looks like every other picker on this desktop.
ROFI_THEME = "gruvbox-dark-hard"

# How many `git remote get-url` children run at once during discovery. 16 was
# measured as the knee on this host (8/16/32/64 -> 24/26/28/32 ms for 100
# checkouts); the curve is flat enough either side that the exact value is not
# load-bearing, which is why no test pins it.
_DISCOVERY_WORKERS = 16

PLATFORM_LABEL = {
    PLATFORM_CLAWGATE: "clawgate task",
    PLATFORM_GITHUB: "github",
    PLATFORM_CLICKUP: "clickup",
}


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested without touching git, tmux, rofi or a browser)
# --------------------------------------------------------------------------- #
_SSH_REMOTE = re.compile(r"^(?:ssh://)?git@[^:/]+[:/](?P<path>.+?)(?:\.git)?/?$")
_HTTP_REMOTE = re.compile(r"^https?://[^/]+/(?P<path>.+?)(?:\.git)?/?$")

# 🔴 THE NUMBER A PICKER MAY OFFER — WHICH IS NOT A REFERENCE THE HANDLER MAY
# OPEN. `mention_scan`'s `_NUM` is `\d{1,5}` precisely so a six-digit hex colour
# cannot become a reference; that guard is UNCHANGED and still decides what
# auto-opens. This pattern is a strictly weaker thing: given text the scanner
# REFUSED, it recovers a number so the operator can be offered a choice instead
# of a dead end.
#
# 🔴 THE TWO MUST NOT BE CONFLATED, AND THE CODE KEEPS THEM APART STRUCTURALLY,
# not by convention: a number that arrives through here can only ever become a
# UNIVERSE row, and `offered_universe` in `main()` unconditionally suppresses the
# "exactly one candidate → just open it" shortcut for universe rows. So there is
# no path on which `#282828` opens anything without the operator selecting it.
#
# The `{1,6}` bound is not arbitrary — it MIRRORS the Alacritty hint regex in
# `nix/programs/alacritty/default.nix`, which is the click surface. Offering a
# number the terminal could never underline would be offering something no click
# can produce. The two bounds are pinned to each other by
# `test_alacritty_hints.py::test_the_handlers_OFFER_bound_matches_the_hints_own`.
_OFFER_NUM_RE = re.compile(r"#(?P<num>[0-9]{1,6})")

# 🔴 THERE IS NO CAP ON HOW MANY REPOS REACH THE PICKER, AND THIS PARAGRAPH
# REPLACES ONE THAT ARGUED FOR IT. The old rule refused above 8, on the reasoning
# that "a 100-row list of URLs differing only by owner is not a choice, it is a
# wall". That reasoning was correct about a list you can only SCROLL and wrong
# about one you can TYPE AT: `-matching fuzzy` turns the wall into a narrowing.
#
# 🔴 DO NOT RE-ADD THE CAP WITHOUT ALSO REMOVING THE FUZZY MATCHER. A comment
# left asserting a hazard the code has closed is exactly how the refusal would
# come back: the next maintainer reads "not a choice, it is a wall", believes it,
# and restores a limit that now only removes the operator's ability to choose.
# The residual cost, stated rather than hidden: the picker can be several hundred
# rows on a host with many checkouts.

# `_OWNER_REPO_RE` is IMPORTED, not re-declared — see the import block. It used
# to be a second copy of `mention_scan`'s rule, which is exactly the shape that
# regenerates one bug at two sites; the `\Z`-not-`$` incident is recorded on the
# surviving definition.


def parse_owner_repo(remote_url: str) -> str:
    """`owner/repo` from a git remote URL, or "" when it is not a GitHub-shaped
    two-segment path.

    Accepts the three forms that actually occur here — `git@host:owner/repo.git`,
    `ssh://git@host/owner/repo`, `https://host/owner/repo.git` — and refuses
    anything that does not resolve to exactly two path segments, because a
    one-segment or three-segment path would produce a URL that 404s while
    looking authoritative.
    """
    url = (remote_url or "").strip()
    if not url:
        return ""
    m = _SSH_REMOTE.match(url) or _HTTP_REMOTE.match(url)
    if not m:
        return ""
    parts = [p for p in m.group("path").split("/") if p]
    if len(parts) != 2:
        return ""
    return f"{parts[0]}/{parts[1]}"


def offer_number(text: str) -> str:
    """The first `#N` in `text` as a bare digit string, or "".

    🔴 THIS IS AN OFFER, NOT A RESOLUTION, AND THE DISTINCTION IS THE WHOLE
    POINT. `mention_scan` refuses `#282828` because a six-digit run is far more
    likely a hex colour than task 282828, and that refusal is correct about what
    may be OPENED. It was wrong about what may be SHOWN: the operator got the
    toast `no mention in the clicked text`, which reads as the handler being
    broken rather than as the guard doing its job.

    So a number recovered here reaches the picker and nowhere else. It is never
    handed to `resolve()`, never used to build a candidate that could satisfy the
    single-candidate auto-open, and never counted as a mention.

    ⚠ IT RETURNS "" FOR TEXT WITH NO `#N` AT ALL, and that case cannot arrive
    from a click: the Alacritty hint regex requires `#[0-9]{1,6}` or a ClickUp
    id, so nothing digitless is ever underlined. It is reachable only from the
    command line, and it is the one place the old refusal survives verbatim.
    """
    m = _OFFER_NUM_RE.search(text or "")
    return m.group("num") if m else ""


def openable(span: dict) -> list[dict]:
    """The span's candidates that actually carry a URL, in display order."""
    return [c for c in span.get("candidates", []) if c.get("url")]


def picker_rows(candidates: list[dict]) -> list[str]:
    """One `TAB`-free display row per candidate: `<platform> <id> — <url>`.

    The URL is IN the row on purpose: the whole point of the picker is that the
    operator can see which of two plausible references they are about to open,
    and the platform name alone does not tell them that.
    """
    rows = []
    for c in candidates:
        label = PLATFORM_LABEL.get(c["platform"], c["platform"])
        rows.append(f"{label} {c['id']} — {c['url']}")
    return rows


def repo_universe(repos: dict | None) -> list[str]:
    """Every distinct `owner/repo` this host knows about, sorted.

    The input is `discover_repos()`'s mapping — the generated file laid under the
    real local checkouts — so this is a MEASUREMENT of the host, not a list of
    plausible names. Values that are not exactly `owner/repo` are dropped for the
    same reason they are dropped on load: they build a URL that 404s while
    looking authoritative.

    🔴 THE RETURN VALUE NAMES PRIVATE REPOSITORIES. It may reach the operator's
    rofi window and nothing else — no log line, no notification body, no
    telemetry row, no test fixture. See the module docstring.
    """
    return sorted({v for v in (repos or {}).values()
                   if isinstance(v, str) and _OWNER_REPO_RE.match(v)})


def universe_candidates(num: str, universe: list[str]) -> list[dict]:
    """One openable GitHub candidate per repository in `universe`.

    These are what turns a dead end into a choice: the operator types a few
    characters of the repo name into the fuzzy picker instead of reading a
    refusal. Nothing here is opened without a selection.
    """
    return [{"platform": PLATFORM_GITHUB, "id": num,
             "url": GITHUB_ISSUE_URL.format(repo=full, id=num)}
            for full in universe]


def row_to_url(row: str, candidates: list[dict]) -> str:
    """Map a picker row back to its URL. Matches on the URL suffix rather than
    the row index, so a rofi build that decorates or reorders rows cannot open
    the wrong one."""
    row = (row or "").strip()
    if not row:
        return ""
    for c in candidates:
        if row.endswith(c["url"]):
            return c["url"]
    return ""


# --------------------------------------------------------------------------- #
# Measurement (the impure half)
# --------------------------------------------------------------------------- #
def _git(args: list[str], cwd: str | None = None) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                           text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def repo_of_checkout(path: Path) -> str:
    """`owner/repo` for a checkout, from its `origin` remote. "" if unreadable."""
    return parse_owner_repo(_git(["remote", "get-url", "origin"], cwd=str(path)))


def load_known_repos(path: Path | None = None) -> dict[str, str]:
    """{name: "owner/repo"} from the operator's generated mapping, or {}.

    🔴 EVERY failure is {} — absent, unreadable, malformed, wrong shape. This
    runs on a detached click handler with nowhere to print a traceback, and the
    mapping is an OPTIONAL accelerator: without it `owner/repo#N`, a local
    checkout and the API fallback all still resolve. An earlier version imported
    a generated module at the top of this file, which meant a checkout predating
    that file killed EVERY click, including the ones needing no mapping at all.
    """
    # 🔴 RESOLVED AT CALL TIME, NOT BOUND AS A DEFAULT. A `path: Path =
    # KNOWN_REPOS_PATH` default is evaluated at IMPORT, so a test that patches
    # the module attribute changes nothing and passes for the wrong reason —
    # measured: the override test below was inert, and deleting this whole call
    # from `discover_repos` left the entire suite green.
    path = path or KNOWN_REPOS_PATH
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    # A value must be EXACTLY `owner/repo` — matched, not counted. That rule now
    # lives ONCE, in `mention_scan.clean_repo_map`, because the collector's
    # tailer loads the same mapping and a predicate open-coded at two sites is
    # wrong at one of them.
    return clean_repo_map(raw)


def discover_repos(workspace: Path | None = None) -> dict:
    """{repo name: "owner/repo"} — the generated mapping, then local checkouts
    laid over it.

    A local checkout WINS: it is a measurement of this disk, so it is the
    authority on its own owner and it settles a name the mapping had to drop as
    ambiguous.

    🔴 EVERY SOURCE HERE IS LOCAL. There is no network fallback below this
    function and none above it; a name this cannot answer becomes a PICKER, not
    a search. See the module docstring for the 4.3s this replaced.
    """
    # 🔴 RESOLVED AT CALL TIME — the same defect as `load_known_repos`' old
    # default, and the reason this is a CLASS sweep rather than one fix: a
    # `workspace: Path = WORKSPACE` default is bound at import, so a test
    # patching `MO.WORKSPACE` was inert and the "no checkout has this name"
    # premise was a property of the OPERATOR'S DISK, not of the fixture.
    # Measured: one such test ran 91 real `git remote` subprocesses against the
    # real ~/workspace and read back 79 real repositories.
    workspace = workspace or WORKSPACE
    out: dict = dict(load_known_repos())
    try:
        entries = sorted(p for p in workspace.iterdir()
                         if p.is_dir() and (p / ".git").exists())
    except OSError:
        return out
    # 🔴 CONCURRENT, AND THAT IS A LATENCY FIX RATHER THAN A FEATURE. This is a
    # fan-out of one `git remote get-url origin` per checkout, and it lands on
    # the operator on every click that needs discovery. MEASURED on this host,
    # 100 checkouts: 0.149-0.184s serially, 0.026s across 16 threads — the work
    # is ~100% waiting on a child process, so threads are the right tool and the
    # GIL is not in the way.
    #
    # 🔴 `git remote get-url` IS KEPT RATHER THAN PARSING `.git/config`
    # DIRECTLY, and that was a real choice: a hand-rolled reader measured 0.008s
    # and agreed with git on all 100 checkouts here (56 of them linked
    # worktrees), but it would have to re-implement `commondir` resolution,
    # submodule gitdirs, `include`/`includeIf` and `url.<base>.insteadOf` to keep
    # agreeing. 26ms is already far inside the budget; reimplementing a slice of
    # git to save 18ms buys a whole class of divergence bugs for nothing.
    from concurrent.futures import ThreadPoolExecutor  # see the import block
    with ThreadPoolExecutor(max_workers=_DISCOVERY_WORKERS) as pool:
        for entry, full in zip(entries, pool.map(repo_of_checkout, entries)):
            if full:
                out[entry.name] = full
    return out


def tmux_pane_repo() -> str:
    """`owner/repo` for the tmux pane the operator was most recently in, or "".

    ⚠ BEST-EFFORT, AND THE CAVEAT IS REAL. This handler is spawned DETACHED by
    Alacritty; it does not inherit the clicked pane's cwd, and there is no
    Alacritty-side channel that carries it. `tmux display-message` answers for
    the MOST RECENTLY ACTIVE CLIENT, which is not guaranteed to be the pane whose
    text was clicked — a second Alacritty window, or a pane switched since the
    text scrolled past, both make it wrong.

    So this is used ONLY to give a bare `#N` a GitHub candidate it would
    otherwise not have. It never overrides an explicit `owner/repo` in the text,
    and it never suppresses the clawgate candidate: a wrong guess here shows up
    as an extra row in the picker, never as the wrong page opening.
    """
    path = ""
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-p", "-F", "#{pane_current_path}"],
            capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            path = r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    if not path or not os.path.isdir(path):
        return ""
    root = _git(["rev-parse", "--show-toplevel"], cwd=path)
    return repo_of_checkout(Path(root)) if root else ""


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #
def notify(summary: str, body: str = "") -> None:
    """Best-effort desktop notification. A handler that fails SILENTLY is
    indistinguishable from one that was never wired up, which is exactly the
    silent-zero shape this repo keeps paying for — so a refusal is always
    announced somewhere."""
    print(f"mention-open: {summary} {body}".strip(), file=sys.stderr)
    try:
        subprocess.run(["notify-send", "-a", "mention-open", summary, body],
                       timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def open_url(url: str) -> int:
    try:
        subprocess.Popen(["xdg-open", url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        notify("could not open the link", f"{type(exc).__name__}: {exc}")
        return 1
    return 0


def pick(candidates: list[dict]) -> str:
    """Ask rofi which candidate to open. Returns the chosen URL, or "" if the
    operator dismissed the picker (which must open NOTHING).

    🔴 `-matching fuzzy` IS LOAD-BEARING, not a nicety. It is the entire reason
    the namesake cap could be removed and the reason a hundred-row universe is a
    narrowing rather than a wall: the operator types `talos-inf` and the list
    collapses. Removing this flag silently restores the wall the old cap existed
    to prevent, with every test still green — so it is pinned by the suite.

    🔴 `-no-custom` is equally load-bearing in the other direction: it stops rofi
    returning free text the operator TYPED as if it were a selection, which
    `row_to_url` would then fail to match — dismissal-shaped, but by accident.
    """
    rows = picker_rows(candidates)
    try:
        r = subprocess.run(
            ["rofi", "-dmenu", "-i", "-matching", "fuzzy",
             "-p", "mention", "-theme", ROFI_THEME,
             "-format", "s", "-no-custom"],
            input="\n".join(rows), capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        notify("could not show the mention picker",
               f"{type(exc).__name__}: {exc}")
        return ""
    if r.returncode != 0:
        return ""  # dismissed — not an error
    return row_to_url(r.stdout, candidates)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Resolve and open a clawgate / GitHub / ClickUp mention.")
    p.add_argument("--print", dest="print_only", action="store_true",
                   help="print the resolved URL instead of opening it (and "
                        "print every candidate when ambiguous). Never offers "
                        "the repository picker: it is non-interactive, so an "
                        "unresolvable reference exits 1 with a named reason "
                        "rather than listing every repo on the host")
    p.add_argument("--no-discovery", action="store_true",
                   help="do not read git remotes or ask tmux — resolve only "
                        "what the text itself carries")
    p.add_argument("--default-repo", default="",
                   help="owner/repo to use for a bare #N (overrides tmux)")
    p.add_argument("text", nargs="+",
                   help="the matched text; Alacritty appends it LAST")
    return p


def resolve(text: str, *, repos: dict | None = None,
            default_repo: str | None = None) -> tuple[dict | None, list[dict]]:
    """(span, openable candidates) for the FIRST mention in `text`.

    Alacritty's looser regex can hand over text with leading/trailing debris; the
    scanner finds the mention inside it. A text with no mention at all — which is
    how `#282828` arrives here — returns (None, []).

    🔴 NO `profile=` ARGUMENT, AND THAT OMISSION IS THE CLICK SURFACE'S ONLY
    DEFENCE. `mention_scan`'s default is `terminal` — the narrow, click-safe set
    — and this is the single call site that decides which surface the operator's
    terminal underlines. Passing `profile="telemetry"` here would put the WIDE
    enumerated set (`gh pr view N`, `clawgate task N`, bare GitHub URLs, and the
    telemetry-only attribution routes) behind a click, where a false positive is
    a wrong page opening rather than a stray row. Pinned by
    `test_mention_open.py::test_every_TELEMETRY_only_shape_is_invisible_to_the_
    click_handler`, which drives each telemetry-only pattern's own ledger sample
    through here and requires it to resolve to NOTHING — with a positive control
    proving the same sample IS detected at the telemetry profile, so the test
    cannot pass by scanning nothing."""
    spans = scan_mention_spans(text, repos=repos, default_repo=default_repo)
    if not spans:
        return (None, [])
    span = spans[0]
    return (span, openable(span))


def universe_reason(path: Path | None = None) -> str:
    """WHICH empty the repo universe is — absent, unreadable, or holding no
    usable row — as one operator-facing sentence. "" when the mapping is fine.

    🔴 IT NAMES THE FILE AND THE REGENERATOR, NEVER A ROW. This string is handed
    to `notify()`, which writes to stderr AND to `notify-send`; a row from the
    mapping reaching either would disclose a private repository name. The path is
    not a row — it is a constant already spelled in this public file.

    🔴 AND IT SEPARATES THREE STATES THAT ALL PRODUCE THE SAME EMPTY DICT,
    because they need three different next moves: "no mapping yet" wants
    `regen-known-repos.py` run; "unreadable" wants the file looked at BEFORE it
    is regenerated over the top; "ran but produced nothing usable" is a `gh auth`
    problem rather than a mention-open one. Collapsing them into "the universe is
    empty" is the silent zero this handler is written against — it is the same
    mistake as the deleted search's empty result, which could not tell "no such
    repo" from "gh is missing".

    ⚠ IT RE-READS THE FILE. `load_known_repos()` already read it, but it answers
    every failure with `{}` on purpose (see its docstring), so the reason is not
    recoverable from its return value. This runs on the refusal path only, never
    on the hot path.
    """
    path = path or KNOWN_REPOS_PATH
    hint = "run scripts/regen-known-repos.py"
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return f"this host has no repo mapping at {path} — {hint}"
    except (OSError, ValueError):
        return (f"the repo mapping at {path} could not be read — "
                f"look at it before you {hint}")
    if not clean_repo_map(raw):
        return f"the repo mapping at {path} holds no usable rows — {hint}"
    return ""


def refuse(span: dict | None, text: str, args: argparse.Namespace) -> int:
    """The last resort, and the ONLY case a toast is still correct in: the picker
    could not be shown. Always returns 1.

    🔴 THE REASON IS NAMED, NEVER GENERIC, and the ADVICE IS APPENDED RATHER THAN
    SUBSTITUTED. The case that names a cause is exactly the case where writing
    `owner/repo#N` is the workaround, so dropping the advice there is backwards —
    it was dropped once already.

    🔴 IT NAMES THE CLICKED TEXT AND NOTHING ELSE. `discovered` is in scope at
    every call site and holds the whole universe; adding "did you mean one of
    these?" here is a natural-looking improvement that would leak every private
    repository name to stderr and to `notify-send`.
    """
    advice = "open the repo in a tmux pane, or write it as owner/repo#N"
    if span is None and not offer_number(text):
        # No mention AND no number to offer. Unreachable from a click — the
        # Alacritty hint regex requires `#[0-9]{1,6}` or a ClickUp id — so this
        # is the command line, and the original refusal is still the right one.
        notify("no mention in the clicked text", repr(text))
        return 1
    subject = span["raw"] if span is not None else repr(text)
    if args.no_discovery:
        why = "--no-discovery resolves only what the text itself carries"
    elif args.print_only:
        # 🔴 A DECIDED CONTRACT, not an oversight. `--print` is non-interactive;
        # the answer to "which of your 369 repositories did you mean?" is a
        # question, and printing all of them is not an answer either.
        why = "--print cannot show the repository picker — there is nobody to ask"
    else:
        why = universe_reason() or "no repository owner is known for it"
    notify(f"cannot resolve {subject}", f"{why} — {advice}")
    return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # 🔴 THE ALACRITTY CONTRACT: the matched text is the LAST argument. Taking
    # argv[1] would silently read a configured `args` entry instead.
    text = args.text[-1]

    # PASS 1 — pure, no I/O. `owner/repo#N` and a ClickUp id already answer the
    # question, and most clicks are one of those.
    span, candidates = resolve(text, default_repo=args.default_repo or None)

    # 🔴 THE NUMBER A PICKER COULD OFFER, computed from the RAW TEXT and kept
    # strictly apart from anything `resolve()` produced. For `#282828` the
    # scanner returns no span at all and this returns "282828" — which is what
    # turns the old `no mention in the clicked text` toast into a choice. It is
    # never merged into `span` and never satisfies the auto-open below.
    offer_num = offer_number(text)

    # PASS 2 — the LOCAL measurement, only when the text did not answer it.
    # Discovery costs a concurrent `git remote` fan-out plus a tmux round-trip
    # (~30ms here), and paying that before opening a link that needed neither is
    # latency the operator feels on every single click.
    #
    # 🔴 IT NOW RUNS FOR `span is None` TOO. It used to short-circuit straight to
    # a refusal there, which is exactly why `#282828` dead-ended: the universe
    # the picker needs is built by this pass. Guarded on `offer_num` so a text
    # carrying no number at all — impossible from a click, see `offer_number` —
    # still costs nothing.
    discovered: dict = {}
    unresolved = span is None or span["ambiguous"] or not candidates
    if unresolved and not args.no_discovery and (span is not None or offer_num):
        default_repo = args.default_repo or tmux_pane_repo()
        # Kept, not recomputed: PASS 3 offers the SAME measurement as a fuzzy
        # universe, and calling `discover_repos()` twice would run the whole
        # `git remote` fan-out again for an answer already in hand.
        discovered = discover_repos()
        span, candidates = resolve(text, repos=discovered,
                                   default_repo=default_repo or None)

    # PASS 3 — THE FUZZY LOCAL UNIVERSE. Everything above has failed to name a
    # repository, and the old behaviour here was a refusal — first a toast, and
    # before that a 4.3s GitHub-wide search that answered with strangers' repos.
    # Offer every repo THIS HOST knows about and let the operator type at it.
    #
    # 🔴 NOT IN `--print` MODE, and not under `--no-discovery`. `--print` exists
    # so a script can read the resolved URL; answering it with several hundred is
    # not an answer, printing them would write PRIVATE repository names to
    # stdout, and a picker is interactive while `--print` has no operator to ask.
    # `--no-discovery` means "resolve only what the text itself carries", which a
    # host-wide mapping is not.
    #
    # ⚠ IT NEEDS A NUMBER, from one of two places that must not be confused:
    # `span["id"]` when the scanner ACCEPTED the text (already bounded to
    # `\d{1,5}`), or `offer_num` when it REFUSED it. Only a numeric reference can
    # become an issue URL under a chosen repo — there is no such thing as "this
    # ClickUp id, but in that repository" — and a ClickUp span always resolves
    # anyway, so the non-digit branch is a guard rather than one anyone reaches.
    #
    # 🔴 A UNIVERSE ROW IS NEVER OPENED WITHOUT A SELECTION, EVEN WHEN IT IS THE
    # ONLY ONE. `offered_universe` exists solely to suppress the "exactly one
    # candidate → just open it" shortcut below. Without it, a host that knows
    # exactly ONE repository would answer `trowelcast#77` by opening issue 77 in
    # that unrelated repository — a confident wrong page, which is precisely the
    # failure this whole handler is anchored against. It is ALSO what keeps
    # `#282828` offered-but-never-auto-opened: a number the scanner refused
    # reaches `candidates` only as universe rows, and universe rows never
    # auto-open. Measured during development: the first version of this pass did
    # open it, and the test that caught it was the one asserting a picker
    # appeared.
    offered_universe = False
    may_offer_universe = not args.print_only and not args.no_discovery
    num = span["id"] if (span is not None and span["id"].isdigit()) else offer_num
    universe = (universe_candidates(num, repo_universe(discovered))
                if (may_offer_universe and num) else [])
    if not candidates and universe:
        # Dead end 1 — an unresolvable `repo#N`, or text the scanner refused
        # outright. Either way the operator now gets a choice instead of a toast.
        offered_universe = True
        candidates = universe
    elif (span is not None and span["ambiguous"] and universe
            and not any(c["platform"] == PLATFORM_GITHUB for c in candidates)):
        # Dead end 2 — a bare `#N` nothing could attribute. The clawgate
        # candidate STAYS FIRST so the common case is still one Enter away; the
        # universe is appended as the way to say "no, GitHub, this repo".
        candidates = candidates + universe

    if not candidates:
        # 🔴 SAY WHICH EMPTY THIS IS. A toast is now correct in exactly one
        # situation — the picker could not be SHOWN — and the three ways that
        # happens need three different next moves. Reporting them as one empty
        # is the silent-zero shape one layer up.
        return refuse(span, text, args)

    if args.print_only:
        for c in candidates:
            print(c["url"])
        return 0

    # 🔴 `and not offered_universe`: see PASS 3. One candidate is enough to open
    # only when that candidate is EVIDENCE about the reference — an explicit
    # owner, a measured checkout, a mapping hit, the pane's repo. A universe row
    # is an OPTION, and a host that happens to know exactly one repository must
    # not have that option opened for it.
    if len(candidates) == 1 and not offered_universe:
        return open_url(candidates[0]["url"])

    url = pick(candidates)
    return open_url(url) if url else 0


if __name__ == "__main__":
    raise SystemExit(main())
