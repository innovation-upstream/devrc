"""Unit tests for ship.sh host-role detection (detect_role).

Both NixOS hosts share hostname `nixos`, so ship.sh must identify the physical
host from its local IPv4 addresses instead. `detect_role` is exposed as a pure,
side-effect-free path via the hidden `ship.sh --detect-role "<ip,ip,...>"` mode:
it consumes an INJECTED IP list (no live-machine / network / SSH calls) and
prints one of workbench|laptop|unknown. These tests drive exactly that, so they
are fully hermetic and headless.

Precedence contract (mirrors the comment in ship.sh):
  1. primary LAN addresses (192.168.50.x) match before secondary (10.42.x);
  2. within a pass, WORKBENCH matches before LAPTOP — so a list carrying BOTH
     hosts' primary addresses deterministically resolves to "workbench".
"""

import subprocess
from pathlib import Path

import pytest

SHIP = Path(__file__).resolve().parents[1] / "ship.sh"


def detect_role(ip_list: str) -> str:
    """Invoke ship.sh's hidden --detect-role mode with an injected IP list."""
    out = subprocess.run(
        ["bash", str(SHIP), "--detect-role", ip_list],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


@pytest.mark.parametrize(
    "ip_list,expected",
    [
        # --- workbench (primary LAN, secondary, mixed with noise) ---
        ("192.168.50.250", "workbench"),
        ("192.168.50.250 172.17.0.1 127.0.0.1", "workbench"),
        ("10.42.0.30", "workbench"),                      # secondary-only fallback
        ("172.17.0.1,10.42.0.30", "workbench"),           # comma-separated input
        # --- laptop (primary LAN, secondary, mixed with noise) ---
        ("192.168.50.155", "laptop"),
        ("192.168.50.155,10.42.0.100,172.17.0.1", "laptop"),
        ("10.42.0.100", "laptop"),                        # secondary-only fallback
        # --- unknown (no canonical address present) ---
        ("172.17.0.1 127.0.0.1", "unknown"),
        ("", "unknown"),                                   # empty list
        ("10.0.0.5", "unknown"),                           # unrelated address
    ],
)
def test_detect_role(ip_list, expected):
    assert detect_role(ip_list) == expected


def test_primary_beats_secondary():
    """A workbench primary + laptop secondary in one list -> workbench (primary wins)."""
    assert detect_role("192.168.50.250 10.42.0.100") == "workbench"
    # symmetric: laptop primary + workbench secondary -> laptop
    assert detect_role("192.168.50.155 10.42.0.30") == "laptop"


def test_ambiguous_both_primaries_resolves_to_workbench():
    """Both hosts' PRIMARY addresses present (unexpected) -> deterministic workbench."""
    assert detect_role("192.168.50.155 192.168.50.250") == "workbench"
    # order of the input list must not change the outcome
    assert detect_role("192.168.50.250 192.168.50.155") == "workbench"
