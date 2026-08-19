"""Per-site reference docs: the registry, the suffix match, and the envelope field.

WHAT IS UNDER TEST
------------------
`server.py` already extracted the bare hostname of every completed command for
telemetry (`_domain_from_result`). This feature routes that host through a
registry — `reference/sites/_index.json` — and, on a hit, adds ONE advisory
field to the result envelope:

    "site_notes": "reference/sites/civitai.com.md"

so `SKILL.md` can name the DIRECTORY once and never grow again as sites are
added.

THE THREE THINGS THAT CAN GO SILENTLY WRONG, and the tests that pin them
------------------------------------------------------------------------
1. **A substring match.** `"civitai.com" in host` is the obvious spelling and it
   is the bug: it hands our operating notes to `notcivitai.com.evil.test`. The
   match must be on LABEL BOUNDARIES. See the `test_suffix_*` block, and
   especially `test_a_host_merely_containing_the_key_does_not_match`.
2. **A miss that is not silent.** A `"site_notes": null` or `""` on every
   unregistered host would put a field on the wire for every command on the
   internet. The test asserts the key is ABSENT, not falsy — `not in`, never
   `.get(...) is None`, because the latter passes for both shapes.
3. **A registry that can break a browser op.** A doc registry is not allowed to
   take the bridge down. Every malformed shape must degrade to "no site_notes"
   AND leave the command working — so those tests drive a REAL round trip, not
   just the loader.

Plus a LEDGER (`test_index_and_files_are_the_same_set`) asserting the registry's
key set and the directory's `*.md` set are identical, failing when either GROWS
or SHRINKS.

The harness is `test_server.py`'s — same `_serve` / `FakeExtension` / `_req`
round trip every other server test uses. Deliberately not a new one.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server as S  # noqa: E402
from test_server import FakeExtension, _req, _serve, _wait_connected  # noqa: E402

SITES_DIR = Path(__file__).resolve().parent.parent / "reference" / "sites"
INDEX = SITES_DIR / "_index.json"


@pytest.fixture(autouse=True)
def _pin_sites_dir_to_this_checkout(monkeypatch):
    """Pin `_SITES_DIR` to THIS checkout's reference/sites, hermetically.

    🔴 Load-bearing, not hygiene. The production default is the stable ABSOLUTE
    repo path under `Path.home()` (server.py is deployed as a flat /nix/store
    symlink, so a `__file__`-relative lookup does not survive deployment — same
    reason the spool emitter resolves that way). Without this pin the suite
    would read the OPERATOR'S primary clone on a dev host — grading a tree
    nobody asked about, and going green or red on someone else's uncommitted
    edits — and in the nix sandbox `$HOME` is an empty temp dir, so the registry
    would parse to {} and every "does not match" assertion would pass vacuously.
    Mirrors `pinned_manifest` in test_server.py, for the same class of reason.
    """
    monkeypatch.setattr(S, "_SITES_DIR", SITES_DIR)
    monkeypatch.setattr(S, "_site_index_cache", None)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _point_at(monkeypatch, directory):
    """Repoint the server's sites dir and drop the parsed-index cache.

    The cache is keyed on the directory, so repointing is normally enough — but
    a test that REWRITES a directory it already pointed at would otherwise read
    the stale parse. Clearing is the honest thing; a test that silently reused a
    cached registry would pass without exercising the file it just wrote.
    """
    monkeypatch.setattr(S, "_SITES_DIR", Path(directory))
    monkeypatch.setattr(S, "_site_index_cache", None)


def _write_index(directory, payload, files=()):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if payload is not None:
        (directory / "_index.json").write_text(payload, encoding="utf-8")
    for name in files:
        (directory / name).write_text("# stub\n", encoding="utf-8")
    return directory


def _roundtrip(url):
    """One real /cmd round trip whose result carries `url`; returns the envelope.

    This is the seam the feature actually lives on — the loader being right is
    not the claim, the ENVELOPE being right is.
    """
    srv, _ = _serve()
    ext = FakeExtension(srv, executor=lambda c: {"text": "hi", "url": url})
    ext.start()
    try:
        assert _wait_connected(srv, want=True)
        status, body = _req(srv, "POST", "/cmd", {"op": "text"})
        assert status == 200, f"round trip failed: {status} {body}"
        assert body["ok"] is True
        return body["result"]
    finally:
        ext.stop(); srv.shutdown(); srv.server_close()


# --------------------------------------------------------------------------- #
# Instrument validation — a zero-entry registry makes every no-match test vacuous
# --------------------------------------------------------------------------- #
def test_the_real_registry_is_not_empty():
    """POSITIVE CONTROL for this whole module.

    Every "does not match" assertion below passes trivially against an EMPTY
    registry — a loader wired to nothing returns "" for `civitai.com` and for
    `notcivitai.com.evil.test` alike, and the suite goes green while measuring
    nothing. This is the test that makes the zeros mean something.
    """
    index = S._load_site_index()
    assert index, (
        f"the real registry at {INDEX} parsed to ZERO entries — every "
        "non-match assertion in this module is vacuous until this passes."
    )
    assert "civitai.com" in index


def test_the_real_index_parses_as_json():
    """Guard the guard: `_load_site_index` swallows every exception by design, so
    a syntactically broken checked-in `_index.json` would degrade to {} and be
    invisible to the loader tests. Parse it the strict way, here, once."""
    raw = json.loads(INDEX.read_text(encoding="utf-8"))
    assert isinstance(raw, dict) and isinstance(raw.get("sites"), dict)


# --------------------------------------------------------------------------- #
# Suffix matching
# --------------------------------------------------------------------------- #
def test_exact_host_matches():
    assert S._site_notes_path("civitai.com") == "reference/sites/civitai.com.md"


def test_subdomain_matches():
    """`www.civitai.com` and `civitai.com` must BOTH resolve — that is the whole
    reason the match is a suffix and not an equality."""
    assert S._site_notes_path("www.civitai.com") == "reference/sites/civitai.com.md"
    assert S._site_notes_path("a.b.civitai.com") == "reference/sites/civitai.com.md"


@pytest.mark.parametrize("host", [
    # 🔴 THE BUG THIS FILE EXISTS FOR. Every one of these CONTAINS "civitai.com"
    # and none of them is civitai.com. A naive `key in host` returns a match for
    # all five and hands an attacker-chosen host our operating notes.
    "notcivitai.com.evil.test",   # key as an infix
    "civitai.com.evil.test",      # key as a PREFIX — the classic suffix-check miss
    "notcivitai.com",             # longer LABEL ending in the key's characters
    "xcivitai.com",
    "evil-civitai.com",
])
def test_a_host_merely_containing_the_key_does_not_match(host):
    assert S._site_notes_path(host) == "", (
        f"{host!r} must NOT match the `civitai.com` entry — matching is on label "
        "boundaries (host == key, or host endswith '.'+key), never a substring."
    )


@pytest.mark.parametrize("host", [
    "CIVITAI.COM", "Www.CiviTai.CoM", "civitai.com.", "  civitai.com  ",
])
def test_host_is_normalised_before_matching(host):
    """Case, a trailing root dot, and surrounding whitespace are not identity."""
    assert S._site_notes_path(host) == "reference/sites/civitai.com.md"


@pytest.mark.parametrize("host", ["example.com", "", None, 0, [], "com"])
def test_unregistered_or_junk_host_returns_empty(host):
    assert S._site_notes_path(host) == ""


def test_longest_matching_suffix_wins(tmp_path, monkeypatch):
    """A specific entry must beat a general one regardless of dict order — so a
    future `foo.example.test` doc is reachable even though `example.test` also
    matches. Asserted in BOTH insertion orders: with a `break`-on-first-match
    loop this passes in one order and fails in the other, which is exactly the
    kind of half-right that a single-order test certifies as correct."""
    for order in (["example.test", "foo.example.test"],
                  ["foo.example.test", "example.test"]):
        d = tmp_path / ("order-" + order[0])
        _write_index(d, json.dumps({"sites": {k: k + ".md" for k in order}}))
        _point_at(monkeypatch, d)
        assert S._site_notes_path("foo.example.test") == \
            "reference/sites/foo.example.test.md"
        assert S._site_notes_path("bar.example.test") == \
            "reference/sites/example.test.md"


# --------------------------------------------------------------------------- #
# The envelope
# --------------------------------------------------------------------------- #
def test_registered_host_gets_site_notes_on_the_envelope():
    result = _roundtrip("https://www.civitai.com/models/1")
    assert result["site_notes"] == "reference/sites/civitai.com.md"


def test_unregistered_host_gets_NO_site_notes_KEY():
    """ABSENT, not empty and not null.

    `assert result.get("site_notes") is None` would pass for a literal
    `"site_notes": null` on the wire — i.e. for the exact defect this asserts
    against. `not in` is the only spelling that distinguishes them.
    """
    result = _roundtrip("https://example.com/whatever")
    assert "site_notes" not in result, (
        f"an unregistered host must add NO field; got {result.get('site_notes')!r}"
    )


def test_a_containing_host_gets_no_site_notes_on_the_wire():
    """The substring bug, asserted at the SEAM rather than on the helper — the
    helper being right does not prove the call site passes it the right host."""
    result = _roundtrip("https://notcivitai.com.evil.test/x")
    assert "site_notes" not in result


def test_existing_envelope_fields_are_untouched():
    """The field is purely additive: everything that was on the envelope before
    is still there, unchanged, on BOTH a hit and a miss."""
    hit = _roundtrip("https://civitai.com/models/1")
    miss = _roundtrip("https://example.com/models/1")
    for env in (hit, miss):
        assert env["ok"] is True
        assert env["data"]["text"] == "hi"
        assert "id" in env and "instanceId" in env
    assert set(hit) - set(miss) == {"site_notes"}, (
        "a hit must differ from a miss by EXACTLY the site_notes key — "
        f"hit={sorted(hit)} miss={sorted(miss)}"
    )


def test_a_screenshot_style_data_url_envelope_is_unaffected():
    """`_domain_from_result` returns "" for a data: URL. That must be a miss, not
    a crash and not a match on a zero-length host."""
    result = _roundtrip("data:image/png;base64,iVBORw0KGgo=")
    assert "site_notes" not in result


# --------------------------------------------------------------------------- #
# Registry resilience — a doc registry may NEVER break a browser op
# --------------------------------------------------------------------------- #
MALFORMED = {
    "missing_file": None,
    "empty_file": "",
    "trailing_comma": '{"sites": {"civitai.com": "civitai.com.md",}}',
    "truncated": '{"sites": {"civitai.com"',
    "not_json_at_all": "# this is markdown, not json\n",
    "toplevel_is_a_list": '["civitai.com"]',
    "toplevel_is_a_string": '"civitai.com"',
    "sites_key_absent": '{"other": {"civitai.com": "civitai.com.md"}}',
    "sites_is_a_list": '{"sites": ["civitai.com"]}',
    "sites_is_null": '{"sites": null}',
}


@pytest.mark.parametrize("case", sorted(MALFORMED))
def test_malformed_registry_degrades_to_no_site_notes(case, tmp_path, monkeypatch):
    _point_at(monkeypatch, _write_index(tmp_path / case, MALFORMED[case]))
    assert S._load_site_index() == {}
    assert S._site_notes_path("civitai.com") == ""


@pytest.mark.parametrize("case", sorted(MALFORMED))
def test_malformed_registry_does_not_break_a_browser_op(case, tmp_path,
                                                        monkeypatch):
    """The load-bearing half. Degrading the LOOKUP is not enough — the command
    must still complete, with its payload intact and no site_notes."""
    _point_at(monkeypatch, _write_index(tmp_path / case, MALFORMED[case]))
    result = _roundtrip("https://civitai.com/models/1")
    assert result["ok"] is True
    assert result["data"]["text"] == "hi"
    assert "site_notes" not in result


def test_missing_directory_entirely_degrades(tmp_path, monkeypatch):
    _point_at(monkeypatch, tmp_path / "does" / "not" / "exist")
    assert S._load_site_index() == {}
    result = _roundtrip("https://civitai.com/models/1")
    assert result["ok"] is True and "site_notes" not in result


@pytest.mark.parametrize("bad_value", [
    "../../../etc/passwd",      # traversal
    "sub/dir/notes.md",         # escapes the flat directory
    "..",
    "",
    "   ",
    None, 5, ["civitai.com.md"],
])
def test_registry_rejects_a_value_that_is_not_a_bare_filename(bad_value, tmp_path,
                                                              monkeypatch):
    """A registry can only ever name a file INSIDE its own directory. The emitted
    string is handed to an agent as a path to read, so a traversal value is a
    real instruction, not a theoretical one."""
    d = _write_index(tmp_path / "bad", json.dumps(
        {"sites": {"civitai.com": bad_value}}))
    _point_at(monkeypatch, d)
    assert S._site_notes_path("civitai.com") == ""


@pytest.mark.parametrize("bad_key", [
    "https://civitai.com", "civitai.com:443", ".civitai.com", "", "   ",
    "civi tai.com", "civitai.com/models",
])
def test_registry_rejects_a_key_that_is_not_a_bare_host(bad_key, tmp_path,
                                                        monkeypatch):
    d = _write_index(tmp_path / "badkey", json.dumps(
        {"sites": {bad_key: "x.md"}}))
    _point_at(monkeypatch, d)
    assert S._load_site_index() == {}


def test_one_junk_entry_does_not_discard_the_good_ones(tmp_path, monkeypatch):
    """Per-entry validation, not all-or-nothing: a typo in a new row must not
    silently switch OFF a site that was working yesterday."""
    d = _write_index(tmp_path / "mixed", json.dumps({"sites": {
        "civitai.com": "civitai.com.md",
        "broken.test": "../escape.md",
        "also/broken": "ok.md",
    }}))
    _point_at(monkeypatch, d)
    assert S._load_site_index() == {"civitai.com": "civitai.com.md"}
    assert S._site_notes_path("civitai.com") == "reference/sites/civitai.com.md"


def test_the_index_is_parsed_once_per_directory(tmp_path, monkeypatch):
    """Cheapness, asserted rather than assumed: the registry must not be re-read
    from disk on every command. Delete the file after the first load — a second
    lookup that still answers proves it came from the cache."""
    d = _write_index(tmp_path / "cached", json.dumps(
        {"sites": {"cached.test": "cached.test.md"}}))
    _point_at(monkeypatch, d)
    assert S._site_notes_path("cached.test") == "reference/sites/cached.test.md"
    (d / "_index.json").unlink()
    assert S._site_notes_path("cached.test") == "reference/sites/cached.test.md"


# --------------------------------------------------------------------------- #
# THE LEDGER — the registry's key set and the directory's *.md set are identical
# --------------------------------------------------------------------------- #
def _ledger_diff(directory):
    """(orphan_entries, unregistered_files) for a sites directory.

    An ORPHAN is a registry key naming a file that is not there — an agent is
    told to read a doc that does not exist. An UNREGISTERED file is a doc nobody
    can ever be routed to — the work of writing it is silently wasted. Both are
    errors, and the ledger fails on either, so the set can neither GROW nor
    SHRINK away from the other.
    """
    directory = Path(directory)
    raw = json.loads((directory / "_index.json").read_text(encoding="utf-8"))
    registered = set(raw["sites"].values())
    on_disk = {p.name for p in directory.glob("*.md")}
    return sorted(registered - on_disk), sorted(on_disk - registered)


def test_index_and_files_are_the_same_set():
    orphans, unregistered = _ledger_diff(SITES_DIR)
    assert not orphans, (
        f"{INDEX} registers file(s) that do not exist: {orphans}. An agent "
        "following `site_notes` would be sent to a missing doc."
    )
    assert not unregistered, (
        f"{SITES_DIR} holds *.md file(s) no registry entry names: "
        f"{unregistered}. Nothing can ever route to them — add a `sites` entry "
        "or delete the file."
    )


def test_the_ledger_fails_when_the_set_GROWS(tmp_path):
    """MUTATION, in-suite and reachable: add a bogus registry entry for a file
    that is not on disk, and the ledger must report an orphan."""
    d = _write_index(tmp_path / "grow", json.dumps({"sites": {
        "civitai.com": "civitai.com.md",
        "bogus.test": "bogus.test.md",
    }}), files=["civitai.com.md"])
    orphans, unregistered = _ledger_diff(d)
    assert orphans == ["bogus.test.md"]
    assert unregistered == []


def test_the_ledger_fails_when_the_set_SHRINKS(tmp_path):
    """The other direction: a real file whose registry entry was deleted must be
    reported as unregistered. A one-directional ledger passes this case."""
    d = _write_index(tmp_path / "shrink", json.dumps({"sites": {
        "civitai.com": "civitai.com.md",
    }}), files=["civitai.com.md", "orphaned.test.md"])
    orphans, unregistered = _ledger_diff(d)
    assert orphans == []
    assert unregistered == ["orphaned.test.md"]


def test_every_registered_file_is_non_trivial():
    """A registered but EMPTY doc routes an agent to nothing and would still pass
    the ledger — the set check is about names, not content."""
    raw = json.loads(INDEX.read_text(encoding="utf-8"))
    for name in raw["sites"].values():
        body = (SITES_DIR / name).read_text(encoding="utf-8")
        assert len(body) > 500, f"{name} is {len(body)} bytes — is it a stub?"
        assert body.lstrip().startswith("# "), f"{name} has no H1 title"
        assert "**Load this when:**" in body, (
            f"{name} lacks the house-style '**Load this when:**' trigger line "
            "every other reference file opens with."
        )


# --------------------------------------------------------------------------- #
# SKILL.md must name the DIRECTORY, never a site
# --------------------------------------------------------------------------- #
def test_skill_md_names_the_directory_and_no_individual_site():
    """🔴 THE INVARIANT: SKILL.md does not grow as sites are added.

    It is loaded on every browser task and has a hard byte ceiling
    (test_skill_size.py). One row naming `reference/sites/<host>.md` is the
    whole contract; a per-site row would make the always-loaded body grow
    linearly with the registry.
    """
    skill = (Path(__file__).resolve().parent.parent / "SKILL.md").read_text(
        encoding="utf-8")
    assert "`reference/sites/<host>.md`" in skill, (
        "SKILL.md must carry the directory-level pointer row.")
    raw = json.loads(INDEX.read_text(encoding="utf-8"))
    for host, name in raw["sites"].items():
        assert f"reference/sites/{name}" not in skill, (
            f"SKILL.md names the individual site file {name}. Sites are routed "
            "at runtime via the `site_notes` envelope field — SKILL.md names "
            "only the directory, so it never grows as sites are added.")
