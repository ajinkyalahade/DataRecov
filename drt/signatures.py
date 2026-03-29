"""
File magic byte database.

Format: { ".ext": [{"magic": b"...", "offset": 0}, ...], ... }

Each entry represents one possible magic byte pattern for that extension.
offset is the byte position within the file where the magic appears.
"""

# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------
_IMAGES: dict[str, list[dict]] = {
    ".jpg": [{"magic": b"\xFF\xD8\xFF\xE0", "offset": 0},
             {"magic": b"\xFF\xD8\xFF\xE1", "offset": 0},
             {"magic": b"\xFF\xD8\xFF\xE2", "offset": 0},
             {"magic": b"\xFF\xD8\xFF\xDB", "offset": 0}],
    ".jpeg": [{"magic": b"\xFF\xD8\xFF\xE0", "offset": 0},
              {"magic": b"\xFF\xD8\xFF\xE1", "offset": 0},
              {"magic": b"\xFF\xD8\xFF\xDB", "offset": 0}],
    ".png":  [{"magic": b"\x89PNG\r\n\x1A\n", "offset": 0}],
    ".bmp":  [{"magic": b"BM", "offset": 0}],
    ".gif":  [{"magic": b"GIF87a", "offset": 0},
              {"magic": b"GIF89a", "offset": 0}],
    ".tiff": [{"magic": b"II\x2A\x00", "offset": 0},   # little-endian
              {"magic": b"MM\x00\x2A", "offset": 0}],  # big-endian
    ".tif":  [{"magic": b"II\x2A\x00", "offset": 0},
              {"magic": b"MM\x00\x2A", "offset": 0}],
    ".webp": [{"magic": b"RIFF", "offset": 0}],        # refined in carver by checking offset 8 for WEBP
    ".heic": [{"magic": b"ftyp", "offset": 4}],        # ISO base media; brand at offset 8 may be heic/heix
}

# ---------------------------------------------------------------------------
# RAW camera formats
# ---------------------------------------------------------------------------
_RAW: dict[str, list[dict]] = {
    ".cr2":  [{"magic": b"II\x2A\x00", "offset": 0}],   # Canon — TIFF container
    ".nef":  [{"magic": b"II\x2A\x00", "offset": 0},    # Nikon — TIFF container
              {"magic": b"MM\x00\x2A", "offset": 0}],
    ".arw":  [{"magic": b"II\x2A\x00", "offset": 0}],   # Sony — TIFF container
    ".dng":  [{"magic": b"II\x2A\x00", "offset": 0},    # Adobe DNG — TIFF container
              {"magic": b"MM\x00\x2A", "offset": 0}],
    ".raw":  [{"magic": b"II\x2A\x00", "offset": 0}],
}

# ---------------------------------------------------------------------------
# Videos
# ---------------------------------------------------------------------------
_VIDEOS: dict[str, list[dict]] = {
    ".mp4":  [{"magic": b"ftyp", "offset": 4}],         # ISO base media — ftypXXXX
    ".mov":  [{"magic": b"ftyp", "offset": 4},
              {"magic": b"moov", "offset": 4},
              {"magic": b"free", "offset": 4},
              {"magic": b"wide", "offset": 4}],
    ".m4v":  [{"magic": b"ftyp", "offset": 4}],
    ".3gp":  [{"magic": b"ftyp", "offset": 4}],
    ".avi":  [{"magic": b"RIFF", "offset": 0}],
    ".mkv":  [{"magic": b"\x1A\x45\xDF\xA3", "offset": 0}],  # EBML header
    ".wmv":  [{"magic": b"\x30\x26\xB2\x75\x8E\x66\xCF\x11", "offset": 0}],  # ASF
    ".flv":  [{"magic": b"FLV\x01", "offset": 0}],
    ".mpg":  [{"magic": b"\x00\x00\x01\xBA", "offset": 0},
              {"magic": b"\x00\x00\x01\xB3", "offset": 0}],
    ".mpeg": [{"magic": b"\x00\x00\x01\xBA", "offset": 0},
              {"magic": b"\x00\x00\x01\xB3", "offset": 0}],
}

# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------
_AUDIO: dict[str, list[dict]] = {
    ".mp3":  [{"magic": b"ID3", "offset": 0},
              {"magic": b"\xFF\xFB", "offset": 0},   # MPEG frame sync (no ID3)
              {"magic": b"\xFF\xF3", "offset": 0},
              {"magic": b"\xFF\xF2", "offset": 0}],
    ".wav":  [{"magic": b"RIFF", "offset": 0}],
    ".flac": [{"magic": b"fLaC", "offset": 0}],
    ".aac":  [{"magic": b"\xFF\xF1", "offset": 0},   # ADTS AAC
              {"magic": b"\xFF\xF9", "offset": 0}],
    ".ogg":  [{"magic": b"OggS", "offset": 0}],
    ".wma":  [{"magic": b"\x30\x26\xB2\x75\x8E\x66\xCF\x11", "offset": 0}],  # ASF
    ".m4a":  [{"magic": b"ftyp", "offset": 4}],
}

# ---------------------------------------------------------------------------
# Archives
# ---------------------------------------------------------------------------
_ARCHIVES: dict[str, list[dict]] = {
    ".zip":  [{"magic": b"PK\x03\x04", "offset": 0},
              {"magic": b"PK\x05\x06", "offset": 0}],  # empty zip
    ".rar":  [{"magic": b"Rar!\x1A\x07\x00", "offset": 0},    # RAR 4.x
              {"magic": b"Rar!\x1A\x07\x01\x00", "offset": 0}],  # RAR 5.x
    ".7z":   [{"magic": b"7z\xBC\xAF\x27\x1C", "offset": 0}],
    ".tar":  [{"magic": b"ustar\x00", "offset": 257},   # POSIX ustar
              {"magic": b"ustar  \x00", "offset": 257}],
    ".gz":   [{"magic": b"\x1F\x8B", "offset": 0}],
    ".cab":  [{"magic": b"MSCF", "offset": 0}],
    ".iso":  [{"magic": b"CD001", "offset": 32769},     # ISO 9660 primary volume descriptor
              {"magic": b"CD001", "offset": 34817}],
}

# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
_DOCUMENTS: dict[str, list[dict]] = {
    ".pdf":  [{"magic": b"%PDF", "offset": 0}],
    ".doc":  [{"magic": b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1", "offset": 0}],  # OLE2
    ".xls":  [{"magic": b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1", "offset": 0}],  # OLE2
    ".ppt":  [{"magic": b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1", "offset": 0}],  # OLE2
    ".docx": [{"magic": b"PK\x03\x04", "offset": 0}],  # ZIP-based OOXML
    ".xlsx": [{"magic": b"PK\x03\x04", "offset": 0}],  # ZIP-based OOXML
    ".pptx": [{"magic": b"PK\x03\x04", "offset": 0}],  # ZIP-based OOXML
    ".odt":  [{"magic": b"PK\x03\x04", "offset": 0}],  # ZIP-based ODF
    ".ods":  [{"magic": b"PK\x03\x04", "offset": 0}],
    ".odp":  [{"magic": b"PK\x03\x04", "offset": 0}],
    ".rtf":  [{"magic": b"{\\rtf1", "offset": 0}],
}

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
_EMAIL: dict[str, list[dict]] = {
    ".pst":  [{"magic": b"!BDN", "offset": 0}],
    ".ost":  [{"magic": b"!BDN", "offset": 0}],
    ".eml":  [{"magic": b"From ", "offset": 0},
              {"magic": b"Return-Path:", "offset": 0},
              {"magic": b"Received:", "offset": 0},
              {"magic": b"MIME-Version:", "offset": 0}],
    ".msg":  [{"magic": b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1", "offset": 0}],  # OLE2
    ".mbox": [{"magic": b"From ", "offset": 0}],
}

# ---------------------------------------------------------------------------
# Databases
# ---------------------------------------------------------------------------
_DATABASES: dict[str, list[dict]] = {
    ".sqlite":  [{"magic": b"SQLite format 3\x00", "offset": 0}],
    ".sqlite3": [{"magic": b"SQLite format 3\x00", "offset": 0}],
    ".db":      [{"magic": b"SQLite format 3\x00", "offset": 0}],
    ".mdb":     [{"magic": b"\x00\x01\x00\x00Standard Jet DB", "offset": 0}],
    ".accdb":   [{"magic": b"\x00\x01\x00\x00Standard ACE DB", "offset": 0}],
}

# ---------------------------------------------------------------------------
# Executables
# ---------------------------------------------------------------------------
_EXECUTABLES: dict[str, list[dict]] = {
    ".exe":  [{"magic": b"MZ", "offset": 0}],
    ".dll":  [{"magic": b"MZ", "offset": 0}],
    ".sys":  [{"magic": b"MZ", "offset": 0}],
    ".msi":  [{"magic": b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1", "offset": 0}],  # OLE2
}

# ---------------------------------------------------------------------------
# Windows artifacts
# ---------------------------------------------------------------------------
_ARTIFACTS: dict[str, list[dict]] = {
    ".lnk":  [{"magic": b"L\x00\x00\x00\x01\x14\x02\x00", "offset": 0}],
    ".evtx": [{"magic": b"ElfFile\x00", "offset": 0}],
    ".reg":  [{"magic": b"Windows Registry Editor", "offset": 0},
              {"magic": b"REGEDIT4", "offset": 0}],
    ".pf":   [{"magic": b"SCCA", "offset": 4}],  # Prefetch — signature at offset 4
}

# ---------------------------------------------------------------------------
# Virtual disks
# ---------------------------------------------------------------------------
_VIRTUAL_DISKS: dict[str, list[dict]] = {
    ".vhd":  [{"magic": b"conectix", "offset": 0}],
    ".vhdx": [{"magic": b"vhdxfile", "offset": 0}],
    ".vmdk": [{"magic": b"KDMV", "offset": 0},      # sparse extent
              {"magic": b"# Disk DescriptorFile", "offset": 0}],  # descriptor
}

# ---------------------------------------------------------------------------
# Master signatures dict — merge all groups
# ---------------------------------------------------------------------------
SIGNATURES: dict[str, list[dict]] = {}
for _group in (
    _IMAGES, _RAW, _VIDEOS, _AUDIO, _ARCHIVES,
    _DOCUMENTS, _EMAIL, _DATABASES, _EXECUTABLES,
    _ARTIFACTS, _VIRTUAL_DISKS,
):
    for _ext, _patterns in _group.items():
        if _ext in SIGNATURES:
            # Merge patterns without duplicates
            existing_magics = {(p["magic"], p["offset"]) for p in SIGNATURES[_ext]}
            for p in _patterns:
                if (p["magic"], p["offset"]) not in existing_magics:
                    SIGNATURES[_ext].append(p)
                    existing_magics.add((p["magic"], p["offset"]))
        else:
            SIGNATURES[_ext] = list(_patterns)


def get_signatures(extension: str) -> list[dict]:
    """Return magic byte patterns for the given extension (e.g. '.jpg')."""
    return SIGNATURES.get(extension.lower(), [])


def all_extensions() -> list[str]:
    """Return every extension in the signatures database."""
    return list(SIGNATURES.keys())


def get_all_patterns() -> list[dict]:
    """
    Return a flat list of all patterns with extension attached.
    Each item: {"magic": bytes, "offset": int, "extension": str}
    """
    patterns: list[dict] = []
    for ext, entries in SIGNATURES.items():
        for entry in entries:
            patterns.append({
                "magic": entry["magic"],
                "offset": entry["offset"],
                "extension": ext,
            })
    return patterns
