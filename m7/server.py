"""Local chat app for M7 Studio Editor (Python standard library only).

    python -m m7.server            # http://127.0.0.1:7777

Binds to 127.0.0.1 only. Photos are copied into the local workspace and
processed by the local RapidRAW binary; nothing leaves this machine.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import diagnostics, engine, nl
from . import spec as m7spec
from .session import DEFAULT_WORKSPACE, Session, sha256

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
            if u.path == "/api/diagnostics":
                d = diagnostics.log_dir()
                tail = ""
                if d and (d / "m7.log").exists():
                    tail = "\n".join((d / "m7.log").read_text(encoding="utf-8", errors="replace")
                                     .splitlines()[-40:])
                return self._json({"version": diagnostics.VERSION, "log_dir": str(d),
                                   "engine": str(engine.find_binary()), "log_tail": tail})
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
                    tmp = app.uploads / f".incoming-{threading.get_ident()}"
                    digest = hashlib.sha256()
                    with open(tmp, "wb") as f:
                        remaining = n
                        while remaining > 0:
                            chunk = self.rfile.read(min(remaining, 1 << 20))
                            if not chunk:
                                break
                            f.write(chunk)
                            digest.update(chunk)
                            remaining -= len(chunk)
                    dest = app.uploads / name
                    i = 1
                    while dest.exists():
                        if sha256(dest) == digest.hexdigest():  # same photo uploaded again
                            break
                        dest = app.uploads / f"{Path(name).stem}-{i}{Path(name).suffix}"
                        i += 1
                    if dest.exists():
                        tmp.unlink()
                    else:
                        tmp.replace(dest)
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
                    with LOCK:
                        changed, notes = s.say(text)
                    if not changed:
                        notes = notes + ["No change made — try e.g. 'warmer', 'more contrast', "
                                         "'golden hour at 70%', 'crop 4:5'."]
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
                    i = 2
                    while out.exists():  # never overwrite an earlier export
                        out = app.exports / f"{s.source.stem}_M7_step{s.data['cursor']}_{i}{ext}"
                        i += 1
                    with LOCK:
                        res = s.export(out, quality=int(body.get("quality", 95)))
                    from PIL import Image
                    with Image.open(res.output) as im:
                        size = im.size
                    return self._json({"download": app.url(res.output) + "&download=1",
                                       "size": size, "seconds": round(res.seconds, 1)})
                return self._json({"error": "not found"}, 404)
            except (engine.EngineError, m7spec.SpecError, KeyError, ValueError) as e:
                diagnostics.log.warning("%s failed: %s", u.path, str(e).splitlines()[0] if str(e) else e)
                return self._json({"error": str(e)}, 400)
            except Exception as e:
                traceback.print_exc()
                diagnostics.log.exception("%s crashed", u.path)
                return self._json({"error": f"Unexpected error ({type(e).__name__}: {e}). "
                                            f"Your original photo was not changed. Log folder: "
                                            f"{diagnostics.log_dir()}"}, 500)

    return H


def serve(workspace: Path, port: int = 7777, open_browser: bool = False):
    workspace = Path(workspace).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)
    logs = diagnostics.setup_logging(workspace)
    print(f"Logs: {logs}")
    engine.find_binary()  # fail fast with a clear message
    srv = None
    for p in range(port, port + 20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), make_handler(App(workspace)))
            port = p
            break
        except OSError:
            continue
    if srv is None:
        raise OSError(f"No free port between {port} and {port + 19}")
    url = f"http://127.0.0.1:{port}"
    print(f"M7 Studio Editor running at {url}  (workspace: {workspace})")
    if open_browser:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    srv.serve_forever()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7777)
    ap.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    ap.add_argument("--open", action="store_true", help="open the browser")
    a = ap.parse_args(argv)
    serve(Path(a.workspace), a.port, a.open)


if __name__ == "__main__":
    main()
