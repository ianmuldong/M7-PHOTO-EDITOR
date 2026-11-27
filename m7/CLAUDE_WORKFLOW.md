# Using M7 Studio Editor from Claude Code

You can use Claude Code as the chat interface. It runs the real RapidRAW engine on your
photo, so you get a professional edit with no generative re-drawing. Claude reads your
request, looks at the photo's analysis and preview, writes a **Change JSON**, and runs
the M7 CLI.

## Workflow

1. Put the photo somewhere in the session, for example `photos/DSC01234.ARW`. You can
   upload it to a cloud session or keep it in a local folder.
2. Ask for the edit, for example: *"Make this look like a Nike ad but keep skin natural, crop 4:5."*
3. Claude runs:
   ```bash
   python3 -m m7 open photos/DSC01234.ARW                  # neutral develop + analysis
   python3 -m m7 apply photos/DSC01234.ARW change.json --note "nike look, 4:5"
   ```
   The command prints the paths of the preview and the side-by-side before/after.
4. Claude shows you `compare-NNN.jpg`. You ask for more changes (*"less contrast, warmer"*),
   and each one becomes a new history step.
5. `python3 -m m7 undo|redo|history <photo>` moves through the history.
6. `python3 -m m7 export <photo> out/final.tiff` (or `.jpg` or `.png`) exports at full resolution.

## Change JSON (what Claude writes)

```json
{
  "presets": [{"name": "nike-commercial", "strength": 0.8}],
  "add":     {"contrast": -5, "temperature": 6, "hsl": {"oranges": {"saturation": -4}}},
  "set":     {"monochromeEnabled": false},
  "crop":    {"aspect": "4:5"},
  "straighten": -1.2,
  "lensCorrection": "auto",
  "masks": [
    {"type": "radial", "centerX": 0.48, "centerY": 0.4, "radiusX": 0.2, "radiusY": 0.3,
     "feather": 0.7, "adjustments": {"exposure": 0.3}},
    {"type": "linear", "fromX": 0.5, "fromY": 0, "toX": 0.5, "toY": 0.4,
     "adjustments": {"exposure": -0.4}},
    {"type": "subject", "adjustments": {"exposure": 0.25}},
    {"type": "background", "adjustments": {"saturation": -30}}
  ]
}
```

- `add` is **relative** to the current values, including the values the preset sets. `set` is **absolute**.
- `presets` replaces the current preset stack.
- Mask coordinates are 0–1 fractions of the developed image. A linear mask is a graduated filter: full effect at `from`, none at `to`.
- Adjustment keys and their ranges are the `RANGES` table in `m7/spec.py`. They are RapidRAW's own slider units.
- To reset everything: `{"reset": true}`.

## Rules Claude follows

- Use tonal, colour, detail and geometry adjustments only. Never use generative fill,
  upscaling or retouching that changes faces, bodies, clothing or logos.
- Keep skin natural. Be gentle with orange/red HSL changes, and avoid strong negative clarity on portraits.
- The original file is never modified. Exports go to a separate path.
- Photos are not sent to external services.
