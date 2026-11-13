"""Minimal Chrome DevTools client over --remote-debugging-pipe (stdlib only): real clicks, typing and screenshots.

Chrome starts with a clean environment (a desktop session variables can make it abort at launch)."""
import base64
import fcntl
import json
import os
import select
import shutil
import subprocess
import tempfile
import time

CHROME = os.environ.get("CHROME") or shutil.which("google-chrome") or shutil.which("chromium") or "/opt/google/chrome/chrome"

class Chrome:
    def __init__(self, width=1100, height=900, profile=None):
        profile = profile or tempfile.mkdtemp(prefix="awb-chrome-")
        r1, w1 = os.pipe(); r2, w2 = os.pipe()
        r1 = fcntl.fcntl(r1, fcntl.F_DUPFD, 10); w2 = fcntl.fcntl(w2, fcntl.F_DUPFD, 10)
        self.w, self.r = w1, r2
        def pre():
            os.dup2(r1, 3); os.dup2(w2, 4)
        env = {"HOME": os.environ["HOME"], "PATH": "/usr/bin:/bin"}
        self.p = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-sandbox",
            f"--user-data-dir={profile}", "--remote-debugging-pipe", "--hide-scrollbars", f"--window-size={width},{height}", "about:blank"],
            env=env, preexec_fn=pre, close_fds=False, stderr=subprocess.DEVNULL)
        os.close(r1); os.close(w2)
        self.buf, self.n, self.events = b"", 0, []
        self.sid = None
        t = self.call("Target.createTarget", url="about:blank")
        a = self.call("Target.attachToTarget", targetId=t["targetId"], flatten=True)
        self.sid = a["sessionId"]
        self.call("Page.enable"); self.call("Runtime.enable")
        self.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)

    def _read(self, timeout):
        end = time.time() + timeout
        while time.time() < end:
            if b"\0" in self.buf:
                m, self.buf = self.buf.split(b"\0", 1)
                return json.loads(m)
            if select.select([self.r], [], [], 0.1)[0]:
                self.buf += os.read(self.r, 1 << 20)
        return None

    def call(self, method, timeout=30, **params):
        self.n += 1; i = self.n
        msg = {"id": i, "method": method, "params": params}
        if self.sid and not method.startswith("Target."): msg["sessionId"] = self.sid
        os.write(self.w, json.dumps(msg).encode() + b"\0")
        end = time.time() + timeout
        while time.time() < end:
            m = self._read(1)
            if not m: continue
            if m.get("id") == i:
                if "error" in m: raise RuntimeError(m["error"])
                return m["result"]
            self.events.append(m)
        raise TimeoutError(method)

    def goto(self, url, wait=0.8):
        self.call("Page.navigate", url=url); time.sleep(wait)

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    def box(self, selector):
        return self.js(f"(()=>{{const e=document.querySelector({json.dumps(selector)});if(!e)return null;e.scrollIntoView({{block:'center'}});const r=e.getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]}})()")

    def click(self, selector):
        x, y = self.box(selector); time.sleep(0.15)
        x, y = self.box(selector)
        for t in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", type=t, x=x, y=y, button="left", clickCount=1)
        time.sleep(0.3)

    def type(self, selector, text):
        self.click(selector)
        self.call("Input.insertText", text=text); time.sleep(1.4)  # the page saves 0.8 s after the last keystroke

    def shot(self, path, full=False):
        r = self.call("Page.captureScreenshot", format="png", captureBeyondViewport=full)
        open(path, "wb").write(base64.b64decode(r["data"]))

    def close(self):
        self.p.kill()
