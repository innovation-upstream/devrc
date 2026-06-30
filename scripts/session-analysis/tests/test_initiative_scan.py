"""Unit tests for initiative-scan PURE logic (no live ClickHouse / git / gh).

Run:
  nix-shell -p 'python3.withPackages(p:[p.pytest p.requests])' \
      --run 'python -m pytest scripts/session-analysis/tests -q'

Covers: handoff filename/title/next-step/open-investigations parsing, dated-variant
clustering, momentum classification (2d/7d boundaries), and slug<->branch matching.
The git/gh/ClickHouse calls are stubbed where the orchestration is exercised.
"""
import importlib.util
import sys
from pathlib import Path

# Load initiative-scan.py (a hyphenated, non-importable filename) by path.
HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "initiative-scan.py"
# chquery lives in ../../validation — the script adds it to sys.path on import.
sys.path.insert(0, str(HERE.parent.parent / "validation"))
_spec = importlib.util.spec_from_file_location("initiative_scan", SCRIPT)
isc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(isc)


DAY = 86400


# --------------------------------------------------------------------------- #
# Handoff filename parsing — slug + date extraction (all real-world variants)
# --------------------------------------------------------------------------- #
def test_filename_date_suffix():
    assert isc.parse_handoff_filename("handoff-activity-telemetry-2026-06-27.md") == (
        "activity-telemetry", "2026-06-27")


def test_filename_date_prefix():
    # Date appears as a prefix (handoff-<date>-<slug>.md) — slug is the tail.
    assert isc.parse_handoff_filename("handoff-2026-06-25-clawgate-tasks.md") == (
        "clawgate-tasks", "2026-06-25")


def test_filename_no_date():
    assert isc.parse_handoff_filename("handoff-app-blocks-launch.md") == (
        "app-blocks-launch", None)


def test_filename_date_is_the_slug():
    # handoff-2026-05-25.md — the whole slug IS the date; keep it as the slug.
    assert isc.parse_handoff_filename("handoff-2026-05-25.md") == (
        "2026-05-25", "2026-05-25")


# --------------------------------------------------------------------------- #
# Title parsing
# --------------------------------------------------------------------------- #
def test_title_strips_date_tail():
    text = "# Handoff: activity-telemetry + mail — 2026-06-27\n\n## Goal\nx\n"
    assert isc.parse_handoff_title(text) == "Handoff: activity-telemetry + mail"


def test_title_none_when_absent():
    assert isc.parse_handoff_title("no heading here\njust prose\n") is None


# --------------------------------------------------------------------------- #
# Next-steps extraction
# --------------------------------------------------------------------------- #
NEXT_DOC = """# Handoff: mail-automation — 2026-06-30

## Goal
Automate the inbox.

## Next steps (ranked)
1. **Ship the extractor** on the live mail table — highest leverage.
2. Lower: rotate the OpenRouter key.

## Gotchas
- something
"""


def test_next_step_first_ranked_item_flattened():
    step = isc.parse_next_step(NEXT_DOC)
    assert step == "Ship the extractor on the live mail table — highest leverage."


def test_next_step_handles_decorated_heading():
    doc = "## Next steps (ranked) — what's LEFT\n1. Do the thing\n\n## End\n"
    assert isc.parse_next_step(doc) == "Do the thing"


def test_next_step_dash_bullets():
    doc = "## Next steps\n- first bullet item\n- second\n"
    assert isc.parse_next_step(doc) == "first bullet item"


def test_next_step_none_without_section():
    assert isc.parse_next_step("## Goal\nstuff\n## Gotchas\nx\n") is None


def test_next_step_section_ends_at_next_h2():
    # No list items before the next H2 -> None (don't bleed into Gotchas).
    doc = "## Next steps\n\n## Gotchas\n1. not a next step\n"
    assert isc.parse_next_step(doc) is None


# --------------------------------------------------------------------------- #
# Open-investigations section (newer template)
# --------------------------------------------------------------------------- #
OPEN_INV_DOC = """# Handoff: dp-prod-500-floor — 2026-06-20

## Goal
Hold the 500 floor.

## Open investigations — live diagnosis state
### 500s spike on the edge nodes during canary
- Symptom: ...
### nginx frame-ancestors blocks the embed
- Observed: ...

## Next steps (ranked)
1. Re-run the canary with the new floor.
"""


def test_open_investigations_extracted():
    inv = isc.parse_open_investigations(OPEN_INV_DOC)
    assert inv == [
        "500s spike on the edge nodes during canary",
        "nginx frame-ancestors blocks the embed",
    ]


def test_open_investigations_empty_when_absent():
    assert isc.parse_open_investigations(NEXT_DOC) == []


def test_open_inv_doc_still_parses_next_step():
    assert isc.parse_next_step(OPEN_INV_DOC) == "Re-run the canary with the new floor."


# --------------------------------------------------------------------------- #
# Dated-variant clustering
# --------------------------------------------------------------------------- #
def _doc(repo, slug, date, mtime, title="t", next_step="ns", path=None):
    return {
        "repo": repo, "slug": slug, "date": date, "mtime": mtime,
        "title": title, "next_step": next_step, "open_investigations": [],
        "path": path or f"{repo}/claudedocs/handoff-{slug}-{date}.md",
    }


def test_cluster_merges_dated_variants_newest_wins():
    docs = [
        _doc("/r", "app-blocks", "2026-06-26", 100.0, next_step="old step"),
        _doc("/r", "app-blocks", "2026-06-27", 200.0, next_step="new step"),
    ]
    inis = isc.cluster_handoffs(docs)
    assert len(inis) == 1
    ini = inis[0]
    assert ini["slug"] == "app-blocks"
    assert ini["date"] == "2026-06-27"          # newest by filename date
    assert ini["next_step"] == "new step"       # current state = newest doc
    assert len(ini["docs"]) == 2                # both members retained


def test_cluster_distinct_slugs_stay_separate():
    docs = [
        _doc("/r", "mail-automation", "2026-06-30", 100.0),
        _doc("/r", "qa-automation", "2026-06-29", 90.0),
    ]
    inis = isc.cluster_handoffs(docs)
    assert {i["slug"] for i in inis} == {"mail-automation", "qa-automation"}


def test_cluster_dateless_doc_uses_mtime():
    docs = [
        _doc("/r", "remote-approval", None, 50.0, next_step="A"),
        _doc("/r", "remote-approval", None, 80.0, next_step="B"),
    ]
    inis = isc.cluster_handoffs(docs)
    assert len(inis) == 1
    assert inis[0]["next_step"] == "B"          # higher mtime wins


# --------------------------------------------------------------------------- #
# Momentum classification — boundaries at 2d / 7d
# --------------------------------------------------------------------------- #
def test_momentum_active_under_2d():
    now = 1_000_000.0
    assert isc.classify_momentum(now - (2 * DAY - 1), now) == "active"


def test_momentum_boundary_2d_is_slowing():
    now = 1_000_000.0
    assert isc.classify_momentum(now - 2 * DAY, now) == "slowing"


def test_momentum_slowing_under_7d():
    now = 1_000_000.0
    assert isc.classify_momentum(now - (7 * DAY - 1), now) == "slowing"


def test_momentum_boundary_7d_is_stalled():
    now = 1_000_000.0
    assert isc.classify_momentum(now - 7 * DAY, now) == "stalled"


def test_momentum_unknown_when_no_touch():
    assert isc.classify_momentum(None, 1_000_000.0) == "unknown"


def test_newest_touch_picks_max_ignoring_none():
    assert isc.newest_touch(None, 10.0, 5.0, None) == 10.0
    assert isc.newest_touch(None, None) is None


# --------------------------------------------------------------------------- #
# Slug <-> branch matching (heuristic)
# --------------------------------------------------------------------------- #
def test_branch_full_slug_substring():
    assert isc.branch_matches_slug("feat/mail-automation", "mail-automation")


def test_branch_type_prefix_stripped():
    assert isc.branch_matches_slug("fix/app-blocks-launch", "app-blocks-launch")


def test_branch_token_overlap_two_tokens():
    # >=2 shared meaningful tokens is enough even without the full slug substring.
    # branch carries "activity" + "telemetry"; slug reorders/extends them.
    assert isc.branch_matches_slug("feat/activity-telemetry-collector", "telemetry-activity-i3")


def test_branch_no_match_single_weak_token():
    # Only one token overlaps ("source"); not enough.
    assert not isc.branch_matches_slug("feat/scroll-source", "activity-telemetry")


def test_trunk_branches_never_match():
    for b in ("main", "master", "trunk", "develop"):
        assert not isc.branch_matches_slug(b, "mail-automation")


def test_short_slug_requires_all_tokens():
    # A single-token slug must match that token.
    assert isc.branch_matches_slug("feat/clawgate", "clawgate")
    assert not isc.branch_matches_slug("feat/clawgate", "sysredis")


def test_slug_tokens_drops_dates_and_stopwords():
    toks = isc.slug_tokens("activity-telemetry-2026-06-27")
    assert "activity" in toks and "telemetry" in toks
    assert "2026" not in toks and "06" not in toks


# --------------------------------------------------------------------------- #
# Commit-window attribution excludes the default branch (anti-inflation)
# --------------------------------------------------------------------------- #
def test_commits_default_branch_returns_zero(monkeypatch):
    # The default branch itself is the unsegmented catch-all, never an initiative.
    monkeypatch.setattr(isc, "_run", lambda cmd, timeout=20.0: "9999\n8888\n")
    assert isc.git_commits_in_window("/r", "trunk", 14, "trunk") == (0, None)


def test_commits_feature_branch_uses_not_default(monkeypatch):
    seen = {}

    def fake_run(cmd, timeout=20.0):
        seen["cmd"] = cmd
        return "1700000000\n1699990000\n"

    # The branch + both default refs resolve (local main present alongside origin).
    monkeypatch.setattr(isc, "_ref_exists", lambda repo, ref: True)
    monkeypatch.setattr(isc, "_run", fake_run)
    n, last = isc.git_commits_in_window("/r", "feat/x", 14, "trunk")
    assert n == 2 and last == 1700000000.0
    # The --not <default> exclusion must be present so trunk history isn't counted.
    assert "--not" in seen["cmd"] and "trunk" in seen["cmd"]


# --------------------------------------------------------------------------- #
# Robust git refs (#2/#3): missing local default + remote-only branch
# --------------------------------------------------------------------------- #
def test_commits_missing_local_default_does_not_fatal(monkeypatch):
    # Repo on `trunk` with no local `main`: only `feat/x` and `origin/trunk`
    # exist. The exclusion set must drop the missing `trunk` local ref instead of
    # passing it to git (which would fatal rc=128 → swallowed "" → a false 0).
    existing = {"feat/x", "origin/trunk"}
    monkeypatch.setattr(isc, "_ref_exists", lambda repo, ref: ref in existing)

    seen = {}

    def fake_run(cmd, timeout=20.0):
        seen["cmd"] = cmd
        return "1700000000\n1699990000\n1699980000\n"

    monkeypatch.setattr(isc, "_run", fake_run)
    n, last = isc.git_commits_in_window("/r", "feat/x", 14, "trunk")
    # Counts correctly (3), NOT a false 0; the missing local `trunk` is excluded
    # from --not but `origin/trunk` is kept.
    assert n == 3 and last == 1700000000.0
    assert "--not" in seen["cmd"]
    assert "origin/trunk" in seen["cmd"]
    # The non-existent bare `trunk` ref must NOT have been passed to git.
    idx = seen["cmd"].index("--not")
    assert "trunk" not in seen["cmd"][idx:]


def test_commits_unresolvable_branch_reports_unknown(monkeypatch):
    # Neither `feat/ghost` nor `origin/feat/ghost` exists → (None, None) "unknown",
    # never a silent (0, None) that masquerades as "no work".
    monkeypatch.setattr(isc, "_ref_exists", lambda repo, ref: False)
    monkeypatch.setattr(isc, "_run", lambda cmd, timeout=20.0: "")
    assert isc.git_commits_in_window("/r", "feat/ghost", 14, "main") == (None, None)


def test_commits_remote_only_branch_resolves_to_origin(monkeypatch):
    # A branch existing ONLY as origin/feat/y must resolve (and git log it) via
    # its remote-tracking ref, not fatal on a non-existent local `feat/y`.
    existing = {"origin/feat/y", "main", "origin/main"}
    monkeypatch.setattr(isc, "_ref_exists", lambda repo, ref: ref in existing)

    seen = {}

    def fake_run(cmd, timeout=20.0):
        seen["cmd"] = cmd
        return "1700000000\n"

    monkeypatch.setattr(isc, "_run", fake_run)
    n, last = isc.git_commits_in_window("/r", "origin/feat/y", 14, "main")
    assert n == 1 and last == 1700000000.0
    # git log was invoked against the resolved origin/feat/y ref.
    assert "origin/feat/y" in seen["cmd"]


def test_git_branches_keeps_remote_only_branch(monkeypatch):
    # `feat/local` exists both places (dedups to bare); `feat/remote` is remote-only
    # (keeps the origin/ prefix so a later `git log` can resolve it). origin/HEAD
    # alias is dropped.
    out = ("main\nfeat/local\norigin/main\norigin/feat/local\n"
           "origin/feat/remote\norigin/HEAD\n")
    monkeypatch.setattr(isc, "_run", lambda cmd, timeout=20.0: out)
    names = isc.git_branches("/r")
    assert "feat/local" in names           # bare (local preferred)
    assert "origin/feat/local" not in names  # deduped away
    assert "origin/feat/remote" in names   # remote-only keeps prefix
    assert "main" in names
    assert not any(n.endswith("HEAD") for n in names)


# --------------------------------------------------------------------------- #
# Word-equality branch matching (#5): no substring false positives
# --------------------------------------------------------------------------- #
def test_longer_slug_not_matched_by_shorter_branch():
    # The `app-blocks-followups` slug must NOT match the plain `feat/app-blocks`
    # branch: {app,blocks,followups} ⊄ {app,blocks}. (The sibling-credit direction —
    # `app-blocks` matching the LONGER branch — is handled by best_matching_initiative,
    # tested in test_best_match_prefers_most_specific_sibling.)
    assert not isc.branch_matches_slug("feat/app-blocks", "app-blocks-followups")


def test_mail_actions_does_not_match_email_fractions():
    # The classic false positive: mail⊂email, actions⊂fractions under substrings.
    # Word equality kills it: {mail,actions} ⊄ {email,fractions,redesign}.
    assert not isc.branch_matches_slug("feat/email-fractions-redesign", "mail-actions")


def test_app_api_does_not_match_mapper_rapid():
    assert not isc.branch_matches_slug("feat/mapper-rapid", "app-api")


def test_exact_feature_branch_still_matches():
    assert isc.branch_matches_slug(
        "zach/civitai-auth-observability", "civitai-auth-observability")


def test_best_match_prefers_most_specific_sibling():
    # Branch app-blocks-followups fits BOTH slugs; credit the specific one only.
    inis = [
        {"slug": "app-blocks", "repo": "/r"},
        {"slug": "app-blocks-followups", "repo": "/r"},
    ]
    best = isc.best_matching_initiative("feat/app-blocks-followups", inis)
    assert best is not None and best["slug"] == "app-blocks-followups"
    # And the plain `app-blocks` branch goes to the broad one (followups not present).
    best2 = isc.best_matching_initiative("feat/app-blocks", inis)
    assert best2 is not None and best2["slug"] == "app-blocks"


def test_siblings_do_not_share_identical_commit_counts(monkeypatch):
    # Two sibling initiatives + per-branch distinct commit counts: each branch is
    # awarded to its single best initiative, so the counts must DIFFER (the bug was
    # both siblings claiming every app-blocks-* branch → identical totals).
    inis = [
        {"slug": "app-blocks", "repo": "/r"},
        {"slug": "app-blocks-followups", "repo": "/r"},
    ]
    monkeypatch.setattr(isc, "git_branches", lambda r: [
        "feat/app-blocks", "feat/app-blocks-followups"])
    monkeypatch.setattr(isc, "git_default_branch", lambda r: "main")
    monkeypatch.setattr(isc, "gh_open_prs", lambda r: [])
    monkeypatch.setattr(isc, "gh_merged_prs", lambda r, d: [])

    def fake_commits(repo, branch, days, default=None):
        return ({"feat/app-blocks": 4,
                 "feat/app-blocks-followups": 9}.get(branch, 0), 1000.0)

    monkeypatch.setattr(isc, "git_commits_in_window", fake_commits)
    isc.attribute_git(inis, 14)
    by_slug = {i["slug"]: i["commits"] for i in inis}
    assert by_slug == {"app-blocks": 4, "app-blocks-followups": 9}


# --------------------------------------------------------------------------- #
# Cross-repo telemetry isolation (#4)
# --------------------------------------------------------------------------- #
def test_telemetry_same_branch_token_does_not_cross_repo():
    # Two initiatives in different repos, same branch token `feat/api`. Activity in
    # repo A's cwd must credit ONLY repo A's initiative, not repo B's.
    inis = [
        {"slug": "api", "repo": "/home/u/workspace/repoA"},
        {"slug": "api", "repo": "/home/u/workspace/repoB"},
    ]
    rows = [
        {"branch": "feat/api", "cwd": "/home/u/workspace/repoA/sub", "n": 7,
         "last_ts": "2026-06-30 10:00:00"},
    ]
    isc.attribute_telemetry(inis, rows, [
        "/home/u/workspace/repoA", "/home/u/workspace/repoB"])
    a = next(i for i in inis if i["repo"].endswith("repoA"))
    b = next(i for i in inis if i["repo"].endswith("repoB"))
    assert a["telem_events"] == 7
    assert b["telem_events"] == 0  # no cross-credit into the other repo


# --------------------------------------------------------------------------- #
# Session attribution (genesis text -> initiative)
# --------------------------------------------------------------------------- #
def test_attribute_sessions_matches_handoff_filename():
    inis = [{
        "slug": "mail-automation",
        "docs": [{"path": "/r/claudedocs/handoff-mail-automation-2026-06-30.md", "date": "2026-06-30"}],
    }]
    genesis = [
        {"text": "continue the work, read handoff-mail-automation-2026-06-30.md first", "mtime": 500.0},
        {"text": "unrelated session about something else", "mtime": 600.0},
        {"text": "pick up handoff-mail-automation per the slug", "mtime": 700.0},
    ]
    isc.attribute_sessions(inis, genesis)
    assert inis[0]["session_count"] == 2
    assert inis[0]["last_session"] == 700.0


def test_attribute_sessions_zero_when_unreferenced():
    inis = [{"slug": "ghost-initiative", "docs": [
        {"path": "/r/claudedocs/handoff-ghost-initiative.md", "date": None}]}]
    isc.attribute_sessions(inis, [{"text": "nothing relevant", "mtime": 1.0}])
    assert inis[0]["session_count"] == 0
    assert inis[0]["last_session"] is None


# --------------------------------------------------------------------------- #
# Telemetry attribution + trunk catch-all (no live CH)
# --------------------------------------------------------------------------- #
def test_attribute_telemetry_segments_and_catchall():
    inis = [{"slug": "mail-automation", "repo": "/home/u/workspace/devrc"}]
    rows = [
        {"branch": "feat/mail-automation", "cwd": "/home/u/workspace/devrc", "n": 12, "last_ts": "2026-06-30 10:00:00"},
        {"branch": "main", "cwd": "/home/u/workspace/devrc", "n": 40, "last_ts": "2026-06-30 09:00:00"},
        {"branch": "feat/unknown-thing", "cwd": "/home/u/workspace/devrc", "n": 5, "last_ts": "2026-06-29 09:00:00"},
    ]
    catchall = isc.attribute_telemetry(inis, rows, ["/home/u/workspace/devrc"])
    assert inis[0]["telem_events"] == 12
    # main (40) + unmatched feat/unknown-thing (5) -> 45 unsegmented.
    assert catchall["/home/u/workspace/devrc"]["events"] == 45


def test_attribute_telemetry_none_rows_is_safe():
    inis = [{"slug": "x", "repo": "/r"}]
    assert isc.attribute_telemetry(inis, None, ["/r"]) == {}
    assert inis[0]["telem_events"] == 0
    assert inis[0]["telem_last"] is None


def test_ch_ts_to_epoch_is_utc_not_local():
    # #1: the ClickHouse `ts` column is UTC (emit uses `date -u`), so the wall-clock
    # string must convert as UTC — NOT the host's local zone, or every relative-age
    # is skewed by the UTC offset. Build the expected epoch with calendar.timegm so
    # the assertion is independent of the machine's TZ.
    import calendar
    expected = calendar.timegm((2026, 6, 30, 12, 34, 56, 0, 0, 0))
    assert isc.ch_ts_to_epoch("2026-06-30 12:34:56") == float(expected)
    # Fractional-seconds variant the column actually returns must also parse as UTC.
    assert isc.ch_ts_to_epoch("2026-06-30 12:34:56.789") == float(expected) + 0.789
    assert isc.ch_ts_to_epoch(None) is None
    assert isc.ch_ts_to_epoch("garbage") is None


def test_ch_ts_to_epoch_independent_of_local_tz(monkeypatch):
    # Force a non-UTC TZ and confirm the result is unchanged (proves UTC parsing).
    import calendar
    import time as _time
    expected = float(calendar.timegm((2026, 1, 15, 8, 0, 0, 0, 0, 0)))
    monkeypatch.setenv("TZ", "America/New_York")
    _time.tzset()
    try:
        assert isc.ch_ts_to_epoch("2026-01-15 08:00:00") == expected
    finally:
        monkeypatch.delenv("TZ", raising=False)
        _time.tzset()


# --------------------------------------------------------------------------- #
# build_report orchestration with stubbed I/O (no git/gh/CH/transcripts)
# --------------------------------------------------------------------------- #
def test_build_report_end_to_end_no_telemetry(tmp_path, monkeypatch):
    # A fake repo with one dated handoff.
    repo = tmp_path / "myrepo"
    (repo / "claudedocs").mkdir(parents=True)
    doc = repo / "claudedocs" / "handoff-mail-automation-2026-06-30.md"
    doc.write_text(NEXT_DOC)

    # Stub the external-process + transcript I/O so the test is hermetic.
    monkeypatch.setattr(isc, "git_branches", lambda r: ["feat/mail-automation", "main"])
    monkeypatch.setattr(isc, "git_default_branch", lambda r: "main")
    monkeypatch.setattr(isc, "git_commits_in_window", lambda r, b, d, db=None: (3, 1_000.0))
    monkeypatch.setattr(isc, "gh_open_prs", lambda r: [
        {"number": 42, "title": "mail extractor", "headRefName": "feat/mail-automation"}])
    monkeypatch.setattr(isc, "gh_merged_prs", lambda r, d: [])
    monkeypatch.setattr(isc, "session_genesis_refs", lambda root, d: [])

    now = 2_000.0  # last_commit at 1000 -> age 1000s -> active
    report = isc.build_report(2, repos=[str(repo)], client=None, now=now)

    assert report["telemetry_available"] is False
    inis = report["by_repo"][str(repo)]
    assert len(inis) == 1
    ini = inis[0]
    assert ini["slug"] == "mail-automation"
    assert ini["commits"] == 3
    assert ini["momentum"] == "active"
    assert ini["open_prs"] == [{"number": 42, "title": "mail extractor"}]
    assert ini["next_step"].startswith("Ship the extractor")

    # Render must not raise and must include the slug + telemetry-off note.
    txt = isc.render(report, now=now)
    assert "mail-automation" in txt
    assert "telemetry OFF" in txt


def test_render_emits_trunk_catchall(monkeypatch, tmp_path):
    repo = tmp_path / "r2"
    (repo / "claudedocs").mkdir(parents=True)
    (repo / "claudedocs" / "handoff-thing.md").write_text("# Handoff: thing\n## Next steps\n1. go\n")
    monkeypatch.setattr(isc, "git_branches", lambda r: ["main"])
    monkeypatch.setattr(isc, "git_default_branch", lambda r: "main")
    monkeypatch.setattr(isc, "gh_open_prs", lambda r: [])
    monkeypatch.setattr(isc, "gh_merged_prs", lambda r, d: [])
    monkeypatch.setattr(isc, "session_genesis_refs", lambda root, d: [])

    class FakeClient:
        def rows(self, sql):
            return [{"branch": "main", "cwd": str(repo), "n": 99, "last_ts": "2026-06-30 10:00:00"}]

    report = isc.build_report(14, repos=[str(repo)], client=FakeClient(), now=2_000_000_000.0)
    assert report["telemetry_available"] is True
    txt = isc.render(report, now=2_000_000_000.0)
    assert "unsegmented trunk/main work" in txt
    assert "ev:99" in txt
