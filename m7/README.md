# M7 Studio Editor

M7 is a personal, chat-driven photo editor built on top of **RapidRAW's own editing
engine**. You describe the look you want in plain language. M7 turns that into RapidRAW
adjustment parameters, and RapidRAW's pipeline renders the photo: RAW decode,
demosaic, tone, colour, detail, lens and geometry. It uses **no generative image
recreation**. Every pixel comes from your original file through standard,
non-destructive processing, so faces, bodies, clothing, logos and detail are preserved.

- **Local only.** M7 adds no cloud APIs and no paid services, and photos are never uploaded.
- **Non-destructive.** The original is only read. Edits live as JSON history next to it,
  and exports go to separate files.
- **Licence.** RapidRAW is AGPL-3.0, so M7 is too. If you distribute it or run it as a
  network service, the AGPL source-sharing terms apply.

## 1. Repository audit: what runs where

| Component | Where it runs | Notes |
|---|---|---|
| RapidRAW engine (Rust, wgpu) | Desktop app **and** headless | It already had a headless `export` command that takes `--adjustments <json>`. M7 drives that command. |
| GPU processing | Real GPU, or software Vulkan (Mesa llvmpipe) | It works with no GPU at all; this is how it was tested in the cloud container. It is just slower. |
| RAW decode (Sony ARW etc.) | Everywhere | Uses rawler/dnglab inside RapidRAW. |
| Lens correction | Everywhere | Uses the lensfun database bundled in `src-tauri/lensfun_db`. |
| AI masks (subject/background/sky) and AI denoise | **Desktop with internet** | ONNX models are downloaded from HuggingFace on first use. The cloud sandbox used for development blocks that host, so these are **untested here**. Without the model they fail with a clear error rather than producing a wrong image. |
| Video features / FFmpeg | Not needed by M7 | |

**Can Claude Code run it directly? Yes.** In a Claude Code session, Claude can read an
uploaded photo, run the real RapidRAW engine through the `m7` CLI, look at the
before/after preview, and refine conversationally. See [CLAUDE_WORKFLOW.md](CLAUDE_WORKFLOW.md).
For everyday use there is also a **local chat web app** (`python3 -m m7.server`) that
needs no Claude and no internet.

## 2. Installation

### Requirements
- Rust toolchain (the repo pins 1.98 in `src-tauri/rust-toolchain.toml`; rustup installs it automatically).
- Node 20+ (only to build the RapidRAW desktop UI).
- Python 3.10+ with `numpy` and `Pillow` (`pip install numpy pillow`).
- Linux build packages (Ubuntu 24.04):
  ```bash
  sudo apt-get install -y libwebkit2gtk-4.1-dev libgtk-3-dev libsoup-3.0-dev \
      libayatana-appindicator3-dev librsvg2-dev libssl-dev pkg-config build-essential \
      clang cmake nasm libdbus-1-dev
  # Headless servers without a display or GPU also need:
  sudo apt-get install -y xvfb mesa-vulkan-drivers
  ```
  macOS: Xcode command line tools. Windows: MSVC build tools + WebView2. These follow RapidRAW's normal requirements.

### Build the engine
```bash
npm ci && npm run build                     # RapidRAW UI (embedded in the binary)
cd src-tauri && cargo build --release       # produces target/release/RapidRAW
```
The first build takes about 30–40 minutes. The build script downloads ONNX Runtime and
FFmpeg from HuggingFace. If you are offline or sandboxed, use
`RAPIDRAW_SKIP_RUNTIME_DOWNLOADS=1 cargo build`. That builds without the AI and video
runtimes, and photo editing still works.

M7 finds the binary in `src-tauri/target/{release,debug}/`, on your `PATH`, or via
`M7_RAPIDRAW_BIN=/path/to/RapidRAW`. On Linux with no display, M7 wraps it in
`xvfb-run` automatically.

## 3. Using it

### Local chat app
```bash
python3 -m m7.server                 # open http://127.0.0.1:7777
```
1. Upload an ARW, JPEG, PNG or TIFF.
2. Type an instruction or click a preset chip. You see a before/after wipe (or side by side when cropped).
3. Use Undo, Redo, or click a history entry.
4. Export a full-resolution JPEG or a 16-bit TIFF.

The workspace (sessions, uploads, exports) is `~/M7Studio`.

### CLI
```bash
python3 -m m7 render photo.ARW --preset cinematic-sports --strength 0.8 \
        --set exposure=0.3 --set hsl.blues.saturation=-20 --out edited.jpg   # one-shot
python3 -m m7 open    photo.ARW                     # neutral develop + analysis
python3 -m m7 say     photo.ARW "nike commercial at 80%, slightly warmer, crop 4:5"
python3 -m m7 apply   photo.ARW change.json         # structured change (what Claude writes)
python3 -m m7 undo    photo.ARW   |  redo  |  history  |  show
python3 -m m7 export  photo.ARW out/final.tiff      # full resolution, 16-bit
python3 -m m7 batch   ./shoot --preset indoor-volleyball --out ./edited --format jpeg
python3 -m m7 save-preset photo.ARW "my look"       # current look -> ~/.m7/presets
python3 -m m7 interpret "lift the shadows a bit and make it cooler"   # dry run
```

### Edit spec (structured JSON)
Every history step is a complete spec, which is stored in `session.json` and in a
`.m7.json` file next to each export:
```json
{
  "m7Version": 1,
  "presets": [{"name": "golden-hour-cinematic", "strength": 0.7}],
  "adjustments": {"exposure": 0.3, "shadows": 20, "hsl": {"blues": {"saturation": -15}},
                  "curves": {"luma": [{"x":0,"y":0},{"x":64,"y":56},{"x":192,"y":200},{"x":255,"y":255}]}},
  "crop": {"aspect": "4:5"},
  "straighten": -1.5,
  "lensCorrection": "auto",
  "masks": [{"type": "radial", "centerX": 0.5, "centerY": 0.45, "radiusX": 0.25, "radiusY": 0.35,
             "feather": 0.6, "adjustments": {"exposure": 0.35}}]
}
```
Adjustments use RapidRAW's own keys and slider units (`RANGES` in `spec.py`). Values
are clamped, and unknown keys are rejected so typos never silently do nothing.

## 4. M7 signature presets
All presets live in `m7/presets/*.json` and are adjustable from 0–150% strength. Hue
choices stay fixed and amounts scale.

| Preset | Character |
|---|---|
| `cinematic-sports` | teal shadows / warm highlights, controlled highlights, punchy mids |
| `nike-commercial` | crisp, deep blacks, bright whites, strong local contrast (no brand assets) |
| `indoor-volleyball` | corrects yellow gym light, tames floor glare, lifts players, cleans noise |
| `dark-athletic-editorial` | low-key, desaturated, sculpted, vignette, fine grain |
| `natural-portrait` | soft highlight roll-off, open shadows, accurate skin; no smoothing or reshaping |
| `golden-hour-cinematic` | warm amber grade, soft contrast, muted blues, light glow |
| `clean-commercial` | bright, neutral, accurate, crisp |
| `high-contrast-monochrome` | bold B&W with tuned channel mix and grain |
| `low-light-sports-recovery` | lifts exposure and shadows, protects highlights, strong NR |
| `vibrant-social` | vibrance-led colour pop that protects skin tones |

These are first versions tuned on test images, not on your work. Expect to adjust them
on your own photos.

## 5. Test status
The engine runs were done in the cloud container: software Vulkan (llvmpipe), 4 CPU
cores, no GPU. The test images were real public Sony RAW samples: a DSC-RX100
(5472×3648 developed, ISO 640) and an ILCE-7CR (9504×6336, ISO 40000).

| Feature | Status | How it was verified |
|---|---|---|
| Sony ARW decode + edit + export | ✅ tested | both cameras; output at full developed resolution, EXIF kept |
| JPEG / PNG / TIFF input | ✅ tested | batch run; original resolution preserved |
| JPEG / 16-bit TIFF output | ✅ tested | `exiftool` confirms size and 16-bit samples; PNG output tested at preview size |
| Original never modified | ✅ tested | SHA-256 identical before and after exports |
| Exposure, contrast, highlights, shadows, whites, blacks | ✅ tested | each moves the expected tonal range (pixel measurements) |
| Temperature (+ = warmer), tint (+ = magenta) | ✅ tested | channel-difference measurements |
| Saturation, vibrance, HSL, colour grading, tone curves | ✅ tested | measured effect per control |
| Sharpness, clarity, noise reduction | ✅ tested | detail and noise metrics move as expected; NR clearly reduces colour noise, and luma noise at ISO 40000 is reduced but not eliminated |
| Lens correction (lensfun auto) | ✅ tested | RX100 barrel distortion visibly removed |
| Straighten (+ = clockwise) + auto inscribed crop; aspect crops | ✅ tested | unit test for crop geometry plus visual check |
| Radial & graduated (linear) masks | ✅ tested | effect confined to the masked region (measured) |
| AI subject / background / sky masks | ⚠️ untested | needs HuggingFace model download, which is blocked in the sandbox; fails safely with an error |
| AI denoise | ⚠️ untested | same reason |
| 10 presets (0–150% strength) | ✅ tested | all compile and render; contact sheet reviewed |
| Natural-language interpreter | ✅ tested | unit tests plus real sessions |
| Undo / redo / history | ✅ tested | CLI and web UI |
| Before/after preview | ✅ tested | wipe slider; side by side when cropped |
| Batch processing | ✅ tested | 3 files, mixed formats |
| Local chat web app | ✅ tested | Playwright: upload ARW → chat edits → undo/redo → full-res download |
| Desktop RapidRAW GUI | ➖ unchanged | not run in the container (no display) |
| Windows / macOS | ➖ not tested | the M7 Python code is cross-platform; engine builds follow RapidRAW's normal process |

Timings on the CPU-only sandbox: a 1600 px preview takes about 10 s for 20 MP and about
60 s for 61 MP. A full-resolution export takes about 10–15 s for 20 MP and about 68 s for
61 MP (16-bit TIFF). A real GPU should be several times faster.

Run the unit tests with `python3 -m unittest m7.tests.test_spec`.

## 6. Known limitations / next steps
- The natural-language interpreter is rule-based so it can run offline. It understands
  common photo vocabulary and reports anything it didn't understand. For nuanced
  requests, use Claude Code (it writes the structured change), or plug in a local LLM later.
- Subject and background masks rely on RapidRAW's AI segmentation. Verify them on a
  desktop with internet access. Radial and gradient masks work everywhere.
- "Auto" exposure is deliberately conservative and scene-aware, so dusk and night scenes stay low-key.
- The presets need tuning on your own volleyball, sports and portrait work.

## Changes to RapidRAW itself
These changes are minimal and leave the engine itself untouched:
- `export --long-edge N` on the headless CLI (fast previews; final exports stay full-size).
- `RAPIDRAW_SKIP_RUNTIME_DOWNLOADS=1` build switch for offline builds.
