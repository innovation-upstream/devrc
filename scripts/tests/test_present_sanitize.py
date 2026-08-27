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

WHAT COUNTS AS COVERAGE. Most of this file is INVARIANT GUARDS: `scripts/present/`
was new in the commit that added them, so they cannot be shown red on pre-change
code. Each substitution test carries its own control — the un-sanitized path must
leave the value ALONE — because a `Sanitizer` that rewrote everything
unconditionally would pass a one-sided assertion and destroy the private build.

🔴 THE `column_kinds` TESTS ARE REGRESSION COVERAGE, NOT INVARIANT GUARDS, and
the difference is load-bearing. They pin a leak that SHIPPED: harvested prose
naming a third party rode into a page stamped SANITIZED while four other
identifier classes on the same run went to zero. `test_a_declared_PROSE_cell_is_
withheld_whole_not_scrubbed` is red on the pre-change tree — verify it there
before trusting it, since a guard nobody has watched fail proves nothing.

Every value below is INVENTED. Real identifiers are read at run time from local
state and appear in no fixture, which is the arrival path this repo's captured-
text and hostname gates were written for.
"""
from __future__ import annotations

import ipaddress
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# Plain import, deliberately — see the note in test_present_measure.py.
from present import content, measure, render, sanitize  # noqa: E402

PRESENT_DIR = SCRIPTS / "present"

# Invented, and deliberately shaped like the real thing.
FAKE_SCOPES = ("acme-widgets", "acme-widgets-infra", "borealis")
FAKE_HOME = "/home/jrandom"
FAKE_HOST = "console.acme-widgets.io"
FAKE_IP = "203.0.113.44"
#: RFC 4193 unique-local — a private topology address, so it names nobody
#: outside the machine that holds it AND is exactly the class a shareable page
#: must not carry. Deliberately NOT `2001:db8::/32`: that prefix is reserved for
#: documentation, is already synthetic, and is what this module substitutes INTO.
FAKE_IP6 = "fd00:1234:5678::beef"
FAKE_STORE = "/nix/store/abcdefghijklmnopqrstuvwxyz012345-python3-3.12.14"
#: A machine name that reads as a machine name — it carries a hyphen. See
#: `test_a_bare_hostname_that_reads_as_a_WORD_is_declined_and_counted` for the
#: other half of this class, which is the one that cannot be substituted safely.
FAKE_NODE = "workbench-prod"

#: Local identifiers for a `name` cell. Deliberately mixed:
#:
#:   * `quartzsight` and `pelagic-mailbox` are shaped like the leak that
#:     motivated the class — a third party's name used as a local identifier;
#:   * `bar` and `resume` are shaped like the CORRUPTION risk — ordinary English
#:     words that are also real local identifiers. They are what proves the
#:     substitution stays inside the cell it was declared on.
#:   * `mailbox` and `pelagic-mailbox` are a PREFIX PAIR reachable through
#:     `_word`'s hyphen boundary. Without a pair like this in the fixture, the
#:     longest-first ordering in `_names` is untestable — and reversing it was
#:     measured to keep the whole suite green while publishing a real client
#:     name in cleartext.
#:
#: 🔴 KEPT IN THE ORDER `build()` ACTUALLY PRODUCES — `tuple(sorted(set(...)))`,
#: i.e. ALPHABETICAL, which puts `mailbox` BEFORE `pelagic-mailbox`. The first
#: version of this tuple listed the longer name first, and that accident rescued
#: a mutant: deleting the `sorted(..., key=len, reverse=True)` in `_names`
#: entirely left the suite green, because iteration order happened to be
#: longest-first anyway. Production never sees that order. A fixture whose
#: incidental layout does the work of the code under test is not a fixture, it
#: is a second bug.
FAKE_NAMES = ("bar", "mailbox", "pelagic-mailbox", "quartzsight", "resume")
#: A sentence of the kind a measurer HARVESTS rather than authors. It names a
#: third party that appears in NO name list — which is the whole point: this is
#: the value substitution structurally cannot reach.
FAKE_PROSE = "Walk the Northwind Dental funnel and screenshot every view"


def _san(enabled=True, **kw) -> sanitize.Sanitizer:
    base = dict(enabled=enabled, home=FAKE_HOME, user="jrandom",
                scopes=FAKE_SCOPES, hostnames=(FAKE_NODE,),
                local_names=FAKE_NAMES)
    base.update(kw)
    return sanitize.Sanitizer(**base)


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
    (f"listening on [{FAKE_IP6}]:8080", FAKE_IP6),
    (f"ssh {FAKE_NODE} and read the journal", FAKE_NODE),
])
def test_each_known_identifier_class_is_substituted(value, must_vanish):
    """INVARIANT GUARD, one case per class the sanitizer claims to cover."""
    out = _san().text(value)
    assert must_vanish not in out, f"{must_vanish!r} survived sanitization: {out!r}"
    assert out != value


@pytest.mark.parametrize("value", [
    f"{FAKE_HOME}/x", f"scope {FAKE_SCOPES[0]}", FAKE_HOST, FAKE_IP, FAKE_IP6,
    FAKE_STORE, FAKE_NODE,
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


#: Dotted tokens that appear in this page and are NOT hostnames. Every one of
#: the first three was measurably corrupted on the shipped page.
NOT_HOSTNAMES = [
    "core.hooksPath",       # rendered as `git config --get host-07.example.test`
    "home.file",            # the nix deploy idiom
    "activity.events",      # a database table
    "os.path.basename",     # an attribute chain
    "measure.take",
    "run-tests.sh", "home.nix", "a.jsonl", "settings.json", "drift-check.sh",
    "bash-guard.py", "flake.nix", "MEMORY.md", "skill-tiers.json",
    "devrc-nodetests.timer", "mail-actions-archive.service",
]


@pytest.mark.parametrize("token", NOT_HOSTNAMES)
def test_a_dotted_token_that_is_not_a_host_survives_untouched(token):
    """🔴 REGRESSION COVERAGE — the CORRUPTION direction, and it was red.

    The shipped rule treated any dotted lowercase token as a hostname unless its
    suffix sat in a hand-listed deny-list of file extensions. That list can only
    enumerate collisions somebody already hit, so a config key, a nix attribute
    and a database table all became fake hostnames — and the sanitized page
    rendered its own settle command as
    `git config --local/--global --get host-07.example.test`, a command that
    cannot work, on a page whose thesis is "run the settle command yourself".

    The predecessor of this test covered exactly three suffixes (`.sh`, `.nix`,
    `.jsonl`) — narrower than the hazard, and green throughout the corruption.
    `HOST_TLDS` inverts the shape: a token is a host only if this module
    recognises its last label.
    """
    sentence = f"see {token} in the tree"
    out = _san().text(sentence)
    assert out == sentence, f"{token!r} was rewritten as if it were a hostname: {out!r}"


def test_the_tld_allowlist_is_a_documented_incompleteness_not_a_promise():
    """🔴 THE LIMITATION OF THE NEW SHAPE, PINNED, so it cannot be read as a fix
    for more than it is.

    An allow-list trades one failure for another: a corrupted document becomes a
    hostname that is NOT rewritten. That is the better trade — over-redaction on
    a shareable page is a wrong command, under-redaction is a name the reader can
    see and act on — but it is a trade, and it must be visible. Suffixes that are
    also common file extensions or common English words are excluded ON PURPOSE.
    """
    for collision in ("sh", "md", "rs", "pl", "so", "cc", "it", "in", "is", "to"):
        assert collision not in sanitize.HOST_TLDS, (
            f"`.{collision}` is a real TLD AND a common extension or word. Adding "
            "it to HOST_TLDS trades a leak for a corrupted command in a document "
            "meant for an outsider — see the deny-list this replaced.")
    # And the control: a suffix that IS on the list really does get substituted,
    # so the list is not merely an empty gesture.
    assert "com" in sanitize.HOST_TLDS
    out = _san().text("reachable at console.borealis-corp.com today")
    assert "borealis-corp.com" not in out and "host-" in out


def test_no_registered_measurement_KEY_is_mistaken_for_a_hostname():
    """🔴 SEAM GUARD between the REGISTRY and the sanitizer, and it was RED.

    Every measurement key here is a dotted lowercase token — `index.store`,
    `gate.tiers`, `repo.head`, `rules.bytes` — which is exactly the shape the
    host rule matches on. Found by reading the rendered page and sweeping it, not
    by any test: with `store` on the allow-list, `index.store` came out as
    `host-01.example.test`. That is the SAME corruption the deny-list produced,
    walked back in through its replacement — which is the whole reason this is a
    ledger over the live registry rather than a fixed list of examples.

    It fails when the registry GROWS a key whose suffix is on the allow-list, and
    when the allow-list grows a suffix some existing key ends in. Neither side
    can move without the other noticing.
    """
    s = _san()
    offenders = []
    for entry in measure.REGISTRY:
        key = entry[0]
        out = s.text(f"see {key} for details")
        if key not in out:
            offenders.append(f"{key} -> {out}")
    # POSITIVE CONTROL: a registry this walk could not read would report zero.
    assert len(measure.REGISTRY) >= 10, (
        f"only {len(measure.REGISTRY)} keys examined — too few to believe a zero")
    assert not offenders, (
        "the page's own measurement keys were rewritten as hostnames:\n  "
        + "\n  ".join(offenders))


def test_the_host_allowlist_holds_no_common_english_noun():
    """CONTROL for the rule above, stated as the PROPERTY rather than the case.

    Pinning only `index.store` would let the next noun-gTLD in and be caught only
    when some future key happened to end in it. The property is what actually
    holds: a dotted token in this document is far more often an attribute, a key
    or a table name than a host, and the new-gTLD namespace is exactly the set of
    words those get named after.
    """
    nouns = {"store", "page", "link", "live", "team", "email", "tools", "app",
             "systems", "services", "network", "digital", "site", "online",
             "shop", "space", "tech", "cloud", "wiki", "blog", "xyz", "int",
             "name", "zip", "codes", "works", "run", "how", "new"}
    collisions = sorted(nouns & sanitize.HOST_TLDS)
    assert not collisions, (
        f"{collisions} are English nouns sold as gTLDs. On this page they are far "
        "more likely to be the tail of an attribute, config key or table name than "
        "a real host — see the measured `index.store` case.")


def test_the_svg_namespace_host_is_never_rewritten():
    """🔴 SEAM GUARD between the sanitizer and the self-containment check.

    `www.w3.org` is the SVG XML namespace and the single external-looking token
    `generate.self_contained()` subtracts before scanning. Rewriting it would
    break every inlined diagram AND make the page fail its own self-containment
    check, because the subtraction would no longer match the bytes on the page.
    Two modules, one string; neither test file owns both, so it is pinned here.
    """
    from present import generate
    assert "www.w3.org" in generate._ALLOWED_URI
    kept = _san().text(f"declared as {generate._ALLOWED_URI}")
    assert generate._ALLOWED_URI in kept, kept


# --------------------------------------------------------------------------- #
# The leak classes — every one of these was reproduced on the real page
# --------------------------------------------------------------------------- #


def test_the_operators_own_NAME_does_not_survive_a_capital_letter():
    """🔴 REGRESSION COVERAGE, and the leak that mattered most.

    The username rule was case-SENSITIVE while the hostname rule beside it was
    not. A skill description that capitalised the operator's given name — as
    English does with a name — walked through a page built to be handed to an
    outsider, while the same name inside a path was replaced. Measured on the
    real page: `Zach` present in the sanitized output, `zach` absent from it.
    """
    for spelling in ("jrandom", "Jrandom", "JRANDOM", "JRandom"):
        out = _san().text(f"evaluate them WITH {spelling} on Tuesday")
        assert spelling not in out, f"{spelling!r} survived: {out!r}"
        assert "operator" in out


def test_a_scope_name_does_not_survive_a_capital_letter():
    """🔴 REGRESSION COVERAGE. The same asymmetry, one rule over.

    A client or repo name referenced in a different case than the store spells
    it walked straight through — and a scope name in a skill description is
    exactly where the prose capitalises things.
    """
    out = _san().text(f"the {FAKE_SCOPES[0].title()} rollout and {FAKE_SCOPES[2].upper()}")
    assert FAKE_SCOPES[0].title() not in out and FAKE_SCOPES[2].upper() not in out
    assert "scope-" in out


def test_a_name_inside_a_hyphenated_token_is_not_invisible():
    """🔴 REGRESSION COVERAGE. The word boundary was `(?![\\w-])`.

    Treating a hyphen as part of the word made a name the module had been SHOWN
    invisible in the middle of a hyphenated token: a systemd unit named after a
    client, a git-remote org, a pytest temp directory. The name was known, the
    rule was present, and it did not fire.
    """
    unit = _san().text(f"{FAKE_SCOPES[2]}-sync.timer fires 4x/day")
    assert FAKE_SCOPES[2] not in unit, unit

    org = _san().text(f"git@example.test:{FAKE_SCOPES[0]}-corp/thing.git")
    assert FAKE_SCOPES[0] not in org, org

    tmpdir = _san().text("under /tmp/pytest-of-jrandom-pytest-0/x")
    assert "jrandom" not in tmpdir, tmpdir


def test_a_project_slug_with_an_UPPERCASE_component_is_fully_substituted():
    """🔴 REGRESSION COVERAGE — the slug fix leaked in the SAME SHAPE it fixed.

    The character class was `[a-z0-9._-]`, lowercase-only, so an uppercase path
    component TRUNCATED the match: the deepest — most identifying — component
    survived, sitting immediately after a stand-in that made the whole token
    read as sanitized. Measured shape, from a slug that exists on the machine
    this page is built on: `…-workspace-learn-<x>-server-Server` rendered as
    `project-01Server`.
    """
    out = _san().text("-home-jrandom-workspace-learn-borealis-server-Server: 9 B")
    assert "borealis" not in out and "Server" not in out, out
    assert re.search(r"project-\d+", out), out


@pytest.mark.parametrize("slug", [
    "-tmp-wt-borealis-684",
    "-tmp-nix-shell-147956-1687608884-pytest-of-jrandom-pytest-0-store-acme-widgets",
    "-var-lib-borealis-checkout",
])
def test_a_project_slug_rooted_OUTSIDE_the_home_directory_is_substituted(slug):
    """🔴 REGRESSION COVERAGE. `-home-` was never the class; it was one root.

    The rule matched `-home-…` only, so every project rooted elsewhere — a
    worktree under `/tmp`, a pytest temp dir carrying `pytest-of-<user>` — passed
    through verbatim while the page announced itself sanitized. Five such slugs
    existed on the machine that built the page when this was measured.
    """
    out = _san().text(f"{slug} holds 12,750 B")
    assert "borealis" not in out and "acme-widgets" not in out and "jrandom" not in out, out
    assert re.search(r"project-\d+", out), out


def test_ordinary_hyphenated_prose_is_not_eaten_by_the_slug_rule():
    """CONTROL for the rule above — widening a pattern is how prose gets eaten.

    `run` is deliberately absent from the slug roots because `-run-tests…` is
    ordinary text in this repo. A rule that traded a leak for a corruption would
    have made the page worse, not better.
    """
    for text in ("bash scripts/run-tests.sh --tier both",
                 "pass --no-systemd --no-network to skip it",
                 "a well-tested-and-documented helper"):
        assert _san().text(text) == text, text


def test_a_bare_hostname_that_reads_as_a_WORD_is_declined_and_counted():
    """🔴 THE DEGRADATION, MADE LOUD rather than silent.

    A dotless hostname is structurally identical to an ordinary word, so the
    module substitutes only names it was handed AND that look like machine names
    (they carry a hyphen or a digit). A generic nodename — `nixos` is the real
    one on both of this repo's hosts — is DECLINED, because rewriting it would
    turn every occurrence of that word on the page into a fake hostname: the
    exact corruption `HOST_TLDS` exists to stop, arriving through another door.

    The point of the test is the second half: the decline is COUNTED, surfaces
    in the page's legend, and is printed by the generator. A hole nobody is told
    about is indistinguishable from no hole.
    """
    s = _san(hostnames=("nixos", FAKE_NODE))
    out = s.text(f"this machine runs nixos; {FAKE_NODE} is the other one")
    assert "nixos" in out, "a word-shaped nodename must not be rewritten"
    assert FAKE_NODE not in out, out
    kinds = dict(s.legend())
    assert any("indistinguishable" in k for k in kinds), kinds
    assert any("indistinguishable" in w for w in s.warnings()), s.warnings()


def test_a_short_scope_is_matched_in_its_exact_form_only_and_says_so():
    """🔴 THE LADDER, AND WHY IT EXISTS: the fix for the leak made a corruption.

    Going case-insensitive and hyphen-blind on EVERY scope rewrote the English
    word "CLI" — an index-store scope really is named `cli` — into a stand-in,
    in prose, on the shareable page. Short scope names are acronyms and acronyms
    are words. So a short name is substituted only where it appears exactly, and
    that weaker treatment is recorded rather than assumed.
    """
    s = _san(scopes=("cli",))
    out = s.text("the headless CLI agent, scope cli, and signal-cli-rest-api")
    assert "CLI agent" in out, f"an English acronym was corrupted: {out!r}"
    assert "signal-cli-rest-api" in out, f"a hyphenated project name was eaten: {out!r}"
    assert "scope-01" in out, f"the exact form was not substituted: {out!r}"
    assert any("exact-form" in k for k in dict(s.legend())), s.legend()


def test_an_email_address_loses_both_halves():
    """INVARIANT GUARD on the composite case a page about mail automation hits.

    Neither half is a new class — a username and a host — but they arrive glued
    together, and a rule that only fired on whitespace-delimited tokens would
    leave one of them.
    """
    out = _san().text("send it AS jrandom@console.borealis-corp.com today")
    assert "jrandom" not in out and "borealis-corp.com" not in out, out


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


# --------------------------------------------------------------------------- #
# Prose is WITHHELD, not scrubbed — the class substitution cannot reach
# --------------------------------------------------------------------------- #


def test_a_declared_PROSE_cell_is_withheld_whole_not_scrubbed():
    """REGRESSION GUARD — red before `column_kinds` existed.

    The shipped bug: a measurer harvested a human sentence out of a file, the
    sentence named a third party no identifier list contained, and it rode into
    a page stamped SANITIZED. Substituting harder cannot fix this — the name is
    not in any list to substitute — so the cell has to go.
    """
    ms = measure.MeasurementSet()
    ms.items.append(_row("skills.inventory",
                         columns=("skill", "what it is"),
                         column_kinds=("name", "prose"),
                         rows=(("/quartzsight", FAKE_PROSE),)))
    out = sanitize.apply(ms, _san())
    cell = out.by_key("skills.inventory").rows[0][1]
    assert "Northwind" not in cell, f"harvested prose survived: {cell!r}"
    assert cell == sanitize.WITHHELD, f"prose was scrubbed, not withheld: {cell!r}"


def test_withholding_says_WITHHELD_and_never_leaves_a_BLANK():
    """A blank cell cannot be told from a skill that has no description.

    Same silent-zero failure the UNMEASURED state exists to prevent, arriving
    one layer down.
    """
    ms = measure.MeasurementSet()
    ms.items.append(_row("k", columns=("c",), column_kinds=("prose",),
                         rows=((FAKE_PROSE,),)))
    cell = sanitize.apply(ms, _san()).by_key("k").rows[0][0]
    assert cell.strip(), "a withheld cell rendered as whitespace"
    assert "WITHHELD" in cell


def test_withholding_still_happens_when_EVERY_name_source_is_empty():
    """FAIL-CLOSED GUARD, and the one that separates this fix from the old one.

    A name-list fix is only as good as the list. Withholding is driven by the
    DECLARATION, so a host with no index store and no skill inventory — where
    every name list is empty and the old approach degraded to doing nothing —
    still removes the prose.
    """
    bare = sanitize.Sanitizer(enabled=True, home="", user="", scopes=(),
                              hostnames=(), local_names=())
    ms = measure.MeasurementSet()
    ms.items.append(_row("k", columns=("c",), column_kinds=("prose",),
                         rows=((FAKE_PROSE,),)))
    cell = sanitize.apply(ms, bare).by_key("k").rows[0][0]
    assert cell == sanitize.WITHHELD, f"empty name sources reopened the hole: {cell!r}"


def test_an_UNKNOWN_column_kind_is_withheld_and_not_waved_through():
    """FAIL-CLOSED GUARD on the declaration itself.

    A typo, or a kind a newer measurer knows and this module does not, must
    lose the text rather than publish it. The ordinary path is spelled `""` and
    has to be spelled — silence is not the safe default here.
    """
    ms = measure.MeasurementSet()
    ms.items.append(_row("k", columns=("c",), column_kinds=("prosee",),
                         rows=((FAKE_PROSE,),)))
    cell = sanitize.apply(ms, _san()).by_key("k").rows[0][0]
    assert cell == sanitize.WITHHELD, f"an unknown kind was treated as ordinary: {cell!r}"


def test_a_NAME_cell_substitutes_every_local_identifier_including_short_ones():
    """A `name` cell is a structured slot, so the length ladder does not apply.

    `bar` is 3 characters and `text()` would decline it — correctly, since it
    is an English word. Here it is unambiguously an identifier.
    """
    ms = measure.MeasurementSet()
    ms.items.append(_row("k", columns=("skill", "path"),
                         column_kinds=("name", "name"),
                         rows=(("/bar", "claude/skills/bar/SKILL.md"),
                               ("/quartzsight", "claude/skills/quartzsight/SKILL.md"))))
    rows = sanitize.apply(ms, _san()).by_key("k").rows
    blob = repr(rows)
    for real in ("bar", "quartzsight"):
        assert f"/{real}" not in blob, f"{real!r} survived a name cell: {blob!r}"
    assert "claude/skills/" in blob, "the path SHAPE was destroyed, not just the name"


def test_the_same_name_gets_the_same_stand_in_in_both_of_its_cells():
    """Otherwise the row stops being readable as one row.

    A skill whose name and path disagreed would read as two different skills,
    which is a worse artefact than the one this replaced.
    """
    ms = measure.MeasurementSet()
    ms.items.append(_row("k", columns=("skill", "path"),
                         column_kinds=("name", "name"),
                         rows=(("/quartzsight", "claude/skills/quartzsight/SKILL.md"),)))
    name_cell, path_cell = sanitize.apply(ms, _san()).by_key("k").rows[0]
    stand = name_cell.lstrip("/")
    assert stand.startswith("name-"), f"unexpected stand-in {name_cell!r}"
    assert f"claude/skills/{stand}/SKILL.md" == path_cell, \
        f"name and path disagree: {name_cell!r} vs {path_cell!r}"


def test_a_local_name_that_PREFIXES_another_is_not_eaten():
    """🔴 REGRESSION GUARD on `_names`' longest-first ordering.

    `_word` treats a hyphen as a boundary, so a short name is reachable INSIDE a
    longer hyphenated one. Shortest-first therefore rewrites the inner name and
    leaves the outer one HALF-REAL — which reads as sanitized and is not.

    Measured before this test existed: flipping `reverse=True` to `reverse=False`
    kept all 75 tests green and published a real client name in cleartext on a
    page stamped SANITIZED. The sibling guard for `_scopes` (see
    `test_a_scope_name_that_prefixes_another_is_not_eaten`) had existed all
    along; the parallel one for `_names` had not, and the fixture carried no
    pair that could have seen it.
    """
    ms = measure.MeasurementSet()
    ms.items.append(_row("k", columns=("skill",), column_kinds=("name",),
                         rows=(("/pelagic-mailbox",), ("/mailbox",))))
    rows = sanitize.apply(ms, _san()).by_key("k").rows
    blob = repr(rows)
    assert "mailbox" not in blob, f"a name survived, whole or in part: {blob!r}"
    stand_ins = re.findall(r"name-\d+", blob)
    assert len(set(stand_ins)) == 2, f"the two distinct names collapsed: {blob!r}"


def test_a_STATIC_LABEL_in_a_name_cell_is_not_corrupted():
    """The `name` kind drops the length ladder, so prove it cannot eat prose.

    🔴 A `name` cell is NOT a pure identifier slot — `skills.listing`'s `what`
    column is declared `name` and holds static English labels beside
    `costliest tier-A entry: <skill>`. The licence for dropping the ladder is
    CONFINEMENT plus `_word`'s boundaries, not purity.

    🔴 EVERY LABEL BELOW CONTAINS A FIXTURE NAME AS A SUBSTRING, AND THAT IS
    THE ENTIRE POINT. The first version asserted on four real labels from
    `m_skill_listing` in which no fixture name appeared at all — so it compared
    two constants and could not fail under ANY mutation of `_names`, `_word` or
    the sort order, while its docstring claimed it proved corruption impossible.
    A guard that reads as coverage and provides none is worse than none, because
    it stops anyone looking. What actually defends these is `_word`'s
    non-word-character boundaries, so the fixtures must exercise exactly that.
    """
    #: name -> a label embedding it WITHOUT a word boundary, so only `_word`
    #: (never a bare `str.replace`) leaves it intact.
    labels = (
        "the run resumed after the gate went green",     # `resume` inside `resumed`
        "pinned to the toolbar, not the status area",    # `bar` inside `toolbar`
        "mailboxes are counted, not read",               # `mailbox` inside `mailboxes`
        "quartzsighted about the ceiling",               # `quartzsight` inside a longer word
        "pelagic-mailboxes archive nightly",             # the whole hyphenated name, extended
    )
    # 🔴 CHECKED AGAINST `FAKE_NAMES` ITSELF, NOT A THIRD HARDCODED TUPLE. The
    # first version listed the names again here, so it could not see the drift
    # it names: deleting one from `FAKE_NAMES` left this test green, and only a
    # sibling caught it. Every name the sanitizer is given must be exercised by
    # some label, or this guard is partly vacuous again.
    unexercised = [n for n in FAKE_NAMES if not any(n in label for label in labels)]
    assert not unexercised, (
        f"fixture drifted: {unexercised} are in FAKE_NAMES but appear in no label, "
        "so this guard does not cover them")

    ms = measure.MeasurementSet()
    ms.items.append(_row("k", columns=("what",), column_kinds=("name",),
                         rows=tuple((label,) for label in labels)))
    out = sanitize.apply(ms, _san()).by_key("k").rows
    for label, (got,) in zip(labels, out):
        assert got == label, f"a static label was corrupted: {label!r} -> {got!r}"

    # CONTROL: the same cell kind DOES substitute a name that stands alone, so
    # the assertions above cannot be passing because `_names` is inert.
    ctl = measure.MeasurementSet()
    ctl.items.append(_row("c", columns=("what",), column_kinds=("name",),
                          rows=(("costliest tier-A entry: quartzsight",),)))
    got = sanitize.apply(ctl, _san()).by_key("c").rows[0][0]
    assert "quartzsight" not in got, f"the name class is inert: {got!r}"
    assert got.startswith("costliest tier-A entry: "), \
        f"the static half of the label was eaten too: {got!r}"


def test_the_NAME_column_is_found_by_its_KIND_not_by_its_POSITION(tmp_path):
    """🔴 REGRESSION GUARD. `build()` used to read `row[0]` and assume.

    Measured: swapping the first two cells of the inventory row, WITHOUT
    touching `column_kinds`, kept 102 tests green, kept both DEGRADED lines
    byte-identical, kept the masthead reading SANITIZED — and republished every
    real skill name, because the name list had become the tier letters.

    A column reorder is a one-line refactor nobody would think to re-audit, so
    the declaration has to be what selects the column.
    """
    env = measure.Env(repo=tmp_path, home=tmp_path / "h", claude_dir=tmp_path / "c",
                      index_store=tmp_path / "s", allow_systemd=False,
                      allow_network=False)
    ms = measure.MeasurementSet()
    ms.items.append(_row("index.store", columns=("scope",), rows=(("borealis",),)))
    # the name column is SECOND here, exactly as a reorder would leave it
    ms.items.append(_row("skills.inventory", columns=("tier", "skill"),
                         column_kinds=("", "name"),
                         rows=(("A", "/quartzsight"), ("B", "/bar"))))
    san = sanitize.build(True, env, ms)
    assert san.local_names == ("bar", "quartzsight"), (
        f"the name column was read positionally: {san.local_names!r}")
    assert not san.degraded, f"a well-formed build degraded: {san.degraded!r}"


def test_a_SHORT_inventory_row_is_skipped_rather_than_raising(tmp_path):
    """The `len(row) > idx` bound in `build()`, which nothing else reaches.

    A row shorter than the declared name column is a measurer bug, but it must
    not take the whole build down — and `>=` there turns the skip into an
    IndexError. No live measurer emits one, so without this the branch is
    unexercised and the off-by-one is invisible.
    """
    env = measure.Env(repo=tmp_path, home=tmp_path / "h", claude_dir=tmp_path / "c",
                      index_store=tmp_path / "s", allow_systemd=False,
                      allow_network=False)
    ms = measure.MeasurementSet()
    ms.items.append(_row("index.store", columns=("scope",), rows=(("borealis",),)))
    ms.items.append(_row("skills.inventory", columns=("tier", "skill"),
                         column_kinds=("", "name"),
                         rows=(("A",), ("B", "/quartzsight"))))
    san = sanitize.build(True, env, ms)
    assert san.local_names == ("quartzsight",), (
        f"a short row was not skipped cleanly: {san.local_names!r}")


def test_an_inventory_with_NO_name_column_degrades_LOUDLY(tmp_path):
    """CONTROL for the above: the selector must be able to come back empty.

    Without this, `test_..._by_its_KIND_not_by_its_POSITION` cannot tell a
    working selector from one that silently falls back to position 0.
    """
    env = measure.Env(repo=tmp_path, home=tmp_path / "h", claude_dir=tmp_path / "c",
                      index_store=tmp_path / "s", allow_systemd=False,
                      allow_network=False)
    ms = measure.MeasurementSet()
    ms.items.append(_row("index.store", columns=("scope",), rows=(("borealis",),)))
    ms.items.append(_row("skills.inventory", columns=("tier", "skill"),
                         rows=(("A", "/quartzsight"),)))
    san = sanitize.build(True, env, ms)
    assert san.local_names == ()
    assert any("no `name` column" in r.lower() or "NO `name` column" in r
               for r in san.degraded), (
        f"an inventory with no name column degraded silently: {san.degraded!r}")


def test_a_RAGGED_row_withholds_the_cells_past_its_DECLARATION():
    """🔴 FAIL-CLOSED GUARD. A position past the end used to read as ordinary.

    Demonstrated: a measurement declaring `("name", "prose")` with a three-cell
    row emitted the third cell VERBATIM into a sanitized page. A declaration
    that does not reach a cell is where this module knows LEAST about it.
    """
    ms = measure.MeasurementSet()
    ms.items.append(_row("k", columns=("skill", "what it is"),
                         column_kinds=("name", "prose"),
                         rows=(("/quartzsight", FAKE_PROSE, FAKE_PROSE),)))
    row = sanitize.apply(ms, _san()).by_key("k").rows[0]
    assert "Northwind" not in repr(row), f"a ragged cell was published: {row!r}"
    assert row[2] == sanitize.WITHHELD


def test_a_local_name_that_is_an_ENGLISH_WORD_is_not_rewritten_OUTSIDE_its_cell():
    """CORRUPTION CONTROL — the failure that killed the name-list approach.

    Sourcing names widely and substituting them everywhere rewrote `test`,
    `fast` and `scratch` as they occurred in ordinary English. Confining the
    class to a declared cell is what makes substituting ALL names safe, so this
    is the assertion that licenses the aggressive half of the fix.
    """
    prose = "resume the run from the status bar and read the bar chart"
    ms = measure.MeasurementSet()
    ms.items.append(_row("k", detail=prose,
                         columns=("ordinary",), column_kinds=("",),
                         rows=((prose,),)))
    out = sanitize.apply(ms, _san()).by_key("k")
    assert out.rows[0][0] == prose, f"an ordinary cell was name-substituted: {out.rows[0][0]!r}"
    assert out.detail == prose, f"`detail` was name-substituted: {out.detail!r}"


def test_a_row_with_NO_declared_kinds_behaves_exactly_as_before():
    """BACKWARD-COMPATIBILITY GUARD. An empty `column_kinds` is not `prose`.

    Twenty-one registered rows declare nothing. If the default drifted to
    withholding, the page would empty itself and the tests that count rows
    would still pass.
    """
    ms = measure.MeasurementSet()
    ms.items.append(_row("k", columns=("c",), rows=((f"at {FAKE_HOME}/x",),)))
    cell = sanitize.apply(ms, _san()).by_key("k").rows[0][0]
    assert "WITHHELD" not in cell, "an undeclared column was withheld"
    assert FAKE_HOME not in cell, "an undeclared column stopped being sanitized"


def test_the_legend_counts_withheld_cells_and_warnings_does_NOT_call_it_a_hole():
    """Withholding is the redaction WORKING; a declined substitution is not.

    Filing them together would make a clean build print degradation warnings
    forever, which is how a warning stops being read.
    """
    ms = measure.MeasurementSet()
    ms.items.append(_row("k", columns=("c",), column_kinds=("prose",),
                         rows=((FAKE_PROSE,), (FAKE_PROSE,))))
    san = _san()
    sanitize.apply(ms, san)
    assert san.withheld == 2
    assert dict(san.legend())["prose-withheld"] == 2
    assert not [w for w in san.warnings() if "withheld" in w.lower()], \
        f"withholding was reported as a degradation: {san.warnings()!r}"


def test_the_disabled_sanitizer_withholds_NOTHING():
    """The private build must be the identity transform on prose too."""
    ms = measure.MeasurementSet()
    ms.items.append(_row("k", columns=("c",), column_kinds=("prose",),
                         rows=((FAKE_PROSE,),)))
    out = sanitize.apply(ms, _san(enabled=False))
    assert out.by_key("k").rows[0][0] == FAKE_PROSE


# --------------------------------------------------------------------------- #
# The declaration ledger — pinned two-way
# --------------------------------------------------------------------------- #

#: Every registry key that declares a non-ordinary column, and what it declares.
#:
#: 🔴 PINNED TWO-WAY ON PURPOSE. A measurer that starts harvesting prose without
#: declaring it is EXACTLY the leak `column_kinds` was added to stop, and it is
#: invisible in review — the diff is one more `rows.append`. So a key that gains
#: a declaration, loses one, or changes one fails this test until someone edits
#: the ledger and, in doing so, states what the new column holds.
#:
#: Adding a key here is not a formality. Ask: can a cell in that column contain
#: a sentence somebody WROTE? If yes it is `prose` — no matter how safe today's
#: contents look, because the contents change without a commit here.
#: 🔴 WHAT THIS LEDGER CANNOT SEE. It compares DECLARATIONS to declarations. A
#: brand-new column that harvests prose and declares NOTHING produces no entry
#: here, so the comparison still holds and the suite stays green. It catches a
#: declaration that is deleted, truncated, mistyped or demoted — the likelier
#: regression now the field exists — and nothing more. An audit found two
#: undeclared prose columns (`gate.tiers`, `drift.ladder`) while this test was
#: green, which is the worked proof of that limit.
PROSE_LEDGER = {
    "skills.listing": ("name", ""),
    "skills.inventory": ("name", "", "prose", "name"),
    # 🔴 `gate.tiers` is deliberately ABSENT: every cell in it is a literal
    # authored in `measure.py`. Its harvested half lives in `gate.exit_codes`,
    # split out precisely so the safe half stops paying for the unsafe one.
    "gate.exit_codes": ("", "prose"),
    "drift.ladder": ("", "prose"),
}


def _ledger_env(tmp_path):
    """The REAL repo — the ledger is a claim about the real measurers.

    Home, claude dir and index store point at `tmp_path` so nothing reads the
    operator's machine, and `allow_network=False` keeps `m_branch_protection`
    from shelling `gh` (see `Env.allow_network`).
    """
    return measure.Env(repo=REPO_ROOT, home=tmp_path,
                       claude_dir=tmp_path / ".claude",
                       index_store=tmp_path / "index-store",
                       allow_systemd=False, allow_network=False)


def _declared_kinds(env) -> dict:
    out = {}
    for item in measure.take(env):
        if item.column_kinds:
            out[item.key] = tuple(item.column_kinds)
    return out


def test_the_column_kind_ledger_is_pinned_two_way(tmp_path):
    """SEAM GUARD over the whole registry, not over one measurer.

    Runs the real registry and compares the declarations it PRODUCES against
    the ledger. Both directions matter: a new prose column that nobody declared
    never appears here (so the ledger is checked against reality, not against
    itself), and a declaration deleted from a measurer fails rather than
    silently reopening the hole.
    """
    env = _ledger_env(tmp_path)
    live = _declared_kinds(env)
    assert live == PROSE_LEDGER, (
        "the declared column kinds moved.\n"
        f"  registry says: {live}\n"
        f"  ledger says:   {PROSE_LEDGER}\n"
        "If a measurer now harvests human-written text out of a file, declare "
        "that column `prose` and add it here. If a column stopped being "
        "harvested, remove it here in the same commit."
    )


def test_the_inventory_NAME_COLUMN_really_holds_the_skill_names(tmp_path):
    """🔴 SEAM GUARD pinning a RELATIONSHIP: rows vs the columns describing them.

    `sanitize.build()` sources every local identifier from the column
    `skills.inventory` declares `name`. Selecting that column BY ITS KIND (which
    it now does) is necessary and not sufficient: if the measurer's row tuple is
    reordered while `columns`/`column_kinds` stay put, the declaration points at
    the wrong cell and the data itself lies. No declaration can catch that —
    only checking the cells against the source can.

    Measured: reordering the row tuple alone kept 102 tests green, kept both
    DEGRADED lines byte-identical, kept the masthead reading SANITIZED, and
    republished every real skill name — because the name list had silently
    become the set of tier letters {"A", "B", "?"}.

    Read through `skill_tiers`, the measurer's OWN source, so this compares the
    rendered table against the thing it claims to render rather than against
    itself.

    🔴 IT PINS EVERY DECLARED CELL, NOT JUST THE ONE `build()` READS. The first
    version checked only the first `name` column it found. `skills.inventory`
    declares TWO (`skill` and `path`), and swapping the description into the
    OTHER one survived all 145 present tests and republished every harvested
    description on a page stamped SANITIZED — the same leak class, one column
    over. A guard scoped to one cell of a row cannot pin a claim about the row.
    """
    env = _ledger_env(tmp_path)
    inv = measure.take(env).by_key("skills.inventory")
    assert inv is not None and inv.measured, (
        f"the inventory did not measure, so this guard checked NOTHING: "
        f"{inv.reason if inv else 'row absent'}")

    name_cols = [i for i in range(len(inv.columns)) if inv.kind_of(i) == "name"]
    assert name_cols, "skills.inventory declares no `name` column"

    sys.path.insert(0, str(SCRIPTS / "lib"))
    try:
        import skill_tiers  # noqa: PLC0415
        shipped = skill_tiers.shipped_skills()
    finally:
        if sys.path and sys.path[0] == str(SCRIPTS / "lib"):
            sys.path.pop(0)
    assert shipped, "shipped_skills() returned nothing — this guard checked NOTHING"

    # The WHOLE declared shape, read back against the measurer's own source:
    # every `name` cell is the skill name (bare or inside its path), and the
    # `prose` cell is the description — so no pair of cells can be swapped
    # without this failing.
    expected = set()
    for name, (rel, desc) in shipped.items():
        first = desc.split(". ")[0].strip()
        if len(first) > 150:
            first = first[:147].rstrip() + "..."
        expected.add((f"/{name}", first, rel))
    got = {(row[0], row[2], row[3]) for row in inv.rows}
    assert got == expected, (
        "the inventory's cells do not match what `skill_tiers` says they are.\n"
        f"  declared `name` columns: {name_cols}\n"
        f"  a row reads: {sorted(got)[:1]}\n"
        f"  source says: {sorted(expected)[:1]}\n"
        "This pins POSITIONS 0/2/3 against the source, so moving `columns` and "
        "`column_kinds` alongside a row reorder will NOT satisfy it — update the "
        "indices here in the same commit, deliberately. Until they agree, "
        "sanitize.build() harvests the wrong cell or a declared `name` cell "
        "carries prose, and either way real values ship."
    )


def test_the_gate_exit_code_legend_matches_gate_sh(tmp_path):
    """🔴 REGRESSION GUARD. The parser silently dropped `gate.sh`'s SUCCESS code.

    `gate.sh` writes its legend as a hanging indent — `# Exit: 0 = …` first,
    `#       1 = …` after — so a pattern anchored on `^#\\s+(\\d+)` matches every
    code EXCEPT 0. The row then rendered "3 exit codes documented in gate.sh's
    header" for a header documenting four, and the filter's own `"0"` entry was
    dead code that made the intent look satisfied.

    It shipped green because NOTHING in this suite pinned any `value` string:
    hardcoding the count to the right number was measured to pass. So this reads
    the codes out of `gate.sh` INDEPENDENTLY — a different pattern, deliberately
    — and compares. It is the same defect `m_drift_ladder` already carries a 🔴
    note about, reintroduced by a new parser 370 lines away, which is why it is
    pinned rather than merely commented.
    """
    env = _ledger_env(tmp_path)
    row = measure.take(env).by_key("gate.exit_codes")
    assert row is not None and row.measured, (
        f"gate.exit_codes did not measure, so this guard checked NOTHING: "
        f"{row.reason if row else 'row absent'}")

    gate = (REPO_ROOT / "scripts" / "gate.sh").read_text(encoding="utf-8")
    # Independent read: every commented `<digits> = <text>` in the header block,
    # without assuming where on the line the digits sit.
    expected = {m.group(1) for m in re.finditer(r"^#[^\n]*?\b(\d+)\s*=\s*\S", gate, re.M)}
    assert expected, "no exit codes found in gate.sh at all — the control is broken"

    got = {code for code, _ in row.rows}
    assert got == expected, (
        f"the rendered legend does not match gate.sh's header.\n"
        f"  rendered: {sorted(got, key=int)}\n"
        f"  gate.sh:  {sorted(expected, key=int)}\n"
        "A code documented in the script and missing from the page sends a "
        "reader looking for their exit status in a set that does not contain it."
    )
    assert row.value.startswith(f"{len(expected)} "), (
        f"the count in the value label disagrees with the rows: {row.value!r}")


def test_every_ledger_KEY_is_a_real_registry_key():
    """The other direction: a ledger entry naming nothing pins nothing."""
    keys = {entry[0] for entry in measure.REGISTRY}
    unknown = set(PROSE_LEDGER) - keys
    assert not unknown, f"the ledger names keys the registry does not have: {unknown}"


def test_every_declaration_is_as_long_as_its_own_columns(tmp_path):
    """A short `column_kinds` silently leaves the tail columns ordinary.

    That is the shape where a declaration READS as coverage and provides none —
    the prose column sits past the end of the tuple and is waved through.
    """
    env = _ledger_env(tmp_path)
    for item in measure.take(env):
        if not item.column_kinds:
            continue
        assert len(item.column_kinds) == len(item.columns), (
            f"{item.key}: {len(item.column_kinds)} kinds for "
            f"{len(item.columns)} columns — the tail is undeclared"
        )


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


def test_scope_substitution_degrading_to_ZERO_is_LOUD(tmp_path):
    """🔴 REGRESSION COVERAGE for the silent zero this module argues against.

    `build()` fills the scope list only when the `index.store` row MEASURED. On a
    host with no store — or with the row absent, or measuring empty — there are
    no scope names to swap, every repo and client name in a skill description
    passes through, and the masthead still reads `mode SANITIZED`. That is a
    `--sanitize` that did almost nothing wearing the badge of one that worked.

    Each of the three ways it can degrade must produce a reason, and the reason
    must reach BOTH the operator (`warnings()`, printed by the generator) and
    the reader of the page (`legend()`).
    """
    env = measure.Env(repo=tmp_path, home=tmp_path / "h", claude_dir=tmp_path / "c",
                      index_store=tmp_path / "s", allow_systemd=False,
                      allow_network=False)

    unmeasured = measure.MeasurementSet()
    unmeasured.items.append(measure.Measurement(
        key="index.store", label="L", section="soft", status=measure.UNMEASURED,
        asof="t", source="(not reached)", reason="the store does not exist",
        settle="ls"))
    empty = measure.MeasurementSet()
    empty.items.append(_row("index.store", columns=("scope",), rows=()))
    absent = measure.MeasurementSet()

    for label, ms in (("unmeasured", unmeasured), ("empty", empty),
                      ("no row", absent), ("no measurements", None)):
        san = sanitize.build(True, env, ms)
        assert san.degraded, f"{label}: degraded silently"
        assert san.warnings(), f"{label}: nothing would be printed"
        assert any("NOT-SUBSTITUTED" in k for k, _ in san.legend()), (
            f"{label}: the page's own legend would not show it: {san.legend()}")
        # 🔴 It must degrade for the SCOPE reason specifically. `build()` now has
        # a second name source (the skill inventory) whose absence ALSO degrades,
        # and a bare `san.degraded` would go green on that one alone — this test
        # would then pass with scope degradation deleted entirely.
        assert any("scope" in reason for reason in san.degraded), (
            f"{label}: degraded, but not about scopes: {san.degraded!r}")

    # CONTROL: with BOTH name sources measured, nothing degrades — so the flag
    # above cannot be a constant.
    fine = measure.MeasurementSet()
    fine.items.append(_row("index.store", columns=("scope",), rows=(("borealis",),)))
    fine.items.append(_row("skills.inventory", columns=("skill",),
                           column_kinds=("name",), rows=(("/pelagic-mail",),)))
    ok = sanitize.build(True, env, fine)
    assert not ok.degraded, f"a fully-measured build degraded: {ok.degraded!r}"
    assert ok.scopes == ("borealis",)
    assert ok.local_names == ("pelagic-mail",), (
        f"the name source did not load: {ok.local_names!r}")
    assert not any("NOT-SUBSTITUTED" in k for k, _ in ok.legend())


def test_the_NAME_source_degrading_is_LOUD_but_does_NOT_reopen_the_prose_hole(tmp_path):
    """The two halves of the fix must fail INDEPENDENTLY.

    A missing skill inventory means identifiers around the prose keep their real
    names — worth a warning. It must NOT mean the prose comes back, because the
    withholding is driven by the DECLARATION, not by any name list. Coupling
    them would rebuild the fail-open behaviour this change removed, one layer up.
    """
    env = measure.Env(repo=tmp_path, home=tmp_path / "h", claude_dir=tmp_path / "c",
                      index_store=tmp_path / "s", allow_systemd=False,
                      allow_network=False)
    ms = measure.MeasurementSet()
    ms.items.append(_row("index.store", columns=("scope",), rows=(("borealis",),)))
    san = sanitize.build(True, env, ms)

    assert any("skills.inventory" in r for r in san.degraded), (
        f"a missing name source degraded silently: {san.degraded!r}")
    assert san.local_names == ()

    page = measure.MeasurementSet()
    page.items.append(_row("k", columns=("c",), column_kinds=("prose",),
                           rows=((FAKE_PROSE,),)))
    cell = sanitize.apply(page, san).by_key("k").rows[0][0]
    assert cell == sanitize.WITHHELD, (
        f"a degraded name source reopened the prose hole: {cell!r}")


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

#: RFC 5737 documentation, RFC 2544 benchmarking, RFC 6598 CGNAT. `ipaddress`
#: knows every other non-routable class; these three it reports as global.
_NOT_ROUTABLE_V4 = tuple(ipaddress.IPv4Network(n) for n in (
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24",
    "198.18.0.0/15", "100.64.0.0/10",
))
#: RFC 3849 — the v6 documentation prefix, and what the sanitizer substitutes into.
_DOC_V6 = ipaddress.IPv6Network("2001:db8::/32")


def _is_routable_v4(literal: str) -> bool:
    """🔴 CLASSIFY BY RANGE, NEVER BY FIRST OCTET.

    The predecessor of this helper allowlisted whole `/8`s by their leading
    octet — `172`, `198`, `203`, `169` among them — as if those were reserved.
    They are not: only `172.16.0.0/12` is private, only `198.51.100.0/24` and
    `203.0.113.0/24` are documentation, only `169.254.0.0/16` is link-local. An
    address one step outside any of those still carries the same first octet and
    is ordinarily routable, so this guard would have waved it into a PUBLIC repo
    — the exact class it exists to stop, and a guard that reads as coverage
    while providing none is worse than no guard, because it stops anyone looking.
    """
    try:
        ip = ipaddress.IPv4Address(literal)
    except ValueError:
        return False
    if any(ip in net for net in _NOT_ROUTABLE_V4):
        return False
    # `is_global` is True for multicast and for some reserved space, so it is a
    # necessary condition and not a sufficient one. Name the rest.
    if (ip.is_multicast or ip.is_reserved or ip.is_loopback
            or ip.is_link_local or ip.is_unspecified):
        return False
    return ip.is_global


def _is_routable_v6(literal: str) -> bool:
    """The v6 half of the same question, behind the same door.

    A helper rather than an inline loop so the scan below and its control read
    the SAME predicate — a control built out of a second spelling of the rule
    proves nothing about the rule that runs.
    """
    if ":" not in literal:
        return False
    try:
        ip = ipaddress.IPv6Address(literal)
    except ValueError:
        return False
    return ip not in _DOC_V6 and ip.is_global


def _just_outside(cidr: str) -> str:
    """The address one step past a reserved range — same first octet, routable.

    🔴 DERIVED, NEVER TYPED, and that is a REPO RULE not a stylistic choice.
    This test's first draft hardcoded four real routable literals as its control
    and was caught by `test_no_public_ips.py` — this repo is PUBLIC and a real
    third-party address must not be committed even as a fixture. Computing the
    neighbour of a range whose literal IS allowed (private, documentation,
    link-local) gives the identical control with nothing to scrub, and states
    the property more exactly than four opaque constants did: what must be
    classified routable is precisely the address one step OUTSIDE each reserved
    block, because that is where the first-octet heuristic is wrong.
    """
    net = ipaddress.ip_network(cidr)
    return str(net.broadcast_address + 1)


def test_the_committed_ip_guard_classifies_by_RANGE_not_by_first_octet():
    """🔴 MUTATION CONTROL for the guard below — it must be able to go RED.

    Each address here shares its first octet with a range that really is
    reserved and is itself routable. Under the first-octet allowlist every one
    of them scanned clean.
    """
    for cidr in ("172.16.0.0/12",        # the only private block in 172/8
                 "198.51.100.0/24",      # the only documentation block in 198/8
                 "203.0.113.0/24",       # the only documentation block in 203/8
                 "169.254.0.0/16"):      # the only link-local block in 169/8
        neighbour = _just_outside(cidr)
        assert neighbour.split(".")[0] == cidr.split(".")[0], (
            f"{neighbour} must keep {cidr}'s first octet or it controls nothing")
        assert _is_routable_v4(neighbour), (
            f"{neighbour} sits one address outside {cidr} and is routable. "
            "Sharing a first octet with a reserved range does not make an "
            "address reserved — that was the bug.")
    for reserved in ("10.0.0.1", "127.0.0.1", "172.16.0.1", "192.168.1.1",
                     "169.254.1.1", "198.51.100.4", "203.0.113.9", "192.0.2.7",
                     "0.0.0.0", "255.255.255.255", "224.0.0.1"):
        assert not _is_routable_v4(reserved), f"{reserved} is not routable"
    # POSITIVE CONTROL for the v6 arm: it must be able to see something. A scan
    # that reports zero because it can never report anything is the silent zero.
    # Derived the same way, from the documentation prefix's own neighbour.
    outside_doc = _just_outside("2001:db8::/32")
    assert _is_routable_v6(outside_doc), outside_doc
    for ignored in ("2001:db8::1", "::1", "fd00:1234:5678::beef", "fe80::1",
                    "3fff::1", "2001:2::1", "10:30:00", "not-an-address"):
        assert not _is_routable_v6(ignored), ignored


def test_no_real_identifier_is_committed_in_the_generator():
    """🔴 THE PUBLIC-REPO GUARD, read off the committed bytes.

    The generator source is committed; the data it renders is not. This walks
    every file under `scripts/present/` and fails on a routable IP literal — v4
    OR v6 — or an absolute home path: the classes that are unambiguous from
    source alone. Scope and client names cannot be checked this way (they are
    read at run time and appear nowhere to compare against), which is precisely
    why they must never be written down here in the first place.

    🔴 THE v6 HALF WAS ADDED WITH THE v6 SUBSTITUTION CLASS. A guard that reads
    "no real address is committed" while inspecting only one address family is
    the description-wider-than-the-implementation shape, and the module now
    contains v6 literals for the first time.
    """
    offenders = []
    ipv4 = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b")
    ipv6 = re.compile(r"(?<![\w:.])[0-9A-Fa-f:]{3,45}(?![\w:.])")
    for path in sorted(PRESENT_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for m in ipv4.finditer(text):
            octets = m.groups()
            if not all(0 <= int(o) <= 255 for o in octets):
                continue                      # a version string
            if not _is_routable_v4(m.group(0)):
                continue                      # documentation / private ranges
            offenders.append(f"{path.name}: routable IPv4 literal {m.group(0)}")
        for m in ipv6.finditer(text):
            if _is_routable_v6(m.group(0)):
                offenders.append(f"{path.name}: routable IPv6 literal {m.group(0)}")
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


# The prose-restatement guard MOVED to `test_present_content.py`, where it sits
# beside the other prose gates — and was narrowed there, because this spelling
# grepped the raw `content.py` SOURCE and so read SVG geometry as prose. It was
# RED on an unrelated machine whose index store had grown to a size that
# collided with a diagram coordinate, with no change to this tree. See
# `_authored_visible_text` in that module for the full account.
