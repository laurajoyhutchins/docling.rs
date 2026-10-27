#!/usr/bin/env python3
"""Hermetic tests for the real full-conformance runner, without Docling/models."""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


def load_runner():
    """Import the actual runner while stubbing only its unused upstream imports."""
    base = types.ModuleType("docling.datamodel.base_models")
    base.InputFormat = type("InputFormat", (), {})
    document = types.ModuleType("docling.datamodel.document")
    document.InputDocument = object
    docling = types.ModuleType("docling")
    docling.__path__ = []
    datamodel = types.ModuleType("docling.datamodel")
    datamodel.__path__ = []
    convert = types.ModuleType("docling_convert")
    convert.EXT_TO_FORMAT = {}
    deps = {
        "docling": docling,
        "docling.datamodel": datamodel,
        "docling.datamodel.base_models": base,
        "docling.datamodel.document": document,
        "docling_convert": convert,
    }
    path = Path(__file__).with_name("full_conformance.py")
    spec = importlib.util.spec_from_file_location("full_conformance_under_test", path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, deps):
        spec.loader.exec_module(module)
    return module


class RustCliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def test_success_stdout_unchanged(self):
        with mock.patch.object(self.runner, "CLI", sys.executable):
            output = self.runner.run_cli("-c", "print('source faithful')")
        self.assertEqual(output, "source faithful\n")

    def test_failure_rejects_plausible_stdout(self):
        command = "import sys; print('EXACT'); print('bad model', file=sys.stderr); sys.exit(23)"
        with mock.patch.object(self.runner, "CLI", sys.executable):
            with self.assertRaisesRegex(RuntimeError, "exit 23.*bad model"):
                self.runner.run_cli("-c", command)

    def test_rust_failure_recorded_and_report_exit_nonzero(self):
        for responses in [
            [RuntimeError("Rust CLI failed (exit 7)")],
            ["valid Markdown", RuntimeError("Rust CLI failed (exit 8)")],
            ["valid Markdown", "{malformed json"],
        ]:
            with self.subTest(responses=responses), tempfile.TemporaryDirectory() as tmp:
                runner = load_runner()
                runner.ROOT = Path(tmp)
                source = runner.ROOT / "tests/data/html/sources/sample.html"
                with (
                    mock.patch.object(runner.sys, "argv", ["full_conformance.py", "html"]),
                    mock.patch.object(runner.glob, "glob", return_value=[str(source)]),
                    mock.patch.object(runner, "upstream", return_value=("valid Markdown", {"items": []})),
                    mock.patch.object(runner, "run_cli", side_effect=responses),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    with self.assertRaises(SystemExit) as exit_context:
                        runner.main()
                self.assertEqual(exit_context.exception.code, 1)
                report = json.loads((runner.ROOT / "target/full_conformance.json").read_text())
                self.assertEqual(report["summary"][0][-1], 1)
                self.assertEqual(report["summary"][0][2:5], [0, 0, 0])
                self.assertEqual(report["rows"][0][2], "RUST-ERR")

    def test_difference_is_report_only_not_process_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = load_runner()
            runner.ROOT = Path(tmp)
            source = runner.ROOT / "tests/data/html/sources/sample.html"
            with (
                mock.patch.object(runner.sys, "argv", ["full_conformance.py", "html"]),
                mock.patch.object(runner.glob, "glob", return_value=[str(source)]),
                mock.patch.object(runner, "upstream", return_value=("expected", None)),
                mock.patch.object(runner, "run_cli", return_value="observed"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                runner.main()
            report = json.loads((runner.ROOT / "target/full_conformance.json").read_text())
            self.assertEqual(report["summary"][0][-1], 0)
            self.assertEqual(report["rows"][0][2], "md=2/2")


if __name__ == "__main__":
    unittest.main()
