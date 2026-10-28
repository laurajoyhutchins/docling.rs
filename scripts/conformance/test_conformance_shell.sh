#!/usr/bin/env bash
# Exercise the actual corpus comparison script with synthetic upstream and
# cargo commands. No model downloads, cargo compilation, or corpus mutations.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

mkdir -p "$tmp/scripts/conformance" "$tmp/tests/data/html/sources" \
         "$tmp/tests/data/html/groundtruth" "$tmp/bin"
cp "$root/scripts/conformance/conformance.sh" "$tmp/scripts/conformance/conformance.sh"

# Keep the real conformance script, but isolate its external prerequisites.
cat > "$tmp/scripts/_common.sh" <<'COMMON'
WORKSPACE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$WORKSPACE_DIR/Cargo.toml"
CORPUS_DIR="$WORKSPACE_DIR/tests/data"
PYBIN=/bin/false
PY_RUNNER=/dev/null
ensure_docling() { :; }
COMMON

printf 'fixture\n' > "$tmp/tests/data/html/sources/sample.html"
printf 'EXACT\n' > "$tmp/tests/data/html/groundtruth/sample.html.md"
cat > "$tmp/bin/cargo" <<'CARGO'
#!/usr/bin/env bash
case "${1:-}" in
  build) exit 0 ;;
  run)
    # Plausible stdout preceding a failing exit is not a conformance result.
    printf 'EXACT\n'
    if [[ "${TEST_RUST_FAILURE:-0}" == 1 ]]; then
      printf 'synthetic converter error\n' >&2
      exit 27
    fi
    ;;
  *) printf 'unexpected cargo command: %s\n' "$1" >&2; exit 99 ;;
esac
CARGO
chmod +x "$tmp/bin/cargo"

# Prove the normal case still scores the independent reference as exact.
PATH="$tmp/bin:$PATH" TEST_RUST_FAILURE=0 \
  bash "$tmp/scripts/conformance/conformance.sh" html > "$tmp/success.log" 2>&1
grep -Eq 'Exact \(strict\): +1 / 1' "$tmp/success.log" || {
  cat "$tmp/success.log" >&2
  echo "successful converter did not score EXACT" >&2
  exit 1
}

# Even with identical-looking stdout, failure must propagate its exact status
# and must not print a completed strict/normalized conformance score.
status=0
PATH="$tmp/bin:$PATH" TEST_RUST_FAILURE=1 \
  bash "$tmp/scripts/conformance/conformance.sh" html > "$tmp/failure.log" 2>&1 || status=$?
if [[ "$status" != 27 ]]; then
  cat "$tmp/failure.log" >&2
  printf 'wanted Rust exit 27, received %s\n' "$status" >&2
  exit 1
fi
grep -Fq 'Rust conversion failed for' "$tmp/failure.log"
grep -Fq 'synthetic converter error' "$tmp/failure.log"
if grep -q '^Exact (strict):' "$tmp/failure.log"; then
  cat "$tmp/failure.log" >&2
  echo "failed converter incorrectly produced summary" >&2
  exit 1
fi
echo 'conformance shell: successful EXACT and failed Rust exit verified'
