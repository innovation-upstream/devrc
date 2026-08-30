#!/usr/bin/env python3
"""mention_scan — find cross-platform reference MENTIONS in agent output.

PURE. No I/O, no subprocess, no network, stdlib only. Two consumers, one set of
regexes:

  * `scripts/collector/claude/session-tailer.py` — emits one telemetry event per
    detected mention into activity.events (source=mentions).
  * `scripts/mention-open.py` — the Alacritty hint handler. Alacritty hands it
    the matched text; it re-scans that text with the SAME rules and opens (or
    offers) the resolved URL.

Both call this module, so what the terminal underlines and what the telemetry
records can never drift apart. That is the whole reason this is a module and not
two regexes.

WHAT IS DETECTED
----------------
  clickup   868abc123                 -> https://app.clickup.com/t/868abc123
  github    owner/repo#12             -> https://github.com/owner/repo/issues/12
  github    repo#12                   -> owner resolved by the CALLER (see below)
  ambiguous #12                       -> clawgate task 12 OR a GitHub issue 12

A bare `#N` is genuinely ambiguous and this module does NOT pick a winner. It
returns a candidate per platform and lets the click-time resolver disambiguate
with facts it can actually measure (the pane's repo). See `scan_mention_spans`.

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

# --------------------------------------------------------------------------- #
# URL templates
# --------------------------------------------------------------------------- #
# clawgate serves a real, server-rendered browser UI at GET /tasks (see
# internal/api/server.go, `s.handleIndex`), and every task card carries a stable
# DOM id `task-<id>` — `internal/ui/notes.go`, `ID("task-"+ids)`, pinned by
# `internal/ui/notes_test.go` as "stable card id for outerHTML swaps". So the
# fragment is a real anchor for any task the list renders.
#
# ⚠ HONEST SCOPE: an anchor only scrolls to a card that is ON the page. A task
# filtered out of the list (archived, or in a collapsed section the server did
# not render) leaves the browser at the top of /tasks. That is a graceful
# degradation — the page you wanted, not the wrong page — and it is why the
# fragment is appended rather than relied upon.
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
    """The clawgate task's browser URL: the real /tasks page, anchored on the
    task card's own stable DOM id."""
    return f"{CLAWGATE_TASKS_URL}#task-{task_id}"


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
# Public API
# --------------------------------------------------------------------------- #
def scan_mentions(text, *, repos: dict | None = None,
                  default_repo: str | None = None) -> list[dict]:
    """Every mention CANDIDATE in `text`, ordered by position then platform.

    Each dict is `{platform, id, raw, url, start, end, ambiguous}`:

        platform   "clawgate" | "github" | "clickup" — always a real platform.
        id         the reference id ("370", "868abc123").
        raw        the exact matched text ("#370", "civitai/talos-infra#1065").
        url        the resolved URL, or "" when it cannot be built without
                   guessing (a GitHub reference whose repo is unknown).
        start/end  the match span in `text`.
        ambiguous  True when this candidate SHARES its span with another
                   platform's candidate, i.e. a bare `#N`.

    A bare `#N` yields TWO candidates over ONE span (clawgate + github). Use
    `scan_mention_spans` when you want one record per underlined span.

    `repos`        {bare repo name: "owner/repo"} — measured by the caller.
    `default_repo` "owner/repo" for the surrounding context, used only to give a
                   bare `#N` a GitHub URL. Also measured by the caller.
    """
    if not isinstance(text, str) or not text:
        return []

    out: list[dict] = []

    for m in CLICKUP_RE.finditer(text):
        ident = m.group("id")
        out.append({
            "platform": PLATFORM_CLICKUP, "id": ident, "raw": m.group(0),
            "url": CLICKUP_TASK_URL.format(id=ident),
            "start": m.start(), "end": m.end(), "ambiguous": False,
        })

    for m in GITHUB_RE.finditer(text):
        num = m.group("num")
        full = _resolve_repo(m.group("owner"), m.group("repo"), repos)
        out.append({
            "platform": PLATFORM_GITHUB, "id": num, "raw": m.group(0),
            "url": _github_url(full, num), "repo": full,
            "start": m.start(), "end": m.end(), "ambiguous": False,
        })

    for m in BARE_RE.finditer(text):
        num = m.group("num")
        span = (m.start(), m.end())
        # ONE span, TWO candidates. They are emitted adjacently and both carry
        # ambiguous=True, so no consumer can treat either as settled.
        out.append({
            "platform": PLATFORM_CLAWGATE, "id": num, "raw": m.group(0),
            "url": clawgate_url(num),
            "start": span[0], "end": span[1], "ambiguous": True,
        })
        out.append({
            "platform": PLATFORM_GITHUB, "id": num, "raw": m.group(0),
            "url": _github_url(default_repo, num), "repo": default_repo or "",
            "start": span[0], "end": span[1], "ambiguous": True,
        })

    out.sort(key=lambda c: (c["start"], c["platform"]))
    return out


def scan_mention_spans(text, *, repos: dict | None = None,
                       default_repo: str | None = None) -> list[dict]:
    """One record per underlined SPAN, built from `scan_mentions`.

    `{raw, start, end, platform, id, url, ambiguous, candidates, context}` where
    `platform` is the single owning platform, or "ambiguous" when the span has
    more than one candidate. `url` is the single candidate's URL, or "" when
    ambiguous — an ambiguous span has no answer yet, and inventing one is the
    failure mode this whole module is anchored against.

    This is the shape the telemetry emitter uses: one row per mention, never one
    row per guess.
    """
    by_span: dict[tuple[int, int], list[dict]] = {}
    for cand in scan_mentions(text, repos=repos, default_repo=default_repo):
        by_span.setdefault((cand["start"], cand["end"]), []).append(cand)

    spans: list[dict] = []
    for (start, end), cands in sorted(by_span.items()):
        ambiguous = len(cands) > 1
        spans.append({
            "raw": cands[0]["raw"],
            "start": start,
            "end": end,
            "platform": PLATFORM_AMBIGUOUS if ambiguous else cands[0]["platform"],
            "id": cands[0]["id"],
            "url": "" if ambiguous else cands[0]["url"],
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
