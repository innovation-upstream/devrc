#!/usr/bin/env python3
"""Deterministic audit of a Claude Code project's auto-memory index (MEMORY.md).

Reports size vs budget, per-section byte-weights + bullet counts vs caps,
archive candidates (shipped Active-work), fat bullets (trim candidates), and
link integrity. Pure measurement — makes NO edits. The /prune-memory command
runs this, then an agent applies the judgment cut.

Usage:
  memory-audit.py [MEMORY_DIR_or_MEMORY.md]

With no arg, derives the memory dir from $PWD using Claude Code's project-slug
convention (~/.claude/projects/<cwd-with-slashes-as-dashes>/memory/).
"""
import os
import re
import sys

TARGET = 12_000       # soft target bytes
HARD = 24_000         # harness hard load cap (content past it is dropped)
SECTION_CAPS = {      # bullet-count soft caps for the re-bloating sections
    "Critical safety": 10,
    "Feedback": 18,
}
FAT_BYTES = 250       # a bullet over this is a trim candidate (want <=140 char tells)
SHIPPED = re.compile(r"SHIPPED|VERIFIED|COMPLETE|MERGED|DEPLOYED|RESOLVED|CLOSED|✅")


def resolve_mem_dir(arg):
    if arg:
        p = os.path.abspath(os.path.expanduser(arg))
        if p.endswith(".md"):
            p = os.path.dirname(p)
        return p
    slug = os.getcwd().replace("/", "-")
    return os.path.expanduser(f"~/.claude/projects/{slug}/memory")


def main():
    mem_dir = resolve_mem_dir(sys.argv[1] if len(sys.argv) > 1 else None)
    mem_md = os.path.join(mem_dir, "MEMORY.md")
    if not os.path.isfile(mem_md):
        sys.exit(f"no MEMORY.md at {mem_md}\n(pass the memory dir or MEMORY.md path explicitly)")

    text = open(mem_md).read()
    size = len(text.encode())
    lines = text.splitlines()

    # --- size verdict ---
    status = "OK" if size <= TARGET else ("WARN" if size < HARD else "OVER-HARD-CAP")
    print(f"# MEMORY.md audit — {mem_md}")
    print(f"\nsize: {size:,} B   target {TARGET:,}   hard {HARD:,}   → {status}")
    if size > TARGET:
        print(f"  ⚠ over target by {size - TARGET:,} B — prune {size - TARGET:,}+ B")

    # --- per-section weights + cap check ---
    print("\n## sections")
    sec = None
    sec_bytes = {}
    sec_bullets = {}
    order = []
    bullets = []  # (section, byte_len, text)
    for ln in lines:
        if ln.startswith("## "):
            sec = ln[3:].strip()
            order.append(sec)
            sec_bytes[sec] = 0
            sec_bullets[sec] = 0
        if sec is not None:
            sec_bytes[sec] += len(ln.encode()) + 1
            if ln.startswith("- "):
                sec_bullets[sec] += 1
                bullets.append((sec, len(ln.encode()), ln))
    for s in order:
        cap = next((c for k, c in SECTION_CAPS.items() if k.lower() in s.lower()), None)
        n = sec_bullets[s]
        flag = ""
        if cap is not None:
            flag = f"  cap {cap}" + ("  ❌OVER" if n > cap else "  ✓")
        print(f"  {s[:46]:46}  {sec_bytes[s]:5,} B  {n:2} bullets{flag}")

    # --- archive candidates: shipped-marked Active-work bullets ---
    print("\n## archive candidates (Active-work bullets marked shipped/done)")
    got = False
    for s, blen, bt in bullets:
        if "active work" in s.lower() and SHIPPED.search(bt):
            got = True
            m = re.search(r"\]\(([^)]+)\)", bt)
            print(f"  • {m.group(1) if m else bt[:60]}  ({blen} B)")
    if not got:
        print("  (none — Active-work is clean)")

    # --- fat bullets: trim to <=140-char tells ---
    print(f"\n## fat bullets (> {FAT_BYTES} B — trim to a <=140-char tell, detail to topic file)")
    fat = sorted([b for b in bullets if b[1] > FAT_BYTES], key=lambda b: -b[1])
    if fat:
        for s, blen, bt in fat:
            m = re.search(r"\]\(([^)]+)\)", bt)
            print(f"  • {blen:4} B  [{s[:20]}]  {m.group(1) if m else bt[:50]}")
    else:
        print("  (none)")

    # --- link integrity ---
    print("\n## link integrity")
    miss = []
    for m in re.findall(r"\]\(([A-Za-z0-9_./-]+\.md)\)", text):
        if not os.path.exists(os.path.join(mem_dir, m)):
            miss.append(m)
    for w in re.findall(r"\[\[([A-Za-z0-9_-]+)\]\]", text):
        if not os.path.exists(os.path.join(mem_dir, w + ".md")):
            miss.append(f"[[{w}]]")
    print("  " + ("MISSING: " + ", ".join(miss) if miss else "all links resolve ✓"))

    # --- one-line verdict ---
    over_caps = [s for s in order
                 for k, c in SECTION_CAPS.items()
                 if k.lower() in s.lower() and sec_bullets[s] > c]
    print("\n## verdict")
    if size <= TARGET and not over_caps and not miss:
        print("  ✓ within budget, caps, and links — no prune needed")
    else:
        need = []
        if size > TARGET:
            need.append(f"cut ~{size - TARGET:,} B")
        if over_caps:
            need.append("over-cap: " + ", ".join(over_caps))
        if miss:
            need.append(f"{len(miss)} broken links")
        print("  ⚠ prune needed — " + "; ".join(need))


if __name__ == "__main__":
    main()
