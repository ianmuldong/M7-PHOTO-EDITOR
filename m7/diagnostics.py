"""Diagnostic logging, friendly error messages and a self-test for M7 Studio Editor.

Logs go to ``<workspace>/logs`` (default ``~/Pictures/M7 Studio/logs``):

* ``m7.log``                      – rolling application log
* ``engine-failure-<time>.txt``   – full RapidRAW output of every failed render
* ``self-test-report.txt``        – result of ``M7 Studio.exe --self-test``
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import logging.handlers
import os
import platform
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

log = logging.getLogger("m7")
_LOG_DIR: Path | None = None
try:  # written by the Windows build
    from ._version import VERSION
except ImportError:
    VERSION = os.environ.get("M7_VERSION", "dev")


def setup_logging(workspace: Path) -> Path:
    """Send the ``m7`` logger to ``<workspace>/logs/m7.log`` (idempotent)."""
    global _LOG_DIR
    d = Path(workspace).expanduser() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    if _LOG_DIR != d:
        h = logging.handlers.RotatingFileHandler(d / "m7.log", maxBytes=2_000_000, backupCount=3,
                                                 encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
        _LOG_DIR = d
        log.info("M7 Studio Editor %s on %s, Python %s", VERSION, platform.platform(),
                 platform.python_version())
    return d


def log_dir() -> Path | None:
    return _LOG_DIR


def gpu_adapter(engine_log: str) -> str | None:
    m = re.search(r"Using GPU adapter:\s*(.+)", engine_log)
    return m.group(1).strip() if m else None


# (pattern in RapidRAW output, plain-language explanation)
_HINTS = [
    (r"polling mapped GPU buffer|timed out before the submission|readback poll failed",
     "The graphics card did not finish the render in time. Update your graphics driver, close "
     "other GPU-heavy apps (games, video editors) and try again. On a PC without a dedicated or "
     "integrated GPU, Windows' software renderer may be too slow."),
    (r"no suitable (gpu )?adapter|failed to find .*adapter|RequestAdapterError|No adapter",
     "No compatible graphics adapter was found. Install the latest graphics driver from Intel, "
     "AMD or NVIDIA (DirectX 12 or Vulkan support is required)."),
    (r"device lost|DeviceLost|is invalid",
     "The graphics driver reset during the render. Update the graphics driver and try again."),
    (r"Failed to (decode|load|open)|unsupported (raw|camera|format)|rawler",
     "The photo could not be decoded. The file may be damaged or from an unsupported camera "
     "model. Try another photo; for RAW files, check that the camera is supported by RapidRAW."),
    (r"out of memory|OutOfMemory|allocation failed",
     "The computer ran out of memory while rendering. Close other programs and try again."),
]


def explain(engine_log: str) -> str:
    for pattern, hint in _HINTS:
        if re.search(pattern, engine_log, re.IGNORECASE):
            return hint
    return "RapidRAW reported an error while processing the photo."


def record_failure(source: Path, cmd: list[str], returncode, engine_log: str) -> Path | None:
    """Write the complete engine output for a failed render; return the file path."""
    d = _LOG_DIR or Path(tempfile.gettempdir()) / "m7-logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"engine-failure-{_dt.datetime.now():%Y%m%d-%H%M%S}.txt"
        p.write_text(
            f"M7 Studio Editor {VERSION}\n{platform.platform()}\n"
            f"source: {source}\nexit code: {returncode}\ncommand: {' '.join(cmd)}\n"
            f"GPU adapter: {gpu_adapter(engine_log) or 'not reported'}\n\n"
            f"--- RapidRAW output ---\n{engine_log}\n",
            encoding="utf-8")
        log.error("render failed for %s (exit %s); details in %s", source.name, returncode, p)
        return p
    except OSError:
        return None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sample_jpeg(path: Path):
    from PIL import Image
    w, h = 1800, 1200
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(0, h):
        for x in range(0, w):
            px[x, y] = (int(x / w * 200 + 20), int(y / h * 180 + 30), 90)
    im.save(path, quality=95)


def self_test(workspace: Path, photos: list[Path] | None = None) -> tuple[bool, str]:
    """Render a built-in sample (and optional user photos) through the real engine.

    User photos are copied to a temporary folder first and their checksum is
    verified afterwards, so the originals are never touched.
    """
    from . import engine
    from PIL import Image

    from .spec import resolved_adjustments

    setup_logging(workspace)
    lines = [f"M7 Studio Editor self-test — {_dt.datetime.now():%Y-%m-%d %H:%M:%S}",
             f"Version: {VERSION}", f"System: {platform.platform()}",
             f"Python: {platform.python_version()} ({'packaged' if getattr(sys, 'frozen', False) else 'source'})"]
    ok = True
    try:
        binary = engine.find_binary()
        lines.append(f"Engine: {binary}")
    except engine.EngineError as e:
        lines += [f"FAIL engine not found: {e}"]
        return False, "\n".join(lines)

    spec = {"presets": [{"name": "cinematic-sports", "strength": 0.8}]}
    with tempfile.TemporaryDirectory(prefix="m7-selftest-") as tmp:
        tmp = Path(tmp)
        jobs = []
        sample = tmp / "sample.jpg"
        _sample_jpeg(sample)
        jobs.append(("Built-in JPEG sample", sample, None))
        for p in photos or []:
            p = Path(p)
            if not p.is_file():
                lines.append(f"FAIL {p}: file not found")
                ok = False
                continue
            before = sha256(p)
            copy = tmp / ("user-" + p.name)
            shutil.copy2(p, copy)
            jobs.append((f"Your photo {p.name}", copy, (p, before)))
        for label, src, original in jobs:
            for kind, long_edge, fmt in (("preview", 1600, "jpeg"), ("full-resolution", None, "tiff")):
                out = tmp / f"{src.stem}-{kind}.{'jpg' if fmt == 'jpeg' else 'tiff'}"
                t0 = time.time()
                try:
                    res = engine.render(src, resolved_adjustments(spec), out, fmt=fmt,
                                        long_edge=long_edge, timeout=1800)
                    with Image.open(res.output) as im:
                        size = im.size
                    adapter = gpu_adapter(res.log)
                    lines.append(f"PASS {label}: {kind} {fmt.upper()} {size[0]}x{size[1]} "
                                 f"in {time.time() - t0:.1f}s"
                                 + (f" (GPU: {adapter})" if adapter else ""))
                except Exception as e:  # report every failure, keep testing the rest
                    ok = False
                    lines.append(f"FAIL {label}: {kind} {fmt.upper()} after {time.time() - t0:.1f}s\n"
                                 + "\n".join("     " + ln for ln in str(e).splitlines()[:12]))
            if original:
                p, before = original
                same = sha256(p) == before
                ok &= same
                lines.append(("PASS" if same else "FAIL") + f" original {p.name} unchanged "
                             f"(SHA-256 {before[:16]}…)")
    lines.append("RESULT: " + ("ALL PASSED" if ok else "SOME CHECKS FAILED"))
    report = "\n".join(lines)
    try:
        (setup_logging(workspace) / "self-test-report.txt").write_text(report + "\n", encoding="utf-8")
    except OSError:
        pass
    return ok, report
