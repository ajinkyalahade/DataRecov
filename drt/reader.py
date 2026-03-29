"""
Raw sector-level disk reader via ctypes kernel32.

Opens physical disks or volumes with FILE_FLAG_NO_BUFFERING for direct
sector-aligned I/O. All operations are read-only; the source disk is
never written to.
"""

import ctypes
import ctypes.wintypes
import struct
import sys
from collections.abc import Iterator


# ---------------------------------------------------------------------------
# Windows constants
# ---------------------------------------------------------------------------
GENERIC_READ            = 0x80000000
FILE_SHARE_READ         = 0x00000001
FILE_SHARE_WRITE        = 0x00000002
OPEN_EXISTING           = 3
FILE_FLAG_NO_BUFFERING  = 0x20000000
INVALID_HANDLE_VALUE    = ctypes.c_void_p(-1).value

SECTOR_SIZE             = 512   # default; geometry IOCTL may refine this
IOCTL_DISK_GET_DRIVE_GEOMETRY_EX = 0x000700A0


# ---------------------------------------------------------------------------
# Open / close
# ---------------------------------------------------------------------------

def open_disk(path: str) -> int:
    """
    Open a physical disk or volume for raw read access.

    path examples:
        "\\\\.\\PhysicalDrive0"
        "\\\\.\\C:"

    Returns a Windows HANDLE (integer).
    Raises OSError on failure.
    """
    kernel32 = ctypes.windll.kernel32

    handle = kernel32.CreateFileW(
        path,
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_NO_BUFFERING,
        None,
    )

    if handle == INVALID_HANDLE_VALUE:
        err = kernel32.GetLastError()
        raise OSError(f"Cannot open {path!r}: Windows error {err}")

    return handle


def close_disk(handle: int) -> None:
    """Close a disk handle obtained from open_disk."""
    ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))


# ---------------------------------------------------------------------------
# Disk size
# ---------------------------------------------------------------------------

def get_disk_size(handle: int) -> int:
    """
    Return total disk size in bytes using IOCTL_DISK_GET_DRIVE_GEOMETRY_EX.
    Returns 0 if the IOCTL fails.
    """
    kernel32  = ctypes.windll.kernel32
    out_buf   = ctypes.create_string_buffer(64)
    bytes_ret = ctypes.wintypes.DWORD(0)

    ok = kernel32.DeviceIoControl(
        ctypes.c_void_p(handle),
        IOCTL_DISK_GET_DRIVE_GEOMETRY_EX,
        None, 0,
        out_buf, 64,
        ctypes.byref(bytes_ret),
        None,
    )

    if not ok or bytes_ret.value < 32:
        return 0

    # DISK_GEOMETRY_EX: Geometry(24 bytes) + DiskSize(LARGE_INTEGER, 8 bytes)
    try:
        (disk_size,) = struct.unpack_from("<q", out_buf.raw, 24)
        return max(disk_size, 0)
    except struct.error:
        return 0


# ---------------------------------------------------------------------------
# Sector-aligned read
# ---------------------------------------------------------------------------

def _align_up(value: int, alignment: int) -> int:
    """Round value up to the nearest multiple of alignment."""
    remainder = value % alignment
    return value if remainder == 0 else value + (alignment - remainder)


def read_sectors(handle: int, offset_bytes: int, length_bytes: int) -> bytes:
    """
    Read raw bytes from a disk handle at the given byte offset.

    FILE_FLAG_NO_BUFFERING requires:
        - offset_bytes must be sector-aligned (multiple of 512)
        - length_bytes is padded up to the next sector boundary

    Returns the raw bytes read (may be longer than length_bytes due to
    sector alignment padding). Returns empty bytes on read failure.
    """
    kernel32 = ctypes.windll.kernel32

    aligned_offset = (offset_bytes // SECTOR_SIZE) * SECTOR_SIZE
    aligned_length = _align_up(
        length_bytes + (offset_bytes - aligned_offset), SECTOR_SIZE
    )

    # SetFilePointerEx
    li_offset = ctypes.c_longlong(aligned_offset)
    new_pos   = ctypes.c_longlong(0)

    ok = kernel32.SetFilePointerEx(
        ctypes.c_void_p(handle),
        li_offset,
        ctypes.byref(new_pos),
        0,  # FILE_BEGIN
    )
    if not ok:
        return b""

    buf      = ctypes.create_string_buffer(aligned_length)
    bytes_rd = ctypes.wintypes.DWORD(0)

    ok = kernel32.ReadFile(
        ctypes.c_void_p(handle),
        buf,
        aligned_length,
        ctypes.byref(bytes_rd),
        None,
    )

    if not ok or bytes_rd.value == 0:
        return b""

    return buf.raw[: bytes_rd.value]


# ---------------------------------------------------------------------------
# Chunk iterator
# ---------------------------------------------------------------------------

def iter_sectors(
    handle: int,
    total_bytes: int,
    chunk_size: int = 1024 * 1024,
) -> Iterator[tuple[int, bytes]]:
    """
    Yield (offset, data) tuples, reading the disk in chunk_size increments.

    chunk_size is automatically aligned up to the nearest sector boundary.
    Unreadable sectors are logged to stderr and skipped; iteration continues.
    """
    aligned_chunk = _align_up(chunk_size, SECTOR_SIZE)
    offset        = 0

    while offset < total_bytes:
        remaining  = total_bytes - offset
        read_len   = min(aligned_chunk, _align_up(remaining, SECTOR_SIZE))

        data = read_sectors(handle, offset, read_len)

        if not data:
            print(
                f"[reader] unreadable sector at offset {offset} "
                f"({offset // SECTOR_SIZE}), skipping {read_len} bytes",
                file=sys.stderr,
            )
            offset += aligned_chunk
            continue

        yield offset, data
        offset += len(data)
