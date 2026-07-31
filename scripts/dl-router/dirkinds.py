"""Directory KINDS — is this directory keyed by subject, or by category?

The library is not purely subject-keyed. Some directories name a person or
group ("performer"); others collect unattributed material by topic
("category"). The two need opposite rules, and conflating them is what produced
the mislearning that motivated this module:

    performer   identity signals only. A Discord channel id, a forum thread
                slug, a subject name in the page title. NEVER a tag: a tag list
                on a forum page is section names and other posters' usernames,
                and learning those aliased unrelated content into a subject's
                directory at full confidence.
    category    a tag IS the legitimate signal, so tag -> directory aliases are
                allowed — site-scoped only, never global, and only from an
                explicit confirmation.
    unknown     never auto-files, and never learns a tag. Absence of a
                classification is not permission.

WHERE IT LIVES. The classification is a fact about the operator's private
library, so it is never committed: `~/.config/dl-router/dirs.toml`, in the same
directory as the rest of the host-specific config. The format is two arrays
because the review action is "move this line to the other list":

    performer = [
      "Ada Lovelace",
    ]
    category = [
      "Field Recordings",
    ]

`dl-route dirs classify` drafts that file from the live index with a best-guess
split and a reason on every line, so the operator corrects rather than types.

A SECOND, MACHINE-WRITTEN SOURCE exists: when a directory is created through
the picker the extension asks which kind it is, and that answer goes into the
SQLite store (`dir_kinds`), not into this file — appending to a human-edited
TOML would either destroy their comments or need a round-tripping writer.
`DirKinds.load` overlays the two with the human file winning, which is the
right precedence: the file is the thing they reviewed.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

from matcher import (
    KIND_CATEGORY, KIND_PERFORMER, KIND_UNKNOWN, KINDS, content_tokens,
    norm_key,
)

DIRS_FILE_ENV = "DL_ROUTER_DIRS_FILE"

# The generator's third list. It is DELIBERATELY not a kind: `DirKinds` ignores
# it, so everything parked there is unclassified and therefore asks. It exists
# so the draft can pre-sort the directories it IS sure about (a single word,
# digits, a shared vocabulary) without silently authorising the ones it is not.
# Folding the ambiguous ones into `category` instead was safe, but it threw away
# the whole labour saving for a library that is mostly people.
ASK_LIST = "ask"

# A word appearing in this many directory names is a taxonomy word, not a
# subject. Same threshold, same reasoning as matcher.CHROME_DIR_SPREAD.
SHARED_TOKEN_DIRS = 2
# A subject directory is a person's name: two or three words in practice.
NAME_MIN_TOKENS = 2
NAME_MAX_TOKENS = 3


def default_dirs_file(env=None) -> Path:
    env = os.environ if env is None else env
    if env.get(DIRS_FILE_ENV):
        return Path(env[DIRS_FILE_ENV])
    return Path.home() / ".config" / "dl-router" / "dirs.toml"


class DirKinds:
    """Resolved directory classification. Read-only, cheap to rebuild."""

    __slots__ = ("_by_key", "path", "present", "error")

    def __init__(self, mapping=None, *, path=None, present: bool = False,
                 error: str | None = None):
        self._by_key: dict = {}
        for name, kind in dict(mapping or {}).items():
            key = norm_key(name)
            if key and kind in KINDS:
                self._by_key[key] = kind
        self.path = Path(path) if path else None
        self.present = bool(present)
        self.error = error

    # --- loading ----------------------------------------------------------- #
    @staticmethod
    def load(path=None, *, overlay=None, env=None) -> "DirKinds":
        """Load the human file, overlaid on the machine-assigned kinds.

        A missing file is normal (every directory is then UNKNOWN, so nothing
        auto-files and nothing learns a tag — the safe end of the range). A
        MALFORMED file degrades the same way rather than raising: this is
        loaded on the /match path, and the sidecar failing a match is worse
        than every match asking.

        A malformed file FAILS CLOSED COMPLETELY — the picker overlay is
        dropped too. It used to seed `merged` from the overlay before parsing,
        so picker-assigned `performer` kinds survived a broken file and kept
        auto-filing while the operator believed their edit had disabled it. The
        overlay is machine state and syntactically fine, but the file is the
        authority over it, and "I cannot read the authority" is not a state in
        which to keep auto-filing.
        """
        path = Path(path) if path is not None else default_dirs_file(env)
        merged: dict = {}
        for name, kind in dict(overlay or {}).items():
            if kind in KINDS:
                merged[name] = kind
        if not path.exists():
            return DirKinds(merged, path=path, present=False)
        try:
            with open(path, "rb") as fh:
                raw = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            # Everything, including the overlay -- see the docstring.
            return DirKinds({}, path=path, present=True,
                            error=f"cannot read {path}: {exc}")
        errors = []
        seen: dict = {}
        for key in raw:
            if key not in (KIND_PERFORMER, KIND_CATEGORY, ASK_LIST):
                errors.append(f"unknown list {key!r} — expected "
                              f"{KIND_PERFORMER}, {KIND_CATEGORY} or "
                              f"{ASK_LIST}")
        for kind in (KIND_PERFORMER, KIND_CATEGORY):
            value = raw.get(kind, [])
            if not isinstance(value, list):
                errors.append(f"{kind} must be a list of directory names")
                continue
            for item in value:
                if not isinstance(item, str) or not item.strip():
                    errors.append(f"{kind}: entries must be non-empty strings")
                    continue
                key = norm_key(item)
                if not key:
                    continue
                if key in seen and seen[key] != kind:
                    # Listed as BOTH. Ambiguous, so it resolves to unknown —
                    # which asks — rather than to whichever list was read last.
                    errors.append(f"{item!r} is in both lists — treated as "
                                  "unclassified")
                    merged.pop(item, None)
                    seen[key] = None
                    continue
                seen[key] = kind
                merged[item] = kind
        for key, kind in seen.items():
            if kind is None:
                # Drop every spelling of an ambiguous directory.
                for name in [n for n in merged if norm_key(n) == key]:
                    merged.pop(name, None)
        return DirKinds(merged, path=path, present=True,
                        error="; ".join(errors) if errors else None)

    # --- lookups ----------------------------------------------------------- #
    def kind(self, name) -> str:
        return self._by_key.get(norm_key(name), KIND_UNKNOWN)

    def as_map(self) -> dict:
        """`{norm_key: kind}` — the shape `Matcher(dir_kinds=...)` wants."""
        return dict(self._by_key)

    def counts(self, names) -> dict:
        out = {KIND_PERFORMER: 0, KIND_CATEGORY: 0, KIND_UNKNOWN: 0}
        for name in names or ():
            out[self.kind(name)] += 1
        return out

    def unclassified(self, names) -> list:
        return [n for n in (names or ()) if self.kind(n) == KIND_UNKNOWN]


# --- the draft generator --------------------------------------------------- #
def _token_document_frequency(names) -> dict:
    freq: dict = {}
    for name in names:
        for tok in set(content_tokens(name)):
            freq[tok] = freq.get(tok, 0) + 1
    return freq


def guess_kind(name: str, freq: dict):
    """Best-guess kind for one directory, with the reason to print beside it.

    IT GUESSES THE ASKING SIDE. Without a vocabulary of names — which could
    never be committed to a public repo and would need endless maintenance —
    nothing distinguishes "Ada Lovelace" from "Field Recordings": two
    capitalised words either way. The first draft resolved that ambiguity
    towards `performer`, which is the AUTO-FILING side, so a skimmed review
    turned category directories into auto-filers. Its own docstring example,
    "Field Recordings", drafted as a performer.

    So the ambiguous case now lands in `category`, and the two failure modes
    are no longer symmetric: skim this draft and the worst outcome is that a
    performer directory keeps asking (mildly annoying, one line to move) rather
    than a category directory silently auto-filing (wrong files, discovered
    later, and the alias is learned too).

    Every line still carries its reason, so the review is "does that hold?"
    rather than "what even is this?".
    """
    toks = content_tokens(name)
    if not toks:
        return KIND_CATEGORY, "no word-like content"
    if any(any(ch.isdigit() for ch in t) for t in toks):
        return KIND_CATEGORY, "contains digits"
    if len(toks) < NAME_MIN_TOKENS:
        return KIND_CATEGORY, "a single word"
    if len(toks) > NAME_MAX_TOKENS:
        return KIND_CATEGORY, f"{len(toks)} words — long for a name"
    if all(freq.get(t, 0) >= SHARED_TOKEN_DIRS for t in toks):
        return KIND_CATEGORY, "every word is shared with other directories"
    return ASK_LIST, f"{len(toks)} words — could be either; you decide"


_TOML_ESCAPES = {
    "\\": "\\\\", '"': '\\"', "\b": "\\b", "\f": "\\f", "\n": "\\n",
    "\r": "\\r", "\t": "\\t",
}


def toml_string(value: str) -> str:
    """A TOML basic string. Escapes everything TOML requires escaped."""
    out = []
    for ch in str(value):
        if ch in _TOML_ESCAPES:
            out.append(_TOML_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append("\\u%04X" % ord(ch))
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


HEADER = """\
# dl-router — directory kinds.  DRAFT: review before use.
#
# Save as ~/.config/dl-router/dirs.toml (or $DL_ROUTER_DIRS_FILE).  This file is
# NEVER committed: it describes a private library.
#
# The only review action is to move a line between the lists.  The comment after
# each line is why the generator put it there.
#
# THE GENERATOR ONLY SORTS WHAT IT CAN PROVE -- a single word, digits, a
# vocabulary shared with other directories.  Nothing available to it
# distinguishes "Ada Lovelace" from "Field Recordings" (two capitalised words
# either way), so those go to `ask` rather than being guessed onto the
# auto-filing side.
#
#   performer  filed by subject identity.  MAY auto-file.  Learns only identity
#              signals — a Discord channel id, a forum thread slug, a subject
#              name in the page title.  Never a tag.
#   category   filed by topic.  ALWAYS opens the picker, whatever it scores.
#              Learns tag -> directory aliases, site-scoped only, and only from
#              an explicit confirmation.
#
#   ask        NOT A KIND -- this list is IGNORED, so everything in it is
#              unclassified and therefore asks.  EMPTY IT as you review: move
#              each line into `performer` or `category`.
#
# A directory in NONE of the lists is unclassified too: it never auto-files and
# never learns a tag.  That is the safe end of the range, not a bug.
"""


def draft(dir_names, *, known=None) -> str:
    """The draft TOML for `dir_names`, best-guess split, reason per line.

    `known` (an existing DirKinds) is honoured over the guess, so re-running
    the generator after a partial review does not undo it.
    """
    names = sorted({str(n) for n in (dir_names or ()) if str(n).strip()})
    freq = _token_document_frequency(names)
    buckets = {KIND_PERFORMER: [], KIND_CATEGORY: [], ASK_LIST: []}
    for name in names:
        settled = known.kind(name) if known is not None else KIND_UNKNOWN
        if settled in KINDS:
            buckets[settled].append((name, "already classified"))
            continue
        kind, why = guess_kind(name, freq)
        buckets[kind].append((name, why))

    lines = [HEADER]
    for kind in (KIND_PERFORMER, KIND_CATEGORY, ASK_LIST):
        rows = buckets[kind]
        lines.append(f"\n{kind} = [")
        if not rows:
            lines.append("  # (none — move directories here from another list)")
        width = max((len(toml_string(n)) for n, _ in rows), default=0)
        for name, why in rows:
            literal = toml_string(name) + ","
            lines.append(f"  {literal.ljust(width + 1)}  # {why}")
        lines.append("]")
    return "\n".join(lines) + "\n"
