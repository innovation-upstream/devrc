#!/usr/bin/env python3
"""Regenerate the mention repo mapping, universe and range table — OUTSIDE the repo.

    scripts/regen-known-repos.py [--print] [--path <file>] [--universe-path <file>]
                                 [--ranges-path <file>]

🔴 THE OUTPUT IS NOT TRACKED, AND THAT IS THE POINT. `gh api user/repos` returns
every repo the token can see, PRIVATE ONES INCLUDED. An earlier version of this
generator wrote its result to `scripts/collector/known_repos.py` and it was
committed: 232 private repos, 217 of them named nowhere else in the tree, went
into a repository that is PUBLIC. The names alone disclose unreleased products,
internal architecture and client relationships, and every content gate in this
repo was structurally blind to it — they scan JSON/JSONL/HTML/TXT and hostnames,
so a `.py` dict of repo names matched none of them.

So all three files are written to `~/.config/mention-open/` (mode 0600), which is
per-host, outside every checkout, and cannot be `git add`ed by accident.
`scripts/mention-open.py` reads them if they are there and works without any.

THREE OUTPUTS, THREE DIFFERENT QUESTIONS — AND THAT IS THE WHOLE DESIGN HERE
  * `known_repos.json`  — the RESOLUTION mapping: "what repo does the bare name
    `foo` in `foo#12` mean?". A wrong answer here opens a confident wrong page,
    so it is filtered hard (below) and an unanswerable name resolves to NOTHING.
  * `known_universe.json` — the PICKER universe: "which repos might I want to
    open, so I can fuzzy-type at them?". Every row is displayed as a full
    `owner/repo` and NOTHING is opened without the operator selecting it, so the
    filters that protect resolution buy nothing here and cost coverage.
  * `known_ranges.json` — the PICKER ORDER: "could this repository plausibly
    have a `#N` at all?". `{"owner/repo": <highest issue-or-PR number>}`. It
    ORDERS the universe rows and never filters them — see `build_ranges`.

🔴 DERIVING THE UNIVERSE FROM THE MAPPING WAS THE BUG. `repo_universe()` used to
read the mapping's `.values()`, which silently inherited both resolution filters.
MEASURED 2026-09-07 on this host: `gh api user/repos` returned **388** repos and
the picker offered **339** — 48 dropped for `has_issues == false` and 7 bare
names dropped as ambiguous (14 repos), overlapping to 53 absent. All 53 are repos
the operator owns or collaborates on, and none of them could be reached by typing
at the picker. The universe is now built from the API rows directly.

WHAT THE MAPPING FILTERS OUT, AND WHY EACH IS A WRONG ANSWER RATHER THAN A MISSING ONE
  * `has_issues == false` — a bare `repo#N` builds an issues URL. ⚠ THE ORIGINAL
    JUSTIFICATION HERE WAS TOO WIDE AND IS CORRECTED: it read "404s for EVERY N",
    which is false for PULL REQUESTS. MEASURED 2026-09-07 with a positive control
    (2 issues-ENABLED repos first, both `PULL`): **6 of 6** issues-disabled repos
    resolved `/issues/<pr>` to the pull request. An earlier probe that saw 3 of 4
    404 was confounded — unauthenticated `curl` 404s on a PRIVATE repo whatever
    the redirect does. So the filter is kept for the MAPPING on the narrower true
    reason — an issues-disabled repo has no ISSUES, so a bare `#N` naming a real
    issue number is a wrong answer — and is NOT applied to the universe, where
    the operator picks the repo themselves and PRs are the common case.
  * a bare name owned by TWO owners — last-write-wins silently picked one.
    Measured: 7 collisions, and `bitdex` resolved to a third party's fork rather
    than the client's repo. An ambiguous name resolves to NOTHING; the operator
    writes `owner/repo#N`, exactly as the module's no-guessing rule requires.
    Not applied to the universe either: a picker ROW is `owner/repo`, so there is
    no ambiguity left to arbitrate — that is precisely what the operator is being
    asked to settle.
  * linked worktrees (`.git` is a FILE, not a directory) — they share the base
    clone's remote, so they add no owner, and their transient names churned the
    output on every run. This one DOES apply to both.

🔴 `user/repos` IS ALREADY "EVERY REPO I CONTRIBUTE TO" — MEASURED, NOT ASSUMED.
Its default affiliation is `owner,collaborator,organization_member`, the widest
the endpoint offers. The obvious worry is a repo contributed to by PR without
membership, so it was measured: `search/issues?q=author:<login> type:pr` returned
**28** repos and **all 28** were already in `user/repos` — 0 new. Do not add a
second API source on the theory that it widens the set; it did not.

🔴 THE RANGE TABLE IS BATCHED GraphQL, NOT 392 REST CALLS, AND THAT IS MEASURED.
One `gh api graphql` request carrying one aliased `repository(...)` selection per
repo answers a whole batch at once. MEASURED 2026-09-11 against public repos:
25/50/75/100 aliases in ONE request all answered in full, at **2.15/2.14/2.27/
2.28s** — flat, so the batch size is not a latency knob and `RANGES_BATCH = 50`
is a conservative pick, not a tuned one. ~400 repos is ~8 requests, well inside
`mention-known-repos-refresh.service`'s `TimeoutStartSec=300`.

🔴 `gh api graphql` EXITS 1 WHEN *ANY* ALIAS ERRORS AND STILL RETURNS `data` FOR
THE REST — so branching on its exit code THROWS AWAY EVERY GOOD ANSWER IN THE
BATCH. MEASURED: a batch of 21 with one deleted repo in it exited **1**, printed
`errors: [{type: NOT_FOUND, path: ["r20"]}]`, and carried all **20** other
answers in `data`. `read_api_ranges` therefore parses stdout regardless of the
exit code and treats a `null` alias as UNKNOWN. This is the exact
`parsing-tool-output` shape `claude/RULES.md` names: the exit code is a claim
about the REQUEST, not about the rows.

🔴 UNKNOWN AND ZERO ARE DIFFERENT FACTS AND MUST NOT BE COLLAPSED. `0` means
"this repo has no issues and no pull requests", which is the STRONGEST signal in
the ordering scheme — it is the only value that can say a numeric reference is
impossible here. A repo the query could not answer for (deleted, renamed,
permission changed, batch failed) is UNKNOWN, and is recorded by being ABSENT
from the table: the reader's "no entry" arm already means exactly that, so no
sentinel value is invented that a future reader could mistake for a number.
⚠ `0` IS A REAL READING, not an artefact — MEASURED on `torvalds/linux`, where
`issues.totalCount` and `pullRequests.totalCount` are both genuinely 0.

EXIT CODES — AND WHY THERE ARE TWO KINDS OF FAILURE
  0  wrote (or, with --print, reported)
  3  🔴 A REAL FAILURE. `gh` is present and authenticated and the run still did
     not produce a trustworthy list — the API errored, or returned fewer than
     MIN_API_REPOS. Worth a toast; something is wrong.
  4  ⚠ NOT CONFIGURED ON THIS HOST — `gh` is absent, or present and not logged
     in. This is a legitimate state, not a fault, and `mention-open.py` degrades
     to `owner/repo#N` plus local checkouts without any of the files.
⚠ A FAILED *RANGES* LEG IS NOT EXIT 3, AND THAT IS DELIBERATE. The mapping and
the universe are what make a click resolve at all; the range table only ORDERS
rows that are offered either way, and `mention-open.py` degrades to today's
order without it. Failing the unit — and firing the DND-defeating toast — daily
for an ordering accelerator is the same objection that kept this timer out of
`nix/home.nix` for months. It is reported on stdout instead, and a range table
that stops being refreshed goes STALE, which the handler surfaces in the picker
header. That staleness is the deadman for this leg; the exit code is not.
🔴 THE SPLIT IS WHAT MAKES THE TIMER POSSIBLE, and it exists because the timer
was once REJECTED on exactly this ground: the note beside `STALE_MAPPING_DAYS`
in `mention-open.py` argued a scheduled run "would take a failing unit and a
failure toast on every fire" on a host without `gh auth`, and that a permanently
red timer is worse than no timer. That objection was correct against a single
failure code. `mention-known-repos-refresh.service` sets `SuccessExitStatus=4`,
so an unauthenticated host is quiet and a genuinely broken run still toasts.
Readiness is asked of `gh auth status`, whose exit code is a documented contract
— never by matching words in stderr, which would make the split depend on
`gh`'s phrasing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_CONFIG_DIR = Path(
    os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
) / "mention-open"

DEFAULT_PATH = _CONFIG_DIR / "known_repos.json"

# 🔴 A SECOND FILE RATHER THAN A NEW SHAPE IN THE FIRST, DELIBERATELY. The
# mapping is read by TWO deployed consumers — `mention-open.py` (from the working
# tree) and the collector's `session-tailer.py` (a nix `home.file` COPY that only
# changes on a `home-manager switch`). Changing `known_repos.json`'s shape would
# need both to change together, and they cannot: between the switch and the next
# regen the two would disagree about the file's format. A new file has no flag
# day — an old reader never opens it, and a new reader treats it as absent.
DEFAULT_UNIVERSE_PATH = _CONFIG_DIR / "known_universe.json"

# 🔴 A THIRD FILE, FOR THE SAME NO-FLAG-DAY REASON AS THE SECOND. An older
# `mention-open.py` never opens it; a newer one treats its absence as "every
# repo is UNKNOWN", which is exactly the cold-start behaviour (see
# `order_universe` there). Nothing has to change in lockstep.
DEFAULT_RANGES_PATH = _CONFIG_DIR / "known_ranges.json"

WORKSPACE = Path(os.environ.get("DEVRC_WORKSPACE", Path.home() / "workspace"))

# A `gh` leg that fails must not be mistaken for "the operator has few repos".
# The generator REFUSES rather than writing a 90%-smaller file and exiting 0 —
# the previous version's silent `except Exception: continue` is the same lossy
# class this whole change exists to fix.
MIN_API_REPOS = 25

# How many aliased `repository(...)` selections go into ONE GraphQL request.
# MEASURED 2026-09-11 (public repos, this host's `gh`): 25/50/75/100 aliases all
# answered 100% in a single request, at 2.15/2.14/2.27/2.28s. The curve is FLAT,
# so this is not a latency knob — 50 is picked for headroom against GitHub's
# node/complexity limits, which 100 did not reach either.
RANGES_BATCH = 50

# The whole ranges leg's budget. `gh api graphql` gets this per REQUEST, and ~8
# requests at ~2.3s each leaves the unit's TimeoutStartSec=300 untroubled.
RANGES_TIMEOUT = 60

EXIT_FAILED = 3
EXIT_NOT_CONFIGURED = 4


class NotConfigured(RuntimeError):
    """`gh` is absent or not logged in — an expected host state, not a fault.

    🔴 A SEPARATE TYPE, NOT A FLAG ON THE MESSAGE, because the caller must not
    have to read the sentence to decide whether to toast. See the module
    docstring's exit-code section for why the two are split at all.
    """

_SSH_REMOTE = re.compile(r"^(?:ssh://)?git@[^:/]+[:/](?P<path>.+?)(?:\.git)?/?$")
_HTTP_REMOTE = re.compile(r"^https?://[^/]+/(?P<path>.+?)(?:\.git)?/?$")

# 🔴 IMPORTED, NOT RE-DECLARED — the same choice `mention-open.py` makes at its
# own import block, for the same reason. What counts as a well-formed
# `owner/repo` is ONE rule owned by `mention_scan`, and this generator WRITES
# the files that module's consumers then filter on load. A second copy here
# would be a predicate at two sites: the day the rule tightens, this script
# keeps writing rows the reader silently drops, and the picker quietly loses
# repos with every file looking fine.
sys.path.insert(0, str(Path(__file__).resolve().parent / "collector"))

from mention_scan import OWNER_REPO_VALUE_RE as _OWNER_REPO_RE  # noqa: E402


def parse_owner_repo(remote_url: str) -> str:
    """`owner/repo` from a git remote URL, else "". Mirrors mention-open.py."""
    url = (remote_url or "").strip()
    if not url:
        return ""
    m = _SSH_REMOTE.match(url) or _HTTP_REMOTE.match(url)
    if not m:
        return ""
    parts = [p for p in m.group("path").split("/") if p]
    return f"{parts[0]}/{parts[1]}" if len(parts) == 2 else ""


def build_mapping(api_repos: list[dict], local_repos: dict[str, str]) -> dict[str, str]:
    """{name: "owner/repo"} from API rows + measured local checkouts.

    `api_repos` rows are `{"full_name": ..., "has_issues": ...}` as the search
    endpoint returns them. `local_repos` is {directory name: "owner/repo"},
    already measured from git remotes.

    A name that resolves to more than one owner is DROPPED rather than
    arbitrated — see the module docstring.

    🔴 A LOCAL CHECKOUT IS NOT A TIEBREAK HERE, and this is deliberately
    NARROWER than the handler's runtime rule. `mention-open.py` overlays
    checkouts on top of this mapping and lets them win, because at click time it
    has just MEASURED the remote. This file is a SNAPSHOT that can be months
    old, so a checkout disagreeing with an API row is two claims about one name
    with no way to tell which is current — and the module's rule for that is to
    resolve to NOTHING and let the operator write `owner/repo#N`. What a
    checkout does do is: supply a name the API never had (its directory name);
    confirm one the API agrees with; and SETTLE a name the API dropped as
    ambiguous, because two owners the operator can merely SEE is a weaker fact
    than one repo they actually have on disk. What it may not do is overrule a
    specific API row that names a different repo.
    """
    owners: dict[str, set[str]] = {}
    for row in api_repos:
        full = (row.get("full_name") or "").strip()
        if not row.get("has_issues") or "/" not in full:
            continue
        owners.setdefault(full.rsplit("/", 1)[-1].lower(), set()).add(full)

    out: dict[str, str] = {}
    for row in api_repos:
        full = (row.get("full_name") or "").strip()
        if not row.get("has_issues") or "/" not in full:
            continue
        name = full.rsplit("/", 1)[-1]
        if len(owners[name.lower()]) > 1:
            continue
        # BOTH spellings: mention_scan._resolve_repo does an EXACT dict lookup,
        # and GitHub repo names are case-insensitive, so a canonical-case key
        # alone cannot match `comfyui#12`.
        out[name] = full
        out[name.lower()] = full

    # 🔴 THE OVERLAY OBEYS THE SAME FILTERS AS THE API PASS. Writing local
    # entries in unconditionally re-opened both holes this function exists to
    # close: a checkout of an issues-disabled repo went back in, and two
    # checkouts sharing a bare name went back to last-write-wins.
    #
    # 🔴 EVERYTHING HERE IS CASE-FOLDED, ON BOTH SIDES, INCLUDING THE DROP.
    # Keys are written in two spellings, so an exact-case comparison is blind:
    # a repo cloned via a lowercase URL (`acme/plotwidget`) looked unrelated to
    # the API's `acme/PlotWidget`, and a drop that popped only the two spellings
    # it knew about left OTHER casings resolving to a name just judged
    # ambiguous. Differential fuzz against the pre-fix code: IDENTICAL while
    # every spelling is lowercase, differing in tens of percent of mixed-case
    # inputs — which is exactly why "I mutated it and nothing changed" was the
    # wrong evidence for calling one of these clauses redundant. ⚠ No exact
    # count is quoted on purpose: two independent harnesses agreed on the
    # direction and disagreed on the number, and neither is committed here, so
    # a figure in this comment could not be re-derived from the tree.
    issues_off = {(row.get("full_name") or "").strip().lower()
                  for row in api_repos if not row.get("has_issues")}

    def claimed(spelling: str) -> str | None:
        """What `out` already resolves `spelling` to, under ANY casing."""
        low = spelling.lower()
        return next((v for k, v in out.items() if k.lower() == low), None)

    def drop(spelling: str) -> None:
        """Remove EVERY casing of `spelling` — a drop that leaves one behind
        still resolves the name the code has just called ambiguous."""
        low = spelling.lower()
        for key in [k for k in out if k.lower() == low]:
            out.pop(key)

    usable = {name: full for name, full in local_repos.items()
              if "/" in full and full.lower() not in issues_off}

    # Which repos each spelling could mean, across checkouts. Needed BEFORE the
    # loop: a clash between two case-variant spellings must be caught even when
    # neither has been written yet, which a check against `out` alone cannot do.
    local_owners: dict[str, set[str]] = {}
    for name, full in usable.items():
        for key in (name.lower(), full.rsplit("/", 1)[-1].lower()):
            local_owners.setdefault(key, set()).add(full.lower())

    # Spellings proven ambiguous. A DROP IS FINAL: re-deriving "is this
    # ambiguous?" from `out` alone is what let a dropped key come back, because
    # `name` and `bare` are often the same string and the second visit found it
    # absent.
    dropped: set[str] = set()
    for name, full in usable.items():
        bare = full.rsplit("/", 1)[-1]
        for spelling in (name, bare):
            low = spelling.lower()
            if low in dropped:
                continue
            existing = claimed(spelling)
            # ⚠ THIS FIRST CHECK IS REDUNDANT ON THIS CODE, AND STAYS ANYWAY.
            # Differential fuzz, 40,000 inputs per alphabet: disabling it
            # changes NOTHING — 0 differences on both — because `claimed()` is
            # case-insensitive and the loop is sequential, so whichever
            # checkout writes a spelling first, the next disagreeing one sees
            # it. NO TEST CAN PIN IT for the same reason; the test that names
            # it says so and is labelled an invariant guard.
            #
            # 🔴 It is here because deleting it ONCE ALREADY WENT WRONG. Before
            # `claimed()` existed the lookup was `out.get(sp) or
            # out.get(sp.lower())` — folded on ONE side only — and the clause
            # was load-bearing;
            # a fuzz over lowercase-only inputs reported no difference, and it
            # was deleted as redundant on that evidence — case being the exact
            # dimension that mattered. THREE independent harnesses have since
            # agreed it mattered and disagreed on the magnitude by about 2x,
            # and NONE of them lives in this repo, so no count here is
            # reproducible from the tree. That is why this comment quotes none.
            #
            # ⚠ The order-independence rationale an earlier comment gave for
            # keeping it was MEASURED HOLLOW: over 4,000 cases × every
            # permutation of the checkouts, the clause changes NOTHING about
            # order-sensitivity — the same count of order-varying outcomes with
            # it on and off, and zero once compared case-folded. (The residual
            # variance is the CASING of a stored `owner/repo`, the same repo
            # either way, and `read_local_repos` returns sorted entries anyway.)
            # The honest reason to keep it is the paragraph above, not a
            # property it has.
            if (len(local_owners[low]) > 1
                    # …or this checkout's name already names a DIFFERENT repo:
                    # an API row it disagrees with, or an earlier checkout.
                    # Agreeing is not a collision — that is the checkout
                    # confirming an owner.
                    or (existing is not None and existing.lower() != full.lower())):
                dropped.add(low)
                drop(spelling)
                continue
            out[spelling] = full
            out[spelling.lower()] = full
    return out


def require_gh_ready() -> None:
    """Raise `NotConfigured` unless `gh` is present AND logged in.

    🔴 THE READINESS QUESTION IS ASKED OF AN EXIT CODE, NEVER OF STDERR.
    `gh auth status` is documented to exit non-zero when there is no usable
    token, and that contract is stable in a way its wording is not — a guard
    that matched "not logged in" would silently start reporting every host as
    configured the day `gh` rephrases the message, which is the parsing-tool-
    output trap. Nothing here reads what it printed.

    ⚠ IT IS A PRECONDITION, NOT A PREDICTION. A token can be revoked between
    this call and the API call below; that lands as a genuine failure (exit 3)
    and SHOULD, because a host that was configured a second ago and is not now
    is worth a toast.
    """
    try:
        r = subprocess.run(["gh", "auth", "status"],
                           capture_output=True, text=True, timeout=30)
    except FileNotFoundError as exc:
        raise NotConfigured("gh is not installed on this host") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise NotConfigured(f"gh auth status could not be run: {exc}") from exc
    if r.returncode != 0:
        raise NotConfigured("gh is installed but not logged in "
                            "(`gh auth status` exited non-zero) — run `gh auth login`")


def build_universe(api_repos: list[dict],
                   local_repos: dict[str, str]) -> list[str]:
    """Every distinct `owner/repo` worth OFFERING, sorted — the picker universe.

    🔴 THIS IS DELIBERATELY WIDER THAN `build_mapping`, AND THE ASYMMETRY IS THE
    POINT. Neither of that function's two filters is applied:

      * `has_issues` is not consulted. A universe row is displayed as a full
        `owner/repo` and turned into `/issues/<n>`, and GitHub resolves that to
        the PULL REQUEST even where issues are disabled — measured 6/6, with a
        positive control, on this host's own issues-disabled repos. Dropping
        them cost 48 repos to protect against a case (a bare number that is a
        real ISSUE id in a repo with no issues) the operator has already ruled
        out by choosing the row.
      * ambiguity is not arbitrated, because there is none left to arbitrate.
        `build_mapping` drops `bitdex` when two owners have one; here BOTH
        `alice/bitdex` and `bob/bitdex` are offered, spelled out, and the
        operator picks. That is a better answer than the mapping's silence and a
        far better one than last-write-wins.

    Local checkouts are unioned in rather than overlaid: this is a list, not a
    lookup, so a checkout cannot contradict an API row — it can only add one the
    API never returned (a clone of somebody else's repository).

    🔴 DEDUPED CASE-INSENSITIVELY, KEEPING THE API'S SPELLING. GitHub repo names
    are case-insensitive, so `civitai/ComfyUI` from the API and `civitai/comfyui`
    from a lowercase clone URL are ONE repository; offering both would put two
    identical-looking rows in front of the operator. The API row wins because it
    is canonical — a remote URL preserves whatever the person who cloned typed.

    🔴 THE RETURN VALUE NAMES PRIVATE REPOSITORIES. It goes to a 0600 file and,
    at click time, to the operator's own rofi window — never to a log, a
    notification body, a telemetry row or a test fixture. See the module
    docstring for the incident that established this.
    """
    by_key: dict[str, str] = {}
    for row in api_repos:
        full = (row.get("full_name") or "").strip()
        if _OWNER_REPO_RE.match(full):
            by_key.setdefault(full.lower(), full)
    for full in local_repos.values():
        full = (full or "").strip()
        if _OWNER_REPO_RE.match(full):
            by_key.setdefault(full.lower(), full)
    # Sorted case-insensitively so the picker's order matches how it reads.
    return sorted(by_key.values(), key=str.lower)


def ranges_query(batch: list[str]) -> str:
    """One GraphQL document asking `batch`'s highest issue AND pull-request
    number, one aliased `repository(...)` selection per repo.

    🔴 BOTH LISTS, NOT JUST ISSUES. GitHub numbers issues and pull requests out
    of ONE sequence per repository, so the highest of the two is the highest
    reference THIS HANDLER CAN OPEN — and a repo with issues DISABLED (48 of
    them on this host, see `build_universe`) would report 0 from the issues list
    alone while its PR numbers run into the thousands. Asking only one list
    would manufacture exactly the false `IMPOSSIBLE` verdict the reader ranks
    last.

    ⚠ DISCUSSIONS ARE IN THAT SEQUENCE TOO AND ARE DELIBERATELY NOT ASKED FOR.
    MEASURED 2026-09-11 on four public repos with discussions enabled
    (`vercel/next.js` 98550 vs 98580/98581, `astral-sh/ruff` 28504 vs
    28519/28530, `vitejs/vite` 23457 vs 23470/23469): the newest discussion sits
    interleaved just below the issue/PR head, which is the signature of ONE
    shared counter. Including them would be WRONG FOR THIS PURPOSE, not merely
    unnecessary: `mention-open.py` turns a chosen row into `/issues/<n>`, and
    GitHub does not redirect that to a discussion — discussions live at
    `/discussions/<n>`. Counting them would file a repo as PLAUSIBLE for a
    number whose URL 404s, and a repo with ONLY discussions reports 0 and is
    ranked last, which is the right answer for the same reason. So the sentence
    above says "can open", not "has": those are different numbers and an
    earlier draft conflated them.

    ⚠ `orderBy: CREATED_AT DESC, first: 1` RATHER THAN `totalCount`. The count
    is how MANY, which is not the same number: a repo with 10 issues whose
    numbering interleaves 400 pull requests has a highest reference in the
    hundreds. Newest-created is the highest number, because the sequence is
    monotonic in creation.

    The alias is positional (`r<i>`), so the caller re-pairs answers with
    `batch` BY INDEX. Anything that reorders this list breaks that pairing — see
    `read_api_ranges`, which zips them.
    """
    parts = []
    for i, full in enumerate(batch):
        owner, name = full.split("/", 1)
        parts.append(
            f'  r{i}: repository(owner: "{owner}", name: "{name}") {{\n'
            f'    issues(first: 1, orderBy: {{field: CREATED_AT, direction: DESC}}) '
            f'{{ nodes {{ number }} }}\n'
            f'    pullRequests(first: 1, orderBy: {{field: CREATED_AT, direction: DESC}}) '
            f'{{ nodes {{ number }} }}\n'
            f'  }}')
    return "query {\n" + "\n".join(parts) + "\n}\n"


def _highest_ref(node: dict) -> int:
    """The highest issue-or-PR number in one `repository` selection."""
    numbers = [0]
    for key in ("issues", "pullRequests"):
        for row in ((node.get(key) or {}).get("nodes") or []):
            n = row.get("number")
            if isinstance(n, int) and n > 0:
                numbers.append(n)
    return max(numbers)


def read_api_ranges(full_names: list[str],
                    runner=subprocess.run) -> dict[str, int]:
    """{"owner/repo": highest ref number} — UNANSWERED REPOS ARE ABSENT.

    🔴 THE EXIT CODE IS NOT THE VERDICT, AND READING IT AS ONE THROWS AWAY THE
    BATCH. MEASURED 2026-09-11: a 21-alias request containing ONE nonexistent
    repo exited **1** with `errors: [{type: NOT_FOUND, path: ["r20"]}]` and all
    20 other answers present in `data`. So this parses stdout whatever the exit
    code says, and only an UNPARSEABLE body costs the whole batch. `claude/
    RULES.md` calls this out by name: a tool's exit code is a claim about the
    tool, never about your data.

    🔴 AN UNANSWERED REPO IS ABSENT, NEVER 0. 0 is the strongest signal the
    ordering has — it is what makes a repo IMPOSSIBLE for any `#N` — so writing
    it for "I could not ask" would rank a perfectly good repository last on no
    evidence. Absence maps to the reader's UNKNOWN class, which sits ABOVE
    IMPOSSIBLE. They are different facts and the file keeps them different.

    ⚠ A BATCH THAT FAILS OUTRIGHT COSTS ONLY ITS OWN REPOS. One `gh` invocation
    that times out or dies leaves its 50 repos UNKNOWN and the run continues;
    the alternative — abandoning the leg — would turn one transient failure into
    a table nobody can order by.
    """
    out: dict[str, int] = {}
    for start in range(0, len(full_names), RANGES_BATCH):
        batch = full_names[start:start + RANGES_BATCH]
        try:
            # 🔴 THE DOCUMENT GOES ON *STDIN*, NEVER ON argv, AND THAT IS A
            # DISCLOSURE RULE RATHER THAN A STYLE. `ranges_query` interpolates
            # up to 50 PRIVATE `owner/repo` names into it, and a process's argv
            # is world-readable in `/proc/<pid>/cmdline` and in `ps` for the
            # life of the call — ~2.3s × 8 requests. The pre-existing
            # `gh api user/repos` names nothing, so this leg introduced the
            # surface; `mention-open.py` already routes the picker rows through
            # a FIFO for exactly this reason.
            #
            # ⚠ `-F`, NOT `-f` — MEASURED, because the two differ precisely
            # here. `-F query=@-` reads the value from stdin; `-f query=@-`
            # sends the literal two characters `@-` and `gh` answers
            # `Expected one of SCHEMA, SCALAR, TYPE, … actual: DIR_SIGN`.
            r = runner(["gh", "api", "graphql", "-F", "query=@-"],
                       input=ranges_query(batch),
                       capture_output=True, text=True, timeout=RANGES_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            continue
        try:
            doc = json.loads(r.stdout)
        except (ValueError, TypeError):
            continue
        data = doc.get("data") if isinstance(doc, dict) else None
        if not isinstance(data, dict):
            continue
        # 🔴 ZIPPED BY INDEX, because the alias IS the index — see
        # `ranges_query`. A `for full in batch` loop that recomputed the alias
        # from the NAME would be a second spelling of the same pairing rule, and
        # a mismatch would silently attach one repo's range to another's row.
        for i, full in enumerate(batch):
            node = data.get(f"r{i}")
            if isinstance(node, dict):
                out[full] = _highest_ref(node)
    return out


def build_ranges(universe: list[str], api_ranges: dict[str, int]) -> dict[str, int]:
    """{"owner/repo": highest ref} for the repos the picker can actually offer.

    🔴 KEYED OFF THE UNIVERSE, NOT OFF THE API ROWS. The table's only consumer
    orders the picker, and a range for a repo that never appears as a row is
    dead weight in a 0600 file that names private repositories — so the set of
    keys is exactly the set of things it can order, and nothing wider.

    🔴 KEYS ARE LOWERCASED. GitHub repository names are case-insensitive, so
    `acme/Widget` and `acme/widget` are ONE repo; the reader looks up a universe
    row (which keeps the API's canonical casing) by `.lower()`, and a table
    keyed on the canonical spelling would miss every row whose casing came from
    a lowercase clone URL. This is the same case-folding lesson `build_mapping`
    above pays for at length.
    """
    lowered = {k.lower(): v for k, v in api_ranges.items()}
    out: dict[str, int] = {}
    for full in universe:
        hit = lowered.get(full.lower())
        if isinstance(hit, int) and hit >= 0:
            out[full.lower()] = hit
    return out


def write_ranges(ranges: dict[str, int], path: Path) -> None:
    """Write 0600, parent 0700 — its KEYS name private repositories.

    ⚠ THE DISCLOSURE IS IN THE KEYS HERE, NOT THE VALUES, AND THAT IS A NEW
    SHAPE. `known_repos.json` leaks through its values and `known_universe.json`
    through its elements; this one is the mirror image, which is precisely why
    the repo's own tracked-file guard had to grow a third detector
    (`looks_like_a_repo_range_table` in `scripts/tests/
    test_regen_known_repos.py`) — the existing two score this file **0**.

    Same atomic tmp-then-replace as its siblings: a reader that opens the file
    mid-rewrite must see the old content or the new, never a truncated JSON
    document, because `mention-open.py` answers a parse error with an empty
    table and would silently fall back to an unordered picker.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(ranges, indent=1, sort_keys=True) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def write_universe(universe: list[str], path: Path) -> None:
    """Write 0600, parent 0700 — it names private repositories.

    Same atomic tmp-then-replace as `write_mapping`: a reader that opens the
    file while it is being rewritten must see the old content or the new, never
    a truncated JSON document. `mention-open.py` answers a parse error with an
    empty universe, so a torn write would silently produce an empty picker.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(universe, indent=1) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def read_api_repos() -> list[dict]:
    """Rows from `gh api user/repos`. Raises RuntimeError — never returns a
    short list quietly, because a short list is indistinguishable from a fine
    one once it has been written."""
    try:
        r = subprocess.run(
            ["gh", "api", "user/repos", "--paginate", "--jq",
             ".[] | {full_name, has_issues} | tostring"],
            capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"gh could not be run: {exc}") from exc
    if r.returncode != 0:
        raise RuntimeError(f"gh exited {r.returncode}: {r.stderr.strip()[:200]}")
    rows = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if len(rows) < MIN_API_REPOS:
        raise RuntimeError(
            f"gh returned only {len(rows)} repo(s), below the floor of "
            f"{MIN_API_REPOS} — refusing to write a mapping that is probably "
            f"truncated. Check `gh auth status`.")
    return rows


def read_local_repos(workspace: Path | None = None) -> dict[str, str]:
    """{directory name: "owner/repo"} for real clones under `workspace`.

    🔴 Resolved at CALL time — a `= WORKSPACE` default binds at import and makes
    every test that patches the module attribute inert."""
    workspace = workspace or WORKSPACE
    out: dict[str, str] = {}
    try:
        entries = sorted(p for p in workspace.iterdir() if p.is_dir())
    except OSError:
        return out
    for entry in entries:
        dotgit = entry / ".git"
        # A linked worktree's .git is a FILE holding 'gitdir: …' — skip it.
        if not dotgit.is_dir():
            continue
        try:
            r = subprocess.run(["git", "remote", "get-url", "origin"],
                               cwd=str(entry), capture_output=True,
                               text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        full = parse_owner_repo(r.stdout if r.returncode == 0 else "")
        if full:
            out[entry.name] = full
    return out


def write_mapping(mapping: dict[str, str], path: Path) -> None:
    """Write 0600, parent 0700 — it names private repositories."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(mapping, indent=1, sort_keys=True) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--path", type=Path, default=DEFAULT_PATH)
    ap.add_argument("--universe-path", type=Path, default=DEFAULT_UNIVERSE_PATH)
    ap.add_argument("--ranges-path", type=Path, default=DEFAULT_RANGES_PATH)
    ap.add_argument("--print", action="store_true", dest="print_only",
                    help="write nothing; report what WOULD be written")
    ap.add_argument("--no-ranges", action="store_true",
                    help="skip the GraphQL range table (the picker then keeps "
                         "its existing order)")
    args = ap.parse_args(argv)

    # 🔴 READINESS IS ASKED BEFORE THE WORK, so an unconfigured host exits 4
    # without a 120-second paginated API call it cannot make. `NotConfigured` is
    # caught FIRST — it subclasses RuntimeError, so the wider `except` below
    # would swallow it and report a real failure on a host that simply has no
    # `gh`. Ordering is the guard here; there is a test that inverts it.
    try:
        require_gh_ready()
    except NotConfigured as exc:
        print(f"regen-known-repos: not configured on this host — {exc}",
              file=sys.stderr)
        return EXIT_NOT_CONFIGURED

    try:
        api = read_api_repos()
    except RuntimeError as exc:
        print(f"regen-known-repos: {exc}", file=sys.stderr)
        return EXIT_FAILED

    local = read_local_repos()
    mapping = build_mapping(api, local)
    universe = build_universe(api, local)
    dropped = len([r for r in api if not r.get("has_issues")])
    # What the mapping's filters cost the picker — printed because it is the
    # whole reason the universe is built separately, and a number nobody can
    # see is a number nobody checks.
    only_universe = len(set(universe) - set(mapping.values()))

    if args.print_only:
        print(f"{len(mapping)} key(s) from {len(api)} repo(s) "
              f"({dropped} with issues disabled, skipped); "
              f"{len(local)} local checkout(s). Would write {args.path}")
        print(f"{len(universe)} repo(s) in the picker universe "
              f"({only_universe} of them reachable ONLY via the picker). "
              f"Would write {args.universe_path}")
        print(f"would ask {'no' if args.no_ranges else len(universe)} repo(s) "
              f"for their highest reference number "
              f"({(len(universe) + RANGES_BATCH - 1) // RANGES_BATCH if not args.no_ranges else 0}"
              f" GraphQL request(s)). Would write {args.ranges_path}")
        return 0

    write_mapping(mapping, args.path)
    write_universe(universe, args.universe_path)
    print(f"wrote {args.path} — {len(mapping)} key(s) from {len(api)} repo(s) "
          f"({dropped} with issues disabled, skipped)")
    print(f"wrote {args.universe_path} — {len(universe)} repo(s) "
          f"({only_universe} reachable ONLY via the picker)")

    # 🔴 THE RANGES LEG RUNS LAST AND CANNOT FAIL THE RUN. See the module
    # docstring: the two files above are what make a click RESOLVE; this one
    # only ORDERS rows that are offered either way. A red unit every day for an
    # ordering accelerator is the objection that kept this timer unwritten.
    #
    # 🔴 AND "CANNOT FAIL THE RUN" IS NOW ENFORCED RATHER THAN ASSERTED. The
    # first version of this block had no `try`, so an exception ANYWHERE in the
    # leg propagated out of `main()` — a non-zero exit, a red unit, and the
    # DND-defeating `notify-failure@` toast, which is precisely what the
    # paragraph above says must not happen. MEASURED by an adversarial audit:
    # `write_ranges` into a read-only directory raises `PermissionError`, and
    # `_highest_ref` raises `AttributeError` on a non-dict `issues` — neither
    # was caught by `read_api_ranges`' narrower `except (ValueError, TypeError)`.
    # The guard's own test named "a failed ranges leg" while exercising only the
    # EMPTY-TABLE branch: a docstring naming a relationship over a body
    # inspecting one side.
    #
    # ⚠ `except Exception`, DELIBERATELY BROAD AND DELIBERATELY NOT BARE — the
    # same posture as `mention-open.py`'s `guarded_main`. `KeyboardInterrupt`
    # and `SystemExit` derive from `BaseException` and must still terminate.
    if not args.no_ranges:
        try:
            ranges = build_ranges(universe, read_api_ranges(universe))
            if ranges:
                write_ranges(ranges, args.ranges_path)
                zero = sum(1 for v in ranges.values() if v == 0)
                # 🔴 BOTH NUMBERS, ALWAYS. "answered 340" alone is the
                # silent-zero shape: it is indistinguishable from a leg that
                # asked about 340 repos when the universe holds 392. The gap IS
                # the UNKNOWN class.
                print(f"wrote {args.ranges_path} — {len(ranges)} of "
                      f"{len(universe)} repo(s) answered "
                      f"({len(universe) - len(ranges)} unknown, {zero} with no "
                      f"references at all)")
            else:
                # Deliberately NOT exit 3 — and deliberately not silent either.
                # The existing table is left in place rather than replaced by an
                # empty one; it then AGES, and `mention-open.py` degrades to
                # today's order and says so in the picker header. That staleness
                # is this leg's deadman.
                print(f"range table NOT written — 0 of {len(universe)} repo(s) "
                      f"answered; {args.ranges_path} left as it was (the "
                      f"handler degrades to an unordered picker)",
                      file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 — see the block comment above
            print(f"range table NOT written — the ranges leg failed with "
                  f"{type(exc).__name__}: {exc}; {args.ranges_path} left as it "
                  f"was (the handler degrades to an unordered picker). This is "
                  f"NOT a run failure: the mapping and the universe above were "
                  f"written", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
