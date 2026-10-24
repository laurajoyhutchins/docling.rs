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
| OTSL HPO Table 1 | [arXiv:2305.03393, p. 9](https://arxiv.org/pdf/2305.03393) | Language labels and TED/mAP/runtime numbers matched to their proper columns and logical rows |

The inputs are the existing sampled PDF corpus under
`tests/data/pdf/sources/`. Each is pinned to its **Git blob SHA-1**, and the
runner checks those bytes before accepting any score. It also records a SHA-256
of the actual tested source bytes in its JSON evidence report. Do not confuse
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
remain significant. The scorer doesn't infer cells or move source words.

Failing rows must be inspected against the original source and the
model-predicted grid: a source-fidelity failure is not automatically a Rust
matcher defect. Reuse `DOCLING_RS_TF_MATCH_DUMP` and
`scripts/test/tf_match_reference.py` to isolate TableFormer predictions
from the ported matching logic before modifying `tf_match.rs`.

## Boundary

This initial slice validates seven independently reviewed table bindings in
two source documents. It does not yet provide full-document text recall,
coordinate accuracy, regulatory cross-reference testing, or a large statistical
accuracy claim. Those are additional #11 acceptance criteria, and the new
machine-readable report provides a place to expand the evidence without
regenerating Markdown snapshots to mask errors.
