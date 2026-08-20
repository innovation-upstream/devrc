#!/usr/bin/env python3
"""Merge an UPDATE into an existing session-handoff doc, behind a real gate.

The write half of `/handoff` when a handoff doc for this topic ALREADY EXISTS —
step 2 of the skill writes a doc from scratch, and this is what runs the second
and every later time. (A missing base is handled rather than refused: the update
simply becomes the whole doc, so nothing here breaks if the skill ever routes a
first write through it too.) It exists because of a measured incident:

  A session re-entered work from a handoff, did ten minutes of genuinely
  valuable analysis — it answered the doc's open question AND corrected a prior
  misreading — and then wrote and PUSHED an updated handoff to a shared
  branch with no confirm gate at all. The operator never approved it.

Both skills involved were correct on their own terms. `/resume` is read-only by
contract and followed it. `/handoff` gates its *index* write ("Write only on
explicit confirm, diff first … on decline, discard"). The gap was underneath:
the handoff DOC's own write+push carried no equivalent gate, and a session
running after a resume inherited no constraint at all.

FOUR RULES, and this module is what makes three of them structural rather than
prose an agent can read and then not follow:

a. UPDATING IS NOT FORBIDDEN. The incident's update was correct and valuable;
   suppressing it costs the next session the ten minutes again. Optimising for
   doc stability over state accuracy is backwards. So this tool exists to make
   the update SAFE, not to make it rare — there is no "don't update" path here.

b. THE GATE IS ON THE PUSH, and it is the SAME gate shape `/handoff` already
   specifies for the index write: one compact unified diff, a single y/N, and
   on decline DISCARD. Structurally: the default mode writes NOTHING — not the
   doc, not a commit, not a ref — it only prints the diff a human is being
   asked to approve. Landing it takes a SECOND invocation carrying `--confirm`
   (and `--push`), which is the action that happens after the `y`. A decline is
   therefore not a code path that has to behave; it is the absence of one, and
   `TestDeclineWritesNothing` hashes the whole repo tree either side of a
   default-mode run to keep it that way.

c. THE STATUS HEADER IS REPLACED; THE FINDINGS APPEND. "State now" / "Next
   steps" / "How to verify" are current state and are overwritten. "Open
   investigations", "Findings" and "Gotchas / decisions / dead-ends" are the
   live diagnosis state the skill itself calls "the single highest-value part
   of the handoff", so an update APPENDS to them and the earlier text survives
   verbatim. The incident is the argument: that update *superseded* an earlier
   interpretation, and the value is seeing the prior reading was corrected —
   not finding it silently gone. Appending is deliberately dumb: a new block
   with the SAME heading as an old one still appends, because supersession is
   exactly the case worth keeping both halves of.

d. NO ADVANCE, NO OFFER. `--advanced` is a required, non-empty statement of
   what changed since the doc was written. Without it — or with one of the
   sentinels that MEAN nothing changed — this exits 4 having printed no diff at
   all. Not an empty diff, not a no-op commit: no offer. A resume that goes
   nowhere overwriting a good handoff is the worst case and the one nobody
   notices until they try to retry cleanly. The honesty of `--advanced` is on
   the caller, so there is a second, independent guard that does not depend on
   it: if the merge produces content equal to what is already on disk, that is
   exit 5 `no-change`, also with no diff and no commit.

WHAT IS OUT OF SCOPE HERE. Whether `/handoff` should push at all in a repo
whose trunk is the deploy branch is a per-repo policy question, not this
module's. It pushes only when asked to, only to the remote and branch it is
given, and only together with `--confirm`.

EXIT CODES
  0  proposed (diff shown, nothing written) — or written/pushed under --confirm
  2  usage
  3  operational failure (unreadable input, git refused) — nothing written
  4  no-advance      — rule (d), no diff printed
  5  no-change       — merge is a no-op, no diff printed, no empty commit
"""

from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
import typing
from pathlib import Path

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_FAIL = 3
EXIT_NO_ADVANCE = 4
EXIT_NO_CHANGE = 5
EXIT_BEHIND = 6
"""--push was asked for and the branch is BEHIND its remote. Nothing written.

🔴 This exists because the alternative is the state this repo has a rule against.
MEASURED 2026-08-15: `--confirm --push` committed the doc to `main` in a SHARED
base clone, then the push was rejected non-fast-forward because two other
sessions had pushed while the session worked. The commit stayed. An un-pushed
commit on `main` in a devrc checkout is exactly what `ship.sh` skips over —
silently, because `merge --ff-only` refuses and the host is left "as found" — so
that host stops receiving every future change while still looking healthy. It has
bitten this repo twice (2026-08-06, 2026-08-09).

Refusing BEFORE the write keeps the tool's existing property — a failure writes
nothing — instead of trading it for a commit the caller has to know how to undo.
"""

# Rule (c). A section whose heading starts with one of these is DIAGNOSIS STATE
# and appends; everything else is CURRENT STATE and is replaced. Matching is on
# a lowercased prefix, not the whole heading, because the canonical spellings
# carry a trailing gloss ("Open investigations — live diagnosis state") that an
# updating session will not reproduce character-for-character.
APPEND_PREFIXES: tuple[str, ...] = (
    "open investigations",
    "findings",
    "gotchas",
)

# Rule (d). Lowercased, stripped `--advanced` values that ASSERT no advance.
# A caller who types one of these has answered the question honestly and gets
# the same treatment as one who omitted the flag: no diff, no offer.
NO_ADVANCE_SENTINELS: frozenset[str] = frozenset(
    {
        "",
        "-",
        "--",
        ".",
        "n/a",
        "na",
        "nil",
        "no",
        "no change",
        "no changes",
        "none",
        "nothing",
        "nothing new",
        "nothing yet",
        "tbd",
        "unchanged",
        "unknown",
    }
)

_H2 = re.compile(r"^##\s+\S")
_FENCE = re.compile(r"^(`{3,}|~{3,})")


def _fence_token(line: str) -> str | None:
    """The fence run opening/closing a code block on this line, if any."""
    m = _FENCE.match(line.strip())
    return m.group(1) if m else None


def split_sections(text: str) -> tuple[str, list[list[str]]]:
    """(preamble, [[heading_line, body], ...]) — FENCE AWARE, and lossless.

    `preamble + "".join(h + b for h, b in sections) == text` exactly, which is
    what lets an untouched section stay byte-identical through a merge rather
    than being re-rendered into a diff nobody asked to approve.

    Fence awareness is not decoration: a handoff doc's step-2 template is a
    fenced markdown block full of `## ` lines, and treating those as real
    headings would shred the doc.
    """
    pre: list[str] = []
    sections: list[list[str]] = []
    open_tok: str | None = None
    for line in text.splitlines(keepends=True):
        tok = _fence_token(line)
        was_open = open_tok
        if open_tok is None:
            if tok:
                open_tok = tok
        elif (
            tok
            and tok[0] == open_tok[0]
            and len(tok) >= len(open_tok)
            and line.strip() == tok
        ):
            open_tok = None
        is_fence_line = tok is not None and (was_open is None or open_tok is None)
        if was_open is None and not is_fence_line and _H2.match(line):
            sections.append([line, ""])
        elif sections:
            sections[-1][1] += line
        else:
            pre.append(line)
    return "".join(pre), sections


def heading_text(heading_line: str) -> str:
    """`## Open investigations — live` -> `Open investigations — live`."""
    return heading_line.lstrip("#").strip()


def append_bucket(heading_line: str) -> str | None:
    """The APPEND_PREFIXES bucket this heading falls in, or None (= replace)."""
    low = heading_text(heading_line).lower()
    for prefix in APPEND_PREFIXES:
        if low.startswith(prefix):
            return prefix
    return None


def _norm_heading(heading_line: str) -> str:
    return " ".join(heading_text(heading_line).lower().split())


def merge(base_text: str, update_text: str) -> str:
    """Rule (c): replace current-state sections, APPEND diagnosis-state ones.

    A section present in the base and absent from the update is left ALONE —
    an update is a delta, not a replacement document, so omitting a section
    never deletes it. A section present only in the update is added at the end.
    """
    base_pre, base_secs = split_sections(base_text)
    upd_pre, upd_secs = split_sections(update_text)

    out_pre = upd_pre if upd_pre.strip() else base_pre
    out = [[h, b] for h, b in base_secs]

    by_bucket: dict[str, int] = {}
    by_heading: dict[str, int] = {}
    for i, (h, _b) in enumerate(out):
        bucket = append_bucket(h)
        if bucket is not None:
            by_bucket.setdefault(bucket, i)
        by_heading.setdefault(_norm_heading(h), i)

    tail: list[list[str]] = []
    for h, b in upd_secs:
        bucket = append_bucket(h)
        if bucket is not None and bucket in by_bucket:
            i = by_bucket[bucket]
            out[i][1] = _append_body(out[i][1], b)
        elif bucket is None and _norm_heading(h) in by_heading:
            i = by_heading[_norm_heading(h)]
            out[i][0] = h
            out[i][1] = _replace_body(out[i][1], b)
        else:
            tail.append([h, b])

    rendered = out_pre + "".join(h + b for h, b in out + tail)
    return rendered.rstrip("\n") + "\n"


def _spacing(body: str) -> str:
    """The run of newlines a section body ends with (at least one).

    Preserved across both merge operations so that a section's SPACING is not a
    change the caller has to approve: a replace that silently ate a blank line
    would put whitespace into a diff a human is being asked to read, and would
    make the no-change verdict below unreachable for a genuinely no-op update.
    """
    tail = body[len(body.rstrip("\n")) :]
    return tail or "\n"


def _replace_body(base_body: str, new_body: str) -> str:
    """New content, base spacing."""
    return new_body.rstrip("\n") + _spacing(base_body)


def _append_body(base_body: str, new_body: str) -> str:
    """Base body VERBATIM, then a blank line, then the new material.

    Only trailing newlines are touched — everything a past session wrote comes
    through character-for-character, which is the whole point of rule (c).
    """
    kept = base_body.rstrip("\n")
    added = new_body.strip("\n")
    if not added:
        return base_body
    if not kept:
        return added + _spacing(base_body)
    return kept + "\n\n" + added + _spacing(base_body)


def _canon(text: str) -> str:
    """Whitespace-insensitive form, for the no-change verdict only."""
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def unified(base_text: str, merged_text: str, relpath: str) -> str:
    """A compact unified diff with git-shaped headers, so it can be compared
    line-for-line against what `git show` prints for the resulting commit."""
    return "".join(
        difflib.unified_diff(
            base_text.splitlines(keepends=True),
            merged_text.splitlines(keepends=True),
            fromfile=f"a/{relpath}",
            tofile=f"b/{relpath}",
            n=3,
        )
    )


def advance_is_real(advanced: str | None) -> bool:
    """Rule (d), as a predicate — one place, so the CLI and the tests agree."""
    if advanced is None:
        return False
    return advanced.strip().lower() not in NO_ADVANCE_SENTINELS


class GitError(RuntimeError):
    pass


def resolve_branch(repo: Path, override: str | None) -> str:
    """The branch a push would land on. Resolved BEFORE the write, not after."""
    if override:
        return override
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch == "HEAD":
        raise GitError("detached HEAD and no --branch given; refusing to guess")
    return branch


def uncommitted_paths(repo: Path) -> list[str]:
    """Paths with uncommitted changes in `repo`'s working tree, staged or not.

    🔴 THIS DECIDES WHICH REMEDY THE `behind` MESSAGE OFFERS, and the hazard it
    measures is NOT "which repo is this". `merge --ff-only` into a tree holding
    someone else's uncommitted work either refuses or overwrites it, and in a
    shared clone that work is routinely not yours: measured in `$DATAPACKET`
    2026-08-19, **38 dirty paths** across at least three sessions while the clone
    sat **90 commits behind**. That repo's own rules forbid `commit`, `add`,
    `stash`, `checkout` and `switch` in the primary clone for exactly this reason.

    So the tool measures the tree instead of enumerating repos — an enumeration
    would be wrong for the next shared checkout nobody added to it.

    UNREADABLE ⇒ TREAT AS DIRTY. A tree we cannot inspect is one we must not
    recommend mutating; the fail-safe direction is the cautious remedy.

    🔴 `--no-optional-locks`, AND IT IS NOT OPTIONAL HERE. A plain `git status`
    REFRESHES THE INDEX and writes `.git/index` — on a shared gitdir that is a
    side effect on every other worktree, and this module already forbids exactly
    that. The existing no-write guard caught this the first time the check
    shipped, which is the guard doing its job. The flag makes the read take no
    lock and write nothing; the cost is that a stat-dirty file can be reported
    as modified when its content matches, which errs toward the cautious remedy
    and is the right direction for this decision.
    """
    try:
        out = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(repo), "status", "--porcelain"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ["<could not read the working tree>"]
    if out.returncode != 0:
        return ["<could not read the working tree>"]
    return [ln[3:].strip() for ln in out.stdout.splitlines() if ln.strip()]


def remote_has_commits_we_lack(repo: Path, remote: str, branch: str) -> bool:
    """Would a push to `<remote>/<branch>` be rejected non-fast-forward?

    🔴 `ls-remote`, NOT `fetch` — three measured reasons, all found by audit after
    the fetch version shipped:

      1. **`fetch` REINTRODUCED THE BUG THIS GUARD EXISTS TO CLOSE.** It wrote
         `FETCH_HEAD` and a second process then read `HEAD..FETCH_HEAD`.
         `FETCH_HEAD` is shared mutable state: any other fetch in that checkout
         between the two wins. Measured on a checkout genuinely 1 behind — after
         another session's `git fetch origin stable`, the check returned **0**.
         A confident zero here means write → commit → push rejected → a stranded
         commit on a shared branch. `drift-check.sh` fetches on a systemd timer,
         so the racer is unattended.
      2. **`fetch` is NOT read-only, and the earlier comment saying so was false.**
         It writes `refs/remotes/<remote>/<branch>` in the COMMON gitdir — shared
         by every worktree — plus objects and reflogs. Two concurrent
         `git fetch --quiet origin main` produced `cannot lock ref` in **30 of 30**
         trials. Fail-safe, but a new transient refusal of the handoff.
      3. **`fetch <remote> <branch>` FAILS when the branch is not on the remote
         yet**, which made a first push impossible — an ordinary end-of-session
         state, hard-refused with no way past it.

    `ls-remote` writes NOTHING locally (measured: added/changed/removed all empty)
    and 12/12 concurrent runs exited 0.

    Returns False — pushable — when the branch does not exist on the remote (a
    first push cannot be rejected non-fast-forward) and when the remote tip is an
    ancestor of HEAD (ahead-only). True when the remote has anything HEAD lacks,
    which covers behind AND diverged.

    🔴 A LOOKUP THAT FAILS IS NOT "PUSHABLE". Network down, no such remote, auth
    expired, or a remote tip this repo has never fetched — each RAISES, and the
    caller refuses rather than guessing. Guessing pushable strands the commit in
    exactly the way this whole guard exists to prevent.
    """
    out = git_allow(repo, "ls-remote", "--exit-code", remote, f"refs/heads/{branch}")
    if out.code == 2:
        return False  # no such branch on the remote — a first push
    if out.code != 0:
        raise GitError(
            f"cannot read {remote}/{branch}: {out.err.strip() or f'git exited {out.code}'}"
        )
    tip = out.out.split()[0] if out.out.split() else ""
    if not tip:
        raise GitError(f"{remote}/{branch} returned no sha")
    # 🔴 One check, not two. A `cat-file -e` guard for "a tip this repo has
    # never fetched" was here and was REDUNDANT: `merge-base --is-ancestor` on an
    # unknown object exits non-zero too, which is already the refuse answer. Two
    # branches reaching one outcome cannot be told apart by any test — deleting
    # either left the suite green — and that is the dead-predicate shape. The
    # fail-safe is the non-zero, so state it once:
    #   ancestor  -> 0     -> ahead-only, pushable
    #   not       -> 1     -> behind or diverged, refuse
    #   unknown / any error-> refuse (this is the property, not an accident)
    return git_allow(repo, "merge-base", "--is-ancestor", tip, "HEAD").code != 0


class GitRun(typing.NamedTuple):
    code: int
    out: str
    err: str


def git_allow(repo: Path, *args: str) -> GitRun:
    """`git` that RETURNS its exit code. `ls-remote --exit-code` uses 2 to mean
    "no such ref", which is an answer, not a failure — raising on it is what made
    a first push impossible."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    return GitRun(proc.returncode, proc.stdout, proc.stderr)


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout).strip()}"
        )
    return proc.stdout


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="handoff_doc.py",
        description="merge an update into an existing handoff doc, behind a confirm gate",
    )
    p.add_argument("--repo", required=True, help="repo root the handoff lives in")
    p.add_argument("--topic", required=True, help="handoff topic slug")
    p.add_argument(
        "--update",
        required=True,
        help="file holding the proposed sections (## headings, a delta not a whole doc)",
    )
    p.add_argument(
        "--advanced",
        help="one line: what changed since the doc was written. Required — "
        "without it, or with a value that means nothing changed, no diff is "
        "offered and nothing is written (rule d).",
    )
    p.add_argument(
        "--confirm",
        action="store_true",
        help="land it: write the doc and make exactly one commit of that path. "
        "Run this ONLY after a human answered y to the diff the default mode printed.",
    )
    p.add_argument(
        "--push",
        action="store_true",
        help="also push the commit. Requires --confirm; this is the half the gate exists for.",
    )
    p.add_argument("--remote", default="origin")
    p.add_argument("--branch", help="defaults to the repo's current branch")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    repo = Path(args.repo).resolve()
    if not (repo / ".git").exists():
        print(f"not a git repo: {repo}", file=sys.stderr)
        return EXIT_FAIL
    if args.push and not args.confirm:
        print(
            "--push requires --confirm: the push is the half the gate exists "
            "for, so it never happens without the confirmed write.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    relpath = f"claudedocs/handoff-{args.topic}.md"
    doc = repo / relpath

    # ---- rule (d): the advance question, asked BEFORE anything is computed ---
    if not advance_is_real(args.advanced):
        print(
            "status=no-advance\n"
            "This session did not state what changed since the handoff was "
            "written, so no update is offered: no diff, no write, no commit.\n"
            "  If state DID advance, re-run with --advanced '<what changed>'.\n"
            "  If it did not, say so plainly and write nothing — a handoff that "
            "still describes reality is not stale.",
            file=sys.stderr,
        )
        return EXIT_NO_ADVANCE

    try:
        update_text = Path(args.update).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read --update: {exc}", file=sys.stderr)
        return EXIT_FAIL

    base_text = doc.read_text(encoding="utf-8") if doc.exists() else ""
    merged_text = merge(base_text, update_text) if base_text else (
        update_text.rstrip("\n") + "\n"
    )

    if _canon(merged_text) == _canon(base_text):
        print(
            "status=no-change\n"
            f"The merge of {args.update} into {relpath} changes nothing. "
            "No diff, no commit — an empty commit is not a handoff update.",
            file=sys.stderr,
        )
        return EXIT_NO_CHANGE

    diff = unified(base_text, merged_text, relpath)
    print(f"doc: {relpath}")
    print(f"advanced: {args.advanced.strip()}")
    print(diff, end="" if diff.endswith("\n") else "\n")

    if not args.confirm:
        print("status=proposed")
        print(
            "NOTHING WRITTEN — not the doc, not a commit, not a ref. Ask exactly "
            "one `update the handoff doc and push it? (y/N)`.\n"
            "  y -> re-run this exact command with --confirm (add --push to land "
            "it on the shared branch, which is what the question asked about)\n"
            "  n -> discard: write nothing and run nothing else. The tree is "
            "already byte-identical."
        )
        return EXIT_OK

    if args.push:
        # 🔴 BEFORE the write, not after. See EXIT_BEHIND.
        try:
            push_branch = resolve_branch(repo, args.branch)
            behind = remote_has_commits_we_lack(repo, args.remote, push_branch)
        except (GitError, ValueError) as exc:
            print(
                f"status=failed\ncannot determine whether {args.remote} has moved, "
                f"so refusing to commit something that may not be pushable: {exc}\n"
                f"  If you only want the doc updated LOCALLY, re-run without "
                f"`--push` — the remote being unreachable does not make the local "
                f"write wrong.",
                file=sys.stderr,
            )
            return EXIT_FAIL
        if behind:
            # 🔴 The RESOLVED branch in every line. An earlier version printed
            # `HEAD` and a literal `<branch>`, so the recovery could not be
            # pasted — and this message is the entire second half of the fix.
            dirty = uncommitted_paths(repo)
            ff = f"git -C {repo} merge --ff-only {args.remote}/{push_branch}"
            head = (
                f"status=behind remote={args.remote} branch={push_branch}\n"
                f"NOTHING WRITTEN — not the doc, not a commit, not a ref.\n"
                f"  {args.remote}/{push_branch} has commit(s) this checkout does "
                f"not, so the push would be rejected and the commit would be left "
                f"behind on a shared branch. In a devrc checkout that is the state "
                f"that silently blocks `ship.sh`; elsewhere it is a stranded commit "
                f"on a branch other people push to.\n"
            )
            if dirty:
                # 🔴 A DIRTY TREE CHANGES THE REMEDY, and this is the branch that
                # matters. `merge --ff-only` here either refuses or overwrites work
                # that is very often NOT the caller's: measured in a shared clone
                # 2026-08-19, 38 dirty paths across three sessions at 90 behind.
                # Repos with a shared primary clone forbid mutating it at all, so
                # the tool must not print that command as if it were the fix.
                shown = ", ".join(sorted(dirty)[:4])
                more = f" (+{len(dirty) - 4} more)" if len(dirty) > 4 else ""
                print(
                    f"{head}"
                    f"  🔴 THIS CHECKOUT IS DIRTY — {len(dirty)} uncommitted "
                    f"path(s): {shown}{more}\n"
                    f"  DO NOT fast-forward it. Some or all of that work is "
                    f"probably another session's, and `merge --ff-only` would "
                    f"either refuse or overwrite it. Several repos forbid "
                    f"committing in a shared primary clone for exactly this "
                    f"reason.\n"
                    f"  Commit and push from a THROWAWAY WORKTREE off the remote "
                    f"branch instead, leaving this tree untouched:\n"
                    f"    git -C {repo} worktree add /tmp/handoff-wt "
                    f"{args.remote}/{push_branch}\n"
                    f"    # write the doc there, commit it path-limited, then:\n"
                    f"    git -C /tmp/handoff-wt push {args.remote} "
                    f"HEAD:{push_branch}\n"
                    f"  🔴 Remove the worktree only AFTER the push succeeds — "
                    f"removing it after a failed push deletes the branch ref and "
                    f"orphans the commit.\n"
                    f"  Verify by CONTENT, never ancestry: a squash merge never "
                    f"makes your head an ancestor of {push_branch}.",
                    file=sys.stderr,
                )
                return EXIT_BEHIND
            print(
                f"{head}"
                f"  This checkout is CLEAN, so a fast-forward is safe. Run it, "
                f"then re-run this exact command:\n"
                f"    {ff}\n"
                f"  🔴 If `--branch {push_branch}` is not the branch you are ON, "
                f"do NOT run that merge — it would merge an unrelated branch into "
                f"your checkout. Push from a checkout of {push_branch} instead.\n"
                f"  If the merge refuses, this checkout has DIVERGED — preserve, "
                f"verify, then move the pointer, in that order:\n"
                f"    git -C {repo} branch <topic> HEAD && git -C {repo} push -u "
                f"{args.remote} <topic>\n"
                f"    git -C {repo} ls-remote --heads {args.remote} <topic>\n"
                f"    git -C {repo} reset --keep {args.remote}/{push_branch}",
                file=sys.stderr,
            )
            return EXIT_BEHIND

    try:
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(merged_text, encoding="utf-8")
        git(repo, "add", "--", relpath)
        subject = f"docs(handoff): {args.advanced.strip().splitlines()[0]}"[:100]
        # Path-limited on purpose: exactly one commit, carrying exactly the
        # diff that was shown, even if the caller had other work staged.
        git(repo, "commit", "-m", subject, "--", relpath)
        sha = git(repo, "rev-parse", "HEAD").strip()
    except (GitError, OSError) as exc:
        print(f"status=failed\n{exc}", file=sys.stderr)
        return EXIT_FAIL

    print(f"status=written commit={sha}")

    if not args.push:
        return EXIT_OK

    try:
        git(repo, "push", args.remote, f"HEAD:refs/heads/{push_branch}")
    except GitError as exc:
        # The pre-check makes this rare, not impossible: the remote can move in
        # the window between them. The commit EXISTS at this point, so say so and
        # hand over the recovery — a caller who is not told is a caller who
        # leaves a shared branch diverged.
        print(
            f"status=push-failed\n{exc}\n"
            f"🔴 THE COMMIT {sha[:12]} EXISTS LOCALLY on `{push_branch}` "
            f"and is NOT on {args.remote}. On a shared branch that is the state "
            f"`ship.sh` skips over silently.\n"
            f"  Preserve, verify, then move the pointer — in that order:\n"
            f"    git -C {repo} branch <topic> HEAD && git -C {repo} push -u "
            f"{args.remote} <topic>\n"
            f"    git -C {repo} ls-remote --heads {args.remote} <topic>   # confirm it landed\n"
            f"    git -C {repo} reset --keep {args.remote}/{push_branch}   # --keep refuses rather than destroys",
            file=sys.stderr,
        )
        return EXIT_FAIL

    print(f"status=pushed remote={args.remote} branch={push_branch}")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
