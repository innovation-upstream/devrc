#!/usr/bin/env python3
"""Extract the genuinely user-TYPED messages from a SCOPED set of sessions.

WHAT THIS IS FOR
----------------
Reading back what the operator actually asked for across one effort — the
kickoff, the corrections, the "no, do X instead" — without the model's replies,
the tool output, or the harness's injected boilerplate in the way.

🔴 THE SCOPE IS THE POINT, AND IT USED NOT TO EXIST. Until 2026-09-25 this
script took one argument (an output path), walked the WHOLE corpus, and wrote
records of `{project, kind, text}`. MEASURED on this host that same day: 974
transcripts, 13,768 records, **54.1 MiB**, 12 s. The records carried NO session
id, so the obvious compose — enumerate an arc's sessions with
`find-session.py --arc … --json`, extract corpus-wide, then grep the ids out —
could not be completed at all: there was nothing to grep on. The only available
filter was `project`, a cwd basename, which for devrc work selects the entire
devrc corpus. So the answer to "what did I ask for across this arc" was 54 MiB
of everything, hand-read.

The selectors below answer it directly. `--arc` resolves the same session chain
`find-session.py --arc` prints, and extracts only those sessions.

🔴 THE ARC RESOLVER IS IMPORTED, NEVER RE-IMPLEMENTED. `find-session.arc_report`
owns the four steps (find the repo holding the doc, walk the corpus for reader
sessions, `handoff_arc.resolve_arc`, then append the note naming the corpus that
walk did NOT search). Each is a claim about coverage; a second spelling here
would publish a chain that looks complete and is not — and the two tools would
then answer "which sessions worked this doc" with different sets and neither
would say so.

🔴 A ZERO IS NEVER REPORTED AS A ZERO. `--arc typo-in-the-name` and an arc that
genuinely has no sessions both produce "nothing extracted"; so do "the ids
resolved to no transcript" and "the transcripts held no typed message". Those
are four different facts with four different fixes, so they get four different
exit codes and a reason on stderr. See `EXIT_CONTRACT`.

HOW THE CORPUS IS REACHED — ONE RULE, ONE PLACE
-----------------------------------------------
Both paths go through `scripts/lib/transcript_search.py`: selected sessions via
`find_transcript` (the by-id lookup), the unscoped walk via `iter_transcripts`.
Both apply `is_corpus_member`, so a `subagents/` transcript is excluded either
way — a subagent is not a session anybody typed into.

⚠ RETRACTED 2026-09-25, DO NOT RE-DERIVE: this file used to keep a PRIVATE glob
here "because the unscoped walk wants EVERY jsonl including `subagents/`". That
sentence was false in four places at once — here, on `corpus_paths`, in the
`JSONL_GLOB_SITES` ledger, and in the line the tool PRINTED on every unscoped
run — and the private walk had never included a single subagent transcript
(measured: 0 of 5,681). See `corpus_paths` for the mechanism.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))

from transcript_search import find_transcript, iter_transcripts  # noqa: E402

ROOT = os.path.expanduser("~/.claude/projects")

# --------------------------------------------------------------------------- #
# EXIT CODES
# --------------------------------------------------------------------------- #
EXIT_OK = 0
EXIT_USAGE = 2
#: `--arc` named nothing measurable: the seed resolved to no handoff doc, or no
#: repo handle holds that doc. 🔴 NOT an empty arc.
EXIT_ARC_UNMEASURED = 3
#: `--arc` resolved a real doc and the arc WAS measured — and it has no member
#: sessions. A measured emptiness, which is a finding rather than a failure.
EXIT_ARC_EMPTY = 4
#: Session ids were selected and NONE of them resolved to a transcript in this
#: host's corpus. The ids may be from the other host, or pruned.
EXIT_NO_TRANSCRIPTS = 5
#: Transcripts were read and yielded zero user-typed messages after filtering.
EXIT_NO_MESSAGES = 6

EXIT_CONTRACT = (
    (EXIT_OK, "messages were extracted"),
    (EXIT_USAGE, "bad invocation — a flag conflict, or an --ids-file that "
                 "could not be read. Nothing was searched."),
    (EXIT_ARC_UNMEASURED,
     "`--arc` ONLY: the seed named no handoff doc, or no $DEVRC/$HOMELAB/"
     "$DATAPACKET/$CIVITAI checkout holds it. 🔴 NOTHING WAS MEASURED — this "
     "is not an empty arc, and a wrong name lands here, not on exit 4."),
    (EXIT_ARC_EMPTY,
     "`--arc` ONLY: the doc resolved and the arc was measured, and it has ZERO "
     "member sessions. A MEASURED empty arc — a real finding about the doc, "
     "not a typo in your argument."),
    (EXIT_NO_TRANSCRIPTS,
     "session ids were selected but NONE resolved to a transcript in "
     "~/.claude/projects on this host. Every unresolved id is named on stderr. "
     "Check the peer host before concluding the sessions are gone."),
    (EXIT_NO_MESSAGES,
     "transcripts WERE read and held zero user-typed messages after filtering "
     "— the sessions exist and are empty of typed input (agent-driven, or "
     "every message was harness boilerplate)."),
)

# --------------------------------------------------------------------------- #
# NOISE FILTERING — what is user-TYPED and what is harness boilerplate
# --------------------------------------------------------------------------- #
# ⚠ `scripts/collector/claude/tailer.py` carries a PORT of these predicates on
# purpose (it deploys as a standalone copy with no `scripts/lib` beside it). If
# you change one, read the other.
SYS_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
COMMAND_STDOUT = re.compile(r"<local-command-stdout>.*?</local-command-stdout>", re.S)
COMMAND_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.S)
COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)

#: Prefixes the harness writes into a `user` record that nobody typed.
BOILERPLATE_PREFIXES = (
    "[Request interrupted",
    "Caveat: The messages below",
    "API Error",
    "API request failed",
)


def clean_text(t):
    t = SYS_REMINDER.sub("", t)
    t = COMMAND_STDOUT.sub("", t)
    return t.strip()


def extract_from_content(content):
    """Every raw text string a `user` record's content carries.

    Returns strings, not `(kind, text)` pairs: the kind is decided by
    `message_of` from the text itself, and a provisional kind here was a second
    place the same decision appeared to be made.
    """
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    # ignore tool_result, image, tool_use, thinking
    return [b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"]


def message_of(raw):
    """`(kind, text)` for one raw content string, or None when it is not typed.

    Factored out of the walk so the filtering can be tested without a corpus —
    it is the whole reason this tool's output is readable, and it had no test of
    its own while it lived inline.
    """
    if not raw:
        return None
    cmd = COMMAND_NAME.search(raw)
    if cmd:
        cargs_m = COMMAND_ARGS.search(raw)
        text = (cmd.group(1).strip() + " " +
                (cargs_m.group(1).strip() if cargs_m else "")).strip()
        return ("command", text) if text else None
    txt = clean_text(raw)
    if not txt:
        return None
    # A bare tag left over after the substitutions above, not prose.
    if txt.startswith("<") and txt.endswith(">") and len(txt) < 80:
        return None
    if txt.startswith(BOILERPLATE_PREFIXES):
        return None
    return ("typed", txt)


def records_of(path, session_id=None):
    """Yield `{session_id, project, ts, kind, text}` for one transcript file.

    `project` is always derived from the path — it had a parameter that no
    caller and no test ever passed.
    """
    path = Path(path)
    session_id = session_id if session_id is not None else path.stem
    project = path.parent.name
    with open(path, errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("type") != "user" or obj.get("isMeta"):
                continue
            # sidechain == a subagent's own transcript, not user-typed
            if obj.get("isSidechain"):
                continue
            msg = obj.get("message") or {}
            if msg.get("role") != "user":
                continue
            for raw in extract_from_content(msg.get("content")):
                got = message_of(raw)
                if got is None:
                    continue
                kind, text = got
                yield {"session_id": session_id, "project": project,
                       "ts": obj.get("timestamp") or "", "kind": kind,
                       "text": text}


# --------------------------------------------------------------------------- #
# SELECTORS
# --------------------------------------------------------------------------- #
def _load_find_session():
    """Import `scripts/find-session.py` — hyphenated, so by path.

    Same mechanism `scripts/check-clickup-addressed/check-addressed.py` uses to
    reach its sibling, and for the same reason: the predicate is ONE rule and a
    copy is a second thing to drift.
    """
    spec = importlib.util.spec_from_file_location(
        "_find_session", SCRIPTS / "find-session.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ArcUnresolved(RuntimeError):
    """The `--arc` seed named nothing measurable. Carries the operator sentence."""


def arc_sessions(seed, root=None, find_session=None):
    """`(members, notes)` for an arc seed — imported resolution, never a copy.

    `members` is a list of `handoff_arc.ArcMember` in the order the chain runs.
    `notes` is every coverage caveat the resolver produced, which the caller
    MUST surface: a chain rendered without them reads as complete.

    Raises `ArcUnresolved` when nothing was measured. An EMPTY `members` from a
    doc that DID resolve is returned normally — that is a measured fact.
    """
    fs = find_session or _load_find_session()
    if root is not None:
        fs.ROOT = root
    basename = fs.arc_seed_to_doc(seed, root=root)
    if not basename:
        raise ArcUnresolved(
            f"--arc {seed!r} names no handoff doc. A slug/basename/path is "
            "taken directly; a SESSION ID is resolved by reading that "
            "session's opening message, and that message named no "
            "`claudedocs/handoff-*.md`. 🔴 Nothing was measured.")
    try:
        report = fs.arc_report(basename)
    except fs.ArcUnmeasured as exc:
        raise ArcUnresolved(f"--arc {seed!r}: {exc}") from exc
    notes = [f"arc {report.doc} (repo {report.repo or 'unknown'})",
             # 🔴 ALWAYS, INCLUDING WHEN IT IS ZERO — an absent coverage line is
             # indistinguishable from "nothing missing".
             fs.handoff_arc.coverage_line(report)]
    notes.extend(report.unmeasured_notes)
    return list(report.members), notes


def read_ids_file(path):
    """One session id per line; `-` is stdin. Blank lines and `#` comments skip.

    Raises `OSError` — the caller turns that into exit 2, because an ids file
    that could not be read must never degrade into "no ids selected".
    """
    if path == "-":
        text = sys.stdin.read()
    else:
        text = Path(path).read_text(errors="replace")
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def resolve_sessions(ids, root=None):
    """`(resolved, missing)` — `resolved` is `[(session_id, path)]`, order kept.

    🔴 `missing` IS RETURNED, NOT DROPPED. An id that names no transcript on
    this host is a fact about THIS host (the session may live on the peer, or
    have been pruned), and silently narrowing the set to what happened to
    resolve is how a partial extraction reads as a whole one.
    """
    resolved, missing, seen = [], [], set()
    for sid in ids:
        if sid in seen:
            continue
        seen.add(sid)
        path = find_transcript(sid, root=root if root is not None else ROOT)
        if path is None:
            missing.append(sid)
        else:
            resolved.append((sid, Path(path)))
    return resolved, missing


def corpus_paths(root=None):
    """Every session transcript in the corpus — the unscoped walk.

    🔴 THE SHARED ENUMERATOR, NOT A GLOB OF OUR OWN, AND THE PRIVATE ONE THIS
    REPLACED CARRIED A FALSE REASON FOR EXISTING. It open-coded exactly the two
    rules `transcript_search.is_corpus_member` owns — skip a `subagents` dir,
    skip a `wf_` prefix — while `JSONL_GLOB_SITES`, this function's docstring,
    the module docstring and the line the tool PRINTS on every unscoped run all
    said it kept its own walk because it "wants EVERY jsonl including
    subagents". MEASURED 2026-09-25: of 5,681 transcripts whose parent dir IS
    `subagents`, the private walk included **0**. It had never included one; a
    real subagent transcript sits at `<project>/<id>/subagents/agent-*.jsonl`,
    so its immediate parent is literally `subagents` and the very check that
    was supposed to let them through is what dropped them.

    Worse, the private copy was the NARROWER of the two: it tested only
    `path.parent.name`, where `is_corpus_member` tests every parent part. A
    transcript nested one level below a `subagents/` dir passed the private
    check and fails the shared one — so routing here both deletes a duplicated
    predicate and fixes the direction it was wrong in.

    The two-way `JSONL_GLOB_SITES` ledger entry and the `scripts/README.md`
    prose row went with it, which is what that ledger is for.
    """
    return list(iter_transcripts(root=root if root is not None else ROOT))


# --------------------------------------------------------------------------- #
# RENDERING
# --------------------------------------------------------------------------- #
def render_jsonl(rows, out):
    for r in rows:
        out.write(json.dumps(r, ensure_ascii=False) + "\n")


def render_markdown(rows, out, title, notes=(), roles=None):
    """Grouped by session, newest-typed-first WITHIN a session preserved.

    Markdown is the DEFAULT because the answer is read by a human or pasted
    into a context window; `--jsonl` is the canonical machine form and carries
    strictly more (every key, un-truncated).
    """
    roles = roles or {}
    by_session = {}
    for r in rows:
        by_session.setdefault(r["session_id"], []).append(r)
    out.write(f"# {title}\n\n")
    out.write(f"{len(by_session)} session(s), {len(rows)} message(s)\n\n")
    for note in notes:
        out.write(f"> {note}\n")
    if notes:
        out.write("\n")
    for sid, group in by_session.items():
        bits = [sid]
        if roles.get(sid):
            bits.append(roles[sid])
        bits.append(f"{len(group)} message(s)")
        out.write(f"## {' · '.join(bits)}\n\n")
        for i, r in enumerate(group, 1):
            stamp = (r.get("ts") or "")[:19].replace("T", " ") or "time UNMEASURED"
            out.write(f"### {i}. {stamp} · {r['kind']}\n\n")
            out.write(r["text"].rstrip() + "\n\n")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
EPILOG = """\
selectors (combinable; with NONE of them the WHOLE corpus is walked)

  --arc SEED            every session in a handoff doc's arc. SEED is a slug,
                        a basename, a path, or a SESSION ID whose opening
                        message names the doc.
      extract_user_msgs.py --arc handoff-cairn-phase3
      extract_user_msgs.py --arc claudedocs/handoff-cairn-phase3.md
      extract_user_msgs.py --arc 8951d8f0-1064-4113-aae8-c9913f5ef5cb

  --session ID          one session. Repeatable.
      extract_user_msgs.py --session 8951d8f0-1064-4113-aae8-c9913f5ef5cb
      extract_user_msgs.py --session <id-a> --session <id-b>

  --ids-file PATH       one id per line; `#` comments and blanks skipped.
                        `-` reads stdin.
      extract_user_msgs.py --ids-file ./ids.txt
      find-session.py --arc handoff-foo --json \\
        | jq -r '.members[].session_id' \\
        | extract_user_msgs.py --ids-file -

output

  (default)             markdown, grouped by session, to stdout
  --jsonl               one canonical record per line:
                        {session_id, project, arc_role, ts, kind, text}
  -o PATH               write to PATH instead of stdout

exit codes — a zero cannot distinguish a wrong name from an empty arc, so
each reason has its own code and prints on stderr:

  0  messages were extracted
  2  bad invocation; nothing was searched
  3  --arc: NOTHING MEASURED (seed names no doc / no checkout holds it)
  4  --arc: the arc was MEASURED and has zero member sessions
  5  ids were selected and none resolved to a transcript on this host
  6  transcripts were read and held zero user-typed messages
"""


def build_parser():
    p = argparse.ArgumentParser(
        prog="extract_user_msgs.py",
        description="Extract the user-TYPED messages from a scoped set of "
                    "Claude Code sessions (an arc, named sessions, or ids on "
                    "stdin).",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--arc", metavar="SEED",
                   help="every session in a handoff doc's arc (slug, path, or "
                        "a session id that opened with the doc)")
    p.add_argument("--session", action="append", default=[], metavar="ID",
                   help="one session id; repeatable")
    p.add_argument("--ids-file", metavar="PATH",
                   help="file of session ids, one per line; `-` reads stdin")
    p.add_argument("--jsonl", action="store_true",
                   help="canonical JSONL instead of markdown")
    p.add_argument("-o", "--out", metavar="PATH",
                   help="write here instead of stdout")
    p.add_argument("--no-dedup", action="store_true",
                   help="keep every repeat; by default an identical message "
                        "seen twice in the selection is emitted once and the "
                        "suppressed count is reported")
    p.add_argument("--root", default=None,
                   help=argparse.SUPPRESS)   # tests point this at a fixture
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    err = sys.stderr
    root = a.root

    ids, roles, notes = [], {}, []
    scoped = bool(a.arc or a.session or a.ids_file)

    if a.arc:
        try:
            members, arc_notes = arc_sessions(a.arc, root=root)
        except ArcUnresolved as exc:
            print(str(exc), file=err)
            return EXIT_ARC_UNMEASURED
        notes.extend(arc_notes)
        if not members:
            for note in arc_notes:
                print(f"! {note}", file=err)
            print(f"--arc {a.arc!r}: the doc resolved and its arc WAS measured "
                  "— it has zero member sessions. 🔴 This is a MEASURED empty "
                  "arc, not a typo in your argument (that is exit "
                  f"{EXIT_ARC_UNMEASURED}).", file=err)
            return EXIT_ARC_EMPTY
        for m in members:
            ids.append(m.session_id)
            roles[m.session_id] = m.role

    ids.extend(a.session)
    if a.ids_file:
        try:
            ids.extend(read_ids_file(a.ids_file))
        except OSError as exc:
            print(f"--ids-file {a.ids_file!r}: {exc}", file=err)
            return EXIT_USAGE
        if not ids:
            print(f"--ids-file {a.ids_file!r} held no session ids. Nothing was "
                  "searched.", file=err)
            return EXIT_USAGE

    if scoped:
        resolved, missing = resolve_sessions(ids, root=root)
        if missing:
            # Printed on SUCCESS too — a partial extraction that does not say
            # so reads as a whole one.
            print(f"! {len(missing)} of {len(ids)} selected session(s) have no "
                  "transcript on this host (peer host? pruned?): "
                  + " ".join(missing), file=err)
            notes.append(f"{len(missing)} of {len(ids)} selected session(s) "
                         "were NOT readable on this host: " + " ".join(missing))
        if not resolved:
            print(f"none of the {len(ids)} selected session id(s) resolved to a "
                  "transcript in ~/.claude/projects on this host. 🔴 Nothing "
                  "was read — this is not 'the sessions are empty' (that is "
                  f"exit {EXIT_NO_MESSAGES}).", file=err)
            return EXIT_NO_TRANSCRIPTS
        sources = resolved
        title = (f"user messages — arc {a.arc}" if a.arc
                 else f"user messages — {len(resolved)} selected session(s)")
    else:
        sources = [(p.stem, p) for p in corpus_paths(root=root)]
        title = f"user messages — WHOLE corpus ({len(sources)} transcripts)"
        notes.append(f"UNSCOPED: every session transcript ({len(sources)}) — "
                     "subagent transcripts are NOT included. Pass "
                     "--arc/--session/--ids-file to scope it.")

    rows, seen, suppressed, unreadable = [], set(), 0, []
    for sid, path in sources:
        try:
            for rec in records_of(path, session_id=sid):
                if not a.no_dedup:
                    # 🔴 `kind` IS PART OF THE IDENTITY. A `/handoff` typed as
                    # prose and a `/handoff` slash command are two different
                    # events with the same text; a key that omits `kind` keeps
                    # whichever the walk reached first and silently drops the
                    # other. MEASURED over a frozen 974-transcript list: 7 rows
                    # the previous implementation kept vanished this way.
                    key = hashlib.md5(
                        "\x1f".join((rec["project"], rec["kind"],
                                     rec["text"])).encode()).hexdigest()
                    if key in seen:
                        suppressed += 1
                        continue
                    seen.add(key)
                rec["arc_role"] = roles.get(sid)
                rows.append(rec)
        except OSError as exc:
            unreadable.append(f"{sid}: {exc}")

    if unreadable:
        print(f"! {len(unreadable)} transcript(s) could not be read: "
              + "; ".join(unreadable), file=err)
        notes.append(f"{len(unreadable)} transcript(s) could not be read")

    if not rows:
        print(f"read {len(sources)} transcript(s) and found zero user-typed "
              "messages after filtering. 🔴 The transcripts WERE read — this "
              "is a measured emptiness, not an unresolved selector.", file=err)
        return EXIT_NO_MESSAGES

    # Chronological across sessions, file order preserved within one.
    rows.sort(key=lambda r: (r["ts"] == "", r["ts"]))

    out = open(a.out, "w") if a.out else sys.stdout
    try:
        if a.jsonl:
            render_jsonl(rows, out)
        else:
            render_markdown(rows, out, title, notes=notes, roles=roles)
    except BrokenPipeError:
        # 🔴 `… | head` / `… | jq | head` closes stdout mid-write, and exiting
        # quietly is the Unix contract — this tool's own --help and its
        # reference doc BOTH show piped usage, so the traceback was reachable
        # from the documented invocation.
        #
        # TWO failures, one symptom, and BOTH lines below are pinned by
        # `TestPipingToHead`. This `except` handles the failed WRITE; the `dup2`
        # handles CPython retrying the flush AT SHUTDOWN, which prints
        # "Exception ignored … BrokenPipeError". Mutating either turns that
        # class red — MEASURED over 20 runs: dup2 deleted => 20/20 red, dup2
        # present => 0/20 red.
        #
        # ⚠ AN EARLIER REVISION OF THIS COMMENT SAID THE OPPOSITE — "NOT pinned,
        # a mutant deleting this line SURVIVES the suite" — and that was wrong
        # in the dangerous direction: it invited a maintainer to delete a
        # load-bearing line, and told anyone who did to disregard the red. The
        # error was not the measurement but its SCOPE: the single SURVIVED draw
        # was taken against the WEAKER fixture this test had before it grew
        # `CUT_POINTS` and 4 KB rows, and the claim was then written as if it
        # applied to the strengthened one. A mutation result is a fact about the
        # test AS IT STOOD; re-run the sweep after touching a fixture.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return EXIT_OK
    finally:
        if a.out:
            out.close()

    print(f"sessions={len(sources)} msgs={len(rows)} "
          f"deduped={suppressed} out={a.out or '-'}", file=err)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
