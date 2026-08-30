#!/usr/bin/env python3
"""Resolve `--mention` arguments into Signal wire mentions. PURE — no I/O.

WHAT A MENTION IS ON THE WIRE (measured against the deployed signal-cli 0.14.7,
`data.MessageMention`, not recalled):

    {"author": "<E.164 or bare UUID>", "start": <int>, "length": <int>}

exactly those three keys.

  * `author` accepts EITHER an E.164 number OR a bare UUID
    (`RecipientIdentifier.Single.fromString` takes both).
  * `start`/`length` are **UTF-16 code units**, NOT Python code points and NOT
    bytes. A non-BMP character — every emoji outside the BMP, and there are many
    in a real Signal thread — is ONE Python code point and TWO UTF-16 units. A
    naive `str.find()` / `len()` therefore produces offsets that are correct for
    plain ASCII and silently WRONG the moment anyone puts an emoji before the
    mention: the receiving client replaces the wrong span of text.
  * the `[start, start+length)` span must ALREADY EXIST in `message`; the
    receiving client REPLACES it with `@DisplayName`. So the body must contain
    the literal `@<identifier>` we measured, and we send both together.

🔴 WHY EVERY FAILURE HERE IS A REFUSAL, NEVER A DROP. A mention pushes a
notification through the recipient's mute settings and names a third party in a
group. Dropping an unresolvable one sends a message that reads as if it pinged
someone and did not — the operator approved a card that described a different
act from the one performed. Each refusal below is its own exception class AND
its own message, so a caller (and a test) can assert WHICH guard fired rather
than accepting any exception.
"""
from __future__ import annotations

import re

# A Signal recipient identifier typed directly rather than by display name.
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")

# The exact key set of one wire mention. Asserted by the suite; a fourth key
# would be silently dropped by the server, and a missing one mis-renders.
MENTION_KEYS = ("author", "start", "length")


class MentionError(ValueError):
    """Base: a `--mention` could not be turned into a wire mention.

    A `ValueError` so `consumer.main()`'s existing `draft` handler — which
    already catches `ValueError` and exits 3 — refuses cleanly rather than
    escaping as a traceback.
    """


class MentionsRequireAGroup(MentionError):
    """Mentions were given for a recipient that is not a group."""


class MentionNameNotFound(MentionError):
    """No member of the target group answers to that display name."""


class MentionNameAmbiguous(MentionError):
    """More than one member of the target group answers to that display name."""


class MentionResolvesToPlaceholder(MentionError):
    """The name resolved to a PLACEHOLDER contact — a synthetic identity.

    `_signal_db.placeholder_uuid()` mints deterministic FAKE uuids for senders
    the pipeline could not identify. Such a uuid is not a Signal identity; put
    on the wire as an `author` it addresses nobody, and the send either errors
    or — worse — succeeds with a mention pointing at no one.
    """


class MentionNotAMember(MentionError):
    """An explicitly-typed uuid/E.164 is not in the target group's membership."""


class MentionSpanMissing(MentionError):
    """The body does not contain the `@<identifier>` text this mention covers."""


# --------------------------------------------------------------------------- #
# UTF-16 offsets — the whole point
# --------------------------------------------------------------------------- #
def utf16_len(text: str) -> int:
    """Length of `text` in UTF-16 code units.

    `len(text)` counts CODE POINTS and is equal to this only while every
    character is in the BMP. Encoding to `utf-16-le` (no BOM — `utf-16` would
    prepend two bytes and inflate every answer by one unit) and halving the byte
    count is the definition, not an approximation.
    """
    return len(text.encode("utf-16-le")) // 2


def utf16_span(body: str, needle: str, *, from_index: int = 0) -> tuple[int, int]:
    """`(start, length)` of `needle` in `body`, in UTF-16 code units.

    `from_index` is a CODE POINT index into `body` — the search cursor, used so
    two mentions of the same identifier take successive occurrences instead of
    both claiming the first. Raises `MentionSpanMissing` if the needle is not
    present at or after it.
    """
    idx = body.find(needle, from_index)
    if idx < 0:
        raise MentionSpanMissing(
            f"the draft body does not contain {needle!r}"
            + (f" at or after character {from_index}" if from_index else "")
            + ". A Signal mention REPLACES an existing span of the message text, "
              "so the text has to be there — add it to --body, or drop the "
              "--mention."
        )
    return utf16_len(body[:idx]), utf16_len(needle)


# --------------------------------------------------------------------------- #
# Identity helpers
# --------------------------------------------------------------------------- #
def looks_like_author_id(value: str) -> bool:
    """True for a value that is ALREADY a Signal identifier (uuid or E.164)."""
    v = (value or "").strip()
    return bool(_UUID_RE.match(v) or _E164_RE.match(v))


def _norm_member(value) -> str:
    """Members arrive MIXED — E.164 and bare UUID, and uuids in either case."""
    return str(value or "").strip().lower()


def _contact_ids(contact: dict) -> set[str]:
    return {_norm_member(contact.get("signal_uuid")),
            _norm_member(contact.get("phone_number"))} - {""}


def _contact_author(contact: dict) -> str:
    """The id to put on the wire. A uuid is preferred: it is stable, a number is not."""
    return (contact.get("signal_uuid") or contact.get("phone_number") or "")


def _contact_names(contact: dict) -> set[str]:
    return {str(n).strip().lower()
            for n in (contact.get("display_name"), contact.get("profile_name"))
            if str(n or "").strip()}


# --------------------------------------------------------------------------- #
# The resolver
# --------------------------------------------------------------------------- #
def resolve_mentions(identifiers, *, body: str, members, contacts,
                     is_group: bool = True) -> list[dict]:
    """`--mention` values → the wire `mentions` array, or raise.

    PURE. `members` is what `GET /v1/groups/<number>/<id>` returned (a mixed list
    of E.164 and bare UUID strings) and `contacts` is the matching
    `signal.contacts` rows; neither is fetched here, so every refusal below is
    reachable from a unit test with no network and no database.

    Returns `[]` for no identifiers — including for a non-group recipient, which
    is only refused when mentions were actually asked for.
    """
    idents = [str(i) for i in (identifiers or [])]
    if not idents:
        return []
    if not is_group:
        raise MentionsRequireAGroup(
            "mentions are a GROUP feature: a mention names a member of the "
            "conversation and notifies them through their mute settings. "
            f"--to is not a group address, so {idents!r} cannot be resolved. "
            "Drop --mention, or address the group."
        )

    member_set = {_norm_member(m) for m in (members or [])} - {""}
    if not member_set:
        raise MentionNameNotFound(
            "the group reported NO members, so no --mention can be resolved. "
            "This is a refusal rather than an empty mentions array on purpose: "
            "an empty membership means the lookup failed, not that the group is "
            "empty."
        )
    # Only contacts that are ACTUALLY in this group are candidates. A name that
    # matches somebody in another conversation must not resolve here.
    candidates = [c for c in (contacts or []) if _contact_ids(c) & member_set]

    out: list[dict] = []
    cursor_for: dict[str, int] = {}
    for ident in idents:
        author = _resolve_one(ident, member_set=member_set, candidates=candidates)
        needle = "@" + ident
        start, length = utf16_span(body or "", needle,
                                   from_index=cursor_for.get(needle, 0))
        # Advance THIS needle's cursor past the occurrence just claimed, so
        # `--mention Ann --mention Ann` takes the first and second "@Ann" rather
        # than pointing both mentions at the same span.
        cursor_for[needle] = (body or "").find(needle,
                                              cursor_for.get(needle, 0)) + len(needle)
        out.append({"author": author, "start": start, "length": length})
    return out


def _resolve_one(ident: str, *, member_set: set, candidates: list) -> str:
    """One `--mention` value → the `author` id, or raise the matching refusal."""
    ident = ident.strip()
    if looks_like_author_id(ident):
        if _norm_member(ident) not in member_set:
            raise MentionNotAMember(
                f"{ident!r} is a valid Signal identifier but is NOT a member of "
                f"the target group, so mentioning it would notify nobody. "
                f"The group's members are {sorted(member_set)!r}."
            )
        for contact in candidates:
            if _norm_member(ident) in _contact_ids(contact) \
                    and contact.get("is_placeholder"):
                raise MentionResolvesToPlaceholder(
                    f"{ident!r} resolves to a PLACEHOLDER contact — a synthetic "
                    f"identity minted for a sender this pipeline could not "
                    f"identify. It is not a real Signal id and must never reach "
                    f"the wire as a mention `author`."
                )
        return ident

    wanted = ident.lower()
    matched = [c for c in candidates if wanted in _contact_names(c)]
    # De-duplicate by IDENTITY, not by row: one person can legitimately have two
    # contact rows mid-`_promote_placeholder`, and that is not ambiguity.
    by_author = {}
    for contact in matched:
        by_author.setdefault(_contact_author(contact), contact)
    if not by_author:
        known = sorted({n for c in candidates for n in _contact_names(c)})
        raise MentionNameNotFound(
            f"no member of the target group is named {ident!r}. Known member "
            f"names are {known!r}. Pass a bare uuid or +E.164 instead if the "
            f"person has no stored name."
        )
    if len(by_author) > 1:
        raise MentionNameAmbiguous(
            f"{ident!r} is AMBIGUOUS — it matches {len(by_author)} members of "
            f"the target group ({sorted(by_author)!r}). Pass the bare uuid or "
            f"+E.164 of the one you mean."
        )
    author, contact = next(iter(by_author.items()))
    if contact.get("is_placeholder"):
        raise MentionResolvesToPlaceholder(
            f"{ident!r} resolves to a PLACEHOLDER contact ({author!r}) — a "
            f"synthetic identity minted for a sender this pipeline could not "
            f"identify. It is not a real Signal id and must never reach the wire "
            f"as a mention `author`."
        )
    if not author:
        raise MentionNameNotFound(
            f"{ident!r} matched a stored contact that carries neither a uuid nor "
            f"a phone number, so there is no `author` to send."
        )
    return author


def canonical_mentions(mentions) -> tuple:
    """A comparable, order-sensitive form of a mentions array.

    Used by the send-authorization binding: `None`, `[]` and a missing column
    must all compare EQUAL (they mean the same thing — no mentions), while any
    difference in author, offset or ORDER must compare unequal.
    """
    return tuple(
        (str(m.get("author")), int(m.get("start")), int(m.get("length")))
        for m in (mentions or [])
    )


def describe_mentions(mentions) -> list[str]:
    """Human lines for the clawgate card — WHO this message will notify."""
    return [f"{m['author']} (chars {m['start']}–{m['start'] + m['length']})"
            for m in (mentions or [])]
