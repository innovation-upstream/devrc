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
import shutil
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
    }


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
    assert str(e).startswith("VAULT-LOCKED: ")


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


def test_a_MISSING_notes_field_is_EMPTY_not_a_crash(tmp_path):
    with pytest.raises(EV.EscrowError) as ei:
        _run(tmp_path, FakeBw(items=[_item(notes=None)]))
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
    assert "server pinned and matched" in v.line()

    v2 = _run(tmp_path, FakeBw(items=[_item()]))
    assert v2.server_pinned is False
    assert "NOT PINNED" in v2.line()


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


def test_shred_removes_the_file_and_never_raises_on_an_absent_one(tmp_path):
    p = tmp_path / "k"
    p.write_bytes(b"synthetic-shred-fixture-0123456789")
    EV._shred(p)
    assert not p.exists()
    EV._shred(p)          # idempotent; a cleanup that raises defeats its finally
    EV._shred(tmp_path / "never-existed")


# --------------------------------------------------------------------------- #
# 13. the CLI — real argv, real exit codes, a real `bw` process
# --------------------------------------------------------------------------- #
BW_STUB = '''#!/usr/bin/env python3
"""A synthetic `bw`. Answers from a JSON script; never touches a real vault."""
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
    stub = tmp_path / "bw-stub" / "bw"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(BW_STUB, encoding="utf-8")
    stub.chmod(0o755)
    planfile = tmp_path / "bw-plan.json"
    planfile.write_text(json.dumps(plan), encoding="utf-8")
    return stub, planfile


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
