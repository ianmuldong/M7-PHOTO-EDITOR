"""Before/after comparison images."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def before_after(before: Path, after: Path, out: Path, height: int = 900) -> Path:
    imgs = []
    for p in (before, after):
        im = Image.open(p).convert("RGB")
        w = round(im.width * height / im.height)
        imgs.append(im.resize((w, height), Image.LANCZOS))
    gap = 12
    canvas = Image.new("RGB", (imgs[0].width + imgs[1].width + gap, height), (18, 18, 18))
    canvas.paste(imgs[0], (0, 0))
    canvas.paste(imgs[1], (imgs[0].width + gap, 0))
    draw = ImageDraw.Draw(canvas)
    font = _font(max(18, height // 30))
    for label, x in (("BEFORE", 16), ("AFTER", imgs[0].width + gap + 16)):
        draw.rectangle([x - 8, 12, x + font.getlength(label) + 8, 16 + font.size + 8], fill=(0, 0, 0))
        draw.text((x, 16), label, fill=(255, 255, 255), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, quality=90)
    return out
