#!/usr/bin/env python3
"""Analyze Claude Code session transcripts for one repo and surface
patterns relevant to improving CLAUDE.md, skills, and token efficiency.

Usage:
  analyze.py [--cwd PATH] [--project-dir DIR] [--days N] [--top N]

Defaults: --cwd $PWD, --days 14, --top 30. Either resolve the transcript
directory from the repo path (default) or point at it explicitly.
"""
import argparse, glob, json, os, re, sys, time
from collections import Counter

def resolve_project_dir(cwd):
    """Map a repo path to its ~/.claude/projects/<encoded> transcript dir.
    Claude Code encodes the abspath by replacing path separators with '-'."""
    base = os.path.expanduser("~/.claude/projects")
    ap = os.path.abspath(cwd)
    candidates = [
        "-" + ap.strip("/").replace("/", "-"),          # /a/b -> -a-b
        "-" + re.sub(r"[/.]", "-", ap.strip("/")),       # also collapse dots
    ]
    for c in candidates:
        d = os.path.join(base, c)
        if os.path.isdir(d):
            return d
    # fuzzy: a projects subdir ending in the repo basename
    bn = os.path.basename(ap)
    for d in sorted(glob.glob(os.path.join(base, "*" + bn))):
        return d
    return None

def iter_files(project_dir, days):
    cutoff = time.time() - days * 86400
    files = glob.glob(project_dir + "/*.jsonl") + glob.glob(project_dir + "/**/*.jsonl", recursive=True)
    return [f for f in sorted(set(files)) if os.path.getmtime(f) >= cutoff]

def as_text(content):
    if isinstance(content, list):
        return " ".join(x.get("text", "") for x in content if isinstance(x, dict))
    return str(content)

SKIP_PROMPT = re.compile(r"^(Warmup|Tool loaded\.|\[Request interrupted|This session is being continued|Your task is to create a detailed summary)")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cwd", default=os.getcwd())
    p.add_argument("--project-dir", default=None)
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--top", type=int, default=30)
    a = p.parse_args()

    pdir = a.project_dir or resolve_project_dir(a.cwd)
    if not pdir or not os.path.isdir(pdir):
        print(f"ERROR: no transcript dir for {a.cwd!r}. Pass --project-dir.", file=sys.stderr)
        print("Available:", file=sys.stderr)
        for d in sorted(glob.glob(os.path.expanduser("~/.claude/projects/*"))):
            print("  " + os.path.basename(d), file=sys.stderr)
        sys.exit(2)

    files = iter_files(pdir, a.days)
    if not files:
        print(f"No transcripts in the last {a.days} days under {pdir}")
        sys.exit(0)

    tools = Counter(); reads = Counter(); fetches = Counter(); bashcat = Counter()
    edits = Counter(); errors = []; prompts = []; big = []
    total_result_chars = 0
    BASH_VERBS = ["tmux","git","jq","grep","rg","cat","ls","find","sed","head","tail",
                  "echo","python3","go","home-manager","nixos-rebuild","sudo","pgrep",
                  "wc","ps","curl","i3-msg","xdotool","flameshot","kubectl","docker","npm","make"]

    for f in files:
        with open(f, errors="ignore") as fh:
            for line in fh:
                try: o = json.loads(line)
                except Exception: continue
                msg = o.get("message", {})
                content = msg.get("content")
                if msg.get("role") == "user" and isinstance(content, str):
                    t = content.strip()
                    if t and not t.startswith("<") and "tool_result" not in t and not SKIP_PROMPT.match(t):
                        prompts.append(t[:200])
                if not isinstance(content, list): continue
                for c in content:
                    if not isinstance(c, dict): continue
                    ct = c.get("type")
                    if ct == "text" and msg.get("role") == "user":
                        t = c.get("text", "").strip()
                        if t and not t.startswith("<") and not SKIP_PROMPT.match(t):
                            prompts.append(t[:200])
                    elif ct == "tool_use":
                        n = c.get("name", "?"); i = c.get("input", {})
                        tools[n] += 1
                        if n == "Read": reads[i.get("file_path", "?")] += 1
                        elif n in ("Edit", "Write", "MultiEdit"):
                            edits[n + ":" + os.path.basename(i.get("file_path", "?"))] += 1
                        elif n == "WebFetch": fetches[i.get("url", "?")] += 1
                        elif n == "Bash":
                            toks = i.get("command", "").split()
                            key = "?"
                            if toks:
                                key = toks[0]
                                for kw in BASH_VERBS:
                                    if kw in toks[:2]: key = kw; break
                            bashcat[key] += 1
                    elif ct == "tool_result":
                        txt = as_text(c.get("content", "")); n = len(txt)
                        total_result_chars += n
                        if c.get("is_error"):
                            e = txt.replace("\n", " ").strip()
                            if e and e != "Warmup": errors.append(e[:200])
                        if n > 8000: big.append((n, txt[:90].replace("\n", " ")))

    def section(title): print(f"\n{'='*4} {title} {'='*4}")
    print(f"PROJECT DIR : {pdir}")
    print(f"WINDOW      : last {a.days} days · {len(files)} transcript files")
    print(f"TOOL RESULT : {total_result_chars:,} chars (~{total_result_chars//4:,} tokens)")

    section("TOOL USAGE")
    for k, v in tools.most_common(): print(f"  {v:5d}  {k}")

    section(f"RE-READ FILES (>1x = candidate for CLAUDE.md layout map)")
    for k, v in reads.most_common():
        if v > 1: print(f"  {v:3d}x  {k}")

    section("RE-FETCHED URLs (>1x = cache the fact in CLAUDE.md)")
    any_ref = False
    for k, v in fetches.most_common(a.top):
        if v > 1: print(f"  {v:3d}x  {k[:100]}"); any_ref = True
    if not any_ref: print("  (none repeated)")

    section(f"OVERSIZED tool_results (>8k chars): {len(big)}")
    for n, s in sorted(big, reverse=True)[:15]: print(f"  {n:>8,}  {s!r}")

    section(f"ERRORS / REJECTIONS: {len(errors)}")
    seen = set()
    for e in errors:
        sig = e[:60]
        if sig in seen: continue
        seen.add(sig); print("  -", e[:160])

    section("BASH VERB FREQUENCY (allowlist candidates if read-only & frequent)")
    for k, v in bashcat.most_common(a.top): print(f"  {v:5d}  {k}")

    section(f"EDIT/WRITE TARGETS")
    for k, v in edits.most_common(15): print(f"  {v:3d}  {k}")

    section(f"USER PROMPTS (deduped, recurring themes -> skill candidates): {len(prompts)}")
    seen = set()
    for u in prompts:
        sig = u[:50].lower()
        if sig in seen: continue
        seen.add(sig); print("  •", u)

if __name__ == "__main__":
    main()
