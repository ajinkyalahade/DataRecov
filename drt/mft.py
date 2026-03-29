"""
NTFS MFT (Master File Table) parser.

Reads deleted file entries from the MFT by scanning for FILE records
whose flags field indicates the entry is not in use (deleted).

All binary parsing uses stdlib struct. Windows-only: reads via the raw
disk handle opened by reader.open_disk.
"""

import struct
from collections.abc import Iterator

from drt import reader as disk_reader


# MFT record size is always 1024 bytes for NTFS versions used on Windows.
_MFT_RECORD_SIZE = 1024

# Attribute type constants
_ATTR_FILE_NAME  = 0x30
_ATTR_DATA       = 0x80
_ATTR_END        = 0xFFFFFFFF


def find_mft_offset(handle: int) -> int:
    """
    Read the NTFS BPB (BIOS Parameter Block) from sector 0 to find the
    MFT start cluster. Returns the byte offset of the MFT on disk, or 0 on failure.

    NTFS BPB layout (from offset 0 of the volume):
      Offset 0x0B: bytes_per_sector (WORD)
      Offset 0x0D: sectors_per_cluster (BYTE)
      Offset 0x30: mft_cluster (QWORD) — cluster number of $MFT
    """
    try:
        sector0 = disk_reader.read_sectors(handle, 0, 512)
        if len(sector0) < 0x38 + 8:
            return 0

        # Verify NTFS OEM ID
        oem_id = sector0[3:11]
        if oem_id != b"NTFS    ":
            return 0

        bytes_per_sector, = struct.unpack_from("<H", sector0, 0x0B)
        sectors_per_cluster, = struct.unpack_from("<B", sector0, 0x0D)
        mft_cluster, = struct.unpack_from("<Q", sector0, 0x30)

        if bytes_per_sector == 0 or sectors_per_cluster == 0:
            return 0

        bytes_per_cluster = bytes_per_sector * sectors_per_cluster
        return mft_cluster * bytes_per_cluster
    except Exception:
        return 0


def _get_bytes_per_cluster(handle: int) -> int:
    """Return bytes_per_cluster from the NTFS BPB, or 0 on failure."""
    try:
        sector0 = disk_reader.read_sectors(handle, 0, 512)
        if len(sector0) < 0x0E:
            return 0
        bytes_per_sector, = struct.unpack_from("<H", sector0, 0x0B)
        sectors_per_cluster, = struct.unpack_from("<B", sector0, 0x0D)
        if bytes_per_sector == 0 or sectors_per_cluster == 0:
            return 0
        return bytes_per_sector * sectors_per_cluster
    except Exception:
        return 0


def _parse_file_name_attr(record: bytes, attr_offset: int) -> str | None:
    """
    Parse a $FILE_NAME attribute and return the filename string, or None on error.

    In a resident $FILE_NAME attribute:
      attr+64: filename_length (BYTE) — length in UTF-16LE characters
      attr+65: namespace (BYTE)
      attr+66: filename data (UTF-16LE)
    """
    try:
        if attr_offset + 66 > len(record):
            return None
        filename_length = record[attr_offset + 64]
        # namespace = record[attr_offset + 65]  # not used for recovery
        name_start = attr_offset + 66
        name_end = name_start + filename_length * 2
        if name_end > len(record):
            return None
        return record[name_start:name_end].decode("utf-16-le", errors="replace")
    except Exception:
        return None


def _parse_data_attr(record: bytes, attr_offset: int, non_resident: int) -> int:
    """
    Parse a $DATA attribute and return the actual file size, or 0 on error.

    Resident: actual_size at attr+16 (QWORD)
    Non-resident: actual_size at attr+48 (QWORD)
    """
    try:
        if non_resident == 0:
            # Resident
            if attr_offset + 24 > len(record):
                return 0
            size, = struct.unpack_from("<Q", record, attr_offset + 16)
            return size
        else:
            # Non-resident
            if attr_offset + 56 > len(record):
                return 0
            size, = struct.unpack_from("<Q", record, attr_offset + 48)
            return size
    except Exception:
        return 0


def read_data_runs(attr_data: bytes) -> list[tuple[int, int]]:
    """
    Parse the non-resident $DATA attribute data runs into a list of
    (start_cluster, cluster_count) tuples. Cluster values are absolute.

    Data run encoding (from NTFS spec):
      Each run starts with a header byte:
        low nibble  = byte length of cluster count field
        high nibble = byte length of cluster offset field (signed, delta)
      Following: cluster_count bytes (little-endian unsigned)
                 cluster_offset bytes (little-endian signed, DELTA from previous cluster)
      A 0x00 header byte terminates the run list.
    """
    # For a non-resident $DATA attribute, data runs start at:
    #   attr_offset + data_runs_offset (at attr+32, WORD)
    # But this function receives the raw attribute bytes from attr_offset.
    # The caller must pass the slice starting at attr_offset.
    runs: list[tuple[int, int]] = []
    try:
        # data_runs_offset is at byte 32 of the attribute (relative to attr start)
        if len(attr_data) < 34:
            return runs
        runs_offset, = struct.unpack_from("<H", attr_data, 32)
        if runs_offset >= len(attr_data):
            return runs

        pos = runs_offset
        prev_cluster = 0

        while pos < len(attr_data):
            header = attr_data[pos]
            pos += 1

            if header == 0x00:
                break

            count_len = header & 0x0F
            offset_len = (header >> 4) & 0x0F

            if count_len == 0:
                break
            if pos + count_len + offset_len > len(attr_data):
                break

            # Cluster count (unsigned little-endian)
            count_bytes = attr_data[pos: pos + count_len]
            count_bytes_padded = count_bytes + b"\x00" * (8 - len(count_bytes))
            cluster_count, = struct.unpack_from("<Q", count_bytes_padded)
            pos += count_len

            if offset_len == 0:
                # Sparse run — no cluster allocation
                prev_cluster = 0
                runs.append((0, cluster_count))
                continue

            # Cluster offset (signed little-endian, delta)
            offset_bytes = attr_data[pos: pos + offset_len]
            # Sign-extend
            raw_val = int.from_bytes(offset_bytes, byteorder="little", signed=False)
            sign_bit = 1 << (offset_len * 8 - 1)
            if raw_val & sign_bit:
                raw_val -= 1 << (offset_len * 8)
            pos += offset_len

            prev_cluster += raw_val
            runs.append((prev_cluster, cluster_count))

    except Exception:
        pass

    return runs


def extract_file_content(
    handle: int,
    data_runs: list[tuple[int, int]],
    bytes_per_cluster: int,
    file_size: int,
) -> bytes:
    """
    Follow data runs and read actual file content from disk.
    Returns up to file_size bytes. Graceful: returns b"" on any read error.
    """
    if not data_runs or bytes_per_cluster == 0 or file_size == 0:
        return b""

    try:
        chunks: list[bytes] = []
        bytes_remaining = file_size

        for start_cluster, cluster_count in data_runs:
            if bytes_remaining <= 0:
                break

            if start_cluster == 0:
                # Sparse run — emit zeroes
                sparse_bytes = min(cluster_count * bytes_per_cluster, bytes_remaining)
                chunks.append(bytes(sparse_bytes))
                bytes_remaining -= sparse_bytes
                continue

            byte_offset = start_cluster * bytes_per_cluster
            read_len = min(cluster_count * bytes_per_cluster, bytes_remaining)

            raw = disk_reader.read_sectors(handle, byte_offset, read_len)
            if not raw:
                return b""

            chunk = raw[:read_len]
            chunks.append(chunk)
            bytes_remaining -= len(chunk)

        return b"".join(chunks)[:file_size]
    except Exception:
        return b""


def iter_deleted_files(handle: int, mft_offset: int, wanted_extensions: set[str]) -> Iterator[dict]:
    """
    Walk MFT records and yield deleted file entries matching wanted_extensions.

    Each MFT record starts with signature b'FILE'. A record is considered
    deleted if bit 0 of the flags field (offset 0x16, WORD) is NOT set (0 = deleted).

    Yields dict:
        name          (str)   filename
        extension     (str)   e.g. ".jpg"
        size_bytes    (int)   file size from $DATA attribute (0 if unknown)
        mft_record    (int)   record number
        source        (str)   "mft"
        data_runs     (list)  list of (start_cluster, count) tuples; empty for resident
        bytes_per_cluster (int) cluster size; 0 if unknown
        resident_data (bytes) raw data if resident; b"" otherwise
    """
    bytes_per_cluster = _get_bytes_per_cluster(handle)

    record_num = 0
    consecutive_empty = 0
    # Stop after 256 consecutive non-FILE records to avoid scanning forever
    _MAX_EMPTY = 256

    while True:
        offset = mft_offset + record_num * _MFT_RECORD_SIZE
        raw = disk_reader.read_sectors(handle, offset, _MFT_RECORD_SIZE)
        if not raw or len(raw) < _MFT_RECORD_SIZE:
            break

        record = raw[:_MFT_RECORD_SIZE]

        if record[:4] != b"FILE":
            consecutive_empty += 1
            if consecutive_empty >= _MAX_EMPTY:
                break
            record_num += 1
            continue

        consecutive_empty = 0

        # Flags at offset 0x16 (WORD): bit 0 = in use
        flags, = struct.unpack_from("<H", record, 0x16)
        is_deleted = (flags & 0x01) == 0

        if is_deleted:
            # Walk attributes
            first_attr_offset, = struct.unpack_from("<H", record, 0x14)
            attr_pos = first_attr_offset
            filename: str | None = None
            size_bytes = 0
            data_runs: list[tuple[int, int]] = []
            resident_data: bytes = b""

            while attr_pos + 8 <= _MFT_RECORD_SIZE:
                try:
                    attr_type, = struct.unpack_from("<I", record, attr_pos)
                    attr_len,  = struct.unpack_from("<I", record, attr_pos + 4)
                    non_resident = record[attr_pos + 8]
                except (struct.error, IndexError):
                    break

                if attr_type == _ATTR_END:
                    break

                if attr_len < 8 or attr_pos + attr_len > _MFT_RECORD_SIZE:
                    break

                if attr_type == _ATTR_FILE_NAME:
                    # Only parse resident $FILE_NAME (non_resident should always be 0)
                    if non_resident == 0:
                        parsed = _parse_file_name_attr(record, attr_pos)
                        if parsed and "." in parsed and not parsed.startswith("$"):
                            filename = parsed

                elif attr_type == _ATTR_DATA:
                    size_bytes = _parse_data_attr(record, attr_pos, non_resident)

                    if non_resident == 0:
                        # Resident $DATA: data at attr+resident_data_offset, length = resident_data_length
                        # resident_data_offset at attr+20 (WORD), resident_data_length at attr+16 (DWORD)
                        try:
                            if attr_pos + 24 <= _MFT_RECORD_SIZE:
                                res_data_len, = struct.unpack_from("<I", record, attr_pos + 16)
                                res_data_off, = struct.unpack_from("<H", record, attr_pos + 20)
                                data_start = attr_pos + res_data_off
                                data_end = data_start + res_data_len
                                if data_end <= _MFT_RECORD_SIZE:
                                    resident_data = record[data_start:data_end]
                        except (struct.error, IndexError):
                            pass
                    else:
                        # Non-resident: parse data runs from the attribute bytes
                        attr_bytes = record[attr_pos: attr_pos + attr_len]
                        data_runs = read_data_runs(attr_bytes)

                attr_pos += attr_len

            if filename:
                ext = ""
                dot_idx = filename.rfind(".")
                if dot_idx >= 0:
                    ext = filename[dot_idx:].lower()

                if ext in wanted_extensions:
                    yield {
                        "name":             filename,
                        "extension":        ext,
                        "size_bytes":       size_bytes,
                        "mft_record":       record_num,
                        "source":           "mft",
                        "data_runs":        data_runs,
                        "bytes_per_cluster": bytes_per_cluster,
                        "resident_data":    resident_data,
                    }

        record_num += 1


def scan(handle: int, wanted_extensions: set[str]) -> list[dict]:
    """
    Entry point: locate MFT and return all deleted file entries matching extensions.
    Returns empty list if MFT cannot be located or parsed (graceful failure).
    """
    try:
        mft_offset = find_mft_offset(handle)
        if mft_offset == 0:
            return []
        return list(iter_deleted_files(handle, mft_offset, wanted_extensions))
    except Exception:
        return []
