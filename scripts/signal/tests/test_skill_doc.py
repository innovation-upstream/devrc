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
    assert len(tables) == 5, tables
    for table in tables:
        assert f"`signal.{table}`" in SKILL_TEXT, f"table {table} undocumented"


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
