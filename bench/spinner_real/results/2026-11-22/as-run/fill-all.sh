#!/bin/bash
# Waits for the unfocused stage, then tops up every cell that lacks quiet runs.
set -u
OUT=$HOME/.cache/zed-pr-67261-feedback
until [ -e "$OUT/quiet-unfocused.done" ]; do sleep 5; done
cd "$OUT" || exit 1
for pass in 1 2 3; do
  echo "== pass $pass $(date +%H:%M:%S) load $(cut -d' ' -f1-3 /proc/loadavg)"
  python3 fill.py 4 180 "$OUT/fill-focused.log" "$OUT/quiet-focused.log" > "$OUT/fill-focused.$pass.out" 2>&1
  echo "focused: exit=$? $(head -1 "$OUT/fill-focused.$pass.out")"
  python3 fill.py 3 180 "$OUT/fill-unfocused.log" "$OUT/quiet-unfocused.log" > "$OUT/fill-unfocused.$pass.out" 2>&1
  echo "unfocused: exit=$? $(head -1 "$OUT/fill-unfocused.$pass.out")"
  if head -1 "$OUT/fill-focused.$pass.out" | command grep -q ' 0 short' && head -1 "$OUT/fill-unfocused.$pass.out" | command grep -q ' 0 short'; then break; fi
done
echo "done $(date +%H:%M:%S)"
touch "$OUT/fill-all.done"
