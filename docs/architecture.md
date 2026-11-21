# Architecture

![Architecture](media/architecture.png)

| Module | Responsibility |
|---|---|
| `store.py` | The JSONL protocol, card revisions, the strict close rule |
| `page.py` + `templates/` | Cards to one self-contained HTML page (no external assets) |
| `server.py` | Stdlib `ThreadingHTTPServer`: static pages, `/api/remark`, `/api/remarks`, `/api/news`, `/api/status` |
| `watch.py` | Settle window, saved position, line format |
| `review.py` | The agent's side: unhandled remarks, close, status |
| `remote.py` | Providers (ssh, gcloud, command), transport (rsync + ssh), units (systemd or detached processes), controller |
| `static.py` | Static review: publish command, pull remarks from a comment backend, push news; `examples/cloudflare-worker/` is the backend |
| `governor.py` | Memory guard; `Governor.step()` is pure given a `System` |
| `netpace.py`, `calm.py` | Rate controller, leaky bucket, calm detection, pause gate |
| `queue.py` | Download, verify, deliver with staging caps and resume state |
| `resumable.py`, `power.py` | Rerun after kills; battery guard |
| `mcp_server.py` | MCP over stdio (JSON-RPC 2.0), four tools |
| `cli.py` | The `awb` command |

## Why it is testable

Anything with a decision in it receives its inputs: the governor a `System` (free memory, pressure, processes,
signals), the watcher a clock, the remote controller a provider, a transport and a units manager, the queue a fetch
and a deliver function, the static sync a fetch function, the power guard a battery directory and a process finder.
Tests use fakes; some also run against the real thing: `sleep` processes, an HTTP server on an ephemeral port, the
MCP server and the plugin's shim and monitor on real pipes, the Worker's code under Node (or `wrangler dev` with
`AWB_WRANGLER`), and the page itself in headless Chrome (`AWB_BROWSER=1`).

## Security model

What is defended, and against whom: the reviewer's ticks are instructions to an agent, so nothing but the
reviewer's own page may write one, and nothing a card shows may act.

| Threat | Defence | Test |
|---|---|---|
| Another machine on the network reads or writes remarks | The server binds `127.0.0.1`; any other address needs `[server] token` and is refused without it | `test_non_loopback_needs_a_token` |
| A web page elsewhere reaches the server through DNS rebinding | `Host` must be loopback, the bound address or one of `allowed_hosts` | `test_host_header_must_be_loopback` |
| A web page elsewhere posts a remark (CSRF) | POSTs must be `application/json` (so a browser asks first, and no preflight is answered), `Origin` must equal `Host`, `Sec-Fetch-Site` must be `same-origin`; the token cookie is `SameSite=Strict`, `HttpOnly` and holds a digest, not the token | `test_bad_requests_are_refused`, `test_post_refuses_cross_site_fetch_metadata_and_bad_lengths` |
| A page elsewhere frames the review page and collects a click on Approve | Every response sends `frame-ancestors 'none'` and `X-Frame-Options: DENY` | `test_no_response_can_be_framed_and_pictures_are_inert` |
| Card text that came from untrusted data (OCR, scraped titles) runs as script | Everything is HTML-escaped; the JSON data block leaves no `<`; the template is filled in one pass; and the page's own policy allows only the kit's script, by hash, so markup that slipped through still could not run | `test_injected_markup_cannot_run_script` (Chrome) |
| A picture that carries script (SVG) is opened in its own tab | SVG and XML are served with a sandboxing policy | `test_a_picture_with_script_is_inert` (Chrome) |
| Reading files outside the site | Paths are resolved (symlinks included) and confined to the site directory; no directory listings | `test_path_traversal_is_confined`, `test_directories_are_not_listed` |
| Large or malformed requests | Bodies are capped at 64 KB, lengths are validated, idle connections time out | `test_bad_requests_are_refused` |
| A save that failed looks saved | Any answer other than 2xx is shown as "not saved" | `test_a_refused_save_is_not_reported_as_saved` (Chrome) |
| A static host that allows framing: a page elsewhere frames the review page, whose browser remembers the key | Inside a frame the page does not use the key, so a click there reaches no one | `test_static_page_in_a_frame_does_not_use_the_key` (Chrome) |

- Static review: the comment backend takes bearer keys (no cookie, so no cross-site request can borrow the
  reviewer's identity), answers only origins on its allow-list, and never sees the reviewer's key in a URL (it
  rides after `#`). The key guards remarks and news; the pages are as public as their host makes them.
- Remote: the VM server binds its loopback; the token travels over ssh stdin into a 0600 file, never on a command
  line; the local tunnel is an ssh port forward to loopback.
- Queue targets must be relative paths without `..`.
