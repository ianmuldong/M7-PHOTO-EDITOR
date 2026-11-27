"""Smoke test for the packaged Windows app.

Starts the packaged launcher's server, then drives the same HTTP API the
browser uses: upload -> natural-language edit -> undo/redo -> full-res export.
Usage: python smoke_test.py "<path to M7 Studio.exe>" <photo> [<photo> ...]
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

exe, photos = Path(sys.argv[1]), [Path(p) for p in sys.argv[2:]]
env = dict(os.environ, M7_NO_BROWSER="1")
proc = subprocess.Popen([str(exe)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True)
base = "http://127.0.0.1:7777"


def call(path, body=None, headers=None, timeout=3600):
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, headers=headers or {"Content-Type": "application/json"},
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if r.headers.get("Content-Type", "").startswith("application/json") else raw


try:
    for _ in range(120):
        try:
            presets = call("/api/presets", timeout=5)
            break
        except Exception:
            if proc.poll() is not None:
                print(proc.stdout.read()); raise SystemExit("launcher exited early")
            time.sleep(1)
    else:
        raise SystemExit("server did not start")
    assert len(presets) == 10, presets
    print("presets:", len(presets))
    downloads = []
    for photo in photos:
        t = time.time()
        st = call("/api/upload", photo.read_bytes(), {"X-Filename": photo.name})
        print(f"[{photo.name}] opened {st['size']} in {time.time() - t:.1f}s; {st['interpreted']}")
        st = call("/api/chat", {"session": st["session"], "text": "cinematic sports at 80%, lift the shadows a bit, fix lens distortion"})
        print("  edit:", st["interpreted"], "step", st["step"])
        assert st["step"] == 1
        st = call("/api/chat", {"session": st["session"], "text": "slightly warmer and crop to 4:5"})
        assert st["step"] == 2
        st = call("/api/chat", {"session": st["session"], "text": "undo"}); assert st["step"] == 1
        st = call("/api/chat", {"session": st["session"], "text": "redo"}); assert st["step"] == 2
        after = call(st["after"])
        assert len(after) > 1000
        for fmt in ("jpeg", "tiff"):
            t = time.time()
            ex = call("/api/export", {"session": st["session"], "format": fmt})
            w, h = ex["size"]
            full_w, full_h = st["size"]
            expect_w = round(min(full_w, full_h * 4 / 5))
            assert h == full_h and abs(w - expect_w) <= 2, (ex, st["size"])
            data = call(ex["download"])
            downloads.append(ex["download"].split("?")[0])
            print(f"  export {fmt}: {w}x{h}, {len(data) / 1e6:.1f} MB in {time.time() - t:.1f}s")
    assert len(set(downloads)) == len(downloads), "an export overwrote another"
    st = call("/api/upload", photos[0].read_bytes(), {"X-Filename": photos[0].name})
    print("re-upload of same file OK, history kept at step", st["step"])
    print("SMOKE TEST PASSED")
finally:
    proc.terminate()
