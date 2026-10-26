"""Regression checks for source-preserving Docling JSON structural comparison."""

import copy
import importlib.util
from pathlib import Path
import unittest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("pdf_structure_diff", HERE / "pdf_structure_diff.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def document():
    return {
        "schema_name": "DoclingDocument",
        "body": {"children": [{"$ref": "#/groups/0"}]},
        "groups": [{
            "label": "form_area", "parent": {"$ref": "#/body"},
            "children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}],
        }],
        "texts": [
            {"label": "section_header", "text": "Heading 1", "parent": {"$ref": "#/groups/0"}},
            {"label": "footnote", "text": "Source citation", "parent": {"$ref": "#/groups/0"}},
        ],
    }


class PdfStructureTests(unittest.TestCase):
    def test_equal_structure(self):
        original = document()
        result = mod.summarize(original, copy.deepcopy(original))
        self.assertTrue(result["structurally_equal"])
        self.assertEqual(result["reference_nodes"], 3)
        self.assertEqual(result["ordered_differences"], [])

    def test_classification_change_is_reported_without_rewriting_source(self):
        original = document()
        changed = copy.deepcopy(original)
        changed["texts"][0]["label"] = "text"
        result = mod.summarize(original, changed)
        self.assertFalse(result["structurally_equal"])
        self.assertEqual(result["classifications"][0]["text"], "Heading 1")
        self.assertEqual(result["classifications"][0]["reference_label"], "section_header")
        self.assertEqual(result["classifications"][0]["actual_label"], "text")

    def test_reading_order_change_detected_even_when_text_is_conserved(self):
        original = document()
        changed = copy.deepcopy(original)
        changed["groups"][0]["children"].reverse()
        result = mod.summarize(original, changed)
        self.assertFalse(result["structurally_equal"])
        self.assertTrue(result["ordered_differences"])
        self.assertFalse(result["classifications"])

    def test_dangling_reference_fails_closed(self):
        broken = document()
        broken["groups"][0]["children"][0] = {"$ref": "#/texts/999"}
        with self.assertRaisesRegex(ValueError, "dangling"):
            mod.walk(broken)

    def test_invalid_parent_and_duplicate_ownership_fail_closed(self):
        broken = document()
        broken["texts"][0]["parent"] = {"$ref": "#/body"}
        with self.assertRaisesRegex(ValueError, "parent mismatch"):
            mod.walk(broken)
        broken = document()
        broken["groups"][0]["children"].append({"$ref": "#/texts/0"})
        with self.assertRaisesRegex(ValueError, "multiply-owned"):
            mod.walk(broken)

    def test_actual_pinned_docling_references_are_accepted(self):
        # Exercise the existing committed reference documents, not mock-only
        # object shapes. These are reference-validity tests, not Rust parity.
        import json
        root = HERE.parents[1]
        for stem in ("normal_4pages", "table_misidentified_as_form",
                     "table_mislabeled_as_picture"):
            with self.subTest(stem=stem):
                source = root / "tests/data/pdf/groundtruth" / (stem + ".json")
                data = json.loads(source.read_text())
                result = mod.summarize(data, data)
                self.assertTrue(result["structurally_equal"])
                self.assertGreater(result["reference_nodes"], 0)


if __name__ == "__main__":
    unittest.main()
