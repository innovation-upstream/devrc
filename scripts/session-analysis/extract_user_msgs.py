#!/usr/bin/env python3
"""Extract genuine user-typed messages from Claude Code JSONL transcripts."""
import json, os, sys, glob, re, hashlib

ROOT = os.path.expanduser("~/.claude/projects")
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/user_msgs.jsonl"

# patterns that mark non-typed / synthetic content
SYS_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
COMMAND_STDOUT = re.compile(r"<local-command-stdout>.*?</local-command-stdout>", re.S)
COMMAND_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.S)
COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)
TAG_BLOCK = re.compile(r"<command-message>.*?</command-message>", re.S)

def clean_text(t):
    t = SYS_REMINDER.sub("", t)
    t = COMMAND_STDOUT.sub("", t)
    return t.strip()

def extract_from_content(content):
    """Return list of (kind, text) where kind is 'typed' or 'command'."""
    out = []
    if isinstance(content, str):
        txt = content
        out.append(("typed", txt))
        return out
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                out.append(("typed", block.get("text", "")))
            # ignore tool_result, image, tool_use, thinking
    return out

def main():
    n_files = 0
    n_msgs = 0
    seen = set()
    with open(OUT, "w") as fout:
        for path in glob.glob(os.path.join(ROOT, "**", "*.jsonl"), recursive=True):
            project = os.path.basename(os.path.dirname(path))
            # skip synthetic agent transcript dirs
            if project == "subagents" or project.startswith("wf_"):
                continue
            n_files += 1
            try:
                with open(path, errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        if obj.get("type") != "user":
                            continue
                        if obj.get("isMeta"):
                            continue
                        # sidechain == subagent's own transcript, not user-typed
                        if obj.get("isSidechain"):
                            continue
                        msg = obj.get("message") or {}
                        if msg.get("role") != "user":
                            continue
                        content = msg.get("content")
                        for kind, raw in extract_from_content(content):
                            if not raw:
                                continue
                            # detect slash command
                            cmd = COMMAND_NAME.search(raw)
                            if cmd:
                                cname = cmd.group(1).strip()
                                cargs_m = COMMAND_ARGS.search(raw)
                                cargs = cargs_m.group(1).strip() if cargs_m else ""
                                rec = {"project": project, "kind": "command",
                                       "text": (cname + " " + cargs).strip()}
                                key = hashlib.md5(rec["text"].encode()).hexdigest()
                                if rec["text"] and key not in seen:
                                    seen.add(key)
                                    fout.write(json.dumps(rec) + "\n")
                                    n_msgs += 1
                                continue
                            txt = clean_text(raw)
                            # skip pure tool/stdout leftovers and tiny noise
                            if not txt:
                                continue
                            if txt.startswith("<") and txt.endswith(">") and len(txt) < 80:
                                continue
                            # skip interrupted/caveat boilerplate
                            if txt.startswith("[Request interrupted"):
                                continue
                            if txt.startswith("Caveat: The messages below"):
                                continue
                            if txt.startswith("API Error") or txt.startswith("API request failed"):
                                continue
                            rec = {"project": project, "kind": "typed", "text": txt}
                            # dedup exact repeats (same text pasted across sessions)
                            key = hashlib.md5((project + txt).encode()).hexdigest()
                            if key in seen:
                                continue
                            seen.add(key)
                            fout.write(json.dumps(rec) + "\n")
                            n_msgs += 1
            except Exception as e:
                print(f"ERR {path}: {e}", file=sys.stderr)
    print(f"files={n_files} msgs={n_msgs} out={OUT}")

if __name__ == "__main__":
    main()
