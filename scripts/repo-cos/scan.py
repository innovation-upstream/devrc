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
import exclusions  # noqa: E402
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
    # --show-exclusions: print the current deterministic exclusion state and exit.
    if getattr(args, "show_exclusions", False):
        print(exclusions.format_state(exclusions.load_state()))
        return 0

    # ---- DETERMINISTIC EXCLUSIONS (load first; a reply can add to them before scan) ----
    # Load the persisted exclusion state. If a reply was fetched (see below), it's parsed
    # into repo-level exclusions and applied BEFORE the scan — so excluded repos are never
    # scanned or synthesized and CANNOT reappear no matter what the LLM does.
    excl_state = exclusions.load_state()

    # ---- REPLY-FEEDBACK: fetch Zach's reply to LAST week's digest ONCE, up front.
    # It serves two masters: (1) the DETERMINISTIC exclusion parse below, and (2) the
    # existing context-injection into synthesis (passed through as `fb`). Best-effort — a
    # None result (no prior digest, no reply, IMAP down) proceeds as a stateless run would.
    # --no-feedback skips the fetch (and the exclusion parse) entirely. The Stage-1-only
    # smoke modes (--no-llm/--candidates-only) skip the fetch too — they must stay free +
    # network-less — but the ALREADY-PERSISTED exclusion filter below still applies to them.
    fb = None
    stage1_only = args.no_llm or args.candidates_only
    if not args.no_feedback and not stage1_only:
        try:
            import feedback as feedback_mod
            fb = feedback_mod.fetch_last_feedback()
        except Exception as exc:  # noqa: BLE001
            print(f"  ! feedback fetch failed (proceeding without): {exc}", file=sys.stderr)
            fb = None

        if fb is not None:
            # Parse the reply into HARD exclusions against the digest Zach ACTUALLY SAW
            # (the last EMAILED one — proposals rotate + latest.json is overwritten each run).
            try:
                emailed = exclusions.load_last_emailed()
                emailed_props = (emailed or {}).get("proposals") or []
                alias_map = exclusions.build_alias_map(DEFAULT_REPOS)
                parsed = exclusions.parse_reply(fb.reply_text, emailed_props,
                                                alias_map=alias_map)
                if (parsed["exclude"] or parsed["resume"] or parsed.get("dismiss")
                        or parsed.get("approve")):
                    exclusions.apply(excl_state, parsed, source="reply")
                    # ---- APPROVE → clawgate Task (the ONLY new network in this path) ----
                    # For each approved proposal, POST a durable Task card to clawgate and
                    # SUPPRESS-ON-SUCCESS ONLY: a proposal whose POST returned a task id is
                    # recorded in excl_state["approved"] so it can't re-nag; a FAILED POST is
                    # left unsuppressed → it re-proposes next week (a natural retry).
                    if parsed.get("approve"):
                        _post_approvals_to_clawgate(parsed["approve"], excl_state)
                    exclusions.save_state(excl_state)
                    if parsed["exclude"]:
                        names = ", ".join(e["repo"] for e in parsed["exclude"])
                        print(f"  exclusions: reply excluded {names}", file=sys.stderr)
                    if parsed["resume"]:
                        print(f"  exclusions: reply resumed {', '.join(parsed['resume'])}",
                              file=sys.stderr)
                    if parsed.get("dismiss"):
                        nrefs = sum(len(d.get("evidence") or []) for d in parsed["dismiss"])
                        print(f"  exclusions: reply dismissed {len(parsed['dismiss'])} "
                              f"recommendation(s) ({nrefs} evidence ref(s)); repos kept",
                              file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                print(f"  ! exclusion parse failed (proceeding): {exc}", file=sys.stderr)

    repos = resolve_repos(args.repos)
    if not repos:
        print("ERROR: no repos to scan (none of the defaults exist and --repos empty).",
              file=sys.stderr)
        return 2

    # Drop excluded repos deterministically — they never reach the scan or the LLM.
    repos, excluded = exclusions.filter_repos(repos, excl_state)
    excluded_names = exclusions.excluded_names(excl_state)
    if excluded:
        names = ", ".join(Path(r).name for r in excluded)
        print(f"  excluding {len(excluded)} repo(s): {names} "
              f"(reply 'resume <repo>' to re-enable)", file=sys.stderr)
    if not repos:
        print("ERROR: no repos to scan (all resolved repos are excluded — "
              "reply 'resume <repo>' or edit ~/.config/repo-cos/exclusions.json).",
              file=sys.stderr)
        return 2

    candidates, scans = prescan.scan_all(repos, args.limit_candidates)

    # ---- SUPPRESSED-RECOMMENDATION FILTER (drop suppressed proposals BEFORE the LLM) ----
    # A recommendation Zach replied "skip" (→ excl_state["dismissed"]) OR "approve" (→
    # excl_state["approved"], already queued in clawgate) to is removed here so its signal
    # never reaches synthesis and cannot re-form as the same proposal — while the rest of that
    # repo still surfaces. ONE combined suppressed-set (dismissed ∪ approved).
    candidates, dropped = exclusions.filter_candidates(candidates, excl_state)
    if dropped:
        print(f"  suppressed: {len(dropped)} candidate(s) (dismissed ∪ approved)",
              file=sys.stderr)
    dismissed_recs = exclusions.dismissed_entries(excl_state)
    cand_dicts = [c.as_dict() for c in candidates]

    scan_errors = [(s.repo, s.error) for s in scans if s.error]
    for repo, err in scan_errors:
        print(f"  ! skipped {repo}: {err}", file=sys.stderr)

    # ---- Stage-1-only smoke mode: no LLM, no key, no spend. ----
    if args.no_llm or args.candidates_only:
        return _emit_candidates(candidates, scans, cand_dicts, args,
                                dismissed_recs=dismissed_recs)

    # ---- Stage 2: LLM synthesis over survivors. ----
    import llm

    if not os.environ.get("OPENROUTER_API_KEY"):
        print("ERROR: OPENROUTER_API_KEY not set — cannot run Stage 2 synthesis.\n"
              "       Use --no-llm (or --candidates-only) for the free pre-scan smoke test.",
              file=sys.stderr)
        return 2

    if not cand_dicts:
        # Nothing to synthesize — emit an honest empty digest without spending.
        body = digest.render([], candidate_count=0, excluded_repos=excluded_names,
                             dismissed_count=len(dismissed_recs))
        return _deliver(body, args, proposals=[], candidate_count=0, approx_tokens=0,
                        feedback_applied=False, excluded_repos=excluded_names,
                        dismissed_count=len(dismissed_recs))

    # `fb` (Zach's reply) was already fetched up front — reused here as synthesis CONTEXT
    # (nuance the deterministic exclusion layer can't express). The reply is passed through
    # unchanged; the exclusion filter already ran on the repo list above.
    try:
        result = llm.synthesize(cand_dicts, top=args.top, model=args.model, feedback=fb)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: synthesis failed: {exc}", file=sys.stderr)
        return 1

    body = digest.render(
        result.proposals,
        candidate_count=len(cand_dicts),
        approx_tokens=result.approx_prompt_tokens,
        excluded_repos=excluded_names,
        dismissed_count=len(dismissed_recs),
    )
    print(f"  synthesis: model={result.model} candidates={len(cand_dicts)} "
          f"proposals={len(result.proposals)} ~prompt_tokens={result.approx_prompt_tokens} "
          f"feedback_applied={fb is not None} excluded={len(excluded_names)} "
          f"dismissed={len(dismissed_recs)}",
          file=sys.stderr)
    return _deliver(
        body, args, proposals=result.proposals,
        candidate_count=len(cand_dicts), approx_tokens=result.approx_prompt_tokens,
        feedback_applied=fb is not None, excluded_repos=excluded_names,
        dismissed_count=len(dismissed_recs),
    )


def _post_approvals_to_clawgate(approvals, excl_state, *, _clawgate=None) -> None:
    """POST each approved proposal to clawgate as a durable Task, then SUPPRESS-ON-SUCCESS.

    For every approved proposal (full dict from parse_reply), build a card + POST it. Collect
    {first-evidence-ref → task_id-or-None} and hand it to `exclusions.apply_approvals`, which
    records ONLY the ones with a real task id into excl_state["approved"] — a failed POST is
    left unsuppressed so it re-proposes next week. Best-effort; never raises (mirrors the rest
    of the reply path). `_clawgate` is injectable for tests (no real network)."""
    clawgate = _clawgate
    if clawgate is None:
        import clawgate as clawgate  # noqa: PLC0414
    task_ids: dict = {}
    posted = 0
    for prop in approvals:
        evidence = prop.get("evidence") or []
        if not evidence:
            continue
        try:
            directory = clawgate.build_task_title(prop)
            body = clawgate.build_task_body(prop)
            repo = clawgate.resolve_repo_fullname(prop.get("repo") or "")
            tid = clawgate.post_task(directory, body, repo=repo)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! clawgate post failed (proceeding): {exc}", file=sys.stderr)
            tid = None
        task_ids[evidence[0]] = tid
        if isinstance(tid, int) and not isinstance(tid, bool):
            posted += 1
    exclusions.apply_approvals(excl_state, approvals, task_ids)
    print(f"  approved: {len(approvals)} proposal(s) → clawgate task(s) {posted}/"
          f"{len(approvals)}; suppressed", file=sys.stderr)


def _emit_candidates(candidates, scans, cand_dicts, args, *, dismissed_recs=None) -> int:
    dismissed_recs = dismissed_recs or []
    if args.json:
        print(json.dumps({
            "repos": [{"repo": s.repo, "path": s.path, "error": s.error,
                       "candidate_count": len(s.candidates)} for s in scans],
            "capped_total": len(cand_dicts),
            "dismissed_count": len(dismissed_recs),
            "candidates": cand_dicts,
        }, indent=2))
        return 0
    print(f"repo-cos pre-scan — {len(candidates)} candidate(s) "
          f"(global cap {args.limit_candidates}) across {len(scans)} repo(s)\n")
    for s in scans:
        tag = f" [ERROR: {s.error}]" if s.error else ""
        print(f"  {s.repo}: {len(s.candidates)} raw candidate(s){tag}")
    if dismissed_recs:
        print(f"\n  dismissed: {len(dismissed_recs)} recommendation(s) suppressed "
              "(repos kept):")
        for d in dismissed_recs:
            reason = f" — {d['reason']}" if d.get("reason") else ""
            print(f"    {d['ref']}{reason}")
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


def _deliver(body: str, args, *, proposals, candidate_count, approx_tokens,
             feedback_applied: bool = False, excluded_repos=None,
             dismissed_count: int = 0) -> int:
    """Print (dry-run) or send (--email) the digest. --dry-run is the default and always
    prints; --email additionally sends."""
    excluded_repos = list(excluded_repos or [])
    if args.json:
        print(json.dumps({
            "subject": digest.subject(),
            "candidate_count": candidate_count,
            "approx_prompt_tokens": approx_tokens,
            "feedback_applied": feedback_applied,
            "excluded_repos": excluded_repos,
            "dismissed_count": int(dismissed_count),
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
            subj = digest.subject()
            to = email_send.send_digest(subject=subj, body=body)
            print(f"\n  emailed digest → {to}", file=sys.stderr)
            emailed = True
            # Snapshot the EMAILED proposals so a later reply's positional refs ("1./2./…")
            # map to what Zach SAW — not the next run's rotated set. Best-effort.
            from datetime import datetime
            exclusions.write_last_emailed(
                proposals, subject=subj,
                generated_at=datetime.now().astimezone().isoformat(timespec="seconds"))
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
    p.add_argument("--no-feedback", action="store_true",
                   help="skip pulling last week's emailed reply into synthesis AND the "
                        "deterministic exclusion parse (stateless run; for clean/testing)")
    p.add_argument("--show-exclusions", action="store_true",
                   help="print the current deterministic repo-exclusion state and exit")
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
