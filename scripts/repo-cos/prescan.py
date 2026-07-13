#!/usr/bin/env python3
"""Deterministic pre-scan — evidence-grounded improvement candidates, no LLM.

Cheap grep/git-based signal collection. Every candidate carries a repo + `file:line`
so nothing reaching the LLM (or the digest) is unsubstantiated. This is the Stage-1
of the repo-cos pipeline, mirroring `scripts/mail-actions/filter.py`: a pure,
unit-testable filter that shrinks the corpus to a small, capped set of survivors the
LLM then clusters/ranks.

Signal types (each capped PER REPO so a huge repo like civit/civitai can't flood the
LLM input):
  marker        TODO|FIXME|HACK|XXX|BUG comments               (file:line + text)
  skipped_test  @pytest.mark.skip / .skip( / xfail / t.Skip( / #[ignore] …  (CI-verifiable)
  churn         files changed most in the last ~90 days        (refactor/stability)
  large_file    files over a LOC threshold                     (split candidate)
  stale_lock    lockfiles older than a threshold               (dep-freshness, best-effort)

Everything is deterministic and ordered so the same repo state yields the same
candidates (important for a weekly digest that shouldn't churn).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# ---- tunables (kept as module constants so tests can reference them) ----------
MARKER_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX|BUG)\b")
# Quote chars that, when they immediately flank a marker token on BOTH sides
# (`'XXX'`, `"XXX"`, `` `XXX` ``), mark it as a data/enum string literal rather than a
# real comment marker — e.g. a SQL `WHEN 16 THEN RETURN 'XXX';`. See
# `_has_unquoted_marker` below.
_MARKER_QUOTE_CHARS = frozenset("'\"`")
# Skipped/xfail test patterns across the languages in Zach's repos. Kept as literal
# substrings/regex fragments — intentionally broad-but-cheap (a false positive is a
# candidate the LLM can drop, not a correctness bug).
SKIP_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("pytest.skip", re.compile(r"@pytest\.mark\.(skip|skipif|xfail)")),
    ("pytest.skip-call", re.compile(r"pytest\.skip\(")),
    # NB: `(it|describe|test).skip(` is overloaded. The *block-modifier* form
    # (`it.skip('name', fn)`, `describe.skip('suite', ...)`) is a genuinely DISABLED
    # test → a CI-verifiable "enable me" candidate → flag it. But the *conditional*
    # form (`test.skip(<condition>, 'reason')`, e.g. `test.skip(!dockerAvailable(), …)`)
    # is a runtime guard: the test RUNS when the condition is false (Docker present in
    # CI) and skips gracefully otherwise — NOT disabled, must never be flagged.
    # This broad regex matches BOTH forms; `_is_conditional_js_skip` post-filters out
    # the conditional one in `scan_skipped_tests` (see it for the first-arg heuristic).
    ("js.skip", re.compile(r"\b(it|describe|test)\.skip\(")),
    ("js.xfail", re.compile(r"\.(only)\(")),  # .only leaves the rest un-run — same smell
    ("go.skip", re.compile(r"\bt\.Skip(f|Now)?\(")),
    ("rust.ignore", re.compile(r"#\[ignore")),
    ("unittest.skip", re.compile(r"@unittest\.skip")),
)

LARGE_FILE_LOC = 800  # files bigger than this are split candidates
STALE_LOCK_DAYS = 365  # lockfiles older than this are flagged (best-effort, cheap)
CHURN_SINCE = "90 days ago"

# Per-repo caps — the whole point of keeping LLM input small + cheap on big repos.
CAP_PER_SIGNAL = {
    "marker": 8,
    "skipped_test": 8,
    "churn": 6,
    "large_file": 5,
    "stale_lock": 3,
}

# Directories never worth scanning (vendored / generated / VCS internals).
PRUNE_DIRS = {
    ".git", "node_modules", "vendor", "dist", "build", ".next", "target",
    "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".terraform",
    "site-packages", ".cache", "coverage", ".nyc_output", "out", ".serena",
    ".claude",  # nested Claude worktrees / config — never source-of-record
}
# Only text/code files we can cheaply line-scan. Binary/asset extensions are skipped.
SCAN_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".rb", ".java", ".kt",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".sh", ".bash", ".zsh", ".nix", ".lua",
    ".vim", ".yaml", ".yml", ".toml", ".sql", ".tf", ".vue", ".svelte",
}
LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Cargo.lock", "go.sum", "Gemfile.lock", "flake.lock", "composer.lock",
}
MAX_LINE_LEN = 400  # ignore absurdly long (likely minified) lines when marker-scanning


@dataclass(frozen=True)
class Candidate:
    """One piece of deterministic evidence. `file` is repo-relative; `line` is 1-based
    (0 for whole-file signals like churn/large_file that aren't tied to a line)."""
    repo: str          # repo display name (basename)
    kind: str          # one of CAP_PER_SIGNAL keys
    file: str          # repo-relative path
    line: int          # 1-based line number, or 0 for file-level signals
    text: str          # the evidence line / a short descriptor

    @property
    def ref(self) -> str:
        """Compact `repo/path:line` reference used in evidence lists."""
        loc = f":{self.line}" if self.line else ""
        return f"{self.repo}/{self.file}{loc}"

    def as_dict(self) -> dict:
        return {
            "repo": self.repo, "kind": self.kind, "file": self.file,
            "line": self.line, "text": self.text, "ref": self.ref,
        }


@dataclass
class RepoScan:
    repo: str
    path: str
    candidates: list[Candidate] = field(default_factory=list)
    error: str | None = None


# ---- filesystem walk ----------------------------------------------------------

def _iter_files(root: Path):
    """Yield scannable files under `root`, pruning vendored/generated dirs. Sorted
    within each directory for deterministic ordering."""
    for dirpath, dirnames, filenames in os.walk(root):
        # prune in-place so os.walk doesn't descend into them. Skip explicit prune dirs
        # AND all hidden dirs (dotdirs are config/state, rarely source-of-record, and
        # include nested .git/.claude worktrees that would pollute results).
        dirnames[:] = sorted(
            d for d in dirnames if d not in PRUNE_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            yield Path(dirpath) / name


def _rel(root: Path, p: Path) -> str:
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


# ---- signal: markers ----------------------------------------------------------

def scan_markers(root: Path, repo: str, cap: int) -> list[Candidate]:
    """Prefer ripgrep (fast, respects .gitignore) but fall back to a python walk so the
    module works with no external tools. Deterministic ordering; capped."""
    if shutil.which("rg"):
        out = _rg_markers(root)
    else:
        out = _walk_markers(root)
    # Deterministic order: by path then line.
    out.sort(key=lambda c: (c.file, c.line))
    return [_reref(c, repo) for c in out[:cap]]


def _reref(c: Candidate, repo: str) -> Candidate:
    return Candidate(repo=repo, kind=c.kind, file=c.file, line=c.line, text=c.text)


def _has_unquoted_marker(line: str) -> bool:
    """True iff `line` has at least one marker token (TODO/FIXME/HACK/XXX/BUG) that is
    NOT immediately wrapped in matching quotes.

    A marker whose char-before and char-after are the SAME quote char (`'XXX'`, `"XXX"`,
    `` `XXX` ``) is a data/enum string literal (e.g. a SQL `RETURN 'XXX';`), not a
    comment marker. We suppress a line only when EVERY marker occurrence on it is
    quote-wrapped — a single un-wrapped hit (an ordinary `# TODO: fix`, where the char
    before is a space) keeps the line. Uses `finditer` so a line mixing a quoted literal
    and a real marker (`RETURN 'XXX';  -- TODO real`) still survives on the real one."""
    for m in MARKER_RE.finditer(line):
        before = line[m.start() - 1] if m.start() > 0 else ""
        after = line[m.end()] if m.end() < len(line) else ""
        if before in _MARKER_QUOTE_CHARS and before == after:
            continue  # quote-wrapped → string/enum literal, not a marker
        return True   # an un-wrapped marker occurrence — this is a real hit
    return False      # no markers, or all of them quote-wrapped → suppress


def _rg_markers(root: Path) -> list[Candidate]:
    globs = []
    for d in PRUNE_DIRS:
        globs += ["-g", f"!{d}/"]
    try:
        proc = subprocess.run(
            ["rg", "--no-heading", "--line-number", "--color", "never",
             "--max-columns", str(MAX_LINE_LEN), *globs,
             r"\b(TODO|FIXME|HACK|XXX|BUG)\b", str(root)],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError):
        return _walk_markers(root)
    res: list[Candidate] = []
    for ln in proc.stdout.splitlines():
        # format: path:line:content
        parts = ln.split(":", 2)
        if len(parts) < 3:
            continue
        path, lno, content = parts
        if not lno.isdigit():
            continue
        # Align with `_walk_markers`: only scan text/code files in SCAN_EXTS. rg walks
        # EVERY file (it ignores SCAN_EXTS), so without this the two code paths disagree
        # and results go non-deterministic depending on whether `rg` is installed — e.g.
        # `.md` docs (RULES.md's "no TODO comments", handoff notes) leak only under rg.
        if Path(path).suffix not in SCAN_EXTS:
            continue
        # Skip marker tokens that are just quoted string/enum literals (`RETURN 'XXX'`).
        if not _has_unquoted_marker(content):
            continue
        rel = _rel(root, Path(path))
        res.append(Candidate("", "marker", rel, int(lno), content.strip()[:200]))
    return res


def _walk_markers(root: Path) -> list[Candidate]:
    res: list[Candidate] = []
    for p in _iter_files(root):
        if p.suffix not in SCAN_EXTS:
            continue
        try:
            with p.open("r", encoding="utf-8", errors="ignore") as fh:
                for i, line in enumerate(fh, 1):
                    if len(line) > MAX_LINE_LEN:
                        continue
                    # Real marker only if some occurrence isn't a quoted literal
                    # (skips SQL enums like `RETURN 'XXX';`). See `_has_unquoted_marker`.
                    if _has_unquoted_marker(line):
                        res.append(Candidate(
                            "", "marker", _rel(root, p), i, line.strip()[:200]))
        except (OSError, UnicodeError):
            continue
    return res


# ---- signal: skipped/xfail tests ---------------------------------------------

# Matches the JS `.skip(` head so we can inspect its first argument. Kept separate from
# the SKIP_PATTERNS entry (which is used only for the yes/no match) so `.end()` lands
# exactly after the `(`.
_JS_SKIP_HEAD_RE = re.compile(r"\b(?:it|describe|test)\.skip\(")


def _is_conditional_js_skip(line: str) -> bool:
    """True when a JS `(it|describe|test).skip(...)` is the *conditional* runtime-guard
    form — `test.skip(<condition>, 'reason')` — rather than a genuinely-disabled block.

    Playwright/Jest overload `.skip(` two ways, and only one is a real "enable me" fix:
      • block modifier / disabled test — first arg is the test NAME (a string literal)
        or a function: `it.skip('renders', () => {})`, `describe.skip('suite', ...)`,
        or the bare `describe.skip()`. Genuinely disabled → a CI-verifiable candidate
        → the caller FLAGS it.
      • conditional guard — first arg is a boolean expression: `test.skip(!dockerAvailable(),
        'needs Docker')`, `test.skip(process.env.CI, ...)`, `it.skip(isSlow, ...)`. The
        test RUNS when the condition is false (e.g. Docker present in CI) and skips
        gracefully otherwise — NOT disabled, so it must NEVER be flagged.

    Heuristic (line-based, no parse): look at the first non-space char after `.skip(`.
    A string-literal quote (`'`, `"`, `` ` ``) ⇒ the arg is a test name ⇒ disabled block
    ⇒ NOT conditional. An empty arg list (`)` immediately) ⇒ bare block modifier ⇒ NOT
    conditional. Anything else (identifier / call / `!expr` / boolean) ⇒ conditional
    guard ⇒ True. Conservative on purpose: a missed disabled test is only a dropped
    candidate, but a flagged conditional guard is the false positive we must avoid."""
    m = _JS_SKIP_HEAD_RE.search(line)
    if not m:
        return False
    rest = line[m.end():].lstrip()
    if not rest or rest[0] == ")":
        return False  # `.skip()` — bare block modifier, treat as disabled block
    if rest[0] in "'\"`":
        return False  # first arg is a string-literal test name → disabled block
    return True       # first arg is a condition expression → conditional runtime guard


def scan_skipped_tests(root: Path, repo: str, cap: int) -> list[Candidate]:
    """Line-scan for skip/xfail/ignore markers across languages. A skipped test is a
    concrete, CI-verifiable fix — bias the LLM toward these."""
    res: list[Candidate] = []
    for p in _iter_files(root):
        if p.suffix not in SCAN_EXTS:
            continue
        try:
            with p.open("r", encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()
        except (OSError, UnicodeError):
            continue
        for i, line in enumerate(lines, 1):
            if len(line) > MAX_LINE_LEN:
                continue
            for label, pat in SKIP_PATTERNS:
                if pat.search(line):
                    # A conditional `test.skip(<cond>, …)` is a runtime guard that runs
                    # in CI, not a disabled test — never a candidate. See the helper.
                    if label == "js.skip" and _is_conditional_js_skip(line):
                        continue
                    res.append(Candidate(
                        repo, "skipped_test", _rel(root, p), i,
                        f"[{label}] {line.strip()[:180]}"))
                    break
    res.sort(key=lambda c: (c.file, c.line))
    return res[:cap]


# ---- signal: churn hotspots (git) --------------------------------------------

def scan_churn(root: Path, repo: str, cap: int) -> list[Candidate]:
    """Files changed most often in the last ~90 days. High-churn = refactor/stability
    candidate. Returns [] if not a git repo or git is unavailable."""
    if not (root / ".git").exists() and not _is_git_worktree(root):
        return []
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "log", f"--since={CHURN_SINCE}",
             "--name-only", "--pretty=format:", "--no-merges"],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    counts: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        f = line.strip()
        if not f:
            continue
        top = f.split("/", 1)[0]
        if top in PRUNE_DIRS:
            continue
        # only count files that still exist (renames/deletes shouldn't dominate)
        if not (root / f).exists():
            continue
        counts[f] = counts.get(f, 0) + 1
    # Rank by count desc, then path for determinism. Require >=3 changes to be a "hotspot".
    ranked = sorted(
        ((f, n) for f, n in counts.items() if n >= 3),
        key=lambda kv: (-kv[1], kv[0]),
    )
    return [
        Candidate(repo, "churn", f, 0, f"{n} changes in last 90d")
        for f, n in ranked[:cap]
    ]


def _is_git_worktree(root: Path) -> bool:
    # a linked worktree has a .git *file* (gitdir pointer), not a dir
    gp = root / ".git"
    return gp.is_file()


# ---- signal: large files ------------------------------------------------------

def scan_large_files(root: Path, repo: str, cap: int, threshold: int = LARGE_FILE_LOC) -> list[Candidate]:
    """Source files over `threshold` LOC — split/refactor candidates."""
    res: list[Candidate] = []
    for p in _iter_files(root):
        if p.suffix not in SCAN_EXTS:
            continue
        try:
            with p.open("rb") as fh:
                loc = sum(1 for _ in fh)
        except OSError:
            continue
        if loc > threshold:
            res.append(Candidate(repo, "large_file", _rel(root, p), 0, f"{loc} LOC"))
    res.sort(key=lambda c: (-int(c.text.split()[0]), c.file))
    return res[:cap]


# ---- signal: stale lockfiles --------------------------------------------------

def scan_stale_locks(root: Path, repo: str, cap: int, now: float | None = None,
                     max_age_days: int = STALE_LOCK_DAYS) -> list[Candidate]:
    """Flag lockfiles whose mtime is older than `max_age_days`. Cheap dep-freshness
    proxy; intentionally conservative to avoid noise. `now` is injectable for tests."""
    import time
    now = now if now is not None else time.time()
    res: list[Candidate] = []
    for p in _iter_files(root):
        if p.name not in LOCKFILES:
            continue
        try:
            age_days = (now - p.stat().st_mtime) / 86400.0
        except OSError:
            continue
        if age_days > max_age_days:
            res.append(Candidate(
                repo, "stale_lock", _rel(root, p), 0,
                f"lockfile untouched {int(age_days)}d"))
    res.sort(key=lambda c: c.file)
    return res[:cap]


# ---- per-repo orchestration ---------------------------------------------------

def scan_repo(path: str, caps: dict[str, int] | None = None) -> RepoScan:
    """Run every signal against one repo, each capped. Missing repo → RepoScan.error."""
    caps = caps or CAP_PER_SIGNAL
    root = Path(path).expanduser()
    repo = root.name
    rs = RepoScan(repo=repo, path=str(root))
    if not root.exists() or not root.is_dir():
        rs.error = "not a directory"
        return rs
    try:
        rs.candidates += scan_markers(root, repo, caps.get("marker", 8))
        rs.candidates += scan_skipped_tests(root, repo, caps.get("skipped_test", 8))
        rs.candidates += scan_churn(root, repo, caps.get("churn", 6))
        rs.candidates += scan_large_files(root, repo, caps.get("large_file", 5))
        rs.candidates += scan_stale_locks(root, repo, caps.get("stale_lock", 3))
    except Exception as exc:  # noqa: BLE001 — one bad repo shouldn't kill the scan
        rs.error = f"{type(exc).__name__}: {exc}"
    return rs


def scan_all(repos: list[str], limit_candidates: int,
             caps: dict[str, int] | None = None) -> tuple[list[Candidate], list[RepoScan]]:
    """Scan every repo, then apply a GLOBAL cap (`limit_candidates`) across all of them
    using round-robin interleaving so no single repo monopolizes the LLM budget.

    Returns (capped_candidates, per_repo_scans). The per-repo scans retain their full
    (pre-global-cap) candidate lists for reporting/--candidates-only.
    """
    scans = [scan_repo(r, caps) for r in repos]
    capped = _interleave_cap([s.candidates for s in scans], limit_candidates)
    return capped, scans


def _interleave_cap(per_repo: list[list[Candidate]], limit: int) -> list[Candidate]:
    """Round-robin across repos so the global cap is spread fairly rather than eaten by
    the first (possibly huge) repo. Deterministic given deterministic inputs."""
    out: list[Candidate] = []
    idx = 0
    active = [list(lst) for lst in per_repo]
    while len(out) < limit and any(active):
        lst = active[idx % len(active)]
        if lst:
            out.append(lst.pop(0))
        idx += 1
        # stop if we've cycled through all and all empty
        if idx % len(active) == 0 and not any(active):
            break
    return out[:limit]
