from __future__ import annotations

import json
import logging
import os
import queue
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests
from rich.live import Live
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn

from .pairs import filter_pairs_by_subnets, load_cf_subnets, resolve_pairs_from_sni_list, save_resolved_pairs
from .profile import load_vless_profile
from .shared import (
    CF_SUBNETS_PATH,
    DEFAULT_SNISPF_CONFIG,
    GLOBAL_STOP,
    LOGS_DIR,
    PAIR_CONNECT_PORT,
    PROBE_OK_STATUS_CODES,
    RESULTS_DIR,
    RUNTIME_DIR,
    SCANNER_LOG_PATH,
    SNISPF_BIN,
    SNISPF_BIND_HOST,
    SNISPF_START_PORT,
    SNI_LIST_PATH,
    ScanSettings,
    VlessProfile,
    XRAY_BIN,
    XRAY_SOCKS_HOST,
    XRAY_SOCKS_START_PORT,
    is_elevated_windows,
    resolve_tool_path,
)
from .ui import (
    UI_CONSOLE,
    ScanSnapshot,
    build_dashboard,
    pause_terminal,
    phase,
    render_plan_table,
    render_summary_tables,
)


def tail_text(path: Path, max_lines: int = 30) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(lines[-max_lines:])


def is_port_open(host: str, port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_port(host: str, port: int, timeout: float, stop_event: Optional[threading.Event] = None) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if stop_event is not None and stop_event.is_set():
            return False
        if is_port_open(host, port):
            return True
        time.sleep(0.05)
    return False


def kill_process(proc: Optional[subprocess.Popen]) -> None:
    if proc is None:
        return
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
            proc.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass


def load_base_snispf_config() -> dict[str, Any]:
    if DEFAULT_SNISPF_CONFIG.exists():
        return json.loads(DEFAULT_SNISPF_CONFIG.read_text(encoding="utf-8"))
    return {
        "LISTEN_HOST": "127.0.0.1",
        "LISTEN_PORT": 40443,
        "CONNECT_IP": "1.1.1.1",
        "CONNECT_PORT": 443,
        "FAKE_SNI": "example.com",
        "BYPASS_METHOD": "wrong_seq",
        "FRAGMENT_STRATEGY": "sni_split",
        "FRAGMENT_DELAY": 0.05,
        "USE_TTL_TRICK": False,
        "FAKE_SNI_METHOD": "raw_inject",
        "WRONG_SEQ_CONFIRM_TIMEOUT_MS": 2000,
        "ENDPOINTS": [],
        "LOAD_BALANCE": "failover",
        "ENDPOINT_PROBE": False,
        "AUTO_FAILOVER": False,
        "FAILOVER_RETRIES": 0,
        "PROBE_TIMEOUT_MS": 2500,
    }


def build_snispf_config(base: dict[str, Any], pair: dict[str, str], listen_host: str, listen_port: int) -> dict[str, Any]:
    cfg = json.loads(json.dumps(base))
    cfg["LISTEN_HOST"] = listen_host
    cfg["LISTEN_PORT"] = listen_port
    cfg["CONNECT_IP"] = pair["ip"]
    cfg["CONNECT_PORT"] = int(base.get("CONNECT_PORT", 443))
    cfg["FAKE_SNI"] = pair["sni"]
    cfg["BYPASS_METHOD"] = "wrong_seq"
    cfg["FAKE_SNI_METHOD"] = "raw_inject"
    cfg["WRONG_SEQ_CONFIRM_TIMEOUT_MS"] = int(base.get("WRONG_SEQ_CONFIRM_TIMEOUT_MS", 2000))
    cfg["ENDPOINTS"] = [
        {
            "NAME": "strict-primary",
            "IP": pair["ip"],
            "PORT": int(base.get("CONNECT_PORT", 443)),
            "SNI": pair["sni"],
            "ENABLED": True,
        }
    ]
    cfg["ENDPOINT_PROBE"] = False
    cfg["AUTO_FAILOVER"] = False
    cfg["FAILOVER_RETRIES"] = 0
    return cfg


def build_xray_config(
    profile: VlessProfile,
    pair: dict[str, str],
    snispf_host: str,
    snispf_port: int,
    socks_host: str,
    socks_port: int,
    override_sni: bool,
    override_host: bool,
) -> dict[str, Any]:
    stream: dict[str, Any] = {
        "network": profile.network,
        "security": profile.security,
        "tlsSettings": {
            "serverName": pair["sni"] if override_sni else profile.sni,
            "allowInsecure": True,
            "fingerprint": profile.fp or "chrome",
        },
    }
    if profile.alpn:
        stream["tlsSettings"]["alpn"] = [x.strip() for x in profile.alpn.split(",") if x.strip()]

    if profile.network == "ws":
        stream["wsSettings"] = {
            "path": profile.path,
            "headers": {"Host": pair["sni"] if override_host else profile.host},
        }
    elif profile.network == "grpc":
        stream["grpcSettings"] = {
            "serviceName": profile.path.lstrip("/"),
            "multiMode": False,
        }

    user: dict[str, Any] = {"id": profile.uuid, "encryption": "none"}
    if profile.flow:
        user["flow"] = profile.flow

    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "socks-in",
                "listen": socks_host,
                "port": socks_port,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": False},
            }
        ],
        "outbounds": [
            {
                "tag": "proxy",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": snispf_host,
                            "port": snispf_port,
                            "users": [user],
                        }
                    ]
                },
                "streamSettings": stream,
            }
        ],
    }


def probe_via_socks(
    socks_host: str,
    socks_port: int,
    url: str,
    connect_timeout: float,
    read_timeout: float,
    ok_codes: list[int],
) -> tuple[bool, str, Optional[float]]:
    session = requests.Session()
    proxy = f"socks5h://{socks_host}:{socks_port}"
    session.proxies = {"http": proxy, "https": proxy}
    session.headers.update({"User-Agent": "SNI-Finder/1.0"})
    t0 = time.perf_counter()
    try:
        resp = session.get(url, timeout=(connect_timeout, read_timeout), allow_redirects=False)
        if resp.status_code in ok_codes:
            return True, f"http_{resp.status_code}", (time.perf_counter() - t0) * 1000.0
        return False, f"http_{resp.status_code}", None
    except requests.exceptions.ConnectTimeout:
        return False, "connect_timeout", None
    except requests.exceptions.ReadTimeout:
        return False, "read_timeout", None
    except requests.exceptions.ProxyError:
        return False, "proxy_error", None
    except requests.exceptions.SSLError:
        return False, "tls_error", None
    except requests.exceptions.ConnectionError:
        return False, "conn_error", None
    except requests.exceptions.RequestException:
        return False, "request_error", None
    finally:
        session.close()


def run_pair(
    pair: dict[str, str],
    worker_id: int,
    settings: ScanSettings,
    profile: VlessProfile,
    base_snispf_cfg: dict[str, Any],
    stop_event: threading.Event,
) -> dict[str, Any]:
    snispf_port = SNISPF_START_PORT + worker_id
    socks_port = XRAY_SOCKS_START_PORT + worker_id
    runtime_path = RUNTIME_DIR / f"worker-{worker_id}"
    runtime_path.mkdir(parents=True, exist_ok=True)

    pair_key = f"{pair['sni'].replace('.', '_')}__{pair['ip'].replace('.', '_')}"
    snispf_cfg_path = runtime_path / f"snispf_{pair_key}.json"
    xray_cfg_path = runtime_path / f"xray_{pair_key}.json"
    snispf_log_path = LOGS_DIR / f"snispf_w{worker_id}.log"
    xray_log_path = LOGS_DIR / f"xray_w{worker_id}.log"

    base_for_pair = json.loads(json.dumps(base_snispf_cfg))
    base_for_pair["CONNECT_PORT"] = PAIR_CONNECT_PORT
    snispf_cfg = build_snispf_config(base_for_pair, pair, SNISPF_BIND_HOST, snispf_port)
    xray_cfg = build_xray_config(
        profile,
        pair,
        SNISPF_BIND_HOST,
        snispf_port,
        XRAY_SOCKS_HOST,
        socks_port,
        False,
        False,
    )
    snispf_cfg_path.write_text(json.dumps(snispf_cfg, indent=2), encoding="utf-8")
    xray_cfg_path.write_text(json.dumps(xray_cfg, indent=2), encoding="utf-8")

    creationflags = 0x08000000 if os.name == "nt" else 0

    snispf_proc: Optional[subprocess.Popen] = None
    xray_proc: Optional[subprocess.Popen] = None
    result: dict[str, Any] = {
        "ok": False,
        "reason": "unknown",
        "pair": pair,
        "worker": worker_id,
        "snispf_log": str(snispf_log_path),
        "xray_log": str(xray_log_path),
        "snispf_config": str(snispf_cfg_path),
        "xray_config": str(xray_cfg_path),
    }

    try:
        if stop_event.is_set():
            result["reason"] = "stopped"
            return result

        with open(snispf_log_path, "a", encoding="utf-8") as snispf_log, open(xray_log_path, "a", encoding="utf-8") as xray_log:
            snispf_proc = subprocess.Popen(
                [str(resolve_tool_path(str(SNISPF_BIN))), "--config", str(snispf_cfg_path)],
                stdout=snispf_log,
                stderr=snispf_log,
                creationflags=creationflags,
            )
            if not wait_port(SNISPF_BIND_HOST, snispf_port, settings.snispf_ready_timeout_seconds, stop_event):
                result["reason"] = "snispf_not_ready"
                result["snispf_pid"] = snispf_proc.pid if snispf_proc else None
                result["snispf_log_tail"] = tail_text(snispf_log_path)
                return result

            xray_proc = subprocess.Popen(
                [str(resolve_tool_path(str(XRAY_BIN))), "run", "-c", str(xray_cfg_path)],
                stdout=xray_log,
                stderr=xray_log,
                creationflags=creationflags,
            )
            if not wait_port(XRAY_SOCKS_HOST, socks_port, settings.xray_ready_timeout_seconds, stop_event):
                result["reason"] = "xray_not_ready"
                result["snispf_pid"] = snispf_proc.pid if snispf_proc else None
                result["xray_pid"] = xray_proc.pid if xray_proc else None
                result["snispf_log_tail"] = tail_text(snispf_log_path)
                result["xray_log_tail"] = tail_text(xray_log_path)
                return result

            reason = "unknown"
            for attempt in range(max(1, settings.retries_per_pair)):
                if stop_event.is_set():
                    result["reason"] = "stopped"
                    return result

                ok, reason, latency = probe_via_socks(
                    XRAY_SOCKS_HOST,
                    socks_port,
                    settings.probe_url,
                    float(settings.probe_connect_timeout_seconds),
                    float(settings.probe_read_timeout_seconds),
                    PROBE_OK_STATUS_CODES,
                )
                if ok:
                    return {
                        "ok": True,
                        "reason": reason,
                        "latency_ms": round(latency or 0, 2),
                        "pair": pair,
                        "worker": worker_id,
                        "attempt": attempt + 1,
                        "snispf_log": str(snispf_log_path),
                        "xray_log": str(xray_log_path),
                    }

            result["reason"] = reason
            result["snispf_pid"] = snispf_proc.pid if snispf_proc else None
            result["xray_pid"] = xray_proc.pid if xray_proc else None
            result["snispf_log_tail"] = tail_text(snispf_log_path)
            result["xray_log_tail"] = tail_text(xray_log_path)
            return result
    except Exception as exc:
        result["reason"] = f"exception:{type(exc).__name__}"
        result["error"] = str(exc)
        result["snispf_log_tail"] = tail_text(snispf_log_path)
        result["xray_log_tail"] = tail_text(xray_log_path)
        logging.exception("worker=%s pair=%s/%s run_pair crashed", worker_id, pair.get("sni"), pair.get("ip"))
        return result
    finally:
        kill_process(xray_proc)
        kill_process(snispf_proc)


class ScanController:
    def __init__(self, settings: ScanSettings, profile: VlessProfile, pairs: list[dict[str, str]]) -> None:
        self.settings = settings
        self.profile = profile
        self.pairs = pairs
        self.base_snispf_cfg = load_base_snispf_config()
        self.q: queue.Queue[dict[str, str]] = queue.Queue()
        self.stop_event = GLOBAL_STOP
        self.working: list[dict[str, Any]] = []
        self.failed: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.started_at = time.time()
        self.last_event = "waiting"
        self.processed = 0
        self.reason_counts: dict[str, int] = {}
        self.worker_states: dict[int, str] = {i: "idle" for i in range(self.settings.workers)}
        self.successful_snis: set[str] = set()
        self.total_snis = len({pair["sni"] for pair in self.pairs})
        self.state = "running"

    def _snapshot(self) -> ScanSnapshot:
        with self.lock:
            elapsed = time.time() - self.started_at
            return ScanSnapshot(
                total_pairs=len(self.pairs),
                processed_pairs=self.processed,
                ok_pairs=len(self.working),
                failed_pairs=len(self.failed),
                total_snis=self.total_snis,
                ok_snis=len(self.successful_snis),
                state=self.state,
                elapsed_seconds=elapsed,
                last_event=self.last_event,
                worker_states=dict(self.worker_states),
                reason_counts=dict(self.reason_counts),
            )

    def _progress_loop(self) -> None:
        with Live(build_dashboard(self._snapshot()), console=UI_CONSOLE, refresh_per_second=4, transient=False) as live:
            while True:
                live.update(build_dashboard(self._snapshot()))
                time.sleep(0.25)
                with self.lock:
                    done = self.processed >= len(self.pairs)
                    stopping = self.stop_event.is_set() and self.state == "stopping"
                if done or stopping:
                    break

            with self.lock:
                if self.state != "stopping":
                    self.state = "completed"
            live.update(build_dashboard(self._snapshot()))

    def run(self) -> dict[str, Any]:
        for pair in self.pairs:
            self.q.put(pair)

        progress_thread = threading.Thread(target=self._progress_loop, daemon=True)
        progress_thread.start()

        def worker_loop(worker_id: int) -> None:
            while True:
                if self.stop_event.is_set():
                    return
                try:
                    pair = self.q.get(timeout=0.2)
                except queue.Empty:
                    if self.q.empty():
                        return
                    continue

                with self.lock:
                    self.worker_states[worker_id] = f"testing {pair['sni']} / {pair['ip']}"

                result = run_pair(pair, worker_id, self.settings, self.profile, self.base_snispf_cfg, self.stop_event)

                with self.lock:
                    if result.get("ok"):
                        self.working.append(result)
                        self.successful_snis.add(pair["sni"])
                        self.last_event = f"OK {pair['sni']} / {pair['ip']} ({result.get('latency_ms')}ms)"
                        logging.info("OK w=%s %s %s %.2fms", worker_id, pair["sni"], pair["ip"], float(result.get("latency_ms", 0)))
                    else:
                        self.failed.append(result)
                        reason = str(result.get("reason", "unknown"))
                        self.reason_counts[reason] = self.reason_counts.get(reason, 0) + 1
                        self.last_event = f"FAIL {pair['sni']} / {pair['ip']} ({reason})"
                        logging.warning(
                            "FAIL w=%s %s %s reason=%s snispf_log=%s",
                            worker_id,
                            pair["sni"],
                            pair["ip"],
                            reason,
                            result.get("snispf_log"),
                        )
                    self.worker_states[worker_id] = "idle"
                    self.processed += 1
                self.q.task_done()

        threads = [threading.Thread(target=worker_loop, args=(i,), daemon=True) for i in range(self.settings.workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        with self.lock:
            if self.state != "stopping":
                self.state = "completed"
        progress_thread.join(timeout=2)

        failed_snis = self.total_snis - len(self.successful_snis)
        return {
            "timestamp": datetime.now().isoformat(),
            "total_snis": self.total_snis,
            "successful_snis": len(self.successful_snis),
            "failed_snis": failed_snis,
            "total_pairs": len(self.pairs),
            "working_pairs": len(self.working),
            "failed_pairs": len(self.failed),
            "workers": self.settings.workers,
            "probe_url": self.settings.probe_url,
            "state": self.state,
            "failure_reasons": dict(self.reason_counts),
        }


def write_results(summary: dict[str, Any], working: list[dict[str, Any]], failed: list[dict[str, Any]]) -> None:
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    result_dir = RESULTS_DIR / ts
    result_dir.mkdir(parents=True, exist_ok=True)

    (result_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (result_dir / "working_pairs.json").write_text(json.dumps(working, indent=2), encoding="utf-8")
    (result_dir / "failed_pairs.json").write_text(json.dumps(failed, indent=2), encoding="utf-8")

    lines: list[str] = []
    for item in working:
        pair = item["pair"]
        lines.append(f"{pair['sni']} {pair['ip']} latency_ms={item.get('latency_ms', 'n/a')}")
    (result_dir / "working_pairs.txt").write_text("\n".join(lines), encoding="utf-8")

    latest = {"summary": summary, "working_pairs": working, "failed_pairs": failed}
    (RESULTS_DIR / "latest.json").write_text(json.dumps(latest, indent=2), encoding="utf-8")


def validate_files(settings: ScanSettings) -> None:
    snispf_path = resolve_tool_path(str(SNISPF_BIN))
    xray_path = resolve_tool_path(str(XRAY_BIN))

    if not snispf_path.exists():
        raise FileNotFoundError(
            "snispf binary not found: "
            f"{snispf_path}. "
            "Set SNI_FINDER_SNISPF_BIN to an absolute path, a relative path under project root, or a command in PATH."
        )
    if not xray_path.exists():
        raise FileNotFoundError(
            "xray binary not found: "
            f"{xray_path}. "
            "Set SNI_FINDER_XRAY_BIN to an absolute path, a relative path under project root, or a command in PATH."
        )

    if os.name != "nt":
        # Make local binaries executable on Linux/macOS when copied without +x bit.
        for tool in (snispf_path, xray_path):
            if not os.access(tool, os.X_OK):
                mode = tool.stat().st_mode
                tool.chmod(mode | 0o111)

    if not SNI_LIST_PATH.exists():
        raise FileNotFoundError(f"SNI list not found: {SNI_LIST_PATH}")
    if not CF_SUBNETS_PATH.exists():
        raise FileNotFoundError(f"Cloudflare subnet list not found: {CF_SUBNETS_PATH}")


def _build_plan(pairs: list[dict[str, str]]) -> dict[str, int]:
    per_sni_counts: dict[str, int] = {}
    for pair in pairs:
        sni = pair["sni"]
        per_sni_counts[sni] = per_sni_counts.get(sni, 0) + 1
    return per_sni_counts


def run_scan(settings: ScanSettings, pause_on_exit: bool = True) -> int:
    GLOBAL_STOP.clear()
    phase("Step 1/6", "Validating scanner prerequisites")
    if os.name == "nt" and not is_elevated_windows():
        print("Scanner requires Administrator privileges on Windows for SNISPF wrong_seq probing.")
        logging.error("run blocked: non-elevated process")
        print(f"See scanner log: {SCANNER_LOG_PATH}")
        pause_terminal(pause_on_exit, "Press Enter to close...")
        return 1

    try:
        validate_files(settings)
        phase("Step 2/6", "Loading VLESS profile")
        profile = load_vless_profile(settings.vless_source)
        phase("Step 3/6", "Resolving SNI list to SNI+IP pairs")
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

            _, resolved_pairs = resolve_pairs_from_sni_list(settings.max_ips_per_sni, progress_cb=_resolve_progress)
        phase("Step 4/6", "Filtering pairs by Cloudflare subnets")
        cf_subnets = load_cf_subnets()
        pairs, dropped_pairs = filter_pairs_by_subnets(resolved_pairs, cf_subnets)
        save_resolved_pairs(pairs)
    except Exception as exc:
        logging.exception("scan setup failed")
        print(f"Scan setup failed: {exc}")
        if isinstance(exc, ValueError) and "vless_source is empty" in str(exc):
            print("vless_source is empty. Set it with one of these:")
            print("  1) python scanner.py configure")
            print("  2) python scanner.py run --vless \"vless://...\"")
            print("  3) Put URI path into config/scanner_settings.json -> vless_source")
        print(f"See scanner log: {SCANNER_LOG_PATH}")
        pause_terminal(pause_on_exit, "Press Enter to close...")
        return 1

    if not pairs:
        print("No Cloudflare-matching SNI+IP pairs remain after subnet filtering.")
        pause_terminal(pause_on_exit, "Press Enter to close...")
        return 1

    per_sni_counts = _build_plan(pairs)
    UI_CONSOLE.print(render_plan_table(per_sni_counts))
    UI_CONSOLE.print(
        f"[dim]Plan:[/] {len(per_sni_counts)} SNIs, {len(pairs)} Cloudflare pairs (dropped {dropped_pairs} non-Cloudflare), max_ips_per_sni={settings.max_ips_per_sni}"
    )
    UI_CONSOLE.print(f"[dim]Source list:[/] {SNI_LIST_PATH}")
    UI_CONSOLE.print(f"[dim]CF subnets:[/] {CF_SUBNETS_PATH}")

    phase("Step 5/6", "Running worker scan (Ctrl+C for graceful stop)")
    controller = ScanController(settings, profile, pairs)

    try:
        summary = controller.run()
    except KeyboardInterrupt:
        with controller.lock:
            controller.state = "stopping"
        GLOBAL_STOP.set()
        summary = {
            "timestamp": datetime.now().isoformat(),
            "total_snis": len(per_sni_counts),
            "successful_snis": len(controller.successful_snis),
            "failed_snis": max(0, len(per_sni_counts) - len(controller.successful_snis)),
            "total_pairs": len(pairs),
            "working_pairs": len(controller.working),
            "failed_pairs": len(controller.failed),
            "workers": settings.workers,
            "probe_url": settings.probe_url,
            "state": "stopping",
            "runtime_error": "interrupted",
            "failure_reasons": dict(controller.reason_counts),
        }
    except Exception as exc:
        logging.exception("scan runtime failed")
        summary = {
            "timestamp": datetime.now().isoformat(),
            "total_snis": len(per_sni_counts),
            "successful_snis": len(controller.successful_snis),
            "failed_snis": max(0, len(per_sni_counts) - len(controller.successful_snis)),
            "total_pairs": len(pairs),
            "working_pairs": len(controller.working),
            "failed_pairs": len(controller.failed),
            "workers": settings.workers,
            "probe_url": settings.probe_url,
            "state": "error",
            "runtime_error": str(exc),
            "failure_reasons": dict(controller.reason_counts),
        }

    phase("Step 6/6", "Saving results")
    write_results(summary, controller.working, controller.failed)

    for table in render_summary_tables(summary, str(RESULTS_DIR / "latest.json"), controller.working):
        UI_CONSOLE.print(table)

    if summary.get("runtime_error"):
        UI_CONSOLE.print(f"[red]Runtime error:[/] {summary['runtime_error']}")
        UI_CONSOLE.print(f"[yellow]See log:[/] {SCANNER_LOG_PATH}")
        pause_terminal(pause_on_exit, "Press Enter to close...")
        return 1

    pause_terminal(pause_on_exit, "Scan complete. Press Enter to close...")
    return 0
