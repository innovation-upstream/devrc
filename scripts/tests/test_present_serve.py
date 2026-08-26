#!/usr/bin/env python3
"""Gate on `scripts/present/serve.py` — the STALENESS CONTRACT, first and last.

THE CENTRAL PROPERTY
--------------------
`scripts/present/generate.py` exits 3 and writes NO FILE when every fact came
back UNMEASURED. So a failed regeneration leaves the PREVIOUS page in place, and
the one outcome that is not acceptable is serving that page as if it were
current: it is a careful document full of measured numbers, every one of them
wrong about now, and a reader cannot tell it from a fresh build.

The chosen behaviour is **serve-last-good, with the age made obvious**. This
module pins the half of that which is mechanical:

    every response that carries ARTEFACT BYTES is either fresh, or bannered.

`test_no_response_ever_carries_unbannered_stale_bytes` is the assertion that
keeps it that way — it drives the resolver across a swept age range and checks
the property at every point rather than at the two the author thought of.

🔴 THERE ARE THREE STATES, NOT TWO, AND THE THIRD IS WHY THE SWEEP GOES
NEGATIVE. `fresh` / `stale` / `clock-suspect`. The age used to be
`max(0.0, now - mtime)` with no third state, so an artefact dated in the FUTURE
— a bad RTC corrected backwards by NTP is the realistic route — reported age 0,
`X-Present-Stale: 0` and no banner, however old it really was. That is the
single outcome this file exists to prevent, reached through the clamp that was
supposed to be a formatting nicety. The clock-suspect tests below therefore pin
two separate things: the STATE (it is not `fresh`), and the REACHABILITY (the
guard is asked BEFORE the staleness comparison, without which it could never
fire, since the clamped age lands in the fresh branch every time).

WHAT COUNTS AS REGRESSION COVERAGE HERE
---------------------------------------
Nothing. `serve.py` is new in the commit that adds this file; at the base ref it
does not import, so no test here can be shown red on pre-change code. These are
**INVARIANT GUARDS**, and this module says so rather than implying otherwise.

What they are not is vacuous, because the guards carry their own controls:

  * `test_positive_control_*` proves the banner detector can actually SEE a
    banner and that the fixture page is bannerable — without it, a detector
    matching nothing would report every page "clean" and make the central
    assertion true by accident.
  * `test_the_stale_branch_is_reachable_at_all` proves the stale branch executes
    on this fixture, so the sweep is not passing because it never left the fresh
    branch.
  * The self-containment checks run through `generate.self_contained` — the
    generator's OWN scanner, already controlled in `test_present_render.py` —
    rather than a second copy of the rule here.

MEASURED AT MORE THAN ONE POINT. The freshness decision depends on AGE, so the
threshold is exercised at a boundary *and* in the middle of each side: exactly
at `stale_after`, one second either way, at zero, and far out. A single sample
would pin the constant rather than the comparison.

🔴 AND THE AGE IS FED THROUGH `now`, NOT THROUGH THE MTIME. See `_resolve`: the
first cut of this file backdated the file and let the resolver read the clock,
so the case written as "exactly on the boundary" was measured at
`stale_after + 3e-4` and the boundary itself was never exercised. The socket
tests still backdate — they go through the real handler, which owns its clock —
and their ages are deliberately far from the threshold for that reason.
"""
from __future__ import annotations

import http.client
import os
import re
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from present import generate, serve  # noqa: E402
from testlib import mockbin  # noqa: E402

REGEN_WRAPPER = SCRIPTS / "present" / "run-regen.sh"

#: A stand-in for the real artefact. It carries the two things the server cares
#: about — the `<body>` injection point and a build stamp — and nothing else.
#: Deliberately NOT the real 114 KB page: the server never parses the page's
#: content, and a fixture that takes a second to render makes the age sweep
#: below unaffordable.
FIXTURE_PAGE = (
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
    "<title>devrc &mdash; the agent layer</title></head><body>"
    "<div class=\"wrap\"><header><span>built <b>2026-08-25 05:00 UTC</b></span>"
    "</header><main><p>a measured figure: 41 skills</p></main></div>"
    "</body></html>"
)

#: 🔴 Distinct from `serve.DEFAULT_STALE_AFTER` and not a multiple or divisor of
#: it, so a mutant that hardcodes the module default cannot survive by producing
#: the same answer, and no sweep offset can land on the module constant.
TEST_STALE_AFTER = 7777.0

#: A fixed mtime so ages are exact rather than "N seconds plus however long the
#: test took". Any value; this one is simply a round number in the past.
_MTIME = 1_700_000_000.0


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _artefacts(tmp_path: Path, *, page: str = FIXTURE_PAGE,
               names=("present.html", "present-sanitized.html")) -> Path:
    """Write the artefacts into `tmp_path` with a PINNED mtime."""
    for name in names:
        p = tmp_path / name
        p.write_text(page, encoding="utf-8")
        os.utime(p, (_MTIME, _MTIME))
    return tmp_path


def _resolve(directory: Path, path: str, *, age: float,
             stale_after: float = TEST_STALE_AFTER):
    """`build_response` at an EXACT age — see the module docstring."""
    return serve.build_response(directory, path, stale_after=stale_after,
                                now=_MTIME + age)


def _backdated(tmp_path: Path, age: float, *, page: str = FIXTURE_PAGE) -> Path:
    """For the socket tests only: a real mtime, `age` seconds in the past."""
    now = time.time()
    for name in ("present.html", "present-sanitized.html"):
        p = tmp_path / name
        p.write_text(page, encoding="utf-8")
        os.utime(p, (now - age, now - age))
    return tmp_path


def _has_banner(body: bytes) -> bool:
    """Does this response carry the STALENESS banner?

    🔴 STRUCTURAL, NOT SPELLED. It looks for the word STALE *and* for the
    z-index that makes the banner unmissable — a page that merely contains the
    word "stale" in its prose (the pages in this repo talk about staleness
    constantly) must not read as bannered.
    """
    text = body.decode("utf-8", "replace")
    return "STALE" in text and "2147483647" in text


def _has_clock_banner(body: bytes) -> bool:
    """Its twin for the clock-suspect state. Same structural rule: the headline
    words AND the z-index, so prose about clocks cannot pass for a banner."""
    text = body.decode("utf-8", "replace")
    return "AGE UNKNOWN" in text and "2147483647" in text


def _warned(body: bytes) -> bool:
    """Was the READER told not to trust this page as current, by ANY banner?

    🔴 The central property is about the reader, not about one message. A fourth
    state added later must extend this predicate; a state that fails to is
    exactly the silent disarm the sweep is looking for.
    """
    return _has_banner(body) or _has_clock_banner(body)


def _artefact_bytes_present(body: bytes) -> bool:
    """Does this response contain the artefact's own content?"""
    return b"a measured figure: 41 skills" in body


# --------------------------------------------------------------------------- #
# Controls — run these first; every assertion below leans on them
# --------------------------------------------------------------------------- #

def test_positive_control_the_banner_detector_can_see_a_banner():
    """A detector that matches nothing would make the central property vacuous."""
    banner = serve.stale_banner(90_000, "present.html")
    assert _has_banner(banner.encode("utf-8")), (
        "the banner detector does not recognise the banner this module produces "
        "— every 'no unbannered stale bytes' assertion below is then vacuous")


def test_positive_control_the_banner_detector_is_not_fooled_by_the_word():
    """The real page discusses staleness in prose. A detector that fired on the
    word alone would report every page bannered, which is the same vacuity in
    the other direction."""
    assert not _has_banner(
        b"<html><body><p>a stale page is STALE and that is bad</p></body></html>")


def test_positive_control_the_clock_banner_detector_can_see_its_banner():
    """Its twin. Without this, every clock-suspect assertion below could be
    satisfied by a detector that matches nothing."""
    banner = serve.clock_suspect_banner(90_000, "present.html")
    assert _has_clock_banner(banner.encode("utf-8"))
    assert _warned(banner.encode("utf-8"))


def test_positive_control_the_two_banner_detectors_do_not_see_each_other():
    """🔴 If either detector fired on the other's banner, `_warned` would be one
    predicate wearing two names and the state tests below would pass while
    serving the wrong message to a reader."""
    stale = serve.stale_banner(90_000, "present.html").encode("utf-8")
    clock = serve.clock_suspect_banner(90_000, "present.html").encode("utf-8")
    assert _has_banner(stale) and not _has_clock_banner(stale)
    assert _has_clock_banner(clock) and not _has_banner(clock)


def test_positive_control_the_clock_banner_detector_is_not_fooled_by_the_word():
    assert not _has_clock_banner(
        b"<html><body><p>the AGE UNKNOWN case is discussed here</p></body></html>")


def test_positive_control_the_fixture_page_is_bannerable():
    """If injection could never succeed on the fixture, the stale-with-banner
    branch is unreachable and the sweep only exercises the refusal path."""
    out = serve.inject_banner(FIXTURE_PAGE, serve.stale_banner(90_000, "present.html"))
    assert out is not None
    assert _has_banner(out.encode("utf-8"))
    assert "a measured figure: 41 skills" in out, (
        "injection dropped the page's own content — the banner is added to the "
        "last-good page, it does not replace it")


def test_positive_control_the_artefact_detector_can_see_artefact_bytes():
    assert _artefact_bytes_present(FIXTURE_PAGE.encode("utf-8"))
    assert not _artefact_bytes_present(b"<html>nothing here</html>")


def test_the_stale_branch_is_reachable_at_all(tmp_path):
    """Proof the sweep below is not passing by never leaving the fresh branch."""
    d = _artefacts(tmp_path)
    status, _ctype, hdr, body = _resolve(d, "/", age=TEST_STALE_AFTER * 3)
    assert status == 200
    assert hdr["X-Present-State"] == "stale-bannered"
    assert _has_banner(body)


def test_the_fresh_branch_is_reachable_at_all(tmp_path):
    """Its twin. A resolver that bannered everything would satisfy the central
    property and be useless."""
    d = _artefacts(tmp_path)
    status, _ctype, hdr, body = _resolve(d, "/", age=5.0)
    assert status == 200
    assert hdr["X-Present-State"] == "fresh"
    assert body.decode("utf-8") == FIXTURE_PAGE


def test_the_clock_suspect_branch_is_reachable_at_all(tmp_path):
    """The third state's reachability control, and the one that mattered: the
    branch it guards is reached through a value (`delta < 0`) that the CLAMPED
    age can never carry, so it is exactly the kind of guard that type-checks,
    reads correctly, and never runs."""
    d = _artefacts(tmp_path)
    status, _ctype, hdr, body = _resolve(d, "/", age=-86_400.0)
    assert status == 200
    assert hdr["X-Present-State"] == "clock-suspect-bannered"
    assert _has_clock_banner(body)


# --------------------------------------------------------------------------- #
# 🔴 THE CENTRAL PROPERTY
# --------------------------------------------------------------------------- #

def test_no_response_ever_carries_unbannered_stale_bytes(tmp_path):
    """Swept across the threshold AND across zero: artefact bytes go out ONLY
    when fresh, or with a banner. Never unmarked, at any age.

    The sweep is what makes this a claim about the COMPARISON rather than about
    two ages the author picked — boundary and middle on both sides, and the
    negative side too, which is where the third state lives.

    🔴 The predicate here is `_warned`, not `_has_banner`. The first cut asked
    only about the staleness banner, so a clock-suspect response carrying its
    OWN banner would have counted as unbannered — and, worse, a clock-suspect
    response carrying NOTHING would have counted as fresh, because `_has_banner`
    and `X-Present-Stale` agreed with each other about a page neither had
    looked at properly.
    """
    offsets = [
        -TEST_STALE_AFTER * 40,                 # far in the future
        -TEST_STALE_AFTER,                      # middle of the suspect side
        -serve.CLOCK_SUSPECT_AFTER - 1,         # boundary, suspect side
        -serve.CLOCK_SUSPECT_AFTER,             # EXACTLY on it — still fresh
        -serve.CLOCK_SUSPECT_AFTER / 2,         # sub-tolerance skew: fresh
        0.0,                                    # just written
        1.0,
        TEST_STALE_AFTER / 2,                   # middle of the fresh side
        TEST_STALE_AFTER - 1,                   # boundary, fresh side
        TEST_STALE_AFTER,                       # EXACTLY on it — `<=` is fresh
        TEST_STALE_AFTER + 1,                   # boundary, stale side
        TEST_STALE_AFTER * 2,                   # middle of the stale side
        TEST_STALE_AFTER * 40,                  # far out
    ]
    seen_fresh = seen_stale = seen_suspect = 0
    d = _artefacts(tmp_path)
    for age in offsets:
        status, _ctype, hdr, body = _resolve(d, "/", age=age)
        not_current = hdr["X-Present-Stale"] == "1"
        if _artefact_bytes_present(body):
            if not_current:
                if hdr["X-Present-State"].startswith("clock-suspect"):
                    seen_suspect += 1
                else:
                    seen_stale += 1
                assert _warned(body), (
                    f"age {age}s: the artefact's bytes were served, the server "
                    f"flagged them NOT CURRENT ({hdr['X-Present-State']}), and "
                    "no banner was injected — this is the exact outcome serve.py "
                    "exists to prevent")
            else:
                seen_fresh += 1
                assert not _warned(body), (
                    f"age {age}s: a FRESH page carries a warning banner; a "
                    "banner that is always there is a banner nobody reads")
        assert status in (200, 503)
    assert seen_fresh >= 3 and seen_stale >= 3 and seen_suspect >= 3, (
        f"the sweep exercised {seen_fresh} fresh / {seen_stale} stale / "
        f"{seen_suspect} clock-suspect responses — it must reach all three or "
        "it proves nothing about the classification")


def test_the_threshold_itself_counts_as_fresh(tmp_path):
    """The `<=` half of the comparison, pinned on its own so a flip to `<` is
    visible as a decision rather than absorbed by the sweep."""
    d = _artefacts(tmp_path)
    assert _resolve(d, "/", age=TEST_STALE_AFTER)[2]["X-Present-Stale"] == "0"
    assert _resolve(d, "/", age=TEST_STALE_AFTER + 1)[2]["X-Present-Stale"] == "1"


def test_an_unbannerable_stale_page_is_withheld_not_served(tmp_path):
    """🔴 The refusal path. A page whose shape the injector does not recognise
    is NOT served once stale — the response is the interstitial instead.

    This is the branch a naive `SimpleHTTPRequestHandler` would not have: it
    would serve the bytes and the reader would see a current-looking page.
    """
    d = _artefacts(tmp_path, page="<html><p>no body tag here</p></html>")
    status, _ctype, hdr, body = _resolve(d, "/", age=TEST_STALE_AFTER * 5)
    assert status == 503
    assert hdr["X-Present-State"] == "stale-withheld"
    assert b"no body tag here" not in body, (
        "the stale artefact's bytes were served without a banner because the "
        "injector could not run — withholding is the only honest degradation")
    assert b"withheld" in body.lower()


def test_a_page_that_cannot_be_bannered_is_still_served_while_fresh(tmp_path):
    """The refusal is scoped to STALENESS, not to page shape. A fresh page needs
    no banner, so an unrecognised shape must not break serving it."""
    d = _artefacts(tmp_path, page="<html><p>no body tag here</p></html>")
    status, _ctype, hdr, body = _resolve(d, "/", age=60.0)
    assert status == 200
    assert hdr["X-Present-State"] == "fresh"
    assert b"no body tag here" in body


def test_the_header_and_the_body_agree_about_staleness(tmp_path):
    """`X-Present-Stale` is the machine-readable twin of the banner. A consumer
    reading the header and a human reading the page must not get different
    answers."""
    d = _artefacts(tmp_path)
    for age, expect in ((10.0, "0"), (TEST_STALE_AFTER * 9, "1")):
        _s, _c, hdr, body = _resolve(d, "/", age=age)
        assert hdr["X-Present-Stale"] == expect
        assert _has_banner(body) is (expect == "1")


# --------------------------------------------------------------------------- #
# 🔴 THE THIRD STATE: an artefact written AFTER now
#
# REPLACES `test_an_mtime_in_the_future_clamps_to_zero_rather_than_going_negative`
# — deliberately, and this note says which half of it survived.
#
# That test asserted `X-Present-Age-Seconds == "0"` AND `X-Present-State ==
# "fresh"` for a mtime 500,000s in the future, justified by a `touch -d
# '3 days 4 hours ago'` mistake made during live verification. The FIRST
# assertion was about header FORMAT and is kept below, unchanged in substance:
# the reported age is never negative. The SECOND was a classification, and it
# was wrong — it pinned the exact silent disarm the module exists to prevent.
# A page that is genuinely eight days old reads `fresh`, age 0, no banner, the
# moment a bad RTC is corrected backwards by NTP. So it is replaced, not
# deleted, and the case it was written for (an operator's `touch` typo) still
# resolves sanely: it now says AGE UNKNOWN instead of lying.
# --------------------------------------------------------------------------- #

def test_the_age_header_never_reports_a_negative_number(tmp_path):
    """The surviving half of the old clamp test. `X-Present-Age-Seconds` is
    consumed as an int; a minus sign is a parse hazard for no benefit. Measured
    on BOTH sides of the tolerance, because the clamp and the classification are
    now two different decisions and only one of them moved."""
    d = _artefacts(tmp_path)
    for age in (-1.0, -serve.CLOCK_SUSPECT_AFTER / 2, -serve.CLOCK_SUSPECT_AFTER,
                -serve.CLOCK_SUSPECT_AFTER - 1, -500_000.0):
        _s, _c, hdr, _b = _resolve(d, "/", age=age)
        assert hdr["X-Present-Age-Seconds"] == "0", (
            f"skew {age}s produced {hdr['X-Present-Age-Seconds']!r}")


def test_a_future_mtime_is_clock_suspect_and_never_fresh(tmp_path):
    """🔴 THE DEFECT. A backwards clock jump (bad RTC corrected by NTP) dates
    every already-written file in the future. Under the old clamp a genuinely
    eight-day-old page reported `fresh`, `X-Present-Age-Seconds: 0` and no
    banner — the single reader-facing staleness signal disarming itself, in the
    one situation where the machine's sense of time is what is broken."""
    d = _artefacts(tmp_path)
    _s, _c, hdr, body = _resolve(d, "/", age=-8 * 86_400.0)
    assert hdr["X-Present-State"] == "clock-suspect-bannered", (
        "an artefact dated 8 days in the FUTURE was classified "
        f"{hdr['X-Present-State']!r}. The clamp makes its age 0, so the "
        "staleness comparison cannot see it: without its own state this page is "
        "served as current however old it really is.")
    assert hdr["X-Present-Stale"] == "1", (
        "the machine-readable twin says this page is current. A consumer "
        "reading only the header gets the disarmed answer.")
    assert hdr["X-Present-Clock-Skew-Seconds"] == str(8 * 86_400)


def test_the_reader_and_not_only_the_header_is_told_about_a_future_mtime(tmp_path):
    """🔴 A header is not a reader-facing signal. `stale` gets a full-bleed
    banner; this state has to be visible the same way or it is a machine-only
    fact about a page a human is reading."""
    d = _artefacts(tmp_path)
    _s, _c, _h, body = _resolve(d, "/", age=-8 * 86_400.0)
    assert _has_clock_banner(body), (
        "the clock-suspect response carries no banner — the reader sees a "
        "normal-looking page and only a header disagrees")
    assert b"AGE UNKNOWN" in body
    assert b"present-regen.service" in body, (
        "the banner must name the unit that would fix it")
    assert not _has_banner(body), (
        "a clock-suspect page must not claim a MEASURED staleness; its age is "
        "precisely what is unknown")
    assert b"a measured figure: 41 skills" in body, (
        "the last-good page's own content was dropped — this state banners the "
        "page, it does not replace it")


def test_a_sub_tolerance_future_mtime_is_still_fresh(tmp_path):
    """The other side of the boundary. A page written seconds before the request
    and read across a sub-second NTP slew must not raise a clock alarm — a
    banner that fires on noise is a banner nobody reads, which is the failure
    this whole file is arguing against."""
    d = _artefacts(tmp_path)
    for skew in (1.0, serve.CLOCK_SUSPECT_AFTER / 2, serve.CLOCK_SUSPECT_AFTER):
        _s, _c, hdr, body = _resolve(d, "/", age=-skew)
        assert hdr["X-Present-State"] == "fresh", (
            f"a {skew}s future skew tripped the clock guard; the tolerance is "
            f"{serve.CLOCK_SUSPECT_AFTER}s and the boundary itself is fresh")
        assert not _warned(body)


def test_a_clock_suspect_page_that_cannot_be_bannered_is_withheld(tmp_path):
    """🔴 The refusal applies to the NEW state too. This is the assertion that
    catches a third state wired straight to a 200 — the exact way the
    no-unbannered-bytes contract gets re-opened by someone adding a case."""
    d = _artefacts(tmp_path, page="<html><p>no body tag here</p></html>")
    status, _c, hdr, body = _resolve(d, "/", age=-8 * 86_400.0)
    assert status == 503, (
        f"got {status}: a clock-suspect page the injector cannot banner was "
        "served anyway. The new state has its own path to a 200 and does not go "
        "through the refusal — the no-unbannered-bytes contract is re-opened.")
    assert hdr["X-Present-State"] == "clock-suspect-withheld"
    assert b"no body tag here" not in body, (
        "the artefact's bytes were served with no banner because the injector "
        "could not run — withholding is the only honest degradation")
    assert b"withheld" in body.lower()


def test_the_clock_guard_is_asked_before_the_staleness_comparison(tmp_path):
    """🔴 REACHABILITY, not just correctness.

    `age` is clamped, so a future mtime reaches `age > stale_after` as 0 and
    lands in the FRESH branch every single time. Reorder the two guards and this
    one becomes dead code that still reads correctly — the shape of an
    unreachable guard. Driven with a `stale_after` so small that ANY ordering
    bug shows: at `stale_after = 1`, a genuinely-aged page is stale, and the
    future-dated page must still come back clock-suspect rather than either
    `fresh` or `stale`.
    """
    d = _artefacts(tmp_path)
    _s, _c, past, _b = _resolve(d, "/", age=3600.0, stale_after=1.0)
    assert past["X-Present-State"] == "stale-bannered", (
        "control: with stale_after=1 an hour-old page must be stale, or this "
        "test is not exercising the staleness branch at all")
    _s, _c, future, _b = _resolve(d, "/", age=-3600.0, stale_after=1.0)
    assert future["X-Present-State"] == "clock-suspect-bannered", (
        f"got {future['X-Present-State']!r} — the clock guard did not run "
        "before the staleness comparison")


def test_the_age_header_reports_the_measured_age(tmp_path):
    d = _artefacts(tmp_path)
    for age in (0.0, 1234.0, 500_000.0):
        _s, _c, hdr, _b = _resolve(d, "/", age=age)
        assert hdr["X-Present-Age-Seconds"] == str(int(age))


# --------------------------------------------------------------------------- #
# Absence
# --------------------------------------------------------------------------- #

def test_a_missing_artefact_is_503_and_invents_nothing(tmp_path):
    status, _ctype, hdr, body = _resolve(tmp_path, "/", age=0.0)
    assert status == 503
    assert hdr["X-Present-State"] == "absent"
    assert not _artefact_bytes_present(body)
    assert b"present-regen.service" in body, (
        "the interstitial must name the unit that would fix it — an error page "
        "that hands over no next step is a dead end")


def test_a_missing_sanitized_export_does_not_fall_back_to_the_full_page(tmp_path):
    """🔴 The route table must not substitute. The sanitized copy is the one
    that LEAVES the LAN; serving the unsanitized page under its route would put
    local identifiers into a file someone forwards on purpose."""
    d = _artefacts(tmp_path, names=("present.html",))
    status, _ctype, hdr, body = _resolve(d, "/sanitized", age=10.0)
    assert status == 503
    assert hdr["X-Present-Artefact"] == "present-sanitized.html"
    assert not _artefact_bytes_present(body)


# --------------------------------------------------------------------------- #
# The route table
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path", [
    "/etc/passwd",
    "/../../../../etc/passwd",
    "/present.html/../../secrets",
    "/index.html",
    "/present-sanitized.html.bak",
    "",
])
def test_paths_outside_the_table_are_404_not_files(tmp_path, path):
    """There is no directory handler, so traversal is not a case that needs
    handling — it is simply not a key. This pins that there is no second
    resolution path which could reintroduce one."""
    d = _artefacts(tmp_path)
    status, _ctype, _hdr, _body = _resolve(d, path, age=10.0)
    assert status == 404


def test_the_route_table_and_the_regen_wrapper_name_the_same_artefacts():
    """🔴 SEAM GUARD — a RELATIONSHIP, not a component.

    The writer (`run-regen.sh`) and the reader (`serve.py`) each look correct in
    isolation while disagreeing about a filename, and the symptom is a
    permanently "absent" page served by a perfectly healthy timer. Both halves
    are asserted, so the ledger fails when the set GROWS or SHRINKS on either
    side.
    """
    served = set(serve.ROUTES.values())
    written = set(re.findall(r'^build_one\s+"([^"]+)"',
                             REGEN_WRAPPER.read_text(encoding="utf-8"), re.M))
    assert written, (
        "no `build_one \"<name>\"` call was found in run-regen.sh — the scan is "
        "broken, so this comparison would pass against an empty set")
    assert served == written, (
        "the server's ROUTES and the regen wrapper's outputs disagree:\n"
        f"  served but never written: {sorted(served - written)}\n"
        f"  written but never served: {sorted(written - served)}")


def test_the_wrapper_produces_the_sanitized_export_from_the_same_run():
    """The shareable copy must come off the SAME trigger as the real one. A flag
    you have to remember later is a flag that gets forgotten, and the copy that
    leaves the LAN is the one it matters on."""
    src = REGEN_WRAPPER.read_text(encoding="utf-8")
    m = re.search(r'^build_one\s+"present-sanitized\.html".*$', src, re.M)
    assert m and "--sanitize" in m.group(0), (
        "the sanitized artefact is not built with --sanitize; it would be a "
        "byte-identical second copy of the unsanitized page under a name that "
        "says otherwise")


def test_both_variants_are_reachable_and_distinct(tmp_path):
    d = _artefacts(tmp_path)
    p = d / "present-sanitized.html"
    p.write_text(FIXTURE_PAGE.replace("41 skills", "N skills"), encoding="utf-8")
    os.utime(p, (_MTIME, _MTIME))
    full = _resolve(d, "/", age=10.0)
    san = _resolve(d, "/sanitized", age=10.0)
    assert full[0] == san[0] == 200
    assert full[2]["X-Present-Artefact"] == "present.html"
    assert san[2]["X-Present-Artefact"] == "present-sanitized.html"
    assert full[3] != san[3]


# --------------------------------------------------------------------------- #
# Self-containment — through the GENERATOR's own scanner, not a second copy
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("age", (TEST_STALE_AFTER * 6, -8 * 86_400.0),
                         ids=("stale", "clock-suspect"))
def test_the_bannered_page_is_still_self_contained(tmp_path, age):
    """The artefact's whole point is that it opens from `file://` with no
    network. Injecting a banner must not smuggle in an external reference — and
    BOTH banners are injected code, so both are checked."""
    d = _artefacts(tmp_path)
    _s, _c, _h, body = _resolve(d, "/", age=age)
    problems = generate.self_contained(body.decode("utf-8"))
    assert problems == [], problems


def test_the_interstitials_are_self_contained():
    """They stand in for the artefact, so they clear the same bar."""
    for page in (serve._missing_page("present.html"),
                 serve._unbannerable_page("present.html", "<code>x</code> is old.")):
        assert generate.self_contained(page) == []


# --------------------------------------------------------------------------- #
# humanise — the words the reader actually sees
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("secs,expect", [
    (0, "under a minute"),
    (59, "under a minute"),
    (60, "1 minute"),
    (3600, "1 hour"),
    (3660, "1 hour 1 minute"),
    (86400, "1 day"),
    (90000, "1 day 1 hour"),
    (259200, "3 days"),
])
def test_humanise_says_something_a_person_can_act_on(secs, expect):
    assert serve.humanise(secs) == expect


def test_the_banner_names_the_age_in_words(tmp_path):
    """A banner that says "stale" without saying HOW stale leaves the reader
    exactly where the unmarked page did."""
    d = _artefacts(tmp_path)
    _s, _c, _h, body = _resolve(d, "/", age=3 * 86400 + 2 * 3600)
    assert b"3 days 2 hours" in body


def test_the_banner_names_the_unit_that_would_fix_it(tmp_path):
    d = _artefacts(tmp_path)
    _s, _c, _h, body = _resolve(d, "/", age=TEST_STALE_AFTER * 20)
    assert b"present-regen.service" in body
    assert b"journalctl" in body


# --------------------------------------------------------------------------- #
# 🔴 THE WRITER HALF OF THE SAME CONTRACT: run-regen.sh
#
# The server can only serve a last-good page if the wrapper never destroys one.
# `generate.py` exits 3 and writes nothing when every fact is UNMEASURED, so the
# wrapper builds into a temp file and promotes with `mv` ONLY on exit 0. These
# drive the real bash with a stub interpreter standing in for the generator —
# the failure modes (exit 3, exit 4, exit 0 with no bytes) are the generator's
# documented ones, and none of them may reach the served name.
# --------------------------------------------------------------------------- #

def _fake_repo(tmp_path: Path) -> Path:
    """A directory the wrapper accepts as a checkout. `.git` need only exist —
    the wrapper's precondition is a presence check, not a git operation, and a
    real `git init` here would put a repo inside the test tree for no gain."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return repo


def _stub_python(tmp_path: Path, body: str) -> Path:
    """A stand-in for `python3 -m scripts.present.generate`. It is handed the
    same argv the wrapper builds, so `-o <tmp>` is available to it.

    🔴 Through `testlib.mockbin.write_exec`, which owns the shebang. A test that
    writes `#!/usr/bin/env bash` at runtime execs fine on the dev host and
    ENOENTs in the nix build sandbox — the tier the merge is gated on — so the
    defect is structurally invisible to the tier you are most likely to run.
    Caught here by `test_runtime_shebangs.py` on the first sandbox-tier gate.
    The stub bodies below are POSIX sh.
    """
    return mockbin.write_exec(tmp_path / "stub-python", body)


def _run_regen(tmp_path: Path, stub_body: str, out_dir: Path):
    import subprocess
    repo = _fake_repo(tmp_path)
    stub = _stub_python(tmp_path, stub_body)
    env = dict(os.environ)
    env.update(PRESENT_REPO=str(repo), PRESENT_ARTEFACT_DIR=str(out_dir),
               PRESENT_PYTHON=str(stub), HOME=str(tmp_path))
    return subprocess.run(["bash", str(REGEN_WRAPPER)],
                          capture_output=True, text=True, env=env, timeout=60)


def test_positive_control_the_wrapper_promotes_a_good_build(tmp_path):
    """Without this, every 'the old page survived' assertion below would also
    pass against a wrapper that promotes nothing, ever."""
    out = tmp_path / "artefacts"
    r = _run_regen(tmp_path, 'o=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && '
                            'o="$2"; shift; done; printf NEW > "$o"\n', out)
    assert r.returncode == 0, r.stderr
    assert (out / "present.html").read_text() == "NEW"
    assert (out / "present-sanitized.html").read_text() == "NEW"


def test_the_staging_file_is_promoted_by_a_same_filesystem_rename(tmp_path):
    """🔴 THE ATOMICITY CLAIM, made checkable.

    `mv` is a rename() — genuinely atomic — only WITHIN one filesystem. Across a
    boundary it degrades to copy-then-unlink and a reader can open a half-written
    page. The first cut staged under `${TMPDIR:-/tmp}`, a tmpfs on this host,
    while the artefact directory is on disk: every promote was a cross-device
    copy under a comment asserting atomicity.

    Checked BEHAVIOURALLY — the stub reports the path the wrapper actually
    handed the generator — rather than by grepping the script for `mktemp`,
    because the grep would pass on a `mktemp` pointed anywhere.
    """
    out = tmp_path / "artefacts"
    seen = tmp_path / "staged-paths"
    r = _run_regen(
        tmp_path,
        f'o=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && o="$2"; shift; done; '
        f'printf "%s\\n" "$o" >> {seen}; printf NEW > "$o"\n', out)
    assert r.returncode == 0, r.stderr
    staged = seen.read_text().split()
    assert len(staged) == 2, staged
    for path in staged:
        assert Path(path).parent.parent == out.resolve() or \
            Path(path).is_relative_to(out), (
            f"the wrapper staged {path}, which is not under {out} — the promote "
            "is a cross-filesystem copy, not an atomic rename")


@pytest.mark.parametrize("rc,why", [
    (3, "every fact UNMEASURED — the generator wrote no file"),
    (4, "the page would reach the network"),
    (2, "usage / unwritable"),
])
def test_a_failed_build_never_overwrites_the_last_good_page(tmp_path, rc, why):
    """🔴 THE WRITER-SIDE CONTRACT. A failed regeneration must leave the previous
    page byte-identical AND report failure, so the server keeps serving
    last-good and the operator gets the OnFailure toast."""
    out = tmp_path / "artefacts"
    out.mkdir()
    for name in ("present.html", "present-sanitized.html"):
        (out / name).write_text("LAST-GOOD", encoding="utf-8")

    r = _run_regen(tmp_path,
                   f'o=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && o="$2"; '
                   f'shift; done; printf GARBAGE > "$o"; exit {rc}\n', out)
    assert r.returncode != 0, (
        f"the wrapper reported SUCCESS after a build that failed with {why} — "
        "systemd would not mark the unit failed and nothing would toast")
    for name in ("present.html", "present-sanitized.html"):
        assert (out / name).read_text() == "LAST-GOOD", (
            f"{name} was overwritten by a build that exited {rc}. The served "
            "page is now the failed build's output, and there is no last-good "
            "copy left to fall back to.")


def test_a_zero_exit_that_produced_no_bytes_is_not_promoted(tmp_path):
    """Not a case generate.py has — and promoting an empty file over a good page
    is unrecoverable, so it is checked rather than assumed."""
    out = tmp_path / "artefacts"
    out.mkdir()
    (out / "present.html").write_text("LAST-GOOD", encoding="utf-8")
    (out / "present-sanitized.html").write_text("LAST-GOOD", encoding="utf-8")
    r = _run_regen(tmp_path, 'o=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && '
                            'o="$2"; shift; done; : > "$o"; exit 0\n', out)
    assert r.returncode != 0
    assert (out / "present.html").read_text() == "LAST-GOOD"


def test_the_two_artefacts_are_promoted_independently(tmp_path):
    """A sanitize failure must not withhold a good full page. They are two
    artefacts, not two halves of one — and the exit status still says a
    regeneration failed."""
    out = tmp_path / "artefacts"
    out.mkdir()
    (out / "present-sanitized.html").write_text("LAST-GOOD", encoding="utf-8")
    r = _run_regen(
        tmp_path,
        'o=""; san=0; for a in "$@"; do [ "$a" = "--sanitize" ] && san=1; done; '
        'while [ $# -gt 0 ]; do [ "$1" = "-o" ] && o="$2"; shift; done; '
        '[ "$san" = "1" ] && exit 3; printf NEW > "$o"\n', out)
    assert r.returncode != 0, "a failed sanitize run must still fail the unit"
    assert (out / "present.html").read_text() == "NEW", (
        "the full page was withheld because the SANITIZED build failed")
    assert (out / "present-sanitized.html").read_text() == "LAST-GOOD"


def test_the_wrapper_refuses_a_repo_that_is_not_a_checkout(tmp_path):
    """Every provenance row would come back UNMEASURED and the generator would
    exit 3 on a tree that is simply the wrong one. Fail with a reason instead."""
    import subprocess
    out = tmp_path / "artefacts"
    env = dict(os.environ)
    env.update(PRESENT_REPO=str(tmp_path / "not-a-repo"),
               PRESENT_ARTEFACT_DIR=str(out), HOME=str(tmp_path))
    r = subprocess.run(["bash", str(REGEN_WRAPPER)],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 2
    assert "not a git checkout" in r.stderr


# --------------------------------------------------------------------------- #
# 🔴 THE SEAM THE PURE FUNCTION CANNOT SEE: the handler over a real socket.
#
# Every assertion above is scoped to `build_response`. A handler that computed
# the right answer and then wrote the file with `SimpleHTTPRequestHandler`
# anyway would pass all of them. So these bind a real socket.
# --------------------------------------------------------------------------- #

@pytest.fixture()
def live_server(tmp_path):
    from http.server import ThreadingHTTPServer

    class H(serve._Handler):
        directory = tmp_path
        stale_after = TEST_STALE_AFTER

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield tmp_path, httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)


def _get(port, path, method="GET"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, path)
        r = conn.getresponse()
        return r.status, dict(r.getheaders()), r.read()
    finally:
        conn.close()


def test_over_a_real_socket_fresh_is_verbatim_and_stale_is_bannered(live_server):
    d, port = live_server
    _backdated(d, 30.0)
    status, hdr, body = _get(port, "/")
    assert status == 200
    assert hdr["X-Present-Stale"] == "0"
    assert body.decode("utf-8") == FIXTURE_PAGE, (
        "a FRESH page must go out byte-for-byte — this is a static server and "
        "rewriting a current page is not in its job")

    _backdated(d, TEST_STALE_AFTER * 4)
    status, hdr, body = _get(port, "/")
    assert status == 200
    assert hdr["X-Present-Stale"] == "1"
    assert _has_banner(body)
    assert hdr["Cache-Control"] == "no-store", (
        "a cached copy would outlive its own banner")


def test_over_a_real_socket_an_off_table_path_is_404(live_server):
    d, port = live_server
    _backdated(d, 30.0)
    status, _hdr, _body = _get(port, "/../../etc/passwd")
    assert status == 404


def test_over_a_real_socket_head_carries_the_headers_and_no_body(live_server):
    d, port = live_server
    _backdated(d, TEST_STALE_AFTER * 4)
    status, hdr, body = _get(port, "/", method="HEAD")
    assert status == 200
    assert hdr["X-Present-Stale"] == "1"
    assert body == b""
    assert int(hdr["Content-Length"]) > 0
