"""`/v2/send` response-shape handling — the LIVE server returns a LIST.

🔴 MEASURED 2026-08-21 against the running signal-cli-rest-api (json-rpc mode)::

    POST /v2/send  ->  201  [{"timestamp":"1787331796630"}]

`send_approved()` previously did `(result or {}).get("errors")`, which raises
`AttributeError: 'list' object has no attribute 'get'` — **after the POST has
already succeeded**. That is the worst available failure shape: the message IS
delivered, the caller sees an exception, the draft is stranded in `sending`, and
the natural human response (retry) would send it twice.

Found the only way it could be: by being the first person to push a real draft
through the D3 send path. Every prior test fed a dict, because the fixture
authors read the upstream type (`ds.SendMessageResponse`, an object) rather than
the wire.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import _signal_db as db  # noqa: E402


def test_the_live_list_shape_is_accepted():
    """The exact bytes the live server returned. Red at base: AttributeError."""
    entries = db._normalize_send_response([{"timestamp": "1787331796630"}])
    assert entries == [{"timestamp": "1787331796630"}]
    assert db._server_timestamp(entries[0]) == 1787331796630


def test_a_bare_dict_still_works():
    """Back-compat: upstream documents an object, other modes return one."""
    entries = db._normalize_send_response({"timestamp": "1787331796630"})
    assert db._server_timestamp(entries[0]) == 1787331796630


def test_per_recipient_errors_are_reachable_in_the_list_shape():
    """The errors branch must survive normalisation, not just the happy path."""
    entries = db._normalize_send_response([{"errors": ["boom"], "timestamp": "1"}])
    errors = [entry["errors"] for entry in entries if entry.get("errors")]
    assert errors == [["boom"]]


def test_an_errors_OBJECT_keeps_its_reason_rather_than_collapsing_to_keys():
    """🔴 Regression guard for a bug this fix introduced and the suite caught.

    Upstream also shapes `errors` as an OBJECT. Flattening it with
    `for e in entry["errors"]` yields the KEYS (`['recipients']`) and discards
    the message — the single thing an operator needs in order to reconcile.
    Collect the payload whole.
    """
    payload = {"recipients": [{"message": "rate limited"}]}
    entries = db._normalize_send_response([{"errors": payload, "timestamp": ""}])
    errors = [entry["errors"] for entry in entries if entry.get("errors")]
    assert "rate limited" in repr(errors), (
        "the errors payload was flattened and lost its reason — an operator "
        f"would see only container keys: {errors!r}")


def test_a_singular_error_key_is_also_collected():
    entries = db._normalize_send_response([{"error": "Invalid identifier"}])
    errors = [entry["error"] for entry in entries if entry.get("error")]
    assert errors == ["Invalid identifier"]


@pytest.mark.parametrize("bad", ["a string", 7, None, True])
def test_an_unrecognised_shape_raises_rather_than_guessing(bad):
    with pytest.raises(ValueError, match="unrecognised|EMPTY"):
        db._normalize_send_response(bad)


def test_an_empty_list_raises():
    """No entry means no timestamp, which means the sync echo cannot dedupe."""
    with pytest.raises(ValueError, match="EMPTY"):
        db._normalize_send_response([])


def test_more_than_one_entry_raises_instead_of_picking_one():
    """We always send to exactly one recipient.

    A longer list means the wire contract changed; choosing an entry would be a
    guess about which message the stored timestamp belongs to — the exact thing
    that breaks dedupe and duplicates messages.
    """
    with pytest.raises(ValueError, match="refusing to guess"):
        db._normalize_send_response([{"timestamp": "1"}, {"timestamp": "2"}])


def test_non_object_entries_raise():
    with pytest.raises(ValueError, match="non-object"):
        db._normalize_send_response([["nested"]])


def test_the_old_dict_only_expression_would_have_failed_on_the_live_shape():
    """Positive control for the whole file.

    Pins that the live shape genuinely breaks the pre-fix expression — so these
    tests are known to be exercising a real defect, not a hypothetical one.
    """
    live = [{"timestamp": "1787331796630"}]
    with pytest.raises(AttributeError):
        (live or {}).get("errors")  # type: ignore[union-attr]
