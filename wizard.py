"""
DataRecoveryTool — Interactive Terminal Wizard
Run this file directly:  python wizard.py
"""

import os
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Step 0 — Platform and Python version gate
# ---------------------------------------------------------------------------

def _bail(msg: str) -> None:
    print(f"\n[ERROR] {msg}\n", file=sys.stderr)
    input("Press Enter to exit...")
    sys.exit(1)


if sys.platform != "win32":
    _bail(
        "DataRecoveryTool requires Windows 10 or 11.\n"
        "       Raw disk access is only available via Windows kernel APIs."
    )

if sys.version_info < (3, 11):
    _bail(
        f"Python 3.11+ is required. You are running {sys.version}.\n"
        "       Download it from https://www.python.org/downloads/"
    )


# ---------------------------------------------------------------------------
# Step 1 — Dependency check / auto-install
# ---------------------------------------------------------------------------

REQUIRED = ["typer", "rich"]

def _check_deps() -> None:
    missing = []
    for pkg in REQUIRED:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)

    if not missing:
        return

    print("\n" + "=" * 60)
    print("  Missing dependencies: " + ", ".join(missing))
    print("=" * 60)
    ans = input("\n  Install them now? [Y/n]: ").strip().lower()
    if ans in ("", "y", "yes"):
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--quiet"] + missing
            )
            print("  Installed successfully.\n")
        except subprocess.CalledProcessError:
            _bail(
                "pip install failed.\n"
                "       Run manually:  pip install " + " ".join(missing)
            )
    else:
        _bail("Cannot continue without required packages.")


_check_deps()

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.table import Table

console = Console()


# ---------------------------------------------------------------------------
# Step 2 — Admin check
# ---------------------------------------------------------------------------

def _is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _check_admin() -> None:
    if _is_admin():
        return

    console.print()
    console.print(Panel(
        "[bold yellow]Not running as Administrator[/bold yellow]\n\n"
        "Raw disk access requires elevated privileges.\n"
        "Without admin rights, most recovery techniques will fail.\n\n"
        "[bold]How to fix:[/bold]\n"
        "  1. Close this window\n"
        "  2. Right-click Command Prompt or PowerShell\n"
        "  3. Select [bold cyan]'Run as administrator'[/bold cyan]\n"
        "  4. Run:  [bold]python wizard.py[/bold]",
        title="[red]Admin Required[/red]",
        border_style="red",
    ))
    console.print()
    ans = Prompt.ask(
        "Continue anyway? Results will be limited",
        choices=["y", "n"],
        default="n",
    )
    if ans == "n":
        sys.exit(0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _step_header(num: int, title: str) -> None:
    console.print()
    console.print(Rule(f"[bold cyan]Step {num}[/bold cyan] — {title}", style="cyan"))


def _drive_letter_from_path(drive_path: str) -> str:
    """
    Extract the drive letter (e.g. 'D:') from a Windows disk path.
    Handles: '\\\\.\\D:' and 'D:' forms.
    """
    if drive_path.startswith("\\\\.\\"):
        candidate = drive_path[4:]          # e.g. "D:" or "PhysicalDrive0"
        if len(candidate) >= 2 and candidate[1] == ":":
            return candidate[:2].upper()
    elif len(drive_path) >= 2 and drive_path[1] == ":":
        return drive_path[:2].upper()
    return "C:"                             # fallback for physical disk paths


# ---------------------------------------------------------------------------
# EOF trimming — truncate carved bytes to the file's natural end
# ---------------------------------------------------------------------------

_EOF_MARKERS: dict[str, list[bytes]] = {
    ".jpg":  [b"\xff\xd9"],
    ".jpeg": [b"\xff\xd9"],
    ".png":  [b"\x49\x45\x4e\x44\xae\x42\x60\x82"],   # IEND chunk CRC
    ".gif":  [b"\x00\x3b"],
    ".pdf":  [b"%%EOF"],
}


def _trim_to_eof(data: bytes, extension: str) -> bytes:
    """Return data truncated at the first EOF marker for the given extension."""
    markers = _EOF_MARKERS.get(extension.lower())
    if not markers:
        return data
    best = len(data)
    for marker in markers:
        pos = data.find(marker)
        if pos != -1:
            best = min(best, pos + len(marker))
    return data[:best]


# ---------------------------------------------------------------------------
# Step 3 — Choose recovery target
# ---------------------------------------------------------------------------

def _choose_target() -> dict:
    """Returns {'mode': 'drive'|'virtual', 'value': <path string>}"""
    _step_header(1, "Choose Recovery Target")

    console.print("\nWhat do you want to recover from?\n")
    console.print("  [bold cyan]1[/bold cyan]  A physical drive or partition (C:, D:, USB stick, etc.)")
    console.print("  [bold cyan]2[/bold cyan]  A virtual disk image file (.vhd, .vhdx, .vmdk)")

    while True:
        choice = Prompt.ask("\nTarget type", choices=["1", "2"], default="1")
        if choice == "1":
            return _choose_drive()
        else:
            return _choose_virtual_disk()


def _choose_drive() -> dict:
    try:
        from drt.drives import get_physical_disks, list_drives
        drives   = list_drives()
        physical = get_physical_disks()
    except Exception as exc:
        console.print(f"\n[red]Could not enumerate drives: {exc}[/red]")
        console.print("Make sure you are running as Administrator.")
        drives, physical = [], []

    if not drives and not physical:
        console.print(
            "\n[yellow]No drives detected.[/yellow]  "
            "Enter a drive letter (e.g. C:) or physical disk path manually."
        )
        raw = Prompt.ask("Drive").strip()
        if len(raw) == 2 and raw[1] == ":":
            return {"mode": "drive", "value": "\\\\.\\"+raw.upper()}
        return {"mode": "drive", "value": raw}

    t = Table(title="Logical Drives", show_lines=True)
    t.add_column("#",          style="dim",    width=4)
    t.add_column("Drive",      style="cyan",   width=8)
    t.add_column("Label",      style="green")
    t.add_column("Type",       style="yellow")
    t.add_column("Filesystem", style="blue")
    t.add_column("Total",      justify="right")
    t.add_column("Free",       justify="right")
    for i, d in enumerate(drives, 1):
        t.add_row(
            str(i),
            d["letter"],
            d["label"] or "(no label)",
            d["drive_type_name"],
            d["filesystem"] or "—",
            _fmt_bytes(d["total_bytes"]),
            _fmt_bytes(d["free_bytes"]),
        )
    console.print(t)

    if physical:
        p = Table(title="Physical Disks", show_lines=True)
        p.add_column("#",     style="dim",   width=4)
        p.add_column("Path",  style="cyan")
        p.add_column("Model", style="green")
        p.add_column("Size",  justify="right")
        for j, disk in enumerate(physical, 1):
            p.add_row(
                f"P{j}",
                disk["path"],
                disk["model"],
                _fmt_bytes(disk["size_bytes"]) if disk["size_bytes"] else "—",
            )
        console.print(p)

    console.print("\nEnter an index (1, 2, …) or P1/P2/… for a physical disk.")
    console.print("Or type a drive letter directly, e.g. [bold]D:[/bold]")

    while True:
        raw = Prompt.ask("Select drive").strip()

        # Numeric index → logical drive
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(drives):
                letter = drives[idx]["letter"]          # e.g. "D:"
                return {"mode": "drive", "value": "\\\\.\\"+letter}
            console.print("[red]Invalid index.[/red]")
            continue

        # P1/P2 → physical disk
        if raw.upper().startswith("P") and raw[1:].isdigit():
            idx = int(raw[1:]) - 1
            if 0 <= idx < len(physical):
                return {"mode": "drive", "value": physical[idx]["path"]}
            console.print("[red]Invalid physical disk index.[/red]")
            continue

        # Plain letter "D:" or "D"
        letter = raw.upper().rstrip(":\\")
        if len(letter) == 1 and letter.isalpha():
            return {"mode": "drive", "value": f"\\\\.\\{letter}:"}

        if raw.startswith("\\\\.\\"):
            return {"mode": "drive", "value": raw}

        console.print("[red]Not recognised — try again.[/red]")


def _choose_virtual_disk() -> dict:
    console.print("\nEnter the full path to the virtual disk file.")
    console.print("Supported formats: [cyan].vhd  .vhdx  .vmdk[/cyan]")
    while True:
        raw = Prompt.ask("File path").strip().strip('"')
        p = Path(raw)
        if not p.exists():
            console.print(f"[red]File not found: {raw}[/red]")
            continue
        if p.suffix.lower() not in (".vhd", ".vhdx", ".vmdk"):
            console.print(f"[red]Unsupported format {p.suffix!r}. Use .vhd, .vhdx, or .vmdk.[/red]")
            continue
        return {"mode": "virtual", "value": str(p)}


# ---------------------------------------------------------------------------
# Step 4 — Scan depth
# ---------------------------------------------------------------------------

def _choose_depth() -> str:
    _step_header(2, "Choose Scan Depth")

    t = Table(show_lines=True)
    t.add_column("Option",       style="cyan",   width=12)
    t.add_column("What it does", style="white")
    t.add_column("Speed",        style="yellow", justify="right")
    t.add_row("quick",      "NTFS MFT + FAT directory scan — finds recently deleted files",      "Fast")
    t.add_row("deep",       "quick + Volume Shadow Copies + Recycle Bin + browser history",      "Medium")
    t.add_row("full-carve", "deep + raw sector-by-sector scan — finds files even after format",  "Slow")
    console.print(t)

    return Prompt.ask("\nDepth", choices=["quick", "deep", "full-carve"], default="deep")


# ---------------------------------------------------------------------------
# Step 5 — File types
# ---------------------------------------------------------------------------

def _choose_types() -> list[str]:
    _step_header(3, "Choose File Types to Recover")

    from drt.types import all_groups
    groups     = all_groups()
    group_keys = list(groups.keys())

    t = Table(show_lines=True)
    t.add_column("#",           style="dim",   width=4)
    t.add_column("Group",       style="cyan",  width=16)
    t.add_column("Description", style="white")
    t.add_column("Example extensions", style="dim")
    for i, (key, data) in enumerate(groups.items(), 1):
        exts = data["extensions"]
        t.add_row(
            str(i),
            key,
            data["description"],
            ", ".join(exts[:8]) + (" …" if len(exts) > 8 else ""),
        )
    t.add_row("*", "all", "Everything above", "—")
    console.print(t)

    console.print("\nEnter comma-separated numbers (e.g. [bold]1,3[/bold]) or press Enter for [bold]all[/bold].")

    while True:
        raw = Prompt.ask("Type groups", default="all").strip()
        if not raw or raw.lower() == "all":
            return ["all"]

        parts = [p.strip() for p in raw.split(",")]
        selected: list[str] = []
        ok = True
        for p in parts:
            if p.isdigit():
                idx = int(p) - 1
                if 0 <= idx < len(group_keys):
                    selected.append(group_keys[idx])
                else:
                    console.print(f"[red]Invalid number: {p}[/red]")
                    ok = False
                    break
            elif p.lower() in group_keys or p.lower() == "all":
                selected.append(p.lower())
            else:
                console.print(f"[red]Unknown group: {p!r}[/red]")
                ok = False
                break
        if ok and selected:
            return selected


# ---------------------------------------------------------------------------
# Step 6 — Output directory
# ---------------------------------------------------------------------------

def _choose_output() -> str:
    _step_header(4, "Output Directory")

    desktop = Path.home() / "Desktop" / "recovered_files"
    default = str(desktop)

    console.print(f"\nRecovered files will be written here.")
    console.print(f"Default: [cyan]{default}[/cyan]")

    raw = Prompt.ask("Output directory", default=default).strip().strip('"')
    return raw or default


# ---------------------------------------------------------------------------
# Step 7 — Filters (with smart defaults per type group)
# ---------------------------------------------------------------------------

# Per-group smart defaults: (min_size_label, max_size_label) or None for no default
_TYPE_DEFAULTS: dict[str, tuple[str, str]] = {
    "images": ("50KB",  "50MB"),   # skip tiny thumbnails/icons; cap at 50 MB
    "videos": ("5MB",   "4GB"),    # skip short clips/thumbnails; cap at 4 GB
    "audio":  ("500KB", "500MB"),  # skip short beeps
}


def _smart_defaults(types: list[str]) -> tuple[str, str]:
    """
    Return (min_size, max_size) string defaults for the given type groups.
    Picks the broadest range that covers all selected groups.
    Returns ("", "") when no smart default applies.
    """
    if "all" in types:
        return "", ""

    mins, maxs = [], []
    for t in types:
        if t in _TYPE_DEFAULTS:
            mn, mx = _TYPE_DEFAULTS[t]
            mins.append(mn)
            maxs.append(mx)

    if not mins:
        return "", ""

    # Use smallest min (most permissive) and largest max
    def _to_bytes(s: str) -> int:
        from drt.filters import parse_size
        return parse_size(s)

    min_val = min(mins, key=_to_bytes)
    max_val = max(maxs, key=_to_bytes)
    return min_val, max_val


def _choose_filters(types: list[str]) -> dict:
    _step_header(5, "File Size Filters")

    default_min, default_max = _smart_defaults(types)

    if default_min or default_max:
        console.print(
            f"\n[dim]Smart defaults applied based on selected types "
            f"(min=[cyan]{default_min or 'none'}[/cyan], "
            f"max=[cyan]{default_max or 'none'}[/cyan]). "
            f"Press Enter to accept or type a new value.[/dim]"
        )
    else:
        console.print("\n[dim]Leave blank to recover files of any size.[/dim]")

    min_size = Prompt.ask("Minimum file size (e.g. 10KB, 1MB)", default=default_min).strip() or None
    max_size = Prompt.ask("Maximum file size (e.g. 500MB, 2GB)", default=default_max).strip() or None
    after    = Prompt.ask("Only files modified after  (YYYY-MM-DD, or blank)", default="").strip() or None
    before   = Prompt.ask("Only files modified before (YYYY-MM-DD, or blank)", default="").strip() or None

    return {"min_size": min_size, "max_size": max_size, "after": after, "before": before}


# ---------------------------------------------------------------------------
# Step 8 — Confirmation summary
# ---------------------------------------------------------------------------

def _confirm(target: dict, depth: str, types: list[str], output: str, filters: dict) -> bool:
    _step_header(6, "Confirm and Start")

    t = Table(show_lines=True, title="Recovery Settings")
    t.add_column("Setting", style="cyan")
    t.add_column("Value",   style="white")
    mode  = target["mode"]
    value = target["value"]
    t.add_row("Target",      f"{'Drive' if mode == 'drive' else 'Virtual disk'}: {value}")
    t.add_row("Scan depth",  depth)
    t.add_row("File types",  ", ".join(types))
    t.add_row("Output",      output)
    active = {k: v for k, v in filters.items() if v}
    if active:
        t.add_row("Filters", "  ".join(f"{k}={v}" for k, v in active.items()))
    console.print(t)
    console.print()
    return Confirm.ask("[bold]Start recovery now?[/bold]", default=True)


# ---------------------------------------------------------------------------
# Step 9 — Run
# ---------------------------------------------------------------------------

def _build_filter(filters: dict):
    """Build a size+date filter function. Returns None if no filters active."""
    from drt.filters import make_filter, parse_date, parse_size
    min_sz  = parse_size(filters["min_size"]) if filters.get("min_size") else None
    max_sz  = parse_size(filters["max_size"]) if filters.get("max_size") else None
    after_  = parse_date(filters["after"])    if filters.get("after")    else None
    before_ = parse_date(filters["before"])   if filters.get("before")   else None
    if not any([min_sz, max_sz, after_, before_]):
        return None
    return make_filter(min_size=min_sz, max_size=max_sz, after_ts=after_, before_ts=before_)


def _find_checkpoint(base_output: str) -> tuple[str, dict] | None:
    """
    Scan <base_output>/recovery_*/ subdirectories for a saved checkpoint.
    Returns (run_dir, checkpoint_dict) for the most recent one, or None.
    """
    from drt import checkpoint as cp_mod
    base = Path(base_output)
    if not base.exists():
        return None
    for subdir in sorted(base.glob("recovery_*"), reverse=True):
        if not subdir.is_dir():
            continue
        cp = cp_mod.load(str(subdir))
        if cp:
            return str(subdir), cp
    return None


def _run(target: dict, depth: str, types: list[str], output: str, filters: dict) -> None:
    import json
    import shutil
    import time
    from datetime import datetime

    from rich.live import Live

    console.print()
    console.print(Rule("[bold green]Recovery in progress[/bold green]", style="green"))

    Path(output).mkdir(parents=True, exist_ok=True)

    # ---- Virtual disk: delegate to CLI (also gets a dated run folder) ----
    if target["mode"] == "virtual":
        run_dir = str(Path(output) / f"recovery_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}")
        Path(run_dir).mkdir(parents=True, exist_ok=True)
        console.print(f"Output folder: [cyan]{run_dir}[/cyan]\n")
        cmd = [
            sys.executable, "-m", "drt.main", "virtual-disk",
            "--file", target["value"],
            "--out",  run_dir,
            "--depth", depth,
            "--types", ",".join(types),
        ]
        console.print(f"[dim]Running: {' '.join(cmd)}[/dim]\n")
        subprocess.run(cmd)
        return

    # ---- Physical drive ----
    from drt import artifacts, browser, carver, fat, mft, report, vss, writer
    from drt import reader as disk_reader
    from drt.checkpoint import CheckpointWriter
    from drt import checkpoint as cp_mod
    from drt.progress import ProgressTracker, make_dashboard
    from drt.types import get_extensions_for_groups

    drive = target["value"]

    # ---- Resolve run directory (resume existing or create new dated folder) ----
    resume_carve_offset = 0
    phases_done: list[str] = []
    files_found     = 0
    bytes_recovered = 0
    index           = 0

    found = _find_checkpoint(output)
    if found:
        found_run_dir, existing_cp = found
        cp_ts = existing_cp.get("last_checkpoint", "unknown")
        console.print()
        console.print(Panel(
            f"[bold yellow]Checkpoint found[/bold yellow]\n\n"
            f"  Folder:       [cyan]{found_run_dir}[/cyan]\n"
            f"  Drive:        [cyan]{existing_cp.get('drive', '?')}[/cyan]\n"
            f"  Depth:        [cyan]{existing_cp.get('depth', '?')}[/cyan]\n"
            f"  Types:        [cyan]{', '.join(existing_cp.get('type_groups', []))}[/cyan]\n"
            f"  Saved at:     [dim]{cp_ts}[/dim]\n"
            f"  Phases done:  [cyan]{', '.join(existing_cp.get('phases_completed', [])) or 'none'}[/cyan]\n"
            f"  Files found:  [cyan]{existing_cp.get('files_found', 0)}[/cyan]\n"
            f"  Carve offset: [cyan]{_fmt_bytes(existing_cp.get('carve_offset', 0))}[/cyan]",
            title="Resume previous scan?",
            border_style="yellow",
        ))
        do_resume = Confirm.ask("\nResume from this checkpoint?", default=True)
        if do_resume:
            run_dir             = found_run_dir
            phases_done         = existing_cp.get("phases_completed", [])
            files_found         = existing_cp.get("files_found", 0)
            index               = existing_cp.get("next_index", 0)
            resume_carve_offset = existing_cp.get("carve_offset", 0)
            depth               = existing_cp.get("depth",       depth)
            types               = existing_cp.get("type_groups", types)
            drive               = existing_cp.get("drive",       drive)
            console.print(f"\n[green]Resuming into: {run_dir}[/green]")
            console.print(f"[green]Skipping phases: {phases_done or 'none'}[/green]")
            console.print(f"[green]Carve continues from {_fmt_bytes(resume_carve_offset)}[/green]\n")
        else:
            cp_mod.delete(found_run_dir)
            run_dir = str(Path(output) / f"recovery_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}")
            Path(run_dir).mkdir(parents=True, exist_ok=True)
            console.print(f"[dim]Starting fresh in: {run_dir}[/dim]\n")
    else:
        run_dir = str(Path(output) / f"recovery_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}")
        Path(run_dir).mkdir(parents=True, exist_ok=True)
        console.print(f"Output folder: [cyan]{run_dir}[/cyan]\n")

    # ---- Build filter ----
    try:
        file_filter = _build_filter(filters)
    except ValueError as exc:
        console.print(f"[yellow]Filter warning: {exc} — no filter applied.[/yellow]")
        file_filter = None

    # ---- Open disk ----
    try:
        handle = disk_reader.open_disk(drive)
    except OSError as exc:
        console.print(f"\n[bold red]Cannot open drive:[/bold red] {exc}")
        console.print(
            "\nThis usually means:\n"
            "  • Not running as Administrator\n"
            "  • The drive letter is wrong\n"
            "  • The drive is disconnected\n"
        )
        input("Press Enter to exit...")
        sys.exit(1)

    total_bytes = disk_reader.get_disk_size(handle)
    if total_bytes == 0:
        console.print("[red]Could not determine disk size. Is the drive accessible?[/red]")
        disk_reader.close_disk(handle)
        sys.exit(1)

    console.print(f"Drive size: [cyan]{_fmt_bytes(total_bytes)}[/cyan]\n")

    wanted_extensions = get_extensions_for_groups(types)
    patterns          = carver.build_search_patterns(types)
    console.print(f"Loaded [cyan]{len(patterns)}[/cyan] file signature patterns.\n")

    writer.ensure_structure(run_dir)
    scan_report = report.new_report(drive, depth, types)
    start_time  = time.monotonic()

    drive_letter = _drive_letter_from_path(drive)

    # ---- Extension → group lookup ----
    _ext_group: dict[str, str] = {}
    def _group_for_ext(ext: str) -> str:
        if not _ext_group:
            from drt.types import all_groups as _ag
            for gk, gv in _ag().items():
                for e in gv["extensions"]:
                    _ext_group.setdefault(e, gk)
        return _ext_group.get(ext.lower(), "other")

    tracker = ProgressTracker(total_bytes=total_bytes, drive=drive, current_phase="Initializing")
    tracker.bytes_scanned   = resume_carve_offset
    tracker.carve_offset    = resume_carve_offset
    tracker.record_sample()

    def _build_cp() -> dict:
        return {
            "tool": "DataRecoveryTool", "version": "0.1.0",
            "drive": drive, "depth": depth, "type_groups": types,
            "output_dir": run_dir, "phases_completed": list(phases_done),
            "carve_offset": tracker.carve_offset,
            "files_found": files_found, "next_index": index + 1,
        }

    # Write an immediate checkpoint so resume works even from the start
    cp_mod.save(run_dir, _build_cp())

    cw = CheckpointWriter(run_dir, _build_cp, interval_seconds=30)
    cw.start()

    def _record(ext: str, size: int, name: str, location: str) -> None:
        nonlocal files_found, bytes_recovered, index
        files_found += 1
        bytes_recovered += size
        index += 1
        grp = _group_for_ext(ext)
        tracker.files_by_group[grp] = tracker.files_by_group.get(grp, 0) + 1
        tracker.recent_finds.append({
            "extension": ext, "name": name,
            "size_bytes": size, "offset_or_path": location,
        })

    try:
        with Live(make_dashboard(tracker), refresh_per_second=4, console=console) as live:

            def _refresh() -> None:
                live.update(make_dashboard(tracker))

            # ---- Phase 1: MFT ----
            if "mft" not in phases_done:
                tracker.current_phase = "Phase 1/6: MFT Scan"
                _refresh()
                try:
                    for entry in mft.scan(handle, wanted_extensions):
                        ext  = entry.get("extension", "")
                        size = entry.get("size_bytes", 0)
                        if file_filter and not file_filter(size, None):
                            continue
                        content = (
                            entry.get("resident_data", b"")
                            or mft.extract_file_content(
                                handle,
                                entry.get("data_runs", []),
                                entry.get("bytes_per_cluster", 0),
                                size,
                            )
                        )
                        name = entry.get("name", f"mft_{index+1}")
                        _record(ext, size, name, f"MFT {entry.get('mft_record','?')}")
                        out_path_str = str(writer.write_file(run_dir, ext, content, index)) if content else "(metadata only)"
                        report.add_found_file(scan_report, ext, 0, out_path_str, size)
                        _refresh()
                except Exception:
                    pass
                phases_done.append("mft")
                cp_mod.save(run_dir, _build_cp())

            # ---- Phase 2: FAT ----
            if "fat" not in phases_done:
                tracker.current_phase = "Phase 2/6: FAT Scan"
                _refresh()
                try:
                    fat_bpb   = fat.read_bpb(handle)
                    fat_table = fat.read_fat_table(handle, fat_bpb) if fat_bpb else []
                    entries   = (
                        list(fat.iter_deleted_entries(handle, fat_bpb, wanted_extensions))
                        if fat_bpb else fat.scan(handle, wanted_extensions)
                    )
                    for entry in entries:
                        ext  = entry.get("extension", "")
                        size = entry.get("size_bytes", 0)
                        if file_filter and not file_filter(size, None):
                            continue
                        content = b""
                        if fat_bpb and fat_table:
                            fc = entry.get("first_cluster", 0)
                            if fc >= 2 and size > 0:
                                clusters = fat.follow_cluster_chain(fat_table, fc)
                                if clusters:
                                    content = fat.read_cluster_chain(handle, fat_bpb, clusters, size)
                        name = entry.get("name", f"fat_{index+1}")
                        _record(ext, size, name, f"cluster {entry.get('first_cluster','?')}")
                        out_path_str = str(writer.write_file(run_dir, ext, content, index)) if content else "(metadata only)"
                        report.add_found_file(scan_report, ext, 0, out_path_str, size)
                        _refresh()
                except Exception:
                    pass
                phases_done.append("fat")
                cp_mod.save(run_dir, _build_cp())

            if depth in ("deep", "full-carve"):

                # ---- Phase 3: VSS ----
                if "vss" not in phases_done:
                    tracker.current_phase = "Phase 3/6: Volume Shadow Copies"
                    _refresh()
                    try:
                        for entry in vss.scan(wanted_extensions):
                            ext  = entry.get("extension", "")
                            size = entry.get("size_bytes", 0)
                            src  = entry.get("path", "")
                            if file_filter and not file_filter(size, None):
                                continue
                            name = entry.get("name", f"vss_{index+1}")
                            _record(ext, size, name, entry.get("shadow_id", ""))
                            out_path_str = "(copy failed)"
                            if src and os.path.isfile(src):
                                try:
                                    dest = writer.get_output_path(run_dir, ext, index)
                                    dest.parent.mkdir(parents=True, exist_ok=True)
                                    shutil.copy2(src, str(dest))
                                    out_path_str = str(dest)
                                except Exception:
                                    pass
                            report.add_found_file(scan_report, ext, 0, out_path_str, size)
                            _refresh()
                    except Exception:
                        pass
                    phases_done.append("vss")
                    cp_mod.save(run_dir, _build_cp())

                # ---- Phase 4: Artifacts ----
                if "artifacts" not in phases_done:
                    tracker.current_phase = "Phase 4/6: Windows Artifacts"
                    _refresh()
                    try:
                        for entry in artifacts.scan(drive_letter):
                            ext  = entry.get("extension", "")
                            size = entry.get("size_bytes", 0)
                            if ext not in wanted_extensions and ext != "":
                                continue
                            if file_filter and not file_filter(size, None):
                                continue
                            source = entry.get("source", "")
                            if source == "recycle_bin":
                                name     = Path(entry.get("original_path", f"recycled_{index+1}")).name
                                src_file = entry.get("r_file_path", "")
                            elif source == "lnk":
                                name     = Path(entry.get("lnk_path", f"lnk_{index+1}")).name
                                src_file = entry.get("lnk_path", "")
                            else:
                                name     = f"artifact_{index+1}"
                                src_file = entry.get("pf_path", "")
                            _record(ext or ".bin", size, name, source)
                            out_path_str = "(metadata only)"
                            if src_file and os.path.isfile(src_file):
                                try:
                                    dest = writer.get_output_path(run_dir, ext or ".bin", index)
                                    dest.parent.mkdir(parents=True, exist_ok=True)
                                    shutil.copy2(src_file, str(dest))
                                    out_path_str = str(dest)
                                except Exception:
                                    pass
                            report.add_found_file(scan_report, ext or ".bin", 0, out_path_str, size)
                            _refresh()
                    except Exception:
                        pass
                    phases_done.append("artifacts")
                    cp_mod.save(run_dir, _build_cp())

                # ---- Phase 5: Browser ----
                if "browser" not in phases_done:
                    tracker.current_phase = "Phase 5/6: Browser History"
                    _refresh()
                    try:
                        browser_data = browser.scan()
                        if browser_data:
                            browser_base = Path(run_dir) / "BrowserData"
                            browser_base.mkdir(parents=True, exist_ok=True)
                            for bname, history in browser_data.items():
                                if not history:
                                    continue
                                bdir = browser_base / bname
                                bdir.mkdir(exist_ok=True)
                                (bdir / "history.json").write_text(
                                    json.dumps(history, indent=2, default=str),
                                    encoding="utf-8",
                                )
                                n = len(history)
                                tracker.files_by_group["browser"] = tracker.files_by_group.get("browser", 0) + n
                                files_found += n
                                tracker.recent_finds.append({
                                    "extension": ".json",
                                    "name": f"{bname}/history.json",
                                    "size_bytes": 0,
                                    "offset_or_path": f"{n} URLs",
                                })
                                _refresh()
                    except Exception:
                        pass
                    phases_done.append("browser")
                    cp_mod.save(run_dir, _build_cp())

            # ---- Phase 6: Carve ----
            tracker.current_phase = "Phase 6/6: Deep Carve"
            tracker.record_sample()
            _refresh()

            def _on_progress(processed: int, total: int) -> None:
                tracker.bytes_scanned = resume_carve_offset + processed
                tracker.carve_offset  = resume_carve_offset + processed
                tracker.record_sample()

            for hit in carver.carve_disk(
                handle, total_bytes, patterns, _on_progress,
                start_offset=resume_carve_offset,
            ):
                ext      = hit["extension"]
                max_read = min(hit["max_size"], total_bytes - hit["disk_offset"])

                # Read the full file from disk (not just the 1 MB scan chunk)
                raw_data = disk_reader.read_sectors(handle, hit["disk_offset"], max_read)
                if not raw_data:
                    continue

                # Trim to natural EOF where possible (avoids garbage at end)
                raw_data = _trim_to_eof(raw_data, ext)
                size = len(raw_data)

                if file_filter and not file_filter(size, None):
                    continue

                name = f"carved_{index+1:05d}{ext}"
                _record(ext, size, name, f"0x{hit['disk_offset']:X}")
                out_path = writer.write_file(run_dir, ext, raw_data, index)
                report.add_found_file(scan_report, ext, hit["disk_offset"], str(out_path), size)
                _refresh()

            tracker.bytes_scanned = total_bytes
            tracker.current_phase = "Complete"
            tracker.record_sample()
            _refresh()

    finally:
        cw.stop()
        # Save a final checkpoint on interrupt so resume works
        cp_mod.save(run_dir, _build_cp())
        disk_reader.close_disk(handle)

    cp_mod.delete(run_dir)
    elapsed = time.monotonic() - start_time
    report.finalize_report(scan_report, elapsed)
    report_path = report.write_report(scan_report, run_dir)

    # ---- Summary ----
    console.print()
    console.print(Rule("[bold green]Recovery Complete[/bold green]", style="green"))
    console.print()

    s = Table(title="Summary", show_lines=True)
    s.add_column("Metric",  style="cyan")
    s.add_column("Value",   style="white", justify="right")
    s.add_row("Files found",     str(files_found))
    s.add_row("Data recovered",  _fmt_bytes(bytes_recovered))
    s.add_row("Duration",        f"{elapsed:.1f}s")
    s.add_row("Run folder",      run_dir)
    s.add_row("Report",          str(report_path))
    console.print(s)

    if scan_report["stats"]["by_type"]:
        bt = Table(title="Files by Type", show_lines=False)
        bt.add_column("Extension", style="cyan")
        bt.add_column("Count",     justify="right")
        for ext_key, count in sorted(
            scan_report["stats"]["by_type"].items(), key=lambda kv: kv[1], reverse=True
        ):
            bt.add_row(f".{ext_key}", str(count))
        console.print(bt)

    console.print(f"\nAll recovered files are in: [bold cyan]{run_dir}[/bold cyan]\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    console.print()
    console.print(Panel(
        "[bold white]DataRecoveryTool[/bold white]  v0.1.0\n"
        "[dim]Recovers deleted, lost, or formatted files from drives and disk images.[/dim]",
        border_style="cyan",
    ))

    _check_admin()

    target  = _choose_target()
    depth   = _choose_depth()
    types   = _choose_types()
    output  = _choose_output()
    filters = _choose_filters(types)

    if not _confirm(target, depth, types, output, filters):
        console.print("\nCancelled.\n")
        sys.exit(0)

    try:
        _run(target, depth, types, output, filters)
    except KeyboardInterrupt:
        console.print("\n\n[yellow]Scan interrupted. Checkpoint saved.[/yellow]")
        console.print(f"Run [bold]python wizard.py[/bold] again with the same output folder to resume.")
    finally:
        input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()
