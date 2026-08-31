#!/usr/bin/env python3
"""Deterministic audit of the `/analyze-service` index store (the /prune-index auditor).

🔴 READ-ONLY. This module opens no file for writing, creates no directory, and
runs no git command inside the store. The store is curated, client-confidential
and has NO off-machine backup (`claude/skills/analyze-service/reference/index-store.md`
-> "Store safety"), so the audit is a MEASUREMENT and the /prune-index skill is
what carries the confirm-gated write half. `_git()` below refuses any repo path
that lies inside the store root, so even the pointer checks cannot reach it.

WHY THIS EXISTS
---------------
The store grows monotonically and nothing applies pressure. Measured
2026-08-21T18:52Z against the live store: 77 entries / 359,395 B, median entry
3,002 B, largest 27,770 B. The value it delivers is RECENCY-ORDERED SELECTION —
`subsystem_recall` surfaces the entries most likely to matter — and selection is
exactly the property that degrades as entries grow. So this is not tidiness.

🔴 THE ROOT CAUSE IS A RULE CONTRADICTION, NOT LAZINESS. `write-back.md` said
"Prune-on-resolve -- when a gotcha is fixed, REMOVE the bullet", while the newer
`OPEN:` / `RESOLVED <sha>:` schema in `index-store.md` says MARK it, don't remove
it. Both were live and both were followed, so `RESOLVED` bullets accumulate
forever. This PR reconciles the docs; this script is the instrument that makes
the reconciled lifecycle checkable:

  * an `OPEN:` bullet ALWAYS STAYS — never an eviction candidate, at any age;
  * a `RESOLVED <sha>` bullet becomes an eviction CANDIDATE only once its content
    has a HOME — a pointer target (a path, a commit sha, a PR/issue ref) that is
    verified to exist;
  * a `RESOLVED` bullet with no reachable home is reported `NO HOME - write the
    record first`, NEVER as evictable. That is the interesting finding, not an
    edge case: it names the bullets whose durable form was never written down.
  * NOTHING IS EVER AUTO-EVICTED. This reports; a human confirms. The cut runs
    the `prune-index` skill's confirm-gated, diff-first contract
    (`claude/skills/prune-index/reference/writing-and-safety.md`). 🔴 NOT the
    APPEND protocol: that pointer used to name `write-back.md`, whose copy was
    retired on 2026-08-31 when the y/N went and the one append protocol moved to
    `subsystem-index/SKILL.md`. A prune keeps its prompt on purpose — the
    evidence that retired the append one ("the answer was always `y`") was
    measured on an append, and a cut removes bytes that are often their
    content's only copy.

WHAT A ZERO HERE MEANS
----------------------
🔴 Every count is printed beside its DENOMINATOR ("N entries examined", "N
pointers checked", "N refs resolved"). A reassuring zero from a scan that walked
nothing is indistinguishable from a clean store, and `claude/RULES.md` ("Validate
the INSTRUMENT") is explicit that the zero alone is not a reading. Where a check
CANNOT be performed — a scope whose owning repo is not derivable, a PR ref that
would need the network — it is reported as `NOT CHECKED` with its count, never
folded into a clean total.

Usage:
  subsystem-audit.py                     audit the whole store
  subsystem-audit.py --scope devrc       one scope
  subsystem-audit.py --store PATH        a different store root (tests use this)
  --all           list every entry in the size table (default: over-budget only)
  --detail N      entries to render a per-entry lifecycle block for (default 8)
  --check-prs     ALSO verify PR/issue refs with `gh` (network; off by default,
                  because a deterministic offline run is what makes this gateable)

Sibling of scripts/skill-audit.py (SKILL.md bodies) and scripts/memory-audit.py
(the per-session MEMORY.md index). The per-entry byte budget is OWNED, with its
derivation, by scripts/tests/test_subsystem_audit_budget.py — read the numbers
there, never restate them.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from subsystem_resolver import (  # noqa: E402
    AmbiguousRefError,
    NUANCE_HEADING,
    ON_MALFORMED_COLLECT,
    POINTERS_HEADING,
    extract_sections,
    load_index,
    normalize_ref,
    parse_front_matter,
    parse_journal_bullets,
    resolve_ref_tiered,
)

DEFAULT_STORE_ROOT = Path.home() / ".claude" / "analyze-service-index"

# --- budgets -------------------------------------------------------------------
# 🔴 THESE TWO LITERALS ARE PINNED, NOT OWNED, HERE.
# `scripts/tests/test_subsystem_audit_budget.py` owns them: it DERIVES both from
# stated reasoning and asserts these copies equal what it derived, printing the
# eviction playbook when they disagree. Change the number there, never here — a
# second hand-maintained copy is exactly how a budget drifts away from its
# justification (`claude/RULES.md` -> "One rule, one place").
#
# The one-sentence justifications, restated here only so a reader of the OUTPUT
# knows what the number claims (the argument and its measurements live in the
# test):
#   TARGET 6,144 B — the store's own demonstrated shape: 64 of 77 live entries
#     (83%) already sit under 6 KiB, measured 2026-08-21T18:52Z.
#   HARD  12,288 B — the PROVEN SKILL.md body budget
#     (scripts/browser-bridge/tests/test_skill_size.py::MAX_BYTES); an index
#     ENTRY heavier than an entire skill body has stopped being a pointer sheet
#     and become the archive it is supposed to point at.
TARGET = 6_144
HARD = 12_288

BUDGET_JUSTIFICATION = (
    "target = the store's own demonstrated shape (most live entries already fit); "
    "hard cap = the proven SKILL.md body budget, past which an ENTRY outweighs a "
    "whole skill and has stopped being a pointer sheet"
)
BUDGET_OWNER = "scripts/tests/test_subsystem_audit_budget.py"

# --- acknowledged over-cap entries ----------------------------------------------
# 🔴 AN ENUMERATION, NOT A THRESHOLD — the same shape as `run-tests.sh`'s
# `EXPECTED_SKIPS`, and for the same reason.
#
# WHY THIS EXISTS. A handful of entries are over the hard cap and CANNOT be
# brought under it by the lifecycle this auditor owns: what remains in them is
# `OPEN:` bullets (never evictable, at any age or size) and gotchas whose only
# written form is that bullet. Before this list, `## verdict` therefore printed
# `⚠ prune needed` on every run, forever, with no action that could ever clear
# it — and `claude/RULES.md` is explicit that a permanently-red gate is worse
# than no gate, because it trains everyone to stop reading the verdict.
#
# 🔴 WHAT IT IS NOT. It is not a raised number, not a ratio, not a pattern, and
# not env-overridable. An over-cap entry that is NOT spelled here is still a
# finding, so new bloat is caught exactly as before. Raising the CAP would have
# blessed every future entry up to the new number; naming five files blesses
# five files.
#
# 🔴 ACKNOWLEDGED NEVER MEANS INVISIBLE. `render()` prints the count and names
# every one of them on every run, in their own block, whether or not the verdict
# is clean — an exemption nobody can see is how a list like this rots into a
# permanent excuse.
#
# 🔴 IT IS PINNED IN BOTH DIRECTIONS, BY THE AUDIT ITSELF (`account_acknowledged`),
# so the pin runs against the LIVE store on every invocation rather than only in
# a hermetic suite that cannot see it:
#   * an entry over the cap that is NOT listed here          -> a finding;
#   * a listed entry that is no longer over the cap          -> a STALE
#     ACKNOWLEDGEMENT finding: delete the line, the exemption has been earned
#     away. This is the direction that decays silently, which is why it is a
#     finding and not a comment;
#   * a listed entry that no longer exists at all            -> also a finding
#     (renamed or deleted; the list has rotted).
#
# 🔴 THE ADMISSION TEST, so a sixth line cannot be added by sentiment. An entry
# qualifies only when evicting EVERY bullet this auditor classes `EVICTABLE`
# still leaves it over the cap — i.e. lifecycle pruning provably cannot reach it.
# Measured 2026-08-22 by simulating the removal (size -> size after evicting all
# EVICTABLE bullets):
#
#   datapacket-talos/tekton-builds.md     26,935 -> 20,917  (3 evictable, 6,018 B)
#   datapacket-talos/storage-resolver.md  23,765 -> 22,347  (1 evictable, 1,418 B)
#   datapacket-talos/monitoring.md        23,266 -> 23,266  (0 evictable)
#   civitai/blocks.md                     14,052 -> 14,052  (0 evictable)
#   devrc/subsystem-index.md              12,295 -> 12,295  (0 evictable)
#
# ⚠ `homelab-talos/subsystem-store.md` (13,299 B) was a candidate and is
# DELIBERATELY ABSENT: evicting its 2 EVICTABLE bullets (1,193 B) lands it at
# 12,106 B, UNDER the cap. Lifecycle pruning CAN reach it, so it is a real,
# actionable finding and the audit keeps reporting it. Prune it via
# `/prune-index` and the hard-cap line goes clean on its own — which is the
# proof that this list did not simply silence the gate.
#
# Keys are `<scope>/<filename>` — exactly `EntryAudit.where`, and exactly the
# names the size table already prints. 🔴 NO ENTRY CONTENT MAY BE RECORDED HERE
# OR ANYWHERE IN THIS REPO: the store is client-confidential and devrc is
# PUBLIC. Names and aggregate byte counts are the most that may be written down.
_ACK_REASON = (
    "remaining content is OPEN bullets or gotchas with no other written form; "
    "lifecycle pruning cannot reduce it further"
)

ACKNOWLEDGED_OVER_CAP: dict[str, str] = {
    "datapacket-talos/tekton-builds.md": _ACK_REASON
    + " — 3 EVICTABLE bullets (6,018 B); evicting all of them still leaves 20,917 B",
    "datapacket-talos/storage-resolver.md": _ACK_REASON
    + " — 1 EVICTABLE bullet (1,418 B); evicting it still leaves 22,347 B",
    "datapacket-talos/monitoring.md": _ACK_REASON
    + " — 0 EVICTABLE bullets; the lifecycle has nothing to remove",
    "civitai/blocks.md": _ACK_REASON
    + " — 0 EVICTABLE bullets; the lifecycle has nothing to remove",
    "devrc/subsystem-index.md": _ACK_REASON
    + " — 0 EVICTABLE bullets; 7 B over, and the 7 B are OPEN/no-other-form content",
}

# The schema (`index-store.md` -> "## Nuance / work-history") says "dated bullets,
# newest-first, <=2 lines each".
#
# ⚠ ADVISORY, DELIBERATELY NOT PART OF THE VERDICT. Measured 2026-08-21: 428 of
# 518 live bullets (82.6%) exceed two lines, and `subsystem_resolver.JournalBullet`
# documents the same finding independently ("a real bullet is WRAPPED PROSE ...
# the median bullet is 3 lines"). Gating on it would make this a permanently-red
# gate, which `claude/RULES.md` says is worse than no gate — it trains everyone to
# click through. It is reported with its denominator so the disagreement between
# the schema and the corpus stays VISIBLE, and the worst offenders are named as
# trim candidates.
BULLET_LINE_LIMIT = 2

# --- pointer-target recognition -------------------------------------------------

_BACKTICKED = re.compile(r"`([^`\n]+)`")
_STRIP = "`,;:()[]{}<>\"'*_ "

# A bare hex run of 7..40 chars. Used ONLY on a token already isolated by
# backticks or by the `RESOLVED <sha>:` grammar — never scanned free-form over
# prose, where it would match ordinary words like `deadbeef` or a decimal id.
_SHA = re.compile(r"\A[0-9a-f]{7,40}\Z")

# `#123` or `owner/repo#123`. Anchored to a boundary so a markdown heading (`#
# Foo`) and a CSS colour cannot match.
_PR_REF = re.compile(r"(?:^|[\s(\[])((?:[\w.-]+/[\w.-]+)?#\d+)\b")

# 🔴 A FLOOR, NOT A LIST — the same discipline as `JournalBullet.unmarked_action`.
# An entry quotes plenty of `a/b` tokens that are NOT paths: k8s `namespace/pod`,
# `owner/repo`, a Grafana `folder/dash`. Treating those as paths would flood the
# report with phantom "missing pointer" findings and train the reader to ignore
# the section. So a token counts as a PATH only when it also looks like one:
# either it ends in a source/doc extension, or it starts under a directory this
# repo family actually uses. Anything else is NOT COUNTED AT ALL — it is not
# reported as missing, and it is not reported as present either. Every renderer
# of this prints "at least N", never "N paths exist".
_PATH_EXT = re.compile(
    r"\.(?:md|py|sh|zsh|bash|ya?ml|json|jsonc|nix|ts|tsx|js|jsx|go|rs|tf|toml|ini|sql|conf|service|timer)\Z"
)
_PATH_PREFIX = (
    "claudedocs/", "scripts/", "docs/", "nix/", ".claude/", "src/", "apps/",
    "charts/", "containers/", "clusters/", "kubernetes/", "k8s/", "infra/",
    "githooks/", "cmd/", "lib/", "tests/", "test/",
)

TARGET_PATH = "path"
TARGET_SHA = "sha"
TARGET_PR = "pr"

# The three lifecycle verdicts a RESOLVED bullet can carry. Named constants
# because three surfaces branch on them and a typo in a string literal is a
# silently-missing branch.
EVICTABLE = "EVICTABLE"
NO_HOME = "NO HOME"
UNVERIFIED = "NOT CHECKED"


def _is_pathish(token: str) -> bool:
    """Is this token a FILE PATH? A narrow floor — see `_PATH_PREFIX`.

    Each rejection below was measured against the live corpus, and dropping any
    of them floods the pointer section with phantom findings:

      * brace/glob syntax (`scripts/x/{a.sh,b.sh}`) is a SET of paths written as
        one token — statting it always fails, so counting it manufactures a
        broken pointer out of a correctly-written line;
      * a single-segment absolute (`/dp-build-deploy`) is a route, a pipeline
        name or an HTTP path far more often than a file — it carries no
        extension and no directory shape, so there is nothing to check;
      * a PLACEHOLDER segment (`reference/sites/<host>.md`) names a whole
        directory through a variable the reader resolves at run time — the same
        convention `skill-audit.py`'s `REF_PATH` deliberately excludes. Statting
        the literal string always fails, so counting it invents a broken pointer
        out of a documented idiom;
      * a `~`/`/` path with a real shape IS checkable and stays in.
    """
    if "/" not in token or any(c.isspace() for c in token):
        return False
    if "://" in token or "$" in token or "@" in token:
        return False
    if any(c in token for c in "{}|*,<>"):
        return False
    if ".." in token.split("/"):
        return False
    if token.startswith("~/"):
        return True
    if token.startswith("/"):
        tail = token.lstrip("/")
        return "/" in tail and (bool(_PATH_EXT.search(token)) or tail.startswith(_PATH_PREFIX))
    return bool(_PATH_EXT.search(token)) or token.startswith(_PATH_PREFIX)


def resolve_path_token(
    tok: str, repo: Path | None, all_repos: dict[str, Path]
) -> Target:
    """One path token -> a `Target`, searched across EVERY derivable scope repo.

    🔴 THE OWNING REPO IS NOT THE ONLY HOME, and scoping the check to it was
    measured wrong: an entry legitimately points at a sibling repo's file (a
    devrc entry citing `clusters/homelab/apps/...`), and 4 of the first 8
    "broken" pointers a repo-scoped check reported were exactly that. `where`
    records which repo answered, so a pointer that resolves only OUTSIDE its own
    scope is still visible as the weaker claim it is.
    """
    if tok.startswith("~/") or tok.startswith("/"):
        p = Path(os.path.expanduser(tok))
        return Target(TARGET_PATH, tok, p.exists(), "absolute")
    order = ([("", repo)] if repo is not None else []) + [
        (name, r) for name, r in sorted(all_repos.items()) if r != repo
    ]
    if not order:
        return Target(TARGET_PATH, tok, None, "no derivable repo for this scope")
    for _name, r in order:
        if (r / tok).exists():
            return Target(TARGET_PATH, tok, True, r.name)
    # Same tri-state rule as the sha search: a miss is evidence only if the
    # OWNING repo was among the ones searched.
    if repo is None:
        return Target(TARGET_PATH, tok, None,
                      "this scope has no derivable repo; the sibling repos searched "
                      "cannot answer for it")
    return Target(TARGET_PATH, tok, False, "not in any derivable scope repo")


def _backtick_tokens(text: str) -> list[str]:
    out: list[str] = []
    for span in _BACKTICKED.findall(text):
        for raw in span.split():
            tok = raw.strip(_STRIP).rstrip(".").rstrip("/")
            if tok:
                out.append(tok)
    return out


@dataclass(frozen=True)
class Target:
    """One pointer target a bullet or a `## Pointers` line NAMES.

    `kind` is what it is; `resolved` is whether it was shown to EXIST; `where` is
    the evidence (the repo the sha was found in, the path that stat'd). A
    `resolved` of None means the check could not be performed at all — a scope
    with no derivable repo, or a PR ref with `--check-prs` off. It is a THIRD
    state on purpose: collapsing it into False would report an unmeasured target
    as a broken one.
    """

    kind: str
    token: str
    resolved: bool | None = None
    where: str = ""

    @property
    def label(self) -> str:
        if self.resolved is None:
            return f"{self.kind}:{self.token} (NOT CHECKED{': ' + self.where if self.where else ''})"
        if self.resolved:
            return f"{self.kind}:{self.token}" + (f" in {self.where}" if self.where else "")
        return f"{self.kind}:{self.token} (does not resolve{': ' + self.where if self.where else ''})"


# --- scope -> owning repo -------------------------------------------------------


def workspace_roots() -> list[Path]:
    """Where a scope's owning repo might live, DERIVED not hardcoded.

    `$WORKSPACE` (or `~/workspace`) plus each of its immediate subdirectories,
    because the real layout nests one level for one client's repos. Returning a
    list rather than a single root is what lets `scope_repo` say NOT DERIVABLE
    honestly instead of guessing.
    """
    base = Path(os.environ.get("WORKSPACE") or (Path.home() / "workspace"))
    roots = [base]
    try:
        roots += sorted(p for p in base.iterdir() if p.is_dir())
    except OSError:
        pass
    return roots


def scope_repo(scope: str, roots: list[Path]) -> Path | None:
    """`<root>/<scope>` that is a git repo, or None.

    EXACT basename match, never a prefix: `~/workspace/` holds a dozen
    `devrc-<topic>` agent worktrees, and a prefix match would attribute the
    `devrc` scope to whichever one sorted first.
    """
    for root in roots:
        cand = root / scope
        if (cand / ".git").exists():
            return cand
    return None


def _git(repo: Path, *args: str, store_root: Path) -> subprocess.CompletedProcess:
    """Run one READ-ONLY git command in `repo`, never in the store.

    🔴 THE GUARD IS THE POINT, not a nicety. The store's scope dirs are each
    their own git repo (`index-store.md`), so a scope-to-repo derivation that
    ever pointed back at the store would run git inside curated, unbacked-up,
    client-confidential content — which "Store safety" forbids outright. The
    check is on the RESOLVED path so a symlink cannot walk around it.
    """
    rp = repo.resolve()
    sr = store_root.resolve()
    if rp == sr or sr in rp.parents or rp.is_relative_to(sr):
        raise RuntimeError(
            f"refusing to run git inside the index store: {rp} is under {sr}. "
            "The store is curated and unbacked-up; this audit is read-only."
        )
    return subprocess.run(
        ["git", "-C", str(rp), *args], capture_output=True, text=True, timeout=20
    )


# --- per-entry audit ------------------------------------------------------------


@dataclass
class BulletFinding:
    scope: str
    filename: str
    population: str
    lines: int
    verdict: str | None = None          # EVICTABLE / NO_HOME / UNVERIFIED
    targets: tuple[Target, ...] = ()
    marker_sha: str | None = None

    @property
    def where(self) -> str:
        return f"{self.scope}/{self.filename}"


@dataclass
class EntryAudit:
    scope: str
    filename: str
    size: int
    bullets: list[BulletFinding] = field(default_factory=list)
    pointer_targets: list[Target] = field(default_factory=list)
    fm_missing: tuple[str, ...] = ()
    legacy_repo_key: bool = False
    has_nuance: bool = True

    @property
    def where(self) -> str:
        return f"{self.scope}/{self.filename}"

    @property
    def status(self) -> str:
        if self.size > HARD:
            return "OVER HARD CAP"
        if self.size > TARGET:
            return "OVER TARGET"
        return "OK"

    @property
    def open_bullets(self) -> list[BulletFinding]:
        return [b for b in self.bullets if b.population == "open"]


def classify_targets(
    bullet_text: str,
    marker_sha: str | None,
    repo: Path | None,
    all_repos: dict[str, Path],
    store_root: Path,
    check_prs: bool,
) -> tuple[Target, ...]:
    """Every target this bullet NAMES, each with whether it was shown to exist."""
    out: list[Target] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, token: str, resolved: bool | None, where: str = "") -> None:
        key = (kind, token)
        if key in seen:
            return
        seen.add(key)
        out.append(Target(kind, token, resolved, where))

    shas: list[str] = []
    if marker_sha:
        shas.append(marker_sha)
    tokens = _backtick_tokens(bullet_text)
    shas += [t.lower() for t in tokens if _SHA.match(t.lower())]

    for sha in shas:
        if repo is None and not all_repos:
            add(TARGET_SHA, sha, None, "no derivable repo for this scope")
            continue
        # 🔴 A MISS IS ONLY EVIDENCE IF THE OWNING REPO WAS ACTUALLY SEARCHED.
        # With no repo for this scope, "not found in the OTHER scopes' repos"
        # says nothing about the repo that would hold it — so a miss here is
        # UNMEASURED, not absent. Collapsing the two would report `NO HOME` for a
        # bullet whose record exists, and send someone to write it a second time.
        miss: bool | None = False if repo is not None else None
        # 🔴 SEARCH EVERY DERIVABLE SCOPE REPO, not only the owning one. A
        # cross-repo `RESOLVED <sha>` is a real shape in this corpus (the store
        # spans a client's several repos), and reporting one as NO HOME because
        # the sha lives next door would send someone to re-write a record that
        # already exists.
        order = ([("", repo)] if repo is not None else []) + [
            (name, p) for name, p in sorted(all_repos.items()) if p != repo
        ]
        # `where` is always the repo's DIRECTORY BASENAME, which by construction
        # (`scope_repo` matches `<root>/<scope>`) equals the scope key. That is
        # what lets the renderer tell an owning-repo hit from a foreign one with
        # a string compare instead of a second lookup.
        found = ""
        for _name, r in order:
            try:
                rc = _git(r, "cat-file", "-e", f"{sha}^{{commit}}", store_root=store_root)
            except (OSError, subprocess.SubprocessError, RuntimeError):
                continue
            if rc.returncode == 0:
                found = r.name
                break
        if found:
            add(TARGET_SHA, sha, True, found)
        else:
            add(TARGET_SHA, sha, miss,
                "not in any derivable scope repo" if miss is False
                else "this scope has no derivable repo; the sibling repos searched cannot answer for it")

    for tok in tokens:
        if not _is_pathish(tok):
            continue
        t = resolve_path_token(tok, repo, all_repos)
        add(t.kind, t.token, t.resolved, t.where)

    for pr in _PR_REF.findall(bullet_text):
        if not check_prs:
            add(TARGET_PR, pr, None, "PR/issue ref; needs network (--check-prs)")
            continue
        add(TARGET_PR, pr, _check_pr(pr, repo), repo.name if repo else "")

    return tuple(out)


def _check_pr(ref: str, repo: Path | None) -> bool | None:
    """`gh pr view` on one ref. None when `gh` is absent or the call failed.

    Deliberately returns None rather than False on a tooling failure: a missing
    `gh`, a rate limit and a genuinely nonexistent PR are three different facts,
    and only the last is a finding.
    """
    if repo is None:
        return None
    num = ref.split("#")[-1]
    slug = ref.split("#")[0]
    args = ["gh", "pr", "view", num, "--json", "number"]
    if slug:
        args += ["--repo", slug]
    try:
        rc = subprocess.run(args, cwd=str(repo), capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if rc.returncode == 0:
        return True
    if "Could not resolve" in rc.stderr or "no pull requests found" in rc.stderr.lower():
        return False
    return None


def verdict_for(targets: tuple[Target, ...]) -> str:
    """The lifecycle verdict for ONE `RESOLVED` bullet. THE single source of it.

    * any target verified to EXIST -> EVICTABLE (its content has a home);
    * else any target that could not be checked -> NOT CHECKED (never NO HOME:
      an unmeasured target is not an absent one);
    * else -> NO HOME. That covers both "named nothing at all" and "named
      targets, none of which resolve" — in both cases the durable record this
      bullet claims to defer to cannot be reached, so evicting it would delete
      the only copy.
    """
    if any(t.resolved is True for t in targets):
        return EVICTABLE
    if any(t.resolved is None for t in targets):
        return UNVERIFIED
    return NO_HOME


def audit_entry(
    path: Path,
    scope: str,
    repo: Path | None,
    all_repos: dict[str, Path],
    store_root: Path,
    check_prs: bool,
) -> EntryAudit:
    text = path.read_text(encoding="utf-8", errors="replace")
    a = EntryAudit(scope=scope, filename=path.name, size=len(text.encode()))

    fm = parse_front_matter(text)
    a.fm_missing = tuple(k for k in ("sensitivity", "created_by", "aliases") if k not in fm)
    a.legacy_repo_key = "repo" in fm and "scope" not in fm

    secs = extract_sections(text, [POINTERS_HEADING, NUANCE_HEADING])
    for tok in _backtick_tokens(secs.get(POINTERS_HEADING, "")):
        if not _is_pathish(tok):
            continue
        a.pointer_targets.append(resolve_path_token(tok, repo, all_repos))

    nuance = secs.get(NUANCE_HEADING, "")
    a.has_nuance = NUANCE_HEADING in secs
    for b in parse_journal_bullets(nuance):
        pop = b.openness_population
        f = BulletFinding(
            scope=scope,
            filename=path.name,
            population=pop,
            lines=len(b.lines),
            marker_sha=b.resolved_by,
        )
        if pop in ("resolved", "unverifiable"):
            f.targets = classify_targets(
                b.text, b.resolved_by, repo, all_repos, store_root, check_prs
            )
            f.verdict = verdict_for(f.targets)
        a.bullets.append(f)
    return a


# --- collisions -----------------------------------------------------------------


@dataclass(frozen=True)
class Collision:
    scope: str
    ref: str
    tier: str
    candidates: tuple[str, ...]


def find_collisions(index, scopes: list[str] | None = None) -> tuple[list[Collision], int]:
    """Every ref in the store that resolves ambiguously, AND how many were tried.

    🔴 The second element is the DENOMINATOR and it is returned, not recomputed:
    "0 collisions" out of a walk that fed the resolver nothing is the silent zero
    `claude/RULES.md` names, and the only way to tell the two apart is to print
    the count of refs actually resolved beside the finding count.

    🔴 THE EXECUTABLE RESOLVER IS THE ONLY JUDGE. Every candidate ref an entry
    makes addressable — its bare slug, its kind-qualified ref, and each of its
    normalized aliases — is fed back through `resolve_ref_tiered`, and a
    collision is exactly an `AmbiguousRefError` coming out. Re-implementing the
    two-tier rule here would be the duplicated predicate `index-store.md` already
    warns about, and the drift would be SILENT: the audit would bless refs the
    reader refuses.

    That also gets the tier precedence right for free. An alias that merely
    matches ANOTHER entry's filename is not ambiguous — the filename tier wins
    outright and the alias tier is never consulted — so a hand-rolled "count the
    claims" detector would report a collision the reader does not have.
    """
    out: list[Collision] = []
    seen: set[tuple[str, str]] = set()
    # 🔴 The scope filter belongs HERE, not at the caller. Filtering the findings
    # afterwards while counting refs store-wide prints a denominator for a walk
    # the reader did not ask for — "0 collisions out of 336" under `--scope devrc`
    # would be a number about eight other scopes.
    for scope in (index.scopes if scopes is None else [s for s in index.scopes if s in scopes]):
        refs: set[str] = set()
        for e in index.entries(scope):
            refs.add(e.slug)
            refs.add(e.ref)
            refs.update(e.aliases)
        for ref in sorted(refs):
            key = (scope, ref)
            if key in seen:
                continue
            seen.add(key)
            try:
                resolve_ref_tiered(ref, index, scope)
            except AmbiguousRefError as exc:
                out.append(
                    Collision(scope=scope, ref=ref, tier=exc.tier, candidates=exc.candidates)
                )
    return out, len(seen)


# --- the whole store ------------------------------------------------------------


@dataclass
class StoreAudit:
    store_root: Path
    entries: list[EntryAudit]
    collisions: list[Collision]
    scopes: list[str]
    scopes_without_readme: list[str]
    repos: dict[str, Path]
    unresolved_scopes: list[str]
    refs_resolved: int = 0
    malformed: tuple = ()


def audit_store(
    store_root: Path, scope_filter: str | None = None, check_prs: bool = False
) -> StoreAudit:
    index = load_index(store_root, on_malformed=ON_MALFORMED_COLLECT)
    roots = workspace_roots()
    scopes = [s for s in index.scopes if scope_filter is None or s == normalize_ref(scope_filter)]
    repos: dict[str, Path] = {}
    unresolved: list[str] = []
    for s in scopes:
        r = scope_repo(s, roots)
        if r is None:
            unresolved.append(s)
        else:
            repos[s] = r

    entries: list[EntryAudit] = []
    no_readme: list[str] = []
    for s in scopes:
        d = store_root / s
        if not (d / "README.md").is_file():
            no_readme.append(s)
        for md in sorted(d.glob("*.md")):
            if md.name == "README.md":
                continue
            entries.append(
                audit_entry(md, s, repos.get(s), repos, store_root, check_prs)
            )
    collisions, n_refs = find_collisions(index, scopes)
    return StoreAudit(
        store_root=store_root,
        entries=entries,
        collisions=collisions,
        scopes=scopes,
        scopes_without_readme=no_readme,
        repos=repos,
        unresolved_scopes=unresolved,
        refs_resolved=n_refs,
        malformed=index.malformed,
    )


# --- acknowledged-list accounting -----------------------------------------------


@dataclass(frozen=True)
class AckAccounting:
    """The two-way pin on `ACKNOWLEDGED_OVER_CAP`, computed against a real store.

    🔴 THE PIN LIVES HERE, NOT ONLY IN A TEST, on purpose. The hermetic suite runs
    in a nix sandbox that cannot see `~/.claude/analyze-service-index/` at all, so
    a test-only pin would be structurally blind to the exact drift it claims to
    catch (`claude/RULES.md` -> "a suite whose CONFIG pins a dimension is
    STRUCTURALLY BLIND to that dimension's bugs"). Computing it in the auditor
    means the list is re-validated against the live store on EVERY run, and the
    hermetic tests then only have to prove that this function is wired correctly —
    which they do with synthetic stores, in both directions.

    * `honoured`      over the cap AND listed -> not a finding, but always PRINTED.
    * `unacknowledged` over the cap and NOT listed -> a finding. New bloat is
      caught exactly as it was before the list existed.
    * `stale`         listed, still present, and no longer over the cap -> a
      finding. THE DIRECTION THAT DECAYS SILENTLY: without it a line could sit
      here forever excusing an entry that had already been fixed, and the list
      would quietly become a blanket rather than an enumeration.
    * `absent`        listed, its scope was audited, and no such entry exists ->
      a finding. The entry was renamed or deleted and the line has rotted.
    """

    honoured: tuple[EntryAudit, ...] = ()
    unacknowledged: tuple[EntryAudit, ...] = ()
    stale: tuple[tuple[str, int], ...] = ()
    absent: tuple[str, ...] = ()

    @property
    def findings(self) -> int:
        return len(self.unacknowledged) + len(self.stale) + len(self.absent)


def account_acknowledged(
    entries: list[EntryAudit],
    scopes: list[str],
    acknowledged: dict[str, str] | None = None,
) -> AckAccounting:
    """Reconcile the acknowledged list against what was actually measured.

    🔴 SCOPE-LIMITED, and that is not a detail. Under `--scope civitai` the other
    scopes' entries were never examined, so a key naming one of them is
    UNMEASURED, not missing — reporting it `absent` would manufacture a rotten
    line out of a narrowed run, and the operator would delete a line that is
    perfectly correct. Only keys whose scope was audited are judged.
    """
    ack = ACKNOWLEDGED_OVER_CAP if acknowledged is None else acknowledged
    in_scope = set(scopes)
    by_where = {e.where: e for e in entries}

    honoured, unack = [], []
    for e in entries:
        if e.size <= HARD:
            continue
        (honoured if e.where in ack else unack).append(e)

    stale, absent = [], []
    for key in sorted(ack):
        scope = key.split("/", 1)[0]
        if scope not in in_scope:
            continue
        e = by_where.get(key)
        if e is None:
            absent.append(key)
        elif e.size <= HARD:
            stale.append((key, e.size))

    return AckAccounting(
        honoured=tuple(sorted(honoured, key=lambda e: -e.size)),
        unacknowledged=tuple(sorted(unack, key=lambda e: -e.size)),
        stale=tuple(stale),
        absent=tuple(absent),
    )


# --- render ---------------------------------------------------------------------


def _mark(status: str) -> str:
    return {"OK": "✓", "OVER TARGET": "⚠", "OVER HARD CAP": "✗"}[status]


def render(
    a: StoreAudit,
    show_all: bool,
    n_detail: int,
    check_prs: bool,
    out=sys.stdout,
    acknowledged: dict[str, str] | None = None,
) -> None:
    p = lambda *x: print(*x, file=out)  # noqa: E731
    ents = sorted(a.entries, key=lambda e: -e.size)
    n = len(ents)
    total = sum(e.size for e in ents)
    ack_map = ACKNOWLEDGED_OVER_CAP if acknowledged is None else acknowledged
    ack = account_acknowledged(a.entries, a.scopes, ack_map)

    p(f"# /analyze-service index audit — {n} entries in {len(a.scopes)} scope(s)")
    p(f"  store: {a.store_root}")
    p(f"\nbudget  target {TARGET:,} B   hard cap {HARD:,} B   per ENTRY")
    p(f"        {BUDGET_JUSTIFICATION}")
    p(f"        constants owned by {BUDGET_OWNER} — read the numbers there")

    if a.malformed:
        p(f"\n🔴 {len(a.malformed)} entry file(s) REJECTED by the loader and NOT audited "
          "below — every count on this page is short by that many:")
        for m in a.malformed[:8]:
            p(f"    {m.scope}/{m.filename}: {m.reason}")

    # --- sizes
    over = [e for e in ents if e.status != "OK"]
    p("\n## sizes (worst first)")
    listed = ents if show_all else over
    honoured_where = {e.where for e in ack.honoured}
    for e in listed:
        ratio = f"  {e.size / TARGET:.1f}x target" if e.size > TARGET else ""
        tag = "  [ACKNOWLEDGED]" if e.where in honoured_where else ""
        p(f"  {e.size:>9,} B  {_mark(e.status)} {e.status:<13} {e.where}{ratio}{tag}")
    if not listed:
        p("  (none over budget)")
    hidden = n - len(listed)
    if hidden:
        p(f"  ({hidden} of {n} entries within budget — not listed; --all to see them)")
    p(f"  store total: {total:,} B across {n} entries examined "
      f"(mean {total // max(n, 1):,} B); {len(over)} of {n} over the {TARGET:,} B target, "
      f"excess {sum(e.size - TARGET for e in over):,} B")

    # --- acknowledged over-cap accounting
    #
    # 🔴 PRINTED WHETHER OR NOT THE VERDICT IS CLEAN. An exemption nobody can see
    # is how an enumeration rots into a blanket; `run-tests.sh` prints its
    # EXPECTED_SKIPS accounting for exactly this reason. The block is emitted
    # whenever the list has ANY key in an audited scope — including when every
    # one of them has gone stale, which is precisely when it must be loudest.
    in_scope_keys = [k for k in sorted(ack_map) if k.split("/", 1)[0] in set(a.scopes)]
    if in_scope_keys or ack.honoured:
        p(f"\n## acknowledged over cap — {len(ack.honoured)} ACKNOWLEDGED over cap "
          f"(of {len(ack.honoured) + len(ack.unacknowledged)} entries over the "
          f"{HARD:,} B cap, {len(in_scope_keys)} listed in the audited scope(s))")
        p("  An ENUMERATION, not a raised cap: each of these is over the hard cap and")
        p("  provably cannot be brought under it by the lifecycle this auditor owns —")
        p("  evicting every EVICTABLE bullet still leaves it over. They are excluded")
        p("  from the verdict and NEVER from this page. An over-cap entry not named")
        p("  here is still a finding.")
        for e in ack.honoured:
            p(f"      {e.size:>9,} B  {e.where}")
            p(f"                   reason: {ack_map[e.where]}")
        if not ack.honoured:
            p("      (none of the listed entries is currently over the cap)")
        if ack.stale:
            p(f"\n  🔴 {len(ack.stale)} STALE ACKNOWLEDGEMENT(S) — listed, but no longer over "
              f"the {HARD:,} B cap.")
            p("     The exemption has been earned away. DELETE the line from")
            p("     ACKNOWLEDGED_OVER_CAP; leaving it there turns an enumeration into a")
            p("     standing excuse for an entry that is already fixed.")
            for key, size in ack.stale:
                p(f"       {size:>9,} B  {key}  (under the cap by {HARD - size:,} B)")
        if ack.absent:
            p(f"\n  🔴 {len(ack.absent)} ACKNOWLEDGED ENTR(IES) NO LONGER EXIST — renamed or "
              "deleted. Remove the line:")
            for key in ack.absent:
                p(f"       {key}")

    # --- bullet shape
    bl = [b for e in ents for b in e.bullets]
    fat = sorted((b for b in bl if b.lines > BULLET_LINE_LIMIT), key=lambda b: -b.lines)
    p(f"\n## bullet shape — schema says ≤{BULLET_LINE_LIMIT} lines per bullet")
    p(f"  {len(bl)} bullet(s) examined across {n} entries; "
      f"{len(fat)} exceed {BULLET_LINE_LIMIT} lines "
      f"({100.0 * len(fat) / len(bl) if bl else 0.0:.1f}%)")
    p("  ⚠ ADVISORY — deliberately NOT part of the verdict. The corpus contradicts")
    p("    the schema by a wide margin and `JournalBullet` documents the same finding,")
    p("    so gating on it would be a permanently-red gate. Trim the worst by hand:")
    for b in fat[:5]:
        p(f"      {b.lines:>3} lines  {b.where}")
    if not fat:
        p("      (none over the limit)")

    # --- lifecycle
    pops: dict[str, int] = {}
    for b in bl:
        pops[b.population] = pops.get(b.population, 0) + 1
    res = [b for b in bl if b.verdict is not None]
    ev = [b for b in res if b.verdict == EVICTABLE]
    nh = [b for b in res if b.verdict == NO_HOME]
    nc = [b for b in res if b.verdict == UNVERIFIED]
    opens = [b for b in bl if b.population == "open"]
    open_entries = {b.where for b in opens}

    p("\n## lifecycle — OPEN keeps, RESOLVED evicts only once its content has a HOME")
    p(f"  {len(bl)} bullet(s) examined across {n} entries "
      f"({', '.join(f'{k}={v}' for k, v in sorted(pops.items())) or 'none'})")
    p(f"\n  OPEN: {len(opens)} bullet(s) in {len(open_entries)} of {n} entries — 🔒 KEEP")
    p("    An OPEN bullet is NEVER an eviction candidate, at any age or size. It is")
    p("    the one thing in this store that cannot be re-derived from a repo.")
    for w in sorted(open_entries)[:n_detail]:
        c = sum(1 for b in opens if b.where == w)
        p(f"      {c:>3} OPEN  {w}")
    if len(open_entries) > n_detail:
        p(f"      ... {len(open_entries) - n_detail} more entries with OPEN bullets")

    p(f"\n  RESOLVED: {len(res)} bullet(s) classified — "
      f"{len(ev)} EVICTABLE / {len(nh)} NO HOME / {len(nc)} NOT CHECKED")
    p(f"    EVICTABLE ({len(ev)} of {len(res)}) — a named pointer target was verified to exist,")
    p("    so the durable form is already somewhere else. Propose, diff, confirm; never auto-cut.")
    # `verdict_for` only returns EVICTABLE when some target resolved, so `_hit`
    # can never be None in a correct build. It is written with a default anyway:
    # a mutation battery on `verdict_for` turned the bare `next()` into a
    # StopIteration that took the whole render down, which made three unrelated
    # tests go red and the attribution unreadable. A renderer must not be the
    # thing that decides whether a classifier bug is diagnosable.
    _hit = lambda b: next((t for t in b.targets if t.resolved is True), None)  # noqa: E731
    foreign = []
    for b in ev[:n_detail]:
        hit = _hit(b)
        p(f"      {b.where}  →  {hit.label if hit else '🔴 EVICTABLE with no resolved target — BUG'}")
    for b in ev:
        hit = _hit(b)
        if hit and hit.where and hit.where not in ("absolute", b.scope):
            foreign.append((b, hit))
    if len(ev) > n_detail:
        p(f"      ... {len(ev) - n_detail} more")
    if foreign:
        p(f"      ⚠ {len(foreign)} of {len(ev)} resolved ONLY in a repo other than their own")
        p("        scope's — a weaker claim. Confirm the attribution before evicting:")
        for b, t in foreign[:5]:
            p(f"          {b.where}  →  {t.kind}:{t.token} found in {t.where}, not {b.scope}")

    p(f"\n    🔴 NO HOME ({len(nh)} of {len(res)}) — write the record first.")
    p("    Every target this bullet names is absent, so it is the ONLY copy. Evicting")
    p("    it deletes the finding. Fix by writing the record, then re-run.")
    for b in nh[:n_detail]:
        why = "; ".join(t.label for t in b.targets) or "names no pointer target at all"
        p(f"      {b.where}  —  {why}")
    if len(nh) > n_detail:
        p(f"      ... {len(nh) - n_detail} more")
    if nh:
        p("      ⚠ a sha can also fail to resolve because the LOCAL CLONE is stale —")
        p("        `git fetch` the scope's repo and re-run before acting on one.")

    p(f"\n    NOT CHECKED ({len(nc)} of {len(res)}) — a target was named but could not be")
    p("    tested here. NOT a clean result and NOT a NO HOME; it is an unmeasured scope.")
    if not check_prs:
        p("      (PR/issue refs are not checked by default — pass --check-prs to use `gh`)")
    for b in nc[:n_detail]:
        p(f"      {b.where}  —  {'; '.join(t.label for t in b.targets)}")
    if len(nc) > n_detail:
        p(f"      ... {len(nc) - n_detail} more")

    # --- pointer integrity
    pts = [t for e in ents for t in e.pointer_targets]
    broken = [(e, t) for e in ents for t in e.pointer_targets if t.resolved is False]
    unchecked = [(e, t) for e in ents for t in e.pointer_targets if t.resolved is None]
    p("\n## pointer integrity (`## Pointers` — paths only)")
    p(f"  at least {len(pts)} path pointer(s) checked across {n} entries in "
      f"{len(a.scopes)} scope(s); {len(broken)} do not resolve, {len(unchecked)} NOT CHECKED")
    p("  ⚠ 'at least' is literal: only tokens that LOOK like paths are counted, so a")
    p("    pointer written without a recognisable path shape is not in this denominator.")
    for e, t in broken[:n_detail]:
        p(f"      🔴 {e.where}  →  {t.label}")
    if len(broken) > n_detail:
        p(f"      ... {len(broken) - n_detail} more")
    for e, t in unchecked[:3]:
        p(f"      ? {e.where}  →  {t.label}")
    if a.unresolved_scopes:
        p(f"  🔴 NOT CHECKED — no owning repo derivable for scope(s): "
          f"{', '.join(a.unresolved_scopes)}. Their pointers were not tested at all.")
    else:
        p(f"  every scope resolved to an owning repo ({len(a.repos)} of {len(a.scopes)}) ✓")

    # --- front matter
    miss = {k: [e.where for e in ents if k in e.fm_missing]
            for k in ("sensitivity", "created_by", "aliases")}
    legacy = [e.where for e in ents if e.legacy_repo_key]
    p("\n## front-matter completeness")
    p(f"  {n} entries examined")
    p(f"    missing `sensitivity:` : {len(miss['sensitivity'])} of {n}  "
      "(fail-safe reads them as client-confidential, so this is a LABELLING gap, not a leak)")
    p(f"    missing `created_by:`  : {len(miss['created_by'])} of {n}  "
      "(absent means it predates the stamp — never fold into either writer)")
    p(f"    missing `aliases:`     : {len(miss['aliases'])} of {n}")
    p(f"    legacy `repo:` instead of `scope:` : {len(legacy)} of {n}")
    for k in ("sensitivity", "created_by"):
        for w in miss[k][:3]:
            p(f"      {k}: {w}")
        if len(miss[k]) > 3:
            p(f"      ... {len(miss[k]) - 3} more missing {k}")

    # --- collisions
    p("\n## ref collisions (a ref that resolves to more than one entry surfaces NOTHING)")
    p(f"  {a.refs_resolved} addressable ref(s) from {n} entries in {len(a.scopes)} scope(s) "
      "fed back through the real resolver (slugs + kind-qualified refs + every alias)")
    if a.collisions:
        for c in a.collisions:
            p(f"  🔴 {c.scope}: `{c.ref}` is ambiguous in the {c.tier} tier — "
              f"{', '.join(c.candidates)}")
        p("     `--ref <that>` returns ref-ambiguous and surfaces NO body. Drop the")
        p("     alias from whichever entry it does not actually name.")
    else:
        p("  every addressable ref resolves to exactly one entry ✓")

    # --- scope policy
    p("\n## scope policy")
    p(f"  {len(a.scopes)} scope(s) examined; {len(a.scopes_without_readme)} without a README.md")
    for s in a.scopes_without_readme:
        p(f"      🔴 {s}/  — no README.md, so no stated policy governs writes there")

    # --- verdict
    p("\n## verdict")
    need = []
    # 🔴 THE VERDICT COUNTS ONLY UNACKNOWLEDGED OVER-CAP ENTRIES. The acknowledged
    # ones are still over the cap and are still printed, above and below — what
    # changes is only whether they can EVER be cleared. Six entries that no action
    # could fix made this line read `⚠ prune needed` on every run forever, which
    # `claude/RULES.md` calls worse than no gate. The TARGET tier is unaffected:
    # acknowledgement is about the hard cap only, so an acknowledged entry is still
    # counted over target below, exactly as before.
    n_hard_all = sum(1 for e in ents if e.status == "OVER HARD CAP")
    n_hard = len(ack.unacknowledged)
    if n_hard:
        need.append(f"{n_hard} entr(ies) over the {HARD:,} B hard cap")
    if ack.stale:
        need.append(f"{len(ack.stale)} stale acknowledgement(s) (listed but no longer over cap)")
    if ack.absent:
        need.append(f"{len(ack.absent)} acknowledged entr(ies) that no longer exist")
    if len(over) - n_hard_all:
        need.append(f"{len(over) - n_hard_all} over the {TARGET:,} B target")
    if ev:
        need.append(f"{len(ev)} RESOLVED bullet(s) evictable")
    if nh:
        need.append(f"{len(nh)} NO HOME (write the record first)")
    if broken:
        need.append(f"{len(broken)} broken pointer(s)")
    if a.collisions:
        need.append(f"{len(a.collisions)} ref collision(s)")
    if a.scopes_without_readme:
        need.append(f"{len(a.scopes_without_readme)} scope(s) with no README")
    if any(miss.values()) or legacy:
        need.append(
            f"front-matter gaps ({len(miss['sensitivity'])} sensitivity, "
            f"{len(miss['created_by'])} created_by, {len(legacy)} legacy repo:)"
        )
    if a.malformed:
        need.append(f"{len(a.malformed)} malformed entr(ies)")
    if need:
        p("  ⚠ prune needed — " + "; ".join(need))
        p(f"  🔒 {len(opens)} OPEN bullet(s) in {len(open_entries)} entries are NOT in that "
          "list and must not be touched.")
    else:
        # 🔴 The clean line must not claim "all N entries within budget" while
        # acknowledged entries sit over the cap — that sentence would be FALSE, and
        # a verdict that lies to go green is worse than the red one it replaced.
        # `claude/RULES.md`: "a comment is a claim too", and so is a verdict.
        budget_clause = (
            f"all {n} entries within budget"
            if not ack.honoured
            else f"{n - len(ack.honoured)} of {n} entries within budget and the other "
                 f"{len(ack.honoured)} ACKNOWLEDGED over cap (named above)"
        )
        p(f"  ✓ {budget_clause}, every ref resolves, every pointer checked "
          "resolves,\n    no RESOLVED bullet is evictable and none lacks a home — "
          "no prune needed (stop; do not churn the files)")
    # 🔴 NAMED IN BOTH BRANCHES. Whether the verdict is clean or red, an
    # acknowledged exemption is restated at the point a reader stops reading.
    if ack.honoured:
        p(f"  ⓘ {len(ack.honoured)} ACKNOWLEDGED over cap — excluded from the verdict, "
          "NOT from the store:")
        for e in ack.honoured:
            p(f"      {e.size:>9,} B  {e.where}")
        p("     Each is over the hard cap with no lifecycle eviction that can reach it.")
        p("     They are an ENUMERATION in `ACKNOWLEDGED_OVER_CAP`, not a raised cap: an")
        p("     over-cap entry not on that list is still a finding above.")
    if nc or unchecked or a.unresolved_scopes:
        p(f"  ⚠ NOT a complete reading: {len(nc)} lifecycle target(s), {len(unchecked)} "
          f"pointer(s) and {len(a.unresolved_scopes)} scope(s) went unmeasured.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="audit the /analyze-service index store (READ-ONLY)")
    ap.add_argument("--store", default=str(DEFAULT_STORE_ROOT), help="store root")
    ap.add_argument("--scope", default=None, help="audit one scope only")
    ap.add_argument("--all", action="store_true", help="list every entry, not just over-budget")
    ap.add_argument("--detail", type=int, default=8, help="rows per findings block")
    ap.add_argument("--check-prs", action="store_true",
                    help="also verify PR/issue refs with `gh` (network)")
    args = ap.parse_args(argv)

    root = Path(os.path.expanduser(args.store))
    if not root.is_dir():
        print(f"subsystem-audit: store root not found: {root}\n"
              "  This is `store-missing`, NOT an empty store — nothing was examined.",
              file=sys.stderr)
        return 2
    a = audit_store(root, args.scope, args.check_prs)
    if not a.scopes:
        print(f"subsystem-audit: no scope matched {args.scope!r} in {root}; "
              "nothing was examined.", file=sys.stderr)
        return 2
    render(a, args.all, args.detail, args.check_prs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
