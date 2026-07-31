#!/usr/bin/env python3
"""Differential fuzz: safety.py vs extension/sanitize.js.

NOT collected by pytest (no `test_` prefix) because it needs BOTH interpreters
in one shell. Run it by hand after touching either implementation:

    nix-shell -p nodejs python312 --run \
      "python3 scripts/dl-router/tests/difffuzz.py"

Exits non-zero and prints the first 40 disagreements if the two sides differ on
any generated input.

Why this exists: the hostile-input tables in test_security.py and
sanitize.test.mjs were two hand-copied literal lists. They agreed with each
other and both passed, while the IMPLEMENTATIONS disagreed on 991 inputs
neither list contained -- U+FEFF (JS `trim()` strips it, Python `strip()` does
not), U+0085 (the reverse), `". foo"`, and an out-of-range port. The sidecar
was the looser side, so `/mkdir` could create a directory the extension would
never route into. The tables are now one shared fixture; this is the thing that
finds the cases nobody thought to add to it.
"""
from __future__ import annotations

import itertools
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from safety import is_http_url, is_safe_dir_name, safe_file_name  # noqa: E402

# Characters chosen to sit exactly on the seams between the two languages'
# primitives: C0/C1 controls, every flavour of unicode space, the zero-width
# and bidi format characters, separators, and NFC/NFKC-unstable letters.
ALPHABET = [
    "a", "Z", "0", " ", ".", "-", "_", "/", "\\", ":",
    "\x00", "\x09", "\x0a", "\x0d", "\x1b", "\x7f",
    "\x85", "\x9f", "\xa0", " ", " ", " ", " ",
    " ", " ", "　", "﻿", "​", "‎", "‮",
    "⁦", "؜", "é", "é", "İ", "Ａ",
    "\U0001f600",
]

URL_ALPHABET = [
    "http://", "https://", "ftp://", "//", "h", "x.test", "example-site.test",
    "/", ":", "80", "99999", "0", "-1", "?a=1", "#f", " ", "\x00", "\n",
    "[::1]", "user:pw@", "..", "%2f", "﻿", "\xa0",
]

JS_DRIVER = """
import { isSafeDirName, sanitizeFileName, isHttpUrl } from "SANITIZE_URL";
import { readFileSync } from "node:fs";
const cases = JSON.parse(readFileSync(process.argv[2], "utf8"));
process.stdout.write(JSON.stringify({
  dir: cases.dir.map((s) => isSafeDirName(s)),
  file: cases.file.map((s) => sanitizeFileName(s)),
  url: cases.url.map((s) => isHttpUrl(s)),
}));
"""


def gen_names(n: int) -> list:
    out = set()
    for combo in itertools.product(ALPHABET, repeat=2):
        joined = "".join(combo)
        out.update({joined, "a" + joined, joined + "a",
                    combo[0] + "a" + combo[1]})
    rnd = random.Random(1234)
    while len(out) < n:
        parts = []
        for _ in range(rnd.randint(1, 8)):
            parts.append(rnd.choice(ALPHABET) if rnd.random() < 0.55
                         else chr(rnd.randint(1, 0x2FFF)))
        out.add("".join(parts))
    return sorted(out)


def gen_urls(n: int) -> list:
    out = set()
    for combo in itertools.product(URL_ALPHABET, repeat=2):
        out.add("".join(combo))
    rnd = random.Random(99)
    while len(out) < n:
        out.add("".join(rnd.choice(URL_ALPHABET)
                        for _ in range(rnd.randint(1, 4))))
    return sorted(out)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    names = gen_names(int(argv[0]) if argv else 30000)
    urls = gen_urls(int(argv[1]) if len(argv) > 1 else 20000)

    with tempfile.TemporaryDirectory(prefix="dl-router-difffuzz-") as tmp:
        work = Path(tmp)
        (work / "cases.json").write_text(
            json.dumps({"dir": names, "file": names, "url": urls}),
            encoding="utf-8")
        (work / "driver.mjs").write_text(
            JS_DRIVER.replace("SANITIZE_URL",
                              (ROOT / "extension" / "sanitize.js").as_uri()),
            encoding="utf-8")
        proc = subprocess.run(
            ["node", str(work / "driver.mjs"), str(work / "cases.json")],
            capture_output=True, text=True, check=True)
        js = json.loads(proc.stdout)

    bad = 0
    for label, values, pyfn in (("dir", names, is_safe_dir_name),
                                ("file", names, safe_file_name),
                                ("url", urls, is_http_url)):
        for i, value in enumerate(values):
            py, other = pyfn(value), js[label][i]
            if py != other:
                bad += 1
                if bad <= 40:
                    print(f"{label:5s} {value!r:44s} py={py!r:22s} js={other!r}")
    total = len(names) * 2 + len(urls)
    print(f"\n{bad} divergence(s) over {total} comparisons")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
