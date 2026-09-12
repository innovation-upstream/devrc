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
  1 openable candidate   -> xdg-open it, UNLESS the repository was guessed from
                            the tmux pane — see `repo_source` in `main()`.
  2+ (a bare `#N`)       -> the picker, one row per platform, showing the URL.
  any of the above whose repository was GUESSED
                         -> the same rows, FIRST, with the fuzzy universe
                            appended beneath them so the guess is overridable.
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
REFUSE. Every repository this host knows about goes into a FUZZY picker (fzf in
a float terminal), so `talos-inf#12` is four keystrokes from `talos-infra`. The
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
clicked text — never a row from the universe. ⚠ THE TELEMETRY SINK ADDED IN
2026-09 DOES NOT WEAKEN THIS. It reports the one row the operator opened and
three scalars about the list (rank, size, class); `click_dims` is never handed
the candidate list at all, so there is no argument through which an offered row
could reach it.

🔴 THE SAME RULE COVERS TWO MORE FILES THIS HANDLER NOW TOUCHES, and both are
0600 under the same directory, outside every checkout:
  * `known_ranges.json` — `{"owner/repo": <highest issue-or-PR number>}`, whose
    KEYS are the private names (the mirror of the two files above, where the
    values are). Written by `regen-known-repos.py`.
  * `picks.jsonl`       — one `{"t","repo","n","via"}` line per repository the
    OPERATOR opened, from the picker AND from the single-candidate auto-open,
    tagged with which. Written HERE, by `record_pick`.

🔴 AND ONE SINK THAT IS *NOT* A FILE: the handler reports the OUTCOME of a click
to the activity-telemetry spool (`source=tool`, `kind=invocation`,
`text=mention-open`). The rule above is unchanged and the distinction is the
whole of it — what leaves this process is what the operator TOUCHED (the repo
whose page opened, the rank their row sat at, how many rows were offered), never
what they were OFFERED. See the CLICK-PATH TELEMETRY block.

ORDERING, AND WHY IT IS ONLY ORDERING
The picker offers ~392 rows for a clicked `#N`, and they used to arrive in one
fixed alphabetical order whatever the number was. MEASURED over a 120-repo
sample of this host's universe: **54 (45%) have no issues and no pull requests
at all**, 40 top out at 1-9, 19 at 10-99, 5 at 100-999 and 2 at 1000+. So `#1291`
is plausible for about two repositories and `#12` for about sixty-six, and
nearly half the list can satisfy NO numeric click. `order_universe` sorts on
that (see it for the classes and for why a learned preference can never cross
one).

🔴 IT RANKS, IT NEVER FILTERS, AND IT NEVER OPENS. Hiding a row on a day-old
snapshot is unrecoverable from inside the picker — the operator cannot type at a
row that is not there — while ranking it last costs a scroll. And the ordering
is not evidence about the REFERENCE, so it cannot promote a row past the
`offered_universe` guard: a universe row is still never auto-opened, however
plausible it looks. Both properties are pinned.
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
    GITHUB_REF_URL,
    PLATFORM_CLAWGATE,
    PLATFORM_CLICKUP,
    PLATFORM_GITHUB,
    OWNER_REPO_VALUE_RE as _OWNER_REPO_RE,
    # 🔴 IMPORTED, NOT SPELLED. `main()` branches on the attribution ladder's
    # weakest rung, and a hand-written "default" here would be a second copy of
    # a vocabulary `mention_scan` owns — silently inert the day that module
    # renames it, with every suite green because the branch simply stops firing.
    SOURCE_DEFAULT,
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

# The generated PICKER UNIVERSE — a JSON list of `owner/repo`, same directory,
# same 0600, same disclosure rules. It answers a DIFFERENT question from the
# mapping beside it: the mapping says what a bare name MEANS, this says what the
# operator might want OFFERED. `regen-known-repos.py`'s docstring carries the
# full argument and the measurement.
#
# 🔴 THE ENV OVERRIDE IS NOT A CONVENIENCE — IT IS WHAT LETS TESTS REDIRECT A
# SUBPROCESS. `MENTION_OPEN_KNOWN_REPOS` exists for exactly that reason (nine
# tests were measured reading the operator's real mapping, two of them
# DISCLOSURE tests, and a `monkeypatch.setattr` on the module constant is
# invisible to a child process that re-imports this file). A new host-state file
# without the same door would re-open the same hole for the universe.
KNOWN_UNIVERSE_PATH = Path(
    os.environ.get("MENTION_OPEN_KNOWN_UNIVERSE")
    or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    / "mention-open" / "known_universe.json")

# The generated RANGE TABLE — `{"owner/repo": <highest issue-or-PR number>}`,
# lowercased keys, same directory, same 0600, same disclosure rules. It answers
# a THIRD question: given a clicked `#N`, could this repository plausibly have
# it? `regen-known-repos.py` writes it from batched GraphQL; its absence is a
# supported state and means "every repo is UNKNOWN".
#
# 🔴 SAME ENV DOOR AS ITS TWO SIBLINGS, FOR THE SAME MEASURED REASON — a
# `monkeypatch.setattr` on a module constant is invisible to the child process a
# subprocess test spawns, and nine tests were once measured reading the
# OPERATOR'S REAL files because of it. A new host-state file without this door
# re-opens that hole.
KNOWN_RANGES_PATH = Path(
    os.environ.get("MENTION_OPEN_KNOWN_RANGES")
    or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    / "mention-open" / "known_ranges.json")

# The operator's own PICK LOG — append-only JSONL, one `{"t","repo","n"}` object
# per repository chosen from the picker. 🔴 THIS IS THE ONE FILE IN THE SET THIS
# HANDLER *WRITES*, and it names private repositories exactly as the others do:
# 0600, parent 0700, outside every checkout, never logged, never in a toast.
PICKS_PATH = Path(
    os.environ.get("MENTION_OPEN_PICKS")
    or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    / "mention-open" / "picks.jsonl")

# 🔴 A TIMER NOW CONVERGES THE MAPPING, AND THIS PARAGRAPH USED TO SAY THE
# OPPOSITE — do not re-derive the old reasoning from a stale copy of it. It read
# "THE MAPPING IS HAND-REGENERATED ONLY … There is no timer anywhere in `nix/`",
# and it argued a timer would be WORSE than the signal: `regen-known-repos.py`
# had one failure code, so a host without `gh auth` would take a failing unit
# and a failure toast on every fire, and a permanently-red timer is worse than
# no timer. That objection was correct, and it was answered rather than
# overruled — the generator now exits 4 for "not configured on this host" and 3
# only for a real failure, and `mention-known-repos-refresh.service` sets
# `SuccessExitStatus=4`. An unauthenticated host is quiet; a broken run toasts.
#
# `nix/home.nix` → `systemd.user.timers.mention-known-repos-refresh`, daily.
#
# 🔴 THE SIGNAL BELOW STAYS, AND IS NOW A DEADMAN RATHER THAN A REMINDER. Its
# job changed: it used to say "you never ran the generator", which the operator
# could act on directly. It now says "the timer has not landed a fresh file in
# a week", whose causes are a unit that is failing, a host that was off, or a
# token that expired — none of which the operator would otherwise see, because
# a stale mapping's only symptom is the picker appearing where a resolution used
# to. A converged file makes the note unreachable in the ordinary case, which is
# the point; it is NOT dead code, and the tests reach it by ageing the file.
#
# Its PRIMARY home is the note above the picker (`universe_note`), because that
# is the path a stale mapping actually produces — the click that did not
# resolve. `staleness_note` covers the two refusals: `--print`, which cannot
# show a picker at all, and the narrow shadowed-mapping arm documented in
# `refuse()`. Stating that split here rather than "the refusal body" is
# deliberate: the vaguer wording read as "every refusal past 7 days", which is
# not what the code does.
#
# Seven days is a week of repo-creating, not a tuned constant; it is pinned at
# two points (fresh and stale) rather than at a boundary, and it is not
# env-overridable because a run must not be able to excuse itself. ⚠ It is
# deliberately NOT re-tuned to the timer's daily period: a 2-day threshold would
# fire on one missed run, and a laptop that spends a weekend closed is not a
# fault. Seven days means "several consecutive runs did not happen".
STALE_MAPPING_DAYS = 7

# ⚠ `ROFI_THEME = "gruvbox-dark-hard"` USED TO BE HERE and is GONE with the rofi
# call it configured. The picker's palette now comes from the terminal it runs
# in: `--color=16` tells fzf to use the sixteen ANSI colours, which alacritty
# already paints gruvbox (nix/programs/alacritty/default.nix). Same argument,
# one fewer copy of the theme name — see `PICKER_SH`.

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
# Pure helpers (unit-tested without touching git, tmux, the picker or a browser)
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


def repo_universe(repos: dict | None,
                  universe: list[str] | None = None) -> list[str]:
    """Every distinct `owner/repo` worth offering, sorted — the picker's rows.

    TWO SOURCES, UNIONED, AND THE UNION IS THE WHOLE FIX:
      * `universe` — the generated `known_universe.json`, which is every repo the
        operator owns or collaborates on, unfiltered.
      * `repos` — `discover_repos()`'s mapping: the generated resolution mapping
        laid under the real local checkouts.

    🔴 THIS USED TO READ `repos.values()` ALONE, AND THAT SILENTLY INHERITED THE
    MAPPING'S FILTERS. The mapping exists to answer "what does the bare name
    `foo` mean?", so it drops a repo whose issues are disabled and drops a name
    two owners share — both correct for resolution, both wrong for a picker
    whose rows are fully-qualified and where nothing opens without a selection.
    MEASURED 2026-09-07: 388 repos on this host, 339 offered, **53 unreachable**
    by typing at the picker. Reading a display list out of a lookup table is the
    general shape; the two questions differ and so must the filters.

    ⚠ THE UNION IS STRICTLY WIDENING, NEVER NARROWING, and that is deliberate.
    A local checkout can name a repo the API never returned (a clone of somebody
    else's repository), and the generated universe can name one that is not on
    this disk. Neither source can REMOVE a row the other contributed — there is
    no precedence rule here because there is no conflict to resolve: this is a
    list of things to offer, not a mapping from a name to one answer.

    Rows that are not exactly `owner/repo` are dropped from BOTH sources — they
    build a URL that 404s while looking authoritative — and the result is deduped
    case-insensitively, because `acme/Widget` and `acme/widget` are one
    repository on GitHub and two identical-looking rows in the picker.

    🔴 THE RETURN VALUE NAMES PRIVATE REPOSITORIES. It may reach the operator's
    picker window and nothing else — no log line, no notification body, no
    telemetry row, no test fixture. See the module docstring.
    """
    by_key: dict[str, str] = {}
    # Mapping values first, so a name this host has ON DISK keeps the spelling
    # its remote uses; the generated universe then adds everything else.
    for v in (repos or {}).values():
        if isinstance(v, str) and _OWNER_REPO_RE.match(v):
            by_key.setdefault(v.lower(), v)
    for v in (universe or []):
        if isinstance(v, str) and _OWNER_REPO_RE.match(v):
            by_key.setdefault(v.lower(), v)
    return sorted(by_key.values(), key=str.lower)


# --------------------------------------------------------------------------- #
# TIER A — PLAUSIBILITY. Which of these repositories could have a `#N` at all?
#
# The four classes, most plausible first. They are ORDINALS and the numbers are
# read by `sorted`, so the sequence is the behaviour — not a label.
#
# 🔴 `IMPOSSIBLE` IS RANKED LAST, NOT EXCLUDED, AND THAT IS THE WHOLE POSTURE.
# The table is a DAY-OLD SNAPSHOT: a repo that had no references yesterday can
# have `#1` today, and a repo whose head was `#40` can be at `#45`. Dropping a
# row on that evidence is unrecoverable from inside the picker — fzf cannot
# match a line that is not in the list, so the operator's only move is to
# dismiss and type a URL by hand, which is the dead end this whole handler
# exists to remove. Ranking it last costs a scroll. Pinned by
# `test_an_IMPOSSIBLE_repo_is_RANKED_LAST_and_never_DROPPED`.
CLASS_PLAUSIBLE = 0     # max_ref >= N — this repo has reached the number
CLASS_BELOW = 1         # 0 < max_ref < N — it has references, just not that far
CLASS_UNKNOWN = 2       # no entry — never measured, or measured and unanswerable
CLASS_IMPOSSIBLE = 3    # max_ref == 0 — no issues and no pull requests at all

# 🔴 PINNED TWO-WAY by `test_the_plausibility_CLASS_set_is_pinned_and_ORDERED`.
# `order_universe` sorts on these, so a fifth class added later would take a
# position nobody chose — the same defect the `PICKED_OUTCOMES` ledger below
# exists for, one concept along.
PLAUSIBILITY_CLASSES = (CLASS_PLAUSIBLE, CLASS_BELOW, CLASS_UNKNOWN,
                        CLASS_IMPOSSIBLE)

# What `ordering_state` reports, and what the picker header says. A THREE-WAY
# split rather than a boolean, because "there is no table yet" and "the table
# stopped being refreshed" need different next moves from the operator: the
# first is a host that has not run the new generator, the second is a unit that
# is failing.
ORDER_APPLIED = "applied"
ORDER_NO_TABLE = "no-table"
ORDER_STALE = "stale"
ORDER_STATES = (ORDER_APPLIED, ORDER_NO_TABLE, ORDER_STALE)

# TIER B — the operator's own picks. Three caps, and each closes a different way
# for old data to mislead:
#   * AGE — a repository that was the answer three months ago may not exist now.
#   * COUNT — how many rows are PARSED and SCORED on the picker path. ⚠ THIS
#     USED TO SAY "an unbounded file would be read in full", WHICH WAS FALSE and
#     an audit caught it: `load_picks` reads the whole file and then slices, so
#     the cap has never bounded the READ. What bounds the file is
#     `record_pick`'s compaction (see `PICKS_COMPACT_AT`); this cap bounds the
#     work done per click.
#   * HALF-LIFE — inside the window, last week outweighs last month smoothly
#     rather than at a cliff.
# None of them is tuned; they are "a season", "more than anyone clicks", and "a
# month". They are not env-overridable, for the same reason `STALE_MAPPING_DAYS`
# is not: a run must not be able to excuse itself.
PICKS_MAX_AGE_DAYS = 90.0
PICKS_MAX_ROWS = 500
PICKS_HALF_LIFE_DAYS = 30.0

# 🔴 WHEN THE LOG IS TRIMMED, AND WHY IT IS TRIMMED AT ALL. `record_pick` only
# APPENDS and the age cap is applied on READ, so without this the file grows
# forever — a 0600 file naming private repositories, accumulating rows no reader
# will ever use. An audit found the comment above claiming the count cap
# prevented that; it never did.
#
# Trimming at a MULTIPLE of the read cap rather than at the cap itself is the
# whole point: rewriting on every pick would be a write amplification, while
# 2x means the rewrite happens once per `PICKS_MAX_ROWS` picks and the tail it
# keeps is always at least what a reader would use.
PICKS_COMPACT_AT = PICKS_MAX_ROWS * 2

# 🔴 WHICH CODE PATH PRODUCED A PICK. Until 2026-09-11 the log recorded only the
# rows the operator selected in the PICKER, and `main()`'s single-candidate
# shortcut — the common, fast path — returned without recording anything. So
# Tier B learned from the AMBIGUOUS clicks alone: every click the handler could
# answer outright was invisible to it, and the scores described the minority of
# clicks that were hard rather than what this operator means by a number.
#
# Both paths are recorded now, and the row says WHICH, because they are not the
# same evidence. A picker pick is a CHOICE among rows the operator read; an
# auto-open is the handler's own resolution, which the operator only implicitly
# ratified by not complaining. A later scoring change may weight or exclude one
# of them — the tag is what makes that possible without having thrown the data
# away. Nothing reads it today: `pick_scores` is unchanged and counts every row
# the same, which is the pre-change behaviour for picker rows and strictly more
# data for auto rows.
PICK_VIA_AUTO = "auto"
PICK_VIA_PICKER = "picker"

# 🔴 WHAT AN UNTAGGED ROW BECOMES, AND WHY IT IS NOT BACK-LABELLED `picker`.
# Rows written before the tag existed carry no `via` at all (7 of them on the
# laptop when this shipped; the workbench had no log). They were in FACT picker
# picks — that was the only writer — but the ROW does not say so, and a reader
# cannot tell a pre-change row from a future writer that forgot the field. So
# they load as `unknown`: a third value a consumer must handle deliberately,
# rather than a claim about provenance the data cannot support. An unrecognised
# value (a corrupt row, or a tag written by a NEWER version of this handler)
# lands here too — it is never a reason to DROP the row, because `repo`/`n`/`t`
# are what the scoring reads and those are still good.
PICK_VIA_UNKNOWN = "unknown"

# What `record_pick` may WRITE. Deliberately narrower than what `load_picks` may
# RETURN — a writer must name one of the two real paths, while a reader must be
# total over whatever is on disk.
PICK_VIA_RECORDED = (PICK_VIA_AUTO, PICK_VIA_PICKER)
# What `load_picks` may return. Pinned two-way by
# `test_the_pick_VIA_vocabulary_is_pinned_two_way`.
PICK_VIA_VALUES = (PICK_VIA_AUTO, PICK_VIA_PICKER, PICK_VIA_UNKNOWN)


# How far apart two reference numbers have to be before proximity stops helping.
# `#1290` and `#1291` in one repo are the same week's work; `#3` and `#1291` are
# not. A soft 1/(1+d/SCALE) curve rather than a window, so nothing falls off a
# cliff — the bonus is in (0, 1] and merely REORDERS repos that already share a
# Tier A class.
PICKS_PROXIMITY_SCALE = 100.0


def universe_candidates(num: str, universe: list[str]) -> list[dict]:
    """One openable GitHub candidate per repository in `universe`.

    These are what turns a dead end into a choice: the operator types a few
    characters of the repo name into the fuzzy picker instead of reading a
    refusal. Nothing here is opened without a selection.
    """
    return [{"platform": PLATFORM_GITHUB, "id": num,
             "url": GITHUB_REF_URL.format(repo=full, id=num)}
            for full in universe]


def plausibility_class(num: str, max_ref: int | None) -> int:
    """Which of the four classes a repository falls in for a clicked `#num`.

    `max_ref is None` is UNKNOWN — the table has no entry, because the repo was
    added since the last refresh, or the GraphQL alias came back `null`, or
    there is no table at all.

    🔴 `None` AND `0` ARE DIFFERENT ANSWERS AND THIS IS WHERE THAT IS DECIDED.
    `0` is the strongest signal in the scheme — it is the only value that says a
    numeric reference is impossible here — so collapsing "I could not ask" into
    it would rank a perfectly good repository below every measured one on no
    evidence at all. `regen-known-repos.py` keeps them apart on the write side
    by OMITTING an unanswered repo rather than writing a 0; this keeps them
    apart on the read side.
    """
    if max_ref is None:
        return CLASS_UNKNOWN
    try:
        n = int(num)
    except (TypeError, ValueError):
        # A non-numeric subject cannot be compared to a range at all. UNKNOWN is
        # the honest class: it is neither ruled in nor ruled out, and it leaves
        # the order alone rather than inventing one.
        return CLASS_UNKNOWN
    if max_ref <= 0:
        return CLASS_IMPOSSIBLE
    return CLASS_PLAUSIBLE if max_ref >= n else CLASS_BELOW


def load_known_ranges(path: Path | None = None) -> dict[str, int]:
    """{"owner/repo" (lowercased): highest ref} from the generated table, or {}.

    🔴 EVERY failure is {} — absent, unreadable, malformed, wrong shape, wrong
    value type. Identical posture to `load_known_repos` and
    `load_known_universe`, for the identical reason: this runs on a detached
    click handler with nowhere to print a traceback, and an empty table means
    "every repo is UNKNOWN", which is exactly the pre-change ordering. A
    corrupted file must cost the operator a worse ORDER, never a dead click.

    ⚠ A DICT OF INTS, AND THE VALUE CHECK IS NOT A FORMALITY: `json.loads` of
    the universe file beside it returns a LIST, and a `--ranges-path`/
    `--universe-path` mix-up at generation time would otherwise be read as a
    table of nothing. `bool` is rejected explicitly — it is an `int` subclass in
    Python, so `True` would otherwise sail in as the number 1.
    """
    # 🔴 RESOLVED AT CALL TIME, NOT BOUND AS A DEFAULT — the defect that made
    # `load_known_repos`' own override test inert. See its comment.
    path = path or KNOWN_RANGES_PATH
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k.lower(): v for k, v in raw.items()
            if isinstance(k, str) and _OWNER_REPO_RE.match(k)
            and isinstance(v, int) and not isinstance(v, bool) and v >= 0}


def ranges_age_days(path: Path | None = None) -> float | None:
    """How long ago the range table was written, in days, or None.

    Separate from `mapping_age_days` and measured on its OWN file rather than
    inferred from the mapping's: the two are written by one run today, but a
    host deployed before the ranges leg existed has a fresh mapping and NO
    table, and reading one mtime for the other would report that host as having
    a fresh range table. None means "could not be measured" and every caller
    must treat it as UNKNOWN, never as fresh.
    """
    path = path or KNOWN_RANGES_PATH
    try:
        return max(0.0, (time.time() - path.stat().st_mtime) / 86400.0)
    except OSError:
        return None


def ordering_state(ranges: dict[str, int], age: float | None) -> str:
    """One of `ORDER_STATES`, from the table and its age. No I/O.

    🔴 STALE MEANS THE TABLE IS IGNORED, NOT TRUSTED-WITH-A-WARNING. Past
    `STALE_MAPPING_DAYS` the daily refresh has missed SEVERAL runs, and the two
    misclassifications that follow point in opposite directions: a repo that has
    advanced past `N` is filed BELOW, and a repo that gained its first reference
    is filed IMPOSSIBLE. Ordering on that is worse than not ordering — it is
    confident and wrong — while ordering on nothing is merely what shipped
    before. So the handler degrades to the old order and SAYS SO above the
    picker, which is the same posture the mapping's own staleness note takes.

    ⚠ AN EMPTY TABLE IS `no-table`, WHATEVER THE MTIME SAYS. A file that exists
    and parses to nothing orders nothing, and reporting it as `applied` would
    tell the operator the rows were ranked when they were not.

    ⚠ AN UNMEASURABLE AGE OVER A NON-EMPTY TABLE IS `stale`, NOT `no-table`.
    `None` means "could not be measured" and every caller must treat it as
    UNKNOWN rather than fresh — the same contract `mapping_age_days` states —
    and the operator-facing difference is real: `no-table` sends them to run the
    generator, which would not help when the table is sitting right there.
    """
    if not ranges:
        return ORDER_NO_TABLE
    if age is None or age >= STALE_MAPPING_DAYS:
        return ORDER_STALE
    return ORDER_APPLIED


def pick_via(row: dict) -> str:
    """Which path produced a pick row — one of `PICK_VIA_VALUES`. Pure, total.

    🔴 TOTAL ON PURPOSE, AND THAT IS THE BACKWARDS-COMPATIBILITY RULE IN ONE
    PLACE. Every way a row can fail to name a path it wrote — the field absent
    (a row written before the tag existed), the wrong type, a value this version
    does not know (a NEWER writer) — is `unknown`. None of them is a reason to
    discard the row: `load_picks` already decided the row is well-formed on the
    three fields the scoring actually reads, and dropping it over a label would
    silently delete history the operator earned.

    🔴 ONE DEFINITION, because `load_picks` is not the only reader that will
    want this. A predicate open-coded at two sites is wrong at one of them.
    """
    via = row.get("via") if isinstance(row, dict) else None
    return via if via in PICK_VIA_VALUES else PICK_VIA_UNKNOWN


def load_picks(path: Path | None = None, now: float | None = None) -> list[dict]:
    """The operator's recent picks as
    `[{"t": float, "repo": str, "n": int, "via": str}]`.

    Newest LAST, capped by `PICKS_MAX_ROWS` and filtered to
    `PICKS_MAX_AGE_DAYS`. 🔴 EVERY failure is [] — absent, unreadable, and
    per-line, a malformed row is SKIPPED rather than killing the file. This is
    an append-only log written by a detached handler that can be killed
    mid-write; one torn last line must not cost the whole history.

    ⚠ THE CAP IS APPLIED TO THE TAIL, NOT THE HEAD. The file grows at one line
    per pick, so the rows that matter are the last ones; taking the first 500
    would freeze the preference at whatever the operator liked when the file was
    new and never move again.

    🔴 `via` IS ALWAYS PRESENT ON THE WAY OUT AND NEVER REQUIRED ON THE WAY IN.
    A row that does not name a path is `unknown`, not dropped — see `pick_via`,
    which owns that rule. Every caller therefore gets a four-key row whatever the
    file's vintage, so nothing downstream has to `.get` around a missing field.
    """
    path = path or PICKS_PATH
    now = time.time() if now is None else now
    try:
        lines = path.read_text().splitlines()
    except (OSError, ValueError):
        # 🔴 `ValueError` IS NOT REDUNDANT — IT IS `UnicodeDecodeError`, AND ITS
        # ABSENCE WAS A DEAD CLICK. `Path.read_text()` decodes, and a decode
        # failure raises `UnicodeDecodeError`, which subclasses `ValueError` and
        # NOT `OSError`. Caught only by `guarded_main`, it became
        # "mention-open failed: UnicodeDecodeError…" with NO picker — reproduced
        # end-to-end against a `picks.jsonl` whose second line held `\xff\xfe`.
        # `load_known_ranges` one screen up already catches both; the asymmetry
        # was the bug, and this docstring's "EVERY failure is []" was false.
        return []
    out: list[dict] = []
    for line in lines[-PICKS_MAX_ROWS:]:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        repo, t, n = row.get("repo"), row.get("t"), row.get("n")
        if not (isinstance(repo, str) and _OWNER_REPO_RE.match(repo)):
            continue
        if not isinstance(t, (int, float)) or isinstance(t, bool):
            continue
        if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
            continue
        if (now - float(t)) / 86400.0 > PICKS_MAX_AGE_DAYS:
            continue
        out.append({"t": float(t), "repo": repo, "n": n, "via": pick_via(row)})
    return out


def pick_scores(picks: list[dict], num: str,
                now: float | None = None) -> dict[str, float]:
    """{"owner/repo" (lowercased): score} — recency-weighted frequency, warmed
    by how close `num` is to the numbers picked in that repo before. No I/O.

    Each row contributes `half_life(age) * (1 + proximity)`, so:
      * picking a repo MORE OFTEN raises it (the terms sum);
      * picking it RECENTLY raises it (the half-life weight);
      * picking a NEARBY number in it raises it (the proximity term), bounded so
        a distant number still counts as a pick rather than being discarded.

    🔴 A SCORE ONLY EVER ORDERS *WITHIN* ONE TIER A CLASS — `order_universe` puts
    the class first in its sort key, and this function never sees the class at
    all. That separation is structural rather than promised: there is no code
    path on which a learned preference can lift an IMPOSSIBLE repository above a
    PLAUSIBLE one, because the two numbers are never compared.

    ⚠ A REPO WITH NO PICKS SCORES 0 AND KEEPS ITS TIER A PLACE. On a host that
    has never picked anything this returns {}, every score is 0, and the order
    is Tier A's alone — which is the cold-start behaviour, not a special case.
    """
    now = time.time() if now is None else now
    try:
        target = int(num)
    except (TypeError, ValueError):
        target = None
    scores: dict[str, float] = {}
    for row in picks:
        age = max(0.0, (now - row["t"]) / 86400.0)
        weight = 0.5 ** (age / PICKS_HALF_LIFE_DAYS)
        if target is None:
            proximity = 0.0
        else:
            distance = abs(row["n"] - target)
            proximity = 1.0 / (1.0 + distance / PICKS_PROXIMITY_SCALE)
        key = row["repo"].lower()
        scores[key] = scores.get(key, 0.0) + weight * (1.0 + proximity)
    return scores


def order_universe(universe: list[str], num: str, ranges: dict[str, int],
                   scores: dict[str, float] | None = None) -> list[str]:
    """The picker's universe rows, ordered by whether each repo could plausibly
    have `#num`. Pure — no I/O, no clock.

    THE SORT KEY, IN ORDER OF AUTHORITY:
      1. the Tier A CLASS — plausible, below, unknown, impossible;
      2. the Tier B SCORE, descending — the operator's own picks;
      3. `max_ref - num` ascending, for PLAUSIBLE rows only — a repository whose
         head is just past `N` is a better fit than one that passed it a
         thousand references ago.

    🔴 AND THAT IS THE WHOLE KEY — THERE IS DELIBERATELY NO NAME IN IT. A
    trailing `name.lower()` was tried and REMOVED: it makes the function
    ALPHABETISE rather than REFINE, so a caller handing it rows in any other
    order silently loses that order even when the table answers nothing. The
    cold-start test caught it. `sorted` is stable, so leaving the name out means
    equal rows keep the order they arrived in — which is what makes this a
    refinement of `repo_universe`'s sort rather than a second, competing one.
    Determinism is unaffected: the input is deterministic and the sort is
    stable.

    🔴 TIER B SITS *UNDER* THE CLASS AND *OVER* THE DISTANCE, AND BOTH HALVES OF
    THAT ARE DELIBERATE. Under the class, because a learned preference is
    evidence about the OPERATOR and the class is evidence about the REPOSITORY —
    no number of past picks makes a repo with zero references able to answer
    `#1291`. Over the distance, because an actual past pick at a nearby number
    is a measurement, while `max_ref - num` is a heuristic about heads; and
    because only rows the operator has really chosen carry a non-zero score, so
    this reorders a handful of rows rather than the list.

    🔴 IT RETURNS EVERY ROW IT WAS GIVEN. Ranking is the whole intervention —
    see `CLASS_IMPOSSIBLE` for why filtering is not. Pinned by
    `test_ordering_is_a_PERMUTATION_and_never_a_filter`.

    ⚠ `sorted` IS STABLE, SO A TABLE THAT ANSWERS NOTHING IS A NO-OP. With no
    ranges and no scores every key is `(CLASS_UNKNOWN, 0.0, 0)`, which
    reproduces the incoming order EXACTLY — the cold-start requirement, met by
    construction rather than by a branch.
    """
    scores = scores or {}
    try:
        target = int(num)
    except (TypeError, ValueError):
        target = None

    def key(full: str):
        low = full.lower()
        max_ref = ranges.get(low)
        klass = plausibility_class(num, max_ref)
        # Only meaningful for PLAUSIBLE, and 0 everywhere else so it cannot
        # reorder a class whose members were never compared on it.
        distance = (max_ref - target
                    if klass == CLASS_PLAUSIBLE and target is not None
                       and max_ref is not None
                    else 0)
        return (klass, -scores.get(low, 0.0), distance)

    return sorted(universe, key=key)




def narrow_dir(directory: Path) -> None:
    """Remove GROUP and OTHER access from `directory`, preserving every other
    bit. Best-effort and silent.

    🔴 STRIP, NEVER "SET 0700" — the rule is that nobody ELSE may read this
    directory, not that it must have one exact mode. A flat `chmod(0o700)`
    WIDENS as well as narrows, which is worse than the hole it closes: it
    re-grants owner-write to a directory the operator deliberately made
    read-only, and it measurably made
    `test_record_pick_CANNOT_RAISE_when_the_log_is_UNWRITABLE` pass by removing
    the very condition that test exists to exercise.

    🔴 `& 0o7777`, NOT `& 0o777` — a round-2 audit measured the narrower mask
    DESTROYING setuid/setgid/sticky: a `0o2755` parent came back `0o0700` and a
    `0o1777` one likewise. Those bits are not ours to clear; only group and
    other are.

    🔴 ITS OWN `try`, SO IT CANNOT COST THE WRITE. This is defence in depth on a
    directory the caller may not own — a redirected `MENTION_OPEN_PICKS` can
    point anywhere, and `/tmp` is not chmod-able by us. Before this guard the
    `EPERM` propagated into `record_pick`'s handler and DROPPED THE PICK, which
    is a regression against a path that worked before the narrowing existed.
    """
    try:
        mode = os.stat(directory).st_mode & 0o7777
        if mode & 0o077:
            os.chmod(directory, mode & ~0o077)
    except OSError:
        return


def record_pick(repo: str, num: str, path: Path | None = None,
                now: float | None = None, *, via: str) -> bool:
    """Append one `{"t","repo","n","via"}` line to the pick log.

    🔴 `via` IS A REQUIRED KEYWORD AND HAS NO DEFAULT, WHICH IS THE WHOLE POINT
    OF THE FIELD. A default would make the tag a thing a call site can forget:
    the auto-open path — the one this field exists to make visible — would then
    record itself as whatever the default said, and the mislabelling would be
    silent and permanent in a 0600 append-only log. Two call sites exist and
    both must state which they are. A value outside `PICK_VIA_RECORDED` is a
    quiet `False` and NO write, for the same reason a malformed repo is: a row
    whose provenance is wrong is worse than no row.

    Returns True if the line was WRITTEN — which is not quite "survived". A
    compaction racing this call carries over anything appended past its read
    offset, so the row is lost only when THIS call's append fd was opened before
    that compaction's `os.replace` AND its write landed after the carry-over
    read. `_compact_picks` carries the measured table, including the ordering
    that survives however late it writes. The distinction is stated because this
    line first said "True if it landed" while a measured ordering destroyed the
    row, and every later version described the losing case at the wrong width.

    🔴 IT CAN NEVER RAISE AND IT CAN NEVER BLOCK THE OPEN. This runs after the
    operator has already chosen a URL; a full disk, a read-only home or a
    permission change must cost the learning, not the click. Every failure is a
    quiet `False`.

    🔴 0600 ON THE FILE, AND NO GROUP/OTHER ACCESS ON THE PARENT, BOTH ENFORCED
    ON EVERY WRITE RATHER THAN AT CREATION — AND THE PARENT HALF WAS THE ONE
    THAT WAS ONLY CLAIMED. Neither `open(..., "a")` nor
    `Path.mkdir(mode=…, exist_ok=True)` re-applies a mode to something that
    already exists, so a file OR a directory created before this rule, by a
    hand-run, or under a looser umask kept it forever while this sentence said
    otherwise. Both name PRIVATE repositories; see the module docstring.

    ⚠ THE PARENT IS NARROWED, NOT SET — see `narrow_dir`, which owns that rule
    and the measurements behind it. (This used to say "see the comment at the
    `chmod`"; the narrowing has since moved into that helper and there is no
    `chmod` in this function at all, so the pointer led nowhere.) The rule is
    that nobody else may read it, and a flat `chmod(0o700)` would also WIDEN a
    directory the operator made read-only on purpose.

    🔴 IT COMPACTS, BECAUSE NOTHING ELSE DOES. `record_pick` only appends and
    the age cap is applied on READ, so without this the log grows without bound
    — an audit found `PICKS_MAX_ROWS`' comment claiming otherwise. Past
    `PICKS_COMPACT_AT` the tail is rewritten tmp-then-`replace`, so a reader
    that opens the file mid-compaction sees the old content or the new, never a
    truncated one. ⚠ THAT LAST CLAIM IS ABOUT READERS ONLY; concurrent WRITERS
    are handled separately and best-effort — see `_compact_picks`.
    ⚠ A FAILED COMPACTION IS NOT A FAILED WRITE: the row is already on disk and
    the operator's pick is recorded, so it returns True and the file is simply
    trimmed on some later pick.

    ⚠ IT RECORDS ONLY WELL-FORMED `owner/repo` AND A POSITIVE INTEGER. A row the
    reader would discard is not worth writing, and a log full of them would
    push real history past `PICKS_MAX_ROWS`.
    """
    if via not in PICK_VIA_RECORDED:
        return False
    if not (isinstance(repo, str) and _OWNER_REPO_RE.match(repo)):
        return False
    try:
        n = int(num)
    except (TypeError, ValueError):
        return False
    if n <= 0:
        return False
    path = path or PICKS_PATH
    row = {"t": round(time.time() if now is None else now, 3),
           "repo": repo, "n": n, "via": via}
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        narrow_dir(path.parent)
        # 🔴 A PLAIN `O_APPEND` WRITE, AND THAT IS SUFFICIENT — MEASURED, after
        # this carried an `flock` and a retry budget through four audit rounds.
        # `open(..., "a")` is `O_APPEND`, and a single write to a regular file
        # under it is atomic on Linux; one row is ~90 bytes. MEASURED with the
        # lock REMOVED: 8 concurrent processes x 400 appends = 3200/3200 rows,
        # 0 torn, 0 lost.
        #
        # ⚠ THE ONE RACE A LOCK WOULD STILL COVER IS PRICED, NOT IGNORED: an
        # append landing inside `_compact_picks`' read->replace window loses
        # that row. That window is a MEASURED 0.28 ms and opens once per
        # `PICKS_COMPACT_AT - PICKS_MAX_ROWS` picks, so it needs a SECOND
        # human click inside a quarter-millisecond — and the cost if it ever
        # happened is one Tier-B learning row, a soft recency signal the next
        # pick re-establishes. That is not worth a lock, a wait budget, an
        # errno filter and a carry-over on a detached click path.
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        os.chmod(path, 0o600)
    except OSError:
        return False
    # 🔴 ITS OWN `try`, AND *NOT* INSIDE THE ONE ABOVE — TWO SEPARATE REASONS.
    # Not inside: a compaction failure must not flip the return to False, since
    # the row is already durable and the operator's pick IS recorded. Guarded at
    # all: `record_pick` runs immediately before `return open_url(url)`, so
    # anything escaping here skips the OPEN and `guarded_main` turns the click
    # into "mention-open failed" with no browser — the same dead-click shape the
    # `load_picks` decode fix closed one round earlier. `_compact_picks` catches
    # its own OSError/ValueError today, so this is defence in depth against a
    # future edit widening what it can raise.
    try:
        _compact_picks(path)
    except Exception:  # noqa: BLE001 — see above; a pick must never cost a click
        pass
    return True


def _compact_picks(path: Path) -> None:
    """Trim the pick log to its most recent `PICKS_MAX_ROWS` rows.

    🔴 IT EXISTS BECAUSE NOTHING ELSE BOUNDS THE FILE. `record_pick` only
    appends and the age cap is applied on READ, so without this a 0600 file
    naming private repositories grows forever. Past `PICKS_COMPACT_AT` the tail
    is rewritten; trimming at a MULTIPLE of the read cap means the rewrite
    happens once per `PICKS_MAX_ROWS` picks rather than on every one.

    🔴 TMP-THEN-REPLACE, AND THE REASON IS NOT CONCURRENCY. `os.replace` is
    atomic, so a process killed mid-compaction leaves the OLD log intact rather
    than a truncated one — a real hazard on a detached click handler that can be
    SIGKILLed, and the same shape `regen-known-repos.py`'s three writers use.
    The tmp is created 0600 by `mkstemp` rather than chmod-ed afterwards: it
    holds up to `PICKS_MAX_ROWS` private repository names, and write-then-chmod
    leaves a window at the umask's mode (measured 0644).

    ⚠ NO LOCK, AND THAT IS A DELETION MADE ON EVIDENCE. This function and
    `record_pick` carried an `flock`, a retry budget, an errno filter and a
    read-offset carry-over through four audit rounds. All of it defended one
    ordering: a second click landing inside this function's read->replace
    window. MEASURED — that window is 0.28 ms and opens once per
    `PICKS_COMPACT_AT - PICKS_MAX_ROWS` picks, appends are `O_APPEND`-atomic
    (8 processes x 400 rows, 0 lost, lock disabled), and the cost if it ever
    struck is ONE Tier-B learning row. Clicks are human-paced; two inside a
    quarter-millisecond is not a state this handler can reach. The scaffolding
    was larger than the feature and is gone.

    Best-effort and SILENT on every failure: it runs after the row is already
    durable, so a full disk costs a large file rather than the pick.
    """
    import tempfile  # noqa: PLC0415 — only this path needs it
    tmp = None
    try:
        lines = path.read_text().splitlines()
        if len(lines) <= PICKS_COMPACT_AT:
            return
        fd, name = tempfile.mkstemp(dir=str(path.parent),
                                    prefix=path.name + ".", suffix=".tmp")
        tmp = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines[-PICKS_MAX_ROWS:]) + "\n")
        os.replace(tmp, path)
        tmp = None                       # the replace consumed it
    except (OSError, ValueError):
        return
    finally:
        if tmp is not None:              # only ever OUR OWN failed tmp
            try:
                tmp.unlink()
            except OSError:
                pass


def repo_of_github_url(url: str) -> str:
    """`owner/repo` from a github.com reference URL, or "".

    Used to turn the row the operator SELECTED back into something
    `record_pick` can store. It is deliberately narrow — `github.com` and a
    two-segment path — so a clawgate or ClickUp URL yields "" and is not
    recorded as a repository.
    """
    m = re.match(r"^https?://(?:www\.)?github\.com/"
                 r"(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)/", url or "")
    if not m:
        return ""
    full = f"{m.group('owner')}/{m.group('repo')}"
    return full if _OWNER_REPO_RE.match(full) else ""


# --------------------------------------------------------------------------- #
# CLICK-PATH TELEMETRY
#
# 🔴 WHAT LEAVES THIS PROCESS, AND WHAT MAY NEVER. The handler now reports the
# OUTCOME of a click to the personal activity pipeline. The line is exactly the
# one `mention_scan`'s telemetry already sets: emit what the operator TOUCHED,
# never what they were OFFERED. A `source=mentions` row has carried a plain
# `owner/repo` since that leg shipped, and this carries the same — the repository
# whose page was opened, which the operator is looking at.
#
# 🔴 THE OFFERED SET IS THE HAZARD AND IT IS EXCLUDED STRUCTURALLY, NOT BY
# CONVENTION. `known_universe.json` names PRIVATE repositories and its committed
# ancestor disclosed 232 of them into this PUBLIC repo. Nothing below is ever
# handed the candidate list: `click_dims` takes SCALARS — a repo, a platform, a
# rank, a class NAME, a count — so there is no argument through which a row the
# operator did not choose could reach the spool. `test_no_universe_token_can_
# reach_the_TELEMETRY_payload` is the guard, with a control that proves it fires.
#
# 🔴 IT CANNOT COST THE CLICK. Same posture as `record_pick`, one layer along:
# `invocation.emit_invocation` swallows every failure and returns "", the import
# is LAZY so a click that emits nothing pays nothing, the write is a single
# `O_APPEND` line to a local spool — no network, no subprocess, no new verb on
# the path `test_the_resolution_path_spawns_ONLY_these_local_commands` pins.
# MEASURED on this host, 7 fresh-process runs: the FIRST emit of a click costs a
# median 3.7 ms (range 3.3-6.8 under load, and the spread is box load, not this
# code), essentially all of it the lazy import; a second emit in the same process
# is 0.05 ms, which no click reaches because a click emits at most once. It is
# paid AFTER `open_url` has already launched the browser.
# --------------------------------------------------------------------------- #
CLICK_TOOL = "mention-open"

# The three outcomes a click can reach once the handler has something to show.
# ⚠ DELIBERATELY NOT EVERY EXIT. The refusal paths (`refuse()`, `--print`, the
# six-digit colour toast) emit nothing: they are several distinct arms with
# their own toasts, and adding them is a separate change with its own argument.
# What is here is the set the ORDERING question needs — a click that opened
# something, and a picker the operator walked away from.
CLICK_AUTO_OPEN = "auto-open"
CLICK_PICKED = "picked"
CLICK_DISMISSED = "dismissed"
# Pinned two-way by `test_the_click_OUTCOME_vocabulary_is_pinned_two_way`.
CLICK_OUTCOMES = (CLICK_AUTO_OPEN, CLICK_PICKED, CLICK_DISMISSED)

# 🔴 THE CLASS GOES OUT AS A NAME, NEVER AS ITS ORDINAL. `CLASS_PLAUSIBLE` is
# `0`, and a consumer reading the payload with ClickHouse's `JSONExtractInt`
# gets `0` for a key that is ABSENT as well as for one that says "plausible" —
# so shipping the ordinal would make "we never measured the class" and "the best
# possible class" the same value, in the reassuring direction. A name makes the
# absent case `''`, which is the one test the pipeline's own conventions say is
# safe. Pinned two-way against `PLAUSIBILITY_CLASSES`.
CLASS_NAMES = {
    CLASS_PLAUSIBLE: "plausible",
    CLASS_BELOW: "below",
    CLASS_UNKNOWN: "unknown",
    CLASS_IMPOSSIBLE: "impossible",
}

# Every dim `click_dims` can put in the payload, pinned two-way by
# `test_the_click_telemetry_DIM_ledger_is_pinned_two_way`. A field added without
# a ledger entry, or an entry naming no field, fails the suite — the shape of a
# telemetry row is a contract with a consumer that is not in this repo.
CLICK_DIM_FIELDS = ("repo", "platform", "picker_shown", "offered_total",
                    "rank", "plausibility")


def click_dims(repo: str = "", platform: str = "",
               picker_shown: bool = False, offered_total: int | None = None,
               rank: int | None = None,
               plausibility: int | None = None) -> dict:
    """The payload dims for one click outcome. Pure, no I/O, no clock.

    🔴 AN UNMEASURABLE FIELD IS OMITTED, NEVER ZEROED — AND THIS IS THE WHOLE
    REASON THE FUNCTION EXISTS RATHER THAN A DICT LITERAL AT EACH CALL SITE.
    The single-candidate AUTO-OPEN shows no list at all, so it has no rank, no
    class and no total. Emitting `rank=0` there would read as "the operator
    chose the top row", which is the SUCCESS value for the ordering this
    telemetry exists to evaluate: every average would be dragged toward a
    conclusion by rows that never ranked anything. So `picker_shown` is the
    field a consumer must filter on, and `rank`/`plausibility`/`offered_total`
    are simply ABSENT when there was nothing to rank.

    ⚠ TWO DIFFERENT ABSENCES, AND THEY ARE NOT THE SAME QUESTION. A MISSING
    `plausibility` means the plausibility ordering did not run for this click —
    no range table, a stale one, or a picker holding no universe rows. A
    `plausibility` of `"unknown"` means it DID run and this repository had no
    entry in the table. `plausibility_class` keeps `None` and `0` apart on the
    read side for the same reason; this keeps "could not ask" and "asked, no
    answer" apart one layer further out.

    ⚠ `repo` AND `platform` ARE ALWAYS PRESENT, INCLUDING EMPTY. A clawgate or
    ClickUp row has no `owner/repo` — `repo_of_github_url` returns "" for it —
    and a dismissed picker opened nothing at all. `""` is the honest value and
    it is one a consumer can test; omitting the key would make a clawgate pick
    indistinguishable from a row this handler never filled in.
    """
    dims: dict = {"repo": repo, "platform": platform,
                  "picker_shown": bool(picker_shown)}
    if offered_total is not None:
        dims["offered_total"] = int(offered_total)
    if rank is not None:
        dims["rank"] = int(rank)
    if plausibility is not None and plausibility in CLASS_NAMES:
        dims["plausibility"] = CLASS_NAMES[plausibility]
    return dims


def emit_click(outcome: str, **dims) -> str:
    """Report one click outcome to the activity spool. Best-effort, returns the
    line written or "" on ANY failure.

    🔴 IT TAKES THE DIMS AS KEYWORDS AND BUILDS THEM *INSIDE* ITS OWN GUARD,
    WHICH IS NOT A STYLE CHOICE — IT CLOSES A MEASURED HOLE. The first version
    took an already-built dict, so `click_dims(...)` was evaluated at the CALL
    SITE, outside this `try`. MEASURED with `click_dims` made to raise: the
    browser still opened, and then `guarded_main` caught the exception, returned
    **1** and fired a "mention-open failed" toast — a working click reported as
    a broken one. A telemetry helper that can do that is worse than no
    telemetry.

    ⚠ WHAT IS STILL OUTSIDE, STATED RATHER THAN IMPLIED: the three pure lookups
    the picker arm does to find the chosen row's rank, platform and plausibility
    class. They are not moved in here because doing so would mean handing this
    function the CANDIDATE LIST — the offered universe — which is the one thing
    the disclosure rule says must never reach the sink. A narrower guarded
    surface is the right trade against a wider disclosure surface.

    🔴 IT CAN NEVER RAISE AND IT CAN NEVER BLOCK THE CLICK — the same contract
    `record_pick` carries and for the same reason, except that this one is
    strictly weaker evidence: a lost pick costs the learning, a lost telemetry
    row costs a data point. The `except Exception` is deliberately wider than
    `record_pick`'s `OSError` because the failure surface is wider: the
    collector may not be deployed on this host at all, in which case
    `scripts/collector/invocation.py` or `spool_emit` simply is not importable
    and this must be a silent no-op rather than a dead click.

    🔴 THE IMPORT IS LAZY, AND THAT IS MEASURED RATHER THAN STYLISTIC — the same
    argument the module docstring makes for `concurrent.futures`. `spool_emit`
    drags in `base64`, `datetime` and `socket`, none of which this handler
    otherwise loads: a median 3.7 ms on this host. Every click that refuses,
    prints or is barred by `--no-discovery` would pay it for nothing.
    """
    if outcome not in CLICK_OUTCOMES:
        return ""
    try:
        # `scripts/collector` is already on `sys.path` — the module-level insert
        # at the top of this file put it there for `mention_scan`. Re-inserting
        # here would grow the path on every click for no gain.
        import invocation  # noqa: PLC0415 — see the docstring
        return invocation.emit_invocation(CLICK_TOOL, outcome,
                                          dims=click_dims(**dims))
    except Exception:  # noqa: BLE001 — telemetry must never cost a click
        return ""


def row_to_url(row: str, candidates: list[dict]) -> str:
    """Map a picker row back to its URL. Matches on the URL suffix rather than
    the row index, so a picker that decorates or reorders rows cannot open the
    wrong one."""
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
    one checkout. Pinned by
    `test_mention_open.py::test_the_fan_out_pairs_every_checkout_with_its_OWN_owner`.

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


def load_known_universe(path: Path | None = None) -> list[str]:
    """`["owner/repo", …]` from the operator's generated universe file, or [].

    🔴 EVERY failure is `[]` — absent, unreadable, malformed, wrong shape, wrong
    element type. Identical posture to `load_known_repos`, for the identical
    reason: this runs on a detached click handler with nowhere to print a
    traceback, and the file is an OPTIONAL widener. Without it the picker still
    offers everything `discover_repos()` found, which is exactly the behaviour
    that shipped before this file existed — so a host that has never run the
    generator, or is mid-write, degrades to the old universe rather than to none.

    ⚠ A LIST, NOT A DICT, and the shape check is not a formality: `json.loads`
    of the mapping file beside it returns a dict, and a `--path`/`--universe-path`
    mix-up at generation time would otherwise silently produce a universe of
    single characters (iterating a dict yields its keys). A non-list is [].
    """
    # 🔴 RESOLVED AT CALL TIME, NOT BOUND AS A DEFAULT — the same defect that
    # made `load_known_repos`' override test inert and left the whole suite
    # green with the call deleted. See that function's comment.
    path = path or KNOWN_UNIVERSE_PATH
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    return [v for v in raw if isinstance(v, str) and _OWNER_REPO_RE.match(v)]


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

    So this is used ONLY to give a reference that names NO repository a GitHub
    candidate it would otherwise not have. It never overrides an explicit
    `owner/repo` in the text, and it never suppresses the clawgate candidate: a
    wrong guess here shows up as a row in the picker, never as the wrong page
    opening.

    🔴 THAT LAST SENTENCE WAS FALSE FOR ONE SHAPE, AND IT IS THE REASON `main()`
    NOW BRANCHES ON `repo_source`. Making `audit-pr N` clickable added the first
    reference with no clawgate reading, so it produced exactly ONE candidate and
    `main()`'s "one candidate → just open it" shortcut fired on a repository
    THIS FUNCTION guessed: measured with the pane forced to a repo the text
    never named, `audit-pr 1291` opened
    `github.com/WRONGORG/wrongrepo/issues/1291`. A bare `#N` was protected only
    incidentally, by having a clawgate sibling to be ambiguous with. The
    guarantee is now enforced where it is decided — `main()` suppresses the
    shortcut for every `repo_source == "default"` candidate — rather than
    promised here, and
    `test_mention_open.py::test_a_GUESSED_repo_is_never_auto_opened_whatever_the_
    shape` fails if the shortcut ever fires on this rung again.
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


# --------------------------------------------------------------------------- #
# THE PICKER — fzf in a dedicated float terminal
#
# 🔴 IT WAS ROFI UNTIL 2026-09-09, AND THE SWAP IS A MEASUREMENT, NOT A TASTE.
# rofi's `-sorting-method fzf` gave the right SCORES and then broke every tie by
# ROW LENGTH, which is exactly backwards for `owner/repo`: every row of one
# owner matches at the same offset, so they all score identically, and the
# LONGEST loses. MEASURED on the real 392-row universe, query `civitai`:
# `civitai/civitai` ranked **8th of 230**, under seven rows containing the token
# ONCE — all of them shorter. The second occurrence did not help; it HURT.
#
# 🔴 ROFI CANNOT FIX THAT, AND ALL THREE ESCAPES WERE MEASURED CLOSED: it
# exposes no `--tiebreak`; `-display-columns` is DISPLAY-only (matching still
# runs over the whole row — verified headless); and reordering the row changes
# nothing (three shapes tried, all rank 8). Under a length tiebreak a longer
# repo name always loses at equal score. Structural.
#
# fzf's `--tiebreak=end` prefers the match nearest the END of the line, and in
# `…/owner/repo/pull/<id>` the suffix after the repo is a CONSTANT, so "nearest
# the end" is exactly "in the repo name rather than the owner". MEASURED on the
# same 392 rows: `civitai` -> **1 of 230**, and the controls do not move
# (`devrc` 1/16, `talos-infra` 1/1, `comfyui` 1/5 — identical either way).
#
# ⚠ TWO FLAGS CONSIDERED AND REJECTED, both by measurement rather than taste:
#   * `--exact` does NOT fix this. Same synthetic corpus, same query: rank 27
#     with it and 27 without — the defect is a TIEBREAK, and exact matching
#     leaves the scores just as tied. It also narrows the match set (a 6-char
#     prefix went from 56 rows to 21), which is the fuzzy narrowing rofi's
#     `-matching fuzzy` existed to provide. Strictly worse on both axes.
#   * `--select-1` would re-open the defect #1336 closed. It auto-accepts when
#     the initial query matches exactly one row — and the ONE-ROW picker is
#     precisely the guessed-repo confirmation case, which must never open
#     unconfirmed. See `repo_source == "default"` in `main()`.
# --------------------------------------------------------------------------- #

# The terminal the picker runs in, and its geometry.
#
# `--class float,mention-open` puts it under `for_window [class="float"]
# floating enable` in `nix/i3/config.nix` — the SAME rule every other float
# terminal in `nix/graphical.nix` relies on — while the instance half names this
# window specifically, so a future i3 rule can size or place it without catching
# every other float. (alacritty's `--class` is `<general>,<instance>`; i3's
# `class=` matches the general half.)
PICKER_CLASS = "float,mention-open"
PICKER_COLUMNS = 120
PICKER_LINES = 22

# 🔴 THE ROWS NEVER TOUCH argv, AND THAT IS THE WHOLE REASON FOR THE FIFOs.
# They name PRIVATE repositories (see the module docstring), and a terminal
# emulator does NOT proxy stdin: it hands its child a PTY, so the `input=` that
# fed rofi has nowhere to go. Every other transport puts the rows somewhere a
# second process can read them — argv is world-readable in `/proc`, an env var
# likewise, a temp file lands them on disk. A FIFO holds nothing at rest: the
# bytes live in a kernel pipe buffer between two processes and are gone when
# both ends close. The pair lives in a 0700 `mkdtemp` removed in a `finally`.
#
# ⚠ "REMOVED IN A `finally`" IS TRUE OF EVERY EXIT THE INTERPRETER SEES, AND NOT
# OF `SIGKILL` — worth stating because 18 orphaned `mention-open-*` dirs were
# found in a test TMPDIR and read as this claim being false. MEASURED both ways:
# a full suite run leaves 0, and a process parked inside `run_picker` and
# SIGKILLed leaves exactly 1. So the orphans are killed TEST subprocesses (the
# mutation battery and timed-out runs kill pytest), never a click. **No
# disclosure either way** — what survives is two EMPTY FIFOs, which hold nothing
# at rest, in a 0700 directory. A click that is killed mid-pick can leave one
# behind; that is the honest scope of the sentence above.
#
# 🔴 THE HEADER GOES DOWN THE SAME PIPE, as `--header-lines`, rather than into
# `--header` on argv. The note is pinned to name no repository — but pinning is
# a claim about today's `universe_note`/`guessed_note`, and routing it through
# the private channel makes "nothing that could carry a universe token is ever
# an argument" STRUCTURAL instead. The count is `len(header)` of the very list
# being prepended, so it cannot drift from what it counts.
#
# `sh -c` is unavoidable — fzf must read one FIFO and write the other, and only
# a shell can redirect. It is NON-INTERACTIVE (`-c`), so no history file is
# read or written, and the FIFO paths arrive as POSITIONAL ARGUMENTS: nothing is
# interpolated into the script text.
#
# 🔴 IT STAYS A LIST LITERAL WHOSE argv[0] IS THE CONSTANT `"alacritty"`.
# `test_mention_open.py`'s AST ledger of spawnable executables reads exactly
# that; handing `subprocess.Popen` a variable reports `<computed>` and the
# ledger — the guard that stops a network call being re-added — goes red for a
# reason that has nothing to do with what is being spawned. The same test reads
# the FIRST WORD of the `-c` script to learn that `fzf` must be on the wrapper's
# PATH, so that word must stay `fzf`.
# 🔴 THE SHAPE OF THIS STRING IS PINNED, NOT JUST ITS FIRST WORD. It is a SHELL
# SCRIPT, which is a surface rofi did not have at all: `fzf … <"$1" | tee
# /tmp/picker.log >"$2"` or `notify-send "$(cat "$1")"; fzf …` would write the
# PRIVATE rows to disk or into a toast argv while every ledger here stayed
# green, because the reader that learns `fzf` must be on PATH only inspects the
# first word. `test_the_picker_SHELL_SCRIPT_stays_the_shape_it_is_pinned_to`
# asserts the whole normalised string AND that it carries no `;` `|` `&` `` ` ``
# `$(` and exactly the two redirections. Reword it freely; the test prints the
# replacement.
#
# 🔴 `-i` IS LOAD-BEARING AND ITS ABSENCE WAS SILENT. fzf's default is
# SMART-CASE: a query containing any uppercase letter becomes case-SENSITIVE.
# rofi's `-i` was unconditional, so the swap quietly changed behaviour.
# MEASURED through `_eponymous_corpus()` (195 rows), query `NimbusWorks`:
# **0 rows** matched without `-i`, **15** with. An empty list and a dismissal are
# indistinguishable to `pick()`, so the operator types a capital letter and gets
# SILENCE — the exact wall this whole change exists to remove, and
# `repo_universe()` preserves each repo's original casing so mixed-case rows are
# real.
#
# ⚠ RANKING IS UNAFFECTED, AND THAT IS MEASURED ON THE LOWERCASE QUERY — the
# only one where both sides have a ranking to compare. `nimbusworks`: 15 matched
# and the eponymous repo 1st, with `-i` and without. An earlier version of this
# comment said "1/41 with and without" beside "0 rows without", which is
# self-contradictory: rank is undefined on an empty set. (41 and 392 came from a
# scratch corpus, not this one; 392 is the real universe's row count.)
PICKER_SH = (
    'fzf -i --tiebreak=end --layout=reverse --info=inline '
    '--prompt="mention > " --pointer=">" --color=16 '
    '--header-lines="$3" <"$1" >"$2"'
)

# How long the picker may stay open before it is abandoned. Inherited from the
# rofi call this replaced, unchanged: it is "the operator walked away", not a
# tuned constant.
PICKER_TIMEOUT = 120.0


def picker_header(mesg: str) -> list[str]:
    """The note, wrapped to the picker's width — one list entry per fzf header
    line.

    Returned as a LIST because `--header-lines` needs a count and the count must
    be `len()` of the exact lines prepended, never a second measurement of the
    same string. Too LOW and a header line becomes a selectable row that
    `row_to_url` maps to nothing (harmless); too HIGH and it eats the FIRST real
    row — which is the clawgate task on a bare `#N`. Deriving both from one list
    removes that direction entirely.

    ⚠ NO MARKUP ESCAPING, and its absence is deliberate. rofi's `-mesg` rendered
    PANGO, so `<` and `&` had to be escaped or the whole line vanished. fzf's
    header is plain text, so the escaping is not merely unnecessary — leaving it
    in would show the operator a literal `&amp;` in text they typed.
    """
    if not mesg:
        return []
    import textwrap  # noqa: PLC0415 — see the `concurrent.futures` note above
    return textwrap.wrap(mesg, width=PICKER_COLUMNS - 2) or [mesg]


# What `run_picker` is reporting alongside the row. `pick()` branches on it, and
# the split exists because THREE different silences used to look identical.
#
# 🔴 A DISMISSAL MUST STAY SILENT AND THE OTHER TWO MUST NOT. Firing a toast
# after a dismissal would fire one after EVERY dismissal, which is the noise
# this handler must not make — but rofi's 120s `timeout=` raised
# `TimeoutExpired`, which the old `except` turned into a toast, and the first
# version of this rewrite swallowed that into the same silent "". Restored here
# rather than argued away: a picker that timed out is not a picker that was
# dismissed.
#
# 🔴 `NEVER_SHOWN` MEANS THE TERMINAL DIED BEFORE THE SHELL OPENED THE FIFOs,
# AND IT CANNOT MEAN A MISSING `fzf` — a round-2 audit finding, and the prose it
# corrects was WRONG IN THE SAME FILE THAT DISPROVES IT. `/bin/sh` applies
# `<"$1" >"$2"` BEFORE exec'ing the command, which is the whole basis of the
# open-order deadlock fix in `run_picker` — so a MISSING fzf still gives the rows
# FIFO a reader, the ENXIO loop still exits normally, and the run ends as a
# silent `DISMISSED`. MEASURED: `/bin/sh -c 'zzznosuchbinary <"$1" >"$2"'` exits
# 127 with the redirections APPLIED. ⚠ And `proc.returncode` cannot rescue it:
# alacritty 0.17.0 exits **0** whether its `-e` command exits 0 or exits 127, so
# `fzf: not found` — which is a 127 from a shell that DID run — is invisible in
# the terminal's status. (An earlier version of this note added "or does not
# exist at all"; that limb is FALSE — measured under Xvfb, `-e zzznosuchbinary`
# exits **1**. It does not change the conclusion, because `-e` here is always the
# existing `/bin/sh`, so the 0-vs-0 case is the only one this code can be in.)
#
# So a missing `fzf` is caught BEFORE the spawn instead, by `pick()`'s
# `shutil.which` pre-flight — which is strictly better than a post-hoc toast: it
# never raises a window at all. What still reaches `NEVER_SHOWN` is alacritty
# starting and exiting before the shell ran — no `DISPLAY`, an X error, a
# terminal that cannot map a window — and answering a CLICK with nothing at all
# is the dead end this whole handler exists to remove.
PICKED_SELECTED = "selected"
PICKED_DISMISSED = "dismissed"
PICKED_TIMEOUT = "timeout"
PICKED_NEVER_SHOWN = "never-shown"

# 🔴 PINNED TWO-WAY by `test_the_PICKED_outcome_set_is_pinned_and_every_one_is_
# HANDLED`. `pick()` branches on these and its `else` is SILENCE, so a fifth
# outcome added later would be dropped without a sound — which is the exact
# failure round 1 found in the timeout arm. The ledger makes adding one a
# deliberate act.
PICKED_OUTCOMES = (PICKED_SELECTED, PICKED_DISMISSED, PICKED_TIMEOUT,
                   PICKED_NEVER_SHOWN)


def run_picker(payload: str, header_lines: int) -> tuple[str, str]:
    """Show `payload` in a terminal running fzf. Returns `(row, outcome)`, where
    outcome is one of the `PICKED_*` constants above and `row` is "" for
    anything but `PICKED_SELECTED`.

    `payload` is `header_lines` header lines followed by the rows, newline
    separated. It is written to a FIFO and NEVER to argv, an env var or a file
    — see `PICKER_SH`.

    🔴 THE OPEN ORDER IS LOAD-BEARING AND IT IS A DEADLOCK, NOT A STYLE. The
    shell applies `<"$1" >"$2"` BEFORE exec'ing fzf, and opening a FIFO for
    WRITING blocks until a READER exists — so the child sits in `open("$2")`
    with fzf not yet running and nothing draining `$1`. Everything still works
    while the rows fit in one 64 KiB pipe buffer, which today's ~392 rows
    (~27 KB) do; the day they do not, the write blocks, the child never
    unblocks, and the click hangs until the 120s timeout. Opening the choice
    FIFO **first** — `O_RDWR`, which needs no peer and never blocks — removes
    the cycle for any payload size.
    """
    # Imported here rather than at module scope for the reason the
    # `concurrent.futures` comment at the top of this file measures: every one
    # of these lands on EVERY click, and only the picker path needs them.
    import errno       # noqa: PLC0415
    import select      # noqa: PLC0415
    import shutil      # noqa: PLC0415
    import tempfile    # noqa: PLC0415

    workdir = tempfile.mkdtemp(prefix="mention-open-")
    rows_fifo = os.path.join(workdir, "rows")
    choice_fifo = os.path.join(workdir, "choice")
    proc = None
    rfd = -1
    try:
        os.mkfifo(rows_fifo, 0o600)
        os.mkfifo(choice_fifo, 0o600)
        proc = subprocess.Popen(
            # 🔴 `selection.save_to_clipboard=false` IS A DISCLOSURE FLAG, NOT
            # COSMETICS, and it is a sink rofi never had. The picker inherits
            # `~/.config/alacritty/alacritty.toml`, where this repo sets
            # `save_to_clipboard = true` — so a drag across the list would copy
            # PRIVATE repository names into the X CLIPBOARD, which OUTLIVES the
            # pick and is readable by every X client on the display. The module
            # docstring's enumeration of where the universe may go does not list
            # a clipboard, and this is what keeps that true. (fzf turns mouse
            # reporting on, so a plain drag is already claimed by fzf and a
            # selection needs Shift — mitigation, not a guarantee, which is why
            # the flag is passed rather than reasoned about.)
            ["alacritty", "--class", PICKER_CLASS,
             "-o", f"window.dimensions.columns={PICKER_COLUMNS}",
             "-o", f"window.dimensions.lines={PICKER_LINES}",
             "-o", "selection.save_to_clipboard=false",
             "-e", "/bin/sh", "-c", PICKER_SH,
             "mention-open", rows_fifo, choice_fifo, str(header_lines)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + PICKER_TIMEOUT

        # 🔴 FIRST, and see the docstring. O_RDWR also means the read loop below
        # can never see a PREMATURE EOF: a read-only FIFO with no writer yet
        # returns end-of-file at once, which is indistinguishable from "the
        # operator dismissed it". Holding a write end ourselves leaves the child
        # exiting — which `poll()` reports — as the only EOF-shaped answer.
        rfd = os.open(choice_fifo, os.O_RDWR | os.O_NONBLOCK)

        # --- feed the rows in -------------------------------------------- #
        # Opening a FIFO for writing fails with ENXIO until a reader opens it,
        # so this is a poll rather than a blocking open: a terminal that never
        # starts (or a stub that exits at once) must return, not hang for two
        # minutes.
        wfd = -1
        while wfd < 0:
            try:
                wfd = os.open(rows_fifo, os.O_WRONLY | os.O_NONBLOCK)
            except OSError as exc:
                if exc.errno != errno.ENXIO:
                    raise
                if proc.poll() is not None:
                    # The terminal exited without the SHELL ever opening the
                    # rows FIFO, so nothing was ever displayed. That is NOT a
                    # dismissal. ⚠ It is also NOT a missing `fzf` — see the
                    # `PICKED_*` block: the shell opens both FIFOs before
                    # exec'ing, so a missing command still lands here as a
                    # normal read. `pick()` pre-flights that case instead.
                    return "", PICKED_NEVER_SHOWN
                if time.monotonic() > deadline:
                    return "", PICKED_TIMEOUT
                time.sleep(0.02)
        data = payload.encode()
        try:
            while data:
                _r, w, _x = select.select([], [wfd], [], 0.2)
                if w:
                    try:
                        data = data[os.write(wfd, data):]
                        continue
                    except BlockingIOError:  # pragma: no cover — select said writable
                        pass
                    except BrokenPipeError:
                        # 🔴 A DEPARTED READER IS A NORMAL END, NOT A FAILURE,
                        # AND TREATING IT AS ONE THREW THE ANSWER AWAY. fzf
                        # accepts or dismisses as soon as the operator presses a
                        # key — it does not wait to drain the whole list — and
                        # closing its read end makes the next `os.write` raise
                        # EPIPE. That is an `OSError`, so it used to propagate
                        # into `pick()`'s handler, fire a "could not show the
                        # mention picker" toast, and discard a row the child had
                        # ALREADY written to the choice FIFO. MEASURED: fine at
                        # 392 rows (~21 KB), broken at 1,200 (~67 KB) and 3,000
                        # (~172 KB) — the boundary is the 64 KiB pipe buffer,
                        # the same threshold the open-order fix above exists for,
                        # because below it the whole payload lands in one write
                        # before fzf can answer. Stop feeding and go read the
                        # selection; it is already in the other pipe.
                        break
                if proc.poll() is not None or time.monotonic() > deadline:
                    break
        finally:
            os.close(wfd)

        # --- read the selection back ------------------------------------- #
        out = b""
        outcome = PICKED_DISMISSED
        while b"\n" not in out:
            r, _w, _x = select.select([rfd], [], [], 0.2)
            if r:
                chunk = os.read(rfd, 65536)
                if chunk:
                    out += chunk
                    continue
            if time.monotonic() > deadline:
                outcome = PICKED_TIMEOUT
                break
            if proc.poll() is not None:
                # The child is gone, so anything it wrote is already in the
                # pipe. Drain once, then stop — a dismissal leaves nothing.
                try:
                    out += os.read(rfd, 65536)
                except BlockingIOError:
                    pass
                break
        row = out.decode("utf-8", "replace").split("\n", 1)[0]
        if row:
            # A row that arrived is an ANSWER even if the deadline passed while
            # it was in flight — the operator chose, and discarding that because
            # a clock ran out would be the same thrown-away selection the
            # `BrokenPipeError` arm above exists to prevent.
            return row, PICKED_SELECTED
        return "", outcome
    finally:
        if rfd >= 0:
            os.close(rfd)
        if proc is not None and proc.poll() is None:
            proc.terminate()
        shutil.rmtree(workdir, ignore_errors=True)




def pick(candidates: list[dict], mesg: str = "") -> str:
    """Ask fzf which candidate to open. Returns the chosen URL, or "" if the
    operator dismissed the picker (which must open NOTHING).

    🔴 `mesg` IS WHERE THE DIAGNOSIS GOES, AND THE REASON IS THAT THE PICKER
    CANNOT TELL THE TWO EXITS APART. fzf does not return the query, so "I typed
    `kubectl-neat` and the list went empty" and "I changed my mind" BOTH arrive
    back here as no selection. There is no signal that separates them — not the
    exit code, not stdout — so a toast fired after a dismissal would fire after
    every dismissal, which is the noise this handler must not make. The
    diagnosis therefore goes where it costs nothing and is read BEFORE the
    choice: a header above the list, on the operator's own screen, which is the
    one surface the universe may already reach. See `universe_note`.

    🔴 FUZZY MATCHING IS LOAD-BEARING, not a nicety, and it is fzf's DEFAULT —
    which is why nothing below spells it. It is the entire reason the namesake
    cap could be removed and the reason a hundred-row universe is a narrowing
    rather than a wall: the operator types `talos-inf` and the list collapses.
    `--exact` would take it away; the suite pins its ABSENCE for that reason.

    🔴 fzf NEVER RETURNS THE QUERY, which is what rofi needed `-no-custom` for.
    Free text the operator TYPED would come back as a row `row_to_url` cannot
    match — dismissal-shaped, but by accident. fzf prints a SELECTED item or
    nothing; the flag that would break that is `--print-query`, and the suite
    pins its absence.

    🔴 WITH AN EMPTY QUERY fzf PRESERVES INPUT ORDER, so the clawgate row stays
    FIRST on a bare `#N`. Sorting only applies to a scored match set, and an
    empty query scores nothing. Verified end-to-end against a real fzf, not
    assumed.
    """
    # 🔴 PRE-FLIGHT, BECAUSE A MISSING `fzf` IS OTHERWISE SILENT. The shell opens
    # both FIFOs before exec'ing, so `fzf: not found` reaches `run_picker` as an
    # ordinary empty read — indistinguishable from a dismissal, which is the dead
    # click the wrapper's PATH test exists to prevent and which no outcome of
    # `run_picker` can report. Asking HERE costs one `which` on the slow path
    # only, and it never raises a window to say so. `alacritty` needs no
    # equivalent: it is `Popen`'s argv[0], so its absence is a
    # `FileNotFoundError` the handler below already names.
    # 🔴 THE BODY NAMES `home-manager switch`, NOT A FILE TO EDIT — a round-3
    # audit finding, and the SAME operator consequence as round 2's finding A: a
    # diagnosis pointing somewhere that is already fine, in a module whose whole
    # thesis is that an undiagnosed silence is the bug. It used to say "add
    # pkgs.fzf to the hint wrapper in nix/programs/alacritty/default.nix". But
    # that entry is already there AND CANNOT BE REMOVED —
    # `test_the_alacritty_wrapper_PATH_covers_every_executable_the_handler_
    # spawns` fails the suite without it — so in any tree that passes, the named
    # remedy is already applied and the operator is sent to edit a correct file.
    #
    # What can ACTUALLY raise this: a deployed wrapper GENERATION predating the
    # entry (this repo's own merged-≠-deployed trap), a GC'd store path, or this
    # script run outside the wrapper altogether. All three are fixed by a
    # switch, none by an edit.
    import shutil  # noqa: PLC0415 — see run_picker's import comment
    if shutil.which("fzf") is None:
        notify("the mention picker cannot run",
               "fzf is not on this handler's PATH. The hint wrapper pins it, so "
               "this is a DEPLOY gap, not a config one — run "
               "`home-manager switch --flake ~/workspace/devrc --impure`")
        return ""
    rows = picker_rows(candidates)
    header = picker_header(mesg)
    try:
        chosen, outcome = run_picker("\n".join([*header, *rows]) + "\n",
                                     len(header))
    except (OSError, subprocess.SubprocessError) as exc:
        notify("could not show the mention picker",
               f"{type(exc).__name__}: {exc}")
        return ""
    # 🔴 ONLY A DISMISSAL IS SILENT. See the `PICKED_*` block: rofi's 120s
    # `timeout=` produced a toast and the first version of this rewrite lost it,
    # and a terminal that died before it could be fed is a CLICK THAT DID
    # NOTHING with no explanation — the dead end the fuzzy picker replaced.
    # Neither body names a repository: the universe reaches the picker only.
    if outcome == PICKED_TIMEOUT:
        notify("the mention picker timed out",
               f"nothing was selected within {PICKER_TIMEOUT:.0f}s")
    elif outcome == PICKED_NEVER_SHOWN:
        # ⚠ NAMES THE CAUSE THAT CAN ACTUALLY REACH THIS ARM. It used to say
        # "check that alacritty and fzf are on the hint wrapper's PATH", and
        # NEITHER of those can produce it: a missing alacritty raises
        # `FileNotFoundError` at `Popen` and takes the branch above, and a
        # missing fzf is pre-flighted before the spawn. What lands here is a
        # terminal that started and exited before the shell ran.
        notify("the mention picker could not open",
               "the terminal exited before it could show anything — check "
               "DISPLAY and try `alacritty --class float,mention-open -e true`")
    elif outcome not in PICKED_OUTCOMES:  # pragma: no cover — pinned two-way
        # 🔴 NOT A SILENT `else`. An outcome nobody taught this function about
        # is the same defect round 1 found in the timeout arm, one layer up, so
        # it says so instead of dropping it.
        #
        # ⚠ A FIXED BODY — it used to interpolate `str(outcome)`. The module
        # docstring says the universe may reach the picker window and NOWHERE
        # else, "never to a notification body", and this was the one line that
        # could carry an arbitrary string into that surface. Not a live leak
        # (the four outcomes are literals and this branch is unreachable from
        # `run_picker`), but the guard is cheaper than the argument, and a
        # future outcome built from data would have leaked through it silently.
        notify("the mention picker returned an unknown outcome",
               "see PICKED_OUTCOMES in scripts/mention-open.py")
    return row_to_url(chosen, candidates)


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

    🔴 IT IS THE TIMER'S DEADMAN. See `STALE_MAPPING_DAYS`. A daily unit
    regenerates this file, so past seven days SEVERAL consecutive runs did not
    land — the unit is failing, the token expired, or the host was off — and
    none of those is visible anywhere else, because a stale mapping's only
    symptom is a picker appearing where a resolution used to.

    ⚠ THE TEXT USED TO SAY "nothing regenerates it", WHICH IS NOW FALSE and
    would send the operator to re-run a generator by hand instead of looking at
    the unit that is silently failing. It names a COUNT, a CONSTANT PATH and a
    UNIT NAME — never a row.
    """
    age = mapping_age_days(path)
    if age is None or age < STALE_MAPPING_DAYS:
        return ""
    return (f"the repo mapping is {age:.0f} days old — the daily refresh has "
            f"not landed, so a repository created since then cannot resolve; "
            f"check `systemctl --user status mention-known-repos-refresh` "
            f"or run scripts/regen-known-repos.py")


def ordering_note(num: str, state: str, plausible: int = 0,
                  impossible: int = 0, age: float | None = None) -> str:
    """The clause appended to the picker header saying HOW the rows below are
    ordered — or that they are not. "" when there is nothing to say.

    🔴 THE OPERATOR MUST BE ABLE TO TELL AN ORDERED LIST FROM AN UNORDERED ONE,
    because the two look identical and only one is worth trusting. Once the top
    rows are the plausible ones, "the first few rows are the likely ones"
    becomes a habit — and a host whose range table is absent or stale silently
    stops earning it. Saying which state this is costs one clause and is the
    only signal the operator will ever get: the picker cannot report anything
    after the fact (see `pick()`), and a toast after every click is the noise
    this handler must not make.

    🔴 IT NAMES THE CLICKED NUMBER AND TWO COUNTS — NEVER A ROW. Same rule as
    `universe_note` and `guessed_note` beside it, and the same reason: this
    string reaches the picker, and the picker's input is the one surface the
    universe may already touch. `num` is what the operator clicked.

    ⚠ THE `no-table` WORDING NAMES THE GENERATOR, NOT A FILE TO EDIT. A host
    that has never run the ranges leg is one refresh away from an ordered
    picker; sending the operator to look at a file they cannot usefully write by
    hand would be the same wrong-remedy defect `pick()`'s fzf branch records.

    🔴 `no-table` IS DELIBERATELY THE QUIETEST OF THE THREE, AND THAT IS A
    FREQUENCY ARGUMENT RATHER THAN A STYLE ONE. It fires on EVERY picker until
    the generator has run once — and on a host where `gh` is absent or not
    logged in, which `regen-known-repos.py` exits 4 for as a legitimate state,
    it fires forever. A shouty clause on the commonest path is the noise that
    gets learned past, which is the same objection that kept the refresh timer
    unwritten for months. `stale` is the one that means something is BROKEN, so
    it is the one that names a unit to look at.
    """
    if state == ORDER_NO_TABLE:
        return ("rows unordered — no reference-range table on this host yet "
                "(scripts/regen-known-repos.py builds one)")
    if state == ORDER_STALE:
        aged = f"{age:.0f} days old" if age is not None else "of unknown age"
        return (f"rows unordered — the reference-range table is {aged}, so "
                f"ordering on it would be confidently wrong; check "
                f"`systemctl --user status mention-known-repos-refresh`")
    if state == ORDER_APPLIED:
        return (f"ordered by plausibility for #{num}: {plausible} could have "
                f"it, {impossible} have no references at all")
    return ""


def universe_note(subject: str, offered: int, path: Path | None = None) -> str:
    """The line shown ABOVE the fuzzy picker when the universe is the last resort.

    🔴 IT EXISTS BECAUSE A DISMISSED PICKER CANNOT BE READ. `pick()` explains
    why: the picker reports "nothing matched what I typed" and "I changed my
    mind" identically, so the only honest place to say "this host has
    no repository called `kubectl-neat`" is BEFORE the choice, not after it. A
    real dismissal still opens nothing and still says nothing.

    🔴 IT NAMES THE CLICKED TEXT, A COUNT AND A DATE — NEVER A ROW. `subject` is
    what the OPERATOR typed or clicked, which they already have; `offered` is a
    cardinality; the date is the mapping file's mtime. None of the three is a
    repository name from the universe, and this string reaches the picker only —
    it is never handed to `notify()`, which would put it on stderr.
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


def guessed_note(subject: str, below: int = 0, rank: int = 1) -> str:
    """The line shown ABOVE the picker when one of the rows on offer was GUESSED
    — `repo_source == "default"`, i.e. the tmux pane rather than anything the
    clicked text said.

    `rank` is that row's 1-based position, and `below` is how many SEARCHABLE
    repository rows sit UNDER it. Both are measured by `main()` from the list it
    is about to hand `pick()`; neither is inferred from the shape of the text.

    🔴 A PICKER WITH NO EXPLANATION READS AS A BUG. Suppressing the auto-open is
    the safety property; saying WHY is what stops the operator concluding the
    handler is broken and going back to typing the URL. It is the same argument
    as `universe_note`: the picker cannot report a reason after the fact, so the
    reason goes above the choice.

    🔴 TWO WORDINGS, KEYED ON `below` RATHER THAN ON A ROW COUNT, AND THE KEY IS
    THE FIX. `below` is the only thing that decides which INSTRUCTION is true:
    "type to search" is nonsense with nothing to search, and "confirm, or
    dismiss" is a dead end when there are alternatives. A row count cannot tell
    those apart — a bare `#N` on a host whose universe holds only the pane's own
    repo offers TWO rows (the clawgate task and the guess) with nothing
    searchable under either, and keying on the count claimed 392 rows that were
    not there.

    🔴 `rank` EXISTS BECAUSE THE GUESS IS NOT ALWAYS FIRST. `audit-pr N` offers
    it at row 1; a bare `#N` puts the clawgate task above it, at row 2. The
    previous wording said "The FIRST row is a guess" unconditionally, which was
    true of the only shape that then reached this note and became false the
    moment the bare-`#N` shape did (2026-09-08). Naming the row NUMBER is the
    one claim that stays true for both without this function having to know
    which platforms are above it.

    ⚠ The `below <= 0` wording is KEPT rather than deleted: the universe can be
    empty (no mapping file yet, or an unreadable one), and a guessed row is
    still reachable then. It is no longer the common path, and the `below`
    default stays 0 so the narrower claim is what a caller gets by omission.

    🔴 IT NAMES THE CLICKED TEXT AND TWO COUNTS, NOTHING ELSE — never the
    repository, never the mapping. The candidate ROW already shows the repo,
    which is the whole point of asking; the note must not become a second place
    a name can leak from.

    ⚠ AND THE TEST SUITE'S `_every_sink` HELPER CANNOT SEE THIS STRING. It folds
    stdout, stderr and the `notify-send` argv — the sinks that outlive the click;
    this line goes to the picker, which is a transient window on the operator's
    own screen and is reached through `pick()`, which the tests replace. So the
    rule above is enforced by the per-site guards on `mesg` instead —
    `_no_universe_token_anywhere` for the mapping, and
    `_no_guessed_repo_token_anywhere` for the pane's own repo, which the first
    one is structurally blind to (a guess never comes from the mapping).

    ⚠ THE PICKER IS NOW A SEPARATE PROCESS, WHICH ADDS SURFACES THE SENTENCE
    ABOVE DOES NOT COVER — a terminal running fzf rather than an in-process rofi
    call. Neither is a leak and both are guarded, but they are guarded
    SEPARATELY and a reader should not take "the picker" as one opaque thing:
    the rows and this note travel a FIFO pair in a 0700 dir (never argv, never a
    file — see `PICKER_SH`), and the terminal's own argv carries only flags and
    those two paths. `test_NO_ROW_reaches_the_terminal_ARGV` and
    `test_the_GUESSED_note_reaches_the_picker_and_no_other_surface` hold that
    line, with BOTH token guards.
    """
    if below <= 0:
        return (f"{subject} names no repository — the GitHub row offered was "
                "measured from the tmux pane, which may not be the pane you "
                "clicked in. Confirm, or dismiss.")
    return (f"{subject} names no repository. Row {rank} is a guess from the "
            f"tmux pane, which may not be the pane you clicked in — the {below} "
            f"rows below it are every repository this host knows. Type to "
            f"search, or dismiss.")


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
            #
            # 🔴 THIS ARM IS NARROW, AND THE CONDITION THAT REACHES IT IS WRITTEN
            # DOWN BECAUSE A ROUND-2 AUDIT READ IT AS DEAD CODE AND PROPOSED
            # DELETING IT. The reasoning was: `reason == ""` means
            # `clean_repo_map` kept a row, `repo_universe` filters on the SAME
            # `OWNER_REPO_VALUE_RE`, and `discover_repos` only ADDS rows — so a
            # non-empty mapping guarantees a non-empty universe, the picker is
            # shown, and `refuse()` is never called. Every step of that is true
            # except the last: `discover_repos` also OVERWRITES, and
            # `parse_owner_repo` is LOOSER than `OWNER_REPO_VALUE_RE` — it asks
            # only for two `/`-separated segments, so a remote like
            # `https://github.com/-acme/widget.git` yields the non-empty
            # `-acme/widget`, which the value regex rejects. A checkout whose
            # DIRECTORY NAME shadows a mapping key therefore replaces a valid row
            # with an invalid one, and a host where that happens to every valid
            # row has `universe_reason() == ""` and an EMPTY universe at once.
            # MEASURED end-to-end, not reasoned about — that exact state is what
            # `test_a_NON_print_refusal_CAN_still_name_the_mapping_AGE` builds,
            # and it was watched to fail with this branch deleted.
            #
            # ⚠ SO THE HONEST SCOPE OF THE SIGNAL IS: the picker note
            # (`universe_note`) on every offered universe, the `--print` refusal
            # above, and THIS arm on the shadowed-mapping host. It is not "every
            # refusal past 7 days" — most refusals name a cause instead, and
            # `reason` wins there by design.
            if not reason and (stale := staleness_note()):
                why = f"{why}; {stale}"
    notify(f"cannot resolve {subject}", f"{why} — {advice}")
    return 1


def _ordered_universe(
        universe: list[str],
        num: str) -> tuple[list[str], str, tuple[int, int], float | None,
                           dict[str, int]]:
    """`(rows, state, (plausible, impossible), age_days, ranges)` — the impure
    composer.

    🔴 THE `ranges` DICT COMES BACK FOR THE SAME "ONE MEASUREMENT, TWO READERS"
    REASON THE AGE DOES, and it is the FIFTH element rather than a second
    `load_known_ranges()` at the caller. The click telemetry reports which
    plausibility class the row the operator CHOSE was in, and a class computed
    from a second read can disagree with the order the operator was actually
    looking at — the table is rewritten by a daily unit. A telemetry row that
    describes a different ordering from the one on screen is worse than no row.

    ⚠ IT IS `{}` IN EVERY DEGRADED STATE, AND THAT IS LOAD-BEARING RATHER THAN
    TIDY. `ordering_state` returns `no-table` for an empty table and `stale`
    past the threshold, and in both the rows come back UNORDERED — so an empty
    `ranges` is exactly the predicate "no plausibility ordering happened here",
    which is what the telemetry must report as an ABSENT class rather than as
    `unknown`.

    🔴 THE AGE COMES BACK WITH THE STATE, AND THAT IS THE "ONE MEASUREMENT, TWO
    READERS" RULE `mapping_age_days` states. The state is DECIDED from the
    file's mtime and the header then REPORTS that mtime; a second `stat` for
    the report can disagree with the first — the file is rewritten by a daily
    unit — and a note whose age contradicted the verdict beside it would read
    as a bug in the note. `universe_note` already makes exactly this argument
    about its own date/age pair.

    🔴 THE ONLY I/O IN THE ORDERING, GATHERED IN ONE PLACE. Everything it calls
    is pure and separately testable (`order_universe`, `pick_scores`,
    `plausibility_class`, `ordering_state`); this reads the two files and the
    clock, so a test that wants the ordering does not have to own a filesystem
    and a test that wants the DEGRADE path does not have to own a sorter.

    🔴 A STALE OR MISSING TABLE RETURNS THE INPUT UNTOUCHED — not a
    best-effort ordering, not a partial one. See `ordering_state`: past the
    staleness threshold the two misclassifications point in opposite directions,
    so an order built on it is confidently wrong, while leaving the rows alone
    is exactly what shipped before this existed. The state token travels back
    with the rows so the header can say which of the two the operator is
    looking at.

    ⚠ TIER B IS APPLIED IN BOTH ORDERED *AND* DEGRADED STATES? NO — and this is
    the one place that could have gone either way. It is applied only when Tier
    A is, because `order_universe`'s whole contract is that a learned preference
    orders WITHIN a class; with no classes to sit inside, picks alone would be
    free to float any repository to the top of the list, which is precisely the
    promotion the tier split forbids. A host with picks and no range table
    therefore gets the old order, and gets the ordered one as soon as the
    generator has run once.
    """
    ranges = load_known_ranges()
    age = ranges_age_days()
    state = ordering_state(ranges, age)
    if state != ORDER_APPLIED:
        return (universe, state, (0, 0), age, {})
    # 🔴 ONE CLOCK READING, TWO READERS — the same discipline `mapping_age_days`
    # states for the mtime. `load_picks` decides which rows are inside the age
    # cap and `pick_scores` decides how much each one decays; two independent
    # `time.time()` calls would let a row sit on the boundary and be filtered by
    # one while weighted by the other. The window is microseconds and the bug
    # would be unreproducible, which is exactly why it is closed here rather
    # than argued about.
    now = time.time()
    scores = pick_scores(load_picks(now=now), num, now=now)
    rows = order_universe(universe, num, ranges, scores)
    # Counted from the SAME `ranges` dict the sort used, not re-read: a header
    # that disagreed with the order beside it would read as a bug in the note.
    classes = [plausibility_class(num, ranges.get(r.lower())) for r in rows]
    return (rows, state,
            (classes.count(CLASS_PLAUSIBLE), classes.count(CLASS_IMPOSSIBLE)),
            age, ranges)


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
    # 🔴 A SECOND, WIDER PREDICATE — AND IT IS NOT A DUPLICATE OF THE ONE ABOVE.
    # `offered_universe` means "suppress the auto-open shortcut"; this means
    # "there are universe rows in this picker". They differ on the bare-`#N`
    # arm, which APPENDS the universe beneath the clawgate row and deliberately
    # leaves `offered_universe` False (two candidates, so the shortcut cannot
    # fire anyway). Keying the ordering note on the narrower one was the first
    # version of this change and it was WRONG on the commonest path: the whole
    # universe was ordered and the header said nothing at all.
    universe_shown = False
    may_offer_universe = (not args.print_only and not args.no_discovery
                          and not colour)
    num = span["id"] if (span is not None and span["id"].isdigit()) else offer_num
    # 🔴 `load_known_universe()` IS INSIDE THE CONDITIONAL EXPRESSION ON PURPOSE
    # — Python does not evaluate the true-branch when the condition is false, so
    # the file read costs NOTHING on the clicks that never show a picker, which
    # are the common ones. Hoisting it to its own statement above would put a
    # `stat` + `read` + `json.loads` on every `owner/repo#N` click, the exact
    # class of tax the lazy `concurrent.futures` import was moved to avoid.
    #
    # 🔴 AND THE ORDERING IS LAZIER STILL — IT IS DEFERRED PAST THIS CONDITION,
    # WHICH IS NOT THE SAME THING AS BEING INSIDE IT. THAT IS A MEASUREMENT.
    # The condition above is `may_offer_universe and num`, which is TRUE for an
    # explicit `owner/repo#N` — that click carries a number and bars no picker,
    # it simply never reaches an arm that shows one. So putting the ordering
    # reads inside it taxes the fastest path in the handler for a list it
    # discards. MEASURED on this host, 392 rows and a 400-row pick log:
    # `_ordered_universe` costs a median **1.94 ms** (0.26 load_known_ranges +
    # 1.09 load_picks + 0.20 the sort), against a ~30 ms click. The first
    # version of this change paid it on every `owner/repo#N`, and the comment
    # right above this one CLAIMED it did not — found by a latency probe, not
    # by review.
    #
    # `universe_rows()` therefore computes the rows AT MOST ONCE and only when
    # an arm is actually about to show them. The arms branch on
    # `universe_repos` — the cheap list — which is exactly as truthy as the
    # candidate list it maps 1:1 onto, so no predicate changed.
    universe_repos = (repo_universe(discovered, load_known_universe())
                      if (may_offer_universe and num) else [])
    order_state = ORDER_NO_TABLE
    order_counts = (0, 0)
    order_age: float | None = None
    # The table the ordering ACTUALLY used, kept so the click telemetry can name
    # the chosen row's plausibility class without a second, possibly disagreeing
    # read — see `_ordered_universe`. It stays `{}` when no ordering ran, which
    # is the predicate the telemetry reads.
    order_ranges: dict[str, int] = {}
    _universe_rows: list[list[dict]] = []

    def universe_rows() -> list[dict]:
        """The universe as ORDERED openable candidates, computed at most once.

        ⚠ THE MEMO IS AN INVARIANT GUARD, NOT AN OPTIMISATION — AND THE COMMENT
        HERE SAID THE OPPOSITE UNTIL A MUTATION SWEEP CAUGHT IT. It claimed "the
        guessed arm below can ask a second time", and that is FALSE: the three
        call sites are mutually exclusive. Dead end 1 and dead end 2 are an
        `if`/`elif`; dead end 1 sets `offered_universe`, which clears
        `guessed_offer`; and dead end 2 requires NO GitHub candidate while
        `guessed` requires one (a `default`-sourced repo IS a GitHub candidate).
        So exactly one site can fire per click, and the memo saves nothing
        today. Measured: a mutant defeating it SURVIVED the whole suite.

        It stays because the property it protects — one click, at most one
        ordering — should survive a fourth call site being added, and because
        the alternative is a reader re-deriving that three-way exclusivity from
        scratch. What it must NOT do is read as a live optimisation."""
        nonlocal order_state, order_counts, order_age, order_ranges
        if not _universe_rows:
            (rows, order_state, order_counts, order_age,
             order_ranges) = _ordered_universe(universe_repos, num)
            _universe_rows.append(universe_candidates(num, rows))
        return _universe_rows[0]

    if not candidates and universe_repos:
        # Dead end 1 — an unresolvable `repo#N`, or text the scanner refused
        # outright. Either way the operator now gets a choice instead of a toast.
        offered_universe = True
        universe_shown = True
        candidates = universe_rows()
    elif (span is not None and span["ambiguous"] and universe_repos
            and not any(c["platform"] == PLATFORM_GITHUB for c in candidates)):
        # Dead end 2 — a bare `#N` nothing could attribute. The clawgate
        # candidate STAYS FIRST so the common case is still one Enter away; the
        # universe is appended as the way to say "no, GitHub, this repo".
        candidates = candidates + universe_rows()
        universe_shown = True

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

    # 🔴 A GUESSED REPOSITORY IS OFFERED, NEVER OPENED — AND THAT IS DECIDED BY
    # `repo_source`, NOT BY THE SHAPE OF THE TEXT.
    #
    # `default` is the weakest rung of `mention_scan`'s attribution ladder: the
    # text named no repository at all and the answer came from the caller —
    # here, `tmux_pane_repo()`, which is explicitly best-effort (see its
    # docstring: it answers for the most recently active CLIENT, which is not
    # guaranteed to be the pane whose text was clicked). Every rung above it is
    # evidence ABOUT the reference — an owner the operator wrote, a name the
    # mapping resolved, a repo the same block named. `default` is evidence about
    # the WINDOW.
    #
    # MEASURED with the pane forced to `WRONGORG/wrongrepo`, base vs merged:
    # `audit-pr 1291` went from REFUSE to OPEN-DIRECTLY on
    # `github.com/WRONGORG/wrongrepo/issues/1291` — the first shape in this
    # handler that could open a page on a repository guessed from the pane
    # alone. A bare `#1291` escaped only by ACCIDENT: it happens to have a
    # clawgate sibling, so it was two candidates and got a picker. Nothing was
    # protecting it, and nothing would have protected the next shape either.
    #
    # 🔴 SO THE RULE IS AT THE `repo_source` LEVEL, not on `audit-pr`. Suppress
    # the shortcut and the pane's repo becomes a ROW TO CONFIRM — one keystroke
    # instead of zero, against a confident wrong page. Any future no-owner shape
    # inherits it for free.
    #
    # ⚠ IT ALSO COVERS `--default-repo`, because that is the same rung. No
    # caller passes it interactively (the Alacritty wrapper forwards the matched
    # text and nothing else) and `--print` returns above this line, so today
    # that costs nothing; if a script ever needs the old behaviour the answer is
    # `--print`, which is what it is for.
    guessed = span is not None and span["repo_source"] == SOURCE_DEFAULT

    # 🔴 A GUESS THE OPERATOR CANNOT OVERRIDE IS NOT AN OFFER — IT IS A PROMPT
    # WITH ONE WRONG ANSWER. Reported from the real click path 2026-09-07:
    # `audit-pr 1291` produced a ONE-ROW picker holding the pane's repo and the
    # note "names no repository". The row happened to be right that time, and
    # the operator's point stands — in practice the pane guess is often wrong,
    # and when it is, the picker offers no way to say so. Confirm the wrong
    # repo, or dismiss and type the URL by hand. Both are worse than the
    # refusal this branch replaced.
    #
    # The suppression above is still exactly right: a `default`-sourced repo is
    # evidence about the WINDOW, not about the reference, so it must never open
    # unconfirmed. What was missing is the other half — having declined to act
    # on the guess, offer the alternatives. The universe is already built and
    # already the answer everywhere else a repository cannot be named, and it is
    # fuzzy-matched, so 392 rows cost the operator a few keystrokes rather than
    # a scroll.
    #
    # 🔴 THE MEASURED ROWS STAY ON TOP, and that is the whole reason this is an
    # APPEND rather than a replace. The guess is the most likely answer and it
    # stays one or two Enters away, so the common case does not get slower; the
    # universe below it is what makes the uncommon case possible at all. Same
    # shape as the bare-`#N` arm in PASS 3, which keeps the clawgate candidate
    # first for the same reason.
    #
    # 🔴 IT IS NOT SCOPED TO THE GUESS THAT IS *ALONE* ANY MORE, AND THE
    # WIDENING IS THE OPERATOR'S CALL (2026-09-08: "fix the bare-#N and any
    # other cases left unfixed"). #1380 shipped this arm as `guessed_alone`,
    # reasoning that a bare `#N` the pane attributes already offers TWO good
    # rows and that burying them under several hundred was "a regression dressed
    # as a feature" — `test_a_bare_hash_N_that_the_PANE_already_attributes_does_
    # NOT_get_the_universe` pinned exactly that. The same commit wrote down the
    # complaint against its own narrowing: if the pane guess is wrong in the
    # two-row case, the right repo is still unreachable. It is the SAME defect
    # one rung along, the operator hit it, and the trade has now been made in
    # their favour. The cost is bounded by the ordering: rows 1 and 2 are
    # unchanged and the picker opens on row 1, so the common case is still one
    # Enter — fzf preserves INPUT ORDER while the query is empty, which `pick()`
    # documents and the suite verifies against a real fzf.
    #
    # 🔴 THE PREDICATE IS `repo_source == default`, NOT A SHAPE. `guessed` is
    # computed above from `mention_scan`'s own constant, so `--default-repo`
    # rides the same rung as the tmux pane and any future no-owner shape
    # inherits this for free.
    guessed_offer = guessed and not offered_universe

    # The guess's 1-based row, measured BEFORE the append so a universe row —
    # which is also a GitHub row — cannot be mistaken for it. In the terminal
    # profile a span carries at most one GitHub candidate: `audit-pr N` puts it
    # at row 1, a bare `#N` puts the clawgate task above it at row 2.
    guess_rank = next((i for i, c in enumerate(candidates, 1)
                       if c["platform"] == PLATFORM_GITHUB), 1) if guessed else 1

    # How many SEARCHABLE repository rows end up under the guess. 0 means the
    # append added nothing, and `guessed_note` needs that rather than a row
    # count — see its docstring.
    below = 0
    if guessed_offer and universe_repos:
        seen = {c["url"] for c in candidates}
        # Deduped: the pane's repo is usually IN the universe too, and offering
        # it twice makes the recommended row look like a rendering bug rather
        # than a recommendation.
        extra = [c for c in universe_rows() if c["url"] not in seen]
        # 🔴 GUARDED ON `extra`, NOT ON `universe`. A host whose whole universe
        # is the pane's own repo dedupes to nothing, and setting
        # `offered_universe` there would claim rows that are not in the list —
        # both to the auto-open guard below and to the note.
        if extra:
            candidates = candidates + extra
            offered_universe = True
            universe_shown = True
            below = len(extra)

    # 🔴 `and not offered_universe`: see PASS 3. One candidate is enough to open
    # only when that candidate is EVIDENCE about the reference — an explicit
    # owner, a measured checkout, a mapping hit. A universe row is an OPTION,
    # and a host that happens to know exactly one repository must not have that
    # option opened for it.
    if len(candidates) == 1 and not offered_universe and not guessed:
        # 🔴 THIS PATH USED TO RETURN WITHOUT RECORDING ANYTHING, AND THAT WAS
        # THE DEFECT. It is the COMMON, fast shape — an `owner/repo#N`, a
        # mapping hit, a measured checkout — so Tier B's pick log only ever saw
        # the clicks the handler could NOT answer. The scores therefore
        # described the operator's ambiguous minority and called it their
        # preference. It is recorded now, tagged `auto` so a later scoring
        # change can weight it differently from a row the operator read and
        # selected; see `PICK_VIA_AUTO`.
        #
        # Same three properties as the picker arm below, which is the point of
        # them being one function: only a github.com reference URL yields a
        # repository, `record_pick` swallows every OSError so a read-only home
        # costs the learning rather than the click, and neither can raise.
        auto_url = candidates[0]["url"]
        auto_repo = repo_of_github_url(auto_url)
        if auto_repo:
            record_pick(auto_repo, num, via=PICK_VIA_AUTO)
        # 🔴 EMITTED *AFTER* THE OPEN, WHICH IS THE OPPOSITE OF `record_pick`'S
        # ORDER AND IS DELIBERATE. The browser launch is what the operator is
        # waiting for and `open_url` is a non-blocking `Popen`, so paying the
        # telemetry import (a measured ~3.7 ms) behind it costs the click
        # nothing. `record_pick` stays in front because it is the signal that
        # must survive — it is what the NEXT click reads.
        #
        # 🔴 NO RANK, NO CLASS, NO TOTAL — AND THAT IS THE POINT OF OMITTING
        # THEM RATHER THAN ZEROING THEM. Nothing was offered here, so there is
        # no list to have been ranked in. `picker_shown=False` is what a
        # consumer filters on; see `click_dims`.
        rc = open_url(auto_url)
        emit_click(CLICK_AUTO_OPEN, repo=auto_repo,
                   platform=candidates[0]["platform"], picker_shown=False)
        return rc

    # 🔴 THE NOTE IS ATTACHED ONLY WHEN THE PICKER WOULD OTHERWISE BE
    # UNEXPLAINED, and there are exactly two such pickers: one carrying a GUESS
    # the operator is being asked to confirm or override, and one that is the
    # universe as a last resort. A picker over rows that are all evidence —
    # the clawgate/GitHub pair for a bare `#N` on a host with nothing else to
    # offer — is not a dead end and needs no explanation, so it still gets none.
    #
    # ⚠ `below or len(candidates) == 1` IS THE "WOULD OTHERWISE BE UNEXPLAINED"
    # TEST, WRITTEN OUT. `below` covers the picker whose bottom is a searchable
    # universe; `len(candidates) == 1` covers the lone guessed row, which
    # without a reason reads as a broken handler asking the operator to confirm
    # the obvious. The remaining case — a guess beside a clawgate row and
    # nothing under either — is the ordinary two-row picker and keeps `""`.
    #
    # 🔴 `guessed` IS TESTED FIRST, AND THE ORDER IS THE WHOLE POINT. Both
    # conditions are now true on the common path — the guessed arm above sets
    # `offered_universe` — and the two notes make OPPOSITE claims about the
    # rows. `universe_note` opens "nothing here knows X", which is false when
    # the top rows are a task and a recommendation the handler is asking about;
    # putting it first would have described the fix as a dead end. Swap these
    # two branches and the picker still works, so no behavioural test catches
    # it: the guard is
    # `test_a_guessed_picker_is_NOT_described_as_nothing_here_knows`.
    mesg = (guessed_note(span["raw"], below, guess_rank)
            if guessed_offer and span is not None
               and (below or len(candidates) == 1)
            else universe_note(span["raw"] if span is not None else text,
                               len(candidates)) if offered_universe
            else "")

    # 🔴 APPENDED, NEVER SUBSTITUTED, AND KEYED ON `universe_shown` RATHER THAN
    # `offered_universe` — see the comment where that variable is declared. It
    # is set by ALL THREE arms that put universe rows into `candidates` and by
    # none that does not, so a picker holding only measured rows (the ordinary
    # clawgate/GitHub pair) never claims an ordering it did not do, while the
    # bare-`#N` arm — which is the commonest shape and the one with several
    # hundred rows under the clawgate task — finally does.
    #
    # Both notes above already end in a sentence, and this is a second,
    # independent fact about the SAME list: the additive shape `refuse()` uses
    # for its staleness clause, for the same reason. Substituting would drop
    # whichever half happened to be checked second.
    if universe_shown:
        # `order_age` is the reading the STATE was decided from, not a second
        # `stat` — see `_ordered_universe`.
        extra = ordering_note(num, order_state, order_counts[0],
                              order_counts[1], order_age)
        if extra:
            mesg = f"{mesg} · {extra}" if mesg else extra

    url = pick(candidates, mesg=mesg)
    if not url:
        # 🔴 A DISMISSAL IS A MEASUREMENT, NOT A NON-EVENT — and it is the
        # DENOMINATOR the ordering question needs. "Did the plausibility
        # ordering help?" cannot be answered from the picks alone: a picker the
        # operator walked away from is the shape where the rows on offer were
        # wrong, and counting only the successes would make any ordering look
        # good. `offered_total` rides along because the list size is the other
        # half of that reading; there is no rank or class, because nothing was
        # chosen.
        emit_click(CLICK_DISMISSED, picker_shown=True,
                   offered_total=len(candidates))
        return 0
    # 🔴 RECORDED *AFTER* THE CHOICE AND *BEFORE* THE OPEN, AND IT CANNOT BLOCK
    # EITHER. `record_pick` swallows every OSError (see it), so a read-only home
    # costs the learning rather than the click. Only a github.com reference URL
    # yields a repository — a clawgate or ClickUp row returns "" from
    # `repo_of_github_url` and is not recorded as one.
    #
    # ⚠ IT RECORDS EVERY GITHUB ROW THE OPERATOR SELECTED, not only universe
    # rows. A pick is a pick: choosing the pane-guessed repo over the clawgate
    # task is the same evidence about what this operator means by a number, and
    # scoping the log to one arm would throw away the signal from the shape that
    # produces it most often.
    picked_repo = repo_of_github_url(url)
    if picked_repo:
        record_pick(picked_repo, num, via=PICK_VIA_PICKER)
    # 🔴 WHERE THE CHOSEN ROW SAT, WHICH IS THE MEASUREMENT THE ORDERING WAS
    # SHIPPED WITHOUT. #1509 ranks the universe by whether a repository could
    # plausibly hold `#N`, and nothing has ever recorded where in that list the
    # operator's row actually was — so the feature could not be evaluated by
    # anything except impression. `rank` is 0-based IN THE LIST AS PRESENTED
    # (`candidates` is exactly what went to `pick`), so a working ordering shows
    # as a rank distribution clustered near 0.
    #
    # ⚠ THE RANK IS LOOKED UP, NOT REMEMBERED. `row_to_url` maps a picker row
    # back to a URL by SUFFIX rather than by index, precisely so a picker that
    # decorates or reorders rows cannot open the wrong one — so an index carried
    # from the row text would be the one thing that arrangement refuses to
    # trust. `None` if the URL is somehow not in the list, which omits the field
    # rather than inventing a position.
    picked_rank = next((i for i, c in enumerate(candidates)
                        if c["url"] == url), None)
    # The class the ROW THE OPERATOR SAW was in, from the table that ordering
    # actually used — `{}` when no ordering ran, which omits the field. See
    # `_ordered_universe` and `click_dims` for why absent ≠ `unknown` here.
    picked_class = (plausibility_class(num, order_ranges.get(picked_repo.lower()))
                    if picked_repo and order_ranges else None)
    picked_platform = next((c["platform"] for c in candidates
                            if c["url"] == url), "")
    rc = open_url(url)
    emit_click(CLICK_PICKED, repo=picked_repo, platform=picked_platform,
               picker_shown=True, offered_total=len(candidates),
               rank=picked_rank, plausibility=picked_class)
    return rc


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
