#!/usr/bin/env python3
"""Gate on the PROSE and the §0 overview of `scripts/present/`.

Three things live here, and they are related: all three exist because a page
that explains a system decays in ways nobody re-reads.

1. PER-SECTION BYTE CEILINGS. The page was halved once, after the operator read
   it and said "too wordy". Without a ratchet it grows back one reasonable
   paragraph at a time — exactly how the rules file this page describes once
   grew 2.9x in three days with every individual commit correct. This module
   OWNS the ceilings and prints the eviction playbook on failure.

2. THE RESTATEMENT GUARD. Prose may name a MECHANISM, never a LIVE VALUE.
   Moved here from `test_present_sanitize.py`, and NARROWED — see the long note
   on `_authored_visible_text` for why the old spelling was red on an unrelated
   machine's state.

3. OVERVIEW INTEGRITY. §0 is the page's primary navigation, so a stage pointing
   at a dead section or a dead measurement key is a broken map, not a typo.

WHAT COUNTS AS REGRESSION COVERAGE HERE
---------------------------------------
The restatement guard's two narrowing tests ARE regression coverage: the guard
was RED at the branch point (`content.py` carried an SVG coordinate `y="100"`
while the operator's index store held 100 entries) and both narrowing tests fail
against the old predicate. Everything else here is an INVARIANT GUARD and says
so rather than implying otherwise.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from present import content, measure, render  # noqa: E402

# --------------------------------------------------------------------------- #
# 1. Per-section byte ceilings
# --------------------------------------------------------------------------- #

#: Authored bytes a section may render. MEASURED at the post-halving size plus
#: roughly 8% headroom, so a small clarification fits and a new subsection does
#: not. These are the ONLY copy of these numbers — nothing else may restate them.
#:
#: 🔴 RAISING A CEILING IS ALLOWED AND COSTS A SENTENCE. The commit message must
#: name the point that would not fit. What is NOT allowed is raising one to park
#: a paragraph that duplicates a neighbouring section — that is the growth this
#: ratchet exists to catch.
SECTION_CEILINGS: dict[str, int] = {
    "overview": 700,
    "how-to-read": 1600,
    "start-here": 1200,
    "told": 5900,
    "may-do": 2100,
    "verified": 5200,
    "ships": 2100,
    "drift": 2600,
    "observed": 1200,
    "invariants": 3000,
    "cost": 2300,
    "negative": 7700,
    "evidence": 4200,
    "soft": 3000,
    "glossary": 3000,
}

#: The whole page's authored prose. Deliberately TIGHTER than the sum of the
#: per-section ceilings: every section may use its own headroom, but not all of
#: them at once. Without this, fifteen individually-legal growths add up to the
#: page the operator already rejected.
TOTAL_CEILING = 45_000

_PLAYBOOK = """
EVICTION PLAYBOOK — the page is over a prose ceiling.

Do NOT raise the number to make this pass. In order of preference:

  1. CONVERT PROSE TO STRUCTURE. Most of this page describes RELATIONSHIPS, and
     a relationship is a table, a `kv` block or a labelled diagram — not a
     paragraph. This is where the cut came from last time and where it still is.
  2. DELETE THE ELABORATION, KEEP THE POINT. A section earns one screen. If the
     point has landed by the third sentence, the rest is commentary.
  3. CHECK FOR A DUPLICATE. §5 once re-explained what §12 and §14 already
     defined, and §5's first paragraph once restated the diagram directly above
     it. A cross-reference costs a dozen bytes; a restatement costs its length
     AND becomes a second thing that can go stale.
  4. MOVE IT TO THE MEASUREMENT. If the sentence is really about a number, the
     number belongs in `measure.py` and the sentence belongs in the row's
     `detail` field, where it is stamped and cannot rot silently.

Only when none of those apply: raise the ceiling in test_present_content.py and
say in the COMMIT MESSAGE which point would not otherwise fit.

🔴 §11 (negative space) and §12 (the evidence bar) carry ARGUMENTS, not
descriptions. They persuade, and stripping them to bullets destroys what they
do. Their ceilings are deliberately the loosest on the page — cut elsewhere.
"""


def authored_bytes(section) -> int:
    """Bytes of the section's AUTHORED output: title, lede and hand-written blocks.

    EXCLUDES `measure`, `svgm` and `unbanner` blocks. Those render from the live
    MeasurementSet, so their size is a property of the machine the page is built
    on — folding them in would make this ceiling drift with the operator's
    index-store size and fail on a different host for no authored reason. What
    is left is exactly the text a human typed, which is the thing being rationed.
    """
    empty = measure.MeasurementSet()
    total = len(section.title) + len(section.lede) + len(section.stub)
    for kind, payload in section.blocks:
        if kind in ("measure", "svgm", "unbanner"):
            continue
        total += len(render._blocks(((kind, payload),), empty))
    return total


def test_every_section_is_under_its_prose_ceiling():
    """INVARIANT GUARD, and the ratchet that keeps the halving from unwinding."""
    over = []
    for s in content.SECTIONS:
        ceiling = SECTION_CEILINGS.get(s.slug)
        assert ceiling is not None, (
            f"section {s.slug!r} has no entry in SECTION_CEILINGS. A new section "
            "must be given one in the SAME commit, at its written size plus "
            "modest headroom — otherwise it is the one section that can grow "
            "without limit."
        )
        n = authored_bytes(s)
        if n > ceiling:
            over.append(f"  §{s.number} {s.slug}: {n} B > {ceiling} B "
                        f"(over by {n - ceiling})")
    assert not over, "\n".join(["", *over, _PLAYBOOK])


def test_the_ceiling_table_names_no_section_that_does_not_exist():
    """SEAM GUARD, the reverse direction.

    A stale entry is not harmless: it reserves budget for a section that was
    renamed or removed, so the TOTAL_CEILING arithmetic silently loosens.
    """
    slugs = {s.slug for s in content.SECTIONS}
    orphans = sorted(set(SECTION_CEILINGS) - slugs)
    assert not orphans, (
        f"SECTION_CEILINGS names section(s) that do not exist: {orphans}. "
        "Remove the entry in the same commit that removed the section."
    )


def test_the_whole_page_is_under_the_total_prose_ceiling():
    """INVARIANT GUARD. Fifteen legal growths must not sum to an illegal page."""
    total = sum(authored_bytes(s) for s in content.SECTIONS)
    assert total <= TOTAL_CEILING, (
        f"\n  authored prose is {total} B against a ceiling of {TOTAL_CEILING} B "
        f"(over by {total - TOTAL_CEILING})\n{_PLAYBOOK}"
    )


def test_the_ceilings_leave_real_headroom_but_not_a_second_page():
    """🔴 The ratchet's OWN control — a ceiling nothing can reach is not a ratchet.

    Ceilings set far above the written size would pass forever and read as
    enforcement while providing none. Assert the page is actually NEAR them: the
    written size must be a substantial fraction of what is permitted.
    """
    written = sum(authored_bytes(s) for s in content.SECTIONS)
    permitted = sum(SECTION_CEILINGS[s.slug] for s in content.SECTIONS)
    assert written <= permitted, "a section is over — see the ceiling test"
    assert written / permitted > 0.80, (
        f"the ceilings permit {permitted} B for {written} B of prose "
        f"({written / permitted:.0%} used). They are so loose that a large "
        "addition would pass, which is indistinguishable from having no "
        "ratchet. Re-pin them to the current sizes plus modest headroom."
    )


# --------------------------------------------------------------------------- #
# 2. The restatement guard — prose may name a mechanism, never a live value
# --------------------------------------------------------------------------- #

#: A STANDALONE quantity: three or more digit/comma characters that are not
#: embedded in an identifier.
#:
#: 🔴 THE IDENTIFIER BOUNDARY IS LOAD-BEARING, NOT TIDINESS. `repo.head` measures
#: to something like "8da6ac15 on worktree-agent-a35f5cfeca060d0e8 — clean". A
#: bare `\\d[\\d,]{2,}` pulls "060" out of that git identifier and then treats it
#: as a number the prose must not contain — a false positive that fires on
#: whatever branch name the build happens to run from, i.e. a test that passes by
#: accident of the environment.
_QUANTITY = re.compile(r"(?<![0-9A-Za-z])\d[\d,]{2,}(?![0-9A-Za-z])")


def live_quantity_tokens(ms) -> set[str]:
    """Every standalone number the generator measures, with and without commas."""
    live: set[str] = set()
    for m in ms.measured:
        for token in _QUANTITY.findall(m.value or ""):
            live.add(token)
            live.add(token.replace(",", ""))
    return live


def _strip_markup(blob: str) -> str:
    """Drop every tag AND its attributes, leaving only text nodes."""
    return re.sub(r"<[^>]*>", " ", blob)


def _authored_visible_text() -> str:
    """The page's authored VISIBLE TEXT — what a reader actually reads.

    🔴 THIS SCANS TEXT, NOT SOURCE, AND THAT NARROWING IS THE WHOLE FIX. The
    previous spelling grepped the raw `content.py` source, which meant SVG
    geometry was in scope: `<text x="20" y="100" class="dhead">` collided with a
    measured "100 entries" and turned the suite red with no code change, purely
    because the operator's index store had grown to 100 entries. Three things
    made that intolerable:

      * it is MACHINE-STATE-dependent, not tree-dependent — it passed for the
        author and failed for the auditor on the same commit;
      * the sandbox tier is structurally BLIND to it (with no home directory the
        index-store row goes UNMEASURED), so the tier the merge is gated on
        could not see a failure the dev-host tier reported;
      * roughly two dozen other coordinates sit in the range a growing store
        walks straight through, so it would re-fire on its own schedule.

    Stripping tags removes attributes with them, so coordinates, class names and
    `href` targets are all out of scope, while a number written into a diagram
    LABEL — a real restatement — is still caught, because a label is a text node.

    Measurement blocks and the live §0 diagram are excluded: they render FROM the
    measurement layer by construction, so a number appearing there is the system
    working, not prose rotting.
    """
    empty = measure.MeasurementSet()
    chunks: list[str] = []
    for s in content.SECTIONS:
        chunks += [s.title, s.lede, s.stub]
        for kind, payload in s.blocks:
            if kind in ("measure", "svgm", "unbanner"):
                continue
            chunks.append(render._blocks(((kind, payload),), empty))
    return _strip_markup(" ".join(c for c in chunks if c))


def collisions(prose: str, tokens: set[str]) -> list[str]:
    """Which live numbers the prose restates. The guard's whole predicate."""
    return sorted(t for t in tokens if _QUANTITY.search(prose) and t in prose)


def _live_set():
    env = measure.Env(repo=REPO_ROOT, home=Path.home(),
                      claude_dir=Path.home() / ".claude",
                      index_store=Path.home() / ".claude" / "analyze-service-index",
                      allow_systemd=False, allow_network=False)
    return measure.take(env)


def test_the_page_never_restates_a_live_measured_number():
    """🔴 SEAM GUARD — the one that keeps the page from rotting.

    The premise is that prose names MECHANISMS and only measured rows carry
    VALUES. The moment a paragraph restates a number the generator measures,
    that paragraph starts aging, and this repo has measured its own prose false
    in both directions.

    Historical measurements (a killed proposal's numbers, a dated incident) are
    permanently true and deliberately NOT in scope: they cannot collide, because
    they are not what the generator measures.
    """
    ms = _live_set()
    assert ms.measured, "nothing measured — this guard would then be vacuous"
    found = collisions(_authored_visible_text(), live_quantity_tokens(ms))
    assert not found, (
        f"the page's prose restates {found}, which the generator measures live. "
        "Name the mechanism in prose and let the measured row carry the value."
    )


def test_the_guard_still_catches_a_real_prose_restatement():
    """🔴 NEGATIVE CONTROL. Narrowing a guard must not make it inert.

    Take a number the live registry actually produces, write it into a sentence
    the way a well-meaning author would, and watch the guard fire. Without this,
    a regex that matches nothing would report the page clean and be
    indistinguishable from a working guard.
    """
    ms = _live_set()
    tokens = live_quantity_tokens(ms)
    assert tokens, (
        "the live registry produced no multi-digit values, so this control "
        "cannot run — the guard above would be vacuous and must not be trusted"
    )
    victim = sorted(tokens)[0]
    assert collisions(f"The store currently holds {victim} entries.", tokens) == [victim]


def test_the_guard_ignores_svg_geometry():
    """🔴 REGRESSION COVERAGE — red at the branch point, and the reason for this file.

    `content.py` carried `<text x="20" y="100" class="dhead">one change</text>`
    while the operator's index store held 100 entries. Grepping the SOURCE made
    that a collision; scanning the stripped TEXT does not. The same number in a
    diagram LABEL must still be caught — otherwise the narrowing went too far and
    opened a hole where a diagram can restate anything it likes.
    """
    tokens = {"100"}
    raw = '<text x="20" y="100" class="dhead">one change</text>'
    # First: the OLD behaviour really did collide. Without this the test could
    # pass against a predicate that never matched anything, and the narrowing
    # would look necessary when it was not.
    assert collisions(raw, tokens) == ["100"], (
        "the unstripped form no longer collides, so this test no longer pins "
        "the narrowing it claims to pin"
    )
    assert collisions(_strip_markup(raw), tokens) == [], (
        "an SVG coordinate is being read as a restated quantity — this is the "
        "exact false positive that made the old guard machine-state-dependent"
    )
    label = _strip_markup('<text x="20" y="44" class="dsub">100 entries</text>')
    assert collisions(label, tokens) == ["100"], (
        "the narrowing went too far: a number written into a diagram LABEL is a "
        "real restatement and must still be caught"
    )


def test_a_git_identifier_is_not_mined_for_quantities():
    """🔴 REGRESSION COVERAGE for the second, unreported half of the same defect.

    `repo.head` renders a short sha and the branch name. Under the old pattern a
    branch like `worktree-agent-a35f5cfeca060d0e8` yielded the token "060", which
    would then have to be kept out of every sentence on the page — a constraint
    that changes with whatever branch the build runs from.
    """
    ms = measure.MeasurementSet()
    ms.items.append(measure.Measurement(
        key="repo.head", label="l", section="how-to-read",
        status=measure.MEASURED, asof="2000-01-01 00:00 UTC", source="s",
        value="8da6ac15 on worktree-agent-a35f5cfeca060d0e8 — clean"))
    assert live_quantity_tokens(ms) == set(), (
        "digits embedded in a git identifier are being treated as measured "
        "quantities; the prose would then have to avoid an arbitrary number "
        "that changes with the branch name"
    )


def test_the_prose_corpus_is_not_empty():
    """🔴 POSITIVE CONTROL on the corpus itself.

    Every assertion above is satisfied by an empty string. This feeds the
    extractor the real page and watches the number move: a corpus that collapsed
    to nothing — a renamed block kind, a changed `_blocks` signature — would make
    the whole section pass while checking no prose at all.
    """
    text = _authored_visible_text()
    assert len(text) > 20_000, f"the authored prose corpus is only {len(text)} B"
    assert "measured" in text and "UNMEASURED" in text


# --------------------------------------------------------------------------- #
# 3. §0 overview integrity — it is the navigation, so a dead link is a dead map
# --------------------------------------------------------------------------- #


def test_every_overview_stage_points_at_a_real_section():
    """SEAM GUARD. A stage linking nowhere is a broken map, not a typo.

    `test_present_render.py` checks cross-links found in `SECTIONS`; the stage
    links live in a diagram FUNCTION and are invisible to that scan, so they
    need their own pin.
    """
    slugs = {s.slug for s in content.SECTIONS}
    for label, slug, _sub, _key, _tone in content.OVERVIEW_STAGES:
        assert slug in slugs, f"overview stage {label!r} links to #{slug}, not a section"


def test_every_overview_stage_draws_its_count_from_the_registry():
    """🔴 SEAM GUARD. The counts must come from the measurement layer, never a literal.

    Pinning the KEY against the live registry is what makes that structural: a
    stage cannot show a number the generator does not produce, and a measurer
    renamed out from under a stage fails here instead of rendering UNMEASURED
    forever on every host.
    """
    registered = {e[0] for e in measure.REGISTRY}
    for label, _slug, _sub, key, _tone in content.OVERVIEW_STAGES:
        assert key in registered, (
            f"overview stage {label!r} draws its count from {key!r}, which no "
            "measurer produces — it would render UNMEASURED on every build"
        )


def test_the_overview_renders_the_measured_value_and_never_a_bare_number():
    """POSITIVE CONTROL. The diagram must actually print what was measured."""
    ms = _live_set()
    svg = content.diagram_overview(ms)
    shown = 0
    for _label, _slug, _sub, key, _tone in content.OVERVIEW_STAGES:
        m = ms.by_key(key)
        if m is not None and m.measured:
            assert m.value in svg or all(w in svg for w in m.value.split()), (
                f"stage {key!r} measured {m.value!r} but the diagram does not show it"
            )
            shown += 1
    assert shown >= 1, (
        "no stage measured on this build, so this control proved nothing about "
        "the diagram's ability to display a value"
    )


def test_an_unmeasured_stage_renders_the_word_not_a_blank():
    """🔴 THE RULE THIS PAGE EXISTS TO TEACH, applied to its own opening diagram.

    A blank, a dash or a zero in a diagram reads as "nothing there" and is
    byte-identical to a stage that measured clean. Both routes to absence are
    checked: a row that came back UNMEASURED, and a key no measurer produces at
    all — the second has no row to be missing from, so a fallback to empty text
    would leave the picture looking complete.
    """
    from dataclasses import replace

    ms = _live_set()
    key = content.OVERVIEW_STAGES[0][3]
    gone = content.OVERVIEW_STAGES[1][3]

    items = []
    for m in ms.items:
        if m.key == gone:
            continue                      # the key vanishes entirely
        if m.key == key:
            m = replace(m, status=measure.UNMEASURED, value=None,
                        reason="a synthetic absence, for this control")
        items.append(m)
    bad = measure.MeasurementSet()
    bad.items = items

    svg = content.diagram_overview(bad)
    assert svg.count("UNMEASURED") >= 4, (
        "each absent stage must render the WORD in the box and name itself in "
        "its tooltip — two mentions per stage, two stages"
    )
    assert svg.count("dbox-warn") == 2, "an absent stage must be visibly toned"
    assert "a synthetic absence, for this control" in svg, (
        "the reason for an UNMEASURED stage must be reachable from the diagram"
    )
    # The tooltip is HTML-escaped on the way out (the reason can carry a path or
    # a stderr string), so assert on the escaped form a browser would show.
    import html as _html
    assert _html.escape(f"no measurer produces {gone!r}") in svg, (
        "a stage whose key no measurer produces must say so — it has no row to "
        "be missing from, so silence here is invisible"
    )
    # And the control that makes the three above meaningful: with everything
    # measured, none of that fires.
    assert "dbox-warn" not in content.diagram_overview(ms), (
        "the fully-measured diagram is also flagging absence, so the assertions "
        "above would pass no matter what the code did"
    )


_ENTITY = re.compile(r"&[a-zA-Z]+;|&#\d+;")


def test_no_html_entity_sits_in_a_field_that_gets_escaped():
    """🔴 REGRESSION COVERAGE — this shipped broken and only a screenshot caught it.

    `render.py` escapes some fields and trusts others. Section titles, ledes,
    note titles and card titles all go through `esc()`, which turns `&mdash;`
    into `&amp;mdash;` — so the reader sees the literal text "&mdash;" in the
    NAVIGATION and in an `<h2>`. Block bodies are authored HTML and entities
    there are correct, which is exactly why the mistake is easy: the same string
    is right in one field and wrong in the next one down.

    Use a literal character in an escaped field. Found on the rewrite that added
    §0: six occurrences, two of them in the sidebar every reader sees first.
    """
    offenders = []
    for s in content.SECTIONS:
        for field, text in (("title", s.title), ("lede", s.lede)):
            for m in _ENTITY.finditer(text or ""):
                offenders.append(f"  §{s.number} {field}: {m.group(0)}")
        for kind, payload in s.blocks:
            if kind == "note":
                for m in _ENTITY.finditer(payload[1] or ""):
                    offenders.append(f"  §{s.number} note title: {m.group(0)}")
            elif kind == "cards":
                for title, _body in payload:
                    for m in _ENTITY.finditer(title or ""):
                        offenders.append(f"  §{s.number} card title: {m.group(0)}")
    assert not offenders, "\n".join([
        "an HTML entity sits in a field render.py escapes, so it will render "
        "literally on the page:", *offenders,
        "  -> use the literal character (— … · ×) instead."])


def test_the_rendered_page_shows_no_double_escaped_entity():
    """🔴 POSITIVE-CONTROL SIDE of the guard above: check the ARTEFACT, not the source.

    The test above is a claim about `content.py`. This one scans the bytes a
    reader opens, so it still fires if a NEW escaped field is added to
    `render.py` that the source-level scan does not know to look at.
    """
    page = render.build_html(_live_set(), sanitized=False)
    assert "&amp;mdash;" not in page and "&amp;#" not in page, (
        "the page contains a double-escaped entity — it will render as literal "
        "text like '&mdash;' to the reader"
    )


def test_every_cross_reference_names_its_targets_actual_number():
    """🔴 SEAM GUARD. A link saying “§5” must point at the section numbered 5.

    `test_present_render.py` already proves every cross-link resolves to a REAL
    section; it cannot see that the §N a reader is told to look for matches the
    §N they land on. Those come apart the moment a section is inserted — which is
    exactly what happened when the overview was added as §0 and everything below
    it shifted by one. A link that resolves to the right anchor while promising
    the wrong number is worse than a dead link: nothing errors, and the reader
    quietly loses trust in the numbering.
    """
    ms = _live_set()
    page = render.build_html(ms, sanitized=False)
    numbers = {s.slug: s.number for s in content.SECTIONS}

    links = re.findall(r'<a href="#([a-z0-9-]+)"[^>]*>(.*?)</a>', page, re.S)
    assert links, "no in-page anchors found at all — this guard would be vacuous"
    labelled = [(slug, text) for slug, text in links if "§" in text]
    assert labelled, (
        "no cross-reference carries a §N label, so this guard checked nothing. "
        "Either the prose stopped using them or the link shape changed."
    )

    wrong = []
    for slug, text in labelled:
        m = re.search(r"§(\d+)", text)
        if m and numbers.get(slug) != m.group(1):
            wrong.append(f"  link says §{m.group(1)} but #{slug} is "
                         f"§{numbers.get(slug)}")
    assert not wrong, "\n".join(["cross-reference numbers are stale:", *wrong])


def test_a_long_measured_value_wraps_instead_of_being_truncated():
    """INVARIANT GUARD. Silently shortening a value is the same class of failure
    as rendering it blank — the reader cannot tell a clipped number from a small
    one."""
    long = "123,456 widgets across 78 scopes and 9 hosts, plus extras"
    lines = content._wrap(long, 38, 2)
    assert len(lines) == 2
    assert " ".join(lines) == long, "the wrapper dropped part of a measured value"
