#!/bin/bash
# usage: queue2.sh <none | file:PATH> <name> <variants> <zed-binary> <runs> <rates> <scenarios>
# Runs my copy of the harness, which repeats runs taken while the machine was busy.
set -u
OUT=$HOME/.cache/zed-pr-67261-feedback
wait_for=$1; name=$2; variants=$3; zed=$4; runs=$5; rates=$6; scenarios=$7
case "$wait_for" in
  file:*) until [ -e "${wait_for#file:}" ]; do sleep 5; done ;;
esac
cd "$OUT/bench-wt/bench/spinner_real" || exit 1
echo "start $(date -Is) load $(cut -d' ' -f1-3 /proc/loadavg)" > "$OUT/$name.log"
BENCH_ZED="$zed" BENCH_WORK="$OUT/bench-work" BENCH_VARIANTS="$variants" BENCH_WINDOW=6 BENCH_MAX_OTHER=800 \
  python3 measure_real.py "$runs" "$rates" "$scenarios" >> "$OUT/$name.log" 2>&1
echo "harness exit=$?"
echo "end $(date -Is) load $(cut -d' ' -f1-3 /proc/loadavg)" >> "$OUT/$name.log"
touch "$OUT/$name.done"
command grep -E '^\||Medians|Cells|retrying|Traceback|Error' "$OUT/$name.log" | cut -c1-300 | tail -30
echo "repeated for a busy machine: $(command grep -c '^busy machine' "$OUT/$name.log")"
