"""Offline natural-language -> edit-change interpreter.

This is deliberately rule-based so the local app works with no network and
no paid API. When you work through Claude Code, Claude writes the change JSON
directly (richer understanding); both paths produce the same ``Change``
structure, which ``apply_change`` folds into an M7 spec.

Change structure:
    {
      "add":   {"exposure": 0.3, "hsl": {"blues": {"saturation": -10}}},  # relative
      "set":   {"monochromeEnabled": true},                               # absolute
      "presets": [{"name": "cinematic-sports", "strength": 0.8}],         # replaces
      "crop": {"aspect": "4:5"}, "straighten": 1.5,
      "lensCorrection": "auto",
      "masks": [{"type": "subject", "adjustments": {"exposure": 0.4}}],   # appended
      "reset": false, "auto": false
    }
"""

from __future__ import annotations

import copy
import re

from . import spec as m7spec
from .analyze import suggest

PRESET_ALIASES = [
    (r"cinematic sports?|sports? cinematic|game[- ]day", "cinematic-sports"),
    (r"nike|commercial sports?|athletic (ad|advert|commercial)", "nike-commercial"),
    (r"volleyball|indoor sports?|gym(nasium)?", "indoor-volleyball"),
    (r"dark (athletic )?editorial|moody editorial|dark athletic", "dark-athletic-editorial"),
    (r"(natural|professional) portrait|portrait look|headshot", "natural-portrait"),
    (r"golden[- ]hour|sunset look|golden cinematic", "golden-hour-cinematic"),
    (r"clean commercial|product shot|corporate|clean look", "clean-commercial"),
    (r"high[- ]contrast (mono|monochrome|b&w|black and white)|(mono|monochrome) high[- ]contrast", "high-contrast-monochrome"),
    (r"low[- ]light|high iso|night (game|sports?)|recover (the )?dark", "low-light-sports-recovery"),
    (r"vibrant|social media|instagram look|pop for social", "vibrant-social"),
]

COLORS = {
    "red": "reds", "orange": "oranges", "yellow": "yellows", "green": "greens",
    "aqua": "aquas", "cyan": "aquas", "teal": "aquas", "blue": "blues", "sky": "blues",
    "purple": "purples", "magenta": "magentas", "pink": "magentas", "skin": "oranges",
}

ASPECTS = {
    "square": "1:1", "instagram portrait": "4:5", "instagram": "4:5", "portrait": "4:5",
    "story": "9:16", "stories": "9:16", "reel": "9:16", "tiktok": "9:16",
    "widescreen": "16:9", "cinematic crop": "2.39:1", "landscape": "3:2",
}


def _intensity(text: str) -> float:
    t = text
    if re.search(r"\b(slight(ly)?|a (little )?bit|a touch|subtle|subtly|gently|a little|tiny)\b", t):
        return 0.5
    if re.search(r"\b(much|a lot|lots|very|really|strong(ly)?|heavy|heavily|way|significantly|dramatic(ally)?)\b", t):
        return 2.0
    return 1.0


def _clauses(text: str) -> list[str]:
    text = re.sub(r"\bblack (and|&) white\b", "b&w", text)
    text = re.sub(r"\bteal (and|&) orange\b", "teal & orange", text)
    parts = re.split(r"[,.;!\n]|\band\b|\bthen\b|\bplus\b|\bbut\b", text)
    return [p.strip() for p in parts if p.strip()]


def _add(change: dict, path: list[str], value: float):
    d = change.setdefault("add", {})
    for k in path[:-1]:
        d = d.setdefault(k, {})
    d[path[-1]] = d.get(path[-1], 0) + value


RULES: list[tuple[str, list, str]] = [
    # (regex, [(path, delta), ...], explanation)
    (r"\b(less|reduce|lower|decrease|tone down|kill|remove|no)\b.*\bcontrast\b|\bflatter\b|\bmatte\b",
     [(["contrast"], -15)], "lower contrast"),
    (r"\b(more|add|increase|boost|extra|higher)\b.*\bcontrast\b|\bpunch(y|ier)?\b|\bcontrasty\b",
     [(["contrast"], 15)], "more contrast"),
    (r"\b(brighter|brighten|lighten|lighter|lift (the )?exposure|more exposure|increase exposure|too dark|underexposed)\b",
     [(["exposure"], 0.3)], "brighter exposure"),
    (r"\b(darker|darken|less exposure|reduce exposure|decrease exposure|too bright|overexposed)\b(?!.*\b(background|sky|edges|corners)\b)",
     [(["exposure"], -0.3)], "darker exposure"),
    (r"\b(recover|tame|tone down|reduce|lower|pull (back|down)|protect|save)\b.*\b(highlights?|bright areas|sky|blown|glare|hot spots?)\b|\bblown( out)?\b",
     [(["highlights"], -25)], "recover highlights"),
    (r"\b(brighter|boost|raise|more)\b.*\bhighlights?\b",
     [(["highlights"], 15)], "brighter highlights"),
    (r"\b(lift|open( up)?|raise|brighten|recover|more detail in)\b.*\bshadows?\b|\bshadows? (are )?too dark\b",
     [(["shadows"], 20)], "lift shadows"),
    (r"\b(deepen|darken|crush|deeper|darker|richer)\b.*\b(shadows?|blacks?)\b",
     [(["shadows"], -12), (["blacks"], -12)], "deeper shadows/blacks"),
    (r"\b(brighter|cleaner|boost|more)\b.*\bwhites?\b",
     [(["whites"], 12)], "brighter whites"),
    (r"\b(warmer|warm (it )?up|more warmth|too (cold|cool|blue))\b",
     [(["temperature"], 10)], "warmer white balance"),
    (r"\b(cooler|colder|cool (it )?down|less warm|too (warm|yellow|orange))\b",
     [(["temperature"], -10)], "cooler white balance"),
    (r"\b(more magenta|less green|too green|green cast|remove (the )?green)\b",
     [(["tint"], 8)], "tint towards magenta"),
    (r"\b(more green|less magenta|too (magenta|pink)|magenta cast)\b",
     [(["tint"], -8)], "tint towards green"),
    (r"\b(desaturate|less saturat\w*|muted|less colou?r(ful)?|tone down (the )?colou?rs?|washed)\b",
     [(["saturation"], -15)], "less saturation"),
    (r"\b(more saturat\w*|saturate|more colou?r(ful)?|colou?rful|vivid|richer colou?rs?|colou?rs? pop)\b",
     [(["vibrance"], 15), (["saturation"], 4)], "more colour (vibrance-led)"),
    (r"\b(sharper|sharpen|crisp(er)?|more detail|more sharpness)\b",
     [(["sharpness"], 15), (["clarity"], 5)], "sharper detail"),
    (r"\b(more clarity|more texture|grittier|gritty|more definition|more structure)\b",
     [(["clarity"], 15), (["structure"], 10)], "more clarity/structure"),
    (r"\b(softer|soften|less clarity|less harsh|dreamy)\b",
     [(["clarity"], -10)], "softer local contrast"),
    (r"\b(denoise|less noise|reduce (the )?noise|noisy|grainy|clean up (the )?noise|noise reduction)\b",
     [(["lumaNoiseReduction"], 20), (["colorNoiseReduction"], 25)], "noise reduction"),
    (r"\b(dehaze|less haze|hazy|foggy|cut (through )?(the )?haze)\b",
     [(["dehaze"], 15)], "dehaze"),
    (r"\b(add|more|stronger|darker)\b.*\bvignette\b|\bvignette\b",
     [(["vignetteAmount"], -20)], "darker vignette"),
    (r"\b(add|more)\b.*\b(grain|film look|filmic)\b",
     [(["grainAmount"], 15)], "film grain"),
    (r"\bteal (and|&) orange\b",
     [(["colorGrading", "shadows", "saturation"], 20), (["colorGrading", "highlights", "saturation"], 18)],
     "teal & orange grade"),
]


def interpret(text: str, analysis: dict | None = None) -> tuple[dict, list[str]]:
    """Return (change, explanations). Unrecognised clauses are reported."""
    change: dict = {}
    notes: list[str] = []
    low = text.lower().strip()

    if re.fullmatch(r"(reset|start over|back to (the )?original|remove all edits)", low):
        return {"reset": True}, ["reset to original"]

    for clause in _clauses(low):
        matched = False
        k = _intensity(clause)

        m = re.search(r"(\d{1,3})\s*%", clause)
        strength = min(int(m.group(1)), 150) / 100 if m else None
        for pattern, preset in PRESET_ALIASES:
            if re.search(pattern, clause):
                s = strength if strength is not None else (0.6 if k < 1 else 1.0)
                change.setdefault("presets", [])
                change["presets"] = [p for p in change["presets"] if p["name"] != preset]
                change["presets"].append({"name": preset, "strength": s})
                notes.append(f"preset {preset} at {round(s * 100)}%")
                matched = True
                break
        if matched:
            continue

        if re.search(r"\b(auto|fix (the )?(exposure|white balance|colou?rs?)|correct (it|the photo)|balance it)\b", clause):
            change["auto"] = True
            notes.append("auto-correct from image analysis")
            matched = True

        if re.search(r"\b(black and white|b&w|b/w|monochrome|greyscale|grayscale)\b", clause) and \
                not re.search(r"\b(no|remove|back to colou?r)\b", clause):
            change.setdefault("set", {})["monochromeEnabled"] = True
            notes.append("monochrome")
            matched = True
        elif re.search(r"\b(back to colou?r|remove (the )?(black and white|monochrome)|in colou?r)\b", clause):
            change.setdefault("set", {})["monochromeEnabled"] = False
            notes.append("colour restored")
            matched = True

        if re.search(r"\blens (correction|profile)|fix (the )?(distortion|lens)|chromatic aberration\b", clause):
            change["lensCorrection"] = "auto"
            notes.append("lens profile correction (lensfun)")
            matched = True

        m = re.search(r"(straighten|rotate|tilt|level)\D*(-?\d+(\.\d+)?)\s*(degrees|degree|deg|°)?\s*(left|right|clockwise|counter-?clockwise|anticlockwise)?", clause)
        if m:
            deg = float(m.group(2))
            if m.group(5) in ("left", "counter-clockwise", "counterclockwise", "anticlockwise"):
                deg = -abs(deg)
            change["straighten"] = deg
            notes.append(f"straighten {deg:+.1f}°")
            matched = True

        m = re.search(r"\bcrop\b.*?(\d+(\.\d+)?)\s*[:x]\s*(\d+(\.\d+)?)", clause)
        if m:
            change["crop"] = {"aspect": f"{m.group(1)}:{m.group(3)}"}
            notes.append(f"crop {m.group(1)}:{m.group(3)}")
            matched = True
        elif "crop" in clause:
            for word, aspect in ASPECTS.items():
                if word in clause:
                    change["crop"] = {"aspect": aspect}
                    notes.append(f"crop {aspect} ({word})")
                    matched = True
                    break
            if re.search(r"\b(remove|no|undo) (the )?crop\b", clause):
                change["crop"] = None
                notes.append("crop removed")
                matched = True

        # Selective (masked) edits.
        sel = re.search(r"\b(subject|player|athlete|person|people|model|face)\b", clause)
        bg = re.search(r"\b(background|backdrop|surroundings|crowd)\b", clause)
        sky = re.search(r"\bsky\b", clause)
        brighten = re.search(r"\b(brighten|brighter|lift|pop|light up|stand out|more light)\b", clause)
        darken = re.search(r"\b(darken|darker|dim|subdue|less bright|burn)\b", clause)
        blur_or_desat = re.search(r"\b(desaturate|muted|less colou?r)\b", clause)
        if (sel or bg) and (brighten or darken or blur_or_desat):
            amt = 0.35 * k * (1 if brighten else -1)
            adj = {"exposure": round(amt, 2)} if (brighten or darken) else {"saturation": -30 * k}
            mtype = "background" if bg and not sel else "subject"
            change.setdefault("masks", []).append({"type": mtype, "name": f"M7 {mtype}", "adjustments": adj})
            notes.append(f"{mtype} mask: {adj}")
            continue
        if sky and (darken or re.search(r"\b(recover|deepen|richer|bluer|more blue)\b", clause)):
            adj = {"exposure": round(-0.4 * k, 2), "highlights": -20, "saturation": 10}
            change.setdefault("masks", []).append({
                "type": "linear", "name": "M7 sky gradient", "startX": 0.5, "startY": 0.0,
                "endX": 0.5, "endY": 0.45, "range": 0.12, "adjustments": adj})
            notes.append("graduated sky filter")
            continue
        if re.search(r"\b(spotlight|centre|center)\b", clause) and (brighten or darken):
            change.setdefault("masks", []).append({
                "type": "radial", "name": "M7 centre light", "centerX": 0.5, "centerY": 0.5,
                "radiusX": 0.35, "radiusY": 0.45, "feather": 0.7,
                "adjustments": {"exposure": round(0.3 * k * (1 if brighten else -1), 2)}})
            notes.append("radial centre mask")
            continue

        # Per-colour HSL.
        m = re.search(r"\b(more|less|increase|reduce|boost|lower|desaturate|saturate|brighten|darken|lighter|darker)\b.*?\b(" +
                      "|".join(COLORS) + r")s?\b", clause) or \
            re.search(r"\b(" + "|".join(COLORS) + r")s?\b.*?\b(more|less|brighter|darker|saturated|muted|pop)\b", clause)
        if m and not sky:
            words = clause
            color = next(COLORS[c] for c in COLORS if re.search(rf"\b{c}s?\b", words))
            sign = -1 if re.search(r"\b(less|reduce|lower|desaturate|muted|darken|darker)\b", words) else 1
            field = "luminance" if re.search(r"\b(brighten|darken|lighter|darker|brighter)\b", words) else "saturation"
            if color == "oranges" and "skin" in words:
                k = min(k, 1.0)  # be gentle with skin
            _add(change, ["hsl", color, field], sign * 12 * k)
            notes.append(f"HSL {color} {field} {'+' if sign > 0 else '-'}{12 * k:g}")
            continue

        for pattern, deltas, why in RULES:
            if re.search(pattern, clause):
                for path, d in deltas:
                    _add(change, path, d * k)
                notes.append(why + ("" if k == 1 else f" (x{k:g})"))
                matched = True
                break
        if not matched:
            notes.append(f"not understood: '{clause}'")

    if change.get("auto") and analysis:
        for key, val in suggest(analysis).items():
            _add(change, [key], val)
    return change, notes


def _get(d: dict, path: list[str], default=0):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


def _set(d: dict, path: list[str], value):
    for k in path[:-1]:
        d = d.setdefault(k, {})
    d[path[-1]] = value


def _walk(delta: dict, prefix=()):
    for k, v in delta.items():
        if isinstance(v, dict):
            yield from _walk(v, prefix + (k,))
        else:
            yield list(prefix + (k,)), v


def apply_change(current: dict, change: dict) -> dict:
    """Fold a Change into a spec and return the new spec (input untouched)."""
    if change.get("reset"):
        return m7spec.new_spec()
    spec = copy.deepcopy(current)
    if "presets" in change:
        spec["presets"] = change["presets"]
    resolved = m7spec.resolved_adjustments(spec)
    adj = spec.setdefault("adjustments", {})
    for path, delta in _walk(change.get("add", {})):
        base = _get(resolved, path, m7spec.NEUTRAL.get(path[-1], 0))
        _set(adj, path, round(base + delta, 3))
    for path, value in _walk(change.get("set", {})):
        _set(adj, path, value)
    # Grading hues for "teal & orange" if none set yet.
    cg = adj.get("colorGrading", {})
    if "shadows" in cg and "hue" not in cg["shadows"] and "hue" not in _get(resolved, ["colorGrading", "shadows"], {}):
        cg["shadows"]["hue"] = 195
    if "highlights" in cg and "hue" not in cg["highlights"] and "hue" not in _get(resolved, ["colorGrading", "highlights"], {}):
        cg["highlights"]["hue"] = 35
    for key in ("crop", "straighten", "lensCorrection"):
        if key in change:
            if change[key] is None:
                spec.pop(key, None)
            else:
                spec[key] = change[key]
    if change.get("masks"):
        spec["masks"] = spec.get("masks", []) + change["masks"]
    # Validate (clamps) the explicit layer so the stored spec is always legal.
    spec["adjustments"] = m7spec.validate_adjustments(spec["adjustments"])
    return spec
