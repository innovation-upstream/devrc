"""Deliberately failing test — the pre-push gate's POSITIVE CONTROL.

Pushed on a throwaway branch so the hook can be OBSERVED running and blocking.
Never merged; the branch is deleted after the demonstration.

Without this, "the hook is installed" is a claim about `git config`, not about
anything executing — the reassuring-zero shape claude/RULES.md names.
"""


def test_this_must_fail_so_the_pre_push_gate_can_be_seen_to_block():
    assert False, "pre-push positive control: this failure must block the push"
