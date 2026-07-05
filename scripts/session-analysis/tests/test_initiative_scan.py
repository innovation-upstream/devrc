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
# Git worktree dedup (#worktree): collapse linked worktrees to canonical repo
# --------------------------------------------------------------------------- #
def test_discover_repos_collapses_worktrees_to_one_canonical(monkeypatch, tmp_path):
    # Three candidate dirs: the MAIN worktree (its .git is a directory) and two
    # linked worktrees of the same repo. All three share ONE git-common-dir, so
    # discover_repos must fold them into the single main worktree.
    main = tmp_path / "civit" / "datapacket-talos"
    wt1 = tmp_path / "civit" / "datapacket-talos-review-sandbox"
    wt2 = tmp_path / "civit" / "datapacket-talos-flagger-autoscaler"
    for d in (main, wt1, wt2):
        (d / "claudedocs").mkdir(parents=True)
        (d / "claudedocs" / "handoff-thing.md").write_text("# Handoff: thing\n")
    common = str(main / ".git")  # the main worktree's .git directory
    (main / ".git").mkdir()  # main worktree has a .git DIRECTORY

    monkeypatch.setattr(isc, "_candidate_repo_dirs",
                        lambda ws: sorted(str(d) for d in (main, wt1, wt2)))

    # Every dir is a worktree of the same repo -> same common-dir.
    def fake_common(path):
        return common

    monkeypatch.setattr(isc, "_git_common_dir", fake_common)

    repos = isc.discover_repos(str(tmp_path))
    assert repos == [str(main)]  # collapsed to the main worktree only


def test_discover_repos_falls_back_to_main_toplevel_when_only_linked(
        monkeypatch, tmp_path):
    # Only linked worktrees carry handoffs (the main worktree isn't a candidate).
    # Fall back to the main worktree's toplevel (common-dir's parent) if it exists.
    main = tmp_path / "repo"
    main.mkdir()
    (main / ".git").mkdir()
    common = str(main / ".git")
    wt = tmp_path / "repo-wt"
    (wt / "claudedocs").mkdir(parents=True)

    monkeypatch.setattr(isc, "_candidate_repo_dirs", lambda ws: [str(wt)])
    monkeypatch.setattr(isc, "_git_common_dir", lambda path: common)

    repos = isc.discover_repos(str(tmp_path))
    # Folds to the main toplevel (parent of <main>/.git), not the linked worktree.
    assert repos == [str(main)]


def test_discover_repos_non_git_dir_survives_as_own_repo(monkeypatch, tmp_path):
    # A plain (non-git) dir with a claudedocs/ has no git-common-dir -> it must
    # survive as its own repo (graceful fallback, never crash, never dropped).
    plain = tmp_path / "loose-notes"
    (plain / "claudedocs").mkdir(parents=True)

    monkeypatch.setattr(isc, "_candidate_repo_dirs", lambda ws: [str(plain)])
    monkeypatch.setattr(isc, "_git_common_dir", lambda path: None)  # not a repo

    repos = isc.discover_repos(str(tmp_path))
    assert repos == [str(plain)]


def test_cwd_in_linked_worktree_maps_to_canonical_not_unknown():
    # A telemetry row whose cwd lives inside a LINKED worktree (a dir that is NOT
    # itself a discovered repo) must attribute to the canonical parent repo, NOT
    # fall into the `(unknown repo)` bucket.
    canonical = "/home/u/workspace/civit/datapacket-talos"
    linked = "/home/u/workspace/civit/datapacket-talos-review-sandbox"
    inis = [{"slug": "flagger-autoscaler", "repo": canonical}]
    rows = [
        {"branch": "feat/flagger-autoscaler",
         "cwd": linked + "/charts",
         "n": 17, "last_ts": "2026-06-30 10:00:00"},
        {"branch": "main", "cwd": linked, "n": 25, "last_ts": "2026-06-30 09:00:00"},
    ]
    catchall = isc.attribute_telemetry(
        inis, rows, [canonical], worktree_map={linked: canonical})
    # The feature-branch row credits the initiative; the trunk row lands in the
    # CANONICAL repo's catch-all, NOT `(unknown repo)`.
    assert inis[0]["telem_events"] == 17
    assert "(unknown repo)" not in catchall
    assert catchall[canonical]["events"] == 25


def test_cwd_without_worktree_map_still_unknown():
    # Control: same linked cwd, but no worktree_map -> it stays `(unknown repo)`,
    # proving the mapping is what rescues it (not an accidental prefix match).
    canonical = "/home/u/workspace/civit/datapacket-talos"
    linked = "/home/u/workspace/civit/datapacket-talos-review-sandbox"
    inis = [{"slug": "x", "repo": canonical}]
    rows = [{"branch": "main", "cwd": linked, "n": 25, "last_ts": "2026-06-30 09:00:00"}]
    catchall = isc.attribute_telemetry(inis, rows, [canonical])  # no worktree_map
    assert "(unknown repo)" in catchall
    assert catchall["(unknown repo)"]["events"] == 25


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


# --------------------------------------------------------------------------- #
# text_tokens — free-text tokenization (prose titles, tmux pane titles)
# --------------------------------------------------------------------------- #
def test_text_tokens_splits_prose_like_slugs():
    # Prose splits on any non-alnum run; same short/stop/date filters as slug_tokens.
    # 'Continue' is a TITLE_STOP action verb -> dropped; topic words survive.
    assert isc.text_tokens("Continue clawgate agent loop soak testing") == [
        "clawgate", "agent", "loop", "soak", "testing"]


def test_text_tokens_drops_action_verbs_keeps_topic():
    # Session-summary verbs ('Resume','Monitor','Build') are noise; the topic remains.
    assert isc.text_tokens("Resume Monitor Build sysredis buffer") == [
        "sysredis", "buffer"]


def test_text_tokens_drops_stopwords_dates_and_short():
    # 'and' is a stop word, '2026-07-05' a date, 'go' too short -> all dropped.
    assert isc.text_tokens("Faro and RUM 2026-07-05 go widen") == [
        "faro", "rum", "widen"]


# --------------------------------------------------------------------------- #
# resolve_cwd_repo — cwd -> canonical repo (shared by telemetry + tmux)
# --------------------------------------------------------------------------- #
def test_resolve_cwd_repo_prefix_match():
    repos = ["/home/u/workspace/devrc", "/home/u/workspace/civit/dp"]
    assert isc.resolve_cwd_repo("/home/u/workspace/devrc/scripts", repos) == \
        "/home/u/workspace/devrc"
    # Longest-prefix wins: the nested repo, not a shorter accidental prefix.
    assert isc.resolve_cwd_repo("/home/u/workspace/civit/dp", repos) == \
        "/home/u/workspace/civit/dp"


def test_resolve_cwd_repo_unknown_is_none():
    assert isc.resolve_cwd_repo("/home/u/taxes/2025", ["/home/u/workspace/devrc"]) is None
    assert isc.resolve_cwd_repo(None, ["/r"]) is None


def test_resolve_cwd_repo_via_worktree_map():
    repos = ["/home/u/workspace/civit/dp"]
    wt_map = {"/home/u/workspace/civit/dp-sandbox": "/home/u/workspace/civit/dp"}
    assert isc.resolve_cwd_repo("/home/u/workspace/civit/dp-sandbox/x", repos, wt_map) == \
        "/home/u/workspace/civit/dp"


# --------------------------------------------------------------------------- #
# best_title_match — pane title tokens -> most-specific initiative
# --------------------------------------------------------------------------- #
def test_best_title_match_by_slug_token():
    inis = [{"slug": "faro-rum-widening", "title": "Faro RUM widening ramp"}]
    toks = set(isc.text_tokens("Wire Faro to main civitai app with Zach review"))
    assert isc.best_title_match(toks, inis) is inis[0]


def test_best_title_match_prefers_more_slug_overlap():
    # A pane naming agent+loop+clawgate should credit agent-loop-close (3 slug
    # tokens overlap), NOT the chat-polish sibling (only 'clawgate' overlaps).
    inis = [
        {"slug": "clawgate-agent-loop-close", "title": "clawgate: the agent loop closes"},
        {"slug": "clawgate-chat-polish", "title": "clawgate agent-chat polish"},
    ]
    toks = set(isc.text_tokens("Continue clawgate agent loop production soak testing"))
    assert isc.best_title_match(toks, inis)["slug"] == "clawgate-agent-loop-close"


def test_best_title_match_needs_distinctive_overlap():
    # One generic title-only word ('review') must NOT link — no slug-token overlap
    # and fewer than two title-token overlaps.
    inis = [{"slug": "faro-rum-widening", "title": "Faro RUM review ramp"}]
    toks = set(isc.text_tokens("Audit PR 355 review"))
    assert isc.best_title_match(toks, inis) is None


def test_best_title_match_empty_when_no_initiatives():
    assert isc.best_title_match({"clawgate"}, []) is None


def test_best_title_match_generic_shared_word_does_not_link():
    # 'session' is shared across many initiatives -> low IDF -> a pane that overlaps
    # ONLY on 'session' must not link (the scratch8 "Resume session <id>" false hit).
    inis = [
        {"slug": "app-blocks-dev-live-session", "title": "App Blocks dev live session"},
        {"slug": "app-blocks-session", "title": "App Blocks session civitai"},
        {"slug": "app-blocks-review-session", "title": "App Blocks review session"},
    ]
    toks = set(isc.text_tokens("Resume session 868k8b9f6"))
    assert isc.best_title_match(toks, inis) is None


def test_best_title_match_distinctive_token_beats_generic(monkeypatch):
    # 'tekton' is unique (idf 1.0); 'app'/'blocks' are shared (low idf). A pane
    # naming tekton links to the tekton initiative, not an app-blocks sibling that
    # only shares generic words.
    inis = [
        {"slug": "tekton-control-plane-ha", "title": "tekton control plane ha"},
        {"slug": "app-blocks-ux", "title": "App Blocks UX readiness"},
        {"slug": "app-blocks-followups", "title": "App Blocks follow-ups"},
    ]
    toks = set(isc.text_tokens("Review cordoned build node and Tekton pipelines"))
    assert isc.best_title_match(toks, inis)["slug"] == "tekton-control-plane-ha"


# --------------------------------------------------------------------------- #
# match_tmux_to_initiatives — attach live sessions, scoped by repo
# --------------------------------------------------------------------------- #
def test_match_tmux_attaches_session_scoped_by_repo():
    devrc, civit = "/home/u/workspace/devrc", "/home/u/workspace/civit/dp"
    inis = [
        {"slug": "faro-rum-widening", "title": "Faro RUM widening", "repo": civit},
        {"slug": "clawgate-chat-polish", "title": "clawgate chat polish", "repo": devrc},
    ]
    panes = [
        {"session": "scratch4", "window": "2", "cwd": civit, "command": "claude",
         "title": "Wire Faro to main civitai app with Zach review"},
        {"session": "1", "window": "3", "cwd": devrc, "command": "claude",
         "title": "clawgate chat polish soak"},
    ]
    # scratch4 is a codenamed scratchpad; session '1' is the un-codenamed main tmux.
    unmatched = isc.match_tmux_to_initiatives(inis, panes, [devrc, civit],
                                              codenames={"scratch4": "Vapor"})
    assert inis[0]["tmux_sessions"] == {"Vapor-2"}
    assert inis[1]["tmux_sessions"] == {"main:1-3"}
    assert unmatched == []


def test_match_tmux_wrong_repo_does_not_cross_credit():
    # A 'faro' pane whose cwd is devrc must NOT credit the civit faro initiative.
    devrc, civit = "/home/u/workspace/devrc", "/home/u/workspace/civit/dp"
    inis = [{"slug": "faro-rum-widening", "title": "Faro RUM widening", "repo": civit}]
    panes = [{"session": "scratchX", "window": "1", "cwd": devrc, "command": "claude",
              "title": "Wire Faro to civitai app"}]
    unmatched = isc.match_tmux_to_initiatives(inis, panes, [devrc, civit])
    assert inis[0]["tmux_sessions"] == set()
    # devrc has no matching initiative -> the claude pane is surfaced as unmatched
    # (un-codenamed -> marked main:).
    assert len(unmatched) == 1
    assert unmatched[0]["id"] == "main:scratchX-1"
    assert unmatched[0]["repo"] == devrc


def test_match_tmux_non_claude_unmatched_pane_ignored():
    # A plain zsh pane in an unknown dir is neither matched nor reported as unmatched.
    inis = [{"slug": "x", "title": "X", "repo": "/r"}]
    panes = [{"session": "scratch5", "window": "1", "cwd": "/home/u/taxes/2025",
              "command": "zsh", "title": "nixos"}]
    unmatched = isc.match_tmux_to_initiatives(inis, panes, ["/r"])
    assert inis[0]["tmux_sessions"] == set()
    assert unmatched == []


def test_match_tmux_two_windows_same_session_two_initiatives():
    # The core reason for window granularity: one session, two windows, two distinct
    # initiatives -> each initiative points at its OWN <session>-<window>.
    civit = "/home/u/workspace/civit/dp"
    inis = [
        {"slug": "sysredis-buffer", "title": "sysRedis buffer soft-dependency",
         "repo": civit},
        {"slug": "sysredis-wedge-latency", "title": "sysRedis wedge latency",
         "repo": civit},
    ]
    panes = [
        {"session": "8", "window": "1", "cwd": civit, "command": "claude",
         "title": "Monitor sysredis wedge fixes"},
        {"session": "8", "window": "3", "cwd": civit, "command": "claude",
         "title": "Continue sysredis buffer soft-dependency work"},
    ]
    isc.match_tmux_to_initiatives(inis, panes, [civit])
    assert inis[0]["tmux_sessions"] == {"main:8-3"}   # buffer -> main tmux window 3
    assert inis[1]["tmux_sessions"] == {"main:8-1"}   # wedge  -> main tmux window 1


def test_match_tmux_same_window_dedups():
    # Two panes in the SAME window matching one initiative dedup to one id.
    civit = "/home/u/workspace/civit/dp"
    inis = [{"slug": "sysredis-buffer", "title": "sysRedis buffer", "repo": civit}]
    panes = [
        {"session": "8", "window": "2", "cwd": civit, "command": "claude",
         "title": "Continue sysredis buffer work"},
        {"session": "8", "window": "2", "cwd": civit, "command": "claude",
         "title": "Monitor sysredis buffer fixes"},
    ]
    isc.match_tmux_to_initiatives(inis, panes, [civit])
    assert inis[0]["tmux_sessions"] == {"main:8-2"}


def test_pane_id_formats_session_window():
    # Un-codenamed session -> marked main: (persistent "main tmux").
    assert isc.pane_id({"session": "8", "window": "1"}) == "main:8-1"
    assert isc.pane_id({"session": "wheat", "window": "3"}) == "main:wheat-3"
    # Missing window -> bare (marked) session, never a dangling 'session-'.
    assert isc.pane_id({"session": "scratch7", "window": ""}) == "main:scratch7"
    assert isc.pane_id({"session": "scratch7"}) == "main:scratch7"


def test_pane_id_translates_scratch_codename():
    # A scratchpad session shows its hotkey codename; a main-tmux session is marked.
    codes = {"scratch4": "Vapor", "scratch11": "wheat"}
    assert isc.pane_id({"session": "scratch4", "window": "2"}, codes) == "Vapor-2"
    assert isc.pane_id({"session": "scratch11", "window": "1"}, codes) == "wheat-1"
    assert isc.pane_id({"session": "8", "window": "3"}, codes) == "main:8-3"  # no codename


def test_load_scratch_codenames_parses_slots(tmp_path):
    # Mirrors the real tmux-scratch-monitor.sh SLOTS format.
    script = tmp_path / "tmux-scratch-monitor.sh"
    script.write_text(
        'SLOTS=(\n'
        '    "g:scratch:#b8bb26:grove"\n'
        '    "V:scratch4:#83a598:Vapor"\n'
        '    "w:scratch11:#ebdbb2:wheat"\n'
        ')\n'
        'printf "unrelated:line:#nothex:x"\n')  # must not be parsed as a slot
    codes = isc.load_scratch_codenames(script)
    assert codes == {"scratch": "grove", "scratch4": "Vapor", "scratch11": "wheat"}


def test_load_scratch_codenames_missing_file_is_empty():
    assert isc.load_scratch_codenames("/no/such/file.sh") == {}


def test_load_scratch_codenames_real_file_has_vapor():
    # Guards against the on-disk SLOTS format drifting away from the parser.
    codes = isc.load_scratch_codenames()  # the repo's real tmux-scratch-monitor.sh
    assert codes.get("scratch4") == "Vapor"
    assert codes.get("scratch11") == "wheat"


def test_match_tmux_uses_codenames_end_to_end():
    civit = "/home/u/workspace/civit/dp"
    inis = [{"slug": "faro-rum-widening", "title": "Faro RUM widening", "repo": civit}]
    panes = [{"session": "scratch4", "window": "2", "cwd": civit, "command": "claude",
              "title": "Wire Faro to civitai app"}]
    isc.match_tmux_to_initiatives(inis, panes, [civit], codenames={"scratch4": "Vapor"})
    assert inis[0]["tmux_sessions"] == {"Vapor-2"}


def test_tmux_session_sort_key_natural_order():
    names = ["scratch10", "scratch2", "8", "1", "scratch"]
    assert sorted(names, key=isc._tmux_session_sort_key) == [
        "1", "8", "scratch", "scratch2", "scratch10"]


def test_tmux_session_sort_key_orders_windows_within_session():
    names = ["8-3", "8-1", "8-10", "1-2", "scratch2-1"]
    assert sorted(names, key=isc._tmux_session_sort_key) == [
        "1-2", "8-1", "8-3", "8-10", "scratch2-1"]


def test_tmux_session_sort_key_handles_main_and_codename_ids():
    # Real ids carry a main: marker or a codename; windows still order within a group.
    names = ["main:8-3", "main:8-1", "Vapor-2", "main:2-1"]
    assert sorted(names, key=isc._tmux_session_sort_key) == [
        "Vapor-2", "main:2-1", "main:8-1", "main:8-3"]


# --------------------------------------------------------------------------- #
# collect_tmux_panes — parsing the tab-delimited tmux output
# --------------------------------------------------------------------------- #
def test_collect_tmux_panes_parses_and_handles_empty_title(monkeypatch):
    out = ("1\t1\t/home/u/workspace/devrc\tclaude\tContinue clawgate loop\n"
           "scratch5\t2\t/home/u/taxes/2025\tzsh\t\n")  # empty title -> ""
    monkeypatch.setattr(isc, "_run", lambda cmd, timeout=20.0: out)
    panes = isc.collect_tmux_panes()
    assert panes[0] == {"session": "1", "window": "1",
                        "cwd": "/home/u/workspace/devrc",
                        "command": "claude", "title": "Continue clawgate loop"}
    assert panes[1]["window"] == "2"
    assert panes[1]["title"] == ""


def test_collect_tmux_panes_empty_when_no_server(monkeypatch):
    monkeypatch.setattr(isc, "_run", lambda cmd, timeout=20.0: "")
    assert isc.collect_tmux_panes() == []


# --------------------------------------------------------------------------- #
# build_report + render with --tmux (panes injected for hermeticity)
# --------------------------------------------------------------------------- #
def test_build_report_tmux_annotates_and_lists_unmatched(tmp_path, monkeypatch):
    repo = tmp_path / "myrepo"
    (repo / "claudedocs").mkdir(parents=True)
    (repo / "claudedocs" / "handoff-mail-automation-2026-06-30.md").write_text(NEXT_DOC)

    monkeypatch.setattr(isc, "git_branches", lambda r: ["main"])
    monkeypatch.setattr(isc, "git_default_branch", lambda r: "main")
    monkeypatch.setattr(isc, "gh_open_prs", lambda r: [])
    monkeypatch.setattr(isc, "gh_merged_prs", lambda r, d: [])
    monkeypatch.setattr(isc, "session_genesis_refs", lambda root, d: [])
    # Isolate the window/unmatched logic from the real codename table (tested apart).
    monkeypatch.setattr(isc, "load_scratch_codenames", lambda *a, **k: {})

    panes = [
        {"session": "scratch9", "window": "1", "cwd": str(repo), "command": "claude",
         "title": "Resume mail automation extractor work"},
        {"session": "scratch2", "window": "4", "cwd": str(repo), "command": "claude",
         "title": "Some brand new unrelated exploration thread"},
    ]
    report = isc.build_report(14, repos=[str(repo)], client=None,
                              now=2_000.0, include_tmux=True, panes=panes)
    assert report["tmux_enabled"] is True
    ini = report["by_repo"][str(repo)][0]
    # Empty codename map -> both sessions fall through to the main: marker.
    assert ini["tmux_sessions"] == ["main:scratch9-1"]
    # The unrelated pane is surfaced as live-but-unmatched, by its <session>-<window>.
    assert any(u["id"] == "main:scratch2-4" for u in report["tmux_unmatched"])

    txt = isc.render(report, now=2_000.0)
    assert "[tmux:main:scratch9-1]" in txt
    assert "live claude sessions — no matched initiative" in txt
    assert "main:scratch2-4" in txt


def test_build_report_tmux_applies_codenames(tmp_path, monkeypatch):
    # End-to-end: a scratch4 pane renders under its Vapor codename in the report.
    repo = tmp_path / "myrepo"
    (repo / "claudedocs").mkdir(parents=True)
    (repo / "claudedocs" / "handoff-mail-automation-2026-06-30.md").write_text(NEXT_DOC)
    monkeypatch.setattr(isc, "git_branches", lambda r: ["main"])
    monkeypatch.setattr(isc, "git_default_branch", lambda r: "main")
    monkeypatch.setattr(isc, "gh_open_prs", lambda r: [])
    monkeypatch.setattr(isc, "gh_merged_prs", lambda r, d: [])
    monkeypatch.setattr(isc, "session_genesis_refs", lambda root, d: [])
    monkeypatch.setattr(isc, "load_scratch_codenames", lambda *a, **k: {"scratch4": "Vapor"})

    panes = [{"session": "scratch4", "window": "2", "cwd": str(repo),
              "command": "claude", "title": "Resume mail automation extractor work"}]
    report = isc.build_report(14, repos=[str(repo)], client=None,
                              now=2_000.0, include_tmux=True, panes=panes)
    assert report["by_repo"][str(repo)][0]["tmux_sessions"] == ["Vapor-2"]
    assert "[tmux:Vapor-2]" in isc.render(report, now=2_000.0)


def test_build_report_tmux_no_session_marker(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    (repo / "claudedocs").mkdir(parents=True)
    (repo / "claudedocs" / "handoff-lonely.md").write_text(
        "# Handoff: lonely\n## Next steps\n1. go\n")
    monkeypatch.setattr(isc, "git_branches", lambda r: ["main"])
    monkeypatch.setattr(isc, "git_default_branch", lambda r: "main")
    monkeypatch.setattr(isc, "gh_open_prs", lambda r: [])
    monkeypatch.setattr(isc, "gh_merged_prs", lambda r, d: [])
    monkeypatch.setattr(isc, "session_genesis_refs", lambda root, d: [])

    # No panes at all -> initiative shows [no session].
    report = isc.build_report(14, repos=[str(repo)], client=None,
                              now=2_000.0, include_tmux=True, panes=[])
    txt = isc.render(report, now=2_000.0)
    assert "[no session]" in txt


def test_build_report_tmux_suppressed_when_no_server(tmp_path, monkeypatch):
    # --tmux on a host with NO tmux server (live read yields []) suppresses the column
    # entirely rather than tagging every initiative "[no session]".
    repo = tmp_path / "r"
    (repo / "claudedocs").mkdir(parents=True)
    (repo / "claudedocs" / "handoff-lonely.md").write_text(
        "# Handoff: lonely\n## Next steps\n1. go\n")
    monkeypatch.setattr(isc, "git_branches", lambda r: ["main"])
    monkeypatch.setattr(isc, "git_default_branch", lambda r: "main")
    monkeypatch.setattr(isc, "gh_open_prs", lambda r: [])
    monkeypatch.setattr(isc, "gh_merged_prs", lambda r, d: [])
    monkeypatch.setattr(isc, "session_genesis_refs", lambda root, d: [])
    monkeypatch.setattr(isc, "collect_tmux_panes", lambda: [])  # no server

    # panes=None -> live read path; collect returns [] -> column disabled.
    report = isc.build_report(14, repos=[str(repo)], client=None,
                              now=2_000.0, include_tmux=True)
    assert report["tmux_enabled"] is False
    txt = isc.render(report, now=2_000.0)
    assert "[no session]" not in txt
    assert "[tmux:" not in txt
