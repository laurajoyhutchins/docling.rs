#!/usr/bin/env python3
"""Reproduce model-backed PDF output without confusing baseline drift with code changes.

Build the two CLI binaries *before* running this tool. Inputs, reference PDFs,
model weights and the renderer are hashed in the retained JSON report. Both
revisions run twice in the same process environment and in single-thread ONNX
mode. A parity difference does not fail the run; inconsistent repeat output
or a failed conversion does.

Example (from the repository root, with models installed):
    python3 scripts/conformance/pdf_repro.py \
      --baseline /tmp/docling/base --baseline-revision <40-hex-sha> \
      --candidate target/release/docling-rs --candidate-revision <40-hex-sha> \
      --source tests/data/pdf/sources/2206.01062.pdf --out target/pdf-repro
"""

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


MODEL_ENV = {
    "DOCLING_LAYOUT_ONNX": ".models/layout_heron.onnx",
    "DOCLING_TABLEFORMER_ENCODER": ".models/tableformer/encoder.onnx",
    "DOCLING_TABLEFORMER_DECODER": ".models/tableformer/decoder.onnx",
    "DOCLING_TABLEFORMER_BBOX": ".models/tableformer/bbox.onnx",
}
PINNED_ENV = {
    "DOCLING_RS_PDF_THREADS": "1",
    "DOCLING_RS_SLOW_RESIZE": "1",
    "DOCLING_RS_FP32": "1",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def changed_lines(expected: bytes, actual: bytes) -> int:
    """The same added+removed-line accounting used by pdf_groundtruth.sh."""
    a = expected.decode("utf-8").rstrip("\n").splitlines()
    b = actual.decode("utf-8").rstrip("\n").splitlines()
    return sum((i2 - i1) + (j2 - j1)
               for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                   None, a, b, autojunk=False).get_opcodes()
               if tag != "equal")


def validate_revision(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise argparse.ArgumentTypeError("expected an immutable 40-character Git commit SHA")
    return value


def required_file(path: Path) -> Path:
    path = path.resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"required nonempty file missing: {path}")
    return path


def prepare_environment(root: Path, renderer: str) -> tuple[dict, dict]:
    env = os.environ.copy()
    # Never silently inherit unpinned model or thread settings from a runner.
    env.update(PINNED_ENV)
    assets = {}
    for key, default in MODEL_ENV.items():
        path = required_file(Path(env.get(key, root / default)))
        env[key] = str(path)
        assets[key] = {"path": str(path), "sha256": sha256(path)}
    env["DOCLING_RS_RENDERER"] = renderer
    if renderer == "docling-parse":
        folder = Path(env.get("DOCLING_PARSE_RENDER_LIB", root / ".docling-parse/lib"))
        dylibs = sorted(folder.glob("libdparse_render.*"))
        if not dylibs:
            raise ValueError(f"docling-parse renderer not installed: {folder}")
        dylib = required_file(dylibs[0])
        env["DOCLING_PARSE_RENDER_LIB"] = str(folder.resolve())
        assets["renderer"] = {"path": str(dylib), "sha256": sha256(dylib)}
    return env, assets


def execute(binary: Path, source: Path, env: dict, output: Path) -> dict:
    result = subprocess.run(
        [str(binary), "--skip-ocr", "--compact-tables", str(source)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    output.with_suffix(".stderr.txt").write_bytes(result.stderr)
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(
            f"conversion failed (exit={result.returncode}, empty={not bool(result.stdout.strip())}): "
            f"{source}, stderr={output.with_suffix('.stderr.txt')}"
        )
    output.with_suffix(".md").write_bytes(result.stdout)
    return {"output": str(output.with_suffix(".md")),
            "sha256": hashlib.sha256(result.stdout).hexdigest(),
            "bytes": len(result.stdout)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--baseline-revision", required=True, type=validate_revision)
    parser.add_argument("--candidate-revision", required=True, type=validate_revision)
    parser.add_argument("--source", action="append", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--baseline-renderer", default="docling-parse",
                        choices=["docling-parse", "rust"])
    parser.add_argument("--candidate-renderer", default="docling-parse",
                        choices=["docling-parse", "rust"])
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args(argv)
    if args.repeats < 2:
        parser.error("--repeats must be >= 2 to establish reproducibility")

    root = Path.cwd().resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    bins = {"baseline": required_file(args.baseline), "candidate": required_file(args.candidate)}
    settings = {
        "baseline": prepare_environment(root, args.baseline_renderer),
        "candidate": prepare_environment(root, args.candidate_renderer),
    }
    report = {
        "schema_version": 1,
        "source_revisions": {
            "baseline": args.baseline_revision,
            "candidate": args.candidate_revision,
        },
        "binaries": {name: {"path": str(binary), "sha256": sha256(binary)}
                     for name, binary in bins.items()},
        "configuration": {name: {"env": {key: env[key] for key in
                              (*PINNED_ENV, *MODEL_ENV, "DOCLING_RS_RENDERER")},
                                "assets": assets}
                          for name, (env, assets) in settings.items()},
        "cases": [],
    }
    unstable = False
    failures = []
    for source_arg in args.source:
        source = required_file(source_arg)
        # Stable collision-free output name even if two PDFs share a filename.
        case_id = hashlib.sha256(str(source).encode()).hexdigest()[:12]
        case = {"source": str(source), "source_sha256": sha256(source),
                "runs": {}, "stable": {}, "changed_lines": {}}
        gt = root / "tests/data/pdf/groundtruth" / (source.stem + ".md")
        if gt.is_file():
            case["groundtruth"] = {"path": str(gt), "sha256": sha256(gt)}
        for name, binary in bins.items():
            env, _ = settings[name]
            case["runs"][name] = []
            for attempt in range(args.repeats):
                artifact = out / f"{case_id}-{name}-{attempt}"
                try:
                    run = execute(binary, source, env, artifact)
                    if gt.is_file():
                        run["groundtruth_changed_lines"] = changed_lines(
                            gt.read_bytes(), Path(run["output"]).read_bytes())
                    case["runs"][name].append(run)
                except RuntimeError as exc:
                    case["runs"][name].append({"error": str(exc)})
                    failures.append(str(exc))
            hashes = [r.get("sha256") for r in case["runs"][name]]
            case["stable"][name] = len(set(hashes)) == 1 and None not in hashes
            unstable |= not case["stable"][name]
        first = [case["runs"][name][0] for name in ("baseline", "candidate")]
        if all("output" in run for run in first):
            case["changed_lines"]["baseline_vs_candidate"] = changed_lines(
                Path(first[0]["output"]).read_bytes(), Path(first[1]["output"]).read_bytes())
        report["cases"].append(case)
    report["repeatable"] = not unstable
    report["conversions_succeeded"] = not failures
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"report": str(out / "report.json"), "repeatable": not unstable,
                      "conversions_succeeded": not failures, "cases": len(report["cases"])}))
    return 0 if not unstable and not failures else 1


if __name__ == "__main__":
    sys.exit(main())
