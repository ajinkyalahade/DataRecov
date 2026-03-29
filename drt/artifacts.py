"""
Windows artifact recovery.

Covers:
  - Recycle Bin ($Recycle.Bin) — $I metadata + $R content files
  - LNK shortcut files — recently accessed file references
  - Prefetch files — evidence of previously executed programs

All binary parsing uses stdlib struct. Windows-only.
"""

import os
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _filetime_to_iso(filetime: int) -> str:
    """Convert a Windows FILETIME (100-ns intervals since 1601-01-01) to ISO8601."""
    if filetime == 0:
        return ""
    # FILETIME epoch: Jan 1 1601; Unix epoch: Jan 1 1970
    # Difference: 116444736000000000 100-ns intervals
    EPOCH_DIFF = 116_444_736_000_000_000
    unix_us = (filetime - EPOCH_DIFF) // 10  # microseconds
    try:
        dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=unix_us)
        return dt.isoformat()
    except (OverflowError, OSError):
        return ""


# ---------------------------------------------------------------------------
# Recycle Bin
# ---------------------------------------------------------------------------

def scan_recycle_bin(drive_letter: str) -> list[dict]:
    """
    Walk <drive_letter>\\$Recycle.Bin for $I and $R file pairs.

    $I file binary format:
      Offset 0:  header (QWORD)
      Offset 8:  file_size (QWORD)
      Offset 16: deletion_time (FILETIME, QWORD)
      Offset 24: original_path (UTF-16LE, null-terminated)

    Returns list of dicts:
        original_path (str)
        size_bytes    (int)
        deletion_time (str)  ISO8601
        r_file_path   (str)  path to the $R file
        extension     (str)
        source        (str)  "recycle_bin"
    """
    results: list[dict] = []
    letter = drive_letter.rstrip("\\").rstrip("/")
    recycle_root = letter + "\\$Recycle.Bin"

    try:
        if not os.path.isdir(recycle_root):
            return []

        for sid_dir in os.listdir(recycle_root):
            sid_path = os.path.join(recycle_root, sid_dir)
            if not os.path.isdir(sid_path):
                continue
            try:
                entries = os.listdir(sid_path)
            except PermissionError:
                continue

            for fname in entries:
                if not fname.upper().startswith("$I"):
                    continue
                i_path = os.path.join(sid_path, fname)
                # Corresponding $R file: replace $I prefix with $R
                r_name = "$R" + fname[2:]
                r_path = os.path.join(sid_path, r_name)

                try:
                    raw = Path(i_path).read_bytes()
                except OSError:
                    continue

                if len(raw) < 28:
                    continue

                try:
                    file_size, = struct.unpack_from("<Q", raw, 8)
                    deletion_time, = struct.unpack_from("<Q", raw, 16)
                    # Original path: UTF-16LE at offset 24, null-terminated
                    path_data = raw[24:]
                    # Find null terminator (two zero bytes, word-aligned)
                    null_idx = path_data.find(b"\x00\x00")
                    if null_idx % 2 != 0:
                        null_idx += 1
                    if null_idx > 0:
                        original_path = path_data[:null_idx].decode("utf-16-le", errors="replace")
                    else:
                        original_path = path_data.decode("utf-16-le", errors="replace").rstrip("\x00")
                except (struct.error, UnicodeDecodeError):
                    continue

                ext = Path(original_path).suffix.lower() if original_path else ""

                results.append({
                    "original_path": original_path,
                    "size_bytes":    file_size,
                    "deletion_time": _filetime_to_iso(deletion_time),
                    "r_file_path":   r_path if os.path.isfile(r_path) else "",
                    "extension":     ext,
                    "source":        "recycle_bin",
                })

    except Exception:
        pass

    return results


# ---------------------------------------------------------------------------
# LNK files
# ---------------------------------------------------------------------------

# LNK LinkFlags bits
_HAS_LINK_TARGET_ID_LIST = 0x00000001
_HAS_LINK_INFO           = 0x00000002
_HAS_NAME                = 0x00000004
_HAS_RELATIVE_PATH       = 0x00000008
_HAS_WORKING_DIR         = 0x00000010
_HAS_ARGUMENTS           = 0x00000020
_HAS_ICON_LOCATION       = 0x00000040
_IS_UNICODE              = 0x00000080


def _parse_lnk(lnk_path: str) -> dict:
    """
    Parse a .lnk file and return a dict with target_path and size_bytes.
    Returns empty strings on any parse error.
    """
    result = {"target_path": "", "size_bytes": 0}
    try:
        raw = Path(lnk_path).read_bytes()
        if len(raw) < 76:
            return result

        # Header
        signature, = struct.unpack_from("<I", raw, 0)
        if signature != 0x0000004C:
            return result

        link_flags, = struct.unpack_from("<I", raw, 20)
        file_size,  = struct.unpack_from("<I", raw, 52)
        result["size_bytes"] = file_size

        pos = 76  # after header

        # Skip IDList if present
        if link_flags & _HAS_LINK_TARGET_ID_LIST:
            if pos + 2 > len(raw):
                return result
            id_list_size, = struct.unpack_from("<H", raw, pos)
            pos += 2 + id_list_size

        # Skip LinkInfo if present
        if link_flags & _HAS_LINK_INFO:
            if pos + 4 > len(raw):
                return result
            link_info_size, = struct.unpack_from("<I", raw, pos)
            # Try to extract LocalBasePath from LinkInfo
            if link_info_size >= 28 and pos + link_info_size <= len(raw):
                link_info_flags, = struct.unpack_from("<I", raw, pos + 8)
                local_base_path_offset, = struct.unpack_from("<I", raw, pos + 16)
                if link_info_flags & 0x01 and local_base_path_offset > 0:
                    path_start = pos + local_base_path_offset
                    if path_start < len(raw):
                        null_idx = raw.find(b"\x00", path_start)
                        if null_idx > path_start:
                            result["target_path"] = raw[path_start:null_idx].decode("ascii", errors="replace")
            pos += link_info_size

        # StringData section — each entry is CountCharacters (WORD) + string
        is_unicode = bool(link_flags & _IS_UNICODE)

        def _read_string_data(offset: int) -> tuple[str, int]:
            """Read a StringData entry. Returns (string, new_offset)."""
            if offset + 2 > len(raw):
                return "", offset
            count, = struct.unpack_from("<H", raw, offset)
            offset += 2
            if is_unicode:
                byte_len = count * 2
                if offset + byte_len > len(raw):
                    return "", offset + byte_len
                s = raw[offset:offset + byte_len].decode("utf-16-le", errors="replace")
            else:
                if offset + count > len(raw):
                    return "", offset + count
                s = raw[offset:offset + count].decode("ascii", errors="replace")
            return s, offset + (count * 2 if is_unicode else count)

        # NAME_STRING
        if link_flags & _HAS_NAME:
            _, pos = _read_string_data(pos)

        # RELATIVE_PATH
        if link_flags & _HAS_RELATIVE_PATH:
            _, pos = _read_string_data(pos)

        # WORKING_DIR
        if link_flags & _HAS_WORKING_DIR:
            _, pos = _read_string_data(pos)

        # COMMAND_LINE_ARGUMENTS — not needed, skip if we already have target
        if not result["target_path"] and link_flags & _HAS_ARGUMENTS:
            _, pos = _read_string_data(pos)

        # ICON_LOCATION — may contain the actual target for some LNKs
        if not result["target_path"] and link_flags & _HAS_ICON_LOCATION:
            icon_loc, _ = _read_string_data(pos)
            if icon_loc and not icon_loc.endswith((".ico", ".exe", ".dll")):
                result["target_path"] = icon_loc

    except Exception:
        pass
    return result


def scan_lnk_files(search_paths: list[str]) -> list[dict]:
    """
    Find .lnk files in the provided search_paths (and common defaults).

    Returns list of dicts:
        lnk_path    (str)
        target_path (str)
        size_bytes  (int)
        source      (str)  "lnk"
    """
    results: list[dict] = []

    # Add standard Windows recent folders
    standard_paths: list[str] = list(search_paths)
    appdata = os.environ.get("APPDATA", "")
    userprofile = os.environ.get("USERPROFILE", "")

    if appdata:
        standard_paths.append(os.path.join(appdata, "Microsoft", "Windows", "Recent"))
    if userprofile:
        standard_paths.append(os.path.join(userprofile, "Desktop"))

    # All users' Recent folders
    systemdrive = os.environ.get("SystemDrive", "C:")
    all_users = os.path.join(systemdrive, "Users")
    try:
        if os.path.isdir(all_users):
            for user in os.listdir(all_users):
                user_recent = os.path.join(all_users, user, "AppData", "Roaming",
                                           "Microsoft", "Windows", "Recent")
                if os.path.isdir(user_recent):
                    standard_paths.append(user_recent)
    except PermissionError:
        pass

    seen: set[str] = set()
    for search_dir in standard_paths:
        try:
            if not os.path.isdir(search_dir):
                continue
            for root, _dirs, files in os.walk(search_dir):
                for fname in files:
                    if not fname.lower().endswith(".lnk"):
                        continue
                    full = os.path.join(root, fname)
                    if full in seen:
                        continue
                    seen.add(full)
                    parsed = _parse_lnk(full)
                    results.append({
                        "lnk_path":    full,
                        "target_path": parsed["target_path"],
                        "size_bytes":  parsed["size_bytes"],
                        "source":      "lnk",
                    })
        except Exception:
            pass

    return results


# ---------------------------------------------------------------------------
# Prefetch
# ---------------------------------------------------------------------------

def scan_prefetch(prefetch_dir: str = r"C:\Windows\Prefetch") -> list[dict]:
    """
    List .pf prefetch files — evidence of programs that ran.

    .pf header:
      Offset 0:  version (DWORD)
      Offset 4:  signature (DWORD) = 0x53434341 ("SCCA")
      Offset 8:  unknown (DWORD)
      Offset 12: file_size (DWORD)
      Offset 16: exe_name (UTF-16LE, 60 bytes = 30 chars)

    Returns list of dicts:
        pf_path   (str)
        exe_name  (str)
        file_size (int)
        source    (str)  "prefetch"
    """
    _SCCA_SIG = 0x53434341
    results: list[dict] = []

    try:
        if not os.path.isdir(prefetch_dir):
            return []
        for fname in os.listdir(prefetch_dir):
            if not fname.lower().endswith(".pf"):
                continue
            pf_path = os.path.join(prefetch_dir, fname)
            try:
                raw = Path(pf_path).read_bytes()
                if len(raw) < 76:
                    continue
                sig, = struct.unpack_from("<I", raw, 4)
                if sig != _SCCA_SIG:
                    continue
                file_size, = struct.unpack_from("<I", raw, 12)
                exe_raw = raw[16:76]  # 60 bytes = 30 UTF-16LE chars
                exe_name = exe_raw.decode("utf-16-le", errors="replace").rstrip("\x00")
                results.append({
                    "pf_path":   pf_path,
                    "exe_name":  exe_name,
                    "file_size": file_size,
                    "source":    "prefetch",
                })
            except Exception:
                continue
    except Exception:
        pass

    return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def scan(drive_letter: str = "C:") -> list[dict]:
    """
    Run all artifact scanners and return combined results.
    Each scanner is wrapped in try/except; failures skip that scanner.
    """
    results: list[dict] = []

    try:
        results.extend(scan_recycle_bin(drive_letter))
    except Exception:
        pass

    try:
        results.extend(scan_lnk_files([]))
    except Exception:
        pass

    try:
        systemdrive = os.environ.get("SystemDrive", drive_letter.rstrip("\\"))
        prefetch_dir = systemdrive + "\\Windows\\Prefetch"
        results.extend(scan_prefetch(prefetch_dir))
    except Exception:
        pass

    return results
