#!/usr/bin/env python3
"""mail-actions — action-required email extractor over the self-hosted inbox.

Pipeline (see scripts/mail-actions/README.md for the full picture):
  Stage 1  deterministic noise DROP  (filter.py, pure, no LLM)
  Stage 2  LLM extraction            (llm.py, OpenRouter)
  Stage 3  persist + mark state      (_db.py, idempotent via mail.processed_at)
  Stage 4  surface to clawgate       (clawgate.py, optional, --emit-clawgate)

Delta/idempotency: a row is only touched while `mail.processed_at IS NULL`. Every
processed row is labelled ('bulk' | 'fyi' | 'action-required' | 'invoice' |
'superseded' | 'sent') and stamped, so a re-run is a no-op over already-seen mail.
  invoice    — an invoice (auto-paid; captured by the archiver, never an action)
  superseded — an older message of a thread already represented by a newer one
  sent       — the owner's own mail (from an owner address); never an action

Thread reconciliation (on the mail_actions.thread_key column):
  Feature 1 (cross-run supersede) — when a NEWER message's action is inserted, the
    older OPEN action for the same thread is set status='superseded' (the other party
    replied → the stale ask is no longer the live one).
  Feature 2 (auto-close on owner reply) — at the START of a run, any OPEN action whose
    thread the OWNER has since replied in (owner reply received_at > action) is set
    status='done'. Inert until the owner's sent mail lands in `mail` (see README).

Subcommands:
  run               run the action-required pipeline (filter → LLM → persist)
  archive-invoices  extract PDF invoices from mail → upload PDF+JSON to MinIO
  list              print open mail_actions (the artifact, for verification)

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
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import archive  # noqa: E402  (sibling module, reuse the invoice definition — do NOT duplicate)
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


def thread_key(headers: dict | None, message_id: str | None) -> str:
    """Stable key grouping every message of one email thread together.

    Resolution order (RFC 5322 threading), case-insensitive on header names:
      1. `References` — a whitespace-separated list of <msg-id>s; return the FIRST
         (the thread root, which every reply carries).
      2. `In-Reply-To` — the parent's msg-id, stripped of angle brackets.
      3. the mail's own `message_id`, stripped.

    A thread root has neither References nor In-Reply-To, and its own message_id is
    exactly what replies put in References[0], so the root and all its replies map to
    the same key. Returns "" only when nothing identifying is present."""
    hdrs = headers or {}
    # Case-insensitive header lookup (header dicts in this pipeline aren't normalized).
    lower = {str(k).lower(): v for k, v in hdrs.items()}

    refs = lower.get("references")
    if refs and str(refs).strip():
        ids = str(refs).split()
        if ids:
            return ids[0].strip().strip("<>")

    in_reply_to = lower.get("in-reply-to")
    if in_reply_to and str(in_reply_to).strip():
        # In-Reply-To is normally a single id, but tolerate extra tokens.
        first = str(in_reply_to).split()[0]
        return first.strip().strip("<>")

    return (message_id or "").strip().strip("<>")


# Owner addresses: a reply from any of these means the action is handled (Feature 2),
# and an inbound survivor authored by one is the owner's own mail, never an action.
DEFAULT_OWNER_ADDRS = "zachlowden1@gmail.com,zach@civitai.com,zacxdev@gmail.com"


def owner_addrs() -> set[str]:
    """Normalized (lowercase, stripped) owner addresses from
    $MAIL_ACTIONS_OWNER_ADDRS (comma-separated), else the built-in default."""
    raw = os.environ.get("MAIL_ACTIONS_OWNER_ADDRS") or DEFAULT_OWNER_ADDRS
    return {a.strip().lower() for a in raw.split(",") if a.strip()}


def reconcile_owner_replies(db, owners: set[str]) -> int:
    """Auto-close OPEN actions whose thread the OWNER has since replied in.

    thread_key isn't computable in SQL, so the grouping is done here in Python:
      1. pull OPEN actions (id, thread_key, received_at); skip NULL-keyed legacy rows;
      2. pull every mail authored by an owner address (regardless of via_gmail — the
         owner's BCC'd/forwarded sent mail may be via_gmail=false) and key each;
      3. close an action iff some owner message shares its thread_key AND arrived
         AFTER the action (the timestamp guard stops a stale owner message closing a
         fresh action from a later inbound reply).
    Returns the number of actions closed."""
    open_actions = db.fetch_open_actions_min()
    if not open_actions:
        return 0
    owner_msgs = db.fetch_owner_messages(sorted(owners))

    # thread_key -> latest owner-reply received_at seen for that thread.
    owner_latest: dict[str, object] = {}
    for m in owner_msgs:
        tkey = thread_key(m.get("headers"), m.get("message_id"))
        if not tkey:
            continue
        ts = m.get("received_at")
        if ts is None:
            continue
        cur = owner_latest.get(tkey)
        if cur is None or ts > cur:
            owner_latest[tkey] = ts

    to_close: list[int] = []
    for a in open_actions:
        tkey = a.get("thread_key")
        if not tkey:  # legacy row with no thread_key — can't be matched.
            continue
        owner_ts = owner_latest.get(tkey)
        a_ts = a.get("received_at")
        if owner_ts is None or a_ts is None:
            continue
        if owner_ts > a_ts:  # owner replied AFTER the action arrived → handled.
            to_close.append(a["id"])

    return db.close_actions_done(to_close)


def _is_invoice(db, r) -> bool:
    """True iff this survivor is an invoice by the SAME definition the archiver uses:
    load the mail's raw bytes, parse PDF attachments, and test
    `archive.is_archive_candidate`. Called only for survivors (few), so the whole
    backlog's `raw` is never loaded. A None/empty raw → not an invoice (no PDFs)."""
    raw = db.fetch_raw(r["id"])
    if not raw:
        return False
    atts = archive.extract_pdf_attachments(raw)
    return archive.is_archive_candidate(
        from_addr=r.get("from_addr"),
        subject=r.get("subject"),
        attachments=atts,
    )


def cmd_run(args) -> int:
    from _db import MailDB

    owners = owner_addrs()

    with MailDB() as db:
        db.ensure_schema()

        # Feature 2: BEFORE fetching new survivors, close any open action whose thread
        # the owner has since replied in (owner reply = handled). Skipped on --dry-run
        # since it writes. Inert until the owner's sent mail actually lands in `mail`
        # (see README "Auto-close on owner reply (Feature 2)").
        closed = 0 if args.dry_run else reconcile_owner_replies(db, owners)

        rows = db.fetch_unprocessed(limit=args.limit)
        dropped, survivors = _partition(rows)

        summary = {
            "delta_rows": len(rows),
            "dropped_bulk": len(dropped),
            "survivors": len(survivors),
            "closed": closed,
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
        invoices = 0
        superseded = 0
        sent = 0
        errors = 0
        emitted = 0
        seen_threads: set[str] = set()
        # Survivors arrive most-recent-first (fetch_unprocessed ORDER BY received_at
        # DESC), so the FIRST survivor seen for a thread is the latest message — the
        # one we keep; older messages of an already-seen thread are superseded.
        for r, _res in survivors:
            tkey = thread_key(r.get("headers"), r.get("message_id"))

            # (0) owner's own mail is never an action — label 'sent', skip the LLM.
            # Owner mail is usually NOT via_gmail (so it won't appear here), but guard
            # defensively in case a forwarded/BCC'd copy does.
            if (r.get("from_addr") or "").strip().lower() in owners:
                db.mark_processed(r["id"], "sent")
                sent += 1
                if tkey:
                    seen_threads.add(tkey)
                continue

            # (1) thread-dedup: an older message of a thread we've already represented.
            if tkey and tkey in seen_threads:
                db.mark_processed(r["id"], "superseded")
                superseded += 1
                continue
            if tkey:
                seen_threads.add(tkey)

            # (2) invoice check: invoices are auto-paid + captured by the archiver for
            # tax records — they are never action items, so skip the LLM entirely.
            if _is_invoice(db, r):
                db.mark_processed(r["id"], "invoice")
                invoices += 1
                continue

            # (3) otherwise: LLM extraction as before.
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
                # Feature 1: a NEWER message's action retires the older OPEN action for
                # this thread (cross-run, persisted). Guarded by received_at so an older
                # message can't supersede a newer open action.
                if tkey:
                    superseded += db.supersede_open_actions(
                        thread_key=tkey, before_received_at=r.get("received_at"),
                    )
                inserted = db.insert_action({
                    "mail_id": r["id"],
                    "message_id": r.get("message_id"),
                    "from_addr": r.get("from_addr"),
                    "subject": r.get("subject"),
                    "received_at": r.get("received_at"),
                    "thread_key": tkey or None,
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
            "invoice": invoices,
            "superseded": superseded,
            "sent": sent,
            "closed": closed,
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


def cmd_archive(args) -> int:
    """Invoice archiver: scan ALL via_gmail mail (independent of processed_at), extract
    PDF invoice attachments, upload each + a JSON sidecar to a per-year MinIO bucket,
    and label the mail `invoice-archived` only after ALL its PDFs upload OK.
    """
    import json as _json

    import archive as a
    from _db import MailDB

    summary = {
        "scanned": 0, "candidates": 0, "pdfs_uploaded": 0, "sidecars": 0,
        "buckets_touched": [], "errors": 0, "labeled": 0,
    }
    buckets_seen: set[str] = set()
    candidates_out: list[dict] = []

    with MailDB() as db:
        rows = db.fetch_unarchived(limit=args.limit)
        summary["scanned"] = len(rows)

        # Identify candidates (pure) first — used by both dry-run and live.
        candidates = []
        for r in rows:
            atts = a.extract_pdf_attachments(r["raw"] or b"")
            if not a.is_archive_candidate(
                from_addr=r.get("from_addr"), subject=r.get("subject"),
                attachments=atts,
            ):
                continue
            dt = a.invoice_date(r.get("date_header"), r.get("received_at"))
            vendor = a.vendor_domain(r.get("from_addr"))
            bucket = a.bucket_for(dt)
            candidates.append((r, atts, dt, vendor, bucket))
        summary["candidates"] = len(candidates)

        if args.dry_run:
            for r, atts, dt, vendor, bucket in candidates:
                keys = [
                    a.object_key(vendor=vendor, dt=dt, filename=att.filename,
                                 message_id=r.get("message_id"))
                    for att in atts
                ]
                buckets_seen.add(bucket)
                candidates_out.append({
                    "mail_id": r["id"], "from_addr": r.get("from_addr"),
                    "subject": r.get("subject"), "bucket": bucket,
                    "pdf_filenames": [att.filename for att in atts],
                    "object_keys": keys,
                })
            summary["buckets_touched"] = sorted(buckets_seen)
            summary["mode"] = "dry-run"
            if args.json:
                print(_json.dumps(
                    {"summary": summary, "candidates": candidates_out}, indent=2,
                    default=str,
                ))
            else:
                _print_archive_dry_run(candidates_out, summary)
            return 0

        # Live: upload PDFs + sidecars, then label.
        from _minio import MinioArchive

        with MinioArchive() as mc:
            ensured: set[str] = set()
            for r, atts, dt, vendor, bucket in candidates:
                amount = None
                try:
                    amount = db.amount_for_mail(r["id"])
                except Exception:  # noqa: BLE001 — amount is best-effort, never fatal
                    amount = None

                mail_ok = True
                for att in atts:
                    key = a.object_key(
                        vendor=vendor, dt=dt, filename=att.filename,
                        message_id=r.get("message_id"),
                    )
                    try:
                        if bucket not in ensured:
                            if mc.ensure_bucket(bucket):
                                pass
                            ensured.add(bucket)
                        buckets_seen.add(bucket)
                        mc.put_object(bucket, key, att.data, "application/pdf")
                        summary["pdfs_uploaded"] += 1
                        sidecar = a.sidecar_metadata(
                            vendor=vendor, from_addr=r.get("from_addr"), dt=dt,
                            amount=amount, subject=r.get("subject"),
                            message_id=r.get("message_id"), mail_id=r["id"],
                        )
                        body = _json.dumps(sidecar, indent=2, default=str).encode()
                        mc.put_object(bucket, key + ".json", body, "application/json")
                        summary["sidecars"] += 1
                    except Exception as exc:  # noqa: BLE001 — report, skip labeling, keep going
                        mail_ok = False
                        summary["errors"] += 1
                        print(f"  ! upload failed mail_id={r['id']} key={key}: {exc}",
                              file=sys.stderr)

                # Label ONLY if every PDF+sidecar for this mail uploaded successfully.
                if mail_ok and atts:
                    db.add_label(r["id"], a.ARCHIVED_LABEL)
                    db.commit()
                    summary["labeled"] += 1

        summary["buckets_touched"] = sorted(buckets_seen)
        summary["mode"] = "run"
        if args.json:
            print(_json.dumps(summary, indent=2, default=str))
        else:
            _print_archive_run(summary)
        return 0


def _print_archive_dry_run(candidates, summary) -> None:
    print(f"DRY RUN — scanned {summary['scanned']} unarchived via_gmail rows → "
          f"{summary['candidates']} invoice candidate(s)")
    if summary["buckets_touched"]:
        print(f"  buckets that WOULD be touched: {', '.join(summary['buckets_touched'])}")
    print()
    for c in candidates:
        print(f"  mail#{c['mail_id']:<6} {(c['from_addr'] or '')[:36]:<36} "
              f"{(c['subject'] or '')[:46]!r}")
        for fn, key in zip(c["pdf_filenames"], c["object_keys"]):
            print(f"      PDF {fn!r}")
            print(f"       → s3://{c['bucket']}/{key}  (+ .json sidecar)")
    if not candidates:
        print("  (no invoice candidates)")


def _print_archive_run(s) -> None:
    print(f"ARCHIVE RUN — scanned {s['scanned']} rows")
    print(f"  candidates:       {s['candidates']}")
    print(f"  PDFs uploaded:    {s['pdfs_uploaded']}")
    print(f"  sidecars written: {s['sidecars']}")
    print(f"  mails labeled:    {s['labeled']}")
    print(f"  buckets touched:  {', '.join(s['buckets_touched']) or '(none)'}")
    if s["errors"]:
        print(f"  upload errors:    {s['errors']}")


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
    print(f"  invoice:          {s.get('invoice', 0)}")
    print(f"  superseded:       {s.get('superseded', 0)}")
    print(f"  sent (owner):     {s.get('sent', 0)}")
    print(f"  closed (owner):   {s.get('closed', 0)}")
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

    arch = sub.add_parser(
        "archive-invoices",
        help="extract PDF invoices from mail → upload PDF+JSON sidecar to MinIO",
    )
    arch.add_argument("--dry-run", action="store_true",
                      help="list candidates + target bucket/key; NO uploads, NO label writes")
    arch.add_argument("--limit", type=int, default=None,
                      help="max unarchived rows to scan (default: all)")
    arch.add_argument("--json", action="store_true", help="machine-readable summary")
    arch.set_defaults(func=cmd_archive)

    ls = sub.add_parser("list", help="print open mail_actions (the artifact)")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=cmd_list)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
