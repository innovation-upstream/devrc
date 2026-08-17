"""THE structural scan for CAPTURED FREE TEXT in committed JSON/JSONL.

WHY
---
`CLAUDE.md`: "This repo is PUBLIC." The IP half of that rule is
`scripts/testlib/public_ip_scan.py`; the hostname half is
`scripts/testlib/client_host_scan.py`. This module gates a THIRD class those two
structurally cannot see: **text that was captured from somewhere else and frozen
into a fixture** — message bodies, operator prompts, transcript excerpts, chat
content, and the model-written summaries OF those.

It exists because that class already shipped. `scripts/initiatives/dspy-eval/`
carried a frozen `initiative-scan --days 30 --json` capture: 1,452 string leaves,
of which 333 were `recent_messages[].text` message bodies (262 of them under a
single CLIENT repo key), 606 were sentence-length, and one was an email address.
It was added by #148 and then SURVIVED #350 — the PR whose whole subject was
scrubbing this repo of client disclosure — because #350's two gates look for IPs
and for hostnames, and a paragraph of captured prose is neither. A scrub of the
category is not a gate on the category.

WHAT IT LOOKS FOR — AND WHY NOT "ALL PROSE"
-------------------------------------------
🔴 STRUCTURAL, NOT SEMANTIC, ON PURPOSE. There is no reliable way to look at a
sentence and decide whether it was typed by the author or captured from someone
else, and a scan that tried would fire on this repo's ~200 markdown files of
legitimate prose. `claude/RULES.md` is explicit that a permanently-red gate is
worse than none: it trains people to click through.

So the finding is a CONJUNCTION of three structural facts, none of them about
what the text means:

    a JSON/JSONL file  ×  a MESSAGE-ISH KEY  ×  a value that is FREE TEXT

That is narrow in the dimension that carries the disclosure. Authored prose lives
in `.md` and in source comments; captured prose arrives as a serialized DUMP,
under the key the producing tool gave it. The dspy-eval capture is exactly that
shape, and so is every transcript, mailbox and chat export this repo could
plausibly grow.

MEASURED over the tree this was written against: 23 tracked JSON/JSONL files
total. The 5 dspy-eval fixtures were the only ones with a finding. The false-
positive surface is three browser-extension `manifest.json` `description` fields,
and `description` is EXCLUDED by name below rather than allowlisted — see
`_EXCLUDED_KEYS`.

WHY THE ALLOWLIST CANNOT BE KEYED ON THE VALUE
----------------------------------------------
🔴 The sibling gates key their exemptions on `(relpath, value)` — the IP or the
hostname is spelled in the allowlist. **This gate cannot do that**, because the
value IS the disclosure: writing it into a tracked source file to exempt it would
commit the very thing being exempted. So exemptions are keyed on
`(relpath, keypath)` and additionally pin the COUNT, which is what stops a pin
from being a rubber stamp that pre-approves whatever lands at that key next.

WHAT THIS DOES **NOT** CATCH
----------------------------
🔴 THIS LIST IS NOT EXHAUSTIVE AND MUST NOT BE READ AS IF IT WERE. Treat a clean
run as "no match for the shapes below", never as "no captured text here".

  * **Captured text in any format that is not JSON/JSONL** — the big one. A
    transcript pasted into a `.md`, a `.csv`/`.txt`/`.yaml` export, a heredoc in
    a shell script, or a Python list literal are all invisible here. In
    particular `claudedocs/the-algorithm-applied-2026-06-17.md` quotes operator
    request phrasings as prose and this scan does not see it (assessed: those are
    aggregated self-authored stems already published as this repo's own feature
    names, not third-party content).
  * **A message-ish key not in `MESSAGE_KEYS`** — a producer that calls the field
    `utterance`, `line`, `msg_body` or `description` walks straight through. The
    only real defence is adding keys as new producers appear.
  * **Free text SHORTER than `MIN_FREE_TEXT_CHARS`** — a 40-character message is
    still a message. The threshold buys the low false-positive rate that keeps
    this gate green, and that is a trade, not a proof.
  * **Free text with no whitespace** — deliberately not a finding, so a long
    path, hash, token or base64 blob under `text` stays quiet.
  * A value nested inside a JSON STRING (double-encoded JSON), which parses as
    one opaque leaf.
  * A file this scan skips: a binary suffix, or a tracked `.json` that is not
    valid UTF-8 or does not parse. Those are NOT silently dropped — see
    `unparseable_files`, which the gate pins at zero.
  * The value's own content: a client name, IP or hostname inside a string this
    scan does not flag is the other two gates' job.

It stops the accidental DUMP. It is not an exfiltration control, and — like both
siblings — it guards HEAD only. Git history still carries everything ever
committed, and rewriting it would not unpublish what has already been cloned.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

# 🔴 ONE RULE, ONE PLACE. The file-enumeration half — `git ls-files` with a
# filesystem fallback for the nix sandbox, and the skip-dir test taken RELATIVE
# to the root so an agent worktree under `.claude/worktrees/<id>/` does not skip
# the entire repo while reporting a confident zero — is already solved and
# already regression-pinned in `public_ip_scan`. A third copy of it would be a
# third place for that trap to come back. `test_no_captured_text.py` pins this
# seam so the delegation cannot rot into a divergent copy.
from testlib.public_ip_scan import repo_files  # noqa: E402

__all__ = ["MESSAGE_KEYS", "MIN_FREE_TEXT_CHARS", "is_free_text", "keypath_of",
           "scan_obj", "scan_file", "scan_repo", "repo_files",
           "unparseable_files", "SCANNED_SUFFIXES"]

#: Only these are read. Captured prose arrives SERIALIZED; authored prose does
#: not. Widening this to `.md` is the permanently-red gate described above.
SCANNED_SUFFIXES = frozenset({".json", ".jsonl"})

#: A string this long, containing whitespace, is a sentence rather than an
#: identifier. 60 is the threshold the dspy-eval exposure was MEASURED with
#: (606 of its 1,452 leaves cleared it); every one of its 333 captured message
#: bodies that mattered is prose well above it.
MIN_FREE_TEXT_CHARS = 60

#: 🔴 AN ENUMERATION, NOT A PATTERN, and each entry says which producer puts
#: captured text there. An unknown key is NOT a finding — that is this gate's
#: single biggest blind spot and it is named in the docstring rather than papered
#: over with a regex that would drag in every config field in the repo.
MESSAGE_KEYS: dict[str, str] = {
    # --- verbatim captured conversation -------------------------------------
    "recent_messages": "the exact key that carried 333 captured chat bodies in dspy-eval",
    "messages": "the generic plural; chat-completion payloads and every message dump",
    "message": "the singular of the same",
    "text": "the leaf INSIDE recent_messages[] — 114 sentence-length values there",
    "body": "an email or webhook body (the mailbox subsystem's shape)",
    "content": "the OpenAI/Anthropic chat-message field name",
    "prompt": "a captured operator prompt — the thing CLAUDE.md's new clause names",
    "transcript": "a session transcript excerpt",
    "chat": "a chat log under its most obvious key",
    "conversation": "the same, singular-noun spelling",
    "comment": "a PR/issue comment body, i.e. someone else's writing",
    "excerpt": "a quoted fragment of a larger captured document",
    "snippet": "the same, under the other common spelling",
    # --- model-written summaries OF captured text (second-order disclosure) --
    # These are in the set because dspy-eval's results-run{1,2,3}.json carried
    # 109 of them: a recap of a client's messages still describes the client's
    # work. Cheap to include — MEASURED zero false positives in this repo.
    "recap": "a generated recap of captured context (results-run*.json)",
    "summary": "the same; 112 of these in the dspy-eval capture",
    "next_step": "a generated next-step sentence derived from captured context",
    "open_investigations": "free-text investigation notes carried per initiative",
    "recent_commits": "real commit subjects from a client repo — captured, not authored",
}

#: 🔴 DELIBERATE EXCLUSIONS, spelled out so they are decisions rather than
#: oversights. Each of these is a key whose long values in THIS repo are authored
#: metadata, and including it would put the gate's only false positives into an
#: allowlist — which is how a scanner ends up pre-approving its own canonical
#: examples and then scanning clean over a real leak.
_EXCLUDED_KEYS: dict[str, str] = {
    "description": "3 browser-extension manifest.json fields — authored UI copy, not captured",
    "title": "short by nature; a long one is a document heading someone wrote",
    "path": "a filesystem path, covered by CLAUDE.md's media-path clause, not this one",
    "current_doc": "a doc PATH, same as above",
    "name": "an identifier; a long one is a package or derivation name, not prose",
    "reason": "generic; used by this repo's own allowlists and error strings",
}

#: A dict key is SCHEMA if it looks like a field name. Anything else is DATA —
#: `by_repo` was keyed by client repo PATH — and data must not be echoed into a
#: failure message or an allowlist entry, so it renders as `*`.
_SCHEMA_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,39}$")


def is_free_text(value: str) -> bool:
    """Long enough to be a sentence, and containing whitespace.

    The whitespace half is what keeps a 64-char hash, a long path, a token or a
    base64 blob under `text` from being a finding.
    """
    return len(value) >= MIN_FREE_TEXT_CHARS and any(c.isspace() for c in value)


def keypath_of(parts) -> str:
    """Render a walk into a stable, DATA-FREE key path (`by_repo.*[].text`).

    `[]` appends with no separator so a list reads as an index on the key it
    belongs to; every other part is dot-joined.
    """
    out = ""
    for p in parts:
        out += p if p == "[]" else (("." + p) if out else p)
    return out


def _key_token(key: str) -> str:
    return key if _SCHEMA_KEY.match(key) else "*"


def scan_obj(doc) -> dict[str, dict]:
    """`{keypath: {"count": n, "max_len": m}}` for one parsed document.

    A LIST INHERITS ITS ENCLOSING KEY. `recent_messages: ["...", "..."]` — bare
    strings directly under the message key, which is the shape `eval_set.json`
    used — is otherwise invisible to a scan that only looks at the key
    immediately above a leaf.
    """
    found: dict[str, dict] = {}

    def walk(node, key: str | None, parts: tuple[str, ...]):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k, parts + (_key_token(k),))
        elif isinstance(node, list):
            for v in node:
                walk(v, key, parts + ("[]",))
        elif isinstance(node, str):
            if key in MESSAGE_KEYS and is_free_text(node):
                kp = keypath_of(parts)
                rec = found.setdefault(kp, {"count": 0, "max_len": 0})
                rec["count"] += 1
                rec["max_len"] = max(rec["max_len"], len(node))

    walk(doc, None, ())
    return found


def _parse(path: Path):
    """Parsed documents from a .json or .jsonl file, or None if unreadable."""
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None
    try:
        if path.suffix.lower() == ".jsonl":
            return [json.loads(ln) for ln in text.splitlines() if ln.strip()]
        return [json.loads(text)]
    except (json.JSONDecodeError, ValueError):
        return None


def scan_file(path: Path) -> list[tuple[str, int, int]]:
    """`(keypath, count, max_len)` for one file, sorted. NEVER returns a value."""
    if path.suffix.lower() not in SCANNED_SUFFIXES:
        return []
    docs = _parse(path)
    if docs is None:
        return []
    merged: dict[str, dict] = {}
    for doc in docs:
        for kp, rec in scan_obj(doc).items():
            m = merged.setdefault(kp, {"count": 0, "max_len": 0})
            m["count"] += rec["count"]
            m["max_len"] = max(m["max_len"], rec["max_len"])
    return sorted((kp, r["count"], r["max_len"]) for kp, r in merged.items())


def _candidates(root: Path) -> list[Path]:
    return [p for p in repo_files(root)
            if p.suffix.lower() in SCANNED_SUFFIXES]


def unparseable_files(root: Path) -> list[str]:
    """Tracked JSON/JSONL this scan could not read — i.e. did NOT scan.

    Reported rather than swallowed. A silent skip is indistinguishable from a
    clean file, and the gate pins this at zero so a new one is visible.
    """
    return sorted(str(p.relative_to(root)) for p in _candidates(root)
                  if _parse(p) is None)


def scan_repo(root: Path) -> list[tuple[str, str, int, int]]:
    """`(relpath, keypath, count, max_len)` for the whole repo, sorted.

    🔴 By construction this returns COUNTS AND SHAPES ONLY. No captured value
    ever leaves this module, so a failure message, a CI log and an allowlist
    entry can all quote it safely.
    """
    hits = []
    for path in _candidates(root):
        rel = str(path.relative_to(root))
        for kp, count, max_len in scan_file(path):
            hits.append((rel, kp, count, max_len))
    return sorted(hits)
