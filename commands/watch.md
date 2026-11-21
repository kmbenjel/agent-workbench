---
description: Start streaming the reviewer's remarks to this session (awb watch as a Monitor)
---

Start `awb watch` with the Monitor tool (persistent). Each line `REMARK <report> <card> verdict=... «comment»` is a
remark to act on; `REPLY _news <id> ...` answers one of your news questions. After acting, record it with
`awb close REPORT CARD 'note quoting «the reviewer's words»'`. If it exits at once saying another watcher already
streams this workspace, one is running (the plugin starts it with the `review-loop` skill): leave it and carry on.
If Monitor is unavailable, run `awb remarks` now and then.
