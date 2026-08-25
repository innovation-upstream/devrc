#!/usr/bin/env python3
"""Generate the devrc explainer page: one self-contained HTML file.

    scripts/present/generate.py [-o OUT] [--sanitize] [--no-systemd] [--repo DIR]
                                [--check] [--quiet]

🔴 THIS IS THE ONLY PLACE THAT DECIDES A BUILD HAS FAILED, and it has exactly
one failure verdict that is about the CONTENT rather than about crashing:

    every registered fact came back UNMEASURED  ->  exit 3, no file written

That case looks fine on the page — a careful document full of honest absences —
and it is a broken build. Refusing to emit it is the whole reason this check is
here rather than left to the reader's judgement. The complement is deliberately
NOT a failure: SOME unmeasured rows are normal and correct (a host without a
given surface, an offline build), and failing on those would train everyone to
pass a `--force` flag.

EXIT CODES
  0  a page was written
  2  usage error, or the output could not be written
  3  every fact was UNMEASURED — no page was written
  4  the page was written but failed its own self-containment check
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from present import measure, render, sanitize  # noqa: E402

#: The ONE external-looking token the output may contain: the SVG XML namespace.
#: It is an identifier, never fetched. Anything else with a scheme is a defect.
_ALLOWED_URI = "http://www.w3.org/2000/svg"

_EXTERNAL_MARKERS = (
    "<script src=", "<script  src=", "rel=\"stylesheet\"", "rel='stylesheet'",
    "@import", "url(http", "url('http", 'url("http', "<iframe", "<link rel=preload",
    "srcset=", "integrity=", "crossorigin=",
)


def self_contained(page: str) -> list[str]:
    """Return the reasons this page is NOT self-contained. Empty means it is.

    🔴 THE CHECK IS ON THE OUTPUT, NOT ON THE GENERATOR'S INTENTIONS. Asserting
    "we never write a script tag" is a claim about the code; scanning the bytes
    that will be opened is a claim about the artefact. Only the second one
    survives someone adding a diagram helper six months from now.
    """
    problems: list[str] = []
    probe = page.replace(_ALLOWED_URI, "")
    for scheme in ("http://", "https://", "//cdn.", "data:application/"):
        if scheme in probe:
            i = probe.find(scheme)
            problems.append(f"external reference {scheme!r} near: {probe[max(0, i - 60):i + 60]!r}")
    for marker in _EXTERNAL_MARKERS:
        if marker in probe:
            problems.append(f"external-asset marker {marker!r} is present")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="present", description="Generate the devrc agent-layer explainer page.")
    ap.add_argument("-o", "--out", default="present.html",
                    help="output HTML path (default: present.html)")
    ap.add_argument("--repo", default=None, help="repo root to measure (default: this checkout)")
    ap.add_argument("--sanitize", action="store_true",
                    help="swap real identifiers for synthetic stand-ins, for a shareable copy")
    ap.add_argument("--no-systemd", action="store_true",
                    help="skip systemd probing (that row then renders UNMEASURED, with the reason)")
    ap.add_argument("--check", action="store_true",
                    help="measure and report, write nothing")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    env = measure.Env.live(Path(args.repo) if args.repo else None)
    if args.no_systemd:
        from dataclasses import replace
        env = replace(env, allow_systemd=False)

    ms = measure.take(env)
    verdict = ms.verdict()

    def say(*a):
        if not args.quiet:
            print(*a, file=sys.stderr)

    say(f"present: {len(ms.measured)} measured, {len(ms.unmeasured)} unmeasured, "
        f"{len(ms)} registered")
    for m in ms.unmeasured:
        say(f"  UNMEASURED  {m.key}: {m.reason}")

    if verdict != "ok":
        say("")
        say(f"present: BUILD FAILED — verdict={verdict}.")
        say("  Every registered fact came back UNMEASURED (or none was registered).")
        say("  That page would LOOK careful and BE broken, so nothing was written.")
        say("  Check that --repo points at a real devrc checkout and that the")
        say("  measurement helpers under scripts/lib are importable.")
        return 3

    san = sanitize.build(args.sanitize, env, ms)
    shown = sanitize.apply(ms, san) if args.sanitize else ms
    page = render.build_html(shown, sanitized=args.sanitize, san=san)

    problems = self_contained(page)
    if args.check:
        say(f"present: --check, nothing written. self-contained="
            f"{'yes' if not problems else 'NO'}")
        for p in problems:
            say(f"  NOT SELF-CONTAINED: {p}")
        return 0 if not problems else 4

    out = Path(args.out)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page, encoding="utf-8")
    except OSError as exc:
        say(f"present: could not write {out}: {exc}")
        return 2

    say(f"present: wrote {out} ({len(page.encode()):,} B)"
        f"{' [SANITIZED]' if args.sanitize else ''}")
    if problems:
        say("present: 🔴 the page is NOT self-contained and will try to reach the network:")
        for p in problems:
            say(f"  {p}")
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
