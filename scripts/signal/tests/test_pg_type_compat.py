"""Postgres TYPE compatibility for hand-written SQL in `_signal_db.py`.

🔴 WHY THIS FILE EXISTS, AND WHAT IT CANNOT DO.

The rest of this suite runs on `fakepg.SqliteConn` — "a REAL relational engine
standing in for Postgres". It faithfully reproduces the properties those tests
are about. It does NOT reproduce Postgres's *static type checking*, because
SQLite is dynamically typed.

That gap shipped a real outage. `_draft_or_raise()` carried:

    CASE WHEN d.is_placeholder THEN d.phone_number
         ELSE COALESCE(d.signal_uuid, d.phone_number)

`contacts.signal_uuid` is `uuid`; `contacts.phone_number` is `text`. Postgres
type-checks the WHOLE `CASE` regardless of which branch would execute, so this
raised `DatatypeMismatch` for EVERY draft — and `approve()`, `send()` and
`reconcile()` all reach `_draft_or_raise()`. The D3 send path had therefore
never worked against production Postgres, while 387 hermetic tests stayed green
because SQLite accepts the uncast form. Measured 2026-08-21: the outbound
population was `pending: 1` and nothing else — nothing had ever been sent.

Two guards, and they are deliberately different in kind:

1. `test_draft_query_runs_on_real_postgres` — the REAL one. Executes the actual
   SQL against a real server. Skips without `SIGNAL_PG_DSN`, so it is honest
   about being unavailable rather than passing vacuously.

2. `test_signal_uuid_is_never_coalesced_without_a_cast` — a SPELLED backstop,
   and labelled as such. It reads source text, so it is walkable by rewriting
   the expression in another shape (a `||`, a nested CASE, a different column
   pair). It exists because guard 1 cannot run in the default gate, NOT because
   it is sufficient. Do not mistake it for type checking.

The durable fix is a Postgres-backed tier for the hand-written SQL. Until that
exists, this file is the seam and its limits are stated rather than implied.
"""
from __future__ import annotations

import os
import re
import pathlib

import pytest

MODULE = pathlib.Path(__file__).resolve().parents[1] / "_signal_db.py"


def _source() -> str:
    return MODULE.read_text(encoding="utf-8")


def _draft_select_sql() -> str:
    """The SELECT `_draft_or_raise()` actually issues, EXTRACTED FROM SOURCE.

    🔴 An earlier version of this test executed a HAND-COPIED copy of the query
    — and spelled it `signal_uuid::text`, the exact spelling its sibling test
    forbids shipping. A guard that runs its own copy of the SQL cannot fail when
    the real SQL regresses; it only ever tests the copy. Extract, never
    transcribe.
    """
    src = _source()
    # Anchor on fields UNIQUE to the draft SELECT. An earlier version anchored on
    # `SELECT m.id, m.message_timestamp` and matched a DIFFERENT query first —
    # so the "real Postgres" guard would have planned the wrong statement and
    # stayed green while `get_draft` regressed.
    m = re.search(r'"""\s*(SELECT m\.id, m\.message_timestamp[^"]*?m\.send_state,'
                  r'[^"]*?dest_contact_id[^"]*?)"""', src, re.S)
    assert m, ("could not locate the _draft_or_raise SELECT in the source — the "
               "query moved or was reshaped, and this guard is now testing "
               "nothing. Fix the extraction rather than deleting the test.")
    return m.group(1)


def test_the_extractor_finds_the_real_query():
    """Positive control for the extraction itself.

    Without this, a regex that silently matched nothing would make the Postgres
    test below execute an empty string and pass.
    """
    sql = _draft_select_sql()
    assert "signal.messages" in sql and "dest_contact_id" in sql, sql[:200]
    assert "COALESCE" in sql, "the extracted SQL is not the draft SELECT"
    # 🔴 It must be the DRAFT select, not a look-alike: `send_state IS NOT NULL`
    # is what distinguishes a draft from a device-sync echo.
    assert "send_state IS NOT NULL" in sql, (
        "extracted a query that is not `get_draft`'s — anchor the extraction "
        f"more tightly. Got: {sql[:200]!r}")


@pytest.mark.skipif(not os.environ.get("SIGNAL_PG_DSN"),
                    reason="needs a real Postgres (SIGNAL_PG_DSN); SQLite cannot "
                           "reproduce Postgres static type checking")
def test_draft_query_runs_on_real_postgres():
    """The actual `_draft_or_raise` SQL — read from source — must plan on a real
    server.

    Red at base: `DatatypeMismatch: COALESCE types uuid and text cannot be
    matched`. Green once `signal_uuid` is cast.
    """
    import psycopg2  # imported lazily: absent in the default hermetic env

    with psycopg2.connect(os.environ["SIGNAL_PG_DSN"]) as conn:
        with conn.cursor() as cur:
            # A row need not exist; PLANNING is what type-checks.
            cur.execute(_draft_select_sql(), (-1,))
            cur.fetchall()


# Any SQL construct that can put `signal_uuid` (uuid) in a common-type position
# with a text column. COALESCE was the shipped bug; an audit confirmed BOTH of
# these refactors also raise DatatypeMismatch on the live server and walked a
# COALESCE-only guard:
#     CASE WHEN d.signal_uuid IS NOT NULL THEN d.signal_uuid ELSE d.phone_number
#     COALESCE(NULLIF(d.signal_uuid, NULL), d.phone_number)
# So match the HAZARD (a type-unifying construct naming signal_uuid), not one
# spelling of it.
#
# 🔴 `CASE` must be anchored as `CASE WHEN` and BOUNDED. An earlier version used
# a bare `\bCASE\b` with re.S, which matched the English word "case." in a
# docstring and ran to a distant `END`, swallowing 17 KB of source into one
# "offending expression". A guard that over-matches is as useless as one that
# under-matches — it just fails noisily instead of silently.
_TYPE_UNIFYING = re.compile(
    r"(?:COALESCE|NULLIF|GREATEST|LEAST)\s*\((?:[^()]|\([^()]*\))*signal_uuid"
    r"(?:[^()]|\([^()]*\))*\)"
    r"|CASE\s+WHEN(?:(?!\bEND\b)[\s\S]){0,800}?\bsignal_uuid\b"
    r"(?:(?!\bEND\b)[\s\S]){0,800}?\bEND\b",
    re.I)

# SQL `--` comments in this module deliberately DISCUSS `signal_uuid` (they
# explain this very hazard). Strip them before matching, or the explanation
# trips the guard that the explanation is about.
_SQL_COMMENT = re.compile(r"--[^\n]*")


def _uncast_coalesces(sql: str) -> list[str]:
    """COALESCE expressions naming `signal_uuid` with no cast around it.

    Accepts BOTH spellings deliberately: ANSI `CAST(x AS text)` (which SQLite
    and Postgres both parse) and Postgres-only `x::text`. The shipped SQL must
    use the ANSI form — `::` is unparseable by the SQLite substrate and turns
    53 hermetic tests red — but a guard that accepted only one spelling would
    fail on a legitimate refactor to the other.
    """
    out = []
    sql = _SQL_COMMENT.sub("", sql)
    for m in _TYPE_UNIFYING.finditer(sql):
        expr = m.group(0)
        stripped = re.sub(r"CAST\s*\(\s*[\w.]*signal_uuid\s+AS\s+[\w()\d ]+\)", "",
                          expr, flags=re.I)
        # `::` is tolerated by the matcher (a legitimate refactor) even though a
        # sibling test forbids SHIPPING it — allow a space, which Postgres does.
        stripped = re.sub(r"[\w.]*signal_uuid\s*::\s*\w+", "", stripped, flags=re.I)
        if re.search(r"\bsignal_uuid\b", stripped, re.I):
            out.append(expr)
    return out


def test_signal_uuid_is_never_coalesced_without_a_cast():
    """SPELLED backstop — see the module docstring for why it is not enough."""
    offenders = _uncast_coalesces(_source())
    assert not offenders, (
        "COALESCE mixes the `uuid` column signal_uuid with a text column and "
        "Postgres type-checks the whole expression regardless of branch, so this "
        "raises DatatypeMismatch for EVERY row. Cast it — use ANSI "
        "`CAST(signal_uuid AS text)`, not `::text`, which SQLite cannot parse. "
        f"Offending: {offenders}"
    )


def test_the_shipped_sql_uses_the_ANSI_cast_not_the_pg_only_one():
    """`::text` type-checks on Postgres and is UNPARSEABLE by the SQLite substrate.

    Measured 2026-08-21: spelling it `::text` took the signal suite from
    1 failed / 598 passed to 54 failed / 547 passed. Both gates must stay able
    to run the same SQL.
    """
    # 🔴 Allow whitespace before `::` — Postgres accepts `signal_uuid ::text`,
    # so a bare substring check for "signal_uuid::" is walked by one space.
    offenders = re.findall(r"signal_uuid\s*::", _source(), re.I)
    assert not offenders, (
        "found a Postgres-only `::` cast on signal_uuid. SQLite cannot parse it, "
        "so the hermetic suite goes red (measured: 54 failed / 561 passed). "
        "Use CAST(signal_uuid AS text).")


def test_the_backstop_can_actually_fire():
    """Positive control — a guard nobody has watched go red may match nothing.

    Feeds the known-bad expression AND both good spellings, requiring the guard
    to flag exactly the bad one.
    """
    bad = "COALESCE(d.signal_uuid, d.phone_number)"
    good_ansi = "COALESCE(CAST(d.signal_uuid AS text), d.phone_number)"
    good_pg = "COALESCE(d.signal_uuid::text, d.phone_number)"

    assert _uncast_coalesces(bad) == [bad], (
        "the backstop failed to flag the known-bad expression — it is matching "
        "nothing and would pass against any source at all")
    assert _uncast_coalesces(good_ansi) == [], "false positive on the ANSI cast"
    assert _uncast_coalesces(good_pg) == [], "false positive on the pg cast"
    assert _uncast_coalesces("COALESCE(CAST(d.signal_uuid AS varchar(64)), d.x)") == [], \
        "false positive on a parameterised cast type"


@pytest.mark.parametrize("walk", [
    # Both confirmed by an audit to raise DatatypeMismatch on the LIVE server,
    # and both walked a COALESCE-only guard.
    "CASE WHEN d.signal_uuid IS NOT NULL THEN d.signal_uuid ELSE d.phone_number END",
    "COALESCE(NULLIF(d.signal_uuid, NULL), d.phone_number)",
])
def test_the_backstop_catches_the_refactors_that_walked_it(walk):
    """🔴 Regression guard for finding 5 of the #657 audit.

    A guard that covers one SPELLING of a hazard is walkable by rewriting the
    expression. These two are not hypothetical: both were executed against the
    live database and both failed with DatatypeMismatch.
    """
    assert _uncast_coalesces(walk), (
        f"the backstop does not flag {walk!r}, which raises DatatypeMismatch on "
        f"a real server — it is guarding a spelling, not the hazard")
