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
at run time (see `nix/system/apply-nebula-443.sh`). Adding an ALLOWLIST entry is
for values that are not a disclosure at all, and every entry must say WHICH kind.
If you are tempted to pin a real endpoint, the answer is an env var, not a pin.

🔴 EVERY EXEMPTION IS SCOPED TO A PATH. Both the hand-written `ALLOWLIST` and the
derived AirVPN-catalogue exemption are keyed on `(relpath, value)`, never on the
value alone. A value-only exemption is repo-wide by construction, and that is not
a theory: the first version of this gate let the audit's M7 mutant through —
a new routable IP added to `scripts/data/airvpn-servers.json` AND pasted into
`claude/skills/bar/SKILL.md` passed 12/12, because the catalogue parse had
pre-approved the value everywhere. `25.7.8.71` (a ClickHouse *version*) is a
genuinely routable address in 25/8 and was pre-approved repo-wide the same way.

🔴 SCOPE, stated honestly: this guards HEAD. Git history still carries every
value ever committed, and rewriting history would not unpublish anything that has
already been cloned or forked. This stops the NEXT one.

Hostnames are a SEPARATE gate — `scripts/tests/test_no_client_hostnames.py`.
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

#: This file. Every pinned value below is SPELLED in the keys, so the scan finds
#: it here too. Those occurrences are the pin, not a site — derived, so the
#: allowlist does not have to carry a mirror entry per value.
SELF = "scripts/tests/test_no_public_ips.py"

#: The AirVPN catalogue exemption applies ONLY in these files. `airvpn-servers.json`
#: IS the published provider catalogue; `test_airvpn_menu.py` mirrors a couple of
#: its endpoints as fixtures. Anywhere else, a catalogue value is as much of a
#: disclosure as any other routable literal — see M7 in this module's docstring.
CATALOGUE_FILES = (
    "scripts/data/airvpn-servers.json",
    "scripts/tests/test_airvpn_menu.py",
)

#: How many DISTINCT catalogue endpoints `test_airvpn_menu.py` may mirror. Pinned
#: because the exemption in a TEST file is the softer of the two: the catalogue
#: file's contents are checkable against the provider, a test fixture's are not.
#: Distinct, not occurrences — the same two values are repeated ~18 times.
MENU_TEST_MIRRORED_ENDPOINTS = 2

# --- THE PINNED (PATH, VALUE) ALLOWLIST ----------------------------------------
# ((relpath, literal), why it is not a disclosure). Accounting runs in BOTH
# directions, the same discipline as test_runtime_shebangs.py:
#   * a hit matching no entry -> FAIL (something new landed);
#   * an entry matching no hit -> FAIL (the site went away; delete the pin rather
#     than leave a rubber stamp that pre-approves whatever appears next).
#
# 🔴 KEYED ON THE PATH. Pinning a bare value grants it repo-wide, which is how the
# audit's M7 mutant survived. If the same value is legitimately used in five
# files, it gets five entries — the noise IS the point: each one is a place a
# reviewer agreed the value is harmless.
ALLOWLIST = {
    # Cloudflare / Google / Quad9 public resolvers — global services, not endpoints
    # of ours. `8.8.8.8` is additionally the UDP-connect trick that picks the
    # egress iface without sending a packet.
    ("nix/system/apply-dns-travel.sh", "1.1.1.1"): "public resolver written into a travel DNS config",
    ("nix/system/apply-travel-prep.sh", "1.1.1.1"): "public resolver written into a travel DNS config",
    ("scripts/tests/test_airvpn_menu.py", "1.1.1.1"): "public resolver as a 'some public IP' fixture",
    ("nix/system/apply-dns-travel.sh", "8.8.8.8"): "public resolver written into a travel DNS config",
    ("nix/system/apply-travel-prep.sh", "8.8.8.8"): "public resolver written into a travel DNS config",
    ("scripts/browser-bridge/server.py", "8.8.8.8"): "UDP-connect egress-iface probe (no packet is sent)",
    ("scripts/browser-bridge/tests/test_server.py", "8.8.8.8"): "asserts the egress-iface probe target",
    ("scripts/session-analysis/session_insight/tests/test_scrub.py", "8.8.8.8"): "scrubber fixture standing in for 'a public IP'",
    ("scripts/tests/test_airvpn_menu.py", "8.8.8.8"): "public resolver as a 'some public IP' fixture",
    ("scripts/tests/test_airvpn_menu.py", "9.9.9.9"): "public resolver as a 'some public IP' fixture",
    # The conventional dummies. Not routable to anything of ours; the point of
    # them is that they are obviously fake.
    ("claudedocs/handoff-agent-setup-audit.md", "1.2.3.4"): "the conventional dummy, quoted from a guard test",
    ("scripts/claude-hooks/guard_core.py", "1.2.3.4"): "the conventional dummy in the module's own docstring/examples",
    ("scripts/claude-hooks/tests/test_guard_core.py", "1.2.3.4"): "the conventional dummy in the talosctl/publish-sink tests",
    ("scripts/tests/test_airvpn_menu.py", "1.2.3.4"): "the conventional dummy",
    ("scripts/tests/test_opencode_config.py", "1.2.3.4"): "the conventional dummy in a permission-pattern fixture",
    ("scripts/claude-hooks/tests/test_guard_core.py", "5.6.7.8"): "the second dummy in the two-node talosctl spellings",
    ("scripts/tests/test_opencode_config.py", "5.6.7.8"): "the second dummy in a permission-pattern fixture",
    # NOT addresses at all — three version/CIDR strings that happen to parse.
    ("claudedocs/clickhouse-headroom-proposal-2026-08-02.md", "25.7.8.71"):
        "NOT an address: the ClickHouse VERSION. 25/8 is genuinely routable, which is "
        "exactly why this entry is path-scoped — pinned repo-wide it pre-approved a whole /8",
    ("scripts/browser-bridge/tests/emulation.test.mjs", "126.0.0.0"):
        "NOT an address: the Chrome major in a mobile User-Agent fixture",
    ("nix/system/apply-mullvad-enable.sh", "128.0.0.0"):
        "NOT a host: the upper half of the WireGuard split-default pair 0.0.0.0/1 + 128.0.0.0/1",
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
    "scripts/airvpn-updown": (
        1,
        "the same Hetzner lighthouse IP, as the NEBULA_LIGHTHOUSE constant. This "
        "file is a LIVE fail-closed killswitch on the workbench's uplink, so "
        "moving the value to a root-owned env file is a runtime change that must "
        "pass the `bar` skill's mandatory re-test protocol on a physically "
        "reachable host. It is therefore split into its own PR rather than "
        "carried here. Merging that PR deletes this entry in the same commit.",
    ),
}


def airvpn_endpoints() -> set[str]:
    """Every endpoint IP in the committed AirVPN catalogue.

    Derived, not spelled: `scripts/data/airvpn-servers.json` IS a public VPN
    provider's PUBLISHED server list, and the menu + its tests are built on it.
    Reading the allowlist out of the file means adding a server cannot turn this
    gate red, and it cannot drift from what the file actually contains.

    🔴 DERIVED IS NOT UNSCOPED. The caller must still confine these to
    `CATALOGUE_FILES`. Granting them repo-wide is what let M7 through.
    """
    data = json.loads(AIRVPN_CATALOGUE.read_text(encoding="utf-8"))
    servers = data["servers"] if isinstance(data, dict) else data
    return {s["endpoint_ip"] for s in servers if s.get("endpoint_ip")}


def _hits():
    return S.scan_repo(REPO)


def is_exempt(path: str, ip: str) -> bool:
    """The ONE place an exemption is decided — path-scoped, always.

    Three sources, in order of how much they are trusted:
      1. a hand-written `(path, value)` pin with a written reason;
      2. this file's own spelling of those pinned values (they are the pin);
      3. the AirVPN catalogue, but ONLY inside `CATALOGUE_FILES`.
    """
    if (path, ip) in ALLOWLIST:
        return True
    if path == SELF and any(v == ip for _p, v in ALLOWLIST):
        return True
    if path in CATALOGUE_FILES and ip in airvpn_endpoints():
        return True
    return False


def _unpinned(hits):
    return [h for h in hits
            if not is_exempt(h[0], h[2]) and h[0] not in PENDING_SCRUB]


# --- the scan ------------------------------------------------------------------

def test_no_unallowlisted_public_ip_literal_is_committed():
    unpinned = _unpinned(_hits())
    assert not unpinned, (
        "a routable public IP literal is committed to a PUBLIC repo — scrub it "
        "(use a $KC_* handle, a hostname, or a run-time env var):\n  "
        + "\n  ".join(f"{p}:{n}: {ip}   | {line}" for p, n, ip, line in unpinned))


def test_every_allowlist_entry_still_matches_something():
    """Stale-pin accounting — the complement of the scan above.

    Now keyed on `(path, value)`, so this catches a strictly larger class than
    the value-only version did: a pin also goes stale when the value MOVES to a
    different file, which under value-only keying was invisible.
    """
    hits = _hits()
    seen = {(h[0], h[2]) for h in hits}
    stale = [f"{path}: {ip} ({why})"
             for (path, ip), why in ALLOWLIST.items() if (path, ip) not in seen]
    assert not stale, (
        "ALLOWLIST entries match nothing — the site went away or moved. Delete "
        "or repoint the pin instead of leaving it to pre-approve the next "
        "value:\n  " + "\n  ".join(stale))


def test_the_menu_test_mirrors_only_a_couple_of_catalogue_endpoints():
    """Bound the softer half of the catalogue exemption.

    `CATALOGUE_FILES` grants ~255 provider endpoints inside two files. In the
    catalogue itself that is the file's whole purpose. In a TEST file it is a
    convenience, and an unbounded grant there is a place to hide a value: paste
    a new endpoint into the catalogue and it becomes quotable in the test file
    forever. Pin the DISTINCT count so growing it is a decision.
    """
    eps = airvpn_endpoints()
    mirrored = {ip for path, _n, ip, _l in _hits()
                if path == "scripts/tests/test_airvpn_menu.py" and ip in eps}
    assert len(mirrored) == MENU_TEST_MIRRORED_ENDPOINTS, (
        f"test_airvpn_menu.py mirrors {len(mirrored)} distinct catalogue "
        f"endpoints, pinned at {MENU_TEST_MIRRORED_ENDPOINTS}: {sorted(mirrored)}")


def pending_scrub_problems(hits):
    """The ratchet, as a pure function of `hits` so a control can drive it."""
    actual = {}
    for path, _n, ip, _line in hits:
        if path in PENDING_SCRUB and not is_exempt(path, ip):
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
    return problems


def test_pending_scrub_counts_are_exact():
    """The ratchet. Fails if a pending leak grows OR if it is fixed."""
    problems = pending_scrub_problems(_hits())
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


def test_positive_control_an_allowlisted_value_is_still_reported_elsewhere():
    """🔴 THE M7 CONTROL, part 1 — the hand-written allowlist.

    The audit's surviving mutant worked because an exemption granted in one file
    was honoured in every file. Take a value that IS pinned, put it somewhere it
    is NOT pinned, and require a finding. Fails the moment anyone re-keys
    `ALLOWLIST` on the bare value again.
    """
    path, ip = next(iter(ALLOWLIST))
    assert is_exempt(path, ip), "fixture is stale: pick a pinned (path, value)"
    elsewhere = [("claude/skills/bar/SKILL.md", 1, ip, f"endpoint {ip}")]
    assert _unpinned(elsewhere) == elsewhere, (
        f"{ip} is pinned in {path} but was exempt in claude/skills/bar/SKILL.md "
        "too — the allowlist is keyed on the value, not the (path, value)")


def test_positive_control_a_catalogue_endpoint_is_reported_outside_the_catalogue():
    """🔴 THE M7 CONTROL, part 2 — the DERIVED exemption, which is the one that
    actually survived. M7 added a routable IP to `scripts/data/airvpn-servers.json`
    and pasted it into `claude/skills/bar/SKILL.md`; 12/12 passed. Reproduced
    here as a unit: a real catalogue endpoint, quoted outside CATALOGUE_FILES.
    """
    ep = sorted(airvpn_endpoints())[0]
    assert is_exempt(CATALOGUE_FILES[0], ep), "the catalogue exemption is broken"
    leaked = [("claude/skills/bar/SKILL.md", 12, ep, f"endpoint: {ep}")]
    assert _unpinned(leaked) == leaked, (
        f"catalogue endpoint {ep} was exempt in claude/skills/bar/SKILL.md — the "
        "catalogue exemption is repo-wide again (this is the audit's M7)")


def test_positive_control_pending_scrub_would_catch_growth():
    """The ratchet is also a zero-shaped claim. Drive it with fabricated hits in
    BOTH directions rather than asserting a property of the constant."""
    path = next(iter(PENDING_SCRUB))
    expected, _why = PENDING_SCRUB[path]
    assert expected > 0, "a pending entry pinned at 0 asserts nothing"

    def hits_for(n):
        return [(p, i, planted_ipv4(), "x")
                for p, (c, _w) in PENDING_SCRUB.items()
                for i in range(1, (n if p == path else c) + 1)]

    assert not pending_scrub_problems(hits_for(expected)), "the ratchet is wired to nothing"
    grew = pending_scrub_problems(hits_for(expected + 1))
    assert any("GREW" in p for p in grew), grew
    shrank = pending_scrub_problems(hits_for(expected - 1))
    assert any("SCRUBBED" in p for p in shrank), shrank


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
    itself. Both files must come back with nothing beyond the pinned values this
    file necessarily spells (see `SELF`)."""
    pinned_values = {v for _p, v in ALLOWLIST}
    for path in (Path(__file__).resolve(), Path(S.__file__).resolve()):
        found = [(n, ip) for n, ip, _l in S.scan_file(path)
                 if ip not in pinned_values]
        assert not found, f"{path.name} matches its own scan: {found}"
