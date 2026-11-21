# Comment backend on Cloudflare: one Worker, one R2 bucket

For review pages published on a static host ([guide](../../docs/guides/static-review.md)). The page posts ticks
and comments here; `awb static sync` pulls them into the agent's `remarks.jsonl`. About 150 lines, no dependencies.

| File | What it is |
|---|---|
| `worker.js` | The backend: four requests, two keys, an origin allow-list, conditional writes to R2 |
| `wrangler.toml` | The Worker's configuration: the bucket binding and `ALLOWED_ORIGINS` |
| `dev-server.mjs` | Runs the same `worker.js` on your machine with a bucket in memory (Node 18+, no account) |

## Deploy

```bash
cd examples/cloudflare-worker
npx wrangler r2 bucket create awb-comments
# wrangler.toml: set ALLOWED_ORIGINS to the origin(s) your pages are served from, e.g. "https://reviews.example.org"
npx wrangler deploy                       # prints the Worker's URL: that is [static] api
python3 -c 'import secrets; print(secrets.token_urlsafe(24))'   # run it twice: two different keys
npx wrangler secret put REVIEW_KEY        # paste the first: the reviewer's key
npx wrangler secret put AGENT_TOKEN       # paste the second: the agent's token
```

Then, where the agent runs:

```toml
# workbench.toml
[static]
api = "https://awb-comments.<your-subdomain>.workers.dev"
page_url = "https://reviews.example.org"
publish_cmd = "npx wrangler pages deploy {site} --project-name reviews"   # or rsync, aws s3 sync, gsutil rsync ...
```

```bash
export AWB_REVIEW_KEY=...      # the same two values as the secrets; keep them out of workbench.toml
export AWB_STATIC_TOKEN=...
awb page build ocr --cards cards.jsonl && awb static publish
awb static url ocr             # the reviewer's link: https://reviews.example.org/ocr.html#k=...
awb static loop &              # every 20 s: pull remarks, push news
awb watch                      # as ever: one line per settled remark
```

## Try it without an account

```bash
REVIEW_KEY=r AGENT_TOKEN=a ALLOWED_ORIGINS=http://127.0.0.1:8000 node dev-server.mjs --port 8787 &
awb demo demo-static >/dev/null
cd demo-static
printf '[static]\napi = "http://127.0.0.1:8787"\npage_url = "http://127.0.0.1:8000"\n' > workbench.toml
export AWB_REVIEW_KEY=r AWB_STATIC_TOKEN=a
awb page build ocr-fixes && python3 -m http.server -d site 8000 &
awb static url ocr-fixes       # open it, tick a card, then:
awb static sync && awb watch --once --from-start
```

## What it stores and who may do what

| Request | Who | Effect |
|---|---|---|
| `GET /api/remarks?report=R` | reviewer key or agent token | `{card: latest remark}` from `remarks/R.json` |
| `POST /api/remark` | reviewer key | one remark in; same limits as `awb serve` (64 KB body, 4000-character comment) |
| `GET /api/news` | reviewer key or agent token | the news panel, from `news.json` |
| `PUT /api/news` | agent token | replaces the news panel (the last 50 items) |

- Keys travel as `Authorization: Bearer ...`, never in a URL the Worker sees: the reviewer's key sits after `#` in
  the link, and a fragment is not sent to any server. There is no cookie, so another site cannot act for the reviewer.
- A request with an `Origin` outside `ALLOWED_ORIGINS` is refused and gets no CORS headers.
- A remark is written with a conditional put on the report object's etag and retried on conflict, so two saves at
  the same moment both land (`tests/test_static.py`).
- The link is a password: anyone who has it can tick and comment. To withdraw it, put a new `REVIEW_KEY`.
- The browser remembers the key for the pages' origin, so publish to an origin that serves only pages of yours.
- The key guards the remarks and the news, not the pages: a page is as public as its host makes it. For material
  that must stay private, publish to a host with access control or use [remote review](../../docs/guides/remote-review.md).

Checked with `wrangler dev` 4.166.0 (the Workers runtime with its local R2) and with Node 18 and 24:
`AWB_WRANGLER="npx wrangler" AWB_BROWSER=1 pytest tests/test_static.py tests/test_browser.py`.
