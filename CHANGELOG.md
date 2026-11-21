# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Static review (`awb static publish | sync | loop | url`, `[static]` in `workbench.toml`): the page on any static
  host, the remarks through a small comment backend, pulled into `remarks.jsonl` so the rest of the loop is
  unchanged. `examples/cloudflare-worker/` holds a backend for Cloudflare (a Worker and an R2 bucket) and a runner
  for it on your own machine (`node dev-server.mjs`).
- The plugin carries its own `awb` (`bin/awb`) and a monitor that starts `awb watch` when the `review-loop` skill
  runs; a `static-review` skill.
- `awb watch` holds a per-workspace lock: a second watcher says which process has the stream and exits with
  status 3 (`--shared` streams anyway).
- Tests that drive the page in headless Chrome (`AWB_BROWSER=1`), start the plugin's shim, MCP server and monitor
  as its manifests declare them, and run the Worker under Node or `wrangler dev` (`AWB_WRANGLER`).
- MCP: protocol revision 2025-11-25; `read_remarks` is marked read-only.

### Changed
- The page shows a save as saved only on a 2xx answer; anything else is "not saved".
- On a host that only serves files the page says that no server answers and keeps ticks and comments in the
  browser, as it does when opened as a file.
- The review server answers the address it is bound to (not only loopback names), tells a refused `Host` which key
  allows it, and serves no directory listings. The token may hold any characters; its cookie holds a digest.
- MCP: any error inside a tool is an error result, whatever the arguments' types.
- Report names are at most 60 characters, the length a remark's `report` field holds.
- Plugin install is documented as `/plugin marketplace add kmbenjel/agent-workbench`, then
  `/plugin install agent-workbench@agent-workbench`.

### Security
- No response can be framed by another site (`frame-ancestors 'none'`, `X-Frame-Options: DENY`).
- Each page carries a content security policy that allows only the kit's own script, by hash.
- A card's `rev` is escaped in its attribute, the page template is filled in one pass, and the JSON data block
  holds no `<`, `>` or `&`.
- SVG and XML files are served sandboxed; `Sec-Fetch-Site` must be `same-origin` on POST; a negative
  `Content-Length` is refused; the `?k=` redirect cannot leave the server.

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
