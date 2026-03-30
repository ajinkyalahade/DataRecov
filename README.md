# DataRecoveryTool (drt)

A deep, Windows-native CLI tool for recovering deleted files, formatted drives, and lost data from any storage medium. Runs completely locally — no internet connection, no telemetry, no cloud.

---

## Requirements

- **Windows 10 / 11 / Server 2016+**
- **Python 3.11+** (or use the standalone `.exe` — see [Packaging](#packaging))
- **Run as Administrator** — raw disk access requires elevated privileges

---

## Installation

```
git clone <repo>
cd DataRecoveryTool
pip install -r requirements.txt
```

### Run as Administrator

Raw disk access requires elevated privileges. Right-click Command Prompt or PowerShell → **Run as administrator**, then run your commands from there.

---

## Quick Start

### Option A — Interactive wizard (recommended)

```
python wizard.py
```

The wizard handles everything: checks your Python version and dependencies, warns if you're not running as admin, then walks you through each choice (drive, scan depth, file types, output folder, optional filters) before starting. No need to memorise any commands.

### Option B — CLI directly

```
python -m drt.main --help
```

All flags are optional on `drt scan` — omitting them also launches a step-by-step prompt.

### Build a standalone `.exe`

```
pip install pyinstaller
pyinstaller --onefile --name drt drt/main.py
```

The output `dist\drt.exe` runs on any Windows machine without Python installed.

---

## CLI Quick Reference

### See available drives

```
drt list-drives
```

### Preview without writing anything

```
drt preview --drive D: --depth deep --types images,documents
```

### Recover files

```
drt scan --drive D: --depth full-carve --out C:\recovery
```

### Resume an interrupted scan

```
drt resume --out C:\recovery
```

---

## Commands

### `drt list-drives`

Show all available drives and physical disks.

```
drt list-drives
```

---

### `drt list-types`

Show all recoverable file type groups and the extensions each covers.

```
drt list-types
```

---

### `drt scan`

Recover files from a drive. All flags are optional — omitting them launches an interactive wizard.

```
drt scan [OPTIONS]
```

| Flag | Description | Example |
|---|---|---|
| `--drive` | Drive letter or physical disk path | `D:` or `\\.\PhysicalDrive1` |
| `--depth` | Scan depth: `quick`, `deep`, `full-carve` | `--depth full-carve` |
| `--types` | Comma-separated type groups to recover | `--types images,documents,videos` |
| `--out` | Output directory for recovered files | `--out E:\recovery` |
| `--min-size` | Skip files smaller than this | `--min-size 10KB` |
| `--max-size` | Skip files larger than this | `--max-size 500MB` |
| `--after` | Only recover files modified after date | `--after 2023-01-01` |
| `--before` | Only recover files modified before date | `--before 2024-12-31` |

**Scan depths:**

| Depth | What it does |
|---|---|
| `quick` | MFT/FAT filesystem scan only. Fast — best for recently deleted files on intact NTFS/FAT32 drives. |
| `deep` | Filesystem scan + VSS shadow copies + Recycle Bin + Windows artifacts + browser history. |
| `full-carve` | Everything in `deep` + raw sector-by-sector signature carving. Slowest but most thorough. Use on formatted or heavily damaged drives. |

**Type groups:**

| Group | What's included |
|---|---|
| `images` | JPG, PNG, BMP, GIF, TIFF, WebP, HEIC, RAW camera formats (CR2, NEF, ARW, DNG) |
| `videos` | MP4, AVI, MKV, MOV, WMV, FLV, MPG |
| `audio` | MP3, WAV, FLAC, AAC, OGG, WMA, M4A |
| `documents` | PDF, Word, Excel, PowerPoint, TXT, CSV, XML, HTML, OpenDocument |
| `archives` | ZIP, RAR, 7Z, TAR, GZ, CAB, ISO |
| `email` | PST, OST (Outlook), EML, MSG, MBOX |
| `databases` | SQLite, Access (MDB/ACCDB) |
| `executables` | EXE, DLL, SYS, MSI, BAT, PS1 |
| `code` | Python, JS/TS, Java, C#, C++, config files |
| `browser` | Chrome, Firefox, Edge history and cached data |
| `artifacts` | Recycle Bin, LNK shortcuts, Prefetch, Registry hives, Event Logs |
| `virtual_disks` | VHD, VHDX, VMDK |
| `all` | Everything above (default) |

**Examples:**

```
# Interactive wizard — prompts for everything
drt scan

# Recover all images from D: drive, full carve
drt scan --drive D: --depth full-carve --types images --out E:\recovered_photos

# Recover documents larger than 10KB from a physical disk
drt scan --drive \\.\PhysicalDrive1 --depth deep --types documents --min-size 10KB --out E:\recovery

# Recover everything modified after Jan 2024
drt scan --drive D: --depth deep --after 2024-01-01 --out E:\recovery
```

---

### `drt preview`

Same as `scan` but writes nothing. Use this to estimate what's recoverable before committing.

```
drt preview --drive D: --depth full-carve --types images,videos
```

Accepts the same flags as `scan`. Shows the live dashboard and final summary without touching the output directory.

---

### `drt image`

Create a byte-for-byte raw image of a disk before recovery. Recommended for damaged drives — work from the image, not the original.

```
drt image --drive D: --out D:\backup\drive_d.img
drt image --drive \\.\PhysicalDrive0 --out E:\disk0.img --verify
```

| Flag | Description |
|---|---|
| `--drive` | Drive letter or physical disk path (required) |
| `--out` | Output path for the `.img` file (required) |
| `--verify` | After imaging, verify integrity via SHA-256 block comparison |

Unreadable sectors are replaced with zeroes. A sidecar file `<output>.bad_sectors.json` is written listing all bad sector offsets and lengths.

After imaging, you can scan the image file with:

```
drt scan --drive \\.\PhysicalDrive1 --out E:\recovery
```

(Mount the `.img` as a virtual disk first using Windows Disk Management or `diskpart`.)

---

### `drt resume`

Resume an interrupted scan from the last checkpoint.

```
drt resume --out E:\recovery
```

`drt scan` saves a checkpoint every 60 seconds to `<output>\.drt_checkpoint.json`. If a scan is interrupted (Ctrl+C, crash, or power loss), `drt resume` will:

1. Show what phases completed and how far the carve reached
2. Ask for confirmation
3. Skip completed phases and continue the carve from where it stopped

The checkpoint is automatically deleted on successful scan completion.

---

### `drt virtual-disk`

Scan a virtual disk image (VHD, VHDX, or VMDK) for recoverable files.

```
drt virtual-disk --file D:\backups\disk.vhd --out E:\recovery
drt virtual-disk --file D:\vm\disk.vmdk --out E:\recovery --types images,documents
```

| Flag | Description |
|---|---|
| `--file` | Path to `.vhd`, `.vhdx`, or `.vmdk` file (required) |
| `--out` | Output directory (required) |
| `--depth` | Scan depth (default: `full-carve`) |
| `--types` | Type groups to recover (default: `all`) |

VHD/VHDX disks are mounted read-only via Windows `virtdisk.dll` and automatically unmounted after the scan. VMDK extents are read directly without mounting.

---

## Output Structure

Every scan writes recovered files into a structured directory:

```
recovery_output/
├── scan_report.json          ← Full scan metadata, stats, file list
├── Images/
│   ├── jpg/
│   │   ├── recovered_0001.jpg
│   │   └── recovered_0002.jpg
│   └── png/
├── Documents/
│   ├── pdf/
│   └── docx/
├── Videos/
├── Audio/
├── Archives/
├── Email/
├── Databases/
├── Executables/
├── Code/
├── Artifacts/
├── BrowserData/
│   ├── chrome/
│   │   └── history.json
│   ├── firefox/
│   │   └── history.json
│   └── edge/
│       └── history.json
├── VirtualDisks/
└── Unclassified/
```

### scan_report.json

Every scan produces a machine-readable report:

```json
{
  "tool": "DataRecoveryTool",
  "version": "0.1.0",
  "scan_date": "2024-06-01T14:23:00Z",
  "drive": "\\\\.\\PhysicalDrive1",
  "depth": "full-carve",
  "type_groups": ["images", "documents"],
  "duration_seconds": 3842.1,
  "stats": {
    "total_files_found": 1247,
    "by_type": { "jpg": 842, "pdf": 124, "docx": 67 },
    "by_group": { "images": 912, "documents": 335 },
    "total_bytes_recovered": 4831838208
  },
  "files": [
    {
      "extension": ".jpg",
      "disk_offset": 442499072,
      "output_path": "E:\\recovery\\Images\\jpg\\recovered_0001.jpg",
      "size_bytes": 245760
    }
  ]
}
```

---

## Live Dashboard

During a scan, a live dashboard shows three panels:

```
┌─ Scan Progress ─────────────────────┐  ┌─ Files Found ──────────────────────┐
│ Phase:   Phase 6: Deep Carve        │  │ Total:  1,247 files                │
│ Drive:   \\.\PhysicalDrive1         │  │                                    │
│ Scanned: 124.5 GB / 500 GB (24.9%)  │  │  Images    ████████████████  842   │
│ Speed:   245.2 MB/s                 │  │  Documents ████             124    │
│ ETA:     ~28 min                    │  │  Videos    ██                67    │
│                                     │  │  Audio     ██                58    │
│ [███████░░░░░░░░░░░░░░] 24.9%       │  │  Archives  █                 43    │
└─────────────────────────────────────┘  └────────────────────────────────────┘
┌─ Recent Finds ──────────────────────────────────────────────────────────────┐
│  .jpg   recovered_0842.jpg   245 KB    offset 0x1A4F3000                    │
│  .pdf   recovered_0841.pdf   1.2 MB    offset 0x1A3C1000                    │
│  .docx  recovered_0840.docx  86 KB     offset 0x1A2B4000                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Tips

**Always image first on failing drives**

If a drive is making clicking sounds, shows SMART errors, or is intermittently inaccessible — image it first with `drt image`, then recover from the image. This avoids additional wear on the failing drive.

```
drt image --drive \\.\PhysicalDrive2 --out E:\failing_drive.img --verify
```

**Use `preview` before `scan` on large drives**

A full-carve of a 2TB drive takes time. Run `preview` first to see what's recoverable and narrow your `--types` selection.

```
drt preview --drive D: --depth full-carve --types images,documents
```

**Narrow your types for faster scans**

`full-carve` with `--types images` is significantly faster than `--types all` on a large drive because fewer signature patterns are searched.

**Point `--out` to a different drive**

Never recover to the same drive you're scanning. Use a separate external drive or a different partition.

```
# Scanning D: — output to E:
drt scan --drive D: --out E:\recovery
```

**Resume long scans**

If a scan is running overnight and gets interrupted, just run:

```
drt resume --out E:\recovery
```

It will skip completed phases and jump straight to where the carve stopped.

---

## Packaging

To build a distributable `.exe` that needs no Python installation:

```
pip install pyinstaller
pyinstaller --onefile --name drt --icon=drt.ico drt\main.py
```

The `.exe` will be in the `dist\` folder. Copy it to any Windows machine and run elevated.

---

## What Gets Recovered

| Source | Depth Required | Notes |
|---|---|---|
| Deleted files (NTFS MFT) | `quick` | Recovers filename, size, and file content via cluster chains |
| Deleted files (FAT32/exFAT) | `quick` | Follows FAT cluster chains for content |
| VSS shadow copies | `deep` | Copies files from Windows volume snapshots |
| Recycle Bin | `deep` | Restores files with original path and deletion timestamp |
| LNK shortcut files | `deep` | Reveals recently accessed files (even if target is deleted) |
| Prefetch files | `deep` | Evidence of programs that ran, including deleted ones |
| Browser history | `deep` | Chrome, Firefox, Edge — URLs, titles, visit counts |
| Raw signature carve | `full-carve` | Recovers files by magic byte patterns even with no filesystem |
| Virtual disks | any | VHD/VHDX/VMDK scanned as physical media |
