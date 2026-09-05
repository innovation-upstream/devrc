#!/usr/bin/env python3
"""mention_scan — find cross-platform reference MENTIONS in agent output.

PURE. No I/O, no subprocess, no network, stdlib only. Two consumers:

  * `scripts/collector/claude/session-tailer.py` — emits one telemetry event per
    detected mention into activity.events (source=mentions).
  * `scripts/mention-open.py` — the Alacritty hint handler. Alacritty hands it
    the matched text; it re-scans that text with the SAME rules and opens (or
    offers) the resolved URL.

🔴 THE TWO CONSUMERS NO LONGER SHARE ONE PATTERN SET, AND THAT IS DELIBERATE.
This module used to run exactly one set of regexes so that "what the terminal
underlines" and "what the telemetry records" could never drift apart. That
invariant is now RELAXED, on purpose, along one axis only: `profile`.

  profile="terminal"  (the DEFAULT)  — today's narrow, click-safe surface. A
      match here becomes an underlined, clickable span on the operator's screen,
      so a false positive is a wrong page opening. Unchanged, byte for byte.
  profile="telemetry" — a WIDER surface, used only by the tailer. A match here
      becomes one row in a private ClickHouse table, so a false positive costs a
      stray row and nothing else.

The relaxation is EXPLICIT rather than implicit: every pattern in this module is
listed in `PATTERN_LEDGER` with the profiles it is consulted in, and
`scripts/tests/test_mention_scan.py` pins that ledger TWO-WAY — a pattern with no
entry fails, an entry naming no pattern fails. The default is `terminal`, so a
caller that says nothing keeps the narrow behaviour.

WHAT IS DETECTED
----------------
Both profiles:
  clickup   868abc123                 -> https://app.clickup.com/t/868abc123
  github    owner/repo#12             -> https://github.com/owner/repo/issues/12
  github    repo#12                   -> owner resolved by the CALLER (see below)
  ambiguous #12                       -> clawgate task 12 OR a GitHub issue 12

profile="telemetry" only — an ENUMERATED widening, never a generic pattern:
  github    github.com/owner/repo/pull/12   (and /issues/12)
  github    /audit-pr 12   audit-pr 12
  github    gh pr view 12  gh issue close 12   (enumerated subcommands)
  clawgate  clawgate task 12            (`task 12` ALONE is NOT detected)
  clawgate  #task-12                    (the legacy anchor form)

🔴 WHAT IS DELIBERATELY NOT DETECTED, measured rather than assumed: a bare
`task N` (179 occurrences in one 24h window, almost all of them prose), a bare
`PR N`, and abbreviated git shas — a `[0-9a-f]{7,12}` probe over the same window
returned 520,000 hits. Each of those is a generic pattern, which is exactly the
shape the ClickUp `DEV-123` note below refuses.

A bare `#N` is genuinely ambiguous BETWEEN PLATFORMS and this module does NOT
pick a winner. It returns a candidate per platform and lets the click-time
resolver disambiguate with facts it can actually measure (the pane's repo). See
`scan_mention_spans`.

ATTRIBUTION — a separate question from ambiguity
------------------------------------------------
"Which platform" and "which repository" are different questions. A bare `#N` can
be attributed to a repository while still being ambiguous between clawgate and
GitHub, and 92% of the mentions in a measured 24h window were bare `#N`. So
every candidate and every span now carries `repo` (an `owner/repo`, or "") and
`repo_source` naming HOW it was resolved, in this priority order:

  "explicit"  an `owner/repo#N` written out in the text            (both profiles)
  "mapped"    a bare `repo#N` — the text named the REPO but not the OWNER, and
              the owner came from the caller-measured `repos` mapping
                                                                   (both profiles)
  "adjacent"  a repo token immediately before the ref — `devrc PR #1291` —
              looked up in the caller-measured `repos` mapping     (telemetry)
  "url"       a `github.com/owner/repo/(pull|issues)/N` URL in the same text
                                                                   (telemetry)
  "flag"      a `--repo owner/repo` in the same text               (telemetry)
  "default"   the caller-supplied `default_repo`                   (both profiles)
  ""          not attributed

🔴 `explicit` MEANS THE TEXT WROTE THE OWNER OUT, AND NOTHING ELSE. `repo#N` and
`owner/repo#N` are different evidence: the first is a name the caller's mapping
resolved, the second is a repository the operator stated. Reporting both as
`explicit` — which this module did until the distinction was measured — makes an
analysis that cannot separate "the text said so" from "our mapping said so", i.e.
one measurement pretending to be two. `mapped` is the second of them.

🔴 "url" and "flag" answer only when the text names EXACTLY ONE distinct
`owner/repo` by that route. Two different repositories in one block is not a
tie to be broken by position — it is an absence of an answer, and inventing one
is the failure this whole module is anchored against.

🔴 AND "adjacent" ANSWERS ONLY THROUGH `repos`. A repo token that the caller has
not MEASURED yields nothing: the ref stays unattributed. There is no default
org, no "probably his", and no synthesised owner anywhere on this path.

🔴 NO OWNER IS EVER GUESSED. `scripts/check-clickup-addressed/check-completion.py`
already paid for this lesson in blood — its `KNOWN_REPOS` header records that a
guessed owner "yields a confident state for the WRONG PR, which is worse than
admitting the citation is ambiguous", and that `civitai/devrc` does not exist
while `innovation-upstream/devrc` does. So `repo#N` with an unknown owner comes
back with `url == ""`, not with a plausible-looking URL. The caller injects what
it has MEASURED via the `repos` / `default_repo` parameters; this module invents
nothing.

🔴 A NOISY SCANNER IS WORSE THAN NONE. `#` is one of the most common characters
in a terminal — comments, markdown headings, colour literals, CSS, URL
fragments, HTML entities. Every pattern below is anchored on BOTH sides and the
negative cases are pinned in `scripts/tests/test_mention_scan.py`. Read
`_KNOWN_FALSE_POSITIVES` before widening anything.
"""
from __future__ import annotations

import re
from typing import NamedTuple

# --------------------------------------------------------------------------- #
# Profiles
# --------------------------------------------------------------------------- #
# 🔴 `terminal` IS THE DEFAULT, and that is load-bearing. `scripts/mention-open.py`
# calls this module with no `profile`, so the click surface can only widen if
# somebody types the word — it cannot widen as a side effect of a change made for
# the telemetry.
PROFILE_TERMINAL = "terminal"
PROFILE_TELEMETRY = "telemetry"
PROFILES = (PROFILE_TERMINAL, PROFILE_TELEMETRY)

_BOTH = (PROFILE_TERMINAL, PROFILE_TELEMETRY)
_TELEMETRY_ONLY = (PROFILE_TELEMETRY,)

# --------------------------------------------------------------------------- #
# URL templates
# --------------------------------------------------------------------------- #
# clawgate serves a server-rendered PAGE PER TASK at `GET /tasks/{id}`
# (`internal/api/server.go` → `handleTaskDetail`), which loads the task by id and
# renders it whatever the board is currently showing. So the deeplink is a PATH.
#
# 🔴 THE OLD `…/tasks#task-<id>` FRAGMENT IS DEAD AS A CONSTRUCTOR — it must not
# come back, and this paragraph replaces one that documented its limits as an
# accepted cost. That comment described a hazard the page CLOSES, and a stale
# note about a closed hazard is how a maintainer later deletes the thing that
# closed it. The limit it recorded: an anchor only scrolls to a card already ON
# the page, so a task that was filtered out, archived, or inside a collapsed
# section left the browser at the top of /tasks. `/tasks/<id>` has no such
# precondition — the server renders the task itself.
#
# ⚠ HONEST SCOPE, restated for the page. This module cannot tell a real id from a
# typo — `#370` in agent output is five characters, not a lookup — so it does not
# claim the URL RESOLVES, only that if the task exists the page shows it. What
# changed is the failure mode, and not uniformly for the better: a bad id now
# gets whatever clawgate answers for an id it does not hold, instead of silently
# landing on the board. Do not describe that as strictly safer.
#
# 🔴 ORDERING: this URL is only correct against a clawgate that serves the route.
# Deploy the clawgate carrying `GET /tasks/{id}` BEFORE anything here reaches a
# machine, or every mention click 404s. Old `#task-N` links already out in the
# world (ClickUp comments, activity.events, docs) are clawgate's problem to
# redirect, not this module's — nothing here should mint one either way.
CLAWGATE_TASKS_URL = "https://clawgate.zacx.dev/tasks"
CLICKUP_TASK_URL = "https://app.clickup.com/t/{id}"
# `/issues/<n>` is deliberate and not a bug: GitHub redirects /issues/<n> to
# /pull/<n> when <n> is a pull request, so one template covers both and we never
# have to know which it is.
GITHUB_ISSUE_URL = "https://github.com/{repo}/issues/{id}"

PLATFORM_CLAWGATE = "clawgate"
PLATFORM_GITHUB = "github"
PLATFORM_CLICKUP = "clickup"
# The `platform` a SPAN carries when more than one platform could own it. Never
# the platform of a candidate — a candidate always names a real platform.
PLATFORM_AMBIGUOUS = "ambiguous"

# How much surrounding text a span carries for telemetry, total, centred on the
# match. Bounded so a mention inside a 50 KB tool dump cannot ship 50 KB.
CONTEXT_CHARS = 100

# --------------------------------------------------------------------------- #
# Patterns
# --------------------------------------------------------------------------- #
# A GitHub owner (user or org) is ASCII alphanumeric plus hyphens ONLY — no dots,
# no underscores, no slashes. That restriction is load-bearing, not pedantry: it
# is the single rule that stops `example.com/page#123` from parsing as
# owner=`example.com`, repo=`page`, issue=123.
_OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"

# A repo name here is DELIBERATELY NARROWER than GitHub allows: no dots. GitHub
# permits `foo.js`, but allowing a dot makes every `file.md#123`,
# `index.html#12` and `README.md#3` a false GitHub mention, and those appear in
# agent output constantly while dotted repo names do not appear at all in this
# operator's world (devrc, talos-infra, civitai, homelab-talos, clawgate, …).
# Cost, stated rather than hidden: a repo whose name contains a dot is not
# detected in the short `repo#N` form. It is still detected as a plain URL by
# Alacritty's URL hint, which this change re-declares untouched.
_REPO = r"[A-Za-z0-9](?:[A-Za-z0-9_-]{0,99})"

# 1-5 digits, and the count is the colour-literal guard. `#282828` and `#000000`
# are 6 digits, so the trailing lookahead rejects every backtrack: `#28282` is
# followed by `8`, `#2828` by `2`, and so on down. A 6-digit hex colour can
# therefore never match. (`#ff00ff` never even starts — `f` is not a digit.)
_NUM = r"\d{1,5}"

# Nothing alphanumeric, and no digit, may follow the number. This is what makes
# the digit bound above a real guard rather than a prefix match.
_NUM_END = r"(?![0-9A-Za-z_-])"

# What may NOT sit immediately before a bare `#`:
#   [0-9A-Za-z_]  `repo#3` — that is the GitHub form, matched by GITHUB_RE, and
#                 letting both patterns claim it would double-count the span.
#   /             `…/page#123`, `…/#12` — a URL fragment. Alacritty's URL hint
#                 already owns whole URLs.
#   &             `&#8212;` — an HTML numeric entity.
#   #             `##3` — a markdown heading that happens to be followed by a
#                 digit, and `#` runs generally.
#   .             `index.html#12`, `v1.2#3`.
#   -             `foo-#3`.
_BARE_BEFORE = r"(?<![0-9A-Za-z_/&#.-])"

# What may NOT sit immediately before an `owner/repo#N` or `repo#N`. Same set
# plus `/`, which is what keeps the pattern out of the middle of a path: in
# `https://github.com/owner/repo#1` the `owner` is preceded by `/`, so the match
# never starts there.
_REF_BEFORE = r"(?<![0-9A-Za-z_/&#.-])"

GITHUB_RE = re.compile(
    _REF_BEFORE
    + rf"(?:(?P<owner>{_OWNER})/)?"
    + rf"(?P<repo>{_REPO})"
    + rf"#(?P<num>{_NUM})"
    + _NUM_END
)

BARE_RE = re.compile(_BARE_BEFORE + rf"#(?P<num>{_NUM})" + _NUM_END)

# ClickUp's team-prefixed task id: `868` + exactly six lowercase alphanumerics.
# Case-SENSITIVE on purpose — ClickUp renders these lowercase, and accepting
# uppercase would start matching abbreviated git shas and hex blobs.
#
# 🔴 THE `DEV-123` CUSTOM-PREFIX FORM IS DELIBERATELY NOT SUPPORTED. Three
# reasons, in order of weight: (1) an `ABC-123` shape is indistinguishable from a
# Jira key, a branch name (`DEV-123-fix-the-thing`), a version string and an
# ordinary hyphenated token, so it cannot be anchored the way `868…` can;
# (2) nothing in this repo or in the operator's ClickUp workspace has been
# MEASURED to use such a prefix — implementing it would be building for a
# requirement nobody has confirmed exists; (3) the whole design premise here is
# that a false positive costs more than a miss. If a custom prefix turns out to
# be in real use, add it as an explicit enumerated allowlist of prefixes, never
# as `[A-Z]{2,5}-\d+`.
CLICKUP_RE = re.compile(r"(?<![0-9A-Za-z_/#-])(?P<id>868[a-z0-9]{6})(?![0-9A-Za-z_-])")

# --------------------------------------------------------------------------- #
# Telemetry-only patterns (profile="telemetry")
# --------------------------------------------------------------------------- #
# In a URL the number is delimited by `/` on the left, so the colour-literal
# hazard that forces `_NUM` down to 1-5 digits does not exist here. A repo name
# may also carry dots here for the same reason: `owner/repo` is delimited by
# slashes, so `foo.js` cannot be confused with `index.html#12`.
_URL_NUM = r"\d{1,9}"
_URL_REPO = r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})"

# `github.com/owner/repo/pull/12` — with or without a scheme, because agent
# output writes it both ways. Trailing path segments are fine: `/pull/12/files`
# still names PR 12, so `_NUM_END` deliberately permits a following `/`.
#
# 🔴 THIS PATTERN IS ALSO AN ATTRIBUTION SOURCE, not only a mention: it is the
# one shape that carries a full `owner/repo` with no lookup at all.
GITHUB_URL_RE = re.compile(
    r"(?<![0-9A-Za-z_.-])(?:https?://)?github\.com/"
    rf"(?P<owner>{_OWNER})/(?P<repo>{_URL_REPO})/(?:pull|issues)/"
    rf"(?P<num>{_URL_NUM})" + _NUM_END
)

# `/audit-pr 1291` and `audit-pr 1291` — the devrc skill, always invoked with a
# PR number. `[ \t]+`, never `\s+`: a number on the NEXT line is not an argument.
AUDIT_PR_RE = re.compile(
    r"(?<![0-9A-Za-z_/&#.-])/?audit-pr[ \t]+(?P<num>" + _NUM + r")" + _NUM_END)

# `gh pr view 1291`, `gh issue close 42`. 🔴 THE SUBCOMMAND LIST IS AN EXPLICIT
# ENUMERATION, never `\w+` — that is the same rule the `DEV-123` note above
# states, applied to a second wordy form. `gh pr create` and `gh pr list` are
# absent on purpose: neither takes a number.
#
# ⚠ EXACTLY ONE SPACE between `gh` and `pr`/`issue`, and the cost is stated
# rather than hidden: `gh<TAB>pr view 3` and `gh  pr view 3` are NOT detected.
# That is what lets the ledger declare the honest literals `gh pr` / `gh issue`
# as pre-filter hints — a `[ \t]+` here would make the only honest hint `gh`,
# which occurs inside `through`, `right` and `high` and would defeat the filter
# entirely. A widening that buys nothing and costs 81% of a short-circuit is not
# a widening.
GH_CLI_SUBCOMMANDS = (
    "view", "diff", "checkout", "merge", "close", "reopen", "edit",
    "comment", "review", "ready", "lock", "unlock", "status",
)
GH_CLI_RE = re.compile(
    r"(?<![0-9A-Za-z_/&#.-])gh (?P<kind>pr|issue)[ \t]+"
    rf"(?:{'|'.join(GH_CLI_SUBCOMMANDS)})[ \t]+(?P<num>{_NUM})" + _NUM_END)

# 🔴 `task N` IS DETECTED ONLY BEHIND THE LITERAL `clawgate`. A bare `task 5`
# occurred 179 times in one measured 24h window and is overwhelmingly prose
# ("the task 5 lines down", "task 3 of 4"). The literal is the anchor; without
# it there is no pattern here worth having.
#
# NO `#` IS ADMITTED between `task` and the digits: `clawgate task #370` is
# already a bare `#370` span, and letting this pattern claim an overlapping,
# longer span would emit the same reference twice.
CLAWGATE_TASK_RE = re.compile(
    r"(?<![0-9A-Za-z_/&#.-])[Cc]lawgate[ \t]+[Tt]ask[ \t]+"
    rf"(?P<num>{_NUM})" + _NUM_END)

# The LEGACY clawgate anchor, `#task-370`. See the URL-templates block: nothing
# here MINTS one, but old links are out in the world (ClickUp comments, docs,
# activity.events) and reading one is not the same as writing one.
#
# 🔴 ITS LEFT GUARD IS DELIBERATELY LOOSER THAN `_BARE_BEFORE`, and this is the
# whole reason the pattern is not inert. The form occurs almost exclusively as
# `https://clawgate.zacx.dev/tasks#task-370`, where the character before `#` is a
# LETTER — the standard guard rejects every real occurrence. It is safe to relax
# here and nowhere else because `#` is followed by the literal `task-`: GITHUB_RE
# needs digits after the `#`, BARE_RE needs digits after the `#`, so no other
# pattern can claim this span and no colour literal can spell it. Only `&` and
# `#` are excluded (an HTML entity, and a `##` run).
TASK_ANCHOR_RE = re.compile(rf"(?<![&#])#task-(?P<num>{_NUM})" + _NUM_END)

# --------------------------------------------------------------------------- #
# Attribution sources (they resolve a repo; they never emit a mention of their own)
# --------------------------------------------------------------------------- #
# The connector words allowed between a repo token and the ref. ENUMERATED for
# the same reason `GH_CLI_SUBCOMMANDS` is: a generic `\w+` would let ANY word
# stand between them, so `see the devrc thing about #370` would attribute to
# `devrc`, which the operator never wrote.
ATTR_CONNECTORS = ("PR", "PRs", "pr", "prs", "issue", "issues",
                   "Issue", "Issues", "pull", "Pull")

# `devrc PR #1291`, `devrc #1291`. Anchored at the END of the text BEFORE the
# ref, so it can only ever match a token that is genuinely adjacent to it.
# `\Z`, not `$`: `$` matches before a trailing newline, which would let a repo
# token on the PREVIOUS line attribute a ref on this one.
REPO_BEFORE_RE = re.compile(
    r"(?<![0-9A-Za-z_/&#.-])"
    rf"(?P<repo>{_REPO})"
    rf"(?:[ \t]+(?:{'|'.join(ATTR_CONNECTORS)}))?"
    r"[ \t]+\Z"
)

# `--repo owner/repo` / `-R owner/repo`, as written on a `gh` command line.
REPO_FLAG_RE = re.compile(
    r"(?<![0-9A-Za-z_-])(?:--repo[ \t=]|-R[ \t])[ \t]*"
    rf"(?P<owner>{_OWNER})/(?P<repo>{_URL_REPO})(?![0-9A-Za-z_./-])")


# --------------------------------------------------------------------------- #
# 🔴 THE PATTERN LEDGER — one row per compiled pattern in this module
# --------------------------------------------------------------------------- #
class _Pat(NamedTuple):
    """`profiles` the profiles the pattern is consulted in.
    `role`     "detect" (emits a mention) or "attribute" (resolves a repo only).
    `hints`    literals, at least one of which appears in ANY text this pattern
               can match. This is what `mention_hints()` derives the tailer's
               cheap pre-filter from — see the note on that function.
    `sample`   a text the pattern MUST match, used by the suite to prove the
               hints above are honest rather than decorative."""
    profiles: tuple[str, ...]
    role: str
    hints: tuple[str, ...]
    sample: str


PATTERN_LEDGER: dict[str, _Pat] = {
    "CLICKUP_RE": _Pat(_BOTH, "detect", ("868",), "please look at 868abc123"),
    "GITHUB_RE": _Pat(_BOTH, "detect", ("#",), "see devrc#591"),
    "BARE_RE": _Pat(_BOTH, "detect", ("#",), "fixed in #370"),
    "GITHUB_URL_RE": _Pat(_TELEMETRY_ONLY, "detect", ("github.com/",),
                          "https://github.com/gardenersguild/trowelcast/pull/7"),
    "AUDIT_PR_RE": _Pat(_TELEMETRY_ONLY, "detect", ("audit-pr",), "/audit-pr 1291"),
    "GH_CLI_RE": _Pat(_TELEMETRY_ONLY, "detect", ("gh pr", "gh issue"),
                      "gh pr view 1291"),
    "CLAWGATE_TASK_RE": _Pat(_TELEMETRY_ONLY, "detect", ("lawgate",),
                             "clawgate task 370"),
    "TASK_ANCHOR_RE": _Pat(_TELEMETRY_ONLY, "detect", ("#task-",),
                           "https://clawgate.zacx.dev/tasks#task-370"),
    # Attribution patterns contribute NO hints: neither can produce a mention on
    # its own, so a text containing only one of them has nothing to emit and
    # skipping it costs nothing.
    "REPO_BEFORE_RE": _Pat(_TELEMETRY_ONLY, "attribute", (), "devrc PR "),
    "REPO_FLAG_RE": _Pat(_TELEMETRY_ONLY, "attribute", (),
                         "--repo gardenersguild/trowelcast"),
}


# 🔴 EVERY PUBLIC COMPILED PATTERN IN THIS MODULE IS IN EXACTLY ONE OF TWO
# PLACES: the ledger above, or this set. A pattern in neither fails the suite.
# This one is not a SCAN pattern at all — it validates a mapping VALUE the caller
# already holds, so it has no profile and emits no mention. Naming it explicitly
# is what stops this from being a hole: an escape hatch nobody has to declare is
# how the next pattern gets added to no ledger at all and is silently never run.
NON_SCAN_PATTERNS = frozenset({"OWNER_REPO_VALUE_RE"})


def patterns_in(profile: str = PROFILE_TERMINAL) -> frozenset[str]:
    """The ledger names consulted in `profile`. An unknown profile is TERMINAL —
    the narrow one — because a typo must never silently widen the click surface.
    """
    if profile not in PROFILES:
        profile = PROFILE_TERMINAL
    return frozenset(n for n, p in PATTERN_LEDGER.items() if profile in p.profiles)


def mention_hints(profile: str = PROFILE_TERMINAL) -> tuple[str, ...]:
    """Literals for a caller's cheap pre-filter: a text containing NONE of them
    cannot match any detecting pattern enabled in `profile`.

    🔴 THIS EXISTS BECAUSE THE PRE-FILTER IS WHERE A WIDENING GOES TO DIE.
    `session-tailer.py` short-circuits on these literals BEFORE the regex pass.
    Re-measured 2026-09-04 over the preceding 24h (10,118 assistant text blocks,
    312 transcripts): the OLD two literals skipped 8,169 of them = 81%; the
    telemetry hint set skips 7,990 = 79%, so the short-circuit survives the
    widening. ⚠ THE PERCENTAGE IS A PROPERTY OF THE WINDOW, NOT OF THE CODE — an
    earlier window read 82%/80% on 6,052 blocks. Re-measure rather than quote
    this; what is stable is that the filter still skips ~4 blocks in 5. Every
    telemetry-only shape above — `audit-pr 1291`, `gh pr view 1291`,
    `clawgate task 370` — contains neither `#` nor `868`, so adding the regex
    alone would have shipped a completely dead feature that still passed every
    unit test calling `scan_mentions()` directly. Deriving the list from the same
    ledger the patterns are declared in is what makes that impossible: ONE rule,
    ONE place.
    """
    hints: set[str] = set()
    for name in patterns_in(profile):
        entry = PATTERN_LEDGER[name]
        if entry.role == "detect":
            hints.update(entry.hints)
    return tuple(sorted(hints))


# Documented residual false positives — the shapes that DO still match, listed so
# a future widening starts from what is already known rather than rediscovering
# it. Pinned as a set by the test suite so this comment cannot silently rot.
_KNOWN_FALSE_POSITIVES = (
    # A three-digit numeric CSS colour, e.g. `#123`, is character-for-character a
    # plausible issue/task number and there is no rule that separates them. It
    # lands as AMBIGUOUS, so the click shows a picker rather than opening
    # anything wrong, and the telemetry row is one stray row. Accepted.
    "#123",
)

# The residuals the WIDER profile adds, kept separate because they are not the
# click surface's problem — nothing here is ever underlined.
_KNOWN_FALSE_POSITIVES_TELEMETRY = (
    # 🔴 AN INSTRUCTIONAL EXAMPLE IS CHARACTER-FOR-CHARACTER A REAL INVOCATION.
    # A runbook, a skill body or a code fence that TEACHES the command spells it
    # exactly as a session that RAN it, and no rule separates the two. Accepted:
    # the cost is a stray row in a private table, and the alternative — dropping
    # the shape — loses the 370 real occurrences measured alongside them.
    "gh pr view 12",
    "/audit-pr 12",
    # A markdown anchor into a document that DOCUMENTS the legacy form. The
    # relaxed left guard on TASK_ANCHOR_RE is what makes the real
    # `…/tasks#task-370` case work at all, and it cannot tell the two apart.
    "[the old anchor](#task-1)",
)


# --------------------------------------------------------------------------- #
# Candidate construction
# --------------------------------------------------------------------------- #
def _candidate(platform: str, ident: str, url: str) -> dict:
    return {"platform": platform, "id": ident, "url": url}


def _github_url(full_repo: str | None, num: str) -> str:
    """`https://github.com/<owner>/<repo>/issues/<n>`, or "" when the owner is
    not known. NEVER a guessed owner — see the module docstring."""
    if not full_repo or "/" not in full_repo:
        return ""
    return GITHUB_ISSUE_URL.format(repo=full_repo, id=num)


def clawgate_url(task_id: str) -> str:
    """The clawgate task's browser URL: `https://clawgate.zacx.dev/tasks/<id>`,
    the server-rendered details page for that one task. NO fragment — see the
    URL-templates block above for why the old `#task-<id>` anchor is gone."""
    return f"{CLAWGATE_TASKS_URL}/{task_id}"


def _resolve_repo(owner: str | None, repo: str, repos: dict | None) -> str:
    """`owner/repo` when it can be known WITHOUT guessing, else "".

    `repos` is a caller-MEASURED mapping of bare repo name -> "owner/repo" (the
    resolver builds it from the git remotes of real local checkouts). An entry
    that is absent yields "", never a default org.
    """
    if owner:
        return f"{owner}/{repo}"
    if repos:
        mapped = repos.get(repo)
        if mapped and "/" in mapped:
            return mapped
    return ""


# --------------------------------------------------------------------------- #
# Repo-map hygiene — ONE definition, shared by every caller that loads one
# --------------------------------------------------------------------------- #
# What a mapping VALUE must look like: exactly two segments, nothing else.
# 🔴 `\Z`, not `$` — `$` matches BEFORE a final newline, so `acme/widget\n`
# passed and built `github.com/acme/widget\n/issues/12`.
OWNER_REPO_VALUE_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def clean_repo_map(raw) -> dict:
    """{name: "owner/repo"} keeping only entries whose value is EXACTLY
    `owner/repo`. Anything else — a wrong type, a trailing slash, an empty middle
    segment, an embedded newline — is dropped.

    🔴 ONE RULE, ONE PLACE. Both `scripts/mention-open.py` and
    `scripts/collector/claude/session-tailer.py` load the operator's generated
    mapping, and each used to need this filter. Counting non-empty segments —
    the obvious implementation — accepted `acme/widget/`, `acme//widget`, a
    trailing space and an embedded newline, each of which builds a URL that 404s
    while looking authoritative. A predicate open-coded at two sites is wrong at
    one of them; this is the consolidation.
    """
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items()
            if isinstance(k, str) and isinstance(v, str)
            and OWNER_REPO_VALUE_RE.match(v)}


# --------------------------------------------------------------------------- #
# Attribution (telemetry profile only)
# --------------------------------------------------------------------------- #
# The ladder, in priority order. Exposed so a consumer can record HOW a mention
# was attributed rather than only THAT it was.
SOURCE_EXPLICIT = "explicit"
# 🔴 NOT A SYNONYM FOR `explicit`. The text named the repo but NOT the owner, so
# the owner is the caller's mapping speaking, not the operator. See the
# ATTRIBUTION block in the module docstring for why the two must not collapse.
SOURCE_MAPPED = "mapped"
SOURCE_ADJACENT = "adjacent"
SOURCE_URL = "url"
SOURCE_FLAG = "flag"
SOURCE_DEFAULT = "default"
SOURCE_NONE = ""


def _sole(values: list[str]) -> str:
    """The one distinct value in `values`, or "" when there are none or several.

    🔴 SEVERAL IS AN ABSENCE OF AN ANSWER, NOT A TIE TO BREAK. A block naming two
    different repositories does not tell you which one a bare `#N` belongs to,
    and picking the nearest — or the first — is a guess wearing a heuristic's
    clothes. This is the same rule `mention-open.py` applies when a name has two
    owners, at a different point in the pipeline.
    """
    distinct = set(values)
    return distinct.pop() if len(distinct) == 1 else ""


def _adjacent_repo(text: str, start: int, repos: dict | None) -> str:
    """`owner/repo` for a repo token written immediately before position
    `start` — `devrc PR #1291` — or "".

    🔴 IT ANSWERS ONLY THROUGH `repos`. The token is whatever word happens to
    precede the ref, so `fixed in #370` offers `in` and `PR #12` offers `PR`; the
    caller-measured mapping is the entire guard, and a token it does not hold
    yields "". That is why this must never fall back to a default org: without
    the mapping there is no evidence here at all, only a word.

    ⚠ THE GUARD IS `_resolve_repo`, NOT THE EARLY RETURN BELOW. Deleting that
    return changes no behaviour — `_resolve_repo(None, …, None)` already answers
    "" — and a mutation sweep scored it SURVIVED for exactly that reason. It is
    an optimisation (it skips a regex search on the common no-mapping path) and
    is labelled as one so nobody reads it as the thing that stops a guess.
    """
    if not repos:
        return ""
    m = REPO_BEFORE_RE.search(text[:start])
    if not m:
        return ""
    return _resolve_repo(None, m.group("repo"), repos)


def _sole_repo_named_by(pattern, text: str) -> str:
    """The SOLE distinct `owner/repo` that `pattern` finds in `text`, or "".

    Used for both block-level routes — the `github.com/owner/repo/…` URL and the
    `--repo owner/repo` flag. They stay SEPARATE calls rather than one merged
    set because the ladder ranks a URL above a flag: a URL is a reference the
    agent actually wrote out, a flag is an argument to a command that may have
    been about something else entirely.
    """
    return _sole([f"{m.group('owner')}/{m.group('repo')}"
                  for m in pattern.finditer(text)])


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def _github_candidate(num: str, raw: str, start: int, end: int,
                      repo: str, source: str, ambiguous: bool) -> dict:
    # 🔴 `source if repo else SOURCE_NONE` IS A GUARD, NOT A TIDY-UP. The
    # `repo#N` path passes `SOURCE_MAPPED` with `repo == ""` whenever the
    # caller's mapping does not hold that name, and without the ternary the
    # candidate reads `repo_source="mapped"` beside an empty `repo` — a claim
    # that a resolution happened, next to the evidence that it did not. The
    # `scan_mentions` docstring's "`""` exactly when `repo` is `""`" contract
    # lives here and nowhere else.
    #
    # ⚠ HONEST SCOPE: it is observable on a CANDIDATE, not on a SPAN.
    # `scan_mention_spans` takes its `repo_source` from a candidate that HAS a
    # repo, so it reports "" for this case with or without the ternary — which
    # is why a span-level test of it SURVIVED a mutation sweep. Both of this
    # repo's consumers read spans, so the guard defends the public
    # `scan_mentions` contract rather than a live row today.
    return {
        "platform": PLATFORM_GITHUB, "id": num, "raw": raw,
        "url": _github_url(repo, num), "repo": repo or "",
        "repo_source": source if repo else SOURCE_NONE,
        "start": start, "end": end, "ambiguous": ambiguous,
    }


def scan_mentions(text, *, repos: dict | None = None,
                  default_repo: str | None = None,
                  profile: str = PROFILE_TERMINAL) -> list[dict]:
    """Every mention CANDIDATE in `text`, ordered by position then platform.

    Each dict is `{platform, id, raw, url, repo, repo_source, start, end,
    ambiguous}`:

        platform    "clawgate" | "github" | "clickup" — always a real platform.
        id          the reference id ("370", "868abc123").
        raw         the exact matched text ("#370", "civitai/talos-infra#1065").
        url         the resolved URL, or "" when it cannot be built without
                    guessing (a GitHub reference whose repo is unknown).
        repo        the attributed "owner/repo", or "". GitHub candidates only.
        repo_source how `repo` was resolved — see the ATTRIBUTION block in the
                    module docstring. "" exactly when `repo` is "".
        start/end   the match span in `text`.
        ambiguous   True when this candidate SHARES its span with another
                    platform's candidate, i.e. a bare `#N`.

    A bare `#N` yields TWO candidates over ONE span (clawgate + github). Use
    `scan_mention_spans` when you want one record per underlined span.

    `repos`        {bare repo name: "owner/repo"} — measured by the caller.
    `default_repo` "owner/repo" for the surrounding context, used only to give a
                   bare `#N` a GitHub URL. Also measured by the caller.
    `profile`      "terminal" (default, narrow) or "telemetry" (wider). See the
                   module docstring; an unknown value is treated as "terminal".
    """
    if not isinstance(text, str) or not text:
        return []

    on = patterns_in(profile)
    out: list[dict] = []

    # Block-level attribution context, computed ONCE per text rather than per
    # match — it is a property of the block, not of the reference.
    #
    # 🔴 THE THREE `in on` GUARDS HERE AND IN `_ladder` ARE THE PROFILE SPLIT
    # ITSELF, not a micro-optimisation. Each one is what keeps a TELEMETRY-only
    # attribution route out of the terminal profile, and deleting any of them
    # silently widens the click surface — the exact drift the "one pattern set"
    # invariant used to prevent. All three are pinned behaviourally by
    # `test_mention_scan.py::test_no_TELEMETRY_only_attribution_route_answers_in_
    # the_terminal_profile`, each with its own case, so a deletion goes red.
    url_repo = _sole_repo_named_by(GITHUB_URL_RE, text) if "GITHUB_URL_RE" in on else ""
    flag_repo = _sole_repo_named_by(REPO_FLAG_RE, text) if "REPO_FLAG_RE" in on else ""

    def _ladder(start: int) -> tuple[str, str]:
        """(repo, repo_source) for a reference at `start` that names no repo of
        its own. The priority order is the module docstring's, in one place."""
        adjacent = _adjacent_repo(text, start, repos) if "REPO_BEFORE_RE" in on else ""
        for repo, source in ((adjacent, SOURCE_ADJACENT),
                             (url_repo, SOURCE_URL),
                             (flag_repo, SOURCE_FLAG),
                             (default_repo or "", SOURCE_DEFAULT)):
            if repo:
                return repo, source
        return "", SOURCE_NONE

    if "CLICKUP_RE" in on:
        for m in CLICKUP_RE.finditer(text):
            ident = m.group("id")
            out.append({
                "platform": PLATFORM_CLICKUP, "id": ident, "raw": m.group(0),
                "url": CLICKUP_TASK_URL.format(id=ident),
                "repo": "", "repo_source": SOURCE_NONE,
                "start": m.start(), "end": m.end(), "ambiguous": False,
            })

    if "GITHUB_RE" in on:
        for m in GITHUB_RE.finditer(text):
            # 🔴 A ref that NAMES a repo is never re-attributed from the block.
            # An unknown `repo#N` stays unresolved rather than borrowing the
            # block's other repository — substituting a different repo for the
            # one the operator wrote is worse than admitting it is unknown.
            owner = m.group("owner")
            full = _resolve_repo(owner, m.group("repo"), repos)
            # 🔴 THE SOURCE IS DECIDED BY WHETHER THE TEXT WROTE AN OWNER, never
            # by whether an owner was FOUND. `_resolve_repo` falls through to the
            # caller's mapping when none was written, and labelling that
            # `explicit` reports the operator as the author of an owner our own
            # mapping supplied.
            source = SOURCE_EXPLICIT if owner else SOURCE_MAPPED
            out.append(_github_candidate(m.group("num"), m.group(0), m.start(),
                                         m.end(), full, source, False))

    if "BARE_RE" in on:
        for m in BARE_RE.finditer(text):
            num = m.group("num")
            repo, source = _ladder(m.start())
            # ONE span, TWO candidates. They are emitted adjacently and both
            # carry ambiguous=True, so no consumer can treat either as settled.
            # 🔴 ATTRIBUTION DOES NOT SETTLE THE PLATFORM. Knowing which repo a
            # `#N` belongs to says nothing about whether it is a GitHub issue or
            # a clawgate task, so the clawgate candidate survives attribution.
            out.append({
                "platform": PLATFORM_CLAWGATE, "id": num, "raw": m.group(0),
                "url": clawgate_url(num), "repo": "", "repo_source": SOURCE_NONE,
                "start": m.start(), "end": m.end(), "ambiguous": True,
            })
            out.append(_github_candidate(num, m.group(0), m.start(), m.end(),
                                         repo, source, True))

    if "GITHUB_URL_RE" in on:
        for m in GITHUB_URL_RE.finditer(text):
            full = f"{m.group('owner')}/{m.group('repo')}"
            out.append(_github_candidate(m.group("num"), m.group(0), m.start(),
                                         m.end(), full, SOURCE_URL, False))

    for name, pattern in (("AUDIT_PR_RE", AUDIT_PR_RE), ("GH_CLI_RE", GH_CLI_RE)):
        if name not in on:
            continue
        for m in pattern.finditer(text):
            repo, source = _ladder(m.start())
            out.append(_github_candidate(m.group("num"), m.group(0), m.start(),
                                         m.end(), repo, source, False))

    for name, pattern in (("CLAWGATE_TASK_RE", CLAWGATE_TASK_RE),
                          ("TASK_ANCHOR_RE", TASK_ANCHOR_RE)):
        if name not in on:
            continue
        for m in pattern.finditer(text):
            num = m.group("num")
            out.append({
                "platform": PLATFORM_CLAWGATE, "id": num, "raw": m.group(0),
                "url": clawgate_url(num), "repo": "", "repo_source": SOURCE_NONE,
                "start": m.start(), "end": m.end(), "ambiguous": False,
            })

    out.sort(key=lambda c: (c["start"], c["platform"]))
    return out


def scan_mention_spans(text, *, repos: dict | None = None,
                       default_repo: str | None = None,
                       profile: str = PROFILE_TERMINAL) -> list[dict]:
    """One record per underlined SPAN, built from `scan_mentions`.

    `{raw, start, end, platform, id, url, repo, repo_source, ambiguous,
    candidates, context}` where `platform` is the single owning platform, or
    "ambiguous" when the span has more than one candidate. `url` is the single
    candidate's URL, or "" when ambiguous — an ambiguous span has no answer yet,
    and inventing one is the failure mode this whole module is anchored against.

    `repo`/`repo_source` are the span's ATTRIBUTION, taken from whichever
    candidate carries one. They are reported even on an ambiguous span: which
    repository a `#N` belongs to and which platform owns it are two different
    questions, and answering the first is not a claim about the second.

    This is the shape the telemetry emitter uses: one row per mention, never one
    row per guess.
    """
    by_span: dict[tuple[int, int], list[dict]] = {}
    for cand in scan_mentions(text, repos=repos, default_repo=default_repo,
                              profile=profile):
        by_span.setdefault((cand["start"], cand["end"]), []).append(cand)

    spans: list[dict] = []
    for (start, end), cands in sorted(by_span.items()):
        ambiguous = len(cands) > 1
        attributed = next((c for c in cands if c.get("repo")), None)
        spans.append({
            "raw": cands[0]["raw"],
            "start": start,
            "end": end,
            "platform": PLATFORM_AMBIGUOUS if ambiguous else cands[0]["platform"],
            "id": cands[0]["id"],
            "url": "" if ambiguous else cands[0]["url"],
            "repo": attributed["repo"] if attributed else "",
            "repo_source": attributed["repo_source"] if attributed else SOURCE_NONE,
            "ambiguous": ambiguous,
            "candidates": [_candidate(c["platform"], c["id"], c["url"]) for c in cands],
            "context": context_for(text, start, end),
        })
    return spans


def context_for(text: str, start: int, end: int, width: int = CONTEXT_CHARS) -> str:
    """Up to `width` characters of `text` centred on [start, end), whitespace
    collapsed to single spaces so one row stays one line."""
    pad = max(0, (width - (end - start)) // 2)
    chunk = text[max(0, start - pad):end + pad]
    return " ".join(chunk.split())
