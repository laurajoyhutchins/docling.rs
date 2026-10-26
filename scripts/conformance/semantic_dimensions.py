#!/usr/bin/env python3
"""Source-reviewed semantic dimensions. Unsupported evidence never scores as success."""
from __future__ import annotations

import re
from typing import Any

from semantic_tables import eligible_cells, matches, normalized, one_row_binding

NUMBER = re.compile(r"\d+(?:\.\d+)*")
UNIT = re.compile(r"(?<!\w)(?:kV|kA|kW|MW|Hz|kHz|mm|ms|V|A|W|HP|°C)(?!\w)", re.I)
DIMENSIONS = ("text_recall", "numbers_units", "table_bindings",
              "reading_order", "cross_references", "bounding_boxes")


def metric(checks: list[dict[str, Any]], unsupported: str) -> dict[str, Any]:
    if not checks:
        return {"status": "unsupported", "passed": 0, "total": 0,
                "reason": unsupported, "checks": []}
    passed = sum(c["passed"] for c in checks)
    return {"status": "passed" if passed == len(checks) else "failed",
            "passed": passed, "total": len(checks), "checks": checks}


def check(name: str, passed: bool, failure_class: str, **evidence: Any) -> dict[str, Any]:
    return {"name": name, "passed": passed,
            "failure_class": None if passed else failure_class, **evidence}


def observed_cells(document: dict[str, Any]) -> list[dict[str, Any]]:
    tables = document.get("tables")
    if not isinstance(tables, list):
        return []
    return [c for t in tables if isinstance(t, dict) for c in eligible_cells(t)]


def row_locations(document: dict[str, Any], facts: list[dict[str, str]]) -> list[tuple[int, int]]:
    """Logical table and row identities, never Markdown line numbers."""
    positions = []
    tables = document.get("tables")
    if not isinstance(tables, list):
        return positions
    for i, table in enumerate(tables):
        if not isinstance(table, dict):
            continue
        data = table.get("data")
        rows = data.get("num_rows") if isinstance(data, dict) else None
        if type(rows) is not int or rows < 0:
            continue
        cells = eligible_cells(table)
        for row in range(rows):
            covering = [c for c in cells if c["start_row_offset_idx"] <= row
                        < c["end_row_offset_idx"]]
            if one_row_binding(covering, facts):
                positions.append((i, row))
    return positions


def score_dimensions(
    document: dict[str, Any], case: dict[str, Any],
    bindings: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    cells = observed_cells(document)
    texts = document.get("texts")
    fragments = [c["text"] for c in cells]
    if isinstance(texts, list):
        fragments += [t["text"] for t in texts if isinstance(t, dict)
                      and isinstance(t.get("text"), str)]
    anchors = case.get("text_anchors", [])
    text = metric([
        check(anchor, any(normalized(anchor) in normalized(s) for s in fragments),
              "text_anchor_missing")
        for anchor in anchors
    ], "No independently reviewed text anchors")
    numeric_checks = []
    unit_facts = 0
    for binding in case["bindings"]:
        for fact in binding["cells"]:
            expected = fact.get("equals", fact.get("contains", ""))
            if not NUMBER.search(expected):
                continue
            unit_facts += bool(UNIT.search(expected))
            numeric_checks.append(check(
                binding["name"] + ": " + expected,
                any(matches(c["text"], fact) for c in cells),
                "numeric_cell_missing", expected=expected))
    numbers = metric(numeric_checks, "No reviewed numeric cells")
    numbers["unit_facts"] = unit_facts
    numbers["unit_status"] = "measured" if unit_facts else "unsupported"
    if not unit_facts:
        numbers["unit_reason"] = "No reviewed engineering-unit facts in these samples"
    tables = metric([
        check(c["name"], c["passed"], "table_row_column_binding_missing")
        for c in bindings
    ], "No source-reviewed bindings")
    order_names = case.get("reading_order", [])
    order_checks = []
    if order_names:
        by_name = {b["name"]: b for b in case["bindings"]}
        positions = [row_locations(document, by_name[name]["cells"])
                     for name in order_names]
        unique = all(len(p) == 1 for p in positions)
        ordered = unique and all(a[0] < b[0] for a, b in zip(positions, positions[1:]))
        order_checks.append(check(
            "reviewed table-row sequence", bool(ordered),
            "reading_order_ambiguous_or_missing" if not unique else "reading_order_inverted",
            expected=order_names, observed=positions))
    return {
        "text_recall": text, "numbers_units": numbers, "table_bindings": tables,
        "reading_order": metric(order_checks, "No source-reviewed reading-order oracle"),
        "cross_references": metric([], "No reviewed source-to-target reference identities"),
        "bounding_boxes": metric([], "No source-reviewed PDF rectangles and tolerances"),
    }


def failures(dimensions: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    return [{"dimension": dimension, "name": c["name"], "class": c["failure_class"]}
            for dimension, score in dimensions.items()
            for c in score["checks"] if not c["passed"]]


def review_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Source-grounded PDF semantic review", "",
        "Sampled source facts only, not trusted regulatory rules or complete-document recall.", "",
    ]
    for case in report["results"]:
        lines.extend([
            "## " + case["id"], "",
            "- Source sample: " + case["source"],
            "- Original URL: " + case.get("source_url", "not recorded"),
            "- Original page(s): " + str(case.get("original_pdf_pages", [])),
            "- Input SHA-256: " + case.get("source_sha256", "unavailable"),
            "- Reference SHA-256: " + case.get("reference_sha256", "unavailable"),
            "- Overall: " + ("PASS" if case["passed"] else "FAIL"),
            "", "| Dimension | Status | Measured |", "| --- | --- | --- |",
        ])
        for name in DIMENSIONS:
            score = case.get("dimensions", {}).get(name, {})
            lines.append("| {} | {} | {}/{} |".format(
                name, score.get("status", "not_run"),
                score.get("passed", 0), score.get("total", 0)))
        lines.extend(["", "### Failures and unsupported checks", ""])
        for f in case.get("failures", []):
            lines.append("- {}: {} [{}]".format(f["dimension"], f["name"], f["class"]))
        for name, value in case.get("dimensions", {}).items():
            if value["status"] == "unsupported":
                lines.append("- {}: unsupported ({})".format(name, value["reason"]))
        unit = case.get("dimensions", {}).get("numbers_units", {})
        if unit.get("unit_status") == "unsupported":
            lines.append("- Engineering units: unsupported ({})".format(unit["unit_reason"]))
        if case.get("error"):
            lines.append("- {}: {}".format(case.get("error_class", "runner_error"), case["error"]))
        lines.append("")
    lines += ["## Boundary", "",
              "An independent original-PDF audit is required before accepting extracted engineering facts.",
              "Python-reference compatibility is not source correctness.",
              "The existing strict Markdown parity suite is unchanged.", ""]
    return "\n".join(lines)
