"""
Write recovered files to a structured output directory.

Output hierarchy mirrors the type groups defined in types.py:
    <base_dir>/
        Documents/   pdf/ doc/ docx/ ...
        Images/      jpg/ png/ ...
        Videos/      mp4/ avi/ ...
        Audio/       mp3/ wav/ ...
        Archives/    zip/ rar/ ...
        Email/       pst/ eml/ ...
        Databases/   sqlite/ mdb/ ...
        Executables/ exe/ dll/ ...
        Code/        py/ js/ ...
        Browser/     db/ json/ ...
        Artifacts/   lnk/ evtx/ ...
        VirtualDisks/ vhd/ vmdk/ ...
        Unclassified/
"""

from pathlib import Path


# ---------------------------------------------------------------------------
# Extension → category subdir mapping
# ---------------------------------------------------------------------------

_EXT_TO_CATEGORY: dict[str, str] = {
    # Documents
    ".pdf":   "Documents",
    ".doc":   "Documents",
    ".docx":  "Documents",
    ".xls":   "Documents",
    ".xlsx":  "Documents",
    ".ppt":   "Documents",
    ".pptx":  "Documents",
    ".txt":   "Documents",
    ".rtf":   "Documents",
    ".csv":   "Documents",
    ".xml":   "Documents",
    ".json":  "Documents",
    ".html":  "Documents",
    ".htm":   "Documents",
    ".odt":   "Documents",
    ".ods":   "Documents",
    ".odp":   "Documents",
    # Images
    ".jpg":   "Images",
    ".jpeg":  "Images",
    ".png":   "Images",
    ".bmp":   "Images",
    ".gif":   "Images",
    ".tiff":  "Images",
    ".tif":   "Images",
    ".webp":  "Images",
    ".heic":  "Images",
    ".cr2":   "Images",
    ".nef":   "Images",
    ".arw":   "Images",
    ".dng":   "Images",
    ".raw":   "Images",
    # Videos
    ".mp4":   "Videos",
    ".avi":   "Videos",
    ".mkv":   "Videos",
    ".mov":   "Videos",
    ".wmv":   "Videos",
    ".flv":   "Videos",
    ".m4v":   "Videos",
    ".3gp":   "Videos",
    ".mpg":   "Videos",
    ".mpeg":  "Videos",
    # Audio
    ".mp3":   "Audio",
    ".wav":   "Audio",
    ".flac":  "Audio",
    ".aac":   "Audio",
    ".ogg":   "Audio",
    ".wma":   "Audio",
    ".m4a":   "Audio",
    # Archives
    ".zip":   "Archives",
    ".rar":   "Archives",
    ".7z":    "Archives",
    ".tar":   "Archives",
    ".gz":    "Archives",
    ".cab":   "Archives",
    ".iso":   "Archives",
    # Email
    ".pst":   "Email",
    ".ost":   "Email",
    ".eml":   "Email",
    ".msg":   "Email",
    ".mbox":  "Email",
    # Databases
    ".db":      "Databases",
    ".sqlite":  "Databases",
    ".sqlite3": "Databases",
    ".mdb":     "Databases",
    ".accdb":   "Databases",
    ".sql":     "Databases",
    # Executables
    ".exe":   "Executables",
    ".dll":   "Executables",
    ".sys":   "Executables",
    ".msi":   "Executables",
    ".bat":   "Executables",
    ".ps1":   "Executables",
    # Code
    ".py":    "Code",
    ".js":    "Code",
    ".ts":    "Code",
    ".java":  "Code",
    ".cs":    "Code",
    ".cpp":   "Code",
    ".c":     "Code",
    ".h":     "Code",
    ".env":   "Code",
    ".yaml":  "Code",
    ".yml":   "Code",
    ".toml":  "Code",
    ".ini":   "Code",
    ".cfg":   "Code",
    # Browser artifacts (SQLite/JSON already handled above, .ldb is Chrome LevelDB)
    ".ldb":   "Browser",
    # Windows artifacts
    ".lnk":   "Artifacts",
    ".evtx":  "Artifacts",
    ".reg":   "Artifacts",
    ".pf":    "Artifacts",
    ".dat":   "Artifacts",
    # Virtual disks
    ".vhd":   "VirtualDisks",
    ".vhdx":  "VirtualDisks",
    ".vmdk":  "VirtualDisks",
}

# All top-level category directories that will be pre-created
_ALL_CATEGORIES: list[str] = [
    "Documents", "Images", "Videos", "Audio", "Archives",
    "Email", "Databases", "Executables", "Code",
    "Browser", "Artifacts", "VirtualDisks", "Unclassified",
]


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _category_for(extension: str) -> str:
    """Return the category directory name for a given extension."""
    return _EXT_TO_CATEGORY.get(extension.lower(), "Unclassified")


def get_output_path(base_dir: str, extension: str, index: int) -> Path:
    """
    Return the output path for a recovered file.

    Example: base_dir/Images/jpg/recovered_0001.jpg
    """
    # Normalise: ensure the extension always starts with a dot
    ext = extension.lower()
    if ext and not ext.startswith("."):
        ext = "." + ext
    ext_clean = ext.lstrip(".")
    category  = _category_for(ext)
    filename  = f"recovered_{index:04d}{ext}" if ext else f"recovered_{index:04d}.bin"
    return Path(base_dir) / category / ext_clean / filename


def ensure_structure(base_dir: str) -> None:
    """
    Create all category subdirectories under base_dir upfront.
    Idempotent — safe to call multiple times.
    """
    root = Path(base_dir)
    root.mkdir(parents=True, exist_ok=True)
    for category in _ALL_CATEGORIES:
        (root / category).mkdir(exist_ok=True)


def write_file(base_dir: str, extension: str, data: bytes, index: int) -> Path:
    """
    Write recovered bytes to the structured output directory.

    Creates the per-extension subdirectory if it does not exist.
    Returns the Path of the written file.
    """
    out_path = get_output_path(base_dir, extension, index)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    return out_path
