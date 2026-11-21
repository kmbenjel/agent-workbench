---
name: static-review
description: Publish a review page on a static host (GitHub Pages, Cloudflare Pages, a bucket, any web server) with a small comment backend, so the reviewer opens an ordinary https link from any device and nothing has to stay running. Use when the reviewer is on a phone or another network, when no machine can stay up for the review, or when the user asks for the review page online or on Cloudflare.
---

# Static review

The page is built here, **uploaded to a static host**, and its ticks and comments go to a small comment backend
(for example the Cloudflare Worker in `examples/cloudflare-worker/` of the agent-workbench repository). You pull
them into the local `remarks.jsonl`, so the rest of the review loop (the `review-loop` skill) is unchanged.

## Needs (ask the user once, do not invent them)

```toml
# workbench.toml
[static]
api = "https://..."        # the comment backend
page_url = "https://..."   # where the site directory is published
publish_cmd = "..."        # uploads it; {site} is the directory (rsync, wrangler pages deploy, aws s3 sync ...)
```

and two keys in the environment, never in a file that is committed: `AWB_REVIEW_KEY` (the reviewer's) and
`AWB_STATIC_TOKEN` (yours). If the user has no backend yet, point them to `examples/cloudflare-worker/README.md`.

## Use

1. Build as usual: `awb page build NAME --cards cards.jsonl`. With `[static] api` set, the page talks to the backend.
2. `awb static publish`, then give the user the link from `awb static url NAME`. It ends in `#k=<key>`: it is a
   password, so hand it to the reviewer only, not into logs, commits or news items.
3. Start `awb static loop` in the background: every 20 s it pulls new remarks and pushes the news panel.
4. Watch and act exactly as in `review-loop` (the plugin's `remarks` monitor or `awb watch`; `awb close` with a
   quote). `awb static sync` does one round by hand.
5. After every rebuild of a page, `awb static publish` again. News needs no publish.

## Rules

- The pages are public to anyone who has their address unless the host restricts them: do not publish private
  material this way; use the `remote-review` skill instead.
- A remark exists for you only once it is pulled: if the loop is not running, run `awb static sync` before you say
  nothing is waiting.
- When the review is over, stop the loop. Nothing else is running.
