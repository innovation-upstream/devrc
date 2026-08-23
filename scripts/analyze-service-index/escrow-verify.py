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
NIX_SHELL_HINT = "nix-shell -p bitwarden-cli jq --run '<command>'"

_DIR_MODE = B._DIR_MODE      # 0700
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
    "AGE-MISSING": 29,          # precondition: the tool is not installed
    "ARTIFACT-UNREADABLE": 30,  # the pipeline failed BEFORE decrypt was reached
    "DECRYPT-FAILED": 25,       # age RAN and REFUSED: the escrowed key is wrong
    "ARTIFACT-EMPTY": 31,       # age SUCCEEDED on nothing: the key is FINE
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
    """

    def __init__(self, token: str, message: str):
        if token not in EXIT_CODES:
            raise KeyError(
                f"{token!r} is not in EXIT_CODES. Every refusal in this script "
                f"is an ENUMERATED cause with its own exit code; adding one "
                f"means adding it to the table in the same commit as the test "
                f"that pins it.")
        super().__init__(message)
        self.token = token
        self.exit_code = EXIT_CODES[token]

    def __str__(self) -> str:
        return f"{self.token}: {super().__str__()}"


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
            path.unlink(missing_ok=True)
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


# 🔴 WHAT THE PROBE BELOW OBSERVES, AND WHY IT IS SOUND — MEASURED, age v1.3.1.
# `age --decrypt --output PLAIN` was run across every outcome that matters:
#
#   wrong key, real payload      rc=1  PLAIN absent
#   wrong key, empty payload     rc=1  PLAIN absent
#   corrupt ciphertext           rc=1  PLAIN absent
#   right key, EMPTY payload     rc=0  PLAIN present, size 0
#   right key, real payload      rc=0  PLAIN present, size 12
#
# So the PRESENCE of the output file at the moment `decrypt()` raises separates
# the two failures that were previously conflated: age REFUSED (no file — the
# escrowed key is wrong) versus age SUCCEEDED on nothing (file present, empty —
# the key is FINE and the artifact is an encryption of nothing, which is exactly
# what restore-verify's own comment at its zero-byte branch says).
#
# ⚠ The inference "no file ⇒ age ran and refused" holds ONLY because
# `decrypt_check` refuses up front when `age` is not on PATH: without that
# precondition, restore-verify's own age-missing guard raises before touching
# PLAIN and would be indistinguishable from a refusal. The two halves are one
# argument; do not remove either alone.
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
    state = {"reached": False, "returned": False, "plain_present": None}
    real = RV.decrypt

    def probe(cipher, plain, identity):
        state["reached"] = True
        try:
            real(cipher, plain, identity)
        except BaseException:
            try:
                state["plain_present"] = Path(plain).exists()
            except OSError:
                state["plain_present"] = None
            raise
        state["returned"] = True

    RV.decrypt = real if real is None else probe
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

    def line(self) -> str:
        head = (f"{PROG}: escrow OK — the Secure Note matches {self.identity} "
                f"{self.classification} ({self.escrow_bytes} escrowed bytes vs "
                f"{self.disk_bytes} on disk)")
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
    raw_session = status.get("serverUrl")
    session_server = (raw_session or "").strip()
    if not session_server:
        why = ("`bw status` reported serverUrl="
               + ("null" if raw_session is None else "empty")
               + " — the CLI's configured server could NOT be cross-checked "
                 "against the authenticated session's")
        if expect is not None and _norm_url(expect) != _norm_url(configured):
            raise EscrowError(
                "SERVER-MISMATCH",
                "the configured server does not match the one pinned by "
                "--expect-server / ASIB_ESCROW_SERVER. URLs withheld — this "
                "repo is public; compare them by hand with `bw config server`.")
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
    if expect is not None:
        if _norm_url(expect) != _norm_url(configured):
            raise EscrowError(
                "SERVER-MISMATCH",
                "the configured server does not match the one pinned by "
                "--expect-server / ASIB_ESCROW_SERVER. Refusing to report an "
                "escrow found on a server that is not the one you named — URLs "
                "withheld, compare them by hand with `bw config server`.")
        return configured, True, None
    return configured, False, None


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
    if "notes" not in item:
        raise EscrowError(
            "NOTE-MISSING",
            f"the vault item {item_name!r} exists but its payload carries NO "
            f"`notes` FIELD AT ALL — not an empty one, an absent one. That is a "
            f"`bw` output-schema surprise, not evidence the escrow was emptied, "
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
            f"could not open the artifact store to test the escrowed key "
            f"({type(exc).__name__}: {exc}). NOTHING about the escrow was "
            f"proven by this run beyond the byte comparison above.")

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

            try:
                with _decrypt_phase_probe(RV) as phase:
                    v = RV.verify_artifact(
                        downloader, key, scope=scope, stamp=stamp,
                        identity=identity, work_dir=restore_dir, store=store,
                        keep=False, now=now, live_scope=live,
                        no_live_reason=no_live_reason)
            except RV.RestoreVerifyError as exc:
                # 🔴 CLASSIFIED BY THE PHASE THAT WAS OBSERVED, NOT BY A
                # SUBSTRING. Each branch asserts only what `phase` actually
                # witnessed; nothing here says "DECRYPTED" on a path where
                # `decrypt()` was not reached, and nothing blames the escrow for
                # a fault the escrow cannot have caused.
                if not phase["reached"]:
                    raise EscrowError(
                        "ARTIFACT-UNREADABLE",
                        f"the pipeline failed BEFORE the escrowed key was ever "
                        f"used, on {key}: {exc} — an empty or unreadable object, "
                        f"not a fact about the escrow. NOTHING here decrypted, "
                        f"and nothing here is evidence for or against the "
                        f"escrowed key. Run `restore-verify.py` to diagnose the "
                        f"object.")
                if not phase["returned"]:
                    if phase["plain_present"]:
                        # age exited 0 and produced an EMPTY plaintext. Measured:
                        # a refusal leaves NO output file, so a file that exists
                        # means the key opened it. restore-verify's own comment
                        # at that branch says the same: "age reported success, so
                        # this is not damage — it is a valid encryption of
                        # nothing."
                        raise EscrowError(
                            "ARTIFACT-EMPTY",
                            f"the ESCROWED key OPENED {key} and the artifact "
                            f"contains NOTHING: {exc}. 🔴 THE ESCROW IS FINE — "
                            f"age reported success and produced an output file, "
                            f"which a wrong key cannot do. Do NOT re-escrow or "
                            f"rotate on the strength of this; the fault is a "
                            f"valid encryption of an empty payload. Run "
                            f"`restore-verify.py` to diagnose the artifact.")
                    raise EscrowError(
                        "DECRYPT-FAILED",
                        f"the ESCROWED bytes do NOT open {key}: {exc} — age ran "
                        f"and REFUSED it, leaving no plaintext at all. The "
                        f"escrow is present but it is not (or no longer) a "
                        f"working identity for these artifacts. Nothing here "
                        f"says the artifacts are damaged; the on-disk key was "
                        f"not used.")
                raise EscrowError(
                    "RESTORE-FAILED",
                    f"the ESCROWED bytes DECRYPTED {key} — `decrypt()` RETURNED, "
                    f"which is what makes that claim observable — and the "
                    f"restore then failed: {exc}. This is a fault in the "
                    f"ARTIFACT, not in the escrow. The escrowed key works; run "
                    f"`restore-verify.py` to diagnose the artifact.")
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
# the run
# --------------------------------------------------------------------------- #
def run(*, bw: BitwardenCLI, identity: Path, item_name: str,
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
    disk = read_identity(identity)

    bw.require_available()
    status = check_vault_state(bw)
    server, pinned, session_reason = check_server(bw, status, expect_server)
    item = find_escrow_item(bw, item_name)
    escrow = read_note(item, item_name)

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
            f"be. Re-escrow the file, or confirm with --decrypt-check.")
    if verdict_class == CLASS_MATERIAL:
        raise EscrowError(
            "BYTES-DIFFER-MATERIALLY",
            f"the escrowed note and {identity} DIFFER: {len(escrow)} escrowed "
            f"bytes vs {len(disk)} on disk, and they are not equal after "
            f"trailing newlines are removed. (The differing content is NOT "
            f"printed — it is key material; compare them by hand if you must.) "
            f"One of the two copies is not the key this subsystem encrypts to. "
            f"Re-escrow from the on-disk identity only after confirming the "
            f"on-disk one is the one the bucket's artifacts open with — "
            f"`restore-verify.py` answers that.")

    verdict = EscrowVerdict(
        item_name=item_name, server=server, server_pinned=pinned,
        server_session_reason=session_reason,
        escrow_bytes=len(escrow), disk_bytes=len(disk),
        classification=verdict_class, identity=identity,
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
               scope_filter: str | None, from_dir: Path | None) -> None:
    """Pure text. Runs no `bw`, touches no network, reads no key material."""
    present = identity.is_file()
    size = identity.stat().st_size if present else 0
    print(f"identity:  {identity} ({size} bytes)" if present
          else f"identity:  {identity} (MISSING)")
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

    identity = args.identity or B.resolve_identity()
    prefix = (args.prefix if args.prefix is not None
              else args.host if args.host is not None
              else B.host_label())

    if args.print_plan:
        print_plan(identity=identity, item_name=args.item_name,
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
                   expect_server=args.expect_server, decrypt=args.decrypt,
                   bucket=source, prefix=prefix, store=args.store,
                   scope_filter=args.scope, from_dir=args.from_dir,
                   work_dir=work)

    try:
        if not args.decrypt:
            verdict = _go(None)
        elif args.work_dir is not None:
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
