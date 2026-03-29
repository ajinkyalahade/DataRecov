"""
Type group definitions — maps group keys to file extensions and descriptions.
The 'all' group is computed at runtime as the union of all other groups.
"""

from typing import TypedDict


class TypeGroup(TypedDict):
    extensions: list[str]
    description: str


_GROUPS: dict[str, TypeGroup] = {
    "documents": {
        "extensions": [
            ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
            ".txt", ".rtf", ".csv", ".xml", ".json", ".html", ".htm",
            ".odt", ".ods", ".odp",
        ],
        "description": "PDF, Office (doc/xls/ppt), text, CSV, XML, HTML, OpenDocument",
    },
    "images": {
        "extensions": [
            ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".tif",
            ".webp", ".heic", ".cr2", ".nef", ".arw", ".dng", ".raw",
        ],
        "description": "JPEG, PNG, BMP, GIF, TIFF, WebP, HEIC, RAW camera formats",
    },
    "videos": {
        "extensions": [
            ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv",
            ".m4v", ".3gp", ".mpg", ".mpeg",
        ],
        "description": "MP4, AVI, MKV, MOV, WMV, FLV, MPG and variants",
    },
    "audio": {
        "extensions": [
            ".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a",
        ],
        "description": "MP3, WAV, FLAC, AAC, OGG, WMA, M4A",
    },
    "archives": {
        "extensions": [
            ".zip", ".rar", ".7z", ".tar", ".gz", ".cab", ".iso",
        ],
        "description": "ZIP, RAR, 7Z, TAR, GZ, CAB, ISO",
    },
    "email": {
        "extensions": [
            ".pst", ".ost", ".eml", ".msg", ".mbox",
        ],
        "description": "PST/OST (Outlook), EML, MSG, MBOX",
    },
    "databases": {
        "extensions": [
            ".db", ".sqlite", ".sqlite3", ".mdb", ".accdb", ".sql",
        ],
        "description": "SQLite, MDB/ACCDB (Access), SQL dumps",
    },
    "executables": {
        "extensions": [
            ".exe", ".dll", ".sys", ".msi", ".bat", ".ps1",
        ],
        "description": "EXE, DLL, SYS, MSI, BAT, PowerShell scripts",
    },
    "code": {
        "extensions": [
            ".py", ".js", ".ts", ".java", ".cs", ".cpp", ".c", ".h",
            ".env", ".yaml", ".yml", ".toml", ".ini", ".cfg",
        ],
        "description": "Source code (Python, JS/TS, Java, C#, C++), config files",
    },
    "browser": {
        "extensions": [
            ".db", ".sqlite", ".sqlite3", ".json", ".ldb",
        ],
        "description": "Chrome/Firefox/Edge history, cache, bookmarks, passwords (SQLite/JSON)",
    },
    "artifacts": {
        "extensions": [
            ".lnk", ".evtx", ".reg", ".pf", ".dat",
        ],
        "description": "Recycle Bin, LNK shortcuts, Event Logs, Prefetch, Registry hives",
    },
    "virtual_disks": {
        "extensions": [
            ".vhd", ".vhdx", ".vmdk",
        ],
        "description": "Hyper-V VHD/VHDX, VMware VMDK virtual disk images",
    },
}


def get_group(key: str) -> TypeGroup:
    """Return a single type group by key, including computed 'all'."""
    if key == "all":
        return _build_all_group()
    if key not in _GROUPS:
        raise KeyError(f"Unknown type group: {key!r}. Valid groups: {list_group_keys()}")
    return _GROUPS[key]


def _build_all_group() -> TypeGroup:
    """Compute the 'all' meta-group as the union of all group extensions."""
    all_extensions: set[str] = set()
    for group in _GROUPS.values():
        all_extensions.update(group["extensions"])
    return {
        "extensions": sorted(all_extensions),
        "description": "All recoverable file types (union of every group)",
    }


def list_group_keys() -> list[str]:
    """Return all valid group keys including 'all'."""
    return list(_GROUPS.keys()) + ["all"]


def resolve_groups(keys: list[str]) -> dict[str, TypeGroup]:
    """
    Resolve a list of group keys to their definitions.
    If 'all' is present, returns every group including 'all'.
    """
    if "all" in keys:
        result = dict(_GROUPS)
        result["all"] = _build_all_group()
        return result
    return {k: get_group(k) for k in keys}


def get_extensions_for_groups(keys: list[str]) -> set[str]:
    """Return the flat set of extensions covered by the given group keys."""
    extensions: set[str] = set()
    for key in keys:
        group = get_group(key)
        extensions.update(group["extensions"])
    return extensions


def all_groups() -> dict[str, TypeGroup]:
    """Return all named groups (excludes the computed 'all' meta-group)."""
    return dict(_GROUPS)
