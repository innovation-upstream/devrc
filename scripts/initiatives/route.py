#!/usr/bin/env python3
"""Route an incoming free-text signal to the best-matching EXISTING initiative(s).

PHASE 2 of the "initiatives consolidation" feature. A READ-ONLY consumer of the
Phase-1 store (`initiatives.current` in the homelab `mailbox` Postgres). Given a
signal — a new task title, a repo-cos proposal, a mail subject/thread snippet —
it returns the existing initiatives ranked with an interpretable score, and
classifies the top one as a confident match vs "likely new work".

It SUGGESTS, never acts: no dispatch, no writes, no confidence *gate* that
force-picks. The caller (repo-cos / mail-actions / a human) decides what to do
with the ranking.

Design (reuse, don't reimplement):
  The scan (`scripts/session-analysis/initiative-scan.py`) already contains the
  exact token matcher used to attach a live tmux pane title to an initiative
  (`text_tokens`, `slug_tokens`, `best_title_match`). A router signal is just a
  pane-title-like free string, so we reuse those SAME scoring components — but
  where `best_title_match` returns only the single best (or None), the router
  returns ALL candidates ranked, each with its score + which tokens matched, and
  classifies confident-vs-new-work using the SAME qualification bar
  `best_title_match` uses (the document-frequency uniqueness gate).

  We import the scan by explicit importlib path (its filename is hyphenated and
  so not importable) rather than copying its ~6 token/match functions, so the
  matcher stays single-sourced: the router can never drift from the scan's
  behaviour. Importing the scan runs its top-level `import chquery` (needs
  `requests` + `scripts/validation` on sys.path) — harmless (its `main()` is
  `__main__`-guarded), and lazy here so merely importing `route` costs nothing.

Scoring (interpretable, not a magic scalar):
  For each initiative we expose the two components `best_title_match` scores on:
    slug_overlap  — # signal tokens that hit the initiative's SLUG tokens
                    (the fingerprint: clawgate, sysredis, tekton, …)
    title_overlap — # signal tokens that hit TITLE-only tokens (title minus slug)
  A candidate is `confident` iff it clears the SAME bar as `best_title_match`:
    slug_overlap >= 2   OR
    exactly one slug hit AND that token is UNIQUE (document-frequency == 1)
                        to this initiative among the eligible set  OR
    title_overlap >= 2
  The df-uniqueness gate is what stops a single token SHARED across siblings
  (e.g. `blocks` in both `app-blocks` and `app-blocks-followups`) from firing a
  confident match on either, while still letting a single DISTINCTIVE token
  (`followups`, `tekton`) match. `score = slug_overlap + 0.25*title_overlap` is
  the headline number for display; the actual RANK order uses the full
  deterministic tuple `(confident, slug_overlap, title_overlap, slug-length,
  slug)` — identical to `best_title_match`'s tie-breaks — so the top confident
  row equals what `best_title_match` would have picked.

Requires (only for the live read / CLI, NOT for the pure `rank_matches`):
    KUBECONFIG  — homelab kubeconfig (the DB is only reachable via port-forward)
    kubectl     — on PATH
    psycopg2    — python dep
On NixOS run under:
    nix-shell -p "python3.withPackages(p:[p.psycopg2 p.requests])" \
      --run 'python scripts/initiatives/route.py "your signal text"'
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

# The scan we borrow the matcher from (hyphenated filename → importlib, not import).
SCAN_PATH = Path(__file__).resolve().parents[1] / "session-analysis" / "initiative-scan.py"
# chquery lives here; the scan adds it to sys.path on import, we mirror that so the
# top-level `import chquery` resolves regardless of cwd.
VALIDATION_DIR = Path(__file__).resolve().parents[1] / "validation"

# The shared mailbox-Postgres helper (kubectl port-forward + psycopg2 + DSN-from-secret).
MAILDB_PATH = Path(__file__).resolve().parents[1] / "mail-actions" / "_db.py"

# Interpretable headline score: a slug hit is worth more than a title hit. The RANK
# tie-breaks use the full component tuple (see module docstring), not this scalar.
SLUG_WEIGHT = 1.0
TITLE_WEIGHT = 0.25


# --------------------------------------------------------------------------- #
# Lazy import of the scan's matcher (single-sourced; not reimplemented).
# --------------------------------------------------------------------------- #
_scan_mod = None


def _scan():
    """Load initiative-scan.py by explicit path and cache it.

    Lazy so importing `route` (e.g. by a repo-cos/mail-actions caller) stays cheap
    and side-effect-light — the scan's top-level `import chquery as Q` only runs
    the first time a match is actually computed. `chquery` needs `requests` and the
    `scripts/validation` dir on sys.path; we add the latter here (idempotently)."""
    global _scan_mod
    if _scan_mod is None:
        vdir = str(VALIDATION_DIR)
        if vdir not in sys.path:
            sys.path.insert(0, vdir)
        spec = importlib.util.spec_from_file_location("initiative_scan_for_route", SCAN_PATH)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {SCAN_PATH}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _scan_mod = mod
    return _scan_mod


# --------------------------------------------------------------------------- #
# Pure core — no infra. Unit-tested directly with fixture initiative sets.
# --------------------------------------------------------------------------- #
def _repo_matches(cand_repo: str | None, want: str | None) -> bool:
    """Does an initiative's `repo` path match a caller-supplied `--repo` scope?

    Accepts a full path (`/home/zach/workspace/devrc`), a worktree/subdir under the
    repo (the repo is that path's ancestor), or a bare basename (`devrc`) — mirroring
    how `best_title_match` is scoped by cwd→repo, but tolerant of the shorthands a
    human/caller will actually pass. Empty `want` matches everything (no scope)."""
    if not want:
        return True
    if not cand_repo:
        return False
    c = str(cand_repo).rstrip("/")
    w = str(want).rstrip("/")
    cr, wr = os.path.realpath(c), os.path.realpath(w)
    if cr == wr or wr.startswith(cr + "/"):  # exact, or want is a subdir/worktree of the repo
        return True
    return os.path.basename(c) == os.path.basename(w)


def rank_matches(signal_text: str, initiatives: list[dict],
                 repo: str | None = None, limit: int | None = 5) -> list[dict]:
    """PURE: rank existing initiatives against a free-text signal, best-first.

    Reuses the scan's `text_tokens` / `slug_tokens` and `best_title_match`'s scoring
    components (slug_overlap, title_overlap, the df-uniqueness confidence gate), but
    returns ALL candidates with any token overlap ranked — not just the single best.

    Args:
      signal_text: the incoming free text (task title / proposal / mail subject).
      initiatives: rows from `initiatives.current` — each needs at least `slug`,
                   `repo`, `title` (extra keys ignored).
      repo:        optional path/basename to scope candidates to one repo.
      limit:       cap the returned list (None = no cap).

    Returns a list of dicts sorted best-first, each:
      {slug, repo, title, score, slug_overlap, title_overlap, matched_tokens, confident}
    where `confident` uses the SAME bar as `best_title_match`. An empty return (no
    candidate had any overlap) is the caller's "likely new work" signal — see
    `classify`."""
    scan = _scan()
    text_tokens = scan.text_tokens
    slug_tokens = scan.slug_tokens
    fingerprint = scan.initiative_fingerprint

    signal_toks = set(text_tokens(signal_text or ""))

    candidates = initiatives
    if repo:
        candidates = [i for i in initiatives if _repo_matches(i.get("repo"), repo)]

    # Document frequency of each token across the ELIGIBLE set (post repo-scope), so a
    # single slug hit can require that token to be UNIQUE (df == 1) — byte-identical to
    # best_title_match's uniqueness gate. Scoping df to the eligible set matches the
    # scan, which builds df per-repo (its candidates are already repo-filtered).
    df: dict[str, int] = {}
    for ini in candidates:
        toks = set(slug_tokens(ini.get("slug", "") or "")) \
            | set(text_tokens(ini.get("title") or ""))
        for t in toks:
            df[t] = df.get(t, 0) + 1

    results: list[dict] = []
    for ini in candidates:
        # Fingerprint = slug tokens, or TITLE tokens for a date-only/degenerate slug
        # (initiative_fingerprint) — so a bare-date initiative in the store isn't
        # structurally unroutable. Strictly additive; a real slug is unchanged.
        slug_t = set(fingerprint(ini))
        title_t = set(text_tokens(ini.get("title") or ""))
        slug_hits = signal_toks & slug_t
        title_hits = signal_toks & (title_t - slug_t)
        slug_overlap = len(slug_hits)
        title_overlap = len(title_hits)
        if slug_overlap == 0 and title_overlap == 0:
            continue  # no overlap at all — not a candidate

        unique_single = slug_overlap == 1 and df.get(next(iter(slug_hits)), 1) == 1
        confident = slug_overlap >= 2 or unique_single or title_overlap >= 2
        score = round(slug_overlap * SLUG_WEIGHT + title_overlap * TITLE_WEIGHT, 3)
        results.append({
            "slug": ini.get("slug"),
            "repo": ini.get("repo"),
            "title": ini.get("title"),
            "score": score,
            "slug_overlap": slug_overlap,
            "title_overlap": title_overlap,
            "matched_tokens": sorted(slug_hits | title_hits),
            "confident": confident,
        })

    # Rank: confident first, then the same tie-breaks best_title_match uses — a
    # real-SLUG fingerprint outranks a date-only TITLE fallback, then
    # (slug_overlap, title_overlap, slug length, slug lexical) — so the top confident
    # row is exactly what best_title_match would have returned for this signal.
    def _real_slug(slug: str | None) -> int:
        return 1 if slug_tokens(slug or "") else 0
    results.sort(
        key=lambda r: (r["confident"], _real_slug(r["slug"]), r["slug_overlap"],
                       r["title_overlap"], len(r["slug"] or ""), r["slug"] or ""),
        reverse=True,
    )
    if limit is not None and limit >= 0:
        results = results[:limit]
    return results


def classify(ranked: list[dict]) -> str:
    """Top-level verdict for a ranked list: confident match vs likely-new-work.

    A weak/absent match surfaces as new work rather than being force-fit to the
    nearest initiative (the router suggests, it does not gate)."""
    if ranked and ranked[0].get("confident"):
        return f"confident match: {ranked[0]['slug']}"
    return "no confident match — likely new work"


# --------------------------------------------------------------------------- #
# I/O layer — read initiatives.current, then call the pure core.
# --------------------------------------------------------------------------- #
def _import_maildb():
    """Load MailDB from scripts/mail-actions/_db.py by EXPLICIT importlib path.

    Do NOT put mail-actions/ on sys.path — its `llm.py` shadows other modules and
    breaks callers (documented in the repo CLAUDE.md; sync.py/repo-cos hit the same
    trap). `_db.py` imports only stdlib+psycopg2, so a standalone load is safe."""
    spec = importlib.util.spec_from_file_location("initiatives_route_maildb", MAILDB_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {MAILDB_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.MailDB


def load_current() -> list[dict]:
    """Read `initiatives.current` → list of initiative dicts (slug/repo/title + a few
    context fields). Reads the Phase-1 store live; does NOT re-run the scan (the whole
    point of Phase 1 was to make this cheap). Raises on an unreachable store — the CLI
    turns that into a clear error rather than silently falling back."""
    import psycopg2.extras  # local so importing route needs no psycopg2

    MailDB = _import_maildb()
    with MailDB() as db:
        with db.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT slug, repo, title, momentum, last_touch, current_doc "
                "FROM initiatives.current"
            )
            return [dict(r) for r in cur.fetchall()]


# --------------------------------------------------------------------------- #
# Callable API — importable by repo-cos / mail-actions.
# --------------------------------------------------------------------------- #
def route(signal_text: str, repo: str | None = None, limit: int | None = 5) -> list[dict]:
    """Route a signal against the LIVE `initiatives.current` and return the ranking.

    The one call a caller (repo-cos with a proposal, mail-actions with a subject)
    makes:  `route("Harden the clawgate approval hook")`  →  ranked list (see
    `rank_matches` for the dict shape) + use `classify(...)` for the verdict.
    Reads the store; the pure `rank_matches` is what you'd unit-test."""
    initiatives = load_current()
    return rank_matches(signal_text, initiatives, repo=repo, limit=limit)


# --------------------------------------------------------------------------- #
# CLI rendering
# --------------------------------------------------------------------------- #
def _short_repo(repo: str | None) -> str:
    return os.path.basename(str(repo).rstrip("/")) if repo else "?"


def render(ranked: list[dict], signal: str, repo: str | None) -> str:
    """Human-readable ranked output: the verdict, then a scannable table."""
    out: list[str] = []
    scope = f"  [repo={_short_repo(repo)}]" if repo else ""
    out.append(f'signal: "{signal}"{scope}')
    out.append(f"verdict: {classify(ranked)}")
    if not ranked:
        out.append("  (no existing initiative shares a meaningful token — likely new work)")
        return "\n".join(out)
    out.append("")
    hdr = (f"{'#':>2} {'ok':<2} {'score':>5} {'sl':>2} {'ti':>2}  "
           f"{'repo':<12} {'slug':<34} matched")
    out.append(hdr)
    out.append("-" * len(hdr))
    for i, r in enumerate(ranked, 1):
        mark = "✓" if r["confident"] else "·"
        out.append(
            f"{i:>2} {mark:<2} {r['score']:>5} {r['slug_overlap']:>2} "
            f"{r['title_overlap']:>2}  {_short_repo(r['repo']):<12.12} "
            f"{str(r['slug'] or ''):<34.34} {', '.join(r['matched_tokens'])}"
        )
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Route a free-text signal to the best-matching existing "
                    "initiative(s) in initiatives.current (suggests, never acts).")
    p.add_argument("signal", help="the incoming free text (task title / proposal / "
                                  "mail subject) to route")
    p.add_argument("--repo", default=None,
                   help="scope candidates to one repo (full path, worktree subdir, "
                        "or bare basename like 'devrc')")
    p.add_argument("--limit", type=int, default=5,
                   help="max ranked matches to return (default 5)")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON instead of the table")
    return p.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    try:
        initiatives = load_current()
    except Exception as exc:  # noqa: BLE001 - surface any store-read failure cleanly
        print(f"error: could not read initiatives.current: {exc}", file=sys.stderr)
        print("  the router reads the Phase-1 store — it does NOT re-run the scan. "
              "Needs KUBECONFIG=$KC_HOMELAB + kubectl + psycopg2.", file=sys.stderr)
        return 1

    ranked = rank_matches(a.signal, initiatives, repo=a.repo, limit=a.limit)

    if a.json:
        payload = {
            "signal": a.signal,
            "repo": a.repo,
            "confident": bool(ranked and ranked[0]["confident"]),
            "classification": classify(ranked),
            "matches": ranked,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    else:
        print(render(ranked, a.signal, a.repo))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
