---
name: remote-review
description: Serve a review page from a cloud machine and reach it through an ssh tunnel, so the reviewer can use another computer or a phone (each with its own ssh tunnel to that machine) and the page survives the agent's laptop sleeping. Use when the reviewer is away from the agent's machine, when pages are heavy, or when the user says to serve review pages from a VM.
---

# Remote review

The page is built here and **served from a remote machine** that listens on its own loopback only. The local side
holds an `ssh -N -L` tunnel (so the URL stays `http://127.0.0.1:PORT/...`), pulls the remarks saved there every
20 s into the local `remarks.jsonl` (so `awb watch` works unchanged), and stops the machine when no card is open.

## Set up once (`workbench.toml`)

```toml
[remote]
provider = "ssh"          # ssh (always-on host) | gcloud | command
host = "user@example.org" # ssh alias or user@host
# gcloud:  provider = "gcloud", instance = "...", zone = "...", project = "..."
# command: status_cmd / address_cmd / start_cmd / stop_cmd shell templates
```

The remote machine needs only `python3`, `rsync` and ssh access (key based). The package itself is pushed there.

## Use

- `awb remote up`: start the machine if needed, push, start the server, open the tunnel and the sync loop; prints
  the URL (it carries a one-time token; the server also needs it from any other client).
- Rebuild pages as usual (`awb page build ...`); the sync loop pushes the changes and pulls the remarks.
- `awb remote down`: a last pull, then stop the machine, the tunnel and the loop. With `auto_stop = true` (default)
  this happens by itself once no card is open.

Prepare early: when a job will end in a review page, bring the remote up at the start, not when the page is due.
Heavy page building (images, crops) also belongs on the remote machine, not on a laptop the person is using.
Stop the machine when the review is over.
