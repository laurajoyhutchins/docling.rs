#!/usr/bin/env python3
"""Source-grounded PDF table checks, independent of Docling's Markdown oracle.

Runs a real Rust conversion or scores previously generated Docling JSON.
Only first-class table_cells count: flattened prose, Markdown pipes, and
repeated grid slots cannot establish a row/column association.

    python3 scripts/conformance/semantic_tables.py
    python3 scripts/conformance/semantic_tables.py --json-dir target/semantic-input
    python3 scripts/conformance/semantic_tables.py --json-dir target/semantic-input --report-only

No ML models are fetched by this script. Live conversion requires the same
installed models as the repository's PDF conformance harness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = Path(__file__).with_name("semantic_tables_cases.json")


def source_digests(path: Path) -> tuple[str, str]:
    content = path.read_bytes()
    sha256 = hashlib.sha256(content).hexdigest()
    git_blob = hashlib.sha1(
        b"blob " + str(len(content)).encode("ascii") + b"\0" + content,
        usedforsecurity=False,
    ).hexdigest()
    return sha256, git_blob


def normalized(text: str) -> str:
    # Ignore dot leaders, but preserve decimal points BETWEEN digits:
    # 0.965 must not equal "0 965", and 0.965 must not equal 0.969.
    return " ".join(re.findall(r"[^\W\d_]+|\d+(?:\.\d+)*", text.casefold()))


def matches(cell_text: str, fact: dict[str, str]) -> bool:
    actual = normalized(cell_text)
    if "equals" in fact:
        return actual == normalized(fact["equals"])
    if "contains" in fact:
        needle = normalized(fact["contains"])
        return bool(needle) and f" {needle} " in f" {actual} "
    raise ValueError("each expected fact needs exactly one of equals/contains")


def eligible_cells(table: dict[str, Any]) -> list[dict[str, Any]]:
    data = table.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("table_cells"), list):
        return []
    count_rows, count_cols = data.get("num_rows"), data.get("num_cols")
    if not isinstance(count_rows, int) or not isinstance(count_cols, int):
        return []
    result = []
    for cell in data["table_cells"]:
        if not isinstance(cell, dict) or not isinstance(cell.get("text"), str):
            continue
        row = cell.get("start_row_offset_idx")
        row_end = cell.get("end_row_offset_idx")
        col = cell.get("start_col_offset_idx")
        col_end = cell.get("end_col_offset_idx")
        if any(type(n) is not int for n in (row, row_end, col, col_end)):
            continue
        if not (0 <= row < row_end <= count_rows and 0 <= col < col_end <= count_cols):
            continue
        result.append(cell)
    return result


def one_row_binding(cells: list[dict[str, Any]], facts: list[dict[str, str]]) -> bool:
    """Find distinct cells in a shared logical row, in strictly ordered columns.

    A row-spanning cell is eligible on every row it actually covers. A cell
    containing two concatenated values cannot satisfy two separate facts.
    """
    if len(facts) < 2:
        raise ValueError("row binding must contain at least two column facts")
    if not cells:
        return False
    candidates = [
        [i for i, cell in enumerate(cells) if matches(cell["text"], fact)]
        for fact in facts
    ]
    if any(not group for group in candidates):
        return False

    def search(
        index: int, previous_end: int, row_start: int, row_end: int,
        used: frozenset[int],
    ) -> bool:
        if index == len(candidates):
            return row_start < row_end
        for i in candidates[index]:
            cell = cells[i]
            shared_start = max(row_start, cell["start_row_offset_idx"])
            shared_end = min(row_end, cell["end_row_offset_idx"])
            if (
                i not in used
                and cell["start_col_offset_idx"] >= previous_end
                and shared_start < shared_end
                and search(index + 1, cell["end_col_offset_idx"],
                           shared_start, shared_end, used | {i})
            ):
                return True
        return False

    return search(0, 0, 0, sys.maxsize, frozenset())


def score_document(document: dict[str, Any], case: dict[str, Any]) -> list[dict[str, Any]]:
    tables = document.get("tables")
    if not isinstance(tables, list):
        tables = []
    all_tables = [eligible_cells(t) for t in tables if isinstance(t, dict)]
    checks = []
    for assertion in case["bindings"]:
        facts = assertion["cells"]
        valid = any(one_row_binding(cells, facts) for cells in all_tables)
        checks.append({
            "name": assertion["name"],
            "passed": valid,
            "expected_cells": facts,
            "reason": None if valid else
                "No single table row contains all values in distinct ordered source cells",
        })
    return checks


def convert(source: Path, binary: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [str(binary), "--skip-ocr", "--compact-tables", "--to", "json", str(source)],
        check=False, text=True, capture_output=True,
    )
    if proc.returncode:
        raise RuntimeError(f"docling-rs exited {proc.returncode}: {proc.stderr[-1000:]}")
    return json.loads(proc.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--bin", type=Path, default=ROOT / "target/release/docling-rs")
    parser.add_argument("--json-dir", type=Path, help="Read CASE_ID.json instead of running Rust")
    parser.add_argument("--report", type=Path, default=ROOT / "target/semantic_tables.json")
    parser.add_argument("--review", type=Path, default=ROOT / "target/semantic_review.md")
    parser.add_argument("--reference-only", action="store_true",
                        help="Validate and score pinned upstream JSON, no Rust binary or models")
    parser.add_argument("--report-only", action="store_true",
                        help="Report failures without returning a failing exit code")
    args = parser.parse_args(argv)
    manifest = json.loads(args.cases.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("cases"), list):
        parser.error("unsupported semantic-table fixture schema")
    from semantic_dimensions import (
        score_dimensions, failures, review_markdown, minimal_repros
    )

    results = []
    integrity_failed = False
    for case in manifest["cases"]:
        name = case["id"]
        entry: dict[str, Any] = {"id": name, "source": case["source"]}
        try:
            source = (ROOT / case["source"]).resolve(strict=True)
            if not source.is_relative_to(ROOT):
                raise ValueError("source must be inside the checkout")
            sha256, git_blob = source_digests(source)
            entry["source_sha256"] = sha256
            entry["source_git_blob"] = git_blob
            entry["source_url"] = case["source_url"]
            entry["source_rights_status"] = case.get("rights_status", "not independently verified")
            entry["review_scope"] = case.get("review_scope", "not recorded")
            entry["original_pdf_sha256"] = None  # sampled source digest != full linked source digest
            entry["original_pdf_pages"] = case["original_pdf_pages"]
            if git_blob != case["source_git_blob"]:
                raise ValueError(
                    f"source fixture changed: expected git blob {case['source_git_blob']}, got {git_blob}"
                )
            # An impossible benchmark must fail before testing Rust. This
            # independent upstream sanity gate caught old HPO assertions
            # that required OTSL/HTML numbers in distinct cells even though
            # the pinned Docling JSON keeps each method pair in one cell.
            reference_root = (ROOT / "tests/data/pdf/groundtruth").resolve(strict=True)
            reference = (ROOT / case["reference_json"]).resolve(strict=True)
            if not reference.is_relative_to(reference_root):
                raise ValueError("reference JSON must be in the pinned groundtruth directory")
            ref_sha256, ref_blob = source_digests(reference)
            entry["reference_json"] = case["reference_json"]
            entry["reference_sha256"] = ref_sha256
            entry["reference_git_blob"] = ref_blob
            if ref_blob != case["reference_git_blob"]:
                raise ValueError(
                    f"reference JSON changed: expected git blob {case['reference_git_blob']}, got {ref_blob}"
                )
            reference_doc = json.loads(reference.read_text(encoding="utf-8"))
            if not isinstance(reference_doc, dict):
                raise ValueError("reference JSON root must be an object")
            invalid_reference_checks = [
                check["name"] for check in score_document(reference_doc, case)
                if not check["passed"]
            ]
            if invalid_reference_checks:
                raise ValueError(
                    "fixture claims contradict pinned upstream reference: "
                    + ", ".join(invalid_reference_checks)
                )
            entry["reference_compatible"] = True
            if args.reference_only:
                doc = reference_doc
            elif args.json_dir is None:
                doc = convert(source, args.bin.resolve())
            else:
                doc = json.loads((args.json_dir / f"{name}.json").read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                raise ValueError("Docling JSON root must be an object")
            entry["checks"] = score_document(doc, case)
            entry["dimensions"] = score_dimensions(doc, case, entry["checks"])
            entry["failures"] = failures(entry["dimensions"])
            entry["minimal_repros"] = minimal_repros(doc, case, entry["checks"])
            entry["unassigned_words"] = {
                "status": "unsupported",
                "reason": "No audited unassigned-source-word contract for this JSON extraction path",
            }
            entry["passed"] = all(v["status"] != "failed" for v in entry["dimensions"].values())
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            integrity_failed = True
            entry["passed"] = False
            entry["error"] = str(error)
            entry["error_class"] = (
                "input_or_reference_integrity"
                if "source" in str(error).lower() or "reference" in str(error).lower()
                else "conversion_or_input_error"
            )
            entry["failures"] = [{"dimension": "runner", "name": name,
                                   "class": entry["error_class"]}]
        results.append(entry)
        ok = sum(c["passed"] for c in entry.get("checks", []))
        total = len(entry.get("checks", []))
        print(f"{'PASS' if entry['passed'] else 'FAIL'} {name}: {ok}/{total} row bindings")
        if "error" in entry:
            print(f"  {entry['error']}", file=sys.stderr)
        for check in entry.get("checks", []):
            if not check["passed"]:
                print(f"  missing: {check['name']}")
    summary = {
        "cases": len(results),
        "passed_cases": sum(r["passed"] for r in results),
        "bindings": sum(len(r.get("checks", [])) for r in results),
        "passed_bindings": sum(sum(c["passed"] for c in r.get("checks", []))
                               for r in results),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    extraction = {
        "engine": "pinned_python_reference" if args.reference_only else
                  ("precomputed_json" if args.json_dir is not None else "rust"),
        "cli_args": ["--skip-ocr", "--compact-tables", "--to", "json"],
        "input_json_dir": str(args.json_dir) if args.json_dir else None,
    }
    if args.json_dir is None and not args.reference_only:
        extraction["binary_path"] = str(args.bin)
        if args.bin.is_file():
            extraction["binary_sha256"] = hashlib.sha256(args.bin.read_bytes()).hexdigest()
        # These record observable configuration, not evidence a model was loaded.
        import os
        extraction["settings"] = {
            key: os.environ.get(key, "not explicitly pinned")
            for key in ("DOCLING_RS_RENDERER", "DOCLING_RS_FP32",
                        "DOCLING_RS_PDF_THREADS", "DOCLING_RS_SLOW_RESIZE")
        }
        defaults = {
            "DOCLING_LAYOUT_ONNX": ".models/layout_heron.onnx",
            "DOCLING_TABLEFORMER_ENCODER": ".models/tableformer/encoder.onnx",
            "DOCLING_TABLEFORMER_DECODER": ".models/tableformer/decoder.onnx",
            "DOCLING_TABLEFORMER_BBOX": ".models/tableformer/bbox.onnx",
        }
        extraction["model_asset_candidates"] = {}
        for key, default in defaults.items():
            path = Path(os.environ.get(key, str(ROOT / default)))
            value = {"path": str(path), "explicit_override": key in os.environ}
            if path.is_file():
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                value["sha256"] = digest.hexdigest()
            else:
                value["status"] = "absent_or_unavailable"
            extraction["model_asset_candidates"][key] = value
        # Fingerprinted candidates may not be the models actually selected by
        # the runtime; the PDF model loader must be audited independently.
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                  text=True, capture_output=True, check=False)
        extraction["checkout_revision"] = (
            revision.stdout.strip() if revision.returncode == 0 else None)
    report = {"schema_version": 2, "summary": summary, "results": results,
              "extraction": extraction,
              "scope": "sampled original-PDF facts, not whole-document or engineering-rule correctness"}
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    args.review.parent.mkdir(parents=True, exist_ok=True)
    args.review.write_text(review_markdown(report), encoding="utf-8")
    print(f"Evidence: {args.report}; review: {args.review}")
    # --report-only may mask observed semantic disagreement but never broken inputs.
    return 1 if integrity_failed else (
        0 if args.report_only or summary["passed_cases"] == summary["cases"] else 1)


if __name__ == "__main__":
    sys.exit(main())
