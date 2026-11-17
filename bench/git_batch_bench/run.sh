#!/usr/bin/env bash
# Usage: run.sh <binary> <runs> <file count>... -- <repository>...
set -euo pipefail
binary=$1
runs=$2
shift 2
counts=()
while [ "$1" != "--" ]; do counts+=("$1"); shift; done
shift
results=$(mktemp)
for repository in "$@"; do
  name=$(basename "$repository")
  for count in "${counts[@]}"; do
    for mode in diffs unstaged; do
      for run in $(seq "$runs"); do
        ZED_NO_DIFF_BASE_BATCH=1 "$binary" "$repository" "$mode" "$count" 1 "$name $mode $count before" 2>/dev/null | tee -a "$results"
        "$binary" "$repository" "$mode" "$count" 1 "$name $mode $count after" 2>/dev/null | tee -a "$results"
      done
    done
  done
done
echo
echo "median wall time (ms) per case:"
sed -E 's/^(.*): .* in ([0-9.]+) ms .*/\1|\2/' "$results" | sort -t'|' -k1,1 -k2,2n |
  awk -F'|' '{values[$1] = values[$1] " " $2; count[$1]++}
    END {for (key in values) {n = split(substr(values[key], 2), sorted, " "); print key ": " sorted[int((n + 1) / 2)] " (n=" n ", all:" values[key] ")"}}' | sort
