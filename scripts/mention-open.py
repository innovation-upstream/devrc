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
                            only if that cannot or should not be shown, a
                            notification saying WHICH empty this is.
  0, and six digits      -> a NAMED toast. A six-digit `#N` is always a colour
                            literal here, so every row a picker could offer
                            would 404. See `colour_literal_offer`.

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
`-matching fuzzy`, so `talos-inf#12` is four keystrokes from `talos-infra`. The
old toast `no mention in the clicked text` read as a failure when it was the
guard working, and a guard that reads as a bug gets deleted by the next
maintainer — so every unresolvable shape now becomes a CHOICE.

⚠ EVERY SHAPE EXCEPT ONE. A SIX-DIGIT `#N` gets a named toast rather than a
picker, because on this host it is always a colour literal and every row a
picker could offer names an issue no repository has. That is not the old dead
end returning: the toast SAYS what it saw. See `colour_literal_offer`.

🔴 THIS IS NOT A RELAXATION OF THE NO-GUESSING RULE, IT IS ITS EXTENSION. A
picker is a CHOICE the operator makes; a guess is one this script makes for
them. Nothing below ever opens a URL the operator did not select, and dismissing
the picker still opens NOTHING — a universe row is never auto-opened even when
it is the only one, see `offered_universe`.

⚠ IT DEGRADES, IT DOES NOT DISAPPEAR. A toast is correct in exactly two cases:
the picker cannot be shown, or it SHOULD not be. The first is the universe being
missing, unreadable or empty, or `--print`/`--no-discovery` barring it by
contract. The second is a SIX-DIGIT `#N`, which is always a false positive here
(nothing this host references numbers past five digits) and whose every offered
row would name an issue no repository has — see `colour_literal_offer`. Either
way the toast names WHICH of those it is, because each needs a different next
move. A silent empty picker would be the same silent zero one layer up.

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
import time
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

# 🔴 THE MAPPING IS HAND-REGENERATED ONLY, AND NOTHING CONVERGES IT. There is no
# timer anywhere in `nix/` that runs `scripts/regen-known-repos.py`, so a
# repository created after the last run is INVISIBLE to every resolution path —
# and the symptom is the picker appearing for a name that ought to have resolved,
# which reads as "the picker is noisy" rather than as "my mapping is old".
#
# So the age is MEASURED and SURFACED, at both places the operator can see it:
# the note above the picker (`universe_note`) and the refusal body
# (`staleness_note`). It is a SIGNAL, not a repair — deliberately, and the
# alternative was weighed: a systemd-user timer would run `gh api user/repos`
# on a schedule, and `regen-known-repos.py` REFUSES below its 25-repo floor and
# exits 3, so a host without `gh auth` would take a failing unit and a failure
# toast on every fire. A permanently-red timer is worse than no timer. The
# signal fires at the exact moment the staleness bites, which is the click that
# did not resolve.
#
# Seven days is a week of repo-creating, not a tuned constant; it is pinned at
# two points (fresh and stale) rather than at a boundary, and it is not
# env-overridable because a run must not be able to excuse itself.
STALE_MAPPING_DAYS = 7

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

    So a number recovered here reaches the picker or a NAMED toast, and nowhere
    else. It is never handed to `resolve()`, never used to build a candidate
    that could satisfy the single-candidate auto-open, and never counted as a
    mention.

    ⚠ IT STILL RETURNS SIX DIGITS, AND THAT IS ON PURPOSE. `colour_literal_offer`
    is the caller that reads six digits as "a colour, not a reference" and routes
    it to a toast instead of a picker — the classification lives THERE, at one
    site, not in this recogniser. Narrowing this bound to `{1,5}` would break the
    seam it is pinned against (the Alacritty hint's own `{1,6}`) and leave the
    six-digit case with no number to name in its message.

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


def _fan_out(paths: list[Path]) -> list[str]:
    """`repo_of_checkout` for every path, IN THE SAME ORDER, across a thread pool.

    🔴 ORDER IS THE CONTRACT, not an implementation detail. The caller re-pairs
    this list with `paths` positionally, so a result list that is reordered,
    reversed or short by one maps every checkout onto its NEIGHBOUR's owner —
    `~/workspace/devrc` would answer `devrc#1291` with issue 1291 of some other
    organisation's repository. That is the confident-wrong-page failure this
    whole module is anchored against, and it is invisible to any fixture holding
    one checkout. Pinned by `test_the_fan_out_pairs_every_checkout_with_its_OWN`.

    The measurement it parallelises is ~100% waiting on a child process, which
    is why threads are the right tool and the GIL is not in the way: 100
    checkouts cost 0.149-0.184s serially and 0.026s across 16 threads.
    """
    from concurrent.futures import ThreadPoolExecutor  # see the import block
    with ThreadPoolExecutor(max_workers=_DISCOVERY_WORKERS) as pool:
        # `list(...)` INSIDE the `with`: `pool.map` is lazy, so returning the
        # iterator would hand the caller a generator over a shut-down pool.
        return list(pool.map(repo_of_checkout, paths))


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
    #
    # 🔴 AND IT DEGRADES TO SERIAL RATHER THAN VANISHING. `ThreadPoolExecutor`
    # raises `RuntimeError: can't start new thread` when the process or the box
    # is at its thread limit — this host routinely runs 100+ concurrent agent
    # worktrees — and the lazy import can raise `ImportError` on a broken
    # interpreter. Either one used to propagate out of `main()` into a DETACHED
    # process with no terminal, so the click did nothing at all: no toast, no
    # visible stderr, indistinguishable from a hint that was never wired up.
    # Serially, 100 checkouts cost ~0.18s — slower, and an answer.
    try:
        resolved = _fan_out(entries)
    except (ImportError, OSError, RuntimeError):
        resolved = [repo_of_checkout(p) for p in entries]
    for entry, full in zip(entries, resolved):
        # 🔴 `if full:` IS A GUARD, NOT A TIDY-UP. `repo_of_checkout` answers ""
        # for a checkout whose `git remote` failed or timed out; writing that ""
        # over `out[entry.name]` would DELETE a good row the generated mapping
        # had already supplied, silently un-resolving a name the host could
        # answer a moment ago. Pinned by
        # `test_an_UNREADABLE_checkout_does_not_ERASE_the_mappings_answer`.
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
    announced somewhere.

    🔴 `--` IS LOAD-BEARING AND WAS MISSING. Several refusal bodies begin with a
    flag NAME — "--print cannot show the repository picker", "--no-discovery
    resolves only what the text itself carries" — because naming the operator's
    own lever is the point. `notify-send` uses GNU getopt, which PERMUTES: an
    argument starting with `--` is parsed as an option wherever it sits.
    MEASURED with notify-send 0.8.8: `notify-send -a mention-open SUMMARY
    "--no-discovery …"` prints `Unknown option` and exits 1 with NO toast, while
    the same call with `--` exits 0 and shows one. And `check=False` swallows
    that exit code, so the failure is silent — a refusal that shows nothing is
    worse than the refusal it replaced. Pinned by
    `test_notify_send_is_given_a_double_dash_before_the_MESSAGE`."""
    print(f"mention-open: {summary} {body}".strip(), file=sys.stderr)
    try:
        subprocess.run(["notify-send", "-a", "mention-open", "--", summary, body],
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


def pick(candidates: list[dict], mesg: str = "") -> str:
    """Ask rofi which candidate to open. Returns the chosen URL, or "" if the
    operator dismissed the picker (which must open NOTHING).

    🔴 `mesg` IS WHERE THE DIAGNOSIS GOES, AND THE REASON IS THAT ROFI CANNOT
    TELL THE TWO EXITS APART. With `-no-custom` a name that matches nothing
    cannot be submitted, so "I typed `kubectl-neat` and the list went empty" and
    "I changed my mind" BOTH arrive back here as rofi exiting non-zero with no
    selection. There is no signal that separates them — not the exit code, not
    stdout — so a toast fired after a dismissal would fire after every dismissal,
    which is the noise this handler must not make. The diagnosis therefore goes
    where it costs nothing and is read BEFORE the choice: a line above the list,
    on the operator's own screen, which is the one surface the universe may
    already reach. See `universe_note` for what it may say.

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
    # `-mesg` renders as PANGO MARKUP, and the note embeds the clicked text. A
    # stray `<` or `&` there would break the whole line's rendering, so the three
    # markup-significant characters are escaped. Rows are NOT markup (there is no
    # `-markup-rows`), so they need no such treatment.
    #
    # 🔴 SPLICED WITH `*`, NEVER CONCATENATED ONTO A NAME. The argv must stay a
    # LIST LITERAL whose first element is the constant `"rofi"`, because
    # `test_mention_open.py`'s AST ledger of spawnable executables reads exactly
    # that; handing `subprocess.run` a variable reports `<computed>` and the
    # ledger — the guard that stops a network call being re-added — goes red for
    # a reason that has nothing to do with what is being spawned.
    mesg_argv = ["-mesg", (mesg.replace("&", "&amp;")
                               .replace("<", "&lt;")
                               .replace(">", "&gt;"))] if mesg else []
    try:
        r = subprocess.run(
            ["rofi", "-dmenu", "-i", "-matching", "fuzzy",
             "-p", "mention", "-theme", ROFI_THEME,
             "-format", "s", "-no-custom", *mesg_argv],
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


def mapping_age_days(path: Path | None = None) -> float | None:
    """How long ago the repo mapping was written, in days, or None.

    🔴 ONE MEASUREMENT, TWO READERS. `staleness_note` puts it in a refusal body
    and `universe_note` puts it above the picker; open-coding `st_mtime` at both
    would be the same predicate at two sites, wrong at one of them. None means
    "could not be measured" — absent, or a stat that failed — and every caller
    must treat that as UNKNOWN, never as fresh.
    """
    path = path or KNOWN_REPOS_PATH
    try:
        return max(0.0, (time.time() - path.stat().st_mtime) / 86400.0)
    except OSError:
        return None


def staleness_note(path: Path | None = None) -> str:
    """"the repo mapping is N days old — …" when it is, else "".

    🔴 IT IS A SIGNAL FOR A FILE NOTHING CONVERGES. See `STALE_MAPPING_DAYS`.
    A repository created since the last `regen-known-repos.py` run cannot be
    resolved by ANY path here, and the only symptom is a picker appearing where
    a resolution used to — so the refusal says how old the mapping is and what
    to run. It names a COUNT and a CONSTANT PATH, never a row.
    """
    age = mapping_age_days(path)
    if age is None or age < STALE_MAPPING_DAYS:
        return ""
    return (f"the repo mapping is {age:.0f} days old and nothing regenerates it "
            f"— a repository created since then cannot resolve; run "
            f"scripts/regen-known-repos.py")


def universe_note(subject: str, offered: int, path: Path | None = None) -> str:
    """The line shown ABOVE the fuzzy picker when the universe is the last resort.

    🔴 IT EXISTS BECAUSE A DISMISSED PICKER CANNOT BE READ. `pick()` explains
    why: rofi with `-no-custom` reports "nothing matched what I typed" and "I
    changed my mind" identically, so the only honest place to say "this host has
    no repository called `kubectl-neat`" is BEFORE the choice, not after it. A
    real dismissal still opens nothing and still says nothing.

    🔴 IT NAMES THE CLICKED TEXT, A COUNT AND A DATE — NEVER A ROW. `subject` is
    what the OPERATOR typed or clicked, which they already have; `offered` is a
    cardinality; the date is the mapping file's mtime. None of the three is a
    repository name from the universe, and this string reaches rofi only — it is
    never handed to `notify()`, which would put it on stderr.
    """
    age = mapping_age_days(path)
    if age is None:
        when = "no mapping file on this host"
    else:
        # Derived from the SAME reading as the age, not a second `stat()`: the
        # file can be regenerated between two calls, and a date that disagreed
        # with the age beside it would read as a bug in the note.
        stamp = time.strftime("%Y-%m-%d", time.localtime(time.time() - age * 86400))
        when = f"mapping generated {stamp} ({age:.0f}d ago)"
    return (f"nothing here knows {subject} — pick a repository, or dismiss. "
            f"{offered} offered · {when} · refresh with "
            f"scripts/regen-known-repos.py")


def colour_literal_offer(span: dict | None, text: str) -> str:
    """The SIX-DIGIT number in `text` that is a colour literal, not a reference —
    or "" for everything else.

    🔴 SIX DIGITS IS ALWAYS A FALSE POSITIVE ON THIS HOST, AND THAT IS A
    MEASUREMENT RATHER THAN A HUNCH. `mention_scan`'s `_NUM` is `\\d{1,5}`
    exactly because nothing here — no clawgate task, no GitHub issue in any repo
    the operator touches — reaches six digits; devrc itself is in the 1300s. So
    a six-digit `#N` that the scanner refused cannot be a reference, and every
    row a universe picker would offer for it names an issue NO repository has.
    Raising a several-hundred-row window to choose between hundreds of URLs that
    all 404 is not a choice, it is a wall with no door.

    ⚠ AND THE ORIGINAL CASE IS THE PROOF. `#282828` is the operator's own gruvbox
    background literal — it is written in `nix/programs/alacritty/default.nix`,
    the very file that configures this hint. The right answer to clicking it is
    "that is a colour", said once, not a picker.

    🔴 THIS DOES NOT WEAKEN THE AUTO-OPEN RULE, IT MAKES IT UNREACHABLE. Six
    digits could never auto-open (`offered_universe` suppresses the shortcut for
    every universe row, and that guard is UNCHANGED); now it is not offered at
    all. Every OTHER unresolvable shape keeps the picker — `kubectl-neat#1`,
    `talos-inf#12`, a bare `#N` nothing attributes. This is the six-digit case
    and nothing else.

    ⚠ IT ASKS FOR `span is None` FIRST. A number the scanner ACCEPTED is a
    reference by definition, whatever its length, and must not be second-guessed
    here.
    """
    num = offer_number(text) if span is None else ""
    return num if len(num) == 6 else ""


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
    colour = colour_literal_offer(span, text)
    if args.no_discovery:
        # The flag is the whole cause and the operator's own lever. The mapping
        # is never even READ under it, so blaming the mapping would send them to
        # regenerate a file that was not involved.
        why = "--no-discovery resolves only what the text itself carries"
    elif colour:
        # 🔴 SIX DIGITS — see `colour_literal_offer`. This is a NAMED answer, not
        # a dead end, and it is the reason the picker is not raised.
        why = (f"#{colour} is six digits — that looks like a colour literal, "
               f"not a reference; nothing here numbers past five digits")
        advice = "if you did mean a reference, write it as owner/repo#N"
    else:
        # 🔴 BOTH CAUSES, NOT WHICHEVER ONE IS CHECKED FIRST. `--print` bars the
        # picker by contract AND the mapping may be absent, and the flag used to
        # win purely by being tested first — so `--print zzz#12` on a host with
        # no mapping at all blamed the flag and never mentioned
        # `regen-known-repos.py`. Both are true; only one is ACTIONABLE, and
        # that is the one that was being dropped. In a handler whose stated
        # discipline is "say WHICH empty this is", reporting the un-actionable
        # half alone is the silent zero wearing a name.
        reason = universe_reason()
        if args.print_only:
            # 🔴 A DECIDED CONTRACT, not an oversight. `--print` is
            # non-interactive; the answer to "which of your 369 repositories did
            # you mean?" is a question, and printing all of them is not an
            # answer either.
            why = ("--print cannot show the repository picker — there is nobody "
                   "to ask")
            if extra := (reason or staleness_note()):
                why = f"{why}; also {extra}"
        else:
            why = reason or "no repository owner is known for it"
            # A mapping that PARSED and holds rows can still be months old, and
            # that is a different, ADDITIVE fact — appended, never substituted
            # for the primary one, for the same reason the advice is.
            if not reason and (stale := staleness_note()):
                why = f"{why}; {stale}"
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
    # strictly apart from anything `resolve()` produced. For a text the scanner
    # refuses outright — `&#123;`, `##370` — it recovers the number so the
    # operator gets a choice instead of `no mention in the clicked text`. It is
    # never merged into `span` and never satisfies the auto-open below.
    offer_num = offer_number(text)

    # 🔴 SIX DIGITS IS ANSWERED, NOT ASKED ABOUT — see `colour_literal_offer`.
    # It is computed HERE, once, and read by both the measurement guard below
    # and by `refuse()`, because a predicate open-coded at two sites is wrong at
    # one of them. Skipping the measurement is the point as well as the window:
    # building a several-hundred-row universe that will not be shown is latency
    # the operator pays on a click that already has its answer.
    colour = colour_literal_offer(span, text)

    # PASS 2 — the LOCAL measurement, only when the text did not answer it.
    # Discovery costs a concurrent `git remote` fan-out plus a tmux round-trip
    # (~30ms here), and paying that before opening a link that needed neither is
    # latency the operator feels on every single click.
    #
    # 🔴 IT RUNS FOR `span is None` TOO. It used to short-circuit straight to a
    # refusal there, and the universe the picker needs is built by this pass.
    # Guarded on `offer_num` so a text carrying no number at all — impossible
    # from a click, see `offer_number` — still costs nothing, and on `colour` so
    # the one shape that has its own answer does not pay for a universe nobody
    # will see.
    #
    # ⚠ SCOPE OF THE `span is None` ARM, STATED HONESTLY BECAUSE IT NARROWED.
    # The scanner refuses a `#N` for two reasons: six or more digits, and a
    # forbidden character immediately before the `#` (`&#123;` an HTML entity,
    # `##370` a heading run). `colour` now takes the first, so this arm covers
    # the second — which the Alacritty hint CANNOT produce, because it matches
    # from the `#` and hands over `#123` rather than `&#123;`. So it is a
    # command-line surface now, not a click one. It is kept because that surface
    # is real and the alternative is a dead end on it.
    discovered: dict = {}
    unresolved = span is None or span["ambiguous"] or not candidates
    if (unresolved and not args.no_discovery and not colour
            and (span is not None or offer_num)):
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
    # failure this whole handler is anchored against. Measured during
    # development: the first version of this pass did open it, and the test that
    # caught it was the one asserting a picker appeared.
    #
    # ⚠ IT IS NOT WHAT HANDLES `#282828` ANY MORE, AND THAT NOTE IS KEPT BECAUSE
    # THIS PARAGRAPH USED TO SAY IT WAS. A six-digit number never reaches here
    # now — `colour` bars it above — so this guard covers the case that remains:
    # a host whose universe holds exactly ONE repository answering an
    # unresolvable `repo#N`. Do not delete it on the reasoning that the
    # six-digit path no longer needs it.
    offered_universe = False
    may_offer_universe = (not args.print_only and not args.no_discovery
                          and not colour)
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

    # 🔴 THE NOTE IS ATTACHED ONLY WHEN THE UNIVERSE IS THE ANSWER. A picker
    # over real candidates — the clawgate/GitHub pair for a bare `#N` — is not a
    # dead end and needs no explanation; adding one there would put a line of
    # apology above the single most common interaction in this handler.
    mesg = (universe_note(span["raw"] if span is not None else text,
                          len(candidates))
            if offered_universe else "")
    url = pick(candidates, mesg=mesg)
    return open_url(url) if url else 0


def guarded_main(argv: list[str] | None = None) -> int:
    """`main()` with a last-resort reporter around it. Always the entry point.

    🔴 AN UNCAUGHT EXCEPTION HERE IS A SILENT CLICK. Alacritty spawns this
    DETACHED: there is no terminal, so a traceback goes to a stderr nobody will
    ever read, and the operator sees a click that did nothing — the exact shape
    `notify()`'s docstring exists to prevent, arriving one level above it.

    The case that is not hypothetical: `ThreadPoolExecutor` raises
    `RuntimeError: can't start new thread` when the box is at its thread limit,
    and this host routinely runs 100+ concurrent agent worktrees.
    `discover_repos` degrades to a serial fan-out for exactly that, but a guard
    that only covers the failure somebody imagined is not a guard — argparse,
    a malformed `known_repos.json` racing a regeneration, a `Path` that vanishes
    mid-`stat`, all land here too.

    ⚠ `except Exception`, DELIBERATELY BROAD AND DELIBERATELY NOT BARE:
    `KeyboardInterrupt` and `SystemExit` derive from `BaseException` and must
    still terminate. This returns 1 rather than re-raising, because a non-zero
    exit that ALSO said something is the whole difference being closed here.
    """
    try:
        return main(argv)
    except Exception as exc:  # noqa: BLE001 — see the docstring
        notify("mention-open failed", f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(guarded_main())
