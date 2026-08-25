#!/usr/bin/env python3
"""Gate on the RENDERED ARTEFACT of `scripts/present/` — self-containment first.

THE CENTRAL PROPERTY
--------------------
The hosted page and the portable export are the SAME FILE. There is no second
renderer and nothing to keep in sync, which is only sound if the one file is
genuinely self-contained: it must open from `file://` with no network at all.

🔴 THE CHECK IS ON THE OUTPUT BYTES, NOT ON THE GENERATOR'S INTENTIONS. Asserting
"we never write a script tag" is a claim about the code and stays true right up
until someone adds a diagram helper. Scanning the artefact that will actually be
opened is a claim about the artefact, and it survives that.

WHAT COUNTS AS REGRESSION COVERAGE HERE
---------------------------------------
Nothing, and this module says so rather than implying otherwise. `present/` is
new in the commit that adds this file; at the base ref none of it imports, so no
test here can be shown red on pre-change code. These are INVARIANT GUARDS.

What they are NOT is vacuous, because each carries its own control:
`test_the_self_containment_scan_can_actually_go_red` feeds the scanner pages
built from REALISTIC external references and watches every one of them fail —
without it, a scanner with a broken pattern would report the real page clean and
be indistinguishable from a working one.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# Plain import, deliberately — see the note in test_present_measure.py.
from present import content, generate, measure, render  # noqa: E402

GENERATOR = SCRIPTS / "present" / "generate.py"


def _fake_set(*rows) -> measure.MeasurementSet:
    ms = measure.MeasurementSet()
    ms.items.extend(rows)
    return ms


def _measured(key, section, value="7 things", **kw) -> measure.Measurement:
    return measure.Measurement(
        key=key, label=f"label for {key}", section=section,
        status=measure.MEASURED, asof="2000-01-01 00:00 UTC",
        source="a synthetic source", value=value, **kw)


def _unmeasured(key, section, reason="the probe could not look") -> measure.Measurement:
    return measure.Measurement(
        key=key, label=f"label for {key}", section=section,
        status=measure.UNMEASURED, asof="2000-01-01 00:00 UTC",
        source="(not reached)", reason=reason, settle="run the settling command")


# --------------------------------------------------------------------------- #
# Self-containment
# --------------------------------------------------------------------------- #


def test_the_self_containment_scan_can_actually_go_red():
    """NEGATIVE CONTROL — and built from REALISTIC references, not a fixture.

    🔴 `claude/RULES.md`: a scanner fed its own canonical example scans clean.
    Every page below is the shape a real regression would take — someone adding
    a CDN chart library, a font stylesheet, a tracking pixel — not a synthetic
    string chosen because the pattern matches it.
    """
    realistic = [
        '<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>',
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter">',
        "<style>@import url(https://example.org/theme.css);</style>",
        '<style>body{background:url(https://example.org/bg.png)}</style>',
        '<img srcset="https://example.org/a.png 1x">',
        '<iframe src="about:blank"></iframe>',
        '<script src="https://unpkg.com/d3@7" integrity="sha384-abc" crossorigin="anonymous"></script>',
    ]
    for snippet in realistic:
        page = f"<!doctype html><html><body>{snippet}</body></html>"
        problems = generate.self_contained(page)
        assert problems, f"the scan reported this page CLEAN: {snippet!r}"


def test_the_self_containment_scan_does_not_fire_on_the_svg_namespace():
    """POSITIVE CONTROL's complement: the one allowed token must not trip it.

    The SVG namespace URI is an XML identifier and is never fetched. A scanner
    that flagged it would be permanently red, which trains people to ignore it —
    worse than no gate at all.
    """
    page = f'<!doctype html><html><body><svg xmlns="{generate._ALLOWED_URI}"></svg></body></html>'
    assert generate.self_contained(page) == []


def test_the_real_page_references_no_external_host():
    """THE headline invariant guard: the artefact opens with no network."""
    env = measure.Env(repo=REPO_ROOT, home=Path.home(),
                      claude_dir=Path.home() / ".claude",
                      index_store=Path.home() / ".claude" / "analyze-service-index",
                      allow_systemd=False)
    ms = measure.take(env)
    page = render.build_html(ms, sanitized=False)
    problems = generate.self_contained(page)
    assert problems == [], f"the generated page is NOT self-contained: {problems}"
    assert "<script src" not in page
    assert page.count("<svg") >= 1, (
        "no diagram was inlined — a page with zero SVGs would pass every "
        "self-containment check by containing nothing to check"
    )


def test_the_page_inlines_its_css_and_its_js():
    ms = _fake_set(_measured("repo.head", "how-to-read"))
    page = render.build_html(ms, sanitized=False, sections=(content.SECTIONS[0],))
    assert "<style>" in page and "</style>" in page
    assert "<script>" in page and "</script>" in page
    assert "prefers-color-scheme" in page, (
        "the page must render in the reader's theme; a light-only page sitting "
        "in a dark viewer is a rendering bug, not a preference"
    )


# --------------------------------------------------------------------------- #
# UNMEASURED must be VISIBLE
# --------------------------------------------------------------------------- #


def test_an_unmeasured_row_renders_with_its_reason_and_its_settling_command():
    """INVARIANT GUARD. Absence is rendered, not omitted and not blanked."""
    row = _unmeasured("k.x", "soft", reason="the audit log is not in this tree")
    html = render.render_measurement(row)
    assert "UNMEASURED" in html
    assert "the audit log is not in this tree" in html
    assert "run the settling command" in html
    assert "Why not:" in html


def test_a_section_asking_for_an_unknown_key_renders_a_defect_not_a_gap():
    """INVARIANT GUARD, and the one people will hit.

    If a section names a measurement key nobody produces, the page must SAY
    a fact is missing. Rendering nothing would leave the reader unable to tell
    a missing measurement from a missing section.
    """
    section = content.Section(
        slug="x", number="9", title="T", lede="L",
        blocks=(("measure", "no.such.key"),))
    page = render.build_html(_fake_set(_measured("a", "x")), sanitized=False,
                             sections=(section,))
    assert "no.such.key" in page and "UNMEASURED" in page


def test_the_masthead_reports_the_measured_and_unmeasured_counts():
    """INVARIANT GUARD. The reader must see the ratio before reading a number."""
    ms = _fake_set(_measured("a", "how-to-read"), _unmeasured("b", "how-to-read"))
    page = render.build_html(ms, sanitized=False, sections=(content.SECTIONS[0],))
    assert "1 measured / 1 unmeasured" in page


def test_every_measured_row_carries_its_own_date_stamp():
    """INVARIANT GUARD. Per row, not per page.

    Different facts age at different speeds; one footer date would claim the
    same freshness for a byte count and a timer roster.
    """
    ms = _fake_set(_measured("a", "x"), _unmeasured("b", "x"))
    section = content.Section(
        slug="x", number="9", title="T", lede="L",
        blocks=(("measure", "a"), ("measure", "b")))
    page = render.build_html(ms, sanitized=False, sections=(section,))
    assert page.count("2000-01-01 00:00 UTC") >= 2, (
        "a stamp is missing from a row — including the unmeasured one, which "
        "still records WHEN the attempt was made"
    )


def test_measured_text_is_escaped_before_its_backticks_are_honoured():
    """🔴 INVARIANT GUARD on the order of two operations that must not swap.

    Measurement text is built from values read off the local machine — file
    paths, error strings, a subprocess's stderr. Interpolating it raw would let
    a filename containing a tag rewrite the page. Escaping first makes that
    impossible; marking up first would not.
    """
    out = render.rich("a path <script>alert(1)</script> and `some/code.py`")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "<code>some/code.py</code>" in out, "backticks were not honoured"


def test_a_backtick_in_a_measured_value_does_not_render_literally():
    """REGRESSION COVERAGE — this was RED and was found by reading the artefact.

    The measurers write their detail text with backticks around identifiers, in
    the same idiom the rest of the repo uses. The first cut interpolated that
    text raw, so every backtick appeared on the page as a literal character. Not
    a crash, not a test failure — just a page that read as unproofed. It was
    found by rendering the page and looking at it, which is the control this
    suite otherwise cannot provide.
    """
    row = _measured("k.t", "soft", detail="the flag is `--sanitize` here")
    html = render.render_measurement(row)
    assert "<code>--sanitize</code>" in html
    assert "`--sanitize`" not in html


def test_authored_markup_in_a_subheading_is_honoured_not_shown_as_source():
    """REGRESSION COVERAGE — also found only by reading the rendered artefact.

    Sub-headings were escaped while the paragraphs beside them were not, so a
    heading naming a command-line flag rendered its own `<code>` tags as visible
    text. Green suite, correct measurements, and a page that read as unproofed.
    """
    section = content.Section(
        slug="x", number="9", title="T", lede="L",
        blocks=(("h3", "A <code>--rearm</code> flag"),))
    page = render.build_html(_fake_set(), sanitized=False, sections=(section,))
    assert "<h3>A <code>--rearm</code> flag</h3>" in page
    assert "&lt;code&gt;" not in page


def test_a_long_table_folds_but_never_truncates():
    """INVARIANT GUARD against the quietest failure a table can have.

    A table that showed the first N rows and stopped would drop measured facts
    with no marker. Folding into a disclosure keeps every row in the artefact.
    """
    rows = tuple((f"item-{i}", str(i)) for i in range(render._ROW_LIMIT + 9))
    row = _measured("k.big", "soft", columns=("name", "n"), rows=rows)
    html = render.render_measurement(row)
    for name, _ in rows:
        assert name in html, f"{name} was truncated away"
    assert "<details" in html


# --------------------------------------------------------------------------- #
# The CLI's build verdicts
# --------------------------------------------------------------------------- #


def test_the_cli_writes_a_page_and_reports_the_counts(tmp_path):
    """POSITIVE CONTROL for the CLI: it must be able to succeed."""
    out = tmp_path / "page.html"
    proc = subprocess.run(
        [sys.executable, str(GENERATOR), "-o", str(out), "--no-systemd",
         "--repo", str(REPO_ROOT)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr
    assert out.is_file() and out.stat().st_size > 20_000
    assert re.search(r"\d+ measured, \d+ unmeasured", proc.stderr)


def test_the_cli_fails_loudly_when_nothing_can_be_measured(tmp_path):
    """NEGATIVE CONTROL for the build verdict — the important one.

    Pointed at a directory that is not a devrc checkout, every measurer comes
    back UNMEASURED. The generator must exit non-zero AND WRITE NOTHING, because
    that page would look careful and be broken.
    """
    empty = tmp_path / "not-a-checkout"
    empty.mkdir()
    out = tmp_path / "should-not-exist.html"
    proc = subprocess.run(
        [sys.executable, str(GENERATOR), "-o", str(out), "--no-systemd",
         "--repo", str(empty)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=600)
    assert proc.returncode == 3, (
        f"expected the all-unmeasured verdict (3), got {proc.returncode}. "
        f"stderr:\n{proc.stderr}")
    assert not out.exists(), "a page was written for a build with nothing measured"
    assert "BUILD FAILED" in proc.stderr
    assert "UNMEASURED" in proc.stderr


def test_check_mode_writes_nothing(tmp_path):
    out = tmp_path / "nope.html"
    proc = subprocess.run(
        [sys.executable, str(GENERATOR), "-o", str(out), "--check", "--no-systemd",
         "--repo", str(REPO_ROOT)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr
    assert not out.exists()


# --------------------------------------------------------------------------- #
# The spine
# --------------------------------------------------------------------------- #


def test_every_section_is_either_written_or_visibly_marked_not_written():
    """INVARIANT GUARD. A thin section must announce itself.

    A partial page that is honest about being partial is a good outcome; a
    complete-looking page with an empty section is not. A section with neither
    blocks nor a stub would render as a heading and a lede, which reads as
    finished.
    """
    for s in content.SECTIONS:
        assert s.blocks or s.stub, (
            f"section {s.slug!r} has no content and no NOT-YET-WRITTEN marker — "
            "it would render as a finished but empty section")
        assert s.lede and s.title


def test_the_nav_links_to_every_section_and_only_to_real_ones():
    """SEAM GUARD, both directions.

    A nav entry for a missing section is a dead link; a section with no nav
    entry is unreachable content that reads as absent.
    """
    ms = _fake_set(*[_measured(e[0], e[1]) for e in measure.REGISTRY])
    page = render.build_html(ms, sanitized=False)
    for s in content.SECTIONS:
        assert f'href="#{s.slug}"' in page, f"{s.slug} has no nav entry"
        assert f'id="{s.slug}"' in page, f"{s.slug} has no anchor"
    nav_targets = set(re.findall(r'nav.*?href="#([a-z0-9-]+)"', page))
    ids = {s.slug for s in content.SECTIONS}
    assert nav_targets <= ids | set(), f"nav points at non-sections: {nav_targets - ids}"


def test_internal_cross_links_resolve_to_real_sections():
    """SEAM GUARD. A cross-reference in prose must not be a dead anchor."""
    ids = {s.slug for s in content.SECTIONS}
    blob = repr(content.SECTIONS)
    for target in set(re.findall(r'href=\\?"#([a-z0-9-]+)\\?"', blob)):
        assert target in ids, f"prose links to #{target}, which is not a section"
