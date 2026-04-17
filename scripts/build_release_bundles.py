#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
RELEASE_DIR = ROOT / "release"
SNISPF_REPO = "NaxonM/snispf-core"
XRAY_REPO = "XTLS/Xray-core"


@dataclass
class ReleaseInfo:
    repo: str
    tag: str
    assets: dict[str, str]


def _api_get(url: str, token: str = "") -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "sni-finder-release-builder",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers)
    with urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download(url: str, dest: Path, token: str = "") -> None:
    headers = {"User-Agent": "sni-finder-release-builder"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers)
    with urlopen(req) as resp, dest.open("wb") as fh:
        shutil.copyfileobj(resp, fh)


def _latest_stable_release(repo: str, token: str = "") -> ReleaseInfo:
    releases = _api_get(f"https://api.github.com/repos/{repo}/releases", token=token)
    if not isinstance(releases, list):
        raise RuntimeError(f"Unexpected releases response for {repo}")

    for rel in releases:
        if rel.get("draft") or rel.get("prerelease"):
            continue
        tag = str(rel.get("tag_name", "")).strip()
        assets = {a.get("name", ""): a.get("browser_download_url", "") for a in rel.get("assets", [])}
        return ReleaseInfo(repo=repo, tag=tag, assets=assets)

    raise RuntimeError(f"No stable release found for {repo}")


def _pick_asset(info: ReleaseInfo, preferred_names: list[str], regex_fallback: str | None = None) -> tuple[str, str]:
    for name in preferred_names:
        url = info.assets.get(name)
        if url:
            return name, url

    if regex_fallback:
        pat = re.compile(regex_fallback)
        for name, url in info.assets.items():
            if pat.fullmatch(name):
                return name, url

    raise RuntimeError(f"Required asset not found in {info.repo}@{info.tag}: {preferred_names}")


def _read_xray_binary_from_zip(zip_path: Path, linux: bool) -> bytes:
    wanted = "xray" if linux else "xray.exe"
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if Path(name).name.lower() == wanted.lower():
                return zf.read(name)
    raise RuntimeError(f"Could not find {wanted} in {zip_path.name}")


def _copy_tree(src: Path, dst: Path) -> None:
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        if "__pycache__" in rel.parts:
            continue
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def _make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_bundle_readme(path: Path, snispf_tag: str, xray_tag: str, is_windows: bool) -> None:
    platform = "Windows" if is_windows else "Linux"
    launcher = "start.bat" if is_windows else "./start.sh"
    content = (
        "SNI-Finder bundled release\n\n"
        f"Platform: {platform}\n"
        f"SNISPF source: {SNISPF_REPO} ({snispf_tag})\n"
        f"Xray source: {XRAY_REPO} ({xray_tag})\n\n"
        "Quick start:\n"
        "1) Install Python 3.10+ and dependencies: pip install -r requirements.txt\n"
        f"2) Configure scanner settings: python scanner.py configure\n"
        f"3) Launch: {launcher}\n"
    )
    path.write_text(content, encoding="utf-8")


def _write_settings_template(path: Path) -> None:
    template = {
        "workers": 4,
        "max_ips_per_sni": 1,
        "probe_url": "https://www.google.com/generate_204",
        "snispf_ready_timeout_seconds": 10.0,
        "xray_ready_timeout_seconds": 10.0,
        "probe_connect_timeout_seconds": 8.0,
        "probe_read_timeout_seconds": 15.0,
        "retries_per_pair": 1,
        "vless_source": "",
    }
    path.write_text(json.dumps(template, indent=2), encoding="utf-8")


def build_release_bundles(output_dir: Path, token: str = "") -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    snispf = _latest_stable_release(SNISPF_REPO, token=token)
    xray = _latest_stable_release(XRAY_REPO, token=token)

    print(f"Using SNISPF release: {snispf.tag}")
    print(f"Using Xray release: {xray.tag}")

    snispf_win_name, snispf_win_url = _pick_asset(
        snispf,
        ["snispf_windows_amd64.exe"],
    )
    snispf_linux_name, snispf_linux_url = _pick_asset(
        snispf,
        ["snispf_linux_amd64"],
    )
    windivert_dll_name, windivert_dll_url = _pick_asset(
        snispf,
        ["WinDivert.dll"],
    )
    windivert_sys_name, windivert_sys_url = _pick_asset(
        snispf,
        ["WinDivert64.sys"],
    )

    xray_win_name, xray_win_url = _pick_asset(
        xray,
        ["Xray-windows-64.zip"],
        regex_fallback=r"Xray-windows-64\\.zip",
    )
    xray_linux_name, xray_linux_url = _pick_asset(
        xray,
        ["Xray-linux-64.zip"],
        regex_fallback=r"Xray-linux-64\\.zip",
    )

    with tempfile.TemporaryDirectory(prefix="sni-finder-release-") as td:
        tmp = Path(td)
        paths: dict[str, Path] = {}

        for key, name, url in [
            ("snispf_win", snispf_win_name, snispf_win_url),
            ("snispf_linux", snispf_linux_name, snispf_linux_url),
            ("windivert_dll", windivert_dll_name, windivert_dll_url),
            ("windivert_sys", windivert_sys_name, windivert_sys_url),
            ("xray_win_zip", xray_win_name, xray_win_url),
            ("xray_linux_zip", xray_linux_name, xray_linux_url),
        ]:
            dest = tmp / name
            print(f"Downloading {name}...")
            _download(url, dest, token=token)
            paths[key] = dest

        win_xray_bytes = _read_xray_binary_from_zip(paths["xray_win_zip"], linux=False)
        linux_xray_bytes = _read_xray_binary_from_zip(paths["xray_linux_zip"], linux=True)

        win_bundle = tmp / "sni-finder_windows_amd64_bundle"
        linux_bundle = tmp / "sni-finder_linux_amd64_bundle"

        for bundle in (win_bundle, linux_bundle):
            bundle.mkdir(parents=True, exist_ok=True)
            (bundle / "bin").mkdir(parents=True, exist_ok=True)
            (bundle / "config").mkdir(parents=True, exist_ok=True)

            for name in ["scanner.py", "requirements.txt", "README.md", "README_fa.md"]:
                src = ROOT / name
                if src.exists():
                    shutil.copy2(src, bundle / name)

            _copy_tree(ROOT / "sni_finder", bundle / "sni_finder")

            for cfg_name in ["cf_subnets.txt", "sni-list.txt"]:
                src = ROOT / "config" / cfg_name
                if src.exists():
                    shutil.copy2(src, bundle / "config" / cfg_name)

            _write_settings_template(bundle / "config" / "scanner_settings.json")

        win_launcher = ROOT / "start.bat"
        if win_launcher.exists():
            shutil.copy2(win_launcher, win_bundle / "start.bat")

        linux_launcher = ROOT / "start.sh"
        if linux_launcher.exists():
            shutil.copy2(linux_launcher, linux_bundle / "start.sh")

        shutil.copy2(paths["snispf_win"], win_bundle / "bin" / "snispf_windows_amd64.exe")
        shutil.copy2(paths["windivert_dll"], win_bundle / "bin" / "WinDivert.dll")
        shutil.copy2(paths["windivert_sys"], win_bundle / "bin" / "WinDivert64.sys")
        (win_bundle / "bin" / "xray.exe").write_bytes(win_xray_bytes)

        shutil.copy2(paths["snispf_linux"], linux_bundle / "bin" / "snispf_linux_amd64")
        (linux_bundle / "bin" / "xray").write_bytes(linux_xray_bytes)

        _make_executable(linux_bundle / "start.sh")
        _make_executable(linux_bundle / "bin" / "snispf_linux_amd64")
        _make_executable(linux_bundle / "bin" / "xray")

        _write_bundle_readme(win_bundle / "README_BUNDLE.txt", snispf.tag, xray.tag, is_windows=True)
        _write_bundle_readme(linux_bundle / "README_BUNDLE.txt", snispf.tag, xray.tag, is_windows=False)

        win_zip = output_dir / "sni-finder_windows_amd64_bundle.zip"
        linux_tgz = output_dir / "sni-finder_linux_amd64_bundle.tar.gz"

        if win_zip.exists():
            win_zip.unlink()
        if linux_tgz.exists():
            linux_tgz.unlink()

        base_name = str(output_dir / "sni-finder_windows_amd64_bundle")
        archive = shutil.make_archive(base_name, "zip", root_dir=tmp, base_dir=win_bundle.name)
        if Path(archive) != win_zip:
            Path(archive).replace(win_zip)

        with tarfile.open(linux_tgz, "w:gz") as tf:
            tf.add(linux_bundle, arcname=linux_bundle.name)

        checksums = output_dir / "checksums.txt"
        manifest = output_dir / "release_manifest.json"

        artifacts = [win_zip, linux_tgz]
        lines = []
        entries = []
        for art in artifacts:
            sha = _sha256(art)
            lines.append(f"{sha}  {art.name}")
            entries.append({
                "name": art.name,
                "sha256": sha,
                "bytes": art.stat().st_size,
            })

        checksums.write_text("\n".join(lines) + "\n", encoding="ascii")
        manifest.write_text(
            json.dumps(
                {
                    "project": "sni-finder",
                    "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "sources": {
                        "snispf": {"repo": SNISPF_REPO, "tag": snispf.tag},
                        "xray": {"repo": XRAY_REPO, "tag": xray.tag},
                    },
                    "artifacts": entries,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        print("Bundle artifacts:")
        for art in artifacts:
            print(f" - {art.name} ({art.stat().st_size} bytes)")
        print("Metadata:")
        print(f" - {checksums}")
        print(f" - {manifest}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build SNI-Finder Windows/Linux release bundles")
    parser.add_argument("--output-dir", default=str(RELEASE_DIR), help="Output directory for bundles")
    parser.add_argument("--github-token", default=os.environ.get("GITHUB_TOKEN", ""), help="GitHub token for API/downloads")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = Path(args.output_dir).resolve()
    build_release_bundles(out_dir, token=args.github_token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
