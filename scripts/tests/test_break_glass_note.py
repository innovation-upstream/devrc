"""The break-glass note in CLAUDE.md must round-trip, not just open the window.

MEASURED, three times on 2026-08-29/30: the note handed over

    gh api -X DELETE .../branches/main/protection/required_status_checks

and said nothing about restoring. `PATCH .../required_status_checks` 404s
("Required status checks not enabled") once the sub-resource is gone, so the
obvious restore fails; two real restores did, one of them inside an EXIT trap
that fired exactly as designed. Closing the window needs a full `PUT` of the
whole protection object, and a partial `PUT` succeeds while silently dropping
every key it omits -- so the restore has to be READ BACK, never trusted.

WHAT THIS GUARD ASSERTS, and it is a RELATIONSHIP rather than a vocabulary:
if CLAUDE.md hands anyone the DELETE, then the same document must also carry

  1. a CAPTURE of the protection object that REDIRECTS TO A FILE, before the DELETE;
  2. a full PUT of the protection OBJECT (not the sub-resource), after the DELETE,
     whose `--input` names THE FILE STEP 1 WROTE;
  3. a read-back of the protection object after the PUT.

Point 2 is the load-bearing one and the reason this is not a keyword check: a
note that says "PUT it back" while never telling you to capture first is exactly
as useless as the note this replaces, because you would have nothing to PUT. The
linkage is asserted by matching the capture's redirect target against the PUT's
`--input` argument, so prose can be reworded freely and the guard still holds --
and cannot pass by spelling the word "restore" somewhere.

It fires ONLY when the DELETE is present. Removing the escape hatch entirely is
a legitimate edit and this test does not forbid it; what it forbids is handing
over the destructive half alone.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
CLAUDE_MD = REPO / "CLAUDE.md"

# A `gh api` invocation may span several physical lines (the capture's --jq
# program does). Split the document on the invocations themselves so each chunk
# holds one whole command, including a redirect that lands lines later.
_GH_SPLIT = re.compile(r"gh api\b")
_VERB = re.compile(r"\A\s*-X\s+([A-Z]+)\b")
# Markdown wraps commands in backticks, and CLAUDE.md writes most of its
# one-liners that way -- including the break-glass DELETE this guard exists for.
# A path regex that swallows the closing backtick makes the parser blind to
# exactly the document shape being policed: the round-trip assertion then passes
# VACUOUSLY. Caught by test_the_note_still_offers_the_escape_hatch against the
# pre-change CLAUDE.md, which is why that control is not decoration.
_PATH = re.compile(r"(/repos/[^\s'\"`]+)")
_PATH_TRAILING = "`.,;:)]}>"
_INPUT = re.compile(r"--input\s+(\S+)")
_REDIRECT = re.compile(r">\s*(\S+\.json)\b")

# The protection OBJECT vs the required_status_checks SUB-RESOURCE. The whole
# incident is that these two are not interchangeable.
_OBJECT = re.compile(r"/branches/[^/\s]+/protection\Z")
_CHECKS = re.compile(r"/branches/[^/\s]+/protection/required_status_checks\Z")


class GhCall:
    __slots__ = ("verb", "path", "input_file", "redirect_file", "offset")

    def __init__(self, verb, path, input_file, redirect_file, offset):
        self.verb = verb
        self.path = path
        self.input_file = input_file
        self.redirect_file = redirect_file
        self.offset = offset

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"<gh api -X {self.verb} {self.path} @{self.offset}>"

    @property
    def on_object(self) -> bool:
        return bool(self.path and _OBJECT.search(self.path))

    @property
    def on_checks(self) -> bool:
        return bool(self.path and _CHECKS.search(self.path))


def parse_gh_calls(body: str) -> list[GhCall]:
    """Every `gh api` invocation in `body`, in document order.

    The verb is None for a plain read (no -X), which is what a read-back is.
    """
    calls: list[GhCall] = []
    marks = list(_GH_SPLIT.finditer(body))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        chunk = body[m.end() : end]
        verb_m = _VERB.match(chunk)
        path_m = _PATH.search(chunk)
        input_m = _INPUT.search(chunk)
        redirect_m = _REDIRECT.search(chunk)
        calls.append(
            GhCall(
                verb=verb_m.group(1) if verb_m else None,
                path=path_m.group(1).rstrip(_PATH_TRAILING) if path_m else None,
                input_file=input_m.group(1) if input_m else None,
                redirect_file=redirect_m.group(1) if redirect_m else None,
                offset=m.start(),
            )
        )
    return calls


def audit(body: str) -> list[str]:
    """Findings against the round-trip contract. Empty list == compliant."""
    calls = parse_gh_calls(body)
    deletes = [c for c in calls if c.verb == "DELETE" and c.on_checks]
    if not deletes:
        # The escape hatch is not offered; there is nothing to round-trip.
        return []
    first_delete = min(c.offset for c in deletes)

    findings: list[str] = []

    puts = [c for c in calls if c.verb == "PUT" and c.on_object and c.offset > first_delete]
    if not puts:
        findings.append(
            "CLAUDE.md hands over `gh api -X DELETE .../protection/required_status_checks` "
            "but carries no `-X PUT` of the protection OBJECT after it. `PATCH` on the "
            "sub-resource 404s once it is deleted -- the window cannot be closed with what "
            "this document currently provides."
        )
        return findings

    captures = {
        c.redirect_file
        for c in calls
        if c.verb is None and c.on_object and c.redirect_file and c.offset < first_delete
    }
    if not captures:
        findings.append(
            "The restoring `PUT` is present but nothing CAPTURES the protection object to a "
            "file before the DELETE. Without a capture there is no payload to PUT, and a "
            "partial PUT silently drops the keys it omits."
        )

    linked = [p for p in puts if p.input_file and p.input_file in captures]
    if captures and not linked:
        inputs = sorted({p.input_file for p in puts if p.input_file} or {"<no --input>"})
        findings.append(
            "The restoring `PUT` does not read the file the capture wrote: capture writes "
            f"{sorted(captures)}, PUT reads {inputs}. The restore is not wired to the backup."
        )

    put_offset = min(p.offset for p in (linked or puts))
    readbacks = [
        c for c in calls if c.verb is None and c.on_object and c.offset > put_offset
    ]
    if not readbacks:
        findings.append(
            "Nothing READS BACK the protection object after the restoring `PUT`. A partial "
            "PUT returns 200, so the request's own status is not evidence that protection "
            "was restored."
        )
    return findings


# --------------------------------------------------------------------------
# The subject.
# --------------------------------------------------------------------------


def test_claude_md_is_readable() -> None:
    assert CLAUDE_MD.is_file(), f"no CLAUDE.md at {CLAUDE_MD}"
    assert CLAUDE_MD.stat().st_size > 1000


def test_the_break_glass_note_round_trips() -> None:
    findings = audit(CLAUDE_MD.read_text(encoding="utf-8"))
    assert not findings, "CLAUDE.md's break-glass note is one-way:\n  - " + "\n  - ".join(
        findings
    )


def test_the_note_still_offers_the_escape_hatch() -> None:
    """Positive control on the SUBJECT: the guard above is not vacuous today.

    `audit()` returns clean for a document that never mentions the DELETE. This
    pins that CLAUDE.md is not that document, so a green run above is a real
    round-trip and not an absence of the hazard.
    """
    calls = parse_gh_calls(CLAUDE_MD.read_text(encoding="utf-8"))
    assert [
        c for c in calls if c.verb == "DELETE" and c.on_checks
    ], "no break-glass DELETE found in CLAUDE.md -- test_the_break_glass_note_round_trips passes VACUOUSLY"


# --------------------------------------------------------------------------
# Controls on the INSTRUMENT. Until these pass, a green above is a fact about
# the parser only.
# --------------------------------------------------------------------------

_DELETE = "gh api -X DELETE /repos/o/r/branches/main/protection/required_status_checks\n"
_CAPTURE = "gh api /repos/o/r/branches/main/protection --jq '{a:1}' > $S/restore.json\n"
_PUT = "gh api -X PUT /repos/o/r/branches/main/protection --input $S/restore.json\n"
_READBACK = "gh api /repos/o/r/branches/main/protection > $S/after.json\n"
COMPLIANT = _CAPTURE + _DELETE + _PUT + _READBACK


def test_negative_control_the_audit_can_go_red() -> None:
    """Realistic bad inputs, each a shape that has actually been written."""
    # The note exactly as it stood before this fix: the DELETE, alone.
    assert audit("The escape hatch:\n" + _DELETE), "a DELETE-only note must be a finding"

    # The restore that was actually attempted, twice, and 404s.
    patch_only = (
        _CAPTURE
        + _DELETE
        + "gh api -X PATCH /repos/o/r/branches/main/protection/required_status_checks --input $S/restore.json\n"
        + _READBACK
    )
    findings = audit(patch_only)
    assert findings, "a PATCH on the sub-resource must not satisfy the PUT requirement"
    assert "no `-X PUT`" in findings[0]

    # A PUT on the sub-resource rather than the object.
    assert audit(
        _CAPTURE
        + _DELETE
        + "gh api -X PUT /repos/o/r/branches/main/protection/required_status_checks --input $S/restore.json\n"
        + _READBACK
    ), "a PUT on the SUB-RESOURCE must not satisfy the object requirement"

    # PATCH on the protection OBJECT. GitHub documents only GET/PUT/DELETE
    # there, so this is an invented verb -- and accepting it would let the note
    # recommend an unmeasured restore, which is the whole incident. Without this
    # case a mutant widening the verb test to `in ("PUT", "PATCH")` SURVIVES.
    assert audit(
        _CAPTURE
        + _DELETE
        + "gh api -X PATCH /repos/o/r/branches/main/protection --input $S/restore.json\n"
        + _READBACK
    ), "PATCH on the protection OBJECT must not satisfy the PUT requirement"

    # PUT present, but nothing captured beforehand -- nothing to restore from.
    assert audit(_DELETE + _PUT + _READBACK), "a missing capture must be a finding"

    # Capture and PUT both present but not wired together.
    assert audit(
        _CAPTURE
        + _DELETE
        + "gh api -X PUT /repos/o/r/branches/main/protection --input $S/something-else.json\n"
        + _READBACK
    ), "a PUT reading a different file than the capture wrote must be a finding"

    # No read-back: the exact thing that let a failed restore report OK.
    assert audit(_CAPTURE + _DELETE + _PUT), "a missing read-back must be a finding"

    # Read-back placed BEFORE the restore proves nothing about the restore.
    assert audit(_CAPTURE + _DELETE + _READBACK + _PUT), "a read-back before the PUT must be a finding"

    # Capture placed AFTER the window is already open captures the broken state.
    assert audit(_DELETE + _CAPTURE + _PUT + _READBACK), "a capture after the DELETE must be a finding"


def test_positive_control_the_audit_passes_a_compliant_note() -> None:
    """Can it ever return clean? A red-always guard is as useless as a green one."""
    assert audit(COMPLIANT) == [], "the compliant fixture must produce no findings"


def test_the_guard_is_scoped_to_documents_that_offer_the_hatch() -> None:
    """No DELETE => no obligation. Removing the hatch is a legitimate edit."""
    assert audit("There is no escape hatch. Ask a human.") == []
    assert audit(_CAPTURE + _READBACK) == [], "reads alone impose no restore obligation"


@pytest.mark.parametrize(
    "verb,path,expect_object,expect_checks",
    [
        (None, "/repos/o/r/branches/main/protection", True, False),
        ("PUT", "/repos/o/r/branches/main/protection", True, False),
        ("DELETE", "/repos/o/r/branches/main/protection/required_status_checks", False, True),
        # A different branch is still the same two shapes.
        ("PUT", "/repos/o/r/branches/release-1.x/protection", True, False),
        # Neither: a neighbouring endpoint must not be mistaken for either.
        (None, "/repos/o/r/branches/main/protection/enforce_admins", False, False),
        (None, "/repos/o/r/branches/main", False, False),
        # `.../required_status_checks/contexts` is a REAL and DIFFERENT endpoint
        # (it drops individual contexts). Both patterns are \Z-anchored so it is
        # neither; without those anchors this row is misclassified as the
        # sub-resource and a mutant dropping them SURVIVES.
        (
            "DELETE",
            "/repos/o/r/branches/main/protection/required_status_checks/contexts",
            False,
            False,
        ),
    ],
)
def test_endpoint_classification(verb, path, expect_object, expect_checks) -> None:
    """The object/sub-resource distinction IS the incident -- pin it directly."""
    line = f"gh api {'-X ' + verb + ' ' if verb else ''}{path}\n"
    (call,) = parse_gh_calls(line)
    assert call.verb == verb
    assert call.path == path
    assert call.on_object is expect_object
    assert call.on_checks is expect_checks


@pytest.mark.parametrize(
    "rendering",
    [
        "`gh api -X DELETE /repos/o/r/branches/main/protection/required_status_checks`",
        "  `gh api -X DELETE /repos/o/r/branches/main/protection/required_status_checks`\n",
        "run `gh api -X DELETE /repos/o/r/branches/main/protection/required_status_checks`.",
        "gh api -X DELETE /repos/o/r/branches/main/protection/required_status_checks",
        "(gh api -X DELETE /repos/o/r/branches/main/protection/required_status_checks)",
    ],
)
def test_a_backticked_command_is_not_invisible(rendering) -> None:
    """MEASURED against the pre-change CLAUDE.md, which wrote the DELETE inline.

    The path regex originally captured the closing backtick, so `on_checks`
    (anchored with \\Z) was False and the parser saw NO break-glass DELETE in a
    document that plainly contained one -- making the round-trip assertion pass
    vacuously on exactly the note it was written to reject.
    """
    (call,) = parse_gh_calls(rendering)
    assert call.verb == "DELETE"
    assert call.on_checks, f"DELETE invisible to the parser when written as: {rendering!r}"
    # ...and the obligation it creates must actually fire.
    assert audit(rendering), "a backticked DELETE must still demand a restore"


def test_multiline_commands_are_parsed_as_one_call() -> None:
    """The real capture spans 11 lines and its redirect is on the last one.

    A per-line parser reads that as a call with no redirect and reports a
    missing capture on a compliant document.
    """
    multiline = (
        "gh api /repos/o/r/branches/main/protection --jq '{\n"
        "  required_status_checks:{strict:.required_status_checks.strict},\n"
        "  enforce_admins:.enforce_admins.enabled,\n"
        "  restrictions}' > $S/restore.json\n"
    ) + _DELETE + _PUT + _READBACK
    calls = parse_gh_calls(multiline)
    assert len(calls) == 4, f"expected 4 gh calls, parsed {len(calls)}: {calls}"
    assert calls[0].redirect_file == "$S/restore.json"
    assert audit(multiline) == []
