#!/usr/bin/env python3
"""Put the PINNED cairn client's `lib/` directory on `sys.path`.

WHY THIS EXISTS. devrc used to carry its own copies of five reader modules —
`host_identity`, `subsystem_resolver`, `subsystem_recall`, `cairn_doctor`,
`subsystem_read_store` — beside the OSS `ZacxDev/cairn` originals. The two sets
FORKED (measured 2026-09-07/08: the OSS side kept taking PRs while devrc's copies
sat still), and the operator decided on 2026-09-08 to CONSOLIDATE ONTO THE PIN:
delete devrc's copies and take them from `flake.nix`'s locked `cairn` input. This
module is the one seam that makes that possible from a plain `python3 script.py`
invocation, which is how every consumer here is run.

🔴 IT APPENDS, IT DOES NOT `insert(0, …)`. Every devrc consumer bootstraps with
`sys.path.insert(0, <repo>/scripts/lib)` and then calls `ensure()`. The pinned
directory therefore lands AFTER devrc's own, so a module name present on BOTH
sides resolves to devrc's copy. Exactly one name is in that position today —
`timeouts` — and it is NOT an accident to be tidied away: devrc's
`scripts/lib/timeouts.py` is imported by `scripts/cairn`, `scripts/claim-work`
and browser-bridge, none of which are cairn's to govern, while the pinned copy
carries a `DEFAULT_TIMEOUT` nothing imports.

🔴 BUT APPENDING IS ONLY HALF THE PROTECTION, AND THE OTHER HALF IS NOT OURS.
MEASURED against `cairn-c84c142`: the pinned `subsystem_recall` runs
`sys.path.insert(0, str(Path(__file__).resolve().parent))` at import time — it
PREPENDS its own directory — so from the moment any consumer imports the reader,
the pinned lib is `sys.path[0]` and wins every later import. Control, in one
interpreter each: importing `entry_shape`/`subsystem_resolver`/`host_identity`/
`cairn_doctor`/`subsystem_read_store` leaves `timeouts` resolving to devrc's copy
and `sys.path[0]` as devrc's lib; adding `import subsystem_recall` moves BOTH to
the store path. So the ordering this module sets is the state BEFORE the reader
loads, not an invariant afterwards.

That is exactly why the overlap set is GUARDED rather than merely arranged.
`scripts/tests/test_cairn_pin.py` pins it two-way — a SECOND shadowed name, in
either direction, fails the suite instead of resolving to whichever copy happened
to be at the front when it was first imported — and additionally requires the
pinned `timeouts` to carry every public name devrc's exports, because for that
one name devrc genuinely cannot control which copy a given process gets.

🔴 THERE IS NO SILENT FALLBACK, BY CONSTRUCTION. The devrc copies are DELETED, so
a resolution failure is not "degrade to the local module" — there is no local
module, and an `ImportError` naming `subsystem_recall` would send the reader
looking for a file that was removed on purpose. `ensure()` raises
`CairnPinUnresolved`, whose message names both resolution routes and the remedy,
so the failure reads as "the pinned client is not deployed here" rather than as
a missing file.

RESOLUTION ORDER
  1. `$CAIRN_LIB` — an explicit override. This is what the hermetic `nix`
     checks set, because a check derivation has no `~/.local/bin` and no
     home-manager generation to derive anything from.
  2. The deployed client: `shutil.which("cairn")` → `os.path.realpath` →
     `<store-path>/libexec/cairn/lib`. `bin/cairn` is a `makeWrapper` shell
     wrapper; the real script and its siblings live under `libexec/cairn/`.
     🔴 `realpath` is load-bearing — `~/.local/bin/cairn` is a home-manager
     symlink into `/nix/store`, and `parents[1]` of the SYMLINK is `~/.local`,
     which has no `libexec` and would resolve to nothing.
  3. Refuse.

🔴 A CANDIDATE IS ACCEPTED ON CONTENT, NEVER ON `is_dir()`. An empty or partial
`libexec/cairn/lib` is exactly what a half-built or half-fetched store path looks
like, and accepting it would turn a deploy fault into an `ImportError` several
frames away from the cause. Both `entry_shape.py` and `subsystem_resolver.py`
must be present — two rather than one because a single marker cannot tell a real
lib dir from a directory that happens to hold one file with that name.

🔴 IT IS DELIBERATELY NOT AN ENV VAR ALONE. `nix/sessionVariables.nix` lands in
`profile.d`, which only INTERACTIVE shells source — a non-interactive `zsh -c`
(what an agent's Bash tool runs) may carry none of it. So route 2, which asks
PATH rather than the environment, is the one that has to work unattended; route 1
exists for the sandbox, where there is no PATH entry to ask.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

__all__ = [
    "CairnPinUnresolved",
    "MARKER_MODULES",
    "ensure",
    "pinned_lib_dir",
]

#: Files that must BOTH exist for a directory to be accepted as the pinned lib.
#: `entry_shape` is the vocabulary devrc's writer now imports; `subsystem_resolver`
#: is the deepest module the reader chain needs. Two markers, for the reason in
#: the module docstring.
MARKER_MODULES: tuple[str, ...] = ("entry_shape.py", "subsystem_resolver.py")

#: The env var route. Named after the thing it points at rather than after cairn's
#: own internals, because the value is a DIRECTORY of modules, not a client.
CAIRN_LIB_ENV = "CAIRN_LIB"

#: Where the packaged client keeps its modules, relative to the store path.
#: `bin/cairn` is a wrapper; `parents[1]` of the RESOLVED wrapper is the store
#: path itself.
LIBEXEC_SUBPATH = ("libexec", "cairn", "lib")


class CairnPinUnresolved(RuntimeError):
    """The pinned cairn client's `lib/` directory could not be located.

    Sentinel: 'pinned cairn lib not found'. Its own class, not `ImportError`, so
    a consumer can tell "the pin is not deployed on this host" from "that module
    genuinely does not exist" — the two have different remedies and only one of
    them is a devrc bug.
    """


def _accepts(candidate: Path) -> bool:
    """Does `candidate` hold a real pinned lib? CONTENT, not existence."""
    return all((candidate / m).is_file() for m in MARKER_MODULES)


def _from_env() -> tuple[Path | None, str]:
    raw = os.environ.get(CAIRN_LIB_ENV, "").strip()
    if not raw:
        return None, f"${CAIRN_LIB_ENV} is unset or empty"
    candidate = Path(raw)
    if not candidate.is_dir():
        return None, f"${CAIRN_LIB_ENV}='{raw}' is not a directory"
    if not _accepts(candidate):
        missing = ", ".join(m for m in MARKER_MODULES if not (candidate / m).is_file())
        return None, (
            f"${CAIRN_LIB_ENV}='{raw}' is a directory but does not hold the pinned "
            f"client's modules (missing: {missing})"
        )
    return candidate, f"${CAIRN_LIB_ENV}='{raw}'"


def _from_client() -> tuple[Path | None, str]:
    found = shutil.which("cairn")
    if not found:
        return None, "no `cairn` on PATH"
    real = Path(os.path.realpath(found))
    # parents[0] is `<store>/bin`; parents[1] is the store path itself.
    try:
        root = real.parents[1]
    except IndexError:  # pragma: no cover — a `cairn` at the filesystem root
        return None, f"`cairn` resolves to '{real}', which has no parent package dir"
    candidate = root.joinpath(*LIBEXEC_SUBPATH)
    if not candidate.is_dir():
        return None, (
            f"`cairn` on PATH resolves to '{real}', but '{candidate}' is not a "
            f"directory"
        )
    if not _accepts(candidate):
        missing = ", ".join(m for m in MARKER_MODULES if not (candidate / m).is_file())
        return None, (
            f"`cairn` on PATH resolves to '{real}', but '{candidate}' does not hold "
            f"the pinned client's modules (missing: {missing})"
        )
    return candidate, f"`cairn` on PATH → '{candidate}'"


def pinned_lib_dir() -> Path:
    """The pinned client's `lib/` directory. Raises `CairnPinUnresolved`.

    READ-ONLY and side-effect-free — it does not touch `sys.path`. `ensure()` is
    the one that mutates, and it is a separate function so a test (and
    `cairn doctor`-shaped reporting) can ask WHERE the pin resolves without
    changing the interpreter it asks from.

    🔴 A `CAIRN_LIB` THAT IS SET BUT UNUSABLE REFUSES — it does NOT fall through
    to the PATH route. Falling through is the confident-wrong-answer shape: the
    hermetic `nix` checks set this variable precisely so the leg tests the PINNED
    client, and a typo there would silently hand them whatever `cairn` the
    builder happens to carry, green and measuring something else. An UNSET or
    empty value is not a mistake — it is the ordinary case on a host — so that
    one does fall through.
    """
    env_dir, env_why = _from_env()
    if env_dir is not None:
        return env_dir
    if os.environ.get(CAIRN_LIB_ENV, "").strip():
        raise CairnPinUnresolved(
            f"pinned cairn lib not found: {env_why}. {CAIRN_LIB_ENV} was set "
            f"explicitly, so it is NOT ignored in favour of the `cairn` on PATH — "
            f"an override that silently resolves somewhere else is how a check "
            f"passes against the wrong client. Point it at the packaged client's "
            f"lib directory (it must hold "
            f"{', '.join(sorted(MARKER_MODULES))}), or unset it to fall back to "
            f"the deployed client on PATH. There is deliberately no local "
            f"fallback: devrc's own copies of these modules were deleted when it "
            f"consolidated onto the pin."
        )
    client_dir, client_why = _from_client()
    if client_dir is not None:
        return client_dir
    raise CairnPinUnresolved(
        "pinned cairn lib not found: devrc takes its store-reader modules "
        f"({', '.join(sorted(m[:-3] for m in MARKER_MODULES))}, and their siblings) "
        "from the pinned `cairn` flake input, and neither resolution route "
        f"answered.\n  route 1 — {env_why}\n  route 2 — {client_why}\n"
        "  remedy: deploy the pin with `home-manager switch --flake "
        "~/workspace/devrc --impure` (this puts the packaged client on PATH at "
        f"~/.local/bin/cairn), or set {CAIRN_LIB_ENV} to the client's lib "
        "directory — `nix build ~/workspace/devrc#checks.x86_64-linux."
        "cairn-client-runs` shows the shape. There is deliberately no local "
        "fallback: devrc's own copies of these modules were deleted when it "
        "consolidated onto the pin."
    )


def ensure() -> Path:
    """APPEND the pinned lib to `sys.path` (idempotently) and return it.

    Append rather than prepend — see the module docstring. Idempotent because
    several devrc modules import each other and each calls this at import time;
    a duplicated entry is harmless but makes `sys.path` unreadable in a
    traceback, which is where this seam is most often diagnosed.
    """
    d = pinned_lib_dir()
    s = str(d)
    if s not in sys.path:
        sys.path.append(s)
    return d


if __name__ == "__main__":
    # 🔴 A SHELL SURFACE, NOT A DEMO. `verify-byte-identity.sh` and
    # `build-push.sh` both need the pinned lib directory from bash, and the
    # alternative — a `python3 -c` one-liner interpolating `$HERE` — is the
    # zsh/bash quoting trap this repo keeps paying for. Printing the path and
    # letting `pinned_lib_dir`'s own exception be the failure keeps one
    # resolution rule and one refusal message for both languages.
    print(pinned_lib_dir())
