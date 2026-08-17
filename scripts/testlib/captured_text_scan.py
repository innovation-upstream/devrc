"""THE structural scan for CAPTURED FREE TEXT in committed JSON/JSONL.

WHY
---
`CLAUDE.md`: "This repo is PUBLIC." The IP half of that rule is
`scripts/testlib/public_ip_scan.py`; the hostname half is
`scripts/testlib/client_host_scan.py`. This module gates a THIRD class those two
structurally cannot see: **text that was captured from somewhere else and frozen
into a fixture** — message bodies, operator prompts, transcript excerpts, chat
content, captured COMMAND LOGS, and the model-written summaries OF those.

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

    a JSON/JSONL/JSONC file  ×  a MESSAGE-ISH KEY  ×  a value that is FREE TEXT

That is narrow in the dimension that carries the disclosure. Authored prose lives
in `.md` and in source comments; captured prose arrives as a serialized DUMP,
under the key the producing tool gave it. The dspy-eval capture is exactly that
shape, and so is every transcript, mailbox and chat export this repo could
plausibly grow.

THE FOUR ARRIVAL PATHS, ALL MEASURED IN THIS TREE
-------------------------------------------------
The first cut of this gate enumerated the keys the dspy-eval capture happened to
use, and was then MEASURED against the producers this repo actually ships. It
missed all four of these; each is now closed and each has a named test:

  1. **The producer's own key.** `scripts/browser-bridge/browser-agent-parse.py`
     emits `answer`/`evidence` from a `transcript.jsonl` and is the repo's
     highest-privilege capture producer (it drives a real logged-in browser).
     `subject` is an email/issue header — someone else's writing by definition;
     28 of them are already in the mail fixture.
  2. **The PLURAL.** 13 of the original 18 keys had no plural, so `comments`
     (GitHub's own API spelling), `prompts`, `transcripts`, `summaries`,
     `excerpts`, `snippets` and `recaps` walked straight through. Matching is
     also CASE-INSENSITIVE now: the mail fixture carries 13 `Subject` headers in
     the canonical RFC capitalisation, which an exact-match set never saw.
  3. **A DICT under a message key, not just a LIST.** `scan_obj` inherited an
     enclosing message key through a list from the start; a dict did not. An
     id-keyed or timestamp-keyed message MAP — `{"messages": {"m_01": "..."}}`,
     `{"transcript": {"<iso-ts>": "..."}}` — is an ordinary export shape and was
     one line away from the case that was handled.
  4. **NO KEY AT ALL above the leaf.** A file *named* `messages.json` holding a
     bare `["...", "..."]`, or a `.jsonl` whose lines are bare strings, has no
     enclosing key for the walk to match. The FILENAME is consulted for the
     first, and a bare-string JSONL line is a text dump by construction.

TWO THRESHOLDS, BECAUSE ONE WAS BLIND TO CHAT
---------------------------------------------
🔴 `MIN_FREE_TEXT_CHARS = 60` is right for a paragraph and near-total blindness
for a chat message. MEASURED: `scripts/signal/tests/fixtures/envelopes.json` — the
repo's ONLY chat subsystem fixture — carries **16** `message`/`body` string
leaves (8 distinct messages, each mirrored in the `envelope` and `expect` halves)
of **12–32 characters. 0 of 16 clear 60.** A gate on captured chat that cannot
see one value in the chat subsystem is scanning clean over its own target class.

So `CHAT_KEYS` — the subset where a short string is never metadata — uses
`MIN_CHAT_TEXT_CHARS = 25`. This is a DELIBERATE trade in the other direction:
it is what puts the synthetic-fixture entries into `ALLOWLIST`, each pinned to a
COUNT so it cannot become a rubber stamp. The alternative considered and
rejected was documenting the blindness and leaving it: that keeps the allowlist
empty, at the price of a gate that is provably blind to the one subsystem whose
entire content is other people's words.

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

  * **Captured text in any format that is not JSON/JSONL/JSONC** — the big one. A
    transcript pasted into a `.md`, a `.csv`/`.txt`/`.yaml` export, a heredoc in
    a shell script, or a Python list literal are all invisible here. In
    particular `claudedocs/the-algorithm-applied-2026-06-17.md` quotes operator
    request phrasings as prose and this scan does not see it (assessed: those are
    aggregated self-authored stems already published as this repo's own feature
    names, not third-party content).
  * **A message-ish key not in `MESSAGE_KEYS`** — a producer that calls the field
    `utterance`, `line` or `msg_body` walks straight through. The only real
    defence is adding keys as new producers appear. `description` is a
    conditional member: see `_MANIFEST_SCOPED_KEYS`.
  * **Free text shorter than the applicable threshold** — 25 characters under a
    `CHAT_KEYS` key, 60 under any other. A 20-character message is still a
    message. The thresholds buy the low false-positive rate that keeps this gate
    green, and that is a trade, not a proof.
  * **Free text with no whitespace** — deliberately not a finding, so a long
    path, hash, token or base64 blob under `text` stays quiet. MEASURED
    consequence: the 11 `transcripts` values in the task-spec-drafter fixture are
    42-character UUID filenames, so that key matching is not by itself what makes
    that file visible — `executed`/`rejected` are.
  * A value nested inside a JSON STRING (double-encoded JSON), which parses as
    one opaque leaf.
  * A file this scan skips: a binary suffix, or a tracked candidate that is not
    valid UTF-8 or does not parse. Those are NOT silently dropped — see
    `unparseable_files`, which the gate pins at zero modulo an explicit,
    reason-carrying allowlist.
  * **The RELPATH is echoed verbatim** in a finding, unlike the key path. A
    fixture directory named after a client therefore leaks the way a data key
    used to. Redacting it was rejected: a finding whose path is `*` cannot be
    acted on. Name fixture directories after the SUBSYSTEM, never the client.
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
from testlib.public_ip_scan import _is_skipped, repo_files  # noqa: E402

__all__ = ["MESSAGE_KEYS", "CHAT_KEYS", "MIN_FREE_TEXT_CHARS",
           "MIN_CHAT_TEXT_CHARS", "JSONL_BARE_STRING_KEY", "min_chars_for",
           "is_free_text", "keypath_of", "scan_obj", "scan_file", "scan_repo",
           "repo_files", "unparseable_files", "SCANNED_SUFFIXES"]

#: Only these are read. Captured prose arrives SERIALIZED; authored prose does
#: not. Widening this to `.md` is the permanently-red gate described above.
#: `.jsonc` is here because this repo TRACKS one (`scripts/opencode/opencode.jsonc`,
#: whose schema has `instructions`/`prompt` fields) and the docstring on the
#: first cut said "JSON/JSONL" while the code meant the two literal suffixes.
#: `.ndjson` is the other spelling of `.jsonl`.
SCANNED_SUFFIXES = frozenset({".json", ".jsonl", ".jsonc", ".ndjson"})

#: Suffixes parsed one-document-per-line.
_LINE_SUFFIXES = frozenset({".jsonl", ".ndjson"})

#: A string this long, containing whitespace, is a sentence rather than an
#: identifier. 60 is the threshold the dspy-eval exposure was MEASURED with
#: (606 of its 1,452 leaves cleared it); every one of its 333 captured message
#: bodies that mattered is prose well above it.
MIN_FREE_TEXT_CHARS = 60

#: 🔴 The chat threshold. MEASURED: the signal fixture's 16 `message`/`body`
#: leaves are 12–32 chars — 0 of 16 clear 60, and 25 catches 8 of them plus one
#: `quote.text`. A real chat line is short; under a
#: `CHAT_KEYS` key there is no metadata for a shorter bound to fire on. See the
#: module docstring for the trade this buys and pays for.
MIN_CHAT_TEXT_CHARS = 25

#: 🔴 AN ENUMERATION, NOT A PATTERN, and each entry says which producer puts
#: captured text there. An unknown key is NOT a finding — that is this gate's
#: single biggest blind spot and it is named in the docstring rather than papered
#: over with a regex that would drag in every config field in the repo.
#: Matched CASE-INSENSITIVELY: every key here is spelled lowercase, and the mail
#: fixture's `Subject` headers are why.
MESSAGE_KEYS: dict[str, str] = {
    # --- verbatim captured conversation -------------------------------------
    "recent_messages": "the exact key that carried 333 captured chat bodies in dspy-eval",
    "messages": "the generic plural; chat-completion payloads and every message dump",
    "message": "the singular of the same",
    "text": "the leaf INSIDE recent_messages[] — 114 sentence-length values there",
    "texts": "the plural of the same; a bare list of captured lines",
    "body": "an email or webhook body (the mailbox subsystem's shape)",
    "bodies": "the plural; a batch export of the same",
    "content": "the OpenAI/Anthropic chat-message field name",
    "contents": "the plural, and the GitHub contents-API spelling",
    "prompt": "a captured operator prompt — the thing CLAUDE.md's new clause names",
    "prompts": "the plural; a prompt corpus frozen for an eval",
    "transcript": "a session transcript excerpt",
    "transcripts": "the plural; browser-agent and drafter runs both emit lists of these",
    "chat": "a chat log under its most obvious key",
    "chats": "the plural of the same",
    "conversation": "the same, singular-noun spelling",
    "conversations": "the plural of the same",
    "comment": "a PR/issue comment body, i.e. someone else's writing",
    "comments": "the plural — GitHub's own API spelling, and the likelier arrival",
    "excerpt": "a quoted fragment of a larger captured document",
    "excerpts": "the plural of the same",
    "snippet": "the same, under the other common spelling",
    "snippets": "the plural of the same",
    "subject": "an email or issue SUBJECT LINE — someone else's writing by definition",
    "subjects": "the plural; a batch of captured headers",
    # --- what this repo's own capture producers emit -------------------------
    "answer": "browser-agent-parse.py's schema key — a model answer ABOUT a real logged-in page",
    "answers": "the plural of the same",
    "evidence": "the sibling key of `answer`; verbatim quoted page content",
    # --- a captured COMMAND LOG (CLAUDE.md's 'route log' class) ---------------
    # A frozen list of the invocations a real run made is captured operational
    # text: it carries real paths, real repo names and real hosts. It is not
    # prose, but it is not authored either.
    "executed": "a frozen log of the commands a REAL agent run executed (the drafter corpus)",
    "rejected": "the other half of the same log — the commands that run was refused",
    "commands": "the generic plural for a captured command/route log",
    "command": "the singular of the same",
    # --- model-written summaries OF captured text (second-order disclosure) --
    # These are in the set because dspy-eval's results-run{1,2,3}.json carried
    # 109 of them: a recap of a client's messages still describes the client's
    # work. Cheap to include — MEASURED zero false positives in this repo.
    "recap": "a generated recap of captured context (results-run*.json)",
    "recaps": "the plural of the same",
    "summary": "the same; 112 of these in the dspy-eval capture",
    "summaries": "the plural of the same",
    "next_step": "a generated next-step sentence derived from captured context",
    "next_steps": "the plural of the same",
    "open_investigations": "free-text investigation notes carried per initiative",
    "recent_commits": "real commit subjects from a client repo — captured, not authored",
    # --- conditional: see _MANIFEST_SCOPED_KEYS ------------------------------
    "description": "a ClickUp task BODY (query.mjs reads it 56×) and a GitHub issue body",
}

#: 🔴 A key that is a finding EXCEPT inside a document that also carries the
#: named sibling. `description` was originally excluded outright, on the sound
#: reasoning that allowlisting a scanner's own canonical examples is how it ends
#: up scanning clean over a real leak — but the exclusion was blunt, and this
#: repo ships a ClickUp integration where `description` IS the task body
#: (`claude/skills/clickup/query.mjs:238`). Scoping keeps the honesty (the three
#: browser-extension manifests are still not findings, and are still not
#: allowlisted) and closes the hole. MEASURED: all 3 long `description` values in
#: this tree sit beside `manifest_version`; the 4th, in
#: `claude/skills/clickup/package.json`, is the empty string.
_MANIFEST_SCOPED_KEYS: dict[str, str] = {
    "description": "manifest_version",
}

#: 🔴 KEYS THAT BLOCK INHERITANCE, spelled out so they are decisions rather than
#: oversights. Each is a key whose long values are authored metadata or a path,
#: so a leaf under one of them does NOT inherit an enclosing message key (arrival
#: path 3 above). This list is READ by `scan_obj` — it is a code path, not a
#: comment; `test_an_excluded_key_blocks_dict_inheritance` is what proves it.
_EXCLUDED_KEYS: dict[str, str] = {
    "title": "short by nature; a long one is a document heading someone wrote",
    "path": "a filesystem path, covered by CLAUDE.md's media-path clause, not this one",
    "current_doc": "a doc PATH, same as above",
    "name": "an identifier; a long one is a package or derivation name, not prose",
    "reason": "generic; used by this repo's own allowlists and error strings",
    "role": "the chat-message ROLE enum that sits beside `content`, never prose",
}

#: 🔴 The subset where a SHORT string is still a message. Every entry is a key
#: whose value is somebody's utterance, never a label, an enum or a path.
#: `subject` is deliberately NOT here: a 25-char subject line is common and the
#: mail fixture alone would contribute 26 pins.
CHAT_KEYS: frozenset[str] = frozenset({
    "recent_messages", "messages", "message", "text", "texts", "body", "bodies",
    "content", "contents", "chat", "chats", "conversation", "conversations",
    "transcript", "transcripts", "comment", "comments",
})

#: The synthetic key a bare-string `.jsonl`/`.ndjson` line is scanned under.
#: A JSONL whose lines are bare strings is a text dump by construction — there is
#: no key above the leaf to enumerate, so the FORMAT is the evidence.
JSONL_BARE_STRING_KEY = "<jsonl-line>"

#: A dict key is SCHEMA if it looks like a field name. Anything else is DATA —
#: `by_repo` was keyed by client repo PATH — and data must not be echoed into a
#: failure message or an allowlist entry, so it renders as `*`.
#:
#: 🔴 NARROWER THAN IT LOOKS, AND IT WAS NARROWED. The first cut admitted `.`
#: and `-`, which let five realistic data keys through verbatim — a client
#: hostname, a bare repo name, an email localpart and a media directory name
#: among them, three of them classes `CLAUDE.md` explicitly forbids. The only
#: guard on it used an ABSOLUTE PATH fixture, the one shape that fails the
#: leading-`[A-Za-z_]` anchor, so widening the class mid-string SURVIVED.
_SCHEMA_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")


def _norm(key) -> str | None:
    """Case-folded key, or None for a non-string key."""
    return key.lower() if isinstance(key, str) else None


def _is_data_map(node: dict) -> bool:
    """True when `node` is a MAP KEYED BY DATA rather than a record of fields.

    🔴 The residual half of the redaction above. `_SCHEMA_KEY` is lexical, so a
    key that happens to look like an identifier — a bare username, a hostname
    with no dot — survives it and echoes verbatim into a finding, and from there
    into a committed allowlist entry in a public repo.

    A structural discriminator, deliberately conservative: a dict is a data map
    only when it has ≥2 keys, NONE of them enumerated, every value is a DICT,
    and all of those dicts carry the IDENTICAL non-empty key set. That last
    clause is what separates a map of same-shaped records — `{"jdoe": {...},
    "asmith": {...}}` — from an object whose fields happen to be objects:
    MEASURED, `scripts/signal/tests/fixtures/envelopes.json` is full of
    `{"envelope": {...}, "expect": {...}}`, whose two children share no key, and
    without the clause every one of those rendered `*` and the report lost the
    only token telling a reader which half a finding was in.

    STILL NOT CLOSED, and the docstring must not claim otherwise: a ONE-entry
    data map, a data map whose values are scalars or lists, and a map of records
    with drifting shapes are all indistinguishable from a record here. The
    lexical `_SCHEMA_KEY` remains the primary defence and this is a second layer,
    not a proof.
    """
    if len(node) < 2:
        return False
    known = set(MESSAGE_KEYS) | set(_EXCLUDED_KEYS)
    if any(_norm(k) in known for k in node):
        return False
    if not all(isinstance(v, dict) and v for v in node.values()):
        return False
    shapes = {frozenset(v) for v in node.values()}
    return len(shapes) == 1


def min_chars_for(key: str | None) -> int:
    """The free-text threshold that applies under `key`."""
    return MIN_CHAT_TEXT_CHARS if key in CHAT_KEYS else MIN_FREE_TEXT_CHARS


def is_free_text(value: str, key: str | None = None) -> bool:
    """Long enough to be a sentence, and containing whitespace.

    The whitespace half is what keeps a 64-char hash, a long path, a token or a
    base64 blob under `text` from being a finding. `key` selects the threshold;
    omitting it asks the strict one.
    """
    return len(value) >= min_chars_for(key) and any(c.isspace() for c in value)


def keypath_of(parts) -> str:
    """Render a walk into a stable, DATA-FREE key path (`by_repo.*[].text`).

    `[]` appends with no separator so a list reads as an index on the key it
    belongs to; every other part is dot-joined.
    """
    out = ""
    for p in parts:
        out += p if p == "[]" else (("." + p) if out else p)
    return out


def _key_token(key, redact: bool = False) -> str:
    if redact or not isinstance(key, str):
        return "*"
    return key if _SCHEMA_KEY.match(key) else "*"


def scan_obj(doc, key: str | None = None, parts: tuple[str, ...] = ()) -> dict[str, dict]:
    """`{keypath: {"count": n, "max_len": m}}` for one parsed document.

    A LIST INHERITS ITS ENCLOSING KEY, and so does a DATA-KEYED DICT.
    `recent_messages: ["...", "..."]` — bare strings directly under the message
    key, which is the shape `eval_set.json` used — is otherwise invisible to a
    scan that only looks at the key immediately above a leaf. So is its dict
    twin, `messages: {"m_01": "..."}`, which is an ordinary id-keyed export.

    `key`/`parts` seed the walk, so a caller that knows the enclosing key from
    somewhere other than the document (the FILENAME, or the fact that a JSONL
    line is a bare string) can supply it.
    """
    found: dict[str, dict] = {}

    def walk(node, key: str | None, parts: tuple[str, ...]):
        if isinstance(node, dict):
            here = {_norm(k) for k in node}
            redact = _is_data_map(node)
            for k, v in node.items():
                nk = _norm(k)
                scoped = _MANIFEST_SCOPED_KEYS.get(nk)
                if scoped is not None and scoped in here:
                    continue  # an extension manifest: authored UI copy
                if nk in MESSAGE_KEYS:
                    child = nk
                elif (isinstance(v, str) and key in MESSAGE_KEYS
                      and nk not in _EXCLUDED_KEYS):
                    child = key  # an id-/timestamp-keyed message MAP
                else:
                    child = nk
                walk(v, child, parts + (_key_token(k, redact),))
        elif isinstance(node, list):
            for v in node:
                walk(v, key, parts + ("[]",))
        elif isinstance(node, str):
            if (key in MESSAGE_KEYS or key == JSONL_BARE_STRING_KEY) \
                    and is_free_text(node, key):
                kp = keypath_of(parts)
                rec = found.setdefault(kp, {"count": 0, "max_len": 0})
                rec["count"] += 1
                rec["max_len"] = max(rec["max_len"], len(node))

    walk(doc, key, parts)
    return found


def _strip_jsonc(text: str) -> str:
    """`//` and `/* */` comments and trailing commas removed, STRINGS PRESERVED.

    Only used for `.jsonc`. A `.json` file is parsed strictly, so a comment in
    one stays an `unparseable_files` finding rather than being quietly accepted.

    🔴 One pass, string-aware throughout. A regex sweep for `,\\s*[}\\]]` over the
    whole text is NOT equivalent: it rewrites the inside of any string value that
    happens to contain `, }` — silently editing the very content this module
    exists to measure. The trailing comma is therefore dropped only when the walk
    knows it is outside a string.
    """
    out: list[str] = []
    i, n, in_str, esc = 0, len(text), False, False
    pending_comma = -1  # index in `out` of a comma that may turn out to be trailing
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            pending_comma = -1
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        if c == ",":
            pending_comma = len(out)
        elif c in "}]" and pending_comma >= 0:
            out[pending_comma] = ""  # it WAS trailing
            pending_comma = -1
        elif not c.isspace():
            pending_comma = -1
        out.append(c)
        i += 1
    return "".join(out)


def _parse(path: Path):
    """Parsed documents from a scanned file, or None if unreadable.

    None is NOT a silent skip — `unparseable_files` reports it and the gate pins
    that list.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None
    suffix = path.suffix.lower()
    try:
        if suffix in _LINE_SUFFIXES:
            return [json.loads(ln) for ln in text.splitlines() if ln.strip()]
        if suffix == ".jsonc":
            return [json.loads(_strip_jsonc(text))]
        return [json.loads(text)]
    except (json.JSONDecodeError, ValueError):
        return None


def _filename_seed(path: Path) -> str | None:
    """The message key a FILENAME asserts, or None.

    🔴 Arrival path 4: `messages.json` holding `["...", "..."]` has no key above
    the leaf at all, and the first cut of the walk started at `key=None` and
    never consulted the name. Applied only when the top-level document is a list
    or a bare string — i.e. exactly when there is no enclosing key — so a normal
    object in a file that happens to be named `content.json` is unaffected.
    """
    stem = path.stem.lower()
    return stem if stem in MESSAGE_KEYS else None


def scan_file(path: Path) -> list[tuple[str, int, int]]:
    """`(keypath, count, max_len)` for one file, sorted. NEVER returns a value."""
    if path.suffix.lower() not in SCANNED_SUFFIXES:
        return []
    docs = _parse(path)
    if docs is None:
        return []
    line_format = path.suffix.lower() in _LINE_SUFFIXES
    seed = _filename_seed(path)
    merged: dict[str, dict] = {}
    for doc in docs:
        key: str | None = None
        parts: tuple[str, ...] = ()
        if isinstance(doc, str) and line_format:
            key = seed or JSONL_BARE_STRING_KEY
            parts = (key,)
        elif isinstance(doc, (list, str)) and seed is not None:
            key, parts = seed, (seed,)
        for kp, rec in scan_obj(doc, key, parts).items():
            m = merged.setdefault(kp, {"count": 0, "max_len": 0})
            m["count"] += rec["count"]
            m["max_len"] = max(m["max_len"], rec["max_len"])
    return sorted((kp, r["count"], r["max_len"]) for kp, r in merged.items())


def _candidates(root: Path) -> list[Path]:
    """Tracked files this scan reads.

    `_is_skipped` is re-applied here for the same reason `public_ip_scan.scan_repo`
    re-applies it: `repo_files` only filters on the filesystem-walk tier, so on
    the `git ls-files` tier a tracked path under a skip dir would come back.
    MEASURED zero such paths today — this closes a DIVERGENCE at the seam this
    module claims to have unified, not a live miss.
    """
    return [p for p in repo_files(root)
            if p.suffix.lower() in SCANNED_SUFFIXES and not _is_skipped(p, root)]


def unparseable_files(root: Path) -> list[str]:
    """Tracked JSON/JSONL this scan could not read — i.e. did NOT scan.

    Reported rather than swallowed. A silent skip is indistinguishable from a
    clean file, and the gate pins this at zero (modulo a reason-carrying
    allowlist) so a new one is visible.
    """
    return sorted(str(p.relative_to(root)) for p in _candidates(root)
                  if _parse(p) is None)


def scan_repo(root: Path) -> list[tuple[str, str, int, int]]:
    """`(relpath, keypath, count, max_len)` for the whole repo, sorted.

    🔴 By construction this returns COUNTS AND SHAPES ONLY. No captured VALUE
    ever leaves this module. The KEY PATH is redacted where a key is data-shaped
    (`_SCHEMA_KEY`, `_is_data_map`); the RELPATH is not redacted at all, and the
    docstring's blind-spot list says so.
    """
    hits = []
    for path in _candidates(root):
        rel = str(path.relative_to(root))
        for kp, count, max_len in scan_file(path):
            hits.append((rel, kp, count, max_len))
    return sorted(hits)
