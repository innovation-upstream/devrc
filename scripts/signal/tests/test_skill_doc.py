"""SKILL.md must document the surface the CODE actually has — derived, not restated.

🔴 Every ledger below is built by `re.findall` over the MODULE SOURCE. A
hand-written list of kinds/commands/env vars could not catch the thing this file
exists to catch: a surface added to the code and never documented, so an agent
reading SKILL.md cannot find it. (`browser-bridge/tests/test_surface_parity.py`
is the precedent; `context` once shipped dead for exactly this reason.)

Each parser asserts a NON-EMPTY, plausible-cardinality result before any parity
claim is made — a regex that silently matched nothing would make every assertion
here pass vacuously.
"""
import re
from pathlib import Path

import pytest

import consumer

SIGNAL_DIR = Path(consumer.__file__).resolve().parent
REPO = SIGNAL_DIR.parents[1]
SKILL = REPO / "claude" / "skills" / "signal" / "SKILL.md"

# Plausible floors. NOT the contract (the derived sets are) — they exist so a
# broken regex fails as "the harness is broken" rather than passing silently.
MIN_KINDS = 8
MIN_STATS = 5
MIN_COMMANDS = 6
MIN_ENV_VARS = 8


def _read(path: Path) -> str:
    if not path.is_file():
        raise AssertionError(
            f"HARNESS BROKEN: {path} does not exist — a parity test cannot be "
            f"green against a source it never read")
    return path.read_text(encoding="utf-8")


SKILL_TEXT = _read(SKILL)
CONSUMER_SRC = _read(SIGNAL_DIR / "consumer.py")


def _kinds() -> set:
    found = set(re.findall(r'^KIND_[A-Z_]+ = "([a-z_]+)"', CONSUMER_SRC, re.MULTILINE))
    assert len(found) >= MIN_KINDS, f"HARNESS BROKEN: parsed {found}"
    return found


def _stats() -> set:
    found = set(re.findall(r'^STAT_[A-Z_]+ = "([a-z_]+)"', CONSUMER_SRC, re.MULTILINE))
    assert len(found) >= MIN_STATS, f"HARNESS BROKEN: parsed {found}"
    return found


def _commands() -> set:
    found = set(re.findall(r'sub\.add_parser\(\s*"([a-z-]+)"', CONSUMER_SRC))
    assert len(found) >= MIN_COMMANDS, f"HARNESS BROKEN: parsed {found}"
    return found


def _env_vars() -> set:
    found = set()
    for path in sorted(SIGNAL_DIR.glob("*.py")):
        src = _read(path)
        found |= set(re.findall(r'os\.environ\.get\(\s*"([A-Z_]+)"', src))
    assert len(found) >= MIN_ENV_VARS, f"HARNESS BROKEN: parsed {found}"
    return found


# --------------------------------------------------------------------------- #
# Harness self-checks
# --------------------------------------------------------------------------- #
def test_skill_file_exists_and_is_substantial():
    assert len(SKILL_TEXT) > 2000


def test_parsers_return_the_values_the_module_really_uses():
    """POSITIVE CONTROL: the derived sets match the module's own attributes."""
    assert _kinds() == {v for k, v in vars(consumer).items()
                        if k.startswith("KIND_") and isinstance(v, str)}
    assert _stats() == {v for k, v in vars(consumer).items()
                        if k.startswith("STAT_") and isinstance(v, str)}


def test_parsers_fail_loudly_on_a_source_they_cannot_read(tmp_path):
    """NEGATIVE CONTROL: `_read` raises rather than returning an empty string."""
    with pytest.raises(AssertionError):
        _read(tmp_path / "definitely-absent.md")


# --------------------------------------------------------------------------- #
# Parity
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", sorted(_kinds()))
def test_every_event_kind_is_documented(kind):
    assert f"`{kind}`" in SKILL_TEXT, (
        f"event kind {kind!r} is emitted by consumer.py but absent from SKILL.md")


@pytest.mark.parametrize("stat", sorted(_stats()))
def test_every_counter_is_documented(stat):
    assert f"`{stat}`" in SKILL_TEXT, (
        f"counter {stat!r} is reported by the daemon but absent from SKILL.md")


@pytest.mark.parametrize("cmd", sorted(_commands()))
def test_every_cli_command_is_documented(cmd):
    assert f"`{cmd}`" in SKILL_TEXT, (
        f"CLI command {cmd!r} exists but is absent from SKILL.md")


@pytest.mark.parametrize("var", sorted(_env_vars()))
def test_every_environment_variable_is_documented(var):
    assert f"`{var}`" in SKILL_TEXT, (
        f"env var {var!r} is read by scripts/signal/ but absent from SKILL.md")


def test_documented_kinds_do_not_exceed_what_the_module_emits():
    """The other direction: SKILL.md must not advertise a kind that does not exist."""
    table = SKILL_TEXT.split("## Event kinds")[1].split("\n##")[0]
    documented = set(re.findall(r"^\| `([a-z_]+)` \|", table, re.MULTILINE))
    assert documented == _kinds()


def test_documented_commands_do_not_exceed_the_cli():
    table = SKILL_TEXT.split("## Commands")[1].split("\n##")[0]
    documented = set(re.findall(r"^\| `([a-z-]+)` \|", table, re.MULTILINE))
    assert documented == _commands()


# --------------------------------------------------------------------------- #
# The claims the skill makes about behaviour must still be true
# --------------------------------------------------------------------------- #
def test_skill_frontmatter_is_routing_surface():
    head = SKILL_TEXT.split("---")[1]
    assert "name: signal" in head
    desc = re.search(r'description: "(.*)"', head, re.DOTALL).group(1)
    assert len(desc) <= 1536                       # the per-entry listing cap
    assert "Signal" in desc
    assert "mailbox" in desc                       # disambiguation from the sibling
    assert "draft" in desc.lower()


def test_skill_states_the_send_path_is_gated_not_direct():
    assert "SendGateError" in SKILL_TEXT
    assert "DRAFT" in SKILL_TEXT and "APPROVE" in SKILL_TEXT
    assert "never direct-send" in SKILL_TEXT


def test_skill_names_the_error_type_the_code_actually_raises():
    """A doc naming a non-existent exception is worse than naming none."""
    import _signal_db
    assert hasattr(_signal_db, "SendGateError")
    assert issubclass(_signal_db.SendGateError, RuntimeError)


def test_skill_documents_every_table_the_schema_creates():
    import _signal_db
    tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS signal\.(\w+)",
                            "\n".join(_signal_db.SCHEMA_STATEMENTS)))
    # Bumped 5 -> 6 for signal.consumer_health (the liveness row), 6 -> 7 for
    # signal.excluded_groups (the group mute list). The literal is the POINT of
    # this guard: a new table cannot appear without someone deciding, here, to
    # document it.
    assert len(tables) == 7, tables
    for table in tables:
        assert f"`signal.{table}`" in SKILL_TEXT, f"table {table} undocumented"


def test_reconcile_sent_without_a_timestamp_is_a_REFUSAL_not_a_traceback():
    """🟢 F3 — `--timestamp` is conditionally required and argparse cannot say so.

    `_server_timestamp` raises a plain `ValueError` and `main` caught only
    `SendGateError`, so "refuses rather than guessing" was true but arrived as a
    traceback and an exit code nobody scripts on. Driven through `main()` here,
    which is the surface an operator actually touches.
    """
    import consumer as consumer_module

    argv = ["reconcile", "7", "--sent"]
    args = consumer_module.build_parser().parse_args(argv)
    assert args.sent is True and args.timestamp is None   # argparse allows it ...

    src = _read(SIGNAL_DIR / "consumer.py")
    branch = src.split('elif args.cmd == "reconcile":')[1].split("\n        elif ")[0]
    assert "if args.sent and not args.timestamp:" in branch   # ... main refuses it
    assert "return 3" in branch
    assert "(SendGateError, ValueError)" in branch


def test_skill_names_the_bucket_the_code_uses():
    import _minio
    assert f"`{_minio.BUCKET}`" in SKILL_TEXT


def test_skill_records_all_four_schema_corrections():
    """Each 🔧 correction is a live gotcha, so each must be in the gotchas list."""
    gotchas = SKILL_TEXT.split("## ⚠ Gotchas")[1]
    assert "does not dedupe over NULL" in gotchas
    assert "echo back via device sync" in gotchas
    assert "Reactions can precede their target" in gotchas
    assert "signal_attachment_id" in gotchas


# --------------------------------------------------------------------------- #
# Round-2 audit F2 + F7 — two places SKILL.md made a claim the code did not keep
# --------------------------------------------------------------------------- #
_PREFIX_SEPARATORS = ["-", ".", " ", "'"]


@pytest.mark.parametrize("sep", _PREFIX_SEPARATORS)
def test_the_prefix_claim_is_true_of_the_CODE_and_stated_in_the_DOC(sep):
    """🔴 THE SEAM: a doc sentence and the code that has to be as wide as it.

    SKILL.md claimed "a member whose name is a PREFIX of another member's cannot
    steal their ping". The code implemented `(?!\\w)`, which blocks only WORD
    characters — so at 9fb6de75 every separator below satisfied the boundary and
    `--mention Ann` landed on the first four characters of `Ann<sep>Marie`. The
    doc was RIGHT and the code was narrow; the audit's instruction was to widen
    the code, and this test is what stops them drifting apart again.

    BOTH halves are asserted from ONE parameter list. Widen the doc without the
    code and the behavioural half fails; narrow the code without the doc and the
    same half fails; drop a separator from the doc and the text half fails.
    Neither half is a guard on its own — separately they are exactly the
    "verified in isolation" shape that let this ship.

    RED at 9fb6de75 on the behavioural half for `-`, `.`, `'` and ` `.
    """
    import _mentions

    ann = "11111111-1111-4111-8111-111111111111"
    other_id = "22222222-2222-4222-8222-222222222222"
    other = f"Ann{sep}Marie"
    contacts = [
        {"signal_uuid": ann, "phone_number": None, "display_name": "Ann",
         "profile_name": None, "is_placeholder": False},
        {"signal_uuid": other_id, "phone_number": None, "display_name": other,
         "profile_name": None, "is_placeholder": False},
    ]
    # (a) the CODE refuses rather than stealing the ping.
    with pytest.raises(_mentions.MentionSpanMissing):
        _mentions.resolve_mentions(["Ann"], body=f"hi @{other} ok",
                                   members=[ann, other_id], contacts=contacts,
                                   is_group=True)
    # (b) the DOC names this separator, so a reader can tell what is covered.
    mentions_section = SKILL_TEXT.split("## Mentions")[1].split("\n## ")[0]
    assert f"@Ann{sep}" in mentions_section, (
        f"the code refuses a {sep!r}-separated prefix collision but SKILL.md's "
        f"Mentions section never mentions that separator — the doc is narrower "
        f"than the code, which is how the reverse drift went unnoticed")


def test_the_prefix_claim_does_NOT_overreach_to_NON_member_text():
    """The other direction: the doc must not claim more than the code does.

    The rule is MEMBER-aware. `@Ann` in `"@Ann-the-dog"`, where nothing is named
    `Ann-the-dog`, still matches — deliberately, because banning punctuation
    outright would break `"thanks @Ann."`. SKILL.md has to say so, or the next
    reader files the behaviour below as a bug.
    """
    import _mentions

    ann = "11111111-1111-4111-8111-111111111111"
    contacts = [{"signal_uuid": ann, "phone_number": None, "display_name": "Ann",
                 "profile_name": None, "is_placeholder": False}]
    assert _mentions.resolve_mentions(
        ["Ann"], body="@Ann-the-dog", members=[ann], contacts=contacts,
        is_group=True) == [{"author": ann, "start": 0, "length": 4}]
    mentions_section = SKILL_TEXT.split("## Mentions")[1].split("\n## ")[0]
    assert "member" in mentions_section.lower()
    assert "thanks @Ann." in mentions_section, (
        "SKILL.md must show the case the member-aware rule deliberately allows")


def test_the_pre_digest_drain_procedure_is_ORDERED_so_it_can_be_RUN():
    """🔴 RED at 9fb6de75. Round-2 audit F7.

    The doc said to drain pre-existing approvals "**before** rolling this out",
    and then prescribed `unapprove` and a `WHERE approved_digest IS NULL` query —
    a command and a column that BOTH ship in this change. Followed literally the
    procedure cannot run: there is no `unapprove` on the previous revision and no
    column to select on. An unexecutable runbook step is worse than none, because
    the operator discovers it mid-incident.

    Pinned by ORDER, not by a phrase.

    🔴 THE PHRASE ASSERTION IS GONE — round-3 audit F2. It read
    `assert "before rolling this out" not in SKILL_TEXT.lower()` and it passed
    only because a markdown line-wrap splits the phrase across a newline at
    SKILL.md's "the old wording had it backwards" paragraph, which quotes those
    exact words on purpose. So it was green for a typographic reason, and a
    purely COSMETIC rewrap — identical words, one line instead of two — turned
    it RED with the message "the reversed ordering is back", which would have
    been false. A guard on WORDS is walkable by rewording and trippable by
    reflowing; the state that actually matters is the ORDER, and the assertion
    below is the one that pins it.
    """
    anchor = "rain the pre-existing approvals"
    assert SKILL_TEXT.count(anchor) == 1, \
        "HARNESS: the drain section anchor is not unique in SKILL.md"
    section = SKILL_TEXT.split(anchor)[1]
    # 🔴 THE COMPARISON IS SCOPED TO THE NUMBERED PROCEDURE, NOT THE WHOLE
    # SECTION. Measured while removing the phrase assertion: `section` opens
    # with a paragraph EXPLAINING the ordering, and that paragraph contains the
    # word "deploy" (offset 741) before the SELECT (offset 1025) — so the index
    # over the whole section was satisfied by the explanation, whatever order
    # the steps below it were in. Swapping steps 1 and 2 in SKILL.md left this
    # test GREEN. It is now anchored on the list itself.
    parts = section.split("\n   1. ", 1)
    assert len(parts) == 2, \
        "HARNESS: the drain procedure is no longer a `1.`-numbered list"
    procedure = parts[1]
    deploy_at = procedure.lower().index("deploy")
    query_at = procedure.index("approved_digest IS NULL")
    assert deploy_at < query_at, (
        "the drain procedure must tell the operator to deploy FIRST — the "
        "column it selects on is created by the new consumer's ensure_schema()")
    # And the recovery command it prescribes must actually exist in the CLI.
    assert "unapprove" in _commands(), "the runbook prescribes a command the CLI lacks"
