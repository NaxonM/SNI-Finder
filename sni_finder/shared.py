from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
RUNTIME_DIR = DATA_DIR / "runtime"
RESULTS_DIR = ROOT / "results"
LOGS_DIR = ROOT / "logs"
SCANNER_LOG_PATH = LOGS_DIR / "scanner.log"
SETTINGS_PATH = CONFIG_DIR / "scanner_settings.json"
SNI_LIST_PATH = CONFIG_DIR / "sni-list.txt"
CF_SUBNETS_PATH = CONFIG_DIR / "cf_subnets.txt"
DEFAULT_SNISPF_CONFIG = ROOT / "bin" / "config.json"


def _pick_first_existing(candidates: list[Path]) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _default_snispf_candidates() -> list[Path]:
    if os.name == "nt":
        return [
            ROOT / "bin" / "snispf_windows_amd64.exe",
        ]

    machine = (os.uname().machine if hasattr(os, "uname") else "").lower()
    linux_arch = "amd64"
    if machine in ("aarch64", "arm64"):
        linux_arch = "arm64"

    return [
        ROOT / "bin" / f"snispf_linux_{linux_arch}",
        ROOT / "bin" / "snispf_linux_amd64",
        ROOT / "bin" / "snispf_linux_arm64",
        ROOT / "bin" / "snispf",
    ]


def _default_xray_candidates() -> list[Path]:
    if os.name == "nt":
        return [
            ROOT / "bin" / "xray.exe",
        ]

    machine = (os.uname().machine if hasattr(os, "uname") else "").lower()
    linux_arch = "amd64"
    if machine in ("aarch64", "arm64"):
        linux_arch = "arm64"

    return [
        ROOT / "bin" / f"xray_linux_{linux_arch}",
        ROOT / "bin" / "xray",
    ]


def _read_tool_override(env_name: str) -> Path | None:
    value = os.environ.get(env_name, "").strip()
    if not value:
        return None
    return Path(value)


def get_snispf_bin_path() -> Path:
    override = _read_tool_override("SNI_FINDER_SNISPF_BIN")
    if override is not None:
        return override
    return _pick_first_existing(_default_snispf_candidates())


def get_xray_bin_path() -> Path:
    override = _read_tool_override("SNI_FINDER_XRAY_BIN")
    if override is not None:
        return override
    return _pick_first_existing(_default_xray_candidates())


SNISPF_BIN = get_snispf_bin_path()
XRAY_BIN = get_xray_bin_path()
SNISPF_BIND_HOST = "127.0.0.1"
SNISPF_START_PORT = 24000
XRAY_SOCKS_HOST = "127.0.0.1"
XRAY_SOCKS_START_PORT = 25000
PAIR_CONNECT_PORT = 443
PROBE_OK_STATUS_CODES = [200, 204, 301, 302, 403, 404]
GLOBAL_STOP = threading.Event()


@dataclass
class VlessProfile:
    uuid: str
    port: int
    path: str
    host: str
    sni: str
    security: str = "tls"
    network: str = "ws"
    flow: str = ""
    fp: str = "chrome"
    alpn: str = ""


@dataclass
class ScanSettings:
    workers: int = 4
    max_ips_per_sni: int = 1
    probe_url: str = "https://www.google.com/generate_204"
    # Conservative, per-phase timeout defaults.
    snispf_ready_timeout_seconds: float = 10.0
    xray_ready_timeout_seconds: float = 10.0
    probe_connect_timeout_seconds: float = 8.0
    probe_read_timeout_seconds: float = 15.0
    retries_per_pair: int = 1
    vless_source: str = ""

    def __post_init__(self) -> None:
        self.workers = max(1, int(self.workers))
        self.max_ips_per_sni = max(1, int(self.max_ips_per_sni))
        self.retries_per_pair = max(1, int(self.retries_per_pair))
        self.snispf_ready_timeout_seconds = max(1.0, float(self.snispf_ready_timeout_seconds))
        self.xray_ready_timeout_seconds = max(1.0, float(self.xray_ready_timeout_seconds))
        self.probe_connect_timeout_seconds = max(1.0, float(self.probe_connect_timeout_seconds))
        self.probe_read_timeout_seconds = max(1.0, float(self.probe_read_timeout_seconds))


def ensure_dirs(include_runtime_dirs: bool = False, include_logs_dir: bool = False) -> None:
    paths = [CONFIG_DIR, DATA_DIR]
    if include_runtime_dirs:
        paths.extend([RUNTIME_DIR, RESULTS_DIR])
    if include_logs_dir:
        paths.append(LOGS_DIR)

    for p in paths:
        p.mkdir(parents=True, exist_ok=True)


def setup_logging() -> None:
    ensure_dirs(include_runtime_dirs=True, include_logs_dir=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.FileHandler(SCANNER_LOG_PATH, encoding="utf-8")],
    )


def resolve_tool_path(path_str: str) -> Path:
    p = Path(path_str)
    if p.is_absolute():
        return p
    # Allow command-name overrides such as "xray" that should resolve via PATH.
    if p.parent == Path("."):
        resolved = shutil.which(path_str)
        if resolved:
            return Path(resolved)
    return (ROOT / p).resolve()


def is_elevated_windows() -> bool:
    if os.name != "nt":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_with_uac() -> bool:
    if os.name != "nt":
        return False

    script_path = str(ROOT / "scanner.py")
    params = subprocess.list2cmdline([script_path, *sys.argv[1:], "--uac-relaunched"])
    rc = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        sys.executable,
        params,
        str(ROOT),
        1,
    )
    return int(rc) > 32
