"""Resolve a handoff ARC — every session that worked one handoff doc.

An *arc* is well-defined because `handoff_doc.py` enforces one doc per effort,
updated in place: the set of sessions that touched `claudedocs/handoff-<topic>.md`
IS the arc. Nothing read it that way before this module.

🔴 THE EDGE ALREADY EXISTED — THIS IS A READER, NOT A CAPTURE SYSTEM. MEASURED
2026-09-18 over the devrc handoff corpus: 327 of 593 doc commits (55%) already
carry a `Claude-Session-Id:` trailer, and 290 of 332 (87%) of the September ones
do, reaching 126 distinct sessions. The first framing of this work was "there is
no session-to-doc link, so capture one"; that was false, and measuring first is
what turned a capture system into a reader plus one gap-closer.

Two independent halves, unioned, because each is blind to what the other sees:

  WRITERS  come from git — `Claude-Session-Id:` trailers on the commits that
           touched the doc. Durable (they are in history forever) and they
           include the ORIGINATING session, which never resumed from the doc it
           created and is therefore invisible to the reader half.
  READERS  come from transcripts — a session whose opening message carries the
           `/handoff` kickoff line naming this doc. Catches a session that is
           working the arc RIGHT NOW and has not committed yet, which the git
           half cannot see.

🔴 NEVER READ THE TRAILER WITH `git log --format='%(trailers:key=…)'`. Git's
trailer parser reads only the message's final block. A GitHub squash turns each
squashed commit into a `*` bullet CARRYING ITS OWN TRAILER INLINE, so the ids end
up mid-message and the parser returns EMPTY for a commit whose trailer is plainly
visible in `%B`. MEASURED on this corpus: the parser reported 197 of 593 (33%)
where a `^Claude-Session-Id:` scan of the body reports 327 (55%) — a 40% relative
undercount, silent, on real commits. `scripts/lib/session_trailer.py` documents
the same trap from the writing side. Everything here reads `%B`.
"""

from __future__ import annotations

import os
import os.path
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import session_trailer  # noqa: E402

__all__ = [
    "TRAILER_KEY",
    "ROLE_ORIGINATED",
    "ROLE_WROTE",
    "ROLE_RESUMED",
    "ROLE_EARLIEST_STAMPED",
    "ROLE_ORDER",
    "ArcCommit",
    "ArcMember",
    "ArcReport",
    "trailer_ids",
    "genesis_names_doc",
    "doc_basename",
    "doc_commits",
    "sessions_docs",
    "writer_members",
    "reader_members",
    "merge_members",
    "coverage_line",
    "resolve_arc",
    "GitUnavailable",
]

TRAILER_KEY = "Claude-Session-Id"

#: 🔴 COLUMN 0 AND WHOLE-LINE, scanned MULTILINE over the entire body — not over
#: the final block, and not `.strip()`ed. Column 0 is what keeps an indented
#: quotation of a trailer (inside a fenced block in a commit message, say) from
#: minting a session id; MULTILINE over the whole body is what catches the squash
#: shape this module's docstring describes. The two requirements pull in opposite
#: directions and both are load-bearing.
_TRAILER_RE = re.compile(rf"^{TRAILER_KEY}:[ \t]*(\S+)[ \t]*$", re.MULTILINE)

#: 🔴 SAFETY, NOT SHAPE — and an earlier revision of this file got that wrong.
#: It filtered reads against a UUID-or-`ses_` regex, which contradicts an explicit
#: 🔴 in `scripts/lib/session_trailer.py`: "DO NOT ASSUME UUID SHAPE … treated as
#: an OPAQUE STRING everywhere: validated for safety, never parsed, normalised,
#: lowercased or shape-checked", citing a measured case where 2 of 41 windows
#: carried a `ses_…` token from another runtime and a shape-assuming join
#: "silently matches nothing and reports a clean 'no live window'".
#:
#: 🔴 AND ITS JUSTIFYING COMMENT WAS FALSE. It claimed symmetry — "the write side
#: already validates" — but `session_trailer.valid_id` validates only what could
#: CORRUPT a commit message, never what an id looks like. The read side was
#: therefore STRICTER than the write side, so the stamping hook could legitimately
#: write an id this module would then refuse. Worse, a refused value made
#: `ArcCommit.stamped` False, so the coverage line reported a commit that DOES
#: carry an id as "carries no session id" — the measured-vs-unmeasured conflation
#: this module exists to refuse, reintroduced by a fix for a different one.
#:
#: The hazard is real but it lives at the RENDER layer: the value becomes
#: `claude --resume <value>` for a human to paste. So validate for safety here
#: and QUOTE at the point of rendering.
#:
#: 🔴 AND THE SAFETY CHECK IS THE WRITER'S OWN FUNCTION, NOT A COPY OF IT. An
#: earlier revision re-spelled it as `_UNSAFE_CHARS = "\r\n\t\x00"` and four
#: separate prose sites then claimed it was "the same predicate the writer uses".
#: It was not, and the divergence ran the dangerous way: `valid_id` rejects EVERY
#: C0 control, this rejected four characters — three of which (`\r`, `\n`, `\t`)
#: `_TRAILER_RE`'s `(\S+)` can never capture anyway. So of the 24 control
#: characters REACHABLE through this parser, the copy checked exactly ONE (NUL)
#: and missed 23 — ⚠ not "all of them", which an earlier draft of this very
#: comment claimed while the commit message beside it got it right. And
#: `\x1b[2J\x1b]0;PWNED\x07…` in any commit body in any of four repos reached the
#: terminal raw. `shlex.quote` does not help: an escape inside quotes still
#: executes when written to a tty. One rule, one place — call it, do not restate
#: it.

#: A handoff doc path as it appears in prose. `archive/` is included because
#: `#1627` renamed 35 docs under it and an arc must not end at a rename.
_DOC_IN_TEXT = re.compile(
    r"claudedocs/(?:archive/)?(handoff-[A-Za-z0-9._-]+\.md)")

ROLE_ORIGINATED = "originated"
ROLE_WROTE = "wrote"
ROLE_RESUMED = "resumed"
#: Used instead of `originated` when the doc has unstamped commits — see
#: `resolve_arc`. The distinction is the difference between a measured fact and
#: the earliest thing the instrument could see.
ROLE_EARLIEST_STAMPED = "earliest-stamped"

#: Most-specific first. A session that both created the doc and later resumed it
#: is reported as `originated`, because that is the fact a reader is hunting.
ROLE_ORDER = (ROLE_ORIGINATED, ROLE_EARLIEST_STAMPED, ROLE_WROTE,
              ROLE_RESUMED)


class GitUnavailable(RuntimeError):
    """git could not answer for this repo/path — NOT 'the doc has no commits'.

    Raised rather than returning an empty tuple on purpose: an empty commit list
    and an unreadable repo produce the same zero, and this module's whole posture
    is that a scoped zero must never be reported as an absence.
    """


@dataclass(frozen=True)
class ArcCommit:
    """One commit that touched the doc, with the ids its BODY carries."""

    sha: str
    date: str          # author date, ISO-8601, as git printed it
    subject: str
    session_ids: tuple[str, ...]

    @property
    def stamped(self) -> bool:
        return bool(self.session_ids)


@dataclass(frozen=True)
class ArcMember:
    """One session in the arc."""

    session_id: str
    role: str
    repo: str = ""
    first_seen: str = ""       # ISO-8601; commit date or transcript first-message
    commits: tuple[str, ...] = ()

    def resume_command(self) -> str:
        """🔴 QUOTED, because this string is rendered for a human to PASTE.

        This is the layer where an odd id can do harm, and it is the right place
        to handle it — filtering by SHAPE on read instead would discard real ids
        from runtimes this tool does not know about yet. `shlex.quote` is a no-op
        for every ordinary id and makes a hostile one inert.
        """
        return f"claude --resume {shlex.quote(self.session_id)}"


@dataclass
class ArcReport:
    """The resolved arc, plus everything it could NOT see.

    🔴 The gap fields are not decoration. `unstamped_commits` is the count of
    writers this chain structurally cannot name, and `unmeasured_notes` carries
    every leg that failed to answer. A caller that renders `members` without
    them is publishing a chain that looks complete and is not.
    """

    doc: str = ""
    repo: str = ""
    members: list[ArcMember] = field(default_factory=list)
    total_commits: int = 0
    unstamped_commits: int = 0
    readers_measured: bool = False
    unmeasured_notes: list[str] = field(default_factory=list)

    @property
    def stamped_commits(self) -> int:
        return self.total_commits - self.unstamped_commits


def doc_basename(seed: str) -> str:
    """The `handoff-<topic>.md` basename a seed names, or ''.

    Accepts a bare slug (`handoff-foo`, `foo`), a basename, or a full path.
    """
    if not seed:
        return ""
    name = os.path.basename(seed.strip())
    if not name:
        return ""
    if name.endswith(".md"):
        return name if name.startswith("handoff-") else ""
    if name.startswith("handoff-"):
        return f"{name}.md"
    return f"handoff-{name}.md"


def _is_safe_id(value: str) -> bool:
    """Safety only — DELEGATED to the writer's predicate, never re-spelled.

    A `ses_…` token, a uuid and any future spelling all pass; that is the point,
    and it is `session_trailer`'s point, which is why this calls it.
    """
    return bool(session_trailer.valid_id(value))


def trailer_ids(body: str) -> tuple[str, ...]:
    """Every SAFE `Claude-Session-Id:` value in a commit BODY, de-duped, in order.

    "Safe" is the only filter, and it is `session_trailer.valid_id` itself: a
    value carrying a control character or exceeding the length cap is dropped.
    Shape is never judged.

    De-duplicated because a squash of N commits from one session carries the id N
    times — the arc wants distinct sessions, not a write count. Order is first
    appearance, so the caller can rely on it without sorting.
    """
    seen: list[str] = []
    for sid in _TRAILER_RE.findall(body or ""):
        if not _is_safe_id(sid):
            # Dropped for SAFETY only, by the WRITER'S OWN predicate — a
            # control character or an absurd length. Shape is deliberately NOT
            # judged here.
            continue
        if sid not in seen:
            seen.append(sid)
    return tuple(seen)


def doc_in_text(text: str) -> str:
    """The first handoff doc basename a piece of text names, or ''.

    Public because `find-session.py` needs exactly this to annotate an ordinary
    hit, and a second spelling of the pattern over there would be a predicate
    open-coded at two sites — wrong at one of them eventually, and in the same
    direction both times.
    """
    m = _DOC_IN_TEXT.search(text or "")
    return m.group(1) if m else ""


def genesis_names_doc(genesis: str, basename: str) -> bool:
    """Does a session's OPENING message name this handoff doc?

    This is the reader-side discriminator and it is the whole reason the reader
    half is usable. MEASURED 2026-09-18 on `handoff-handoff-resume-skill-trace`:
    a plain keyword search for the slug returns 48 sessions of which 3 are real
    (~6% precision); requiring the slug in the GENESIS returns exactly those 3.
    The 45 rejects mention the doc in passing — a recalled index bullet, a quoted
    path — which is not the same as having been handed the work.

    Matches the basename anywhere in the opening message rather than pinning the
    exact `/handoff` kickoff sentence: the kickoff wording is prose that has been
    reworded before, and degrading to a path match keeps the reader working
    instead of silently returning fewer rows.
    """
    if not genesis or not basename:
        return False
    # A plain substring test IS the whole predicate: `_DOC_IN_TEXT.findall`
    # returns literal substrings of `genesis`, so a second check against its
    # results is unreachable by construction. An earlier revision had one and it
    # read as a widening guard that widened nothing.
    return basename in genesis


#: 🔴 `git -C <path>` DOES NOT OVERRIDE `$GIT_DIR` — and the failure is SILENT.
#: With `GIT_DIR` exported, `git -C <repo> log -- <path>` logs the OTHER repo,
#: finds no such path, and exits **0** with empty output. `GitUnavailable` never
#: fires, `arc_repo_for` already found the doc on disk so exit 5 never fires, and
#: the arc renders `0 of 0 commit(s)` — a confident empty writer set. MEASURED:
#: `GIT_DIR=<other>/.git find-session.py --arc <doc>` printed exactly that at
#: exit 0. That is the precise conflation this module's `GitUnavailable`
#: docstring says the design structurally prevents, so it was not a gap in the
#: posture but a hole underneath it. Every caller inherits the ambient
#: environment: a git hook, `git rebase --exec`, `git bisect run`, or any shell
#: that exported it — and this repo ships `githooks/`.
_GIT_ENV_OVERRIDES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                      "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR")

#: `log.showSignature=true` prepends `gpg: …` lines to STDOUT ahead of each
#: record, which would land inside the first field and make `ArcCommit.sha` read
#: `gpg: Signature made…`. devrc's commits ARE signed; the setting is simply not
#: on today, so this is latent rather than broken. Pinned off per-invocation
#: rather than trusted.
_GIT_CONFIG_PINS = ("-c", "log.showSignature=false")


def _git_env() -> dict:
    """The environment for a git call: ours, minus the repo-selecting overrides."""
    env = dict(os.environ)
    for name in _GIT_ENV_OVERRIDES:
        env.pop(name, None)
    return env


def _git(repo: str, args: Sequence[str],
         run: Callable[..., subprocess.CompletedProcess] | None = None) -> str:
    runner = run or (lambda argv: subprocess.run(
        argv, capture_output=True, text=True, timeout=60, env=_git_env()))
    argv = ["git", "-C", str(repo), *_GIT_CONFIG_PINS, *args]
    try:
        res = runner(argv)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitUnavailable(f"git failed in {repo}: {exc}") from exc
    if getattr(res, "returncode", 1) != 0:
        raise GitUnavailable(
            f"git {' '.join(args)} in {repo} exited "
            f"{getattr(res, 'returncode', '?')}: "
            f"{(getattr(res, 'stderr', '') or '').strip()[:200]}")
    return getattr(res, "stdout", "") or ""


#: `%B` is the whole message — see the module docstring for why nothing here may
#: use `%(trailers:…)`. Records are NUL-separated and fields are US-separated so
#: a body containing blank lines, bullets or newlines cannot be mistaken for a
#: record boundary.
_LOG_FORMAT = "%H%x1f%aI%x1f%s%x1f%B"


#: Probes for the ref that carries a doc's PUSHED history, in order. The first
#: is the branch's own upstream; the second covers a DETACHED HEAD, where
#: `@{upstream}` has nothing to resolve — which is the normal state of the
#: throwaway worktrees the handoff flow itself commits from.
_UPSTREAM_PROBES: tuple[tuple[str, ...], ...] = (
    ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"),
    ("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"),
)


def doc_commit_revs(repo: str,
                    run: Callable[..., subprocess.CompletedProcess] | None = None,
                    ) -> tuple[tuple[str, ...], str | None]:
    """The revisions to walk for a doc's history, plus a coverage note.

    🔴 `git log -- <path>` WITH NO REVISION WALKS `HEAD`, SO A STALE LOCAL BRANCH
    HIDES THE ENTIRE ARC — and it does so silently, as an empty result that reads
    exactly like a doc nobody has committed to. This is not an edge case: the
    handoff flow commits from a THROWAWAY WORKTREE and pushes `HEAD:<branch>`, so
    the local branch of the primary clone never advances. Measured 2026-09-28 in
    `datapacket-talos`, local `trunk` 250+ commits behind `origin/trunk`:
    `git log --follow -- claudedocs/handoff-app-blocks-digital-goods.md` returned
    **0** commits for a doc with **3**, which reported an arc of 1 session as
    complete and dropped the ORIGINATING session — the one writer the reader half
    structurally cannot see (it never resumed the doc it created).

    So walk `HEAD` **and** the upstream. The union can only ADD writers, never
    remove one, and `git` dedupes a commit reachable from both.

    🔴 Returns a note when NO upstream resolved, because then this is back to
    walking `HEAD` alone and the caller must be able to say so. An empty note is
    not a promise of completeness — a ref this clone has never fetched is still
    invisible.
    """
    revs: list[str] = ["HEAD"]
    for probe in _UPSTREAM_PROBES:
        try:
            out = _git(repo, list(probe), run=run).strip()
        except GitUnavailable:
            continue                 # no upstream / no origin/HEAD — try the next
        if out and out not in revs:
            revs.append(out)
            return tuple(revs), None
    return tuple(revs), (
        "no upstream ref resolved, so the WRITER half walked `HEAD` alone — a "
        "commit pushed from a worktree but absent from this clone's checked-out "
        "branch is NOT in this chain")


def doc_commits(repo: str, relpath: str,
                run: Callable[..., subprocess.CompletedProcess] | None = None,
                ) -> tuple[ArcCommit, ...]:
    """Every commit that touched `relpath`, newest first, with its body's ids.

    `--follow` so a doc that was renamed (the `claudedocs/archive/` move renamed
    35 of them) does not truncate its own arc at the rename.

    🔴 ONE `git log` PER REV, MERGED HERE — deliberately not one call listing
    both. `--follow` is documented to take a single starting revision; passing
    two happens to work on git 2.55 (measured) but its path-rewriting is
    undefined across versions, and `--follow` is the half that keeps a renamed
    doc's arc intact. Two defined calls beat one convenient undefined one.
    """
    revs, _note = doc_commit_revs(repo, run=run)
    by_sha: dict[str, ArcCommit] = {}
    for rev in revs:
        out = _git(repo, ["log", "-z", f"--format={_LOG_FORMAT}", "--follow",
                          rev, "--", relpath], run=run)
        for record in out.split("\0"):
            if not record.strip():
                continue
            parts = record.split("\x1f", 3)
            if len(parts) < 4:
                continue
            sha, date, subject, body = parts
            sha = sha.strip()
            if sha in by_sha:
                continue
            by_sha[sha] = ArcCommit(sha=sha, date=date.strip(),
                                    subject=subject.strip(),
                                    session_ids=trailer_ids(body))
    # 🔴 INSERTION ORDER, AND DELIBERATELY NOT A DATE SORT. Each `git log` emits
    # newest-first already, so concatenating the walks in rev order preserves
    # git's own ordering — and `HEAD` comes first precisely because a commit on
    # `HEAD` but not upstream is an UNPUSHED one, i.e. newer than anything the
    # upstream carries.
    #
    # Sorting on `%aI` was the first implementation here and it was WRONG: the
    # format is second-resolution, so commits made inside one second tie and the
    # tie-break (sha) is arbitrary rather than chronological. The fixture
    # reproduced it immediately — all of its commits share a second — but real
    # history does this too, and the damage is silent: `writer_members` reads
    # `commits[-1]` as the ORIGINATING commit, so an inverted tie relabels who
    # started an arc. `dict` preserves insertion order, so this needs no sort.
    return tuple(by_sha.values())


#: The handoff-doc paths a reverse walk will accept, as `git log --name-only`
#: prints them (no quoting, `-z`). 🔴 THE `archive/` ALTERNATION IS LOAD-BEARING
#: — `#1627` renamed 35 docs under `claudedocs/archive/`, so a reader without it
#: reports every drifting session as single-doc for every archived arc. It is the
#: same alternation `_DOC_IN_TEXT` and `arc_repo_for` carry; anchored whole here
#: because a `--name-only` line IS the whole path.
_DOC_PATH_RE = re.compile(
    r"^claudedocs/(?:archive/)?(handoff-[A-Za-z0-9._-]+\.md)$")

#: POSIX-ERE specials. An id is an OPAQUE STRING (`session_trailer`'s 🔴), so it
#: may legitimately carry a character git's `-E` would read as a metacharacter —
#: a single unescaped `(` makes the whole walk exit non-zero, which surfaces as
#: `GitUnavailable` for a perfectly ordinary session. Escaped rather than
#: shape-filtered, because filtering by shape is the thing that module forbids.
_ERE_SPECIAL = set(r".[]{}()*+?^$|\/")


def _ere_escape(value: str) -> str:
    return "".join("\\" + c if c in _ERE_SPECIAL else c for c in value)


def _trailer_grep(session_id: str) -> str:
    """The `--grep` PREFILTER for one session id.

    🔴 A PREFILTER AND NOTHING MORE. It is wider than `_TRAILER_RE` in two ways
    that both matter: `[[:space:]]` is POSIX ERE's nearest spelling of `[ \\t]`
    and also admits other blanks, and several of these are ORed into ONE walk —
    git returns a commit matching ANY of them. So every matched body is re-parsed
    with `trailer_ids()`; crediting a matched commit to every requested id is how
    a session gets docs it never wrote. One rule, one place.
    """
    return (f"^{_ere_escape(TRAILER_KEY)}:[[:space:]]*"
            f"{_ere_escape(session_id)}[[:space:]]*$")


def sessions_docs(repo: str, session_ids: Iterable[str],
                  run: Callable[..., subprocess.CompletedProcess] | None = None,
                  ) -> dict[str, tuple[str, ...]]:
    """`{session_id: handoff doc basenames}` for commits IN `repo`, newest first.

    🔴 THE SAME EDGE `doc_commits` READS, READ SESSION-FIRST — and that is the
    whole change. `doc_commits` asks "which sessions touched THIS doc"; a session
    that DRIFTS (resumed from handoff-A, ended by writing handoff-B) is invisible
    to every doc-first query but handoff-B's. No new trailer, no new persisted
    state — the commit already carries the id, and its FILE LIST names the doc.

    🔴 THE RATE, AT THE SCOPE MEASURED — **29 of 291 (~1 in 10)**, not 1 in 8.
    Measured 2026-10-03 over the stamped corpus at the scope `doc_commit_revs`
    walks (HEAD + upstream per handle), across the four SET handles with
    `$CIVITAI_CLI` UNMEASURED. **36 of 291** stamped writer sessions touched >=2
    distinct docs, but 7 of those 36 wrote every one of them in ONE commit — a
    bulk move, not a session changing subject. Requiring some pair of a
    session's docs to have **DISJOINT commit sets** gives **29 of 291**;
    excluding the single 37-doc bulk-move session gives **28 of 291**. 4 of the
    29 crossed repos. ⚠ State it at this scope: the `>=2 docs` population is a
    different, wider claim, and quoting it as the drift rate overstates by ~25%.

    🔴 ONE WALK FOR ALL THE IDS, by ORing their `--grep` patterns — **~3x
    faster than per-member** for a six-member arc over the 4 readable handles.
    Measured 2026-10-03 on the laptop (8 cores), the three shapes INTERLEAVED
    round-robin in ONE process, warmed once, 5 runs each, medians, AT TWO LOAD
    POINTS because one measurement here is not a general claim:

        load avg ~5        ~18        shape
        ---------    ---------        -----------------------------------------
            1.91s      7.81s         A bare prefix `^Claude-Session-Id: `
            3.97s     14.91s         B the ORed anchored full ids RUN HERE
           12.65s     42.37s         C one pass per member
            3.19x      2.84x         RATIO C/B

    🔴 THE RATIO IS THE CLAIM; THE ABSOLUTES ARE A PROPERTY OF THE BOX'S LOAD,
    NOT OF THE QUERY. They swing ~4x between those two points — so quoting one
    as "the cost" is how this docstring was wrong the first time. The ratio is
    internally controlled by the interleaving (a load swing hits all three
    shapes, not whichever ran last) and lands at 2.8-3.2x here; round 0
    measured 2.1x at a third load. Direction not in doubt, magnitude ~2-3x.

    ⚠ AN EARLIER DOCSTRING QUOTED **1.28s**, WHICH TIMED SHAPE A — the bare
    prefix, not the anchored ORed full ids this function runs. Paired against
    `~10s` for per-member it implied ~8x, where the real saving is ~3x. (And
    1.28s does reproduce as shape A at low load: 1.91s above. The number was
    right; the query it was attached to was not.) Quote the shape you timed.

    Raises `GitUnavailable` like its siblings: a session that wrote nothing here
    and a repo that could not be read produce the same empty dict otherwise, and
    this module's posture is that a scoped zero is never an absence.
    """
    wanted = [sid for sid in dict.fromkeys(session_ids) if sid]
    out: dict[str, list[str]] = {sid: [] for sid in wanted}
    if not wanted:
        return {}
    greps = [f"--grep={_trailer_grep(sid)}" for sid in wanted]
    revs, _note = doc_commit_revs(repo, run=run)
    for rev in revs:
        # 🔴 `--name-only` RATHER THAN `--follow`, and NOT a per-doc pathspec.
        # `--follow` takes one starting path by definition; the question here is
        # "which docs", so the paths are the ANSWER and cannot also be the query.
        # The pathspec is the directory, which is what makes this cheap.
        raw = _git(repo, ["log", "-z", f"--format={_LOG_FORMAT}", "-E", *greps,
                          "--name-only", rev, "--", "claudedocs"], run=run)
        # With `-z`, git emits the format record, then each changed path, all
        # NUL-separated. A record is told from a path by the `\x1f` field
        # separators `_LOG_FORMAT` plants: a path cannot contain one.
        ids: tuple[str, ...] = ()
        seen_shas: set[str] = set()
        for chunk in raw.split("\0"):
            if "\x1f" in chunk:
                parts = chunk.split("\x1f", 3)
                if len(parts) < 4:
                    ids = ()
                    continue
                sha = parts[0].strip()
                # 🔴 RE-PARSED, NOT TRUSTED — see `_trailer_grep`.
                ids = tuple(sid for sid in trailer_ids(parts[3])
                            if sid in out)
                if sha in seen_shas:
                    ids = ()
                seen_shas.add(sha)
                continue
            path = chunk.strip()
            if not path or not ids:
                continue
            m = _DOC_PATH_RE.match(path)
            if not m:
                continue
            for sid in ids:
                if m.group(1) not in out[sid]:
                    out[sid].append(m.group(1))
    return {sid: tuple(docs) for sid, docs in out.items()}


# ⚠ NO SINGULAR `session_docs(repo, sid)` DOOR, DELETED 2026-10-03 AND NOT AN
# OVERSIGHT. It shipped as "a thin single-session door rather than a second
# walk" and had ZERO production callers — the cross-arc footer calls the plural
# — so it was kept alive by 10 tests and a named FOLLOW-ON (`arc_seeds_to_docs`),
# which is not a named CONSUMER. That is the distinction the `next_command`
# deletion in `find-session.py` turned on, and this is the same rule applied
# again rather than an exception argued once. `sessions_docs(repo, (sid,))[sid]`
# is the one-line spelling and returns the identical answer, so nothing was
# lost but a name. Re-add it when a caller exists and is NAMED here.


def writer_members(commits: Sequence[ArcCommit], repo: str = "") -> list[ArcMember]:
    """Writer sessions, oldest commit first.

    The OLDEST stamped commit's sessions are `originated`; every later one
    `wrote`. That is an inference from commit order, not from a recorded fact —
    if the true originating commit is unstamped (which is exactly what the
    coverage gap means), the oldest STAMPED session inherits the label. The
    report's `unstamped_commits` is what tells a reader whether to trust it.
    """
    by_session: dict[str, list[ArcCommit]] = {}
    oldest_ids: tuple[str, ...] = ()
    for c in reversed(commits):            # oldest first
        if c.session_ids and not oldest_ids:
            oldest_ids = c.session_ids
        for sid in c.session_ids:
            by_session.setdefault(sid, []).append(c)
    members: list[ArcMember] = []
    for sid, cs in by_session.items():
        # 🔴 EVERY session on the oldest stamped commit, not just the first. A
        # GitHub squash puts SEVERAL sessions' trailers in one body — which is
        # this module's founding premise — so `i == 0` labelled one of them and
        # silently demoted its co-authors to `wrote`. The docstring said
        # "session_S_" while the body labelled one; this is the body catching up.
        members.append(ArcMember(
            session_id=sid,
            role=ROLE_ORIGINATED if sid in oldest_ids else ROLE_WROTE,
            repo=repo,
            first_seen=cs[0].date,
            commits=tuple(c.sha for c in cs),
        ))
    return members


def reader_members(rows: Iterable[Mapping], basename: str,
                   repo: str = "") -> list[ArcMember]:
    """Sessions whose genesis names the doc, from `find-session` JSON rows."""
    out: list[ArcMember] = []
    for r in rows:
        if not genesis_names_doc(r.get("genesis") or "", basename):
            continue
        out.append(ArcMember(
            session_id=r.get("session_id") or "",
            role=ROLE_RESUMED,
            repo=repo or os.path.basename(r.get("cwd") or ""),
            first_seen=r.get("first") or "",
        ))
    return [m for m in out if m.session_id]


def merge_members(*groups: Sequence[ArcMember]) -> list[ArcMember]:
    """Union by session id, keeping the most specific role, ordered by time.

    🔴 A session appearing in BOTH halves is the common case, not an edge: a
    session resumes from the doc and then writes to it. Keeping the git-derived
    role is deliberate — it is the one backed by a commit.
    """
    best: dict[str, ArcMember] = {}
    for group in groups:
        for m in group:
            prev = best.get(m.session_id)
            if prev is None:
                best[m.session_id] = m
                continue
            if ROLE_ORDER.index(m.role) < ROLE_ORDER.index(prev.role):
                best[m.session_id] = ArcMember(
                    session_id=m.session_id, role=m.role,
                    repo=m.repo or prev.repo,
                    first_seen=prev.first_seen or m.first_seen,
                    commits=m.commits or prev.commits)
            elif not prev.first_seen and m.first_seen:
                best[m.session_id] = ArcMember(
                    session_id=prev.session_id, role=prev.role,
                    repo=prev.repo or m.repo, first_seen=m.first_seen,
                    commits=prev.commits or m.commits)
    # Unknown timestamps sort LAST rather than first: an empty string would
    # otherwise put a session with no measured time at the head of the chain.
    return sorted(best.values(),
                  key=lambda m: (m.first_seen == "", m.first_seen, m.session_id))


def coverage_line(report: "ArcReport") -> str:
    """The sentence that keeps a partial chain from reading as a complete one.

    🔴 NEVER RETURNS EMPTY FOR A ZERO. `0 of 0` and `0 of 7` are both printed in
    full, because "no line" is indistinguishable from "nothing missing" and this
    module's entire posture is the opposite of that.
    """
    return (f"{report.unstamped_commits} of {report.total_commits} commit(s) on "
            f"this doc carry no session id — those writers are NOT in this chain")


def resolve_arc(repo: str, relpath: str,
                reader_rows: Iterable[Mapping] | None = None,
                readers_measured: bool = False,
                run: Callable[..., subprocess.CompletedProcess] | None = None,
                ) -> ArcReport:
    """Resolve one doc's arc. Pure apart from the injected `run`.

    `reader_rows` is `find-session` JSON; pass `readers_measured=False` (the
    default) when the transcript walk did not run, so the report says the reader
    half is UNMEASURED instead of implying nobody resumed.
    """
    basename = os.path.basename(relpath)
    report = ArcReport(doc=basename, repo=os.path.basename(str(repo).rstrip("/")))
    try:
        commits = doc_commits(repo, relpath, run=run)
    except GitUnavailable as exc:
        report.unmeasured_notes.append(
            f"git could not answer for {basename}: {exc} — the WRITER half of "
            f"this arc was NOT measured, which is not the same as empty")
        commits = ()
    else:
        # 🔴 Only meaningful when the walk RAN. On `GitUnavailable` above the
        # writer half is already reported unmeasured, and adding a narrower note
        # about which refs it used would imply it got that far.
        try:
            _revs, rev_note = doc_commit_revs(repo, run=run)
        except GitUnavailable:
            rev_note = None
        if rev_note:
            report.unmeasured_notes.append(rev_note)
    report.total_commits = len(commits)
    report.unstamped_commits = sum(1 for c in commits if not c.stamped)
    writers = writer_members(commits, repo=report.repo)
    readers = reader_members(reader_rows or [], basename, repo=report.repo)
    report.readers_measured = bool(readers_measured)
    if not report.readers_measured:
        report.unmeasured_notes.append(
            "the transcript corpus was NOT walked, so sessions that resumed this "
            "doc without committing to it are NOT in this chain")
    report.members = merge_members(writers, readers)
    # 🔴 THE `originated` LABEL IS AN INFERENCE FROM COMMIT ORDER, AND IT IS
    # UNSOUND EXACTLY WHEN A WRITER IS MISSING. If any commit on this doc carries
    # no session id, the true originating commit may be one of them, and the
    # oldest STAMPED session is then merely the earliest one we can see. Printing
    # `ORIGINATED` there is an affirmative claim about who started the effort,
    # made on the same screen as a line saying some writers are invisible —
    # measured at 45% unstamped across the corpus, so this is the common case on
    # an older doc, not a corner. Demote rather than guess.
    if report.unstamped_commits and report.members:
        report.members = [
            ArcMember(session_id=m.session_id, role=ROLE_EARLIEST_STAMPED,
                      repo=m.repo, first_seen=m.first_seen, commits=m.commits)
            if m.role == ROLE_ORIGINATED else m
            for m in report.members]
    return report
