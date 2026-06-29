"""Tests for extract.thread_key — the RFC 5322 thread-grouping key.

Pure logic, no DB, no network. Verifies the resolution order
(References[0] → In-Reply-To → own message_id) and that a synthetic root + its
two replies all collapse to the same key (the real naida-thread shape).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import extract  # noqa: E402


def test_references_returns_first_id_thread_root():
    # References is a whitespace-separated list of <msg-id>s; the FIRST is the root.
    k = extract.thread_key(
        {"References": "<root@x> <mid@x> <latest@x>"}, message_id="<latest@x>"
    )
    assert k == "root@x"


def test_references_single_id():
    k = extract.thread_key({"References": "<only@x>"}, message_id="<reply@x>")
    assert k == "only@x"


def test_in_reply_to_fallback_when_no_references():
    k = extract.thread_key({"In-Reply-To": "<parent@x>"}, message_id="<self@x>")
    assert k == "parent@x"


def test_own_message_id_fallback_when_no_threading_headers():
    k = extract.thread_key({}, message_id="<self@x>")
    assert k == "self@x"


def test_own_message_id_fallback_when_headers_none():
    k = extract.thread_key(None, message_id="<self@x>")
    assert k == "self@x"


def test_header_lookup_is_case_insensitive():
    assert extract.thread_key({"references": "<r@x> <m@x>"}, "<m@x>") == "r@x"
    assert extract.thread_key({"IN-REPLY-TO": "<p@x>"}, "<s@x>") == "p@x"


def test_angle_brackets_stripped_everywhere():
    assert extract.thread_key({}, message_id="<bare@x>") == "bare@x"
    assert extract.thread_key({"In-Reply-To": "<p@x>"}, "<s@x>") == "p@x"


def test_empty_references_falls_through_to_in_reply_to():
    k = extract.thread_key(
        {"References": "   ", "In-Reply-To": "<p@x>"}, message_id="<s@x>"
    )
    assert k == "p@x"


def test_three_message_thread_collapses_to_one_key():
    # The real naida-thread shape: a root with no threading headers, then two replies
    # whose References lists start with the root's message_id.
    root = {"headers": {}, "message_id": "<M0>"}
    reply1 = {"headers": {"References": "<M0>"}, "message_id": "<M1>"}
    reply2 = {"headers": {"References": "<M0> <M1>"}, "message_id": "<M2>"}

    keys = {
        extract.thread_key(m["headers"], m["message_id"])
        for m in (root, reply1, reply2)
    }
    assert keys == {"M0"}
