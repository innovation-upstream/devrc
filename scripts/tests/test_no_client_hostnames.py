#!/usr/bin/env python3
"""REPO-WIDE structural guard: no CLIENT subdomain literal in a tracked file.

The hostname half of `CLAUDE.md`'s "never commit … a real third-party hostname
used as an example". The IP half is `test_no_public_ips.py`; the scan is
`scripts/testlib/client_host_scan.py`, whose docstring explains — with the
measured token counts — why this deliberately targets client SUBDOMAINS rather
than attempting to police every FQDN in the repo.

HOW TO SATISFY IT
-----------------
Ask which of two kinds the value is.

  * **Test-load-bearing only** — a fixture, a doc example, a frame URL in an
    assertion. Nothing resolves it at run time. Replace it with a fictional host
    under a reserved TLD (`*.example.test`, RFC 6761). That is what happened to
    the browser-bridge OOPIF fixtures.

  * **FUNCTIONALLY load-bearing** — something actually opens it. Do NOT
    placeholder it; that ships a dead button. Move the VALUE out of tracked
    source and keep the FEATURE: `~/.config/bar/urls.env` + `scripts/bar-url`,
    which is how the `civ` bar block still opens the client Grafana.

Adding an ALLOWLIST entry is the third option and it is the rare one — it is for
a subdomain that is genuinely public and genuinely not topology.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from testlib import client_host_scan as H  # noqa: E402

# --- THE PINNED (PATH, HOST) ALLOWLIST -----------------------------------------
# Keyed on the PATH, for the same reason the IP gate is (see the M7 note there):
# a bare-value exemption is repo-wide by construction.
#
# Accounting runs in BOTH directions — an unpinned hit fails, and a pin matching
# nothing fails so a stale entry cannot pre-approve whatever appears next.
ALLOWLIST: dict[tuple[str, str], str] = {}


def _hits():
    return H.scan_repo(REPO)


def _unpinned(hits):
    return [h for h in hits if (h[0], h[2]) not in ALLOWLIST]


# --- the scan ------------------------------------------------------------------

def test_no_client_subdomain_literal_is_committed():
    unpinned = _unpinned(_hits())
    assert not unpinned, (
        "a CLIENT subdomain is committed to a PUBLIC repo — this is internal "
        "topology. Replace a fixture with a *.example.test host; move a value "
        "something actually OPENS into ~/.config/bar/urls.env (see "
        "scripts/bar-url):\n  "
        + "\n  ".join(f"{p}:{n}: {h}   | {line}" for p, n, h, line in unpinned))


def test_every_allowlist_entry_still_matches_something():
    """Stale-pin accounting — the complement of the scan above.

    INVARIANT GUARD while `ALLOWLIST` is empty: it asserts nothing today and is
    labelled as such. It exists so the first entry anyone adds is accounted for
    in both directions from the moment it is added, rather than the accounting
    being remembered later.
    """
    seen = {(h[0], h[2]) for h in _hits()}
    stale = [f"{p}: {h} ({why})" for (p, h), why in ALLOWLIST.items()
             if (p, h) not in seen]
    assert not stale, (
        "ALLOWLIST entries match nothing — the site went away or moved. Delete "
        "or repoint the pin:\n  " + "\n  ".join(stale))


# --- positive controls ---------------------------------------------------------
# 🔴 The assertion above is a ZERO, and a zero is indistinguishable from a scan
# wired to nothing (RULES.md → "a harness that COUNTS needs a POSITIVE control").
# Everything below must produce a NON-ZERO count.
#
# The planted hosts are ASSEMBLED at run time. Spelled as literals they would be
# findings in this very file — the self-match trap that made the first draft of
# testlib/shebang_scan.py report its own source, and the first draft of
# public_ip_scan.py report its own docstring.

def planted_domain() -> str:
    """A client apex, assembled — the scan must NOT report this."""
    return ".".join(("civit", "ai"))


def planted_host() -> str:
    """A realistic internal subdomain: a service name, not a textbook `www`.

    RULES.md: a control built from a canonical example proves nothing, because
    scanners allowlist canonical examples.
    """
    return "grafana-staging." + planted_domain()


def test_positive_control_the_scan_finds_a_planted_client_subdomain(tmp_path):
    bad = tmp_path / "nested" / "notes.md"
    bad.parent.mkdir(parents=True)
    bad.write_text(f"dashboards live at https://{planted_host()}/d/abc\n",
                   encoding="utf-8")
    hits = H.scan_repo(tmp_path)
    assert len(hits) == 1, f"scan is wired to nothing: {hits}"
    assert hits[0][0] == "nested/notes.md" and hits[0][1] == 1
    assert hits[0][2] == planted_host()


def test_positive_control_a_deep_subdomain_and_a_bare_host_both_report(tmp_path):
    """Two shapes the browser fixtures actually used: a URL, and a BARE host in
    a string argument (`frame: "<host>"`). A URL-only regex misses the second —
    which is most of the occurrences that were in this repo."""
    deep = "a.b." + planted_host()
    f = tmp_path / "t.mjs"
    f.write_text(f'OPS.eval({{ frame: "{planted_host()}" }});\n'
                 f'const U = "https://{deep}/app";\n', encoding="utf-8")
    hits = H.scan_repo(tmp_path)
    assert [h[2] for h in hits] == [planted_host(), deep], hits


def test_positive_control_case_is_normalised(tmp_path):
    """The browser-bridge suite asserts an UPPERCASE host to pin case-insensitive
    frame matching. A case-sensitive scan would have missed it — and did, in the
    first pass of the scrub this gate was written for."""
    f = tmp_path / "t.mjs"
    f.write_text(f'resolveFrame(frames, "{planted_host().upper()}")\n',
                 encoding="utf-8")
    hits = H.scan_repo(tmp_path)
    assert len(hits) == 1 and hits[0][2] == planted_host(), hits


def test_a_subdomain_written_as_a_REGEX_LITERAL_is_found():
    """🔴 MEASURED SMUGGLING FORM #1 (audit). A hostname matcher in JS/Python
    source is one of the likeliest places a real client host gets written down,
    and there it is spelled with ESCAPED DOTS. Before the fix: escaped → 0 hits,
    unescaped → 1. The scrub this gate was written for found a regex literal
    among its own occurrences, so this is hypothetical only in the current tree.

    The hit is reported in ONE canonical form — backslashes stripped — so an
    allowlist cannot need an entry per escaping style.
    """
    host = planted_host()
    esc = host.replace(".", r"\.")
    assert H.find_in_line(f"/^{esc}$/") == [host]
    assert H.find_in_line(f"re.compile(r'{esc}')") == [host]
    # a JS regex with escaped slashes around it, the exact shape in this repo
    assert H.find_in_line(rf"/frame_not_found:https:\/\/{esc}\//") == [host]


def test_a_subdomain_whose_left_neighbour_is_an_underscore_is_found():
    """🔴 MEASURED SMUGGLING FORM #2 (audit). `_` is `\\w`, so with it excluded
    from a label the left lookbehind refused to back off to the valid suffix and
    the whole host reported NOTHING — 0 hits despite an embedded real leak.

    Realistic shapes: a DNS SRV/TXT record, and markdown italics. Underscore is
    not legal in a hostname label; it is very common in the places one gets
    WRITTEN DOWN, which is what this scan reads.
    """
    host = planted_host()
    assert H.find_in_line(f"_grpc.{host}") == [f"_grpc.{host}"]
    assert H.find_in_line(f"_{host}_") == [f"_{host}"]      # markdown italics
    srv = H.find_in_line(f"_grpc._tcp.{host}  3600 IN SRV 0 5 443 x.{host}")
    assert srv == [f"_grpc._tcp.{host}", f"x.{host}"], srv


def test_the_widened_pattern_did_not_lose_anything_it_used_to_find():
    """🔴 THE COST SIDE of the two fixes above, pinned rather than assumed.

    Widening a scanner can LOSE matches as easily as gain them, and a loss is
    invisible — the gate just goes quieter. MEASURED across six domains that are
    heavily present in this repo (519 hits total): the widened pattern was
    **+2, -0**, and both additions were genuine escaped-dot regex literals.

    This case re-runs the shrinking half as a unit: every shape the narrow
    pattern matched must still match.
    """
    host = planted_host()
    for line in (f"https://{host}/app",
                 f'frame: "{host}"',
                 f"the dashboard lives at {host}.",
                 f"({host}), reachable",
                 f"a.b.{host}",
                 host.upper()):
        assert H.find_in_line(line), f"the widened pattern LOST {line!r}"


def test_positive_control_the_allowlist_does_not_swallow_a_real_value():
    """The scan FINDING a line and the assertion REPORTING it are different
    things; an over-broad allowlist would eat a genuine leak silently."""
    fabricated = [("nix/graphical.nix", 200, planted_host(), "xdg-open")]
    assert _unpinned(fabricated) == fabricated


def test_positive_control_an_allowlisted_host_is_still_reported_elsewhere():
    """Path-scoping, driven rather than asserted about the constant. Grant a
    fabricated pin, then require the SAME host to be reported in another file."""
    pin = ("docs/somewhere.md", planted_host())
    fabricated_allow = {pin: "control"}
    hits = [("docs/somewhere.md", 1, planted_host(), "x"),
            ("nix/graphical.nix", 200, planted_host(), "y")]
    left = [h for h in hits if (h[0], h[2]) not in fabricated_allow]
    assert [h[0] for h in left] == ["nix/graphical.nix"], left


# --- negative controls (the false-positive half) --------------------------------

def test_the_client_apex_alone_is_not_a_finding():
    """🔴 DELIBERATE. `civitai.com` is prose about a client, ~100 times over. If
    the apex were a finding this gate would be permanently red and therefore
    meaningless (RULES.md). What leaks is the SUBDOMAIN — the topology."""
    assert H.find_in_line(f"we do infra for {planted_domain()}") == []
    assert H.find_in_line(f"see https://{planted_domain()}/models") == []


def test_a_longer_domain_that_merely_ends_the_same_is_not_a_finding():
    """Boundary cases either side of the domain, each of which a sloppy
    `endswith` or an unanchored regex gets wrong. All three were MEASURED — the
    first draft of the regex failed the third."""
    apex = planted_domain()
    assert H.find_in_line(f"visit my{apex} today") == [], "left boundary leaks"
    assert H.find_in_line(f"a.{apex}munity is not it") == [], "right boundary leaks"
    assert H.find_in_line(f"x.{apex}.evil.test") == [], "must not truncate a longer host"


def test_a_sentence_final_host_is_still_a_finding():
    """The counterweight to the case above. Rejecting ANY following dot would
    also reject the most ordinary way a leak gets written: a host at the end of
    a sentence. Both must hold at once, which is why the right boundary is two
    lookaheads rather than one character class."""
    assert H.find_in_line(f"the dashboard lives at {planted_host()}.") == [
        planted_host()]
    assert H.find_in_line(f"({planted_host()}), reachable") == [planted_host()]


def test_the_fictional_replacement_hosts_are_not_findings():
    """The scrub replaced the real fixtures with `*.example.test`. If those were
    findings the gate would be red on the very tree that satisfies it."""
    for host in ("model-benchmarking.example.test", "auth.example.test",
                 "other-origin.example.test"):
        assert H.find_in_line(f'frame: "{host}"') == [], host


# --- guarding the guard's inputs -----------------------------------------------

def test_the_scan_actually_reads_the_repo():
    """A wrong REPO root yields an empty file set and the zero above passes
    vacuously — the exact bug the IP gate shipped and caught."""
    files = H.repo_files(REPO)
    assert len(files) > 300, f"REPO={REPO} produced only {len(files)} files"
    assert any(f.name == "flake.nix" for f in files), "not the devrc root"


def test_the_skip_dir_test_is_relative_to_the_root(tmp_path):
    """🔴 The harness bug the IP gate shipped, pinned here so this scan cannot
    repeat it: every agent worktree lives under `…/worktrees/<id>/`, so an
    ABSOLUTE-parts skip test is true for every file and skips the whole repo
    while reporting a clean zero."""
    root = tmp_path / "worktrees" / "agent-x"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "n.md").write_text(f"https://{planted_host()}/\n",
                                       encoding="utf-8")
    hits = H.scan_repo(root)
    assert len(hits) == 1, (
        "the repo was skipped because its own PATH contains a skip-dir name: "
        f"{hits}")


def test_the_domain_list_drives_the_regex():
    """The list is DATA and the pattern is DERIVED. Pinning the relationship
    means adding a client cannot be half-done (RULES.md → 'one rule, one place').
    Driven with a fabricated domain, so it fails if anyone hard-codes the
    pattern instead of building it.
    """
    rx = H._build_re({"acme-client.test"})
    assert H.find_in_line("api.acme-client.test", rx) == ["api.acme-client.test"]
    assert H.find_in_line("acme-client.test", rx) == [], "apex must not match"
    assert H.find_in_line(f"api.{planted_domain()}", rx) == [], (
        "a domain NOT in the set passed to _build_re was still matched — the "
        "regex is not derived from its argument")


def test_this_guards_own_sources_are_clean():
    """The self-match trap. Neither this file nor the scanner may contain a
    client subdomain literal — including in the prose that explains the rule."""
    for path in (Path(__file__).resolve(), Path(H.__file__).resolve()):
        found = H.scan_file(path)
        assert not found, f"{path.name} matches its own scan: {found}"
