#!/usr/bin/env python3
"""Search opencode session transcripts stored in SQLite.

opencode stores session content in `~/.local/share/opencode/opencode-stable.db`.
Each session's text lives in the `part` table as JSON blobs in the `data` column.
This module queries that database and returns results compatible with
`transcript_search.py` so `find-session.py` can merge both sources.

The database is per-host; both hosts are checked when the other is reachable via SSH.
"""
import json
import os
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SNIPPET_PAD = 50
GENESIS_CHARS = 200

# Both hosts' DB paths, checked in order.
LOCAL_DB = Path.home() / ".local" / "share" / "opencode" / "opencode-stable.db"
REMOTE_HOST = "zach@10.42.0.30"
REMOTE_DB = Path("/home/zach/.local/share/opencode/opencode-stable.db")


def _local_naive(dt):
    """A tz-aware datetime as naive local, matching transcript_search convention."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone().replace(tzinfo=None)


def _parse_ts(millis):
    """Millisecond epoch as an aware datetime, or None."""
    if not millis:
        return None
    try:
        return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)
    except (OSError, ValueError):
        return None


def _extract_text(data_str):
    """Pull all text content from a part's JSON data blob."""
    try:
        data = json.loads(data_str) if isinstance(data_str, str) else data_str
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    parts = []
    # text parts
    if data.get("type") == "text" and "text" in data:
        parts.append(data["text"])
    # reasoning blocks
    for rd in data.get("reasoning_details", []):
        if isinstance(rd, dict) and "text" in rd:
            parts.append(rd["text"])
    # nested content in tool results
    content = data.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for sub in content:
            if isinstance(sub, dict) and sub.get("type") == "text":
                parts.append(sub.get("text", ""))
    return "\n".join(parts)


def _query_db(db_path, terms, patterns, *, match_any=False, since=None, project="",
              limit=None):
    """Search one opencode database. Returns list of result dicts."""
    if not db_path.exists():
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # Build session query
        sess_sql = "SELECT id, title, directory, time_created, time_updated FROM session"
        sess_params = []
        conditions = []
        if since is not None:
            # since is naive local; compare against time_created (millis epoch)
            since_utc = since.replace(tzinfo=timezone.utc)
            conditions.append("time_created >= ?")
            sess_params.append(int(since_utc.timestamp() * 1000))
        if project:
            conditions.append("LOWER(directory) LIKE ?")
            sess_params.append(f"%{project.lower()}%")
        if conditions:
            sess_sql += " WHERE " + " AND ".join(conditions)
        sess_sql += " ORDER BY time_created DESC"

        sessions = conn.execute(sess_sql, sess_params).fetchall()
        if not sessions:
            return []

        # For each session, search its parts
        results = []
        needle_lower = [t.lower() for t in terms]

        for sess in sessions:
            sess_id = sess["id"]
            title = sess["title"] or ""
            directory = sess["directory"] or ""
            ts_created = _parse_ts(sess["time_created"])
            ts_updated = _parse_ts(sess["time_updated"])

            # Search title first
            title_lower = title.lower()
            title_hits = {t: (1 if t.lower() in title_lower else 0) for t in terms}

            # Search part content
            part_rows = conn.execute(
                "SELECT data FROM part WHERE session_id = ?", (sess_id,)
            ).fetchall()

            term_hits = dict(title_hits)
            snippets = {}
            genesis = ""
            first_role = "title"

            for row in part_rows:
                text = _extract_text(row["data"])
                if not text:
                    continue

                # Capture genesis (first substantive user text)
                if not genesis and len(text.strip()) > 10:
                    clean = text.strip()[:GENESIS_CHARS]
                    if not clean.startswith("<") and not clean.startswith("Caveat:"):
                        genesis = clean
                        first_role = "assistant"

                for term, pat in zip(terms, patterns):
                    found = pat.findall(text)
                    if found:
                        term_hits[term] += len(found)
                        if term not in snippets:
                            m = pat.search(text)
                            start, end = max(0, m.start() - SNIPPET_PAD), m.end() + SNIPPET_PAD
                            snippets[term] = ("claude", text[start:end].replace("\n", " ").strip())

            matched_terms = [t for t in terms if term_hits[t] > 0]
            ok = bool(matched_terms) if match_any else len(matched_terms) == len(terms)
            if not ok:
                continue

            # Compute last timestamp
            last_local = _local_naive(ts_updated) if ts_updated else (
                _local_naive(ts_created) if ts_created else datetime.now()
            )

            results.append({
                "session_id": sess_id,
                "path": f"opencode:{db_path}",
                "cwd": directory,
                "branch": "",
                "title": title,
                "genesis": genesis or f"[title] {title}",
                "opening": genesis or f"[title] {title}",
                "first": ts_created.isoformat() if ts_created else "",
                "last": ts_updated.isoformat() if ts_updated else "",
                "last_local": last_local,
                "matched_terms": matched_terms,
                "term_hits": {t: term_hits[t] for t in matched_terms},
                "total_hits": sum(term_hits[t] for t in matched_terms),
                "snippets": snippets,
                "project_dir": "",
                "source": "opencode",
            })

        return results
    finally:
        conn.close()


def search_opencode(terms, *, match_any=False, since=None, project="", limit=None):
    """Search opencode sessions on local + remote hosts.

    Returns results in the same dict format as transcript_search.search(),
    with an extra "source": "opencode" field.

    The remote host is queried via SSH (running sqlite3 there) rather than SCP,
    because the database is ~1.5 GB and would time out over scp.
    """
    patterns = [re.compile(re.escape(t), re.I) for t in terms]
    all_results = []

    # Local
    all_results.extend(_query_db(LOCAL_DB, terms, patterns,
                                 match_any=match_any, since=since, project=project))

    # Remote (best-effort SSH query — no file transfer needed)
    try:
        remote_results = _query_remote(terms, patterns, match_any=match_any,
                                       since=since, project=project)
        all_results.extend(remote_results)
    except Exception:
        pass  # Remote unreachable; skip silently

    # Sort by relevance + recency, same as transcript_search
    all_results.sort(
        key=lambda r: (len(r["matched_terms"]), r["total_hits"], r["last_local"]),
        reverse=True
    )

    if limit is not None:
        all_results = all_results[:limit]

    return all_results


def _query_remote(terms, patterns, *, match_any=False, since=None, project=""):
    """Query the workbench's opencode DB via SSH. Returns result dicts."""
    search_payload = json.dumps({
        "terms": terms,
        "match_any": match_any,
        "since": since.isoformat() if since else None,
        "project": project,
    })
    try:
        # Write the script to a temp file on the remote, then execute it.
        # This avoids shell-escaping the multi-line Python through SSH+zsh.
        write_cmd = f"cat > /tmp/_oc_search.py << 'PYEOF'\n{_REMOTE_SEARCH_SCRIPT}\nPYEOF"
        proc = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
             REMOTE_HOST, write_cmd],
            capture_output=True, timeout=10
        )
        if proc.returncode != 0:
            return []
        # Now run the script with payload on stdin
        proc = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
             REMOTE_HOST, "python3 /tmp/_oc_search.py"],
            input=search_payload.encode(),
            capture_output=True, timeout=30
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return []
        remote_data = json.loads(proc.stdout.decode())
        # Convert last_local strings back to datetime for sorting
        for r in remote_data:
            try:
                r["last_local"] = datetime.fromisoformat(r["last_local"])
            except (ValueError, TypeError):
                r["last_local"] = datetime.now()
        return remote_data
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError, OSError):
        return []


# The script that runs on the remote host. Receives JSON config as the last line on stdin.
_REMOTE_SEARCH_SCRIPT = r'''
import json, sqlite3, re, os, sys
from datetime import datetime, timezone

# Read all lines; the last non-empty line is the JSON payload
lines = sys.stdin.read().strip().split("\n")
payload = json.loads(lines[-1])
terms = payload["terms"]
match_any = payload["match_any"]
since_str = payload["since"]
project = payload["project"]

since = None
if since_str:
    since = datetime.fromisoformat(since_str)

db = "/home/zach/.local/share/opencode/opencode-stable.db"
if not os.path.exists(db):
    print("[]")
    exit()

conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row

sess_sql = "SELECT id, title, directory, time_created, time_updated FROM session"
params = []
conds = []
if since:
    since_ms = int(since.replace(tzinfo=timezone.utc).timestamp() * 1000)
    conds.append("time_created >= ?")
    params.append(since_ms)
if project:
    conds.append("LOWER(directory) LIKE ?")
    params.append(f"%{project.lower()}%")
if conds:
    sess_sql += " WHERE " + " AND ".join(conds)
sess_sql += " ORDER BY time_created DESC"

sessions = conn.execute(sess_sql, params).fetchall()
results = []
patterns = [re.compile(re.escape(t), re.I) for t in terms]

for sess in sessions:
    sid = sess["id"]
    title = sess["title"] or ""
    directory = sess["directory"] or ""
    tc = sess["time_created"]
    tu = sess["time_updated"]

    title_lower = title.lower()
    term_hits = {t: (1 if t.lower() in title_lower else 0) for t in terms}

    parts = conn.execute("SELECT data FROM part WHERE session_id = ?", (sid,)).fetchall()
    snippets = {}
    genesis = ""

    for row in parts:
        try:
            data = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        text = ""
        if data.get("type") == "text" and "text" in data:
            text = data["text"]
        if not text:
            continue
        if not genesis and len(text.strip()) > 10:
            c = text.strip()[:200]
            if not c.startswith("<") and not c.startswith("Caveat:"):
                genesis = c
        for term, pat in zip(terms, patterns):
            found = pat.findall(text)
            if found:
                term_hits[term] += len(found)
                if term not in snippets:
                    m = pat.search(text)
                    s, e = max(0, m.start() - 50), m.end() + 50
                    snippets[term] = ("claude", text[s:e].replace("\n", " ").strip())

    matched = [t for t in terms if term_hits[t] > 0]
    ok = bool(matched) if match_any else len(matched) == len(terms)
    if not ok:
        continue

    last_local = datetime.fromtimestamp(tu / 1000, tz=timezone.utc).replace(tzinfo=None) if tu else (
        datetime.fromtimestamp(tc / 1000, tz=timezone.utc).replace(tzinfo=None) if tc else datetime.now()
    )

    results.append({
        "session_id": sid,
        "path": "opencode:workbench",
        "cwd": directory,
        "branch": "",
        "title": title,
        "genesis": genesis or f"[title] {title}",
        "opening": genesis or f"[title] {title}",
        "first": datetime.fromtimestamp(tc / 1000, tz=timezone.utc).isoformat() if tc else "",
        "last": datetime.fromtimestamp(tu / 1000, tz=timezone.utc).isoformat() if tu else "",
        "last_local": last_local.isoformat() if isinstance(last_local, datetime) else str(last_local),
        "matched_terms": matched,
        "term_hits": {t: term_hits[t] for t in matched},
        "total_hits": sum(term_hits[t] for t in matched),
        "snippets": snippets,
        "project_dir": "",
        "source": "opencode",
    })

conn.close()
print(json.dumps(results, default=str))
'''
