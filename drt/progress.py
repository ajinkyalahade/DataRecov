"""
Live scan progress tracker and dashboard renderer.

Exposes:
  ProgressTracker  — dataclass holding all live scan stats
  make_dashboard   — builds a rich Layout from current tracker state
"""

import time
from collections import deque
from dataclasses import dataclass, field

from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


# ---------------------------------------------------------------------------
# ProgressTracker
# ---------------------------------------------------------------------------

@dataclass
class ProgressTracker:
    total_bytes: int
    bytes_scanned: int = 0
    current_phase: str = "Initializing"
    drive: str = ""
    files_by_group: dict = field(default_factory=dict)
    # Each element: {"name": str, "extension": str, "size_bytes": int, "offset_or_path": str}
    recent_finds: deque = field(default_factory=lambda: deque(maxlen=5))
    start_time: float = field(default_factory=time.monotonic)
    # Carve phase current byte offset (updated during Phase 6)
    carve_offset: int = 0
    # Speed sampling: list of (monotonic_time, bytes_scanned) over last 5s
    _speed_samples: deque = field(default_factory=lambda: deque(maxlen=60))

    def record_sample(self) -> None:
        """Record a speed sample at the current moment."""
        self._speed_samples.append((time.monotonic(), self.bytes_scanned))

    @property
    def speed_bytes_per_second(self) -> float:
        """Rolling 5-second speed. Returns 0.0 if insufficient data."""
        now = time.monotonic()
        cutoff = now - 5.0
        # Filter to samples within the last 5 seconds
        recent = [(t, b) for t, b in self._speed_samples if t >= cutoff]
        if len(recent) < 2:
            return 0.0
        dt = recent[-1][0] - recent[0][0]
        db = recent[-1][1] - recent[0][1]
        if dt <= 0:
            return 0.0
        return db / dt

    @property
    def total_files(self) -> int:
        return sum(self.files_by_group.values())



# ---------------------------------------------------------------------------
# Dashboard builder
# ---------------------------------------------------------------------------

_GROUP_DISPLAY_ORDER = [
    ("images",    "Images"),
    ("videos",    "Videos"),
    ("audio",     "Audio"),
    ("documents", "Documents"),
    ("archives",  "Archives"),
    ("databases", "Databases"),
    ("email",     "Email"),
    ("code",      "Code"),
    ("artifacts", "Artifacts"),
    ("browser",   "Browser"),
    ("other",     "Other"),
]

_BAR_CHARS = "█"
_EMPTY_CHAR = "░"
_MAX_BAR_WIDTH = 16


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _fmt_speed(bps: float) -> str:
    if bps <= 0:
        return "—"
    return _fmt_bytes(int(bps)) + "/s"


def _fmt_eta(total: int, scanned: int, bps: float) -> str:
    if bps <= 0:
        return "calculating…"
    remaining = total - scanned
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


def _build_scan_progress_panel(tracker: ProgressTracker) -> Panel:
    total = tracker.total_bytes
    scanned = tracker.bytes_scanned
    pct = (scanned / total * 100) if total > 0 else 0.0
    speed = tracker.speed_bytes_per_second
    eta = _fmt_eta(total, scanned, speed)

    scanned_str = _fmt_bytes(scanned)
    total_str   = _fmt_bytes(total)

    # Build progress bar string manually (rich ProgressBar is not renderable inline)
    bar_width = 30
    filled = round(pct / 100 * bar_width)
    bar_str = _BAR_CHARS * filled + _EMPTY_CHAR * (bar_width - filled)

    lines = [
        f"Phase:    [bold cyan]{tracker.current_phase}[/bold cyan]",
        f"Drive:    [dim]{tracker.drive or '—'}[/dim]",
        f"Scanned:  [green]{scanned_str}[/green] / {total_str}  ([yellow]{pct:.1f}%[/yellow])",
        f"Speed:    [cyan]{_fmt_speed(speed)}[/cyan]",
        f"ETA:      [yellow]{eta}[/yellow]",
        "",
        f"[bold blue]{bar_str}[/bold blue] [yellow]{pct:.1f}%[/yellow]",
    ]
    content = Text.from_markup("\n".join(lines))
    return Panel(content, title="[bold]Scan Progress[/bold]", border_style="blue")


def _build_files_found_panel(tracker: ProgressTracker) -> Panel:
    total_files = tracker.total_files

    rows: list[tuple[str, int]] = []
    found_groups = set(tracker.files_by_group.keys())

    # Show defined order first, then any extras
    shown: set[str] = set()
    for key, label in _GROUP_DISPLAY_ORDER:
        count = tracker.files_by_group.get(key, 0)
        if count > 0 or key in found_groups:
            rows.append((label, count))
            shown.add(key)

    # Any groups not in the display order
    for key, count in tracker.files_by_group.items():
        if key not in shown and count > 0:
            rows.append((key.capitalize(), count))

    max_count = max((r[1] for r in rows), default=1)

    t = Table.grid(padding=(0, 1))
    t.add_column(width=10)
    t.add_column(width=_MAX_BAR_WIDTH)
    t.add_column(justify="right", width=6)

    for label, count in rows:
        bar_text = Text()
        filled = round((count / max_count) * _MAX_BAR_WIDTH) if max_count > 0 else 0
        if count > 0:
            filled = max(1, filled)
        bar_text.append(_BAR_CHARS * filled, style="bold green")
        bar_text.append(_EMPTY_CHAR * (_MAX_BAR_WIDTH - filled), style="dim")
        t.add_row(f"[cyan]{label}[/cyan]", bar_text, f"[white]{count:,}[/white]")

    header = Text.from_markup(
        f"Total:  [bold green]{total_files:,}[/bold green] files\n"
    )

    from rich.console import Group as RichGroup
    content = RichGroup(header, t)
    return Panel(content, title="[bold]Files Found[/bold]", border_style="green")


def _build_recent_finds_panel(tracker: ProgressTracker) -> Panel:
    t = Table.grid(padding=(0, 1))
    t.add_column(width=6)   # ext
    t.add_column(width=30)  # name
    t.add_column(width=10, justify="right")  # size
    t.add_column()          # offset / path

    for item in tracker.recent_finds:
        ext  = item.get("extension", "")
        name = item.get("name", "")
        size = _fmt_bytes(item.get("size_bytes", 0))
        loc  = item.get("offset_or_path", "")
        t.add_row(
            f"[cyan]{ext}[/cyan]",
            f"[white]{name}[/white]",
            f"[dim]{size}[/dim]",
            f"[dim]{loc}[/dim]",
        )

    return Panel(t, title="[bold]Recent Finds[/bold]", border_style="magenta")


def make_dashboard(tracker: ProgressTracker) -> Layout:
    """
    Build and return a rich Layout from current tracker state.
    Called each refresh cycle inside a Live context.
    """
    layout = Layout()

    layout.split_column(
        Layout(name="top", ratio=3),
        Layout(name="bottom", ratio=2),
    )

    layout["top"].split_row(
        Layout(name="left"),
        Layout(name="right"),
    )

    layout["left"].update(_build_scan_progress_panel(tracker))
    layout["right"].update(_build_files_found_panel(tracker))
    layout["bottom"].update(_build_recent_finds_panel(tracker))

    return layout
