#!/usr/bin/env python3
"""REPO-WIDE structural guard: no CAPTURED FREE TEXT in a committed JSON/JSONL.

The third sibling of `test_no_public_ips.py` (IP literals) and
`test_no_client_hostnames.py` (client subdomains). Those two gate the values
`CLAUDE.md`'s PUBLIC-repo rule names; this one gates the class that rule did not
name until the clause this PR added — **message bodies, prompts, transcript
excerpts and captured command logs frozen into a fixture**.

The scan is `scripts/testlib/captured_text_scan.py`, whose docstring explains —
with the measured counts — why this is a STRUCTURAL rule (json × message-ish key
× free text) rather than an attempt to tell captured prose from authored prose by
reading it, and which four ARRIVAL PATHS it was measured to miss on its first
cut.

HOW TO SATISFY IT
-----------------
Ask what the fixture is FOR.

  * **A frozen capture backing an eval or a report that has already been
    written** — delete it. Its findings belong in `claudedocs/`, which is where
    they get read; the capture itself is a liability with no reader. That is what
    happened to `scripts/initiatives/dspy-eval/`, the exposure this gate was
    written for: a report-only harness whose verdict was fully recorded in
    `claudedocs/dspy-recap-eval-2026-07-23.md` and whose code no longer even ran.

  * **A fixture something still consumes** — regenerate it with SYNTHETIC
    content. A test that needs a message-shaped payload needs the SHAPE, never a
    real person's words.

Adding an ALLOWLIST entry is the rare third option, for a JSON file whose free
text is genuinely authored or genuinely invented — and note it is keyed on
`(path, keypath)` plus a COUNT, never on the value, because spelling the value
here would commit the disclosure this gate exists to prevent.

🔴 THE COUNT PIN IS THE POINT AND IT IS DELIBERATELY NOISY. Every entry below
sits on a fixture whose own header says its content is invented. Adding one more
message to any of them turns this gate red, and the fix is to re-read the new
value, confirm it is invented too, and bump the count. That is the review this
gate exists to force — not an inconvenience to route around by widening a pin.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from testlib import captured_text_scan as C  # noqa: E402
from testlib import public_ip_scan as P  # noqa: E402

_SYNTHETIC_MAIL = ("synthetic mail fixture — test_filter.py's own header says the "
                   "headers are invented; subjects are classifier bait, not real mail")
_SYNTHETIC_SIGNAL = ("synthetic signal-cli envelope corpus — its `_README` says every "
                     "value is REAL-SHAPED and invented, pairwise distinct on purpose")
_SYNTHETIC_SESSION = ("synthetic Claude-session transcript fixture for the refsources "
                      "validator — five hand-written lines, no real session")
_REAL_DRAFTER = ("🔴 NOT synthetic. A frozen capture of a REAL 2026-07-29 agent run, "
                 "consumed by test_allowlist_covers_prompt_shapes.py's regression "
                 "corpus. PINNED, NOT BLESSED: the delete-vs-scrub-vs-regenerate call "
                 "is the operator's, and this entry exists so the gate SEES the file "
                 "instead of scanning clean over it. See the PR body for the options "
                 "and their costs.")

# --- THE PINNED (PATH, KEYPATH) -> (COUNT, WHY) ALLOWLIST -----------------------
# 🔴 KEYED ON (PATH, KEYPATH) AND PINNED TO A COUNT — deliberately unlike both
# siblings, which key on `(relpath, value)`. The value cannot appear here: it is
# the disclosure. The COUNT is what stops the pin from being a rubber stamp, since
# without it an exemption at `messages[].text` would pre-approve every future
# message added at that key.
#
# Accounting runs in BOTH directions, the same discipline as the sibling gates:
#   * a hit matching no entry        -> FAIL (something new landed);
#   * an entry matching no hit       -> FAIL (the site went away; delete the pin);
#   * a hit whose count moved EITHER WAY -> FAIL (grew under a live pin, or the
#     pin was written oversized and would have pre-approved growth).
ALLOWLIST: dict[tuple[str, str], tuple[int, str]] = {
    ("scripts/mail-actions/tests/fixtures/mail_headers.json",
     "[].subject"): (2, _SYNTHETIC_MAIL),
    ("scripts/mail-actions/tests/fixtures/mail_headers.json",
     "[].headers.Subject"): (2, _SYNTHETIC_MAIL),

    ("scripts/signal/tests/fixtures/envelopes.json",
     "attachment.envelope.dataMessage.message"): (1, _SYNTHETIC_SIGNAL),
    ("scripts/signal/tests/fixtures/envelopes.json",
     "attachment.expect.message.body"): (1, _SYNTHETIC_SIGNAL),
    ("scripts/signal/tests/fixtures/envelopes.json",
     "dm.envelope.dataMessage.message"): (1, _SYNTHETIC_SIGNAL),
    ("scripts/signal/tests/fixtures/envelopes.json",
     "dm.expect.message.body"): (1, _SYNTHETIC_SIGNAL),
    ("scripts/signal/tests/fixtures/envelopes.json",
     "edit.envelope.editMessage.dataMessage.message"): (1, _SYNTHETIC_SIGNAL),
    ("scripts/signal/tests/fixtures/envelopes.json",
     "edit.expect.message.body"): (1, _SYNTHETIC_SIGNAL),
    ("scripts/signal/tests/fixtures/envelopes.json",
     "group.envelope.dataMessage.message"): (1, _SYNTHETIC_SIGNAL),
    ("scripts/signal/tests/fixtures/envelopes.json",
     "group.expect.message.body"): (1, _SYNTHETIC_SIGNAL),
    ("scripts/signal/tests/fixtures/envelopes.json",
     "quote.envelope.dataMessage.quote.text"): (1, _SYNTHETIC_SIGNAL),

    ("scripts/validation/tests/fixtures/claude_session.jsonl",
     "message.content"): (1, _SYNTHETIC_SESSION),

    ("scripts/task-spec-drafter/tests/fixtures/drafter_run_2026_07_29_commands.json",
     "executed[]"): (95, _REAL_DRAFTER),
    ("scripts/task-spec-drafter/tests/fixtures/drafter_run_2026_07_29_commands.json",
     "rejected[]"): (2, _REAL_DRAFTER),
}

#: 🔴 The escape hatch `unparseable_files` had none of. Without it, ONE tracked
#: file that legitimately does not parse — a template with placeholders, a
#: deliberately-malformed parser fixture — makes this gate permanently red, which
#: is the exact failure `claude/RULES.md` says is worse than no gate at all.
#: Empty today, and accounted for in both directions like the one above.
UNPARSEABLE_ALLOWLIST: dict[str, str] = {}

#: A 70-character sentence: clears BOTH thresholds, so it is usable for any key.
_PROSE = "the follower has been lagging behind the primary since the last deploy"
#: A 30-character sentence: clears the chat threshold only.
_SHORT_CHAT = "can you check the queue depth"


def _hits():
    return C.scan_repo(REPO)


def _unpinned(hits):
    out = []
    for relpath, keypath, count, max_len in hits:
        pin = ALLOWLIST.get((relpath, keypath))
        if pin is None or pin[0] != count:
            out.append((relpath, keypath, count, max_len))
    return out


def _fmt(hits):
    return "\n  ".join(
        f"{p}: {k}  ({n} free-text values, longest {m} chars)"
        for p, k, n, m in hits)


# --- the scan ------------------------------------------------------------------

def test_no_captured_free_text_is_committed():
    unpinned = _unpinned(_hits())
    assert not unpinned, (
        "CAPTURED FREE TEXT is committed to a PUBLIC repo — message bodies, "
        "prompts, transcript excerpts or a captured command log frozen into a "
        "fixture. If the capture backs a report that is already written, DELETE "
        "it; if something still consumes the fixture, regenerate it with "
        "SYNTHETIC content (a test needs the SHAPE, not a real person's words). "
        "Counts and key paths only below — this gate never prints the text:\n  "
        + _fmt(unpinned))


def test_every_allowlist_entry_still_matches_something():
    """Stale-pin accounting — the complement of the scan above.

    A REAL guard now, not the invariant guard it was while `ALLOWLIST` was empty:
    every entry names a live (path, keypath) in this tree, so deleting or
    renaming any of those fixtures fails here instead of leaving a dead pin that
    silently pre-approves whatever lands at that path next.
    """
    seen = {(p, k) for p, k, _n, _m in _hits()}
    stale = [f"{p}: {k} ({why})" for (p, k), (_n, why) in ALLOWLIST.items()
             if (p, k) not in seen]
    assert not stale, (
        "ALLOWLIST entries match nothing — the fixture went away or the key "
        "moved. Delete or repoint the pin:\n  " + "\n  ".join(stale))


def test_every_allowlist_entry_carries_a_reason():
    """`claude/RULES.md`: an allowlist is an enumeration with each entry's reason.

    Pinned so a bare pin cannot be added, in either allowlist.
    """
    for (p, k), (n, why) in ALLOWLIST.items():
        assert isinstance(n, int) and n > 0, f"{p}:{k} has a non-count pin"
        assert why and len(why) > 30, f"{p}:{k} has no reason"
    for p, why in UNPARSEABLE_ALLOWLIST.items():
        assert why and len(why) > 30, f"{p} has no reason"


def test_the_tree_has_no_unscanned_json():
    """A tracked JSON this scan could not PARSE was not scanned at all.

    A silent skip is indistinguishable from a clean file, which is the failure
    mode `claude/RULES.md` calls a zero from a harness wired to nothing. Pinned
    at zero modulo `UNPARSEABLE_ALLOWLIST` so a new unparseable fixture is
    visible rather than exempt — and so that ONE such file cannot make this gate
    permanently red with no way out.
    """
    bad = [b for b in C.unparseable_files(REPO) if b not in UNPARSEABLE_ALLOWLIST]
    assert bad == [], f"tracked JSON/JSONL that was NOT scanned: {bad}"


def test_every_unparseable_allowlist_entry_still_matches_something():
    """The other direction, so an exemption cannot outlive the file it names."""
    bad = set(C.unparseable_files(REPO))
    stale = sorted(p for p in UNPARSEABLE_ALLOWLIST if p not in bad)
    assert stale == [], f"UNPARSEABLE_ALLOWLIST entries match nothing: {stale}"


def test_negative_control_the_unparseable_detector_can_go_RED(tmp_path):
    """🔴 The pinned zero above had NO negative control.

    MEASURED: mutating `unparseable_files` to `return []` SURVIVED all 20 tests
    of the first cut, and so did partially blinding it. The code genuinely worked
    — both a BOM'd file and a trailing comma return None from `_parse` — but the
    TEST was vacuous, which is the same thing as untested. Two realistic shapes:
    a UTF-8 BOM (what a Windows editor writes) and a trailing comma (what a
    hand-edit leaves).
    """
    (tmp_path / "bom.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"a": 1}).encode())
    (tmp_path / "trailing.json").write_text('{"a": 1,}', encoding="utf-8")
    (tmp_path / "fine.json").write_text('{"a": 1}', encoding="utf-8")
    assert C.unparseable_files(tmp_path) == ["bom.json", "trailing.json"]


def test_a_jsonc_comment_is_parsed_but_a_json_comment_is_not(tmp_path):
    """`.jsonc` is a scanned suffix, so it must not land in the unparseable pin.

    The mirror half matters as much: the SAME bytes under `.json` stay
    unparseable, so adding comment tolerance to `.jsonc` did not quietly loosen
    the strict parse everywhere.
    """
    src = '{\n  // a comment\n  "prompt": %s,\n}' % json.dumps(_PROSE)
    (tmp_path / "cfg.jsonc").write_text(src, encoding="utf-8")
    (tmp_path / "cfg.json").write_text(src, encoding="utf-8")
    assert C.unparseable_files(tmp_path) == ["cfg.json"]
    assert [(h[0], h[1], h[2]) for h in C.scan_repo(tmp_path)] == [
        ("cfg.jsonc", "prompt", 1)]


def test_the_jsonc_stripper_does_not_edit_the_content_it_measures(tmp_path):
    """🔴 A comment-stripper that rewrites string VALUES corrupts the evidence.

    A regex sweep for a trailing comma (`,\\s*[}\\]]`) is not string-aware, so it
    silently deletes a comma from inside any value containing `, }` or `, ]` —
    changing the length this gate reports and, at the boundary, whether a value
    is a finding at all. Driven with a value that contains BOTH sequences plus a
    `//` and a `/* */` that must survive because they are inside the string, and
    a real trailing comma outside it that must not.
    """
    value = "step one, } then step two, ] see http://x.test /* not a comment */ ok"
    src = '{\n  // lead\n  "prompt": %s,\n}\n' % json.dumps(value)
    (tmp_path / "c.jsonc").write_text(src, encoding="utf-8")
    parsed = C._parse(tmp_path / "c.jsonc")
    assert parsed == [{"prompt": value}], parsed
    hits = C.scan_repo(tmp_path)
    assert [(h[1], h[2], h[3]) for h in hits] == [("prompt", 1, len(value))]


# --- negative controls: the gate must GO RED on the real shape ------------------
# 🔴 Built from a REALISTIC fixture, not a textbook one. RULES.md: a scanner
# validated against its own canonical example proves nothing, because scanners
# allowlist canonical examples. This reproduces the ACTUAL structure of the
# exposure that motivated the gate — `by_repo.<repo>[].recent_messages[].text`,
# an array of sentence-like strings — with invented content.

def _realistic_capture() -> dict:
    """The shape of an `initiative-scan --days 30 --json` dump, invented content.

    Sentences are fabricated but have the properties that matter: >60 chars,
    whitespace, and the same nesting (a DATA-keyed repo map -> a list of
    initiatives -> a message list -> a `text` leaf).
    """
    def msg(n: int) -> dict:
        return {"ts": f"2026-01-0{n}T00:00:00Z",
                "text": ("we should probably roll the staging deploy back before "
                         f"the sync window closes, attempt number {n} today")}
    return {
        "days": 30,
        "by_repo": {
            "/home/someone/workspace/some-client-repo": [
                {"slug": "a-slug", "momentum": "active",
                 "recent_messages": [msg(1), msg(2), msg(3)]},
            ],
        },
    }


def test_negative_control_the_scan_goes_RED_on_a_realistic_capture(tmp_path):
    f = tmp_path / "scripts" / "someeval" / "scan-days30.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps(_realistic_capture()), encoding="utf-8")
    hits = C.scan_repo(tmp_path)
    assert len(hits) == 1, f"scan is wired to nothing: {hits}"
    relpath, keypath, count, max_len = hits[0]
    assert relpath == "scripts/someeval/scan-days30.json"
    assert keypath == "by_repo.*[].recent_messages[].text", keypath
    assert count == 3 and max_len > C.MIN_FREE_TEXT_CHARS


def test_negative_control_the_gates_ASSERTION_fails_not_just_the_scan(tmp_path):
    """The scan FINDING something and the gate REPORTING it are different things.

    An over-broad allowlist, or a `_unpinned` that swallowed everything, would
    leave the scan correct and the gate inert.
    """
    fabricated = [("scripts/x/dump.json", "messages[].body", 7, 210)]
    assert _unpinned(fabricated) == fabricated
    assert "7 free-text values" in _fmt(fabricated)


def test_negative_control_a_bare_string_list_under_a_message_key(tmp_path):
    """🔴 The shape `eval_set.json` used: `recent_messages: ["...", "..."]`.

    Bare strings DIRECTLY under the message key, with no `text` leaf. A scan that
    only inspects the key immediately above a leaf sees the enclosing key as
    `None` here and reports nothing — MEASURED as the reason list inheritance
    exists in `scan_obj`.
    """
    f = tmp_path / "eval_set.json"
    f.write_text(json.dumps({"train": [{"ctx": {"recent_messages": [
        "the migration finished but the follower is still lagging by six hours",
        "can you take a look at the queue depth when you get a moment tomorrow",
    ]}}]}), encoding="utf-8")
    hits = C.scan_repo(tmp_path)
    assert [(h[1], h[2]) for h in hits] == [
        ("train[].ctx.recent_messages[]", 2)], hits


def test_negative_control_a_jsonl_transcript_is_scanned(tmp_path):
    """JSONL is the native format of every transcript export, and a `.json`-only
    scan would miss the likeliest future shape of this exposure entirely."""
    f = tmp_path / "sessions.jsonl"
    f.write_text("\n".join(json.dumps({"role": "user", "content": c}) for c in (
        "please go ahead and restart the ingest workers on the primary cluster",
        "that did not help, the lag is still climbing steadily since the deploy",
    )), encoding="utf-8")
    hits = C.scan_repo(tmp_path)
    assert [(h[1], h[2]) for h in hits] == [("content", 2)], hits


def test_negative_control_a_GROWN_fixture_under_a_live_pin_still_fails():
    """The count half of the allowlist, driven rather than asserted about.

    Without it, one exemption at a message key pre-approves every message added
    there afterwards — the rubber stamp the sibling gates' M7 incident is about.

    🔴 BOTH DIRECTIONS. The first cut only drove the GROWN case, so mutating
    `pin[0] != count` to `pin[0] < count` SURVIVED — and an oversized pin is the
    same rubber stamp written in advance: pin 9 against a real 5 pre-approves the
    next four messages silently.
    """
    pin = ("docs/x.json", "messages[].text")
    hits = [("docs/x.json", "messages[].text", 5, 100)]
    saved = dict(ALLOWLIST)
    try:
        ALLOWLIST.clear()
        ALLOWLIST[pin] = (3, "control")
        assert _unpinned(hits) == hits, "a GROWN fixture passed under its pin"
        ALLOWLIST[pin] = (9, "control")
        assert _unpinned(hits) == hits, "an OVERSIZED pin exempted a smaller hit"
        ALLOWLIST[pin] = (5, "control")
        assert _unpinned(hits) == [], "an exact-count pin did not exempt"
    finally:
        ALLOWLIST.clear()
        ALLOWLIST.update(saved)


# --- the ARRIVAL PATHS the first cut was measured to miss ------------------------

# 🔴 THE ASSERTED LEDGER. A test PARAMETRIZED OVER the enumeration cannot see a
# DELETION from it — the case simply stops being generated and the suite goes
# green with one fewer test. MEASURED: with only the parametrized test below,
# deleting `"recap"` from `MESSAGE_KEYS` SURVIVED the whole suite (97 -> 96
# passed, zero failures). So the ledger is spelled out here, independently, and
# the set is asserted for equality: a deletion, a typo or a merge-dropped line
# fails, and so does an addition that nobody wrote a reason for.
_EXPECTED_MESSAGE_KEYS = frozenset({
    "recent_messages", "messages", "message", "text", "texts", "body", "bodies",
    "content", "contents", "prompt", "prompts", "transcript", "transcripts",
    "chat", "chats", "conversation", "conversations", "comment", "comments",
    "excerpt", "excerpts", "snippet", "snippets", "subject", "subjects",
    "answer", "answers", "evidence", "executed", "rejected", "commands",
    "command", "recap", "recaps", "summary", "summaries", "next_step",
    "next_steps", "open_investigations", "recent_commits", "description",
})
_EXPECTED_CHAT_KEYS = frozenset({
    "recent_messages", "messages", "message", "text", "texts", "body", "bodies",
    "content", "contents", "chat", "chats", "conversation", "conversations",
    "transcript", "transcripts", "comment", "comments",
})
_EXPECTED_EXCLUDED_KEYS = frozenset({
    "title", "path", "current_doc", "name", "reason", "role",
})


@pytest.mark.parametrize(
    "table,expected",
    [("MESSAGE_KEYS", _EXPECTED_MESSAGE_KEYS),
     ("CHAT_KEYS", _EXPECTED_CHAT_KEYS),
     ("_EXCLUDED_KEYS", _EXPECTED_EXCLUDED_KEYS)])
def test_the_key_enumerations_are_pinned_as_a_ledger(table, expected):
    """🔴 The enumeration IS the rule, so its SET is asserted, not sampled.

    Failing here means a key moved. That is a review, not a chore: re-derive the
    entry's reason, update this ledger in the SAME commit, and say in the commit
    message which producer stopped or started emitting it.
    """
    actual = frozenset(getattr(C, table))
    assert actual == expected, (
        f"{table} drifted — dropped {sorted(expected - actual)}, "
        f"added {sorted(actual - expected)}")


@pytest.mark.parametrize("key", sorted(C.MESSAGE_KEYS))
def test_every_enumerated_message_key_is_actually_wired(key, tmp_path):
    """🔴 THE OTHER HALF: every entry must be EXERCISED, not just listed.

    MEASURED on the first cut: 12 of 18 keys were never touched by any test, so
    a key could sit in the table without the walk ever matching it. The ledger
    above catches a deletion; this catches an entry that is present and inert.
    """
    (tmp_path / "d.json").write_text(json.dumps({key: _PROSE}), encoding="utf-8")
    hits = C.scan_repo(tmp_path)
    assert [(h[1], h[2]) for h in hits] == [(key, 1)], (
        f"MESSAGE_KEYS[{key!r}] is enumerated but not wired: {hits}")


@pytest.mark.parametrize("key", sorted(C.CHAT_KEYS))
def test_every_chat_key_uses_the_lower_threshold(key, tmp_path):
    """🔴 The 60-char threshold was near-total blindness for chat.

    MEASURED: `scripts/signal/tests/fixtures/envelopes.json` carries 6
    16 `message`/`body` leaves of 12-32 chars — 0 of 16 over 60. A gate on captured
    chat that cannot see one value in the repo's only chat subsystem is scanning
    clean over its own target class. Driven per key so a key dropped from
    `CHAT_KEYS` fails here rather than silently reverting to 60.
    """
    assert len(_SHORT_CHAT) < C.MIN_FREE_TEXT_CHARS
    assert key in C.MESSAGE_KEYS, f"{key} is a chat key but not a message key"
    (tmp_path / "d.json").write_text(json.dumps({key: _SHORT_CHAT}),
                                     encoding="utf-8")
    assert [(h[1], h[2]) for h in C.scan_repo(tmp_path)] == [(key, 1)]


def test_a_short_value_under_a_NON_chat_key_is_still_not_a_finding(tmp_path):
    """The other half of the two-threshold trade, so it is a decision not a slip.

    `subject` is the one that matters: a 25-char subject line is ordinary, and
    lowering its threshold would put 26 more pins into `ALLOWLIST` from the mail
    fixture alone.
    """
    non_chat = sorted(set(C.MESSAGE_KEYS) - set(C.CHAT_KEYS))
    assert "subject" in non_chat and "summary" in non_chat
    (tmp_path / "d.json").write_text(
        json.dumps({k: _SHORT_CHAT for k in non_chat}), encoding="utf-8")
    assert C.scan_repo(tmp_path) == []


def test_key_matching_is_case_insensitive(tmp_path):
    """`Subject`, `Text` and `Body` are the canonical RFC/DTO capitalisations.

    MEASURED: the mail fixture carries 13 `Subject` headers that an exact-match
    key set never saw, beside the 15 lowercase ones that it did.
    """
    (tmp_path / "d.json").write_text(json.dumps(
        {"Subject": _PROSE, "TEXT": _PROSE, "Body": _PROSE}), encoding="utf-8")
    assert [(h[1], h[2]) for h in C.scan_repo(tmp_path)] == [
        ("Body", 1), ("Subject", 1), ("TEXT", 1)]


def test_a_DICT_under_a_message_key_inherits_it_like_a_list_does(tmp_path):
    """🔴 Arrival path 3: only a LIST inherited the enclosing message key.

    An id-keyed or timestamp-keyed message MAP is an ordinary export shape and
    was one line away from the list case the first cut built deliberately.
    """
    (tmp_path / "a.json").write_text(json.dumps(
        {"messages": {"m_01": _PROSE, "m_02": _PROSE}}), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(
        {"transcript": {"2026-01-01T00:00:00Z": _PROSE}}), encoding="utf-8")
    assert [(h[0], h[1], h[2]) for h in C.scan_repo(tmp_path)] == [
        ("a.json", "messages.m_01", 1), ("a.json", "messages.m_02", 1),
        ("b.json", "transcript.*", 1)]


def test_an_excluded_key_blocks_dict_inheritance(tmp_path):
    """`_EXCLUDED_KEYS` is READ by `scan_obj`, not documentation that reads as a
    guard — which is what it was on the first cut, where no code path touched it.

    A long PATH sitting inside a message object is the media-path clause's
    business, not this gate's, and it must not inherit `messages`.
    """
    long_path = "/some/dir/" + "x" * 40 + " and a trailing note about it"
    (tmp_path / "d.json").write_text(json.dumps(
        {"messages": {"path": long_path, "note": _PROSE}}), encoding="utf-8")
    assert [h[1] for h in C.scan_repo(tmp_path)] == ["messages.note"]


def test_a_filename_supplies_the_key_when_nothing_else_does(tmp_path):
    """🔴 Arrival path 4a: `messages.json` holding a bare `["...", "..."]`.

    The walk started at `key=None` and the FILENAME was never consulted, so a
    dump saved under the most obvious possible name was invisible. Applied only
    when the top-level document is a list or a bare string, so a normal object in
    a file named `content.json` is unaffected — the negative half is pinned here
    too.
    """
    (tmp_path / "messages.json").write_text(json.dumps([_PROSE, _PROSE]),
                                            encoding="utf-8")
    (tmp_path / "notes.json").write_text(json.dumps([_PROSE, _PROSE]),
                                         encoding="utf-8")
    (tmp_path / "content.json").write_text(json.dumps({"note": _PROSE}),
                                           encoding="utf-8")
    assert [(h[0], h[1], h[2]) for h in C.scan_repo(tmp_path)] == [
        ("messages.json", "messages[]", 2)]


def test_a_jsonl_of_BARE_STRINGS_is_a_text_dump_by_construction(tmp_path):
    """🔴 Arrival path 4b: no key above the leaf, and no helpful filename either.

    A `.jsonl` whose lines are bare sentences cannot be anything but a text dump,
    so the FORMAT is the evidence and the finding renders under a synthetic key.
    `.ndjson` is the same format under its other name.
    """
    for name in ("dump.jsonl", "dump.ndjson"):
        (tmp_path / name).write_text(
            "\n".join(json.dumps(s) for s in (_PROSE, _PROSE)), encoding="utf-8")
    assert [(h[0], h[1], h[2]) for h in C.scan_repo(tmp_path)] == [
        ("dump.jsonl", C.JSONL_BARE_STRING_KEY, 2),
        ("dump.ndjson", C.JSONL_BARE_STRING_KEY, 2)]


def test_the_browser_agents_own_schema_keys_are_covered(tmp_path):
    """`scripts/browser-bridge/browser-agent-parse.py` emits `answer`/`evidence`.

    It is this repo's highest-privilege capture producer — it drives the
    operator's REAL logged-in browser and its input is a `transcript.jsonl` — and
    neither of the keys it emits was enumerated on the first cut.
    """
    (tmp_path / "out.json").write_text(json.dumps(
        {"status": "ok", "answer": _PROSE, "evidence": [_PROSE, _PROSE],
         "steps_used": 3}), encoding="utf-8")
    assert [(h[1], h[2]) for h in C.scan_repo(tmp_path)] == [
        ("answer", 1), ("evidence[]", 2)]


def test_a_captured_COMMAND_LOG_is_a_finding(tmp_path):
    """CLAUDE.md's PUBLIC-repo rule names a ROUTE LOG as a forbidden class.

    A frozen list of the invocations a real run made carries real paths, real
    repo names and real hosts. It is not prose and it is not authored, and the
    first cut had no key for it — which is why the gate scanned clean over
    `scripts/task-spec-drafter/tests/fixtures/`.
    """
    cmd = "git -C /home/someone/workspace/some-client-repo log --oneline -20 --since=2026-01-01"
    (tmp_path / "run.json").write_text(json.dumps(
        {"executed": [cmd, cmd], "rejected": [cmd]}), encoding="utf-8")
    assert [(h[1], h[2]) for h in C.scan_repo(tmp_path)] == [
        ("executed[]", 2), ("rejected[]", 1)]


# --- positive controls: the gate must stay GREEN on legitimate content ----------
# 🔴 A gate that fires on everything gets disabled, and `claude/RULES.md` is
# explicit that a permanently-red gate is worse than none. These pin the
# false-positive half that keeps this one green.

def test_positive_control_the_repo_scans_clean_today():
    """The zero this gate reports must be a zero from a scan that WALKED.

    Reported as a PAIR with the negative controls above, never alone: those prove
    the scan can return non-zero on the real shapes, this one proves the real
    tree is accounted for.

    The floor answers "did it walk anything at all", NOT "is the census right" —
    it is deliberately far below the 18 candidates measured here, because a floor
    set one or two below the count reds the gate the day someone deletes a
    fixture, which is a spurious failure and trains people to click through.
    """
    files = C._candidates(REPO)
    assert len(files) >= 10, (
        f"only {len(files)} JSON/JSONL candidates under REPO={REPO} — the scan "
        "walked (almost) nothing and its zero means nothing")
    assert _unpinned(_hits()) == []


def test_positive_control_a_manifests_description_is_not_a_finding(tmp_path):
    """`description` is a CONDITIONAL member of `MESSAGE_KEYS`, not excluded flat.

    Excluding it by name was sound reasoning (do not allowlist your own canonical
    examples) applied bluntly: this repo ships a ClickUp integration where
    `description` IS the task body (`claude/skills/clickup/query.mjs:238`, 56
    occurrences) and GitHub issue bodies use the same field. Scoping the
    exclusion to a document that also carries `manifest_version` keeps the three
    browser-extension manifests quiet — still not allowlisted — and closes the
    hole. Both halves are driven here; the exclusion is worthless without the
    second.
    """
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"manifest_version": 3, "name": "x", "version": "1.0",
         "description": "Routes downloads into the right library folder based on "
                        "the page they came from, rather than the filename."}),
        encoding="utf-8")
    (tmp_path / "task.json").write_text(json.dumps(
        {"id": "abc123", "name": "a task", "status": {"status": "open"},
         "description": _PROSE}), encoding="utf-8")
    assert [(h[0], h[1], h[2]) for h in C.scan_repo(tmp_path)] == [
        ("task.json", "description", 1)]
    assert "description" in C.MESSAGE_KEYS
    assert C._MANIFEST_SCOPED_KEYS["description"] == "manifest_version"


def test_positive_control_long_non_prose_values_are_not_findings(tmp_path):
    """A hash, a path, a token and a URL are long but are not free text.

    Whitespace is the discriminator; without it this gate would fire on every
    lockfile, every base64 blob and every absolute path in the repo.
    """
    f = tmp_path / "d.json"
    f.write_text(json.dumps({
        "text": "a" * 300,
        "body": "/nix/store/" + "b" * 120 + "-some-derivation-name/bin/thing",
        "content": "sha256-" + "c" * 80,
        "prompt": "https://example.test/" + "d" * 90,
    }), encoding="utf-8")
    assert C.scan_repo(tmp_path) == [], "a non-prose long value was a finding"


def test_positive_control_a_short_message_is_not_a_finding(tmp_path):
    """The thresholds, pinned AT THE BOUNDARY in both directions.

    🔴 The first cut claimed "pinned in both directions" while neither fixture
    was near the boundary (19 and 70 chars), so mutating `>=` to `>` SURVIVED.
    Exactly-N and N-1 are the only two lengths that can see that.
    """
    for th, key in ((C.MIN_FREE_TEXT_CHARS, "summary"),
                    (C.MIN_CHAT_TEXT_CHARS, "message")):
        at = "ab " + "c" * (th - 3)
        below = at[:-1]
        assert len(at) == th and len(below) == th - 1
        assert C.is_free_text(at, key), f"{key}: exactly {th} must be free text"
        assert not C.is_free_text(below, key), f"{key}: {th - 1} must not be"
        d = tmp_path / key
        d.mkdir()
        (d / "at.json").write_text(json.dumps({key: at}), encoding="utf-8")
        (d / "below.json").write_text(json.dumps({key: below}), encoding="utf-8")
        assert [(h[0], h[1]) for h in C.scan_repo(d)] == [("at.json", key)]


def test_positive_control_an_unknown_key_is_not_a_finding(tmp_path):
    """The enumeration is the rule. Pinned so the blind spot is a MEASURED,
    documented trade rather than an assumption — a producer calling its field
    `utterance` walks through, and the scan module says so in its own docstring.
    """
    f = tmp_path / "d.json"
    f.write_text(json.dumps({"utterance":
        "this is a long sentence of captured text under a key nobody enumerated"}),
        encoding="utf-8")
    assert C.scan_repo(tmp_path) == []


# --- guarding the guard's inputs -----------------------------------------------

def test_the_scan_actually_reads_the_repo():
    """A wrong REPO root yields an empty file set and the zero above passes
    vacuously — the exact bug the IP gate shipped and caught."""
    files = C.repo_files(REPO)
    assert len(files) > 300, f"REPO={REPO} produced only {len(files)} files"
    assert any(f.name == "flake.nix" for f in files), "not the devrc root"


def test_the_skip_dir_test_is_relative_to_the_root(tmp_path):
    """🔴 The harness bug the IP gate shipped, inherited here rather than
    re-derived: every agent worktree lives under `…/worktrees/<id>/`, so an
    ABSOLUTE-parts skip test is true for every file and skips the whole repo
    while reporting a clean zero. This gate reaches that logic through the
    delegation below, so the pin belongs here too.
    """
    root = tmp_path / "worktrees" / "agent-x"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "d.json").write_text(json.dumps({"messages": [
        "the primary is still refusing writes after the failover completed"]}),
        encoding="utf-8")
    hits = C.scan_repo(root)
    assert len(hits) == 1, (
        f"the repo was skipped because its own PATH contains a skip-dir: {hits}")


def test_candidates_re_applies_the_skip_dir_test(tmp_path):
    """`public_ip_scan.scan_repo` re-applies `_is_skipped` after `repo_files`;
    `_candidates` did not, which is a DIVERGENCE at the seam this module claims
    to have unified.

    🔴 IT ONLY SHOWS ON ONE TIER, so the fixture must build that tier. `repo_files`
    applies the skip test itself on the filesystem-walk fallback, and a tmp_path
    with no `.git` takes exactly that path — a fixture built there passes with or
    without the fix and proves nothing. This drives a REAL git repo so the scan
    takes the `git ls-files` tier, where a tracked path under a skip dir comes
    back and only the re-application drops it. (RULES.md: a suite whose harness
    pins a dimension is structurally blind to that dimension's bugs.)
    """
    assert shutil.which("git"), (
        "git is not on PATH — this test must NOT skip its way to green: "
        "run-tests.sh lists git in REQUIRED_TOOLS and the nix check puts it in "
        "nativeBuildInputs, so its absence is a harness fault, not a reason to "
        "stop measuring the tier the divergence lives on")
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "d.json").write_text(
        json.dumps({"messages": [_PROSE]}), encoding="utf-8")
    (tmp_path / "keep.json").write_text(json.dumps({"messages": [_PROSE]}),
                                        encoding="utf-8")
    run = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a], check=True,
                                    capture_output=True)
    run("init", "-q")
    run("-c", "core.excludesFile=/dev/null", "add", "-f",
        "keep.json", "node_modules/pkg/d.json")
    tracked = C.repo_files(tmp_path)
    assert any("node_modules" in p.parts for p in tracked), (
        "the git tier did not return the skipped path — this fixture is not "
        "exercising the tier the divergence lives on")
    assert [str(p.relative_to(tmp_path)) for p in C._candidates(tmp_path)] == [
        "keep.json"]


def test_the_file_enumeration_is_the_ip_gates_not_a_third_copy():
    """🔴 ONE RULE, ONE PLACE — the seam, pinned rather than trusted.

    `captured_text_scan` imports `repo_files` from `public_ip_scan` instead of
    growing a third copy of the git-ls-files/fallback/skip-dir logic. Identity,
    not equality: a divergent copy that happened to behave the same today would
    satisfy an equality check and rot apart later.
    """
    assert C.repo_files is P.repo_files
    assert C._is_skipped is P._is_skipped


# --- the REPORT must not leak, which is a claim about KEYS, not just types ------

# 🔴 REALISTIC DATA KEYS, of the four shapes `_SCHEMA_KEY` used to admit. The
# only guard the first cut had used an ABSOLUTE PATH — the one shape that fails
# the leading-`[A-Za-z_]` anchor — so widening the character class mid-string
# SURVIVED and five constructed data keys echoed verbatim into a formatted
# finding. Three of these are classes `CLAUDE.md` names outright.
_DATA_KEYS = [
    "gpu-07.some-client.internal",   # a client hostname
    "some-client-infra",             # a bare repo name
    "j.doe",                         # an email localpart
    "Some.Show.S01",                 # a media directory name
    "/home/someone/workspace/repo",  # the absolute path the first cut pinned
]


@pytest.mark.parametrize("data_key", _DATA_KEYS)
def test_a_data_shaped_map_key_is_never_echoed(data_key, tmp_path):
    """🔴 THE REPORT ITSELF MUST NOT LEAK.

    `by_repo` was keyed by CLIENT REPO PATH. A gate that printed its key paths
    verbatim would put that path into every failure message, every CI log and —
    worse — into a COMMITTED allowlist entry in a public repo, which is the exact
    place the design says disclosure must never go. Data-shaped keys render as
    `*`; schema-shaped keys survive so the report stays useful.
    """
    (tmp_path / "d.json").write_text(json.dumps(
        {"by_repo": {data_key: {"messages": [_PROSE]}}}), encoding="utf-8")
    hits = C.scan_repo(tmp_path)
    assert len(hits) == 1
    assert hits[0][1] == "by_repo.*.messages[]", hits[0][1]
    assert data_key not in _fmt(hits), "a data key leaked into the report"


def test_a_map_of_same_shaped_records_redacts_keys_the_regex_admits(tmp_path):
    """The residual `_SCHEMA_KEY` cannot reach: a bare identifier.

    `jdoe` and `web01` are lexically indistinguishable from a field name, so the
    second layer is STRUCTURAL — a dict of ≥2 unenumerated keys whose values are
    dicts with the identical key set is a map of records, not an object.
    """
    rec = {"messages": [_PROSE], "seen": 1}
    (tmp_path / "d.json").write_text(json.dumps(
        {"by_user": {"jdoe": rec, "web01": dict(rec)}}), encoding="utf-8")
    hits = C.scan_repo(tmp_path)
    assert [h[1] for h in hits] == ["by_user.*.messages[]"]
    assert "jdoe" not in _fmt(hits) and "web01" not in _fmt(hits)


def test_an_object_whose_fields_are_objects_is_NOT_redacted(tmp_path):
    """The other direction, so the redaction above cannot creep into a blanket.

    MEASURED: `scripts/signal/tests/fixtures/envelopes.json` is full of
    `{"envelope": {...}, "expect": {...}}`. Redacting those cost the report the
    only token that says which half a finding is in, for no privacy gain.
    """
    (tmp_path / "d.json").write_text(json.dumps(
        {"case": {"envelope": {"messages": [_PROSE]},
                  "expect": {"count": 1}}}), encoding="utf-8")
    assert [h[1] for h in C.scan_repo(tmp_path)] == [
        "case.envelope.messages[]"]


def test_a_hit_carries_only_paths_and_INTS_by_type(tmp_path):
    """A TYPE guard, and named as one.

    It was called `test_the_scan_never_returns_a_captured_VALUE`, which reads as
    a guarantee about CONTENT while structurally being unable to see a leaked
    KEY — the finding above. It is still worth having: a future field carrying an
    excerpt "for context" fails here. The content claim is the parametrized
    data-key tests, not this.
    """
    f = tmp_path / "d.json"
    sentence = "the queue depth alarm has been flapping every twenty minutes since noon"
    f.write_text(json.dumps({"messages": [sentence]}), encoding="utf-8")
    hits = C.scan_repo(f.parent)
    assert len(hits) == 1
    relpath, keypath, count, max_len = hits[0]
    assert isinstance(relpath, str) and isinstance(keypath, str)
    assert isinstance(count, int) and isinstance(max_len, int)
    assert sentence not in str(hits), "the scan returned the captured text"


def test_the_key_enumeration_and_the_exclusions_are_disjoint_and_explained():
    """Both lists are DATA, and every entry must carry its reason in the source.

    `claude/RULES.md`: an allowlist is an enumeration with each entry's reason,
    not a pattern — and not env-overridable. This pins that neither list can grow
    a bare entry, that a key cannot be in both, and that `CHAT_KEYS` is a strict
    subset of the enumeration rather than a third independent list.
    """
    assert not (set(C.MESSAGE_KEYS) & set(C._EXCLUDED_KEYS)), "a key is in both"
    assert C.CHAT_KEYS < set(C.MESSAGE_KEYS), "CHAT_KEYS is not a strict subset"
    assert set(C._MANIFEST_SCOPED_KEYS) <= set(C.MESSAGE_KEYS)
    for name, table in (("MESSAGE_KEYS", C.MESSAGE_KEYS),
                        ("_EXCLUDED_KEYS", C._EXCLUDED_KEYS)):
        for key, why in table.items():
            assert key == key.lower(), f"{name}[{key!r}] is not lowercase"
            assert why and len(why) > 15, f"{name}[{key!r}] has no reason"


def test_the_thresholds_are_not_env_overridable(monkeypatch):
    """The constants are reviewed edits, not runtime knobs — the same rule the
    per-target test floors follow. An env-tunable threshold is a gate anyone can
    turn off for one run."""
    import importlib
    monkeypatch.setenv("MIN_FREE_TEXT_CHARS", "100000")
    monkeypatch.setenv("MIN_CHAT_TEXT_CHARS", "100000")
    importlib.reload(C)
    assert C.MIN_FREE_TEXT_CHARS == 60
    assert C.MIN_CHAT_TEXT_CHARS == 25
