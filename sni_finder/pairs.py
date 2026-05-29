from __future__ import annotations

import ipaddress
import json
import logging
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from pathlib import Path
from typing import Callable
from typing import Any

from .shared import CF_SUBNETS_PATH, GLOBAL_STOP, RESULTS_DIR, SNI_LIST_PATH

# Number of parallel DNS resolver threads.
_DNS_WORKERS = 16
# Timeout in seconds for a single getaddrinfo call (generous for high-congestion/censored networks).
_DNS_TIMEOUT_SECONDS = 8.0


def load_sni_list(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"SNI list not found: {path}")

    out: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        sni = line.strip().lower()
        if not sni or sni.startswith("#"):
            continue
        if sni in seen:
            continue
        seen.add(sni)
        out.append(sni)
    return out


def _getaddrinfo_with_timeout(sni: str, timeout: float) -> list[tuple[Any, ...]]:
    """Run socket.getaddrinfo in a thread with a timeout.

    getaddrinfo is a blocking C call that ignores Python signals, so
    we run it in a daemon thread and abandon it if it takes too long.
    """
    result: list[tuple[Any, ...]] = []
    error: list[BaseException] = []

    def _resolve() -> None:
        try:
            result.extend(socket.getaddrinfo(sni, None, family=socket.AF_INET, type=socket.SOCK_STREAM))
        except Exception as exc:
            error.append(exc)

    t = threading.Thread(target=_resolve, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        # Thread is stuck in getaddrinfo — abandon it (daemon thread will
        # be cleaned up on process exit).
        return []
    if error:
        return []
    return result


def resolve_ips_for_sni(sni: str, max_ips: int) -> list[str]:
    ips: list[str] = []
    seen: set[str] = set()
    try:
        infos = _getaddrinfo_with_timeout(sni, _DNS_TIMEOUT_SECONDS)
    except socket.gaierror:
        return ips

    for item in infos:
        ip = item[4][0]
        try:
            if not ipaddress.ip_address(ip).is_global:
                continue
        except ValueError:
            continue
        if ip in seen:
            continue
        seen.add(ip)
        ips.append(ip)
        if len(ips) >= max_ips:
            break
    return ips


def extract_pairs(
    snis: list[str],
    max_ips_per_sni: int,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> list[dict[str, Any]]:
    """Resolve all SNIs to IP pairs using parallel DNS lookups.

    Respects GLOBAL_STOP — will abort early if the stop event is set.
    """
    pairs: list[dict[str, Any]] = []
    total = len(snis)
    completed_count = 0
    lock = threading.Lock()

    def _resolve_one(sni: str) -> list[dict[str, Any]]:
        if GLOBAL_STOP.is_set():
            return []
        ips = resolve_ips_for_sni(sni, max_ips_per_sni)
        if not ips:
            logging.warning("DNS resolution failed for SNI: %s (yielded 0 IPs, check DNS/censorship/reachability)", sni)
        return [{"sni": sni, "ip": ip} for ip in ips]

    with ThreadPoolExecutor(max_workers=min(_DNS_WORKERS, total or 1)) as executor:
        future_to_sni: dict[Future[list[dict[str, Any]]], str] = {}
        for sni in snis:
            if GLOBAL_STOP.is_set():
                break
            future_to_sni[executor.submit(_resolve_one, sni)] = sni

        for future in as_completed(future_to_sni):
            if GLOBAL_STOP.is_set():
                # Cancel remaining futures.
                for f in future_to_sni:
                    f.cancel()
                break

            sni = future_to_sni[future]
            try:
                result = future.result(timeout=_DNS_TIMEOUT_SECONDS + 1)
                with lock:
                    pairs.extend(result)
                    completed_count += 1
                    if progress_cb is not None:
                        progress_cb(completed_count, total, sni)
            except Exception:
                with lock:
                    completed_count += 1
                    if progress_cb is not None:
                        progress_cb(completed_count, total, sni)

    return pairs


def resolve_pairs_from_sni_list(
    max_ips_per_sni: int,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> tuple[list[str], list[dict[str, str]]]:
    snis = load_sni_list(SNI_LIST_PATH)
    pairs = extract_pairs(snis, max_ips_per_sni, progress_cb=progress_cb)
    return snis, pairs


def load_cf_subnets(path: Path = CF_SUBNETS_PATH) -> list[ipaddress.IPv4Network]:
    if not path.exists():
        raise FileNotFoundError(f"Cloudflare subnet list not found: {path}")

    subnets: list[ipaddress.IPv4Network] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        net = ipaddress.ip_network(raw, strict=False)
        if isinstance(net, ipaddress.IPv4Network):
            subnets.append(net)
    if not subnets:
        raise ValueError(f"No IPv4 subnets found in {path}")
    return subnets


def filter_pairs_by_subnets(
    pairs: list[dict[str, str]], subnets: list[ipaddress.IPv4Network]
) -> tuple[list[dict[str, str]], int]:
    kept: list[dict[str, str]] = []
    dropped = 0
    for pair in pairs:
        ip = ipaddress.ip_address(pair["ip"])
        if any(ip in subnet for subnet in subnets):
            kept.append(pair)
        else:
            dropped += 1
    return kept, dropped


def save_resolved_pairs(pairs: list[dict[str, str]]) -> None:
    (RESULTS_DIR / "resolved_pairs.json").write_text(json.dumps(pairs, indent=2), encoding="utf-8")


def build_pair_list(max_ips_per_sni: int) -> list[dict[str, str]]:
    _, pairs = resolve_pairs_from_sni_list(max_ips_per_sni)
    cf_subnets = load_cf_subnets()
    filtered_pairs, _ = filter_pairs_by_subnets(pairs, cf_subnets)
    save_resolved_pairs(filtered_pairs)
    return filtered_pairs
