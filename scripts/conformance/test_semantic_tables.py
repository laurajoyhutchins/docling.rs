#!/usr/bin/env python3
"""Pure-standard-library tests for the source-grounded table semantics scorer."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import semantic_tables as audit


def cell(text, row, col, row_span=1, col_span=1):
    return {
        "text": text,
        "start_row_offset_idx": row,
        "end_row_offset_idx": row + row_span,
        "start_col_offset_idx": col,
        "end_col_offset_idx": col + col_span,
    }


def table(*cells, rows=3, cols=4):
    return {"data": {"table_cells": list(cells), "num_rows": rows, "num_cols": cols}}


class SemanticTableTests(unittest.TestCase):
    def test_source_digest_is_git_blob_compatible(self):
        from hashlib import sha1, sha256
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as folder:
            p = Path(folder) / "source.pdf"
            p.write_bytes(b"PDF fixture")
            sha, blob = audit.source_digests(p)
            self.assertEqual(sha, sha256(b"PDF fixture").hexdigest())
            self.assertEqual(
                blob,
                sha1(b"blob 11\0PDF fixture", usedforsecurity=False).hexdigest(),
            )

    def test_independent_cells_on_one_row_pass(self):
        cells = [cell("10 HP", 1, 0), cell("14 A", 1, 1), cell("30.8 A", 1, 2)]
        self.assertTrue(audit.one_row_binding(cells, [
            {"equals": "10 HP"}, {"equals": "14 A"}, {"equals": "30.8 A"},
        ]))

    def test_swapped_voltage_columns_fail(self):
        cells = [cell("10 HP", 1, 0), cell("30.8 A", 1, 1), cell("14 A", 1, 2)]
        self.assertFalse(audit.one_row_binding(cells, [
            {"equals": "10 HP"}, {"equals": "14 A"}, {"equals": "30.8 A"},
        ]))

    def test_cross_row_numbers_cannot_satisfy_one_row(self):
        cells = [cell("10 HP", 0, 0), cell("14 A", 1, 1)]
        self.assertFalse(audit.one_row_binding(cells, [
            {"equals": "10 HP"}, {"equals": "14 A"},
        ]))

    def test_rowspan_intersection_not_anchor_equality(self):
        cells = [cell("10 HP", 0, 0, row_span=2), cell("14 A", 1, 1)]
        self.assertTrue(audit.one_row_binding(cells, [
            {"equals": "10 HP"}, {"equals": "14 A"},
        ]))

    def test_single_cell_cannot_fabricate_two_columns(self):
        cells = [cell("10 HP 14 A", 0, 0)]
        self.assertFalse(audit.one_row_binding(cells, [
            {"contains": "10 HP"}, {"contains": "14 A"},
        ]))

    def test_dot_leaders_ignored_but_page_number_not(self):
        cells = [cell("Notices . . . .", 0, 0), cell(" . . vii", 0, 1)]
        self.assertTrue(audit.one_row_binding(cells, [
            {"contains": "Notices"}, {"equals": "vii"},
        ]))
        self.assertFalse(audit.one_row_binding(cells, [
            {"contains": "Notices"}, {"equals": "viii"},
        ]))

    def test_decimal_value_identity_is_not_substring(self):
        cells = [cell("OTSL", 0, 0), cell("0.965 0.969", 0, 1)]
        self.assertFalse(audit.one_row_binding(cells, [
            {"equals": "OTSL"}, {"equals": "0.965"},
        ]))

    def test_otsl_html_paired_values_are_one_cell_per_metric(self):
        # The pinned Python Docling reference and the original HPO table
        # express two method results within each *single* metric cell.
        # Demand the correct ordered metric cells, not a fabricated extra
        # column for every OTSL/HTML scalar.
        cells = [
            cell("6", 2, 0), cell("6", 2, 1),
            cell("OTSL HTML", 2, 2),
            cell("0.965 0.969", 2, 3),
            cell("0.934 0.927", 2, 4),
            cell("0.955 0.955", 2, 5),
            cell("0.88 0.857", 2, 6),
            cell("2.73 5.39", 2, 7),
        ]
        expected = [
            {"equals": "6"}, {"equals": "6"},
            {"equals": "OTSL HTML"},
            {"equals": "0.965 0.969"}, {"equals": "0.934 0.927"},
            {"equals": "0.955 0.955"}, {"equals": "0.88 0.857"},
            {"equals": "2.73 5.39"},
        ]
        self.assertTrue(audit.one_row_binding(cells, expected))
        self.assertFalse(audit.one_row_binding(cells, [
            {"equals": "OTSL"}, {"equals": "0.965"},
        ]))
        # A transposition changes the paired metric's column ownership.
        cells[4]["text"], cells[5]["text"] = cells[5]["text"], cells[4]["text"]
        self.assertFalse(audit.one_row_binding(cells, expected))

    def test_decimal_point_is_semantically_significant(self):
        self.assertNotEqual(audit.normalized("0.965"), audit.normalized("0 965"))
        self.assertEqual(audit.normalized("Notices . . . vii"), "notices vii")

    def test_only_first_class_cells_count_not_repeated_grid(self):
        doc = {"tables": [{"data": {
            "num_rows": 1, "num_cols": 2, "table_cells": [],
            "grid": [[cell("10 HP", 0, 0), cell("14 A", 0, 1)]],
        }}]}
        check = {"bindings": [{
            "name": "Current", "cells": [
                {"equals": "10 HP"}, {"equals": "14 A"},
            ],
        }]}
        self.assertFalse(audit.score_document(doc, check)[0]["passed"])

    def test_malformed_bounds_cannot_prove_association(self):
        cells = audit.eligible_cells(table(
            cell("10 HP", 0, 0), cell("14 A", 0, 9), rows=1, cols=2,
        ))
        self.assertEqual(len(cells), 1)
        self.assertFalse(audit.one_row_binding(cells, [
            {"equals": "10 HP"}, {"equals": "14 A"},
        ]))


    def test_dimensions_separate_wrong_number_from_wrong_row(self):
        import semantic_dimensions as dims
        case = {"bindings": [{"name": "rating", "cells": [
            {"equals": "10 HP"}, {"equals": "14 A"}]}],
            "text_anchors": ["10 HP"]}
        doc = {"tables": [table(cell("10 HP", 0, 0),
                                 cell("14 A", 1, 1), rows=2, cols=2)]}
        scores = dims.score_dimensions(doc, case, audit.score_document(doc, case))
        self.assertEqual(scores["numbers_units"]["status"], "passed")
        self.assertEqual(scores["numbers_units"]["unit_status"], "measured")
        self.assertEqual(scores["table_bindings"]["status"], "failed")
        self.assertIn("table_row_column_binding_missing",
                      [f["class"] for f in dims.failures(scores)])
        doc["tables"][0]["data"]["table_cells"][1]["text"] = "15 A"
        scores = dims.score_dimensions(doc, case, audit.score_document(doc, case))
        self.assertEqual(scores["numbers_units"]["status"], "failed")
        self.assertIn("numeric_cell_missing", [f["class"] for f in dims.failures(scores)])
        for name in ("reading_order", "cross_references", "bounding_boxes"):
            self.assertEqual(scores[name]["status"], "unsupported")
            self.assertEqual(scores[name]["total"], 0)

    def test_reading_order_rejects_inversion_and_duplicate_rows(self):
        import semantic_dimensions as dims
        first = {"name": "first", "cells": [{"equals": "A"}, {"equals": "1"}]}
        second = {"name": "second", "cells": [{"equals": "B"}, {"equals": "2"}]}
        case = {"bindings": [first, second], "reading_order": ["first", "second"]}
        cells = [cell("A", 0, 0), cell("1", 0, 1),
                 cell("B", 1, 0), cell("2", 1, 1)]
        doc = {"tables": [table(*cells, rows=3, cols=2)]}
        self.assertEqual(dims.score_dimensions(
            doc, case, audit.score_document(doc, case))["reading_order"]["status"], "passed")
        for item in cells:
            item["start_row_offset_idx"] = 1 - item["start_row_offset_idx"]
            item["end_row_offset_idx"] = item["start_row_offset_idx"] + 1
        self.assertEqual(dims.score_dimensions(
            doc, case, audit.score_document(doc, case))["reading_order"]["status"], "failed")
        for item in cells:
            item["start_row_offset_idx"] = 1 - item["start_row_offset_idx"]
            item["end_row_offset_idx"] = item["start_row_offset_idx"] + 1
        doc["tables"][0]["data"]["table_cells"].extend([cell("A", 2, 0), cell("1", 2, 1)])
        self.assertEqual(dims.score_dimensions(
            doc, case, audit.score_document(doc, case))["reading_order"]["status"], "failed")

    def test_review_exposes_unsupported_dimensions(self):
        import semantic_dimensions as dims
        scores = dims.score_dimensions({"tables": []},
            {"bindings": [], "text_anchors": []}, [])
        report = {"results": [{"id": "small", "source": "fixture.pdf",
                               "passed": False, "dimensions": scores,
                               "failures": dims.failures(scores)}]}
        text = dims.review_markdown(report)
        self.assertIn("| bounding_boxes | unsupported | 0/0 |", text)
        self.assertIn("| cross_references | unsupported | 0/0 |", text)


    def test_reference_only_scores_pinned_real_documents_without_models(self):
        import json
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as folder:
            report = Path(folder) / "scores.json"
            review = Path(folder) / "review.md"
            exit_status = audit.main(["--reference-only", "--report", str(report),
                                      "--review", str(review)])
            self.assertEqual(exit_status, 0)
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(data["schema_version"], 2)
            self.assertEqual(data["summary"]["bindings"], 8)
            self.assertEqual(data["summary"]["passed_bindings"], 8)
            self.assertEqual(data["extraction"]["engine"], "pinned_python_reference")
            self.assertTrue(review.is_file())
            self.assertEqual([r["id"] for r in data["results"]],
                             ["ibm-redp5110-toc", "otsl-paper-hpo-table"])
            self.assertEqual(data["results"][1]["dimensions"]["reading_order"]["status"],
                             "passed")
            for item in data["results"]:
                self.assertEqual(item["dimensions"]["cross_references"]["status"],
                                 "unsupported")
                self.assertEqual(item["dimensions"]["bounding_boxes"]["status"],
                                 "unsupported")

    def test_report_only_cannot_hide_missing_input(self):
        import json
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as folder:
            tmp = Path(folder)
            manifest = tmp / "cases.json"
            manifest.write_text(json.dumps({
                "schema_version": 1, "cases": [{
                    "id": "missing", "source": "tests/data/pdf/sources/missing.pdf",
                    "source_git_blob": "bad", "source_url": "not-present",
                    "original_pdf_pages": [1], "bindings": []
                }]
            }), encoding="utf-8")
            report = tmp / "report.json"
            result = audit.main(["--cases", str(manifest), "--reference-only",
                                 "--report-only", "--report", str(report),
                                 "--review", str(tmp / "review.md")])
            self.assertEqual(result, 1)
            rows = json.loads(report.read_text(encoding="utf-8"))["results"]
            self.assertEqual(rows[0]["passed"], False)
            self.assertEqual(rows[0]["error_class"], "input_or_reference_integrity")

    def test_failed_binding_includes_bounded_minimal_repro(self):
        import semantic_dimensions as dims
        case = {"bindings": [{"name": "rating", "cells": [
            {"equals": "10 HP"}, {"equals": "14 A"}]}]}
        doc = {"tables": [table(cell("10 HP", 0, 0),
                                 cell("14 A", 1, 1), rows=2, cols=2)]}
        repro = dims.minimal_repros(doc, case, audit.score_document(doc, case))
        self.assertEqual(len(repro), 1)
        self.assertEqual(repro[0]["expected_source_cells"], case["bindings"][0]["cells"])
        self.assertEqual(repro[0]["observed_candidate_count"], 2)
        self.assertEqual([x["row"] for x in repro[0]["observed_matching_cells"]],
                         [[0, 1], [1, 2]])


if __name__ == "__main__":
    unittest.main()
