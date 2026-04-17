# SNI-Finder Scanner

SNI-Finder scans SNI+IP candidates by chaining:

1. SNISPF core in strict `wrong_seq` mode per candidate pair.
2. Xray core with a VLESS outbound pointed to local SNISPF.
3. HTTP probe over Xray SOCKS to mark each pair as working or failed.

Persian guide: [README_fa.md](README_fa.md)

## Quick Start From GitHub Releases (Recommended)

Use this flow if you want to scan SNI candidates with released bundles.

1. Download the latest release asset for your OS from GitHub Releases:
	- Windows: `sni-finder_windows_amd64_bundle.zip`
	- Linux: `sni-finder_linux_amd64_bundle.tar.gz`
2. Extract the archive and open a terminal in the extracted folder.
3. Edit `config/sni-list.txt` and put one SNI per line.
4. Launch the scanner:

Windows (run as Administrator):

```powershell
cd sni-finder_windows_amd64_bundle
.\start.bat
```

Linux (run with required privileges):

```bash
cd sni-finder_linux_amd64_bundle
chmod +x ./start.sh
sudo ./start.sh
```

5. On first run:
	- The launcher checks required Python packages and tries to install missing ones.
	- If `vless_source` is empty, interactive setup starts automatically.
6. Start scanning:
	- Use menu option `Run Scan`, or
	- Run directly: `python scanner.py run`
7. Read results in:
	- `results/latest.json`
	- `results/<timestamp>/working_pairs.txt`
	- `logs/scanner.log`

## Screenshots

![SNI-Finder results view](resources/SNI-Finder-01.png)

![SNI-Finder scan view](resources/SNI-Finder-02.png)

![SNI-Finder main menu](resources/SNI-Finder-03.png)

## Features

- Resolves SNI list to IPv4 pairs.
- Filters pairs to Cloudflare subnets before scanning.
- Runs parallel workers with isolated SNISPF/Xray ports.
- Shows live Rich dashboard and failure reason breakdown.
- Saves full run artifacts (summary, working/failed lists, logs).

## Prerequisites

- Python 3.10+
- A valid VLESS source
- SNISPF + Xray binaries

Windows:

- Run elevated PowerShell for strict `wrong_seq` + WinDivert.
- Use `bin/snispf_windows_amd64.exe`, `bin/xray.exe`, `bin/WinDivert.dll`, `bin/WinDivert64.sys`.

Linux:

- Run with required raw packet privileges for strict `wrong_seq` (root or `CAP_NET_RAW`).
- Use `bin/snispf_linux_amd64` (or arm64) and `bin/xray`.

Optional explicit overrides:

- `SNI_FINDER_SNISPF_BIN`
- `SNI_FINDER_XRAY_BIN`

Overrides accept absolute path, project-relative path, or command in PATH.

## Install

```powershell
cd SNI-Finder
pip install -r requirements.txt
```

Linux shell:

```bash
cd SNI-Finder
python3 -m pip install -r requirements.txt
```

## Configure

Set `vless_source` by one of:

- Full `vless://...` URI
- Path to text file containing a `vless://...` URI
- Path to Xray JSON with a VLESS outbound

Interactive configure:

```powershell
python scanner.py configure
```

Settings file: `config/scanner_settings.json`

## Run

Quick run with launch scripts:

- Windows: `start.bat`
- Linux: `sudo ./start.sh`

Menu mode:

```powershell
python scanner.py
```

Direct scan:

```powershell
python scanner.py run
```

Resolve-only mode:

```powershell
python scanner.py resolve
```

Override VLESS source for a single run:

```powershell
python scanner.py run --vless "vless://..."
```

Graceful stop:

- Press `Ctrl+C` during scan.
- Active workers clean up processes and ports before exit.

## Build Publish Bundles (Windows + Linux)

This project includes automated bundle builder that fetches:

- Latest stable SNISPF release from `NaxonM/snispf-core`
- Latest stable Xray release from `XTLS/Xray-core`

Build bundles:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_release_bundles.ps1
```

Linux shell:

```bash
bash ./scripts/build_release_bundles.sh
```

Release outputs:

- `release/sni-finder_windows_amd64_bundle.zip`
- `release/sni-finder_linux_amd64_bundle.tar.gz`
- `release/checksums.txt`
- `release/release_manifest.json`

## GitHub Actions Release (Recommended)

Use workflow-based releases instead of committing generated files.

Workflow file: `.github/workflows/release.yml`

- `workflow_dispatch`: build bundles for validation/testing.
- Tag push (`v*`): build bundles and publish GitHub Release assets automatically.

Tag release example:

```bash
git tag v0.1.0
git push origin v0.1.0
```

## Runtime Outputs

- `results/latest.json`
- `results/<timestamp>/summary.json`
- `results/<timestamp>/working_pairs.json`
- `results/<timestamp>/failed_pairs.json`
- `results/<timestamp>/working_pairs.txt`
- `logs/scanner.log`

## Notes

- `config/cf_subnets.txt` is required.
- Non-Cloudflare pairs are dropped before scan starts.
