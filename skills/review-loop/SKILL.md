---
name: review-loop
description: Run a human-in-the-loop review during a long job. Use when work needs a person's eye (uncertain fixes, labels, renames, crops, merges), when the user wants to review in a browser and comment, or when remarks from a review page must be picked up and acted on. Builds a review page of cards, streams the reviewer's ticks and comments to you, and keeps them informed on the page.
---

# The review loop

The reviewer reads a **browser page**, not your terminal. You post cards and news to the page; they tick, reject
and comment; you receive each remark as it settles and act on it. All state is plain JSONL in `.workbench/`.

Use the `awb` CLI (or the MCP tools `make_review_page`, `post_news`, `read_remarks`, `close_card` when the
`agent-workbench` MCP server is connected). `awb --help` lists everything. With the plugin enabled, `awb` is already
on your shell's `PATH`: there is nothing to install.

## Steps

1. **Cards.** One card per decision the person must make. Each card has a stable `id` you choose (a hash of the item
   is ideal), a `title`, a `body` (what you propose, in a line or two), a `why` (why you are unsure), and `images`
   (a crop, a before/after, a screenshot). Write them as JSONL/JSON and build the page:
   `awb page build NAME --cards cards.jsonl --title "..." --intro "..."`
   Rebuilding is safe: remarks are keyed by card id, an approved and unchanged card stays hidden, a card whose content
   changed returns marked *changed since your last judgment*, and nothing is renumbered.
2. **Show only what is really doubtful.** Do your own review first. Cards you are sure of are not the reviewer's job;
   ask about the doubtful ones, with your opinion on the card (`why`). A page of a thousand cards is not reviewed.
3. **Serve.** `awb serve` (127.0.0.1 only) and give the reviewer the URL `http://127.0.0.1:8765/NAME.html`. For a
   reviewer on another device, or a laptop that sleeps, use the `remote-review` skill.
4. **Tell them on the page.** `awb news post "what I did / what I need" --kind info|question|done`. Questions get an
   answer box on the page. People do not read the terminal: anything they must see goes into news, not into chat.
5. **Watch.** Each remark must reach you as a notification, once the reviewer stops typing (45 s by default).
   - In an interactive Claude Code session the plugin has already started the watcher when this skill was invoked
     (monitor `remarks`, which runs `awb watch` in the session's directory). Do not start another.
   - If you work on another directory (`awb --root DIR ...`), or no monitor is running, start one yourself as a
     **persistent Monitor** (the Monitor tool with command `awb watch`, or `awb --root DIR watch`).
   - One watcher streams a workspace at a time. A second `awb watch` prints who holds the stream and exits with
     status 3: that is not an error to retry, the remarks arrive where the first one runs.
   - Without Monitor (headless runs): poll `awb remarks` (unhandled remarks, `--json` for machine use).
   Line format: `REMARK <report> <card> verdict=approve|reject|none «comment»`; answers to your questions arrive as
   `REPLY _news <news-id> verdict=none «answer»`.
6. **Act on every remark**, then **close it**: `awb close REPORT CARD 'swapped for the older scan: «use the older one»'`.
   - A tick with no comment approves the proposal as shown: apply it.
   - A rejection with no comment is a question: re-check, and ask via news if still unclear.
   - A comment is an instruction. Do it; do not argue on the card.
7. **Closing is strict.** The note must quote the reviewer in «…» and the words must really be on record, or say
   `my judgment` when you decided. Never write "he says it is fine" without his words: the command refuses it.
8. **Keep the loop alive.** Process remarks continuously while the job runs; if you started the Monitor yourself,
   re-arm it at its expiry. After a redo of the job, re-run step 1: judged cards stay hidden unless their content
   changed.

## Rules that matter

- Never invent an approval. If nothing is on record, the card is not approved.
- A remark you cannot act on is a question: post it as news with the card id.
- A reviewer's earlier ruling is a standing check: after a redo, compare the new result with each ruling and treat a
  regression as your fault, not as a new doubt to show.
- Do not close a card the reviewer asked about before you have actually done what they asked.
