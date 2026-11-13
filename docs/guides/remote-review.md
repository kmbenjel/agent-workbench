# Guide: remote review

Serve the page from a cloud machine and reach it through an ssh tunnel.

```toml
[remote]
provider = "gcloud"        # ssh | gcloud | command
instance = "review-box"
zone = "europe-west1-b"
# provider = "ssh" with host = "user@host" for an always-on machine
# provider = "command" with status_cmd / address_cmd / start_cmd / stop_cmd for anything else
```

The remote machine needs `python3`, `rsync` and key-based ssh. `awb remote up` starts it if needed, pushes the
package and the site, starts the server on the remote loopback with a generated token, opens the tunnel unit
(`awb-tunnel`, restarted by systemd) and the sync loop (`awb-sync`, every 20 s: pull remarks, push changes), and
prints `http://127.0.0.1:8765/index.html?k=TOKEN`. When no card is open any more the loop does a last pull and stops
the machine (`auto_stop = false` to keep it). `awb remote down` does it by hand.

The tunnel runs on the agent's machine, so that URL opens there. A phone or a second computer opens it through
its own ssh client (on Android, for example, Termux) with the same forward, `ssh -N -L 8765:127.0.0.1:8765 user@vm`,
and the URL `awb remote url` prints; the server answers only requests that reach it on its loopback.

Without systemd the tunnel and the loop run as detached processes instead of user units.
