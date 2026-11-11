#!/usr/bin/env bash
#
# Per-PDF byte-conformance of the Rust pipeline vs the committed docling
# groundtruth (tests/data/pdf/groundtruth/*.md). Unlike conformance.sh this needs
# no docling install — it diffs against the checked-in reference. Use it to track
# how many groundtruth PDFs are byte-for-byte exact (see docs/PDF_CONFORMANCE.md).
#
# Usage: scripts/conformance/pdf_groundtruth.sh

set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../.."

source scripts/conformance/pdf_env.sh
docling_pdf_conformance_env

cargo build --release --quiet -p docling-cli
BIN=./target/release/docling-rs
# Upstream generates tests/data/pdf/groundtruth with `do_ocr=False`
# (tests/test_e2e_conversion.py: layout + TableFormer with cell matching, no
# OCR) and serializes it with `export_to_markdown(compact_tables=True)`
# (tests/verify_utils.py), so the pipeline runs the same way — layout and
# tables, never OCR, unpadded `| - |` tables.
BIN_ARGS=(--skip-ocr --compact-tables)

# Collapse whitespace runs to a single space (and trim) so a spacing-only diff —
# e.g. docling's spurious double space in amt's `up to  1 / 4`, where our
# single-spaced rendering is the more faithful one — counts as a normalized match.
norm() { sed -E 's/[[:space:]]+/ /g; s/^ +//; s/ +$//'; }

# Conformance is a ratchet, not just a headline count: another fixture getting
# better must never conceal a regression in one that was already exact.
# Add a name here only *after* an exact-revision conformance run proves it.
# These names are pinned to the 2026-10-23 v2.51.0 (7a965118) baseline.
declare -A baseline_strict=(
  [picture_classification]=1
  [multi_page]=1
  [2305.03393v1-pg9]=1
  [right_to_left_01]=1
  [right_to_left_02]=1
  [base14_fonts_rot90]=1
  [base14_fonts_rot180]=1
  [base14_fonts_rot270]=1
  [code_and_formula]=1
  [right_to_left_03]=1
)
# Upstream's extra space in the AMT fractions should not become a parser bug.
declare -A baseline_whitespace=([amt_handbook_sample]=1)
# The remaining eight also have a proven ceiling. A change that worsens one
# failing fixture must fail even when all ten exact fixtures remain exact.
# Counts are changed lines from the pinned docling-parse / skip-OCR corpus
# (2026-10-23, 324 changed lines in total). Tighten a ceiling only after an
# exact-head model-backed run confirms the new result.
declare -A baseline_max_diff=(
  [amt_handbook_sample]=2
  [2305.03393v1]=4
  [normal_4pages]=12
  [2203.01017v2]=15
  [2206.01062]=10
  [table_misidentified_as_form]=44
  [table_mislabeled_as_picture]=85
  [redp5110_sampled]=152
)
declare -A seen_fixtures=()
regressions=()

exact=0
nmatch=0
total=0
printf "%-34s %14s\n" "PDF" "DIFF-LINES"
printf "%-34s %14s\n" "---" "----------"
for gt in tests/data/pdf/groundtruth/*.md; do
  stem="$(basename "$gt" .md)"
  src="tests/data/pdf/sources/$stem.pdf"
  [[ -f "$src" ]] || continue
  total=$((total + 1))
  if [[ -z "${baseline_strict[$stem]:-}" && -z "${baseline_max_diff[$stem]:-}" ]]; then
    regressions+=("$stem: new fixture has no recorded conformance baseline")
  fi
  # Never turn a failed conversion into a diff score; on a large reference
  # document the literal "<ERROR>" could even score below a known ceiling.
  if ! out=$("$BIN" "${BIN_ARGS[@]}" "$src"); then
    regressions+=("$stem: PDF conversion failed")
    seen_fixtures["$stem"]=1
    printf "%-34s %14s\n" "$stem" "ERROR"
    continue
  fi
  if [[ -z "$out" ]]; then
    regressions+=("$stem: conversion returned empty output")
    seen_fixtures["$stem"]=1
    printf "%-34s %14s\n" "$stem" "EMPTY"
    continue
  fi
  # Strict comparison, trailing-newline-insensitive; one changed line counts as 2.
  d="$(diff <(printf '%s' "$out") <(printf '%s' "$(cat "$gt")") | grep -cE '^[<>]' || true)"
  # Whitespace-normalized comparison.
  dn="$(diff <(printf '%s' "$out" | norm) <(printf '%s' "$(cat "$gt")" | norm) | grep -cE '^[<>]' || true)"
  seen_fixtures["$stem"]=1
  if [[ -n "${baseline_strict[$stem]:-}" && "$d" -ne 0 ]]; then
    regressions+=("$stem: lost byte-for-byte conformance ($d changed lines)")
  fi
  if [[ -n "${baseline_whitespace[$stem]:-}" && "$dn" -ne 0 ]]; then
    regressions+=("$stem: lost whitespace-normalized conformance ($dn changed lines)")
  fi
  if [[ -n "${baseline_max_diff[$stem]:-}" && "$d" -gt "${baseline_max_diff[$stem]}" ]]; then
    regressions+=("$stem: changed lines $d exceed pinned ceiling ${baseline_max_diff[$stem]}")
  fi
  if [[ "$d" -eq 0 ]]; then
    exact=$((exact + 1))
    nmatch=$((nmatch + 1))
    mark="EXACT"
  elif [[ "$dn" -eq 0 ]]; then
    nmatch=$((nmatch + 1))
    mark="$d (ws-ok)"
  else
    mark="$d"
  fi
  printf "%-34s %14s\n" "$stem" "$mark"
done
echo
echo "Fully conformant (strict):     $exact / $total"
echo "Whitespace-normalized matches: $nmatch / $total"
# A fixture removed or renamed must not silently remove its protection.
for stem in "${!baseline_strict[@]}" "${!baseline_whitespace[@]}" "${!baseline_max_diff[@]}"; do
  if [[ -z "${seen_fixtures[$stem]:-}" ]]; then
    regressions+=("$stem: pinned fixture source/groundtruth was not measured")
  fi
done
if (( ${#regressions[@]} )); then
  printf '\nPDF conformance baseline REGRESSED:\n' >&2
  printf '  - %s\n' "${regressions[@]}" >&2
  exit 1
fi
echo "PDF conformance baseline: no regressions"
