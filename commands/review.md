---
description: Build a review page from the cards for the current job, serve it and start watching the remarks
argument-hint: <report-name> [cards-file]
---

Follow the `review-loop` skill for report **$1**.

1. If a cards file was given (`$2`), run `awb page build $1 --cards $2`; otherwise build the cards yourself from the
   current job's uncertain items (stable ids, a `why` on each) and save them as JSONL first.
2. Start `awb serve` in the background if it is not running, and print `http://127.0.0.1:8765/$1.html`.
3. Post one `awb news post` that says how many cards are open and what the reviewer should decide.
4. Make sure the remarks reach you (the skill's step 5: the plugin's `remarks` monitor, or `awb watch` as a
   persistent Monitor), and act on each remark as it arrives, closing each card with a note that quotes the reviewer.
