"""Editing sessions: non-destructive history with undo/redo.

A session lives in its own folder and only *references* the original photo
(by absolute path + SHA-256). The original is opened read-only by RapidRAW
and is never written to. Every edit is a full M7 spec snapshot, so undo/redo
is just moving a cursor.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path

from PIL import Image, ImageOps

from . import analyze as analysis
from . import engine
from . import spec as m7spec

DEFAULT_WORKSPACE = Path.home() / "M7Studio"
PREVIEW_EDGE = 1600


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Session:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.file = self.folder / "session.json"
        self.data = json.loads(self.file.read_text())

    # ------------------------------------------------------------ creation
    @classmethod
    def open(cls, photo: Path, workspace: Path | None = None) -> "Session":
        photo = Path(photo).resolve()
        if photo.suffix.lower() not in engine.SUPPORTED_EXTENSIONS:
            raise engine.EngineError(f"Unsupported file type {photo.suffix}")
        digest = sha256(photo)
        folder = Path(workspace or DEFAULT_WORKSPACE) / f"{photo.stem}-{digest[:10]}"
        if (folder / "session.json").exists():
            s = cls(folder)
            if not s.data.get("width"):
                s._prepare()
            return s
        folder.mkdir(parents=True, exist_ok=True)
        data = {
            "source": str(photo),
            "sourceSha256": digest,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "history": [{"spec": m7spec.new_spec(), "note": "original", "time": time.time()}],
            "cursor": 0,
        }
        (folder / "session.json").write_text(json.dumps(data, indent=2))
        s = cls(folder)
        s._prepare()
        return s

    def _prepare(self):
        """Render the untouched 'before' image and learn the developed size."""
        src = self.source
        before = self.folder / "before.jpg"
        engine.render(src, {}, before, fmt="jpeg", quality=92, long_edge=PREVIEW_EDGE)
        self.data["width"], self.data["height"] = self._full_size(before)
        self.data["analysis"] = analysis.analyze(before)
        self.save()

    def _full_size(self, preview: Path) -> tuple[int, int]:
        src = self.source
        if src.suffix.lower() not in engine.RAW_EXTENSIONS:
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im)
                return im.width, im.height
        # RAW: the developed size can differ from EXIF (sensor border crop), so
        # ask RapidRAW for one full-size neutral render and measure it.
        full = self.folder / "original-neutral.jpg"
        engine.render(src, {}, full, fmt="jpeg", quality=85, keep_metadata=False)
        with Image.open(full) as im:
            w, h = im.size
        full.unlink(missing_ok=True)
        return w, h

    # ------------------------------------------------------------ state
    @property
    def source(self) -> Path:
        return Path(self.data["source"])

    @property
    def size(self) -> tuple[int, int]:
        return self.data["width"], self.data["height"]

    @property
    def spec(self) -> dict:
        return copy.deepcopy(self.data["history"][self.data["cursor"]]["spec"])

    def save(self):
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        tmp.replace(self.file)

    def check_original(self):
        if not self.source.exists():
            raise engine.EngineError(f"Original photo is missing: {self.source}")
        if sha256(self.source) != self.data["sourceSha256"]:
            raise engine.EngineError("Original photo changed on disk since the session started.")

    # ------------------------------------------------------------ history
    def commit(self, new_spec: dict, note: str) -> dict:
        # Validate by compiling before recording anything.
        m7spec.compile_spec(new_spec, *self.size)
        h = self.data["history"][: self.data["cursor"] + 1]
        h.append({"spec": new_spec, "note": note, "time": time.time()})
        self.data["history"] = h
        self.data["cursor"] = len(h) - 1
        self.save()
        return new_spec

    def undo(self) -> bool:
        if self.data["cursor"] == 0:
            return False
        self.data["cursor"] -= 1
        self.save()
        return True

    def redo(self) -> bool:
        if self.data["cursor"] >= len(self.data["history"]) - 1:
            return False
        self.data["cursor"] += 1
        self.save()
        return True

    def history(self) -> list[dict]:
        return [{"step": i, "note": e["note"], "current": i == self.data["cursor"]}
                for i, e in enumerate(self.data["history"])]

    # ------------------------------------------------------------ rendering
    def rapidraw_adjustments(self, spec: dict | None = None) -> dict:
        return m7spec.compile_spec(spec or self.spec, *self.size)

    def render_preview(self) -> Path:
        out = self.folder / "previews" / f"step-{self.data['cursor']:03d}.jpg"
        if not out.exists():
            engine.render(self.source, self.rapidraw_adjustments(), out, fmt="jpeg",
                          quality=90, long_edge=PREVIEW_EDGE, keep_metadata=False)
        return out

    def compare(self) -> Path:
        from .preview import before_after
        after = self.render_preview()
        out = self.folder / "previews" / f"compare-{self.data['cursor']:03d}.jpg"
        return before_after(self.folder / "before.jpg", after, out)

    def export(self, output: Path, fmt: str | None = None, quality: int = 95,
               tiff_bit_depth: int = 16) -> engine.RenderResult:
        self.check_original()
        output = Path(output)
        if fmt is None:
            ext = output.suffix.lower()
            fmt = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".tif": "tiff",
                   ".tiff": "tiff"}.get(ext, "jpeg")
        res = engine.render(self.source, self.rapidraw_adjustments(), output, fmt=fmt,
                            quality=quality, tiff_bit_depth=tiff_bit_depth)
        (output.parent / (output.name + ".m7.json")).write_text(json.dumps(
            {"source": str(self.source), "sourceSha256": self.data["sourceSha256"],
             "spec": self.spec}, indent=2))
        return res
