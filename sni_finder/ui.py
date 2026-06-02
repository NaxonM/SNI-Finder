from __future__ import annotations

import collections
import os
import sys
import threading
from dataclasses import dataclass, field
from math import isfinite
from typing import Iterable

from rich.align import Align
from rich.console import Console, Group
from rich.layout import Layout
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

UI_CONSOLE = Console(highlight=False, soft_wrap=False)


# ----------------------------------------------------------------------------
# Theme
# ----------------------------------------------------------------------------

ACCENT = "cyan"
OK_COLOR = "green"
FAIL_COLOR = "red"
WARN_COLOR = "yellow"
MUTED = "grey62"
HEADER_BG = "grey15"

# ASCII-only glyphs so terminals without Unicode fonts still render cleanly.
OK_MARK = "+"
FAIL_MARK = "x"
PENDING_MARK = "."
ARROW = ">"


# ----------------------------------------------------------------------------
# Formatting helpers
# ----------------------------------------------------------------------------


def fmt_duration(seconds: float) -> str:
    if not isfinite(seconds) or seconds < 0:
        return "--:--"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def fmt_rate(rate: float) -> str:
    if not isfinite(rate) or rate <= 0:
        return "--/s"
    if rate >= 10:
        return f"{rate:.0f}/s"
    return f"{rate:.1f}/s"


def fmt_latency(ms: float) -> str:
    if ms is None or not isfinite(ms) or ms < 0:
        return "  --"
    return f"{ms:4.0f}"


def truncate_middle(text: str, width: int) -> str:
    if width <= 1 or len(text) <= width:
        return text.ljust(width) if len(text) < width else text
    if width < 5:
        return text[:width]
    keep = width - 1
    head = (keep + 1) // 2
    tail = keep - head
    return text[:head] + "~" + text[-tail:] if tail > 0 else text[:head] + "~"


def clamp_percent(value: float) -> float:
    if not isfinite(value):
        return 0.0
    return max(0.0, min(100.0, value))


# ----------------------------------------------------------------------------
# Snapshot data
# ----------------------------------------------------------------------------


@dataclass
class WorkingEntry:
    sni: str
    ip: str
    latency_ms: float
    worker: int


@dataclass
class ScanSnapshot:
    total_pairs: int
    processed_pairs: int
    ok_pairs: int
    failed_pairs: int
    total_snis: int
    ok_snis: int
    state: str
    elapsed_seconds: float
    last_event: str
    worker_states: dict[int, str]
    reason_counts: dict[str, int]
    working_recent: list[WorkingEntry] = field(default_factory=list)


# ----------------------------------------------------------------------------
# Pause / terminal utilities
# ----------------------------------------------------------------------------


def pause_terminal(enabled: bool, message: str) -> None:
    if not enabled:
        return
    try:
        input(message)
        return
    except (EOFError, OSError):
        pass

    if os.name == "nt":
        try:
            import msvcrt  # noqa: F401  (kept for runtime check / availability)
            sys.stdout.write(message)
            sys.stdout.flush()
            msvcrt.getch()  # type: ignore[attr-defined]
        except Exception:
            pass


def clear_screen() -> None:
    """Reset cursor to home and clear from there. Avoids the full ANSI clear
    used by Console.clear(), which causes visible flicker on Windows conhost.
    Safe to call between major top-level transitions only."""
    UI_CONSOLE.print("\x1b[H\x1b[J", end="")


# ----------------------------------------------------------------------------
# Resolve-plan table (used outside Live)
# ----------------------------------------------------------------------------


def render_plan_table(per_sni_counts: dict[str, int], max_rows: int = 12) -> Table:
    table = Table(title="Scan Plan", border_style=ACCENT, show_lines=False, expand=True)
    table.add_column("SNI", style="white", ratio=5)
    table.add_column("IPs", justify="right", style=ACCENT, ratio=1)
    table.add_column("Density", ratio=3)

    rows = sorted(per_sni_counts.items(), key=lambda x: (-x[1], x[0]))
    visible = rows[:max_rows]
    max_count = max((count for _, count in rows), default=1)

    for sni, count in visible:
        density = ProgressBar(total=max_count, completed=count, width=24)
        table.add_row(sni, str(count), density)

    hidden = len(rows) - len(visible)
    if hidden > 0:
        table.add_row(f"... and {hidden} more", "", "")
    return table


# ----------------------------------------------------------------------------
# Live scan dashboard
# ----------------------------------------------------------------------------


# Fixed sizing eliminates height jitter and lets Rich Layout diff regions.
LAYOUT_HEADER_SIZE = 3
LAYOUT_PROGRESS_SIZE = 4
LAYOUT_FOOTER_SIZE = 3
LAYOUT_MAIN_MIN_SIZE = 14
SIDEBAR_WORKER_RATIO = 1
SIDEBAR_REASONS_RATIO = 1

WORKING_STREAM_ROWS = 10
WORKER_STATUS_TRUNC = 32
REASON_ROWS = 6


def _state_style(state: str) -> str:
    s = state.lower()
    if s == "running":
        return ACCENT
    if s == "completed":
        return OK_COLOR
    if s in ("stopping", "error"):
        return FAIL_COLOR
    return MUTED


def _make_header(snapshot: ScanSnapshot) -> Panel:
    grid = Table.grid(expand=True)
    grid.add_column(justify="left", ratio=2)
    grid.add_column(justify="center", ratio=1)
    grid.add_column(justify="right", ratio=2)

    state_style = _state_style(snapshot.state)
    grid.add_row(
        Text("SNI-Finder", style=f"bold {ACCENT}"),
        Text(snapshot.state.upper(), style=f"bold {state_style}"),
        Text(f"elapsed {fmt_duration(snapshot.elapsed_seconds)}", style=MUTED),
    )
    return Panel(grid, border_style=ACCENT, padding=(0, 1))


def _make_progress(snapshot: ScanSnapshot, width: int) -> Panel:
    METRICS_WIDTH = 38  # enough for "9999/9999  rate 99.9/s  ETA 99:99:99"
    LABEL_WIDTH = 6
    bar_width = max(10, width - LABEL_WIDTH - METRICS_WIDTH - 8)
    pair_percent = clamp_percent(
        (snapshot.processed_pairs * 100.0 / snapshot.total_pairs) if snapshot.total_pairs else 0.0
    )
    sni_percent = clamp_percent(
        (snapshot.ok_snis * 100.0 / snapshot.total_snis) if snapshot.total_snis else 0.0
    )
    rate = (snapshot.processed_pairs / snapshot.elapsed_seconds) if snapshot.elapsed_seconds > 0 else 0.0
    eta_seconds = ((snapshot.total_pairs - snapshot.processed_pairs) / rate) if rate > 0 else 0.0

    pair_bar = ProgressBar(total=100, completed=pair_percent, width=bar_width, complete_style=ACCENT)
    sni_bar = ProgressBar(total=100, completed=sni_percent, width=bar_width, complete_style=OK_COLOR)

    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(width=LABEL_WIDTH, justify="right", style="bold", no_wrap=True)
    grid.add_column(ratio=1, no_wrap=True)
    grid.add_column(width=METRICS_WIDTH, justify="right", style=MUTED, no_wrap=True)

    grid.add_row(
        Text("Pairs", style="bold white"),
        pair_bar,
        Text(
            f"{snapshot.processed_pairs}/{snapshot.total_pairs}  rate {fmt_rate(rate)}  ETA {fmt_duration(eta_seconds)}",
            style=MUTED,
        ),
    )
    grid.add_row(
        Text("SNIs", style=f"bold {OK_COLOR}"),
        sni_bar,
        Text(f"{snapshot.ok_snis}/{snapshot.total_snis} ({sni_percent:5.1f}%)", style=MUTED),
    )
    return Panel(grid, border_style=ACCENT, padding=(0, 1), title="Progress", title_align="left")


def _make_working_stream(snapshot: ScanSnapshot, rows: int) -> Panel:
    table = Table(
        show_header=True,
        header_style=f"bold {ACCENT}",
        box=None,
        expand=True,
        padding=(0, 1),
    )
    table.add_column("", width=1)
    table.add_column("SNI", ratio=5, no_wrap=True)
    table.add_column("IP", width=15, no_wrap=True)
    table.add_column("ms", width=4, justify="right")
    table.add_column("w", width=2, justify="right")

    entries = list(snapshot.working_recent)[-rows:]
    if not entries:
        table.add_row(
            Text(PENDING_MARK, style=MUTED),
            Text("waiting for first working pair...", style=MUTED),
            "",
            "",
            "",
        )
        # Pad blank rows to keep height stable.
        for _ in range(rows - 1):
            table.add_row("", "", "", "", "")
    else:
        for entry in reversed(entries):
            table.add_row(
                Text(OK_MARK, style=OK_COLOR),
                Text(truncate_middle(entry.sni, 32), style="white"),
                Text(entry.ip, style=ACCENT),
                Text(fmt_latency(entry.latency_ms), style=OK_COLOR),
                Text(str(entry.worker), style=MUTED),
            )
        # Pad to fixed height.
        for _ in range(rows - len(entries)):
            table.add_row("", "", "", "", "")

    return Panel(
        table,
        border_style=OK_COLOR,
        padding=(0, 0),
        title=f"Working pairs (latest {min(len(snapshot.working_recent), rows)} of {snapshot.ok_pairs})",
        title_align="left",
    )


def _make_workers(snapshot: ScanSnapshot, rows: int) -> Panel:
    table = Table(show_header=True, header_style=f"bold {ACCENT}", box=None, expand=True, padding=(0, 1))
    table.add_column("w", width=2, justify="right")
    table.add_column("status", ratio=1, no_wrap=True)

    worker_items = sorted(snapshot.worker_states.items())
    visible = worker_items[:rows]
    for worker_id, status in visible:
        s = truncate_middle(status, WORKER_STATUS_TRUNC)
        lower = status.lower()
        if "testing" in lower:
            style = WARN_COLOR
        elif "idle" in lower or "done" in lower:
            style = MUTED
        elif "stopped" in lower:
            style = FAIL_COLOR
        else:
            style = "white"
        table.add_row(Text(str(worker_id), style=MUTED), Text(s, style=style))
    for _ in range(rows - len(visible)):
        table.add_row("", "")

    return Panel(table, border_style=ACCENT, padding=(0, 0), title="Workers", title_align="left")


def _make_reasons(snapshot: ScanSnapshot, rows: int) -> Panel:
    table = Table(show_header=True, header_style=f"bold {WARN_COLOR}", box=None, expand=True, padding=(0, 1))
    table.add_column("reason", ratio=1, no_wrap=True)
    table.add_column("count", width=6, justify="right")

    items = sorted(snapshot.reason_counts.items(), key=lambda x: -x[1])[:rows]
    for reason, count in items:
        table.add_row(Text(truncate_middle(reason, 22), style=FAIL_COLOR), Text(str(count), style=MUTED))
    for _ in range(rows - len(items)):
        table.add_row("", "")
    if not items:
        # Replace first padded row with a hint so the panel isn't blank.
        # (rows >=1 always)
        pass

    return Panel(table, border_style=WARN_COLOR, padding=(0, 0), title="Failure reasons", title_align="left")


def _make_footer(snapshot: ScanSnapshot) -> Panel:
    grid = Table.grid(expand=True)
    grid.add_column(ratio=4, justify="left")
    grid.add_column(ratio=1, justify="right")
    last_event_style = MUTED
    if snapshot.last_event.startswith("OK "):
        last_event_style = OK_COLOR
    elif snapshot.last_event.startswith("FAIL "):
        last_event_style = FAIL_COLOR
    grid.add_row(
        Text(truncate_middle(snapshot.last_event, 90), style=last_event_style),
        Text("Ctrl+C to stop gracefully", style=MUTED),
    )
    return Panel(grid, border_style=MUTED, padding=(0, 1))


def build_layout() -> Layout:
    layout = Layout(name="root")
    layout.split_column(
        Layout(name="header", size=LAYOUT_HEADER_SIZE),
        Layout(name="progress", size=LAYOUT_PROGRESS_SIZE),
        Layout(name="main", minimum_size=LAYOUT_MAIN_MIN_SIZE),
        Layout(name="footer", size=LAYOUT_FOOTER_SIZE),
    )
    layout["main"].split_row(
        Layout(name="stream", ratio=2),
        Layout(name="sidebar", ratio=1),
    )
    layout["sidebar"].split_column(
        Layout(name="workers"),
        Layout(name="reasons"),
    )
    return layout


def update_layout(layout: Layout, snapshot: ScanSnapshot, width: int) -> None:
    """Update each region in-place; Rich diffs regions for minimal redraw."""
    layout["header"].update(_make_header(snapshot))
    layout["progress"].update(_make_progress(snapshot, width=width))
    layout["stream"].update(_make_working_stream(snapshot, WORKING_STREAM_ROWS))
    layout["workers"].update(_make_workers(snapshot, max(4, len(snapshot.worker_states))))
    layout["reasons"].update(_make_reasons(snapshot, REASON_ROWS))
    layout["footer"].update(_make_footer(snapshot))


# Backwards-compatible single-renderable for code paths that still call it.
def build_dashboard(snapshot: ScanSnapshot) -> Layout:
    layout = build_layout()
    update_layout(layout, snapshot, width=UI_CONSOLE.size.width)
    return layout


# ----------------------------------------------------------------------------
# Summary screen
# ----------------------------------------------------------------------------


def render_summary_tables(
    summary: dict[str, object],
    report_path: str,
    working_pairs: list[dict[str, object]] | None = None,
    max_working_rows: int = 12,
) -> list[Table]:
    total_pairs = int(summary.get("total_pairs", 0) or 0)
    working_pair_count = int(summary.get("working_pairs", 0) or 0)
    success_rate = (working_pair_count * 100.0 / total_pairs) if total_pairs else 0.0

    totals = Table(title="Summary", show_header=False, border_style=OK_COLOR, expand=True)
    totals.add_column("Key", style=ACCENT, ratio=1)
    totals.add_column("Value", style="white", ratio=2)
    totals.add_row("Total SNIs", str(summary.get("total_snis", 0)))
    totals.add_row("Successful SNIs", str(summary.get("successful_snis", 0)))
    totals.add_row("Total pairs", str(summary.get("total_pairs", 0)))
    totals.add_row("Working pairs", str(summary.get("working_pairs", 0)))
    totals.add_row("Failed pairs", str(summary.get("failed_pairs", 0)))
    totals.add_row("Pair success rate", f"{success_rate:.2f}%")
    totals.add_row("State", str(summary.get("state", "unknown")))
    totals.add_row("Report", report_path)

    working_table = Table(title="Working SNI/IP Pairs (top by latency)", border_style=OK_COLOR, expand=True)
    working_table.add_column("SNI", style="white", ratio=4, no_wrap=True)
    working_table.add_column("IP", style=ACCENT, ratio=2, no_wrap=True)
    working_table.add_column("Latency", style=OK_COLOR, justify="right", ratio=1)
    working_table.add_column("Worker", style=MUTED, justify="right", ratio=1)

    rows = working_pairs or []
    rows_sorted = sorted(rows, key=lambda item: float(item.get("latency_ms", 10**9)))
    for item in rows_sorted[:max_working_rows]:
        pair_obj = item.get("pair") if isinstance(item.get("pair"), dict) else {}
        pair = pair_obj if isinstance(pair_obj, dict) else {}
        working_table.add_row(
            str(pair.get("sni", "-")),
            str(pair.get("ip", "-")),
            f"{float(item.get('latency_ms', 0)):.0f} ms" if item.get("latency_ms") is not None else "-",
            str(item.get("worker", "-")),
        )
    hidden = len(rows_sorted) - min(len(rows_sorted), max_working_rows)
    if hidden > 0:
        working_table.add_row(f"... and {hidden} more", "", "", "")
    if not rows_sorted:
        working_table.add_row("No working pairs found", "-", "-", "-")

    return [totals, working_table]


# ----------------------------------------------------------------------------
# Phase / banner helpers (used outside Live)
# ----------------------------------------------------------------------------


def phase(title: str, detail: str = "") -> None:
    header = f"[bold {ACCENT}]{ARROW} {title}[/bold {ACCENT}]"
    body = detail if detail else "Working..."
    UI_CONSOLE.print(f"{header}  [white]{body}[/white]")


def banner(title: str, detail: str = "", style: str = ACCENT) -> None:
    """Compact single-line banner for inline status. No box, no flicker."""
    content = f"[bold {style}]{title}[/bold {style}]"
    if detail:
        content += f"  [white]{detail}[/white]"
    UI_CONSOLE.print(content)


def section_rule(title: str, style: str = ACCENT) -> None:
    UI_CONSOLE.rule(f"[bold {style}]{title}[/bold {style}]", style=style)


def error(title: str, detail: str = "") -> None:
    """Render an error: red prefix tag + bold title + optional detail."""
    head = f"[white on red] ERROR [/]  [bold {FAIL_COLOR}]{title}[/]"
    UI_CONSOLE.print(head)
    if detail:
        UI_CONSOLE.print(f"         [white]{detail}[/white]")


def warn(title: str, detail: str = "") -> None:
    """Render a warning: yellow prefix tag + bold title + optional detail."""
    head = f"[black on yellow] WARN  [/]  [bold {WARN_COLOR}]{title}[/]"
    UI_CONSOLE.print(head)
    if detail:
        UI_CONSOLE.print(f"         [white]{detail}[/white]")


def success(title: str, detail: str = "") -> None:
    head = f"[white on green]  OK   [/]  [bold {OK_COLOR}]{title}[/]"
    UI_CONSOLE.print(head)
    if detail:
        UI_CONSOLE.print(f"         [white]{detail}[/white]")


def info(title: str, detail: str = "") -> None:
    head = f"[white on blue] INFO  [/]  [bold {ACCENT}]{title}[/]"
    UI_CONSOLE.print(head)
    if detail:
        UI_CONSOLE.print(f"         [white]{detail}[/white]")
