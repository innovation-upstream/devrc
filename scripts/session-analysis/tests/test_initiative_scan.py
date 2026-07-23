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


def test_all_next_steps_returns_every_item_flattened():
    steps = isc.parse_all_next_steps(NEXT_DOC)
    assert steps == ["Ship the extractor on the live mail table — highest leverage.",
                     "Lower: rotate the OpenRouter key."]


def test_all_next_steps_stops_at_next_h2_and_empty_without_section():
    doc = "## Next steps\n- a\n- b\n\n## Gotchas\n- not counted\n"
    assert isc.parse_all_next_steps(doc) == ["a", "b"]
    assert isc.parse_all_next_steps("## Goal\nx\n") == []


# --------------------------------------------------------------------------- #
# Summary / goal extraction (parse_summary)
# --------------------------------------------------------------------------- #
def test_summary_inline_bold_goal_marker():
    doc = ("# Handoff — X, 2026-07-22\n\n"
           "**Goal:** consolidate the scan output into a durable store.\n\n"
           "## Status\nsomething else\n")
    assert isc.parse_summary(doc) == "consolidate the scan output into a durable store."


def test_summary_plain_objective_marker_flattens_markdown():
    assert isc.parse_summary("Objective: build **the** thing `fast`\n") == "build the thing fast"


def test_summary_status_heading_takes_paragraph_beneath():
    doc = "# T\n\n## Status\n\nWe are mid-flight on the migration.\n\n## Next steps\n1. x\n"
    assert isc.parse_summary(doc) == "We are mid-flight on the migration."


def test_summary_empty_marker_falls_to_paragraph_beneath():
    doc = "# T\n\n**Goal:**\n\nDeferred goal paragraph here.\n"
    assert isc.parse_summary(doc) == "Deferred goal paragraph here."


def test_summary_first_paragraph_fallback_when_no_marker():
    doc = "# Title only heading\n\nResumed from the earlier handoff and shipped the thing.\n"
    assert isc.parse_summary(doc) == "Resumed from the earlier handoff and shipped the thing."


def test_summary_none_when_no_prose():
    assert isc.parse_summary("# Only a title\n") is None
    assert isc.parse_summary("") is None


def test_summary_caps_length_with_ellipsis():
    s = isc.parse_summary("Goal: " + "word " * 80)
    assert s is not None
    assert len(s) <= isc.SUMMARY_MAX + 1  # +1 for the trailing ellipsis
    assert s.endswith("…")


def test_summary_carried_through_read_handoff_and_cluster(tmp_path):
    repo = tmp_path / "repo"
    (repo / "claudedocs").mkdir(parents=True)
    doc = repo / "claudedocs" / "handoff-thing-2026-07-20.md"
    doc.write_text("# Thing — 2026-07-20\n\n**Goal:** do the thing well.\n")
    parsed = isc.read_handoff(str(doc))
    assert parsed["summary"] == "do the thing well."
    inis = isc.cluster_handoffs([parsed])
    assert inis[0]["summary"] == "do the thing well."


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
def _doc(repo, slug, date, mtime, title="t", next_step="ns", path=None, summary="s"):
    return {
        "repo": repo, "slug": slug, "date": date, "mtime": mtime,
        "title": title, "summary": summary, "next_step": next_step,
        "open_investigations": [],
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
    # build_report now single-walks transcripts via collect_session_records (genesis is
    # derived from it); stub that so the orchestration test stays hermetic.
    monkeypatch.setattr(isc, "collect_session_records", lambda root, d, n=5: [])

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
    # build_report now single-walks transcripts via collect_session_records (genesis is
    # derived from it); stub that so the orchestration test stays hermetic.
    monkeypatch.setattr(isc, "collect_session_records", lambda root, d, n=5: [])

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


def test_best_title_match_shared_single_token_does_not_link():
    # 'grafana' is shared across two initiatives -> a pane overlapping ONLY 'grafana'
    # links to neither (the false grafana-alert-provisioning-drift match).
    inis = [
        {"slug": "grafana-alert-provisioning-drift", "title": "Grafana alerting drift"},
        {"slug": "alert-chaos-grafana-sqlite", "title": "alert chaos Grafana sqlite"},
    ]
    toks = set(isc.text_tokens("Build Support 10x Grafana dashboard"))
    assert isc.best_title_match(toks, inis) is None


def test_best_title_match_unique_single_token_still_links():
    # A token unique to ONE initiative (faro) still matches on its own.
    inis = [
        {"slug": "faro-rum-widening", "title": "Faro RUM widening"},
        {"slug": "sysredis-buffer", "title": "sysRedis buffer"},
    ]
    toks = set(isc.text_tokens("Wire Faro to civitai app"))
    assert isc.best_title_match(toks, inis)["slug"] == "faro-rum-widening"


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
    # Mirrors the real tmux-scratch-slots.sh SCRATCH_SLOTS format (session:key:color:name).
    script = tmp_path / "tmux-scratch-slots.sh"
    script.write_text(
        'SCRATCH_SLOTS=(\n'
        '    "scratch:g:#b8bb26:grove"\n'
        '    "scratch4:V:#83a598:Vapor"\n'
        '    "scratch11:w:#ebdbb2:wheat"\n'
        ')\n'
        'printf "unrelated:line:#nothex:x"\n')  # must not be parsed as a slot
    codes = isc.load_scratch_codenames(script)
    assert codes == {"scratch": "grove", "scratch4": "Vapor", "scratch11": "wheat"}


def test_load_scratch_codenames_missing_file_is_empty():
    assert isc.load_scratch_codenames("/no/such/file.sh") == {}


def test_load_scratch_codenames_real_file_has_vapor():
    # Guards against the on-disk SCRATCH_SLOTS format drifting away from the parser.
    codes = isc.load_scratch_codenames()  # the repo's real tmux-scratch-slots.sh
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
    # build_report now single-walks transcripts via collect_session_records (genesis is
    # derived from it); stub that so the orchestration test stays hermetic.
    monkeypatch.setattr(isc, "collect_session_records", lambda root, d, n=5: [])
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
    # build_report now single-walks transcripts via collect_session_records (genesis is
    # derived from it); stub that so the orchestration test stays hermetic.
    monkeypatch.setattr(isc, "collect_session_records", lambda root, d, n=5: [])
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
    # build_report now single-walks transcripts via collect_session_records (genesis is
    # derived from it); stub that so the orchestration test stays hermetic.
    monkeypatch.setattr(isc, "collect_session_records", lambda root, d, n=5: [])

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
    # build_report now single-walks transcripts via collect_session_records (genesis is
    # derived from it); stub that so the orchestration test stays hermetic.
    monkeypatch.setattr(isc, "collect_session_records", lambda root, d, n=5: [])
    monkeypatch.setattr(isc, "collect_tmux_panes", lambda: [])  # no server

    # panes=None -> live read path; collect returns [] -> column disabled.
    report = isc.build_report(14, repos=[str(repo)], client=None,
                              now=2_000.0, include_tmux=True)
    assert report["tmux_enabled"] is False
    txt = isc.render(report, now=2_000.0)
    assert "[no session]" not in txt
    assert "[tmux:" not in txt


# --------------------------------------------------------------------------- #
# doc freshness + window filtering (the mtime-clobber fix)
# --------------------------------------------------------------------------- #
def test_doc_touch_epoch_prefers_authored_date_over_mtime():
    import calendar
    authored = float(calendar.timegm((2026, 6, 16, 0, 0, 0, 0, 0, 0)))
    # A clobbered-recent fs-mtime must NOT win over the filename's authored date.
    ini = {"date": "2026-06-16", "doc_mtime": 9_999_999_999.0}
    assert isc.doc_touch_epoch(ini) == authored


def test_doc_touch_epoch_falls_back_to_mtime_when_dateless():
    assert isc.doc_touch_epoch({"date": None, "doc_mtime": 123.0}) == 123.0
    assert isc.doc_touch_epoch({"doc_mtime": 123.0}) == 123.0


def _stub_no_external_io(monkeypatch):
    monkeypatch.setattr(isc, "git_branches", lambda r: ["main"])
    monkeypatch.setattr(isc, "git_default_branch", lambda r: "main")
    monkeypatch.setattr(isc, "gh_open_prs", lambda r: [])
    monkeypatch.setattr(isc, "gh_merged_prs", lambda r, d: [])
    # build_report now single-walks transcripts via collect_session_records (genesis is
    # derived from it); stub that so the orchestration test stays hermetic.
    monkeypatch.setattr(isc, "collect_session_records", lambda root, d, n=5: [])


def test_build_report_windows_out_stale_dated_handoff(tmp_path, monkeypatch):
    # The reported bug: an old, done handoff whose fs-mtime got bulk-clobbered to
    # "recent" must NOT surface in a short window — its AUTHORED date is what counts.
    import calendar
    now = float(calendar.timegm((2026, 7, 5, 0, 0, 0, 0, 0, 0)))
    repo = tmp_path / "r"
    (repo / "claudedocs").mkdir(parents=True)
    (repo / "claudedocs" / "handoff-fresh-2026-07-04.md").write_text(
        "# Handoff: fresh\n## Next steps\n1. go\n")
    (repo / "claudedocs" / "handoff-stale-2026-06-16.md").write_text(
        "# Handoff: stale\n## Next steps\n1. go\n")  # fs-mtime real-now, but dated 06-16
    _stub_no_external_io(monkeypatch)

    fresh_only = isc.build_report(4, repos=[str(repo)], client=None, now=now)
    assert {i["slug"] for i in fresh_only["by_repo"].get(str(repo), [])} == {"fresh"}

    # Widen the window and the stale one resurfaces (not dropped, just out-of-window).
    both = isc.build_report(30, repos=[str(repo)], client=None, now=now)
    assert {i["slug"] for i in both["by_repo"].get(str(repo), [])} == {"fresh", "stale"}


def test_build_report_keeps_stale_handoff_with_live_session(tmp_path, monkeypatch):
    # An old handoff still being worked (a live tmux session on it) stays — and reads
    # active — even though its authored date is far outside the window.
    import calendar
    now = float(calendar.timegm((2026, 7, 5, 0, 0, 0, 0, 0, 0)))
    repo = tmp_path / "r"
    (repo / "claudedocs").mkdir(parents=True)
    (repo / "claudedocs" / "handoff-oldwork-2026-05-01.md").write_text(
        "# Handoff: oldwork\n## Next steps\n1. go\n")
    _stub_no_external_io(monkeypatch)
    monkeypatch.setattr(isc, "load_scratch_codenames", lambda *a, **k: {})

    panes = [{"session": "8", "window": "1", "cwd": str(repo), "command": "claude",
              "title": "Continue oldwork task"}]
    report = isc.build_report(4, repos=[str(repo)], client=None, now=now,
                              include_tmux=True, panes=panes)
    inis = report["by_repo"].get(str(repo), [])
    assert len(inis) == 1 and inis[0]["slug"] == "oldwork"
    assert inis[0]["momentum"] == "active"        # live session => touched now
    assert inis[0]["tmux_sessions"] == ["main:8-1"]


# --------------------------------------------------------------------------- #
# Recent user-message extraction + attribution (Phase A card legibility)
# --------------------------------------------------------------------------- #
import json as _json  # noqa: E402


def _jsonl_user(text, ts, cwd="/home/u/workspace/devrc", branch="feat/x"):
    """One transcript user-turn line (mirrors the real ~/.claude JSONL shape)."""
    return _json.dumps({
        "type": "user", "timestamp": ts, "cwd": cwd, "gitBranch": branch,
        "message": {"role": "user", "content": text},
    })


def _write_transcript(path, entries):
    path.write_text("\n".join(entries) + "\n")


def test_read_session_turns_collects_turns_with_ts_cwd_branch(tmp_path):
    p = tmp_path / "s.jsonl"
    _write_transcript(p, [
        _jsonl_user("<system-reminder>noise</system-reminder>   ", "2026-07-20T10:00:00Z"),
        _jsonl_user("read handoff-foo.md and start", "2026-07-20T10:01:00Z", branch="feat/foo"),
        _jsonl_user("do the next thing", "2026-07-20T10:02:00Z", branch="feat/foo"),
        _jsonl_user("[Request interrupted by user]", "2026-07-20T10:03:00Z"),
        _jsonl_user("third real turn", "2026-07-20T10:04:00Z", branch="feat/foo-2"),
    ])
    rec = isc._read_session_turns(str(p), 5)
    # genesis = FIRST genuine turn (the system-reminder + interrupt turns are skipped).
    assert rec["genesis"] == "read handoff-foo.md and start"
    assert [t["text"] for t in rec["turns"]] == [
        "read handoff-foo.md and start", "do the next thing", "third real turn"]
    assert rec["turns"][0]["ts"] is not None  # ISO timestamp parsed to epoch
    # cwd/branch come from the MOST-RECENT turn that carried them.
    assert rec["cwd"] == "/home/u/workspace/devrc"
    assert rec["branch"] == "feat/foo-2"


def test_read_session_turns_keeps_only_last_n(tmp_path):
    p = tmp_path / "s.jsonl"
    _write_transcript(p, [
        _jsonl_user(f"turn {i}", f"2026-07-20T10:0{i}:00Z") for i in range(6)])
    rec = isc._read_session_turns(str(p), 2)
    assert rec["genesis"] == "turn 0"                 # genesis independent of the window
    assert [t["text"] for t in rec["turns"]] == ["turn 4", "turn 5"]  # last 2 only


def test_read_session_turns_none_without_genuine_turn(tmp_path):
    p = tmp_path / "s.jsonl"
    _write_transcript(p, [_jsonl_user("<system-reminder>only noise</system-reminder>",
                                      "2026-07-20T10:00:00Z")])
    assert isc._read_session_turns(str(p), 5) is None


def test_read_session_turns_extracts_list_content(tmp_path):
    # content as a list of blocks -> the first text block is the turn text.
    line = _json.dumps({
        "type": "user", "timestamp": "2026-07-20T10:00:00Z",
        "cwd": "/r", "gitBranch": "feat/y",
        "message": {"role": "user",
                    "content": [{"type": "text", "text": "block-form message"}]},
    })
    p = tmp_path / "s.jsonl"
    _write_transcript(p, [line])
    rec = isc._read_session_turns(str(p), 5)
    assert rec["turns"][0]["text"] == "block-form message"


def test_first_user_turn_returns_genesis(tmp_path):
    p = tmp_path / "s.jsonl"
    _write_transcript(p, [
        _jsonl_user("<system-reminder>x</system-reminder>", "2026-07-20T10:00:00Z"),
        _jsonl_user("the real first message", "2026-07-20T10:01:00Z"),
        _jsonl_user("second", "2026-07-20T10:02:00Z"),
    ])
    assert isc._first_user_turn(str(p)) == "the real first message"


def test_collect_session_records_skips_subagents_and_old(tmp_path):
    root = tmp_path
    good = root / "proj" / "a.jsonl"
    good.parent.mkdir(parents=True)
    _write_transcript(good, [_jsonl_user("hello world", "2026-07-20T10:00:00Z")])
    sub = root / "subagents" / "b.jsonl"
    sub.parent.mkdir(parents=True)
    _write_transcript(sub, [_jsonl_user("sub msg", "2026-07-20T10:00:00Z")])
    recs = isc.collect_session_records(str(root), 3650)  # wide window -> the good one in
    genes = [r["genesis"] for r in recs]
    assert "hello world" in genes
    assert "sub msg" not in genes          # /subagents/ path excluded
    # a very tight window excludes even the just-written file (mtime older than cutoff=now)
    assert isc.collect_session_records(str(root), 0) == []


def test_session_genesis_refs_derives_from_records(tmp_path):
    p = tmp_path / "proj" / "a.jsonl"
    p.parent.mkdir(parents=True)
    _write_transcript(p, [
        _jsonl_user("genesis line", "2026-07-20T10:00:00Z"),
        _jsonl_user("later line", "2026-07-20T10:01:00Z"),
    ])
    refs = isc.session_genesis_refs(str(tmp_path), 3650)
    assert len(refs) == 1
    assert refs[0]["text"] == "genesis line"     # genesis, not the last turn
    assert "mtime" in refs[0]


def test_attribute_recent_messages_genesis_pool_desc_truncate():
    inis = [{"slug": "foo-bar", "repo": "/r",
             "docs": [{"path": "/r/claudedocs/handoff-foo-bar-2026-07-20.md",
                       "date": "2026-07-20"}]}]
    long = "y" * 250
    records = [
        {"genesis": "resume handoff-foo-bar-2026-07-20.md", "mtime": 1.0,
         "cwd": None, "branch": None,
         "turns": [{"text": "older msg", "ts": 100.0}, {"text": long, "ts": 300.0}]},
        {"genesis": "continue handoff-foo-bar per slug", "mtime": 2.0,
         "cwd": None, "branch": None,
         "turns": [{"text": "newest msg", "ts": 400.0}]},
        {"genesis": "unrelated session about weather", "mtime": 3.0,
         "cwd": None, "branch": None,
         "turns": [{"text": "should not appear", "ts": 999.0}]},
    ]
    isc.attribute_recent_messages(inis, records, ["/r"], keep=5)
    texts = [m["text"] for m in inis[0]["recent_messages"]]
    assert texts[0] == "newest msg"                 # DESC by ts (400 > 300 > 100)
    assert texts[1].endswith("…") and len(texts[1]) == 200  # truncated to 200 chars
    assert texts[2] == "older msg"
    assert "should not appear" not in texts         # unattributed session excluded


def test_attribute_recent_messages_branch_cwd_fallback():
    # genesis does NOT name the handoff, but branch+cwd match -> still credited.
    repo = "/home/u/workspace/devrc"
    inis = [{"slug": "app-blocks", "repo": repo, "docs": []}]
    records = [{"genesis": "just start working", "mtime": 1.0,
                "cwd": repo, "branch": "feat/app-blocks",
                "turns": [{"text": "branch-matched msg", "ts": 500.0}]}]
    isc.attribute_recent_messages(inis, records, [repo])
    assert [m["text"] for m in inis[0]["recent_messages"]] == ["branch-matched msg"]


def test_attribute_recent_messages_branch_cwd_wrong_repo_no_credit():
    # right branch token, WRONG cwd/repo -> no cross-repo credit (mirrors telemetry).
    devrc, civit = "/home/u/workspace/devrc", "/home/u/workspace/civit/dp"
    inis = [{"slug": "faro-rum-widening", "repo": civit, "docs": []}]
    records = [{"genesis": "start", "mtime": 1.0, "cwd": devrc,
                "branch": "feat/faro-rum-widening",
                "turns": [{"text": "x", "ts": 1.0}]}]
    isc.attribute_recent_messages(inis, records, [devrc, civit])
    assert inis[0]["recent_messages"] == []


def test_attribute_recent_messages_empty_when_no_records():
    inis = [{"slug": "x", "repo": "/r", "docs": []}]
    isc.attribute_recent_messages(inis, [], ["/r"])
    assert inis[0]["recent_messages"] == []


def test_attribute_recent_messages_dedupes_identical_boilerplate():
    # Automated agent sessions re-inject the SAME prompt across many sessions; identical
    # displayed lines collapse to ONE (newest ts wins), not N duplicate card rows.
    repo = "/r"
    inis = [{"slug": "drafter", "repo": repo,
             "docs": [{"path": "/r/claudedocs/handoff-drafter.md", "date": None}]}]
    boiler = "# task-spec drafter pipeline — you are the drafter"
    records = [
        {"genesis": "start handoff-drafter", "mtime": 1.0, "cwd": None, "branch": None,
         "turns": [{"text": boiler, "ts": 100.0}]},
        {"genesis": "start handoff-drafter", "mtime": 2.0, "cwd": None, "branch": None,
         "turns": [{"text": boiler, "ts": 200.0}]},
    ]
    isc.attribute_recent_messages(inis, records, [repo])
    assert inis[0]["recent_messages"] == [{"text": boiler, "ts": 200.0}]


def test_attribute_recent_messages_sibling_genesis_credits_only_most_specific():
    # THE core precision fix. A session whose genesis names the SPECIFIC child handoff
    # must credit its message to ONLY that child — NOT the generic `app-blocks` sibling
    # (whose `handoff-app-blocks` name is a prefix SUBSTRING of the child's filename).
    repo = "/r"
    generic = {"slug": "app-blocks", "repo": repo,
               "docs": [{"path": "/r/claudedocs/handoff-app-blocks.md", "date": None}]}
    child = {"slug": "app-blocks-comfy-cloud-scaffold", "repo": repo,
             "docs": [{"path": "/r/claudedocs/handoff-app-blocks-comfy-cloud-scaffold.md",
                       "date": None}]}
    inis = [generic, child]
    records = [{"genesis": "resume handoff-app-blocks-comfy-cloud-scaffold.md",
                "mtime": 1.0, "cwd": None, "branch": None,
                "turns": [{"text": "wire the comfy cloud scaffold", "ts": 500.0}]}]
    isc.attribute_recent_messages(inis, records, [repo])
    assert generic["recent_messages"] == []                       # NOT duplicated onto generic
    assert [m["text"] for m in child["recent_messages"]] == ["wire the comfy cloud scaffold"]


def test_attribute_recent_messages_three_prefix_siblings_single_credit():
    # generic + TWO specific children all prefix-share `app-blocks`; a child-named genesis
    # lands on exactly ONE (the named child), never the generic OR the other sibling.
    repo = "/r"
    generic = {"slug": "app-blocks", "repo": repo,
               "docs": [{"path": "/r/claudedocs/handoff-app-blocks.md", "date": None}]}
    scaffold = {"slug": "app-blocks-comfy-cloud-scaffold", "repo": repo,
                "docs": [{"path":
                          "/r/claudedocs/handoff-app-blocks-comfy-cloud-scaffold.md",
                          "date": None}]}
    review = {"slug": "app-blocks-agentic-review-arc", "repo": repo,
              "docs": [{"path": "/r/claudedocs/handoff-app-blocks-agentic-review-arc.md",
                        "date": None}]}
    inis = [generic, scaffold, review]
    records = [{"genesis": "continue handoff-app-blocks-agentic-review-arc.md",
                "mtime": 1.0, "cwd": None, "branch": None,
                "turns": [{"text": "close the review arc", "ts": 500.0}]}]
    isc.attribute_recent_messages(inis, records, [repo])
    assert generic["recent_messages"] == []
    assert scaffold["recent_messages"] == []
    assert [m["text"] for m in review["recent_messages"]] == ["close the review arc"]


def test_attribute_recent_messages_generic_genesis_credits_generic_not_child():
    # A genesis naming ONLY the generic handoff credits the generic. The child does NOT
    # match (its longer `handoff-app-blocks-comfy-cloud-scaffold` name is not a substring of
    # the short generic genesis), so the single-best rule has just one candidate — no
    # accidental diversion to a child the session never referenced.
    repo = "/r"
    generic = {"slug": "app-blocks", "repo": repo,
               "docs": [{"path": "/r/claudedocs/handoff-app-blocks.md", "date": None}]}
    child = {"slug": "app-blocks-comfy-cloud-scaffold", "repo": repo,
             "docs": [{"path": "/r/claudedocs/handoff-app-blocks-comfy-cloud-scaffold.md",
                       "date": None}]}
    inis = [generic, child]
    records = [{"genesis": "resume handoff-app-blocks.md", "mtime": 1.0,
                "cwd": None, "branch": None,
                "turns": [{"text": "generic app-blocks work", "ts": 500.0}]}]
    isc.attribute_recent_messages(inis, records, [repo])
    assert [m["text"] for m in generic["recent_messages"]] == ["generic app-blocks work"]
    assert child["recent_messages"] == []


def test_attribute_recent_messages_tiebreak_longer_slug_wins():
    # Two candidates with the SAME slug-token count: the tie-break (longer raw slug, then
    # lexical — `_specificity_key`, mirroring best_matching_initiative) decides the winner.
    repo = "/r"
    # both have 2 meaningful tokens ({red, panda} vs {red, pandas}) — different raw lengths.
    a = {"slug": "red-panda", "repo": repo,
         "docs": [{"path": "/r/claudedocs/handoff-red-panda.md", "date": None}]}
    b = {"slug": "red-pandas", "repo": repo,
         "docs": [{"path": "/r/claudedocs/handoff-red-pandas.md", "date": None}]}
    inis = [a, b]
    # Genesis names BOTH handoffs -> both are candidates; longer raw slug ("red-pandas") wins.
    records = [{"genesis": "handoff-red-panda.md and handoff-red-pandas.md", "mtime": 1.0,
                "cwd": None, "branch": None,
                "turns": [{"text": "which panda", "ts": 500.0}]}]
    isc.attribute_recent_messages(inis, records, [repo])
    assert a["recent_messages"] == []
    assert [m["text"] for m in b["recent_messages"]] == ["which panda"]
    # Confirm the tie-break agrees with _specificity_key directly.
    assert isc._specificity_key(b) > isc._specificity_key(a)


def test_attribute_recent_messages_single_credit_does_not_change_session_counts():
    # SCOPE GUARD: the message single-credit fix must NOT touch attribute_sessions —
    # `session_count` (the displayed `sess:` count) still MULTI-credits prefix siblings.
    repo = "/r"
    generic = {"slug": "app-blocks", "repo": repo,
               "docs": [{"path": "/r/claudedocs/handoff-app-blocks.md", "date": None}]}
    child = {"slug": "app-blocks-comfy-cloud-scaffold", "repo": repo,
             "docs": [{"path": "/r/claudedocs/handoff-app-blocks-comfy-cloud-scaffold.md",
                       "date": None}]}
    inis = [generic, child]
    genesis = [{"text": "resume handoff-app-blocks-comfy-cloud-scaffold.md", "mtime": 10.0}]
    isc.attribute_sessions(inis, genesis)
    # BOTH still counted (unchanged multi-credit) — the child filename contains the generic
    # `handoff-app-blocks` substring, so the generic's session_count is 1, not diverted.
    assert generic["session_count"] == 1
    assert child["session_count"] == 1
    assert generic["last_session"] == 10.0 and child["last_session"] == 10.0


# --------------------------------------------------------------------------- #
# Recent commit subjects
# --------------------------------------------------------------------------- #
def test_git_recent_commit_subjects_parses_and_excludes_default(monkeypatch):
    monkeypatch.setattr(isc, "_resolve_branch_ref", lambda r, b: b)
    monkeypatch.setattr(isc, "_ref_exists", lambda r, ref: ref == "main")
    seen = {}

    def fake_run(cmd, timeout=20.0):
        seen["cmd"] = cmd
        return "1783000200\x00feat: two words\n1783000100\x00fix: one\n"

    monkeypatch.setattr(isc, "_run", fake_run)
    out = isc.git_recent_commit_subjects("/r", "feat/x", 7, "main", limit=5)
    assert out == [(1783000200.0, "feat: two words"), (1783000100.0, "fix: one")]
    assert "--not" in seen["cmd"] and "main" in seen["cmd"]  # default excluded
    assert any("%ct%x00%s" in c for c in seen["cmd"])        # NUL-separated format


def test_git_recent_commit_subjects_caps_at_limit(monkeypatch):
    monkeypatch.setattr(isc, "_resolve_branch_ref", lambda r, b: b)
    monkeypatch.setattr(isc, "_ref_exists", lambda r, ref: False)
    monkeypatch.setattr(isc, "_run",
                        lambda cmd, timeout=20.0: "".join(
                            f"{1000 + i}\x00subject {i}\n" for i in range(10)))
    out = isc.git_recent_commit_subjects("/r", "feat/x", 7, "main", limit=3)
    assert len(out) == 3


def test_git_recent_commit_subjects_default_branch_and_unresolvable_empty(monkeypatch):
    assert isc.git_recent_commit_subjects("/r", "main", 7, "main") == []  # default branch
    monkeypatch.setattr(isc, "_resolve_branch_ref", lambda r, b: None)
    assert isc.git_recent_commit_subjects("/r", "feat/x", 7, "main") == []  # no such ref


def test_attribute_git_populates_recent_commits(monkeypatch):
    repo = "/home/u/workspace/devrc"
    inis = [{"slug": "mail-automation", "repo": repo}]
    monkeypatch.setattr(isc, "git_branches", lambda r: ["feat/mail-automation", "main"])
    monkeypatch.setattr(isc, "git_default_branch", lambda r: "main")
    monkeypatch.setattr(isc, "git_commits_in_window", lambda r, b, d, db=None: (2, 1000.0))
    monkeypatch.setattr(isc, "gh_open_prs", lambda r: [])
    monkeypatch.setattr(isc, "gh_merged_prs", lambda r, d: [])
    monkeypatch.setattr(
        isc, "git_recent_commit_subjects",
        lambda r, b, d, db=None, limit=5:
            [(300.0, "newer subject"), (100.0, "older subject")]
            if b == "feat/mail-automation" else [])
    isc.attribute_git(inis, 7)
    assert inis[0]["recent_commits"] == ["newer subject", "older subject"]  # newest-first


# --------------------------------------------------------------------------- #
# tmux task titles (the render-time `live: <task>` signal)
# --------------------------------------------------------------------------- #
def test_match_tmux_populates_tmux_tasks():
    civit = "/home/u/workspace/civit/dp"
    inis = [{"slug": "faro-rum-widening", "title": "Faro RUM widening", "repo": civit}]
    panes = [{"session": "scratch4", "window": "2", "cwd": civit, "command": "claude",
              "title": "Wire Faro to main civitai app"}]
    isc.match_tmux_to_initiatives(inis, panes, [civit], codenames={"scratch4": "Vapor"})
    assert inis[0]["tmux_tasks"] == ["Wire Faro to main civitai app"]
    assert inis[0]["tmux_sessions"] == {"Vapor-2"}


def test_match_tmux_tasks_dedupe_and_absent_when_unmatched():
    civit = "/home/u/workspace/civit/dp"
    inis = [{"slug": "sysredis-buffer", "title": "sysRedis buffer", "repo": civit}]
    panes = [
        {"session": "8", "window": "2", "cwd": civit, "command": "claude",
         "title": "Continue sysredis buffer work"},
        {"session": "8", "window": "2", "cwd": civit, "command": "claude",
         "title": "Continue sysredis buffer work"},  # identical title -> de-duped
    ]
    isc.match_tmux_to_initiatives(inis, panes, [civit])
    assert inis[0]["tmux_tasks"] == ["Continue sysredis buffer work"]
