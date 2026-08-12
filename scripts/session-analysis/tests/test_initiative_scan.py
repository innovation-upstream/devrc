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
    # SESSIONS-ONLY board: a doc no longer creates a card on its own — a session must
    # anchor it. This session's ai-title overlaps the handoff slug (mail+automation), so
    # it folds into the handoff (source="both") and the mail-automation card exists.
    sess = _sess("Mail automation extractor polish", session_id="s1", cwd=str(repo),
                 last_user_ts=1_500.0, n_turns=10)
    monkeypatch.setattr(isc, "collect_session_records", lambda root, d, n=5: [sess])

    now = 2_000.0  # last_commit at 1000 -> age 1000s -> active
    # gh_ok is stated, not probed: the PR figures below come from the stub, so the
    # report's honesty flag must not depend on whether the HOST running the test
    # happens to have an authenticated `gh`.
    report = isc.build_report(2, repos=[str(repo)], client=None, now=now, gh_ok=True)

    assert report["telemetry_available"] is False
    assert report["gh_available"] is True
    inis = report["by_repo"][str(repo)]
    assert len(inis) == 1
    ini = inis[0]
    assert ini["slug"] == "mail-automation"
    assert ini["source"] == "both"                # doc anchored by the session, not pure-doc
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

    report = isc.build_report(14, repos=[str(repo)], client=FakeClient(),
                              now=2_000_000_000.0, gh_ok=True)
    assert report["telemetry_available"] is True
    txt = isc.render(report, now=2_000_000_000.0)
    assert "unsegmented trunk/main work" in txt
    assert "ev:99" in txt


# --------------------------------------------------------------------------- #
# `gh_available` — telling a MEASURED zero apart from a missing measurement.
#
# `gh_open_prs` returns [] on any failure, so `open_prs: []` on a host without an
# authenticated `gh` is byte-identical to "this initiative genuinely has no open
# PRs". A blind dogfood run read `open_prs: []` + `commits: 0` across ~33 "active"
# initiatives and had nothing in the output telling it which of those zeroes were
# real. The flag does not fix the ambiguity — it REPORTS it.
# --------------------------------------------------------------------------- #
def _tiny_report(monkeypatch, tmp_path, **kw):
    repo = tmp_path / "ghflag"
    (repo / "claudedocs").mkdir(parents=True)
    (repo / "claudedocs" / "handoff-thing.md").write_text(
        "# Handoff: thing\n## Next steps\n1. go\n")
    monkeypatch.setattr(isc, "git_branches", lambda r: ["main"])
    monkeypatch.setattr(isc, "git_default_branch", lambda r: "main")
    monkeypatch.setattr(isc, "gh_open_prs", lambda r: [])
    monkeypatch.setattr(isc, "gh_merged_prs", lambda r, d: [])
    monkeypatch.setattr(isc, "collect_session_records", lambda root, d, n=5: [])
    return isc.build_report(14, repos=[str(repo)], client=None,
                            now=2_000_000_000.0, **kw)


def test_report_carries_gh_available_true(monkeypatch, tmp_path):
    """POSITIVE side of the pair — the flag must be able to read True."""
    report = _tiny_report(monkeypatch, tmp_path, gh_ok=True)
    assert report["gh_available"] is True
    assert "gh UNAVAILABLE" not in isc.render(report, now=2_000_000_000.0)


def test_report_carries_gh_available_false_and_says_so(monkeypatch, tmp_path):
    """NEGATIVE side — and the rendered report must SAY the PR figures are not
    measurements, or the flag only helps a reader who already knew to look."""
    report = _tiny_report(monkeypatch, tmp_path, gh_ok=False)
    assert report["gh_available"] is False
    txt = isc.render(report, now=2_000_000_000.0)
    assert "gh UNAVAILABLE" in txt
    assert "NOT MEASURED, not zero" in txt


def test_gh_available_probe_is_false_without_gh_on_path(monkeypatch):
    """The default (unstubbed) probe must be able to answer False for real."""
    monkeypatch.setattr(isc.shutil, "which", lambda name: None)
    assert isc.gh_available() is False


def test_gh_available_probe_is_false_when_auth_fails(monkeypatch):
    """`gh` present but unauthenticated is exactly the case that produced the
    ambiguous `[]` — it must not read as available."""
    monkeypatch.setattr(isc.shutil, "which", lambda name: "/usr/bin/gh")

    class R:
        returncode = 1

    monkeypatch.setattr(isc.subprocess, "run", lambda *a, **k: R())
    assert isc.gh_available() is False


def test_gh_available_probe_is_true_when_authed(monkeypatch):
    """Positive control for the probe itself: it can also return True."""
    monkeypatch.setattr(isc.shutil, "which", lambda name: "/usr/bin/gh")

    class R:
        returncode = 0

    monkeypatch.setattr(isc.subprocess, "run", lambda *a, **k: R())
    assert isc.gh_available() is True


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
# initiative_fingerprint — slug tokens, or title tokens for a date-only slug
# --------------------------------------------------------------------------- #
def test_initiative_fingerprint_uses_slug_tokens_for_a_real_slug():
    # A normal slug is unchanged — the fingerprint IS its slug tokens (byte-identical
    # to slug_tokens), so every existing match keeps its exact behaviour.
    ini = {"slug": "clawgate-agent-loop", "title": "Something else entirely"}
    assert isc.initiative_fingerprint(ini) == isc.slug_tokens("clawgate-agent-loop")
    assert isc.initiative_fingerprint(ini) == ["clawgate", "agent", "loop"]


def test_initiative_fingerprint_falls_back_to_title_for_date_only_slug():
    # A bare-date slug yields ZERO slug tokens -> fall back to the TITLE tokens so the
    # initiative isn't structurally unmatchable (the ComfyUI handoff-2026-07-21 case).
    ini = {"slug": "2026-07-21",
           "title": "Session Handoff — ComfyUI NSFW realism pipeline"}
    assert isc.slug_tokens("2026-07-21") == []  # premise: date-only -> no slug tokens
    assert isc.initiative_fingerprint(ini) == isc.text_tokens(ini["title"])
    assert "comfyui" in isc.initiative_fingerprint(ini)


# --------------------------------------------------------------------------- #
# best_title_match — date-only-slug fingerprint fallback (recall Fix #1)
# --------------------------------------------------------------------------- #
def test_best_title_match_date_only_slug_links_on_unique_title_token():
    # The live ComfyUI case: a date-only slug ('2026-07-21') matches its own pane via a
    # TITLE token ('comfyui') that is UNIQUE among the eligible set (df==1).
    inis = [
        {"slug": "2026-07-21",
         "title": "Session Handoff — ComfyUI NSFW realism pipeline"},
        {"slug": "sysredis-buffer", "title": "sysRedis buffer soft-dependency"},
    ]
    toks = set(isc.text_tokens("Run ComfyUI preference optimization loop end-to-end"))
    assert isc.best_title_match(toks, inis)["slug"] == "2026-07-21"


def test_best_title_match_date_only_slug_ignores_unrelated_pane():
    # A date-only slug must NOT grab a pane it has no topical overlap with — the
    # fallback only ADDS matches on real title-token overlap, never blanket-claims.
    inis = [
        {"slug": "2026-07-21",
         "title": "Session Handoff — ComfyUI NSFW realism pipeline"},
    ]
    toks = set(isc.text_tokens("Fix hands in walk/portrait output with detailer"))
    assert isc.best_title_match(toks, inis) is None


def test_best_title_match_date_only_slug_shared_title_token_does_not_link():
    # The df/uniqueness gate STILL guards the title fallback: when the only overlapping
    # title token is SHARED (df>1), the date-only slug does NOT link on it (precision).
    inis = [
        {"slug": "2026-07-21", "title": "ComfyUI pipeline realism"},
        {"slug": "comfyui-runner", "title": "comfyui runner"},
    ]
    toks = set(isc.text_tokens("ComfyUI status"))  # only the SHARED 'comfyui' overlaps
    assert isc.best_title_match(toks, inis) is None


def test_best_title_match_real_slug_outranks_date_only_title_fallback():
    # PRECISION GUARD: when a real-SLUG initiative and a date-only TITLE-fallback
    # initiative both match a contested pane, the real slug wins — a fallback fingerprint
    # can never STEAL a pane a real-slug initiative would have claimed.
    inis = [
        {"slug": "2026-07-21", "title": "ComfyUI pipeline realism"},   # date-only
        {"slug": "comfyui-pipeline", "title": "comfyui pipeline"},     # real slug
    ]
    toks = set(isc.text_tokens("comfyui pipeline run"))
    assert isc.best_title_match(toks, inis)["slug"] == "comfyui-pipeline"


def test_best_title_match_sibling_shared_prefix_token_stays_unmatched():
    # DELIBERATE precision decision (recall Fix #2 DEFERRED): a pane whose ONLY overlap
    # is a token SHARED across a sibling family (df>1) stays UNMATCHED. Attaching it to
    # the most-recently-touched sibling by recency was evaluated and rejected — on live
    # data that rule is structurally indistinguishable from a false match on a generic
    # client/domain token, and a wrong tag costs more than a miss.
    inis = [
        {"slug": "remix-session", "title": "Remix — session handoff"},
        {"slug": "remix-hardening-session", "title": "Remix — hardening"},
        {"slug": "remix-platform", "title": "Remix platform"},
        {"slug": "remix-templates", "title": "Remix render templates"},
    ]
    toks = set(isc.text_tokens("Resume remix 0.18 work and verify live feed"))
    assert isc.best_title_match(toks, inis) is None


# --------------------------------------------------------------------------- #
# match_tmux_to_initiatives — attach live sessions, scoped by repo
# --------------------------------------------------------------------------- #
def test_match_tmux_date_only_slug_initiative_attaches_its_pane():
    # End-to-end: a date-only-slug initiative (ComfyUI handoff-2026-07-21) in its own
    # repo attaches its live pane via the title-token fingerprint fallback, and a
    # topically-unrelated pane in the same repo stays unmatched.
    comfy = "/home/u/workspace/fast/comfyui"
    inis = [{"slug": "2026-07-21",
             "title": "Session Handoff — ComfyUI NSFW realism pipeline",
             "repo": comfy}]
    # The matching pane must share a DISTINGUISHING title token (nsfw/realism/pipeline), NOT just
    # the repo name "comfyui" — repo-name tokens are stripped before matching (see
    # match_tmux_to_initiatives), so a pane that only NAMES the repo no longer attaches. This
    # exercises the date-only-slug TITLE-fingerprint fallback on a real topic word.
    panes = [
        {"session": "scratch6", "window": "4", "cwd": comfy, "command": "claude",
         "title": "Run the ComfyUI NSFW realism pipeline optimization loop"},
        {"session": "scratch6", "window": "5", "cwd": comfy, "command": "claude",
         "title": "Fix hands in walk/portrait output with detailer and LoRA"},
    ]
    unmatched = isc.match_tmux_to_initiatives(inis, panes, [comfy],
                                              codenames={"scratch6": "Pool"})
    assert inis[0]["tmux_sessions"] == {"Pool-4"}
    assert [u["id"] for u in unmatched] == ["Pool-5"]  # the hands pane has no overlap


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


def test_match_tmux_generic_repo_named_pane_does_not_false_match():
    # A generic pane that only NAMES the repo ("Continue civitai-manager development work") must
    # NOT attach to a same-repo initiative on the repo-name tokens alone (civitai/manager) — it
    # shares no DISTINGUISHING word with the Security Audit initiative, so it stays unmatched
    # (surfaced as a live-but-untied session) instead of falsely badging that card live.
    civit = "/home/u/workspace/civit/civitai-manager"
    inis = [{"slug": "SECURITY-AUDIT-v0.1.64",
             "title": "Security Audit — civitai-manager v0.1.64 (holistic / cross-cutting)",
             "repo": civit}]
    panes = [{"session": "scratch6", "window": "1", "cwd": civit, "command": "claude",
              "title": "Continue civitai-manager development work"}]
    unmatched = isc.match_tmux_to_initiatives(inis, panes, [civit])
    assert inis[0]["tmux_sessions"] == set()
    assert len(unmatched) == 1
    assert unmatched[0]["repo"] == civit


def test_match_tmux_distinguishing_token_matches_despite_repo_name():
    # Stripping repo-name tokens must NOT break a legit match: a pane sharing DISTINGUISHING
    # tokens ("holistic"/"cross"/"cutting") still attaches even though it also names the repo.
    civit = "/home/u/workspace/civit/civitai-manager"
    inis = [{"slug": "SECURITY-AUDIT-v0.1.64",
             "title": "Security Audit — civitai-manager v0.1.64 (holistic / cross-cutting)",
             "repo": civit}]
    panes = [{"session": "scratch6", "window": "1", "cwd": civit, "command": "claude",
              "title": "Resume civitai-manager holistic cross-cutting hardening"}]
    unmatched = isc.match_tmux_to_initiatives(inis, panes, [civit])
    assert inis[0]["tmux_sessions"] == {"main:scratch6-1"}
    assert unmatched == []


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


def test_match_tmux_threads_activity_ts_to_matched_and_unmatched():
    # activity_ts flows onto BOTH a matched task (via tmux_task_activity) AND the unmatched entry.
    devrc = "/home/u/workspace/devrc"
    inis = [{"slug": "clawgate-chat-polish", "title": "clawgate chat polish", "repo": devrc}]
    panes = [
        {"session": "1", "window": "3", "cwd": devrc, "command": "claude",
         "title": "clawgate chat polish soak", "activity_ts": 1722000500},
        {"session": "scratchX", "window": "1", "cwd": devrc, "command": "claude",
         "title": "brand new unrelated thread", "activity_ts": 1722000900},
    ]
    unmatched = isc.match_tmux_to_initiatives(inis, panes, [devrc])
    assert inis[0]["tmux_tasks"] == ["clawgate chat polish soak"]
    assert inis[0]["tmux_task_activity"] == {"clawgate chat polish soak": 1722000500}
    assert len(unmatched) == 1
    assert unmatched[0]["title"] == "brand new unrelated thread"
    assert unmatched[0]["activity_ts"] == 1722000900


def test_match_tmux_missing_activity_ts_threads_none():
    # A pane dict without activity_ts (older tmux / degraded read) threads None, never KeyErrors.
    devrc = "/home/u/workspace/devrc"
    inis = [{"slug": "clawgate-chat-polish", "title": "clawgate chat polish", "repo": devrc}]
    panes = [
        {"session": "1", "window": "3", "cwd": devrc, "command": "claude",
         "title": "clawgate chat polish soak"},                      # no activity_ts key
        {"session": "scratchX", "window": "1", "cwd": devrc, "command": "claude",
         "title": "unrelated thread"},                               # no activity_ts key
    ]
    unmatched = isc.match_tmux_to_initiatives(inis, panes, [devrc])
    assert inis[0]["tmux_task_activity"] == {"clawgate chat polish soak": None}
    assert unmatched[0]["activity_ts"] is None


def test_match_tmux_task_activity_first_write_wins():
    # Two panes with the SAME matched task text: tmux_tasks dedups to one, and its activity_ts
    # is the FIRST pane's (aligned with the insertion-ordered dedup).
    civit = "/home/u/workspace/civit/dp"
    inis = [{"slug": "sysredis-buffer", "title": "sysRedis buffer", "repo": civit}]
    panes = [
        {"session": "8", "window": "2", "cwd": civit, "command": "claude",
         "title": "Continue sysredis buffer work", "activity_ts": 111},
        {"session": "8", "window": "2", "cwd": civit, "command": "claude",
         "title": "Continue sysredis buffer work", "activity_ts": 999},
    ]
    isc.match_tmux_to_initiatives(inis, panes, [civit])
    assert inis[0]["tmux_tasks"] == ["Continue sysredis buffer work"]
    assert inis[0]["tmux_task_activity"] == {"Continue sysredis buffer work": 111}


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
    # Fields: session, window, cwd, command, window_activity, title.
    out = ("1\t1\t/home/u/workspace/devrc\tclaude\t1721990000\tContinue clawgate loop\n"
           "scratch5\t2\t/home/u/taxes/2025\tzsh\t1721980000\t\n")  # empty title -> ""
    monkeypatch.setattr(isc, "_run", lambda cmd, timeout=20.0: out)
    panes = isc.collect_tmux_panes()
    assert panes[0] == {"session": "1", "window": "1",
                        "cwd": "/home/u/workspace/devrc",
                        "command": "claude", "activity_ts": 1721990000,
                        "title": "Continue clawgate loop"}
    assert panes[1]["window"] == "2"
    assert panes[1]["title"] == ""
    assert panes[1]["activity_ts"] == 1721980000


def test_collect_tmux_panes_parses_window_activity_as_int(monkeypatch):
    out = "wheat\t3\t/home/u/workspace/civitai\tclaude\t1722000123\tSoak the faro rollout\n"
    monkeypatch.setattr(isc, "_run", lambda cmd, timeout=20.0: out)
    (pane,) = isc.collect_tmux_panes()
    assert pane["activity_ts"] == 1722000123
    assert isinstance(pane["activity_ts"], int)


def test_collect_tmux_panes_missing_or_bad_activity_degrades_to_none(monkeypatch):
    # Older tmux (no #{window_activity} → blank 5th field) OR a non-integer value must NOT
    # crash and must degrade to activity_ts=None so the Live-now sort key is simply absent.
    out = ("a\t1\t/r\tclaude\t\tblank activity\n"          # blank → None
           "b\t2\t/r\tclaude\tnot-an-int\tbad activity\n"  # non-int → None
           # An OLD 5-field line (no activity field at all): the 5th field is read as activity
           # (here 'legacy title' → non-int → None) and the title degrades to "" — never a crash.
           "c\t3\t/r\tclaude\tlegacy title\n")
    monkeypatch.setattr(isc, "_run", lambda cmd, timeout=20.0: out)
    panes = isc.collect_tmux_panes()
    assert [p["activity_ts"] for p in panes] == [None, None, None]
    assert panes[0]["title"] == "blank activity"
    assert panes[1]["title"] == "bad activity"
    assert panes[2]["title"] == ""   # 5-field legacy line: no crash, title empty


def test_collect_tmux_panes_title_with_tab_is_preserved(monkeypatch):
    # The title is the LAST field and may contain a literal tab; it must be re-joined, not
    # truncated, and must not shift the (tab-free) activity field.
    out = "s\t1\t/r\tclaude\t1722000000\ttask\twith\ttabs\n"
    monkeypatch.setattr(isc, "_run", lambda cmd, timeout=20.0: out)
    (pane,) = isc.collect_tmux_panes()
    assert pane["activity_ts"] == 1722000000
    assert pane["title"] == "task\twith\ttabs"


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
    # SESSIONS-ONLY board: a session must anchor the handoff for its card to exist
    # (docs no longer float). This session's ai-title overlaps mail+automation.
    sess = _sess("Mail automation extractor polish", session_id="s1", cwd=str(repo),
                 last_user_ts=1_900.0, n_turns=10)
    monkeypatch.setattr(isc, "collect_session_records", lambda root, d, n=5: [sess])
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
    # SESSIONS-ONLY: anchor the handoff with a matching session so its card exists.
    sess = _sess("Mail automation extractor polish", session_id="s1", cwd=str(repo),
                 last_user_ts=1_900.0, n_turns=10)
    monkeypatch.setattr(isc, "collect_session_records", lambda root, d, n=5: [sess])
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
    # SESSIONS-ONLY: anchor the lonely handoff with a matching session so a card exists
    # (its slug is a single unique token, so a 1-token overlap anchors it).
    sess = _sess("Lonely task cleanup", session_id="s1", cwd=str(repo),
                 last_user_ts=1_900.0, n_turns=10)
    monkeypatch.setattr(isc, "collect_session_records", lambda root, d, n=5: [sess])

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


def test_build_report_windows_out_by_session_recency_not_doc_date(tmp_path, monkeypatch):
    # SESSIONS-ONLY windowing: the board keeps/drops a card by its SESSION recency, not
    # the handoff's date/mtime. Two handoffs, each anchored by a session — one session
    # touched 1d ago (in a 4d window), one touched 19d ago (out of 4d, in 30d). The docs'
    # authored dates are deliberately NOT the windowing signal any more.
    import calendar
    now = float(calendar.timegm((2026, 7, 5, 0, 0, 0, 0, 0, 0)))
    repo = tmp_path / "r"
    (repo / "claudedocs").mkdir(parents=True)
    (repo / "claudedocs" / "handoff-fresh-2026-07-04.md").write_text(
        "# Handoff: fresh\n## Next steps\n1. go\n")
    (repo / "claudedocs" / "handoff-stale-2026-06-16.md").write_text(
        "# Handoff: stale\n## Next steps\n1. go\n")
    _stub_no_external_io(monkeypatch)
    # Anchoring sessions (single-token unique slugs -> a 1-token overlap anchors each).
    fresh_sess = _sess("Fresh feature build", session_id="sf", cwd=str(repo),
                       last_user_ts=now - isc.DAY, n_turns=10)
    stale_sess = _sess("Stale cleanup task", session_id="ss", cwd=str(repo),
                       last_user_ts=now - 19 * isc.DAY, n_turns=10)
    monkeypatch.setattr(isc, "collect_session_records",
                        lambda root, d, n=5: [fresh_sess, stale_sess])

    fresh_only = isc.build_report(4, repos=[str(repo)], client=None, now=now)
    assert {i["slug"] for i in fresh_only["by_repo"].get(str(repo), [])} == {"fresh"}

    # Widen the window and the stale one resurfaces (not dropped, just out-of-window).
    both = isc.build_report(30, repos=[str(repo)], client=None, now=now)
    assert {i["slug"] for i in both["by_repo"].get(str(repo), [])} == {"fresh", "stale"}
    # Both are session-anchored -> source is "both", never pure "doc".
    assert {i["source"] for i in both["by_repo"][str(repo)]} == {"both"}


def test_build_report_live_pane_without_session_is_unmatched_not_a_card(tmp_path, monkeypatch):
    # SESSIONS-ONLY: a live tmux pane is NOT a Claude session record — on its own it can no
    # longer resurrect a stale, session-less handoff into a card. With no anchoring session,
    # the doc is dropped and the live pane surfaces as UNMATCHED (the Live-now feed) instead.
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
    assert inis == []                                     # session-less handoff -> no card
    # ...but the live pane is still visible, as an unmatched live session (Live-now source).
    assert any(u["id"] == "main:8-1" for u in report["tmux_unmatched"])


def test_build_report_stale_handoff_with_live_session_stays_active(tmp_path, monkeypatch):
    # An old handoff still being worked (a Claude session anchors it) stays — and reads
    # active — because a LIVE tmux pane on it counts as touched-now, even though both its
    # authored date AND its last user-turn are far outside the window.
    import calendar
    now = float(calendar.timegm((2026, 7, 5, 0, 0, 0, 0, 0, 0)))
    repo = tmp_path / "r"
    (repo / "claudedocs").mkdir(parents=True)
    (repo / "claudedocs" / "handoff-oldwork-2026-05-01.md").write_text(
        "# Handoff: oldwork\n## Next steps\n1. go\n")
    _stub_no_external_io(monkeypatch)
    monkeypatch.setattr(isc, "load_scratch_codenames", lambda *a, **k: {})
    # Session anchors the handoff but its last turn is ancient (30d ago, outside the 4d
    # window) -> only the LIVE pane keeps it in-window and active.
    sess = _sess("Oldwork migration task", session_id="s1", cwd=str(repo),
                 last_user_ts=now - 30 * isc.DAY, n_turns=10)
    monkeypatch.setattr(isc, "collect_session_records", lambda root, d, n=5: [sess])

    panes = [{"session": "8", "window": "1", "cwd": str(repo), "command": "claude",
              "title": "Continue oldwork task"}]
    report = isc.build_report(4, repos=[str(repo)], client=None, now=now,
                              include_tmux=True, panes=panes)
    inis = report["by_repo"].get(str(repo), [])
    assert len(inis) == 1 and inis[0]["slug"] == "oldwork"
    assert inis[0]["momentum"] == "active"        # live pane => touched now
    assert inis[0]["tmux_sessions"] == ["main:8-1"]


def test_build_report_last_session_from_user_turn_not_mtime(tmp_path, monkeypatch):
    # HOT-PATH GUARD for the false-active bug: build_report inlines the genesis dict from
    # collect_session_records; it MUST carry last_user_ts so last_session times from the
    # last genuine user turn, not the transcript's fs-mtime (which Claude Code bumps on an
    # in-place metadata rewrite with zero new work). Pre-fix, mtime (~30m ago) read active;
    # post-fix, the 20-day-old last user turn reads stalled. The exact clawgate-chat-polish
    # case, exercised through the real build_report path (not just attribute_sessions).
    import calendar
    now = float(calendar.timegm((2026, 7, 25, 0, 0, 0, 0, 0, 0)))
    repo = tmp_path / "r"
    (repo / "claudedocs").mkdir(parents=True)
    (repo / "claudedocs" / "handoff-widget-2026-07-01.md").write_text(
        "# Handoff: widget\n## Next steps\n1. go\n")   # dated 07-01 -> doc freshness stalled too
    _stub_no_external_io(monkeypatch)
    old_turn = now - 20 * isc.DAY                       # ~2026-07-05, a genuine old interaction
    # A session whose ai-title anchors the widget handoff (source=both). Its fs-mtime was
    # bumped to ~30m ago by an in-place metadata rewrite, but its last genuine user turn is
    # 20d old — momentum must read from the turn, not the mtime.
    rec = _sess("Widget rendering task", session_id="s1", cwd=str(repo), branch="main",
                last_user_ts=old_turn, n_turns=10, genesis="resume handoff-widget",
                turns=[{"text": "resume handoff-widget", "ts": old_turn}])
    rec["mtime"] = now - 1800                           # in-place-rewrite bump: ~30m ago
    monkeypatch.setattr(isc, "collect_session_records", lambda root, d, n=5: [rec])

    report = isc.build_report(30, repos=[str(repo)], client=None, now=now)
    ini = report["by_repo"][str(repo)][0]
    assert ini["slug"] == "widget"
    assert ini["session_count"] == 1                    # attribution unchanged
    assert ini["last_session"] == old_turn              # NOT rec["mtime"]
    assert ini["momentum"] == "stalled"                 # would be "active" off the mtime


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


# --------------------------------------------------------------------------- #
# last_user_ts — momentum from the last genuine user turn, NOT transcript mtime.
# (Claude Code rewrites .jsonl in place for metadata → mtime bumps with zero new
#  work → an idle session reads as freshly touched → false `active`. Mirrors the
#  doc_touch_epoch mtime-avoidance fix.)
# --------------------------------------------------------------------------- #
def test_read_session_turns_carries_last_user_ts(tmp_path):
    # last_user_ts = MAX epoch across ALL genuine user turns, independent of the
    # n-turn window and of the (noise) turns that get skipped.
    p = tmp_path / "s.jsonl"
    _write_transcript(p, [
        _jsonl_user("<system-reminder>noise</system-reminder>", "2026-07-05T09:59:00Z"),
        _jsonl_user("read handoff-foo.md and start", "2026-07-05T10:00:00Z"),
        _jsonl_user("do the next thing", "2026-07-05T10:05:00Z"),
    ])
    rec = isc._read_session_turns(str(p), 1)   # tiny window; last_user_ts must ignore it
    assert rec["last_user_ts"] == isc._iso_to_epoch("2026-07-05T10:05:00Z")
    assert len(rec["turns"]) == 1              # window kept only the last turn


def test_read_session_turns_last_user_ts_none_without_timestamp(tmp_path):
    # A genuine turn whose `timestamp` is absent -> last_user_ts None (fallback path),
    # but the record is still returned (genesis present).
    line = _json.dumps({"type": "user",
                        "message": {"role": "user", "content": "a real turn, no ts"}})
    p = tmp_path / "s.jsonl"
    _write_transcript(p, [line])
    rec = isc._read_session_turns(str(p), 5)
    assert rec is not None and rec["genesis"] == "a real turn, no ts"
    assert rec["last_user_ts"] is None


def test_session_genesis_refs_carries_last_user_ts_below_mtime(tmp_path):
    # The clawgate reproduction at the walk level: the file's mtime is "now" (just
    # written) but its last genuine user turn is 18 days old -> last_user_ts is the
    # OLD epoch, far below mtime.
    p = tmp_path / "proj" / "a.jsonl"
    p.parent.mkdir(parents=True)
    _write_transcript(p, [
        _jsonl_user("resume handoff-clawgate-chat-polish-2026-07-05.md", "2026-07-05T10:00:00Z"),
        _jsonl_user("last real turn that day", "2026-07-05T11:00:00Z"),
    ])
    refs = isc.session_genesis_refs(str(tmp_path), 3650)
    assert len(refs) == 1
    assert refs[0]["last_user_ts"] == isc._iso_to_epoch("2026-07-05T11:00:00Z")
    # file was just created -> its mtime is "now", strictly newer than the 2026-07-05 turn.
    assert refs[0]["mtime"] > refs[0]["last_user_ts"]


def test_attribute_sessions_times_last_session_from_user_turn_not_mtime():
    # The exact clawgate-chat-polish bug: mtime bumped to ~53m ago by an in-place
    # rewrite, but the last genuine user turn was 18d ago. last_session must be the
    # OLD user-turn epoch (-> stalled), NOT the recent mtime (-> false active).
    now = isc._iso_to_epoch("2026-07-23T10:00:00Z")
    old_turn = isc._iso_to_epoch("2026-07-05T10:00:00Z")   # 18 days before `now`
    recent_mtime = now - 53 * 60                            # in-place-rewrite bump
    inis = [{"slug": "clawgate-chat-polish", "docs": [
        {"path": "/r/claudedocs/handoff-clawgate-chat-polish-2026-07-05.md",
         "date": "2026-07-05"}]}]
    genesis = [{"text": "resume handoff-clawgate-chat-polish-2026-07-05.md",
                "mtime": recent_mtime, "last_user_ts": old_turn}]
    isc.attribute_sessions(inis, genesis)
    assert inis[0]["session_count"] == 1
    assert inis[0]["last_session"] == old_turn              # NOT recent_mtime
    assert isc.classify_momentum(inis[0]["last_session"], now) == "stalled"


def test_attribute_sessions_recent_user_turn_stays_active():
    # A genuinely-recent session (last user turn ~1h ago) still classifies active.
    now = isc._iso_to_epoch("2026-07-23T10:00:00Z")
    recent_turn = now - 3600
    inis = [{"slug": "live-work", "docs": [
        {"path": "/r/claudedocs/handoff-live-work-2026-07-23.md", "date": "2026-07-23"}]}]
    genesis = [{"text": "read handoff-live-work-2026-07-23.md",
                "mtime": now, "last_user_ts": recent_turn}]
    isc.attribute_sessions(inis, genesis)
    assert inis[0]["last_session"] == recent_turn
    assert isc.classify_momentum(inis[0]["last_session"], now) == "active"


def test_attribute_sessions_falls_back_to_mtime_without_user_ts():
    # No parseable user-turn ts (last_user_ts absent/None) -> fall back to mtime,
    # the only remaining signal (session still counts).
    inis = [{"slug": "edge", "docs": [
        {"path": "/r/claudedocs/handoff-edge.md", "date": None}]}]
    # one session carries a real user-turn ts, one only a mtime (no last_user_ts key)
    genesis = [
        {"text": "resume handoff-edge.md", "mtime": 5000.0, "last_user_ts": None},
        {"text": "resume handoff-edge.md again", "mtime": 9000.0},   # key absent -> .get None
    ]
    isc.attribute_sessions(inis, genesis)
    assert inis[0]["session_count"] == 2
    assert inis[0]["last_session"] == 9000.0    # newest mtime wins via fallback


def test_attribute_sessions_session_count_independent_of_ts_source():
    # SCOPE GUARD: switching last_session's timestamp source does NOT change session_count
    # — a mix of ts-bearing and ts-less matching sessions all still count.
    inis = [{"slug": "cnt", "docs": [
        {"path": "/r/claudedocs/handoff-cnt.md", "date": None}]}]
    genesis = [
        {"text": "resume handoff-cnt.md", "mtime": 1.0, "last_user_ts": 100.0},
        {"text": "resume handoff-cnt.md", "mtime": 2.0, "last_user_ts": None},
        {"text": "resume handoff-cnt.md", "mtime": 3.0},           # no key
        {"text": "unrelated", "mtime": 4.0, "last_user_ts": 400.0},
    ]
    isc.attribute_sessions(inis, genesis)
    assert inis[0]["session_count"] == 3        # three matched, one unrelated


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


# =========================================================================== #
# Session-first initiative discovery (the flip: sessions make an initiative
# exist; a handoff doc is an OPTIONAL title-matched anchor).
# =========================================================================== #

# The sacred output contract: every initiative dict (doc- OR session-derived) MUST
# carry this exact key set (sync.write_snapshot does positional r[c] over ROW_COLUMNS).
CONTRACT_KEYS = {
    "repo", "slug", "title", "summary", "date", "momentum", "last_touch", "next_step",
    "commits", "commits_unknown", "merged_prs", "open_prs", "session_count",
    "telem_events", "telem_last", "current_doc", "open_investigations", "docs",
    "recent_messages", "recent_commits", "opening_message", "search_text",
}


def _sess(ai_title, *, n_turns=10, session_id="s0", cwd="/home/u/workspace/devrc",
          last_user_ts=1000.0, genesis="genesis text", last_prompt=None,
          branch=None, turns=None, repo=None, search_text=None):
    """A session record shaped like collect_session_records output."""
    rec = {"ai_title": ai_title, "last_prompt": last_prompt, "genesis": genesis,
           "n_turns": n_turns, "session_id": session_id, "cwd": cwd,
           "last_user_ts": last_user_ts, "mtime": last_user_ts, "branch": branch,
           "search_text": search_text if search_text is not None else genesis,
           "turns": turns if turns is not None else [{"text": genesis, "ts": last_user_ts}]}
    if repo is not None:
        rec["repo"] = repo
    return rec


# --------------------------------------------------------------------------- #
# topic_tokens — precedence aiTitle > last-prompt > genesis
# --------------------------------------------------------------------------- #
def test_topic_tokens_prefers_ai_title():
    rec = _sess("ComfyUI realism pipeline", last_prompt="mail automation",
                genesis="something else entirely")
    assert isc.topic_tokens(rec) == frozenset({"comfyui", "realism", "pipeline"})


def test_topic_tokens_falls_back_to_last_prompt_then_genesis():
    assert isc.topic_tokens(_sess(None, last_prompt="mail automation shipping",
                                   genesis="unrelated")) == frozenset(
        {"mail", "automation", "shipping"})
    assert isc.topic_tokens(_sess(None, last_prompt=None,
                                   genesis="sysredis buffer soak")) == frozenset(
        {"sysredis", "buffer", "soak"})


# --------------------------------------------------------------------------- #
# session_eligible — the noise floor
# --------------------------------------------------------------------------- #
def test_session_eligible_requires_ai_title():
    assert not isc.session_eligible(_sess(None, n_turns=9), corroborated=True)
    assert not isc.session_eligible(_sess("   ", n_turns=9), corroborated=True)


def test_session_eligible_requires_min_topic_tokens():
    # A single meaningful token (clawgate) is below MIN_TOPIC_TOKENS=2.
    assert not isc.session_eligible(_sess("Clawgate", n_turns=9), corroborated=True)
    assert isc.session_eligible(_sess("Clawgate approval flow", n_turns=9),
                                corroborated=False)


def test_min_session_turns_is_one():
    # Owner decision (2026-07-27): every titled session is a first-class card — the turn floor was
    # dropped 8 -> 1. MIN_TOPIC_TOKENS is untouched (a card still needs a >=2-token title).
    assert isc.MIN_SESSION_TURNS == 1
    assert isc.MIN_TOPIC_TOKENS == 2


def test_session_eligible_one_turn_titled_session_now_admitted():
    # A short (few-turn) session with an ai-title + >=2 topic tokens is now eligible on its own,
    # regardless of corroboration — at floor 8 the 2-turn session was excluded; at floor 1 it is
    # a first-class card.
    short = _sess("ComfyUI realism pipeline", n_turns=2)
    assert isc.session_eligible(short, corroborated=False)      # was EXCLUDED at the old 8-floor
    assert isc.session_eligible(_sess("ComfyUI realism pipeline", n_turns=1), corroborated=False)


def test_session_eligible_below_floor_still_needs_corroboration():
    # The git-corroboration rescue path is intact for anything BELOW the floor: a 0-turn record
    # (< MIN_SESSION_TURNS=1) is eligible only when corroborated (real sessions carry >=1 turn, so
    # in practice every titled session now clears the floor outright).
    zero = _sess("ComfyUI realism pipeline", n_turns=0)
    assert not isc.session_eligible(zero, corroborated=False)
    assert isc.session_eligible(zero, corroborated=True)


def test_session_corroborated_is_feature_branch_only():
    # git corroboration = a real non-trunk feature branch (topic-specific), NOT repo-level.
    assert isc.session_corroborated(_sess("x y", branch="feat/comfyui-pipeline"))
    assert not isc.session_corroborated(_sess("x y", branch="main"))
    assert not isc.session_corroborated(_sess("x y", branch=None))


def test_build_session_groups_short_trunk_session_now_admitted():
    # At the dropped floor (MIN_SESSION_TURNS=1) a short session seeds a group on its own — even on
    # trunk, no feature-branch corroboration needed — because every titled session is a first-class
    # card now. At floor 8 the trunk short session was excluded (only the branch one survived).
    on_branch = _sess("ComfyUI realism pipeline", session_id="a", repo=R, n_turns=2,
                      branch="feat/comfyui")
    on_trunk = _sess("Sysredis buffer soak", session_id="b", repo=R, n_turns=2,
                     branch="main")
    groups = isc.build_session_groups([on_branch, on_trunk])
    assert {g["slug"] for g in groups} == {"comfyui-pipeline-realism", "buffer-soak-sysredis"}


def test_build_session_groups_below_floor_still_needs_corroboration():
    # BELOW the floor of 1 (a 0-turn record) the per-session git corroboration still gates: the
    # feature-branch one seeds a group, the trunk one does not. Guards that the rescue path lives.
    on_branch = _sess("ComfyUI realism pipeline", session_id="a", repo=R, n_turns=0,
                      branch="feat/comfyui")
    on_trunk = _sess("Sysredis buffer soak", session_id="b", repo=R, n_turns=0,
                     branch="main")
    groups = isc.build_session_groups([on_branch, on_trunk])
    assert {g["slug"] for g in groups} == {"comfyui-pipeline-realism"}


# --------------------------------------------------------------------------- #
# Grouping — exact, drift-merge, sibling-family stays split
# --------------------------------------------------------------------------- #
R = "/home/u/workspace/devrc"


def test_build_session_groups_identical_topics_form_one_group():
    recs = [_sess("ComfyUI realism pipeline", session_id="a", repo=R),
            _sess("ComfyUI realism pipeline", session_id="b", repo=R)]
    groups = isc.build_session_groups(recs, corroborated_repos=set())
    assert len(groups) == 1
    assert groups[0]["session_ids"] == {"a", "b"}
    assert groups[0]["slug"] == "comfyui-pipeline-realism"


def test_build_session_groups_title_drift_does_not_merge_by_default():
    # DEFAULT (ENABLE_TITLE_DRIFT_MERGE=False): {comfyui,realism,pipeline} vs
    # {comfyui,nsfw,pipeline} share the distinctive {comfyui,pipeline} but STILL stand as two
    # separate EXACT-group initiatives — Pass-2 drift-merge is off (it produced token salad).
    assert isc.ENABLE_TITLE_DRIFT_MERGE is False           # the shipped default
    recs = [_sess("ComfyUI realism pipeline", session_id="a", repo=R),
            _sess("ComfyUI NSFW pipeline", session_id="b", repo=R)]
    groups = isc.build_session_groups(recs, corroborated_repos=set())
    assert {g["slug"] for g in groups} == {"comfyui-pipeline-realism",
                                           "comfyui-nsfw-pipeline"}
    assert {tuple(sorted(g["session_ids"])) for g in groups} == {("a",), ("b",)}


def test_build_session_groups_title_drift_merges_when_enabled(monkeypatch):
    # With the flag flipped back ON the OLD union-find drift-merge still fuses the pair on the
    # distinctive shared {comfyui,pipeline} (df==2) — the behaviour is preserved, just gated.
    monkeypatch.setattr(isc, "ENABLE_TITLE_DRIFT_MERGE", True)
    recs = [_sess("ComfyUI realism pipeline", session_id="a", repo=R),
            _sess("ComfyUI NSFW pipeline", session_id="b", repo=R)]
    groups = isc.build_session_groups(recs, corroborated_repos=set())
    assert len(groups) == 1
    assert groups[0]["session_ids"] == {"a", "b"}
    assert groups[0]["slug"] == "comfyui-nsfw-pipeline-realism"


def test_build_session_groups_transitive_salad_split_by_default_merged_when_enabled(monkeypatch):
    # The token-SALAD regression: three sessions that pairwise share a single distinctive token
    # (web~delete via {web}, delete~dispatch via {delete}, dispatch~flow via {dispatch}) would,
    # under the transitive union, ALL fuse into one unrecognizable mega-slug. Default OFF keeps
    # them as THREE distinct exact-group initiatives (assert the slugs stay separate)...
    recs = [_sess("web delete", session_id="a", repo=R),
            _sess("delete dispatch", session_id="b", repo=R),
            _sess("dispatch flow", session_id="c", repo=R)]
    groups = isc.build_session_groups(recs, corroborated_repos=set())
    assert {g["slug"] for g in groups} == {"delete-web", "delete-dispatch", "dispatch-flow"}
    assert len(groups) == 3
    # ...while flipping the flag back ON reproduces the old single salad-merged card.
    monkeypatch.setattr(isc, "ENABLE_TITLE_DRIFT_MERGE", True)
    merged = isc.build_session_groups(recs, corroborated_repos=set())
    assert len(merged) == 1
    assert merged[0]["session_ids"] == {"a", "b", "c"}
    assert merged[0]["slug"] == "delete-dispatch-flow-web"     # the sorted-union token salad


def test_build_session_groups_sibling_family_stays_split():
    # Three app-blocks siblings all share {app,blocks} (df==3 > 2 -> NOT distinctive), so
    # they do NOT fuse — the deliberate conservative bar (don't merge app-blocks siblings).
    recs = [_sess("App blocks soft launch", session_id="a", repo=R),
            _sess("App blocks followups review", session_id="b", repo=R),
            _sess("App blocks readiness polish", session_id="c", repo=R)]
    groups = isc.build_session_groups(recs, corroborated_repos=set())
    assert len(groups) == 3


def test_build_session_groups_slug_stable_across_session_order():
    a = _sess("ComfyUI realism pipeline", session_id="a", repo=R)
    b = _sess("ComfyUI NSFW pipeline", session_id="b", repo=R)
    g1 = isc.build_session_groups([a, b], corroborated_repos=set())
    g2 = isc.build_session_groups([b, a], corroborated_repos=set())
    assert g1[0]["slug"] == g2[0]["slug"]          # sorted-token slug is order-independent


def test_build_session_groups_scoped_per_repo():
    # Same topic tokens in two different repos stay two groups (no cross-repo fuse).
    recs = [_sess("ComfyUI realism pipeline", session_id="a", repo="/r/one"),
            _sess("ComfyUI realism pipeline", session_id="b", repo="/r/two")]
    groups = isc.build_session_groups(recs, corroborated_repos=set())
    assert len(groups) == 2


def test_build_session_groups_drops_ineligible_and_repoless():
    recs = [_sess(None, session_id="noai", repo=R),                  # no ai-title
            _sess("Clawgate", session_id="short", repo=R),           # <2 tokens
            _sess("ComfyUI realism pipeline", session_id="x", repo=None)]  # no repo
    assert isc.build_session_groups(recs, corroborated_repos=set()) == []


# --------------------------------------------------------------------------- #
# _make_group_initiative — full contract set, newest-session title/summary
# --------------------------------------------------------------------------- #
def test_make_group_initiative_carries_full_contract_and_newest_title():
    # Same topic tokens (one EXACT group) but different RAW ai-titles (word order): the newest
    # session's raw ai-title drives the card face even though grouping is token-identical.
    recs = [_sess("ComfyUI pipeline realism", session_id="a", last_user_ts=100.0, repo=R),
            _sess("ComfyUI realism pipeline", session_id="b", last_user_ts=900.0, repo=R)]
    groups = isc.build_session_groups(recs, corroborated_repos=set())
    assert len(groups) == 1                                # token-identical -> one exact group
    ini = groups[0]
    assert CONTRACT_KEYS <= set(ini)                       # FULL contract key set present
    assert ini["session_ids"] == {"a", "b"}
    assert ini["source"] == "session" and ini["undocumented"] is True
    assert ini["current_doc"] is None and ini["docs"] == [] and ini["date"] is None
    assert ini["title"] == "ComfyUI realism pipeline"      # newest session's raw ai-title
    assert ini["summary"] == "ComfyUI realism pipeline"    # summary from ai-title (not blank)


# --------------------------------------------------------------------------- #
# opening_message — the thread's origin (genesis) prompt surfaced on every initiative
# --------------------------------------------------------------------------- #
def test_group_opening_message_is_earliest_session_genesis():
    # A multi-session EXACT group's opening_message is the EARLIEST session's genesis (the
    # original ask), NOT the newest — even though the title/summary come from the newest.
    recs = [_sess("ComfyUI realism pipeline", session_id="new", last_user_ts=900.0,
                  genesis="most recent ask, should NOT win", repo=R),
            _sess("ComfyUI realism pipeline", session_id="old", last_user_ts=100.0,
                  genesis="//image-cacher investigate why this 404s", repo=R)]
    groups = isc.build_session_groups(recs, corroborated_repos=set())
    assert len(groups) == 1
    assert groups[0]["opening_message"] == "//image-cacher investigate why this 404s"


def test_group_opening_message_trimmed_to_cap():
    long = "x " * 400                                       # ~800 chars, > OPENING_MAX
    grp = isc.build_session_groups(
        [_sess("ComfyUI realism pipeline", session_id="a", repo=R, genesis=long)],
        corroborated_repos=set())[0]
    assert len(grp["opening_message"]) <= isc.OPENING_MAX + 1   # +1 for the ellipsis
    assert grp["opening_message"].endswith("…")


def test_doc_initiative_opening_message_defaults_empty():
    # Doc/extra initiatives carry no genesis of their own — opening_message defaults to "".
    doc = isc._new_initiative(repo=R, slug="mail-automation", title="Mail automation",
                              summary="doc summary", source="doc")
    assert doc["opening_message"] == ""


def test_combine_anchored_doc_adopts_folded_group_opening_when_empty():
    # An anchored doc with NO opening of its own adopts the folding session group's genesis,
    # so the card still shows where the thread began.
    doc = isc._new_initiative(
        repo=R, slug="mail-automation", title="Mail automation", summary="doc summary",
        current_doc="/r/handoff-mail.md",
        docs=[{"path": "/r/handoff-mail.md", "date": None}], source="doc")
    assert doc["opening_message"] == ""
    grp = _group("Mail automation shipping polish", session_id="sX")
    grp["opening_message"] = "//kick off the mail automation build"
    out = isc.combine_docs_and_groups([doc], [], [grp])
    assert out == [doc]
    assert doc["source"] == "both"
    assert doc["opening_message"] == "//kick off the mail automation build"


def test_combine_anchored_doc_earliest_group_opening_wins():
    # Two groups fold into the same doc; the EARLIEST genesis (min _opening_ts) wins, so the
    # adoption is independent of group iteration order (deterministic).
    doc = isc._new_initiative(repo=R, slug="mail-automation", title="Mail automation",
                              docs=[{"path": "/r/handoff-mail.md", "date": None}],
                              current_doc="/r/handoff-mail.md", source="doc")
    late = _group("Mail automation polish", session_id="late", last_user_ts=900.0)
    late["opening_message"] = "the later ask"
    early = _group("Mail automation shipping", session_id="early", last_user_ts=100.0)
    early["opening_message"] = "the original ask"
    # Pass them newest-first to prove ts (not order) decides.
    out = isc.combine_docs_and_groups([doc], [], [late, early])
    assert out == [doc]
    assert doc["opening_message"] == "the original ask"
    assert "_opening_ts" not in doc and "_adopt_open" not in doc  # internals cleaned up


def test_combine_anchored_doc_keeps_its_own_opening():
    # A doc that already carries an opening_message is NOT overwritten by a folded group.
    doc = isc._new_initiative(repo=R, slug="mail-automation", title="Mail automation",
                              docs=[{"path": "/r/handoff-mail.md", "date": None}],
                              current_doc="/r/handoff-mail.md", source="doc",
                              opening_message="the doc's own origin line")
    grp = _group("Mail automation shipping polish", session_id="sX")
    grp["opening_message"] = "a folded group's origin"
    isc.combine_docs_and_groups([doc], [], [grp])
    assert doc["opening_message"] == "the doc's own origin line"


# --------------------------------------------------------------------------- #
# search_text — the SEARCH-ONLY full user-turn index (v6). All the user's own turns
# across the WHOLE session, so a keyword typed in a MIDDLE turn is findable even though
# the card only surfaces the opening + last-N. NEVER synthesized — real turn text or "".
# --------------------------------------------------------------------------- #
def test_read_session_turns_search_text_collects_ALL_turns_not_just_last_n(tmp_path):
    # A keyword ONLY in a MIDDLE turn lands in search_text but NOT in genesis / the last-N turns.
    p = tmp_path / "s.jsonl"
    _write_transcript(p, [
        _jsonl_user("//image-cacher investigate why this 404s", "2026-07-20T10:00:00Z"),
        _jsonl_user("it's an announcement image, 404ing is a large blast radius",
                    "2026-07-20T10:01:00Z"),  # an EARLY-MIDDLE turn (outside the last-5 window)
        _jsonl_user("dig into the cache headers", "2026-07-20T10:02:00Z"),
        _jsonl_user("dispatch a fix", "2026-07-20T10:03:00Z"),
        _jsonl_user("check the canary", "2026-07-20T10:04:00Z"),
        _jsonl_user("looks good, ship", "2026-07-20T10:05:00Z"),
        _jsonl_user("merge it", "2026-07-20T10:06:00Z"),
    ])
    rec = isc._read_session_turns(str(p), 5)   # display window = last 5
    # search_text carries EVERY genuine turn, joined by newlines, oldest->newest.
    assert rec["search_text"].startswith("//image-cacher investigate why this 404s")
    assert "announcement" in rec["search_text"]
    # ...but "announcement" is NOT in genesis (first turn) nor in the surfaced last-5 turns.
    assert "announcement" not in rec["genesis"]
    assert "announcement" not in " ".join(t["text"] for t in rec["turns"])
    assert rec["search_text"].count("\n") == 6   # 7 turns -> 6 separators


def test_read_session_turns_search_text_skips_harness_noise(tmp_path):
    # search_text is the SAME cleaned/genuine turns as `turns` — noise turns are excluded.
    p = tmp_path / "s.jsonl"
    _write_transcript(p, [
        _jsonl_user("<system-reminder>noise</system-reminder>", "2026-07-20T10:00:00Z"),
        _jsonl_user("real opening ask", "2026-07-20T10:01:00Z"),
        _jsonl_user("[Request interrupted by user]", "2026-07-20T10:02:00Z"),
        _jsonl_user("real second ask", "2026-07-20T10:03:00Z"),
    ])
    rec = isc._read_session_turns(str(p), 5)
    assert rec["search_text"] == "real opening ask\nreal second ask"
    assert "system-reminder" not in rec["search_text"]
    assert "Request interrupted" not in rec["search_text"]


def test_read_session_turns_search_text_hard_capped(tmp_path):
    p = tmp_path / "s.jsonl"
    _write_transcript(p, [
        _jsonl_user("x" * 5000, "2026-07-20T10:00:00Z"),
        _jsonl_user("y" * 5000, "2026-07-20T10:01:00Z"),
    ])
    rec = isc._read_session_turns(str(p), 5)
    assert len(rec["search_text"]) == isc.SEARCH_TEXT_MAX   # hard truncation, no ellipsis
    assert not rec["search_text"].endswith("…")


def test_new_initiative_search_text_defaults_empty():
    doc = isc._new_initiative(repo=R, slug="mail-automation", title="Mail automation",
                              source="doc")
    assert doc["search_text"] == ""


def test_group_search_text_concatenates_all_sessions_and_dedups():
    # A merged group pools EVERY session's search_text (oldest->newest), dedup lines, re-cap.
    recs = [_sess("ComfyUI realism pipeline", session_id="new", last_user_ts=900.0, repo=R,
                  genesis="newer ask", search_text="newer ask\nshared line"),
            _sess("ComfyUI realism pipeline", session_id="old", last_user_ts=100.0, repo=R,
                  genesis="older ask", search_text="older ask\nshared line\nannouncement here")]
    grp = isc.build_session_groups(recs, corroborated_repos=set())[0]
    st = grp["search_text"]
    assert "announcement here" in st              # a mid-session keyword from one session
    assert "older ask" in st and "newer ask" in st
    assert st.count("shared line") == 1           # dedup across sessions
    assert st.index("older ask") < st.index("newer ask")   # oldest->newest ordering


def test_group_search_text_recapped_at_max():
    recs = [_sess("ComfyUI realism pipeline", session_id="a", last_user_ts=100.0, repo=R,
                  search_text="a" * 5000),
            _sess("ComfyUI realism pipeline", session_id="b", last_user_ts=200.0, repo=R,
                  search_text="b" * 5000)]
    grp = isc.build_session_groups(recs, corroborated_repos=set())[0]
    assert len(grp["search_text"]) == isc.SEARCH_TEXT_MAX


def test_combine_anchored_doc_adopts_folded_group_search_text():
    # A doc (default search_text "") gains the folding group's full-text index.
    doc = isc._new_initiative(
        repo=R, slug="mail-automation", title="Mail automation",
        current_doc="/r/handoff-mail.md",
        docs=[{"path": "/r/handoff-mail.md", "date": None}], source="doc")
    assert doc["search_text"] == ""
    grp = _group("Mail automation shipping polish", session_id="sX",
                 search_text="kick off\nthe announcement pointer fix")
    isc.combine_docs_and_groups([doc], [], [grp])
    assert "announcement pointer fix" in doc["search_text"]
    assert "_adopt_search" not in doc            # internal accumulator cleaned up


def test_combine_multiple_groups_concat_search_text_ordered_and_recapped():
    doc = isc._new_initiative(repo=R, slug="mail-automation", title="Mail automation",
                              docs=[{"path": "/r/handoff-mail.md", "date": None}],
                              current_doc="/r/handoff-mail.md", source="doc")
    late = _group("Mail automation polish", session_id="late", last_user_ts=900.0,
                  search_text="LATER session words")
    early = _group("Mail automation shipping", session_id="early", last_user_ts=100.0,
                   search_text="EARLIER session words")
    # Pass newest-first to prove the merge orders by ts, not iteration order.
    isc.combine_docs_and_groups([doc], [], [late, early])
    st = doc["search_text"]
    assert "EARLIER session words" in st and "LATER session words" in st
    assert st.index("EARLIER") < st.index("LATER")   # oldest->newest, deterministic


def test_doc_only_initiative_search_text_defaults_empty():
    # A doc/extra initiative with no session keeps search_text "" through _doc_to_initiative.
    d = {"repo": R, "slug": "notes", "title": "Some notes", "summary": None, "date": None,
         "mtime": 100.0, "next_step": None, "open_investigations": [],
         "path": "/r/claudedocs/notes.md"}
    ini = isc._doc_to_initiative(d)
    assert ini["search_text"] == ""


# --------------------------------------------------------------------------- #
# git_toplevel + discover_repos union with session/telem cwds
# --------------------------------------------------------------------------- #
def test_git_toplevel_returns_realpath_or_none(monkeypatch):
    monkeypatch.setattr(isc, "_run", lambda cmd, timeout=20.0: "/home/u/workspace/devrc\n")
    # non-existent path -> realpath is a no-op, so it round-trips unchanged.
    assert isc.git_toplevel("/home/u/workspace/devrc/scripts") == "/home/u/workspace/devrc"
    monkeypatch.setattr(isc, "_run", lambda cmd, timeout=20.0: "")  # not a repo
    assert isc.git_toplevel("/home/u") is None
    assert isc.git_toplevel(None) is None


def test_discover_repos_unions_session_cwds(monkeypatch, tmp_path):
    # A repo with NO handoff/doc glob hit still surfaces via a session cwd's git toplevel.
    doc_repo = tmp_path / "hasdoc"
    (doc_repo / "claudedocs").mkdir(parents=True)
    (doc_repo / "claudedocs" / "handoff-x.md").write_text("# x\n")
    sess_repo = str(tmp_path / "civit" / "civitai-manager")

    monkeypatch.setattr(isc, "git_toplevel",
                        lambda cwd: sess_repo if cwd and cwd.startswith(sess_repo) else None)
    monkeypatch.setattr(isc, "_git_common_dir", lambda p: None)  # treat each as own repo

    repos = isc.discover_repos(str(tmp_path),
                               session_cwds=[sess_repo + "/sub"], telem_cwds=[])
    assert str(doc_repo) in repos          # doc-glob source
    assert sess_repo in repos              # session-cwd source (invisible to doc glob)


def test_discover_repos_no_cwds_is_backward_compatible(monkeypatch, tmp_path):
    # Called with no cwds (viewer/route) -> doc-glob set only, dedup unchanged.
    plain = tmp_path / "loose"
    (plain / "claudedocs").mkdir(parents=True)
    monkeypatch.setattr(isc, "_candidate_repo_dirs", lambda ws: [str(plain)])
    monkeypatch.setattr(isc, "_git_common_dir", lambda p: None)
    assert isc.discover_repos(str(tmp_path)) == [str(plain)]


# --------------------------------------------------------------------------- #
# read_handoff / _doc_is_handoff_structured — the anti-flood structural gate
# --------------------------------------------------------------------------- #
def test_read_handoff_flags_handoff_and_structure(tmp_path):
    repo = tmp_path / "r"
    (repo / "claudedocs").mkdir(parents=True)
    h = repo / "claudedocs" / "handoff-thing.md"
    h.write_text("# Handoff: thing\n## Next steps\n1. go\n")
    d = isc.read_handoff(str(h))
    assert d["is_handoff"] is True and d["handoff_structured"] is True


def test_doc_is_handoff_structured_variants():
    assert isc._doc_is_handoff_structured("random notes\n", is_handoff=False) is False
    assert isc._doc_is_handoff_structured("random notes\n", is_handoff=True) is True
    assert isc._doc_is_handoff_structured("# Session Handoff\nprose\n", is_handoff=False)
    assert isc._doc_is_handoff_structured("## Next steps\n1. do it\n", is_handoff=False)
    assert not isc._doc_is_handoff_structured("# Design doc\n## Goals\nx\n", is_handoff=False)


# --------------------------------------------------------------------------- #
# combine_docs_and_groups — anchor / standalone / dormant-doc gating
# --------------------------------------------------------------------------- #
def _group(ai_title, repo=R, session_id="g0", last_user_ts=900.0, search_text=None):
    return isc.build_session_groups(
        [_sess(ai_title, session_id=session_id, repo=repo, last_user_ts=last_user_ts,
               search_text=search_text)],
        corroborated_repos=set())[0]


def test_combine_doc_anchors_group_overlays_and_keeps_doc_slug():
    doc = isc._new_initiative(
        repo=R, slug="mail-automation", title="Mail automation",
        summary="doc summary", next_step="ship it", current_doc="/r/handoff-mail.md",
        docs=[{"path": "/r/handoff-mail.md", "date": None}], source="doc")
    grp = _group("Mail automation shipping polish", session_id="sX")
    out = isc.combine_docs_and_groups([doc], [], [grp])
    assert out == [doc]                                    # group folded into the doc
    assert doc["source"] == "both" and doc["undocumented"] is False
    assert doc["slug"] == "mail-automation"                # doc slug preserved (byte-identical)
    assert "sX" in doc["session_ids"]                      # session credited to the doc
    assert doc["summary"] == "doc summary"                 # doc fields win (overlay)


def test_combine_group_with_no_doc_stands_alone_undocumented():
    grp = _group("ComfyUI realism pipeline", session_id="sY")
    out = isc.combine_docs_and_groups([], [], [grp])
    assert out == [grp]
    assert grp["source"] == "session" and grp["undocumented"] is True
    assert grp["current_doc"] is None
    assert grp["summary"] == "ComfyUI realism pipeline"    # card face from ai-title


def test_combine_dormant_nonhandoff_docs_all_dropped_when_unanchored():
    # SESSIONS-ONLY: with NO session anchoring them, EVERY doc/extra is dropped — even a
    # handoff-structured one. A doc never creates a card on its own any more.
    structured = isc._doc_to_initiative({
        "repo": R, "slug": "SESSION-HANDOFF", "title": "Session Handoff notes",
        "summary": "s", "date": None, "mtime": 1.0, "next_step": "go",
        "open_investigations": [], "path": "/r/claudedocs/SESSION-HANDOFF.md",
        "is_handoff": False, "handoff_structured": True})
    plain = isc._doc_to_initiative({
        "repo": R, "slug": "DESIGN", "title": "Design", "summary": "s", "date": None,
        "mtime": 1.0, "next_step": None, "open_investigations": [],
        "path": "/r/claudedocs/DESIGN.md", "is_handoff": False,
        "handoff_structured": False})
    out = isc.combine_docs_and_groups([], [structured, plain], [])
    assert out == []                       # no session behind either -> neither emitted


def test_combine_anchor_overrides_structural_gate_for_nonhandoff_doc():
    # An UNSTRUCTURED non-handoff doc that a session ANCHORS is kept (source=both).
    design = isc._doc_to_initiative({
        "repo": R, "slug": "COMFYUI-DESIGN", "title": "ComfyUI integration design",
        "summary": "s", "date": None, "mtime": 1.0, "next_step": None,
        "open_investigations": [], "path": "/r/claudedocs/COMFYUI-DESIGN.md",
        "is_handoff": False, "handoff_structured": False})
    grp = _group("ComfyUI integration pipeline", session_id="sZ")
    out = isc.combine_docs_and_groups([], [design], [grp])
    assert design in out and design["source"] == "both"
    assert "sZ" in design["session_ids"]


# --------------------------------------------------------------------------- #
# session_ids crediting in attribution
# --------------------------------------------------------------------------- #
def test_attribute_sessions_credits_by_session_membership():
    ini = {"slug": "comfyui-nsfw-pipeline", "docs": [], "session_ids": {"sA"}}
    genesis = [{"text": "totally unrelated genesis", "mtime": 1.0,
                "last_user_ts": 500.0, "session_id": "sA"},
               {"text": "another unrelated", "mtime": 2.0,
                "last_user_ts": 700.0, "session_id": "sB"}]
    isc.attribute_sessions([ini], genesis)
    assert ini["session_count"] == 1        # only sA belongs (by membership, not genesis text)
    assert ini["last_session"] == 500.0


def test_attribute_recent_messages_credits_by_session_membership():
    ini = {"slug": "comfyui-nsfw-pipeline", "repo": R, "docs": [], "session_ids": {"sA"}}
    records = [_sess("ComfyUI realism pipeline", session_id="sA", repo=R,
                     genesis="no handoff named here", cwd=None, branch=None,
                     turns=[{"text": "wire the comfy pipeline", "ts": 500.0}])]
    isc.attribute_recent_messages([ini], records, [R])
    assert [m["text"] for m in ini["recent_messages"]] == ["wire the comfy pipeline"]


# --------------------------------------------------------------------------- #
# _read_session_turns — ai-title / last-prompt / n_turns capture
# --------------------------------------------------------------------------- #
def test_read_session_turns_captures_ai_title_last_prompt_and_n_turns(tmp_path):
    p = tmp_path / "s.jsonl"
    lines = [
        _json.dumps({"type": "ai-title", "aiTitle": "Old title", "sessionId": "sid"}),
        _jsonl_user("first real turn", "2026-07-20T10:00:00Z"),
        _json.dumps({"type": "last-prompt", "lastPrompt": "the last prompt text"}),
        _jsonl_user("second real turn", "2026-07-20T10:01:00Z"),
        _json.dumps({"type": "ai-title", "aiTitle": "ComfyUI realism pipeline"}),
        _jsonl_user("third real turn", "2026-07-20T10:02:00Z"),
    ]
    _write_transcript(p, lines)
    rec = isc._read_session_turns(str(p), 5)
    assert rec["ai_title"] == "ComfyUI realism pipeline"   # LAST ai-title wins
    assert rec["last_prompt"] == "the last prompt text"
    assert rec["n_turns"] == 3                             # genuine user turns only


def test_collect_session_records_sets_session_id_from_filename(tmp_path):
    root = tmp_path
    f = root / "proj" / "abc-123.jsonl"
    f.parent.mkdir(parents=True)
    _write_transcript(f, [_jsonl_user("hello", "2026-07-20T10:00:00Z")])
    recs = isc.collect_session_records(str(root), 3650)
    assert recs[0]["session_id"] == "abc-123"


# --------------------------------------------------------------------------- #
# End-to-end: a repo with NO handoff doc + a resolving session -> ONE
# session-derived initiative carrying the FULL contract key set.
# --------------------------------------------------------------------------- #
def test_build_report_session_only_initiative_full_contract(tmp_path, monkeypatch):
    import calendar
    now = float(calendar.timegm((2026, 7, 25, 0, 0, 0, 0, 0, 0)))
    repo = tmp_path / "civit" / "civitai-manager"
    repo.mkdir(parents=True)                               # NO claudedocs/ at all
    monkeypatch.setattr(isc, "git_branches", lambda r: [])
    monkeypatch.setattr(isc, "git_default_branch", lambda r: "main")
    monkeypatch.setattr(isc, "gh_open_prs", lambda r: [])
    monkeypatch.setattr(isc, "gh_merged_prs", lambda r, d: [])
    monkeypatch.setattr(isc, "worktree_canonical_map", lambda repos: {})

    rec = _sess("ComfyUI NSFW realism pipeline", session_id="sess-1",
                cwd=str(repo), last_user_ts=now - 3600, n_turns=10,
                genesis="start the comfyui work",
                turns=[{"text": "run the comfyui optimization loop", "ts": now - 3600}])
    monkeypatch.setattr(isc, "collect_session_records", lambda root, d, n=5: [rec])

    report = isc.build_report(14, repos=[str(repo)], client=None, now=now)
    inis = report["by_repo"][str(repo)]
    assert len(inis) == 1
    ini = inis[0]
    assert CONTRACT_KEYS <= set(ini)                       # FULL contract set — no missing key
    assert ini["source"] == "session" and ini["undocumented"] is True
    assert ini["slug"] == "comfyui-nsfw-pipeline-realism"
    assert ini["title"] == "ComfyUI NSFW realism pipeline"
    assert ini["session_count"] == 1                       # credited via session_ids
    assert ini["momentum"] == "active"                     # last user turn ~1h ago
    assert ini["session_ids"] == ["sess-1"]                # set -> sorted list in the report
    assert [m["text"] for m in ini["recent_messages"]] == ["run the comfyui optimization loop"]


def test_build_report_session_anchors_existing_handoff_slug_unchanged(tmp_path, monkeypatch):
    # A session on the SAME topic as an existing handoff must anchor it (source=both) and
    # leave the handoff's byte-identical slug/doc intact — the hard no-churn guarantee.
    import calendar
    now = float(calendar.timegm((2026, 7, 25, 0, 0, 0, 0, 0, 0)))
    repo = tmp_path / "devrc"
    (repo / "claudedocs").mkdir(parents=True)
    (repo / "claudedocs" / "handoff-mail-automation-2026-07-24.md").write_text(NEXT_DOC)
    monkeypatch.setattr(isc, "git_branches", lambda r: [])
    monkeypatch.setattr(isc, "git_default_branch", lambda r: "main")
    monkeypatch.setattr(isc, "gh_open_prs", lambda r: [])
    monkeypatch.setattr(isc, "gh_merged_prs", lambda r, d: [])
    monkeypatch.setattr(isc, "worktree_canonical_map", lambda repos: {})

    rec = _sess("Mail automation shipping polish", session_id="sess-9",
                cwd=str(repo), last_user_ts=now - 3600, n_turns=10,
                genesis="continue mail automation")
    monkeypatch.setattr(isc, "collect_session_records", lambda root, d, n=5: [rec])

    report = isc.build_report(14, repos=[str(repo)], client=None, now=now)
    inis = report["by_repo"][str(repo)]
    assert len(inis) == 1                                  # NOT duplicated into a 2nd card
    ini = inis[0]
    assert ini["slug"] == "mail-automation"                # byte-identical handoff slug
    assert ini["source"] == "both"                         # doc anchored the session group
    assert ini["session_ids"] == ["sess-9"]


# --------------------------------------------------------------------------- #
# SESSIONS-ONLY board: docs enrich but never create/float/time a card
# --------------------------------------------------------------------------- #
def test_build_report_unanchored_handoff_is_not_emitted(tmp_path, monkeypatch):
    # A handoff with NO session behind it must NOT become a card (owner: "sessions only").
    import calendar
    now = float(calendar.timegm((2026, 7, 25, 0, 0, 0, 0, 0, 0)))
    repo = tmp_path / "devrc"
    (repo / "claudedocs").mkdir(parents=True)
    (repo / "claudedocs" / "handoff-mail-automation-2026-07-24.md").write_text(NEXT_DOC)
    monkeypatch.setattr(isc, "git_branches", lambda r: [])
    monkeypatch.setattr(isc, "git_default_branch", lambda r: "main")
    monkeypatch.setattr(isc, "gh_open_prs", lambda r: [])
    monkeypatch.setattr(isc, "gh_merged_prs", lambda r, d: [])
    monkeypatch.setattr(isc, "worktree_canonical_map", lambda repos: {})
    monkeypatch.setattr(isc, "collect_session_records", lambda root, d, n=5: [])  # no sessions

    report = isc.build_report(14, repos=[str(repo)], client=None, now=now)
    assert report["by_repo"].get(str(repo), []) == []      # pure-doc card dropped


def test_build_report_both_card_adopts_doc_summary_and_next_step(tmp_path, monkeypatch):
    # A session that best-title-matches a handoff becomes source="both" AND adopts the
    # handoff's summary/next_step for the expanded card detail (enrichment preserved).
    import calendar
    now = float(calendar.timegm((2026, 7, 25, 0, 0, 0, 0, 0, 0)))
    repo = tmp_path / "devrc"
    (repo / "claudedocs").mkdir(parents=True)
    (repo / "claudedocs" / "handoff-mail-automation-2026-07-24.md").write_text(NEXT_DOC)
    monkeypatch.setattr(isc, "git_branches", lambda r: [])
    monkeypatch.setattr(isc, "git_default_branch", lambda r: "main")
    monkeypatch.setattr(isc, "gh_open_prs", lambda r: [])
    monkeypatch.setattr(isc, "gh_merged_prs", lambda r, d: [])
    monkeypatch.setattr(isc, "worktree_canonical_map", lambda repos: {})
    sess = _sess("Mail automation shipping polish", session_id="sess-9", cwd=str(repo),
                 last_user_ts=now - 3600, n_turns=10)
    monkeypatch.setattr(isc, "collect_session_records", lambda root, d, n=5: [sess])

    report = isc.build_report(14, repos=[str(repo)], client=None, now=now)
    ini = report["by_repo"][str(repo)][0]
    assert ini["source"] == "both"
    assert ini["summary"] == "Automate the inbox."        # adopted from the handoff Goal
    assert ini["next_step"].startswith("Ship the extractor")  # adopted from the handoff
    assert ini["session_ids"] == ["sess-9"]


def test_build_report_both_card_timed_by_session_not_doc_mtime(tmp_path, monkeypatch):
    # THE timing guarantee: a `both` card whose handoff is FRESH (dateless -> fs mtime,
    # written just now) but whose last SESSION turn is 3 days old must read the SESSION
    # age (slowing), never the doc mtime (which would read active). last_touch must equal
    # the session turn exactly — proving doc mtime no longer contributes.
    import calendar
    now = float(calendar.timegm((2026, 7, 25, 0, 0, 0, 0, 0, 0)))
    repo = tmp_path / "devrc"
    (repo / "claudedocs").mkdir(parents=True)
    # Dateless handoff -> doc_touch_epoch falls back to fs mtime (which is real-now, fresh).
    (repo / "claudedocs" / "handoff-mail-automation.md").write_text(NEXT_DOC)
    monkeypatch.setattr(isc, "git_branches", lambda r: [])
    monkeypatch.setattr(isc, "git_default_branch", lambda r: "main")
    monkeypatch.setattr(isc, "gh_open_prs", lambda r: [])
    monkeypatch.setattr(isc, "gh_merged_prs", lambda r, d: [])
    monkeypatch.setattr(isc, "worktree_canonical_map", lambda repos: {})
    old_turn = now - 3 * isc.DAY
    sess = _sess("Mail automation shipping polish", session_id="sess-9", cwd=str(repo),
                 last_user_ts=old_turn, n_turns=10)
    monkeypatch.setattr(isc, "collect_session_records", lambda root, d, n=5: [sess])

    report = isc.build_report(14, repos=[str(repo)], client=None, now=now)
    ini = report["by_repo"][str(repo)][0]
    assert ini["source"] == "both"
    assert ini["last_touch"] == old_turn                  # SESSION turn, NOT the fresh doc mtime
    assert ini["momentum"] == "slowing"                   # 3d old; would be "active" off doc mtime


def test_build_report_source_distribution_has_no_pure_doc(tmp_path, monkeypatch):
    # A mixed repo: one session anchors a handoff (both), one standalone session
    # (session), one handoff with no session (dropped). The emitted `source` set is
    # exactly {session, both} — zero pure `doc`.
    import calendar
    now = float(calendar.timegm((2026, 7, 25, 0, 0, 0, 0, 0, 0)))
    repo = tmp_path / "devrc"
    (repo / "claudedocs").mkdir(parents=True)
    (repo / "claudedocs" / "handoff-mail-automation-2026-07-24.md").write_text(NEXT_DOC)
    (repo / "claudedocs" / "handoff-orphan-doc-2026-07-24.md").write_text(
        "# Handoff: orphan doc\n## Next steps\n1. go\n")   # NO session -> must be dropped
    monkeypatch.setattr(isc, "git_branches", lambda r: [])
    monkeypatch.setattr(isc, "git_default_branch", lambda r: "main")
    monkeypatch.setattr(isc, "gh_open_prs", lambda r: [])
    monkeypatch.setattr(isc, "gh_merged_prs", lambda r, d: [])
    monkeypatch.setattr(isc, "worktree_canonical_map", lambda repos: {})
    anchoring = _sess("Mail automation shipping polish", session_id="s-both",
                      cwd=str(repo), last_user_ts=now - 3600, n_turns=10)
    standalone = _sess("Grafana alert drift dashboards", session_id="s-solo",
                       cwd=str(repo), last_user_ts=now - 3600, n_turns=10)
    monkeypatch.setattr(isc, "collect_session_records",
                        lambda root, d, n=5: [anchoring, standalone])

    report = isc.build_report(14, repos=[str(repo)], client=None, now=now)
    inis = report["by_repo"][str(repo)]
    slugs = {i["slug"] for i in inis}
    sources = {i["source"] for i in inis}
    assert sources == {"session", "both"}                 # zero pure "doc"
    assert "doc" not in sources
    assert "orphan-doc" not in slugs                      # unanchored handoff dropped
    assert "mail-automation" in slugs                     # anchored handoff survives as both
