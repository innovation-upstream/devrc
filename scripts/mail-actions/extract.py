#!/usr/bin/env python3
"""mail-actions — action-required email extractor over the self-hosted inbox.

Pipeline (see scripts/mail-actions/README.md for the full picture):
  Stage 1  deterministic noise DROP  (filter.py, pure, no LLM)
  Stage 2  LLM extraction            (llm.py, OpenRouter)
  Stage 3  persist + mark state      (_db.py, idempotent via mail.processed_at)
  Stage 4  surface to clawgate       (clawgate.py, optional, --emit-clawgate)

Delta/idempotency: a row is only touched while `mail.processed_at IS NULL`. Every
processed row is labelled ('bulk' | 'fyi' | 'action-required') and stamped, so a
re-run is a no-op over already-seen mail.

Subcommands:
  run            run the pipeline (filter → LLM → persist)
  list           print open mail_actions (the artifact, for verification)

Run flags:
  --dry-run         filter only; show survivor counts + what WOULD be extracted; no LLM, no writes
  --limit N         cap rows pulled from the delta (cost cap; default 150)
  --model NAME      OpenRouter model (default $MAIL_ACTIONS_MODEL or deepseek/deepseek-v4-flash)
  --emit-clawgate   POST a Task card per NEW action item (needs CLAWGATE_HOOK_TOKEN)
  --json            machine-readable summary

Env: OPENROUTER_API_KEY (Stage 2), KUBECONFIG (DB), CLAWGATE_HOOK_TOKEN (optional Stage 4).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import filter as f  # noqa: E402  (sibling module, not a package)
import llm  # noqa: E402

DEFAULT_LIMIT = 150
# Rough OpenRouter price for the default cheap model (USD per 1M tokens). Estimate only.
EST_USD_PER_1K_TOKENS = 0.0002
EST_TOKENS_PER_MAIL = 1500  # system+subject+truncated body+output, order of magnitude


def _est_cost(n: int) -> float:
    return n * EST_TOKENS_PER_MAIL / 1000.0 * EST_USD_PER_1K_TOKENS


def _partition(rows):
    """Split delta rows into (dropped, survivors) using the Stage-1 pure filter."""
    dropped, survivors = [], []
    for r in rows:
        res = f.classify(
            from_addr=r.get("from_addr"),
            subject=r.get("subject"),
            category=r.get("category"),
            headers=r.get("headers"),
        )
        (dropped if res.drop else survivors).append((r, res))
    return dropped, survivors


def cmd_run(args) -> int:
    from _db import MailDB

    with MailDB() as db:
        db.ensure_schema()
        rows = db.fetch_unprocessed(limit=args.limit)
        dropped, survivors = _partition(rows)

        summary = {
            "delta_rows": len(rows),
            "dropped_bulk": len(dropped),
            "survivors": len(survivors),
            "est_llm_cost_usd": round(_est_cost(len(survivors)), 4),
        }

        if args.dry_run:
            summary["mode"] = "dry-run"
            if args.json:
                print(json.dumps(summary, indent=2))
            else:
                _print_dry_run(dropped, survivors, summary)
            return 0

        api_key_present = bool(__import__("os").environ.get("OPENROUTER_API_KEY"))
        if not api_key_present:
            print("ERROR: OPENROUTER_API_KEY not set — cannot run Stage 2. "
                  "Use --dry-run for the filter-only pass.", file=sys.stderr)
            return 2

        actions = 0
        fyis = 0
        errors = 0
        emitted = 0
        for r, _res in survivors:
            try:
                ex = llm.extract(
                    from_addr=r.get("from_addr") or "",
                    subject=r.get("subject") or "",
                    body=r.get("text_body") or "",
                    model=args.model,
                )
            except Exception as exc:  # noqa: BLE001 — keep going, report at end
                errors += 1
                print(f"  ! extract failed mail_id={r['id']}: {exc}", file=sys.stderr)
                continue

            if ex.action_required:
                inserted = db.insert_action({
                    "mail_id": r["id"],
                    "message_id": r.get("message_id"),
                    "from_addr": r.get("from_addr"),
                    "subject": r.get("subject"),
                    "received_at": r.get("received_at"),
                    **ex.as_row(),
                })
                db.mark_processed(r["id"], "action-required")
                actions += 1
                if inserted and args.emit_clawgate:
                    emitted += _emit_clawgate(r, ex)
            else:
                db.mark_processed(r["id"], "fyi")
                fyis += 1

        # bulk drops: label + stamp (no LLM)
        for r, res in dropped:
            db.mark_processed(r["id"], "bulk")
        db.commit()

        summary.update({
            "mode": "run",
            "action_required": actions,
            "fyi": fyis,
            "extract_errors": errors,
            "clawgate_emitted": emitted,
            "est_llm_cost_usd": round(_est_cost(len(survivors)), 4),
        })
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            _print_run(summary)
        return 0


def _emit_clawgate(r, ex) -> int:
    try:
        from clawgate import emit_task
        ok = emit_task(
            who=ex.who, ask=ex.ask, deadline=ex.deadline, amount=ex.amount,
            source_ref=f"mail#{r['id']} {r.get('from_addr')}",
        )
        return 1 if ok else 0
    except Exception as exc:  # noqa: BLE001
        print(f"  ! clawgate emit failed mail_id={r['id']}: {exc}", file=sys.stderr)
        return 0


def cmd_list(args) -> int:
    from _db import MailDB

    with MailDB() as db:
        db.ensure_schema()
        rows = db.list_open_actions()
    if args.json:
        print(json.dumps([
            {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()}
            for r in rows
        ], indent=2))
        return 0
    if not rows:
        print("No open action items.")
        return 0
    print(f"{len(rows)} open action item(s):\n")
    for r in rows:
        print(f"  [{r['confidence']:.2f}] {r['who']} — {r['ask']}")
        meta = []
        if r.get("deadline"):
            meta.append(f"deadline={r['deadline']}")
        if r.get("amount"):
            meta.append(f"amount={r['amount']}")
        meta.append(f"from={r['from_addr']}")
        meta.append(f"mail#{r['mail_id']}")
        print(f"        {'  '.join(meta)}")
        print(f"        subj: {r['subject']}\n")
    return 0


def _print_dry_run(dropped, survivors, summary) -> None:
    print(f"DRY RUN — delta {summary['delta_rows']} rows "
          f"({summary['dropped_bulk']} dropped, {summary['survivors']} survivors)")
    print(f"  est LLM cost if run: ${summary['est_llm_cost_usd']}\n")
    print("  Survivors (would be sent to LLM):")
    for r, _res in survivors:
        print(f"    mail#{r['id']:<6} {(r.get('from_addr') or '')[:36]:<36} "
              f"{(r.get('subject') or '')[:50]!r}")
    if not survivors:
        print("    (none)")


def _print_run(s) -> None:
    print(f"RUN — delta {s['delta_rows']} rows")
    print(f"  dropped (bulk):   {s['dropped_bulk']}")
    print(f"  survivors → LLM:  {s['survivors']}")
    print(f"  action-required:  {s['action_required']}")
    print(f"  fyi:              {s['fyi']}")
    if s["extract_errors"]:
        print(f"  extract errors:   {s['extract_errors']}")
    if s.get("clawgate_emitted"):
        print(f"  clawgate emitted: {s['clawgate_emitted']}")
    print(f"  est LLM cost:     ${s['est_llm_cost_usd']}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mail-actions", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run the extraction pipeline")
    run.add_argument("--dry-run", action="store_true",
                     help="filter only; show survivors; no LLM, no writes")
    run.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                     help=f"max delta rows to pull (cost cap; default {DEFAULT_LIMIT})")
    run.add_argument("--model", default=None, help="OpenRouter model id")
    run.add_argument("--emit-clawgate", action="store_true",
                     help="POST a clawgate Task card per NEW action item")
    run.add_argument("--json", action="store_true", help="machine-readable summary")
    run.set_defaults(func=cmd_run)

    ls = sub.add_parser("list", help="print open mail_actions (the artifact)")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=cmd_list)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
