#!/usr/bin/env python3
"""
SNI-Finder scanner entrypoint

Modular layout:
- shared.py: shared constants/models/environment helpers
- settings.py: persistent settings load/save
- profile.py: VLESS profile parsing
- pairs.py: SNI/IP pair extraction and DNS resolution
- ui.py: dashboard rendering and pause behavior
- engine.py: worker runtime and scan orchestration
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
from typing import Any

from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.prompt import FloatPrompt, IntPrompt, Prompt
from rich.table import Table

from sni_finder.engine import run_scan
from sni_finder.pairs import filter_pairs_by_subnets, load_cf_subnets, resolve_pairs_from_sni_list, save_resolved_pairs
from sni_finder.settings import load_settings, save_settings
from sni_finder.shared import CF_SUBNETS_PATH, GLOBAL_STOP, RESULTS_DIR, SCANNER_LOG_PATH, SNI_LIST_PATH, ScanSettings, ensure_dirs, is_elevated_windows, relaunch_with_uac, setup_logging
from sni_finder.ui import UI_CONSOLE, pause_terminal, render_plan_table


def resolve_with_progress(max_ips_per_sni: int) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]], int]:
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=40),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=UI_CONSOLE,
        transient=False,
    ) as progress:
        task_id = progress.add_task("Resolving DNS", total=None)

        def _resolve_progress(idx: int, total: int, sni: str) -> None:
            progress.update(
                task_id,
                total=total,
                completed=idx,
                description=f"Resolving DNS ({sni})",
            )

        snis, resolved_pairs = resolve_pairs_from_sni_list(max_ips_per_sni, progress_cb=_resolve_progress)

    cf_subnets = load_cf_subnets()
    pairs, dropped_pairs = filter_pairs_by_subnets(resolved_pairs, cf_subnets)
    save_resolved_pairs(pairs)
    return snis, resolved_pairs, pairs, dropped_pairs


def configure_interactive(settings: ScanSettings) -> ScanSettings:
    UI_CONSOLE.print(
        Panel(
            "Edit scanner settings. Press Enter to keep current values.",
            title="[bold cyan]Configure SNI-Finder[/bold cyan]",
            border_style="cyan",
        )
    )

    settings.vless_source = Prompt.ask(
        "vless_source (vless://... or path to xray json/txt)",
        default=settings.vless_source,
        show_default=True,
    ).strip()
    settings.workers = IntPrompt.ask("workers", default=settings.workers, show_default=True)
    settings.max_ips_per_sni = IntPrompt.ask("max_ips_per_sni", default=settings.max_ips_per_sni, show_default=True)
    settings.retries_per_pair = IntPrompt.ask("retries_per_pair", default=settings.retries_per_pair, show_default=True)
    settings.probe_url = Prompt.ask("probe_url", default=settings.probe_url, show_default=True).strip()
    settings.snispf_ready_timeout_seconds = FloatPrompt.ask(
        "snispf_ready_timeout_seconds", default=float(settings.snispf_ready_timeout_seconds), show_default=True
    )
    settings.xray_ready_timeout_seconds = FloatPrompt.ask(
        "xray_ready_timeout_seconds", default=float(settings.xray_ready_timeout_seconds), show_default=True
    )
    settings.probe_connect_timeout_seconds = FloatPrompt.ask(
        "probe_connect_timeout_seconds", default=float(settings.probe_connect_timeout_seconds), show_default=True
    )
    settings.probe_read_timeout_seconds = FloatPrompt.ask(
        "probe_read_timeout_seconds", default=float(settings.probe_read_timeout_seconds), show_default=True
    )

    save_settings(settings)
    UI_CONSOLE.print(Panel("[green]Settings saved.[/green]", border_style="green"))
    return settings


def menu(settings: ScanSettings) -> int:
    while True:
        menu_table = Table(title="SNI-Finder Menu", border_style="cyan", show_header=True, expand=True)
        menu_table.add_column("Option", style="cyan", width=8)
        menu_table.add_column("Action", style="white")
        menu_table.add_row("1", "Configure scanner settings")
        menu_table.add_row("2", "Resolve SNI+IP pairs only")
        menu_table.add_row("3", "Run full scan (Ctrl+C = graceful stop)")
        menu_table.add_row("4", "Exit")
        UI_CONSOLE.print(menu_table)

        choice = Prompt.ask("Select option", choices=["1", "2", "3", "4"], show_choices=False)

        if choice == "1":
            settings = configure_interactive(settings)
        elif choice == "2":
            snis, resolved_pairs, pairs, dropped_pairs = resolve_with_progress(settings.max_ips_per_sni)
            per_sni_counts: dict[str, int] = {}
            for pair in pairs:
                sni = str(pair.get("sni", ""))
                per_sni_counts[sni] = per_sni_counts.get(sni, 0) + 1

            UI_CONSOLE.print(render_plan_table(per_sni_counts))
            UI_CONSOLE.print(
                Panel(
                    f"[bold]Input SNIs:[/bold] {len(snis)}\n"
                    f"[bold]Resolved pairs:[/bold] {len(resolved_pairs)}\n"
                    f"[bold]Cloudflare pairs:[/bold] {len(pairs)}\n"
                    f"[bold]Dropped (non-CF):[/bold] {dropped_pairs}\n"
                    f"[bold]Saved:[/bold] {RESULTS_DIR / 'resolved_pairs.json'}\n"
                    f"[bold]SNI list:[/bold] {SNI_LIST_PATH}\n"
                    f"[bold]CF subnets:[/bold] {CF_SUBNETS_PATH}",
                    title="Resolve Complete",
                    border_style="green",
                )
            )
        elif choice == "3":
            # In menu mode, never terminate the app after a scan.
            # Show summary, then return to menu on Enter.
            exit_code = run_scan(settings, pause_on_exit=False)
            if exit_code == 0:
                pause_terminal(True, "Scan complete. Press Enter to return to menu...")
            else:
                pause_terminal(True, "Scan ended with errors. Press Enter to return to menu...")
            os.system("cls" if os.name == "nt" else "clear")
            continue
        elif choice == "4":
            return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SNI+IP scanner using SNISPF + Xray")
    parser.add_argument("command", nargs="?", default="menu", choices=["menu", "configure", "resolve", "run"], help="Action")
    parser.add_argument("--vless", default="", help="Override vless_source for this run")
    parser.add_argument("--workers", type=int, default=0, help="Override workers for this run")
    parser.add_argument("--no-pause-on-error", action="store_true", help="Do not wait for Enter on fatal setup errors")
    parser.add_argument("--no-pause-on-complete", action="store_true", help="Do not wait for Enter after scan summary")
    parser.add_argument("--uac-relaunched", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command in ("run", "menu"):
        setup_logging()
    elif args.command == "resolve":
        ensure_dirs(include_runtime_dirs=True)

    settings = load_settings()

    if args.vless:
        settings.vless_source = args.vless
    if args.workers > 0:
        settings.workers = args.workers

    shutdown = threading.Event()

    def _on_signal(_sig: int, _frame: Any) -> None:
        shutdown.set()
        GLOBAL_STOP.set()
        print("\nStop requested. Finishing active workers and cleaning up...")

    signal.signal(signal.SIGINT, _on_signal)

    if args.command == "configure":
        configure_interactive(settings)
        return 0

    if args.command == "resolve":
        snis, resolved_pairs, pairs, dropped_pairs = resolve_with_progress(settings.max_ips_per_sni)
        UI_CONSOLE.print(
            Panel(
                f"[bold]Input SNIs:[/bold] {len(snis)}\n"
                f"[bold]Resolved pairs:[/bold] {len(resolved_pairs)}\n"
                f"[bold]Cloudflare pairs:[/bold] {len(pairs)}\n"
                f"[bold]Dropped (non-CF):[/bold] {dropped_pairs}\n"
                f"[bold]Saved:[/bold] {RESULTS_DIR / 'resolved_pairs.json'}\n"
                f"[bold]SNI list:[/bold] {SNI_LIST_PATH}\n"
                f"[bold]CF subnets:[/bold] {CF_SUBNETS_PATH}",
                title="Resolve Complete",
                border_style="green",
            )
        )
        return 0

    if args.command == "run":
        pause_on_exit = not args.no_pause_on_complete
        if os.name == "nt" and not is_elevated_windows():
            UI_CONSOLE.print(
                Panel(
                    "Scanner requires Administrator privileges on Windows for SNISPF wrong_seq probing.",
                    border_style="yellow",
                    title="Elevation Required",
                )
            )
            if args.uac_relaunched:
                UI_CONSOLE.print(
                    Panel(
                        "UAC relaunch did not provide elevation. Please run from an elevated PowerShell.",
                        border_style="red",
                        title="Elevation Failed",
                    )
                )
                logging.error("UAC relaunch did not provide elevation")
                pause_terminal(not args.no_pause_on_error, "Press Enter to close...")
                return 1
            UI_CONSOLE.print(Panel("Requesting elevation via UAC...", border_style="cyan", title="Action"))
            logging.info("Requesting elevation via UAC")
            if relaunch_with_uac():
                # Parent exits after successful handoff to elevated child.
                return 0
            UI_CONSOLE.print(Panel("UAC elevation request was denied or failed.", border_style="red", title="Elevation Failed"))
            logging.error("UAC elevation request denied or failed")
            pause_terminal(not args.no_pause_on_error, "Press Enter to close...")
            return 1

        return run_scan(settings, pause_on_exit=pause_on_exit)

    return menu(settings)


if __name__ == "__main__":
    raise SystemExit(main())
