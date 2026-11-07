# Direct verification of PDF table parsing

This is an **execution command in the source repository**, not another CI system. It reuses a local or disposable development VM's Cargo cache and pinned model files. `verify_direct.py` does not implement PDF extraction, emulate TableFormer, or generate ground truth.

## Fast proof at an exact commit

From a **clean checkout** of the reviewed commit with Rust 1.96.0 (rustfmt and Clippy) available:

```sh
python3 scripts/conformance/verify_direct.py \
  --repo . \
  --expected-sha '<reviewed 40-character Git commit SHA>' \
  --out /tmp/docling-proof/<reviewed-sha> \
  --mode focused
```

`PASS_FOCUSED` requires the exact commit and Git tree, a clean checkout, formatting, the malformed-geometry, ownership, original-source-text and cross-column ambiguity tests, all `docling-pdf` Rust library tests, and `clippy -D warnings` on Rust 1.96. This does **not** establish real-PDF model fidelity.

Failure leaves `receipt.json` and individual command logs; the wrapper never changes expected output or downgrades a failure. `--out` must be an empty directory **outside the checkout**. An untracked or modified source makes the proof fail closed.

## Full model-backed proof

Prepare the required ONNX files with the existing supported Docling dependency installer, plus the pinned `docling-parse` renderer. Independently pin their SHA-256 values in a JSON file maintained **outside the repository**:

```json
{
  "schema": "docling-pdf-model-set/1.0",
  "model_sha256": {
    "layout_heron.onnx": "<sha256>",
    "ocr_rec.onnx": "<sha256>",
    "ocr_det.onnx": "<sha256>",
    "ppocr_keys_v1.txt": "<sha256>",
    "tableformer/encoder.onnx": "<sha256>",
    "tableformer/decoder.onnx": "<sha256>",
    "tableformer/bbox.onnx": "<sha256>"
  },
  "renderer_sha256": "<sha256>"
}
```

Use an **immutable, reviewed** manifest, not values copied blindly from the currently installed model directory. The actual model SHA-256 values and the manifest SHA-256 are included in the receipt.

Keep the original licensed PDFs and independently reviewed table references in private storage. With the original NEC 2017 and IBC 2018 PDFs present as `/private/codebooks/nec-2017.pdf` and `/private/codebooks/ibc-2018.pdf` and the original reference/checker files under `/private/verification`:

```sh
python3 scripts/conformance/verify_direct.py \
  --repo . \
  --expected-sha '<reviewed 40-character Git commit SHA>' \
  --out /tmp/docling-full/<reviewed-sha> \
  --mode full \
  --model-manifest /private/model-set-sha256.json \
  --source-root /private/codebooks \
  --benchmark-root /private/verification
```

The full proof *also* executes the repository's existing model-backed PDF groundtruth and independent snapshot regression scripts, builds its actual release `docling-rs` binary, extracts only the source-reviewed NEC physical PDF page **153** and IBC physical PDF page **286** with TableFormer, and tests exact column/row ownership and unresolved source-word evidence against the private reviewed benchmarks. Each source PDF and reference hash is pinned in the receipt; private PDFs must **not** enter public GitHub. There is no alternate OCR-based success path.

`PASS_MODEL_AND_SOURCE` is only emitted when every required step succeeds. Missing models, a mismatched original PDF, a layout regression, unplaced or ambiguous source words, and wrong value-to-cell binding all fail the run. The full report includes command-log digests, model and renderer digests, source commit and tree identity, and produced Docling JSON hashes.

## Verification scope, retention and promotion

- A source-verified test measures a particular **Git SHA, model set, renderer, and input PDF revision**; it does not automatically verify another commit or another codebook.
- Independent human-reviewed table expectations are **not** generated from the Rust extractor. Python checkers only grade source associations and never repair a model's output.
- A bad result stays failed or unsupported; never update the expected snapshots to make it green without first determining whether the source or the model is wrong.
- Store receipts and logs alongside immutable model/source provenance. Review failures by their actual supporting page and cell. Avoid attaching private codebooks to public PRs or cloud logs.
- Once an exact candidate passes, process the 16 source PDFs incrementally using the existing pinned conversion batch script, retaining both Docling JSON and Markdown plus page checks and a separate audit. Do not replace earlier transcriptions until the new ones are independently accepted.

## Wrapper self-test

```sh
python3 scripts/conformance/verify_direct.py --self-test
```

This proves only the wrapper's error handling, hash guard, model pin guard and log receipts. It is **not** evidence that Rust compiled or that any PDF was parsed. The full command must run on a real Rust-enabled host with the private references accessible.
