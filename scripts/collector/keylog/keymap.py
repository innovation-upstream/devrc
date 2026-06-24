"""keymap — pure keycode/keysym → character mapping honoring modifier state.

Separated from the X plumbing so it is unit-testable WITHOUT an X server: the
mapping logic takes a keysym + modifier flags and returns the character (or a
named token for non-printing keys). The live keylogger feeds it keysyms it gets
from `display.keycode_to_keysym(keycode, group_index)`; tests feed it keysyms
directly.

Keysym → character rules (X11):
  * Latin-1 keysyms 0x20..0xff map straight to that code point.
  * Unicode keysyms have the 0x01000000 bit set: char = keysym & 0x00ffffff.
  * A small table covers the editing/whitespace keys we care about (Return,
    Tab, space, BackSpace, …) as named tokens.
"""
from __future__ import annotations

# keysym constants we special-case (values from X11/keysymdef.h).
XK_BackSpace = 0xFF08
XK_Tab = 0xFF09
XK_Return = 0xFF0D
XK_Escape = 0xFF1B
XK_Delete = 0xFFFF
XK_space = 0x0020

# Named non-printing keys → a stable token. Return/Tab/space resolve to their
# literal character (they belong in captured text); the rest are control keys
# the chunker may act on but that do not contribute glyphs.
_NAMED = {
    XK_Return: "\n",
    XK_Tab: "\t",
    XK_space: " ",
    XK_BackSpace: "\b",
}

UNICODE_KEYSYM_FLAG = 0x01000000


def keysym_to_char(keysym: int) -> str | None:
    """Return the character a keysym produces, or None if it is non-printing.

    Return/Tab/space resolve to their literal characters; BackSpace resolves to
    "\\b" so the chunker can apply it as an edit. Other control/navigation keys
    (arrows, F-keys, modifiers) return None.
    """
    if keysym == 0:
        return None
    if keysym in _NAMED:
        return _NAMED[keysym]
    # Latin-1 printable range.
    if 0x20 <= keysym <= 0x7E or 0xA0 <= keysym <= 0xFF:
        return chr(keysym)
    # Unicode keysyms: high bit set, low 24 bits are the code point.
    if keysym & UNICODE_KEYSYM_FLAG:
        cp = keysym & 0x00FFFFFF
        if cp >= 0x20:
            try:
                return chr(cp)
            except (ValueError, OverflowError):
                return None
    return None


def group_index(state: int, mod_altgr: int = 0x80) -> int:
    """Pick the keyboard group (0 = base, 1 = AltGr/level-3) from modifier state.

    AltGr on most layouts is reported as Mod5 (mask 0x80). When it is held we use
    keysym group 1 so AltGr combinations resolve correctly.
    """
    return 1 if (state & mod_altgr) else 0


def shift_active(state: int, keysym_lower: int, keysym_upper: int,
                 shift_mask: int = 0x01, lock_mask: int = 0x02) -> bool:
    """Decide whether the SHIFTED keysym applies, honoring Shift + CapsLock.

    CapsLock only affects keys that are alphabetic (lower != upper as letters);
    for those, Lock XORs with Shift. For non-letters Lock is ignored (standard
    X behaviour), so only Shift matters.
    """
    shift = bool(state & shift_mask)
    lock = bool(state & lock_mask)
    is_letter = (
        keysym_lower != keysym_upper
        and (chr(keysym_lower).isalpha() if 0x20 <= keysym_lower <= 0x10FFFF else False)
    )
    if is_letter:
        return shift ^ lock
    return shift


def resolve_char(keysym_lower: int, keysym_upper: int, state: int,
                 shift_mask: int = 0x01, lock_mask: int = 0x02) -> str | None:
    """Resolve the final character for a key given its lower/upper keysyms (the
    two level-0/level-1 keysyms for the active group) and the modifier `state`.

    Returns the produced character, or None for non-printing keys.
    """
    use_upper = shift_active(state, keysym_lower, keysym_upper, shift_mask, lock_mask)
    keysym = keysym_upper if use_upper else keysym_lower
    return keysym_to_char(keysym)
