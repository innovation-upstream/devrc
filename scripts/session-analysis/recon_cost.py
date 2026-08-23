#!/usr/bin/env python3
"""What does one `/analyze-service` recon COST, and did a change move it?

WHY THIS EXISTS
---------------
`/analyze-service` recon was hand-run shell, and "it feels slow" is not a claim
you can act on or verify. This measures it, from the transcripts the sessions
already leave behind, so the before/after of any change to that skill is a
re-runnable number rather than an impression.

The measurement that motivated the change (n=20, by the one-off harness this
replaces; the provenance of both runs is in
`claudedocs/analyze-service-baseline/README.md`, and the machine baseline
`--compare` reads is `BASELINE.json` beside it, n=22):

    median  39.5 assistant turns · 22.5 tool calls · 35.5 KB tool output (~9.1k tok)
    p90     ~15.4k tok            max  91 KB / 62 calls
    shape   359 Bash calls, mean 1.5 KB, largest single result 15 KB

That last line is the finding that decided the fix: there was no fat dump to
trim, so the lever was collapsing round trips, not truncating output.

🔴 IT NEVER EMITS CAPTURED TEXT. devrc is PUBLIC and these transcripts are the
operator's own prompts, a client's infrastructure and a model's summaries of
both. So this tool's output — text AND `--json` — is NUMBERS, CATEGORY NAMES and
TOOL NAMES only. It never prints a command string, a file path, a message body,
a session id or a repo name, and `TestNoCapturedTextEscapes` asserts that
against a fixture built from strings that would be unmistakable if they leaked.
The reference harnesses this replaces DID print the invoking command line; that
is the one behaviour deliberately not carried over.

🔴 A ZERO IS NEVER PRINTED AS A RESULT. "0 invocations" is what a broken matcher
and an unused command look like alike, so finding none is `EXIT_NO_SAMPLE` with
a message saying the INSTRUMENT failed — never a clean report of nothing. The
same discipline the module measures is the one it is written under.

WHAT A "RECON WINDOW" IS
------------------------
From the `/analyze-service` invocation turn to the next GENUINE human turn.
Three things make that boundary non-obvious, and all three were wrong in the
first version of this measurement:

  * A slash command is followed by its own EXPANSION as a `user` turn. Skipped —
    it is the harness talking, not the operator.
  * `tool_result`s arrive as `user` turns. They are the thing being MEASURED, so
    they must not close the window.
  * Skill bodies, system reminders, task notifications and local-command stdout
    all arrive as `user` turns too (`SYNTHETIC_MARKERS`). None closes it.

`agent-*.jsonl` sidecars are subagent transcripts, not the main session, and are
skipped: counting them would double-count the work a dispatched Explore did.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import statistics as st
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

__all__ = [
    "DEFAULT_COMMAND",
    "SYNTHETIC_MARKERS",
    "CATEGORIES",
    "EXIT_OK",
    "EXIT_NO_SAMPLE",
    "EXIT_USAGE",
    "Window",
    "Report",
    "text_of",
    "is_tool_result",
    "is_synthetic",
    "classify",
    "invocation_marker",
    "windows_in",
    "measure",
    "report_json",
    "render",
    "compare",
    "main",
]

DEFAULT_COMMAND = "analyze-service"

EXIT_OK = 0
EXIT_USAGE = 2
#: 🔴 NOT "there were none". It means the instrument located no sample, which is
#: what a wrong command name, a moved transcript directory and a genuinely
#: unused command all look like. A caller must never read it as a measurement.
EXIT_NO_SAMPLE = 3

#: A `user` turn carrying any of these is the HARNESS, not the operator, and does
#: not close a recon window. Extending this list makes windows LONGER, so a new
#: marker is a measurement change: say so when you add one.
SYNTHETIC_MARKERS: tuple[str, ...] = (
    "<command-message>", "<command-name>", "<command-args>",
    "Base directory for this skill:", "<system-reminder>",
    "<task-notification>", "<local-command-stdout>",
    "Launching skill:", "ARGUMENTS:",
)

#: (category, pattern) in PRIORITY ORDER — the first match wins, so the more
#: specific `kubectl` verbs must precede `kubectl:other`, and `git log` must
#: precede `git:other`. Order is the whole contract here; a dict would lose it.
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("kubectl:get-workloads", r"\bkubectl\b.*\bget\s+(po|pods|deploy|sts|svc|all)\b"),
    ("kubectl:events",        r"\bkubectl\b.*\bevents\b"),
    ("kubectl:logs",          r"\bkubectl\b.*\blogs\b"),
    ("kubectl:describe/yaml", r"\bkubectl\b.*\b(describe|get .* -o (yaml|json))\b"),
    ("kubectl:other",         r"\bkubectl\b"),
    ("flux",                  r"\bflux\b"),
    ("git:log",               r"\bgit\s+log\b"),
    ("git:other",             r"\bgit\b"),
    ("search:grep/rg",        r"\b(rg|grep)\b"),
    ("search:find/ls",        r"\b(find|ls|fd)\b"),
    ("read:cat/head/sed",     r"\b(cat|head|sed -n|tail|awk)\b"),
    ("gh",                    r"\bgh\b"),
    ("probe:net/db",          r"\b(curl|psql|redis-cli)\b"),
)

_COMPILED = tuple((name, re.compile(pat)) for name, pat in CATEGORIES)

#: Bytes per token, for the estimate only. Stated as a constant so the estimate
#: is auditable rather than a number that appeared in a sentence.
BYTES_PER_TOKEN = 4


# --- Transcript primitives -----------------------------------------------------


def text_of(msg: dict) -> str:
    c = (msg.get("message") or {}).get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(
            b.get("text", "") for b in c
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def is_tool_result(msg: dict) -> bool:
    c = (msg.get("message") or {}).get("content")
    return isinstance(c, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in c
    )


def is_synthetic(msg: dict) -> bool:
    """Is this `user` turn the HARNESS rather than the operator?

    🔴 A tool_result is synthetic by this definition even though it is also the
    thing being measured — the caller reads the bytes off it FIRST and then
    continues the window. Conflating "does not close the window" with "is not
    interesting" is how the first version measured zero-byte windows.
    """
    if is_tool_result(msg):
        return True
    t = text_of(msg)
    if not t.strip():
        return True
    return any(mk in t for mk in SYNTHETIC_MARKERS)


def classify(command: str) -> str:
    """Which recon bucket does one Bash command line fall in? PURE, no I/O."""
    c = " ".join(command.split())
    for name, rx in _COMPILED:
        if rx.search(c):
            return name
    return "other"


def invocation_marker(command: str) -> str:
    return f"<command-name>/{command}</command-name>"


# --- The window ----------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    """One recon window's cost. NUMBERS AND CATEGORY NAMES ONLY — see the module
    docstring on why no identifier or command string is carried here."""

    assistant_turns: int = 0
    tool_calls: int = 0
    result_bytes: int = 0
    thinking_bytes: int = 0
    text_bytes: int = 0
    closed_by_human: bool = False
    tools: dict[str, int] = field(default_factory=dict)
    bash_categories: dict[str, int] = field(default_factory=dict)
    bash_category_bytes: dict[str, int] = field(default_factory=dict)
    largest_result_bytes: int = 0

    @property
    def result_kb(self) -> float:
        return self.result_bytes / 1024


def _iter_transcripts(projects: Path) -> Iterable[Path]:
    for p in sorted(glob.glob(str(projects / "**" / "*.jsonl"), recursive=True)):
        path = Path(p)
        # Subagent sidecars: their work is already inside the parent's window as
        # ONE tool call, so counting them too would double-count a dispatch.
        if path.name.startswith("agent-"):
            continue
        yield path


def _load(path: Path) -> list[dict]:
    msgs: list[dict] = []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return msgs
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msgs.append(json.loads(line))
        except ValueError:
            continue
    return msgs


def windows_in(msgs: Sequence[dict], command: str = DEFAULT_COMMAND) -> list[Window]:
    """Every recon window in ONE transcript. Pure over an already-parsed list."""
    marker = invocation_marker(command)
    out: list[Window] = []
    for i, m in enumerate(msgs):
        if m.get("type") != "user":
            continue
        t = text_of(m)
        if marker not in t and not t.strip().startswith(f"/{command}"):
            continue

        tools: Counter[str] = Counter()
        cats: Counter[str] = Counter()
        catbytes: Counter[str] = Counter()
        pending: dict[str, tuple[str, str]] = {}
        asst = think = text = res = largest = 0
        closed = False
        skip_expansion = True

        for m2 in msgs[i + 1:]:
            ty = m2.get("type")
            if ty == "user":
                if skip_expansion:
                    skip_expansion = False
                    continue
                if is_synthetic(m2):
                    if is_tool_result(m2):
                        for b in (m2.get("message") or {}).get("content") or []:
                            if not isinstance(b, dict) or b.get("type") != "tool_result":
                                continue
                            size = len(json.dumps(b.get("content", "")))
                            res += size
                            largest = max(largest, size)
                            hit = pending.pop(b.get("tool_use_id"), None)
                            if hit and hit[0] == "Bash":
                                cat = classify(hit[1])
                                cats[cat] += 1
                                catbytes[cat] += size
                    continue
                closed = True
                break
            if ty != "assistant":
                continue
            asst += 1
            for b in (m2.get("message") or {}).get("content") or []:
                if not isinstance(b, dict):
                    continue
                k = b.get("type")
                if k == "tool_use":
                    name = b.get("name", "?")
                    tools[name] += 1
                    inp = b.get("input") or {}
                    pending[b.get("id")] = (name, str(inp.get("command", "")))
                elif k == "thinking":
                    think += len(b.get("thinking", ""))
                elif k == "text":
                    text += len(b.get("text", ""))

        out.append(Window(
            assistant_turns=asst, tool_calls=sum(tools.values()), result_bytes=res,
            thinking_bytes=think, text_bytes=text, closed_by_human=closed,
            tools=dict(tools), bash_categories=dict(cats),
            bash_category_bytes=dict(catbytes), largest_result_bytes=largest,
        ))
    return out


# --- The report ----------------------------------------------------------------


@dataclass(frozen=True)
class Report:
    command: str
    windows: tuple[Window, ...]
    transcripts_read: int = 0

    @property
    def n(self) -> int:
        return len(self.windows)

    def _median(self, attr: str) -> float:
        return round(st.median(getattr(w, attr) for w in self.windows), 1)

    @property
    def closed_by_human(self) -> int:
        return sum(1 for w in self.windows if w.closed_by_human)

    @property
    def median_result_kb(self) -> float:
        return round(st.median(w.result_kb for w in self.windows), 1)

    @property
    def mean_result_kb(self) -> float:
        return round(st.mean(w.result_kb for w in self.windows), 1)

    @property
    def p90_result_kb(self) -> float:
        """The `int(n * 0.9)`-th smallest window, keeping the reference harness's
        definition so this baseline stays comparable to the recorded one.

        🔴 THE CLAMP IS DEFENSIVE AND CANNOT CURRENTLY FIRE — stated because an
        earlier version of this docstring claimed it fixed an IndexError "at n=10
        and every multiple of 10", and that was FALSE: `int(n * 0.9) < n` for
        every n ≥ 1 (verified over n = 1..2000, zero out-of-range). A mutation
        that removed the clamp SURVIVED the suite, which is how the false claim
        was found. Do not re-derive it.

        The clamp stays because it is free and the percentile is a constant
        someone will eventually raise: at 1.0 the bare index IS off the end.
        """
        vals = sorted(w.result_kb for w in self.windows)
        return round(vals[min(len(vals) - 1, int(len(vals) * 0.9))], 1)

    @property
    def max_result_kb(self) -> float:
        return round(max(w.result_kb for w in self.windows), 1)

    def tokens(self, kb: float) -> int:
        return round(kb * 1024 / BYTES_PER_TOKEN)

    @property
    def tool_mix(self) -> dict[str, int]:
        agg: Counter[str] = Counter()
        for w in self.windows:
            agg.update(w.tools)
        return dict(agg.most_common())

    @property
    def bash_mix(self) -> dict[str, dict[str, float]]:
        calls: Counter[str] = Counter()
        kb: Counter[str] = Counter()
        for w in self.windows:
            calls.update(w.bash_categories)
            for k, v in w.bash_category_bytes.items():
                kb[k] += v
        return {
            name: {"calls": n, "kb": round(kb[name] / 1024, 1),
                   "kb_per_call": round(kb[name] / 1024 / n, 2)}
            for name, n in calls.most_common()
        }


def measure(projects: str | Path, command: str = DEFAULT_COMMAND) -> Report:
    """Walk the transcript tree and measure every window. READ-ONLY."""
    root = Path(projects)
    wins: list[Window] = []
    read = 0
    for path in _iter_transcripts(root):
        read += 1
        wins.extend(windows_in(_load(path), command))
    return Report(command=command, windows=tuple(wins), transcripts_read=read)


def report_json(rep: Report) -> dict:
    """🔴 NUMBERS, CATEGORY NAMES AND TOOL NAMES. Nothing else may enter here."""
    return {
        "command": rep.command,
        "n": rep.n,
        "transcripts_read": rep.transcripts_read,
        "closed_by_human": rep.closed_by_human,
        "median": {
            "assistant_turns": rep._median("assistant_turns"),
            "tool_calls": rep._median("tool_calls"),
            "result_kb": rep.median_result_kb,
            "thinking_kb": round(st.median(w.thinking_bytes / 1024 for w in rep.windows), 1),
            "text_kb": round(st.median(w.text_bytes / 1024 for w in rep.windows), 1),
        },
        "mean_result_kb": rep.mean_result_kb,
        "p90_result_kb": rep.p90_result_kb,
        "max_result_kb": rep.max_result_kb,
        "max_tool_calls": max(w.tool_calls for w in rep.windows),
        "largest_single_result_kb": round(
            max(w.largest_result_bytes for w in rep.windows) / 1024, 1),
        "est_tokens": {
            "median": rep.tokens(rep.median_result_kb),
            "p90": rep.tokens(rep.p90_result_kb),
        },
        "tool_mix": rep.tool_mix,
        "bash_mix": rep.bash_mix,
    }


def render(rep: Report) -> str:
    j = report_json(rep)
    L = [
        f"POSITIVE CONTROL: {rep.n} real /{rep.command} invocation(s) located "
        f"in {rep.transcripts_read} transcript(s)",
        f"  (window closed by a real human turn in {rep.closed_by_human}/{rep.n})",
        "",
        f"MEDIAN  asst_turns={j['median']['assistant_turns']}  "
        f"tool_calls={j['median']['tool_calls']}  res_KB={j['median']['result_kb']}  "
        f"think_KB={j['median']['thinking_kb']}  out_KB={j['median']['text_kb']}",
        f"MEAN    res_KB={j['mean_result_kb']}",
        f"MAX     res_KB={j['max_result_kb']}  tools={j['max_tool_calls']}  "
        f"largest_single_result_KB={j['largest_single_result_kb']}",
        f"Est. recon context cost: median ~{j['median']['result_kb']} KB tool output "
        f"≈ {j['est_tokens']['median'] / 1000:.1f}k tokens, "
        f"p90 ≈ {j['est_tokens']['p90'] / 1000:.1f}k tokens",
        "",
        f"{'category':26}{'calls':>6}{'KB':>8}{'KB/call':>9}",
    ]
    for name, row in j["bash_mix"].items():
        L.append(f"{name:26}{int(row['calls']):>6}{row['kb']:>8.0f}{row['kb_per_call']:>9.1f}")
    L += ["", "TOOL MIX (real recon windows only):"]
    total = sum(j["tool_mix"].values()) or 1
    for name, n in j["tool_mix"].items():
        L.append(f"  {name:28} {n:5}  ({round(100 * n / total)}%)")
    return "\n".join(L) + "\n"


def compare(baseline: dict, now: dict) -> str:
    """Before/after on the fields that decide whether recon got cheaper."""
    rows = [
        ("n (sample size)", "n", 0),
        ("median tool calls", ("median", "tool_calls"), 1),
        ("median assistant turns", ("median", "assistant_turns"), 1),
        ("median tool output KB", ("median", "result_kb"), 1),
        ("p90 tool output KB", "p90_result_kb", 1),
        ("max tool output KB", "max_result_kb", 1),
    ]

    def get(d: dict, key):
        return d[key[0]][key[1]] if isinstance(key, tuple) else d.get(key)

    L = [f"{'metric':26}{'baseline':>12}{'now':>12}{'delta':>12}{'':>4}"]
    for label, key, nd in rows:
        b, n = get(baseline, key), get(now, key)
        if b is None or n is None:
            L.append(f"{label:26}{'—':>12}{'—':>12}{'NOT COMPARED':>16}")
            continue
        d = n - b
        arrow = "↓" if d < 0 else ("↑" if d > 0 else "=")
        L.append(f"{label:26}{b:>12.{nd}f}{n:>12.{nd}f}{d:>+12.{nd}f}  {arrow}")
    L.append("")
    L.append("⚠ A comparison is only about the windows BOTH sides measured. A post-change "
             "sample of 0 is NOT an improvement — it is no measurement.")
    return "\n".join(L) + "\n"


# --- CLI -----------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="recon_cost.py",
        description="Measure what one /analyze-service recon costs, from transcripts.",
    )
    p.add_argument("--command", default=DEFAULT_COMMAND,
                   help=f"slash command to measure (default: {DEFAULT_COMMAND})")
    p.add_argument("--projects", default=str(Path.home() / ".claude" / "projects"),
                   help="transcript root")
    p.add_argument("--json", action="store_true", help="machine-readable report")
    p.add_argument("--compare", default=None, metavar="BASELINE.json",
                   help="print a before/after table against a recorded baseline")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    projects = Path(args.projects).expanduser()
    if not projects.is_dir():
        print(f"recon_cost: transcript root does not exist: {projects}", file=sys.stderr)
        return EXIT_USAGE

    rep = measure(projects, args.command)
    if rep.n == 0:
        print(
            f"recon_cost: NO /{args.command} INVOCATION FOUND in {rep.transcripts_read} "
            f"transcript(s) under {projects}.\n"
            f"  This is an INSTRUMENT FAILURE, not a measurement of zero cost: a wrong\n"
            f"  --command, a moved transcript root and a genuinely unused command all\n"
            f"  look exactly like this. Nothing is reported.",
            file=sys.stderr,
        )
        return EXIT_NO_SAMPLE

    now = report_json(rep)
    if args.compare:
        try:
            baseline = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"recon_cost: baseline unreadable: {exc}", file=sys.stderr)
            return EXIT_USAGE
        sys.stdout.write(compare(baseline, now))
        return EXIT_OK

    if args.json:
        print(json.dumps(now, indent=2))
    else:
        sys.stdout.write(render(rep))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
