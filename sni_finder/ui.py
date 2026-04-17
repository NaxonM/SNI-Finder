from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from math import isfinite

from rich.console import Console
from rich.progress_bar import ProgressBar
from rich.panel import Panel
from rich.table import Table

UI_CONSOLE = Console()

ACCENT = "cyan"
OK_COLOR = "green"
FAIL_COLOR = "red"
MUTED = "grey62"


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
            import msvcrt

            sys.stdout.write(message)
            sys.stdout.flush()
            msvcrt.getch()
        except Exception:
            pass


def phase(title: str, detail: str = "") -> None:
    header = f"[bold {ACCENT}]{title}[/bold {ACCENT}]"
    body = detail if detail else "Working..."
    UI_CONSOLE.print(
        Panel(
            f"[white]{body}[/white]",
            title=header,
            border_style=ACCENT,
            padding=(0, 1),
        )
    )


def _clamp_percent(value: float) -> float:
    if not isfinite(value):
        return 0.0
    return max(0.0, min(100.0, value))


def _status_style(text: str) -> str:
    lower = text.lower()
    if "testing" in lower:
        return "yellow"
    if "fail" in lower or "error" in lower:
        return FAIL_COLOR
    if "ok" in lower or "done" in lower:
        return OK_COLOR
    return MUTED


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


def build_dashboard(snapshot: ScanSnapshot) -> Panel:
    percent_pairs = _clamp_percent(
        (snapshot.processed_pairs * 100.0 / snapshot.total_pairs) if snapshot.total_pairs else 0.0
    )
    percent_snis = _clamp_percent((snapshot.ok_snis * 100.0 / snapshot.total_snis) if snapshot.total_snis else 0.0)
    rate = (snapshot.processed_pairs / snapshot.elapsed_seconds) if snapshot.elapsed_seconds > 0 else 0.0
    eta_seconds = ((snapshot.total_pairs - snapshot.processed_pairs) / rate) if rate > 0 else 0.0

    header = Table.grid(expand=True)
    header.add_column(justify="left")
    header.add_column(justify="right")
    header.add_row(
        "[bold white]SNI-Finder Live Dashboard[/bold white]",
        f"[bold yellow]{snapshot.state.upper()}[/bold yellow]",
    )

    stats = Table.grid(expand=True, pad_edge=False)
    stats.add_column(ratio=1)
    stats.add_column(ratio=1)
    stats.add_column(ratio=1)
    stats.add_column(ratio=1)

    rate = (snapshot.processed_pairs / snapshot.elapsed_seconds) if snapshot.elapsed_seconds > 0 else 0.0
    stats.add_row(
        f"[white]Pairs[/]: {snapshot.processed_pairs}/{snapshot.total_pairs}",
        f"[{OK_COLOR}]Pair OK[/]: {snapshot.ok_pairs}",
        f"[{FAIL_COLOR}]Pair FAIL[/]: {snapshot.failed_pairs}",
        f"[{ACCENT}]Rate[/]: {rate:.2f}/s",
    )
    stats.add_row(
        f"[white]SNI Success[/]: {snapshot.ok_snis}/{snapshot.total_snis}",
        f"[{ACCENT}]Elapsed[/]: {snapshot.elapsed_seconds:.1f}s",
        f"[{ACCENT}]ETA[/]: {eta_seconds:.1f}s",
        f"[{ACCENT}]Progress[/]: {percent_pairs:.1f}%",
    )

    progress_block = Table.grid(expand=True)
    progress_block.add_column()
    progress_block.add_row("[bold]Pair progress[/bold]")
    progress_block.add_row(ProgressBar(total=100, completed=percent_pairs, width=60))
    progress_block.add_row(f"[bold]SNI success coverage[/bold] {percent_snis:.1f}%")
    progress_block.add_row(ProgressBar(total=100, completed=percent_snis, width=60))

    workers = Table(show_header=True, header_style=f"bold {ACCENT}", expand=True)
    workers.add_column("Worker", width=8)
    workers.add_column("Status", ratio=8)
    for worker_id in sorted(snapshot.worker_states.keys()):
        status = snapshot.worker_states[worker_id][:100]
        workers.add_row(str(worker_id), f"[{_status_style(status)}]{status}[/]")

    reasons = Table(show_header=True, header_style="bold yellow", expand=True)
    reasons.add_column("Failure reason")
    reasons.add_column("Share", justify="right")
    reasons.add_column("Count", justify="right")
    if snapshot.reason_counts:
        fail_total = max(1, snapshot.failed_pairs)
        for reason, count in sorted(snapshot.reason_counts.items(), key=lambda x: -x[1])[:8]:
            share = (count * 100.0) / fail_total
            reasons.add_row(reason, f"{share:.1f}%", str(count))
    else:
        reasons.add_row("-", "0.0%", "0")

    body = Table.grid(expand=True)
    body.add_row(header)
    body.add_row(Panel(progress_block, border_style=ACCENT, title="Progress"))
    body.add_row(stats)
    body.add_row(Panel(workers, title="Workers", border_style="blue"))
    body.add_row(Panel(reasons, title="Failure Reasons", border_style="yellow"))
    body.add_row(f"[dim]Last event: {snapshot.last_event}[/dim]")

    return Panel(body, border_style=ACCENT, padding=(0, 1))


def render_summary_tables(
    summary: dict[str, object],
    report_path: str,
    working_pairs: list[dict[str, object]] | None = None,
    max_working_rows: int = 12,
) -> list[Table]:
    total_pairs = int(summary.get("total_pairs", 0) or 0)
    working_pair_count = int(summary.get("working_pairs", 0) or 0)
    success_rate = (working_pair_count * 100.0 / total_pairs) if total_pairs else 0.0

    totals = Table(title="Summary", show_header=False, border_style=OK_COLOR)
    totals.add_column("Key", style="cyan")
    totals.add_column("Value", style="white")
    totals.add_row("Total SNIs", str(summary.get("total_snis", 0)))
    totals.add_row("Successful SNIs", str(summary.get("successful_snis", 0)))
    totals.add_row("Total pairs", str(summary.get("total_pairs", 0)))
    totals.add_row("Working pairs", str(summary.get("working_pairs", 0)))
    totals.add_row("Failed pairs", str(summary.get("failed_pairs", 0)))
    totals.add_row("Pair success rate", f"{success_rate:.2f}%")
    totals.add_row("State", str(summary.get("state", "unknown")))
    totals.add_row("Report", report_path)

    sni_breakdown = Table(title="SNI Outcome", show_header=False, border_style="blue")
    sni_breakdown.add_column("Key", style="cyan")
    sni_breakdown.add_column("Value", style="white")
    sni_breakdown.add_row("SNIs with at least one success", str(summary.get("successful_snis", 0)))
    sni_breakdown.add_row("SNIs with zero success", str(summary.get("failed_snis", 0)))

    working_table = Table(title="Working SNI/IP Pairs", border_style="magenta", expand=True)
    working_table.add_column("SNI", style="white", ratio=4)
    working_table.add_column("IP", style="cyan", ratio=2)
    working_table.add_column("Latency (ms)", style="green", justify="right", ratio=1)
    working_table.add_column("Worker", style="yellow", justify="right", ratio=1)
    working_table.add_column("Attempt", style="yellow", justify="right", ratio=1)

    rows = working_pairs or []
    rows_sorted = sorted(rows, key=lambda item: float(item.get("latency_ms", 10**9)))
    for item in rows_sorted[:max_working_rows]:
        pair_obj = item.get("pair") if isinstance(item.get("pair"), dict) else {}
        pair = pair_obj if isinstance(pair_obj, dict) else {}
        working_table.add_row(
            str(pair.get("sni", "-")),
            str(pair.get("ip", "-")),
            str(item.get("latency_ms", "-")),
            str(item.get("worker", "-")),
            str(item.get("attempt", "-")),
        )

    hidden = len(rows_sorted) - min(len(rows_sorted), max_working_rows)
    if hidden > 0:
        working_table.add_row(f"... and {hidden} more", "", "", "", "")
    if not rows_sorted:
        working_table.add_row("-", "-", "-", "-", "-")

    return [totals, sni_breakdown, working_table]
