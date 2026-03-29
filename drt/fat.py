"""
FAT32/exFAT deleted file recovery.

Reads the BPB from sector 0, locates the root directory, and walks
directory entries looking for deleted (0xE5 first-byte) entries.

All binary parsing uses stdlib struct. Windows-only.
"""

import struct
from collections.abc import Iterator

from drt import reader as disk_reader


def read_bpb(handle: int) -> dict | None:
    """
    Read the FAT32/exFAT BPB from sector 0. Returns dict with:
        fs_type          (str)  "FAT32" or "exFAT"
        bytes_per_sector (int)
        sectors_per_cluster (int)
        reserved_sectors (int)
        num_fats         (int)
        fat_size_sectors (int)
        root_cluster     (int)  cluster of root dir
        data_start_sector (int)

    Returns None if not a FAT32/exFAT volume.
    """
    try:
        sector0 = disk_reader.read_sectors(handle, 0, 512)
        if len(sector0) < 512:
            return None

        # Check exFAT OEM name (offset 3, 8 bytes)
        oem = sector0[3:11]
        if oem == b"EXFAT   ":
            return _parse_exfat_bpb(sector0)

        # FAT32: OEM name can vary; check signature bytes instead
        # bytes_per_sector at 0x0B must be a power-of-2 between 512–4096
        bytes_per_sector, = struct.unpack_from("<H", sector0, 0x0B)
        if bytes_per_sector not in (512, 1024, 2048, 4096):
            return None

        sectors_per_cluster = sector0[0x0D]
        if sectors_per_cluster == 0:
            return None

        reserved_sectors, = struct.unpack_from("<H", sector0, 0x0E)
        num_fats = sector0[0x10]

        # FAT32: fat_size at 0x24 (DWORD); if 0x16 (WORD) is non-zero it's FAT12/16
        fat_size_16, = struct.unpack_from("<H", sector0, 0x16)
        fat_size_32, = struct.unpack_from("<I", sector0, 0x24)

        root_entry_count, = struct.unpack_from("<H", sector0, 0x11)
        if root_entry_count != 0:
            # FAT12 or FAT16 — not supported
            return None

        fat_size_sectors = fat_size_32 if fat_size_16 == 0 else fat_size_16

        root_cluster, = struct.unpack_from("<I", sector0, 0x2C)

        # Verify FAT32 FS type string at offset 0x52
        fs_type_str = sector0[0x52:0x5A]
        if not fs_type_str.startswith(b"FAT32"):
            # Some FAT32 volumes don't have this; trust the field layout
            pass

        data_start_sector = reserved_sectors + num_fats * fat_size_sectors

        return {
            "fs_type":            "FAT32",
            "bytes_per_sector":   bytes_per_sector,
            "sectors_per_cluster": sectors_per_cluster,
            "reserved_sectors":   reserved_sectors,
            "num_fats":           num_fats,
            "fat_size_sectors":   fat_size_sectors,
            "root_cluster":       root_cluster,
            "data_start_sector":  data_start_sector,
        }
    except Exception:
        return None


def _parse_exfat_bpb(sector0: bytes) -> dict | None:
    """Parse an exFAT BPB. Returns bpb dict or None on failure."""
    try:
        # exFAT BPB layout (all offsets from start of VBR):
        # 0x40: partition_offset (QWORD)
        # 0x48: volume_length (QWORD)
        # 0x50: fat_offset (DWORD) — sector offset of first FAT
        # 0x54: fat_length (DWORD)
        # 0x58: cluster_heap_offset (DWORD) — sector of data region
        # 0x5C: cluster_count (DWORD)
        # 0x60: root_dir_cluster (DWORD)
        # 0x6C: bytes_per_sector_shift (BYTE)
        # 0x6D: sectors_per_cluster_shift (BYTE)
        # 0x6E: num_fats (BYTE)

        bytes_per_sector_shift = sector0[0x6C]
        sectors_per_cluster_shift = sector0[0x6D]
        num_fats = sector0[0x6E]

        bytes_per_sector = 1 << bytes_per_sector_shift
        sectors_per_cluster = 1 << sectors_per_cluster_shift

        fat_offset, = struct.unpack_from("<I", sector0, 0x50)
        fat_length, = struct.unpack_from("<I", sector0, 0x54)
        cluster_heap_offset, = struct.unpack_from("<I", sector0, 0x58)
        root_dir_cluster, = struct.unpack_from("<I", sector0, 0x60)

        return {
            "fs_type":            "exFAT",
            "bytes_per_sector":   bytes_per_sector,
            "sectors_per_cluster": sectors_per_cluster,
            "reserved_sectors":   fat_offset,
            "num_fats":           num_fats,
            "fat_size_sectors":   fat_length,
            "root_cluster":       root_dir_cluster,
            "data_start_sector":  cluster_heap_offset,
        }
    except Exception:
        return None


def _cluster_to_offset(cluster: int, bpb: dict) -> int:
    """Convert a cluster number to a byte offset on disk."""
    bps = bpb["bytes_per_sector"]
    spc = bpb["sectors_per_cluster"]
    # FAT32: data area starts at cluster 2
    sector = bpb["data_start_sector"] + (cluster - 2) * spc
    return sector * bps


def _read_cluster(handle: int, cluster: int, bpb: dict) -> bytes:
    """Read one cluster's worth of data. Returns empty bytes on failure."""
    offset = _cluster_to_offset(cluster, bpb)
    cluster_bytes = bpb["bytes_per_sector"] * bpb["sectors_per_cluster"]
    data = disk_reader.read_sectors(handle, offset, cluster_bytes)
    return data or b""


def _parse_lfn_entry(entry: bytes) -> str:
    """Extract the UTF-16LE characters from a LFN directory entry (32 bytes)."""
    # LFN layout: chars at offsets 1-10 (5 chars), 14-25 (6 chars), 28-31 (2 chars)
    chars = entry[1:11] + entry[14:26] + entry[28:32]
    name = chars.decode("utf-16-le", errors="replace")
    # Trim at null terminator
    null_idx = name.find("\x00")
    if null_idx >= 0:
        name = name[:null_idx]
    return name


def read_fat_table(handle: int, bpb: dict) -> list[int]:
    """
    Read the entire FAT32 table into memory as a list of 32-bit cluster values.
    Returns [] on failure.
    """
    try:
        bps = bpb["bytes_per_sector"]
        fat_start_sector = bpb["reserved_sectors"]
        fat_size_sectors = bpb["fat_size_sectors"]
        fat_size_bytes = fat_size_sectors * bps

        fat_offset = fat_start_sector * bps
        raw = disk_reader.read_sectors(handle, fat_offset, fat_size_bytes)
        if not raw or len(raw) < 8:
            return []

        # FAT32: each entry is 4 bytes; mask with 0x0FFFFFFF
        entry_count = len(raw) // 4
        table: list[int] = []
        for i in range(entry_count):
            val, = struct.unpack_from("<I", raw, i * 4)
            table.append(val & 0x0FFFFFFF)
        return table
    except Exception:
        return []


def follow_cluster_chain(fat_table: list[int], start_cluster: int) -> list[int]:
    """
    Walk FAT chain from start_cluster until EOC (>= 0x0FFFFFF8) or bad cluster.
    Returns list of cluster numbers. Max 1M clusters to prevent infinite loops.
    """
    _EOC_MIN   = 0x0FFFFFF8
    _BAD_CLUSTER = 0x0FFFFFF7
    _MAX_CLUSTERS = 1_000_000

    chain: list[int] = []
    visited: set[int] = set()
    cluster = start_cluster

    while cluster < len(fat_table) and len(chain) < _MAX_CLUSTERS:
        if cluster < 2:
            break
        if cluster in visited:
            break
        visited.add(cluster)
        chain.append(cluster)

        next_cluster = fat_table[cluster]
        if next_cluster >= _EOC_MIN or next_cluster == _BAD_CLUSTER:
            break
        cluster = next_cluster

    return chain


def read_cluster_chain(handle: int, bpb: dict, clusters: list[int], file_size: int) -> bytes:
    """
    Read data from the given cluster list and return up to file_size bytes.
    Returns b"" on error.
    """
    if not clusters or file_size == 0:
        return b""

    try:
        chunks: list[bytes] = []
        bytes_remaining = file_size

        for cluster in clusters:
            if bytes_remaining <= 0:
                break
            data = _read_cluster(handle, cluster, bpb)
            if not data:
                return b""
            chunk = data[:bytes_remaining]
            chunks.append(chunk)
            bytes_remaining -= len(chunk)

        return b"".join(chunks)[:file_size]
    except Exception:
        return b""


def iter_deleted_entries(handle: int, bpb: dict, wanted_extensions: set[str]) -> Iterator[dict]:
    """
    Walk the root directory and subdirectories looking for deleted entries.

    A deleted FAT32 directory entry has 0xE5 as the first byte of the name.
    Each entry is 32 bytes. LFN (Long File Name) entries have attribute 0x0F.

    Yields dict:
        name          (str)
        extension     (str)
        size_bytes    (int)
        first_cluster (int)
        source        (str)  "fat"
    """
    # Use a queue of clusters to visit (BFS over directory tree)
    visited_clusters: set[int] = set()
    cluster_queue = [bpb["root_cluster"]]

    while cluster_queue:
        cluster = cluster_queue.pop(0)
        if cluster in visited_clusters or cluster < 2:
            continue
        visited_clusters.add(cluster)

        data = _read_cluster(handle, cluster, bpb)
        if not data:
            continue

        entry_count = len(data) // 32
        lfn_parts: list[str] = []

        for i in range(entry_count):
            entry = data[i * 32 : i * 32 + 32]
            if len(entry) < 32:
                break

            first_byte = entry[0]

            if first_byte == 0x00:
                # End of directory
                lfn_parts.clear()
                break

            attr = entry[11]

            # LFN entry
            if attr == 0x0F:
                lfn_parts.insert(0, _parse_lfn_entry(entry))
                continue

            # Deleted entry
            if first_byte == 0xE5:
                # Assemble name: prefer LFN if available
                if lfn_parts:
                    full_name = "".join(lfn_parts)
                    lfn_parts = []
                else:
                    # 8.3 short name — first char replaced with 0xE5
                    name_raw = entry[1:8].rstrip(b" ")
                    ext_raw  = entry[8:11].rstrip(b" ")
                    base = name_raw.decode("ascii", errors="replace")
                    ext_part = ext_raw.decode("ascii", errors="replace")
                    full_name = f"_{base}.{ext_part}" if ext_part else f"_{base}"

                dot_idx = full_name.rfind(".")
                ext = full_name[dot_idx:].lower() if dot_idx >= 0 else ""

                first_cluster_hi, = struct.unpack_from("<H", entry, 20)
                first_cluster_lo, = struct.unpack_from("<H", entry, 26)
                first_cluster = (first_cluster_hi << 16) | first_cluster_lo

                size_bytes, = struct.unpack_from("<I", entry, 28)

                if ext in wanted_extensions:
                    yield {
                        "name":          full_name,
                        "extension":     ext,
                        "size_bytes":    size_bytes,
                        "first_cluster": first_cluster,
                        "source":        "fat",
                    }
                continue

            # Active directory entry — recurse into subdirectories
            lfn_parts = []
            is_dir = bool(attr & 0x10)
            if is_dir:
                first_cluster_hi, = struct.unpack_from("<H", entry, 20)
                first_cluster_lo, = struct.unpack_from("<H", entry, 26)
                sub_cluster = (first_cluster_hi << 16) | first_cluster_lo
                if sub_cluster >= 2 and sub_cluster not in visited_clusters:
                    cluster_queue.append(sub_cluster)


def scan(handle: int, wanted_extensions: set[str]) -> list[dict]:
    """
    Entry point: parse BPB and return deleted entries. Graceful failure returns [].
    """
    try:
        bpb = read_bpb(handle)
        if bpb is None:
            return []
        return list(iter_deleted_entries(handle, bpb, wanted_extensions))
    except Exception:
        return []
