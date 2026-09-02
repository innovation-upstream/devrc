#!/usr/bin/env python3
"""Which directory does a HOST-LOCAL read surface read, and can it say how fresh it is?

🔴 ONE RULE, ONE PLACE — "where does this host read the subsystem store from?".

Before the Cairn cutover there was one answer and every reader baked it in:
`subsystem_touch.DEFAULT_STORE_ROOT` (`~/.claude/analyze-service-index`), which was
both the write target and the read target. The cutover made a hosted pod the
canonical datastore, FROZE that directory (entry files `0444`, nothing refreshes
it) and introduced a synced read-through cache that `cairn sync` / `cairn recall`
maintain. The read path was not repointed, so two host-local read surfaces —
`subsystem_recall.py`'s CLI and `service_recon.py`'s recon — went on reading the
frozen copy. MEASURED 2026-09-02 on the workbench: the frozen mirror served 26
`devrc/` entries and the cache 29, and the frozen one printed
"ALL 26 entries in `devrc/`, none omitted" — a completeness claim about a store
that had stopped moving the day before, with nothing in the output saying so.

**The discriminator is the stamp, not the path.** `cairn sync` writes
`.sync-stamp` into the cache; the frozen mirror has none and never will. So this
module answers the question with a FACT read off disk rather than with a second
hardcoded path: a store that carries a stamp can state its own freshness, and one
that cannot must not be served silently — which is exactly how the frozen mirror
spent a day masquerading as current.

🔴 REFUSE, DO NOT FALL BACK. Falling back to the frozen mirror when the cache is
unstamped would reinstate the defect under a nicer name: the caller would still
get an index, still get a completeness claim, and still have no way to tell. The
refusal names a one-command remedy (`cairn sync`) instead.

🔴 NO CLOCK. This module reads a file and returns its lines UNINTERPRETED — see
`read_stamp` for the one normalisation it does apply. It does NOT
compute an age — `subsystem_recall` documents itself "READ-ONLY. No clock, no
network, no git, no prompt" and its consumers depend on that. `cairn.cache_age`
owns age computation and is the only place that should.

The constants below have exactly ONE definition each. `scripts/cairn` imports
them from here; it used to declare them, and a second copy in a reader would
disagree with the writer the first time either moved.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DEFAULT_CACHE_ROOT",
    "SYNC_STAMP",
    "STAMP_PREFIX",
    "ReadStore",
    "read_store_root",
    "read_stamp",
    "resolve_read_store",
    "stamp_header",
    "REMEDY",
    "refusal_message",
]

#: The synced read-through cache `cairn sync` installs. THE one definition.
DEFAULT_CACHE_ROOT = Path.home() / ".cache" / "subsystem-store"

#: The snapshot stamp `cairn sync` writes into the cache root. THE one definition.
SYNC_STAMP = ".sync-stamp"

#: The one-command remedy, spelled once so every refusal quotes the same thing.
REMEDY = "cairn sync"

#: How a rendered stamp line is introduced, wherever a reader sees one.
#:
#: 🔴 ONE SPELLING, TWO RENDERERS. `subsystem_recall`'s CLI header and
#: `service_recon`'s `index:` block both print the stamp; when the second was
#: added it open-coded the same f-string, which is the shape that regenerates the
#: same drift at every site. A reader (human or agent) is told to relay
#: "the `stamp:` line", so the token is a wire fact, not a formatting whim.
STAMP_PREFIX = "  stamp: "


@dataclass(frozen=True)
class ReadStore:
    """The resolved host-local read store, and whether it can date itself.

    `stamp` holds the stamp file's non-blank lines with TRAILING WHITESPACE
    STRIPPED, and nothing else done to them — no parsing, no reordering, no
    interpretation; a caller renders them. (This docstring said "VERBATIM" while
    the body called `rstrip()`; the fixture had no trailing whitespace, so no
    test could see the difference and the word was simply wrong. The strip stays
    — it is what keeps a `\\r` from a CRLF write out of the rendered header — and
    the sentence now describes it.)

    `reason` says why there is no stamp and is `None` exactly when `stamp` is not.
    """

    root: Path
    stamp: tuple[str, ...] | None
    reason: str | None

    @property
    def stamped(self) -> bool:
        return self.stamp is not None


def read_store_root() -> Path:
    """The host-local READ store root.

    Reads the module global at CALL time rather than closing over it, so a test
    (and only a test) can repoint the whole read path by assigning
    `subsystem_read_store.DEFAULT_CACHE_ROOT`. A `from … import DEFAULT_CACHE_ROOT`
    in a consumer would defeat that, which is why the consumers call this.
    """
    return DEFAULT_CACHE_ROOT


def read_stamp(root: str | Path) -> tuple[tuple[str, ...] | None, str | None]:
    """`(lines, reason-there-are-none)` — exactly one is not None.

    READ-ONLY and clock-free: it opens one file, splits it, drops blank lines and
    strips each line's TRAILING whitespace. That is the whole normalisation — the
    fields are neither parsed nor reordered, and this module does not own the
    stamp's schema. An unreadable or empty stamp is reported as ABSENT, never as
    a stamp with no fields: "the store is stamped" must not be satisfiable by a
    zero-byte file.
    """
    path = Path(root) / SYNC_STAMP
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, f"no `{SYNC_STAMP}` in {path.parent}"
    except OSError as exc:
        return None, f"`{path}` could not be read: {exc}"
    except UnicodeDecodeError as exc:
        return None, f"`{path}` is not text: {exc}"
    lines = tuple(line.rstrip() for line in text.splitlines() if line.strip())
    if not lines:
        return None, f"`{path}` is empty"
    return lines, None


def resolve_read_store(root: str | Path | None = None) -> ReadStore:
    """Resolve the read store and read its stamp in one call.

    `root=None` is the DEFAULT resolution — the synced cache. Passing a root
    explicitly is the operator naming a directory deliberately; this function
    still reports whether that directory is stamped, and leaves the decision of
    what to do about it to the caller, because "the default resolved somewhere
    undateable" and "you asked me to read this" warrant different answers.
    """
    resolved = Path(root) if root is not None else read_store_root()
    stamp, reason = read_stamp(resolved)
    return ReadStore(root=resolved, stamp=stamp, reason=reason)


def stamp_header(lines: Sequence[str] | None) -> tuple[str, ...]:
    """The stamp's lines as rendered header lines — the ONE spelling.

    Takes the lines rather than a `ReadStore` so a caller that carried the stamp
    through its own dataclass (`service_recon.Brief.store_stamp`) renders them
    identically to the one that still holds the `ReadStore`. Still clock-free and
    still unparsed: it prefixes, and does nothing else.
    """
    return tuple(f"{STAMP_PREFIX}{line}" for line in (lines or ()))


def refusal_message(prog: str, store: ReadStore) -> str:
    """The refusal, naming the store, the reason and the remedy.

    Deliberately does NOT name the frozen mirror's path: this module is a leaf
    and the mirror's path belongs to `subsystem_touch`. Naming it here would be a
    second spelling of somebody else's constant.
    """
    return (
        f"{prog}: REFUSING to read {store.root} — {store.reason}.\n"
        f"An unstamped store cannot say how fresh it is, and the pre-cutover store "
        f"on this host is FROZEN: serving it silently is how a stale index comes to "
        f"claim completeness. Run `{REMEDY}` and re-run, or pass `--store <path>` to "
        f"read a directory deliberately."
    )
