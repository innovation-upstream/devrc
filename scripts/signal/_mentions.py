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

# How many member names a "no such name" refusal may print. See the comment at
# the raise site: `draft` needs no approval token, so an unbounded enumeration
# turns a refusal into a free membership oracle for any group address a caller
# can guess.
NAME_HINT_MAX = 5


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


class MentionSpansOverlap(MentionError):
    """Two mentions claim OVERLAPPING spans of the body.

    Signal REPLACES each `[start, start+length)` span with `@DisplayName` on the
    receiving client. Two spans that overlap are not two pings on two words —
    they are two rewrites of the SAME characters, and what the recipient sees is
    undefined. Refused rather than sent, for the same reason every other failure
    here is: the operator approved a card describing two distinct pings.
    """


class MentionGroupLookupFailed(MentionError):
    """The group-membership lookup itself failed, so nothing can be resolved.

    A `MentionError` (hence a `ValueError`) on purpose: `consumer.main()`'s
    `draft` handler catches `ValueError` and exits 3. Before this existed an
    HTTP error from `GET /v1/groups/<number>/<id>` — a 404 from an EMPTY
    `--from-number` being the ordinary case — escaped as a traceback and exit 1,
    which a caller cannot tell apart from the interpreter dying.
    """


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


def _needle_pattern(needle: str) -> "re.Pattern":
    """The ONE definition of "this body says `@who` here".

    🔴 TWO THINGS THE PLAIN `str.find()` GOT WRONG, both silent:

    * **NO BOUNDARY.** `find("@Ann")` matches INSIDE `"@Anna"`. With members
      `Ann` and `Anna` and a body `"hi @Anna and @Ann ok"`, `--mention Ann`
      produced span `(3, 4)` — Ann is pinged, Anna's name is the text on screen,
      and the render is corrupted. `(?!\\w)` requires a non-word character (or
      end of string) immediately after the needle, so a name that is a PREFIX of
      another member's name can no longer land on them. The scan CONTINUES past
      a boundary-failing hit rather than refusing, so the real `@Ann` later in
      that body is still found.
    * **CASE.** `_resolve_one()` matches names case-INSENSITIVELY (`.lower()`),
      so `--mention ANN` resolved to Ann and then died on `MentionSpanMissing`
      against a body reading `@Ann` — resolution and span search disagreed about
      what the same argument meant. `re.IGNORECASE` makes the two halves agree.
      A regex is used rather than lower-casing the body because `str.lower()`
      and `str.casefold()` can CHANGE LENGTH (`İ`, `ß`), which would silently
      shift every offset computed from the folded copy.
    """
    return re.compile(re.escape(needle) + r"(?!\w)", re.IGNORECASE | re.UNICODE)


def find_span(body: str, needle: str, *, from_index: int = 0) -> tuple[int, int, int]:
    """`(code_point_index, start, length)` of `needle` in `body`.

    `start`/`length` are UTF-16 code units — the wire units. The code-point index
    is returned as well so the CALLER's search cursor is advanced from the SAME
    match this span describes; the previous code re-ran `body.find()` to move the
    cursor, a second copy of the predicate that would now disagree with the
    boundary-aware search above.
    """
    body = body or ""
    match = _needle_pattern(needle).search(body, from_index)
    if match is None:
        raise MentionSpanMissing(
            f"the draft body does not contain {needle!r}"
            + (f" at or after character {from_index}" if from_index else "")
            + " as a whole word. A Signal mention REPLACES an existing span of "
              "the message text, so the text has to be there — and a match that "
              "runs straight into more letters (`@Ann` inside `@Anna`) is a "
              "DIFFERENT person's name, not this one. Add it to --body, or drop "
              "the --mention."
        )
    idx = match.start()
    return idx, utf16_len(body[:idx]), utf16_len(needle)


def utf16_span(body: str, needle: str, *, from_index: int = 0) -> tuple[int, int]:
    """`(start, length)` of `needle` in `body`, in UTF-16 code units.

    `from_index` is a CODE POINT index into `body` — the search cursor, used so
    two mentions of the same identifier take successive occurrences instead of
    both claiming the first. Raises `MentionSpanMissing` if the needle is not
    present at or after it. A thin wrapper over `find_span()` so there is exactly
    one matching rule.
    """
    _, start, length = find_span(body, needle, from_index=from_index)
    return start, length


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
        idx, start, length = find_span(body or "", needle,
                                       from_index=cursor_for.get(needle, 0))
        # Advance THIS needle's cursor past the occurrence just claimed, so
        # `--mention Ann --mention Ann` takes the first and second "@Ann" rather
        # than pointing both mentions at the same span. Derived from the match
        # `find_span` returned, NOT from a second search.
        cursor_for[needle] = idx + len(needle)
        out.append({"author": author, "start": start, "length": length})
    _refuse_overlapping_spans(out)
    return out


def _refuse_overlapping_spans(mentions: list) -> None:
    """🔴 No two mentions may claim the same characters.

    The word boundary above removes the `Ann`-inside-`@Anna` case, but it cannot
    remove this one: with members `Ann` and `Ann Smith` and a body `@Ann Smith`,
    `--mention "Ann Smith"` spans `(0, 10)` and `--mention Ann` spans `(0, 4)` —
    both boundary-legal (a space follows `@Ann`), both starting at 0. The
    receiving client is handed two overlapping rewrites of one region and what
    it renders is undefined, so this refuses rather than sending it.
    """
    spans = sorted(((m["start"], m["start"] + m["length"], i)
                    for i, m in enumerate(mentions)), key=lambda s: (s[0], s[1]))
    for (a_start, a_end, a_i), (b_start, b_end, b_i) in zip(spans, spans[1:]):
        if b_start < a_end:
            raise MentionSpansOverlap(
                f"--mention #{a_i + 1} covers UTF-16 units {a_start}–{a_end} and "
                f"--mention #{b_i + 1} covers {b_start}–{b_end}; the two spans "
                f"OVERLAP. A Signal mention REPLACES its span with the member's "
                f"display name, so overlapping spans are two rewrites of the same "
                f"characters and the result on the recipient's screen is "
                f"undefined. Give each mention its own `@who` text in --body."
            )


def _resolve_one(ident: str, *, member_set: set, candidates: list) -> str:
    """One `--mention` value → the `author` id, or raise the matching refusal."""
    ident = ident.strip()
    if looks_like_author_id(ident):
        if _norm_member(ident) not in member_set:
            raise MentionNotAMember(
                f"{ident!r} is a valid Signal identifier but is NOT a member of "
                f"the target group, so mentioning it would notify nobody. The "
                f"group has {len(member_set)} member(s). "
                # 🔴 THE ROSTER IS NOT PRINTED. `draft` needs no approval token,
                # so this refusal is a FREE, repeatable probe available to any
                # agent that can run the CLI — and it used to interpolate every
                # member uuid and phone number, for a group the caller may only
                # have guessed the address of and which may be MUTED. A raw
                # identifier list is also the least actionable thing that could
                # go here: the operator typed an id, so the answer they need is
                # "not this group", not 40 uuids to eyeball.
                f"Check --to names the group you meant."
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
        # 🔴 TRUNCATED, DELIBERATELY. Some names help an operator spot a typo;
        # the WHOLE list is a roster dump, and `draft` needs no approval token,
        # so an unbounded enumeration here is a free membership oracle for any
        # group address a caller can guess — muted ones included. A few names
        # keeps the message actionable; the count keeps it honest about what is
        # being withheld.
        known = sorted({n for c in candidates for n in _contact_names(c)})
        shown = known[:NAME_HINT_MAX]
        more = len(known) - len(shown)
        raise MentionNameNotFound(
            f"no member of the target group is named {ident!r}. Some known member "
            f"names: {shown!r}"
            + (f" (+{more} not shown)" if more > 0 else "")
            + ". Pass a bare uuid or +E.164 instead if the person has no stored "
              "name."
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
    return tuple(_one_canonical(m) for m in (mentions or []))


def _one_canonical(mention) -> tuple:
    """One stored mention → `(author, start, length)`, or a `MentionError`.

    🔴 A MALFORMED STORED ROW IS A REFUSAL, NOT A `TypeError`. The `mentions`
    column is JSON written by an earlier process; a row carrying `null`, a
    string, or an entry missing `start` used to reach `int(None)` and raise a
    bare `TypeError` — which no CLI handler catches (`send` catches
    `SendGateError`, `draft` catches `ValueError`), so a bad row in the database
    surfaced as a traceback and exit 1 instead of a refusal and exit 3.
    `MentionError` is a `ValueError`, and the two send-path call sites in
    `_signal_db` translate it to `SendGateError`.
    """
    if not isinstance(mention, dict):
        raise MentionError(
            f"unreadable stored mention {mention!r}: each entry must be an object "
            f"with {list(MENTION_KEYS)!r}")
    try:
        return (str(mention["author"]), int(mention["start"]),
                int(mention["length"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise MentionError(
            f"unreadable stored mention {mention!r}: it must carry "
            f"{list(MENTION_KEYS)!r} with an integer start and length ({exc})"
        ) from exc


def describe_mentions(mentions, author_names=None) -> list[str]:
    """Human lines for the clawgate card — WHO this message will notify.

    🔴 A BARE UUID IS NOT AN ANSWER TO "WHO". The card exists so a human can see
    who a draft pings before approving it, and
    `11111111-1111-4111-8111-111111111111` tells them nothing they can check —
    it is exactly as opaque as the `@Ann` in the preview it was added to
    disambiguate. `author_names` maps `author` → the resolved display/profile
    name; the id stays on the line too, because the name is what a human reads
    and the id is what actually goes on the wire.

    🔴 "chars" WAS WRONG. `start`/`length` are UTF-16 CODE UNITS, and the whole
    reason `utf16_len()` exists is that those differ from characters the moment
    an emoji appears earlier in the body. A card that says "chars 3–7" for a body
    whose `@Ann` starts at character 2 sends the operator checking the wrong
    thing.
    """
    names = author_names or {}
    lines = []
    for mention in (mentions or []):
        author, start, length = _one_canonical(mention)
        name = str(names.get(author) or "").strip()
        who = f"{name} <{author}>" if name else f"(no stored name) <{author}>"
        lines.append(f"{who} — replaces UTF-16 units {start}–{start + length}")
    return lines


def author_names(mentions, contacts) -> dict:
    """`{author_id: display name}` for the ids in `mentions`. PURE.

    Built from the SAME contact rows the resolver matched against, so the name on
    the card is the name the resolver used — not a second lookup that could
    disagree with it.
    """
    wanted = {str(m.get("author")) for m in (mentions or [])
              if isinstance(m, dict) and m.get("author")}
    out = {}
    for contact in (contacts or []):
        name = contact.get("display_name") or contact.get("profile_name")
        if not str(name or "").strip():
            continue
        for ident in _contact_ids(contact):
            for author in wanted:
                if _norm_member(author) == ident:
                    out.setdefault(author, str(name).strip())
    return out
