import tempfile
import unittest
from pathlib import Path

from m7 import diagnostics


class DiagnosticsTest(unittest.TestCase):
    def test_explain_maps_known_failures(self):
        self.assertIn("graphics card did not finish",
                      diagnostics.explain("Export error: Failed while polling mapped GPU buffer: timed out"))
        self.assertIn("could not be decoded", diagnostics.explain("Failed to decode raw file"))
        self.assertIn("RapidRAW reported an error", diagnostics.explain("something odd"))

    def test_gpu_adapter_parsed(self):
        log = "[INFO] Using GPU adapter: NVIDIA GeForce RTX 3060 (Dx12, DiscreteGpu)\nmore"
        self.assertEqual(diagnostics.gpu_adapter(log), "NVIDIA GeForce RTX 3060 (Dx12, DiscreteGpu)")

    def test_failure_report_written(self):
        with tempfile.TemporaryDirectory() as d:
            diagnostics.setup_logging(Path(d))
            p = diagnostics.record_failure(Path("x.ARW"), ["RapidRAW", "export"], 1, "boom")
            self.assertTrue(p.is_file())
            self.assertIn("boom", p.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
