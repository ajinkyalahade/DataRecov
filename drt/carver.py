"""
Signature-based file carver.

Scans raw disk data for known magic byte patterns and yields
carved file hits with their disk offset and estimated size.
"""

from collections.abc import Iterator
from typing import Callable

from drt.signatures import SIGNATURES
from drt.types import get_extensions_for_groups
from drt import reader as disk_reader


# ---------------------------------------------------------------------------
# Max carve sizes per category (bytes)
# ---------------------------------------------------------------------------
_MAX_SIZES: dict[str, int] = {
    # Images
    ".jpg":  50  * 1024 * 1024,
    ".jpeg": 50  * 1024 * 1024,
    ".png":  50  * 1024 * 1024,
    ".bmp":  50  * 1024 * 1024,
    ".gif":  50  * 1024 * 1024,
    ".tiff": 50  * 1024 * 1024,
    ".tif":  50  * 1024 * 1024,
    ".webp": 50  * 1024 * 1024,
    ".heic": 50  * 1024 * 1024,
    ".cr2":  50  * 1024 * 1024,
    ".nef":  50  * 1024 * 1024,
    ".arw":  50  * 1024 * 1024,
    ".dng":  50  * 1024 * 1024,
    ".raw":  50  * 1024 * 1024,
    # Videos
    ".mp4":  4   * 1024 * 1024 * 1024,
    ".avi":  4   * 1024 * 1024 * 1024,
    ".mkv":  4   * 1024 * 1024 * 1024,
    ".mov":  4   * 1024 * 1024 * 1024,
    ".wmv":  4   * 1024 * 1024 * 1024,
    ".flv":  4   * 1024 * 1024 * 1024,
    ".m4v":  4   * 1024 * 1024 * 1024,
    ".3gp":  4   * 1024 * 1024 * 1024,
    ".mpg":  4   * 1024 * 1024 * 1024,
    ".mpeg": 4   * 1024 * 1024 * 1024,
    # Audio
    ".mp3":  500 * 1024 * 1024,
    ".wav":  500 * 1024 * 1024,
    ".flac": 500 * 1024 * 1024,
    ".aac":  500 * 1024 * 1024,
    ".ogg":  500 * 1024 * 1024,
    ".wma":  500 * 1024 * 1024,
    ".m4a":  500 * 1024 * 1024,
    # Documents
    ".pdf":  200 * 1024 * 1024,
    ".doc":  200 * 1024 * 1024,
    ".docx": 200 * 1024 * 1024,
    ".xls":  200 * 1024 * 1024,
    ".xlsx": 200 * 1024 * 1024,
    ".ppt":  200 * 1024 * 1024,
    ".pptx": 200 * 1024 * 1024,
    ".odt":  200 * 1024 * 1024,
    ".ods":  200 * 1024 * 1024,
    ".odp":  200 * 1024 * 1024,
    # Archives
    ".zip":  4   * 1024 * 1024 * 1024,
    ".rar":  4   * 1024 * 1024 * 1024,
    ".7z":   4   * 1024 * 1024 * 1024,
    ".tar":  4   * 1024 * 1024 * 1024,
    ".gz":   4   * 1024 * 1024 * 1024,
    ".cab":  4   * 1024 * 1024 * 1024,
    ".iso":  4   * 1024 * 1024 * 1024,
    # Databases
    ".sqlite":  2 * 1024 * 1024 * 1024,
    ".sqlite3": 2 * 1024 * 1024 * 1024,
    ".db":      2 * 1024 * 1024 * 1024,
    ".mdb":     2 * 1024 * 1024 * 1024,
    ".accdb":   2 * 1024 * 1024 * 1024,
}
_DEFAULT_MAX_SIZE = 100 * 1024 * 1024  # 100 MB for everything else


def _max_size_for(extension: str) -> int:
    return _MAX_SIZES.get(extension.lower(), _DEFAULT_MAX_SIZE)


# ---------------------------------------------------------------------------
# Pattern builder
# ---------------------------------------------------------------------------

def build_search_patterns(type_groups: list[str]) -> list[dict]:
    """
    Build the list of search patterns filtered to the requested type groups.

    Each returned dict contains:
        magic      (bytes)  — the byte sequence to search for
        offset     (int)    — position within the file where magic appears
        extension  (str)    — file extension (e.g. ".jpg")
        max_size   (int)    — max bytes to extract after this header
    """
    wanted_extensions = get_extensions_for_groups(type_groups)
    patterns: list[dict] = []
    seen: set[tuple] = set()

    for ext, entries in SIGNATURES.items():
        if ext not in wanted_extensions:
            continue
        for entry in entries:
            key = (entry["magic"], entry["offset"], ext)
            if key in seen:
                continue
            seen.add(key)
            patterns.append({
                "magic":     entry["magic"],
                "offset":    entry["offset"],
                "extension": ext,
                "max_size":  _max_size_for(ext),
            })

    return patterns


# ---------------------------------------------------------------------------
# Chunk scanner
# ---------------------------------------------------------------------------

def carve_chunk(data: bytes, chunk_offset: int, patterns: list[dict]) -> list[dict]:
    """
    Scan a chunk of raw bytes for magic byte signatures.

    For each pattern, the search adjusts for the magic's own offset within
    the file (e.g. MP4's "ftyp" appears at file-byte 4, so we search for it
    starting from byte 4 and subtract 4 when reporting the file's true start).

    Returns a list of dicts:
        disk_offset   (int)  — absolute byte position on disk
        extension     (str)  — matched extension
        confidence    (str)  — "high" (always, for exact magic match)
        max_size      (int)  — extraction budget
    """
    hits: list[dict] = []
    data_len = len(data)

    for pat in patterns:
        magic      = pat["magic"]
        magic_off  = pat["offset"]
        ext        = pat["extension"]
        max_size   = pat["max_size"]
        magic_len  = len(magic)

        # Search through the chunk for every occurrence of this magic sequence.
        # When magic_off > 0 the magic appears after the true file start,
        # so the search window starts at magic_off bytes into the chunk.
        search_start = magic_off

        pos = search_start
        while pos <= data_len - magic_len:
            idx = data.find(magic, pos)
            if idx == -1:
                break

            # idx is where magic was found; the actual file starts magic_off earlier
            file_start_in_chunk = idx - magic_off
            if file_start_in_chunk < 0:
                pos = idx + 1
                continue

            disk_offset = chunk_offset + file_start_in_chunk
            hits.append({
                "disk_offset": disk_offset,
                "extension":   ext,
                "confidence":  "high",
                "max_size":    max_size,
            })

            pos = idx + 1

    return hits


# ---------------------------------------------------------------------------
# Full-disk carver
# ---------------------------------------------------------------------------

def carve_disk(
    handle: int,
    total_bytes: int,
    patterns: list[dict],
    progress_callback: Callable[[int, int], None] | None = None,
    start_offset: int = 0,
) -> Iterator[dict]:
    """
    Iterate over the disk in 1 MB chunks and yield carved file hits.

    start_offset — skip bytes before this position (for resume).
    progress_callback(bytes_processed, total_bytes) is called after each chunk.

    Each yielded dict contains:
        disk_offset    (int)   — byte offset on disk
        extension      (str)   — e.g. ".jpg"
        estimated_size (int)   — bytes to extract (capped at next hit or max_size)
        max_size       (int)   — the per-extension extraction budget
        chunk_data     (bytes) — the 1 MB chunk containing this hit
        chunk_offset   (int)   — absolute offset of chunk_data on disk
    """
    CHUNK_SIZE = 1024 * 1024  # 1 MB
    # Align start_offset down to a chunk boundary so we don't miss headers
    aligned_start = (start_offset // CHUNK_SIZE) * CHUNK_SIZE
    bytes_processed = aligned_start

    for chunk_offset, data in disk_reader.iter_sectors(
        handle, total_bytes, CHUNK_SIZE, start_offset=aligned_start
    ):
        hits = carve_chunk(data, chunk_offset, patterns)

        # Cap each hit's estimated_size to the start of the next hit in this chunk,
        # so we never over-read into an adjacent file's data.
        for i, hit in enumerate(hits):
            if i + 1 < len(hits):
                next_start = hits[i + 1]["disk_offset"]
                capped = min(hit["max_size"], next_start - hit["disk_offset"])
            else:
                capped = min(hit["max_size"], len(data) - (hit["disk_offset"] - chunk_offset))

            yield {
                "disk_offset":    hit["disk_offset"],
                "extension":      hit["extension"],
                "estimated_size": max(capped, 0),
                "max_size":       hit["max_size"],
                "chunk_data":     data,
                "chunk_offset":   chunk_offset,
            }

        bytes_processed += len(data)
        if progress_callback is not None:
            progress_callback(bytes_processed, total_bytes)
