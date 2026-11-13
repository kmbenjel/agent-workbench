# The JSONL protocol

Three append-only files in the workspace (`.workbench/` by default). One JSON object per line, UTF-8. Every record
carries `"v": 1`. Readers skip lines they cannot parse (a torn last line is normal while a writer is working) and
ignore unknown fields, so the protocol can grow.

## `remarks.jsonl`: what the reviewer did

```json
{"v":1,"time":"2026-11-13T10:04:11Z","report":"ocr-fixes","card":"line-7","verdict":"reject","comment":"Keep the British spelling.","rev":"1f3a9c0b52"}
```

| Field | Meaning |
|---|---|
| `report`, `card` | Which card. `report` is `_news` for an answer to a news item; `card` is then the news id |
| `verdict` | `approve`, `reject` or `""` (a comment only, or a tick removed) |
| `comment` | Free text, up to 4000 characters |
| `rev` | The revision of the card the reviewer saw (see below) |

The **latest** remark per `(report, card)` wins; ties go to the later file. Written by the review server
(`POST /api/remark`, JSON body, `Content-Type: application/json`). Anything can append to the file.

## `news.jsonl`: what the agent tells the reviewer

```json
{"v":1,"id":"n3f9a1c","time":"2026-11-13T10:00:02Z","kind":"question","title":"Seven lines","text":"British or modernised spelling?","card":""}
```

`kind` is `info`, `question` or `done`. A `question` shows an answer box on the page; the answer is a remark on
report `_news`, card = the news `id`.

## `closed.jsonl`: what the agent dealt with

```json
{"v":1,"time":"2026-11-13T10:07:40Z","report":"ocr-fixes","card":"line-7","note":"kept British: «Keep the British spelling»","rev":"1f3a9c0b52","reopen":false}
```

A close record hides the card (while its `rev` still matches) and removes the remark from `awb remarks`. A record
whose note starts with `REOPEN` cancels earlier closes of that card. In strict mode (default) the note must hold a
quote in «…» that appears in a remark on record for that card, or say `my judgment`, or start with `ticked`/`REOPEN`.

## Cards: `reports/<name>.json`

```json
{"title":"OCR fixes","intro":"...","sections":[{"id":"a","title":"Proposed fixes"}],
 "cards":[{"id":"line-1","section":"a","title":"...","subtitle":"...","why":"...","body":"...","images":["crops/1.png"],"rev":"optional"}]}
```

`awb page build NAME --cards FILE` also accepts JSONL (one card per line) and TSV (`id title body images`).
Card ids are yours and must be unique; they never change when a page is rebuilt, and hidden cards never renumber.

### Revisions: ticks survive redos

A card's *revision* is its `rev` field, or a hash of title, subtitle, body, images, why and extra. A remark stores
the revision it judged. A card whose latest remark is an approval of the current revision, or that was closed at the
current revision, is hidden. If the content changes, the card returns marked "changed since your last judgment".

## Watch output

`awb watch` prints one line per settled remark: `REMARK <report> <card> verdict=<approve|reject|none> «comment»`
(`REPLY` for `_news`), or the raw JSON with `--json`. A remark settles after `settle_seconds` without change.
The read position is saved in `watch.pos`.
