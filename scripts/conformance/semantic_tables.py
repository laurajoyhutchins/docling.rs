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
    # Dot leaders and whitespace are not table semantics. Preserve every
    # digit: decimal values 0.965 and 0.969 must remain distinguishable.
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


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
    parser.add_argument("--report-only", action="store_true",
                        help="Report failures without returning a failing exit code")
    args = parser.parse_args(argv)
    manifest = json.loads(args.cases.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("cases"), list):
        parser.error("unsupported semantic-table fixture schema")
    results = []
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
            entry["original_pdf_pages"] = case["original_pdf_pages"]
            if git_blob != case["source_git_blob"]:
                raise ValueError(
                    f"source fixture changed: expected git blob {case['source_git_blob']}, got {git_blob}"
                )
            if args.json_dir is None:
                doc = convert(source, args.bin.resolve())
            else:
                doc = json.loads((args.json_dir / f"{name}.json").read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                raise ValueError("Docling JSON root must be an object")
            entry["checks"] = score_document(doc, case)
            entry["passed"] = all(c["passed"] for c in entry["checks"])
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            entry["passed"] = False
            entry["error"] = str(error)
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
    args.report.write_text(
        json.dumps({"schema_version": 1, "summary": summary, "results": results},
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Evidence: {args.report}")
    return 0 if args.report_only or summary["passed_cases"] == summary["cases"] else 1


if __name__ == "__main__":
    sys.exit(main())
