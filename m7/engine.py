"""Thin wrapper around RapidRAW's headless export command."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

RAW_EXTENSIONS = {".arw", ".srf", ".sr2", ".cr2", ".cr3", ".nef", ".raf", ".dng", ".orf", ".rw2", ".pef"}
SUPPORTED_EXTENSIONS = RAW_EXTENSIONS | {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

FORMAT_EXT = {"jpeg": ".jpg", "png": ".png", "tiff": ".tiff"}


class EngineError(RuntimeError):
    pass


def find_binary() -> Path:
    """Locate the RapidRAW executable.

    Order: $M7_RAPIDRAW_BIN, a local cargo build in this repo, then PATH.
    """
    env = os.environ.get("M7_RAPIDRAW_BIN")
    if env:
        p = Path(env)
        if p.is_file():
            return p
        raise EngineError(f"M7_RAPIDRAW_BIN points to a missing file: {env}")
    names = ["RapidRAW", "RapidRAW.exe", "rapidraw", "rapid-raw"]
    candidates = []
    # Packaged M7 (Windows installer): engine sits next to the launcher.
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates += [exe_dir / "engine" / n for n in names] + [exe_dir / n for n in names]
    for profile in ("release", "debug"):
        candidates += [REPO_ROOT / "src-tauri" / "target" / profile / n for n in names]
    if os.name == "nt":
        for var in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(var)
            if base:
                candidates += [Path(base) / d / "RapidRAW.exe" for d in ("RapidRAW", "Programs/RapidRAW")]
    for p in candidates:
        if p.is_file():
            return p
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    raise EngineError(
        "RapidRAW binary not found. Build it with `cd src-tauri && cargo build --release` "
        "or set M7_RAPIDRAW_BIN."
    )


@dataclass
class RenderResult:
    output: Path
    seconds: float
    log: str


def _needs_virtual_display() -> bool:
    if os.name != "posix" or os.uname().sysname != "Linux":
        return False
    return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def render(
    source: Path,
    adjustments: dict,
    output: Path,
    fmt: str = "jpeg",
    quality: int = 95,
    long_edge: int | None = None,
    tiff_bit_depth: int = 16,
    keep_metadata: bool = True,
    timeout: int = 1800,
) -> RenderResult:
    """Render ``source`` with RapidRAW using ``adjustments``; never touches ``source``."""
    source = Path(source).resolve()
    output = Path(output).resolve()
    if not source.is_file():
        raise EngineError(f"Source image not found: {source}")
    if output == source:
        raise EngineError("Refusing to overwrite the original photograph.")
    if fmt not in FORMAT_EXT:
        raise EngineError(f"Unsupported output format {fmt!r}; use jpeg, png or tiff.")
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="m7-") as tmp:
        adj_path = Path(tmp) / "adjustments.json"
        adj_path.write_text(json.dumps(adjustments))
        # Export into an empty temp dir first so a failed render never leaves a
        # half-written file at the destination.
        tmp_out = Path(tmp) / ("render" + FORMAT_EXT[fmt])
        cmd = [
            str(find_binary()), "export", str(source),
            "--output", str(tmp_out),
            "--format", fmt,
            "--quality", str(quality),
            "--adjustments", str(adj_path),
            "--on-conflict", "overwrite",
        ]
        if fmt == "tiff":
            cmd += ["--tiff-bit-depth", str(tiff_bit_depth)]
        if keep_metadata:
            cmd.append("--keep-metadata")
        if long_edge:
            cmd += ["--long-edge", str(int(long_edge))]
        if _needs_virtual_display():
            if not shutil.which("xvfb-run"):
                raise EngineError("No display available and xvfb-run is not installed.")
            cmd = ["xvfb-run", "-a", "-s", "-screen 0 1280x720x24"] + cmd

        env = dict(os.environ)
        env.setdefault("RUST_LOG", "warn")
        # Large files on slow or software GPUs can exceed RapidRAW's default
        # 60 s GPU wait; allow up to 10 minutes before reporting an error.
        env.setdefault("RAPIDRAW_GPU_TIMEOUT_SECS", "600")
        start = time.time()
        extra = {"creationflags": 0x08000000} if os.name == "nt" else {}  # CREATE_NO_WINDOW
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env,
                              encoding="utf-8", errors="replace", **extra)
        elapsed = time.time() - start
        log = (proc.stdout or "") + (proc.stderr or "")
        # Judge success by exit code + output file (a GUI-subsystem build on
        # Windows may not forward console text).
        if proc.returncode != 0 or "export failed" in log.lower() or not tmp_out.exists():
            raise EngineError(f"RapidRAW export failed (exit {proc.returncode}):\n{log[-4000:]}")
        shutil.move(str(tmp_out), str(output))
    return RenderResult(output=output, seconds=elapsed, log=log)
