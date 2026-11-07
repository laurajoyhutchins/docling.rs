"""Negative controls for the revision-matched PDF repeatability diagnostic."""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


MODULE = Path(__file__).with_name("pdf_repro.py")
SPEC = importlib.util.spec_from_file_location("pdf_repro", MODULE)
repro = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repro)
SHA_A = "a" * 40
SHA_B = "b" * 40


class PdfReproTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.original_dir = Path.cwd()
        self.addCleanup(os.chdir, self.original_dir)
        self.root = Path(self.tmp.name)
        os.chdir(self.root)
        for path in repro.MODEL_ENV.values():
            f = self.root / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"fake-test-asset")
        self.source = self.root / "source.pdf"
        self.source.write_bytes(b"fixture")
        self.base = self.root / "base"
        self.candidate = self.root / "candidate"

    def binary(self, path, script):
        path.write_text("#!/usr/bin/env python3\n" + script)
        path.chmod(0o755)

    def run_repro(self):
        return repro.main([
            "--baseline", str(self.base), "--candidate", str(self.candidate),
            "--baseline-revision", SHA_A, "--candidate-revision", SHA_B,
            "--source", str(self.source), "--out", str(self.root / "report"),
            "--baseline-renderer", "rust", "--candidate-renderer", "rust",
        ])

    def test_changed_line_accounting_ignores_trailing_newline(self):
        self.assertEqual(repro.changed_lines(b"a\nb\n", b"a\nc\n"), 2)
        self.assertEqual(repro.changed_lines(b"a\n", b"a"), 0)
        self.assertEqual(repro.changed_lines(b"a\n", b"a\nb\n"), 1)

    def test_stable_revisions_report_parity_diff_without_failing(self):
        self.binary(self.base, "print('baseline')\n")
        self.binary(self.candidate, "print('candidate')\n")
        self.assertEqual(self.run_repro(), 0)
        data = json.loads((self.root / "report/report.json").read_text())
        self.assertTrue(data["repeatable"])
        self.assertTrue(data["conversions_succeeded"])
        self.assertEqual(data["source_revisions"]["baseline"], SHA_A)
        self.assertEqual(data["cases"][0]["changed_lines"]["baseline_vs_candidate"], 2)
        self.assertTrue(all(data["cases"][0]["stable"].values()))
        self.assertEqual(len(data["cases"][0]["runs"]["candidate"]), 2)
        self.assertIn("DOCLING_LAYOUT_ONNX", data["configuration"]["baseline"]["assets"])
        self.assertEqual(data["configuration"]["candidate"]["env"]["DOCLING_RS_PDF_THREADS"], "1")

    def test_identical_binary_with_variable_output_fails_closed(self):
        self.binary(self.base, "print('stable')\n")
        self.binary(self.candidate, (
            "from pathlib import Path\n"
            "counter = Path('calls.txt')\n"
            "n = int(counter.read_text()) if counter.exists() else 0\n"
            "counter.write_text(str(n + 1))\n"
            "print(f'value-{n}')\n"
        ))
        self.assertEqual(self.run_repro(), 1)
        data = json.loads((self.root / "report/report.json").read_text())
        self.assertFalse(data["repeatable"])
        self.assertTrue(data["cases"][0]["stable"]["baseline"])
        self.assertFalse(data["cases"][0]["stable"]["candidate"])

    def test_empty_or_failed_conversion_never_counts_as_parity(self):
        self.binary(self.base, "print('good')\n")
        self.binary(self.candidate, "import sys\nsys.exit(3)\n")
        self.assertEqual(self.run_repro(), 1)
        data = json.loads((self.root / "report/report.json").read_text())
        self.assertFalse(data["conversions_succeeded"])
        self.assertIn("conversion failed", data["cases"][0]["runs"]["candidate"][0]["error"])

    def test_missing_model_asset_is_an_error_not_a_fallback(self):
        self.binary(self.base, "print('good')\n")
        self.binary(self.candidate, "print('good')\n")
        (self.root / ".models/layout_heron.onnx").unlink()
        with self.assertRaisesRegex(ValueError, "required nonempty file missing"):
            self.run_repro()


if __name__ == "__main__":
    unittest.main()
