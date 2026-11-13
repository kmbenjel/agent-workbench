# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-13

### Added
- Review pages: cards with Approve / Reject / comment, news panel with answer boxes, progress and filters, dark
  mode, RTL support, translatable labels, browser-local mode when no server is attached.
- Review server (stdlib, loopback only, token to listen elsewhere, Host/Origin/content-type checks).
- JSONL protocol (`remarks.jsonl`, `news.jsonl`, `closed.jsonl`), card revisions, strict close notes.
- `awb watch` (settle window, saved position), `awb remarks`, `awb news`, `awb close`, `awb status`.
- Remote mode (`awb remote`): ssh, gcloud and command providers, tunnel unit, sync loop, auto-stop.
- Job hygiene: `awb governor`, `awb calm wait`, `awb netguard`, `awb queue run`, `awb run`, `awb power`.
- Claude Code plugin (skills, slash commands) and a dependency-free MCP server.
- `awb demo`, `awb init`, `workbench.toml` configuration with validation.
- `bench/compare.py` footprint comparison.
