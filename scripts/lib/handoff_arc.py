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
import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Sequence

__all__ = [
    "TRAILER_KEY",
    "ROLE_ORIGINATED",
    "ROLE_WROTE",
    "ROLE_RESUMED",
    "ROLE_ORDER",
    "ArcCommit",
    "ArcMember",
    "ArcReport",
    "trailer_ids",
    "genesis_names_doc",
    "doc_basename",
    "doc_commits",
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

#: A handoff doc path as it appears in prose. `archive/` is included because
#: `#1627` renamed 35 docs under it and an arc must not end at a rename.
_DOC_IN_TEXT = re.compile(
    r"claudedocs/(?:archive/)?(handoff-[A-Za-z0-9._-]+\.md)")

ROLE_ORIGINATED = "originated"
ROLE_WROTE = "wrote"
ROLE_RESUMED = "resumed"

#: Most-specific first. A session that both created the doc and later resumed it
#: is reported as `originated`, because that is the fact a reader is hunting.
ROLE_ORDER = (ROLE_ORIGINATED, ROLE_WROTE, ROLE_RESUMED)


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
        return f"claude --resume {self.session_id}"


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


def trailer_ids(body: str) -> tuple[str, ...]:
    """Every `Claude-Session-Id:` value in a commit BODY, de-duplicated, in order.

    De-duplicated because a squash of N commits from one session carries the id N
    times — the arc wants distinct sessions, not a write count. Order is first
    appearance, so the caller can rely on it without sorting.
    """
    seen: list[str] = []
    for sid in _TRAILER_RE.findall(body or ""):
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
    if basename in genesis:
        return True
    return basename in set(_DOC_IN_TEXT.findall(genesis))


def _git(repo: str, args: Sequence[str],
         run: Callable[..., subprocess.CompletedProcess] | None = None) -> str:
    runner = run or (lambda argv: subprocess.run(
        argv, capture_output=True, text=True, timeout=60))
    argv = ["git", "-C", str(repo), *args]
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


def doc_commits(repo: str, relpath: str,
                run: Callable[..., subprocess.CompletedProcess] | None = None,
                ) -> tuple[ArcCommit, ...]:
    """Every commit that touched `relpath`, newest first, with its body's ids.

    `--follow` so a doc that was renamed (the `claudedocs/archive/` move renamed
    35 of them) does not truncate its own arc at the rename.
    """
    out = _git(repo, ["log", "-z", f"--format={_LOG_FORMAT}", "--follow",
                      "--", relpath], run=run)
    commits: list[ArcCommit] = []
    for record in out.split("\0"):
        if not record.strip():
            continue
        parts = record.split("\x1f", 3)
        if len(parts) < 4:
            continue
        sha, date, subject, body = parts
        commits.append(ArcCommit(sha=sha.strip(), date=date.strip(),
                                 subject=subject.strip(),
                                 session_ids=trailer_ids(body)))
    return tuple(commits)


def writer_members(commits: Sequence[ArcCommit], repo: str = "") -> list[ArcMember]:
    """Writer sessions, oldest commit first.

    The OLDEST stamped commit's sessions are `originated`; every later one
    `wrote`. That is an inference from commit order, not from a recorded fact —
    if the true originating commit is unstamped (which is exactly what the
    coverage gap means), the oldest STAMPED session inherits the label. The
    report's `unstamped_commits` is what tells a reader whether to trust it.
    """
    by_session: dict[str, list[ArcCommit]] = {}
    for c in reversed(commits):            # oldest first
        for sid in c.session_ids:
            by_session.setdefault(sid, []).append(c)
    members: list[ArcMember] = []
    for i, (sid, cs) in enumerate(by_session.items()):
        members.append(ArcMember(
            session_id=sid,
            role=ROLE_ORIGINATED if i == 0 else ROLE_WROTE,
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
    return report
