#!/usr/bin/env python3
"""Extract the FIRST genuine user-typed message per session transcript."""
import json, os, sys, glob, re

ROOT = os.path.expanduser("~/.claude/projects")
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/genesis.jsonl"
SYS_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
COMMAND_STDOUT = re.compile(r"<local-command-stdout>.*?</local-command-stdout>", re.S)
COMMAND_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.S)
COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)
HARNESS = ("<task-notification>", "<task-reminder>", "<post-tool-use", "<bash-",
           "<user-prompt-submit", "<local-command-stdout>")

def clean(t):
    t = SYS_REMINDER.sub("", t)
    t = COMMAND_STDOUT.sub("", t)
    return t.strip()

def first_text(content):
    """Return (kind, text) of first usable user block, or None."""
    if isinstance(content, str):
        return ("typed", content)
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                return ("typed", b.get("text", ""))
    return None

def main():
    n = 0
    with open(OUT, "w") as fout:
        for path in glob.glob(os.path.join(ROOT, "**", "*.jsonl"), recursive=True):
            project = os.path.basename(os.path.dirname(path))
            if project == "subagents" or project.startswith("wf_"):
                continue
            try:
                with open(path, errors="replace") as f:
                    lines = f.readlines()
            except Exception:
                continue
            ts = None
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("type") != "user" or obj.get("isMeta") or obj.get("isSidechain"):
                    continue
                msg = obj.get("message") or {}
                if msg.get("role") != "user":
                    continue
                ft = first_text(msg.get("content"))
                if not ft:
                    continue
                kind, raw = ft
                if not raw:
                    continue
                cmd = COMMAND_NAME.search(raw)
                if cmd:
                    cargs = COMMAND_ARGS.search(raw)
                    txt = ("/" + cmd.group(1).strip() + " " + (cargs.group(1).strip() if cargs else "")).strip()
                    kind = "command"
                else:
                    txt = clean(raw)
                    if not txt or txt.lstrip().startswith(HARNESS):
                        continue
                    if txt.startswith("[Request interrupted") or txt.startswith("Caveat: The messages below"):
                        continue
                # first usable message for this session
                ts = obj.get("timestamp")
                rec = {"project": project, "session": os.path.basename(path)[:8],
                       "ts": ts, "kind": kind, "text": txt}
                fout.write(json.dumps(rec) + "\n")
                n += 1
                break  # only first per file
    print(f"sessions_with_first_msg={n} out={OUT}")

if __name__ == "__main__":
    main()
