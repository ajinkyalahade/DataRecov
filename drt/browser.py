"""
Browser data recovery.

Locates Chrome, Firefox, and Edge profile directories, copies SQLite
databases to a temp location (to avoid browser lock), and extracts
browsing history.

Uses stdlib sqlite3. Windows-only (uses %LOCALAPPDATA% / %APPDATA%).
"""

import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chrome_time_to_iso(chrome_time: int) -> str:
    """
    Convert Chrome timestamp (microseconds since 1601-01-01) to ISO8601 string.
    Chrome uses the same FILETIME epoch as Windows.
    """
    if chrome_time <= 0:
        return ""
    EPOCH_DIFF_US = 11_644_473_600_000_000  # microseconds from 1601 to 1970
    unix_us = chrome_time - EPOCH_DIFF_US
    try:
        dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=unix_us)
        return dt.isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _firefox_time_to_iso(firefox_time: int) -> str:
    """
    Convert Firefox timestamp (microseconds since Unix epoch) to ISO8601 string.
    """
    if not firefox_time:
        return ""
    try:
        dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=firefox_time)
        return dt.isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _query_sqlite(db_path: Path, query: str) -> list[tuple]:
    """
    Copy db_path to a temp file, run query, return rows.
    Returns [] on any error.
    """
    try:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name
        shutil.copy2(str(db_path), tmp_path)
        try:
            conn = sqlite3.connect(tmp_path)
            try:
                cur = conn.execute(query)
                return cur.fetchall()
            finally:
                conn.close()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Profile discovery
# ---------------------------------------------------------------------------

def _profile_paths(browser: str) -> list[Path]:
    """
    Return known profile directory paths for chrome, firefox, or edge.
    Uses %LOCALAPPDATA% and %APPDATA% environment variables.
    """
    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")
    paths: list[Path] = []

    if browser == "chrome":
        if local:
            base = Path(local) / "Google" / "Chrome" / "User Data"
            if base.is_dir():
                # Default profile
                paths.append(base / "Default")
                # Additional profiles: Profile 1, Profile 2, …
                for item in base.iterdir():
                    if item.is_dir() and item.name.startswith("Profile"):
                        paths.append(item)

    elif browser == "edge":
        if local:
            base = Path(local) / "Microsoft" / "Edge" / "User Data"
            if base.is_dir():
                paths.append(base / "Default")
                for item in base.iterdir():
                    if item.is_dir() and item.name.startswith("Profile"):
                        paths.append(item)

    elif browser == "firefox":
        if roaming:
            profiles_ini = Path(roaming) / "Mozilla" / "Firefox" / "profiles.ini"
            if profiles_ini.is_file():
                # Parse profiles.ini to find profile directories
                try:
                    content = profiles_ini.read_text(encoding="utf-8", errors="replace")
                    base_dir = profiles_ini.parent
                    for line in content.splitlines():
                        line = line.strip()
                        if line.lower().startswith("path="):
                            rel_path = line[5:].strip()
                            profile_path = base_dir / rel_path
                            if profile_path.is_dir():
                                paths.append(profile_path)
                except Exception:
                    pass

    return paths


# ---------------------------------------------------------------------------
# History extraction
# ---------------------------------------------------------------------------

def extract_chrome_history(profile_dir: Path) -> list[dict]:
    """
    Read Chrome's History SQLite file.
    Returns list of {url, title, visit_count, last_visit_time, browser}.
    Returns [] on any error.
    """
    db = profile_dir / "History"
    if not db.is_file():
        return []

    rows = _query_sqlite(
        db,
        "SELECT url, title, visit_count, last_visit_time "
        "FROM urls ORDER BY last_visit_time DESC LIMIT 1000",
    )
    results: list[dict] = []
    for row in rows:
        url, title, visit_count, last_visit_time = row
        results.append({
            "url":             url or "",
            "title":           title or "",
            "visit_count":     visit_count or 0,
            "last_visit_time": _chrome_time_to_iso(last_visit_time or 0),
            "browser":         "chrome",
        })
    return results


def extract_firefox_history(profile_dir: Path) -> list[dict]:
    """
    Read Firefox's places.sqlite.
    Returns list of {url, title, visit_count, last_visit_time, browser}.
    Returns [] on any error.
    """
    db = profile_dir / "places.sqlite"
    if not db.is_file():
        return []

    rows = _query_sqlite(
        db,
        "SELECT url, title, visit_count, last_visit_date "
        "FROM moz_places ORDER BY last_visit_date DESC LIMIT 1000",
    )
    results: list[dict] = []
    for row in rows:
        url, title, visit_count, last_visit_date = row
        results.append({
            "url":             url or "",
            "title":           title or "",
            "visit_count":     visit_count or 0,
            "last_visit_time": _firefox_time_to_iso(last_visit_date or 0),
            "browser":         "firefox",
        })
    return results


def _extract_edge_history(profile_dir: Path) -> list[dict]:
    """Edge uses the same Chromium schema as Chrome."""
    rows_raw = extract_chrome_history(profile_dir)
    for row in rows_raw:
        row["browser"] = "edge"
    return rows_raw


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def scan() -> dict:
    """
    Scan all browsers and return:
    {
      "chrome":  [...history dicts...],
      "firefox": [...],
      "edge":    [...],
    }
    Missing profiles return empty lists.
    """
    result: dict = {"chrome": [], "firefox": [], "edge": []}

    for profile_dir in _profile_paths("chrome"):
        try:
            result["chrome"].extend(extract_chrome_history(profile_dir))
        except Exception:
            pass

    for profile_dir in _profile_paths("firefox"):
        try:
            result["firefox"].extend(extract_firefox_history(profile_dir))
        except Exception:
            pass

    for profile_dir in _profile_paths("edge"):
        try:
            result["edge"].extend(_extract_edge_history(profile_dir))
        except Exception:
            pass

    return result
