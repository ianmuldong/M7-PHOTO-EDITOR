"""Local chat app for M7 Studio Editor (Python standard library only).

    python -m m7.server            # http://127.0.0.1:7777

Binds to 127.0.0.1 only. Photos are copied into the local workspace and
processed by the local RapidRAW binary; nothing leaves this machine.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import engine, nl
from . import spec as m7spec
from .session import DEFAULT_WORKSPACE, Session

WEB = Path(__file__).resolve().parent / "web"
LOCK = threading.Lock()  # one render at a time keeps memory predictable


class App:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.uploads = workspace / "uploads"
        self.exports = workspace / "exports"
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.exports.mkdir(parents=True, exist_ok=True)

    def session(self, sid: str) -> Session:
        folder = (self.workspace / sid).resolve()
        if folder.parent != self.workspace.resolve() or not (folder / "session.json").exists():
            raise engine.EngineError("Unknown session")
        return Session(folder)

    def url(self, path: Path) -> str:
        rel = Path(path).resolve().relative_to(self.workspace.resolve())
        return "/files/" + urllib.parse.quote(rel.as_posix()) + f"?v={int(Path(path).stat().st_mtime)}"

    def state(self, s: Session, notes=None) -> dict:
        with LOCK:
            preview = s.render_preview()
            compare = s.compare()
        return {
            "session": s.folder.name, "source": s.source.name, "size": s.size,
            "step": s.data["cursor"], "history": s.history(), "spec": s.spec,
            "before": self.url(s.folder / "before.jpg"), "after": self.url(preview),
            "compare": self.url(compare), "analysis": s.data.get("analysis"),
            "interpreted": notes or [],
        }


def make_handler(app: App):
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path: Path, download=False):
            ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
                     ".jpg": "image/jpeg", ".png": "image/png", ".tiff": "image/tiff",
                     ".json": "application/json"}.get(path.suffix.lower(), "application/octet-stream")
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path in ("/", "/index.html"):
                return self._file(WEB / "index.html")
            if u.path == "/api/presets":
                return self._json(m7spec.list_presets())
            if u.path.startswith("/files/"):
                rel = urllib.parse.unquote(u.path[len("/files/"):])
                p = (app.workspace / rel).resolve()
                if app.workspace.resolve() not in p.parents or not p.is_file():
                    return self._json({"error": "not found"}, 404)
                return self._file(p, download="download" in u.query)
            return self._json({"error": "not found"}, 404)

        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            n = int(self.headers.get("Content-Length", 0))
            try:
                if u.path == "/api/upload":
                    name = Path(urllib.parse.unquote(self.headers.get("X-Filename", "photo.jpg"))).name
                    name = re.sub(r"[^\w.\-]", "_", name)
                    if Path(name).suffix.lower() not in engine.SUPPORTED_EXTENSIONS:
                        return self._json({"error": f"Unsupported file type {Path(name).suffix}"}, 400)
                    dest = app.uploads / name
                    with open(dest, "wb") as f:
                        remaining = n
                        while remaining > 0:
                            chunk = self.rfile.read(min(remaining, 1 << 20))
                            if not chunk:
                                break
                            f.write(chunk)
                            remaining -= len(chunk)
                    dest.chmod(0o444)  # the uploaded original is read-only from here on
                    with LOCK:
                        s = Session.open(dest, app.workspace)
                    return self._json(app.state(s, [f"Opened {name} ({s.size[0]}×{s.size[1]})"]
                                                + [f"analysis: {', '.join(s.data['analysis']['notes']) or 'well balanced'}"]))
                body = json.loads(self.rfile.read(n) or b"{}")
                s = app.session(body.get("session", ""))
                if u.path == "/api/chat":
                    text = body.get("text", "").strip()
                    low = text.lower()
                    if low in ("undo", "go back", "undo that"):
                        ok = s.undo()
                        return self._json(app.state(s, ["undone" if ok else "nothing to undo"]))
                    if low in ("redo", "redo that"):
                        ok = s.redo()
                        return self._json(app.state(s, ["redone" if ok else "nothing to redo"]))
                    change, notes = nl.interpret(text, s.data.get("analysis"))
                    actionable = any(k in change for k in ("add", "set", "presets", "crop", "straighten",
                                                           "lensCorrection", "masks", "reset"))
                    if not actionable:
                        st = app.state(s, notes + ["No change made — try e.g. 'warmer', 'more contrast', "
                                                   "'golden hour at 70%', 'crop 4:5'."])
                        return self._json(st)
                    s.commit(nl.apply_change(s.spec, change), text)
                    return self._json(app.state(s, notes))
                if u.path == "/api/apply":
                    s.commit(nl.apply_change(s.spec, body["change"]), body.get("note", "manual change"))
                    return self._json(app.state(s, ["applied"]))
                if u.path == "/api/goto":
                    step = int(body["step"])
                    if 0 <= step < len(s.data["history"]):
                        s.data["cursor"] = step
                        s.save()
                    return self._json(app.state(s, [f"jumped to step {step}"]))
                if u.path == "/api/export":
                    fmt = body.get("format", "jpeg")
                    ext = {"jpeg": ".jpg", "tiff": ".tiff", "png": ".png"}[fmt]
                    out = app.exports / f"{s.source.stem}_M7_step{s.data['cursor']}{ext}"
                    with LOCK:
                        res = s.export(out, quality=int(body.get("quality", 95)))
                    from PIL import Image
                    with Image.open(res.output) as im:
                        size = im.size
                    return self._json({"download": app.url(res.output) + "&download=1",
                                       "size": size, "seconds": round(res.seconds, 1)})
                return self._json({"error": "not found"}, 404)
            except (engine.EngineError, m7spec.SpecError, KeyError, ValueError) as e:
                return self._json({"error": str(e)}, 400)
            except Exception as e:
                traceback.print_exc()
                return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    return H


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7777)
    ap.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    a = ap.parse_args(argv)
    ws = Path(a.workspace).expanduser()
    ws.mkdir(parents=True, exist_ok=True)
    engine.find_binary()  # fail fast with a clear message
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(App(ws)))
    print(f"M7 Studio Editor running at http://127.0.0.1:{a.port}  (workspace: {ws})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
