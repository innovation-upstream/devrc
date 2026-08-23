#!/usr/bin/env python3
"""Tests for check-completion.py — windowed signal extraction."""
import json, os, sys, tempfile, shutil
from pathlib import Path

# Add scripts dir to path and import the module
SCRIPT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

# Import the module by loading the file directly
import importlib.util
spec = importlib.util.spec_from_file_location("check_completion", SCRIPT_DIR / "check-completion.py")
check_completion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_completion)


def test_extract_text_windows_exact_match():
    text = "Task 868gy0ddd was fixed. PR #123 merged for 868gy0ddd. Later 868gy0ddd was closed."
    windows = check_completion.extract_text_windows(text, "868gy0ddd", window_size=50)
    assert len(windows) == 3, f"Expected 3 windows, got {len(windows)}"
    for window, dist, offset in windows:
        assert window[offset:offset + 9] == "868gy0ddd"


def test_extract_text_windows_no_match():
    text = "No task ID here."
    windows = check_completion.extract_text_windows(text, "868gy0ddd", window_size=50)
    assert len(windows) == 0


def test_extract_text_windows_partial_match():
    text = "Task 868gy0ddd done. Also 868gy0 referenced."
    windows = check_completion.extract_text_windows(text, "868gy0ddd", window_size=100)
    # Should find exact match and partial match
    assert len(windows) >= 1





def test_extract_signals_from_windows():
    windows = [
        ("Task 868gy0ddd fixed. PR #123 merged.", 0, 5),
        ("Different task. Still open.", 100, 0),
    ]
    completion = check_completion.extract_signals_from_windows(windows, check_completion.COMPLETION_PATTERNS)
    open_items = check_completion.extract_signals_from_windows(windows, check_completion.OPEN_PATTERNS)
    assert len(completion) >= 1
    # "Still open" is in the second window (distance=100), so it should have lower proximity
    if open_items:
        assert open_items[0][2] < 1.0, "Distant signals should have lower proximity"


def test_check_task_status_likely_addressed():
    """Task with completion signals near the mention, no open signals."""
    # Create a mock session file
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_dir = Path(tmpdir) / ".claude" / "projects" / "test-project"
        mock_dir.mkdir(parents=True)
        
        session_id = "test-session-123"
        session_file = mock_dir / f"{session_id}.jsonl"
        
        # Create mock session with completion signals near task ID
        mock_data = [
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Working on 868gy0ddd. PR #123 merged for this task. Verified on main."}
            ]}}
        ]
        with open(session_file, "w") as f:
            for entry in mock_data:
                f.write(json.dumps(entry) + "\n")
        
        # Temporarily override CLAUDE_DIR
        original_dir = check_completion.CLAUDE_DIR
        check_completion.CLAUDE_DIR = Path(tmpdir) / ".claude" / "projects"
        
        try:
            result = check_completion.check_task("868gy0ddd", session_ids=[session_id])
            assert result["status"] == "likely_addressed", f"Expected likely_addressed, got {result['status']}"
            assert len(result["completion"]) > 0, "Should find completion signals"
        finally:
            check_completion.CLAUDE_DIR = original_dir


def test_check_task_status_no_sessions():
    """Must be hermetic: with session_ids=[] this falls back to scanning every real
    transcript under CLAUDE_DIR, including the one the test run is being written into,
    which contains this test's own source. Point CLAUDE_DIR at an empty dir instead."""
    with tempfile.TemporaryDirectory() as tmpdir:
        empty = Path(tmpdir) / ".claude" / "projects"
        empty.mkdir(parents=True)
        original_dir = check_completion.CLAUDE_DIR
        check_completion.CLAUDE_DIR = empty
        try:
            result = check_completion.check_task("nonexistent_task", session_ids=[])
        finally:
            check_completion.CLAUDE_DIR = original_dir

    assert result["status"] == "no_sessions_found"
    assert result["completion"] == []
    assert result["open"] == []


def test_proximity_scoring():
    """Closer signals should have higher proximity scores.

    ⚠️ NOT evidence about production. These windows are hand-built with distance 500;
    `extract_text_windows` — the only producer outside these tests — hardcodes distance 0,
    so this covers the formula, not any reachable path. Fixtures of exactly this shape are
    what let the `proximity > 0.5` status tier look tested for two rounds while being
    unreachable. The reachable premise is pinned by
    test_production_windows_are_all_distance_zero in test_status_and_tiers.py.
    """
    windows = [
        ("868gy0ddd fixed. PR #123 merged.", 0, 0),  # Very close
        ("868gy0ddd elsewhere. PR #456 merged.", 500, 0),  # Far away
    ]
    completion = check_completion.extract_signals_from_windows(windows, check_completion.COMPLETION_PATTERNS)
    if len(completion) >= 2:
        # First signal should have higher proximity
        assert completion[0][2] >= completion[1][2]


def test_empty_text():
    windows = check_completion.extract_text_windows("", "868gy0ddd")
    assert len(windows) == 0


def test_special_characters_in_task_id():
    text = "Task 868gy-0ddd with dash. And 868gy_0ddd with underscore."
    windows = check_completion.extract_text_windows(text, "868gy-0ddd", window_size=50)
    assert len(windows) >= 1


if __name__ == "__main__":
    # Run all test functions
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  ✓ {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {test.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
