"""
Virtual disk support — mount VHD/VHDX and scan them.

Uses Windows built-in VHD APIs via ctypes (virtdisk.dll).
VMDK support: read-only, parse the descriptor file to find extents,
open each flat/sparse extent as a raw file.
"""

import ctypes
import ctypes.wintypes
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# VirtDisk constants
# ---------------------------------------------------------------------------

VIRTUAL_DISK_ACCESS_READ         = 0x000d0000
OPEN_VIRTUAL_DISK_FLAG_NONE      = 0x00000000
ATTACH_VIRTUAL_DISK_FLAG_READ_ONLY = 0x00000002
DETACH_VIRTUAL_DISK_FLAG_NONE    = 0x00000000

# VIRTUAL_STORAGE_TYPE vendor/device IDs
VIRTUAL_STORAGE_TYPE_VENDOR_MICROSOFT = (
    b"\xec\x98\x4a\xec\xa0\x82\x4b\xab\x90\x98\x4b\xd5\x1d\x86\x08\x5a"
)
VIRTUAL_STORAGE_TYPE_DEVICE_VHD  = 2
VIRTUAL_STORAGE_TYPE_DEVICE_VHDX = 3

# GET_VIRTUAL_DISK_INFO_SIZE identifier
GET_VIRTUAL_DISK_INFO_SIZE              = 1
GETSET_VIRTUAL_DISK_VERSION_1           = 1
GET_VIRTUAL_DISK_INFO_PHYSICAL_PATH_ID  = 7


def _load_virtdisk():
    """Load virtdisk.dll; returns the ctypes CDLL or None on failure."""
    try:
        return ctypes.windll.LoadLibrary("virtdisk.dll")
    except Exception:
        return None


def _make_vst(device_id: int) -> ctypes.Structure:
    """Build a VIRTUAL_STORAGE_TYPE structure."""
    class VIRTUAL_STORAGE_TYPE(ctypes.Structure):
        _fields_ = [
            ("DeviceId", ctypes.c_ulong),
            ("VendorId",  ctypes.c_byte * 16),
        ]

    vst = VIRTUAL_STORAGE_TYPE()
    vst.DeviceId = device_id
    vendor_bytes = VIRTUAL_STORAGE_TYPE_VENDOR_MICROSOFT
    for i, b in enumerate(vendor_bytes):
        vst.VendorId[i] = b
    return vst


def mount_vhd(vhd_path: str) -> str | None:
    """
    Mount a VHD/VHDX read-only via virtdisk.dll OpenVirtualDisk + AttachVirtualDisk.
    Returns the physical disk path (e.g. '\\\\.\\PhysicalDrive2') or None on failure.

    Steps:
      1. OpenVirtualDisk(VIRTUAL_STORAGE_TYPE for VHD or VHDX, path, READ access, flags)
      2. AttachVirtualDisk(READ_ONLY flag) — makes it appear as a disk
      3. GetVirtualDiskPhysicalPath → returns the \\\\.\\PhysicalDriveN path
    """
    vd = _load_virtdisk()
    if vd is None:
        return None

    try:
        ext = Path(vhd_path).suffix.lower()
        device_id = VIRTUAL_STORAGE_TYPE_DEVICE_VHDX if ext == ".vhdx" else VIRTUAL_STORAGE_TYPE_DEVICE_VHD
        vst = _make_vst(device_id)

        handle = ctypes.c_void_p(0)

        # OPEN_VIRTUAL_DISK_PARAMETERS version 1: Version=1, ReadOnly=FALSE
        class OPEN_PARAMS_V1(ctypes.Structure):
            _fields_ = [
                ("Version",   ctypes.c_uint),
                ("ReadOnly",  ctypes.c_int),
                ("GetDevVirtSz", ctypes.c_ulong),
            ]

        open_params = OPEN_PARAMS_V1()
        open_params.Version = 1
        open_params.ReadOnly = 0
        open_params.GetDevVirtSz = 0

        ret = vd.OpenVirtualDisk(
            ctypes.byref(vst),
            vhd_path,
            VIRTUAL_DISK_ACCESS_READ,
            OPEN_VIRTUAL_DISK_FLAG_NONE,
            ctypes.byref(open_params),
            ctypes.byref(handle),
        )
        if ret != 0:
            return None

        # ATTACH_VIRTUAL_DISK_PARAMETERS version 1
        class ATTACH_PARAMS_V1(ctypes.Structure):
            _fields_ = [
                ("Version",   ctypes.c_uint),
                ("Reserved",  ctypes.c_ulong),
            ]

        attach_params = ATTACH_PARAMS_V1()
        attach_params.Version = 1
        attach_params.Reserved = 0

        ret = vd.AttachVirtualDisk(
            handle,
            None,
            ATTACH_VIRTUAL_DISK_FLAG_READ_ONLY,
            0,
            ctypes.byref(attach_params),
            None,
        )
        if ret != 0:
            ctypes.windll.kernel32.CloseHandle(handle)
            return None

        # GetVirtualDiskPhysicalPath
        path_buf_size = ctypes.c_ulong(1024)
        path_buf = ctypes.create_unicode_buffer(512)

        ret = vd.GetVirtualDiskPhysicalPath(
            handle,
            ctypes.byref(path_buf_size),
            path_buf,
        )
        if ret != 0:
            vd.DetachVirtualDisk(handle, DETACH_VIRTUAL_DISK_FLAG_NONE, 0)
            ctypes.windll.kernel32.CloseHandle(handle)
            return None

        phys_path = path_buf.value

        # Store handle in a module-level dict keyed by vhd_path so detach can
        # close it later.  Using a simple module dict avoids classes.
        _vhd_handles[vhd_path] = handle
        return phys_path

    except Exception:
        return None


# Module-level dict: vhd_path -> open virtdisk handle (ctypes c_void_p)
_vhd_handles: dict[str, ctypes.c_void_p] = {}


def detach_vhd(vhd_path: str) -> None:
    """Detach (unmount) a previously attached VHD."""
    vd = _load_virtdisk()
    handle = _vhd_handles.pop(vhd_path, None)
    if handle is None or vd is None:
        return
    try:
        vd.DetachVirtualDisk(handle, DETACH_VIRTUAL_DISK_FLAG_NONE, 0)
        ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        pass


def parse_vmdk_descriptor(vmdk_path: str) -> list[str]:
    """
    Parse a VMDK descriptor file (text format) to find extent filenames.
    The descriptor contains lines like:
      RW 2097152 SPARSE "disk-flat.vmdk"
      RW 2097152 FLAT "disk-flat.vmdk" 0
    Returns list of absolute paths to extent files relative to the descriptor.
    Returns [] if not a descriptor or on error.
    """
    results: list[str] = []
    try:
        base_dir = os.path.dirname(os.path.abspath(vmdk_path))
        with open(vmdk_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                # Extent descriptor lines start with RW or RDONLY
                parts = line.split()
                if len(parts) < 4:
                    continue
                if parts[0].upper() not in ("RW", "RDONLY"):
                    continue
                # parts[3] is the filename, possibly quoted
                raw_name = parts[3].strip('"').strip("'")
                if not raw_name:
                    continue
                abs_path = os.path.join(base_dir, raw_name)
                if os.path.isfile(abs_path):
                    results.append(abs_path)
    except Exception:
        pass
    return results


def scan_virtual_disk(
    path: str,
    wanted_extensions: set[str],
    type_groups: list[str],
) -> list[dict]:
    """
    High-level: detect type (VHD/VHDX vs VMDK), mount/open, run carver on it,
    return list of carved hit dicts in the same format as carver.carve_disk yields.

    For VHD/VHDX: mount → get physical path → open with reader.open_disk → carve → detach.
    For VMDK flat/sparse extents: open each extent file directly as raw bytes → carve.
    Graceful: returns [] on any error.
    """
    from drt import carver, reader as disk_reader

    ext = Path(path).suffix.lower()
    results: list[dict] = []

    patterns = carver.build_search_patterns(type_groups)

    if ext in (".vhd", ".vhdx"):
        phys_path = mount_vhd(path)
        if phys_path is None:
            return []
        try:
            handle = disk_reader.open_disk(phys_path)
            total_bytes = disk_reader.get_disk_size(handle)
            if total_bytes > 0:
                for hit in carver.carve_disk(handle, total_bytes, patterns):
                    results.append(hit)
            disk_reader.close_disk(handle)
        finally:
            detach_vhd(path)

    elif ext == ".vmdk":
        # Try descriptor-based extent parsing first
        extents = parse_vmdk_descriptor(path)
        if not extents:
            # Treat the file itself as a flat extent
            extents = [path] if os.path.isfile(path) else []

        for extent_path in extents:
            try:
                extent_size = os.path.getsize(extent_path)
                if extent_size == 0:
                    continue
                # Read extent in 1MB chunks via plain file I/O
                chunk_size = 1024 * 1024
                offset = 0
                with open(extent_path, "rb") as ef:
                    while offset < extent_size:
                        ef.seek(offset)
                        data = ef.read(chunk_size)
                        if not data:
                            break
                        hits = carver.carve_chunk(data, offset, patterns)
                        for hit in hits:
                            results.append({
                                "disk_offset":    hit["disk_offset"],
                                "extension":      hit["extension"],
                                "estimated_size": min(
                                    hit["max_size"],
                                    len(data) - (hit["disk_offset"] - offset),
                                ),
                                "chunk_data":     data,
                                "chunk_offset":   offset,
                            })
                        offset += len(data)
            except Exception:
                continue

    return results
