#!/usr/bin/env python3
"""`cairn who <task>` — resolve a TASK to the sessions that worked it, the tmux
windows hosting them, and the transcripts they wrote.

WHY THIS IS A COMMAND AND NOT A RECIPE. Every hop already resolved before this
existed; none of them joined. Answering "who is working task 360, and where do I
read what they did" took four tools and a hand-written jq join, which is exactly
the shape of thing nobody does twice — so the question stopped being asked.

🔴 THE TWO ANSWERS HAVE DIFFERENT LIFETIMES, AND COLLAPSING THEM IS THE WHOLE
TRAP. A tmux window is TRANSIENT: it is gone when the pane closes, the session
ends or the host reboots. A transcript is DURABLE: it is a file on disk that
outlives all of that. Measured 2026-08-27 on the task this command was written
for — the session's window had already vanished, while its 6 MB transcript sat
exactly where it was written. A resolver that reports only the window answers
"nobody" for most of the history it is asked about, and a resolver that reports
one line for both makes "the window is gone" indistinguishable from "the work
never happened". So every session yields TWO independent findings, always
labelled, and one may be present while the other is not.

🔴 AN UNMEASURED LIVE HALF IS NOT "NO WINDOW". `session-manager` shells out to
tmux on two hosts; the laptop may be asleep, off the nebula, or simply slow. If
that lookup fails, the honest report is UNMEASURED with the reason — never an
empty window column, which reads as "this session is not running anywhere" and
is the silent zero this repo's whole measurement layer exists to prevent. The
transcript half is unaffected by that failure and is still reported.

🔴 THE SESSION-ID JOIN KEY IS NOT ALWAYS A UUID. Measured across a live scan:
39 of 41 windows carried a uuid in `claude_session_id` and 2 carried a
`ses_…` token from a different runtime. A join that assumes uuid shape, or
that lowercases/normalises one side only, silently matches nothing and reports
a clean "no live window". The comparison here is on the exact string, both
sides, and `test_cairn_who.py` pins a non-uuid id against a real match.

WHAT IT DOES NOT DO. It does not raise, focus or switch to a window — that is
the operator's screen, and stealing it is a `pkill`-class action (RULES.md).
It prints the address; moving there is the human's call.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

#: How long to wait on each external tool. `session-manager` shells into tmux on
#: two hosts and a full cross-host scan measured ~5s; the laptop being asleep is
#: the case this bound exists for, and it must EXPIRE rather than hang a command
#: someone typed.
DEFAULT_TIMEOUT = 60

#: Outcome vocabulary. Every one of these is a DIFFERENT answer to "who worked
#: this task", and the point of naming them is that several of them print little
#: or nothing — which is precisely when they get confused for each other.
WHO_RESOLVED = "resolved"
#: The task exists and clawgate recorded no session against it. A real state —
#: a task filed through the web UI has none — and NOT an error.
WHO_NO_SESSIONS = "no-sessions-recorded"
#: The task id is not in clawgate. Distinct from every "found nothing" state.
WHO_NO_TASK = "task-not-found"
#: Could not ask clawgate at all. Distinct from `task-not-found`, because one of
#: them means "the answer is no" and the other means "there was no answer".
WHO_NO_CLAWGATE = "clawgate-unreachable"
#: Sessions exist, and NEITHER half resolved for any of them — no live window
#: and no transcript. That is a genuine gap worth reporting loudly, and it is
#: not the same as the task having no sessions.
WHO_UNLOCATED = "sessions-recorded-but-none-located"
#: clawgate refused the id itself (400). A typo is the likeliest failure of all,
#: and reporting it as `clawgate-unreachable` sent the operator to debug the
#: network while clawgate's own 400 was printed directly above.
WHO_BAD_TASK_ID = "bad-task-id"

EXIT_OK = 0
EXIT_USAGE = 2
#: Nothing located though sessions were recorded — see `WHO_UNLOCATED`.
EXIT_UNLOCATED = 6
#: The task id does not exist.
EXIT_NO_TASK = 7
#: Could not reach clawgate. Never folded into 7: "no such task" and "I could
#: not ask" are opposite claims that print the same emptiness.
EXIT_NO_CLAWGATE = 8


class WhoError(RuntimeError):
    """A failure that names which HOP could not be taken."""


class TaskNotFound(WhoError):
    """clawgate answered, and the answer is "no such task"."""


class BadTaskId(WhoError):
    """clawgate refused the id itself (400). A typo, not an outage."""


class ClawgateUnreachable(WhoError):
    """clawgate could not be asked at all."""


#: `session-manager`'s own exit codes, which it documents and which this module
#: must honour rather than re-derive.
#:
#: 🔴 3 AND 4 ARE THE WHOLE POINT AND THEY BOTH RETURN AN EMPTY SCAN. 3 is
#: `EXIT_EMPTY` — every requested host answered and the answer is a real zero.
#: 4 is `EXIT_UNAVAILABLE` — NO host could be reached, so the zero is
#: unmeasured. Its source says a caller "must never read success off a truncated
#: run". Treating 4 as success is exactly that, and it renders as a confident
#: "this session is running nowhere".
SM_EXIT_EMPTY = 3
SM_EXIT_UNAVAILABLE = 4


@dataclass
class WindowHit:
    """A LIVE tmux window. Transient — see the module docstring."""

    host: str
    session: str
    window_index: str | None = None
    window_id: str | None = None
    pane_id: str | None = None
    codename: str | None = None
    hotkey: str | None = None
    path: str | None = None

    @property
    def address(self) -> str:
        """`<session>:<window_index>` — what `tmux switch-client -t` accepts.

        🔴 `window_index` AND `window_id` ARE DIFFERENT THINGS and both are
        carried. The index (`1`) is what a human types; the id (`@349`) is
        stable across renumbering when other windows are killed. Printing only
        the index sends someone to a different window after a close; printing
        only the id gives them something tmux accepts but nobody recognises.
        """
        idx = self.window_index if self.window_index is not None else "?"
        return f"{self.session}:{idx}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "session": self.session,
            "window_index": self.window_index,
            "window_id": self.window_id,
            "pane_id": self.pane_id,
            "codename": self.codename,
            "hotkey": self.hotkey,
            "path": self.path,
            "address": self.address,
        }


@dataclass
class SessionHit:
    """One session on the task, with its two independent findings."""

    session_id: str
    role: str | None = None
    project: str | None = None
    cwd: str | None = None
    host: str | None = None
    last_seen: str | None = None
    #: The transient half. `None` means "looked and found none" ONLY when
    #: `windows_measured` is True.
    window: WindowHit | None = None
    #: 🔴 The flag that keeps an absent window honest. False means the live
    #: lookup did not happen or failed, so `window is None` says nothing.
    windows_measured: bool = True
    #: The durable half.
    transcript: Path | None = None

    @property
    def located(self) -> bool:
        return self.window is not None or self.transcript is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "role": self.role,
            "project": self.project,
            "cwd": self.cwd,
            "host": self.host,
            "last_seen": self.last_seen,
            "window": self.window.to_dict() if self.window else None,
            "windows_measured": self.windows_measured,
            "transcript": str(self.transcript) if self.transcript else None,
        }


@dataclass
class WhoReport:
    task: str
    state: str
    sessions: list[SessionHit] = field(default_factory=list)
    title: str | None = None
    status: str | None = None
    #: Why the live half is unmeasured on this run, if it is. Printed; never
    #: swallowed.
    windows_reason: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return {
            WHO_RESOLVED: EXIT_OK,
            WHO_NO_SESSIONS: EXIT_OK,
            WHO_UNLOCATED: EXIT_UNLOCATED,
            WHO_NO_TASK: EXIT_NO_TASK,
            WHO_NO_CLAWGATE: EXIT_NO_CLAWGATE,
            WHO_BAD_TASK_ID: EXIT_USAGE,
        }[self.state]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "state": self.state,
            "title": self.title,
            "status": self.status,
            "windows_reason": self.windows_reason,
            "notes": list(self.notes),
            "sessions": [s.to_dict() for s in self.sessions],
        }


# --------------------------------------------------------------------------- #
# The hops
# --------------------------------------------------------------------------- #


def unbounded_timeout_reason(value) -> str | None:
    """Why `value` is not a usable timeout, or `None` if it is fine.

    🔴 ONE PREDICATE, ONE PLACE — AND CONSOLIDATING IT IS WHAT FOUND THE BUG.
    This rule was open-coded at two sites (`_run` here, `fetch_snapshot` in
    `scripts/cairn`) and the copies DISAGREED: only one excluded `bool`. A
    comment claiming the two mirrored each other was therefore false, and the
    weaker copy was measured running `_run(cmd, True)` with a ONE-SECOND bound
    and reporting `did not answer within Trues` — verbatim the "nobody notices"
    failure `_run`'s own docstring describes.

    `bool` is the trap: it subclasses `int`, so `isinstance(x, int)` accepts
    `True` and silently yields a 1s timeout. `None` is the other: it means NO
    timeout to both `subprocess` and `urlopen`, an unbounded wait rather than a
    default. Both callers raise their OWN exception type from this reason, so
    the rule is shared without coupling the store path to `who`'s error class.
    """
    if isinstance(value, bool):
        return (f"timeout={value!r} is a bool — it subclasses int, so this "
                "would silently run with a 1-second bound")
    if not isinstance(value, int):
        return (f"timeout={value!r} is not an int — a missing bound is an "
                "UNBOUNDED wait, not a default")
    if value <= 0:
        return (f"timeout={value!r} is not positive — a non-positive bound is "
                "an UNBOUNDED wait, not a default")
    return None


def _one_line(text: str) -> str:
    """Collapse a diagnostic to one line.

    🔴 APPLIED IN `_run`, NOT AT EACH CALL SITE. The first version collapsed
    stderr inside `fetch_task` only, so `live_windows` still carried multi-line
    text into `windows_reason`, which `render` prints inside an indented block —
    the exact breakage the other site's comment described. One rule, two places
    is how the second copy stays wrong.
    """
    return " ".join((text or "").split())


def _run(cmd: Sequence[str], timeout: int) -> tuple[int, str, str]:
    """Run a tool, capturing both streams SEPARATELY.

    🔴 Never `2>&1`. `clawgatectl` documents that JSON goes to stdout and
    nothing else ever does, precisely so a diagnostic on stderr cannot corrupt
    a parse. Merging them here would throw that guarantee away at the one place
    that depends on it.
    """
    # 🔴 `timeout=None` MEANS NO TIMEOUT AT ALL, so a None here does not
    # "fall back to a default" — it removes the bound entirely and `cairn who`
    # waits forever on a host that will never answer. Measured: a None timeout
    # let a 2s sleep run to completion unbounded. The expiry message would also
    # have read "within Nones", which is how nobody notices. Refuse it.
    bad = unbounded_timeout_reason(timeout)
    if bad:
        raise WhoError(f"refusing to run {cmd[0]}: {bad}")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise WhoError(f"{cmd[0]} is not on PATH")
    except subprocess.TimeoutExpired:
        raise WhoError(f"{cmd[0]} did not answer within {timeout}s")
    return p.returncode, p.stdout, _one_line(p.stderr)


def fetch_task(task: str, *, timeout: int = DEFAULT_TIMEOUT,
               runner=_run) -> dict[str, Any]:
    """`clawgatectl task get <id>` -> the task object.

    Its documented exit codes are used as-is rather than re-derived from the
    message text: 4 is not-found, 3 is auth, 6 is network. Reading the message
    instead is how a wording change turns "unreachable" into "not found".
    """
    if not str(task).strip():
        raise WhoError("empty task id")
    rc, out, err = runner(["clawgatectl", "task", "get", str(task)], timeout)
    # One line. A tool's stderr is often several (a version-skew notice above
    # the real error), and interpolating it raw breaks the render's
    # indentation so the follow-up sentence reads as unrelated output.
    detail = err or "no diagnostic"
    # 🔴 CLASSIFY BY EXCEPTION TYPE, NOT BY THE MESSAGE TEXT. The first version
    # raised one error class and had the caller substring-match `"has no task"`
    # to decide between "no such task" and "could not ask" — while interpolating
    # clawgate's raw stderr into the other message, so any diagnostic containing
    # that phrase would have flipped the verdict. Deciding a state by grepping a
    # string you just built is the defect this module exists to avoid, one layer
    # up from the exit code that already answers it.
    if rc == 4:
        raise TaskNotFound(f"clawgate has no task {task}")
    if rc == 2:
        # 400 — the id itself was refused. An operator typo, and the LIKELIEST
        # failure of all; reporting it as "unreachable" sends them to debug the
        # network while clawgate's own 400 is printed directly above.
        raise BadTaskId(f"clawgatectl refused the id {task!r} — {detail}")
    if rc != 0:
        raise ClawgateUnreachable(f"clawgatectl exited {rc} — {detail}")
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise ClawgateUnreachable(f"clawgatectl returned non-JSON: {exc}")


def sessions_of(task_obj: dict[str, Any]) -> list[SessionHit]:
    """The `sessions[]` clawgate embeds on a task read.

    A session with no `sessionId` is DROPPED rather than carried as a blank —
    a blank id would match every window whose id is also blank, which is how a
    join invents a result.
    """
    out: list[SessionHit] = []
    for row in task_obj.get("sessions") or []:
        sid = (row.get("sessionId") or "").strip()
        if not sid:
            continue
        out.append(SessionHit(
            session_id=sid,
            role=row.get("role"),
            project=row.get("project"),
            cwd=row.get("cwd"),
            host=row.get("host"),
            last_seen=row.get("lastSeenAt"),
        ))
    return out


def _index_windows(payload: dict[str, Any]) -> tuple[dict[str, WindowHit], list[str]]:
    """`claude_session_id` -> window, PLUS the hosts that were not measured.

    🔴 THE SECOND RETURN VALUE IS THE HONEST HALF, AND OMITTING IT SHIPPED A
    SILENT ZERO. `session-manager` publishes `reachable`, `windows_measured` and
    `windows_error` PER HOST, and leaves an unreachable host's `windows` as an
    empty list while still exiting 0 because the other host answered.
    Indexing only `windows` therefore turns "the laptop is asleep" into a
    confident "this session is running nowhere" — demonstrated with the same
    session rendering `none live` with the laptop down and a real window with it
    up. `ConnectTimeout=4` means a sleeping laptop fails FAST, so this is the
    everyday case rather than an edge.

    🔴 LAST WRITER WINS IS WRONG HERE, SO IT IS NOT DONE. Two windows can carry
    the same session id (a split pane, a re-attached session). The first is kept
    rather than the last, so the answer cannot depend on iteration order.
    `session-manager` dumps with `sort_keys=True`, which is what makes the host
    order stable run to run — a property of ITS writer, not of dicts, so it is
    named here rather than assumed.
    """
    idx: dict[str, WindowHit] = {}
    unmeasured: list[str] = []
    for host, block in sorted((payload.get("hosts") or {}).items()):
        if (block.get("reachable") is False
                or block.get("windows_measured") is False
                or block.get("windows_error")):
            unmeasured.append(host)
            continue
        for w in block.get("windows") or []:
            sid = w.get("claude_session_id")
            if not sid or sid in idx:
                continue
            idx[sid] = WindowHit(
                host=w.get("host") or host,
                session=str(w.get("session")),
                window_index=(str(w["window_index"])
                              if w.get("window_index") is not None else None),
                window_id=w.get("window_id"),
                pane_id=w.get("pane_id"),
                codename=w.get("codename"),
                hotkey=w.get("hotkey"),
                path=w.get("path"),
            )
    return idx, unmeasured


def live_windows(*, timeout: int = DEFAULT_TIMEOUT, host: str | None = None,
                 runner=_run, script: Path | None = None
                 ) -> tuple[dict[str, WindowHit], list[str]]:
    """Scan tmux across hosts and index by session id.

    🔴 `--lean` IS NOT USABLE HERE and that is measured, not assumed: its
    `lean_row_fields` omit `pane_id`, `window_id` and `codename`, which are
    three of the four things this command exists to print. The full scan cost
    ~5s cross-host, which is what a typed command can afford.
    """
    exe = Path(script) if script else _session_manager_path()
    cmd = [sys.executable, str(exe), "scan", "--json"]
    if host:
        cmd += ["--host", host]
    rc, out, err = runner(cmd, timeout)
    detail = err or "no diagnostic"
    # 🔴 rc 4 ARRIVES WITH A FULL, WELL-FORMED JSON REPORT OF ZERO WINDOWS, so
    # the old `rc != 0 and not out.strip()` guard let it through as a measured
    # scan. `session-manager` documents 4 as "NO requested host could be
    # reached — the 0 is unmeasured", and its own header says a caller must
    # never read success off a truncated run. 3 is the opposite code and is a
    # genuine measured zero, so it deliberately does NOT raise.
    if rc == SM_EXIT_UNAVAILABLE:
        raise WhoError(
            f"session-manager exited {rc} — no host could be reached, so its "
            f"empty scan is UNMEASURED, not a zero ({detail})")
    if rc not in (0, SM_EXIT_EMPTY) and not out.strip():
        raise WhoError(f"session-manager exited {rc} — {detail}")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        raise WhoError(f"session-manager returned non-JSON: {exc}")
    return _index_windows(payload)


def _session_manager_path() -> Path:
    """Prefer the checkout this file ships in; fall back to PATH."""
    local = Path(__file__).resolve().parents[1] / "session-manager"
    if local.is_file():
        return local
    found = shutil.which("session-manager")
    if found:
        return Path(found)
    raise WhoError("session-manager was not found in this checkout or on PATH")


def find_transcript(session_id: str, root: Path | None = None) -> Path | None:
    """Delegate to `transcript_search`, the repo's ONE transcript locator.

    🔴 Re-implementing the `<cwd> -> -home-zach-…` mangling here would make two
    copies of a rule that has to agree, and `claude/RULES.md` records that a
    predicate open-coded at N sites is typically wrong at N-1 of them. That
    module already survived a consolidation that found seven real bugs; it also
    resolves by SCAN, so it finds a transcript whose project directory does not
    match the cwd clawgate recorded — which happens whenever a session moved.
    """
    lib = str(Path(__file__).resolve().parent)
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import transcript_search  # noqa: PLC0415

    return transcript_search.find_transcript(session_id, root=root)


# --------------------------------------------------------------------------- #
# The join
# --------------------------------------------------------------------------- #


def resolve(task: str, *, timeout: int = DEFAULT_TIMEOUT,
            host: str | None = None, skip_windows: bool = False,
            transcript_root: Path | None = None,
            task_fetcher=fetch_task, window_fetcher=live_windows,
            transcript_finder=find_transcript) -> WhoReport:
    """Task -> sessions -> (window, transcript). Every hop's failure is NAMED."""
    try:
        obj = task_fetcher(task, timeout=timeout)
    except TaskNotFound as exc:
        return WhoReport(task=task, state=WHO_NO_TASK, notes=[str(exc)])
    except BadTaskId as exc:
        return WhoReport(task=task, state=WHO_BAD_TASK_ID, notes=[str(exc)])
    except WhoError as exc:
        return WhoReport(task=task, state=WHO_NO_CLAWGATE, notes=[str(exc)])

    report = WhoReport(
        task=task, state=WHO_RESOLVED,
        title=obj.get("title"), status=obj.get("status"),
    )
    report.sessions = sessions_of(obj)
    if not report.sessions:
        report.state = WHO_NO_SESSIONS
        return report

    windows: dict[str, WindowHit] = {}
    measured = False
    if skip_windows:
        report.windows_reason = "--no-windows: the live half was not looked at"
    else:
        try:
            windows, unmeasured_hosts = window_fetcher(timeout=timeout, host=host)
            # 🔴 A PARTIAL SCAN IS NOT A MEASURED SCAN. If ANY host went
            # unmeasured, a session with no match might simply have been on
            # that host, so "none live" is unproven for every session that did
            # not match. A session that DID match is a positive finding and is
            # unaffected — an absence is what needs the caveat.
            measured = not unmeasured_hosts
            if unmeasured_hosts:
                report.windows_reason = (
                    "host(s) not measured by session-manager: "
                    + ", ".join(unmeasured_hosts))
        except WhoError as exc:
            # 🔴 NOT fatal, and NOT an empty window column. The durable half is
            # independent of tmux being reachable, and it is usually the half
            # that answers the question.
            report.windows_reason = str(exc)

    for s in report.sessions:
        # A hit is a hit regardless of whether some OTHER host was measured.
        s.window = windows.get(s.session_id)
        s.windows_measured = measured or s.window is not None
        try:
            s.transcript = transcript_finder(s.session_id, root=transcript_root)
        except Exception as exc:  # noqa: BLE001 — a bad transcript root must
            # degrade this ONE session's durable half, never the whole report.
            report.notes.append(
                f"transcript lookup failed for {s.session_id[:8]}…: {exc}")

    # 🔴 `UNLOCATED` IS A CLAIM ABOUT A GAP, SO IT REQUIRES HAVING LOOKED. When
    # the live half is unmeasured, "nothing located" means "no transcript, and
    # the window is unknown" — indeterminate, not a proven gap. Firing exit 6
    # there made the machine-readable surface assert what the human-readable
    # one had just disclaimed, which is the worse half to get wrong.
    if measured and not any(s.located for s in report.sessions):
        report.state = WHO_UNLOCATED
    return report


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def render(report: WhoReport) -> str:
    """Human output. Every absence is spelled, never left blank."""
    lines: list[str] = []
    head = f"task {report.task}"
    if report.title:
        head += f" — {report.title}"
    if report.status:
        head += f"  [{report.status}]"
    lines.append(head)

    if report.state == WHO_BAD_TASK_ID:
        lines.append(f"  🔴 {WHO_BAD_TASK_ID} — {'; '.join(report.notes)}")
        lines.append("     clawgate answered; it refused the id. Check the id, "
                     "not the network.")
        return "\n".join(lines)
    if report.state == WHO_NO_TASK:
        lines.append(f"  🔴 {WHO_NO_TASK} — {'; '.join(report.notes)}")
        return "\n".join(lines)
    if report.state == WHO_NO_CLAWGATE:
        lines.append(f"  🔴 {WHO_NO_CLAWGATE} — {'; '.join(report.notes)}")
        lines.append("     This is NOT 'the task has no sessions' — nothing was asked.")
        return "\n".join(lines)
    if report.state == WHO_NO_SESSIONS:
        lines.append("  no session is recorded against this task.")
        lines.append("  That is a real state (a task filed in the UI has none), "
                     "not a lookup failure.")
        return "\n".join(lines)

    if report.windows_reason:
        lines.append(f"  🔴 LIVE WINDOWS UNMEASURED — {report.windows_reason}")
        lines.append("     'no window' below would be unproven, so it is not claimed.")

    for s in report.sessions:
        role = f" ({s.role})" if s.role else ""
        where = f" in {s.project}" if s.project else ""
        lines.append(f"\n  session {s.session_id}{role}{where}")
        if s.last_seen:
            lines.append(f"    last seen   {s.last_seen}")

        if not s.windows_measured:
            lines.append("    window      UNMEASURED — not looked up on this run")
        elif s.window is None:
            lines.append("    window      none live — the session is not open in tmux now")
        else:
            w = s.window
            extra = []
            if w.codename:
                extra.append(w.codename)
            if w.hotkey:
                extra.append(w.hotkey)
            tag = f"  ({', '.join(extra)})" if extra else ""
            lines.append(f"    window      {w.host} {w.address}{tag}")
            ids = " ".join(x for x in (w.window_id, w.pane_id) if x)
            if ids:
                lines.append(f"                ids {ids}")
            if w.path:
                lines.append(f"                cwd {w.path}")

        if s.transcript:
            lines.append(f"    transcript  {s.transcript}")
        else:
            lines.append("    transcript  not found on this host")

    for note in report.notes:
        lines.append(f"  note: {note}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="cairn who",
        description="Resolve a task to its sessions, tmux windows and transcripts.",
    )
    ap.add_argument("task")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--host", default=None, choices=["workbench", "laptop"])
    ap.add_argument("--no-windows", action="store_true",
                    help="skip the tmux scan; report only the durable transcripts")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = ap.parse_args(argv)

    report = resolve(args.task, timeout=args.timeout, host=args.host,
                     skip_windows=args.no_windows)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(render(report))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
