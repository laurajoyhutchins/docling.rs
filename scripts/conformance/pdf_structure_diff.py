#!/usr/bin/env python3
"""Compare Docling JSON structure without treating Markdown parity as semantics.

The report distinguishes changes to item labels, ordered ownership and table
grid shapes. It deliberately does not claim source-document correctness:
a Python-Docling reference is an upstream parity oracle, not a source oracle.

Usage:
    python3 scripts/conformance/pdf_structure_diff.py \
      --reference tests/data/pdf/groundtruth/normal_4pages.json \
      --actual target/normal_4pages.json --out target/normal4-structure.json
"""

import argparse
from collections import Counter
import difflib
import hashlib
import json
from pathlib import Path
import re
import sys


REF = re.compile(r"^#/([a-zA-Z_]+)/([0-9]+)$")
BUCKETS = ("texts", "tables", "pictures", "groups", "key_value_items", "form_items")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def walk(document: dict) -> list[dict]:
    """Flatten typed body/group ownership in declared order, retaining paths."""
    if document.get("schema_name") != "DoclingDocument":
        raise ValueError("not a DoclingDocument")
    if not isinstance(document.get("body", {}).get("children"), list):
        raise ValueError("missing body children")
    for bucket in BUCKETS:
        if bucket in document and not isinstance(document[bucket], list):
            raise ValueError(f"malformed {bucket} collection")
    seen = set()
    rows = []

    def follow(pointer: str, ancestors: tuple[str, ...]):
        match = REF.fullmatch(pointer)
        if not match:
            raise ValueError(f"malformed item reference: {pointer}")
        bucket, index = match.group(1), int(match.group(2))
        if bucket not in BUCKETS or index >= len(document.get(bucket, [])):
            raise ValueError(f"dangling item reference: {pointer}")
        if pointer in seen:
            raise ValueError(f"cycle or multiply-owned item reference: {pointer}")
        seen.add(pointer)
        item = document[bucket][index]
        if not isinstance(item, dict):
            raise ValueError(f"non-object item: {pointer}")
        label = item.get("label", "")
        text = item.get("text", "") or ""
        if not isinstance(text, str):
            raise ValueError(f"non-string source text: {pointer}")
        parent = item.get("parent", {}).get("$ref", "#/body")
        actual_parent = ancestors[-1] if ancestors else "#/body"
        if parent != actual_parent:
            raise ValueError(f"parent mismatch: {pointer}: {parent} != {actual_parent}")
        grid = item.get("data", {})
        rows.append({
            "ref": pointer,
            "bucket": bucket,
            "label": label,
            "text": text,
            "parent_labels": list(ancestors),
            "table_shape": [grid.get("num_rows"), grid.get("num_cols")]
            if bucket == "tables" and isinstance(grid, dict) else None,
            "page": (item.get("prov") or [{}])[0].get("page_no"),
        })
        children = item.get("children", [])
        if children and bucket != "groups":
            # Docling pictures and other elements can own nested text,
            # too. Follow them rather than flattening their contents away.
            pass
        for child in children:
            if not isinstance(child, dict) or not isinstance(child.get("$ref"), str):
                raise ValueError(f"invalid child of {pointer}")
            follow(child["$ref"], (*ancestors, pointer))

    for child in document["body"]["children"]:
        if not isinstance(child, dict) or not isinstance(child.get("$ref"), str):
            raise ValueError("invalid body child reference")
        follow(child["$ref"], ())
    return rows


def shape_counts(rows: list[dict]) -> dict:
    return dict(sorted(Counter(f"{row['bucket']}:{row['label']}" for row in rows).items()))


def signature(row: dict) -> tuple:
    """Ignore allocation index; retain visible identity and ownership depth."""
    return (row["bucket"], row["label"], row["text"],
            len(row["parent_labels"]), tuple(row["table_shape"] or ()))


def summarize(reference: dict, actual: dict) -> dict:
    old, new = walk(reference), walk(actual)
    a, b = [signature(r) for r in old], [signature(r) for r in new]
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    changes = []
    for tag, i0, i1, j0, j1 in sm.get_opcodes():
        if tag == "equal":
            continue
        changes.append({
            "change": tag,
            "reference_range": [i0, i1],
            "actual_range": [j0, j1],
            "reference": [{k: r[k] for k in ("ref", "label", "bucket", "text", "page", "table_shape")}
                          for r in old[i0:min(i1, i0 + 3)]],
            "actual": [{k: r[k] for k in ("ref", "label", "bucket", "text", "page", "table_shape")}
                       for r in new[j0:min(j1, j0 + 3)]],
        })
    # Correlate identical *unique* source text even when its item index moved.
    # Duplicate or empty strings are intentionally not guessed into matches.
    old_by_text = {}
    new_by_text = {}
    for rows, target in ((old, old_by_text), (new, new_by_text)):
        counts = Counter(r["text"] for r in rows if r["text"])
        target.update({r["text"]: r for r in rows if r["text"] and counts[r["text"]] == 1})
    classifications = []
    for text in sorted(old_by_text.keys() & new_by_text.keys()):
        before, after = old_by_text[text], new_by_text[text]
        if before["label"] != after["label"] or before["bucket"] != after["bucket"]:
            classifications.append({
                "text": text[:160], "reference_label": before["label"],
                "actual_label": after["label"], "reference_bucket": before["bucket"],
                "actual_bucket": after["bucket"],
            })
    return {
        "structurally_equal": a == b,
        "reference_nodes": len(old),
        "actual_nodes": len(new),
        "reference_classes": shape_counts(old),
        "actual_classes": shape_counts(new),
        "classifications": classifications,
        "ordered_differences": changes,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--actual", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--require-parity", action="store_true",
                        help="fail only when explicitly gating an agreed reference")
    args = parser.parse_args(argv)
    ref = json.loads(args.reference.read_text())
    actual = json.loads(args.actual.read_text())
    result = summarize(ref, actual)
    result["reference_sha256"] = digest(args.reference)
    result["actual_sha256"] = digest(args.actual)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "reference_nodes": result["reference_nodes"],
        "actual_nodes": result["actual_nodes"],
        "difference_groups": len(result["ordered_differences"]),
        "class_reassignments": len(result["classifications"]),
        "structurally_equal": result["structurally_equal"],
        "report": str(args.out),
    }))
    return 1 if args.require_parity and not result["structurally_equal"] else 0


if __name__ == "__main__":
    sys.exit(main())
