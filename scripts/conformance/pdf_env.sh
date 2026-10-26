#!/usr/bin/env bash
# Shared, pinned PDF conformance environment. Source after changing to the
# repository root; both snapshot and upstream-groundtruth gates call this.
# Keep the model choices in one place so the two gates cannot silently diverge.

docling_pdf_conformance_env() {
  local root="$(pwd)" f found
  export DOCLING_RS_SLOW_RESIZE="${DOCLING_RS_SLOW_RESIZE:-1}"
  export DOCLING_LAYOUT_ONNX="${DOCLING_LAYOUT_ONNX:-$root/.models/layout_heron.onnx}"
  export DOCLING_OCR_REC_ONNX="${DOCLING_OCR_REC_ONNX:-$root/.models/ocr_rec.onnx}"
  export DOCLING_OCR_DICT="${DOCLING_OCR_DICT:-$root/.models/ppocr_keys_v1.txt}"
  export DOCLING_OCR_DET_ONNX="${DOCLING_OCR_DET_ONNX:-$root/.models/ocr_det.onnx}"
  export DOCLING_TABLEFORMER_ENCODER="${DOCLING_TABLEFORMER_ENCODER:-$root/.models/tableformer/encoder.onnx}"
  export DOCLING_TABLEFORMER_DECODER="${DOCLING_TABLEFORMER_DECODER:-$root/.models/tableformer/decoder.onnx}"
  export DOCLING_TABLEFORMER_BBOX="${DOCLING_TABLEFORMER_BBOX:-$root/.models/tableformer/bbox.onnx}"
  export DOCLING_RS_RENDERER="${DOCLING_RS_RENDERER:-docling-parse}"
  export DOCLING_PARSE_RENDER_LIB="${DOCLING_PARSE_RENDER_LIB:-$root/.docling-parse/lib}"

  # The fixture baseline uses Layout, OCR and TableFormer. Missing TableFormer
  # otherwise silently selects a geometric reconstruction and masks fidelity.
  for f in "$DOCLING_LAYOUT_ONNX" "$DOCLING_OCR_REC_ONNX" \
           "$DOCLING_OCR_DICT" "$DOCLING_OCR_DET_ONNX" \
           "$DOCLING_TABLEFORMER_ENCODER" "$DOCLING_TABLEFORMER_DECODER" \
           "$DOCLING_TABLEFORMER_BBOX"; do
    if [[ ! -s "$f" ]]; then
      printf 'MISSING: %s (run scripts/install/pdf_setup.sh)\n' "$f" >&2
      return 1
    fi
  done

  # The reference baselines were generated using the docling-parse renderer.
  # Explicit Rust or PDFium overrides remain useful for parity diagnosis.
  case "$DOCLING_RS_RENDERER" in
  docling-parse)
    found=0
    for f in "$DOCLING_PARSE_RENDER_LIB"/libdparse_render.*; do
      if [[ -s "$f" ]]; then found=1; break; fi
    done
    if (( ! found )); then
      printf 'MISSING: %s/libdparse_render.* (build docling-parse renderer or select DOCLING_RS_RENDERER=rust)\n' \
        "$DOCLING_PARSE_RENDER_LIB" >&2
      return 1
    fi
    ;;
  rust|pdfium) ;;
  *)
    printf 'UNSUPPORTED: DOCLING_RS_RENDERER=%s (expected docling-parse, rust or pdfium)\n' "$DOCLING_RS_RENDERER" >&2
    return 1
    ;;
  esac
}
