#!/usr/bin/env python3
"""Rewrite `services.journald.extraConfig` to `services.journald.settings.Journal`.

nixos-26.11 removed `services.journald.extraConfig` (mkRemovedOptionModule in
nixos/modules/system/boot/systemd/journald.nix), so a configuration.nix carrying it
fails `nixos-rebuild switch` on an assertion. `settings.Journal` is a freeform attrset
of journald.conf(5) keys, so the ini text becomes attributes.

This module is the parsing half, kept separate from the shell wrapper so it can be
tested without root and without touching /etc/nixos. It REFUSES rather than guessing:
every failure raises Refused, and the caller writes nothing.

Both spellings of the removed option occur in the wild and both are handled:

    services.journald.extraConfig = ''        services.journald.extraConfig = "SyncIntervalSec=30s";
      SystemMaxUse=2G
    '';

The workbench carried the first, the laptop the second — so a migration that only
handled the block form would refuse on half the fleet.
"""

from __future__ import annotations

import re

__all__ = ["Refused", "parse_settings", "rewrite", "to_nix_string"]

# A journald.conf(5) key: letters and digits, starting with a letter. Deliberately
# strict — anything else means we did not understand the block, and a wrong guess here
# writes wrong Nix into a file the next `nixos-rebuild` will act on.
_KEY = re.compile(r"[A-Za-z][A-Za-z0-9]*\Z")

# The two assignment forms. Both capture the leading indent (group 1) and the body
# (group 2). Non-greedy bodies, anchored on the terminator, so a second assignment
# later in the file is a separate match rather than being swallowed into this one.
#
# `\r?\n` throughout: a CRLF file is unlikely on NixOS but a `\n`-only anchor does not
# refuse it, it fails to MATCH it — which reads as "no extraConfig here" and would have
# the caller report a clean, already-migrated config.
_BLOCK = re.compile(
    r"^([ \t]*)services\.journald\.extraConfig[ \t]*=[ \t]*''[ \t]*\r?\n"
    r"(.*?)^[ \t]*'';[ \t]*\r?\n",
    re.S | re.M,
)
_INLINE = re.compile(
    r"^([ \t]*)services\.journald\.extraConfig[ \t]*=[ \t]*\"([^\"\r\n]*)\";[ \t]*\r?\n",
    re.M,
)


class Refused(Exception):
    """The input was not something we understood well enough to rewrite."""


def to_nix_string(value: str) -> str:
    r"""Quote a value as a Nix double-quoted string.

    The escapes are not cosmetic. The source is a `''`-string (or a `"`-string), and we
    are emitting into a `"`-string, where the rules differ: a literal backslash in a
    `''`-body means a backslash, but in a `"`-body it starts an escape — so `5m\t` would
    silently become a TAB rather than the four characters it was. `${` likewise starts
    an antiquotation and would be evaluated as a variable reference.
    """
    out = value.replace("\\", "\\\\").replace('"', '\\"').replace("${", "\\${")
    return f'"{out}"'


def parse_settings(body: str) -> list[tuple[str, str]]:
    """Parse a journald.conf(5) fragment into ordered key/value pairs.

    Comments and blank lines are skipped. Anything else that is not a `Key=Value` with a
    well-formed key raises Refused — including a `[Journal]` section header, which would
    otherwise be silently dropped and change what the config means.
    """
    pairs: list[tuple[str, str]] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("["):
            raise Refused(
                f"section header {line!r} in an extraConfig block — this migration "
                "targets the [Journal] section only; rewrite it by hand"
            )
        if "=" not in line:
            raise Refused(f"unparseable journald.conf line: {raw!r}")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not _KEY.match(key):
            raise Refused(f"unexpected journald.conf key: {key!r}")
        pairs.append((key, value))
    if not pairs:
        raise Refused("the extraConfig block contained no settings")
    return pairs


def _matches(src: str) -> tuple[re.Pattern[str], list[tuple[str, str]]]:
    block = _BLOCK.findall(src)
    inline = _INLINE.findall(src)
    total = len(block) + len(inline)
    if total != 1:
        raise Refused(
            f"expected exactly 1 services.journald.extraConfig assignment, found {total}"
        )
    return (_BLOCK, block) if block else (_INLINE, inline)


def rewrite(src: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (new_source, migrated_pairs). Raises Refused and writes nothing on doubt."""
    pattern, matches = _matches(src)
    indent, body = matches[0]
    pairs = parse_settings(body)

    # Emit the file's own line ending, so a CRLF config does not come back as a mixed
    # one — the operator reviews this as a `diff -u` and a changed ending shows up there.
    eol = "\r\n" if "\r\n" in src else "\n"
    inner = "".join(f"{indent}  {k} = {to_nix_string(v)};{eol}" for k, v in pairs)
    replacement = (
        f"{indent}services.journald.settings.Journal = {{{eol}{inner}{indent}}};{eol}"
    )

    # A literal replacement: re.sub would interpret backslashes in the value as group
    # references, so the whole point of to_nix_string could be undone here.
    return pattern.sub(lambda _match: replacement, src, count=1), pairs


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", help="configuration.nix to read")
    ap.add_argument("--out", help="write the rewritten file here (default: stdout)")
    ap.add_argument(
        "--print-keys",
        action="store_true",
        help="print the migrated KEY=VALUE pairs to stderr, for the caller to verify against",
    )
    args = ap.parse_args(argv)

    # newline="" keeps the file's own line endings: a universal-newlines read followed by
    # a "\n" write silently converts a CRLF file end to end, which turns the operator's
    # review diff into "every line changed".
    with open(args.source, encoding="utf-8", newline="") as fh:
        src = fh.read()

    try:
        new, pairs = rewrite(src)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=__import__("sys").stderr)
        return 2

    if args.print_keys:
        import sys

        for key, value in pairs:
            print(f"{key}={value}", file=sys.stderr)

    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="") as fh:
            fh.write(new)
    else:
        import sys

        sys.stdout.write(new)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
