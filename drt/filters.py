"""
Scan result filters — applied to each found file before writing.
"""

from datetime import datetime, timezone


def parse_size(s: str) -> int:
    """
    Parse a human size string to bytes.
    Accepts: "512", "1KB", "500KB", "10MB", "2GB", "1TB"
    Case-insensitive. Raises ValueError on bad input.
    """
    s = s.strip()
    if not s:
        raise ValueError("Empty size string")

    _UNITS: dict[str, int] = {
        "B":  1,
        "KB": 1024,
        "MB": 1024 * 1024,
        "GB": 1024 * 1024 * 1024,
        "TB": 1024 * 1024 * 1024 * 1024,
    }

    upper = s.upper()
    for suffix, multiplier in sorted(_UNITS.items(), key=lambda kv: -len(kv[0])):
        if upper.endswith(suffix):
            number_part = s[: len(s) - len(suffix)].strip()
            try:
                return int(float(number_part) * multiplier)
            except ValueError:
                raise ValueError(f"Cannot parse size: {s!r}")

    # No unit — treat as plain byte count
    try:
        return int(s)
    except ValueError:
        raise ValueError(f"Cannot parse size: {s!r}")


def parse_date(s: str) -> float:
    """
    Parse YYYY-MM-DD to a Unix timestamp (float).
    Raises ValueError on bad format.
    """
    s = s.strip()
    try:
        dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        raise ValueError(f"Cannot parse date: {s!r} — expected YYYY-MM-DD")


def make_filter(
    min_size: int | None,
    max_size: int | None,
    after_ts: float | None,
    before_ts: float | None,
):
    """
    Return a filter function: f(size_bytes, mtime_ts) -> bool
    Returns True if the file should be kept, False if it should be skipped.
    mtime_ts may be None (unknown) — date filters pass unknown mtimes through.
    """
    def _filter(size_bytes: int, mtime_ts: float | None) -> bool:
        # Size filters
        if min_size is not None and size_bytes < min_size:
            return False
        if max_size is not None and size_bytes > max_size:
            return False

        # Date filters — unknown mtime passes through
        if mtime_ts is not None:
            if after_ts is not None and mtime_ts < after_ts:
                return False
            if before_ts is not None and mtime_ts > before_ts:
                return False

        return True

    return _filter
