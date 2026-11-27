"""Double-click entry point for the packaged M7 Studio Editor."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def _workspace() -> Path:
    pictures = Path.home() / "Pictures"
    return (pictures if pictures.is_dir() else Path.home()) / "M7 Studio"


def _alert(msg: str):
    print(msg)
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg, "M7 Studio Editor", 0x10)
            return
        except Exception:
            pass
    try:
        input("Press Enter to close...")
    except EOFError:
        pass


def _self_test(photos):
    from m7 import diagnostics
    print("M7 Studio Editor self-test: rendering with your graphics card. This can take a few minutes.")
    print("Your photos are copied to a temporary folder first; the originals are never changed.\n")
    ok, report = diagnostics.self_test(_workspace(), [Path(p) for p in photos])
    print(report)
    print(f"\nReport saved to: {_workspace() / 'logs' / 'self-test-report.txt'}")
    if sys.stdin and sys.stdin.isatty():
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass
    sys.exit(0 if ok else 1)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        return _self_test(sys.argv[2:])
    # Imported here so packaging problems surface in the alert below.
    from m7 import engine
    from m7.server import serve
    print("=" * 60)
    print(" M7 Studio Editor")
    print(" Your browser will open automatically.")
    print(" Keep this window open while editing; close it to quit.")
    print("=" * 60)
    try:
        serve(_workspace(), 7777, open_browser=not os.environ.get("M7_NO_BROWSER"))
    except engine.EngineError as e:
        _alert(f"M7 could not find its photo engine (RapidRAW).\n\n{e}\n\n"
               "Please re-run the M7 Studio installer.")
        sys.exit(1)
    except KeyboardInterrupt:
        pass
    except Exception:
        _alert("M7 Studio Editor stopped unexpectedly:\n\n" + traceback.format_exc()
               + f"\n\nLogs: {_workspace() / 'logs'}")
        sys.exit(1)


if __name__ == "__main__":
    main()
