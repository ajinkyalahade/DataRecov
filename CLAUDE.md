# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Tool

```bash
# Interactive step-by-step wizard (recommended)
python wizard.py

# CLI directly
python -m drt.main --help

# Build standalone .exe
pip install pyinstaller
pyinstaller --onefile --name drt drt/main.py
```

`wizard.py` is the primary user-facing entry point. It auto-checks dependencies, warns about admin privileges, walks through all choices, then runs the scan inline (calls `drt` internals directly for physical drives; shells out to `drt.main` only for virtual disks).

**Platform constraint:** Windows-only. Raw disk access requires `pywin32` (WMI, COM, virtdisk.dll, kernel32) and elevated/administrator privileges. The tool degrades gracefully if optional Windows components are unavailable.

## Dependencies

```bash
pip install -r requirements.txt  # typer, rich, pywin32
```

Python 3.11+ required. No test suite exists yet.

## Architecture

The codebase is organized into three logical layers:

**CLI layer** — `drt/main.py` (1,267 lines). All Typer commands live here: `scan`, `preview`, `image`, `resume`, `virtual-disk`, `list-drives`, `list-types`. The main scan flow `_run_scan()` orchestrates all phases in order: MFT → FAT → VSS → Recycle Bin → Artifacts → Browser → Full-Carve.

**Scanning layer** — each module handles one recovery technique:
- `mft.py` — NTFS Master File Table parser for deleted files
- `fat.py` — FAT32/exFAT deleted entry recovery
- `carver.py` — Signature-based file carving from raw sectors (uses `signatures.py`)
- `vss.py` — Volume Shadow Copy scanning via WMI
- `artifacts.py` — Windows artifacts: Recycle Bin, LNK shortcuts, Prefetch, Registry
- `browser.py` — Chrome, Firefox, Edge history/cache extraction
- `virtual_disk.py` — VHD/VHDX/VMDK mounting via virtdisk.dll; VMDK via raw extent reading

**I/O and support layer:**
- `reader.py` — Raw sector-level disk reads via Windows kernel32 (`open_disk`, `read_sectors`, `close_disk`)
- `writer.py` — Organizes recovered files into hierarchical output directories by type
- `progress.py` — Rich live dashboard (`ProgressTracker`, `make_dashboard`)
- `checkpoint.py` — JSON-based scan state persistence for resume capability
- `report.py` — JSON scan report generation
- `filters.py` — Size/date filtering (`parse_size`, `parse_date`, `make_filter`)
- `types.py` — Defines 11 recoverable type groups + "all" meta-group
- `drives.py` — Windows drive/physical disk enumeration
- `imager.py` — Byte-for-byte disk imaging with bad-sector handling

## Scan Depths

Three depths, each a superset of the previous:
1. **quick** — MFT + FAT only
2. **deep** — + VSS + Recycle Bin + Artifacts + Browser
3. **full-carve** — + raw sector signature carving (slowest; handles formatted/corrupted disks)

## Key Design Constraints

- **Read-only** — the tool never writes to the source disk
- **No external databases** — all type/signature data is hardcoded in `types.py` and `signatures.py`
- **Streaming** — files are processed and written as found; checkpoints are saved periodically
- `reader.py` is the only module that touches raw disk I/O; all other scanners call into it
