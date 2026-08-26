#!/usr/bin/env python3
"""ESCROW VERIFIER for the age identity the /analyze-service index backups need.

WHAT THIS CLOSES — THE LAST SINGLE POINT OF FAILURE
---------------------------------------------------
`backup.py` writes age ciphertext off-machine and `restore-verify.py` proves the
bytes in the bucket still restore. Both of them decrypt with ONE file:
`~/workspace/homelab-talos/.secrets/age.key`. Lose that file and every artifact
in the bucket becomes a well-formed, well-replicated, permanently unreadable
blob — the backups would keep passing every check `restore-verify.py` makes,
right up until the moment somebody needed them, because that verifier runs on
the machine that still HAS the key.

So the key is escrowed into the operator's self-hosted Vaultwarden as a Secure
Note. This script answers the three questions that escrow raises, and it refuses
to answer any of them by silence:

  1. IS IT STILL THERE?    the note exists, exactly once, and is not empty;
  2. IS IT STILL CORRECT?  its bytes equal the on-disk identity, byte-for-byte;
  3. DOES IT STILL WORK?   `--decrypt-check` writes the ESCROWED bytes to a
                           throwaway identity and decrypts a REAL artifact out
                           of the bucket with it.

🔴 (2) AND (3) ARE DIFFERENT CLAIMS AND ONLY ONE OF THEM MATTERS ALONE.
Byte equality proves the two copies agree; it cannot prove either of them opens
anything, because it never asks age. Decryption proves the ESCROWED COPY —
not the on-disk one — reconstructs history. A run without `--decrypt-check`
says so in its own verdict line rather than letting "verified" stand for both.

🔴 EVERY EMPTY OUTCOME IS A FAILURE
-----------------------------------
The same stance as `backup.py` and `restore-verify.py`, for the same reason: a
verifier that reports success having checked nothing is a false all-clear, and a
false all-clear about the ONLY copy of a decryption key is the worst one in this
subsystem.

  * `bw` not installed                        -> failure (with the exact command)
  * vault locked / unauthenticated            -> failure, distinct codes
  * the item is not found                     -> failure
  * TWO items carry the name                  -> failure (ambiguous: this script
                                                 cannot tell which one is the
                                                 escrow, and picking either is a
                                                 coin flip recorded as a verdict)
  * the note body is empty or whitespace      -> failure
  * the on-disk identity is empty             -> failure (two empty files compare
                                                 EQUAL, which is the quiet way to
                                                 certify nothing at all)
  * zero artifacts under the prefix           -> failure (--decrypt-check)

🔴 EVERY FAILURE MODE HAS ITS OWN TOKEN AND ITS OWN EXIT CODE
--------------------------------------------------------------
`EXIT_CODES` below is the whole list, pinned as an exact mapping by the test
suite. This is not tidiness: "escrow check failed" sends an operator to the
wrong place. `VAULT-LOCKED` means type one command; `ITEM-NOT-FOUND` means the
escrow is gone and the key has no second copy; `BYTES-DIFFER-TRAILING-NEWLINE`
means the server trimmed the note and the escrow is RECOVERABLE with one `echo`;
`BYTES-DIFFER-MATERIALLY` means it is not. Four remedies, four codes.

The trailing-newline classification exists because it is the difference that a
copy/paste through a web vault actually produces, and because an `age` identity
whose final newline is gone still decrypts — so a byte comparison is the ONLY
place that difference is visible, and reporting it as a generic mismatch would
send the operator hunting for corruption that is not there.

🔴 NO KEY MATERIAL IS EVER PRINTED, LOGGED, OR PASSED IN ARGV
--------------------------------------------------------------
  * the note is read out of `bw list items --search`'s JSON on stdout and stays
    in memory; nothing derived from it reaches a message. A mismatch reports
    BYTE COUNTS AND A CLASSIFICATION, never the differing content, and never a
    hash of it either (a hash of a 189-byte key with a known format is not a
    protection worth arguing about in a public repo's issue tracker).
  * `bw`'s stdout is NEVER quoted in an error message. Every subprocess result
    here is handled as redacted by construction — `_run()` has no path that
    interpolates output into an exception.
  * the throwaway identity written by `--decrypt-check` is created FRESH at
    0600 inside a 0700 directory — `O_EXCL|O_NOFOLLOW` plus an `fchmod` on the
    fd, because `O_CREAT|O_TRUNC` applies its mode only when it creates and
    follows a symlink otherwise (measured: a pre-existing 0666 file stayed
    0666, and a symlink got the key written THROUGH it to a 0644 file outside
    the 0700 dir). It is overwritten and unlinked in a `finally` that covers
    success, every early return, every refusal and every unexpected exception,
    and — since the signal handlers below make SIGTERM/SIGINT/SIGHUP unwind
    rather than kill — the timer-driven stop path too. The overwrite is
    best-effort: on a journalling or copy-on-write filesystem it is not a
    guarantee and this file does not claim it is; the UNLINK is what is tested.
    ⚠ The WORK DIRECTORY is removed only when this script created it. Under an
    explicit `--work-dir` the operator's directory is left in place — emptied,
    not deleted.

🔴 THE ENDPOINT IS NEVER HARDCODED. devrc is a public repo. The server is read
from `bw config server` at run time. It is cross-checked against the
authenticated session's own `serverUrl` (a repointed CLI with a stale session is
a real state this vault has been in), and against `--expect-server` when the
operator pins one. With no pin the configured server is reported as INFORMATION
and the run says the pin was absent, rather than implying it checked.
⚠ The session cross-check CANNOT ALWAYS BE MADE — `bw status` may report
`serverUrl: null`. When that happens the verdict says `session cross-check NOT
COMPARED (<reason>)`. It is never silently skipped behind a sentence claiming it
matched, which is exactly what the first version did.

🔴 THE MASTER PASSWORD CANNOT BE AUTOMATED, so this never tries. `bw status` is
the authority; a vault that is not `unlocked` ends the run with a distinct code
and the exact command to type. Every `bw` call runs with stdin on /dev/null AND
`--nointeraction` AND a timeout, so a future `bw` that decides to prompt gets
EOF instead of an agent hanging forever on an invisible password prompt.

Usage:
    escrow-verify.py [--identity FILE] [--item-name NAME] [--expect-server URL]
                     [--decrypt-check [--scope S] [--bucket B | --from-dir DIR]
                                      [--host H | --prefix P] [--store DIR]]
                     [--work-dir DIR] [--timeout SECONDS] [--print-plan]

  --print-plan   pure text; runs no `bw`, touches no network, reads no key.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import backup as B  # noqa: E402  (sibling module; owns the paths and the modes)

PROG = "analyze-service-index-escrow-verify"

_HERE = Path(__file__).resolve().parent

DEFAULT_IDENTITY = B.DEFAULT_IDENTITY
DEFAULT_BUCKET = B.DEFAULT_BUCKET
DEFAULT_STORE = B.DEFAULT_STORE

# 🔴 The Secure Note's name, EXACTLY as it was created. Not a secret and not an
# endpoint — it is the lookup key, and this script cannot find the escrow
# without it. The em dash is U+2014 and is part of the name; `bw`'s search is
# fuzzy, so the match below is an exact byte comparison on `name` rather than
# whatever the search returned.
DEFAULT_ITEM_NAME = "age.key — SOPS + analyze-service-index backups"

# Bitwarden's item type enum. 2 == Secure Note. Checked as STATE rather than
# trusting the name: any item type can be given any name, and a Login item whose
# `notes` happen to hold something is not the escrow.
ITEM_TYPE_SECURE_NOTE = 2

# What `main()` reports when the operator passed `--identity` explicitly. A
# named constant because `provenance_clauses()` BRANCHES on it: a flag is a
# deliberate act, and wording it like an environment redirect sends the reader
# chasing a variable they never set — and told them to re-run with the very
# flag they had just used.
IDENTITY_SOURCE_FLAG = "the --identity flag"

# The file the escrowed bytes are written to under `--decrypt-check`. A module
# constant so the test suite can assert the path is GONE afterwards by name,
# rather than by re-deriving it from this file's own code.
ESCROW_IDENTITY_FILENAME = "escrowed-identity.key"
RESTORE_SUBDIR = "restore"

# `bw` is a Node program that talks to a remote server; without a ceiling a
# hung TLS handshake is indistinguishable from a hung password prompt, and both
# read as "the timer never finished".
DEFAULT_TIMEOUT = 60.0

# 🔴 `bw` IS NOT INSTALLED ON EITHER HOST — deliberately, it is a one-command
# nix-shell away and nothing runs it unattended. The exact command lives here so
# the failure message can hand it over instead of dying on FileNotFoundError.
#
# 🔴 THE ADVERTISED SHELL MUST BE ABLE TO RUN THE COMMAND IT ADVERTISES.
# MEASURED 2026-08-25: this hint provisioned `bitwarden-cli jq` ONLY, while
# `--decrypt-check` reaches MinIO through `backup.MinioUploader`, whose `minio`
# import is LAZY (inside the method, not at module import). So the advertised
# shell parsed, started, unlocked the vault, and THEN died with
# `ModuleNotFoundError: No module named 'minio'` — after the operator had
# already typed the master password. A hint that cannot complete its own
# invocation is worse than no hint: it is followed.
#
# So the packages are a LEDGER and the hint is DERIVED from it. The ledger is
# pinned against the modules the decrypt path imports by
# `scripts/tests/test_analyze_service_index_escrow_verify.py`, so the advertised
# shell and the code's actual dependencies cannot drift apart silently.
NIX_SHELL_PACKAGES: tuple[str, ...] = (
    "bitwarden-cli",                       # `bw` itself
    "jq",                                  # the documented pipeline around it
    "python3.withPackages(p:[p.minio])",   # --decrypt-check reaches the bucket
)

# The third-party Python modules the `--decrypt-check` path imports, named here
# so the ledger above can be checked against them mechanically rather than by
# someone remembering. `minio` is imported lazily by `backup.MinioUploader`.
DECRYPT_PYTHON_MODULES: tuple[str, ...] = ("minio",)


def _quote_nix_package(pkg: str) -> str:
    """Shell-quote a `-p` argument that is a nix EXPRESSION, not a bare name.

    🔴 `shlex.quote`, NOT a hand-rolled rule. The first revision tested for a
    trigger set of metacharacters and wrapped in single quotes without escaping
    any embedded quote — correct for today's two entries and wrong for the
    first entry that contains a `'` or a `$`. `shlex.quote` leaves a bare name
    bare and is correct for every input, which is the whole point of a hint the
    operator pastes into a shell.
    """
    return shlex.quote(pkg)


NIX_SHELL_HINT = ("nix-shell -p "
                  + " ".join(_quote_nix_package(p) for p in NIX_SHELL_PACKAGES)
                  + " --run '<command>'")

# 🔴 THERE IS DELIBERATELY NO `_DIR_MODE` ALIAS HERE. Directory mode is enforced
# by `B._private_dir()`, which owns the number; re-exporting it would be a second
# name for one constant, and an unused one at that. `_FILE_MODE` earns its keep
# because `_create_private_file()` passes it to `os.open` and `os.fchmod`.
_FILE_MODE = B._FILE_MODE    # 0600

# --------------------------------------------------------------------------- #
# the failure vocabulary
# --------------------------------------------------------------------------- #
# 🔴 ONE TOKEN AND ONE EXIT CODE PER DISTINGUISHABLE CAUSE. Pinned as an exact
# mapping by the test suite, in both directions, so a token cannot be raised
# without a code and a code cannot outlive the token it names.
#
# 0 is success and 1 is "an unexpected exception, read the traceback" — the same
# meaning `restore-verify.py` gives 1. Everything classified starts at 10 so that
# a classified refusal can never be confused with an interpreter-level failure.
EXIT_OK = 0
EXIT_UNEXPECTED = 1

EXIT_CODES: dict[str, int] = {
    # the tool itself
    "BW-MISSING": 10,
    "BW-FAILED": 11,
    # the vault's state — `bw status` is the authority
    "VAULT-LOCKED": 12,
    "VAULT-UNAUTHENTICATED": 13,
    "VAULT-STATUS-UNKNOWN": 14,
    # which server the answer came from
    "SERVER-UNKNOWN": 15,
    "SERVER-MISMATCH": 16,
    # the item
    "ITEM-NOT-FOUND": 17,
    "ITEM-AMBIGUOUS": 18,
    "ITEM-WRONG-TYPE": 19,
    "NOTE-EMPTY": 20,
    # 🔴 SEPARATE FROM `NOTE-EMPTY`, and the split matters. `NOTE-EMPTY` says
    # "the escrow was emptied" and sends the operator to re-escrow. If `bw` ever
    # OMITS the field rather than returning "", that is a CLI/schema surprise and
    # the note may be perfectly intact — re-escrowing on that advice would
    # overwrite a good copy on the strength of a parsing accident.
    "NOTE-MISSING": 32,
    # the comparison
    "BYTES-DIFFER-TRAILING-NEWLINE": 21,
    "BYTES-DIFFER-MATERIALLY": 22,
    # the local side of the comparison
    "IDENTITY-MISSING": 23,
    "IDENTITY-EMPTY": 24,
    # --decrypt-check
    #
    # 🔴 FIVE OUTCOMES, NOT TWO, AND THE SPLIT IS DERIVED FROM AN OBSERVED PHASE
    # rather than from the absence of a substring in somebody else's message.
    # The first version had only DECRYPT-FAILED/RESTORE-FAILED, chosen by
    # `"DECRYPT FAILED" in str(exc)`, and it was WRONG in three measured cases:
    #
    #   * `age` not on PATH          -> RESTORE-FAILED, asserting the escrowed
    #                                   key "works" and the ARTIFACT is at fault;
    #   * a zero-BYTE object         -> RESTORE-FAILED, asserting bytes
    #                                   "DECRYPTED" on a path where the decrypt
    #                                   step was never reached at all;
    #   * a valid encryption of NOTHING -> DECRYPT-FAILED, "not a working
    #                                   identity" — for a key that worked
    #                                   perfectly. That verdict gets a good
    #                                   disaster-recovery key ROTATED.
    #
    # Two of the three blame the escrow for a fault that is not the escrow's, in
    # the direction that makes someone act destructively.
    # 🔴 AND A SIXTH, ADDED AFTER RE-MEASURING age: `ARTIFACT-CORRUPT`. The
    # five-way version reported a TAMPERED or TRUNCATED artifact as
    # `ARTIFACT-EMPTY` — "THE ESCROW IS FINE … a valid encryption of an empty
    # payload" — because it read file PRESENCE as "age reported success". It does
    # not: age writes output before authenticating the payload. Detecting
    # tampering is the single most important thing a backup verifier does, and
    # that spelling reported it as nothing to worry about.
    "AGE-MISSING": 29,          # precondition: the tool is not installed
    "ARTIFACT-UNREADABLE": 30,  # the pipeline failed BEFORE decrypt was reached
    "DECRYPT-FAILED": 25,       # age wrote NOTHING: wrong key OR damaged header
    # 🔴 RAISED BEFORE ANY `bw` CALL — see `preflight_decrypt_imports`. Its own
    # code because the remedy is unlike every other one here: nothing is wrong
    # with the escrow, the artifact or the vault; the INTERPRETER is missing a
    # package, and the fix is to re-run under a different shell.
    "DECRYPT-DEPS-MISSING": 34,
    "ARTIFACT-CORRUPT": 33,     # age authenticated the HEADER then failed the
                                #   payload: the key WORKED, the bytes are bad
    "ARTIFACT-EMPTY": 31,       # age exited ZERO on nothing: the key is FINE
    "RESTORE-FAILED": 26,       # decrypt returned: the key WORKS, data is bad
    "STORE-UNREACHABLE": 27,
    "NO-ARTIFACT": 28,
}

# The three answers a byte comparison may give. Named constants because the test
# suite pins the literal strings and a verdict is a machine-readable claim.
CLASS_IDENTICAL = "IDENTICAL"
CLASS_TRAILING_NEWLINE = "DIFFERS-TRAILING-NEWLINE-ONLY"
CLASS_MATERIAL = "DIFFERS-MATERIALLY"


class EscrowError(B.BackupError):
    """A classified refusal: a token, a message, and the exit code it maps to.

    Subclasses `BackupError` for the same reason `RestoreVerifyError` does — the
    helpers reused from `backup.py` raise that type, and two exception
    hierarchies over one pipeline is how a failure escapes to a bare traceback.

    🔴 The exit code is DERIVED from the token via `EXIT_CODES`, never passed in.
    A constructor that accepted both could raise a token with the wrong code, and
    that is precisely the conflation this class exists to prevent.

    🔴 `verdict` AND `detail` ARE SEPARATE FIELDS, so the sentence THIS module
    asserts can be pinned by exact equality while the part it does not own —
    another module's message, an age exit code, a stderr fragment whose wording
    varies run to run — stays out of the pin.

    That split exists because a test asserting `"THE ESCROW IS FINE" in msg`
    passed unchanged on a TAMPERED artifact, certifying a sentence that was
    false on the very run it tested. A substring cannot tell a true message from
    a confident wrong one; exact equality on the owned half can.
    """

    def __init__(self, token: str, verdict: str, detail: str | None = None):
        if token not in EXIT_CODES:
            raise KeyError(
                f"{token!r} is not in EXIT_CODES. Every refusal in this script "
                f"is an ENUMERATED cause with its own exit code; adding one "
                f"means adding it to the table in the same commit as the test "
                f"that pins it.")
        super().__init__(verdict)
        self.token = token
        self.verdict = verdict
        self.detail = detail
        self.exit_code = EXIT_CODES[token]

    def __str__(self) -> str:
        base = f"{self.token}: {self.verdict}"
        return f"{base} [upstream: {self.detail}]" if self.detail else base


# --------------------------------------------------------------------------- #
# the `bw` seam
# --------------------------------------------------------------------------- #
def _default_runner(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess:
    """Run `bw`, structurally unable to prompt.

    Three independent belts, because the failure they prevent — an unattended
    run blocked forever on an invisible master-password prompt — is silent:

      * `--nointeraction` is passed by the caller on every command (a request);
      * stdin is /dev/null, so a prompt that ignores the flag reads EOF and dies
        (a mechanism, which is the half that does not depend on `bw`'s manners);
      * a timeout, so a hung network call ends the run rather than the shift.
    """
    return subprocess.run(
        argv, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=timeout, env=dict(os.environ))


class BitwardenCLI:
    """Every `bw` invocation this script makes, behind one injectable seam.

    🔴 `runner` and `locator` are BOTH injectable, and the second one is not an
    afterthought: the "`bw` is not installed" path is a first-class failure mode
    with its own message and exit code, and a suite that could only reach it by
    un-installing `bw` from the host would never reach it at all.

    🔴 NO METHOD HERE PUTS OUTPUT INTO AN EXCEPTION. `bw list items` returns full
    item objects, `notes` included — that is key material — and `bw status`
    carries the server URL and the account's email. There is deliberately no
    code path that interpolates a captured stream into a message, so no future
    edit has to remember not to.
    """

    def __init__(self, *, runner=None, locator=None, bw: str = "bw",
                 timeout: float = DEFAULT_TIMEOUT):
        self.runner = runner or _default_runner
        self.locator = locator or shutil.which
        self.bw = bw
        self.timeout = timeout

    # -- plumbing ----------------------------------------------------------- #
    def require_available(self) -> str:
        path = self.locator(self.bw)
        if path is None:
            raise EscrowError(
                "BW-MISSING",
                f"{self.bw!r} is not on PATH. It is NOT installed on either host "
                f"on purpose — run this under nix instead of installing it: "
                f"{NIX_SHELL_HINT}. Refusing to guess: an escrow that cannot be "
                f"read is not an escrow that is intact.")
        return path

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        argv = [self.bw, "--nointeraction", *args]
        try:
            return self.runner(argv, timeout=self.timeout)
        except FileNotFoundError:
            # The locator check above races with an uninstall, and a raw
            # FileNotFoundError here would surface as an unexplained traceback
            # rather than as the actionable message that already exists.
            raise EscrowError(
                "BW-MISSING",
                f"{self.bw!r} vanished between the PATH lookup and the call. "
                f"Run this under nix: {NIX_SHELL_HINT}.")
        except subprocess.TimeoutExpired:
            raise EscrowError(
                "BW-FAILED",
                f"`bw {args[0]}` did not finish within {self.timeout}s. It was "
                f"run with stdin on /dev/null and --nointeraction, so this is a "
                f"network or server stall rather than a password prompt — but "
                f"either way nothing was verified.")

    def _ok(self, p: subprocess.CompletedProcess, what: str) -> str:
        if p.returncode != 0:
            raise EscrowError(
                "BW-FAILED",
                f"`bw {what}` exited {p.returncode}. Its output is NOT quoted "
                f"here: `bw list items` returns note bodies (key material) and "
                f"`bw status` returns the server and the account email, and this "
                f"script never puts either into a message. Re-run the command by "
                f"hand to read it: {NIX_SHELL_HINT}")
        return p.stdout

    def _json(self, p: subprocess.CompletedProcess, what: str):
        out = self._ok(p, what)
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            raise EscrowError(
                "BW-FAILED",
                f"`bw {what}` exited 0 but did not return JSON ({len(out)} "
                f"bytes). Output withheld — it may contain key material. A "
                f"reply this script cannot parse is a question it did not get an "
                f"answer to, which is not the same as a clean answer.")

    # -- the four questions ------------------------------------------------- #
    def status(self) -> dict:
        d = self._json(self._run("status"), "status")
        if not isinstance(d, dict):
            raise EscrowError(
                "VAULT-STATUS-UNKNOWN",
                "`bw status` returned JSON that is not an object, so the vault's "
                "state is UNKNOWN. An unreadable state must never be treated as "
                "`unlocked`.")
        return d

    def config_server(self) -> str:
        return self._ok(self._run("config", "server"), "config server").strip()

    def search_items(self, name: str) -> list[dict]:
        d = self._json(self._run("list", "items", "--search", name),
                       "list items --search <name>")
        if not isinstance(d, list):
            raise EscrowError(
                "BW-FAILED",
                "`bw list items` returned JSON that is not a list. Refusing to "
                "guess at its shape; nothing was verified.")
        return d


# --------------------------------------------------------------------------- #
# the comparison
# --------------------------------------------------------------------------- #
def classify(escrow: bytes, disk: bytes) -> str:
    """One of the three CLASS_* constants. Pure; reads nothing, prints nothing.

    🔴 THE TRAILING-NEWLINE CASE IS ITS OWN ANSWER, not a shade of "mismatch".
    An age identity keeps working when its final newline is trimmed, and a note
    round-tripped through a web vault is exactly where a trim happens. So the
    two differences have different remedies — one is `printf '\\n' >> key`, the
    other is "the escrow is wrong, re-escrow it" — and a verifier that gave them
    one word would send the operator to the second remedy for the first fault.

    The rule is narrow on purpose: equal after removing TRAILING `\\n` bytes from
    both sides, and not already equal. A CRLF rewrite changes every line ending,
    not just the last byte, and is reported as material — which is honest, since
    that is what it is.
    """
    if escrow == disk:
        return CLASS_IDENTICAL
    if escrow.rstrip(b"\n") == disk.rstrip(b"\n"):
        return CLASS_TRAILING_NEWLINE
    return CLASS_MATERIAL


def _shred(path: Path) -> None:
    """Overwrite then unlink `path`. Never raises.

    🔴 The overwrite is BEST EFFORT and this docstring is the widest claim that
    is true: on a journalling or copy-on-write filesystem the old blocks may
    survive it. The UNLINK is the part that is guaranteed, the part the test
    suite asserts, and the part that matters here — the file lives for
    milliseconds inside a 0700 directory under the process's own temp root.
    Claiming "securely erased" would be a comment the code cannot support, which
    is the failure this subsystem's rules are most emphatic about.

    🔴 A SYMLINK IS UNLINKED, NEVER FOLLOWED. `open(path, "r+b")` follows one,
    so the previous version would ZERO THE TARGET and then remove only the link
    — a truncate-in-place primitive aimed at any path the user can write, left
    behind as a zero-filled decoy. Measured via `--work-dir`. The link itself is
    the only thing this function may destroy.
    """
    try:
        if path.is_symlink():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                # 🔴 `return`, NOT fall-through. A bare `except OSError: pass`
                # here dropped into the `open(path, "r+b")` below — which
                # FOLLOWS the link and zeroes the target: the exact
                # truncate-in-place primitive this branch exists to remove,
                # reachable whenever the unlink fails (a read-only directory, a
                # race). The docstring's "the link itself is the only thing this
                # function may destroy" was false on that path.
                pass
            return
    except OSError:
        pass
    try:
        size = path.stat().st_size
        with open(path, "r+b") as fh:
            fh.write(b"\0" * size)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        pass
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _create_private_file(path: Path) -> int:
    """Open `path` as a FRESH 0600 regular file. Returns the fd.

    🔴 `O_CREAT|O_TRUNC` APPLIES THE MODE ONLY ON CREATE, and follows a symlink.
    That made the module's "0600 inside a 0700 directory" claim true only on the
    path where nothing was there already — and `--work-dir` is precisely the
    option that hands this a directory somebody else populated. MEASURED:

      * a pre-existing 0666 file RECEIVED the escrowed key and STAYED 0666;
      * a pre-existing SYMLINK had the key written THROUGH it to a 0644 file
        OUTSIDE the 0700 directory.

    So: unlink whatever is there (`_shred` above refuses to follow a link),
    then create with `O_EXCL|O_NOFOLLOW` so the open FAILS rather than reusing
    or following anything, and `fchmod` the fd we actually hold — the umask can
    still mask the `os.open` mode argument, and fchmod cannot be redirected.
    """
    _shred(path)
    fd = os.open(path,
                 os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 _FILE_MODE)
    try:
        os.fchmod(fd, _FILE_MODE)
    except BaseException:
        os.close(fd)
        raise
    return fd


# --------------------------------------------------------------------------- #
# reusing the restore verifier — never reimplementing it
# --------------------------------------------------------------------------- #
_RV = None


def _rv():
    """Import `restore-verify.py` (hyphenated file name), once, lazily.

    🔴 IMPORTED RATHER THAN REIMPLEMENTED. `verify_artifact()` already owns the
    whole download -> `age -d` -> `git clone --mirror` -> `git fsck` ->
    cross-check pipeline AND the `finally` that unlinks the plaintext bundle on
    every path out. A second copy of that pipeline would be a second copy of
    that `finally`, and the copy nobody exercises is the broken one.

    Lazy because the byte check — the default, and the only thing a locked-vault
    run reaches — needs none of it.

    `sys.modules[...] = mod` BEFORE `exec_module` is required, not tidiness:
    `@dataclass` resolves annotations by looking the defining module up in
    `sys.modules`. See the same dance in the restore verifier's test suite.
    """
    global _RV
    if _RV is not None:
        return _RV
    if "restore_verify" in sys.modules:
        _RV = sys.modules["restore_verify"]
        return _RV
    path = _HERE / "restore-verify.py"
    spec = importlib.util.spec_from_file_location("restore_verify", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["restore_verify"] = mod
    spec.loader.exec_module(mod)
    _RV = mod
    return mod


# 🔴 WHAT THE PROBE BELOW OBSERVES — RE-MEASURED, age v1.3.1, MANY SAMPLES.
#
# An earlier round wrote down "corrupt ciphertext -> rc=1, PLAIN absent" from a
# SINGLE corrupted offset and built the classifier on it. That is false, and the
# way it is false is the worst possible one: a TAMPERED artifact was reported as
# `ARTIFACT-EMPTY` — "THE ESCROW IS FINE … a valid encryption of an empty
# payload" — while quoting age's own "may be corrupted or tampered with" in the
# same sentence. Detecting tampering is the single most important thing a backup
# verifier does. One measurement is not a general claim; here is the general one:
#
#   WRONG KEY          5 payload sizes (0 B … 2 MB)      rc=1  PLAIN **ABSENT**
#   HEADER corrupt     2 sizes                           rc=1  PLAIN **ABSENT**
#   PAYLOAD corrupt    8 offsets x 4 sizes               rc=1  PLAIN PRESENT
#                                                              (size 0 or partial)
#   TRUNCATED          3 sizes                           rc=1  PLAIN PRESENT
#   valid enc of NOTHING                                 rc=0  PLAIN PRESENT, 0
#   intact                                               rc=0  PLAIN PRESENT, N
#
# ⚠ A no-op mutation nearly produced a second false table: writing 0xff over a
# byte that was ALREADY 0xff left one sample reading rc=0, which would have said
# age fails to detect tampering. Re-run with a guaranteed bit flip (XOR 0xFF),
# all 8 offsets give rc=1. Verify the mutation actually mutates.
#
# TWO CONSEQUENCES, BOTH LOAD-BEARING:
#
#  1. `plain_present` CANNOT separate "valid encryption of nothing" from
#     "corrupt payload" — both are PRESENT at size 0. So the rc==0 / rc!=0 split
#     has to come from the module that knows, and it does:
#     `restore_verify.DECRYPT_*` causes, published as values.
#  2. Within a NON-ZERO age exit, file presence IS meaningful and is the only
#     thing that separates a KEY fault from a DATA fault:
#       * PLAIN ABSENT  -> age never wrote anything: the identity did not match
#                          the recipients, OR the header is damaged. NOT
#                          separable from outside, so neither is asserted.
#       * PLAIN PRESENT -> age AUTHENTICATED THE HEADER, which requires the
#                          identity to match, and then failed on a payload chunk
#                          or ran out of input. The escrowed key WORKED; the
#                          artifact is tampered, corrupt or truncated.
@contextlib.contextmanager
def _decrypt_phase_probe(RV):
    """Observe HOW FAR restore-verify's decrypt step got. Yields a state dict.

    🔴 THIS IS A PROBE, NOT A REIMPLEMENTATION. It calls the real `decrypt()`
    and adds nothing to it; all it does is record whether the call was reached,
    whether it returned, and — at the instant it raises, before
    `verify_artifact`'s own `finally` unlinks the plaintext — whether `age` left
    an output file behind. Classification is then derived from an OBSERVED
    PHASE rather than from a substring of a message this module does not own.

    ⚠ It swaps a module global for the duration, so it is not safe against
    concurrent use of `restore_verify` in the same process. This is a
    single-shot CLI that owns its own import; the swap is restored in a
    `finally`.
    """
    state = {"reached": False, "returned": False, "plain_present": None,
             "cause": None}
    real = RV.decrypt

    def probe(cipher, plain, identity):
        state["reached"] = True
        try:
            real(cipher, plain, identity)
        except BaseException as exc:
            # 🔴 `Path.exists()` CAN RAISE ON THIS INTERPRETER, and an earlier
            # comment here asserted the opposite. MEASURED on a directory without
            # `+x`, three interpreters, twice each (behaviour, and whether
            # `Path.exists`'s source still calls `_ignore_error`):
            #
            #     3.12.14 (the flake's pin)  raises PermissionError   _ignore_error: yes
            #     3.13.15                    raises PermissionError   _ignore_error: yes
            #     3.14.7                     returns False            _ignore_error: no
            #
            # `_ignore_error` swallows only ENOENT/ENOTDIR/EBADF/ELOOP, so EACCES
            # propagates; 3.14 dropped that helper from `exists()` and swallows
            # unconditionally. 🔴 THE BOUNDARY IS 3.14, NOT 3.13 — an earlier
            # revision of this comment said 3.13 and was wrong, and a test written
            # to that boundary would have gone red on 3.13 for no reason anyone
            # could find. Measure the interpreter, do not reason about it.
            #
            # Unhandled, it would replace the REAL exception mid-`except` — the
            # same masking class removed for `phase` — and the run would report a
            # permissions error instead of whatever age actually did. An
            # unreadable path is simply an unobservable one: None, never False,
            # so no branch downstream can mistake it for "age wrote nothing".
            try:
                state["plain_present"] = Path(plain).exists()
            except OSError:
                state["plain_present"] = None
            # The rc==0-vs-rc!=0 split, taken as a VALUE from the module that
            # owns it. `getattr` because a non-RestoreVerifyError has no cause;
            # None then means "no published cause", never a default one.
            state["cause"] = getattr(exc, "cause", None)
            raise
        state["returned"] = True

    RV.decrypt = probe
    try:
        yield state
    finally:
        RV.decrypt = real


# --------------------------------------------------------------------------- #
# verdict
# --------------------------------------------------------------------------- #
@dataclass
class EscrowVerdict:
    """What this run PROVED. Every field is a measured number or a literal."""
    item_name: str
    server: str
    server_pinned: bool
    escrow_bytes: int
    disk_bytes: int
    classification: str
    identity: Path
    # None means the CLI's configured server really WAS compared against the
    # authenticated session's; a string is the reason it could not be. See
    # `check_server` — a skipped comparison used to be reported as a passing one.
    server_session_reason: str | None = None
    decrypt_checked: bool = False
    decrypt_scope: str | None = None
    decrypt_key: str | None = None
    decrypt_commits: int | None = None
    decrypt_refs: int | None = None

    # What chose `identity`, when a caller stated it. 🔴 DISCLOSED ON THE
    # SUCCESS LINE TOO: "escrow OK — the Secure Note matches <path>" is exactly
    # where a comparison against a file nobody intended is least likely to be
    # questioned, and this module's stated stance is that a false all-clear is
    # the worst outcome in this subsystem.
    identity_source: str | None = None

    def line(self) -> str:
        head = (f"{PROG}: escrow OK — the Secure Note matches {self.identity} "
                f"{self.classification} ({self.escrow_bytes} escrowed bytes vs "
                f"{self.disk_bytes} on disk)")
        if (self.identity_source is not None
                and not B.same_identity_file(self.identity, B.DEFAULT_IDENTITY)):
            head += (f" 🔴 NOTE: that is NOT the default identity — it was "
                     f"chosen by {self.identity_source}, so this 'matches' is a "
                     f"claim about that file, not about "
                     f"{B.DEFAULT_IDENTITY}")
        # 🔴 TWO INDEPENDENT FACTS, NEVER MERGED INTO ONE SENTENCE: whether the
        # operator PINNED a server, and whether the session cross-check could be
        # made at all. The old line asserted the second unconditionally while
        # the code skipped it silently.
        pin = ("server PINNED and matched" if self.server_pinned
               else "server NOT PINNED (--expect-server / ASIB_ESCROW_SERVER "
                    "unset)")
        if self.server_session_reason is None:
            session = ("session cross-check RAN: the CLI's configured server "
                       "matched the authenticated session's")
        else:
            session = (f"session cross-check NOT COMPARED "
                       f"({self.server_session_reason})")
        pin = f"{pin}; {session}"
        if not self.decrypt_checked:
            return (f"{head}; {pin}; NOT DECRYPT-CHECKED — byte equality proves "
                    f"the two copies agree, NOT that either of them opens an "
                    f"artifact. Re-run with --decrypt-check for that claim.")
        return (f"{head}; {pin}; DECRYPT-CHECKED: the ESCROWED bytes decrypted "
                f"{self.decrypt_key} (scope {self.decrypt_scope}) and restored "
                f"{self.decrypt_commits} commit(s) over {self.decrypt_refs} "
                f"ref(s)")


# --------------------------------------------------------------------------- #
# the steps
# --------------------------------------------------------------------------- #
def read_identity(identity: Path) -> bytes:
    """The on-disk age identity's bytes, or a classified refusal.

    🔴 AN EMPTY IDENTITY IS A FAILURE, not one half of a clean comparison. Two
    empty files are byte-identical, so without this the whole run would report
    `IDENTICAL` over nothing at all — the exact false all-clear this script was
    written to prevent, arrived at by the shortest possible route.
    """
    if not identity.is_file():
        raise EscrowError(
            "IDENTITY-MISSING",
            f"no age identity at {identity}. This compares the ESCROWED copy "
            f"against the one this machine holds; with no local copy there is "
            f"nothing to compare and 'could not compare' must never be reported "
            f"as 'compared and matched'. Set ASIB_AGE_IDENTITY or "
            f"SOPS_AGE_KEY_FILE (see SECRETS.md).")
    data = identity.read_bytes()
    if not data.strip():
        raise EscrowError(
            "IDENTITY-EMPTY",
            f"the age identity at {identity} is empty or whitespace only "
            f"({len(data)} bytes). Two empty files compare EQUAL, so continuing "
            f"would report a matching escrow of nothing.")
    return data


def check_vault_state(bw: BitwardenCLI) -> dict:
    """`bw status` is the authority. Anything but `unlocked` ends the run."""
    st = bw.status()
    state = st.get("status")
    if state == "unlocked":
        return st
    if state == "locked":
        raise EscrowError(
            "VAULT-LOCKED",
            "the vault is LOCKED. The master password CANNOT be automated and "
            "this script will not prompt for it — it runs every `bw` call with "
            "stdin on /dev/null so an unattended run fails fast instead of "
            "hanging on an invisible prompt. Unlock it yourself, then re-run "
            "with the session exported:\n"
            "    export BW_SESSION=\"$(bw unlock --raw)\"\n"
            "and if `bw` is not on PATH, do both inside one shell: "
            + NIX_SHELL_HINT)
    if state == "unauthenticated":
        raise EscrowError(
            "VAULT-UNAUTHENTICATED",
            "the CLI is NOT LOGGED IN to any vault — this is not the same as a "
            "locked vault and the remedy is different: `bw login` (which needs "
            "the account email and, if enabled, a second factor), THEN "
            "`export BW_SESSION=\"$(bw unlock --raw)\"`. Check `bw config "
            "server` points at the right server before logging in.")
    raise EscrowError(
        "VAULT-STATUS-UNKNOWN",
        f"`bw status` reported status={state!r}, which this script does not "
        f"recognise. Refusing to proceed: an unrecognised state is an UNKNOWN "
        f"one, and treating an unknown as `unlocked` is how a run reports a "
        f"clean escrow it never read.")


def check_server(bw: BitwardenCLI, status: dict,
                 expect: str | None) -> tuple[str, bool, str | None]:
    """`(configured server, was it pinned, why the session was NOT compared)`.

    🔴 THE ENDPOINT IS READ, NEVER HARDCODED — devrc is public. Two checks:

      * the CLI's configured server against the AUTHENTICATED SESSION's own
        `serverUrl`. This vault has actually been repointed once, and a session
        left over from the old endpoint is a state where every answer below
        comes from a server the operator no longer thinks they are using.
      * `--expect-server` / `ASIB_ESCROW_SERVER`, when the operator sets one.
        Absent, the run reports NOT PINNED rather than implying it checked.

    🔴 THE SESSION COMPARISON CAN BE UNAVAILABLE, AND SAYING SO IS THE WHOLE
    POINT. `bw status` may omit `serverUrl` or return `null` (that is what it
    prints for the official cloud). The first version did
    `if session_server and …`, which SKIPPED the comparison with no signal
    whatsoever — while the success verdict went on to print "the CLI's
    configured server matched the session's, which is all that was checked".
    Measured twice, exit 0 both times: a check that did not run, reported as a
    check that passed. The docstring above it even said "this half needs no pin
    and ALWAYS RUNS", which was simply false.

    So the third return value is the REASON it could not be compared, or None
    when it really was. `EscrowVerdict.line()` prints the distinction, the same
    way `restore-verify.py` prints `NOT CROSS-CHECKED (<reason>)` rather than
    folding an unmeasured scope into the word used for a measured one. It is
    NOT a hard failure: a vault legitimately reachable without a `serverUrl` in
    `bw status` must not be a permanently-red gate.
    """
    configured = bw.config_server()
    if not configured:
        raise EscrowError(
            "SERVER-UNKNOWN",
            "`bw config server` printed nothing, so which server answered "
            "cannot be determined. An escrow verified against an unknown server "
            "is an escrow verified against no server in particular.")
    # 🔴 THE PIN CHECK LIVES IN EXACTLY ONE PLACE. It was open-coded at two
    # sites — the session-unavailable early return and the normal path — with
    # DIFFERENT WORDING for the same refusal, which is how one copy drifts and
    # the disagreement becomes inaudible. Hoisted above both branches, so it
    # cannot be skipped by whichever path is taken.
    _refuse_unless_pin_matches(expect, configured)

    raw_session = status.get("serverUrl")
    session_server = (raw_session or "").strip()
    if not session_server:
        why = ("`bw status` reported serverUrl="
               + ("null" if raw_session is None else "empty")
               + " — the CLI's configured server could NOT be cross-checked "
                 "against the authenticated session's")
        return configured, expect is not None, why
    if _norm_url(session_server) != _norm_url(configured):
        raise EscrowError(
            "SERVER-MISMATCH",
            "the CLI's CONFIGURED server and the AUTHENTICATED SESSION's server "
            "disagree, so the item this run would read comes from a different "
            "place than `bw config server` reports. (Neither URL is printed: "
            "this repo is public and messages from it end up pasted into it.) "
            "Re-run `bw config server` and `bw status` by hand to see both, "
            "then `bw logout` and log in against the intended one.")
    return configured, expect is not None, None


def _refuse_unless_pin_matches(expect: str | None, configured: str) -> None:
    """The `--expect-server` pin, in ONE place. No-op when nothing is pinned."""
    if expect is None or _norm_url(expect) == _norm_url(configured):
        return
    raise EscrowError(
        "SERVER-MISMATCH",
        "the configured server does not match the one pinned by "
        "--expect-server / ASIB_ESCROW_SERVER. Refusing to report an escrow "
        "found on a server that is not the one you named — URLs withheld, this "
        "repo is public; compare them by hand with `bw config server`.")


def _norm_url(u: str) -> str:
    """Compare URLs without tripping over a trailing slash or letter case."""
    return u.strip().rstrip("/").lower()


def find_escrow_item(bw: BitwardenCLI, item_name: str) -> dict:
    """The one Secure Note carrying `item_name`, or a classified refusal.

    🔴 `bw list items --search` IS FUZZY, so its result set is a candidate list
    and not an answer. The match here is an exact comparison on `name`, and the
    three ways it can fail to produce exactly one item are three findings:

      * NONE   — the escrow is gone (or was never made under this name), and the
                 key has no second copy anywhere;
      * TWO+   — this script CANNOT TELL WHICH ONE IS THE ESCROW. Picking either
                 is a coin flip recorded as a verdict; a stale duplicate that
                 still holds a rotated-out key would verify green forever;
      * WRONG TYPE — the name is right and the item is not a Secure Note. The
                 guard is on the item's TYPE (state), not on a word another item
                 can spell.
    """
    matches = [it for it in bw.search_items(item_name)
               if isinstance(it, dict) and it.get("name") == item_name]
    if not matches:
        raise EscrowError(
            "ITEM-NOT-FOUND",
            f"no vault item is named exactly {item_name!r}. This is the whole "
            f"off-machine copy of the decryption key being absent: every "
            f"artifact in the bucket is still there and none of them can be "
            f"opened without the identity on this one disk. `bw list items "
            f"--search` was used and it is FUZZY, so a near-miss would have "
            f"been returned and rejected here — check for a renamed item before "
            f"concluding it was deleted.")
    if len(matches) > 1:
        raise EscrowError(
            "ITEM-AMBIGUOUS",
            f"{len(matches)} vault items are named exactly {item_name!r}. This "
            f"script cannot tell which one is the escrow, and choosing one would "
            f"turn a coin flip into a verdict — a stale duplicate holding a "
            f"rotated-out key verifies green forever. Delete or rename the "
            f"duplicates so exactly one remains.")
    item = matches[0]
    if item.get("type") != ITEM_TYPE_SECURE_NOTE:
        raise EscrowError(
            "ITEM-WRONG-TYPE",
            f"the item named {item_name!r} has type={item.get('type')!r}, not "
            f"{ITEM_TYPE_SECURE_NOTE} (Secure Note). The escrow was created as a "
            f"Secure Note; an item of another type carrying the same name is a "
            f"different object that happens to be spelled the same.")
    return item


def read_note(item: dict, item_name: str) -> bytes:
    """The note body as bytes, or a classified refusal. Never logged.

    🔴 AN ABSENT FIELD IS NOT AN EMPTY VALUE. `item.get("notes")` collapses the
    two, and they lead to OPPOSITE actions: `NOTE-EMPTY` tells the operator the
    escrow was emptied and to re-escrow, which — if `bw` merely OMITTED the key
    for an intact note — overwrites a good copy on the strength of a parsing
    accident. `"notes" in item` is the whole fix.
    """
    # 🔴 ABSENT AND NULL ARE THE SAME FINDING HERE, DELIBERATELY. JSON's shape
    # for "this item has no notes" is almost certainly `{"notes": null}`, not an
    # omitted key — the very shape this module already handles for `serverUrl`
    # in `check_server`. Splitting on `"notes" not in item` alone let a null fall
    # through to `NOTE-EMPTY`, i.e. straight back to the "re-escrow over a good
    # copy" advice this token was created to prevent.
    #
    # `.get(...) is None` covers both, so there is no sentinel: an earlier
    # version carried one whose comment claimed the two cases "can be told apart
    # where that matters" while its only consumer merged them. They are merged
    # because the REMEDY is the same — read the item in the web vault before
    # touching the escrow.
    if item.get("notes") is None:
        raise EscrowError(
            "NOTE-MISSING",
            f"the vault item {item_name!r} exists but its payload carries NO "
            f"`notes` VALUE — the field is absent or null, not empty. That is a "
            f"`bw` output-schema answer, not evidence the escrow was emptied, "
            f"and the note may be perfectly intact. Do NOT re-escrow on this: "
            f"read the item in the web vault first.")
    notes = item["notes"]
    if not isinstance(notes, str) or not notes.strip():
        raise EscrowError(
            "NOTE-EMPTY",
            f"the vault item {item_name!r} exists and its note body is EMPTY "
            f"(the field is PRESENT and carries nothing). An empty note lists, "
            f"syncs and exports exactly like a full one, so this is the quiet "
            f"way for an escrow to be gone while every count still says it is "
            f"there.")
    return notes.encode("utf-8")


# --------------------------------------------------------------------------- #
# --decrypt-check
# --------------------------------------------------------------------------- #
def decrypt_check(*, escrow_bytes: bytes, work_dir: Path, bucket: str,
                  prefix: str, store: Path, scope_filter: str | None,
                  from_dir: Path | None, now: datetime,
                  downloader_factory=None) -> tuple[str, str, int, int]:
    """Decrypt a REAL artifact with the ESCROWED bytes. `(scope, key, commits, refs)`.

    🔴 THE ESCROWED BYTES, NOT THE ON-DISK KEY. The whole point: the on-disk key
    demonstrably works — `restore-verify.py` uses it — and proving it again
    would be a test of the wrong copy. The identity handed to the restore
    pipeline here is written from what came back from the vault.

    🔴 THE PIPELINE IS `restore_verify.verify_artifact`, CALLED, NOT COPIED. That
    function owns the plaintext-bundle `finally`, the 0600/0700 tightening and
    the `git fsck`; a second implementation would be a second chance to get the
    plaintext lifetime wrong.

    🔴 ONE ARTIFACT, NOT A FULL RUN. `run()` in the restore verifier also gates
    on artifact staleness and on local scopes missing from the bucket. Both are
    real faults and neither is a fact about the KEY, so folding them in would
    make a stale-timer failure indistinguishable from a broken escrow — the
    conflation this file's exit table exists to prevent. `restore-verify.py` is
    the tool that reports those; this one answers "does the escrowed key open
    what is actually in the bucket".
    """
    RV = _rv()
    prefix = RV.normalise_prefix(prefix)

    # 🔴 A PRECONDITION, CHECKED BEFORE THE STORE IS EVEN OPENED, AND IT IS
    # LOAD-BEARING TWICE.
    #
    #   1. `age` missing is an ENVIRONMENT fault. Left to fall through, it
    #      surfaced as RESTORE-FAILED — "the escrowed key works, the artifact is
    #      at fault" — three false claims in one sentence, sending the operator
    #      to `restore-verify.py`, where it fails identically for the same
    #      reason nobody has named yet.
    #   2. It is what makes the phase probe's inference sound. restore-verify's
    #      `decrypt()` checks `age` on PATH FIRST and raises before touching the
    #      output file, so without this an age-missing failure would be
    #      indistinguishable from "age ran and refused" — the two would share
    #      DECRYPT-FAILED and blame the escrow for a missing package.
    #
    # This duplicates restore-verify's own check by design: the point is not to
    # guard the call (it guards itself) but to CLASSIFY the cause here, where
    # the exit code is chosen.
    if shutil.which("age") is None:
        raise EscrowError(
            "AGE-MISSING",
            "`age` is not on PATH, so the escrowed key cannot be tested against "
            "anything. This is an ENVIRONMENT fault and says NOTHING about the "
            "escrow — do not read it as a verdict on the key or on the "
            "artifacts. age is declared in nix/pkgs/default.nix and in "
            "flake.nix `gateTools`; add it there, or run this whole command "
            "under nix.")

    if downloader_factory is not None:
        factory = downloader_factory
    elif from_dir is not None:
        factory = lambda: RV.DirectoryStore(from_dir)   # noqa: E731
    else:
        factory = lambda: RV.MinioDownloader(bucket)    # noqa: E731

    try:
        ctx = factory()
        downloader = ctx.__enter__()
    except Exception as exc:
        # 🔴 CLASSIFIED BY PHASE, NOT BY MESSAGE. Everything that can go wrong
        # while OPENING the store — no kubeconfig, a missing bucket, an S3Error,
        # a urllib3 connection reset, a --from-dir that is not a directory — is
        # "could not reach the artifacts", and none of it is a fact about the
        # escrowed key. Reading a string to decide that would make the
        # classification depend on wording somebody else owns.
        raise EscrowError(
            "STORE-UNREACHABLE",
            "could not open the artifact store to test the escrowed key. "
            "NOTHING about the escrow was proven by this run beyond the byte "
            "comparison above.",
            detail=f"{type(exc).__name__}: {exc}")

    try:
        keys = sorted(downloader.list(prefix))
        if not keys:
            raise EscrowError(
                "NO-ARTIFACT",
                f"zero objects under {bucket}/{prefix}, so there is nothing for "
                f"the escrowed key to open. This is NOT a clean decrypt-check: "
                f"a key that was never asked to decrypt anything has not been "
                f"shown to work. `restore-verify.py` is the tool that diagnoses "
                f"an empty prefix — it can tell 'the backups are gone' from 'you "
                f"looked under the wrong prefix', and this script deliberately "
                f"does not duplicate that diagnosis.")
        by_scope = RV.group_by_scope(keys, prefix)
        if scope_filter is not None:
            if scope_filter not in by_scope:
                raise EscrowError(
                    "NO-ARTIFACT",
                    f"scope {scope_filter!r} has zero objects under "
                    f"{bucket}/{prefix}. Scopes that do: {sorted(by_scope)}.")
            scope = scope_filter
        else:
            # The scope holding the NEWEST artifact overall. `sorted()` first so
            # a tie resolves lexicographically rather than by dict order — a
            # verifier that picks a different artifact on different runs cannot
            # be reasoned about when it disagrees with itself.
            scope = max(sorted(by_scope), key=lambda s: by_scope[s][-1][0])
        stamp, key = by_scope[scope][-1]

        this_host = B.host_label()
        this_machine_id = RV.machine_id()
        live, no_live_reason = RV.cross_check_target(
            store, scope,
            artifacts_are_foreign=not RV.prefix_belongs_to_this_host(
                prefix, this_host, this_machine_id),
            machine_id_unreadable=(this_machine_id is None))

        restore_dir = B._private_dir(work_dir / RESTORE_SUBDIR)
        identity = work_dir / ESCROW_IDENTITY_FILENAME
        try:
            # 🔴 0600 BEFORE THE BYTES, on a file that cannot be a pre-existing
            # one or a symlink. See `_create_private_file` for the two measured
            # ways the previous `O_CREAT|O_TRUNC` spelling left the mode claim
            # false.
            with os.fdopen(_create_private_file(identity), "wb") as fh:
                fh.write(escrow_bytes)

            # 🔴 THE HANDLER LIVES INSIDE THE `with`, NOT AFTER IT — so `phase`
            # is bound before the `try` is ever entered and CANNOT be read
            # unbound. Written the other way round (handler outside the `with`)
            # the only unbound path needed `__enter__` to raise
            # `RestoreVerifyError`, which its setup cannot do — unreachable, and
            # left alone it would have been an `UnboundLocalError` MASKING the
            # real exception in the one classifier whose whole purpose is to stop
            # confidently-wrong verdicts. A context manager's `__enter__` is a
            # recurring blind spot in this subsystem (the sibling file's
            # `MinioArchive.__enter__` was the same shape), so the class is
            # removed by structure rather than argued away as unreachable.
            with _decrypt_phase_probe(RV) as phase:
                try:
                    v = RV.verify_artifact(
                        downloader, key, scope=scope, stamp=stamp,
                        identity=identity, work_dir=restore_dir, store=store,
                        keep=False, now=now, live_scope=live,
                        no_live_reason=no_live_reason)
                except RV.RestoreVerifyError as exc:
                    # 🔴 CLASSIFIED BY THE PHASE THAT WAS OBSERVED, NOT BY A
                    # SUBSTRING. Each branch asserts only what `phase` actually
                    # witnessed; nothing here says "DECRYPTED" on a path where
                    # `decrypt()` was not reached, and nothing blames the escrow
                    # for a fault the escrow cannot have caused.
                    if not phase["reached"]:
                        raise EscrowError(
                            "ARTIFACT-UNREADABLE",
                            f"the pipeline failed BEFORE the escrowed key was "
                            f"ever used, on {key} — an empty or unreadable "
                            f"object, not a fact about the escrow. NOTHING here "
                            f"decrypted, and nothing here is evidence for or "
                            f"against the escrowed key. Run `restore-verify.py` "
                            f"to diagnose the object.",
                            detail=str(exc))
                    if phase["cause"] == RV.DECRYPT_AGE_MISSING:
                        # Belt: the precondition at the top of `decrypt_check`
                        # normally wins, so this is reachable only if `age`
                        # disappears mid-run. It must never be read as a verdict
                        # on the escrow.
                        raise EscrowError(
                            "AGE-MISSING",
                            "`age` vanished between the precondition check and "
                            "the decrypt call, so the escrowed key was never "
                            "tested. This is an ENVIRONMENT fault and says "
                            "NOTHING about the escrow or the artifacts.",
                            detail=str(exc))
                    if not phase["returned"]:
                        # 🔴 `returned == False` MEANS `decrypt()` RAISED. The
                        # previous version's comment here read "age exited 0 and
                        # produced an EMPTY plaintext" — contradicting its own
                        # enclosing condition, and that contradiction is exactly
                        # where the tampered-artifact bug lived. Which of age's
                        # two failure branches fired is taken as a VALUE from
                        # restore-verify, never inferred from file presence.
                        if phase["cause"] == RV.DECRYPT_EMPTY_PLAINTEXT:
                            raise EscrowError(
                                "ARTIFACT-EMPTY",
                                f"the ESCROWED key OPENED {key} and the artifact "
                                f"contains NOTHING. 🔴 THE ESCROW IS FINE — age "
                                f"exited ZERO, which a wrong key cannot make it "
                                f"do; the payload is a valid encryption of an "
                                f"empty file. Do NOT re-escrow or rotate on the "
                                f"strength of this. Run `restore-verify.py` to "
                                f"diagnose the artifact.",
                                detail=str(exc))
                        if (phase["cause"] == RV.DECRYPT_AGE_REFUSED
                                and phase["plain_present"]):
                            # 🔴 THE CAUSE IS PART OF THE CONDITION, not decoration.
                            # This branch makes the strongest claim in the file —
                            # "the key worked, the BACKUP is tampered" — and it was
                            # keyed on `plain_present` ALONE, treating a `cause` of
                            # None identically to `age-refused`. A sweep confirmed
                            # it: dropping `cause=DECRYPT_AGE_REFUSED` from
                            # restore-verify's raise survived the whole suite, so
                            # the published set was 2-for-3 load-bearing plus a
                            # claim. Now it is 3-for-3 — and it is the RAISE-SIDE
                            # mutant that proves it, because that one is killed.
                            #
                            # ⚠ Deleting the cause test HERE is currently an
                            # EQUIVALENT mutant and the sweep reports it as a
                            # labelled survivor: the two branches above consume
                            # `empty-plaintext` and `age-missing`, so the only
                            # cause that can still reach this line is
                            # `age-refused`. It is kept because that is a
                            # property of the branches above it, not of this
                            # condition — publish a fourth cause and this line is
                            # the one that stops the strongest claim in the file
                            # being made about it by default.
                            #
                            # age exited NON-ZERO having already written output.
                            # Measured across 8 offsets x 4 sizes plus 3
                            # truncations: reaching the payload at all means age
                            # authenticated the HEADER, which requires the
                            # identity to match. So the key worked and the bytes
                            # did not.
                            raise EscrowError(
                                "ARTIFACT-CORRUPT",
                                f"🔴 {key} is TAMPERED, CORRUPT or TRUNCATED. age "
                                f"authenticated the header with the ESCROWED key "
                                f"— which a non-matching identity cannot do — "
                                f"began writing plaintext, and then FAILED on the "
                                f"payload. THE ESCROW IS FINE; THE BACKUP IS NOT. "
                                f"This is the finding a backup verifier exists to "
                                f"make: treat the artifact as unusable, check the "
                                f"other retained objects for this scope, and do "
                                f"NOT rotate the key.",
                                detail=str(exc))
                        raise EscrowError(
                            "DECRYPT-FAILED",
                            f"age REFUSED {key} without writing any plaintext at "
                            f"all. TWO CAUSES PRODUCE THIS AND THEY ARE NOT "
                            f"SEPARABLE FROM HERE: the escrowed identity does not "
                            f"match this artifact's recipients, or the artifact's "
                            f"HEADER is damaged. Neither is asserted. To tell them "
                            f"apart, try a DIFFERENT artifact with this same "
                            f"escrowed copy — `--scope <another scope>`, or "
                            f"`restore-verify.py --all` for an older stamp: if "
                            f"another artifact OPENS, the escrowed key is fine and "
                            f"THIS object's header is damaged; if none open, the "
                            f"key is the likely cause. Do NOT rotate the key "
                            f"before running that.",
                            detail=str(exc))
                    raise EscrowError(
                        "RESTORE-FAILED",
                        f"the ESCROWED bytes DECRYPTED {key} — `decrypt()` "
                        f"RETURNED, which is what makes that claim observable — "
                        f"and the restore then failed. This is a fault in the "
                        f"ARTIFACT, not in the escrow. The escrowed key works; "
                        f"run `restore-verify.py` to diagnose the artifact.",
                        detail=str(exc))
                return scope, key, v.commits_restored, v.refs_restored
        finally:
            # 🔴 EVERY PATH OUT: success, either classified failure, and any
            # unexpected exception from the pipeline. A failed run is exactly
            # when a copy of a decryption key is most likely to be left behind
            # and least likely to be noticed.
            _shred(identity)
            shutil.rmtree(restore_dir, ignore_errors=True)
    finally:
        ctx.__exit__(None, None, None)


# --------------------------------------------------------------------------- #
# provenance
# --------------------------------------------------------------------------- #
# The exact sentence `--print-plan` prints when the on-disk file is NOT the
# default. A module constant so the test suite can pin it WHOLE: a guard that
# asserts only the fragment "NOT the default" is satisfied by any other
# sentence containing it, including a false one — which is how the first
# revision of this feature shipped a line claiming an `--identity` flag could
# be "set by an unrelated shell".
NON_DEFAULT_NOTE = "  <- NOT the default identity"


def preflight_decrypt_tools(*, which=None) -> None:
    """Refuse a `--decrypt-check` whose TOOLS are absent — before the vault.

    🔴 `age` is needed by EVERY decrypt path, including `--from-dir` and an
    injected downloader, so its scope is wider than the `minio` check below.
    It was already checked — but deep inside `decrypt_check`, i.e. AFTER the
    vault round-trip. That is the same "right check, wrong moment" shape this
    module is being repaired for: on the documented workflow the operator has
    already typed a master password by then.

    🔴 THIS DOES NOT CLOSE THE CLASS. `kubectl`, `git`, a route to the tenant
    and a readable `--store` can all still fail late, and the advertised
    nix-shell provisions none of them — they resolve only because `nix-shell
    -p` is impure. `age` is fixed here because it is the one precondition
    testable with a single PATH lookup and no network.
    """
    which = shutil.which if which is None else which
    if which("age") is not None:
        return
    raise EscrowError(
        "AGE-MISSING",
        "`age` is not on PATH, so the escrowed key cannot be tested against "
        "anything. NOTHING HAS BEEN CHECKED and the vault was NOT contacted — "
        "this refusal is raised before any `bw` call. It is an ENVIRONMENT "
        "fault and says NOTHING about the escrow: do not read it as a verdict "
        "on the key or on the artifacts. age is declared in "
        "nix/pkgs/default.nix and in flake.nix `gateTools`.")


def preflight_decrypt_imports(*, modules: tuple[str, ...] | None = None,
                              find_spec=None) -> None:
    """Refuse a `--decrypt-check` the interpreter CANNOT complete — BEFORE the
    vault is touched.

    🔴 THIS RUNS BEFORE ANY `bw` CALL, AND THAT ORDERING IS THE WHOLE POINT.
    MEASURED 2026-08-25, twice, in the same shape:

      * the advertised shell omitted `minio`, so `--decrypt-check` unlocked the
        vault and THEN died `ModuleNotFoundError` — after the master password
        had been typed. The hint was fixed; the ORDERING was not, so the same
        thing happened again from a shell that simply lacked the package.
      * the failure surfaced as `scripts/mail-actions/_minio.py`'s own
        `SystemExit`, which names a DIFFERENT program (`extract.py
        archive-invoices`) and a DIFFERENT package set (`psycopg2`,
        `requests`). Accurate for its original caller, actively misleading
        here, and it never says WHICH interpreter came up short.

    `importlib.util.find_spec` is used rather than a real import: it answers
    "can this be imported" without executing `_minio.py`, whose import guard is
    the `SystemExit` above — so probing by import would trade one confusing
    message for another.

    Only the modules the DECRYPT path needs are checked, and only when the run
    will actually build the real downloader; a caller that injects a fake needs
    none of them and must not be refused.
    """
    modules = DECRYPT_PYTHON_MODULES if modules is None else modules
    find_spec = importlib.util.find_spec if find_spec is None else find_spec
    missing = []
    for mod in modules:
        try:
            if find_spec(mod) is None:
                missing.append(mod)
        except (ImportError, ValueError):
            # A parent package that is itself absent raises rather than
            # returning None. Same conclusion: not importable here.
            missing.append(mod)
    if not missing:
        return
    raise EscrowError(
        "DECRYPT-DEPS-MISSING",
        f"--decrypt-check needs {', '.join(missing)}, which "
        f"{sys.executable} cannot import. NOTHING HAS BEEN CHECKED and the "
        f"vault was NOT contacted — this refusal is raised before any `bw` "
        f"call precisely so a master password is not spent on a run that "
        f"cannot finish. Re-run under a shell that provides it: "
        f"{NIX_SHELL_HINT}. (A shell WITHOUT the "
        f"`python3.withPackages(...)` argument resolves `python3` from the "
        f"ambient profile, which does not carry these packages.)")


def _undo_advice(identity_source: str) -> str:
    """How to re-run against the default, phrased for THIS kind of source.

    🔴 `env -u` is only meaningful for an ENVIRONMENT VARIABLE. The sources are
    a closed set of shapes, and one combination is self-contradictory — the
    built-in default paired with a path that is not the default — so it gets
    generic advice rather than a sentence telling the operator to unset
    something called "the built-in default".
    """
    if identity_source.startswith("$"):
        return (f"Re-run with `env -u {identity_source.lstrip('$')} …` to "
                f"compare against the default first.")
    return (f"Re-run against {B.DEFAULT_IDENTITY} to compare against the "
            f"default first.")


def provenance_clauses(identity: Path, identity_source: str | None
                       ) -> tuple[str, str]:
    """`(chose, redirect)` — what to say about WHICH FILE was compared.

    🔴 THE TRIGGER IS THE FILE, NEVER THE MECHANISM. Both of the first
    revision's branches were wrong, and both printed a confident FALSE
    sentence on an ordinary invocation:

      * `--identity <the default path>` — the documented recommended command —
        was reported as "NOT by the default", and warned that "an unrelated
        shell can set this" about a COMMAND-LINE FLAG.
      * `SOPS_AGE_KEY_FILE=<the default path>` is what the deployed backup unit
        itself sets, so the warning fired on the subsystem's own normal
        configuration — a permanently-red warning, which trains a reader to
        skip the one sentence that matters when it is finally true.

    So: `redirect` is emitted only when the resolved file genuinely differs
    from `DEFAULT_IDENTITY`, and the source name is carried as INFORMATION.
    A source of `None` means no caller stated one — nothing is claimed about
    provenance rather than a default being invented for them.
    """
    if identity_source is None:
        return "", ""

    if B.same_identity_file(identity, B.DEFAULT_IDENTITY):
        # Named by anything at all — env var, flag or the built-in default —
        # it is still the default FILE, so there is nothing to warn about.
        return f" The on-disk path is the default identity, named by {identity_source}.", ""

    chose = (f" The on-disk path is NOT the default identity; it was chosen by "
             f"{identity_source}.")

    if identity_source == IDENTITY_SOURCE_FLAG:
        # A flag is a deliberate act by the person reading this message. Say
        # what was compared, without implying something redirected them.
        redirect = (
            f" 🔴 READ THIS BEFORE RE-ESCROWING: you pointed --identity at a file "
            f"that is NOT the identity this subsystem encrypts to, so this "
            f"mismatch says nothing about whether the escrow is intact. Every age "
            f"identity file is the same size, so equal byte counts on both sides "
            f"is what comparing two DIFFERENT keys looks like, not evidence they "
            f"are the same key. Re-run against {B.DEFAULT_IDENTITY} before "
            f"concluding anything, and do NOT re-escrow from the file you just "
            f"named.")
    elif not identity_source.startswith("$"):
        # 🔴 NEITHER a flag NOR an environment variable. Reachable only through
        # `run()`'s keyword API (the CLI pairs the default sentinel exclusively
        # with DEFAULT_IDENTITY), but it RENDERS, and the first version said
        # "the built-in default redirected the on-disk path away from …" — the
        # same self-contradiction `_undo_advice` exists to prevent, one
        # sentence earlier. Neutral wording that is true of any source.
        redirect = (
            f" 🔴 READ THIS BEFORE RE-ESCROWING: the on-disk path is not "
            f"{B.DEFAULT_IDENTITY}, so a mismatch here is at least as likely to "
            f"mean you compared the WRONG FILE as it is to mean the escrow is "
            f"damaged — and the two remedies are opposites. Every age identity "
            f"file is the same size, so equal byte counts on both sides is what "
            f"comparing two DIFFERENT keys looks like, not evidence they are the "
            f"same key. {_undo_advice(identity_source)}")
    else:
        redirect = (
            f" 🔴 READ THIS BEFORE RE-ESCROWING: {identity_source} redirected the "
            f"on-disk path away from {B.DEFAULT_IDENTITY}, so a mismatch here is "
            f"at least as likely to mean you compared the WRONG FILE as it is to "
            f"mean the escrow is damaged — and the two remedies are opposites. "
            f"Every age identity file is the same size, so equal byte counts on "
            f"both sides is what comparing two DIFFERENT keys looks like, not "
            f"evidence they are the same key. Re-escrowing now would overwrite a "
            f"possibly-good escrow with whatever {identity_source} happens to "
            f"point at. {_undo_advice(identity_source)}")
    return chose, redirect


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #
def run(*, bw: BitwardenCLI, identity: Path, item_name: str,
        identity_source: str | None = None,
        expect_server: str | None = None, decrypt: bool = False,
        bucket: str = DEFAULT_BUCKET, prefix: str | None = None,
        store: Path = DEFAULT_STORE, scope_filter: str | None = None,
        from_dir: Path | None = None, work_dir: Path | None = None,
        now: datetime | None = None,
        downloader_factory=None) -> EscrowVerdict:
    """Byte check, then optionally the decrypt check. Raises `EscrowError`."""
    now = now or datetime.now(timezone.utc)

    # 🔴 THE LOCAL SIDE FIRST, before any network call. It needs no vault, and
    # an absent or empty on-disk identity makes the comparison meaningless — so
    # the run should end there rather than after unlocking, listing and reading
    # a note it then has nothing to compare against.
    # 🔴 FIRST, BEFORE EVEN THE LOCAL IDENTITY READ. Two orderings are pinned
    # here as decisions, both by tests:
    #   * ahead of `read_identity`, because IDENTITY-MISSING's remedy (point
    #     ASIB_AGE_IDENTITY / SOPS_AGE_KEY_FILE somewhere real) is DISJOINT
    #     from the shell fix, so reporting it first buys the operator a second
    #     trip — the exact thing this preflight exists to prevent;
    #   * ahead of `require_available`, because DECRYPT-DEPS-MISSING's remedy
    #     is one nix-shell that provides `bw` too.
    # Tools are checked before modules: `age` is needed by every decrypt path,
    # `minio` only by the one that reaches the real bucket.
    if decrypt:
        preflight_decrypt_tools()
        if downloader_factory is None and from_dir is None:
            preflight_decrypt_imports()

    disk = read_identity(identity)

    bw.require_available()
    status = check_vault_state(bw)
    server, pinned, session_reason = check_server(bw, status, expect_server)
    item = find_escrow_item(bw, item_name)
    escrow = read_note(item, item_name)

    chose, redirect = provenance_clauses(identity, identity_source)

    verdict_class = classify(escrow, disk)
    if verdict_class == CLASS_TRAILING_NEWLINE:
        raise EscrowError(
            "BYTES-DIFFER-TRAILING-NEWLINE",
            f"the escrowed note and {identity} differ ONLY in trailing "
            f"newlines: {len(escrow)} escrowed bytes vs {len(disk)} on disk. "
            f"(The differing content is NOT printed — it is key material.) This "
            f"is what a copy through a web vault that trims looks like. An age "
            f"identity still decrypts without its final newline, so the escrow "
            f"is very likely USABLE — but it is no longer a byte-for-byte copy, "
            f"and 'very likely' is not what a disaster-recovery artifact gets to "
            f"be.{chose} Re-escrow the file. 🔴 --decrypt-check CANNOT confirm this: "
            f"this refusal is raised BEFORE the decrypt step runs, so the flag "
            f"produces the identical message and tests nothing. To check the "
            f"trimmed bytes by hand, write them to a 0600 file yourself and pass "
            f"it as --identity to `restore-verify.py`.")
    if verdict_class == CLASS_MATERIAL:
        raise EscrowError(
            "BYTES-DIFFER-MATERIALLY",
            f"the escrowed note and {identity} DIFFER: {len(escrow)} escrowed "
            f"bytes vs {len(disk)} on disk, and they are not equal after "
            f"trailing newlines are removed. (The differing content is NOT "
            f"printed — it is key material; compare them by hand if you must.) "
            # 🔴 ORDER IS LOAD-BEARING: the refusal comes BEFORE the remedy.
            # The first revision appended it after "Re-escrow from the on-disk
            # identity…", so the operator read the dangerous instruction first
            # and the reason not to follow it second.
            f"One of the two copies is not the key this subsystem encrypts to."
            f"{chose}{redirect} Re-escrow from the on-disk identity only after "
            f"confirming the on-disk one is the one the bucket's artifacts open "
            f"with — `restore-verify.py` answers that (pass it the SAME "
            f"--identity you used here, or it will resolve its own).")

    verdict = EscrowVerdict(
        item_name=item_name, server=server, server_pinned=pinned,
        server_session_reason=session_reason,
        escrow_bytes=len(escrow), disk_bytes=len(disk),
        classification=verdict_class, identity=identity,
        identity_source=identity_source,
    )
    if not decrypt:
        return verdict

    if work_dir is None:
        raise ValueError("decrypt=True requires work_dir; main() always passes one")
    scope, key, commits, refs = decrypt_check(
        escrow_bytes=escrow, work_dir=work_dir, bucket=bucket,
        prefix=prefix if prefix is not None else B.host_label(),
        store=store, scope_filter=scope_filter, from_dir=from_dir, now=now,
        downloader_factory=downloader_factory)
    verdict.decrypt_checked = True
    verdict.decrypt_scope = scope
    verdict.decrypt_key = key
    verdict.decrypt_commits = commits
    verdict.decrypt_refs = refs
    return verdict


def print_plan(*, identity: Path, item_name: str, expect_server: str | None,
               decrypt: bool, bucket: str, prefix: str, store: Path,
               scope_filter: str | None, from_dir: Path | None,
               identity_source: str | None = None) -> None:
    """Pure text. Runs no `bw`, touches no network, reads no key material."""
    present = identity.is_file()
    size = identity.stat().st_size if present else 0
    print(f"identity:  {identity} ({size} bytes)" if present
          else f"identity:  {identity} (MISSING)")
    # 🔴 The SIZE above cannot discriminate: every age identity file is the same
    # size, so a different key looks identical here to the right one. Whether
    # the file IS the default is the only thing on this line that separates
    # them — and it is asked of the FILE, never of what named it, so that
    # `--identity <the default>` and the deployed unit's own
    # `SOPS_AGE_KEY_FILE=<the default>` are both correctly silent.
    if identity_source is not None:
        note = ("" if B.same_identity_file(identity, B.DEFAULT_IDENTITY)
                else NON_DEFAULT_NOTE)
        print(f"chosen by: {identity_source}{note}")
    print(f"item:      {item_name!r} (exact name match; `bw list items "
          f"--search` is FUZZY, so its results are candidates)")
    print(f"type:      must be {ITEM_TYPE_SECURE_NOTE} (Secure Note) — checked "
          f"as STATE, not by the name alone")
    print("server:    read from `bw config server` AT RUN TIME and cross-checked "
          "against the session's own serverUrl.")
    print("           NEVER hardcoded here — this repo is public.")
    print(f"           pin: {'--expect-server / ASIB_ESCROW_SERVER is SET' if expect_server else 'NOT PINNED'}")
    print("vault:     `bw status` is the authority. locked / unauthenticated / "
          "anything unrecognised each")
    print("           end the run with their own exit code. The master password "
          "is never prompted for:")
    print("           every call runs with stdin on /dev/null and "
          "--nointeraction.")
    print("compare:   the note body, UTF-8 encoded, against the identity file, "
          "BYTE FOR BYTE.")
    print(f"           three answers: {CLASS_IDENTICAL} / "
          f"{CLASS_TRAILING_NEWLINE} / {CLASS_MATERIAL}.")
    print("           A mismatch reports byte COUNTS and the classification "
          "only — never the content.")
    if decrypt:
        src = str(from_dir) if from_dir is not None else bucket
        # The trailing slash is not cosmetic: an S3 prefix is a BYTE prefix, so
        # `workbench` would also list `workbench-laptop/…`. The plan prints the
        # prefix the run will actually use, normalised by the same function.
        shown = prefix.rstrip("/") + "/"
        print(f"decrypt:   ON — the ESCROWED bytes (not {identity}) are written "
              f"0600 into a 0700 dir")
        print(f"           and used to decrypt the newest artifact under "
              f"{src}/{shown}")
        print(f"           scope: {scope_filter or '(the scope with the newest artifact)'}")
        print(f"           store: {store} (cross-check target, via "
              f"restore-verify.py)")
        print("           the throwaway identity is overwritten and unlinked in "
              "a finally that covers")
        print("           success, every refusal and every unexpected exception.")
    else:
        print("decrypt:   OFF — byte equality proves the two copies AGREE, not "
              "that either OPENS anything.")
        print("           Pass --decrypt-check for that claim.")
    print(f"exit codes: {', '.join(f'{t}={c}' for t, c in sorted(EXIT_CODES.items(), key=lambda kv: kv[1]))}")


# 🔴 SIGNALS THAT MUST NOT SKIP THE SHRED. Python's DEFAULT SIGTERM handling
# terminates the process WITHOUT unwinding, so every `finally` in this file is
# bypassed. MEASURED: SIGTERM during `--decrypt-check` left
# `escrowed-identity.key` on disk at full size, byte-identical to the operator's
# real age identity, and `TemporaryDirectory` did not clean up either.
#
# That is not a theoretical window. SECRETS.md proposes running this from a
# systemd timer, and systemd's stop/timeout path IS SIGTERM — so the documented
# deployment is the one that leaks.
#
# Turning the signal into `SystemExit` makes the interpreter unwind normally, so
# the `finally` that shreds the throwaway identity runs. The exit status keeps
# the shell convention (128 + signal number) rather than colliding with a
# classified escrow verdict.
_FATAL_SIGNALS = ("SIGTERM", "SIGINT", "SIGHUP")


def install_signal_handlers() -> list[str]:
    """Make fatal signals unwind instead of killing. Returns the names installed.

    Returns the list so a caller — and the test suite — can assert WHICH signals
    were covered rather than that the function was called. A signal missing from
    `_FATAL_SIGNALS` is a path where the key survives.
    """
    installed: list[str] = []

    def _raise(signum, _frame):
        raise SystemExit(128 + signum)

    for name in _FATAL_SIGNALS:
        sig = getattr(signal, name, None)
        if sig is None:                      # not on this platform
            continue
        try:
            signal.signal(sig, _raise)
        except (OSError, ValueError):        # not the main thread, or blocked
            continue
        installed.append(name)
    return installed


def main(argv: list[str] | None = None) -> int:
    # BEFORE any work: the window this closes opens the moment a throwaway
    # identity exists, and argument parsing can itself be interrupted.
    install_signal_handlers()
    ap = argparse.ArgumentParser(prog=PROG, description=__doc__)
    ap.add_argument("--identity", type=Path, default=None,
                    help=f"age identity to compare against (default: "
                         f"{DEFAULT_IDENTITY}, or ASIB_AGE_IDENTITY / "
                         f"SOPS_AGE_KEY_FILE)")
    ap.add_argument("--item-name",
                    default=os.environ.get("ASIB_ESCROW_ITEM", DEFAULT_ITEM_NAME),
                    help="the vault item's EXACT name")
    ap.add_argument("--expect-server",
                    default=os.environ.get("ASIB_ESCROW_SERVER") or None,
                    help="fail unless `bw config server` equals this (no default: "
                         "the endpoint is never hardcoded, this repo is public)")
    ap.add_argument("--bw", default="bw", help="the bitwarden CLI to run")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help=f"per-`bw`-call ceiling in seconds (default "
                         f"{DEFAULT_TIMEOUT})")
    ap.add_argument("--decrypt-check", dest="decrypt", action="store_true",
                    help="ALSO decrypt a real artifact with the ESCROWED bytes")
    ap.add_argument("--bucket", default=os.environ.get("ASIB_BUCKET", DEFAULT_BUCKET))
    ap.add_argument("--host", default=None,
                    help="--decrypt-check: use this host label's artifacts")
    ap.add_argument("--prefix", default=None,
                    help="--decrypt-check: object key prefix; overrides --host")
    ap.add_argument("--scope", default=None,
                    help="--decrypt-check: use this scope (default: the scope "
                         "with the newest artifact)")
    ap.add_argument("--store", type=Path, default=DEFAULT_STORE,
                    help="--decrypt-check: the live store to cross-check against")
    ap.add_argument("--from-dir", type=Path, default=None,
                    help="--decrypt-check: read artifacts from this directory "
                         "instead of MinIO")
    ap.add_argument("--work-dir", type=Path, default=None,
                    help="where the throwaway identity is written, created 0700 "
                         "(default: a private temp dir). It is shredded and "
                         "removed either way.")
    ap.add_argument("--print-plan", action="store_true",
                    help="print what would happen; run no `bw`, read no key")
    args = ap.parse_args(argv)

    # 🔴 RESOLVE THE PATH AND WHAT CHOSE IT TOGETHER. An explicit --identity is
    # its own source: it is the one choice the operator definitely made on
    # purpose, so it must not be reported as an environment redirect.
    if args.identity is not None:
        identity, identity_source = args.identity, IDENTITY_SOURCE_FLAG
    else:
        identity, identity_source = B.resolve_identity_with_source()
    prefix = (args.prefix if args.prefix is not None
              else args.host if args.host is not None
              else B.host_label())

    if args.print_plan:
        print_plan(identity=identity, identity_source=identity_source,
                   item_name=args.item_name,
                   expect_server=args.expect_server, decrypt=args.decrypt,
                   bucket=str(args.from_dir) if args.from_dir else args.bucket,
                   prefix=prefix, store=args.store, scope_filter=args.scope,
                   from_dir=args.from_dir)
        return EXIT_OK

    bw = BitwardenCLI(bw=args.bw, timeout=args.timeout)

    # `source`, not `bucket`: with `--from-dir` it is a local directory, and a
    # NO-ARTIFACT message naming a bucket nobody queried would send the reader
    # to look in the wrong place. Same convention as restore-verify.py.
    source = str(args.from_dir) if args.from_dir is not None else args.bucket

    def _go(work: Path | None) -> EscrowVerdict:
        return run(bw=bw, identity=identity, item_name=args.item_name,
                   identity_source=identity_source,
                   expect_server=args.expect_server, decrypt=args.decrypt,
                   bucket=source, prefix=prefix, store=args.store,
                   scope_filter=args.scope, from_dir=args.from_dir,
                   work_dir=work)

    try:
        if not args.decrypt:
            verdict = _go(None)
        elif args.work_dir is not None:
            # 🔴 PREFLIGHT BEFORE THE DIRECTORY IS TOUCHED. `_private_dir`
            # creates the whole parent chain and chmods an EXISTING directory
            # to 0700; doing that and then refusing left a filesystem change
            # behind a message that says nothing happened. The refusal must be
            # the first thing that acts, not merely the first thing reported.
            if args.decrypt:
                preflight_decrypt_tools()
                if args.from_dir is None:
                    preflight_decrypt_imports()
            verdict = _go(B._private_dir(args.work_dir))
        else:
            with tempfile.TemporaryDirectory(prefix="asi-escrow-verify.") as td:
                verdict = _go(B._private_dir(Path(td)))
        print(verdict.line())
        return EXIT_OK
    except EscrowError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return exc.exit_code
    except B.BackupError as exc:
        # A refusal from a reused helper (`_private_dir`, `_git_scratch`'s store
        # guard). It has no token of its own; it is still a failure.
        print(f"{PROG}: UNCLASSIFIED: {exc}", file=sys.stderr)
        return EXIT_UNEXPECTED
    except Exception as exc:  # noqa: BLE001 — the failure signal, not a swallow
        traceback.print_exc()
        print(f"{PROG}: FAILED with an unexpected {type(exc).__name__}: {exc}. "
              f"NOTHING about the escrow was verified on this run — treat this "
              f"as no verification and read the traceback above.", file=sys.stderr)
        return EXIT_UNEXPECTED


if __name__ == "__main__":
    raise SystemExit(main())
