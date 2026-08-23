"""The group MUTE list — `signal.excluded_groups` and the one read predicate.

Scope of the claim these tests make, stated up front because it is narrower than
"the group is filtered out":

  * They cover the three PYTHON read surfaces on `SignalDB`. Raw `psql` against
    the schema bypasses every one of them, by construction — a predicate in an
    application query cannot bind a hand-typed statement. `SKILL.md` says so
    where it prints those one-liners.
  * They assert HIDING, never deleting. The rows stay; that is the whole design,
    and `test_muting_deletes_nothing...` is what keeps it true.

The seam these tests exist for: a filter open-coded at three call sites is wrong
at two of them, in the same direction, and the failure is silent. So there is a
behavioural case per surface AND a ledger that fails when the set of read
surfaces grows or shrinks — a structural check alone type-checks past a wrong
argument, and a behavioural check alone cannot see a NEW method that skipped it.
"""
from __future__ import annotations

import ast
import base64
import inspect
import re
import sys
import textwrap
import types

import pytest

import _signal_db
from _signal_db import SignalDB, not_excluded

import consumer


GROUP_KEEP = b"\x11" * 32       # 32 bytes, like every real Signal group id
GROUP_MUTE = b"\x22" * 32
GROUP_UNSEEN = b"\x33" * 32     # muted before it was ever stored

# The shape of the API's `id` field: `group.` + base64(base64(raw)). GENERATED
# from a repeated byte, never copied from a live response.
#
# 🔴 THIS REPO IS PUBLIC AND NOTHING WOULD CATCH A REAL ONE. A Signal group id
# identifies a real group of real people; the four content gates
# (`test_no_captured_text.py`, `test_no_captured_markup.py`, and the IP and
# hostname ones) scan JSON/JSONL/HTML/TXT and **not** `.py`, so a real id pasted
# into a test file lands in history permanently with no gate between it and
# `main`. An earlier revision of this very file carried one, copied straight out
# of `GET /v1/groups/<account>` while checking what the field looked like.
# Generate fixtures; never paste a response.
GROUP_ID_FIELD_SHAPE = "group." + base64.b64encode(
    base64.b64encode(b"\x44" * 32)).decode()


def _seed(db):
    """Two groups and a DM, each with a distinctive body. Returns row ids.

    🔴 Every body, group id and contact is PAIRWISE DISTINCT, and distinct from
    any constant an assertion names. A fixture whose values collapse into each
    other cannot see a mutant that hardcodes one of them — this pipeline has
    already shipped three such fixtures (two operand-order mutants survived
    because `source` and `sourceNumber` were the same string, and a hardcoding
    mutant survived 400 tests because the fixture emoji equalled the asserted
    constant).
    """
    keep_row = db.upsert_group(group_id=GROUP_KEEP, name="")
    mute_row = db.upsert_group(group_id=GROUP_MUTE, name="")
    ids = {}
    ids["keep"] = db.upsert_message({
        "message_timestamp": 1000,
        "source_uuid": "11111111-1111-4111-8111-111111111111",
        "source_number": "+15550001", "source_name": "Alice",
        "message_type": "group_message", "body": "quarterly kestrel report",
        "group_id": GROUP_KEEP, "is_outbound": False,
    })
    ids["mute"] = db.upsert_message({
        "message_timestamp": 2000,
        "source_uuid": "22222222-2222-4222-8222-222222222222",
        "source_number": "+15550002", "source_name": "Bob",
        "message_type": "group_message", "body": "quarterly kestrel potluck",
        "group_id": GROUP_MUTE, "is_outbound": False,
    })
    ids["dm"] = db.upsert_message({
        "message_timestamp": 3000,
        "source_uuid": "33333333-3333-4333-8333-333333333333",
        "source_number": "+15550003", "source_name": "Carol",
        "message_type": "direct_message", "body": "quarterly kestrel invoice",
        "group_id": None, "is_outbound": False,
    })
    ids["_rows"] = {"keep": keep_row, "mute": mute_row}
    return ids


def _conv_group_rows(db):
    return {r["group_row_id"] for r in db.list_conversations()}


# --------------------------------------------------------------------------- #
# Behaviour, one case per read surface — each with its own positive control
# --------------------------------------------------------------------------- #
def test_conversations_hides_a_muted_group_and_shows_it_again_after_unmute(db):
    ids = _seed(db)
    mute_row = ids["_rows"]["mute"]
    keep_row = ids["_rows"]["keep"]

    # POSITIVE CONTROL: it is visible BEFORE the mute. Without this the "hidden"
    # assertion below is indistinguishable from a query wired to nothing.
    assert mute_row in _conv_group_rows(db)
    assert keep_row in _conv_group_rows(db)

    db.exclude_group(GROUP_MUTE, note="filtered by operator")
    after = _conv_group_rows(db)
    assert mute_row not in after
    assert keep_row in after
    assert None in after, "the DM conversation must survive a group mute"

    # And the rollback story is real, not asserted.
    assert db.unexclude_group(GROUP_MUTE) == 1
    assert _conv_group_rows(db) == {mute_row, keep_row, None}


def test_search_hides_a_muted_group(db):
    _seed(db)
    # All three bodies match this query — that is deliberate. A query matching
    # only the muted row could not tell "filtered correctly" from "matched
    # nothing", which is the empty-result trap.
    before = {r["body"] for r in db.search("quarterly kestrel")}
    assert before == {"quarterly kestrel report", "quarterly kestrel potluck",
                      "quarterly kestrel invoice"}

    db.exclude_group(GROUP_MUTE)
    after = {r["body"] for r in db.search("quarterly kestrel")}
    assert after == {"quarterly kestrel report", "quarterly kestrel invoice"}


def test_get_message_hides_a_muted_message_by_id(db):
    ids = _seed(db)
    assert db.get_message(ids["mute"])["body"] == "quarterly kestrel potluck"

    db.exclude_group(GROUP_MUTE)
    assert db.get_message(ids["mute"]) is None, (
        "the id route must be filtered too — otherwise the mute has a hole in "
        "the shape of anyone who knows a message id")
    # The unmuted neighbours are untouched: this is a filter, not an outage.
    assert db.get_message(ids["keep"])["body"] == "quarterly kestrel report"
    assert db.get_message(ids["dm"])["body"] == "quarterly kestrel invoice"


def test_muting_deletes_nothing_and_unmute_restores_exactly(db):
    ids = _seed(db)
    before_rows = db.conn.count("messages")
    before_search = [dict(r) for r in db.search("quarterly kestrel")]

    db.exclude_group(GROUP_MUTE)
    assert db.conn.count("messages") == before_rows, (
        "muting must not delete — the pipeline is forward-only, so a deleted "
        "message can never be re-fetched and the decision would be irreversible")
    assert db.conn.count("attachments") == 0  # nothing cascaded either

    db.unexclude_group(GROUP_MUTE)
    assert [dict(r) for r in db.search("quarterly kestrel")] == before_search
    assert db.get_message(ids["mute"])["body"] == "quarterly kestrel potluck"


# --------------------------------------------------------------------------- #
# The mute list itself
# --------------------------------------------------------------------------- #
def test_muting_is_idempotent_and_the_note_is_updated_not_duplicated(db):
    _seed(db)
    db.exclude_group(GROUP_MUTE, note="first reason")
    db.exclude_group(GROUP_MUTE, note="second reason")
    rows = db.list_excluded_groups()
    assert len(rows) == 1
    assert rows[0]["note"] == "second reason"


def test_re_muting_WITHOUT_a_note_keeps_the_recorded_reason(db):
    """The note→note case above cannot see this; note→None is the common one.

    `mute <id>` with no --note is the natural way to re-issue a mute, and a bare
    `note = EXCLUDED.note` made that destroy the only record of WHY the group is
    muted — as a side effect of a command that otherwise changes nothing.
    """
    _seed(db)
    db.exclude_group(GROUP_MUTE, note="spam group, muted 2026-08-19")
    db.exclude_group(GROUP_MUTE)
    assert db.list_excluded_groups()[0]["note"] == "spam group, muted 2026-08-19"


def test_a_group_can_be_muted_before_it_has_ever_been_seen(db):
    _seed(db)
    db.exclude_group(GROUP_UNSEEN, note="not stored yet")
    listed = {bytes(r["group_id"]): r for r in db.list_excluded_groups()}
    assert listed[GROUP_UNSEEN]["group_row_id"] is None, (
        "an unseen group is an ordinary state — the mute is the INTENT, and a "
        "FK to signal.groups could not express it")

    # …and it takes effect the moment the group does appear.
    db.upsert_message({
        "message_timestamp": 4000,
        "source_uuid": "44444444-4444-4444-8444-444444444444",
        "source_number": "+15550004", "source_name": "Dave",
        "message_type": "group_message", "body": "quarterly kestrel latecomer",
        "group_id": GROUP_UNSEEN, "is_outbound": False,
    })
    row = db.upsert_group(group_id=GROUP_UNSEEN, name="")
    assert row not in _conv_group_rows(db)
    assert not [r for r in db.search("quarterly kestrel")
                if r["body"] == "quarterly kestrel latecomer"]


def test_hidden_message_count_reports_what_the_mute_is_actually_hiding(db):
    ids = _seed(db)
    db.exclude_group(GROUP_MUTE)
    row = next(r for r in db.list_excluded_groups()
               if bytes(r["group_id"]) == GROUP_MUTE)
    assert row["hidden_message_count"] == 1
    assert row["group_row_id"] == ids["_rows"]["mute"]


def test_unmuting_something_that_was_not_muted_reports_zero(db):
    _seed(db)
    assert db.unexclude_group(GROUP_KEEP) == 0


@pytest.mark.parametrize("bad", [b"", bytearray()])
def test_an_empty_group_id_is_refused(db, bad):
    with pytest.raises(ValueError):
        db.exclude_group(bad)


def test_a_str_group_id_is_refused(db):
    """A base64 STRING silently stored as bytes would mute nothing, forever.

    🔴 `match=` is load-bearing. Without it this passed on the `TypeError` that
    `bytes("…")` raises further down — green for the wrong reason, and still
    green with the isinstance guard deleted. Pin THIS guard's own message.
    """
    with pytest.raises(TypeError, match="raw Signal group id"):
        db.exclude_group("IiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiI=")


# --------------------------------------------------------------------------- #
# The predicate
# --------------------------------------------------------------------------- #
def test_the_predicate_refuses_an_unsafe_alias():
    """The alias is interpolated, so the whitelist is load-bearing."""
    for bad in ("m; DROP TABLE signal.messages --", "M", "1m", "m m", ""):
        with pytest.raises(ValueError):
            not_excluded(bad)


def test_the_predicate_actually_USES_the_alias_it_is_given():
    """🔴 Validating the alias is not the same as using it.

    A mutant that validated `alias` and then hardcoded `m` SURVIVED the whole
    suite: every call site passes `m`, so nothing could tell the two apart. The
    parameter would have silently become decorative, and the first read method
    to use a different alias would get a predicate pointing at the wrong table.
    """
    assert "q.group_id" in not_excluded("q")
    assert "m.group_id" not in not_excluded("q")


def test_the_predicate_is_composable_with_AND():
    """`NOT EXISTS`, never `... IS NULL OR ...`.

    An OR-form predicate ANDed onto a WHERE clause without brackets widens the
    query to every row — silently, and only at the call site that forgot them.
    This pins the shape rather than the wording, so a rewrite that reintroduces
    a top-level OR fails here.
    """
    sql = not_excluded("m")
    assert sql.startswith("NOT EXISTS ("), sql
    # No OR outside the subquery's parens.
    depth, top_level = 0, []
    for tok in re.findall(r"\(|\)|\bOR\b", sql):
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth -= 1
        elif depth == 0:
            top_level.append(tok)
    assert not top_level, f"top-level OR in the predicate: {sql}"


# --------------------------------------------------------------------------- #
# 🔴 THE LEDGER — two sets that must PARTITION every read of signal.messages
# --------------------------------------------------------------------------- #
# Every method that reads `signal.messages` must either apply `not_excluded()`
# or be listed as EXEMPT with a reason — in EXACTLY ONE of the two sets below,
# never both, never neither. This is the seam guard: the behavioural cases above
# can only see the surfaces that existed when they were written, and a new read
# method that skipped the filter is exactly the failure this pipeline has already
# shipped once (the sync reaction branch, added without the guards its inbound
# twin already had).
#
# 🔴 WHY A PARTITION AND NOT A UNION. The earlier version asserted only
# `found == FILTERED_READS | EXEMPT_READS`. That is HALF A GUARD: a method MOVED
# from one set to the other leaves the union identical, so re-classifying
# `get_message` as exempt — turning the mute off for it — was a green change.
# The partition below is checked against what the CODE does (an AST scan for a
# real call to `not_excluded`), so the ledger cannot disagree with the
# implementation in either direction.
FILTERED_READS = {"list_conversations", "search", "get_message"}

# 🔴 THE REASON LIVES HERE, AT THE POINT OF DECISION — not in a comment
# elsewhere that can rot on its own. This ledger's justification has ALREADY gone
# stale once: it read "drafts have no group_id", which was true until
# `draft_message()` learned to address a group, and had to be re-derived after
# the fact. Everything a reviewer needs to re-litigate an exemption is in the
# string beside it.
EXEMPT_READS = {
    "list_excluded_groups":
        "Counts what a mute is HIDING — it must see muted rows, that is its "
        "entire job. Filtering it would make every mute report 0 hidden "
        "messages, which is the number `consumer.py mute` refuses to print.",
    # --- WRITES that name signal.messages in a subquery, not read surfaces.
    "upsert_reaction":
        "WRITE, not a read surface: the SELECT resolves a reaction's target "
        "message id. It returns no body to any caller.",
    "apply_remote_delete":
        "WRITE, not a read surface: the SELECT scopes which rows to tombstone. "
        "It returns a rowcount, never content.",
    # --- The DRAFT / outbound surface (D3).
    "get_draft":
        "OUTBOUND surface (D3). Two independent reasons, both re-derived after "
        "the previous reason ('drafts have no group_id') was falsified by group "
        "drafting. (1) POPULATION: `send_state IS NOT NULL` restricts it to rows "
        "the OPERATOR composed — muting is about what you READ, a draft is what "
        "you WROTE, and no third party's words can reach a caller through it. "
        "That predicate is load-bearing, not incidental: without it `is_outbound` "
        "alone also matched every device-sync ECHO, which DOES carry another "
        "person's body — pinned by "
        "test_get_draft_refuses_a_device_sync_ECHO_not_just_a_draft and by "
        "test_the_draft_exemptions_PREMISE_is_still_in_the_code. (2) FILTERING "
        "IT WOULD BE A LIVE BUG: approve_draft, send_approved and reconcile_send "
        "all reach it through _draft_or_raise, so a mute landing mid-flight "
        "would strand that draft in 'sending' with no route out and report it as "
        "'does not exist' — an undesigned extra refusal path through the D3 "
        "approval gate.",
    "list_drafts":
        "OUTBOUND surface (D3), same two reasons as get_draft: `send_state IS "
        "NOT NULL` restricts it to the operator's own composed rows, and "
        "filtering the compose/approve/send path would hide a draft the operator "
        "still has to resolve.",
    "account_number":
        "Returns the SENDING account's phone number for a draft — no message "
        "content at all, so there is nothing for a mute to hide. Pinned by "
        "test_the_draft_exemptions_PREMISE_is_still_in_the_code, which fails if "
        "this ever starts selecting a body.",
}

# The subset of EXEMPT_READS whose exemption rests on the `send_state IS NOT
# NULL` population claim. Named here so the premise can be pinned structurally
# rather than trusted from the prose above.
_POPULATION_CLAIM_READS = {"get_draft", "list_drafts"}


# --------------------------------------------------------------------------- #
# Discovery — over the AST, never over raw source text
# --------------------------------------------------------------------------- #
def _method_tree(fn):
    """The parsed `FunctionDef` for a method, or None if it has no source."""
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):            # pragma: no cover - defensive
        return None
    try:
        return ast.parse(textwrap.dedent(src)).body[0]
    except (SyntaxError, IndexError):       # pragma: no cover - defensive
        return None


def _docstring_node(node):
    """The method's docstring Constant NODE, or None."""
    first = node.body[0] if node.body else None
    if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)):
        return first.value
    return None


def _sql_strings(node) -> list:
    """Every string literal in a method EXCEPT its docstring.

    🔴 The docstring is excluded on purpose. These methods' docstrings DISCUSS
    the schema and the mute predicate at length — that is prose, not SQL, and
    reading it as either is how a guard certifies the opposite of the truth.

    🔴 EXCLUDED BY NODE IDENTITY, not by comparing the string. The first version
    of this function did `n.value != ast.get_docstring(node)` — and
    `ast.get_docstring()` CLEANS (dedents) what it returns, so the comparison
    only ever matched a SINGLE-LINE docstring. Every multi-line docstring in the
    module was therefore still being read as SQL, which is the exact confusion
    this helper exists to prevent. Measured: a mutant that deleted
    `send_state IS NOT NULL` from `get_draft`'s SQL left
    `test_the_draft_exemptions_PREMISE_is_still_in_the_code` GREEN, because the
    docstring above that SQL also contains the phrase.
    """
    doc = _docstring_node(node)
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n is not doc]


def _reads_messages(fn) -> bool:
    node = _method_tree(fn)
    if node is None:
        return False
    strings = _sql_strings(node)
    return (any("signal.messages" in s for s in strings)
            and any(re.search(r"\bSELECT\b", s, re.IGNORECASE) for s in strings))


def _calls_the_mute_predicate(fn) -> bool:
    """True iff the method REALLY CALLS `not_excluded(...)`.

    🔴 AST, not a substring search, and this is not fastidiousness — the
    substring version was WRONG on the shipped code. `SignalDB.get_draft` does
    not filter, and must not, but its docstring explains the mute predicate and
    contains the characters `not_excluded(`. The old guard therefore reported
    get_draft as filtered; had anyone moved it into FILTERED_READS the suite
    would have stayed green while the method filtered nothing. A guard SPELLED
    over source text is satisfiable by PROSE. A call node is not.

    (f-strings are covered: `f"... {not_excluded('m')}"` parses to a
    `FormattedValue` wrapping a real `Call`, which `ast.walk` reaches.)
    """
    node = _method_tree(fn)
    if node is None:
        return False
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "not_excluded" for n in ast.walk(node))


def _read_methods_touching_messages(cls=None) -> set[str]:
    """Methods on `cls` whose SQL SELECTs from `signal.messages`.

    🔴 SCOPE, stated because this discovery is still SHAPE-BASED and the
    difference matters. It parses the method's own source, so it detects exactly
    one shape: SQL written as a literal naming `signal.messages`, in a method
    defined directly on the class. Measured evasions — all confirmed to slip
    past it: SQL held in a module-level constant; `SET search_path` plus an
    unqualified `FROM messages`; delegation to a module-level helper; a method
    inherited from a mixin (absent from `vars(cls)`).

    What it no longer does is FALSE-POSITIVE on prose: the docstring is excluded,
    so a method that merely discusses the schema is not counted as reading it.

    So this catches the copy-paste shape — which is how every read method here
    was actually written, and how the next one will be — and it is not a proof
    of completeness. Claim it as the former.
    """
    cls = cls or SignalDB
    return {name for name, fn in vars(cls).items()
            if callable(fn) and not name.startswith("__") and _reads_messages(fn)}


def _filtered_by_the_code(cls=None) -> set[str]:
    cls = cls or SignalDB
    return {n for n in _read_methods_touching_messages(cls)
            if _calls_the_mute_predicate(getattr(cls, n))}


# --------------------------------------------------------------------------- #
# Controls for the discovery itself — synthetic surfaces, never called
# --------------------------------------------------------------------------- #
class _LedgerProbeSubject:      # pragma: no cover - parsed, never executed
    """Read surfaces built to order, so the scanners can be watched to work.

    Nothing here is ever CALLED — only `inspect.getsource`d and parsed. That is
    why the bodies reference a `self._c` that does not exist.
    """

    def unfiltered_read(self):
        with self._c.cursor() as cur:
            cur.execute("SELECT m.id FROM signal.messages m WHERE m.id = %s", (1,))

    def filtered_read(self):
        with self._c.cursor() as cur:
            cur.execute(f"SELECT m.id FROM signal.messages m "
                        f"WHERE {not_excluded('m')}")

    def docstring_CLAIMS_the_predicate(self):
        """Applies not_excluded() to every row it returns.

        🔴 It does not. This is the exact shape found in the shipped code:
        `get_draft`'s docstring explains the mute predicate, and a substring
        guard read that explanation as a call.
        """
        with self._c.cursor() as cur:
            cur.execute("SELECT m.id FROM signal.messages m")

    def prose_only_mentions_the_schema(self):
        """Talks about a SELECT over signal.messages without issuing one."""
        return None

    def MULTILINE_prose_only_mentions_the_schema(self):
        """Talks about the schema across several lines, and issues nothing.

        🔴 THE MULTI-LINE CASE IS A SEPARATE CONTROL, not a duplicate of the
        one above. `ast.get_docstring()` CLEANS what it returns, so excluding
        the docstring by comparing its VALUE silently worked for a single-line
        docstring and silently failed for every multi-line one. With only the
        one-line control present, that bug was invisible and shipped.

        This method issues no SQL: it must NOT be seen as a read, and it names
        SELECT and signal.messages purely as prose.
        """
        return None

    def not_a_read_at_all(self):
        return "nothing to do with the schema"


def test_the_scan_and_the_predicate_detector_can_BOTH_go_red():
    """🔴 Validate the instrument before reading its verdict.

    Negative control (can it go red?) and positive control (can it ever observe
    the thing?) for both scanners, on surfaces built to order. Without this, the
    partition test passing proves only that two sets match each other — it would
    look identical if the scan were wired to nothing.
    """
    found = _read_methods_touching_messages(_LedgerProbeSubject)
    assert found == {"unfiltered_read", "filtered_read",
                     "docstring_CLAIMS_the_predicate"}, sorted(found)
    # POSITIVE: it detects a real read.  NEGATIVE: prose alone is not a read —
    # asserted for BOTH docstring shapes, because they fail differently.
    for prose in ("prose_only_mentions_the_schema",
                  "MULTILINE_prose_only_mentions_the_schema"):
        assert prose not in found, (
            f"the scan counted {prose}, a method that only DISCUSSES the "
            f"schema — it is matching prose and would read a docstring as SQL")
    assert "not_a_read_at_all" not in found

    filtered = _filtered_by_the_code(_LedgerProbeSubject)
    assert filtered == {"filtered_read"}, sorted(filtered)


def test_the_predicate_detector_is_NOT_walked_by_a_DOCSTRING_mention():
    """🔴 The regression this rebuild exists for, pinned on the REAL method.

    Measured on the shipped code: `SignalDB.get_draft`'s docstring contains the
    characters `not_excluded(` while the method deliberately does not filter. A
    substring guard therefore certified it as filtered — so moving it into
    FILTERED_READS would have been a GREEN change that turned mute off for it.
    """
    src = inspect.getsource(SignalDB.get_draft)
    assert "not_excluded(" in src, (
        "POSITIVE CONTROL FAILED: get_draft's source no longer contains the "
        "characters this test is about, so it can no longer detect the "
        "confusion. Point it at whichever method now discusses the predicate.")
    assert not _calls_the_mute_predicate(SignalDB.get_draft), (
        "the detector reported get_draft as filtering. It does not — the match "
        "is its DOCSTRING. A guard spelled over source text is satisfiable by "
        "prose; use the AST.")


# --------------------------------------------------------------------------- #
# THE PARTITION
# --------------------------------------------------------------------------- #
def test_the_two_ledgers_PARTITION_every_read_of_signal_messages():
    """Every read of `signal.messages` is in EXACTLY ONE ledger — and the
    ledger it is in must agree with what the METHOD ACTUALLY DOES.

    Four independent failure directions, each with its own message so the
    diagnosis arrives with the failure:

      * a new read method in NEITHER set,
      * a method listed in BOTH,
      * a name in a ledger that is no longer a read method (renamed/deleted),
      * a method MISCLASSIFIED — listed as filtered while it does not call the
        predicate, or listed as exempt while it does. This is the direction a
        union check cannot see, and it is the one that turns mute off.
    """
    found = _read_methods_touching_messages()
    assert found, (
        "HARNESS BROKEN: the scan found no message-reading methods at all, so "
        "every assertion below would pass vacuously")

    both = FILTERED_READS & set(EXEMPT_READS)
    assert not both, (
        f"these methods are in BOTH ledgers, so the partition is meaningless "
        f"and a union check would not notice: {sorted(both)}")

    unclassified = found - (FILTERED_READS | set(EXEMPT_READS))
    assert not unclassified, (
        f"these methods SELECT from signal.messages and are in NEITHER ledger: "
        f"{sorted(unclassified)}. Add each to FILTERED_READS and apply "
        f"not_excluded(), or to EXEMPT_READS with a reason that says why a "
        f"muted group's content cannot reach a caller through it.")

    stale = (FILTERED_READS | set(EXEMPT_READS)) - found
    assert not stale, (
        f"these names are in a ledger but no longer read signal.messages "
        f"(renamed, deleted, or the SQL moved out of the method): "
        f"{sorted(stale)}. Drop them from the ledger.")

    # 🔴 THE DIRECTION A UNION CHECK IS BLIND TO.
    by_code = _filtered_by_the_code()
    assert by_code == FILTERED_READS, (
        f"the ledger disagrees with the CODE about who filters.\n"
        f"  listed as filtered but does NOT call not_excluded(): "
        f"{sorted(FILTERED_READS - by_code)}\n"
        f"  calls not_excluded() but is listed EXEMPT: "
        f"{sorted(by_code - FILTERED_READS)}\n"
        f"Moving a method between the ledgers leaves their UNION identical, so "
        f"this assertion is the only one that can see it.")
    assert set(EXEMPT_READS) == found - by_code, (
        f"EXEMPT_READS is not the complement of the filtered set: "
        f"{sorted(set(EXEMPT_READS) ^ (found - by_code))}")


@pytest.mark.parametrize("name", sorted(FILTERED_READS))
def test_every_filtered_read_actually_calls_the_predicate(name):
    assert _calls_the_mute_predicate(getattr(SignalDB, name)), (
        f"SignalDB.{name} SELECTs from signal.messages without calling the mute "
        f"predicate — muted rows leak through it")


@pytest.mark.parametrize("name", sorted(EXEMPT_READS))
def test_every_exempt_read_really_does_NOT_call_the_predicate(name):
    """The mirror. Without it, "exempt" is an unchecked assertion.

    A method that both filters AND is listed exempt is not a leak, but it means
    the ledger is describing something other than the code — and the ledger is
    what the next person reads before deciding whether a new method is safe.
    """
    assert not _calls_the_mute_predicate(getattr(SignalDB, name)), (
        f"SignalDB.{name} is listed in EXEMPT_READS but DOES call "
        f"not_excluded(). Move it to FILTERED_READS: the ledger is now lying "
        f"about which surfaces the mute covers.")


@pytest.mark.parametrize("name", sorted(EXEMPT_READS))
def test_every_exemption_carries_a_REASON_at_the_point_of_decision(name):
    """An exemption with no argument beside it is the stale-prose failure again.

    🔴 SCOPE: this is a FLOOR, not a check that the reason is TRUE. It stops
    `"name": ""` and one-word placeholders; it cannot tell a correct reason from
    a confident wrong one. The reasons that are load-bearing are pinned
    STRUCTURALLY instead — see
    `test_the_draft_exemptions_PREMISE_is_still_in_the_code`.
    """
    reason = EXEMPT_READS[name]
    assert isinstance(reason, str) and len(reason.split()) >= 12, (
        f"EXEMPT_READS[{name!r}] does not carry an argument a reviewer could "
        f"disagree with: {reason!r}")


def test_the_draft_exemptions_PREMISE_is_still_in_the_code():
    """🔴 The load-bearing half of the draft exemption, pinned STRUCTURALLY.

    The reason strings claim `get_draft`/`list_drafts` return only rows the
    OPERATOR composed, and that `account_number` returns no content. Those are
    claims about SQL, so they are checked against the SQL — a prose reason that
    is merely re-read each time is exactly how the previous justification
    survived being false.

    If `send_state IS NOT NULL` is ever dropped, the population widens to every
    device-sync ECHO — which carries OTHER PEOPLE's bodies, in groups — and the
    exemption becomes a real leak. This goes red on that edit.
    """
    assert _POPULATION_CLAIM_READS <= set(EXEMPT_READS), (
        f"a method whose exemption rests on the population claim is no longer "
        f"exempt: {sorted(_POPULATION_CLAIM_READS - set(EXEMPT_READS))}")
    for name in sorted(_POPULATION_CLAIM_READS):
        sql = " ".join(_sql_strings(_method_tree(getattr(SignalDB, name))))
        assert re.search(r"send_state\s+IS\s+NOT\s+NULL", sql, re.IGNORECASE), (
            f"SignalDB.{name} no longer restricts itself to drafts with "
            f"`send_state IS NOT NULL`. Its EXEMPT_READS reason depends on that "
            f"predicate: without it the method also returns device-sync ECHOES, "
            f"which carry other people's bodies from muted groups.")

    account_sql = " ".join(_sql_strings(_method_tree(SignalDB.account_number)))
    assert "signal.messages" in account_sql, (
        "POSITIVE CONTROL FAILED: no SQL extracted for account_number")
    assert not re.search(r"\bbody\b", account_sql, re.IGNORECASE), (
        "SignalDB.account_number now selects a message body. Its exemption is "
        "'returns no message content at all' — that is no longer true.")


def test_get_draft_refuses_a_device_sync_ECHO_not_just_a_draft(db):
    """🔴 The exemption's REASON, measured on the population it actually returns.

    `test_a_draft_really_has_no_group_id` only inspects a row that
    `draft_message()` created, so it can never see this: `is_outbound` alone
    also matches every device-sync echo of a message sent from the phone, and
    those DO carry a `group_id`. Before `send_state IS NOT NULL`, `get_draft`
    returned a muted group's body in full while `get_message` returned None for
    the very same row.
    """
    ids = _seed(db)
    db.exclude_group(GROUP_MUTE)
    # The seeded muted-group row is inbound; make an OUTBOUND one in that group,
    # exactly as a sync echo arrives — no send_state, because nothing drafted it.
    echo_id = db.upsert_message({
        "message_timestamp": 5000,
        "source_uuid": "66666666-6666-4666-8666-666666666666",
        "source_number": "+15550006", "source_name": "me",
        "message_type": "group_message", "body": "SECRET body in a muted group",
        "group_id": GROUP_MUTE, "is_outbound": True,
    })
    assert db.get_message(echo_id) is None, "positive control: get_message hides it"
    assert db.get_draft(echo_id) is None, (
        "get_draft returned a device-sync echo — so `is_outbound` alone is not "
        "'a draft', and the ledger's exemption reason is false")
    assert ids["mute"] is not None


def test_a_DM_draft_still_has_no_group_id(db):
    """A draft to a PERSON carries no group — the group linkage is not global.

    This used to be `test_a_draft_really_has_no_group_id` and carried the
    exemption's whole reason. Group drafting has since been added, so the claim
    it can honestly make is narrower: a DM draft is still ungrouped, which is
    what stops `draft_message()`'s new branch from stamping a group onto every
    outbound row. The mute-side claim moved to
    `test_a_muted_group_draft_is_hidden_from_every_filtered_read`, where it is
    measured against the reads rather than inferred from a NULL.
    """
    draft = db.draft_message(recipient="+15550009", body="drafted, never sent",
                             self_number="+15550000")
    rows = db.conn.rows("SELECT group_id FROM signal.messages WHERE id = ?",
                        (draft["id"],))
    assert rows and rows[0]["group_id"] is None


# --------------------------------------------------------------------------- #
# Drafting TO a group — the outbound path doing what the inbound path already did
# --------------------------------------------------------------------------- #
SELF_NUMBER = "+15550000"


def _group_address(raw: bytes) -> str:
    """The API's `id` field for a raw group id, built by the SAME rule as prod.

    Built from `_signal_db._group_id_to_address` deliberately: a hand-rolled
    double-b64 here would be a second encoder, and a test that carries its own
    copy of the rule cannot fail when the shipped rule regresses. The ROUND-TRIP
    against a literal is pinned separately, below, so this is not circular.
    """
    return _signal_db._group_id_to_address(raw)


def test_the_group_address_encoding_round_trips_against_a_LITERAL():
    """Non-circular anchor: the double encoding, spelled out once.

    Every other group-draft test builds its address through
    `_group_id_to_address`. If that function were wrong, those tests would all
    agree with each other and with nothing else. This one pins the shape against
    a literal computed the way the API documents it — `group.` + b64(b64(raw)) —
    and pins the decode as its exact inverse.
    """
    raw = b"\x55" * 32
    literal = "group." + base64.b64encode(base64.b64encode(raw)).decode()
    assert _signal_db._group_id_to_address(raw) == literal
    assert _signal_db._group_address_to_id(literal) == raw
    # The shape the API actually emits, generated at module scope, decodes too.
    assert _signal_db._group_address_to_id(GROUP_ID_FIELD_SHAPE) == b"\x44" * 32


def test_a_group_draft_is_LINKED_to_the_group_row_not_a_contact(db):
    """Criterion 1 + 2: `group_id` set, `dest_contact_id` NULL, no phantom contact.

    The defect: `draft_message` never named `group_id` and resolved ANY recipient
    through `upsert_contact`, so a group draft stored `group_id = NULL` AND
    minted a fake person whose `phone_number` was the group address.
    """
    group_row = db.upsert_group(group_id=GROUP_KEEP, name="")
    before = db.conn.rows(
        "SELECT count(*) AS n FROM signal.contacts "
        "WHERE phone_number LIKE 'group.%'")[0]["n"]

    draft = db.draft_message(recipient=_group_address(GROUP_KEEP),
                             body="pool car is booked", self_number=SELF_NUMBER)

    row = db.conn.rows(
        "SELECT group_id, dest_contact_id FROM signal.messages WHERE id = ?",
        (draft["id"],))[0]
    assert row["group_id"] == group_row, (
        "the draft is not linked to the group row the inbound path uses — "
        "`_muted_predicate` keys on m.group_id and cannot see it")
    assert row["dest_contact_id"] is None, (
        "a group draft addressed a CONTACT; the phantom-contact defect is back")
    after = db.conn.rows(
        "SELECT count(*) AS n FROM signal.contacts "
        "WHERE phone_number LIKE 'group.%'")[0]["n"]
    assert after == before, (
        f"drafting to a group created {after - before} phantom contact(s) — a "
        f"fake person whose phone_number is a group address")
    assert draft["group_id"] == group_row, "the returned dict must say so too"


def test_drafting_to_an_UNSEEN_group_WARNS_loudly_on_stderr(db, capsys):
    """🔴 A canonically-encoded but WRONG id cannot be validated — so SAY SO.

    The strict decoder rejects a MALFORMED address. It cannot reject a wrong
    one: any well-formed 32 bytes decodes perfectly, `upsert_group` mints a
    group nobody is in, and the send goes nowhere while reporting success. That
    is the silent-zero shape `mute` was hardened against (it EXITS 4 rather than
    report a mute that hides nothing).

    A WARNING, not a refusal: drafting to a not-yet-ingested group is legitimate
    on a forward-only pipeline, and refusing would make that group undraftable.
    """
    capsys.readouterr()                       # discard anything already buffered
    draft = db.draft_message(recipient=_group_address(GROUP_UNSEEN),
                             body="into the void?", self_number=SELF_NUMBER)
    err = capsys.readouterr().err
    assert "WARNING" in err and "matched NO stored group" in err, (
        f"drafting to an unseen group said nothing on stderr: {err!r}")
    assert _group_address(GROUP_UNSEEN) in err, (
        "the warning must name the offending address, or the operator cannot "
        "tell WHICH draft it is about")
    assert draft["group_created"] is True, (
        "the returned dict must also report it, so a programmatic caller that "
        "never reads stderr can still branch on it")


def test_drafting_to_a_KNOWN_group_is_SILENT(db, capsys):
    """The other half — without this, a warning printed unconditionally passes.

    🔴 This is the control that makes the warning INFORMATIVE rather than
    decorative: a warning on every draft is one an operator learns to ignore,
    which is indistinguishable from no warning at all.
    """
    db.upsert_group(group_id=GROUP_KEEP, name="")
    capsys.readouterr()
    draft = db.draft_message(recipient=_group_address(GROUP_KEEP),
                             body="into a real conversation",
                             self_number=SELF_NUMBER)
    err = capsys.readouterr().err
    assert err == "", (
        f"drafting to a group that IS stored warned anyway: {err!r}. A warning "
        f"that fires every time carries no information.")
    assert draft["group_created"] is False


def test_a_DM_draft_never_reports_group_created(db, capsys):
    """A person is not a group — the flag must not leak into the DM path."""
    capsys.readouterr()
    draft = db.draft_message(recipient="+15550111", body="hello",
                             self_number=SELF_NUMBER)
    assert draft["group_created"] is False
    assert capsys.readouterr().err == ""


def test_drafting_to_an_UNSEEN_group_creates_it_rather_than_refusing(db):
    """Criterion 1's second half: `upsert_group`, not a lookup that refuses.

    A group can be drafted to before its first message has been ingested (the
    pipeline is forward-only). Refusing would make that group undraftable; the
    inbound path creates the row the same way.
    """
    assert db.conn.rows("SELECT count(*) AS n FROM signal.groups")[0]["n"] == 0

    draft = db.draft_message(recipient=_group_address(GROUP_UNSEEN),
                             body="first thing ever said here",
                             self_number=SELF_NUMBER)

    groups = db.conn.rows("SELECT id, group_id FROM signal.groups")
    assert len(groups) == 1, groups
    assert bytes(groups[0]["group_id"]) == GROUP_UNSEEN
    assert draft["group_id"] == groups[0]["id"]


@pytest.mark.parametrize("bad", [
    "group.",                       # prefix and nothing else
    "group.not!base64",             # not base64 at all
    "group." + base64.b64encode(b"Team").decode(),          # a DISPLAY NAME
    "group." + base64.b64encode(
        base64.b64encode(b"\x77" * 7)).decode(),            # 7 bytes: not 16/32
])
def test_a_MALFORMED_group_address_is_refused_and_stores_nothing(db, bad):
    """A bad address must not become a group, a contact, or a draft.

    🔴 The failure mode this forbids is the one the old code had in a new shape:
    `upsert_contact(phone_number=<garbage>)` succeeded for ANYTHING, so a typo
    produced a draft that reported success and could never be delivered. The
    strict decoder refuses instead — and the assertions below check the STORE,
    not just the exception, because a refusal that has already written a row is
    not a refusal.
    """
    with pytest.raises(ValueError):
        db.draft_message(recipient=bad, body="should never be stored",
                         self_number=SELF_NUMBER)
    assert db.conn.rows("SELECT count(*) AS n FROM signal.groups")[0]["n"] == 0
    assert db.conn.rows("SELECT count(*) AS n FROM signal.messages")[0]["n"] == 0
    assert db.conn.rows(
        "SELECT count(*) AS n FROM signal.contacts "
        "WHERE phone_number LIKE 'group.%'")[0]["n"] == 0


def _cli(monkeypatch, db, argv):
    """Run `consumer.main(argv)` against the hermetic db. Returns the exit code."""
    class _Ctx:
        def __enter__(self_inner):
            return db

        def __exit__(self_inner, *exc):
            return False

    monkeypatch.setattr(consumer, "SignalDB", lambda *a, **kw: _Ctx())
    monkeypatch.setattr(db, "ensure_schema", lambda *a, **kw: None, raising=False)
    return consumer.main(argv)


def test_draft_REFUSES_a_bad_recipient_with_exit_3_like_its_siblings(
        db, monkeypatch, capsys):
    """🟢 Consistency: a refused `draft` must exit like a refused `mute`/`send`.

    `mute` catches ValueError and `send`/`reconcile` catch SendGateError, both
    printing `refused: …` and exiting 3. The `draft` branch alone let a bad
    `--to` escape as an uncaught traceback and exit 1 — indistinguishable, to a
    caller, from the interpreter dying for an unrelated reason.
    """
    rc = _cli(monkeypatch, db,
              ["draft", "--to", "group.not!base64", "--body", "x",
               "--from-number", SELF_NUMBER])
    err = capsys.readouterr().err
    assert rc == 3, f"expected exit 3 like `mute`/`send`, got {rc}"
    assert err.startswith("refused: "), err
    assert "group.not!base64" in err, (
        "the refusal must name the offending address")
    # …and nothing was written on the way out.
    assert db.conn.rows("SELECT count(*) AS n FROM signal.messages")[0]["n"] == 0


def test_draft_does_NOT_exit_3_on_a_GOOD_recipient(db, monkeypatch, capsys):
    """Positive control — without it, `return 3` unconditionally would pass."""
    stub = types.ModuleType("clawgate")
    stub.emit_draft_task = lambda **kw: None
    monkeypatch.setitem(sys.modules, "clawgate", stub)

    rc = _cli(monkeypatch, db,
              ["draft", "--to", _group_address(GROUP_KEEP), "--body", "hi",
               "--from-number", SELF_NUMBER])
    assert rc != 3, capsys.readouterr()
    assert db.conn.rows("SELECT count(*) AS n FROM signal.messages")[0]["n"] == 1


def test_a_BARE_internal_id_is_REFUSED_not_turned_into_a_phantom_contact(db):
    """🔴 The `mute`/`draft` mix-up — the defect this whole PR removes, by the back door.

    `_looks_like_group_address` is a bare `group.` PREFIX test. A bare
    `internal_id` — the form `mute` takes, and which SKILL.md had begun telling
    operators about — has no prefix, is not a uuid, and fell through to
    `upsert_contact(phone_number=...)`. Measured before this fix:

        BARE internal_id -> ACCEPTED, dest_contact_id=2, group_id=None,
                            group_created=False, stderr=''
        contacts: [(1,'+15550000'), (2,'zX3i…2IQ=')]

    A phantom contact, `group_id` NULL, NO warning, exit 0 — and a row no mute
    can ever see, because `not_excluded()` keys on `group_id`. Worse than the
    original bug, because the docs now point at it.
    """
    internal_id = base64.b64encode(GROUP_KEEP).decode()
    assert not internal_id.startswith("group."), "fixture must be the BARE form"

    with pytest.raises(ValueError) as exc:
        db.draft_message(recipient=internal_id, body="should never be stored",
                         self_number=SELF_NUMBER)

    # The refusal must NAME THE FIX, or it is a dead end rather than a correction.
    assert _group_address(GROUP_KEEP) in str(exc.value), (
        f"the refusal does not tell the operator what to pass instead: {exc.value}")

    # 🔴 And it must have written NOTHING. A refusal that already created the
    # phantom is not a refusal.
    assert db.conn.rows(
        "SELECT count(*) AS n FROM signal.contacts "
        "WHERE phone_number = ?", (internal_id,))[0]["n"] == 0, (
        "the bare internal_id was stored as a contact's phone number — that IS "
        "the phantom-contact defect")
    assert db.conn.rows("SELECT count(*) AS n FROM signal.messages")[0]["n"] == 0
    assert db.conn.rows("SELECT count(*) AS n FROM signal.groups")[0]["n"] == 0


def test_a_bare_16_byte_GroupV1_internal_id_is_refused_too(db):
    """The legacy id length is refused on the same footing — 16, not only 32.

    A guard written for GroupV2 only would let the legacy form through into the
    contact fallback, which is the same defect in a shape nobody tested.
    """
    with pytest.raises(ValueError):
        db.draft_message(recipient=base64.b64encode(b"\x99" * 16).decode(),
                         body="legacy", self_number=SELF_NUMBER)
    assert db.conn.rows("SELECT count(*) AS n FROM signal.messages")[0]["n"] == 0


@pytest.mark.parametrize("label,recipient", [
    ("E.164 short", "+15550100"),
    ("E.164 max length", "+123456789012345"),
    ("uuid lower", "550e8400-e29b-41d4-a716-446655440000"),
    ("uuid upper", "550E8400-E29B-41D4-A716-446655440000"),
    ("signal username", "gyro.42"),
    ("display name", "Team"),
])
def test_no_REAL_recipient_shape_is_mistaken_for_a_group_id(db, label, recipient):
    """🔴 The blast radius of the refusal above, MEASURED rather than argued.

    Refusing a whole input shape is only safe if no legitimate recipient can
    land in it. The argument is that a Signal recipient is `+E164` or a uuid and
    neither survives a canonical-base64 round-trip to 16 or 32 bytes — but that
    is reasoning, and this is the measurement, at BOTH E.164 length extremes and
    in both uuid cases. `Team` is here because it is valid base64 that decodes
    to 3 bytes: it must be rejected by LENGTH, not by the alphabet.
    """
    assert not _signal_db._looks_like_bare_group_internal_id(recipient), (
        f"{label} ({recipient!r}) would be refused as a group internal_id — "
        f"the guard is too wide and has broken a legitimate recipient")
    # …and it really does still draft, which the predicate alone cannot show.
    draft = db.draft_message(recipient=recipient, body="ordinary recipient",
                             self_number=SELF_NUMBER)
    assert draft["dest_contact_id"] is not None
    assert draft["group_id"] is None


def test_the_SEND_recipient_is_derived_from_the_group_row_not_a_contact(db):
    """Criterion 3: what `send_approved()` hands `/v2/send` for a group draft.

    The phantom contact was LOAD-BEARING — `get_draft`'s `CASE … END AS
    recipient` read the address back out of it. This is the rewiring that let it
    be deleted, so it is asserted on the value the send path actually uses, and
    with the contacts table proven empty of any group-addressed row so the
    string cannot have come from one.
    """
    address = _group_address(GROUP_KEEP)
    draft = db.draft_message(recipient=address, body="see you at 6",
                             self_number=SELF_NUMBER)

    read_back = db.get_draft(draft["id"])
    assert read_back["recipient"] == address, (
        f"the send path would address {read_back['recipient']!r}, not the group")
    assert db.conn.rows(
        "SELECT count(*) AS n FROM signal.contacts "
        "WHERE phone_number LIKE 'group.%'")[0]["n"] == 0, (
        "the recipient could have come from a phantom contact — this test would "
        "then prove nothing about the derivation")
    # The raw BYTEA id must NOT escape: the CLI json.dumps()es this dict.
    assert "group_signal_id" not in read_back
    import json
    json.dumps(read_back, default=str)


def test_send_and_reconcile_work_END_TO_END_on_a_group_draft(db, monkeypatch):
    """Criterion 3: the whole D3 path, on a group draft, with the wire captured.

    Not "get_draft returns the right string" — the actual approve → send →
    (strand) → reconcile sequence, asserting the recipient that reached the
    transport.
    """
    address = _group_address(GROUP_MUTE)      # distinct from every other case
    draft = db.draft_message(recipient=address, body="ride at seven",
                             self_number=SELF_NUMBER)
    db.approve_draft(draft["id"], approval_ref="clawgate/1")

    seen = {}

    def fake_transmit(auth, *, recipient, body, number):
        seen.update(recipient=recipient, body=body, number=number,
                    draft_id=auth.draft_id)
        return [{"timestamp": "1787331796630"}]

    out = db.send_approved(draft["id"], transmit=fake_transmit)
    assert seen["recipient"] == address, seen
    assert seen["number"] == SELF_NUMBER, (
        "account_number() must still read the SENDING contact, which a group "
        "draft still has")
    assert out["send_state"] == "sent"
    assert out["message_timestamp"] == 1787331796630
    assert out["recipient"] == address

    # …and reconcile, on a SECOND group draft stranded in `sending`.
    other = db.draft_message(recipient=address, body="actually eight",
                             self_number=SELF_NUMBER)
    db.approve_draft(other["id"], approval_ref="clawgate/2")

    def explode(auth, *, recipient, body, number):
        raise RuntimeError("pod killed after the POST")

    with pytest.raises(RuntimeError):
        db.send_approved(other["id"], transmit=explode)
    assert db.get_draft(other["id"])["send_state"] == "sending"
    done = db.reconcile_send(other["id"], outcome="sent",
                             server_timestamp=1787331799999)
    assert done["send_state"] == "sent"
    assert done["recipient"] == address


# One behavioural probe per FILTERED read, keyed by the method's own name so the
# set can be compared against the ledger instead of trusted. Each returns the
# rows that read surfaces FOR THIS DRAFT — so an empty list means "hidden", and a
# non-empty one before the mute is the positive control.
#
# 🔴 THE KEY IS NOT THE PROBE. `set(_MUTE_PROBES) == FILTERED_READS` is invariant
# under SWAPPING TWO BODIES — an audit did exactly that (gave `get_message` the
# `search` probe's body and vice versa) and the whole suite stayed green, 650
# passed, while `get_message` had no behavioural coverage of a group draft at
# all. It is the same set-invariant-under-a-swap shape already fixed once for the
# two ledgers, and deleting a KEY (the half the old mutant covered) was never the
# dangerous direction. `test_each_MUTE_PROBE_actually_calls_the_method_it_is_keyed_under`
# closes it by RUNNING each probe against a recording proxy and asserting which
# read it actually reached — a source-text check would be walked by a probe that
# names the method in a comment.
_MUTE_PROBES = {
    "list_conversations": lambda db, draft, body: [
        r for r in db.list_conversations()
        if r["last_message_timestamp"] == draft["message_timestamp"]],
    "search": lambda db, draft, body: [
        r for r in db.search(body) if r["id"] == draft["id"]],
    "get_message": lambda db, draft, body: [
        r for r in [db.get_message(draft["id"])] if r],
}


class _RecordingReads:
    """Proxy over a real `SignalDB` that records WHICH filtered read was called.

    Everything is delegated, so the probe runs for real and its return value is
    genuine — this records, it does not stub. Only the names in
    `FILTERED_READS` are recorded, because those are the ones a probe is
    supposed to be pinned to.
    """

    def __init__(self, db):
        self._db = db
        self.called: list = []

    def __getattr__(self, name):
        attr = getattr(self._db, name)
        if name in FILTERED_READS and callable(attr):
            def recording(*a, **kw):
                self.called.append(name)
                return attr(*a, **kw)
            return recording
        return attr


def test_each_MUTE_PROBE_actually_calls_the_method_it_is_keyed_under(db):
    """🔴 The probe must EXERCISE the read it is filed under, not just be filed.

    An audit swapped the `get_message` and `search` probe bodies and the suite
    stayed fully green (650 passed, 0 failed): `set(_MUTE_PROBES) ==
    FILTERED_READS` cannot see a swap, and the mute test's own assertions pass
    as long as SOMETHING hides. `get_message` silently lost all behavioural
    coverage of a group draft.

    So each probe is RUN against a recording proxy and must reach exactly the
    method its key names — measured, not read off the source text (a probe that
    merely mentions `db.get_message` in a comment would satisfy a grep).
    """
    db.upsert_group(group_id=GROUP_KEEP, name="")
    draft = db.draft_message(recipient=_group_address(GROUP_KEEP),
                             body="probe pinning fixture", self_number=SELF_NUMBER)

    for name, probe in sorted(_MUTE_PROBES.items()):
        spy = _RecordingReads(db)
        probe(spy, draft, "probe pinning fixture")
        assert spy.called == [name], (
            f"_MUTE_PROBES[{name!r}] exercised {spy.called or 'NO filtered read'} "
            f"instead of exactly [{name!r}]. The dict key claims coverage of "
            f"SignalDB.{name}; swapping two probe bodies leaves the key set "
            f"identical, so that method would silently have no behavioural "
            f"coverage at all.")


class _BlindRecordingReads(_RecordingReads):
    """`_RecordingReads` with the recording removed — the BLINDING mutant.

    An audit produced exactly this: a proxy that still delegates, so every probe
    returns real rows and every downstream assertion passes, while `called`
    stays empty and the pinning test can no longer see anything.
    """

    def __getattr__(self, name):                       # pragma: no cover - via test
        return getattr(self._db, name)


def test_the_probe_pinning_guard_can_go_red(db):
    """🔴 Negative control that EXERCISES the recording path, not list equality.

    The previous version of this test did `_RecordingReads.__new__`, hand-set
    `spy.called = ["search"]`, and asserted `["search"] != ["get_message"]` — a
    tautology about list comparison that never touched the proxy. Its docstring
    claimed it ruled out "the proxy recorded nothing", which it structurally
    could not: under an audit's BLINDING mutant, that control still PASSED while
    the real pinning test went red. A negative control that cannot go red is
    worse than none, because it stops anyone looking.

    This runs the REAL probes through a blinded proxy and requires the pinning
    assertion to become FALSE for every one of them.
    """
    db.upsert_group(group_id=GROUP_KEEP, name="")
    draft = db.draft_message(recipient=_group_address(GROUP_KEEP),
                             body="negative control fixture",
                             self_number=SELF_NUMBER)

    for name, probe in sorted(_MUTE_PROBES.items()):
        blind = _BlindRecordingReads(db)
        rows = probe(blind, draft, "negative control fixture")
        # POSITIVE half: the probe still WORKS through the blind proxy, so the
        # only thing this demonstrates is lost VISIBILITY, not a broken probe.
        assert rows, (
            f"the blinded proxy broke the {name} probe itself, so this control "
            f"is measuring the wrong failure")
        assert blind.called != [name], (
            f"HARNESS BROKEN: the pinning assertion still holds for {name!r} "
            f"against a proxy that records NOTHING — it is not reading `called` "
            f"and would pass against any probe at all")

    # …and the real proxy does record, or the two halves prove nothing together.
    live = _RecordingReads(db)
    _MUTE_PROBES["search"](live, draft, "negative control fixture")
    assert live.called == ["search"], live.called


def test_a_muted_group_draft_is_hidden_from_every_filtered_read(db):
    """🔴 CRITERION 4 — the sharp one. Mute must not be bypassable by drafting.

    `_muted_predicate` keys on `m.group_id`. With `group_id` NULL on every draft
    the predicate matched nothing, so a draft to a MUTED group came back in full
    from every read that believed it was filtering. Mute is the privacy control
    here.

    🔴 STRUCTURE ALONE IS NOT ENOUGH, WHICH IS WHY THIS EXISTS. The partition
    test checks that every method in FILTERED_READS really CALLS the predicate.
    A call is not an effect: the predicate could be ANDed onto a subquery that
    is later discarded, or parenthesised so it never binds. Only running the
    reads against a muted row settles it. Together the two mean "listed as
    filtered" implies "measurably filters".

    `_MUTE_PROBES` is keyed by method name and asserted equal to FILTERED_READS
    below, so a read surface added to that ledger without a behavioural probe
    fails HERE rather than being quietly uncovered. 🔴 That assertion covers the
    KEY SET only — it is invariant under swapping two probe BODIES, which an
    audit demonstrated with a fully green suite.
    `test_each_MUTE_PROBE_actually_calls_the_method_it_is_keyed_under` is the
    other half, and the implication in the paragraph above holds only because
    BOTH exist.

    Each read gets a POSITIVE CONTROL first: it must SEE the draft before the
    mute. Without that, "hidden" is indistinguishable from a query wired to
    nothing, and this whole test would pass against a `draft_message` that
    stored no row at all.
    """
    assert set(_MUTE_PROBES) == FILTERED_READS, (
        f"every method in FILTERED_READS needs a behavioural probe here, or "
        f"this test covers less than its name claims.\n"
        f"  filtered but unprobed: {sorted(FILTERED_READS - set(_MUTE_PROBES))}\n"
        f"  probed but not filtered: {sorted(set(_MUTE_PROBES) - FILTERED_READS)}")

    db.upsert_group(group_id=GROUP_MUTE, name="")
    body = "quarterly kestrel rendezvous"          # distinct from every _seed body
    draft = db.draft_message(recipient=_group_address(GROUP_MUTE), body=body,
                             self_number=SELF_NUMBER)

    # 🔴 EVERY probe identifies the DRAFT, never the group row. Keying
    # `list_conversations` on `group_row_id` looked equivalent and was not: on
    # the BROKEN code the draft lands in the phantom contact's DM thread, so
    # that probe returned [] before the mute and this test failed on its own
    # positive control — reporting "the harness is blind" where the real finding
    # is "the mute was bypassed". The provisional timestamp is negative and
    # unique to this draft, so it names whichever conversation the row landed
    # in, in both worlds.
    #
    # 🔴 Its ONE precondition, asserted rather than assumed: this draft must be
    # the newest message in whatever conversation it lands in, because
    # `list_conversations` reports `max(message_timestamp)` per conversation and
    # a draft's provisional timestamp is NEGATIVE — so any real (positive) server
    # timestamp in the same group would win the max and the probe would go blind.
    # Measured on real Postgres while writing this: a second, already-sent draft
    # in the same group made this probe report 0 before the mute.
    assert db.conn.rows(
        "SELECT count(*) AS n FROM signal.messages")[0]["n"] == 1, (
        "the probe below reads max(message_timestamp) per conversation and this "
        "draft's is NEGATIVE — another message in this group would mask it")

    def probe() -> dict:
        return {name: fn(db, draft, body) for name, fn in _MUTE_PROBES.items()}

    before = probe()
    for name, rows in before.items():
        assert rows, (
            f"POSITIVE CONTROL FAILED: {name} cannot see the group draft even "
            f"BEFORE the mute, so its 'hidden' assertion below would be vacuous")

    db.exclude_group(GROUP_MUTE, note="muted by the operator")

    after = probe()
    for name, rows in after.items():
        assert rows == [], (
            f"MUTE BYPASSED: {name} returned a draft to a MUTED group "
            f"({len(rows)} row(s)). `not_excluded()` keys on m.group_id, so a "
            f"draft stored with group_id NULL walks straight through it.")

    # Muting HIDES; it must not have deleted the operator's own draft, and the
    # outbound surface (exempt, deliberately) must still reach it.
    assert db.conn.rows("SELECT count(*) AS n FROM signal.messages "
                        "WHERE id = ?", (draft["id"],))[0]["n"] == 1
    assert db.get_draft(draft["id"])["body"] == body


def test_group_drafts_appear_in_the_GROUP_conversation_not_a_phantom_DM(db):
    """Criterion 2's other consequence: the conversation reads two-sided.

    With `group_id` NULL the draft grouped by `dest_contact_id` — the phantom —
    so the group thread showed inbound messages only and a fake one-person DM
    appeared beside it.
    """
    ids = _seed(db)
    keep_row = ids["_rows"]["keep"]
    before = {r["group_row_id"]: r["message_count"]
              for r in db.list_conversations()}

    db.draft_message(recipient=_group_address(GROUP_KEEP), body="on my way",
                     self_number=SELF_NUMBER)

    after = {r["group_row_id"]: r["message_count"]
             for r in db.list_conversations()}
    assert after[keep_row] == before[keep_row] + 1, (
        "the group conversation did not count our own outbound message")
    assert set(after) == set(before), (
        f"a NEW conversation appeared for a group draft — that is the phantom "
        f"contact's DM thread: {set(after) - set(before)}")


def test_the_ledger_would_notice_an_unfiltered_read():
    """Negative control on the ledger itself: can it go red?

    Without this, `test_the_ledger_...` passing proves only that the CURRENT
    code matches the CURRENT list — not that the check can ever fail.
    """
    found = _read_methods_touching_messages()
    pretend_new = found | {"list_archived_messages"}
    assert pretend_new != (FILTERED_READS | set(EXEMPT_READS))


# --------------------------------------------------------------------------- #
# Operator input decoding
# --------------------------------------------------------------------------- #
def test_decode_internal_id_round_trips_a_canonical_id():
    text = base64.b64encode(GROUP_MUTE).decode()
    assert consumer._decode_internal_id(text) == GROUP_MUTE
    assert consumer._fmt_group_id(GROUP_MUTE) == text


def test_fmt_group_id_emits_the_STANDARD_alphabet_not_urlsafe():
    """🔴 `GROUP_MUTE` cannot see this — its encoding contains no `+` or `/`.

    A mutant switching `_fmt_group_id` to `urlsafe_b64encode` SURVIVED, because
    every fixture that reached it was a repeated byte whose base64 has neither
    character. The output is what an operator pastes back into `mute`, so a
    urlsafe rendering round-trips through our own tolerant decoder and diverges
    from what the Signal API prints. Use bytes that CAN tell the two apart.
    """
    raw = b"\xfb\xff" * 16
    std = base64.b64encode(raw).decode()
    assert "+" in std and "/" in std, (
        "HARNESS BROKEN: this fixture cannot distinguish the two alphabets")
    assert consumer._fmt_group_id(raw) == std


def test_decode_internal_id_tolerates_the_urlsafe_alphabet():
    raw = b"\xfb\xff" * 16                      # encodes to '+/' repeated
    std = base64.b64encode(raw).decode()
    urlsafe = base64.urlsafe_b64encode(raw).decode()
    assert "+" in std or "/" in std, (
        "HARNESS BROKEN: this fixture does not exercise the +/ characters, so "
        "the urlsafe branch is never reached and this test proves nothing")
    assert consumer._decode_internal_id(urlsafe) == raw


@pytest.mark.parametrize("bad", [
    "",
    "   ",
    "not base64 at all!",
    GROUP_ID_FIELD_SHAPE,          # the `id` field, not `internal_id`
    "Team",                        # a display name that IS valid base64
    "deadbeef",                    # 6 bytes — decodes cleanly, is not a group id
])
def test_decode_internal_id_refuses_anything_non_canonical(bad):
    """🔴 The ingest decoder falls back to `str.encode()` rather than failing.

    That is right for a frame (never kill ingest over one field) and wrong for
    operator input, where it turns a typo into a mute of 32 bytes that match
    nothing — a filter that reports success and hides nothing. This pins the
    strict half; `_decode_group_id` keeps the lenient half.
    """
    with pytest.raises(ValueError):
        consumer._decode_internal_id(bad)


def test_a_NON_CANONICAL_32_byte_encoding_is_refused():
    """🔴 The only case that reaches the round-trip check — found by a mutant.

    Adding the 16/32 length check made every other bad input die on LENGTH, so
    disabling the round-trip check entirely left the suite GREEN: a guard with
    no case that reaches it. (Round 1's battery killed that mutant; round 2's
    did not, because round 2's own fix had made it unreachable. A fix round
    resets the gate.)

    Base64's last data character carries only 4 significant bits for a 2-byte
    tail, so the low 2 bits are ignored on decode: `v` and `v+1` decode to the
    SAME 32 bytes while only `v` re-encodes to itself. That is a string of the
    right length, decoding to the right size, which is still not what the API
    emitted — exactly what the round-trip check is for and nothing else catches.
    """
    alphabet = ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                "0123456789+/")
    canonical = base64.b64encode(GROUP_MUTE).decode()
    assert canonical.endswith("=") and canonical[-2] in alphabet
    v = alphabet.index(canonical[-2])
    assert v % 4 == 0, "HARNESS BROKEN: canonical tail already has low bits set"
    variant = canonical[:-2] + alphabet[v + 1] + "="

    # Positive control: it really is the same 32 bytes, so this is NOT caught by
    # the length check — it can only be caught by the round trip.
    assert base64.b64decode(variant) == GROUP_MUTE
    assert len(base64.b64decode(variant)) == 32
    assert variant != canonical

    with pytest.raises(ValueError, match="canonical"):
        consumer._decode_internal_id(variant)


def test_the_two_decoders_genuinely_disagree_on_the_same_input():
    """Positive control for the paragraph above: the leniency is real."""
    bad = "not base64 at all!"
    assert consumer._decode_group_id(bad) == bad.encode()
    with pytest.raises(ValueError):
        consumer._decode_internal_id(bad)


# --------------------------------------------------------------------------- #
# The group NAME — found while checking the claim that names are never stored
# --------------------------------------------------------------------------- #
def _group_envelope(*, group_info: dict) -> dict:
    return {
        "timestamp": 9000,
        "sourceUuid": "55555555-5555-4555-8555-555555555555",
        "sourceNumber": "+15550005",
        "sourceName": "Erin",
        "dataMessage": {"message": "hello", "timestamp": 9000,
                        "groupInfo": group_info},
    }


def test_the_parser_reads_groupName_which_is_what_real_envelopes_carry():
    """🔴 Regression: `groupInfo.name` was read and is never present.

    Measured on the live store before this fix: 34 of 34 stored group envelopes
    carried a non-empty `groupInfo.groupName`, and NONE carried `groupInfo.name`
    — so every group was stored with an empty name. Nothing caught it because no
    test and no output ever asserted on a group name.
    """
    parsed = consumer.parse_envelope(_group_envelope(group_info={
        "type": "DELIVER", "groupId": "IiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiI=",
        "revision": 3, "groupName": "Widget Testers",
    }))
    assert parsed.message["group_name"] == "Widget Testers"


def test_groupName_WINS_over_the_legacy_spelling():
    """The two keys hold DISTINCT values here, deliberately.

    A fixture setting both to the same string collapses the two operand orders
    into identical output, and an order-swap mutant then survives a fully green
    suite — which is exactly how two such mutants survived 416 tests in this
    same module's history.
    """
    parsed = consumer.parse_envelope(_group_envelope(group_info={
        "type": "DELIVER", "groupId": "IiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiI=",
        "revision": 3, "groupName": "Widget Testers", "name": "Sprocket Fans",
    }))
    assert parsed.message["group_name"] == "Widget Testers"


def test_the_legacy_spelling_is_still_honoured_when_it_is_the_only_one():
    parsed = consumer.parse_envelope(_group_envelope(group_info={
        "type": "DELIVER", "groupId": "IiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiI=",
        "revision": 3, "name": "Sprocket Fans",
    }))
    assert parsed.message["group_name"] == "Sprocket Fans"


def test_a_LATER_nameless_envelope_does_not_WIPE_a_stored_group_name(db):
    """🔴 Two envelopes, because one cannot see this.

    `upsert_group` binds `name or ""` (the column is NOT NULL), so `EXCLUDED.name`
    is never NULL — it is `''` — and the original `COALESCE(EXCLUDED.name, …)`
    could therefore never fire. It was dead code that READ as protection: a later
    frame for a known group carrying no name (a sync echo, an edit, a plain
    DELIVER with only `groupId`) overwrote the stored name with `''`. The name
    would appear and then silently revert, and nothing asserted on group names
    over time, so a mutant DELETING the whole COALESCE survived all 508 tests.
    """
    gid = "IiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiI="
    db.upsert_message(consumer.parse_envelope(_group_envelope(group_info={
        "type": "DELIVER", "groupId": gid, "revision": 3,
        "groupName": "Widget Testers"})).message)
    assert "Widget Testers" in {r["group_name"] for r in db.list_conversations()}

    # A second frame for the SAME group, carrying no name at all.
    second = _group_envelope(group_info={"type": "DELIVER", "groupId": gid,
                                         "revision": 4})
    second["timestamp"] = 9100
    second["dataMessage"]["timestamp"] = 9100
    db.upsert_message(consumer.parse_envelope(second).message)

    assert "Widget Testers" in {r["group_name"] for r in db.list_conversations()}, (
        "a nameless envelope wiped the stored group name back to ''")


def test_a_stored_group_keeps_the_name_the_envelope_carried(db):
    """End-to-end through `upsert_message` -> `upsert_group`, not just the parser.

    The parser and the DB layer were each clean on their own the last time this
    module shipped a defect; the bug lived in the seam. One fixture builds both.
    """
    parsed = consumer.parse_envelope(_group_envelope(group_info={
        "type": "DELIVER", "groupId": "IiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiI=",
        "revision": 3, "groupName": "Widget Testers",
    }))
    db.upsert_message(parsed.message)
    names = {r["group_name"] for r in db.list_conversations()}
    assert "Widget Testers" in names


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
def test_the_mute_table_is_in_the_schema_and_survives_translation():
    ddl = [s for s in _signal_db.SCHEMA_STATEMENTS
           if "excluded_groups" in s and "CREATE TABLE" in s]
    assert len(ddl) == 1, "the mute table must be declared exactly once"
    import fakepg
    translated = fakepg.translate_ddl(ddl[0])
    assert translated and "BLOB" in translated, (
        "BYTEA must translate to BLOB or the hermetic suite is testing a "
        "different column type than production")


def test_the_mute_table_is_keyed_on_the_binary_id_not_the_name():
    """The mute list keys on the binary group id, never on a NAME.

    🔴 RETRACTED REASON (2026-08-21). This docstring used to open with
    "`signal.groups.name` is EMPTY for every group this consumer has stored",
    and rest the whole argument on it. That is FALSE: `upsert_group()` persists
    the envelope's `groupName`, `test_a_stored_group_keeps_the_name_the_envelope_carried`
    two functions below pins exactly that, and the live store has populated
    names. The claim was already disproved by its own file.

    The advice survives on a reason that does not decay: a NAME IS NOT AN
    IDENTITY. Any member can rename a group at any time, two groups can share a
    name, and the stored value is only as fresh as the last envelope that
    carried one — so a name-keyed mute silently stops hiding the moment someone
    renames the conversation. The binary id never changes.
    """
    ddl = next(s for s in _signal_db.SCHEMA_STATEMENTS
               if "excluded_groups" in s and "CREATE TABLE" in s)
    assert re.search(r"group_id\s+BYTEA\s+PRIMARY KEY", ddl), ddl
    # 🔴 Read the COLUMN LIST, not the statement. The previous version did
    # `ddl.split("(", 1)[1]`, which split on a `(` inside the COMMENT block
    # above the DDL, so `body` was prose; and its assertion was
    # `X or "group_id" in body` whose right side is unconditionally true. Two
    # independent ways of asserting nothing, in one line, both invisible.
    cols = ddl[ddl.index("CREATE TABLE"):]
    cols = cols[cols.index("(") + 1:cols.rindex(")")]
    assert "group_id" in cols
    assert "name" not in cols, (
        f"the mute list must not key on a group NAME — column list was: {cols}")
