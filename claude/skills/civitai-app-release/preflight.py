#!/usr/bin/env python3
"""Refuse a Civitai App submit that the platform builder would fail on.

Every check here corresponds to a defect that actually shipped, not a
hypothetical:

- `generate-from-model` committed a `pnpm-lock.yaml`, deleted its
  `package-lock.json`, and declared NO `buildCommand`. The platform falls back
  to a legacy `npm run build` default, which runs `npm ci` — strictly from a
  lockfile that was no longer there. CI was green throughout; `.github/` is not
  in the submitted bundle, so the merge gate never executes that command.
- A seven-app batch once bumped `block.manifest.json` and left `package.json`
  behind. One of them shipped that way.
- Two apps in this fleet had a WITHDRAWN latest submission, which still
  occupies the submit floor — so their next version had to clear the withdrawn
  one, not the live one.

The builder's own recipe (talos-infra
`clusters/production/apps/tekton-builds/app-blocks-pipeline.yaml`) selects the
install command from `buildCommand`'s FIRST WORD:

    pnpm)  [ -f pnpm-lock.yaml ]     || fail;  corepack enable; pnpm install --frozen-lockfile
    *)     [ -f package-lock.json ]  || fail;  npm ci --ignore-scripts

so a manifest and a lockfile that disagree is a guaranteed build failure that
nothing local reports. That pairing is what check_lockfile_matches_builder
asserts.

Exit 0 = safe to submit. Non-zero = do not submit; each failure names the file
and the fix.
"""

from __future__ import annotations

import json
import os
import sys

# Mirrors the platform's own allowlist (BUILD_COMMAND_RE in civitai's
# block-manifest-validator.service.ts). Anchored, no surrounding whitespace —
# `pnpm  run build` with two spaces is REJECTED outright by the platform, which
# first-token parsing alone would happily accept.
import re

BUILD_COMMAND_RE = re.compile(
    r"^(?:(?:npm|pnpm|yarn) run [a-zA-Z0-9:_-]+|(?:npx )?vite build)$"
)

LOCKFILE_FOR = {
    "pnpm": "pnpm-lock.yaml",
    "npm": "package-lock.json",
    "yarn": "yarn.lock",
}
ALL_LOCKFILES = set(LOCKFILE_FOR.values())


class Failure(Exception):
    pass


def _read_json(root: str, name: str) -> dict:
    path = os.path.join(root, name)
    if not os.path.exists(path):
        raise Failure(f"{name} is missing from {root}")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def check_version_lockstep(root: str) -> str:
    """block.manifest.json and package.json must state the same version."""
    manifest = _read_json(root, "block.manifest.json")
    package = _read_json(root, "package.json")
    mv, pv = manifest.get("version"), package.get("version")
    if not isinstance(mv, str):
        raise Failure('block.manifest.json has no string "version"')
    if not isinstance(pv, str):
        raise Failure('package.json has no string "version"')
    if mv != pv:
        raise Failure(
            f"version lockstep broken: block.manifest.json={mv} package.json={pv} "
            "— bump BOTH; the platform reads the manifest, the build reads package.json"
        )
    return mv


def check_lockfile_matches_builder(root: str) -> str:
    """The manifest's buildCommand and the committed lockfile must agree, and
    exactly one lockfile may be present."""
    manifest = _read_json(root, "block.manifest.json")
    build = manifest.get("buildCommand")

    present = sorted(n for n in ALL_LOCKFILES if os.path.exists(os.path.join(root, n)))
    if len(present) > 1:
        raise Failure(
            f"more than one lockfile committed ({', '.join(present)}) — the builder "
            "installs from exactly one; delete the others"
        )

    if build is None:
        # Not merely a style nit: absent means the builder uses its LEGACY
        # default (`npm run build` -> `npm ci`), so a pnpm-only tree fails.
        if "pnpm-lock.yaml" in present or "yarn.lock" in present:
            raise Failure(
                "no buildCommand declared, so the platform falls back to its legacy "
                f"`npm run build` default — but the committed lockfile is {present[0]}. "
                'Set "buildCommand" (e.g. "pnpm run build") and "outputDir".'
            )
        return "npm"

    if not isinstance(build, str) or not BUILD_COMMAND_RE.match(build):
        raise Failure(
            f"buildCommand {build!r} does not match the platform allowlist "
            f"{BUILD_COMMAND_RE.pattern} — note it is anchored, so extra whitespace "
            "(e.g. 'pnpm  run build') is rejected outright"
        )

    first = build.split()[0]
    manager = "npm" if first == "npx" else first
    wanted = LOCKFILE_FOR[manager]
    if wanted not in present:
        raise Failure(
            f"buildCommand is {build!r} so the builder runs {manager}, which installs "
            f"strictly from {wanted} — but that file is not committed "
            f"(present: {', '.join(present) or 'none'})"
        )
    return manager


def check_above_floor(root: str, floor: str | None) -> None:
    """The version must be strictly above the highest version ON RECORD."""
    if floor is None:
        return
    version = _read_json(root, "block.manifest.json")["version"]

    def key(v: str) -> tuple:
        return tuple(int(p) if p.isdigit() else -1 for p in v.split("."))

    if key(version) <= key(floor):
        raise Failure(
            f"version {version} is not above the submit floor {floor} — the floor is "
            "the highest version ON RECORD, and a WITHDRAWN submission still occupies "
            "it. Bump both version fields."
        )


def run(root: str, floor: str | None = None) -> list[str]:
    """Returns the list of passed check names, or raises Failure on the first
    problem. Callers print; this returns data."""
    passed = []
    version = check_version_lockstep(root)
    passed.append(f"version lockstep ({version})")
    manager = check_lockfile_matches_builder(root)
    passed.append(f"builder/lockfile agree ({manager})")
    check_above_floor(root, floor)
    passed.append(f"above submit floor ({floor or 'no prior submission'})")
    return passed


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: preflight.py <export-dir> [submit-floor-version]", file=sys.stderr)
        return 2
    root = sys.argv[1]
    floor = sys.argv[2] if len(sys.argv) > 2 else None
    try:
        for name in run(root, floor):
            print(f"  ok   {name}")
    except Failure as exc:
        print(f"  FAIL {exc}", file=sys.stderr)
        return 1
    print("preflight passed — safe to submit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
