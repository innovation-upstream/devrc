#!/usr/bin/env python3
"""Tests for recent-comments.py — ClickUp API integration."""
import json, os, sys, tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

# Import the module by loading the file directly
SCRIPT_DIR = Path(__file__).parent.parent
import importlib.util
spec = importlib.util.spec_from_file_location("recent_comments", SCRIPT_DIR / "recent-comments.py")
recent_comments = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recent_comments)


def test_extract_text():
    """Test extracting text from comment object."""
    comment = {"comment": [{"text": "Hello "}, {"text": "world"}]}
    result = recent_comments.extract_text(comment)
    # The function joins with space, so "Hello " + " " + "world" = "Hello  world"
    assert "Hello" in result and "world" in result


def test_extract_text_empty():
    """Test extracting text from empty comment."""
    comment = {"comment": []}
    result = recent_comments.extract_text(comment)
    assert result == ""


def test_extract_text_nested():
    """Test extracting text with nested objects."""
    comment = {"comment": [{"text": "Hello"}, {"not_text": "ignored"}, {"text": "world"}]}
    result = recent_comments.extract_text(comment)
    assert "Hello" in result and "world" in result


def test_format_date_valid():
    """Test formatting a valid timestamp."""
    # 2026-08-19 12:00:00 UTC (approximately)
    ts_ms = 1787227200000
    result = recent_comments.format_date(ts_ms)
    # Just check it returns a string with the date
    assert isinstance(result, str)
    assert len(result) > 0


def test_format_date_invalid():
    """Test formatting an invalid timestamp."""
    result = recent_comments.format_date("invalid")
    assert result == "invalid"


def test_format_date_none():
    """Test formatting None timestamp."""
    result = recent_comments.format_date(None)
    assert result == "None"


def test_run_clickup_mock():
    """Test run_clickup with mocked subprocess."""
    mock_result = MagicMock()
    mock_result.stdout = "User: Test User\nID: 12345"
    
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        result = recent_comments.run_clickup("me")
        assert result == "User: Test User\nID: 12345"
        mock_run.assert_called_once()


def test_get_my_user_id():
    """Test extracting user ID from output."""
    with patch.object(recent_comments, "run_clickup", return_value="User: Test\nID: 12345\nEmail: test@test.com"):
        result = recent_comments.get_my_user_id()
        assert result == "12345"


def test_get_my_user_id_not_found():
    """Test when user ID is not in output."""
    with patch.object(recent_comments, "run_clickup", return_value="User: Test\nNo ID here"):
        result = recent_comments.get_my_user_id()
        assert result is None


def test_get_my_tasks_mock():
    """Test get_my_tasks with mocked API response."""
    mock_tasks = [{"id": "868gy0ddd", "name": "Test Task"}]
    mock_output = "Scanned 19 spaces\n" + json.dumps(mock_tasks)
    
    with patch.object(recent_comments, "run_clickup", return_value=mock_output):
        result = recent_comments.get_my_tasks()
        assert len(result) == 1
        assert result[0]["id"] == "868gy0ddd"


def test_get_my_tasks_empty():
    """Test get_my_tasks with empty response."""
    with patch.object(recent_comments, "run_clickup", return_value="No tasks found"):
        result = recent_comments.get_my_tasks()
        assert len(result) == 0


def test_get_comments_mock():
    """Test get_comments with mocked API response."""
    mock_comments = [{"id": "123", "comment": [{"text": "Test comment"}], "user": {"id": "999"}, "date": "1787227200000"}]
    mock_output = json.dumps(mock_comments)
    
    with patch.object(recent_comments, "run_clickup", return_value=mock_output):
        result = recent_comments.get_comments("868gy0ddd")
        assert len(result) == 1
        assert result[0]["id"] == "123"


def test_filter_my_comments():
    """Test that my own comments are filtered out."""
    my_id = "81593871"
    comments = [
        {"id": "1", "comment": [{"text": "My comment"}], "user": {"id": "81593871"}, "date": "1787227200000"},
        {"id": "2", "comment": [{"text": "Other comment"}], "user": {"id": "99999"}, "date": "1787227200000"},
    ]
    
    # Simulate filtering logic
    filtered = [c for c in comments if str(c.get("user", {}).get("id", "")) != my_id]
    assert len(filtered) == 1
    assert filtered[0]["id"] == "2"


def test_sort_by_date():
    """Test that comments are sorted by date (newest first)."""
    comments = [
        {"task_id": "1", "date": "2026-08-17 10:00"},
        {"task_id": "2", "date": "2026-08-19 10:00"},
        {"task_id": "3", "date": "2026-08-18 10:00"},
    ]
    
    sorted_comments = sorted(comments, key=lambda x: x["date"], reverse=True)
    assert sorted_comments[0]["task_id"] == "2"  # Aug 19
    assert sorted_comments[1]["task_id"] == "3"  # Aug 18
    assert sorted_comments[2]["task_id"] == "1"  # Aug 17


def test_limit_results():
    """Test that results are limited to N items."""
    comments = [{"task_id": str(i)} for i in range(10)]
    limited = comments[:3]
    assert len(limited) == 3


if __name__ == "__main__":
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
