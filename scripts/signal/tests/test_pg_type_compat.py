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


@pytest.mark.skipif(not os.environ.get("SIGNAL_PG_DSN"),
                    reason="needs a real Postgres (SIGNAL_PG_DSN); SQLite cannot "
                           "reproduce Postgres static type checking")
def test_draft_query_runs_on_real_postgres():
    """The actual `_draft_or_raise` SQL must plan on a real server.

    Red at base: raises `DatatypeMismatch: COALESCE types uuid and text cannot
    be matched`. Green once `signal_uuid` is cast.
    """
    import psycopg2  # imported lazily: absent in the default hermetic env

    sql = (
        "SELECT m.id, "
        "       CASE WHEN d.is_placeholder THEN d.phone_number "
        "            ELSE COALESCE(d.signal_uuid::text, d.phone_number) "
        "       END AS recipient "
        "FROM signal.messages m "
        "LEFT JOIN signal.contacts d ON d.id = m.dest_contact_id "
        "WHERE m.id = %s AND m.is_outbound AND m.send_state IS NOT NULL"
    )
    with psycopg2.connect(os.environ["SIGNAL_PG_DSN"]) as conn:
        with conn.cursor() as cur:
            # A row need not exist; planning is what type-checks.
            cur.execute(sql, (-1,))
            cur.fetchall()


_COALESCE_WITH_UUID = re.compile(r"COALESCE\s*\((?:[^()]|\([^()]*\))*signal_uuid"
                                 r"(?:[^()]|\([^()]*\))*\)", re.I)


def _uncast_coalesces(sql: str) -> list[str]:
    """COALESCE expressions naming `signal_uuid` with no cast around it.

    Accepts BOTH spellings deliberately: ANSI `CAST(x AS text)` (which SQLite
    and Postgres both parse) and Postgres-only `x::text`. The shipped SQL must
    use the ANSI form — `::` is unparseable by the SQLite substrate and turns
    53 hermetic tests red — but a guard that accepted only one spelling would
    fail on a legitimate refactor to the other.
    """
    out = []
    for m in _COALESCE_WITH_UUID.finditer(sql):
        expr = m.group(0)
        stripped = re.sub(r"CAST\s*\(\s*[\w.]*signal_uuid\s+AS\s+\w+\s*\)", "", expr, flags=re.I)
        stripped = re.sub(r"[\w.]*signal_uuid\s*::\s*\w+", "", stripped, flags=re.I)
        if re.search(r"signal_uuid", stripped, re.I):
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
    src = _source()
    assert "signal_uuid::" not in src, (
        "found a Postgres-only `::` cast on signal_uuid. SQLite cannot parse it, "
        "so the hermetic suite goes red. Use CAST(signal_uuid AS text).")


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
