"""
Raw disk imager — byte-for-byte clone to a .img file.

Unreadable sectors are replaced with zeroes and logged.
"""

import hashlib
import time
from collections.abc import Callable

from drt import reader as disk_reader


# Chunk size for streaming write: 1 MB
_CHUNK_SIZE = 1024 * 1024


def image_disk(
    handle: int,
    total_bytes: int,
    out_path: str,
    progress_callback: Callable[[int, int], None] | None = None,
    bad_sector_callback: Callable[[int, int], None] | None = None,
) -> dict:
    """
    Stream-write disk to out_path. Returns summary dict:
        {bytes_written, bad_sector_count, bad_sector_bytes, duration_seconds}
    """
    from drt.reader import SECTOR_SIZE, read_sectors

    def _align(n: int, boundary: int) -> int:
        r = n % boundary
        return n if r == 0 else n + (boundary - r)

    bytes_written = 0
    bad_sector_count = 0
    bad_sector_bytes = 0
    start = time.monotonic()

    aligned_chunk = _align(_CHUNK_SIZE, SECTOR_SIZE)
    offset = 0

    with open(out_path, "wb") as f:
        while offset < total_bytes:
            remaining = total_bytes - offset
            read_len = min(aligned_chunk, _align(remaining, SECTOR_SIZE))
            # Trim to actual remaining so we don't over-write past disk end
            write_len = min(read_len, remaining)

            data = read_sectors(handle, offset, read_len)

            if not data:
                # Unreadable sector range — write zeroes
                zeroes = bytes(write_len)
                f.write(zeroes)
                bad_sector_count += 1
                bad_sector_bytes += write_len
                if bad_sector_callback is not None:
                    bad_sector_callback(offset, write_len)
                bytes_written += write_len
            else:
                chunk = data[:write_len]
                f.write(chunk)
                bytes_written += len(chunk)

            offset += write_len

            if progress_callback is not None:
                progress_callback(bytes_written, total_bytes)

    return {
        "bytes_written":    bytes_written,
        "bad_sector_count": bad_sector_count,
        "bad_sector_bytes": bad_sector_bytes,
        "duration_seconds": round(time.monotonic() - start, 3),
    }


def verify_image(
    img_path: str,
    handle: int,
    total_bytes: int,
    block_size: int = 64 * 1024 * 1024,
) -> list[dict]:
    """
    Compare SHA-256 of each block_size chunk between img_path and the live disk.
    Returns list of mismatches: [{offset, length, disk_sha256, img_sha256}]
    """
    from drt.reader import read_sectors

    mismatches: list[dict] = []
    offset = 0

    with open(img_path, "rb") as img_f:
        while offset < total_bytes:
            length = min(block_size, total_bytes - offset)

            # Read from image file
            img_f.seek(offset)
            img_chunk = img_f.read(length)

            # Read from live disk
            disk_raw = read_sectors(handle, offset, length)
            disk_chunk = disk_raw[:length] if disk_raw else bytes(length)

            img_sha = hashlib.sha256(img_chunk).hexdigest()
            disk_sha = hashlib.sha256(disk_chunk).hexdigest()

            if img_sha != disk_sha:
                mismatches.append({
                    "offset":      offset,
                    "length":      length,
                    "disk_sha256": disk_sha,
                    "img_sha256":  img_sha,
                })

            offset += length

    return mismatches
