import math
import unittest

from m7 import nl
from m7 import spec as s


class SpecTests(unittest.TestCase):
    def test_all_presets_compile(self):
        names = [p["name"] for p in s.list_presets()]
        self.assertEqual(len(names), 10)
        for n in names:
            for strength in (0, 0.5, 1, 1.5):
                adj = s.compile_spec({"m7Version": 1, "presets": [{"name": n, "strength": strength}]}, 6000, 4000)
                self.assertIsInstance(adj, dict)

    def test_strength_scales_numbers_not_hues(self):
        full = s.scaled_preset("cinematic-sports", 1.0)
        half = s.scaled_preset("cinematic-sports", 0.5)
        self.assertAlmostEqual(half["contrast"], full["contrast"] / 2)
        self.assertEqual(half["colorGrading"]["shadows"]["hue"], full["colorGrading"]["shadows"]["hue"])
        zero = s.scaled_preset("cinematic-sports", 0.0)
        for p in zero["curves"]["luma"]:
            self.assertEqual(p["x"], p["y"])

    def test_clamping_and_unknown_keys(self):
        self.assertEqual(s.validate_adjustments({"exposure": 9})["exposure"], 5)
        with self.assertRaises(s.SpecError):
            s.validate_adjustments({"exposur": 1})

    def test_inscribed_crop_fits_after_rotation(self):
        w, h, ang = 6000, 4000, 5.0
        c = s.inscribed_crop(w, h, ang, 4 / 5)
        self.assertAlmostEqual(c["width"] / c["height"], 0.8, places=6)
        t = math.radians(ang)
        bw = c["width"] * math.cos(t) + c["height"] * math.sin(t)
        bh = c["width"] * math.sin(t) + c["height"] * math.cos(t)
        self.assertLessEqual(bw, w + 1e-6)
        self.assertLessEqual(bh, h + 1e-6)

    def test_masks_compile_to_pixels(self):
        adj = s.compile_spec({"m7Version": 1, "masks": [
            {"type": "radial", "centerX": 0.5, "centerY": 0.5, "radiusX": 0.25, "radiusY": 0.25,
             "adjustments": {"exposure": 0.5}},
            {"type": "background", "adjustments": {"exposure": -0.5}}]}, 4000, 3000)
        r = adj["masks"][0]["subMasks"][0]["parameters"]
        self.assertEqual((r["centerX"], r["radiusX"]), (2000, 1000))
        self.assertTrue(adj["masks"][1]["invert"])
        self.assertEqual(adj["masks"][1]["subMasks"][0]["type"], "ai-foreground")


class NLTests(unittest.TestCase):
    def test_relative_changes_stack_on_presets(self):
        spec = s.new_spec()
        ch, _ = nl.interpret("golden hour at 50%")
        spec = nl.apply_change(spec, ch)
        base = s.resolved_adjustments(spec)["temperature"]
        ch, _ = nl.interpret("warmer")
        spec = nl.apply_change(spec, ch)
        self.assertAlmostEqual(s.resolved_adjustments(spec)["temperature"], base + 10)

    def test_intensity_words(self):
        a, _ = nl.interpret("slightly more contrast")
        b, _ = nl.interpret("much more contrast")
        self.assertLess(a["add"]["contrast"], b["add"]["contrast"])

    def test_examples(self):
        cases = {
            "make it black and white": ("set", "monochromeEnabled"),
            "reduce noise": ("add", "lumaNoiseReduction"),
            "cooler": ("add", "temperature"),
            "recover the highlights": ("add", "highlights"),
            "crop to 4:5": ("crop", None),
            "straighten 2 degrees left": ("straighten", None),
            "darken the background": ("masks", None),
            "fix lens distortion": ("lensCorrection", None),
        }
        for text, (top, key) in cases.items():
            ch, notes = nl.interpret(text)
            self.assertIn(top, ch, f"{text}: {notes}")
            if key:
                self.assertIn(key, ch[top], text)
        ch, _ = nl.interpret("straighten 2 degrees left")
        self.assertEqual(ch["straighten"], -2)

    def test_reset(self):
        ch, _ = nl.interpret("reset")
        self.assertEqual(nl.apply_change({"m7Version": 1, "adjustments": {"exposure": 1}}, ch), s.new_spec())


if __name__ == "__main__":
    unittest.main()
