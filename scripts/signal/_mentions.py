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


class MentionIdentityUnresolvable(MentionError):
    """Contact rows for the target group cannot be split into people.

    `_identity_groups()` merges a real contact row with the durable phone-only
    PLACEHOLDER row for the same person. When THREE rows share one number —
    two real people plus a placeholder minted for that number — the placeholder
    is a bridge, and no rule that looks at rows PAIRWISE can say which real row
    it belongs to. The resulting group would hold two different people under
    one identity, which is precisely the state that made `--mention Ann` ping A
    under text reading `@Ann Smith` (round-5 audit F-A).

    So it refuses. Identity here is load-bearing twice over — it decides whose
    longer name may veto a span AND whether two name matches are an ambiguity —
    and a guess in either direction is a mention pointing at the wrong human.
    🔴 IT REFUSES THE WHOLE CALL, INCLUDING A MENTION TYPED AS A BARE UUID.
    That is not over-reach: identity also builds `_colliding_needles()`'s veto
    set, so a polluted group drops the OTHER real person's longer name from the
    avoid list and the span lands on the first characters of THEIR name — the
    same corrupted render, reached without `_resolve_one()` ever consulting a
    name. The remedy is in the DATA (delete the stale placeholder row), not in
    how the mention is spelled.
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

    * **NO WORD BOUNDARY.** `find("@Ann")` matches INSIDE `"@Anna"`. With members
      `Ann` and `Anna` and a body `"hi @Anna and @Ann ok"`, `--mention Ann`
      produced span `(3, 4)` — Ann is pinged, Anna's name is the text on screen,
      and the render is corrupted. `(?!\\w)` requires a non-word character (or
      end of string) immediately after the needle.

      🔴 `(?!\\w)` IS NOT THE WHOLE PREFIX RULE, AND MUST NOT BE DESCRIBED AS
      ONE. It blocks only WORD characters, so every member name whose second
      component is introduced by punctuation — `Ann-Marie`, `Ann.Smith`,
      `Ann'Marie` — satisfies it and `--mention Ann` landed on the first four
      characters of somebody ELSE's name anyway (measured, round-2 audit F2).
      The rest of the rule is member-aware and lives in `find_span`'s `avoid`
      argument, built by `_colliding_needles()`: a hit is skipped when ANOTHER
      group member's longer `@name` also matches at that same offset, whatever
      character separates the parts. Together the two cover "a name that is a
      prefix of another GROUP MEMBER's name cannot land on them"; neither covers
      a prefix of an arbitrary non-member string in the body, and no docstring
      here claims it does.
    * **CASE.** `_resolve_one()` matches names case-INSENSITIVELY (`.lower()`),
      so `--mention ANN` resolved to Ann and then died on `MentionSpanMissing`
      against a body reading `@Ann` — resolution and span search disagreed about
      what the same argument meant. `re.IGNORECASE` makes the two halves agree.
      A regex is used rather than lower-casing the body because `str.lower()`
      and `str.casefold()` can CHANGE LENGTH (`İ`, `ß`), which would silently
      shift every offset computed from the folded copy.
    """
    return re.compile(re.escape(needle) + r"(?!\w)", re.IGNORECASE | re.UNICODE)


def find_span(body: str, needle: str, *, from_index: int = 0,
              avoid=()) -> tuple[int, int, int]:
    """`(code_point_index, start, length)` of `needle` in `body`.

    `start`/`length` are UTF-16 code units — the wire units. The code-point index
    is returned as well so the CALLER's search cursor is advanced from the SAME
    match this span describes; the previous code re-ran `body.find()` to move the
    cursor, a second copy of the predicate that would now disagree with the
    boundary-aware search above.

    `avoid` is a list of OTHER members' `@name` needles that are LONGER than this
    one. A hit at which any of them also matches is a PREFIX COLLISION — the
    visible text belongs to the other member — and is SKIPPED, exactly as a
    `(?!\\w)` failure is skipped, so a genuine later `@who` in the same body is
    still found. Scanning on rather than refusing is what keeps
    `"hi @Ann-Marie and @Ann ok"` working.
    """
    body = body or ""
    pattern = _needle_pattern(needle)
    longer = [_needle_pattern(a) for a in (avoid or ())]
    pos = from_index
    while True:
        match = pattern.search(body, pos)
        if match is None:
            raise MentionSpanMissing(
                f"the draft body does not contain {needle!r}"
                + (f" at or after character {from_index}" if from_index else "")
                + " as a whole word that is not part of another member's name. A "
                  "Signal mention REPLACES an existing span of the message text, "
                  "so the text has to be there — and a match that runs straight "
                  "into more letters (`@Ann` inside `@Anna`) or into the rest of "
                  "another member's name (`@Ann` inside `@Ann-Marie`) is a "
                  "DIFFERENT person's name, not this one. Add it to --body, or "
                  "drop the --mention."
            )
        idx = match.start()
        if not any(p.match(body, idx) for p in longer):
            return idx, utf16_len(body[:idx]), utf16_len(needle)
        # A longer member name occupies this offset. Step ONE character, not one
        # needle: the skipped text belongs to somebody else and may itself
        # contain the needle again.
        pos = idx + 1


def utf16_span(body: str, needle: str, *, from_index: int = 0,
               avoid=()) -> tuple[int, int]:
    """`(start, length)` of `needle` in `body`, in UTF-16 code units.

    `from_index` is a CODE POINT index into `body` — the search cursor, used so
    two mentions of the same identifier take successive occurrences instead of
    both claiming the first. Raises `MentionSpanMissing` if the needle is not
    present at or after it. A thin wrapper over `find_span()` so there is exactly
    one matching rule.

    🔴 `avoid` IS FORWARDED, and that is what makes the sentence above true.
    Round-3 audit F4: this dropped the argument, so it implemented only the
    `(?!\\w)` half of the rule — pre-round-2 behaviour under a docstring
    promising the opposite. It has no production caller today, which is exactly
    why the gap was invisible; the next caller would have inherited it.
    """
    _, start, length = find_span(body, needle, from_index=from_index, avoid=avoid)
    return start, length


def _same_needle(a: str, b: str) -> bool:
    """Can the MATCHER tell these two needles apart?

    🔴 THE EQUIVALENCE THE CURSOR USES, DERIVED FROM THE SEARCH RATHER THAN
    RESTATED. Each needle's own compiled pattern is run against the other, and
    both directions must consume the whole string. Anything else is a SECOND
    matching rule that can — and did — disagree with the first: see the block
    comment in `resolve_mentions()` for the two unicode pairs where
    `str.casefold()` and `re.IGNORECASE` diverge in opposite directions.

    Symmetric by construction. NOT assumed transitive: `re`'s folding is
    per-code-point and unicode case classes are not a partition under it, so the
    caller reuses the FIRST seen needle this returns True for rather than
    building equivalence classes.
    """
    def _whole(pattern_src: str, text: str) -> bool:
        match = _needle_pattern(pattern_src).match(text)
        return match is not None and match.end() == len(text)

    return _whole(a, b) and _whole(b, a)


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


def _contact_names_raw(contact: dict) -> set[str]:
    """The stored names AS WRITTEN. Used to build `@name` needles.

    Kept un-folded on purpose: `str.lower()` can CHANGE LENGTH (`İ` → two code
    points), and a needle built from a folded name would no longer be the text
    that is actually in the body.
    """
    return {str(n).strip()
            for n in (contact.get("display_name"), contact.get("profile_name"))
            if str(n or "").strip()}


def _contact_names(contact: dict) -> set[str]:
    return {n.lower() for n in _contact_names_raw(contact)}


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
    identity = _identity_groups(candidates)

    out: list[dict] = []
    # 🔴 THE CURSOR'S EQUIVALENCE CLASS IS DERIVED FROM THE MATCHER ITSELF.
    # Two needles share a search cursor iff the SEARCH cannot tell them apart —
    # decided by `_same_needle()`, which asks the compiled patterns, not by a
    # second folding function that happens to agree on ASCII.
    #
    # Round-2 audit F1 keyed this on `needle.casefold()`, reasoning that
    # `re.IGNORECASE` and `casefold()` are the same relation. They are NOT, and
    # they disagree in BOTH directions — round-3 audit F1, both arms measured in
    # `test_the_cursor_does_NOT_merge_…` / `test_the_cursor_DOES_merge_…`:
    #
    #   over-merge  `'@ß'.casefold() == '@ss'.casefold()` is True, but the
    #               matcher never expands `ß` to `ss`, so one shared cursor made
    #               `--mention ss --mention ß` search for `@ß` from PAST the
    #               `@ss` match and refuse a body that contains it.
    #   under-merge `'@İstanbul'.casefold()` is `'@i̇stanbul'` — a `i` plus a
    #               combining dot, one code point LONGER — so it keyed apart
    #               from `'@istanbul'` while `re.IGNORECASE` folds `İ` to `i`
    #               and matches. Two cursors, both at 0, both landing on the
    #               first occurrence: `MentionSpansOverlap` on a body that
    #               genuinely holds two.
    #
    # A list, not a dict: matcher equivalence is not guaranteed transitive, so
    # this is first-match-wins over the needles already seen — which is exactly
    # "reuse the cursor of an earlier needle this search cannot distinguish from
    # the new one", and never invents a class the matcher does not have.
    cursors: list[list] = []  # [needle, cursor] in first-seen order
    for ident in idents:
        author = _resolve_one(ident, member_set=member_set,
                              candidates=candidates, identity=identity)
        needle = "@" + ident
        slot = next((s for s in cursors if _same_needle(s[0], needle)), None)
        if slot is None:
            slot = [needle, 0]
            cursors.append(slot)
        idx, start, length = find_span(
            body or "", needle, from_index=slot[1],
            avoid=_colliding_needles(ident, author=author, candidates=candidates,
                                     identity=identity))
        # Advance THIS needle's cursor past the occurrence just claimed, so
        # `--mention Ann --mention Ann` takes the first and second "@Ann" rather
        # than pointing both mentions at the same span. Derived from the match
        # `find_span` returned, NOT from a second search. `len(needle)` is the
        # matched width: the pattern is `re.escape(needle)`, and `re` folds one
        # code point to one code point, so a match is always exactly as long as
        # the needle even when the two spell the character differently.
        slot[1] = idx + len(needle)
        out.append({"author": author, "start": start, "length": length})
    _refuse_overlapping_spans(out)
    return out


def _identity_groups(candidates: list) -> dict[str, frozenset]:
    """`author id -> the author ids of every contact ROW that is the SAME PERSON`.

    THE ONE DEFINITION OF "these two contact rows are one person". Both
    `_colliding_needles()` (is this OTHER member's longer name allowed to veto?)
    and `_resolve_one()` (are these two matches an AMBIGUITY?) ask it, so the two
    cannot drift apart — round-4 audit F-B, where the second site open-coded the
    predicate as a per-row author string while its own comment claimed identity.

    🔴 ONE PERSON CAN BE TWO CONTACT ROWS, DURABLY. `_promote_placeholder()`'s
    `NOT EXISTS` branch deliberately DECLINES when a real row already holds the
    uuid, so a phone-only PLACEHOLDER minted by a draft is left in place forever
    once an envelope has taught the real row the same number. Both rows then
    carry that number, and the shared identifier is the only thing tying them
    together.

    🔴 BUT A SHARED IDENTIFIER ALONE IS NOT ENOUGH, AND ROUND-3'S FIX ASSUMED IT
    WAS (round-4 audit F-A). `signal.contacts.phone_number` is plain `TEXT` with
    NO unique constraint, and `_promote_placeholder()` only ever touches
    `is_placeholder` rows — so `upsert_contact(signal_uuid=B, phone_number=NUM)`
    while a REAL row A already holds `NUM` simply inserts a SECOND REAL row.
    That is the ordinary number-recycling / number-change shape, and unioning it
    merged two genuinely different people into one identity: person B's longer
    name stopped vetoing, and `--mention Ann` against a body reading
    `@Ann Smith` PINGED A. A wrong send, not a refusal — the one thing this
    module promises never to do.

    So the union is gated: two rows are the same person only when they share an
    identifier AND at least one of them is a PLACEHOLDER, which is exactly the
    mid-`_promote_placeholder` shape the paragraph above describes and the only
    one the data can actually justify. Two real rows sharing a number are two
    people until something other than the number says otherwise; the collision
    rule then vetoes, and a veto is a refusal.

    🔴 AND THE GATE ABOVE IS A PER-PAIR PREDICATE, WHICH CANNOT SURVIVE
    TRANSITIVITY (round-5 audit F-A). The union-find joins PATHS, not just
    edges, so with three rows on one number — real A, placeholder P, real C —
    the pair rule blocks the direct edge A—C and then A—P and P—C union anyway:
    `find(A) == find(C)`, and two real people are one identity again. Every
    step is reachable through five ordinary `upsert_contact()` calls (a draft
    mints P for an unknown number; two envelopes each hit the durable-placeholder
    DECLINE and land a real row carrying the same number). Measured before this
    fix: `--mention Ann` against `hi @Ann Smith` returned a mention for A.
    Round 4 moved the nodes from identifiers to rows, which changed WHICH nodes
    merge and left transitivity untouched — the reason this is not another patch
    to the pair condition.

    The invariant is a property of the RESULTING GROUP, so it is checked on the
    resulting group: **no group may contain two rows that are both real.** Any
    group that does is not one person, and nothing pairwise can say which real
    row the bridging placeholder belongs to — so this FAILS CLOSED with
    `MentionIdentityUnresolvable` rather than picking one. Enforced here, on the
    output, which is why relocating or re-tuning the pair rule cannot reopen it:
    a merge this function did not intend is caught by what it returns.

    Rows with no identifier in common stay separate for the same reason: merging
    on a NAME would reopen the prefix collision the collision rule exists for.
    """
    rows = list(candidates or [])
    parent = list(range(len(rows)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, row_a in enumerate(rows):
        for j in range(i + 1, len(rows)):
            row_b = rows[j]
            if not (_contact_ids(row_a) & _contact_ids(row_b)):
                continue
            if not (row_a.get("is_placeholder") or row_b.get("is_placeholder")):
                continue
            root_a, root_b = find(i), find(j)
            if root_a != root_b:
                parent[root_a] = root_b

    # 🔴 THE GROUP-LEVEL INVARIANT. Checked on the formed groups, not on the
    # pairs that formed them, because the property "this group is ONE person"
    # is not expressible as a pairwise predicate under transitivity.
    real_per_group: dict[int, list] = {}
    for i, row in enumerate(rows):
        if not row.get("is_placeholder"):
            real_per_group.setdefault(find(i), []).append(row)
    for members_ in real_per_group.values():
        if len(members_) > 1:
            authors = sorted({_contact_author(r) for r in members_} - {""})
            raise MentionIdentityUnresolvable(
                f"the stored contacts for this group put "
                f"{len(members_)} DIFFERENT real identities "
                f"({authors[:NAME_HINT_MAX]!r}) into one identity, bridged by a "
                f"PLACEHOLDER row that shares an identifier with both. Which of "
                f"them the placeholder belongs to is not decidable from the "
                f"rows, and guessing picks who gets notified — so no --mention "
                f"can be resolved against this group until the stale placeholder "
                f"contact row is removed."
            )

    clusters: dict[int, set] = {}
    for i, row in enumerate(rows):
        clusters.setdefault(find(i), set()).add(
            _norm_member(_contact_author(row)))
    out: dict[str, set] = {}
    for i, row in enumerate(rows):
        key = _norm_member(_contact_author(row))
        if key:
            out.setdefault(key, set()).update(clusters[find(i)] - {""})
    return {key: frozenset(ids) for key, ids in out.items()}


def _colliding_needles(ident: str, *, author: str, candidates: list,
                       identity: dict | None = None) -> list[str]:
    """`@name` needles of OTHER group members that are LONGER than `@ident`.

    🔴 THE HALF OF THE PREFIX RULE `(?!\\w)` CANNOT DO. The right-hand word
    boundary blocks `@Ann` inside `@Anna` but NOT inside `@Ann-Marie` or
    `@Ann.Smith` — a hyphen and a dot are both non-word characters, so the
    boundary is satisfied and the mention landed on the first four characters of
    a different member's name while the text on screen stayed theirs. Handing
    the other members' full names to `find_span` closes that for ANY separator,
    because the test is "does a longer member name occupy this exact offset",
    not "which characters are allowed to follow".

    Restricted to OTHER PEOPLE: a contact with `display_name="Ann"` and
    `profile_name="Ann Marie"` is one person, and their own longer name must not
    veto their own ping. 🔴 "Other person" is decided on RESOLVED IDENTITY —
    `_identity_groups()`, which unions a real row with a durable phone-only
    PLACEHOLDER for the same person — not on the raw `_contact_author()` string,
    which splits that person into two (round-3 audit F3). 🔴 And the union is
    gated on `is_placeholder`, because two REAL rows sharing a recycled phone
    number are two people: merging them dropped the second person's veto and
    sent a mention pointing at the FIRST (round-4 audit F-A). Restricted to
    LONGER names because an equal-length match IS this identifier (the matcher
    is case-insensitive), which is the `MentionNameAmbiguous` case and belongs
    to `_resolve_one`.
    """
    width = len(ident.strip())
    identity = identity or {}
    key = _norm_member(author)
    mine = identity.get(key) or frozenset({key})
    return ["@" + name
            for contact in (candidates or [])
            if _norm_member(_contact_author(contact)) not in mine
            for name in _contact_names_raw(contact)
            if len(name) > width]


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


def _resolve_one(ident: str, *, member_set: set, candidates: list,
                 identity: dict | None = None) -> str:
    """One `--mention` value → the `author` id, or raise the matching refusal.

    `identity` is `_identity_groups(candidates)` — the SAME map
    `_colliding_needles()` uses. Passed in rather than rebuilt so the two sites
    cannot disagree about who is one person (round-4 audit F-B: this site
    open-coded the predicate as `_contact_author(contact)`, a per-ROW string,
    while the comment beside it claimed identity — so one person holding a real
    row and a durable placeholder row was refused as an AMBIGUITY, and the
    remedy the message offered was half wrong because one of the two ids it
    printed was the synthetic placeholder uuid, which is not in `member_set`).
    """
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
    # contact rows mid-`_promote_placeholder`, and that is not ambiguity. The
    # identity comes from `_identity_groups()` — the one definition, shared with
    # `_colliding_needles()` — and the group's REAL row wins over its placeholder
    # row, so a person who has both resolves to their real Signal id instead of
    # the synthetic one.
    identity = identity or {}
    by_identity: dict = {}
    for contact in matched:
        key = _norm_member(_contact_author(contact))
        group = identity.get(key) or frozenset({key})
        group_key = min(group) if group else key
        seen = by_identity.get(group_key)
        if seen is None or (seen.get("is_placeholder")
                            and not contact.get("is_placeholder")):
            by_identity[group_key] = contact
    if not by_identity:
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
    if len(by_identity) > 1:
        # The ids PRINTED are the ones a caller can actually pass back: each
        # group's chosen row's author, not the normalised grouping key.
        choices = sorted(_contact_author(c) for c in by_identity.values())
        raise MentionNameAmbiguous(
            f"{ident!r} is AMBIGUOUS — it matches {len(by_identity)} members of "
            f"the target group ({choices!r}). Pass the bare uuid or "
            f"+E.164 of the one you mean."
        )
    contact = next(iter(by_identity.values()))
    author = _contact_author(contact)
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
