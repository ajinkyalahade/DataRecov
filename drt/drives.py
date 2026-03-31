"""
Windows drive enumeration via ctypes.

Calls GetLogicalDrives, GetVolumeInformationW, GetDriveTypeW,
GetDiskFreeSpaceExW for logical drives, and CreateFileW +
DeviceIoControl (IOCTL_STORAGE_QUERY_PROPERTY) for physical disks.

All code assumes Windows; importing on non-Windows will raise ImportError
at runtime when ctypes.windll is accessed.
"""

import ctypes
import ctypes.wintypes
import struct
import sys


# ---------------------------------------------------------------------------
# Windows constants
# ---------------------------------------------------------------------------
DRIVE_UNKNOWN     = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE   = 2
DRIVE_FIXED       = 3
DRIVE_REMOTE      = 4
DRIVE_CDROM       = 5
DRIVE_RAMDISK     = 6

GENERIC_READ                = 0x80000000
FILE_SHARE_READ             = 0x00000001
FILE_SHARE_WRITE            = 0x00000002
OPEN_EXISTING               = 3
FILE_FLAG_NO_BUFFERING      = 0x20000000
INVALID_HANDLE_VALUE        = ctypes.c_void_p(-1).value

IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
StorageDeviceProperty        = 0
PropertyStandardQuery        = 0


# ---------------------------------------------------------------------------
# Drive type helpers
# ---------------------------------------------------------------------------

def get_drive_type_name(drive_type_int: int) -> str:
    """Return a human-readable drive type string for a GetDriveTypeW result."""
    return {
        DRIVE_UNKNOWN:     "Unknown",
        DRIVE_NO_ROOT_DIR: "No root dir",
        DRIVE_REMOVABLE:   "Removable",
        DRIVE_FIXED:       "Fixed",
        DRIVE_REMOTE:      "Network",
        DRIVE_CDROM:       "CD-ROM",
        DRIVE_RAMDISK:     "RAM disk",
    }.get(drive_type_int, f"Unknown ({drive_type_int})")


# ---------------------------------------------------------------------------
# Logical drive enumeration
# ---------------------------------------------------------------------------

def list_drives() -> list[dict]:
    """
    Return a list of logical drive dicts.

    Each dict contains:
        letter        (str)   e.g. "C:"
        label         (str)   volume label
        filesystem    (str)   e.g. "NTFS"
        drive_type    (int)   raw GetDriveTypeW value
        drive_type_name (str) human-readable type
        total_bytes   (int)
        free_bytes    (int)
        is_removable  (bool)
        is_fixed      (bool)
        is_network    (bool)
    """
    kernel32 = ctypes.windll.kernel32

    bitmask: int = kernel32.GetLogicalDrives()
    drives: list[dict] = []

    for i in range(26):
        if not (bitmask >> i) & 1:
            continue
        letter = chr(ord("A") + i) + ":"
        root   = letter + "\\"

        drive_type: int = kernel32.GetDriveTypeW(root)

        # Volume information
        label_buf      = ctypes.create_unicode_buffer(261)
        fs_buf         = ctypes.create_unicode_buffer(261)
        serial         = ctypes.wintypes.DWORD(0)
        max_comp_len   = ctypes.wintypes.DWORD(0)
        fs_flags       = ctypes.wintypes.DWORD(0)

        ok = kernel32.GetVolumeInformationW(
            root,
            label_buf, 261,
            ctypes.byref(serial),
            ctypes.byref(max_comp_len),
            ctypes.byref(fs_flags),
            fs_buf, 261,
        )
        label      = label_buf.value if ok else ""
        filesystem = fs_buf.value   if ok else ""

        # Free space
        free_bytes_available = ctypes.c_ulonglong(0)
        total_bytes          = ctypes.c_ulonglong(0)
        total_free_bytes     = ctypes.c_ulonglong(0)

        kernel32.GetDiskFreeSpaceExW(
            root,
            ctypes.byref(free_bytes_available),
            ctypes.byref(total_bytes),
            ctypes.byref(total_free_bytes),
        )

        drives.append({
            "letter":          letter,
            "label":           label,
            "filesystem":      filesystem,
            "drive_type":      drive_type,
            "drive_type_name": get_drive_type_name(drive_type),
            "total_bytes":     total_bytes.value,
            "free_bytes":      total_free_bytes.value,
            "is_removable":    drive_type == DRIVE_REMOVABLE,
            "is_fixed":        drive_type == DRIVE_FIXED,
            "is_network":      drive_type == DRIVE_REMOTE,
        })

    return drives


# ---------------------------------------------------------------------------
# Physical disk enumeration
# ---------------------------------------------------------------------------

def _query_storage_property(handle: int) -> dict:
    """
    Issue IOCTL_STORAGE_QUERY_PROPERTY to get device descriptor.
    Returns dict with model and serial_number strings.
    On failure returns placeholder strings.
    """
    kernel32 = ctypes.windll.kernel32

    # STORAGE_PROPERTY_QUERY (8 bytes: PropertyId DWORD, QueryType DWORD)
    query = struct.pack("<II", StorageDeviceProperty, PropertyStandardQuery)

    # Output buffer — STORAGE_DEVICE_DESCRIPTOR is variable length; 1 KB is plenty.
    out_size   = 1024
    out_buf    = ctypes.create_string_buffer(out_size)
    bytes_ret  = ctypes.wintypes.DWORD(0)

    ok = kernel32.DeviceIoControl(
        ctypes.c_void_p(handle),
        IOCTL_STORAGE_QUERY_PROPERTY,
        query, len(query),
        out_buf, out_size,
        ctypes.byref(bytes_ret),
        None,
    )

    if not ok or bytes_ret.value < 36:
        return {"model": "(requires elevation)", "serial_number": ""}

    raw = out_buf.raw

    # STORAGE_DEVICE_DESCRIPTOR layout (partial):
    # DWORD Version, Size, DeviceType, DeviceTypeModifier
    # BOOLEAN RemovableMedia, CommandQueueing
    # DWORD VendorIdOffset, ProductIdOffset, ProductRevisionOffset,
    #       SerialNumberOffset
    # STORAGE_BUS_TYPE BusType (DWORD)
    # DWORD RawPropertiesLength
    try:
        (version, size, dev_type, dev_type_mod,
         removable, cmd_queue,
         vendor_offset, product_offset, revision_offset, serial_offset,
         bus_type, raw_props_len) = struct.unpack_from("<IIIIBBIIIIII", raw, 0)
    except struct.error:
        return {"model": "(parse error)", "serial_number": ""}

    def _read_ascii(offset: int) -> str:
        if offset == 0 or offset >= len(raw):
            return ""
        end = raw.index(b"\x00", offset)
        return raw[offset:end].decode("ascii", errors="replace").strip()

    vendor  = _read_ascii(vendor_offset)
    product = _read_ascii(product_offset)
    serial  = _read_ascii(serial_offset)

    model = f"{vendor} {product}".strip() or "(unknown)"
    return {"model": model, "serial_number": serial}


def get_physical_disks() -> list[dict]:
    """
    Return a list of physical disk dicts by probing \\.\PhysicalDrive0–9.

    Each dict contains:
        path         (str)  e.g. "\\\\.\\PhysicalDrive0"
        size_bytes   (int)  total disk size, 0 if not readable
        model        (str)  device model string
        serial_number (str) device serial number
    """
    kernel32 = ctypes.windll.kernel32
    disks: list[dict] = []

    for n in range(10):
        path = f"\\\\.\\PhysicalDrive{n}"

        handle = kernel32.CreateFileW(
            path,
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )

        if handle == INVALID_HANDLE_VALUE:
            # Disk does not exist or access fully denied — stop enumerating
            err = kernel32.GetLastError()
            if err == 2:   # ERROR_FILE_NOT_FOUND
                break
            if err == 5:   # ERROR_ACCESS_DENIED — disk exists but no elevation
                disks.append({
                    "path":          path,
                    "size_bytes":    0,
                    "model":         "(requires elevation)",
                    "serial_number": "",
                })
                continue
            # Any other error — skip this index
            continue

        try:
            size_bytes = _get_disk_size_from_handle(handle, kernel32)
            info       = _query_storage_property(handle)
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))

        if size_bytes > 0:
            disks.append({
                "path":          path,
                "size_bytes":    size_bytes,
                "model":         info["model"],
                "serial_number": info["serial_number"],
            })

    # Drop zero-size entries — these are virtual adapters (Hyper-V, Docker,
    # VPN) that respond to CreateFileW but hold no storage.
    return [d for d in disks if d["size_bytes"] > 0]


def _get_disk_size_from_handle(handle: int, kernel32) -> int:
    """
    Use IOCTL_DISK_GET_DRIVE_GEOMETRY_EX to get total disk size in bytes.
    Returns 0 on failure.
    """
    IOCTL_DISK_GET_DRIVE_GEOMETRY_EX = 0x000700A0

    # DISK_GEOMETRY_EX is at least 24 bytes
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

    if not ok or bytes_ret.value < 24:
        return 0

    # DISK_GEOMETRY_EX:
    #   DISK_GEOMETRY Geometry (24 bytes): Cylinders(8) + MediaType(4) +
    #     TracksPerCylinder(4) + SectorsPerTrack(4) + BytesPerSector(4)
    #   LARGE_INTEGER DiskSize (8 bytes) at offset 24
    try:
        (disk_size,) = struct.unpack_from("<q", out_buf.raw, 24)
        return max(disk_size, 0)
    except struct.error:
        return 0
