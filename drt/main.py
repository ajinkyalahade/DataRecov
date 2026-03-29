"""
DataRecoveryTool CLI entry point.

Commands:
    drt list-drives    — table of available drives
    drt list-types     — table of type groups
    drt scan           — full scan (interactive wizard or flags)
    drt preview        — like scan but counts only, no writes
    drt image          — byte-for-byte disk image (.img)
    drt resume         — resume an interrupted scan
    drt virtual-disk   — scan a VHD/VHDX/VMDK virtual disk
"""

import ctypes
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from drt import artifacts, browser, carver, fat, mft, report, vss, writer
from drt.checkpoint import CheckpointWriter, delete as checkpoint_delete, load as checkpoint_load, save as checkpoint_save
from drt.filters import make_filter, parse_date, parse_size
from drt.progress import ProgressTracker, make_dashboard
from drt.types import all_groups, get_extensions_for_groups, list_group_keys

console = Console()
app     = typer.Typer(
    name="drt",
    help="DataRecoveryTool — Windows-native CLI file recovery",
    no_args_is_help=True,
    add_completion=False,
)

_TOOL_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Admin check
# ---------------------------------------------------------------------------

def _is_admin() -> bool:
    """Return True if the current process has administrator privileges."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _warn_if_not_admin() -> None:
    if not _is_admin():
        console.print(
            "[bold yellow]Warning:[/bold yellow] Not running as Administrator. "
            "Raw disk access may fail or return partial results. "
            "Run this tool elevated for best results.",
        )


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _fmt_groups(keys: list[str]) -> str:
    return ", ".join(keys)


def _fmt_speed(bps: float) -> str:
    if bps <= 0:
        return "—"
    return _fmt_bytes(int(bps)) + "/s"


def _fmt_eta(total: int, done: int, bps: float) -> str:
    if bps <= 0:
        return "calculating…"
    remaining = total - done
    if remaining <= 0:
        return "done"
    seconds = remaining / bps
    if seconds < 60:
        return f"~{int(seconds)}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"~{int(minutes)} min"
    hours = minutes / 60
    return f"~{hours:.1f} hr"


# ---------------------------------------------------------------------------
# Drive selection helpers
# ---------------------------------------------------------------------------

def _print_drives_table(drives: list[dict], physical: list[dict]) -> None:
    t = Table(title="Logical Drives", show_lines=True)
    t.add_column("#",           style="dim",    width=4)
    t.add_column("Drive",       style="cyan")
    t.add_column("Label",       style="green")
    t.add_column("Type",        style="yellow")
    t.add_column("Filesystem",  style="blue")
    t.add_column("Total",       justify="right")
    t.add_column("Free",        justify="right")

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
        p.add_column("#",      style="dim", width=4)
        p.add_column("Path",   style="cyan")
        p.add_column("Model",  style="green")
        p.add_column("Size",   justify="right")

        for j, disk in enumerate(physical, 1):
            p.add_row(
                str(j),
                disk["path"],
                disk["model"],
                _fmt_bytes(disk["size_bytes"]) if disk["size_bytes"] else "—",
            )

        console.print(p)


def _select_drive_interactively(drives: list[dict], physical: list[dict]) -> str:
    """Prompt the user to pick a drive. Returns a path string."""
    _print_drives_table(drives, physical)

    all_options: list[str] = [d["letter"] for d in drives] + [d["path"] for d in physical]
    choices_display = ", ".join(
        [f"{i+1}={d['letter']}" for i, d in enumerate(drives)]
        + [f"P{j+1}={d['path']}" for j, d in enumerate(physical)]
    )
    console.print(f"\nOptions: {choices_display}")
    console.print("Enter a drive letter (e.g. C:), a physical disk path, or an index number.")

    while True:
        raw = Prompt.ask("Select drive").strip()
        # Numeric index into logical drives
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(drives):
                return "\\\\.\\"+drives[idx]["letter"]
        # P1, P2… for physical disks
        if raw.upper().startswith("P") and raw[1:].isdigit():
            idx = int(raw[1:]) - 1
            if 0 <= idx < len(physical):
                return physical[idx]["path"]
        # Direct entry
        normalized = raw.upper()
        if len(normalized) == 2 and normalized[1] == ":":
            return "\\\\.\\"+normalized
        if raw.startswith("\\\\.\\"):
            return raw
        console.print("[red]Invalid selection. Try again.[/red]")


# ---------------------------------------------------------------------------
# Type group selection helpers
# ---------------------------------------------------------------------------

def _print_types_table() -> None:
    groups = all_groups()
    t = Table(title="Recovery Type Groups", show_lines=True)
    t.add_column("#",           style="dim",   width=4)
    t.add_column("Group Key",   style="cyan",  width=16)
    t.add_column("Description", style="white")
    t.add_column("Extensions",  style="dim")

    for i, (key, data) in enumerate(groups.items(), 1):
        t.add_row(
            str(i),
            key,
            data["description"],
            ", ".join(data["extensions"][:10])
            + (" …" if len(data["extensions"]) > 10 else ""),
        )

    t.add_row("*", "all", "Everything above (union of all groups)", "—")
    console.print(t)


def _select_groups_interactively() -> list[str]:
    """Multi-select type groups. Returns list of group keys."""
    _print_types_table()
    groups = all_groups()
    group_keys = list(groups.keys())

    console.print(
        "\nEnter comma-separated numbers to select groups "
        "(e.g. 1,3,5), or press Enter for [bold]all[/bold]."
    )

    while True:
        raw = Prompt.ask("Type groups", default="all").strip()
        if raw == "" or raw.lower() == "all":
            return ["all"]
        parts = [p.strip() for p in raw.split(",")]
        selected: list[str] = []
        valid = True
        for p in parts:
            if p.isdigit():
                idx = int(p) - 1
                if 0 <= idx < len(group_keys):
                    selected.append(group_keys[idx])
                else:
                    console.print(f"[red]Invalid index: {p}[/red]")
                    valid = False
                    break
            elif p.lower() in group_keys or p.lower() == "all":
                selected.append(p.lower())
            else:
                console.print(f"[red]Unknown group: {p!r}. Valid: {list_group_keys()}[/red]")
                valid = False
                break
        if valid and selected:
            return selected


# ---------------------------------------------------------------------------
# Output dir helper
# ---------------------------------------------------------------------------

def _select_output_dir(default: str = "./recovery_output") -> str:
    raw = Prompt.ask("Output directory", default=default).strip()
    return raw or default


# ---------------------------------------------------------------------------
# Depth helper
# ---------------------------------------------------------------------------

def _select_depth() -> str:
    console.print("\nScan depth options:")
    console.print("  [cyan]quick[/cyan]      — MFT/FAT filesystem scan only")
    console.print("  [cyan]deep[/cyan]       — filesystem + VSS + artifacts + browser data")
    console.print("  [cyan]full-carve[/cyan] — deep + raw sector-by-sector signature carving (most thorough)")
    while True:
        raw = Prompt.ask("Depth", choices=["quick", "deep", "full-carve"], default="deep")
        if raw in ("quick", "deep", "full-carve"):
            return raw


# ---------------------------------------------------------------------------
# Drive letter extraction helper
# ---------------------------------------------------------------------------

def _drive_letter_from_path(drive: str) -> str:
    """Extract a drive letter (e.g. 'C:') from a drive path like '\\\\.\\C:'."""
    # "\\\\.\\C:" → "C:"
    stripped = drive.lstrip("\\").lstrip(".")
    if len(stripped) >= 2 and stripped[1] == ":":
        return stripped[:2].upper()
    return "C:"


# ---------------------------------------------------------------------------
# Extension → group helper (used for tracker.files_by_group)
# ---------------------------------------------------------------------------

_EXT_GROUP_CACHE: dict[str, str] = {}

def _group_for_ext(ext: str) -> str:
    """Map a file extension to a display group key for the dashboard."""
    global _EXT_GROUP_CACHE
    if not _EXT_GROUP_CACHE:
        from drt.types import all_groups as _all_groups
        for gk, gv in _all_groups().items():
            for e in gv["extensions"]:
                if e not in _EXT_GROUP_CACHE:
                    _EXT_GROUP_CACHE[e] = gk
    return _EXT_GROUP_CACHE.get(ext.lower(), "other")


# ---------------------------------------------------------------------------
# Core scan logic (shared by scan, preview, resume, virtual-disk)
# ---------------------------------------------------------------------------

def _run_scan(
    drive: str,
    depth: str,
    type_groups: list[str],
    output_dir: str,
    preview_only: bool,
    file_filter=None,           # callable(size_bytes, mtime_ts) -> bool | None
    resume_state: dict | None = None,
) -> None:
    from drt import reader as disk_reader

    console.print(f"\n[bold]Drive:[/bold]  {drive}")
    console.print(f"[bold]Depth:[/bold]  {depth}")
    console.print(f"[bold]Types:[/bold]  {_fmt_groups(type_groups)}")
    console.print(f"[bold]Output:[/bold] {output_dir}")
    if preview_only:
        console.print("[yellow]Preview mode — no files will be written.[/yellow]")
    if resume_state:
        console.print(
            f"[yellow]Resuming scan — skipping phases: "
            f"{resume_state.get('phases_completed', [])}[/yellow]"
        )
    console.print()

    # Determine which phases to skip (resume support)
    phases_completed: list[str] = list(resume_state.get("phases_completed", [])) if resume_state else []
    resume_carve_offset: int = int(resume_state.get("carve_offset", 0)) if resume_state else 0

    # Open disk
    try:
        handle = disk_reader.open_disk(drive)
    except OSError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(1)

    total_bytes = disk_reader.get_disk_size(handle)
    if total_bytes == 0:
        console.print("[red]Could not determine disk size. Aborting.[/red]")
        disk_reader.close_disk(handle)
        raise typer.Exit(1)

    console.print(f"Disk size: [cyan]{_fmt_bytes(total_bytes)}[/cyan]")

    wanted_extensions = get_extensions_for_groups(type_groups)
    patterns = carver.build_search_patterns(type_groups)
    console.print(f"Loaded [cyan]{len(patterns)}[/cyan] signature patterns.\n")

    if not preview_only:
        writer.ensure_structure(output_dir)

    scan_report  = report.new_report(drive, depth, type_groups)
    start_time   = time.monotonic()
    drive_letter = _drive_letter_from_path(drive)

    files_found      = int(resume_state.get("files_found", 0)) if resume_state else 0
    bytes_recovered  = 0
    index            = int(resume_state.get("next_index", 1)) - 1 if resume_state else 0

    tracker = ProgressTracker(
        total_bytes=total_bytes,
        drive=drive,
        current_phase="Initializing",
    )
    tracker.record_sample()

    # Checkpoint state builder — called by CheckpointWriter every interval
    def _build_checkpoint_state() -> dict:
        return {
            "tool":              "DataRecoveryTool",
            "version":           _TOOL_VERSION,
            "drive":             drive,
            "depth":             depth,
            "type_groups":       type_groups,
            "output_dir":        output_dir,
            "scan_date":         scan_report.get("scan_date", ""),
            "phases_completed":  phases_completed,
            "carve_offset":      tracker.carve_offset,
            "files_found":       files_found,
            "next_index":        index + 1,
        }

    cw = CheckpointWriter(output_dir, _build_checkpoint_state, interval_seconds=60)
    cw.start()

    try:
        with Live(make_dashboard(tracker), refresh_per_second=4, console=console) as live:

            # ------------------------------------------------------------------
            # Phase 1 — MFT scan
            # ------------------------------------------------------------------
            if "mft" not in phases_completed:
                tracker.current_phase = "Phase 1: MFT Scan"
                live.update(make_dashboard(tracker))

                mft_entries: list[dict] = []
                try:
                    mft_entries = mft.scan(handle, wanted_extensions)
                except Exception:
                    pass

                for entry in mft_entries:
                    ext = entry.get("extension", "")
                    size = entry.get("size_bytes", 0)
                    mtime_ts: float | None = None

                    if file_filter is not None and not file_filter(size, mtime_ts):
                        continue

                    files_found += 1
                    index += 1
                    bytes_recovered += size

                    group = _group_for_ext(ext)
                    tracker.files_by_group[group] = tracker.files_by_group.get(group, 0) + 1
                    tracker.recent_finds.append({
                        "extension":      ext,
                        "name":           entry.get("name", f"mft_record_{entry.get('mft_record', index)}"),
                        "size_bytes":     size,
                        "offset_or_path": f"MFT record {entry.get('mft_record', '?')}",
                    })

                    out_path_str = "(metadata only)"
                    if not preview_only:
                        # Try to extract actual file content
                        content = b""
                        resident = entry.get("resident_data", b"")
                        if resident:
                            content = resident
                        else:
                            data_runs = entry.get("data_runs", [])
                            bpc = entry.get("bytes_per_cluster", 0)
                            if data_runs and bpc and size:
                                content = mft.extract_file_content(handle, data_runs, bpc, size)

                        if content:
                            out_path = writer.write_file(output_dir, ext, content, index)
                            out_path_str = str(out_path)
                            bytes_recovered = bytes_recovered - size + len(content)
                        else:
                            # Fall back to metadata-only record
                            out_path_str = "(metadata only — content extraction failed)"

                    report.add_found_file(
                        scan_report,
                        ext,
                        0,
                        out_path_str,
                        size,
                    )
                    live.update(make_dashboard(tracker))

                phases_completed.append("mft")

            # ------------------------------------------------------------------
            # Phase 2 — FAT scan
            # ------------------------------------------------------------------
            if "fat" not in phases_completed:
                tracker.current_phase = "Phase 2: FAT Scan"
                live.update(make_dashboard(tracker))

                fat_bpb = None
                fat_table: list[int] = []
                try:
                    fat_bpb = fat.read_bpb(handle)
                    if fat_bpb is not None:
                        fat_table = fat.read_fat_table(handle, fat_bpb)
                except Exception:
                    pass

                fat_entries: list[dict] = []
                try:
                    if fat_bpb is not None:
                        fat_entries = list(fat.iter_deleted_entries(handle, fat_bpb, wanted_extensions))
                    else:
                        fat_entries = fat.scan(handle, wanted_extensions)
                except Exception:
                    pass

                for entry in fat_entries:
                    ext = entry.get("extension", "")
                    size = entry.get("size_bytes", 0)
                    mtime_ts = None

                    if file_filter is not None and not file_filter(size, mtime_ts):
                        continue

                    files_found += 1
                    index += 1
                    bytes_recovered += size

                    group = _group_for_ext(ext)
                    tracker.files_by_group[group] = tracker.files_by_group.get(group, 0) + 1
                    tracker.recent_finds.append({
                        "extension":      ext,
                        "name":           entry.get("name", f"fat_entry_{index}"),
                        "size_bytes":     size,
                        "offset_or_path": f"cluster {entry.get('first_cluster', '?')}",
                    })

                    out_path_str = "(metadata only)"
                    if not preview_only and fat_bpb is not None and fat_table:
                        first_cluster = entry.get("first_cluster", 0)
                        content = b""
                        if first_cluster >= 2 and size > 0:
                            clusters = fat.follow_cluster_chain(fat_table, first_cluster)
                            if clusters:
                                content = fat.read_cluster_chain(handle, fat_bpb, clusters, size)
                        if content:
                            out_path = writer.write_file(output_dir, ext, content, index)
                            out_path_str = str(out_path)
                            bytes_recovered = bytes_recovered - size + len(content)
                        else:
                            out_path_str = "(metadata only — content extraction failed)"

                    report.add_found_file(
                        scan_report,
                        ext,
                        0,
                        out_path_str,
                        size,
                    )
                    live.update(make_dashboard(tracker))

                phases_completed.append("fat")

            # ------------------------------------------------------------------
            # Phase 3 — VSS scan (deep and full-carve only)
            # ------------------------------------------------------------------
            if depth in ("deep", "full-carve") and "vss" not in phases_completed:
                tracker.current_phase = "Phase 3: VSS Shadow Copy Scan"
                live.update(make_dashboard(tracker))

                vss_entries: list[dict] = []
                try:
                    vss_entries = vss.scan(wanted_extensions)
                except Exception:
                    pass

                for entry in vss_entries:
                    ext = entry.get("extension", "")
                    size = entry.get("size_bytes", 0)
                    mtime_ts = None
                    src_path = entry.get("path", "")

                    if file_filter is not None and not file_filter(size, mtime_ts):
                        continue

                    files_found += 1
                    index += 1
                    bytes_recovered += size

                    group = _group_for_ext(ext)
                    tracker.files_by_group[group] = tracker.files_by_group.get(group, 0) + 1
                    tracker.recent_finds.append({
                        "extension":      ext,
                        "name":           entry.get("name", f"vss_{index}"),
                        "size_bytes":     size,
                        "offset_or_path": entry.get("shadow_id", ""),
                    })

                    out_path_str = "(preview — not written)"
                    if not preview_only and src_path and os.path.isfile(src_path):
                        try:
                            dest = writer.get_output_path(output_dir, ext, index)
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src_path, str(dest))
                            out_path_str = str(dest)
                        except Exception:
                            out_path_str = "(copy failed)"

                    report.add_found_file(scan_report, ext, 0, out_path_str, size)
                    live.update(make_dashboard(tracker))

                phases_completed.append("vss")

            # ------------------------------------------------------------------
            # Phase 4 — Artifact scan (deep and full-carve only)
            # ------------------------------------------------------------------
            if depth in ("deep", "full-carve") and "artifacts" not in phases_completed:
                tracker.current_phase = "Phase 4: Artifact Scan"
                live.update(make_dashboard(tracker))

                artifact_entries: list[dict] = []
                try:
                    artifact_entries = artifacts.scan(drive_letter)
                except Exception:
                    pass

                for entry in artifact_entries:
                    source = entry.get("source", "")
                    ext = entry.get("extension", "")
                    size = entry.get("size_bytes", 0)

                    if ext not in wanted_extensions and ext != "":
                        continue

                    if file_filter is not None and not file_filter(size, None):
                        continue

                    files_found += 1
                    index += 1
                    bytes_recovered += size

                    group = _group_for_ext(ext) if ext else "artifacts"
                    tracker.files_by_group[group] = tracker.files_by_group.get(group, 0) + 1

                    # Determine display name
                    if source == "recycle_bin":
                        name = Path(entry.get("original_path", f"recycled_{index}")).name
                        src_file = entry.get("r_file_path", "")
                        location = entry.get("deletion_time", "")
                    elif source == "lnk":
                        name = Path(entry.get("lnk_path", f"lnk_{index}")).name
                        src_file = entry.get("lnk_path", "")
                        location = entry.get("target_path", "")
                    elif source == "prefetch":
                        name = entry.get("exe_name", f"prefetch_{index}")
                        src_file = entry.get("pf_path", "")
                        location = entry.get("pf_path", "")
                    else:
                        name = f"artifact_{index}"
                        src_file = ""
                        location = ""

                    tracker.recent_finds.append({
                        "extension":      ext,
                        "name":           name,
                        "size_bytes":     size,
                        "offset_or_path": location,
                    })

                    out_path_str = "(preview — not written)"
                    if not preview_only and src_file and os.path.isfile(src_file):
                        try:
                            dest = writer.get_output_path(output_dir, ext or ".bin", index)
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src_file, str(dest))
                            out_path_str = str(dest)
                        except Exception:
                            out_path_str = "(copy failed)"

                    report.add_found_file(scan_report, ext or ".bin", 0, out_path_str, size)
                    live.update(make_dashboard(tracker))

                phases_completed.append("artifacts")

            # ------------------------------------------------------------------
            # Phase 5 — Browser data (deep and full-carve only)
            # ------------------------------------------------------------------
            if depth in ("deep", "full-carve") and "browser" not in phases_completed:
                tracker.current_phase = "Phase 5: Browser Data"
                live.update(make_dashboard(tracker))

                browser_data: dict = {}
                try:
                    browser_data = browser.scan()
                except Exception:
                    pass

                if not preview_only and browser_data:
                    browser_base = Path(output_dir) / "BrowserData"
                    browser_base.mkdir(parents=True, exist_ok=True)
                    for browser_name, history in browser_data.items():
                        if not history:
                            continue
                        browser_dir = browser_base / browser_name
                        browser_dir.mkdir(exist_ok=True)
                        hist_file = browser_dir / "history.json"
                        try:
                            hist_file.write_text(
                                json.dumps(history, indent=2, default=str),
                                encoding="utf-8",
                            )
                        except Exception:
                            pass

                for browser_name, history in browser_data.items():
                    if not history:
                        continue
                    tracker.files_by_group["browser"] = (
                        tracker.files_by_group.get("browser", 0) + len(history)
                    )
                    files_found += len(history)
                    tracker.recent_finds.append({
                        "extension":      ".json",
                        "name":           f"{browser_name}/history.json",
                        "size_bytes":     0,
                        "offset_or_path": f"{len(history)} URLs",
                    })
                    live.update(make_dashboard(tracker))

                phases_completed.append("browser")

            # ------------------------------------------------------------------
            # Phase 6 — Deep carve (always runs as final pass)
            # ------------------------------------------------------------------
            tracker.current_phase = "Phase 6: Deep Carve"
            tracker.bytes_scanned = resume_carve_offset
            tracker.carve_offset  = resume_carve_offset
            tracker.record_sample()
            live.update(make_dashboard(tracker))

            def _on_progress(processed: int, total: int) -> None:
                tracker.bytes_scanned = processed
                tracker.carve_offset  = processed
                tracker.record_sample()

            for hit in carver.carve_disk(handle, total_bytes, patterns, _on_progress):
                # Skip hits before resume offset
                if hit["disk_offset"] < resume_carve_offset:
                    continue

                start_in_chunk = hit["disk_offset"] - hit["chunk_offset"]
                end_in_chunk   = start_in_chunk + hit["estimated_size"]
                raw_data       = hit["chunk_data"][start_in_chunk:end_in_chunk]

                if not raw_data:
                    continue

                ext = hit["extension"]
                size = len(raw_data)

                if file_filter is not None and not file_filter(size, None):
                    continue

                files_found     += 1
                bytes_recovered += size
                index           += 1

                group = _group_for_ext(ext)
                tracker.files_by_group[group] = tracker.files_by_group.get(group, 0) + 1
                tracker.recent_finds.append({
                    "extension":      ext,
                    "name":           f"recovered_{index:04d}{ext}",
                    "size_bytes":     size,
                    "offset_or_path": f"offset 0x{hit['disk_offset']:X}",
                })

                if not preview_only:
                    out_path = writer.write_file(output_dir, ext, raw_data, index)
                    report.add_found_file(
                        scan_report,
                        ext,
                        hit["disk_offset"],
                        str(out_path),
                        size,
                    )
                else:
                    report.add_found_file(
                        scan_report,
                        ext,
                        hit["disk_offset"],
                        "(preview — not written)",
                        size,
                    )

                live.update(make_dashboard(tracker))

            # Mark scan complete
            tracker.bytes_scanned = total_bytes
            tracker.current_phase = "Complete"
            tracker.record_sample()
            live.update(make_dashboard(tracker))

    finally:
        cw.stop()
        disk_reader.close_disk(handle)

    # Scan finished successfully — remove checkpoint
    checkpoint_delete(output_dir)

    elapsed = time.monotonic() - start_time
    report.finalize_report(scan_report, elapsed)

    if not preview_only:
        report_path = report.write_report(scan_report, output_dir)
        console.print(f"\nReport written: [cyan]{report_path}[/cyan]")

    # Summary table
    t = Table(title="Scan Summary", show_lines=True)
    t.add_column("Metric",  style="cyan")
    t.add_column("Value",   style="white", justify="right")
    t.add_row("Drive",             drive)
    t.add_row("Depth",             depth)
    t.add_row("Files found",       str(files_found))
    t.add_row("Bytes recovered",   _fmt_bytes(bytes_recovered))
    t.add_row("Duration",          f"{elapsed:.1f}s")
    console.print(t)

    if files_found > 0 and scan_report["stats"]["by_type"]:
        bt = Table(title="Files by Type", show_lines=False)
        bt.add_column("Extension", style="cyan")
        bt.add_column("Count",     justify="right")
        for ext_key, count in sorted(
            scan_report["stats"]["by_type"].items(),
            key=lambda kv: kv[1],
            reverse=True,
        ):
            bt.add_row(f".{ext_key}", str(count))
        console.print(bt)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.command("list-drives")
def cmd_list_drives() -> None:
    """Show all available drives with metadata."""
    from drt.drives import get_physical_disks, list_drives
    _warn_if_not_admin()
    drives   = list_drives()
    physical = get_physical_disks()
    _print_drives_table(drives, physical)


@app.command("list-types")
def cmd_list_types() -> None:
    """Show all recoverable type groups and the extensions they cover."""
    _print_types_table()


@app.command("scan")
def cmd_scan(
    drive:    Optional[str] = typer.Option(None, "--drive",    help="Drive letter or physical disk path"),
    depth:    Optional[str] = typer.Option(None, "--depth",    help="quick | deep | full-carve"),
    types:    Optional[str] = typer.Option(None, "--types",    help="Comma-separated group keys (default: all)"),
    out:      Optional[str] = typer.Option(None, "--out",      help="Output directory"),
    min_size: Optional[str] = typer.Option(None, "--min-size", help="Minimum file size, e.g. 1KB, 10MB"),
    max_size: Optional[str] = typer.Option(None, "--max-size", help="Maximum file size, e.g. 100MB, 2GB"),
    after:    Optional[str] = typer.Option(None, "--after",    help="Only files modified after YYYY-MM-DD"),
    before:   Optional[str] = typer.Option(None, "--before",   help="Only files modified before YYYY-MM-DD"),
) -> None:
    """
    Recover files from a drive. Runs interactively if flags are omitted.
    """
    from drt.drives import get_physical_disks, list_drives
    _warn_if_not_admin()

    # Resolve drive
    if drive is None:
        drives_list   = list_drives()
        physical_list = get_physical_disks()
        drive = _select_drive_interactively(drives_list, physical_list)

    # Normalise drive path
    if len(drive) == 2 and drive[1] == ":":
        drive = "\\\\.\\"+drive.upper()

    # Resolve depth
    if depth is None:
        depth = _select_depth()
    if depth not in ("quick", "deep", "full-carve"):
        console.print(f"[red]Invalid depth: {depth!r}. Choose quick, deep, or full-carve.[/red]")
        raise typer.Exit(1)

    # Resolve type groups
    if types is None:
        type_groups = _select_groups_interactively()
    else:
        type_groups = [t.strip() for t in types.split(",") if t.strip()]

    # Validate type groups
    valid_keys = set(list_group_keys())
    for key in type_groups:
        if key not in valid_keys:
            console.print(f"[red]Unknown type group: {key!r}. Valid: {sorted(valid_keys)}[/red]")
            raise typer.Exit(1)

    # Resolve output dir
    if out is None:
        out = _select_output_dir()

    # Build filter
    file_filter = _build_file_filter(min_size, max_size, after, before)

    _run_scan(drive, depth, type_groups, out, preview_only=False, file_filter=file_filter)


@app.command("preview")
def cmd_preview(
    drive:    Optional[str] = typer.Option(None, "--drive",    help="Drive letter or physical disk path"),
    depth:    Optional[str] = typer.Option(None, "--depth",    help="quick | deep | full-carve"),
    types:    Optional[str] = typer.Option(None, "--types",    help="Comma-separated group keys"),
    out:      Optional[str] = typer.Option(None, "--out",      help="Output directory (unused in preview)"),
    min_size: Optional[str] = typer.Option(None, "--min-size", help="Minimum file size, e.g. 1KB, 10MB"),
    max_size: Optional[str] = typer.Option(None, "--max-size", help="Maximum file size, e.g. 100MB, 2GB"),
    after:    Optional[str] = typer.Option(None, "--after",    help="Only files modified after YYYY-MM-DD"),
    before:   Optional[str] = typer.Option(None, "--before",   help="Only files modified before YYYY-MM-DD"),
) -> None:
    """
    Preview what would be recovered without writing any files.
    """
    from drt.drives import get_physical_disks, list_drives
    _warn_if_not_admin()

    if drive is None:
        drives_list   = list_drives()
        physical_list = get_physical_disks()
        drive = _select_drive_interactively(drives_list, physical_list)

    if len(drive) == 2 and drive[1] == ":":
        drive = "\\\\.\\"+drive.upper()

    if depth is None:
        depth = _select_depth()
    if depth not in ("quick", "deep", "full-carve"):
        console.print(f"[red]Invalid depth: {depth!r}.[/red]")
        raise typer.Exit(1)

    if types is None:
        type_groups = _select_groups_interactively()
    else:
        type_groups = [t.strip() for t in types.split(",") if t.strip()]

    valid_keys = set(list_group_keys())
    for key in type_groups:
        if key not in valid_keys:
            console.print(f"[red]Unknown type group: {key!r}.[/red]")
            raise typer.Exit(1)

    file_filter = _build_file_filter(min_size, max_size, after, before)

    _run_scan(
        drive, depth, type_groups,
        out or "./preview_output",
        preview_only=True,
        file_filter=file_filter,
    )


@app.command("image")
def cmd_image(
    drive:  str           = typer.Option(..., "--drive",  help="Drive letter or physical disk path"),
    out:    str           = typer.Option(..., "--out",    help="Output path for the .img file"),
    verify: bool          = typer.Option(False, "--verify", help="Verify image integrity after writing"),
) -> None:
    """
    Create a byte-for-byte raw image of a disk drive.
    Unreadable sectors are replaced with zeroes and logged to a sidecar file.
    """
    from drt import imager
    from drt import reader as disk_reader

    _warn_if_not_admin()

    # Normalise drive path
    if len(drive) == 2 and drive[1] == ":":
        drive = "\\\\.\\"+drive.upper()

    try:
        handle = disk_reader.open_disk(drive)
    except OSError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(1)

    total_bytes = disk_reader.get_disk_size(handle)
    if total_bytes == 0:
        console.print("[red]Could not determine disk size. Aborting.[/red]")
        disk_reader.close_disk(handle)
        raise typer.Exit(1)

    console.print(f"\n[bold]Drive:[/bold]  {drive}")
    console.print(f"[bold]Output:[/bold] {out}")
    console.print(f"[bold]Size:[/bold]   {_fmt_bytes(total_bytes)}\n")

    # Ensure output directory exists
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    bad_sectors: list[dict] = []
    bytes_written_ref = [0]
    start_time = time.monotonic()

    def _progress(bw: int, total: int) -> None:
        bytes_written_ref[0] = bw

    def _bad_sector(offset: int, length: int) -> None:
        bad_sectors.append({"offset": offset, "length": length})

    # Live imaging dashboard (simple 2-panel)
    def _make_image_dashboard(bw: int) -> Layout:
        elapsed = time.monotonic() - start_time
        speed = bw / elapsed if elapsed > 0 else 0.0
        eta = _fmt_eta(total_bytes, bw, speed)
        pct = bw / total_bytes * 100 if total_bytes > 0 else 0.0

        bar_width = 30
        filled = round(pct / 100 * bar_width)
        bar = "█" * filled + "░" * (bar_width - filled)

        left_lines = [
            f"Drive:    [dim]{drive}[/dim]",
            f"Written:  [green]{_fmt_bytes(bw)}[/green] / {_fmt_bytes(total_bytes)}  ([yellow]{pct:.1f}%[/yellow])",
            f"Speed:    [cyan]{_fmt_speed(speed)}[/cyan]",
            f"ETA:      [yellow]{eta}[/yellow]",
            "",
            f"[bold blue]{bar}[/bold blue] [yellow]{pct:.1f}%[/yellow]",
        ]
        right_lines = [
            f"Output:         [dim]{out}[/dim]",
            f"Bytes written:  [green]{_fmt_bytes(bw)}[/green]",
            f"Bad sectors:    [{'red' if bad_sectors else 'green'}]{len(bad_sectors)}[/{'red' if bad_sectors else 'green'}]",
            f"Bad bytes:      [dim]{_fmt_bytes(sum(s['length'] for s in bad_sectors))}[/dim]",
        ]

        layout = Layout()
        layout.split_row(
            Layout(name="left"),
            Layout(name="right"),
        )
        layout["left"].update(
            Panel(Text.from_markup("\n".join(left_lines)), title="[bold]Imaging Progress[/bold]", border_style="blue")
        )
        layout["right"].update(
            Panel(Text.from_markup("\n".join(right_lines)), title="[bold]Image Stats[/bold]", border_style="green")
        )
        return layout

    with Live(_make_image_dashboard(0), refresh_per_second=4, console=console) as live:
        # Wrap progress to also refresh dashboard
        def _progress_live(bw: int, total: int) -> None:
            bytes_written_ref[0] = bw
            live.update(_make_image_dashboard(bw))

        result = imager.image_disk(
            handle=handle,
            total_bytes=total_bytes,
            out_path=str(out_path),
            progress_callback=_progress_live,
            bad_sector_callback=_bad_sector,
        )
        live.update(_make_image_dashboard(result["bytes_written"]))

    console.print(f"\n[bold green]Imaging complete.[/bold green]")
    console.print(f"Bytes written:  {_fmt_bytes(result['bytes_written'])}")
    console.print(f"Duration:       {result['duration_seconds']:.1f}s")
    console.print(f"Bad sectors:    {result['bad_sector_count']} ({_fmt_bytes(result['bad_sector_bytes'])})")

    # Write bad sector sidecar
    if bad_sectors:
        sidecar = str(out_path) + ".bad_sectors.json"
        try:
            Path(sidecar).write_text(
                json.dumps(bad_sectors, indent=2),
                encoding="utf-8",
            )
            console.print(f"Bad sector log: [yellow]{sidecar}[/yellow]")
        except Exception as exc:
            console.print(f"[yellow]Could not write bad sector log: {exc}[/yellow]")

    # Verify
    if verify:
        console.print("\n[bold]Verifying image integrity…[/bold]")
        mismatches = imager.verify_image(str(out_path), handle, total_bytes)
        if mismatches:
            console.print(f"[bold red]{len(mismatches)} block(s) failed verification:[/bold red]")
            for m in mismatches:
                console.print(
                    f"  offset 0x{m['offset']:X}  len {_fmt_bytes(m['length'])}  "
                    f"disk={m['disk_sha256'][:16]}…  img={m['img_sha256'][:16]}…"
                )
        else:
            console.print("[bold green]Verification passed — image matches disk.[/bold green]")

    disk_reader.close_disk(handle)


@app.command("resume")
def cmd_resume(
    out: str = typer.Option(..., "--out", help="Output directory from an interrupted scan"),
) -> None:
    """
    Resume an interrupted scan from the last checkpoint.
    """
    _warn_if_not_admin()

    cp = checkpoint_load(out)
    if cp is None:
        console.print(
            f"[bold red]No checkpoint found[/bold red] in [cyan]{out}[/cyan].\n"
            "Start a new scan with [bold]drt scan[/bold]."
        )
        raise typer.Exit(1)

    # Display checkpoint summary
    t = Table(title="Checkpoint Found", show_lines=True)
    t.add_column("Field",  style="cyan")
    t.add_column("Value",  style="white")
    t.add_row("Drive",            cp.get("drive", "—"))
    t.add_row("Depth",            cp.get("depth", "—"))
    t.add_row("Types",            ", ".join(cp.get("type_groups", [])))
    t.add_row("Last checkpoint",  cp.get("last_checkpoint", "—"))
    t.add_row("Phases done",      ", ".join(cp.get("phases_completed", [])) or "(none)")
    t.add_row("Carve offset",     _fmt_bytes(cp.get("carve_offset", 0)))
    t.add_row("Files found",      str(cp.get("files_found", 0)))
    console.print(t)

    if not Confirm.ask("Resume this scan?"):
        console.print("Aborted.")
        raise typer.Exit(0)

    drive       = cp.get("drive", "")
    depth       = cp.get("depth", "deep")
    type_groups = cp.get("type_groups", ["all"])
    output_dir  = cp.get("output_dir", out)

    if not drive:
        console.print("[red]Checkpoint is missing drive path. Cannot resume.[/red]")
        raise typer.Exit(1)

    _run_scan(
        drive=drive,
        depth=depth,
        type_groups=type_groups,
        output_dir=output_dir,
        preview_only=False,
        resume_state=cp,
    )


@app.command("virtual-disk")
def cmd_virtual_disk(
    file:  str           = typer.Option(..., "--file",  help="Path to .vhd, .vhdx, or .vmdk file"),
    out:   str           = typer.Option(..., "--out",   help="Output directory for recovered files"),
    depth: Optional[str] = typer.Option("full-carve", "--depth", help="quick | deep | full-carve"),
    types: Optional[str] = typer.Option(None, "--types", help="Comma-separated group keys (default: all)"),
) -> None:
    """
    Scan a VHD, VHDX, or VMDK virtual disk image for recoverable files.
    """
    from drt import virtual_disk

    _warn_if_not_admin()

    if depth not in ("quick", "deep", "full-carve"):
        console.print(f"[red]Invalid depth: {depth!r}.[/red]")
        raise typer.Exit(1)

    if types is None:
        type_groups = ["all"]
    else:
        type_groups = [t.strip() for t in types.split(",") if t.strip()]

    valid_keys = set(list_group_keys())
    for key in type_groups:
        if key not in valid_keys:
            console.print(f"[red]Unknown type group: {key!r}.[/red]")
            raise typer.Exit(1)

    ext = Path(file).suffix.lower()
    if ext not in (".vhd", ".vhdx", ".vmdk"):
        console.print(f"[red]Unsupported virtual disk format: {ext!r}. Use .vhd, .vhdx, or .vmdk.[/red]")
        raise typer.Exit(1)

    if ext in (".vhd", ".vhdx"):
        console.print(f"\nMounting [cyan]{file}[/cyan] read-only via virtdisk.dll…")
        phys_path = virtual_disk.mount_vhd(file)
        if phys_path is None:
            console.print("[bold red]Failed to mount virtual disk.[/bold red] Ensure the file exists and the tool is running elevated.")
            raise typer.Exit(1)
        console.print(f"Mounted as: [green]{phys_path}[/green]\n")
        try:
            _run_scan(
                drive=phys_path,
                depth=depth,
                type_groups=type_groups,
                output_dir=out,
                preview_only=False,
            )
        finally:
            virtual_disk.detach_vhd(file)
            console.print(f"Detached [dim]{file}[/dim]")
    else:
        # VMDK — carve extents directly, no _run_scan (no physical disk handle)
        console.print(f"\nScanning VMDK extents from [cyan]{file}[/cyan]…")
        wanted_extensions = get_extensions_for_groups(type_groups)
        hits = virtual_disk.scan_virtual_disk(file, wanted_extensions, type_groups)
        if not hits:
            console.print("[yellow]No files carved from VMDK.[/yellow]")
            raise typer.Exit(0)

        writer.ensure_structure(out)
        scan_report = report.new_report(file, depth, type_groups)
        index = 0
        for hit in hits:
            start_in_chunk = hit["disk_offset"] - hit["chunk_offset"]
            end_in_chunk   = start_in_chunk + hit["estimated_size"]
            raw_data       = hit["chunk_data"][start_in_chunk:end_in_chunk]
            if not raw_data:
                continue
            index += 1
            ext_h = hit["extension"]
            out_path = writer.write_file(out, ext_h, raw_data, index)
            report.add_found_file(scan_report, ext_h, hit["disk_offset"], str(out_path), len(raw_data))

        report.finalize_report(scan_report, 0.0)
        report_path = report.write_report(scan_report, out)
        console.print(f"\n[bold green]Done.[/bold green] Recovered {index} file(s).")
        console.print(f"Report written: [cyan]{report_path}[/cyan]")


# ---------------------------------------------------------------------------
# Filter builder helper
# ---------------------------------------------------------------------------

def _build_file_filter(
    min_size: Optional[str],
    max_size: Optional[str],
    after:    Optional[str],
    before:   Optional[str],
):
    """Parse filter CLI options and return a filter function, or None if no filters set."""
    min_bytes:  int | None   = None
    max_bytes:  int | None   = None
    after_ts:   float | None = None
    before_ts:  float | None = None

    if min_size:
        try:
            min_bytes = parse_size(min_size)
        except ValueError as exc:
            console.print(f"[red]--min-size: {exc}[/red]")
            raise typer.Exit(1)

    if max_size:
        try:
            max_bytes = parse_size(max_size)
        except ValueError as exc:
            console.print(f"[red]--max-size: {exc}[/red]")
            raise typer.Exit(1)

    if after:
        try:
            after_ts = parse_date(after)
        except ValueError as exc:
            console.print(f"[red]--after: {exc}[/red]")
            raise typer.Exit(1)

    if before:
        try:
            before_ts = parse_date(before)
        except ValueError as exc:
            console.print(f"[red]--before: {exc}[/red]")
            raise typer.Exit(1)

    if any(x is not None for x in (min_bytes, max_bytes, after_ts, before_ts)):
        return make_filter(min_bytes, max_bytes, after_ts, before_ts)
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app()


if __name__ == "__main__":
    main()
