"""Unit tests for keymap: keysym → char honoring Shift / CapsLock / AltGr."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import keymap as KM  # noqa: E402

# keysym constants for the layouts under test.
A_LOWER, A_UPPER = 0x0061, 0x0041          # a / A
ONE, EXCL = 0x0031, 0x0021                 # 1 / !
RETURN, TAB, SPACE, BS = 0xFF0D, 0xFF09, 0x0020, 0xFF08
LEFT = 0xFF51                              # arrow (non-printing)
E_ACUTE = 0x00E9                           # é (latin-1)


def test_latin1_printable():
    assert KM.keysym_to_char(A_LOWER) == "a"
    assert KM.keysym_to_char(EXCL) == "!"
    assert KM.keysym_to_char(E_ACUTE) == "é"


def test_named_keys_resolve_to_literals():
    assert KM.keysym_to_char(RETURN) == "\n"
    assert KM.keysym_to_char(TAB) == "\t"
    assert KM.keysym_to_char(SPACE) == " "
    assert KM.keysym_to_char(BS) == "\b"


def test_nonprinting_returns_none():
    assert KM.keysym_to_char(LEFT) is None
    assert KM.keysym_to_char(0) is None


def test_unicode_keysym():
    # Unicode keysym = 0x01000000 | codepoint. 你 = U+4F60.
    ni = 0x01000000 | 0x4F60
    assert KM.keysym_to_char(ni) == "你"
    euro = 0x01000000 | 0x20AC
    assert KM.keysym_to_char(euro) == "€"


def test_shift_uppercases_letter():
    # Shift held (state bit 0) → upper keysym.
    assert KM.resolve_char(A_LOWER, A_UPPER, state=0x01) == "A"
    assert KM.resolve_char(A_LOWER, A_UPPER, state=0x00) == "a"


def test_capslock_uppercases_letter_only():
    # CapsLock (bit 1) on a letter → upper.
    assert KM.resolve_char(A_LOWER, A_UPPER, state=0x02) == "A"
    # CapsLock + Shift → cancels back to lower.
    assert KM.resolve_char(A_LOWER, A_UPPER, state=0x03) == "a"


def test_capslock_does_not_affect_digits():
    # CapsLock must NOT shift "1" to "!"; only Shift does.
    assert KM.resolve_char(ONE, EXCL, state=0x02) == "1"
    assert KM.resolve_char(ONE, EXCL, state=0x00) == "1"
    assert KM.resolve_char(ONE, EXCL, state=0x01) == "!"


def test_group_index_altgr():
    assert KM.group_index(state=0x00) == 0
    assert KM.group_index(state=0x80) == 1  # Mod5 / AltGr
