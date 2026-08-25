#!/usr/bin/env python3
"""Gate on `--sanitize` and on the CONTENT/MEASUREMENT SEPARATION.

TWO PROPERTIES, ONE FILE, BECAUSE THEY GUARD THE SAME HAZARD
------------------------------------------------------------
`CLAUDE.md`: this repo is PUBLIC. Two different ways a private value reaches a
committed artefact:

  1. it is BAKED IN — a real scope name, hostname or path written into committed
     source or a fixture. Guarded here by `test_no_real_identifier_is_committed_
     in_the_generator`, which reads the generator's own source.
  2. it is RENDERED — the page is built on a real machine and then shared.
     Guarded by the substitution tests, which drive `Sanitizer` over
     realistically-shaped values and watch each class change.

🔴 SANITIZATION IS A REDACTION AID, NOT A SECURITY BOUNDARY, and these tests are
scoped to say only what they can. They prove the KNOWN classes are substituted
and that the mapping is STABLE. They cannot prove an unknown class is caught —
`test_the_sanitizer_cannot_see_an_unknown_identifier_class` pins that limitation
as a property, so nobody reads this suite as a guarantee it does not make.

WHAT COUNTS AS COVERAGE. All INVARIANT GUARDS: `scripts/present/` is new in this
commit, so nothing here can be shown red on pre-change code. Each substitution
test carries its own control — the un-sanitized path must leave the value ALONE —
because a `Sanitizer` that rewrote everything unconditionally would pass a
one-sided assertion and destroy the private build.

Every value below is INVENTED. Real identifiers are read at run time from local
state and appear in no fixture, which is the arrival path this repo's captured-
text and hostname gates were written for.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

pytest.importorskip("present")
from present import content, measure, render, sanitize  # noqa: E402

PRESENT_DIR = SCRIPTS / "present"

# Invented, and deliberately shaped like the real thing.
FAKE_SCOPES = ("acme-widgets", "acme-widgets-infra", "borealis")
FAKE_HOME = "/home/jrandom"
FAKE_HOST = "console.acme-widgets.io"
FAKE_IP = "203.0.113.44"
FAKE_STORE = "/nix/store/abcdefghijklmnopqrstuvwxyz012345-python3-3.12.14"


def _san(enabled=True) -> sanitize.Sanitizer:
    return sanitize.Sanitizer(
        enabled=enabled, home=FAKE_HOME, user="jrandom", scopes=FAKE_SCOPES)


# --------------------------------------------------------------------------- #
# The substitution classes — each with its disabled-path control
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value,must_vanish", [
    (f"the store lives at {FAKE_HOME}/.claude/analyze-service-index", FAKE_HOME),
    (f"scope {FAKE_SCOPES[0]} holds 6 entries", FAKE_SCOPES[0]),
    (f"the pod answered on {FAKE_HOST}", FAKE_HOST),
    (f"bound to {FAKE_IP}:8080", FAKE_IP),
    (f"resolved to {FAKE_STORE}/bin/python3", "abcdefghijklmnopqrstuvwxyz012345"),
    ("owned by jrandom", "jrandom"),
    ("-home-jrandom-workspace-acme-widgets holds 9,001 B", "acme-widgets"),
])
def test_each_known_identifier_class_is_substituted(value, must_vanish):
    """INVARIANT GUARD, one case per class the sanitizer claims to cover."""
    out = _san().text(value)
    assert must_vanish not in out, f"{must_vanish!r} survived sanitization: {out!r}"
    assert out != value


@pytest.mark.parametrize("value", [
    f"{FAKE_HOME}/x", f"scope {FAKE_SCOPES[0]}", FAKE_HOST, FAKE_IP, FAKE_STORE,
])
def test_the_disabled_sanitizer_is_the_identity_transform(value):
    """CONTROL. Without the flag, nothing moves.

    🔴 Without this, a `Sanitizer` that rewrote every string unconditionally
    would satisfy every test above while silently destroying the private build.
    """
    assert sanitize.Sanitizer(enabled=False, home=FAKE_HOME, user="jrandom",
                              scopes=FAKE_SCOPES).text(value) == value


def test_the_substitution_is_stable_within_a_build():
    """INVARIANT GUARD. One real value maps to one stand-in, every time.

    An unstable map makes two builds of the same page uncomparable, and makes a
    single page internally inconsistent — the same scope appearing under two
    names reads as two scopes.
    """
    s = _san()
    a = s.text(f"{FAKE_SCOPES[0]} and {FAKE_HOST}")
    b = s.text(f"again: {FAKE_SCOPES[0]} and {FAKE_HOST}")
    for token in re.findall(r"scope-\d+|host-\d+\.example\.test", a):
        assert token in b, f"{token} was not reused on the second call"


def test_a_scope_name_that_prefixes_another_is_not_eaten():
    """INVARIANT GUARD against the classic substitution bug.

    Substituting the short name first would rewrite the prefix of the long one
    and leave a mangled hybrid, which looks sanitized and is not.
    """
    out = _san().text(f"{FAKE_SCOPES[1]} vs {FAKE_SCOPES[0]}")
    assert FAKE_SCOPES[0] not in out and FAKE_SCOPES[1] not in out
    names = re.findall(r"scope-\d+", out)
    assert len(set(names)) == 2, f"the two distinct scopes collapsed: {out!r}"


def test_reserved_and_placeholder_hosts_are_left_alone():
    """INVARIANT GUARD. Rewriting an already-synthetic host makes the page worse.

    `*.test` / `*.example` / `*.invalid` are RFC 6761 reserved: they name nobody.
    Rewriting them would add churn and suggest a real value had been hidden.
    """
    for host in ("thing.example.test", "localhost", "example.com"):
        assert host in _san().text(f"see {host} for details")


def test_a_filename_is_not_mistaken_for_a_hostname():
    """INVARIANT GUARD. A dotted token is not automatically a host.

    A sanitizer that rewrote `run-tests.sh` to a fake hostname would make the
    sanitized page unusable as documentation while proving nothing about privacy.
    """
    out = _san().text("see scripts/run-tests.sh and nix/home.nix and a.jsonl")
    for name in ("run-tests.sh", "home.nix", "a.jsonl"):
        assert name in out, f"{name} was rewritten as if it were a hostname: {out!r}"


def test_none_stays_none_and_never_becomes_an_empty_string():
    """INVARIANT GUARD, and it is the silent-zero rule in miniature.

    An unmeasured row's `value` is `None`. Collapsing that to `""` would turn
    "never measured" into "measured empty".
    """
    assert _san().text(None) is None
    assert sanitize.Sanitizer(enabled=False).text(None) is None


def test_a_path_mangled_project_slug_is_substituted():
    """🔴 REGRESSION COVERAGE, and the only genuine instance in this suite.

    This one WAS red before the fix: the sanitized page was generated, read, and
    found to still carry client and repo names inside per-project state-directory
    slugs — an absolute path with its separators rewritten as dashes, which the
    home-path rule cannot match and the scope rule does not know about. Every
    other substitution test was green throughout.

    The lesson is the one the module docstring states and this test makes
    concrete: the sanitizer catches the classes it has been SHOWN, and reading
    the output is the only thing that finds the rest.
    """
    out = _san().text("-home-jrandom-workspace-borealis-infra: 12,750 B")
    assert "borealis" not in out and "jrandom" not in out
    assert re.search(r"project-\d+", out), out
    # And the disabled path still leaves it exactly alone.
    assert sanitize.Sanitizer(enabled=False).text("-home-jrandom-x-y") == "-home-jrandom-x-y"


def test_the_sanitizer_cannot_see_an_unknown_identifier_class():
    """🔴 THE LIMITATION, PINNED AS A PROPERTY rather than left as a caveat.

    An identifier shaped like nothing the sanitizer knows walks straight
    through. This test asserts that it does, so nobody can read this suite as
    proving the sanitized page is safe to publish unread. An honest claim beats
    a reassuring one.
    """
    opaque = "PROJECT-CODENAME-QUARTZ-7719"
    assert opaque in _san().text(f"internal reference {opaque}")


# --------------------------------------------------------------------------- #
# Applied across a whole MeasurementSet
# --------------------------------------------------------------------------- #


def _row(key, **kw):
    base = dict(key=key, label="L", section="soft", status=measure.MEASURED,
                asof="2000-01-01 00:00 UTC", source="s", value="v")
    base.update(kw)
    return measure.Measurement(**base)


def test_apply_sanitizes_every_string_field_including_an_absence():
    """SEAM GUARD. The field that gets skipped is the field that leaks.

    An UNMEASURED row's `reason` is as likely to name a real path as a measured
    row's value — more so, since a reason is usually "X does not exist".
    """
    ms = measure.MeasurementSet()
    ms.items.append(_row("a", value=f"at {FAKE_HOME}", detail=f"see {FAKE_HOST}",
                         source=f"read {FAKE_HOME}/x",
                         columns=("c",), rows=((FAKE_SCOPES[0],),)))
    ms.items.append(measure.Measurement(
        key="b", label="L", section="soft", status=measure.UNMEASURED,
        asof="2000-01-01 00:00 UTC", source="(not reached)",
        reason=f"{FAKE_HOME}/missing does not exist",
        settle=f"ls {FAKE_HOME}/missing"))
    out = sanitize.apply(ms, _san())
    blob = repr([(m.value, m.detail, m.source, m.reason, m.settle, m.rows) for m in out])
    for secret in (FAKE_HOME, FAKE_HOST, FAKE_SCOPES[0]):
        assert secret not in blob, f"{secret!r} survived apply(): {blob!r}"
    assert out.by_key("b").status == measure.UNMEASURED, "apply() changed a status"


def test_apply_preserves_the_measured_unmeasured_split():
    """INVARIANT GUARD. Sanitizing must not turn an absence into a value."""
    ms = measure.MeasurementSet()
    ms.items.append(_row("a"))
    ms.items.append(measure.Measurement(
        key="b", label="L", section="soft", status=measure.UNMEASURED,
        asof="t", source="(not reached)", reason="r", settle="s"))
    out = sanitize.apply(ms, _san())
    assert len(out.measured) == 1 and len(out.unmeasured) == 1
    assert out.by_key("b").value is None


def test_the_sanitized_page_declares_itself_and_leaks_no_real_value():
    """INVARIANT GUARD on the artefact, not on the transform.

    The reader must be able to tell a sanitized page from a full one at a
    glance — a sanitized page that looked identical would be indistinguishable
    from one nobody remembered to sanitize.
    """
    ms = measure.MeasurementSet()
    ms.items.append(_row("repo.head", section="how-to-read",
                         value=f"abc1234 on main at {FAKE_HOME}"))
    san = _san()
    page = render.build_html(sanitize.apply(ms, san), sanitized=True, san=san,
                             sections=(content.SECTIONS[0],))
    assert "SANITIZED" in page
    assert FAKE_HOME not in page
    plain = render.build_html(ms, sanitized=False, sections=(content.SECTIONS[0],))
    assert "contains local identifiers" in plain


def test_the_legend_never_prints_the_real_side():
    """🔴 INVARIANT GUARD on the obvious own goal.

    A two-column legend would re-publish, inside the sanitized artefact, exactly
    the values the sanitized artefact exists to remove.
    """
    s = _san()
    s.text(f"{FAKE_SCOPES[0]} {FAKE_HOST} {FAKE_IP}")
    blob = repr(s.legend())
    for secret in (FAKE_SCOPES[0], FAKE_HOST, FAKE_IP):
        assert secret not in blob
    assert s.substitutions >= 3, "the legend counted nothing — a zero here is the failure"


# --------------------------------------------------------------------------- #
# The committed source itself
# --------------------------------------------------------------------------- #


def test_no_real_identifier_is_committed_in_the_generator():
    """🔴 THE PUBLIC-REPO GUARD, read off the committed bytes.

    The generator source is committed; the data it renders is not. This walks
    every file under `scripts/present/` and fails on a routable IPv4 literal or
    an absolute home path — the two classes that are unambiguous from source
    alone. Scope and client names cannot be checked this way (they are read at
    run time and appear nowhere to compare against), which is precisely why they
    must never be written down here in the first place.
    """
    offenders = []
    ipv4 = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b")
    reserved_first = {"0", "10", "127", "169", "172", "192", "198", "203", "224", "255"}
    for path in sorted(PRESENT_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for m in ipv4.finditer(text):
            octets = m.groups()
            if not all(0 <= int(o) <= 255 for o in octets):
                continue                      # a version string
            if octets[0] in reserved_first:
                continue                      # documentation / private ranges
            offenders.append(f"{path.name}: routable IPv4 literal {m.group(0)}")
        for hit in re.finditer(r"/home/(?!operator\b|user\b|jrandom\b)[a-z][a-z0-9_-]*", text):
            offenders.append(f"{path.name}: absolute home path {hit.group(0)}")
    assert not offenders, (
        "a real identifier is committed to a PUBLIC repo:\n  " + "\n  ".join(offenders))


def test_the_generator_ships_no_committed_html_or_data_fixture():
    """🔴 INVARIANT GUARD. A rendered page must never be committed.

    A generated page carries every value the machine that built it held, and
    `.html` is the format a captured page arrives in — the class this repo's
    markup gate exists for. The generator is committed; its output is not.
    """
    stray = [p.name for p in PRESENT_DIR.rglob("*")
             if p.is_file() and p.suffix.lower() in {".html", ".htm", ".json",
                                                     ".jsonl", ".txt", ".csv"}]
    assert not stray, f"generated or captured data is committed under present/: {stray}"


def test_the_content_module_never_restates_a_live_measured_number():
    """🔴 SEAM GUARD — the one that keeps the page from rotting.

    The whole premise is that prose names MECHANISMS and only measured rows
    carry VALUES. The moment a paragraph restates a number the generator
    measures, that paragraph starts aging, and this repo has measured its own
    prose false in both directions.

    So: take every value the live registry produces, pull the multi-digit
    numeric tokens out of it, and assert none of them appears in the content
    module's prose. Historical measurements (a killed proposal's numbers, a
    dated incident) are permanently true and are deliberately NOT in scope —
    they cannot collide, because they are not what the generator measures.
    """
    env = measure.Env(repo=REPO_ROOT, home=Path.home(),
                      claude_dir=Path.home() / ".claude",
                      index_store=Path.home() / ".claude" / "analyze-service-index",
                      allow_systemd=False)
    ms = measure.take(env)
    assert ms.measured, "nothing measured — this guard would then be vacuous"

    prose = (PRESENT_DIR / "content.py").read_text(encoding="utf-8")
    live: set[str] = set()
    for m in ms.measured:
        for token in re.findall(r"\d[\d,]{2,}", m.value or ""):
            live.add(token)
            live.add(token.replace(",", ""))
    collisions = sorted(t for t in live if t in prose)
    assert not collisions, (
        "content.py restates a number the generator measures live: "
        f"{collisions}. Name the mechanism in prose and let the measured row "
        "carry the value.")
