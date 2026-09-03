#!/usr/bin/env python3
"""`cairn doctor` — one call that answers what a reader otherwise checks by hand.

🔴 WHY THIS EXISTS. Every fact below was previously established by a human
running four or five commands and holding the results in their head: is the pod
up, is my token any good, is my cache stamped, does my cache agree with the pod,
is anything on this disk invisible to my credential, and does this host's READER
resolve to the synced cache or to the frozen pre-cutover mirror. Nothing joined
them, so the answers were assembled differently every time and the joins that
matter — cache count vs pod count, local scopes vs visible scopes — were the
ones nobody made.

🔴 FOUR STATES, AND THE FOURTH IS THE WHOLE POINT. This subsystem's recurring
defect is a reassuring zero: an output that cannot distinguish "there is nothing
there" from "I could not look". So every check reports one of

    OK              measured, and the answer is fine
    PROBLEM         measured, and the answer is not fine
    UNMEASURED      I tried and could not — the reason is mandatory
    NOT-OBSERVABLE  this cannot be answered from a client AT ALL, structurally,
                    and the detail names who can answer it and how

`NOT-OBSERVABLE` is separate from `UNMEASURED` on purpose. Folding the two would
make the exit code permanently non-zero — the deployed API exposes no identity
route and never will from here — and `claude/RULES.md` says a permanently-red
gate is worse than no gate because it trains everyone to click through.

🔴 DOCTOR NEVER INSTALLS A SNAPSHOT. It fetches, reads the headers and the member
list, and throws the bytes away. A diagnostic that repaired the cache as a side
effect would destroy the staleness it was run to measure — and it would be the
one command you must not run twice.

🔴 THE HOLE IT CANNOT CLOSE, STATED RATHER THAN LEFT TO BE FOUND. A scope this
host holds but the pod's snapshot does not contain is EITHER a scope the store
has never held OR a scope outside this token's allowlist. The API answers those
two identically **by design** — `server.py` closes an enumeration oracle by
making a refused scope byte-identical to an absent one — so no client can tell
them apart, and this module does not pretend to. It reports the set and names
both readings.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "OK",
    "PROBLEM",
    "UNMEASURED",
    "NOT_OBSERVABLE",
    "STATES",
    "TOKEN_FINGERPRINT_CHARS",
    "Check",
    "PodFacts",
    "token_fingerprint",
    "store_entry_files",
    "store_scopes",
    "writable_entry_files",
    "collect",
    "render",
    "exit_code",
]

#: The four verdicts a check may carry. Written out once; `render` and
#: `exit_code` both read this tuple rather than restating the list.
OK = "OK"
PROBLEM = "PROBLEM"
UNMEASURED = "UNMEASURED"
NOT_OBSERVABLE = "NOT-OBSERVABLE"
STATES: tuple[str, ...] = (OK, PROBLEM, UNMEASURED, NOT_OBSERVABLE)

#: How many hex characters of `sha256(token)` identify a credential.
#:
#: 🔴 THIS MIRRORS `subsystem-store-api/server.py::token_id`, WHICH IS WHAT THE
#: POD'S AUDIT LOG CARRIES. The value is what makes the fingerprint printed here
#: matchable against a `token=<id>` line in the pod's log, so the two agreeing is
#: a wire fact and not an implementation detail. `cairn` cannot import
#: `server.py` — that module inserts paths, pulls in the whole reader and exists
#: to be run as a daemon — so the rule is spelled twice and pinned once:
#: `test_cairn_doctor.py::test_the_fingerprint_is_the_SERVERS_token_id` imports
#: both and requires they agree on a fixture whose expected digest is written out
#: as a literal in the test.
TOKEN_FINGERPRINT_CHARS = 12


def token_fingerprint(token: str) -> str:
    """A stable, non-reversible handle for a credential. NEVER the token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:TOKEN_FINGERPRINT_CHARS]


@dataclass(frozen=True)
class Check:
    """One diagnosed fact.

    `detail` is mandatory and is not decoration: for `UNMEASURED` it carries the
    reason, and for `NOT-OBSERVABLE` it carries the command that CAN answer it.
    A state with an empty detail is refused at construction, because a bare
    `UNMEASURED` is the reassuring zero wearing a different word.
    """

    name: str
    state: str
    detail: str

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"unknown check state {self.state!r}; expected one of {STATES}")
        if not self.detail.strip():
            raise ValueError(
                f"check {self.name!r} has an empty detail — a state with no "
                f"reason cannot be acted on"
            )


@dataclass(frozen=True)
class PodFacts:
    """What one non-installing snapshot fetch established, or why it did not.

    🔴 `reached` IS NOT DERIVED FROM THE COUNTS. A store that genuinely holds
    zero entries and a fetch that never happened both leave the counts at 0, so
    the counts may only be read when `reached` is True — and every consumer below
    branches on `reached` first.
    """

    reached: bool
    #: Set when `reached` is False. Mandatory in that case.
    reason: str = ""
    #: The HTTP status when the server ANSWERED but refused. `None` when there
    #: was no answer at all, which is what separates unauthorised from
    #: unreachable without parsing a message.
    http_status: int | None = None
    #: `X-Store-Entries` — what THIS token's snapshot contained.
    visible_entries: int | None = None
    #: `entry-files=` out of `X-Store-Snapshot` — the STORE-WIDE total, which the
    #: server emits unfiltered (a documented, deliberate residual count leak).
    store_wide_entries: int | None = None
    #: Scope directory names present in the fetched archive.
    visible_scopes: tuple[str, ...] = ()
    #: The raw `X-Store-Snapshot` header, relayed verbatim.
    snapshot_header: str = ""


# --------------------------------------------------------------------------- #
# Disk facts. Each one is a plain function so a test can drive it directly.
# --------------------------------------------------------------------------- #

def _scope_dirs(root: Path) -> list[Path]:
    """`<root>/<scope>/` directories, dot-directories excluded.

    Raises `OSError` rather than returning `[]` on an unreadable root: an empty
    list and an unreadable directory are the two things this whole module exists
    to keep apart, so the failure has to reach a caller that can name it.
    """
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def store_scopes(root: Path) -> tuple[str, ...]:
    """The scope names a store root holds."""
    return tuple(p.name for p in _scope_dirs(root))


def store_entry_files(root: Path) -> int:
    """`<scope>/<entry>.md` files under a store root.

    The SAME shape `server.snapshot_freshness` counts — depth 2, `*.md`, no
    dot-directories and no dot-files — so this number and the pod's
    `entry-files=` are answers to one question rather than two.
    """
    total = 0
    for scope in _scope_dirs(root):
        for entry in scope.iterdir():
            if entry.is_file() and entry.name.endswith(".md") and not entry.name.startswith("."):
                total += 1
    return total


def writable_entry_files(root: Path) -> tuple[str, ...]:
    """Entry files under `root` that any mode bit still allows WRITING.

    🔴 THE FREEZE IS A PROPERTY OF EVERY FILE, NOT OF THE DIRECTORY. The cutover
    chmods the pre-cutover mirror read-only so nothing can write to a store that
    no longer feeds anybody. A partially-frozen mirror still accepts a write,
    and that write then lives on one host and is invisible to the pod — the
    exact stranding the cutover exists to prevent. So this counts FILES, and a
    non-empty answer is a PROBLEM even when the directory looks frozen.
    """
    loose: list[str] = []
    for scope in _scope_dirs(root):
        for entry in sorted(scope.iterdir()):
            if not (entry.is_file() and entry.name.endswith(".md")):
                continue
            if entry.stat().st_mode & 0o222:
                loose.append(f"{scope.name}/{entry.name}")
    return tuple(loose)


@dataclass(frozen=True)
class Reading:
    """One disk fact, or the structured reason there is none.

    🔴 `absent` IS A FLAG, NOT A SENTENCE. The first version had callers ask
    `reason.endswith("does not exist")` to tell a missing directory from an
    unreadable one — a guard SPELLED rather than STRUCTURAL, which `claude/RULES.md`
    names directly: reword the message and the branch silently stops firing, with
    an unreadable mirror then reported as "nothing pre-cutover on this host".
    """

    value: object | None
    reason: str
    absent: bool

    @property
    def ok(self) -> bool:
        return self.reason == ""


def _describe(root: Path, what: Callable[[Path], object]) -> Reading:
    """Read one fact off disk, or say why not. Never a silent zero."""
    try:
        return Reading(what(root), "", absent=False)
    except FileNotFoundError:
        return Reading(None, f"{root} does not exist", absent=True)
    except OSError as exc:
        return Reading(None, f"{root} could not be read: {exc}", absent=False)


# --------------------------------------------------------------------------- #
# The checks
# --------------------------------------------------------------------------- #

def collect(
    *,
    resolved_root: Path,
    stamp_lines: Iterable[str] | None,
    stamp_reason: str | None,
    cache_root: Path,
    mirror_root: Path,
    pod: PodFacts,
    token: str | None,
    token_reason: str = "",
    identity_remedy: str,
) -> list[Check]:
    """Every check, in the order a reader should read them.

    Takes FACTS, not sources: the caller does the network and the config load, so
    every branch here is reachable from a test with no server, no `$HOME` and no
    clock.
    """
    checks: list[Check] = []

    # 1. WHICH DIRECTORY does this host's reader resolve, and can it date itself?
    #    This is `subsystem_read_store`'s question, asked out loud.
    if stamp_lines is not None:
        checks.append(Check(
            "reader-resolution",
            OK,
            f"the reader resolves {resolved_root}, which carries a sync stamp",
        ))
    else:
        checks.append(Check(
            "reader-resolution",
            PROBLEM,
            f"the reader resolves {resolved_root}, which cannot date itself — "
            f"{stamp_reason}. A read against it refuses (exit 4, the READER's "
            f"EXIT_UNSTAMPED_READ_STORE). Fix: `cairn sync`.",
        ))

    # 2. THE STAMP'S OWN FIELDS, relayed unparsed.
    if stamp_lines is None:
        checks.append(Check(
            "cache-stamp", PROBLEM,
            f"no readable stamp in {resolved_root} — {stamp_reason}",
        ))
    else:
        rendered = "; ".join(stamp_lines)
        checks.append(Check("cache-stamp", OK, rendered or "(the stamp is empty)"))

    # 3. THE FROZEN MIRROR. Absent is fine — a fresh host never had one. Present
    #    and fully read-only is fine. Present with a WRITABLE entry is not.
    mirror = _describe(mirror_root, writable_entry_files)
    loose = mirror.value
    if not mirror.ok:
        if mirror.absent:
            checks.append(Check(
                "frozen-mirror", OK,
                f"{mirror_root} does not exist — nothing pre-cutover on this host",
            ))
        else:
            checks.append(Check("frozen-mirror", UNMEASURED, mirror.reason))
    elif loose:
        shown = ", ".join(list(loose)[:5]) + ("…" if len(loose) > 5 else "")
        checks.append(Check(
            "frozen-mirror", PROBLEM,
            f"{len(loose)} entry file(s) under {mirror_root} are still WRITABLE "
            f"({shown}). The mirror is frozen so nothing writes to a store the "
            f"pod does not read; a write to one of these lives on this host "
            f"only. Re-run `cairn-cutover.py --freeze --apply`.",
        ))
    else:
        checks.append(Check(
            "frozen-mirror", OK,
            f"every entry file under {mirror_root} is read-only",
        ))

    # 4. THE POD. Unreachable, unauthorised and refused are three answers.
    if pod.reached:
        checks.append(Check(
            "pod", OK, f"answered a snapshot request — {pod.snapshot_header or 'no stamp header'}",
        ))
    elif pod.http_status in (401, 403):
        checks.append(Check(
            "pod", PROBLEM,
            f"the store ANSWERED and refused this credential (HTTP "
            f"{pod.http_status}) — {pod.reason}. This is NOT an outage: the host "
            f"is up. A 403 can also be the edge refusing the User-Agent rather "
            f"than the token being wrong.",
        ))
    elif pod.http_status is not None:
        checks.append(Check(
            "pod", PROBLEM,
            f"the store ANSWERED HTTP {pod.http_status} — {pod.reason}",
        ))
    else:
        checks.append(Check(
            "pod", UNMEASURED,
            f"no answer from the store — {pod.reason}. Nothing below that needs "
            f"the pod could be measured.",
        ))

    # 5. CACHE vs POD. Only readable once the pod answered; otherwise UNMEASURED
    #    with the reason, never a zero.
    cache = _describe(cache_root, store_entry_files)
    cached = cache.value
    if not pod.reached:
        checks.append(Check(
            "cache-vs-pod", UNMEASURED,
            f"the pod was not reached ({pod.reason}), so the cache's "
            f"{cached if cache.ok else 'unreadable'} entry file(s) "
            f"could not be compared against anything",
        ))
    elif not cache.ok:
        checks.append(Check("cache-vs-pod", UNMEASURED, cache.reason))
    elif pod.visible_entries is None:
        checks.append(Check(
            "cache-vs-pod", UNMEASURED,
            "the store answered without an `X-Store-Entries` header, so its own "
            "count of what it sent is unknown",
        ))
    elif cached == pod.visible_entries:
        checks.append(Check(
            "cache-vs-pod", OK,
            f"{cached} entry file(s) here, {pod.visible_entries} in the store's "
            f"answer to this token — they agree",
        ))
    else:
        checks.append(Check(
            "cache-vs-pod", PROBLEM,
            f"{cached} entry file(s) here but {pod.visible_entries} in the "
            f"store's answer to this token. Fix: `cairn sync`.",
        ))

    # 6. WHAT THIS TOKEN CANNOT SEE. Two independent readings, both reported.
    checks.append(_visibility_check(pod, cache_root, mirror_root))

    # 7. THE CREDENTIAL. A fingerprint is measurable here; the IDENTITY behind it
    #    is not, from any client, and says so rather than going quiet.
    if token is None:
        checks.append(Check(
            "token", PROBLEM,
            token_reason or "no token is configured, so no request can be authenticated",
        ))
    else:
        checks.append(Check(
            "token", NOT_OBSERVABLE,
            f"fingerprint {token_fingerprint(token)} (sha256[:{TOKEN_FINGERPRINT_CHARS}], "
            f"the handle the pod's audit log carries). The IDENTITY and the "
            f"declared scope allowlist behind it live in the pod's token file and "
            f"the API exposes no route that returns them — see the scope check "
            f"above for what this credential can actually reach. To read the "
            f"declared row: {identity_remedy}",
        ))

    return checks


def _visibility_check(pod: PodFacts, cache_root: Path, mirror_root: Path) -> Check:
    """Scopes and entries on this disk that the store's answer did not carry."""
    if not pod.reached:
        return Check(
            "token-scopes", UNMEASURED,
            f"the pod was not reached ({pod.reason}), so nothing is known about "
            f"which scopes this credential can reach",
        )

    local: set[str] = set()
    unread: list[str] = []
    for root in (cache_root, mirror_root):
        reading = _describe(root, store_scopes)
        if not reading.ok:
            # An ABSENT root contributes nothing and is not a failure; an
            # UNREADABLE one is a hole in this check's own coverage and is named
            # in the detail, so a clean-looking answer cannot come from a walk
            # that could not see half its input.
            if not reading.absent:
                unread.append(reading.reason)
            continue
        local |= set(reading.value)  # type: ignore[arg-type]

    visible = set(pod.visible_scopes)
    missing = sorted(local - visible)

    hidden_entries = ""
    if pod.store_wide_entries is not None and pod.visible_entries is not None:
        gap = pod.store_wide_entries - pod.visible_entries
        if gap > 0:
            hidden_entries = (
                f" The store reports {pod.store_wide_entries} entry file(s) "
                f"store-wide but sent this token {pod.visible_entries}, so "
                f"{gap} live in scopes this credential cannot reach."
            )

    unread_note = (" Some local roots were unreadable: " + "; ".join(unread)) if unread else ""

    if not missing and not hidden_entries:
        return Check(
            "token-scopes", OK,
            f"this credential reaches all {len(visible)} scope(s) the store sent, "
            f"and every scope on this disk is among them.{unread_note}",
        )
    if not missing:
        return Check("token-scopes", PROBLEM, hidden_entries.strip() + unread_note)
    return Check(
        "token-scopes", PROBLEM,
        f"{len(missing)} scope(s) exist on this disk and are NOT in the store's "
        f"answer to this token: {', '.join(missing)}. The API cannot tell you "
        f"which of the two this is — a refused scope is byte-identical to one the "
        f"store has never held, deliberately, so that an error cannot enumerate "
        f"the store. Both readings are actionable: either seed the scope, or add "
        f"it to this token's allowlist.{hidden_entries}{unread_note}",
    )


# --------------------------------------------------------------------------- #
# Rendering and the verdict
# --------------------------------------------------------------------------- #

#: 🔴 THE EXIT CODES, OWNED BY THE COMMAND AND DOCUMENTED BY IT. `render` prints
#: this legend on every run, so a caller never has to find a skill to learn what
#: a number meant. They are disjoint from every other `cairn` code: 0/3/4/5 are
#: read outcomes, 6/7/8 are write outcomes, 2 is usage.
EXIT_DOCTOR_OK = 0
EXIT_DOCTOR_PROBLEM = 9
EXIT_DOCTOR_UNMEASURED = 10

EXIT_LEGEND: tuple[tuple[int, str], ...] = (
    (EXIT_DOCTOR_OK, "every check measured, and every answer is fine"),
    (EXIT_DOCTOR_PROBLEM, "at least one check MEASURED a problem"),
    (EXIT_DOCTOR_UNMEASURED,
     "no problem measured, but at least one check COULD NOT LOOK — "
     "this is not a clean bill of health"),
)


def exit_code(checks: Iterable[Check]) -> int:
    """PROBLEM outranks UNMEASURED outranks OK.

    🔴 `NOT-OBSERVABLE` CONTRIBUTES NOTHING, and that is deliberate. It marks a
    fact no client can reach, so escalating on it would make this command
    non-zero on every healthy run forever — the permanently-red gate
    `claude/RULES.md` says trains everyone to click through.
    """
    states = {c.state for c in checks}
    if PROBLEM in states:
        return EXIT_DOCTOR_PROBLEM
    if UNMEASURED in states:
        return EXIT_DOCTOR_UNMEASURED
    return EXIT_DOCTOR_OK


_MARKER = {OK: "  ", PROBLEM: "🔴", UNMEASURED: "⚠ ", NOT_OBSERVABLE: "· "}


def render(checks: list[Check]) -> str:
    """The report, plus the exit legend, plus a one-line verdict."""
    width = max((len(c.name) for c in checks), default=0)
    lines = [f"{_MARKER[c.state]} {c.name.ljust(width)}  {c.state:<14} {c.detail}"
             for c in checks]
    code = exit_code(checks)
    counts = {s: sum(1 for c in checks if c.state == s) for s in STATES}
    lines.append("")
    lines.append(
        "checks: "
        + "  ".join(f"{s}={counts[s]}" for s in STATES)
        + f"   -> exit {code}"
    )
    lines.append("exit codes: " + "; ".join(f"{n} = {why}" for n, why in EXIT_LEGEND))
    return "\n".join(lines)


def to_dict(checks: list[Check]) -> dict:
    return {
        "checks": [{"name": c.name, "state": c.state, "detail": c.detail} for c in checks],
        "counts": {s: sum(1 for c in checks if c.state == s) for s in STATES},
        "exit": exit_code(checks),
        "exit_legend": {str(n): why for n, why in EXIT_LEGEND},
    }
