#!/usr/bin/env python3
"""Tests for search-sessions.py — session transcript search."""
import json, os, sys, tempfile
from pathlib import Path

# Import the module by loading the file directly
SCRIPT_DIR = Path(__file__).parent.parent
import importlib.util
spec = importlib.util.spec_from_file_location("search_sessions", SCRIPT_DIR / "search-sessions.py")
search_sessions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(search_sessions)


def test_load_session_basic():
    """Test loading a session with user and assistant messages."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps({"type": "user", "message": {"content": "Hello"}}) + "\n")
        f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Hi there"}]}}) + "\n")
        f.write(json.dumps({"type": "ai-title", "aiTitle": "Test Session"}) + "\n")
        f.flush()
        
        try:
            entries = search_sessions.load_session(f.name)
            assert len(entries) == 3
            assert entries[0] == ("user", "Hello")
            assert entries[1] == ("assistant", "Hi there")
            assert entries[2] == ("title", "Test Session")
        finally:
            os.unlink(f.name)


def test_load_session_empty():
    """Test loading an empty session."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.flush()
        
        try:
            entries = search_sessions.load_session(f.name)
            assert len(entries) == 0
        finally:
            os.unlink(f.name)


def test_load_session_tool_use():
    """Test loading a session with tool use blocks."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}
        ]}}) + "\n")
        f.flush()
        
        try:
            entries = search_sessions.load_session(f.name)
            assert len(entries) == 1
            assert "Let me check." in entries[0][1]
            assert "ls" in entries[0][1]  # Tool input should be included
        finally:
            os.unlink(f.name)


def test_search_sessions_no_match():
    """Test searching for terms that don't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir) / "projects"
        projects_dir.mkdir()
        
        # Create a mock session
        session_dir = projects_dir / "test-project"
        session_dir.mkdir()
        session_file = session_dir / "session1.jsonl"
        
        with open(session_file, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Hello world"}]}}) + "\n")
        
        # Override CLAUDE_DIR
        original_dir = search_sessions.CLAUDE_DIR
        search_sessions.CLAUDE_DIR = projects_dir
        
        try:
            results = search_sessions.search_sessions(["nonexistent_term_xyz123"])
            assert len(results) == 0
        finally:
            search_sessions.CLAUDE_DIR = original_dir


def test_search_sessions_match():
    """Test searching for terms that exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir) / "projects"
        projects_dir.mkdir()
        
        # Create a mock session
        session_dir = projects_dir / "test-project"
        session_dir.mkdir()
        session_file = session_dir / "session1.jsonl"
        
        with open(session_file, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Task 868gy0ddd was fixed. PR #123 merged."}]}}) + "\n")
        
        # Override CLAUDE_DIR
        original_dir = search_sessions.CLAUDE_DIR
        search_sessions.CLAUDE_DIR = projects_dir
        
        try:
            results = search_sessions.search_sessions(["868gy0ddd"])
            assert len(results) == 1
            assert results[0]["session_id"] == "session1"
            assert results[0]["hits"] >= 1
        finally:
            search_sessions.CLAUDE_DIR = original_dir


def test_search_sessions_multiple_terms():
    """Test searching with multiple terms (AND logic)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir) / "projects"
        projects_dir.mkdir()
        
        # Create a mock session with both terms
        session_dir = projects_dir / "test-project"
        session_dir.mkdir()
        session_file = session_dir / "session1.jsonl"
        
        with open(session_file, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "868gy0ddd was fixed. Exit 0 confirmed."}]}}) + "\n")
        
        # Override CLAUDE_DIR
        original_dir = search_sessions.CLAUDE_DIR
        search_sessions.CLAUDE_DIR = projects_dir
        
        try:
            # Should match (both terms present)
            results = search_sessions.search_sessions(["868gy0ddd", "exit 0"])
            assert len(results) == 1
            
            # Should not match (one term missing)
            results = search_sessions.search_sessions(["868gy0ddd", "nonexistent"])
            assert len(results) == 0
        finally:
            search_sessions.CLAUDE_DIR = original_dir


def test_search_sessions_any_mode():
    """Test searching with --any (OR logic)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir) / "projects"
        projects_dir.mkdir()
        
        # Create a mock session with only one term
        session_dir = projects_dir / "test-project"
        session_dir.mkdir()
        session_file = session_dir / "session1.jsonl"
        
        with open(session_file, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "868gy0ddd was fixed."}]}}) + "\n")
        
        # Override CLAUDE_DIR
        original_dir = search_sessions.CLAUDE_DIR
        search_sessions.CLAUDE_DIR = projects_dir
        
        try:
            # With match_any=True, should match even with only one term
            results = search_sessions.search_sessions(["868gy0ddd", "nonexistent"], match_any=True)
            assert len(results) == 1
        finally:
            search_sessions.CLAUDE_DIR = original_dir


def test_search_sessions_ranking():
    """Test that sessions are ranked by hit count."""
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir) / "projects"
        projects_dir.mkdir()
        
        # Create two sessions with different hit counts
        for i, hits in enumerate([(1, "low"), (3, "high")]):
            session_dir = projects_dir / f"project-{i}"
            session_dir.mkdir()
            session_file = session_dir / f"session{i}.jsonl"
            
            text = "test_term " * hits[0]
            with open(session_file, "w") as f:
                f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}) + "\n")
        
        # Override CLAUDE_DIR
        original_dir = search_sessions.CLAUDE_DIR
        search_sessions.CLAUDE_DIR = projects_dir
        
        try:
            results = search_sessions.search_sessions(["test_term"])
            assert len(results) == 2
            # Higher hit count should come first
            assert results[0]["hits"] >= results[1]["hits"]
        finally:
            search_sessions.CLAUDE_DIR = original_dir


def test_search_sessions_since_filter():
    """Test filtering by date."""
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir) / "projects"
        projects_dir.mkdir()
        
        # Create a mock session
        session_dir = projects_dir / "test-project"
        session_dir.mkdir()
        session_file = session_dir / "session1.jsonl"
        
        with open(session_file, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "test_term"}]}}) + "\n")
        
        # Override CLAUDE_DIR
        original_dir = search_sessions.CLAUDE_DIR
        search_sessions.CLAUDE_DIR = projects_dir
        
        try:
            from datetime import datetime, timedelta
            # Should match (future date)
            results = search_sessions.search_sessions(["test_term"], since=datetime.now() - timedelta(days=1))
            assert len(results) == 1
            
            # Should not match (past date)
            results = search_sessions.search_sessions(["test_term"], since=datetime.now() + timedelta(days=1))
            assert len(results) == 0
        finally:
            search_sessions.CLAUDE_DIR = original_dir


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
