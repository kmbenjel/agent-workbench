# Guide: the review loop

1. **Write cards.** One per decision. Give each a stable `id`, a `title`, a `body` (the proposal), a `why` (why the
   agent is unsure) and `images`. JSONL is easiest: one card per line.
2. **Build and serve.** `awb page build NAME --cards cards.jsonl --title "..."` then `awb serve`.
3. **Tell the reviewer.** `awb news post "what I need" --kind question`. The reviewer reads the page, not your
   terminal.
4. **Watch.** `awb watch` (Claude Code: start it with the Monitor tool). Each settled remark is one line.
5. **Act, then close.** `awb close NAME CARD 'what I did: «words the reviewer wrote»'`.
6. **Rebuild** whenever cards change. Judged, unchanged cards stay hidden; changed ones return marked.

Show only what is really doubtful: review your own cards first, and put your opinion in `why`.

Without a server the page still works as a file: ticks stay in the browser (useful for a static demo).

Other languages: set `[page] lang = "ar"`, `dir = "rtl"` and translate any label under `[page.labels]`.
