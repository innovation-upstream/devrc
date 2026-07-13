"""Unit tests for scripts/deep-search — Prowlarr search + qBittorrent grab tool.

All OFFLINE: no live Prowlarr/qBittorrent/network is ever touched. The decision
logic is factored into PURE functions and exercised here against a real-shaped
Prowlarr search fixture (scripts/tests/fixtures/prowlarr_search.json) that
deliberately mixes:
  * clean cross-indexer matches (TrackerA/PublicIndexer "Big Buck Bunny" at
    480p/720p/1080p/2160p, plus a 1080p copy duplicated on TrackerB), and
  * NOISE (unrelated Sintel / Tears of Steel / Cosmos Laundromat releases that an
    indexer dumps for a specific query).
We assert the relevance filter removes the noise, quality ranking puts 1080p above
720p above 4K, dedupe collapses the cross-indexer duplicate keeping the best-seeded
copy, the min-seeders filter works, and every small pure helper behaves. The
network seam (`search(..., fetch=)`, `grab(..., post=)`) is mocked, and the
`--json` / `--dry-run` / `--search-fixture` CLI paths run via subprocess with the
canned fixture and NO network. Mirrors scripts/tests/test_media_menu.py.

    run:  pytest scripts/tests/test_deep_search.py
"""
import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "prowlarr_search.json"
QUERY = "Big Buck Bunny"


def _load(name, modname):
    loader = importlib.machinery.SourceFileLoader(modname, str(SCRIPTS / name))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ds = _load("deep-search", "deep_search")


@pytest.fixture
def raw():
    return json.loads(FIXTURE.read_text())


def _guids(results):
    return {r.get("guid") for r in results}


def _titles(results):
    return " || ".join(r.get("title", "") for r in results)


# --------------------------------------------------------------------------- #
# parse_quality
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("title,expected", [
    ("Big Buck Bunny 2008 2160p BluRay x264 GROUP", "2160p"),
    ("Some Title 4K UHD Remux", "2160p"),
    ("Big.Buck.Bunny.2008.1080p.BluRay.x264-GROUP", "1080p"),
    ("Big Buck Bunny 2008 720p BluRay x264 GROUP", "720p"),
    ("Big Buck Bunny 2008 480p BluRay x264", "480p"),
    ("Old Classic Movie SD DVDRip", "sd"),
    ("Big Buck Bunny No Resolution Tag", None),
    ("", None),
    (None, None),
])
def test_parse_quality(title, expected):
    assert ds.parse_quality(title) == expected


def test_parse_quality_does_not_falsematch_sd_inside_words():
    # 'sd' must be a whole token, not a substring of e.g. 'HDsd' / 'wisdom'.
    assert ds.parse_quality("Big Buck Bunny wisdom title") is None


# --------------------------------------------------------------------------- #
# relevance + is_relevant (the noise filter)
# --------------------------------------------------------------------------- #
def test_relevance_counts_distinct_query_tokens():
    assert ds.relevance(QUERY, "Big Buck Bunny 2008 1080p") == 3
    assert ds.relevance(QUERY, "Buck Bunny Collection 1080p") == 2
    assert ds.relevance(QUERY, "Buck Rogers Complete Series") == 1
    assert ds.relevance(QUERY, "Sintel 2010 BluRay") == 0


def test_is_relevant_keeps_full_and_partial_majority_matches():
    assert ds.is_relevant(QUERY, "Big Buck Bunny 2008 1080p")
    assert ds.is_relevant(QUERY, "Buck Bunny Shorts Collection 1080p")


def test_is_relevant_drops_single_token_and_unrelated_noise():
    # single-token (1/3 tokens) is below the ceil(n/2)=2 majority threshold ...
    assert not ds.is_relevant(QUERY, "Buck Rogers Complete Series 2019 1080p")
    # ... and the noise dump shares NONE of the query tokens.
    assert not ds.is_relevant(QUERY, "Sintel 2010 1080p BluRay x264")
    assert not ds.is_relevant(QUERY, "Tears of Steel 2012 720p BluRay x264")
    assert not ds.is_relevant(QUERY, "Cosmos Laundromat 2015 2160p BluRay x264")


def test_is_relevant_empty_query_keeps_everything():
    assert ds.is_relevant("", "literally anything")


# --------------------------------------------------------------------------- #
# dedupe (collapse cross-indexer duplicates, keep best-seeded)
# --------------------------------------------------------------------------- #
def test_dedupe_collapses_cross_indexer_dup_keeping_best_seeded(raw):
    deduped = ds.dedupe(raw)
    # The 1080p release exists on BOTH TrackerA (S=59) and TrackerB (S=35);
    # after dedupe only the best-seeded (TrackerA 59) survives.
    tracker_a = "http://trackera.test/big-buck-bunny-1080p-1"
    tracker_b = "http://trackerb.test/big-buck-bunny-1080p-dup-99"
    guids = _guids(deduped)
    assert tracker_a in guids
    assert tracker_b not in guids
    # one fewer than the raw set (exactly one pair collapsed)
    assert len(deduped) == len(raw) - 1


def test_dedupe_ignores_non_dicts():
    assert ds.dedupe([{"title": "a", "seeders": 1}, "junk", None]) == \
        [{"title": "a", "seeders": 1}]


# --------------------------------------------------------------------------- #
# rank (relevance -> quality preference -> seeders)
# --------------------------------------------------------------------------- #
def test_rank_prefers_1080_over_720_over_4k(raw):
    # Same-relevance TrackerA release at each quality; ranking must order
    # 1080p < 720p < ... < 2160p (4K deprioritized below the SD tiers).
    same_rel = [r for r in raw if r["indexer"] == "TrackerA"]
    ranked = ds.rank(same_rel, QUERY)
    qualities = [ds.parse_quality(r["title"]) for r in ranked]
    assert qualities.index("1080p") < qualities.index("720p")
    assert qualities.index("720p") < qualities.index("2160p")


def test_rank_relevance_dominates_quality_and_seeders():
    results = [
        {"title": "Buck Bunny Collection 1080p", "seeders": 999, "indexer": "x"},
        {"title": "Big Buck Bunny 2008 720p", "seeders": 1, "indexer": "y"},
    ]
    ranked = ds.rank(results, QUERY)
    # the 3-token match wins despite worse quality + far fewer seeders
    assert ranked[0]["title"].startswith("Big Buck Bunny")


# --------------------------------------------------------------------------- #
# filter_results
# --------------------------------------------------------------------------- #
def test_filter_min_seeders(raw):
    kept = ds.filter_results(raw, QUERY, min_seeders=20, relevant_only=True)
    assert all(r["seeders"] >= 20 for r in kept)
    # the 0-seeder remux and the 2-seeder 2160p release are gone
    assert all(r["seeders"] not in (0, 2) for r in kept)


def test_filter_relevance_removes_noise(raw):
    kept = ds.filter_results(raw, QUERY, min_seeders=1, relevant_only=True)
    blob = _titles(kept)
    assert "Sintel" not in blob
    assert "Tears of Steel" not in blob
    assert "Cosmos Laundromat" not in blob
    assert "Buck Rogers" not in blob   # single-token match


def test_filter_all_keeps_noise(raw):
    kept = ds.filter_results(raw, QUERY, min_seeders=1, relevant_only=False)
    assert "Sintel" in _titles(kept)


# --------------------------------------------------------------------------- #
# process (full pipeline: filter -> dedupe -> rank)
# --------------------------------------------------------------------------- #
def test_process_end_to_end(raw):
    out = ds.process(raw, QUERY, min_seeders=1, relevant_only=True)
    # 5 survivors: the four TrackerA qualities (1080/720/480/2160) + the
    # 2-token "Buck Bunny Shorts Collection". Noise + 0-seeder + single-token
    # dropped; the TrackerB 1080p dup collapsed into the TrackerA copy.
    assert len(out) == 5
    # top = best-seeded 1080p (dedupe winner)
    assert out[0]["seeders"] == 59
    assert ds.parse_quality(out[0]["title"]) == "1080p"
    # quality order holds across the top TrackerA releases
    qs = [ds.parse_quality(r["title"]) for r in out]
    assert qs.index("1080p") < qs.index("720p") < qs.index("2160p")
    # no noise, no dropped dup
    blob = _titles(out)
    for bad in ("Sintel", "Tears of Steel", "Cosmos Laundromat", "Buck Rogers"):
        assert bad not in blob
    assert "http://trackerb.test/big-buck-bunny-1080p-dup-99" not in _guids(out)


def test_process_min_seeders_20(raw):
    out = ds.process(raw, QUERY, min_seeders=20, relevant_only=True)
    # keeps 1080p(59) + 480p(23); 720p(14)/2160p(2)/compilation(12) all < 20.
    assert len(out) == 2
    assert all(r["seeders"] >= 20 for r in out)


def test_process_all_keeps_noise(raw):
    out = ds.process(raw, QUERY, min_seeders=1, relevant_only=False)
    # everything with >=1 seeder minus the collapsed dup = 9
    assert len(out) == 9
    assert "Sintel" in _titles(out)


# --------------------------------------------------------------------------- #
# human_size
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("n,expected", [
    (0, "0B"),
    (512, "512B"),
    (1024, "1.0K"),
    (562026240, "536.0M"),
    (3478923520, "3.2G"),
    (1024 ** 4, "1.0T"),
])
def test_human_size(n, expected):
    assert ds.human_size(n) == expected


def test_human_size_bad_input():
    assert ds.human_size(None) == "?"
    assert ds.human_size("abc") == "?"
    assert ds.human_size(-5) == "?"


# --------------------------------------------------------------------------- #
# grab_payload (must be EXACTLY {guid, indexerId})
# --------------------------------------------------------------------------- #
def test_grab_payload_is_exactly_guid_and_indexer_id():
    r = {"title": "x", "seeders": 5, "guid": "G-123", "indexerId": 7,
         "downloadUrl": "http://d", "size": 10}
    assert ds.grab_payload(r) == {"guid": "G-123", "indexerId": 7}


# --------------------------------------------------------------------------- #
# parse_selection
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("inp,n,expected", [
    ("1", 5, 0),
    ("5", 5, 4),
    (" 3 ", 5, 2),
    ("0", 5, None),        # 1-based; 0 is out of range
    ("6", 5, None),        # past end
    ("-1", 5, None),
    ("abc", 5, None),
    ("", 5, None),
    (None, 5, None),
    ("2.5", 5, None),
])
def test_parse_selection(inp, n, expected):
    assert ds.parse_selection(inp, n) == expected


# --------------------------------------------------------------------------- #
# format helpers
# --------------------------------------------------------------------------- #
def test_format_row_shape(raw):
    row = ds.format_row(1, raw[0])
    assert row.strip().startswith("1")
    assert "S=59" in row
    assert "1080p" in row
    assert "[TrackerA]" in row
    assert "Big.Buck.Bunny" in row


def test_format_list_numbers_from_one(raw):
    out = ds.process(raw, QUERY, min_seeders=1, relevant_only=True)
    listing = ds.format_list(out)
    lines = listing.splitlines()
    assert len(lines) == len(out)
    assert lines[0].strip().startswith("1")
    assert lines[-1].strip().startswith(str(len(out)))


def test_project_shape(raw):
    p = ds.project(raw[0])
    assert p["quality"] == "1080p"
    assert p["seeders"] == 59
    assert p["indexerId"] == 3
    assert p["size_h"] == "3.2G"
    assert set(("title", "guid", "protocol")).issubset(p)


# --------------------------------------------------------------------------- #
# network seam: search() + grab() mocked (NO live network)
# --------------------------------------------------------------------------- #
def test_search_uses_fetch_seam_and_builds_url(raw):
    captured = {}

    def fake_fetch(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return raw

    c = {"PROWLARR_URL": "http://prowlarr.example", "PROWLARR_KEY": "TESTKEY"}
    out = ds.search("Big Buck Bunny", c, limit=50, fetch=fake_fetch)
    assert out == raw
    assert "/api/v1/search?type=search&limit=50&query=Big%20Buck%20Bunny" \
        in captured["url"]
    assert captured["headers"]["X-Api-Key"] == "TESTKEY"


def test_search_non_list_response_is_empty():
    out = ds.search("q", {"PROWLARR_URL": "http://p", "PROWLARR_KEY": "k"},
                    fetch=lambda *a, **k: {"error": "boom"})
    assert out == []


def test_grab_posts_exact_payload_and_reports_success():
    captured = {}

    def fake_post(url, headers=None, data=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json.loads(data.decode())
        return 201, b""

    r = {"guid": "G-9", "indexerId": 3}
    c = {"PROWLARR_URL": "http://prowlarr.example", "PROWLARR_KEY": "K"}
    ok, detail = ds.grab(r, c, post=fake_post)
    assert ok is True
    assert "201" in detail
    assert captured["url"].endswith("/api/v1/search")
    assert captured["body"] == {"guid": "G-9", "indexerId": 3}
    assert captured["headers"]["X-Api-Key"] == "K"


def test_grab_non_2xx_is_failure():
    ok, detail = ds.grab({"guid": "g", "indexerId": 1},
                         {"PROWLARR_URL": "http://p", "PROWLARR_KEY": "k"},
                         post=lambda *a, **k: (500, b"nope"))
    assert ok is False
    assert "500" in detail


def test_grab_exception_is_caught():
    def boom(*a, **k):
        raise OSError("connection refused")
    ok, detail = ds.grab({"guid": "g", "indexerId": 1},
                         {"PROWLARR_URL": "http://p", "PROWLARR_KEY": "k"}, post=boom)
    assert ok is False
    assert "connection refused" in detail


# --------------------------------------------------------------------------- #
# credentials: MEDIA_ENV override, no secret hardcoded
# --------------------------------------------------------------------------- #
def test_creds_reads_media_env_override(tmp_path, monkeypatch):
    env = tmp_path / "media.env"
    env.write_text("# comment\nPROWLARR_URL=http://x\nPROWLARR_KEY=abc\nBAD LINE\n")
    monkeypatch.setenv("MEDIA_ENV", str(env))
    c = ds.creds()
    assert c["PROWLARR_URL"] == "http://x"
    assert c["PROWLARR_KEY"] == "abc"


def test_creds_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_ENV", str(tmp_path / "nope.env"))
    assert ds.creds() == {}


# --------------------------------------------------------------------------- #
# CLI: --json / --dry-run / --search-fixture (offline, NO network, NO grab)
# --------------------------------------------------------------------------- #
def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPTS / "deep-search"), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=30)


def test_cli_json_processes_fixture_offline():
    r = _run(QUERY, "--json", "--search-fixture", str(FIXTURE))
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert len(data) == 5
    assert data[0]["quality"] == "1080p"
    assert data[0]["seeders"] == 59
    blob = json.dumps(data)
    assert "Sintel" not in blob and "Tears of Steel" not in blob


def test_cli_json_all_keeps_noise():
    r = _run(QUERY, "--json", "--all", "--search-fixture", str(FIXTURE))
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert len(data) == 9
    assert "Sintel" in json.dumps(data)


def test_cli_dry_run_lists_and_grabs_nothing():
    r = _run(QUERY, "--dry-run", "--search-fixture", str(FIXTURE))
    assert r.returncode == 0
    assert "dry-run" in r.stdout
    assert "S=59" in r.stdout
    assert "Sintel" not in r.stdout          # noise filtered from the list


def test_cli_min_seeders_flag():
    r = _run(QUERY, "--json", "--min-seeders", "20", "--search-fixture", str(FIXTURE))
    data = json.loads(r.stdout)
    assert len(data) == 2
    assert all(d["seeders"] >= 20 for d in data)


def test_cli_requires_query():
    r = _run("--json", "--min-seeders", "1")   # no query, no fixture
    assert r.returncode == 2
    assert "required" in (r.stdout + r.stderr).lower()
