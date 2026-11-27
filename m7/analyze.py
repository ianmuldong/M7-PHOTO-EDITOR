"""Measure a rendered image so instructions can be grounded in the actual photo."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def _load(path: Path, max_side: int = 1024) -> np.ndarray:
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        return np.asarray(im).astype(np.float32) / 255.0


def analyze(path: Path) -> dict:
    rgb = _load(Path(path))
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    luma = 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]
    l_srgb = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    mx, mn = rgb.max(-1), rgb.min(-1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    p = np.percentile(l_srgb, [1, 5, 50, 95, 99])
    # Grey-world colour cast estimate on mid-tones only.
    mid = (l_srgb > 0.2) & (l_srgb < 0.8)
    means = rgb[mid].mean(0) if mid.any() else rgb.reshape(-1, 3).mean(0)
    warm = float(means[0] - means[2])
    green = float(means[1] - (means[0] + means[2]) / 2)
    stats = {
        "meanLuminance": round(float(l_srgb.mean()), 4),
        "medianLuminance": round(float(p[2]), 4),
        "p1": round(float(p[0]), 4), "p99": round(float(p[4]), 4),
        "contrastSpread": round(float(p[3] - p[1]), 4),
        "clippedHighlightsPct": round(float((mx >= 0.995).mean() * 100), 3),
        "crushedShadowsPct": round(float((mx <= 0.005).mean() * 100), 3),
        "meanSaturation": round(float(sat.mean()), 4),
        "warmCoolBias": round(warm, 4),
        "greenMagentaBias": round(green, 4),
        "sceneLinearMean": round(float(luma.mean()), 4),
    }
    stats["notes"] = describe(stats)
    return stats


def describe(s: dict) -> list[str]:
    notes = []
    if s["medianLuminance"] < 0.25:
        notes.append("underexposed / low-key")
    elif s["medianLuminance"] > 0.65:
        notes.append("bright / high-key")
    if s["clippedHighlightsPct"] > 1.0:
        notes.append(f"{s['clippedHighlightsPct']}% clipped highlights")
    if s["crushedShadowsPct"] > 2.0:
        notes.append(f"{s['crushedShadowsPct']}% crushed shadows")
    if s["contrastSpread"] < 0.45:
        notes.append("flat / low contrast")
    if s["warmCoolBias"] > 0.06:
        notes.append("warm colour cast")
    elif s["warmCoolBias"] < -0.06:
        notes.append("cool colour cast")
    if s["greenMagentaBias"] > 0.03:
        notes.append("green tint")
    elif s["greenMagentaBias"] < -0.03:
        notes.append("magenta tint")
    if s["meanSaturation"] < 0.12:
        notes.append("muted colours")
    return notes


def suggest(s: dict) -> dict:
    """Conservative auto-correction suggestion (RapidRAW units)."""
    adj: dict = {}
    m = max(s["medianLuminance"], 0.02)
    # Scene-aware target: genuinely low-key scenes (night, dusk, dark gyms)
    # should stay darker than mid-grey; bright scenes may stay bright.
    target = 0.42 if 0.25 <= m <= 0.65 else (0.32 if m < 0.25 else 0.55)
    if abs(m - target) > 0.06:
        # sRGB median -> stops, damped so we never overshoot.
        stops = np.log2((target / m) ** 2.2) * 0.5
        # Don't brighten into clipping.
        hi = 1.0 if s["p99"] < 0.9 else 0.5
        adj["exposure"] = round(float(np.clip(stops, -1.0, hi)), 2)
    if s["clippedHighlightsPct"] > 1.0:
        adj["highlights"] = -min(60, round(10 + s["clippedHighlightsPct"] * 5))
    if s["crushedShadowsPct"] > 2.0:
        adj["shadows"] = min(50, round(10 + s["crushedShadowsPct"] * 3))
    if abs(s["warmCoolBias"]) > 0.06:
        adj["temperature"] = round(float(np.clip(-s["warmCoolBias"] * 120, -25, 25)))
    if abs(s["greenMagentaBias"]) > 0.03:
        adj["tint"] = round(float(np.clip(s["greenMagentaBias"] * 150, -20, 20)))
    return adj
