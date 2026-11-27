"""M7 Studio Editor command line.

    python -m m7 render photo.ARW --spec edit.json --out edited.jpg   # one-shot
    python -m m7 open photo.ARW                                        # start session
    python -m m7 say  photo.ARW "cinematic sports at 80%, lift shadows"
    python -m m7 undo photo.ARW | redo | history | show
    python -m m7 export photo.ARW out/final.tiff
    python -m m7 batch ./shoot --preset indoor-volleyball --out ./edited
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from . import engine, nl
from . import spec as m7spec
from .analyze import analyze
from .session import DEFAULT_WORKSPACE, Session


def _session(target: str, workspace: str | None) -> Session:
    p = Path(target)
    if p.is_dir() and (p / "session.json").exists():
        return Session(p)
    return Session.open(p, Path(workspace) if workspace else None)


def _report(s: Session, notes=None, render=True):
    out = {"session": str(s.folder), "step": s.data["cursor"],
           "note": s.data["history"][s.data["cursor"]]["note"], "spec": s.spec}
    if notes:
        out["interpreted"] = notes
    if render:
        out["preview"] = str(s.render_preview())
        out["compare"] = str(s.compare())
    print(json.dumps(out, indent=2))


def _parse_value(v: str):
    try:
        return json.loads(v)
    except json.JSONDecodeError:
        return v


def _load_spec(args) -> dict:
    spec = m7spec.new_spec()
    if args.spec:
        spec = json.loads(Path(args.spec).read_text())
    if getattr(args, "preset", None):
        spec["presets"] = [{"name": args.preset, "strength": args.strength}]
    for kv in getattr(args, "set", None) or []:
        k, v = kv.split("=", 1)
        nl._set(spec.setdefault("adjustments", {}), k.split("."), _parse_value(v))
    return spec


def _dims(photo: Path, folder: Path) -> tuple[int, int]:
    if photo.suffix.lower() in engine.RAW_EXTENSIONS:
        s = Session.open(photo, folder)
        return s.size
    from PIL import Image, ImageOps
    with Image.open(photo) as im:
        im = ImageOps.exif_transpose(im)
        return im.size


def cmd_render(a):
    photo = Path(a.photo)
    spec = _load_spec(a)
    w, h = _dims(photo, Path(a.workspace) if a.workspace else DEFAULT_WORKSPACE)
    adj = m7spec.compile_spec(spec, w, h)
    out = Path(a.out)
    fmt = {".png": "png", ".tif": "tiff", ".tiff": "tiff"}.get(out.suffix.lower(), "jpeg")
    res = engine.render(photo, adj, out, fmt=fmt, quality=a.quality, long_edge=a.long_edge,
                        tiff_bit_depth=a.bit_depth)
    print(json.dumps({"output": str(res.output), "seconds": round(res.seconds, 1),
                      "size": [w, h]}, indent=2))


def cmd_open(a):
    s = _session(a.photo, a.workspace)
    print(json.dumps({"session": str(s.folder), "size": s.size, "analysis": s.data.get("analysis"),
                      "before": str(s.folder / "before.jpg")}, indent=2))


def cmd_say(a):
    s = _session(a.photo, a.workspace)
    change, notes = nl.interpret(a.text, s.data.get("analysis"))
    if not any(k in change for k in ("add", "set", "presets", "crop", "straighten",
                                       "lensCorrection", "masks", "reset")):
        print(json.dumps({"interpreted": notes, "error": "Nothing to change."}, indent=2))
        return 1
    s.commit(nl.apply_change(s.spec, change), a.text)
    _report(s, notes, render=not a.no_render)


def cmd_apply(a):
    s = _session(a.photo, a.workspace)
    change = json.loads(Path(a.change).read_text() if a.change != "-" else sys.stdin.read())
    s.commit(nl.apply_change(s.spec, change), a.note or "applied change")
    _report(s, render=not a.no_render)


def cmd_setspec(a):
    s = _session(a.photo, a.workspace)
    s.commit(json.loads(Path(a.spec).read_text()), a.note or "spec replaced")
    _report(s, render=not a.no_render)


def cmd_undo(a):
    s = _session(a.photo, a.workspace)
    if not s.undo():
        print("Nothing to undo.")
    _report(s, render=not a.no_render)


def cmd_redo(a):
    s = _session(a.photo, a.workspace)
    if not s.redo():
        print("Nothing to redo.")
    _report(s, render=not a.no_render)


def cmd_history(a):
    s = _session(a.photo, a.workspace)
    for e in s.history():
        print(("→ " if e["current"] else "  ") + f"{e['step']:3d}  {e['note']}")


def cmd_show(a):
    s = _session(a.photo, a.workspace)
    _report(s, render=not a.no_render)


def cmd_export(a):
    s = _session(a.photo, a.workspace)
    res = s.export(Path(a.out), quality=a.quality, tiff_bit_depth=a.bit_depth)
    from PIL import Image
    with Image.open(res.output) as im:
        size = im.size
    print(json.dumps({"output": str(res.output), "size": size, "seconds": round(res.seconds, 1)}, indent=2))


def cmd_batch(a):
    src = Path(a.folder)
    files = sorted(p for p in src.iterdir() if p.suffix.lower() in engine.SUPPORTED_EXTENSIONS)
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = _load_spec(a)
    results = []
    for f in files:
        try:
            w, h = _dims(f, Path(a.workspace) if a.workspace else DEFAULT_WORKSPACE)
            adj = m7spec.compile_spec(spec, w, h)
            target = out_dir / (f.stem + {"jpeg": ".jpg", "tiff": ".tiff", "png": ".png"}[a.format])
            r = engine.render(f, adj, target, fmt=a.format, quality=a.quality, tiff_bit_depth=a.bit_depth)
            results.append({"file": f.name, "output": str(r.output), "seconds": round(r.seconds, 1)})
        except Exception as e:  # keep going; report per file
            results.append({"file": f.name, "error": str(e)[-500:]})
    print(json.dumps(results, indent=2))
    return 1 if any("error" in r for r in results) else 0


def cmd_presets(a):
    for p in m7spec.list_presets():
        print(f"{p['name']:28s} {p['title']}\n{'':28s} {p['description']}")


def cmd_save_preset(a):
    s = _session(a.photo, a.workspace)
    path = m7spec.save_user_preset(a.name, m7spec.resolved_adjustments(s.spec), description=a.description)
    print(f"Saved preset to {path}")


def cmd_analyze(a):
    p = Path(a.image)
    if p.suffix.lower() in engine.RAW_EXTENSIONS:
        s = _session(a.image, a.workspace)
        print(json.dumps(s.data["analysis"], indent=2))
    else:
        print(json.dumps(analyze(p), indent=2))


def cmd_interpret(a):
    change, notes = nl.interpret(a.text)
    print(json.dumps({"change": change, "interpreted": notes}, indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="m7", description="M7 Studio Editor")
    ap.add_argument("--workspace", help=f"session folder root (default {DEFAULT_WORKSPACE})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def spec_args(p):
        p.add_argument("--spec", help="M7 spec JSON file")
        p.add_argument("--preset")
        p.add_argument("--strength", type=float, default=1.0)
        p.add_argument("--set", action="append", help="key=value, e.g. exposure=0.4 or hsl.blues.saturation=-20")

    def out_args(p):
        p.add_argument("--quality", type=int, default=95)
        p.add_argument("--bit-depth", type=int, default=16, choices=[8, 16])

    p = sub.add_parser("render", help="one-shot: photo + parameters -> edited file")
    p.add_argument("photo"); p.add_argument("--out", required=True)
    p.add_argument("--long-edge", type=int)
    spec_args(p); out_args(p); p.set_defaults(fn=cmd_render)

    p = sub.add_parser("open"); p.add_argument("photo"); p.set_defaults(fn=cmd_open)
    p = sub.add_parser("say", help="natural-language edit")
    p.add_argument("photo"); p.add_argument("text"); p.add_argument("--no-render", action="store_true")
    p.set_defaults(fn=cmd_say)
    p = sub.add_parser("apply", help="apply a Change JSON (as written by Claude)")
    p.add_argument("photo"); p.add_argument("change"); p.add_argument("--note")
    p.add_argument("--no-render", action="store_true"); p.set_defaults(fn=cmd_apply)
    p = sub.add_parser("set-spec", help="replace the whole spec")
    p.add_argument("photo"); p.add_argument("spec"); p.add_argument("--note")
    p.add_argument("--no-render", action="store_true"); p.set_defaults(fn=cmd_setspec)
    for name, fn in (("undo", cmd_undo), ("redo", cmd_redo), ("show", cmd_show)):
        p = sub.add_parser(name); p.add_argument("photo"); p.add_argument("--no-render", action="store_true")
        p.set_defaults(fn=fn)
    p = sub.add_parser("history"); p.add_argument("photo"); p.set_defaults(fn=cmd_history)
    p = sub.add_parser("export", help="full-resolution export of the current step")
    p.add_argument("photo"); p.add_argument("out"); out_args(p); p.set_defaults(fn=cmd_export)
    p = sub.add_parser("batch"); p.add_argument("folder"); p.add_argument("--out", required=True)
    p.add_argument("--format", default="jpeg", choices=["jpeg", "tiff", "png"])
    spec_args(p); out_args(p); p.set_defaults(fn=cmd_batch)
    p = sub.add_parser("presets"); p.set_defaults(fn=cmd_presets)
    p = sub.add_parser("save-preset"); p.add_argument("photo"); p.add_argument("name")
    p.add_argument("--description", default=""); p.set_defaults(fn=cmd_save_preset)
    p = sub.add_parser("analyze"); p.add_argument("image"); p.set_defaults(fn=cmd_analyze)
    p = sub.add_parser("interpret", help="show how text is interpreted, no rendering")
    p.add_argument("text"); p.set_defaults(fn=cmd_interpret)

    a = ap.parse_args(argv)
    try:
        return a.fn(a) or 0
    except (engine.EngineError, m7spec.SpecError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
