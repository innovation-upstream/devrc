#!/usr/bin/env python3
"""Assemble a reproducible eval set of real `recap_context` inputs.

Source: a frozen `initiative-scan.py --days 30 --json` capture (scan-days30.json).
We build the SAME `recap_context(ini)` the production sync feeds the model, then
curate a fixed train/test split. The split is CURATED (not random) so the test set
is guaranteed to contain the documented weak cases:

  * doc-meta summaries  — remix-session / remix-platform / next-session /
    app-blocks-arc-complete (summaries that literally say "Supersedes ….md",
    "Read-first", "Read this handoff")
  * thin context        — next-session / app-blocks-arc-complete (no next_step,
    no commits, ~0 messages)
  * rich context        — initiatives-consolidation / clawgate-chat-polish
    (next_step + open_investigations + commits) as non-degenerate controls

Train slugs are a disjoint, momentum-diverse set used only to bootstrap few-shot
demonstrations for the DSPy candidate. Everything is keyed by slug and frozen to
`eval_set.json` so every downstream step scores the identical inputs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Import the PRODUCTION recap module so we use the exact same recap_context().
sys.path.insert(0, str(HERE.parent))
import recap  # noqa: E402

SCAN_JSON = HERE / "scan-days30.json"
EVAL_SET = HERE / "eval_set.json"

# Curated, disjoint slug lists. Chosen from the day-30 scan (112 initiatives).
TEST_SLUGS = [
    "remix-session",              # doc-meta: "Supersedes …handoff-….md"
    "remix-platform",             # doc-meta: "A very long session…", kickoff msgs
    "next-session",               # thin + doc-meta: "Read-first. Prior arc detail:…md"
    "app-blocks-arc-complete",    # thin: "> …Read this handoff…", 0 msgs/commits
    "remix-templates",            # commits present, slowing
    "initiatives-consolidation",  # rich: next_step + investigation + commits
    "clawgate-chat-polish",       # rich: next_step + investigation + msgs
    "dp-prod-rightsizing-node-drain",  # investigations x2 + next_step, stalled
]
TRAIN_SLUGS = [
    "spend-analytics",
    "app-blocks-ux",
    "task-spec-drafter",
    "tekton-control-plane-ha",
    "activity-telemetry",
    "mail-automation",
    "harness-tuning",
    "repo-cos-selfhosted-complete",
    "app-blocks-session",
    "dp-prod-500-floor-resume",
]


def _index_scan() -> dict:
    report = json.loads(SCAN_JSON.read_text())
    by_slug: dict = {}
    for _repo, inis in (report.get("by_repo") or {}).items():
        for ini in inis or []:
            by_slug[ini.get("slug")] = ini
    return by_slug


def _record(ini: dict) -> dict:
    ctx = recap.recap_context(ini)
    return {
        "slug": ini.get("slug"),
        "repo": ini.get("repo"),
        "momentum": ctx.get("momentum"),
        "ctx": ctx,
    }


def build_eval_set() -> dict:
    by_slug = _index_scan()
    missing = [s for s in TRAIN_SLUGS + TEST_SLUGS if s not in by_slug]
    if missing:
        raise SystemExit(f"slugs absent from scan capture: {missing}")
    overlap = set(TRAIN_SLUGS) & set(TEST_SLUGS)
    if overlap:
        raise SystemExit(f"train/test overlap: {overlap}")
    return {
        "source": SCAN_JSON.name,
        "train": [_record(by_slug[s]) for s in TRAIN_SLUGS],
        "test": [_record(by_slug[s]) for s in TEST_SLUGS],
    }


def load_eval_set() -> dict:
    if not EVAL_SET.exists():
        data = build_eval_set()
        EVAL_SET.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return json.loads(EVAL_SET.read_text())


if __name__ == "__main__":
    data = build_eval_set()
    EVAL_SET.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    for split in ("train", "test"):
        print(f"\n=== {split} ({len(data[split])}) ===")
        for r in data[split]:
            c = r["ctx"]
            print(f"  {r['momentum']:8} msgs={len(c['recent_messages']):2} "
                  f"cmts={len(c['recent_commits']):2} inv={len(c['open_investigations']):2} "
                  f"nxt={1 if c['next_step'] else 0}  {r['slug']}")
    print(f"\nwrote {EVAL_SET}")
