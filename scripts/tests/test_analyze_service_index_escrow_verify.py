"""Tests for scripts/analyze-service-index/escrow-verify.py — the ESCROW VERIFIER.

WHAT IS BEING PROTECTED
-----------------------
`backup.py` and `restore-verify.py` both decrypt with ONE file. Escrowing that
file into Vaultwarden removes the single point of failure only if the escrow is
still there, still correct and still working — three claims, and a verifier that
collapses them (or that reports success having checked nothing) puts the
subsystem back where it started while looking green.

So this suite is mostly about the DIFFERENCES between failures. A single
"escrow check failed" is what this repo has been bitten by repeatedly; every
refusal here is asserted by its own TOKEN and its own EXIT CODE, not by a
substring of prose, because a substring assertion cannot tell a true sentence
from a confident wrong one.

🔴 ALL FIXTURES ARE SYNTHETIC AND PAIRWISE DISTINCT. devrc is a public repo. No
real key material, no real item name, no real endpoint and no real host label
appears here. The two key-shaped fixtures differ in every line, are different
lengths (179 vs 180 bytes), and neither length equals any constant this suite
asserts — a fixture that can only produce the constant's own value cannot see a
mutant that hardcodes the literal.

🔴 NOTHING HERE SKIPS and NOTHING HERE SHELLS OUT TO A REAL `bw`. `git`, `age`
and `age-keygen` are hard requirements resolved at import, exactly as in the
backup and restore suites. `bw` is injected at a seam — both the RUNNER and the
PATH LOCATOR — so the "`bw` is not installed" branch is reachable on a host
where `bw` IS installed, and vice versa.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import signal
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
SCRIPT = SCRIPTS / "analyze-service-index" / "escrow-verify.py"
RESTORE_SCRIPT = SCRIPTS / "analyze-service-index" / "restore-verify.py"

sys.path.insert(0, str(SCRIPTS / "analyze-service-index"))
sys.path.insert(0, str(SCRIPTS))

import backup as B  # noqa: E402

# 🔴 ONE PLACE OWNS THE RUNTIME SHEBANG. See `_bw_stub` — and
# `scripts/tests/test_runtime_shebangs.py`, which fails the suite if this file
# writes one of its own.
from testlib import mockbin  # noqa: E402


def _load(name: str, path: Path):
    """Import a module whose FILE NAME contains a hyphen.

    `sys.modules[name] = mod` BEFORE `exec_module` is REQUIRED: `@dataclass`
    resolves its annotations by looking the defining module up in `sys.modules`,
    and without the registration every test here becomes a collection error.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


EV = _load("escrow_verify", SCRIPT)
RV = _load("restore_verify", RESTORE_SCRIPT)


def _require(tool: str, why: str) -> str:
    p = shutil.which(tool)
    if p is None:  # pragma: no cover - the flake check puts all three on PATH
        raise RuntimeError(
            f"{tool} is not on PATH. {why} It is declared in nix/pkgs/default.nix "
            f"and in flake.nix `gateTools`; add it there rather than skipping "
            f"these tests — a skipped escrow test reports recoverability it "
            f"never measured.")
    return p


GIT: str = _require("git", "The decrypt check restores a real git bundle.")
AGE: str = _require("age", "The artifacts are age ciphertext.")
AGE_KEYGEN: str = _require("age-keygen", "Identities here are generated per run.")


# --------------------------------------------------------------------------- #
# synthetic fixtures — pairwise distinct, none of them a default
# --------------------------------------------------------------------------- #
HOST = "synthetic-escrow-host"
PREFIX = HOST + "/"
ITEM = "synthetic escrow note — delta"
SERVER = "https://vault.invalid.example"

# 🔴 KEY-SHAPED, NOT A KEY. Both are 3 lines and neither is a valid age
# identity: nothing in the BYTE-CHECK half of this script ever calls age, and a
# fixture that looked like a working key would invite someone to reuse it where
# one is needed. The decrypt half generates real throwaway identities instead.
ESCROW_NOTE = (
    "# created: 2026-01-02T03:04:05Z\n"
    "# public key: age1synthetic00000000000000000000000000000000000000000000000\n"
    "AGE-SECRET-KEY-1SYNTHETIC0000000000000000000000000000000000000000000000\n"
)
ESCROW_NOTE_BYTES = 179          # pinned literal; asserted against the fixture below

OTHER_KEY = (
    "# created: 2019-11-12T13:14:15Z\n"
    "# public key: age1localdisk0000000000000000000000000000000000000000000000\n"
    "AGE-SECRET-KEY-1LOCALDISK000000000000000000000000000000000000000000000000\n"
)
OTHER_KEY_BYTES = 180            # pinned literal, and DIFFERENT from the one above

# Bitwarden's Secure Note type id. Pinned as a literal rather than read from the
# module under test: a test that imports the constant it checks proves only that
# the module agrees with itself.
SECURE_NOTE = 2

NOW = datetime(2026, 3, 9, 17, 42, 5, tzinfo=timezone.utc)


def test_the_key_shaped_fixtures_are_the_lengths_this_suite_PINS():
    """HARNESS SELF-CHECK. Every byte-count assertion below is a literal; if a
    later edit reflows a fixture, this goes red loudly instead of the pins
    silently tracking the new value."""
    assert len(ESCROW_NOTE.encode("utf-8")) == ESCROW_NOTE_BYTES
    assert len(OTHER_KEY.encode("utf-8")) == OTHER_KEY_BYTES
    assert ESCROW_NOTE_BYTES != OTHER_KEY_BYTES
    assert ESCROW_NOTE != OTHER_KEY
    # Pairwise distinct LINE BY LINE, so no assertion below can pass by two
    # fixtures happening to share a line.
    assert not (set(ESCROW_NOTE.splitlines()) & set(OTHER_KEY.splitlines()))


def _one_byte_different(text: str) -> str:
    """The same length, one byte changed in the middle.

    🔴 SAME LENGTH ON PURPOSE. A negative control built by appending would also
    be caught by a comparison that only looked at `len()`, and would then certify
    a byte check that never compares bytes.
    """
    i = len(text) // 2
    ch = "X" if text[i] != "X" else "Y"
    return text[:i] + ch + text[i + 1:]


# --------------------------------------------------------------------------- #
# the `bw` seam — a fake RUNNER and a fake LOCATOR
# --------------------------------------------------------------------------- #
class FakeBw:
    """Serves canned `bw` answers and RECORDS every argv it was given.

    🔴 An unknown command is an assertion failure, never a quiet empty answer. A
    fake that returned `{}` for a command it did not recognise would let a
    mis-dispatched call read as a clean result — the harness manufacturing the
    all-clear the script exists to refuse.
    """

    def __init__(self, *, status: dict | None = None, server: str = SERVER,
                 items: list | None = None, status_rc: int = 0,
                 server_rc: int = 0, items_rc: int = 0,
                 status_out: str | None = None, items_out: str | None = None,
                 raise_timeout_on: str | None = None):
        self.status_doc = {"serverUrl": SERVER, "status": "unlocked"} \
            if status is None else status
        self.server = server
        self.items = [] if items is None else items
        self.status_rc, self.server_rc, self.items_rc = status_rc, server_rc, items_rc
        self.status_out, self.items_out = status_out, items_out
        self.raise_timeout_on = raise_timeout_on
        self.calls: list[list[str]] = []
        self.timeouts: list[float] = []
        self.searches: list[str] = []

    def __call__(self, argv, *, timeout):
        self.calls.append(list(argv))
        self.timeouts.append(timeout)
        rest = [a for a in argv[1:] if a != "--nointeraction"]
        verb = " ".join(rest[:2])
        if self.raise_timeout_on is not None and rest[0] == self.raise_timeout_on:
            raise subprocess.TimeoutExpired(cmd=list(argv), timeout=timeout)
        if rest[0] == "status":
            out = (self.status_out if self.status_out is not None
                   else json.dumps(self.status_doc))
            return subprocess.CompletedProcess(argv, self.status_rc, out, "")
        if verb == "config server":
            return subprocess.CompletedProcess(argv, self.server_rc,
                                               self.server + "\n", "")
        if verb == "list items":
            self.searches.append(rest[rest.index("--search") + 1])
            out = (self.items_out if self.items_out is not None
                   else json.dumps(self.items))
            return subprocess.CompletedProcess(argv, self.items_rc, out, "")
        raise AssertionError(
            f"FakeBw was asked for an unmodelled command: {argv!r}. Returning a "
            f"benign answer here would make a mis-dispatched call look clean.")


def _cli(fake: FakeBw, *, locator=lambda name: "/nonexistent/bin/bw") -> EV.BitwardenCLI:
    return EV.BitwardenCLI(runner=fake, locator=locator, bw="bw", timeout=7.5)


def _item(name: str = ITEM, notes: str | None = ESCROW_NOTE,
          type_: int = SECURE_NOTE, id_: str = "synthetic-item-id-0001") -> dict:
    d = {"id": id_, "name": name, "type": type_, "favorite": False}
    if notes is not None:
        d["notes"] = notes
    return d


def _identity(tmp_path: Path, text: str = ESCROW_NOTE,
              name: str = "on-disk.key") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    p.chmod(0o600)
    return p


def _run(tmp_path: Path, fake: FakeBw, *, identity_text: str = ESCROW_NOTE,
         item_name: str = ITEM, **kw):
    return EV.run(bw=_cli(fake), identity=_identity(tmp_path, identity_text),
                  item_name=item_name, now=NOW, **kw)


# --------------------------------------------------------------------------- #
# 0. harness self-validation — validate the INSTRUMENT before reading its verdict
# --------------------------------------------------------------------------- #
def test_the_verifier_imported_and_carries_the_symbols_this_suite_reads():
    """POSITIVE CONTROL for the hyphenated-filename loader.

    A loader that silently produced an empty module would make every `getattr`
    below fail confusingly — or, worse, make a tolerant assertion pass against
    nothing."""
    assert SCRIPT.is_file(), f"{SCRIPT} missing — every test below is vacuous"
    for name in ("run", "classify", "decrypt_check", "read_identity",
                 "check_vault_state", "check_server", "find_escrow_item",
                 "read_note", "BitwardenCLI", "EscrowError", "EXIT_CODES",
                 "_shred", "_rv"):
        assert hasattr(EV, name), f"the loaded module has no {name!r}"


def test_the_fake_bw_actually_observes_the_calls_it_is_asked_about():
    """POSITIVE CONTROL for the instrument every test below reads.

    A fake wired to nothing records zero calls, and every "the script asked the
    vault" assertion would then be about a harness that never ran. Watch the
    counters move before trusting them."""
    f = FakeBw(items=[_item()])
    assert f.calls == [] and f.searches == []
    cli = _cli(f)
    assert cli.status()["status"] == "unlocked"
    assert cli.config_server() == SERVER
    assert [i["name"] for i in cli.search_items(ITEM)] == [ITEM]
    assert len(f.calls) == 3, f.calls
    assert f.searches == [ITEM]


def test_the_fake_bw_REFUSES_an_unmodelled_command():
    """NEGATIVE CONTROL for the fake itself: it must not invent clean answers."""
    f = FakeBw()
    with pytest.raises(AssertionError):
        f(["bw", "--nointeraction", "sync"], timeout=1.0)


# --------------------------------------------------------------------------- #
# 1. the failure vocabulary is an ENUMERATION, pinned as literals
# --------------------------------------------------------------------------- #
def test_the_exit_code_table_is_pinned_EXACTLY_both_ways():
    """🔴 THE WHOLE POINT OF THIS SCRIPT, pinned.

    Distinguishable failure modes are the deliverable, so the token->code map is
    asserted as an EXACT dict of literals — not a subset, not a length. Adding a
    cause means editing this line, which is a reviewed act; silently reusing an
    existing code (the conflation this replaces) cannot happen unnoticed.
    """
    assert EV.EXIT_CODES == {
        "BW-MISSING": 10,
        "BW-FAILED": 11,
        "VAULT-LOCKED": 12,
        "VAULT-UNAUTHENTICATED": 13,
        "VAULT-STATUS-UNKNOWN": 14,
        "SERVER-UNKNOWN": 15,
        "SERVER-MISMATCH": 16,
        "ITEM-NOT-FOUND": 17,
        "ITEM-AMBIGUOUS": 18,
        "ITEM-WRONG-TYPE": 19,
        "NOTE-EMPTY": 20,
        "BYTES-DIFFER-TRAILING-NEWLINE": 21,
        "BYTES-DIFFER-MATERIALLY": 22,
        "IDENTITY-MISSING": 23,
        "IDENTITY-EMPTY": 24,
        "DECRYPT-FAILED": 25,
        "RESTORE-FAILED": 26,
        "STORE-UNREACHABLE": 27,
        "NO-ARTIFACT": 28,
        "AGE-MISSING": 29,
        "ARTIFACT-UNREADABLE": 30,
        "ARTIFACT-EMPTY": 31,
        "NOTE-MISSING": 32,
        "ARTIFACT-CORRUPT": 33,
        # raised BEFORE any `bw` call — the interpreter cannot finish the run,
        # so no master password is spent on one that was never going to
        "DECRYPT-DEPS-MISSING": 34,
    }


def test_the_SIX_decrypt_outcomes_are_SIX_DISTINCT_codes():
    """🔴 THE HEADLINE SPLIT, pinned as a set of distinct integers.

    Two rounds of audit each found the previous split wrong, both times in the
    direction that makes someone act destructively. These six must never
    collapse back: `age` absent is an ENVIRONMENT fault; "failed before decrypt
    ran" is an OBJECT fault; "age wrote nothing" is a wrong key OR a damaged
    header and asserts neither; "age authenticated the header then failed the
    payload" is TAMPERING with a working key; "age exited zero on nothing" is an
    empty artifact with a working key; "decrypt returned, restore failed" is a
    bundle fault with a working key.
    """
    names = ["AGE-MISSING", "ARTIFACT-UNREADABLE", "DECRYPT-FAILED",
             "ARTIFACT-CORRUPT", "ARTIFACT-EMPTY", "RESTORE-FAILED"]
    codes = [EV.EXIT_CODES[n] for n in names]
    assert codes == [29, 30, 25, 33, 31, 26]
    assert len(set(codes)) == 6


def test_every_exit_code_is_DISTINCT_and_never_collides_with_success_or_crash():
    """A table with two names on one number is a conflated failure with extra
    steps, and 0/1 already mean success and unexpected-crash."""
    codes = list(EV.EXIT_CODES.values())
    assert len(set(codes)) == len(codes), "two tokens share an exit code"
    assert EV.EXIT_OK == 0 and EV.EXIT_UNEXPECTED == 1
    assert 0 not in codes and 1 not in codes
    assert min(codes) >= 10


def test_an_unknown_token_CANNOT_be_raised():
    """The code is DERIVED from the token, so a refusal cannot carry the wrong
    one — and a token with no table entry is a programming error, loudly."""
    with pytest.raises(KeyError):
        EV.EscrowError("NOT-A-REAL-TOKEN", "x")
    e = EV.EscrowError("VAULT-LOCKED", "x")
    assert e.exit_code == 12 and e.token == "VAULT-LOCKED"
    assert str(e) == "VAULT-LOCKED: x"
    assert e.detail is None


def test_the_rendered_message_carries_BOTH_the_verdict_and_the_upstream_detail():
    """🔴 `str(exc)` IS WHAT THE OPERATOR READS — the CLI prints it and nothing
    else. Pinning only `.verdict` left `__str__` untested: a sweep found that
    dropping the `[upstream: …]` half entirely SURVIVED the suite, silently
    discarding age's own diagnosis on every failure path.

    Both halves, whole, by equality — and the detail must NOT be swallowed."""
    e = EV.EscrowError("ARTIFACT-CORRUPT", "the verdict half", "the upstream half")
    assert e.verdict == "the verdict half"
    assert e.detail == "the upstream half"
    assert str(e) == "ARTIFACT-CORRUPT: the verdict half [upstream: the upstream half]"


# --------------------------------------------------------------------------- #
# 2. the comparison itself
# --------------------------------------------------------------------------- #
def test_classify_returns_the_three_PINNED_literals():
    """Pinned as literal strings: a verdict is a machine-readable claim, and a
    renamed classification is a broken consumer, not a cosmetic change."""
    assert EV.classify(b"abc", b"abc") == "IDENTICAL"
    assert EV.classify(b"abc\n", b"abc") == "DIFFERS-TRAILING-NEWLINE-ONLY"
    assert EV.classify(b"abc", b"abd") == "DIFFERS-MATERIALLY"
    assert (EV.CLASS_IDENTICAL, EV.CLASS_TRAILING_NEWLINE, EV.CLASS_MATERIAL) == (
        "IDENTICAL", "DIFFERS-TRAILING-NEWLINE-ONLY", "DIFFERS-MATERIALLY")


def test_classify_calls_a_CRLF_rewrite_MATERIAL_not_a_trailing_newline():
    """A CRLF round-trip changes EVERY line ending, not the last byte. Calling
    it "trailing newline only" would tell the operator the escrow is fine."""
    unix = b"one\ntwo\nthree\n"
    dos = b"one\r\ntwo\r\nthree\r\n"
    assert EV.classify(dos, unix) == "DIFFERS-MATERIALLY"


def test_classify_does_not_confuse_a_leading_newline_with_a_trailing_one():
    assert EV.classify(b"\nabc\n", b"abc\n") == "DIFFERS-MATERIALLY"


@pytest.mark.parametrize("suffix,name", [
    (b" ", "space"), (b"\t", "tab"), (b"\r", "carriage return"),
    (b"\n \n", "newline-space-newline"), (b"  \n", "two spaces then newline"),
])
def test_only_NEWLINES_count_as_a_trailing_newline_difference(suffix, name):
    """🔴 THE NARROWNESS IS THE CLAIM, so it gets measured.

    `classify`'s docstring calls the rule "narrow on purpose" — trailing `\\n`
    and nothing else. Widening it to a bare `rstrip()` would fold trailing
    SPACES and TABS into "differs by trailing newline only", i.e. into the
    verdict that means "probably still usable, one `printf` fixes it". A key
    with trailing whitespace is a different object and the operator would be
    told the wrong thing.

    Measured here, not asserted: an audit's independent sweep found
    `rstrip(b"\\n") -> rstrip()` SURVIVED the suite before these cases existed.
    """
    base = b"AGE-SECRET-KEY-1SYNTHETICFIXTUREVALUE\n"
    assert EV.classify(base + suffix, base) == "DIFFERS-MATERIALLY", name


def test_a_PURE_trailing_newline_difference_is_STILL_its_own_class():
    """The positive half of the pair above: narrowing must not narrow to
    nothing. Without this, `rstrip(b"\\n") -> (no strip at all)` would pass every
    case above while destroying the classification that matters."""
    base = b"AGE-SECRET-KEY-1SYNTHETICFIXTUREVALUE"
    assert EV.classify(base + b"\n", base) == "DIFFERS-TRAILING-NEWLINE-ONLY"
    assert EV.classify(base, base + b"\n\n") == "DIFFERS-TRAILING-NEWLINE-ONLY"


# --------------------------------------------------------------------------- #
# 3. 🔴 THE POSITIVE CONTROL — and it asserts bytes were actually COMPARED
# --------------------------------------------------------------------------- #
def test_an_identical_escrow_PASSES_and_the_comparison_saw_nonzero_bytes(tmp_path):
    """🔴 A CHECKER WIRED TO NOTHING ALSO REPORTS "no problems".

    So the pass is not the whole assertion: the verdict must carry the number of
    bytes it compared, that number must be NON-ZERO, and it must equal the
    fixture's own pinned length on BOTH sides.
    """
    f = FakeBw(items=[_item()])
    v = _run(tmp_path, f)
    assert v.classification == "IDENTICAL"
    assert v.escrow_bytes == ESCROW_NOTE_BYTES > 0
    assert v.disk_bytes == ESCROW_NOTE_BYTES > 0
    assert v.decrypt_checked is False
    # The vault really was consulted — three commands, in order.
    assert [c[2] for c in f.calls] == ["status", "config", "list"], f.calls
    assert f.searches == [ITEM]


def test_the_verdict_line_says_it_did_NOT_prove_the_key_works(tmp_path):
    """Byte equality proves the copies AGREE. A verdict that let "verified"
    stand for "and it opens the artifacts" would be the wider-than-the-code
    sentence this subsystem keeps being bitten by."""
    v = _run(tmp_path, FakeBw(items=[_item()]))
    line = v.line()
    assert "NOT DECRYPT-CHECKED" in line
    assert "--decrypt-check" in line
    assert "DECRYPT-CHECKED: " not in line
    assert str(ESCROW_NOTE_BYTES) in line


def test_the_search_is_EXACT_even_though_bw_search_is_FUZZY(tmp_path):
    """`bw list items --search` returns candidates. A near-miss must not be
    accepted, and the ambiguity check must not be fooled by one either."""
    f = FakeBw(items=[_item(name=ITEM + " (old)"), _item(name=ITEM)])
    v = _run(tmp_path, f)
    assert v.classification == "IDENTICAL"


# --------------------------------------------------------------------------- #
# 4. 🔴 THE NEGATIVE CONTROLS — one byte, and a newline
# --------------------------------------------------------------------------- #
def test_a_ONE_BYTE_difference_FAILS_with_the_MATERIAL_token(tmp_path):
    """🔴 SAME LENGTH, one byte changed. A comparison that only checked `len()`
    would pass this, and would then be certifying a check it never made."""
    mutated = _one_byte_different(ESCROW_NOTE)
    assert len(mutated) == len(ESCROW_NOTE) and mutated != ESCROW_NOTE
    f = FakeBw(items=[_item(notes=mutated)])
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, f)
    assert ei.value.token == "BYTES-DIFFER-MATERIALLY"
    assert ei.value.exit_code == 22


def test_a_WHOLLY_different_key_FAILS_materially_and_reports_BOTH_counts(tmp_path):
    f = FakeBw(items=[_item(notes=OTHER_KEY)])
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, f, identity_text=ESCROW_NOTE)
    assert ei.value.token == "BYTES-DIFFER-MATERIALLY"
    msg = str(ei.value)
    # Kind AND count: both measured numbers appear, and they are the two
    # DIFFERENT fixture lengths — a message that printed one of them twice, or
    # printed a constant, cannot satisfy this.
    assert str(OTHER_KEY_BYTES) in msg and str(ESCROW_NOTE_BYTES) in msg


def test_a_TRAILING_NEWLINE_difference_is_its_OWN_classification(tmp_path):
    """🔴 NOT a generic mismatch and NOT a pass.

    Vaultwarden may trim. An age identity still decrypts without its final
    newline, so this is the one difference whose remedy is one `printf` — and
    the operator cannot choose that remedy if the verifier calls it corruption.
    """
    trimmed = ESCROW_NOTE.rstrip("\n")
    assert len(trimmed) == ESCROW_NOTE_BYTES - 1
    f = FakeBw(items=[_item(notes=trimmed)])
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, f)
    assert ei.value.token == "BYTES-DIFFER-TRAILING-NEWLINE"
    assert ei.value.exit_code == 21
    assert ei.value.exit_code != EV.EXIT_CODES["BYTES-DIFFER-MATERIALLY"]
    assert ei.value.exit_code != EV.EXIT_OK
    # 🔴 IT MUST NOT OFFER A CHECK IT CANNOT PERFORM. This refusal is raised
    # BEFORE the `if not decrypt` return, so `--decrypt-check` produces the
    # identical message and can never confirm anything — the message used to
    # say "or confirm with --decrypt-check", which is advice to run the same
    # command again.
    assert "confirm with --decrypt-check" not in ei.value.verdict
    assert "--decrypt-check CANNOT confirm this" in ei.value.verdict


def test_the_trailing_newline_refusal_is_IDENTICAL_with_and_without_decrypt_check(
        tmp_path, escrow_world):
    """The measurement behind the sentence above: the flag changes nothing here,
    because the byte check refuses first. Both runs, same token, same verdict."""
    trimmed = ESCROW_NOTE.rstrip("\n")
    ident = _identity(tmp_path, ESCROW_NOTE, name="tn.key")
    seen = []
    for decrypt in (False, True):
        d = FakeDownloader(escrow_world["objects"])
        with pytest.raises(EV.EscrowError) as ei:
            EV.run(bw=_cli(FakeBw(items=[_item(notes=trimmed)])), identity=ident,
                   item_name=ITEM, decrypt=decrypt, prefix=PREFIX,
                   store=escrow_world["store"], work_dir=escrow_world["work"],
                   now=NOW, downloader_factory=lambda: d)
        seen.append((ei.value.token, ei.value.verdict))
        assert d.gets == [], "the artifact store was reached on a byte-mismatch"
    assert seen[0] == seen[1], "the flag changed the outcome after all"


def test_the_trailing_newline_case_is_NOT_reported_as_a_pass(tmp_path):
    """The failure that would matter most if this were only a classification:
    a trimmed note must still make the RUN fail, not merely be labelled."""
    f = FakeBw(items=[_item(notes=ESCROW_NOTE.rstrip("\n"))])
    with pytest.raises(EV.EscrowError):
        _run(tmp_path, f)


# --------------------------------------------------------------------------- #
# 5. 🔴 NO KEY MATERIAL IN ANY MESSAGE
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("note", [
    _one_byte_different(ESCROW_NOTE),
    OTHER_KEY,
    ESCROW_NOTE.rstrip("\n"),
])
def test_no_message_on_a_mismatch_path_contains_key_material(tmp_path, note):
    """🔴 A mismatch reports COUNTS AND A CLASSIFICATION. Never the content.

    Asserted against every substantial line of BOTH fixtures, so a message that
    quoted either side — or a diff of them — goes red.
    """
    f = FakeBw(items=[_item(notes=note)])
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, f)
    msg = str(ei.value)
    leaked = [ln for ln in set(ESCROW_NOTE.splitlines()) | set(OTHER_KEY.splitlines())
              | set(note.splitlines()) if len(ln) > 20 and ln in msg]
    assert leaked == [], f"key material in the message: {len(leaked)} line(s)"
    assert "AGE-SECRET-KEY" not in msg


# --------------------------------------------------------------------------- #
# 6. the item: missing / ambiguous / empty / wrong type — four findings
# --------------------------------------------------------------------------- #
def test_an_ABSENT_item_is_its_own_failure(tmp_path):
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, FakeBw(items=[]))
    assert ei.value.token == "ITEM-NOT-FOUND"
    assert ei.value.exit_code == 17


def test_a_near_miss_name_is_still_ITEM_NOT_FOUND(tmp_path):
    """The fuzzy search returned something; the exact match did not."""
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, FakeBw(items=[_item(name=ITEM + " (archived)")]))
    assert ei.value.token == "ITEM-NOT-FOUND"


def test_TWO_items_with_the_SAME_name_is_AMBIGUOUS_not_a_pick(tmp_path):
    """🔴 Choosing one would turn a coin flip into a verdict, and a stale
    duplicate holding a rotated-out key would verify green forever."""
    f = FakeBw(items=[_item(id_="a"), _item(id_="b")])
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, f)
    assert ei.value.token == "ITEM-AMBIGUOUS"
    assert ei.value.exit_code == 18
    assert "2" in str(ei.value), "the message must say HOW MANY it found"


def test_ambiguity_is_detected_even_when_BOTH_copies_would_have_PASSED(tmp_path):
    """🔴 THE REACHABILITY CASE. With two identical, correct notes a verifier
    that picked the first would exit 0 and nobody would ever see the duplicate.
    The ambiguity guard must fire on the happy content, not only on bad."""
    f = FakeBw(items=[_item(id_="a", notes=ESCROW_NOTE),
                      _item(id_="b", notes=ESCROW_NOTE)])
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, f)
    assert ei.value.token == "ITEM-AMBIGUOUS"


def test_an_EMPTY_note_body_is_its_own_failure(tmp_path):
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, FakeBw(items=[_item(notes="")]))
    assert ei.value.token == "NOTE-EMPTY"
    assert ei.value.exit_code == 20


def test_a_WHITESPACE_ONLY_note_is_EMPTY_not_a_mismatch(tmp_path):
    """It is the same finding — the escrow is gone — and reporting it as a byte
    mismatch would send the operator looking for a corrupted copy."""
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, FakeBw(items=[_item(notes="   \n\t\n")]))
    assert ei.value.token == "NOTE-EMPTY"


def test_an_ABSENT_notes_FIELD_is_NOT_the_same_finding_as_an_EMPTY_one(tmp_path):
    """🔴 OPPOSITE ACTIONS. `NOTE-EMPTY` says the escrow was emptied and sends
    the operator to re-escrow — which, if `bw` merely OMITTED the field for an
    INTACT note, overwrites a good copy on the strength of a parsing accident.

    `_item(notes=None)` builds a payload with no `notes` key at all, which is
    the shape being distinguished."""
    item = _item(notes=None)
    assert "notes" not in item, "the fixture must omit the field, not empty it"
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, FakeBw(items=[item]))
    assert ei.value.token == "NOTE-MISSING"
    assert ei.value.exit_code == 32
    assert ei.value.exit_code != EV.EXIT_CODES["NOTE-EMPTY"]
    assert "Do NOT re-escrow" in str(ei.value)


def test_a_JSON_NULL_notes_is_ALSO_NOTE_MISSING(tmp_path):
    """🔴 `{"notes": null}` IS THE SHAPE JSON ACTUALLY PRODUCES for "no notes",
    and it is the very shape this module already handles for `serverUrl`.

    Splitting on `"notes" not in item` alone let a null fall through to
    `NOTE-EMPTY` — straight back to the "re-escrow over a good copy" advice
    `NOTE-MISSING` was created to prevent."""
    item = {"id": "n", "name": ITEM, "type": SECURE_NOTE, "notes": None}
    assert "notes" in item, "the fixture must PRESENT the field as null"
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, FakeBw(items=[item]))
    assert ei.value.token == "NOTE-MISSING"
    assert ei.value.exit_code == 32


def test_a_notes_field_present_but_NOT_A_STRING_is_NOTE_EMPTY(tmp_path):
    """The field is there, so this is not the absent case; it carries nothing
    usable, so it is the empty one."""
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, FakeBw(items=[{"id": "x", "name": ITEM,
                                      "type": SECURE_NOTE, "notes": 17}]))
    assert ei.value.token == "NOTE-EMPTY"


def test_the_right_NAME_on_the_wrong_TYPE_is_refused(tmp_path):
    """🔴 A GUARD ON STATE, NOT ON A WORD ANOTHER ITEM CAN SPELL. Any item type
    can be given any name; only a Secure Note is the escrow."""
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, FakeBw(items=[_item(type_=1)]))
    assert ei.value.token == "ITEM-WRONG-TYPE"
    assert ei.value.exit_code == 19


# --------------------------------------------------------------------------- #
# 7. the vault's state — distinct, actionable, and it CANNOT hang
# --------------------------------------------------------------------------- #
def test_a_LOCKED_vault_fails_distinctly_and_names_the_UNLOCK_command(tmp_path):
    f = FakeBw(status={"serverUrl": SERVER, "status": "locked"})
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, f)
    assert ei.value.token == "VAULT-LOCKED"
    assert ei.value.exit_code == 12
    msg = str(ei.value)
    # DISCRIMINATING, not a substring presence check: the locked remedy is
    # `bw unlock`, and naming `bw login` here would send the operator to the
    # OTHER failure's remedy — which is exactly the conflation being tested.
    assert 'bw unlock --raw' in msg
    assert "bw login" not in msg
    # It must not have gone on to ask the vault anything.
    assert [c[2] for c in f.calls] == ["status"], f.calls


def test_an_UNAUTHENTICATED_vault_is_a_DIFFERENT_failure_with_a_DIFFERENT_remedy(tmp_path):
    f = FakeBw(status={"serverUrl": SERVER, "status": "unauthenticated"})
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, f)
    assert ei.value.token == "VAULT-UNAUTHENTICATED"
    assert ei.value.exit_code == 13
    assert ei.value.exit_code != EV.EXIT_CODES["VAULT-LOCKED"]
    assert "bw login" in str(ei.value)
    assert [c[2] for c in f.calls] == ["status"], f.calls


def test_an_UNRECOGNISED_status_is_UNKNOWN_and_never_treated_as_unlocked(tmp_path):
    """🔴 An empty/odd result cannot distinguish its causes. Treating it as
    `unlocked` is how a run reports a clean escrow it never read."""
    for weird in ("", "Unlocked", "UNLOCKED", "pending", None):
        f = FakeBw(status={"serverUrl": SERVER, "status": weird})
        with pytest.raises(EV.EscrowError) as ei:
            _run(tmp_path, f)
        assert ei.value.token == "VAULT-STATUS-UNKNOWN", weird
        assert ei.value.exit_code == 14


def test_status_json_that_is_not_an_object_is_UNKNOWN(tmp_path):
    f = FakeBw(status_out='["unlocked"]')
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, f)
    assert ei.value.token == "VAULT-STATUS-UNKNOWN"


def test_status_that_is_not_JSON_at_all_is_BW_FAILED_not_a_crash(tmp_path):
    f = FakeBw(status_out="You are not logged in.\n")
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, f)
    assert ei.value.token == "BW-FAILED"
    assert ei.value.exit_code == 11


def test_a_nonzero_bw_exit_is_BW_FAILED_and_its_OUTPUT_IS_NOT_QUOTED(tmp_path):
    """🔴 `bw list items` output is key material and `bw status` carries the
    server and the account email. Neither may reach a message."""
    canary = "AGE-SECRET-KEY-1CANARYMUSTNOTAPPEARINANYMESSAGE"
    f = FakeBw(items_rc=3, items_out=canary)
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, f)
    assert ei.value.token == "BW-FAILED"
    assert canary not in str(ei.value)


@pytest.mark.parametrize("kw", [{"status_rc": 4}, {"server_rc": 5}, {"items_rc": 6}])
def test_a_nonzero_bw_exit_FAILS_even_when_the_OUTPUT_is_perfectly_valid(tmp_path, kw):
    """🔴 REACHABILITY, MEASURED — this test exists because its absence let a
    mutant live.

    The test above feeds UNPARSEABLE output alongside the non-zero exit, so the
    JSON guard speaks first: deleting the exit-status check entirely kept that
    test green, and a mutation sweep scored the mutant SURVIVED. Here every
    stream is well-formed and the ONLY thing wrong is the status, so no earlier
    check can answer and the rc guard has to be the one that fires — on all
    three commands, including `config server`, whose output is not JSON at all
    and therefore has no second guard behind it.
    """
    f = FakeBw(items=[_item()], **kw)
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, f)
    assert ei.value.token == "BW-FAILED"
    assert ei.value.exit_code == 11


def test_a_TIMEOUT_is_BW_FAILED_and_says_it_was_not_a_password_prompt(tmp_path):
    """The non-hanging guarantee's failure mode, named. A run that stalls must
    end, and must not leave the reader guessing whether it was waiting on them."""
    f = FakeBw(raise_timeout_on="status")
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, f)
    assert ei.value.token == "BW-FAILED"
    assert "7.5" in str(ei.value), "the ceiling that was hit must be named"


def test_EVERY_bw_call_passes_nointeraction_in_the_SAME_position(tmp_path):
    """A flag is a request; the count is the measurement. Every call, not most."""
    f = FakeBw(items=[_item()])
    _run(tmp_path, f)
    assert len(f.calls) == 3
    assert [c[1] for c in f.calls] == ["--nointeraction"] * 3
    assert f.timeouts == [7.5, 7.5, 7.5]


def test_the_REAL_runner_gives_bw_no_stdin_and_a_timeout(monkeypatch):
    """🔴 THE MECHANISM, not the manners. `--nointeraction` asks `bw` not to
    prompt; stdin on /dev/null makes a prompt that ignores it read EOF and die.
    A flag alone would leave an unattended run able to hang forever."""
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        seen["kw"] = kw
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    monkeypatch.setattr(EV.subprocess, "run", fake_run)
    EV._default_runner(["bw", "--nointeraction", "status"], timeout=3.25)
    assert seen["kw"]["stdin"] == subprocess.DEVNULL
    assert seen["kw"]["timeout"] == 3.25
    assert seen["kw"]["capture_output"] is True


# --------------------------------------------------------------------------- #
# 8. `bw` absent — reachable on a host where `bw` IS installed
# --------------------------------------------------------------------------- #
def test_bw_ABSENT_is_its_own_failure_and_names_the_nix_shell_command(tmp_path):
    f = FakeBw(items=[_item()])
    with pytest.raises(EV.EscrowError) as ei:
        EV.run(bw=EV.BitwardenCLI(runner=f, locator=lambda name: None),
               identity=_identity(tmp_path), item_name=ITEM, now=NOW)
    assert ei.value.token == "BW-MISSING"
    assert ei.value.exit_code == 10
    assert "nix-shell -p bitwarden-cli jq" in str(ei.value)
    # 🔴 REACHABILITY: it refused BEFORE running anything, so the message is not
    # a fallback printed after a different failure already decided the outcome.
    assert f.calls == []


def test_a_FileNotFoundError_from_the_runner_is_also_BW_MISSING(tmp_path):
    """The locator check races with an uninstall; a raw FileNotFoundError would
    surface as an unexplained traceback instead of the message that exists."""
    def boom(argv, *, timeout):
        raise FileNotFoundError(2, "No such file or directory", "bw")

    with pytest.raises(EV.EscrowError) as ei:
        EV.run(bw=EV.BitwardenCLI(runner=boom, locator=lambda n: "/bin/bw"),
               identity=_identity(tmp_path), item_name=ITEM, now=NOW)
    assert ei.value.token == "BW-MISSING"


# --------------------------------------------------------------------------- #
# 9. the local side of the comparison
# --------------------------------------------------------------------------- #
def test_a_MISSING_on_disk_identity_is_its_own_failure(tmp_path):
    with pytest.raises(EV.EscrowError) as ei:
        EV.run(bw=_cli(FakeBw(items=[_item()])),
               identity=tmp_path / "nope.key", item_name=ITEM, now=NOW)
    assert ei.value.token == "IDENTITY-MISSING"
    assert ei.value.exit_code == 23


def test_an_EMPTY_on_disk_identity_is_a_FAILURE_not_half_a_clean_comparison(tmp_path):
    """🔴 TWO EMPTY FILES COMPARE EQUAL. Without this guard the run would report
    `IDENTICAL` over nothing at all — the shortest possible route to the false
    all-clear this script exists to prevent."""
    f = FakeBw(items=[_item(notes="")])
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, f, identity_text="")
    assert ei.value.token == "IDENTITY-EMPTY"
    assert ei.value.exit_code == 24
    # It refused before touching the vault: the local side is checked first.
    assert f.calls == []


def test_a_WHITESPACE_ONLY_on_disk_identity_is_ALSO_empty(tmp_path):
    """🔴 THE OPERAND, NOT JUST THE TRUTHINESS. `if not data:` catches a
    zero-byte file and passes a file holding one newline — which is not an age
    identity either, and would then be compared against a trimmed note and
    reported as a trailing-newline difference. Only `data.strip()` sees it."""
    f = FakeBw(items=[_item()])
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, f, identity_text="\n \t\n")
    assert ei.value.token == "IDENTITY-EMPTY"
    assert f.calls == []


def test_the_local_check_runs_BEFORE_any_network_call(tmp_path):
    f = FakeBw(items=[_item()])
    with pytest.raises(EV.EscrowError):
        EV.run(bw=_cli(f), identity=tmp_path / "absent.key",
               item_name=ITEM, now=NOW)
    assert f.calls == [], "the vault was consulted before the local file existed"


# --------------------------------------------------------------------------- #
# 10. which server answered
# --------------------------------------------------------------------------- #
def test_an_EMPTY_configured_server_is_a_FAILURE(tmp_path):
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, FakeBw(server="", items=[_item()]))
    assert ei.value.token == "SERVER-UNKNOWN"
    assert ei.value.exit_code == 15


def test_a_STALE_SESSION_pointing_at_another_server_is_a_MISMATCH(tmp_path):
    """This vault has really been repointed. A session left over from the old
    endpoint means every answer comes from a server the operator no longer
    thinks they are using — and this half needs no pin to fire."""
    f = FakeBw(server="https://new.invalid.example",
               status={"serverUrl": "https://old.invalid.example",
                       "status": "unlocked"},
               items=[_item()])
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, f)
    assert ei.value.token == "SERVER-MISMATCH"
    assert ei.value.exit_code == 16


def test_a_server_MISMATCH_message_prints_NEITHER_url(tmp_path):
    """devrc is public and messages from it get pasted into it."""
    f = FakeBw(server="https://new.invalid.example",
               status={"serverUrl": "https://old.invalid.example",
                       "status": "unlocked"}, items=[_item()])
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, f)
    msg = str(ei.value)
    assert "new.invalid.example" not in msg and "old.invalid.example" not in msg


def test_a_PINNED_server_that_disagrees_FAILS(tmp_path):
    f = FakeBw(server="https://actual.invalid.example", items=[_item()],
               status={"serverUrl": "https://actual.invalid.example",
                       "status": "unlocked"})
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, f, expect_server="https://expected.invalid.example")
    assert ei.value.token == "SERVER-MISMATCH"


def test_a_PINNED_server_that_agrees_PASSES_and_the_verdict_SAYS_it_was_pinned(tmp_path):
    """🔴 "not pinned" and "pinned and matched" are two facts. A verdict that
    printed the same sentence for both would imply a check it never made."""
    f = FakeBw(items=[_item()])
    v = _run(tmp_path, f, expect_server=SERVER + "/")   # trailing slash tolerated
    assert v.server_pinned is True
    assert v.server_session_reason is None

    v2 = _run(tmp_path, FakeBw(items=[_item()]))
    assert v2.server_pinned is False
    assert v2.server_session_reason is None


# --------------------------------------------------------------------------- #
# 10b. 🔴 THE SESSION CROSS-CHECK CANNOT SILENTLY NO-OP
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status_doc,expect_word", [
    ({"serverUrl": None, "status": "unlocked"}, "null"),
    ({"serverUrl": "", "status": "unlocked"}, "empty"),
    ({"serverUrl": "   ", "status": "unlocked"}, "empty"),
    ({"status": "unlocked"}, "null"),              # field absent entirely
])
def test_an_UNAVAILABLE_session_server_is_reported_NOT_COMPARED_never_as_matched(
        tmp_path, status_doc, expect_word):
    """🔴 THE AUDIT'S SECOND 🔴. `bw status` returns `serverUrl: null` for the
    official cloud, and the guard read `if session_server and …` — so the
    comparison was SKIPPED with no signal while the success verdict went on to
    print "the CLI's configured server matched the session's, which is all that
    was checked". A check that did not run, reported as one that passed.

    It is not a hard failure — a vault legitimately reachable without a
    serverUrl must not be a permanently-red gate — so the requirement is that
    the verdict SAYS SO, with the reason, exactly as `restore-verify.py` prints
    `NOT CROSS-CHECKED (<reason>)`.
    """
    v = _run(tmp_path, FakeBw(status=status_doc, items=[_item()]))
    assert v.server_session_reason is not None
    assert expect_word in v.server_session_reason
    line = v.line()
    assert "session cross-check NOT COMPARED" in line
    assert "session cross-check RAN" not in line
    assert "matched the authenticated session's" not in line


def test_the_verdict_line_is_pinned_WHOLE_for_every_server_combination(tmp_path):
    """🔴 PIN THE WHOLE NORMALISED STRING, not a word inside it.

    The previous assertion here was `"NOT PINNED" in v2.line()` — a bare
    substring on a sentence that was, at that moment, ALREADY FALSE about the
    session cross-check. An audit's mutant rewrote the entire sentence into a
    flat lie and SURVIVED all 87 tests, because the two words it kept were the
    two words being asserted.

    A substring cannot tell a true sentence from a confident wrong one. So each
    of the four combinations pins its complete line. A cosmetic reword fails
    this test — that is the price of a machine-readable claim, and it is worth
    paying for the one sentence an operator reads to decide the escrow is fine.
    """
    ident = _identity(tmp_path, ESCROW_NOTE, name="whole-line.key")
    reason = "SYNTHETIC-REASON"

    def mk(**kw):
        return EV.EscrowVerdict(
            item_name=ITEM, server=SERVER, escrow_bytes=ESCROW_NOTE_BYTES,
            disk_bytes=ESCROW_NOTE_BYTES, classification=EV.CLASS_IDENTICAL,
            identity=ident, **kw)

    head = (f"analyze-service-index-escrow-verify: escrow OK — the Secure Note "
            f"matches {ident} IDENTICAL (179 escrowed bytes vs 179 on disk)")
    tail = ("NOT DECRYPT-CHECKED — byte equality proves the two copies agree, "
            "NOT that either of them opens an artifact. Re-run with "
            "--decrypt-check for that claim.")
    ran = ("session cross-check RAN: the CLI's configured server matched the "
           "authenticated session's")
    notrun = f"session cross-check NOT COMPARED ({reason})"

    assert mk(server_pinned=False, server_session_reason=None).line() == (
        f"{head}; server NOT PINNED (--expect-server / ASIB_ESCROW_SERVER "
        f"unset); {ran}; {tail}")
    assert mk(server_pinned=True, server_session_reason=None).line() == (
        f"{head}; server PINNED and matched; {ran}; {tail}")
    assert mk(server_pinned=False, server_session_reason=reason).line() == (
        f"{head}; server NOT PINNED (--expect-server / ASIB_ESCROW_SERVER "
        f"unset); {notrun}; {tail}")
    assert mk(server_pinned=True, server_session_reason=reason).line() == (
        f"{head}; server PINNED and matched; {notrun}; {tail}")


def test_the_DECRYPT_CHECKED_verdict_line_is_pinned_WHOLE(tmp_path):
    """The fifth combination, for the same reason. This is the sentence that
    claims the escrowed key OPENED something."""
    ident = _identity(tmp_path, ESCROW_NOTE, name="whole-line-2.key")
    v = EV.EscrowVerdict(
        item_name=ITEM, server=SERVER, server_pinned=False,
        escrow_bytes=ESCROW_NOTE_BYTES, disk_bytes=ESCROW_NOTE_BYTES,
        classification=EV.CLASS_IDENTICAL, identity=ident,
        server_session_reason=None, decrypt_checked=True,
        decrypt_scope="scope-delta", decrypt_key=KEY_DELTA_NEW,
        decrypt_commits=3, decrypt_refs=1)
    assert v.line() == (
        f"analyze-service-index-escrow-verify: escrow OK — the Secure Note "
        f"matches {ident} IDENTICAL (179 escrowed bytes vs 179 on disk); "
        f"server NOT PINNED (--expect-server / ASIB_ESCROW_SERVER unset); "
        f"session cross-check RAN: the CLI's configured server matched the "
        f"authenticated session's; DECRYPT-CHECKED: the ESCROWED bytes "
        f"decrypted {KEY_DELTA_NEW} (scope scope-delta) and restored 3 "
        f"commit(s) over 1 ref(s)")


def test_an_unavailable_session_server_STILL_enforces_an_explicit_PIN(tmp_path):
    """🔴 REACHABILITY: the new early-return must not become an escape hatch.
    With no session serverUrl to compare against, a WRONG pinned server must
    still fail — otherwise the fix for one silent skip introduces another."""
    f = FakeBw(status={"serverUrl": None, "status": "unlocked"},
               server="https://actual.invalid.example", items=[_item()])
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, f, expect_server="https://expected.invalid.example")
    assert ei.value.token == "SERVER-MISMATCH"
    # ...and a MATCHING pin passes, so the branch above is not simply always-red.
    f2 = FakeBw(status={"serverUrl": None, "status": "unlocked"},
                server="https://actual.invalid.example", items=[_item()])
    v = _run(tmp_path, f2, expect_server="https://actual.invalid.example")
    assert v.server_pinned is True
    assert v.server_session_reason is not None


def test_url_comparison_ignores_case_and_a_trailing_slash_only(tmp_path):
    assert EV._norm_url("HTTPS://A.Example/") == EV._norm_url("https://a.example")
    assert EV._norm_url("https://a.example") != EV._norm_url("https://b.example")


# --------------------------------------------------------------------------- #
# 11. --decrypt-check: REAL artifacts, REAL age, REAL git
# --------------------------------------------------------------------------- #
def _git_env() -> dict:
    e = dict(os.environ)
    e.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@localhost",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@localhost",
        "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "file",
    })
    return e


def _make_scope(store: Path, name: str, commits: int) -> Path:
    scope = store / name
    scope.mkdir(parents=True)
    subprocess.run([GIT, "init", "-q", "-b", "trunk", str(scope)],
                   check=True, capture_output=True, env=_git_env())
    for i in range(commits):
        (scope / "e.md").write_text(f"synthetic entry {name} {i}\n", encoding="utf-8")
        subprocess.run([GIT, "-C", str(scope), "add", "e.md"],
                       check=True, capture_output=True, env=_git_env())
        subprocess.run([GIT, "-C", str(scope), "commit", "-q", "-m", f"c{i}"],
                       check=True, capture_output=True, env=_git_env())
    return scope


def _new_identity(tmp: Path, name: str) -> Path:
    key = tmp / name
    p = subprocess.run([AGE_KEYGEN, "-o", str(key)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    key.chmod(0o600)
    return key


def _recipient(identity: Path) -> str:
    p = subprocess.run([AGE_KEYGEN, "-y", str(identity)],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def _artifact(scope: Path, identity: Path, tmp: Path, *, mangle=None,
              tag: str = "i") -> bytes:
    work = tmp / f"mk-{scope.name}-{tag}-{'m' if mangle else 'i'}"
    work.mkdir(parents=True, exist_ok=True)
    bundle, cipher = work / "a.bundle", work / "a.bundle.age"
    B.bundle_scope(scope, bundle, work)
    if mangle is not None:
        mangle(bundle)
    B.encrypt(bundle, cipher, _recipient(identity))
    data = cipher.read_bytes()
    bundle.unlink(missing_ok=True)
    cipher.unlink(missing_ok=True)
    return data


def _flip_middle_byte(path: Path) -> None:
    raw = bytearray(path.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    path.write_bytes(bytes(raw))


class FakeDownloader:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = dict(objects)
        self.gets: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def list(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]

    def get(self, key):
        self.gets.append(key)
        return self.objects[key]


# 🔴 THE STAMPS ARE CHOSEN SO THAT `[-1]` AND `[0]` PICK DIFFERENT SCOPES.
# `scope-delta` holds TWO artifacts straddling `scope-epsilon`'s single one:
#
#     scope-delta    T-30h ............................ T-1h   <- newest overall
#     scope-epsilon  .............. T-2h ......................
#
# so "newest per scope, then newest scope" selects scope-delta while an
# off-by-one that read the OLDEST per scope would select scope-epsilon. A
# one-artifact-per-scope world would make those two implementations produce
# identical output, and the mutant would survive a fully green suite.
DELTA_OLD = NOW - timedelta(hours=30)
DELTA_NEW = NOW - timedelta(hours=1)
EPSILON_ONE = NOW - timedelta(hours=2)

# Pinned literal object keys — the producer's own key function is exercised in
# the fixture, but the ASSERTIONS name the string, so a change to either side of
# that seam is visible instead of tracking silently.
KEY_DELTA_NEW = "synthetic-escrow-host/scope-delta/20260309T164205Z.bundle.age"
KEY_EPSILON = "synthetic-escrow-host/scope-epsilon/20260309T154205Z.bundle.age"


@pytest.fixture()
def escrow_world(tmp_path, monkeypatch):
    """A store with TWO scopes, THREE artifacts, distinct stamps, and a real key.

    Different commit counts (3 vs 5) and different stamps, so a test can tell
    WHICH artifact was opened — a one-scope, one-artifact world would make both
    "it picked the newest" and "it picked the right scope" unfalsifiable.
    """
    monkeypatch.setenv("ASIB_HOST", HOST)
    store = tmp_path / "store"
    store.mkdir()
    good = _new_identity(tmp_path, "good.key")
    delta = _make_scope(store, "scope-delta", commits=3)
    epsilon = _make_scope(store, "scope-epsilon", commits=5)
    objects = {
        B.object_key(HOST, "scope-delta", DELTA_OLD):
            _artifact(delta, good, tmp_path, tag="old"),
        B.object_key(HOST, "scope-delta", DELTA_NEW):
            _artifact(delta, good, tmp_path, tag="new"),
        B.object_key(HOST, "scope-epsilon", EPSILON_ONE):
            _artifact(epsilon, good, tmp_path, tag="one"),
    }
    return {
        "store": store, "identity": good, "objects": objects,
        "work": B._private_dir(tmp_path / "work"),
        "note": good.read_text(encoding="utf-8"),
        "tmp": tmp_path,
    }


def test_the_fixture_keys_are_the_LITERALS_the_selection_tests_PIN():
    """HARNESS SELF-CHECK for the two pinned key strings, and for the ordering
    the selection tests depend on: delta's newest is newer than epsilon's, and
    delta's oldest is older."""
    assert B.object_key(HOST, "scope-delta", DELTA_NEW) == KEY_DELTA_NEW
    assert B.object_key(HOST, "scope-epsilon", EPSILON_ONE) == KEY_EPSILON
    assert DELTA_OLD < EPSILON_ONE < DELTA_NEW < NOW


def _decrypt_run(world, *, note: str | None = None, objects=None, scope=None):
    d = FakeDownloader(world["objects"] if objects is None else objects)
    return EV.run(
        bw=_cli(FakeBw(items=[_item(notes=world["note"] if note is None else note)])),
        identity=world["identity"],
        item_name=ITEM, decrypt=True, prefix=PREFIX, store=world["store"],
        scope_filter=scope, work_dir=world["work"], now=NOW,
        downloader_factory=lambda: d), d


def test_decrypt_check_SUCCEEDS_with_the_escrowed_key_and_reports_real_counts(escrow_world):
    """🔴 THE CLAIM THAT MATTERS: the ESCROWED bytes — written to a throwaway
    file — decrypt a real artifact and restore real history.

    Every expected value is a literal pinned from the fixture (3 commits over 1
    ref for `scope-delta`, whose NEWEST artifact is the newest in the bucket),
    not read back from the verdict — a pipeline that restored nothing cannot
    satisfy this by reporting zeros. The KEY is asserted too, so an off-by-one
    that took a scope's OLDEST artifact is visible even though the scope name
    would be the same.
    """
    v, d = _decrypt_run(escrow_world)
    assert v.decrypt_checked is True
    assert v.decrypt_scope == "scope-delta"        # holds the newest artifact
    assert v.decrypt_key == KEY_DELTA_NEW          # ...and it took the NEWEST one
    assert v.decrypt_commits == 3
    assert v.decrypt_refs == 1
    assert d.gets == [KEY_DELTA_NEW], "exactly one artifact, and the right one"
    assert "DECRYPT-CHECKED: " in v.line()
    assert "NOT DECRYPT-CHECKED" not in v.line()


def test_decrypt_check_can_be_pointed_at_a_NAMED_scope(escrow_world):
    """THE SECOND MEASUREMENT POINT: the selector really selects, so the default
    above was not just the only thing it could ever have returned. Different
    scope, different key, different commit count — all three move."""
    v, d = _decrypt_run(escrow_world, scope="scope-epsilon")
    assert v.decrypt_scope == "scope-epsilon"
    assert v.decrypt_key == KEY_EPSILON
    assert v.decrypt_commits == 5
    assert d.gets == [KEY_EPSILON]


def test_a_prefix_with_NO_trailing_slash_still_selects_the_right_objects(escrow_world):
    """🔴 AN S3 PREFIX IS A BYTE PREFIX, NOT A PATH COMPONENT. Without
    normalisation `<host>` would also list `<host>-laptop/…`, and the key parser
    would then see a scope of `''`. Passing the un-slashed form is the only way
    to observe that the normalisation happens at all."""
    d = FakeDownloader(escrow_world["objects"])
    v = EV.run(bw=_cli(FakeBw(items=[_item(notes=escrow_world["note"])])),
               identity=escrow_world["identity"], item_name=ITEM, decrypt=True,
               prefix=HOST,                       # no trailing slash
               store=escrow_world["store"], work_dir=escrow_world["work"],
               now=NOW, downloader_factory=lambda: d)
    assert v.decrypt_key == KEY_DELTA_NEW
    assert v.decrypt_commits == 3


def test_a_matching_but_WRONG_key_fails_with_DECRYPT_FAILED(escrow_world, tmp_path):
    """🔴 THE ESCROW IS INTACT AND USELESS. Both copies agree byte-for-byte, so
    the byte check PASSES — and the artifacts were encrypted to a different
    recipient, so nothing opens. This is the case byte equality alone cannot
    see, which is the entire argument for --decrypt-check.
    """
    wrong = _new_identity(tmp_path, "wrong.key")
    text = wrong.read_text(encoding="utf-8")
    d = FakeDownloader(escrow_world["objects"])
    with pytest.raises(EV.EscrowError) as ei:
        EV.run(bw=_cli(FakeBw(items=[_item(notes=text)])),
               identity=wrong,                    # both sides agree: bytes MATCH
               item_name=ITEM, decrypt=True, prefix=PREFIX,
               store=escrow_world["store"], work_dir=escrow_world["work"],
               now=NOW, downloader_factory=lambda: d)
    assert ei.value.token == "DECRYPT-FAILED"
    assert ei.value.exit_code == 25
    # 🔴 DISTINGUISHABLE from a byte mismatch — the two are different faults
    # with different remedies and this is the pair that used to be one word.
    assert ei.value.exit_code != EV.EXIT_CODES["BYTES-DIFFER-MATERIALLY"]
    assert ei.value.exit_code != EV.EXIT_CODES["BYTES-DIFFER-TRAILING-NEWLINE"]
    assert ei.value.exit_code != EV.EXIT_CODES["RESTORE-FAILED"]


def test_a_CORRUPTED_ARTIFACT_is_RESTORE_FAILED_not_DECRYPT_FAILED(escrow_world, tmp_path):
    """🔴 THE SECOND MEASUREMENT POINT for the classifier. A key fault and a
    data fault must not share a token: only the first says anything about the
    escrow, and the remedies are "re-escrow" and "the backup is damaged"."""
    scope = escrow_world["store"] / "scope-delta"
    bad = _artifact(scope, escrow_world["identity"], tmp_path,
                    mangle=_flip_middle_byte, tag="bad")
    objects = dict(escrow_world["objects"])
    objects[KEY_DELTA_NEW] = bad
    with pytest.raises(EV.EscrowError) as ei:
        _decrypt_run(escrow_world, objects=objects)
    assert ei.value.token == "RESTORE-FAILED"
    assert ei.value.exit_code == 26


def test_a_ZERO_BYTE_object_is_ARTIFACT_UNREADABLE_and_never_claims_it_DECRYPTED(
        escrow_world):
    """🔴 AUDIT CASE 3. `verify_artifact` refuses a 0-byte object BEFORE calling
    `decrypt()`, so the old classifier — which read the absence of a substring —
    reported RESTORE-FAILED and asserted the bytes "DECRYPTED".

    The message must not contain that word in the past tense about this run, and
    the token must say the pipeline never reached the key.
    """
    objects = dict(escrow_world["objects"])
    objects[KEY_DELTA_NEW] = b""
    with pytest.raises(EV.EscrowError) as ei:
        _decrypt_run(escrow_world, objects=objects)
    assert ei.value.token == "ARTIFACT-UNREADABLE"
    assert ei.value.exit_code == 30
    msg = str(ei.value)
    assert "DECRYPTED" not in msg
    assert "The escrowed key works" not in msg
    # It says the opposite, and says it explicitly.
    assert "NOTHING here decrypted" in msg


def _corrupt_payload(blob: bytes, offset: int = 400) -> bytes:
    """Flip a byte PAST the age header. XOR, never a literal write.

    🔴 `printf '\\xff'` is a NO-OP when the byte was already 0xff, and one such
    no-op nearly produced a measurement saying age fails to detect tampering.
    XOR always changes the byte. Verify the mutation actually mutates.
    """
    b = bytearray(blob)
    before = b[offset]
    b[offset] ^= 0xFF
    assert b[offset] != before
    return bytes(b)


@pytest.mark.parametrize("mangle,name", [
    (lambda blob: _corrupt_payload(blob, 400), "payload byte flipped"),
    (lambda blob: _corrupt_payload(blob, 300), "payload byte flipped, offset 300"),
    (lambda blob: blob[:-30], "ciphertext truncated"),
])
def test_a_TAMPERED_or_TRUNCATED_artifact_is_ARTIFACT_CORRUPT_not_EMPTY(
        escrow_world, tmp_path, mangle, name):
    """🔴 THE REGRESSION THIS ROUND EXISTS FOR — a verifier reporting TAMPERING
    as "nothing to worry about".

    The previous classifier read file PRESENCE as "age reported success" and so
    reported a tampered or truncated artifact as `ARTIFACT-EMPTY` — *"THE ESCROW
    IS FINE … a valid encryption of an empty payload"* — while quoting age's own
    "may be corrupted or tampered with" in the same breath. Detecting tampering
    is the single most important thing a backup verifier does.

    Re-measured (age v1.3.1, 8 offsets x 4 sizes + 3 truncations): age writes
    output BEFORE authenticating the payload, so PRESENT+rc!=0 means the header
    authenticated — the escrowed key WORKED — and the bytes did not.
    """
    good = escrow_world["objects"][KEY_DELTA_NEW]
    objects = dict(escrow_world["objects"])
    objects[KEY_DELTA_NEW] = mangle(good)
    with pytest.raises(EV.EscrowError) as ei:
        _decrypt_run(escrow_world, objects=objects)
    assert ei.value.token == "ARTIFACT-CORRUPT", name
    assert ei.value.exit_code == 33
    # 🔴 It must NOT be any of the three verdicts that would calm the operator
    # down or send them at the key.
    for wrong in ("ARTIFACT-EMPTY", "DECRYPT-FAILED", "ARTIFACT-UNREADABLE"):
        assert ei.value.exit_code != EV.EXIT_CODES[wrong], (name, wrong)


def test_a_valid_encryption_of_NOTHING_says_the_ESCROW_IS_FINE(escrow_world, tmp_path):
    """🔴 AUDIT CASE 2 — THE ONE THAT GETS A GOOD DR KEY ROTATED.

    `age` encrypts a zero-byte payload to a perfectly valid ~200-byte
    ciphertext and decrypts it back at rc=0. The old classifier saw
    restore-verify's `DECRYPT FAILED … decrypted to ZERO bytes` and reported
    DECRYPT-FAILED — "not (or no longer) a working identity" — for a key that
    had just worked. Acting on that verdict destroys the escrow.

    The discriminator is MEASURED, not guessed (age v1.3.1): a refusal leaves NO
    output file, a success-on-empty leaves one at size 0. `_decrypt_phase_probe`
    reads exactly that.
    """
    empty = tmp_path / "empty-payload"
    empty.write_bytes(b"")
    cipher = tmp_path / "empty-payload.age"
    B.encrypt(empty, cipher, _recipient(escrow_world["identity"]))
    blob = cipher.read_bytes()
    assert blob.startswith(b"age-encryption.org/") and len(blob) > 100

    objects = dict(escrow_world["objects"])
    objects[KEY_DELTA_NEW] = blob
    with pytest.raises(EV.EscrowError) as ei:
        _decrypt_run(escrow_world, objects=objects)
    assert ei.value.token == "ARTIFACT-EMPTY"
    assert ei.value.exit_code == 31
    assert ei.value.exit_code != EV.EXIT_CODES["DECRYPT-FAILED"]
    assert ei.value.exit_code != EV.EXIT_CODES["ARTIFACT-CORRUPT"]
    # 🔴 THE WHOLE OWNED SENTENCE, BY EQUALITY — see the module docstring on
    # `verdict` vs `detail`. The previous assertions here were
    # `"THE ESCROW IS FINE" in msg` and `"Do NOT re-escrow or rotate" in msg`,
    # and BOTH passed unchanged on a TAMPERED artifact, certifying a sentence
    # that was false on the very run they tested.
    assert ei.value.verdict == (
        f"the ESCROWED key OPENED {KEY_DELTA_NEW} and the artifact contains "
        f"NOTHING. 🔴 THE ESCROW IS FINE — age exited ZERO, which a wrong key "
        f"cannot make it do; the payload is a valid encryption of an empty "
        f"file. Do NOT re-escrow or rotate on the strength of this. Run "
        f"`restore-verify.py` to diagnose the artifact.")
    # The upstream half is carried separately, so it stays OUT of the pin.
    assert ei.value.detail and "DECRYPT FAILED" in ei.value.detail


def test_age_ABSENT_is_an_ENVIRONMENT_fault_not_a_verdict_on_the_escrow(
        escrow_world, monkeypatch):
    """🔴 AUDIT CASE 1. With `age` off PATH the old code reported RESTORE-FAILED,
    asserting in one sentence that the escrowed key WORKED, that the artifact was
    at fault, and that `restore-verify.py` would diagnose it — three false
    claims, the last of which sends the operator to a tool that fails
    identically for the same unnamed reason."""
    monkeypatch.setattr(EV.shutil, "which",
                        lambda name: None if name == "age" else "/usr/bin/" + name)
    with pytest.raises(EV.EscrowError) as ei:
        _decrypt_run(escrow_world)
    assert ei.value.token == "AGE-MISSING"
    assert ei.value.exit_code == 29
    # The owned sentence, WHOLE — a substring here would pass on a reworded
    # message that had stopped saying the thing that matters.
    #
    # 🔴 THIS IS NOW THE *PREFLIGHT* SENTENCE, and the difference is the point:
    # the check moved ahead of the vault, so it can truthfully add that nothing
    # was checked and no `bw` call was made. The LATE site keeps its own
    # wording — see the test below — because "the vault was NOT contacted"
    # would be FALSE there, and one shared string would make one of the two
    # sites lie.
    assert ei.value.verdict == (
        "`age` is not on PATH, so the escrowed key cannot be tested against "
        "anything. NOTHING HAS BEEN CHECKED and the vault was NOT contacted — "
        "this refusal is raised before any `bw` call. It is an ENVIRONMENT "
        "fault and says NOTHING about the escrow: do not read it as a verdict "
        "on the key or on the artifacts. age is declared in "
        "nix/pkgs/default.nix and in flake.nix `gateTools`.")
    assert ei.value.detail is None


def test_the_LATE_age_check_is_still_REACHABLE_and_says_something_TRUE(
        escrow_world, monkeypatch):
    """🔴 A LAYER NOBODY CAN OBSERVE FAILING IS A LAYER NOBODY KNOWS IS GONE.

    Hoisting the `age` check into the preflight makes the original one
    unreachable through `run()` — so it is exercised HERE, by driving
    `decrypt_check` directly, which is also the real path if `age` vanishes
    between the preflight and the decrypt. Its wording must stay its own: it
    runs after the vault, so it must NOT claim the vault was not contacted.
    """
    monkeypatch.setattr(EV.shutil, "which",
                        lambda name: None if name == "age" else "/usr/bin/" + name)
    with pytest.raises(EV.EscrowError) as ei:
        EV.decrypt_check(escrow_bytes=escrow_world["note"].encode(),
                         work_dir=escrow_world["work"], bucket="b",
                         prefix=PREFIX, store=escrow_world["store"],
                         scope_filter=None, from_dir=None, now=NOW,
                         downloader_factory=lambda: FakeDownloader(
                             escrow_world["objects"]))
    assert ei.value.token == "AGE-MISSING"
    assert "vault was NOT contacted" not in ei.value.verdict, (
        "the late check copied the preflight's sentence, which is false there")
    assert "ENVIRONMENT fault" in ei.value.verdict


def test_the_age_precondition_runs_BEFORE_the_store_is_opened(escrow_world,
                                                              monkeypatch):
    """REACHABILITY for the precondition, and it is load-bearing twice: it also
    makes the phase probe's "no plaintext ⇒ age refused" inference sound."""
    monkeypatch.setattr(EV.shutil, "which",
                        lambda name: None if name == "age" else "/usr/bin/" + name)
    d = FakeDownloader(escrow_world["objects"])
    with pytest.raises(EV.EscrowError) as ei:
        EV.run(bw=_cli(FakeBw(items=[_item(notes=escrow_world["note"])])),
               identity=escrow_world["identity"], item_name=ITEM, decrypt=True,
               prefix=PREFIX, store=escrow_world["store"],
               work_dir=escrow_world["work"], now=NOW,
               downloader_factory=lambda: d)
    assert ei.value.token == "AGE-MISSING"
    assert d.gets == [], "the store was opened before the precondition was checked"


def test_every_decrypt_family_VERDICT_is_pinned_WHOLE(escrow_world, tmp_path):
    """🔴 FOUR OWNED SENTENCES, BY EXACT EQUALITY, IN ONE PLACE.

    The other two decrypt-family verdicts are pinned whole beside the cases they
    belong to — `ARTIFACT-EMPTY` in
    `test_a_valid_encryption_of_NOTHING_says_the_ESCROW_IS_FINE`, and the
    in-classifier `AGE-MISSING` in
    `test_the_in_classifier_AGE_MISSING_belt_is_REACHED_and_pinned`. Six
    verdicts, six exact pins, three files' worth of context; this docstring used
    to say "all five" while the body pinned four, which is the same
    wider-than-the-code sentence this suite exists to catch.


    The verdict LINES were already pinned whole; the refusal MESSAGES were not,
    and that is where the false sentence survived a green suite. `verdict` holds
    only what this module asserts, so equality is possible without pinning
    another module's wording or an age exit code that varies run to run.

    A cosmetic reword fails this test. That is the price of a machine-readable
    claim on the sentences that decide whether a key gets rotated.
    """
    good = escrow_world["objects"][KEY_DELTA_NEW]
    objects = dict(escrow_world["objects"])

    # 1. ARTIFACT-CORRUPT
    objects[KEY_DELTA_NEW] = _corrupt_payload(good)
    with pytest.raises(EV.EscrowError) as ei:
        _decrypt_run(escrow_world, objects=objects)
    assert ei.value.verdict == (
        f"🔴 {KEY_DELTA_NEW} is TAMPERED, CORRUPT or TRUNCATED. age "
        f"authenticated the header with the ESCROWED key — which a non-matching "
        f"identity cannot do — began writing plaintext, and then FAILED on the "
        f"payload. THE ESCROW IS FINE; THE BACKUP IS NOT. This is the finding a "
        f"backup verifier exists to make: treat the artifact as unusable, check "
        f"the other retained objects for this scope, and do NOT rotate the key.")

    # 2. ARTIFACT-UNREADABLE
    objects[KEY_DELTA_NEW] = b""
    with pytest.raises(EV.EscrowError) as ei:
        _decrypt_run(escrow_world, objects=objects)
    assert ei.value.verdict == (
        f"the pipeline failed BEFORE the escrowed key was ever used, on "
        f"{KEY_DELTA_NEW} — an empty or unreadable object, not a fact about the "
        f"escrow. NOTHING here decrypted, and nothing here is evidence for or "
        f"against the escrowed key. Run `restore-verify.py` to diagnose the "
        f"object.")

    # 3. DECRYPT-FAILED — wrong key, so age writes nothing at all.
    wrong = _new_identity(tmp_path, "verdict-wrong.key")
    d = FakeDownloader(escrow_world["objects"])
    with pytest.raises(EV.EscrowError) as ei:
        EV.run(bw=_cli(FakeBw(items=[_item(notes=wrong.read_text())])),
               identity=wrong, item_name=ITEM, decrypt=True, prefix=PREFIX,
               store=escrow_world["store"], work_dir=escrow_world["work"],
               now=NOW, downloader_factory=lambda: d)
    assert ei.value.verdict == (
        f"age REFUSED {KEY_DELTA_NEW} without writing any plaintext at all. TWO "
        f"CAUSES PRODUCE THIS AND THEY ARE NOT SEPARABLE FROM HERE: the escrowed "
        f"identity does not match this artifact's recipients, or the artifact's "
        f"HEADER is damaged. Neither is asserted. To tell them apart, try a "
        f"DIFFERENT artifact with this same escrowed copy — `--scope <another "
        f"scope>`, or `restore-verify.py --all` for an older stamp: if another "
        f"artifact OPENS, the escrowed key is fine and THIS object's header is "
        f"damaged; if none open, the key is the likely cause. Do NOT rotate the "
        f"key before running that.")

    # 4. RESTORE-FAILED — decrypt returns, the BUNDLE inside is damaged.
    scope = escrow_world["store"] / "scope-delta"
    objects[KEY_DELTA_NEW] = _artifact(scope, escrow_world["identity"], tmp_path,
                                       mangle=_flip_middle_byte, tag="vpin")
    with pytest.raises(EV.EscrowError) as ei:
        _decrypt_run(escrow_world, objects=objects)
    assert ei.value.token == "RESTORE-FAILED"
    assert ei.value.verdict == (
        f"the ESCROWED bytes DECRYPTED {KEY_DELTA_NEW} — `decrypt()` RETURNED, "
        f"which is what makes that claim observable — and the restore then "
        f"failed. This is a fault in the ARTIFACT, not in the escrow. The "
        f"escrowed key works; run `restore-verify.py` to diagnose the artifact.")


def test_a_HEADER_corrupt_artifact_lands_on_the_NOT_SEPARABLE_verdict(escrow_world):
    """🔴 THE HONEST LIMIT, MEASURED AND PINNED.

    Header corruption and a wrong key BOTH give rc=1 with no output file, so
    they are genuinely indistinguishable from outside. The verdict must
    therefore assert NEITHER — and must hand over the check that does separate
    them (re-run with the ON-DISK identity). A verdict that blamed the escrow
    here would send someone to rotate a working key over a damaged artifact.
    """
    good = escrow_world["objects"][KEY_DELTA_NEW]
    b = bytearray(good)
    b[40] ^= 0xFF                      # offset 40 is inside the age header
    objects = dict(escrow_world["objects"])
    objects[KEY_DELTA_NEW] = bytes(b)
    with pytest.raises(EV.EscrowError) as ei:
        _decrypt_run(escrow_world, objects=objects)
    assert ei.value.token == "DECRYPT-FAILED"
    assert "NOT SEPARABLE FROM HERE" in ei.value.verdict
    assert "Do NOT rotate the key before running that" in ei.value.verdict
    # It must not claim the escrow is the fault, which the old wording did.
    assert "not (or no longer) a working identity" not in ei.value.verdict
    # 🔴 AND THE HANDOVER MUST BE A CONTROL, NOT A SECOND SAMPLE. `decrypt_check`
    # only runs when the escrowed bytes are byte-IDENTICAL to the on-disk
    # identity, so "re-run with the ON-DISK identity" was the same experiment
    # with the same input — guaranteed to fail identically, and the conclusion
    # ("the artifact is the problem") unsound, since both copies can be a stale
    # key while the artifact is fine. The check offered must vary the ARTIFACT.
    assert "ON-DISK identity" not in ei.value.verdict
    assert "DIFFERENT artifact" in ei.value.verdict


def test_the_decrypt_CAUSE_is_a_published_VALUE_not_a_substring():
    """🔴 THE SEAM, pinned from both sides.

    `escrow-verify` branches on `restore_verify.DECRYPT_*`. If those constants
    are renamed or the set changes, this goes red rather than the classifier
    silently falling into its `else` — which is how a tampered artifact got
    reported as empty in the first place.
    """
    assert RV.DECRYPT_AGE_MISSING == "age-missing"
    assert RV.DECRYPT_AGE_REFUSED == "age-refused"
    assert RV.DECRYPT_EMPTY_PLAINTEXT == "empty-plaintext"
    assert RV.DECRYPT_CAUSES == {"age-missing", "age-refused", "empty-plaintext"}
    with pytest.raises(KeyError):
        RV.RestoreVerifyError("x", cause="not-a-published-cause")
    # A failure with no published cause reads as None, never as a default one.
    assert RV.RestoreVerifyError("x").cause is None


def test_the_in_classifier_AGE_MISSING_belt_is_REACHED_and_pinned(escrow_world,
                                                                  monkeypatch):
    """🔴 F2: THE BELT BRANCH HAD ZERO COVERAGE.

    A sweep found `if phase["cause"] == RV.DECRYPT_AGE_MISSING:` -> `if False:`
    SURVIVED the whole suite. With the branch gone, control falls through to
    `plain_present == False` and reports `DECRYPT-FAILED` — "age REFUSED {key}"
    — which is exactly the environment-fault-blamed-on-the-escrow
    misclassification the token exists to prevent.

    The branch is for `age` vanishing BETWEEN the precondition and the call, so
    the test injects precisely that: `which` still answers (precondition
    passes), and the decrypt step raises with the published age-missing cause.
    """
    monkeypatch.setattr(EV.shutil, "which", lambda name: "/usr/bin/" + name)
    RVmod = EV._rv()

    def gone(cipher, plain, identity):
        raise RVmod.RestoreVerifyError(
            "age is not on PATH", cause=RVmod.DECRYPT_AGE_MISSING)

    monkeypatch.setattr(RVmod, "decrypt", gone)
    with pytest.raises(EV.EscrowError) as ei:
        _decrypt_run(escrow_world)
    assert ei.value.token == "AGE-MISSING"
    assert ei.value.exit_code == 29
    assert ei.value.exit_code != EV.EXIT_CODES["DECRYPT-FAILED"]
    assert ei.value.verdict == (
        "`age` vanished between the precondition check and the decrypt call, so "
        "the escrowed key was never tested. This is an ENVIRONMENT fault and "
        "says NOTHING about the escrow or the artifacts.")
    assert _work_contents(escrow_world["work"]) == []


def test_a_STALE_plaintext_cannot_turn_a_WRONG_KEY_into_ARTIFACT_CORRUPT(tmp_path):
    """🔴 F3: THE HEADLINE DEFECT, RECREATED ONE FILE OVER.

    `plain_present` is a sound witness only if the output path cannot
    pre-exist. MEASURED: on BOTH the wrong-key and damaged-header paths age
    leaves a PRE-EXISTING `--output` file completely untouched (rc=1, present,
    original bytes intact) — it creates the file only after authenticating the
    header. `work_dir` is routinely REUSED, so a leftover from a run aborted
    mid-decrypt makes a WRONG KEY read as `ARTIFACT-CORRUPT` — "THE ESCROW IS
    FINE; THE BACKUP IS NOT … do NOT rotate the key."

    Driven through `restore_verify.decrypt` directly, which is where the unlink
    lives, with a positive control that the stale file really was there first.
    """
    ident_a = _new_identity(tmp_path, "stale-a.key")
    ident_b = _new_identity(tmp_path, "stale-b.key")
    src = tmp_path / "payload.bin"
    src.write_bytes(b"synthetic payload for the stale-plaintext case\n" * 64)
    cipher = tmp_path / "payload.age"
    B.encrypt(src, cipher, _recipient(ident_a))

    plain = tmp_path / "leftover.bundle"
    plain.write_bytes(b"STALE-LEFTOVER-FROM-AN-ABORTED-RUN")
    assert plain.is_file(), "positive control: the stale file must exist first"

    with pytest.raises(RV.RestoreVerifyError) as ei:
        RV.decrypt(cipher, plain, ident_b)          # ident_b cannot open it
    assert ei.value.cause == RV.DECRYPT_AGE_REFUSED
    # 🔴 THE ASSERTION THAT MATTERS: the stale file is GONE, so a consumer
    # reading `plain.exists()` gets the truth — age wrote nothing.
    assert not plain.exists(), (
        "the stale plaintext survived a failed decrypt; `plain_present` would "
        "report a wrong key as a corrupt artifact")


def test_the_probe_reports_an_UNREADABLE_plaintext_path_as_UNKNOWN(tmp_path,
                                                                  monkeypatch):
    """🔴 THIS TEST IS INTERPRETER-DEPENDENT, AND THAT IS THE MECHANISM.

    A parent directory without `+x` makes `Path.exists()` raise `PermissionError`
    — or not — depending on the CPython version. MEASURED, three interpreters,
    twice each (behaviour, and whether `Path.exists`'s source still calls
    `pathlib._ignore_error`):

        3.12.14 (the flake's pin)  raises PermissionError   _ignore_error: yes
        3.13.15                    raises PermissionError   _ignore_error: yes
        3.14.7                     returns False            _ignore_error: no

    `_ignore_error` swallows only ENOENT/ENOTDIR/EBADF/ELOOP, so EACCES
    propagates; 3.14 dropped that helper from `exists()` and swallows
    unconditionally.

    🔴 THE BOUNDARY IS 3.14, NOT 3.13. An earlier revision of the source comment
    said 3.13; a version guard written to that number would have gone RED on 3.13
    for a reason nobody could find, in a test whose name reads like a logic bug —
    exactly the failure this guard exists to prevent, reintroduced by the guard.
    So the expectation is NOT keyed on a version literal at all: it is taken from
    an INDEPENDENT probe of the interpreter, on its own path, not through the
    module under test. The version table above is documentation; the probe is the
    control.

    Either way the requirement is the same and is what is asserted: no OSError
    escapes the handler, and an unreadable path reads as None (unobservable) —
    never False, which downstream would take for "age wrote nothing".
    """
    if os.geteuid() == 0:
        pytest.skip("root traverses a directory without +x; the arm is "
                    "unreachable as root and a skip here is honest")
    RVmod = EV._rv()

    # INDEPENDENT REGIME PROBE: same hazard, its own directory, no involvement
    # from the module under test. This is what makes the assertion below
    # meaningful on an interpreter nobody has run this suite on yet.
    canary_dir = tmp_path / "regime"
    canary_dir.mkdir()
    canary = canary_dir / "c"
    canary.write_text("x", encoding="utf-8")
    os.chmod(canary_dir, 0o600)
    try:
        try:
            canary.exists()
            exists_raises = False
        except OSError:
            exists_raises = True
    finally:
        os.chmod(canary_dir, 0o700)

    holder = tmp_path / "noexec"
    holder.mkdir()
    plain = holder / "p.bundle"
    plain.write_text("x", encoding="utf-8")

    def raiser(cipher, p, identity):
        os.chmod(holder, 0o600)                 # rw- : traversal denied
        raise RVmod.RestoreVerifyError("synthetic", cause=RVmod.DECRYPT_AGE_REFUSED)

    monkeypatch.setattr(RVmod, "decrypt", raiser)
    try:
        with EV._decrypt_phase_probe(RVmod) as state:
            with pytest.raises(RVmod.RestoreVerifyError):
                RVmod.decrypt(tmp_path / "c.age", plain, tmp_path / "k")
        # 🔴 The real exception got out INTACT on every interpreter — that is the
        # part that must never regress, and it is asserted above by
        # `pytest.raises(RestoreVerifyError)` rather than PermissionError.
        if exists_raises:
            # Handler LIVE: without it this line is never reached, because
            # `exists()` raises out of the probe's `except` block.
            assert state["plain_present"] is None, (
                "the OSError handler did not run on an interpreter whose "
                "Path.exists() raises")
        else:
            # Handler DEAD on this interpreter: `exists()` answered False by
            # itself. Asserted rather than skipped, so the test still covers the
            # probe's behaviour instead of quietly covering nothing.
            assert state["plain_present"] is False, (
                "Path.exists() returned rather than raising, so the probe should "
                "have recorded its answer verbatim")
        assert state["cause"] == RVmod.DECRYPT_AGE_REFUSED
    finally:
        os.chmod(holder, 0o700)


def test_the_phase_probe_OBSERVES_rather_than_reimplements(escrow_world):
    """🔴 THE PROBE MUST BE TRANSPARENT. It wraps restore-verify's `decrypt` for
    the duration of one call; if it changed behaviour, or failed to restore the
    original, every later run in the same process would be wrong.

    Watch it: the module attribute is the same object before and after, and a
    successful run still reports `returned`."""
    RVmod = EV._rv()
    before = RVmod.decrypt
    seen = {}
    with EV._decrypt_phase_probe(RVmod) as state:
        assert RVmod.decrypt is not before, "the probe never installed itself"
        seen["state"] = state
    assert RVmod.decrypt is before, "the probe did not restore the original"
    assert seen["state"] == {"reached": False, "returned": False,
                             "plain_present": None, "cause": None}
    # And on a real run it records the success phase.
    v, _ = _decrypt_run(escrow_world)
    assert v.decrypt_checked is True
    assert RVmod.decrypt is before


def test_ZERO_artifacts_under_the_prefix_is_NO_ARTIFACT_not_a_clean_check(escrow_world):
    """🔴 A key that was never asked to decrypt anything has not been shown to
    work. An empty bucket must not read as a successful decrypt check."""
    with pytest.raises(EV.EscrowError) as ei:
        _decrypt_run(escrow_world, objects={})
    assert ei.value.token == "NO-ARTIFACT"
    assert ei.value.exit_code == 28


def test_a_NAMED_scope_with_no_artifacts_is_NO_ARTIFACT(escrow_world):
    with pytest.raises(EV.EscrowError) as ei:
        _decrypt_run(escrow_world, scope="scope-zeta-does-not-exist")
    assert ei.value.token == "NO-ARTIFACT"


def test_decrypt_check_works_against_a_LOCAL_DIRECTORY_of_artifacts(escrow_world,
                                                                    tmp_path):
    """SECRETS.md's restore recipe fetches objects to disk first, so verifying
    what is on disk is the natural next step — and it is the only decrypt-check
    path that can be exercised without a cluster.

    No `downloader_factory` here: this drives the REAL `--from-dir` reader
    (`restore_verify.DirectoryStore`), so the wiring is measured rather than
    stubbed out by the same seam every other test uses.
    """
    root = tmp_path / "fetched"
    for k, v in escrow_world["objects"].items():
        p = root / k
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(v)
    v = EV.run(bw=_cli(FakeBw(items=[_item(notes=escrow_world["note"])])),
               identity=escrow_world["identity"], item_name=ITEM, decrypt=True,
               bucket=str(root), prefix=PREFIX, store=escrow_world["store"],
               from_dir=root, work_dir=escrow_world["work"], now=NOW)
    assert v.decrypt_key == KEY_DELTA_NEW
    assert v.decrypt_commits == 3
    assert _work_contents(escrow_world["work"]) == []


def test_a_from_dir_NO_ARTIFACT_message_names_the_DIRECTORY_not_a_bucket(escrow_world,
                                                                        tmp_path):
    """🔴 A MESSAGE THAT NAMES THE WRONG PLACE SENDS THE READER TO THE WRONG
    PLACE. Under `--from-dir` the source is a local directory; printing the
    default bucket name would describe a query nobody made."""
    empty = tmp_path / "fetched-empty"
    empty.mkdir()
    with pytest.raises(EV.EscrowError) as ei:
        EV.run(bw=_cli(FakeBw(items=[_item(notes=escrow_world["note"])])),
               identity=escrow_world["identity"], item_name=ITEM, decrypt=True,
               bucket=str(empty), prefix=PREFIX, store=escrow_world["store"],
               from_dir=empty, work_dir=escrow_world["work"], now=NOW)
    assert ei.value.token == "NO-ARTIFACT"
    assert str(empty) in str(ei.value)
    assert EV.DEFAULT_BUCKET not in str(ei.value)


def test_the_CLI_passes_the_from_dir_through_as_the_SOURCE(tmp_path):
    """The same fact through `main()`'s own argument plumbing — the place the
    bucket-vs-directory substitution actually lives."""
    empty = tmp_path / "cli-fetched-empty"
    empty.mkdir()
    p = _run_cli(tmp_path, _plan(items=[_item()]),
                 "--decrypt-check", "--from-dir", str(empty),
                 "--host", HOST, "--store", str(tmp_path / "no-store"))
    assert p.returncode == EV.EXIT_CODES["NO-ARTIFACT"], (p.stdout, p.stderr)
    assert str(empty) in p.stderr
    assert EV.DEFAULT_BUCKET not in p.stderr


def test_a_store_that_cannot_be_OPENED_is_STORE_UNREACHABLE(escrow_world):
    """Classified by PHASE, not by parsing somebody else's error text."""
    class Boom:
        def __enter__(self):
            raise OSError("no route to the tenant")

        def __exit__(self, *e):
            return False

    with pytest.raises(EV.EscrowError) as ei:
        EV.run(bw=_cli(FakeBw(items=[_item(notes=escrow_world["note"])])),
               identity=escrow_world["identity"], item_name=ITEM, decrypt=True,
               prefix=PREFIX, store=escrow_world["store"],
               work_dir=escrow_world["work"], now=NOW,
               downloader_factory=Boom)
    assert ei.value.token == "STORE-UNREACHABLE"
    assert ei.value.exit_code == 27


def test_a_BYTE_MISMATCH_short_circuits_and_never_reaches_the_store(escrow_world):
    """The codes must stay distinguishable, so the run stops at the first
    finding rather than reporting a decrypt failure caused by a wrong note."""
    d = FakeDownloader(escrow_world["objects"])
    with pytest.raises(EV.EscrowError) as ei:
        EV.run(bw=_cli(FakeBw(items=[_item(notes=OTHER_KEY)])),
               identity=escrow_world["identity"], item_name=ITEM, decrypt=True,
               prefix=PREFIX, store=escrow_world["store"],
               work_dir=escrow_world["work"], now=NOW,
               downloader_factory=lambda: d)
    assert ei.value.token == "BYTES-DIFFER-MATERIALLY"
    assert d.gets == [], "the artifact store was touched on a byte-mismatch path"


# --------------------------------------------------------------------------- #
# 12. 🔴 NO KEY MATERIAL SURVIVES — on the success path AND the failure paths
# --------------------------------------------------------------------------- #
def _work_contents(work: Path) -> list[str]:
    return sorted(p.name for p in work.iterdir())


def test_no_key_material_survives_a_SUCCESSFUL_decrypt_check(escrow_world):
    work = escrow_world["work"]
    v, _ = _decrypt_run(escrow_world)
    assert v.decrypt_checked is True
    assert not (work / EV.ESCROW_IDENTITY_FILENAME).exists()
    assert _work_contents(work) == [], _work_contents(work)


@pytest.mark.parametrize("case", ["wrong-key", "corrupt-artifact"])
def test_no_key_material_survives_a_FAILING_decrypt_check(escrow_world, tmp_path, case):
    """🔴 THE PATH IT EXISTS FOR. A failed run is exactly when a copy of a
    decryption key is most likely to be left behind and least likely to be
    noticed — both classified failures are checked, not just the tidy one."""
    work = escrow_world["work"]
    if case == "wrong-key":
        wrong = _new_identity(tmp_path, "wrong2.key")
        d = FakeDownloader(escrow_world["objects"])
        with pytest.raises(EV.EscrowError):
            EV.run(bw=_cli(FakeBw(items=[_item(notes=wrong.read_text())])),
                   identity=wrong, item_name=ITEM, decrypt=True, prefix=PREFIX,
                   store=escrow_world["store"], work_dir=work, now=NOW,
                   downloader_factory=lambda: d)
    else:
        scope = escrow_world["store"] / "scope-delta"
        objects = dict(escrow_world["objects"])
        objects[KEY_DELTA_NEW] = _artifact(
            scope, escrow_world["identity"], tmp_path,
            mangle=_flip_middle_byte, tag="bad2")
        with pytest.raises(EV.EscrowError):
            _decrypt_run(escrow_world, objects=objects)
    assert not (work / EV.ESCROW_IDENTITY_FILENAME).exists()
    assert _work_contents(work) == [], _work_contents(work)


def test_no_key_material_survives_an_UNEXPECTED_exception(escrow_world, monkeypatch):
    """The `finally` must cover the paths nobody wrote a message for, too."""
    work = escrow_world["work"]
    RVmod = EV._rv()

    def explode(*a, **kw):
        raise ZeroDivisionError("synthetic, from inside the restore pipeline")

    monkeypatch.setattr(RVmod, "verify_artifact", explode)
    with pytest.raises(ZeroDivisionError):
        _decrypt_run(escrow_world)
    assert not (work / EV.ESCROW_IDENTITY_FILENAME).exists()
    assert _work_contents(work) == [], _work_contents(work)


def test_the_throwaway_identity_is_0600_inside_a_0700_dir_WHILE_IT_EXISTS(escrow_world,
                                                                         monkeypatch):
    """🔴 A POSITIVE CONTROL FOR THE SHRED TESTS. Every assertion above is that
    the file is GONE — which a script that never created it would also satisfy.
    This one observes it mid-flight, with the right content and the right mode,
    so "gone afterwards" is a claim about a file that really existed."""
    work = escrow_world["work"]
    seen = {}
    RVmod = EV._rv()
    real = RVmod.verify_artifact

    def spy(downloader, key, **kw):
        p = Path(kw["identity"])
        seen["path"] = p
        seen["mode"] = stat.S_IMODE(p.stat().st_mode)
        seen["dir_mode"] = stat.S_IMODE(p.parent.stat().st_mode)
        seen["bytes"] = p.read_bytes()
        return real(downloader, key, **kw)

    monkeypatch.setattr(RVmod, "verify_artifact", spy)
    _decrypt_run(escrow_world)
    assert seen["path"] == work / EV.ESCROW_IDENTITY_FILENAME
    assert seen["mode"] == 0o600, oct(seen["mode"])
    assert seen["dir_mode"] == 0o700, oct(seen["dir_mode"])
    # 🔴 THE ESCROWED BYTES, NOT THE ON-DISK FILE. Handing the pipeline the
    # local key would make the whole check a test of the wrong copy — and it
    # would pass just as green.
    assert seen["bytes"] == escrow_world["note"].encode("utf-8")
    assert not seen["path"].exists()


def test_a_PREEXISTING_loose_file_at_the_identity_path_does_not_keep_its_mode(
        escrow_world, monkeypatch):
    """🔴 `O_CREAT|O_TRUNC` APPLIES ITS MODE ONLY ON CREATE.

    Measured by an audit via `--work-dir`: a pre-existing 0666 file RECEIVED the
    escrowed key and STAYED 0666 for the whole time it held it. The 0700
    directory bounded the damage, but the module's own "0600" sentence was false
    on exactly the option that hands this a directory somebody else populated.
    """
    work = escrow_world["work"]
    victim = work / EV.ESCROW_IDENTITY_FILENAME
    victim.write_bytes(b"pre-existing decoy content")
    victim.chmod(0o666)
    assert stat.S_IMODE(victim.stat().st_mode) == 0o666

    seen = {}
    RVmod = EV._rv()
    real = RVmod.verify_artifact

    def spy(downloader, key, **kw):
        p = Path(kw["identity"])
        seen["mode"] = stat.S_IMODE(p.stat().st_mode)
        seen["bytes"] = p.read_bytes()
        return real(downloader, key, **kw)

    monkeypatch.setattr(RVmod, "verify_artifact", spy)
    _decrypt_run(escrow_world)
    assert seen["mode"] == 0o600, oct(seen["mode"])
    assert seen["bytes"] == escrow_world["note"].encode("utf-8")
    assert _work_contents(work) == []


def test_a_SYMLINK_at_the_identity_path_is_NOT_followed_and_its_target_survives(
        escrow_world, tmp_path):
    """🔴 THE WORST SHAPE THE AUDIT FOUND: a truncate-in-place primitive.

    With `O_CREAT|O_TRUNC` plus the old `_shred`, a symlink planted at the
    identity path got the escrowed key written THROUGH it to a 0644 file
    OUTSIDE the 0700 directory — and `_shred` then ZEROED that target while
    unlinking only the link, leaving a zero-filled decoy on any path the user
    can write.

    Both halves are asserted: the target keeps its original bytes and its
    original mode, and the link itself is gone.
    """
    work = escrow_world["work"]
    outside = tmp_path / "innocent-bystander.txt"
    outside.write_text("do not touch me\n", encoding="utf-8")
    outside.chmod(0o644)
    before = outside.read_bytes()

    link = work / EV.ESCROW_IDENTITY_FILENAME
    link.symlink_to(outside)
    assert link.is_symlink()

    v, _ = _decrypt_run(escrow_world)
    assert v.decrypt_checked is True
    assert outside.read_bytes() == before, "the symlink target was written through"
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644
    assert not link.exists() and not link.is_symlink()
    assert _work_contents(work) == []


def test_shred_unlinks_a_SYMLINK_without_touching_its_target(tmp_path):
    """The unit-level half of the case above. `open(path, "r+b")` follows a
    link; the link is the only thing `_shred` may destroy."""
    target = tmp_path / "target.txt"
    target.write_text("keep me\n", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target)
    EV._shred(link)
    assert not link.is_symlink() and not link.exists()
    assert target.read_text(encoding="utf-8") == "keep me\n"


def test_shred_does_NOT_follow_a_symlink_it_FAILED_to_unlink(tmp_path):
    """🔴 THE FALL-THROUGH, measured — a mutation sweep found it uncovered.

    The test above uses a link whose unlink SUCCEEDS, so the `except OSError`
    arm never runs. Restoring the old `pass`-and-fall-through survived the whole
    suite: on that path `open(path, "r+b")` FOLLOWS the link and zeroes the
    target, which is the truncate-in-place primitive the symlink branch exists
    to remove. Same lesson as the `O_NOFOLLOW` layer one test up — a layer
    nobody can observe failing is a layer nobody knows is gone.

    A read-only parent directory makes `unlink` raise EACCES while the link
    itself is still perfectly followable.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores directory write permission; the arm is "
                    "unreachable as root and a skip here is honest")
    target = tmp_path / "precious.txt"
    target.write_text("must survive\n", encoding="utf-8")
    holder = tmp_path / "ro"
    holder.mkdir()
    link = holder / "link"
    link.symlink_to(target)
    os.chmod(holder, 0o500)                     # r-x: unlink will fail
    try:
        # POSITIVE CONTROL: the unlink really is impossible here, so the
        # assertion below is about the arm under test and not about a link that
        # simply vanished.
        with pytest.raises(OSError):
            link.unlink()
        EV._shred(link)
        assert target.read_text(encoding="utf-8") == "must survive\n"
        assert link.is_symlink(), "the link should still be there, unfollowed"
    finally:
        os.chmod(holder, 0o700)


def test_create_private_file_REFUSES_to_follow_a_symlink_it_cannot_remove(tmp_path):
    """A directory in the way is the case `O_EXCL` must turn into an error
    rather than a silent reuse — `_shred` cannot unlink a directory."""
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    with pytest.raises(OSError):
        EV._create_private_file(blocked)


@pytest.mark.parametrize("plant", ["symlink", "regular-file"])
def test_the_OPEN_flags_defend_the_path_EVEN_IF_the_unlink_layer_fails(tmp_path,
                                                                       monkeypatch,
                                                                       plant):
    """🔴 MEASURE THE SECOND LAYER ON ITS OWN, or it is not defence in depth.

    `_create_private_file` has two independent defences: `_shred` removes
    whatever is at the path, and `O_EXCL|O_NOFOLLOW` refuses to create over or
    follow anything that is still there. A mutation sweep scored
    `O_EXCL|O_NOFOLLOW -> O_TRUNC` as SURVIVED — correctly, because the unlink
    always got there first, so the flags were never the thing being tested.
    A layer nobody can observe failing is a layer nobody knows is gone.

    So: neuter the unlink (that is the layer failing — a race, or a future
    regression in `_shred`) and watch the OPEN refuse. With `O_TRUNC` this test
    goes red, which is what makes the flags load-bearing rather than decorative.
    """
    monkeypatch.setattr(EV, "_shred", lambda p: None)
    outside = tmp_path / "target.txt"
    outside.write_text("original\n", encoding="utf-8")
    outside.chmod(0o644)
    victim = tmp_path / "victim"
    if plant == "symlink":
        victim.symlink_to(outside)
    else:
        victim.write_text("pre-existing\n", encoding="utf-8")
        victim.chmod(0o666)

    with pytest.raises(FileExistsError):
        EV._create_private_file(victim)

    # Nothing was written through, truncated, or re-moded.
    assert outside.read_text(encoding="utf-8") == "original\n"
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644
    if plant == "symlink":
        assert victim.is_symlink()
    else:
        assert victim.read_text(encoding="utf-8") == "pre-existing\n"


def test_the_fchmod_is_LOAD_BEARING_under_a_hostile_umask(tmp_path):
    """🔴 MAKE THE REDUNDANT LAYER OBSERVABLE, or drop it — same lesson as the
    `O_EXCL|O_NOFOLLOW` case one test up.

    `os.open(..., 0o600)` is masked by the umask, and no ordinary umask clears
    bits from 0600 — so deleting `os.fchmod(fd, _FILE_MODE)` survived all 120
    tests while a positive control (`fchmod(fd, 0o666)`) was killed, proving the
    assertions COULD see it and simply never exercised the case.

    A umask of 0o377 clears owner-WRITE (and all group/other bits) from the
    `os.open` mode: `0o600 & ~0o377 == 0o400`, i.e. the key file would be
    created owner-READ-ONLY. With the fchmod the mode is 0o600 regardless. The
    umask is restored in a `finally`; it is process-global.
    """
    p = tmp_path / "hostile.key"
    old = os.umask(0o377)
    try:
        fd = EV._create_private_file(p)
        with os.fdopen(fd, "wb") as fh:
            fh.write(b"synthetic-under-hostile-umask")
    finally:
        os.umask(old)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600, oct(
        stat.S_IMODE(p.stat().st_mode))
    assert p.read_bytes() == b"synthetic-under-hostile-umask"


def test_create_private_file_makes_a_fresh_0600_regular_file(tmp_path):
    """POSITIVE CONTROL: the helper must actually produce a usable fd, or every
    assertion above is about a function that only ever raises."""
    p = tmp_path / "fresh.key"
    fd = EV._create_private_file(p)
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"synthetic")
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert p.read_bytes() == b"synthetic"
    assert not p.is_symlink()


def test_shred_removes_the_file_and_never_raises_on_an_absent_one(tmp_path):
    p = tmp_path / "k"
    p.write_bytes(b"synthetic-shred-fixture-0123456789")
    EV._shred(p)
    assert not p.exists()
    EV._shred(p)          # idempotent; a cleanup that raises defeats its finally
    EV._shred(tmp_path / "never-existed")


# --------------------------------------------------------------------------- #
# 12b. 🔴 A SIGNAL MUST NOT SKIP THE SHRED
# --------------------------------------------------------------------------- #
def test_the_signal_handlers_cover_the_signals_a_TIMER_actually_sends():
    """Assert the SET, not that the installer was called.

    systemd's stop and timeout paths send SIGTERM, and `SECRETS.md` proposes
    timer-driven use — so a signal missing from this list is a path where a copy
    of the age key survives on disk."""
    installed = EV.install_signal_handlers()
    try:
        assert set(installed) == {"SIGTERM", "SIGINT", "SIGHUP"}
        assert list(EV._FATAL_SIGNALS) == ["SIGTERM", "SIGINT", "SIGHUP"]
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        signal.signal(signal.SIGHUP, signal.SIG_DFL)


def test_SIGTERM_unwinds_so_a_finally_RUNS_instead_of_being_skipped():
    """🔴 THE MECHANISM. Python's DEFAULT SIGTERM disposition terminates without
    unwinding, so every `finally` in the module is bypassed. Measured by an
    audit: rc 143 with `escrowed-identity.key` surviving at full size,
    byte-identical to the real identity.

    Here the signal is REAL — delivered to this process — and the assertion is
    that the `finally` ran and the exception is `SystemExit(143)`."""
    ran = []
    EV.install_signal_handlers()
    try:
        with pytest.raises(SystemExit) as ei:
            try:
                os.kill(os.getpid(), signal.SIGTERM)
                # The handler raises on the next bytecode boundary; give it one.
                for _ in range(1000):
                    pass
            finally:
                ran.append("finally")
        assert ran == ["finally"], "the finally block was skipped"
        assert ei.value.code == 128 + int(signal.SIGTERM) == 143
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        signal.signal(signal.SIGHUP, signal.SIG_DFL)


def test_a_REAL_SIGTERM_mid_pipeline_still_SHREDS_the_throwaway_identity(
        escrow_world):
    """🔴 THE END-TO-END CLAIM, with a real signal delivered at the exact moment
    the key exists on disk.

    The downloader signals this process from inside `get()` — which
    `verify_artifact` calls AFTER the throwaway identity has been written and
    BEFORE anything else. So the interrupt lands inside the window the shred
    exists for, and the identity is asserted gone afterwards.
    """
    work = escrow_world["work"]
    saw = {}

    class SignallingDownloader(FakeDownloader):
        def get(self, key):
            saw["identity_existed"] = (work / EV.ESCROW_IDENTITY_FILENAME).is_file()
            os.kill(os.getpid(), signal.SIGTERM)
            for _ in range(1000):
                pass
            return super().get(key)          # pragma: no cover - handler fires first

    d = SignallingDownloader(escrow_world["objects"])
    EV.install_signal_handlers()
    try:
        with pytest.raises(SystemExit) as ei:
            EV.run(bw=_cli(FakeBw(items=[_item(notes=escrow_world["note"])])),
                   identity=escrow_world["identity"], item_name=ITEM,
                   decrypt=True, prefix=PREFIX, store=escrow_world["store"],
                   work_dir=work, now=NOW, downloader_factory=lambda: d)
        assert ei.value.code == 143
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        signal.signal(signal.SIGHUP, signal.SIG_DFL)

    # 🔴 POSITIVE CONTROL: the key really was on disk when the signal landed, so
    # "it is gone now" is a claim about a file that existed.
    assert saw["identity_existed"] is True
    assert not (work / EV.ESCROW_IDENTITY_FILENAME).exists()
    assert _work_contents(work) == []


def test_main_installs_the_handlers_BEFORE_doing_any_work(monkeypatch, tmp_path):
    """The wiring, separately from the mechanism: a handler that is never
    installed protects nothing, and `main()` is the only installer."""
    order = []
    monkeypatch.setattr(EV, "install_signal_handlers",
                        lambda: order.append("installed") or ["SIGTERM"])
    monkeypatch.setattr(EV, "read_identity",
                        lambda p: order.append("work") or b"x")
    ident = _identity(tmp_path, ESCROW_NOTE, name="sig.key")
    EV.main(["--identity", str(ident), "--bw", str(tmp_path / "no-such-bw")])
    assert order[0] == "installed", order


# --------------------------------------------------------------------------- #
# 13. the CLI — real argv, real exit codes, a real `bw` process
# --------------------------------------------------------------------------- #
BW_STUB_BODY = '''
"""A synthetic `bw`. Answers from a JSON plan; never touches a real vault.

Deliberately carries NO shebang of its own — see `_bw_stub` below.
"""
import json, os, sys
plan = json.load(open(os.environ["FAKE_BW_PLAN"]))
args = [a for a in sys.argv[1:] if a != "--nointeraction"]
key = " ".join(args[:2])
if key not in plan:
    sys.stderr.write("unmodelled: %r\\n" % (args,))
    sys.exit(99)
entry = plan[key]
sys.stdout.write(entry["out"])
sys.exit(entry["rc"])
'''


def _bw_stub(tmp_path: Path, plan: dict) -> tuple[Path, Path]:
    """An executable synthetic `bw` on disk, plus the JSON plan it answers from.

    🔴 THE SHEBANG IS `testlib.mockbin`'s, NOT THIS FILE'S. A stub written at
    runtime with `#!/usr/bin/env python3` execs fine on the NixOS dev host and
    ENOENTs inside the nix build sandbox, which is the AUTHORITATIVE tier — the
    two-tier hazard, and the reason `mockbin` exists at all rather than a sixth
    hand-rolled copy of the rule. So the Python body is a plain `.py` file with
    no shebang, and the executable is a POSIX-sh shim that execs the RUNNING
    interpreter by absolute path.
    """
    body = tmp_path / "bw_stub_body.py"
    body.write_text(BW_STUB_BODY, encoding="utf-8")
    stub = tmp_path / "bw-stub" / "bw"
    stub.parent.mkdir(parents=True, exist_ok=True)
    mockbin.write_exec(
        stub,
        f"exec {shlex.quote(sys.executable)} {shlex.quote(str(body))} \"$@\"\n")
    planfile = tmp_path / "bw-plan.json"
    planfile.write_text(json.dumps(plan), encoding="utf-8")
    return stub, planfile


def test_the_bw_STUB_really_execs_and_can_REFUSE(tmp_path):
    """POSITIVE + NEGATIVE CONTROL for the stub every CLI test below runs.

    A stub that cannot exec would make each CLI assertion a statement about a
    process that never started — and in the nix sandbox that is exactly how a
    `/usr/bin/env` shebang fails. So: it runs, it answers a modelled command,
    and it exits 99 on one it does not model instead of inventing a reply."""
    stub, planfile = _bw_stub(tmp_path, _plan(items=[_item()]))
    env = dict(os.environ)
    env["FAKE_BW_PLAN"] = str(planfile)
    good = subprocess.run([str(stub), "--nointeraction", "status"],
                          capture_output=True, text=True, env=env)
    assert good.returncode == 0, good.stderr
    assert json.loads(good.stdout)["status"] == "unlocked"
    bad = subprocess.run([str(stub), "--nointeraction", "sync"],
                         capture_output=True, text=True, env=env)
    assert bad.returncode == 99, (bad.returncode, bad.stdout, bad.stderr)


def _plan(*, status: str = "unlocked", items: list | None = None,
          server: str = SERVER) -> dict:
    return {
        "status": {"rc": 0, "out": json.dumps(
            {"serverUrl": server, "status": status})},
        "config server": {"rc": 0, "out": server + "\n"},
        "list items": {"rc": 0, "out": json.dumps(items or [])},
    }


def _run_cli(tmp_path: Path, plan: dict, *extra: str,
             identity_text: str = ESCROW_NOTE) -> subprocess.CompletedProcess:
    stub, planfile = _bw_stub(tmp_path, plan)
    ident = _identity(tmp_path, identity_text, name="cli-on-disk.key")
    env = dict(os.environ)
    env["FAKE_BW_PLAN"] = str(planfile)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--bw", str(stub),
         "--identity", str(ident), "--item-name", ITEM, *extra],
        capture_output=True, text=True, env=env)


def test_the_CLI_exits_ZERO_on_an_identical_escrow(tmp_path):
    p = _run_cli(tmp_path, _plan(items=[_item()]))
    assert p.returncode == 0, p.stderr
    assert "escrow OK" in p.stdout
    assert str(ESCROW_NOTE_BYTES) in p.stdout


@pytest.mark.parametrize("plan_kw,extra,token,code", [
    ({"items": []}, (), "ITEM-NOT-FOUND", 17),
    ({"items": [_item(id_="a"), _item(id_="b")]}, (), "ITEM-AMBIGUOUS", 18),
    ({"items": [_item(notes="")]}, (), "NOTE-EMPTY", 20),
    ({"items": [_item(notes=OTHER_KEY)]}, (), "BYTES-DIFFER-MATERIALLY", 22),
    ({"items": [_item(notes=ESCROW_NOTE.rstrip("\n"))]}, (),
     "BYTES-DIFFER-TRAILING-NEWLINE", 21),
    ({"status": "locked"}, (), "VAULT-LOCKED", 12),
    ({"status": "unauthenticated"}, (), "VAULT-UNAUTHENTICATED", 13),
    ({"items": [_item()]}, ("--expect-server", "https://elsewhere.invalid"),
     "SERVER-MISMATCH", 16),
])
def test_the_CLI_exit_code_and_token_are_DISTINCT_per_failure(tmp_path, plan_kw,
                                                              extra, token, code):
    """🔴 THE DELIVERABLE, END TO END. Each cause gets its OWN exit code out of
    the real process — an operator's timer reads the code, not the prose."""
    p = _run_cli(tmp_path, _plan(**plan_kw), *extra)
    assert p.returncode == code, (p.returncode, p.stdout, p.stderr)
    assert token in p.stderr
    assert p.stdout == "", "a failing run must print no verdict on stdout"


def test_the_CLI_never_prints_key_material_on_a_mismatch(tmp_path):
    p = _run_cli(tmp_path, _plan(items=[_item(notes=OTHER_KEY)]))
    assert p.returncode == 22
    blob = p.stdout + p.stderr
    assert "AGE-SECRET-KEY" not in blob
    for line in set(ESCROW_NOTE.splitlines()) | set(OTHER_KEY.splitlines()):
        if len(line) > 20:
            assert line not in blob


def test_the_CLI_reports_a_MISSING_bw_with_the_nix_shell_command(tmp_path):
    ident = _identity(tmp_path, ESCROW_NOTE, name="cli2.key")
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "--bw", str(tmp_path / "no-such-bw"),
         "--identity", str(ident), "--item-name", ITEM],
        capture_output=True, text=True, env=dict(os.environ))
    assert p.returncode == 10
    assert "BW-MISSING" in p.stderr
    assert "nix-shell -p bitwarden-cli jq" in p.stderr


def test_the_CLI_work_dir_branch_HARDENS_a_directory_it_did_not_create(tmp_path):
    """🔴 THE ONLY BRANCH THAT RECEIVES A NON-FRESH DIRECTORY, and no test
    reached it: every other test passes `work_dir` as a library kwarg, so an
    audit's mutant dropping `B._private_dir(...)` from this branch SURVIVED the
    whole suite. It is also exactly where the mode hazards above bite.

    A pre-existing world-readable directory must come out 0700, and must be
    EMPTY afterwards — but still EXIST, because it is the operator's.
    """
    wd = tmp_path / "operator-work-dir"
    wd.mkdir(mode=0o777)
    os.chmod(wd, 0o777)
    assert stat.S_IMODE(wd.stat().st_mode) == 0o777

    empty_src = tmp_path / "cli-wd-empty"
    empty_src.mkdir()
    p = _run_cli(tmp_path, _plan(items=[_item()]),
                 "--decrypt-check", "--from-dir", str(empty_src),
                 "--host", HOST, "--store", str(tmp_path / "cli-wd-no-store"),
                 "--work-dir", str(wd))
    # It got far enough to build the work dir, then refused for want of an
    # artifact — the refusal is what proves the branch ran at all.
    assert p.returncode == EV.EXIT_CODES["NO-ARTIFACT"], (p.stdout, p.stderr)
    assert stat.S_IMODE(wd.stat().st_mode) == 0o700, oct(
        stat.S_IMODE(wd.stat().st_mode))
    assert wd.is_dir(), "the operator's own directory must not be deleted"
    assert sorted(x.name for x in wd.iterdir()) == []


def test_the_CLI_leaves_NO_key_material_in_an_operator_supplied_work_dir(tmp_path):
    """The same branch on a run that actually writes the throwaway identity."""
    wd = tmp_path / "wd2"
    wd.mkdir()
    fetched = tmp_path / "wd2-fetched"
    fetched.mkdir()
    # A well-formed prefix with a single unreadable (zero-byte) object: the run
    # reaches the identity write, then fails.
    obj = fetched / HOST / "scope-omega" / "20260309T164205Z.bundle.age"
    obj.parent.mkdir(parents=True)
    obj.write_bytes(b"")
    p = _run_cli(tmp_path, _plan(items=[_item()]),
                 "--decrypt-check", "--from-dir", str(fetched),
                 "--host", HOST, "--store", str(tmp_path / "wd2-no-store"),
                 "--work-dir", str(wd))
    assert p.returncode == EV.EXIT_CODES["ARTIFACT-UNREADABLE"], (p.stdout, p.stderr)
    assert sorted(x.name for x in wd.iterdir()) == []
    assert "AGE-SECRET-KEY" not in (p.stdout + p.stderr)


def test_print_plan_runs_NO_bw_and_reads_NO_key(tmp_path):
    """A no-network, no-vault surface. The stub would exit 99 on any command it
    was given, so a plan that shelled out could not exit 0."""
    stub, planfile = _bw_stub(tmp_path, {})
    ident = _identity(tmp_path, ESCROW_NOTE, name="plan.key")
    env = dict(os.environ)
    env["FAKE_BW_PLAN"] = str(planfile)
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "--print-plan", "--bw", str(stub),
         "--identity", str(ident), "--item-name", ITEM],
        capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    assert str(ESCROW_NOTE_BYTES) in p.stdout      # it measured the local file
    assert "AGE-SECRET-KEY" not in p.stdout        # without reading its content
    # Every classified cause is listed, so the operator can map a code by eye.
    for token in EV.EXIT_CODES:
        assert token in p.stdout


def test_print_plan_names_all_three_classifications(tmp_path):
    ident = _identity(tmp_path, ESCROW_NOTE, name="plan2.key")
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "--print-plan", "--identity", str(ident)],
        capture_output=True, text=True, env=dict(os.environ))
    assert p.returncode == 0, p.stderr
    for c in (EV.CLASS_IDENTICAL, EV.CLASS_TRAILING_NEWLINE, EV.CLASS_MATERIAL):
        assert c in p.stdout


# --------------------------------------------------------------------------- #
# 14. the seam with restore-verify.py — imported, never reimplemented
# --------------------------------------------------------------------------- #
def test_the_escrow_verifier_REUSES_the_restore_pipeline():
    """🔴 A SEAM GUARD: it pins the RELATIONSHIP, not one side.

    If someone reimplements the download -> decrypt -> clone -> fsck pipeline
    here, they also reimplement the `finally` that unlinks the plaintext bundle
    — and the copy nobody exercises is the broken one. So: the module resolves
    to the real restore verifier, and this file does not contain a second
    `git clone --mirror` or a second `age --decrypt`.
    """
    mod = EV._rv()
    assert mod is RV or mod.__file__ == str(RESTORE_SCRIPT)
    assert hasattr(mod, "verify_artifact")
    src = SCRIPT.read_text(encoding="utf-8")
    # The ARGV, not the word: `--mirror` and `--decrypt-check` both appear in
    # prose here, and a guard that could not tell a docstring from a call site
    # would go red for a comment and green for a real second implementation.
    assert '"clone", "--mirror"' not in src, "the restore was reimplemented here"
    assert '"age", "--decrypt"' not in src, "the age call was reimplemented here"
    assert "subprocess.run" not in src.split("def _default_runner", 1)[1].split(
        "class BitwardenCLI", 1)[1], "a second subprocess call outside the bw seam"
    assert "RV.verify_artifact(" in src


def test_the_module_hardcodes_no_endpoint_and_no_host():
    """🔴 devrc is PUBLIC. The server is read from `bw config server` at run
    time; nothing that looks like an endpoint may be committed here."""
    src = SCRIPT.read_text(encoding="utf-8")
    for needle in ("http://", "https://"):
        assert needle not in src, f"{needle!r} appears in a public repo's source"
    assert "bw config server" in src or "\"config\", \"server\"" in src


# --------------------------------------------------------------------------- #
# 15. 🔴 THE ADVERTISED SHELL, AND WHICH FILE WAS ACTUALLY COMPARED
#
# Regressions from a live run on 2026-08-25 in which the operator typed the
# master password and got a confident wrong answer, and from the adversarial
# audit of the first attempt to fix it — which shipped a NEW confident wrong
# sentence in the very line added to prevent one:
#
#   * the hint advertised `nix-shell -p bitwarden-cli jq`, and --decrypt-check
#     then died `ModuleNotFoundError: No module named 'minio'` AFTER the
#     password had been spent.
#   * `SOPS_AGE_KEY_FILE`, exported by unrelated work, redirected the on-disk
#     side to a DIFFERENT age key. Every age identity file is the same size, so
#     the mismatch reported equal byte counts and read as a damaged escrow. Its
#     remedy would have overwritten a good escrow with an unrelated key.
#   * the FIRST FIX then branched on WHAT NAMED the file instead of WHICH FILE
#     it was, so `--identity <the default>` (the documented command!) and the
#     deployed unit's own `SOPS_AGE_KEY_FILE=<the default>` both printed
#     "NOT the default" — and told the reader an `--identity` FLAG could be
#     "set by an unrelated shell".
#
# 🔴 THE SENTENCES ARE PINNED WHOLE. A guard asserting the fragment "NOT the
# default" passed unchanged on that false line. When the artifact under test is
# prose, a substring cannot tell a true message from a confident wrong one.
# --------------------------------------------------------------------------- #
def test_the_advertised_nix_shell_provisions_every_module_the_decrypt_path_IMPORTS():
    """🔴 The RELATIONSHIP, not either side alone: every module the decrypt path
    needs must be provisioned by the shell the failure messages hand over.

    Scope, stated so nobody reads more coverage into this than it has: it pins
    LEDGER -> HINT. It cannot DISCOVER a newly-added third-party import; the
    companion test below ties the ledger to the import site that creates the
    need. Neither covers NON-Python tools (`age`, `kubectl`, `git`) — those
    resolve from the ambient profile because `nix-shell -p` is impure, and
    `--pure` or a fresh host would break them with no guard watching.
    """
    assert EV.DECRYPT_PYTHON_MODULES, "an empty ledger would pass vacuously"
    for mod in EV.DECRYPT_PYTHON_MODULES:
        assert any(f"p.{mod}" in pkg for pkg in EV.NIX_SHELL_PACKAGES), (
            f"{mod!r} is imported by --decrypt-check but no advertised package "
            f"provides it: {EV.NIX_SHELL_PACKAGES}")
        assert f"p.{mod}" in EV.NIX_SHELL_HINT, (
            f"{mod!r} is in the ledger but the RENDERED hint omits it — the "
            f"hint is what the operator actually copies")


def test_the_decrypt_module_ledger_names_the_REAL_third_party_import_site():
    """🔴 THE SITE THAT CREATES THE NEED, not the local shim that re-exports it.

    The first revision asserted `from _minio import MinioArchive` in backup.py.
    `_minio` is devrc's OWN module (`scripts/mail-actions/_minio.py`); the
    third-party dependency is the `from minio import Minio` INSIDE it. Pinning
    the shim meant swapping that module to another SDK would leave both tests
    green while the advertised shell was wrong again — the same hazard in a
    different shape.
    """
    shim = SCRIPT.parent.parent / "mail-actions" / "_minio.py"
    assert shim.is_file(), f"the shim moved: {shim}"
    assert "from minio import Minio" in shim.read_text(encoding="utf-8"), (
        "the third-party minio import moved — re-derive DECRYPT_PYTHON_MODULES "
        "from wherever it lives now")
    assert "minio" in EV.DECRYPT_PYTHON_MODULES
    # and backup.py must still route through that shim, or the chain is broken
    assert "from _minio import MinioArchive" in Path(B.__file__).read_text(
        encoding="utf-8")


def test_the_rendered_hint_is_accepted_by_a_REAL_SHELL():
    """🔴 THE NEGATIVE CONTROL THAT THE FIRST REVISION LACKED.

    That version asserted the quoting via `shlex.split`, which does NOT treat
    `(`/`)`/`[`/`]` as metacharacters — so deleting the quoting entirely left
    the suite fully green while the advertised command became a bash syntax
    error. `bash -n` is the instrument that can actually see it.

    Both halves are asserted: the real hint PARSES, and an unquoted rendering
    of the same packages does NOT. Without the second half this test could pass
    against a `bash -n` that accepts anything.
    """
    if shutil.which("bash") is None:
        pytest.fail("bash is required to validate the hint this module hands out")
    cmd = EV.NIX_SHELL_HINT.replace("<command>", "true")
    ok = subprocess.run(["bash", "-n", "-c", cmd], capture_output=True, text=True)
    assert ok.returncode == 0, (
        f"the advertised hint is not valid shell: {ok.stderr.strip()}")

    unquoted = ("nix-shell -p " + " ".join(EV.NIX_SHELL_PACKAGES)
                + " --run 'true'")
    bad = subprocess.run(["bash", "-n", "-c", unquoted], capture_output=True,
                         text=True)
    assert bad.returncode != 0, (
        "the positive control did not fire: an UNQUOTED package list was "
        "accepted by `bash -n`, so this test cannot see missing quoting")


def test_the_hint_carries_its_run_clause_and_quotes_only_what_needs_it():
    """The `--run '<command>'` half is what makes the hint copy-pasteable;
    deleting it survived the first revision's guards."""
    assert EV.NIX_SHELL_HINT.endswith(" --run '<command>'")
    assert EV.NIX_SHELL_HINT.startswith("nix-shell -p ")
    assert "'python3.withPackages(p:[p.minio])'" in EV.NIX_SHELL_HINT
    assert "'bitwarden-cli'" not in EV.NIX_SHELL_HINT, "a bare name was quoted"


# --------------------------------------------------------------------------- #
# 15b. resolution order + the SAME-FILE predicate
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("env,expect_path,expect_source", [
    ({}, None, B.IDENTITY_SOURCE_DEFAULT),
    ({"SOPS_AGE_KEY_FILE": "/srv/sops-chosen.key"},
     "/srv/sops-chosen.key", "$SOPS_AGE_KEY_FILE"),
    ({"ASIB_AGE_IDENTITY": "/srv/asib-chosen.key"},
     "/srv/asib-chosen.key", "$ASIB_AGE_IDENTITY"),
    ({"ASIB_AGE_IDENTITY": "/srv/asib-chosen.key",
      "SOPS_AGE_KEY_FILE": "/srv/sops-chosen.key"},
     "/srv/asib-chosen.key", "$ASIB_AGE_IDENTITY"),
    # an EMPTY env var is not a choice — it must fall through, not resolve to
    # Path(""). `if v:` vs `if v is not None:` is invisible without this row.
    ({"SOPS_AGE_KEY_FILE": ""}, None, B.IDENTITY_SOURCE_DEFAULT),
    ({"ASIB_AGE_IDENTITY": "", "SOPS_AGE_KEY_FILE": "/srv/sops-chosen.key"},
     "/srv/sops-chosen.key", "$SOPS_AGE_KEY_FILE"),
])
def test_resolve_identity_with_source_NAMES_what_chose_the_path(
        monkeypatch, env, expect_path, expect_source):
    """The three fixture paths are pairwise distinct AND distinct from
    DEFAULT_IDENTITY, so a mutant returning any constant is visible."""
    for var in B.IDENTITY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    path, source = B.resolve_identity_with_source()
    expected = B.DEFAULT_IDENTITY if expect_path is None else Path(expect_path)
    assert path == expected
    assert source == expect_source
    assert B.resolve_identity() == path


def test_the_env_var_ORDER_is_declared_once_and_the_sentinels_are_PINNED():
    """`resolve_identity()` delegates rather than repeating the loop: two copies
    of a precedence order drift silently, because both return a plausible path.

    The two source sentinels are pinned as WHOLE strings — they are printed to
    the operator, and `provenance_clauses` BRANCHES on the flag one.
    """
    assert B.IDENTITY_ENV_VARS == ("ASIB_AGE_IDENTITY", "SOPS_AGE_KEY_FILE")
    src = Path(B.__file__).read_text(encoding="utf-8")
    body = src.split("def resolve_identity(", 1)[1].split("\ndef ", 1)[0]
    assert "resolve_identity_with_source" in body, (
        "resolve_identity() re-implements the order instead of delegating")
    assert "os.environ" not in body, "a SECOND copy of the env order"
    assert B.IDENTITY_SOURCE_DEFAULT == "the built-in default"
    assert EV.IDENTITY_SOURCE_FLAG == "the --identity flag"


def test_same_identity_file_asks_about_the_FILE_not_the_spelling(tmp_path):
    """🔴 THE PREDICATE THE WHOLE FEATURE TURNS ON.

    Equal-but-differently-spelled paths are the SAME file (that is the deployed
    config: the unit exports SOPS_AGE_KEY_FILE=<the default>). A symlink to it
    is also the same file. Two genuinely different files are not.
    """
    real = tmp_path / "real.key"
    real.write_text("an identity file's contents", encoding="utf-8")
    other = tmp_path / "other.key"
    other.write_text("different", encoding="utf-8")
    link = tmp_path / "link.key"
    link.symlink_to(real)

    assert B.same_identity_file(real, real)
    assert B.same_identity_file(real, Path(str(tmp_path) + "/./real.key"))
    assert B.same_identity_file(real, link), "a symlink to the file is the file"
    assert not B.same_identity_file(real, other)
    # `~` must expand: argparse does NOT expanduser, so a quoted
    # `--identity '~/...'` arrives literally and would otherwise never match.
    home = Path.home()
    # BOTH sides — dropping `.expanduser()` from `b` alone survived a guard
    # that only ever put `~` in position `a`. The NUL assertions below are
    # symmetric; this one was not, and the asymmetry was an oversight.
    assert B.same_identity_file(Path("~"), home)
    assert B.same_identity_file(home, Path("~"))
    assert not B.same_identity_file(Path("~"), home / "definitely-not-here")
    assert not B.same_identity_file(home / "definitely-not-here", Path("~"))
    # 🔴 THE EXCEPTION BELT, MADE REACHABLE. An embedded NUL is the one input
    # that actually raises out of `resolve()` (ValueError, measured — symlink
    # loops, deep chains and unreadable parents all return normally on 3.12).
    # Without this the fallback was dead code and mutating it to the UNSAFE
    # `return True` — which would silence the warning for every path — survived
    # the whole suite.
    # 🔴 THE PRECONDITION, ASSERTED. Without this the pair below pins only the
    # OUTCOME: on a runtime whose `resolve()` stopped raising, the belt would
    # silently become dead code again and the unsafe `return True` mutant would
    # pass. Measured raising on 3.12 and 3.13; this says so the day it changes.
    with pytest.raises(ValueError):
        Path("a\x00b").expanduser().resolve()
    assert not B.same_identity_file(Path("a\x00b"), real)
    assert not B.same_identity_file(real, Path("a\x00b"))
    # a path that does not exist must compare, not raise
    assert not B.same_identity_file(real, tmp_path / "absent.key")
    assert B.same_identity_file(tmp_path / "absent.key", tmp_path / "absent.key")


# --------------------------------------------------------------------------- #
# 15c. 🔴 THE MESSAGES — pinned WHOLE, and the warning must DISCRIMINATE
# --------------------------------------------------------------------------- #
ELSEWHERE = Path("/srv/not-the-default.key")


def test_the_default_FILE_never_triggers_a_warning_however_it_was_NAMED():
    """🔴 THE AUDIT'S BLOCKING FINDING, both halves.

    `--identity <the default>` is the command the handoff doc RECOMMENDS, and
    `SOPS_AGE_KEY_FILE=<the default>` is what the deployed backup unit sets
    (nix/home.nix). Branching on the mechanism made BOTH print "NOT the
    default" — the second being a permanently-red warning on the subsystem's
    own normal configuration, which trains a reader to skip it.
    """
    for source in (EV.IDENTITY_SOURCE_FLAG, "$SOPS_AGE_KEY_FILE",
                   "$ASIB_AGE_IDENTITY", B.IDENTITY_SOURCE_DEFAULT):
        chose, redirect = EV.provenance_clauses(B.DEFAULT_IDENTITY, source)
        assert redirect == "", f"a warning fired on the DEFAULT file via {source}"
        assert chose == (f" The on-disk path is the default identity, named by "
                         f"{source}.")
        assert "NOT the default" not in chose


def test_an_UNSTATED_source_asserts_NOTHING_about_provenance():
    """The honest branch: a caller that states no source must not have one
    invented for it — neither for the default nor against it."""
    for path in (B.DEFAULT_IDENTITY, ELSEWHERE):
        assert EV.provenance_clauses(path, None) == ("", "")


def test_an_ENV_REDIRECT_to_a_DIFFERENT_file_refuses_to_call_it_diagnosed():
    chose, redirect = EV.provenance_clauses(ELSEWHERE, "$SOPS_AGE_KEY_FILE")
    assert chose == (" The on-disk path is NOT the default identity; it was "
                     "chosen by $SOPS_AGE_KEY_FILE.")
    assert redirect.startswith(" 🔴 READ THIS BEFORE RE-ESCROWING: "
                               "$SOPS_AGE_KEY_FILE redirected the on-disk path")
    assert "WRONG FILE" in redirect
    assert "same size" in redirect
    # it must hand over a command that actually UNDOES the redirect...
    assert "env -u SOPS_AGE_KEY_FILE" in redirect
    # ...and must NOT tell them to re-run with the thing they already did
    assert "--identity" not in redirect


def test_an_EXPLICIT_FLAG_to_a_DIFFERENT_file_does_not_blame_a_shell():
    """🔴 A flag is a deliberate act. The first revision told the operator an
    `--identity` flag could be 'set by an unrelated shell', and advised them to
    'Re-run with --identity <default>' — which, for someone who had just passed
    --identity, is advice to repeat what they just did."""
    chose, redirect = EV.provenance_clauses(ELSEWHERE, EV.IDENTITY_SOURCE_FLAG)
    assert chose == (" The on-disk path is NOT the default identity; it was "
                     "chosen by the --identity flag.")
    assert "unrelated shell" not in redirect
    assert "redirected" not in redirect
    assert "you pointed --identity at a file" in redirect
    assert str(B.DEFAULT_IDENTITY) in redirect
    assert "do NOT re-escrow" in redirect


def _mismatch_verdict(escrow_world, source, identity=None):
    with pytest.raises(EV.EscrowError) as ei:
        EV.run(bw=_cli(FakeBw(items=[_item(notes=OTHER_KEY)])),
               identity=identity or escrow_world["identity"], item_name=ITEM,
               identity_source=source, store=escrow_world["store"], now=NOW)
    assert ei.value.token == "BYTES-DIFFER-MATERIALLY"
    return ei.value.verdict


def test_the_refusal_comes_BEFORE_the_remedy_it_argues_against(escrow_world):
    """🔴 ORDER, not mere presence. The first revision appended the warning
    AFTER 'Re-escrow from the on-disk identity…', so the reader met the
    dangerous instruction first. A substring test could not see that, and the
    PR body claimed the opposite of what the code did."""
    msg = _mismatch_verdict(escrow_world, "$SOPS_AGE_KEY_FILE")
    warn = msg.index("READ THIS BEFORE RE-ESCROWING")
    remedy = msg.index("Re-escrow from the on-disk identity")
    assert warn < remedy, "the warning trails the remedy it argues against"


def test_a_mismatch_on_the_DEFAULT_file_carries_no_warning(escrow_world,
                                                            monkeypatch):
    """🔴 HERMETIC — the default is MONKEYPATCHED onto the fixture's own key.

    The first version passed the REAL `B.DEFAULT_IDENTITY` into `run()`, which
    READS that file. It therefore passed only on a machine holding the
    operator's age key; anywhere else `run()` raised IDENTITY-MISSING before
    reaching the comparison, and it turned the merge-gating nix sandbox tier
    RED while the dev-host tier stayed green. Reproduce any such test with
    `env HOME=<empty dir>` — that is the whole difference.
    """
    monkeypatch.setattr(B, "DEFAULT_IDENTITY", escrow_world["identity"])
    msg = _mismatch_verdict(escrow_world, B.IDENTITY_SOURCE_DEFAULT)
    assert "READ THIS BEFORE RE-ESCROWING" not in msg
    assert "restore-verify.py" in msg


def test_the_TRAILING_NEWLINE_arm_also_states_which_file_it_compared(
        escrow_world, tmp_path):
    """That arm gained the provenance clause but had ZERO coverage — dropping
    it there survived the first revision's suite."""
    ident = tmp_path / "trailing.key"
    ident.write_text(escrow_world["identity"].read_text(encoding="utf-8"),
                     encoding="utf-8")
    note = ident.read_text(encoding="utf-8").rstrip("\n")
    with pytest.raises(EV.EscrowError) as ei:
        EV.run(bw=_cli(FakeBw(items=[_item(notes=note)])), identity=ident,
               item_name=ITEM, identity_source="$SOPS_AGE_KEY_FILE",
               store=escrow_world["store"], now=NOW)
    assert ei.value.token == "BYTES-DIFFER-TRAILING-NEWLINE"
    assert "is NOT the default identity" in ei.value.verdict


def test_run_and_print_plan_DEFAULT_to_claiming_nothing():
    """🔴 Both defaults were mutable to the DEFAULT sentinel invisibly, because
    every test passes the argument explicitly. That mutation would invent a
    provenance claim for callers that never made one."""
    import inspect
    for fn in (EV.run, EV.print_plan):
        assert inspect.signature(fn).parameters["identity_source"].default is None


# --------------------------------------------------------------------------- #
# 15d. main()'s OWN derivation — the suite is otherwise hermetic here
# --------------------------------------------------------------------------- #
def _plan_out(capsys, argv):
    """Run `--print-plan` in-process and return stdout.

    NOT named `_plan` — this module already has a `_plan()` that builds a fake
    `bw` script's behaviour plan, and shadowing it broke 14 CLI tests.
    """
    assert EV.main(["--print-plan", *argv]) == EV.EXIT_OK
    return capsys.readouterr().out


def test_print_plan_flags_a_REDIRECT_to_a_different_file(monkeypatch, capsys,
                                                         tmp_path):
    key = tmp_path / "redirected.key"
    key.write_text(OTHER_KEY, encoding="utf-8")
    for var in B.IDENTITY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(key))
    out = _plan_out(capsys, [])
    assert f"chosen by: $SOPS_AGE_KEY_FILE{EV.NON_DEFAULT_NOTE}" in out
    assert str(key) in out


@pytest.mark.parametrize("argv,env,expect_source", [
    ([], {}, B.IDENTITY_SOURCE_DEFAULT),
    # 🔴 the deployed configuration: the unit exports the DEFAULT path
    ([], {"SOPS_AGE_KEY_FILE": None}, "$SOPS_AGE_KEY_FILE"),
    # 🔴 the command the handoff doc recommends
    (["--identity", None], {}, EV.IDENTITY_SOURCE_FLAG),
])
def test_print_plan_is_SILENT_when_the_file_is_the_default(
        monkeypatch, capsys, argv, env, expect_source):
    """🔴 Both of the audit's blocking reproductions, as tests. `None` in the
    fixtures is substituted with DEFAULT_IDENTITY so the row cannot drift from
    the constant it is about."""
    for var in B.IDENTITY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    for k in env:
        monkeypatch.setenv(k, str(B.DEFAULT_IDENTITY))
    argv = [str(B.DEFAULT_IDENTITY) if a is None else a for a in argv]
    out = _plan_out(capsys, argv)
    assert f"chosen by: {expect_source}" in out
    assert EV.NON_DEFAULT_NOTE not in out, (
        "a warning fired for the DEFAULT identity file")
    assert "NOT the default" not in out


def test_an_explicit_flag_WINS_over_a_set_env_var(monkeypatch, capsys, tmp_path):
    key = tmp_path / "explicit.key"
    key.write_text(OTHER_KEY, encoding="utf-8")
    monkeypatch.setenv("SOPS_AGE_KEY_FILE", "/srv/should-have-been-overridden.key")
    out = _plan_out(capsys, ["--identity", str(key)])
    assert f"chosen by: {EV.IDENTITY_SOURCE_FLAG}" in out
    assert "$SOPS_AGE_KEY_FILE" not in out
    assert "should-have-been-overridden" not in out
    assert str(key) in out


def test_the_non_default_note_is_pinned_WHOLE():
    """🔴 The first revision asserted only the fragment 'NOT the default', and
    that fragment was present in a sentence claiming a command-line flag could
    be set by an unrelated shell. Pin the whole string."""
    assert EV.NON_DEFAULT_NOTE == "  <- NOT the default identity"
    assert "shell" not in EV.NON_DEFAULT_NOTE


def test_the_SUCCESS_line_discloses_a_non_default_identity(escrow_world):
    """🔴 'escrow OK — the Secure Note matches <path>' is where an undisclosed
    comparison against the wrong file is LEAST likely to be questioned."""
    v = EV.EscrowVerdict(
        item_name=ITEM, server="s", server_pinned=True, escrow_bytes=1,
        disk_bytes=1, classification=EV.CLASS_IDENTICAL, identity=ELSEWHERE,
        identity_source="$SOPS_AGE_KEY_FILE")
    assert "NOT the default identity" in v.line()
    assert "$SOPS_AGE_KEY_FILE" in v.line()

    d = EV.EscrowVerdict(
        item_name=ITEM, server="s", server_pinned=True, escrow_bytes=1,
        disk_bytes=1, classification=EV.CLASS_IDENTICAL,
        identity=B.DEFAULT_IDENTITY, identity_source="$SOPS_AGE_KEY_FILE")
    assert "NOT the default identity" not in d.line()



def test_the_SUCCESS_line_from_a_REAL_run_discloses_a_non_default_identity(
        escrow_world, monkeypatch):
    """🔴 THE SEAM, not the dataclass. The sibling test constructs
    `EscrowVerdict` directly, so deleting `identity_source=identity_source`
    from `run()`'s construction left the whole disclosure INERT on the only
    path that produces it, with the suite fully green."""
    monkeypatch.setattr(B, "DEFAULT_IDENTITY", Path("/srv/not-this-one.key"))
    v = EV.run(bw=_cli(FakeBw(items=[_item(notes=escrow_world["note"])])),
               identity=escrow_world["identity"], item_name=ITEM,
               identity_source="$SOPS_AGE_KEY_FILE",
               store=escrow_world["store"], now=NOW)
    line = v.line()
    assert "escrow OK" in line
    assert "NOT the default identity" in line
    assert "$SOPS_AGE_KEY_FILE" in line


def test_no_source_renders_a_SELF_CONTRADICTING_refusal():
    """🔴 RENDERS THE WHOLE PARAGRAPH, not just the helper.

    `_undo_advice()` fixed the last sentence; the FIRST one still read "the
    built-in default redirected the on-disk path away from …" — the same class
    of nonsense, one sentence earlier. A test that calls the helper in
    isolation structurally cannot see that, which is why this one renders
    `provenance_clauses` instead.
    """
    for src in ("$SOPS_AGE_KEY_FILE", "$ASIB_AGE_IDENTITY",
                EV.IDENTITY_SOURCE_FLAG, B.IDENTITY_SOURCE_DEFAULT):
        chose, redirect = EV.provenance_clauses(Path("/srv/elsewhere.key"), src)
        assert redirect, src
        # 🔴 POSITIVE CONTENT PINS, not only word-ABSENCES. The first version of
        # this loop asserted three forbidden words and nothing else, and an
        # INVERTED SAFETY CLAIM — "a mismatch here certainly means the escrow is
        # DAMAGED and you should re-escrow at once", the exact opposite of this
        # PR's advice — passed the whole suite using none of those words. A
        # guard on WORDS is walkable by REWORDING; these say what must be TRUE.
        assert "READ THIS BEFORE RE-ESCROWING" in redirect, (src, redirect)
        assert str(B.DEFAULT_IDENTITY) in redirect, (src, redirect)
        assert "same size" in redirect, (src, redirect)
        # only an ENV VAR can have "redirected" anything
        if not src.startswith("$"):
            assert "redirected" not in redirect, (src, redirect)
            assert "env -u" not in redirect, (src, redirect)
        # and nothing may claim a flag is settable by a shell
        assert "unrelated shell" not in redirect, (src, redirect)


def test_the_NEUTRAL_arms_refusal_is_pinned_as_a_WHOLE_STRING():
    """🔴 THE ARM THAT HAD NO POSITIVE PIN AT ALL, pinned whole.

    Its two siblings each carry positive content assertions; this one carried
    only word-absences, which is how an inverted safety instruction survived.
    When the artifact under test IS prose, the guard is the whole normalised
    string — a cosmetic reword then fails this test, and that is the trade
    being made deliberately for a machine-readable claim about key material.
    """
    _, redirect = EV.provenance_clauses(Path("/srv/elsewhere.key"),
                                        B.IDENTITY_SOURCE_DEFAULT)
    d = B.DEFAULT_IDENTITY
    assert redirect == (
        f" 🔴 READ THIS BEFORE RE-ESCROWING: the on-disk path is not {d}, so a "
        f"mismatch here is at least as likely to mean you compared the WRONG "
        f"FILE as it is to mean the escrow is damaged — and the two remedies "
        f"are opposites. Every age identity file is the same size, so equal "
        f"byte counts on both sides is what comparing two DIFFERENT keys looks "
        f"like, not evidence they are the same key. Re-run against {d} to "
        f"compare against the default first.")


def test_the_undo_advice_matches_the_KIND_of_source():
    """`env -u` is meaningless for a flag, and nonsense for the built-in
    default — which can pair with a non-default path only through `run()`'s
    keyword API, but renders a sentence either way."""
    assert "env -u SOPS_AGE_KEY_FILE" in EV._undo_advice("$SOPS_AGE_KEY_FILE")
    for src in (EV.IDENTITY_SOURCE_FLAG, B.IDENTITY_SOURCE_DEFAULT):
        advice = EV._undo_advice(src)
        assert "env -u" not in advice, advice
        assert str(B.DEFAULT_IDENTITY) in advice


# --------------------------------------------------------------------------- #
# 16. 🔴 --decrypt-check REFUSES BEFORE THE VAULT WHEN IT CANNOT FINISH
#
# Measured twice on 2026-08-25, the second time AFTER the hint was fixed:
# `--decrypt-check` unlocked the vault and then died on a missing `minio`,
# i.e. after the master password had been typed. Fixing WHICH shell is
# advertised did not fix WHEN the run discovers it is in the wrong one.
# --------------------------------------------------------------------------- #
def test_the_preflight_token_and_code_are_pinned():
    assert EV.EXIT_CODES["DECRYPT-DEPS-MISSING"] == 34
    # distinct from every other code, or the operator is sent to the wrong place
    codes = list(EV.EXIT_CODES.values())
    assert len(codes) == len(set(codes))


def test_the_preflight_PASSES_when_every_module_resolves():
    """The positive control. Without it a preflight wired to nothing — or one
    that always raised — would be indistinguishable from a working one."""
    EV.preflight_decrypt_imports(modules=("os", "sys"))
    # and the real ledger resolves in this environment too
    EV.preflight_decrypt_imports()


def test_the_preflight_NAMES_the_module_the_interpreter_and_the_shell():
    with pytest.raises(EV.EscrowError) as ei:
        EV.preflight_decrypt_imports(modules=("definitely_not_installed_xyz",))
    assert ei.value.token == "DECRYPT-DEPS-MISSING"
    assert ei.value.exit_code == 34
    msg = ei.value.verdict
    assert "definitely_not_installed_xyz" in msg
    # 🔴 WHICH interpreter — the whole confusion was not knowing which python ran
    assert sys.executable in msg
    # ...and the shell that would work, rendered from the ledger
    assert EV.NIX_SHELL_HINT in msg
    # ...and that nothing was checked, so this is not read as a verdict
    assert "NOTHING HAS BEEN CHECKED" in msg


def test_a_module_whose_PARENT_is_absent_counts_as_missing():
    """`find_spec` RAISES rather than returning None when a parent package is
    itself absent. Treating that as 'present' would let the run proceed to the
    exact failure the preflight exists to prevent."""
    with pytest.raises(EV.EscrowError) as ei:
        EV.preflight_decrypt_imports(modules=("no_such_parent_pkg.child",))
    assert ei.value.token == "DECRYPT-DEPS-MISSING"


def test_a_find_spec_that_RAISES_ImportError_is_treated_as_missing():
    def boom(_mod):
        raise ImportError("simulated")
    with pytest.raises(EV.EscrowError) as ei:
        EV.preflight_decrypt_imports(modules=("anything",), find_spec=boom)
    assert ei.value.token == "DECRYPT-DEPS-MISSING"


def test_the_refusal_happens_BEFORE_A_SINGLE_bw_CALL(escrow_world, monkeypatch):
    """🔴 THE POINT OF THE WHOLE CHANGE, asserted as a COUNT.

    A message that merely mentions the vault was not contacted is a claim; the
    fake `bw` recording ZERO invocations is the evidence. If this ever regresses
    the operator spends a master password on a run that cannot finish.
    """
    monkeypatch.setattr(EV, "DECRYPT_PYTHON_MODULES",
                        ("definitely_not_installed_xyz",))
    fake = FakeBw(items=[_item(notes=escrow_world["note"])])
    with pytest.raises(EV.EscrowError) as ei:
        EV.run(bw=_cli(fake), identity=escrow_world["identity"], item_name=ITEM,
               decrypt=True, prefix=PREFIX, store=escrow_world["store"],
               work_dir=escrow_world["work"], now=NOW)
    assert ei.value.token == "DECRYPT-DEPS-MISSING"
    assert fake.calls == [], (
        f"the vault was contacted before the preflight refused: {fake.calls}")


def test_an_INJECTED_downloader_is_never_refused(escrow_world, monkeypatch):
    """The negative control for the guard's SCOPE. A caller supplying a fake
    needs none of these modules; refusing it would make the seam untestable
    wherever the package is absent, which is its own failure mode."""
    monkeypatch.setattr(EV, "DECRYPT_PYTHON_MODULES",
                        ("definitely_not_installed_xyz",))
    d = FakeDownloader(escrow_world["objects"])
    v = EV.run(bw=_cli(FakeBw(items=[_item(notes=escrow_world["note"])])),
               identity=escrow_world["identity"], item_name=ITEM, decrypt=True,
               prefix=PREFIX, store=escrow_world["store"],
               work_dir=escrow_world["work"], now=NOW,
               downloader_factory=lambda: d)
    assert v.decrypt_checked is True


def test_a_run_WITHOUT_decrypt_check_is_never_refused(escrow_world, monkeypatch):
    """A byte-comparison run reaches no bucket, so the modules are irrelevant.
    Refusing it would be a permanently-red gate on the cheap check."""
    monkeypatch.setattr(EV, "DECRYPT_PYTHON_MODULES",
                        ("definitely_not_installed_xyz",))
    v = EV.run(bw=_cli(FakeBw(items=[_item(notes=escrow_world["note"])])),
               identity=escrow_world["identity"], item_name=ITEM,
               store=escrow_world["store"], now=NOW)
    assert v.decrypt_checked is False


def test_the_preflight_OUTRANKS_a_missing_bw(escrow_world, monkeypatch):
    """🔴 A DELIBERATE ORDERING, pinned — not an accident of line order.

    Both can be wrong at once: no `bw` on PATH AND an interpreter without the
    decrypt deps. The deps refusal is the more useful of the two, because its
    remedy is ONE nix-shell that provides `bw` as well. Reporting BW-MISSING
    first sends the operator to solve half the problem and hit the other half
    on the next run — the second trip being exactly what this change exists to
    prevent.

    Without this test, moving the preflight below `require_available()` is an
    invisible change: that method is a PATH lookup with no subprocess, so the
    zero-bw-calls guard cannot see the swap.
    """
    monkeypatch.setattr(EV, "DECRYPT_PYTHON_MODULES",
                        ("definitely_not_installed_xyz",))
    fake = FakeBw(items=[_item(notes=escrow_world["note"])])
    with pytest.raises(EV.EscrowError) as ei:
        EV.run(bw=_cli(fake, locator=lambda name: None),
               identity=escrow_world["identity"], item_name=ITEM,
               decrypt=True, prefix=PREFIX, store=escrow_world["store"],
               work_dir=escrow_world["work"], now=NOW)
    assert ei.value.token == "DECRYPT-DEPS-MISSING", (
        "BW-MISSING won: the preflight no longer runs before require_available")
    assert fake.calls == []
    # and the remedy it prints must cover BOTH faults, or the ordering is wrong
    assert "bitwarden-cli" in ei.value.verdict
    assert "p.minio" in ei.value.verdict


def test_a_missing_bw_STILL_reports_itself_when_the_deps_are_fine(escrow_world):
    """The negative control for the ordering above: with the deps present, a
    missing `bw` must still produce BW-MISSING. Otherwise the preflight could
    be masking that failure rather than outranking it."""
    fake = FakeBw(items=[_item(notes=escrow_world["note"])])
    with pytest.raises(EV.EscrowError) as ei:
        EV.run(bw=_cli(fake, locator=lambda name: None),
               identity=escrow_world["identity"], item_name=ITEM,
               decrypt=True, prefix=PREFIX, store=escrow_world["store"],
               work_dir=escrow_world["work"], now=NOW)
    assert ei.value.token == "BW-MISSING"


# --------------------------------------------------------------------------- #
# 16b. findings from the #851 audit — each one a guard that was NOT there
# --------------------------------------------------------------------------- #
def test_the_preflight_refusal_is_pinned_as_a_WHOLE_STRING():
    """🔴 THE CLAUSE THAT MATTERS MOST WAS THE ONE NOT ASSERTED.

    The first version asserted three substrings and never touched
    "the vault was NOT contacted" — so flipping it to "the vault WAS
    contacted", a straight falsehood about whether the operator's credential
    had been used, left the entire suite green. This module's own section
    header says a substring cannot tell a true message from a confident wrong
    one; that standard applies here more than anywhere.
    """
    with pytest.raises(EV.EscrowError) as ei:
        EV.preflight_decrypt_imports(modules=("definitely_not_installed_xyz",))
    assert ei.value.verdict == (
        f"--decrypt-check needs definitely_not_installed_xyz, which "
        f"{sys.executable} cannot import. NOTHING HAS BEEN CHECKED and the "
        f"vault was NOT contacted — this refusal is raised before any `bw` "
        f"call precisely so a master password is not spent on a run that "
        f"cannot finish. Re-run under a shell that provides it: "
        f"{EV.NIX_SHELL_HINT}. (A shell WITHOUT the "
        f"`python3.withPackages(...)` argument resolves `python3` from the "
        f"ambient profile, which does not carry these packages.)")


def test_EVERY_module_in_the_ledger_is_checked_not_just_the_first():
    """🔴 A ONE-TUPLE FIXTURE CANNOT SEE `modules[:1]`.

    Every fixture here — and the real ledger — had exactly one entry, so a
    mutant that checks only the first module survived, and the multi-module
    `', '.join(missing)` rendering was never exercised. A two-element fixture
    whose FIRST entry resolves is the control that can see it.
    """
    with pytest.raises(EV.EscrowError) as ei:
        EV.preflight_decrypt_imports(modules=("os", "definitely_not_installed_xyz"))
    assert "definitely_not_installed_xyz" in ei.value.verdict
    # both missing -> both named, comma-joined
    with pytest.raises(EV.EscrowError) as ei2:
        EV.preflight_decrypt_imports(modules=("no_such_a_xyz", "no_such_b_xyz"))
    assert "no_such_a_xyz, no_such_b_xyz" in ei2.value.verdict


def test_a_find_spec_raising_ValueError_is_treated_as_missing():
    """The `ValueError` arm of the except tuple was untested — dropping it
    survived, while dropping `ImportError` was caught. Both arms now observable."""
    def boom(_mod):
        raise ValueError("simulated embedded null")
    with pytest.raises(EV.EscrowError) as ei:
        EV.preflight_decrypt_imports(modules=("anything",), find_spec=boom)
    assert ei.value.token == "DECRYPT-DEPS-MISSING"


def test_FROM_DIR_is_never_refused_for_a_missing_bucket_package(escrow_world,
                                                                 monkeypatch,
                                                                 tmp_path):
    """🔴 THE NEGATIVE CONTROL THE `from_dir` ARM LACKED.

    Deleting `and from_dir is None` from the scope condition survived the whole
    suite, because every `--from-dir` test ran with the real ledger and `minio`
    present. `--from-dir` is the ONLY decrypt path exercisable without a
    cluster — so it is exactly the one most likely to be run from a shell with
    no `minio`, and wrongly refusing it with rc 34 would be a permanently-red
    gate on the cheap path.
    """
    monkeypatch.setattr(EV, "DECRYPT_PYTHON_MODULES",
                        ("definitely_not_installed_xyz",))
    root = tmp_path / "fetched"
    for k, blob in escrow_world["objects"].items():
        obj = root / k
        obj.parent.mkdir(parents=True, exist_ok=True)
        obj.write_bytes(blob)
    v = EV.run(bw=_cli(FakeBw(items=[_item(notes=escrow_world["note"])])),
               identity=escrow_world["identity"], item_name=ITEM, decrypt=True,
               bucket=str(root), prefix=PREFIX, store=escrow_world["store"],
               from_dir=root, work_dir=escrow_world["work"], now=NOW)
    assert v.decrypt_checked is True


def test_the_preflight_OUTRANKS_a_missing_identity(escrow_world, monkeypatch,
                                                   tmp_path):
    """🔴 A SECOND ORDERING PINNED AS A DECISION.

    With no `minio` AND an unreadable identity, DECRYPT-DEPS-MISSING must win.
    IDENTITY-MISSING's remedy (point ASIB_AGE_IDENTITY / SOPS_AGE_KEY_FILE at a
    real file) is DISJOINT from the shell fix, so reporting it first buys the
    operator a second trip — the exact thing this preflight exists to prevent.
    Moving the preflight below `read_identity` is otherwise invisible.
    """
    monkeypatch.setattr(EV, "DECRYPT_PYTHON_MODULES",
                        ("definitely_not_installed_xyz",))
    fake = FakeBw(items=[_item(notes=escrow_world["note"])])
    with pytest.raises(EV.EscrowError) as ei:
        EV.run(bw=_cli(fake), identity=tmp_path / "does-not-exist.key",
               item_name=ITEM, decrypt=True, prefix=PREFIX,
               store=escrow_world["store"], work_dir=escrow_world["work"],
               now=NOW)
    assert ei.value.token == "DECRYPT-DEPS-MISSING", (
        "IDENTITY-MISSING won: the preflight no longer runs before read_identity")
    assert fake.calls == []


def test_a_missing_identity_STILL_reports_itself_when_the_deps_are_fine(
        escrow_world, tmp_path):
    """Negative control for the ordering above — the preflight must OUTRANK
    IDENTITY-MISSING, not MASK it."""
    with pytest.raises(EV.EscrowError) as ei:
        EV.run(bw=_cli(FakeBw(items=[_item(notes=escrow_world["note"])])),
               identity=tmp_path / "does-not-exist.key", item_name=ITEM,
               decrypt=True, prefix=PREFIX, store=escrow_world["store"],
               work_dir=escrow_world["work"], now=NOW)
    assert ei.value.token == "IDENTITY-MISSING"


def test_SECRETS_md_exit_codes_agree_with_the_module():
    """🔴 THE TABLE A HUMAN READS UNDER PRESSURE, BOUND TO THE CODE.

    `--print-plan` renders `EXIT_CODES` dynamically and is covered — but
    SECRETS.md's `--decrypt-check` table is hand-written, and nothing tied the
    two together. It silently went stale the moment code 34 was added: the doc
    still said "eight outcomes" and omitted the one an operator is now most
    likely to hit.

    Scope, stated so this is not read as more than it is: this pins every
    (code, token) pair the doc DOES list against the module, and requires the
    preflight code to appear. It cannot know which subset of codes the doc
    *ought* to list, so a future code omitted entirely is still invisible here.
    """
    import re
    doc = (ROOT / "SECRETS.md").read_text(encoding="utf-8")
    pairs = re.findall(r"\|\s*`(\d+)`\s*`([A-Z][A-Z-]+)`\s*\|", doc)
    assert pairs, "no exit-code rows found — the table moved or changed shape"
    for code, token in pairs:
        assert token in EV.EXIT_CODES, (
            f"SECRETS.md documents {token!r}, which the module does not define")
        assert EV.EXIT_CODES[token] == int(code), (
            f"SECRETS.md says {token}={code}, module says "
            f"{EV.EXIT_CODES[token]}")
    documented = {t for _, t in pairs}
    assert "DECRYPT-DEPS-MISSING" in documented, (
        "the preflight's code is undocumented — it is the one an operator in "
        "the wrong shell will actually hit")


def test_a_REFUSED_run_leaves_no_work_dir_behind(monkeypatch, tmp_path):
    """🔴 THE REFUSAL MUST BE THE FIRST THING THAT ACTS, not merely the first
    thing reported.

    `main()` built the work dir before calling `run()`, so a refusal that says
    "NOTHING HAS BEEN CHECKED" had already created the whole parent chain —
    and, on a pre-existing directory, chmodded it to 0700. The message stayed
    literally true (nothing was *checked*), which is exactly why this needed a
    test rather than a reading: removing the fix leaves the suite green.
    """
    monkeypatch.setattr(EV, "DECRYPT_PYTHON_MODULES",
                        ("definitely_not_installed_xyz",))
    work = tmp_path / "deep" / "nested" / "work"
    rc = EV.main(["--decrypt-check", "--work-dir", str(work),
                  "--identity", str(tmp_path / "any.key"),
                  "--host", "synthetic-host"])
    assert rc == EV.EXIT_CODES["DECRYPT-DEPS-MISSING"]
    assert not work.exists(), "the work dir was created before the refusal"
    assert not work.parent.exists(), "the parent chain was created too"


def test_a_REFUSED_run_does_not_CHMOD_an_existing_dir(monkeypatch, tmp_path):
    """The second half of the same fix, and the one with a real blast radius:
    `_private_dir` chmods an EXISTING directory to 0700. Refusing afterwards
    left an operator-supplied directory's mode silently changed."""
    monkeypatch.setattr(EV, "DECRYPT_PYTHON_MODULES",
                        ("definitely_not_installed_xyz",))
    work = tmp_path / "mine"
    work.mkdir(mode=0o755)
    work.chmod(0o755)
    before = stat.S_IMODE(work.stat().st_mode)
    rc = EV.main(["--decrypt-check", "--work-dir", str(work),
                  "--identity", str(tmp_path / "any.key"),
                  "--host", "synthetic-host"])
    assert rc == EV.EXIT_CODES["DECRYPT-DEPS-MISSING"]
    assert stat.S_IMODE(work.stat().st_mode) == before, (
        "the refused run changed the mode of a directory it did not create")
