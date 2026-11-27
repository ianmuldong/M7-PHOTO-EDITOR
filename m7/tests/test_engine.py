"""Integration tests: run the real RapidRAW engine (skipped if it isn't built)."""
import hashlib
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from m7 import engine
from m7 import spec as s

try:
    engine.find_binary()
    HAVE_ENGINE = True
except engine.EngineError:
    HAVE_ENGINE = False


@unittest.skipUnless(HAVE_ENGINE, "RapidRAW binary not built")
class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="m7test-"))
        y, x = np.mgrid[0:600, 0:900]
        img = np.stack([x / 900 * 200 + 20, y / 600 * 180 + 30, np.full_like(x, 90.0)], -1)
        cls.src = cls.tmp / "synthetic.jpg"
        Image.fromarray(img.astype(np.uint8)).save(cls.src, quality=95)
        cls.sha = hashlib.sha256(cls.src.read_bytes()).hexdigest()

    def render(self, adj, name, fmt="png"):
        out = self.tmp / f"{name}.{fmt if fmt != 'jpeg' else 'jpg'}"
        engine.render(self.src, adj, out, fmt=fmt, keep_metadata=False)
        return np.asarray(Image.open(out).convert("RGB")).astype(float), out

    def test_pipeline(self):
        base, _ = self.render({}, "base")
        self.assertEqual(base.shape[:2], (600, 900))  # original resolution
        bright, _ = self.render({"exposure": 1.0}, "bright")
        self.assertGreater(bright.mean(), base.mean() + 10)
        warm, _ = self.render({"temperature": 50}, "warm")
        self.assertGreater((warm[..., 0] - warm[..., 2]).mean(), (base[..., 0] - base[..., 2]).mean() + 5)
        mono, _ = self.render({"monochromeEnabled": True}, "mono")
        self.assertLess(np.std(mono, axis=2).mean(), 1.0)
        crop, _ = self.render(s.compile_spec({"m7Version": 1, "crop": {"aspect": "1:1"}}, 900, 600), "crop")
        self.assertEqual(crop.shape[:2], (600, 600))
        _, tif = self.render({}, "t16", fmt="tiff")
        with Image.open(tif) as im:
            self.assertEqual(im.size, (900, 600))
        self.assertEqual(hashlib.sha256(self.src.read_bytes()).hexdigest(), self.sha)

    def test_refuses_to_overwrite_original(self):
        with self.assertRaises(engine.EngineError):
            engine.render(self.src, {}, self.src)


if __name__ == "__main__":
    unittest.main()
