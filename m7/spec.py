"""M7 edit specification and its compilation into RapidRAW adjustments.

An M7 edit spec is a small, human/AI-writable JSON document:

    {
      "m7Version": 1,
      "presets": [{"name": "cinematic-sports", "strength": 0.8}],
      "adjustments": {"exposure": 0.3, "hsl": {"blues": {"saturation": -20}}},
      "crop": {"aspect": "4:5"},             # or {"x":0.1,"y":0,"w":0.8,"h":1}
      "straighten": -1.5,                    # degrees, positive = clockwise
      "lensCorrection": "auto",              # or "off"
      "masks": [ {"type": "radial", ...}, {"type": "subject", ...} ]
    }

``adjustments`` use RapidRAW's own key names and slider units (see
``RANGES``), so whatever the desktop app can do, the spec can express.
Presets are applied first (scaled by strength), then ``adjustments`` are
merged on top (explicit values win).
"""

from __future__ import annotations

import copy
import json
import math
import uuid
from pathlib import Path

PRESET_DIR = Path(__file__).resolve().parent / "presets"

# Slider ranges as exposed by the RapidRAW UI.
RANGES: dict[str, tuple[float, float]] = {
    "exposure": (-5, 5),
    "brightness": (-5, 5),
    "contrast": (-100, 100),
    "highlights": (-100, 100),
    "shadows": (-100, 100),
    "whites": (-100, 100),
    "blacks": (-100, 100),
    "temperature": (-100, 100),
    "tint": (-100, 100),
    "saturation": (-100, 100),
    "vibrance": (-100, 100),
    "clarity": (-100, 100),
    "structure": (-100, 100),
    "dehaze": (-100, 100),
    "sharpness": (0, 100),
    "sharpnessThreshold": (0, 100),
    "lumaNoiseReduction": (0, 100),
    "colorNoiseReduction": (0, 100),
    "vignetteAmount": (-100, 100),
    "vignetteMidpoint": (0, 100),
    "vignetteFeather": (0, 100),
    "vignetteRoundness": (-100, 100),
    "grainAmount": (0, 100),
    "grainSize": (0, 100),
    "grainRoughness": (0, 100),
    "glowAmount": (0, 100),
    "halationAmount": (0, 100),
    "chromaticAberrationRedCyan": (-100, 100),
    "chromaticAberrationBlueYellow": (-100, 100),
    "rotation": (-45, 45),
    "lensDistortionAmount": (0, 200),
    "lensVignetteAmount": (0, 200),
    "lensTcaAmount": (0, 200),
    "transformVertical": (-100, 100),
    "transformHorizontal": (-100, 100),
}
NESTED_KEYS = {
    "hsl", "colorGrading", "curves", "pointCurves", "monochromeMix", "monochromeToning",
    "colorCalibration", "parametricCurve",
}
FLAG_KEYS = {
    "monochromeEnabled", "lensDistortionEnabled", "lensTcaEnabled", "lensVignetteEnabled",
    "flipHorizontal", "flipVertical",
}
STRING_KEYS = {"toneMapper", "curveMode", "lensCorrectionMode", "monochromeFilm"}
HSL_COLORS = ["reds", "oranges", "yellows", "greens", "aquas", "blues", "purples", "magentas"]
GRADING_ZONES = ["shadows", "midtones", "highlights", "global"]

# Keys whose neutral value is not 0 (needed to scale presets by strength).
NEUTRAL = {
    "sharpnessThreshold": 15, "vignetteFeather": 50, "vignetteMidpoint": 50,
    "grainSize": 25, "grainRoughness": 50,
}
# Keys that describe "where" rather than "how much": never scaled by strength.
UNSCALED = {
    "hue", "highlightHue", "shadowHue", "monochromeFilterHue", "blending", "balance",
    "sharpnessThreshold", "vignetteFeather", "vignetteMidpoint", "vignetteRoundness",
    "grainSize", "grainRoughness", "split1", "split2", "split3",
}


class SpecError(ValueError):
    pass


def new_spec() -> dict:
    return {"m7Version": 1, "presets": [], "adjustments": {}}


# ---------------------------------------------------------------- presets

def list_presets() -> list[dict]:
    out = []
    for p in sorted(PRESET_DIR.glob("*.json")):
        data = json.loads(p.read_text())
        out.append({"name": p.stem, "title": data.get("title", p.stem),
                    "description": data.get("description", "")})
    return out


def load_preset(name: str) -> dict:
    key = name.strip().lower().replace(" ", "-").replace("_", "-")
    path = PRESET_DIR / f"{key}.json"
    if not path.exists():
        user_dir = Path.home() / ".m7" / "presets"
        path = user_dir / f"{key}.json"
    if not path.exists():
        names = ", ".join(p["name"] for p in list_presets())
        raise SpecError(f"Unknown preset {name!r}. Available: {names}")
    return json.loads(path.read_text())


def save_user_preset(name: str, adjustments: dict, title: str = "", description: str = "") -> Path:
    user_dir = Path.home() / ".m7" / "presets"
    user_dir.mkdir(parents=True, exist_ok=True)
    key = name.strip().lower().replace(" ", "-")
    path = user_dir / f"{key}.json"
    path.write_text(json.dumps({"title": title or name, "description": description,
                                "adjustments": adjustments}, indent=2))
    return path


def _scale(value, strength: float, key: str):
    if isinstance(value, bool) or key in UNSCALED:
        return value
    if isinstance(value, (int, float)):
        neutral = NEUTRAL.get(key, 0)
        return neutral + (value - neutral) * strength
    if isinstance(value, dict):
        return {k: _scale(v, strength, k) for k, v in value.items()}
    if isinstance(value, list):
        # Curve points: pull y towards the identity line.
        if value and isinstance(value[0], dict) and "x" in value[0]:
            return [{"x": p["x"], "y": p["x"] + (p["y"] - p["x"]) * strength} for p in value]
        return value
    return value


def scaled_preset(name: str, strength: float = 1.0) -> dict:
    if not 0 <= strength <= 1.5:
        raise SpecError("Preset strength must be between 0 and 1.5")
    adj = copy.deepcopy(load_preset(name)["adjustments"])
    return {k: _scale(v, strength, k) for k, v in adj.items()}


# ---------------------------------------------------------------- merging

def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _clamp(key: str, value):
    lo, hi = RANGES[key]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SpecError(f"{key} must be a number")
    return max(lo, min(hi, float(value)))


def validate_adjustments(adj: dict) -> dict:
    """Clamp to slider ranges and reject unknown keys (typos would silently no-op)."""
    out = {}
    for key, value in adj.items():
        if key in RANGES:
            out[key] = _clamp(key, value)
        elif key in FLAG_KEYS:
            out[key] = bool(value)
        elif key in STRING_KEYS:
            out[key] = str(value)
        elif key in NESTED_KEYS:
            if not isinstance(value, dict):
                raise SpecError(f"{key} must be an object")
            out[key] = _validate_nested(key, value)
        else:
            raise SpecError(f"Unknown adjustment {key!r}")
    return out


def _clip(v, lo, hi, name):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        raise SpecError(f"{name} must be a number")
    return max(lo, min(hi, float(v)))


def _validate_nested(key: str, value: dict) -> dict:
    out = {}
    if key == "hsl":
        for color, hsl in value.items():
            if color not in HSL_COLORS:
                raise SpecError(f"Unknown HSL colour {color!r}; use {HSL_COLORS}")
            out[color] = {c: _clip(hsl[c], -100, 100, f"hsl.{color}.{c}")
                          for c in ("hue", "saturation", "luminance") if c in hsl}
    elif key == "colorGrading":
        for zone, v in value.items():
            if zone in GRADING_ZONES:
                z = {}
                if "hue" in v:
                    z["hue"] = float(v["hue"]) % 360
                if "saturation" in v:
                    z["saturation"] = _clip(v["saturation"], 0, 100, f"{zone}.saturation")
                if "luminance" in v:
                    z["luminance"] = _clip(v["luminance"], -100, 100, f"{zone}.luminance")
                out[zone] = z
            elif zone == "blending":
                out[zone] = _clip(v, 0, 100, "blending")
            elif zone == "balance":
                out[zone] = _clip(v, -100, 100, "balance")
            else:
                raise SpecError(f"Unknown colorGrading field {zone!r}")
    elif key in ("curves", "pointCurves"):
        for ch, pts in value.items():
            if ch not in ("luma", "red", "green", "blue"):
                raise SpecError(f"Unknown curve channel {ch!r}")
            if not isinstance(pts, list) or len(pts) < 2:
                raise SpecError("A curve needs at least two points")
            pts = sorted(({"x": _clip(p["x"], 0, 255, "x"), "y": _clip(p["y"], 0, 255, "y")}
                          for p in pts), key=lambda p: p["x"])
            out[ch] = pts
    else:
        out = copy.deepcopy(value)
    return out


# ---------------------------------------------------------------- geometry

def _aspect(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    a, b = str(value).replace("x", ":").split(":")
    return float(a) / float(b)


def inscribed_crop(width: int, height: int, angle_deg: float, aspect: float | None,
                   zoom: float = 1.0) -> dict:
    """Largest centred crop of ``aspect`` that fits once the image is rotated.

    RapidRAW rotates about the centre and keeps the canvas size, so the crop is
    expressed in canvas pixels. The crop rectangle, rotated back into the
    original frame, must fit inside width x height.
    """
    t = math.radians(abs(angle_deg))
    c, s = math.cos(t), math.sin(t)
    a = aspect if aspect else width / height
    # Solve for the largest h with w = a*h.
    h = min(height, width / a,
            width / (a * c + s), height / (a * s + c))
    h *= zoom
    w = a * h
    return {"x": (width - w) / 2, "y": (height - h) / 2, "width": w, "height": h}


def _compile_crop(spec: dict, width: int, height: int) -> dict | None:
    crop = spec.get("crop")
    angle = float(spec.get("straighten", 0) or 0)
    if not crop and abs(angle) < 1e-6:
        return None
    if crop and all(k in crop for k in ("x", "y", "w", "h")):
        x, y, w, h = (float(crop[k]) for k in ("x", "y", "w", "h"))
        if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 - x + 1e-9 and 0 < h <= 1 - y + 1e-9):
            raise SpecError("Normalised crop must stay inside 0..1")
        return {"x": x * width, "y": y * height, "width": w * width, "height": h * height}
    aspect = _aspect(crop["aspect"]) if crop and crop.get("aspect") else None
    zoom = float(crop.get("zoom", 1.0)) if crop else 1.0
    return inscribed_crop(width, height, angle, aspect, zoom)


def _compile_mask(m: dict, width: int, height: int, index: int) -> dict:
    mtype = m.get("type")
    long_side = max(width, height)
    if mtype == "radial":
        params = {
            "centerX": float(m.get("centerX", 0.5)) * width,
            "centerY": float(m.get("centerY", 0.5)) * height,
            "radiusX": float(m.get("radiusX", 0.3)) * width,
            "radiusY": float(m.get("radiusY", 0.3)) * height,
            "rotation": float(m.get("rotation", 0)),
            "feather": float(m.get("feather", 0.5)),
        }
        sub = {"type": "radial", "parameters": params}
    elif mtype == "linear":
        params = {
            "startX": float(m.get("startX", 0.5)) * width,
            "startY": float(m.get("startY", 0.0)) * height,
            "endX": float(m.get("endX", 0.5)) * width,
            "endY": float(m.get("endY", 0.5)) * height,
            "range": float(m.get("range", 0.1)) * long_side,
        }
        sub = {"type": "linear", "parameters": params}
    elif mtype in ("subject", "background"):
        # RapidRAW's AI foreground segmentation; models download on first use.
        sub = {"type": "ai-foreground", "parameters": {}}
    elif mtype == "sky":
        sub = {"type": "ai-sky", "parameters": {}}
    elif mtype == "all":
        sub = {"type": "all", "parameters": {}}
    else:
        raise SpecError(f"Unknown mask type {mtype!r}; use radial, linear, subject, background, sky")
    invert = bool(m.get("invert", False)) ^ (mtype == "background")
    sub.update({"id": str(uuid.uuid4()), "visible": True, "invert": False,
                "opacity": 100.0, "mode": "additive"})
    return {
        "id": str(uuid.uuid4()),
        "name": m.get("name", f"M7 mask {index + 1}"),
        "visible": True,
        "invert": invert,
        "opacity": float(m.get("opacity", 100)),
        "adjustments": validate_adjustments(m.get("adjustments", {})),
        "subMasks": [sub],
    }


# ---------------------------------------------------------------- compile

def resolved_adjustments(spec: dict) -> dict:
    """Presets (scaled) + explicit adjustments, validated. No geometry."""
    merged: dict = {}
    for p in spec.get("presets", []):
        merged = deep_merge(merged, scaled_preset(p["name"], float(p.get("strength", 1.0))))
    merged = deep_merge(merged, spec.get("adjustments", {}))
    return validate_adjustments(merged)


def compile_spec(spec: dict, width: int, height: int) -> dict:
    """Return RapidRAW adjustment JSON for an image of the given oriented size."""
    if spec.get("m7Version") != 1:
        raise SpecError("Spec must have m7Version: 1")
    adj = resolved_adjustments(spec)
    angle = float(spec.get("straighten", 0) or 0)
    if abs(angle) > 45:
        raise SpecError("straighten must be within ±45°")
    if abs(angle) > 1e-6:
        adj["rotation"] = angle
    crop = _compile_crop(spec, width, height)
    if crop:
        adj["crop"] = crop
    lens = spec.get("lensCorrection")
    if lens == "auto":
        adj.update({"lensCorrectionMode": "auto", "lensDistortionEnabled": True,
                    "lensTcaEnabled": True, "lensVignetteEnabled": True})
    masks = spec.get("masks") or []
    if masks:
        adj["masks"] = [_compile_mask(m, width, height, i) for i, m in enumerate(masks)]
    return adj
