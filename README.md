# SNI-Finder Scanner

SNI-Finder scans SNI+IP candidate pairs by chaining three stages:

1. **SNISPF core** — runs in strict `wrong_seq` mode for each candidate pair.
2. **Xray core** — launches with a VLESS outbound pointed at the local SNISPF instance.
3. **HTTP probe** — sends a request over Xray's SOCKS interface to mark each pair as working or failed.

> Persian guide: [README_fa.md](README_fa.md)

---

## Quick Start (GitHub Releases)

The recommended way to get started is to use a pre-built release bundle.

**1. Download the bundle for your platform** from GitHub Releases:

| Platform | Asset |
|----------|-------|
| Windows  | `sni-finder_windows_amd64_bundle.zip` |
| Linux    | `sni-finder_linux_amd64_bundle.tar.gz` |

**2. Extract the archive** and open a terminal inside the extracted folder.

**3. Edit `config/sni-list.txt`** — add one SNI per line.

**4. Launch the scanner:**

*Windows (run as Administrator):*
```powershell
cd sni-finder_windows_amd64_bundle
.\start.bat
```

*Linux (requires elevated privileges):*
```bash
cd sni-finder_linux_amd64_bundle
chmod +x ./start.sh
sudo ./start.sh
```

**5. First-run setup:**
- The launcher checks for required Python packages and installs any that are missing.
- If `vless_source` is not configured, an interactive setup wizard starts automatically.

**6. Start scanning:**
- From the menu, select **Run Scan**, or
- Run directly: `python scanner.py run`

**7. Review results:**
- `results/latest.json`
- `results/<timestamp>/working_pairs.txt`
- `logs/scanner.log`

---

## Screenshots

![Results view](resources/SNI-Finder-01.png)
![Scan view](resources/SNI-Finder-02.png)
![Main menu](resources/SNI-Finder-03.png)

---

## Features

- Resolves the SNI list to IPv4 pairs.
- Filters pairs to Cloudflare subnets before scanning begins.
- Runs parallel workers with isolated SNISPF/Xray port assignments.
- Displays a live Rich dashboard with failure-reason breakdowns.
- Saves full run artifacts: summary, working/failed lists, and logs.

---

## Prerequisites

- **Python 3.10+**
- A valid **VLESS source**
- **SNISPF** and **Xray** binaries

**Windows:**
- Run an elevated PowerShell session (required for strict `wrong_seq` and WinDivert).
- Place the following files in `bin/`:
  - `snispf_windows_amd64.exe`
  - `xray.exe`
  - `WinDivert.dll`
  - `WinDivert64.sys`

**Linux:**
- Run with raw-packet privileges (root or `CAP_NET_RAW`).
- Place the following files in `bin/`:
  - `snispf_linux_amd64` (or `arm64` variant)
  - `xray`

**Optional binary overrides** (accept absolute path, project-relative path, or a command in `PATH`):

| Variable | Purpose |
|----------|---------|
| `SNI_FINDER_SNISPF_BIN` | Override SNISPF binary path |
| `SNI_FINDER_XRAY_BIN`   | Override Xray binary path   |

---

## Installation

**Windows:**
```powershell
cd SNI-Finder
pip install -r requirements.txt
```

**Linux:**
```bash
cd SNI-Finder
python3 -m pip install -r requirements.txt
```

---

## Configuration

Set `vless_source` using one of the following formats:

- A full `vless://...` URI
- Path to a text file containing a `vless://...` URI
- Path to an Xray JSON config file with a VLESS outbound

**Interactive configuration:**
```powershell
python scanner.py configure
```

Settings are stored in: `config/scanner_settings.json`

---

## Usage

| Method | Command |
|--------|---------|
| Launch script (Windows) | `start.bat` |
| Launch script (Linux) | `sudo ./start.sh` |
| Interactive menu | `python scanner.py` |
| Direct scan | `python scanner.py run` |
| Resolve-only | `python scanner.py resolve` |
| Override VLESS for one run | `python scanner.py run --vless "vless://..."` |

**Graceful stop:** Press `Ctrl+C` during a scan. Active workers will clean up their processes and release ports before exiting.

---

## Building Release Bundles

The bundle builder automatically fetches the latest stable releases of:
- **SNISPF** from `NaxonM/snispf-core`
- **Xray** from `XTLS/Xray-core`

**Windows:**
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_release_bundles.ps1
```

**Linux:**
```bash
bash ./scripts/build_release_bundles.sh
```

**Output files:**

| File | Description |
|------|-------------|
| `release/sni-finder_windows_amd64_bundle.zip` | Windows bundle |
| `release/sni-finder_linux_amd64_bundle.tar.gz` | Linux bundle |
| `release/checksums.txt` | File checksums |
| `release/release_manifest.json` | Release manifest |

---

## GitHub Actions Release (Recommended)

Use the included workflow to publish releases — do not commit generated bundle files directly.

**Workflow file:** `.github/workflows/release.yml`

| Trigger | Behavior |
|---------|----------|
| `workflow_dispatch` | Builds bundles for validation or testing |
| Tag push matching `v*` | Builds bundles and publishes them as GitHub Release assets |

**Example tag release:**
```bash
git tag v0.1.0
git push origin v0.1.0
```

---

## Runtime Outputs

| Path | Description |
|------|-------------|
| `results/latest.json` | Latest run results (symlink/copy) |
| `results/<timestamp>/summary.json` | Run summary |
| `results/<timestamp>/working_pairs.json` | Working pairs (JSON) |
| `results/<timestamp>/failed_pairs.json` | Failed pairs (JSON) |
| `results/<timestamp>/working_pairs.txt` | Working pairs (plain text) |
| `logs/scanner.log` | Full scanner log |

---

## Notes

- `config/cf_subnets.txt` is required and must be present before scanning.
- Pairs that fall outside known Cloudflare subnets are dropped before the scan begins.
