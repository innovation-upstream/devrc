#!/usr/bin/env python3
"""mention-open — Alacritty hint handler for clawgate / GitHub / ClickUp mentions.

Alacritty runs a hint's `command` with the matched text appended as the LAST
argument (after any configured `args`). That is the whole contract, and it is why
`sys.argv[-1]` — not `sys.argv[1]` — is the text.

    hints.enabled = [{ command = ".../mention-open.py", regex = "…", … }]

The matched text is re-scanned here with `scripts/collector/mention_scan.py`, the
SAME module the telemetry emitter uses, so what the terminal underlines and what
gets opened can never disagree. The Alacritty regex is deliberately LOOSER than
the scanner (Rust's regex crate has no lookaround, so the trailing-digit guard
that rejects `#282828` cannot be expressed there) — this script is the strict
authority, and a match the scanner rejects opens nothing.

RESOLUTION
----------
  1 openable candidate   -> xdg-open it.
  2+ (a bare `#N`)       -> rofi picker, one row per platform, showing the URL.
  0                      -> a desktop notification saying why. Never a guess.

🔴 NOTHING IS GUESSED. A GitHub reference needs an owner, and an owner that is
wrong points confidently at a real-but-unrelated issue. An owner is only ever
accepted from a source that NAMES this exact repo:

  1. an explicit `owner/repo` in the clicked text itself;
  2. `scripts/collector/known_repos.py` — a generated mapping of every repo the
     operator can see (`scripts/regen-known-repos.sh`; keys carry both the
     canonical and the lowercased spelling, because the lookup is an exact dict
     hit and GitHub names are case-insensitive);
  3. the git remote of a real local checkout under `~/workspace` (MEASURED, and
     it OVERRIDES 2 — the checkout is authoritative about its own owner);
  4. last resort, a GitHub search restricted to an EXACT name match.

When none of them answers, the candidate has no URL and simply does not appear
in the picker. A near-miss is never promoted to an answer.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "collector"))

from mention_scan import (  # noqa: E402
    GITHUB_ISSUE_URL,
    PLATFORM_CLAWGATE,
    PLATFORM_CLICKUP,
    PLATFORM_GITHUB,
    scan_mention_spans,
)
from known_repos import KNOWN_REPOS  # noqa: E402

# Where local checkouts live. Only used to READ git remotes — the mapping it
# produces is measured, never invented.
WORKSPACE = Path(os.environ.get("DEVRC_WORKSPACE", Path.home() / "workspace"))

# rofi theme — the same one nix/i3/config.nix already uses for the app launcher,
# so the picker looks like every other picker on this desktop.
ROFI_THEME = "gruvbox-dark-hard"

PLATFORM_LABEL = {
    PLATFORM_CLAWGATE: "clawgate task",
    PLATFORM_GITHUB: "github",
    PLATFORM_CLICKUP: "clickup",
}


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested without touching git, tmux, rofi or a browser)
# --------------------------------------------------------------------------- #
_SSH_REMOTE = re.compile(r"^(?:ssh://)?git@[^:/]+[:/](?P<path>.+?)(?:\.git)?/?$")
_HTTP_REMOTE = re.compile(r"^https?://[^/]+/(?P<path>.+?)(?:\.git)?/?$")


def parse_owner_repo(remote_url: str) -> str:
    """`owner/repo` from a git remote URL, or "" when it is not a GitHub-shaped
    two-segment path.

    Accepts the three forms that actually occur here — `git@host:owner/repo.git`,
    `ssh://git@host/owner/repo`, `https://host/owner/repo.git` — and refuses
    anything that does not resolve to exactly two path segments, because a
    one-segment or three-segment path would produce a URL that 404s while
    looking authoritative.
    """
    url = (remote_url or "").strip()
    if not url:
        return ""
    m = _SSH_REMOTE.match(url) or _HTTP_REMOTE.match(url)
    if not m:
        return ""
    parts = [p for p in m.group("path").split("/") if p]
    if len(parts) != 2:
        return ""
    return f"{parts[0]}/{parts[1]}"


def openable(span: dict) -> list[dict]:
    """The span's candidates that actually carry a URL, in display order."""
    return [c for c in span.get("candidates", []) if c.get("url")]


def picker_rows(candidates: list[dict]) -> list[str]:
    """One `TAB`-free display row per candidate: `<platform> <id> — <url>`.

    The URL is IN the row on purpose: the whole point of the picker is that the
    operator can see which of two plausible references they are about to open,
    and the platform name alone does not tell them that.
    """
    rows = []
    for c in candidates:
        label = PLATFORM_LABEL.get(c["platform"], c["platform"])
        rows.append(f"{label} {c['id']} — {c['url']}")
    return rows


def row_to_url(row: str, candidates: list[dict]) -> str:
    """Map a picker row back to its URL. Matches on the URL suffix rather than
    the row index, so a rofi build that decorates or reorders rows cannot open
    the wrong one."""
    row = (row or "").strip()
    if not row:
        return ""
    for c in candidates:
        if row.endswith(c["url"]):
            return c["url"]
    return ""


# --------------------------------------------------------------------------- #
# Measurement (the impure half)
# --------------------------------------------------------------------------- #
def _git(args: list[str], cwd: str | None = None) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                           text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def repo_of_checkout(path: Path) -> str:
    """`owner/repo` for a checkout, from its `origin` remote. "" if unreadable."""
    return parse_owner_repo(_git(["remote", "get-url", "origin"], cwd=str(path)))


_GITHUB_API_CACHE: dict[str, str] = {}


def _gh_api_repo_search(name: str) -> dict[str, str]:
    """{name: "owner/repo"} for the first repo whose NAME equals `name`
    (case-insensitively), searched via the GitHub API. {} when nothing matches.

    🔴 THE NAME MATCH IS DONE HERE, NOT IN THE jq PROGRAM. `gh api --jq` takes
    no `--arg`, so a name written into the filter has to be interpolated as a
    string literal — which is how the original filter came to compare against
    the literal text `"$name"` and match NOTHING, ever, for any input. The jq
    program is now a constant with no interpolation, and the whole class is gone.

    The search is relevance-ordered, so the first row is frequently a
    near-miss (`kubernetes` -> someone's `kubernetes-examples`): the exact-name
    filter below is what stops a click opening a stranger's repo.
    """
    if name in _GITHUB_API_CACHE:
        return {name: _GITHUB_API_CACHE[name]}
    try:
        r = subprocess.run(
            ["gh", "api", "search/repositories", "--method", "GET",
             "-f", f"q={name} in:name", "--jq", ".items[].full_name"],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return {}
    if r.returncode != 0:
        return {}
    want = name.lower()
    for line in r.stdout.splitlines():
        full = line.strip()
        if "/" in full and full.rsplit("/", 1)[-1].lower() == want:
            _GITHUB_API_CACHE[name] = full
            return {name: full}
    return {}


def discover_repos(workspace: Path = WORKSPACE) -> dict:
    """{repo name: "owner/repo"} for resolution. Combines:
    1. Static mapping of all repos the operator has access to (instant)
    2. Local checkout remotes from ~/workspace/ (measured, overrides static)
    3. GitHub API fallback for repos not in the static list (last resort)
    """
    # Start with the static mapping — covers all known repos instantly.
    out: dict[str, str] = dict(KNOWN_REPOS)

    # Overlay local checkout remotes. These take precedence: a local checkout
    # is the most authoritative source for its own owner/repo.
    try:
        entries = sorted(p for p in workspace.iterdir() if p.is_dir())
    except OSError:
        return out
    for entry in entries:
        if not (entry / ".git").exists():
            continue
        full = repo_of_checkout(entry)
        if full:
            out[entry.name] = full
    return out


def tmux_pane_repo() -> str:
    """`owner/repo` for the tmux pane the operator was most recently in, or "".

    ⚠ BEST-EFFORT, AND THE CAVEAT IS REAL. This handler is spawned DETACHED by
    Alacritty; it does not inherit the clicked pane's cwd, and there is no
    Alacritty-side channel that carries it. `tmux display-message` answers for
    the MOST RECENTLY ACTIVE CLIENT, which is not guaranteed to be the pane whose
    text was clicked — a second Alacritty window, or a pane switched since the
    text scrolled past, both make it wrong.

    So this is used ONLY to give a bare `#N` a GitHub candidate it would
    otherwise not have. It never overrides an explicit `owner/repo` in the text,
    and it never suppresses the clawgate candidate: a wrong guess here shows up
    as an extra row in the picker, never as the wrong page opening.
    """
    path = ""
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-p", "-F", "#{pane_current_path}"],
            capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            path = r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    if not path or not os.path.isdir(path):
        return ""
    root = _git(["rev-parse", "--show-toplevel"], cwd=path)
    return repo_of_checkout(Path(root)) if root else ""


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #
def notify(summary: str, body: str = "") -> None:
    """Best-effort desktop notification. A handler that fails SILENTLY is
    indistinguishable from one that was never wired up, which is exactly the
    silent-zero shape this repo keeps paying for — so a refusal is always
    announced somewhere."""
    print(f"mention-open: {summary} {body}".strip(), file=sys.stderr)
    try:
        subprocess.run(["notify-send", "-a", "mention-open", summary, body],
                       timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def open_url(url: str) -> int:
    try:
        subprocess.Popen(["xdg-open", url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        notify("could not open the link", f"{type(exc).__name__}: {exc}")
        return 1
    return 0


def pick(candidates: list[dict]) -> str:
    """Ask rofi which candidate to open. Returns the chosen URL, or "" if the
    operator dismissed the picker (which must open NOTHING)."""
    rows = picker_rows(candidates)
    try:
        r = subprocess.run(
            ["rofi", "-dmenu", "-i", "-p", "mention", "-theme", ROFI_THEME,
             "-format", "s", "-no-custom"],
            input="\n".join(rows), capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        notify("could not show the mention picker",
               f"{type(exc).__name__}: {exc}")
        return ""
    if r.returncode != 0:
        return ""  # dismissed — not an error
    return row_to_url(r.stdout, candidates)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Resolve and open a clawgate / GitHub / ClickUp mention.")
    p.add_argument("--print", dest="print_only", action="store_true",
                   help="print the resolved URL instead of opening it (and "
                        "print every candidate when ambiguous)")
    p.add_argument("--no-discovery", action="store_true",
                   help="do not read git remotes or ask tmux — resolve only "
                        "what the text itself carries")
    p.add_argument("--default-repo", default="",
                   help="owner/repo to use for a bare #N (overrides tmux)")
    p.add_argument("text", nargs="+",
                   help="the matched text; Alacritty appends it LAST")
    return p


def resolve(text: str, *, repos: dict | None = None,
            default_repo: str | None = None) -> tuple[dict | None, list[dict]]:
    """(span, openable candidates) for the FIRST mention in `text`.

    Alacritty's looser regex can hand over text with leading/trailing debris; the
    scanner finds the mention inside it. A text with no mention at all — which is
    how `#282828` arrives here — returns (None, [])."""
    spans = scan_mention_spans(text, repos=repos, default_repo=default_repo)
    if not spans:
        return (None, [])
    span = spans[0]
    return (span, openable(span))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # 🔴 THE ALACRITTY CONTRACT: the matched text is the LAST argument. Taking
    # argv[1] would silently read a configured `args` entry instead.
    text = args.text[-1]

    # PASS 1 — pure, no I/O. `owner/repo#N` and a ClickUp id already answer the
    # question, and most clicks are one of those.
    span, candidates = resolve(text, default_repo=args.default_repo or None)

    # PASS 2 — only when the text did NOT answer it. Discovery costs a fan-out
    # of `git remote` calls plus a tmux round-trip, and paying that before
    # opening a link that needed neither is latency the operator feels on every
    # single click.
    needs_measuring = span is not None and (span["ambiguous"] or not candidates)
    if needs_measuring and not args.no_discovery:
        default_repo = args.default_repo or tmux_pane_repo()
        span, candidates = resolve(text, repos=discover_repos(),
                                   default_repo=default_repo or None)

    if span is None:
        notify("no mention in the clicked text", repr(text))
        return 1

    if not candidates and span and not args.no_discovery:
        # PASS 3 — GitHub API fallback for repos not in the static mapping.
        # Only for explicit repo#N (not bare #N, which has no repo name).
        m = re.match(r"(?:[A-Za-z0-9][A-Za-z0-9-]*/)?([A-Za-z0-9][A-Za-z0-9_-]*)#(\d+)",
                     span["raw"])
        if m:
            repo_name = m.group(1)
            num = m.group(2)
            api_repos = _gh_api_repo_search(repo_name)
            if api_repos:
                candidates = [
                    {"platform": PLATFORM_GITHUB, "id": num,
                     "url": GITHUB_ISSUE_URL.format(repo=full, id=num),
                     "raw": span["raw"]}
                    for full in api_repos.values()
                ]

    if not candidates:
        notify(f"cannot resolve {span['raw']}",
               "no repository owner is known for it — open the repo in a tmux "
               "pane, or write it as owner/repo#N")
        return 1

    if args.print_only:
        for c in candidates:
            print(c["url"])
        return 0

    if len(candidates) == 1:
        return open_url(candidates[0]["url"])

    url = pick(candidates)
    return open_url(url) if url else 0


if __name__ == "__main__":
    raise SystemExit(main())
