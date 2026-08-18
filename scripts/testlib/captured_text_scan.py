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
           "repo_files", "unparseable_files", "SCANNED_SUFFIXES",
           # --- the MARKUP half (`.html`/`.txt`); see its own banner below ---
           "HTML_SUFFIXES", "TEXT_SUFFIXES", "MARKUP_SUFFIXES", "PERSON_ATTRS",
           "PERSON_ITEMPROPS", "TEXT_ATTRS", "BLOCK_ELEMENTS",
           "MIN_VOCABULARY_LINES", "MIN_VOCABULARY_PURITY", "MAX_RECORD_TOKENS",
           "MIN_PROSE_RUNS", "DEPENDENCY_MANIFESTS",
           "SIGNAL_HTML_TEXT", "SIGNAL_HTML_PROSE_RUNS",
           "SIGNAL_HTML_UNTERMINATED_CODE",
           "SIGNAL_TXT_LINE", "SIGNAL_TXT_VOCABULARY",
           "visible_text_runs", "visible_text_blocks", "scan_html_file",
           "scan_text_file", "scan_markup_file", "scan_markup_repo",
           "markup_candidates", "markup_unparseable_files"]

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


# =============================================================================
# THE MARKUP HALF — `.html` and `.txt`
# =============================================================================
# 🔴 WHY THIS IS A SECOND SET OF RULES AND NOT A WIDER `SCANNED_SUFFIXES`.
#
# Everything above keys on `a MESSAGE-ISH KEY x a value that is FREE TEXT`, and
# that conjunction is only available because JSON hands you keys. HTML and plain
# text have no keys, so routing them through `SCANNED_SUFFIXES` would not widen
# the gate — it would send them to `json.loads`, fail, and land every one of them
# in `unparseable_files`. The suffix sets are therefore SEPARATE and the walk is
# separate; what is SHARED is the part that must not fork: `repo_files`,
# `_is_skipped` and `is_free_text`.
#
# WHY THESE TWO SUFFIXES, AND WHAT ACTUALLY OCCUPIES THEM
# -------------------------------------------------------
# Both were MEASURED against this tree before a rule was written, and the
# measurement changed the design twice — the numbers are in
# `scripts/tests/test_no_captured_markup.py`, which owns the pins.
#
#   * `.html` — the format a captured PAGE arrives in. This repo drives a real
#     logged-in browser (`browser-bridge`) and files downloads by page context
#     (`dl-router`), so a scraped page landing in a fixture directory is the
#     likeliest future shape of this exposure, not a hypothetical.
#
#   * `.txt` — the format a captured LIST arrives in: a roster, a vocabulary, an
#     export of names. 🔴 A ROSTER OF NAMES IS A MULTI-TOKEN SHAPE, and the first
#     cut of this rule could not see it. MEASURED against that cut: 40 lines of
#     `Firstname Lastname` -> 0 findings; 40 tab-separated `handle<TAB>email`
#     rows -> 0; only 40 BARE handles fired. Every multi-token line broke the
#     whitespace test (killing `<vocabulary>`) and sat under 60 chars (killing
#     `<line>`), so the stated motivating case walked through. The unit is
#     therefore a RECORD LINE — a data line that is not free text and carries at
#     most `MAX_RECORD_TOKENS` whitespace-separated tokens — not a bare token.
#
# 🔴 THE PROSE RULE CANNOT SEE A LIST OF IDENTIFIERS, AND THAT IS WHY THERE ARE
# TWO `.txt` RULES. `is_free_text` requires WHITESPACE, deliberately, so a path,
# hash or token stays quiet. MEASURED: the one `.txt` in this tree that carries a
# mined vocabulary — `scripts/repo-cos/tests/fixtures/initiatives_current_slugs.txt`
# — has 144 data lines and **0 of them contain whitespace**. A line-oriented
# free-text rule is structurally blind to it. Dropping the whitespace requirement
# to reach it would fire on every `requirements.txt`, every lockfile and every
# id list in the repo: the permanently-red gate `claude/RULES.md` forbids. So the
# vocabulary rule keys on the SHAPE OF THE FILE (many distinct record lines)
# instead of the shape of one value.
#
# 🔴 THE `.html` PROSE RULE KEYS ON A BLOCK, NOT ON A TEXT NODE — MEASURED WHY.
# The first cut applied `is_free_text` to each whitespace-collapsed TEXT NODE, so
# it keyed on the LONGEST UNINTERRUPTED RUN and was blind to prose the same size
# delivered in pieces. MEASURED against that cut:
#
#     1 run  x 148 chars (total 148)  -> 1 finding
#     3 runs x  70 chars (total 210)  -> 1 finding   (a single >60 run still fires)
#     3 runs x  55 chars (total 165)  -> 0 findings
#     5 runs x  40 chars (total 200)  -> 0 findings
#     8 runs x  25 chars (total 200)  -> 0 findings
#
# One `<a>` mid-paragraph usually leaves a >60 run, so ordinary inline markup did
# NOT generally defeat it — but a markup-rich post (several links, a `<br>`, an
# `<em>`) fragments into runs that are ALL under the threshold, and then total
# volume is irrelevant. MEASURED: a three-paragraph post of that shape, 105 chars
# of prose per paragraph, scored 0 under the old rule and 3 under this one.
#
# So each text run is attributed to the INNERMOST enclosing `BLOCK_ELEMENTS`
# element and the block's runs are re-joined before the threshold is applied.
# Two consequences, both driven by tests rather than argued:
#   * the old rule is a SUBSET — any run over the threshold sits inside a block
#     at least that long — so nothing that used to fire stopped firing;
#   * IDENTICAL repeated runs inside one block are DEDUPLICATED first. Re-joining
#     exists to reassemble a sentence split by inline markup, and a split
#     sentence yields DISTINCT fragments; a block that is one short label
#     repeated is chrome. MEASURED on this tree without the dedupe: the forum
#     fixture scored 5 and `embed-player-frame.html` 1, every one of them the
#     placeholder `Sample text` repeated 5-7 times. With it: 1 and 0.
#
# The residue the block rule still cannot see is a page of SHORT STANDALONE
# paragraphs — each its own block, each under the threshold. `SIGNAL_HTML_PROSE_RUNS`
# is the second rule for exactly that shape; see `MIN_PROSE_RUNS`.
#
# WHAT THE MARKUP RULES DO **NOT** CATCH
# ---------------------------------------
# 🔴 NOT EXHAUSTIVE. A clean run means "no match for the shapes below".
#
#   * **Any other format.** `.md`, `.csv`, `.yaml`, `.tsv`, `.srt`/`.vtt`
#     subtitles, a heredoc in a shell script, a Python list literal. `.md` is
#     excluded for the same reason the JSON half excludes it: this repo is ~200
#     files of authored markdown and a gate on it would be red forever.
#   * **Short HTML prose, once BLOCK aggregation has been applied.** A block
#     under `MIN_FREE_TEXT_CHARS` whose page has fewer than `MIN_PROSE_RUNS`
#     wordy runs is not a finding, so a page of a handful of one-line chat
#     messages still walks through both text rules. There is no `CHAT_KEYS`
#     equivalent here because HTML gives no key to scope a lower threshold to,
#     and applying 25 to every block in the tree fires on ordinary UI copy.
#     MEASURED consequence: `forum-thread-page.html` has 108 visible text runs,
#     the longest 48 chars and 102 of them the same 11-char placeholder; its one
#     `<text>` finding is a BLOCK aggregate, and the file is otherwise seen by
#     the ATTRIBUTE rules alone.
#   * **A person attribute this enumeration does not name.** `PERSON_ATTRS` is an
#     enumeration, not a pattern, for the same reason `MESSAGE_KEYS` is: a regex
#     over `data-*` would drag in every framework hook in every fixture. The
#     three major forum dialects (XenForo, Discourse, Reddit) are enumerated; a
#     fourth is not.
#   * **Text in an attribute not in `TEXT_ATTRS`**, and text inside `<script>` or
#     `<style>` — a JSON-LD blob or an inlined state object is skipped as code.
#     🔴 This is a real hole: `<script type="application/ld+json">` is exactly
#     where a captured page puts its structured author metadata. Closing it means
#     parsing embedded JSON, which is the JSON half's job on a file it never
#     sees. Named, not fixed. What IS closed is the *unterminated* case: a
#     `<script>` with no `</script>` puts `html.parser` in CDATA mode for the
#     whole remainder of the document, which was a one-tag way to hide a captured
#     page exactly as `<template>` would have been — it is now the
#     `SIGNAL_HTML_UNTERMINATED_CODE` finding rather than silence.
#   * **An HTML COMMENT.** Deliberate, not an oversight: `forum-thread-page.html`
#     carries 7 documentation comments of its own and this repo's fixtures
#     annotate themselves that way, so scanning comments would red the gate on
#     its own documentation. The cost is that a 148-char sentence inside
#     `<!-- ... -->` is invisible — MEASURED, and pinned in both directions.
#   * **A `.txt` comment.** Everything from a `#` to end of line is treated as
#     authored, so captured text pasted after a `#` is invisible.
#   * **A `.txt` named as a dependency manifest** (`DEPENDENCY_MANIFESTS`): the
#     vocabulary rule is a SHAPE rule and a 30-package `requirements.txt` has the
#     shape of a roster exactly. Those basenames are exempted from the vocabulary
#     rule so an ordinary dependency bump is not a gate review; the cost is that
#     a captured roster committed under one of those literal names is invisible.
#   * **A vocabulary below the size floor**, or one mixed with enough non-record
#     lines to fall under the purity floor, or whose lines carry more than
#     `MAX_RECORD_TOKENS` tokens.
#   * **The VALUE**, always: like the JSON half, these functions return counts,
#     lengths and enumerated signal tokens only. The RELPATH is echoed verbatim.
#
# A file this scan cannot DECODE or PARSE is NOT a silent pass: it is reported by
# `markup_unparseable_files`, the exact analogue of the JSON half's
# `unparseable_files`, and the gate pins that list at zero. MEASURED why it must
# be: the same captured page saved UTF-8 scored 3 signals and saved windows-1252
# scored 0, while still counting toward the candidates-walked floor.
#
# Like both siblings this guards HEAD only — see `test_no_captured_markup.py`,
# which spells out what that structurally cannot see.

import html as _html                                            # noqa: E402
from html.parser import HTMLParser                              # noqa: E402

#: The markup suffixes, kept apart from `SCANNED_SUFFIXES` (see the banner).
HTML_SUFFIXES = frozenset({".html", ".htm"})
TEXT_SUFFIXES = frozenset({".txt"})
MARKUP_SUFFIXES = HTML_SUFFIXES | TEXT_SUFFIXES

#: Element content that is CODE, not prose. Narrow on purpose: `<template>` is
#: NOT here, because its contents are ordinary inert markup and skipping it would
#: hand an author a one-tag way to hide a captured page from this gate. An
#: UNTERMINATED one of these is the same hazard through a different door and is
#: reported as `SIGNAL_HTML_UNTERMINATED_CODE` rather than skipped in silence.
_NON_PROSE_ELEMENTS = frozenset({"script", "style"})

#: 🔴 THE PROSE UNIT. A text run is attributed to the INNERMOST open element from
#: this set, and the block's DISTINCT runs are re-joined before the free-text
#: threshold is applied — see the banner for the measurement that forced it.
#: These are the HTML block-level containers: an element from this set starts a
#: new paragraph, everything else (`a`, `em`, `span`, `strong`, `code`, `br`, …)
#: is inline and must NOT split a sentence. `body` is here so that stray text
#: with no block wrapper is still aggregated instead of being lost.
BLOCK_ELEMENTS = frozenset({
    "address", "article", "aside", "blockquote", "body", "caption", "dd",
    "details", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer",
    "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "li",
    "main", "nav", "ol", "p", "pre", "section", "summary", "table", "tbody",
    "td", "template", "tfoot", "th", "thead", "title", "tr", "ul",
})

#: 🔴 AN ENUMERATION, NOT A PATTERN. Each entry is an attribute whose VALUE
#: identifies a person, and each says which markup dialect puts it there. The
#: value is never reported — only that the attribute is present and how many
#: times — because a user id IS the disclosure.
PERSON_ATTRS: dict[str, str] = {
    "data-user-id": "XenForo/Discourse/Reddit: the numeric account id of a poster",
    "data-userid": "the same field under the unhyphenated spelling",
    "data-user": "the same, carrying a handle rather than an id",
    "data-username": "an account handle verbatim",
    "data-author": "the post author, as a handle or a slug",
    "data-author-id": "the author's numeric id",
    "data-member-id": "the forum-member spelling of the same",
    "data-poster-id": "the imageboard/forum spelling of the same",
    "data-account-id": "the generic account spelling",
    "data-profile-id": "a social-profile identifier",
    "data-handle": "an @handle as a first-class attribute",
    # --- the two dialects the XenForo-shaped list above could not see --------- #
    # MEASURED: before these three entries a Discourse capture and a Reddit
    # capture each produced NO person signal at all, and those are the other two
    # major forum dialects a scrape in this repo would arrive in.
    "data-user-card": "Discourse: the poster's handle, on every avatar and byline",
    "data-author-fullname": "Reddit: the AUTHOR's account fullname (`t2_…`)",
    "data-fullname": (
        "Reddit: the thing fullname. On a profile capture it IS the account; on "
        "a listing it names the post. Enumerated anyway — a page carrying it is "
        "a Reddit capture either way, which is the disclosure this gate exists "
        "to see"),
    "rel": "only when it is `author` — see `_PERSON_REL_VALUES`",
}

#: `rel` is a finding ONLY for these values. `rel="stylesheet"` is on every page
#: in this tree, so an unconditional `rel` entry would be red forever — the same
#: scoping `_MANIFEST_SCOPED_KEYS` applies to `description` in the JSON half.
_PERSON_REL_VALUES = frozenset({"author"})

#: Microdata/RDFa `itemprop` values that name a PERSON. schema.org's `name`
#: inside an `itemscope` of type Person is how a forum marks up its post authors,
#: and it is the shape `forum-thread-page.html` actually carries.
PERSON_ITEMPROPS: dict[str, str] = {
    "name": "schema.org Person.name — the author's display name",
    "alternatename": "schema.org Person.alternateName — a handle or nickname",
    "givenname": "schema.org Person.givenName",
    "familyname": "schema.org Person.familyName",
    "author": "schema.org author, when spelled as an itemprop",
    "creator": "the same under schema.org's other spelling",
}

#: Attributes whose value is PROSE rather than an identifier, so the free-text
#: threshold applies to them exactly as it does to a text node. `content` is the
#: one that matters: `<meta property="og:description">` is where a captured page
#: keeps a post excerpt, and it is invisible to a text-node walk.
TEXT_ATTRS: dict[str, str] = {
    "content": "the `<meta>` payload — og:description/og:title carry page prose",
    "alt": "alternative text for an image, written as a sentence",
    "title": "the tooltip/advisory text, frequently a full caption",
    "aria-label": "an accessible name, written as prose",
    "placeholder": "form placeholder copy",
    "data-caption": "a lightbox/gallery caption — verbatim page content",
}

#: Signal tokens. 🔴 Every token is drawn from an ENUMERATION in this module and
#: never from the document, so a signal can no more leak data than a count can.
SIGNAL_HTML_TEXT = "<text>"
SIGNAL_HTML_PROSE_RUNS = "<prose-runs>"
SIGNAL_HTML_UNTERMINATED_CODE = "<unterminated-code>"
SIGNAL_TXT_LINE = "<line>"
SIGNAL_TXT_VOCABULARY = "<vocabulary>"

#: 🔴 The SECOND `.html` prose rule: how many DISTINCT wordy runs a page carries.
#: The block rule keys on one paragraph's length, so a page of short standalone
#: paragraphs — a chat or message export — is invisible to it whatever the
#: volume. A run counts here when it holds whitespace and clears the SHARED
#: `MIN_CHAT_TEXT_CHARS`; the floor is on the COUNT, which is what stops 25 from
#: firing on ordinary UI copy the way a per-run threshold of 25 would.
#:
#: MEASURED over the 19 tracked `.html` in this tree: the maximum is 5, on the
#: two extension options pages, whose help text is authored prose. 10 is DOUBLE
#: that — those pages can grow by 100% before a pin is needed — and a captured
#: chat or message dump of ten lines still fires. The forum fixture scores 2 and
#: a three-poster Discourse capture 3, so this rule is not what sees either;
#: that is deliberate, and the block rule and the attribute rules are.
MIN_PROSE_RUNS = 10

#: 🔴 The vocabulary floor. MEASURED on this tree: the slugs fixture has 144
#: record data lines; the next-largest `.txt` has 23 data lines and 0 records.
#: 25 sits in that gap with room on both sides — high enough that a short id list
#: (a 4-line `requirements.txt`) is not a finding, low enough that a roster is. A
#: floor set at the occupant's own count would red the gate the day a line is
#: deleted.
MIN_VOCABULARY_LINES = 25

#: The fraction of data lines that must be RECORD lines for the file to be a LIST
#: rather than a document that happens to contain some. MEASURED: the slugs
#: fixture is 1.0; `MANIFEST.txt` is 0.0.
MIN_VOCABULARY_PURITY = 0.9

#: 🔴 The width of a RECORD line. A roster row is `Firstname Lastname`, a
#: `handle<TAB>email` pair, or `name, city, role` — a small, fixed number of
#: fields. A sentence is not. MEASURED: at 3 the three roster shapes in the
#: banner all fire and nothing in this tree moves; the free-text test is applied
#: FIRST regardless, so a 3-token line that is already prose (a 60-char
#: `<line>` finding) is never counted as a record.
MAX_RECORD_TOKENS = 3

#: 🔴 AN ENUMERATION OF BASENAMES, NOT A PATTERN. A dependency manifest has the
#: shape of a roster — many distinct short record lines — so without this a
#: 30-package `requirements.txt` is 30 findings. MEASURED on the shape that would
#: land next: 30 `package==1.0.n` lines scored `<vocabulary> 30` before this
#: exemption and 0 after. The named cost is in the blind-spot list: a captured
#: roster committed under one of these literal basenames is invisible.
DEPENDENCY_MANIFESTS = frozenset({
    "requirements.txt",         # pip, and the only one this tree carries today
    "requirements-dev.txt",     # the conventional dev split of the same
    "requirements-test.txt",    # and the test split
    "constraints.txt",          # pip's version-pin companion file
})

#: Everything from a `#` to end of line is authored by convention.
_TXT_COMMENT = re.compile(r"#.*$")


class _VisibleText(HTMLParser):
    """Collect text nodes outside `_NON_PROSE_ELEMENTS`, grouped into BLOCKS,
    plus the attribute occurrences the rules above enumerate.

    Built on `html.parser` rather than a regex split on `<[^>]*>`: an attribute
    value containing `<` or `>` (a `title="a > b"` is ordinary) desynchronises a
    regex split and silently moves the boundary between markup and text, which
    would mean this gate reporting a number it cannot justify.

    🔴 IT ALSO DEPENDS ON `html.parser` NORMALISING CASE. Tag names and attribute
    NAMES arrive already lower-cased, which is why `tag in BLOCK_ELEMENTS` and
    `name in PERSON_ATTRS` need no fold of their own — an added `.lower()` there
    is dead code that reads like a guard. Attribute VALUES are NOT normalised, so
    the two value comparisons below do fold explicitly. The dependency is pinned
    by `test_html_parser_normalises_tag_and_attribute_NAMES`.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.runs: list[str] = []
        #: one entry per closed block element, its DISTINCT runs re-joined
        self.blocks: list[str] = []
        #: signal -> list of value LENGTHS (never the values)
        self.attr_hits: dict[str, list[int]] = {}
        self._skip = 0
        #: accumulator stack; index 0 is the implicit document-level block
        self._stack: list[list[str]] = [[]]

    # -- elements ---------------------------------------------------------- #
    def handle_starttag(self, tag, attrs):
        if tag in _NON_PROSE_ELEMENTS:
            self._skip += 1
        if tag in BLOCK_ELEMENTS:
            self._stack.append([])
        self._note_attrs(attrs)

    def handle_startendtag(self, tag, attrs):
        self._note_attrs(attrs)

    def handle_endtag(self, tag):
        if tag in _NON_PROSE_ELEMENTS and self._skip:
            self._skip -= 1
        if tag in BLOCK_ELEMENTS and len(self._stack) > 1:
            self._close_block()

    # -- data -------------------------------------------------------------- #
    def handle_data(self, data):
        if self._skip:
            return
        run = " ".join(_html.unescape(data).split())
        if run:
            self.runs.append(run)
            self._stack[-1].append(run)

    # -- blocks ------------------------------------------------------------ #
    def _close_block(self):
        """Pop one accumulator and record its DISTINCT runs, in first-seen order.

        The dedupe is the difference between "a paragraph split by inline markup"
        and "a container holding the same short label seven times"; the banner
        carries the measurement that made it necessary.
        """
        seen: set[str] = set()
        kept = [r for r in self._stack.pop()
                if not (r in seen or seen.add(r))]
        if kept:
            self.blocks.append(" ".join(kept))

    def close(self):
        super().close()
        while self._stack:
            self._close_block()

    @property
    def unterminated_code(self) -> bool:
        """A `<script>`/`<style>` that never closed swallowed the rest of the
        document. `html.parser` stays in CDATA mode to EOF, so every text node
        and every attribute after it is invisible — the same one-tag hide that
        keeping `<template>` out of `_NON_PROSE_ELEMENTS` refuses to allow."""
        return self._skip > 0

    # -- attributes -------------------------------------------------------- #
    def _record(self, signal: str, length: int):
        self.attr_hits.setdefault(signal, []).append(length)

    def _note_attrs(self, attrs):
        for name, raw_value in attrs:
            value = _html.unescape(raw_value or "").strip()
            if not value:
                continue          # a bare or empty attribute carries nothing
            if name in PERSON_ATTRS:
                if name != "rel" or value.lower() in _PERSON_REL_VALUES:
                    self._record(f"@person:{name}", len(value))
            if name == "itemprop" and value.lower() in PERSON_ITEMPROPS:
                self._record(f"@itemprop:{value.lower()}", len(value))
            if name in TEXT_ATTRS and is_free_text(value):
                self._record(f"@text:{name}", len(value))


def _parsed(markup: str) -> _VisibleText | None:
    """A fed-and-closed parser, or None if `html.parser` raised.

    None is the markup half's `_parse(...) is None`: the file was NOT scanned,
    and `markup_unparseable_files` reports it rather than the scan returning an
    empty list that reads as clean.
    """
    p = _VisibleText()
    try:
        p.feed(markup)
        p.close()
    except Exception:            # a malformed page must not break CI for everyone
        return None
    return p


def visible_text_runs(markup: str) -> list[str]:
    """Whitespace-collapsed text nodes of `markup`, outside script/style.

    Exposed because the `.html` text rule is the half most likely to be argued
    about, and an argument about a number should be settleable by running the
    function that produced it. `visible_text_blocks` is what the rule ACTUALLY
    keys on; this is the finer unit the block aggregate is built from.
    """
    p = _parsed(markup)
    return [] if p is None else p.runs


def visible_text_blocks(markup: str) -> list[str]:
    """The per-block aggregates the `.html` prose rule keys on.

    One entry per block element that held text, its DISTINCT direct runs
    re-joined with a space, in document order of block CLOSE.
    """
    p = _parsed(markup)
    return [] if p is None else p.blocks


def _wordy_runs(runs) -> set[str]:
    """The DISTINCT runs that look like a sentence at the CHAT threshold.

    Distinct, because a repeated label is one piece of copy however many times a
    template emits it — the same reason `_close_block` deduplicates.
    """
    return {r for r in runs
            if len(r) >= MIN_CHAT_TEXT_CHARS and any(c.isspace() for c in r)}


def _scan_markup_html(markup: str) -> list[tuple[str, int, int]] | None:
    """`(signal, count, max_len)` for one HTML document, or None if unparseable."""
    p = _parsed(markup)
    if p is None:
        return None
    found: dict[str, list[int]] = dict(p.attr_hits)
    prose = [len(b) for b in p.blocks if is_free_text(b)]
    if prose:
        found[SIGNAL_HTML_TEXT] = prose
    wordy = _wordy_runs(p.runs)
    if len(wordy) >= MIN_PROSE_RUNS:
        found[SIGNAL_HTML_PROSE_RUNS] = [len(r) for r in wordy]
    if p.unterminated_code:
        found[SIGNAL_HTML_UNTERMINATED_CODE] = [len(markup)]
    return sorted((sig, len(lens), max(lens)) for sig, lens in found.items())


def scan_html_file(path: Path) -> list[tuple[str, int, int]]:
    """`(signal, count, max_len)` for one HTML file, sorted. NEVER a value.

    An unreadable or unparseable file returns `[]` here and is named by
    `markup_unparseable_files` — the empty list alone is not the report.
    """
    try:
        markup = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    return _scan_markup_html(markup) or []


def _txt_data_lines(text: str) -> list[str]:
    """Non-blank, non-comment lines, stripped. The `.txt` unit of measurement."""
    out = []
    for line in text.splitlines():
        body = _TXT_COMMENT.sub("", line).strip()
        if body:
            out.append(body)
    return out


def _is_record_line(line: str) -> bool:
    """A roster ROW: not prose, and at most `MAX_RECORD_TOKENS` fields.

    🔴 The free-text test runs FIRST and is what keeps this from swallowing the
    `<line>` rule's occupant: a 60-char three-word sentence is prose, not a
    record. Below that length the two rules genuinely overlap, and they are
    independent by design — a short two-column export is a roster.
    """
    if is_free_text(line):
        return False
    return 1 <= len(line.split()) <= MAX_RECORD_TOKENS


def _scan_markup_text(text: str, name: str) -> list[tuple[str, int, int]]:
    """`(signal, count, max_len)` for one `.txt` body, sorted. NEVER a value.

    Two independent rules, because one cannot see the other's occupant:

      * `<line>`       — a data line that is free text (prose pasted into a txt)
      * `<vocabulary>` — the FILE is a list of RECORD lines (a roster, an export)
    """
    data = _txt_data_lines(text)
    found: list[tuple[str, int, int]] = []

    prose = [len(line) for line in data if is_free_text(line)]
    if prose:
        found.append((SIGNAL_TXT_LINE, len(prose), max(prose)))

    if data and name.lower() not in DEPENDENCY_MANIFESTS:
        records = [line for line in data if _is_record_line(line)]
        distinct = set(records)
        if (len(distinct) >= MIN_VOCABULARY_LINES
                and len(records) / len(data) >= MIN_VOCABULARY_PURITY):
            found.append((SIGNAL_TXT_VOCABULARY, len(distinct),
                          max(len(line) for line in distinct)))
    return sorted(found)


def scan_text_file(path: Path) -> list[tuple[str, int, int]]:
    """`(signal, count, max_len)` for one `.txt`, sorted. NEVER a value.

    An undecodable file returns `[]` here and is named by
    `markup_unparseable_files` — the empty list alone is not the report.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    return _scan_markup_text(text, path.name)


def scan_markup_file(path: Path) -> list[tuple[str, int, int]]:
    """Dispatch one path to the rule set its suffix selects."""
    suffix = path.suffix.lower()
    if suffix in HTML_SUFFIXES:
        return scan_html_file(path)
    if suffix in TEXT_SUFFIXES:
        return scan_text_file(path)
    return []


def _markup_unreadable(path: Path) -> bool:
    """True when this scan could not DECODE or PARSE `path` — i.e. did NOT scan it.

    🔴 The exact analogue of the JSON half's `_parse(...) is None`, and it exists
    because the markup half shipped without one. MEASURED: the same captured page
    saved UTF-8 scored 3 signals and saved windows-1252 scored 0 — silently,
    while still counting toward the candidates-walked floor. A one-byte re-encode
    was a complete bypass of a gate whose sibling closes exactly this.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return True
    if path.suffix.lower() in HTML_SUFFIXES:
        return _scan_markup_html(text) is None
    return False


def markup_unparseable_files(root: Path) -> list[str]:
    """Tracked `.html`/`.txt` this scan could not read — i.e. did NOT scan.

    Reported rather than swallowed, and pinned at zero by the gate, for the
    reason `unparseable_files` is: a silent skip is indistinguishable from a
    clean file.
    """
    return sorted(str(p.relative_to(root)) for p in markup_candidates(root)
                  if _markup_unreadable(p))


def markup_candidates(root: Path) -> list[Path]:
    """Tracked `.html`/`.txt` this scan reads.

    `_is_skipped` is re-applied after `repo_files` for exactly the reason
    `_candidates` does it above — `repo_files` only filters on the
    filesystem-walk tier, so on the `git ls-files` tier a tracked path under a
    skip dir comes back.
    """
    return [p for p in repo_files(root)
            if p.suffix.lower() in MARKUP_SUFFIXES and not _is_skipped(p, root)]


def scan_markup_repo(root: Path) -> list[tuple[str, str, int, int]]:
    """`(relpath, signal, count, max_len)` for every tracked `.html`/`.txt`.

    🔴 COUNTS AND ENUMERATED SIGNAL TOKENS ONLY. No captured value, and no token
    taken from the document, ever leaves this function. The relpath is not
    redacted — same trade, and same warning, as `scan_repo`.
    """
    hits = []
    for path in markup_candidates(root):
        rel = str(path.relative_to(root))
        for signal, count, max_len in scan_markup_file(path):
            hits.append((rel, signal, count, max_len))
    return sorted(hits)
