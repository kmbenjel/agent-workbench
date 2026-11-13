---
name: job-hygiene
description: Keep long batch jobs from hurting the machine or the network. Use before starting jobs that run for hours (OCR, media conversion, bulk downloads, parallel workers), when memory is short, when downloads should not lag other people on the network, or when a job must survive being killed and continue where it stopped.
---

# Job hygiene

Four small tools, all driven by `workbench.toml`:

| Problem | Tool |
|---|---|
| The job's workers eat memory; the desktop (or VM) gets OOM-killed | `awb governor` (run it as a background service) |
| A bulk transfer makes other people lag | `awb calm wait 10 && ...`, `awb netguard -- CMD` |
| Many files to fetch, verify, deliver; must resume | `awb queue run queue.tsv` |
| A job is killed (by the governor, a reboot) and must continue | `awb run --log job.log -- CMD` |
| Laptop battery dying under a job | `awb power` |

## Rules

1. **Governor first.** Start `awb governor` before heavy work. It writes `.workbench/workers` (how many workers the
   job may run); make your job read that file before starting each unit of work. It pauses (SIGSTOP) the largest heavy
   process under pressure, kills it only when memory is critical, resumes when calm. Never raise limits it set.
2. **Workers are bounded**: at most 3 on a shared laptop unless the governor allows more; heavy CPU work belongs on a
   remote machine.
3. **Downloads**: one bulk transfer at a time; start after `awb calm wait`; use the queue (`awb queue run`), never
   one `curl` per file. It resumes (`.done`/`.failed` files) and caps staged bytes (`stage_gb`).
4. **Resumable by design**: jobs must continue where they stopped; run them with `awb run --log ... -- CMD`.
5. **Stopping a job**: `kill <pid>` found with `pgrep -f "[n]ame"` in a separate command (a `pkill -f name` in the same
   command line as the restart kills the shell itself).
6. **Build a path from a lookup that can come back empty? Never.** Use `"${P:?empty path}"`: an empty variable once
   turned a single-file copy into a whole-library copy.

See `docs/guides/` in the repository for the config keys.
