"""Backup video of a live session: Grafana through the Session Manager tunnel (infra/gpu.sh ui), one
frame every few seconds, with the session phase and the agent's latest message laid over it.

    infra/gpu.sh ui                                   # Grafana on localhost:3401
    python3 cluster/demo/record_video.py out/ 95      # minutes to record; writes out/frames + out/session.mp4

Headless Brave (any Chromium works: BROWSER=/path/to/binary), its own throw-away profile. The overlay
text comes from the ops machine every 45 s: /root/phase.txt (written by the session script) and the
agent's outbox. Light on the laptop: one tab, one screenshot per FRAME_S seconds.
"""
from __future__ import annotations

import html
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
BROWSER = os.environ.get("BROWSER", "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser")
FRAME_S = float(os.environ.get("FRAME_S", "10"))
SWITCH_S = 120   # alternate the two dashboards every 2 min
DASHES = ["ic-shop", "ic-cluster"]
URL = "http://localhost:3401/d/{uid}?orgId=1&kiosk&refresh=10s&from=now-20m&to=now"

feed = {"phase": "starting", "agent": ""}

POLL = """export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
cat /root/phase.txt 2>/dev/null || echo starting
echo ===
kubectl -n agent exec deploy/ic-agent -- sh -c 'tail -n 40 /incidents/outbox.log 2>/dev/null' 2>/dev/null
"""


def poll_forever():
    f = Path(tempfile.mkstemp(suffix=".sh")[1])
    f.write_text(POLL)
    while True:
        try:
            out = subprocess.run([str(REPO / "infra/gpu.sh"), "run", str(f), "40"], capture_output=True,
                                 text=True, timeout=90).stdout.replace("\r", "")
            phase, _, outbox = out.partition("===")
            feed["phase"] = phase.strip().splitlines()[-1] if phase.strip() else feed["phase"]
            msgs = [m.strip() for m in outbox.strip().split("\n\n") if m.strip()]
            if msgs:
                feed["agent"] = "\n".join(msgs[-1].splitlines()[:4])[:420]
        except Exception as e:   # a missed poll keeps the last text
            print("poll:", e, file=sys.stderr)
        time.sleep(45)


OVERLAY = """(t) => {
  let d = document.getElementById('ic-overlay');
  if (!d) { d = document.createElement('div'); d.id = 'ic-overlay'; document.body.appendChild(d);
    d.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:99999;padding:10px 18px;' +
      'font:15px/1.35 -apple-system,Segoe UI,sans-serif;color:#fff;background:rgba(10,14,22,.88);' +
      'border-top:3px solid #f2a33a;white-space:pre-wrap'; }
  d.innerHTML = t;
}"""


def main(out_dir: str, minutes: float):
    out = Path(out_dir); frames = out / "frames"; frames.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=poll_forever, daemon=True).start()
    end = time.time() + minutes * 60
    n = 0
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=BROWSER, headless=True)
        page = b.new_page(viewport={"width": 1600, "height": 900})
        current = None
        while time.time() < end:
            uid = DASHES[int(time.time() // SWITCH_S) % len(DASHES)]
            if uid != current:
                page.goto(URL.format(uid=uid), wait_until="networkidle", timeout=60000)
                current = uid
            stamp = time.strftime("%H:%M:%S UTC", time.gmtime())
            text = (f"<b>{html.escape(stamp)} &nbsp;·&nbsp; {html.escape(feed['phase'])}</b>"
                    + (f"<br><span style='color:#f2a33a'>Agent:</span> {html.escape(feed['agent'])}" if feed["agent"] else ""))
            try:
                page.evaluate(OVERLAY, text)
                page.screenshot(path=str(frames / f"{n:05d}.png"))
                n += 1
            except Exception as e:
                print("frame:", e, file=sys.stderr)
                current = None   # reload next time
            time.sleep(FRAME_S)
        b.close()
    # 10 s of session per frame, shown at 3 frames/s: ~30x real time
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "3", "-i", str(frames / "%05d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-vf", "scale=1600:-2", str(out / "session.mp4")], check=True)
    print(f"{n} frames -> {out / 'session.mp4'}")


if __name__ == "__main__":
    main(sys.argv[1], float(sys.argv[2]))
