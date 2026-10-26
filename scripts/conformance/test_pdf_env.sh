#!/usr/bin/env bash
# Model-free negative controls for the shared conformance configuration.
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/conformance/pdf_env.sh

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/models" "$tmp/renderer"
export DOCLING_LAYOUT_ONNX="$tmp/models/layout.onnx"
export DOCLING_OCR_REC_ONNX="$tmp/models/rec.onnx"
export DOCLING_OCR_DICT="$tmp/models/dict.txt"
export DOCLING_OCR_DET_ONNX="$tmp/models/det.onnx"
export DOCLING_TABLEFORMER_ENCODER="$tmp/models/encoder.onnx"
export DOCLING_TABLEFORMER_DECODER="$tmp/models/decoder.onnx"
export DOCLING_TABLEFORMER_BBOX="$tmp/models/bbox.onnx"
export DOCLING_PARSE_RENDER_LIB="$tmp/renderer"
export DOCLING_RS_RENDERER=rust

for file in "$DOCLING_LAYOUT_ONNX" "$DOCLING_OCR_REC_ONNX" \
            "$DOCLING_OCR_DICT" "$DOCLING_OCR_DET_ONNX" \
            "$DOCLING_TABLEFORMER_ENCODER" "$DOCLING_TABLEFORMER_DECODER" \
            "$DOCLING_TABLEFORMER_BBOX"; do
  touch "$file"
done
# Explicit overrides are respected, even when the default model directory is absent.
docling_pdf_conformance_env
[[ "$DOCLING_LAYOUT_ONNX" == "$tmp/models/layout.onnx" ]]
[[ "$DOCLING_RS_RENDERER" == rust ]]

# Any missing model, including TableFormer, must make BOTH gates fail closed.
for missing in "$DOCLING_LAYOUT_ONNX" "$DOCLING_TABLEFORMER_DECODER"; do
  rm "$missing"
  if (docling_pdf_conformance_env >/dev/null 2>&1); then
    echo "FAIL: accepted missing model $missing" >&2; exit 1
  fi
  touch "$missing"
done

# Reference mode must not silently degrade to the native renderer.
export DOCLING_RS_RENDERER=docling-parse
if (docling_pdf_conformance_env >/dev/null 2>&1); then
  echo 'FAIL: accepted missing reference renderer' >&2; exit 1
fi
touch "$tmp/renderer/libdparse_render.so"
docling_pdf_conformance_env
printf 'PASS: overrides, missing-model rejection and renderer pin\n'
