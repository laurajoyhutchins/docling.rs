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
  printf "fixture\n" > "$file"
done
# Explicit overrides are respected, even when the default model directory is absent.
docling_pdf_conformance_env
[[ "$DOCLING_LAYOUT_ONNX" == "$tmp/models/layout.onnx" ]]
[[ "$DOCLING_RS_RENDERER" == rust ]]

# Any missing model, including TableFormer, must make BOTH gates fail closed.
for missing in "$DOCLING_LAYOUT_ONNX" "$DOCLING_OCR_REC_ONNX" \
               "$DOCLING_OCR_DICT" "$DOCLING_OCR_DET_ONNX" \
               "$DOCLING_TABLEFORMER_ENCODER" "$DOCLING_TABLEFORMER_DECODER" \
               "$DOCLING_TABLEFORMER_BBOX"; do
  rm "$missing"
  if (docling_pdf_conformance_env >/dev/null 2>&1); then
    echo "FAIL: accepted missing model $missing" >&2; exit 1
  fi
  printf "fixture\n" > "$missing"
done

# Existing but empty files are not valid model assets.
: > "$DOCLING_TABLEFORMER_DECODER"
if (docling_pdf_conformance_env >/dev/null 2>&1); then
  echo "FAIL: accepted zero-byte TableFormer model" >&2; exit 1
fi
printf "fixture\n" > "$DOCLING_TABLEFORMER_DECODER"

# Both real entrypoints must reject missing models before invoking cargo.
mkdir -p "$tmp/bin"
printf '#!/usr/bin/env bash\nprintf "invoked\n" >> "$CARGO_CALL_LOG"\nexit 97\n' > "$tmp/bin/cargo"
chmod +x "$tmp/bin/cargo"
export CARGO_CALL_LOG="$tmp/cargo-called"
rm "$DOCLING_TABLEFORMER_DECODER"
for entry in scripts/conformance/pdf_conformance.sh scripts/conformance/pdf_groundtruth.sh; do
  [[ -f "$entry" ]] || { echo "FAIL: missing real entrypoint $entry" >&2; exit 1; }
  if (PATH="$tmp/bin:$PATH" bash "$entry" >/dev/null 2>&1); then
    echo "FAIL: $entry accepted incomplete model set" >&2; exit 1
  fi
  if [[ -e "$CARGO_CALL_LOG" ]]; then
    echo "FAIL: $entry invoked cargo before validating models" >&2; exit 1
  fi
done
printf "fixture\n" > "$DOCLING_TABLEFORMER_DECODER"

# Reference mode must not silently degrade to the native renderer.
export DOCLING_RS_RENDERER=docling-parse
if (docling_pdf_conformance_env >/dev/null 2>&1); then
  echo 'FAIL: accepted missing reference renderer' >&2; exit 1
fi
printf "fixture\n" > "$tmp/renderer/libdparse_render.so"
docling_pdf_conformance_env
export DOCLING_RS_RENDERER=auto
if (docling_pdf_conformance_env >/dev/null 2>&1); then
  echo 'FAIL: accepted non-deterministic auto renderer' >&2; exit 1
fi
printf 'PASS: overrides, missing-model rejection and renderer pin\n'
