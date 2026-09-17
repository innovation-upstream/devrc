#!/usr/bin/env python3
"""Re-derive the clawgate write-back guard's rung-2 split from the transcript corpus.

    python3 claudedocs/clawgate-writeback-rung2-corpus-scan.py

Read-only. It walks `~/.claude/projects` on the host it runs on and prints counts;
it starts no hook, writes nothing, and needs no environment.

WHY THIS FILE EXISTS -- THE MEASUREMENT WAS AUTHORISING A CHANGE AND LIVED NOWHERE.
PR #1747's closing comment said "the corpus scan lives in its history"; it did not.
Nobody could re-run the classification, and the first version of the split was WRONG
in a way only a re-run could find: it used `re.search` on the guard's blocking
message and so captured only the FIRST task id. A blocking message can name several
-- measured here, 13 of 172 blocking errors (7.6%) name more than one, up to four.
Every id after the first was silently dropped, and a SIBLING task's write-back
landing between the two fires then misclassified the dropped task as a re-arm. The
quoted "31 wrong for 1 right" is retracted; the corrected split is below.

🔴 THE UNIT IS `(transcript file, task id)`, and the number depends on it. Subagent
transcript files are included -- they are separate `.jsonl` files under the same
project dir, so a subagent's ladder counts as its own. A different unit gives a
different denominator, so quote the unit with the figure; an unqualified figure is
exactly how this went wrong the first time.

🔴 THE CLASSIFICATION CANNOT DISTINGUISH A PER-TASK WRITE-BACK FROM A SIBLING'S IN A
MULTI-TASK STOP unless the ids are bound individually -- which is the defect that
produced the wrong number, and the reason every regex below matches an id rather
than merely matching a command. A Stop that blocks on tasks N and M emits ONE
message naming both; a `clawgatectl task comment N` landing between the two fires
says nothing about M. Binding per id is necessary; it is not sufficient for any
shape where a command writes back without naming the id it serves, and no such shape
was found in this corpus.

🔴 EVIDENCE IS COUNTED ONLY FROM ASSISTANT-ISSUED `tool_use` BLOCKS, never from raw
transcript text. The guard's own blocking message contains the literal string
`clawgatectl task comment`, so a text scan matches the hook talking to itself and
reports ~100% compliance. Not hypothetical -- it happened.

Emits ids, counts, timestamps and booleans only; no transcript content, no file
names, no project paths. Keep it that way: this repo is PUBLIC.
"""
import collections
import json
import os
import re

ROOT = os.path.expanduser("~/.claude/projects")

IDS = re.compile(r"clawgate write-back MISSING for task (\d+)")
COMMENT = re.compile(r"clawgatectl\s+task\s+comment\s+(\d+)")
STATUS = re.compile(r"clawgatectl\s+task\s+status\s+(\d+)\s+(\w+)")
API = re.compile(r"/api/tasks/(\d+)/comments")
# `for n in 434 433 430; do clawgatectl task comment $n ...` -- a real shape in the
# corpus, and the one that decides task 430. A per-id regex alone misses it.
LOOP = re.compile(r"for\s+\w+\s+in\s+([\d\s]+);")

TERMINAL = ("ready_for_review", "complete")


def wrote_back(recs, task_id):
    """Did an assistant tool_use write back for THIS task id?"""
    for r in recs:
        if r.get("type") != "assistant":
            continue
        msg = r.get("message")
        if not isinstance(msg, dict):
            continue
        for b in msg.get("content") or []:
            if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                continue
            if (b.get("name") or "") != "Bash":
                continue
            inp = b.get("input")
            cmd = str(inp.get("command", "")) if isinstance(inp, dict) else ""
            if any(g == task_id for g in COMMENT.findall(cmd)):
                return True
            if any(g == task_id for g in API.findall(cmd)):
                return True
            if any(g == task_id and s in TERMINAL for g, s in STATUS.findall(cmd)):
                return True
            if "clawgatectl" in cmd and "comment" in cmd:
                for grp in LOOP.findall(cmd):
                    if task_id in grp.split():
                        return True
    return False


def main():
    ladders = collections.defaultdict(list)
    store = {}
    widths = collections.Counter()
    stamps = []

    for dirpath, _dirs, files in os.walk(ROOT):
        for fn in files:
            if not fn.endswith(".jsonl"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                blob = open(path, "rb").read()
            except OSError:
                continue
            if b"clawgate write-back MISSING" not in blob:
                continue
            recs = []
            for raw in blob.split(b"\n"):
                if raw.strip():
                    try:
                        recs.append(json.loads(raw))
                    except ValueError:
                        pass
            store[path] = recs
            for i, o in enumerate(recs):
                a = o.get("attachment")
                if not isinstance(a, dict) or a.get("type") != "hook_blocking_error":
                    continue
                be = a.get("blockingError")
                txt = (be.get("blockingError") if isinstance(be, dict) else str(be)) or ""
                ids = sorted(set(IDS.findall(txt)))
                if not ids:
                    continue
                widths[len(ids)] += 1
                if o.get("timestamp"):
                    stamps.append(o["timestamp"])
                for tid in ids:            # EVERY id, not just the first
                    ladders[(path, tid)].append(i)

    rearm, legit = [], []
    for (path, tid), idx in ladders.items():
        if len(idx) < 2:
            continue
        recs = store[path]
        between = recs[idx[0] + 1:idx[1]]
        after = recs[idx[1] + 1:]
        row = (tid, recs[idx[1]].get("timestamp"), wrote_back(after, tid))
        (rearm if wrote_back(between, tid) else legit).append(row)

    blocking = sum(widths.values())
    multi = sum(v for k, v in widths.items() if k > 1)
    n = len(rearm) + len(legit)
    print(f"corpus: {ROOT}  (the host this ran on -- NOT both hosts)")
    if stamps:
        print(f"  blocking errors span {min(stamps)} .. {max(stamps)}")
    print(f"clawgate blocking errors: {blocking}  (naming >1 task id: {multi})")
    print(f"  ids per blocking error: {dict(sorted(widths.items()))}")
    print(f"fire-2 ladders: {n}   unit = (transcript file, task id)")
    if n:
        print(f"  RE-ARM     : {len(rearm)} ({len(rearm) / n * 100:.1f}%)")
        print(f"  LEGITIMATE : {len(legit)} ({len(legit) / n * 100:.1f}%)")
    for tid, when, after in sorted(legit, key=lambda r: r[1] or ""):
        print(f"    task {tid}: fire 2 at {when}, write-back after fire 2 = {after}")
    won = sum(1 for _t, _w, a in legit if a)
    print(f"  rung 2 produced a write-back in {won} of {len(legit)} legitimate firings")
    # Positive/negative control: the classifier must return BOTH buckets. If either is
    # 0, it is degenerate and the split means nothing.
    print(f"  controls: rearm={len(rearm)} legit={len(legit)} "
          f"-> {'usable' if rearm and legit else 'DEGENERATE, do not quote'}")


if __name__ == "__main__":
    main()
