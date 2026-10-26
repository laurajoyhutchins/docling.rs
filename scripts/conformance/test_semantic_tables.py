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


if __name__ == "__main__":
    unittest.main()
