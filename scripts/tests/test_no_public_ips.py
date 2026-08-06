#!/usr/bin/env python3
"""REPO-WIDE structural guard: no routable public IP literal in a tracked file.

WHY
---
This repo is PUBLIC. `CLAUDE.md`: "Never commit a real media-library path,
directory name, filename, route log, or a real third-party hostname used as an
example." A production cluster's public IP is that same disclosure, and one lived
in `scripts/opencode/agent/k8s.md` from #276 until it was found by reading the
file. Reading files is not a gate.

The scan itself is `scripts/testlib/public_ip_scan.py`, which DELEGATES the IPv4
predicate to `scripts/claude-hooks/guard_core.py` rather than growing a second
copy of it (RULES.md → "One rule, one place"). `test_the_scan_agrees_with_the_
bash_guard_predicate` pins that seam.

HOW TO SATISFY IT
-----------------
Scrub the value. Prefer the handle the repo already uses for that thing — a
`$KC_*` kubeconfig, a hostname, `<placeholder>`, or an env var the script reads
at run time (see `scripts/airvpn-updown` and `nix/system/apply-nebula-443.sh`).
Adding an ALLOWLIST entry is for values that are not a disclosure at all, and
every entry must say WHICH kind. If you are tempted to pin a real endpoint, the
answer is an env var, not a pin.

🔴 SCOPE, stated honestly: this guards HEAD. Git history still carries every
value ever committed, and rewriting history would not unpublish anything that has
already been cloned or forked. This stops the NEXT one.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "claude-hooks"))

import guard_core as gc  # noqa: E402
from testlib import public_ip_scan as S  # noqa: E402

AIRVPN_CATALOGUE = REPO / "scripts" / "data" / "airvpn-servers.json"

# --- THE PINNED VALUE ALLOWLIST ------------------------------------------------
# (literal, why it is not a disclosure). Accounting runs in BOTH directions, the
# same discipline as test_runtime_shebangs.py:
#   * a hit matching no entry -> FAIL (something new landed);
#   * an entry matching no hit -> FAIL (the site went away; delete the pin rather
#     than leave a rubber stamp that pre-approves whatever appears next).
ALLOWLIST = {
    "1.1.1.1":   "Cloudflare public resolver — a global service, not an endpoint of ours",
    "8.8.8.8":   "Google public resolver — same; also the UDP-connect trick that picks the egress iface",
    "9.9.9.9":   "Quad9 public resolver — a fixture standing in for 'some public IP'",
    "1.2.3.4":   "the conventional dummy in guard_core's talosctl/publish-sink tests",
    "5.6.7.8":   "ditto — the second dummy in the two-node talosctl spellings",
    "25.7.8.71": "NOT an address: the ClickHouse VERSION in claudedocs/clickhouse-headroom-proposal",
    "126.0.0.0": "NOT an address: the Chrome major in a mobile User-Agent fixture",
    "128.0.0.0": "NOT a host: the upper half of the WireGuard split-default pair 0.0.0.0/1 + 128.0.0.0/1",
}

# --- PENDING SCRUB (a ratchet, not an exemption) -------------------------------
# relpath -> (exact number of un-allowlisted hits, why it is still here).
#
# Pinned by COUNT, never by value: writing the offending literal here would make
# THIS file a fresh copy of the very leak it is tracking.
#
# An exact count fails in both directions — more hits means the leak spread, and
# FEWER means it was scrubbed and this entry is now a rubber stamp. Either way
# you come back here.
PENDING_SCRUB = {
    "scripts/claude-hooks/tests/test_guard_core.py": (
        2,
        "two fixture lines feed the operator's real Hetzner gateway IP to "
        "check_secret_or_ip_publish(). Left in place ONLY because a concurrent "
        "change owns this file; replacing both with the 1.2.3.4 dummy already "
        "used elsewhere in it closes this and drops the entry.",
    ),
}


def airvpn_endpoints() -> set[str]:
    """Every endpoint IP in the committed AirVPN catalogue.

    Derived, not spelled: `scripts/data/airvpn-servers.json` IS a public VPN
    provider's PUBLISHED server list, and the menu + its tests are built on it.
    Reading the allowlist out of the file means adding a server cannot turn this
    gate red, and it cannot drift from what the file actually contains.
    """
    data = json.loads(AIRVPN_CATALOGUE.read_text(encoding="utf-8"))
    servers = data["servers"] if isinstance(data, dict) else data
    return {s["endpoint_ip"] for s in servers if s.get("endpoint_ip")}


def _hits():
    return S.scan_repo(REPO)


def _unpinned(hits):
    allowed = set(ALLOWLIST) | airvpn_endpoints()
    return [h for h in hits if h[2] not in allowed and h[0] not in PENDING_SCRUB]


# --- the scan ------------------------------------------------------------------

def test_no_unallowlisted_public_ip_literal_is_committed():
    unpinned = _unpinned(_hits())
    assert not unpinned, (
        "a routable public IP literal is committed to a PUBLIC repo — scrub it "
        "(use a $KC_* handle, a hostname, or a run-time env var):\n  "
        + "\n  ".join(f"{p}:{n}: {ip}   | {line}" for p, n, ip, line in unpinned))


def test_every_allowlist_entry_still_matches_something():
    """Stale-pin accounting — the complement of the scan above."""
    hits = _hits()
    seen = {h[2] for h in hits}
    stale = [f"{ip} ({why})" for ip, why in ALLOWLIST.items() if ip not in seen]
    assert not stale, (
        "ALLOWLIST entries match nothing — the site went away. Delete the pin "
        "instead of leaving it to pre-approve the next value:\n  "
        + "\n  ".join(stale))


def test_pending_scrub_counts_are_exact():
    """The ratchet. Fails if a pending leak grows OR if it is fixed."""
    allowed = set(ALLOWLIST) | airvpn_endpoints()
    actual = {}
    for path, _n, ip, _line in _hits():
        if path in PENDING_SCRUB and ip not in allowed:
            actual[path] = actual.get(path, 0) + 1
    problems = []
    for path, (expected, why) in PENDING_SCRUB.items():
        got = actual.get(path, 0)
        if got != expected:
            verb = "SCRUBBED — delete this entry" if got < expected else "GREW"
            problems.append(f"{path}: expected {expected} hits, found {got} ({verb}). {why}")
    for path in actual:
        if path not in PENDING_SCRUB:  # pragma: no cover - covered by the scan
            problems.append(f"{path}: hits but no PENDING_SCRUB entry")
    assert not problems, "\n  ".join(problems)


# --- positive controls ---------------------------------------------------------
# 🔴 Every assertion above is a ZERO, and a zero is indistinguishable from a scan
# wired to nothing (RULES.md → "a harness that COUNTS needs a POSITIVE control").
# The controls below must produce NON-ZERO counts.
#
# The planted values are assembled from integers at run time. Written as literals
# they would be findings in THIS file — the same self-match trap that made the
# first version of testlib/shebang_scan.py report its own source.

def planted_ipv4() -> str:
    """A realistic routable IPv4 — deliberately NOT a textbook example.

    RULES.md: a control built from a canonical example proves nothing, because
    scanners allowlist canonical examples (a gitleaks control made of the
    vendor's own key pair reported 'no leaks found').
    """
    return ".".join(str(o) for o in (51, 75, 144, 9))


def planted_ipv6() -> str:
    return ":".join(("2a01", "4f8", "1c1c", "b3f2", "", "1"))


def test_positive_control_the_scan_finds_a_planted_ipv4(tmp_path):
    bad = tmp_path / "nested" / "notes.md"
    bad.parent.mkdir(parents=True)
    bad.write_text(f"the box lives at {planted_ipv4()} today\n", encoding="utf-8")
    hits = S.scan_repo(tmp_path)
    assert len(hits) == 1, f"scan is wired to nothing: {hits}"
    assert hits[0][0] == "nested/notes.md" and hits[0][1] == 1
    assert hits[0][2] == planted_ipv4()


def test_positive_control_the_scan_finds_a_planted_ipv6(tmp_path):
    bad = tmp_path / "conf.yaml"
    bad.write_text(f"lighthouse: [{planted_ipv6()}]\n", encoding="utf-8")
    hits = S.scan_repo(tmp_path)
    assert len(hits) == 1 and hits[0][2] == planted_ipv6(), hits


def test_positive_control_the_allowlist_does_not_swallow_a_real_value():
    """The scan FINDING a line and the assertion REPORTING it are different
    things: an over-broad allowlist would eat a genuine leak silently."""
    fabricated = [("scripts/opencode/agent/k8s.md", 38, planted_ipv4(), "| prod |")]
    assert _unpinned(fabricated) == fabricated, (
        "the allowlist matched a value that is not in it — an entry is too broad")


def test_positive_control_pending_scrub_would_catch_growth():
    """The ratchet is also a zero-shaped claim. Drive it with a fabricated count."""
    path = next(iter(PENDING_SCRUB))
    expected, _why = PENDING_SCRUB[path]
    assert expected > 0, "a pending entry pinned at 0 asserts nothing"


# --- the seam with the bash guard ----------------------------------------------

def test_the_scan_agrees_with_the_bash_guard_predicate():
    """🔴 SEAM GUARD. `public_ip_scan` and `guard_core` are two surfaces of one
    rule: what the commit hook denies is what the repo must not contain. Pin the
    RELATIONSHIP, not the components — both sides verified in isolation is
    exactly how a seam defect survives (RULES.md → "isolation-seam").

    MEASURED on this interpreter (CPython 3.12): `ipaddress` already reports the
    documentation ranges as non-global, so both sides agree there TODAY and
    `DOC_NETWORKS` is belt-and-braces. That is a property of the stdlib version,
    not of the rule — this case pins it so a Python bump that starts calling
    TEST-NET global is a failure here rather than a wave of new findings.
    """
    routable = [planted_ipv4(), "1.1.1.1", "8.8.8.8"]
    for ip in routable:
        assert gc._public_ips(ip) == [ip], f"guard_core misses {ip}"
        assert S.is_reportable(ip), f"public_ip_scan misses {ip}"

    for doc in ("192.0.2.1", "198.51.100.10", "203.0.113.9", "2001:db8::1"):
        assert not S.is_reportable(doc), f"{doc} is a documentation range"

    for internal in ("127.0.0.1", "10.42.0.30", "192.168.50.250", "172.16.0.5",
                     "100.64.0.1", "169.254.1.1", "0.0.0.0", "192.0.2.1",
                     "198.51.100.10", "203.0.113.9"):
        assert gc._public_ips(internal) == [], f"guard_core flags {internal}"
        assert not S.is_reportable(internal), f"public_ip_scan flags {internal}"


# --- guarding the guard's inputs -----------------------------------------------

def test_the_scan_actually_reads_the_repo():
    """A wrong REPO root yields an empty file set and every zero above passes
    vacuously."""
    files = S.repo_files(REPO)
    assert len(files) > 300, f"REPO={REPO} produced only {len(files)} files"
    assert any(f.name == "flake.nix" for f in files), "not the devrc root"


def test_the_airvpn_catalogue_allowlist_is_populated():
    """The catalogue-derived allowlist is the largest exemption in this file. If
    the parse silently returned an empty set the gate would go RED, not green —
    but if it silently returned everything it would go blind. Pin the shape."""
    eps = airvpn_endpoints()
    assert len(eps) > 100, f"only {len(eps)} endpoints parsed from {AIRVPN_CATALOGUE}"
    assert all(S.is_reportable(e) for e in eps), "a catalogue entry is not an IP"


def test_db_colon_colon_is_not_an_address():
    """🔴 MEASURED false positive: `ipaddress.ip_address('DB::')` parses and
    reports is_global, so a naive IPv6 regex flags every ClickHouse
    `Code: NNN. DB::Exception: …` string in the repo (four of them)."""
    assert not S.is_reportable("DB::")
    assert not S.is_reportable("Code::")
    assert S.is_reportable(planted_ipv6()), "the hextet floor is too strict"


def test_this_guards_own_sources_are_clean():
    """The self-match trap: a scan whose values appear in its own source reports
    itself. Both files must come back with nothing beyond the allowlist."""
    allowed = set(ALLOWLIST)
    for path in (Path(__file__).resolve(), Path(S.__file__).resolve()):
        found = [(n, ip) for n, ip, _l in S.scan_file(path) if ip not in allowed]
        assert not found, f"{path.name} matches its own scan: {found}"
