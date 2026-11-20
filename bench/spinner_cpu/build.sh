#!/bin/bash
# Builds before/after release test binaries of the ui crate into ./bin. Run from a zed checkout at the PR head.
set -euo pipefail
harness=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$harness/bin"
f=crates/ui/src/components/label/spinner_label.rs
l=crates/ui/src/components/label/loading_label.rs
cp "$f" "$harness/spinner_label.orig"; cp "$l" "$harness/loading_label.orig"
cat "$harness/bench_module.rs" >> "$f"
build() {
  cargo test --release -p ui --lib --no-run --message-format=json 2>/dev/null \
    | python3 -c 'import json,sys; [print(m["executable"]) for m in map(json.loads,sys.stdin) if m.get("reason")=="compiler-artifact" and m.get("executable") and m["target"]["name"]=="ui"]' | tail -1
}
cp "$(build)" "$harness/bin/after"
perl -pi "$harness/revert.pl" "$f"
perl -pi "$harness/revert.pl" "$l"
command grep -q with_max_fps "$f" "$l" && { echo "revert did not apply"; exit 1; }
cp "$(build)" "$harness/bin/before"
cp "$harness/spinner_label.orig" "$f"; cp "$harness/loading_label.orig" "$l"
ls -la "$harness/bin"
