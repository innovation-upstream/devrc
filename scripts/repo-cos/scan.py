#!/usr/bin/env python3
"""repo-cos — a "repo chief-of-staff": scan Zach's codebases for improvement
opportunities, rank them, and (opt-in) email the top few.

CEO model: agents bring HIM bounded, evidence-backed ideas instead of him originating
every task. The quality bar is broad ("increases productivity OR makes the repos/products
better"), so the slop-defense is STRUCTURAL, not wording:
  (a) every proposal cites concrete repo/file:line EVIDENCE from a deterministic scan;
  (b) the output is HARD-CAPPED to the top N (default 5) by leverage;
  (c) each proposal is BOUNDED/shippable (an agent could implement + verify it);
  (d) the pipeline BIASES toward CI/test-verifiable proposals (fix a skipped test, add a
      missing test, remove dead code, fix a real bug) over "nice idea, your call".

Pipeline (mirrors scripts/mail-actions/: deterministic Stage-1 → LLM on survivors):
  Stage 1  prescan.py  — cheap grep/git signals, capped PER REPO      (no LLM, no spend)
  Stage 2  llm.py      — OpenRouter clusters survivors → ranked JSON   (single call)
  Stage 3  digest.py   — one formatter for stdout AND email
  Stage 4  email_send  — Gmail SMTP, gated behind --email (default OFF)

Modes:
  (default) --dry-run        run Stage 1+2, PRINT the digest to stdout, send nothing.
  --no-llm / --candidates-only  run ONLY Stage 1, print the raw candidate list. Needs NO
                             API key — this is the free smoke test that verifies the
                             pre-scan without any OpenRouter spend.
  --email                    send the digest via Gmail SMTP (opt-in; default OFF). Builds
                             the SAME body the dry-run prints.

Repos: workbench-local. Discovered git repos under the default roots, else the hardcoded
list. NOTE: naida / vetr live only on the LAPTOP (~/workspace/scratch/), so this
workbench tool cannot see them yet.

Env:
  OPENROUTER_API_KEY   required for synthesis (not for --no-llm).
  REPO_COS_MODEL       overrides --model default.
  REPO_COS_SMTP_USER / REPO_COS_SMTP_PASSWORD  optional SMTP cred override (else SOPS).

Persistence: EVERY run writes its proposals to `~/.config/repo-cos/latest.json` (+ a
dated copy under `history/`) with an `emailed` flag — so another session can read the
exact set and evaluate it collaboratively without re-running the (rotating) LLM.

Schedule: LIVE — a weekly serverMode-gated workbench systemd user timer (`repo-cos.timer`,
Mon 08:00, mirrors `mail-actions-archive`) runs `scan.py --email` via the committed
wrapper `run-weekly.sh`. Check `systemctl --user list-timers | grep repo-cos`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import digest  # noqa: E402
import prescan  # noqa: E402

# Workbench-local default repos. naida/vetr are laptop-only (~/workspace/scratch/) —
# see the module docstring; not reachable from the workbench.
DEFAULT_REPOS = [
    "~/workspace/devrc",
    "~/workspace/homelab-talos",
    "~/workspace/kubeclaw",
    "~/workspace/kubeclaw-cloud",
    "~/workspace/kubeclaw-embed",
    "~/workspace/promptver",
    "~/workspace/baseball-manitoba-pitch",
    "~/workspace/civit/civitai",
    "~/workspace/civit/datapacket-talos",
    "~/workspace/civit/civitai-orchestration",
]

DEFAULT_LIMIT_CANDIDATES = 60
DEFAULT_TOP = 5
DEFAULT_MODEL = "deepseek/deepseek-v4-flash"

# Every run persists its proposals here so ANOTHER session can read the exact set
# that was generated/emailed and evaluate it — without re-running the (rotating) LLM.
PERSIST_DIR = Path("~/.config/repo-cos").expanduser()


def resolve_repos(override: str | None) -> list[str]:
    """--repos wins (an explicitly-passed empty string means "no repos", NOT "use
    defaults"); else the default list, filtered to existing directories."""
    if override is not None:
        return [p.strip() for p in override.split(",") if p.strip()]
    existing = []
    for r in DEFAULT_REPOS:
        if Path(r).expanduser().is_dir():
            existing.append(r)
    return existing


def cmd_scan(args) -> int:
    repos = resolve_repos(args.repos)
    if not repos:
        print("ERROR: no repos to scan (none of the defaults exist and --repos empty).",
              file=sys.stderr)
        return 2

    candidates, scans = prescan.scan_all(repos, args.limit_candidates)
    cand_dicts = [c.as_dict() for c in candidates]

    scan_errors = [(s.repo, s.error) for s in scans if s.error]
    for repo, err in scan_errors:
        print(f"  ! skipped {repo}: {err}", file=sys.stderr)

    # ---- Stage-1-only smoke mode: no LLM, no key, no spend. ----
    if args.no_llm or args.candidates_only:
        return _emit_candidates(candidates, scans, cand_dicts, args)

    # ---- Stage 2: LLM synthesis over survivors. ----
    import llm

    if not os.environ.get("OPENROUTER_API_KEY"):
        print("ERROR: OPENROUTER_API_KEY not set — cannot run Stage 2 synthesis.\n"
              "       Use --no-llm (or --candidates-only) for the free pre-scan smoke test.",
              file=sys.stderr)
        return 2

    if not cand_dicts:
        # Nothing to synthesize — emit an honest empty digest without spending.
        body = digest.render([], candidate_count=0)
        return _deliver(body, args, proposals=[], candidate_count=0, approx_tokens=0)

    try:
        result = llm.synthesize(cand_dicts, top=args.top, model=args.model)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: synthesis failed: {exc}", file=sys.stderr)
        return 1

    body = digest.render(
        result.proposals,
        candidate_count=len(cand_dicts),
        approx_tokens=result.approx_prompt_tokens,
    )
    print(f"  synthesis: model={result.model} candidates={len(cand_dicts)} "
          f"proposals={len(result.proposals)} ~prompt_tokens={result.approx_prompt_tokens}",
          file=sys.stderr)
    return _deliver(
        body, args, proposals=result.proposals,
        candidate_count=len(cand_dicts), approx_tokens=result.approx_prompt_tokens,
    )


def _emit_candidates(candidates, scans, cand_dicts, args) -> int:
    if args.json:
        print(json.dumps({
            "repos": [{"repo": s.repo, "path": s.path, "error": s.error,
                       "candidate_count": len(s.candidates)} for s in scans],
            "capped_total": len(cand_dicts),
            "candidates": cand_dicts,
        }, indent=2))
        return 0
    print(f"repo-cos pre-scan — {len(candidates)} candidate(s) "
          f"(global cap {args.limit_candidates}) across {len(scans)} repo(s)\n")
    for s in scans:
        tag = f" [ERROR: {s.error}]" if s.error else ""
        print(f"  {s.repo}: {len(s.candidates)} raw candidate(s){tag}")
    print()
    # group the capped set by repo for readability
    by_repo: dict[str, list] = {}
    for c in candidates:
        by_repo.setdefault(c.repo, []).append(c)
    for repo in sorted(by_repo):
        print(f"── {repo} ─────────────────────────────")
        for c in by_repo[repo]:
            print(f"  {c.kind:<12} {c.ref}")
            if c.text:
                print(f"               {c.text[:120]}")
        print()
    return 0


def _deliver(body: str, args, *, proposals, candidate_count, approx_tokens) -> int:
    """Print (dry-run) or send (--email) the digest. --dry-run is the default and always
    prints; --email additionally sends."""
    if args.json:
        print(json.dumps({
            "subject": digest.subject(),
            "candidate_count": candidate_count,
            "approx_prompt_tokens": approx_tokens,
            "proposals": [p.as_dict() for p in proposals],
            "emailed": bool(args.email),
        }, indent=2))
    else:
        print(body)

    emailed = False
    rc = 0
    if args.email:
        import email_send
        try:
            to = email_send.send_digest(subject=digest.subject(), body=body)
            print(f"\n  emailed digest → {to}", file=sys.stderr)
            emailed = True
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: email send failed: {exc}", file=sys.stderr)
            rc = 1

    _persist_latest(proposals, subject=digest.subject(), candidate_count=candidate_count,
                    approx_tokens=approx_tokens, emailed=emailed)
    return rc


def _persist_latest(proposals, *, subject, candidate_count, approx_tokens, emailed) -> None:
    """Write this run's proposals to ~/.config/repo-cos/latest.json (+ a dated history
    copy) so another session can read the EXACT set (not a re-rolled LLM call) and
    evaluate it collaboratively. Best-effort — never fails the run."""
    from datetime import datetime
    try:
        (PERSIST_DIR / "history").mkdir(parents=True, exist_ok=True)
        now = datetime.now().astimezone()
        payload = {
            "generated_at": now.isoformat(timespec="seconds"),
            "emailed": emailed,
            "subject": subject,
            "candidate_count": candidate_count,
            "approx_prompt_tokens": approx_tokens,
            "proposals": [p.as_dict() for p in proposals],
        }
        data = json.dumps(payload, indent=2)
        (PERSIST_DIR / "latest.json").write_text(data)
        (PERSIST_DIR / "history" / f"{now.strftime('%Y%m%d-%H%M%S')}.json").write_text(data)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! could not persist proposals: {exc}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="repo-cos", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--dry-run", action="store_true", default=True,
                   help="(default) run scan + synthesis, PRINT digest to stdout, send nothing")
    p.add_argument("--email", action="store_true",
                   help="send the digest via Gmail SMTP (opt-in; default OFF)")
    p.add_argument("--no-llm", action="store_true",
                   help="Stage-1 only: print raw candidates, no LLM, no API key needed")
    p.add_argument("--candidates-only", action="store_true",
                   help="alias for --no-llm (the free pre-scan smoke test)")
    p.add_argument("--repos", default=None,
                   help="comma-separated repo paths overriding the default list")
    p.add_argument("--limit-candidates", type=int, default=DEFAULT_LIMIT_CANDIDATES,
                   help=f"global cap on candidates fed to the LLM (default {DEFAULT_LIMIT_CANDIDATES})")
    p.add_argument("--top", type=int, default=DEFAULT_TOP,
                   help=f"max proposals to emit (default {DEFAULT_TOP})")
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help=f"OpenRouter model id (default {DEFAULT_MODEL})")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_scan)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
