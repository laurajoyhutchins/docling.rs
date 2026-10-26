#!/usr/bin/env bash
# Regenerate PDF output for the test corpus and diff it against the committed
# snapshot baseline (tests/snapshots/). The pipeline is deterministic, so a
# clean checkout should report every fixture EXACT; a non-zero diff means the
# output drifted. Run scripts/install/pdf_setup.sh first to fetch the libs/models.
set -euo pipefail
cd "$(dirname "$0")/../.."   # docling.rs/

source scripts/conformance/pdf_env.sh
docling_pdf_conformance_env

# PDF_CONFORMANCE_OUT keeps the regenerated tree (CI uploads it as the
# artifact a maintainer refreshes tests/snapshots from); unset, it is scratch.
if [ -n "${PDF_CONFORMANCE_OUT:-}" ]; then
  tmp="$PDF_CONFORMANCE_OUT"
  mkdir -p "$tmp"
else
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
fi
echo "regenerating PDF output ..."
cargo run --release -q -p docling-pdf --example snapshot -- tests/data "$tmp" 1>&2

exact=0; drift=0; tot=0
while IFS= read -r snap; do
  rel="${snap#tests/snapshots/}"
  gen="$tmp/$rel"
  tot=$((tot + 1))
  if [ -f "$gen" ] && diff -q "$snap" "$gen" >/dev/null 2>&1; then
    exact=$((exact + 1))
  else
    drift=$((drift + 1))
    d=$(diff "$snap" "$gen" 2>/dev/null | grep -cE '^[<>]' || true)
    printf "  %-55s %s\n" "$rel" "${d:-MISSING}"
  fi
done < <(find tests/snapshots -name '*.md' | sort)

echo "PDF snapshot conformance: $exact/$tot exact ($drift drifted)"
[ "$drift" -eq 0 ]
