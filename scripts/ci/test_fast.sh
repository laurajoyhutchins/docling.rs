#!/usr/bin/env bash
# Fast checkpoint: retain all workspace tests except three exhaustive,
# corpus-wide output/streaming sweeps. Run those with:
#   cargo test -p docling --test regression --locked
# Keep this command identical in local VMs and the required CI job.
set -euo pipefail
cd "$(dirname "$0")/../.."
exec scripts/ci/cargo_retry_ort.sh "${CARGO:-cargo}" test --workspace --locked -- \
  --skip streaming_matches_buffered_markdown \
  --skip streaming_with_options_matches_buffered_markdown \
  --skip outputs_match_fixtures
