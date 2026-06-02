# Changelog

All notable changes to SNI-Finder are documented in this file.

## [v0.1.6] - 2026-06-02

### Added
- Trojan proxy URI support alongside VLESS, including xhttp/httpupgrade transports.
- Live dashboard rewrite with fixed layout regions and streaming working-pair panel.
- First-run onboarding flow, menu refresh, and last-results view.
- Optional `tls_insecure_compat` setting for broken-cert endpoints.
- Enhanced failure diagnostics with per-worker SNISPF/Xray log tails.

### Changed
- Xray config no longer uses the removed `allowInsecure` field.
- Release bundles rebuilt with updated runtime components.

### Fixed
- Ctrl+C during scans returns to the menu instead of exiting the app.

### Runtime Components
- SNISPF Core: v0.1.3 -> v0.1.8
- Xray Core: v26.3.27 -> v26.3.27 (no change)

## [v0.1.5] - 2026-05-29

### Added
- Parallel DNS resolution using a thread pool, vastly speeding up the resolution phase.
- Added timeout support for socket name resolution queries so they don't hang indefinitely.
- Linux virtual environment support in `start.sh`, automatically creating, activating, and installing dependencies inside a local `.venv` rather than polluting global site-packages.

### Fixed
- Fixed graceful stop (Ctrl+C) ignoring stops during name resolution, waiting forever for worker joins, or stuck queue processing.
- Completely fixed the menu system UI to avoid screen flashes, out-of-date menus, and unintuitive screen-clearing before the user can read the action output. Added a status panel.

## [v0.1.4] - 2026-04-18

### Changed
- Expanded the default SNI list.
- Rebuilt release bundles from scratch to refresh bundled runtime components.

### Runtime Components
- SNISPF Core: v0.1.2 -> v0.1.3
- Xray Core: v26.3.27 -> v26.3.27 (no change)

## [v0.1.3] - 2026-04-18

### Added
- Changelog-based release workflow support for future tagged releases.

### Changed
- Launcher experience and docs were polished across platforms.
- Startup flows were improved with dependency checks and fallback installation behavior.
- Release guidance and quick-start documentation were reorganized for better first-run clarity.

### Fixed
- Inline screenshot visibility and onboarding UX regressions addressed.

### Runtime Components
- SNISPF Core: v0.1.2
- Xray Core: v26.3.27

## [v0.1.2] - 2026-04-17

See Git history and GitHub release notes for details.

