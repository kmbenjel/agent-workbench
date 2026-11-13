# Guide: job hygiene

- **Memory.** `awb governor` (run it as a background service). It writes `.workbench/workers`; your job reads that
  number before starting each unit of work. Under pressure it pauses the largest heavy process (SIGSTOP), under
  critical pressure it kills it, and when memory is calm it resumes paused ones one at a time. Tune `warn_gb`,
  `kill_gb`, `pause_mb`, `job_patterns`, `protect_units`, `protect_cmd` in `[governor]`. Opt in to closing idle
  browsers with `close_idle_browsers = true`. With `pen_slice = "jobs.slice"` it also sizes a systemd slice from the
  free memory (start your jobs with `systemd-run --user --slice=jobs.slice ...`).
- **Network.** `awb calm wait 10 && your-bulk-job`, or `awb netguard -- your-bulk-job` to stop it while the network is
  busy. Latency to the router and to an internet host is the signal; see `[netpace]`.
- **Downloads.** `awb queue run queue.tsv` with lines `url<TAB>size<TAB>target<TAB>reason`. State lives in
  `.workbench/queue/<name>.done` and `.failed`; run it again to resume. Configure verification (`verify_magic`,
  `verify_tail`, `verify_cmd = "pdfinfo {file}"`) and delivery (`local`, `rclone`, `command`) in `[queue]`.
- **Reruns.** `awb run --log job.log -- your-job` reruns after a kill once memory is back and stops after
  `max_stalls` reruns without progress.
- **Battery.** `awb power` pauses jobs at a low battery; `sleep_cmd` makes it sleep and verify the clock jumped.
