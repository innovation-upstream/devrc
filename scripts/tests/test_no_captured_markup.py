#!/usr/bin/env python3
"""REPO-WIDE structural guard: no CAPTURED CONTENT in a committed `.html`/`.txt`.

The fourth sibling of `test_no_public_ips.py` (IP literals),
`test_no_client_hostnames.py` (client subdomains) and `test_no_captured_text.py`
(captured free text in JSON/JSONL). The scan is the MARKUP HALF of
`scripts/testlib/captured_text_scan.py`, whose banner comment explains — with the
measured counts — why HTML and plain text need their own rules rather than a
wider `SCANNED_SUFFIXES`, and what those rules structurally cannot see.

WHY THESE TWO SUFFIXES
----------------------
Both were picked because they are ALREADY OCCUPIED in this tree, not because
they round out a list. MEASURED over 23 tracked candidates:

  * `.html` — `scripts/dl-router/tests/fixtures/forum-thread-page.html` is a
    structure-faithful capture of a live forum thread carrying 8 `data-user-id`,
    4 `data-author`, 10 `itemprop="name"` and 4 `itemprop="author"` occurrences.
    This repo drives a real logged-in browser and files downloads by page
    context, so a scraped page landing in a fixture directory is the likeliest
    future shape of this exposure.

  * `.txt` — `scripts/repo-cos/tests/fixtures/initiatives_current_slugs.txt` is a
    hand-refreshed snapshot of a live board: 144 distinct project slugs, one per
    line, its own header calling it a snapshot of
    `select slug from initiatives.current`.

🔴 WHAT THE MEASUREMENT CHANGED, TWICE — read this before "simplifying" a rule.

  1. **The `.html` PROSE rule finds almost nothing in the occupant.** The forum
     fixture has **108** visible text runs — 102 of them the same 11-character
     placeholder, the rest 8/8/16/16/39/48 — and **not one clears
     `MIN_FREE_TEXT_CHARS`**. Its captured content was already replaced with
     placeholders. So the rule that SEES it is the ATTRIBUTE rule, and a gate
     built only on visible prose would have scanned clean over the one file that
     motivated it. (Its single `<text>` finding is a BLOCK aggregate — see 3.)
     The prose rule is still here — it is what catches the NEXT capture, one that
     has not been scrubbed — but it is not what makes `.html` worth gating.

  2. **The `.txt` FREE-TEXT rule is structurally blind to the occupant.**
     `is_free_text` requires whitespace, deliberately, and all 144 data lines of
     the slugs fixture are single tokens. 0 of 144 are findings. Dropping the
     whitespace requirement to reach it would fire on every `requirements.txt`
     and every id list in the repo. So `<vocabulary>` keys on the SHAPE OF THE
     FILE — many distinct RECORD lines — and `<line>` stays as the rule for
     the other arrival, prose pasted into a `.txt`.

  3. **Neither text rule could see the shape this module NAMES as its motive.**
     Measured against the first cut of both:

       * `.html`, keyed on the longest UNINTERRUPTED run: 1x148 chars fired,
         3x70 fired, but 3x55, 5x40 and 8x25 all scored **0** — prose fragmented
         below the threshold was invisible whatever the total volume, which is
         what a markup-rich post (links, `<br>`, `<em>`) actually looks like.
       * `.txt`, whose banner calls itself "the format a captured LIST arrives
         in: a roster, a vocabulary, an export of names": 40 lines of
         `Firstname Lastname` -> **0**, 40 `handle<TAB>email` rows -> **0**. Only
         bare single tokens fired. Every multi-token line broke the whitespace
         test AND sat under 60 chars, so a roster of NAMES walked clean through.

     So the `.html` prose unit is now the BLOCK (`BLOCK_ELEMENTS`, distinct runs
     re-joined) with `<prose-runs>` for the page of short standalone paragraphs
     the block rule still cannot reach, and the `.txt` vocabulary unit is now the
     RECORD LINE (`MAX_RECORD_TOKENS`) rather than the bare token.

HOW TO SATISFY IT — the same three options as the JSON sibling
--------------------------------------------------------------
Ask what the file is FOR. A frozen capture backing a report that is already
written: DELETE it. A fixture something still consumes: regenerate it with
SYNTHETIC content — a test needs the SHAPE, never a real person's words or a
real roster. Otherwise pin it below, with its count and its reason.

🔴 PIN, DO NOT BLESS. A pin means the gate SEES the file, so growth is a review.
It does not mean the content was assessed and cleared; where it was not, the
reason says so in those words.

🔴 THE STRUCTURAL GAP ALL FOUR GATES SHARE: THEY READ `git ls-files` ONLY
-------------------------------------------------------------------------
Every one of these scans enumerates TRACKED FILES AT HEAD. None reads history,
and `test_the_gate_is_blind_to_git_history` below DRIVES that limitation rather
than asserting it in prose, so it cannot rot into a comment nobody believes.

That blind spot is not hypothetical here. THREE genuine P-256 EC private keys —
a Linkerd trust anchor, its identity issuer, and a rotated CA — sit in this
repo's reachable history at `cmd/cluster/certs/{root,issuer,ca-new}.key`, added
by `eb5d197b` (2021-04-29) and `d487dadb` (2022-03-16), both ancestors of
`origin/main`. They are absent from HEAD, so all four gates scan clean over them,
and they went unnoticed for four to five years. The determination that nothing
trusts them, and that history is deliberately not being rewritten, is recorded in
`SECRETS.md` -> "Dead credentials in reachable history".

**If you extend one of these gates, extending it to history is a SEPARATE piece
of work** — the file enumeration, the allowlist keying and the cost model are all
different, and a scan over every blob in every commit is not a per-run gate. What
must not happen is a future reader inferring from a green run that the repo's
history is clean. It is not, and this docstring is the record of that.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from testlib import captured_text_scan as C  # noqa: E402
from testlib import public_ip_scan as P  # noqa: E402

_AUTHORED_UI = ("authored extension UI copy — this is SOURCE, the page the "
                "operator's own extension renders, not a captured document")
_RIG_FIXTURE = ("authored browser-bridge OOPIF test rig — hand-written marker "
                "text whose job is to be findable across frame boundaries")
_AUTHORED_NOTE = ("authored operator note — a hand-written manifest of what was "
                  "removed from ~/.claude/agents and why; not captured")
_FORUM_FIXTURE = (
    "🔴 PINNED, NOT BLESSED. A structure-faithful capture of a live forum "
    "thread; its own header comment says every URL, id, filename, username, "
    "thread title and caption was replaced, and that was re-MEASURED when this "
    "gate landed: 0 prose runs over the threshold, every hostname under "
    "*.example.test, every attribute value SYNTH*/ID*/sample-*/example-*. The "
    "person-metadata ATTRIBUTES remain because the router's tests resolve this "
    "structure. Whether to prune them is the operator's call.")
_FORUM_BLOCK = (
    "🔴 PINNED, NOT BLESSED. The BLOCK aggregate of the breadcrumb + thread-title "
    "area — 66 chars of synthetic placeholders (`Prefix A`, `Prefix B` and the "
    "`Subject Name | subject_alias | Subject Full Name` string the router tests "
    "assert on). No single run in this file clears the threshold; this is the "
    "block rule doing exactly what it was added for, on a region that on a REAL "
    "capture holds the thread title and its category names.")
_SLUGS_FIXTURE = (
    "🔴 PINNED, NOT BLESSED. A hand-refreshed snapshot of the live initiatives "
    "board, consumed as the routing vocabulary test_routing.py measures against "
    "— so it is FUNCTIONAL, not a leftover capture. Whether to hash or truncate "
    "the slugs is the operator's call; this pin exists so the gate SEES it "
    "instead of scanning clean over it.")

# --- THE PINNED (PATH, SIGNAL) -> (COUNT, WHY) ALLOWLIST ------------------------
# 🔴 Keyed on (PATH, SIGNAL) and pinned to a COUNT, exactly like the JSON
# sibling and for the same reason: the VALUE cannot appear here, because the
# value is the disclosure. The count is what stops a pin from pre-approving
# whatever lands at that signal next.
#
# Accounting runs in BOTH directions:
#   * a hit matching no entry            -> FAIL (something new landed);
#   * an entry matching no hit           -> FAIL (the file went away; drop the pin);
#   * a count that moved EITHER WAY      -> FAIL (grew under a live pin, or the
#     pin was written oversized and would have pre-approved growth).
ALLOWLIST: dict[tuple[str, str], tuple[int, str]] = {
    ("claudedocs/superclaude-agents-removed-2026-08-10/MANIFEST.txt",
     C.SIGNAL_TXT_LINE): (5, _AUTHORED_NOTE),

    ("scripts/browser-bridge/extension/options.html",
     C.SIGNAL_HTML_TEXT): (3, _AUTHORED_UI),
    ("scripts/dl-router/extension/options.html",
     C.SIGNAL_HTML_TEXT): (3, _AUTHORED_UI),
    ("scripts/dl-router/extension/picker.html",
     C.SIGNAL_HTML_TEXT): (1, _AUTHORED_UI),

    ("scripts/browser-bridge/tests/fixtures/oopif-rig/leaf.html",
     C.SIGNAL_HTML_TEXT): (1, _RIG_FIXTURE),
    ("scripts/browser-bridge/tests/fixtures/oopif-rig/mid.html",
     C.SIGNAL_HTML_TEXT): (1, _RIG_FIXTURE),

    ("scripts/dl-router/tests/fixtures/forum-thread-page.html",
     C.SIGNAL_HTML_TEXT): (1, _FORUM_BLOCK),
    ("scripts/dl-router/tests/fixtures/forum-thread-page.html",
     "@person:data-user-id"): (8, _FORUM_FIXTURE),
    ("scripts/dl-router/tests/fixtures/forum-thread-page.html",
     "@person:data-author"): (4, _FORUM_FIXTURE),
    ("scripts/dl-router/tests/fixtures/forum-thread-page.html",
     "@itemprop:name"): (10, _FORUM_FIXTURE),
    ("scripts/dl-router/tests/fixtures/forum-thread-page.html",
     "@itemprop:author"): (4, _FORUM_FIXTURE),

    ("scripts/repo-cos/tests/fixtures/initiatives_current_slugs.txt",
     C.SIGNAL_TXT_VOCABULARY): (144, _SLUGS_FIXTURE),
}

#: A 70-character sentence — clears `MIN_FREE_TEXT_CHARS` with room to spare, so
#: it is usable wherever a fixture needs "this is prose" and nothing else.
_PROSE = "the follower has been lagging behind the primary since the last deploy"


def _hits():
    return C.scan_markup_repo(REPO)


def _unpinned(hits):
    out = []
    for relpath, signal, count, max_len in hits:
        pin = ALLOWLIST.get((relpath, signal))
        if pin is None or pin[0] != count:
            out.append((relpath, signal, count, max_len))
    return out


def _fmt(hits):
    return "\n  ".join(
        f"{p}: {s}  ({n} occurrences, longest {m} chars)" for p, s, n, m in hits)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- the scan ------------------------------------------------------------------

def test_no_captured_markup_is_committed():
    unpinned = _unpinned(_hits())
    assert not unpinned, (
        "CAPTURED CONTENT is committed to a PUBLIC repo in an .html or .txt — a "
        "scraped page's person metadata or prose, or a captured list of "
        "identifiers. If the capture backs a report that is already written, "
        "DELETE it; if something still consumes the fixture, regenerate it with "
        "SYNTHETIC content. Counts and enumerated signals only below — this gate "
        "never prints the content:\n  " + _fmt(unpinned))


def test_every_allowlist_entry_still_matches_something():
    """Stale-pin accounting — the complement of the scan above.

    Every entry names a live (path, signal) in this tree, so deleting or renaming
    any of those files fails here instead of leaving a dead pin that silently
    pre-approves whatever lands at that path next.
    """
    seen = {(p, s) for p, s, _n, _m in _hits()}
    stale = [f"{p}: {s} ({why})" for (p, s), (_n, why) in ALLOWLIST.items()
             if (p, s) not in seen]
    assert not stale, (
        "ALLOWLIST entries match nothing — the file went away or the signal "
        "moved. Delete or repoint the pin:\n  " + "\n  ".join(stale))


def test_every_allowlist_entry_carries_a_reason():
    """`claude/RULES.md`: an allowlist is an enumeration with each entry's reason."""
    for (p, s), (n, why) in ALLOWLIST.items():
        assert isinstance(n, int) and n > 0, f"{p}:{s} has a non-count pin"
        assert why and len(why) > 30, f"{p}:{s} has no reason"


def test_a_GROWN_file_under_a_live_pin_still_fails():
    """The count half of the allowlist, DRIVEN rather than asserted about.

    🔴 BOTH DIRECTIONS. An oversized pin is the same rubber stamp written in
    advance: pin 9 against a real 5 pre-approves the next four silently.
    """
    pin = ("docs/page.html", "@person:data-user-id")
    hits = [("docs/page.html", "@person:data-user-id", 5, 7)]
    saved = dict(ALLOWLIST)
    try:
        ALLOWLIST.clear()
        ALLOWLIST[pin] = (3, "control")
        assert _unpinned(hits) == hits, "a GROWN file passed under its pin"
        ALLOWLIST[pin] = (9, "control")
        assert _unpinned(hits) == hits, "an OVERSIZED pin exempted a smaller hit"
        ALLOWLIST[pin] = (5, "control")
        assert _unpinned(hits) == [], "an exact-count pin did not exempt"
    finally:
        ALLOWLIST.clear()
        ALLOWLIST.update(saved)


# --- NEGATIVE CONTROLS: the gate MUST go red on the real shape ------------------
# 🔴 Built from REALISTIC data of the real shape, not a textbook fixture.
# `claude/RULES.md`: a scanner validated against its own canonical example proves
# nothing, because scanners allowlist canonical examples. Each of these
# reproduces the ACTUAL structure of a capture this repo could plausibly grow,
# with invented content.

def _realistic_forum_capture() -> str:
    """A XenForo-shaped thread page: post articles, author microdata, bodies.

    Invented users and invented post text, but the markup is the real dialect —
    the same `data-user-id` / `itemprop` / `<article>` nesting a live forum
    emits, which is what makes this a control rather than a demo.
    """
    posts = []
    for i, (uid, handle, body) in enumerate((
        ("884210", "riverbend_owl",
         "I finally got the second stage tuned last night and the numbers held "
         "steady across the whole pull, which is more than I can say for the "
         "old setup."),
        ("331907", "kestrel.mk4",
         "Did you end up swapping the intercooler as well, or is that still the "
         "original one from the build thread you posted back in the spring?"),
        ("905544", "delta_wing",
         "Following this closely because I am about to do the same job and I "
         "would rather learn from someone else's afternoon than my own."),
    )):
        posts.append(f"""
    <article class="message message--post js-post"
             data-author="{handle}" data-content="post-{9000 + i}">
      <section class="message-user" itemprop="author" itemscope
               itemtype="https://schema.org/Person">
        <a href="https://forums.example.invalid/members/{handle}.{uid}/"
           class="avatar avatar--m" data-user-id="{uid}">
          <img src="https://cdn.example.invalid/avatars/m/{uid}.jpg" alt="{handle}">
        </a>
        <h4 class="message-name">
          <span class="username" itemprop="name">{handle}</span>
        </h4>
      </section>
      <div class="message-content js-messageContent">
        <div class="bbWrapper">{body}</div>
      </div>
    </article>""")
    return f"""<!DOCTYPE html>
<html lang="en"><head>
  <title>Second stage tune results | Page 4 | Example Forums</title>
  <meta property="og:description"
        content="I finally got the second stage tuned last night and the numbers
                 held steady across the whole pull.">
</head><body>
  <div class="p-body-main">{"".join(posts)}</div>
</body></html>"""


def test_negative_control_a_realistic_forum_capture_goes_RED(tmp_path):
    """🔴 The whole point. If this returns nothing, the gate is wired to nothing."""
    _write(tmp_path / "scripts" / "somesub" / "tests" / "fixtures" / "thread.html",
           _realistic_forum_capture())
    hits = C.scan_markup_repo(tmp_path)
    assert hits, "the .html scan is wired to nothing"
    got = {sig: n for _p, sig, n, _m in hits}
    # The person metadata: three posters, each marked up three different ways.
    assert got.get("@person:data-user-id") == 3, got
    assert got.get("@person:data-author") == 3, got
    assert got.get("@itemprop:name") == 3, got
    assert got.get("@itemprop:author") == 3, got
    # ...and the post BODIES, which is the half a metadata-only rule would miss.
    assert got.get(C.SIGNAL_HTML_TEXT) == 3, got
    # ...and the og:description, which no text-node walk can see.
    assert got.get("@text:content") == 1, got
    assert {p for p, _s, _n, _m in hits} == {
        "scripts/somesub/tests/fixtures/thread.html"}


def test_negative_control_the_prose_rule_alone_goes_RED_on_a_scraped_article(tmp_path):
    """A captured page with NO person metadata at all — a news/blog scrape.

    Separated from the forum control on purpose: without it, deleting the whole
    prose rule would still leave that control green on its attribute assertions,
    and the prose half would be untested.

    🔴 EVERY PARAGRAPH CARRIES INLINE MARKUP, and that is load-bearing. The first
    cut of this control used tag-free paragraphs, so it went red only because
    each paragraph happened to survive as ONE run over the threshold: adding a
    single `<a>` per paragraph took it to 0 findings. A control that a captured
    page defeats by being ordinary HTML is not a control.
    """
    paras = "".join(
        f"<p>{s}</p>" for s in (
            "The council confirmed on <a href='/t/12'>Tuesday</a> that the "
            "<em>crossing</em> will stay shut until the replacement barriers "
            "arrive<br>some time in the <a href='/t/13'>new year</a>.",
            "Residents on the eastern side have been asked to use the "
            "<a href='/t/14'>footbridge</a>, which adds <em>roughly ten "
            "minutes</em> to the walk into the <a href='/t/15'>town centre</a>.",
        ))
    _write(tmp_path / "capture.html",
           f"<html><body><main>{paras}</main></body></html>")
    hits = C.scan_markup_repo(tmp_path)
    assert [(h[1], h[2]) for h in hits] == [(C.SIGNAL_HTML_TEXT, 2)], hits
    # ...and no individual RUN clears the threshold, so the old rule scored 0.
    runs = C.visible_text_runs(
        (tmp_path / "capture.html").read_text(encoding="utf-8"))
    assert not any(C.is_free_text(r) for r in runs), (
        f"a run cleared {C.MIN_FREE_TEXT_CHARS} on its own — this control is "
        f"back to proving the OLD rule: {sorted(len(r) for r in runs)}")


def test_negative_control_a_DISCOURSE_capture_has_a_person_signal(tmp_path):
    """🔴 The second forum dialect, which the XenForo-shaped table could not see.

    MEASURED before `data-user-card` was enumerated: a complete Discourse-dialect
    capture produced **no person signal at all**. Discourse marks its posters
    with `data-user-card="<handle>"` on every avatar and byline and uses none of
    the `data-user-id`/`data-author` spellings. Asserted on the PERSON signal
    specifically — a prose finding from the post bodies would otherwise let this
    pass with the dialect still invisible.
    """
    posts = "".join(f'''
    <article class="topic-post"><div class="topic-avatar">
      <a class="trigger-user-card" data-user-card="poster_{i}"></a></div>
      <div class="topic-body"><div class="names"><span class="first">
      <a class="trigger-user-card" data-user-card="poster_{i}">Poster {i}</a>
      </span></div><div class="cooked"><p>short note {i}</p></div>
    </div></article>''' for i in range(3))
    _write(tmp_path / "t.html", f'<html><body><div id="main-outlet">{posts}'
                                "</div></body></html>")
    got = {s: n for _p, s, n, _m in C.scan_markup_repo(tmp_path)}
    assert got.get("@person:data-user-card") == 6, got


def test_negative_control_a_REDDIT_capture_has_a_person_signal(tmp_path):
    """The third dialect. Reddit carries the author's account fullname in
    `data-author-fullname` (`t2_…`) and the thing's own in `data-fullname`."""
    things = "".join(
        f'<div class="thing" data-fullname="t3_ab{i}" '
        f'data-author-fullname="t2_zz{i}"><p>short note {i}</p></div>'
        for i in range(3))
    _write(tmp_path / "r.html", f"<html><body>{things}</body></html>")
    got = {s: n for _p, s, n, _m in C.scan_markup_repo(tmp_path)}
    assert got.get("@person:data-author-fullname") == 3, got
    assert got.get("@person:data-fullname") == 3, got


def test_negative_control_a_captured_roster_txt_goes_RED(tmp_path):
    """🔴 The `.txt` shape the free-text rule structurally cannot see.

    A roster/vocabulary export: one bare identifier per line. Deliberately 40
    entries, NOT `MIN_VOCABULARY_LINES` — a fixture whose size equals the
    constant it tests cannot see a mutant that hardcodes the literal.
    """
    assert 40 != C.MIN_VOCABULARY_LINES, "this fixture must not equal the floor"
    names = "\n".join(f"member-{i:03d}-handle" for i in range(40))
    _write(tmp_path / "roster.txt", f"# exported {40} entries\n{names}\n")
    hits = C.scan_markup_repo(tmp_path)
    assert [(h[1], h[2]) for h in hits] == [(C.SIGNAL_TXT_VOCABULARY, 40)], hits


def test_negative_control_prose_pasted_into_a_txt_goes_RED(tmp_path):
    """The other `.txt` arrival: a transcript or message dump saved as plain text."""
    lines = "\n".join((
        "we pushed the migration at about four and the replica caught up fine",
        "the second batch is still running, I will check again after standup",
    ))
    _write(tmp_path / "notes.txt", lines + "\n")
    hits = C.scan_markup_repo(tmp_path)
    assert [(h[1], h[2]) for h in hits] == [(C.SIGNAL_TXT_LINE, 2)], hits


def test_negative_control_the_gates_ASSERTION_fails_not_just_the_scan():
    """The scan FINDING something and the gate REPORTING it are different things.

    An over-broad allowlist, or a `_unpinned` that swallowed everything, would
    leave the scan correct and the gate inert.
    """
    fabricated = [("scripts/x/page.html", "@person:data-user-id", 7, 9)]
    assert _unpinned(fabricated) == fabricated
    assert "7 occurrences" in _fmt(fabricated)


# --- POSITIVE CONTROLS: the gate must stay GREEN on legitimate content ----------
# 🔴 A gate that fires on everything gets disabled, and `claude/RULES.md` is
# explicit that a permanently-red gate is worse than none.

def test_positive_control_the_repo_scans_clean_today():
    """The zero this gate reports must be a zero from a scan that WALKED.

    Reported as a PAIR with the negative controls above, never alone: those prove
    the scan can return non-zero on the real shapes, this one proves the real
    tree is accounted for. The floor answers "did it walk anything at all", NOT
    "is the census right" — it sits well below the 23 candidates measured here,
    because a floor set one or two below the count reds the gate the day someone
    deletes a fixture.
    """
    files = C.markup_candidates(REPO)
    assert len(files) >= 12, (
        f"only {len(files)} .html/.txt candidates under REPO={REPO} — the scan "
        "walked (almost) nothing and its zero means nothing")
    assert _unpinned(_hits()) == []


def test_positive_control_a_SHORT_id_list_is_not_a_vocabulary(tmp_path):
    """A short id list must stay quiet — the SIZE floor, not the name exemption.

    MEASURED: `scripts/signal/requirements.txt` has 4 data lines after comment
    stripping. Reaching the slugs fixture by lowering the floor to catch this too
    is the permanently-red gate; the floor is the trade, and this pins it. Named
    `deps.txt` on purpose, so `DEPENDENCY_MANIFESTS` cannot be what makes it
    pass — otherwise this test would go green with the floor deleted.
    """
    _write(tmp_path / "deps.txt",
           "# runtime deps\npsycopg2-binary  # the db client\nminio\nrequests\n"
           "websocket-client\n")
    assert C.scan_markup_repo(tmp_path) == []


def test_a_dependency_manifest_is_exempt_and_the_SAME_FILE_renamed_is_not(tmp_path):
    """🔴 BOTH HALVES of the `DEPENDENCY_MANIFESTS` exemption, same bytes.

    A 30-package `requirements.txt` has the shape of a roster exactly — MEASURED
    at `<vocabulary> 30` before the exemption existed — and reviewing a pin every
    time a dependency is added is how a gate gets switched off. The cost is a
    real bypass, so the rename half proves the exemption is keyed on the NAME and
    nothing else, and the blind-spot list names it in those words.
    """
    body = "\n".join(f"package-name-{i:02d}==1.0.{i}" for i in range(30)) + "\n"
    assert 30 > C.MIN_VOCABULARY_LINES, "the fixture must clear the size floor"
    for name in sorted(C.DEPENDENCY_MANIFESTS):
        _write(tmp_path / name.replace(".txt", "") / name, body)
        assert C.scan_markup_repo(tmp_path / name.replace(".txt", "")) == [], name
    _write(tmp_path / "renamed" / "handles.txt", body)
    assert [(h[1], h[2]) for h in C.scan_markup_repo(tmp_path / "renamed")] == [
        (C.SIGNAL_TXT_VOCABULARY, 30)]


def test_positive_control_authored_ui_markup_without_prose_is_quiet(tmp_path):
    """An extension page whose copy is all short labels, plus a stylesheet `rel`.

    `rel` is a member of `PERSON_ATTRS` only for `author`; `rel="stylesheet"` is
    on nearly every page in this tree and an unconditional entry would red the
    gate forever. Both halves are driven — the scoping is worthless without the
    negative one below it.
    """
    _write(tmp_path / "options.html", """<!DOCTYPE html>
<html><head><link rel="stylesheet" href="options.css">
<title>Options</title></head>
<body><h1>Options</h1><label>Port</label><input placeholder="8765">
<button id="save">Save</button></body></html>""")
    assert C.scan_markup_repo(tmp_path) == []


def test_a_rel_author_link_IS_a_finding(tmp_path):
    """The other half of the `rel` scoping: the value that names a person."""
    _write(tmp_path / "post.html",
           '<html><body><a rel="author" href="/u/someone">by</a></body></html>')
    assert [(h[1], h[2]) for h in C.scan_markup_repo(tmp_path)] == [
        ("@person:rel", 1)], C.scan_markup_repo(tmp_path)


def test_positive_control_script_and_style_bodies_are_not_prose(tmp_path):
    """Minified JS and CSS are long, whitespace-bearing and not captured text.

    Without the skip, every extension page and every inlined stylesheet in the
    repo is a finding — and the gate is off within a week.
    """
    _write(tmp_path / "app.html", """<html><head>
<style>.row { display: flex; gap: 6px; align-items: center; font: 500 12px sans-serif; }</style>
<script>var a = 1; function go(x) { return x + 1; } /* a long enough comment to clear the threshold */</script>
</head><body><p>ok</p></body></html>""")
    assert C.scan_markup_repo(tmp_path) == []


def test_a_template_element_is_NOT_treated_as_code(tmp_path):
    """🔴 The skip list is `script`/`style` ONLY, and that is load-bearing.

    `<template>` holds ordinary inert markup. Skipping it would hand an author a
    one-tag way to hide a whole captured page from this gate, so the narrowness
    of `_NON_PROSE_ELEMENTS` is pinned here rather than left to a reader.
    """
    _write(tmp_path / "t.html",
           f"<html><body><template><p>{_PROSE}</p>"
           '<span data-user-id="4471">x</span></template></body></html>')
    got = {s: n for _p, s, n, _m in C.scan_markup_repo(tmp_path)}
    assert got == {C.SIGNAL_HTML_TEXT: 1, "@person:data-user-id": 1}, got


def test_positive_control_a_long_non_prose_run_is_not_a_finding(tmp_path):
    """Whitespace is the discriminator, shared with the JSON half.

    Without it this gate fires on every base64 data URI and every long path that
    happens to sit in a text node.
    """
    blob = "d" * 300
    _write(tmp_path / "d.html", f"<html><body><code>{blob}</code></body></html>")
    assert C.scan_markup_repo(tmp_path) == []


def test_positive_control_an_empty_person_attribute_is_not_a_finding(tmp_path):
    """A template placeholder (`data-user-id=""`) carries nobody."""
    _write(tmp_path / "d.html",
           '<html><body><div data-user-id="" data-author="">x</div></body></html>')
    assert C.scan_markup_repo(tmp_path) == []


# --- the thresholds, driven at the boundary in BOTH directions ------------------

def test_the_html_prose_rule_uses_the_SHARED_free_text_threshold(tmp_path):
    """🔴 Not a second copy of 60. Driven at exactly-N and N-1, computed FROM the
    constant, so a markup path that grew its own hardcoded literal fails here.
    """
    th = C.MIN_FREE_TEXT_CHARS
    at = "ab " + "c" * (th - 3)
    below = at[:-1]
    assert len(at) == th and len(below) == th - 1
    _write(tmp_path / "at.html", f"<html><body><p>{at}</p></body></html>")
    _write(tmp_path / "below.html", f"<html><body><p>{below}</p></body></html>")
    assert [(h[0], h[1], h[3]) for h in C.scan_markup_repo(tmp_path)] == [
        ("at.html", C.SIGNAL_HTML_TEXT, th)]


# --- the BLOCK rule: what the longest-run rule could not see --------------------

def _fragmented(paragraph_chars: int, paragraphs: int) -> str:
    """`paragraphs` <p> blocks of ~`paragraph_chars` of prose, every one of them
    split by inline markup into runs that are individually SHORT.

    Built from DISTINCT sentences, not one repeated: the block rule deduplicates
    identical runs, so a fixture of copies would test the dedupe instead of the
    aggregation and could pass for the wrong reason.
    """
    out = []
    for i in range(paragraphs):
        words = [f"w{i}x{j:02d}" for j in range(paragraph_chars // 7)]
        parts, k = [], 0
        while k < len(words):
            chunk = " ".join(words[k:k + 3])
            parts.append(chunk if (k // 3) % 2 else
                         f'<a href="/t/{i}{k}">{chunk}</a>')
            k += 3
        out.append("<p>" + " ".join(parts) + "</p>")
    return "<html><body><main>" + "".join(out) + "</main></body></html>"


def test_the_prose_rule_keys_on_the_BLOCK_not_the_longest_RUN(tmp_path):
    """🔴 THE HOLE THIS RULE EXISTS FOR, driven with the numbers that found it.

    MEASURED against the first cut, which applied `is_free_text` to each TEXT
    NODE: 1x148 chars fired and 3x70 fired, but 3x55, 5x40 and 8x25 all scored 0.
    Prose fragmented below the threshold was invisible whatever the total volume,
    and that is what a markup-rich post looks like.

    Here every run is UNDER the threshold and every block is OVER it, so this
    test can only pass if the aggregation is what is being measured — the
    run-level assertion below is not decoration, it is what makes that true.
    """
    markup = _fragmented(paragraph_chars=105, paragraphs=3)
    _write(tmp_path / "post.html", markup)
    runs = C.visible_text_runs(markup)
    assert runs and not any(C.is_free_text(r) for r in runs), (
        f"a RUN cleared {C.MIN_FREE_TEXT_CHARS} — the old rule would fire and "
        f"this test would prove nothing: {sorted(len(r) for r in runs)}")
    blocks = C.visible_text_blocks(markup)
    assert sum(1 for b in blocks if C.is_free_text(b)) == 3, blocks
    assert [(h[1], h[2]) for h in C.scan_markup_repo(tmp_path)] == [
        (C.SIGNAL_HTML_TEXT, 3)]


def test_a_block_of_ONE_REPEATED_LABEL_is_not_prose(tmp_path):
    """🔴 THE DEDUPE, and why re-joining alone was not shippable.

    MEASURED on this tree without it: `forum-thread-page.html` scored 5 and
    `embed-player-frame.html` 1, every one of them the placeholder `Sample text`
    repeated 5-7 times inside one container. Re-joining exists to reassemble a
    sentence split by inline markup, and a split sentence yields DISTINCT
    fragments; a container holding one short label N times is chrome.

    Both halves: the SAME number of runs of the SAME total length, distinct
    versus repeated, so only the dedupe can move the verdict.
    """
    label = "Sample text"
    n = (C.MIN_FREE_TEXT_CHARS // (len(label) + 1)) + 2
    repeated = "".join(f"<strong>{label}</strong>" for _ in range(n))
    distinct = "".join(f"<strong>{label[:-1]}{i}</strong>" for i in range(n))
    _write(tmp_path / "same" / "d.html", f"<html><body><div>{repeated}</div></body></html>")
    _write(tmp_path / "diff" / "d.html", f"<html><body><div>{distinct}</div></body></html>")
    assert C.scan_markup_repo(tmp_path / "same") == []
    assert [(h[1], h[2]) for h in C.scan_markup_repo(tmp_path / "diff")] == [
        (C.SIGNAL_HTML_TEXT, 1)]


def test_an_INLINE_element_does_not_end_a_block_and_a_BLOCK_one_does(tmp_path):
    """🔴 The membership of `BLOCK_ELEMENTS` is the whole rule, driven both ways.

    The same prose, the same runs, differing ONLY in whether the separator is an
    inline element or a block one. Inline -> one aggregate over the threshold;
    block -> two paragraphs under it. An inline tag added to `BLOCK_ELEMENTS`
    turns the first case green and defeats the rule, which the ledger catches as
    a set change and this catches as behaviour.
    """
    a = "the follower has been lagging behind the"
    b = "primary since the last deploy went out"
    assert len(a) < C.MIN_FREE_TEXT_CHARS and len(b) < C.MIN_FREE_TEXT_CHARS
    assert len(f"{a} {b}") >= C.MIN_FREE_TEXT_CHARS
    _write(tmp_path / "inline" / "d.html",
           f"<html><body><p>{a} <em>{b}</em></p></body></html>")
    _write(tmp_path / "block" / "d.html",
           f"<html><body><p>{a}</p><p>{b}</p></body></html>")
    assert [(h[1], h[2]) for h in C.scan_markup_repo(tmp_path / "inline")] == [
        (C.SIGNAL_HTML_TEXT, 1)]
    assert C.scan_markup_repo(tmp_path / "block") == []


def test_text_with_NO_block_wrapper_is_still_aggregated(tmp_path):
    """`body` is in `BLOCK_ELEMENTS` so stray inline text is not lost.

    A capture saved as a wall of `<span>`s has no paragraph element anywhere; if
    the aggregator only counted explicit blocks it would score 0 on it.
    """
    parts = "".join(f"<span>fragment number {i:02d}</span> " for i in range(4))
    _write(tmp_path / "d.html", f"<html><body>{parts}</body></html>")
    assert [(h[1], h[2]) for h in C.scan_markup_repo(tmp_path)] == [
        (C.SIGNAL_HTML_TEXT, 1)]


# --- the SECOND prose rule: the page of short standalone paragraphs -------------

def _short_paragraphs(n: int) -> str:
    """`n` standalone paragraphs, each its own block and each UNDER the block
    threshold — the shape a chat or message export has."""
    return "<html><body>" + "".join(
        f"<p>message {i:02d} landed on the queue</p>" for i in range(n)
    ) + "</body></html>"


def test_the_prose_RUN_COUNT_floor_is_driven_at_the_boundary(tmp_path):
    """Exactly `MIN_PROSE_RUNS` distinct wordy runs fires; one fewer does not.

    🔴 The block rule cannot see this shape at all — each paragraph is its own
    block and each is under `MIN_FREE_TEXT_CHARS` — so the assertion below that
    NO block clears the threshold is what proves this rule, and not the other
    one, is what moved. Both sides computed from the constant.
    """
    n = C.MIN_PROSE_RUNS
    for label, count in (("at", n), ("below", n - 1)):
        markup = _short_paragraphs(count)
        _write(tmp_path / label / "chat.html", markup)
        assert not any(C.is_free_text(b) for b in C.visible_text_blocks(markup)), (
            "a block cleared the free-text threshold — the block rule is what "
            "would be firing, and this test would prove nothing")
    assert [(h[1], h[2]) for h in C.scan_markup_repo(tmp_path / "at")] == [
        (C.SIGNAL_HTML_PROSE_RUNS, n)]
    assert C.scan_markup_repo(tmp_path / "below") == []


def test_the_run_count_rule_uses_the_SHARED_chat_threshold(tmp_path):
    """🔴 Not a third copy of 25. Driven at exactly-N and N-1 chars per run,
    computed FROM `MIN_CHAT_TEXT_CHARS`, with the run COUNT held constant well
    above `MIN_PROSE_RUNS` so only the per-run length can decide.
    """
    th = C.MIN_CHAT_TEXT_CHARS
    n = C.MIN_PROSE_RUNS + 2
    for label, ln in (("at", th), ("below", th - 1)):
        paras = "".join(f"<p>{('m%02d ' % i) + 'w' * (ln - 4)}</p>"
                        for i in range(n))
        _write(tmp_path / label / "chat.html", f"<html><body>{paras}</body></html>")
    assert [(h[1], h[2]) for h in C.scan_markup_repo(tmp_path / "at")] == [
        (C.SIGNAL_HTML_PROSE_RUNS, n)]
    assert C.scan_markup_repo(tmp_path / "below") == []


def test_the_run_count_is_DISTINCT_runs_not_total(tmp_path):
    """A template emitting one wordy label 200 times is one piece of copy.

    Pinned because counting raw runs would fire on every repeated row in every
    authored table in the tree.
    """
    label = "queued for review by the operator"
    assert len(label) >= C.MIN_CHAT_TEXT_CHARS
    body = "".join(f"<p>{label}</p>" for _ in range(C.MIN_PROSE_RUNS * 20))
    _write(tmp_path / "d.html", f"<html><body>{body}</body></html>")
    assert C.scan_markup_repo(tmp_path) == []


def test_the_txt_line_rule_uses_the_SHARED_free_text_threshold(tmp_path):
    """The same pin for the `.txt` half, for the same reason."""
    th = C.MIN_FREE_TEXT_CHARS
    at = "ab " + "c" * (th - 3)
    _write(tmp_path / "at.txt", at + "\n")
    _write(tmp_path / "below.txt", at[:-1] + "\n")
    assert [(h[0], h[1], h[3]) for h in C.scan_markup_repo(tmp_path)] == [
        ("at.txt", C.SIGNAL_TXT_LINE, th)]


# --- the `.txt` shape the module NAMES as its motive: a roster of NAMES ---------

def test_negative_control_a_roster_of_NAMES_goes_RED(tmp_path):
    """🔴 THE STATED MOTIVATING CASE, which the first cut could not see.

    The banner calls `.txt` "the format a captured LIST arrives in: a roster, a
    vocabulary, an export of names". MEASURED against that cut: **40 lines of
    `Firstname Lastname` -> 0 findings**. Every multi-token line broke the
    whitespace test (killing `<vocabulary>`) and sat under 60 chars (killing
    `<line>`), so an export of names — the shape the comment advertises — walked
    clean through. 40 entries, deliberately not `MIN_VOCABULARY_LINES`.
    """
    assert 40 != C.MIN_VOCABULARY_LINES, "this fixture must not equal the floor"
    names = "\n".join(f"Given{i:02d} Family{i:02d}" for i in range(40))
    _write(tmp_path / "roster.txt", f"# exported {40} entries\n{names}\n")
    hits = C.scan_markup_repo(tmp_path)
    assert [(h[1], h[2]) for h in hits] == [(C.SIGNAL_TXT_VOCABULARY, 40)], hits


def test_negative_control_a_TAB_separated_export_goes_RED(tmp_path):
    """The other measured miss: 40 `handle<TAB>email` rows -> 0 findings.

    A two-column export is the commonest way a roster leaves an admin panel, and
    the tab is whitespace, which is exactly what made it invisible.
    """
    rows = "\n".join(f"handle{i:03d}\tuser{i:03d}@mail.example.invalid"
                     for i in range(40))
    _write(tmp_path / "export.txt", rows + "\n")
    assert [(h[1], h[2]) for h in C.scan_markup_repo(tmp_path)] == [
        (C.SIGNAL_TXT_VOCABULARY, 40)]


def test_the_RECORD_WIDTH_floor_is_driven_at_the_boundary(tmp_path):
    """Exactly `MAX_RECORD_TOKENS` fields is a record; one more is not.

    Both sides computed from the constant, with every other input held equal —
    the same line count, well above the size floor, and every line short enough
    that `is_free_text` is not what decides.
    """
    n = C.MAX_RECORD_TOKENS
    rows = C.MIN_VOCABULARY_LINES + 5
    for label, width in (("at", n), ("over", n + 1)):
        lines = "\n".join(" ".join(f"f{j}v{i:02d}" for j in range(width))
                          for i in range(rows))
        _write(tmp_path / label / "r.txt", lines + "\n")
        assert not any(C.is_free_text(ln) for ln in lines.splitlines()), (
            "a line is free text — `<line>` would be what fires, not the width")
    assert [(h[1], h[2]) for h in C.scan_markup_repo(tmp_path / "at")] == [
        (C.SIGNAL_TXT_VOCABULARY, rows)]
    assert C.scan_markup_repo(tmp_path / "over") == []


def test_a_record_line_is_never_ALSO_prose(tmp_path):
    """🔴 The two `.txt` rules must not collapse into one.

    `_is_record_line` applies `is_free_text` FIRST, so a file of long three-word
    sentences is `<line>`, never `<vocabulary>` — otherwise widening the record
    width would have quietly swallowed the prose rule's occupant.
    """
    rows = C.MIN_VOCABULARY_LINES + 5
    lines = "\n".join(
        "aaaaaaaaaaaaaaaaaaaa bbbbbbbbbbbbbbbbbbbb cccccccccccccccccc%02d" % i
        for i in range(rows))
    assert all(C.is_free_text(ln) for ln in lines.splitlines())
    assert all(len(ln.split()) <= C.MAX_RECORD_TOKENS
               for ln in lines.splitlines()), "the width is not what excludes it"
    _write(tmp_path / "p.txt", lines + "\n")
    assert [h[1] for h in C.scan_markup_repo(tmp_path)] == [C.SIGNAL_TXT_LINE]


def test_the_vocabulary_SIZE_floor_is_driven_at_the_boundary(tmp_path):
    """Exactly `MIN_VOCABULARY_LINES` fires; one fewer does not.

    Both sides, computed from the constant — the only two sizes that can see a
    `>=` mutated to `>`, or the floor moved.
    """
    n = C.MIN_VOCABULARY_LINES
    _write(tmp_path / "at" / "v.txt",
           "\n".join(f"slug-{i:03d}" for i in range(n)) + "\n")
    _write(tmp_path / "below" / "v.txt",
           "\n".join(f"slug-{i:03d}" for i in range(n - 1)) + "\n")
    assert [(h[1], h[2]) for h in C.scan_markup_repo(tmp_path / "at")] == [
        (C.SIGNAL_TXT_VOCABULARY, n)]
    assert C.scan_markup_repo(tmp_path / "below") == []


def test_the_vocabulary_PURITY_floor_is_driven_at_the_boundary(tmp_path):
    """Exactly `MIN_VOCABULARY_PURITY` fires; just under does not.

    🔴 The size floor is held CONSTANT and above its own threshold in both
    halves, so this test can only be moved by the purity constant. Built at 30
    data lines — a number that is neither floor — with the single-token count
    computed from `MIN_VOCABULARY_PURITY` rather than written as a literal.
    """
    total = 30
    assert total != C.MIN_VOCABULARY_LINES
    at = round(total * C.MIN_VOCABULARY_PURITY)          # 27 -> purity 0.9
    assert at >= C.MIN_VOCABULARY_LINES, "the size floor must not be what decides"
    prose = ("a sentence with spaces in it that is nobody's identifier at all")
    for label, singles in (("at", at), ("below", at - 1)):
        lines = [f"slug-{i:03d}" for i in range(singles)]
        lines += [f"{prose} {i}" for i in range(total - singles)]
        _write(tmp_path / label / "v.txt", "\n".join(lines) + "\n")
    # The padding lines are prose, so `<line>` fires in BOTH halves — the two
    # rules are independent by design. Only the vocabulary verdict may move.
    at_hits = {h[1]: h[2] for h in C.scan_markup_repo(tmp_path / "at")}
    below_hits = {h[1]: h[2] for h in C.scan_markup_repo(tmp_path / "below")}
    assert at_hits.get(C.SIGNAL_TXT_VOCABULARY) == at, at_hits
    assert C.SIGNAL_TXT_VOCABULARY not in below_hits, below_hits
    assert C.SIGNAL_TXT_LINE in at_hits and C.SIGNAL_TXT_LINE in below_hits, (
        "the padding must be prose in both halves, or purity is not what moved")


def test_the_vocabulary_count_is_DISTINCT_lines_not_total(tmp_path):
    """A file repeating one slug 200 times is not a 200-entry roster.

    Pinned because counting raw lines would let a pin be satisfied by padding.
    """
    n = C.MIN_VOCABULARY_LINES
    body = "\n".join(f"slug-{i:03d}" for i in range(n)) + "\n"
    _write(tmp_path / "v.txt", body + body)          # every entry twice
    assert [(h[1], h[2]) for h in C.scan_markup_repo(tmp_path)] == [
        (C.SIGNAL_TXT_VOCABULARY, n)]


def test_a_txt_comment_is_not_scanned_and_the_blindness_is_pinned(tmp_path):
    """Both halves of the comment convention, so it is a decision not a slip.

    A `#` header is authored by construction — every `.txt` in this tree opens
    with one — and treating it as data would red the gate on its own
    documentation. The cost is that captured text pasted AFTER a `#` is
    invisible, which the scan module's blind-spot list names and this pins.
    """
    _write(tmp_path / "a.txt", f"# {_PROSE}\n")
    _write(tmp_path / "b.txt", f"slug-a  # {_PROSE}\n")
    _write(tmp_path / "c.txt", f"{_PROSE}\n")
    assert [(h[0], h[1]) for h in C.scan_markup_repo(tmp_path)] == [
        ("c.txt", C.SIGNAL_TXT_LINE)]


# --- the enumerations, pinned as LEDGERS ---------------------------------------
# 🔴 A test PARAMETRIZED OVER an enumeration cannot see a DELETION from it — the
# case simply stops being generated and the suite goes green with one fewer test.
# So each set is spelled out here independently and asserted for EQUALITY.

_EXPECTED_PERSON_ATTRS = frozenset({
    "data-user-id", "data-userid", "data-user", "data-username", "data-author",
    "data-author-id", "data-member-id", "data-poster-id", "data-account-id",
    "data-profile-id", "data-handle", "rel",
    # the two dialects the XenForo-shaped list above could not see at all
    "data-user-card",                                   # Discourse
    "data-author-fullname", "data-fullname",            # Reddit
})
_EXPECTED_PERSON_ITEMPROPS = frozenset({
    "name", "alternatename", "givenname", "familyname", "author", "creator",
})
_EXPECTED_TEXT_ATTRS = frozenset({
    "content", "alt", "title", "aria-label", "placeholder", "data-caption",
})
_EXPECTED_HTML_SUFFIXES = frozenset({".html", ".htm"})
_EXPECTED_TEXT_SUFFIXES = frozenset({".txt"})
_EXPECTED_DEPENDENCY_MANIFESTS = frozenset({
    "requirements.txt", "requirements-dev.txt", "requirements-test.txt",
    "constraints.txt",
})
#: 🔴 Spelled out rather than derived: this set decides where one paragraph ENDS,
#: so an entry silently added to it MERGES two paragraphs and an entry removed
#: SPLITS one — both change every `<text>` count in the tree without touching a
#: threshold. Adding an inline element here (`a`, `em`, `span`) would defeat the
#: block rule entirely, which is the failure this ledger exists to make loud.
_EXPECTED_BLOCK_ELEMENTS = frozenset({
    "address", "article", "aside", "blockquote", "body", "caption", "dd",
    "details", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer",
    "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "li",
    "main", "nav", "ol", "p", "pre", "section", "summary", "table", "tbody",
    "td", "template", "tfoot", "th", "thead", "title", "tr", "ul",
})


@pytest.mark.parametrize("table,expected", [
    ("PERSON_ATTRS", _EXPECTED_PERSON_ATTRS),
    ("PERSON_ITEMPROPS", _EXPECTED_PERSON_ITEMPROPS),
    ("TEXT_ATTRS", _EXPECTED_TEXT_ATTRS),
    ("HTML_SUFFIXES", _EXPECTED_HTML_SUFFIXES),
    ("TEXT_SUFFIXES", _EXPECTED_TEXT_SUFFIXES),
    ("BLOCK_ELEMENTS", _EXPECTED_BLOCK_ELEMENTS),
    ("DEPENDENCY_MANIFESTS", _EXPECTED_DEPENDENCY_MANIFESTS),
])
def test_the_enumerations_are_pinned_as_a_ledger(table, expected):
    """Failing here means an entry moved. That is a review, not a chore."""
    actual = frozenset(getattr(C, table))
    assert actual == expected, (
        f"{table} drifted — dropped {sorted(expected - actual)}, "
        f"added {sorted(actual - expected)}")


@pytest.mark.parametrize("attr", sorted(_EXPECTED_PERSON_ATTRS - {"rel"}))
def test_every_person_attribute_is_actually_wired(attr, tmp_path):
    """🔴 THE OTHER HALF: every entry must be EXERCISED, not just listed.

    The ledger above catches a deletion; this catches an entry that is present
    and inert. `rel` is excluded because it is value-scoped and has its own pair
    of tests above.
    """
    _write(tmp_path / "d.html",
           f'<html><body><div {attr}="somebody">x</div></body></html>')
    assert [(h[1], h[2]) for h in C.scan_markup_repo(tmp_path)] == [
        (f"@person:{attr}", 1)], f"PERSON_ATTRS[{attr!r}] is enumerated but inert"


@pytest.mark.parametrize("prop", sorted(_EXPECTED_PERSON_ITEMPROPS))
def test_every_person_itemprop_is_actually_wired(prop, tmp_path):
    _write(tmp_path / "d.html",
           f'<html><body><span itemprop="{prop}">x</span></body></html>')
    assert [(h[1], h[2]) for h in C.scan_markup_repo(tmp_path)] == [
        (f"@itemprop:{prop}", 1)], f"PERSON_ITEMPROPS[{prop!r}] is inert"


@pytest.mark.parametrize("attr", sorted(_EXPECTED_TEXT_ATTRS))
def test_every_text_attribute_is_actually_wired(attr, tmp_path):
    """A prose-bearing attribute is a finding; the SAME attribute holding a short
    value is not — otherwise `title="Options"` reds every page in the tree."""
    _write(tmp_path / "long" / "d.html",
           f'<html><body><meta {attr}="{_PROSE}"></body></html>')
    _write(tmp_path / "short" / "d.html",
           f'<html><body><meta {attr}="Options"></body></html>')
    assert [(h[1], h[2]) for h in C.scan_markup_repo(tmp_path / "long")] == [
        (f"@text:{attr}", 1)], f"TEXT_ATTRS[{attr!r}] is enumerated but inert"
    assert C.scan_markup_repo(tmp_path / "short") == []


def test_the_itemprop_and_person_tables_are_lowercase_and_explained():
    """Both tables are DATA, matched case-insensitively, each entry with a reason."""
    for name, table in (("PERSON_ATTRS", C.PERSON_ATTRS),
                        ("PERSON_ITEMPROPS", C.PERSON_ITEMPROPS),
                        ("TEXT_ATTRS", C.TEXT_ATTRS)):
        for key, why in table.items():
            assert key == key.lower(), f"{name}[{key!r}] is not lowercase"
            assert why and len(why) > 15, f"{name}[{key!r}] has no reason"
    assert not (set(C.PERSON_ATTRS) & set(C.TEXT_ATTRS)), "an attr is in both"


def test_attribute_and_tag_matching_is_case_insensitive(tmp_path):
    """`DATA-USER-ID` and `ITEMPROP="Name"` are the same disclosure.

    A capture saved by a tool that upcases attributes must not walk through.
    🔴 Read `test_html_parser_normalises_tag_and_attribute_NAMES` next: only the
    itemprop and `rel` VALUE folds are this module's own code. The NAME half is
    `html.parser`'s doing, and a `.lower()` written here to "handle" it was dead
    on arrival — deleting it left this test green, which is how it was found.
    """
    _write(tmp_path / "d.html",
           '<HTML><BODY><DIV DATA-USER-ID="7781" ITEMPROP="Name">x</DIV></BODY></HTML>')
    got = {s for _p, s, _n, _m in C.scan_markup_repo(tmp_path)}
    assert got == {"@person:data-user-id", "@itemprop:name"}, got


def test_html_parser_normalises_tag_and_attribute_NAMES():
    """🔴 THE DEPENDENCY, PINNED — this module folds case only where it must.

    `_VisibleText` compares raw `tag` against `BLOCK_ELEMENTS`/`_NON_PROSE_ELEMENTS`
    and raw attribute NAMES against `PERSON_ATTRS`/`TEXT_ATTRS` with no `.lower()`
    of its own, because `html.parser` has already lower-cased both. That is an
    external guarantee, not an internal one: if it ever stops holding, the case
    test above starts passing for the wrong reason while every upper-case capture
    walks through. So it is asserted directly, on the parser, here.

    Attribute VALUES are deliberately NOT normalised by the parser — which is why
    the itemprop and `rel` comparisons in `_note_attrs` DO fold, and why deleting
    either of those is caught by a test rather than by nothing.
    """
    seen = []

    class _Probe(C.HTMLParser):
        def handle_starttag(self, tag, attrs):
            seen.append((tag, attrs))

    p = _Probe()
    p.feed('<DIV DATA-USER-ID="7781" ITEMPROP="Name" REL="Author">')
    p.close()
    assert seen == [("div", [("data-user-id", "7781"), ("itemprop", "Name"),
                             ("rel", "Author")])], seen


def test_a_rel_AUTHOR_link_is_matched_case_insensitively(tmp_path):
    """The `rel` VALUE fold, driven — `_PERSON_REL_VALUES` holds `author` only.

    A capture that spells it `REL="Author"` is the same byline. Without this the
    fold at that line is live code with no test: deleting it left the whole
    suite green, because every other `rel` fixture was already lowercase.
    """
    _write(tmp_path / "post.html",
           '<html><body><a REL="Author" href="/u/someone">by</a></body></html>')
    assert [(h[1], h[2]) for h in C.scan_markup_repo(tmp_path)] == [
        ("@person:rel", 1)], C.scan_markup_repo(tmp_path)


# --- `max_len` is the LONGEST, and the exposed helpers agree with the rule ------

def test_max_len_is_the_LONGEST_occurrence_not_the_shortest(tmp_path):
    """🔴 Every `max_len` in the markup half, pinned in one place.

    MEASURED: `max` -> `min` at all three sites (`.html` signals, `.txt` `<line>`,
    `.txt` `<vocabulary>`) SURVIVED the whole battery — every fixture happened to
    carry occurrences of one length, so the two agreed. Each half below therefore
    carries occurrences of DELIBERATELY DIFFERENT lengths, and asserts the larger.
    """
    short = "the follower has been lagging behind the primary since this morning"
    long_ = short + " and for the last three deploys as well"
    assert C.is_free_text(long_) and len(long_) > len(short) >= C.MIN_FREE_TEXT_CHARS
    _write(tmp_path / "h" / "d.html",
           f'<html><body><p>{short}</p><p>{long_}</p>'
           f'<div data-author="ab">x</div><div data-author="abcdefgh">y</div>'
           "</body></html>")
    got = {s: (n, m) for _p, s, n, m in C.scan_markup_repo(tmp_path / "h")}
    assert got == {C.SIGNAL_HTML_TEXT: (2, len(long_)),
                   "@person:data-author": (2, 8)}, got

    _write(tmp_path / "t" / "d.txt", f"{short}\n{long_}\n")
    assert [(h[1], h[2], h[3]) for h in C.scan_markup_repo(tmp_path / "t")] == [
        (C.SIGNAL_TXT_LINE, 2, len(long_))]

    rows = C.MIN_VOCABULARY_LINES + 5
    lines = [f"slug-{i:03d}" for i in range(rows - 1)] + ["slug-with-a-longer-name"]
    _write(tmp_path / "v" / "d.txt", "\n".join(lines) + "\n")
    assert [(h[1], h[2], h[3]) for h in C.scan_markup_repo(tmp_path / "v")] == [
        (C.SIGNAL_TXT_VOCABULARY, rows, len("slug-with-a-longer-name"))]


def test_the_exposed_helpers_report_what_the_rule_measures(tmp_path):
    """`visible_text_runs` and `visible_text_blocks` are in `__all__` because an
    argument about a number should be settleable by running the function that
    produced it — so they must actually agree with the scan.

    Driven on the tree's own occupant, whose numbers the module docstring cites.
    """
    fixture = REPO / "scripts/dl-router/tests/fixtures/forum-thread-page.html"
    markup = fixture.read_text(encoding="utf-8")
    runs = C.visible_text_runs(markup)
    blocks = C.visible_text_blocks(markup)
    assert len(runs) >= 100, (
        f"the docstring cites 108 visible text runs; got {len(runs)}")
    assert max(len(r) for r in runs) < C.MIN_FREE_TEXT_CHARS, (
        "a RUN now clears the threshold — the claim that this file is seen by "
        "the attribute rules and one BLOCK aggregate no longer holds")
    assert sum(1 for b in blocks if C.is_free_text(b)) == 1, (
        f"the pin for this file is one <text> block: "
        f"{sorted(len(b) for b in blocks if C.is_free_text(b))}")
    assert len(C._wordy_runs(runs)) < C.MIN_PROSE_RUNS, (
        "the run-count rule now fires on the fixture; it is pinned as not doing so")


def test_every_public_markup_name_is_exported():
    """🔴 `__all__` is the module's own claim about its surface.

    It shipped missing `TEXT_ATTRS` — a table the gate's ledger reads — which is
    the class of drift this catches. Asserted for EQUALITY over the markup half
    so an addition is a review, not a silent widening.
    """
    expected = {
        "HTML_SUFFIXES", "TEXT_SUFFIXES", "MARKUP_SUFFIXES", "PERSON_ATTRS",
        "PERSON_ITEMPROPS", "TEXT_ATTRS", "BLOCK_ELEMENTS",
        "MIN_VOCABULARY_LINES", "MIN_VOCABULARY_PURITY", "MAX_RECORD_TOKENS",
        "MIN_PROSE_RUNS", "DEPENDENCY_MANIFESTS", "SIGNAL_HTML_TEXT",
        "SIGNAL_HTML_PROSE_RUNS", "SIGNAL_HTML_UNTERMINATED_CODE",
        "SIGNAL_TXT_LINE", "SIGNAL_TXT_VOCABULARY", "visible_text_runs",
        "visible_text_blocks", "scan_html_file", "scan_text_file",
        "scan_markup_file", "scan_markup_repo", "markup_candidates",
        "markup_unparseable_files",
    }
    exported = set(C.__all__)
    assert expected <= exported, f"not exported: {sorted(expected - exported)}"
    for name in expected:
        assert hasattr(C, name), f"__all__ names {name}, which does not exist"


# --- the report must not leak, and the suffix sets must not collide -------------

def test_a_hit_carries_only_paths_ENUMERATED_SIGNALS_and_INTS(tmp_path):
    """🔴 THE REPORT ITSELF MUST NOT LEAK.

    Unlike the JSON half there is no key path to redact — every signal token is
    drawn from an enumeration in the scan module — so the claim is that no token
    originates in the DOCUMENT. Driven with a handle and a sentence that would be
    unmistakable in the output.
    """
    handle = "riverbend_owl_88"
    sentence = "the queue depth alarm has been flapping every twenty minutes since noon"
    _write(tmp_path / "d.html",
           f'<html><body><div data-author="{handle}"><p>{sentence}</p>'
           "</div></body></html>")
    hits = C.scan_markup_repo(tmp_path)
    assert len(hits) == 2, hits
    for relpath, signal, count, max_len in hits:
        assert isinstance(relpath, str) and isinstance(signal, str)
        assert isinstance(count, int) and isinstance(max_len, int)
    blob = str(hits) + _fmt(hits)
    assert handle not in blob, "an author handle leaked into the report"
    assert sentence not in blob, "captured text leaked into the report"
    known = ({f"@person:{a}" for a in C.PERSON_ATTRS}
             | {f"@itemprop:{p}" for p in C.PERSON_ITEMPROPS}
             | {f"@text:{a}" for a in C.TEXT_ATTRS}
             | {C.SIGNAL_HTML_TEXT, C.SIGNAL_HTML_PROSE_RUNS,
                C.SIGNAL_HTML_UNTERMINATED_CODE, C.SIGNAL_TXT_LINE,
                C.SIGNAL_TXT_VOCABULARY})
    assert {h[1] for h in hits} <= known, "a signal token came from the document"


def test_the_markup_suffixes_do_not_overlap_the_json_ones():
    """The two halves must not both claim a file — a `.json` routed to the markup
    walk would be scanned by the wrong rules and silently under-report."""
    assert not (C.MARKUP_SUFFIXES & C.SCANNED_SUFFIXES)
    assert C.MARKUP_SUFFIXES == C.HTML_SUFFIXES | C.TEXT_SUFFIXES


def test_an_undecodable_markup_file_is_REPORTED_not_silently_clean(tmp_path):
    """🔴 A ONE-BYTE BYPASS, closed the way the JSON sibling closes it.

    MEASURED before `markup_unparseable_files` existed: the same captured page
    saved UTF-8 scored 3 signals and saved windows-1252 scored **0** — silently,
    while still counting toward the candidates-walked floor of the positive
    control. Re-encoding a file was a complete pass, and the JSON half pins
    `unparseable_files()` at zero for exactly this. Both halves are driven: the
    undecodable file is NAMED, and the counterpart proves the walk was live in
    the same run.
    """
    page = ("<html><body><p>The council confirmed on Tuesday that the crossing "
            "will stay shut until the barriers arrive — café.</p></body></html>")
    (tmp_path / "bad.html").write_bytes(page.encode("cp1252"))
    _write(tmp_path / "good.html", page)
    assert [h[0] for h in C.scan_markup_repo(tmp_path)] == ["good.html"]
    assert C.markup_unparseable_files(tmp_path) == ["bad.html"]
    # ...and the same bytes as UTF-8 ARE a finding, so the file is not just short.
    _write(tmp_path / "utf8" / "same.html", page)
    assert [(h[1], h[2]) for h in C.scan_markup_repo(tmp_path / "utf8")] == [
        (C.SIGNAL_HTML_TEXT, 1)]


def test_an_undecodable_TXT_is_reported_too(tmp_path):
    """The `.txt` half of the same bypass — `scan_text_file` swallows the same
    `UnicodeDecodeError` and must not be the only thing that saw it."""
    (tmp_path / "bad.txt").write_bytes(
        "\n".join(f"handle-{i:03d}-café" for i in range(40)).encode("cp1252"))
    assert C.scan_markup_repo(tmp_path) == []
    assert C.markup_unparseable_files(tmp_path) == ["bad.txt"]


def test_the_repo_has_no_unparseable_markup():
    """The zero, reported as a PAIR with the negative controls above.

    `markup_candidates` is re-counted here rather than trusted: a zero from an
    enumeration that walked nothing is the failure mode, not the all-clear.
    """
    files = C.markup_candidates(REPO)
    assert len(files) >= 12, f"only {len(files)} candidates — the walk is empty"
    assert C.markup_unparseable_files(REPO) == []


def test_a_PARSER_failure_is_reported_as_unparseable_not_as_clean(tmp_path,
                                                                  monkeypatch):
    """🔴 THE `except Exception` PATH, REACHED — it was unreachable by the suite.

    MEASURED: replacing that `except` with a bare `raise` SURVIVED the whole
    battery, because `html.parser` does not raise on unclosed tags and no input
    was found that makes it raise at all. So the "a malformed page must not break
    CI" guard was itself an untested second silent-drop path. It is reached here
    by making the parser raise, and what it must now do is REPORT the file, not
    return an empty list that reads as clean.
    """
    boom = RuntimeError("parser exploded")

    def _raise(self, _data):
        raise boom

    monkeypatch.setattr(C._VisibleText, "feed", _raise)
    _write(tmp_path / "torn.html", f"<html><body><p>{_PROSE}</p></body></html>")
    assert C.scan_markup_repo(tmp_path) == [], "the throw was not contained"
    assert C.markup_unparseable_files(tmp_path) == ["torn.html"]


def test_malformed_markup_degrades_to_no_hits_never_a_throw(tmp_path):
    """A half-saved page must not break CI for everyone else.

    NOTE this does NOT reach the `except` above — `html.parser` recovers from
    unclosed tags without raising, which is why the test above exists. What it
    pins is that a torn page is still SCANNED: the attribute is found, and the
    file is not reported as unparseable.
    """
    _write(tmp_path / "torn.html",
           '<html><body><div data-user-id="991"><p>unclosed')
    got = {s: n for _p, s, n, _m in C.scan_markup_repo(tmp_path)}
    assert got.get("@person:data-user-id") == 1, got
    assert C.markup_unparseable_files(tmp_path) == []


def test_an_unterminated_script_is_a_FINDING_not_silence(tmp_path):
    """🔴 THE `<template>` HAZARD THROUGH THE OTHER DOOR.

    `_NON_PROSE_ELEMENTS` keeps `<template>` out precisely so no single tag can
    hide a captured page. An UNTERMINATED `<script>` did exactly that: it puts
    `html.parser` into CDATA mode to EOF, so MEASURED, the whole remainder of a
    document — prose and person attributes alike — scored 0. Both halves here:
    the same page terminated is scanned normally.
    """
    tail = f'<p>{_PROSE}</p><div data-user-id="4471">x</div>'
    _write(tmp_path / "open" / "u.html",
           f"<html><body><script>var a=1;{tail}</body></html>")
    _write(tmp_path / "closed" / "u.html",
           f"<html><body><script>var a=1;</script>{tail}</body></html>")
    opened = {s: n for _p, s, n, _m in C.scan_markup_repo(tmp_path / "open")}
    closed = {s: n for _p, s, n, _m in C.scan_markup_repo(tmp_path / "closed")}
    assert opened == {C.SIGNAL_HTML_UNTERMINATED_CODE: 1}, opened
    assert closed == {C.SIGNAL_HTML_TEXT: 1, "@person:data-user-id": 1}, closed


def test_an_unterminated_STYLE_is_the_same_finding(tmp_path):
    """`<style>` is the other member of `_NON_PROSE_ELEMENTS`, so it is the same
    one-tag hide — enumerated rather than left to the reader."""
    _write(tmp_path / "u.html",
           f"<html><body><style>.a{{color:red}}<p>{_PROSE}</p></body></html>")
    got = {s: n for _p, s, n, _m in C.scan_markup_repo(tmp_path)}
    assert got == {C.SIGNAL_HTML_UNTERMINATED_CODE: 1}, got


def test_html_COMMENTS_are_not_scanned_and_the_blindness_is_pinned(tmp_path):
    """🔴 A DECISION, NOT A DEFAULT — both halves driven.

    MEASURED: a 148-character sentence inside `<!-- ... -->` scores 0. Scanning
    comments is rejected because this repo's fixtures document themselves that
    way — `forum-thread-page.html` carries 7 documentation comments of its own,
    and the gate would then be red on its own documentation. The cost is that
    captured text pasted into a comment is invisible, which the module's
    blind-spot list names in those words and this pins.
    """
    sentence = ("The council confirmed on Tuesday that the crossing will stay "
                "shut until the replacement barriers arrive in the new year, "
                "and the footbridge is the diversion.")
    assert len(sentence) > C.MIN_FREE_TEXT_CHARS
    _write(tmp_path / "hidden" / "c.html",
           f"<html><body><!-- {sentence} --></body></html>")
    _write(tmp_path / "shown" / "c.html",
           f"<html><body><p>{sentence}</p></body></html>")
    assert C.scan_markup_repo(tmp_path / "hidden") == []
    assert [(h[1], h[2]) for h in C.scan_markup_repo(tmp_path / "shown")] == [
        (C.SIGNAL_HTML_TEXT, 1)]


# --- guarding the guard's inputs -----------------------------------------------

def test_the_scan_actually_reads_the_repo():
    """A wrong REPO root yields an empty file set and the zero above passes
    vacuously — the exact bug the IP gate shipped and caught."""
    files = C.repo_files(REPO)
    assert len(files) > 300, f"REPO={REPO} produced only {len(files)} files"
    assert any(f.name == "flake.nix" for f in files), "not the devrc root"


def test_the_skip_dir_test_is_relative_to_the_root(tmp_path):
    """🔴 Every agent worktree lives under `…/worktrees/<id>/`, so an
    ABSOLUTE-parts skip test is true for every file and skips the whole repo
    while reporting a clean zero. The markup walk reaches that logic through the
    same delegation, so the pin belongs here too.
    """
    root = tmp_path / "worktrees" / "agent-x"
    _write(root / "sub" / "d.html",
           f"<html><body><p>{_PROSE}</p></body></html>")
    hits = C.scan_markup_repo(root)
    assert len(hits) == 1, (
        f"the repo was skipped because its own PATH contains a skip-dir: {hits}")


def test_markup_candidates_re_applies_the_skip_dir_test(tmp_path):
    """🔴 IT ONLY SHOWS ON THE `git ls-files` TIER, so the fixture must build it.

    `repo_files` applies the skip test itself on the filesystem-walk fallback, so
    a `tmp_path` with no `.git` passes with or without the re-application and
    proves nothing. This drives a REAL git repo, where a tracked path under a
    skip dir comes back and only `markup_candidates` drops it.
    """
    import shutil
    assert shutil.which("git"), (
        "git is not on PATH — this test must NOT skip its way to green: "
        "run-tests.sh lists git in REQUIRED_TOOLS and the nix check puts it in "
        "nativeBuildInputs, so its absence is a harness fault")
    _write(tmp_path / "node_modules" / "pkg" / "d.html",
           f"<html><body><p>{_PROSE}</p></body></html>")
    _write(tmp_path / "keep.html", f"<html><body><p>{_PROSE}</p></body></html>")

    def run(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True,
                       capture_output=True)

    run("init", "-q")
    run("-c", "core.excludesFile=/dev/null", "add", "-f",
        "keep.html", "node_modules/pkg/d.html")
    tracked = C.repo_files(tmp_path)
    assert any("node_modules" in p.parts for p in tracked), (
        "the git tier did not return the skipped path — this fixture is not "
        "exercising the tier the divergence lives on")
    assert [str(p.relative_to(tmp_path)) for p in C.markup_candidates(tmp_path)] \
        == ["keep.html"]


def test_the_file_enumeration_is_the_ip_gates_not_a_fourth_copy():
    """🔴 ONE RULE, ONE PLACE — the seam, pinned rather than trusted.

    Identity, not equality: a divergent copy that happened to behave the same
    today would satisfy an equality check and rot apart later.
    """
    assert C.repo_files is P.repo_files
    assert C._is_skipped is P._is_skipped


def test_the_gate_is_blind_to_git_history(tmp_path):
    """🔴 THE STRUCTURAL GAP, DRIVEN — see this module's docstring.

    All four scans enumerate TRACKED FILES AT HEAD. A file that was committed and
    later deleted is still in history, still clonable, and returns NOTHING here.
    That is how three genuine P-256 private keys sat in this repo's reachable
    history for four to five years while every gate reported clean.

    This is a LIMITATION PIN, not regression coverage: it is green before and
    after, and it exists so the blind spot is a measured fact instead of a
    sentence in a docstring that nobody re-checks. If someone later teaches these
    gates to read history, THIS test is what must change, and its failure is the
    prompt to update the claim in `SECRETS.md`.
    """
    import shutil
    assert shutil.which("git"), "git is a harness requirement, not optional"

    # 🔴 INHERIT the environment and override only the identity. Building a `env=`
    # from scratch drops PATH, and on NixOS there is no `/usr/bin/git` to fall
    # back to — the test then fails for a reason that has nothing to do with the
    # claim it is making.
    import os
    env = dict(os.environ,
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e.test",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e.test",
               GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")

    def run(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True,
                       capture_output=True, env=env)

    secret = _write(tmp_path / "leak.html",
                    f"<html><body><p>{_PROSE}</p></body></html>")
    run("init", "-q")
    run("-c", "core.excludesFile=/dev/null", "add", "-f", "leak.html")
    run("commit", "-q", "-m", "add")
    # It IS a finding while it is at HEAD — the positive control for this test.
    assert [h[0] for h in C.scan_markup_repo(tmp_path)] == ["leak.html"]

    secret.unlink()
    run("rm", "-q", "--cached", "leak.html")
    run("commit", "-q", "-m", "remove")

    # ...and the SAME content, still fully recoverable from history, is invisible.
    assert C.scan_markup_repo(tmp_path) == [], (
        "the scan reached history — if that is now intended, update this test "
        "AND the history claim in SECRETS.md and this module's docstring")
    log = subprocess.run(["git", "-C", str(tmp_path), "log", "--all",
                          "--diff-filter=A", "--name-only", "--format="],
                         capture_output=True, check=True)
    assert "leak.html" in log.stdout.decode(), (
        "the fixture did not actually leave the content in history, so the "
        "blindness above was not demonstrated")
