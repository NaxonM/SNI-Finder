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
import json
import logging
import os
import signal
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.prompt import Confirm, FloatPrompt, IntPrompt, Prompt

from sni_finder.engine import run_scan
from sni_finder.pairs import filter_pairs_by_subnets, load_cf_subnets, resolve_pairs_from_sni_list, save_resolved_pairs
from sni_finder.settings import load_settings, save_settings
from sni_finder import shared
from sni_finder.shared import CF_SUBNETS_PATH, GLOBAL_STOP, RESULTS_DIR, SCANNER_LOG_PATH, SNI_LIST_PATH, ScanSettings, ensure_dirs, is_elevated_windows, relaunch_with_uac, setup_logging
from sni_finder.ui import (
    ACCENT,
    FAIL_COLOR,
    MUTED,
    OK_COLOR,
    UI_CONSOLE,
    WARN_COLOR,
    banner,
    clear_screen,
    error,
    info,
    pause_terminal,
    render_plan_table,
    render_summary_tables,
    section_rule,
    success,
    warn,
)


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


def _ask_proxy_source(current: str) -> str:
    """Prompt for a proxy URI and validate it live. Re-prompts on parse error."""
    from sni_finder.profile import parse_proxy_uri

    UI_CONSOLE.print(f"  [{MUTED}]Examples:[/]")
    UI_CONSOLE.print(f"    [{MUTED}]vless://uuid@host:443?security=tls&type=ws&host=example.com&path=/p&sni=example.com[/]")
    UI_CONSOLE.print(f"    [{MUTED}]trojan://password@host:443?security=tls&type=ws&host=example.com&path=/p&sni=example.com[/]")
    UI_CONSOLE.print()

    while True:
        value = Prompt.ask(
            "  Proxy URI [bold](vless:// or trojan://)[/]",
            default=current,
            show_default=bool(current),
        ).strip()

        if not value:
            UI_CONSOLE.print(f"  [{WARN_COLOR}]A proxy URI is required.[/{WARN_COLOR}]")
            continue

        # File path / xray json — accept as-is; load_vless_profile handles them.
        if not (value.startswith("vless://") or value.startswith("trojan://")):
            if Path(value).exists():
                return value
            UI_CONSOLE.print(
                f"  [{FAIL_COLOR}]That doesn't look like a vless:// or trojan:// URI, "
                f"and no file exists at that path.[/{FAIL_COLOR}]"
            )
            continue

        try:
            profile = parse_proxy_uri(value)
        except Exception as exc:
            UI_CONSOLE.print(f"  [{FAIL_COLOR}]Could not parse URI:[/{FAIL_COLOR}] {exc}")
            UI_CONSOLE.print(f"  [{MUTED}]Try again, or press Ctrl+C to cancel.[/{MUTED}]")
            continue

        # Compact detected-fields summary, so the user can confirm they pasted the right thing.
        UI_CONSOLE.print()
        UI_CONSOLE.print(f"  [{OK_COLOR}]Parsed OK:[/{OK_COLOR}]")
        UI_CONSOLE.print(f"    Protocol:  [bold]{profile.protocol}[/]")
        UI_CONSOLE.print(f"    Transport: [bold]{profile.network}[/]  ([{MUTED}]security={profile.security}[/])")
        UI_CONSOLE.print(f"    SNI:       [bold]{profile.sni or '(none)'}[/]")
        UI_CONSOLE.print(f"    Host:      [bold]{profile.host or '(none)'}[/]")
        UI_CONSOLE.print(f"    Path:      [bold]{profile.path or '(none)'}[/]")
        UI_CONSOLE.print()
        return value


def configure_interactive(settings: ScanSettings, *, first_run: bool = False) -> ScanSettings:
    if first_run:
        section_rule("Welcome to SNI-Finder")
        UI_CONSOLE.print(
            f"  [white]SNI-Finder probes Cloudflare-fronted SNIs through your VLESS / Trojan endpoint[/]"
        )
        UI_CONSOLE.print(
            f"  [white]to find pairs that survive SNI-based DPI. This one-time setup takes ~30 seconds.[/]"
        )
        UI_CONSOLE.print()
    else:
        section_rule("Configure SNI-Finder")
        UI_CONSOLE.print(f"  [{MUTED}]Press Enter to keep current values. Ctrl+C to cancel.[/]")
        UI_CONSOLE.print()

    # --- Step 1: required ---
    UI_CONSOLE.print(f"[bold {ACCENT}]Step 1 — Proxy source[/] [{MUTED}](required)[/]")
    settings.vless_source = _ask_proxy_source(settings.vless_source)

    # --- Step 2: performance ---
    UI_CONSOLE.print(f"[bold {ACCENT}]Step 2 — Performance[/]")
    if first_run:
        UI_CONSOLE.print(f"  [{MUTED}]Sensible defaults; tweak only if you know what you want.[/]")
    settings.workers = IntPrompt.ask("  Parallel workers", default=settings.workers, show_default=True)
    settings.max_ips_per_sni = IntPrompt.ask("  Max IPs per SNI", default=settings.max_ips_per_sni, show_default=True)
    UI_CONSOLE.print()

    # --- Step 3: advanced (skipped by default on first run) ---
    if first_run:
        configure_advanced = Confirm.ask(
            f"[bold {ACCENT}]Step 3 — Configure advanced settings[/] [{MUTED}](timeouts, probe URL)?[/]",
            default=False,
        )
    else:
        UI_CONSOLE.print(f"[bold {ACCENT}]Step 3 — Advanced[/]")
        configure_advanced = True

    if configure_advanced:
        settings.retries_per_pair = IntPrompt.ask("  Retries per SNI/IP pair", default=settings.retries_per_pair, show_default=True)
        settings.probe_url = Prompt.ask("  Probe URL", default=settings.probe_url, show_default=True).strip()
        settings.tls_insecure_compat = Confirm.ask(
            "  TLS insecure compatibility (skip TLS for broken certs)",
            default=bool(settings.tls_insecure_compat),
        )
        settings.snispf_ready_timeout_seconds = FloatPrompt.ask(
            "  SNISPF ready timeout (s)", default=float(settings.snispf_ready_timeout_seconds), show_default=True
        )
        settings.xray_ready_timeout_seconds = FloatPrompt.ask(
            "  Xray ready timeout (s)", default=float(settings.xray_ready_timeout_seconds), show_default=True
        )
        settings.probe_connect_timeout_seconds = FloatPrompt.ask(
            "  Probe connect timeout (s)", default=float(settings.probe_connect_timeout_seconds), show_default=True
        )
        settings.probe_read_timeout_seconds = FloatPrompt.ask(
            "  Probe read timeout (s)", default=float(settings.probe_read_timeout_seconds), show_default=True
        )
    UI_CONSOLE.print()

    # --- Summary ---
    section_rule("Setup Summary", style=OK_COLOR)
    UI_CONSOLE.print(f"  [{MUTED}]Proxy source:[/]    {settings.vless_source[:80]}{'...' if len(settings.vless_source) > 80 else ''}")
    UI_CONSOLE.print(f"  [{MUTED}]Workers:[/]         [bold]{settings.workers}[/]")
    UI_CONSOLE.print(f"  [{MUTED}]Max IPs/SNI:[/]     [bold]{settings.max_ips_per_sni}[/]")
    UI_CONSOLE.print(f"  [{MUTED}]Retries/pair:[/]    [bold]{settings.retries_per_pair}[/]")
    UI_CONSOLE.print(f"  [{MUTED}]Probe URL:[/]       {settings.probe_url}")
    UI_CONSOLE.print(f"  [{MUTED}]TLS compat:[/]      {'on' if settings.tls_insecure_compat else 'off'}")
    UI_CONSOLE.print()

    save_settings(settings)
    success(
        "Settings saved",
        f"Choose [bold]Start scan[/] from the menu, or run [bold]python scanner.py run[/]." if first_run else "Returning to menu.",
    )
    return settings


def _format_relative_time(ts_iso: str) -> str:
    try:
        ts = datetime.fromisoformat(ts_iso)
    except Exception:
        return "unknown"
    delta = datetime.now() - ts
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _load_last_summary() -> dict[str, Any] | None:
    latest = RESULTS_DIR / "latest.json"
    if not latest.exists():
        return None
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except Exception:
        return None


def _render_status_line(settings: ScanSettings) -> None:
    """Single status line above the menu — what's the current state."""
    parts: list[str] = []
    vless_configured = bool(getattr(settings, "vless_source", "").strip())
    parts.append(
        f"Proxy [{OK_COLOR}]configured[/{OK_COLOR}]"
        if vless_configured
        else f"Proxy [{WARN_COLOR}]not configured[/{WARN_COLOR}]"
    )
    parts.append(f"Workers [bold]{settings.workers}[/]")
    last = _load_last_summary()
    if last and isinstance(last, dict):
        summary = last.get("summary", {}) if isinstance(last.get("summary"), dict) else {}
        working = summary.get("working_pairs", 0)
        ts = summary.get("timestamp", "")
        if ts:
            color = OK_COLOR if working else MUTED
            parts.append(
                f"Last scan [{color}]{working} working[/{color}] "
                f"[{MUTED}]({_format_relative_time(str(ts))})[/{MUTED}]"
            )
    else:
        parts.append(f"[{MUTED}]No prior runs[/{MUTED}]")
    UI_CONSOLE.print("  " + "   ".join(parts))


def _render_menu(last_status: str, settings: ScanSettings) -> None:
    """Print the menu, clearing the terminal view on redraw to keep it clean."""
    clear_screen()
    section_rule("SNI-Finder")
    _render_status_line(settings)
    if last_status:
        UI_CONSOLE.print(f"  {last_status}")
    UI_CONSOLE.print()
    UI_CONSOLE.print(f"  [bold {ACCENT}]1[/]  Start scan         [{MUTED}](default — press Enter)[/{MUTED}]")
    UI_CONSOLE.print(f"  [bold {ACCENT}]2[/]  View last results")
    UI_CONSOLE.print(f"  [bold {ACCENT}]3[/]  Configure settings")
    UI_CONSOLE.print(f"  [bold {ACCENT}]4[/]  Resolve SNI+IP pairs only  [{MUTED}](advanced)[/{MUTED}]")
    UI_CONSOLE.print(f"  [bold {ACCENT}]q[/]  Quit")
    UI_CONSOLE.print()


def _action_start_scan(settings: ScanSettings) -> tuple[ScanSettings, str]:
    if not getattr(settings, "vless_source", "").strip():
        section_rule("First-Time Setup", style=WARN_COLOR)
        warn(
            "Proxy source not configured",
            "SNI-Finder needs a working VLESS or Trojan URI to scan with. Let's set one up now.",
        )
        UI_CONSOLE.print()
        settings = configure_interactive(settings, first_run=True)
        if not getattr(settings, "vless_source", "").strip():
            return settings, f"[{FAIL_COLOR}]Setup cancelled. Proxy source is required.[/{FAIL_COLOR}]"

    clear_screen()
    section_rule("Live Scan", style=ACCENT)
    shared.SCAN_ACTIVE = True
    try:
        exit_code = run_scan(settings, pause_on_exit=False)
    except KeyboardInterrupt:
        exit_code = 1
        UI_CONSOLE.print()
        warn("Stop requested", "Scan interrupted. Returning to menu.")
    finally:
        shared.SCAN_ACTIVE = False

    if exit_code == 0:
        msg = f"[{OK_COLOR}]Scan completed.[/{OK_COLOR}]"
        pause_terminal(True, "Press Enter to return to menu...")
    else:
        msg = f"[{WARN_COLOR}]Scan ended with errors or was interrupted.[/{WARN_COLOR}]"
        pause_terminal(True, "Press Enter to return to menu...")
    return settings, msg


def _action_view_results() -> str:
    clear_screen()
    section_rule("Last Scan Results")
    data = _load_last_summary()
    if not data:
        warn("No results yet", "Run a scan first to see results here.")
        pause_terminal(True, "Press Enter to return to menu...")
        return f"[{MUTED}]No prior results.[/{MUTED}]"

    summary = data.get("summary", {}) if isinstance(data.get("summary"), dict) else {}
    working = data.get("working_pairs", []) if isinstance(data.get("working_pairs"), list) else []
    for table in render_summary_tables(summary, str(RESULTS_DIR / "latest.json"), working):
        UI_CONSOLE.print(table)
    pause_terminal(True, "Press Enter to return to menu...")
    return f"[{OK_COLOR}]Showed last results.[/{OK_COLOR}]"


def _action_configure(settings: ScanSettings) -> tuple[ScanSettings, str]:
    settings = configure_interactive(settings)
    return settings, f"[{OK_COLOR}]Settings saved.[/{OK_COLOR}]"


def _action_resolve(settings: ScanSettings) -> str:
    clear_screen()
    section_rule("Resolve SNI+IP Pairs", style=ACCENT)
    snis, resolved_pairs, pairs, dropped_pairs = resolve_with_progress(settings.max_ips_per_sni)
    per_sni_counts: dict[str, int] = {}
    for pair in pairs:
        sni = str(pair.get("sni", ""))
        per_sni_counts[sni] = per_sni_counts.get(sni, 0) + 1

    UI_CONSOLE.print(render_plan_table(per_sni_counts))
    UI_CONSOLE.print()
    UI_CONSOLE.print(f"  [{MUTED}]Input SNIs:[/]       {len(snis)}")
    UI_CONSOLE.print(f"  [{MUTED}]Resolved pairs:[/]   {len(resolved_pairs)}")
    UI_CONSOLE.print(f"  [{MUTED}]Cloudflare pairs:[/] [{OK_COLOR}]{len(pairs)}[/{OK_COLOR}]")
    UI_CONSOLE.print(f"  [{MUTED}]Dropped (non-CF):[/] {dropped_pairs}")
    UI_CONSOLE.print(f"  [{MUTED}]Saved to:[/]         {RESULTS_DIR / 'resolved_pairs.json'}")
    UI_CONSOLE.print()
    pause_terminal(True, "Press Enter to return to menu...")
    return f"[{OK_COLOR}]Resolved {len(pairs)} CF pairs from {len(snis)} SNIs.[/{OK_COLOR}]"


def menu(settings: ScanSettings) -> int:
    last_status: str = ""
    while True:
        _render_menu(last_status, settings)

        try:
            UI_CONSOLE.print("Select [1/2/3/4/q] (Enter = 1): ", end="")
            raw = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            UI_CONSOLE.print()
            return 0

        choice = raw or "1"

        if choice in ("q", "quit", "exit", "5"):
            UI_CONSOLE.print(f"[{MUTED}]Goodbye.[/]")
            return 0

        if choice == "1":
            settings, last_status = _action_start_scan(settings)
        elif choice == "2":
            last_status = _action_view_results()
        elif choice == "3":
            settings, last_status = _action_configure(settings)
        elif choice == "4":
            last_status = _action_resolve(settings)
        else:
            last_status = f"[{FAIL_COLOR}]Invalid option — choose 1, 2, 3, 4, or q.[/{FAIL_COLOR}]"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SNI+IP scanner using SNISPF + Xray")
    parser.add_argument(
        "command",
        nargs="?",
        default="menu",
        choices=["menu", "configure", "onboarding", "resolve", "run"],
        help="Action",
    )
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
        import sys
        shutdown.set()
        GLOBAL_STOP.set()
        if not shared.SCAN_ACTIVE:
            print("\nExiting...")
            sys.exit(0)
        else:
            print("\nStop requested. Finishing active workers and cleaning up...")

    signal.signal(signal.SIGINT, _on_signal)

    if args.command == "configure":
        configure_interactive(settings, first_run=False)
        return 0

    if args.command == "onboarding":
        configure_interactive(settings, first_run=True)
        return 0

    if args.command == "resolve":
        section_rule("Resolve SNI+IP Pairs")
        snis, resolved_pairs, pairs, dropped_pairs = resolve_with_progress(settings.max_ips_per_sni)
        UI_CONSOLE.print()
        UI_CONSOLE.print(f"  [dim]Input SNIs:[/]       {len(snis)}")
        UI_CONSOLE.print(f"  [dim]Resolved pairs:[/]   {len(resolved_pairs)}")
        UI_CONSOLE.print(f"  [dim]Cloudflare pairs:[/] [{OK_COLOR}]{len(pairs)}[/{OK_COLOR}]")
        UI_CONSOLE.print(f"  [dim]Dropped (non-CF):[/] {dropped_pairs}")
        UI_CONSOLE.print(f"  [dim]Saved to:[/]         {RESULTS_DIR / 'resolved_pairs.json'}")
        return 0

    if args.command == "run":
        pause_on_exit = not args.no_pause_on_complete
        if os.name == "nt" and not is_elevated_windows():
            section_rule("Administrator Privileges Required", style=WARN_COLOR)
            warn(
                "Run as Administrator",
                "SNISPF uses raw packet injection (wrong_seq probing) which requires Windows admin rights.",
            )
            if args.uac_relaunched:
                error(
                    "Elevation failed",
                    "UAC relaunch did not produce an elevated process. Right-click start.bat and choose 'Run as administrator', "
                    "or launch from an elevated PowerShell.",
                )
                logging.error("UAC relaunch did not provide elevation")
                pause_terminal(not args.no_pause_on_error, "Press Enter to close...")
                return 1
            info("Requesting elevation via UAC...")
            logging.info("Requesting elevation via UAC")
            if relaunch_with_uac():
                return 0
            error(
                "UAC request denied or failed",
                "Please re-launch start.bat and accept the elevation prompt, or run from an elevated terminal.",
            )
            logging.error("UAC elevation request denied or failed")
            pause_terminal(not args.no_pause_on_error, "Press Enter to close...")
            return 1

        return run_scan(settings, pause_on_exit=pause_on_exit)

    return menu(settings)


if __name__ == "__main__":
    raise SystemExit(main())
