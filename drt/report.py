"""
Scan report builder.

Creates, populates, and writes scan_report.json to the output directory.
The report is a plain dict serialised as JSON — no external schema library.
"""

import json
from datetime import datetime, timezone
from pathlib import Path


_TOOL_NAME    = "DataRecoveryTool"
_TOOL_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Group membership lookup (extension → group key)
# Derived from types.py at import time to keep report.py self-contained.
# ---------------------------------------------------------------------------

def _build_ext_to_group() -> dict[str, str]:
    from drt.types import all_groups
    mapping: dict[str, str] = {}
    for group_key, group_data in all_groups().items():
        for ext in group_data["extensions"]:
            if ext not in mapping:
                mapping[ext] = group_key
    return mapping


_EXT_TO_GROUP: dict[str, str] = {}  # lazily populated on first use


def _ext_group(extension: str) -> str:
    global _EXT_TO_GROUP
    if not _EXT_TO_GROUP:
        _EXT_TO_GROUP = _build_ext_to_group()
    ext = extension.lower()
    return _EXT_TO_GROUP.get(ext, "unclassified")


# ---------------------------------------------------------------------------
# Report lifecycle
# ---------------------------------------------------------------------------

def new_report(drive: str, depth: str, type_groups: list[str]) -> dict:
    """Create and return a fresh report skeleton."""
    return {
        "tool":            _TOOL_NAME,
        "version":         _TOOL_VERSION,
        "scan_date":       datetime.now(timezone.utc).isoformat(),
        "drive":           drive,
        "depth":           depth,
        "type_groups":     list(type_groups),
        "duration_seconds": 0.0,
        "stats": {
            "total_files_found":    0,
            "by_type":              {},
            "by_group":             {},
            "total_bytes_recovered": 0,
        },
        "files": [],
    }


def add_found_file(
    report: dict,
    extension: str,
    disk_offset: int,
    output_path: str,
    size_bytes: int,
) -> None:
    """Append a recovered file record and update all running stats."""
    ext   = extension.lower()
    group = _ext_group(ext)

    # File list entry
    report["files"].append({
        "extension":   ext,
        "disk_offset": disk_offset,
        "output_path": output_path,
        "size_bytes":  size_bytes,
    })

    # Aggregate stats
    stats = report["stats"]
    stats["total_files_found"] += 1
    stats["total_bytes_recovered"] += size_bytes

    ext_key = ext.lstrip(".")
    stats["by_type"][ext_key]  = stats["by_type"].get(ext_key, 0) + 1
    stats["by_group"][group]   = stats["by_group"].get(group, 0) + 1


def finalize_report(report: dict, duration_seconds: float) -> None:
    """Set the elapsed duration on the report (mutates in place)."""
    report["duration_seconds"] = round(duration_seconds, 3)


def write_report(report: dict, output_dir: str) -> Path:
    """
    Write scan_report.json to output_dir.
    Returns the Path of the written file.
    """
    out_path = Path(output_dir) / "scan_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, indent=2, default=str),
        encoding="utf-8",
    )
    return out_path
