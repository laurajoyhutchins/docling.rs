# Source-grounded PDF table checks

This is an independent semantic check for [#9](https://github.com/laurajoyhutchins/docling.rs/issues/9)
and [#11](https://github.com/laurajoyhutchins/docling.rs/issues/11). It does **not**
replace `pdf_groundtruth.sh`, which measures parity with Python Docling's
Markdown, and it does **not** turn extracted document content into trusted
engineering or regulatory rules.

## Reviewed sources

The fixture manifest at `scripts/conformance/semantic_tables_cases.json`
contains facts manually transcribed from the original PDFs rather than from
either Docling implementation.

| Case | Original source page (1-based) | What is checked |
| --- | --- | --- |
| IBM REDP-5110 contents | [IBM Redpaper, p. 5](https://www.redbooks.ibm.com/redpapers/pdfs/redp5110.pdf) | Contents titles matched to the correct printed page numbers, in **different table columns on the same row** |
| OTSL HPO Table 1 | [arXiv:2305.03393, p. 9](https://arxiv.org/pdf/2305.03393) | Each encoder/decoder configuration matched to its paired OTSL/HTML TED/mAP/runtime values in distinct metric columns on the same row |

The inputs are the existing sampled PDF corpus under
`tests/data/pdf/sources/`. Each is pinned to its **Git blob SHA-1**, and the
runner checks those bytes before accepting any score. The corresponding
pinned Python Docling groundtruth JSON is independently checked against every
manifest assertion *before* scoring Rust, and its Git blob SHA-1 / content SHA-256
are recorded. This catches impossible expectations: the earlier HPO manifest
demanded separate OTSL/HTML scalar cells that Python Docling did not have.
**Passing the reference check proves only reference compatibility**, not
independent source fidelity. The original PDF is still the final authority.
Do not confuse
the sampled PDFs' SHA-256 with the SHA-256 of the linked full original PDFs.

These bindings are deliberately small: they prove selected relations only.
The `redp5110_sampled` input is a *sample*, not the full 146-page original.
The scoring runner does not assume sampled page numbering equals that of the
original.

## Running

With Rust CLI and the PDF models already installed:

```bash
cargo build --release -p docling-cli
python3 scripts/conformance/semantic_tables.py
```

To score JSON outputs prepared independently (`CASE_ID.json`, one file per
case), without a Rust binary or models:

```bash
python3 scripts/conformance/semantic_tables.py --json-dir target/semantic-input
```

To validate the scoring logic itself, without Rust or models:

```bash
python3 -m unittest discover -s scripts/conformance -p test_semantic_tables.py
```

The scorer exits nonzero on a missing fixture, changed source blob,
conversion failure, malformed JSON, or failed binding. Use `--report-only`
for **diagnostic** collection in CI while the source-fidelity baseline is
being characterized; this only changes the process exit code, not the
`passed`/failure fields in the report. The score and source digests are
written to `target/semantic_tables.json`.

## What constitutes a match

The scorer uses only `tables[].data.table_cells` with valid first-class
`start_row_offset_idx`, `end_row_offset_idx`, and corresponding column
indices. All expected values must inhabit **distinct** cells in strictly
increasing, nonoverlapping columns with a shared logical row. A row-spanning
cell can participate on a covered row. Repeated `grid` slots, Markdown pipe
text, flat concatenations, and values on adjacent rows do not establish
ownership. Punctuation-only dot leaders are ignored; all letters and digits
remain significant. **Do not split paired metrics into imaginary cells.**
The HPO table has one Language cell containing `OTSL HTML`, with one cell per
metric containing both method values (for example `0.965 0.969`).
The pinned Python reference JSON agrees. A check demanding that `OTSL` and
`0.965` appear in *separate* source cells would incorrectly fail even the
upstream reference; the cases now compare four complete source-defined rows
and reject swapped columns. The scorer doesn't infer cells or move source words.

Failing rows must be inspected against the original source and the
model-predicted grid: a source-fidelity failure is not automatically a Rust
matcher defect. Reuse `DOCLING_RS_TF_MATCH_DUMP` and
`scripts/test/tf_match_reference.py` to isolate TableFormer predictions
from the ported matching logic before modifying `tf_match.rs`.

## Boundary

This initial slice validates eight independently reviewed table bindings in
two source documents. It does not yet provide full-document text recall,
coordinate accuracy, regulatory cross-reference testing, or a large statistical
accuracy claim. Those are additional #11 acceptance criteria, and the new
machine-readable report provides a place to expand the evidence without
regenerating Markdown snapshots to mask errors.

## Source-grounded semantic scorecard

The report now separates six dimensions. Text recall means only the reviewed anchor phrases,
not the entire PDF. Numbers/units checks numeric text in distinct table cells without claiming
correct column association; table bindings score row and column ownership separately.
Engineering units are explicitly unsupported when no reviewed unit facts exist. Reading
order is checked for the reviewed OTSL row sequence; the IBM sample has no such oracle.
Cross-reference targets and physical bounding boxes are unsupported, not counted as passes.

The runner emits target/semantic_tables.json (schema version 2, classified failures) and
target/semantic_review.md (human review). Use --reference-only to audit the pinned Python
reference without a Rust binary; that is NOT Rust model-backed evidence.
Use --report-only for diagnostic semantic scoring, never to mask failed source/reference
integrity or failed conversions.

Source SHA-256 in the report refers to the committed sample, not the original whole PDF.
Source rights status is explicitly unverified for redistribution. CLI args and any
explicit model configuration are captured, but model asset digests and full-source
SHA-256s are not pinned here. Do not claim extractor accuracy or legal rights from
public accessibility or Python parity.

Failed table associations now include a bounded expected-versus-observed logical-cell reproduction, and unassigned-source-word coverage is explicitly unsupported. The runner records SHA-256 fingerprints for candidate ONNX model files when present, along with explicit overrides, extraction settings, Rust binary digest, and checkout revision. Candidate model presence is NOT proof of which weights the runtime actually loaded.

The fixture manifest and machine-readable scorecard have internal Draft 2020-12 JSON Schema contracts at scripts/conformance/schemas/semantic_cases.schema.json and semantic_scorecard.schema.json, respectively. Schema presence does not itself establish independent source review or model-backed extraction; those remain separate gates.
