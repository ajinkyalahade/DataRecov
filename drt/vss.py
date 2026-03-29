"""
VSS (Volume Shadow Copy Service) shadow copy scanner.

Enumerates shadow copies via WMI (win32com) and walks each shadow copy
volume as a normal directory tree to find files matching wanted extensions.

Requires pywin32. Gracefully returns [] if pywin32 is unavailable or no
shadow copies exist.
"""

import os
from pathlib import Path


def list_shadow_copies() -> list[dict]:
    """
    Use WMI (win32com) to enumerate Win32_ShadowCopy instances.
    Returns list of dicts: {id, volume, device_object, creation_time, provider}.
    Returns [] if WMI unavailable or no shadow copies exist.
    """
    try:
        import win32com.client  # type: ignore[import]
        wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\cimv2")
        shadow_copies = wmi.ExecQuery("SELECT * FROM Win32_ShadowCopy")
        results: list[dict] = []
        for sc in shadow_copies:
            results.append({
                "id":            getattr(sc, "ID", ""),
                "volume":        getattr(sc, "VolumeName", ""),
                "device_object": getattr(sc, "DeviceObject", ""),
                "creation_time": getattr(sc, "InstallDate", ""),
                "provider":      getattr(sc, "ProviderID", ""),
            })
        return results
    except Exception:
        return []


def scan_shadow_copy(device_object: str, wanted_extensions: set[str]) -> list[dict]:
    """
    Walk the shadow copy volume as a normal directory tree via os.walk and
    return files matching wanted_extensions.

    Returns list of dicts:
        path      (str)  full path inside the shadow copy
        name      (str)  filename
        extension (str)
        size_bytes (int)
        source    (str)  "vss"
        shadow_id (str)  the shadow copy device_object
    """
    results: list[dict] = []
    try:
        # The device_object path ends with no trailing slash; append \
        root = device_object.rstrip("\\") + "\\"
        for dirpath, _dirnames, filenames in os.walk(root):
            for fname in filenames:
                ext = Path(fname).suffix.lower()
                if ext not in wanted_extensions:
                    continue
                full_path = os.path.join(dirpath, fname)
                try:
                    size_bytes = os.path.getsize(full_path)
                except OSError:
                    size_bytes = 0
                results.append({
                    "path":       full_path,
                    "name":       fname,
                    "extension":  ext,
                    "size_bytes": size_bytes,
                    "source":     "vss",
                    "shadow_id":  device_object,
                })
    except Exception:
        pass
    return results


def scan(wanted_extensions: set[str]) -> list[dict]:
    """
    Entry point: list all shadow copies and scan each one.
    Returns combined results. Graceful failure returns [].
    """
    try:
        copies = list_shadow_copies()
        results: list[dict] = []
        for sc in copies:
            device = sc.get("device_object", "")
            if not device:
                continue
            try:
                results.extend(scan_shadow_copy(device, wanted_extensions))
            except Exception:
                pass
        return results
    except Exception:
        return []
